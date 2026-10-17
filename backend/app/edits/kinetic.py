"""Kinetic caption style for story renders.

Body words: one word at a time, Montserrat Medium, white with a soft shadow,
mid-frame, fade + blur-in with a small upward drift (ASS, ``captions.ass``).
Emphasis words: huge Montserrat Black with a cyan -> blue -> magenta gradient.
Plain ASS cannot draw gradients, so they are rendered as transparent PNGs with
Pillow (an ~8 frame RGB-split glitch-in sequence, then a clean hold frame) and
overlaid by ffmpeg. A small Regular connector word (the 1-2 words before the
emphasis word) is drawn in ASS over the big word's top-left corner.

Everything scales with the output resolution; ``u = min(W, H) / 1080``.
"""
from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Optional

from .. import config

FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
BODY_FONT = "Montserrat Medium"
CONNECTOR_FONT = "Montserrat"
EMPH_FONT_FILE = "Montserrat-Black.ttf"

GRADIENT = [(0x19, 0xC6, 0xE6), (0x3A, 0x6C, 0xF4), (0xC0, 0x2B, 0xD8)]
GLITCH_FRAMES = 8
MAX_EMPH_PER_BEAT = 2
MAX_EMPH_TOTAL = 24
HOLD_PAD = 0.4            # words linger this long past their end (cap)
BODY_Y = 0.50
EMPH_Y = 0.22
EMPH_SCALE = 2.7

STOPWORDS = {
    "about", "above", "after", "again", "against", "because", "before", "being",
    "below", "between", "could", "doing", "during", "every", "further", "having",
    "itself", "myself", "other", "ought", "should", "since", "still", "their",
    "theirs", "there", "these", "those", "through", "under", "until", "where",
    "which", "while", "would", "yourself", "another", "anyone", "anything",
    "around", "almost", "always", "maybe", "never", "nothing", "something",
    "everything", "someone", "without", "within", "really", "though", "although",
}


def fonts_available() -> bool:
    return all((FONTS_DIR / f).is_file() for f in
               ("Montserrat-Regular.ttf", "Montserrat-Medium.ttf", EMPH_FONT_FILE))


def _unit(w: int, h: int) -> float:
    return min(w, h) / 1080.0


def body_size(w: int, h: int) -> int:
    """~4.5% of frame height on 9:16; landscape gets a larger share so it reads."""
    return int(round(h * (0.045 if h >= w else 0.06)))


# ---------------------------------------------------------------------------
# Emphasis markup
# ---------------------------------------------------------------------------

_TRAIL = ".,!?;:\"')]"


def parse_emphasis(raw: str) -> tuple[str, list[tuple[int, int]]]:
    """``"the city *wakes up*."`` -> ("the city wakes up.", [(2, 4)]).

    Spans are (start, end_exclusive) indices into ``clean.split()``. An
    unclosed ``*`` runs to the end of the text.
    """
    out: list[str] = []
    spans: list[tuple[int, int]] = []
    open_at: Optional[int] = None
    for tok in raw.split():
        lead = tok.lstrip("\"'([")
        opens = lead.startswith("*")
        body = tok.rstrip(_TRAIL)
        closes = body.endswith("*") and (len(body) > 1 or open_at is not None)
        clean = tok.replace("*", "")
        if not clean:
            continue
        idx = len(out)
        out.append(clean)
        if opens and open_at is None:
            open_at = idx
        if closes and open_at is not None:
            spans.append((open_at, idx + 1))
            open_at = None
    if open_at is not None and out:
        spans.append((open_at, len(out)))
    return " ".join(out), spans


def auto_emphasis(words: list[str]) -> list[tuple[int, int]]:
    """One content word: the longest with >= 6 letters that is not a stopword."""
    best = -1
    best_len = 5
    for i, w in enumerate(words):
        letters = "".join(c for c in w if c.isalpha())
        if len(letters) <= best_len or letters.lower() in STOPWORDS:
            continue
        best, best_len = i, len(letters)
    return [(best, best + 1)] if best >= 0 else []


# ---------------------------------------------------------------------------
# Event planning (pure)
# ---------------------------------------------------------------------------

def _strip_trail(text: str) -> str:
    return text.rstrip(".,!?;:\"')]").lstrip("\"'([") or text


