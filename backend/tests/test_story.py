"""Story voiceover pipeline. All ElevenLabs HTTP is mocked; no real calls."""
import base64
import hashlib
import json
import shutil
import subprocess

import pytest

from app import config, models
from app.db import SessionLocal
from app.edits import render, story


# ---------------------------------------------------------------------------
# pure functions
# ---------------------------------------------------------------------------
def _para(n_sentences, words_each=8):
    return " ".join(
        " ".join(f"w{i}x{j}" for j in range(words_each)) + "." for i in range(n_sentences)
    )


def test_split_beats_paragraphs_and_empty():
    assert story.split_beats("") == []
    assert story.split_beats("   \n\n  ") == []
    beats = story.split_beats("First short para.\n\nSecond short para.")
    assert beats == ["First short para.", "Second short para."]


def test_split_beats_long_paragraph_is_split():
    text = _para(12)  # 96 words
    beats = story.split_beats(text)
    assert len(beats) > 1
    assert " ".join(beats) == text
    for b in beats:
        assert len(b.split()) <= story.BEAT_MAX_WORDS
        assert b.rstrip().endswith(".")  # split on sentence boundaries only


def test_alignment_to_words():
    text = "Hi there."
    alignment = {
        "characters": list(text),
        "character_start_times_seconds": [i * 0.1 for i in range(len(text))],
        "character_end_times_seconds": [i * 0.1 + 0.1 for i in range(len(text))],
    }
    words = story.alignment_to_words(alignment)
    assert [w["word"] for w in words] == ["Hi", "there."]
    assert words[0]["start"] == pytest.approx(0.0)
    assert words[0]["end"] == pytest.approx(0.2)
    assert words[1]["start"] == pytest.approx(0.3)
    assert words[1]["end"] == pytest.approx(0.9)


def _words(text, t0=0.0, step=0.4):
    out = []
    for i, w in enumerate(text.split()):
        out.append({"word": w, "start": t0 + i * step, "end": t0 + i * step + step * 0.9})
    return out


def test_chunking_limits_and_punctuation():
    words = _words("one two three four five six seven eight nine ten")
    lines = story.chunk_words(words)
    assert [len(x) for x in lines] == [5, 5]
    words = _words("Hello there, my friend. Welcome back")
    lines = story.chunk_words(words)
    assert [" ".join(w["word"] for w in x) for x in lines] == [
        "Hello there,", "my friend.", "Welcome back"]
    long = _words("extraordinarily magnificent unbelievable landscapes")
    for line in story.chunk_words(long):
        assert len(" ".join(w["word"] for w in line)) <= 32 or len(line) == 1


def test_build_srt_format_and_offsets():
    beats = [
        {"start": 0.0, "words": _words("Hello world.")},
        {"start": 10.0, "words": _words("Second beat here.", step=0.5)},
    ]
    srt = story.build_srt(beats)
    blocks = srt.strip().split("\n\n")
    assert len(blocks) == 2
    first = blocks[0].splitlines()
    assert first[0] == "1"
    assert first[1] == "00:00:00,000 --> 00:00:00,760"
    assert first[2] == "Hello world."
    second = blocks[1].splitlines()
    assert second[0] == "2"
    assert second[1].startswith("00:00:10,000 --> 00:00:11,")
    assert second[2] == "Second beat here."


def test_srt_time_hours():
    assert story._srt_time(3661.5) == "01:01:01,500"


# ---------------------------------------------------------------------------
# clip allocation
# ---------------------------------------------------------------------------
def _cand(aid, dur, start=None):
    return {"asset_id": aid, "scene_id": None, "start": start, "duration": dur,
            "caption": None, "score": 1.0}


def test_allocate_single_long_asset_skips_takeoff():
    used = set()
    clips = story.allocate_clips(6.3, [_cand(1, 60.0)], used)
    assert len(clips) == 1
    assert clips[0]["start"] == story.TAKEOFF_SKIP
    assert clips[0]["end"] - clips[0]["start"] == pytest.approx(6.3)
    assert used == {1}


def test_allocate_uses_scene_start():
    clips = story.allocate_clips(4.0, [_cand(1, 60.0, start=12.5)], set())
    assert clips[0]["start"] == 12.5


