"""Speech-to-text via faster-whisper (small.en, int8 quantization).

Lazy singleton model; CUDA when torch says so, CPU otherwise. ``transcribe``
returns segment timings plus per-word timings (used by the renderer's burned
captions). Any load/inference failure marks the model unavailable and
returns None — the pipeline skips the transcript phase gracefully.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from .. import config
from .gpu import device as gpu_device, set_model_status, clear_gpu_cache

log = logging.getLogger("mediaforge.whisper")

_model: Any = None
_device: str = "cpu"
_LOAD_TRIED = False


def _load() -> bool:
    """Load the Whisper model once (downloads weights on first use)."""
    global _model, _device, _LOAD_TRIED
    if _model is not None:
        return True
    if _LOAD_TRIED:
        return False
    _LOAD_TRIED = True
    try:
        from faster_whisper import WhisperModel  # type: ignore
        _device = gpu_device()
        _model = WhisperModel(
            config.WHISPER_MODEL,
            device=_device,
            compute_type=config.WHISPER_COMPUTE,
        )
        set_model_status("whisper", "loaded")
        log.info("whisper %s (%s) loaded on %s", config.WHISPER_MODEL,
                 config.WHISPER_COMPUTE, _device)
        return True
    except Exception as exc:  # noqa: BLE001 - registry + degrade, never crash
        _model = None
        set_model_status("whisper", "unavailable",
                         f"{type(exc).__name__}: {exc}")
        log.warning("whisper model unavailable: %s", exc)
        return False


def available() -> bool:
    return _model is not None or _load()


def transcribe(path: str) -> Optional[dict]:
    """Transcribe a media file.

    Returns {"segments": [{"start", "end", "text"}], "words": [{"start",
    "end", "word"}]} or None when the model is unavailable or the file
    cannot be processed.
    """
    if not os.path.exists(path):
        log.info("whisper: file missing %s", path)
        return None
    if not available():
        return None
    try:
        segments_iter, _info = _model.transcribe(path, word_timestamps=True)
        segments: list[dict] = []
        words: list[dict] = []
        for seg in segments_iter:
            segments.append({
                "start": round(float(seg.start), 3),
                "end": round(float(seg.end), 3),
                "text": (seg.text or "").strip(),
            })
            for w in (seg.words or []):
                words.append({
                    "start": round(float(w.start), 3),
                    "end": round(float(w.end), 3),
                    "word": (w.word or "").strip(),
                })
        if not segments:
            return None
        return {"segments": segments, "words": words}
    except Exception as exc:  # noqa: BLE001 - per-call resilience
        log.warning("whisper transcribe failed for %s: %s", path, exc)
        return None


def unload() -> None:
    """Release the model (frees VRAM between pipeline phases)."""
    global _model
    _model = None
    clear_gpu_cache()
    set_model_status("whisper", "not_loaded")
