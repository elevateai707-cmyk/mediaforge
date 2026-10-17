"""Asset transcript caption preview and download endpoints."""
from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from . import config, models
from .db import get_db
from .subtitles import build_document, serialize

router = APIRouter(tags=["captions"])


def _sidecar_segments(asset: models.Asset) -> tuple[list[dict], str | None, str | None] | None:
    """Return verified sidecar data, or a limitation when it is stale/broken."""
    asset_id = asset.id
    path = config.DATA_DIR / "transcripts" / f"{asset_id}.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None, "Transcript sidecar was unreadable; using stored transcript segments."
    segments = payload.get("segments") if isinstance(payload, dict) else None
    if not isinstance(segments, list) or payload.get("asset_hash") != asset.hash:
        return None, None, "Transcript sidecar did not match this asset; using stored transcript segments."
    stored = sorted(asset.transcript, key=lambda item: item.start)
    if stored:
        if len(stored) != len(segments) or any(
            not isinstance(item, dict)
            or item.get("text") != segment.text
            or item.get("start") != segment.start
            or item.get("end") != segment.end
            for item, segment in zip(segments, stored)
        ):
            return None, None, "Transcript sidecar differed from stored transcript segments; using stored transcript segments."
    return segments, payload.get("timing_source") if isinstance(payload.get("timing_source"), str) else None, None


def _document(asset_id: int, style: str, db: Session) -> dict:
    asset = db.get(models.Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="asset not found")
    sidecar = _sidecar_segments(asset)
    sidecar_limitation = None
    if sidecar is not None and sidecar[0] is not None:
        segments, source, sidecar_limitation = sidecar
    else:
        if sidecar is not None:
            sidecar_limitation = sidecar[2]
        segments = [
            {"start": segment.start, "end": segment.end, "text": segment.text}
            for segment in sorted(asset.transcript, key=lambda item: item.start)
        ]
        source = "historical_transcript_segments" if segments else None
    if not segments:
        raise HTTPException(status_code=409, detail="asset has no transcript")
    try:
        document = build_document(segments, style=style)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if source:
        document["qc"]["timing_source"] = source
    if sidecar_limitation:
        document["qc"].setdefault("limitations", []).append(sidecar_limitation)
    return document


@router.get("/api/assets/{asset_id}/captions")
def asset_captions(asset_id: int, style: str = Query("standard"), db: Session = Depends(get_db)):
    return _document(asset_id, style, db)


@router.get("/api/assets/{asset_id}/captions/{format}")
def download_captions(asset_id: int, format: Literal["srt", "vtt", "ass"], style: str = Query("standard"), db: Session = Depends(get_db)):
    document = _document(asset_id, style, db)
    media_types = {"srt": "application/x-subrip", "vtt": "text/vtt", "ass": "text/x-ssa"}
    return Response(
        content=serialize(document, format),
        media_type=f"{media_types[format]}; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="asset-{asset_id}.{format}"'},
    )
