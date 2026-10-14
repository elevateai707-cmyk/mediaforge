"""The one place AdLab calls Claude.

Every stage asks for a Pydantic model back (structured outputs via
`client.beta.messages.parse`); nothing free-form leaves this module. Each call:
* checks `stop_reason == "refusal"` before reading output (raises LLMRefusal);
* opts into server-side refusal fallbacks (`fallbacks="default"`, beta
  `server-side-fallback-2026-07-01`), switchable off with ADLAB_LLM_FALLBACKS=0;
* logs tokens + cost to the usage ledger (usage.py) under the calling command.

Tests inject a fake client with the same `beta.messages.parse` surface.
"""
from __future__ import annotations

import base64
import io
import os
from pathlib import Path
from typing import Any, Optional, TypeVar

from pydantic import BaseModel

from . import usage
from .config import settings

T = TypeVar("T", bound=BaseModel)
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(RuntimeError):
    pass


class LLMRefusal(LLMError):
    pass


_client: Any = None


def client() -> Any:
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic()  # ANTHROPIC_API_KEY, or an `ant auth login` profile
    return _client


def set_client(c: Any) -> None:
    """For tests (and a custom-configured client)."""
    global _client
    _client = c


def image_block(path: Path, max_side: int = 1024, quality: int = 85) -> dict:
    """JPEG-encode (downscaled) an image as a base64 content block."""
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality)
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.standard_b64encode(buf.getvalue()).decode()}}


def request_kwargs(system: str, content: list[dict], output_model: type[BaseModel],
                   effort: str = "high", max_tokens: int = 16000, model: Optional[str] = None) -> dict:
    """Exactly what parse() is called with (also printed by --dry-run, minus image bytes)."""
    kw: dict[str, Any] = {
        "model": model or settings().default_model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": content}],
        "output_format": output_model,
        "output_config": {"effort": effort},
    }
    if os.environ.get("ADLAB_LLM_FALLBACKS", "1") != "0":
        kw["betas"] = [FALLBACK_BETA]
        kw["fallbacks"] = "default"
    return kw


def redacted(kw: dict) -> dict:
    """request_kwargs for display: image bytes and the Pydantic class summarised."""
    shown = dict(kw)
    shown["output_format"] = kw["output_format"].__name__
    msgs = []
    for m in kw["messages"]:
        parts = []
        for b in m["content"]:
            if b.get("type") == "image":
                parts.append({"type": "image", "bytes": len(b["source"]["data"]) * 3 // 4})
            else:
                parts.append(b)
        msgs.append({"role": m["role"], "content": parts})
    shown["messages"] = msgs
    return shown


def parse(command: str, system: str, content: list[dict], output_model: type[T],
          effort: str = "high", max_tokens: int = 16000, model: Optional[str] = None) -> T:
    kw = request_kwargs(system, content, output_model, effort, max_tokens, model)
    resp = client().beta.messages.parse(**kw)
    u = getattr(resp, "usage", None)
    if u is not None:
        usage.record(command, "anthropic", getattr(resp, "model", kw["model"]) or kw["model"],
                     input_tokens=getattr(u, "input_tokens", 0) or 0,
                     output_tokens=getattr(u, "output_tokens", 0) or 0,
                     cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
                     cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0)
    if getattr(resp, "stop_reason", None) == "refusal":
        details = getattr(resp, "stop_details", None)
        raise LLMRefusal(f"Claude declined ({getattr(details, 'category', None) or 'no category'})")
    if getattr(resp, "stop_reason", None) == "max_tokens":
        raise LLMError("output hit max_tokens before the JSON was complete")
    parsed = getattr(resp, "parsed_output", None)
    if parsed is None:
        raise LLMError("no structured output in the response")
    return output_model.model_validate(parsed.model_dump() if isinstance(parsed, BaseModel) else parsed)