def test_allocate_short_asset_spills_to_next_clip():
    used = set()
    clips = story.allocate_clips(7.0, [_cand(1, 3.0), _cand(2, 2.5), _cand(3, 30.0)], used)
    assert [c["asset_id"] for c in clips] == [1, 2, 3]
    total = sum(c["end"] - c["start"] for c in clips)
    assert total == pytest.approx(7.0, abs=0.01)
    assert clips[0]["end"] - clips[0]["start"] == pytest.approx(3.0)


def test_allocate_prefers_unused_then_reuses():
    used = {1}
    clips = story.allocate_clips(2.0, [_cand(1, 30.0), _cand(2, 30.0)], used)
    assert clips[0]["asset_id"] == 2
    # only asset 1 exists and it is used: reuse rather than fail
    clips = story.allocate_clips(2.0, [_cand(1, 30.0)], {1})
    assert clips[0]["asset_id"] == 1


def test_allocate_no_candidates():
    with pytest.raises(story.StoryError):
        story.allocate_clips(2.0, [], set())


def test_is_aerial():
    assert story.is_aerial("/x/blue-drone-1o1/clip.mp4", None)
    assert story.is_aerial("/x/DJI_0042.MP4", None)
    assert story.is_aerial("/x/a.mp4", "An aerial view of cliffs")
    assert not story.is_aerial("/x/a.mp4", "a dog on a couch")


# ---------------------------------------------------------------------------
# ElevenLabs client (HTTP mocked)
# ---------------------------------------------------------------------------
def _silent_mp3(path, seconds=1.5):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         f"anullsrc=r=44100:cl=mono:d={seconds}", "-c:a", "libmp3lame", str(path)],
        check=True, capture_output=True)
    return path.read_bytes()


@pytest.fixture()
def fake_tts(monkeypatch, tmp_path):
    audio = _silent_mp3(tmp_path / "silent.mp3")
    calls = []

    def _http(url, headers, payload):
        calls.append({"url": url, "headers": headers, "payload": payload})
        text = payload["text"]
        return 200, {
            "audio_base64": base64.b64encode(audio).decode(),
            "alignment": {
                "characters": list(text),
                "character_start_times_seconds": [i * 0.03 for i in range(len(text))],
                "character_end_times_seconds": [i * 0.03 + 0.03 for i in range(len(text))],
            },
        }

    monkeypatch.setattr(story, "_tts_http", _http)
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "test-key-not-real")
    return calls


def test_synthesize_requires_key(monkeypatch):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "")
    with pytest.raises(story.StoryError, match="ELEVENLABS_API_KEY"):
        story.synthesize_beat("hi", "voice")


def test_synthesize_request_shape(fake_tts):
    audio, alignment = story.synthesize_beat("Hello there.", "VID", "prev", "next")
    call = fake_tts[0]
    assert "/v1/text-to-speech/VID/with-timestamps" in call["url"]
    assert "output_format=mp3_44100_192" in call["url"]
    assert call["headers"]["xi-api-key"] == "test-key-not-real"
    p = call["payload"]
    assert p["previous_text"] == "prev" and p["next_text"] == "next"
    assert p["model_id"] == config.ELEVENLABS_MODEL
    assert p["voice_settings"]["similarity_boost"] == 0.85
    assert audio and alignment["characters"]


def test_synthesize_http_error_hides_key(monkeypatch):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "sekrit-value")
    monkeypatch.setattr(story, "_tts_http", lambda *a: (
        401, {"detail": {"status": "invalid_api_key", "message": "bad key"}}))
    with pytest.raises(story.StoryError) as ei:
        story.synthesize_beat("hi", "v")
    assert "401" in str(ei.value) and "bad key" in str(ei.value)
    assert "sekrit-value" not in str(ei.value)


# ---------------------------------------------------------------------------
# full pipeline
# ---------------------------------------------------------------------------
@pytest.fixture()
def story_assets(client, video_library):
    """Register the 2s test videos as assets (one DJI-named = aerial)."""
    ids = {}
    with SessionLocal() as db:
        for name, aerial in (("plain_a.mp4", False), ("DJI_0001.mp4", True)):
            dst = video_library / name
            shutil.copyfile(video_library / "render_a.mp4", dst)
            a = models.Asset(
                path=str(dst), hash=hashlib.md5(str(dst).encode()).hexdigest(),
                kind="video", width=320, height=180, duration=2.0, status="indexed")
            db.add(a)
            db.flush()
            ids[name] = a.id
        db.commit()
    return ids


