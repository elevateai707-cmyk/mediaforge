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


CAPTION_PROMPT = (
    "Describe this frame for a video editor in one sentence (max 25 words): main subject, "
    "setting, notable action, food, or landmark. Be specific and concrete. Only the description."
)


def caption(image_b64: str, model: Optional[str] = None,
            timeout: Optional[float] = None) -> str:
    """One-line caption for a base64 JPEG. Raises RuntimeError on failure."""
    content = [
        {"type": "text", "text": CAPTION_PROMPT},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + image_b64}},
    ]
    text = _chat(
        [{"role": "user", "content": content}],
        model or config.CLOUD_CAPTION_MODEL,
        timeout if timeout is not None else config.CLOUD_CAPTION_TIMEOUT,
        max_tokens=120,
    )
    return " ".join(text.split())


def generate_json(prompt: str, model: Optional[str] = None,
                  timeout: Optional[float] = None) -> str:
    """Return the model's JSON text. Raises RuntimeError on any failure."""
    return _chat(
        [{"role": "user", "content": prompt}],
        model or config.CLOUD_PLAN_MODEL,
        timeout if timeout is not None else config.CLOUD_PLAN_TIMEOUT,
        response_format={"type": "json_object"},
    )


def _chat(messages: list, model: str, timeout: float, response_format=None,
          max_tokens: Optional[int] = None) -> str:
    key = api_key()
    if not key:
        raise RuntimeError("no OpenRouter key (set OPENROUTER_API_KEY or MF_OPENROUTER_KEY_FILE)")
    body = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        # Reasoning makes calls much slower and pricier for a marginal gain.
        "reasoning": {"enabled": config.CLOUD_PLAN_REASONING},
    }
    if response_format:
        body["response_format"] = response_format
    if max_tokens:
        body["max_tokens"] = max_tokens
    try:
        resp = httpx.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {key}", "X-Title": "MediaForge"},
            json=body,
            timeout=timeout,
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
    log.info("cloud call via %s: %s/%s tokens, cost %s", model,
             usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("cost"))
    return text or ""
