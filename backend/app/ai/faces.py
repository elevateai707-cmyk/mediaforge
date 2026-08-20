"""Face detection + incremental agglomerative clustering (insightface).

Detections are stored as ``FaceDetection`` rows (ORM) linked to clusters
stored in the raw ``face_clusters`` table (name + packed centroid blob).
Clustering is incremental: each new embedding is matched against existing
cluster centroids within ``FACE_DISTANCE_THRESHOLD`` (0.45 cosine distance);
otherwise a new cluster is spawned. Renaming a cluster re-tags every asset
that owns a detection in it, because asset "faces" lists are derived from
cluster names at serialization time.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import numpy as np

from app import config, db
from app.ai import gpu
from app.models import Asset, FaceDetection

log = logging.getLogger(__name__)

FACE_DISTANCE_THRESHOLD = 0.45

_model = None
_init_error: str | None = None


# --------------------------------------------------------------------------
# model lifecycle
# --------------------------------------------------------------------------
def _ensure_model() -> Any | None:
    """Lazily initialise the FaceAnalysis buffalo_sc model (GPU/CPU)."""
    global _model, _init_error
    if _model is not None:
        return _model
    if _init_error is not None:
        return None
    try:
        import insightface
        from insightface.app import FaceAnalysis

        providers = gpu.available("insightface") and gpu.provider() or ["CPUExecutionProvider"]
        app = FaceAnalysis(
            name="buffalo_sc",
            root=str(config.MODELS_DIR),
            providers=providers,
            allowed_modules=["detection", "recognition"],
        )
        app.prepare(ctx_id=0 if gpu.device() == "cuda" else -1, det_size=(640, 640))
        _model = app
        gpu.set_model_status("insightface", "loaded", {"model": "buffalo_sc"})
        return _model
    except Exception as exc:  # pragma: no cover - depends on environment
        _init_error = str(exc)
        log.warning("insightface unavailable: %s", exc)
        gpu.set_model_status("insightface", "unavailable", {"error": str(exc)})
        return None


def unload() -> None:  # pragma: no cover - VRAM hygiene helper
    """Free the model (and any cached tensors) to release VRAM."""
    global _model
    _model = None
    gpu.clear_gpu_cache()


# --------------------------------------------------------------------------
# detection
# --------------------------------------------------------------------------
def detect_faces(image_path: str) -> list[dict[str, Any]]:
    """Detect faces in an image.

    Returns ``[{"bbox": [x1, y1, x2, y2], "embedding": [512 floats]}, ...]``.
    Empty list on any failure (model unavailable, bad image, ...).
    """
    if not image_path or not os.path.isfile(image_path):
        return []
    app = _ensure_model()
    if app is None:
        return []
    try:
        faces = app.get(np.asarray(db.load_image(image_path)))
        out: list[dict[str, Any]] = []
        for f in faces:
            if f is None or not hasattr(f, "normed_embedding"):
                continue
            emb = np.asarray(f.normed_embedding, dtype=np.float32)
            if emb.size == 0:
                continue
            bbox = [float(v) for v in f.bbox.tolist()]
            out.append({"bbox": bbox, "embedding": emb.tolist()})
        return out
    except Exception as exc:  # pragma: no cover - depends on environment
        log.warning("face detection failed for %s: %s", image_path, exc)
        return []


# --------------------------------------------------------------------------
# clustering
# --------------------------------------------------------------------------
def _all_clusters(db_session) -> list[dict[str, Any]]:
    """[(id, name, member_count, centroid_array), ...] from face_clusters."""
    rows = db_session.execute(
        "SELECT id, name, member_count, centroid FROM face_clusters"
    ).fetchall()
    clusters = []
    for row in rows:
        centroid = db.unpack_embedding(row[3]) if row[3] else None
        clusters.append(
            {"id": row[0], "name": row[1], "member_count": row[2], "centroid": centroid}
        )
    return clusters


def cluster_face(
    embedding: list[float] | np.ndarray,
    clusters: list[dict[str, Any]],
    threshold: float = FACE_DISTANCE_THRESHOLD,
) -> int | None:
    """Match ``embedding`` against cluster centroids (cosine distance).

    Returns the cluster id whose centroid is closest and within
    ``threshold``; None when nothing is close enough (caller creates a new
    cluster).
    """
    emb = np.asarray(embedding, dtype=np.float32).reshape(-1)
    if emb.size == 0:
        return None
    best_id: int | None = None
    best_dist = float("inf")
    for cl in clusters:
        centroid = cl.get("centroid")
        if centroid is None or len(centroid) != len(emb):
            continue
        dist = 1.0 - float(np.dot(emb, centroid) / (
            np.linalg.norm(emb) * np.linalg.norm(centroid) + 1e-9
        ))
        if dist < best_dist:
            best_dist = dist
            best_id = cl["id"]
    if best_id is not None and best_dist <= threshold:
        return best_id
    return None


def _upsert_cluster(db_session, embedding: list[float]) -> int:
    """Return existing matching cluster id or create a new one."""
    emb = np.asarray(embedding, dtype=np.float32)
    clusters = _all_clusters(db_session)
    match = cluster_face(emb, clusters)
    if match is not None:
        for cl in clusters:
            if cl["id"] == match:
                n = int(cl["member_count"])
                centroid = (n * cl["centroid"] + emb) / (n + 1)
                db_session.execute(
                    "UPDATE face_clusters SET centroid = :c, member_count = :n WHERE id = :i",
                    {
                        "c": db.pack_embedding(centroid),
                        "n": n + 1,
                        "i": match,
                    },
                )
                return match
    res = db_session.execute(
        "INSERT INTO face_clusters (name, centroid, member_count) VALUES (:n, :c, 1)",
        {"n": f"Person {_next_person_index(db_session)}", "c": db.pack_embedding(emb)},
    )
    return int(res.lastrowid)


def _next_person_index(db_session) -> int:
    row = db_session.execute(
        "SELECT COUNT(*) FROM face_clusters WHERE name LIKE 'Person %'"
    ).fetchone()
    return int(row[0]) + 1


# --------------------------------------------------------------------------
# asset-level processing
# --------------------------------------------------------------------------
def _frames_for_asset(asset: Asset) -> list[tuple[str, float]]:
    """[(image_path, frame_time)] to analyse for a given asset."""
    from app.ingest import thumbs

    if asset.kind != "video" or not asset.duration or asset.duration <= 0:
        return [(asset.path, 0.0)]
    frames: list[tuple[str, float]] = []
    for frac in (0.25, 0.5, 0.75):
        t = asset.duration * frac
        out = thumbs.extract_frame(asset.path, t, asset.id, f"face_{int(frac * 100)}")
        if out:
            frames.append((out, t))
    return frames


def process_faces_for_asset(db_session, asset: Asset) -> int:
    """Detect + cluster faces for an asset, storing FaceDetection rows.

    Returns the number of detections stored (0 when the model is
    unavailable). Existing detections for the asset are replaced.
    """
    if _ensure_model() is None:
        return 0
    db_session.query(FaceDetection).filter_by(asset_id=asset.id).delete()
    db_session.flush()
    stored = 0
    for frame_path, frame_time in _frames_for_asset(asset):
        for det in detect_faces(frame_path):
            cluster_id = _upsert_cluster(db_session, det["embedding"])
            db_session.add(
                FaceDetection(
                    asset_id=asset.id,
                    cluster_id=cluster_id,
                    frame_time=frame_time,
                    bbox=db.json_dumps(det["bbox"]),
                    embedding=db.json_dumps(det["embedding"]),
                )
            )
            stored += 1
        db_session.flush()
    return stored


# --------------------------------------------------------------------------
# cluster management (used by the API layer)
# --------------------------------------------------------------------------
def rename_cluster(db_session, cluster_id: int, name: str) -> bool:
    name = (name or "").strip()
    if not name:
        return False
    res = db_session.execute(
        "UPDATE face_clusters SET name = :n WHERE id = :i", {"n": name, "i": cluster_id}
    )
    return res.rowcount > 0


def merge_clusters(db_session, from_id: int, into_id: int) -> bool:
    """Move all detections of ``from_id`` into ``into_id`` and merge centroids."""
    if from_id == into_id:
        return False
    src = db_session.execute(
        "SELECT member_count, centroid FROM face_clusters WHERE id = :i", {"i": from_id}
    ).fetchone()
    dst = db_session.execute(
        "SELECT member_count, centroid FROM face_clusters WHERE id = :i", {"i": into_id}
    ).fetchone()
    if not src or not dst:
        return False
    src_c = db.unpack_embedding(src[1]) if src[1] else None
    dst_c = db.unpack_embedding(dst[1]) if dst[1] else None
    n = int(src[0]) + int(dst[0])
    if src_c is not None and dst_c is not None and len(src_c) == len(dst_c):
        centroid = (int(src[0]) * src_c + int(dst[0]) * dst_c) / n
    else:
        centroid = dst_c if dst_c is not None else src_c
    db_session.execute(
        "UPDATE face_clusters SET centroid = :c, member_count = :n WHERE id = :i",
        {"c": db.pack_embedding(centroid) if centroid is not None else None, "n": n, "i": into_id},
    )
    db_session.execute(
        "UPDATE face_detections SET cluster_id = :to WHERE cluster_id = :fr",
        {"to": into_id, "fr": from_id},
    )
    db_session.execute("DELETE FROM face_clusters WHERE id = :i", {"i": from_id})
    return True


def cluster_summary(db_session) -> list[dict[str, Any]]:
    """All clusters with name, member count and a representative asset id."""
    rows = db_session.execute(
        """
        SELECT c.id, c.name, c.member_count,
               (SELECT d.asset_id FROM face_detections d
                WHERE d.cluster_id = c.id ORDER BY d.id LIMIT 1) AS asset_id
        FROM face_clusters c ORDER BY c.member_count DESC, c.id ASC
        """
    ).fetchall()
    return [
        {"id": r[0], "name": r[1], "member_count": r[2], "asset_id": r[3]} for r in rows
    ]


def faces_for_asset(db_session, asset_id: int) -> list[dict[str, Any]]:
    """Named face clusters present in an asset (deduplicated, by count)."""
    rows = db_session.execute(
        """
        SELECT c.id, c.name, COUNT(*) AS n
        FROM face_detections d JOIN face_clusters c ON c.id = d.cluster_id
        WHERE d.asset_id = :a AND c.name IS NOT NULL AND c.name != ''
        GROUP BY c.id, c.name ORDER BY n DESC
        """,
        {"a": asset_id},
    ).fetchall()
    return [{"cluster_id": r[0], "name": r[1], "count": r[2]} for r in rows]


def face_names(db_session, asset_id: int) -> list[str]:
    return [f["name"] for f in faces_for_asset(db_session, asset_id)]
