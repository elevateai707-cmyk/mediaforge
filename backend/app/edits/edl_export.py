"""CMX3600 EDL export.

Standard edit decision list readable by Resolve, Premiere, FCP, Kdenlive,
Shotcut and CapCut. Non-drop-frame timecode, one event per plan clip:

    000001  AX       V     C        00:00:00:00 00:00:05:00 00:00:00:00 00:00:05:00
    * FROM CLIP NAME: IMG_1234.MOV

Frame math: round(start * fps); fps probed per source file via ffprobe
(default 30). Record in/out are cumulative on the timeline; source in/out are
the trim points inside the original file.

Output: exports/<plan_id>.edl
"""
from __future__ import annotations

import logging
from pathlib import Path

from .. import config, models
from ..db import SessionLocal
from .render import probe_fps

log = logging.getLogger("mediaforge.edl")


def _tc(frames: int, fps: int) -> str:
    """Frames -> HH:MM:SS:FF (NDF)."""
    frames = max(0, int(frames))
    h, rem = divmod(frames, 3600 * fps)
    m, s = divmod(rem, 60 * fps)
    f = s % fps
    s = s // fps
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def _load_plan(plan_id: str) -> list[tuple[models.EditClip, models.Asset]]:
    with SessionLocal() as db:
        plan = db.get(models.EditPlan, plan_id)
        if plan is None:
            raise ValueError(f"plan {plan_id!r} not found")
        rows = []
        for c in plan.clips:
            asset = db.get(models.Asset, c.asset_id)
            if asset is None:
                continue
            rows.append((c, asset))
        if not rows:
            raise ValueError(f"plan {plan_id!r} has no usable clips")
        return rows


def export_edl(plan_id: str) -> str:
    """Write exports/<plan_id>.edl; returns the output path."""
    rows = _load_plan(plan_id)

    lines: list[str] = [
        f"TITLE: MediaForge plan {plan_id}",
        "FCM: NON-DROP FRAME",
        "",
    ]

    rec_in = 0
    for idx, (c, asset) in enumerate(rows, start=1):
        fps = int(round(probe_fps(asset.path)))
        src_in = int(round(float(c.start) * fps))
        src_out = int(round(float(c.end) * fps))
        dur = max(1, src_out - src_in)
        rec_out = rec_in + dur
        lines.append(
            f"{idx:06d}  AX       V     C        "
            f"{_tc(src_in, fps)} {_tc(src_out, fps)} "
            f"{_tc(rec_in, fps)} {_tc(rec_out, fps)}"
        )
        lines.append(f"* FROM CLIP NAME: {Path(asset.path).name}")
        lines.append("")
        rec_in = rec_out

    out_path = config.EXPORTS_DIR / f"{plan_id}.edl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("edl export written: %s (%d events)", out_path, len(rows))
    return str(out_path)
