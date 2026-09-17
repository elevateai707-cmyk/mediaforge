#!/usr/bin/env bash
# =============================================================================
# MediaForge — start the backend (uvicorn :8420) and make the UI reachable.
#
# * Backend : .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8420
#             (background, PID -> /tmp/mediaforge_backend.pid)
# * Frontend: if frontend/dist exists the backend serves the built app;
#             otherwise a Vite dev server is started as a fallback
#             (background, PID -> /tmp/mediaforge_vite.pid)
#
# Stop everything with:
#   kill $(cat /tmp/mediaforge_backend.pid) $(cat /tmp/mediaforge_vite.pid) 2>/dev/null
# =============================================================================
set -euo pipefail

MF_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$MF_ROOT/backend"
FRONTEND_DIR="$MF_ROOT/frontend"
VENV_DIR="$BACKEND_DIR/.venv"
NODE_BIN="/home/bfam/.nvm/versions/node/v22.22.3/bin"
PORT=8420
VITE_PORT=5173
BACKEND_PID_FILE="/tmp/mediaforge_backend.pid"
VITE_PID_FILE="/tmp/mediaforge_vite.pid"

GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
info() { echo -e "  ${CYAN}→${NC} $*"; }
warn() { echo -e "  ${YELLOW}!${NC} $*"; }
err()  { echo -e "  ${RED}✗${NC} $*"; }

echo
echo "================================================================"
echo "  MediaForge run"
echo "================================================================"
echo

# --- Preflight --------------------------------------------------------------
export PATH="$NODE_BIN:$PATH"

if [ ! -d "$VENV_DIR" ]; then
  err "Backend venv missing ($VENV_DIR)."
  warn "Run ./scripts/setup.sh first (it creates the venv and installs deps), then re-run this script."
  exit 1
fi

if [ ! -f "$BACKEND_DIR/app/main.py" ]; then
  err "Backend app not found at $BACKEND_DIR/app/main.py — the backend build may still be in progress."
  exit 1
fi

# --- PID-file cleanup (stale pids from crashed runs) -------------------------
for pid_file in "$BACKEND_PID_FILE" "$VITE_PID_FILE"; do
  if [ -f "$pid_file" ]; then
    old_pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
      ok "already running: $pid_file -> pid $old_pid"
    else
      warn "stale pid file $pid_file (pid $old_pid not alive) — removing."
      rm -f "$pid_file"
    fi
  fi
done

# Prefer the persistent user service when installed.
SERVICE_MANAGED=0
if command -v systemctl >/dev/null && [ "$(systemctl --user show mediaforge.service -p LoadState --value 2>/dev/null || true)" = "loaded" ]; then
  systemctl --user start mediaforge.service
  SERVICE_MANAGED=1
fi

# --- Backend ----------------------------------------------------------------
echo
echo "── Backend (uvicorn :$PORT) ────────────────────────────────────"

if [ "$SERVICE_MANAGED" = 1 ] || curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
  ok "backend is started; verifying readiness on port $PORT."
else
  info "starting uvicorn ..."
  (cd "$BACKEND_DIR" && exec nohup "$VENV_DIR/bin/python" -m uvicorn app.main:app \
      --host 127.0.0.1 --port "$PORT") \
      >/tmp/mediaforge_backend.log 2>&1 &
  echo $! > "$BACKEND_PID_FILE"
  ok "backend started (pid $(cat "$BACKEND_PID_FILE"), log /tmp/mediaforge_backend.log)"
fi

# --- Frontend ---------------------------------------------------------------
echo
echo "── Frontend ────────────────────────────────────────────────────"

if [ -d "$FRONTEND_DIR/dist" ]; then
  ok "frontend/dist present — the backend serves the built app at :$PORT (no Vite needed)."
else
  if [ -f "$VITE_PID_FILE" ] && kill -0 "$(cat "$VITE_PID_FILE")" 2>/dev/null; then
    ok "Vite dev server already running (pid $(cat "$VITE_PID_FILE"))."
  else
    info "frontend/dist missing — starting Vite dev server as fallback ..."
    (cd "$FRONTEND_DIR" && exec nohup npm run dev -- --port "$VITE_PORT" --host 127.0.0.1) \
        >/tmp/mediaforge_vite.log 2>&1 &
    echo $! > "$VITE_PID_FILE"
    ok "Vite started (pid $(cat "$VITE_PID_FILE"), log /tmp/mediaforge_vite.log)"
  fi
fi

# --- Health check -----------------------------------------------------------
echo
echo "── Health check ────────────────────────────────────────────────"

ready=0
for attempt in $(seq 1 45); do
  if curl -fsS --max-time 2 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [ "$ready" != 1 ]; then
  err "Backend did not become healthy. See /tmp/mediaforge_backend.log."
  exit 1
fi
ok "Backend health check passed."

# --- URLs -------------------------------------------------------------------
echo
echo "================================================================"
echo "  MediaForge is up"
echo "================================================================"
echo
echo "  Local : http://localhost:$PORT/"
if [ ! -d "$FRONTEND_DIR/dist" ]; then
  echo
  echo "  Vite dev fallback (hot reload) : http://localhost:$VITE_PORT/"
fi
echo
echo "  Stop everything:"
echo "    kill \$(cat $BACKEND_PID_FILE) \$(cat $VITE_PID_FILE) 2>/dev/null"
echo "  Logs: /tmp/mediaforge_backend.log  /tmp/mediaforge_vite.log"
echo
