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
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import or_

from .. import models
from ..db import SessionLocal
from ..geo.gazetteer import haversine_km
from ..ingest.trips import trip_asset_ids
from .intent import Intent, parse_intent

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

def _place_asset_ids(db, parsed: Intent, radius_mult: float = 1.0) -> Optional[set[int]]:
    """Asset ids matching the requested place/trip.

    None = no place constraint. Empty set = place was requested and nothing matched.
    """
    if parsed.trip_id:
        return set(trip_asset_ids(db, parsed.trip_id))
    if not parsed.place:
        return None
    city = parsed.place.lower()
    radius = float(parsed.radius_km or 45.0) * radius_mult
    ids: set[int] = set()
    for asset in db.query(models.Asset).all():
        if (asset.city or "").lower() == city:
            ids.add(asset.id)
            continue
        if (asset.region or "").lower() == city:
            ids.add(asset.id)
            continue
        path_l = (asset.path or "").lower()
        if city in path_l:
            ids.add(asset.id)
            continue
        if (
            parsed.place_lat is not None and parsed.place_lon is not None
            and asset.gps_lat is not None and asset.gps_lon is not None
        ):
            dist = haversine_km(
                float(parsed.place_lat), float(parsed.place_lon),
                float(asset.gps_lat), float(asset.gps_lon),
            )
            if dist <= radius:
                ids.add(asset.id)
    return ids


def _restrict_ids(query, ids: Optional[set[int]]):
    if ids is None:
        return query
    if not ids:
        return query.filter(models.Asset.id == -1)
    return query.filter(models.Asset.id.in_(ids))


def _row_source(asset: models.Asset, scene=None) -> dict:
    if scene is not None:
        start = float(scene.start or 0.0)
        end = float(scene.end or 0.0)
        caption = scene.caption or asset.caption or ""
        score = float(scene.aesthetic_score or asset.aesthetic_score or 0.0)
        scene_id = scene.id
    else:
        start = 0.0
        end = max(MIN_CLIP, float(asset.duration or 4.0))
        caption = asset.caption or ""
        score = float(asset.aesthetic_score or 0.0)
        scene_id = None
    return {
        "asset_id": asset.id,
        "scene_id": scene_id,
        "start": start,
        "end": end,
        "caption": caption,
        "score": score,
        "path": asset.path,
        "kind": asset.kind,
        "city": asset.city,
        "taken_at": asset.taken_at.isoformat() if asset.taken_at else None,
    }


