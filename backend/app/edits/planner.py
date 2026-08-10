"""Edit planner: intent string -> draft EditPlan (draft -> approved lifecycle).

Pipeline:
1. Try Ollama (qwen2.5vl:7b via ``app/ai/ollama_llm.py``) with a strict JSON
   prompt listing the real candidate scenes, so the model can only pick
   asset/scene ids that exist in the library.
2. If Ollama is down, the model output is not valid JSON (retried once with a
   repair instruction), or none of the returned clips validate -> a fully
   deterministic greedy fallback picks top-aesthetic scenes matching intent
   keywords and budgets them to the target duration parsed from the intent
   ("60-second", "60s", ratio "9:16" etc.).

The plan is persisted as EditPlan(status='draft') + EditClip rows. Rendering
is gated behind POST /api/edits/plan/{id}/approve (hard approval gate).
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import or_

from .. import models
from ..db import SessionLocal

log = logging.getLogger("mediaforge.planner")

# Contract transition names the renderer understands.
KNOWN_TRANSITIONS = {"crossfade", "fade"}

DEFAULT_DURATION = 30.0
DEFAULT_RATIO = "9:16"
MIN_CLIP = 1.0   # shortest usable clip, seconds
MAX_CLIP = 12.0  # longest usable clip, seconds

# ---------------------------------------------------------------------------
# Intent parsing (duration + target ratio)
# ---------------------------------------------------------------------------

_RATIO_RE = re.compile(r"(\d+)\s*[:/x×]\s*(\d+)")
_DUR_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-?\s*)?(second|sec|s|minute|min)\b", re.IGNORECASE)


def _parse_ratio(text: str) -> str:
    """Extract a "W:H" ratio from the intent; default 9:16."""
    m = _RATIO_RE.search(text)
    if not m:
        return DEFAULT_RATIO
    a, b = int(m.group(1)), int(m.group(2))
    if a <= 0 or b <= 0 or a > 100 or b > 100:
        return DEFAULT_RATIO
    # Normalize known orientations; keep arbitrary ratios as-is.
    if (a, b) in ((9, 16), (16, 9), (1, 1)):
        return f"{a}:{b}"
    return f"{a}:{b}"


def _parse_duration(text: str) -> float:
    """Target duration in seconds from "60-second" / "60s" / "2 minutes"."""
    m = _DUR_RE.search(text)
    if not m:
        return DEFAULT_DURATION
    value = float(m.group(1))
    unit = m.group(2).lower()
    if unit.startswith("min"):
        value *= 60
    if value < 3:
        value = 3.0
    if value > 900:
        value = 900.0
    return value


def _clamp_duration(d: float) -> float:
    return max(MIN_CLIP, min(MAX_CLIP, d))


# ---------------------------------------------------------------------------
# Candidate gathering (shared by the LLM prompt and the fallback)
# ---------------------------------------------------------------------------

def _candidate_sources(db, limit: int = 40) -> list[dict]:
    """Scenes of video assets ordered by aesthetic score, then whole assets.

    Returns a list of dicts:
      {asset_id, scene_id (or None), start, end, caption, score, path, kind}
    Videos with detected scenes contribute one entry per scene; videos without
    scenes contribute one entry spanning the whole file; photos contribute a
    static entry when no video material exists at all.
    """
    sources: list[dict] = []

    # Scenes (video segmentation) first — highest value edit material.
    scene_rows = (
        db.query(models.Scene, models.Asset)
        .join(models.Asset, models.Asset.id == models.Scene.asset_id)
        .filter(models.Asset.kind == "video")
        .order_by(models.Scene.aesthetic_score.desc().nullslast())
        .limit(limit * 2)
        .all()
    )
    for scene, asset in scene_rows:
        dur = max(0.0, float(scene.end or 0.0) - float(scene.start or 0.0))
        if dur < MIN_CLIP:
            continue
        sources.append({
            "asset_id": asset.id, "scene_id": scene.id,
            "start": float(scene.start or 0.0),
            "end": float(scene.end or 0.0),
            "caption": scene.caption or asset.caption or "",
            "score": float(scene.aesthetic_score or 0.0),
            "path": asset.path, "kind": asset.kind,
        })

    # Whole videos with no usable scenes (or too few scenes).
    if len(sources) < limit:
        seen = {s["scene_id"] for s in sources}
        video_rows = (
            db.query(models.Asset)
            .filter(models.Asset.kind == "video")
            .order_by(models.Asset.aesthetic_score.desc().nullslast())
            .limit(limit)
            .all()
        )
        for asset in video_rows:
            if asset.scene_count and asset.id in {s["asset_id"] for s in sources}:
                continue  # already covered by its scenes
            dur = float(asset.duration or 5.0)
            sources.append({
                "asset_id": asset.id, "scene_id": None,
                "start": 0.0, "end": max(MIN_CLIP, dur),
                "caption": asset.caption or "",
                "score": float(asset.aesthetic_score or 0.0),
                "path": asset.path, "kind": asset.kind,
            })

    # Photos as static clips only when there is no video at all.
    if not sources:
        photo_rows = (
            db.query(models.Asset)
            .filter(models.Asset.kind == "photo")
            .order_by(models.Asset.aesthetic_score.desc().nullslast())
            .limit(limit)
            .all()
        )
        for asset in photo_rows:
            sources.append({
                "asset_id": asset.id, "scene_id": None,
                "start": 0.0, "end": 4.0,
                "caption": asset.caption or "",
                "score": float(asset.aesthetic_score or 0.0),
                "path": asset.path, "kind": asset.kind,
            })

    sources.sort(key=lambda s: (s["score"], s["asset_id"]), reverse=True)
    return sources[:limit]


def _intent_keywords(intent: str) -> list[str]:
    """Lowercased, deduped content words from the intent (for fallback match)."""
    stop = {
        "a", "an", "the", "of", "to", "for", "with", "and", "or", "in", "on",
        "at", "by", "from", "my", "our", "their", "his", "her", "its", "is",
        "are", "was", "were", "be", "been", "being", "i", "we", "you", "it",
        "this", "that", "these", "those", "highlight", "reel", "video", "clips",
        "clip", "short", "shorts", "vertical", "horizontal", "square", "ratio",
        "second", "seconds", "minute", "minutes", "upbeat", "vibe", "style",
        "make", "create", "edit", "with", "tiktok", "instagram", "reels",
        "youtube", "shorts", "9:16", "16:9", "1:1", "60", "30", "15", "10",
    }
    words = re.findall(r"[a-z0-9]{3,}", intent.lower())
    return list(dict.fromkeys(w for w in words if w not in stop))


def _caption_text(asset: models.Asset, scene: Optional[models.Scene]) -> str:
    parts = [scene.caption or ""] if scene else []
    parts.append(asset.caption or "")
    return " ".join(p for p in parts if p).lower()


# ---------------------------------------------------------------------------
# Deterministic fallback planner
# ---------------------------------------------------------------------------

def _deterministic_plan(intent: str, db) -> dict:
    """Greedy: keyword-matched scenes by aesthetic score, budgeted to duration."""
    target = _parse_duration(intent)
    ratio = _parse_ratio(intent)
    keywords = _intent_keywords(intent)
    sources = _candidate_sources(db)

    if keywords:
        scored: list[tuple[float, dict]] = []
        for s in sources:
            text = f"{s['caption']} {s['path']}".lower()
            hits = sum(1 for k in keywords if k in text)
            if hits:
                scored.append((hits, s))
        scored.sort(key=lambda t: (t[0], t[1]["score"]), reverse=True)
        picked = [s for _h, s in scored]
        if not picked:
            picked = sources
    else:
        picked = sources

    clips: list[dict] = []
    budget = 0.0
    for s in picked:
        if budget >= target - 0.25:
            break
        dur = _clamp_duration(min(float(s["end"]) - float(s["start"]), MAX_CLIP))
        if budget + dur > target + 0.5:
            dur = max(MIN_CLIP, target - budget)  # final clip to hit the target
        clips.append({
            "asset_id": s["asset_id"],
            "scene_id": s.get("scene_id"),
            "start": round(float(s["start"]), 3),
            "end": round(float(s["start"]) + dur, 3),
            "caption": (s.get("caption") or "").strip() or None,
            "transition": "crossfade",
            "score": round(float(s.get("score") or 0.0), 2),
        })
        budget += dur

    matched = len(keywords) > 0
    summary = (
        f"Deterministic plan from {len(clips)} clip(s) "
        + ("matched to intent keywords." if matched else "(no keyword matches; top aesthetic scenes).")
    )
    return {
        "summary": summary,
        "clips": clips,
        "total_duration": round(budget, 2),
        "target_ratio": ratio,
        "source": "fallback",
    }


# ---------------------------------------------------------------------------
# Ollama JSON planner
# ---------------------------------------------------------------------------

_OLLAMA_IMPORT_ERROR: Optional[str] = None
try:
    from ..ai.ollama_llm import generate as _ollama_generate  # type: ignore
except Exception as exc:  # pragma: no cover - ai package may not exist yet
    _OLLAMA_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
    _ollama_generate = None


def _ollama_prompt(intent: str, sources: list[dict], target: float,
                   ratio: str) -> str:
    """Strict JSON prompt; the model may only reference the listed scenes."""
    lines = []
    for s in sources:
        cap = (s["caption"] or "")[:160].replace("\n", " ")
        lines.append(
            f'  {{"asset_id": {s["asset_id"]}, "scene_id": {s["scene_id"]}, '
            f'"start": {s["start"]:.2f}, "end": {s["end"]:.2f}, '
            f'"score": {s["score"]:.1f}, "caption": "{cap}"}}'
        )
    return (
        "You are a video editing assistant. Build a highlight reel edit plan.\n"
        "User intent: " + intent + "\n\n"
        "Available source scenes (asset_id, scene_id, in/out timestamps in "
        "seconds, aesthetic score 0-10, caption) — you may ONLY use these:\n"
        "[\n" + ",\n".join(lines) + "\n]\n\n"
        f"Target total duration: {target:.0f} seconds. "
        f"Target aspect ratio: {ratio}.\n\n"
        "Respond with ONLY a JSON object (no markdown, no commentary) of the form:\n"
        '{\n'
        '  "summary": "one sentence describing the reel",\n'
        f'  "target_ratio": "{ratio}",\n'
        f'  "total_duration": {target:.0f},\n'
        '  "clips": [\n'
        '    {"asset_id": 1, "scene_id": 2, "start": 2.0, "end": 7.5, '
        '"caption": "short caption", "transition": "crossfade", "score": 8.1}\n'
        '  ]\n'
        "}\n"
        "Rules: pick 3-12 clips that best match the intent; each clip start/end "
        "must lie inside the scene's [start, end] range; end > start; clip "
        "duration 1-12 seconds; sum of clip durations approximates the target "
        "total duration; transition must be 'crossfade'; score 0-10."
    )


def _extract_json(text: str) -> Optional[dict]:
    """Tolerant JSON extraction: strips fences, grabs the outermost object."""
    if not text:
        return None
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(stripped[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None


def _normalize_clips(clips: Any, db) -> list[dict]:
    """Validate/normalize model clips; drop anything referencing unknown ids."""
    out: list[dict] = []
    if not isinstance(clips, list):
        return out
    for c in clips:
        if not isinstance(c, dict):
            continue
        raw_asset_id = c.get("asset_id")
        if raw_asset_id is None:
            continue
        try:
            asset_id = int(raw_asset_id)
        except (TypeError, ValueError):
            continue
        asset = db.get(models.Asset, asset_id)
        if asset is None:
            continue
        try:
            start = max(0.0, float(c.get("start", 0.0)))
            end = float(c.get("end", start))
        except (TypeError, ValueError):
            continue
        if end - start < MIN_CLIP:
            end = start + MIN_CLIP
        if asset.duration and end > float(asset.duration):
            end = float(asset.duration)
        if end - start < MIN_CLIP:
            continue
        transition = str(c.get("transition", "crossfade")).lower()
        if transition not in KNOWN_TRANSITIONS:
            transition = "crossfade"
        scene_id = c.get("scene_id")
        try:
            scene_id = int(scene_id) if scene_id is not None else None
        except (TypeError, ValueError):
            scene_id = None
        try:
            score = round(float(c.get("score", 0.0)), 2)
        except (TypeError, ValueError):
            score = 0.0
        caption = str(c.get("caption", "")).strip() or None
        out.append({
            "asset_id": asset_id, "scene_id": scene_id,
            "start": round(start, 3), "end": round(end, 3),
            "caption": caption, "transition": transition, "score": score,
        })
    return out


def _ollama_plan(intent: str, db) -> Optional[dict]:
    """Ask Ollama for the plan; None on any failure (caller falls back)."""
    if _ollama_generate is None:
        log.info("ollama_llm unavailable (%s); using fallback planner",
                 _OLLAMA_IMPORT_ERROR)
        return None
    target = _parse_duration(intent)
    ratio = _parse_ratio(intent)
    sources = _candidate_sources(db, limit=30)
    if not sources:
        return None

    prompt = _ollama_prompt(intent, sources, target, ratio)
    for attempt in (1, 2):
        try:
            repair = (
                "Your previous response was not valid JSON. "
                "Return ONLY a valid JSON object, no markdown, no extra text. "
                "Same schema as before."
            ) if attempt == 2 else None
            text = _ollama_generate(
                "qwen2.5vl:7b", prompt + ("\n" + repair if repair else ""),
                format="json", temperature=0.2, retries=5,
            )
        except Exception as exc:  # noqa: BLE001 - ollama down -> fallback
            log.warning("ollama plan failed (attempt %d): %s", attempt, exc)
            continue
        data = _extract_json(text)
        if not isinstance(data, dict):
            continue
        clips = _normalize_clips(data.get("clips"), db)
        if not clips:
            continue
        return {
            "summary": str(data.get("summary", "")).strip() or
                       f"AI plan with {len(clips)} clips.",
            "clips": clips,
            "total_duration": round(sum(c["end"] - c["start"] for c in clips), 2),
            "target_ratio": str(data.get("target_ratio", ratio)),
            "source": "ollama",
        }
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _new_plan_id() -> str:
    return uuid.uuid4().hex[:12]


def _clip_dict(c: models.EditClip) -> dict:
    return {
        "asset_id": c.asset_id,
        "scene_id": c.scene_id,
        "start": round(float(c.start), 3),
        "end": round(float(c.end), 3),
        "caption": c.caption,
        "transition": c.transition or "crossfade",
        "score": round(float(c.score or 0.0), 2),
    }


def _plan_dict(plan: models.EditPlan) -> dict:
    return {
        "plan_id": plan.id,
        "status": plan.status,
        "summary": plan.summary,
        "clips": [_clip_dict(c) for c in plan.clips],
        "total_duration": round(float(plan.total_duration or 0.0), 2),
        "target_ratio": plan.target_ratio or DEFAULT_RATIO,
        "intent": plan.intent,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
    }


def create_plan(intent: str) -> dict:
    """POST /api/edits/plan: build + persist a draft plan; never raises for
    planning failures (deterministic fallback always produces a valid plan)."""
    with SessionLocal() as db:
        plan_data = _ollama_plan(intent, db) or _deterministic_plan(intent, db)
        plan_id = _new_plan_id()
        plan = models.EditPlan(
            id=plan_id,
            intent=intent,
            status="draft",
            summary=plan_data.get("summary"),
            target_ratio=plan_data.get("target_ratio", DEFAULT_RATIO),
            total_duration=plan_data.get("total_duration", 0.0),
        )
        db.add(plan)
        for pos, c in enumerate(plan_data["clips"]):
            db.add(models.EditClip(
                plan_id=plan_id, position=pos,
                asset_id=c["asset_id"], scene_id=c.get("scene_id"),
                start=c["start"], end=c["end"], caption=c.get("caption"),
                transition=c.get("transition", "crossfade"),
                score=c.get("score", 0.0),
            ))
        db.commit()
        db.refresh(plan)
        return _plan_dict(plan)


def get_plan(plan_id: str) -> Optional[dict]:
    """GET /api/edits/plan/{plan_id}: full plan JSON or None."""
    with SessionLocal() as db:
        plan = db.get(models.EditPlan, plan_id)
        if plan is None:
            return None
        return _plan_dict(plan)


def update_plan(plan_id: str, clips: list[dict]) -> Optional[dict]:
    """PUT /api/edits/plan/{plan_id}: replace clip rows, recompute duration."""
    with SessionLocal() as db:
        plan = db.get(models.EditPlan, plan_id)
        if plan is None:
            return None
        if plan.status == "approved":
            # Editing an approved plan is allowed (returns to draft) — the
            # approval gate protects rendering, not reviewing.
            plan.status = "draft"
        valid = _normalize_clips(clips, db)
        for c in plan.clips:
            db.delete(c)
        db.flush()
        total = 0.0
        for pos, c in enumerate(valid):
            dur = max(0.0, float(c["end"]) - float(c["start"]))
            total += dur
            db.add(models.EditClip(
                plan_id=plan_id, position=pos,
                asset_id=c["asset_id"], scene_id=c.get("scene_id"),
                start=c["start"], end=c["end"], caption=c.get("caption"),
                transition=c.get("transition", "crossfade"),
                score=c.get("score", 0.0),
            ))
        plan.total_duration = round(total, 2)
        plan.summary = plan.summary or f"Edited plan with {len(valid)} clips."
        db.commit()
        db.refresh(plan)
        return _plan_dict(plan)


def approve_plan(plan_id: str) -> Optional[dict]:
    """POST /api/edits/plan/{plan_id}/approve: hard approval gate."""
    with SessionLocal() as db:
        plan = db.get(models.EditPlan, plan_id)
        if plan is None:
            return None
        if not plan.clips:
            raise ValueError("cannot approve a plan with no clips")
        plan.status = "approved"
        db.commit()
        return {"ok": True, "status": plan.status}


def list_plans(limit: int = 50) -> list[dict]:
    """GET /api/edits/plans: recent plans (newest first), clipped summaries."""
    with SessionLocal() as db:
        plans = (db.query(models.EditPlan)
                 .order_by(models.EditPlan.created_at.desc())
                 .limit(limit).all())
        return [_plan_dict(p) for p in plans]
