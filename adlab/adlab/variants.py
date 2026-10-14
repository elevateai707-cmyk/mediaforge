"""Variant grouping: is an advertiser running the same concept in several versions?

Being scaled is one of the few public signals that an ad works. Two ads from the
same advertiser (same site niche) are variants when either
* their visuals are near-identical: 64-bit difference hash (dHash) of the image,
  or of a frame 1 s into a video, within `MAX_HAMMING` bits; or
* their copy is near-identical: difflib ratio >= `TEXT_SIMILARITY` on texts of
  at least 20 characters.
Groups are connected components (A~B and B~C puts A, B, C together), recomputed
from scratch on every scout run, so they never drift.
"""
from __future__ import annotations

import difflib
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from PIL import Image

MAX_HAMMING = 10
TEXT_SIMILARITY = 0.8
VIDEO_FRAME_AT = "1.0"


def dhash_image(path: Path) -> str:
    """Difference hash: 9x8 greyscale, compare horizontal neighbours -> 64 bits (hex)."""
    with Image.open(path) as im:
        px = list(im.convert("L").resize((9, 8), Image.LANCZOS).tobytes())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (px[row * 9 + col] > px[row * 9 + col + 1])
    return f"{bits:016x}"


def dhash_video(path: Path, timeout: float = 60) -> Optional[str]:
    """dHash of one frame (1 s in, or the first frame for very short clips)."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return None
    with tempfile.TemporaryDirectory(prefix="adlab_frame_") as tmp:
        frame = Path(tmp) / "f.png"
        for seek in (VIDEO_FRAME_AT, "0"):
            proc = subprocess.run(
                [ffmpeg, "-v", "error", "-nostdin", "-ss", seek, "-i", str(path), "-frames:v", "1", "-y", str(frame)],
                capture_output=True, timeout=timeout,
            )
            if proc.returncode == 0 and frame.is_file() and frame.stat().st_size:
                return dhash_image(frame)
    return None


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s$%]", " ", text.lower())).strip()


def similar_text(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    if len(a) < 20 or len(b) < 20:
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= TEXT_SIMILARITY


def group(items: list[dict]) -> dict[str, str]:
    """items: {id, key (site+advertiser), text, dhashes: [hex]} -> {id: group_id}."""
    parent = {it["id"]: it["id"] for it in items}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_key: dict[str, list[dict]] = {}
    for it in items:
        by_key.setdefault(it["key"], []).append(it)
    for members in by_key.values():
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                visual = any(hamming(x, y) <= MAX_HAMMING for x in a["dhashes"] for y in b["dhashes"])
                if visual or similar_text(a["text"], b["text"]):
                    ra, rb = find(a["id"]), find(b["id"])
                    if ra != rb:
                        parent[max(ra, rb)] = min(ra, rb)
    return {it["id"]: "vg_" + find(it["id"])[:12] for it in items}