def _candidate_sources(db, parsed: Optional[Intent] = None, limit: int = 40,
                       trip_id: Optional[int] = None, selected_ids: Optional[list[int]] = None) -> tuple[list[dict], dict]:
    """Place-prefiltered scenes. Never mixes another city into a placed request."""
    if parsed is None:
        parsed = parse_intent("", trip_id=trip_id)
    elif trip_id and not parsed.trip_id:
        parsed.trip_id = trip_id

    ids = _place_asset_ids(db, parsed, radius_mult=1.0)
    stats = {
        "place": parsed.place,
        "radius_km": parsed.radius_km,
        "matched_assets": len(ids) if ids is not None else None,
        "widened": False,
    }
    if ids is not None:
        video_n = 0
        if ids:
            video_n = (
                db.query(models.Asset)
                .filter(models.Asset.id.in_(ids), models.Asset.kind == "video")
                .count()
            )
        if video_n < 3:
            wider = _place_asset_ids(db, parsed, radius_mult=2.0)
            if wider is not None and len(wider) > len(ids or []):
                ids = wider
                stats["widened"] = True
                stats["matched_assets"] = len(ids)
                stats["radius_km"] = float(parsed.radius_km or 45.0) * 2.0

    if selected_ids is not None:
        ids = set(selected_ids) if ids is None else ids.intersection(selected_ids)
    sources: list[dict] = []
    scene_q = (
        db.query(models.Scene, models.Asset)
        .join(models.Asset, models.Asset.id == models.Scene.asset_id)
        .filter(models.Asset.kind == "video")
    )
    scene_q = _restrict_ids(scene_q, ids)
    scene_rows = (
        scene_q
        .order_by(models.Scene.aesthetic_score.desc().nullslast())
        .limit(limit * 2)
        .all()
    )
    for scene, asset in scene_rows:
        dur = max(0.0, float(scene.end or 0.0) - float(scene.start or 0.0))
        if dur < MIN_CLIP:
            continue
        if not os.path.isfile(asset.path or ""):
            continue
        sources.append(_row_source(asset, scene))

    if len(sources) < limit:
        video_q = db.query(models.Asset).filter(models.Asset.kind == "video")
        video_q = _restrict_ids(video_q, ids)
        video_rows = (
            video_q
            .order_by(models.Asset.aesthetic_score.desc().nullslast())
            .limit(limit)
            .all()
        )
        have = {s["asset_id"] for s in sources}
        for asset in video_rows:
            if asset.scene_count and asset.id in have:
                continue
            if not os.path.isfile(asset.path or ""):
                continue
            sources.append(_row_source(asset))

    if not sources:
        photo_q = db.query(models.Asset).filter(models.Asset.kind == "photo")
        photo_q = _restrict_ids(photo_q, ids)
        photo_rows = (
            photo_q
            .order_by(models.Asset.aesthetic_score.desc().nullslast())
            .limit(limit)
            .all()
        )
        for asset in photo_rows:
            if not os.path.isfile(asset.path or ""):
                continue
            sources.append(_row_source(asset))

    if parsed.trip_id or parsed.place:
        sources.sort(key=lambda s: (s.get("taken_at") or "", s["start"]))
    else:
        sources.sort(key=lambda s: (s["score"], s["asset_id"]), reverse=True)
    stats["candidate_clips"] = len(sources[:limit])
    return sources[:limit], stats


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

