"""Hyperlapse compilations: pool selection, ordering, music snapping, slowdown, render."""
import hashlib
import json
import subprocess
from datetime import datetime

import pytest

from app import models
from app.ai import motion, motion_store
from app.db import SessionLocal
from app.edits import broll, compilation, render
from .test_beat_sync import _write_map
from .test_story import _silent_mp3

pytestmark = pytest.mark.usefixtures("cleanup_assets")


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------
def test_snap_to_music_trims_only_and_prefers_downbeats():
    downs = [2.0, 4.0, 6.0, 8.0]
    beats = [0.5 * i for i in range(1, 20)]
    res = compilation.snap_to_music([2.3, 1.9, 2.6], downs, beats, min_clip=0.8)
    assert res["kinds"][0] == "down" and res["lengths"][0] == pytest.approx(2.0)      # 0.3 trimmed
    assert all(n <= o + 1e-9 for n, o in zip(res["lengths"], [2.3, 1.9, 2.6]))         # never lengthened
    t = 0.0
    for length, kind in zip(res["lengths"], res["kinds"]):
        t += length
        pool = downs if kind == "down" else beats
        if kind != "free":
            assert min(abs(t - x) for x in pool) < 1e-3, (t, kind)
    for head, tail in res["trims"]:
        assert 0 <= tail <= compilation.SNAP_TRIM_S + 1e-9 and 0 <= head <= compilation.SNAP_TRIM_S + 1e-9
    # a beat is used when no downbeat is within reach; a clip is never cut below min_clip
    far = compilation.snap_to_music([3.1], [10.0], [3.0], 0.8)
    assert far["kinds"] == ["beat"] and far["lengths"][0] == pytest.approx(3.0)
    tiny = compilation.snap_to_music([1.0], [0.9], [], 0.95)
    assert tiny["lengths"][0] >= 0.95


# ---------------------------------------------------------------------------
# plans
# ---------------------------------------------------------------------------
@pytest.fixture()
def hyperlapses(client, tmp_path_factory):
    d = tmp_path_factory.mktemp("hyper")
    specs = [  # name, duration, daypart, taken_at, colour
        ("a_day", 5.0, "day", datetime(2024, 3, 1, 10), "red"),
        ("b_day", 4.0, "day", datetime(2024, 3, 2, 10), "green"),
        ("b_day_copy", 4.0, "day", datetime(2024, 3, 2, 10), "green"),       # same clip, re-encoded
        ("c_dusk", 6.0, "dusk", datetime(2024, 3, 3, 18), "orange"),
        ("d_night", 5.0, "night", datetime(2024, 3, 4, 23), "blue"),
        ("e_night", 3.0, "night", datetime(2024, 3, 3, 22), "purple"),
        ("tiny_day", 0.5, "day", datetime(2024, 3, 5, 11), "white"),
    ]
    ids = {}
    recs = {}
    with SessionLocal() as db:
        for name, dur, part, taken, color in specs:
            f = d / f"{name}.mp4"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                            f"color=c={color}:s=180x320:r=25:d={dur}", "-pix_fmt", "yuv420p", "-c:v", "libx264",
                            "-preset", "ultrafast", str(f)], check=True)
            a = models.Asset(path=str(f), hash=hashlib.md5(str(f).encode()).hexdigest(), kind="video",
                             width=180, height=320, duration=dur, taken_at=taken, status="indexed")
            db.add(a)
            db.flush()
            ids[name] = a.id
            recs[name] = {"version": motion.VERSION, "fps": 25.0, "is_hyperlapse": True, "hyper_signal": "fps25",
                          "daypart": part, "smoothness": None, "profile": [], "windows": [], "error": None}
        # a normal (non-hyperlapse) day clip must never be compiled
        f = d / "normal.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=180x320:r=30:d=5",
                        "-pix_fmt", "yuv420p", str(f)], check=True)
        n = models.Asset(path=str(f), hash="normal", kind="video", width=180, height=320, duration=5.0,
                         status="indexed")
        db.add(n)
        db.flush()
        ids["normal"] = n.id
        recs["normal"] = {"version": motion.VERSION, "fps": 29.97, "is_hyperlapse": False, "daypart": "day",
                          "smoothness": 0.9, "profile": [0.9] * 5, "windows": [[0, 5, 0.9]], "error": None}
        db.commit()
    for name, rec in recs.items():
        motion_store.save_record(ids[name], rec)
    return ids


