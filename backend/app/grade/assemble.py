"""Script -> clip assembly: match clips to script beats, group takes, order.

Evidence per clip (all local):
  speech   whisper transcript (library rows, or live ``transcribe_profile`` when asked)
  caption  library caption + overlapping scene captions (vision model output already in the library)
  meta     library city/place + file name
  cues     measured luma (night/day) when the caller provides it
Matching is deterministic and explainable (IDF-weighted concept overlap + fuzzy
dialogue match); every assignment carries a confidence and the matched terms.
No cloud calls.
"""
from __future__ import annotations
import json
import math
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from .sampling import VIDEO_EXT

UNASSIGNED = 0
STOP = set("""a an and are as at be by for from has have in into is it its of on or that the their this to was were with
we you your our i my me he she they them his her up out over under then than so but not no just very more most some any
all can will would should could do does did done get got going go one two three shot clip footage video scene take
showing shows show view looks look seen see""".split())

# canonical concepts: any member token maps to the first word of its group
CONCEPTS = [
    "night nighttime evening dark neon lit lights illuminated dusk nightlife",
    "sunny daylight midday noon sunlit daytime",
    "beach shore shoreline sand sandy coast waterfront seaside bay ocean sea water turquoise sailboat",
    "mountain mountains skyline",
    "parade march marching dancer dancers dancing dance flag flags costume costumes festival procession cape capes",
    "legislature legislative building dome capitol government parliament",
    "ride rides carnival midway ferris coaster rodeo stampede fair",
    "stage concert performance band",
    "selfie vlog talking",
]
_CONCEPT_OF = {}
for _g in CONCEPTS:
    _w = _g.split()
    for _t in _w:
        _CONCEPT_OF[_t] = _w[0]


