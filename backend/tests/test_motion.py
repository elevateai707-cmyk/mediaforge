"""Drone motion analysis: smoothness metric, hyperlapse / daypart detection, filters, API."""
import hashlib
import json
import subprocess

import numpy as np
import pytest

from app import models
from app.ai import motion, motion_store
from app.db import SessionLocal
from app.edits import story

pytestmark = pytest.mark.usefixtures("cleanup_assets")

W, H = 640, 360


def _ff(*args):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


@pytest.fixture(scope="module")
def texture(tmp_path_factory):
    """A large static texture the synthetic 'camera' pans over."""
    d = tmp_path_factory.mktemp("tex")
    p = d / "tex.png"
    _ff("-f", "lavfi", "-i", "nullsrc=s=2400x1400", "-vf",
        "geq=lum='128+55*sin(X/23)+45*sin(Y/31)+30*sin((X+Y)/13)+20*sin(X*Y/9000)':cb=128:cr=128",
        "-frames:v", "1", str(p))
    return p


def _pan(texture, out, x_expr, y_expr="300", seconds=8, fps=30, extra=""):
    _ff("-loop", "1", "-framerate", str(fps), "-i", str(texture), "-t", str(seconds), "-vf",
        f"crop={W}:{H}:x='{x_expr}':y='{y_expr}'{extra},format=yuv420p", "-c:v", "libx264",
        "-preset", "ultrafast", str(out))
    return out


def _analyse(path, fps=None):
    return motion.analyze_video(str(path), W, H, 8.0, fps=fps)


# a smooth ease-in/ease-out pan vs the same pan flown with stick jabs
SMOOTH_X = "200+240*(1-cos(PI*t/8))/2*2"
JITTER_X = "500+80*(2*gt(sin(2*PI*t/1.2),0)-1)*abs(sin(PI*mod(t,0.6)/0.6))"


def test_smooth_pan_scores_high_and_jittery_pan_low(texture, tmp_path):
    smooth = _analyse(_pan(texture, tmp_path / "smooth.mp4", SMOOTH_X))
    jitter = _analyse(_pan(texture, tmp_path / "jitter.mp4", JITTER_X))
    assert smooth["error"] is None and jitter["error"] is None
    assert smooth["smoothness"] >= 0.85, smooth
    assert jitter["smoothness"] <= 0.45, jitter
    assert smooth["smoothness"] - jitter["smoothness"] >= 0.3
    assert jitter["reversals_per_s"] > smooth["reversals_per_s"]
    assert not smooth["is_hyperlapse"] and not jitter["is_hyperlapse"]
    assert smooth["windows"] and smooth["windows"][0][1] - smooth["windows"][0][0] >= motion.MIN_WINDOW_S


def test_steady_hover_counts_as_smooth(texture, tmp_path):
    hover = _analyse(_pan(texture, tmp_path / "hover.mp4", "600", "400"))
    assert hover["smoothness"] >= 0.8


def test_long_clip_with_one_rough_moment_keeps_smooth_windows(texture, tmp_path):
    # smooth, a violent jab around t=6..7, smooth again
    x = "if(between(t,6,7),400+90*sin(2*PI*1.5*(t-6)),400+25*t)"
    rec = motion.analyze_video(str(_pan(texture, tmp_path / "mixed.mp4", x, seconds=14)), W, H, 14.0)
    wins = rec["windows"]
    assert wins and all(b - a >= motion.MIN_WINDOW_S for a, b, _ in wins)
    assert all(not (a < 6.3 and b > 6.7) for a, b, _ in wins)          # no window spans the jab
    assert rec["smoothness"] < 0.9


def test_hyperlapse_signals(texture, tmp_path):
    clip = _pan(texture, tmp_path / "n25.mp4", SMOOTH_X, fps=25)
    rec = _analyse(clip)
    assert rec["is_hyperlapse"] and rec["hyper_signal"] == "fps25" and rec["fps"] == 25.0
    assert rec["smoothness"] is None                                  # exempt from the smoothness score
    # 30 fps footage whose consecutive frames are unrelated jumps = temporal discontinuity
    jumpy = _pan(texture, tmp_path / "jumpy.mp4", "100+1500*random(1)", "100+800*random(2)", fps=30)
    r2 = _analyse(jumpy)
    assert r2["is_hyperlapse"] and r2["hyper_signal"] == "discontinuity"
    normal = _analyse(_pan(texture, tmp_path / "n30.mp4", SMOOTH_X, fps=30))
    assert not normal["is_hyperlapse"] and normal["hyper_signal"] is None
    assert motion.hyperlapse_signals(25.0, 0.001, 0.0) == (True, "fps25")
    assert motion.hyperlapse_signals(29.97, 0.04, 0.0) == (True, "discontinuity")
    assert motion.hyperlapse_signals(25.0, 0.04, 0.0) == (True, "fps25+discontinuity")
    assert motion.hyperlapse_signals(29.97, 0.001, 0.0) == (False, None)


