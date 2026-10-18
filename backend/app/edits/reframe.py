"""Lightweight, deterministic crop planning for vertical renders.

The planner deliberately does not load a face model.  It first reuses face
detections already stored by the indexing pipeline, then uses a handful of
small OpenCV frames for motion when OpenCV is available, and finally falls
back to the centre of the source.  Its output is JSON-safe so it can be kept
as a sidecar and reused for several output presets.
"""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Mapping, Optional

from sqlalchemy import text

from .. import config
from ..db import SessionLocal

log = logging.getLogger("mediaforge.reframe")

DEFAULT_SAFE_ZONES = {"top": 0.12, "bottom": 0.22, "left": 0.0, "right": 0.14}
_SAMPLES = (0.0, 0.25, 0.5, 0.75, 1.0)


def normalize_safe_zones(value: Optional[Mapping[str, Any]] = None) -> dict[str, float]:
    """Return bounded, caption-safe fractional margins.

    Unknown keys are ignored so clients can add presentation-only settings
    without changing crop planning.
    """
    out = dict(DEFAULT_SAFE_ZONES)
    for key in out:
        if value is not None and key in value:
            try:
                out[key] = min(0.45, max(0.0, float(value[key])))
            except (TypeError, ValueError):
                pass
    return out


def _probe_size(path: str) -> tuple[int, int, float]:
    """Return display dimensions and duration, honoring video rotation metadata."""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=width,height:stream_tags=rotate:stream_side_data=rotation:format=duration",
             "-of", "json", path],
            capture_output=True, text=True, timeout=30, check=False,
        )
        data = json.loads(proc.stdout or "{}")
        stream = next((s for s in data.get("streams", []) if s.get("width")), {})
        width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
        rotation = (stream.get("tags") or {}).get("rotate")
        if rotation is None:
            for side_data in stream.get("side_data_list") or []:
                if "rotation" in side_data:
                    rotation = side_data["rotation"]
                    break
        try:
            if int(float(rotation or 0)) % 180:
                width, height = height, width
        except (TypeError, ValueError):
            pass
        return width, height, float((data.get("format") or {}).get("duration") or 0.0)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return 0, 0, 0.0


def _face_frame_size(asset_id: int, kind: str, display_width: int,
                     display_height: int) -> tuple[int, int]:
    """Dimensions used by the detector for current and legacy face records."""
    if kind != "video":
        return display_width, display_height
    try:
        from PIL import Image
        frames = sorted((config.THUMBS_DIR / str(asset_id)).glob("face_*.jpg"))
        if frames:
            with Image.open(frames[0]) as image:
                return image.size
    except (OSError, ValueError):
        pass
    # Video faces were extracted with max_w=960.  This deterministic fallback
    # repairs old records after their thumbnail cache has been cleaned.
    if display_width > 960:
        return 960, max(1, round(display_height * 960 / display_width))
    return display_width, display_height


def _face_points(asset_id: Optional[int], start: float, end: float,
                 display_width: int, display_height: int,
                 db: Any = None) -> list[tuple[float, float, float]]:
    """Read stored face centres as ``(absolute time, x, y)``.

    The SQL is intentionally kept here instead of importing ORM objects; this
    makes this helper usable in render tests and avoids a lazy relationship on
    an expired Asset instance.
    """
    if asset_id is None:
        return []
    own_session = db is None
    session = db or SessionLocal()
    try:
        rows = session.execute(text(
            "SELECT d.bbox, d.frame_time, a.kind FROM face_detections d "
            "LEFT JOIN assets a ON a.id = d.asset_id WHERE d.asset_id = :asset_id "
            "AND (d.frame_time IS NULL OR d.frame_time >= :start_time AND d.frame_time <= :end_time)"),
            {"asset_id": asset_id, "start_time": start, "end_time": end},
        ).all()
        result: list[tuple[float, float, float]] = []
        for bbox, frame_time, kind in rows:
            try:
                x1, y1, x2, y2 = json.loads(bbox or "[]")
                detector_w, detector_h = _face_frame_size(
                    asset_id, str(kind or "video"), display_width, display_height)
                cx = (float(x1) + float(x2)) / 2 / max(1, detector_w) * display_width
                cy = (float(y1) + float(y2)) / 2 / max(1, detector_h) * display_height
                result.append((float(frame_time if frame_time is not None else start), cx, cy))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return result
    except Exception as exc:  # a render should work against an old/no DB
        log.debug("stored face lookup skipped: %s", exc)
        return []
    finally:
        if own_session:
            session.close()


