"""Pillow helpers: register HEIC once, open as RGB."""
from __future__ import annotations

from typing import Any

from PIL import Image, ImageFile

# iPhone libraries carry partially-written stills (interrupted AirDrop/import).
# Pillow raises OSError("tile cannot extend outside image") on those, which
# used to abort the caption stage and get reported as an Ollama outage. Decode
# what is there instead — a slightly short image still captions and hashes fine.
ImageFile.LOAD_TRUNCATED_IMAGES = True

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
    # Close the file handle as soon as pixels are decoded; the returned image
    # is a detached in-memory copy.
    with Image.open(path) as src:
        src.load()
        return src.convert("RGB") if src.mode != "RGB" else src.copy()
