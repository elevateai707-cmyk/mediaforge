"""Token usage and cost, logged per command.

Every LLM call records one line in data/logs/usage.jsonl (and, later, the
`llm_usage` table) via `record()`. Commands wrap themselves in `track(command)`,
which prints a one-line cost summary when they finish, e.g.

    adlab analyze: 3 calls, 41,200 in / 6,300 out tokens, $0.29

Prices come from config/pricing.yaml (edit there; nothing is hard-coded here).
An unknown model is logged with cost None rather than guessed.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Iterator, Optional

import yaml
from pydantic import BaseModel, Field

from .config import settings


class Price(BaseModel):
    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cache_read: Optional[float] = Field(default=None, ge=0)    # None = unknown
    cache_write: Optional[float] = Field(default=None, ge=0)


@lru_cache(maxsize=4)
def _prices(path: str) -> dict[str, Price]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return {m: Price(**p) for m, p in (data.get("models") or {}).items()}


def prices() -> dict[str, Price]:
    return _prices(str(settings().config_dir / "pricing.yaml"))


def cost_usd(model: str, input_tokens: int = 0, output_tokens: int = 0,
             cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> Optional[float]:
    p = prices().get(model)
    if p is None:
        return None
    total = input_tokens * p.input + output_tokens * p.output
    for tokens, price in ((cache_read_tokens, p.cache_read), (cache_write_tokens, p.cache_write)):
        if tokens:
            if price is None:
                return None   # unknown price: report unpriced, don't guess
            total += tokens * price
    return round(total / 1_000_000, 6)


_current: list[dict] = []   # stack of active track() sessions


def record(command: str, provider: str, model: str, input_tokens: int = 0, output_tokens: int = 0,
           cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> dict:
    """Log one call. For Anthropic responses pass the fields of `response.usage`."""
    line = {
        "at": datetime.now(timezone.utc).isoformat(), "command": command, "provider": provider,
        "model": model, "input_tokens": input_tokens, "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens, "cache_write_tokens": cache_write_tokens,
        "cost_usd": cost_usd(model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens),
    }
    logs = settings().logs_dir
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / "usage.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")
    for session in _current:
        session["calls"].append(line)
    return line


def service_price(name: str) -> Optional[float]:
    """Per-unit price from pricing.yaml `services:` (None = you haven't set it)."""
    data = yaml.safe_load((settings().config_dir / "pricing.yaml").read_text(encoding="utf-8")) or {}
    v = (data.get("services") or {}).get(name)
    return float(v) if v is not None else None


def record_service(command: str, provider: str, item: str, units: float, unit_price: Optional[float]) -> dict:
    """Log a non-token call (ElevenLabs characters, ...). Unknown price -> cost None."""
    line = {
        "at": datetime.now(timezone.utc).isoformat(), "command": command, "provider": provider,
        "model": item, "units": units, "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 0, "cache_write_tokens": 0,
        "cost_usd": round(units * unit_price, 6) if unit_price is not None else None,
    }
    logs = settings().logs_dir
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / "usage.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")
    for session in _current:
        session["calls"].append(line)
    return line


def summarize(calls: list[dict]) -> dict:
    known = [c["cost_usd"] for c in calls if c["cost_usd"] is not None]
    return {
        "calls": len(calls),
        "input_tokens": sum(c["input_tokens"] + c["cache_read_tokens"] + c["cache_write_tokens"] for c in calls),
        "output_tokens": sum(c["output_tokens"] for c in calls),
        "cost_usd": round(sum(known), 4),
        "unpriced_calls": len(calls) - len(known),
    }


@contextmanager
def track(command: str, echo=print) -> Iterator[dict]:
    session: dict = {"command": command, "calls": []}
    _current.append(session)
    try:
        yield session
    finally:
        _current.remove(session)
        s = summarize(session["calls"])
        if s["calls"]:
            extra = f" ({s['unpriced_calls']} unpriced: add the model to config/pricing.yaml)" if s["unpriced_calls"] else ""
            echo(f"{command}: {s['calls']} calls, {s['input_tokens']:,} in / {s['output_tokens']:,} out tokens, "
                 f"${s['cost_usd']:.4f}{extra}")


def ledger(since: Optional[datetime] = None) -> list[dict]:
    path = settings().logs_dir / "usage.jsonl"
    if not path.is_file():
        return []
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if since is not None:
        rows = [r for r in rows if datetime.fromisoformat(r["at"]) >= since]
    return rows
