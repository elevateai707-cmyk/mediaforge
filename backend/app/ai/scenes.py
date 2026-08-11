"""Scene detection via PySceneDetect (ContentDetector) + frame extraction.

``detect_scenes`` returns [(start_sec, end_sec), ...]. When scenedetect is
unavailable or finds no cuts, a single scene spanning the full duration is
returned so downstream stages always have something to work with.

``extract_scene_frames`` writes one representative webp per scene to
data/thumbs/<asset_id>/scene_<index>.webp and returns the paths.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from .. import config
from ..proc import tool_env
from .gpu import set_model_status

log = logging.getLogger("mediaforge.scenes")

_IMPORT_ERROR: Optional[str] = None
try:
    from scenedetect import ContentDetector, detect  # type: ignore
except Exception as exc:  # pragma: no cover - optional dependency
    _IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
    ContentDetector = None  # type: ignore
    detect = None  # type: ignore

THRESHOLD = 27.0  # ContentDetector sensitivity


def _probe_duration(path: str) -> float:
    """Best-effort duration in seconds via ffprobe (0.0 on failure)."""
    import json
    import subprocess
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", path],
            capture_output=True, text=True, timeout=30, env=tool_env())
        if proc.returncode == 0:
            data = json.loads(proc.stdout or "{}")
            return float(data.get("format", {}).get("duration") or 0.0)
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def _fc_seconds(fc) -> float:
    """FrameTimecode -> seconds across scenedetect versions."""
    for attr in ("get_seconds",):
        getter = getattr(fc, attr, None)
        if callable(getter):
            try:
                return float(getter())
            except Exception:  # noqa: BLE001
                pass
    try:
        return float(fc)
    except (TypeError, ValueError):
        return 0.0


def detect_scenes(path: str, fps: Optional[float] = None) -> list[tuple[float, float]]:
    """Detect scene boundaries; [(start, end)] in seconds.

    Falls back to a single full-duration scene when scenedetect is missing
    or finds no cuts. Never raises.
    """
    if not os.path.exists(path):
        return [(0.0, 0.0)]
    if detect is None:
        set_model_status("scenedetect", "unavailable", _IMPORT_ERROR)
        dur = _probe_duration(path)
        return [(0.0, dur if dur > 0 else (fps or 30.0) * 2.0)]
    try:
        scene_list = detect(path, ContentDetector(threshold=THRESHOLD))
        out: list[tuple[float, float]] = []
        for sc in scene_list:
            try:
                start_fc, end_fc = sc[0], sc[1]
            except (TypeError, IndexError):
                start_fc = getattr(sc, "start_time", None)
                end_fc = getattr(sc, "end_time", None)
            if start_fc is None or end_fc is None:
                continue
            start = _fc_seconds(start_fc)
            end = _fc_seconds(end_fc)
            if end - start < 0.05:
                continue
            out.append((round(start, 3), round(end, 3)))
        if out:
            set_model_status("scenedetect", "loaded")
            return out
        # No cuts detected -> single scene covering the whole file.
        dur = _probe_duration(path)
        if dur <= 0:
            dur = (fps or 30.0) * 2.0
        return [(0.0, round(dur, 3))]
    except Exception as exc:  # noqa: BLE001 - degrade, never crash
        log.warning("scene detection failed for %s: %s", path, exc)
        set_model_status("scenedetect", "unavailable", f"{type(exc).__name__}: {exc}")
        dur = _probe_duration(path)
        return [(0.0, round(dur, 3)) if dur > 0 else (0.0, 60.0)]


def extract_scene_frames(path: str, scenes: list[tuple[float, float]],
                         asset_id: int) -> list[Optional[str]]:
    """One representative webp per scene; returns output paths (None on fail).

    The representative frame is the scene midpoint. Files land in
    data/thumbs/<asset_id>/scene_<index>.webp (same dir as thumb/poster).
    """
    out_dir = config.THUMBS_DIR / f"{asset_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Optional[str]] = []
    for i, (start, end) in enumerate(scenes):
        out_path = out_dir / f"scene_{i}.webp"
        if out_path.exists():
            paths.append(str(out_path))
            continue
        at = start + (end - start) / 2.0
        try:
            import subprocess
            proc = subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-ss", f"{at:.3f}",
                 "-i", path, "-frames:v", "1",
                 "-vf", "scale='min(960,iw)':-2", str(out_path)],
                capture_output=True, text=True, timeout=120, env=tool_env())
            paths.append(str(out_path) if proc.returncode == 0
                         and out_path.exists() else None)
        except Exception as exc:  # noqa: BLE001
            log.warning("scene frame extraction failed (%s): %s", out_path, exc)
            paths.append(None)
    return paths
