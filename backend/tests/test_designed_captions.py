"""Designed on-video text: *emphasis*, glow halo, fixed height, fit-to-width."""

import numpy as np
import pytest
from app import config
from app.edits.project import Clip, Cue, Overlay, Project, Style
from app.edits.render_v2 import render_snapshot
from app.edits.subtitles import (
    ass_document,
    ass_rgb,
    emphasis_segments,
    text_width,
)
from app.jobs import _CancelFlag

from .test_editor_v2 import fixture_video, frame

RED = "#FF1A1A"


def overlay_project(text, style, start=0, end=2):
    c = Clip(asset_id=1, start=0, end=3)
    return Project(
        clips=[c],
        on_video_text=True,
        overlays={c.uid: [Overlay(start=start, end=end, text=text, style=style)]},
    )


def dialogues(doc):
    return [x for x in doc.splitlines() if x.startswith("Dialogue:")]


def test_new_style_fields_default_off_and_validate():
    s = Style()
    assert not s.designed
    assert (s.bold, s.highlight, s.glow, s.y, s.fade_in) == (False, None, 0, None, 0)
    assert Style(highlight=RED).designed
    assert Style(glow=20).designed
    assert Style(y=0.65).designed
    for bad in (
        dict(highlight="red"),
        dict(highlight="#FFF"),
        dict(y=0.0),
        dict(y=1.2),
        dict(glow=-1),
        dict(max_width=0.1),
        dict(fade_in=-5),
    ):
        with pytest.raises(ValueError):
            Style(**bad)


def test_legacy_styles_render_the_same_ass():
    # No designed field set: single wrapped Dialogue, no per-line tags.
    p = overlay_project("plain *text*\nsecond", Style(preset="bold"))
    doc = ass_document(p, 1080, 1920)
    d = dialogues(doc)
    assert len(d) == 1
    assert r"\pos" not in doc and r"\fad" not in doc and r"\blur" not in doc
    assert "plain *text* second" in d[0]
    # Bold stays driven by the preset: -1 bold flag in the style line.
    assert ",-1,0,0,0,100,100" in doc
    assert ",0,0,0,0,100,100" in ass_document(overlay_project("x", Style()), 1080, 1920)


def test_bold_flag_sets_ass_bold():
    doc = ass_document(overlay_project("x", Style(bold=True)), 1080, 1920)
    assert ",-1,0,0,0,100,100" in doc


def test_emphasis_segments():
    assert emphasis_segments("no *permission.*", True) == [
        ("no ", False),
        ("permission.", True),
    ]
    assert emphasis_segments("I kept *showing up*", True) == [
        ("I kept ", False),
        ("showing up", True),
    ]
    assert emphasis_segments("a *b* c *d*", True) == [
        ("a ", False),
        ("b", True),
        (" c ", False),
        ("d", True),
    ]
    # Markup stays literal when no highlight colour is configured.
    assert emphasis_segments("keep *this*", False) == [("keep *this*", False)]
    assert ass_rgb("#FF1A2B") == "&H2B1AFF&"


def test_designed_events_layers_position_and_colour():
    style = Style(
        bold=True, size=150, highlight=RED, glow=24, y=0.65, outline=0, fade_in=120
    )
    p = overlay_project("one step\nat a *time*", style)
    d = dialogues(ass_document(p, 1080, 1920))
    assert len(d) == 6  # two lines x (shadow + glow + text layer)
    shadow = [x for x in d if x.startswith("Dialogue: 0,")]
    halo = [x for x in d if x.startswith("Dialogue: 1,")]
    text = [x for x in d if x.startswith("Dialogue: 2,")]
    assert len(shadow) == len(halo) == len(text) == 2
    assert all("&H000000&" in x and r"\blur" in x for x in shadow)
    assert all(r"\an5\pos(540," in x and r"\fad(120,0)" in x for x in d)
    # Block is centred on y=0.65 (1248px): line centres sit half a pitch apart.
    ys = sorted(int(x.split(r"\pos(540,")[1].split(")")[0]) for x in text)
    assert ys[0] < 1248 < ys[1]
    assert abs((ys[0] + ys[1]) / 2 - 1248) <= 1
    # Highlighted word is red in the text layer and has a red blurred halo.
    assert ass_rgb(RED) in text[1] and ass_rgb(RED) not in text[0]
    assert ass_rgb(RED) in halo[1] and r"\blur" in halo[1] and r"\1a&HFF&" in halo[1]
    assert halo[0].count(r"\3a&HFF&") == 1  # plain words get no coloured glow
    assert "&HFFFFFF&" in text[0]
    # No glow -> no halo layer.
    d2 = dialogues(
        ass_document(
            overlay_project("a *b*", Style(highlight=RED, y=0.5)), 1080, 1920
        )
    )
    assert len(d2) == 1 and d2[0].startswith("Dialogue: 2,")