def _motion_points(path: str, start: float, end: float, source_w: int, source_h: int) -> list[tuple[float, float, float]]:
    """Find a coarse moving-region centre from at most five tiny frames."""
    try:
        import cv2  # type: ignore
    except Exception:
        return []
    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        return []
    points: list[tuple[float, float, float]] = []
    previous = None
    duration = max(0.01, end - start)
    try:
        for fraction in _SAMPLES:
            timestamp = start + duration * fraction
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
            ok, frame = capture.read()
            if not ok:
                continue
            small = cv2.resize(frame, (160, max(2, round(frame.shape[0] * 160 / frame.shape[1]))))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            if previous is not None:
                diff = cv2.absdiff(gray, previous)
                _, mask = cv2.threshold(diff, 20, 255, cv2.THRESH_BINARY)
                moments = cv2.moments(mask)
                if moments["m00"] > 100:
                    x = moments["m10"] / moments["m00"] / max(1, mask.shape[1])
                    y = moments["m01"] / moments["m00"] / max(1, mask.shape[0])
                    points.append((timestamp, x * source_w, y * source_h))
            previous = gray
    except Exception as exc:
        log.debug("motion analysis skipped: %s", exc)
    finally:
        capture.release()
    return points


def _smooth(points: list[tuple[float, float, float]], start: float, end: float,
            width: int, height: int) -> list[dict[str, float]]:
    """Sample a bounded pan path; neighbouring points move at most 18% frame."""
    if not points:
        return [{"t": f, "x": 0.5, "y": 0.5} for f in _SAMPLES]
    points.sort(key=lambda p: p[0])
    out: list[dict[str, float]] = []
    last_x = last_y = None
    span = max(0.01, end - start)
    max_dx, max_dy = 0.18, 0.18
    for f in _SAMPLES:
        wanted = start + span * f
        nearest = min(points, key=lambda p: abs(p[0] - wanted))
        x = min(1.0, max(0.0, nearest[1] / max(1, width)))
        y = min(1.0, max(0.0, nearest[2] / max(1, height)))
        if last_x is not None:
            x = min(last_x + max_dx, max(last_x - max_dx, x))
            y = min(last_y + max_dy, max(last_y - max_dy, y))
        out.append({"t": round(f, 4), "x": round(x, 5), "y": round(y, 5)})
        last_x, last_y = x, y
    return out


def sidecar_path(path: str, asset_id: Optional[int] = None, start: float = 0.0,
                 end: Optional[float] = None) -> Path:
    """Stable cache path for one source interval; never writes beside media."""
    ident = f"{asset_id or ''}|{Path(path).resolve()}|{start:.3f}|{end if end is not None else ''}"
    digest = hashlib.sha256(ident.encode("utf-8")).hexdigest()[:20]
    return config.RENDER_TMP_DIR / "reframes" / f"{digest}.json"


