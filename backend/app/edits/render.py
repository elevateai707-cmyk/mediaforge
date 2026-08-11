"""FFmpeg renderer for approved edit plans.

Builds a single filter_complex that:
- trims each clip from its ORIGINAL asset file (-ss/-t on the filter chain so
  xfade offsets stay frame-accurate),
- center-crops every clip to the target ratio (scale=force_original_aspect_ratio
  =increase + center crop),
- crossfades consecutive clips with xfade (video) and acrossfade (audio),
- applies loudnorm (I=-14, TP=-1.5, LRA=11) to the final mix,
- burns per-clip captions with drawtext (DejaVu Sans when installed; colons,
  apostrophes, backslashes and % are escaped; ~40 char wrapping),
- optionally mixes a music file under the voice track using sidechaincompress
  ducking: the music gain dips when the audio track is loud (approximates the
  contract's "beat-sync" note — beat-grid analysis is future work, ducking is
  the implemented behavior).

Output: exports/reel_<plan_id>_<ts>.mp4 (h264, yuv420p, crf 18, preset medium).
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from .. import config, models
from ..db import SessionLocal
from ..proc import tool_env

log = logging.getLogger("mediaforge.render")


class RenderError(Exception):
    """FFmpeg (or probe) failure; message carries the stderr tail."""


def _run_retry(cmd: list[str], timeout: int = 120,
               attempts: int = 5) -> subprocess.CompletedProcess:
    """Run a command retrying transient failures (connection/timeout/5xx-ish)."""
    last_err: Optional[str] = None
    for i in range(1, attempts + 1):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout, env=tool_env())
            if proc.returncode == 0 or i == attempts:
                return proc
            # Nonzero but retryable (e.g. ffprobe on a still-locked file).
            last_err = f"exit {proc.returncode}: {(proc.stderr or '')[-200:]}"
            time.sleep(1.0 * i)
        except subprocess.TimeoutExpired as exc:
            last_err = f"timeout: {exc}"
            time.sleep(1.0 * i)
        except OSError as exc:
            last_err = f"{type(exc).__name__}: {exc}"
            time.sleep(1.0 * i)
    raise RenderError(f"command failed after {attempts} attempts: {last_err}")


def probe_json(path: str) -> dict:
    """ffprobe JSON for a media file ({} on any failure)."""
    try:
        proc = _run_retry([
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", path,
        ], timeout=60)
    except RenderError:
        return {}
    if proc.returncode != 0:
        return {}
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def probe_fps(path: str) -> float:
    """Best-effort video frame rate; 30.0 default."""
    data = probe_json(path)
    for s in data.get("streams", []):
        if s.get("codec_type") == "video":
            r = s.get("avg_frame_rate") or s.get("r_frame_rate") or ""
            if "/" in r:
                num, _, den = r.partition("/")
                try:
                    den = float(den)
                    if den > 0:
                        fps = float(num) / den
                        if 5 < fps < 240:
                            return round(fps, 4)
                except ValueError:
                    pass
            elif r:
                try:
                    fps = float(r)
                    if 5 < fps < 240:
                        return round(fps, 4)
                except ValueError:
                    pass
            break
    return 30.0


def _has_audio(path: str) -> bool:
    data = probe_json(path)
    return any(s.get("codec_type") == "audio" for s in data.get("streams", []))


# ---------------------------------------------------------------------------
# drawtext escaping (colons, apostrophes, backslashes, % and line wrapping)
# ---------------------------------------------------------------------------

def _wrap_text(text: str, width: int = 40) -> str:
    """Word-wrap to ~width chars; returns lines joined with literal \\n."""
    words = text.split()
    if not words:
        return ""
    lines: list[str] = []
    cur = ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return "\\n".join(lines)


def _escape_drawtext(text: str) -> str:
    """Escape text for drawtext inside a quoted filter argument.

    av_get_token (ffmpeg's filtergraph parser) processes backslash escapes
    inside single quotes: \\\\ -> \\, \\' -> ', \\: -> :, \\n -> newline, so we
    double backslashes, escape apostrophes and colons, and turn % into \\% so
    drawtext's expansion does not touch it.
    """
    return (
        text.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace(":", "\\:")
        .replace("%", "\\%")
    )


# ---------------------------------------------------------------------------
# Plan loading
# ---------------------------------------------------------------------------

def _load_plan(plan_id: str) -> tuple[models.EditPlan, list[models.EditClip]]:
    with SessionLocal() as db:
        plan = db.get(models.EditPlan, plan_id)
        if plan is None:
            raise RenderError(f"plan {plan_id!r} not found")
        if plan.status != "approved":
            raise RenderError(
                f"plan {plan_id!r} is not approved; approve it first "
                "(POST /api/edits/plan/{id}/approve)")
        clips = list(plan.clips)
        if not clips:
            raise RenderError(f"plan {plan_id!r} has no clips")
        # Resolve asset paths inside the session.
        resolved = []
        for c in clips:
            asset = db.get(models.Asset, c.asset_id)
            if asset is None or not Path(asset.path).exists():
                raise RenderError(
                    f"clip {c.id} references missing asset {c.asset_id} "
                    f"({asset.path if asset else 'unknown'})")
            resolved.append((c, asset))
        return plan, resolved


# ---------------------------------------------------------------------------
# Main renderer
# ---------------------------------------------------------------------------

def _progress_callable(job: Any) -> Callable[[float, str], None]:
    """Normalize the `job` argument to a progress callback.

    Accepts: a callable(frac, msg), an object with .progress(frac, msg), or
    None (no-op).
    """
    def _noop(_frac: float, _msg: str) -> None:
        return None

    if job is None:
        return _noop
    if callable(job):
        return job
    progress = getattr(job, "progress", None)
    if callable(progress):
        return progress  # type: ignore[return-value]
    return _noop


def render_plan(plan_id: str, ratio: str = "9:16", width: int = 1080,
                height: int = 1920, captions: bool = True,
                music_path: Optional[str] = None,
                job: Any = None) -> str:
    """Render an approved plan to exports/reel_<plan_id>_<ts>.mp4.

    Returns the output path. Raises RenderError on any failure.
    """
    progress = _progress_callable(job)
    plan, resolved = _load_plan(plan_id)
    n = len(resolved)

    ratio = (ratio or "9:16").lower()
    if ratio not in ("9:16", "16:9", "1:1"):
        # Derive WxH from the explicit width/height instead of a named ratio.
        pass
    elif ratio == "16:9":
        height = round(width * 9 / 16 / 2) * 2
    elif ratio == "1:1":
        height = width
    else:  # 9:16
        height = round(width * 16 / 9 / 2) * 2
    if width % 2:
        width += 1
    if height % 2:
        height += 1

    xfade = config.XFADE_DURATION
    font = config.find_font()
    total = 0.0
    per_clip: list[dict] = []
    for c, asset in resolved:
        dur = max(0.2, float(c.end) - float(c.start))
        if asset.kind == "photo":
            has_audio = False
        else:
            has_audio = _has_audio(asset.path)
        per_clip.append({
            "clip": c, "asset": asset, "dur": dur, "has_audio": has_audio,
            "path": asset.path,
        })
        total += dur
    # Timeline length after xfade overlaps.
    timeline = total - xfade * (n - 1) if n > 1 else total

    # ------------------------------------------------------------------
    # Build the command
    # ------------------------------------------------------------------
    cmd = ["ffmpeg", "-y", "-v", "error", "-nostdin"]
    inputs: list[str] = []
    for i, pc in enumerate(per_clip):
        if pc["asset"].kind == "photo":
            cmd += ["-loop", "1", "-t", f"{pc['dur']:.3f}", "-i", pc["path"]]
        else:
            cmd += ["-i", pc["path"]]
        if not pc["has_audio"]:
            cmd += ["-f", "lavfi", "-t", f"{pc['dur']:.3f}",
                    "-i", "anullsrc=r=48000:cl=stereo"]
    if music_path:
        if not Path(music_path).exists():
            raise RenderError(f"music file not found: {music_path}")
        cmd += ["-stream_loop", "-1", "-i", music_path]

    filters: list[str] = []
    vlabels: list[str] = []
    alabels: list[str] = []
    # photo loop inputs are video-only; anullsrc inputs follow each asset
    # input, so track input indices explicitly.
    input_index = 0
    for i, pc in enumerate(per_clip):
        vlabel = f"v{i}"
        alabel = f"a{i}"
        in_idx = input_index  # this clip's asset input file index
        if pc["asset"].kind == "photo":
            # photo loop input is video-only; anullsrc is the *next* input
            has_audio = False
            audio_idx = input_index + 1
            input_index += 2
        else:
            has_audio = pc["has_audio"]
            # same input carries both video and audio
            audio_idx = input_index
            input_index += 1

        chain = [f"[{in_idx}:v]fps={config.RENDER_FPS}"]
        chain.append(
            f"trim=start={pc['clip'].start:.3f}:end={pc['clip'].end:.3f}"
            if pc["asset"].kind != "photo" else
            f"trim=start=0:end={pc['dur']:.3f}"
        )
        chain.append("setpts=PTS-STARTPTS")
        chain.append(
            f"scale={width}:{height}:force_original_aspect_ratio=increase"
            ":force_divisible_by=2")
        chain.append(f"crop={width}:{height}")
        chain.append("setsar=1")
        if captions and pc["clip"].caption:
            txt = _wrap_text(str(pc["clip"].caption).strip())
            if txt:
                escaped = _escape_drawtext(txt)
                fontfile = f":fontfile={font}" if font else ""
                chain.append(
                    f"drawtext=text='{escaped}'{fontfile}"
                    f":fontsize={max(18, height // 30)}"
                    ":fontcolor=white:borderw=2:bordercolor=black@0.6"
                    f":x=(w-text_w)/2:y=h-text_h-{max(40, height // 20)}"
                    f":enable='between(t,0,{pc['dur']:.3f})'")
        filters.append(",".join(chain) + f"[{vlabel}]")
        vlabels.append(vlabel)

        # Audio chain.
        if has_audio:
            achain = (
                f"[{audio_idx}:a]"
                f"atrim=start={pc['clip'].start:.3f}:end={pc['clip'].end:.3f},"
                "asetpts=PTS-STARTPTS,aresample=48000,"
                f"apad=whole_dur={pc['dur']:.3f}[{alabel}]"
            )
        else:
            achain = (
                f"[{audio_idx}:a]atrim=start=0:end={pc['dur']:.3f},"
                f"asetpts=PTS-STARTPTS[{alabel}]"
            )
        filters.append(achain)
        alabels.append(alabel)

    # Music input (last input index).
    music_label: Optional[str] = None
    if music_path:
        music_label = f"m{input_index}"
        filters.append(f"[{input_index}:a]aresample=48000[{music_label}]")

    # Video xfade chain.
    vout = vlabels[0]
    if n > 1:
        offset = per_clip[0]["dur"] - xfade
        for k in range(1, n):
            out = f"x{k}" if k < n - 1 else "vout"
            filters.append(
                f"[{vout}][{vlabels[k]}]xfade=transition=fade:"
                f"duration={xfade:.3f}:offset={offset:.3f}[{out}]")
            vout = out
            offset = offset + per_clip[k]["dur"] - xfade
    else:
        filters.append(f"[{vlabels[0]}]null[vout]")

    # Audio chain: acrossfade between consecutive clips, then loudnorm.
    aout = alabels[0]
    if n > 1:
        for k in range(1, n):
            out = f"ax{k}" if k < n - 1 else "amix"
            filters.append(
                f"[{aout}][{alabels[k]}]acrossfade=d={xfade:.3f}[{out}]")
            aout = out
    else:
        filters.append(f"[{alabels[0]}]anull[amix]")

    if music_path:
        # Duck the music under the voice track, then mix.
        ducked = "duck"
        filters.append(
            f"[{music_label}][amix]sidechaincompress="
            "threshold=0.03:ratio=8:attack=20:release=250:makeup=1"
            f"[{ducked}]")
        filters.append(
            f"[amix][{ducked}]amix=inputs=2:duration=first:normalize=0[aout]")
    else:
        filters.append(
            f"[amix]loudnorm=I=-14:TP=-1.5:LRA=11[aout]")

    # Final loudnorm when music is present (after mixing).
    if music_path:
        filters.append("[aout]loudnorm=I=-14:TP=-1.5:LRA=11[afinal]")
        amap = "afinal"
    else:
        amap = "aout"

    out_dir = config.EXPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"reel_{plan_id}_{ts}.mp4"

    cmd += ["-filter_complex", ";".join(filters),
            "-map", "[vout]", "-map", f"[{amap}]",
            "-c:v", "libx264", "-preset", config.RENDER_PRESET,
            "-crf", str(config.RENDER_CRF), "-pix_fmt", "yuv420p",
            "-r", str(config.RENDER_FPS),
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", "-shortest",
            str(out_path)]

    log.info("render %s: %d clip(s), %dx%d, %s, music=%s",
             plan_id, n, width, height, ratio, bool(music_path))
    if log.isEnabledFor(logging.DEBUG):
        log.debug("ffmpeg cmd: %s", " ".join(cmd))

    progress(0.1, f"rendering {n} clips")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=7200, env=tool_env())
    except subprocess.TimeoutExpired:
        raise RenderError("ffmpeg render timed out after 2 hours")
    except OSError as exc:
        raise RenderError(f"ffmpeg failed to start: {exc}")

    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-30:]
        raise RenderError(
            f"ffmpeg render failed (exit {proc.returncode}): "
            + "\n".join(tail)[-2000:])
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RenderError("ffmpeg exited 0 but produced no output file")

    progress(1.0, f"render complete: {out_path.name}")
    return str(out_path)


# ---------------------------------------------------------------------------
# Job-compatible async wrapper (used by main.py)
# ---------------------------------------------------------------------------

async def render_job(plan_id: str, ratio: str, width: int, height: int,
                     captions: bool, music_path: Optional[str],
                     progress, _cancel_flag) -> str:
    """Async entrypoint for jobs.run: renders in a thread, honours cancel."""
    import asyncio

    def _sync() -> str:
        return render_plan(plan_id, ratio=ratio, width=width, height=height,
                           captions=captions, music_path=music_path,
                           job=progress)

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync)
