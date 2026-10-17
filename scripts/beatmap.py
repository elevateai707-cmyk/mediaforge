#!/usr/bin/env python3
"""Generate beat maps for music tracks (offline helper; MediaForge never runs it).

    python scripts/beatmap.py "/srv/iris/media/music/suno" [more files/dirs] [--force]

For every audio file this writes ``<dir>/.beats/<filename>.json`` with:
  file, duration, bpm, beats[], downbeats[], drop_s, best_start_15s,
  best_start_30s, energy_1s[]
which the MediaForge renderer reads for beat-synced cuts (backend/app/edits/beatmap.py).

librosa is an OPTIONAL dependency used only here, imported lazily, so install it
in a separate venv (the MediaForge runtime venv does not need it):

    python3 -m venv ~/.venvs/beatmap && ~/.venvs/beatmap/bin/pip install librosa soundfile
    ~/.venvs/beatmap/bin/python scripts/beatmap.py /srv/iris/media/music/suno

The numeric helpers below are pure Python so they can be tested without librosa.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional, Sequence

AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".aac", ".opus"}


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------

def _nearest(values: Sequence[float], t: float) -> Optional[float]:
    return min(values, key=lambda v: abs(v - t)) if values else None


def downbeats_from_beats(beats: Sequence[float], strengths: Sequence[float]) -> list[float]:
    """Pick every 4th beat, choosing the phase whose beats are strongest on average."""
    if not beats:
        return []
    best_phase, best_score = 0, float("-inf")
    for phase in range(min(4, len(beats))):
        picked = [strengths[i] for i in range(phase, len(beats), 4) if i < len(strengths)]
        score = sum(picked) / len(picked) if picked else float("-inf")
        if score > best_score:
            best_phase, best_score = phase, score
    return [round(float(b), 3) for b in beats[best_phase::4]]


def find_drop(energy: Sequence[float], window: int = 4) -> Optional[float]:
    """Second where mean energy over the next ``window`` s jumps most above the previous ``window`` s."""
    best_t, best_jump = None, 0.0
    for t in range(window, len(energy) - window + 1):
        before = sum(energy[t - window:t]) / window
        after = sum(energy[t:t + window]) / window
        if after - before > best_jump:
            best_t, best_jump = t, after - before
    return float(best_t) if best_t is not None else None


def best_window_start(energy: Sequence[float], length_s: int) -> float:
    """Start second of the highest-average-energy window of ``length_s`` seconds."""
    n = len(energy)
    if n <= length_s:
        return 0.0
    best_i, best_sum = 0, float("-inf")
    run = sum(energy[:length_s])
    for i in range(0, n - length_s + 1):
        if i:
            run += energy[i + length_s - 1] - energy[i - 1]
        if run > best_sum:
            best_i, best_sum = i, run
    return float(best_i)


def build_map(name: str, duration: float, bpm: float, beats: Sequence[float],
              strengths: Sequence[float], energy: Sequence[float]) -> dict:
    downbeats = downbeats_from_beats(beats, strengths)

    def snap(t: Optional[float]) -> Optional[float]:
        if t is None:
            return None
        d = _nearest(downbeats, t)
        return round(d if d is not None else t, 3)

    drop = find_drop(energy)
    return {
        "file": name, "duration": round(float(duration), 2), "bpm": round(float(bpm), 1),
        "beats": [round(float(b), 3) for b in beats], "downbeats": downbeats,
        "drop_s": snap(drop),
        "best_start_15s": snap(best_window_start(energy, 15)),
        "best_start_30s": snap(best_window_start(energy, 30)),
        "energy_1s": [round(float(e), 4) for e in energy],
    }


# ---------------------------------------------------------------------------
# librosa-backed analysis (lazy import)
# ---------------------------------------------------------------------------

def analyze(path: Path) -> dict:
    try:
        import numpy as np
        import librosa
    except ImportError:
        sys.exit("librosa is required to GENERATE beat maps: "
                 "pip install librosa soundfile (in a separate venv; see this file's docstring)")
    y, sr = librosa.load(str(path), sr=22050, mono=True)
    duration = float(len(y)) / sr
    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    tempo, beat_frames = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr)
    bpm = float(np.atleast_1d(tempo)[0])
    beats = librosa.frames_to_time(beat_frames, sr=sr)
    strengths = [float(onset_env[min(int(f), len(onset_env) - 1)]) for f in beat_frames]
    seconds = int(duration)
    energy = [float(np.sqrt(np.mean(np.square(y[s * sr:(s + 1) * sr])))) for s in range(seconds + 1)
              if len(y[s * sr:(s + 1) * sr])]
    return build_map(path.name, duration, bpm, [float(b) for b in beats], strengths, energy)


def write_map(track: Path, data: dict, out_dir: Optional[Path] = None) -> Path:
    out_dir = out_dir or track.parent / ".beats"
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{track.name}.json"
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, dest)
    return dest


def iter_tracks(paths: Sequence[str]):
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix.lower() in AUDIO_EXTS and ".beats" not in f.parts:
                    yield f
        elif p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            yield p


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="+", help="audio files or directories")
    ap.add_argument("--force", action="store_true", help="regenerate existing maps")
    ap.add_argument("--out-dir", help="write maps here instead of <dir>/.beats")
    args = ap.parse_args(argv)
    done = skipped = 0
    for track in iter_tracks(args.paths):
        out_dir = Path(args.out_dir) if args.out_dir else None
        dest = (out_dir or track.parent / ".beats") / f"{track.name}.json"
        if dest.exists() and not args.force:
            skipped += 1
            continue
        print(f"analysing {track.name} ...", flush=True)
        write_map(track, analyze(track), out_dir)
        done += 1
    print(f"{done} map(s) written, {skipped} skipped (use --force to redo)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
