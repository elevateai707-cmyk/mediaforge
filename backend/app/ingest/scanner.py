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
import time
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


# Files modified more recently than this are assumed to still be copying.
SETTLE_SECONDS = 3.0
DEFER_UNSTABLE_S = 4.0       # re-check a still-changing file after this long
DEFER_INCOMPLETE_S = 60.0    # re-probe a file ffprobe could not read after this long

_IGNORED_SUFFIXES = (".part", ".crdownload", ".tmp", ".temp", ".partial", ".download")


def is_ignored_name(name: str) -> bool:
    """Hidden, temp and partial-download names (never media)."""
    low = name.lower()
    return (name.startswith(".") or name.startswith("~$")
            or low.endswith(_IGNORED_SUFFIXES) or low == "thumbs.db")


def is_settled(path: Path, now: Optional[float] = None) -> bool:
    """True when neither mtime nor ctime changed in the last SETTLE_SECONDS.

    ctime moves on every write/extend even when a copy tool restores the old
    mtime, so a still-growing file never looks settled.
    """
    try:
        st = path.stat()
    except OSError:
        return False
    now = time.time() if now is None else now
    return now - max(st.st_mtime, st.st_ctime) >= SETTLE_SECONDS


class Deferred:
    """A file that cannot be ingested yet (still copying / unreadable)."""

    def __init__(self, path: str, delay: float, reason: str) -> None:
        self.path, self.delay, self.reason = path, delay, reason

    def __repr__(self) -> str:  # pragma: no cover
        return f"Deferred({self.path!r}, {self.delay}, {self.reason!r})"


def scan_paths(paths: list[str], progress: Callable[[float, str], None] | None = None,
               cancel_flag=None, rescan: bool = False, settle: bool = False) -> dict:
    """Scan directories/files. Returns {'added', 'skipped', 'errors', 'deferred'}.

    `rescan=True` re-checks metadata of known assets (same as scan, but known
    hashes get metadata refreshed instead of being skipped silently).

    Known paths are skipped with one set lookup and previously seen duplicates
    by path+size+mtime (scan_skips), so a catch-up scan over a large library
    never re-hashes anything. `settle=True` defers files modified in the last
    few seconds (a copy may still be running). A video ffprobe cannot read is
    deferred, not inserted half-indexed. ``deferred`` lists these files as
    ``Deferred`` objects for the caller to re-enqueue.
    """
    added, skipped, errors = 0, 0, []
    deferred: list[Deferred] = []
    files = _collect_files(paths)
    total = len(files)
    new_ids: list[int] = []
    known_paths: set[str] = set()
    skip_rows: dict[str, tuple[int, int]] = {}
    if not rescan:
        with SessionLocal() as db:
            known_paths = {r[0] for r in db.query(models.Asset.path).all()}
            skip_rows = {r[0]: (r[1], r[2]) for r in db.query(
                models.ScanSkip.path, models.ScanSkip.size, models.ScanSkip.mtime_ns).all()}
    for i, path in enumerate(files):
        if cancel_flag is not None and cancel_flag.cancelled:
            log.info("scan cancelled at %s", path)
            break
        try:
            outcome = _scan_one(path, rescan, settle, known_paths, skip_rows, new_ids)
            if isinstance(outcome, Deferred):
                deferred.append(outcome)
            elif outcome == "added":
                added += 1
            else:
                skipped += 1
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

    if new_ids or rescan:
        try:
            cluster_trips()
        except Exception as exc:
            log.warning("trip clustering failed: %s", exc)

    if progress:
        progress(1.0, f"scan complete: {added} added, {skipped} skipped"
                      + (f", {len(deferred)} deferred" if deferred else ""))
    return {"added": added, "skipped": skipped, "errors": errors,
            "deferred": deferred}


def _scan_one(path: Path, rescan: bool, settle: bool, known_paths: set[str],
              skip_rows: dict[str, tuple[int, int]], new_ids: list[int]):
    """Ingest one file. Returns 'added' | 'skipped' | Deferred."""
    spath = str(path)
    if rescan:
        with SessionLocal() as db:
            known = db.query(models.Asset).filter_by(path=spath).first()
            if known is not None:
                _refresh_asset(db, known)
                return "skipped"
    elif spath in known_paths:
        return "skipped"
    st = path.stat()
    if skip_rows.get(spath) == (st.st_size, st.st_mtime_ns):
        return "skipped"
    if settle and not is_settled(path):
        return Deferred(spath, DEFER_UNSTABLE_S, "file still changing")
    kind = "video" if path.suffix.lower() in config.VIDEO_EXTS else "photo"
    meta = metadata.extract_metadata(spath, kind)
    if kind == "video" and not meta.get("duration"):
        # ffprobe could not read it: most likely an incomplete copy.
        return Deferred(spath, DEFER_INCOMPLETE_S, "video not readable yet")
    # Hash outside any DB session: it can take minutes on a 4K file and must
    # not pin a pooled connection while it runs.
    file_hash = sha256_of(path)
    st2 = path.stat()
    if (st2.st_size, st2.st_mtime_ns) != (st.st_size, st.st_mtime_ns):
        return Deferred(spath, DEFER_UNSTABLE_S, "file changed while hashing")
    with SessionLocal() as db:
        if db.query(models.Asset).filter_by(path=spath).first() is not None:
            return "skipped"
        dup = db.query(models.Asset).filter_by(hash=file_hash).first()
        if dup is not None:
            row = db.get(models.ScanSkip, spath)
            if row is None:
                db.add(models.ScanSkip(path=spath, size=st2.st_size,
                                       mtime_ns=st2.st_mtime_ns,
                                       reason=f"duplicate of asset {dup.id}"))
            else:
                row.size, row.mtime_ns = st2.st_size, st2.st_mtime_ns
            db.commit()
            return "skipped"
        asset = _insert_asset(db, path, file_hash, meta)
        new_ids.append(asset.id)
        known_paths.add(spath)
        return "added"


def _collect_files(paths: list[str]) -> list[Path]:
    files: dict[str, Path] = {}
    for p in paths:
        path = Path(p)
        if not path.exists():
            continue
        if path.is_file():
            if path.suffix.lower() in config.SUPPORTED_EXTS and not is_ignored_name(path.name):
                files[str(path)] = path
            continue
        for root, dirs, names in os.walk(path):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in names:
                if is_ignored_name(name):
                    continue
                f = Path(root) / name
                if f.suffix.lower() in config.SUPPORTED_EXTS and f.is_file():
                    files[str(f)] = f
    return [files[k] for k in sorted(files)]


def _insert_asset(db: Session, path: Path, file_hash: str,
                  meta: Optional[dict] = None) -> models.Asset:
    kind = "video" if path.suffix.lower() in config.VIDEO_EXTS else "photo"
    if meta is None:
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
