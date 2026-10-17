#!/usr/bin/env python
"""Re-caption the library with the cloud vision model (Settings -> Cloud LLM).

Assets keep their poster/thumb frame; scenes are captioned from a frame at
their midpoint. Resumable: without --force only rows whose caption is empty
(or, for assets, still the old local caption) are touched. Nothing is
deleted — captions are overwritten in place.

    backend/.venv/bin/python scripts/recaption.py --what assets --dry-run
    backend/.venv/bin/python scripts/recaption.py --what assets --workers 6
    backend/.venv/bin/python scripts/recaption.py --what scenes --limit 50
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import config, models  # noqa: E402
from app.ai import captions, cloud_llm  # noqa: E402
from app.ai.ollama_llm import image_to_b64  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.ingest import thumbs  # noqa: E402

_print_lock = threading.Lock()


def asset_frame(asset) -> str | None:
    if asset.kind == "photo":
        return asset.path if os.path.exists(asset.path) else None
    for candidate in (asset.poster_path, asset.thumb_path):
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def scene_frame(scene, asset) -> str | None:
    """Frame at the scene midpoint, cached under THUMBS_DIR/<asset>/scene_<id>.jpg."""
    if not os.path.exists(asset.path):
        return None
    out = config.THUMBS_DIR / str(asset.id) / f"scene_{scene.id}.jpg"
    if out.exists():
        return str(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    start = float(scene.start or 0.0)
    end = float(scene.end or start)
    return thumbs.extract_frame(asset.path, str(out), at=start + max(0.0, (end - start) / 2))


def caption_one(frame: str) -> str:
    b64 = image_to_b64(frame, max_side=1024)
    if not b64:
        raise RuntimeError("could not encode frame")
    return captions._clean(cloud_llm.caption(b64))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--what", choices=("assets", "scenes"), default="assets")
    ap.add_argument("--limit", type=int, default=0, help="0 = everything")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--force", action="store_true", help="also redo rows that already have a caption")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not cloud_llm.available():
        print("No OpenRouter key found.", file=sys.stderr)
        return 2

    with SessionLocal() as db:
        if args.what == "assets":
            q = db.query(models.Asset)
            if not args.force:
                q = q.filter((models.Asset.caption.is_(None)) | (models.Asset.caption == ""))
            rows = q.order_by(models.Asset.id).all()
            work = [(a.id, asset_frame(a)) for a in rows]
        else:
            q = db.query(models.Scene, models.Asset).join(
                models.Asset, models.Scene.asset_id == models.Asset.id)
            if not args.force:
                q = q.filter((models.Scene.caption.is_(None)) | (models.Scene.caption == ""))
            rows = q.order_by(models.Scene.id).all()
            work = [(s.id, scene_frame(s, a)) for s, a in rows]

    work = [w for w in work if w[1]]
    if args.limit:
        work = work[: args.limit]
    print(f"{len(work)} {args.what} to caption with {config.CLOUD_CAPTION_MODEL} "
          f"({args.workers} workers)")
    if args.dry_run or not work:
        return 0

    done = {"n": 0, "fail": 0}
    started = time.time()

    def run(item):
        row_id, frame = item
        try:
            text = caption_one(frame)
        except Exception as exc:  # noqa: BLE001 - keep going, report at the end
            done["fail"] += 1
            with _print_lock:
                print(f"  ! {args.what[:-1]} {row_id}: {exc}")
            return
        with SessionLocal() as db:
            model = models.Asset if args.what == "assets" else models.Scene
            row = db.get(model, row_id)
            if row is not None and text:
                row.caption = text[:300]
                db.commit()
        done["n"] += 1
        if done["n"] % 25 == 0:
            rate = done["n"] / max(1e-6, time.time() - started)
            left = (len(work) - done["n"]) / max(1e-6, rate)
            with _print_lock:
                print(f"  {done['n']}/{len(work)}  {rate:.1f}/s  ~{left/60:.0f} min left")

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(run, work))

    print(f"done: {done['n']} captioned, {done['fail']} failed, "
          f"{(time.time() - started)/60:.1f} min")
    return 1 if done["fail"] and not done["n"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
