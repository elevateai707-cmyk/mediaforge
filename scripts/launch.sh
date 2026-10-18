#!/usr/bin/env bash
# MediaForge quick launch: starts the backend if needed, opens the browser.
set -u
MF="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${MF_HOST:-127.0.0.1}"
PORT="${MF_PORT:-8420}"
BASE_URL="${MF_URL:-http://${HOST}:${PORT}}"
BASE_URL="${BASE_URL%/}"

# A user service may already own the backend. Never start a second instance
# behind it; use its configured URL when it can be determined.
if command -v systemctl >/dev/null 2>&1 && systemctl --user is-active --quiet mediaforge.service; then
  if ! curl -fsS -m 2 "$BASE_URL/api/health" >/dev/null 2>&1 && [ -z "${MF_URL:-}" ]; then
    exec_start="$(systemctl --user show mediaforge.service --property=ExecStart --value 2>/dev/null || true)"
    svc_host="$(printf '%s' "$exec_start" | sed -n 's/.*--host[= ]\{1,\}\([^ ]*\).*/\1/p')"
    svc_port="$(printf '%s' "$exec_start" | sed -n 's/.*--port[= ]\{1,\}\([0-9][0-9]*\).*/\1/p')"
    if [ -n "$svc_host" ] && [ -n "$svc_port" ]; then
      HOST="$svc_host"; PORT="$svc_port"; BASE_URL="http://${HOST}:${PORT}"
    else
      echo "mediaforge.service is active but its endpoint is unavailable; set MF_URL (for example http://127.0.0.1:8420) to continue." >&2
      exit 1
    fi
  fi
  if ! curl -fsS -m 2 "$BASE_URL/api/health" >/dev/null 2>&1; then
    echo "mediaforge.service is active but MF_URL=$BASE_URL is not responding; refusing to start a duplicate backend." >&2
    exit 1
  fi
fi

# Already running?
if curl -fsS -m 2 "$BASE_URL/api/health" >/dev/null 2>&1; then
  echo "MediaForge already running at $BASE_URL"
else
  echo "Starting MediaForge backend..."
  cd "$MF/backend" || exit 1
  nohup .venv/bin/python -m uvicorn app.main:app --host "$HOST" --port "$PORT" \
    >/tmp/mediaforge_backend.log 2>&1 &
  # wait for readiness (up to 30s)
  for i in $(seq 1 30); do
    if curl -fsS -m 2 "$BASE_URL/api/health" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
  if ! curl -fsS -m 2 "$BASE_URL/api/health" >/dev/null 2>&1; then
    echo "Backend did not become ready; inspect /tmp/mediaforge_backend.log." >&2
    exit 1
  fi
  echo "Backend up (log: /tmp/mediaforge_backend.log)"
fi

# Open the browser
URL="$BASE_URL/"
echo "Opening $URL"
if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL" >/dev/null 2>&1 &
else
  echo "Open this in your browser: $URL"
fi

exit 0
