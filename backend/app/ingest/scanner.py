"""Scan/ingest job: recursive indexing with SHA-256 dedupe.

- Walks the requested paths, finds supported media files.
- Computes SHA-256; skips files whose hash already exists (resumable).
- Extracts metadata, generates thumb/poster/proxy, phash for photos.
- Newly inserted assets start with status 'pending' and the AI pipeline
  worker picks them up automatically.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Callable, Optional

from sqlalchemy.orm import Session

from .. import config, models
from ..db import SessionLocal
from ..ws import broadcast_batch, notify
from . import metadata, thumbs

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
    try:
        import imagehash  # type: ignore
        from PIL import Image
        img = Image.open(path)
        img.load()
        return str(imagehash.phash(img))
    except Exception:
        return None


def _phash_distance(a: str, b: str) -> float:
    ha, hb = int(a, 16), int(b, 16)
    return bin(ha ^ hb).count("1") / 64.0


def scan_paths(paths: list[str], progress: Callable[[float, str], None] | None = None,
               cancel_flag=None, rescan: bool = False) -> dict:
    """Scan directories/files. Returns {'added': n, 'skipped': n, 'errors': [...]}.

    `rescan=True` re-checks metadata of known assets (same as scan, but known
    hashes get metadata refreshed instead of being skipped silently).
    """
    added, skipped, errors = 0, 0, []
    files = _collect_files(paths)
    total = len(files)
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
                _insert_asset(db, path, file_hash)
                added += 1
        except Exception as exc:  # noqa: BLE001 - per-file resilience
            log.exception("scan failed for %s", path)
            errors.append(f"{path}: {exc}")
        frac = (i + 1) / total if total else 1.0
        if progress:
            progress(frac, f"scanning {path.name}")
        if total:
            broadcast_batch("scan", frac, i + 1, total)
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
        for root, _dirs, names in os_walk(path):
            for name in names:
                f = Path(root) / name
                if f.suffix.lower() in config.SUPPORTED_EXTS and f.is_file():
                    files.append(f)
    # deterministic order
    return sorted(files, key=lambda f: str(f))


def os_walk(path: Path):
    import os
    for root, dirs, files in os.walk(path):
        yield Path(root), dirs, files


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
        status="pending",
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    # Thumbnail / poster / proxy (best-effort)
    asset.thumb_path = thumbs.make_thumb(str(path), kind, asset.id)
    if kind == "video":
        asset.poster_path = thumbs.make_poster(str(path), asset.id, asset.duration)
        asset.proxy_path = thumbs.make_proxy(str(path), asset.id)
    else:
        asset.phash = _phash_of(str(path))
    db.commit()
    notify("scan", "", "running", 0.0, f"indexed {path.name}")
    return asset


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
        if asset.kind == "photo" and not asset.phash:
            asset.phash = _phash_of(asset.path)
        db.commit()
    except Exception as exc:
        log.warning("rescan metadata refresh failed for %s: %s", asset.path, exc)
