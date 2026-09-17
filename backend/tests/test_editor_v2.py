import asyncio
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest
from app import config, models
from app.db import SessionLocal
from app.edits.project import (
    Clip,
    Cue,
    Overlay,
    Project,
    Style,
    Word,
    snapshot,
    timeline,
)
from app.edits.render_v2 import render_snapshot
from app.edits.subtitles import ass_document, mapped_cues, sidecar
from app.jobs import _CancelFlag
from app.proc import tool_env


def test_mapping_trim_order_speed_overlap():
    a = Clip(asset_id=1, start=10, end=14, speed=2)
    b = Clip(asset_id=2, start=2, end=5, speed=0.5)
    p = Project(
        clips=[a, b],
        captions={
            a.uid: [Cue(start=9, end=12, text="trimmed")],
            b.uid: [Cue(start=3, end=4, text="second")],
        },
    )
    cues = mapped_cues(p)
    assert (cues[0][0].start, cues[0][0].end) == (0, 1)
    assert cues[1][0].start == pytest.approx(3.7)
    p.clips.reverse()
    assert mapped_cues(p)[0][0].start == 2
    assert timeline(p)[1]["start"] == pytest.approx(5.7)


def test_unicode_and_safe_ass():
    c = Clip(asset_id=1, start=0, end=3)
    p = Project(
        clips=[c],
        captions={
            c.uid: [
                Cue(
                    start=0,
                    end=2,
                    text="Café, it's 100%: {\\pos(0,0)} 👋\n" + "longword" * 30,
                )
            ]
        },
    )
    result = ass_document(p, 1080, 1920)
    assert "Café, it's 100%" in result
    assert r"{\pos" not in result
    assert r"\N" in result
    assert "WEBVTT" in sidecar(p, "vtt")
    assert "00:00:00,000 --> 00:00:02,000" in sidecar(p, "srt")


def test_four_combinations_persist_and_reapprove(client, approved_plan):
    pid = approved_plan()
    envelope = client.get(f"/api/editor/{pid}").json()
    p = envelope["project"]
    # Legacy descriptions migrate as overlays without deleting originals.
    with SessionLocal() as db:
        assert db.get(models.EditPlan, pid).clips
    for speech, overlay in [(False, False), (True, False), (False, True), (True, True)]:
        p.update(speech_captions=speech, on_video_text=overlay)
        r = client.put(f"/api/editor/{pid}", json=p)
        assert r.status_code == 200, r.text
        p = r.json()["project"]
        loaded = client.get(f"/api/editor/{pid}").json()
        assert loaded["project"] == p
        assert loaded["approved_revision"] is None
        assert client.post(f"/api/editor/{pid}/render").status_code == 409
        assert (
            client.post(
                f"/api/editor/{pid}/approve", json={"revision": p["revision"]}
            ).status_code
            == 200
        )
        snap, _ = snapshot(pid)
        old_revision = p["revision"]
        p["post_title"] = "changed"
        p = client.put(f"/api/editor/{pid}", json=p).json()["project"]
        assert snap["revision"] == old_revision
        assert (
            client.post(
                f"/api/editor/{pid}/approve", json={"revision": old_revision}
            ).status_code
            == 409
        )
        assert client.put(f"/api/editor/{pid}", json=snap).status_code == 409


def fixture_video(path, audio=True):
    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=0x203040:s=320x240:r=24:d=3",
    ]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-shortest"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True, env=tool_env())


def frame(path, t=0.5):
    r = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            str(t),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        capture_output=True,
        check=True,
        env=tool_env(),
    )
    return np.frombuffer(r.stdout, dtype=np.uint8)


