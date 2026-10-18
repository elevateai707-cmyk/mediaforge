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
from typing import Mapping, Optional

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
HOOK_SECONDS = 1.4         # hook headline on screen 0 .. HOOK_SECONDS
PORTRAIT_BODY = 0.055     # body caption size as a fraction of frame height

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


def _safe_rect(w: int, h: int, safe_zones: Optional[Mapping[str, float]] = None) -> tuple[int, int, int, int]:
    """Return the usable caption rectangle, accepting fractional margins.

    ``None`` intentionally means the full frame to retain the pre-safe-zone
    public behavior for callers that use this module directly.
    """
    zones = safe_zones or {}
    def margin(name: str) -> int:
        try:
            return max(0, min(int(round((w if name in ("left", "right") else h)
                                        * float(zones.get(name, 0)))),
                              (w if name in ("left", "right") else h) // 2))
        except (TypeError, ValueError):
            return 0
    left, right, top, bottom = margin("left"), margin("right"), margin("top"), margin("bottom")
    return left, top, max(2, w - left - right), max(2, h - top - bottom)


def body_size(w: int, h: int, safe_zones: Optional[Mapping[str, float]] = None) -> int:
    """~5.5% of frame height on 9:16; landscape gets a larger share so it reads."""
    _, _, _inner_w, inner_h = _safe_rect(w, h, safe_zones)
    return int(round(inner_h * (PORTRAIT_BODY if h >= w else 0.06)))


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


def plan_events(beats: list[dict], hook_end: float = 0.0) -> dict:
    """Timeline events from story.json beats.

    Emphasis words that would start before ``hook_end`` (a hook headline is on
    screen) stay ordinary body words so the two big texts never collide.

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
        chosen = [(s, e) for s, e in chosen[:MAX_EMPH_PER_BEAT]
                  if off + float(words[s]["start"]) >= hook_end]
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


def emphasis_font_size(text: str, w: int, h: int,
                       safe_zones: Optional[Mapping[str, float]] = None) -> int:
    """2.7x body size, shrunk so the word fits in 88% of the frame width."""
    from PIL import ImageFont
    size = int(round(body_size(w, h, safe_zones) * EMPH_SCALE))
    font = ImageFont.truetype(str(FONTS_DIR / EMPH_FONT_FILE), size)
    width = font.getlength(text)
    # Leave enough space for the rendered PNG's glow/shadow padding too.
    _, _, inner_w, inner_h = _safe_rect(w, h, safe_zones)
    limit = (0.72 if safe_zones is not None else 0.88) * inner_w
    if width > limit:
        size = max(1, int(size * limit / width))
    # The visual extent includes a glow pad that is proportional to font
    # size.  Recheck the actual PNG dimensions so its image, rather than only
    # its glyphs, fits within the safe rectangle for unusually long words.
    u = _unit(w, h)
    while size > 1:
        font = ImageFont.truetype(str(FONTS_DIR / EMPH_FONT_FILE), size)
        l, t, r, b = font.getbbox(text)
        pad = int(0.15 * size + 30 * u)
        if r - l + 2 * pad <= inner_w and b - t + 2 * pad <= inner_h:
            break
        size = max(1, size - max(1, size // 8))
    return size


def _gradient_at(x: float, colors: Optional[list] = None) -> tuple[int, int, int]:
    stops = colors or GRADIENT
    x = min(1.0, max(0.0, x)) * (len(stops) - 1)
    i = min(int(x), len(stops) - 2)
    f = x - i
    a, b = stops[i], stops[i + 1]
    return tuple(int(round(a[k] + (b[k] - a[k]) * f)) for k in range(3))  # type: ignore[return-value]


def render_emphasis_images(text: str, w: int, h: int, out_dir: Path, tag: str,
                           seed: int = 0,
                           safe_zones: Optional[Mapping[str, float]] = None) -> dict:
    """Write glitch frames + clean hold frame PNGs for one emphasis word.

    Returns {"frames": [paths...], "hold": path, "size": (iw, ih),
    "text_box": (left, top, right, bottom) inside the image}.
    """
    size = emphasis_font_size(text, w, h, safe_zones)
    return render_gradient_images([text], size, w, h, out_dir, tag, seed=seed)


def render_gradient_images(lines: list[str], size: int, w: int, h: int, out_dir: Path,
                           tag: str, seed: int = 0, colors: Optional[list] = None,
                           glitch_in: bool = True) -> dict:
    """Gradient-filled (multi-line, centred) text as transparent PNGs.

    ``glitch_in`` writes the RGB-split/slice-displacement frames before the clean
    hold frame; without it only the hold frame exists (``frames`` is empty).
    """
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    out_dir.mkdir(parents=True, exist_ok=True)
    u = _unit(w, h)
    font = _font(size)
    boxes = [font.getbbox(line) for line in lines]
    widths = [r - l for l, _t, r, _b in boxes]
    tw = max(widths)
    line_h = int(size * 1.15)
    top0 = min(t for _l, t, _r, _b in boxes)
    th = (len(lines) - 1) * line_h + max(b for _l, _t, _r, b in boxes) - top0
    pad = int(0.15 * size + 30 * u)
    iw, ih = tw + 2 * pad, th + 2 * pad

    mask = Image.new("L", (iw, ih), 0)
    draw = ImageDraw.Draw(mask)
    for i, (line, (l, _t, _r, _b), lw) in enumerate(zip(lines, boxes, widths)):
        draw.text((pad + (tw - lw) // 2 - l, pad + i * line_h - top0), line, font=font, fill=255)

    strip = Image.new("RGB", (iw, 1))
    sp = strip.load()
    for x in range(iw):
        sp[x, 0] = _gradient_at((x - pad) / max(1, tw), colors)
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
    if glitch_in:
        for k in range(GLITCH_FRAMES - 1):
            p = out_dir / f"{tag}_g{k}.png"
            finish(glitch(k)).save(p)
            frames.append(str(p))
    hold = out_dir / f"{tag}_hold.png"
    finish(base).save(hold)
    return {"frames": frames, "hold": str(hold), "size": (iw, ih),
            "text_box": (pad, pad, pad + tw, pad + th)}


def _split_lines(words: list[str], n: int) -> list[str]:
    """Split words into ``n`` consecutive lines minimising the widest line (by characters)."""
    if n <= 1 or len(words) <= 1:
        return [" ".join(words)]
    best, best_w = None, None
    from itertools import combinations
    for cuts in combinations(range(1, len(words)), min(n - 1, len(words) - 1)):
        parts, prev = [], 0
        for c in (*cuts, len(words)):
            parts.append(" ".join(words[prev:c]))
            prev = c
        widest = max(len(p) for p in parts)
        if best_w is None or widest < best_w:
            best, best_w = parts, widest
    return best or [" ".join(words)]


def hook_layout(text: str, w: int, h: int,
                safe_zones: Optional[Mapping[str, float]] = None) -> tuple[list[str], int]:
    """Uppercase hook wrapped to <= 3 lines at the largest size that fits the safe
    rectangle (<= 2.4x body size, <= 88% of safe width, <= 26% of safe height)."""
    from PIL import ImageFont
    safe_x, safe_y, safe_w, safe_h = _safe_rect(w, h, safe_zones)
    words = text.upper().split()
    base = int(round(body_size(w, h, safe_zones) * 2.4))
    u = _unit(w, h)
    best_lines, best_size = [" ".join(words)], 1
    for n in (1, 2, 3):
        lines = _split_lines(words, n)
        size = base
        while size > 8:
            font = ImageFont.truetype(str(FONTS_DIR / EMPH_FONT_FILE), size)
            width = max(font.getlength(line) for line in lines)
            height = int(size * 1.15) * (len(lines) - 1) + int(size * 1.0)
            pad = int(0.15 * size + 30 * u)
            if width + 2 * pad <= 0.96 * safe_w and height + 2 * pad <= 0.30 * safe_h \
                    and width <= 0.88 * safe_w:
                break
            size -= max(1, size // 12)
        if size > best_size:
            best_lines, best_size = lines, size
    return best_lines, best_size


def render_hook_images(text: str, w: int, h: int, out_dir: Path, tag: str = "hook",
                       safe_zones: Optional[Mapping[str, float]] = None) -> dict:
    """Hook headline PNGs plus its frame position (top-anchored in the safe zone)."""
    lines, size = hook_layout(text, w, h, safe_zones)
    info = render_gradient_images(lines, size, w, h, out_dir, tag, seed=7919)
    safe_x, safe_y, safe_w, safe_h = _safe_rect(w, h, safe_zones)
    iw, ih = info["size"]
    info["x"] = safe_x + (safe_w - iw) // 2
    info["y"] = safe_y
    info["lines"] = lines
    return info


def _legible(color: tuple[int, int, int]) -> tuple[int, int, int]:
    """Lighten colours too dark to read on the darkened end-card background."""
    r, g, b = color
    luma = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
    if luma >= 0.3:
        return color
    mix = 0.65
    return tuple(int(c + (255 - c) * mix) for c in color)  # type: ignore[return-value]


def _hex_rgb(value: str) -> tuple[int, int, int]:
    v = value.lstrip("#")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def brand_card_assets(brand: dict, w: int, h: int, out_dir: Path,
                      safe_zones: Optional[Mapping[str, float]] = None) -> dict:
    """Brand-name PNG (brand gradient) plus ASS text lines geometry for an end card.

    Returns {"name": image info with x/y, "texts": [{text, size, y, color, font}], "cx"}.
    """
    from PIL import ImageFont
    safe_x, safe_y, safe_w, safe_h = _safe_rect(w, h, safe_zones)
    body = body_size(w, h, safe_zones)
    colors = [_legible(_hex_rgb(c)) for c in brand.get("colors") or ["#FFFFFF", "#FFFFFF"]]
    if len(colors) < 2:
        colors = colors * 2
    words = str(brand["name"]).split()
    u = _unit(w, h)
    best_lines, size = [" ".join(words)], int(round(body * 2.3))
    for n in (1, 2):
        lines = _split_lines(words, n)
        s_try = int(round(body * 2.3))
        while s_try > 8:
            font = ImageFont.truetype(str(FONTS_DIR / EMPH_FONT_FILE), s_try)
            pad = int(0.15 * s_try + 30 * u)
            if max(font.getlength(x) for x in lines) + 2 * pad <= safe_w * 0.94:
                break
            s_try -= max(1, s_try // 12)
        if n == 1 or s_try > size * 1.15:
            best_lines, size = lines, s_try
    info = render_gradient_images(best_lines, size, w, h, out_dir, "card_name",
                                  colors=colors, glitch_in=False)
    iw, ih = info["size"]
    cx = safe_x + safe_w // 2
    name_center = safe_y + int(round(safe_h * 0.40))
    info["x"] = cx - iw // 2
    info["y"] = max(safe_y, name_center - ih // 2)
    bottom = info["y"] + ih
    gap = int(body * 0.9)
    texts = []
    y = bottom + gap
    if brand.get("cta"):
        texts.append({"text": brand["cta"], "size": int(body * 1.15), "y": y,
                      "color": (255, 255, 255), "font": "Montserrat Medium"})
        y += int(body * 1.15) + gap // 2
    if brand.get("url"):
        texts.append({"text": brand["url"], "size": int(body * 0.95), "y": y,
                      "color": colors[0], "font": "Montserrat Medium"})
        y += int(body * 0.95) + gap // 3
    if brand.get("handle") and brand.get("handle") != brand.get("url"):
        texts.append({"text": brand["handle"], "size": int(body * 0.85), "y": y,
                      "color": (220, 220, 220), "font": "Montserrat"})
    return {"name": info, "texts": texts, "cx": cx, "safe": (safe_x, safe_y, safe_w, safe_h)}


def card_ass_lines(card: dict, start: float, end: float) -> list[str]:
    """Pre-formatted ASS Dialogue lines for the end card's text (appended to build_ass)."""
    out = []
    safe_x, _sy, safe_w, _sh = card["safe"]
    for i, t in enumerate(card["texts"]):
        size, text = t["size"], str(t["text"])
        # shrink to the safe width (measured with the same font file)
        font_file = "Montserrat-Medium.ttf" if "Medium" in t["font"] else "Montserrat-Regular.ttf"
        if not fonts_available():
            font_file = "DejaVuSans.ttf"
        while size > 8 and _ass_extent(text, size, font_file)[0] > safe_w * 0.92:
            size -= max(1, size // 15)
        r, g, b = t["color"]
        font = t["font"] if fonts_available() else "DejaVu Sans"
        tag = (f"{{\\an8\\pos({card['cx']},{t['y']})\\fn{font}\\fs{size}"
               f"\\1c&H{b:02X}{g:02X}{r:02X}&\\fad(250,0)}}")
        out.append(f"Dialogue: 2,{_ass_time(start + 0.15 * (i + 1))},{_ass_time(end)},"
                   f"Body,,0,0,0,,{tag}{_ass_escape(text)}\n")
    return out


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


def _ass_extent(text: str, size: int, font_file: str) -> tuple[int, int]:
    """Measured glyph extent used to keep ASS events inside a safe rectangle."""
    try:
        from PIL import ImageFont
        font = ImageFont.truetype(str(FONTS_DIR / font_file), max(1, size))
        left, top, right, bottom = font.getbbox(text)
        return max(1, right - left), max(1, bottom - top)
    except Exception:
        # Conservative fallback for hosts that use DejaVu through libass.
        return max(1, int(math.ceil(len(text) * size * .9))), max(1, size)


def _fit_ass_size(text: str, wanted: int, max_width: int, max_height: int,
                  font_file: str, outline: int) -> tuple[int, int, int]:
    """Return a readable measured font size whose outlined glyphs fit."""
    for size in range(max(1, wanted), 0, -1):
        glyph_w, glyph_h = _ass_extent(text, size, font_file)
        if glyph_w + 2 * outline <= max_width and glyph_h + 2 * outline <= max_height:
            return size, glyph_w, glyph_h
    glyph_w, glyph_h = _ass_extent(text, 1, font_file)
    return 1, glyph_w, glyph_h


def build_ass(events: dict, w: int, h: int, layout: Optional[list[dict]] = None,
              safe_zones: Optional[Mapping[str, float]] = None,
              extra_lines: Optional[list[str]] = None) -> str:
    """ASS document for body words and connector words.

    ``layout``: per-emphasis dict {"text_left", "text_top"} (frame pixels), from
    the PNG geometry, so connectors land on the big word's top-left corner.
    """
    u = _unit(w, h)
    size = body_size(w, h, safe_zones)
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
    safe_x, safe_y, safe_w, safe_h = _safe_rect(w, h, safe_zones)
    safe_right, safe_bottom = safe_x + safe_w, safe_y + safe_h
    x = safe_x + safe_w // 2
    y = safe_y + int(round(safe_h * BODY_Y))
    clip = f"\\clip({safe_x},{safe_y},{safe_right},{safe_bottom})"
    body_file = "Montserrat-Medium.ttf" if fonts_available() else "DejaVuSans.ttf"
    connector_file = "Montserrat-Regular.ttf" if fonts_available() else "DejaVuSans.ttf"
    lines = [head]
    for ev in events["body"]:
        word_size, glyph_w, glyph_h = _fit_ass_size(
            str(ev["text"]), size, safe_w, safe_h - drift, body_file, shad)
        word_y = max(safe_y + glyph_h // 2 + shad,
                     min(safe_bottom - glyph_h // 2 - shad - drift, y))
        tag = (f"{{\\an5{clip}\\fs{word_size}\\move({x},{word_y + drift},{x},{word_y},0,80)\\fad(80,0)\\blur6"
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
        conn_size, glyph_w, glyph_h = _fit_ass_size(
            str(ev["connector"]), csize, safe_w, safe_h - drift, connector_file, shad)
        cx = max(safe_x + shad, min(safe_right - glyph_w - shad, int(cx - 0.01 * w)))
        cy = max(safe_y + glyph_h + shad,
                 min(safe_bottom - shad - drift, int(cy + 0.35 * conn_size)))
        tag = (f"{{\\an1{clip}\\fs{conn_size}\\move({cx},{cy + drift},{cx},{cy},0,80)\\fad(80,0)\\blur6"
               f"\\t(0,80,\\blur0)\\4a&H70&}}")
        start = ev["connector_start"] if ev.get("connector_start") is not None else ev["start"]
        lines.append(f"Dialogue: 1,{_ass_time(start)},{_ass_time(ev['end'])},"
                     f"Connector,,0,0,0,,{tag}{_ass_escape(ev['connector'])}\n")
    lines.extend(extra_lines or [])
    return "".join(lines)


def emphasis_layout(infos: list[dict], w: int, h: int,
                    safe_zones: Optional[Mapping[str, float]] = None) -> list[dict]:
    """Frame-pixel text corner + image position for each emphasis PNG."""
    safe_x, safe_y, safe_w, safe_h = _safe_rect(w, h, safe_zones)
    safe_right, safe_bottom = safe_x + safe_w, safe_y + safe_h
    out = []
    for info in infos:
        iw, ih = info["size"]
        # Image generation reserves its padding against this same rectangle;
        # clamp nevertheless so callers with legacy image infos remain safe.
        x0 = max(safe_x, min(safe_right - iw, safe_x + (safe_w - iw) // 2))
        y0 = max(safe_y, min(safe_bottom - ih,
                             int(round(safe_y + safe_h * EMPH_Y - ih / 2))))
        l, t, _r, _b = info["text_box"]
        out.append({"x": x0, "y": y0, "text_left": x0 + l, "text_top": y0 + t})
    return out
