"""Isolated story-plan history and B-roll regression tests."""
from __future__ import annotations

import json
import shutil
import uuid

import pytest

from app import config, models
from app.caption_editor import save_story_words
from app.db import SessionLocal, init_db
from app.edits import plan_history, planner, story


@pytest.fixture()
def story_plan(tmp_path):
    """A small, self-cleaning story plan with two narration beats."""
    init_db()
    plan_id = f"hist_{uuid.uuid4().hex}"
    source_paths = [tmp_path / f"source-{index}.mp4" for index in range(4)]
    for path in source_paths:
        path.touch()
    with SessionLocal() as db:
        assets = [models.Asset(path=str(path), hash=f"{plan_id}-{index}", kind="video", duration=10)
                  for index, path in enumerate(source_paths)]
        db.add_all(assets)
        db.flush()
        plan = models.EditPlan(id=plan_id, status="approved", intent="story test",
                               parsed_json=json.dumps({"story": True}), target_ratio="9:16",
                               total_duration=5)
        db.add(plan)
        # Beat one has two clips; beat two has one. This exercises grouped fitting.
        initial = [(assets[0].id, 0, 1.5, "first beat"), (assets[1].id, 0, 1.5, "first beat"),
                   (assets[2].id, 0, 2.0, "second beat")]
        for position, (asset_id, start, end, caption) in enumerate(initial):
            db.add(models.EditClip(plan_id=plan_id, position=position, asset_id=asset_id,
                                   start=start, end=end, caption=caption, transition="cut"))
        db.commit()
        asset_ids = [asset.id for asset in assets]

    out = story.story_dir(plan_id)
    out.mkdir(parents=True)
    sidecar = {
        "plan_id": plan_id, "duration": 5.0, "ratio": "9:16", "beats": [
            {"index": 1, "start": 0.0, "slot": 3.0, "audio_duration": 3.0,
             "text": "first beat", "words": [{"word": "first", "start": 0.0, "end": 0.5}, {"word": "beat", "start": 0.6, "end": 1.0}],
             "clips": [{"asset_id": asset_ids[0], "scene_id": None, "start": 0.0, "end": 1.5},
                       {"asset_id": asset_ids[1], "scene_id": None, "start": 0.0, "end": 1.5}]},
            {"index": 2, "start": 3.0, "slot": 2.0, "audio_duration": 2.0,
             "text": "second beat", "words": [{"word": "second", "start": 0.0, "end": 0.5}, {"word": "beat", "start": 0.6, "end": 1.0}],
             "clips": [{"asset_id": asset_ids[2], "scene_id": None, "start": 0.0, "end": 2.0}]},
        ],
    }
    (out / "story.json").write_text(json.dumps(sidecar), encoding="utf-8")
    (out / "captions.srt").write_text("original captions", encoding="utf-8")
    try:
        yield {"id": plan_id, "asset_ids": asset_ids, "sidecar": sidecar}
    finally:
        with SessionLocal() as db:
            plan = db.get(models.EditPlan, plan_id)
            if plan is not None:
                db.delete(plan)
            for asset_id in asset_ids:
                asset = db.get(models.Asset, asset_id)
                if asset is not None:
                    db.delete(asset)
            db.commit()
        shutil.rmtree(out, ignore_errors=True)
        shutil.rmtree(config.DATA_DIR / "plan_versions" / plan_id, ignore_errors=True)


def test_story_update_auto_fits_each_beat_to_its_audio_slot(story_plan):
    pid = story_plan["id"]
    ids = story_plan["asset_ids"]
    result = planner.update_plan(pid, [
        {"asset_id": ids[0], "start": 0, "end": 1},
        {"asset_id": ids[1], "start": 0, "end": 1},
        {"asset_id": ids[2], "start": 0, "end": 1},
    ])

    assert result["status"] == "draft"
    assert result["total_duration"] == pytest.approx(5.0)
    assert sum(c["end"] - c["start"] for c in result["clips"][:2]) == pytest.approx(3.0)
    assert result["clips"][2]["end"] - result["clips"][2]["start"] == pytest.approx(2.0)
    assert {clip["transition"] for clip in result["clips"]} == {"cut"}


