"""Motion-graphic story cards: spec validation, frames, video, story + render integration."""
import json
import subprocess

import numpy as np
import pytest
from PIL import ImageChops

from app import config
from app.edits import cards, kinetic, planner, render, story
from .test_beat_sync import _write_map, long_assets  # noqa: F401
from .test_story import SCRIPT, _capture_ffmpeg, _silent_mp3, fake_tts  # noqa: F401

pytestmark = pytest.mark.usefixtures("cleanup_assets")
ZONES = {"top": 0.12, "bottom": 0.22, "left": 0.06, "right": 0.14}
SPECS = {
    "headline": {"kind": "headline", "text": "Critical PHP flaws let attackers take over servers",
                 "sub": "Severity 9.2 / 10 · Source: BankInfoSecurity"},
    "stat": {"kind": "stat", "text": "7 in 10", "sub": "websites run PHP, including WordPress"},
    "alert": {"kind": "alert", "text": "UPDATE NOW OR GO OFFLINE", "sub": "Advice from UK NCSC and US NSA · Oct 21"},
    "terminal": {"kind": "terminal", "text": "exploit attempts",
                 "sub": "counter 1,000 → 10,000 → 100,000 → 1,000,000+"},
    "timeline": {"kind": "timeline", "text": "Oct 17 attacks begin · Oct 20 details leak · Oct 21 patch released",
                 "sub": "PHP2Shell timeline"},
}


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------
def test_validate_card_derives_fields():
    tl = cards.validate_card(SPECS["timeline"])
    assert tl["items"] == ["Oct 17 attacks begin", "Oct 20 details leak", "Oct 21 patch released"]
    term = cards.validate_card(SPECS["terminal"])
    assert term["counter"] == ["1,000", "10,000", "100,000", "1,000,000+"] and term["items"] == []
    checks = cards.validate_card({"kind": "terminal", "text": "system check", "sub": "✔ backups ✔ updates ✔ alerts"})
    assert checks["items"] == ["backups", "updates", "alerts"] and checks["counter"] == []
    lst = cards.validate_card({"kind": "alert", "text": "CHECKLIST", "sub": "Updated PHP · Patched plugins · Secure hosting · Tested backups"})
    assert len(lst["items"]) == 4
    two = cards.validate_card(SPECS["alert"])
    assert two["items"] == []                                 # two parts = plain text, not a checklist
    h = cards.validate_card(SPECS["headline"])
    assert h["date"] and h["date"] == h["date"].upper()
    assert cards.validate_card({**SPECS["stat"], "kind": "STAT"})["kind"] == "stat"


@pytest.mark.parametrize("bad", [
    None, "x", {"kind": "poster", "text": "x"}, {"kind": "stat"}, {"kind": "stat", "text": "x" * 401},
    {"kind": "timeline", "text": "only one step"}, {"kind": "timeline", "text": " · ".join("abcdef")},
    {"kind": "terminal", "text": "t", "sub": "counter 1"},
])
def test_validate_card_rejects(bad):
    with pytest.raises(cards.CardError):
        cards.validate_card(bad)


def test_validate_beat_visuals():
    card = {"type": "card", "card": SPECS["stat"]}
    out = cards.validate_beat_visuals([None, card], 4)
    assert len(out) == 4 and out[0] is None and out[1]["card"]["kind"] == "stat" and out[2:] == [None, None]
    assert cards.validate_beat_visuals(None, 3) == [None] * 3
    with pytest.raises(cards.CardError, match="only 2 beats"):
        cards.validate_beat_visuals([None, None, card], 2)
    with pytest.raises(cards.CardError):
        cards.validate_beat_visuals([{"type": "video"}], 2)
    with pytest.raises(cards.CardError):
        cards.validate_beat_visuals([{"type": "card", "card": {"kind": "nope", "text": "x"}}], 2)


# ---------------------------------------------------------------------------
# frames
# ---------------------------------------------------------------------------
def _diff(a, b):
    return np.asarray(ImageChops.difference(a, b).convert("L"))