def _clips(client, plan_id):
    return client.get(f"/api/edits/plan/{plan_id}").json()["clips"]


def test_day_compilation_uses_all_unique_hyperlapses_in_time_order(client, hyperlapses):
    r = client.post("/api/edits/compilation", json={"daypart": "day", "title": "Day"})
    assert r.status_code == 200, r.text
    plan = r.json()
    ids = hyperlapses
    used = [c["asset_id"] for c in plan["clips"]]
    assert used == [ids["a_day"], ids["b_day"]]                  # copy dropped, tiny (<0.8 s) skipped, normal excluded
    assert plan["clips"][0]["start"] == 0 and plan["clips"][0]["end"] == pytest.approx(5.0, abs=0.01)   # used whole
    b = plan["broll"]
    assert b["natural_seconds"] == pytest.approx(9.0, abs=0.05) and b["clip_count"] == 2
    assert b["duplicates_dropped"] == 1 and b["speed"] == 1.0
    assert any("under 30s" in w for w in b["warnings"])          # reported, not looped
    assert plan["total_duration"] == pytest.approx(9.0, abs=0.05)
    assert all(c["transition"] == "cut" for c in plan["clips"])


def test_night_and_dusk_folding(client, hyperlapses):
    ids = hyperlapses
    night = client.post("/api/edits/compilation", json={"daypart": "night"}).json()
    assert [c["asset_id"] for c in night["clips"]] == [ids["e_night"], ids["d_night"]]    # by taken_at
    day_dusk = client.post("/api/edits/compilation", json={"daypart": "day", "include_dusk_in": "day"}).json()
    assert [c["asset_id"] for c in day_dusk["clips"]] == [ids["a_day"], ids["b_day"], ids["c_dusk"]]
    dusk_only = client.post("/api/edits/compilation", json={"daypart": "dusk"}).json()
    assert [c["asset_id"] for c in dusk_only["clips"]] == [ids["c_dusk"]]
    flow = client.post("/api/edits/compilation", json={"daypart": "day", "include_dusk_in": "day",
                                                       "order": "flow"})
    assert flow.status_code == 200 and len(flow.json()["clips"]) == 3


def test_min_seconds_slows_uniformly_but_never_loops(client, hyperlapses):
    ok = client.post("/api/edits/compilation", json={"daypart": "day", "min_seconds": 12}).json()
    side = client.get(f"/api/edits/{ok['plan_id']}/broll").json()
    assert side["speed"] == pytest.approx(0.75, abs=0.001) and side["duration"] == pytest.approx(12.0, abs=0.05)
    assert [c["asset_id"] for c in ok["clips"]] == [hyperlapses["a_day"], hyperlapses["b_day"]]
    capped = client.post("/api/edits/compilation", json={"daypart": "day", "min_seconds": 30}).json()
    assert capped["broll"]["speed"] == 0.6 and capped["total_duration"] == pytest.approx(9 / 0.6, abs=0.1)
    assert any("looping is not allowed" in w for w in capped["broll"]["warnings"])


def test_compilation_errors(client, hyperlapses, tmp_path):
    assert client.post("/api/edits/compilation", json={"daypart": "dawn"}).status_code == 422
    r = client.post("/api/edits/compilation", json={"daypart": "day", "brand": "nope"})
    assert r.status_code == 422 and "unknown brand" in r.text
    nomap = tmp_path / "plain.mp3"
    _silent_mp3(nomap, 5)
    r = client.post("/api/edits/compilation", json={"daypart": "day", "music_path": str(nomap)})
    assert r.status_code == 422 and "beat map" in r.text
    with SessionLocal() as db:
        db.query(models.MotionAnalysis).delete()
        db.commit()
    r = client.post("/api/edits/compilation", json={"daypart": "day"})
    assert r.status_code == 422 and "motion/analyze" in r.text


