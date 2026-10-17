"""Watch media directories and feed the single scan queue.

Events are debounced and enqueued as settle-checked scan requests (files still
being copied are re-checked later, see scanner.is_settled). A supervisor thread
guards against the failure the plain observer cannot see: a USB drive that
dropped and was remounted leaves inotify watching a dead mount forever. Every
SUPERVISE_S it verifies each watched directory still exists with the same
identity (st_dev, st_ino, mount id); if not, the observer is restarted and a
catch-up scan of that directory is queued. The same thread queues a periodic
safety-net scan for events the watcher missed (default every 30 minutes,
``MF_RESCAN_INTERVAL_MIN``; 0 disables).
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .. import config, persist
from . import scanner
from .scanqueue import scan_queue

log = logging.getLogger("mediaforge.watcher")

DEBOUNCE_S = 2.0
SUPERVISE_S = float(os.environ.get("MF_WATCH_SUPERVISE_S", "60"))
SAFETY_SCAN_S = float(os.environ.get("MF_RESCAN_INTERVAL_MIN", "30")) * 60.0

_observer: Optional[Observer] = None
_timer: Optional[threading.Timer] = None
_pending: set[str] = set()
_lock = threading.Lock()
# dir -> {"identity": tuple|None, "status": str, "since": iso, "restarted_at": iso|None}
_dirs: dict[str, dict] = {}
_supervisor: Optional[threading.Thread] = None
_stop_evt = threading.Event()
_last_safety = 0.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _Handler(FileSystemEventHandler):
    def on_created(self, event: FileSystemEvent) -> None:
        _note(event)

    def on_modified(self, event: FileSystemEvent) -> None:
        _note(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        _note(event)


def _note(event: FileSystemEvent) -> None:
    path = getattr(event, "dest_path", None) or getattr(event, "src_path", None) or ""
    if not path:
        return
    p = Path(path)
    if event.is_directory:
        # A modified-directory event fires for every child change; the file
        # events carry the useful path.
        if p.name.startswith(".") or event.event_type == "modified":
            return
    elif p.suffix.lower() not in config.SUPPORTED_EXTS or scanner.is_ignored_name(p.name):
        return
    global _timer
    with _lock:
        _pending.add(str(p))
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(DEBOUNCE_S, _flush)
        _timer.daemon = True
        _timer.start()


def _flush() -> None:
    with _lock:
        paths = sorted(_pending)
        _pending.clear()
    if not paths:
        return
    log.info("watcher enqueue scan for %d path(s)", len(paths))
    scan_queue.enqueue_threadsafe(paths, settle=True, source="watcher")


# ---------------------------------------------------------------------------
# identity of a watched directory (detects unmount/remount)
# ---------------------------------------------------------------------------

def _mount_id(path: str) -> Optional[str]:
    """Mount id of the mount containing ``path`` (Linux mountinfo; None if unknown)."""
    try:
        real = os.path.realpath(path)
        best, best_id = "", None
        with open("/proc/self/mountinfo", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) < 5:
                    continue
                mnt = parts[4].replace("\\040", " ")
                if (real == mnt or real.startswith(mnt.rstrip("/") + "/")) and len(mnt) > len(best):
                    best, best_id = mnt, parts[0]
        return best_id
    except OSError:
        return None


def _identity(path: str) -> Optional[tuple]:
    """(st_dev, st_ino, mount_id) or None when the directory is not accessible."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    if not os.path.isdir(path):
        return None
    return (st.st_dev, st.st_ino, _mount_id(path))


# ---------------------------------------------------------------------------
# observer lifecycle
# ---------------------------------------------------------------------------

