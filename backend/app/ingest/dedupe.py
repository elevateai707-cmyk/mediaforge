"""Duplicate detection: exact (SHA-256) and near (phash distance < 0.1).

Pairs are computed deterministically on request. `pair_id` is stable
("<asset_a>:<asset_b>" sorted by id), so resolve actions can reference it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import SessionLocal, delete_embedding
from .. import models

log = logging.getLogger("mediaforge.dedupe")

NEAR_THRESHOLD = 0.1


def _hash_dist(a: str | None, b: str | None) -> float | None:
    if not a or not b:
        return None
    try:
        ha, hb = int(a, 16), int(b, 16)
        return bin(ha ^ hb).count("1") / 64.0
    except ValueError:
        return None


def find_duplicates(db: Session) -> dict:
    """Return {'exact': [...], 'near': [...]} per the API contract."""
    exact, near = [], []

    rows = db.query(models.Asset).order_by(models.Asset.id).all()
    by_hash: dict[str, list[models.Asset]] = {}
    for r in rows:
        by_hash.setdefault(r.hash, []).append(r)
    for _h, group in by_hash.items():
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                exact.append({
                    "id": f"{a.id}:{b.id}", "asset_a": a.id, "asset_b": b.id,
                    "path_a": a.path, "path_b": b.path, "distance": 0.0,
                    "kind": "exact",
                })

    photos = [r for r in rows if r.kind == "photo" and r.phash]
    for i in range(len(photos)):
        for j in range(i + 1, len(photos)):
            a, b = photos[i], photos[j]
            d = _hash_dist(a.phash, b.phash)
            if d is not None and d < NEAR_THRESHOLD:
                near.append({
                    "id": f"{a.id}:{b.id}", "asset_a": a.id, "asset_b": b.id,
                    "path_a": a.path, "path_b": b.path,
                    "distance": round(d, 4), "kind": "near",
                })
    return {"exact": exact, "near": near}


def resolve_pair(pair_id: str, action: str) -> bool:
    """Resolve a dedupe pair. Returns False if the pair is unknown.

    - keep_a: no-op (a stays)
    - keep_b: no-op (b stays)
    - delete_b: delete asset b and its artifacts
    """
    parts = pair_id.split(":")
    if len(parts) != 2:
        return False
    try:
        id_a, id_b = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    with SessionLocal() as db:
        a = db.get(models.Asset, id_a)
        b = db.get(models.Asset, id_b)
        if a is None or b is None:
            return False
        if action == "delete_b":
            _delete_asset(db, b)
        # keep_a / keep_b keep both (the user's choice is recorded as resolved)
        db.execute(text(
            "INSERT OR REPLACE INTO dedupe_pairs "
            "(id, asset_a, asset_b, distance, kind, resolved, created_at) "
            "VALUES (:id, :a, :b, 0.0, 'exact', 1, :ts)"
        ).bindparams(id=pair_id, a=id_a, b=id_b,
                     ts=datetime.now(timezone.utc).isoformat()))
        db.commit()
    return True


def _delete_asset(db: Session, asset: models.Asset) -> None:
    for p in (asset.thumb_path, asset.poster_path, asset.proxy_path):
        if p:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass
    delete_embedding(db, asset.id)
    db.delete(asset)
    db.commit()
    log.info("dedupe delete_b removed asset %s (%s)", asset.id, asset.path)
