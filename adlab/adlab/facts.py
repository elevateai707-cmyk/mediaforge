"""Zero fabrication, enforced in code.

`sites/<site>/facts.yaml` is the ONLY source of factual claims an ad may make.
It compiles to a FactSheet; `brief_model_for(sheet)` then builds a Brief model
whose Claim.fact_key is `Literal[<verified keys>]`. A claim with a missing,
misspelled, unverified, or expired key fails Pydantic validation, so the brief is
rejected before it reaches Compliance. (Compliance separately checks that a
claim's *text* actually says what its fact says.)

A fact only counts when you marked it `verified: true` and gave its exact
approved wording in `statement`. Templates ship unverified on purpose.

    facts:
      red_seal_master_electrician:
        statement: "Installed by a Red Seal Master Electrician"
        verified: true
        source: "Alberta trade certificate #..."    # where the proof lives
        expires: null                                # or a date (promos, prices)
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model, field_validator

from .config import settings
from .schemas import Brief, Claim

KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    statement: str = ""                 # exact approved wording
    verified: bool = False
    source: str = ""                    # where the proof lives (licence, price page, invoice)
    expires: Optional[date] = None      # promos and prices go stale
    notes: str = ""

    def usable(self, today: date) -> bool:
        return self.verified and bool(self.statement) and (self.expires is None or self.expires >= today)


class FactSheet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site: str
    facts: dict[str, Fact] = {}

    @field_validator("facts")
    @classmethod
    def _keys(cls, v: dict[str, Fact]) -> dict[str, Fact]:
        bad = [k for k in v if not KEY_RE.match(k)]
        if bad:
            raise ValueError(f"fact keys must be snake_case identifiers: {bad}")
        return v

    def usable_keys(self, today: Optional[date] = None) -> list[str]:
        today = today or date.today()
        return sorted(k for k, f in self.facts.items() if f.usable(today))

    def unusable(self, today: Optional[date] = None) -> dict[str, str]:
        """key -> why it can't be used yet (for `adlab facts`)."""
        today = today or date.today()
        out = {}
        for k, f in sorted(self.facts.items()):
            if f.usable(today):
                continue
            if not f.statement:
                out[k] = "no statement"
            elif not f.verified:
                out[k] = "not verified"
            else:
                out[k] = f"expired {f.expires}"
        return out


class NoUsableFacts(ValueError):
    pass


def load_facts(site: str, sites_dir: Optional[Path] = None) -> FactSheet:
    path = (sites_dir or settings().sites_dir) / site / "facts.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"no facts file for site {site!r}: {path}")
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a mapping")
    return FactSheet(site=site, facts=raw.get("facts") or {})


def claim_model_for(sheet: FactSheet, today: Optional[date] = None) -> type[Claim]:
    """Claim whose fact_key is Literal[<this site's usable keys>]."""
    keys = sheet.usable_keys(today)
    if not keys:
        raise NoUsableFacts(
            f"{sheet.site}: no verified facts yet. Fill sites/{sheet.site}/facts.yaml "
            "(statement + verified: true) before writing briefs."
        )
    key_type = Literal[tuple(keys)]  # type: ignore[valid-type]
    return create_model(f"{_camel(sheet.site)}Claim", __base__=Claim, fact_key=(key_type, ...))


def brief_model_for(sheet: FactSheet, today: Optional[date] = None) -> type[Brief]:
    """A Brief model for this site whose claims may only cite usable facts."""
    site_claim = claim_model_for(sheet, today)
    return create_model(
        f"{_camel(sheet.site)}Brief", __base__=Brief,
        site=(Literal[sheet.site], ...),  # type: ignore[valid-type]
        claims=(list[site_claim], Field(default_factory=list)),  # type: ignore[valid-type]
    )


class ClaimProblem(BaseModel):
    index: int
    text: str
    fact_key: Any
    problem: str


class BriefCheck(BaseModel):
    ok: bool
    brief: Optional[Brief] = None
    claim_problems: list[ClaimProblem] = []
    other_errors: list[str] = []


def check_brief(data: dict[str, Any], sheet: FactSheet, today: Optional[date] = None) -> BriefCheck:
    """Validate a raw brief; say exactly which claim broke and why."""
    model = brief_model_for(sheet, today)
    try:
        return BriefCheck(ok=True, brief=model.model_validate(data))
    except ValidationError as exc:
        claims = data.get("claims") or []
        problems: list[ClaimProblem] = []
        other: list[str] = []
        known = set(sheet.facts)
        for err in exc.errors():
            loc = err["loc"]
            if len(loc) >= 2 and loc[0] == "claims" and isinstance(loc[1], int):
                i = loc[1]
                claim = claims[i] if i < len(claims) and isinstance(claims[i], dict) else {}
                key = claim.get("fact_key")
                if loc[2:] == ("fact_key",):
                    if key in known:
                        why = f"fact {key!r} exists but isn't usable ({sheet.unusable(today).get(key, '?')})"
                    elif key in (None, ""):
                        why = "claim has no fact_key: every factual claim must cite a fact"
                    else:
                        why = f"unknown fact {key!r}: not in sites/{sheet.site}/facts.yaml"
                else:
                    why = err["msg"]
                problems.append(ClaimProblem(index=i, text=str(claim.get("text", "")), fact_key=key, problem=why))
            else:
                other.append(f"{'.'.join(map(str, loc))}: {err['msg']}")
        return BriefCheck(ok=False, claim_problems=problems, other_errors=other)


def _camel(site: str) -> str:
    return "".join(p.capitalize() for p in re.split(r"[^a-zA-Z0-9]+", site) if p) or "Site"
