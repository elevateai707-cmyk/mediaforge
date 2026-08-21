"""Speech-to-text via faster-whisper (small.en, int8 quantization).

Lazy singleton model; CUDA when torch says so, CPU otherwise. ``transcribe``
returns segment timings plus per-word timings (used by the renderer's burned
captions). Any load/inference failure marks the model unavailable and
returns None — the pipeline skips the transcript phase gracefully.

Two failure modes are handled explicitly because both silently produced zero
transcripts for an entire library:

* **CUDA runtime mismatch.** ``WhisperModel(device="cuda")`` constructs fine —
  CTranslate2 only dlopens ``libcublas``/``libcudnn`` on the first inference.
  So the model reported "loaded" while *every* ``transcribe`` call failed with
  ``Library libcublas.so.12 is not found``. We now catch that at inference,
  permanently downgrade to CPU, and retry once. CPU int8 small.en is a few
  seconds per clip — slower than CUDA, infinitely better than nothing.
* **Files with no audio stream.** CTranslate2 raises ``tuple index out of
  range`` from its feature extractor on a silent/audio-less MP4. We probe with
  ffprobe first and skip cleanly instead of logging a bogus error.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Any, Optional

from .. import config
from ..proc import tool_env
from .gpu import device as gpu_device, set_model_status, clear_gpu_cache

log = logging.getLogger("mediaforge.whisper")

_model: Any = None
_device: str = "cpu"
_LOAD_TRIED = False
# Set once CTranslate2 has proven it cannot use this box's GPU. Survives
# unload/reload cycles so we never re-pay the failed-CUDA-load cost.
_CUDA_BROKEN = False

# Substrings that mean "this box cannot run CTranslate2 on the GPU" rather
# than "this particular file is bad". Matched case-insensitively.
_CUDA_ERROR_HINTS = (
    "libcublas",
    "libcudnn",
    "libcudart",
    "cuda driver",
    "cuda runtime",
    "cuda failed",
    "no cuda-capable device",
    "cudnn",
)


def _is_cuda_error(exc: Exception) -> bool:
    """True when the failure is the CUDA stack, not the input file."""
    msg = f"{type(exc).__name__}: {exc}".lower()
    return any(hint in msg for hint in _CUDA_ERROR_HINTS)


def _build(device: str) -> Any:
    """Construct a WhisperModel on `device` (raises on failure)."""
    from faster_whisper import WhisperModel  # type: ignore
    compute = config.WHISPER_COMPUTE if device == "cuda" else "int8"
    return WhisperModel(config.WHISPER_MODEL, device=device,
                        compute_type=compute)


def has_audio(path: str) -> bool:
    """True when the file carries at least one decodable audio stream.

    ffprobe runs under ``tool_env()`` so a Resolve export cannot poison it.
    Unknown/unprobeable files return True so we still attempt a transcript.
    """
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-print_format", "json", path],
            capture_output=True, text=True, timeout=30, env=tool_env())
        if proc.returncode != 0:
            return True
        streams = json.loads(proc.stdout or "{}").get("streams") or []
        return bool(streams)
    except Exception:  # noqa: BLE001 - probe is advisory only
        return True


def _load() -> bool:
    """Load the Whisper model once (downloads weights on first use)."""
    global _model, _device, _LOAD_TRIED
    if _model is not None:
        return True
    if _LOAD_TRIED:
        return False
    _LOAD_TRIED = True
    try:
        _device = "cpu" if _CUDA_BROKEN else gpu_device()
        _model = _build(_device)
        set_model_status("whisper", "loaded")
        log.info("whisper %s (%s) loaded on %s", config.WHISPER_MODEL,
                 config.WHISPER_COMPUTE, _device)
        return True
    except Exception as exc:  # noqa: BLE001 - registry + degrade, never crash
        if _device == "cuda" and _is_cuda_error(exc):
            return _fallback_to_cpu(exc)
        _model = None
        set_model_status("whisper", "unavailable",
                         f"{type(exc).__name__}: {exc}")
        log.warning("whisper model unavailable: %s", exc)
        return False


def _fallback_to_cpu(exc: Exception) -> bool:
    """Permanently rebuild the model on CPU after a CUDA failure."""
    global _model, _device, _CUDA_BROKEN
    log.warning("whisper: CUDA unusable (%s) — falling back to CPU int8", exc)
    _model = None
    clear_gpu_cache()
    _CUDA_BROKEN = True
    _device = "cpu"
    try:
        _model = _build("cpu")
        set_model_status("whisper", "loaded")
        log.info("whisper %s loaded on cpu (CUDA unavailable)",
                 config.WHISPER_MODEL)
        return True
    except Exception as cpu_exc:  # noqa: BLE001
        _model = None
        set_model_status("whisper", "unavailable",
                         f"{type(cpu_exc).__name__}: {cpu_exc}")
        log.warning("whisper unavailable on CPU too: %s", cpu_exc)
        return False


def available() -> bool:
    return _model is not None or _load()


def device() -> str:
    """Device the model is actually running on ('cuda' or 'cpu')."""
    return _device


def _run(path: str) -> Optional[dict]:
    """One transcribe pass against the loaded model."""
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


def transcribe(path: str) -> Optional[dict]:
    """Transcribe a media file.

    Returns {"segments": [{"start", "end", "text"}], "words": [{"start",
    "end", "word"}]} or None when the model is unavailable, the file has no
    audio, or the file cannot be processed.
    """
    if not os.path.exists(path):
        log.info("whisper: file missing %s", path)
        return None
    if not has_audio(path):
        log.info("whisper: no audio stream in %s — skipping", path)
        return None
    if not available():
        return None
    try:
        return _run(path)
    except Exception as exc:  # noqa: BLE001 - per-call resilience
        # CTranslate2 only touches the CUDA libs on first inference, so a
        # broken GPU stack surfaces here rather than at load. Downgrade once
        # and retry; every later call goes straight to CPU.
        if _device == "cuda" and _is_cuda_error(exc):
            if _fallback_to_cpu(exc):
                try:
                    return _run(path)
                except Exception as retry_exc:  # noqa: BLE001
                    log.warning("whisper transcribe failed for %s: %s",
                                path, retry_exc)
                    return None
            return None
        log.warning("whisper transcribe failed for %s: %s", path, exc)
        return None


def unload() -> None:
    """Release the model (frees VRAM between pipeline phases).

    Also clears the ``_LOAD_TRIED`` latch so the next ``transcribe`` can
    reload. ``_CUDA_BROKEN`` deliberately survives: a box that already proved
    it cannot run CTranslate2 on CUDA reloads straight onto CPU instead of
    re-paying the failed-GPU cost on every unload/reload cycle.
    """
    global _model, _LOAD_TRIED
    _model = None
    _LOAD_TRIED = False
    clear_gpu_cache()
    set_model_status("whisper", "not_loaded")