def test_designed_shrinks_to_fit_but_never_grows():
    long_text = "own your *future* and everything else too"
    p = overlay_project(long_text, Style(size=150, highlight=RED, y=0.6, bold=True))
    d = dialogues(ass_document(p, 1080, 1920))[0]
    size = float(d.split(r"\fs")[1].split("}")[0].split("\\")[0])
    assert size < 150
    assert text_width(
        long_text.replace("*", ""), "DejaVu Sans", True, size
    ) <= 1080 * 0.8 + 3
    short = dialogues(
        ass_document(
            overlay_project("hi", Style(size=150, highlight=RED, y=0.6)), 1080, 1920
        )
    )[0]
    assert float(short.split(r"\fs")[1].split("\\")[0].split("}")[0]) == 150.0


def test_designed_overlay_renders_red_text(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EXPORTS_DIR", tmp_path / "out")
    src = tmp_path / "s.mp4"
    fixture_video(src)
    paths = {1: {"path": str(src), "kind": "video"}}
    style = Style(
        font="Liberation Sans",
        bold=True,
        size=150,
        highlight="#FF0000",
        glow=24,
        y=0.5,
        outline=0,
    )
    p = overlay_project("one step\nat a *time*", style, 0, 2)
    p.export.width = 360
    plain = Project(clips=p.clips, export=p.export)
    with_text = render_snapshot(p.model_dump(), paths, lambda *_: None, _CancelFlag())
    without = render_snapshot(plain.model_dump(), paths, lambda *_: None, _CancelFlag())
    a = frame(with_text["video"], 0.5).reshape(-1, 3).astype(int)
    b = frame(without["video"], 0.5).reshape(-1, 3).astype(int)
    assert not np.array_equal(a, b)
    white = ((a > 235).all(axis=1)).sum()
    red = ((a[:, 0] > 200) & (a[:, 1] < 60) & (a[:, 2] < 60)).sum()
    assert white > 50 and red > 50
    assert not (((b[:, 0] > 200) & (b[:, 1] < 60)).any())


def test_shade_controls_shadow_opacity():
    def shadow(shade):
        p = overlay_project("a *b*", Style(highlight=RED, glow=20, y=0.4, shade=shade))
        return next(
            x
            for x in dialogues(ass_document(p, 1080, 1920))
            if x.startswith("Dialogue: 0,")
        )

    assert r"\3a&H60&" in shadow(0.625)  # default matches the original look
    assert r"\3a&H19&" in shadow(0.9)
    assert r"\3a&HFF&" in shadow(0)
    with pytest.raises(ValueError):
        Style(shade=1.5)


def test_timeline_overlays_use_final_time_and_clamp():
    a = Clip(asset_id=1, start=0, end=2, transition="cut")
    b = Clip(asset_id=1, start=5, end=7, transition="cut")
    p = Project(
        clips=[a, b],
        on_video_text=True,
        timeline_overlays=[
            Overlay(start=1.5, end=2.5, text="spans the cut"),
            Overlay(start=3.5, end=9, text="clamped", style=Style(highlight=RED, y=0.3)),
            Overlay(start=4.5, end=5, text="past the end"),
        ],
    )
    d = dialogues(ass_document(p, 1080, 1920, speech=False))
    assert len(d) == 2  # the cue starting after the timeline is dropped
    assert "0:00:01.50,0:00:02.50" in d[0] and "spans the cut" in d[0]
    assert "0:00:03.50,0:00:04.00" in d[1]  # end clamped to the 4s timeline
    # Off unless on_video_text is enabled for the render (ass_document flag).
    assert dialogues(ass_document(p, 1080, 1920, speech=False, overlays=False)) == []


def test_gain_points_validate_and_expression():
    from app.edits.project import Audio, GainPoint
    from app.edits.render_v2 import gain_expr

    with pytest.raises(ValueError):
        Audio(gain_points=[GainPoint(t=2, db=0), GainPoint(t=1, db=-6)])
    with pytest.raises(ValueError):
        Audio(gain_points=[GainPoint(t=1, db=0), GainPoint(t=1, db=-6)])
    assert Audio().gain_points == []
    e = gain_expr([GainPoint(t=0, db=-12), GainPoint(t=2, db=0)])
    assert "pow(10" in e and "lt(t,2" in e


def test_mute_clip_and_gain_automation_render(tmp_path, monkeypatch):
    import subprocess

    from app.edits.project import Audio, GainPoint
    from app.proc import tool_env

    monkeypatch.setattr(config, "EXPORTS_DIR", tmp_path / "out")
    src = tmp_path / "s.mp4"
    fixture_video(src)
    music = tmp_path / "music.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         "sine=frequency=220:duration=3", str(music)],
        check=True,
        env=tool_env(),
    )
    paths = {1: {"path": str(src), "kind": "video"}}

    def pcm(video, t0, t1):
        raw = subprocess.check_output(
            ["ffmpeg", "-v", "error", "-ss", str(t0), "-t", str(t1 - t0), "-i", video,
             "-vn", "-f", "f32le", "-ac", "1", "-"],
            env=tool_env(),
        )
        return np.frombuffer(raw, dtype=np.float32)

    # Muted clip -> silent output even though the source has a tone.
    p = Project(clips=[Clip(asset_id=1, end=3, mute=True)])
    p.export.width = 240
    r = render_snapshot(p.model_dump(), paths, lambda *_: None, _CancelFlag())
    assert np.max(np.abs(pcm(r["video"], 0, 2.5))) < 0.001
    p.clips[0].mute = False
    r = render_snapshot(p.model_dump(), paths, lambda *_: None, _CancelFlag())
    assert np.max(np.abs(pcm(r["video"], 0, 2.5))) > 0.05

    # Music with a -12 dB -> 0 dB ramp and a lower limiter ceiling.
    p.original_audio.enabled = False
    p.ducking = False
    p.music = Audio(
        enabled=True,
        path=str(music),
        volume=0.5,
        gain_points=[GainPoint(t=0, db=-12), GainPoint(t=1, db=-12),
                     GainPoint(t=1.5, db=0)],
    )
    p.export.limit = 0.8
    p.music.path = str(music)
    # media_path validation only runs on save; render reads the path directly.
    r = render_snapshot(p.model_dump(), paths, lambda *_: None, _CancelFlag())
    quiet = np.sqrt(np.mean(pcm(r["video"], 0.2, 0.9) ** 2))
    loud = np.sqrt(np.mean(pcm(r["video"], 2.0, 2.8) ** 2))
    assert 20 * np.log10(loud / quiet) == pytest.approx(12, abs=1.0)
    assert np.max(np.abs(pcm(r["video"], 0, 3))) <= 0.8 + 0.02