SCRIPT = "We rose above the valley at dawn.\n\nThe river cut silver through the forest."


def test_create_story_plan(client, fake_tts, story_assets):
    ids = list(story_assets.values())
    plan = story.create_story_plan(
        SCRIPT, trip_id=None, ratio="16:9", title="Valley",
        cloud_approved=True, voice_consent=True,
        search_fn=lambda db, q, limit: ids)
    pid = plan["plan_id"]
    assert plan["status"] == "draft"
    assert plan["intent"] == "story: Valley"
    assert plan["story"]["beat_count"] == 2

    # voice id default + prosody context passed
    assert fake_tts[0]["url"].count(config.ELEVENLABS_VOICE_ID) == 1
    assert "previous_text" not in fake_tts[0]["payload"]
    assert fake_tts[0]["payload"]["next_text"].startswith("The river")
    assert fake_tts[1]["payload"]["previous_text"].startswith("We rose")

    sc = story.get_story(pid)
    assert sc["voice_id"] == config.ELEVENLABS_VOICE_ID
    d = story_dir = story.story_dir(pid)
    for f in ("beat_01.mp3", "beat_02.mp3", "voiceover.mp3", "captions.srt", "story.json"):
        assert (d / f).is_file(), f
    assert "key" not in json.dumps(sc).lower().replace("keyframe", "")

    # clips line up with slots and the voiceover
    beats = sc["beats"]
    assert beats[1]["start"] == pytest.approx(beats[0]["slot"], abs=1e-3)
    for b in beats:
        dur = sum(c["end"] - c["start"] for c in b["clips"])
        assert dur == pytest.approx(b["slot"], abs=0.02)
        assert b["slot"] == pytest.approx(b["audio_duration"] + story.BEAT_GAP, abs=1e-3)
    clip_total = sum(c["end"] - c["start"] for c in plan["clips"])
    assert clip_total == pytest.approx(sc["duration"], abs=0.05)
    assert story.probe_duration(str(d / "voiceover.mp3")) == pytest.approx(
        sc["duration"], abs=0.15)
    assert plan["clips"][0]["caption"] == beats[0]["text"]

    # aerial asset ranked first, and not reused while unused assets remain
    assert [c["asset_id"] for c in plan["clips"]][:2] == [
        story_assets["DJI_0001.mp4"], story_assets["plain_a.mp4"]]
    srt = (d / "captions.srt").read_text()
    assert "-->" in srt and "We rose" in srt

    # plan GET carries story info; sidecar endpoint works
    r = client.get(f"/api/edits/plan/{pid}")
    assert r.json()["story"]["beat_count"] == 2
    r = client.get(f"/api/edits/{pid}/story")
    assert r.status_code == 200 and r.json()["plan_id"] == pid
    assert client.get("/api/edits/nope/story").status_code == 404


def test_story_endpoint_job(client, wait_job, fake_tts, story_assets, monkeypatch):
    ids = list(story_assets.values())
    monkeypatch.setattr(story, "_default_search", lambda db, q, limit: ids)
    r = client.post("/api/edits/story", json={"script": SCRIPT, "ratio": "16:9",
                                            "cloud_approved": True, "voice_consent": True})
    assert r.status_code == 200, r.text
    job = wait_job(client, r.json()["job_id"])
    assert job["status"] == "done", job
    assert job["meta"]["plan_id"]
    assert client.get(f"/api/edits/plan/{job['meta']['plan_id']}").status_code == 200


def test_story_endpoint_validation(client, monkeypatch):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "")
    assert client.post("/api/edits/story", json={"script": "hi"}).status_code == 503
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "x")
    assert client.post("/api/edits/story", json={"script": "hi", "ratio": "4:3"}).status_code == 422


# ---------------------------------------------------------------------------
# render + exports
# ---------------------------------------------------------------------------
def _approved_story(client, ids):
    plan = story.create_story_plan(SCRIPT, cloud_approved=True, voice_consent=True,
                                  search_fn=lambda db, q, limit: ids)
    assert client.post(f"/api/edits/plan/{plan['plan_id']}/approve").status_code == 200
    return plan


