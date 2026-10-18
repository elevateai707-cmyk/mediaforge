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


def _story_to_resolve(plan_id: str, mp: Any, timeline: Any, fps: float) -> Optional[dict]:
    """Import voiceover.mp3 onto an audio track at t=0 and captions.srt as
    subtitles. Every step is guarded; the returned dict says what happened.
    None for non-story plans. The Resolve scripting API has no documented
    SRT-to-subtitle-track call, so subtitles use ImportMedia + AppendToTimeline
    and may need a manual Timeline > Import > Subtitle in some versions.
    """
    from . import story as story_mod

    story = story_mod.get_story(plan_id)
    if not story:
        return None
    report: dict = {"voiceover": story.get("voiceover"), "srt": story.get("srt"),
                    "voiceover_status": "not attempted", "subtitles_status": "not attempted"}
    # Audio.
    try:
        added = mp.ImportMedia([story["voiceover"]])
        if not added:
            raise RuntimeError("ImportMedia returned nothing")
        try:
            timeline.AddTrack("audio")
        except Exception:  # noqa: BLE001 - track may already exist
            pass
        try:
            n_audio = int(timeline.GetTrackCount("audio") or 1)
        except Exception:  # noqa: BLE001
            n_audio = 1
        ok = mp.AppendToTimeline([{
            "mediaPoolItem": added[0], "mediaType": 2,
            "trackIndex": max(1, n_audio), "recordFrame": 0,
        }])
        report["voiceover_status"] = "added on audio track" if ok else \
            "imported to media pool; append failed (drag it to A1 manually)"
    except Exception as exc:  # noqa: BLE001
        report["voiceover_status"] = f"failed: {type(exc).__name__}: {exc}"
    # Subtitles.
    try:
        added = mp.ImportMedia([story["srt"]])
        if not added:
            raise RuntimeError("ImportMedia returned nothing for the SRT")
        try:
            track = int(timeline.AddTrack("subtitle") and timeline.GetTrackCount("subtitle") or 1)
        except Exception:  # noqa: BLE001
            track = 1
        ok = mp.AppendToTimeline([{
            "mediaPoolItem": added[0], "trackIndex": track, "recordFrame": 0,
        }])
        report["subtitles_status"] = "added on subtitle track" if ok else \
            "SRT imported to media pool; add via Timeline > Import > Subtitle"
    except Exception as exc:  # noqa: BLE001
        report["subtitles_status"] = f"failed: {type(exc).__name__}: {exc}"
    return report


