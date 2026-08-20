"""Resumable AI pipeline worker (embedding -> scenes -> transcript -> faces
-> caption -> indexed).

Every stage is guarded by ``Asset.status`` and persisted to SQLite, so a
restart resumes exactly where the previous run stopped (assets with
status='indexed' are skipped entirely). A model that fails to load marks its
phase unavailable (gpu.py registry) and the worker skips that stage — it
never crashes and always advances the status so the queue makes progress.

Stage order:  pending -> embedding_done -> scenes_done -> transcript_done
              -> faces_done -> captioned -> indexed
"""
from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

import numpy as np
from sqlalchemy.orm import Session

from .. import config, models
from ..db import SessionLocal, store_embedding
from ..ingest import thumbs
from ..ingest.place import upgrade_place_from_text
from ..jobs import JobManager
from ..ws import broadcast_batch, broadcast_job
from . import aesthetic, captions, clip, faces, gpu, scenes, whisper

log = logging.getLogger("mediaforge.pipeline")

STAGE_ORDER = {
    "pending": 0,
    "embedding_done": 1,
    "scenes_done": 2,
    "transcript_done": 3,
    "faces_done": 4,
    "captioned": 5,
    "indexed": 6,
}

AI_JOB_ID = "ai-worker"
ProgressFn = Optional[Callable[[float, str], None]]


# ---------------------------------------------------------------------------
# Stage helpers
# ---------------------------------------------------------------------------

def _representative_image(db: Session, asset: models.Asset) -> Optional[str]:
    """A single image for CLIP/aesthetic/caption phases.

    Photos use the original file; videos prefer the poster frame (created at
    scan time), then the thumbnail, then a fresh extraction at 10%.
    """
    if asset.kind == "photo":
        return asset.path
    for p in (asset.poster_path, asset.thumb_path):
        if p:
            if os.path.exists(p):
                return p
    at = 0.0
    if asset.duration:
        at = min(max(0.0, asset.duration * 0.1), max(0.0, asset.duration - 0.1))
    out = config.THUMBS_DIR / f"{asset.id}" / "ai_frame.jpg"
    out.parent.mkdir(parents=True, exist_ok=True)
    return thumbs.extract_frame(asset.path, str(out), at=at)


def _rebuild_fts(db: Session, asset: models.Asset) -> None:
    """Refresh the asset_fts row (transcript + caption text)."""
    transcript_text = " ".join(
        (s.text or "") for s in asset.transcript
    ).strip()
    caption_text = (asset.caption or "").strip()
    scene_text = " ".join(
        (s.caption or "") for s in asset.scenes
    ).strip()
    payload = f"{transcript_text}\n{caption_text}\n{scene_text}".strip()
    db.execute(
        "DELETE FROM asset_fts WHERE asset_id = :a",
        {"a": asset.id},
    )
    db.execute(
        "INSERT INTO asset_fts (asset_id, transcript, caption) "
        "VALUES (:a, :t, :c)",
        {"a": asset.id, "t": payload, "c": caption_text},
    )


# ---------------------------------------------------------------------------
# Per-asset processing (single stage guard, resumable)
# ---------------------------------------------------------------------------