@pytest.mark.parametrize("kind", list(SPECS))
def test_card_frames_per_kind(kind):
    spec = cards.validate_card(SPECS[kind])
    w, h = 540, 960
    r = cards.CardRenderer(spec, w, h, 6.0, 30, ZONES, seed=2)
    blank = r.background(0.0)
    late = r.frame(4.5).copy()
    early = r.frame(0.1).copy()
    assert late.size == (w, h) and late.mode == "RGB"
    content = _diff(late, r.background(4.5)) > 70                       # pixels the card drew
    assert content.sum() > 1500, f"{kind}: nothing drawn"
    assert content[:int(0.10 * h)].sum() == 0 or kind in ("headline",), kind     # headline pill sits at 12%+
    sx, sy, sw, sh = kinetic._safe_rect(w, h, ZONES)
    assert content[:, sx + sw + 4:].sum() == 0                          # right UI margin stays clear
    assert content[int(0.78 * h):].sum() == 0                           # bottom UI zone stays clear
    band = content[int(0.46 * h):int(0.56 * h), int(0.30 * w):int(0.86 * w)]
    assert band.sum() == 0, f"{kind}: card content in the caption band"
    assert _diff(early, late).mean() > 0.5                              # it animates
    assert _diff(r.frame(0.1), r.frame(0.11)).mean() > 0                # even idle frames move (grain/drift)
    del blank


def test_stat_counts_up_and_dots_fill():
    spec = cards.validate_card(SPECS["stat"])
    r = cards.CardRenderer(spec, 540, 960, 6.0, 30, ZONES)
    assert r._count_text("7 in 10", 0.0) == "0 in 0" and r._count_text("7 in 10", 5.0) == "7 in 10"
    assert r._count_text("1,250 users", 5.0) == "1,250 users" and r._count_text("9.2 / 10", 5.0) == "9.2 / 10"
    mid = r._count_text("7 in 10", 0.4)
    assert 0 <= int(mid.split()[0]) <= 7


def test_reveal_delay_holds_back_content():
    spec = cards.validate_card(SPECS["headline"])
    r = cards.CardRenderer(spec, 540, 960, 6.0, 30, ZONES, reveal_delay=1.4)
    assert (_diff(r.frame(1.0), r.background(1.0)) > 70).sum() == 0             # hook time: background only
    assert (_diff(r.frame(3.5), r.background(3.5)) > 70).sum() > 500


def test_landscape_card_renders():
    r = cards.CardRenderer(cards.validate_card(SPECS["terminal"]), 960, 540, 4.0, 30, None)
    img = r.frame(3.0)
    assert img.size == (960, 540) and (_diff(img, r.background(3.0)) > 70).sum() > 1000


def test_card_video_has_requested_size_and_duration(tmp_path):
    out = cards.render_card_video(cards.validate_card(SPECS["alert"]), 360, 640, 2.0, tmp_path / "c.mp4",
                                  safe_zones=ZONES)
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format",
                                      str(out)], capture_output=True, text=True).stdout)
    v = info["streams"][0]
    assert (v["width"], v["height"]) == (360, 640) and v["r_frame_rate"] == "30/1"
    assert float(info["format"]["duration"]) == pytest.approx(2.0, abs=0.1)


# ---------------------------------------------------------------------------
# story + render
# ---------------------------------------------------------------------------
def _visuals():
    return [{"type": "card", "card": SPECS["stat"]}, None]


def test_story_stores_card_visuals_and_validates(client, fake_tts, long_assets):  # noqa: F811
    plan = story.create_story_plan(SCRIPT, search_fn=lambda db, q, limit: long_assets, cloud_approved=True,
                                   voice_consent=True, beat_visuals=_visuals())
    sc = story.get_story(plan["plan_id"])
    assert sc["beats"][0]["visual"]["card"]["kind"] == "stat" and sc["beats"][1]["visual"] is None
    assert sc["beats"][0]["clips"]                                       # underlay clip kept for exports / sync
    planner.validate_story_sync(__import__("app.db", fromlist=["x"]).SessionLocal().get(
        __import__("app.models", fromlist=["x"]).EditPlan, plan["plan_id"]), sc)
    with pytest.raises(story.StoryError, match="only 2 beats"):
        story.validate_story_options(SCRIPT, None, None, None, None, [None, None, _visuals()[0]])
    with pytest.raises(story.StoryError, match="card kind"):
        story.validate_story_options(SCRIPT, None, None, None, None, [{"type": "card", "card": {"kind": "x", "text": "y"}}])