def _capture_ffmpeg(monkeypatch):
    seen = []
    real = subprocess.run

    def _run(cmd, *a, **kw):
        if cmd and cmd[0] == "ffmpeg" and "-filter_complex" in cmd:
            seen.append(cmd)
        return real(cmd, *a, **kw)

    monkeypatch.setattr(render.subprocess, "run", _run)
    return seen


def test_render_story_plan(client, fake_tts, story_assets, monkeypatch):
    plan = _approved_story(client, list(story_assets.values()))
    pid = plan["plan_id"]
    seen = _capture_ffmpeg(monkeypatch)
    out = render.render_plan(pid, ratio="16:9", width=320, captions=True,
                             caption_style="classic")
    cmd = seen[-1]
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert story.story_dir(pid).joinpath("voiceover.mp3").as_posix() in cmd
    assert "subtitles=" in graph and "captions.srt" in graph
    assert "drawtext" not in graph
    assert "concat=n=" in graph and "xfade" not in graph
    assert "acrossfade" not in graph
    assert "anullsrc" not in " ".join(cmd)  # clip audio is not used at all
    assert "[0:a]" not in graph and "[1:a]" not in graph

    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
         "-show_streams", out], capture_output=True, text=True, check=True).stdout)
    kinds = {s["codec_type"] for s in probe["streams"]}
    assert kinds == {"video", "audio"}
    sc = story.get_story(pid)
    assert float(probe["format"]["duration"]) == pytest.approx(sc["duration"], abs=0.3)
    assert out.endswith(".mp4")
    assert shutil.os.path.isfile(out[:-4] + ".srt")


def test_render_story_captions_off_and_music(client, fake_tts, story_assets, monkeypatch,
                                             tmp_path):
    plan = _approved_story(client, list(story_assets.values()))
    music = tmp_path / "music.mp3"
    _silent_mp3(music, 1.0)
    seen = _capture_ffmpeg(monkeypatch)
    out = render.render_plan(plan["plan_id"], ratio="16:9", width=320,
                             captions=False, music_path=str(music),
                             caption_style="classic")
    graph = seen[-1][seen[-1].index("-filter_complex") + 1]
    assert "subtitles=" not in graph
    assert "sidechaincompress" in graph and "loudnorm" in graph
    assert shutil.os.path.isfile(out)


def test_render_non_story_unchanged(client, approved_plan, monkeypatch):
    pid = approved_plan()
    seen = _capture_ffmpeg(monkeypatch)
    out = render.render_plan(pid, ratio="9:16", width=180, captions=True)
    graph = seen[-1][seen[-1].index("-filter_complex") + 1]
    assert "subtitles=" not in graph
    assert "voiceover" not in " ".join(seen[-1])
    assert shutil.os.path.isfile(out)
    assert not shutil.os.path.exists(out[:-4] + ".srt")


def test_exports_include_story_files(client, fake_tts, story_assets):
    plan = _approved_story(client, list(story_assets.values()))
    pid = plan["plan_id"]
    for ep in ("fcpxml", "edl", "capcut"):
        r = client.post(f"/api/export/{ep}", json={"plan_id": pid})
        assert r.status_code == 200, (ep, r.text)
        files = r.json()["story_files"]
        assert shutil.os.path.isfile(files["voiceover"])
        assert shutil.os.path.isfile(files["srt"])
    xml = open(client.post("/api/export/fcpxml", json={"plan_id": pid}).json()["path"]).read()
    assert "voiceover.mp3" in xml and 'lane="-1"' in xml


# ---------------------------------------------------------------------------
# kinetic captions
# ---------------------------------------------------------------------------
from app.edits import kinetic  # noqa: E402


def test_parse_emphasis():
    assert kinetic.parse_emphasis("plain words here") == ("plain words here", [])
    assert kinetic.parse_emphasis("the city *wakes up*.") == ("the city wakes up.", [(2, 4)])
    assert kinetic.parse_emphasis("so *honest*, really") == ("so honest, really", [(1, 2)])
    clean, spans = kinetic.parse_emphasis("go *far away")  # unclosed runs to the end
    assert clean == "go far away" and spans == [(1, 3)]
    assert "*" not in kinetic.parse_emphasis("*a* *b*")[0]


def test_auto_emphasis():
    words = "We rose above the valley at dawn today".split()
    assert kinetic.auto_emphasis(words) == [(4, 5)]  # valley, longest >= 6 letters
    assert kinetic.auto_emphasis("it is so big".split()) == []
    # stopwords excluded even when longest
    assert kinetic.auto_emphasis("because rivers".split()) == [(1, 2)]


