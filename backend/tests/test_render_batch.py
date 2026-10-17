"""Batch orchestration and mixed cut/crossfade render coverage."""
from __future__ import annotations

import json
import math
import subprocess
import uuid
from array import array

import pytest


def _video(path, color, tone=440):
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
        "-i", f"color=c={color}:s=320x180:r=30:d=1",
        "-f", "lavfi", "-i", f"sine=frequency={tone}:sample_rate=48000:duration=1",
        "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac", str(path),
    ], check=True, capture_output=True)


def _duration(path):
    proc = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(path),
    ], check=True, capture_output=True, text=True)
    return float(proc.stdout.strip())


def _tone_strength(path, at, frequency, sample_rate=48000):
    """Small DFT window used to verify real audio starts on the video timeline."""
    proc = subprocess.run([
        "ffmpeg", "-v", "error", "-ss", str(at), "-t", "0.12", "-i", str(path),
        "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-",
    ], check=True, capture_output=True)
    samples = array("h")
    samples.frombytes(proc.stdout)
    phase = 2 * math.pi * frequency / sample_rate
    real = sum(sample * math.cos(index * phase) for index, sample in enumerate(samples))
    imag = sum(sample * math.sin(index * phase) for index, sample in enumerate(samples))
    return real * real + imag * imag


def _approved_three_clip_plan(client, tmp_path, transitions):
    """Create a real approved DB plan with one stored face on clip one."""
    # Client startup initializes the test SQLite schema before importing models.
    assert client.get("/api/health").status_code == 200
    from app import models
    from app.db import SessionLocal

    plan_id = f"mixed-{uuid.uuid4().hex}"
    files = []
    for index, (color, tone) in enumerate(zip(("red", "green", "blue"), (440, 660, 880))):
        path = tmp_path / f"{plan_id}_clip_{index}.mp4"
        _video(path, color, tone)
        files.append(path)
    with SessionLocal() as db:
        assets = []
        for index, path in enumerate(files):
            asset = models.Asset(path=str(path), hash=f"mixed-{plan_id}-{index}",
                                 kind="video", width=320, height=180, duration=1)
            db.add(asset)
            db.flush()
            assets.append(asset)
        db.add(models.FaceDetection(asset_id=assets[0].id,
                                    bbox=json.dumps([210, 40, 270, 130]), frame_time=.5))
        db.add(models.EditPlan(id=plan_id, status="approved", target_ratio="9:16"))
        for index, asset in enumerate(assets):
            db.add(models.EditClip(plan_id=plan_id, position=index, asset_id=asset.id,
                                   start=0, end=1, caption=f"caption {index}",
                                   transition="crossfade" if index == 0 else transitions[index - 1]))
        db.commit()
        return plan_id, assets[0]


def test_render_batch_reuses_analysis(monkeypatch):
    from app.edits import render

    class Clip:
        id = 11
        start = 0
        end = 2

    class Asset:
        id = 7
        path = "/synthetic.mp4"

    calls = []
    monkeypatch.setattr(render, "_load_plan", lambda _pid: (None, [(Clip(), Asset())]))
    from app.edits import reframe
    monkeypatch.setattr(reframe, "analyze_reframe", lambda *a, **kw: {"path_points": []})

    def fake_render(plan_id, **kwargs):
        calls.append((plan_id, kwargs))
        return f"/tmp/{kwargs['ratio']}.mp4"

    monkeypatch.setattr(render, "render_plan", fake_render)
    result = render.render_batch("p1", presets=["1:1", "16:9"], reframe="center")
    assert result == {"1:1": "/tmp/1:1.mp4", "16:9": "/tmp/16:9.mp4"}
    assert [call[1]["ratio"] for call in calls] == ["1:1", "16:9"]
    assert calls[0][1]["_reframe_analysis"] is calls[1][1]["_reframe_analysis"]


