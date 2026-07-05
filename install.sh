#!/usr/bin/env bash
set -euo pipefail

AI_DASH_URL="${AI_DASH_URL:-https://dash.ai-coordinator.io}"
AI_DASH_DIR="$HOME/.ai_dash"
VENV_DIR="$AI_DASH_DIR/venv"
COLLECTOR_PY="$AI_DASH_DIR/collector.py"
CONFIG_FILE="$AI_DASH_DIR/config.json"

echo "[ai-dash] installing to $AI_DASH_DIR"
mkdir -p "$AI_DASH_DIR"

# 1. Dedicated venv — isolated from any other project's Python environment,
#    so an unrelated `pip install` elsewhere can never break the collector's deps.
if [ ! -d "$VENV_DIR" ]; then
  echo "[ai-dash] creating dedicated virtualenv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
else
  echo "[ai-dash] virtualenv already exists at $VENV_DIR, reusing"
fi
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet httpx watchfiles

# 2. Download collector.py (always refreshed, even on re-run)
echo "[ai-dash] downloading collector.py from $AI_DASH_URL"
curl -fsSL "$AI_DASH_URL/collector.py" -o "$COLLECTOR_PY"

# 3. Config — prompt only if missing, so re-running is idempotent
if [ ! -f "$CONFIG_FILE" ]; then
  read -rp "ai-dash API key: " AI_DASH_KEY
  cat > "$CONFIG_FILE" <<EOF
{"url": "$AI_DASH_URL", "key": "$AI_DASH_KEY"}
EOF
  echo "[ai-dash] wrote config to $CONFIG_FILE"
else
  echo "[ai-dash] config already exists at $CONFIG_FILE, leaving as-is"
fi

# 4. Service definition + load/start (always rewritten + reloaded, safe to re-run)
OS_NAME="$(uname -s)"
if [ "$OS_NAME" = "Darwin" ]; then
  PLIST="$HOME/Library/LaunchAgents/com.ai-dash.collector.plist"
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ai-dash.collector</string>
    <key>ProgramArguments</key>
    <array>
        <string>$VENV_DIR/bin/python</string>
        <string>$COLLECTOR_PY</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/dev/null</string>
    <key>StandardErrorPath</key>
    <string>/dev/null</string>
</dict>
</plist>
EOF
  launchctl unload "$PLIST" >/dev/null 2>&1 || true
  launchctl load "$PLIST"
  echo "[ai-dash] launchd service installed and started"
elif [ "$OS_NAME" = "Linux" ]; then
  SERVICE_DIR="$HOME/.config/systemd/user"
  SERVICE_FILE="$SERVICE_DIR/ai-dash-collector.service"
  mkdir -p "$SERVICE_DIR"
  cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=ai-dash collector

[Service]
ExecStart=$VENV_DIR/bin/python $COLLECTOR_PY
Restart=always
StandardOutput=null
StandardError=null

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now ai-dash-collector.service
  echo "[ai-dash] systemd service installed and started"
else
  echo "[ai-dash] unsupported OS '$OS_NAME' — run manually: $VENV_DIR/bin/python $COLLECTOR_PY" >&2
  exit 1
fi

echo "[ai-dash] done. Dashboard: $AI_DASH_URL"
echo "[ai-dash] logs: $AI_DASH_DIR/collector.log"
