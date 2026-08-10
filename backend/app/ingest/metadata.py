"""Media metadata extraction: exiftool (when present) with ffprobe/Pillow fallback.

exiftool is installed by scripts/setup.sh; until then the backend degrades
gracefully: images use Pillow EXIF, videos use ffprobe. Every field is
optional and never raises.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("mediaforge.metadata")

EXIFTOOL = "exiftool"


def _run(cmd: list[str], timeout: int = 20) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        log.debug("command failed %s: %s", cmd, exc)
        return None


_HAVE_EXIFTOOL: Optional[bool] = None


def exiftool_present() -> bool:
    """Memoized check for the exiftool binary."""
    global _HAVE_EXIFTOOL
    if _HAVE_EXIFTOOL is None:
        try:
            proc = subprocess.run(["which", "exiftool"], capture_output=True,
                                  text=True, timeout=10)
            _HAVE_EXIFTOOL = proc.returncode == 0 and bool(proc.stdout.strip())
        except Exception:
            _HAVE_EXIFTOOL = False
        log.info("exiftool present: %s", _HAVE_EXIFTOOL)
    return _HAVE_EXIFTOOL


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    s = str(value).strip().strip('"')
    if not s:
        return None
    s = re.sub(r"[:.](\d{2})$", r" \1", s, count=1) if "T" not in s else s
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f", "%Y:%m:%d %H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S%z"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _exif_to_gps(proc: subprocess.CompletedProcess) -> tuple[Optional[float], Optional[float]]:
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None, None
    if not data:
        return None, None
    row = data[0]
    lat = row.get("GPSLatitude")
    lon = row.get("GPSLongitude")
    lat_ref = row.get("GPSLatitudeRef", "N")
    lon_ref = row.get("GPSLongitudeRef", "E")
    try:
        lat_f = float(lat) * (-1 if lat_ref.upper() == "S" else 1)
        lon_f = float(lon) * (-1 if lon_ref.upper() == "W" else 1)
    except (TypeError, ValueError):
        return None, None
    return lat_f, lon_f


def extract_metadata(path: str, kind: str) -> dict[str, Any]:
    """Best-effort metadata extraction. Never raises.

    Returns dict with keys: mime, width, height, duration, taken_at,
    camera_make, camera_model, gps_lat, gps_lon.
    """
    p = Path(path)
    out: dict[str, Any] = {
        "mime": None, "width": None, "height": None, "duration": None,
        "taken_at": None, "camera_make": None, "camera_model": None,
        "gps_lat": None, "gps_lon": None,
    }
    if not p.exists():
        return out
    out["mime"] = mimetypes.guess_type(path)[0] or _guess_mime(path)
    if kind == "video":
        _video_metadata(path, out)
    else:
        _image_metadata(path, out)
    return out


def _guess_mime(path: str) -> str:
    ext = Path(path).suffix.lower()
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".heic": "image/heic", ".heif": "image/heif", ".webp": "image/webp",
        ".mov": "video/quicktime", ".mp4": "video/mp4", ".m4v": "video/x-m4v",
        ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
        ".webm": "video/webm",
    }.get(ext, "application/octet-stream")


def _video_metadata(path: str, out: dict) -> None:
    # ffprobe: duration, dimensions, creation_time
    proc = _run(["ffprobe", "-v", "quiet", "-print_format", "json",
                 "-show_format", "-show_streams", path], timeout=60)
    if proc and proc.returncode == 0:
        try:
            data = json.loads(proc.stdout)
            streams = data.get("streams", [])
            vstream = next((s for s in streams if s.get("codec_type") == "video"), None)
            if vstream:
                out["width"] = int(vstream.get("width") or 0) or None
                out["height"] = int(vstream.get("height") or 0) or None
                if vstream.get("codec_name") == "prores":
                    out["mime"] = "video/x-prores"
            fmt = data.get("format", {})
            dur = fmt.get("duration")
            if dur:
                try:
                    out["duration"] = float(dur)
                except ValueError:
                    out["duration"] = None
            created = fmt.get("tags", {}).get("creation_time")
            out["taken_at"] = _parse_dt(created) if created else None
        except (json.JSONDecodeError, ValueError):
            pass
    # exiftool adds camera info for phone videos when available
    if exiftool_present():
        proc = _run([EXIFTOOL, "-json", "-DateTimeOriginal", "-Make", "-Model",
                     "-GPSLatitude", "-GPSLongitude", "-GPSLatitudeRef",
                     "-GPSLongitudeRef", path], timeout=60)
        if proc and proc.returncode == 0:
            try:
                data = json.loads(proc.stdout)
                row = data[0] if data else {}
                out["taken_at"] = out["taken_at"] or _parse_dt(
                    row.get("DateTimeOriginal"))
                out["camera_make"] = row.get("Make")
                out["camera_model"] = row.get("Model")
                lat, lon = _exif_to_gps(proc)
                out["gps_lat"] = out["gps_lat"] or lat
                out["gps_lon"] = out["gps_lon"] or lon
            except (json.JSONDecodeError, IndexError):
                pass


def _image_metadata(path: str, out: dict) -> None:
    # Pillow: dimensions (+ EXIF when exiftool absent)
    try:
        from PIL import Image, ExifTags
        with Image.open(path) as img:
            out["width"], out["height"] = img.size
            exif = img.getexif()
            if exif:
                tagmap = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
                dt = _parse_dt(tagmap.get("DateTimeOriginal") or tagmap.get("DateTime"))
                if dt:
                    out["taken_at"] = dt
                make = tagmap.get("Make")
                model = tagmap.get("Model")
                if make:
                    out["camera_make"] = str(make).strip()
                if model:
                    out["camera_model"] = str(model).strip()
                lat = exif.get(0x8825)  # GPS IFD
                if lat is not None:
                    try:
                        from PIL.ExifTags import GPSTAGS
                        gps = {GPSTAGS.get(k, k): v for k, v in lat.items()}
                        def _dms(v):
                            d, m, s = v
                            return d + m / 60.0 + s / 3600.0
                        lat_f = _dms(gps.get("GPSLatitude"))
                        lon_f = _dms(gps.get("GPSLongitude"))
                        if gps.get("GPSLatitudeRef") == "S":
                            lat_f = -lat_f
                        if gps.get("GPSLongitudeRef") == "W":
                            lon_f = -lon_f
                        out["gps_lat"], out["gps_lon"] = lat_f, lon_f
                    except Exception:
                        pass
    except Exception as exc:
        log.debug("Pillow metadata failed for %s: %s", path, exc)
    # exiftool (when installed) gives richer tags
    if exiftool_present():
        proc = _run([EXIFTOOL, "-json", "-DateTimeOriginal", "-Make", "-Model",
                     "-GPSLatitude", "-GPSLongitude", "-GPSLatitudeRef",
                     "-GPSLongitudeRef", path], timeout=60)
        if proc and proc.returncode == 0:
            try:
                data = json.loads(proc.stdout)
                row = data[0] if data else {}
                out["taken_at"] = out["taken_at"] or _parse_dt(
                    row.get("DateTimeOriginal"))
                out["camera_make"] = out["camera_make"] or row.get("Make")
                out["camera_model"] = out["camera_model"] or row.get("Model")
                lat, lon = _exif_to_gps(proc)
                out["gps_lat"] = out["gps_lat"] or lat
                out["gps_lon"] = out["gps_lon"] or lon
            except (json.JSONDecodeError, IndexError):
                pass