def _start_observer(dirs: list[str], restarted: Optional[set[str]] = None) -> None:
    global _observer
    _stop_observer()
    observer = Observer()
    handler = _Handler()
    for d in dirs:
        ident = _identity(d)
        info = _dirs.setdefault(d, {"since": _now(), "restarted_at": None})
        if ident is None:
            info.update(identity=None, status="missing")
            continue
        try:
            observer.schedule(handler, d, recursive=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("cannot watch %s: %s", d, exc)
            info.update(identity=None, status="dead")
            continue
        info.update(identity=ident, status="watching")
        if restarted and d in restarted:
            info["restarted_at"] = _now()
        log.info("watching %s", d)
    observer.daemon = True
    observer.start()
    _observer = observer


def _stop_observer() -> None:
    global _observer
    if _observer is not None:
        try:
            _observer.stop()
            _observer.join(timeout=2)
        except Exception:  # noqa: BLE001
            pass
        _observer = None


def check_once(now: Optional[float] = None) -> dict:
    """One supervisor pass (public so tests can drive it). Returns what it did."""
    global _last_safety
    did: dict = {"restarted": [], "safety_scan": False}
    if not persist.watcher_enabled():
        return did
    dirs = list(persist.media_dirs())
    changed: list[str] = []
    for d in dirs:
        info = _dirs.get(d)
        ident = _identity(d)
        if info is None:
            changed.append(d)            # newly configured directory
        elif ident is None:
            if info.get("status") != "missing":
                log.warning("watched dir %s is gone; waiting for it to return", d)
            info["status"] = "missing"
        elif info.get("identity") != ident or info.get("status") != "watching":
            log.warning("watched dir %s changed/returned (%s -> %s); restarting watcher",
                        d, info.get("identity"), ident)
            changed.append(d)
    observer_dead = _observer is None or not _observer.is_alive()
    if changed or (observer_dead and dirs):
        _start_observer(dirs, restarted=set(changed))
        watching = [d for d in dirs if _dirs.get(d, {}).get("status") == "watching"]
        for d in changed:
            if d in watching:
                scan_queue.enqueue_threadsafe([d], settle=True, source="watcher-restart")
                did["restarted"].append(d)
        if observer_dead and not changed:
            did["restarted"] = watching
    now = time.monotonic() if now is None else now
    if SAFETY_SCAN_S > 0 and dirs and now - _last_safety >= SAFETY_SCAN_S:
        _last_safety = now
        scan_queue.note_catchup()
        scan_queue.enqueue_threadsafe(dirs, settle=True, source="periodic")
        did["safety_scan"] = True
    return did


def _supervise() -> None:
    while not _stop_evt.wait(SUPERVISE_S):
        try:
            check_once()
        except Exception as exc:  # noqa: BLE001 - the supervisor must never die
            log.exception("watcher supervisor error: %s", exc)


def start(loop: Optional[asyncio.AbstractEventLoop] = None) -> bool:
    """Start (or restart) the observer on the media dirs plus the supervisor."""
    global _supervisor, _last_safety
    stop()
    if loop is not None:
        scan_queue.bind_loop(loop)
    elif scan_queue._loop is None:
        try:
            scan_queue.bind_loop(asyncio.get_event_loop())
        except RuntimeError:
            pass
    if not persist.watcher_enabled():
        log.info("folder watcher disabled")
        return False
    dirs = list(persist.media_dirs())
    if not dirs:
        log.info("folder watcher: no media dirs to watch")
        return False
    _dirs.clear()
    _start_observer(dirs)
    _last_safety = time.monotonic()
    _stop_evt.clear()
    _supervisor = threading.Thread(target=_supervise, name="mf-watch-supervisor", daemon=True)
    _supervisor.start()
    return True


def stop() -> None:
    global _timer, _supervisor
    _stop_evt.set()
    with _lock:
        if _timer is not None:
            _timer.cancel()
            _timer = None
    _stop_observer()
    if _supervisor is not None and _supervisor.is_alive() \
            and _supervisor is not threading.current_thread():
        _supervisor.join(timeout=2)
    _supervisor = None


def status() -> dict:
    """Per-directory watcher status for /api/health."""
    dirs = {d: {"status": info.get("status"), "since": info.get("since"),
                "restarted_at": info.get("restarted_at")}
            for d, info in _dirs.items()}
    return {
        "enabled": persist.watcher_enabled(),
        "observer_alive": bool(_observer is not None and _observer.is_alive()),
        "supervisor_alive": bool(_supervisor is not None and _supervisor.is_alive()),
        "dirs": dirs,
        "safety_scan_interval_s": SAFETY_SCAN_S,
    }
