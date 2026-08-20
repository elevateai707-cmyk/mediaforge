"""ISO 6709 / Apple QuickTime GPS string parser.

These tests are the contract for iPhone .MOV location ingest. The golden
path fixture ``+53.5461-113.4938/`` (Edmonton) must round-trip here
before it is ever stamped onto an asset row.
"""

from __future__ import annotations

import math

import pytest

from app.geo.iso6709 import GeoPoint, parse, parse_point


def _approx(a: float, b: float, tol: float = 1e-4) -> None:
    assert math.isclose(a, b, abs_tol=tol, rel_tol=0.0), f"{a} != {b} (tol={tol})"


class TestIPhoneCompact:
    def test_edmonton_trailing_slash(self):
        got = parse("+53.5461-113.4938/")
        assert got is not None
        _approx(got[0], 53.5461)
        _approx(got[1], -113.4938)

    def test_vancouver(self):
        got = parse("+49.2827-123.1207/")
        assert got is not None
        _approx(got[0], 49.2827)
        _approx(got[1], -123.1207)

    def test_without_trailing_slash(self):
        got = parse("+53.5461-113.4938")
        assert got is not None
        _approx(got[0], 53.5461)
        _approx(got[1], -113.4938)

    def test_with_altitude(self):
        point = parse_point("+53.5461-113.4938+668.2/")
        assert point is not None
        _approx(point.lat, 53.5461)
        _approx(point.lon, -113.4938)
        _approx(point.alt, 668.2)

    def test_negative_altitude(self):
        point = parse_point("+31.2304+121.4737-12.5/")
        assert point is not None
        _approx(point.lat, 31.2304)
        _approx(point.lon, 121.4737)
        _approx(point.alt, -12.5)

    def test_high_precision_iphone(self):
        got = parse("+53.54610000-113.49380000/")
        assert got is not None
        _approx(got[0], 53.5461)
        _approx(got[1], -113.4938)

    def test_whitespace_around(self):
        got = parse("  +53.5461-113.4938/  ")
        assert got is not None
        _approx(got[0], 53.5461)
        _approx(got[1], -113.4938)

    def test_crs_suffix(self):
        got = parse("+53.5461-113.4938CRSWGS_84/")
        assert got is not None
        _approx(got[0], 53.5461)
        _approx(got[1], -113.4938)

    def test_crs84(self):
        got = parse("+53.5461-113.4938CRS84/")
        assert got is not None
        _approx(got[0], 53.5461)

    def test_eastern_hemisphere_tokyo(self):
        got = parse("+35.6762+139.6503/")
        assert got is not None
        _approx(got[0], 35.6762)
        _approx(got[1], 139.6503)

    def test_southern_hemisphere_melbourne(self):
        got = parse("-37.8136+144.9631/")
        assert got is not None
        _approx(got[0], -37.8136)
        _approx(got[1], 144.9631)

    def test_southwest_santiago(self):
        got = parse("-33.4489-70.6693/")
        assert got is not None
        _approx(got[0], -33.4489)
        _approx(got[1], -70.6693)

    def test_integer_degrees(self):
        got = parse("+53-113/")
        assert got is not None
        _approx(got[0], 53.0)
        _approx(got[1], -113.0)

    def test_parse_returns_tuple_not_point(self):
        got = parse("+53.5461-113.4938/")
        assert isinstance(got, tuple) and len(got) == 2
        assert parse_point("+53.5461-113.4938/").as_tuple() == got


class TestSexagesimal:
    def test_dm_edmonton(self):
        # 53°32.766' N, 113°29.628' W == 53.5461, -113.4938
        got = parse("+5332.766-11329.628/")
        assert got is not None
        _approx(got[0], 53.5461)
        _approx(got[1], -113.4938)

    def test_dms_edmonton(self):
        # 53°32'46.00" N, 113°29'37.68" W
        got = parse("+533246.00-1132937.68/")
        assert got is not None
        _approx(got[0], 53 + 32 / 60 + 46 / 3600)
        _approx(got[1], -(113 + 29 / 60 + 37.68 / 3600))

    def test_dm_padded_longitude(self):
        got = parse("+0936.0+07615.0/")
        assert got is not None
        _approx(got[0], 9.6)
        _approx(got[1], 76.25)

    def test_everest_with_altitude(self):
        point = parse_point("+27.5916+086.5640+8850/")
        assert point is not None
        _approx(point.lat, 27.5916)
        _approx(point.lon, 86.5640)
        _approx(point.alt, 8850.0)

    def test_minutes_out_of_range_rejected(self):
        assert parse("+5360.0-11300.0/") is None

    def test_seconds_out_of_range_rejected(self):
        assert parse("+533260.0-1130000.0/") is None

    def test_three_digit_lat_is_dm_not_100_degrees(self):
        # +100.0 → 10°00.0′, not 100°.
        got = parse("+100.0+010.0/")
        assert got is not None
        _approx(got[0], 10.0)
        _approx(got[1], 10.0)


