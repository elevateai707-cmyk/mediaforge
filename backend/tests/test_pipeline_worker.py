"""Worker must process real asset ids, not (count-1)."""
from __future__ import annotations

from app.ai.pipeline import next_pending_ids
from app.db import SessionLocal
from app.models import Asset


def test_worker_selects_real_asset_id_not_count_minus_one(client):
    with SessionLocal() as db:
        snapshot = {a.id: a.status for a in db.query(Asset).all()}
        for aid in (10, 11):
            existing = db.get(Asset, aid)
            if existing is not None:
                existing.status = "pending"
                existing.path = f"/tmp/mf_pipeline/gap_{aid}.jpg"
                existing.hash = f"gap-hash-{aid}"
            else:
                db.add(Asset(
                    id=aid,
                    path=f"/tmp/mf_pipeline/gap_{aid}.jpg",
                    hash=f"gap-hash-{aid}",
                    kind="photo",
                    status="pending",
                ))
        db.query(Asset).filter(~Asset.id.in_([10, 11])).update(
            {Asset.status: "indexed"}, synchronize_session=False
        )
        db.commit()
        pending = db.query(Asset).filter(Asset.status != "indexed").count()

    try:
        ids = next_pending_ids(1)
        assert ids == [10], f"expected asset 10, got {ids} (count-1 would be {pending - 1})"
        assert pending - 1 != 10
    finally:
        with SessionLocal() as db:
            for aid, status in snapshot.items():
                row = db.get(Asset, aid)
                if row is not None:
                    row.status = status
            db.commit()
