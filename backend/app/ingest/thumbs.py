"""Thumbnail / poster / proxy generation.

- thumb: 512px webp for photos and videos (first frame).
- poster: video poster frame at 10% of duration (webp).
- proxy: 720p h264 mp4 proxy for videos (also used by the renderer).

All functions are best-effort and never raise.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from .. import config
from ..ingest.metadata import _run

log = logging.getLogger("mediaforge.thumbs")

THUMB_SIZE = 512
PROXY_HEIGHT = 720
PROXY_CRF = 23


def _ensure_pil_heif() -> None:
    try:
        import pillow_heif  # type: ignore
        pillow_heif.register_heif_opener()
    except Exception:
        pass


def _pil_image(path: str):
    """Open an image with HEIC support if available; None on failure."""
    _ensure_pil_heif()
    from PIL import Image
    img = Image.open(path)
    img.load()
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    return img


def make_thumb(path: str, kind: str, asset_id: int) -> str | None:
    """Write 512px webp thumb; returns path or None."""
    out_dir = config.THUMBS_DIR / f"{asset_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "thumb.webp"
    if out_path.exists():
        return str(out_path)
    try:
        if kind == "photo":
            img = _pil_image(path)
            img.thumbnail((THUMB_SIZE, THUMB_SIZE))
            img.save(out_path, "WEBP", quality=82)
        else:
            proc = _run([
                "ffmpeg", "-y", "-v", "error", "-i", path, "-frames:v", "1",
                "-vf", f"scale='min({THUMB_SIZE},iw)':-2",
                str(out_path),
            ], timeout=120)
            if not proc or proc.returncode != 0:
                return None
        return str(out_path)
    except Exception as exc:
        log.warning("thumb failed for %s: %s", path, exc)
        return None


def make_poster(path: str, asset_id: int, duration: float | None) -> str | None:
    """Video poster frame at 10% mark; returns path or None."""
    out_dir = config.THUMBS_DIR / f"{asset_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "poster.webp"
    if out_path.exists():
        return str(out_path)
    try:
        pos = 0.0
        if duration and duration > 1.0:
            pos = min(duration * 0.1, duration - 0.1)
        proc = _run([
            "ffmpeg", "-y", "-v", "error", "-ss", f"{pos:.3f}", "-i", path,
            "-frames:v", "1", "-vf", "scale='min(1280,iw)':-2",
            str(out_path),
        ], timeout=120)
        if not proc or proc.returncode != 0:
            return None
        return str(out_path)
    except Exception as exc:
        log.warning("poster failed for %s: %s", path, exc)
        return None


def make_proxy(path: str, asset_id: int) -> str | None:
    """720p h264 yuv420p proxy; returns path or None."""
    out_dir = config.PROXIES_DIR / f"{asset_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "proxy.mp4"
    if out_path.exists():
        return str(out_path)
    try:
        proc = _run([
            "ffmpeg", "-y", "-v", "error", "-i", path,
            "-vf", f"scale=-2:'min({PROXY_HEIGHT},ih)'",
            "-c:v", "libx264", "-preset", "fast", "-crf", str(PROXY_CRF),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-an", str(out_path),
        ], timeout=600)
        if not proc or proc.returncode != 0:
            return None
        return str(out_path)
    except Exception as exc:
        log.warning("proxy failed for %s: %s", path, exc)
        return None


def extract_frame(path: str, out_path: str, at: float = 0.0,
                  max_w: int = 960) -> str | None:
    """Extract a single frame from a video to a jpg; returns path or None."""
    try:
        proc = _run([
            "ffmpeg", "-y", "-v", "error", "-ss", f"{at:.3f}", "-i", path,
            "-frames:v", "1", "-vf", f"scale='min({max_w},iw)':-2",
            out_path,
        ], timeout=120)
        if proc and proc.returncode == 0:
            return out_path
        return None
    except Exception:
        return None
