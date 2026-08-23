#!/usr/bin/env bash
# Start Winston and open it. That is the whole script.
set -euo pipefail
cd "$(dirname "$0")"

PORT="${WINSTON_PORT:-5001}"

# Clear any previous run so the port is free.
pkill -f winston_app.py 2>/dev/null || true
sleep 1

[ -d venv ] && source venv/bin/activate

echo "Starting Winston..."
WINSTON_PORT="$PORT" python3 winston_app.py > /tmp/winston.log 2>&1 &

# Wait for it to actually answer before opening a browser at a dead page.
for _ in $(seq 1 30); do
  if curl -sf "http://localhost:$PORT/health" > /dev/null 2>&1; then
    echo "Winston is up on http://localhost:$PORT"
    open "http://localhost:$PORT"
    exit 0
  fi
  sleep 0.5
done

echo "Winston did not start. Last 20 lines of /tmp/winston.log:"
tail -20 /tmp/winston.log
exit 1
