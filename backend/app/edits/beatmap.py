"""Read-only access to precomputed music beat maps, plus cut-snapping math.

Beat maps are JSON files written by ``scripts/beatmap.py`` to
``<music dir>/.beats/<track filename>.json`` with keys: duration, bpm, beats[],
downbeats[], drop_s, best_start_15s, best_start_30s, energy_1s[]. MediaForge
only READS them (no audio analysis library is needed at runtime); a track
without a map simply renders without beat sync.
"""
from __future__ import annotations

import bisect
import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

log = logging.getLogger("mediaforge.beatmap")

SNAP_TOLERANCE_S = 0.15     # a cut moves at most this far to meet a music beat
MIN_CLIP_S = 0.4            # snapping never leaves a clip shorter than this
FLASH_MAX_DISTANCE_S = 0.75  # glitch flash goes to a downbeat at most this far from the cut


@dataclass
class BeatMap:
    path: str
    duration: float
    bpm: float
    beats: list[float]
    downbeats: list[float]
    drop_s: Optional[float] = None
    best_start_15s: Optional[float] = None
    best_start_30s: Optional[float] = None
    energy_1s: list[float] = field(default_factory=list)

    @property
    def bar_s(self) -> float:
        """Typical bar length from the downbeats (falls back to 4 beats of the bpm)."""
        if len(self.downbeats) >= 2:
            diffs = sorted(b - a for a, b in zip(self.downbeats, self.downbeats[1:]))
            return diffs[len(diffs) // 2]
        return 240.0 / self.bpm if self.bpm > 0 else 2.0


def beatmap_path(music_path: str) -> Path:
    p = Path(music_path)
    return p.parent / ".beats" / f"{p.name}.json"


def _floats(values) -> list[float]:
    out = [float(v) for v in values if isinstance(v, (int, float)) and math.isfinite(float(v))]
    return sorted(out)


def load(music_path: Optional[str]) -> Optional[BeatMap]:
    """Beat map for a track, or None when missing/unusable (never raises)."""
    if not music_path:
        return None
    f = beatmap_path(music_path)
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        duration = float(data["duration"])
        bm = BeatMap(
            path=str(f), duration=duration, bpm=float(data.get("bpm") or 0),
            beats=_floats(data.get("beats") or []),
            downbeats=_floats(data.get("downbeats") or []),
            drop_s=_opt(data.get("drop_s")),
            best_start_15s=_opt(data.get("best_start_15s")),
            best_start_30s=_opt(data.get("best_start_30s")),
            energy_1s=[float(x) for x in (data.get("energy_1s") or [])
                       if isinstance(x, (int, float)) and math.isfinite(float(x))])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if f.exists():
            log.warning("unusable beat map %s: %s", f, exc)
        return None
    if not math.isfinite(duration) or duration <= 0 or not bm.beats:
        return None
    if not bm.downbeats:
        bm.downbeats = bm.beats[::4]
    return bm


def _opt(v) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and math.isfinite(float(v)) else None


def nearest(values: Sequence[float], t: float) -> Optional[float]:
    """Value in the sorted sequence closest to t (None when empty)."""
    if not values:
        return None
    i = bisect.bisect_left(values, t)
    cands = values[max(0, i - 1):i + 1]
    return min(cands, key=lambda v: abs(v - t))


def default_start(bm: BeatMap, window: float) -> float:
    """Music start for a video of ``window`` seconds: the track's best section
    start moved onto the nearest downbeat, kept inside the track."""
    key = bm.best_start_30s if window > 20 else bm.best_start_15s
    key = key if key is not None else (bm.best_start_15s or bm.best_start_30s or 0.0)
    latest = max(0.0, bm.duration - window)
    start = nearest(bm.downbeats, min(key, latest)) if bm.downbeats else min(key, latest)
    if start is None:
        start = min(key, latest)
    if start > latest:
        earlier = [d for d in bm.downbeats if d <= latest]
        start = earlier[-1] if earlier else 0.0
    return round(float(start), 3)


def rel(times: Sequence[float], start: float, length: float, pad: float = 0.0) -> list[float]:
    """Times inside [start, start+length] expressed relative to ``start``."""
    return [round(t - start, 4) for t in times if -pad <= t - start <= length + pad]


def snap_plan(durations: Sequence[float], start_room: Sequence[float],
              end_room: Sequence[float], beats_rel: Sequence[float],
              tol: float = SNAP_TOLERANCE_S, min_dur: float = MIN_CLIP_S) -> dict:
    """Move internal cuts onto music beats without changing total length.

    durations[k]: planned length of clip k. start_room[k]: how much earlier clip
    k may start in its source (its in-point); end_room[k]: how much later it
    may end. Returns {"deltas": per-boundary shifts (len n-1), "cuts": original,
    "snapped": new cut times, "offsets": [(start_off, end_off)] per clip}.
    The last clip's end and first clip's start never move, so the timeline length
    (and anything locked to it, like a voiceover) is unchanged.
    """
    n = len(durations)
    cuts, acc = [], 0.0
    for d in durations[:-1]:
        acc += d
        cuts.append(acc)
    beats = sorted(beats_rel)
    new = list(cuts)
    for k, cut in enumerate(cuts):
        b = nearest(beats, cut)
        if b is None or abs(b - cut) > tol:
            continue
        delta = b - cut
        lower = (new[k - 1] if k else 0.0) + min_dur
        upper = (cuts[k + 1] if k + 1 < len(cuts) else acc + durations[-1]) - min_dur
        if not lower <= b <= upper:
            continue
        # clip k ends later (delta>0) / clip k+1 starts earlier (delta<0): needs source room
        if delta > 0 and end_room[k] < delta - 1e-6:
            continue
        if delta < 0 and start_room[k + 1] < -delta - 1e-6:
            continue
        new[k] = b
    deltas = [round(a - c, 6) for a, c in zip(new, cuts)]
    offsets = []
    for k in range(n):
        s_off = deltas[k - 1] if k else 0.0
        e_off = deltas[k] if k < n - 1 else 0.0
        offsets.append((s_off, e_off))
    return {"deltas": deltas, "cuts": cuts, "snapped": new, "offsets": offsets}


def flash_time(cut_t: float, downbeats_rel: Sequence[float]) -> float:
    """Where the glitch flash for a beat boundary lands: the nearest downbeat
    when it is close enough to read as part of the cut, else the cut itself."""
    d = nearest(sorted(downbeats_rel), cut_t)
    if d is not None and abs(d - cut_t) <= FLASH_MAX_DISTANCE_S:
        return d
    return cut_t
