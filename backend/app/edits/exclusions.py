"""Footage exclusions: ranges (or whole clips) that clip selection must never use.

Stored per asset in ``footage_exclusions``. Every selection path (story, B-roll, compilation,
swap-broll) subtracts them from the usable windows, and the renderer refuses a plan whose
clips overlap one (so an old plan cannot publish excluded footage by accident).
"""
from __future__ import annotations

import math
from typing import Iterable, Optional

from .. import models
from ..db import SessionLocal

INF = float("inf")
MIN_FREE_S = 1.0          # leftover pieces shorter than this are not offered as windows
EPS = 1e-6


class ExclusionError(ValueError):
    """Invalid exclusion request (HTTP 422)."""


def _merge(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + EPS:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def load(db, asset_ids: Optional[Iterable[int]] = None) -> dict[int, list[tuple[float, float]]]:
    """{asset_id: merged [(start, end)]}; a missing end means 'to the end' (inf)."""
    q = db.query(models.FootageExclusion)
    if asset_ids is not None:
        ids = list(asset_ids)
        if not ids:
            return {}
        q = q.filter(models.FootageExclusion.asset_id.in_(ids))
    raw: dict[int, list[tuple[float, float]]] = {}
    for r in q.all():
        raw.setdefault(r.asset_id, []).append(
            (float(r.start_s or 0.0), INF if r.end_s is None else float(r.end_s)))
    return {k: _merge(v) for k, v in raw.items()}


def is_whole(ex: list[tuple[float, float]]) -> bool:
    return any(a <= EPS and b == INF for a, b in ex)


def overlaps(ex: list[tuple[float, float]], start: float, end: float) -> bool:
    return any(start < b - EPS and end > a + EPS for a, b in ex)


def subtract(windows: list[list[float]], ex: list[tuple[float, float]],
             min_len: float = MIN_FREE_S) -> list[list[float]]:
    """Remove exclusion ranges from [[start, end, score], ...] windows."""
    out: list[list[float]] = []
    for w in windows:
        pieces = [(float(w[0]), float(w[1]))]
        for ea, eb in ex:
            nxt = []
            for a, b in pieces:
                if eb <= a or ea >= b:
                    nxt.append((a, b))
                    continue
                if ea > a:
                    nxt.append((a, ea))
                if eb < b:
                    nxt.append((eb, b))
            pieces = nxt
        for a, b in pieces:
            if b - a >= min_len:
                out.append([round(a, 3), round(b, 3), *w[2:]])
    return out


def free_ranges(duration: float, ex: list[tuple[float, float]], min_len: float = MIN_FREE_S) -> list[tuple[float, float]]:
    return [(w[0], w[1]) for w in subtract([[0.0, float(duration)]], ex, min_len)]


def first_free_start(duration: float, need: float, ex: list[tuple[float, float]], prefer: float = 0.0) -> Optional[float]:
    """Earliest start >= prefer where [start, start+need] avoids every exclusion (None if none)."""
    for a, b in free_ranges(duration, ex, min_len=need):
        start = max(a, prefer)
        if start + need <= b + EPS:
            return round(start, 3)
    return None


def _parse_ranges(ranges, whole: bool) -> list[tuple[float, Optional[float]]]:
    if whole:
        return [(0.0, None)]
    if not ranges:
        raise ExclusionError("give ranges [[start, end], ...] (end may be null = to the end) or all=true")
    if len(ranges) > 50:
        raise ExclusionError("at most 50 ranges per request")
    out: list[tuple[float, Optional[float]]] = []
    for r in ranges:
        try:
            a = float(r[0])
            b = None if len(r) < 2 or r[1] is None else float(r[1])
        except (TypeError, ValueError, IndexError):
            raise ExclusionError("each range must be [start, end] in seconds") from None
        if not math.isfinite(a) or a < 0 or (b is not None and (not math.isfinite(b) or b <= a)):
            raise ExclusionError("ranges need 0 <= start < end (end may be null)")
        out.append((a, b))
    return out


def add(asset_id: int, ranges=None, whole: bool = False, reason: str = "") -> dict:
    parsed = _parse_ranges(ranges, whole)
    with SessionLocal() as db:
        asset = db.get(models.Asset, asset_id)
        if asset is None:
            raise LookupError("asset not found")
        for a, b in parsed:
            db.add(models.FootageExclusion(asset_id=asset_id, start_s=a, end_s=b,
                                           reason=(reason or "")[:300] or None))
        db.commit()
        return describe(db, asset_id)


def remove(asset_id: int, exclusion_id: Optional[int] = None) -> dict:
    with SessionLocal() as db:
        q = db.query(models.FootageExclusion).filter_by(asset_id=asset_id)
        if exclusion_id is not None:
            q = q.filter_by(id=exclusion_id)
        n = q.delete()
        db.commit()
        out = describe(db, asset_id)
        out["removed"] = n
        return out


def describe(db, asset_id: int) -> dict:
    rows = (db.query(models.FootageExclusion).filter_by(asset_id=asset_id)
            .order_by(models.FootageExclusion.start_s).all())
    return {"asset_id": asset_id, "whole_asset": is_whole(load(db, [asset_id]).get(asset_id, [])),
            "ranges": [{"id": r.id, "start": r.start_s, "end": r.end_s, "reason": r.reason,
                        "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]}


def list_all() -> list[dict]:
    with SessionLocal() as db:
        ids = [r[0] for r in db.query(models.FootageExclusion.asset_id).distinct().all()]
        out = []
        for aid in sorted(ids):
            d = describe(db, aid)
            a = db.get(models.Asset, aid)
            d["path"] = a.path if a else None
            d["duration"] = a.duration if a else None
            out.append(d)
        return out


def clip_violations(clips: list) -> list[dict]:
    """Clips (objects with asset_id/start/end) that overlap an exclusion."""
    with SessionLocal() as db:
        ex = load(db, {c.asset_id for c in clips})
        bad = []
        for i, c in enumerate(clips, 1):
            rng = ex.get(c.asset_id)
            if rng and overlaps(rng, float(c.start), float(c.end)):
                reasons = [r.reason for r in db.query(models.FootageExclusion)
                           .filter_by(asset_id=c.asset_id).all() if r.reason]
                bad.append({"clip": i, "asset_id": c.asset_id, "start": float(c.start),
                            "end": float(c.end), "reason": reasons[0] if reasons else None})
        return bad
