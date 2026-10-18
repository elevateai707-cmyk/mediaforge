"""Beat maps: reader, cut snapping, music start, and the beat-synced story render."""
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from app import config, models
from app.db import SessionLocal
from app.edits import beatmap, render, story
from .test_story import SCRIPT, _capture_ffmpeg, _silent_mp3, fake_tts  # noqa: F401

REPO = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.usefixtures("cleanup_assets")


def _write_map(track: Path, **over):
    data = {"file": track.name, "duration": 30.0, "bpm": 120.0,
            "beats": [round(0.0 + 0.5 * i, 3) for i in range(60)],
            "downbeats": [round(2.0 * i, 3) for i in range(15)],
            "drop_s": 12.0, "best_start_15s": 4.1, "best_start_30s": 0.0,
            "energy_1s": [0.1] * 30}
    data.update(over)
    d = track.parent / ".beats"
    d.mkdir(exist_ok=True)
    (d / f"{track.name}.json").write_text(json.dumps(data))
    return data


def test_load_beatmap_and_defaults(tmp_path):
    track = tmp_path / "song.mp3"
    track.write_bytes(b"x")
    assert beatmap.load(str(track)) is None
    _write_map(track)
    bm = beatmap.load(str(track))
    assert bm.bpm == 120.0 and bm.bar_s == pytest.approx(2.0)
    assert beatmap.beatmap_path(str(track)).name == "song.mp3.json"
    # best_start 4.1 moves onto the nearest downbeat; window must fit in the track
    assert beatmap.default_start(bm, 12) == 4.0
    assert beatmap.default_start(bm, 28) == 0.0 or beatmap.default_start(bm, 28) <= 2.0
    (track.parent / ".beats" / "song.mp3.json").write_text("{not json")
    assert beatmap.load(str(track)) is None            # corrupt -> no sync, no crash


def test_snap_plan_moves_cuts_within_tolerance_and_keeps_length():
    durs = [1.8, 1.8, 1.8]
    plan = beatmap.snap_plan(durs, [1.0] * 3, [1.0] * 3, [0.0, 1.7, 3.7, 5.4])
    assert plan["snapped"] == [1.7, 3.7]
    assert plan["deltas"] == pytest.approx([-0.1, 0.1])
    new_durs = [e - s for (s_off, e_off), d in zip(plan["offsets"], durs)
                for s, e in [(s_off, d + e_off)]]
    assert sum(new_durs) == pytest.approx(sum(durs))        # timeline length unchanged
    assert plan["offsets"][0][0] == 0 and plan["offsets"][-1][1] == 0   # outer edges fixed


def test_snap_plan_respects_tolerance_room_and_min_length():
    # beat 0.3 s away: too far
    assert beatmap.snap_plan([2, 2], [1, 1], [1, 1], [2.3])["deltas"] == [0]
    # needs to start the next clip earlier but it has no room before its in-point
    assert beatmap.snap_plan([2, 2], [1, 0.0], [1, 1], [1.9])["deltas"] == [0]
    # needs to extend the first clip but its source has no tail
    assert beatmap.snap_plan([2, 2], [1, 1], [0.0, 1], [2.1])["deltas"] == [0]
    # would leave a clip shorter than the minimum
    assert beatmap.snap_plan([0.5, 2], [1, 1], [1, 1], [0.38])["deltas"] == [0]


def test_flash_time_prefers_nearby_downbeat():
    assert beatmap.flash_time(3.7, [0.0, 4.0, 8.0]) == 4.0
    assert beatmap.flash_time(6.0, [0.0, 4.0, 8.0]) == 6.0          # too far: stay on the cut


