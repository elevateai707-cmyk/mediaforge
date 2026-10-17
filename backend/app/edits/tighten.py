"""Create reversible silence/filler-removal edit plans for video assets."""
from __future__ import annotations

import json
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

from .. import config, models
from ..db import SessionLocal


class TightenError(Exception):
    """A user-presentable tightening failure."""


_SILENCE_START = re.compile(r"silence_start:\s*([0-9.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*([0-9.]+)")
_FILLERS = {"um", "uh", "erm"}


def detect_silences(path: str, silence_db: float, min_silence_s: float) -> list[tuple[float, float]]:
    """Run ffmpeg silencedetect and return completed source-time intervals."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-v", "info", "-i", path, "-af",
             f"silencedetect=noise={silence_db}dB:d={min_silence_s}", "-f", "null", "-"],
            capture_output=True, text=True, timeout=120, check=False,
        )
    except FileNotFoundError as exc:
        raise TightenError("ffmpeg is required to detect silence") from exc
    except subprocess.TimeoutExpired as exc:
        raise TightenError("silence detection timed out") from exc
    if result.returncode:
        message = (result.stderr or "").strip().splitlines()
        detail = message[-1] if message else "unknown ffmpeg error"
        lowered = (result.stderr or "").lower()
        if any(text in lowered for text in ("does not contain any stream", "matches no streams", "no audio")):
            raise TightenError("asset has no audio stream to tighten")
        raise TightenError(f"ffmpeg could not analyze asset audio: {detail[:180]}")
    # ffmpeg reports diagnostics on stderr even when the null output succeeds.
    starts: list[float] = []
    intervals: list[tuple[float, float]] = []
    for line in result.stderr.splitlines():
        start = _SILENCE_START.search(line)
        if start:
            starts.append(float(start.group(1)))
            continue
        end = _SILENCE_END.search(line)
        if end and starts:
            s = starts.pop(0)
            e = float(end.group(1))
            if e > s:
                intervals.append((s, e))
    return intervals


def _verified_words(asset: models.Asset) -> list[dict[str, Any]]:
    """Only use modern Whisper word timings matching the current asset hash."""
    path = config.DATA_DIR / "transcripts" / f"{asset.id}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if (not isinstance(payload, dict) or payload.get("asset_hash") != asset.hash or
            payload.get("timing_source") != "faster-whisper"):
        return []
    words: list[dict[str, Any]] = []
    for segment in payload.get("segments", []):
        if not isinstance(segment, dict):
            continue
        for word in segment.get("words", []):
            if not isinstance(word, dict):
                continue
            try:
                start, end = float(word["start"]), float(word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if end > start and isinstance(word.get("word"), str):
                words.append({"word": word["word"], "start": start, "end": end})
    return sorted(words, key=lambda item: (item["start"], item["end"]))


def filler_intervals(words: list[dict[str, Any]], padding_s: float) -> list[tuple[float, float]]:
    """Locate unambiguous fillers. ``like`` needs a pause on either side."""
    removals: list[tuple[float, float]] = []
    normalized = [re.sub(r"[^a-z]", "", str(w["word"]).lower()) for w in words]
    i = 0
    while i < len(words):
        token = normalized[i]
        end_index = i
        remove = token in _FILLERS
        if token == "you" and i + 1 < len(words) and normalized[i + 1] == "know":
            remove, end_index = True, i + 1
        elif token == "like":
            previous_gap = (words[i]["start"] - words[i - 1]["end"]) if i else 1.0
            next_gap = (words[i + 1]["start"] - words[i]["end"]) if i + 1 < len(words) else 1.0
            remove = previous_gap >= 0.15 and next_gap >= 0.15
        if remove:
            removals.append((max(0.0, words[i]["start"] - padding_s),
                             words[end_index]["end"] + padding_s))
        i = end_index + 1
    return removals


def _merge(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def _kept_intervals(start: float, end: float, removals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    cursor = start
    kept: list[tuple[float, float]] = []
    for remove_start, remove_end in _merge(removals):
        remove_start, remove_end = max(start, remove_start), min(end, remove_end)
        if remove_end <= cursor:
            continue
        if remove_start > cursor:
            kept.append((cursor, remove_start))
        cursor = max(cursor, remove_end)
    if cursor < end:
        kept.append((cursor, end))
    return [(s, e) for s, e in kept if e > s]


def _asset_clips(asset: models.Asset, source_start: float, source_end: float,
                 silence_db: float, min_silence_s: float, remove_fillers: bool,
                 padding_s: float, silences: list[tuple[float, float]] | None = None) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if asset.kind != "video":
        raise TightenError("tighten currently supports video assets with audio")
    if not Path(asset.path).is_file():
        raise TightenError(f"asset {asset.id} source file is unavailable")
    if silences is None:
        silences = detect_silences(asset.path, silence_db, min_silence_s)
    # Preserve a little silence on either side of each audible section.
    removals = [(start + padding_s, end - padding_s) for start, end in silences]
    words = _verified_words(asset) if remove_fillers else []
    fillers = filler_intervals(words, padding_s) if words else []
    kept = _kept_intervals(source_start, source_end, removals + fillers)
    if not kept:
        # Do not create an empty/dead plan if detection considers all audio silent.
        kept = [(source_start, source_end)]
    return ([{
        "asset_id": asset.id, "scene_id": None, "start": round(start, 3),
        "end": round(end, 3), "caption": None, "transition": "cut", "score": 0.0,
    } for start, end in kept], {"silences": len(silences), "fillers": len(fillers)})


def create_tighten_plan(*, asset_id: int | None = None, plan_id: str | None = None,
                        silence_db: float = -35, min_silence_s: float = .4,
                        remove_fillers: bool = True, padding_s: float = .08) -> dict[str, Any]:
    if (asset_id is None) == (plan_id is None):
        raise TightenError("provide exactly one of asset_id or plan_id")
    if not -100 <= silence_db <= 0 or min_silence_s <= 0 or padding_s < 0:
        raise TightenError("invalid silence or padding settings")
    with SessionLocal() as db:
        source_plan = db.get(models.EditPlan, plan_id) if plan_id else None
        if plan_id and source_plan is None:
            raise TightenError(f"plan {plan_id!r} not found")
        if source_plan is not None:
            try:
                source_extra = json.loads(source_plan.parsed_json or "{}")
            except json.JSONDecodeError:
                source_extra = {}
            if source_extra.get("story"):
                raise TightenError("cannot tighten a story plan: it would break voiceover synchronization")
        sources: list[tuple[models.Asset, float, float, int | None, str | None, float, str | None]] = []
        if source_plan is not None:
            for old in source_plan.clips:
                asset = db.get(models.Asset, old.asset_id)
                if asset is None:
                    raise TightenError(f"plan references missing asset {old.asset_id}")
                sources.append((asset, float(old.start), float(old.end), old.scene_id,
                                old.caption, float(old.score or 0.0), old.transition))
        else:
            asset = db.get(models.Asset, asset_id)
            if asset is None:
                raise TightenError(f"asset {asset_id!r} not found")
            sources.append((asset, 0.0, float(asset.duration or 0.0), None, None, 0.0, "cut"))

        clips: list[dict[str, Any]] = []
        stats = {"silences": 0, "fillers": 0}
        silence_cache: dict[int, list[tuple[float, float]]] = {}
        for asset, start, end, scene_id, caption, score, incoming_transition in sources:
            if end <= start:
                raise TightenError(f"asset {asset.id} has no usable duration")
            if not Path(asset.path).is_file():
                raise TightenError(f"asset {asset.id} source file is unavailable")
            if asset.id not in silence_cache:
                silence_cache[asset.id] = detect_silences(asset.path, silence_db, min_silence_s)
            new_clips, found = _asset_clips(asset, start, end, silence_db, min_silence_s,
                                            remove_fillers, padding_s, silence_cache[asset.id])
            for clip in new_clips:
                clip.update(scene_id=scene_id, caption=caption, score=score)
            if new_clips:
                new_clips[0]["transition"] = incoming_transition or "cut"
            clips.extend(new_clips)
            stats["silences"] += found["silences"]
            stats["fillers"] += found["fillers"]

        original_duration = sum(end - start for _asset, start, end, *_rest in sources)
        tightened_duration = sum(c["end"] - c["start"] for c in clips)
        extra = json.loads(source_plan.parsed_json or "{}") if source_plan and source_plan.parsed_json else {}
        if extra.get("story") and tightened_duration < original_duration - 0.01:
            raise TightenError("cannot tighten this story plan because removed time would break voiceover synchronization")
        new_id = uuid.uuid4().hex[:12]
        metadata = {"tighten": {"source_plan_id": plan_id, "source_asset_id": asset_id,
                    "silence_db": silence_db, "min_silence_s": min_silence_s,
                    "remove_fillers": remove_fillers, "padding_s": padding_s, **stats}}
        plan = models.EditPlan(
            id=new_id, intent=f"tighten {plan_id or f'asset {asset_id}'}", status="draft",
            summary=f"Tightened {len(sources)} source clip(s): {stats['silences']} silence interval(s), {stats['fillers']} filler interval(s).",
            parsed_json=json.dumps(metadata), target_ratio=(source_plan.target_ratio if source_plan else "9:16"),
            total_duration=round(tightened_duration, 3),
        )
        db.add(plan)
        for position, clip in enumerate(clips):
            db.add(models.EditClip(plan_id=new_id, position=position, **clip))
        db.commit()
        return {"plan_id": new_id, "status": "draft", "source_plan_id": plan_id,
                "source_asset_id": asset_id, "clips": clips,
                "total_duration": round(tightened_duration, 3), "analysis": metadata["tighten"]}