def test_daypart_classification():
    assert motion.classify_daypart({"luma_mean": 30, "shadow_frac": 0.8}) == "night"
    assert motion.classify_daypart({"luma_mean": 85, "shadow_frac": 0.2}) == "dusk"
    assert motion.classify_daypart({"luma_mean": 130, "shadow_frac": 0.05}) == "day"
    assert motion.classify_daypart({}) is None
    frames = np.full((4, 36, 64), 20, dtype=np.uint8)
    frames[:, 5:8, 5:8] = 250                                           # city lights on dark
    st = motion.luma_stats(frames)
    assert motion.classify_daypart(st) == "night" and st["highlight_frac"] > 0


def test_daypart_from_real_files(tmp_path):
    for name, expr, want in (("night", "color=c=0x101010", "night"), ("day", "color=c=0xB0C0D0", "day"),
                             ("dusk", "color=c=0x485060", "dusk")):
        f = tmp_path / f"{name}.mp4"
        _ff("-f", "lavfi", "-i", f"{expr}:s={W}x{H}:r=30:d=4", "-pix_fmt", "yuv420p", str(f))
        assert motion.analyze_video(str(f), W, H, 4.0)["daypart"] == want


def test_smoothness_math_on_known_paths():
    n = 120
    t = np.arange(n) / 10.0
    ok = np.ones(n, dtype=bool)
    zero = np.zeros(n)
    steady = motion.smoothness_from_steps({"dx": np.full(n, 0.01), "dy": zero, "rot": zero, "ds": zero, "ok": ok})
    sawtooth = motion.smoothness_from_steps({"dx": 0.02 * np.sign(np.sin(2 * np.pi * t / 1.0)), "dy": zero,
                                             "rot": zero, "ds": zero, "ok": ok})
    assert steady["smoothness"] > 0.95 and sawtooth["smoothness"] < 0.3
    assert motion.smoothness_from_steps({"dx": zero[:5], "dy": zero[:5], "rot": zero[:5], "ds": zero[:5],
                                         "ok": ok[:5]})["smoothness"] is None       # too short
    assert motion.windows_from_profile([0.9] * 5 + [0.2] * 2 + [0.8] * 3, 0.6) == [[0.0, 5.0, 0.9], [7.0, 10.0, 0.8]]
    assert motion.windows_from_profile([0.9, 0.9], 0.6) == []


# ---------------------------------------------------------------------------
# storage, filters, API
# ---------------------------------------------------------------------------
def _asset(db, path, dur, **kw):
    a = models.Asset(path=str(path), hash=hashlib.md5(str(path).encode()).hexdigest(), kind="video",
                     width=W, height=H, duration=dur, status="indexed", **kw)
    db.add(a)
    db.flush()
    return a


def _rec(**kw):
    base = {"version": motion.VERSION, "fps": 29.97, "is_hyperlapse": False, "hyper_signal": None,
            "daypart": "day", "smoothness": 0.8, "profile": [0.9] * 20, "windows": [[0, 20, 0.9]], "error": None}
    base.update(kw)
    return base


