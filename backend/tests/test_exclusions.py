"""Footage exclusions: never select, swap in or render excluded footage."""
import hashlib
import subprocess

import pytest

from app import models
from app.db import SessionLocal
from app.edits import broll, compilation, exclusions, planner, render, story
from .test_beat_sync import long_assets  # noqa: F401
from .test_story import SCRIPT, fake_tts  # noqa: F401

pytestmark = pytest.mark.usefixtures("cleanup_assets")
INF = float("inf")


@pytest.fixture(autouse=True)
def _clear_exclusions(client):
    yield
    with SessionLocal() as db:
        db.query(models.FootageExclusion).delete()
        db.commit()


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------
def test_ranges_merge_subtract_and_free_start():
    assert exclusions._merge([(7, 9), (1, 3), (2, 4)]) == [(1, 4), (7, 9)]
    ex = [(7.0, INF)]
    assert exclusions.overlaps(ex, 12, 18) and exclusions.overlaps(ex, 6.5, 7.5) and not exclusions.overlaps(ex, 0, 7.0)
    assert exclusions.subtract([[0.0, 20.0, 0.9]], ex) == [[0.0, 7.0, 0.9]]
    assert exclusions.subtract([[0, 20, 0.9]], [(5, 8)]) == [[0, 5, 0.9], [8, 20, 0.9]]
    assert exclusions.subtract([[0, 3, 0.9]], [(0.5, 2.9)]) == []                     # leftovers < 1 s dropped
    assert exclusions.first_free_start(20.0, 4.0, [(0, 6)]) == 6.0
    assert exclusions.first_free_start(20.0, 4.0, [(0, 17)]) is None
    assert exclusions.is_whole([(0.0, INF)]) and not exclusions.is_whole([(7.0, INF)])


def test_add_validates_and_stores(client):
    with SessionLocal() as db:
        a = models.Asset(path="/x/ex.mp4", hash="exh1", kind="video", duration=20.0, status="indexed")
        db.add(a)
        db.commit()
        aid = a.id
    out = exclusions.add(aid, [[7, None], [1, 2]], reason="private house")
    assert [(r["start"], r["end"]) for r in out["ranges"]] == [(1.0, 2.0), (7.0, None)] and not out["whole_asset"]
    assert exclusions.add(aid, whole=True)["whole_asset"]
    for bad in ([[5, 2]], [[-1, 3]], [["a", 3]], [], None):
        with pytest.raises(exclusions.ExclusionError):
            exclusions.add(aid, bad)
    with pytest.raises(LookupError):
        exclusions.add(999999, whole=True)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def test_exclusion_api_roundtrip(client, long_assets):  # noqa: F811
    aid = long_assets[0]
    r = client.post(f"/api/assets/{aid}/exclude", json={"ranges": [[7, None]], "reason": "private house, people"})
    assert r.status_code == 200, r.text
    assert r.json()["ranges"][0]["reason"] == "private house, people"
    listing = client.get("/api/assets/excluded").json()["excluded"]
    assert [e["asset_id"] for e in listing] == [aid] and listing[0]["ranges"][0]["end"] is None
    asset = client.get("/api/assets", params={"path_prefix": listing[0]["path"]}).json()["items"][0]
    assert asset["excluded"] == [{"start": 7.0, "end": None, "reason": "private house, people"}]
    assert client.post(f"/api/assets/{aid}/exclude", json={}).status_code == 422
    assert client.post(f"/api/assets/{aid}/exclude", json={"ranges": [[3, 1]]}).status_code == 422
    assert client.post("/api/assets/99999999/exclude", json={"all": True}).status_code == 404
    d = client.delete(f"/api/assets/{aid}/exclude").json()
    assert d["removed"] == 1 and client.get("/api/assets/excluded").json()["excluded"] == []


def test_asset_listing_reports_free_smooth_windows(client, long_assets):  # noqa: F811
    from app.ai import motion_store
    aid = long_assets[0]
    motion_store.save_record(aid, {"version": 1, "fps": 30.0, "is_hyperlapse": False, "daypart": "day",
                                   "smoothness": 0.9, "profile": [0.95] * 10, "windows": [[0, 10, 0.95]], "error": None})
    exclusions.add(aid, [[6, None]])
    with SessionLocal() as db:
        path = db.get(models.Asset, aid).path
    item = client.get("/api/assets", params={"path_prefix": path, "min_smoothness": 0.85}).json()["items"][0]
    assert item["motion"]["windows_min"] == [[0.0, 6.0, 0.95]]


# ---------------------------------------------------------------------------
# selection paths
# ---------------------------------------------------------------------------
def test_story_never_picks_excluded_ranges(client, fake_tts, long_assets):  # noqa: F811
    a1, a2 = long_assets
    exclusions.add(a1, [[0, 6]], reason="people")
    exclusions.add(a2, whole=True, reason="overused")
    plan = story.create_story_plan(SCRIPT, search_fn=lambda db, q, limit: long_assets,
                                   cloud_approved=True, voice_consent=True)
    assert {c["asset_id"] for c in plan["clips"]} == {a1}
    for c in plan["clips"]:
        assert c["start"] >= 6.0 - 1e-6, c                      # only the free part of asset 1
    with SessionLocal() as db:
        pool, windows = story.filtered_video_pool(db, None, None)
        assert a2 not in pool and windows[a1] == [[6.0, 10.0, 0.0]]


