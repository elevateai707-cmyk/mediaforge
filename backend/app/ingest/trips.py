"""Cluster indexed assets into trips (city + date span).

A trip is a run of assets whose timestamps are within a 36-hour gap of their
neighbours AND that share a place. A known city wins; failing that, GPS within
80 km; the same-folder fallback applies only when neither asset can be placed
at all (a phone camera roll puts every city in the same DCIM folder).
Recompute is idempotent: existing trips are matched by (city, start date, end
date) so titles survive a rescan.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from .. import models
from ..db import SessionLocal
from ..geo.gazetteer import haversine_km
from ..ws import manager

log = logging.getLogger("mediaforge.trips")

GAP = timedelta(hours=36)
NEAR_KM = 80.0


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _when(asset: models.Asset) -> datetime:
    return _aware(asset.taken_at) or _aware(asset.added_at) or datetime.now(timezone.utc)


def _folder(asset: models.Asset) -> str:
    try:
        return str(Path(asset.path).resolve().parent)
    except Exception:
        return str(Path(asset.path).parent)


def _city_key(asset: models.Asset) -> str:
    return (asset.city or "").strip().lower()


def _has_gps(asset: models.Asset) -> bool:
    return asset.gps_lat is not None and asset.gps_lon is not None


def _location_match(a: models.Asset, b: models.Asset) -> bool:
    """Do two assets belong to the same place?

    Known locations are authoritative. The same-folder fallback only applies
    when neither side can be placed: a phone camera roll buckets everything
    into a couple of DCIM folders (100APPLE/101APPLE), so treating "same
    folder" as "same place" merged every city into one trip.
    """
    ca, cb = _city_key(a), _city_key(b)
    if ca and cb:
        return ca == cb
    if _has_gps(a) and _has_gps(b):
        return haversine_km(float(a.gps_lat), float(a.gps_lon),
                            float(b.gps_lat), float(b.gps_lon)) < NEAR_KM
    return _folder(a) == _folder(b)


def _can_join(group: list[models.Asset], asset: models.Asset) -> bool:
    prev = group[-1]
    if _when(asset) - _when(prev) > GAP:
        return False
    # When both the incoming asset and the run already have a known city, the
    # city decides. Otherwise a single un-placed frame in the middle of the
    # run acts as a bridge and welds two cities into one trip.
    asset_city = _city_key(asset)
    if asset_city:
        group_cities = {_city_key(m) for m in group if _city_key(m)}
        if group_cities:
            return asset_city in group_cities
    return any(_location_match(member, asset) for member in group)


def _fmt_day(dt: datetime) -> str:
    return f"{dt.strftime('%b')} {dt.day}, {dt.year}"


def _title(city: str, start: Optional[datetime], end: Optional[datetime]) -> str:
    label = city or "Trip"
    if start is None or end is None:
        return label
    if start.date() == end.date():
        return f"{label} · {_fmt_day(start)}"
    if start.year == end.year and start.month == end.month:
        return f"{label} · {start.strftime('%b')} {start.day}–{end.day}, {end.year}"
    if start.year == end.year:
        return (
            f"{label} · {start.strftime('%b')} {start.day}–"
            f"{end.strftime('%b')} {end.day}, {end.year}"
        )
    return f"{label} · {_fmt_day(start)}–{_fmt_day(end)}"


def _signature(city: str, start: Optional[datetime], end: Optional[datetime]) -> str:
    sd = start.date().isoformat() if start else ""
    ed = end.date().isoformat() if end else ""
    return f"{(city or '').lower()}|{sd}|{ed}"


def _group_assets(assets: list[models.Asset]) -> list[list[models.Asset]]:
    ordered = sorted(assets, key=lambda a: (_when(a), a.id or 0))
    groups: list[list[models.Asset]] = []
    for asset in ordered:
        if groups and _can_join(groups[-1], asset):
            groups[-1].append(asset)
        else:
            groups.append([asset])
    return [g for g in groups if g]


def cluster_trips(db: Optional[Session] = None) -> list[models.Trip]:
    """Rebuild trip membership. Returns the current trip rows."""
    own = db is None
    session = db or SessionLocal()
    try:
        assets = session.query(models.Asset).all()
        groups = _group_assets(assets)
        existing = {t.id: t for t in session.query(models.Trip).all()}
        by_sig: dict[str, models.Trip] = {}
        for trip in existing.values():
            by_sig[_signature(trip.city or "", _aware(trip.start_at), _aware(trip.end_at))] = trip

        keep_ids: set[int] = set()
        for group in groups:
            times = [_when(a) for a in group]
            start, end = min(times), max(times)
            cities = [a.city for a in group if a.city]
            city = max(set(cities), key=cities.count) if cities else ""
            region = next((a.region for a in group if a.region), None)
            lats = [float(a.gps_lat) for a in group if a.gps_lat is not None]
            lons = [float(a.gps_lon) for a in group if a.gps_lon is not None]
            lat = sum(lats) / len(lats) if lats else None
            lon = sum(lons) / len(lons) if lons else None
            cover = max(
                group,
                key=lambda a: (float(a.aesthetic_score or 0.0), -(a.id or 0)),
            )
            sig = _signature(city, start, end)
            trip = by_sig.get(sig)
            if trip is None:
                trip = models.Trip(title=_title(city, start, end), city=city or None)
                session.add(trip)
                session.flush()
                by_sig[sig] = trip
            else:
                if not trip.title:
                    trip.title = _title(city, start, end)
            trip.city = city or trip.city
            trip.region = region
            trip.start_at = start
            trip.end_at = end
            trip.asset_count = len(group)
            trip.cover_asset_id = cover.id
            trip.lat = lat
            trip.lon = lon
            trip.radius_km = 45.0 if city else 80.0
            session.query(models.TripAsset).filter_by(trip_id=trip.id).delete()
            for asset in group:
                session.add(models.TripAsset(trip_id=trip.id, asset_id=asset.id))
            keep_ids.add(trip.id)
            try:
                manager.emit({
                    "type": "trip.updated",
                    "trip_id": trip.id,
                    "title": trip.title,
                    "asset_count": trip.asset_count,
                })
            except Exception:
                pass

        stale = [t for t in existing.values() if t.id not in keep_ids]
        for trip in stale:
            session.query(models.TripAsset).filter_by(trip_id=trip.id).delete()
            session.delete(trip)
        session.commit()
        rows = (session.query(models.Trip)
                .order_by(models.Trip.start_at.desc().nullslast())
                .all())
        log.info("clustered %d trips from %d assets", len(rows), len(assets))
        return rows
    finally:
        if own:
            session.close()


def trip_asset_ids(db: Session, trip_id: int) -> list[int]:
    rows = (db.query(models.TripAsset.asset_id)
            .filter_by(trip_id=trip_id)
            .all())
    return [int(r[0]) for r in rows]


def trip_for_asset(db: Session, asset_id: int) -> Optional[int]:
    row = (db.query(models.TripAsset.trip_id)
           .filter_by(asset_id=asset_id)
           .first())
    return int(row[0]) if row else None
