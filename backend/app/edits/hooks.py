"""Hook library: slot-filled opening-line templates -> timeline overlays.

    build("contrast", {"common_advice": "Original opening", "hidden_part": "Original insight"})

returns overlay dicts (Project.timeline_overlays entries) for the first 1-3 s:
one text overlay per line (lines enter staggered, all leave together) plus an
optional full-frame pattern-interrupt inset when the template defines one and a
media path is supplied.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from . import style_pack
from .subtitles import text_width

LIBRARY = Path(__file__).with_name("hooks.json")
SLOT = re.compile(r"\{(\w+)\}")
LINE_GAP = 1.12  # line pitch as a multiple of the font em


@lru_cache(maxsize=1)
def library():
    return json.loads(LIBRARY.read_text(encoding="utf8"))["hooks"]


def get(template_id):
    for h in library():
        if h["id"] == template_id:
            return h
    raise KeyError(f"Unknown hook template: {template_id}")


def clean_slot(value):
    # Slots are text, never markup: no emphasis stars, no line breaks.
    return " ".join(str(value).replace("*", "").split())


def fill(template, slots=None):
    """Return the template's lines with slots filled (defaults for missing ones)."""
    slots = dict(slots or {})
    unknown = set(slots) - set(template["slots"])
    if unknown:
        raise ValueError(f"Unknown slot(s): {', '.join(sorted(unknown))}")
    values = {
        k: clean_slot(slots.get(k) or spec["default"])
        for k, spec in template["slots"].items()
    }
    return [SLOT.sub(lambda m: values[m.group(1)], line) for line in template["lines"]]


def build(template_id, slots=None, start=0.0, duration=None, accent=None, y=None,
          insert_media=None):
    t = get(template_id)
    lines = fill(t, slots)
    end = start + (duration if duration is not None else t["duration"])
    y0 = t["y"] if y is None else y
    over = {}
    if accent:
        over["highlight"] = accent
    # One em size for every line: the preset size, shrunk so the longest line fits.
    preset = style_pack.PRESETS[t["style"]]
    em = float(preset["size"])
    limit = 1920 * 9 / 16 * preset["max_width"]  # at 1080 wide
    for line in lines:
        w = text_width(line.replace("*", ""), preset["font"], False, em)
        if w > limit:
            em *= limit / w
    em = int(em)
    pitch = em * LINE_GAP / 1920
    out = []
    for k, line in enumerate(lines):
        a = start + k * t["stagger"]
        if a >= end:
            break
        out.append(
            dict(
                start=round(a, 3),
                end=round(end, 3),
                text=line,
                words=[],
                style=dict(preset=t["style"], size=em,
                           y=round(y0 + (k - (len(lines) - 1) / 2) * pitch, 4), **over),
            )
        )
    ins = t.get("insert")
    if ins and insert_media:
        a = start + ins["at"]
        out = style_pack.inset(
            round(a, 3), round(min(end, a + ins["duration"]), 3), insert_media,
            x=0, y=0, w=1, h=1, radius=0, shadow=False, anim="none",
        ) + out
    return out
