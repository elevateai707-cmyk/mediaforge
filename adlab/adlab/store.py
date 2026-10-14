"""Where scouted ads live.

* `LocalStore` (SQLite, data/adlab.db) is the working store and the source of truth.
  It works offline, and holds the media index: file hashes, local paths and visual
  fingerprints, which only make sense on this machine.
* `SupabaseMirror` pushes validated rows to the Supabase `ads` table (migration in
  supabase/migrations/) for the Phase 7 dashboard, when SUPABASE_URL and
  SUPABASE_SERVICE_ROLE_KEY are set. A failed push is logged and retried by
  `adlab sync`; it never blocks scouting.

Rows are written only from validated `Ad` models; the full model is kept as JSON.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable, Optional, TypeVar

import httpx
from pydantic import BaseModel

from .config import settings
from .schemas import Ad

log = logging.getLogger("adlab.store")
M = TypeVar("M", bound=BaseModel)

SCHEMA = """
create table if not exists ads (
  id            text primary key,
  site          text not null,
  advertiser    text not null,
  platform      text not null,
  source_url    text,
  first_seen    text not null,
  last_seen     text not null,
  variant_group text,
  score_total   real,
  payload       text not null,
  synced        integer not null default 0,
  updated_at    text not null default (datetime('now'))
);
create index if not exists ads_site_score on ads (site, score_total desc);
create index if not exists ads_url on ads (source_url);
create table if not exists media (
  sha256   text primary key,
  ad_id    text not null references ads (id) on delete cascade,
  path     text not null,
  kind     text not null check (kind in ('video', 'image')),
  dhash    text                                   -- 64-bit visual fingerprint, hex
);
create index if not exists media_ad on media (ad_id);
create table if not exists entities (       -- teardowns, patterns, briefs, creatives, compliance, results
  kind       text not null,
  id         text not null,
  site       text,
  ref        text,                          -- parent id (ad for a teardown, brief for a creative, ...)
  status     text,
  score      real,
  payload    text not null,
  synced     integer not null default 0,
  created_at text not null default (datetime('now')),
  updated_at text not null default (datetime('now')),
  primary key (kind, id)
);
create index if not exists entities_kind_ref on entities (kind, ref);
create index if not exists entities_kind_site on entities (kind, site, score desc);
"""

KINDS = ("teardown", "pattern", "brief", "creative", "compliance", "result", "review")


class LocalStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = path or settings().data_dir / "adlab.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._db()) as db:
            db.executescript(SCHEMA)

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("pragma foreign_keys = on")
        return db

    # --- ads ---------------------------------------------------------------------
    def upsert(self, ad: Ad) -> None:
        ad = Ad.model_validate(ad.model_dump())  # never store an unvalidated object
        with closing(self._db()) as db, db:
            db.execute(
                """insert into ads (id, site, advertiser, platform, source_url, first_seen, last_seen,
                                    variant_group, score_total, payload, synced, updated_at)
                   values (?,?,?,?,?,?,?,?,?,?,0,datetime('now'))
                   on conflict(id) do update set site=excluded.site, advertiser=excluded.advertiser,
                     platform=excluded.platform, source_url=excluded.source_url,
                     first_seen=excluded.first_seen, last_seen=excluded.last_seen,
                     variant_group=excluded.variant_group, score_total=excluded.score_total,
                     payload=excluded.payload, synced=0, updated_at=datetime('now')""",
                (ad.id, ad.site, ad.source.advertiser, ad.source.platform,
                 str(ad.source.url) if ad.source.url else None, ad.first_seen.isoformat(),
                 ad.last_seen.isoformat(), ad.variant_group,
                 ad.score.total if ad.score else None, ad.model_dump_json()),
            )

    def get(self, ad_id: str) -> Optional[Ad]:
        with closing(self._db()) as db:
            row = db.execute("select payload from ads where id = ?", (ad_id,)).fetchone()
        return Ad.model_validate_json(row["payload"]) if row else None

    def by_url(self, url: str) -> Optional[Ad]:
        with closing(self._db()) as db:
            row = db.execute("select payload from ads where source_url = ?", (url,)).fetchone()
        return Ad.model_validate_json(row["payload"]) if row else None

    def ads(self, site: Optional[str] = None) -> list[Ad]:
        q, args = "select payload from ads", ()
        if site:
            q, args = q + " where site = ?", (site,)
        with closing(self._db()) as db:
            rows = db.execute(q + " order by coalesce(score_total, -1) desc, id", args).fetchall()
        return [Ad.model_validate_json(r["payload"]) for r in rows]

    # --- media index -------------------------------------------------------------
    def add_media(self, sha256: str, ad_id: str, path: str, kind: str, dhash: Optional[str]) -> None:
        with closing(self._db()) as db, db:
            db.execute("insert or ignore into media (sha256, ad_id, path, kind, dhash) values (?,?,?,?,?)",
                       (sha256, ad_id, path, kind, dhash))

    def ad_for_media(self, sha256: str) -> Optional[str]:
        with closing(self._db()) as db:
            row = db.execute("select ad_id from media where sha256 = ?", (sha256,)).fetchone()
        return row["ad_id"] if row else None

    def media_for(self, ad_ids: Iterable[str]) -> dict[str, list[dict]]:
        ids = list(ad_ids)
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        with closing(self._db()) as db:
            rows = db.execute(f"select * from media where ad_id in ({marks})", ids).fetchall()
        out: dict[str, list[dict]] = {}
        for r in rows:
            out.setdefault(r["ad_id"], []).append(dict(r))
        return out

    # --- other pipeline objects ----------------------------------------------------
    def put(self, kind: str, obj: BaseModel, id: str, site: Optional[str] = None, ref: Optional[str] = None,
            status: Optional[str] = None, score: Optional[float] = None) -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown kind {kind!r}")
        obj = type(obj).model_validate(obj.model_dump())  # validated objects only
        with closing(self._db()) as db, db:
            db.execute(
                """insert into entities (kind, id, site, ref, status, score, payload, synced)
                   values (?,?,?,?,?,?,?,0)
                   on conflict(kind, id) do update set site=excluded.site, ref=excluded.ref,
                     status=excluded.status, score=excluded.score, payload=excluded.payload,
                     synced=0, updated_at=datetime('now')""",
                (kind, id, site, ref, status, score, obj.model_dump_json()))

    def fetch(self, kind: str, id: str, model: type[M]) -> Optional[M]:
        with closing(self._db()) as db:
            row = db.execute("select payload from entities where kind=? and id=?", (kind, id)).fetchone()
        return model.model_validate_json(row["payload"]) if row else None

    def rows(self, kind: str, site: Optional[str] = None, ref: Optional[str] = None,
             status: Optional[str] = None, since: Optional[str] = None) -> list[sqlite3.Row]:
        q, args = "select * from entities where kind = ?", [kind]
        for col, val in (("site", site), ("ref", ref), ("status", status)):
            if val is not None:
                q += f" and {col} = ?"
                args.append(val)
        if since:
            q += " and created_at >= ?"
            args.append(since)
        with closing(self._db()) as db:
            return db.execute(q + " order by coalesce(score, -1e9) desc, created_at desc", args).fetchall()

    def list(self, kind: str, model: type[M], **filters: Any) -> list[M]:
        return [model.model_validate_json(r["payload"]) for r in self.rows(kind, **filters)]

    def set_status(self, kind: str, id: str, status: str) -> None:
        with closing(self._db()) as db, db:
            n = db.execute("update entities set status=?, synced=0, updated_at=datetime('now') "
                           "where kind=? and id=?", (status, kind, id)).rowcount
        if not n:
            raise KeyError(f"{kind} {id} not found")

    # --- sync bookkeeping ----------------------------------------------------------
    def unsynced(self) -> list[Ad]:
        with closing(self._db()) as db:
            rows = db.execute("select payload from ads where synced = 0").fetchall()
        return [Ad.model_validate_json(r["payload"]) for r in rows]

    def unsynced_rows(self, kind: str) -> list[sqlite3.Row]:
        with closing(self._db()) as db:
            return db.execute("select * from entities where kind = ? and synced = 0 order by created_at",
                              (kind,)).fetchall()

    def mark_rows_synced(self, kind: str, ids: Iterable[str]) -> None:
        with closing(self._db()) as db, db:
            db.executemany("update entities set synced = 1 where kind = ? and id = ?", [(kind, i) for i in ids])

    def mark_synced(self, ad_ids: Iterable[str]) -> None:
        with closing(self._db()) as db, db:
            db.executemany("update ads set synced = 1 where id = ?", [(i,) for i in ad_ids])


class SupabaseMirror:
    """Upserts ads into Supabase over PostgREST with the service-role key."""

    def __init__(self, url: Optional[str] = None, key: Optional[str] = None,
                 http: Optional[httpx.Client] = None):
        self.url = (url or os.environ.get("SUPABASE_URL", "")).rstrip("/")
        self.key = key or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
        self._http = http or httpx.Client(timeout=30)

    @property
    def configured(self) -> bool:
        return bool(self.url and self.key)

    @staticmethod
    def row(ad: Ad) -> dict:
        return {
            "id": ad.id, "site": ad.site, "platform": ad.source.platform,
            "captured_via": ad.source.captured_via, "advertiser": ad.source.advertiser,
            "source_url": str(ad.source.url) if ad.source.url else None,
            "first_seen": ad.first_seen.isoformat(), "last_seen": ad.last_seen.isoformat(),
            "variant_group": ad.variant_group,
            "score_total": ad.score.total if ad.score else None,
            "payload": json.loads(ad.model_dump_json()),
        }

    def push(self, ads: list[Ad]) -> int:
        return self.upsert("ads", [self.row(a) for a in ads], "id")

    def upsert(self, table: str, rows: list[dict], on_conflict: str) -> int:
        if not rows:
            return 0
        if not self.configured:
            raise RuntimeError("Supabase not configured (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY)")
        r = self._http.post(
            f"{self.url}/rest/v1/{table}", params={"on_conflict": on_conflict},
            headers={"apikey": self.key, "Authorization": f"Bearer {self.key}",
                     "Content-Type": "application/json",
                     "Prefer": "resolution=merge-duplicates,return=minimal"},
            json=rows,
        )
        if r.status_code >= 300:
            raise RuntimeError(f"Supabase upsert into {table} failed: {r.status_code} {r.text[:300]}")
        return len(rows)


def _entity_row(kind: str, row: sqlite3.Row, store: "LocalStore") -> Optional[dict]:
    """Local entity -> Supabase row (None = not mirrored, e.g. brief-level pre-checks)."""
    p = json.loads(row["payload"])
    if kind == "teardown":
        return {"ad_id": p["ad_id"], "hook_type": p["hook"]["type"], "format": p["format"],
                "angles": p["angles"], "model": p["model"], "payload": p}
    if kind == "pattern":
        return {"id": p["id"], "name": p["name"], "steps": p["steps"],
                "own_performance_score": p.get("own_performance_score"), "payload": p}
    if kind == "brief":
        return {"id": p["id"], "site": p["site"], "pattern_id": p["pattern_id"], "status": row["status"] or "draft",
                "fact_keys": sorted({c["fact_key"] for c in p["claims"]}), "missing_facts": p["missing_facts"],
                "payload": p}
    if kind == "creative":
        with closing(store._db()) as db:
            rv = db.execute("select payload from entities where kind='review' and id=?", (p["id"],)).fetchone()
        return {"id": p["id"], "brief_id": p["brief_id"], "aspect_ratio": p["aspect_ratio"], "kind": p["kind"],
                "path": p["path"], "sha256": p["sha256"], "ai_generated": True,
                "review_status": row["status"] or "pending",
                "review_notes": json.loads(rv["payload"])["notes"] if rv else "",
                "cost_usd": round(sum(c["usd"] for c in p.get("cost", [])), 4), "payload": p}
    if kind == "compliance":
        if row["id"].startswith("brief:"):
            return None
        return {"report_id": row["id"], "creative_id": p["creative_id"], "verdict": p["verdict"], "payload": p}
    if kind == "review":
        return {"creative_id": p["creative_id"], "decision": p["decision"], "notes": p["notes"],
                "decided_at": p["at"], "payload": p}
    if kind == "result":
        return {k: p[k] for k in ("creative_id", "platform", "campaign_id", "day", "impressions", "clicks",
                                  "spend_cad", "leads", "conversions")} | {"payload": p}
    return None


# kind -> (table, on_conflict), in foreign-key order
MIRROR = (("teardown", "teardowns", "ad_id"), ("pattern", "patterns", "id"), ("brief", "briefs", "id"),
          ("creative", "creatives", "id"), ("compliance", "compliance_reports", "report_id"),
          ("review", "reviews", "creative_id"), ("result", "results", "creative_id,platform,campaign_id,day"))


def sync(store: LocalStore, mirror: Optional[SupabaseMirror] = None) -> dict:
    """Push every changed ad to Supabase (no-op when not configured)."""
    mirror = mirror or SupabaseMirror()
    if not mirror.configured:
        return {"configured": False, "pushed": 0}
    pending = store.unsynced()
    for i in range(0, len(pending), 200):
        batch = pending[i:i + 200]
        mirror.push(batch)
        store.mark_synced(a.id for a in batch)
    out: dict = {"configured": True, "pushed": len(pending)}
    for kind, table, conflict in MIRROR:
        rows = store.unsynced_rows(kind)
        for i in range(0, len(rows), 200):
            batch = rows[i:i + 200]
            mapped = [m for r in batch if (m := _entity_row(kind, r, store)) is not None]
            mirror.upsert(table, mapped, conflict)
            store.mark_rows_synced(kind, [r["id"] for r in batch])
            out[table] = out.get(table, 0) + len(mapped)
    n = _sync_usage(mirror)
    if n:
        out["llm_usage"] = n
    return out


def _sync_usage(mirror: SupabaseMirror) -> int:
    """Mirror new data/logs/usage.jsonl lines; the line count already pushed lives next to it."""
    import hashlib

    log = settings().logs_dir / "usage.jsonl"
    mark = settings().logs_dir / "usage.synced"
    if not log.is_file():
        return 0
    lines = [l for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
    done = int(mark.read_text() or 0) if mark.is_file() else 0
    new = lines[done:]
    for i in range(0, len(new), 500):
        rows = []
        for raw in new[i:i + 500]:
            u = json.loads(raw)
            rows.append({"entry_key": hashlib.sha1(raw.encode()).hexdigest(), "command": u["command"],
                         "provider": u["provider"], "model": u["model"], "input_tokens": u["input_tokens"],
                         "output_tokens": u["output_tokens"], "cache_read_tokens": u["cache_read_tokens"],
                         "cache_write_tokens": u["cache_write_tokens"], "cost_usd": u["cost_usd"],
                         "units": u.get("units"), "created_at": u["at"]})
        mirror.upsert("llm_usage", rows, "entry_key")
        mark.write_text(str(done + i + len(rows)))
    return len(new)
