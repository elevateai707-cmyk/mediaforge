"""Thin wrapper over Media Forge (HTTP API) and Elevate Vision Studio (local CLI).

See docs/mediaforge_interface.md for the full interface and its gaps. Short
version: Media Forge indexes, plans, edits and renders *existing* media. It has
no prompt-to-image/video generation (no ComfyUI) and no voiceover (ElevenLabs is
called directly by AdLab), so those Producer backends are pending your answer.

What this module wraps today:
* `MediaForge.health()/status()`        is it up, GPU, which engines are installed
* `MediaForge.release_gpu()`            POST /api/gpu/release: unload its models
* `render_still(lines, ratio, out)`     a still frame at 9:16/1:1/4:5/16:9 rendered by
                                        Elevate Vision Studio (CPU, no models), used
                                        by the Phase 1 smoke test and later end cards
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

import httpx

from .config import settings
from .schemas import AspectRatio

RATIO_SIZES: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350), "16:9": (1920, 1080),
}

# What the Producer needs vs what Media Forge offers. Shown by `adlab mf status`.
CAPABILITIES: list[dict[str, str]] = [
    {"need": "image generation (prompt -> image)", "status": "missing",
     "how": "Not in Media Forge (no ComfyUI/diffusion). Pending: where your ComfyUI lives."},
    {"need": "short video generation", "status": "missing",
     "how": "Not in Media Forge. It renders reels from EXISTING footage (plan -> approve -> render)."},
    {"need": "edit / assemble video", "status": "available",
     "how": "Studio project from a plan (POST /api/export/filmcraft) or FFmpeg render (/api/render)."},
    {"need": "photo edits", "status": "available",
     "how": "PhotoCraft recipes/steps on library photos (POST /api/photocraft/apply)."},
    {"need": "voiceover", "status": "missing",
     "how": "Not in Media Forge. AdLab will call ElevenLabs directly (Phase 5)."},
    {"need": "captions (burned in)", "status": "available",
     "how": "Per-clip captions in /api/render and Studio projects; transcripts via whisper."},
    {"need": "aspect ratios", "status": "partial",
     "how": "Media Forge: 9:16, 16:9, 1:1. 4:5 only via Studio CLI (render_still); not in /api/render yet."},
    {"need": "free VRAM before AdLab GPU jobs", "status": "available",
     "how": "POST /api/gpu/release (unloads CLIP/whisper/faces, evicts Ollama model)."},
]


class MediaForgeError(RuntimeError):
    pass


class MediaForge:
    def __init__(self, base_url: Optional[str] = None, timeout: float = 30):
        self.base_url = (base_url or settings().mediaforge_url).rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout)

    def _req(self, method: str, path: str, **kw: Any) -> Any:
        try:
            r = self._http.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise MediaForgeError(f"Media Forge unreachable at {self.base_url}: {exc}") from None
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            raise MediaForgeError(f"{method} {path} -> {r.status_code}: {detail}")
        return r.json() if r.content else None

    def health(self) -> dict:
        return self._req("GET", "/api/health")

    def status(self) -> dict:
        out: dict[str, Any] = {"url": self.base_url, "health": self.health()}
        for name, path in (("photocraft", "/api/photocraft/status"), ("studio", "/api/filmcraft/status")):
            try:
                out[name] = self._req("GET", path)
            except MediaForgeError as exc:
                out[name] = {"available": False, "error": str(exc)}
        return out

    def release_gpu(self) -> dict:
        """Ask Media Forge to unload its models. Older Media Forge builds lack the endpoint."""
        return self._req("POST", "/api/gpu/release")


# --- Elevate Vision Studio (local CLI) ---------------------------------------------
def find_studio_cli() -> Optional[str]:
    configured = settings().studio_cli
    if configured:
        return configured if os.access(configured, os.X_OK) else None
    for name in ("evstudio-cli", "filmcraft-cli"):
        found = shutil.which(name) or (str(Path.home() / ".local/bin" / name)
                                       if os.access(Path.home() / ".local/bin" / name, os.X_OK) else None)
        if found:
            return found
    return None


def png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as fh:
        head = fh.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    return struct.unpack(">II", head[16:24])


def render_still(lines: list[str], ratio: AspectRatio, out: Path, timeout: float = 120) -> Path:
    """Render a text card at `ratio` to PNG with Elevate Vision Studio (no GPU models)."""
    cli = find_studio_cli()
    if cli is None:
        raise MediaForgeError("Elevate Vision Studio CLI not found: run scripts/studio-setup.sh or set STUDIO_CLI")
    w, h = RATIO_SIZES[ratio]
    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    size = round(min(w, h) * 0.07)
    steps: list[dict[str, Any]] = [
        {"id": "file.newProject", "params": {"name": "AdLab still"}},
        {"id": "file.newSequence", "params": {"name": f"{ratio} still", "width": w, "height": h, "fps": 30}},
    ]
    for i, text in enumerate(lines):
        steps.append({"id": "graphics.newText", "params": {
            "text": text, "size": size if i == 0 else round(size * 0.6),
            "box": [round(w * 0.84), round(size * 2.6)],
            "position": [round(w * 0.08), round(h * 0.38 + i * size * 2.2)], "seconds": 3, "time": 0}})
    with tempfile.TemporaryDirectory(prefix="adlab_still_") as tmp:
        project = Path(tmp) / "still.fcproj"
        script = Path(tmp) / "steps.jsonl"
        script.write_text("".join(json.dumps(s) + "\n" for s in steps), encoding="utf-8")
        for stage, args in (
            ("build", ["--save-as", str(project), "--compact", "run", str(script)]),
            ("render", ["--project", str(project), "--compact", "render", "--seconds", "1", "--out", str(out)]),
        ):
            proc = subprocess.run([cli, *args], capture_output=True, text=True, timeout=timeout,
                                  cwd=tmp, stdin=subprocess.DEVNULL)
            if proc.returncode != 0:
                raise MediaForgeError(f"Studio {stage} failed: {(proc.stderr or proc.stdout).strip()[-300:]}")
    if png_size(out) != (w, h):
        raise MediaForgeError(f"expected {w}x{h}, got {png_size(out)}")
    return out
