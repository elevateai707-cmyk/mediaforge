"""Review → explicit generation; durable request identity and restart-safe polling."""

import asyncio
import hashlib
import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import update

from .. import config, models
from ..db import SessionLocal
from . import comfy, elevenlabs, settings

router = APIRouter(prefix="/api/providers")
LOCK = threading.RLock()


class Settings(BaseModel):
    comfy_key: str | None = Field(default=None, max_length=1000)
    elevenlabs_key: str | None = Field(default=None, max_length=1000)
    workflows: dict = Field(default_factory=dict)
    max_daily_jobs: int = Field(default=10, ge=0, le=1000)
    max_narration_chars: int = Field(default=5000, ge=100, le=20000)


@router.get("/settings")
def get_settings():
    return {
        **settings.configuration(),
        "configured": {p: bool(settings.key(p)) for p in ("comfy", "elevenlabs")},
    }


@router.put("/settings")
def put_settings(body: Settings):
    try:
        for wf in body.workflows.values():
            if not isinstance(wf, dict):
                raise ValueError("Each named workflow must be an object")
            comfy.validate(wf.get("workflow"), wf.get("mappings", {}))
    except ValueError as e:
        raise HTTPException(422, str(e))
    for provider, value in [
        ("comfy", body.comfy_key),
        ("elevenlabs", body.elevenlabs_key),
    ]:
        if value is not None:
            settings.set_key(provider, value)
    settings.save_configuration(
        body.model_dump(exclude={"comfy_key", "elevenlabs_key"})
    )
    return get_settings()


def public_error(exc):
    # Never return raw provider errors: they can contain requests, keys, or workflow text.
    status = getattr(getattr(exc, "response", None), "status_code", None) or getattr(
        exc, "http_status", None
    )
    if status in (401, 403):
        return "Provider rejected credentials or account access. Check your key and subscription."
    if status in (402, 429):
        return "Provider credit or account limit reached. Check its dashboard before another request."
    if status == 404:
        return "Provider resource unavailable or expired."
    if isinstance(exc, ValueError):
        message = str(exc)[:300]
        for p in ("comfy", "elevenlabs"):
            token = settings.key(p)
            if token:
                message = message.replace(token, "[redacted]")
        return message
    return "Provider request failed or timed out. Submission may have been accepted; it will not be resubmitted automatically."


@router.post("/{provider}/test")
def test(provider: str):
    try:
        if provider == "comfy":
            return {
                "connected": True,
                "available_nodes": len(comfy.node_info()),
                "usage": None,
                "notice": "Account balance and price unavailable; check Comfy Cloud dashboard.",
            }
        if provider == "elevenlabs":
            return {"connected": True, **elevenlabs.discovery()}
        raise ValueError("Unknown provider")
    except Exception as e:
        raise HTTPException(400, public_error(e))


class Review(BaseModel):
    provider: str
    workflow: str | None = None
    inputs: dict = Field(default_factory=dict)
    script: str = Field(default="", max_length=20000)
    voice: str = Field(default="", max_length=200)
    model: str = Field(default="", max_length=200)
    settings: dict = Field(default_factory=dict)
    preview: bool = False
    operation: str = "narration"
    asset_id: int | None = None
    language: str | None = None


def job_view(row):
    return {
        "id": row.id,
        "provider": row.provider,
        "status": row.status,
        "remote_id": row.remote_id,
        "error": row.error,
        "request": json.loads(row.request_json),
        "result": json.loads(row.result_json) if row.result_json else None,
    }


