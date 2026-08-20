"""Media metadata extraction: exiftool (when present) with ffprobe/Pillow fallback.

exiftool is installed by scripts/setup.sh; until then the backend degrades
gracefully: images use Pillow EXIF, videos use ffprobe. Every field is
optional and never raises.

GPS extraction covers iPhone stills and .MOV/.MP4 files: numeric EXIF GPS,
Keys:GPSCoordinates, Com.apple.quicktime.location.ISO6709, XMP LocationShown,
and ffprobe format/stream location tags.
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

from PIL import ExifTags, Image
from PIL.ExifTags import GPSTAGS

from ..geo.iso6709 import GpsPoint, parse_iso6709, parse_location_value
from ..proc import tool_env

log = logging.getLogger("mediaforge.metadata")

EXIFTOOL = "exiftool"

# Tag names (with or without group prefixes) that often hold ISO6709 strings.
_LOCATION_TAG_HINTS = (
    "gpscoordinates",
    "iso6709",
    "location",
    "gpsposition",
)

EXIFTOOL_ARGS = [
    EXIFTOOL, "-json", "-n",
    "-DateTimeOriginal", "-CreateDate", "-MediaCreateDate",
    "-Make", "-Model",
    "-GPSLatitude", "-GPSLongitude", "-GPSLatitudeRef", "-GPSLongitudeRef",
    "-GPSAltitude",
    "-GPSCoordinates",
    "-Keys:GPSCoordinates",
    "-QuickTime:GPSCoordinates",
    "-QuickTime:LocationInformation",
    "-LocationShownGPSLatitude", "-LocationShownGPSLongitude",
    "-XMP:GPSLatitude", "-XMP:GPSLongitude",
    "-Composite:GPSLatitude", "-Composite:GPSLongitude",
    "-Composite:GPSPosition",
]


def _run(cmd: list[str], timeout: int = 20) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, env=tool_env())
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
                                  text=True, timeout=10, env=tool_env())
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
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m:%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
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


def _apply_hemisphere(value: Any, ref: Any, south_or_west: str) -> Optional[float]:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if isinstance(ref, str) and ref.strip().upper().startswith(south_or_west):
        return -abs(num)
    return num


def gps_from_exif_row(row: dict[str, Any]) -> Optional[GpsPoint]:
    """Pull WGS84 coordinates out of one exiftool JSON object."""
    if not row:
        return None

    lat = row.get("GPSLatitude")
    lon = row.get("GPSLongitude")
    if lat is None:
        lat = row.get("LocationShownGPSLatitude") or row.get("XMP:GPSLatitude")
    if lon is None:
        lon = row.get("LocationShownGPSLongitude") or row.get("XMP:GPSLongitude")
    lat_f = _apply_hemisphere(lat, row.get("GPSLatitudeRef"), "S")
    lon_f = _apply_hemisphere(lon, row.get("GPSLongitudeRef"), "W")
    alt: Optional[float] = None
    try:
        if row.get("GPSAltitude") is not None:
            alt = float(row["GPSAltitude"])
    except (TypeError, ValueError):
        alt = None
    if lat_f is not None and lon_f is not None and abs(lat_f) <= 90 and abs(lon_f) <= 180:
        return GpsPoint(lat=lat_f, lon=lon_f, alt=alt)

    for key, value in row.items():
        hint = key.lower().replace(":", "")
        if not any(h in hint for h in _LOCATION_TAG_HINTS):
            continue
        pt = parse_location_value(value)
        if pt is not None:
            if alt is not None and pt.alt is None:
                return GpsPoint(lat=pt.lat, lon=pt.lon, alt=alt)
            return pt
    return None


def gps_from_ffprobe(data: dict[str, Any]) -> Optional[GpsPoint]:
    """Read location tags from ffprobe format.tags and stream.tags."""
    blobs: list[Any] = []
    fmt_tags = (data.get("format") or {}).get("tags") or {}
    blobs.extend(fmt_tags.values())
    for stream in data.get("streams") or []:
        blobs.extend((stream.get("tags") or {}).values())
    for value in blobs:
        pt = parse_location_value(value)
        if pt is not None:
            return pt
    return None


def extract_metadata(path: str, kind: str) -> dict[str, Any]:
    """Best-effort metadata extraction. Never raises.

    Returns dict with keys: mime, width, height, duration, taken_at,
    camera_make, camera_model, gps_lat, gps_lon, gps_alt.
    """
    p = Path(path)
    out: dict[str, Any] = {
        "mime": None, "width": None, "height": None, "duration": None,
        "taken_at": None, "camera_make": None, "camera_model": None,
        "gps_lat": None, "gps_lon": None, "gps_alt": None,
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


def _set_gps(out: dict[str, Any], pt: Optional[GpsPoint]) -> None:
    if pt is None:
        return
    if out.get("gps_lat") is None:
        out["gps_lat"] = pt.lat
        out["gps_lon"] = pt.lon
    if out.get("gps_alt") is None and pt.alt is not None:
        out["gps_alt"] = pt.alt


def _apply_exiftool(path: str, out: dict[str, Any]) -> None:
    if not exiftool_present():
        return
    proc = _run(EXIFTOOL_ARGS + [path], timeout=60)
    if not proc or proc.returncode != 0:
        return
    try:
        data = json.loads(proc.stdout)
        row = data[0] if data else {}
    except (json.JSONDecodeError, IndexError):
        return
    out["taken_at"] = out["taken_at"] or _parse_dt(
        row.get("DateTimeOriginal") or row.get("CreateDate") or row.get("MediaCreateDate")
    )
    out["camera_make"] = out["camera_make"] or row.get("Make")
    out["camera_model"] = out["camera_model"] or row.get("Model")
    _set_gps(out, gps_from_exif_row(row))


def _video_metadata(path: str, out: dict) -> None:
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
            _set_gps(out, gps_from_ffprobe(data))
        except (json.JSONDecodeError, ValueError):
            pass
    _apply_exiftool(path, out)


def _image_metadata(path: str, out: dict) -> None:
    try:
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
                gps_ifd = exif.get(0x8825)
                if gps_ifd is not None:
                    try:
                        gps = {GPSTAGS.get(k, k): v for k, v in gps_ifd.items()}

                        def _dms(v):
                            d, m, s = v
                            return float(d) + float(m) / 60.0 + float(s) / 3600.0

                        lat_f = _dms(gps.get("GPSLatitude"))
                        lon_f = _dms(gps.get("GPSLongitude"))
                        if gps.get("GPSLatitudeRef") == "S":
                            lat_f = -lat_f
                        if gps.get("GPSLongitudeRef") == "W":
                            lon_f = -lon_f
                        alt = None
                        if gps.get("GPSAltitude") is not None:
                            try:
                                alt = float(gps.get("GPSAltitude"))
                            except (TypeError, ValueError):
                                alt = None
                        _set_gps(out, GpsPoint(lat=lat_f, lon=lon_f, alt=alt))
                    except Exception:
                        pass
    except Exception as exc:
        log.debug("Pillow metadata failed for %s: %s", path, exc)
    _apply_exiftool(path, out)
