"""Persistence, selection helpers and background analysis for ai/motion.py results."""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

from sqlalchemy import or_

from .. import models
from ..db import SessionLocal
from . import motion

log = logging.getLogger("mediaforge.motion")

DRONE_MARKERS = ("blue-drone-1o1", "/dji_")
_COLUMNS = ("version", "runtime_s", "fps", "fps_avg", "is_hyperlapse", "hyper_signal", "disc_disp", "disc_fail",
            "daypart", "luma_mean", "luma_p5", "luma_p95", "highlight_frac", "shadow_frac",
            "smoothness", "jerk_norm", "reversals_per_s", "stopstart_per_s", "mean_speed", "error")
_write_lock = threading.Lock()


def is_drone_path(path: str) -> bool:
    low = (path or "").lower()
    return any(m in low for m in DRONE_MARKERS)


def save_record(asset_id: int, rec: dict) -> None:
    with _write_lock, SessionLocal() as db:
        row = db.get(models.MotionAnalysis, asset_id) or models.MotionAnalysis(asset_id=asset_id)
        for col in _COLUMNS:
            if col in rec:
                value = rec[col]
                setattr(row, col, int(value) if col == "is_hyperlapse" and value is not None else value)
        row.profile = json.dumps(rec.get("profile") or [])
        row.windows = json.dumps(rec.get("windows") or [])
        row.analyzed_at = datetime.now(timezone.utc)
        db.add(row)
        db.commit()


def motion_dict(row: Optional["models.MotionAnalysis"]) -> Optional[dict]:
    """API view of a stored analysis (no per-second profile)."""
    if row is None:
        return None
    return {
        "is_hyperlapse": bool(row.is_hyperlapse), "hyper_signal": row.hyper_signal,
        "daypart": row.daypart, "smoothness": row.smoothness, "fps": row.fps, "fps_avg": row.fps_avg,
        "luma_mean": row.luma_mean, "luma_p5": row.luma_p5, "luma_p95": row.luma_p95,
        "highlight_frac": row.highlight_frac, "shadow_frac": row.shadow_frac,
        "jerk_norm": row.jerk_norm, "reversals_per_s": row.reversals_per_s,
        "stopstart_per_s": row.stopstart_per_s, "mean_speed": row.mean_speed,
        "disc_disp": row.disc_disp, "disc_fail": row.disc_fail,
        "windows": json.loads(row.windows or "[]"), "error": row.error,
        "analyzed_at": row.analyzed_at.isoformat() if row.analyzed_at else None,
    }


def windows_for(row: "models.MotionAnalysis", min_score: float, duration: Optional[float] = None) -> list[list[float]]:
    """Smooth windows (>= 3 s) at the requested score; short clips qualify whole."""
    try:
        profile = json.loads(row.profile or "[]")
    except ValueError:
        profile = []
    wins = motion.windows_from_profile(profile, float(min_score))
    if not wins and duration and 2.0 <= duration < motion.MIN_WINDOW_S \
            and row.smoothness is not None and row.smoothness >= min_score:
        wins = [[0.0, round(float(duration), 2), float(row.smoothness)]]
    if duration:
        wins = [[a, min(b, round(float(duration), 2)), s] for a, b, s in wins if min(b, duration) - a >= 1.0]
    return wins


def analyze_asset(asset_id: int, force: bool = False) -> Optional[dict]:
    """Analyse one video asset and store the result. None if skipped/unusable."""
    with SessionLocal() as db:
        asset = db.get(models.Asset, asset_id)
        if asset is None or asset.kind != "video" or not asset.duration or not asset.width:
            return None
        existing = db.get(models.MotionAnalysis, asset_id)
        if existing is not None and existing.version == motion.VERSION and not existing.error and not force:
            return None
        path, w, h, dur = asset.path, int(asset.width), int(asset.height), float(asset.duration)
    if not os.path.isfile(path):
        return None
    rec = motion.analyze_video(path, w, h, dur)
    save_record(asset_id, rec)
    return rec


def pending_ids(limit: int = 50, drone_only: bool = True, force: bool = False,
                asset_ids: Optional[Iterable[int]] = None, path_prefix: Optional[str] = None) -> list[int]:
    with SessionLocal() as db:
        q = db.query(models.Asset.id, models.Asset.path).filter(
            models.Asset.kind == "video", models.Asset.duration.isnot(None))
        if asset_ids:
            q = q.filter(models.Asset.id.in_(list(asset_ids)))
        if path_prefix:
            q = q.filter(models.Asset.path.startswith(path_prefix, autoescape=True))
        # Rows with an error count as done here (no endless retry of an unreadable file);
        # an explicit analyze request with force=True redoes them.
        done = {r[0] for r in db.query(models.MotionAnalysis.asset_id).filter(
            models.MotionAnalysis.version == motion.VERSION).all()}
        out = []
        for aid, path in q.order_by(models.Asset.id).all():
            if drone_only and not asset_ids and not path_prefix and not is_drone_path(path):
                continue
            if aid in done and not force:
                continue
            out.append(aid)
            if len(out) >= limit:
                break
        return out


def analyze_many(ids: list[int], force: bool = False,
                 progress: Optional[Callable[[float, str], None]] = None, cancel_flag=None,
                 workers: int = 2) -> dict:
    """Analyse assets with a small CPU pool. Returns counts and total runtime."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    done = failed = 0
    runtime = 0.0
    total = len(ids)
    with ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="mf-motion") as pool:
        futs = {pool.submit(analyze_asset, i, force): i for i in ids}
        for n, fut in enumerate(as_completed(futs), 1):
            if cancel_flag is not None and getattr(cancel_flag, "cancelled", False):
                for f in futs:
                    f.cancel()
                break
            try:
                rec = fut.result()
            except Exception as exc:  # noqa: BLE001
                log.warning("motion analysis of asset %s failed: %s", futs[fut], exc)
                failed += 1
                continue
            if rec:
                done += 1
                runtime += rec.get("runtime_s") or 0.0
                failed += 1 if rec.get("error") else 0
            if progress:
                progress(n / max(1, total), f"analysed {n}/{total}")
    return {"analysed": done, "failed": failed, "total": total, "cpu_seconds": round(runtime, 1)}


def status() -> dict:
    with SessionLocal() as db:
        rows = db.query(models.MotionAnalysis).all()
        total_drone = sum(1 for (p,) in db.query(models.Asset.path).filter(
            models.Asset.kind == "video", models.Asset.duration.isnot(None)).all() if is_drone_path(p))
    by_day: dict = {}
    for r in rows:
        key = ("hyperlapse-" if r.is_hyperlapse else "") + (r.daypart or "unknown")
        by_day[key] = by_day.get(key, 0) + 1
    return {"analysed": len(rows), "drone_assets": total_drone,
            "pending": max(0, total_drone - len(rows)), "by_class": by_day,
            "version": motion.VERSION}
