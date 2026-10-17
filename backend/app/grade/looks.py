"""Reference-image looks: colour transfer -> 33^3 creative .cube.

Methods
  reinhard  Lab mean/std transfer (Reinhard 2001), clamped std ratios.
  sot       Sliced optimal transport (iterative distribution transfer): captures
            split-toning (teal shadows / warm highlights) that mean/std cannot.
Both fit a *transform* from footage statistics to reference statistics and then
evaluate it on the LUT lattice, so one LUT serves every clip.  ``strength``
blends with identity; ``skin_protect`` keeps skin-hue colours from taking the
look's chroma shift (luminance still follows).
"""
from __future__ import annotations
import numpy as np

from . import cube
from .colorspace import lab_to_rgb, rgb_to_lab

STD_RATIO_L = (0.75, 1.3)
STD_RATIO_AB = (0.6, 1.5)
L_MEAN_SHIFT_KEEP = 0.5       # look follows the reference's exposure only half-way
AB_SHIFT_CAP = 28.0


def clean_reference(px: np.ndarray) -> np.ndarray:
    """Drop letterbox/UI black and blown white so they do not skew the stats."""
    lab = rgb_to_lab(px)
    keep = (lab[:, 0] > 3.0) & (lab[:, 0] < 99.0)
    return px[keep] if keep.mean() > 0.2 and keep.sum() > 200 else px


def lab_stats(px: np.ndarray) -> dict:
    lab = rgb_to_lab(px)
    return {"mean": lab.mean(0), "std": lab.std(0) + 1e-6}


def skin_weight(lab: np.ndarray) -> np.ndarray:
    """Soft mask (0..1) of skin-like colours in Lab (hue ~20-70deg, moderate chroma)."""
    a, b = lab[..., 1], lab[..., 2]
    h = np.degrees(np.arctan2(b, a)) % 360
    c = np.hypot(a, b)
    hue = np.clip(1 - np.abs(h - 45) / 28.0, 0, 1)
    chr_ = np.clip((c - 6) / 6, 0, 1) * np.clip((55 - c) / 15, 0, 1)
    lum = np.clip((lab[..., 0] - 20) / 15, 0, 1) * np.clip((92 - lab[..., 0]) / 10, 0, 1)
    return hue * chr_ * lum


# ---- transforms (operate on Lab arrays (...,3)) ------------------------------
def reinhard_transform(src_px: np.ndarray, ref_px: np.ndarray):
    s, r = lab_stats(clean_reference(src_px)), lab_stats(clean_reference(ref_px))
    ratio = r["std"] / s["std"]
    ratio[0] = np.clip(ratio[0], *STD_RATIO_L)
    ratio[1:] = np.clip(ratio[1:], *STD_RATIO_AB)
    shift = r["mean"] - s["mean"]
    shift[0] *= L_MEAN_SHIFT_KEEP
    shift[1:] = np.clip(shift[1:], -AB_SHIFT_CAP, AB_SHIFT_CAP)
    target_mean = s["mean"] + shift

    def f(lab):
        return (lab - s["mean"]) * ratio + target_mean
    f.info = {"method": "reinhard", "ratio": ratio.round(3).tolist(), "shift": shift.round(2).tolist()}
    return f


def sot_transform(src_px: np.ndarray, ref_px: np.ndarray, iters: int = 20, nq: int = 96, seed: int = 7):
    """Sliced OT: random rotations, per-axis quantile maps; replayable on any point."""
    rng = np.random.default_rng(seed)
    src = rgb_to_lab(clean_reference(src_px))
    ref = rgb_to_lab(clean_reference(ref_px))
    qs = np.linspace(0.0, 1.0, nq)
    steps = []
    cur = src.copy()
    for _ in range(iters):
        q, _r = np.linalg.qr(rng.normal(size=(3, 3)))
        a, b = cur @ q, ref @ q
        sq = np.quantile(a, qs, axis=0)
        rq = np.quantile(b, qs, axis=0)
        sq = np.maximum.accumulate(sq + np.arange(nq)[:, None] * 1e-9, axis=0)    # strictly increasing for interp
        for ax in range(3):
            a[:, ax] = np.interp(a[:, ax], sq[:, ax], rq[:, ax])
        cur = a @ q.T
        steps.append((q, sq, rq))

    def f(lab):
        shape = lab.shape
        x = lab.reshape(-1, 3)
        for q, sq, rq in steps:
            y = x @ q
            for ax in range(3):
                # linear extrapolation outside the sampled range keeps the map monotone/continuous
                lo, hi = sq[0, ax], sq[-1, ax]
                inside = np.interp(y[:, ax], sq[:, ax], rq[:, ax])
                below = rq[0, ax] + (y[:, ax] - lo)
                above = rq[-1, ax] + (y[:, ax] - hi)
                y[:, ax] = np.where(y[:, ax] < lo, below, np.where(y[:, ax] > hi, above, inside))
            x = y @ q.T
        return x.reshape(shape)
    f.info = {"method": "sot", "iterations": iters}
    return f


def build_look(src_px: np.ndarray, ref_px: np.ndarray, method: str = "reinhard", strength: float = 0.8,
               skin_protect: float = 0.7, size: int = cube.DEFAULT_SIZE):
    """-> (lut (N,N,N,3), info dict). src_px = corrected footage sample, ref_px = reference pixels."""
    if method == "reinhard":
        f = reinhard_transform(src_px, ref_px)
    elif method == "sot":
        f = sot_transform(src_px, ref_px)
    else:
        raise ValueError("method must be reinhard or sot")
    pts = cube.grid_points(size)
    lab = rgb_to_lab(pts)
    tgt = f(lab)
    out = lab + strength * (tgt - lab)
    sw = skin_weight(lab) * skin_protect * strength
    out[:, 1:] = lab[:, 1:] + (1 - sw[:, None]) * (out[:, 1:] - lab[:, 1:])
    # Keep achromatic axis honest: a clean grey ramp may take a tint, but never loses neutrality entirely via gamut fit.
    rgb = lab_to_rgb(out)
    info = dict(f.info, strength=strength, skin_protect=skin_protect, size=size)
    return cube.from_points(rgb, size), info
