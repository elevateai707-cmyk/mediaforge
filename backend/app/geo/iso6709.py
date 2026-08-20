"""ISO 6709 (and Apple QuickTime) GPS string parser.

iPhone .MOV/.MP4 location is commonly stored as:

    Keys:GPSCoordinates = "+53.5461-113.4938/"
    Com.apple.quicktime.location.ISO6709 = "+53.5461-113.4938+668.2/"

Those are ISO 6709 Annex H strings: compact ±lat ±lon [±alt] with a
trailing slash. Apple also sometimes writes a comma form:

    "+53.5461, -113.4938"
    "53.5461, -113.4938, 668.5"

``parse`` / ``parse_point`` never raise. Garbage in → None out.

ISO 6709 compact degrees / DM / DMS is disambiguated by the number of
digits *before* the decimal point:

    lat  2 digits → degrees          lon  3 digits → degrees
    lat  4 digits → degrees+minutes  lon  5 digits → degrees+minutes
    lat  6 digits → deg+min+sec      lon  7 digits → deg+min+sec
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class GeoPoint:
    """WGS-84 point. ``alt`` is metres above the ellipsoid when present."""

    lat: float
    lon: float
    alt: Optional[float] = None

    def as_tuple(self) -> tuple[float, float]:
        return (self.lat, self.lon)


# ±lat ±lon [±alt] [CRSident] [/]
# lat/lon bodies are digits with at most one decimal point (no sign inside).
_ISO_COMPACT = re.compile(
    r"""
    ^\s*
    (?P<lat_hem>[+-])
    (?P<lat>\d+(?:\.\d+)?)
    (?P<lon_hem>[+-])
    (?P<lon>\d+(?:\.\d+)?)
    (?:
        (?P<alt_hem>[+-])
        (?P<alt>\d+(?:\.\d+)?)
    )?
    (?:CRS(?P<crs>[A-Za-z0-9_]+))?
    /?
    \s*$
    """,
    re.VERBOSE,
)

# "lat, lon" or "lat, lon, alt" with optional ± / N/S / E/W suffixes.
_COMMA = re.compile(
    r"""
    ^\s*
    (?P<lat_hem>[+-])?
    (?P<lat>\d+(?:\.\d+)?)
    (?:\s*[°]?\s*(?P<lat_ns>[NnSs]))?
    \s*[,;\s]\s*
    (?P<lon_hem>[+-])?
    (?P<lon>\d+(?:\.\d+)?)
    (?:\s*[°]?\s*(?P<lon_ew>[EeWw]))?
    (?:
        \s*[,;\s]\s*
        (?P<alt_hem>[+-])?
        (?P<alt>\d+(?:\.\d+)?)
        (?:\s*m)?
    )?
    \s*$
    """,
    re.VERBOSE,
)

_LAT_MIN, _LAT_MAX = -90.0, 90.0
_LON_MIN, _LON_MAX = -180.0, 180.0
# Allow a hair of float noise on the poles / antimeridian.
_EPS = 1e-9


def parse(value: Optional[str]) -> Optional[tuple[float, float]]:
    """Return ``(lat, lon)`` in decimal degrees, or ``None``."""
    point = parse_point(value)
    if point is None:
        return None
    return point.as_tuple()


def parse_point(value: Optional[str]) -> Optional[GeoPoint]:
    """Parse an ISO 6709 / Apple GPS string into a :class:`GeoPoint`.

    Accepts compact ISO 6709 (``+53.5461-113.4938/``), the same with
    altitude, optional ``CRS…`` suffix, and Apple's comma-separated form.
    Returns ``None`` for missing, empty, or unparseable input. Never raises.
    """
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            value = value.decode("ascii")
        except (UnicodeDecodeError, AttributeError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None

    point = _parse_compact(text)
    if point is None:
        point = _parse_comma(text)
    if point is None:
        return None
    if not _in_range(point.lat, point.lon):
        return None
    return GeoPoint(
        lat=_round(point.lat),
        lon=_round(point.lon),
        alt=_round(point.alt) if point.alt is not None else None,
    )


def _parse_compact(text: str) -> Optional[GeoPoint]:
    m = _ISO_COMPACT.match(text)
    if not m:
        return None
    try:
        lat = _sexagesimal(m.group("lat_hem"), m.group("lat"), lon=False)
        lon = _sexagesimal(m.group("lon_hem"), m.group("lon"), lon=True)
    except (TypeError, ValueError):
        return None
    if lat is None or lon is None:
        return None
    alt = None
    if m.group("alt") is not None:
        try:
            alt = float(m.group("alt"))
            if m.group("alt_hem") == "-":
                alt = -alt
        except ValueError:
            alt = None
    return GeoPoint(lat=lat, lon=lon, alt=alt)


def _parse_comma(text: str) -> Optional[GeoPoint]:
    """Apple / exiftool comma or whitespace form: ``lat, lon[, alt]``."""
    m = _COMMA.match(text)
    if not m:
        return None
    try:
        lat = float(m.group("lat"))
        lon = float(m.group("lon"))
    except (TypeError, ValueError):
        return None
    if m.group("lat_hem") == "-":
        lat = -abs(lat)
    elif m.group("lat_hem") == "+":
        lat = abs(lat)
    ns = m.group("lat_ns")
    if ns:
        lat = -abs(lat) if ns.upper() == "S" else abs(lat)

    if m.group("lon_hem") == "-":
        lon = -abs(lon)
    elif m.group("lon_hem") == "+":
        lon = abs(lon)
    ew = m.group("lon_ew")
    if ew:
        lon = -abs(lon) if ew.upper() == "W" else abs(lon)

    alt = None
    if m.group("alt") is not None:
        try:
            alt = float(m.group("alt"))
            if m.group("alt_hem") == "-":
                alt = -alt
        except ValueError:
            alt = None
    return GeoPoint(lat=lat, lon=lon, alt=alt)


def _sexagesimal(hem: str, raw: str, *, lon: bool) -> Optional[float]:
    """Decode one compact ISO 6709 component to decimal degrees.

    Digit-count-before-decimal selects degrees / DM / DMS. Unpadded
    decimal degrees (``5.5`` for latitude) are accepted as degrees —
    iPhone always pads, but lavfi/exiftool fixtures sometimes don't.
    """
    deg_digits = 3 if lon else 2
    sign = -1.0 if hem == "-" else 1.0
    if "." in raw:
        whole, frac = raw.split(".", 1)
        if not whole or not frac or not whole.isdigit() or not frac.isdigit():
            return None
        fraction = "." + frac
    else:
        whole, fraction = raw, ""
        if not whole.isdigit():
            return None

    n = len(whole)
    try:
        if n <= deg_digits:
            degrees = float(whole + fraction)
        elif n <= deg_digits + 2:
            deg = float(whole[:deg_digits])
            minutes = float(whole[deg_digits:] + fraction)
            if minutes >= 60.0:
                return None
            degrees = deg + minutes / 60.0
        elif n <= deg_digits + 4:
            deg = float(whole[:deg_digits])
            minutes = float(whole[deg_digits : deg_digits + 2])
            seconds = float(whole[deg_digits + 2 :] + fraction)
            if minutes >= 60.0 or seconds >= 60.0:
                return None
            degrees = deg + minutes / 60.0 + seconds / 3600.0
        else:
            return None
    except ValueError:
        return None
    if not math.isfinite(degrees):
        return None
    return sign * degrees


def _in_range(lat: float, lon: float) -> bool:
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return False
    if lat < _LAT_MIN - _EPS or lat > _LAT_MAX + _EPS:
        return False
    if lon < _LON_MIN - _EPS or lon > _LON_MAX + _EPS:
        return False
    return True


def _round(value: float, ndigits: int = 7) -> float:
    """7 dp is ~1.1 cm at the equator — well past iPhone GPS noise."""
    return round(float(value), ndigits)
