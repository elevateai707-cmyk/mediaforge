#!/usr/bin/env bash
# MediaForge quick launch: verify startup, then open the built editor.
set -euo pipefail
MF_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"$MF_ROOT/scripts/run.sh"
URL="http://localhost:8420/studio"
echo "Opening $URL"
if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL" >/dev/null 2>&1 &
fi
