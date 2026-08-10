#!/usr/bin/env bash
# =============================================================================
# MediaForge — one-shot, idempotent setup for Ubuntu 24.04
#
# Installs:
#   a. system deps (ffmpeg, exiftool)          b. ollama + qwen2.5vl:7b
#   c. backend Python venv + deps              d. frontend npm install + build
#   e. data/exports/models dirs                f. .env.example
#
# Safe to re-run any number of times: every step is guarded and skipped with
# a "✓ already" message when its work is already done.
#
# NOTE: passwordless sudo is NOT configured on this machine. This script will
# prompt for your sudo password when it needs to install system packages or
# ollama. Type it when prompted — it is only used by apt/install.sh.
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MF_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$MF_ROOT/backend"
FRONTEND_DIR="$MF_ROOT/frontend"
VENV_DIR="$BACKEND_DIR/.venv"
DATA_DIR="$MF_ROOT/data"
EXPORTS_DIR="$MF_ROOT/exports"
MODELS_DIR="$MF_ROOT/models"
OLLAMA_HOST="${OLLAMA_HOST:-http://127.0.0.1:11434}"
OLLAMA_MODEL="qwen2.5vl:7b"
NODE_BIN="/home/bfam/.nvm/versions/node/v22.22.3/bin"

GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
info() { echo -e "  ${CYAN}→${NC} $*"; }
warn() { echo -e "  ${YELLOW}!${NC} $*"; }
err()  { echo -e "  ${RED}✗${NC} $*"; }

# ---------------------------------------------------------------------------
# 0. Banner + OS check
# ---------------------------------------------------------------------------
echo
echo "================================================================"
echo "  MediaForge setup"
echo "  root: $MF_ROOT"
echo "================================================================"
echo

if ! command -v lsb_release >/dev/null 2>&1 || ! lsb_release -ds | grep -qi "Ubuntu 24.04"; then
  warn "This script targets Ubuntu 24.04. Detected: $(lsb_release -ds 2>/dev/null || echo unknown)"
  warn "Continuing anyway — package names may differ on other distros."
else
  ok "OS check passed: $(lsb_release -ds)"
fi

# ---------------------------------------------------------------------------
# a. System deps: ffmpeg, exiftool, curl
# ---------------------------------------------------------------------------
echo
echo "── System dependencies ──────────────────────────────────────────"

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v exiftool >/dev/null 2>&1; then
  info "Installing ffmpeg + libimage-exiftool-perl (sudo password will be asked)..."
  sudo apt-get update -y
  sudo apt-get install -y ffmpeg libimage-exiftool-perl
  ok "apt packages installed."
else
  ok "already: ffmpeg + exiftool present."
fi

for bin in ffmpeg exiftool curl; do
  if command -v "$bin" >/dev/null 2>&1; then
    ok "already: $bin -> $(command -v "$bin")"
  else
    err "MISSING: $bin — install it manually (apt-get install -y $bin)."
  fi
done

# ---------------------------------------------------------------------------
# b. ollama + qwen2.5vl:7b
# ---------------------------------------------------------------------------
echo
echo "── Ollama + vision model ────────────────────────────────────────"

if ! command -v ollama >/dev/null 2>&1; then
  info "ollama not found — installing via official script (sudo password will be asked)..."
  curl -fsSL https://ollama.com/install.sh | sh
  ok "ollama installed."
else
  ok "already: ollama -> $(command -v ollama)"
fi

if ! curl -sf "$OLLAMA_HOST/api/tags" >/dev/null 2>&1; then
  info "ollama serve not running — starting it in the background..."
  if ! command -v ollama >/dev/null 2>&1; then
    export PATH="$PATH:/usr/local/bin"
  fi
  nohup ollama serve >/tmp/mediaforge_ollama.log 2>&1 &
  # Poll until the API answers (max ~10s)
  for _ in $(seq 1 10); do
    if curl -sf "$OLLAMA_HOST/api/tags" >/dev/null 2>&1; then break; fi
    sleep 1
  done
  if curl -sf "$OLLAMA_HOST/api/tags" >/dev/null 2>&1; then
    ok "ollama serve is up ($OLLAMA_HOST)."
  else
    err "ollama serve did not answer on $OLLAMA_HOST — check /tmp/mediaforge_ollama.log"
  fi
else
  ok "already: ollama serve running on $OLLAMA_HOST."
fi

if ! ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$OLLAMA_MODEL"; then
  info "Pulling $OLLAMA_MODEL (~7 GB download — this can take several minutes)..."
  ollama pull "$OLLAMA_MODEL"
  ok "$OLLAMA_MODEL pulled."
