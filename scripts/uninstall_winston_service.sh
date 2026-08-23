#!/usr/bin/env bash
# Remove Winston's autostart. Leaves the database, logs and backups untouched.
set -uo pipefail
LABEL="studio.yardlink.winston"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null
if [ -f "$PLIST" ]; then rm -f "$PLIST"; echo "Autostart removed."; else echo "Autostart was not installed."; fi
pkill -f winston_app.py 2>/dev/null && echo "Winston stopped."
echo "Your database, logs and backups were not touched."