def test_speed_one_leaves_clip_audio_untouched(tmp_path, monkeypatch):
    """atempo=1 jitters timing; a speed-1 clip must pass audio through unchanged."""
    import subprocess

    from app.proc import tool_env

    monkeypatch.setattr(config, "EXPORTS_DIR", tmp_path / "out")
    src = tmp_path / "noise.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0x203040:s=320x240:r=30:d=4",
         "-f", "lavfi", "-i", "anoisesrc=d=4:c=pink:r=48000:a=0.3:seed=7", "-shortest",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", str(src)],
        check=True,
        env=tool_env(),
    )
    p = Project(clips=[Clip(asset_id=1, start=0, end=4, transition="cut")])
    p.export.width = 240
    r = render_snapshot(
        p.model_dump(), {1: {"path": str(src), "kind": "video"}}, lambda *_: None, _CancelFlag()
    )

    def pcm(f):
        raw = subprocess.check_output(
            ["ffmpeg", "-v", "error", "-i", f, "-vn", "-f", "f32le", "-ac", "1", "-ar", "48000", "-"],
            env=tool_env(),
        )
        return np.frombuffer(raw, dtype=np.float32).astype(float)

    a, b = pcm(str(src)), pcm(r["video"])
    n = min(len(a), len(b)) - 1000
    # Constant ~5 ms limiter lookahead delay is expected; per-window lag must not wander.
    for start in (0, 48000, 96000, 130000):
        x = a[start : start + 40000]
        best, lag = max(
            (np.corrcoef(x, b[start + lag : start + lag + 40000])[0, 1], lag)
            for lag in range(0, 400, 1)
        )
        assert best > 0.99
        assert abs(lag - 240) <= 3


