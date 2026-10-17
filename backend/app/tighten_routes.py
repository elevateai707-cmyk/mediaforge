"""HTTP API for reversible audio tightening plans."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, model_validator

from .edits.tighten import TightenError, create_tighten_plan

router = APIRouter(tags=["edits"])


class TightenRequest(BaseModel):
    asset_id: int | None = None
    plan_id: str | None = None
    silence_db: float = -35
    min_silence_s: float = .4
    remove_fillers: bool = True
    padding_s: float = .08

    @model_validator(mode="after")
    def exactly_one_source(self):
        if (self.asset_id is None) == (self.plan_id is None):
            raise ValueError("provide exactly one of asset_id or plan_id")
        return self


@router.post("/api/edits/tighten")
def tighten(req: TightenRequest):
    try:
        return create_tighten_plan(**req.model_dump())
    except TightenError as exc:
        detail = str(exc)
        status = 409 if "voiceover synchronization" in detail else 422
        if "not found" in detail:
            status = 404
        raise HTTPException(status_code=status, detail=detail) from exc
