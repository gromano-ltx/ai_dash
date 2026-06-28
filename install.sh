#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="$HOME/.ai_dash"
CONFIG_FILE="$INSTALL_DIR/config.json"
COLLECTOR_FILE="$INSTALL_DIR/collector.py"
VENV_DIR="$INSTALL_DIR/venv"

# ── prompt ────────────────────────────────────────────────────────────────────
echo ""
echo "  ai-dash collector installer"
echo ""

read -rp "  Server URL (e.g. https://ai-dash.yourco.com): " SERVER_URL
read -rp "  API key: " API_KEY
echo ""

SERVER_URL="${SERVER_URL%/}"

# ── dirs ──────────────────────────────────────────────────────────────────────
mkdir -p "$INSTALL_DIR"

# ── config ────────────────────────────────────────────────────────────────────
cat > "$CONFIG_FILE" <<EOF
{"url": "$SERVER_URL", "key": "$API_KEY"}
EOF

# ── download collector ────────────────────────────────────────────────────────
echo "  → downloading collector..."
curl -fsSL "$SERVER_URL/collector.py" -o "$COLLECTOR_FILE"
chmod +x "$COLLECTOR_FILE"

# ── python venv ───────────────────────────────────────────────────────────────
echo "  → setting up python environment..."
python3 -m venv "$VENV_DIR" --quiet
"$VENV_DIR/bin/pip" install --quiet httpx watchfiles

# ── install as background service ─────────────────────────────────────────────
OS="$(uname -s)"

if [[ "$OS" == "Darwin" ]]; then
    PLIST="$HOME/Library/LaunchAgents/com.ai-dash.collector.plist"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ai-dash.collector</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV_DIR/bin/python3</string>
        <string>$COLLECTOR_FILE</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$INSTALL_DIR/collector.log</string>
    <key>StandardErrorPath</key>
    <string>$INSTALL_DIR/collector.log</string>
</dict>
</plist>
EOF
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST"
    echo "  → service installed (launchd)"

elif [[ "$OS" == "Linux" ]]; then
    SERVICE_DIR="$HOME/.config/systemd/user"
    mkdir -p "$SERVICE_DIR"
    cat > "$SERVICE_DIR/ai-dash-collector.service" <<EOF
[Unit]
Description=ai-dash collector
After=network.target

[Service]
ExecStart=$VENV_DIR/bin/python3 $COLLECTOR_FILE
Restart=always
RestartSec=10
StandardOutput=append:$INSTALL_DIR/collector.log
StandardError=append:$INSTALL_DIR/collector.log

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now ai-dash-collector
    echo "  → service installed (systemd)"

else
    echo "  → unsupported OS: $OS. Run manually:"
    echo "     $VENV_DIR/bin/python3 $COLLECTOR_FILE"
fi

echo ""
echo "  ✓ done. Collector is running in the background."
echo "  Logs: $INSTALL_DIR/collector.log"
echo ""