def test_render_batch_lock_covers_analysis_and_all_presets(monkeypatch):
    from app.edits import plan_history, reframe, render

    state = {"active": False, "events": []}

    class Lock:
        def __enter__(self):
            assert not state["active"]
            state["active"] = True
            state["events"].append("enter")

        def __exit__(self, *_args):
            state["events"].append("exit")
            state["active"] = False

    class Clip:
        id, start, end = 1, 0, 1

    class Asset:
        id, path = 1, "/synthetic.mp4"

    monkeypatch.setattr(plan_history, "plan_lock", lambda _plan_id: Lock())
    monkeypatch.setattr(render, "_load_plan", lambda _plan_id: (None, [(Clip(), Asset())]))
    monkeypatch.setattr(reframe, "analyze_reframe",
                        lambda *_args, **_kwargs: state["events"].append("analysis")
                        or {"path_points": []})
    monkeypatch.setattr(render, "render_plan",
                        lambda _plan_id, **kwargs: state["events"].append(kwargs["ratio"])
                        or f"/tmp/{kwargs['ratio']}.mp4")
    render.render_batch("locked", presets=["1:1", "16:9"])
    assert state["events"] == ["enter", "analysis", "1:1", "16:9", "exit"]


def test_rapid_repeated_renders_get_distinct_export_paths(client, tmp_path):
    from app.edits import render

    plan_id, _ = _approved_three_clip_plan(client, tmp_path, ("cut", "cut"))
    first = render.render_plan(plan_id, ratio="9:16", width=180, height=320,
                               captions=False, reframe="center")
    second = render.render_plan(plan_id, ratio="9:16", width=180, height=320,
                                captions=False, reframe="center")
    assert first != second
    assert "180x320" in first and "180x320" in second


def test_render_batch_honours_cancel_before_analysis(monkeypatch):
    import asyncio
    from app.edits import render

    class Cancelled:
        cancelled = True

    monkeypatch.setattr(render, "_load_plan",
                        lambda _plan_id: pytest.fail("cancelled batch loaded a plan"))
    with pytest.raises(asyncio.CancelledError):
        render.render_batch("unused", cancel_flag=Cancelled())


def test_mixed_cuts_keep_timebase_duration_and_face_reframe(client, tmp_path):
    """A concat output must be normalized before the next crossfade input."""
    from app.edits import reframe, render

    for transitions in (("cut", "crossfade"), ("crossfade", "cut")):
        plan_id, first_asset = _approved_three_clip_plan(client, tmp_path, transitions)
        output = render.render_plan(plan_id, ratio="9:16", width=180, height=320,
                                    captions=False, reframe="smart")
        # 1 + 1 + 1 seconds, with exactly one 0.5 second crossfade.
        assert 2.4 <= _duration(output) <= 2.6
        # The actual tone present at each post-transition segment confirms the
        # delayed envelopes share the video timeline, rather than merely
        # inspecting the generated filter string.
        checks = ((.25, 440), (1.2, 660), (2.15, 880)) if transitions[0] == "cut" \
            else ((.25, 440), (1.2, 660), (1.8, 880))
        for timestamp, tone in checks:
            expected = _tone_strength(output, timestamp, tone)
            wrong = _tone_strength(output, timestamp, 440 if tone != 440 else 880)
            assert expected > wrong * 4
        sidecar = json.loads((__import__("pathlib").Path(output).with_suffix(".reframe.json")).read_text())
        assert sidecar["clips"][0]["source"] == "faces"
        analysis = reframe.analyze_reframe(first_asset.path, asset_id=first_asset.id,
                                           start=0, end=1, mode="smart")
        previews = reframe.generate_preview_stills(first_asset.path, analysis,
                                                   tmp_path / f"previews_{plan_id}",
                                                   target_width=180, target_height=320)
        assert len(previews) == 3


def test_vertical_caption_uses_bottom_safe_zone(client, tmp_path, monkeypatch):
    """Inspect the generated filter: bottom 22% takes precedence over 5%."""
    from pathlib import Path
    from app.edits import reframe, render

    plan_id, _ = _approved_three_clip_plan(client, tmp_path, ("cut", "cut"))
    captured = {}
    real_run = render.subprocess.run

    def fake_run(cmd, **_kwargs):
        if cmd[0] != "ffmpeg":
            return real_run(cmd, **_kwargs)
        captured["cmd"] = cmd
        Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(cmd[-1]).write_bytes(b"synthetic")
        class Result:
            returncode = 0
            stderr = ""
        return Result()

    monkeypatch.setattr(render.subprocess, "run", fake_run)
    monkeypatch.setattr(reframe, "analyze_reframe",
                        lambda *_args, **_kwargs: {"path_points": [],
                                                   "safe_zones": {"bottom": .22}})
    render.render_plan(plan_id, ratio="9:16", width=180, height=320, captions=True,
                       safe_zones={"bottom": .22})
    graph = captured["cmd"][captured["cmd"].index("-filter_complex") + 1]
    assert "y=h-text_h-70" in graph