def test_music_snapping_trims_ends_and_keeps_lengths_consistent(client, hyperlapses, tmp_path):
    track = tmp_path / "music" / "t.mp3"
    track.parent.mkdir()
    _silent_mp3(track, 60.0)
    _write_map(track, duration=60.0, beats=[round(1.0 + 0.5 * i, 3) for i in range(100)],
               downbeats=[round(1.0 + 2.0 * i, 3) for i in range(25)], best_start_15s=3.0, best_start_30s=3.0, drop_s=40.0)
    r = client.post("/api/edits/compilation", json={"daypart": "day", "include_dusk_in": "day",
                                                    "music_path": str(track), "music_start": 3.0,
                                                    "text_line": "Day *hyperlapse*", "hook_text": "All the days",
                                                    "brand": "albertaweb"})
    assert r.status_code == 200, r.text
    plan = r.json()
    side = client.get(f"/api/edits/{plan['plan_id']}/broll").json()
    assert side["music"]["start"] == 3.0 and side["compilation"] and side["brand"]["id"] == "albertaweb"
    assert side["snap_summary"]["down"] + side["snap_summary"]["beat"] + side["snap_summary"]["free"] == 3
    t = 0.0
    for cut, clip in zip(side["cuts"], plan["clips"]):
        assert cut["start"] == pytest.approx(t, abs=1e-3)
        assert cut["slot"] == pytest.approx(clip["end"] - clip["start"], abs=1e-3)
        t += cut["slot"]
        assert clip["start"] >= 0 and clip["end"] <= 6.0 + 1e-6
    originals = [5.0, 4.0, 6.0]
    for clip, o in zip(plan["clips"], originals):
        assert o - (clip["end"] - clip["start"]) <= 2 * compilation.SNAP_TRIM_S + 1e-6   # only a little trimmed
    assert side["duration"] == pytest.approx(t, abs=1e-3) and side["caption_beats"]
    # track too short for the footage: refuse instead of looping
    short = tmp_path / "music" / "short.mp3"
    _silent_mp3(short, 8.0)
    _write_map(short, duration=8.0, beats=[1.0, 1.5, 2.0], downbeats=[1.0, 3.0, 5.0], best_start_15s=1.0, best_start_30s=1.0)
    r = client.post("/api/edits/compilation", json={"daypart": "day", "include_dusk_in": "day", "music_path": str(short)})
    assert r.status_code == 422 and "looping is not allowed" in r.text


def test_compilation_renders_with_slowdown_and_end_card(client, hyperlapses):
    r = client.post("/api/edits/compilation", json={"daypart": "day", "min_seconds": 12, "brand": "buildwithdb",
                                                    "hook_text": "All the day hyperlapses", "text_line": "Golden *hours*"})
    plan = r.json()
    client.post(f"/api/edits/plan/{plan['plan_id']}/approve")
    out = render.render_plan(plan["plan_id"], width=180, height=320)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", out],
                               capture_output=True, text=True).stdout)
    assert dur == pytest.approx(plan["total_duration"] + 1.8, abs=0.4)         # slowed length + end card
    streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", out],
                             capture_output=True, text=True).stdout.split()
    assert sorted(streams) == ["audio", "video"]


def test_render_rejects_compilation_whose_clips_drifted(client, hyperlapses):
    plan = client.post("/api/edits/compilation", json={"daypart": "day", "min_seconds": 12}).json()
    with SessionLocal() as db:
        c = db.query(models.EditClip).filter_by(plan_id=plan["plan_id"]).first()
        c.end = c.end - 1.0
        db.commit()
    client.post(f"/api/edits/plan/{plan['plan_id']}/approve")
    with pytest.raises(render.RenderError, match="no longer match"):
        render.render_plan(plan["plan_id"], width=180, height=320)
