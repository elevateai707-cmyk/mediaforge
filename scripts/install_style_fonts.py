#!/usr/bin/env python3
"""Install the free/OFL fonts used by the MediaForge style pack (user-local).

Downloads sources from github.com/google/fonts, cuts static instances out of the
variable fonts (each instance becomes its own family, e.g. "Montserrat Light",
so libass picks it by name with no bold/italic flags) and writes everything to
~/.local/share/fonts/mediaforge-styles/, then runs fc-cache.

    python3 scripts/install_style_fonts.py   (needs fonttools; not a backend dependency)
"""

from __future__ import annotations

import subprocess
import urllib.parse
import urllib.request
from io import BytesIO
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

RAW = "https://raw.githubusercontent.com/google/fonts/main/ofl/"
DEST = Path.home() / ".local/share/fonts/mediaforge-styles"

# (family name used in plans, source path under ofl/, axis location or None)
INSTANCES = [
    ("Montserrat Light", "montserrat/Montserrat[wght].ttf", {"wght": 300}),
    ("Montserrat Medium", "montserrat/Montserrat[wght].ttf", {"wght": 500}),
    ("Montserrat SemiBold", "montserrat/Montserrat[wght].ttf", {"wght": 600}),
    ("Montserrat Bold", "montserrat/Montserrat[wght].ttf", {"wght": 700}),
    ("Montserrat ExtraBold", "montserrat/Montserrat[wght].ttf", {"wght": 800}),
    ("Montserrat Black", "montserrat/Montserrat[wght].ttf", {"wght": 900}),
    ("Montserrat Light Italic", "montserrat/Montserrat-Italic[wght].ttf", {"wght": 300}),
    ("Montserrat Bold Italic", "montserrat/Montserrat-Italic[wght].ttf", {"wght": 700}),
    ("Montserrat ExtraBold Italic", "montserrat/Montserrat-Italic[wght].ttf", {"wght": 800}),
    ("Montserrat Black Italic", "montserrat/Montserrat-Italic[wght].ttf", {"wght": 900}),
    ("Cormorant Garamond Medium", "cormorantgaramond/CormorantGaramond[wght].ttf", {"wght": 500}),
    ("Cormorant Garamond SemiBold", "cormorantgaramond/CormorantGaramond[wght].ttf", {"wght": 600}),
    ("Cormorant Garamond Bold", "cormorantgaramond/CormorantGaramond[wght].ttf", {"wght": 700}),
    ("Cinzel SemiBold", "cinzel/Cinzel[wght].ttf", {"wght": 600}),
    ("Playfair Display SemiBold", "playfairdisplay/PlayfairDisplay[wght].ttf", {"wght": 600}),
    ("Allura", "allura/Allura-Regular.ttf", None),
    ("Great Vibes", "greatvibes/GreatVibes-Regular.ttf", None),
    ("Parisienne", "parisienne/Parisienne-Regular.ttf", None),
    ("Anton", "anton/Anton-Regular.ttf", None),
    ("Archivo Black", "archivoblack/ArchivoBlack-Regular.ttf", None),
]
LICENSES = ["montserrat", "cormorantgaramond", "cinzel", "playfairdisplay", "allura", "greatvibes", "parisienne", "anton", "archivoblack"]


def fetch(path):
    with urllib.request.urlopen(RAW + urllib.parse.quote(path, safe="/"), timeout=60) as r:
        return r.read()


def rename(font, family):
    ps = family.replace(" ", "") + "-Regular"
    name = font["name"]
    for rec in list(name.names):
        if rec.nameID in (16, 17, 21, 22, 25):
            name.removeNames(nameID=rec.nameID)
    for nid, value in ((1, family), (2, "Regular"), (3, ps), (4, family), (6, ps)):
        name.setName(value, nid, 3, 1, 0x409)
        name.setName(value, nid, 1, 0, 0)
    font["OS/2"].usWeightClass = 400
    font["OS/2"].fsSelection = (font["OS/2"].fsSelection & ~0b100001) | 0b1000000  # REGULAR
    font["head"].macStyle = 0


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    cache = {}
    for family, src, loc in INSTANCES:
        out = DEST / (family.replace(" ", "") + ".ttf")
        if out.exists():
            print("have", out.name)
            continue
        if src not in cache:
            cache[src] = fetch(src)
        font = TTFont(BytesIO(cache[src]))
        if loc:
            font = instancer.instantiateVariableFont(font, loc)
        rename(font, family)
        font.save(out)
        print("wrote", out.name)
    for d in LICENSES:
        lic = DEST / f"OFL-{d}.txt"
        if not lic.exists():
            lic.write_bytes(fetch(f"{d}/OFL.txt"))
    subprocess.run(["fc-cache", "-f", str(DEST)], check=False)
    for family, _, _ in INSTANCES:
        got = subprocess.run(["fc-match", "-f", "%{family}|%{file}", family], capture_output=True, text=True).stdout
        print(f"{family:34s} -> {got}")


if __name__ == "__main__":
    main()
