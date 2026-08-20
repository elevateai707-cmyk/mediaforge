"""Parse ISO 6709 and Apple QuickTime location strings into WGS84 coordinates.

iPhone .MOV/.MP4 files commonly store location as:
  Keys:GPSCoordinates = "+53.5461-113.4938/"
  Com.apple.quicktime.location.ISO6709 = "+53.5461-113.4938/"
  GPSCoordinates = "53.5461, -113.4938"
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Optional

# Signed decimal pair, optional altitude, optional trailing slash.
# "+53.5461-113.4938/"  "+53.5461-113.4938+670/"
_SIGNED_PAIR = re.compile(
    r"^\s*([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)(?:([+-]\d+(?:\.\d+)?))?/?\s*$"
)

# Comma / slash separated decimals, signs optional.
# "53.5461, -113.4938"  "53.5461/-113.4938"
_COMMA_PAIR = re.compile(
    r"^\s*([+-]?\d+(?:\.\d+)?)\s*[,/]\s*([+-]?\d+(?:\.\d+)?)"
    r"(?:\s*[,/]\s*([+-]?\d+(?:\.\d+)?))?\s*$"
)

# Compact ISO 6709 without explicit signs on the second half is rare;
# degrees+minutes / degrees+minutes+seconds with leading signs.
# +5323.46-11329.62/  (DDMM.m / DDDMM.m)
_COMPACT = re.compile(
    r"^\s*([+-])(\d{2,6}(?:\.\d+)?)([+-])(\d{3,7}(?:\.\d+)?)(?:[+-]\d+(?:\.\d+)?)?/?\s*$"
)


@dataclass(frozen=True)
class GpsPoint:
    lat: float
    lon: float
    alt: Optional[float] = None


def _valid(lat: float, lon: float) -> bool:
    return math.isfinite(lat) and math.isfinite(lon) and abs(lat) <= 90.0 and abs(lon) <= 180.0


def _compact_component(raw: str, is_lon: bool) -> Optional[float]:
    """Decode an ISO 6709 compact lat/lon token (DD / DDMM / DDMMSS)."""
    if "." in raw:
        whole, frac = raw.split(".", 1)
        frac_val = float("0." + frac)
    else:
        whole, frac_val = raw, 0.0
    n = len(whole)
    expected_min = 3 if is_lon else 2
    if n < expected_min:
        return None
    deg_len = expected_min
    try:
        if n == deg_len:
            return float(whole) + frac_val
        if n == deg_len + 2:
            deg = int(whole[:deg_len])
            minutes = float(whole[deg_len:]) + frac_val
            return deg + minutes / 60.0
        if n == deg_len + 4:
            deg = int(whole[:deg_len])
            minutes = int(whole[deg_len:deg_len + 2])
            seconds = float(whole[deg_len + 2:]) + frac_val
            return deg + minutes / 60.0 + seconds / 3600.0
    except ValueError:
        return None
    return None


def parse_iso6709(raw: str) -> Optional[GpsPoint]:
    """Parse an ISO 6709 / Apple location string. Returns None if unusable."""
    if raw is None:
        return None
    text = str(raw).strip().strip('"').strip("'")
    if not text:
        return None

    m = _SIGNED_PAIR.match(text)
    if m:
        lat = float(m.group(1))
        lon = float(m.group(2))
        alt = float(m.group(3)) if m.group(3) is not None else None
        if _valid(lat, lon):
            return GpsPoint(lat=lat, lon=lon, alt=alt)

    m = _COMMA_PAIR.match(text)
    if m:
        lat = float(m.group(1))
        lon = float(m.group(2))
        alt = float(m.group(3)) if m.group(3) is not None else None
        if _valid(lat, lon):
            return GpsPoint(lat=lat, lon=lon, alt=alt)

    m = _COMPACT.match(text)
    if m:
        lat = _compact_component(m.group(2), is_lon=False)
        lon = _compact_component(m.group(4), is_lon=True)
        if lat is not None and lon is not None:
            if m.group(1) == "-":
                lat = -lat
            if m.group(3) == "-":
                lon = -lon
            if _valid(lat, lon):
                return GpsPoint(lat=lat, lon=lon)

    return None


def parse_location_value(value: Any) -> Optional[GpsPoint]:
    """Best-effort parse of any GPS tag value (string, list, or number pair)."""
    if value is None:
        return None
    if isinstance(value, GpsPoint):
        return value
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            lat = float(value[0])
            lon = float(value[1])
        except (TypeError, ValueError):
            return None
        if _valid(lat, lon):
            alt = None
            if len(value) >= 3:
                try:
                    alt = float(value[2])
                except (TypeError, ValueError):
                    alt = None
            return GpsPoint(lat=lat, lon=lon, alt=alt)
        return None
    return parse_iso6709(str(value))