def process_asset(db: Session, asset_id: int,
                  progress: ProgressFn = None) -> dict:
    """Run all pending stages for one asset. Never raises.

    Returns {"stages_run": [...], "status": final_status}.
    """
    asset = db.get(models.Asset, asset_id)
    if asset is None:
        return {"stages_run": [], "status": "missing"}
    prog = progress or (lambda _f, _m: None)
    stages_run: list[str] = []
    cur = STAGE_ORDER.get(asset.status, 0)

    # ---- 1. embedding + aesthetic ----------------------------------------
    if cur < 1:
        img = _representative_image(db, asset)
        if img:
            vec = clip.embed_image(img)
            if vec is not None:
                store_embedding(db, asset.id,
                                np.asarray(vec, dtype=np.float32))
                score = aesthetic.score_image(img)
                if score is not None:
                    asset.aesthetic_score = round(float(score), 2)
            else:
                log.info("asset %s: clip unavailable; skipping embedding",
                         asset.id)
        asset.status = "embedding_done"
        asset.status_error = None
        db.commit()
        stages_run.append("embedding")
        prog(0.2, f"asset {asset_id}: embedding done")
        _unload_if_tight(clip.unload)

    # ---- 2. scenes (videos only) ------------------------------------------
    if cur < 2:
        if asset.kind == "video":
            try:
                scene_ranges = scenes.detect_scenes(asset.path, fps=30.0)
                db.query(models.Scene).filter_by(asset_id=asset.id).delete()
                db.flush()
                for i, (start, end) in enumerate(scene_ranges):
                    db.add(models.Scene(asset_id=asset.id, index=i,
                                        start=float(start), end=float(end)))
                db.flush()
                frames = scenes.extract_scene_frames(asset.path, scene_ranges,
                                                     asset.id)
                rows = (db.query(models.Scene)
                        .filter_by(asset_id=asset.id)
                        .order_by(models.Scene.index).all())
                for s, fp in zip(rows, frames):
                    s.frame_path = fp
                    if fp:
                        s.aesthetic_score = aesthetic.score_image(fp)
                asset.scene_count = len(rows)
            except Exception as exc:  # noqa: BLE001 - never crash the worker
                log.exception("scene detection failed for asset %s", asset.id)
                asset.scene_count = 0
        else:
            asset.scene_count = 0
        asset.status = "scenes_done"
        db.commit()
        stages_run.append("scenes")
        prog(0.4, f"asset {asset_id}: scenes done ({asset.scene_count})")

    # ---- 3. transcript (videos only) ---------------------------------------
    if cur < 3:
        if asset.kind == "video" and not config.SKIP_WHISPER:
            try:
                result = whisper.transcribe(asset.path)
                if result and result.get("segments"):
                    db.query(models.TranscriptSegment) \
                        .filter_by(asset_id=asset.id).delete()
                    db.flush()
                    for s in result["segments"]:
                        db.add(models.TranscriptSegment(
                            asset_id=asset.id, start=s["start"],
                            end=s["end"], text=s["text"]))
                    asset.has_transcript = 1
                    joined = " ".join(
                        (s.get("text") or "") for s in result["segments"]
                    )
                    upgrade_place_from_text(asset, joined, "transcript")
            except Exception as exc:  # noqa: BLE001
                log.warning("transcription failed for asset %s: %s",
                            asset.id, exc)
        asset.status = "transcript_done"
        db.commit()
        stages_run.append("transcript")
        prog(0.6, f"asset {asset_id}: transcript done")
        _unload_if_tight(whisper.unload)

    # ---- 4. faces ----------------------------------------------------------
    if cur < 4:
        if not config.SKIP_FACES:
            try:
                faces.process_faces_for_asset(db, asset)
            except Exception as exc:  # noqa: BLE001
                log.warning("face detection failed for asset %s: %s",
                            asset.id, exc)
        asset.status = "faces_done"
        db.commit()
        stages_run.append("faces")
        prog(0.75, f"asset {asset_id}: faces done")
        _unload_if_tight(faces.unload)

    # ---- 5. caption ---------------------------------------------------------
    if cur < 5:
        if not config.SKIP_CAPTIONS:
            img = _representative_image(db, asset)
            if img:
                cap = captions.caption_image(img)
                if cap:
                    asset.caption = cap[:300]
                    upgrade_place_from_text(asset, asset.caption, "caption")
        asset.status = "captioned"
        db.commit()
        stages_run.append("caption")
        prog(0.9, f"asset {asset_id}: captioned")

    # ---- 6. index (FTS rebuild + terminal status) ---------------------------
    if cur < 6:
        _rebuild_fts(db, asset)
        asset.status = "indexed"
        db.commit()
        stages_run.append("indexed")
        prog(1.0, f"asset {asset_id}: indexed")

    if cur >= 6:
        log.debug("asset %s already indexed; skipping", asset_id)
    return {"stages_run": stages_run, "status": asset.status}


