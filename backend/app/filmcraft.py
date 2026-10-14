"""FilmCraft bridge: turn an edit plan into a real, editable NLE project (+ MP4).

FilmCraft (https://github.com/storytold/filmcraft, MIT OR Apache-2.0) is a
pure-Rust, Premiere-style editor with its own codecs. Its ``filmcraft-cli``
opens/saves ``.fcproj`` projects and runs engine commands headlessly, so
MediaForge can hand the user a finished first cut they can keep editing in a
real timeline (multicam, Lumetri, keyframes, titles) instead of a flat MP4.
Elevate Vision Studio is our rebranded build of it (``studio/``); either
binary works.

How a plan becomes a project (each phase is one CLI process that loads, runs a
JSON-lines script, and saves the project, because later commands need the ids
earlier ones return):

1. new project, import every distinct source (HEIC stills are converted to
   JPEG first; FilmCraft reads PNG/JPEG/TIFF/WebP/GIF/BMP stills);
2. new sequence at the plan's ratio, each clip placed back to back from its
   in-point, plus a caption text clip per captioned clip;
3. fill-frame (MediaForge's centre crop), cross dissolves at the cuts, caption
   styling (centred, bold, outlined, backed);
4. optionally export H.264 MP4 (only for approved plans, like /api/render).

Safety: every command is generated here from the plan; nothing an agent sends
is forwarded to the CLI, which has no path sandbox of its own. Outputs go to
``exports/filmcraft/<plan_id>/``; originals are only ever read.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from . import config, models
from .db import SessionLocal
from .proc import tool_env

log = logging.getLogger("mediaforge.filmcraft")

TICKS_PER_SECOND = 254_016_000_000
FILMCRAFT_DIR = Path(
    os.environ.get("MF_FILMCRAFT_DIR", str(config.EXPORTS_DIR / "filmcraft"))
).resolve()
TIMEOUT_S = float(os.environ.get("MF_FILMCRAFT_TIMEOUT", "3600"))
CLI_NAMES = ("evstudio-cli", "filmcraft-cli")
RATIO_SIZES = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}
FPS = 30
DISSOLVE_FRAMES = 15
STILL_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".gif", ".bmp"}

Progress = Callable[[float, str], None]

# One build per plan at a time: phases rewrite the same project file.
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _plan_lock(plan_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(plan_id, threading.Lock())


class FilmCraftError(RuntimeError):
    """A plan that can't be built, or a CLI phase that failed."""


class FilmCraftUnavailable(RuntimeError):
    """No filmcraft-cli / evstudio-cli installed."""


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------
def find_cli() -> Optional[str]:
    override = os.environ.get("MF_FILMCRAFT_CLI", "").strip()
    if override:
        return override if os.path.isfile(override) and os.access(override, os.X_OK) else None
    for name in CLI_NAMES:
        found = shutil.which(name)
        if found:
            return found
    for name in CLI_NAMES:  # where scripts/studio-setup.sh installs it
        local = Path.home() / ".local" / "bin" / name
        if local.is_file() and os.access(local, os.X_OK):
            return str(local)
    return None


