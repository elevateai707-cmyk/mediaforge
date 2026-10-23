"""Green-screen plans: a talking-head recording cut out and composited over news cards / drone B-roll.

The result is an ordinary *story-shaped* plan (so captions, hook, brand end card, beat cuts, music
staging, swap-broll, history and exports all work) whose "voice" is the recording's own cleaned audio
and whose beats come from Whisper's word timestamps:

  * beats: contiguous groups of the transcript; each beat's background is a motion-graphic card
    (``visual``) or a smooth drone shot (underlay clips), see ``assign_backgrounds``;
  * subject: two layer videos (colour + alpha, with a baked soft shadow and rim light) written to
    the plan folder and composited by the renderer over the backgrounds;
  * captions: kinetic words from the user's own speech, placed in the lower third (below the face),
    without the big emphasis words (the head occupies that area).

Everything runs on the CPU (matting via onnxruntime, Whisper int8) so the shared GPU stays free.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .. import config, models
from ..db import SessionLocal
from ..proc import tool_env
from . import brands, cards, kinetic, segment, speech, story

log = logging.getLogger("mediaforge.greenscreen")

MAX_SOURCE_S = 240.0
FPS = 30
BODY_Y = 0.96            # caption row inside the safe rectangle (lower third, under the face)
MODES = ("news", "drone", "mixed")


class GreenscreenError(ValueError):
    """Invalid request or unusable recording (HTTP 422)."""


# ---------------------------------------------------------------------------
# backgrounds
# ---------------------------------------------------------------------------

def normalise_backgrounds(items: Optional[list]) -> list[dict]:
    """[card spec | {"hint": str} | "hint text"] (each optionally with "at": seconds) -> normalised."""
    out: list[dict] = []
    for raw in items or []:
        if isinstance(raw, str):
            raw = {"hint": raw}
        if not isinstance(raw, dict):
            raise GreenscreenError("each background must be a card spec, a {hint} object or a string")
        at = raw.get("at")
        if at is not None:
            try:
                at = float(at)
            except (TypeError, ValueError):
                raise GreenscreenError("background 'at' must be a time in seconds") from None
        if "kind" in raw:
            try:
                out.append({"card": cards.validate_card(raw), "at": at})
            except cards.CardError as exc:
                raise GreenscreenError(str(exc)) from None
        elif raw.get("hint"):
            out.append({"hint": str(raw["hint"])[:200], "at": at})
        else:
            raise GreenscreenError("a background needs a card 'kind' or a drone 'hint'")
    return out


def assign_backgrounds(mode: str, items: list[dict], beats: list[dict]) -> list[dict]:
    """One {"card": spec|None, "hint": str|None} per beat.

    news: cards only (ambient backdrop when nothing is given); drone: smooth footage only; mixed:
    cards and hints in order. Items with ``at`` land on the beat containing that time; the rest fill
    the remaining beats in order (news cycles through its cards, drone cycles its hints).
    """
    if mode not in MODES:
        raise GreenscreenError(f"background_mode must be one of {', '.join(MODES)}")
    cards_in = [i for i in items if "card" in i]
    hints_in = [i for i in items if "hint" in i]
    if mode == "news" and hints_in:
        raise GreenscreenError("news mode takes card backgrounds only (use mixed for drone hints)")
    if mode == "drone" and cards_in:
        raise GreenscreenError("drone mode takes hints only (use mixed for cards)")
    slots: list[Optional[dict]] = [None] * len(beats)
    pending: list[dict] = []
    for item in items:
        if item.get("at") is not None:
            idx = next((i for i, b in enumerate(beats) if b["start"] <= item["at"] < b["end"]), len(beats) - 1)
            if slots[idx] is None:
                slots[idx] = item
                continue
        pending.append(item)
    queue = list(pending)
    for i in range(len(beats)):
        if slots[i] is None and queue:
            slots[i] = queue.pop(0)
    out = []
    cycle = 0
    for i, slot in enumerate(slots):
        if slot is None:
            if mode == "news":
                pool = [x for x in items if "card" in x]
                slot = pool[cycle % len(pool)] if pool else {"card": cards.validate_card({"kind": "ambient", "text": ""})}
                cycle += 1
            elif mode == "drone" and hints_in:
                slot = hints_in[cycle % len(hints_in)]
                cycle += 1
        out.append({"card": slot.get("card") if slot else None, "hint": slot.get("hint") if slot else None})
    return out


# ---------------------------------------------------------------------------
# subject layer
# ---------------------------------------------------------------------------

def layer_geometry(canvas_w: int, canvas_h: int, scale: float, position: str) -> dict:
    """Pixel size/position of the 9:16 subject layer on the canvas (also stored as fractions)."""
    lw = max(2, int(round(canvas_w * scale / 2)) * 2)
    lh = int(round(lw * 16 / 9 / 2)) * 2
    if lh > canvas_h:
        lh = canvas_h - canvas_h % 2
        lw = int(round(lh * 9 / 16 / 2)) * 2
    x = (canvas_w - lw) // 2
    y = canvas_h - lh if position == "lower" else (canvas_h - lh) // 2
    return {"w": lw, "h": lh, "x": x, "y": y, "w_frac": lw / canvas_w, "x_frac": x / canvas_w,
            "y_frac": y / canvas_h}


def _reader(src: str, lw: int, lh: int, fps: int, duration: float):
    crop = "crop=w='if(gt(iw/ih,9/16),ih*9/16,iw)':h='if(gt(iw/ih,9/16),ih,iw*16/9)'"
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-t", f"{duration:.3f}", "-i", src, "-an",
           "-vf", f"{crop},scale={lw}:{lh},fps={fps}", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=tool_env())


def _writer(path: Path, lw: int, lh: int, fps: int, pix: str, crf: int):
    cmd = ["ffmpeg", "-y", "-v", "error", "-nostdin", "-f", "rawvideo", "-pix_fmt", pix, "-s", f"{lw}x{lh}",
           "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
           "-pix_fmt", "yuv420p", str(path)]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, env=tool_env())


def process_subject(src: str, out_dir: Path, lw: int, lh: int, duration: float, keying: str = "auto",
                    segmenter: Optional[segment.Segmenter] = None, fps: int = FPS,
                    progress: Optional[Callable[[float, str], None]] = None) -> dict:
    """Cut the person out of ``src`` into ``subject_rgb.mp4`` + ``subject_alpha.mp4`` (lw x lh)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rgb_path, alpha_path = out_dir / "subject_rgb.mp4", out_dir / "subject_alpha.mp4"
    frame_bytes = lw * lh * 3
    reader = _reader(src, lw, lh, fps, duration)
    rgb_w, alpha_w = _writer(rgb_path, lw, lh, fps, "rgb24", 14), _writer(alpha_path, lw, lh, fps, "gray", 8)
    mode = keying
    prev: Optional[np.ndarray] = None
    n = 0
    t0 = time.monotonic()
    sample: list[np.ndarray] = []
    seg: Optional[segment.Segmenter] = segmenter
    total_frames = max(1, int(duration * fps))
    scale = lw / 864.0

    def handle(frame: np.ndarray) -> None:
        nonlocal prev, n
        if mode == "chroma":
            alpha, rgb = segment.chroma_key(frame)
        else:
            alpha, rgb = seg.alpha(frame), frame
        alpha = segment.refine_alpha(alpha, prev)
        prev = alpha
        clean = segment.decontaminate(rgb, alpha)
        out_rgb, out_a = segment.shadow_and_rim(alpha, clean, scale=scale)
        rgb_w.stdin.write(out_rgb.tobytes())
        alpha_w.stdin.write((out_a * 255).astype(np.uint8).tobytes())
        n += 1
        if progress and n % 30 == 0:
            progress(min(0.95, n / total_frames), f"cutting out the subject ({n}/{total_frames} frames)")

    def decide(frames: list) -> None:
        nonlocal mode, seg
        mode = "chroma" if segment.detect_green_screen(frames) else "segment"
        if mode == "segment":
            if seg is None:
                seg = segment.RVMSegmenter()
            seg.reset()

    try:
        pending: list[np.ndarray] = []
        decided = keying != "auto"
        if keying == "segment":
            if seg is None:
                seg = segment.RVMSegmenter()
            seg.reset()
        while True:
            buf = reader.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            frame = np.frombuffer(buf, np.uint8).reshape(lh, lw, 3)
            if not decided:
                pending.append(frame.copy())
                if len(pending) < 12:
                    continue
                decide(pending)
                decided = True
                for f in pending:
                    handle(f)
                pending = []
                continue
            handle(frame)
        if pending:                               # clip shorter than the detection window
            decide(pending)
            for f in pending:
                handle(f)
    finally:
        for w in (rgb_w, alpha_w):
            try:
                w.stdin.close()
            except OSError:
                pass
        reader.stdout.close()
        reader.wait()
        errs = [w.stderr.read().decode("utf-8", "replace") for w in (rgb_w, alpha_w)]
        codes = [w.wait() for w in (rgb_w, alpha_w)]
    if n == 0:
        raise GreenscreenError("no video frames could be read from the recording")
    if any(codes):
        raise GreenscreenError("could not encode the subject layer: " + " ".join(errs)[-200:])
    elapsed = max(1e-3, time.monotonic() - t0)
    return {"rgb": str(rgb_path), "alpha": str(alpha_path), "frames": n, "fps": fps, "keying": mode,
            "processing_fps": round(n / elapsed, 1)}


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