def _load_script():
    spec = importlib.util.spec_from_file_location("beatmap_script", REPO / "scripts" / "beatmap.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_beatmap_script_pure_helpers_need_no_librosa(tmp_path):
    mod = _load_script()
    beats = [i * 0.5 for i in range(40)]
    strengths = [0.2, 1.0, 0.3, 0.4] * 10                     # phase 1 is strongest
    assert mod.downbeats_from_beats(beats, strengths)[:3] == [0.5, 2.5, 4.5]
    energy = [0.1] * 12 + [0.9] * 12
    assert mod.find_drop(energy) == 12.0
    assert mod.best_window_start(energy, 6) == 12.0
    data = mod.build_map("x.mp3", 24.0, 120.0, beats, strengths, energy)
    assert set(data) >= {"file", "duration", "bpm", "beats", "downbeats", "drop_s",
                         "best_start_15s", "best_start_30s", "energy_1s"}
    assert data["drop_s"] in data["downbeats"]
    track = tmp_path / "t.mp3"
    dest = mod.write_map(track, data)
    assert dest == tmp_path / ".beats" / "t.mp3.json"
    # the file the script writes is what the runtime reader consumes
    track.write_bytes(b"x")
    assert beatmap.load(str(track)).downbeats == data["downbeats"]
    assert "librosa" not in open(REPO / "backend" / "app" / "edits" / "beatmap.py").read()


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------
@pytest.fixture()
def long_assets(client, tmp_path_factory):
    d = tmp_path_factory.mktemp("long_assets")
    ids = []
    with SessionLocal() as db:
        for i, src in enumerate(("testsrc2", "smptebars")):
            f = d / f"long_{i}.mp4"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                            f"{src}=s=320x180:r=30:d=10", "-pix_fmt", "yuv420p",
                            "-c:v", "libx264", "-preset", "ultrafast", str(f)], check=True)
            a = models.Asset(path=str(f), hash=hashlib.md5(str(f).encode()).hexdigest(),
                             kind="video", width=320, height=180, duration=10.0, status="indexed")
            db.add(a)
            db.flush()
            ids.append(a.id)
        db.commit()
    return ids


def test_story_render_snaps_cuts_to_music_beats(client, fake_tts, long_assets, tmp_path, monkeypatch):  # noqa: F811
    plan = _approved_story_with(client, long_assets)
    pid = plan["plan_id"]
    sc = story.get_story(pid)
    first_slot = sc["beats"][0]["slot"]                       # ~1.8 s
    track = tmp_path / "beat.mp3"
    _silent_mp3(track, 20.0)
    # beats chosen so the story's single internal cut sits 0.1 s past one
    _write_map(track, beats=[0.0, first_slot - 0.1, 2 * first_slot], downbeats=[0.0],
               best_start_15s=0.0, best_start_30s=0.0, duration=20.0)
    seen = _capture_ffmpeg(monkeypatch)
    out = render.render_plan(pid, ratio="9:16", width=180, height=320, music_path=str(track),
                             effects=True, captions=False)
    cmd = seen[-1]
    graph = cmd[cmd.index("-filter_complex") + 1]
    clips = plan["clips"]
    a0, a1 = clips[0], clips[1]
    assert f"trim=start={a0['start']:.3f}:end={a0['end'] - 0.1:.3f}" in graph
    assert f"trim=start={a1['start'] - 0.1:.3f}:end={a1['end']:.3f}" in graph
    assert "afade=t=out" in graph and "d=0.600" in graph      # music fades over the last 0.6 s
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "csv=p=0", out], capture_output=True, text=True).stdout)
    assert dur == pytest.approx(sc["duration"], abs=0.3)      # length (and voice sync) unchanged


def test_story_render_without_beatmap_is_unchanged(client, fake_tts, long_assets, tmp_path, monkeypatch):  # noqa: F811
    plan = _approved_story_with(client, long_assets)
    track = tmp_path / "nomap.mp3"
    _silent_mp3(track, 5.0)
    seen = _capture_ffmpeg(monkeypatch)
    render.render_plan(plan["plan_id"], ratio="9:16", width=180, height=320,
                       music_path=str(track), captions=False)
    graph = seen[-1][seen[-1].index("-filter_complex") + 1]
    c0 = plan["clips"][0]
    assert f"trim=start={c0['start']:.3f}:end={c0['end']:.3f}" in graph


def test_music_start_is_passed_to_ffmpeg(client, fake_tts, long_assets, tmp_path, monkeypatch):  # noqa: F811
    plan = _approved_story_with(client, long_assets)
    track = tmp_path / "start.mp3"
    _silent_mp3(track, 20.0)
    _write_map(track)
    seen = _capture_ffmpeg(monkeypatch)
    render.render_plan(plan["plan_id"], ratio="9:16", width=180, height=320,
                       music_path=str(track), music_start=4.0, captions=False)
    cmd = seen[-1]
    i = cmd.index(str(track))
    assert cmd[i - 5:i - 3] == ["-ss", "4.000"]


def _approved_story_with(client, ids):
    plan = story.create_story_plan(SCRIPT, search_fn=lambda db, q, limit: ids,
                                   cloud_approved=True, voice_consent=True)
    assert client.post(f"/api/edits/plan/{plan['plan_id']}/approve").status_code == 200
    return plan
