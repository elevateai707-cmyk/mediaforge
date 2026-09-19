"""DaVinci Resolve scripting bridge.

POST /api/export/resolve drives a *live* Resolve instance over its Python
scripting API (DaVinciResolveScript / fusionscript). On Linux the module lives
at /opt/resolve/apis/Scripting/Module and needs /opt/resolve/libs on
LD_LIBRARY_PATH — both are set up automatically here (see docs/RESOLVE.md).

If the module is missing, cannot be imported, or resolve.scriptapp('Resolve')
returns None, :class:`ResolveUnavailable` is raised; main.py maps it to HTTP
503 with the exact contract message.

Every Resolve call after connect is individually guarded so partial API
mismatches degrade gracefully instead of crashing the endpoint. Exports are
serialized with a lock (the Resolve API is single-connection).
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from contextlib import contextmanager
from typing import Any, Optional

from .. import config, models
from ..db import SessionLocal
from .render import probe_fps

log = logging.getLogger("mediaforge.resolve")

# Exact 503 detail message mandated by API_CONTRACT.md.
RESOLVE_UNAVAILABLE_MSG = (
    "DaVinci Resolve is not running or the scripting module was not found. "
    "Start Resolve once, enable 'External scripting using' = Network/Local "
    "in Preferences > System > General, then retry. See docs/RESOLVE.md."
)

_export_lock = threading.Lock()


class ResolveUnavailable(Exception):
    """Raised when Resolve cannot be reached; maps to HTTP 503."""


# ---------------------------------------------------------------------------
# Module loading
# ---------------------------------------------------------------------------

@contextmanager
def _prepared_env():
    """Expose the Resolve libs/module dirs for the duration of the import only.

    The mutation MUST be undone. ``os.environ`` is process-global, so leaking
    ``LD_LIBRARY_PATH=/opt/resolve/libs`` makes every later ffmpeg/ffprobe child
    load Resolve's bundled libavutil and exit 127 — one Resolve export (even a
    failed one) would break thumbnails and renders until the backend restarted.

    Note that glibc snapshots ``LD_LIBRARY_PATH`` at process start, so this
    assignment never affected this process's own ``dlopen`` anyway; the import
    below resolves through ``sys.path``. It is kept, scoped, only for anything
    the Resolve module may spawn itself.
    """
    libs = config.RESOLVE_LIBS_DIR
    module_dir = config.RESOLVE_MODULE_DIR
    saved = {k: os.environ.get(k) for k in ("LD_LIBRARY_PATH", "PYTHONPATH")}
    os.environ["LD_LIBRARY_PATH"] = (
        f"{libs}:{saved['LD_LIBRARY_PATH'] or ''}"
    )
    os.environ["PYTHONPATH"] = (
        f"{module_dir}:{saved['PYTHONPATH'] or ''}"
    )
    if module_dir not in sys.path:
        sys.path.insert(0, module_dir)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _load_dvr():
    """Import the Resolve scripting module; None when unavailable."""
    with _prepared_env():
        return _import_dvr()


def _import_dvr():
    try:
        import DaVinciResolveScript as dvr  # type: ignore
        return dvr
    except Exception as exc:  # noqa: BLE001
        log.debug("DaVinciResolveScript import failed: %s", exc)
    try:
        import fusionscript as dvr  # type: ignore  # noqa: F401
        return dvr
    except Exception as exc:  # noqa: BLE001
        log.debug("fusionscript import failed: %s", exc)
    return None


def resolve_available() -> bool:
    """True when the scripting module exists on disk (config endpoint)."""
    return os.path.isdir(config.RESOLVE_MODULE_DIR)


def _connect(retries: int = 5) -> Any:
    """Import + connect to the running Resolve; raise ResolveUnavailable."""
    dvr = _load_dvr()
    if dvr is None:
        raise ResolveUnavailable(RESOLVE_UNAVAILABLE_MSG)
    resolve = None
    last_err: Optional[str] = None
    # First connection can be slow (Resolve spawns the server lazily) — retry.
    for attempt in range(1, retries + 1):
        try:
            resolve = dvr.scriptapp("Resolve")
        except Exception as exc:  # noqa: BLE001
            last_err = f"{type(exc).__name__}: {exc}"
            resolve = None
        if resolve is not None:
            break
        log.info("resolve connect attempt %d/%d returned None", attempt, retries)
        time.sleep(1.0 * attempt)
    if resolve is None:
        log.warning("resolve unavailable: %s", last_err or "scriptapp returned None")
        raise ResolveUnavailable(RESOLVE_UNAVAILABLE_MSG)
    return resolve


# ---------------------------------------------------------------------------
# Plan -> Resolve timeline
# ---------------------------------------------------------------------------

def _load_plan(plan_id: str) -> tuple[dict, list[tuple[models.EditClip, models.Asset]]]:
    with SessionLocal() as db:
        plan = db.get(models.EditPlan, plan_id)
        if plan is None:
            raise ResolveUnavailable(f"plan {plan_id!r} not found")
        rows = []
        for c in plan.clips:
            asset = db.get(models.Asset, c.asset_id)
            if asset is None:
                continue
            rows.append((c, asset))
        if not rows:
            raise ResolveUnavailable(f"plan {plan_id!r} has no usable clips")
        return {
            "id": plan.id, "intent": plan.intent, "summary": plan.summary,
            "target_ratio": plan.target_ratio,
        }, rows


def export_to_resolve(plan_id: str) -> dict:
    """Legacy route now delegates to the checked revision-aware bridge.

    Requires a compatible cuts-only editor document; never appends full clips
    plus trimmed duplicates or silently drops failed Resolve calls.
    """
    from . import project, resolve_workflow
    # Preserve the established unavailable response before legacy plan validation.
    with _export_lock:
        _connect(retries=1)
    try:
        doc = project.get_project(plan_id)["project"]
        return resolve_workflow.export(plan_id, doc["revision"])
    except ValueError as exc:
        raise ResolveUnavailable(str(exc)) from exc
