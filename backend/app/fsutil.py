"""Safe filesystem stat for the Settings folder picker."""
from __future__ import annotations

from pathlib import Path

from . import config


def stat_path(raw: str) -> dict:
    """Return exists/is_dir/media counts. Rejects relative paths and .. escapes."""
    text = (raw or "").strip()
    if not text:
        return {"path": text, "exists": False, "is_dir": False,
                "videos": 0, "photos": 0, "sample_count": 0}
    path = Path(text).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("path must be an absolute path without '..'")
    resolved = path.resolve()
    exists = resolved.exists()
    is_dir = resolved.is_dir() if exists else False
    videos = photos = 0
    if is_dir:
        for child in resolved.rglob("*"):
            if not child.is_file():
                continue
            ext = child.suffix.lower()
            if ext in config.VIDEO_EXTS:
                videos += 1
            elif ext in config.IMAGE_EXTS:
                photos += 1
    elif exists and resolved.is_file():
        ext = resolved.suffix.lower()
        if ext in config.VIDEO_EXTS:
            videos = 1
        elif ext in config.IMAGE_EXTS:
            photos = 1
    return {
        "path": str(resolved),
        "exists": exists,
        "is_dir": is_dir,
        "videos": videos,
        "photos": photos,
        "sample_count": videos + photos,
    }
