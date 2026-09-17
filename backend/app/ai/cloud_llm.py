"""OpenRouter chat client for edit planning (opt-in via Settings -> Cloud LLM).

Only the planner prompt is sent: the user's intent plus scene ids, in/out
times, scores and captions. No media, file paths or GPS leave the machine.
The API key stays server-side (env or key file) and is never returned to
the browser.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import httpx

from .. import config

log = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def api_key() -> str:
    env = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if env:
        return env
    try:
        return Path(config.OPENROUTER_KEY_FILE).expanduser().read_text().strip()
    except OSError:
        return ""


def available() -> bool:
    return bool(api_key())


def generate_json(prompt: str, model: Optional[str] = None,
                  timeout: Optional[float] = None) -> str:
    """Return the model's JSON text. Raises RuntimeError on any failure."""
    key = api_key()
    if not key:
        raise RuntimeError("no OpenRouter key (set OPENROUTER_API_KEY or MF_OPENROUTER_KEY_FILE)")
    body = {
        "model": model or config.CLOUD_PLAN_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        # Reasoning makes plans ~13x slower and ~4x pricier for a marginal gain.
        "reasoning": {"enabled": config.CLOUD_PLAN_REASONING},
    }
    try:
        resp = httpx.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {key}", "X-Title": "MediaForge"},
            json=body,
            timeout=timeout if timeout is not None else config.CLOUD_PLAN_TIMEOUT,
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"OpenRouter request failed: {type(exc).__name__}") from None
    if resp.status_code != 200:
        raise RuntimeError(f"OpenRouter returned HTTP {resp.status_code}")
    try:
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise RuntimeError("OpenRouter returned an unexpected response") from None
    usage = data.get("usage") or {}
    log.info("cloud plan via %s: %s/%s tokens, cost %s", body["model"],
             usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("cost"))
    return text or ""
