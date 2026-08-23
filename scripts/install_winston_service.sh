#!/usr/bin/env bash
# Install Winston as a macOS LaunchAgent so it starts at login and restarts on crash.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="studio.yardlink.winston"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST="$PLIST_DIR/${LABEL}.plist"
PORT="${WINSTON_PORT:-5001}"

[ "$(uname)" = "Darwin" ] || { echo "This installer is macOS only."; exit 1; }

PYTHON="$ROOT/venv/bin/python3"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"
[ -x "$PYTHON" ] || { echo "No python3 found."; exit 1; }

mkdir -p "$PLIST_DIR" "$ROOT/logs" "$ROOT/data/backups"

# Paths are resolved at install time rather than hardcoded, so this works for any user.
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${LABEL}</string>

  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON}</string>
    <string>${ROOT}/winston_app.py</string>
  </array>

  <key>WorkingDirectory</key><string>${ROOT}</string>

  <key>EnvironmentVariables</key>
  <dict>
    <key>WINSTON_PORT</key><string>${PORT}</string>
    <key>WINSTON_HOST</key><string>127.0.0.1</string>
    <key>PATH</key><string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>

  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key>
  <dict>
    <!-- Restart on crash, but not when stopped deliberately. -->
    <key>SuccessfulExit</key><false/>
    <key>Crashed</key><true/>
  </dict>

  <!-- Back off between restarts so a broken config cannot spin. -->
  <key>ThrottleInterval</key><integer>15</integer>

  <key>StandardOutPath</key><string>${ROOT}/logs/winston.log</string>
  <key>StandardErrorPath</key><string>${ROOT}/logs/winston.error.log</string>

  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
PLISTEOF

plutil -lint "$PLIST" > /dev/null || { echo "Generated plist is invalid."; exit 1; }

launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/${LABEL}"

echo "Waiting for Winston..."
for _ in $(seq 1 40); do
  if curl -sf "http://127.0.0.1:${PORT}/health" > /dev/null 2>&1; then
    echo
    echo "Winston is running."
    echo "  URL       http://127.0.0.1:${PORT}"
    echo "  Python    ${PYTHON}"
    echo "  Root      ${ROOT}"
    echo "  Logs      ${ROOT}/logs/winston.log"
    echo "  Autostart installed at login"
    echo
    echo "  Open it:  ./winstonctl open"
    echo "  Check it: ./winstonctl doctor"
    exit 0
  fi
  sleep 0.5
done

echo "Winston did not become healthy. Recent errors:"
tail -20 "$ROOT/logs/winston.error.log" 2>/dev/null
exit 1
