"""Trips must not weld separate cities together.

A phone camera roll drops every shot into one or two DCIM folders
(100APPLE/101APPLE), so the "same folder means same place" fallback matched
almost any two assets. A fortnight of continuous shooting collapsed into a
single 960-asset trip spanning nine cities and labelled by the plurality —
the Edmonton trip the user actually took had no card of its own.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app import models
from app.ingest.trips import _group_assets

BASE = datetime(2026, 8, 8, 12, 0, 0)
ROLL = "/home/bfam/iphone-media/100APPLE"


def _asset(aid, hours, city=None, lat=None, lon=None, folder=ROLL):
    return models.Asset(
        id=aid,
        path=f"{folder}/IMG_{aid:04d}.MOV",
        hash=f"h{aid}",
        kind="video",
        taken_at=BASE + timedelta(hours=hours),
        city=city,
        gps_lat=lat,
        gps_lon=lon,
    )


def _cities(group):
    return {a.city for a in group if a.city}


def test_same_folder_does_not_merge_two_cities():
    """Edmonton and Calgary shot 6h apart out of one camera roll stay apart."""
    assets = [
        _asset(1, 0, "Edmonton", 53.5461, -113.4938),
        _asset(2, 1, "Edmonton", 53.5439, -113.4953),
        _asset(3, 6, "Calgary", 51.0447, -114.0719),
        _asset(4, 7, "Calgary", 51.0452, -114.0611),
    ]
    groups = _group_assets(assets)
    assert len(groups) == 2, f"expected one trip per city, got {len(groups)}"
    assert {frozenset(_cities(g)) for g in groups} == {
        frozenset({"Edmonton"}), frozenset({"Calgary"})
    }
    for g in groups:
        assert len(_cities(g)) == 1, "a trip must never mix cities"


def test_unplaced_asset_cannot_bridge_two_cities():
    """A GPS-less frame between two cities must not weld them together."""
    assets = [
        _asset(1, 0, "Edmonton", 53.5461, -113.4938),
        _asset(2, 3),                                   # no city, no GPS
        _asset(3, 6, "Calgary", 51.0447, -114.0719),
    ]
    groups = _group_assets(assets)
    placed = [g for g in groups if _cities(g)]
    for g in placed:
        assert len(_cities(g)) == 1, f"trip mixes cities: {_cities(g)}"
    assert {next(iter(_cities(g))) for g in placed} == {"Edmonton", "Calgary"}


def test_unplaced_assets_still_join_their_run():
    """Footage the phone never geotagged still lands in the trip around it."""
    assets = [
        _asset(1, 0, "Edmonton", 53.5461, -113.4938),
        _asset(2, 1),                                   # no city, same roll
        _asset(3, 2, "Edmonton", 53.5439, -113.4953),
    ]
    groups = _group_assets(assets)
    assert len(groups) == 1
    assert len(groups[0]) == 3


def test_a_real_gap_still_splits_one_city():
    """Two Edmonton visits months apart are two trips, not one."""
    assets = [
        _asset(1, 0, "Edmonton", 53.5461, -113.4938),
        _asset(2, 24 * 60, "Edmonton", 53.5461, -113.4938),
    ]
    assert len(_group_assets(assets)) == 2
