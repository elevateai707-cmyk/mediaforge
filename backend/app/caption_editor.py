"""Caption word editing with durable sidecars and regenerated exports."""
from __future__ import annotations

import json
import math
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import config, models
from .db import SessionLocal
from .subtitles import build_document, serialize


class CaptionEditError(ValueError):
    pass


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=".caption-", delete=False) as stream:
            handle = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        handle.replace(path)
    finally:
        if handle is not None:
            handle.unlink(missing_ok=True)


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=".caption-", delete=False) as stream:
            temp = Path(stream.name)
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _as_words(raw: Any, *, duration: float | None = None) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise CaptionEditError("words must be a non-empty list")
    result: list[dict[str, Any]] = []
    previous_end = 0.0
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise CaptionEditError(f"word {index} must be an object")
        text = item.get("text", item.get("word", ""))
        text = str(text).strip() if text is not None else ""
        if not text:
            raise CaptionEditError(f"word {index} is empty")
        try:
            start, end = float(item.get("start")), float(item.get("end"))
        except (TypeError, ValueError):
            raise CaptionEditError(f"word {index} has invalid timing") from None
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise CaptionEditError(f"word {index} has invalid timing")
        if duration is not None and end > duration + 1e-6:
            raise CaptionEditError(f"word {index} exceeds media duration")
        if start < previous_end - 1e-6:
            raise CaptionEditError(f"word {index} overlaps the previous word")
        result.append({"word": text, "start": start, "end": end})
        previous_end = end
    return result


def _qc(words: list[dict[str, Any]]) -> dict[str, Any]:
    if not words:
        return {"cue_count": 0, "issue_count": 1,
                "issues": [{"cue_index": 0, "code": "empty_words", "message": "No caption words are available."}]}
    return build_document([{"start": words[0]["start"], "end": words[-1]["end"],
                            "text": " ".join(w["word"] for w in words), "words": words}])["qc"]


def advisory_qc(raw_words: list[dict[str, Any]]) -> dict[str, Any]:
    """QC for a GET response; malformed legacy timings remain visible to edit."""
    issues: list[dict[str, Any]] = []
    previous_end: float | None = None
    for index, word in enumerate(raw_words):
        text = str(word.get("text", "")).strip()
        if not text:
            issues.append({"cue_index": index, "code": "empty_word", "message": "Caption word is empty."})
        try:
            start, end = float(word.get("start")), float(word.get("end"))
        except (TypeError, ValueError):
            issues.append({"cue_index": index, "code": "invalid_timing", "message": "Caption timing is invalid."})
            continue
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            issues.append({"cue_index": index, "code": "invalid_timing", "message": "Caption timing is invalid."})
        elif previous_end is not None and start < previous_end:
            issues.append({"cue_index": index, "code": "overlap", "message": "Caption word overlaps the previous word."})
        previous_end = end if math.isfinite(end) else previous_end
    return {"cue_count": len(raw_words), "issue_count": len(issues), "issues": issues}


def asset_words(asset: models.Asset) -> list[dict[str, Any]]:
    path = config.DATA_DIR / "transcripts" / f"{asset.id}.json"
    sidecar = _read_json(path)
    if sidecar and sidecar.get("asset_hash") == asset.hash:
        words = [w for seg in sidecar.get("segments", []) if isinstance(seg, dict)
                 for w in seg.get("words", []) if isinstance(w, dict)]
        if words:
            return [{"text": str(w.get("word", w.get("text", ""))), "start": w.get("start"), "end": w.get("end")}
                    for w in words]
    # Historical transcripts do not have word alignment. Expose each segment as
    # an editable word-sized item rather than inventing timing.
    return [{"text": seg.text or "", "start": seg.start, "end": seg.end}
            for seg in sorted(asset.transcript, key=lambda s: s.start)]


def save_asset_words(db: Any, asset: models.Asset, raw_words: Any) -> dict[str, Any]:
    words = _as_words(raw_words, duration=asset.duration)
    existing = _read_json(config.DATA_DIR / "transcripts" / f"{asset.id}.json") or {}
    # One segment is deliberate: edited words are the source of truth, and it
    # avoids retaining stale source text or invalid historical segment splits.
    segment = {"start": words[0]["start"], "end": words[-1]["end"],
               "text": " ".join(w["word"] for w in words), "words": words}
    payload = dict(existing)
    payload.update({"schema_version": 1, "asset_id": asset.id, "asset_hash": asset.hash,
                    "timing_source": "edited", "segments": [segment]})
    sidecar_path = config.DATA_DIR / "transcripts" / f"{asset.id}.json"
    try:
        prior_sidecar = sidecar_path.read_bytes()
    except FileNotFoundError:
        prior_sidecar = None
    _atomic_json(sidecar_path, payload)
    # Keep the normalized relational transcript in lockstep with the sidecar.
    for row in list(asset.transcript):
        db.delete(row)
    db.add(models.TranscriptSegment(asset_id=asset.id, start=segment["start"],
                                    end=segment["end"], text=segment["text"]))
    asset.has_transcript = 1
    try:
        # Keep full-text search aligned with the edited transcript.
        from .ai.pipeline import _rebuild_fts
        db.flush()
        _rebuild_fts(db, asset)
        db.commit()
    except Exception:
        db.rollback()
        if prior_sidecar is None:
            sidecar_path.unlink(missing_ok=True)
        else:
            _atomic_text(sidecar_path, prior_sidecar.decode("utf-8"))
        raise
    document = build_document([segment])
    exports = config.DATA_DIR / "transcripts"
    for fmt in ("srt", "vtt", "ass"):
        _atomic_text(exports / f"{asset.id}.{fmt}", serialize(document, fmt))
    return {"words": [{"text": w["word"], "start": w["start"], "end": w["end"]} for w in words],
            "qc": document["qc"]}


