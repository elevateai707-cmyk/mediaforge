"""Automatic per-clip colour balance -> parametric correction -> .cube.

Per clip: white balance (weighted gray-world blended with a white-patch
estimate, damped and clamped), exposure normalisation towards a scene target,
gentle black/white-point + S-curve contrast.  Clips in the same *scene* share
part of their target (exposure and WB gains are pulled towards the scene median)
so shots cut together instead of each being normalised in isolation.

Gray-world is a prior, not a truth: strongly coloured scenes (turquoise sea,
red night) are detected via the neutral-pixel fraction and corrected less.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict, field
import numpy as np

from . import cube
from .colorspace import GAMMA, LUMA, chroma, luma, to_encoded, to_linear

ABS_TARGET_MEDIAN = 0.45     # encoded median luma we nudge towards
WB_STRENGTH = 0.65
GAIN_MIN, GAIN_MAX = 0.75, 1.33
MAX_STOPS = 0.8


@dataclass
class Correction:
    gains: tuple = (1.0, 1.0, 1.0)      # linear-light RGB multipliers (luma-normalised)
    stops: float = 0.0                  # exposure, linear light
    black: float = 0.0                  # encoded black point
    white: float = 1.0                  # encoded white point
    contrast: float = 0.1               # S-curve blend 0..1
    stats: dict = field(default_factory=dict)

    def to_dict(self):
        d = asdict(self)
        d["gains"] = [round(float(x), 5) for x in self.gains]
        return d

    @staticmethod
    def from_dict(d):
        return Correction(tuple(d["gains"]), d["stops"], d["black"], d["white"], d["contrast"], d.get("stats", {}))


def apply_correction(c: Correction, enc: np.ndarray) -> np.ndarray:
    """Encoded RGB (...,3) -> corrected encoded RGB. Exactly what the .cube bakes."""
    lin = to_linear(enc) * (np.asarray(c.gains) * 2.0 ** c.stops)
    x = to_encoded(lin)
    span = max(c.white - c.black, 1e-3)
    x = np.clip((x - c.black) / span, 0.0, 1.0)
    s = x * x * (3.0 - 2.0 * x)
    return np.clip(x + c.contrast * (s - x), 0.0, 1.0)


def correction_lut(c: Correction, size: int = cube.DEFAULT_SIZE) -> np.ndarray:
    return cube.from_points(apply_correction(c, cube.grid_points(size)), size)


# ---- estimation -------------------------------------------------------------
def _norm_gains(g):
    g = np.asarray(g, dtype=np.float64)
    return g / float(g @ LUMA)


def measure(px: np.ndarray) -> dict:
    """Clip statistics from pooled encoded pixels (n,3)."""
    px = np.asarray(px, dtype=np.float64)
    y = luma(px)
    keep = (y > 0.04) & (y < 0.97)
    if keep.sum() < 50:
        keep = np.ones(len(px), bool)
    p = px[keep]
    lin = to_linear(p)
    ch = chroma(p)
    w = np.exp(-(ch / 0.25) ** 2)                      # neutral-ish pixels dominate
    wmean = (lin * w[:, None]).sum(0) / max(w.sum(), 1e-9)
    top = y >= np.quantile(y, 0.97)
    wp = to_linear(px[top]).mean(0) if top.sum() >= 5 else wmean
    wp_clipped = bool(np.median(px[top].max(axis=1)) > 0.97) if top.sum() >= 5 else True
    q = np.quantile(y, [0.005, 0.01, 0.05, 0.5, 0.95, 0.99, 0.995])
    return {
        "gray_mean_lin": wmean, "white_patch_lin": wp, "wp_clipped": wp_clipped,
        "neutral_frac": float((ch < 0.12).mean()), "p005": float(q[0]), "p01": float(q[1]), "p05": float(q[2]),
        "median": float(q[3]), "p95": float(q[4]), "p99": float(q[5]), "p995": float(q[6]),
        "mean_chroma": float(ch.mean()),
        "cast": float(abs(wmean[0] - wmean[1]) + abs(wmean[2] - wmean[1])) / max(float(wmean.mean()), 1e-6),
    }


def estimate(px: np.ndarray, scene_median: float | None = None,
             scene_gains: np.ndarray | None = None, scene_weight: float = 0.5) -> Correction:
    """Correction for one clip. ``scene_*`` pull this clip toward its scene."""
    m = measure(px)
    gw = _norm_gains(m["gray_mean_lin"].mean() / np.maximum(m["gray_mean_lin"], 1e-6))
    if not m["wp_clipped"]:
        gp = _norm_gains(m["white_patch_lin"].mean() / np.maximum(m["white_patch_lin"], 1e-6))
        raw = np.exp(0.75 * np.log(gw) + 0.25 * np.log(gp))
    else:
        raw = gw
    strength = WB_STRENGTH * (0.5 if m["neutral_frac"] < 0.15 else 1.0)    # colourful scene: trust gray-world less
    gains = np.exp(strength * np.log(np.clip(_norm_gains(raw), GAIN_MIN, GAIN_MAX)))
    if scene_gains is not None:
        gains = np.exp((1 - scene_weight) * np.log(gains) + scene_weight * np.log(scene_gains))
    gains = _norm_gains(np.clip(gains, GAIN_MIN, GAIN_MAX))

    # exposure on the post-WB median
    corrected = to_encoded(to_linear(px) * gains)
    med = float(np.median(luma(corrected)))
    ref = med if scene_median is None else scene_median
    target = 0.35 * ABS_TARGET_MEDIAN + 0.65 * ref
    stops = 0.8 * float(np.log2(max(to_linear(np.array(target)), 1e-5) / max(to_linear(np.array(med)), 1e-5)))
    stops = float(np.clip(stops, -MAX_STOPS, MAX_STOPS))
    p99 = float(np.quantile(luma(corrected), 0.99))
    if stops > 0:                                                          # don't push highlights over the edge
        headroom = float(np.log2(max(to_linear(np.array(0.985)), 1e-5) / max(to_linear(np.array(p99)), 1e-5)))
        stops = float(np.clip(min(stops, max(headroom, 0.0)), 0.0, MAX_STOPS))

    lin = to_linear(px) * gains * 2.0 ** stops
    y2 = luma(to_encoded(lin))
    q = np.quantile(y2, [0.01, 0.05, 0.95, 0.995])
    black = float(np.clip(q[0] - 0.02, 0.0, 0.08) * 0.8)
    white = float(np.clip(q[3], 0.85, 1.0))
    span = q[2] - q[1]
    contrast = 0.2 if span < 0.45 else (0.08 if span > 0.8 else 0.12)
    c = Correction(tuple(float(x) for x in gains), stops, black, white, contrast)
    out = apply_correction(c, px.astype(np.float64))
    m2 = measure(out)
    c.stats = {"before": _brief(m), "after": _brief(m2)}
    return c


def _brief(m):
    return {k: round(float(m[k]), 4) for k in ("median", "p05", "p95", "cast", "neutral_frac", "mean_chroma")}


def estimate_scene(pixels_by_clip: dict, scene_of: dict, scene_weight: float = 0.5) -> dict:
    """Corrections for many clips, matching clips within the same scene.

    pixels_by_clip: {clip_id: (n,3)}, scene_of: {clip_id: scene_key}.
    """
    solo = {k: estimate(px) for k, px in pixels_by_clip.items()}
    by_scene: dict = {}
    for k, s in scene_of.items():
        by_scene.setdefault(s, []).append(k)
    out = {}
    for s, members in by_scene.items():
        if len(members) < 2:
            out[members[0]] = solo[members[0]]
            continue
        meds = [float(np.median(luma(apply_correction(Correction(solo[k].gains, 0, 0, 1, 0), pixels_by_clip[k].astype(np.float64))))) for k in members]
        ref_med = float(np.median(meds))
        ref_gains = _norm_gains(np.exp(np.median([np.log(solo[k].gains) for k in members], axis=0)))
        for k in members:
            out[k] = estimate(pixels_by_clip[k], scene_median=ref_med, scene_gains=ref_gains, scene_weight=scene_weight)
    return out