@router.post("/review")
def review(body: Review):
    cfg = settings.configuration()
    uploads = []
    paths = {}
    if body.provider not in ("comfy", "elevenlabs"):
        raise HTTPException(422, "Unknown provider")
    if not settings.key(body.provider):
        raise HTTPException(
            409, "Configure the provider key first. Local editing remains available."
        )
    request = body.model_dump()
    try:
        if body.provider == "comfy":
            wf = cfg.get("workflows", {}).get(body.workflow)
            if not wf:
                raise ValueError("Choose a configured, Cloud-compatible workflow")
            comfy.validate(wf["workflow"], wf.get("mappings", {}), comfy.node_info())
            if set(body.inputs) != set(wf.get("mappings", {})):
                raise ValueError("Supply exactly the configured workflow inputs")
            with SessionLocal() as db:
                for name, m in wf.get("mappings", {}).items():
                    value = body.inputs[name]
                    if m["type"] == "asset":
                        a = db.get(models.Asset, int(value))
                        if not a:
                            raise ValueError("Unknown selected asset")
                        path = Path(a.path)
                        if not path.is_file() or path.stat().st_size > 100_000_000:
                            raise ValueError(
                                "Choose an existing source asset smaller than 100 MB"
                            )
                        paths[a.id] = str(path)
                        uploads.append(
                            {
                                "asset_id": a.id,
                                "filename": path.name,
                                "bytes": path.stat().st_size,
                                "hash": a.hash,
                            }
                        )
                    elif not isinstance(value, str) or len(value) > 20000:
                        raise ValueError("Text input is invalid")
            request.update(
                workflow=wf["workflow"],
                workflow_name=body.workflow,
                mappings=wf.get("mappings", {}),
                paths=paths,
            )
        elif body.operation == "transcription":
            if body.model not in ("scribe_v1", "scribe_v2"):
                raise ValueError(
                    "Select a supported Scribe model; account access is checked by ElevenLabs"
                )
            with SessionLocal() as db:
                a = db.get(models.Asset, body.asset_id)
                if not a or a.kind != "video":
                    raise ValueError("Choose one video with audio")
                path = Path(a.path)
                if not path.is_file() or path.stat().st_size > 100_000_000:
                    raise ValueError("Choose a source smaller than 100 MB")
                from ..ai.whisper import has_audio

                if not has_audio(str(path)):
                    raise ValueError(
                        "Selected clip has no audio; nothing will be uploaded"
                    )
                paths[a.id] = str(path)
                uploads.append(
                    {
                        "asset_id": a.id,
                        "filename": path.name,
                        "bytes": path.stat().st_size,
                        "hash": a.hash,
                    }
                )
            request["paths"] = paths
        else:
            limit = cfg.get("max_narration_chars", 5000)
            if not body.script.strip() or len(body.script) > limit:
                raise ValueError(f"Enter a script up to {limit} characters")
            if not body.voice or not body.model:
                raise ValueError("Select a voice and model from your account")
            if body.preview and len(body.script) > 250:
                raise ValueError(
                    "Short previews are limited to 250 characters; shorten the preview script"
                )
            allowed = {
                "stability": (0, 1),
                "similarity_boost": (0, 1),
                "style": (0, 1),
                "speed": (0.7, 1.2),
                "use_speaker_boost": (0, 1),
            }
            for k, v in body.settings.items():
                if (
                    k not in allowed
                    or not isinstance(v, (int, float))
                    or not allowed[k][0] <= v <= allowed[k][1]
                ):
                    raise ValueError("Unsupported voice setting")
        request["uploads"] = uploads
        digest = hashlib.sha256(
            json.dumps(
                {k: v for k, v in request.items() if k != "preview"}, sort_keys=True
            ).encode()
        ).hexdigest()
        with LOCK, SessionLocal() as db:
            row = db.query(models.ProviderJob).filter_by(cache_key=digest).first()
            if not row:
                row = models.ProviderJob(
                    id=str(uuid.uuid4()),
                    provider=body.provider,
                    cache_key=digest,
                    status="review",
                    request_json=json.dumps(request),
                )
                db.add(row)
                db.commit()
            result = job_view(row)
        return {
            **result,
            "billable": True,
            "price_estimate": None,
            "notice": "Billable generation; amount unavailable. Only the listed selected files will upload. Replay of a completed result uses the local cache.",
        }
    except (ValueError, TypeError) as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        raise HTTPException(400, public_error(e))


def poll_one(job_id):
    with SessionLocal() as db:
        row = db.get(models.ProviderJob, job_id)
        if (
            not row
            or row.provider != "comfy"
            or not row.remote_id
            or row.status in ("succeeded", "failed", "canceled", "expired")
        ):
            return
        directory = config.EXPORTS_DIR / "generated" / row.id
        directory.mkdir(parents=True, exist_ok=True)
        try:
            result = comfy.poll(row.remote_id, directory)
            row.status = result["status"]
            row.result_json = json.dumps(result)
            row.error = None
        except Exception as e:
            row.error = public_error(e)
        db.commit()


def generate_one(job_id):
    with SessionLocal() as db:
        row = db.get(models.ProviderJob, job_id)
        req = json.loads(row.request_json)
        directory = config.EXPORTS_DIR / "generated" / row.id
        directory.mkdir(parents=True, exist_ok=True)
        try:
            if row.provider == "comfy":
                row.remote_id = comfy.submit(
                    req, {int(k): v for k, v in req["paths"].items()}, row.id
                )
                row.status = "queued"
                db.commit()
            else:
                result = (
                    elevenlabs.transcribe(req, directory)
                    if req.get("operation") == "transcription"
                    else elevenlabs.generate(req, directory)
                )
                row.result_json = json.dumps(result)
                row.status = "succeeded"
                db.commit()
        except Exception as e:
            status = getattr(
                getattr(e, "response", None), "status_code", None
            ) or getattr(e, "http_status", None)
            definite = status in (401, 402, 403, 429) or (
                status == 422 and getattr(e, "code", "") != "idempotency_key_reuse"
            )
            row.status = "rejected" if definite else "unknown"
            row.error = public_error(e)
            db.commit()