def test_broll_segments_avoid_exclusions(client, long_assets):  # noqa: F811
    a1, a2 = long_assets
    exclusions.add(a1, [[2, 6]])
    exclusions.add(a2, [[0, 3]])
    plan = broll.create_broll_plan(silent=True, target_seconds=30,
                                   broll_filter={"asset_ids": long_assets, "min_smoothness": None, "hyperlapse": None})
    side = broll.get_broll(plan["plan_id"])
    for cut in side["cuts"]:
        for c in cut["clips"]:
            lo, hi = {a1: [(2, 6)], a2: [(0, 3)]}[c["asset_id"]][0]
            assert c["end"] <= lo + 1e-6 or c["start"] >= hi - 1e-6, (c, lo, hi)


def _plan_with_asset(client, long_assets, n_beats_script=SCRIPT):  # noqa: F811
    plan = story.create_story_plan(n_beats_script, search_fn=lambda db, q, limit: long_assets,
                                   cloud_approved=True, voice_consent=True)
    return plan


def test_swap_broll_respects_exclusions_and_start(client, fake_tts, long_assets):  # noqa: F811
    a1, a2 = long_assets
    plan = _plan_with_asset(client, [a1])
    pid = plan["plan_id"]
    exclusions.add(a2, [[0, 5]], reason="people at the door")
    out = planner.swap_broll(pid, 1, a2)
    c = out["clips"][0]
    assert c["asset_id"] == a2 and c["start"] >= 5.0 - 1e-6                 # earliest free start, not 0
    with pytest.raises(ValueError, match="excluded"):
        planner.swap_broll(pid, 2, a2, start=3.0)
    ok = planner.swap_broll(pid, 2, a2, start=5.5)
    assert ok["clips"][1]["start"] == 5.5
    exclusions.add(a2, whole=True)
    with pytest.raises(ValueError, match="excluded"):
        planner.swap_broll(pid, 1, a2)
    # without exclusions the old behaviour is unchanged (starts at 0)
    exclusions.remove(a2)
    assert planner.swap_broll(pid, 1, a2)["clips"][0]["start"] == 0.0
    via_api = client.post(f"/api/edits/plan/{pid}/swap-broll", json={"beat_index": 1, "asset_id": a2, "start": 1.0})
    assert via_api.status_code == 200 and via_api.json()["clips"][0]["start"] == 1.0


def test_render_refuses_plans_that_use_excluded_footage(client, fake_tts, long_assets):  # noqa: F811
    a1, a2 = long_assets
    plan = _plan_with_asset(client, [a1])
    pid = plan["plan_id"]
    client.post(f"/api/edits/plan/{pid}/approve")
    exclusions.add(a1, [[0, 4]], reason="private house")
    with pytest.raises(render.RenderError, match="excluded footage.*private house"):
        render.render_plan(pid, ratio="9:16", width=180, height=320)
    # swap the offending beats to the other clip, approve again, render works
    for beat in (1, 2):
        planner.swap_broll(pid, beat, a2)
    client.post(f"/api/edits/plan/{pid}/approve")
    out = render.render_plan(pid, ratio="9:16", width=180, height=320, captions=False, effects=False)
    assert out.endswith(".mp4")


def test_compilation_skips_excluded_assets_and_trims_to_free_piece(client, tmp_path):
    from app.ai import motion, motion_store
    from datetime import datetime
    ids = {}
    with SessionLocal() as db:
        for name, dur in (("h1", 6.0), ("h2", 6.0), ("h3", 5.0)):
            f = tmp_path / f"{name}.mp4"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=red:s=180x320:r=25:d={dur}",
                            "-pix_fmt", "yuv420p", str(f)], check=True)
            a = models.Asset(path=str(f), hash=hashlib.md5(str(f).encode()).hexdigest(), kind="video", width=180,
                             height=320, duration=dur, taken_at=datetime(2024, 1, int(name[1]) + 1), status="indexed")
            db.add(a)
            db.flush()
            ids[name] = a.id
        db.commit()
    for aid in ids.values():
        motion_store.save_record(aid, {"version": motion.VERSION, "fps": 25.0, "is_hyperlapse": True,
                                       "hyper_signal": "fps25", "daypart": "day", "smoothness": None,
                                       "profile": [], "windows": [], "error": None})
    exclusions.add(ids["h1"], whole=True)
    exclusions.add(ids["h2"], [[0, 4]])
    plan = compilation.create_compilation("day")
    by_asset = {c["asset_id"]: c for c in plan["clips"]}
    assert ids["h1"] not in by_asset
    assert by_asset[ids["h2"]]["start"] >= 4.0 - 1e-6 and by_asset[ids["h2"]]["end"] == pytest.approx(6.0, abs=0.05)
    assert ids["h3"] in by_asset
