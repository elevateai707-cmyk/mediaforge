"""Local ingestion: how ads get into AdLab on day one.

Three doors, all local, none of which fetch anything from the internet:

1. Drop files in `data/incoming/<site>/`: MP4/MOV/WebM/M4V, PNG/JPG/WebP screenshots,
   or CSV/JSON you exported from a platform's own tool. Optional sidecar
   `<file>.yaml` (or `<file-stem>.yaml`) with what you know (see `Sidecar`).
   `process_incoming()` handles the folder, and the watcher calls it in a loop.
2. `adlab ingest <url> [file]`: record an Ad Library / Creative Center link you
   pasted. The URL is stored as the source reference and is never fetched.
3. CSV/JSON rows: one ad per row/object (Meta API JSON `{"data": [...]}` works too).

Dedupe: identical media (SHA-256) always maps to the same ad; a known source URL
attaches new media to its existing ad. Re-seeing an ad extends last_seen and bumps
times_seen instead of creating a duplicate. Media is copied to `data/media/` with
owner-only permissions (analysis only, never reused in production).
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Literal, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, ValidationError, field_validator

from .config import settings
from .schemas import Ad, CaptureMethod, Platform, Signals, SourceRef
from .sites import list_sites
from .store import LocalStore
from .variants import dhash_image, dhash_video

VIDEO_EXTS = {".mp4", ".mov", ".webm", ".m4v"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
DATA_EXTS = {".csv", ".json"}
SIDECAR_EXTS = {".yaml", ".yml"}
DROP_TOKEN_PARAMS = {"access_token", "fbclid", "gclid", "ttclid"}


# --- helpers -----------------------------------------------------------------
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_URL = TypeAdapter(HttpUrl)


def clean_url(url: str) -> str:
    """Canonical form used for storage AND lookup: secrets/click-trackers dropped
    (a pasted Meta API URL carries your access_token), normalised like HttpUrl."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k not in DROP_TOKEN_PARAMS and not k.startswith("utm_")]
    return str(_URL.validate_python(
        urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, urlencode(query), ""))))


def guess_platform(url: Optional[str]) -> Platform:
    host = urlsplit(url).netloc.lower() if url else ""
    if any(h in host for h in ("facebook.com", "fb.com", "instagram.com", "meta.com")):
        return "meta"
    if "tiktok" in host:
        return "tiktok"
    return "other"


def parse_date(value: Any) -> Optional[date]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip()[:10])


def parse_bool(value: Any) -> Optional[bool]:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    v = str(value).strip().lower()
    if v in ("yes", "y", "true", "1", "active", "running"):
        return True
    if v in ("no", "n", "false", "0", "inactive", "stopped", "ended"):
        return False
    raise ValueError(f"not a yes/no value: {value!r}")