def test_representative_renders(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EXPORTS_DIR", tmp_path / "renders")
    source = tmp_path / "source.mp4"
    silent = tmp_path / "silent.mp4"
    fixture_video(source)
    fixture_video(silent, False)
    c = Clip(asset_id=1, start=0.25, end=2.25, speed=1)
    p = Project(
        clips=[c],
        captions={
            c.uid: [
                Cue(
                    start=0.25,
                    end=2,
                    text="Speech: café, it's 100%",
                    words=[Word(start=0.25, end=1, text="Speech")],
                )
            ]
        },
        overlays={
            c.uid: [
                Overlay(start=0.25, end=2, text="TITLE", style=Style(position="top"))
            ]
        },
    )
    p.export.width = 320
    p.export.ratio = "16:9"
    paths = {
        1: {"path": str(source), "kind": "video"},
        2: {"path": str(silent), "kind": "video"},
    }
    frames = {}
    for speech, overlay in [(False, False), (True, False), (False, True), (True, True)]:
        p.speech_captions = speech
        p.on_video_text = overlay
        result = render_snapshot(p.model_dump(), paths, lambda *_: None, _CancelFlag())
        f = frame(result["video"])
        frames[speech, overlay] = f
        info = json.loads(
            subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_streams",
                    "-show_format",
                    "-of",
                    "json",
                    result["video"],
                ],
                env=tool_env(),
            )
        )
        assert abs(float(info["format"]["duration"]) - 2) < 0.15
        assert not any(s["codec_type"] == "subtitle" for s in info["streams"])
        assert not list(Path(result["video"]).parent.glob("*.srt"))
        assert any(s["codec_type"] == "audio" for s in info["streams"])
    assert not np.array_equal(frames[False, False], frames[True, False])
    assert not np.array_equal(frames[False, False], frames[False, True])
    assert not np.array_equal(frames[True, False], frames[True, True])
    # No-audio source, crossfade and speed changes must produce synchronized streams.
    p.clips = [
        Clip(asset_id=2, start=0, end=2, speed=2),
        Clip(asset_id=1, start=0.5, end=2.5, speed=0.5),
    ]
    p.speech_captions = False
    p.on_video_text = False
    r = render_snapshot(p.model_dump(), paths, lambda *_: None, _CancelFlag())
    info = json.loads(
        subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", r["video"]],
            env=tool_env(),
        )
    )
    lengths = [float(x["duration"]) for x in info["streams"]]
    assert max(lengths) - min(lengths) < 0.15
    assert lengths[0] == pytest.approx(4.7, abs=0.15)
    # Keep representative results for human visual QA outside pytest's temporary tree.
    import os
    import shutil

    evidence = os.environ.get("MF_RENDER_EVIDENCE")
    if evidence:
        dest = Path(evidence)
        dest.mkdir(parents=True, exist_ok=True)
        for path in (tmp_path / "renders").glob("*.mp4"):
            shutil.copy2(path, dest / path.name)


def test_cancellation_cleans_partial_files(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EXPORTS_DIR", tmp_path / "out")
    src = tmp_path / "s.mp4"
    fixture_video(src)
    p = Project(clips=[Clip(asset_id=1, end=3)])
    p.export.width = 320
    flag = _CancelFlag()
    flag.cancel()
    with pytest.raises(asyncio.CancelledError):
        render_snapshot(
            p.model_dump(),
            {1: {"path": str(src), "kind": "video"}},
            lambda *_: None,
            flag,
        )
    assert not list((tmp_path / "out").glob("*"))


def test_local_access_and_untrusted_paths(client, approved_plan):
    assert (
        client.get(
            "/api/providers/settings", headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/api/providers/settings", headers={"Host": "evil.example"}
        ).status_code
        == 403
    )
    pid = approved_plan()
    p = client.get(f"/api/editor/{pid}").json()["project"]
    p["music"]["path"] = "/etc/passwd"
    assert client.put(f"/api/editor/{pid}", json=p).status_code == 409


def test_audio_mix_and_formats(tmp_path, monkeypatch):
    from PIL import Image

    monkeypatch.setattr(config, "EXPORTS_DIR", tmp_path / "out")
    source = tmp_path / "s.mp4"
    fixture_video(source)
    voice = tmp_path / "voice.wav"
    music = tmp_path / "music.wav"
    for path, freq in [(voice, 880), (music, 220)]:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={freq}:duration=2",
                str(path),
            ],
            check=True,
            env=tool_env(),
        )
    c = Clip(asset_id=1, start=0, end=2)
    p = Project(clips=[c])
    p.export.width = 240
    p.export.ratio = "1:1"
    p.original_audio.enabled = False
    paths = {1: {"path": str(source), "kind": "video"}}
    result = render_snapshot(p.model_dump(), paths, lambda *_: None, _CancelFlag())
    raw = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            result["video"],
            "-vn",
            "-f",
            "f32le",
            "-ac",
            "1",
            "-",
        ],
        env=tool_env(),
    )
    assert np.max(np.abs(np.frombuffer(raw, dtype=np.float32))) < 0.001
    p.voice_over.enabled = True
    p.voice_over.path = str(voice)
    p.voice_over.start = 0.2
    p.voice_over.fade_in = 0.1
    p.music.enabled = True
    p.music.path = str(music)
    p.music.volume = 0.15
    p.music.fade_out = 0.2
    p.caption_source = "narration"
    p.narration_captions = [
        Cue(start=0, end=1, text="Voice", words=[Word(start=0, end=1, text="Voice")])
    ]
    p.speech_captions = True
    p.caption_style.preset = "active"
    p.export.ratio = "9:16"
    p.export.sidecars = ["srt", "vtt", "ass"]
    p.export.clean_master = True
    result = render_snapshot(p.model_dump(), paths, lambda *_: None, _CancelFlag())
    assert all(
        Path(result[k]).is_file()
        for k in ["video", "clean_master", "srt", "vtt", "ass"]
    )
    assert "00:00:00,200" in Path(result["srt"]).read_text()
    assert not np.array_equal(frame(result["video"]), frame(result["clean_master"]))
    raw = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            result["video"],
            "-vn",
            "-f",
            "f32le",
            "-ac",
            "1",
            "-",
        ],
        env=tool_env(),
    )
    assert np.max(np.abs(np.frombuffer(raw, dtype=np.float32))) > 0.01
    # Still image input is a finite clip, not an infinite loop or missing audio failure.
    photo = tmp_path / "photo.png"
    Image.new("RGB", (100, 200), "navy").save(photo)
    p = Project(clips=[Clip(asset_id=2, start=0, end=1)])
    p.export.width = 240
    r = render_snapshot(
        p.model_dump(),
        {2: {"path": str(photo), "kind": "photo"}},
        lambda *_: None,
        _CancelFlag(),
    )
    assert Path(r["video"]).stat().st_size > 0


