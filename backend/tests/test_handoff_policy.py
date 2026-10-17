"""Offline integration checks for consent, preserved timing and handoff integrity."""
from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest
from sqlalchemy import text

from app import config, models, policy
from app.db import SessionLocal
from app.edits import story
from app.transcripts import save_transcript


@pytest.fixture(autouse=True)
def _isolated_rows(client):
    # Existing tests share a session-wide DB and some reserve numeric IDs.
    # New feature fixtures must not change their asset population/order.
    with SessionLocal() as db:
        assets = {a.id for a in db.query(models.Asset).all()}
        plans = {p.id for p in db.query(models.EditPlan).all()}
    yield
    with SessionLocal() as db:
        for plan in db.query(models.EditPlan).all():
            if plan.id not in plans:
                db.delete(plan)
        db.flush()
        for asset in db.query(models.Asset).all():
            if asset.id not in assets:
                db.query(models.TripAsset).filter_by(asset_id=asset.id).delete()
                db.execute(text("DELETE FROM asset_fts WHERE asset_id = :id"), {"id": asset.id})
                (config.DATA_DIR / "transcripts" / f"{asset.id}.json").unlink(missing_ok=True)
                db.delete(asset)
        db.commit()


@pytest.mark.parametrize("approval,consent,cap,status", [
    (False, True, 100, 403), (True, False, 100, 403),
    (True, True, 2, 422), (True, True, 0, 422),
])
def test_denied_story_never_starts_paid_work(client, monkeypatch, approval, consent, cap, status):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "offline-test-key")
    def forbidden(*args, **kwargs):
        pytest.fail("denied request scheduled paid work")
    monkeypatch.setattr(story, "create_story_plan", forbidden)
    response = client.post("/api/edits/story", json={
        "script": "hello", "cloud_approved": approval, "voice_consent": consent,
        "max_characters": cap,
    })
    assert response.status_code == status, response.text


def test_direct_story_call_also_requires_consent(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("paid work ran without approval")
    monkeypatch.setattr(story, "_create_story_plan", forbidden)
    with pytest.raises(policy.PolicyError):
        story.create_story_plan("hello")


def test_audit_receipt_is_redacted_and_records_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "never-log-this-secret")
    def failure(*args, **kwargs):
        raise story.StoryError("provider unavailable")
    monkeypatch.setattr(story, "_create_story_plan", failure)
    with pytest.raises(story.StoryError):
        story.create_story_plan("private narration", cloud_approved=True, voice_consent=True)
    path = tmp_path / "audit" / "operations.jsonl"
    text = path.read_text()
    events = [json.loads(line) for line in text.splitlines()]
    assert [e["outcome"] for e in events] == ["approved", "failed"]
    assert events[0]["plan_id"] == events[1]["plan_id"]
    assert events[0]["approval"]["script_sha256"] == hashlib.sha256(b"private narration").hexdigest()
    assert "private narration" not in text and "never-log-this-secret" not in text
    assert path.stat().st_mode & 0o777 == 0o600


def test_audit_failure_blocks_paid_work(monkeypatch):
    monkeypatch.setattr(policy, "record_story_event", lambda *a: (_ for _ in ()).throw(OSError("full")))
    monkeypatch.setattr(story, "_create_story_plan", lambda *a, **kw: pytest.fail("paid call"))
    with pytest.raises(OSError):
        story.create_story_plan("hello", cloud_approved=True, voice_consent=True)


def test_transcript_sidecar_preserves_words_and_detects_stale_hash(client, tmp_path):
    with SessionLocal() as db:
        asset = models.Asset(path=str(tmp_path / "spoken.mp4"), hash="source-sha256",
                             kind="video", has_transcript=1)
        db.add(asset)
        db.flush()
        asset_id = asset.id
        db.add(models.TranscriptSegment(asset_id=asset_id, start=0.0, end=2.0, text="Hello world."))
        db.commit()
    result = {"segments": [{"start": 0.0, "end": 2.0, "text": "Hello world."}],
              "words": [{"start": 0.2, "end": 0.6, "word": "Hello"},
                        {"start": 0.8, "end": 1.5, "word": "world."}]}
    save_transcript(asset_id, "source-sha256", result)
    document = client.get(f"/api/assets/{asset_id}/captions").json()
    assert document["cues"][0]["start"] == 0.2
    assert document["cues"][0]["end"] == 1.5
    save_transcript(asset_id, "stale-hash", result)
    document = client.get(f"/api/assets/{asset_id}/captions").json()
    assert document["cues"][0]["start"] == 0.0
    assert any("did not match" in note for note in document["qc"]["limitations"])