def test_vertical_caption_honours_left_and_right_safe_zones(client, tmp_path, monkeypatch):
    """Classic captions and guides use the same inner safe rectangle."""
    from pathlib import Path
    from app.edits import reframe, render

    plan_id, _ = _approved_three_clip_plan(client, tmp_path, ("cut", "cut"))
    captured = {}
    real_run = render.subprocess.run

    def fake_run(cmd, **_kwargs):
        if cmd[0] != "ffmpeg":
            return real_run(cmd, **_kwargs)
        captured["cmd"] = cmd
        Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(cmd[-1]).write_bytes(b"synthetic")
        return type("Result", (), {"returncode": 0, "stderr": ""})()

    zones = {"top": .1, "bottom": .2, "left": .1, "right": .2}
    monkeypatch.setattr(render.subprocess, "run", fake_run)
    monkeypatch.setattr(reframe, "analyze_reframe",
                        lambda *_args, **_kwargs: {"path_points": [], "safe_zones": zones})
    render.render_plan(plan_id, ratio="9:16", width=180, height=320, captions=True,
                       safe_zones=zones, guide_overlay=True)
    graph = captured["cmd"][captured["cmd"].index("-filter_complex") + 1]
    assert "drawbox=x=18:y=32:w=126:h=224" in graph
    assert "x=max(18,min((w-text_w)/2,w-36-text_w))" in graph


def test_kinetic_safe_zone_bounds_include_left_margin(tmp_path):
    """PNG emphasis and ASS body coordinates stay within a non-default inset."""
    from app.edits import kinetic

    width, height = 1080, 1920
    zones = {"top": .12, "bottom": .22, "left": .11, "right": .14}
    info = kinetic.render_emphasis_images("EXTRAORDINARY" * 5, width, height, tmp_path, "safe",
                                          safe_zones=zones)
    layout = kinetic.emphasis_layout([info], width, height, zones)[0]
    left, top = round(width * .11), round(height * .12)
    right, bottom = width - round(width * .14), height - round(height * .22)
    iw, ih = info["size"]
    assert left <= layout["x"] and layout["x"] + iw <= right
    assert top <= layout["y"] and layout["y"] + ih <= bottom
    ass = kinetic.build_ass({"body": [{"start": 0, "end": 1, "text": "supercalifragilisticexpialidocious"}],
                             "emphasis": []}, width, height, safe_zones=zones)
    assert f"\\move({left + (right - left) // 2}," in ass
    assert f",{top + round((bottom - top) * kinetic.BODY_Y)}" in ass


def test_kinetic_long_body_and_connector_are_measured_and_clipped_to_safe_zone():
    """Wide glyphs use measured fitting and cannot spill past safe margins."""
    from app.edits import kinetic

    width, height = 1080, 1920
    zones = {"top": .12, "bottom": .22, "left": .11, "right": .14}
    left, top = round(width * .11), round(height * .12)
    right, bottom = width - round(width * .14), height - round(height * .22)
    events = {
        "body": [{"start": 0, "end": 1, "text": "W" * 80}],
        "emphasis": [{"start": 0, "end": 1, "connector": "W" * 80,
                      "connector_start": 0}],
    }
    ass = kinetic.build_ass(events, width, height, safe_zones=zones)
    assert ass.count(f"\\clip({left},{top},{right},{bottom})") == 2
    # Both dialogue styles override their nominal font size after measuring
    # the actual glyphs, rather than using a character-count approximation.
    assert ass.count("\\fs") >= 2
    body_default = kinetic.body_size(width, height, zones)
    assert f"\\fs{body_default}" not in ass


def test_marked_story_without_voiceover_is_rejected(monkeypatch):
    """A corrupt story sidecar must not silently become an ordinary montage."""
    from app.edits import render, story

    class Plan:
        parsed_json = '{"story": true}'

    monkeypatch.setattr(render, "_load_plan", lambda _plan_id: (Plan(), []))
    monkeypatch.setattr(story, "get_story", lambda _plan_id: {"beats": []})
    with pytest.raises(render.RenderError, match="voiceover missing"):
        render._render_plan("no-voice", captions=False)
