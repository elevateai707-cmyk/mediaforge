"""Single scan queue: at most ONE scan runs at a time.

Every source (folder watcher, POST /api/scan, import-folder, uploads, startup
catch-up, the periodic safety net, retries) enqueues paths here. Paths coalesce
into a pending set that is covered by exactly one not-yet-started "scan" job:

  * nothing queued   -> a new job is created and returned;
  * a job is queued  -> paths merge into it and its id is returned;
  * a scan is running-> the queued follow-up job waits for it (asyncio.Lock),
                        so N enqueues during a long scan produce ONE follow-up.

The same path enqueued twice is scanned once. Files the scanner defers (still
being copied, unreadable because incomplete) are re-enqueued later with a
bounded number of attempts instead of being marked permanently bad.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from ..jobs import JobManager, jobs
from . import scanner

log = logging.getLogger("mediaforge.scanqueue")

MAX_RETRIES = 8


class ScanQueue:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        # path -> (rescan, settle). Merge: rescan OR, settle AND (an explicit
        # request for a path overrides a watcher's wait-for-stable).
        self._pending: dict[str, tuple[bool, bool]] = {}
        self._pending_job: Optional[str] = None
        self._running_job: Optional[str] = None
        self._run_lock: Optional[asyncio.Lock] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._attempts: dict[str, int] = {}
        self.gave_up: list[str] = []
        self.last_catchup: Optional[str] = None
        self.last_scan_finished: Optional[str] = None
        self.last_result: Optional[dict] = None
        # Injectable for tests.
        self.scan_fn: Callable[..., dict] = scanner.scan_paths

    # -- wiring ---------------------------------------------------------------
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._run_lock = None  # re-created lazily on the new loop

    def reset(self) -> None:
        """Forget all state (tests)."""
        with self._lock:
            self._pending.clear()
            self._pending_job = None
            self._running_job = None
            self._attempts.clear()
            self.gave_up.clear()
            self._run_lock = None

    # -- enqueue ----------------------------------------------------------------
    def enqueue(self, paths: list[str], rescan: bool = False, settle: bool = False,
                source: str = "api") -> str:
        """Add paths; returns the id of the job that will cover them.

        Must be called on the event-loop thread (it schedules a job); other
        threads use ``enqueue_threadsafe``.
        """
        paths = [str(p) for p in paths if str(p).strip()]
        with self._lock:
            for p in paths:
                old = self._pending.get(p)
                self._pending[p] = ((old[0] or rescan, old[1] and settle)
                                    if old else (rescan, settle))
            if self._pending_job is None:
                self._pending_job = jobs.run("scan", self._run, status="queued")
                log.info("scan queued (%s): job %s, %d pending path(s)",
                         source, self._pending_job, len(self._pending))
            return self._pending_job

    def enqueue_threadsafe(self, paths: list[str], rescan: bool = False,
                           settle: bool = False, source: str = "watcher") -> bool:
        loop = self._loop
        if loop is None or loop.is_closed():
            log.warning("scan queue has no event loop; dropping %s enqueue", source)
            return False
        loop.call_soon_threadsafe(
            lambda: self.enqueue(paths, rescan=rescan, settle=settle, source=source))
        return True

    def note_catchup(self) -> None:
        self.last_catchup = datetime.now(timezone.utc).isoformat()

    # -- worker -------------------------------------------------------------------
    async def _run(self, progress, flag) -> None:
        job_id = JobManager.current_job_id()
        if self._run_lock is None:
            self._run_lock = asyncio.Lock()
        async with self._run_lock:
            with self._lock:
                snap = dict(self._pending)
                self._pending.clear()
                self._pending_job = None
                self._running_job = job_id
            try:
                if flag.cancelled or not snap:
                    return
                if job_id:
                    JobManager._update_row(
                        job_id, status="running",
                        message=f"scanning {len(snap)} path(s)")
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None, lambda: self._scan_snapshot(snap, progress, flag))
                self.last_result = {k: result[k] for k in ("added", "skipped")} | {
                    "errors": len(result["errors"]), "deferred": len(result["deferred"])}
                self._handle_deferred(result["deferred"])
                self._reset_attempts(snap, result)
            finally:
                with self._lock:
                    self._running_job = None
                    self.last_scan_finished = datetime.now(timezone.utc).isoformat()

    def _scan_snapshot(self, snap: dict[str, tuple[bool, bool]], progress, flag) -> dict:
        groups: dict[tuple[bool, bool], list[str]] = {}
        for path, key in snap.items():
            groups.setdefault(key, []).append(path)
        total = {"added": 0, "skipped": 0, "errors": [], "deferred": []}
        n = len(groups)
        for i, ((rescan, settle), paths) in enumerate(sorted(groups.items())):
            if flag.cancelled:
                break
            sub = (lambda f, m, i=i: progress((i + f) / n, m)) if progress else None
            res = self.scan_fn(paths, progress=sub, cancel_flag=flag,
                               rescan=rescan, settle=settle)
            total["added"] += res["added"]
            total["skipped"] += res["skipped"]
            total["errors"] += res["errors"]
            total["deferred"] += res.get("deferred", [])
        return total

    def _handle_deferred(self, deferred: list) -> None:
        loop = self._loop
        for d in deferred:
            n = self._attempts.get(d.path, 0) + 1
            self._attempts[d.path] = n
            if n > MAX_RETRIES:
                log.warning("giving up on %s after %d attempts (%s)", d.path, n - 1, d.reason)
                if d.path not in self.gave_up:
                    self.gave_up.append(d.path)
                continue
            log.info("deferring %s for %.0fs (%s, attempt %d)", d.path, d.delay, d.reason, n)
            if loop is None or loop.is_closed():
                continue
            loop.call_soon_threadsafe(
                loop.call_later, d.delay,
                lambda p=d.path: self.enqueue([p], settle=True, source="retry"))

    def _reset_attempts(self, snap: dict, result: dict) -> None:
        deferred = {d.path for d in result["deferred"]}
        for p in list(self._attempts):
            if p not in deferred and p in snap:
                self._attempts.pop(p, None)
        # A later periodic scan may retry files we gave up on.
        if not deferred:
            self.gave_up.clear()

    # -- introspection ------------------------------------------------------------
    def state(self) -> dict:
        with self._lock:
            return {
                "running_job": self._running_job,
                "queued_job": self._pending_job,
                "pending_paths": len(self._pending),
                "retrying_files": len(self._attempts),
                "gave_up": list(self.gave_up[:20]),
                "last_scan_finished": self.last_scan_finished,
                "last_result": self.last_result,
                "last_catchup": self.last_catchup,
            }


scan_queue = ScanQueue()