def test_sidecar_failure_still_saves_primary_transcript(client, monkeypatch, tmp_path):
    from app import transcripts
    from app.ai import pipeline
    monkeypatch.setattr(config, "SKIP_WHISPER", False)
    monkeypatch.setattr(pipeline, "_unload_if_tight", lambda *a: None)
    monkeypatch.setattr(pipeline.whisper, "transcribe", lambda *a: {
        "segments": [{"start": 0.0, "end": 1.0, "text": "Still saved."}]})
    def full_disk(*args):
        raise OSError("disk unavailable")
    monkeypatch.setattr(transcripts, "save_transcript", full_disk)
    with SessionLocal() as db:
        asset = models.Asset(path=str(tmp_path / "sidecar-failure.mp4"), hash="disk-test",
                             kind="video", status="scenes_done")
        db.add(asset)
        db.commit()
        asset_id = asset.id
        pipeline.process_asset(db, asset_id)
        rows = db.query(models.TranscriptSegment).filter_by(asset_id=asset_id).all()
        assert len(rows) == 1 and rows[0].text == "Still saved."
        assert db.get(models.Asset, asset_id).has_transcript == 1


def test_package_has_verified_manifest_and_no_source_writes(client, approved_plan):
    pid = approved_plan()
    with SessionLocal() as db:
        rows = db.get(models.EditPlan, pid).clips
        paths = [db.get(models.Asset, c.asset_id).path for c in rows]
    from pathlib import Path
    before = {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}
    response = client.get(f"/api/edits/plan/{pid}/package")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert {"edit_plan.json", "provenance.json", "captions.srt", "captions.vtt",
                "captions.ass", "caption_qc.json", "transcripts.json"} <= set(archive.namelist())
        manifest = json.loads(archive.read("provenance.json"))
        assert manifest["approval_status"] == "approved"
        assert manifest["source_files_included"] is False
        assert manifest["caption_kind"] == "clip_labels"
        assert hashlib.sha256(archive.read("edit_plan.json")).hexdigest() == manifest["plan_sha256"]
        for name, detail in manifest["files"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == detail["sha256"]
            assert len(archive.read(name)) == detail["bytes"]
        assert not any(name.endswith((".mp4", ".jpg")) for name in archive.namelist())
    assert before == {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}


def test_package_draft_and_missing_plan_rejected(client, approved_plan):
    pid = approved_plan()
    with SessionLocal() as db:
        db.get(models.EditPlan, pid).status = "draft"
        db.commit()
    assert client.get(f"/api/edits/plan/{pid}/package").status_code == 409
    assert client.get("/api/edits/plan/not-found/package").status_code == 404


def test_package_story_uses_global_word_timing(client, approved_plan):
    pid = approved_plan()
    with SessionLocal() as db:
        plan = db.get(models.EditPlan, pid)
        plan.parsed_json = '{"story": true}'
        plan.total_duration = 6
        asset_id = plan.clips[0].asset_id
        for clip in list(plan.clips):
            db.delete(clip)
        db.flush()
        db.add(models.EditClip(plan_id=pid, position=0, asset_id=asset_id, start=0, end=4, transition="cut"))
        db.add(models.EditClip(plan_id=pid, position=1, asset_id=asset_id, start=0, end=2, transition="cut"))
        db.commit()
    directory = story.story_dir(pid)
    directory.mkdir(parents=True)
    (directory / "story.json").write_text(json.dumps({
        "duration": 6,
        "beats": [{"start": 0, "slot": 4, "audio_duration": 4, "text": "Opening", "words": [],
                   "clips": [{"asset_id": asset_id, "start": 0, "end": 4}]},
                  {"start": 4, "slot": 2, "audio_duration": 2, "text": "Hello world.",
                   "clips": [{"asset_id": asset_id, "start": 0, "end": 2}], "words": [
            {"start": 0.2, "end": 0.6, "word": "Hello"},
            {"start": 0.8, "end": 1.5, "word": "world."},
        ]}],
    }))
    response = client.get(f"/api/edits/plan/{pid}/package")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert b"00:00:04,200 --> 00:00:05,500" in archive.read("captions.srt")
        manifest = json.loads(archive.read("provenance.json"))
        assert manifest["caption_kind"] == "narration"
        assert manifest["legacy_voice_note"]


def test_story_package_rejects_missing_or_corrupt_sidecar(client, approved_plan):
    pid = approved_plan()
    with SessionLocal() as db:
        db.get(models.EditPlan, pid).parsed_json = '{"story": true}'
        db.commit()
    assert client.get(f"/api/edits/plan/{pid}/package").status_code == 409
    directory = story.story_dir(pid)
    directory.mkdir(parents=True)
    for content in ('broken json', '[]', '{"beats": [{"text": "Hi"}]}'):
        (directory / "story.json").write_text(content)
        assert client.get(f"/api/edits/plan/{pid}/package").status_code == 409
