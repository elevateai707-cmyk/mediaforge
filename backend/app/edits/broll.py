"""Music-led B-roll plans: drone clips cut exactly on the music's downbeats.

A plan is an ordinary EditPlan (hard-cut clips, status "draft") plus a sidecar at
``EXPORTS_DIR/broll/<plan_id>/broll.json`` that the renderer reads for the music
track, its start offset, the on-screen text and the brand end card. Cuts land on
bar lines of the track's beat map; the total is a whole number of bars. With
``silent=True`` no music is used and cuts come every ~1.6 s.
"""
from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import Any, Optional

from .. import config, models, persist
from ..db import SessionLocal
from . import beatmap, brands, kinetic, story
from .plan_history import atomic_write, validate_id

log = logging.getLogger("mediaforge.broll")

SILENT_CUT_S = 1.6
MIN_ASSET_S = 2.0           # shorter clips are skipped
START_SKIP_S = 1.0          # avoid the takeoff wobble
FADE_OUT_S = 0.5
TEXT_BAR_INDEX = 1          # text line appears on the downbeat of bar 2
MUSIC_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
RATIOS = ("9:16", "16:9", "4:5", "1:1")
MIN_TARGET_S = 30.0         # B-roll videos are at least 30 s
# Normal (non-hyperlapse) drone footage must fly smoothly. On the real library (117 normal
# clips) the score is bunched near 1.0 (median 0.97) and the rough clips sit well below: 0.8
# drops the least steady ~20% of clips and, via the per-second windows, the rough stretches of
# the rest. Callers can override with broll_filter.min_smoothness.
DEFAULT_MIN_SMOOTHNESS = 0.8


class BrollError(ValueError):
    """Invalid request or nothing usable to build the plan (HTTP 422)."""


def broll_dir(plan_id: str) -> Path:
    validate_id(plan_id)
    return config.EXPORTS_DIR / "broll" / plan_id


def get_broll(plan_id: str) -> Optional[dict]:
    try:
        f = broll_dir(plan_id) / "broll.json"
    except ValueError:
        return None
    if not f.is_file():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def broll_summary(plan_id: str) -> Optional[dict]:
    b = get_broll(plan_id)
    if not b:
        return None
    return {k: b.get(k) for k in ("duration", "silent", "music", "cut_count", "text_line",
                                  "hook_text", "bars", "bars_per_cut")} | {
        "brand": (b.get("brand") or {}).get("id")}


# ---------------------------------------------------------------------------
# timeline math (pure)
# ---------------------------------------------------------------------------

def bar_timeline(downbeats: list[float], start: float, target: float, bars_per_cut: int,
                 bar_s: float) -> dict:
    """Cut times on bar lines.

    Returns {"start": downbeat used, "bars": N, "total": seconds, "cuts": [rel start of
    each cut], "slots": [length of each cut], "downbeats_rel": [...]}. N is the whole
    number of bars nearest ``target`` (min ``bars_per_cut``), shifted earlier when the
    track does not have enough downbeats after ``start``.
    """
    if len(downbeats) < 2:
        raise BrollError("the beat map has too few downbeats")
    n = max(bars_per_cut, int(round(target / bar_s)))
    if len(downbeats) - 1 < n:
        raise BrollError(f"the track is too short for {n} bars (it has {len(downbeats) - 1})")
    i0 = min(range(len(downbeats)), key=lambda i: abs(downbeats[i] - start))
    i0 = min(i0, len(downbeats) - 1 - n)
    rel = [round(downbeats[i0 + j] - downbeats[i0], 4) for j in range(n + 1)]
    cut_idx = list(range(0, n, bars_per_cut)) + [n]
    cuts = [rel[i] for i in cut_idx]
    slots = [round(b - a, 4) for a, b in zip(cuts, cuts[1:])]
    return {"start": downbeats[i0], "bars": n, "total": rel[n], "cuts": cuts[:-1],
            "slots": slots, "downbeats_rel": rel}


def silent_timeline(target: float) -> dict:
    n = max(2, int(round(target / SILENT_CUT_S)))
    slot = round(target / n, 4)
    return {"start": 0.0, "bars": 0, "total": round(slot * n, 4),
            "cuts": [round(slot * i, 4) for i in range(n)], "slots": [slot] * n,
            "downbeats_rel": []}


# ---------------------------------------------------------------------------
# music + clips
# ---------------------------------------------------------------------------

