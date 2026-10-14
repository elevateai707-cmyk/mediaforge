"""Phase 7, Reporter: YOUR results -> per-creative / hook / pattern performance -> re-rank.

Sources (your own accounts only):
* Meta Marketing API Insights: GET /<ver>/act_<account>/insights, level=ad,
  time_increment=1 (needs a token with ads_read: META_ADS_TOKEN, META_AD_ACCOUNT_ID).
* CSV exports from Meta Ads Manager or TikTok Ads Manager (`adlab report --import`).
  TikTok's reporting API isn't wired: export a by-day, by-ad CSV instead.

Rows are matched to creatives by the `[adlab:<creative_id>]` tag `adlab export` put in
the ad name; rows without it are counted as "unmatched" and ignored.

Re-rank: every pattern with >= min_impressions of YOUR ads gets
own_performance_score = 50 x mean(CTR index, CPL index), clipped to 0-100, where an
index compares the pattern with your account average (1.0 = average, so 50 = average).
The Strategist ranks patterns by this score first, so new briefs lean on what converts
for you, not on what competitors run.
"""
from __future__ import annotations

import csv
import json
import os
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Literal, Optional

import httpx
import yaml
from pydantic import BaseModel

from .config import settings
from .schemas import Brief, Creative, Pattern, Result
from .store import LocalStore

TAG = re.compile(r"\[adlab:(cr_[0-9a-f]{6,})\]")
GRAPH = "https://graph.facebook.com"
INSIGHT_FIELDS = "ad_id,ad_name,campaign_id,impressions,clicks,spend,actions,date_start,date_stop,account_currency"


class ReportError(RuntimeError):
    pass


@lru_cache(maxsize=4)
def _cfg(path: str) -> dict:
    p = Path(path)
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.is_file() else {}


def cfg() -> dict:
    return _cfg(str(settings().config_dir / "report.yaml"))


def _num(v) -> float:
    if v in (None, "", "-", "--"):
        return 0.0
    try:
        return float(str(v).replace(",", "").replace("$", "").replace("CA", "").strip())
    except ValueError:
        return 0.0


def _first_action(actions: list[dict], types: list[str]) -> int:
    by = {a.get("action_type"): a.get("value") for a in actions or []}
    for t in types:
        if t in by:
            return int(_num(by[t]))
    return 0


class ImportResult(BaseModel):
    stored: int = 0
    unmatched: int = 0
    unknown_creatives: list[str] = []
    warnings: list[str] = []


def _store_results(store: LocalStore, raw: Iterable[dict], platform: str) -> ImportResult:
    """raw rows: ad_name, day, campaign_id, impressions, clicks, spend, leads, conversions."""
    out = ImportResult()
    agg: dict[tuple[str, str, date], dict] = {}
    known = {r["id"] for r in store.rows("creative")}
    for r in raw:
        m = TAG.search(r.get("ad_name") or "")
        if not m:
            out.unmatched += 1
            continue
        cid = m.group(1)
        if cid not in known:
            if cid not in out.unknown_creatives:
                out.unknown_creatives.append(cid)
            continue
        key = (cid, str(r.get("campaign_id") or "unknown"), r["day"])
        a = agg.setdefault(key, {"impressions": 0, "clicks": 0, "spend": 0.0, "leads": 0, "conversions": 0})
        for k in a:
            a[k] += r.get(k) or 0
    for (cid, camp, day), a in agg.items():
        res = Result(creative_id=cid, platform=platform, campaign_id=camp, day=day,  # type: ignore[arg-type]
                     impressions=int(a["impressions"]), clicks=int(a["clicks"]), spend_cad=round(a["spend"], 2),
                     leads=int(a["leads"]), conversions=int(a["conversions"]))
        brief_id = next((r["ref"] for r in store.rows("creative") if r["id"] == cid), None)
        store.put("result", res, id=f"{platform}:{camp}:{cid}:{day.isoformat()}", ref=cid,
                  site=_site_of(store, brief_id), score=res.spend_cad)
        out.stored += 1
    return out


def _site_of(store: LocalStore, brief_id: Optional[str]) -> Optional[str]:
    b = store.fetch("brief", brief_id, Brief) if brief_id else None
    return b.site if b else None


