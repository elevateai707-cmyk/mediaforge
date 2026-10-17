"""Music-led B-roll plans: bar math, clip assignment, API and render."""
import hashlib
import json
import subprocess

import pytest

from app import models, persist
from app.db import SessionLocal
from app.edits import beatmap, broll, render, story
from .test_beat_sync import _write_map
from .test_story import _capture_ffmpeg, _silent_mp3

pytestmark = pytest.mark.usefixtures("cleanup_assets")

DOWNBEATS = [round(1.0 + 2.0 * i, 3) for i in range(20)]        # 120 bpm, 4/4: a bar every 2 s


def _cand(aid, dur, score, start=None):
    return {"asset_id": aid, "scene_id": None, "start": start, "duration": dur, "score": score}


# ---------------------------------------------------------------------------
# pure math
# ---------------------------------------------------------------------------
def test_bar_timeline_whole_bars_on_downbeats():
    tl = broll.bar_timeline(DOWNBEATS, 5.1, 12.0, 1, 2.0)
    assert tl["start"] == 5.0 and tl["bars"] == 6 and tl["total"] == 12.0
    assert tl["cuts"] == [0.0, 2.0, 4.0, 6.0, 8.0, 10.0] and tl["slots"] == [2.0] * 6
    two = broll.bar_timeline(DOWNBEATS, 5.0, 12.0, 2, 2.0)
    assert two["cuts"] == [0.0, 4.0, 8.0] and two["slots"] == [4.0, 4.0, 4.0]
    odd = broll.bar_timeline(DOWNBEATS, 5.0, 11.0, 2, 2.0)           # 6 bars (nearest to 5.5 -> 6)
    assert odd["total"] == 12.0
    assert broll.bar_timeline(DOWNBEATS, 5.0, 7.0, 2, 2.0)["bars"] == 4   # 3.5 bars -> 4 bars
    # every cut is an actual downbeat of the track
    for c in tl["cuts"]:
        assert round(c + tl["start"], 3) in DOWNBEATS


def test_bar_timeline_shifts_earlier_near_track_end_and_rejects_short_tracks():
    tl = broll.bar_timeline(DOWNBEATS, DOWNBEATS[-1], 12.0, 1, 2.0)
    assert tl["start"] == DOWNBEATS[-1 - 6] and tl["total"] == 12.0
    with pytest.raises(broll.BrollError, match="too short"):
        broll.bar_timeline(DOWNBEATS[:4], 1.0, 12.0, 1, 2.0)


def test_silent_timeline():
    tl = broll.silent_timeline(12.0)
    assert tl["total"] == pytest.approx(12.0, abs=0.01) and len(tl["slots"]) == 8
    assert all(s == pytest.approx(1.5, abs=0.2) for s in tl["slots"])
    assert tl["cuts"][1] == pytest.approx(tl["slots"][0])


def test_assign_clips_no_reuse_drop_gets_best_and_start_skips_takeoff():
    cands = [_cand(1, 9.0, 9.5), _cand(2, 9.0, 8.0), _cand(3, 9.0, 7.0), _cand(4, 9.0, 6.0)]
    per_cut, reused = broll.assign_clips([2.0, 2.0, 2.0], cands, drop_cut=2)
    assert reused == 0
    assert per_cut[2][0]["asset_id"] == 1                        # best clip on the drop cut
    ids = [c[0]["asset_id"] for c in per_cut]
    assert len(set(ids)) == 3
    for cut, slot in zip(per_cut, [2.0] * 3):
        assert cut[0]["start"] >= broll.START_SKIP_S and cut[0]["end"] - cut[0]["start"] == pytest.approx(slot)


def test_assign_clips_short_assets_reuse_and_errors():
    # a 2.5 s asset cannot fill a 4 s cut after skipping 1 s: spills into a second clip
    per_cut, _ = broll.assign_clips([4.0], [_cand(1, 2.5, 5), _cand(2, 9.0, 4)], None)
    assert sum(c["end"] - c["start"] for c in per_cut[0]) == pytest.approx(4.0, abs=0.01)
    # pool smaller than the number of cuts: reuse is counted rather than failing
    per_cut, reused = broll.assign_clips([2.0, 2.0, 2.0], [_cand(1, 9, 5), _cand(2, 9, 4)], None)
    assert reused >= 1 and all(per_cut)
    with pytest.raises(broll.BrollError):
        broll.assign_clips([2.0], [], None)


# ---------------------------------------------------------------------------
# plan creation
# ---------------------------------------------------------------------------
COLORS = ["red", "green", "blue", "yellow", "magenta", "cyan", "orange", "white"]


