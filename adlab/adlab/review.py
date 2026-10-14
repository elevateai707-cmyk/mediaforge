"""Phase 7, the human gate: every creative waits here until YOU approve, edit or kill it.

Nothing is uploaded or paid for by AdLab. "approve" only marks a creative ready for
`adlab export`, which writes upload-ready files + a sheet for you to upload by hand.
A creative the Compliance Officer REJECTed can't be approved (fix the brief instead).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .compliance import policy
from .schemas import Ad, Brief, ComplianceReport, Creative, Pattern, Teardown
from .store import LocalStore

Decision = Literal["approve", "edit", "kill"]
STATUS = {"approve": "approved", "edit": "edit", "kill": "killed"}
OPEN = ("pending", "edit")


class ReviewDecision(BaseModel):
    creative_id: str
    decision: Decision
    notes: str = ""
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class LearnedFrom(BaseModel):
    ad_id: str
    advertiser: str
    platform: str
    url: Optional[str] = None
    score: Optional[float] = None
    teardown: Optional[Teardown] = None


class ReviewItem(BaseModel):
    creative: Creative
    status: str
    brief: Optional[Brief] = None
    pattern: Optional[Pattern] = None
    compliance: Optional[ComplianceReport] = None
    learned_from: list[LearnedFrom] = []
    decision: Optional[ReviewDecision] = None
    manual_checks: list[str] = []
    rank: float = 0.0


def item(store: LocalStore, creative_id: str) -> Optional[ReviewItem]:
    rows = [r for r in store.rows("creative") if r["id"] == creative_id]
    if not rows:
        return None
    c = Creative.model_validate_json(rows[0]["payload"])
    b = store.fetch("brief", c.brief_id, Brief)
    p = store.fetch("pattern", b.pattern_id, Pattern) if b else None
    learned = []
    for ad_id in c.source_ad_ids:
        ad: Optional[Ad] = store.get(ad_id)
        if ad:
            learned.append(LearnedFrom(ad_id=ad.id, advertiser=ad.source.advertiser, platform=ad.source.platform,
                                       url=str(ad.source.url) if ad.source.url else None,
                                       score=ad.score.total if ad.score else None,
                                       teardown=store.fetch("teardown", ad.id, Teardown)))
    comp = store.fetch("compliance", c.id, ComplianceReport)
    rank = float(p.own_performance_score or 0) if p else 0.0          # your results first (0-100)
    rank += {"PASS": 3, "FIX": 1}.get(comp.verdict if comp else "", 0)
    rank += max((l.score or 0) for l in learned) / 100 if learned else 0
    return ReviewItem(creative=c, status=rows[0]["status"] or "pending", brief=b, pattern=p, compliance=comp,
                      learned_from=learned, decision=store.fetch("review", c.id, ReviewDecision),
                      manual_checks=policy().manual_checks, rank=round(rank, 3))


def queue(store: LocalStore, site: Optional[str] = None, statuses: tuple[str, ...] = OPEN) -> list[ReviewItem]:
    """Open creatives, best bet first (your converting patterns, then PASS, then source strength)."""
    out = []
    for r in store.rows("creative", site=site):
        if (r["status"] or "pending") in statuses:
            it = item(store, r["id"])
            if it:
                out.append(it)
    return sorted(out, key=lambda i: (-i.rank, i.creative.brief_id, i.creative.aspect_ratio))


class ReviewError(ValueError):
    pass


def decide(store: LocalStore, creative_id: str, decision: Decision, notes: str = "") -> ReviewDecision:
    it = item(store, creative_id)
    if it is None:
        raise ReviewError(f"no creative {creative_id!r}")
    if decision == "approve":
        if it.status == "rejected" or (it.compliance and it.compliance.verdict == "REJECT"):
            raise ReviewError("compliance REJECTed this creative: it can't be approved (kill it or fix the brief)")
        if not Path(it.creative.path).is_file():
            raise ReviewError(f"file missing: {it.creative.path}")
    if decision == "edit" and not notes.strip():
        raise ReviewError("say what to change (notes are required for edit)")
    d = ReviewDecision(creative_id=creative_id, decision=decision, notes=notes.strip())
    store.put("review", d, id=creative_id, site=it.brief.site if it.brief else None, ref=it.creative.brief_id,
              status=STATUS[decision])
    store.set_status("creative", creative_id, STATUS[decision])
    return d


def counts(store: LocalStore, site: Optional[str] = None) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in store.rows("creative", site=site):
        s = r["status"] or "pending"
        out[s] = out.get(s, 0) + 1
    return out
