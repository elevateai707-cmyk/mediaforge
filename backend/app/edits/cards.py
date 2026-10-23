"""Motion-graphic "cards": locally rendered news-style visuals that replace a story beat's
drone shot (the beat's voice slot sets the card's duration, so narration timing never moves).

Kinds: headline, stat, alert, terminal, timeline. Frames are drawn with Pillow on the CPU
(dark background with a drifting grid, scanlines and grain, Montserrat / JetBrains Mono text,
cyan -> blue -> magenta accents) and piped to ffmpeg as an h264 clip. Nothing external is used:
icons are drawn shapes, sources are text credits, terminal lines are generic fake log lines.

Layout keeps 9:16 social safe zones and leaves the band around mid-frame (44%-58% of the frame
height) free for the kinetic captions.
"""
from __future__ import annotations

import math
import random
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Mapping, Optional

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont

from ..proc import tool_env
from . import kinetic

KINDS = ("headline", "stat", "alert", "terminal", "timeline", "ambient")
FONTS = kinetic.FONTS_DIR
CYAN, BLUE, MAGENTA = (25, 198, 230), (58, 108, 244), (192, 43, 216)
RED, AMBER, GREEN = (226, 36, 52), (255, 176, 0), (60, 214, 120)
WHITE, GREY = (244, 247, 252), (160, 172, 192)
BG_TOP, BG_BOTTOM = (6, 9, 20), (11, 16, 34)
CAPTION_BAND = (0.44, 0.58)          # fractions of frame height kept clear for captions
MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


class CardError(ValueError):
    """Invalid card specification (HTTP 422)."""


# ---------------------------------------------------------------------------
# spec parsing / validation
# ---------------------------------------------------------------------------

