"""Caption documents keep text intact and exports remain parser-safe."""
from __future__ import annotations

import json

from app import config, models
from app.db import SessionLocal
from app.subtitles import build_document, serialize


def test_word_timing_splits_long_segment_at_words():
    text = "one two three four five six seven eight nine ten eleven twelve"
    words = []
    for i, word in enumerate(text.split()):
        words.append({"word": word, "start": i * 0.4, "end": i * 0.4 + 0.3})
    document = build_document([{"start": 0, "end": 5, "text": text, "words": words}], "social_vertical")
    assert document["qc"]["timing_source"] == "word_timing"
    assert len(document["cues"]) >= 1
    assert " ".join(cue["text"].replace("\n", " ") for cue in document["cues"]) == text
    for cue in document["cues"]:
        assert cue["end"] >= cue["start"]
        assert len(cue["text"].splitlines()) <= 2


def test_segment_fallback_reports_limitations_and_timing_qc():
    document = build_document([
        {"start": "bad", "end": 0, "text": "A very long caption with enough content to exceed nominal reading speed."},
        {"start": 4, "end": 5, "text": "Second cue."},
    ])
    assert document["qc"]["timing_source"] == "segment_timing"
    assert document["qc"]["limitations"]
    codes = {issue["code"] for issue in document["qc"]["issues"]}
    assert "invalid_start" in codes and "invalid_timing" in codes
    assert " ".join(cue["text"].replace("\n", " ") for cue in document["cues"]).startswith("A very long")


def test_serializers_handle_unicode_and_untrusted_markup():
    document = build_document([{
        "start": 0, "end": 2, "text": "Café <b>{\\b1}, hello\n\n世界",
    }])
    srt = serialize(document, "srt")
    vtt = serialize(document, "vtt")
    ass = serialize(document, "ass")
    assert "Café" in srt and "世界" in srt and "&lt;b&gt;" in srt
    assert srt.count("\n\n") == 0, "no source blank line may terminate an SRT cue"
    assert vtt.startswith("WEBVTT\n") and "&lt;b&gt;" in vtt
    assert "[Events]" in ass and "｛" in ass and "＼" in ass


def test_ass_uses_vertical_preset_and_carry_safe_timestamp():
    document = build_document([{"start": 59.999, "end": 61.0, "text": "Vertical caption"}], "social_vertical")
    ass = serialize(document, "ass")
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "Dialogue: 0,0:01:00.00" in ass


def _asset(asset_id: int, has_transcript: bool = False) -> None:
    with SessionLocal() as db:
        db.add(models.Asset(
            id=asset_id, path=f"/tmp/subtitle-{asset_id}.mp4", hash=f"subtitle-{asset_id}",
            kind="video", mime="video/mp4", has_transcript=int(has_transcript),
        ))
        db.commit()


def test_caption_routes_404_and_no_transcript(client):
    assert client.get("/api/assets/987654/captions").status_code == 404
    _asset(987655)
    assert client.get("/api/assets/987655/captions").status_code == 409


def test_caption_routes_sidecar_and_download_formats(client):
    asset_id = 987656
    _asset(asset_id, has_transcript=True)
    directory = config.DATA_DIR / "transcripts"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{asset_id}.json"
    path.write_text(json.dumps({
        "timing_source": "faster-whisper", "asset_hash": f"subtitle-{asset_id}",
        "segments": [{"start": 0, "end": 1.4, "text": "Hello <i>world</i>", "words": [
            {"start": 0, "end": .4, "word": "Hello"},
            {"start": .5, "end": 1.4, "word": "<i>world</i>"},
        ]}],
    }), encoding="utf-8")
    try:
        response = client.get(f"/api/assets/{asset_id}/captions?style=accessibility")
        assert response.status_code == 200
        assert response.json()["qc"]["timing_source"] == "faster-whisper"
        for format, marker in (("srt", "-->"), ("vtt", "WEBVTT"), ("ass", "[Events]")):
            download = client.get(f"/api/assets/{asset_id}/captions/{format}")
            assert download.status_code == 200
            assert marker in download.text
            assert "attachment;" in download.headers["content-disposition"]
        assert "&lt;i&gt;" in client.get(f"/api/assets/{asset_id}/captions/vtt").text
    finally:
        path.unlink(missing_ok=True)
