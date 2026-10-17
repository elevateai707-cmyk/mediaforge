"""HTTP contracts for plan history, B-roll, and batched render metadata."""
from __future__ import annotations

import json
import shutil
import uuid

import pytest

from app import config, models
from app.db import SessionLocal, init_db
from app.edits import story


@pytest.fixture()
def approved_plan_for_batch(tmp_path):
    init_db()
    plan_id = f"batch_{uuid.uuid4().hex}"
    source = tmp_path / "source.mp4"
    source.touch()
    with SessionLocal() as db:
        asset = models.Asset(path=str(source), hash=plan_id, kind="video", duration=4)
        db.add(asset)
        db.flush()
        db.add(models.EditPlan(id=plan_id, status="approved", intent="test", total_duration=2,
                               target_ratio="9:16"))
        db.add(models.EditClip(plan_id=plan_id, position=0, asset_id=asset.id, start=0, end=2))
        db.commit()
        asset_id = asset.id
    try:
        yield plan_id
    finally:
        with SessionLocal() as db:
            plan = db.get(models.EditPlan, plan_id)
            if plan is not None:
                db.delete(plan)
            asset = db.get(models.Asset, asset_id)
            if asset is not None:
                db.delete(asset)
            db.commit()
        shutil.rmtree(config.DATA_DIR / "plan_versions" / plan_id, ignore_errors=True)


def test_batch_render_persists_all_output_paths(client, wait_job, approved_plan_for_batch, monkeypatch):
    import app.main as main

    seen = {}
    def batch(plan_id, **kwargs):
        seen["plan_id"] = plan_id
        seen.update(kwargs)
        return {"9:16": "/tmp/out-vertical.mp4", "1:1": "/tmp/out-square.mp4"}

    monkeypatch.setattr(main.render, "render_batch", batch)
    response = client.post("/api/render", json={
        "plan_id": approved_plan_for_batch, "presets": ["9:16", "1:1"],
        "captions": False, "reframe": "center", "safe_zones": {"top": .1},
        "guide_overlay": True,
    })
    assert response.status_code == 200, response.text
    job = wait_job(client, response.json()["job_id"])
    assert job["status"] == "done", job
    status = client.get(f"/api/render/{response.json()['job_id']}")
    assert status.status_code == 200
    assert status.json()["output_paths"] == {"9:16": "/tmp/out-vertical.mp4", "1:1": "/tmp/out-square.mp4"}
    assert seen["plan_id"] == approved_plan_for_batch
    assert seen["reframe"] == "center"
    assert seen["safe_zones"] == {"top": .1}
    assert seen["guide_overlay"] is True
