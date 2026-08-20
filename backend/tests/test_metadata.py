"""iPhone / ISO6709 GPS extraction from exiftool rows and ffprobe tags."""
from __future__ import annotations

from app.ingest.metadata import gps_from_exif_row, gps_from_ffprobe


def test_exif_keys_gpscoordinates_iso6709():
    pt = gps_from_exif_row({"Keys:GPSCoordinates": "+53.5461-113.4938/"})
    assert pt is not None
    assert abs(pt.lat - 53.5461) < 1e-4
    assert abs(pt.lon - (-113.4938)) < 1e-4


def test_exif_apple_iso6709_tag():
    row = {"Com.apple.quicktime.location.ISO6709": "+49.2827-123.1207/"}
    pt = gps_from_exif_row(row)
    assert pt is not None
    assert abs(pt.lat - 49.2827) < 1e-4
    assert abs(pt.lon - (-123.1207)) < 1e-4


def test_exif_numeric_lat_lon():
    pt = gps_from_exif_row({
        "GPSLatitude": 53.5461,
        "GPSLongitude": -113.4938,
        "GPSAltitude": 670,
    })
    assert pt is not None
    assert abs(pt.lat - 53.5461) < 1e-4
    assert abs(pt.lon - (-113.4938)) < 1e-4
    assert pt.alt == 670


def test_ffprobe_format_location_tag():
    data = {
        "format": {"tags": {"location": "+53.5461-113.4938/"}},
        "streams": [],
    }
    pt = gps_from_ffprobe(data)
    assert pt is not None
    assert abs(pt.lat - 53.5461) < 1e-4
    assert abs(pt.lon - (-113.4938)) < 1e-4


def test_ffprobe_stream_iso6709():
    data = {
        "format": {"tags": {}},
        "streams": [{
            "codec_type": "video",
            "tags": {"com.apple.quicktime.location.ISO6709": "53.5461, -113.4938"},
        }],
    }
    pt = gps_from_ffprobe(data)
    assert pt is not None
    assert abs(pt.lat - 53.5461) < 1e-4
    assert abs(pt.lon - (-113.4938)) < 1e-4
