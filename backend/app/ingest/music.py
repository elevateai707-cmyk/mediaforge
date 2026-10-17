"""Scan a registered music folder and derive small, local track metadata."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Optional

import numpy as np

from .. import config

MUSIC_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}

_MOOD_WORDS = {
    "upbeat": "upbeat", "happy": "upbeat", "summer": "upbeat", "party": "upbeat",
    "energetic": "energetic", "energy": "energetic", "workout": "energetic",
    "chill": "chill", "calm": "chill", "lofi": "chill", "ambient": "chill",
    "cinematic": "cinematic", "epic": "cinematic", "dramatic": "cinematic",
    "sad": "melancholic", "melancholy": "melancholic", "piano": "reflective",
}
def _cache_path(track: Path) -> Path:
    digest = hashlib.sha256(str(track.resolve()).encode("utf-8")).hexdigest()
    directory = config.DATA_DIR / "music_cache"
    return directory / f"{digest}.json"


def _read_cache(track: Path) -> Optional[dict[str, Any]]:
    try:
        stat = track.stat()
        payload = json.loads(_cache_path(track).read_text(encoding="utf-8"))
        if (payload.get("size") == stat.st_size and
                payload.get("mtime_ns") == stat.st_mtime_ns and
                isinstance(payload.get("metadata"), dict)):
            return payload["metadata"]
    except (OSError, ValueError, TypeError):
        pass
    return None


def _write_cache(track: Path, metadata: dict[str, Any]) -> None:
    """Persist one tiny entry per discovered track.

    The cache is naturally bounded by the configured music library, rather than
    evicting at an arbitrary count and re-running ffmpeg for large libraries.
    """
    try:
        directory = _cache_path(track).parent
        directory.mkdir(parents=True, exist_ok=True)
        stat = track.stat()
        _cache_path(track).write_text(json.dumps({
            "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "metadata": metadata,
        }, separators=(",", ":")), encoding="utf-8")
    except OSError:
        pass


def _ffprobe_duration(track: Path) -> Optional[float]:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(track)],
            capture_output=True, text=True, timeout=15, check=False,
        )
        value = float(result.stdout.strip())
        return round(value, 3) if value >= 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _loudness_and_bpm(track: Path) -> tuple[Optional[float], Optional[float]]:
    """Return mean dBFS and a deliberately rough beat estimate using local tools."""
    try:
        volume = subprocess.run(
            ["ffmpeg", "-v", "info", "-i", str(track), "-t", "90", "-af",
             "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        match = re.search(r"mean_volume:\s*([-+0-9.]+)\s*dB", volume.stderr)
        loudness = round(float(match.group(1)), 2) if match else None
        pcm = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(track), "-t", "90", "-ac", "1",
             "-ar", "8000", "-f", "f32le", "-"],
            capture_output=True, timeout=30, check=False,
        ).stdout
        samples = np.frombuffer(pcm, dtype=np.float32)
        if samples.size < 16_000:
            return loudness, None
        # Envelope autocorrelation is inexpensive and avoids a librosa dependency.
        hop = 320
        env = np.mean(np.abs(samples[:samples.size // hop * hop]).reshape(-1, hop), axis=1)
        env -= env.mean()
        if not np.any(env):
            return loudness, None
        corr = np.correlate(env, env, mode="full")[env.size - 1:]
        lo, hi = max(1, int(25 * 60 / 220)), min(corr.size, int(25 * 60 / 55))
        if hi <= lo:
            return loudness, None
        lag = int(np.argmax(corr[lo:hi]) + lo)
        bpm = 60 * 25 / lag
        return loudness, round(bpm, 1) if 55 <= bpm <= 220 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None, None


def _mood_tags(track: Path, root: Path) -> list[str]:
    try:
        relative = track.relative_to(root)
    except ValueError:
        relative = track
    words = re.findall(r"[a-z]+", " ".join(relative.parts).lower())
    return list(dict.fromkeys(_MOOD_WORDS[word] for word in words if word in _MOOD_WORDS))


def _metadata(track: Path, root: Path) -> dict[str, Any]:
    cached = _read_cache(track)
    if cached is not None:
        return cached
    loudness, bpm = _loudness_and_bpm(track)
    data = {
        "duration": _ffprobe_duration(track), "loudness": loudness, "rough_bpm": bpm,
        "filename": track.name, "folder": str(track.parent.relative_to(root)) if track.parent != root else "",
        "mood_tags": _mood_tags(track, root),
    }
    _write_cache(track, data)
    return data


def list_tracks(music_dir: str) -> list[dict]:
    root = Path(music_dir).expanduser() if music_dir else None
    if root is None or not root.is_dir():
        return []
    root = root.resolve()
    tracks: list[dict] = []
    for child in sorted(root.rglob("*")):
        # rglob follows a final symlink for is_file(). Reject anything whose
        # resolved target leaves the operator-configured music root.
        try:
            resolved = child.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        if child.is_file() and child.suffix.lower() in MUSIC_EXTS:
            tracks.append({
                "name": child.stem,
                "path": str(resolved),
                "ext": child.suffix.lower(),
                **_metadata(child, root),
            })
    return tracks


def select_track(music_dir: str, mood: str | None = None,
                 duration: float | None = None) -> Optional[dict]:
    """Choose a stable local track, preferring requested mood and usable length."""
    tracks = list_tracks(music_dir)
    if not tracks:
        return None
    wanted = {word for word in re.findall(r"[a-z]+", (mood or "").lower())}

    def rank(track: dict) -> tuple:
        tags = set(track.get("mood_tags") or [])
        mood_match = -len(wanted & tags)
        track_duration = track.get("duration")
        shortfall = 0.0
        distance = 0.0
        if duration and isinstance(track_duration, (int, float)):
            shortfall = max(0.0, float(duration) - float(track_duration))
            distance = abs(float(track_duration) - float(duration))
        return (mood_match, shortfall, distance, track["path"].lower())

    return min(tracks, key=rank)