def test_filters_use_motion_rows_and_windows(client, tmp_path):
    recs = {}
    with SessionLocal() as db:
        ids = {}
        for name, rec in {
            "smooth": _rec(),
            "rough_but_with_window": _rec(smoothness=0.4, profile=[0.2] * 4 + [0.9] * 6 + [0.2] * 4),
            "jerky": _rec(smoothness=0.2, profile=[0.2] * 14),
            "hyper_night": _rec(is_hyperlapse=True, hyper_signal="fps25", daypart="night", smoothness=None,
                                profile=[], windows=[]),
            "dusk": _rec(daypart="dusk"),
        }.items():
            f = tmp_path / f"{name}.mp4"
            f.write_bytes(b"x")
            a = _asset(db, f, 14.0)
            ids[name] = a.id
            recs[name] = rec
        unanalysed = tmp_path / "plain.mp4"
        unanalysed.write_bytes(b"x")
        ids["plain"] = _asset(db, unanalysed, 14.0).id
        db.commit()
        for name, rec in recs.items():
            motion_store.save_record(ids[name], rec)
        pool = lambda flt: story.filtered_video_pool(db, None, {"path_prefixes": [str(tmp_path) + "/"], **flt})  # noqa: E731
        rows, wins = pool({"min_smoothness": 0.6})
        # unanalysed excluded; hyperlapses are exempt from the smoothness rule
        assert set(rows) == {ids["smooth"], ids["rough_but_with_window"], ids["dusk"], ids["hyper_night"]}
        assert set(pool({"min_smoothness": 0.6, "hyperlapse": False})[0]) == {
            ids["smooth"], ids["rough_but_with_window"], ids["dusk"]}
        assert wins[ids["rough_but_with_window"]] == [[4.0, 10.0, 0.9]]                    # only the smooth stretch
        assert set(pool({"min_smoothness": 0.95, "hyperlapse": False})[0]) == set()
        assert set(pool({"hyperlapse": True})[0]) == {ids["hyper_night"]}
        assert ids["hyper_night"] not in pool({"hyperlapse": False})[0] and ids["plain"] in pool({"hyperlapse": False})[0]
        assert set(pool({"hyperlapse": True, "daypart": ["night"]})[0]) == {ids["hyper_night"]}
        assert set(pool({"daypart": ["dusk", "day"], "hyperlapse": False})[0]) == {
            ids["smooth"], ids["rough_but_with_window"], ids["jerky"], ids["dusk"]}
    assert story.clean_broll_filter({"min_smoothness": 0.5, "hyperlapse": False, "daypart": "night"}) == {
        "min_smoothness": 0.5, "hyperlapse": False, "daypart": ["night"]}
    for bad in ({"min_smoothness": 2}, {"daypart": ["noon"]}):
        with pytest.raises(story.StoryError):
            story.clean_broll_filter(bad)


def test_assets_api_filters_and_motion_fields(client, tmp_path):
    recs = {}
    with SessionLocal() as db:
        out = {}
        for name, rec in {"s": _rec(smoothness=0.9), "j": _rec(smoothness=0.2),
                          "h": _rec(is_hyperlapse=True, hyper_signal="fps25", smoothness=None, daypart="night")}.items():
            f = tmp_path / f"api_{name}.mp4"
            f.write_bytes(b"x")
            a = _asset(db, f, 10.0)
            out[name] = a.id
            recs[name] = rec
        db.commit()
        for name, rec in recs.items():
            motion_store.save_record(out[name], rec)
    pfx = str(tmp_path) + "/api_"
    items = client.get("/api/assets", params={"path_prefix": pfx, "min_smoothness": 0.6}).json()["items"]
    assert [i["id"] for i in items] == [out["s"]] and items[0]["motion"]["smoothness"] == 0.9
    assert items[0]["motion"]["windows"] == [[0, 20, 0.9]] and items[0]["motion"]["daypart"] == "day"
    hl = client.get("/api/assets", params={"path_prefix": pfx, "hyperlapse": "true"}).json()["items"]
    assert [i["id"] for i in hl] == [out["h"]] and hl[0]["motion"]["hyper_signal"] == "fps25"
    ng = client.get("/api/assets", params={"path_prefix": pfx, "daypart": "night"}).json()["items"]
    assert [i["id"] for i in ng] == [out["h"]]
    assert client.get("/api/assets", params={"daypart": "noon"}).status_code == 422