def plan_events(beats: list[dict]) -> dict:
    """Timeline events from story.json beats.

    Returns {"body": [{start,end,text}], "emphasis": [{start,end,text,
    connector, connector_start}]}. Emphasis words and their connector words are
    excluded from ``body``.
    """
    body: list[dict] = []
    emph: list[dict] = []
    for beat in beats:
        words = beat.get("words") or []
        if not words:
            continue
        off = float(beat["start"])
        slot_end = off + float(beat.get("slot") or (words[-1]["end"] + 0.3))
        spans = beat.get("emphasis")
        if spans is None:
            spans = auto_emphasis([w["word"] for w in words])
        spans = [(int(s), int(e)) for s, e in spans if 0 <= int(s) < int(e) <= len(words)]
        spans.sort()
        chosen: list[tuple[int, int]] = []
        for s, e in spans:
            if chosen and s < chosen[-1][1]:
                continue
            chosen.append((s, e))
        chosen = chosen[:MAX_EMPH_PER_BEAT]
        skip: set[int] = set()
        for s, e in chosen:
            skip.update(range(s, e))
        for s, e in chosen:
            conn_idx = [i for i in range(max(0, s - 2), s) if i not in skip]
            # only contiguous words directly before the emphasis word
            while conn_idx and conn_idx[-1] != s - 1:
                conn_idx.pop()
            while len(conn_idx) > 1 and conn_idx[-1] - conn_idx[0] != len(conn_idx) - 1:
                conn_idx.pop(0)
            skip.update(conn_idx)
            emph.append({
                "start": off + words[s]["start"],
                "end": min(off + words[e - 1]["end"] + HOLD_PAD, slot_end),
                "text": _strip_trail(" ".join(w["word"] for w in words[s:e])),
                "connector": " ".join(words[i]["word"] for i in conn_idx) or None,
                "connector_start": off + words[conn_idx[0]]["start"] if conn_idx else None,
            })
        for i, w in enumerate(words):
            if i in skip:
                continue
            start = off + float(w["start"])
            cap = off + float(w["end"]) + HOLD_PAD
            nxt = off + float(words[i + 1]["start"]) if i + 1 < len(words) else cap
            end = min(nxt if nxt > start else cap, cap, slot_end)
            body.append({"start": start, "end": max(end, start + 0.05), "text": w["word"]})
    emph = emph[:MAX_EMPH_TOTAL]
    return {"body": body, "emphasis": emph}


# ---------------------------------------------------------------------------
# Emphasis PNGs
# ---------------------------------------------------------------------------

def _font(size: int):
    from PIL import ImageFont
    return ImageFont.truetype(str(FONTS_DIR / EMPH_FONT_FILE), size)


def emphasis_font_size(text: str, w: int, h: int) -> int:
    """2.7x body size, shrunk so the word fits in 88% of the frame width."""
    from PIL import ImageFont
    size = int(round(body_size(w, h) * EMPH_SCALE))
    font = ImageFont.truetype(str(FONTS_DIR / EMPH_FONT_FILE), size)
    width = font.getlength(text)
    limit = 0.88 * w
    if width > limit:
        size = max(12, int(size * limit / width))
    return size


def _gradient_at(x: float) -> tuple[int, int, int]:
    x = min(1.0, max(0.0, x)) * (len(GRADIENT) - 1)
    i = min(int(x), len(GRADIENT) - 2)
    f = x - i
    a, b = GRADIENT[i], GRADIENT[i + 1]
    return tuple(int(round(a[k] + (b[k] - a[k]) * f)) for k in range(3))  # type: ignore[return-value]


def render_emphasis_images(text: str, w: int, h: int, out_dir: Path, tag: str,
                           seed: int = 0) -> dict:
    """Write glitch frames + clean hold frame PNGs for one emphasis word.

    Returns {"frames": [paths...], "hold": path, "size": (iw, ih),
    "text_box": (left, top, right, bottom) inside the image}.
    """
    from PIL import Image, ImageChops, ImageFilter

    out_dir.mkdir(parents=True, exist_ok=True)
    u = _unit(w, h)
    size = emphasis_font_size(text, w, h)
    font = _font(size)
    l, t, r, b = font.getbbox(text)
    tw, th = r - l, b - t
    pad = int(0.15 * size + 30 * u)
    iw, ih = tw + 2 * pad, th + 2 * pad

    mask = Image.new("L", (iw, ih), 0)
    from PIL import ImageDraw
    ImageDraw.Draw(mask).text((pad - l, pad - t), text, font=font, fill=255)

    strip = Image.new("RGB", (iw, 1))
    sp = strip.load()
    for x in range(iw):
        sp[x, 0] = _gradient_at((x - pad) / max(1, tw))
    grad = strip.resize((iw, ih))
    base = grad.convert("RGBA")
    base.putalpha(mask)

    def finish(img: "Image.Image") -> "Image.Image":
        a = img.getchannel("A")
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        shadow_a = ImageChops.offset(a, 0, max(2, int(5 * u))).filter(
            ImageFilter.GaussianBlur(max(2, 6 * u))).point(lambda p: int(p * 0.55))
        sh.putalpha(shadow_a)
        return Image.alpha_composite(sh, img)

    def glitch(k: int) -> "Image.Image":
        d = int(round(12 * u * (1 - k / (GLITCH_FRAMES - 1)))) if GLITCH_FRAMES > 1 else 0
        if d == 0:
            return base
        r_, g_, b_, a_ = base.split()
        r2 = ImageChops.offset(r_, d, 0)
        b2 = ImageChops.offset(b_, -d, 0)
        alpha = ImageChops.lighter(ImageChops.lighter(a_, ImageChops.offset(a_, d, 0)),
                                   ImageChops.offset(a_, -d, 0))
        img = Image.merge("RGBA", (r2, g_, b2, alpha))
        rng = random.Random(seed * 997 + k)
        if k < GLITCH_FRAMES - 2:
            for _ in range(1 + (k % 2)):
                band_h = max(4, int(ih * rng.uniform(0.10, 0.2)))
                y0 = rng.randint(0, max(0, ih - band_h))
                dx = int(rng.choice((-1, 1)) * (d * 1.6 + 6 * u))
                band = img.crop((0, y0, iw, y0 + band_h))
                img.paste((0, 0, 0, 0), (0, y0, iw, y0 + band_h))
                img.paste(ImageChops.offset(band, dx, 0), (0, y0))
        return img

    frames: list[str] = []
    for k in range(GLITCH_FRAMES - 1):
        p = out_dir / f"{tag}_g{k}.png"
        finish(glitch(k)).save(p)
        frames.append(str(p))
    hold = out_dir / f"{tag}_hold.png"
    finish(base).save(hold)
    return {"frames": frames, "hold": str(hold), "size": (iw, ih),
            "text_box": (pad, pad, pad + tw, pad + th)}


