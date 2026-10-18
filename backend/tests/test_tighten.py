from __future__ import annotations

import json
import subprocess
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config, models
from app.db import SessionLocal, init_db
from app.edits.tighten import TightenError, create_tighten_plan


def _tone_silence_video(path):
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "color=c=black:s=160x90:d=2.8:r=24",
        "-f", "lavfi", "-i", "sine=frequency=660:duration=1",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=0.8",
        "-f", "lavfi", "-i", "sine=frequency=660:duration=1",
        "-filter_complex", "[1:a][2:a][3:a]concat=n=3:v=0:a=1[a]",
        "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac", "-shortest", str(path),
    ], check=True, capture_output=True)


def _add_asset(path, duration=2.8):
    init_db()
    with SessionLocal() as db:
        asset = models.Asset(path=str(path), hash=f"tighten-{path.name}", kind="video",
                             duration=duration)
        db.add(asset)
        db.commit()
        return asset.id


def _cleanup_rows(plan_id, asset_id):
    with SessionLocal() as db:
        plan = db.get(models.EditPlan, plan_id) if plan_id else None
        if plan is not None:
            db.delete(plan)
        asset = db.get(models.Asset, asset_id)
        if asset is not None:
            db.delete(asset)
        db.commit()


def test_tighten_creates_draft_and_preserves_audible_intervals(media_dir):
    source = media_dir / "tone_silence.mp4"
    _tone_silence_video(source)
    asset_id = _add_asset(source)

    result = create_tighten_plan(asset_id=asset_id, remove_fillers=False,
                                 min_silence_s=.35, padding_s=.05)

    assert result["status"] == "draft"
    assert source.exists()  # Planning never modifies the original media.
    assert len(result["clips"]) == 2
    assert result["clips"][0]["start"] == pytest.approx(0, abs=.05)
    assert result["clips"][0]["end"] == pytest.approx(1.05, abs=.16)
    assert result["clips"][1]["start"] == pytest.approx(1.75, abs=.16)
    assert result["clips"][1]["end"] == pytest.approx(2.8, abs=.12)
    with SessionLocal() as db:
        assert db.get(models.EditPlan, result["plan_id"]).status == "draft"


def test_tighten_removes_only_verified_whisper_fillers(monkeypatch, media_dir):
    source = media_dir / "filler.mp4"
    source.touch()
    asset_id = _add_asset(source, duration=4)
    transcript_dir = config.DATA_DIR / "transcripts"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    (transcript_dir / f"{asset_id}.json").write_text(json.dumps({
        "asset_id": asset_id, "asset_hash": f"tighten-{source.name}",
        "timing_source": "faster-whisper", "segments": [{"words": [
            {"word": "um", "start": 1.0, "end": 1.2},
            {"word": "like", "start": 1.3, "end": 1.5},
        ]}],
    }))
    monkeypatch.setattr("app.edits.tighten.detect_silences", lambda *_args: [])
    result = create_tighten_plan(asset_id=asset_id, padding_s=.05)
    # "um" is cut. "like" is retained because it is not isolated by pauses.
    assert [(c["start"], c["end"]) for c in result["clips"]] == [(0.0, .95), (1.25, 4.0)]


def test_story_plan_rejects_tightening_that_changes_voice_sync(monkeypatch, media_dir):
    source = media_dir / "story.mp4"
    source.touch()
    asset_id = _add_asset(source, duration=4)
    with SessionLocal() as db:
        plan = models.EditPlan(id="storytight", status="draft", parsed_json=json.dumps({"story": True}),
                               total_duration=4)
        db.add(plan)
        db.add(models.EditClip(plan_id=plan.id, asset_id=asset_id, start=0, end=4, transition="cut"))
        db.commit()
    monkeypatch.setattr("app.edits.tighten.detect_silences", lambda *_args: [(1.0, 2.0)])
    with pytest.raises(TightenError, match="voiceover synchronization"):
        create_tighten_plan(plan_id="storytight", remove_fillers=False)


def test_story_plan_is_rejected_before_audio_analysis(monkeypatch, media_dir):
    source = media_dir / "story-before-analysis.mp4"
    source.touch()
    asset_id = _add_asset(source, duration=4)
    plan_id = f"story_{uuid.uuid4().hex}"
    with SessionLocal() as db:
        db.add(models.EditPlan(id=plan_id, status="draft", parsed_json=json.dumps({"story": True}), total_duration=4))
        db.add(models.EditClip(plan_id=plan_id, asset_id=asset_id, start=0, end=4, transition="cut"))
        db.commit()
    monkeypatch.setattr("app.edits.tighten.detect_silences", lambda *_args: pytest.fail("audio must not be analyzed"))
    try:
        with pytest.raises(TightenError, match="voiceover synchronization"):
            create_tighten_plan(plan_id=plan_id)
    finally:
        _cleanup_rows(plan_id, asset_id)


def test_tighten_caches_asset_analysis_and_preserves_boundary_transitions(monkeypatch, media_dir):
    source = media_dir / "transitions.mp4"
    source.touch()
    asset_id = _add_asset(source, duration=3)
    plan_id = f"tight_{uuid.uuid4().hex}"
    with SessionLocal() as db:
        db.add(models.EditPlan(id=plan_id, status="draft", total_duration=2))
        db.add_all([
            models.EditClip(plan_id=plan_id, position=0, asset_id=asset_id, start=0, end=1, transition="fade"),
            models.EditClip(plan_id=plan_id, position=1, asset_id=asset_id, start=2, end=3, transition="crossfade"),
        ])
        db.commit()
    calls = []
    monkeypatch.setattr("app.edits.tighten.detect_silences", lambda *_args: calls.append(1) or [(.2, .8)])
    try:
        result = create_tighten_plan(plan_id=plan_id, remove_fillers=False, padding_s=0)
        assert len(calls) == 1
        assert [(clip["start"], clip["end"], clip["transition"]) for clip in result["clips"]] == [
            (0.0, .2, "fade"), (.8, 1.0, "cut"), (2.0, 3.0, "crossfade"),
        ]
    finally:
        _cleanup_rows(plan_id, asset_id)


def test_tighten_keeps_tiny_audible_shards_and_reports_ffmpeg_audio_errors(monkeypatch):
    from app.edits import tighten

    assert tighten._kept_intervals(0, 1, [(.01, .9)]) == [(0, .01), (.9, 1)]
    monkeypatch.setattr(tighten.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(
        returncode=1, stderr="Output file #0 does not contain any stream\n"))
    with pytest.raises(TightenError, match="no audio stream"):
        tighten.detect_silences("not-audio.mp4", -35, .4)


def test_tighten_route_requires_one_source_and_returns_draft(monkeypatch):
    from app import tighten_routes

    app = FastAPI()
    app.include_router(tighten_routes.router)
    monkeypatch.setattr(tighten_routes, "create_tighten_plan", lambda **kwargs: {
        "plan_id": "draft123", "status": "draft", "received": kwargs,
    })
    with TestClient(app) as client:
        assert client.post("/api/edits/tighten", json={}).status_code == 422
        response = client.post("/api/edits/tighten", json={"asset_id": 9})
    assert response.status_code == 200
    assert response.json()["received"]["asset_id"] == 9
