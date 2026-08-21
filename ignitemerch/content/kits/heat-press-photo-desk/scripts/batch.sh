#!/usr/bin/env bash
set -euo pipefail

# Folder-in, folder-out for Heat Press Photo Desk.
# Requires ComfyUI running locally with /prompt open.

COMFY="${COMFY_URL:-http://127.0.0.1:8188}"
IN_DIR="${1:-./in}"
OUT_DIR="${2:-./out}"
WORKFLOW="$(cd "$(dirname "$0")/.." && pwd)/workflows/heat-press-cutout.json"

if [[ ! -d "$IN_DIR" ]]; then
  echo "Usage: batch.sh ./in ./out"
  echo "Drop wall-shot photos in ./in"
  exit 1
fi

mkdir -p "$OUT_DIR"
shopt -s nullglob

for file in "$IN_DIR"/*.{png,jpg,jpeg,webp,PNG,JPG,JPEG,WEBP}; do
  name="$(basename "$file")"
  echo "Queue $name"
  curl -sS -X POST "$COMFY/upload/image" \
    -F "image=@${file}" \
    -F "overwrite=true" >/dev/null

  python3 - "$WORKFLOW" "$name" "$COMFY" <<'PY'
import json, sys, urllib.request
workflow_path, image_name, comfy = sys.argv[1], sys.argv[2], sys.argv[3]
graph = json.load(open(workflow_path))
for node in graph.get("nodes", []):
    if node.get("type") == "LoadImage":
        values = node.get("widgets_values", [])
        if values:
            values[0] = image_name
payload = {"prompt": graph}
req = urllib.request.Request(
    comfy.rstrip("/") + "/prompt",
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
)
urllib.request.urlopen(req).read()
PY
done

echo "Queued. Pull results from ComfyUI output, then copy keepers to $OUT_DIR"