def test_clips_render_exact_frame_counts_and_auto_level_off(tmp_path, monkeypatch):
    import json
    import subprocess

    from app.proc import tool_env

    monkeypatch.setattr(config, "EXPORTS_DIR", tmp_path / "out")
    src = tmp_path / "s.mp4"
    fixture_video(src)  # 24 fps source rendered at 30 fps
    paths = {1: {"path": str(src), "kind": "video"}}

    def frames(video):
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
             "-show_entries", "stream=nb_read_frames", "-of", "json", video],
            env=tool_env(),
        )
        return int(json.loads(out)["streams"][0]["nb_read_frames"])

    clips = [
        Clip(asset_id=1, start=0, end=1, transition="cut"),
        Clip(asset_id=1, start=0.25, end=1.6, speed=0.75, transition="cut"),  # 1.8 s
        Clip(asset_id=1, start=0, end=0.7, transition="cut"),
    ]
    p = Project(clips=clips)
    p.export.width, p.export.fps = 240, 30
    r = render_snapshot(p.model_dump(), paths, lambda *_: None, _CancelFlag())
    assert frames(r["video"]) == 30 + 54 + 21
    p.clips[1].transition = "crossfade"  # 0.3 s = 9 frames overlap
    r = render_snapshot(p.model_dump(), paths, lambda *_: None, _CancelFlag())
    assert frames(r["video"]) == 30 + 54 + 21 - 9

    # auto_level=False: limiter is a pure ceiling (no re-normalisation boost).
    p.export.limit, p.export.auto_level = 0.2, False
    r = render_snapshot(p.model_dump(), paths, lambda *_: None, _CancelFlag())
    raw = subprocess.check_output(
        ["ffmpeg", "-v", "error", "-i", r["video"], "-vn", "-f", "f32le", "-ac", "1", "-"],
        env=tool_env(),
    )
    assert np.max(np.abs(np.frombuffer(raw, dtype=np.float32))) <= 0.2 + 0.03


def test_crossfade_start_is_frame_exact_despite_float_noise(tmp_path, monkeypatch):
    """Clip lengths a few microseconds over a frame multiple made xfade pick the
    previous incoming frame, shifting everything after the dissolve by one frame."""
    import subprocess

    from app.proc import tool_env

    monkeypatch.setattr(config, "EXPORTS_DIR", tmp_path / "out")
    a_src, b_src = tmp_path / "a.mp4", tmp_path / "b.mp4"
    for f, lavfi in ((a_src, "color=c=red:s=160x284:r=30:d=3"), (b_src, "testsrc2=s=160x284:r=30:d=3")):
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", lavfi, "-c:v", "libx264",
             "-pix_fmt", "yuv420p", "-crf", "10", str(f)],
            check=True,
            env=tool_env(),
        )
    paths = {1: {"path": str(a_src), "kind": "video"}, 2: {"path": str(b_src), "kind": "video"}}
    # 1.000004 s first clip -> second clip starts at 0.700004 s (frame 21 + 4 us noise)
    p = Project(
        clips=[
            Clip(asset_id=1, start=0, end=1.000004, transition="crossfade"),
            Clip(asset_id=2, start=0, end=1.0, transition="cut"),
        ]
    )
    p.export.width, p.export.fps = 160, 30
    r = render_snapshot(p.model_dump(), paths, lambda *_: None, _CancelFlag())

    def gray(path, t):
        return frame(path, t).reshape(-1, 3).mean(1)

    # Output frame 30 is past the 9-frame dissolve: it must be second-clip frame 9.
    out = gray(r["video"], 30 / 30 + 0.001)
    errs = [np.abs(out - gray(str(b_src), k / 30 + 0.001)).mean() for k in range(0, 14)]
    assert int(np.argmin(errs)) == 9


def test_input_seek_keeps_frame_selection(tmp_path, monkeypatch):
    """Clips starting late in a file are seeked to, not decoded from 0; same frames out."""
    import subprocess

    from app.proc import tool_env

    monkeypatch.setattr(config, "EXPORTS_DIR", tmp_path / "out")
    src = tmp_path / "t.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x284:r=30:d=8",
         "-f", "lavfi", "-i", "sine=d=8", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-crf", "8", "-g", "30", str(src)],
        check=True,
        env=tool_env(),
    )
    paths = {1: {"path": str(src), "kind": "video"}}
    p = Project(clips=[Clip(asset_id=1, start=5, end=6.5, transition="cut")])
    p.export.width, p.export.fps = 160, 30
    r = render_snapshot(p.model_dump(), paths, lambda *_: None, _CancelFlag())

    def gray(path, t):
        return frame(path, t).reshape(-1, 3).mean(1)

    # First output frame must be source frame 150 (5.0 s), then 151.
    errs = [np.abs(gray(r["video"], 0.001) - gray(str(src), (k / 30) + 0.001)).mean() for k in range(146, 155)]
    assert 146 + int(np.argmin(errs)) == 150
    errs = [np.abs(gray(r["video"], 1 / 30 + 0.001) - gray(str(src), (k / 30) + 0.001)).mean() for k in range(146, 155)]
    assert 146 + int(np.argmin(errs)) == 151