@pytest.fixture()
def clips(client, tmp_path_factory):
    """Eight solid-colour 10 s clips (aesthetic 9..2) plus one too-short clip."""
    d = tmp_path_factory.mktemp("broll_assets")
    ids = {}
    with SessionLocal() as db:
        for i, color in enumerate(COLORS + ["short"]):
            f = d / f"DJI_{i:04d}.mp4"
            dur = 1.5 if color == "short" else 10
            src = "black" if color == "short" else color
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                            f"color=c={src}:s=160x90:r=30:d={dur}", "-pix_fmt", "yuv420p",
                            "-c:v", "libx264", "-preset", "ultrafast", str(f)], check=True)
            a = models.Asset(path=str(f), hash=hashlib.md5(str(f).encode()).hexdigest(),
                             kind="video", width=160, height=90, duration=float(dur),
                             aesthetic_score=9.0 - i if color != "short" else 10.0, status="indexed")
            db.add(a)
            db.flush()
            ids[color] = a.id
        db.commit()
    return ids, str(d) + "/"


@pytest.fixture()
def track(tmp_path):
    t = tmp_path / "music" / "beat.mp3"
    t.parent.mkdir()
    _silent_mp3(t, 45.0)
    _write_map(t, duration=45.0, beats=[round(1.0 + 0.5 * i, 3) for i in range(80)],
               downbeats=DOWNBEATS, drop_s=DOWNBEATS[3], best_start_15s=DOWNBEATS[2] + 0.2,
               best_start_30s=DOWNBEATS[2] + 0.2)
    return t


def test_create_broll_plan_api(client, clips, track):
    ids, prefix = clips
    r = client.post("/api/edits/broll", json={
        "music_path": str(track), "target_seconds": 12, "bars_per_cut": 1,
        "text_line": "Your site is *invisible*", "brand": "albertaweb", "hook_text": "Stop scrolling",
        "broll_filter": {"path_prefixes": [prefix]}})
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["status"] == "draft" and plan["intent"].startswith("broll: ") and plan["target_ratio"] == "9:16"
    sc = client.get(f"/api/edits/{plan['plan_id']}/broll").json()
    music = sc["music"]
    assert music["start"] == DOWNBEATS[2]                       # best_start moved onto a downbeat
    assert sc["duration"] == 12.0 and sc["bars"] == 6 and plan["total_duration"] == 12.0
    # cuts exactly on the track's downbeats, one clip per cut, no reuse, none too short
    abs_cuts = [round(c["start"] + music["start"], 3) for c in sc["cuts"]]
    assert all(c in DOWNBEATS for c in abs_cuts) and len(abs_cuts) == 6
    used = [c["clips"][0]["asset_id"] for c in sc["cuts"]]
    assert len(set(used)) == 6 and ids["short"] not in used
    assert all(c["clips"][0]["start"] >= 1.0 for c in sc["cuts"])
    assert sum(c["slot"] for c in sc["cuts"]) == pytest.approx(12.0)
    # drop (DOWNBEATS[3] = 1 bar after start) falls in the window: best clip starts there
    drop_cut = next(c for c in sc["cuts"] if c["is_drop_cut"])
    assert drop_cut["start"] == 2.0 and drop_cut["clips"][0]["asset_id"] == ids["red"]
    # text appears on the downbeat of bar 2, one word per beat, emphasis from *markup*
    cb = sc["caption_beats"][0]
    assert cb["start"] == 2.0 and [w["word"] for w in cb["words"]] == ["Your", "site", "is", "invisible"]
    assert cb["emphasis"] == [[3, 4]]
    assert sc["brand"]["id"] == "albertaweb" and sc["hook_text"] == "Stop scrolling"
    # plan rows are hard cuts matching the sidecar; plan GET exposes the summary
    got = client.get(f"/api/edits/plan/{plan['plan_id']}").json()
    assert got["broll"]["cut_count"] == 6 and all(c["transition"] == "cut" for c in got["clips"])
    assert client.post(f"/api/edits/plan/{plan['plan_id']}/approve").status_code == 200


def test_create_broll_two_bars_per_cut_and_asset_ids_order(client, clips, track):
    ids, prefix = clips
    order = [ids["blue"], ids["green"], ids["red"]]
    r = client.post("/api/edits/broll", json={
        "music_path": str(track), "target_seconds": 12, "bars_per_cut": 2, "asset_ids": order})
    assert r.status_code == 200, r.text
    sc = client.get(f"/api/edits/{r.json()['plan_id']}/broll").json()
    assert [c["slot"] for c in sc["cuts"]] == [4.0, 4.0, 4.0]
    non_drop = [c["clips"][0]["asset_id"] for c in sc["cuts"] if not c["is_drop_cut"]]
    assert set(c["clips"][0]["asset_id"] for c in sc["cuts"]) == set(order)
    assert non_drop == [a for a in order if a != next(c for c in sc["cuts"] if c["is_drop_cut"])["clips"][0]["asset_id"]]


def test_create_broll_silent(client, clips):
    ids, prefix = clips
    r = client.post("/api/edits/broll", json={"silent": True, "target_seconds": 12,
                                              "broll_filter": {"path_prefixes": [prefix]}})
    assert r.status_code == 200, r.text
    sc = client.get(f"/api/edits/{r.json()['plan_id']}/broll").json()
    assert sc["silent"] and sc["music"] is None and sc["duration"] == pytest.approx(12.0, abs=0.01)
    assert all(1.4 <= c["slot"] <= 1.8 for c in sc["cuts"])


