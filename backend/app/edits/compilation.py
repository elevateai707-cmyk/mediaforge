"""Hyperlapse compilations: ALL hyperlapse clips of a daypart in one video.

Hyperlapses are the one kind of drone footage exempt from the smoothness rule, so each
clip is used whole (only black head/tail frames are trimmed). The plan is an ordinary
EditPlan plus a ``broll.json`` sidecar (``compilation: true``), rendered by the same
music-led B-roll path: kinetic hook / text line, brand end card, smart reframe.

* Order: ``time`` (taken_at) or ``flow`` (greedy nearest neighbour on thumbnail look).
* With music, each cut is nudged onto the nearest downbeat (else beat) by trimming at most
  0.4 s from a clip's head and 0.4 s from its tail; nothing is ever sped up.
* The length is the natural sum of the clips. Looping is not allowed; ``min_seconds`` instead
  applies one uniform slowdown (at most to 0.6x, frames blended) to reach it. A result still
  under 30 s is reported in ``warnings``.
"""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Optional

from .. import models
from ..db import SessionLocal
from ..proc import tool_env
from . import beatmap, brands, broll, story

log = logging.getLogger("mediaforge.compilation")

MAX_EDGE_TRIM_S = 1.5
SNAP_TRIM_S = 0.4          # per clip end, in output time
MIN_SLOWDOWN = 0.6
TARGET_S = 30.0


