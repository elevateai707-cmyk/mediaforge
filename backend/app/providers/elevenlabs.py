"""Explicit, cached timestamped narration; no voice cloning and no paid retries."""

import base64
import math
from pathlib import Path

import httpx

from .settings import key

BASE = "https://api.elevenlabs.io"


def request(method, path, **kwargs):
    token = key("elevenlabs")
    if not token:
        raise ValueError("Configure an ElevenLabs key in Settings")
    with httpx.Client(timeout=120) as c:
        r = c.request(method, BASE + path, headers={"xi-api-key": token}, **kwargs)
        r.raise_for_status()
        return r.json()


def discovery():
    voices = []
    cursor = None
    for _ in range(20):
        page = request(
            "GET",
            "/v2/voices",
            params={
                "page_size": 100,
                **({"next_page_token": cursor} if cursor else {}),
            },
        )
        voices += page.get("voices", [])
        cursor = page.get("next_page_token")
        if not page.get("has_more") or not cursor:
            break
    models = request("GET", "/v1/models")
    try:
        usage = request("GET", "/v1/user/subscription")
    except httpx.HTTPStatusError:
        usage = None
    return {
        "voices": [{"id": v["voice_id"], "name": v["name"]} for v in voices],
        "models": [
            {"id": m["model_id"], "name": m["name"]}
            for m in models
            if m.get("can_do_text_to_speech")
        ],
        "usage": {
            k: usage[k] for k in ("character_count", "character_limit") if k in usage
        }
        if usage
        else None,
    }


def alignment_cues(data):
    from ..edits.project import Word
    from ..edits.subtitles import phrase_cues

    chars = data.get("characters", [])
    starts = data.get("character_start_times_seconds", [])
    ends = data.get("character_end_times_seconds", [])
    if not len(chars) == len(starts) == len(ends):
        raise ValueError("Provider returned invalid alignment lengths")
    words = []
    text = ""
    start = 0.0
    end = 0.0
    last = 0.0
    for ch, a, b in zip(chars, starts, ends):
        if (
            not isinstance(ch, str)
            or not math.isfinite(a)
            or not math.isfinite(b)
            or a < last - 0.05
            or b < a
            or a < 0
        ):
            raise ValueError("Provider returned invalid alignment timing")
        last = a
        if ch.isspace():
            if text and end > start:
                words.append(Word(start=start, end=end, text=text))
                text = ""
        else:
            if not text:
                start = a
            text += ch
            end = b
    if text and end > start:
        words.append(Word(start=start, end=end, text=text))
    return [c.model_dump() for c in phrase_cues(words)]


def generate(body, directory):
    from urllib.parse import quote

    payload = {
        "text": body["script"],
        "model_id": body["model"],
        "voice_settings": body.get("settings", {}),
    }
    data = request(
        "POST",
        "/v1/text-to-speech/" + quote(body["voice"], safe="") + "/with-timestamps",
        json=payload,
        params={"output_format": "mp3_44100_128"},
    )
    audio = base64.b64decode(data["audio_base64"], validate=True)
    if len(audio) > 50_000_000:
        raise ValueError("Audio exceeds local download limit")
    path = directory / "narration.mp3"
    partial = directory / "audio.part"
    partial.write_bytes(audio)
    partial.replace(path)
    notice = None
    try:
        cues = alignment_cues(
            data.get("normalized_alignment") or data.get("alignment") or {}
        )
    except (ValueError, TypeError):
        cues = []
    if not cues:
        # Keep paid audio even if local alignment is unavailable; never generate twice.
        from ..ai.whisper import transcribe_profile

        try:
            result = transcribe_profile(str(path), "balanced")
            cues = result.get("segments", [])
            notice = "Provider timing absent or invalid; used local transcription. Review synchronization."
        except Exception:
            notice = "Audio is ready, but caption alignment failed. Transcribe locally or enter captions manually."
    from ..edits.render import probe_json

    duration = float(probe_json(str(path)).get("format", {}).get("duration", 0))
    if cues and duration and cues[-1]["end"] > duration + 0.25:
        cues = []
        notice = "Alignment exceeded narration duration and was discarded. Audio is retained; correct captions manually."
    return {
        "status": "succeeded",
        "assets": [str(path)],
        "cues": cues,
        "duration": duration,
        "notice": notice,
    }


def transcribe(body, directory):
    from ..edits.project import Word
    from ..edits.subtitles import phrase_cues

    path = body["paths"][str(body["asset_id"])]
    with open(path, "rb") as source:
        data = request(
            "POST",
            "/v1/speech-to-text",
            files={"file": (Path(path).name, source)},
            data={
                "model_id": body["model"],
                "timestamps_granularity": "word",
                "tag_audio_events": "false",
                "diarize": "false",
                **({"language_code": body["language"]} if body.get("language") else {}),
            },
        )
    words = [
        Word(start=w["start"], end=w["end"], text=w["text"])
        for w in data.get("words", [])
        if w.get("type") == "word" and w.get("end", 0) > w.get("start", 0)
    ]
    return {
        "status": "succeeded",
        "assets": [],
        "cues": [c.model_dump() for c in phrase_cues(words)],
        "asset_id": body["asset_id"],
        "language": data.get("language_code"),
        "notice": None
        if words
        else "No reliable speech words returned; no captions added.",
    }
