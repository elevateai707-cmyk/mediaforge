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
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

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

# DejaVu Sans averages ~0.52 em per character; keep a margin so a wide line
# (caps, wide glyphs) still clears the frame edge.
_AVG_CHAR_EM = 0.52
_SAFE_MARGIN = 0.86


def _chars_per_line(frame_width: int, fontsize: int) -> int:
    """How many characters fit on one line at this font size."""
    usable = frame_width * _SAFE_MARGIN
    return max(12, int(usable / max(1.0, fontsize * _AVG_CHAR_EM)))


def _wrap_text(text: str, width: int = 28) -> str:
    """Word-wrap to ~width chars, joined with REAL newlines.

    This used to join with a literal backslash-n. _escape_drawtext then
    doubled the backslash, and by the time ffmpeg's filtergraph parser was
    done the escape had collapsed to a bare 'n' — captions rendered as
    "Saint Kittsnand Nevis". Real newlines go through the textfile= path
    below, which needs no escaping at all.
    """
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
    return "\n".join(lines)


def _escape_filter_path(path: str) -> str:
    """Escape a path used as a drawtext option value."""
    return path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _cleanup_caption_files(plan_id: str) -> None:
    """Remove this plan's caption temp files (best-effort)."""
    try:
        for f in config.RENDER_TMP_DIR.glob(f"cap_{plan_id}_*.txt"):
            f.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001 - cleanup must never fail a render
        log.debug("caption cleanup skipped: %s", exc)


def _caption_file(plan_id: str, index: int, text: str) -> str:
    """Write one caption to a temp file for drawtext's textfile= option.

    Passing captions through textfile= sidesteps filtergraph escaping
    entirely: no quoting, no colon/apostrophe/percent handling, no newline
    mangling. Only the path itself needs escaping.
    """
    config.RENDER_TMP_DIR.mkdir(parents=True, exist_ok=True)
    out = config.RENDER_TMP_DIR / f"cap_{plan_id}_{index}.txt"
    out.write_text(text, encoding="utf-8")
    return str(out)


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