def _mapped_tracks(root: Path) -> list[Path]:
    out: list[Path] = []
    if not root.is_dir():
        return out
    root = root.resolve()
    for f in sorted(root.rglob("*")):
        try:
            resolved = f.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        if f.is_file() and f.suffix.lower() in MUSIC_EXTS and ".beats" not in f.parts \
                and beatmap.beatmap_path(str(resolved)).is_file():
            out.append(resolved)
    return out


def _pick_music(music_path: Optional[str], seed: Optional[int], need_s: float) -> tuple[str, beatmap.BeatMap]:
    if music_path:
        p = Path(music_path).expanduser()
        if not p.is_file():
            raise BrollError(f"music file not found: {music_path}")
        bm = beatmap.load(str(p))
        if bm is None:
            raise BrollError("this track has no beat map; run scripts/beatmap.py on it first")
        return str(p), bm
    candidates = []
    for t in _mapped_tracks(Path(persist.music_dir()).expanduser() if persist.music_dir() else Path("/nonexistent")):
        bm = beatmap.load(str(t))
        if bm is not None and bm.duration >= need_s + 2 * bm.bar_s:
            candidates.append((t, bm))
    if not candidates:
        raise BrollError("no music track with a beat map was found; pass music_path or run "
                         "scripts/beatmap.py on the music folder (or use silent=true)")
    t, bm = random.Random(seed).choice(candidates)
    return str(t), bm


def _usable_start(c: dict) -> float:
    s = c.get("start")
    return max(START_SKIP_S, float(s)) if s is not None else START_SKIP_S