def test_silence_profile_never_loads_model(tmp_path, monkeypatch):
    from app.ai import whisper

    source = tmp_path / "silent.mp4"
    fixture_video(source, False)
    monkeypatch.setattr(
        whisper, "available", lambda: pytest.fail("No audio must not load a model")
    )
    result = whisper.transcribe_profile(str(source), "quality")
    assert result["segments"] == [] and "No audio" in result["notice"]


def test_project_schema_rejects_ambiguous_ids_and_invalid_words():
    c = Clip(asset_id=1, end=2)
    with pytest.raises(ValueError):
        Project(clips=[c, c])
    with pytest.raises(ValueError):
        Word(start=2, end=1, text="x")
    with pytest.raises(ValueError):
        Clip(asset_id=1, end=float("nan"))


def test_migrated_project_rejects_legacy_render_bypass(client, approved_plan):
    pid = approved_plan()
    assert client.get(f"/api/editor/{pid}").status_code == 200
    assert client.post(f"/api/edits/plan/{pid}/approve").status_code == 409
    assert client.post("/api/render", json={"plan_id": pid}).status_code == 409


def test_local_websocket_origin_scheme():
    from app.security import LocalAccess

    accepted = []

    async def inner(scope, receive, send):
        accepted.append(True)

    async def unused():
        return {}

    async def send(message):
        raise AssertionError(message)

    asyncio.run(
        LocalAccess(inner)(
            {
                "type": "websocket",
                "scheme": "ws",
                "client": ("127.0.0.1", 123),
                "headers": [
                    (b"host", b"127.0.0.1:8421"),
                    (b"origin", b"http://127.0.0.1:8421"),
                ],
            },
            unused,
            send,
        )
    )
    assert accepted == [True]


def test_backup_precedes_migration(tmp_path, monkeypatch):
    import sqlite3
    from sqlalchemy import create_engine
    from app import db as database

    path = tmp_path / "existing.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE edit_plans (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO edit_plans VALUES ('preserved')")
    engine = create_engine(f"sqlite:///{path}")
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(database, "_engine", engine)
    monkeypatch.setattr(database, "VEC_AVAILABLE", False)
    database.init_db()
    backup = path.with_name(path.name + ".pre-editor-v2.bak")
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as conn:
        assert conn.execute("SELECT id FROM edit_plans").fetchone()[0] == "preserved"
        assert not conn.execute(
            "SELECT name FROM sqlite_master WHERE name='project_documents'"
        ).fetchone()
    database.init_db()
    engine.dispose()
