"""Person cut-out for green-screen plans: CPU video matting + chroma key + compositing helpers.

Model: Robust Video Matting (RVM, MobileNetV3, 15 MB ONNX, run with onnxruntime on the CPU, never the
GPU). It is a *video* matting network: recurrent state carries information between frames, so the
mask is temporally stable and hair edges come out soft rather than blocky. (RVM code and weights are
GPL-3.0, https://github.com/PeterL1n/RobustVideoMatting.) The file lives in MODELS_DIR as
``rvm_mobilenetv3_fp32.onnx`` and is downloaded once if missing (MF_ALLOW_MODEL_DOWNLOAD=0 disables).

If the background really is green the segmenter is skipped and a classic chroma key is used.
Both paths feed the same finishing steps: edge refinement, light temporal smoothing, colour
decontamination at the edge, a soft drop shadow and a faint rim light.
"""
from __future__ import annotations

import logging
import os
import urllib.request
from pathlib import Path
from typing import Optional, Protocol

import cv2
import numpy as np

from .. import config

log = logging.getLogger("mediaforge.segment")

MODEL_NAME = "rvm_mobilenetv3_fp32.onnx"
MODEL_URL = ("https://github.com/PeterL1n/RobustVideoMatting/releases/download/v1.0.0/"
             "rvm_mobilenetv3_fp32.onnx")
MODEL_INPUT_W = 540          # frames are matted at this width (height keeps the aspect)
DOWNSAMPLE = 0.45            # RVM internal downsample ratio


class Segmenter(Protocol):
    """Anything that turns an RGB uint8 frame (h, w, 3) into an alpha float32 (h, w) in 0..1."""

    def reset(self) -> None: ...
    def alpha(self, rgb: np.ndarray) -> np.ndarray: ...


class SegmenterUnavailable(RuntimeError):
    pass


def model_path() -> Path:
    return config.MODELS_DIR / MODEL_NAME


