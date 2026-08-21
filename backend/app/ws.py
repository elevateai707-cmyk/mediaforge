"""WebSocket hub: /ws endpoint handler + broadcast helpers.

Server -> client events (JSON), per API contract:
  {"type":"job","job_id":...,"kind":"scan|ai|render|touchup",
   "status":"running|done|error|cancelled","progress":0.0,"message":"..."}
  {"type":"job.batch","job_id":...,"progress":0.0,"done":N,"total":M}
  {"type":"gpu","mode":"cuda|cpu","vram_mb":...,"warning":"..."}

The gpu event is sent once on connect and again whenever the mode changes.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from fastapi import WebSocket

log = logging.getLogger("mediaforge.ws")


class WSManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._last_gpu: Optional[dict] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections.add(ws)
        if self._last_gpu:
            await self._safe_send(ws, self._last_gpu)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(ws)

    async def _safe_send(self, ws: WebSocket, payload: dict) -> None:
        try:
            await ws.send_text(json.dumps(payload))
        except Exception:
            await self.disconnect(ws)

    async def broadcast(self, payload: dict) -> None:
        async with self._lock:
            conns = list(self._connections)
        for ws in conns:
            await self._safe_send(ws, payload)

    def _schedule(self, payload: dict) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            log.warning("no running event loop for ws broadcast; dropping event")
            return
        asyncio.run_coroutine_threadsafe(self.broadcast(payload), loop)

    def set_gpu(self, payload: dict) -> None:
        self._last_gpu = payload
        self._schedule(payload)

    def emit(self, payload: dict) -> None:
        """Schedule a broadcast from any thread/context."""
        self._schedule(payload)


manager = WSManager()


def broadcast_job(job_id: str, kind: str, status: str, progress: float,
                  message: str) -> None:
    """Contract event for a job status change (called from job runner)."""
    manager.emit({
        "type": "job",
        "job_id": job_id,
        "kind": kind,
        "status": status,
        "progress": round(float(progress), 4),
        "message": message or "",
    })


def broadcast_batch(job_id: str, progress: float, done: int, total: int) -> None:
    """Contract event for per-item batch progress."""
    manager.emit({
        "type": "job.batch",
        "job_id": job_id,
        "progress": round(float(progress), 4),
        "done": done,
        "total": total,
    })


def broadcast_gpu(mode: str, vram_mb: int, warning: str = "") -> None:
    manager.set_gpu({"type": "gpu", "mode": mode, "vram_mb": vram_mb,
                     "warning": warning})


def notify(kind: str, job_id: str, status: str, progress: float,
           message: str) -> None:
    """Generic notify used by every long task (scan/ai/render/touchup)."""
    broadcast_job(job_id, kind, status, progress, message)
