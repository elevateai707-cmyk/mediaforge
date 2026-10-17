"""Immutable local edit snapshots, including story captions (never source media)."""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from .. import config, models
from ..db import SessionLocal

_guard = threading.Lock()
_locks: dict[str, threading.RLock] = {}
ARTIFACTS = ("story.json", "captions.srt", "captions.vtt", "captions.ass")


def validate_id(plan_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", plan_id):
        raise ValueError("invalid plan id")
    return plan_id


def plan_lock(plan_id: str):
    validate_id(plan_id)
    with _guard:
        return _locks.setdefault(plan_id, threading.RLock())


def atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, encoding="utf-8", delete=False) as stream:
            temp = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        if temp:
            temp.unlink(missing_ok=True)


def _directory(plan_id):
    return config.DATA_DIR / "plan_versions" / validate_id(plan_id)


def _snapshot(plan_id: str) -> dict:
    from .planner import _clip_dict
    with SessionLocal() as db:
        plan = db.get(models.EditPlan, plan_id)
        if plan is None:
            raise LookupError("plan not found")
        data = {key: getattr(plan, key) for key in ("intent", "status", "summary", "parsed_json", "target_ratio", "total_duration")}
        data["clips"] = [_clip_dict(c) for c in plan.clips]
    story_dir = config.EXPORTS_DIR / "story" / plan_id
    data["artifacts"] = {name: (story_dir / name).read_text(encoding="utf-8")
                         for name in ARTIFACTS if (story_dir / name).is_file()}
    return data


def record_revision(plan_id: str, reason: str = "edit") -> int:
    with plan_lock(plan_id):
        data = _snapshot(plan_id)
        folder = _directory(plan_id)
        paths = sorted(folder.glob("*.json")) if folder.is_dir() else []
        last = json.loads(paths[-1].read_text()) if paths else None
        if last and last["snapshot"] == data:
            return last["version"]
        version = (last["version"] + 1) if last else 1
        payload = {"version": version, "reason": reason, "created_at": datetime.now(timezone.utc).isoformat(), "snapshot": data}
        atomic_write(folder / f"{version:08d}.json", json.dumps(payload, allow_nan=False, ensure_ascii=False))
        return version


def list_versions(plan_id: str) -> list[dict]:
    # Import legacy plans lazily into history without changing their contents.
    with plan_lock(plan_id):
        record_revision(plan_id, "current plan")
        return [{k: data[k] for k in ("version", "reason", "created_at")}
                for path in sorted(_directory(plan_id).glob("*.json"), reverse=True)
                for data in [json.loads(path.read_text())]]


def restore(plan_id: str, version: int) -> dict:
    from . import planner
    with plan_lock(plan_id):
        path = _directory(plan_id) / f"{version:08d}.json"
        if version < 1 or not path.is_file():
            raise LookupError("plan version not found")
        snapshot = json.loads(path.read_text())["snapshot"]
        record_revision(plan_id, "before restore")
        current = _snapshot(plan_id)
        out = config.EXPORTS_DIR / "story" / plan_id
        # Revoke approval in its own durable transaction before touching files.
        planner.mark_draft(plan_id)
        try:
            with SessionLocal() as db:
                plan = db.get(models.EditPlan, plan_id)
                if plan is None:
                    raise LookupError("plan not found")
                for key in ("intent", "summary", "parsed_json", "target_ratio", "total_duration"):
                    setattr(plan, key, snapshot[key])
                plan.status = "draft"
                for clip in list(plan.clips):
                    db.delete(clip)
                db.flush()
                for pos, clip in enumerate(snapshot["clips"]):
                    db.add(models.EditClip(plan_id=plan_id, position=pos, **clip))
                for name in ARTIFACTS:
                    if name in snapshot["artifacts"]:
                        atomic_write(out / name, snapshot["artifacts"][name])
                    elif (out / name).exists():
                        (out / name).unlink()
                db.commit()
        except Exception:
            for name in ARTIFACTS:
                if name in current["artifacts"]:
                    atomic_write(out / name, current["artifacts"][name])
                else:
                    (out / name).unlink(missing_ok=True)
            raise
        record_revision(plan_id, f"restored version {version}")
        return planner.get_plan(plan_id)