class TestCommaForm:
    def test_signed_comma(self):
        got = parse("+53.5461, -113.4938")
        assert got is not None
        _approx(got[0], 53.5461)
        _approx(got[1], -113.4938)

    def test_unsigned_comma_negative_lon(self):
        got = parse("53.5461, -113.4938")
        assert got is not None
        _approx(got[0], 53.5461)
        _approx(got[1], -113.4938)

    def test_comma_with_altitude(self):
        point = parse_point("53.5461, -113.4938, 668.5")
        assert point is not None
        _approx(point.lat, 53.5461)
        _approx(point.lon, -113.4938)
        _approx(point.alt, 668.5)

    def test_semicolon_separator(self):
        got = parse("53.5461; -113.4938")
        assert got is not None
        _approx(got[0], 53.5461)
        _approx(got[1], -113.4938)

    def test_whitespace_separator(self):
        got = parse("53.5461 -113.4938")
        assert got is not None
        _approx(got[0], 53.5461)
        _approx(got[1], -113.4938)

    def test_hemisphere_letters(self):
        got = parse("53.5461 N, 113.4938 W")
        assert got is not None
        _approx(got[0], 53.5461)
        _approx(got[1], -113.4938)

    def test_hemisphere_letters_glued(self):
        got = parse("53.5461N, 113.4938W")
        assert got is not None
        _approx(got[0], 53.5461)
        _approx(got[1], -113.4938)

    def test_south_east_letters(self):
        got = parse("33.8688 S, 151.2093 E")
        assert got is not None
        _approx(got[0], -33.8688)
        _approx(got[1], 151.2093)


class TestPolesAndMeridians:
    def test_equator_prime_meridian(self):
        got = parse("+00.0+000.0/")
        assert got is not None
        _approx(got[0], 0.0)
        _approx(got[1], 0.0)

    def test_north_pole(self):
        got = parse("+90+000/")
        assert got is not None
        _approx(got[0], 90.0)
        _approx(got[1], 0.0)

    def test_south_pole(self):
        got = parse("-90+000/")
        assert got is not None
        _approx(got[0], -90.0)

    def test_antimeridian_positive(self):
        got = parse("+00+180/")
        assert got is not None
        _approx(got[1], 180.0)

    def test_antimeridian_negative(self):
        got = parse("+00-180/")
        assert got is not None
        _approx(got[1], -180.0)


class TestRejection:
    @pytest.mark.parametrize(
        "value",
        [
            None,
            "",
            "   ",
            "not a coordinate",
            "Edmonton",
            "+",
            "+53",
            "+53.5461",
            "+53.5461-",
            "++53.5461-113.4938/",
            "+91.0-113.0/",
            "+53.0-191.0/",
            "+53.0+191.0/",
            "+1234567.0+010.0/",
            "nan, nan",
            "+inf-000/",
            "53.5461, -113.4938, 668.5, extra",
        ],
    )
    def test_unparseable_returns_none(self, value):
        assert parse(value) is None
        assert parse_point(value) is None

    def test_never_raises_on_bytes(self):
        got = parse(b"+53.5461-113.4938/")
        assert got is not None
        _approx(got[0], 53.5461)
        _approx(got[1], -113.4938)
        assert parse(b"\xff\xfe") is None

    def test_non_string_int_returns_none(self):
        assert parse(42) is None  # type: ignore[arg-type]

    def test_lat_just_inside_is_accepted(self):
        assert parse("+90.0+000.0/") is not None
        assert parse("-90.0+000.0/") is not None

    def test_lat_just_outside_is_rejected(self):
        assert parse("+90.00001+000.0/") is None
        assert parse("-90.00001+000.0/") is None


class TestGeoPoint:
    def test_frozen(self):
        p = parse_point("+53.5461-113.4938/")
        assert p is not None
        with pytest.raises(Exception):
            p.lat = 0.0  # type: ignore[misc]

    def test_alt_none_when_absent(self):
        p = parse_point("+53.5461-113.4938/")
        assert p is not None
        assert p.alt is None

    def test_equality(self):
        a = parse_point("+53.5461-113.4938/")
        b = parse_point("+53.5461-113.4938/")
        assert a == b
        assert a == GeoPoint(lat=a.lat, lon=a.lon, alt=None)


class TestGoldenPathPair:
    EDMONTON = "+53.5461-113.4938/"
    VANCOUVER = "+49.2827-123.1207/"

    def test_cities_are_distinct(self):
        ed = parse(self.EDMONTON)
        van = parse(self.VANCOUVER)
        assert ed is not None and van is not None
        assert abs(ed[0] - van[0]) > 1.0
        assert abs(ed[1] - van[1]) > 1.0

    def test_edmonton_is_in_alberta_box(self):
        lat, lon = parse(self.EDMONTON)
        assert 49.0 <= lat <= 60.0
        assert -120.0 <= lon <= -110.0

    def test_vancouver_is_in_bc_box(self):
        lat, lon = parse(self.VANCOUVER)
        assert 48.0 <= lat <= 50.5
        assert -128.0 <= lon <= -121.0
