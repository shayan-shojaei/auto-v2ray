#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "requests[socks]",
#   "qrcode",
# ]
# ///
"""auto-v2ray: Fetch vmess subscription, test latency, start SOCKS5 proxy."""

import argparse
import atexit
import base64
import html
import io
import json
import os
import platform
import random
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import qrcode
import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_SUB = (
    "https://raw.githubusercontent.com/ebrasha/free-v2ray-public-list"
    "/refs/heads/main/vmess_configs.txt"
)
SINGBOX_DIR = Path.home() / ".auto-v2ray"
SINGBOX_BIN = SINGBOX_DIR / "sing-box"
TEST_URL = "http://www.gstatic.com/generate_204"
TCP_TIMEOUT = 2.0
HTTP_TIMEOUT = 6
TEST_PORT_RANGE = (20000, 29999)
CACHE_FILE = SINGBOX_DIR / "cache.json"
CACHE_TTL = 300  # 5 minutes

# ---------------------------------------------------------------------------
# sing-box download / bootstrap
# ---------------------------------------------------------------------------

def _platform_asset(version: str) -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    os_name = {"darwin": "darwin", "linux": "linux"}.get(system)
    if not os_name:
        raise RuntimeError(f"Unsupported OS: {system}")
    arch = "arm64" if machine in ("arm64", "aarch64") else "amd64"
    return f"sing-box-{version}-{os_name}-{arch}.tar.gz"


def _download_singbox():
    SINGBOX_DIR.mkdir(parents=True, exist_ok=True)
    print("Fetching latest sing-box release info...")
    with urllib.request.urlopen(
        "https://api.github.com/repos/SagerNet/sing-box/releases/latest",
        timeout=15,
    ) as r:
        release = json.loads(r.read())

    version = release["tag_name"].lstrip("v")
    asset_name = _platform_asset(version)

    asset_url = next(
        (a["browser_download_url"] for a in release["assets"] if a["name"] == asset_name),
        None,
    )
    if not asset_url:
        raise RuntimeError(f"Asset not found: {asset_name}")

    archive_path = SINGBOX_DIR / asset_name
    print(f"Downloading sing-box v{version} ...")
    with urllib.request.urlopen(asset_url, timeout=120) as r:
        archive_path.write_bytes(r.read())

    with tarfile.open(archive_path) as tar:
        member = next(
            (m for m in tar.getmembers() if m.name.endswith("/sing-box") or m.name == "sing-box"),
            None,
        )
        if not member:
            raise RuntimeError("sing-box binary not found in archive")
        # Extract to SINGBOX_DIR as "sing-box"
        member.name = "sing-box"
        tar.extract(member, SINGBOX_DIR)

    SINGBOX_BIN.chmod(0o755)
    archive_path.unlink(missing_ok=True)
    _strip_quarantine(SINGBOX_BIN)
    print(f"sing-box installed at {SINGBOX_BIN}")


