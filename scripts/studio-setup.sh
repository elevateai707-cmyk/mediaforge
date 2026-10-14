#!/usr/bin/env bash
# =============================================================================
# Elevate Vision Studio — build + install on Ubuntu (idempotent)
#
# Elevate Vision Studio is Elevate Vision Digital's build of FilmCraft (a
# pure-Rust, Premiere-style editor). This script:
#   1. installs build deps (apt, asks for sudo once) and Rust (rustup) if missing;
#   2. fetches FilmCraft at the exact commit pinned in studio/brand.json;
#   3. applies studio/rebrand.py (name, icon, wordmark, links, binary names);
#   4. builds `evstudio` (desktop app) + `evstudio-cli` (headless, used by
#      MediaForge's /api/export/filmcraft and the agents' studio_build tool);
#   5. installs to ~/.local/opt/evstudio/<commit>/, links ~/.local/bin, and adds
#      a desktop launcher + icons.
#
# First build: ~10–20 min (all codecs compile); updates reuse the cache.
#
# Knobs:
#   STUDIO_CACHE=~/.cache/elevate-studio   source + cargo target cache
#   STUDIO_AGENT_MCP=1   also register `evstudio-cli mcp` with Claude Code /
#                        Codex / Hermes. OFF by default: FilmCraft's MCP server
#                        has no folder sandbox (it can read/write anywhere you
#                        can). Agents already get studio_build via MediaForge.
#   FORCE=1              rebuild even if this commit is already installed
# =============================================================================
set -euo pipefail

MF_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRAND="$MF_ROOT/studio/brand.json"
CACHE="${STUDIO_CACHE:-$HOME/.cache/elevate-studio}"
BIN_DIR="$HOME/.local/bin"

GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
info() { echo -e "  ${CYAN}→${NC} $*"; }
warn() { echo -e "  ${YELLOW}!${NC} $*"; }
err()  { echo -e "  ${RED}✗${NC} $*"; }

b() { python3 -c 'import json,sys,functools; print(functools.reduce(lambda d,k: d[k], sys.argv[2:], json.load(open(sys.argv[1]))))' "$BRAND" "$@"; }
PRODUCT="$(b product)"; APP_ID="$(b app_id)"; EXE="$(b binary)"; CLI="$(b cli)"
UP_REPO="$(b upstream repo)"; UP_COMMIT="$(b upstream commit)"
SHORT="${UP_COMMIT:0:12}"
DEST="$HOME/.local/opt/evstudio/$SHORT"

echo
echo "================================================================"
echo "  $PRODUCT — build & install"
echo "  based on $UP_REPO @ $SHORT"
echo "================================================================"
echo

if [ -x "$DEST/$CLI" ] && [ "${FORCE:-0}" != 1 ]; then
  ok "already installed: $("$DEST/$CLI" --version) ($DEST; FORCE=1 to rebuild)"
