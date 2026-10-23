"""Story voiceover pipeline: script -> beats -> ElevenLabs TTS -> B-roll plan.

Flow:
1. ``split_beats`` turns the script into beats (~15-40 words each).
2. Each beat is spoken in the configured ElevenLabs voice via the
   ``with-timestamps`` endpoint (audio + character alignment -> word timings).
3. Each beat gets matching drone/aerial video clips, trimmed so the clips for a
   beat add up to the beat's audio length plus a small gap.
4. A normal EditPlan + EditClips is created (status "draft"). Artifacts live in
   ``EXPORTS_DIR/story/<plan_id>/``: beat_NN.mp3, voiceover.mp3 (beats padded to
   their slot and concatenated, so the timeline matches the clips exactly),
   captions.srt and a story.json sidecar.

The API key is read from config at call time and is never logged or placed in
an error message.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
from sqlalchemy import or_

from .. import config, models
from . import kinetic
from ..db import SessionLocal
from ..proc import tool_env

log = logging.getLogger("mediaforge.story")

ELEVEN_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"
OUTPUT_FORMAT = "mp3_44100_192"
VOICE_SETTINGS = {
    "stability": 0.5,
    "similarity_boost": 0.85,
    "style": 0.15,
    "use_speaker_boost": True,
}
BEAT_GAP = 0.3            # silence appended to every beat (also extra clip length)
TAKEOFF_SKIP = 1.0        # skip drone takeoff wobble when no scene start exists
MIN_SEGMENT = 0.3         # shortest clip segment worth cutting
AERIAL_MARKERS = ("blue-drone-1o1", "dji_")
AERIAL_WORDS = ("aerial", "drone")

BEAT_MIN_WORDS = 15
BEAT_MAX_WORDS = 40


class StoryError(Exception):
    """Pipeline failure with a user-presentable message (never contains the key)."""


# ---------------------------------------------------------------------------
# Beat splitting
# ---------------------------------------------------------------------------

_SENT_RE = re.compile(r"(?<=[.!?])[\"')\]*]*\s+")


def _words(text: str) -> int:
    return len(text.replace("*", "").split())


def split_beats(script: str) -> list[str]:
    """Paragraphs first; long paragraphs become 1-2 sentence beats.

    Short sentences are grouped until a beat reaches BEAT_MIN_WORDS; a beat is
    closed early rather than exceed BEAT_MAX_WORDS. Tiny trailing remainders are
    merged into the previous beat when that stays under the cap.
    """
    beats: list[str] = []
    for para in re.split(r"\n\s*\n", (script or "").strip()):
        para = " ".join(para.split())
        if not para:
            continue
        if _words(para) <= BEAT_MAX_WORDS:
            beats.append(para)
            continue
        sentences = [s.strip() for s in _SENT_RE.split(para) if s.strip()]
        cur: list[str] = []
        para_beats: list[str] = []
        for s in sentences:
            if cur and (_words(" ".join(cur)) >= BEAT_MIN_WORDS
                        or _words(" ".join(cur + [s])) > BEAT_MAX_WORDS
                        or len(cur) >= 2):
                para_beats.append(" ".join(cur))
                cur = []
            cur.append(s)
        if cur:
            tail = " ".join(cur)
            if (para_beats and _words(tail) < BEAT_MIN_WORDS // 2
                    and _words(para_beats[-1]) + _words(tail) <= BEAT_MAX_WORDS):
                para_beats[-1] += " " + tail
            else:
                para_beats.append(tail)
        beats.extend(para_beats)
    return beats


# ---------------------------------------------------------------------------
# Alignment -> words -> SRT
# ---------------------------------------------------------------------------

def alignment_to_words(alignment: dict) -> list[dict]:
    """Character alignment -> [{"word", "start", "end"}] (seconds, beat-local)."""
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    n = min(len(chars), len(starts), len(ends))
    words: list[dict] = []
    cur: list[str] = []
    w_start = w_end = 0.0
    for i in range(n):
        ch = chars[i]
        if ch is None or str(ch).isspace():
            if cur:
                words.append({"word": "".join(cur), "start": w_start, "end": w_end})
                cur = []
            continue
        if not cur:
            w_start = float(starts[i])
        cur.append(str(ch))
        w_end = float(ends[i])
    if cur:
        words.append({"word": "".join(cur), "start": w_start, "end": w_end})
    return words


def chunk_words(words: list[dict], max_words: int = 5, max_chars: int = 32) -> list[list[dict]]:
    """Group words into caption lines: <=max_words / ~max_chars, break after .,!?"""
    lines: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        text = w["word"]
        if cur:
            cur_chars = len(" ".join(x["word"] for x in cur))
            if len(cur) >= max_words or cur_chars + 1 + len(text) > max_chars:
                lines.append(cur)
                cur = []
        cur.append(w)
        if text and text[-1] in ".,!?":
            lines.append(cur)
            cur = []
    if cur:
        lines.append(cur)
    return lines


def _srt_time(t: float) -> str:
    ms = max(0, int(round(t * 1000)))
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_srt(beats: list[dict]) -> str:
    """beats: [{"start": timeline offset, "words": [...]}] -> SRT text.

    Each line ends when the next one starts (or at its own last word) so that
    captions never overlap.
    """
    entries: list[tuple[float, float, str]] = []
    for beat in beats:
        off = float(beat["start"])
        for line in chunk_words(beat["words"]):
            entries.append((off + line[0]["start"], off + line[-1]["end"],
                            " ".join(w["word"] for w in line)))
    out: list[str] = []
    for i, (s, e, text) in enumerate(entries):
        if i + 1 < len(entries):
            e = min(e, entries[i + 1][0]) if entries[i + 1][0] > s else e
        e = max(e, s + 0.05)
        out.append(f"{i + 1}\n{_srt_time(s)} --> {_srt_time(e)}\n{text}\n")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# ElevenLabs client
# ---------------------------------------------------------------------------

def _tts_http(url: str, headers: dict, payload: dict) -> tuple[int, Any]:
    """POST to ElevenLabs; returns (status, parsed JSON or text). Mock point in tests."""
    try:
        r = httpx.post(url, headers=headers, json=payload,
                       timeout=httpx.Timeout(180.0, connect=15.0))
    except httpx.HTTPError as exc:
        raise StoryError(f"ElevenLabs request failed: {type(exc).__name__}") from None
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text


def _eleven_error(body: Any) -> str:
    if isinstance(body, dict):
        detail = body.get("detail", body)
        if isinstance(detail, dict):
            detail = detail.get("message") or detail.get("status") or detail
        return str(detail)[:300]
    return str(body)[:300]


def synthesize_beat(text: str, voice_id: str, previous_text: Optional[str] = None,
                    next_text: Optional[str] = None) -> tuple[bytes, dict]:
    """One beat -> (mp3 bytes, alignment dict)."""
    key = config.ELEVENLABS_API_KEY
    if not key:
        raise StoryError(
            "ELEVENLABS_API_KEY is not set; add it to the service environment "
            "(.env) and restart MediaForge.")
    payload: dict[str, Any] = {
        "text": text,
        "model_id": config.ELEVENLABS_MODEL,
        "voice_settings": VOICE_SETTINGS,
    }
    if previous_text:
        payload["previous_text"] = previous_text
    if next_text:
        payload["next_text"] = next_text
    url = ELEVEN_URL.format(voice_id=voice_id) + f"?output_format={OUTPUT_FORMAT}"
    status, body = _tts_http(url, {"xi-api-key": key, "Content-Type": "application/json"},
                             payload)
    if status != 200:
        raise StoryError(f"ElevenLabs HTTP {status}: {_eleven_error(body)}")
    if not isinstance(body, dict) or not body.get("audio_base64"):
        raise StoryError("ElevenLabs response had no audio_base64")
    alignment = body.get("alignment") or body.get("normalized_alignment") or {}
    try:
        audio = base64.b64decode(body["audio_base64"])
    except Exception:  # noqa: BLE001
        raise StoryError("ElevenLabs returned undecodable audio") from None
    return audio, alignment


# ---------------------------------------------------------------------------
# ffmpeg helpers
# ---------------------------------------------------------------------------

def probe_duration(path: str) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True, timeout=60, env=tool_env())
    try:
        return float(proc.stdout.strip())
    except ValueError:
        raise StoryError(f"could not read audio duration of {Path(path).name}") from None


def build_voiceover(beat_files: list[str], slots: list[float], out_path: str) -> None:
    """Pad each beat to its slot length and concatenate into one mp3."""
    cmd = ["ffmpeg", "-y", "-v", "error", "-nostdin"]
    for f in beat_files:
        cmd += ["-i", f]
    parts = [f"[{i}:a]aresample=44100,apad=whole_dur={slot:.3f},"
             f"atrim=end={slot:.3f}[p{i}]" for i, slot in enumerate(slots)]
    labels = "".join(f"[p{i}]" for i in range(len(slots)))
    filt = ";".join(parts) + f";{labels}concat=n={len(slots)}:v=0:a=1[out]"
    cmd += ["-filter_complex", filt, "-map", "[out]",
            "-c:a", "libmp3lame", "-b:a", "192k", out_path]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                          env=tool_env())
    if proc.returncode != 0:
        raise StoryError("voiceover concat failed: "
                         + (proc.stderr or "")[-400:])


# ---------------------------------------------------------------------------
# B-roll matching
# ---------------------------------------------------------------------------

def is_aerial(path: str, caption: Optional[str]) -> bool:
    p = (path or "").lower()
    if any(m in p for m in AERIAL_MARKERS):
        return True
    c = (caption or "").lower()
    return any(w in c for w in AERIAL_WORDS)


def allocate_clips(need: float, candidates: list[dict], used: set[int]) -> list[dict]:
    """Fill ``need`` seconds with consecutive clips from ranked candidates.

    candidates: [{"asset_id","scene_id","start" (or None),"duration","caption","score"}]
    in preference order. Unused assets come first; when the pool runs out
    assets are reused. A short asset contributes what it has and the next
    candidate supplies the rest. Mutates ``used``.
    """
    if not candidates:
        raise StoryError("no video footage matches this story (check broll_filter / trip_id)")
    fresh = [c for c in candidates if c["asset_id"] not in used]
    order = fresh + [c for c in candidates if c["asset_id"] in used]
    clips: list[dict] = []
    remaining = need
    guard = 0
    while remaining > 0.05 and guard < len(order) * 4 + 4:
        cand = order[guard % len(order)]
        guard += 1
        dur = float(cand["duration"])
        start = cand.get("start")
        limit = dur
        if cand.get("windows"):
            # Smooth-flying ranges only: the longest window, skipping takeoff wobble.
            w0, w1, _s = max(cand["windows"], key=lambda w: w[1] - w[0])
            skip = TAKEOFF_SKIP if w1 - max(w0, TAKEOFF_SKIP) >= min(remaining, 2.0) else w0
            start, limit = max(w0, skip), w1
        elif start is None:
            start = TAKEOFF_SKIP if dur > remaining + TAKEOFF_SKIP + 0.5 else 0.0
        start = min(float(start), max(0.0, limit - MIN_SEGMENT))
        take = min(remaining, limit - start)
        if take < MIN_SEGMENT and remaining > MIN_SEGMENT:
            continue
        take = round(take, 3)
        clips.append({
            "asset_id": cand["asset_id"], "scene_id": cand.get("scene_id"),
            "start": round(start, 3), "end": round(start + take, 3),
            "score": cand.get("score", 0.0),
        })
        used.add(cand["asset_id"])
        remaining -= take
    if not clips:
        raise StoryError("no usable video footage (all assets too short)")
    # Land the beat exactly on its slot: stretch the last segment into rounding slack.
    gap = need - sum(c["end"] - c["start"] for c in clips)
    if 0 < gap <= 0.06:
        clips[-1]["end"] = round(clips[-1]["end"] + gap, 3)
    return clips


def _default_search(db, q: str, limit: int) -> list[int]:
    from .. import main  # lazy: main imports this module's package
    return main._hybrid_search(db, q, limit=limit)


def filtered_video_assets(db, trip_ids: Optional[set[int]] = None,
                          broll_filter: Optional[dict] = None) -> dict[int, "models.Asset"]:
    return filtered_video_pool(db, trip_ids, broll_filter)[0]


def filtered_video_pool(db, trip_ids: Optional[set[int]] = None, broll_filter: Optional[dict] = None
                        ) -> tuple[dict[int, "models.Asset"], dict[int, list]]:
    """Usable video assets (known duration) restricted by trip and ``broll_filter``.

    broll_filter keys (all optional): path_prefixes, require_aerial,
    exclude_faces, min_aesthetic, asset_ids, exclude_asset_ids. A missing key
    means "no restriction"; assets with a null aesthetic score fail min_aesthetic.
    """
    flt = broll_filter or {}
    q = db.query(models.Asset).filter(models.Asset.kind == "video",
                                      models.Asset.duration.isnot(None),
                                      models.Asset.duration > 0)
    if trip_ids is not None:
        q = q.filter(models.Asset.id.in_(trip_ids or {-1}))
    prefixes = [p for p in (flt.get("path_prefixes") or []) if p]
    if prefixes:
        q = q.filter(or_(*[models.Asset.path.startswith(p, autoescape=True) for p in prefixes]))
    if flt.get("asset_ids"):
        q = q.filter(models.Asset.id.in_(list(flt["asset_ids"])))
    if flt.get("exclude_asset_ids"):
        q = q.filter(~models.Asset.id.in_(list(flt["exclude_asset_ids"])))
    if flt.get("min_aesthetic") is not None:
        q = q.filter(models.Asset.aesthetic_score >= float(flt["min_aesthetic"]))
    if flt.get("exclude_faces"):
        q = q.filter(~models.Asset.id.in_(db.query(models.FaceDetection.asset_id)))
    rows = {a.id: a for a in q.all()}
    if flt.get("require_aerial"):
        rows = {i: a for i, a in rows.items() if is_aerial(a.path, a.caption)}
    windows: dict[int, list] = {}
    hyper, parts = flt.get("hyperlapse"), flt.get("daypart")
    min_smooth = flt.get("min_smoothness")
    if rows and (hyper is not None or parts or min_smooth is not None):
        from ..ai import motion_store
        motion = {m.asset_id: m for m in db.query(models.MotionAnalysis)
                  .filter(models.MotionAnalysis.asset_id.in_(list(rows))).all()}
        keep: dict[int, "models.Asset"] = {}
        for aid, a in rows.items():
            m = motion.get(aid)
            # Unanalysed assets only pass filters that do not need analysis data.
            if m is None and (hyper is True or parts or min_smooth is not None):
                continue
            if m is not None:
                if hyper is True and not m.is_hyperlapse:
                    continue
                if hyper is False and m.is_hyperlapse:
                    continue
                if parts and m.daypart not in parts:
                    continue
                if min_smooth is not None and not m.is_hyperlapse:
                    w = motion_store.windows_for(m, float(min_smooth), float(a.duration))
                    if not w:
                        continue
                    windows[aid] = w
            keep[aid] = a
        rows = keep
    if rows:
        from . import exclusions
        excl = exclusions.load(db, list(rows))
        for aid, ex in excl.items():
            if aid not in rows:
                continue
            if exclusions.is_whole(ex):
                rows.pop(aid)
                windows.pop(aid, None)
                continue
            base = windows.get(aid) or [[0.0, float(rows[aid].duration), 0.0]]
            free = exclusions.subtract(base, ex)
            if not free:
                rows.pop(aid)
                windows.pop(aid, None)
            else:
                windows[aid] = free
    return rows, windows


def beat_candidates(db, text: str, search_fn: Callable, trip_ids: Optional[set[int]],
                    limit: int = 40, broll_filter: Optional[dict] = None) -> list[dict]:
    """Ranked video candidates for one beat (aerial boosted, trip/filter restricted)."""
    try:
        ids = list(search_fn(db, text, limit))
    except Exception as exc:  # noqa: BLE001 - search must never kill the pipeline
        log.warning("beat search failed: %s", exc)
        ids = []
    scored: dict[int, float] = {aid: 1.0 - i / max(1, limit) for i, aid in enumerate(ids)}

    rows, windows = filtered_video_pool(db, trip_ids, broll_filter)
    # Backfill from the library so a CLIP-less / sparse search still has a pool.
    for a in rows.values():
        scored.setdefault(a.id, 0.0)
    out: list[dict] = []
    for aid, base in scored.items():
        a = rows.get(aid)
        if a is None or not Path(a.path or "").is_file():
            continue
        score = base + (0.5 if is_aerial(a.path, a.caption) else 0.0) \
            + 0.01 * float(a.aesthetic_score or 0.0)
        scene = (db.query(models.Scene).filter(models.Scene.asset_id == aid)
                 .order_by(models.Scene.index).first())
        out.append({
            "asset_id": aid, "scene_id": scene.id if scene else None,
            "start": float(scene.start) if scene and scene.start is not None else None,
            "duration": float(a.duration), "caption": a.caption, "score": score,
            "path": a.path, "windows": windows.get(aid),
        })
    out.sort(key=lambda c: c["score"], reverse=True)
    return out


# ---------------------------------------------------------------------------
# Sidecar
# ---------------------------------------------------------------------------

def _ratio_size(ratio: str) -> tuple[int, int]:
    return {"16:9": (1920, 1080), "1:1": (1080, 1080)}.get(ratio, (1080, 1920))


def story_dir(plan_id: str) -> Path:
    from .plan_history import validate_id
    validate_id(plan_id)
    return config.EXPORTS_DIR / "story" / plan_id


def get_story(plan_id: str) -> Optional[dict]:
    f = story_dir(plan_id) / "story.json"
    if not f.is_file():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def story_summary(plan_id: str) -> Optional[dict]:
    s = get_story(plan_id)
    if not s:
        return None
    return {k: s.get(k) for k in ("voiceover", "srt", "ass", "beat_count", "voice_id",
                                  "duration", "caption_style")}


def export_companions(plan_id: str, export_path: str) -> dict[str, str]:
    """Copy voiceover.mp3 + captions.srt next to an export file (story plans only).

    Returns {"voiceover": path, "srt": path}; empty for non-story plans.
    """
    import shutil

    s = get_story(plan_id)
    if not s:
        return {}
    dest_dir = Path(export_path).parent
    prefixed = dest_dir == config.EXPORTS_DIR
    names = {"voiceover": f"{plan_id}_voiceover.mp3" if prefixed else "voiceover.mp3",
             "srt": f"{plan_id}.srt" if prefixed else "captions.srt"}
    out: dict[str, str] = {}
    for key in ("voiceover", "srt"):
        src = Path(s.get(key) or "")
        if not src.is_file():
            continue
        dst = dest_dir / names[key]
        try:
            shutil.copyfile(src, dst)
            out[key] = str(dst)
        except OSError as exc:
            log.warning("could not copy %s next to export: %s", key, exc)
    return out


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def create_story_plan(script: str, voice_id: Optional[str] = None,
                      trip_id: Optional[int] = None, ratio: str = "16:9",
                      title: Optional[str] = None, caption_style: str = "kinetic",
                      cloud_approved: bool = False, voice_consent: bool = False,
                      max_characters: int = 5000,
                      progress: Optional[Callable[[float, str], None]] = None,
                      search_fn: Optional[Callable] = None,
                      broll_filter: Optional[dict] = None,
                      beat_hints: Optional[list[str]] = None,
                      hook_text: Optional[str] = None,
                      brand: Optional[str] = None,
                      beat_visuals: Optional[list] = None) -> dict:
    """Generate narration only after per-job consent and a character cap.

    ``beat_visuals`` ([null | {"type": "card", "card": {kind, text, sub}}] per beat) replaces a
    beat's drone shot with a rendered motion-graphic card (see cards.py).

    ``broll_filter`` restricts B-roll sources, ``beat_hints`` replaces the beat
    text as the per-beat search query, ``hook_text`` adds a 0-1.4 s headline and
    ``brand`` an end card (see render.py). All are validated before any paid call.
    """
    from .. import policy
    from . import planner

    selected_voice = voice_id or config.ELEVENLABS_VOICE_ID
    approval = policy.story_approval(
        script, selected_voice, cloud_approved=cloud_approved,
        voice_consent=voice_consent, max_characters=max_characters)
    options = validate_story_options(script, broll_filter, beat_hints, hook_text, brand, beat_visuals)
    plan_id = planner._new_plan_id()
    policy.record_story_event(plan_id, "approved", approval)
    try:
        result = _create_story_plan(
            script, voice_id=selected_voice, trip_id=trip_id, ratio=ratio,
            title=title, caption_style=caption_style, progress=progress,
            search_fn=search_fn, plan_id=plan_id, approval=approval, options=options)
    except Exception:
        policy.record_story_event(plan_id, "failed", approval)
        raise
    policy.record_story_event(plan_id, "completed", approval)
    return result


def validate_story_options(script: str, broll_filter: Optional[dict],
                           beat_hints: Optional[list[str]], hook_text: Optional[str],
                           brand: Optional[str], beat_visuals: Optional[list] = None) -> dict:
    """Normalise optional story controls; raises StoryError (HTTP 422) on bad input."""
    from . import brands

    beats = [b for b in (kinetic.parse_emphasis(x)[0] for x in split_beats(script)) if b]
    hints = [str(h).strip() for h in (beat_hints or [])]
    if len(hints) > len(beats):
        raise StoryError(f"beat_hints has {len(hints)} entries but the script has only {len(beats)} beats")
    if any(len(h) > 300 for h in hints):
        raise StoryError("each beat hint must be at most 300 characters")
    hook = " ".join((hook_text or "").replace("*", " ").split()) or None
    if hook and len(hook) > 90:
        raise StoryError("hook_text must be at most 90 characters")
    try:
        brand_row = brands.get_brand(brand)
    except brands.BrandError as exc:
        raise StoryError(str(exc)) from None
    from . import cards
    try:
        visuals = cards.validate_beat_visuals(beat_visuals, len(beats)) if beat_visuals is not None else None
    except cards.CardError as exc:
        raise StoryError(str(exc)) from None
    return {"broll_filter": clean_broll_filter(broll_filter), "beat_hints": hints,
            "hook_text": hook, "brand": brand_row, "beat_visuals": visuals}


def clean_broll_filter(raw: Optional[dict]) -> Optional[dict]:
    """Validate a broll_filter mapping; returns None when it restricts nothing."""
    if not raw:
        return None
    if not isinstance(raw, dict):
        raise StoryError("broll_filter must be an object")
    known = {"path_prefixes", "require_aerial", "exclude_faces", "min_aesthetic",
             "asset_ids", "exclude_asset_ids", "min_smoothness", "hyperlapse", "daypart"}
    extra = set(raw) - known
    if extra:
        raise StoryError(f"unknown broll_filter key(s): {', '.join(sorted(extra))}")
    out: dict[str, Any] = {}
    prefixes = [str(p).strip() for p in (raw.get("path_prefixes") or []) if str(p).strip()]
    for p in prefixes:
        if not p.startswith("/") or ".." in Path(p).parts or len(p) > 1024:
            raise StoryError("broll_filter.path_prefixes must be absolute paths without '..'")
    if prefixes:
        out["path_prefixes"] = prefixes[:20]
    for key in ("require_aerial", "exclude_faces"):
        if raw.get(key):
            out[key] = True
    if raw.get("min_aesthetic") is not None:
        try:
            out["min_aesthetic"] = float(raw["min_aesthetic"])
        except (TypeError, ValueError):
            raise StoryError("broll_filter.min_aesthetic must be a number") from None
    if raw.get("min_smoothness") is not None:
        try:
            ms = float(raw["min_smoothness"])
        except (TypeError, ValueError):
            raise StoryError("broll_filter.min_smoothness must be a number") from None
        if not 0 <= ms <= 1:
            raise StoryError("broll_filter.min_smoothness must be between 0 and 1")
        out["min_smoothness"] = ms
    if raw.get("hyperlapse") is not None:
        out["hyperlapse"] = bool(raw["hyperlapse"])
    if raw.get("daypart"):
        parts = [raw["daypart"]] if isinstance(raw["daypart"], str) else list(raw["daypart"])
        bad = [p for p in parts if p not in ("day", "dusk", "night")]
        if bad:
            raise StoryError("broll_filter.daypart values must be day, dusk or night")
        out["daypart"] = sorted(set(parts))
    for key in ("asset_ids", "exclude_asset_ids"):
        if raw.get(key):
            try:
                out[key] = sorted({int(i) for i in raw[key]})[:5000]
            except (TypeError, ValueError):
                raise StoryError(f"broll_filter.{key} must be a list of integers") from None
    return out or None


def _create_story_plan(script: str, *, voice_id: str, trip_id: Optional[int],
                       ratio: str, title: Optional[str], caption_style: str,
                       progress: Optional[Callable], search_fn: Optional[Callable],
                       plan_id: str, approval: dict, options: Optional[dict] = None) -> dict:
    from . import planner
    from ..ingest.trips import trip_asset_ids

    report = progress or (lambda _f, _m: None)
    raw_beats = split_beats(script)
    if not raw_beats:
        raise StoryError("script is empty")
    if caption_style not in ("kinetic", "classic"):
        raise StoryError("caption_style must be 'kinetic' or 'classic'")
    # *emphasis* markup never reaches ElevenLabs; spans are kept as word indices.
    parsed = [kinetic.parse_emphasis(b) for b in raw_beats]
    beats = [clean for clean, _ in parsed if clean]
    parsed = [p for p in parsed if p[0]]
    voice_id = voice_id or config.ELEVENLABS_VOICE_ID
    if not config.ELEVENLABS_API_KEY:
        raise StoryError("ELEVENLABS_API_KEY is not set; add it to the service "
                         "environment (.env) and restart MediaForge.")
    search_fn = search_fn or _default_search

    out_dir = story_dir(plan_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. TTS per beat
    beat_rows: list[dict] = []
    t = 0.0
    n = len(beats)
    for i, text in enumerate(beats):
        report(0.05 + 0.6 * i / n, f"voicing beat {i + 1}/{n}")
        spans = parsed[i][1][:kinetic.MAX_EMPH_PER_BEAT]
        auto = not spans
        if auto:
            spans = kinetic.auto_emphasis(text.split())
        audio, alignment = synthesize_beat(
            text, voice_id,
            previous_text=beats[i - 1] if i > 0 else None,
            next_text=beats[i + 1] if i + 1 < n else None)
        f = out_dir / f"beat_{i + 1:02d}.mp3"
        f.write_bytes(audio)
        dur = probe_duration(str(f))
        slot = round(dur + BEAT_GAP, 3)
        beat_rows.append({
            "index": i + 1, "text": text, "audio": str(f),
            "audio_duration": round(dur, 3), "slot": slot, "start": round(t, 3),
            "words": alignment_to_words(alignment),
            "emphasis": [list(sp) for sp in spans],
            "emphasis_auto": auto,
        })
        t += slot
    total = round(t, 3)

    # 2. voiceover + captions
    report(0.68, "building voiceover")
    voiceover = out_dir / "voiceover.mp3"
    build_voiceover([b["audio"] for b in beat_rows], [b["slot"] for b in beat_rows],
                    str(voiceover))
    srt_path = out_dir / "captions.srt"
    srt_path.write_text(build_srt(beat_rows), encoding="utf-8")
    ass_path = out_dir / "captions.ass"
    ass_path.write_text(kinetic.build_ass(
        kinetic.plan_events(beat_rows), *_ratio_size(ratio)), encoding="utf-8")

    # 3. B-roll
    report(0.75, "matching footage")
    options = options or {}
    hints = options.get("beat_hints") or []
    clip_rows: list[dict] = []
    with SessionLocal() as db:
        trip_ids = set(trip_asset_ids(db, trip_id)) if trip_id else None
        used: set[int] = set()
        visuals = options.get("beat_visuals") or [None] * len(beat_rows)
        for b in beat_rows:
            hint = hints[b["index"] - 1] if b["index"] - 1 < len(hints) else None
            cands = beat_candidates(db, hint or b["text"], search_fn, trip_ids,
                                    broll_filter=options.get("broll_filter"))
            vis = visuals[b["index"] - 1] if b["index"] - 1 < len(visuals) else None
            b["visual"] = vis
            # a card beat still gets an (unseen) underlay clip so exports and sync checks keep
            # working, but it does not use up footage the other beats could show
            clips = allocate_clips(b["slot"], cands, set(used) if vis else used)
            by_id = {c["asset_id"]: c for c in cands}
            b["clips"] = []
            for c in clips:
                b["clips"].append({k: c[k] for k in ("asset_id", "scene_id", "start", "end")})
                c["caption"] = b["text"]
                c["path"] = by_id[c["asset_id"]]["path"]
                clip_rows.append(c)

        # 4. plan rows
        label = (title or "").strip() or " ".join(beats[0].split()[:6])
        plan = models.EditPlan(
            id=plan_id, intent=f"story: {label}", status="draft",
            summary=f"Story voiceover: {n} beats, {total:.1f}s, voice {voice_id}.",
            parsed_json=json.dumps({"story": True}),
            target_ratio=ratio, total_duration=total)
        db.add(plan)
        for pos, c in enumerate(clip_rows):
            db.add(models.EditClip(
                plan_id=plan_id, position=pos, asset_id=c["asset_id"],
                scene_id=c.get("scene_id"), start=c["start"], end=c["end"],
                caption=c["caption"], transition="cut",
                score=round(float(c.get("score") or 0.0), 2)))
        db.commit()

    sidecar = {
        "provenance": approval,
        "plan_id": plan_id, "title": title, "voice_id": voice_id,
        "model": config.ELEVENLABS_MODEL, "ratio": ratio, "trip_id": trip_id,
        "voiceover": str(voiceover), "srt": str(srt_path),
        "ass": str(ass_path), "caption_style": caption_style,
        "broll_filter": options.get("broll_filter"), "beat_hints": hints or None,
        "hook_text": options.get("hook_text"), "brand": options.get("brand"),
        "beat_count": n, "duration": total, "gap": BEAT_GAP,
        "beats": [{k: b[k] for k in ("index", "text", "audio", "audio_duration",
                                     "slot", "start", "words", "clips", "emphasis",
                                     "emphasis_auto", "visual")}
                  for b in beat_rows],
    }
    (out_dir / "story.json").write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    report(1.0, "story plan ready")
    result = planner.get_plan(plan_id) or {}
    result["story"] = story_summary(plan_id)
    return result
