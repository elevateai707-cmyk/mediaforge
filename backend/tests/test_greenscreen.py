"""Green-screen plans: chroma key / matting helpers, backgrounds, subject layer, plan + render."""
import json
import subprocess

import numpy as np
import pytest
from PIL import Image, ImageDraw

from app import models
from app.db import SessionLocal
from app.edits import cards, greenscreen, planner, render, segment, speech, story
from .test_beat_sync import long_assets  # noqa: F401

pytestmark = pytest.mark.usefixtures("cleanup_assets")
SKIN, SHIRT, GREEN = (224, 172, 140), (40, 70, 160), (0, 200, 40)


def _person(w=404, h=720, bg=GREEN, shift=0):
    """A drawn 'person': head ellipse + shoulders on a flat background. Returns (rgb, truth mask)."""
    im = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(im)
    cx = w // 2 + shift
    d.ellipse([cx - 60, h // 3 - 80, cx + 60, h // 3 + 60], fill=SKIN)
    d.rectangle([cx - 20, h // 3 + 50, cx + 20, h // 3 + 110], fill=SKIN)
    d.rounded_rectangle([cx - 130, h // 3 + 100, cx + 130, h], radius=50, fill=SHIRT)
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse([cx - 60, h // 3 - 80, cx + 60, h // 3 + 60], fill=255)
    md.rectangle([cx - 20, h // 3 + 50, cx + 20, h // 3 + 110], fill=255)
    md.rounded_rectangle([cx - 130, h // 3 + 100, cx + 130, h], radius=50, fill=255)
    return np.asarray(im), np.asarray(mask) > 127


def _write_video(path, frames, fps=30, audio_s=None):
    h, w = frames[0].shape[:2]
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-"]
    if audio_s:
        cmd += ["-f", "lavfi", "-i", f"sine=f=220:d={audio_s}", "-shortest", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in frames:
        p.stdin.write(f.tobytes())
    p.stdin.close()
    assert p.wait() == 0
    return path


# ---------------------------------------------------------------------------
# chroma key + finishing
# ---------------------------------------------------------------------------
def test_chroma_key_matches_the_subject_and_removes_spill():
    rgb, truth = _person()
    assert segment.detect_green_screen([rgb, rgb]) and not segment.detect_green_screen([_person(bg=(120, 120, 125))[0]])
    alpha, despilled = segment.chroma_key(rgb)
    got = alpha > 0.5
    iou = (got & truth).sum() / (got | truth).sum()
    assert iou > 0.97, iou
    inside = alpha > 0.9
    assert (despilled[..., 1][inside].astype(int) - np.maximum(despilled[..., 0], despilled[..., 2])[inside].astype(int)).max() <= 12
    assert alpha[:20, :20].max() < 0.05                                 # the screen is gone


def test_refine_alpha_cleans_halo_and_smooths_in_time():
    a = np.zeros((40, 40), np.float32)
    a[10:30, 10:30] = 1.0
    a[10:30, 4:10] = 0.04                                               # faint halo around the edge
    r = segment.refine_alpha(a)
    assert r[20, 5] < 0.01 and r[20, 20] > 0.98
    prev = r.copy()
    noisy = np.clip(r + 0.05, 0, 1)
    smoothed = segment.refine_alpha(noisy, prev)
    assert abs(smoothed[5, 5] - prev[5, 5]) < abs(segment.refine_alpha(noisy)[5, 5] - prev[5, 5]) + 1e-9
    moved = np.roll(r, 15, axis=1)
    assert np.abs(segment.refine_alpha(moved, r) - segment.refine_alpha(moved)).max() < 0.12   # real motion passes through


def test_decontaminate_pulls_edge_colour_to_the_subject():
    h = w = 60
    rgb = np.zeros((h, w, 3), np.uint8)
    rgb[:] = SKIN
    alpha = np.zeros((h, w), np.float32)
    alpha[:, :30] = 1.0
    rgb[:, 28:31] = (60, 190, 60)                                       # green fringe on the edge
    alpha[:, 29] = 0.5
    out = segment.decontaminate(rgb, alpha)
    assert abs(int(out[30, 29, 1]) - SKIN[1]) < abs(int(rgb[30, 29, 1]) - SKIN[1])
    assert (out[30, 5] == rgb[30, 5]).all()                             # interior untouched


def test_shadow_and_rim_only_adds_alpha_below_right():
    alpha = np.zeros((200, 200), np.float32)
    alpha[40:120, 60:140] = 1.0
    rgb = np.full((200, 200, 3), 200, np.uint8)
    out_rgb, out_a = segment.shadow_and_rim(alpha, rgb)
    assert (out_a >= alpha - 1e-6).all()
    assert out_a[130, 100] > 0.05 and out_a[20, 100] < 0.03             # shadow falls below, not above
    assert out_a[80, 100] > 0.99
    over = segment.compose_over(np.full((200, 200, 3), 255, np.uint8), out_rgb, out_a)
    assert over[130, 100].sum() < 3 * 250                               # background darkened by the shadow


def test_layer_geometry():
    g = greenscreen.layer_geometry(1080, 1920, 0.8, "lower")
    assert (g["w"], g["h"]) == (864, 1536) and g["y"] + g["h"] == 1920 and g["x"] == 108
    c = greenscreen.layer_geometry(1080, 1920, 0.7, "center")
    assert c["y"] == (1920 - c["h"]) // 2
    big = greenscreen.layer_geometry(1080, 1920, 1.2, "lower")
    assert big["h"] <= 1920


# ---------------------------------------------------------------------------
# backgrounds, beats
# ---------------------------------------------------------------------------
BEATS = [{"start": 0.0, "end": 5.0}, {"start": 5.0, "end": 10.0}, {"start": 10.0, "end": 15.0}, {"start": 15.0, "end": 20.0}]


def test_assign_backgrounds_modes():
    stat = {"kind": "stat", "text": "7 in 10"}
    head = {"kind": "headline", "text": "Big news"}
    items = greenscreen.normalise_backgrounds([stat, head])
    news = greenscreen.assign_backgrounds("news", items, BEATS)
    assert [b["card"]["kind"] for b in news] == ["stat", "headline", "stat", "headline"]          # cycles
    timed = greenscreen.assign_backgrounds("news", greenscreen.normalise_backgrounds([{**head, "at": 12.0}, stat]), BEATS)
    assert timed[2]["card"]["kind"] == "headline" and timed[0]["card"]["kind"] == "stat"
    assert [b["card"]["kind"] for b in greenscreen.assign_backgrounds("news", [], BEATS)] == ["ambient"] * 4
    drone = greenscreen.assign_backgrounds("drone", greenscreen.normalise_backgrounds(["aerial city", {"hint": "highway"}]), BEATS)
    assert all(b["card"] is None for b in drone) and [b["hint"] for b in drone] == ["aerial city", "highway", "aerial city", "highway"]
    mixed = greenscreen.assign_backgrounds("mixed", greenscreen.normalise_backgrounds([stat, "aerial city"]), BEATS)
    assert mixed[0]["card"]["kind"] == "stat" and mixed[1]["hint"] == "aerial city" and mixed[2] == {"card": None, "hint": None}
    for bad_mode, bad_items in (("drone", [stat]), ("news", ["aerial city"]), ("nope", [])):
        with pytest.raises(greenscreen.GreenscreenError):
            greenscreen.assign_backgrounds(bad_mode, greenscreen.normalise_backgrounds(bad_items), BEATS)
    for bad in ([{"x": 1}], [{"kind": "poster", "text": "x"}], [5], [{"hint": "a", "at": "soon"}]):
        with pytest.raises(greenscreen.GreenscreenError):
            greenscreen.normalise_backgrounds(bad)


def _words(n, step=0.5, start=0.2):
    return [{"word": ("w%d." % i if i % 6 == 5 else "w%d" % i), "start": round(start + i * step, 3),
             "end": round(start + i * step + step * 0.8, 3)} for i in range(n)]


def test_group_beats_is_contiguous_and_sentence_aware():
    words = _words(40)
    total = 20.5
    beats = speech.group_beats(words, total)
    assert beats[0]["start"] == 0.0 and beats[-1]["end"] == total
    for a, b in zip(beats, beats[1:]):
        assert a["end"] == pytest.approx(b["start"])
    assert sum(len(b["words"]) for b in beats) == len(words)
    for b in beats[:-1]:
        assert 3.0 <= b["end"] - b["start"] <= 10.5
        assert b["words"][-1]["word"].endswith(".") or b["end"] - b["start"] >= 8.5     # closes on a sentence
        assert all(0 <= w["start"] <= b["end"] - b["start"] for w in b["words"])         # beat-local times
    assert speech.group_beats([], 5.0) == [{"start": 0.0, "end": 5.0, "words": []}]
    late = speech.group_beats(_words(60), 12.0)                          # words past the audio end are dropped
    assert late[-1]["end"] == 12.0 and all(b["end"] > b["start"] for b in late)


# ---------------------------------------------------------------------------
# subject layer + plan
# ---------------------------------------------------------------------------
@pytest.fixture()
def chroma_clip(tmp_path):
    frames = [_person(shift=int(8 * np.sin(i / 6)))[0] for i in range(75)]       # 2.5 s
    return _write_video(tmp_path / "selfie_green.mp4", frames, audio_s=2.5)


def test_process_subject_chroma_layers(chroma_clip, tmp_path):
    info = greenscreen.process_subject(str(chroma_clip), tmp_path / "out", 432, 768, 2.5)
    assert info["keying"] == "chroma" and info["frames"] >= 70 and info["processing_fps"] > 3
    alpha = subprocess.run(["ffmpeg", "-v", "error", "-i", info["alpha"], "-frames:v", "1", "-vf", "format=gray",
                            "-f", "rawvideo", "-"], capture_output=True).stdout
    a = np.frombuffer(alpha, np.uint8).reshape(768, 432)
    assert a[300, 216] > 230 and a[20, 20] < 10 and a[700, 216] > 200               # head solid, corner clear, shirt solid


def test_segment_path_uses_the_segmenter(tmp_path):
    class Fake:
        resets = 0

        def reset(self):
            Fake.resets += 1

        def alpha(self, rgb):
            return (np.abs(rgb.astype(int) - np.array([120, 120, 125])).sum(axis=-1) > 60).astype(np.float32)

    gray = [_person(bg=(120, 120, 125))[0] for _ in range(40)]
    clip = _write_video(tmp_path / "gray.mp4", gray)
    info = greenscreen.process_subject(str(clip), tmp_path / "out", 432, 768, 1.3, segmenter=Fake())
    assert info["keying"] == "segment" and Fake.resets >= 1 and info["frames"] >= 35


def test_preflight_errors(tmp_path):
    ok = dict(background_mode="news", backgrounds=None, subject_position="lower", subject_scale=0.8, brand=None,
              hook_text=None, music_path=None, ratio="9:16", keying="auto", broll_filter=None)
    with pytest.raises(greenscreen.GreenscreenError, match="existing recording"):
        greenscreen.preflight("/nope.mp4", None, **ok)
    silent = tmp_path / "silent.mp4"
    _write_video(silent, [_person()[0]] * 60)
    with pytest.raises(greenscreen.GreenscreenError, match="no audio"):
        greenscreen.preflight(str(silent), None, **ok)
    with pytest.raises(greenscreen.GreenscreenError, match="9:16"):
        greenscreen.preflight(str(silent), None, **{**ok, "ratio": "16:9"})
    with pytest.raises(greenscreen.GreenscreenError, match="subject_scale"):
        greenscreen.preflight(str(silent), None, **{**ok, "subject_scale": 3})
    with pytest.raises(greenscreen.GreenscreenError, match="unknown brand"):
        greenscreen.preflight(str(silent), None, **{**ok, "brand": "nope"})


def test_plan_and_render_with_chroma_recording(client, long_assets, chroma_clip, monkeypatch):  # noqa: F811
    words = [{"word": w, "start": 0.2 + i * 0.35, "end": 0.2 + i * 0.35 + 0.28}
             for i, w in enumerate("Hello there. This week two flaws hit PHP. Update now.".split())]
    monkeypatch.setattr(story, "_default_search", lambda db, q, limit: long_assets)
    plan = greenscreen.create_greenscreen_plan(
        source_path=str(chroma_clip), background_mode="mixed",
        backgrounds=[{"kind": "stat", "text": "7 in 10", "sub": "websites run PHP"}, "aerial city"],
        brand="albertaweb", hook_text="Stop scrolling", title="selfie",
        broll_filter={"min_smoothness": None, "hyperlapse": None}, transcriber=lambda path: words)
    pid = plan["plan_id"]
    sc = story.get_story(pid)
    assert sc["kind"] == "greenscreen" and sc["subject"]["keying"] == "chroma" and sc["captions"]
    assert sc["caption_layout"] == {"body_y": greenscreen.BODY_Y, "emphasis": False}
    assert sc["duration"] == pytest.approx(plan["total_duration"], abs=0.01) and sc["duration"] > 2.0
    assert sc["beats"][0]["visual"]["card"]["kind"] == "stat" and all(b["words"] for b in sc["beats"])
    assert Path(sc["voiceover"]).is_file() and Path(sc["subject"]["rgb"]).is_file()
    with SessionLocal() as db:
        planner.validate_story_sync(db.get(models.EditPlan, pid), sc)             # story-shaped: sync passes
    assert client.post(f"/api/edits/plan/{pid}/approve").status_code == 200
    out = render.render_plan(pid, ratio="9:16", width=270, height=480)
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", out],
                                     capture_output=True, text=True).stdout)
    assert {s["codec_type"] for s in info["streams"]} == {"video", "audio"}
    assert float(info["format"]["duration"]) == pytest.approx(sc["duration"] + 1.8, abs=0.5)    # plus the brand card
    frame = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1.0", "-i", out, "-frames:v", "1", "-f", "rawvideo",
                            "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    img = np.frombuffer(frame, np.uint8).reshape(480, 270, 3).astype(int)
    skin = (np.abs(img - np.array(SKIN)).sum(axis=-1) < 70)
    assert skin[150:320].sum() > 300                                             # the person is composited in
    green = (img[..., 1] - np.maximum(img[..., 0], img[..., 2]) > 90)
    assert green.sum() < 60                                                      # and the green screen is gone


def test_render_refuses_missing_subject_files(client, long_assets, chroma_clip, monkeypatch):  # noqa: F811
    monkeypatch.setattr(story, "_default_search", lambda db, q, limit: long_assets)
    plan = greenscreen.create_greenscreen_plan(source_path=str(chroma_clip), background_mode="drone",
                                               broll_filter={"min_smoothness": None, "hyperlapse": None},
                                               captions=False)
    pid = plan["plan_id"]
    client.post(f"/api/edits/plan/{pid}/approve")
    Path(story.get_story(pid)["subject"]["rgb"]).unlink()
    with pytest.raises(render.RenderError, match="subject layer"):
        render.render_plan(pid, ratio="9:16", width=270, height=480)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def test_greenscreen_route_validates_and_runs(client, wait_job, long_assets, chroma_clip, monkeypatch):  # noqa: F811
    assert client.post("/api/edits/greenscreen", json={"source_path": "/nope.mp4"}).status_code == 422
    assert client.post("/api/edits/greenscreen", json={"source_path": str(chroma_clip), "background_mode": "drone",
                                                       "backgrounds": [{"kind": "stat", "text": "x"}]}).status_code == 422
    assert client.post("/api/edits/greenscreen", json={"source_path": str(chroma_clip), "ratio": "16:9"}).status_code == 422
    monkeypatch.setattr(story, "_default_search", lambda db, q, limit: long_assets)
    monkeypatch.setattr(speech, "transcribe_words", lambda path, language="en": [
        {"word": "Hello", "start": 0.2, "end": 0.5}, {"word": "world.", "start": 0.6, "end": 1.0}])
    r = client.post("/api/edits/greenscreen", json={
        "source_path": str(chroma_clip), "background_mode": "news", "title": "api",
        "broll_filter": {"min_smoothness": None, "hyperlapse": None}})
    assert r.status_code == 200, r.text
    job = wait_job(client, r.json()["job_id"], timeout=180)
    assert job["status"] == "done", job
    pid = job["meta"]["plan_id"]
    plan = client.get(f"/api/edits/plan/{pid}").json()
    assert plan["story"]["beat_count"] >= 1 and job["meta"]["greenscreen"]["keying"] == "chroma"


from pathlib import Path  # noqa: E402