# --- Meta Insights API ------------------------------------------------------------------------
def meta_request(account_id: str, since: date, until: date, token: str = "***") -> dict:
    ver = cfg().get("meta", {}).get("graph_version", "v25.0")
    return {"method": "GET", "url": f"{GRAPH}/{ver}/act_{account_id.removeprefix('act_')}/insights",
            "params": {"level": "ad", "time_increment": 1, "fields": INSIGHT_FIELDS, "limit": 500,
                       "time_range": json.dumps({"since": since.isoformat(), "until": until.isoformat()}),
                       "access_token": token}}


def pull_meta(store: LocalStore, days: int = 7, http: Optional[httpx.Client] = None,
              today: Optional[date] = None) -> ImportResult:
    token, account = os.environ.get("META_ADS_TOKEN", ""), os.environ.get("META_AD_ACCOUNT_ID", "")
    if not token or not account:
        raise ReportError("set META_ADS_TOKEN (ads_read) and META_AD_ACCOUNT_ID, or import a CSV export")
    today = today or date.today()
    req = meta_request(account, today - timedelta(days=days), today - timedelta(days=1), token)
    http = http or httpx.Client(timeout=60)
    mc = cfg().get("meta", {})
    rows, url, params = [], req["url"], req["params"]
    currencies = set()
    while url:
        r = http.get(url, params=params)
        if r.status_code >= 400:
            err = r.json().get("error", {}) if r.headers.get("content-type", "").startswith("application/json") else {}
            raise ReportError(f"Meta Insights {r.status_code}: {err.get('message') or r.text[:200]}")
        data = r.json()
        for d in data.get("data", []):
            currencies.add(d.get("account_currency"))
            rows.append({"ad_name": d.get("ad_name", ""), "campaign_id": d.get("campaign_id"),
                         "day": date.fromisoformat(d["date_start"]), "impressions": int(_num(d.get("impressions"))),
                         "clicks": int(_num(d.get("clicks"))), "spend": _num(d.get("spend")),
                         "leads": _first_action(d.get("actions"), mc.get("lead_actions", ["lead"])),
                         "conversions": _first_action(d.get("actions"), mc.get("conversion_actions", []))})
        url, params = (data.get("paging") or {}).get("next"), None   # `next` already carries the query
    out = _store_results(store, rows, "meta")
    if currencies - {"CAD", None}:
        out.warnings.append(f"account currency is {', '.join(sorted(c for c in currencies if c))}, "
                            "stored as-is in spend_cad")
    return out