def analyze_reframe(path: str, *, asset_id: Optional[int] = None, start: float = 0.0,
                    end: Optional[float] = None, mode: str = "smart",
                    safe_zones: Optional[Mapping[str, Any]] = None, db: Any = None,
                    use_cache: bool = True) -> dict[str, Any]:
    """Create/reuse a source-normalized crop plan for a clip interval."""
    mode = mode if mode in {"smart", "center"} else "smart"
    width, height, media_duration = _probe_size(path)
    finish = float(end if end is not None else media_duration)
    finish = max(float(start) + 0.01, finish)
    cache = sidecar_path(path, asset_id, start, finish)
    zones = normalize_safe_zones(safe_zones)
    if use_cache and cache.is_file():
        try:
            saved = json.loads(cache.read_text(encoding="utf-8"))
            if (saved.get("version") == 2 and saved.get("mode") == mode
                    and saved.get("safe_zones") == zones):
                saved["sidecar"] = str(cache)
                return saved
        except (OSError, json.JSONDecodeError):
            pass
    source = "center"
    points: list[tuple[float, float, float]] = []
    if mode == "smart" and width and height:
        points = _face_points(asset_id, float(start), finish, width, height, db)
        if points:
            source = "faces"
        else:
            points = _motion_points(path, float(start), finish, width, height)
            if points:
                source = "motion"
    analysis: dict[str, Any] = {
        "version": 2, "path": str(Path(path)), "asset_id": asset_id,
        "start": round(float(start), 4), "end": round(finish, 4),
        "source_size": {"width": width, "height": height}, "mode": mode,
        "source": source, "safe_zones": zones,
        "path_points": _smooth(points, float(start), finish, width or 1, height or 1),
    }
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(analysis, indent=2, sort_keys=True), encoding="utf-8")
        analysis["sidecar"] = str(cache)
    except OSError as exc:
        log.debug("could not write reframe sidecar: %s", exc)
    return analysis


def _path_at_fraction(analysis: Mapping[str, Any], fraction: float) -> tuple[float, float]:
    """Linearly interpolate the normalized, already-smoothed pan path."""
    parsed: list[tuple[float, float, float]] = []
    for point in analysis.get("path_points") or []:
        try:
            parsed.append((min(1.0, max(0.0, float(point.get("t", 0.0)))),
                           min(1.0, max(0.0, float(point.get("x", .5)))),
                           min(1.0, max(0.0, float(point.get("y", .5))))))
        except (AttributeError, TypeError, ValueError):
            continue
    if not parsed:
        return .5, .5
    parsed.sort(key=lambda point: point[0])
    f = min(1.0, max(0.0, float(fraction)))
    if f <= parsed[0][0]:
        return parsed[0][1], parsed[0][2]
    for first, last in zip(parsed, parsed[1:]):
        if f <= last[0]:
            span = last[0] - first[0]
            if span <= 0:
                return last[1], last[2]
            amount = (f - first[0]) / span
            return (first[1] + (last[1] - first[1]) * amount,
                    first[2] + (last[2] - first[2]) * amount)
    return parsed[-1][1], parsed[-1][2]


def _crop_path_expression(analysis: Mapping[str, Any], axis: str, duration: float) -> str:
    """An ffmpeg ``t`` expression with one linear segment per path interval."""
    key = "x" if axis == "x" else "y"
    points = analysis.get("path_points") or []
    parsed: list[tuple[float, float]] = []
    for point in points:
        try:
            parsed.append((min(1.0, max(0.0, float(point.get("t", 0.0)))),
                           min(1.0, max(0.0, float(point.get(key, .5))))) )
        except (AttributeError, TypeError, ValueError):
            continue
    if not parsed:
        return ".50000"
    parsed.sort(key=lambda point: point[0])
    # Collapse duplicate timestamps deterministically; crop expressions cannot
    # divide by their zero-length intervals.
    unique: list[tuple[float, float]] = []
    for point in parsed:
        if unique and point[0] == unique[-1][0]:
            unique[-1] = point
        else:
            unique.append(point)
    expression = f"{unique[-1][1]:.5f}"
    for (start_f, start_v), (end_f, end_v) in reversed(list(zip(unique, unique[1:]))):
        start_t, end_t = start_f * duration, end_f * duration
        segment = (f"{start_v:.5f}+({end_v:.5f}-{start_v:.5f})*"
                   f"(t-{start_t:.5f})/{max(.00001, end_t - start_t):.5f}")
        expression = f"if(lt(t,{end_t:.5f}),{segment},{expression})"
    if unique[0][0] > 0:
        expression = f"if(lt(t,{unique[0][0] * duration:.5f}),{unique[0][1]:.5f},{expression})"
    return expression


