"""Approval-gated removable-media import API."""
from __future__ import annotations

import asyncio
from fastapi import APIRouter, HTTPException

from .ingest.removable import removable_imports, RemovableImportError
from .jobs import jobs

router = APIRouter(tags=["import"])


@router.get("/api/import/pending")
def pending_imports():
    return {"pending": removable_imports.poll()}


@router.post("/api/import/pending/{import_id}/dismiss")
def dismiss_import(import_id: str):
    if not removable_imports.dismiss(import_id):
        raise HTTPException(status_code=404, detail="pending import not found")
    return {"ok": True}


@router.post("/api/import/pending/{import_id}/approve")
async def approve_import(import_id: str):
    try:
        plan = removable_imports.reserve(import_id)
    except RemovableImportError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    async def run(progress, flag):
        await asyncio.get_running_loop().run_in_executor(
            None, lambda: removable_imports.copy(plan, progress, lambda: flag.cancelled))
    return {"job_id": jobs.run("import", run), "kind": "removable_import",
            "source": plan["source"], "files": len(plan["manifest"]), "bytes": plan["bytes"]}
