#!/usr/bin/env bash
# MediaForge quick launch: starts the backend if needed, opens the browser.
set -u
MF=/home/bfam/mediaforge
PORT=8420

# Already running?
if curl -s -m 2 "http://localhost:$PORT/api/health" >/dev/null 2>&1; then
  echo "MediaForge already running on :$PORT"
else
  echo "Starting MediaForge backend..."
  cd "$MF/backend" || exit 1
  nohup .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port $PORT \
    >/tmp/mediaforge_backend.log 2>&1 &
  # wait for readiness (up to 30s)
  for i in $(seq 1 30); do
    if curl -s -m 2 "http://localhost:$PORT/api/health" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
  echo "Backend up (log: /tmp/mediaforge_backend.log)"
fi

# Open the browser
URL="http://localhost:$PORT/"
echo "Opening $URL"
if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL" >/dev/null 2>&1 &
else
  echo "Open this in your browser: $URL"
fi

exit 0
