#!/usr/bin/env bash
set -euo pipefail

REPO="https://raw.githubusercontent.com/shayan-shojaei/auto-v2ray/main"
INSTALL_DIR="$HOME/.local/bin"
SCRIPT_DEST="$HOME/.local/lib/auto_v2ray.py"
WRAPPER="$INSTALL_DIR/auto-v2ray"
SINGBOX_DIR="$HOME/.auto-v2ray"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
green() { printf '\033[32m%s\033[0m\n' "$*"; }
bold()  { printf '\033[1m%s\033[0m\n'  "$*"; }
die()   { printf '\033[31mError: %s\033[0m\n' "$*"; exit 1; }

command -v curl &>/dev/null || die "curl is required. Install it with: brew install curl  or  apt install curl"

# ---------------------------------------------------------------------------
# 1. Ensure uv is installed (uv manages Python + all deps automatically)
# ---------------------------------------------------------------------------
bold "Checking for uv..."

if ! command -v uv &>/dev/null; then
  echo "  uv not found — installing uv (the fast Python package manager)..."
  curl -fsSL https://astral.sh/uv/install.sh | sh
  # Add to PATH for the rest of this script
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

UV=$(command -v uv 2>/dev/null) || die "uv installation failed. Try manually: https://docs.astral.sh/uv/"
green "  uv found: $UV"

# ---------------------------------------------------------------------------
# 2. Download auto_v2ray.py
# ---------------------------------------------------------------------------
bold "Downloading auto_v2ray.py..."
mkdir -p "$(dirname "$SCRIPT_DEST")"
curl -fsSL "$REPO/auto_v2ray.py" -o "$SCRIPT_DEST"
green "  Saved to $SCRIPT_DEST"

# ---------------------------------------------------------------------------
# 3. Download sing-box
# ---------------------------------------------------------------------------
bold "Setting up sing-box..."

OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m)
case "$ARCH" in
  arm64|aarch64) ARCH="arm64" ;;
  x86_64|amd64)  ARCH="amd64" ;;
  *) die "Unsupported architecture: $ARCH" ;;
esac

mkdir -p "$SINGBOX_DIR"

if [ ! -f "$SINGBOX_DIR/sing-box" ]; then
  RELEASE_JSON=$(curl -fsSL "https://api.github.com/repos/SagerNet/sing-box/releases/latest")
  VERSION=$(echo "$RELEASE_JSON" | grep '"tag_name"' | head -1 \
    | sed 's/.*"tag_name": *"v*\([^"]*\)".*/\1/')
  ASSET="sing-box-${VERSION}-${OS}-${ARCH}.tar.gz"
  DOWNLOAD_URL=$(echo "$RELEASE_JSON" | grep "browser_download_url" \
    | grep "\"${ASSET}\"" | head -1 \
    | sed 's/.*"browser_download_url": *"\([^"]*\)".*/\1/')

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
# 4. Create CLI wrapper  (uv run handles Python version + deps via PEP 723)
# ---------------------------------------------------------------------------
bold "Installing auto-v2ray command..."
mkdir -p "$INSTALL_DIR"

cat > "$WRAPPER" <<EOF
#!/usr/bin/env bash
exec "$UV" run "$SCRIPT_DEST" "\$@"
EOF

chmod +x "$WRAPPER"
green "  Command installed: $WRAPPER"

# ---------------------------------------------------------------------------
# 5. PATH hint
# ---------------------------------------------------------------------------
if ! echo ":${PATH}:" | grep -q ":${INSTALL_DIR}:"; then
  echo ""
  bold "Add ~/.local/bin to your PATH. Paste this into your shell config (~/.zshrc or ~/.bashrc):"
  echo ""
  echo '    export PATH="$HOME/.local/bin:$PATH"'
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
