"""In-process asyncio job queue with SQLite persistence.

Every long-running operation (scan, ai, render, touchup) is wrapped in a Job
row (status/progress/message) and runs as an asyncio task. Progress updates
are persisted to SQLite and broadcast over the WebSocket by ws.py.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy.orm import Session

from . import models
from .db import SessionLocal

log = logging.getLogger("mediaforge.jobs")

ProgressFn = Callable[[float, str], None]

# The id of the job whose coroutine is executing (set around create_task, so the
# task's copied context carries it). Lets a factory update its own row.
_current_job: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "mf_current_job", default=None)

# Long-lived status row owned by the AI worker (not a real job); hidden from
# job lists and never reconciled as a ghost.
AI_JOB_ID = "ai-worker"
ACTIVE_STATUSES = ("running", "queued")
FINISHED_STATUSES = ("done", "error", "cancelled", "interrupted")


class _CancelFlag:
    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled


class JobManager:
    """Registry of running jobs + SQLite persistence."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancel_flags: dict[str, _CancelFlag] = {}
        self._lock = asyncio.Lock()

    # -- persistence ----------------------------------------------------------
    @staticmethod
    def _create_row(job_id: str, kind: str, status: str = "running") -> None:
        with SessionLocal() as db:
            db.add(models.Job(id=job_id, kind=kind, status=status,
                              progress=0.0, message="queued"))
            db.commit()

    @staticmethod
    def _update_row(job_id: str, status: Optional[str] = None,
                    progress: Optional[float] = None,
                    message: Optional[str] = None,
                    meta: Optional[dict] = None) -> None:
        with SessionLocal() as db:
            job = db.get(models.Job, job_id)
            if job is None:
                return
            if status is not None:
                job.status = status
            if progress is not None:
                job.progress = round(float(progress), 4)
            if message is not None:
                job.message = message
            if meta is not None:
                job.meta = json.dumps(meta)
            if status in FINISHED_STATUSES:
                job.finished_at = datetime.now(timezone.utc)
            db.commit()

    @staticmethod
    def get_job(job_id: str) -> Optional[dict]:
        with SessionLocal() as db:
            job = db.get(models.Job, job_id)
            if job is None:
                return None
            return {
                "id": job.id, "kind": job.kind, "status": job.status,
                "progress": job.progress, "message": job.message,
                "created_at": job.created_at.isoformat() if job.created_at else None,
                "finished_at": job.finished_at.isoformat() if job.finished_at else None,
                "meta": json.loads(job.meta) if job.meta else None,
            }

    @staticmethod
    def list_jobs(limit: int = 20) -> list[dict]:
        with SessionLocal() as db:
            rows = (db.query(models.Job)
                    .filter(models.Job.id != AI_JOB_ID)
                    .order_by(models.Job.created_at.desc())
                    .limit(limit).all())
            ids = [j.id for j in rows]
        return [j for j in (JobManager.get_job(i) for i in ids) if j is not None]

    # -- housekeeping ---------------------------------------------------------
    @staticmethod
    def reconcile_startup() -> int:
        """Mark every job left running/queued by a previous process as interrupted.

        Called once at startup, before this process creates any job, so every
        active row is by definition a ghost. The AI worker's status row is not a
        job and is reset by the worker itself.
        """
        with SessionLocal() as db:
            rows = (db.query(models.Job)
                    .filter(models.Job.status.in_(ACTIVE_STATUSES),
                            models.Job.id != AI_JOB_ID).all())
            now = datetime.now(timezone.utc)
            for job in rows:
                job.status = "interrupted"
                job.message = "interrupted by restart"
                job.finished_at = now
            ai = db.get(models.Job, AI_JOB_ID)
            if ai is not None and ai.status in ACTIVE_STATUSES:
                ai.status, ai.progress, ai.message = "done", 1.0, "AI worker idle"
            db.commit()
            n = len(rows)
        if n:
            log.warning("startup: marked %d stale job(s) interrupted", n)
        return n

    def cleanup(self, older_than_minutes: float = 0.0,
                delete_finished_older_than_days: Optional[float] = None) -> dict:
        """Mark active rows with no live task here as interrupted (ghosts) and
        optionally delete finished rows older than N days."""
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        interrupted = deleted = 0
        with SessionLocal() as db:
            q = db.query(models.Job).filter(models.Job.status.in_(ACTIVE_STATUSES),
                                            models.Job.id != AI_JOB_ID)
            for job in q.all():
                if job.id in self._tasks:
                    continue
                created = job.created_at
                if created is not None and created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if older_than_minutes and created is not None and \
                        now - created < timedelta(minutes=older_than_minutes):
                    continue
                job.status = "interrupted"
                job.message = "interrupted (stale job cleanup)"
                job.finished_at = now
                interrupted += 1
            if delete_finished_older_than_days is not None:
                cutoff = now - timedelta(days=float(delete_finished_older_than_days))
                old = (db.query(models.Job)
                       .filter(models.Job.status.in_(FINISHED_STATUSES),
                               models.Job.id != AI_JOB_ID).all())
                for job in old:
                    ref = job.finished_at or job.created_at
                    if ref is not None and ref.tzinfo is None:
                        ref = ref.replace(tzinfo=timezone.utc)
                    if ref is not None and ref < cutoff:
                        db.delete(job)
                        deleted += 1
            db.commit()
        return {"interrupted": interrupted, "deleted": deleted}

    @staticmethod
    def current_job_id() -> Optional[str]:
        return _current_job.get()

    # -- scheduling -----------------------------------------------------------
    def create_job(self, kind: str, status: str = "running") -> str:
        job_id = uuid.uuid4().hex[:12]
        self._create_row(job_id, kind, status)
        return job_id

    def cancel(self, job_id: str) -> bool:
        flag = self._cancel_flags.get(job_id)
        if flag is not None:
            flag.cancel()
            self._update_row(job_id, status="cancelled", message="cancelled by user")
            return True
        job = self.get_job(job_id)
        if job and job["status"] in ("done", "error", "cancelled"):
            return True
        return False

    def run(self, kind: str, coro_factory: Callable[[ProgressFn, _CancelFlag], Awaitable[Any]],
            meta: Optional[dict] = None, status: str = "running") -> str:
        """Create a job row and schedule the coroutine as a background task."""
        job_id = self.create_job(kind, status)
        flag = _CancelFlag()
        self._cancel_flags[job_id] = flag

        def progress(frac: float, msg: str) -> None:
            self._update_row(job_id, progress=frac, message=msg)

        async def _runner() -> None:
            from .ws import broadcast_job  # deferred to avoid circular import
            try:
                broadcast_job(job_id, kind, "running", 0.0, "starting")
                await coro_factory(progress, flag)
                self._update_row(job_id, status="done", progress=1.0,
                                 message="completed")
                broadcast_job(job_id, kind, "done", 1.0, "completed")
            except asyncio.CancelledError:
                self._update_row(job_id, status="cancelled", message="cancelled")
                broadcast_job(job_id, kind, "cancelled", 0.0, "cancelled")
            except Exception as exc:  # noqa: BLE001 - surface to job + health
                log.exception("job %s (%s) failed", job_id, kind)
                self._update_row(job_id, status="error", message=str(exc))
                broadcast_job(job_id, kind, "error", 0.0, str(exc))
            finally:
                self._cancel_flags.pop(job_id, None)

        token = _current_job.set(job_id)
        try:
            task = asyncio.get_event_loop().create_task(_runner())
        finally:
            _current_job.reset(token)
        self._tasks[job_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(job_id, None))
        return job_id


# Module-level singleton
jobs = JobManager()


def run_background(kind: str, coro_factory, meta: Optional[dict] = None) -> str:
    """Convenience wrapper used by API routes."""
    return jobs.run(kind, coro_factory, meta=meta)
