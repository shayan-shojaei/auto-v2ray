#!/usr/bin/env bash
set -euo pipefail

REPO="https://raw.githubusercontent.com/shayan-shojaei/auto-v2ray/main"
SCRIPT_DEST="$HOME/.auto-v2ray/auto_v2ray.py"
SINGBOX_DIR="$HOME/.auto-v2ray"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
green() { printf '\033[32m%s\033[0m\n' "$*"; }
bold()  { printf '\033[1m%s\033[0m\n'  "$*"; }
die()   { printf '\033[31mError: %s\033[0m\n' "$*"; exit 1; }

command -v curl &>/dev/null || die "curl is required. Install it with: brew install curl  or  apt install curl"

# ---------------------------------------------------------------------------
# 1. Ensure uv is installed
# ---------------------------------------------------------------------------
bold "Checking for uv..."

if ! command -v uv &>/dev/null; then
  echo "  uv not found — installing uv (manages Python + dependencies automatically)..."
  curl -fsSL https://astral.sh/uv/install.sh | sh
  # uv installs itself; source the env file it drops so we can use it immediately
  for env_file in "$HOME/.local/bin/env" "$HOME/.cargo/env"; do
    [ -f "$env_file" ] && source "$env_file" && break
  done
  # Broad fallback: prepend common uv install locations
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

UV=$(command -v uv 2>/dev/null) || die "uv installation failed. Try manually: https://docs.astral.sh/uv/"
green "  uv found: $UV"

# ---------------------------------------------------------------------------
# 2. Pick install dir — use the same directory uv lives in (guaranteed in PATH)
# ---------------------------------------------------------------------------
UV_DIR=$(dirname "$UV")

# Prefer UV_DIR if writable; fall back to the first writable dir already in PATH
pick_install_dir() {
  # Try uv's own directory first
  if [ -w "$UV_DIR" ]; then
    echo "$UV_DIR"
    return
  fi
  # Walk PATH (as seen by the login shell via /etc/profile + shell rc)
  local shell_path
  shell_path=$(bash -lc 'echo $PATH' 2>/dev/null || echo "$PATH")
  IFS=: read -ra dirs <<< "$shell_path"
  for dir in "${dirs[@]}"; do
    case "$dir" in /usr/bin|/bin|/sbin|/usr/sbin|/System/*|/var/*) continue ;; esac
    if [ -d "$dir" ] && [ -w "$dir" ]; then
      echo "$dir"
      return
    fi
  done
  # Last resort: ~/.local/bin
  mkdir -p "$HOME/.local/bin"
  echo "$HOME/.local/bin"
}

INSTALL_DIR=$(pick_install_dir)
WRAPPER="$INSTALL_DIR/auto-v2ray"

# ---------------------------------------------------------------------------
# 3. Download auto_v2ray.py
# ---------------------------------------------------------------------------
bold "Downloading auto_v2ray.py..."
mkdir -p "$SINGBOX_DIR"
curl -fsSL "$REPO/auto_v2ray.py" -o "$SCRIPT_DEST"
green "  Saved to $SCRIPT_DEST"

# ---------------------------------------------------------------------------
# 4. Download sing-box
# ---------------------------------------------------------------------------
bold "Setting up sing-box..."

OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)
case "$ARCH" in
  arm64|aarch64) ARCH="arm64" ;;
  x86_64|amd64)  ARCH="amd64" ;;
  *) die "Unsupported architecture: $ARCH" ;;
esac

if [ ! -f "$SINGBOX_DIR/sing-box" ]; then
  RELEASE_JSON=$(curl -fsSL "https://api.github.com/repos/SagerNet/sing-box/releases/latest") \
    || die "Failed to fetch sing-box release info from GitHub"

  # Detect API errors (rate limit, etc.) before trying to parse
  if echo "$RELEASE_JSON" | grep -q '"message"'; then
    API_MSG=$(echo "$RELEASE_JSON" | grep '"message"' | head -1 \
      | sed 's/.*"message": *"\([^"]*\)".*/\1/')
    die "GitHub API error: $API_MSG"
  fi

  VERSION=$(echo "$RELEASE_JSON" | grep '"tag_name"' | head -1 \
    | sed 's/.*"tag_name": *"v*\([^"]*\)".*/\1/' || true)
  [ -z "$VERSION" ] && die "Could not parse sing-box version from GitHub API response"

  ASSET="sing-box-${VERSION}-${OS}-${ARCH}.tar.gz"
  DOWNLOAD_URL=$(echo "$RELEASE_JSON" | grep "browser_download_url" \
    | grep "\"${ASSET}\"" | head -1 \
    | sed 's/.*"browser_download_url": *"\([^"]*\)".*/\1/' || true)

  [ -z "$DOWNLOAD_URL" ] && die "Could not find sing-box release asset: $ASSET"

  echo "  Downloading sing-box v${VERSION} (${OS}/${ARCH})..."
  curl -fsSL --progress-bar "$DOWNLOAD_URL" -o "$SINGBOX_DIR/$ASSET"
  tar -xzf "$SINGBOX_DIR/$ASSET" -C "$SINGBOX_DIR" \
    --strip-components=1 "sing-box-${VERSION}-${OS}-${ARCH}/sing-box"
  chmod +x "$SINGBOX_DIR/sing-box"
  rm -f "$SINGBOX_DIR/$ASSET"
  green "  sing-box installed at $SINGBOX_DIR/sing-box"
else
  green "  sing-box already present at $SINGBOX_DIR/sing-box"
fi

# ---------------------------------------------------------------------------
# 5. Create CLI wrapper
# ---------------------------------------------------------------------------
bold "Installing auto-v2ray command..."

cat > "$WRAPPER" <<EOF
#!/usr/bin/env bash
exec "$UV" run "$SCRIPT_DEST" "\$@"
EOF

chmod +x "$WRAPPER"
green "  Command installed: $WRAPPER"

# ---------------------------------------------------------------------------
# 6. PATH check — warn only if the chosen dir is somehow not in PATH
# ---------------------------------------------------------------------------
login_path=$(bash -lc 'echo $PATH' 2>/dev/null || echo "$PATH")
if ! echo ":${login_path}:" | grep -q ":${INSTALL_DIR}:"; then
  echo ""
  bold "Add $INSTALL_DIR to your PATH. Paste into your shell config (~/.zshrc or ~/.bashrc):"
  echo ""
  echo "    export PATH=\"$INSTALL_DIR:\$PATH\""
  echo ""
  bold "Then restart your shell or run:  source ~/.zshrc"
  echo ""
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
green ""
green "Installation complete!"
green ""
green "  Run:  auto-v2ray"
green "  Help: auto-v2ray --help"
green ""
