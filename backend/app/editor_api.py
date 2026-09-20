"""Project API. All changes require revision checks; preview and export share snapshots."""

import asyncio

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from . import models
from .db import SessionLocal
from .edits import project, render_v2, subtitles
from .jobs import jobs

router = APIRouter(prefix="/api/editor")


@router.get("/schema")
def schema():
    return project.Project.model_json_schema()


@router.get("/capabilities")
def capabilities():
    return render_v2.capabilities()


@router.get("/{plan_id}")
def get(plan_id: str):
    try:
        return project.get_project(plan_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.put("/{plan_id}")
def save(plan_id: str, p: project.Project):
    try:
        return project.save_project(plan_id, p)
    except ValueError as e:
        raise HTTPException(409, str(e))


class Revision(BaseModel):
    revision: int


@router.post("/{plan_id}/approve")
def approve(plan_id: str, r: Revision):
    try:
        return project.approve(plan_id, r.revision)
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.post("/{plan_id}/render")
async def render(plan_id: str, preview: bool = False):
    try:
        doc, paths = project.snapshot(plan_id, approved=not preview)
    except ValueError as e:
        raise HTTPException(409, str(e))

    async def runner(progress, flag):
        return await asyncio.to_thread(
            render_v2.render_snapshot, doc, paths, progress, flag, preview
        )

    return {
        "job_id": jobs.run(
            "preview" if preview else "render",
            runner,
            meta={"snapshot": doc, "paths": paths, "plan_id": plan_id},
        )
    }


@router.get("/{plan_id}/subtitles/{kind}")
def export_subtitles(plan_id: str, kind: str):
    if kind not in ("srt", "vtt", "ass"):
        raise HTTPException(422, "Choose SRT, VTT or ASS")
    p = project.Project.model_validate(get(plan_id)["project"])
    return Response(
        subtitles.sidecar(p, kind),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="captions.{kind}"'},
    )


class Transcribe(BaseModel):
    clip_uid: str
    profile: str = "balanced"
    language: str | None = None
    revision: int


@router.post("/{plan_id}/transcribe")
async def transcribe(plan_id: str, r: Transcribe):
    p = project.Project.model_validate(get(plan_id)["project"])
    c = next((c for c in p.clips if c.uid == r.clip_uid), None)
    if not c:
        raise HTTPException(404, "Clip not found")
    if p.revision != r.revision:
        raise HTTPException(409, "Save current changes first")
    with SessionLocal() as db:
        path = db.get(models.Asset, c.asset_id).path

    async def runner(progress, flag):
        from .ai.whisper import transcribe_profile

        progress(0.05, "Transcribing locally; model weights may download on first use")
        result = await asyncio.to_thread(
            transcribe_profile, path, r.profile, r.language
        )
        if flag.cancelled:
            raise asyncio.CancelledError()
        words = [
            project.Word(
                start=w["start"], end=w["end"], text=w.get("word", w.get("text", ""))
            )
            for w in result.get("words", [])
            if w["end"] > w["start"]
        ]
        cues = (
            subtitles.phrase_cues(words)
            if words
            else [project.Cue(**x) for x in result["segments"] if x["end"] > x["start"]]
        )
        return {
            "cues": [c.model_dump() for c in cues],
            "clip_uid": r.clip_uid,
            "revision": r.revision,
            "notice": result.get("notice"),
        }

    return {"job_id": jobs.run("transcription", runner, meta={"plan_id": plan_id})}


@router.post("/jobs/{job_id}/resume")
async def resume(job_id: str):
    old = jobs.get_job(job_id)
    if (
        not old
        or old["kind"] not in ("render", "preview")
        or old["status"] not in ("interrupted", "error", "cancelled")
    ):
        raise HTTPException(
            409, "Only an interrupted, failed or cancelled render can resume"
        )
    meta = old.get("meta") or {}
    if "snapshot" not in meta or "paths" not in meta:
        raise HTTPException(
            409, "This legacy job has no snapshot; render from the project instead"
        )

    async def runner(progress, flag):
        return await asyncio.to_thread(
            render_v2.render_snapshot,
            meta["snapshot"],
            meta["paths"],
            progress,
            flag,
            old["kind"] == "preview",
        )

    return {"job_id": jobs.run(old["kind"], runner, meta=meta)}