class Confirm(BaseModel):
    confirmed: bool = False


@router.post("/jobs/{job_id}/generate")
async def generate(job_id: str, body: Confirm):
    if not body.confirmed:
        raise HTTPException(422, "Explicit generation confirmation required")
    with LOCK, SessionLocal() as db:
        row = db.get(models.ProviderJob, job_id)
        if not row:
            raise HTTPException(404, "Job not found")
        if row.status not in ("review", "rejected"):
            return job_view(row)
        if not settings.key(row.provider):
            raise HTTPException(409, "Provider key missing")
        limit = settings.configuration().get("max_daily_jobs", 10)
        since = datetime.now(timezone.utc) - timedelta(days=1)
        count = (
            db.query(models.ProviderJob)
            .filter(
                models.ProviderJob.status != "review",
                models.ProviderJob.created_at >= since,
            )
            .count()
        )
        if count >= limit:
            raise HTTPException(
                429,
                "Local daily generation count limit reached. This is not a monetary spending ceiling.",
            )
        # Claim in SQLite before any paid request; concurrent callers cannot bill twice.
        changed = db.execute(
            update(models.ProviderJob)
            .where(
                models.ProviderJob.id == job_id,
                models.ProviderJob.status.in_(["review", "rejected"]),
            )
            .values(status="submitting")
        )
        db.commit()
        if changed.rowcount:
            asyncio.create_task(asyncio.to_thread(generate_one, job_id))
    return {"id": job_id, "status": "submitting"}


@router.get("/jobs")
def list_jobs():
    with SessionLocal() as db:
        return [
            job_view(r)
            for r in db.query(models.ProviderJob)
            .order_by(models.ProviderJob.created_at.desc())
            .limit(100)
        ]


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    with SessionLocal() as db:
        r = db.get(models.ProviderJob, job_id)
        if not r:
            raise HTTPException(404, "Job not found")
        return job_view(r)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    with SessionLocal() as db:
        row = db.get(models.ProviderJob, job_id)
        if not row:
            raise HTTPException(404, "Job not found")
        if row.status == "review":
            row.status = "canceled"
        elif row.provider == "comfy" and row.remote_id:
            try:
                row.status = comfy.cancel(row.remote_id)
            except Exception as e:
                raise HTTPException(400, public_error(e))
        elif row.status not in ("succeeded", "failed", "canceled", "expired"):
            raise HTTPException(
                409,
                "Submission is in flight or uncertain. Provider cancellation is unavailable; do not regenerate.",
            )
        db.commit()
        return job_view(row)


class Attach(BaseModel):
    remote_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,100}$")


@router.post("/jobs/{job_id}/attach")
def attach(job_id: str, body: Attach):
    with SessionLocal() as db:
        row = db.get(models.ProviderJob, job_id)
        if not row or row.provider != "comfy" or row.status != "unknown":
            raise HTTPException(409, "Only an uncertain Comfy submission can be linked")
        with comfy.client() as c:
            c.jobs.get(body.remote_id)
        row.remote_id = body.remote_id
        row.status = "queued"
        db.commit()
    return get_job(job_id)


@router.post("/jobs/{job_id}/import/{index}")
def import_asset(job_id: str, index: int):

    with SessionLocal() as db:
        row = db.get(models.ProviderJob, job_id)
        if not row or row.status != "succeeded":
            raise HTTPException(409, "Wait for a completed asset")
        paths = json.loads(row.result_json).get("assets", [])
        if index < 0 or index >= len(paths):
            raise HTTPException(404, "Output not found")
        path = paths[index]
    # Caller adds the imported asset to its project; never alter originals.
    from ..ingest import scanner

    scanner.scan_paths([path])
    with SessionLocal() as db:
        asset = db.query(models.Asset).filter_by(path=path).first()
        if not asset:
            raise HTTPException(
                422, "This output is audio; add it as voice-over instead"
            )
        return {"asset_id": asset.id, "duration": asset.duration or 5}


async def monitor():
    with SessionLocal() as db:
        db.query(models.ProviderJob).filter_by(status="submitting").update(
            {
                "status": "unknown",
                "error": "Interrupted during submission. Do not regenerate; check provider history and attach the job ID.",
            }
        )
        db.commit()
    while True:
        with SessionLocal() as db:
            ids = [
                r.id
                for r in db.query(models.ProviderJob).filter(
                    models.ProviderJob.remote_id.isnot(None),
                    models.ProviderJob.status.notin_(
                        ["succeeded", "failed", "canceled", "expired"]
                    ),
                )
            ]
        for job_id in ids:
            await asyncio.to_thread(poll_one, job_id)
        await asyncio.sleep(5)
