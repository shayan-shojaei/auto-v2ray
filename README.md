# auto-v2ray

One command to pick the fastest vmess proxy from a subscription list and start a SOCKS5 server — with QR codes for easy phone setup.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/shayan-shojaei/auto-v2ray/main/install.sh | bash
```

Then (if `~/.local/bin` isn't already in your PATH):

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## Usage

```bash
# Use default subscription
auto-v2ray

# Custom subscription URL
auto-v2ray --sub https://example.com/vmess.txt

# Custom port
auto-v2ray --port 1080

# Test more candidates before picking the best
auto-v2ray --top 20
```

## What it does

1. Fetches vmess configs from the subscription URL
2. Runs a concurrent TCP latency test on all servers
3. HTTP-tests the top candidates through a real sing-box instance
4. Starts a SOCKS5 server on `0.0.0.0:<port>` (accessible from all devices on LAN)
5. Prints proxy URLs + QR codes for localhost and LAN

## Output example

```
✓ Best: sg1.example.com:443  latency: 142ms

  Local:  socks5://127.0.0.1:1080
  LAN:    socks5://192.168.1.42:1080

  [QR — Local]        [QR — LAN]
  ...
```

Scan the LAN QR code with your phone to configure its proxy settings.

## Requirements

- Python 3.8+
- curl or wget
- macOS or Linux (arm64 / amd64)

## Default subscription

```
https://raw.githubusercontent.com/ebrasha/free-v2ray-public-list/refs/heads/main/vmess_configs.txt
```

Override at any time with `--sub`.
