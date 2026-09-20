#!/usr/bin/env bash
# Keep MediaForge running independently of the launching terminal.
set -euo pipefail
MF_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$UNIT_DIR"
if [ ! -f "$MF_ROOT/frontend/dist/index.html" ]; then
  echo 'Build the frontend first: npm run build --prefix frontend' >&2
  exit 1
fi
cat > "$UNIT_DIR/mediaforge.service" <<UNIT
[Unit]
Description=MediaForge local video editor
After=network.target

[Service]
Type=simple
WorkingDirectory=$MF_ROOT/backend
ExecStart=$MF_ROOT/backend/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8420
Restart=on-failure
RestartSec=3
TimeoutStopSec=30
KillMode=mixed

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now mediaforge.service
for attempt in $(seq 1 45); do
  if curl -fsS --max-time 2 http://127.0.0.1:8420/api/editor/schema >/dev/null 2>&1; then
    echo 'MediaForge is running: http://localhost:8420/studio'
    exit 0
  fi
  sleep 1
done
echo 'Startup failed. Inspect: journalctl --user -u mediaforge.service -n 50' >&2
exit 1
