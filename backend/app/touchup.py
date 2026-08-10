"""Photo touch-up presets (non-destructive; Phase 5).

Presets:
- auto_levels    : per-channel autocontrast (PIL ImageOps.autocontrast)
- white_balance  : gray-world scaling per RGB channel
- denoise        : cv2.fastNlMeansDenoisingColored when OpenCV is importable,
                   otherwise PIL GaussianBlur
- sharpen        : PIL UnsharpMask
- all            : auto_levels -> white_balance -> denoise -> sharpen

apply_touchup(asset_id, preset) writes
exports/touchup/<asset_id>_<preset>.<ext> (source extension preserved when
web-friendly, else .jpg). preview_touchup returns a side-by-side before|after
JPEG composite for GET /api/touchup/preview.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageFilter, ImageOps

from . import config, models
from .db import SessionLocal

log = logging.getLogger("mediaforge.touchup")

PRESETS = ("auto_levels", "white_balance", "denoise", "sharpen", "all")

_CV2: Any = None
try:
    import cv2  # type: ignore
    _CV2 = cv2
except Exception:  # pragma: no cover - opencv optional
    _CV2 = None


def _open_image(path: str) -> Image.Image:
    """Open an image (HEIC-aware, like ingest/thumbs)."""
    try:
        import pillow_heif  # type: ignore
        pillow_heif.register_heif_opener()
    except Exception:
        pass
    img = Image.open(path)
    img.load()
    if img.mode != "RGB":
        img = img.convert("RGB")
    return img


def _autocontrast(img: Image.Image) -> Image.Image:
    """Per-channel autocontrast (keeps color cast removal in check)."""
    r, g, b = img.split()
    return Image.merge("RGB", (
        ImageOps.autocontrast(r),
        ImageOps.autocontrast(g),
        ImageOps.autocontrast(b),
    ))


def _white_balance(img: Image.Image) -> Image.Image:
    """Gray-world: scale each channel so its mean hits the overall mean."""
    r, g, b = img.split()
    means = [sum(ch.histogram()[i] * i for i in range(256)) / (ch.width * ch.height)
             for ch in (r, g, b)]
    target = sum(means) / 3.0
    out = []
    for ch, mean in zip((r, g, b), means):
        if mean <= 1e-6:
            out.append(ch)
            continue
        scale = min(2.5, max(0.4, target / mean))
        lut = [min(255, int(i * scale)) for i in range(256)]
        out.append(ch.point(lut))
    return Image.merge("RGB", tuple(out))


def _denoise(img: Image.Image) -> Image.Image:
    if _CV2 is not None:
        try:
            import numpy as np
            bgr = _CV2.cvtColor(np.asarray(img), _CV2.COLOR_RGB2BGR)
            denoised = _CV2.fastNlMeansDenoisingColored(bgr, None, 6, 6, 7, 21)
            return Image.fromarray(_CV2.cvtColor(denoised, _CV2.COLOR_BGR2RGB))
        except Exception as exc:  # noqa: BLE001 - fall back to PIL
            log.debug("cv2 denoise failed (%s); using PIL blur", exc)
    return img.filter(ImageFilter.GaussianBlur(radius=1.2))


def _sharpen(img: Image.Image) -> Image.Image:
    return img.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))


def apply_preset(img: Image.Image, preset: str) -> Image.Image:
    """Apply a named preset to an RGB image."""
    preset = (preset or "").strip()
    if preset not in PRESETS:
        raise ValueError(
            f"unknown preset {preset!r}; expected one of {', '.join(PRESETS)}")
    if preset == "auto_levels":
        return _autocontrast(img)
    if preset == "white_balance":
        return _white_balance(img)
    if preset == "denoise":
        return _denoise(img)
    if preset == "sharpen":
        return _sharpen(img)
    # "all"
    out = _autocontrast(img)
    out = _white_balance(out)
    out = _denoise(out)
    return _sharpen(out)


def _progress_callable(job: Any):
    if job is None:
        return lambda _f, _m: None
    if callable(job):
        return job
    p = getattr(job, "progress", None)
    return p if callable(p) else lambda _f, _m: None


def _output_ext(asset: models.Asset) -> str:
    ext = Path(asset.path).suffix.lower()
    return ext if ext in (".jpg", ".jpeg", ".png", ".webp") else ".jpg"


def apply_touchup(asset_id: int, preset: str, job: Any = None) -> str:
    """Apply preset to an asset photo; writes exports/touchup/<id>_<preset>.<ext>.

    Returns the output path. Raises ValueError for bad preset / video assets.
    """
    progress = _progress_callable(job)
    preset = (preset or "").strip()
    if preset not in PRESETS:
        raise ValueError(
            f"unknown preset {preset!r}; expected one of {', '.join(PRESETS)}")

    with SessionLocal() as db:
        asset = db.get(models.Asset, asset_id)
    if asset is None:
        raise ValueError(f"asset {asset_id} not found")
    if not Path(asset.path).exists():
        raise ValueError(f"asset file missing: {asset.path}")
    if asset.kind != "photo":
        raise ValueError(
            f"asset {asset_id} is a video; touch-up currently supports photos")

    progress(0.2, f"loading {Path(asset.path).name}")
    img = _open_image(asset.path)
    progress(0.5, f"applying {preset}")
    out = apply_preset(img, preset)

    out_dir = config.TOUCHUP_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = _output_ext(asset)
    out_path = out_dir / f"{asset_id}_{preset}{ext}"
    progress(0.8, "saving")
    if ext == ".webp":
        out.save(out_path, "WEBP", quality=90)
    elif ext == ".png":
        out.save(out_path, "PNG")
    else:
        out.save(out_path, "JPEG", quality=90)
    progress(1.0, f"saved {out_path.name}")
    log.info("touchup %s on asset %s -> %s", preset, asset_id, out_path)
    return str(out_path)


def preview_touchup(asset_id: int, preset: str) -> bytes:
    """Side-by-side before|after JPEG composite (both halves same size)."""
    preset = (preset or "").strip()
    if preset not in PRESETS:
        raise ValueError(
            f"unknown preset {preset!r}; expected one of {', '.join(PRESETS)}")
    with SessionLocal() as db:
        asset = db.get(models.Asset, asset_id)
    if asset is None:
        raise ValueError(f"asset {asset_id} not found")
    if not Path(asset.path).exists():
        raise ValueError(f"asset file missing: {asset.path}")
    if asset.kind != "photo":
        raise ValueError(
            f"asset {asset_id} is a video; touch-up currently supports photos")

    before = _open_image(asset.path)
    after = apply_preset(before.copy(), preset)
    # Same size for both halves.
    w, h = before.size
    composite = Image.new("RGB", (w * 2, h))
    composite.paste(before, (0, 0))
    composite.paste(after, (w, 0))

    import io
    buf = io.BytesIO()
    composite.save(buf, "JPEG", quality=88)
    return buf.getvalue()
