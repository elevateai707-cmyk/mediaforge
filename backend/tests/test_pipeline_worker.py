"""Worker must process real asset ids, not (count-1)."""
from __future__ import annotations

from sqlalchemy import text

from app.ai.pipeline import _rebuild_fts, next_pending_ids
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


def test_rebuild_fts_uses_bound_sql(client):
    """FTS refresh must use sqlalchemy.text(), not a raw string (SQLAlchemy 2)."""
    with SessionLocal() as db:
        asset = db.query(Asset).order_by(Asset.id.asc()).first()
        assert asset is not None
        _rebuild_fts(db, asset)
        db.commit()
        row = db.execute(
            text("SELECT asset_id FROM asset_fts WHERE asset_id = :a"),
            {"a": asset.id},
        ).fetchone()
        assert row is not None
        assert int(row[0]) == asset.id


def test_repeated_failures_quarantine_asset_so_queue_drains(client, monkeypatch):
    """One poison asset must not starve every asset queued behind it."""
    from app.ai import pipeline

    pipeline.clear_quarantine()
    with SessionLocal() as db:
        snapshot = {a.id: a.status for a in db.query(Asset).all()}
        db.query(Asset).update({Asset.status: "indexed"},
                               synchronize_session=False)
        for aid in (10, 11):
            row = db.get(Asset, aid)
            if row is None:
                row = Asset(id=aid, path=f"/tmp/mf_pipeline/q_{aid}.jpg",
                            hash=f"q-hash-{aid}", kind="photo")
                db.add(row)
            row.status = "pending"
            row.path = f"/tmp/mf_pipeline/q_{aid}.jpg"
            row.hash = f"q-hash-{aid}"
        db.commit()

    def boom(db, asset_id, progress=None):
        raise RuntimeError("poison asset")

    try:
        monkeypatch.setattr(pipeline, "process_asset", boom)
        assert next_pending_ids(1) == [10]
        for _ in range(pipeline.MAX_ASSET_FAILURES):
            pipeline._process_one_sync(10)
        assert 10 in pipeline.quarantined_ids()
        # The queue moves on to the next asset instead of retrying 10 forever.
        assert next_pending_ids(1) == [11]
        with SessionLocal() as db:
            assert "poison asset" in (db.get(Asset, 10).status_error or "")
    finally:
        pipeline.clear_quarantine()
        with SessionLocal() as db:
            for aid, status in snapshot.items():
                row = db.get(Asset, aid)
                if row is not None:
                    row.status = status
                    row.status_error = None
            db.commit()
