"""Weekly digest email (Resend): 3 sections, scannable in 30 seconds.

1. Top 5 new hooks   - from teardowns of this week's winning competitor ads (structure only)
2. Top 3 to approve  - creatives waiting in your review queue, best bet first
3. What's converting - your patterns ranked by YOUR CTR / CPL

Send: POST https://api.resend.com/emails, Authorization: Bearer RESEND_API_KEY,
body {from, to, subject, html, text}. Needs RESEND_API_KEY, DIGEST_FROM (a sender on a
domain you verified in Resend) and DIGEST_TO. Without them `adlab digest` just prints it.
"""
from __future__ import annotations

import html
import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from pydantic import BaseModel

from .reporter import performance
from .review import queue
from .schemas import Teardown
from .store import LocalStore

RESEND_URL = "https://api.resend.com/emails"


class Digest(BaseModel):
    subject: str
    tldr: list[str]
    hooks: list[dict]
    to_approve: list[dict]
    converting: list[dict]
    text: str
    html: str


def _money(v: Optional[float]) -> str:
    return f"${v:,.2f}" if v is not None else "n/a"


def _pct(v: Optional[float]) -> str:
    return f"{v * 100:.2f}%" if v is not None else "n/a"


def build(store: Optional[LocalStore] = None, days: int = 7, today: Optional[date] = None,
          dashboard_url: str = "") -> Digest:
    store = store or LocalStore()
    today = today or date.today()
    since = (datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc) - timedelta(days=days))
    since_sql = since.strftime("%Y-%m-%d %H:%M:%S")
    ads = {a.id: a for a in store.ads()}
    hooks = []
    for r in store.rows("teardown", since=since_sql):
        t = Teardown.model_validate_json(r["payload"])
        ad = ads.get(t.ad_id)
        hooks.append({"type": t.hook.type, "visual": t.hook.visual, "why": t.why_it_works,
                      "advertiser": ad.source.advertiser if ad else "?", "site": ad.site if ad else "?",
                      "score": ad.score.total if ad and ad.score else 0})
    hooks = sorted(hooks, key=lambda h: -h["score"])[:5]
    approve = []
    for it in queue(store, statuses=("pending",))[:3]:
        b = it.brief
        approve.append({"id": it.creative.id, "ratio": it.creative.aspect_ratio,
                        "site": b.site if b else "?", "hook": b.hook.type if b else "?",
                        "script": b.script if b else "", "pattern": it.pattern.name if it.pattern else "?",
                        "compliance": it.compliance.verdict if it.compliance else "?"})
    rep = performance(store, days=days * 4, today=today)
    converting = [p for p in rep.by_pattern if p["impressions"]][:3]
    waiting = len(queue(store, statuses=("pending",)))
    tldr = [
        f"{len(hooks)} new winning hooks scouted this week" if hooks else "no new teardowns this week",
        f"{waiting} ads waiting for your review" + (f" (top: {approve[0]['hook']} for {approve[0]['site']})"
                                                     if approve else ""),
        (f"best pattern: {converting[0]['label']} (CPL {_money(converting[0]['cpl'])}, CTR {_pct(converting[0]['ctr'])})"
         if converting else "no results yet: import your Ads Manager numbers with `adlab report`"),
    ]
    subject = f"AdLab weekly: {waiting} to review, {len(hooks)} new hooks"
    text = "\n".join(
        ["TL;DR", *(f"- {l}" for l in tldr), "", "TOP 5 NEW HOOKS",
         *(f"{i}. {h['type']} ({h['advertiser']}, {h['site']}, score {h['score']:g}): {h['why']}"
           for i, h in enumerate(hooks, 1)), "", "TOP 3 TO APPROVE",
         *(f"{i}. [{a['compliance']}] {a['site']} {a['hook']} {a['ratio']}: \"{a['script']}\" ({a['id']})"
           for i, a in enumerate(approve, 1)), "", "WHAT'S CONVERTING (last 4 weeks)",
         *(f"{i}. {p['label']}: CTR {_pct(p['ctr'])}, CPL {_money(p['cpl'])}, {p['leads']} leads, "
           f"spend {_money(p['spend'])}" for i, p in enumerate(converting, 1)),
         *([f"", f"Review: {dashboard_url}"] if dashboard_url else [])])
    e = html.escape
    def li(items: list[str], empty: str) -> str:
        return "".join(f"<li style='margin:0 0 6px'>{x}</li>" for x in items) or f"<li>{empty}</li>"

    hook_li = [f"<b>{e(h['type'])}</b> · {e(h['advertiser'])} ({e(h['site'])}, {h['score']:g}): {e(h['why'])}"
               for h in hooks]
    approve_li = [f"<b>[{e(a['compliance'])}]</b> {e(a['site'])} · {e(a['hook'])} · {e(a['ratio'])}"
                  f"<br><i>{e(a['script'])}</i>" for a in approve]
    conv_li = [f"<b>{e(p['label'])}</b>: CTR {_pct(p['ctr'])} · CPL {_money(p['cpl'])} · {p['leads']} leads"
               for p in converting]
    link = f"<p><a href='{e(dashboard_url)}'>Open the review queue →</a></p>" if dashboard_url else ""
    body = (
        "<div style='font:15px/1.45 -apple-system,Segoe UI,Arial,sans-serif;max-width:600px;color:#111'>"
        "<div style='background:#f4f4f5;border-radius:10px;padding:12px 16px;margin-bottom:16px'><b>TL;DR</b>"
        f"<ul style='margin:6px 0 0;padding-left:18px'>{li([e(l) for l in tldr], '')}</ul></div>"
        f"<h3>🎣 Top 5 new hooks</h3><ol>{li(hook_li, 'none this week')}</ol>"
        f"<h3>✅ Top 3 to approve</h3><ol>{li(approve_li, 'queue is empty')}</ol>"
        f"<h3>📈 What's converting</h3><ol>{li(conv_li, 'no results yet')}</ol>"
        f"{link}</div>")
    return Digest(subject=subject, tldr=tldr, hooks=hooks, to_approve=approve, converting=converting,
                  text=text, html=body)


def resend_payload(d: Digest) -> dict:
    to = [x.strip() for x in os.environ.get("DIGEST_TO", "").split(",") if x.strip()]
    return {"from": os.environ.get("DIGEST_FROM", ""), "to": to, "subject": d.subject, "html": d.html, "text": d.text}


def send(d: Digest, http: Optional[httpx.Client] = None) -> str:
    key = os.environ.get("RESEND_API_KEY", "")
    payload = resend_payload(d)
    if not key or not payload["from"] or not payload["to"]:
        raise RuntimeError("set RESEND_API_KEY, DIGEST_FROM (verified sender) and DIGEST_TO to send the digest")
    r = (http or httpx.Client(timeout=30)).post(RESEND_URL, json=payload,
                                                headers={"Authorization": f"Bearer {key}"})
    if r.status_code >= 300:
        raise RuntimeError(f"Resend {r.status_code}: {r.text[:300]}")
    return r.json().get("id", "")
