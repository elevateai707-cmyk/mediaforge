"""Display-referred colour helpers (numpy). Values are encoded RGB in [0,1].

Footage and reference stills are both treated as sRGB/Rec.709-ish display
encoding for *statistics*; the LUTs we emit are encoded-in/encoded-out, which
is what Resolve's Rec.709 timeline and ffmpeg's ``lut3d`` expect.
"""
from __future__ import annotations
import numpy as np

GAMMA = 2.4                                    # Rec.709/BT.1886-style display gamma
LUMA = np.array([0.2126, 0.7152, 0.0722])
_M_RGB2XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                       [0.2126729, 0.7151522, 0.0721750],
                       [0.0193339, 0.1191920, 0.9503041]])
_M_XYZ2RGB = np.linalg.inv(_M_RGB2XYZ)
_WHITE = np.array([0.95047, 1.0, 1.08883])


def to_linear(enc):
    return np.power(np.clip(enc, 0.0, 1.0), GAMMA)


def to_encoded(lin):
    return np.power(np.clip(lin, 0.0, None), 1.0 / GAMMA)


def luma(enc):
    """Encoded-domain luma (Rec.709 weights)."""
    return np.asarray(enc)[..., :3] @ LUMA


def _srgb_to_lin(c):
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _lin_to_srgb(c):
    c = np.clip(c, 0.0, None)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def rgb_to_lab(rgb):
    lin = _srgb_to_lin(np.asarray(rgb, dtype=np.float64))
    xyz = lin @ _M_RGB2XYZ.T / _WHITE
    f = np.where(xyz > 216 / 24389, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def lab_to_rgb_raw(lab):
    """Lab -> encoded RGB WITHOUT clipping (may leave [0,1] when out of gamut)."""
    lab = np.asarray(lab, dtype=np.float64)
    fy = (lab[..., 0] + 16) / 116
    fx = fy + lab[..., 1] / 500
    fz = fy - lab[..., 2] / 200
    f = np.stack([fx, fy, fz], axis=-1)
    xyz = np.where(f ** 3 > 216 / 24389, f ** 3, (116 * f - 16) * 27 / 24389) * _WHITE
    lin = xyz @ _M_XYZ2RGB.T
    c = np.where(lin <= 0.0031308, lin * 12.92, 1.055 * np.power(np.clip(lin, 1e-12, None), 1 / 2.4) - 0.055)
    return c


def lab_to_rgb(lab, fit_gamut=True):
    """Lab -> encoded RGB in [0,1]; out-of-gamut colours lose chroma, not hue."""
    lab = np.asarray(lab, dtype=np.float64)
    rgb = lab_to_rgb_raw(lab)
    if fit_gamut:
        bad = np.any((rgb < -0.002) | (rgb > 1.002), axis=-1)
        if np.any(bad):
            sub = lab[bad]
            lo = np.zeros(len(sub))
            hi = np.ones(len(sub))
            for _ in range(14):
                mid = (lo + hi) / 2
                t = sub.copy()
                t[:, 1:] *= mid[:, None]
                ok = np.all((lab_to_rgb_raw(t) >= -0.002) & (lab_to_rgb_raw(t) <= 1.002), axis=-1)
                lo = np.where(ok, mid, lo)
                hi = np.where(ok, hi, mid)
            sub[:, 1:] *= lo[:, None]
            rgb = rgb.copy()
            rgb[bad] = lab_to_rgb_raw(sub)
    return np.clip(rgb, 0.0, 1.0)


def chroma(enc):
    """Max-min chroma proxy in the encoded domain."""
    enc = np.asarray(enc)
    return enc.max(axis=-1) - enc.min(axis=-1)