def probe_source(path: str) -> dict:
    proc = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                          capture_output=True, text=True, timeout=60, env=tool_env())
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        raise GreenscreenError("the recording could not be read (is it a video file?)") from None
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    if v is None:
        raise GreenscreenError("the file has no video stream")
    if a is None:
        raise GreenscreenError("the recording has no audio track; the speech drives captions and timing")
    dur = float(data.get("format", {}).get("duration") or v.get("duration") or 0)
    if not 2.0 <= dur <= MAX_SOURCE_S:
        raise GreenscreenError(f"the recording must be between 2 and {MAX_SOURCE_S:.0f} seconds (got {dur:.1f})")
    return {"duration": dur, "width": v.get("width"), "height": v.get("height"), "codec": v.get("codec_name")}


def preflight(source_path: Optional[str], asset_id: Optional[int], background_mode: str,
              backgrounds: Optional[list], subject_position: str, subject_scale: float, brand: Optional[str],
              hook_text: Optional[str], music_path: Optional[str], ratio: str, keying: str,
              broll_filter: Optional[dict]) -> dict:
    """Cheap validation shared by the API route (instant 422) and the plan builder."""
    if ratio != "9:16":
        raise GreenscreenError("green-screen plans are 9:16 for now")
    if background_mode not in MODES:
        raise GreenscreenError(f"background_mode must be one of {', '.join(MODES)}")
    if subject_position not in ("lower", "center"):
        raise GreenscreenError("subject_position must be lower or center")
    if not 0.4 <= float(subject_scale) <= 1.2:
        raise GreenscreenError("subject_scale must be between 0.4 and 1.2")
    if keying not in ("auto", "segment", "chroma"):
        raise GreenscreenError("keying must be auto, segment or chroma")
    items = normalise_backgrounds(backgrounds)
    assign_backgrounds(background_mode, items, [{"start": 0.0, "end": 1.0}])      # mode/background consistency
    try:
        opts = story.validate_story_options("x", broll_filter, None, hook_text, brand)
    except story.StoryError as exc:
        raise GreenscreenError(str(exc)) from None
    if music_path and not Path(music_path).expanduser().is_file():
        raise GreenscreenError(f"music file not found: {music_path}")
    if asset_id is not None:
        with SessionLocal() as db:
            a = db.get(models.Asset, int(asset_id))
            if a is None or a.kind != "video":
                raise GreenscreenError("asset_id must be a video asset")
            source_path = a.path
    if not source_path or not Path(source_path).expanduser().is_file():
        raise GreenscreenError("source_path (or asset_id) must point to an existing recording")
    source_path = str(Path(source_path).expanduser())
    return {"source_path": source_path, "info": probe_source(source_path), "items": items, "opts": opts}


