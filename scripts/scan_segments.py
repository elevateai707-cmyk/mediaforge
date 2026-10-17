#!/usr/bin/env python
"""Motion-scan every drone clip and build a searchable index of best moments.

    python scripts/scan_segments.py --want 10 --workers 3

Writes <SD>/segments_index.json: one row per clip with its most dynamic
window, the motion score, and whatever the vision model already said about it.
Resumable — clips already in the index are skipped unless --force.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reel_builder import probe_duration  # noqa: E402

MEDIA = Path("/media/bfam/5C2B-86B2/DCIM/100MEDIA")
INDEX = Path("/media/bfam/5C2B-86B2/segments_index.json")
SCRATCH = Path("/tmp/claude-1000/-home-bfam/c64913c1-3527-4d68-8426-afb133b22c8c/scratchpad")


def motion_profile(path: str) -> list[float]:
    """Frame-to-frame difference at 4 fps across the whole file."""
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as fh:
        stats = fh.name
    try:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-i", path,
             "-vf", f"fps=4,scale=64:36,format=gray,signalstats,"
                    f"metadata=print:key=lavfi.signalstats.YDIF:file={stats}",
             "-an", "-f", "null", "-"],
            check=True, capture_output=True, timeout=1200)
        return [float(l.split("=")[1]) for l in open(stats) if "YDIF" in l]
    except Exception:  # noqa: BLE001
        return []
    finally:
        os.unlink(stats)


def best_window(ydif: list[float], want: float, dur: float) -> tuple[float, float, float]:
    """(start, score, mean_motion) for the steadiest-moving window."""
    if len(ydif) < 8:
        return max(0.0, (dur - want) / 2), 0.0, 0.0
    rate, window = 4.0, int(want * 4)
    lo, hi = int(len(ydif) * 0.10), int(len(ydif) * 0.90)
    best, best_score, best_mean = lo, -1e9, 0.0
    for i in range(lo, max(lo + 1, hi - window)):
        seg = ydif[i:i + window]
        if len(seg) < window:
            break
        mean = sum(seg) / len(seg)
        score = mean - 0.25 * (max(seg) - mean)
        if score > best_score:
            best_score, best, best_mean = score, i, mean
    return round(best / rate, 2), round(best_score, 3), round(best_mean, 3)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--want", type=float, default=10.0)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    # what the vision pass already knows about each clip
    captions: dict[str, dict] = {}
    for f in ("drone/score_v.json", "drone/score_h.json"):
        p = SCRATCH / f
        if p.exists():
            for r in json.loads(p.read_text()):
                if "hook" in r:
                    captions[r["name"]] = r
    rots = json.loads((SCRATCH / "rot.json").read_text()) if (SCRATCH / "rot.json").exists() else {}

    index: dict[str, dict] = {}
    if INDEX.exists() and not args.force:
        index = json.loads(INDEX.read_text())

    files = sorted(p for p in MEDIA.glob("*.MP4"))
    todo = [p for p in files if p.stem not in index]
    print(f"{len(files)} clips, {len(todo)} to scan, {len(index)} already indexed", flush=True)
    started = time.time()
    done = {"n": 0}

    def run(path: Path) -> None:
        dur = probe_duration(str(path))
        ydif = motion_profile(str(path))
        start, score, mean = best_window(ydif, min(args.want, max(1.0, dur)), dur)
        meta = captions.get(path.stem, {})
        index[path.stem] = {
            "file": str(path), "duration": round(dur, 2),
            "vertical": rots.get(str(path)) == 90,
            "best_start": start, "best_end": round(min(start + args.want, dur), 2),
            "motion_score": score, "motion_mean": mean,
            "caption": meta.get("caption", ""), "subject": meta.get("subject", ""),
            "hook": meta.get("hook"), "quality": meta.get("quality"),
        }
        done["n"] += 1
        if done["n"] % 10 == 0:
            rate = done["n"] / max(1e-6, time.time() - started)
            left = (len(todo) - done["n"]) / max(1e-6, rate) / 60
            print(f"  {done['n']}/{len(todo)}  {rate*60:.0f}/min  ~{left:.0f} min left", flush=True)
            INDEX.write_text(json.dumps(index, indent=1))      # checkpoint

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(run, todo))

    INDEX.write_text(json.dumps(index, indent=1))
    ranked = sorted(index.values(), key=lambda r: -(r["motion_score"]), )
    print(f"\ndone in {(time.time()-started)/60:.1f} min → {INDEX}")
    print("\ntop 15 by motion:")
    for r in ranked[:15]:
        print(f"  {Path(r['file']).stem}  motion {r['motion_score']:6.2f}  "
              f"{r['best_start']:6.1f}s  {'VERT' if r['vertical'] else 'horiz'}  "
              f"h{r['hook']} q{r['quality']}  {r['caption'][:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