def _render_plan(plan_id: str, ratio: Optional[str] = None,
                width: Optional[int] = None, height: Optional[int] = None,
                captions: bool = True, music_path: Optional[str] = None,
                job: Any = None, caption_style: Optional[str] = None,
                effects: bool = True, reframe: str = "smart",
                safe_zones: Optional[Mapping[str, Any]] = None,
                guide_overlay: bool = False, music_start: Optional[float] = None,
                _reframe_analysis: Optional[Mapping[str, dict]] = None) -> str:
    """Render an approved plan to exports/reel_<plan_id>_<ts>.mp4.

    Returns the output path. Raises RenderError on any failure.

    Story plans (see story.py) default to ``caption_style="kinetic"`` and to
    the story's own ratio when ``ratio`` is None; non-story plans ignore both
    ``caption_style`` and ``effects`` and render exactly as before.
    """
    progress = _progress_callable(job)
    plan, resolved = _load_plan(plan_id)
    n = len(resolved)
    from . import story as story_mod
    from . import kinetic, reframe as reframe_mod
    from . import beatmap as beatmap_mod
    from . import brands as brands_mod
    from . import audio_levels
    story = story_mod.get_story(plan_id)
    # Story sidecars are timing contracts, not merely caption metadata.  Do
    # not render a voiceover against clips that have drifted since planning.
    try:
        story_marked = bool(json.loads(plan.parsed_json or "{}").get("story"))
    except (TypeError, json.JSONDecodeError):
        story_marked = False
    if story_marked and not story:
        raise RenderError(f"story sidecar missing or invalid for plan {plan_id!r}")
    if story_marked and not story.get("voiceover"):
        raise RenderError(f"story sidecar voiceover missing for plan {plan_id!r}")
    if story_marked:
        from . import planner
        try:
            planner.validate_story_sync(plan, story)
        except ValueError as exc:
            raise RenderError(str(exc)) from None
    # Music-led B-roll plans carry their own sidecar (music, text, brand).
    from . import broll as broll_mod
    try:
        broll_marked = bool(json.loads(plan.parsed_json or "{}").get("broll"))
    except (TypeError, json.JSONDecodeError):
        broll_marked = False
    broll = None if story else broll_mod.get_broll(plan_id)
    if broll_marked and broll is None:
        raise RenderError(f"B-roll sidecar missing or invalid for plan {plan_id!r}")
    if broll is not None:
        drift = abs(sum(float(c.end) - float(c.start) for c, _a in resolved)
                    - float(broll["duration"]))
        if drift > 0.05:
            raise RenderError("B-roll clips no longer match the music timeline; create a new plan")
        if broll.get("music") and not broll.get("silent"):
            music_path = broll["music"]["path"]
            music_start = broll["music"]["start"]
        else:
            music_path, music_start = None, None

    if ratio is None:
        ratio = (story or broll or {}).get("ratio") or "9:16"
        if width is None and height is None and ratio == "16:9" and (story or broll):
            width = 1920
    width = 1080 if width is None else width
    height = 1920 if height is None else height
    ratio = (ratio or "9:16").lower()
    if ratio not in ("9:16", "16:9", "4:5", "1:1"):
        # Derive WxH from the explicit width/height instead of a named ratio.
        pass
    elif ratio == "16:9":
        height = round(width * 9 / 16 / 2) * 2
    elif ratio == "1:1":
        height = width
    elif ratio == "4:5":
        height = round(width * 5 / 4 / 2) * 2
    else:  # 9:16
        height = round(width * 16 / 9 / 2) * 2
    if width % 2:
        width += 1
    if height % 2:
        height += 1

    # Story plans: voiceover is the audio, clips are hard-cut (xfade would
    # shorten the timeline and drift it away from the voiceover), clip audio
    # is muted and captions come from captions.srt.
    voiceover = story.get("voiceover") if story else None
    if voiceover and not Path(voiceover).is_file():
        raise RenderError(f"story voiceover file missing: {voiceover}")
    srt_path = story.get("srt") if story else None
    if srt_path and not Path(srt_path).is_file():
        srt_path = None
    is_broll = broll is not None
    is_story = bool(voiceover) or is_broll
    style = (caption_style or (story or broll or {}).get("caption_style") or "kinetic") \
        if is_story else "classic"
    is_kinetic = is_story and style == "kinetic"
    kinetic_zones = reframe_mod.normalize_safe_zones(safe_zones) if height > width else None
    fx = is_kinetic and effects
    kin_dir = config.RENDER_TMP_DIR / f"emph_{plan_id}"
    overlays: list[dict] = []      # PNG sequences / images composited by ffmpeg
    ass_file: Optional[Path] = None
    side = story or broll or {}
    hook_text = side.get("hook_text") if is_kinetic else None
    brand = side.get("brand") if is_kinetic else None
    card_s = brands_mod.END_CARD_SECONDS if brand else 0.0

    xfade = 0.0 if is_story else config.XFADE_DURATION
    font = config.find_font()
    total = 0.0
    per_clip: list[dict] = []
    for c, asset in resolved:
        dur = max(0.2, float(c.end) - float(c.start))
        if asset.kind == "photo" or is_story:
            has_audio = False
        else:
            has_audio = _has_audio(asset.path)
        per_clip.append({
            "clip": c, "asset": asset, "dur": dur, "has_audio": has_audio,
            "path": asset.path, "start": float(c.start), "end": float(c.end),
        })
        total += dur
    # Reframe analysis is source-normalized, so it can be reused by
    # render_batch across output dimensions.  A caller can also supply the
    # already-computed map to avoid repeat DB/OpenCV work.
    analysis_by_clip: dict[str, dict] = dict(_reframe_analysis or {})
    for i, pc in enumerate(per_clip):
        key = str(pc["clip"].id)
        if key not in analysis_by_clip:
            try:
                analysis_by_clip[key] = reframe_mod.analyze_reframe(
                    pc["path"], asset_id=pc["asset"].id,
                    start=float(pc["clip"].start), end=float(pc["clip"].end),
                    mode=reframe, safe_zones=safe_zones,
                )
            except Exception as exc:  # reframe may never make a render fail
                log.warning("reframe analysis failed for clip %s: %s", key, exc)
                analysis_by_clip[key] = {"path_points": [], "safe_zones": {}}
    # Only crossfades overlap time.  Hard cuts retain every source second.
    transition_overlap = sum(
        xfade for pc in per_clip[1:]
        if str(getattr(pc["clip"], "transition", "crossfade") or "crossfade").lower()
        != "cut"
    )
    timeline = total - transition_overlap if n > 1 else total

    # Beat sync (voice-led plans with music): nudge each internal cut onto the
    # nearest music beat by moving the neighbouring clips' in/out points. The
    # first start and last end never move, so the timeline length and the
    # voiceover alignment are unchanged.
    music_ss: Optional[float] = music_start
    beat_bm = beatmap_mod.load(music_path) if (is_story and not is_broll and music_path) else None
    snap_info: Optional[dict] = None
    if beat_bm is not None:
        if music_ss is None:
            music_ss = beatmap_mod.default_start(beat_bm, timeline)
        music_ss = max(0.0, min(float(music_ss), max(0.0, beat_bm.duration - timeline)))
        beats_rel = beatmap_mod.rel(beat_bm.beats, music_ss, timeline,
                                    pad=beatmap_mod.SNAP_TOLERANCE_S)
        snap_info = beatmap_mod.snap_plan(
            [pc["dur"] for pc in per_clip],
            [float(pc["clip"].start) for pc in per_clip],
            [max(0.0, float(pc["asset"].duration) - float(pc["clip"].end))
             if pc["asset"].kind == "video" and pc["asset"].duration else 0.0
             for pc in per_clip],
            beats_rel)
        for pc, (s_off, e_off) in zip(per_clip, snap_info["offsets"]):
            pc["start"] = float(pc["clip"].start) + s_off
            pc["end"] = float(pc["clip"].end) + e_off
            pc["dur"] = pc["end"] - pc["start"]
        log.info("beat sync: %d/%d cuts snapped (music start %.2fs)",
                 sum(1 for d in snap_info["deltas"] if abs(d) > 1e-6),
                 len(snap_info["deltas"]), music_ss)

    timeline_total = timeline + card_s     # clips + (optional) brand end card

    if is_kinetic:
        config.RENDER_TMP_DIR.mkdir(parents=True, exist_ok=True)
        events = {"body": [], "emphasis": []}
        extra_ass: list[str] = []
        hook_end = kinetic.HOOK_SECONDS if hook_text else 0.0
        if hook_text:
            info = kinetic.render_hook_images(hook_text, width, height, kin_dir, "hook",
                                              safe_zones=kinetic_zones)
            overlays.append({"info": info, "start": 0.0, "end": hook_end})
        layout: list[dict] = []
        if captions:
            events = kinetic.plan_events(
                (side.get("caption_beats") if is_broll else side.get("beats")) or [],
                hook_end=hook_end)
            for k, ev in enumerate(events["emphasis"]):
                info = kinetic.render_emphasis_images(ev["text"], width, height, kin_dir,
                                                      f"e{k}", seed=k, safe_zones=kinetic_zones)
                overlays.append({"info": info, "start": ev["start"], "end": ev["end"]})
            layout = kinetic.emphasis_layout(
                [o["info"] for o in overlays[1 if hook_text else 0:]], width, height, kinetic_zones)
            for o, lay in zip(overlays[1 if hook_text else 0:], layout):
                o["info"].update(lay)
        if brand:
            card = kinetic.brand_card_assets(brand, width, height, kin_dir, kinetic_zones)
            name = card["name"]
            overlays.append({"info": name, "start": timeline, "end": timeline_total, "fade": True,
                             "x": name["x"], "y": name["y"]})
            extra_ass = kinetic.card_ass_lines(card, timeline, timeline_total)
            logo = brand.get("logo_path")
            if logo and Path(logo).is_file():
                overlays.append({"image": logo, "start": timeline, "end": timeline_total,
                                 "fade": True, "scale_w": round(card["safe"][2] * 0.22),
                                 "cy": max(card["safe"][1], name["y"] - round(height * 0.07)),
                                 "cx": card["cx"]})
        for k, o in enumerate(overlays):
            info = o.get("info")
            if info is None:
                continue
            info["list"] = str(kin_dir / f"seq{k}.txt")
            kinetic.write_sequence_list(info, o["end"] - o["start"], Path(info["list"]),
                                        config.RENDER_FPS)
            o.setdefault("x", info.get("x", 0))
            o.setdefault("y", info.get("y", 0))
        if events["body"] or events["emphasis"] or extra_ass:
            ass_file = config.RENDER_TMP_DIR / f"cap_{plan_id}_kin.ass"
            ass_file.write_text(kinetic.build_ass(events, width, height, layout, kinetic_zones,
                                                  extra_lines=extra_ass), encoding="utf-8")

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
        if not pc["has_audio"] and not is_story:
            cmd += ["-f", "lavfi", "-t", f"{pc['dur']:.3f}",
                    "-i", "anullsrc=r=48000:cl=stereo"]
    voice_idx: Optional[int] = None
    if voiceover:
        voice_idx = cmd.count("-i")
        cmd += ["-i", voiceover]
    emph_idx0 = cmd.count("-i")
    for o in overlays:
        if o.get("info") is not None:
            cmd += ["-f", "concat", "-safe", "0", "-i", o["info"]["list"]]
        else:
            cmd += ["-loop", "1", "-framerate", str(config.RENDER_FPS),
                    "-t", f"{o['end'] - o['start']:.3f}", "-i", o["image"]]
    silent_idx: Optional[int] = None
    if is_broll and not music_path:
        silent_idx = cmd.count("-i")
        cmd += ["-f", "lavfi", "-t", f"{timeline_total:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    if music_path:
        if not Path(music_path).exists():
            raise RenderError(f"music file not found: {music_path}")
        if music_ss:
            cmd += ["-ss", f"{music_ss:.3f}"]
        cmd += ["-stream_loop", "-1", "-i", music_path]

    filters: list[str] = []
    vlabels: list[str] = []
    alabels: list[str] = []
    caption_files: list[str] = []
    # photo loop inputs are video-only; anullsrc inputs follow each asset
    # input, so track input indices explicitly.
    input_index = 0
    for i, pc in enumerate(per_clip):
        vlabel = f"v{i}"
        alabel = f"a{i}"
        in_idx = input_index  # this clip's asset input file index
        if is_story:
            has_audio = False
            audio_idx = -1
            input_index += 1
        elif pc["asset"].kind == "photo":
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
            f"trim=start={pc['start']:.3f}:end={pc['end']:.3f}"
            if pc["asset"].kind != "photo" else
            f"trim=start=0:end={pc['dur']:.3f}"
        )
        chain.append("setpts=PTS-STARTPTS")
        chain.append(
            f"scale={width}:{height}:force_original_aspect_ratio=increase"
            ":force_divisible_by=2")
        analysis = analysis_by_clip.get(str(pc["clip"].id), {})
        zones = reframe_mod.normalize_safe_zones(analysis.get("safe_zones"))
        chain.append(reframe_mod.build_crop_filter(analysis, width, height, pc["dur"]))
        if guide_overlay:
            x = round(width * zones["left"])
            y = round(height * zones["top"])
            box_h = max(2, round(height * (1 - zones["top"] - zones["bottom"])))
            box_w = max(2, round(width * (1 - zones["left"] - zones["right"])))
            chain.append(f"drawbox=x={x}:y={y}:w={box_w}:h={box_h}:color=cyan@0.8:t=3")
        if fx:
            # Slow push-in 1.00 -> 1.08 over the clip: scale up per frame, then
            # centre-crop back; clip duration is untouched.
            zoom = f"(1+0.08*min(t/{pc['dur']:.3f},1))"
            chain.append(
                f"scale=w='trunc(iw*{zoom}/2)*2':h='trunc(ih*{zoom}/2)*2':eval=frame")
            chain.append(f"crop={width}:{height}")
        chain.append("setsar=1")
        if captions and pc["clip"].caption and not is_story:
            fontsize = max(18, height // 34)
            txt = _wrap_text(str(pc["clip"].caption).strip(),
                             width=_chars_per_line(width, fontsize))
            if txt:
                cap_path = _escape_filter_path(
                    _caption_file(plan_id, i, txt))
                caption_files.append(cap_path)
                fontfile = f":fontfile={font}" if font else ""
                caption_x = "(w-text_w)/2"
                if height > width:
                    left = round(width * zones["left"])
                    right = round(width * zones["right"])
                    caption_x = f"max({left},min((w-text_w)/2,w-{right}-text_w))"
                chain.append(
                    f"drawtext=textfile='{cap_path}'{fontfile}"
                    f":fontsize={fontsize}:line_spacing=8"
                    ":fontcolor=white:borderw=2:bordercolor=black@0.6"
                    f":x={caption_x}:y=h-text_h-"
                    f"{max(40, height // 20, round(height * zones['bottom']) if height > width else 0)}"
                    f":enable='between(t,0,{pc['dur']:.3f})'")
        filters.append(",".join(chain) + f"[{vlabel}]")
        vlabels.append(vlabel)

        # Audio chain.
        if is_story:
            alabels.append(alabel)
            continue
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

    if is_story:
        input_index += (1 if voiceover else 0) + len(overlays)  # voiceover + overlay inputs
    # Gain staging for narrated renders with a music bed (see audio_levels.py).
    levels: Optional[dict] = None
    if voiceover and music_path and not is_broll and config.MUSIC_LEVELING:
        levels = audio_levels.plan_levels(voiceover, music_path, music_ss or 0.0, timeline)
    # Music input (last input index).
    music_label: Optional[str] = None
    if music_path:
        music_label = f"m{input_index}"
        fade_duration = min((0.5 if is_broll else 0.6) if is_story else 1.0, max(0.05, timeline_total))
        fade_start = max(0.0, timeline_total - fade_duration)
        bed = (audio_levels.music_filter(levels["music_gain_db"], timeline if card_s else None) + ","
               if levels else "")
        filters.append(
            f"[{input_index}:a]aresample=48000,{bed}atrim=end={timeline_total:.3f},"
            f"afade=t=out:st={fade_start:.3f}:d={fade_duration:.3f}[{music_label}]")

    # Video join chain. A hard cut concatenates complete clips; mixed plans
    # keep crossfade offsets correct after each non-overlapping join.
    vout = vlabels[0]
    if is_story and n > 1:
        filters.append("".join(f"[{v}]" for v in vlabels)
                       + f"concat=n={n}:v=1:a=0,settb=1/{config.RENDER_FPS}[vout]")
    elif n > 1:
        joined_duration = per_clip[0]["dur"]
        for k in range(1, n):
            out = f"x{k}" if k < n - 1 else "vout"
            is_cut = str(getattr(per_clip[k]["clip"], "transition", "crossfade")
                         or "crossfade").lower() == "cut"
            if is_cut:
                filters.append(
                    f"[{vout}][{vlabels[k]}]concat=n=2:v=1:a=0,"
                    f"settb=1/{config.RENDER_FPS}[{out}]")
                joined_duration += per_clip[k]["dur"]
            else:
                offset = joined_duration - xfade
                filters.append(
                    f"[{vout}][{vlabels[k]}]xfade=transition=fade:"
                    f"duration={xfade:.3f}:offset={offset:.3f}[{out}]")
                joined_duration += per_clip[k]["dur"] - xfade
            vout = out
    else:
        filters.append(f"[{vlabels[0]}]null[vout]")

    # Audio follows the same timeline as video.  A mixed cut/crossfade plan
    # cannot safely chain concat into acrossfade on ffmpeg 5.x (it emits NaN
    # samples), so each pre-trimmed clip is independently faded and delayed
    # to its video start before a single sample-rate-normalized mix.
    aout = alabels[0]
    if is_broll:
        pass  # no voice: music (or silence) is the whole soundtrack, built below
    elif is_story:
        filters.append(f"[{voice_idx}:a]aresample=48000"
                       + (f",{audio_levels.voice_filter(levels['voice_gain_db'])}" if levels else "")
                       + (f",apad=whole_dur={timeline_total:.3f}" if card_s else "") + "[amix]")
    elif n > 1:
        has_cut = any(
            str(getattr(pc["clip"], "transition", "crossfade") or "crossfade").lower()
            == "cut" for pc in per_clip[1:]
        )
        has_crossfade = any(
            str(getattr(pc["clip"], "transition", "crossfade") or "crossfade").lower()
            != "cut" for pc in per_clip[1:]
        )
        if has_cut and has_crossfade:
            offsets = [0.0]
            for k in range(1, n):
                previous = offsets[-1] + per_clip[k - 1]["dur"]
                is_crossfade = str(getattr(per_clip[k]["clip"], "transition", "crossfade")
                                   or "crossfade").lower() != "cut"
                offsets.append(previous - (xfade if is_crossfade else 0.0))
            mix_labels: list[str] = []
            for k, (alabel, pc) in enumerate(zip(alabels, per_clip)):
                # apad is used by the general clip path; trim it back to the
                # planned segment before delaying so no padded tail reaches
                # amix on older ffmpeg builds.
                chain = [f"[{alabel}]atrim=end={pc['dur']:.3f},"
                         "asetpts=PTS-STARTPTS,aresample=48000,asettb=expr=1/sr"]
                if k > 0 and str(getattr(pc["clip"], "transition", "crossfade")
                                    or "crossfade").lower() != "cut":
                    chain.append(f"afade=t=in:st=0:d={xfade:.3f}")
                if k < n - 1 and str(getattr(per_clip[k + 1]["clip"], "transition", "crossfade")
                                        or "crossfade").lower() != "cut":
                    chain.append(f"afade=t=out:st={max(0.0, pc['dur'] - xfade):.3f}:d={xfade:.3f}")
                chain.append(f"adelay={round(offsets[k] * 1000)}:all=1")
                label = f"mix{k}"
                filters.append(",".join(chain) + f"[{label}]")
                mix_labels.append(label)
            filters.append("".join(f"[{label}]" for label in mix_labels)
                           + f"amix=inputs={n}:duration=longest:normalize=0,"
                             f"atrim=end={timeline:.3f},asetpts=PTS-STARTPTS[amix]")
        elif has_cut:
            filters.append("".join(f"[{a}]" for a in alabels)
                           + f"concat=n={n}:v=0:a=1[amix]")
        else:
            for k in range(1, n):
                out = f"ax{k}" if k < n - 1 else "amix"
                filters.append(
                    f"[{aout}][{alabels[k]}]acrossfade=d={xfade:.3f}[{out}]")
                aout = out
    else:
        filters.append(f"[{alabels[0]}]anull[amix]")

    if is_broll:
        if music_path:
            filters.append(f"[{music_label}]loudnorm=I=-14:TP=-1.5:LRA=11[afinal]")
        else:
            filters.append(f"[{silent_idx}:a]aresample=48000[afinal]")
    elif music_path:
        # Duck the music under the voice track, then mix. The voice feeds both
        # the sidechain and the mix, so it must be split (a filter label can
        # only be consumed once).
        ducked = "duck"
        filters.append("[amix]asplit=2[vox1][vox2]")
        filters.append(
            f"[{music_label}][vox1]sidechaincompress="
            + (audio_levels.DUCK_FILTER if levels
               else "threshold=0.03:ratio=8:attack=20:release=250:makeup=1")
            +
            f"[{ducked}]")
        filters.append(
            f"[vox2][{ducked}]amix=inputs=2:duration=first:normalize=0[aout]")
    else:
        filters.append(
            f"[amix]loudnorm=I=-14:TP=-1.5:LRA=11[aout]")

    # Final loudnorm when music is present (after mixing).
    if is_broll:
        amap = "afinal"
    elif music_path:
        filters.append("[aout]loudnorm=I=-14:TP=-1.5:LRA=11[afinal]")
        amap = "afinal"
    else:
        amap = "aout"

    vmap = "vout"
    if is_kinetic:
        cur = "vout"

        def _chain(filt: str, label: str) -> None:
            nonlocal cur
            filters.append(f"[{cur}]{filt}[{label}]")
            cur = label

        if card_s:
            # Freeze the last frame for the end card (blurred + darkened below).
            _chain(f"tpad=stop_mode=clone:stop_duration={card_s:.3f}", "vcard")
        if fx:
            _chain("eq=contrast=1.08:saturation=1.15,"
                   "colorbalance=rs=0.03:bs=-0.03:rm=0.02:bm=-0.02", "vgrade")
            starts = [float(b["start"]) for b in (story.get("beats") or [])[1:]] if story else []
            if snap_info is not None:
                cuts, snapped = snap_info["cuts"], snap_info["snapped"]
                downs = beatmap_mod.rel(beat_bm.downbeats, music_ss, timeline)
                mapped = []
                for t0 in starts:
                    j = next((i for i, c in enumerate(cuts) if abs(c - t0) < 0.02), None)
                    mapped.append(beatmap_mod.flash_time(snapped[j] if j is not None else t0, downs))
                starts = sorted(set(round(t, 3) for t in mapped))
            if starts:
                flash = 3.0 / config.RENDER_FPS
                shift = max(2, round(12 * min(width, height) / 1080))
                enable = "+".join(f"between(t,{t0:.3f},{t0 + flash:.3f})" for t0 in starts)
                _chain(f"rgbashift=rh={-shift}:bh={shift}:enable='{enable}'", "vglitch")
        if card_s:
            radius = max(4, round(26 * min(width, height) / 1080))
            _chain(f"boxblur=luma_radius={radius}:luma_power=2:chroma_radius={radius // 2}"
                   f":enable='gte(t,{timeline:.3f})',"
                   f"eq=brightness=-0.30:enable='gte(t,{timeline:.3f})'", "vcarddim")
        for k, o in enumerate(overlays):
            src = f"em{k}"
            chain = [f"[{emph_idx0 + k}:v]fps={config.RENDER_FPS}"]
            if o.get("scale_w"):
                chain.append(f"scale={o['scale_w']}:-2")
            chain.append("format=rgba")
            chain.append(f"setpts=PTS+{o['start']:.3f}/TB")
            if o.get("fade"):
                chain.append(f"fade=t=in:st={o['start']:.3f}:d=0.25:alpha=1")
            filters.append(",".join(chain) + f"[{src}]")
            pos = (f"x={o['cx']}-w/2:y={o['cy']}-h/2" if o.get("scale_w")
                   else f"x={o['x']}:y={o['y']}")
            filters.append(
                f"[{cur}][{src}]overlay={pos}:eof_action=pass:"
                f"enable='between(t,{o['start']:.3f},{o['end']:.3f})'[vo{k}]")
            cur = f"vo{k}"
        if ass_file is not None:
            fd = f":fontsdir='{_escape_filter_path(str(kinetic.FONTS_DIR))}'" \
                if kinetic.fonts_available() else ""
            _chain(f"ass=filename='{_escape_filter_path(str(ass_file))}'{fd}", "vass")
        if is_broll and broll.get("fade_out_s"):
            fade = min(float(broll["fade_out_s"]), timeline_total)
            _chain(f"fade=t=out:st={max(0.0, timeline_total - fade):.3f}:d={fade:.3f}", "vfade")
        vmap = cur
    elif is_story and captions and srt_path:
        fontsize = max(18, height // 28)
        story_zones = reframe_mod.normalize_safe_zones(safe_zones)
        margin_v = max(30, height // 14,
                       round(height * story_zones["bottom"]) if height > width else 0)
        style = (f"FontName=DejaVu Sans,Bold=1,FontSize={fontsize},"
                 "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
                 "BackColour=&H80000000,BorderStyle=1,Outline=2.5,Shadow=1,"
                 f"Alignment=2,MarginL={round(width * story_zones['left'])},"
                 f"MarginR={round(width * story_zones['right'])},MarginV={margin_v}")
        filters.append(
            f"[vout]subtitles=filename='{_escape_filter_path(srt_path)}'"
            f":original_size={width}x{height}:force_style='{style}'[vsub]")
        vmap = "vsub"

    out_dir = config.EXPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    # Batches can finish several presets in the same second.  Include the
    # dimensions plus microseconds and a nonce so ffmpeg's ``-y`` never
    # replaces an earlier preset export.
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    out_path = out_dir / f"reel_{plan_id}_{width}x{height}_{ts}_{uuid.uuid4().hex[:8]}.mp4"

    cmd += ["-filter_complex", ";".join(filters),
            "-map", f"[{vmap}]", "-map", f"[{amap}]",
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
    finally:
        _cleanup_caption_files(plan_id)
        if is_kinetic:
            import shutil
            shutil.rmtree(kin_dir, ignore_errors=True)
            if ass_file is not None:
                ass_file.unlink(missing_ok=True)

    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-30:]
        raise RenderError(
            f"ffmpeg render failed (exit {proc.returncode}): "
            + "\n".join(tail)[-2000:])
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RenderError("ffmpeg exited 0 but produced no output file")

    if is_story and srt_path:
        try:
            import shutil
            shutil.copyfile(srt_path, out_path.with_suffix(".srt"))
        except OSError as exc:
            log.warning("could not copy captions.srt next to render: %s", exc)

    progress(1.0, f"render complete: {out_path.name}")
    # Store the exact source crop plans beside the export as a portable
    # sidecar.  It is useful when an editor needs to inspect the smart pan
    # without rerunning face/motion analysis.
    try:
        sidecar = out_path.with_suffix(".reframe.json")
        sidecar.write_text(json.dumps({
            "version": 1, "plan_id": plan_id, "ratio": ratio,
            "width": width, "height": height, "reframe": reframe,
            "safe_zones": reframe_mod.normalize_safe_zones(safe_zones),
            "clips": [analysis_by_clip.get(str(pc["clip"].id), {}) for pc in per_clip],
        }, indent=2, sort_keys=True), encoding="utf-8")
    except OSError as exc:
        log.warning("could not write render reframe sidecar: %s", exc)

    return str(out_path)


def render_plan(plan_id: str, ratio: Optional[str] = None,
                width: Optional[int] = None, height: Optional[int] = None,
                captions: bool = True, music_path: Optional[str] = None,
                job: Any = None, caption_style: Optional[str] = None,
                effects: bool = True, reframe: str = "smart",
                safe_zones: Optional[Mapping[str, Any]] = None,
                guide_overlay: bool = False, music_start: Optional[float] = None,
                _reframe_analysis: Optional[Mapping[str, dict]] = None) -> str:
    """Render one immutable view of a plan and its story sidecar.

    Plan mutation, approval and sidecar restoration use the same re-entrant
    lock, so the DB rows and files cannot change halfway through a render.
    """
    from .plan_history import plan_lock

    with plan_lock(plan_id):
        return _render_plan(
            plan_id, ratio=ratio, width=width, height=height, captions=captions,
            music_path=music_path, job=job, caption_style=caption_style,
            effects=effects, reframe=reframe, safe_zones=safe_zones,
            guide_overlay=guide_overlay, music_start=music_start,
            _reframe_analysis=_reframe_analysis,
        )


# ---------------------------------------------------------------------------
# Job-compatible async wrapper (used by main.py)
# ---------------------------------------------------------------------------

async def render_job(plan_id: str, ratio: Optional[str], width: Optional[int],
                     height: Optional[int], captions: bool, music_path: Optional[str],
                     progress, _cancel_flag, caption_style: Optional[str] = None,
                     effects: bool = True, reframe: str = "smart",
                     safe_zones: Optional[Mapping[str, Any]] = None,
                     guide_overlay: bool = False,
                     music_start: Optional[float] = None) -> str:
    """Async entrypoint for jobs.run: renders in a thread, honours cancel."""
    import asyncio

    def _sync() -> str:
        return render_plan(plan_id, ratio=ratio, width=width, height=height,
                           captions=captions, music_path=music_path,
                           job=progress, caption_style=caption_style,
                           effects=effects, reframe=reframe,
                           safe_zones=safe_zones, guide_overlay=guide_overlay,
                           music_start=music_start)

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync)


_BATCH_PRESETS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920), "4:5": (1080, 1350), "1:1": (1080, 1080),
    "16:9": (1920, 1080),
}