def write_sequence_list(info: dict, duration: float, list_path: Path, fps: float) -> float:
    """concat-demuxer list: glitch frames then the hold frame. Returns total length."""
    frame = 1.0 / fps
    lines: list[str] = []
    for p in info["frames"]:
        lines.append(f"file '{p}'\nduration {frame:.5f}")
    hold_len = max(0.1, duration - frame * len(info["frames"]))
    lines.append(f"file '{info['hold']}'\nduration {hold_len:.5f}")
    lines.append(f"file '{info['hold']}'")  # concat quirk: last duration needs a trailing entry
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return frame * len(info["frames"]) + hold_len


# ---------------------------------------------------------------------------
# ASS
# ---------------------------------------------------------------------------

def _ass_time(t: float) -> str:
    cs = max(0, int(round(t * 100)))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")")


def build_ass(events: dict, w: int, h: int, layout: Optional[list[dict]] = None) -> str:
    """ASS document for body words and connector words.

    ``layout``: per-emphasis dict {"text_left", "text_top"} (frame pixels), from
    the PNG geometry, so connectors land on the big word's top-left corner.
    """
    u = _unit(w, h)
    size = body_size(w, h)
    csize = int(round(size * 0.9))
    body_font, conn_font = BODY_FONT, CONNECTOR_FONT
    if not fonts_available():
        body_font = conn_font = "DejaVu Sans"
    shad = max(1, round(2.5 * u))
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {w}\nPlayResY: {h}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,"
        "BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,"
        "BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"Style: Body,{body_font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
        f"0,0,0,0,100,100,0,0,1,0,{shad},5,0,0,0,1\n"
        f"Style: Connector,{conn_font},{csize},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
        f"0,0,0,0,100,100,0,0,1,0,{shad},1,0,0,0,1\n\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )
    drift = max(2, round(8 * u))
    x = w // 2
    y = int(round(h * BODY_Y))
    lines = [head]
    for ev in events["body"]:
        tag = (f"{{\\an5\\move({x},{y + drift},{x},{y},0,80)\\fad(80,0)\\blur6"
               f"\\t(0,80,\\blur0)\\4a&H70&}}")
        lines.append(f"Dialogue: 0,{_ass_time(ev['start'])},{_ass_time(ev['end'])},"
                     f"Body,,0,0,0,,{tag}{_ass_escape(ev['text'])}\n")
    for i, ev in enumerate(events["emphasis"]):
        if not ev.get("connector"):
            continue
        if layout and i < len(layout):
            cx, cy = layout[i]["text_left"], layout[i]["text_top"]
        else:
            cx, cy = int(w * 0.1), int(h * (EMPH_Y - 0.05))
        cx = int(cx - 0.01 * w)
        cy = int(cy + 0.35 * csize)
        tag = (f"{{\\an1\\move({cx},{cy + drift},{cx},{cy},0,80)\\fad(80,0)\\blur6"
               f"\\t(0,80,\\blur0)\\4a&H70&}}")
        start = ev["connector_start"] if ev.get("connector_start") is not None else ev["start"]
        lines.append(f"Dialogue: 1,{_ass_time(start)},{_ass_time(ev['end'])},"
                     f"Connector,,0,0,0,,{tag}{_ass_escape(ev['connector'])}\n")
    return "".join(lines)


def emphasis_layout(infos: list[dict], w: int, h: int) -> list[dict]:
    """Frame-pixel text corner + image position for each emphasis PNG."""
    out = []
    for info in infos:
        iw, ih = info["size"]
        x0 = (w - iw) // 2
        y0 = int(round(h * EMPH_Y - ih / 2))
        l, t, _r, _b = info["text_box"]
        out.append({"x": x0, "y": y0, "text_left": x0 + l, "text_top": y0 + t})
    return out
