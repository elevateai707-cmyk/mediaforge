"""Pydantic contracts between AdLab's pipeline stages.

    Ad -> Teardown -> Pattern -> Brief -> Creative -> ComplianceReport -> Result

Every stage reads one of these and writes the next, so each stage can be run and
tested alone, and an LLM's output is never trusted until it validates here.
Models forbid unknown fields: a typo or a hallucinated key is an error, not data.

Two guarantees live in these types rather than in prompts:
* Brief.claims are `{text, fact_key}`; `facts.brief_model_for()` narrows
  fact_key to a Literal of the site's verified facts, so an invented claim fails
  validation before Compliance ever sees it.
* Creative.ai_generated is Literal[True]: every generated ad carries the AI
  disclosure needed for Meta's and TikTok's AI-content labels.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, model_validator

Platform = Literal["meta", "tiktok", "other"]
AspectRatio = Literal["9:16", "1:1", "4:5", "16:9"]
HookType = Literal["question", "pattern_interrupt", "bold_claim", "pov", "problem_callout", "demo"]
Angle = Literal["pain", "desire", "fear", "savings", "speed", "trust", "local_pride"]
AdFormat = Literal["ugc", "talking_head", "demo", "slideshow", "text_on_screen", "before_after"]
CaptureMethod = Literal[
    "manual_drop",           # a file I saved and dropped into data/incoming/
    "manual_link",           # an Ad Library / Creative Center link I pasted
    "meta_ad_library_api",
    "tiktok_creative_center_export",
    "tiktok_commercial_content_api",
    "own_account_api",       # my own Meta/TikTok ad accounts
]
Verdict = Literal["PASS", "FIX", "REJECT"]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --- Scout -------------------------------------------------------------------
class SourceRef(Strict):
    """Where an ad came from. AdLab never fetches media from `url`."""
    platform: Platform
    captured_via: CaptureMethod
    url: Optional[HttpUrl] = None
    advertiser: str = Field(min_length=1)
    notes: str = ""


class WinningScore(Strict):
    """Proxy score: competitors' conversions aren't public, so these stand in."""
    longevity_days: int = Field(ge=0)
    still_running: bool
    variant_count: int = Field(ge=1)
    platform_rank: Optional[int] = Field(default=None, ge=1)  # e.g. Creative Center ranking
    repeat_pattern_count: int = Field(default=0, ge=0)        # same angle across advertisers
    total: float = Field(ge=0, le=100)
    breakdown: dict[str, float] = {}                         # component -> points


class Ad(Strict):
    id: str = Field(min_length=8)                 # sha256 of media, else of the source URL
    site: str                                     # the site whose niche this ad was scouted for
    source: SourceRef
    ad_text: str = ""
    media_paths: list[str] = []                   # private copies, analysis only
    media_sha256: list[Sha256] = []
    first_seen: date
    last_seen: date
    variant_group: Optional[str] = None
    score: Optional[WinningScore] = None

    @model_validator(mode="after")
    def _dates(self) -> "Ad":
        if self.last_seen < self.first_seen:
            raise ValueError("last_seen is before first_seen")
        return self


# --- Analyst -----------------------------------------------------------------
class Hook(Strict):
    type: HookType
    seconds: float = Field(gt=0, le=3)
    on_screen_text: str = ""
    visual: str = Field(min_length=1)


class Scene(Strict):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    description: str = Field(min_length=1)      # structure only: what happens, not their script
    on_screen_text: str = ""

    @model_validator(mode="after")
    def _order(self) -> "Scene":
        if self.end <= self.start:
            raise ValueError("scene end must be after start")
        return self


class Pacing(Strict):
    length_s: float = Field(gt=0)
    cuts_per_10s: float = Field(ge=0)
    caption_style: str = ""
    music_energy: Literal["none", "low", "medium", "high"]


class Teardown(Strict):
    """WHY an ad works, as structure. Never stores their script lines verbatim."""
    ad_id: str
    hook: Hook
    angles: list[Angle] = Field(min_length=1)
    offer: str = ""
    cta: str = ""
    timeline: list[Scene] = Field(min_length=1)
    format: AdFormat
    pacing: Pacing
    why_it_works: str = Field(min_length=1, max_length=400)
    model: str                                   # e.g. "claude-opus-5-5" or a local model
    created_at: datetime = Field(default_factory=utcnow)


# --- Strategist --------------------------------------------------------------
PatternStep = Literal[
    "hook", "problem_callout", "agitate", "credential", "demo", "social_proof",
    "price_anchor", "offer", "objection_handle", "urgency", "cta",
]


class Pattern(Strict):
    id: str
    name: str                                    # "Problem call-out → credential → price anchor → CTA"
    steps: list[PatternStep] = Field(min_length=2)
    hook_types: list[HookType] = []
    angles: list[Angle] = []
    formats: list[AdFormat] = []
    source_teardown_ids: list[str] = Field(min_length=1)
    own_performance_score: Optional[float] = None   # set by Reporter from MY results


class Claim(Strict):
    """A factual statement in an ad. fact_key is narrowed per site (facts.py)."""
    text: str = Field(min_length=1)
    fact_key: str


class Shot(Strict):
    index: int = Field(ge=1)
    seconds: float = Field(gt=0, le=15)
    visual: str = Field(min_length=1)
    asset: Literal["generated_image", "generated_video", "brand_asset", "text_card", "own_footage"]
    on_screen_text: str = ""
    vo_line: str = ""


class Brief(Strict):
    id: str
    site: str
    pattern_id: str
    source_ad_ids: list[str] = Field(min_length=1)   # what it learned from (structure only)
    hook: Hook
    angle: Angle
    script: str = Field(min_length=1)
    shots: list[Shot] = Field(min_length=1)
    cta: str = Field(min_length=1)
    claims: list[Claim] = []
    aspect_ratios: list[AspectRatio] = Field(min_length=1)
    missing_facts: list[str] = []    # facts the brief wanted but facts.yaml lacks -> ask me

    @model_validator(mode="after")
    def _shots_numbered(self) -> "Brief":
        if [s.index for s in self.shots] != list(range(1, len(self.shots) + 1)):
            raise ValueError("shots must be numbered 1..n in order")
        return self


# --- Producer ----------------------------------------------------------------
class CostLine(Strict):
    provider: str                     # anthropic | elevenlabs | mediaforge | local
    item: str
    usd: float = Field(ge=0)


class Creative(Strict):
    id: str
    brief_id: str
    aspect_ratio: AspectRatio
    path: str
    kind: Literal["video", "image"]
    sha256: Sha256
    ai_generated: Literal[True] = True
    ai_disclosure: str = "Made with AI tools. No real customers or testimonials depicted."
    facts_used: list[str] = []
    source_ad_ids: list[str] = []
    cost: list[CostLine] = []
    created_at: datetime = Field(default_factory=utcnow)


# --- Compliance --------------------------------------------------------------
class CheckResult(Strict):
    name: str                          # fact_trace | similarity | banned_content | platform_policy
    passed: bool
    reasons: list[str] = []


class ComplianceReport(Strict):
    creative_id: str
    verdict: Verdict
    checks: list[CheckResult] = Field(min_length=1)
    created_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _consistent(self) -> "ComplianceReport":
        failed = [c for c in self.checks if not c.passed]
        if self.verdict == "PASS" and failed:
            raise ValueError(f"PASS with failed checks: {[c.name for c in failed]}")
        if self.verdict != "PASS" and not any(c.reasons for c in failed):
            raise ValueError("FIX/REJECT needs at least one failed check with reasons")
        return self


# --- Reporter ----------------------------------------------------------------
class Result(Strict):
    """MY ad account results: the only real conversion data AdLab learns from."""
    creative_id: str
    platform: Literal["meta", "tiktok"]
    campaign_id: str
    day: date
    impressions: int = Field(ge=0)
    clicks: int = Field(ge=0)
    spend_cad: float = Field(ge=0)
    leads: int = Field(default=0, ge=0)
    conversions: int = Field(default=0, ge=0)

    @property
    def ctr(self) -> Optional[float]:
        return self.clicks / self.impressions if self.impressions else None

    @property
    def cpc(self) -> Optional[float]:
        return self.spend_cad / self.clicks if self.clicks else None

    @property
    def cpl(self) -> Optional[float]:
        return self.spend_cad / self.leads if self.leads else None
