"""Aesthetic scoring (0-10).

Primary path: ONNX MLP at ``models/aesthetic.onnx`` expecting a 384-dim
feature vector (LAION-style aesthetic predictor over CLIP embeddings).

Fallback (model file absent — the normal case today): a deterministic,
seeded scorer over the CLIP embedding — same image always yields the same
score, output is always a 0-10 float. This is placeholder-quality by design;
deploy ``aesthetic.onnx`` to the models dir to switch to the real model.
"""

from __future__ import annotations

import logging
import os
import struct
from typing import Any

import numpy as np

from app import config
from app.ai import clip, gpu

log = logging.getLogger(__name__)

_session = None
_session_error: str | None = None

AESTHETIC_INPUT_DIM = 384


# --------------------------------------------------------------------------
# ONNX path
# --------------------------------------------------------------------------
def _ensure_onnx() -> Any | None:
    global _session, _session_error
    if _session is not None:
        return _session
    if _session_error is not None:
        return None
    model_path = config.MODELS_DIR / "aesthetic.onnx"
    if not model_path.is_file():
        _session_error = "aesthetic.onnx not present"
        return None
    try:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        _session = ort.InferenceSession(str(model_path), sess_options=opts)
        gpu.set_model_status("aesthetic", "loaded", {"onnx": True})
        return _session
    except Exception as exc:  # pragma: no cover - depends on environment
        _session_error = str(exc)
        log.warning("aesthetic ONNX load failed: %s", exc)
        gpu.set_model_status("aesthetic", "unavailable", {"error": str(exc)})
        return None


def _onnx_score(sess: Any, features_512: np.ndarray) -> float | None:
    try:
        inp = sess.get_inputs()[0]
        name, shape = inp.name, inp.shape
        dim = int(shape[1]) if shape and len(shape) > 1 and shape[1] else AESTHETIC_INPUT_DIM
        if dim == len(features_512):
            x = features_512.astype(np.float32).reshape(1, -1)
        elif dim == AESTHETIC_INPUT_DIM:
            rng = np.random.default_rng(42)
            proj = rng.normal(0.0, 1.0 / 512.0, size=(len(features_512), dim)).astype(np.float32)
            x = (features_512 @ proj).astype(np.float32).reshape(1, -1)
        else:
            return None
        y = sess.run(None, {name: x})[0]
        raw = float(np.asarray(y).reshape(-1)[0])
        return float(min(max(raw, 0.0), 10.0))
    except Exception as exc:  # pragma: no cover - depends on environment
        log.warning("aesthetic ONNX inference failed: %s", exc)
        return None


# --------------------------------------------------------------------------
# deterministic fallback over CLIP features
# --------------------------------------------------------------------------
def _fallback_score(features_512: np.ndarray, seed: int) -> float:
    """Deterministic placeholder score in [0, 10] derived from CLIP features.

    Combines a smooth feature-statistic term with a seeded hash term so the
    score is stable per image and spread across the range. Documented as
    placeholder-quality: replace by deploying ``aesthetic.onnx``.
    """
    f = np.asarray(features_512, dtype=np.float32).reshape(-1)
    mean = float(f.mean()) if f.size else 0.0
    std = float(f.std()) if f.size else 0.0
    span = float(f.max() - f.min()) if f.size else 0.0
    stat = 10.0 / (1.0 + np.exp(-(2.5 * mean + 1.5 * std + 0.6 * span - 0.4)))
    rng = np.random.default_rng(seed)
    hash_term = float(rng.uniform(0.0, 1.0))
    score = float(0.72 * stat + 0.28 * hash_term * 10.0)
    return round(min(max(score, 0.0), 10.0), 2)


def _image_stats_score(image: Any) -> float:
    """Ultra-fallback when even CLIP is unavailable: brightness-based score."""
    try:
        import numpy as _np

        arr = _np.asarray(image.convert("L")).astype(_np.float32)
        if arr.size == 0:
            return 5.0
        return round(min(max(float(arr.mean()) / 25.5, 0.0), 10.0), 2)
    except Exception:
        return 5.0


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------
def score_image(image_path: str) -> float:
    """Aesthetic score (0-10) for an image path. Never raises."""
    if not image_path or not os.path.isfile(image_path):
        return 5.0
    try:
        features = clip.embed_image(image_path)
        if features is None:
            try:
                from app.images import open_rgb

                return _image_stats_score(open_rgb(image_path))
            except Exception:
                return 5.0
        f = np.asarray(features, dtype=np.float32).reshape(-1)
        seed = int(np.abs(f).sum() * 1e6) % (2**31)
        sess = _ensure_onnx()
        if sess is not None:
            onnx_score = _onnx_score(sess, f)
            if onnx_score is not None:
                return round(onnx_score, 2)
        return _fallback_score(f, seed)
    except Exception as exc:  # pragma: no cover
        log.warning("aesthetic scoring failed: %s", exc)
        return 5.0


def score_features(features_512: list[float] | np.ndarray) -> float:
    """Score precomputed CLIP features (used by the pipeline to avoid a
    second embed pass)."""
    f = np.asarray(features_512, dtype=np.float32).reshape(-1)
    seed = int(np.abs(f).sum() * 1e6) % (2**31)
    sess = _ensure_onnx()
    if sess is not None:
        onnx_score = _onnx_score(sess, f)
        if onnx_score is not None:
            return round(onnx_score, 2)
    return _fallback_score(f, seed)


def score_cluster_centroid(centroid_blob: bytes | None) -> float:
    """Score a packed face-cluster centroid blob (0-10, deterministic)."""
    if not centroid_blob:
        return 5.0
    try:
        n = len(centroid_blob) // 4
        f = np.frombuffer(centroid_blob, dtype=np.float32, count=n)
        return _fallback_score(f, 7)
    except Exception:
        return 5.0