def build_crop_filter(analysis: Mapping[str, Any], width: int, height: int,
                      duration: float) -> str:
    """Return an ffmpeg crop filter after a cover-scale operation.

    The same bounded, piecewise-linear path used by previews is evaluated in
    ffmpeg.  This preserves a subject move-and-return instead of interpolating
    directly from the first sample to the last one.
    """
    dur = max(0.01, float(duration))
    # crop x/y are expressed after scale; clip to the legal input bounds.
    cx = f"({_crop_path_expression(analysis, 'x', dur)})*iw-ow/2"
    cy = f"({_crop_path_expression(analysis, 'y', dur)})*ih-oh/2"
    # crop evaluates x/y for each frame; unlike scale it has no ``eval``
    # option on older ffmpeg builds.
    return (f"crop={width}:{height}:x='min(max(0,{cx}),iw-ow)':"
            f"y='min(max(0,{cy}),ih-oh)'")


def crop_box(analysis: Mapping[str, Any], target_width: int, target_height: int,
             fraction: float = 0.5) -> tuple[int, int, int, int]:
    """Source-pixel crop box used by preview stills."""
    source = analysis.get("source_size") or {}
    sw, sh = int(source.get("width") or 0), int(source.get("height") or 0)
    if sw <= 0 or sh <= 0:
        return (0, 0, 0, 0)
    ratio = target_width / max(1, target_height)
    if sw / sh > ratio:
        ch, cw = sh, max(1, round(sh * ratio))
    else:
        cw, ch = sw, max(1, round(sw / ratio))
    cx, cy = _path_at_fraction(analysis, fraction)
    x = min(sw - cw, max(0, round(cx * sw - cw / 2)))
    y = min(sh - ch, max(0, round(cy * sh - ch / 2)))
    return (x, y, cw, ch)


def generate_preview_stills(path: str, analysis: Mapping[str, Any], output_dir: Path | str,
                            *, prefix: str = "reframe", target_width: int = 1080,
                            target_height: int = 1920) -> list[str]:
    """Create three JPEG source previews with the planned crop rectangle."""
    from PIL import Image, ImageDraw

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    start, end = float(analysis.get("start", 0.0)), float(analysis.get("end", 0.0))
    duration = max(0.01, end - start)
    result: list[str] = []
    for index, fraction in enumerate((0.0, 0.5, 1.0)):
        frame = out_dir / f"{prefix}_{index + 1}.jpg"
        # The nominal end timestamp belongs just beyond the final decodable
        # frame for many mp4s, so sample a millisecond inside the interval.
        timestamp = start + duration * fraction
        if fraction >= 1.0:
            timestamp = max(start, timestamp - min(0.05, duration * 0.05))
        proc = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{timestamp:.3f}",
                               "-i", path, "-frames:v", "1", str(frame)],
                              capture_output=True, text=True, timeout=60, check=False)
        if proc.returncode != 0 or not frame.is_file():
            continue
        try:
            with Image.open(frame) as image:
                image = image.convert("RGB")
                x, y, w, h = crop_box(analysis, target_width, target_height, fraction)
                # Probe dimensions can differ from decoded dimensions; scale coordinates.
                source = analysis.get("source_size") or {}
                sx = image.width / max(1, int(source.get("width") or image.width))
                sy = image.height / max(1, int(source.get("height") or image.height))
                ImageDraw.Draw(image).rectangle((round(x * sx), round(y * sy),
                                                 round((x + w) * sx), round((y + h) * sy)),
                                                outline=(0, 220, 255), width=max(2, image.width // 200))
                image.save(frame, "JPEG", quality=90)
            result.append(str(frame))
        except (OSError, ValueError) as exc:
            log.debug("preview annotation skipped: %s", exc)
    return result


# Friendly alias used by route callers that describe these as "reframe previews".
preview_stills = generate_preview_stills
