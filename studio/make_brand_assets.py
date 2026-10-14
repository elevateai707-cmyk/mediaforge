#!/usr/bin/env python3
"""Generate Elevate Vision Studio's icon and wordmark (studio/assets/).

Run once after changing the palette or name; the PNG/ICO/ICNS outputs are
committed so builds need no Pillow. Palette matches MediaForge's UI
(frontend/src/index.css): warm charcoal, amber primary, teal secondary.

    python3 studio/make_brand_assets.py [--font path/to/Inter-Bold.ttf]

The default font is Inter Bold (SIL OFL 1.1), which FilmCraft ships in
assets/fonts/; studio/assets/OFL-Inter.txt carries its licence.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
OUT = HERE / "assets"
BRAND = json.loads((HERE / "brand.json").read_text())

BG = (20, 17, 14)
AMBER = (232, 163, 23)
TEAL = (78, 189, 184)
TEAL_LIGHT = (150, 226, 221)
INK_LIGHT = (243, 234, 216)
INK_DARK = (26, 18, 4)
SS = 4  # supersampling


def gradient(size: tuple[int, int], a: tuple[int, int, int], b: tuple[int, int, int]) -> Image.Image:
    """Diagonal a -> b gradient."""
    w, h = size
    g = Image.new("RGB", size)
    px = g.load()
    for y in range(h):
        for x in range(w):
            t = (x / max(1, w - 1) + y / max(1, h - 1)) / 2
            px[x, y] = tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))
    return g


def icon(size: int = 1024) -> Image.Image:
    s = size * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    r = round(s * 0.22)
    pad = round(s * 0.04)
    border = round(s * 0.022)
    # Gradient rim, then the charcoal tile inside it.
    rim = Image.new("L", (s, s), 0)
    ImageDraw.Draw(rim).rounded_rectangle((pad, pad, s - pad, s - pad), r, fill=255)
    img.paste(gradient((s, s), AMBER, TEAL), (0, 0), rim)
    ImageDraw.Draw(img).rounded_rectangle(
        (pad + border, pad + border, s - pad - border, s - pad - border), r - border, fill=BG + (255,))

    # Play triangle (teal -> amber) with a rising chevron above it: "elevate" + "video".
    mark = Image.new("L", (s, s), 0)
    d = ImageDraw.Draw(mark)
    cx, cy = s * 0.53, s * 0.60
    tri = s * 0.20
    d.polygon([(cx - tri * 0.85, cy - tri), (cx - tri * 0.85, cy + tri), (cx + tri * 1.05, cy)], fill=255)
    img.paste(gradient((s, s), TEAL_LIGHT, TEAL), (0, 0), mark)
    chev = Image.new("L", (s, s), 0)
    w = round(s * 0.055)
    top, left, right, base = s * 0.20, s * 0.30, s * 0.70, s * 0.33
    ImageDraw.Draw(chev).line([(left, base), (s * 0.5, top), (right, base)], fill=255, width=w, joint="curve")
    for x, y in ((left, base), (right, base), (s * 0.5, top)):
        ImageDraw.Draw(chev).ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=255)
    img.paste(Image.new("RGB", (s, s), AMBER), (0, 0), chev)
    return img.resize((size, size), Image.LANCZOS)


def wordmark(font_path: Path, ink: tuple[int, int, int]) -> Image.Image:
    """Mark + 'ELEVATE VISION' / 'STUDIO', transparent background."""
    h = 160 * SS
    big = ImageFont.truetype(str(font_path), round(h * 0.42))
    small = ImageFont.truetype(str(font_path), round(h * 0.20))
    line1, line2 = "ELEVATE VISION", "STUDIO  ·  " + BRAND["company"].upper()
    tmp = ImageDraw.Draw(Image.new("L", (1, 1)))
    w1 = tmp.textlength(line1, font=big)
    w2 = tmp.textlength(line2, font=small)
    mark = icon(h // SS).resize((h, h), Image.LANCZOS)
    gap = round(h * 0.18)
    width = round(h + gap + max(w1, w2) + h * 0.05)
    img = Image.new("RGBA", (width, h), (0, 0, 0, 0))
    img.paste(mark, (0, 0), mark)
    d = ImageDraw.Draw(img)
    x = h + gap
    d.text((x, h * 0.10), line1, font=big, fill=ink + (255,))
    d.text((x, h * 0.62), line2, font=small, fill=TEAL + (255,))
    return img.resize((width // SS, h // SS), Image.LANCZOS)


def svg_icon() -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">
  <defs>
    <linearGradient id="rim" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#e8a317"/><stop offset="1" stop-color="#4ebdb8"/></linearGradient>
    <linearGradient id="play" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#96e2dd"/><stop offset="1" stop-color="#4ebdb8"/></linearGradient>
  </defs>
  <title>{BRAND['product']}</title>
  <rect x="41" y="41" width="942" height="942" rx="225" fill="url(#rim)"/>
  <rect x="63.5" y="63.5" width="897" height="897" rx="202.5" fill="#14110e"/>
  <polygon points="368,410 368,820 758,614" fill="url(#play)"/>
  <polyline points="307,338 512,205 717,338" fill="none" stroke="#e8a317" stroke-width="56"
            stroke-linecap="round" stroke-linejoin="round"/>
</svg>
"""


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--font", type=Path, default=HERE / "assets" / "Inter-Bold.ttf")
    a = p.parse_args()
    OUT.mkdir(exist_ok=True)
    big = icon(1024)
    big.save(OUT / "icon-1024.png", optimize=True)
    for size in (16, 24, 32, 48, 64, 128, 256, 512):
        big.resize((size, size), Image.LANCZOS).save(OUT / f"icon-{size}.png", optimize=True)
    big.save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    big.save(OUT / "icon.icns")
    (OUT / "icon.svg").write_text(svg_icon(), encoding="utf-8")
    wordmark(a.font, INK_LIGHT).save(OUT / "wordmark-light.png", optimize=True)  # for dark UI
    wordmark(a.font, INK_DARK).save(OUT / "wordmark-dark.png", optimize=True)    # for light UI
    print("wrote", ", ".join(sorted(p.name for p in OUT.iterdir())))


if __name__ == "__main__":
    main()
