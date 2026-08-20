"""Scan a registered music folder for audio tracks."""
from __future__ import annotations

from pathlib import Path

MUSIC_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}


def list_tracks(music_dir: str) -> list[dict]:
    root = Path(music_dir).expanduser() if music_dir else None
    if root is None or not root.is_dir():
        return []
    tracks: list[dict] = []
    for child in sorted(root.rglob("*")):
        if child.is_file() and child.suffix.lower() in MUSIC_EXTS:
            tracks.append({
                "name": child.stem,
                "path": str(child.resolve()),
                "ext": child.suffix.lower(),
            })
    return tracks
