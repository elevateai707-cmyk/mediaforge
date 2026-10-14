"""Official API collectors (enhancements on top of local ingestion).

Only the Meta Ad Library API for now. Per Meta's docs, it returns commercial ads
**only if they reached the EU**, so it's for pattern research in EU markets, not
for Canadian competitors (docs/data_sources.md). TikTok: the Commercial Content API
is EU-only and needs an application; Creative Center has no official API. Both
stay manual drops.

Rules this module keeps: official endpoint + your own token; no media download
(the snapshot URL carries your token and isn't stored); stop on rate-limit
error 613 instead of retrying around it; at most `max_pages` pages per search.
"""
from __future__ import annotations

import json
import os
import time
from datetime import date
from pathlib import Path
from typing import Any, Optional

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field

from .config import settings
from .ingest import IngestResult, Sidecar, _upsert
from .store import LocalStore

GRAPH = "https://graph.facebook.com"
FIELDS = ("id,page_id,page_name,ad_creative_bodies,ad_creative_link_titles,ad_creative_link_captions,"
          "ad_creative_link_descriptions,ad_delivery_start_time,ad_delivery_stop_time,languages,"
          "publisher_platforms")


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Competitor(_M):
    name: str
    meta_page_id: str = ""        # numeric Page ID, for search_page_ids
    tiktok_advertiser: str = ""
    notes: str = ""


class MetaAdLibraryConfig(_M):
    enabled: bool = False
    api_version: str = "v26.0"
    countries: list[str] = ["IE"]           # must include an EU country for commercial ads
    languages: list[str] = ["en"]
    search_terms: list[str] = []            # default: the site's keywords
    active_status: str = Field(default="ACTIVE", pattern="^(ACTIVE|ALL|INACTIVE)$")
    media_type: str = Field(default="ALL", pattern="^(ALL|IMAGE|MEME|VIDEO|NONE)$")
    max_pages: int = Field(default=3, ge=1, le=20)
    page_size: int = Field(default=50, ge=1, le=500)


class ScoutConfig(_M):
    keywords: list[str] = []
    competitors: list[Competitor] = []
    meta_ad_library: MetaAdLibraryConfig = MetaAdLibraryConfig()


def load_scout_config(site: str, sites_dir: Optional[Path] = None) -> ScoutConfig:
    path = (sites_dir or settings().sites_dir) / site / "scout.yaml"
    if not path.is_file():
        return ScoutConfig()
    return ScoutConfig(**(yaml.safe_load(path.read_text(encoding="utf-8")) or {}))


class CollectorRun(BaseModel):
    site: str
    source: str
    queries: int = 0
    pages: int = 0
    results: list[IngestResult] = []
    stopped: Optional[str] = None          # why the run ended early (rate limit, auth...)


def _archived_ad_to_sidecar(row: dict[str, Any], today: date) -> Sidecar:
    stop = row.get("ad_delivery_stop_time")
    texts = [*row.get("ad_creative_bodies", []), *row.get("ad_creative_link_titles", []),
             *row.get("ad_creative_link_descriptions", [])]
    unique = list(dict.fromkeys(t for t in texts if t))
    platforms = ", ".join(row.get("publisher_platforms", []))
    return Sidecar(
        platform="meta", advertiser=row.get("page_name") or f"page {row.get('page_id', '?')}",
        url=f"https://www.facebook.com/ads/library/?id={row['id']}",
        running_since=row.get("ad_delivery_start_time"),
        last_seen=stop or today, still_running=not stop,
        ad_text="\n\n".join(unique), notes=f"Meta Ad Library API · {platforms}".strip(" ·"),
    )


def meta_ad_library(site: str, cfg: ScoutConfig, store: Optional[LocalStore] = None,
                    token: Optional[str] = None, http: Optional[httpx.Client] = None,
                    today: Optional[date] = None, pause_s: float = 1.0) -> CollectorRun:
    run = CollectorRun(site=site, source="meta_ad_library_api")
    mc = cfg.meta_ad_library
    token = token or os.environ.get("META_AD_LIBRARY_TOKEN", "")
    if not mc.enabled:
        run.stopped = "disabled in scout.yaml"
        return run
    if not token:
        run.stopped = "META_AD_LIBRARY_TOKEN not set"
        return run
    store, today = store or LocalStore(), today or date.today()
    http = http or httpx.Client(timeout=60)
    queries: list[dict[str, str]] = [{"search_terms": t[:100]} for t in (mc.search_terms or cfg.keywords)]
    page_ids = [c.meta_page_id for c in cfg.competitors if c.meta_page_id]
    for i in range(0, len(page_ids), 10):
        queries.append({"search_page_ids": ",".join(page_ids[i:i + 10])})
    for q in queries:
        run.queries += 1
        url: Optional[str] = f"{GRAPH}/{mc.api_version}/ads_archive"
        params: Optional[dict[str, str]] = {
            "access_token": token, "ad_type": "ALL", "fields": FIELDS,
            "ad_reached_countries": json.dumps(mc.countries), "languages": json.dumps(mc.languages),
            "ad_active_status": mc.active_status, "media_type": mc.media_type,
            "limit": str(mc.page_size), **q,
        }
        for _ in range(mc.max_pages):
            if url is None:
                break
            r = http.get(url, params=params)
            body = r.json() if r.headers.get("content-type", "").startswith(("application/json", "text/javascript")) else {}
            err = body.get("error") if isinstance(body, dict) else None
            if r.status_code >= 400 or err:
                code = (err or {}).get("code")
                run.stopped = ("rate limit reached (error 613): stopped; try again next run" if code == 613
                               else f"API error {code or r.status_code}: {(err or {}).get('message', r.text[:200])}")
                return run
            run.pages += 1
            for row in body.get("data", []):
                try:
                    res = _upsert(store, site, _archived_ad_to_sidecar(row, today), today, "meta_ad_library_api")
                except ValueError as exc:
                    res = IngestResult(source="", status="failed", message=str(exc).splitlines()[0])
                res.source = f"meta:{row.get('id')}"
                run.results.append(res)
            url = (body.get("paging") or {}).get("next")
            params = None  # `next` already carries every parameter
            if url and pause_s:
                time.sleep(pause_s)
    return run
