"""Local HTTP API for the dashboard (`adlab serve`, default 127.0.0.1:8430).

Read-mostly. The only writes are your review decisions and export, the same as the
CLI. Nothing here uploads to an ad account or spends money. Bound to localhost by
default; set ADLAB_API_TOKEN to require `Authorization: Bearer <token>` (do that
before binding it to your LAN).

    GET  /api/home                      the 3 home cards
    GET  /api/queue?site=&status=       review queue (status: pending | edit | approved | killed | rejected | revised)
    GET  /api/creatives/{id}            one creative + brief + compliance + teardowns it learned from
    GET  /api/creatives/{id}/video      the file (only files inside data/batches/)
    POST /api/creatives/{id}/decision   {"decision": "approve"|"edit"|"kill", "notes": ""}
    POST /api/export                    {"platforms": ["meta","tiktok"]} -> files + upload sheets
    GET  /api/report?days=28            your results by pattern / hook / creative
    GET  /api/patterns                  patterns, your results first
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import digest, export as export_mod, reporter, review
from .config import settings
from .store import LocalStore
from .strategist import ranked_patterns


def _auth(authorization: Optional[str] = Header(default=None), token: Optional[str] = Query(default=None)) -> None:
    """Bearer header, or ?token= (a <video> tag can't send headers)."""
    want = os.environ.get("ADLAB_API_TOKEN", "")
    if want and not (secrets.compare_digest(authorization or "", f"Bearer {want}")
                     or secrets.compare_digest(token or "", want)):
        raise HTTPException(401, "missing or wrong ADLAB_API_TOKEN")


app = FastAPI(title="AdLab", version="0.1.0", dependencies=[Depends(_auth)])
app.add_middleware(CORSMiddleware,
                   allow_origins=[o.strip() for o in os.environ.get(
                       "ADLAB_DASHBOARD_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")],
                   allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])


def store() -> LocalStore:
    return LocalStore()


@app.get("/api/home")
def home() -> dict:
    s = store()
    d = digest.build(s)
    return {"tldr": d.tldr, "to_review": {"count": review.counts(s).get("pending", 0), "top": d.to_approve},
            "hooks": d.hooks, "converting": d.converting}


@app.get("/api/queue")
def get_queue(site: Optional[str] = None, status: str = "pending") -> list[dict]:
    return [_summary(i) for i in review.queue(store(), site, statuses=tuple(status.split(",")))]


def _summary(i: review.ReviewItem) -> dict:
    return {"id": i.creative.id, "status": i.status, "ratio": i.creative.aspect_ratio,
            "site": i.brief.site if i.brief else None, "hook": i.brief.hook.type if i.brief else None,
            "script": i.brief.script if i.brief else "", "pattern": i.pattern.name if i.pattern else None,
            "verdict": i.compliance.verdict if i.compliance else None, "rank": i.rank}


@app.get("/api/creatives/{cid}")
def get_creative(cid: str) -> dict:
    it = review.item(store(), cid)
    if it is None:
        raise HTTPException(404, "no such creative")
    return it.model_dump(mode="json")


@app.get("/api/creatives/{cid}/video")
def video(cid: str) -> FileResponse:
    it = review.item(store(), cid)
    if it is None:
        raise HTTPException(404, "no such creative")
    path = Path(it.creative.path).resolve()
    if not path.is_relative_to(settings().batches_dir.resolve()) or not path.is_file():
        raise HTTPException(404, "file not available")
    return FileResponse(path, media_type="video/mp4" if path.suffix == ".mp4" else None)


class DecisionIn(BaseModel):
    decision: Literal["approve", "edit", "kill"]
    notes: str = ""


@app.post("/api/creatives/{cid}/decision")
def decision(cid: str, body: DecisionIn) -> dict:
    try:
        d = review.decide(store(), cid, body.decision, body.notes)
    except review.ReviewError as exc:
        raise HTTPException(409, str(exc))
    return d.model_dump(mode="json")


class ExportIn(BaseModel):
    platforms: list[Literal["meta", "tiktok"]] = ["meta", "tiktok"]
    site: Optional[str] = None


@app.post("/api/export")
def do_export(body: ExportIn) -> dict:
    return export_mod.export(store(), tuple(body.platforms), body.site).model_dump()


@app.get("/api/report")
def report(days: int = 28, site: Optional[str] = None) -> dict:
    return reporter.performance(store(), site, days).model_dump(mode="json")


@app.get("/api/patterns")
def patterns(site: Optional[str] = None) -> list[dict]:
    return [{**p.model_dump(mode="json"), "evidence": e} for p, e in ranked_patterns(store(), site)]
