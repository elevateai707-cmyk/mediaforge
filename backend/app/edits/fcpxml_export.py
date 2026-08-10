"""FCPXML 1.10 export (Final Cut Pro interchange format).

Writes an FCPXML 1.10 document with:
- <resources>: one <format> (timebase derived from the source fps, default 30)
  plus one <asset> per unique source file (href = file:// URL),
- <library>/<event>/<project>/<sequence>/<spine>: one <clip> per plan clip,
  with offset/duration in integer ticks at the project timebase
  (1s = <fps> ticks; set MF_FCPXML_MS_TICKS=1 for a millisecond timebase
  where 1s = 1000 ticks), trim via clip start/duration,
- per-clip caption text as a <text> element (subtitle style).

Output: exports/<plan_id>.fcpxml. Serialized with xml.etree.ElementTree
(text is XML-escaped automatically).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

from .. import config, models
from ..db import SessionLocal
from .render import probe_fps

log = logging.getLogger("mediaforge.fcpxml")

MS_TICKS = os.environ.get("MF_FCPXML_MS_TICKS", "0") == "1"


def _ticks(seconds: float, fps: float) -> int:
    """Seconds -> integer ticks at the project timebase."""
    if MS_TICKS:
        return int(round(float(seconds) * 1000.0))
    return int(round(float(seconds) * fps))


def _tc(seconds: float, fps: float) -> str:
    """Seconds -> HH:MM:SS:FF (NDF) timecode string."""
    total = max(0, int(round(float(seconds) * fps)))
    frames = total % int(round(fps))
    total_s = total // int(round(fps))
    h, rem = divmod(total_s, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}:{frames:02d}"


def _frame_duration(fps: float) -> str:
    """FCPXML frameDuration string for the project format."""
    fps = float(fps)
    if MS_TICKS:
        return "1/1000s"
    if abs(fps - 29.97) < 0.01:
        return "100/3001s"
    if abs(fps - 23.976) < 0.01:
        return "1001/24000s"
    if abs(fps - 59.94) < 0.01:
        return "100/6006s"
    return f"1/{int(round(fps))}s"


def _format_name(fps: float, width: int, height: int) -> str:
    fps = float(fps)
    if MS_TICKS:
        return f"FFVideoFormat{width}x{height}ms"
    if abs(fps - 29.97) < 0.01:
        return f"FFVideoFormat{height}p2997"
    return f"FFVideoFormat{height}p{int(round(fps))}"


def _load_plan(plan_id: str) -> tuple[models.EditPlan, list[tuple[models.EditClip, models.Asset]]]:
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
        return plan, rows


def export_fcpxml(plan_id: str) -> str:
    """Write exports/<plan_id>.fcpxml; returns the output path."""
    plan, rows = _load_plan(plan_id)

    fps = 30.0
    width, height = 1920, 1080
    for _c, asset in rows:
        fps = probe_fps(asset.path) or fps
        if asset.width and asset.height:
            width, height = int(asset.width), int(asset.height)

    # FCPXML is namespace-free; ElementTree writes plain tags by default.
    # -- resources -----------------------------------------------------------
    resources = ET.Element("resources")
    fmt = ET.SubElement(resources, "format",
                        id="r1",
                        name=_format_name(fps, width, height),
                        frameDuration=_frame_duration(fps),
                        width=str(width), height=str(height))
    style = ET.SubElement(resources, "text-style",
                          id="ts1",
                          fontName="Helvetica",
                          fontSize="40",
                          fontColor="1 1 1 1",
                          shadowColor="0 0 0 0.6",
                          shadowOffset="0 -2",
                          alignment="center")

    # One asset resource per unique source path.
    asset_ids: dict[str, str] = {}
    asset_seq = 1
    for _c, asset in rows:
        if asset.path in asset_ids:
            continue
        rid = f"r{asset_seq + 1}"
        asset_seq += 1
        asset_ids[asset.path] = rid
        name = Path(asset.path).name
        src = Path(asset.path).as_uri()
        a_fps = probe_fps(asset.path) or fps
        dur = float(asset.duration or 0.0)
        ET.SubElement(
            resources, "asset",
            id=rid, name=name,
            start="0s",
            duration=f"{_ticks(dur, fps)}/{int(round(fps))}s"
            if not MS_TICKS else f"{_ticks(dur, fps)}/1000s",
            hasVideo="1", hasAudio="1" if _asset_has_audio(asset) else "0",
            format="r1", src=src,
        )

    # -- project -------------------------------------------------------------
    seq_dur = sum(_ticks(float(c.end) - float(c.start), fps) for c, _ in rows)
    seq_dur_s = sum(float(c.end) - float(c.start) for c, _ in rows)
    sequence = ET.Element(
        "sequence",
        format="r1",
        duration=f"{seq_dur}/{int(round(fps))}s"
        if not MS_TICKS else f"{seq_dur}/1000s",
        tcStart="0s",
        tcFormat="NDF",
        audioLayout="stereo",
        audioRate="48000",
    )
    spine = ET.SubElement(sequence, "spine")

    offset_ticks = 0
    for c, asset in rows:
        dur_s = float(c.end) - float(c.start)
        clip = ET.SubElement(
            spine, "clip",
            name=Path(asset.path).name,
            offset=f"{offset_ticks}/{int(round(fps))}s"
            if not MS_TICKS else f"{offset_ticks}/1000s",
            duration=f"{_ticks(dur_s, fps)}/{int(round(fps))}s"
            if not MS_TICKS else f"{_ticks(dur_s, fps)}/1000s",
            start=f"{_ticks(float(c.start), fps)}/{int(round(fps))}s"
            if not MS_TICKS else f"{_ticks(float(c.start), fps)}/1000s",
            format="r1",
            tcFormat="NDF",
            ref=asset_ids[asset.path],
        )
        if _asset_has_audio(asset):
            audio = ET.SubElement(clip, "audio")
            ET.SubElement(audio, "sourceChannelCount").text = "2"
            ET.SubElement(audio, "sourceChannelMode").text = "stereo"
        if c.caption and c.caption.strip():
            title = ET.SubElement(clip, "text")
            title_style = ET.SubElement(title, "text-style", ref="ts1")
            title_style.text = c.caption.strip()
        offset_ticks += _ticks(dur_s, fps)

    project = ET.Element(
        "project", name=f"MediaForge-{plan_id}", uid=f"MediaForge-{plan_id}")
    project.append(sequence)
    event = ET.Element("event", name="MediaForge")
    event.append(project)
    library = ET.Element("library")
    library.append(event)

    root = ET.Element("fcpxml", version="1.10")
    root.append(resources)
    root.append(library)

    out_path = config.EXPORTS_DIR / f"{plan_id}.fcpxml"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    if hasattr(ET, "indent"):  # Python 3.9+
        ET.indent(tree, space="  ")
    tree.write(out_path, encoding="utf-8", xml_declaration=True)
    log.info("fcpxml export written: %s (%d clips)", out_path, len(rows))
    return str(out_path)


def _asset_has_audio(asset: models.Asset) -> bool:
    """Best-effort: assets whose path looks like video are treated as having
    audio; a real stream check happens only for video files."""
    if asset.kind == "photo":
        return False
    ext = Path(asset.path).suffix.lower()
    return ext in config.VIDEO_EXTS
