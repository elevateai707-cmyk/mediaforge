"""Ollama HTTP client (127.0.0.1:11434) with transient-error retries.

Used for qwen2.5vl:7b image captions and the edit planner. The model is
auto-pulled on first use when MF_ALLOW_OLLAMA_PULL=1 (default), otherwise a
clear failure message is produced and the calling phase degrades gracefully.
"""
from __future__ import annotations

import base64
import json
import logging
import subprocess
import time
from typing import Any, Optional

import httpx

from .. import config
from ..proc import tool_env
from .gpu import set_model_status

log = logging.getLogger("mediaforge.ollama")

OLLAMA_STATE: dict[str, Any] = {"reachable": None, "error": None,
                                "model_installed": None}


def _client(timeout: Optional[float] = None) -> httpx.Client:
    return httpx.Client(
        base_url=config.OLLAMA_HOST,
        timeout=timeout if timeout is not None else config.OLLAMA_TIMEOUT,
    )


def ollama_reachable() -> bool:
    """True when the Ollama server answers /api/tags."""
    if OLLAMA_STATE["reachable"] is not None:
        return OLLAMA_STATE["reachable"]
    try:
        with _client() as client:
            resp = client.get("/api/tags")
            OLLAMA_STATE["reachable"] = resp.status_code == 200
    except Exception as exc:
        OLLAMA_STATE["reachable"] = False
        OLLAMA_STATE["error"] = str(exc)
    if not OLLAMA_STATE["reachable"]:
        set_model_status("ollama", "unavailable", OLLAMA_STATE["error"])
    else:
        set_model_status("ollama", "loaded")
    return OLLAMA_STATE["reachable"]


def list_models() -> list[str]:
    try:
        with _client() as client:
            resp = client.get("/api/tags")
            if resp.status_code != 200:
                return []
            return [m.get("name", "") for m in resp.json().get("models", [])]
    except Exception:
        return []


def model_installed(name: Optional[str] = None) -> bool:
    name = name or config.OLLAMA_MODEL
    models = list_models()
    return any(m.split(":")[0] == name.split(":")[0] and
               (m.split(":")[1] if ":" in m else "") ==
               (name.split(":")[1] if ":" in name else "") for m in models) or \
        name in models


def pull_model(name: Optional[str] = None, timeout: int = 900) -> bool:
    """Auto-pull the model via the ollama CLI (blocking)."""
    name = name or config.OLLAMA_MODEL
    if not config.ALLOW_OLLAMA_PULL:
        log.info("ollama auto-pull disabled by MF_ALLOW_OLLAMA_PULL=0")
        return False
    log.info("auto-pulling ollama model %s (first use)", name)
    try:
        proc = subprocess.run(
            ["ollama", "pull", name], capture_output=True, text=True,
            timeout=timeout, env=tool_env())
        if proc.returncode == 0:
            OLLAMA_STATE["model_installed"] = True
            set_model_status("ollama:" + name, "loaded")
            return True
        OLLAMA_STATE["error"] = f"ollama pull failed: {proc.stderr[-400:]}"
        set_model_status("ollama:" + name, "unavailable", OLLAMA_STATE["error"])
        return False
    except Exception as exc:
        OLLAMA_STATE["error"] = f"ollama pull error: {exc}"
        set_model_status("ollama:" + name, "unavailable", str(exc))
        return False


def ensure_model(name: Optional[str] = None) -> bool:
    """Ensure the model is installed; pull once if missing. Returns True when ready."""
    name = name or config.OLLAMA_MODEL
    if OLLAMA_STATE["model_installed"]:
        return True
    if not ollama_reachable():
        return False
    if model_installed(name):
        OLLAMA_STATE["model_installed"] = True
        set_model_status("ollama:" + name, "loaded")
        return True
    return pull_model(name)


class OllamaClient:
    """Small object wrapper around the module-level helpers.

    Used by app/ai/captions.py; keeps a base_url/model so callers can hold
    one client. All methods delegate to the module functions above.
    """

    def __init__(self, base_url: Optional[str] = None,
                 model: Optional[str] = None) -> None:
        self.base_url = base_url or config.OLLAMA_HOST
        self.model = model or config.OLLAMA_MODEL

    def list_models(self) -> list[str]:
        return list_models()

    def pull_model(self, name: Optional[str] = None) -> bool:
        return pull_model(name)

    def generate(self, prompt: str, images: Optional[list[str]] = None,
                 format: Optional[str] = None, temperature: float = 0.2,
                 retries: int = 5) -> str:
        return generate(self.model, prompt, images=images, format=format,
                        temperature=temperature, retries=retries)


def generate(model: str, prompt: str, images: Optional[list[str]] = None,
             format: Optional[str] = None, temperature: float = 0.2,
             retries: int = 5, timeout: Optional[float] = None) -> str:
    """Call /api/generate with retries on transient connection errors.

    images: list of base64-encoded JPEGs. Returns the model's text response.
    Raises RuntimeError with a clear message when Ollama is unreachable or the
    model is missing.
    """
    last_exc: Optional[Exception] = None
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }
    if images:
        payload["images"] = images
    if format:
        payload["format"] = format
    for attempt in range(1, retries + 1):
        try:
            with _client(timeout=timeout) as client:
                resp = client.post("/api/generate", json=payload)
                if resp.status_code == 404:
                    raise RuntimeError(
                        f"Ollama model '{model}' not found on this server. "
                        "Run `ollama pull " + model + "` (the backend attempts "
                        "this automatically on first use when auto-pull is "
                        "enabled) or check OLLAMA_HOST.")
                resp.raise_for_status()
                data = resp.json()
                return str(data.get("response", ""))
        except httpx.TransportError as exc:
            last_exc = exc
            wait = 1.0 * (2 ** (attempt - 1))
            log.warning("ollama connection error (attempt %d/%d): %s — retrying in %.1fs",
                        attempt, retries, exc, wait)
            time.sleep(wait)
        except RuntimeError:
            raise
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(1.0)
            continue
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < retries:
                time.sleep(1.0)
    raise RuntimeError(f"Ollama generate failed after {retries} attempts: {last_exc}")


def image_to_b64(path: str, max_side: int = 768) -> str:
    """Load an image and return base64 JPEG (Ollama vision input)."""
    from ..images import open_rgb
    img = open_rgb(path)
    img.thumbnail((max_side, max_side))
    import io
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("ascii")
