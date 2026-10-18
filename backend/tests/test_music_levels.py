"""Voice/music gain staging for narrated renders (voice must sit well above the bed)."""
import base64
import re
import subprocess

import pytest

from app import config
from app.edits import audio_levels, render, story
from .test_beat_sync import long_assets  # noqa: F401
from .test_story import _capture_ffmpeg

pytestmark = pytest.mark.usefixtures("cleanup_assets")

VOICE_HZ, MUSIC_HZ = 440, 1760


def _ff(*args):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


def _speechlike(path, seconds, amp=0.03):
    """440 Hz tone bursts (2.5 Hz on/off) standing in for quiet TTS speech."""
    _ff("-f", "lavfi", "-i",
        f"aevalsrc='{amp}*sin(2*PI*{VOICE_HZ}*t)*gt(sin(2*PI*2.5*t),0)':s=44100:d={seconds}",
        "-c:a", "libmp3lame", "-b:a", "192k", str(path))


def _loud_music(path, seconds=40):
    _ff("-f", "lavfi", "-i", f"aevalsrc='0.6*sin(2*PI*{MUSIC_HZ}*t)':s=44100:d={seconds}",
        "-c:a", "libmp3lame", "-b:a", "192k", str(path))


def _band_peak_db(path, hz, start, end):
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path), "-vn", "-af",
         f"atrim=start={start}:end={end},bandpass=f={hz}:width_type=h:w=120,volumedetect",
         "-f", "null", "-"], capture_output=True, text=True).stderr
    return float(re.search(r"max_volume: (-?[0-9.]+) dB", out).group(1))


# ---------------------------------------------------------------------------
# gain maths
# ---------------------------------------------------------------------------
def test_gain_computation():
    assert audio_levels.voice_gain_db(-29.5) == pytest.approx(13.5)
    assert audio_levels.voice_gain_db(-16) == 0
    assert audio_levels.voice_gain_db(-60) == audio_levels.MAX_BOOST_DB          # clamped
    assert audio_levels.music_gain_db(-12.0, 18) == pytest.approx(-22.0)         # -16 - 18 - (-12)
    assert audio_levels.music_gain_db(-35.0, 18) == pytest.approx(1.0)
    assert audio_levels.music_gain_db(-12.0, 12) == pytest.approx(-16.0)


def test_plan_levels_uses_measurements(monkeypatch):
    seen = []

    def fake(path, start=0.0, duration=None):
        seen.append((path, start, duration))
        return -29.5 if path == "v.mp3" else -11.0

    monkeypatch.setattr(audio_levels, "measure_lufs", fake)
    lv = audio_levels.plan_levels("v.mp3", "m.mp3", 12.5, 30.0)
    assert lv["voice_gain_db"] == 13.5 and lv["music_gain_db"] == pytest.approx(-23.0)
    assert ("m.mp3", 12.5, 30.0) in seen                      # the exact music segment is measured
    monkeypatch.setattr(config, "MUSIC_BED_DB", 12.0)
    assert audio_levels.plan_levels("v.mp3", "m.mp3", 0, 30)["music_gain_db"] == pytest.approx(-17.0)
    monkeypatch.setattr(audio_levels, "measure_lufs", lambda *a, **k: None)
    assert audio_levels.plan_levels("v.mp3", "m.mp3", 0, 30) is None  # unmeasurable -> legacy mix


def test_filters():
    assert "alimiter=limit=0.8414" in audio_levels.voice_filter(13.5) and "volume=13.50dB" in audio_levels.voice_filter(13.5)
    assert audio_levels.music_filter(-22, None) == "volume=-22.00dB"
    rise = audio_levels.music_filter(-22, 31.2)
    assert "eval=frame" in rise and "t-31.200" in rise and "0.9953" in rise     # +6 dB = x1.9953


def test_measure_lufs_cached_and_segmented(tmp_path):
    f = tmp_path / "tone.mp3"
    _ff("-f", "lavfi", "-i", "aevalsrc='0.1*sin(2*PI*1000*t)':s=44100:d=6", "-c:a", "libmp3lame", str(f))
    full = audio_levels.measure_lufs(str(f))
    assert -24 < full < -19                                   # 0.1 amplitude sine = about -23 dBFS rms -> ~-23 LUFS
    assert audio_levels.measure_lufs(str(f), 1.0, 3.0) == pytest.approx(full, abs=0.6)
    assert audio_levels.measure_lufs(str(tmp_path / "missing.mp3")) is None
    silent = tmp_path / "silent.mp3"
    _ff("-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=3", "-c:a", "libmp3lame", str(silent))
    assert audio_levels.measure_lufs(str(silent)) is None


