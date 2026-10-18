"""MediaForge backend configuration.

All paths are derived from the monorepo root unless overridden by environment
variables (used by the test suite to keep tests off the real data dirs).
"""
from __future__ import annotations

import os
from pathlib import Path

# Must be set before torch is first imported (config is imported first). The
# 4 GB card is shared with Ollama, so fragmentation matters.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

# ---------------------------------------------------------------------------
# Monorepo root: the repo root (<root>/backend/app/config.py), NOT /mediaforge.
# Override with MF_ROOT.
# ---------------------------------------------------------------------------
MF_ROOT = Path(
    os.environ.get("MF_ROOT") or Path(__file__).resolve().parents[2]
).resolve()

# ---------------------------------------------------------------------------
# Data directories (override for tests via env)
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("MF_DATA_DIR", str(MF_ROOT / "data"))).resolve()
EXPORTS_DIR = Path(os.environ.get("MF_EXPORTS_DIR", str(MF_ROOT / "exports"))).resolve()
MODELS_DIR = Path(os.environ.get("MF_MODELS_DIR", str(MF_ROOT / "models"))).resolve()
THUMBS_DIR = Path(os.environ.get("MF_THUMBS_DIR", str(DATA_DIR / "thumbs"))).resolve()
PROXIES_DIR = Path(os.environ.get("MF_PROXIES_DIR", str(DATA_DIR / "proxies"))).resolve()
TOUCHUP_DIR = Path(os.environ.get("MF_TOUCHUP_DIR", str(EXPORTS_DIR / "touchup"))).resolve()
DB_PATH = Path(os.environ.get("MF_DB_PATH", str(DATA_DIR / "mediaforge.db"))).resolve()
RENDER_TMP_DIR = Path(os.environ.get("MF_RENDER_TMP", str(DATA_DIR / "render_tmp"))).resolve()

VERSION = "2.0.0"
APP_HOST = os.environ.get("MF_HOST", "127.0.0.1")
APP_PORT = int(os.environ.get("MF_PORT", "8420"))
API_TOKEN = os.environ.get("MF_API_TOKEN", "")

# ---------------------------------------------------------------------------
# Feature flags / runtime knobs
# ---------------------------------------------------------------------------
# AI worker: set 0 to disable the background pipeline entirely.
AI_ENABLED = os.environ.get("MF_AI_ENABLED", "1") == "1"
# Skip individual pipeline phases (used by tests to avoid big model downloads).
SKIP_WHISPER = os.environ.get("MF_SKIP_WHISPER", "0") == "1"
SKIP_FACES = os.environ.get("MF_SKIP_FACES", "0") == "1"
SKIP_CAPTIONS = os.environ.get("MF_SKIP_CAPTIONS", "0") == "1"
# Allow the pipeline to auto-pull qwen2.5vl:7b via `ollama pull` on first use.
# Disabled in tests so a 7 GB pull never happens accidentally.
ALLOW_OLLAMA_PULL = os.environ.get("MF_ALLOW_OLLAMA_PULL", "1") == "1"

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("MF_OLLAMA_MODEL", "qwen2.5vl:7b")
OLLAMA_TIMEOUT = float(os.environ.get("MF_OLLAMA_TIMEOUT", "300"))
# Ollama is shared with other apps and the in-process torch models compete
# for the same 4 GB card, so unload the VL model right after each request.
# "0" = unload immediately; any Ollama duration ("30s", "5m") or -1 also works.
OLLAMA_KEEP_ALIVE = os.environ.get("MF_OLLAMA_KEEP_ALIVE", "0")
# Ollama options.num_gpu for caption calls: unset = Ollama decides; "0" = run
# captions on CPU (slow but never contends for VRAM).
CAPTION_NUM_GPU = (int(os.environ["MF_CAPTION_NUM_GPU"])
                   if os.environ.get("MF_CAPTION_NUM_GPU", "").strip() else None)
# Free VRAM (MB) wanted before a caption call; below it the torch models
# (CLIP/whisper/faces) are released first.
CAPTION_MIN_FREE_MB = int(os.environ.get("MF_CAPTION_MIN_FREE_MB", "2800"))
# Alias used by app/ai/captions.py (kept in sync with OLLAMA_HOST).
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", OLLAMA_HOST)

