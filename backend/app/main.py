"""MediaForge FastAPI backend — full API surface per docs/API_CONTRACT.md.

Wires every contract endpoint to the existing backend modules (ingest,
AI pipeline, edit planner, render, exports, touchup, jobs, websocket hub).
Long-running operations run as asyncio background jobs via jobs.py and are
broadcast over /ws by ws.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import mimetypes
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

from . import config, fsutil, models, persist, schemas, touchup
from .ai import clip
from .ai import gpu
from .db import SessionLocal, get_db, init_db, search_embeddings
from .edits import capcut_export, edl_export, fcpxml_export, planner, render, resolve_export
from .edits.intent import parse_intent
from .ingest import dedupe, music, scanner
from .ingest import watcher as folder_watcher
from .ingest.trips import cluster_trips, trip_asset_ids, trip_for_asset
from .jobs import jobs
from .ws import manager as ws_manager

log = logging.getLogger("mediaforge.api")

_STOPWORDS = {
    "with", "from", "this", "that", "what", "when", "where", "which",
    "there", "here", "them", "they", "then", "than", "were", "will",
    "have", "been", "being", "into", "over", "under", "very", "just",
    "about", "after", "before", "between", "their", "there", "these",
    "those", "would", "could", "should", "your", "youre", "ours",
    "image", "photo", "picture", "video", "scene", "clip", "shot",
}

# job_id -> final output path captured from coroutine return values
# (jobs.py persists progress/message but not the return value).
_RENDER_OUTPUTS: dict[str, str] = {}


# ---------------------------------------------------------------------------
# startup / lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    try:
        with SessionLocal() as db:
            n_assets = db.query(models.Asset).count()
            n_trips = db.query(models.Trip).count()
        if n_assets and n_trips == 0:
            cluster_trips()
    except Exception as exc:
        log.warning("startup trip cluster skipped: %s", exc)
    try:
        folder_watcher.start()
    except Exception as exc:
        log.warning("folder watcher not started: %s", exc)
    try:
        from .ai.pipeline import start_ai_worker

        start_ai_worker(app)
    except ImportError as exc:  # pragma: no cover - heavy deps missing
        log.warning("AI worker not started (ImportError): %s", exc)
    try:
        ws_manager.set_gpu(gpu.gpu_payload())
    except Exception as exc:  # pragma: no cover
        log.warning("gpu payload failed: %s", exc)
    yield
    folder_watcher.stop()


app = FastAPI(title="MediaForge", version=config.VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _derive_tags(asset: models.Asset) -> list[str]:
    words = re.findall(r"[a-z0-9]{4,}", (asset.caption or "").lower())
    seen, out = set(), []
    for w in words:
        if w in _STOPWORDS or w in seen:
            continue
        seen.add(w)
        out.append(w)
        if len(out) >= 8:
            break
    return out


def _asset_out(db: Session, asset: models.Asset) -> dict:
    faces: list[str] = []
    try:
        faces = gpu_faces_names(db, asset.id)
    except Exception:  # pragma: no cover
        pass
    return {
        "id": asset.id,
        "path": asset.path,
        "kind": asset.kind,
        "mime": asset.mime,
        "size": asset.size or 0,
        "width": asset.width,
        "height": asset.height,
        "duration": asset.duration,
        "taken_at": _iso(asset.taken_at),
        "added_at": _iso(asset.added_at),
        "camera_make": asset.camera_make,
        "camera_model": asset.camera_model,
        "gps_lat": asset.gps_lat,
        "gps_lon": asset.gps_lon,
        "gps_alt": asset.gps_alt,
        "city": asset.city,
        "region": asset.region,
        "country": asset.country,
        "place_name": asset.place_name,
        "location_source": asset.location_source,
        "location_confidence": asset.location_confidence,
        "trip_id": trip_for_asset(db, asset.id),
        "aesthetic_score": asset.aesthetic_score,
        "caption": asset.caption,
        "status": asset.status,
        "hash": asset.hash or "",
        "tags": _derive_tags(asset),
        "faces": faces,
        "scene_count": asset.scene_count or 0,
        "has_transcript": bool(asset.has_transcript),
    }


def gpu_faces_names(db: Session, asset_id: int) -> list[str]:
    """Named face clusters present in an asset."""
    from .ai.faces import face_names

    return face_names(db, asset_id)


def _background(fn, *args, **kwargs):
    """Run a blocking callable in the default executor as a coroutine.

    The returned function accepts (progress, flag) — jobs.run always calls
    coro_factory(progress, flag) — and ignores both.
    """

    async def _run(progress=None, flag=None):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))

    return _run


def _asset_file_response(asset: models.Asset, range_header: Optional[str],
                         media_type: Optional[str] = None) -> Response:
    path = asset.path
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="file missing on disk")
    media_type = media_type or asset.mime or mimetypes.guess_type(path)[0] or "application/octet-stream"
    size = os.path.getsize(path)
    if not range_header:
        return FileResponse(path, media_type=media_type)
    m = re.match(r"bytes=(\d*)-(\d*)$", range_header.strip())
    if not m:
        raise HTTPException(status_code=416, detail="invalid range header")
    start_s, end_s = m.group(1), m.group(2)
    if start_s == "" and end_s == "":
        raise HTTPException(status_code=416, detail="invalid range header")
    start = int(start_s) if start_s else None
    end = int(end_s) if end_s else None
    if start is None:  # suffix range: bytes=-N
        length = min(end or 0, size)
        start = max(0, size - length)
        end = size - 1
    else:
        end = min(end if end is not None else size - 1, size - 1)
        if start >= size or start > end:
            raise HTTPException(status_code=416, detail="range not satisfiable")
    chunk_len = end - start + 1
    with open(path, "rb") as f:
        f.seek(start)
        data = f.read(chunk_len)
    return Response(
        content=data,
        status_code=206,
        media_type=media_type,
        headers={
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(chunk_len),
        },
    )


def _hybrid_search(db: Session, q: str, limit: int = 50) -> list[int]:
    """Fused ranking: CLIP vector similarity + FTS5 + filename/caption LIKE."""
    scores: dict[int, float] = {}
    try:
        vec = clip.embed_text(q)
        if vec is not None:
            for aid, sim in search_embeddings(
                db, np.asarray(vec, dtype=np.float32), top_k=limit
            ):
                scores[int(aid)] = scores.get(int(aid), 0.0) + 0.35 * float(sim)
    except Exception as exc:
        log.debug("vector search skipped: %s", exc)

    terms = [t for t in re.findall(r"[A-Za-z0-9]+", q) if len(t) > 1]
    if terms:
        match_q = " OR ".join(f'"{t}"' for t in terms[:8])
        try:
            rows = db.execute(
                text(
                    "SELECT asset_id, bm25(asset_fts) AS r FROM asset_fts "
                    "WHERE asset_fts MATCH :q"
                ).bindparams(q=match_q)
            ).fetchall()
            ranked = [(int(r[0]), float(r[1])) for r in rows if r[1] is not None]
            if ranked:
                best = min(r for _, r in ranked)
                worst = max(r for _, r in ranked)
                for aid, r in ranked:
                    norm = (worst - r) / (worst - best) if worst != best else 1.0
                    scores[aid] = scores.get(aid, 0.0) + 0.25 * norm
        except Exception as exc:
            log.debug("fts search skipped: %s", exc)

    ql = q.lower().strip()
    if ql:
        try:
            for needle, weight in (("%" + ql + "%", 0.2),):
                rows = db.execute(
                    text("SELECT id FROM assets WHERE path LIKE :p OR caption LIKE :p")
                    .bindparams(p=needle)
                ).fetchall()
                for (aid,) in rows:
                    scores[int(aid)] = scores.get(int(aid), 0.0) + weight
        except Exception as exc:
            log.debug("like search skipped: %s", exc)

    parsed = parse_intent(q)
    if parsed.place:
        place_ids: set[int] = set()
        city = parsed.place.lower()
        radius = float(parsed.radius_km or 45.0)
        for asset in db.query(models.Asset).all():
            hit = False
            if (asset.city or "").lower() == city:
                hit = True
            elif city in (asset.path or "").lower():
                hit = True
            elif (
                parsed.place_lat is not None and parsed.place_lon is not None
                and asset.gps_lat is not None and asset.gps_lon is not None
            ):
                dlat = abs(float(asset.gps_lat) - float(parsed.place_lat))
                dlon = abs(float(asset.gps_lon) - float(parsed.place_lon))
                if (dlat ** 2 + dlon ** 2) ** 0.5 * 111.0 <= radius:
                    hit = True
            if hit:
                place_ids.add(asset.id)
                scores[asset.id] = scores.get(asset.id, 0.0) + 0.20
        if place_ids:
            scores = {aid: sc for aid, sc in scores.items() if aid in place_ids}

    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [aid for aid, _ in ordered[:limit]]


# ---------------------------------------------------------------------------
# system
# ---------------------------------------------------------------------------
@app.get("/api/health", response_model=schemas.HealthOut)
def health():
    try:
        mode = gpu.device()
        name = gpu.gpu_name()
        vram = gpu.vram_total_mb()
        warnings = gpu.warnings_list()
        models_status = dict(gpu.MODEL_STATUS)
    except Exception as exc:  # pragma: no cover
        log.warning("health gpu probe failed: %s", exc)
        mode, name, vram, warnings, models_status = "cpu", "", 0, [], {}
    return schemas.HealthOut(
        status="ok", gpu=mode, gpu_name=name, vram_mb=vram,
        version=config.VERSION, models=models_status, warnings=warnings,
    )


@app.get("/api/config", response_model=schemas.ConfigOut)
def get_config():
    try:
        resolve_ok = bool(resolve_export.resolve_available())
    except Exception:  # pragma: no cover
        resolve_ok = False
    return schemas.ConfigOut(
        use_cloud_llm=persist.use_cloud_llm(),
        media_dirs=persist.media_dirs(),
        ollama_model=config.OLLAMA_MODEL,
        resolve_available=resolve_ok,
        watcher_enabled=persist.watcher_enabled(),
        music_dir=persist.music_dir(),
    )


@app.put("/api/config", response_model=schemas.ConfigOut)
def put_config(req: schemas.ConfigUpdate):
    if req.media_dirs is not None:
        persist.set_media_dirs(req.media_dirs)
        try:
            folder_watcher.start()
        except Exception as exc:
            log.warning("watcher restart failed: %s", exc)
    if req.watcher_enabled is not None:
        persist.set_watcher_enabled(req.watcher_enabled)
        try:
            if req.watcher_enabled:
                folder_watcher.start()
            else:
                folder_watcher.stop()
        except Exception as exc:
            log.warning("watcher toggle failed: %s", exc)
    if req.music_dir is not None:
        persist.set_music_dir(req.music_dir)
    if req.use_cloud_llm is not None:
        persist.set_use_cloud_llm(req.use_cloud_llm)
    return get_config()


# ---------------------------------------------------------------------------
# scan / ingest
# ---------------------------------------------------------------------------
@app.post("/api/scan", response_model=schemas.JobRefOut)
async def scan(req: schemas.ScanRequest):
    paths = [p for p in (req.paths or []) if p.strip()]
    if not paths:
        paths = persist.persisted_media_dirs()
    if not paths:
        raise HTTPException(status_code=422, detail="paths must not be empty")
    job_id = jobs.run(
        "scan", _background(scanner.scan_paths, paths, rescan=False)
    )
    return schemas.JobRefOut(job_id=job_id, kind="scan")


@app.post("/api/rescan", response_model=schemas.JobRefOut)
async def rescan(req: schemas.ScanRequest):
    paths = [p for p in (req.paths or []) if p.strip()]
    if not paths:
        paths = persist.persisted_media_dirs()
    if not paths:
        raise HTTPException(status_code=422, detail="paths must not be empty")
    job_id = jobs.run(
        "scan", _background(scanner.scan_paths, paths, rescan=True)
    )
    return schemas.JobRefOut(job_id=job_id, kind="scan")


@app.post("/api/scan/cancel", response_model=schemas.OkOut)
def cancel_scan(req: schemas.CancelRequest):
    if not jobs.cancel(req.job_id):
        raise HTTPException(status_code=404, detail=f"job {req.job_id!r} not found")
    return schemas.OkOut(ok=True)


@app.post("/api/fs/stat", response_model=schemas.FsStatOut)
def fs_stat(req: schemas.FsStatRequest):
    try:
        return fsutil.stat_path(req.path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/api/music", response_model=list[schemas.MusicTrackOut])
def list_music():
    return music.list_tracks(persist.music_dir())


@app.get("/api/dedupe", response_model=schemas.DedupeOut)
def get_dedupe(db: Session = Depends(get_db)):
    return dedupe.find_duplicates(db)


@app.post("/api/dedupe/resolve", response_model=schemas.OkOut)
def resolve_dupe(req: schemas.DedupeResolveRequest):
    if req.action not in ("keep_a", "keep_b", "delete_b"):
        raise HTTPException(
            status_code=422,
            detail="action must be one of keep_a, keep_b, delete_b",
        )
    if not dedupe.resolve_pair(req.pair_id, req.action):
        raise HTTPException(status_code=404, detail=f"pair {req.pair_id!r} not found")
    return schemas.OkOut(ok=True)


# ---------------------------------------------------------------------------
# assets
# ---------------------------------------------------------------------------
@app.get("/api/assets", response_model=schemas.AssetListOut)
def list_assets(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    kind: Optional[str] = Query(None),
    sort: str = Query("added"),
    order: str = Query("desc"),
    tag: Optional[str] = Query(None),
    face: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    lat: Optional[float] = Query(None),
    lon: Optional[float] = Query(None),
    radius_km: Optional[float] = Query(None),
    city: Optional[str] = Query(None),
    trip_id: Optional[int] = Query(None),
    min_aesthetic: Optional[float] = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(models.Asset)

    if kind in ("photo", "video"):
        query = query.filter(models.Asset.kind == kind)

    if tag:
        like = f"%{tag}%"
        query = query.filter(
            or_(
                models.Asset.caption.like(like),
                models.Asset.id.in_(
                    db.query(models.Scene.asset_id).filter(models.Scene.caption.like(like))
                ),
            )
        )

    if face and face.lower() != "all":
        if face.isdigit():
            rows = db.execute(
                text(
                    "SELECT DISTINCT fd.asset_id FROM face_detections fd "
                    "JOIN face_clusters fc ON fc.id = fd.cluster_id "
                    "WHERE fc.id = :cid OR lower(fc.name) = lower(:n)"
                ).bindparams(cid=int(face), n=face)
            ).fetchall()
        else:
            rows = db.execute(
                text(
                    "SELECT DISTINCT fd.asset_id FROM face_detections fd "
                    "JOIN face_clusters fc ON fc.id = fd.cluster_id "
                    "WHERE lower(fc.name) = lower(:n)"
                ).bindparams(n=face)
            ).fetchall()
        ids = [r[0] for r in rows]
        if not ids:
            return schemas.AssetListOut(total=0, items=[])
        query = query.filter(models.Asset.id.in_(ids))

    if date_from:
        try:
            query = query.filter(models.Asset.taken_at >= datetime.fromisoformat(date_from))
        except ValueError:
            raise HTTPException(status_code=422, detail=f"invalid date_from: {date_from!r}")
    if date_to:
        try:
            query = query.filter(models.Asset.taken_at <= datetime.fromisoformat(date_to))
        except ValueError:
            raise HTTPException(status_code=422, detail=f"invalid date_to: {date_to!r}")

    if lat is not None and lon is not None and radius_km:
        dlat = radius_km / 111.0
        dlon = radius_km / (111.0 * max(0.1, math.cos(math.radians(lat))))
        query = query.filter(
            models.Asset.gps_lat.between(lat - dlat, lat + dlat),
            models.Asset.gps_lon.between(lon - dlon, lon + dlon),
        )

    if city:
        query = query.filter(func.lower(models.Asset.city) == city.strip().lower())

    if trip_id is not None:
        ids = trip_asset_ids(db, trip_id)
        if not ids:
            return schemas.AssetListOut(total=0, items=[])
        query = query.filter(models.Asset.id.in_(ids))

    if min_aesthetic is not None:
        query = query.filter(models.Asset.aesthetic_score >= min_aesthetic)

    if q:
        ids = _hybrid_search(db, q, limit=250)
        if not ids:
            return schemas.AssetListOut(total=0, items=[])
        query = query.filter(models.Asset.id.in_(ids))
        total = query.count()
        rows = query.all()
        rank = {aid: i for i, aid in enumerate(ids)}
        rows.sort(key=lambda a: rank.get(a.id, len(ids)))
        items = rows[offset : offset + limit]
    else:
        col = {
            "taken_at": models.Asset.taken_at,
            "aesthetic": models.Asset.aesthetic_score,
            "added": models.Asset.added_at,
        }.get(sort, models.Asset.added_at)
        ordering = col.desc().nullslast() if order == "desc" else col.asc().nullslast()
        total = query.count()
        items = query.order_by(ordering).offset(offset).limit(limit).all()

    return schemas.AssetListOut(
        total=total, items=[_asset_out(db, a) for a in items]
    )


def _get_asset(db: Session, asset_id: int) -> models.Asset:
    asset = db.get(models.Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail=f"asset {asset_id} not found")
    return asset


@app.get("/api/assets/{asset_id}", response_model=schemas.AssetDetailOut)
def asset_detail(asset_id: int, db: Session = Depends(get_db)):
    asset = _get_asset(db, asset_id)
    scenes = sorted(asset.scenes, key=lambda s: s.index or 0)
    faces_detail = []
    for d in asset.faces:
        bbox = None
        if d.bbox:
            try:
                bbox = json.loads(d.bbox)
            except (ValueError, TypeError):
                bbox = None
        faces_detail.append({"id": d.id, "cluster_id": d.cluster_id, "bbox": bbox})
    return schemas.AssetDetailOut(
        **_asset_out(db, asset),
        scenes=[
            {
                "id": s.id, "start": s.start, "end": s.end,
                "caption": s.caption, "aesthetic": s.aesthetic_score,
            }
            for s in scenes
        ],
        faces_detail=faces_detail,
    )


@app.get("/api/assets/{asset_id}/file")
def asset_file(asset_id: int, request: Request, db: Session = Depends(get_db)):
    asset = _get_asset(db, asset_id)
    return _asset_file_response(asset, request.headers.get("range"))


def _derived_media_path(asset: models.Asset, suffix: str) -> Optional[str]:
    if asset.kind == "photo":
        p = config.THUMBS_DIR / str(asset.id) / f"{suffix}.webp"
    else:
        p = config.PROXIES_DIR / str(asset.id) / f"{suffix}.mp4" if suffix == "proxy" \
            else config.THUMBS_DIR / str(asset.id) / f"{suffix}.webp"
    return str(p) if os.path.isfile(p) else None


@app.get("/api/assets/{asset_id}/thumb")
def asset_thumb(asset_id: int, db: Session = Depends(get_db)):
    asset = _get_asset(db, asset_id)
    path = asset.thumb_path or _derived_media_path(asset, "thumb")
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="thumbnail not generated yet")
    return FileResponse(path, media_type="image/webp")


@app.get("/api/assets/{asset_id}/poster")
def asset_poster(asset_id: int, db: Session = Depends(get_db)):
    asset = _get_asset(db, asset_id)
    path = asset.poster_path or _derived_media_path(asset, "poster")
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="poster not generated yet")
    return FileResponse(path, media_type="image/webp")


@app.get("/api/assets/{asset_id}/proxy")
def asset_proxy(asset_id: int, db: Session = Depends(get_db)):
    asset = _get_asset(db, asset_id)
    path = asset.proxy_path or _derived_media_path(asset, "proxy")
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="proxy not generated yet")
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/assets/{asset_id}/transcript", response_model=schemas.TranscriptOut)
def asset_transcript(asset_id: int, db: Session = Depends(get_db)):
    asset = _get_asset(db, asset_id)
    return schemas.TranscriptOut(
        segments=[
            {"start": s.start, "end": s.end, "text": s.text}
            for s in sorted(asset.transcript, key=lambda s: s.start)
        ]
    )


@app.get("/api/assets/{asset_id}/scenes", response_model=schemas.ScenesOut)
def asset_scenes(asset_id: int, db: Session = Depends(get_db)):
    asset = _get_asset(db, asset_id)
    return schemas.ScenesOut(
        scenes=[
            {
                "id": s.id, "start": s.start, "end": s.end,
                "caption": s.caption, "aesthetic": s.aesthetic_score,
            }
            for s in sorted(asset.scenes, key=lambda s: s.index or 0)
        ]
    )


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------
@app.get("/api/search", response_model=schemas.SearchOut)
def search(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    t0 = time.perf_counter()
    ids = _hybrid_search(db, q, limit=limit)
    items: list[models.Asset] = []
    if ids:
        rank = {aid: i for i, aid in enumerate(ids)}
        rows = db.query(models.Asset).filter(models.Asset.id.in_(ids)).all()
        rows.sort(key=lambda a: rank.get(a.id, len(ids)))
        items = rows[:limit]
    took_ms = int((time.perf_counter() - t0) * 1000)
    return schemas.SearchOut(
        query=q,
        results=[_asset_out(db, a) for a in items],
        took_ms=took_ms,
    )


# ---------------------------------------------------------------------------
# faces
# ---------------------------------------------------------------------------
@app.get("/api/faces", response_model=schemas.FacesOut)
def list_faces(db: Session = Depends(get_db)):
    from .ai.faces import cluster_summary

    clusters = [
        {
            "id": c["id"],
            "name": c["name"],
            "count": c["member_count"] or 0,
            "thumb_asset_id": c["asset_id"],
        }
        for c in cluster_summary(db)
    ]
    return schemas.FacesOut(clusters=clusters)


@app.post("/api/faces/{cluster_id}/name", response_model=schemas.OkOut)
def name_face(cluster_id: int, req: schemas.FaceNameRequest, db: Session = Depends(get_db)):
    from .ai.faces import rename_cluster

    if not rename_cluster(db, cluster_id, req.name.strip()):
        raise HTTPException(status_code=404, detail=f"cluster {cluster_id} not found")
    return schemas.OkOut(ok=True)


@app.get("/api/faces/{cluster_id}/assets", response_model=schemas.AssetListOut)
def face_assets(cluster_id: int, limit: int = Query(50, ge=1, le=200),
                db: Session = Depends(get_db)):
    rows = db.execute(
        text(
            "SELECT DISTINCT asset_id FROM face_detections "
            "WHERE cluster_id = :c ORDER BY asset_id LIMIT :l"
        ).bindparams(c=cluster_id, l=limit)
    ).fetchall()
    assets = [a for a in (db.get(models.Asset, r[0]) for r in rows) if a is not None]
    return schemas.AssetListOut(total=len(assets), items=[_asset_out(db, a) for a in assets])


@app.post("/api/faces/merge", response_model=schemas.OkOut)
def merge_faces(req: schemas.FaceMergeRequest, db: Session = Depends(get_db)):
    from .ai.faces import merge_clusters

    if not merge_clusters(db, req.from_cluster, req.into_cluster):
        raise HTTPException(
            status_code=400,
            detail=f"merge failed: clusters {req.from_cluster} -> {req.into_cluster}",
        )
    return schemas.OkOut(ok=True)


def _trip_out(trip: models.Trip) -> dict:
    return {
        "id": trip.id,
        "title": trip.title,
        "city": trip.city,
        "region": trip.region,
        "start_at": _iso(trip.start_at),
        "end_at": _iso(trip.end_at),
        "asset_count": trip.asset_count or 0,
        "cover_asset_id": trip.cover_asset_id,
        "lat": trip.lat,
        "lon": trip.lon,
        "radius_km": trip.radius_km,
    }


@app.get("/api/places", response_model=list[schemas.PlaceOut])
def list_places(db: Session = Depends(get_db)):
    rows = (
        db.query(
            models.Asset.city,
            models.Asset.region,
            func.count(models.Asset.id),
            func.avg(models.Asset.gps_lat),
            func.avg(models.Asset.gps_lon),
        )
        .filter(models.Asset.city.isnot(None), models.Asset.city != "")
        .group_by(models.Asset.city, models.Asset.region)
        .order_by(func.count(models.Asset.id).desc())
        .all()
    )
    return [
        schemas.PlaceOut(
            city=city,
            region=region,
            count=int(count or 0),
            lat=float(lat) if lat is not None else None,
            lon=float(lon) if lon is not None else None,
        )
        for city, region, count, lat, lon in rows
    ]


@app.get("/api/trips", response_model=list[schemas.TripOut])
def list_trips(db: Session = Depends(get_db)):
    trips = (
        db.query(models.Trip)
        .order_by(models.Trip.start_at.desc().nullslast())
        .all()
    )
    return [_trip_out(t) for t in trips]


@app.get("/api/trips/{trip_id}", response_model=schemas.TripDetailOut)
def get_trip(trip_id: int, db: Session = Depends(get_db)):
    trip = db.get(models.Trip, trip_id)
    if trip is None:
        raise HTTPException(status_code=404, detail=f"trip {trip_id} not found")
    ids = trip_asset_ids(db, trip_id)
    assets = []
    if ids:
        rows = db.query(models.Asset).filter(models.Asset.id.in_(ids)).all()
        rank = {aid: i for i, aid in enumerate(ids)}
        rows.sort(key=lambda a: rank.get(a.id, len(ids)))
        assets = [_asset_out(db, a) for a in rows]
    return schemas.TripDetailOut(**_trip_out(trip), assets=assets)


@app.put("/api/trips/{trip_id}", response_model=schemas.TripOut)
def rename_trip(trip_id: int, req: schemas.TripUpdateRequest, db: Session = Depends(get_db)):
    trip = db.get(models.Trip, trip_id)
    if trip is None:
        raise HTTPException(status_code=404, detail=f"trip {trip_id} not found")
    title = req.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="title must not be empty")
    trip.title = title
    db.commit()
    return _trip_out(trip)


@app.post("/api/trips/{trip_id}/reel", response_model=schemas.PlanOut)
def trip_reel(trip_id: int, req: schemas.TripReelRequest, db: Session = Depends(get_db)):
    trip = db.get(models.Trip, trip_id)
    if trip is None:
        raise HTTPException(status_code=404, detail=f"trip {trip_id} not found")
    place = trip.city or "this trip"
    intent = (req.intent or "").strip() or (
        f"make a highlight reel for tiktok of my trip to {place} 9:16"
    )
    return planner.create_plan(intent, trip_id=trip_id)


# ---------------------------------------------------------------------------
# edit planner
# ---------------------------------------------------------------------------
@app.post("/api/edits/plan", response_model=schemas.PlanOut)
def create_plan(req: schemas.PlanRequest):
    plan = planner.create_plan(req.intent, trip_id=req.trip_id)
    if req.auto_approve and plan.get("clips"):
        planner.approve_plan(plan["plan_id"])
        plan = planner.get_plan(plan["plan_id"]) or plan
    return plan


@app.get("/api/edits/plan/{plan_id}", response_model=schemas.PlanOut)
def get_plan(plan_id: str):
    plan = planner.get_plan(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"plan {plan_id!r} not found")
    return plan


@app.put("/api/edits/plan/{plan_id}", response_model=schemas.PlanOut)
def update_plan(plan_id: str, req: schemas.PlanUpdateRequest):
    clips = [c.model_dump() for c in req.clips]
    plan = planner.update_plan(plan_id, clips)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"plan {plan_id!r} not found")
    return plan


@app.post("/api/edits/plan/{plan_id}/approve", response_model=schemas.ApproveOut)
def approve_plan(plan_id: str):
    try:
        result = planner.approve_plan(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if result is None:
        raise HTTPException(status_code=404, detail=f"plan {plan_id!r} not found")
    return result


@app.get("/api/edits/plans", response_model=list[schemas.PlanOut])
def list_plans(limit: int = Query(50, ge=1, le=200)):
    return planner.list_plans(limit=limit)


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------
@app.post("/api/render", response_model=schemas.RenderJobOut)
async def start_render(req: schemas.RenderRequest):
    plan = planner.get_plan(req.plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"plan {req.plan_id!r} not found")
    if plan.get("status") != "approved":
        raise HTTPException(
            status_code=409,
            detail="plan not approved; approve it first "
                   "(POST /api/edits/plan/{id}/approve)",
        )
    if req.ratio not in ("9:16", "16:9", "1:1"):
        raise HTTPException(
            status_code=422, detail="ratio must be one of 9:16, 16:9, 1:1"
        )

    ns: dict[str, Any] = {}

    async def _render_runner(progress, _flag):
        out = await render.render_job(
            req.plan_id, req.ratio, req.width, req.height,
            req.captions, req.music_path, progress, _flag,
        )
        if out:
            _RENDER_OUTPUTS[ns["job_id"]] = out
        return out

    job_id = jobs.run("render", _render_runner)
    ns["job_id"] = job_id
    return schemas.RenderJobOut(job_id=job_id, plan_id=req.plan_id)


@app.get("/api/render/{job_id}", response_model=schemas.RenderStatusOut)
def render_status(job_id: str):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id!r} not found")
    output_path = _RENDER_OUTPUTS.get(job_id) if job["status"] == "done" else None
    return schemas.RenderStatusOut(
        status=job["status"], output_path=output_path, progress=job["progress"],
    )


# ---------------------------------------------------------------------------
# exports
# ---------------------------------------------------------------------------
def _export_file(plan_id: str, exporter) -> schemas.ExportPathOut:
    try:
        path = exporter(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"export file not found: {path}")
    return schemas.ExportPathOut(ok=True, path=path)


@app.post("/api/export/fcpxml", response_model=schemas.ExportPathOut)
def export_fcpxml(req: schemas.ExportRequest):
    return _export_file(req.plan_id, fcpxml_export.export_fcpxml)


@app.post("/api/export/edl", response_model=schemas.ExportPathOut)
def export_edl(req: schemas.ExportRequest):
    return _export_file(req.plan_id, edl_export.export_edl)


@app.post("/api/export/capcut", response_model=schemas.ExportPathOut)
def export_capcut(req: schemas.ExportRequest):
    return _export_file(req.plan_id, capcut_export.export_capcut)


@app.post("/api/export/resolve", response_model=schemas.ResolveExportOut)
def export_resolve(req: schemas.ExportRequest):
    try:
        return resolve_export.export_to_resolve(req.plan_id)
    except resolve_export.ResolveUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))


# ---------------------------------------------------------------------------
# touchup
# ---------------------------------------------------------------------------
@app.post("/api/touchup", response_model=schemas.TouchupOut)
async def start_touchup(req: schemas.TouchupRequest):
    if req.preset not in touchup.PRESETS:
        raise HTTPException(
            status_code=422,
            detail=f"unknown preset {req.preset!r}; expected one of {', '.join(touchup.PRESETS)}",
        )
    with SessionLocal() as db:
        asset = db.get(models.Asset, req.asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail=f"asset {req.asset_id} not found")
        if asset.kind != "photo":
            raise HTTPException(status_code=400, detail="touchup only supports photos")

    def factory(progress, _flag):
        return _background(touchup.apply_touchup, req.asset_id, req.preset, job=progress)()

    job_id = jobs.run("touchup", factory)
    return schemas.TouchupOut(job_id=job_id, preset=req.preset)


@app.get("/api/touchup/preview")
def touchup_preview(asset_id: int = Query(...), preset: str = Query(...)):
    if preset not in touchup.PRESETS:
        raise HTTPException(
            status_code=422,
            detail=f"unknown preset {preset!r}; expected one of {', '.join(touchup.PRESETS)}",
        )
    try:
        data = touchup.preview_touchup(asset_id, preset)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return Response(content=data, media_type="image/jpeg")


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------
@app.get("/api/jobs", response_model=list[schemas.JobOut])
def list_jobs(limit: int = Query(20, ge=1, le=200)):
    return jobs.list_jobs(limit=limit)


@app.get("/api/jobs/{job_id}", response_model=schemas.JobOut)
def get_job(job_id: str):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id!r} not found")
    return job


# ---------------------------------------------------------------------------
# websocket
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        await ws_manager.disconnect(ws)


# ---------------------------------------------------------------------------
# static mounts (must come after all API routes)
# ---------------------------------------------------------------------------
app.mount("/exports", StaticFiles(directory=str(config.EXPORTS_DIR)), name="exports")

if config.FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(config.FRONTEND_DIST), html=True),
              name="frontend")
else:  # pragma: no cover - dev without a built frontend
    app.mount("/", StaticFiles(directory=str(config.FRONTEND_DIST), html=True,
                               check_dir=False), name="frontend")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=config.APP_HOST, port=config.APP_PORT)