def story_words(plan_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from .edits import story
    from .edits.plan_history import validate_id
    try:
        validate_id(plan_id)
    except ValueError as exc:
        raise CaptionEditError("story plan not found") from exc
    with SessionLocal() as db:
        plan = db.get(models.EditPlan, plan_id)
        if plan is None:
            raise CaptionEditError("story plan not found")
    sidecar = story.get_story(plan_id)
    if not sidecar:
        raise CaptionEditError("story plan not found")
    words: list[dict[str, Any]] = []
    for beat in sidecar.get("beats", []):
        offset = float(beat.get("start", 0))
        for word in beat.get("words", []):
            words.append({"text": word.get("word", ""), "start": offset + float(word.get("start", 0)),
                          "end": offset + float(word.get("end", 0)), "beat_index": beat.get("index")})
    return sidecar, words


def save_story_words(plan_id: str, raw_words: Any,
                     record_revision: Callable[[str, str], Any] | None = None) -> dict[str, Any]:
    from .edits import kinetic, planner, story
    from .edits.plan_history import plan_lock
    with plan_lock(plan_id):
        sidecar, old = story_words(plan_id)
        words = _as_words(raw_words, duration=float(sidecar.get("duration", 0)) or None)
        if len(words) != len(old):
            raise CaptionEditError("story caption edits must retain the existing word count")
        beats = sidecar.get("beats", [])
        cursor = 0
        assignments: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
        for beat in beats:
            beat_words = beat.get("words", [])
            count = len(beat_words)
            chosen = words[cursor:cursor + count]
            cursor += count
            offset = float(beat.get("start", 0))
            maximum = offset + float(beat.get("audio_duration", beat.get("slot", 0)))
            if chosen and (chosen[0]["start"] < offset - 1e-6 or chosen[-1]["end"] > maximum + 1e-6):
                raise CaptionEditError(f"words must remain within story beat {beat.get('index')}")
            assignments.append((beat, chosen))
        # A revision implementation may snapshot the on-disk sidecar, so invoke it
        # only after validation but before modifying any beat data or artifacts.
        if record_revision is not None:
            record_revision(plan_id, "before caption words edited")
        # Revocation is durable before writing any mutable story artifact. A
        # failed export must still require the user to review/reapprove.
        planner.mark_draft(plan_id)
        out = story.story_dir(plan_id)
        artifacts = ("story.json", "captions.srt", "captions.vtt", "captions.ass")
        prior = {name: (out / name).read_bytes() if (out / name).is_file() else None for name in artifacts}
        for beat, chosen in assignments:
            offset = float(beat.get("start", 0))
            beat_words = beat.get("words", [])
            # Preserve every word's non-text metadata and all beat durations/audio.
            for current, replacement in zip(beat_words, chosen):
                current["word"] = replacement["word"]
                current["start"] = round(replacement["start"] - offset, 6)
                current["end"] = round(replacement["end"] - offset, 6)
            beat["text"] = " ".join(str(w["word"]) for w in beat_words)
        try:
            _atomic_text(out / "captions.srt", story.build_srt(beats))
            document_segments = []
            for beat in beats:
                offset = float(beat["start"])
                local_words = beat.get("words", [])
                document_segments.append({
                    "start": offset,
                    "end": offset + float(beat.get("audio_duration", beat.get("slot", 0))),
                    "text": beat.get("text", ""),
                    "words": [{"word": word["word"], "start": offset + float(word["start"]),
                               "end": offset + float(word["end"])} for word in local_words],
                })
            _atomic_text(out / "captions.vtt", serialize(build_document(document_segments), "vtt"))
            sidecar["vtt"] = str(out / "captions.vtt")
            ratio = sidecar.get("ratio", "9:16")
            _atomic_text(out / "captions.ass", kinetic.build_ass(kinetic.plan_events(beats), *story._ratio_size(ratio)))
            _atomic_json(out / "story.json", sidecar)
        except Exception:
            for name, contents in prior.items():
                path = out / name
                if contents is None:
                    path.unlink(missing_ok=True)
                else:
                    _atomic_text(path, contents.decode("utf-8"))
            raise
        if record_revision is not None:
            record_revision(plan_id, "caption words edited")
        response_words = [{"text": w["word"], "start": w["start"], "end": w["end"]} for w in words]
        return {"words": response_words, "qc": _qc([{ "word": w["text"], "start": w["start"], "end": w["end"]} for w in response_words])}