def _strip_quarantine(path: Path) -> None:
    """Remove macOS quarantine xattr so the binary can run in subprocesses."""
    if platform.system() == "Darwin":
        subprocess.run(
            ["xattr", "-d", "com.apple.quarantine", str(path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


def ensure_singbox():
    if not SINGBOX_BIN.exists():
        _download_singbox()
    else:
        _strip_quarantine(SINGBOX_BIN)
    _verify_singbox()


def _verify_singbox():
    result = subprocess.run(
        [str(SINGBOX_BIN), "version"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        msg = (result.stderr or result.stdout).strip()
        print(f"sing-box binary check failed:\n  {msg}")
        print(f"\nTry removing and re-running to re-download:")
        print(f"  rm {SINGBOX_BIN}")
        sys.exit(1)


def _load_cache(sub_url: str) -> tuple[dict, int] | None:
    try:
        data = json.loads(CACHE_FILE.read_text())
        age = int(time.time() - data.get("timestamp", 0))
        if data.get("sub_url") == sub_url and age < CACHE_TTL and data.get("config"):
            return data["config"], age
    except Exception:
        pass
    return None


def _save_cache(sub_url: str, cfg: dict) -> None:
    try:
        SINGBOX_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps({
            "sub_url": sub_url,
            "timestamp": time.time(),
            "config": cfg,
        }))
    except Exception:
        pass

# ---------------------------------------------------------------------------
# vmess:// URL parsing
# ---------------------------------------------------------------------------

def _parse_vmess_uri(uri: str) -> dict | None:
    """Parse uuid@host:port?params (URI body, after vmess://)."""
    try:
        uri = html.unescape(uri)
        parsed = urllib.parse.urlparse("vmess://" + uri)
        if not parsed.hostname or not parsed.port:
            return None
        # Support both plain uuid@host and security:uuid@host (password field = uuid)
        uuid = parsed.password or parsed.username
        if not uuid:
            return None
        p = urllib.parse.parse_qs(parsed.query)

        def q(key, default=""):
            return p.get(key, [default])[0]

        return {
            "name": q("remarks", f"{parsed.hostname}:{parsed.port}"),
            "server": parsed.hostname,
            "port": int(parsed.port),
            "uuid": uuid,
            "aid": int(q("alterId", "0")),
            "net": q("type", "tcp"),
            "type": q("headerType", "none"),
            "host": q("host", ""),
            "path": q("path", "/"),
            "tls": q("security", "none"),
            "sni": q("sni", ""),
            "security": q("encryption", "auto"),
        }
    except Exception:
        return None


def _parse_vmess_json(text: str) -> dict | None:
    """Parse v2rayN JSON config text into a vmess config dict."""
    try:
        cfg = json.loads(text)
        return {
            "name": cfg.get("ps", f"{cfg.get('add', '')}:{cfg.get('port', '')}"),
            "server": cfg.get("add", ""),
            "port": int(cfg.get("port", 443)),
            "uuid": cfg.get("id", ""),
            "aid": int(cfg.get("aid", 0)),
            "net": cfg.get("net", "tcp"),
            "type": cfg.get("type", "none"),
            "host": cfg.get("host", ""),
            "path": cfg.get("path", "/"),
            "tls": cfg.get("tls", ""),
            "sni": cfg.get("sni", ""),
            "security": cfg.get("scy", "auto"),
        }
    except Exception:
        return None


def _b64decode(s: str) -> str | None:
    """Attempt base64 decode; return string or None on failure."""
    try:
        pad = (4 - len(s) % 4) % 4
        return base64.b64decode(s + "=" * pad).decode("utf-8")
    except Exception:
        return None


def parse_vmess(url: str) -> dict | None:
    """Parse a vmess:// URL supporting URI, base64-URI, v2rayN JSON, and double-b64-JSON formats."""
    raw = url[len("vmess://"):]

    # Many subscriptions encode vmess://BASE64 where BASE64 may be:
    #   (a) base64(uuid@host:port?params)    — URI format in base64
    #   (b) base64(base64(JSON))             — double-encoded v2rayN JSON
    #   (c) base64(JSON)                     — v2rayN JSON
    # Additionally: vmess://BASE64?extra_params where BASE64 encodes only the userinfo@host:port

    # Separate any literal query params appended outside the base64
    if "?" in raw and not raw.startswith("{"):
        b64_part = raw.split("?")[0]
        query_suffix = raw[len(b64_part):]  # "?..." or ""
    else:
        b64_part = raw
        query_suffix = ""

    decoded = _b64decode(b64_part)
    if decoded:
        if decoded.strip().startswith("{"):
            # Direct JSON
            cfg = _parse_vmess_json(decoded)
        elif "@" in decoded:
            # URI embedded in base64; re-attach any outer query params
            cfg = _parse_vmess_uri(decoded + query_suffix)
        else:
            # Possibly double-base64 JSON
            decoded2 = _b64decode(decoded)
            cfg = _parse_vmess_json(decoded2) if decoded2 and decoded2.strip().startswith("{") else None

        if cfg and cfg.get("server") and cfg.get("uuid"):
            return cfg

    # Literal URI (no base64 wrapping)
    if "@" in raw:
        cfg = _parse_vmess_uri(raw)
        if cfg and cfg.get("server") and cfg.get("uuid"):
            return cfg

    return None

# ---------------------------------------------------------------------------
# TCP latency test
# ---------------------------------------------------------------------------

def tcp_latency(cfg: dict) -> float | None:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(TCP_TIMEOUT)
        t0 = time.monotonic()
        err = s.connect_ex((cfg["server"], cfg["port"]))
        elapsed = time.monotonic() - t0
        s.close()
        return elapsed if err == 0 else None
    except Exception:
        return None

# ---------------------------------------------------------------------------
# sing-box config builder
# ---------------------------------------------------------------------------

def build_config(cfg: dict, listen: str = "0.0.0.0", port: int = 1080) -> dict:
    outbound: dict = {
        "type": "vmess",
        "tag": "proxy",
        "server": cfg["server"],
        "server_port": cfg["port"],
        "uuid": cfg["uuid"],
        "security": cfg.get("security") or "auto",
        "alter_id": cfg.get("aid", 0),
    }

    net = cfg.get("net") or "tcp"
    if net == "ws":
        t: dict = {"type": "ws"}
        if cfg.get("path") and cfg["path"] != "/":
            t["path"] = cfg["path"]
        if cfg.get("host"):
            t["headers"] = {"Host": cfg["host"]}
        outbound["transport"] = t
    elif net == "grpc":
        t = {"type": "grpc"}
        if cfg.get("path"):
            t["service_name"] = cfg["path"]
        outbound["transport"] = t
    elif net in ("h2", "http"):
        t = {"type": "http"}
        if cfg.get("path"):
            t["path"] = cfg["path"]
        if cfg.get("host"):
            t["host"] = [cfg["host"]]
        outbound["transport"] = t
    elif net == "httpupgrade":
        t = {"type": "httpupgrade"}
        if cfg.get("path"):
            t["path"] = cfg["path"]
        if cfg.get("host"):
            t["host"] = cfg["host"]
        outbound["transport"] = t

    if cfg.get("tls") in ("tls", "reality"):
        sni = cfg.get("sni") or cfg.get("host") or cfg["server"]
        outbound["tls"] = {"enabled": True, "server_name": sni}

    return {
        "log": {"level": "error"},
        "inbounds": [{
            "type": "socks",
            "tag": "socks-in",
            "listen": listen,
            "listen_port": port,
        }],
        "outbounds": [outbound, {"type": "direct", "tag": "direct"}],
        "route": {"final": "proxy"},
    }

# ---------------------------------------------------------------------------
# HTTP latency test through a temporary sing-box instance
# ---------------------------------------------------------------------------

_active_test_procs: list = []


def _wait_port(host: str, port: int, timeout: float = 2.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            s = socket.socket()
            s.settimeout(0.15)
            s.connect((host, port))
            s.close()
            return True
        except Exception:
            time.sleep(0.05)
    return False


def http_latency(cfg: dict) -> float | None:
    port = random.randint(*TEST_PORT_RANGE)
    tmpf = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(build_config(cfg, listen="127.0.0.1", port=port), tmpf)
    tmpf.close()

    proc = None
    try:
        proc = subprocess.Popen(
            [str(SINGBOX_BIN), "run", "-c", tmpf.name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _active_test_procs.append(proc)

        if not _wait_port("127.0.0.1", port):
            return None

        t0 = time.monotonic()
        resp = requests.get(
            TEST_URL,
            proxies={
                "http": f"socks5h://127.0.0.1:{port}",
                "https": f"socks5h://127.0.0.1:{port}",
            },
            timeout=HTTP_TIMEOUT,
        )
        elapsed = time.monotonic() - t0
        return elapsed if resp.status_code in (200, 204) else None
    except Exception:
        return None
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
            if proc in _active_test_procs:
                _active_test_procs.remove(proc)
        try:
            os.unlink(tmpf.name)
        except Exception:
            pass

# ---------------------------------------------------------------------------
# LAN IP detection
# ---------------------------------------------------------------------------

def get_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

# ---------------------------------------------------------------------------
# QR code helpers
# ---------------------------------------------------------------------------

def _qr_lines(data: str) -> list:
    qr = qrcode.QRCode(border=1)
    qr.add_data(data)
    qr.make(fit=True)
    buf = io.StringIO()
    qr.print_ascii(out=buf, invert=True)
    return buf.getvalue().rstrip("\n").splitlines()


def print_qr_pair(label1: str, url1: str, label2: str, url2: str):
    lines1 = _qr_lines(url1)
    lines2 = _qr_lines(url2)
    w = max((len(l) for l in lines1), default=0)
    gap = 4
    height = max(len(lines1), len(lines2))
    lines1 += [""] * (height - len(lines1))
    lines2 += [""] * (height - len(lines2))

    print(f"\n  {label1:<{w + gap}}{label2}")
    for l1, l2 in zip(lines1, lines2):
        print(f"  {l1:<{w + gap}}{l2}")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Pick the fastest vmess config and start a SOCKS5 proxy.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n  auto-v2ray\n  auto-v2ray --sub https://example.com/vmess.txt --port 1080",
    )
    parser.add_argument("--sub", default=DEFAULT_SUB, metavar="URL",
                        help="vmess subscription URL (default: built-in free list)")
    parser.add_argument("--port", type=int, default=1080, metavar="PORT",
                        help="SOCKS5 listen port (default: 1080)")
    parser.add_argument("--top", type=int, default=None, metavar="N",
                        help="max configs to HTTP-test (default: unlimited)")
    parser.add_argument("--min-working", type=int, default=3, metavar="K",
                        help="stop HTTP-testing once this many working proxies found (default: 3)")
    args = parser.parse_args()

    ensure_singbox()

    # ---- Fetch subscription ----
    print(f"\nFetching subscription...")
    resp = requests.get(args.sub, timeout=15)
    resp.raise_for_status()
    content = resp.text.strip()

    # Some subscription lists are base64-encoded as a whole
    try:
        decoded = base64.b64decode(content + "==").decode("utf-8")
        if "vmess://" in decoded:
            content = decoded
    except Exception:
        pass

    lines = [l.strip() for l in content.splitlines() if l.strip().startswith("vmess://")]
    configs = [c for c in (parse_vmess(l) for l in lines) if c]

    if not configs:
        print("No valid vmess:// configs found in subscription.")
        sys.exit(1)

    cached = _load_cache(args.sub)
    if cached:
        best, age = cached
        print(f"  Using cached config from {age}s ago (within {CACHE_TTL}s window), skipping latency checks.")
        print(f"  Config: {best['name']}  ({best['server']}:{best['port']})")
    else:
        best = None

    if best is None:
        print(f"Found {len(configs)} vmess configs. Running TCP latency test...")

        # ---- TCP filter ----
        tcp_results: list = []
        with ThreadPoolExecutor(max_workers=50) as ex:
            futs = {ex.submit(tcp_latency, cfg): cfg for cfg in configs}
            for fut in as_completed(futs):
                lat = fut.result()
                if lat is not None:
                    tcp_results.append((lat, futs[fut]))

        tcp_results.sort(key=lambda x: x[0])

        all_candidates = [cfg for _, cfg in tcp_results]
        if not all_candidates:
            print("All servers unreachable. Check your network connection.")
            sys.exit(1)

        min_working = args.min_working
        max_tested = args.top  # None means no cap
        batch_size = 5
        http_results: list = []
        tested = 0

        print(f"{len(tcp_results)}/{len(configs)} servers reachable. "
              f"HTTP-testing in batches of {batch_size} until {min_working} working found...")

        # ---- HTTP test via sing-box ----
        for batch_start in range(0, len(all_candidates), batch_size):
            if len(http_results) >= min_working:
                break
            if max_tested is not None and tested >= max_tested:
                break
            batch = all_candidates[batch_start:batch_start + batch_size]
            if max_tested is not None:
                batch = batch[:max_tested - tested]
            with ThreadPoolExecutor(max_workers=len(batch)) as ex:
                futs = {ex.submit(http_latency, cfg): cfg for cfg in batch}
                for fut in as_completed(futs):
                    cfg = futs[fut]
                    lat = fut.result()
                    tested += 1
                    tag = f"{cfg['server']}:{cfg['port']}"
                    if lat is not None:
                        http_results.append((lat, cfg))
                        print(f"  [{tested:3d}] {tag:<40} {int(lat * 1000)}ms ✓")
                    else:
                        print(f"  [{tested:3d}] {tag:<40} failed")

        if not http_results:
            print("\nNo configs passed the HTTP test. The subscription may be stale.")
            sys.exit(1)

        http_results.sort(key=lambda x: x[0])
        best_lat, best = http_results[0]
        print(f"\n✓ Best of {len(http_results)} working: {best['name']}  "
              f"({best['server']}:{best['port']})  {int(best_lat * 1000)}ms")
        _save_cache(args.sub, best)

    # ---- Start production proxy ----
    cfg_path = SINGBOX_DIR / "config.json"
    cfg_path.write_text(json.dumps(build_config(best, listen="0.0.0.0", port=args.port), indent=2))

    # Validate config before starting
    check = subprocess.run(
        [str(SINGBOX_BIN), "check", "-c", str(cfg_path)],
        capture_output=True, text=True,
    )
    if check.returncode != 0:
        print("sing-box config validation failed:")
        print(check.stderr or check.stdout)
        sys.exit(1)

    proc = subprocess.Popen(
        [str(SINGBOX_BIN), "run", "-c", str(cfg_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    _stop_called = False

    def _stop():
        nonlocal _stop_called
        if _stop_called:
            return
        _stop_called = True
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        except ProcessLookupError:
            pass
        print("\nProxy stopped.")

    def _signal_stop(*_):
        _stop()
        os._exit(0)

    atexit.register(_stop)
    signal.signal(signal.SIGINT, _signal_stop)
    signal.signal(signal.SIGTERM, _signal_stop)

    print(f"Starting proxy on 0.0.0.0:{args.port} ...")
    if not _wait_port("127.0.0.1", args.port, timeout=6.0):
        print("Warning: proxy port did not open in time.")

    lan_ip = get_lan_ip()
    local_url = f"socks5://127.0.0.1:{args.port}"
    lan_url = f"socks5://{lan_ip}:{args.port}"
    remark = urllib.parse.quote(best.get("name", "auto-v2ray"), safe="")
    lan_qr_url = f"socks://{lan_ip}:{args.port}#{remark}"

    print(f"\n  Local:  {local_url}")
    print(f"  LAN:    {lan_url}")

    print(f"\n  LAN proxy")
    for l in _qr_lines(lan_qr_url):
        print(f"  {l}")

    print("\nPress Ctrl+C to stop.\n")

    while True:
        if proc.poll() is not None:
            stderr_output = ""
            try:
                stderr_output = proc.stderr.read().decode("utf-8", errors="replace").strip()
            except Exception:
                pass
            print("sing-box exited unexpectedly.")
            if stderr_output:
                print("sing-box error output:")
                print(stderr_output)
            sys.exit(1)
        time.sleep(1)


if __name__ == "__main__":
    main()