def test_script_markup_stripped_before_tts_and_stored(client, fake_tts, story_assets):
    ids = list(story_assets.values())
    plan = story.create_story_plan(
        "We rose above the *valley* at dawn.\n\nThe river cut *silver light* through trees.",
        cloud_approved=True, voice_consent=True,
        search_fn=lambda db, q, limit: ids)
    for call in fake_tts:
        assert "*" not in call["payload"]["text"]
        assert "*" not in call["payload"].get("next_text", "")
        assert "*" not in call["payload"].get("previous_text", "")
    sc = story.get_story(plan["plan_id"])
    assert sc["beats"][0]["emphasis"] == [[4, 5]] and not sc["beats"][0]["emphasis_auto"]
    assert sc["beats"][1]["emphasis"] == [[3, 5]]
    assert sc["caption_style"] == "kinetic"
    assert (story.story_dir(plan["plan_id"]) / "captions.ass").is_file()
    assert (story.story_dir(plan["plan_id"]) / "captions.srt").is_file()


def _beat(start, text, spans=None, step=0.4, slot=None):
    words = _words(text, step=step)
    b = {"start": start, "words": words,
         "slot": slot or words[-1]["end"] + 0.3}
    if spans is not None:
        b["emphasis"] = spans
    return b


def test_plan_events_one_word_each_and_emphasis_excluded():
    beats = [_beat(0.0, "We rose above the valley at dawn.", [[4, 5]]),
             _beat(5.0, "Second beat here now.", [])]
    ev = kinetic.plan_events(beats)
    texts = [e["text"] for e in ev["body"]]
    assert "valley" not in texts            # emphasis word
    # connector words (2 before the emphasis word) are not body words either
    assert "the" not in texts and "above" not in texts
    assert ev["emphasis"][0]["text"] == "valley"
    assert ev["emphasis"][0]["connector"] == "above the"
    # exact word start -> next word start, within +0.4s of the word end
    first = ev["body"][0]
    assert first["text"] == "We" and first["start"] == pytest.approx(0.0)
    assert first["end"] == pytest.approx(0.4)
    for e in ev["body"]:
        assert e["end"] > e["start"] and len(e["text"].split()) == 1
    # last word of a beat lingers 0.4s after it ends (capped at the slot)
    last = [e for e in ev["body"] if e["text"] == "now."][0]
    assert last["end"] <= 5.0 + beats[1]["slot"] + 1e-6 and last["end"] > last["start"] + 0.3


def test_plan_events_auto_pick_and_cap():
    ev = kinetic.plan_events([_beat(0.0, "The mountains glow brightly tonight.")])
    assert [e["text"] for e in ev["emphasis"]] == ["mountains"]
    many = _beat(0.0, "alpha bravo charlie delta echo foxtrot golf", [[0, 1], [2, 3], [4, 5]])
    assert len(kinetic.plan_events([many])["emphasis"]) == kinetic.MAX_EMPH_PER_BEAT


@pytest.mark.parametrize("w,h", [(1080, 1920), (1920, 1080)])
def test_build_ass_scaling(w, h):
    ev = kinetic.plan_events([_beat(0.0, "We rose above the valley at dawn.", [[4, 5]])])
    ass = kinetic.build_ass(ev, w, h)
    assert f"PlayResX: {w}" in ass and f"PlayResY: {h}" in ass
    assert "Montserrat Medium" in ass and "\\fad(80,0)" in ass and "\\blur6" in ass
    dialogues = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    body = [l for l in dialogues if ",Body," in l]
    assert len(body) == len(ev["body"])
    assert f"\\move({w // 2},{round(h * 0.5) + 8},{w // 2},{round(h * 0.5)},0,80)" in body[0]
    size = kinetic.body_size(w, h)
    assert f"Style: Body,Montserrat Medium,{size}," in ass
    assert any(",Connector," in l for l in dialogues)
    assert kinetic.body_size(1080, 1920) == round(1920 * 0.045)


def test_ass_time_format():
    assert kinetic._ass_time(3661.256) == "1:01:01.26"