def segments_for(c: dict, need: float) -> list[tuple[float, float]]:
    """Non-overlapping ``need``-second ranges of a candidate: inside its smooth windows when it
    has them (skipping the takeoff second), else from its usable start to the end."""
    dur = float(c["duration"])
    wins = c.get("windows")
    segs: list[tuple[float, float]] = []
    if wins:
        spans = [(float(w[0]), float(w[1])) for w in wins]
        spans = [(max(a, START_SKIP_S) if b - max(a, START_SKIP_S) >= need else a, b) for a, b in spans]
    else:
        spans = [(_usable_start(c), dur)]
    for a, b in spans:
        for i in range(int((b - a + 1e-6) // need)):
            segs.append((round(a + i * need, 3), round(a + (i + 1) * need, 3)))
    return segs


def assign_clips(slots: list[float], candidates: list[dict], drop_cut: Optional[int]) -> tuple[list[list[dict]], int, int]:
    """One entry per cut: the clips filling it.

    Preference order: an unused asset; then a DIFFERENT range of an already used asset;
    then (clips shorter than the cut) several short clips from unused assets; only as a
    last resort an identical range again. The best candidate goes to ``drop_cut``.
    Returns (clips per cut, cuts reusing an asset, cuts repeating an identical range).
    """
    if not candidates:
        raise BrollError("no usable drone clips match this request (check broll_filter, and that "
                         "POST /api/motion/analyze has run for smoothness/hyperlapse filters)")
    used_assets: set[int] = set()
    used_ranges: set[tuple[int, float]] = set()
    reused_assets = reused_ranges = 0
    result: list[Optional[list[dict]]] = [None] * len(slots)
    order = ([drop_cut] if drop_cut is not None else []) + \
        [i for i in range(len(slots)) if i != drop_cut]

    def clip(c: dict, seg: tuple[float, float]) -> list[dict]:
        return [{"asset_id": c["asset_id"], "scene_id": c.get("scene_id"),
                 "start": seg[0], "end": seg[1]}]

    for i in order:
        need = slots[i]
        segs = {c["asset_id"]: segments_for(c, need) for c in candidates}
        pick = None
        for c in candidates:                                   # 1. unused asset
            if c["asset_id"] not in used_assets and segs[c["asset_id"]]:
                pick = (c, segs[c["asset_id"]][0])
                break
        if pick is None:                                       # 2. another range of a used asset
            for c in candidates:
                free = [g for g in segs[c["asset_id"]] if (c["asset_id"], g[0]) not in used_ranges]
                if free:
                    pick = (c, free[0])
                    reused_assets += 1
                    break
        if pick is not None:
            c, seg = pick
            result[i] = clip(c, seg)
            used_assets.add(c["asset_id"])
            used_ranges.add((c["asset_id"], seg[0]))
            continue
        fresh = [c for c in candidates if c["asset_id"] not in used_assets] or candidates
        try:                                                   # 3. stitch short clips together
            cl = story.allocate_clips(need, [dict(c, start=_usable_start(c)) for c in fresh], set())
            result[i] = [{k: x[k] for k in ("asset_id", "scene_id", "start", "end")} for x in cl]
            used_assets.update(x["asset_id"] for x in cl)
            if fresh is candidates:
                reused_assets += 1
        except story.StoryError as exc:
            raise BrollError(str(exc)) from None
        if any((x["asset_id"], x["start"]) in used_ranges for x in result[i]):
            reused_ranges += 1
        used_ranges.update((x["asset_id"], x["start"]) for x in result[i])
    return [r or [] for r in result], reused_assets, reused_ranges


# ---------------------------------------------------------------------------
# public
# ---------------------------------------------------------------------------

def create_broll_plan(music_path: Optional[str] = None, music_start: Optional[float] = None,
                      target_seconds: float = 30.0, bars_per_cut: int = 1, text_line: str = "",
                      brand: Optional[str] = None, broll_filter: Optional[dict] = None,
                      silent: bool = False, asset_ids: Optional[list[int]] = None,
                      hook_text: Optional[str] = None, ratio: str = "9:16",
                      title: Optional[str] = None, seed: Optional[int] = None) -> dict:
    from . import planner

    if bars_per_cut not in (1, 2):
        raise BrollError("bars_per_cut must be 1 or 2")
    if not MIN_TARGET_S <= float(target_seconds) <= 90:
        raise BrollError("target_seconds must be between 30 and 90 (B-roll videos are at least 30 s)")
    if ratio not in RATIOS:
        raise BrollError(f"ratio must be one of {', '.join(RATIOS)}")
    text_line = " ".join((text_line or "").split())
    if len(text_line) > 90:
        raise BrollError("text_line must be at most 90 characters")
    raw_filter = dict(broll_filter or {})
    # Smooth, non-hyperlapse footage by default; an explicit null/value overrides.
    raw_filter.setdefault("hyperlapse", False)
    raw_filter.setdefault("min_smoothness", DEFAULT_MIN_SMOOTHNESS)
    try:
        opts = story.validate_story_options("x", raw_filter, None, hook_text, brand)
    except story.StoryError as exc:
        raise BrollError(str(exc)) from None
    flt = dict(opts["broll_filter"] or {})
    priority: list[int] = []
    if asset_ids:
        try:
            priority = [int(i) for i in dict.fromkeys(asset_ids)]
        except (TypeError, ValueError):
            raise BrollError("asset_ids must be integers") from None
        flt["asset_ids"] = sorted(set(flt.get("asset_ids") or priority) & set(priority)) or priority

    # -- timeline --------------------------------------------------------------
    music: Optional[dict] = None
    drop_rel: Optional[float] = None
    if silent:
        tl = silent_timeline(float(target_seconds))
    else:
        mpath, bm = _pick_music(music_path, seed, float(target_seconds))
        start = beatmap.nearest(bm.downbeats, music_start) if music_start is not None \
            else beatmap.default_start(bm, float(target_seconds))
        tl = bar_timeline(bm.downbeats, float(start), float(target_seconds), bars_per_cut, bm.bar_s)
        if bm.drop_s is not None and tl["start"] <= bm.drop_s < tl["start"] + tl["total"]:
            drop_rel = round(bm.drop_s - tl["start"], 4)
        music = {"path": mpath, "start": tl["start"], "bpm": bm.bpm, "bar_s": round(bm.bar_s, 4),
                 "drop_rel": drop_rel, "track_duration": bm.duration}

    drop_cut = None
    if drop_rel is not None:
        drop_cut = min(range(len(tl["cuts"])), key=lambda i: abs(tl["cuts"][i] - drop_rel))

    # -- footage -------------------------------------------------------------------
    plan_id = planner._new_plan_id()
    out_dir = broll_dir(plan_id)
    with SessionLocal() as db:
        pool, windows = story.filtered_video_pool(db, None, flt or None)
        pool = {i: a for i, a in pool.items() if float(a.duration) >= MIN_ASSET_S
                and Path(a.path or "").is_file()}
        cands = []
        for aid, a in pool.items():
            scene = (db.query(models.Scene).filter(models.Scene.asset_id == aid)
                     .order_by(models.Scene.index).first())
            cands.append({"asset_id": aid, "scene_id": scene.id if scene else None,
                          "start": float(scene.start) if scene and scene.start is not None else None,
                          "duration": float(a.duration), "score": float(a.aesthetic_score or 0.0),
                          "path": a.path, "windows": windows.get(aid)})
        if priority:
            rank = {i: n for n, i in enumerate(priority)}
            cands.sort(key=lambda c: rank.get(c["asset_id"], 10 ** 6))
        else:
            cands.sort(key=lambda c: (-c["score"], c["asset_id"]))
        per_cut, reused, reused_ranges = assign_clips(tl["slots"], cands, drop_cut)
        score_of = {c["asset_id"]: c["score"] for c in cands}

        label = (title or "").strip() or text_line or "drone cut"
        total = tl["total"]
        plan = models.EditPlan(
            id=plan_id, intent=f"broll: {label}", status="draft",
            summary=(f"Music-led B-roll: {len(tl['slots'])} cuts, {total:.1f}s"
                     + (", silent" if silent else f", {music['bpm']:.0f} bpm") + "."),
            parsed_json=json.dumps({"broll": True}), target_ratio=ratio, total_duration=total)

        # caption words for the text line: one word per beat from bar 2's downbeat
        if silent:
            t0, step = SILENT_CUT_S, 0.4
        else:
            t0 = tl["downbeats_rel"][min(TEXT_BAR_INDEX, len(tl["downbeats_rel"]) - 1)]
            step = max(0.25, music["bar_s"] / 4)
        caption_beats = caption_beats_for(text_line, t0, step, total)

    sidecar = {
        "kind": "broll", "plan_id": plan_id, "title": title, "ratio": ratio,
        "duration": total, "silent": silent, "music": music, "bars": tl["bars"],
        "bars_per_cut": bars_per_cut, "cut_count": len(tl["slots"]),
        "downbeats_rel": tl["downbeats_rel"], "reused_assets": reused, "reused_ranges": reused_ranges,
        "cuts": [{"index": i + 1, "start": tl["cuts"][i], "slot": tl["slots"][i],
                  "is_drop_cut": i == drop_cut, "clips": per_cut[i]}
                 for i in range(len(tl["slots"]))],
        "text_line": text_line or None, "caption_beats": caption_beats,
        "caption_style": "kinetic", "hook_text": opts["hook_text"], "brand": opts["brand"],
        "broll_filter": opts["broll_filter"], "fade_out_s": FADE_OUT_S,
    }
    persist_plan(plan, sidecar, score_of)
    result = planner.get_plan(plan_id) or {}
    result["broll"] = broll_summary(plan_id)
    return result


def caption_beats_for(text_line: str, t0: float, step: float, total: float) -> list[dict]:
    """One-word-per-step kinetic caption beat for a short text line (empty list if no text)."""
    if not text_line:
        return []
    clean, spans = kinetic.parse_emphasis(text_line)
    words_in = clean.split()
    words = [{"word": w, "start": round(i * step, 3), "end": round(i * step + step * 0.9, 3)}
             for i, w in enumerate(words_in)]
    span_list = [list(sp) for sp in spans[:kinetic.MAX_EMPH_PER_BEAT]] or \
        [list(sp) for sp in kinetic.auto_emphasis(words_in)]
    return [{"index": 1, "text": clean, "start": round(t0, 3),
             "slot": max(0.5, round(total - t0, 3)), "words": words, "emphasis": span_list}]


def persist_plan(plan: "models.EditPlan", sidecar: dict, score_of: dict) -> None:
    """Write the sidecar, then the plan + one hard-cut EditClip per sidecar clip."""
    plan_id = plan.id
    out_dir = broll_dir(plan_id)
    atomic_write(out_dir / "broll.json", json.dumps(sidecar, indent=2))
    try:
        with SessionLocal() as db:
            db.add(plan)
            pos = 0
            for cut in sidecar["cuts"]:
                for c in cut["clips"]:
                    db.add(models.EditClip(
                        plan_id=plan_id, position=pos, asset_id=c["asset_id"],
                        scene_id=c.get("scene_id"), start=c["start"], end=c["end"],
                        caption=None, transition="cut",
                        score=round(score_of.get(c["asset_id"], 0.0), 2)))
                    pos += 1
            db.commit()
    except Exception:
        import shutil
        shutil.rmtree(out_dir, ignore_errors=True)
        raise
