"""Offline gazetteer: curated NA metros plus reverse_geocoder fallback.

No network. GeoNames dumps are not downloaded at runtime; reverse_geocoder's
own packaged dataset is used only as a last resort when a point falls outside
every curated metro radius.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

try:
    import reverse_geocoder as _rg
except ImportError:  # pragma: no cover - installed via requirements.txt
    _rg = None

_CITIES_PATH = Path(__file__).with_name("cities.json")
_TOKEN = re.compile(r"[a-z0-9]+")

_CC_NAMES = {
    "CA": "Canada",
    "US": "United States",
    "MX": "Mexico",
}


@dataclass(frozen=True)
class City:
    name: str
    aliases: tuple[str, ...]
    lat: float
    lon: float
    radius_km: float
    region: str
    country: str
    country_code: str


@dataclass(frozen=True)
class GeocodeHit:
    name: str
    lat: float
    lon: float
    radius_km: float
    region: str = ""
    country: str = ""
    country_code: str = ""
    source: str = "gazetteer"
    distance_km: float = 0.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _norm(text: str) -> str:
    lowered = text.lower().replace(".", " ").replace(",", " ").replace("-", " ")
    lowered = re.sub(r"[^a-z0-9 ]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


@lru_cache(maxsize=1)
def load_cities() -> tuple[City, ...]:
    raw = json.loads(_CITIES_PATH.read_text(encoding="utf-8"))
    cities: list[City] = []
    for row in raw:
        aliases = tuple(_norm(a) for a in (row.get("aliases") or []) if a)
        cities.append(
            City(
                name=str(row["name"]),
                aliases=aliases,
                lat=float(row["lat"]),
                lon=float(row["lon"]),
                radius_km=float(row["radius_km"]),
                region=str(row.get("region") or ""),
                country=str(row.get("country") or ""),
                country_code=str(row.get("country_code") or ""),
            )
        )
    return tuple(cities)


def _city_keys(city: City) -> list[str]:
    keys = [_norm(city.name), *city.aliases]
    if city.region:
        keys.append(_norm(f"{city.name} {city.region}"))
        keys.append(_norm(f"{city.name} {city.region[:2]}"))
    if city.country_code:
        keys.append(_norm(f"{city.name} {city.country_code}"))
    # unique, longest first so "edmonton alberta" beats "edmonton"
    seen: set[str] = set()
    out: list[str] = []
    for k in sorted(keys, key=len, reverse=True):
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _hit_from_city(city: City, distance_km: float = 0.0, source: str = "gazetteer") -> GeocodeHit:
    return GeocodeHit(
        name=city.name,
        lat=city.lat,
        lon=city.lon,
        radius_km=city.radius_km,
        region=city.region,
        country=city.country,
        country_code=city.country_code,
        source=source,
        distance_km=distance_km,
    )


def geocode_name(text: str) -> Optional[GeocodeHit]:
    """Resolve a place string to a curated metro. Case-insensitive aliases."""
    query = _norm(text or "")
    if not query:
        return None
    cities = load_cities()

    exact: list[tuple[int, City]] = []
    for city in cities:
        for key in _city_keys(city):
            if query == key:
                exact.append((len(key), city))
                break
    if exact:
        exact.sort(key=lambda t: t[0], reverse=True)
        return _hit_from_city(exact[0][1])

    # Query contains the city name ("edmonton ab", "trip edmonton 2024").
    contained: list[tuple[int, City]] = []
    for city in cities:
        for key in _city_keys(city):
            if len(key) < 3:
                continue
            if re.search(rf"\b{re.escape(key)}\b", query):
                contained.append((len(key), city))
                break
    if contained:
        contained.sort(key=lambda t: t[0], reverse=True)
        return _hit_from_city(contained[0][1])
    return None


def find_place_in_text(text: str) -> Optional[GeocodeHit]:
    """Scan folder names, filenames, captions for a known city (longest wins)."""
    query = _norm(text or "")
    if not query:
        return None
    best: Optional[tuple[int, City]] = None
    for city in load_cities():
        for key in _city_keys(city):
            if len(key) < 3:
                continue
            if re.search(rf"\b{re.escape(key)}\b", query):
                if best is None or len(key) > best[0]:
                    best = (len(key), city)
    if best is None:
        return None
    return _hit_from_city(best[1], source="text")


def reverse_geocode(lat: float, lon: float) -> Optional[GeocodeHit]:
    """Nearest curated metro whose haversine is within radius, else GeoNames."""
    if not math.isfinite(lat) or not math.isfinite(lon):
        return None
    best: Optional[tuple[float, City]] = None
    for city in load_cities():
        dist = haversine_km(lat, lon, city.lat, city.lon)
        if dist <= city.radius_km and (best is None or dist < best[0]):
            best = (dist, city)
    if best is not None:
        return _hit_from_city(best[1], distance_km=best[0], source="gps")
    return _reverse_geocoder_fallback(lat, lon)


def _reverse_geocoder_fallback(lat: float, lon: float) -> Optional[GeocodeHit]:
    if _rg is None:
        return None
    try:
        rows = _rg.search((lat, lon))
    except Exception:
        return None
    if not rows:
        return None
    row = rows[0]
    cc = str(row.get("cc") or "").upper()
    name = str(row.get("name") or "").strip()
    if not name:
        return None
    region = str(row.get("admin1") or "").strip()
    try:
        rlat = float(row.get("lat", lat))
        rlon = float(row.get("lon", lon))
    except (TypeError, ValueError):
        rlat, rlon = lat, lon
    return GeocodeHit(
        name=name,
        lat=rlat,
        lon=rlon,
        radius_km=15.0,
        region=region,
        country=_CC_NAMES.get(cc, cc),
        country_code=cc,
        source="reverse_geocoder",
        distance_km=haversine_km(lat, lon, rlat, rlon),
    )