def _render_batch(plan_id: str, *, presets: Optional[list[str]] = None,
                 captions: bool = True, music_path: Optional[str] = None,
                 job: Any = None, caption_style: Optional[str] = None,
                 effects: bool = True, reframe: str = "smart",
                 safe_zones: Optional[Mapping[str, Any]] = None,
                 guide_overlay: bool = False, cancel_flag: Any = None,
                 music_start: Optional[float] = None) -> dict[str, str]:
    """Render selected presets sequentially, sharing the light crop analysis.

    The sequential loop intentionally avoids competing ffmpeg processes on a
    CPU-bound machine.  Values are ``{preset: output_path}``.
    """
    def _cancelled() -> bool:
        value = getattr(cancel_flag, "cancelled", False)
        return bool(value() if callable(value) else value)

    if _cancelled():
        import asyncio
        raise asyncio.CancelledError()
    chosen = presets or list(_BATCH_PRESETS)
    invalid = [p for p in chosen if p not in _BATCH_PRESETS]
    if invalid:
        raise RenderError(f"unsupported render preset(s): {', '.join(invalid)}")
    _, resolved = _load_plan(plan_id)
    from . import reframe as reframe_mod
    analysis = {
        str(clip.id): reframe_mod.analyze_reframe(
            asset.path, asset_id=asset.id, start=float(clip.start), end=float(clip.end),
            mode=reframe, safe_zones=safe_zones,
        )
        for clip, asset in resolved
    }
    progress = _progress_callable(job)
    output: dict[str, str] = {}
    for index, preset in enumerate(chosen):
        if _cancelled():
            import asyncio
            raise asyncio.CancelledError()
        w, h = _BATCH_PRESETS[preset]
        def _one_progress(frac: float, message: str, *, _index: int = index) -> None:
            progress((_index + frac) / len(chosen), f"{preset}: {message}")
        output[preset] = render_plan(
            plan_id, ratio=preset, width=w, height=h, captions=captions,
            music_path=music_path, job=_one_progress, caption_style=caption_style,
            effects=effects, reframe=reframe, safe_zones=safe_zones,
            guide_overlay=guide_overlay, music_start=music_start,
            _reframe_analysis=analysis,
        )
    progress(1.0, "batch render complete")
    return output


def render_batch(plan_id: str, *, presets: Optional[list[str]] = None,
                 captions: bool = True, music_path: Optional[str] = None,
                 job: Any = None, caption_style: Optional[str] = None,
                 effects: bool = True, reframe: str = "smart",
                 safe_zones: Optional[Mapping[str, Any]] = None,
                 guide_overlay: bool = False, cancel_flag: Any = None,
                 music_start: Optional[float] = None) -> dict[str, str]:
    """Render an entire preset batch from one locked plan/sidecar revision."""
    from .plan_history import plan_lock

    with plan_lock(plan_id):
        return _render_batch(
            plan_id, presets=presets, captions=captions, music_path=music_path,
            job=job, caption_style=caption_style, effects=effects,
            reframe=reframe, safe_zones=safe_zones, guide_overlay=guide_overlay,
            cancel_flag=cancel_flag, music_start=music_start,
        )
