"""Caption word edits keep durable sources and generated captions aligned."""
from __future__ import annotations

import json
import shutil

import pytest
from sqlalchemy import text

from app import config, models
from app.db import SessionLocal
from app import caption_editor


@pytest.fixture(autouse=True)
def cleanup_caption_rows():
    yield
    ids = (981001, 981002)
    with SessionLocal() as db:
        db.query(models.TranscriptSegment).filter(models.TranscriptSegment.asset_id.in_(ids)).delete(synchronize_session=False)
        db.query(models.Asset).filter(models.Asset.id.in_(ids)).delete(synchronize_session=False)
        db.query(models.EditPlan).filter(models.EditPlan.id == "captionstory981").delete(synchronize_session=False)
        db.execute(text("DELETE FROM asset_fts WHERE asset_id IN (981001, 981002)"))
        db.commit()
    for asset_id in ids:
        for suffix in (".json", ".srt", ".vtt", ".ass"):
            (config.DATA_DIR / "transcripts" / f"{asset_id}{suffix}").unlink(missing_ok=True)
    shutil.rmtree(config.EXPORTS_DIR / "story" / "captionstory981", ignore_errors=True)
    shutil.rmtree(config.DATA_DIR / "plan_versions" / "captionstory981", ignore_errors=True)


def test_asset_caption_word_edit_updates_sidecar_db_and_exports(client):
    asset_id = 981001
    with SessionLocal() as db:
        db.add(models.Asset(id=asset_id, path=f"/tmp/{asset_id}.mp4", hash="caption-edit",
                            kind="video", duration=3.0, has_transcript=1))
        db.add(models.TranscriptSegment(asset_id=asset_id, start=0, end=2, text="old text"))
        db.commit()
    sidecar = config.DATA_DIR / "transcripts" / f"{asset_id}.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps({"asset_hash": "caption-edit", "segments": [{
        "start": 0, "end": 2, "text": "old text", "words": [
            {"word": "old", "start": 0, "end": .5}, {"word": "text", "start": .6, "end": 1.2}]}]}))
    response = client.put(f"/api/assets/{asset_id}/caption-words", json={"words": [
        {"text": "new", "start": 0, "end": .5}, {"text": "words", "start": .6, "end": 1.4}]})
    assert response.status_code == 200, response.text
    assert response.json()["words"][1]["text"] == "words"
    saved = json.loads(sidecar.read_text())
    assert saved["timing_source"] == "edited"
    assert saved["segments"][0]["text"] == "new words"
    for ext in ("srt", "vtt", "ass"):
        assert (sidecar.parent / f"{asset_id}.{ext}").is_file()
    with SessionLocal() as db:
        assert db.query(models.TranscriptSegment).filter_by(asset_id=asset_id).one().text == "new words"


def test_caption_word_edit_rejects_overlap_and_out_of_bounds(client):
    asset_id = 981002
    with SessionLocal() as db:
        db.add(models.Asset(id=asset_id, path=f"/tmp/{asset_id}.mp4", hash="caption-bounds",
                            kind="video", duration=1.0, has_transcript=1))
        db.add(models.TranscriptSegment(asset_id=asset_id, start=0, end=1, text="one"))
        db.commit()
    bad = client.put(f"/api/assets/{asset_id}/caption-words", json={"words": [
        {"text": "one", "start": 0, "end": .8}, {"text": "two", "start": .7, "end": 1.2}]})
    assert bad.status_code == 422


def test_story_caption_edit_snapshots_and_returns_plan_to_draft():
    plan_id = "captionstory981"
    with SessionLocal() as db:
        db.add(models.EditPlan(id=plan_id, status="approved", summary="story", target_ratio="9:16"))
        db.commit()
    story_dir = config.EXPORTS_DIR / "story" / plan_id
    story_dir.mkdir(parents=True, exist_ok=True)
    (story_dir / "story.json").write_text(json.dumps({
        "plan_id": plan_id, "ratio": "9:16", "duration": 2.3, "beats": [{
            "index": 1, "start": 0, "slot": 2.3, "audio_duration": 2.0,
            "text": "Hello world", "emphasis": [[1, 2]], "words": [
                {"word": "Hello", "start": 0, "end": .5},
                {"word": "world", "start": .6, "end": 1.1},
            ]}],
    }), encoding="utf-8")
    result = caption_editor.save_story_words(plan_id, [
        {"text": "Hi", "start": 0, "end": .5}, {"text": "there", "start": .6, "end": 1.1},
    ], __import__("app.edits.planner", fromlist=["record_revision"]).record_revision)
    assert result["words"][0]["text"] == "Hi"
    saved = json.loads((story_dir / "story.json").read_text())
    assert saved["beats"][0]["emphasis"] == [[1, 2]]
    assert (story_dir / "captions.vtt").is_file()
    with SessionLocal() as db:
        assert db.get(models.EditPlan, plan_id).status == "draft"


def test_story_caption_failure_restores_artifacts_but_keeps_plan_draft(monkeypatch):
    plan_id = "captionstoryfail981"
    with SessionLocal() as db:
        db.add(models.EditPlan(id=plan_id, status="approved", summary="story", target_ratio="9:16")); db.commit()
    story_dir = config.EXPORTS_DIR / "story" / plan_id; story_dir.mkdir(parents=True, exist_ok=True)
    story_json = {"plan_id": plan_id, "ratio": "9:16", "duration": 2, "beats": [{"index": 1, "start": 0, "slot": 2, "audio_duration": 1.5, "text": "one two", "words": [{"word": "one", "start": 0, "end": .4}, {"word": "two", "start": .5, "end": .9}]}]}
    (story_dir / "story.json").write_text(json.dumps(story_json)); (story_dir / "captions.srt").write_text("old srt")
    original = caption_editor._atomic_text
    failed = False
    def fail_vtt(path, value):
        nonlocal failed
        if path.name == "captions.vtt" and not failed:
            failed = True; raise OSError("disk failure")
        original(path, value)
    monkeypatch.setattr(caption_editor, "_atomic_text", fail_vtt)
    with pytest.raises(OSError):
        caption_editor.save_story_words(plan_id, [{"text": "new", "start": 0, "end": .4}, {"text": "words", "start": .5, "end": .9}])
    assert (story_dir / "captions.srt").read_text() == "old srt"
    assert json.loads((story_dir / "story.json").read_text()) == story_json
    with SessionLocal() as db:
        assert db.get(models.EditPlan, plan_id).status == "draft"
    shutil.rmtree(story_dir, ignore_errors=True); shutil.rmtree(config.DATA_DIR / "plan_versions" / plan_id, ignore_errors=True)
    with SessionLocal() as db:
        db.query(models.EditPlan).filter_by(id=plan_id).delete(); db.commit()
