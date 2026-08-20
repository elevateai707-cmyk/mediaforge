"""Assign city/region/country on an asset from GPS, folder, or filename."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .. import models
from ..geo.gazetteer import GeocodeHit, find_place_in_text, reverse_geocode

_SOURCE_RANK = {
    "manual": 1.0,
    "gps": 0.95,
    "folder": 0.70,
    "filename": 0.55,
    "caption": 0.45,
    "transcript": 0.40,
}


def _apply_hit(asset: models.Asset, hit: GeocodeHit, source: str, confidence: float) -> None:
    asset.place_name = asset.place_name or hit.name
    asset.city = hit.name
    asset.region = hit.region or asset.region
    asset.country = hit.country or asset.country
    asset.country_code = hit.country_code or asset.country_code
    asset.location_source = source
    asset.location_confidence = confidence
    if asset.gps_lat is None:
        asset.gps_lat = hit.lat
    if asset.gps_lon is None:
        asset.gps_lon = hit.lon


def assign_place(asset: models.Asset) -> None:
    """Fill place fields. GPS wins; folder then filename as fallbacks."""
    if asset.location_source == "manual":
        return
    path = Path(asset.path)

    if asset.gps_lat is not None and asset.gps_lon is not None:
        hit = reverse_geocode(float(asset.gps_lat), float(asset.gps_lon))
        if hit is not None:
            _apply_hit(asset, hit, source="gps", confidence=_SOURCE_RANK["gps"])
            return

    folder_hit = find_place_in_text(str(path.parent))
    if folder_hit is not None:
        _apply_hit(asset, folder_hit, source="folder", confidence=_SOURCE_RANK["folder"])
        return

    file_hit = find_place_in_text(path.stem)
    if file_hit is not None:
        _apply_hit(asset, file_hit, source="filename", confidence=_SOURCE_RANK["filename"])


def upgrade_place_from_text(
    asset: models.Asset, text: str, source: str, confidence: Optional[float] = None,
) -> bool:
    """Later pipeline stages may upgrade a weaker place guess. Never overrides manual."""
    if asset.location_source == "manual":
        return False
    conf = confidence if confidence is not None else _SOURCE_RANK.get(source, 0.3)
    current = float(asset.location_confidence or 0.0)
    if current >= conf:
        return False
    hit = find_place_in_text(text or "")
    if hit is None:
        return False
    _apply_hit(asset, hit, source=source, confidence=conf)
    return True
