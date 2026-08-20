"""Watch persisted media directories and enqueue a debounced rescan."""
from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Optional

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .. import config, persist
from ..jobs import jobs
from . import scanner

log = logging.getLogger("mediaforge.watcher")

_observer: Optional[Observer] = None
_loop: Optional[asyncio.AbstractEventLoop] = None
_timer: Optional[threading.Timer] = None
_pending: set[str] = set()
_lock = threading.Lock()
DEBOUNCE_S = 2.0


class _Handler(FileSystemEventHandler):
    def on_created(self, event: FileSystemEvent) -> None:
        _note(event)

    def on_modified(self, event: FileSystemEvent) -> None:
        _note(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        _note(event)


def _note(event: FileSystemEvent) -> None:
    src = getattr(event, "src_path", None) or ""
    dest = getattr(event, "dest_path", None) or ""
    path = dest or src
    if not path:
        return
    p = Path(path)
    if p.is_dir() or (p.suffix.lower() not in config.SUPPORTED_EXTS and not event.is_directory):
        if not event.is_directory and p.suffix.lower() not in config.SUPPORTED_EXTS:
            return
    with _lock:
        global _timer
        _pending.add(str(Path(path).parent if not event.is_directory else path))
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(DEBOUNCE_S, _flush)
        _timer.daemon = True
        _timer.start()


def _flush() -> None:
    with _lock:
        paths = list(_pending)
        _pending.clear()
    if not paths or _loop is None:
        return
    log.info("watcher enqueue scan for %s", paths)
    _loop.call_soon_threadsafe(_kick_scan, paths)


def _kick_scan(paths: list[str]) -> None:
    async def factory(progress, flag):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, lambda: scanner.scan_paths(paths, progress=progress, cancel_flag=flag)
        )

    try:
        jobs.run("scan", factory)
    except Exception as exc:
        log.warning("watcher could not enqueue scan: %s", exc)


def start(loop: Optional[asyncio.AbstractEventLoop] = None) -> bool:
    """Start (or restart) the observer on persisted media_dirs."""
    global _observer, _loop
    stop()
    if not persist.watcher_enabled():
        log.info("folder watcher disabled")
        return False
    dirs = [d for d in persist.media_dirs() if Path(d).is_dir()]
    if not dirs:
        log.info("folder watcher: no media dirs to watch")
        return False
    _loop = loop or asyncio.get_event_loop()
    observer = Observer()
    handler = _Handler()
    for d in dirs:
        observer.schedule(handler, d, recursive=True)
        log.info("watching %s", d)
    observer.daemon = True
    observer.start()
    _observer = observer
    return True


def stop() -> None:
    global _observer, _timer
    with _lock:
        if _timer is not None:
            _timer.cancel()
            _timer = None
    if _observer is not None:
        try:
            _observer.stop()
            _observer.join(timeout=2)
        except Exception:
            pass
        _observer = None
