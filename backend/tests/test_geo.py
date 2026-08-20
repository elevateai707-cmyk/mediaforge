"""Gazetteer + ISO 6709 parser tests (no network)."""
from __future__ import annotations

from app.geo.gazetteer import geocode_name, reverse_geocode
from app.geo.iso6709 import parse_iso6709


def test_parse_iso6709_edmonton_decimal():
    pt = parse_iso6709("+53.5461-113.4938/")
    assert pt is not None
    assert abs(pt.lat - 53.5461) < 1e-4
    assert abs(pt.lon - (-113.4938)) < 1e-4


def test_parse_iso6709_comma_form():
    pt = parse_iso6709("53.5461, -113.4938")
    assert pt is not None
    assert abs(pt.lat - 53.5461) < 1e-4
    assert abs(pt.lon - (-113.4938)) < 1e-4


def test_geocode_edmonton():
    hit = geocode_name("edmonton")
    assert hit is not None
    assert hit.name == "Edmonton"
    assert abs(hit.lat - 53.55) < 0.05
    assert abs(hit.lon - (-113.49)) < 0.05
    assert hit.radius_km == 45


def test_geocode_yeg_alias():
    hit = geocode_name("YEG")
    assert hit is not None
    assert hit.name == "Edmonton"
    assert abs(hit.lat - 53.55) < 0.05
    assert abs(hit.lon - (-113.49)) < 0.05
    assert hit.radius_km == 45


def test_geocode_edmonton_ab():
    hit = geocode_name("Edmonton, AB")
    assert hit is not None
    assert hit.name == "Edmonton"


def test_reverse_geocode_edmonton():
    hit = reverse_geocode(53.5461, -113.4938)
    assert hit is not None
    assert hit.name == "Edmonton"


def test_reverse_geocode_vancouver():
    hit = reverse_geocode(49.28, -123.12)
    assert hit is not None
    assert hit.name == "Vancouver"