def _cli(args: list[str], timeout: float = 120, cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    cli = find_cli()
    if cli is None:
        raise FilmCraftUnavailable("FilmCraft not installed; run scripts/studio-setup.sh or set MF_FILMCRAFT_CLI")
    try:
        return subprocess.run([cli, *args], capture_output=True, text=True, timeout=timeout,
                              env=tool_env(), cwd=cwd, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        raise FilmCraftError(f"FilmCraft timed out after {timeout:.0f}s") from exc
    except OSError as exc:
        raise FilmCraftUnavailable(f"FilmCraft failed to start: {exc}") from exc


def status() -> dict[str, Any]:
    cli = find_cli()
    version = None
    if cli:
        try:
            proc = _cli(["--version"], timeout=15)
            version = proc.stdout.strip() if proc.returncode == 0 else None
        except (FilmCraftError, FilmCraftUnavailable) as exc:
            log.warning("filmcraft --version failed: %s", exc)
    return {"available": version is not None, "path": cli, "version": version}


def ticks(seconds: float) -> int:
    return int(round(seconds * TICKS_PER_SECOND))


# ---------------------------------------------------------------------------
# plan -> sources
# ---------------------------------------------------------------------------
def load_plan(plan_id: str, require_approved: bool) -> dict[str, Any]:
    with SessionLocal() as db:
        plan = db.get(models.EditPlan, plan_id)
        if plan is None:
            raise LookupError(f"plan {plan_id!r} not found")
        if require_approved and plan.status != "approved":
            raise PermissionError(f"plan {plan_id!r} is not approved; approve it before rendering")
        clips = []
        for c in sorted(plan.clips, key=lambda c: (c.position or 0, c.id)):
            asset = db.get(models.Asset, c.asset_id)
            if asset is None or not asset.path or not Path(asset.path).is_file():
                raise FilmCraftError(f"clip references missing asset {c.asset_id}")
            clips.append({
                "asset_id": asset.id, "kind": asset.kind, "path": asset.path,
                "start": max(0.0, float(c.start or 0.0)),
                "dur": max(0.2, float(c.end) - float(c.start or 0.0)),
                "caption": (c.caption or "").strip(),
                "transition": (c.transition or "crossfade").lower(),
            })
        if not clips:
            raise FilmCraftError(f"plan {plan_id!r} has no clips")
        return {"plan_id": plan.id, "status": plan.status,
                "ratio": plan.target_ratio or "9:16", "clips": clips}


def _source_for(clip: dict[str, Any], media_dir: Path) -> tuple[str, Optional[str]]:
    """Path FilmCraft should import for this clip, plus a warning if substituted."""
    path = clip["path"]
    if clip["kind"] == "photo" and Path(path).suffix.lower() not in STILL_EXTS:
        from .images import open_rgb  # HEIC/HEIF-aware
        out = media_dir / f"{clip['asset_id']}.jpg"
        if not out.is_file():
            media_dir.mkdir(parents=True, exist_ok=True)
            open_rgb(path).save(out, "JPEG", quality=95)
        return str(out), None
    if clip["kind"] == "video":
        probe = _cli(["probe", path, "--compact"], timeout=60)
        if probe.returncode != 0:
            proxy = config.PROXIES_DIR / str(clip["asset_id"]) / "proxy.mp4"
            if proxy.is_file():
                return str(proxy), f"asset {clip['asset_id']}: FilmCraft can't decode the original; used the 720p proxy"
            raise FilmCraftError(f"FilmCraft can't read asset {clip['asset_id']} ({probe.stderr.strip()[-200:]})")
    return path, None


# ---------------------------------------------------------------------------
# CLI phases
# ---------------------------------------------------------------------------
def _run_phase(project: Path, steps: list[dict[str, Any]], create: bool = False,
               keep_going: bool = False) -> list[dict[str, Any]]:
    script = project.parent / ".phase.jsonl"
    script.write_text("".join(json.dumps(s) + "\n" for s in steps), encoding="utf-8")
    args = (["--save-as", str(project)] if create else ["--project", str(project), "--save"])
    args += ["--compact"] + (["--keep-going"] if keep_going else []) + ["run", str(script)]
    proc = _cli(args, timeout=600, cwd=str(project.parent))
    results = []
    for line in proc.stdout.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and "line" in row:
            results.append(row)
    script.unlink(missing_ok=True)
    if proc.returncode != 0 and not keep_going:
        bad = next((r for r in results if not r.get("ok")), None)
        why = (bad or {}).get("error") or proc.stderr.strip()[-300:]
        raise FilmCraftError(f"FilmCraft: {why}")
    if len(results) != len(steps) and not keep_going:
        raise FilmCraftError(f"FilmCraft ran {len(results)} of {len(steps)} steps: {proc.stderr.strip()[-300:]}")
    return results


def build(plan_id: str, ratio: Optional[str] = None, captions: bool = True,
          render: bool = False, job: Optional[Progress] = None) -> dict[str, Any]:
    """Build exports/filmcraft/<plan>/<plan>.fcproj (+ .mp4 when render). Blocking."""
    with _plan_lock(plan_id):
        return _build(plan_id, ratio, captions, render, job)


def _build(plan_id: str, ratio: Optional[str], captions: bool, render: bool,
           job: Optional[Progress]) -> dict[str, Any]:
    progress = job or (lambda _f, _m: None)
    plan = load_plan(plan_id, require_approved=render)
    ratio = ratio or plan["ratio"]
    if ratio not in RATIO_SIZES:
        raise FilmCraftError(f"ratio must be one of {', '.join(RATIO_SIZES)}")
    width, height = RATIO_SIZES[ratio]

    out_dir = FILMCRAFT_DIR / plan_id
    out_dir.mkdir(parents=True, exist_ok=True)
    project = out_dir / f"{plan_id}.fcproj"
    warnings: list[str] = []

    # 1. project + media
    progress(0.05, "FilmCraft: importing media")
    sources: list[str] = []
    resolved: dict[int, str] = {}  # asset id -> source (probe each file once)
    for clip in plan["clips"]:
        if clip["asset_id"] not in resolved:
            src, warn = _source_for(clip, out_dir / "media")
            resolved[clip["asset_id"]] = src
            if warn:
                warnings.append(warn)
        src = clip["source"] = resolved[clip["asset_id"]]
        if src not in sources:
            sources.append(src)
    res = _run_phase(project, [
        {"id": "file.newProject", "params": {"name": f"MediaForge {plan_id}"}},
        {"id": "file.import", "params": {"paths": sources}},
    ], create=True)
    imported = res[1]["result"]
    if imported.get("errors"):
        raise FilmCraftError(f"FilmCraft import failed: {imported['errors']}")
    item_of = dict(zip(sources, imported["items"]))

    # 2. sequence, clips, caption text clips
    progress(0.25, "FilmCraft: building the timeline")
    steps: list[dict[str, Any]] = [{"id": "file.newSequence", "params": {
        "name": f"{ratio} reel", "width": width, "height": height, "fps": FPS}}]
    t = 0
    for clip in plan["clips"]:
        dur = ticks(clip["dur"])
        place = {"item": item_of[clip["source"]], "track": "V1", "time": t, "duration": dur}
        if clip["kind"] == "video":
            place["sourceIn"] = ticks(clip["start"])
        steps.append({"id": "timeline.place", "params": place})
        clip["time"] = t
        t += dur
    caption_rows = []
    if captions:
        short = min(width, height)
        for clip in plan["clips"]:
            if clip["caption"]:
                size = round(short * 0.055)
                caption_rows.append(len(steps))
                steps.append({"id": "graphics.newText", "params": {
                    "text": clip["caption"], "size": size,
                    # Two lines of text plus padding; the background fills the box.
                    "box": [round(width * 0.86), round(size * 2.9)],
                    "position": [round(width * 0.07), round(height * 0.76)],
                    "seconds": clip["dur"], "time": clip["time"]}})
    res = _run_phase(project, steps)
    video_clips = [r["result"]["clips"][0] for r in res[1:1 + len(plan["clips"])]]
    text_clips = [res[i]["result"]["clip"] for i in caption_rows]

    # 3. look: fill frame, dissolves, caption style (cosmetic: keep going)
    progress(0.45, "FilmCraft: transitions and captions")
    steps = [{"id": "clip.fillFrame", "params": {"clips": video_clips}}]
    for i, clip in enumerate(plan["clips"][:-1]):
        if clip["transition"] in ("crossfade", "fade"):
            steps.append({"id": "sequence.applyVideoTransition", "params": {
                "clip": video_clips[i], "effect": "cross_dissolve",
                "frames": DISSOLVE_FRAMES, "edge": "out"}})
    for tc in text_clips:
        steps.append({"id": "graphics.set", "params": {"clip": tc, "props": {
            "font_style": "Bold", "align": "center", "fill_color": "#ffffff",
            "stroke": True, "stroke_width": 5, "background": True}}})
    for row in _run_phase(project, steps, keep_going=True):
        if not row.get("ok"):
            warnings.append(f"{row.get('id')}: {row.get('error')}")

    result: dict[str, Any] = {"plan_id": plan_id, "ratio": ratio, "project_path": str(project),
                              "clips": len(video_clips), "captions": len(text_clips),
                              "seconds": round(t / TICKS_PER_SECOND, 3), "warnings": warnings,
                              "output_path": None}

    # 4. optional render
    if render:
        progress(0.55, "FilmCraft: exporting H.264")
        mp4 = out_dir / f"{plan_id}_{ratio.replace(':', 'x')}.mp4"
        mp4.unlink(missing_ok=True)
        proc = _cli(["--project", str(project), "--compact", "export", str(mp4), "--format", "h264"],
                    timeout=TIMEOUT_S, cwd=str(out_dir))
        if proc.returncode != 0 or not mp4.is_file():
            raise FilmCraftError(f"FilmCraft export failed: {(proc.stderr or proc.stdout).strip()[-300:]}")
        result["output_path"] = str(mp4)
    # Job rows keep no return value; agents read this via /exports/.../build.json.
    (out_dir / "build.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    progress(1.0, "FilmCraft project ready")
    log.info("filmcraft built %s", project)
    return result


def planned_paths(plan_id: str, ratio: str, render: bool) -> dict[str, Optional[str]]:
    out_dir = FILMCRAFT_DIR / plan_id
    return {"project_path": str(out_dir / f"{plan_id}.fcproj"),
            "output_path": str(out_dir / f"{plan_id}_{ratio.replace(':', 'x')}.mp4") if render else None}