def export_to_resolve(plan_id: str) -> dict:
    """Import media + build the timeline in the live Resolve project.

    Returns {"ok": True, "resolve": version, "project": name} on success.
    Raises ResolveUnavailable (-> 503) when Resolve cannot be reached.
    """
    plan, rows = _load_plan(plan_id)
    with _export_lock:  # Resolve API is single-connection; serialize exports.
        resolve = _connect()

        try:
            pm = resolve.GetProjectManager()
            project_name = f"MediaForge-{plan['id']}"
            project = None
            try:
                project = pm.CreateProject(project_name)
            except Exception:
                project = None
            if project is None:
                try:
                    project = pm.LoadProject(project_name)
                except Exception:
                    project = None
            if project is None:
                try:
                    project = pm.GetCurrentProject()
                except Exception:
                    project = None
            if project is None:
                raise ResolveUnavailable(
                    "Could not create/load a DaVinci Resolve project.")
            try:
                project.SetName(project_name)
            except Exception:  # noqa: BLE001 - cosmetic rename only
                pass

            # -- media pool: import every unique source file -----------------
            mp = project.GetMediaPool()
            fps = 30.0
            pool_items: dict[int, Any] = {}
            for c, asset in rows:
                if asset.id in pool_items:
                    continue
                item = None
                try:
                    added = mp.AddItemsToMediaPool([asset.path])
                    if added:
                        item = added[0]
                except Exception as exc:  # noqa: BLE001
                    log.debug("AddItemsToMediaPool(%s) failed: %s",
                              asset.path, exc)
                if item is not None:
                    pool_items[asset.id] = item
                    fps = probe_fps(asset.path) or fps

            # -- timeline ----------------------------------------------------
            timeline = None
            try:
                items = [pool_items[c.asset_id] for c, _ in rows
                         if c.asset_id in pool_items]
                if items:
                    timeline = mp.CreateTimelineFromClips(project_name, items)
            except Exception as exc:  # noqa: BLE001
                log.debug("CreateTimelineFromClips failed: %s", exc)
            if timeline is None:
                try:
                    timeline = mp.CreateEmptyTimeline(project_name)
                except Exception as exc:  # noqa: BLE001
                    log.debug("CreateEmptyTimeline failed: %s", exc)
            if timeline is None:
                timeline = project.GetCurrentTimeline()
            if timeline is None:
                raise ResolveUnavailable(
                    "Could not create a timeline in the Resolve project.")

            # -- append clips with trims (start/end frame offsets) -----------
            rec_frame = 0
            for c, asset in rows:
                item = pool_items.get(c.asset_id)
                if item is None:
                    continue
                start_frame = int(round(float(c.start) * fps))
                end_frame = int(round(float(c.end) * fps))
                try:
                    ok = mp.AppendToTimeline([{
                        "mediaPoolItem": item,
                        "startFrame": start_frame,
                        "endFrame": end_frame,
                    }])
                    if not ok and isinstance(ok, bool):
                        mp.AppendToTimeline([item])
                except Exception as exc:  # noqa: BLE001
                    log.debug("AppendToTimeline failed: %s", exc)
                    try:
                        mp.AppendToTimeline([item])
                    except Exception:  # noqa: BLE001
                        pass
                rec_frame += max(1, end_frame - start_frame)

            # -- markers + clip colors (guarded) -----------------------------
            timeline_items = []
            try:
                timeline_items = timeline.GetItemListInTrack("video", 1) or []
            except Exception:  # noqa: BLE001
                pass
            palette = ["Orange", "Cyan", "Green", "Yellow", "Purple", "Pink"]
            for idx, (c, _asset) in enumerate(rows):
                ti = None
                try:
                    ti = timeline_items[idx] if idx < len(timeline_items) else None
                except Exception:  # noqa: BLE001
                    ti = None
                if ti is None:
                    continue
                try:
                    frame = int(round(float(c.start) * fps))
                    ti.AddMarker(frame, "MediaForge", c.caption or "clip",
                                 palette[idx % len(palette)], "")
                except Exception as exc:  # noqa: BLE001
                    log.debug("AddMarker failed: %s", exc)
                try:
                    ti.SetClipColor(palette[idx % len(palette)])
                except Exception as exc:  # noqa: BLE001
                    log.debug("SetClipColor failed: %s", exc)

            # -- story voiceover + word-timed subtitles (best effort) --------
            story_report = _story_to_resolve(plan_id, mp, timeline, fps)

            # -- subtitle track with captions (guarded) ----------------------
            try:
                if story_report is not None:
                    raise RuntimeError("story captions come from captions.srt")
                track_idx = timeline.AddSubtitleTrack("MediaForge Captions")
                if not track_idx:
                    track_idx = 1
                for c, _asset in rows:
                    caption = (c.caption or "").strip()
                    if not caption:
                        continue
                    try:
                        timeline.AddSubtitle(
                            int(track_idx),
                            int(round(float(c.start) * fps)),
                            int(round(float(c.end) * fps)),
                            caption,
                        )
                    except Exception as exc:  # noqa: BLE001
                        log.debug("AddSubtitle failed: %s", exc)
            except Exception as exc:  # noqa: BLE001
                log.debug("subtitle track failed: %s", exc)

            # -- version string ----------------------------------------------
            version = "unknown"
            try:
                version = str(resolve.GetVersionString() or "unknown")
            except Exception:  # noqa: BLE001
                pass
            if not version.startswith("v"):
                version = f"v{version}"

            log.info("resolve export ok: project=%s version=%s clips=%d",
                     project_name, version, len(rows))
            result = {"ok": True, "resolve": version, "project": project_name}
            if story_report is not None:
                result["story"] = story_report
            return result
        finally:
            pass  # lock released by `with`