def create_greenscreen_plan(source_path: Optional[str] = None, asset_id: Optional[int] = None,
                            background_mode: str = "news", backgrounds: Optional[list] = None,
                            subject_position: str = "lower", subject_scale: float = 0.72,
                            brand: Optional[str] = None, hook_text: Optional[str] = None, captions: bool = True,
                            music_path: Optional[str] = None, music_start: Optional[float] = None,
                            ratio: str = "9:16", title: Optional[str] = None, keying: str = "auto",
                            broll_filter: Optional[dict] = None,
                            progress: Optional[Callable[[float, str], None]] = None,
                            segmenter: Optional[segment.Segmenter] = None,
                            transcriber: Optional[Callable[[str], list]] = None) -> dict:
    from . import planner
    report = progress or (lambda _f, _m: None)
    pre = preflight(source_path, asset_id, background_mode, backgrounds, subject_position, subject_scale, brand,
                    hook_text, music_path, ratio, keying, broll_filter)
    source_path, info, items, opts = pre["source_path"], pre["info"], pre["items"], pre["opts"]
    total = round(info["duration"], 3)

    plan_id = planner._new_plan_id()
    out_dir = story.story_dir(plan_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []

    # 1. voice: cleaned audio + words
    report(0.02, "cleaning the audio")
    voice = speech.clean_voice(source_path, out_dir / "voiceover.wav")
    voice_total = story.probe_duration(str(voice))
    total = round(min(total, voice_total) if voice_total else total, 3)
    words: list[dict] = []
    if captions:
        report(0.08, "transcribing the speech")
        try:
            words = (transcriber or speech.transcribe_words)(str(voice))
        except Exception as exc:  # noqa: BLE001 - captions are optional, the video is not
            log.warning("transcription failed: %s", exc)
            warnings.append(f"captions skipped: {type(exc).__name__}: {exc}"[:200])
    if words:
        beats_raw = speech.group_beats(words, total)
    else:                                         # no captions: fixed chunks of about 8 s
        edges = [8.0 * k for k in range(int(total // 8) + 1) if 8.0 * k < total - 0.5] + [total]
        beats_raw = [{"start": a, "end": b, "words": []} for a, b in zip(edges, edges[1:])]

    # 2. backgrounds -> per-beat visuals and footage
    assigned = assign_backgrounds(background_mode, items, beats_raw)
    flt = dict(opts["broll_filter"] or {})
    flt.setdefault("hyperlapse", False)
    flt.setdefault("min_smoothness", 0.8)
    beat_rows: list[dict] = []
    used: set[int] = set()
    with SessionLocal() as db:
        for i, (b, bg) in enumerate(zip(beats_raw, assigned), 1):
            slot = round(b["end"] - b["start"], 3)
            text = " ".join(w["word"] for w in b["words"]) or f"beat {i}"
            cands = story.beat_candidates(db, bg.get("hint") or text, story._default_search, None, broll_filter=flt)
            if not cands:
                cands = story.beat_candidates(db, text, story._default_search, None, broll_filter=None)
            clips = story.allocate_clips(slot, cands, set(used) if bg["card"] else used)
            visual = {"type": "card", "card": bg["card"]} if bg["card"] else None
            beat_rows.append({
                "index": i, "text": text, "audio": str(voice), "audio_duration": slot, "slot": slot,
                "start": round(b["start"], 3), "words": b["words"], "emphasis": [], "emphasis_auto": False,
                "visual": visual, "clips": [{k: c[k] for k in ("asset_id", "scene_id", "start", "end")} for c in clips],
            })
    # contiguous timeline: each beat starts where the previous one ended
    cursor = 0.0
    for b in beat_rows:
        b["start"] = round(cursor, 3)
        cursor += b["slot"]
    total = round(cursor, 3)

    # 3. subject layer (the slow part)
    geo = layer_geometry(1080, 1920, float(subject_scale), subject_position)
    subject = process_subject(source_path, out_dir, geo["w"], geo["h"], total, keying, segmenter,
                              progress=lambda f, m: report(0.15 + 0.8 * f, m))
    subject.update({k: geo[k] for k in ("w_frac", "x_frac", "y_frac")})
    subject["position"], subject["scale"] = subject_position, float(subject_scale)

    # 4. captions + plan rows + sidecar
    srt = out_dir / "captions.srt"
    srt.write_text(story.build_srt(beat_rows) if words else "", encoding="utf-8")
    ass = out_dir / "captions.ass"
    ass.write_text(kinetic.build_ass(kinetic.plan_events(beat_rows), 1080, 1920, body_y=BODY_Y,
                                     safe_zones={"top": 0.12, "bottom": 0.22, "left": 0.06, "right": 0.14}),
                   encoding="utf-8")
    label = (title or "").strip() or "Green-screen talking head"
    with SessionLocal() as db:
        db.add(models.EditPlan(
            id=plan_id, intent=f"greenscreen: {label}", status="draft",
            summary=f"Green-screen: {len(beat_rows)} beats, {total:.1f}s, {background_mode} backgrounds, {subject['keying']} key.",
            parsed_json=json.dumps({"story": True, "greenscreen": True}), target_ratio=ratio, total_duration=total))
        pos = 0
        for b in beat_rows:
            for c in b["clips"]:
                db.add(models.EditClip(plan_id=plan_id, position=pos, asset_id=c["asset_id"], scene_id=c.get("scene_id"),
                                       start=c["start"], end=c["end"], caption=b["text"], transition="cut", score=0.0))
                pos += 1
        db.commit()
    sidecar = {
        "kind": "greenscreen", "plan_id": plan_id, "title": title, "voice_id": None, "ratio": ratio,
        "voiceover": str(voice), "srt": str(srt), "ass": str(ass), "caption_style": "kinetic",
        "beat_count": len(beat_rows), "duration": total, "gap": 0.0, "beats": beat_rows,
        "hook_text": opts["hook_text"], "brand": opts["brand"], "broll_filter": flt,
        "background_mode": background_mode, "subject": subject, "captions": bool(words),
        "caption_layout": {"body_y": BODY_Y, "emphasis": False},
        "music": ({"path": str(Path(music_path).expanduser()), "start": music_start} if music_path else None),
        "source": {"path": source_path, **info}, "warnings": warnings,
    }
    story.write_sidecar(plan_id, sidecar)
    report(1.0, "green-screen plan ready")
    result = planner.get_plan(plan_id) or {}
    result["story"] = story.story_summary(plan_id)
    result["greenscreen"] = {"keying": subject["keying"], "processing_fps": subject["processing_fps"],
                             "beats": len(beat_rows), "duration": total, "warnings": warnings}
    return result