def test_analyze_job_stores_results_and_skips_done(client, wait_job, texture, tmp_path):
    f = _pan(texture, tmp_path / "DJI_9999.mp4", SMOOTH_X)
    with SessionLocal() as db:
        a = _asset(db, f, 8.0)
        db.commit()
        aid = a.id
    r = client.post("/api/motion/analyze", json={"asset_ids": [aid]})
    assert r.status_code == 200, r.text
    job = wait_job(client, r.json()["job_id"])
    assert job["status"] == "done"
    asset = client.get("/api/assets", params={"path_prefix": str(f)}).json()["items"][0]
    assert asset["motion"]["smoothness"] >= 0.7 and asset["motion"]["daypart"] in ("day", "dusk")
    assert motion_store.pending_ids(asset_ids=[aid]) == []              # already analysed -> skipped
    assert motion_store.pending_ids(asset_ids=[aid], force=True) == [aid]
    st = client.get("/api/motion/status").json()
    assert st["analysed"] >= 1 and "by_class" in st
    # idle-time pickup only considers drone assets
    assert motion_store.is_drone_path("/x/blue-drone-1o1/a.mp4") and motion_store.is_drone_path("/x/DJI_0001.MP4")
    assert not motion_store.is_drone_path("/x/holiday.mp4")


def test_story_allocation_uses_smooth_windows_only():
    cand = {"asset_id": 1, "scene_id": None, "start": None, "duration": 40.0, "score": 1.0,
            "windows": [[5.0, 9.0, 0.9], [20.0, 36.0, 0.85]]}
    clips = story.allocate_clips(6.0, [cand], set())
    assert len(clips) == 1 and clips[0]["start"] >= 20.0 and clips[0]["end"] <= 36.0   # longest window
    short = {"asset_id": 2, "scene_id": None, "start": None, "duration": 40.0, "score": 1.0,
             "windows": [[5.0, 9.0, 0.9]]}
    clips = story.allocate_clips(6.0, [short, dict(cand, asset_id=3)], set())
    assert all(any(w[0] <= c["start"] and c["end"] <= w[1] for w in (short["windows"] if c["asset_id"] == 2 else cand["windows"]))
               for c in clips)


def test_broll_defaults_pick_only_smooth_non_hyperlapse_footage(client, tmp_path):
    from app.edits import broll
    specs = {  # name: (record, expected usable)
        "s1": _rec(smoothness=0.95, profile=[0.95] * 10, windows=[[0, 10, 0.95]]),
        "s2": _rec(smoothness=0.9, profile=[0.9] * 10, windows=[[0, 10, 0.9]]),
        "s3": _rec(smoothness=0.9, profile=[0.9] * 10),
        "part": _rec(smoothness=0.5, profile=[0.2] * 3 + [0.9] * 7 + [0.2] * 0),   # smooth only from 3 s
        "jerky": _rec(smoothness=0.3, profile=[0.3] * 10),
        "hyper": _rec(is_hyperlapse=True, hyper_signal="fps25", smoothness=None, profile=[], windows=[]),
    }
    ids, recs = {}, {}
    with SessionLocal() as db:
        for name, rec in specs.items():
            f = tmp_path / f"DJI_{name}.mp4"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=teal:s=160x90:r=30:d=10",
                            "-pix_fmt", "yuv420p", str(f)], check=True)
            ids[name] = _asset(db, f, 10.0, aesthetic_score=5.0).id
            recs[name] = rec
        db.commit()
    for name, rec in recs.items():
        motion_store.save_record(ids[name], rec)
    plan = broll.create_broll_plan(silent=True, target_seconds=30,
                                   broll_filter={"path_prefixes": [str(tmp_path) + "/"]})
    side = broll.get_broll(plan["plan_id"])
    used = {c["asset_id"] for cut in side["cuts"] for c in cut["clips"]}
    assert used <= {ids["s1"], ids["s2"], ids["s3"], ids["part"]}
    assert ids["jerky"] not in used and ids["hyper"] not in used
    assert side["broll_filter"]["min_smoothness"] == broll.DEFAULT_MIN_SMOOTHNESS and side["broll_filter"]["hyperlapse"] is False
    for cut in side["cuts"]:
        for c in cut["clips"]:
            if c["asset_id"] == ids["part"]:
                assert c["start"] >= 3.0                      # only inside its smooth window
    # 30 s from four short assets: other ranges are reused before any identical range repeats
    assert side["reused_ranges"] == 0 or side["reused_assets"] > 0
    # explicit nulls switch the defaults off and let the jerky clip and hyperlapse in
    off = broll.create_broll_plan(silent=True, target_seconds=30,
                                  broll_filter={"path_prefixes": [str(tmp_path) + "/"], "min_smoothness": None, "hyperlapse": None})
    used_off = {c["asset_id"] for cut in broll.get_broll(off["plan_id"])["cuts"] for c in cut["clips"]}
    assert {ids["jerky"], ids["hyper"]} <= used_off