def test_emphasis_png_renderer(tmp_path):
    info = kinetic.render_emphasis_images("familiarity", 1080, 1920, tmp_path, "e0")
    from PIL import Image
    assert len(info["frames"]) == kinetic.GLITCH_FRAMES - 1
    img = Image.open(info["hold"])
    assert img.mode == "RGBA" and img.size == tuple(info["size"])
    assert img.size[0] <= 1080  # fitted into the frame width
    px = [p for p in img.getdata() if p[3] == 255]
    assert len(px) > 500
    # gradient: leftmost opaque pixels are cyan-ish, rightmost magenta-ish
    w = img.size[0]
    left = [img.getpixel((x, y)) for x in range(w // 6) for y in range(img.size[1])
            if img.getpixel((x, y))[3] == 255]
    right = [img.getpixel((x, y)) for x in range(5 * w // 6, w) for y in range(img.size[1])
             if img.getpixel((x, y))[3] == 255]
    assert left and right
    avg = lambda ps, i: sum(p[i] for p in ps) / len(ps)  # noqa: E731
    assert avg(left, 2) > avg(left, 0) and avg(left, 1) > avg(left, 0)      # cyan: low R
    assert avg(right, 0) > 120 and avg(right, 2) > 150 and avg(right, 1) < 100  # magenta
    # glitch frames differ from the hold frame
    g0 = Image.open(info["frames"][0])
    assert list(g0.getdata()) != list(img.getdata())
    lst = tmp_path / "seq.txt"
    total = kinetic.write_sequence_list(info, 1.0, lst, 30.0)
    assert total == pytest.approx(1.0, abs=0.01)
    assert lst.read_text().count("file ") == kinetic.GLITCH_FRAMES - 1 + 2


def test_render_kinetic_filtergraph_and_output(client, fake_tts, story_assets, monkeypatch):
    plan = _approved_story(client, list(story_assets.values()))
    pid = plan["plan_id"]
    seen = _capture_ffmpeg(monkeypatch)
    out = render.render_plan(pid, width=360)   # ratio None -> the story's own (16:9)
    cmd = seen[-1]
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "ass=filename=" in graph and "fontsdir=" in graph and "Montserrat" not in graph
    assert "overlay=" in graph and "eq=contrast=1.08:saturation=1.15" in graph
    assert "scale=w='trunc(iw*" in graph and "eval=frame" in graph  # push-in
    assert "rgbashift=" in graph                                        # beat glitch
    assert "subtitles=" not in graph and "drawtext" not in graph
    assert "concat=n=" in graph and "xfade" not in graph
    assert "concat" in cmd  # emphasis PNG sequences are concat-demuxed inputs
    sc = story.get_story(pid)
    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
         "-show_streams", out], capture_output=True, text=True, check=True).stdout)
    v = [s for s in probe["streams"] if s["codec_type"] == "video"][0]
    assert (v["width"], v["height"]) == (360, 202)   # 16:9 story ratio honoured
    assert float(probe["format"]["duration"]) == pytest.approx(sc["duration"], abs=0.3)
    assert shutil.os.path.isfile(out[:-4] + ".srt")
    # temp files cleaned
    assert not list(config.RENDER_TMP_DIR.glob(f"cap_{pid}_*"))
    assert not (config.RENDER_TMP_DIR / f"emph_{pid}").exists()


def test_render_kinetic_no_effects_no_captions(client, fake_tts, story_assets, monkeypatch):
    plan = _approved_story(client, list(story_assets.values()))
    seen = _capture_ffmpeg(monkeypatch)
    render.render_plan(plan["plan_id"], ratio="9:16", width=180, height=320,
                       captions=False, effects=False)
    graph = seen[-1][seen[-1].index("-filter_complex") + 1]
    assert "overlay=" not in graph and "ass=" not in graph
    assert "eq=" not in graph and "rgbashift" not in graph and "eval=frame" not in graph


def test_render_api_ratio_default_and_style(client, wait_job, fake_tts, story_assets):
    plan = _approved_story(client, list(story_assets.values()))
    r = client.post("/api/render", json={"plan_id": plan["plan_id"], "width": 320})
    assert r.status_code == 200, r.text
    job = wait_job(client, r.json()["job_id"])
    assert job["status"] == "done", job
    bad = client.post("/api/render", json={"plan_id": plan["plan_id"], "caption_style": "x"})
    assert bad.status_code == 422
