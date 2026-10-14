"""`adlab scout`: process drops, run enabled API collectors, regroup, rescore.

One run, in order:
1. sweep data/incoming/ (local drops always come first);
2. official API collectors enabled in sites/<site>/scout.yaml (Meta Ad Library);
3. variant groups recomputed from scratch per site (variants.py);
4. every ad rescored (scoring.py); "new winners" = ads that crossed the winner
   threshold in this run;
5. Supabase mirror sync, when configured.

`schedule()` runs the API refresh weekly with APScheduler (manual drops don't need
a schedule: the watcher or the next `adlab scout` picks them up).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Callable, Optional

from pydantic import BaseModel

from . import scoring, variants
from .collectors import CollectorRun, load_scout_config, meta_ad_library
from .ingest import IngestResult, process_incoming
from .schemas import Ad
from .sites import list_sites
from .store import LocalStore, sync

log = logging.getLogger("adlab.scout")


class Winner(BaseModel):
    ad_id: str
    site: str
    advertiser: str
    score: float
    why: str


class ScoutReport(BaseModel):
    ingested: list[IngestResult] = []
    collectors: list[CollectorRun] = []
    ads_scored: int = 0
    new_winners: list[Winner] = []
    top: list[Winner] = []
    sync: dict = {}


def _winner(ad: Ad) -> Winner:
    assert ad.score is not None
    return Winner(ad_id=ad.id, site=ad.site, advertiser=ad.source.advertiser,
                  score=ad.score.total, why=scoring.explain(ad.score))


def rescore(store: LocalStore, site: Optional[str] = None, today: Optional[date] = None) -> tuple[int, list[Winner], list[Ad]]:
    """Regroup variants and rescore every ad (of one site, or all)."""
    threshold = scoring.weights().winner_threshold
    ads = store.ads(site)
    media = store.media_for(a.id for a in ads)
    groups = variants.group([
        {"id": a.id, "key": f"{a.site}|{a.source.advertiser.strip().lower()}", "text": a.ad_text,
         "dhashes": [m["dhash"] for m in media.get(a.id, []) if m["dhash"]]}
        for a in ads
    ])
    sizes: dict[str, int] = {}
    for g in groups.values():
        sizes[g] = sizes.get(g, 0) + 1
    new_winners, updated = [], []
    for ad in ads:
        before = ad.score.total if ad.score else None
        ad.variant_group = groups[ad.id] if sizes[groups[ad.id]] > 1 else None
        ad.score = scoring.score(ad, group_size=sizes[groups[ad.id]], today=today)
        store.upsert(ad)
        updated.append(ad)
        if ad.score.total >= threshold and (before is None or before < threshold):
            new_winners.append(_winner(ad))
    return len(ads), new_winners, updated


def run(site: Optional[str] = None, store: Optional[LocalStore] = None, today: Optional[date] = None,
        collectors: bool = True, meta: Callable[..., CollectorRun] = meta_ad_library) -> ScoutReport:
    store = store or LocalStore()
    report = ScoutReport(ingested=process_incoming(store, today))
    if collectors:
        for s in [site] if site else list_sites():
            cfg = load_scout_config(s)
            if cfg.meta_ad_library.enabled:
                report.collectors.append(meta(s, cfg, store=store, today=today))
    report.ads_scored, report.new_winners, ads = rescore(store, site, today)
    report.top = [_winner(a) for a in sorted(ads, key=lambda a: -(a.score.total if a.score else 0))[:5]]
    try:
        report.sync = sync(store)
    except Exception as exc:  # noqa: BLE001 - mirror problems never block scouting
        log.warning("supabase sync failed: %s", exc)
        report.sync = {"configured": True, "error": str(exc)}
    return report


def scheduler(blocking: bool = True):
    """Weekly API refresh: Mondays 06:47 Alberta time (off the hour on purpose)."""
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    sched = BlockingScheduler() if blocking else BackgroundScheduler()
    sched.add_job(run, CronTrigger(day_of_week="mon", hour=6, minute=47, timezone="America/Edmonton"),
                  id="weekly_api_refresh", name="AdLab weekly scout (API sources)",
                  replace_existing=True, misfire_grace_time=6 * 3600, coalesce=True)
    return sched