MEDIA_DIRS = [
    d for d in os.environ.get("MEDIA_DIRS", "").split(",") if d.strip()
]

# Parallelism
FFMPEG_JOBS = int(os.environ.get("MF_FFMPEG_JOBS", "8"))
CLIP_JOBS = int(os.environ.get("MF_CLIP_JOBS", "4"))
WATCHER_ENABLED = os.environ.get("MF_WATCHER", "1") == "1"
MUSIC_DIR = os.environ.get("MF_MUSIC_DIR", "/srv/iris/media/music")
USE_CLOUD_LLM = os.environ.get("USE_CLOUD_LLM", "false").lower() in ("1", "true", "yes")

# ElevenLabs text-to-speech (story voiceover). The API key is read from the
# environment only (systemd EnvironmentFile); it is never logged or persisted.
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "kMQD669ym1wk5czHQfyu")
ELEVENLABS_MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2")

# Supported media extensions -> kind
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".mkv", ".avi", ".webm", ".prores", ".mts", ".m2ts", ".wmv", ".flv", ".3gp"}
# .prores-mov is a .mov with ProRes codec; treated as video by extension.
SUPPORTED_EXTS = IMAGE_EXTS | VIDEO_EXTS

# CLIP model
CLIP_MODEL = os.environ.get("MF_CLIP_MODEL", "ViT-B-32")
CLIP_PRETRAINED = os.environ.get("MF_CLIP_PRETRAINED", "laion2b_s34b_b79k")
CLIP_DIM = 512  # ViT-B-32 embedding dimension

# Face clustering
FACE_DISTANCE_THRESHOLD = float(os.environ.get("MF_FACE_THRESHOLD", "0.45"))

# Aesthetic ONNX model (optional). If missing a deterministic fallback scorer
# (seeded, documented) is used so a 0-10 score is always produced.
AESTHETIC_ONNX = MODELS_DIR / "aesthetic.onnx"
AESTHETIC_INPUT_DIM = 384  # contract-stated ONNX input; fallback is dim-agnostic

# Whisper
WHISPER_MODEL = os.environ.get("MF_WHISPER_MODEL", "small.en")
WHISPER_COMPUTE = os.environ.get("MF_WHISPER_COMPUTE", "int8")

# Face model
FACE_MODEL = os.environ.get("MF_FACE_MODEL", "buffalo_sc")

# Resolve scripting module (Linux install path)
RESOLVE_MODULE_DIR = os.environ.get("MF_RESOLVE_MODULE_DIR", "/opt/resolve/Developer/Scripting/Modules")
RESOLVE_LIBS_DIR = os.environ.get("MF_RESOLVE_LIBS_DIR", "/opt/resolve/libs")

# Narrated renders with music: voice is normalised to -16 LUFS and the music bed sits
# this many dB below it (integrated), before sidechain ducking. MF_MUSIC_LEVELING=0
# restores the old un-normalised mix.
MUSIC_BED_DB = float(os.environ.get("MF_MUSIC_BED_DB", "18"))
MUSIC_LEVELING = os.environ.get("MF_MUSIC_LEVELING", "1") != "0"

# Render defaults
RENDER_CRF = 18
RENDER_PRESET = "medium"
RENDER_FPS = 30
XFADE_DURATION = 0.5

# CORS origins
CORS_ORIGINS = [origin.strip().rstrip("/") for origin in
                os.environ.get("MF_CORS_ORIGINS", "").split(",") if origin.strip()]

# Frontend dist served at / when present
FRONTEND_DIST = Path(os.environ.get("MF_FRONTEND_DIST", str(MF_ROOT / "frontend" / "dist")))

# DejaVu Sans font for burned-in captions
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


def find_font() -> str | None:
    """Return the first existing caption font, or None (ffmpeg default)."""
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return p
    return None


def ensure_dirs() -> None:
    """Create all runtime directories (idempotent)."""
    for d in (DATA_DIR, EXPORTS_DIR, MODELS_DIR, THUMBS_DIR, PROXIES_DIR,
              TOUCHUP_DIR, RENDER_TMP_DIR):
        d.mkdir(parents=True, exist_ok=True)


def load_env_file() -> None:
    """Load .env if present (python-dotenv optional)."""
    env_path = MF_ROOT / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(env_path)
    except Exception:
        pass
