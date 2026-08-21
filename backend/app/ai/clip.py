"""CLIP embedding module (open_clip ViT-B-32).

Lazy singletons: the model, preprocess pipeline, tokenizer and device are
created on first use and cached. Every public function is defensive — any
import/load/inference failure marks the model unavailable in the gpu.py
registry and returns None, so the pipeline never crashes when CLIP cannot
load (e.g. missing weights, no network for the first download).

Embeddings are L2-normalized float32 lists (cosine = dot product).
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Union

import numpy as np

from .. import config
from ..images import open_rgb
from .gpu import device as gpu_device, set_model_status, clear_gpu_cache

log = logging.getLogger("mediaforge.clip")

# Lazy singletons -------------------------------------------------------------
_model: Any = None
_preprocess: Any = None
_tokenizer: Any = None
_device: str = "cpu"
_LOAD_TRIED = False


def _load() -> bool:
    """Load the CLIP model once. Returns True when ready."""
    global _model, _preprocess, _tokenizer, _device, _LOAD_TRIED
    if _model is not None:
        return True
    if _LOAD_TRIED:
        return False
    _LOAD_TRIED = True
    try:
        import open_clip  # type: ignore
        _device = gpu_device()
        model, _, preprocess = open_clip.create_model_and_transforms(
            config.CLIP_MODEL,
            pretrained=config.CLIP_PRETRAINED,
            device=_device,
            jit=False,
        )
        model.eval()
        _model = model
        _preprocess = preprocess
        _tokenizer = open_clip.get_tokenizer(config.CLIP_MODEL)
        set_model_status("clip", "loaded")
        log.info("clip %s/%s loaded on %s", config.CLIP_MODEL,
                 config.CLIP_PRETRAINED, _device)
        return True
    except Exception as exc:  # noqa: BLE001 - registry + degrade, never crash
        _model = None
        set_model_status("clip", "unavailable", f"{type(exc).__name__}: {exc}")
        log.warning("clip model unavailable: %s", exc)
        return False


def available() -> bool:
    """True when the CLIP model is loaded and usable."""
    return _model is not None or _load()


def embed_image(image: Union[str, Any]) -> Optional[list]:
    """Embed an image path or a PIL image -> normalized float32 list.

    Returns None when the model is unavailable or the image cannot be read.
    """
    if not available():
        return None
    try:
        if isinstance(image, str):
            img = open_rgb(image)
        else:
            img = image
            if img.mode != "RGB":
                img = img.convert("RGB")
        import torch  # noqa: PLC0415 - deferred heavy import
        with torch.no_grad():
            batch = _preprocess(img).unsqueeze(0).to(_device)
            vec = _model.encode_image(batch)
            vec = vec / vec.norm(dim=-1, keepdim=True)
        return np.asarray(vec.squeeze(0).float().cpu().numpy(),
                          dtype=np.float32).tolist()
    except Exception as exc:  # noqa: BLE001 - per-call resilience
        log.warning("clip embed_image failed: %s", exc)
        return None


def embed_text(query: str) -> Optional[list]:
    """Embed a text query -> normalized float32 list; None when unavailable."""
    if not available():
        return None
    try:
        import torch  # noqa: PLC0415
        with torch.no_grad():
            tokens = _tokenizer([query]).to(_device)
            vec = _model.encode_text(tokens)
            vec = vec / vec.norm(dim=-1, keepdim=True)
        return np.asarray(vec.squeeze(0).float().cpu().numpy(),
                          dtype=np.float32).tolist()
    except Exception as exc:  # noqa: BLE001
        log.warning("clip embed_text failed: %s", exc)
        return None


def unload() -> None:
    """Release model weights + CUDA cache (call before other VRAM phases).

    Clears the ``_LOAD_TRIED`` latch as well: unloading means "free VRAM
    now", not "never load again". Leaving it set made a single VRAM-pressure
    unload disable CLIP embeddings *and* text search for the rest of the
    process, because ``_load`` would short-circuit to False forever.
    """
    global _model, _preprocess, _tokenizer, _LOAD_TRIED
    _model = None
    _preprocess = None
    _tokenizer = None
    _LOAD_TRIED = False
    clear_gpu_cache()
    set_model_status("clip", "not_loaded")