# --- CSV exports ---------------------------------------------------------------------------
def _parse_day(v: str) -> Optional[date]:
    v = (v or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def import_csv(store: LocalStore, path: Path, platform: Literal["meta", "tiktok"]) -> ImportResult:
    cols = cfg().get("csv_columns", {})
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        header = {h.strip().lower(): h for h in reader.fieldnames or []}
        pick = {k: next((header[c] for c in cands if c in header), None) for k, cands in cols.items()}
        need = [k for k in ("ad_name", "day", "impressions", "clicks", "spend") if not pick.get(k)]
        if need:
            raise ReportError(f"{path.name}: can't find column(s) {need}. Columns are: {list(header.values())}. "
                              "Export by day + by ad, or add the names to config/report.yaml csv_columns.")
        rows, bad_days = [], 0
        for r in reader:
            day = _parse_day(r[pick["day"]])
            if day is None:
                bad_days += 1
                continue
            rows.append({"ad_name": r[pick["ad_name"]], "day": day,
                         "campaign_id": r[pick["campaign_id"]] if pick.get("campaign_id") else "csv",
                         **{k: (_num(r[pick[k]]) if pick.get(k) else 0)
                            for k in ("impressions", "clicks", "spend", "leads", "conversions")}})
    out = _store_results(store, rows, platform)
    if bad_days:
        out.warnings.append(f"{bad_days} rows without a readable day (totals rows?) skipped")
    if not pick.get("leads"):
        out.warnings.append("no leads column found: CPL will be blank")
    return out


# --- performance + re-rank ---------------------------------------------------------------------
class Perf(BaseModel):
    key: str
    label: str = ""
    impressions: int = 0
    clicks: int = 0
    spend: float = 0.0
    leads: int = 0
    conversions: int = 0
    creatives: int = 0

    @property
    def ctr(self) -> Optional[float]:
        return self.clicks / self.impressions if self.impressions else None

    @property
    def cpc(self) -> Optional[float]:
        return self.spend / self.clicks if self.clicks else None

    @property
    def cpl(self) -> Optional[float]:
        return self.spend / self.leads if self.leads else None

    def row(self) -> dict:
        return {**self.model_dump(), "ctr": self.ctr, "cpc": self.cpc, "cpl": self.cpl}


class Report(BaseModel):
    since: Optional[date] = None
    total: Perf
    by_pattern: list[dict] = []
    by_hook: list[dict] = []
    by_creative: list[dict] = []
    rescored: dict[str, Optional[float]] = {}


def _add(p: Perf, r: Result) -> None:
    p.impressions += r.impressions
    p.clicks += r.clicks
    p.spend += r.spend_cad
    p.leads += r.leads
    p.conversions += r.conversions


def performance(store: LocalStore, site: Optional[str] = None, days: Optional[int] = None,
                today: Optional[date] = None) -> Report:
    since = (today or date.today()) - timedelta(days=days) if days else None
    results = [r for r in store.list("result", Result, site=site) if not since or r.day >= since]
    creatives = {c.id: c for c in store.list("creative", Creative)}
    briefs = {b.id: b for b in store.list("brief", Brief)}
    patterns = {p.id: p for p in store.list("pattern", Pattern)}
    total = Perf(key="all")
    by_c: dict[str, Perf] = {}
    by_p: dict[str, Perf] = {}
    by_h: dict[str, Perf] = {}
    seen: dict[str, set] = defaultdict(set)
    for r in results:
        _add(total, r)
        c = creatives.get(r.creative_id)
        b = briefs.get(c.brief_id) if c else None
        for bucket, key, label in (
            (by_c, r.creative_id, f"{b.hook.type} {c.aspect_ratio}" if b and c else r.creative_id),
            (by_p, b.pattern_id if b else "?", patterns[b.pattern_id].name if b and b.pattern_id in patterns else "?"),
            (by_h, b.hook.type if b else "?", b.hook.type if b else "?"),
        ):
            p = bucket.setdefault(key, Perf(key=key, label=label))
            _add(p, r)
            if r.creative_id not in seen[f"{id(bucket)}:{key}"]:
                seen[f"{id(bucket)}:{key}"].add(r.creative_id)
                p.creatives += 1

    def ranked(d: dict[str, Perf]) -> list[dict]:
        return [p.row() for p in sorted(d.values(), key=lambda p: (p.cpl is None, p.cpl or 0, -(p.ctr or 0)))]

    return Report(since=since, total=total, by_pattern=ranked(by_p), by_hook=ranked(by_h), by_creative=ranked(by_c))


def pattern_score(p: Perf, total: Perf) -> Optional[float]:
    idx = []
    if p.ctr is not None and total.ctr:
        idx.append(p.ctr / total.ctr)
    if p.cpl is not None and total.cpl:
        idx.append(total.cpl / p.cpl)
    elif p.leads == 0 and total.leads and p.spend > 0:
        idx.append(0.0)              # spent money, no leads, while others got leads
    if not idx:
        return None
    return round(max(0.0, min(100.0, 50 * sum(idx) / len(idx))), 1)


def rerank(store: LocalStore, today: Optional[date] = None) -> dict[str, Optional[float]]:
    """Write own_performance_score onto every pattern with enough of YOUR impressions."""
    rep = performance(store, today=today)
    floor = int(cfg().get("min_impressions", 1000))
    out = {}
    for row in rep.by_pattern:
        pid = row["key"]
        p = store.fetch("pattern", pid, Pattern)
        if p is None:
            continue
        perf = Perf(**{k: row[k] for k in Perf.model_fields})
        score = pattern_score(perf, rep.total) if perf.impressions >= floor else None
        p.own_performance_score = score
        prow = next(r for r in store.rows("pattern") if r["id"] == pid)
        store.put("pattern", p, id=pid, site=prow["site"], score=prow["score"])
        out[pid] = score
    return out