def test_story_route_rejects_bad_visuals(client, monkeypatch):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "x")
    body = {"script": SCRIPT, "cloud_approved": True, "voice_consent": True}
    r = client.post("/api/edits/story", json={**body, "beat_visuals": [{"type": "card", "card": {"kind": "nope", "text": "x"}}]})
    assert r.status_code == 422 and "card kind" in r.text
    assert client.post("/api/edits/story", json={**body, "beat_visuals": [None, None, None]}).status_code == 422


def _approved_with_cards(client, long_assets):  # noqa: F811
    plan = story.create_story_plan(SCRIPT, search_fn=lambda db, q, limit: long_assets, cloud_approved=True,
                                   voice_consent=True, beat_visuals=_visuals())
    assert client.post(f"/api/edits/plan/{plan['plan_id']}/approve").status_code == 200
    return plan, story.get_story(plan["plan_id"])


def test_render_with_card_beat_keeps_voice_sync(client, fake_tts, long_assets, monkeypatch):  # noqa: F811
    plan, sc = _approved_with_cards(client, long_assets)
    seen = _capture_ffmpeg(monkeypatch)
    out = render.render_plan(plan["plan_id"], ratio="9:16", width=270, height=480)
    cmd = seen[-1]
    graph = cmd[cmd.index("-filter_complex") + 1]
    card_inputs = [c for c in cmd if c.endswith("card_1.mp4")]
    assert card_inputs, "the card video is an ffmpeg input"
    assert not any(c.endswith("card_2.mp4") for c in cmd)               # beat 2 keeps its footage
    assert "concat=n=" in graph and "xfade" not in graph
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", out],
                               capture_output=True, text=True).stdout)
    assert dur == pytest.approx(sc["duration"], abs=0.3)                # card replaces footage, length unchanged
    assert not (config.RENDER_TMP_DIR / f"emph_{plan['plan_id']}").exists()      # temp card files cleaned up
    # the card beat has no big emphasis words (the card owns the upper frame)
    events = kinetic.plan_events(sc["beats"])
    assert all(e["start"] >= sc["beats"][1]["start"] - 1e-6 for e in events["emphasis"])


def test_card_beat_with_music_beat_sync_and_effects(client, fake_tts, long_assets, tmp_path, monkeypatch):  # noqa: F811
    plan, sc = _approved_with_cards(client, long_assets)
    track = tmp_path / "beat.mp3"
    _silent_mp3(track, 20.0)
    slot = sc["beats"][0]["slot"]
    _write_map(track, beats=[0.0, slot - 0.1, 2 * slot], downbeats=[0.0], best_start_15s=0.0, best_start_30s=0.0,
               duration=20.0)
    seen = _capture_ffmpeg(monkeypatch)
    out = render.render_plan(plan["plan_id"], ratio="9:16", width=270, height=480, music_path=str(track),
                             effects=True, captions=True)
    graph = seen[-1][seen[-1].index("-filter_complex") + 1]
    assert "rgbashift" in graph and "eq=contrast" in graph               # grade + glitch still apply to cards
    # the cut after the card moved onto the beat by trimming card/footage in/out, not the timeline
    assert f"trim=start=0.000:end={slot - 0.1:.3f}" in graph
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", out],
                               capture_output=True, text=True).stdout)
    assert dur == pytest.approx(sc["duration"], abs=0.3)


def test_swap_broll_on_a_card_beat_restores_footage(client, fake_tts, long_assets):  # noqa: F811
    plan, _sc = _approved_with_cards(client, long_assets)
    planner.swap_broll(plan["plan_id"], 1, long_assets[1])
    assert story.get_story(plan["plan_id"])["beats"][0].get("visual") is None
