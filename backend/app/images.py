"""Pillow helpers: register HEIC once, open as RGB."""
from __future__ import annotations

from typing import Any

_HEIF_TRIED = False


def ensure_heif() -> None:
    """Register pillow-heif so Image.open can read .HEIC/.HEIF."""
    global _HEIF_TRIED
    if _HEIF_TRIED:
        return
    _HEIF_TRIED = True
    try:
        import pillow_heif  # type: ignore
        pillow_heif.register_heif_opener()
    except Exception:
        pass


def open_rgb(path: str) -> Any:
    """Open an image path as RGB (HEIC-aware). Caller owns close/lifetime."""
    ensure_heif()
    from PIL import Image
    img = Image.open(path)
    img.load()
    if img.mode != "RGB":
        img = img.convert("RGB")
    return img
