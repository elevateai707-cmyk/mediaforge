#!/usr/bin/env bash
# =============================================================================
# MediaForge — AI agent setup for Ubuntu (idempotent, no sudo)
#
#   1. Installs photocraft-cli from the official GitHub release of
#      storytold/photocraft, verified against the release's SHA256SUMS.txt,
#      into ~/.local/opt/photocraft/<tag> and links it into ~/.local/bin.
#   2. Registers the `mediaforge` and `photocraft` MCP servers, plus the
#      mediaforge skill, with every agent that's installed: Claude Code, Codex
#      and Hermes Agent (agents/register_agents.py).
#
# Knobs (environment variables):
#   MEDIAFORGE_URL       backend the agents talk to   (http://127.0.0.1:8420)
#   PHOTOCRAFT_VERSION   release tag or "latest"     (latest)
#   PC_READ_ROOT         folder the photocraft MCP server may read
#                        (first MEDIA_DIRS entry in .env, else ~/Pictures)
#   PC_WRITE_ROOT        folder it may write         (exports/photocraft-agent)
#   AGENTS               claude,codex,hermes
#   CAN_APPROVE=1        let agents approve edit plans (default: you approve)
#   FORCE=1              reinstall photocraft-cli even if present
# =============================================================================
set -euo pipefail

MF_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="storytold/photocraft"
PHOTOCRAFT_VERSION="${PHOTOCRAFT_VERSION:-latest}"
MEDIAFORGE_URL="${MEDIAFORGE_URL:-http://127.0.0.1:8420}"
AGENTS="${AGENTS:-claude,codex,hermes}"
INSTALL_ROOT="$HOME/.local/opt/photocraft"
BIN_DIR="$HOME/.local/bin"

GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
info() { echo -e "  ${CYAN}→${NC} $*"; }
warn() { echo -e "  ${YELLOW}!${NC} $*"; }
err()  { echo -e "  ${RED}✗${NC} $*"; }

echo
echo "================================================================"
echo "  MediaForge agents setup (Claude Code · Codex · Hermes)"
echo "================================================================"
echo

command -v python3 >/dev/null || { err "python3 is required"; exit 1; }
command -v curl >/dev/null || { err "curl is required (sudo apt install curl)"; exit 1; }

# --- 1. photocraft-cli --------------------------------------------------------
case "$(uname -m)" in
  x86_64|amd64)  ARCH=x86_64 ;;
  aarch64|arm64) ARCH=aarch64 ;;
  *) err "no PhotoCraft Linux build for $(uname -m)"; exit 1 ;;
esac

PC_CLI="$BIN_DIR/photocraft-cli"
if [ -x "$PC_CLI" ] && [ "$PHOTOCRAFT_VERSION" = latest ] && [ "${FORCE:-0}" != 1 ]; then
  ok "already installed: $("$PC_CLI" --version 2>/dev/null || echo "$PC_CLI") (FORCE=1 to update)"
else
  if [ "$PHOTOCRAFT_VERSION" = latest ]; then
    TAG="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
      | python3 -c 'import json,sys; print(json.load(sys.stdin)["tag_name"])')"
  else
    TAG="$PHOTOCRAFT_VERSION"
  fi
  info "PhotoCraft release $TAG ($ARCH)"
  BASE="https://github.com/$REPO/releases/download/$TAG"
  TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

  curl -fsSL "$BASE/SHA256SUMS.txt" -o "$TMP/SHA256SUMS.txt"
  ASSET="$(awk '{sub(/^\*/, "", $2); print $2}' "$TMP/SHA256SUMS.txt" \
    | grep -E "^photocraft-[^/]*-linux-${ARCH}\.tar\.gz$" | head -1 || true)"
  [ -n "$ASSET" ] || { err "no linux-$ARCH tarball listed in $TAG's SHA256SUMS.txt"; exit 1; }

  info "downloading $ASSET"
  curl -fSL --progress-bar "$BASE/$ASSET" -o "$TMP/$ASSET"
  ( cd "$TMP" && grep -E "[ *]${ASSET//./\\.}$" SHA256SUMS.txt | sha256sum -c --status - ) \
    || { err "SHA-256 mismatch for $ASSET; refusing to install"; exit 1; }
  ok "checksum verified ($ASSET)"

  DEST="$INSTALL_ROOT/$TAG"
  rm -rf "$DEST"; mkdir -p "$DEST" "$BIN_DIR"
  tar -xzf "$TMP/$ASSET" -C "$DEST"
  FOUND="$(find "$DEST" -type f -name photocraft-cli -perm -u+x | head -1)"
  [ -n "$FOUND" ] || { err "photocraft-cli not found inside $ASSET"; exit 1; }
  ln -sfn "$FOUND" "$PC_CLI"
  APP="$(find "$DEST" -type f -name photocraft -perm -u+x | head -1)"
  [ -n "$APP" ] && ln -sfn "$APP" "$BIN_DIR/photocraft"
  ok "installed $("$PC_CLI" --version) → $PC_CLI"
fi
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) warn "$BIN_DIR is not on PATH; add it in ~/.bashrc (MediaForge finds it either way)";; esac

# --- 2. PhotoCraft MCP sandbox roots ------------------------------------------
if [ -z "${PC_READ_ROOT:-}" ]; then
  PC_READ_ROOT="$HOME/Pictures"
  if [ -f "$MF_ROOT/.env" ]; then
    first="$(grep -E '^MEDIA_DIRS=' "$MF_ROOT/.env" | tail -1 | cut -d= -f2- | cut -d, -f1 || true)"
    [ -n "$first" ] && PC_READ_ROOT="$first"
  fi
fi
PC_WRITE_ROOT="${PC_WRITE_ROOT:-$MF_ROOT/exports/photocraft-agent}"
mkdir -p "$PC_WRITE_ROOT"
extra=()
if [ -d "$PC_READ_ROOT" ]; then
  extra+=(--pc-read-root "$PC_READ_ROOT")
  info "photocraft MCP may read $PC_READ_ROOT and write $PC_WRITE_ROOT only"
else
  warn "$PC_READ_ROOT doesn't exist; photocraft MCP gets no read access (set PC_READ_ROOT)"
fi

# --- 3. register with the agents ----------------------------------------------
[ "${CAN_APPROVE:-0}" = 1 ] && extra+=(--can-approve)
python3 "$MF_ROOT/agents/register_agents.py" \
  --url "$MEDIAFORGE_URL" --python "$(command -v python3)" --agents "$AGENTS" \
  --photocraft-cli "$PC_CLI" --pc-write-root "$PC_WRITE_ROOT" "${extra[@]}"

# --- 4. reachability ----------------------------------------------------------
if curl -fsS --max-time 3 "$MEDIAFORGE_URL/api/health" >/dev/null 2>&1; then
  ok "MediaForge reachable at $MEDIAFORGE_URL"
else
  warn "MediaForge not reachable at $MEDIAFORGE_URL yet; start it with ./scripts/run.sh"
fi

echo
echo "  Try it:"
echo "    claude  → \"/mcp__mediaforge__library_report\" or \"make a 30s TikTok of my last trip\""
echo "    codex   → \"use mediaforge to find my best beach photos and give them the film look\""
echo "    hermes  → \"what trips are in my MediaForge library?\""
echo
