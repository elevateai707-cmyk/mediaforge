"""Image captioning via Ollama qwen2.5vl:7b (multimodal).

``caption_image`` returns a short one-line caption. When Ollama is down or
the model is missing, a deterministic fallback (filename stem + dominant
color) is returned — the function ALWAYS returns a string.

Uses the module-level helpers from ``app.ai.ollama_llm`` directly
(``ensure_model``, ``generate``, ``image_to_b64``); there is no client class.
"""

from __future__ import annotations

import logging
import os
import re

from app import config
from app.ai import gpu
from app.ai.ollama_llm import ensure_model, generate, image_to_b64
from app.images import open_rgb

log = logging.getLogger(__name__)

_ensured_model = False

_CAPTION_PROMPT = (
    "Describe this image in one short sentence (max 15 words). "
    "Only the description, no preamble."
)


def _ensure_model_ready() -> bool:
    """Ensure the vision model is present (pull on first use when allowed)."""
    global _ensured_model
    if _ensured_model:
        return True
    try:
        if ensure_model(config.OLLAMA_MODEL):
            _ensured_model = True
            return True
    except Exception as exc:  # pragma: no cover
        log.debug("ollama model check failed: %s", exc)
    return False


def _fallback_caption(path: str) -> str:
    """Deterministic fallback: filename stem + dominant colour name."""
    try:
        img = open_rgb(path).resize((64, 64))
        quantized = img.quantize(colors=4)
        palette = quantized.getpalette()
        counts = sorted(quantized.getcolors(), reverse=True)
        dominant = counts[0][1] if counts else 0
        rgb = tuple(palette[dominant * 3 : dominant * 3 + 3]) if palette else (0, 0, 0)
        color = _color_name(rgb)
    except Exception:
        color = "unknown color"
    stem = os.path.splitext(os.path.basename(path))[0].replace("_", " ").replace("-", " ").strip()
    stem = re.sub(r"\s+", " ", stem)[:60]
    return f"{stem or 'image'} with {color}"


_COLOR_TABLE: list[tuple[str, tuple[int, int, int]]] = [
    ("black", (0, 0, 0)),
    ("white", (255, 255, 255)),
    ("red", (255, 0, 0)),
    ("green", (0, 180, 0)),
    ("blue", (0, 0, 255)),
    ("yellow", (255, 255, 0)),
    ("orange", (255, 140, 0)),
    ("purple", (160, 60, 220)),
    ("pink", (255, 120, 180)),
    ("brown", (139, 69, 19)),
    ("grey", (128, 128, 128)),
    ("teal", (0, 150, 150)),
]


def _color_name(rgb: tuple[int, int, int]) -> str:
    best, best_d = "grey", float("inf")
    for name, ref in _COLOR_TABLE:
        d = sum((a - b) ** 2 for a, b in zip(rgb, ref))
        if d < best_d:
            best_d, best = d, name
    return best


def caption_image(image_path: str) -> str:
    """Short caption for an image. Always returns a non-empty string."""
    if not image_path or not os.path.isfile(image_path):
        return "image"
    if _ensure_model_ready():
        # Encoding is a per-file concern: an unreadable still must not be
        # reported as an Ollama outage. Only failures from generate() say
        # anything about the model.
        try:
            b64 = image_to_b64(image_path, max_side=1024)
        except Exception as exc:  # noqa: BLE001 - one bad file, not a model fault
            log.debug("caption encode failed for %s: %s", image_path, exc)
            return _fallback_caption(image_path)
        if not b64:
            return _fallback_caption(image_path)
        try:
            response = generate(
                model=config.OLLAMA_MODEL,
                prompt=_CAPTION_PROMPT,
                images=[b64],
            )
            text = (response or "").strip()
            text = re.sub(r"\s+", " ", text).strip(" '\".,")
            if text:
                return text[:200]
        except Exception as exc:  # pragma: no cover
            log.debug("ollama caption failed: %s", exc)
            gpu.set_model_status("ollama", "unavailable", str(exc))
    return _fallback_caption(image_path)