class CompilationError(ValueError):
    """Invalid request or nothing to compile (HTTP 422)."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _signature(path: Optional[str]) -> Optional[list[float]]:
    if not path or not Path(path).is_file():
        return None
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB").resize((16, 9))
        return [float(v) for px in im.getdata() for v in px]
    except Exception:  # noqa: BLE001
        return None


def _dist(a: list[float], b: list[float]) -> float:
    return (sum((x - y) ** 2 for x, y in zip(a, b)) / len(a)) ** 0.5


def dedupe(assets: list["models.Asset"]) -> tuple[list["models.Asset"], int]:
    """Drop re-encoded copies of the same clip (same taken_at / near-identical look and length)."""
    kept: list["models.Asset"] = []
    sigs: dict[int, Optional[list[float]]] = {}
    dropped = 0
    for a in sorted(assets, key=lambda x: x.id):
        sigs[a.id] = _signature(a.thumb_path)
        dup = False
        for k in kept:
            same_len = abs(float(a.duration) - float(k.duration)) <= 0.15
            if not same_len:
                continue
            if a.taken_at and k.taken_at and a.taken_at == k.taken_at:
                dup = True
            elif sigs[a.id] and sigs[k.id] and _dist(sigs[a.id], sigs[k.id]) < 3.0:
                dup = True
            if dup:
                break
        if dup:
            dropped += 1
        else:
            kept.append(a)
    return kept, dropped


def edge_trim(path: str, duration: float) -> tuple[float, float]:
    """Black head / tail frames to cut (seconds, each capped at 1.5 s)."""
    def black(start: float, length: float) -> list[tuple[float, float]]:
        cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "info", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
               "-i", path, "-an", "-vf", "blackdetect=d=0.04:pic_th=0.94", "-f", "null", "-"]
        try:
            err = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=tool_env()).stderr
        except (OSError, subprocess.SubprocessError):
            return []
        return [(float(a), float(b)) for a, b in
                re.findall(r"black_start:([0-9.]+) black_end:([0-9.]+)", err)]
    length = min(MAX_EDGE_TRIM_S, duration / 3)
    head = tail = 0.0
    for a, b in black(0.0, length):
        if a <= 0.05:
            head = min(MAX_EDGE_TRIM_S, b)
    for a, b in black(max(0.0, duration - length), length):
        if b >= length - 0.05:
            tail = min(MAX_EDGE_TRIM_S, length - a)
    return round(head, 3), round(tail, 3)


def order_assets(assets: list["models.Asset"], mode: str) -> list["models.Asset"]:
    from datetime import datetime
    far = datetime.max
    by_time = sorted(assets, key=lambda a: (a.taken_at or far, a.id))
    if mode != "flow" or len(by_time) < 3:
        return by_time
    sigs = {a.id: _signature(a.thumb_path) for a in by_time}
    chain = [by_time[0]]
    rest = by_time[1:]
    while rest:
        last = sigs[chain[-1].id]
        nxt = min(rest, key=lambda a: _dist(last, sigs[a.id]) if (last and sigs[a.id]) else 1e9)
        chain.append(nxt)
        rest.remove(nxt)
    return chain


def snap_to_music(lengths: list[float], downbeats: list[float], beats: list[float],
                  min_clip: float, trim_per_end: float = SNAP_TRIM_S) -> dict:
    """Shorten each clip by <= 2 x trim_per_end so every cut lands on a downbeat, else a beat.

    ``lengths`` are output-time clip lengths; ``downbeats``/``beats`` are relative to the video
    start. Greedy left to right; nothing is ever lengthened. Returns {"lengths", "trims":
    [(head, tail)], "kinds": ["down"|"beat"|"free"]}.
    """
    t = 0.0
    out, trims, kinds = [], [], []
    for d in lengths:
        natural_end = t + d
        lo = max(t + min_clip, natural_end - 2 * trim_per_end)
        pick, kind = natural_end, "free"
        for cand, label in ((downbeats, "down"), (beats, "beat")):
            inside = [x for x in cand if lo - 1e-6 <= x <= natural_end + 1e-6]
            if inside:
                pick, kind = max(inside), label
                break
        trim = max(0.0, natural_end - pick)
        tail = min(trim_per_end, trim)
        trims.append((round(trim - tail, 4), round(tail, 4)))
        out.append(round(d - trim, 4))
        kinds.append(kind)
        t += d - trim
    return {"lengths": out, "trims": trims, "kinds": kinds}


# ---------------------------------------------------------------------------
# public
# ---------------------------------------------------------------------------

def create_compilation(daypart: str, include_dusk_in: Optional[str] = None, music_path: Optional[str] = None,
                       music_start: Optional[float] = None, brand: Optional[str] = None,
                       hook_text: Optional[str] = None, text_line: str = "", ratio: str = "9:16",
                       order: str = "time", min_clip_s: float = 0.8, min_seconds: Optional[float] = None,
                       title: Optional[str] = None) -> dict:
    from . import planner

    if daypart not in ("day", "dusk", "night"):
        raise CompilationError("daypart must be day, dusk or night")
    if include_dusk_in not in (None, "day", "night"):
        raise CompilationError("include_dusk_in must be day, night or null")
    if order not in ("time", "flow"):
        raise CompilationError("order must be time or flow")
    if ratio not in broll.RATIOS:
        raise CompilationError(f"ratio must be one of {', '.join(broll.RATIOS)}")
    if min_seconds is not None and not 4 <= float(min_seconds) <= 300:
        raise CompilationError("min_seconds must be between 4 and 300")
    text_line = " ".join((text_line or "").split())
    try:
        opts = story.validate_story_options("x", None, None, hook_text, brand)
    except story.StoryError as exc:
        raise CompilationError(str(exc)) from None
    parts = {daypart} | ({"dusk"} if include_dusk_in == daypart else set())

    with SessionLocal() as db:
        analysed = db.query(models.MotionAnalysis).count()
        rows = (db.query(models.Asset)
                .join(models.MotionAnalysis, models.MotionAnalysis.asset_id == models.Asset.id)
                .filter(models.Asset.kind == "video", models.Asset.duration.isnot(None),
                        models.MotionAnalysis.is_hyperlapse == 1,
                        models.MotionAnalysis.daypart.in_(sorted(parts))).all())
        rows = [a for a in rows if Path(a.path or "").is_file()]
        for a in rows:
            db.expunge(a)
    if not rows:
        hint = " Run POST /api/motion/analyze first." if not analysed else ""
        raise CompilationError(f"no hyperlapse clips found for daypart {'+'.join(sorted(parts))}.{hint}")

    unique, dup_dropped = dedupe(rows)
    ordered = order_assets(unique, order)
    clips = []
    for a in ordered:
        head, tail = edge_trim(a.path, float(a.duration))
        length = float(a.duration) - head - tail
        if length < min_clip_s:
            continue
        clips.append({"asset": a, "start": head, "end": float(a.duration) - tail})
    if not clips:
        raise CompilationError("every hyperlapse clip is shorter than min_clip_s")
    natural = sum(c["end"] - c["start"] for c in clips)

    warnings: list[str] = []
    speed = 1.0
    if min_seconds and natural < float(min_seconds):
        speed = max(MIN_SLOWDOWN, natural / float(min_seconds))
        if natural / speed < float(min_seconds) - 0.05:
            warnings.append(f"even at {MIN_SLOWDOWN}x the video is {natural / speed:.1f}s, below the "
                            f"requested {float(min_seconds):.0f}s (looping is not allowed)")
    lengths = [(c["end"] - c["start"]) / speed for c in clips]

    music = None
    start = 0.0
    kinds: list[str] = []
    if music_path:
        p = Path(music_path).expanduser()
        if not p.is_file():
            raise CompilationError(f"music file not found: {music_path}")
        bm = beatmap.load(str(p))
        if bm is None:
            raise CompilationError("this track has no beat map; run scripts/beatmap.py on it first")
        total_est = sum(lengths)
        start = float(beatmap.nearest(bm.downbeats, music_start)) if music_start is not None \
            else beatmap.default_start(bm, total_est)
        if start + total_est > bm.duration + 0.5:
            start = max(0.0, beatmap.nearest([d for d in bm.downbeats if d + total_est <= bm.duration] or [0.0], start))
        if start + total_est > bm.duration + 0.5:
            raise CompilationError(f"the track is too short for {total_est:.0f}s of footage "
                                   f"({bm.duration:.0f}s); looping is not allowed")
        downs = beatmap.rel(bm.downbeats, start, total_est + 1.0)
        beats = beatmap.rel(bm.beats, start, total_est + 1.0)
        snap = snap_to_music(lengths, downs, beats, min_clip_s)
        for c, (head, tail), new_len in zip(clips, snap["trims"], snap["lengths"]):
            c["start"] = round(c["start"] + head * speed, 3)
            c["end"] = round(c["end"] - tail * speed, 3)
        lengths = [(c["end"] - c["start"]) / speed for c in clips]
        kinds = snap["kinds"]
        music = {"path": str(p), "start": round(start, 3), "bpm": bm.bpm, "bar_s": round(bm.bar_s, 4),
                 "drop_rel": None, "track_duration": bm.duration}
    total = round(sum(lengths), 3)
    if total < TARGET_S:
        warnings.append(f"natural length {total:.1f}s is under 30s; pass min_seconds=30 to slow the "
                        f"clips down to {MIN_SLOWDOWN}x at most (looping is not allowed)")

    plan_id = planner._new_plan_id()
    cuts, t = [], 0.0
    for i, (c, ln) in enumerate(zip(clips, lengths)):
        cuts.append({"index": i + 1, "start": round(t, 4), "slot": round(ln, 4), "is_drop_cut": False,
                     "snap": kinds[i] if kinds else None,
                     "clips": [{"asset_id": c["asset"].id, "scene_id": None,
                                "start": round(c["start"], 3), "end": round(c["end"], 3)}]})
        t += ln
    caption_beats = []
    if text_line:
        t0 = cuts[min(1, len(cuts) - 1)]["start"] if music else 1.6
        caption_beats = broll.caption_beats_for(text_line, t0, 0.45, total)
    label = (title or "").strip() or f"{daypart} hyperlapse compilation"
    sidecar = {
        "kind": "broll", "compilation": True, "plan_id": plan_id, "title": title, "ratio": ratio,
        "duration": total, "silent": music is None, "music": music, "speed": round(speed, 4),
        "daypart": sorted(parts), "order": order, "natural_seconds": round(natural, 3),
        "clip_count": len(clips), "duplicates_dropped": dup_dropped,
        "cuts": cuts, "cut_count": len(cuts), "bars": 0, "bars_per_cut": 0,
        "downbeats_rel": [], "reused_assets": 0, "reused_ranges": 0,
        "snap_summary": {k: kinds.count(k) for k in ("down", "beat", "free")} if kinds else None,
        "text_line": text_line or None, "caption_beats": caption_beats, "caption_style": "kinetic",
        "hook_text": opts["hook_text"], "brand": opts["brand"], "broll_filter": None,
        "fade_out_s": broll.FADE_OUT_S, "warnings": warnings,
    }
    plan = models.EditPlan(
        id=plan_id, intent=f"compilation: {label}", status="draft",
        summary=(f"Hyperlapse compilation ({'+'.join(sorted(parts))}): {len(clips)} clips, {total:.1f}s"
                 + (f", {speed:.2f}x" if speed < 1 else "") + "."),
        parsed_json='{"broll": true, "compilation": true}', target_ratio=ratio, total_duration=total)
    broll.persist_plan(plan, sidecar, {})
    result = planner.get_plan(plan_id) or {}
    summary = broll.broll_summary(plan_id) or {}
    summary.update({"natural_seconds": round(natural, 2), "speed": round(speed, 3), "clip_count": len(clips),
                    "duplicates_dropped": dup_dropped, "warnings": warnings,
                    "snap_summary": sidecar["snap_summary"]})
    result["broll"] = summary
    return result
