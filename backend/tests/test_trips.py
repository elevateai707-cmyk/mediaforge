"""Trip clustering + /api/places + /api/trips."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import SessionLocal
from app.ingest.trips import cluster_trips
from app import models


def _asset(db, path, city, lat, lon, hours_offset, kind="video"):
    when = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc) + timedelta(hours=hours_offset)
    row = models.Asset(
        path=path,
        hash=path,
        kind=kind,
        city=city,
        gps_lat=lat,
        gps_lon=lon,
        taken_at=when,
        duration=5.0,
        status="pending",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_cluster_splits_edmonton_and_vancouver(client):
    with SessionLocal() as db:
        _asset(db, "/tmp/mf_trips/Edmonton Trip/a.mov", "Edmonton", 53.5461, -113.4938, 0)
        _asset(db, "/tmp/mf_trips/Edmonton Trip/b.mov", "Edmonton", 53.55, -113.49, 2)
        _asset(db, "/tmp/mf_trips/Vancouver/c.mov", "Vancouver", 49.2827, -123.1207, 4)
        cluster_trips(db)

    trips = client.get("/api/trips").json()
    cities = {t["city"] for t in trips}
    assert "Edmonton" in cities
    assert "Vancouver" in cities
    edm = next(t for t in trips if t["city"] == "Edmonton" and t["asset_count"] >= 2)
    assert edm["asset_count"] >= 2
    assert "Edmonton" in edm["title"]

    places = client.get("/api/places").json()
    names = {p["city"] for p in places}
    assert "Edmonton" in names
    assert "Vancouver" in names
