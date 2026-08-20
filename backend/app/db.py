"""SQLAlchemy engine + sqlite-vec integration.

Vector search:
  - If the sqlite-vec extension loads, a `vec0` virtual table is created and
    used for top-K ANN queries.
  - If it does not load (missing lib, restricted builds), every embedding is
    also stored as a float32 BLOB in `clip_embeddings` and search degrades to
    an exact cosine scan over all rows (documented; slower on large libraries
    but always correct).
"""
from __future__ import annotations

import logging
import struct
from typing import Optional

import numpy as np
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, Session

from . import config

log = logging.getLogger("mediaforge.db")

VEC_AVAILABLE: bool = False
VEC_LOAD_ERROR: str = ""


def _load_vec(conn) -> None:
    """Enable and load the sqlite-vec extension on a raw connection."""
    global VEC_AVAILABLE, VEC_LOAD_ERROR
    if VEC_AVAILABLE or VEC_LOAD_ERROR:
        return
    try:
        import sqlite_vec  # type: ignore
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        VEC_AVAILABLE = True
        log.info("sqlite-vec loaded: vec0 virtual tables enabled")
    except Exception as exc:  # pragma: no cover - env dependent
        VEC_AVAILABLE = False
        VEC_LOAD_ERROR = f"{type(exc).__name__}: {exc}"
        log.warning(
            "sqlite-vec unavailable (%s); vector search will fall back to "
            "exact cosine scan over stored float32 BLOBs.", VEC_LOAD_ERROR
        )


config.ensure_dirs()
_engine = create_engine(
    f"sqlite:///{config.DB_PATH}",
    connect_args={"check_same_thread": False, "timeout": 30},
    pool_pre_ping=True,
)


@event.listens_for(_engine, "connect")
def _on_connect(dbapi_conn, _record) -> None:
    dbapi_conn.execute("PRAGMA journal_mode=WAL")
    dbapi_conn.execute("PRAGMA foreign_keys=ON")
    dbapi_conn.execute("PRAGMA busy_timeout=30000")
    _load_vec(dbapi_conn)


SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)


def get_db():
    """FastAPI dependency: yields a session and always closes it."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables (ORM + raw virtual tables) idempotently."""
    from . import models  # noqa: F401  (register mappers)

    models.Base.metadata.create_all(bind=_engine)
    with _engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS clip_embeddings ("
            " asset_id INTEGER PRIMARY KEY,"
            " embedding BLOB NOT NULL,"
            " model TEXT NOT NULL DEFAULT 'clip-vit-b-32'"
            ")"
        ))
        conn.execute(text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS clip_embeddings_vec "
            "USING vec0(embedding float[512])"
        )) if VEC_AVAILABLE else None
        conn.execute(text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS asset_fts USING fts5("
            "asset_id UNINDEXED, transcript, caption)"
        ))
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS face_clusters ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " name TEXT,"
            " centroid BLOB,"
            " member_count INTEGER DEFAULT 0,"
            " created_at TEXT"
            ")"
        ))
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS face_detections ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " asset_id INTEGER NOT NULL,"
            " cluster_id INTEGER,"
            " embedding BLOB,"
            " bbox TEXT,"
            " frame_time REAL"
            ")"
        ))
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS dedupe_pairs ("
            " id TEXT PRIMARY KEY,"
            " asset_a INTEGER NOT NULL,"
            " asset_b INTEGER NOT NULL,"
            " distance REAL,"
            " kind TEXT NOT NULL,"
            " resolved INTEGER DEFAULT 0,"
            " created_at TEXT"
            ")"
        ))
        _migrate_place_columns(conn)


_ASSET_PLACE_COLUMNS = (
    ("gps_alt", "REAL"),
    ("place_name", "TEXT"),
    ("city", "TEXT"),
    ("region", "TEXT"),
    ("country", "TEXT"),
    ("country_code", "TEXT"),
    ("location_source", "TEXT"),
    ("location_confidence", "REAL"),
)


def _migrate_place_columns(conn) -> None:
    """ALTER TABLE ADD COLUMN for existing mediaforge.db libraries. Idempotent."""
    rows = conn.execute(text("PRAGMA table_info(assets)")).fetchall()
    existing = {r[1] for r in rows}
    for name, spec in _ASSET_PLACE_COLUMNS:
        if name in existing:
            continue
        conn.execute(text(f"ALTER TABLE assets ADD COLUMN {name} {spec}"))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_assets_city ON assets(city)"
    ))
    conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_trip_assets_asset_id ON trip_assets(asset_id)"
    ))


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------

def pack_embedding(vec: np.ndarray) -> bytes:
    """Serialize a float32 vector to a BLOB."""
    return np.asarray(vec, dtype=np.float32).tobytes()


def unpack_embedding(blob: bytes) -> np.ndarray:
    """Deserialize a float32 BLOB back to a vector."""
    return np.frombuffer(blob, dtype=np.float32)


def store_embedding(session: Session, asset_id: int, vec: np.ndarray) -> None:
    """Store an embedding: always as BLOB; mirrored into vec0 when available."""
    blob = pack_embedding(vec)
    session.execute(
        text("INSERT INTO clip_embeddings (asset_id, embedding) VALUES (:a, :b)")
        .bindparams(a=asset_id, b=blob)
    )
    if VEC_AVAILABLE:
        dims = list(np.asarray(vec, dtype=np.float32).tolist())
        session.execute(
            text("INSERT OR REPLACE INTO clip_embeddings_vec (rowid, embedding) VALUES (:a, :b)")
            .bindparams(a=asset_id, b=struct.pack(f"<{len(dims)}f", *dims))
        )
    session.commit()


def delete_embedding(session: Session, asset_id: int) -> None:
    session.execute(
        text("DELETE FROM clip_embeddings WHERE asset_id=:a").bindparams(a=asset_id)
    )
    if VEC_AVAILABLE:
        session.execute(
            text("DELETE FROM clip_embeddings_vec WHERE rowid=:a").bindparams(a=asset_id)
        )
    session.commit()


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def search_embeddings(session: Session, query_vec: np.ndarray, top_k: int = 50) -> list[tuple[int, float]]:
    """Top-K assets by cosine similarity against the query vector.

    Uses vec0 ANN when available; otherwise exact cosine over BLOBs.
    Returns [(asset_id, score)] sorted desc.
    """
    q = np.asarray(query_vec, dtype=np.float32)
    if VEC_AVAILABLE:
        try:
            qblob = struct.pack(f"<{len(q)}f", *q.tolist())
            rows = session.execute(
                text("SELECT rowid, distance FROM clip_embeddings_vec "
                     "WHERE embedding MATCH :q AND k = :k")
                .bindparams(q=qblob, k=top_k)
            ).fetchall()
            # vec0 distance = 1 - cosine for normalized vectors
            return [(int(r[0]), max(0.0, 1.0 - float(r[1]))) for r in rows]
        except Exception as exc:  # pragma: no cover - runtime safety
            log.warning("vec0 query failed (%s); falling back to cosine scan", exc)
    rows = session.execute(
        text("SELECT asset_id, embedding FROM clip_embeddings")
    ).fetchall()
    results: list[tuple[int, float]] = []
    for asset_id, blob in rows:
        try:
            vec = unpack_embedding(blob)
        except Exception:
            continue
        results.append((int(asset_id), _cosine(q, vec)))
    results.sort(key=lambda r: r[1], reverse=True)
    return results[:top_k]
