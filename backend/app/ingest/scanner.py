"""Scan/ingest job: recursive indexing with SHA-256 dedupe.

- Walks the requested paths, finds supported media files.
- Computes SHA-256; skips files whose hash already exists (resumable).
- Extracts metadata, writes place fields, generates thumb/poster/proxy, phash.
- Newly inserted assets start with status 'pending' and the AI pipeline
  worker picks them up automatically.
- Trip clustering runs once at the end of a scan, not per file.
"""
from __future__ import annotations

import hashlib
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional
from sqlalchemy.orm import Session

try:
    import imagehash  # type: ignore
except ImportError:  # pragma: no cover
    imagehash = None

from .. import config, models
from ..db import SessionLocal
from ..images import open_rgb
from ..ws import broadcast_batch, notify
from . import metadata, thumbs
from .place import assign_place
from .trips import cluster_trips

log = logging.getLogger("mediaforge.scanner")

CHUNK = 1024 * 1024


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _phash_of(path: str) -> Optional[str]:
    """Perceptual hash for near-duplicate detection (photos only)."""
    if imagehash is None:
        return None
    try:
        img = open_rgb(path)
        return str(imagehash.phash(img))
    except Exception:
        return None


def scan_paths(paths: list[str], progress: Callable[[float, str], None] | None = None,
               cancel_flag=None, rescan: bool = False) -> dict:
    """Scan directories/files. Returns {'added': n, 'skipped': n, 'errors': [...]}.

    `rescan=True` re-checks metadata of known assets (same as scan, but known
    hashes get metadata refreshed instead of being skipped silently).
    """
    added, skipped, errors = 0, 0, []
    files = _collect_files(paths)
    total = len(files)
    new_ids: list[int] = []
    for i, path in enumerate(files):
        if cancel_flag is not None and cancel_flag.cancelled:
            log.info("scan cancelled at %s", path)
            break
        try:
            with SessionLocal() as db:
                known = db.query(models.Asset).filter_by(path=str(path)).first()
                if known is not None:
                    skipped += 1
                    if rescan:
                        _refresh_asset(db, known)
                    continue
                file_hash = sha256_of(path)
                dup = db.query(models.Asset).filter_by(hash=file_hash).first()
                if dup is not None:
                    skipped += 1
                    continue
                asset = _insert_asset(db, path, file_hash)
                new_ids.append(asset.id)
                added += 1
        except Exception as exc:  # noqa: BLE001 - per-file resilience
            log.exception("scan failed for %s", path)
            errors.append(f"{path}: {exc}")
        frac = (i + 1) / max(total, 1) * 0.85
        if progress:
            progress(frac, f"scanning {path.name}")
        if total:
            broadcast_batch("scan", frac, i + 1, total)

    if new_ids:
        _generate_derivatives_parallel(new_ids)

    try:
        cluster_trips()
    except Exception as exc:
        log.warning("trip clustering failed: %s", exc)

    if progress:
        progress(1.0, f"scan complete: {added} added, {skipped} skipped")
    return {"added": added, "skipped": skipped, "errors": errors}


def _collect_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        path = Path(p)
        if not path.exists():
            continue
        if path.is_file():
            if path.suffix.lower() in config.SUPPORTED_EXTS:
                files.append(path)
            continue
        for root, _dirs, names in os.walk(path):
            for name in names:
                f = Path(root) / name
                if f.suffix.lower() in config.SUPPORTED_EXTS and f.is_file():
                    files.append(f)
    return sorted(files, key=lambda f: str(f))


def _insert_asset(db: Session, path: Path, file_hash: str) -> models.Asset:
    kind = "video" if path.suffix.lower() in config.VIDEO_EXTS else "photo"
    meta = metadata.extract_metadata(str(path), kind)
    asset = models.Asset(
        path=str(path), hash=file_hash, kind=kind,
        mime=meta.get("mime"), size=path.stat().st_size,
        width=meta.get("width"), height=meta.get("height"),
        duration=meta.get("duration"), taken_at=meta.get("taken_at"),
        camera_make=meta.get("camera_make"), camera_model=meta.get("camera_model"),
        gps_lat=meta.get("gps_lat"), gps_lon=meta.get("gps_lon"),
        gps_alt=meta.get("gps_alt"),
        status="pending",
    )
    assign_place(asset)
    db.add(asset)
    db.commit()
    db.refresh(asset)
    notify("scan", "", "running", 0.0, f"indexed {path.name}")
    return asset


def _generate_derivatives_parallel(asset_ids: list[int]) -> None:
    workers = max(1, min(config.FFMPEG_JOBS, len(asset_ids), 16))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(_generate_derivatives, aid) for aid in asset_ids]
        for fut in as_completed(futs):
            try:
                fut.result()
            except Exception as exc:
                log.warning("derivative generation failed: %s", exc)


def _generate_derivatives(asset_id: int) -> None:
    with SessionLocal() as db:
        asset = db.get(models.Asset, asset_id)
        if asset is None:
            return
        asset.thumb_path = thumbs.make_thumb(asset.path, asset.kind, asset.id)
        if asset.kind == "video":
            asset.poster_path = thumbs.make_poster(asset.path, asset.id, asset.duration)
            asset.proxy_path = thumbs.make_proxy(asset.path, asset.id)
        else:
            asset.phash = _phash_of(asset.path)
        db.commit()


def _refresh_asset(db: Session, asset: models.Asset) -> None:
    """Rescan: refresh metadata of a known asset."""
    try:
        meta = metadata.extract_metadata(asset.path, asset.kind)
        asset.width = meta.get("width") or asset.width
        asset.height = meta.get("height") or asset.height
        asset.duration = meta.get("duration") or asset.duration
        asset.taken_at = meta.get("taken_at") or asset.taken_at
        asset.camera_make = meta.get("camera_make") or asset.camera_make
        asset.camera_model = meta.get("camera_model") or asset.camera_model
        asset.gps_lat = meta.get("gps_lat") or asset.gps_lat
        asset.gps_lon = meta.get("gps_lon") or asset.gps_lon
        asset.gps_alt = meta.get("gps_alt") or asset.gps_alt
        if asset.location_source != "manual":
            assign_place(asset)
        if asset.kind == "photo" and not asset.phash:
            asset.phash = _phash_of(asset.path)
        db.commit()
    except Exception as exc:
        log.warning("rescan metadata refresh failed for %s: %s", asset.path, exc)