def _split_list(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\s*[·•]\s*|\s+\|\s+", text or "") if p.strip()]


def validate_card(raw: object) -> dict:
    """Normalise a card spec ({kind, text, sub, ...}); raises CardError.

    Derived fields: ``items`` (timeline steps, alert checklist rows or terminal checks),
    ``counter`` (terminal milestones) and ``date`` (headline tag, default today).
    """
    if not isinstance(raw, dict):
        raise CardError("card must be an object")
    kind = str(raw.get("kind") or "").lower()
    if kind not in KINDS:
        raise CardError(f"card kind must be one of {', '.join(KINDS)}")
    text = " ".join(str(raw.get("text") or "").split())
    sub = " ".join(str(raw.get("sub") or "").split())
    if not text and kind != "ambient":
        raise CardError(f"a {kind} card needs text")
    if len(text) > 400 or len(sub) > 400:
        raise CardError("card text and sub are limited to 400 characters")
    card: dict = {"kind": kind, "text": text, "sub": sub}
    if kind == "ambient":
        pass
    elif kind == "headline":
        card["date"] = str(raw.get("date") or datetime.now().strftime("%b %d, %Y")).upper()[:24]
    elif kind == "timeline":
        items = [str(i) for i in raw["items"]] if isinstance(raw.get("items"), list) else _split_list(text)
        if not 2 <= len(items) <= 5:
            raise CardError("a timeline card needs 2-5 steps separated by ' · '")
        card["items"] = [i[:90] for i in items]
    elif kind == "alert":
        items = [str(i) for i in raw["items"]] if isinstance(raw.get("items"), list) else _split_list(sub)
        card["items"] = [i[:60] for i in items[:5]] if len(items) >= 3 else []   # 3+ parts = checklist
    elif kind == "terminal":
        if isinstance(raw.get("counter"), list):
            counter = [str(c) for c in raw["counter"]]
        elif re.match(r"(?i)^counter\b", sub):
            counter = [c.strip() for c in re.split(r"\s*(?:→|->)\s*", re.sub(r"(?i)^counter\s*", "", sub)) if c.strip()]
        else:
            counter = []
        if counter and not 2 <= len(counter) <= 6:
            raise CardError("a terminal counter needs 2-6 milestones separated by '→'")
        card["counter"] = [c[:16] for c in counter]
        checks = [c.strip() for c in re.split(r"[✔✓]", sub) if c.strip()] if (not counter and re.search(r"[✔✓]", sub)) else []
        card["items"] = checks[:5]
    return card


def validate_beat_visuals(visuals: Optional[list], n_beats: int) -> list[Optional[dict]]:
    """[null | {"type": "card", "card": {...}}] aligned to beats -> normalised list of length n."""
    if visuals is None:
        return [None] * n_beats
    if len(visuals) > n_beats:
        raise CardError(f"beat_visuals has {len(visuals)} entries but the script has only {n_beats} beats")
    out: list[Optional[dict]] = []
    for i, v in enumerate(visuals, 1):
        if v is None:
            out.append(None)
            continue
        if not isinstance(v, dict) or v.get("type") != "card":
            raise CardError(f"beat_visuals[{i}] must be null or {{\"type\": \"card\", \"card\": {{...}}}}")
        out.append({"type": "card", "card": validate_card(v.get("card"))})
    return out + [None] * (n_beats - len(out))


# ---------------------------------------------------------------------------
# drawing helpers
# ---------------------------------------------------------------------------

def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _ease(x: float) -> float:
    x = _clamp(x)
    return 1 - (1 - x) ** 3


def _lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _grad_color(x: float, colors=(CYAN, BLUE, MAGENTA)) -> tuple[int, int, int]:
    x = _clamp(x) * (len(colors) - 1)
    i = min(int(x), len(colors) - 2)
    return _lerp(colors[i], colors[i + 1], x - i)


class Canvas:
    """A frame plus cached fonts / text masks."""

    def __init__(self, w: int, h: int):
        self.w, self.h = w, h
        self.img = Image.new("RGB", (w, h))
        self._fonts: dict = {}
        self._masks: dict = {}
        self._grads: dict = {}

    def font(self, name: str, size: int):
        key = (name, size)
        if key not in self._fonts:
            self._fonts[key] = ImageFont.truetype(str(FONTS / name), max(6, size))
        return self._fonts[key]

    def mask(self, text: str, font) -> Image.Image:
        key = (text, id(font))
        m = self._masks.get(key)
        if m is None:
            l, t, r, b = font.getbbox(text)
            m = Image.new("L", (max(1, r + 4), max(1, b + 4)), 0)
            ImageDraw.Draw(m).text((0, 0), text, font=font, fill=255)
            if len(self._masks) > 600:
                self._masks.clear()
            self._masks[key] = m
        return m

    @staticmethod
    def _scaled(mask: Image.Image, alpha: float) -> Image.Image:
        if alpha >= 0.999:
            return mask
        return mask.point([int(i * alpha) for i in range(256)])

    def text(self, text: str, font, xy: tuple[float, float], fill=WHITE, alpha: float = 1.0,
             anchor: str = "l", gradient: Optional[tuple] = None) -> tuple[int, int]:
        """Draw text with its top-left (anchor l), centre (c) or right edge (r) at x. Returns its size."""
        if not text or alpha <= 0.01:
            return (0, 0)
        m = self.mask(text, font)
        l, t, r, b = font.getbbox(text)
        tw, th = r - l, b
        x, y = xy
        x0 = int(x - (tw / 2 if anchor == "c" else tw if anchor == "r" else 0)) - l
        y0 = int(y)
        am = self._scaled(m, alpha)
        if gradient:
            key = (m.size, gradient)
            g = self._grads.get(key)
            if g is None:
                strip = Image.new("RGB", (m.size[0], 1))
                px = strip.load()
                for i in range(m.size[0]):
                    px[i, 0] = _grad_color(i / max(1, m.size[0] - 1), gradient)
                g = strip.resize(m.size)
                self._grads[key] = g
            self.img.paste(g, (x0, y0), am)
        else:
            self.img.paste(Image.new("RGB", m.size, fill), (x0, y0), am)
        return tw, th

    def layer(self) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        im = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        return im, ImageDraw.Draw(im)

    def overlay(self, im: Image.Image, box: Optional[tuple[int, int, int, int]] = None) -> None:
        if box:
            crop = im.crop(box)
            self.img.paste(crop, box[:2], crop)
        else:
            self.img.paste(im, (0, 0), im)


def wrap(text: str, font, max_w: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if cur and font.getlength(trial) > max_w:
            lines.append(cur)
            cur = w
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def fit_block(cv: Canvas, text: str, fontname: str, max_w: int, max_h: int, start: int, min_size: int = 18,
              max_lines: int = 6, spacing: float = 1.12) -> tuple[list[str], int]:
    """Largest size (<= start) whose wrapped lines fit max_w x max_h."""
    size = start
    while size > min_size:
        font = cv.font(fontname, size)
        lines = wrap(text, font, max_w)
        if len(lines) <= max_lines and len(lines) * size * spacing <= max_h and \
                all(font.getlength(l) <= max_w for l in lines):
            return lines, size
        size -= max(1, size // 14)
    return wrap(text, cv.font(fontname, min_size), max_w), min_size


# ---------------------------------------------------------------------------
# renderer
# ---------------------------------------------------------------------------

class CardRenderer:
    def __init__(self, spec: dict, w: int, h: int, duration: float, fps: int = 30,
                 safe_zones: Optional[Mapping[str, float]] = None, seed: int = 0, reveal_delay: float = 0.0,
                 compact: bool = False):
        self.spec, self.w, self.h, self.T, self.fps = spec, w, h, max(0.5, duration), fps
        self.u = min(w, h) / 1080.0
        self.sx, self.sy, self.sw, self.sh = kinetic._safe_rect(w, h, safe_zones)
        self.delay = max(0.0, reveal_delay)
        self.rng_seed = seed
        self.cv = Canvas(w, h)
        self._build_background()
        # vertical bands (pixel y): content above the caption band and below it
        self.top0 = self.sy + int(0.012 * h)
        self.top1 = int(CAPTION_BAND[0] * h) - int(0.01 * h)
        self.low0 = int(CAPTION_BAND[1] * h) + int(0.02 * h)
        self.low1 = self.sy + self.sh - int(0.02 * h)
        if h < w:                                        # landscape: use the full height more evenly
            self.top1 = int(0.45 * h)
            self.low0 = int(0.60 * h)
        self.compact = bool(compact) and h >= w
        if self.compact:
            # green-screen backgrounds: the presenter covers the middle and lower frame, so everything
            # lives in the top ~37% of the frame (headline block above, detail lines just below)
            self.top0 = self.sy + int(0.004 * h)
            self.top1 = int(0.285 * h)
            self.low0 = int(0.295 * h)
            self.low1 = int(0.375 * h)
        # Symmetric horizontal frame: at least 7% of the width on both sides in every orientation
        # (8% here leaves room for the light camera drift), centred blocks are centred on the frame.
        self.fx = int(0.08 * w)
        self.fw = w - 2 * self.fx
        # Short beats: speed the reveal up so the card is complete before its voice slot ends.
        kind = spec["kind"]
        n_items = len(spec.get("items") or [])
        design = {"headline": 4.2, "stat": 1.8, "alert": 1.6 + 0.5 * n_items,
                  "timeline": 1.0 + 0.8 * n_items, "terminal": 0.0, "ambient": 0.0}[kind]
        avail = self.T - self.delay - 0.5
        self.speed = 1.0 if design <= 0 else _clamp(avail / design, 0.4, 1.0)

    # -- background --------------------------------------------------------------
    def _build_background(self) -> None:
        w, h, u = self.w, self.h, self.u
        ys = np.linspace(0, 1, h)[:, None]
        top, bot = np.array(BG_TOP, dtype=np.float32), np.array(BG_BOTTOM, dtype=np.float32)
        base = (top[None, None, :] * (1 - ys[:, :, None]) + bot[None, None, :] * ys[:, :, None])
        base = np.repeat(base, w, axis=1)
        # soft radial glow behind the upper content
        yy, xx = np.mgrid[0:h, 0:w]
        glow = np.exp(-(((xx - w * 0.5) / (w * 0.7)) ** 2 + ((yy - h * 0.28) / (h * 0.28)) ** 2))
        base[:, :, 2] += 26 * glow
        base[:, :, 1] += 10 * glow
        self.bg = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))
        self.spacing = max(24, int(90 * u))
        grid = Image.new("L", (w, h + self.spacing), 0)
        d = ImageDraw.Draw(grid)
        for x in range(0, w, self.spacing):
            d.line([(x, 0), (x, h + self.spacing)], fill=34, width=max(1, int(2 * u)))
        for y in range(0, h + self.spacing, self.spacing):
            d.line([(0, y), (w, y)], fill=34, width=max(1, int(2 * u)))
        self.grid = grid
        self.grid_fill = Image.new("RGB", (w, h), (40, 110, 190))
        scan = np.ones((h, 1, 3), dtype=np.float32)
        scan[::4] = 0.86
        self.scan = Image.fromarray((np.repeat(scan, w, axis=1) * 255).astype(np.uint8))
        rng = np.random.default_rng(self.rng_seed + 7)
        noise = rng.integers(0, 14, size=(h + 64, w + 64), dtype=np.uint8)
        self.noise = Image.fromarray(noise, "L").convert("RGB")
        self.noise_rng = random.Random(self.rng_seed + 11)

    def background(self, t: float) -> Image.Image:
        frame = self.bg.copy()
        off = int(t * self.spacing * 0.55) % self.spacing
        mask = self.grid.crop((0, off, self.w, off + self.h))
        frame.paste(self.grid_fill, (0, 0), mask)
        frame = ImageChops.multiply(frame, self.scan)
        nx, ny = self.noise_rng.randint(0, 64), self.noise_rng.randint(0, 64)
        return ImageChops.add(frame, self.noise.crop((nx, ny, nx + self.w, ny + self.h)))

    # -- timing helpers -------------------------------------------------------------
    def clock(self, t: float) -> float:
        return max(0.0, t - self.delay)

    def drift(self, t: float) -> tuple[int, int]:
        return (int(6 * self.u * math.sin(t * 0.7)), int(8 * self.u * math.sin(t * 0.5 + 1.0)))

    # -- frames ------------------------------------------------------------------------
    def frame(self, t: float) -> Image.Image:
        cv = self.cv
        cv.img = self.background(t)
        c = self.clock(t) / self.speed
        if t >= self.delay:
            getattr(self, "_" + self.spec["kind"])(cv, c, t)
        return cv.img

    def frame_bytes(self, t: float) -> bytes:
        return self.frame(t).tobytes()

    # -- shared pieces -----------------------------------------------------------------
    def _source_lines(self, cv: Canvas, parts: list[str], y: int, t: float, start: float, size: int) -> int:
        """Credit / detail lines ('Source: X' gets a cyan label); returns the next free y."""
        dx, dy = self.drift(t)
        for i, part in enumerate(parts):
            a = _ease((t - start - i * 0.3) / 0.4)
            if a <= 0:
                continue
            font = cv.font("Montserrat-Medium.ttf", size)
            m = re.match(r"(?i)^(source|sources)\s*:\s*(.*)$", part)
            yy = y + int((1 - a) * 14 * self.u) + dy
            if m:
                lab = cv.font("Montserrat-Black.ttf", int(size * 0.8))
                lw, _ = cv.text("SOURCE", lab, (self.fx + dx, yy + 3), CYAN, a)
                cv.text(m.group(2), font, (self.fx + dx + lw + 18 * self.u, yy), WHITE, a)
            else:
                cv.text(part, font, (self.fx + dx, yy), GREY if i else WHITE, a)
            y += int(size * 1.5)
        return y

    # -- ambient: just the animated backdrop (green-screen backgrounds) with an optional title -----------
    def _ambient(self, cv: Canvas, c: float, t: float) -> None:
        u = self.u
        dx, dy = self.drift(t)
        im, d = cv.layer()
        bar_w = int(self.fw * (0.35 + 0.1 * math.sin(t * 0.8)))
        for i in range(0, bar_w, 4):
            d.rectangle([self.fx + i, self.top0 + int(6 * u), self.fx + i + 4, self.top0 + int(14 * u)],
                        fill=(*_grad_color(i / max(1, self.fw * 0.45)), 235))
        cv.overlay(im)
        if self.spec.get("text"):
            lines, size = fit_block(cv, self.spec["text"], "Montserrat-Black.ttf", self.fw, int(0.16 * self.h),
                                    int(72 * u), int(32 * u), 3)
            y = self.top0 + int(40 * u)
            for ln in lines:
                cv.text(ln, cv.font("Montserrat-Black.ttf", size), (self.fx + dx, y + dy), WHITE, _ease(c / 0.5))
                y += int(size * 1.12)

    # -- headline -----------------------------------------------------------------------
    def _headline(self, cv: Canvas, c: float, t: float) -> None:
        u, spec = self.u, self.spec
        dx, dy = self.drift(t)
        pill_h = int(62 * u)
        im, d = cv.layer()
        d.rounded_rectangle([self.fx, self.top0, self.fx + int(620 * u), self.top0 + pill_h], radius=pill_h // 2,
                            fill=(*RED, 235))
        blink = 255 if int(c * 2) % 2 == 0 else 80
        d.ellipse([self.fx + int(22 * u), self.top0 + int(20 * u), self.fx + int(44 * u), self.top0 + int(42 * u)],
                  fill=(255, 255, 255, blink))
        cv.overlay(im)
        tag = f"BREAKING · {spec.get('date', '')}"
        cv.text(tag, cv.font("Montserrat-Black.ttf", int(30 * u)), (self.fx + int(60 * u), self.top0 + int(14 * u)),
                WHITE, _ease(c / 0.3))
        y0 = self.top0 + pill_h + int(0.025 * self.h)
        lines, size = fit_block(cv, spec["text"], "Montserrat-Black.ttf", self.fw, self.top1 - y0 - int(40 * u),
                                int(96 * u), int(40 * u), 6)
        font = cv.font("Montserrat-Black.ttf", size)
        y = y0
        t0 = 0.35
        last_done = t0
        for i, line in enumerate(lines):
            dur = min(0.9, 0.045 * len(line))
            p = _clamp((c - t0) / dur)
            if p > 0:
                shown = line[: max(1, int(len(line) * p))]
                cv.text(shown, font, (self.fx + dx, y + dy), WHITE)
                if p < 1 or (i == len(lines) - 1 and int(c * 3) % 2 == 0):
                    cw = font.getlength(shown)
                    im, d = cv.layer()
                    d.rectangle([self.fx + dx + cw + 6, y + dy + int(size * 0.12), self.fx + dx + cw + 6 + int(size * 0.09),
                                 y + dy + int(size * 0.95)], fill=(*CYAN, 255))
                    cv.overlay(im)
            t0 += dur + 0.12
            last_done = t0
            y += int(size * 1.12)
        bar_p = _ease((c - last_done) / 0.6)
        if bar_p > 0:
            im, d = cv.layer()
            bw = int(self.fw * 0.62 * bar_p)
            for i in range(0, bw, 4):
                d.rectangle([self.fx + i, y + int(14 * u), self.fx + i + 4, y + int(14 * u) + int(10 * u)],
                            fill=(*_grad_color(i / max(1, self.fw * 0.62)), 255))
            cv.overlay(im)
        parts = _split_list(spec.get("sub") or "")
        self._source_lines(cv, parts, self.low0, c, last_done + 0.2, int(36 * u))

    # -- stat -----------------------------------------------------------------------------
    def _count_text(self, text: str, p: float) -> str:
        def repl(m: re.Match) -> str:
            raw = m.group(0)
            val = float(raw.replace(",", ""))
            cur = val * _ease(p)
            if "." in raw:
                return f"{cur:.{len(raw.split('.')[1])}f}"
            s = f"{int(round(cur)):,}" if "," in raw else str(int(round(cur)))
            return s
        return _NUM.sub(repl, text)

    def _stat(self, cv: Canvas, c: float, t: float) -> None:
        u, spec = self.u, self.spec
        dx, dy = self.drift(t)
        text = spec["text"]
        lines, size = fit_block(cv, text, "Montserrat-Black.ttf", int(self.fw * 0.96), int((self.top1 - self.top0) * 0.8),
                                int(300 * u), int(60 * u), 3)
        font = cv.font("Montserrat-Black.ttf", size)
        total_h = int(len(lines) * size * 1.08)
        y = self.top0 + int(0.05 * self.h) + max(0, (self.top1 - self.top0 - int(0.05 * self.h) - total_h) // 3)
        numeric = bool(_NUM.search(text))
        glitch = (not numeric) and c < 0.5
        for line in lines:
            shown = self._count_text(line, c / 1.3) if numeric else line
            fw = font.getlength(line)
            x = self.fx + (self.fw - fw) / 2 + dx
            if glitch:
                k = 1 - c / 0.5
                off = int(24 * u * k) + 2
                cv.text(shown, font, (x + off, y + dy), (255, 40, 90), 0.8 * _ease(c / 0.1))
                cv.text(shown, font, (x - off, y + dy), (0, 230, 255), 0.8 * _ease(c / 0.1))
                if int(c * 40) % 2:
                    cv.text(shown, font, (x + off * 2, y + dy + int(size * 0.3)), WHITE, 0.6)
            cv.text(shown, font, (x, y + dy), gradient=(CYAN, BLUE, MAGENTA), alpha=_ease(c / 0.25))
            y += int(size * 1.08)
        dots_bottom = y
        m = re.search(r"(\d+)\s+in\s+(\d+)", text)
        if m and 2 <= int(m.group(2)) <= 12:
            n, k = int(m.group(1)), int(m.group(2))
            r = int(0.019 * self.w)
            gap = int(r * 0.9)
            total = k * 2 * r + (k - 1) * gap
            x0 = self.fx + (self.fw - total) // 2 + dx
            im, d = cv.layer()
            for i in range(k):
                filled = c > 0.3 + i * 0.12 and i < n
                col = (*CYAN, 255) if filled else (60, 76, 104, 255)
                cx = x0 + i * (2 * r + gap) + r
                d.ellipse([cx - r, y + int(20 * u) + dy, cx + r, y + int(20 * u) + 2 * r + dy], fill=col)
            cv.overlay(im)
            dots_bottom = y + int(20 * u) + 2 * r
        label, rest = (_split_list(spec.get("sub") or "") or [""])[0], _split_list(spec.get("sub") or "")[1:]
        if label:
            lfont_lines, ls = fit_block(cv, label, "Montserrat-Medium.ttf", self.fw, self.low1 - self.low0 - int(120 * u),
                                        int(54 * u), int(26 * u), 3)
            a = _ease((c - 0.7) / 0.5)
            yy = max(self.low0, dots_bottom + int(18 * u)) if self.compact else self.low0
            for ln in lfont_lines:
                cv.text(ln, cv.font("Montserrat-Medium.ttf", ls), (self.fx + dx, yy + dy + int((1 - a) * 14 * u)), WHITE, a)
                yy += int(ls * 1.25)
            self._source_lines(cv, rest, yy + int(10 * u), c, 1.4, int(34 * u))

    # -- alert -------------------------------------------------------------------------------
    def _hazard_bar(self, cv: Canvas, y: int, hgt: int, t: float) -> None:
        im, d = cv.layer()
        x0, x1 = self.fx, self.fx + self.fw
        d.rectangle([x0, y, x1, y + hgt], fill=(18, 14, 8, 255))
        step = int(hgt * 1.6)
        off = int(t * 90 * self.u) % (step * 2)
        for x in range(x0 - hgt - step * 2, x1 + hgt, step * 2):
            xs = x + off
            d.polygon([(xs, y + hgt), (xs + step, y + hgt), (xs + step + hgt, y), (xs + hgt, y)], fill=(*AMBER, 255))
        im2 = im.crop((x0, y, x1, y + hgt))
        cv.img.paste(im2, (x0, y), im2)

    def _alert(self, cv: Canvas, c: float, t: float) -> None:
        u, spec = self.u, self.spec
        dx, dy = self.drift(t)
        bar_h = int((0.012 if self.compact else 0.022) * self.h)
        self._hazard_bar(cv, self.top0, bar_h, t)
        # warning triangle drawn from shapes, pulsing
        pulse = 1 + 0.05 * math.sin(c * 6)
        tri_h = 0 if self.compact else int(0.10 * self.h * pulse)
        cx = self.fx + self.fw // 2 + dx
        ty = self.top0 + bar_h + int(0.02 * self.h) + dy
        im, d = cv.layer()
        half = int(tri_h * 0.62)
        if self.compact:
            ty = self.top0 + bar_h - int(0.02 * self.h)
        flash = 0.78 + 0.22 * (1 if int(c * 2.4) % 2 == 0 else 0)
        d.polygon([(cx, ty), (cx - half, ty + tri_h), (cx + half, ty + tri_h)], fill=(*AMBER, int(255 * flash)))
        inset = int(tri_h * 0.12)
        d.polygon([(cx, ty + inset * 2), (cx - half + inset * 2, ty + tri_h - inset), (cx + half - inset * 2, ty + tri_h - inset)],
                  fill=(20, 14, 6, 255))
        bw = int(tri_h * 0.10)
        d.rectangle([cx - bw, ty + int(tri_h * 0.36), cx + bw, ty + int(tri_h * 0.68)], fill=(*AMBER, 255))
        d.ellipse([cx - bw, ty + int(tri_h * 0.74), cx + bw, ty + int(tri_h * 0.74) + 2 * bw], fill=(*AMBER, 255))
        cv.overlay(im)
        # flashing red panel with the message
        py0 = ty + tri_h + int(0.025 * self.h)
        py1 = self.top1 - bar_h - int(0.01 * self.h)
        lines, size = fit_block(cv, spec["text"], "Montserrat-Black.ttf", self.fw - int(60 * u), py1 - py0 - int(50 * u),
                                int(100 * u), int(36 * u), 4)
        im, d = cv.layer()
        a = 215 if int(c * 2.4) % 2 == 0 else 150
        d.rounded_rectangle([self.fx, py0, self.fx + self.fw, py1], radius=int(26 * u), fill=(*RED, a))
        d.rounded_rectangle([self.fx, py0, self.fx + self.fw, py1], radius=int(26 * u), outline=(*AMBER, 255), width=max(2, int(5 * u)))
        cv.overlay(im, (self.fx - 2, py0 - 2, self.fx + self.fw + 2, py1 + 2))
        font = cv.font("Montserrat-Black.ttf", size)
        block = int(len(lines) * size * 1.1)
        y = py0 + (py1 - py0 - block) // 2
        for ln in lines:
            cv.text(ln, font, (self.fx + self.fw // 2 + dx, y + dy), WHITE, _ease(c / 0.25), anchor="c")
            y += int(size * 1.1)
        if not self.compact:
            self._hazard_bar(cv, self.top1 - bar_h, bar_h, -t)
        items = spec.get("items") or []
        yy = self.low0
        if items:
            fsz = int(46 * u)
            for i, item in enumerate(items):
                p = _ease((c - 0.5 - i * 0.45) / 0.35)
                if p <= 0:
                    continue
                im, d = cv.layer()
                bx, by = self.fx + dx, yy + dy + int(4 * u)
                s = int(fsz * 0.9)
                d.rounded_rectangle([bx, by, bx + s, by + s], radius=int(8 * u), outline=(*AMBER, int(255 * p)), width=max(2, int(4 * u)))
                d.line([(bx + s * 0.2, by + s * 0.52), (bx + s * 0.42, by + s * 0.76), (bx + s * 0.82, by + s * 0.24)],
                       fill=(*GREEN, int(255 * p)), width=max(3, int(7 * u)))
                cv.overlay(im, (bx - 4, by - 4, bx + s + 8, by + s + 8))
                cv.text(item, cv.font("Montserrat-Medium.ttf", fsz), (bx + s + int(26 * u), yy + dy), WHITE, p)
                yy += int(fsz * 1.55)
        elif spec.get("sub"):
            lines2, ls = fit_block(cv, spec["sub"], "Montserrat-Medium.ttf", self.fw, self.low1 - self.low0, int(50 * u), int(24 * u), 4)
            a = _ease((c - 0.6) / 0.4)
            for ln in lines2:
                cv.text(ln, cv.font("Montserrat-Medium.ttf", ls), (self.fx + dx, yy + dy), WHITE, a)
                yy += int(ls * 1.3)

    # -- terminal -------------------------------------------------------------------------------
    def _log_line(self, k: int) -> tuple[str, tuple]:
        r = random.Random(self.rng_seed * 100003 + k)
        method = r.choice(["GET", "GET", "POST", "POST", "HEAD"])
        path = r.choice(["/index.php", "/wp-login.php", "/xmlrpc.php", "/admin/", "/cgi-bin/", "/wp-admin/admin-ajax.php",
                         "/api/v1/status", "/login.php", "/upload.php"])
        status = r.choice([200, 200, 200, 403, 404, 500, 302])
        ip = f"{r.choice([203, 198])}.{r.choice([0, 51])}.{r.choice([100, 113])}.{r.randint(2, 250)}"
        secs = 7 + k
        line = f"[22:41:{secs % 60:02d}] {ip:<15} {method:<4} {path:<26} {status}"
        col = {200: GREEN, 302: GREEN, 403: AMBER, 404: GREY, 500: (255, 90, 100)}[status]
        return line, col

    def _terminal(self, cv: Canvas, c: float, t: float) -> None:
        u, spec = self.u, self.spec
        dx, dy = self.drift(t)
        win = [self.fx, self.top0, self.fx + self.fw, self.top1]
        im, d = cv.layer()
        d.rounded_rectangle(win, radius=int(22 * u), fill=(6, 10, 16, 235), outline=(*CYAN, 160), width=max(2, int(3 * u)))
        bar = int(54 * u)
        d.rounded_rectangle([win[0], win[1], win[2], win[1] + bar], radius=int(22 * u), fill=(18, 28, 44, 255))
        for i, col in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
            cx = win[0] + int((34 + i * 36) * u)
            d.ellipse([cx - int(10 * u), win[1] + bar // 2 - int(10 * u), cx + int(10 * u), win[1] + bar // 2 + int(10 * u)], fill=(*col, 255))
        cv.overlay(im, (win[0] - 2, win[1] - 2, win[2] + 2, win[3] + 2))
        mono = "JetBrainsMono-Regular.ttf"
        cv.text("monitor — attempts.log", cv.font(mono, int(24 * u)), (win[0] + int(150 * u), win[1] + int(12 * u)), GREY)
        fsz = int(26 * u)
        mfont = cv.font(mono, fsz)
        line_h = int(fsz * 1.45)
        rows = max(3, (win[3] - win[1] - bar - int(24 * u)) // line_h)
        checks = spec.get("items") or []
        n_logs = int(c * (5 + 14 * (c / self.T) ** 1.5) + c * 4)
        if checks:
            n_logs = min(n_logs, rows - len(checks) - 1 if rows > len(checks) + 2 else rows)
        first = max(0, n_logs - rows)
        y = win[1] + bar + int(14 * u)
        max_chars = int((self.fw - int(40 * u)) / (fsz * 0.6))
        for k in range(first, n_logs):
            line, col = self._log_line(k)
            cv.text(line[:max_chars], mfont, (win[0] + int(22 * u), y), col)
            y += line_h
        if int(c * 2) % 2 == 0 and n_logs < rows * 4:
            cv.text("█", mfont, (win[0] + int(22 * u), y), CYAN)
        low = self.low0
        counter = spec.get("counter") or []
        if counter:
            label = spec["text"].upper()
            cv.text(label, cv.font("Montserrat-Black.ttf", int(38 * u)), (self.fx + dx, low + dy), CYAN, _ease(c / 0.4))
            nums = [int(re.sub(r"\D", "", m) or 0) for m in counter]
            seg = max(0.4, (self.T - self.delay - 0.4) / max(1, len(nums) - 1))
            prog = _clamp(c / (seg * (len(nums) - 1)), 0, 1) * (len(nums) - 1)
            i = min(int(prog), len(nums) - 2)
            f = _ease((prog - i) / 0.75)
            lo, hi = max(1, nums[i]), max(1, nums[i + 1])
            val = int(math.exp(math.log(lo) + (math.log(hi) - math.log(lo)) * f))
            done = prog >= len(nums) - 1 - 1e-6
            shown = counter[-1] if done else f"{val:,}"
            lines, size = fit_block(cv, shown, "Montserrat-Black.ttf", self.fw,
                                    min(int(0.16 * self.h), max(40, self.low1 - low - int(70 * u))) if self.compact else int(0.16 * self.h),
                                    int(190 * u), 36 if self.compact else 60, 1)
            cv.text(shown, cv.font("Montserrat-Black.ttf", size), (self.fx + dx, low + int(54 * u) + dy), gradient=(CYAN, BLUE, MAGENTA))
            cv.text("exploit attempts logged" if not spec.get("sub") else "attempts logged", cv.font("Montserrat-Medium.ttf", int(34 * u)),
                    (self.fx + dx, low + int(54 * u) + int(size * 1.15) + dy), GREY, _ease((c - 0.6) / 0.5))
        elif checks:
            yy = low
            cv.text(spec["text"].upper(), cv.font("Montserrat-Black.ttf", int(38 * u)), (self.fx + dx, yy + dy), CYAN, _ease(c / 0.4))
            yy += int(70 * u)
            for i, item in enumerate(checks):
                p = _ease((c - 0.8 - i * 0.55) / 0.35)
                if p <= 0:
                    continue
                im, d = cv.layer()
                bx, by, s = self.fx + dx, yy + dy + int(4 * u), int(40 * u)
                d.line([(bx + s * 0.1, by + s * 0.55), (bx + s * 0.4, by + s * 0.85), (bx + s * 0.95, by + s * 0.15)],
                       fill=(*GREEN, int(255 * p)), width=max(4, int(8 * u)))
                cv.overlay(im, (bx - 4, by - 4, bx + s + 8, by + s + 8))
                cv.text(item.upper(), cv.font("JetBrainsMono-Bold.ttf", int(40 * u)), (bx + s + int(26 * u), yy + dy), WHITE, p)
                cv.text("OK", cv.font("JetBrainsMono-Bold.ttf", int(34 * u)), (self.fx + self.fw - int(30 * u), yy + dy + 4), GREEN, p, anchor="r")
                yy += int(64 * u)
        else:
            cv.text(spec["text"], cv.font("Montserrat-Black.ttf", int(56 * u)), (self.fx + dx, low + dy), WHITE, _ease(c / 0.4))

    # -- timeline -----------------------------------------------------------------------------------
    @staticmethod
    def _split_step(item: str) -> tuple[str, str]:
        if ":" in item:
            a, b = item.split(":", 1)
            return a.strip(), b.strip()
        words = item.split()
        if len(words) >= 3 and words[0].lower()[:3] in MONTHS and words[1].isdigit():
            return " ".join(words[:2]), " ".join(words[2:])
        return "", item

    def _timeline(self, cv: Canvas, c: float, t: float) -> None:
        u, spec = self.u, self.spec
        dx, dy = self.drift(t)
        items = spec["items"]
        n = len(items)
        slots = [0.17, 0.27, 0.37, 0.62, 0.72]
        order = {2: [0.19, 0.32], 3: slots[:3], 4: slots[:3] + [0.62], 5: slots}[n]
        if self.compact:
            order = [0.15 + i * (0.335 - 0.15) / max(1, n - 1) for i in range(n)]
        if self.h < self.w:
            order = [0.22 + 0.18 * i for i in range(n)]
        ys = [int(f * self.h) for f in order]
        lx = self.fx + int(46 * u) + dx
        # title above the first step
        if spec.get("sub"):
            cv.text(spec["sub"].upper(), cv.font("Montserrat-Black.ttf", int(34 * u)), (self.fx + dx, self.top0 + dy), CYAN, _ease(c / 0.4))
        prog_end = 0.5 + n * 0.8
        line_p = _clamp((c - 0.2) / (prog_end - 0.2))
        im, d = cv.layer()
        y_a, y_b = ys[0] + int(30 * u), int(ys[0] + (ys[-1] - ys[0]) * line_p) + int(30 * u)
        d.line([(lx, y_a), (lx, y_b)], fill=(*BLUE, 255), width=max(3, int(6 * u)))
        cv.overlay(im)
        for i, item in enumerate(items):
            p = _ease((c - 0.5 - i * 0.8) / 0.45)
            if p <= 0:
                continue
            date, desc = self._split_step(item)
            y = ys[i] + dy + int((1 - p) * 20 * u)
            im, d = cv.layer()
            r = int(15 * u * (0.6 + 0.4 * p))
            ring = int(r + 12 * u * (1 - p))
            d.ellipse([lx - ring, y + int(30 * u) - ring, lx + ring, y + int(30 * u) + ring], outline=(*CYAN, int(160 * (1 - p) + 60)), width=max(2, int(3 * u)))
            d.ellipse([lx - r, y + int(30 * u) - r, lx + r, y + int(30 * u) + r], fill=(*CYAN, 255))
            cv.overlay(im)
            tx = lx + int(54 * u)
            yy = y
            if date:
                cv.text(date.upper(), cv.font("Montserrat-Black.ttf", int(40 * u)), (tx, yy), gradient=(CYAN, BLUE, MAGENTA), alpha=p)
                yy += int(52 * u)
            lines, ls = fit_block(cv, desc, "Montserrat-Medium.ttf", self.fx + self.fw - tx, int(0.085 * self.h), int(44 * u), int(24 * u), 2)
            for ln in lines:
                cv.text(ln, cv.font("Montserrat-Medium.ttf", ls), (tx, yy), WHITE, p)
                yy += int(ls * 1.2)


# ---------------------------------------------------------------------------
# video output
# ---------------------------------------------------------------------------

def render_card_still(spec: dict, w: int, h: int, t: float, duration: float = 6.0,
                      safe_zones: Optional[Mapping[str, float]] = None, seed: int = 0,
                      reveal_delay: float = 0.0, compact: bool = False) -> Image.Image:
    r = CardRenderer(spec, w, h, duration, 30, safe_zones, seed, reveal_delay, compact)
    return r.frame(t).copy()


def render_card_video(spec: dict, w: int, h: int, duration: float, out_path: Path, fps: int = 30,
                      safe_zones: Optional[Mapping[str, float]] = None, seed: int = 0,
                      reveal_delay: float = 0.0, compact: bool = False) -> Path:
    """Render ``duration`` seconds of a card to an h264 file (size w x h)."""
    w, h = w - w % 2, h - h % 2
    r = CardRenderer(spec, w, h, duration, fps, safe_zones, seed, reveal_delay, compact)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = int(round(duration * fps))
    cmd = ["ffmpeg", "-y", "-v", "error", "-nostdin", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
           "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
           "-pix_fmt", "yuv420p", str(out_path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, env=tool_env())
    try:
        for i in range(n):
            proc.stdin.write(r.frame_bytes(i / fps))
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        if proc.wait() != 0:
            raise RuntimeError("card encode failed: " + err[-300:])
    except BrokenPipeError:
        raise RuntimeError("card encode failed: " + proc.stderr.read().decode("utf-8", "replace")[-300:]) from None
    finally:
        if proc.poll() is None:
            proc.kill()
    return out_path