def test_story_update_rejects_short_replacement_without_mutating(story_plan):
    pid = story_plan["id"]
    ids = story_plan["asset_ids"]
    with SessionLocal() as db:
        short = models.Asset(path=f"/tmp/{pid}-short.mp4", hash=f"{pid}-short", kind="video", duration=.25)
        db.add(short)
        db.commit()
        short_id = short.id
    before = planner.get_plan(pid)
    sidecar_before = (story.story_dir(pid) / "story.json").read_text(encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="too short"):
            planner.update_plan(pid, [
                {"asset_id": short_id, "start": 0, "end": .2},
                {"asset_id": short_id, "start": 0, "end": .2},
                {"asset_id": ids[2], "start": 0, "end": 1},
            ])
        assert planner.get_plan(pid)["clips"] == before["clips"]
        assert (story.story_dir(pid) / "story.json").read_text(encoding="utf-8") == sidecar_before
    finally:
        with SessionLocal() as db:
            short = db.get(models.Asset, short_id)
            if short is not None:
                db.delete(short)
                db.commit()


def test_swap_broll_uses_one_based_beat_and_preserves_story_duration(story_plan):
    pid = story_plan["id"]
    replacement = story_plan["asset_ids"][3]
    result = planner.swap_broll(pid, 1, replacement)

    assert result["status"] == "draft"
    assert result["total_duration"] == pytest.approx(5.0)
    assert result["clips"][0]["asset_id"] == replacement
    assert result["clips"][0]["end"] - result["clips"][0]["start"] == pytest.approx(3.0)
    assert story.get_story(pid)["beats"][0]["clips"][0]["asset_id"] == replacement


def test_history_restores_story_caption_snapshot_and_drafts_plan(story_plan):
    pid = story_plan["id"]
    initial_version = plan_history.record_revision(pid, "initial")
    edited_words = [
        {"text": "changed", "start": 0.0, "end": 0.5}, {"text": "beat", "start": 0.6, "end": 1.0},
        {"text": "second", "start": 3.0, "end": 3.5}, {"text": "beat", "start": 3.6, "end": 4.0},
    ]
    save_story_words(pid, edited_words, planner.record_revision)
    assert story.get_story(pid)["beats"][0]["text"] == "changed beat"
    assert planner.get_plan(pid)["status"] == "draft"  # caption edits re-arm approval.

    restored = plan_history.restore(pid, initial_version)
    assert restored["status"] == "draft"
    assert story.get_story(pid)["beats"][0]["text"] == "first beat"
    assert (story.story_dir(pid) / "captions.srt").read_text(encoding="utf-8") == "original captions"


def test_history_and_broll_routes_use_one_based_contract(client, story_plan):
    pid = story_plan["id"]
    versions = client.get(f"/api/edits/plan/{pid}/versions")
    assert versions.status_code == 200
    version = versions.json()["versions"][0]["version"]

    swapped = client.post(f"/api/edits/plan/{pid}/swap-broll", json={
        "beat_index": 1, "asset_id": story_plan["asset_ids"][3],
    })
    assert swapped.status_code == 200, swapped.text
    assert swapped.json()["clips"][0]["asset_id"] == story_plan["asset_ids"][3]

    restored = client.post(f"/api/edits/plan/{pid}/versions/{version}/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["status"] == "draft"
    assert restored.json()["clips"][0]["asset_id"] == story_plan["asset_ids"][0]


@pytest.mark.parametrize("operation", ["edit", "swap", "restore"])
def test_approval_revoked_before_any_story_write(story_plan, monkeypatch, operation):
    pid = story_plan["id"]
    version = plan_history.record_revision(pid, "initial")
    original = plan_history.atomic_write
    seen = []
    def checked(path, text):
        if path.name == "story.json":
            assert planner.get_plan(pid)["status"] == "draft"
            seen.append(path)
        return original(path, text)
    monkeypatch.setattr(plan_history, "atomic_write", checked)
    if operation == "edit":
        planner.update_plan(pid, planner.get_plan(pid)["clips"])
    elif operation == "swap":
        planner.swap_broll(pid, 1, story_plan["asset_ids"][3])
    else:
        plan_history.restore(pid, version)
    assert seen


def test_missing_or_mismatched_story_cannot_be_reapproved(story_plan):
    pid = story_plan["id"]
    planner.mark_draft(pid)
    sidecar = story_plan["sidecar"]
    sidecar["beats"][0]["clips"][0]["end"] = 1.0
    path = story.story_dir(pid) / "story.json"
    path.write_text(json.dumps(sidecar))
    with pytest.raises(ValueError, match="not synchronized"):
        planner.approve_plan(pid)
    path.unlink()
    with pytest.raises(ValueError, match="not synchronized"):
        planner.approve_plan(pid)
    assert planner.get_plan(pid)["status"] == "draft"