def next_pending_ids(limit: int = 1) -> list[int]:
    """Oldest non-indexed asset ids. Never uses (count-1) as an id."""
    with SessionLocal() as db:
        rows = (
            db.query(models.Asset.id)
            .filter(models.Asset.status != "indexed")
            .order_by(models.Asset.id.asc())
            .limit(max(1, limit))
            .all()
        )
        return [int(r[0]) for r in rows]


def _unload_if_tight(unload_fn) -> None:
    """Release a stage's weights when free VRAM is under 1 GB."""
    try:
        if gpu.vram_pressure(1024):
            unload_fn()
    except Exception as exc:
        log.debug("unload skipped: %s", exc)


def process_pending_batch(limit: int = 1, progress: ProgressFn = None) -> dict:
    """Process up to `limit` non-indexed assets (oldest first)."""
    with SessionLocal() as db:
        rows = (db.query(models.Asset)
                .filter(models.Asset.status != "indexed")
                .order_by(models.Asset.id)
                .limit(limit).all())
        if not rows:
            return {"processed": 0, "total": 0}
        total = (db.query(models.Asset)
                 .filter(models.Asset.status != "indexed").count())
        done = 0
        for asset in rows:
            process_asset(db, asset.id, progress=progress)
            done += 1
        return {"processed": done, "total": total}


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

_worker_task: Optional[asyncio.Task] = None
_worker_lock = asyncio.Lock()


def _ensure_ai_job_row() -> None:
    """Persist the fixed 'ai-worker' job row so progress survives restarts."""
    with SessionLocal() as db:
        if db.get(models.Job, AI_JOB_ID) is None:
            db.add(models.Job(id=AI_JOB_ID, kind="ai", status="running",
                              progress=0.0, message="AI worker idle"))
            db.commit()


def _report(frac: float, msg: str) -> None:
    try:
        JobManager._update_row(AI_JOB_ID, progress=frac, message=msg)
    except Exception:  # noqa: BLE001 - reporting is best-effort
        pass
    broadcast_job(AI_JOB_ID, "ai", "running", frac, msg)


def _process_one_sync(asset_id: int) -> None:
    """Run one asset through the pipeline (executor-safe)."""
    def _progress(frac: float, msg: str) -> None:
        _report(frac, f"{msg}")

    with SessionLocal() as db:
        try:
            process_asset(db, asset_id, progress=_progress)
        except Exception as exc:  # noqa: BLE001 - per-asset resilience
            log.exception("pipeline failed for asset %s", asset_id)
            asset = db.get(models.Asset, asset_id)
            if asset is not None:
                asset.status_error = f"{type(exc).__name__}: {exc}"
                db.commit()


async def _queue_loop() -> None:
    """Poll for pending assets and process them in a small parallel pool."""
    _ensure_ai_job_row()
    workers = max(1, min(config.CLIP_JOBS, 8))
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mf-ai")
    try:
        while True:
            try:
                await asyncio.sleep(2)
                if not config.AI_ENABLED:
                    return
                ids = next_pending_ids(workers)
                if not ids:
                    _report(1.0, "queue idle")
                    continue
                loop = asyncio.get_running_loop()
                futs = [
                    loop.run_in_executor(pool, _process_one_sync, asset_id)
                    for asset_id in ids
                ]
                await asyncio.gather(*futs)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - loop must survive
                log.exception("ai worker loop error: %s", exc)
                await asyncio.sleep(5)
    finally:
        pool.shutdown(wait=False)


def ensure_started() -> bool:
    """Idempotent guard: start the worker task exactly once."""
    global _worker_task
    if not config.AI_ENABLED:
        log.info("AI worker disabled (MF_AI_ENABLED=0)")
        return False
    if _worker_task is not None and not _worker_task.done():
        return True
    try:
        loop = asyncio.get_event_loop()
        _worker_task = loop.create_task(_queue_loop())
        log.info("AI pipeline worker started")
        return True
    except RuntimeError as exc:
        log.warning("could not start AI worker: %s", exc)
        return False


def start_ai_worker(app: Any) -> None:
    """Startup hook: called from app.main after tables exist."""
    ensure_started()
