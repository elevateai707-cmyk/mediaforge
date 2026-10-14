"""Winning score from public proxies, with the per-signal breakdown kept.

Weights live in config/scoring.yaml. Every component is explainable ("longevity
92 days: 35") so you can see *why* an ad ranks where it does.
"""
from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field

from .config import settings
from .schemas import Ad, WinningScore


class Weights(BaseModel):
    longevity_days: dict[int, float]
    still_running_bonus: float = Field(ge=0)
    variants: dict[str, float]
    platform_rank: dict[int, float]
    repeat_pattern: dict[str, float]
    winner_threshold: float = Field(ge=0, le=100)


@lru_cache(maxsize=4)
def _weights(path: str) -> Weights:
    return Weights(**yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def weights() -> Weights:
    return _weights(str(settings().config_dir / "scoring.yaml"))


def score(ad: Ad, group_size: int = 1, repeat_pattern_count: int = 0,
          today: Optional[date] = None, w: Optional[Weights] = None) -> WinningScore:
    w = w or weights()
    today = today or date.today()
    running = ad.signals.still_running
    end = today if running else ad.last_seen
    days = max(0, (end - ad.first_seen).days)
    variants = max(group_size, ad.signals.reported_variants or 1)
    rank = ad.signals.platform_rank

    b: dict[str, float] = {}
    b["longevity"] = max([pts for d, pts in w.longevity_days.items() if days >= d], default=0.0)
    b["still_running"] = w.still_running_bonus if running else 0.0
    b["variants"] = min((variants - 1) * w.variants["per_extra_variant"], w.variants["max"])
    b["platform_rank"] = (max([pts for top, pts in w.platform_rank.items() if rank <= top], default=0.0)
                          if rank else 0.0)
    b["repeat_pattern"] = min(repeat_pattern_count * w.repeat_pattern["per_other_advertiser"],
                              w.repeat_pattern["max"])
    return WinningScore(
        longevity_days=days, still_running=bool(running), variant_count=variants,
        platform_rank=rank, repeat_pattern_count=repeat_pattern_count,
        total=round(min(100.0, sum(b.values())), 1), breakdown=b,
    )


def explain(s: WinningScore) -> str:
    parts = [f"{k.replace('_', ' ')} {v:g}" for k, v in s.breakdown.items() if v]
    return f"{s.total:g} = " + (" + ".join(parts) if parts else "0")
