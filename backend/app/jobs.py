"""In-process asyncio job queue with SQLite persistence.

Every long-running operation (scan, ai, render, touchup) is wrapped in a Job
row (status/progress/message) and runs as an asyncio task. Progress updates
are persisted to SQLite and broadcast over the WebSocket by ws.py.
"""
from __future__ import annotations

import asyncio
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
    def _create_row(job_id: str, kind: str) -> None:
        with SessionLocal() as db:
            db.add(models.Job(id=job_id, kind=kind, status="running",
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
            if status in ("done", "error", "cancelled"):
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
                    .order_by(models.Job.created_at.desc())
                    .limit(limit).all())
            return [JobManager.get_job(j.id) for j in rows]

    # -- scheduling -----------------------------------------------------------
    def create_job(self, kind: str) -> str:
        job_id = uuid.uuid4().hex[:12]
        self._create_row(job_id, kind)
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
            meta: Optional[dict] = None) -> str:
        """Create a job row and schedule the coroutine as a background task."""
        job_id = self.create_job(kind)
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

        task = asyncio.get_event_loop().create_task(_runner())
        self._tasks[job_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(job_id, None))
        return job_id


# Module-level singleton
jobs = JobManager()


def run_background(kind: str, coro_factory, meta: Optional[dict] = None) -> str:
    """Convenience wrapper used by API routes."""
    return jobs.run(kind, coro_factory, meta=meta)