def _deterministic_plan(intent: str, db, trip_id: Optional[int] = None,
                        parsed: Optional[Intent] = None, selected_ids: Optional[list[int]] = None) -> dict:
    """Greedy: place-prefiltered scenes, budgeted to duration."""
    parsed = parsed or parse_intent(intent, trip_id=trip_id)
    if trip_id:
        parsed.trip_id = trip_id
    target = parsed.duration_s
    ratio = parsed.ratio
    keywords = _intent_keywords(intent)
    sources, stats = _candidate_sources(db, parsed=parsed, trip_id=trip_id, selected_ids=selected_ids)

    if parsed.place and not sources:
        place = parsed.place
        return {
            "summary": (
                f"No media found in {place}. Scan a folder or widen the radius."
            ),
            "clips": [],
            "total_duration": 0.0,
            "target_ratio": ratio,
            "source": "empty",
            "parsed_intent": parsed.to_dict(),
            "match_stats": stats,
        }

    if keywords:
        scored: list[tuple[float, dict]] = []
        for s in sources:
            text = f"{s['caption']} {s['path']} {s.get('city') or ''}".lower()
            hits = sum(1 for k in keywords if k in text)
            scored.append((hits, s))
        scored.sort(key=lambda t: (t[0], t[1]["score"]), reverse=True)
        picked = [s for _h, s in scored]
    else:
        picked = sources

    # Diversity: at most 2 clips from the same source minute.
    clips: list[dict] = []
    budget = 0.0
    minute_counts: dict[tuple[int, int], int] = {}
    for s in picked:
        if budget >= target - 0.25:
            break
        minute_key = (int(s["asset_id"]), int(float(s["start"]) // 60))
        if minute_counts.get(minute_key, 0) >= 2:
            continue
        dur = _clamp_duration(min(float(s["end"]) - float(s["start"]), MAX_CLIP))
        if budget + dur > target + 0.5:
            dur = max(MIN_CLIP, target - budget)
        if dur < MIN_CLIP:
            continue
        clips.append({
            "asset_id": s["asset_id"],
            "scene_id": s.get("scene_id"),
            "start": round(float(s["start"]), 3),
            "end": round(float(s["start"]) + dur, 3),
            "caption": (s.get("caption") or "").strip() or None,
            "transition": "crossfade",
            "score": round(float(s.get("score") or 0.0), 2),
        })
        minute_counts[minute_key] = minute_counts.get(minute_key, 0) + 1
        budget += dur

    place_bit = f" in {parsed.place}" if parsed.place else ""
    summary = (
        f"Matched {stats.get('matched_assets') or len(clips)} clips{place_bit}. "
        f"Deterministic plan from {len(clips)} clip(s)."
    )
    return {
        "summary": summary,
        "clips": clips,
        "total_duration": round(budget, 2),
        "target_ratio": ratio,
        "source": "fallback",
        "parsed_intent": parsed.to_dict(),
        "match_stats": stats,
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


def _normalize_clips(clips: Any, db, allowed_ids: Optional[set[int]] = None) -> list[dict]:
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
        if allowed_ids is not None and asset_id not in allowed_ids:
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


def _ollama_plan(intent: str, db, trip_id: Optional[int] = None, selected_ids: Optional[list[int]] = None) -> Optional[dict]:
    """Ask Ollama for the plan; None on any failure (caller falls back)."""
    if _ollama_generate is None:
        log.info("ollama_llm unavailable (%s); using fallback planner",
                 _OLLAMA_IMPORT_ERROR)
        return None
    parsed = parse_intent(intent, trip_id=trip_id)
    target = parsed.duration_s
    ratio = parsed.ratio
    sources, stats = _candidate_sources(db, parsed=parsed, limit=30, trip_id=trip_id, selected_ids=selected_ids)
    if not sources:
        return None

    prompt = _ollama_prompt(intent, sources, target, ratio)
    for attempt in (1,):
        try:
            text = _ollama_generate(
                "qwen2.5vl:7b", prompt,
                format="json", temperature=0.2, retries=1, timeout=12.0,
            )
        except Exception as exc:  # noqa: BLE001 - ollama down -> fallback
            log.warning("ollama plan failed (attempt %d): %s", attempt, exc)
            continue
        data = _extract_json(text)
        if not isinstance(data, dict):
            continue
        clips = _normalize_clips(
            data.get("clips"), db,
            allowed_ids={s["asset_id"] for s in sources},
        )
        if not clips:
            continue
        return {
            "summary": str(data.get("summary", "")).strip() or
                       f"AI plan with {len(clips)} clips.",
            "clips": clips,
            "total_duration": round(sum(c["end"] - c["start"] for c in clips), 2),
            "target_ratio": str(data.get("target_ratio", ratio)),
            "source": "ollama",
            "parsed_intent": parsed.to_dict(),
            "match_stats": stats,
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
    extra: dict[str, Any] = {}
    if plan.parsed_json:
        try:
            extra = json.loads(plan.parsed_json)
        except json.JSONDecodeError:
            extra = {}
    return {
        "plan_id": plan.id,
        "status": plan.status,
        "summary": plan.summary,
        "clips": [_clip_dict(c) for c in plan.clips],
        "total_duration": round(float(plan.total_duration or 0.0), 2),
        "target_ratio": plan.target_ratio or DEFAULT_RATIO,
        "intent": plan.intent,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
        "parsed_intent": extra.get("parsed_intent"),
        "match_stats": extra.get("match_stats"),
    }


def create_plan(intent: str, trip_id: Optional[int] = None, selected_ids: Optional[list[int]] = None) -> dict:
    """POST /api/edits/plan: build + persist a draft plan."""
    with SessionLocal() as db:
        parsed = parse_intent(intent, trip_id=trip_id)
        plan_data = _ollama_plan(intent, db, trip_id=trip_id, selected_ids=selected_ids) or _deterministic_plan(
            intent, db, trip_id=trip_id, parsed=parsed, selected_ids=selected_ids
        )
        plan_id = _new_plan_id()
        blob = json.dumps({
            "parsed_intent": plan_data.get("parsed_intent") or parsed.to_dict(),
            "match_stats": plan_data.get("match_stats") or {},
        })
        plan = models.EditPlan(
            id=plan_id,
            intent=intent,
            status="draft",
            summary=plan_data.get("summary"),
            parsed_json=blob,
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