def ensure_model() -> Path:
    p = model_path()
    if p.is_file() and p.stat().st_size > 1_000_000:
        return p
    if os.environ.get("MF_ALLOW_MODEL_DOWNLOAD", "1") == "0":
        raise SegmenterUnavailable(f"person-matting model missing: {p}")
    p.parent.mkdir(parents=True, exist_ok=True)
    log.info("downloading the RVM matting model (15 MB) to %s", p)
    tmp = p.with_suffix(".part")
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=120) as r, open(tmp, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        os.replace(tmp, p)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise SegmenterUnavailable(f"could not download the matting model: {exc}") from None
    return p


class RVMSegmenter:
    """Recurrent video matting on the CPU. Call ``reset()`` at the start of each video."""

    def __init__(self, threads: int = 4):
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        self.session = ort.InferenceSession(str(ensure_model()), so, providers=["CPUExecutionProvider"])
        self.reset()

    def reset(self) -> None:
        self.state = [np.zeros((1, 1, 1, 1), np.float32) for _ in range(4)]

    def alpha(self, rgb: np.ndarray) -> np.ndarray:
        h, w = rgb.shape[:2]
        mw = MODEL_INPUT_W
        mh = max(2, int(round(h * mw / w / 2)) * 2)
        small = cv2.resize(rgb, (mw, mh), interpolation=cv2.INTER_AREA)
        src = (small.astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
        out = self.session.run(None, {"src": src, "r1i": self.state[0], "r2i": self.state[1],
                                      "r3i": self.state[2], "r4i": self.state[3],
                                      "downsample_ratio": np.array([DOWNSAMPLE], np.float32)})
        self.state = list(out[2:])
        pha = out[1][0, 0]
        return cv2.resize(pha, (w, h), interpolation=cv2.INTER_LINEAR)


# ---------------------------------------------------------------------------
# chroma key
# ---------------------------------------------------------------------------

def green_fraction(rgb: np.ndarray, border: float = 0.08) -> float:
    """Share of the frame's outer border that is saturated green (screen detection)."""
    h, w = rgb.shape[:2]
    bh, bw = max(2, int(h * border)), max(2, int(w * border))
    mask = np.zeros((h, w), bool)
    mask[:bh], mask[-bh:], mask[:, :bw], mask[:, -bw:] = True, True, True, True
    return float(_greenish(rgb)[mask].mean())


def _greenish(rgb: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    return (hue >= 35) & (hue <= 85) & (sat >= 70) & (val >= 50)


def detect_green_screen(frames: list[np.ndarray], threshold: float = 0.55) -> bool:
    return bool(frames) and float(np.median([green_fraction(f) for f in frames])) >= threshold


def chroma_key(rgb: np.ndarray, key_lo: float = 0.10, key_hi: float = 0.30) -> tuple[np.ndarray, np.ndarray]:
    """(alpha float32, despilled rgb uint8). Alpha falls as green dominates the other channels."""
    f = rgb.astype(np.float32) / 255.0
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    dominance = g - np.maximum(r, b)                       # >0 on the screen, ~0 on skin / clothes
    alpha = 1.0 - np.clip((dominance - key_lo) / (key_hi - key_lo), 0.0, 1.0)
    # despill: clamp green to the other channels so a green fringe disappears
    g2 = np.minimum(g, np.maximum(r, b) + 0.02)
    out = np.stack([r, g2, b], axis=-1)
    return cv2.GaussianBlur(alpha.astype(np.float32), (0, 0), 0.8), (np.clip(out, 0, 1) * 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# finishing
# ---------------------------------------------------------------------------

def refine_alpha(alpha: np.ndarray, previous: Optional[np.ndarray] = None, smooth: float = 0.28) -> np.ndarray:
    """Tighten the matte (smoothstep removes halo / noise), feather 1 px and smooth over time."""
    a = np.clip(alpha, 0.0, 1.0)
    lo, hi = 0.06, 0.94
    t = np.clip((a - lo) / (hi - lo), 0.0, 1.0)
    a = t * t * (3 - 2 * t)
    a = cv2.GaussianBlur(a, (0, 0), 0.9)
    if previous is not None:
        # only blend where the matte barely moved (edges/noise); real motion passes through untouched
        still = np.abs(a - previous) < 0.35
        a = np.where(still, (1 - smooth) * a + smooth * previous, a)
    return a.astype(np.float32)


def decontaminate(rgb: np.ndarray, alpha: np.ndarray, radius: float = 4.0) -> np.ndarray:
    """Pull edge colours towards the subject's interior colour so the old background does not
    bleed through (foreground estimate = blurred(rgb*alpha) / blurred(alpha))."""
    f = rgb.astype(np.float32)
    num = cv2.GaussianBlur(f * alpha[..., None], (0, 0), radius)
    den = cv2.GaussianBlur(alpha, (0, 0), radius)[..., None] + 1e-4
    est = num / den
    edge = np.clip(4.0 * alpha * (1.0 - alpha), 0.0, 1.0)[..., None]       # 1 at alpha 0.5, 0 inside/outside
    return np.clip(f * (1 - edge) + est * edge, 0, 255).astype(np.uint8)


def shadow_and_rim(alpha: np.ndarray, rgb: np.ndarray, shadow_opacity: float = 0.38,
                   rim_opacity: float = 0.22, scale: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """Bake a soft drop shadow (under) and a thin cool rim (over the silhouette edge) into one layer.

    Returns (rgb uint8, alpha float32) of the combined subject + shadow layer.
    """
    h, w = alpha.shape
    blur = max(3.0, 22.0 * scale)
    dx, dy = int(10 * scale), int(16 * scale)
    shifted = np.zeros_like(alpha)
    shifted[dy:, dx:] = alpha[:h - dy, :w - dx]
    shadow = cv2.GaussianBlur(shifted, (0, 0), blur) * shadow_opacity
    inner = cv2.erode(alpha, np.ones((3, 3), np.uint8), iterations=max(1, int(2 * scale)))
    rim = np.clip(alpha - inner, 0.0, 1.0)
    rim = cv2.GaussianBlur(rim, (0, 0), 1.2) * rim_opacity
    f = rgb.astype(np.float32)
    cyan = np.array([140, 215, 255], np.float32)
    f = f * (1 - rim[..., None]) + cyan * rim[..., None]
    out_a = alpha + shadow * (1.0 - alpha)
    out_rgb = f * alpha[..., None] / np.maximum(out_a, 1e-4)[..., None]       # shadow colour is black
    return np.clip(out_rgb, 0, 255).astype(np.uint8), np.clip(out_a, 0, 1).astype(np.float32)


def compose_over(background: np.ndarray, layer_rgb: np.ndarray, layer_alpha: np.ndarray,
                 x: int = 0, y: int = 0) -> np.ndarray:
    """Software composite (used by tests and stills); ffmpeg does this in the real render."""
    out = background.astype(np.float32).copy()
    h, w = layer_alpha.shape
    region = out[y:y + h, x:x + w]
    a = layer_alpha[:region.shape[0], :region.shape[1], None]
    region[:] = region * (1 - a) + layer_rgb[:region.shape[0], :region.shape[1]].astype(np.float32) * a
    return np.clip(out, 0, 255).astype(np.uint8)