# ---------------------------------------------------------------------------
# real render: the voice must dominate the bed
# ---------------------------------------------------------------------------
def _story_with_quiet_voice(client, long_assets, tmp_path, monkeypatch):  # noqa: F811
    voice = tmp_path / "voice_src.mp3"
    _speechlike(voice, 4.0)
    audio = voice.read_bytes()

    def http(url, headers, payload):
        text = payload["text"]
        return 200, {"audio_base64": base64.b64encode(audio).decode(), "alignment": {
            "characters": list(text),
            "character_start_times_seconds": [i * 0.04 for i in range(len(text))],
            "character_end_times_seconds": [i * 0.04 + 0.04 for i in range(len(text))]}}

    monkeypatch.setattr(story, "_tts_http", http)
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "test-key-not-real")
    plan = story.create_story_plan(
        "A quiet narrator speaks over a loud song here.\n\nThe second beat keeps the same voice going.",
        search_fn=lambda db, q, limit: long_assets, cloud_approved=True, voice_consent=True)
    client.post(f"/api/edits/plan/{plan['plan_id']}/approve")
    return plan, story.get_story(plan["plan_id"])


def test_music_bed_sits_well_below_the_voice(client, long_assets, tmp_path, monkeypatch):  # noqa: F811
    plan, sc = _story_with_quiet_voice(client, long_assets, tmp_path, monkeypatch)
    music = tmp_path / "loud_song.mp3"
    _loud_music(music)
    voice_lufs = audio_levels.measure_lufs(sc["voiceover"])
    music_lufs = audio_levels.measure_lufs(str(music), 0, sc["duration"])
    assert voice_lufs < -25 and music_lufs > -16        # the real-world mismatch being fixed
    start, end = 0.6, sc["duration"] - 0.6

    # legacy mix (the bug): the music drowns the narration
    monkeypatch.setattr(config, "MUSIC_LEVELING", False)
    old = render.render_plan(plan["plan_id"], ratio="9:16", width=180, height=320,
                             music_path=str(music), music_start=0, captions=False, effects=False)
    old_gap = _band_peak_db(old, VOICE_HZ, start, end) - _band_peak_db(old, MUSIC_HZ, start, end)
    assert old_gap < 0, f"expected the legacy mix to bury the voice, voice-music gap {old_gap:.1f} dB"

    # levelled mix: music at least 15 dB below the voice while it speaks
    monkeypatch.setattr(config, "MUSIC_LEVELING", True)
    seen = _capture_ffmpeg(monkeypatch)
    new = render.render_plan(plan["plan_id"], ratio="9:16", width=180, height=320,
                             music_path=str(music), music_start=0, captions=False, effects=False)
    graph = seen[-1][seen[-1].index("-filter_complex") + 1]
    assert "alimiter=" in graph and "threshold=0.05:ratio=4:attack=15:release=300" in graph
    assert "volume=" in graph and "loudnorm=I=-14" in graph
    voice_db = _band_peak_db(new, VOICE_HZ, start, end)
    music_db = _band_peak_db(new, MUSIC_HZ, start, end)
    new_gap = voice_db - music_db
    print(f"voice-music gap: legacy {old_gap:+.1f} dB, levelled {new_gap:+.1f} dB "
          f"(voice {voice_lufs:.1f} LUFS, music {music_lufs:.1f} LUFS)")
    assert new_gap >= 15, f"music only {new_gap:.1f} dB below the voice"
    assert voice_db > -12                                          # voice is also loud enough overall


def test_end_card_lets_the_music_rise(client, long_assets, tmp_path, monkeypatch):  # noqa: F811
    plan, sc = _story_with_quiet_voice(client, long_assets, tmp_path, monkeypatch)
    # same story, now with a brand end card
    plan = story.create_story_plan("One quiet beat of narration for the card test.",
                                   search_fn=lambda db, q, limit: long_assets,
                                   cloud_approved=True, voice_consent=True, brand="albertaweb")
    client.post(f"/api/edits/plan/{plan['plan_id']}/approve")
    sc = story.get_story(plan["plan_id"])
    music = tmp_path / "loud_song2.mp3"
    _loud_music(music)
    seen = _capture_ffmpeg(monkeypatch)
    out = render.render_plan(plan["plan_id"], ratio="9:16", width=180, height=320,
                             music_path=str(music), music_start=0, captions=False, effects=False)
    graph = seen[-1][seen[-1].index("-filter_complex") + 1]
    assert "eval=frame" in graph and f"t-{sc['duration']:.3f}" in graph
    during = _band_peak_db(out, MUSIC_HZ, 0.6, sc["duration"] - 0.6)
    card = _band_peak_db(out, MUSIC_HZ, sc["duration"] + 0.9, sc["duration"] + 1.6)
    assert card - during >= 3                                      # bed rises under the card


def test_broll_and_voice_only_paths_are_unchanged(client, long_assets, tmp_path, monkeypatch):  # noqa: F811
    plan, sc = _story_with_quiet_voice(client, long_assets, tmp_path, monkeypatch)
    seen = _capture_ffmpeg(monkeypatch)
    render.render_plan(plan["plan_id"], ratio="9:16", width=180, height=320, captions=False, effects=False)
    graph = seen[-1][seen[-1].index("-filter_complex") + 1]
    assert "alimiter" not in graph and "sidechaincompress" not in graph      # voice-only: no music, no levelling