def test_create_broll_errors(client, clips, track, tmp_path, monkeypatch):
    ids, prefix = clips
    nomap = tmp_path / "plain.mp3"
    _silent_mp3(nomap, 5)
    for body, text in (
            ({"music_path": str(nomap)}, "beat map"),
            ({"music_path": str(tmp_path / "missing.mp3")}, "not found"),
            ({"music_path": str(track), "brand": "nope"}, "unknown brand"),
            ({"music_path": str(track), "broll_filter": {"asset_ids": [999999]}}, "no usable"),
            ({"music_path": str(track), "broll_filter": {"path_prefixes": ["/nowhere/"]}}, "no usable")):
        r = client.post("/api/edits/broll", json=body)
        assert r.status_code == 422 and text in r.text, (body, r.text)
    assert client.post("/api/edits/broll", json={"bars_per_cut": 3}).status_code == 422
    # no music_path and no mapped tracks in the music folder
    monkeypatch.setattr(persist, "music_dir", lambda: str(tmp_path / "empty"))
    r = client.post("/api/edits/broll", json={"broll_filter": {"path_prefixes": [prefix]}})
    assert r.status_code == 422 and "beat map" in r.text


def test_default_music_is_a_mapped_track(client, clips, track, monkeypatch):
    ids, prefix = clips
    monkeypatch.setattr(persist, "music_dir", lambda: str(track.parent))
    r = client.post("/api/edits/broll", json={"seed": 1, "broll_filter": {"path_prefixes": [prefix]}})
    assert r.status_code == 200, r.text
    assert client.get(f"/api/edits/{r.json()['plan_id']}/broll").json()["music"]["path"] == str(track.resolve())


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------
def _color_cuts(path, fps=30):
    """Times where the frame's average colour jumps (solid-colour test clips)."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", f"fps={fps},scale=1:1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    px = [raw[i:i + 3] for i in range(0, len(raw) - 2, 3)]
    return [round(i / fps, 4) for i in range(1, len(px))
            if max(abs(a - b) for a, b in zip(px[i], px[i - 1])) > 60]


def test_render_broll_cuts_land_on_downbeats(client, clips, track, monkeypatch):
    ids, prefix = clips
    r = client.post("/api/edits/broll", json={"music_path": str(track), "target_seconds": 8,
                                              "brand": "buildwithdb", "text_line": "Built to *scale*",
                                              "broll_filter": {"path_prefixes": [prefix]}})
    plan = r.json()
    sc = client.get(f"/api/edits/{plan['plan_id']}/broll").json()
    client.post(f"/api/edits/plan/{plan['plan_id']}/approve")
    seen = _capture_ffmpeg(monkeypatch)
    out = render.render_plan(plan["plan_id"], width=270, height=480)
    cmd = seen[-1]
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "fade=t=out" in graph and "tpad=" in graph and "afade=t=out" in graph
    assert "sidechaincompress" not in graph and "apad" not in graph
    assert cmd[cmd.index(str(track)) - 5:cmd.index(str(track)) - 3] == ["-ss", f"{sc['music']['start']:.3f}"]
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                                       "-show_format", out], capture_output=True, text=True).stdout)
    assert {s["codec_type"] for s in probe["streams"]} == {"video", "audio"}
    assert float(probe["format"]["duration"]) == pytest.approx(sc["duration"] + 1.8, abs=0.3)
    # measured cut times vs the planned downbeat cut times (within one frame at 30 fps)
    planned = [c["start"] for c in sc["cuts"][1:]]
    measured = [t for t in _color_cuts(out) if t < sc["duration"] - 0.2]
    assert len(measured) == len(planned)
    for p, m in zip(planned, measured):
        assert abs(p - m) <= 1 / 30 + 0.01, (planned, measured)


def test_render_silent_broll_has_silent_audio_track(client, clips, monkeypatch):
    ids, prefix = clips
    plan = client.post("/api/edits/broll", json={"silent": True, "target_seconds": 6,
                                                 "broll_filter": {"path_prefixes": [prefix]}}).json()
    client.post(f"/api/edits/plan/{plan['plan_id']}/approve")
    out = render.render_plan(plan["plan_id"], width=180, height=320)
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                                       "-show_format", out], capture_output=True, text=True).stdout)
    assert {s["codec_type"] for s in probe["streams"]} == {"video", "audio"}
    assert float(probe["format"]["duration"]) == pytest.approx(6.0, abs=0.3)


def test_render_rejects_broll_plan_whose_clips_drifted(client, clips, track):
    ids, prefix = clips
    plan = client.post("/api/edits/broll", json={"music_path": str(track), "target_seconds": 8,
                                                 "broll_filter": {"path_prefixes": [prefix]}}).json()
    pid = plan["plan_id"]
    with SessionLocal() as db:
        c = db.query(models.EditClip).filter_by(plan_id=pid).first()
        c.end = c.end + 1.0
        db.commit()
    client.post(f"/api/edits/plan/{pid}/approve")
    with pytest.raises(render.RenderError, match="no longer match"):
        render.render_plan(pid, width=180, height=320)
