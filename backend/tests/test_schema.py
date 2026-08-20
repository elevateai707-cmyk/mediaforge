"""Schema migration: existing libraries gain place columns without DROP."""
from __future__ import annotations

from sqlalchemy import text

from app.db import SessionLocal, init_db


def test_place_columns_exist_after_init(client):
    init_db()
    with SessionLocal() as db:
        cols = {r[1] for r in db.execute(text("PRAGMA table_info(assets)")).fetchall()}
    for name in (
        "gps_alt", "place_name", "city", "region", "country",
        "country_code", "location_source", "location_confidence",
    ):
        assert name in cols, f"missing assets.{name}"


def test_trips_tables_exist(client):
    init_db()
    with SessionLocal() as db:
        names = {
            r[0] for r in db.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
        }
    assert "trips" in names
    assert "trip_assets" in names
    assert "system_settings" in names
