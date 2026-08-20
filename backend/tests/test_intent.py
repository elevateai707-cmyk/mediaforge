"""Intent parser tests for the Edmonton golden-path prompt."""
from app.edits.intent import parse_intent


def test_edmonton_tiktok_golden_path():
    parsed = parse_intent(
        "make a highlight reel for tiktok of my trip to edmonton 9:16"
    )
    assert parsed.place == "Edmonton"
    assert parsed.ratio == "9:16"
    assert parsed.platform == "tiktok"
    assert parsed.duration_s == 30
    assert parsed.kind == "highlight"
    assert parsed.place_lat is not None
    assert abs(parsed.place_lat - 53.55) < 0.05


def test_vancouver_recap_60s():
    parsed = parse_intent("60-second vancouver recap 16:9")
    assert parsed.place == "Vancouver"
    assert parsed.duration_s == 60
    assert parsed.ratio == "16:9"
    assert parsed.kind == "recap"
