"""Parse a natural-language edit intent into structured fields.

Gazetteer match is the place source of truth — coordinates never come from
an LLM. Platform tokens set defaults; explicit duration/ratio always win.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from ..geo.gazetteer import find_place_in_text, geocode_name

DEFAULT_DURATION = 30.0
DEFAULT_RATIO = "9:16"

_RATIO_RE = re.compile(r"(\d+)\s*[:/x×]\s*(\d+)")
_DUR_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:-?\s*)?(second|sec|s|minute|min)\b",
    re.IGNORECASE,
)
_RADIUS_KM_RE = re.compile(
    r"(?:widen\s+(?:the\s+)?)?radius(?:\s+to)?\s+(\d+(?:\.\d+)?)\s*k?m\b",
    re.IGNORECASE,
)
_WIDEN_RADIUS_RE = re.compile(r"\bwiden\s+(?:the\s+)?radius\b", re.IGNORECASE)

_PLATFORM_RATIO = {
    "tiktok": "9:16",
    "reels": "9:16",
    "shorts": "9:16",
    "youtube": "16:9",
}

_MOODS = (
    "upbeat", "cinematic", "chill", "energetic", "moody",
    "romantic", "dramatic", "fun", "calm", "hype",
)

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}


@dataclass
class Intent:
    raw: str
    place: Optional[str] = None
    place_lat: Optional[float] = None
    place_lon: Optional[float] = None
    radius_km: Optional[float] = None
    people: list[str] = field(default_factory=list)
    duration_s: float = DEFAULT_DURATION
    ratio: str = DEFAULT_RATIO
    platform: str = "none"
    mood: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    kind: str = "highlight"
    trip_id: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_ratio(text: str) -> Optional[str]:
    m = _RATIO_RE.search(text)
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    if a <= 0 or b <= 0 or a > 100 or b > 100:
        return None
    return f"{a}:{b}"


def _parse_duration(text: str) -> Optional[float]:
    m = _DUR_RE.search(text)
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2).lower()
    if unit.startswith("min"):
        value *= 60
    return max(3.0, min(900.0, value))


def _parse_platform(text: str) -> str:
    low = text.lower()
    if re.search(r"\btiktoks?\b", low):
        return "tiktok"
    if re.search(r"\breels?\b", low):
        return "reels"
    if re.search(r"\bshorts?\b", low):
        return "shorts"
    if re.search(r"\byoutube\b", low):
        return "youtube"
    return "none"


def _parse_kind(text: str) -> str:
    low = text.lower()
    if re.search(r"\brecap\b", low):
        return "recap"
    if re.search(r"\b(photo\s*montage|slideshow|photos)\b", low):
        return "photo_montage"
    return "highlight"


def _parse_mood(text: str) -> Optional[str]:
    low = text.lower()
    for mood in _MOODS:
        if re.search(rf"\b{re.escape(mood)}\b", low):
            return mood
    return None


def _parse_dates(text: str) -> tuple[Optional[str], Optional[str]]:
    low = text.lower()
    year = None
    ym = re.search(r"\b(20\d{2})\b", low)
    if ym:
        year = int(ym.group(1))
    month = None
    for name, num in _MONTHS.items():
        if re.search(rf"\b{name}\b", low):
            month = num
            break
    now = datetime.now(timezone.utc)
    if month and year:
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        end_month = month + 1 if month < 12 else 1
        end_year = year if month < 12 else year + 1
        end = datetime(end_year, end_month, 1, tzinfo=timezone.utc)
        return start.date().isoformat(), end.date().isoformat()
    if month:
        year = now.year
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        end_month = month + 1 if month < 12 else 1
        end_year = year if month < 12 else year + 1
        end = datetime(end_year, end_month, 1, tzinfo=timezone.utc)
        return start.date().isoformat(), end.date().isoformat()
    if year:
        return f"{year}-01-01", f"{year}-12-31"
    return None, None


def _parse_people(text: str) -> list[str]:
    names: list[str] = []
    for m in re.finditer(r"\bwith\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", text):
        names.append(m.group(1).strip())
    return list(dict.fromkeys(names))


def _parse_radius(text: str, gazetteer_km: Optional[float]) -> Optional[float]:
    """Explicit 'radius 80km' wins; bare 'widen radius' doubles the gazetteer default."""
    m = _RADIUS_KM_RE.search(text)
    if m:
        return max(1.0, min(500.0, float(m.group(1))))
    if _WIDEN_RADIUS_RE.search(text):
        return min(500.0, float(gazetteer_km or 45.0) * 2.0)
    return gazetteer_km


def parse_intent(text: str, trip_id: Optional[int] = None) -> Intent:
    raw = text or ""
    platform = _parse_platform(raw)
    explicit_ratio = _parse_ratio(raw)
    explicit_dur = _parse_duration(raw)
    ratio = explicit_ratio or _PLATFORM_RATIO.get(platform, DEFAULT_RATIO)
    if explicit_dur is not None:
        duration = explicit_dur
    elif platform in ("tiktok", "reels", "shorts"):
        duration = 30.0
    else:
        duration = DEFAULT_DURATION

    hit = find_place_in_text(raw) or geocode_name(raw)
    date_from, date_to = _parse_dates(raw)

    return Intent(
        raw=raw,
        place=hit.name if hit else None,
        place_lat=hit.lat if hit else None,
        place_lon=hit.lon if hit else None,
        radius_km=_parse_radius(raw, hit.radius_km if hit else None),
        people=_parse_people(raw),
        duration_s=float(duration),
        ratio=ratio,
        platform=platform,
        mood=_parse_mood(raw),
        date_from=date_from,
        date_to=date_to,
        kind=_parse_kind(raw),
        trip_id=trip_id,
    )
