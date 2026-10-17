#!/usr/bin/env python3
"""Hands-off: script assembly + auto colour balance + reference-image looks + comparison grid -> Resolve Free.

  # 1) run steps 1-4 (+ queue the Resolve assembly), then go get coffee
  scripts/assemble_and_grade.py --clips DIR_OR_MANIFEST.json [--script script.txt] [--refs REFDIR] [--out JOB]
  # 2) after watching grid_4k.mp4, pick a look (name from report.md, or Balanced)
  scripts/assemble_and_grade.py --out JOB --apply-look NAME [--no-queue] [--no-preview]

Everything is local: ffmpeg + numpy, whisper only with --transcribe, no cloud calls.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clips", help="folder of clips, or a manifest .json ([{path,start,end}])")
    ap.add_argument("--script", help="script/shot-list text file (numbered lines, bullets or paragraphs); omit for capture-time order")
    ap.add_argument("--refs", help="folder of reference images (or one image)")
    ap.add_argument("--out", required=True, help="job name (-> jobs/<name>) or a directory path")
    ap.add_argument("--apply-look", metavar="NAME", help="step 5: apply this look (or 'Balanced') to the whole timeline")
    ap.add_argument("--no-queue", action="store_true", help="do not queue anything for Resolve (files are still written)")
    ap.add_argument("--no-preview", action="store_true", help="with --apply-look: skip the ffmpeg graded preview")
    ap.add_argument("--transcribe", action="store_true", help="whisper-transcribe clips missing a library transcript (local)")
    ap.add_argument("--no-library", action="store_true", help="ignore the MediaForge library (captions/transcripts/city)")
    ap.add_argument("--method", choices=["reinhard", "sot"], default="reinhard", help="colour-transfer method")
    ap.add_argument("--strength", type=float, default=0.8, help="look strength 0..1")
    ap.add_argument("--skin-protect", type=float, default=0.7, help="skin-tone protection 0..1")
    ap.add_argument("--resolve-quality", choices=["proxy", "edit", "finish"], default="proxy", help="DNxHR profile for conditioned media")
    ap.add_argument("--quad-cap", default="1920x1080", help="max quadrant size (landscape WxH; portrait is transposed)")
    ap.add_argument("--montage-seconds", type=float, default=36.0)
    args = ap.parse_args()

    from app.grade import job

    def progress(step, msg):
        print(f"[{step}] {msg}", flush=True)

    if args.apply_look:
        res = job.apply_look(args.out, args.apply_look, queue=not args.no_queue, preview=not args.no_preview, progress=progress)
        print(json.dumps(res, indent=2, default=str))
        return 0
    if not args.clips:
        ap.error("--clips is required (unless --apply-look)")
    cw, ch = (int(x) for x in args.quad_cap.lower().split("x"))
    state = job.run_job(args.clips, args.out, script=args.script, refs=args.refs, transcribe=args.transcribe,
                        use_library=not args.no_library, look_method=args.method, strength=args.strength,
                        skin_protect=args.skin_protect, resolve_quality=args.resolve_quality, queue=not args.no_queue,
                        grid_cap=(cw, ch), max_montage=args.montage_seconds, progress=progress)
    print(f"\nDONE ({state['status']}) in {state['total_seconds']} s -> {state['dir']}/report.md")
    if state.get("looks"):
        print("Looks:", ", ".join(["Balanced"] + list(state["looks"])))
        print(f"Next: watch {state['dir']}/grid_4k.mp4, then --apply-look NAME")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
