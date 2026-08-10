"""GPU detection + shared model-status registry.

Every heavy model registers its state here so /api/health and the WebSocket
can surface exactly which models loaded, which failed, and why. A model that
fails to load sets status='unavailable' with an error; the pipeline skips the
corresponding phase and continues on CPU/fallbacks — it never crashes.
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("mediaforge.gpu")

MODEL_STATUS: dict[str, dict] = {}
# name -> {"status": "loaded"|"unavailable"|"not_loaded", "error": str|None}

GPU_WARNING: str = ""


def _torch():
    import torch
    return torch


def cuda_available() -> bool:
    try:
        return bool(_torch().cuda.is_available())
    except Exception:
        return False


def device() -> str:
    return "cuda" if cuda_available() else "cpu"


def gpu_name() -> str:
    if not cuda_available():
        return ""
    try:
        return _torch().cuda.get_device_name(0)
    except Exception:
        return ""


def vram_total_mb() -> int:
    if not cuda_available():
        return 0
    try:
        return int(_torch().cuda.get_device_properties(0).total_memory // (1024 * 1024))
    except Exception:
        return 0


def vram_free_mb() -> int:
    if not cuda_available():
        return 0
    try:
        free, _total = _torch().cuda.mem_get_info()
        return int(free // (1024 * 1024))
    except Exception:
        return 0


def vram_pressure(threshold_mb: int = 1024) -> bool:
    """True when free VRAM is below threshold (models should be unloaded)."""
    if not cuda_available():
        return False
    return vram_free_mb() < threshold_mb


def clear_gpu_cache() -> None:
    if cuda_available():
        try:
            _torch().cuda.empty_cache()
        except Exception:
            pass


def set_model_status(name: str, status: str, error: Optional[str] = None) -> None:
    global GPU_WARNING
    MODEL_STATUS[name] = {"status": status, "error": error}
    if status == "unavailable":
        GPU_WARNING = f"model '{name}' unavailable: {error or 'unknown error'}"
        log.warning("model unavailable: %s -> %s", name, error)
    log.info("model status: %s = %s%s", name, status, f" ({error})" if error else "")


def model_status(name: str) -> dict:
    return MODEL_STATUS.get(name, {"status": "not_loaded", "error": None})


def warnings_list() -> list[str]:
    out = []
    if device() == "cpu":
        out.append("CUDA not available — all models running on CPU (slower, works).")
    if GPU_WARNING:
        out.append(GPU_WARNING)
    for name, st in MODEL_STATUS.items():
        if st["status"] == "unavailable" and st.get("error"):
            out.append(f"{name}: {st['error']}")
    return out


def gpu_payload() -> dict:
    """Contract gpu event payload."""
    return {
        "type": "gpu",
        "mode": device(),
        "vram_mb": vram_total_mb(),
        "warning": warnings_list()[0] if warnings_list() else "",
    }