else
  # --- 1. dependencies ----------------------------------------------------------
  PKGS=(build-essential pkg-config git curl python3 libasound2-dev libgtk-3-dev libxkbcommon-dev
        libwayland-dev libx11-dev libxcursor-dev libxrandr-dev libxi-dev libudev-dev)
  missing=()
  for p in "${PKGS[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p"); done
  if [ ${#missing[@]} -gt 0 ]; then
    info "installing build deps: ${missing[*]} (sudo)"
    sudo apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${missing[@]}"
  fi
  ok "build deps present"

  [ -f "$HOME/.cargo/env" ] && . "$HOME/.cargo/env"
  if ! command -v cargo >/dev/null; then
    info "installing Rust (rustup, minimal profile)"
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
    . "$HOME/.cargo/env"
  fi
  if ! rustc --version | awk '{split($2,v,"."); exit !(v[1]>1 || v[2]>=95)}'; then
    info "updating Rust (FilmCraft needs ≥ 1.95)"; rustup update stable
  fi
  ok "$(rustc --version)"

  # --- 2. pinned upstream source -------------------------------------------------
  SRC="$CACHE/upstream"
  mkdir -p "$CACHE"
  if [ ! -d "$SRC/.git" ]; then
    git init -q "$SRC" && git -C "$SRC" remote add origin "$UP_REPO"
  fi
  if [ "$(git -C "$SRC" rev-parse -q --verify HEAD 2>/dev/null)" != "$UP_COMMIT" ]; then
    info "fetching FilmCraft $SHORT"
    GIT_LFS_SKIP_SMUDGE=1 git -C "$SRC" fetch -q --depth 1 origin "$UP_COMMIT"
    git -C "$SRC" -c advice.detachedHead=false checkout -q --force FETCH_HEAD
  fi
  [ "$(git -C "$SRC" rev-parse HEAD)" = "$UP_COMMIT" ] || { err "fetched commit doesn't match brand.json"; exit 1; }
  ok "source at $SHORT"

  # --- 3. rebrand a fresh copy -------------------------------------------------------
  TREE="$CACHE/build"
  rm -rf "$TREE"
  cp -a "$SRC" "$TREE"
  python3 "$MF_ROOT/studio/rebrand.py" "$TREE" >/dev/null
  ok "rebranded as $PRODUCT"

  # --- 4. build --------------------------------------------------------------------
  info "building (first time: 10–20 min)…"
  (cd "$TREE" && CARGO_TARGET_DIR="$CACHE/target" cargo build --release --locked -p filmcraft -p filmcraft-cli)
  OUT="$CACHE/target/release"
  ok "built $("$OUT/$CLI" --version)"

  # --- 5. install ------------------------------------------------------------------
  rm -rf "$DEST"; mkdir -p "$DEST" "$BIN_DIR"
  install -m755 "$OUT/$EXE" "$OUT/$CLI" "$DEST/"
  cp "$TREE/NOTICE" "$TREE/LICENSE-MIT" "$TREE/LICENSE-APACHE" "$TREE/ATTRIBUTION.md" "$DEST/"
  ln -sfn "$DEST/$EXE" "$BIN_DIR/$EXE"
  ln -sfn "$DEST/$CLI" "$BIN_DIR/$CLI"
  ok "installed → $DEST (linked in $BIN_DIR)"
fi

# --- desktop launcher + icons (cheap; always refreshed) ------------------------------
for s in 16 24 32 48 64 128 256 512; do
  install -Dm644 "$MF_ROOT/studio/assets/icon-$s.png" "$HOME/.local/share/icons/hicolor/${s}x${s}/apps/$APP_ID.png"
done
install -Dm644 "$MF_ROOT/studio/assets/icon.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/$APP_ID.svg"
mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/$APP_ID.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$PRODUCT
GenericName=Video Editor
Comment=Edit MediaForge reels on a real timeline
Exec=$BIN_DIR/$EXE %F
Icon=$APP_ID
Terminal=false
Categories=AudioVideo;Video;AudioVideoEditing;
StartupWMClass=$APP_ID
EOF
command -v update-desktop-database >/dev/null && update-desktop-database -q "$HOME/.local/share/applications" || true
ok "launcher: $PRODUCT (apps menu)"

# --- agents (opt-in raw MCP) ------------------------------------------------------------
if [ "${STUDIO_AGENT_MCP:-0}" = 1 ]; then
  warn "registering raw $CLI MCP: it is NOT folder-sandboxed (reads/writes anywhere you can)"
  python3 "$MF_ROOT/agents/register_agents.py" --studio-cli "$BIN_DIR/$CLI" --studio-only
fi

echo
echo "  MediaForge now builds editable $PRODUCT projects:"
echo "    agents → studio_build(plan_id)        API → POST /api/export/filmcraft"
echo "  Open one:  $EXE <exports/filmcraft/<plan>/<plan>.fcproj>"
echo