def stem(t: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if len(t) > len(suf) + 3 and t.endswith(suf):
            return t[: -len(suf)]
    return t


def tokens(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z][a-z']+", (text or "").lower()):
        w = w.replace("'s", "").strip("'")
        if w in STOP or len(w) < 3:
            continue
        w = _CONCEPT_OF.get(w, _CONCEPT_OF.get(stem(w), stem(w)))
        out.append(w)
    return out


# ---- data ------------------------------------------------------------------
@dataclass
class Beat:
    index: int
    title: str
    text: str


@dataclass
class ClipRef:
    path: str
    start: Optional[float] = None
    end: Optional[float] = None
    note: str = ""


@dataclass
class ClipInfo:
    path: str
    start: float
    end: float
    duration: float = 0.0
    name: str = ""
    city: str = ""
    place: str = ""
    caption: str = ""
    speech: str = ""
    taken_at: str = ""
    source: str = "probe"
    luma: Optional[float] = None
    fps: float = 30.0
    width: int = 0
    height: int = 0
    hdr: bool = False
    note: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def key(self):
        return f"{self.path}|{self.start:.3f}|{self.end:.3f}"


# ---- script parsing --------------------------------------------------------
_BULLET = re.compile(r"^\s*(?:beat\s*\d+\s*[:.)-]|\d+\s*[:.)-]|[-*•])\s*", re.I)


def parse_script(text: str) -> list[Beat]:
    """Numbered/bulleted lines, else blank-line paragraphs, else one beat per line."""
    text = (text or "").strip()
    if not text:
        return []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if sum(1 for ln in lines if _BULLET.match(ln)) >= max(2, len(lines) // 2):
        raw = [_BULLET.sub("", ln).strip() for ln in lines if _BULLET.match(ln)]
    elif re.search(r"\n\s*\n", text):
        raw = [" ".join(p.split()) for p in re.split(r"\n\s*\n", text) if p.strip()]
    else:
        raw = [ln.strip() for ln in lines]
    beats = []
    for i, r in enumerate(raw, 1):
        title = re.split(r"\s+[-–—:]\s+|[:–—]", r, maxsplit=1)[0].strip() or r
        title = " ".join(title.split()[:8])
        beats.append(Beat(i, title, r))
    return beats


def collect_clips(spec) -> list[ClipRef]:
    """A folder of clips, a manifest .json ([{path,start,end}] or {'clips':[...]}), or a list."""
    if isinstance(spec, (list, tuple)):
        out = []
        for s in spec:
            out.append(ClipRef(**s) if isinstance(s, dict) else ClipRef(str(s)))
        return out
    p = Path(spec)
    if p.is_dir():
        return [ClipRef(str(f)) for f in sorted(p.rglob("*")) if f.is_file() and f.suffix.lower() in VIDEO_EXT]
    if p.suffix.lower() == ".json":
        data = json.loads(p.read_text())
        data = data.get("clips", data) if isinstance(data, dict) else data
        return collect_clips(list(data))
    if p.is_file():
        return [ClipRef(str(p))]
    raise ValueError(f"clips input not found: {spec}")


# ---- clip description ------------------------------------------------------
def describe(ref: ClipRef, probe_info: dict, use_library: bool = True, transcribe: bool = False,
             profile: str = "fast") -> ClipInfo:
    start = float(ref.start or 0.0)
    end = float(ref.end if ref.end is not None else probe_info["duration"])
    info = ClipInfo(path=ref.path, start=start, end=end, duration=probe_info["duration"], name=Path(ref.path).name,
                    taken_at=probe_info.get("taken_at") or "", fps=probe_info["fps"], width=probe_info["width"],
                    height=probe_info["height"], hdr=probe_info["hdr"], note=ref.note)
    if use_library:
        try:
            _fill_from_library(info)
        except Exception:                                  # library optional; never fatal
            pass
    if transcribe and not info.speech and probe_info.get("has_audio"):
        try:
            from ..ai import whisper
            res = whisper.transcribe_profile(ref.path, profile)
            segs = [s for s in res.get("segments", []) if s["end"] >= start and s["start"] <= end]
            info.speech = " ".join(s["text"].strip() for s in segs)
            info.source += "+whisper"
        except Exception:
            pass
    if not info.taken_at:
        try:
            info.taken_at = __import__("datetime").datetime.fromtimestamp(Path(ref.path).stat().st_mtime).isoformat()
        except OSError:
            pass
    return info


def _fill_from_library(info: ClipInfo) -> None:
    from sqlalchemy import select
    from .. import models
    from ..db import SessionLocal
    with SessionLocal() as db:
        a = db.execute(select(models.Asset).where(models.Asset.path == info.path)).scalar_one_or_none()
        if a is None:
            return
        info.source = "library"
        info.city = a.city or ""
        info.place = " ".join(x for x in (a.place_name, a.region, a.country) if x)
        caps = [a.caption or ""]
        for s in a.scenes:
            if s.caption and s.end >= info.start and s.start <= info.end:
                caps.append(s.caption)
        info.caption = " . ".join(dict.fromkeys(c for c in caps if c))
        segs = [t.text for t in a.transcript if t.text and t.end >= info.start and t.start <= info.end]
        info.speech = " ".join(segs)
        if a.taken_at:
            info.taken_at = a.taken_at.isoformat()


# ---- matching --------------------------------------------------------------
def _idf(beat_tokens: list[set]) -> dict:
    b = len(beat_tokens)
    df: dict = {}
    for ts in beat_tokens:
        for t in ts:
            df[t] = df.get(t, 0) + 1
    return {t: math.log((b + 1) / (n + 0.5)) + 0.25 for t, n in df.items()}


def _cue(beat_tokens: set, luma: Optional[float]) -> float:
    if luma is None:
        return 0.0
    c = 0.0
    if "night" in beat_tokens:
        c += 0.5 if luma < 0.38 else (-0.5 if luma > 0.52 else 0.0)
    if "sunny" in beat_tokens:
        c += 0.2 if luma > 0.40 else (-0.2 if luma < 0.22 else 0.0)
    return c


def score_clip(beats: list[Beat], clip: ClipInfo, beat_tokens: list[set], idf: dict) -> list[dict]:
    meta = set(tokens(f"{clip.city} {clip.place} {Path(clip.path).stem.replace('_', ' ')} {clip.note}"))
    cap = set(tokens(clip.caption))
    speech_txt = clip.speech
    speech = set(tokens(speech_txt))
    city_toks = set(tokens(clip.city))
    out = []
    for b, bt in zip(beats, beat_tokens):
        if not bt:
            out.append({"beat": b.index, "score": 0.0, "terms": []})
            continue
        total = sum(idf[t] for t in bt)
        got, terms = 0.0, []
        for t in bt:
            w = 0.0
            if t in city_toks:
                w = max(w, 2.2)                                   # place name: strongest evidence
            if t in meta:
                w = max(w, 1.4)
            if t in cap:
                w = max(w, 1.0)
            if t in speech:
                w = max(w, 1.3)
            if w:
                got += idf[t] * w
                terms.append(t)
        s = got / math.sqrt(total)
        if len(speech) >= 4:                                       # scripted dialogue repeated on camera
            shared = speech & bt
            frac = len(shared) / max(1, min(len(speech), len(bt)))
            if len(shared) >= 3 and frac >= 0.4:
                s += 1.5 * min(1.0, frac)
                terms.append("<dialogue>")
        s += _cue(bt, clip.luma) if terms else 0.0
        out.append({"beat": b.index, "score": round(max(s, 0.0), 4), "terms": sorted(terms)})
    return out


def confidence(scores: list[dict]) -> tuple[Optional[dict], float]:
    ranked = sorted(scores, key=lambda x: -x["score"])
    best = ranked[0]
    second = ranked[1]["score"] if len(ranked) > 1 else 0.0
    if best["score"] <= 0:
        return None, 0.0
    margin = 1.0 - second / best["score"]
    absolute = best["score"] / (best["score"] + 0.6)
    return best, round(absolute * (0.3 + 0.7 * margin), 3)


def _sort_key(c: ClipInfo):
    return (c.taken_at or "9999", c.path, c.start)


def assemble(clips: list[ClipInfo], beats: list[Beat] | None = None, min_confidence: float = 0.25) -> dict:
    """Ordered assembly plan. With beats: script order, takes grouped. Without: chronological."""
    items: list[dict] = []
    if beats:
        bts = [set(tokens(b.text + " " + b.title)) for b in beats]
        idf = _idf(bts)
        assigned: dict[int, list] = {b.index: [] for b in beats}
        loose = []
        for c in clips:
            scores = score_clip(beats, c, bts, idf)
            best, conf = confidence(scores)
            alts = sorted(scores, key=lambda x: -x["score"])[:3]
            if best is None or conf < min_confidence:
                loose.append((c, conf, alts))
            else:
                assigned[best["beat"]].append((c, conf, best["terms"], alts))
        order = 0
        for b in beats:
            group = sorted(assigned[b.index], key=lambda t: _sort_key(t[0]))
            for take, (c, conf, terms, alts) in enumerate(group, 1):
                order += 1
                items.append(_item(order, c, b.index, b.title, take, conf, terms, alts))
        for take, (c, conf, alts) in enumerate(sorted(loose, key=lambda t: _sort_key(t[0])), 1):
            order += 1
            items.append(_item(order, c, UNASSIGNED, "Unassigned", take, conf, [], alts, label_prefix="Unassigned"))
        mode = "script"
    else:
        ordered = sorted(clips, key=_sort_key)
        group_no, last_toks, last_city = 0, None, None
        take = 0
        for order, c in enumerate(ordered, 1):
            toks = set(tokens(c.caption + " " + c.speech))
            same = last_toks is not None and c.city == last_city and toks and last_toks and \
                len(toks & last_toks) / max(1, len(toks | last_toks)) >= 0.5
            if same:
                take += 1
            else:
                group_no, take = group_no + 1, 1
            last_toks, last_city = toks, c.city
            items.append(_item(order, c, group_no, f"Shot {group_no}", take, 1.0, [], [], label_prefix="Shot"))
        mode = "chronological"
    return {
        "mode": mode,
        "beats": [asdict(b) for b in beats] if beats else [],
        "items": items,
        "summary": _summary(items, beats),
    }


def _item(order, c: ClipInfo, beat, title, take, conf, terms, alts, label_prefix="Beat") -> dict:
    label = f"{label_prefix} {beat:02d} · take {take}" if label_prefix != "Unassigned" else f"Unassigned · take {take}"
    return {
        "order": order, "beat": beat, "beat_title": title, "take": take, "label": label,
        "confidence": conf, "matched_terms": terms, "alternatives": alts,
        "path": c.path, "name": c.name, "start": round(c.start, 3), "end": round(c.end, 3),
        "city": c.city, "caption": c.caption[:240], "speech": c.speech[:200], "taken_at": c.taken_at,
        "fps": c.fps, "width": c.width, "height": c.height, "hdr": c.hdr, "source": c.source,
        "luma": c.luma, "note": c.note,
    }


def _summary(items, beats):
    per = {}
    for it in items:
        per.setdefault(it["beat"], []).append(it["confidence"])
    return {
        "clips": len(items),
        "unassigned": sum(1 for it in items if it["beat"] == UNASSIGNED and beats),
        "mean_confidence": round(sum(i["confidence"] for i in items) / max(1, len(items)), 3),
        "per_beat": {str(k): {"clips": len(v), "min_conf": min(v), "mean_conf": round(sum(v) / len(v), 3)} for k, v in per.items()},
    }