else
  ok "already: model $OLLAMA_MODEL present."
fi

# ---------------------------------------------------------------------------
# c. Backend Python venv + deps
# ---------------------------------------------------------------------------
echo
echo "── Backend Python environment ───────────────────────────────────"

if [ ! -d "$VENV_DIR" ]; then
  info "Creating venv at $VENV_DIR ..."
  python3 -m venv "$VENV_DIR"
  ok "venv created."
else
  ok "already: venv present."
fi

PY="$VENV_DIR/bin/python"
"$PY" -m pip install --upgrade pip -q
ok "pip upgraded."

if [ -f "$BACKEND_DIR/requirements.txt" ]; then
  info "Installing backend deps from requirements.txt ..."
  "$PY" -m pip install -r "$BACKEND_DIR/requirements.txt"
  ok "backend deps installed."
elif [ -f "$BACKEND_DIR/pyproject.toml" ]; then
  info "Installing backend (editable) from pyproject.toml ..."
  "$PY" -m pip install -e "$BACKEND_DIR"
  ok "backend installed editable."
else
  warn "No backend/requirements.txt or backend/pyproject.toml yet (backend build in progress)."
  warn "Re-run ./scripts/setup.sh after the backend lands to finish pip install."
fi

# ---------------------------------------------------------------------------
# d. Frontend: npm install + build
# ---------------------------------------------------------------------------
echo
echo "── Frontend (npm install + build) ───────────────────────────────"

export PATH="$NODE_BIN:$PATH"
if ! command -v node >/dev/null 2>&1; then
  err "node not found at $NODE_BIN — is nvm/node v22 installed?"
  warn "Skipping frontend steps."
else
  ok "node $(node --version) / npm $(npm --version)"
  if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
    info "npm install ..."
    if ! (cd "$FRONTEND_DIR" && npm install); then
      warn "First npm install failed (registry hiccup?) — retrying once ..."
      (cd "$FRONTEND_DIR" && npm install)
    fi
    ok "npm install done."
  else
    ok "already: node_modules present."
  fi
  if [ ! -d "$FRONTEND_DIR/dist" ]; then
    info "npm run build ..."
    (cd "$FRONTEND_DIR" && npm run build)
    ok "frontend built -> frontend/dist"
  else
    ok "already: frontend/dist present."
  fi
fi

# ---------------------------------------------------------------------------
# e. Data dirs
# ---------------------------------------------------------------------------
echo
echo "── Data directories ─────────────────────────────────────────────"

mkdir -p "$DATA_DIR" "$EXPORTS_DIR" "$MODELS_DIR"
for d in "$DATA_DIR" "$EXPORTS_DIR" "$MODELS_DIR"; do
  ok "already/ensured: $d"
done

# ---------------------------------------------------------------------------
# f. .env.example
# ---------------------------------------------------------------------------
echo
echo "── Environment template ─────────────────────────────────────────"

ENV_EXAMPLE="$MF_ROOT/.env.example"
if [ ! -f "$ENV_EXAMPLE" ]; then
  cat > "$ENV_EXAMPLE" <<'EOF'
# MediaForge runtime configuration (copy to .env and edit)
USE_CLOUD_LLM=false
OLLAMA_HOST=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5vl:7b
MEDIA_DIRS=
EXPORT_DIR=/home/bfam/mediaforge/exports
EOF
  ok "wrote $ENV_EXAMPLE"
else
  ok "already: $ENV_EXAMPLE present."
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo
echo "================================================================"
echo "  Setup summary"
echo "================================================================"
echo
echo "  ffmpeg   : $(ffmpeg -version 2>/dev/null | head -1 || echo MISSING)"
echo "  exiftool : $(exiftool -ver 2>/dev/null || echo MISSING)"
echo "  ollama   : $(ollama --version 2>/dev/null || echo MISSING)"
echo "  node     : $(node --version 2>/dev/null || echo MISSING) (npm $(npm --version 2>/dev/null || echo MISSING))"
echo "  python   : $("$PY" --version 2>/dev/null || echo MISSING) (venv: $VENV_DIR)"
echo
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
  echo "  GPU      : $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | head -1) — CUDA mode available"
else
  warn "  GPU      : no NVIDIA GPU detected — backend will run in CPU fallback mode (slower)."
fi
echo
echo "  Next step: ./scripts/run.sh"
echo "  (re-running this script is always safe — it skips finished steps)"
echo