class Sidecar(BaseModel):
    """What you know about a dropped ad. Every field is optional; typos are errors."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    site: Optional[str] = None
    platform: Optional[Platform] = None
    advertiser: Optional[str] = None
    url: Optional[str] = None
    running_since: Optional[date] = None
    last_seen: Optional[date] = None
    still_running: Optional[bool] = None
    variants: Optional[int] = Field(default=None, ge=1)
    platform_rank: Optional[int] = Field(default=None, ge=1)
    ad_text: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("still_running", mode="before")
    @classmethod
    def _bool(cls, v: Any) -> Optional[bool]:
        return parse_bool(v)

    @field_validator("running_since", "last_seen", mode="before")
    @classmethod
    def _date(cls, v: Any) -> Optional[date]:
        return parse_date(v)


def load_sidecar(media: Path) -> tuple[Sidecar, Optional[Path]]:
    for cand in (media.with_name(media.name + ".yaml"), media.with_name(media.name + ".yml"),
                 media.with_suffix(".yaml"), media.with_suffix(".yml")):
        if cand.is_file() and cand != media:
            data = yaml.safe_load(cand.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                raise ValueError(f"{cand.name}: expected key: value lines")
            return Sidecar(**data), cand
    return Sidecar(), None


class IngestResult(BaseModel):
    source: str
    status: Literal["new", "updated", "failed"]
    ad_id: Optional[str] = None
    message: str = ""


# --- core merge ---------------------------------------------------------------
def _upsert(store: LocalStore, site: str, sc: Sidecar, today: date, captured_via: CaptureMethod,
            media: Optional[tuple[Path, str]] = None, row_key: Optional[str] = None) -> IngestResult:
    """Create or extend one ad from a single observation (+ optional media file)."""
    if site not in list_sites():
        return IngestResult(source="", status="failed",
                            message=f"unknown site {site!r} (sites: {', '.join(list_sites())})")
    url = clean_url(sc.url) if sc.url else None
    existing: Optional[Ad] = None
    if media:
        ad_id = store.ad_for_media(media[1])
        existing = store.get(ad_id) if ad_id else None
    if existing is None and url:
        existing = store.by_url(url)
    if existing is None and row_key:
        existing = store.get(row_key)

    # The latest day the ad is KNOWN to have run. Seeing a stopped ad today doesn't
    # mean it ran until today: without an explicit last_seen, don't extend it.
    seen = sc.last_seen or (today if sc.still_running is not False else None)
    if existing is not None:
        ad = existing.model_copy(deep=True)
        if ad.site != site:
            return IngestResult(source="", status="failed", ad_id=ad.id,
                                message=f"already filed under site {ad.site!r}, not {site!r}")
        ad.first_seen = min(ad.first_seen, sc.running_since or ad.first_seen)
        ad.last_seen = max(ad.last_seen, seen or ad.last_seen, ad.first_seen)
        ad.signals.times_seen += 1
        status = "updated"
    else:
        ad_id = media[1] if media else row_key or hashlib.sha256(f"url:{url}".encode()).hexdigest()
        first = sc.running_since or seen or today
        ad = Ad(id=ad_id, site=site, first_seen=first, last_seen=max(first, seen or first),
                source=SourceRef(platform=sc.platform or guess_platform(url), captured_via=captured_via,
                                 url=url, advertiser=sc.advertiser or "unknown", notes=sc.notes or ""))
        status = "new"

    src = ad.source
    if url and not src.url:
        src.url = url  # type: ignore[assignment]
    if sc.advertiser and src.advertiser == "unknown":
        src.advertiser = sc.advertiser
    if sc.platform and src.platform == "other":
        src.platform = sc.platform
    if sc.notes and sc.notes not in src.notes:
        src.notes = f"{src.notes}\n{sc.notes}".strip()
    if sc.ad_text and len(sc.ad_text) > len(ad.ad_text):
        ad.ad_text = sc.ad_text
    sig = ad.signals
    if sc.still_running is not None:
        sig.still_running = sc.still_running
    if sc.variants:
        sig.reported_variants = max(sc.variants, sig.reported_variants or 1)
    if sc.platform_rank:
        sig.platform_rank = min(sc.platform_rank, sig.platform_rank or sc.platform_rank)

    if media:
        path, digest = media
        stored = _store_media(path, digest)
        if digest not in ad.media_sha256:
            ad.media_sha256.append(digest)
            ad.media_paths.append(str(stored))
    ad = Ad.model_validate(ad.model_dump())
    store.upsert(ad)
    if media:
        kind = "video" if media[0].suffix.lower() in VIDEO_EXTS else "image"
        fp = dhash_video(stored) if kind == "video" else dhash_image(stored)
        store.add_media(media[1], ad.id, str(stored), kind, fp)
    return IngestResult(source="", status=status, ad_id=ad.id)


def _store_media(src: Path, digest: str) -> Path:
    """Private copy: data/media/<aa>/<sha><ext>, owner-only."""
    d = settings().media_dir / digest[:2]
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(settings().media_dir, 0o700)
    dest = d / f"{digest}{src.suffix.lower()}"
    if not dest.exists():
        shutil.copy2(src, dest)
        os.chmod(dest, 0o600)
    return dest


# --- public entry points ----------------------------------------------------------
def ingest_media(path: Path, site: str, sidecar: Optional[Sidecar] = None,
                 store: Optional[LocalStore] = None, today: Optional[date] = None,
                 captured_via: CaptureMethod = "manual_drop") -> IngestResult:
    ext = path.suffix.lower()
    if ext not in VIDEO_EXTS | IMAGE_EXTS:
        return IngestResult(source=str(path), status="failed", message=f"unsupported file type {ext}")
    res = _upsert(store or LocalStore(), site, sidecar or Sidecar(), today or date.today(),
                  captured_via, media=(path, sha256_file(path)))
    res.source = str(path)
    return res


def ingest_link(url: str, site: str, file: Optional[Path] = None, sidecar: Optional[Sidecar] = None,
                store: Optional[LocalStore] = None, today: Optional[date] = None) -> IngestResult:
    """Record a pasted Ad Library / Creative Center link (never fetched)."""
    if urlsplit(url).scheme not in ("http", "https"):
        return IngestResult(source=url, status="failed", message="not an http(s) link")
    sc = (sidecar or Sidecar()).model_copy(update={"url": url})
    if file is not None:
        return ingest_media(file, site, sc, store, today, captured_via="manual_link")
    res = _upsert(store or LocalStore(), site, sc, today or date.today(), "manual_link")
    res.source = url
    return res


ROW_ALIASES: dict[str, tuple[str, ...]] = {
    "advertiser": ("advertiser", "advertiser_name", "page_name", "brand"),
    "ad_text": ("ad_text", "text", "body", "primary_text", "caption", "ad_creative_bodies"),
    "url": ("url", "ad_url", "link", "ad_library_url", "permalink"),
    "running_since": ("running_since", "first_seen", "start_date", "ad_delivery_start_time", "first_shown"),
    "last_seen": ("last_seen", "end_date", "ad_delivery_stop_time", "last_shown"),
    "platform": ("platform",),
    "variants": ("variants", "variant_count"),
    "platform_rank": ("platform_rank", "rank"),
    "still_running": ("still_running", "active", "status"),
    "notes": ("notes",),
}


def row_to_sidecar(row: dict[str, Any], base: Sidecar) -> Sidecar:
    data = base.model_dump(exclude_none=True)
    lower = {str(k).strip().lower(): v for k, v in row.items()}
    for field, names in ROW_ALIASES.items():
        for n in names:
            v = lower.get(n)
            if v in (None, "", []):
                continue
            if isinstance(v, list):
                v = "\n\n".join(str(x) for x in v if x)
            data[field] = v
            break
    if "url" not in data and lower.get("id") and base.platform == "meta":
        data["url"] = f"https://www.facebook.com/ads/library/?id={lower['id']}"
    return Sidecar(**data)


def ingest_rows(path: Path, site: str, sidecar: Optional[Sidecar] = None,
                store: Optional[LocalStore] = None, today: Optional[date] = None) -> list[IngestResult]:
    store, today, base = store or LocalStore(), today or date.today(), sidecar or Sidecar()
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data.get("data", data) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            return [IngestResult(source=str(path), status="failed", message="JSON must be a list or {data: [...]}")]
    else:
        with path.open(newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
    out = []
    for i, row in enumerate(rows, 1):
        src = f"{path.name}#{i}"
        try:
            sc = row_to_sidecar(row, base)
            if not (sc.url or sc.ad_text):
                raise ValueError("row has neither a link nor ad text")
            key = None if sc.url else hashlib.sha256(
                f"row:{site}:{sc.advertiser}:{sc.ad_text}".encode()).hexdigest()
            res = _upsert(store, site, sc, today, "manual_drop", row_key=key)
        except (ValidationError, ValueError) as exc:
            res = IngestResult(source=src, status="failed", message=str(exc).splitlines()[0])
        res.source = src
        out.append(res)
    return out


# --- the incoming folder -----------------------------------------------------------
def _site_for(path: Path, incoming: Path, sc: Sidecar, known: set[str]) -> Optional[str]:
    rel = path.relative_to(incoming).parts
    folder = rel[0] if len(rel) > 1 else None
    site = sc.site or folder
    return site if site in known else None


def process_incoming(store: Optional[LocalStore] = None, today: Optional[date] = None,
                     min_age_s: float = 3.0) -> list[IngestResult]:
    """One sweep of data/incoming/. Files still being written (younger than min_age_s) wait."""
    s = settings()
    s.ensure_dirs()
    incoming, store, today = s.incoming_dir, store or LocalStore(), today or date.today()
    known = set(list_sites())
    results: list[IngestResult] = []
    now = time.time()
    for path in sorted(p for p in incoming.rglob("*") if p.is_file()):
        rel = path.relative_to(incoming)
        if rel.parts[0] in ("_done", "_failed") or path.name.startswith("."):
            continue
        ext = path.suffix.lower()
        if ext in SIDECAR_EXTS:
            continue  # moved together with its media
        if now - path.stat().st_mtime < min_age_s:
            continue
        try:
            sc, sidecar_path = load_sidecar(path)
        except (ValidationError, ValueError, yaml.YAMLError) as exc:
            results.append(_park(path, None, "_failed", f"bad sidecar: {str(exc).splitlines()[0]}"))
            continue
        site = _site_for(path, incoming, sc, known)
        if site is None:
            results.append(_park(path, sidecar_path, "_failed",
                                 f"which site? put it in incoming/<site>/ (sites: {', '.join(sorted(known))})"))
            continue
        if ext in DATA_EXTS:
            rows = ingest_rows(path, site, sc, store, today)
            results.extend(rows)
            ok = any(r.status != "failed" for r in rows)
            _park(path, sidecar_path, "_done" if ok else "_failed",
                  "; ".join(f"{r.source}: {r.message}" for r in rows if r.status == "failed"))
            continue
        if ext not in VIDEO_EXTS | IMAGE_EXTS:
            results.append(_park(path, sidecar_path, "_failed", f"unsupported file type {ext}"))
            continue
        res = ingest_media(path, site, sc, store, today)
        results.append(res)
        _park(path, sidecar_path, "_done" if res.status != "failed" else "_failed", res.message)
    return results


def _park(path: Path, sidecar: Optional[Path], where: str, message: str) -> IngestResult:
    """Move a processed drop (and its sidecar) out of the way, with a reason if it failed."""
    dest = settings().incoming_dir / where / date.today().isoformat()
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / path.name
    n = 1
    while target.exists():
        target = dest / f"{path.stem}-{n}{path.suffix}"
        n += 1
    shutil.move(str(path), target)
    if sidecar and sidecar.exists():
        shutil.move(str(sidecar), dest / f"{target.name}.yaml")
    if where == "_failed" and message:
        (dest / f"{target.name}.error.txt").write_text(message + "\n", encoding="utf-8")
    return IngestResult(source=str(path), status="failed" if where == "_failed" else "updated",
                        message=message)


def watch(interval_s: float = 5.0, on_result=print, stop=lambda: False) -> None:
    """Poll data/incoming/ until stop() is true (Ctrl+C in the CLI)."""
    store = LocalStore()
    while not stop():
        for r in process_incoming(store):
            on_result(r)
        time.sleep(interval_s)


def summarize(results: Iterable[IngestResult]) -> dict[str, int]:
    out = {"new": 0, "updated": 0, "failed": 0}
    for r in results:
        out[r.status] += 1
    return out
