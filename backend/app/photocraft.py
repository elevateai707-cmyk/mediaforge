"""PhotoCraft bridge: Photoshop-grade, non-destructive photo edits via photocraft-cli.

PhotoCraft (https://github.com/storytold/photocraft, MIT OR Apache-2.0) is a
pure-Rust image editor whose every menu item is an engine command. Its
``photocraft-cli run`` subcommand opens a file, plays a list of commands and
exports the result, which is all MediaForge needs.

Safety model — ``photocraft-cli run`` is a *trusted local* entry point: it
does not confine engine commands that take paths (LUT import, displacement
maps, saves) to any root. MediaForge therefore only forwards commands that
edit pixels in memory, and refuses anything that could name a file:

* command ids must match :data:`ALLOWED_PREFIXES` / :data:`ALLOWED_IDS` and not
  :data:`DENIED_IDS`;
* params are bounded JSON with no path-like keys and no path-like strings;
* the CLI runs without a shell, with ``tool_env()`` hygiene, a timeout, and a
  scratch cwd;
* the only file written is ``exports/photocraft/<asset>_<digest>.<ext>``,
  produced in a temp file and renamed into place, so the source is never
  touched and a failed run leaves nothing half-written.

The binary is found via ``MF_PHOTOCRAFT_CLI`` or ``photocraft-cli`` on PATH
(``scripts/agents-setup.sh`` installs a checksum-verified release).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

from . import config, models
from .db import SessionLocal
from .proc import tool_env

log = logging.getLogger("mediaforge.photocraft")

PHOTOCRAFT_DIR = Path(
    os.environ.get("MF_PHOTOCRAFT_DIR", str(config.EXPORTS_DIR / "photocraft"))
).resolve()
TIMEOUT_S = float(os.environ.get("MF_PHOTOCRAFT_TIMEOUT", "300"))

MAX_STEPS = 32
MAX_PARAMS_BYTES = 16 * 1024
OUTPUT_FORMATS = ("jpg", "png", "webp", "tif", "psd")

# Commands that only change pixels of the open document.
ALLOWED_PREFIXES = (
    "image.adjustments.",
    "layer.newAdjustmentLayer.",
    "image.imageRotation.",
    "filter.blur.",
    "filter.sharpen.",
    "filter.noise.",
    "filter.stylize.",
    "filter.distort.",
    "filter.pixelate.",
    "filter.render.",
)
ALLOWED_IDS = frozenset({
    "image.autoTone", "image.autoContrast", "image.autoColor",
    "image.crop", "image.trim", "image.imageSize", "image.canvasSize",
    "layer.flattenImage",
})
# Allowed by prefix but they load an external file (LUT / source image).
DENIED_IDS = frozenset({
    "image.adjustments.colorLookup",
    "image.adjustments.colorLookup.list",
    "image.adjustments.matchColor",
    "layer.newAdjustmentLayer.colorLookup",
})
_ID_RE = re.compile(r"^[A-Za-z0-9]+(\.[A-Za-z0-9]+)+$")
_PATH_KEYS = frozenset({
    "path", "paths", "file", "files", "filename", "dir", "directory", "folder",
    "src", "source", "url", "uri", "out", "output", "lut", "texture", "map",
})
_WIN_ABS = re.compile(r"^[A-Za-z]:[\\/]")

# Named looks an agent (or the UI) can ask for by name. Param ranges are the
# engine's own (crates/engine/src/adjust_params.rs, filters.rs).
RECIPES: dict[str, dict[str, Any]] = {
    "auto": {
        "description": "One-click Auto Tone + Auto Color, like Photoshop's.",
        "steps": [
            {"id": "image.autoTone", "params": {}},
            {"id": "image.autoColor", "params": {}},
        ],
    },
    "punchy": {
        "description": "Social-ready pop: auto contrast, vibrance, smart sharpen.",
        "steps": [
            {"id": "image.autoContrast", "params": {}},
            {"id": "image.adjustments.vibrance", "params": {"vibrance": 35, "saturation": 8}},
            {"id": "filter.sharpen.smartSharpen", "params": {"amount": 60, "radius": 1.0, "reduceNoise": 10}},
        ],
    },
    "clean": {
        "description": "Low-light rescue: reduce noise, then gentle sharpening.",
        "steps": [
            {"id": "filter.noise.reduceNoise", "params": {
                "strength": 5, "preserveDetails": 60, "reduceColorNoise": 50, "sharpenDetails": 20}},
            {"id": "filter.sharpen.smartSharpen", "params": {"amount": 40, "radius": 0.8, "reduceNoise": 20}},
        ],
    },
    "film": {
        "description": "Faded film: lifted blacks, soft highlights, warm mids, fine grain.",
        "steps": [
            {"id": "image.adjustments.curves", "params": {"points": [[0, 22], [64, 62], [192, 198], [255, 238]]}},
            {"id": "image.adjustments.colorBalance", "params": {"midtones": [8, 0, -10]}},
            {"id": "image.adjustments.vibrance", "params": {"vibrance": -10, "saturation": 0}},
            {"id": "filter.noise.addNoise", "params": {"amount": 3, "distribution": "gaussian", "monochromatic": True}},
        ],
    },
    "warm": {
        "description": "Golden-hour warmth with a touch of vibrance.",
        "steps": [
            {"id": "image.adjustments.colorBalance", "params": {"midtones": [10, 0, -12]}},
            {"id": "image.adjustments.vibrance", "params": {"vibrance": 15, "saturation": 0}},
        ],
    },
    "bw": {
        "description": "Rich black & white with a contrast lift.",
        "steps": [
            {"id": "image.adjustments.blackWhite", "params": {}},
            {"id": "image.adjustments.brightnessContrast", "params": {"contrast": 18}},
        ],
    },
}


class PhotoCraftError(ValueError):
    """Bad request (unknown recipe, refused command, bad params)."""


class PhotoCraftUnavailable(RuntimeError):
    """photocraft-cli is not installed or not runnable."""


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------
def find_cli() -> Optional[str]:
    """Path to photocraft-cli, or None when it is not installed."""
    override = os.environ.get("MF_PHOTOCRAFT_CLI", "").strip()
    if override:
        return override if os.path.isfile(override) and os.access(override, os.X_OK) else None
    found = shutil.which("photocraft-cli")
    if found:
        return found
    # Where scripts/agents-setup.sh links it; the desktop launcher's PATH may
    # not include ~/.local/bin.
    local = Path.home() / ".local" / "bin" / "photocraft-cli"
    return str(local) if local.is_file() and os.access(local, os.X_OK) else None


def _run_cli(args: list[str], timeout: float, cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    cli = find_cli()
    if cli is None:
        raise PhotoCraftUnavailable(
            "photocraft-cli not found; run scripts/agents-setup.sh or set MF_PHOTOCRAFT_CLI"
        )
    try:
        return subprocess.run(
            [cli, *args], capture_output=True, text=True, timeout=timeout,
            env=tool_env(), cwd=cwd, stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise PhotoCraftUnavailable(f"photocraft-cli timed out after {timeout:.0f}s") from exc
    except OSError as exc:
        raise PhotoCraftUnavailable(f"photocraft-cli failed to start: {exc}") from exc


def status() -> dict[str, Any]:
    cli = find_cli()
    if cli is None:
        return {"available": False, "path": None, "version": None,
                "recipes": sorted(RECIPES)}
    version = None
    try:
        proc = _run_cli(["--version"], timeout=15)
        if proc.returncode == 0:
            version = proc.stdout.strip()
    except PhotoCraftUnavailable as exc:
        log.warning("photocraft-cli --version failed: %s", exc)
    return {"available": version is not None, "path": cli, "version": version,
            "recipes": sorted(RECIPES)}


def list_commands(filter_text: str = "") -> list[dict[str, Any]]:
    """Engine commands MediaForge will forward, with their parameter docs."""
    args = ["commands", "--json"]
    if filter_text:
        args += ["--filter", filter_text]
    proc = _run_cli(args, timeout=30)
    if proc.returncode != 0:
        raise PhotoCraftUnavailable(proc.stderr.strip() or "photocraft-cli commands failed")
    try:
        items = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise PhotoCraftUnavailable(f"unreadable command list: {exc}") from exc
    return [
        {"id": c.get("id"), "label": c.get("label"), "params": c.get("params")}
        for c in items if isinstance(c, dict) and is_allowed(str(c.get("id", "")))
    ]


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------
def is_allowed(cmd_id: str) -> bool:
    if not _ID_RE.match(cmd_id) or cmd_id in DENIED_IDS:
        return False
    return cmd_id in ALLOWED_IDS or cmd_id.startswith(ALLOWED_PREFIXES)


def _check_value(cmd_id: str, value: Any, depth: int = 0) -> None:
    if depth > 6:
        raise PhotoCraftError(f"{cmd_id}: params nested too deeply")
    if isinstance(value, dict):
        for k, v in value.items():
            if str(k).lower() in _PATH_KEYS:
                raise PhotoCraftError(f"{cmd_id}: param {k!r} would reference a file; not allowed")
            _check_value(cmd_id, v, depth + 1)
    elif isinstance(value, list):
        for v in value:
            _check_value(cmd_id, v, depth + 1)
    elif isinstance(value, str):
        if value.startswith(("/", "\\", "~")) or _WIN_ABS.match(value) or ".." in value:
            raise PhotoCraftError(f"{cmd_id}: path-like string params are not allowed")


def validate_steps(steps: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    """Normalise ``[{"id", "params"}]`` and enforce the command allow-list."""
    if not steps:
        raise PhotoCraftError("no steps given")
    if len(steps) > MAX_STEPS:
        raise PhotoCraftError(f"too many steps ({len(steps)} > {MAX_STEPS})")
    out: list[tuple[str, dict[str, Any]]] = []
    for step in steps:
        cmd_id = str(step.get("id", ""))
        params = step.get("params") or {}
        if not is_allowed(cmd_id):
            raise PhotoCraftError(
                f"command {cmd_id!r} is not allowed; MediaForge only forwards in-memory "
                "adjustments/filters (see GET /api/photocraft/commands)"
            )
        if not isinstance(params, dict):
            raise PhotoCraftError(f"{cmd_id}: params must be an object")
        if len(json.dumps(params)) > MAX_PARAMS_BYTES:
            raise PhotoCraftError(f"{cmd_id}: params too large")
        _check_value(cmd_id, params)
        out.append((cmd_id, params))
    return out


def resolve_steps(recipe: Optional[str], steps: Optional[list[dict[str, Any]]]) -> list[tuple[str, dict[str, Any]]]:
    """A recipe, explicit steps, or a recipe followed by extra steps."""
    combined: list[dict[str, Any]] = []
    if recipe:
        if recipe not in RECIPES:
            raise PhotoCraftError(f"unknown recipe {recipe!r}; expected one of {', '.join(sorted(RECIPES))}")
        combined.extend(RECIPES[recipe]["steps"])
    combined.extend(steps or [])
    return validate_steps(combined)


def output_path(asset_id: int, steps: list[tuple[str, dict[str, Any]]],
                fmt: str, quality: Optional[int], label: str = "") -> Path:
    """Deterministic output name: the same edit of the same asset is cached."""
    digest = hashlib.sha256(
        json.dumps([steps, fmt, quality], sort_keys=True).encode()
    ).hexdigest()[:12]
    tag = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:24]
    stem = f"{asset_id}_{tag}_{digest}" if tag else f"{asset_id}_{digest}"
    return PHOTOCRAFT_DIR / f"{stem}.{fmt}"


def prepare(asset_id: int, recipe: Optional[str], steps: Optional[list[dict[str, Any]]],
            fmt: str = "jpg", quality: Optional[int] = 92) -> dict[str, Any]:
    """Validate a request up front so the API can 4xx before queuing a job."""
    fmt = fmt.lower().lstrip(".")
    if fmt == "jpeg":
        fmt = "jpg"
    if fmt not in OUTPUT_FORMATS:
        raise PhotoCraftError(f"format must be one of {', '.join(OUTPUT_FORMATS)}")
    if quality is not None and not 1 <= int(quality) <= 100:
        raise PhotoCraftError("quality must be 1-100")
    resolved = resolve_steps(recipe, steps)
    with SessionLocal() as db:
        asset = db.get(models.Asset, asset_id)
        if asset is None:
            raise LookupError(f"asset {asset_id} not found")
        if asset.kind != "photo":
            raise PhotoCraftError("PhotoCraft edits only apply to photos")
        src = asset.path
    if not src or not os.path.isfile(src):
        raise LookupError(f"asset {asset_id} file missing on disk")
    out = output_path(asset_id, resolved, fmt, quality, recipe or "custom")
    return {"asset_id": asset_id, "src": src, "steps": resolved,
            "format": fmt, "quality": quality, "output_path": str(out)}


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------
def apply(prepared: dict[str, Any], job=None) -> str:
    """Run the edit; returns the output path. Blocking (call from an executor)."""
    out = Path(prepared["output_path"])
    if out.is_file():
        if job is not None:
            job(1.0, "cached")
        return str(out)
    PHOTOCRAFT_DIR.mkdir(parents=True, exist_ok=True)
    if job is not None:
        job(0.05, f"photocraft: {len(prepared['steps'])} step(s)")

    args = ["run", prepared["src"]]
    for cmd_id, params in prepared["steps"]:
        args += ["--cmd", cmd_id, "--params", json.dumps(params, separators=(",", ":"))]

    with tempfile.TemporaryDirectory(prefix="pc_", dir=str(PHOTOCRAFT_DIR)) as scratch:
        tmp_out = Path(scratch) / out.name
        args += ["--out", str(tmp_out)]
        if prepared.get("quality") and prepared["format"] in ("jpg", "webp"):
            args += ["--quality", str(int(prepared["quality"]))]
        proc = _run_cli(args, timeout=TIMEOUT_S, cwd=scratch)
        if proc.returncode != 0 or not tmp_out.is_file():
            msg = (proc.stderr or proc.stdout or "").strip().splitlines()
            raise RuntimeError(f"photocraft-cli exit {proc.returncode}: {' | '.join(msg[-3:])}")
        os.replace(tmp_out, out)
    if job is not None:
        job(1.0, f"wrote {out.name}")
    log.info("photocraft wrote %s", out)
    return str(out)
