#!/usr/bin/env python3
"""Turn a FilmCraft checkout into Elevate Vision Studio (studio/brand.json).

    python3 studio/rebrand.py /path/to/filmcraft-checkout [--allow-other-commit]

Why a patch kit instead of a hard fork: upstream ships fixes daily. Keeping our
changes as one script over a pinned commit means updating is "bump
brand.json's commit, re-run, build" rather than merging into a diverged fork.

What changes (user-facing surfaces only; the engine and `.fcproj` format are
untouched, so projects stay compatible both ways):

* ArtCraft marks removed, as FilmCraft's docs/brand/LICENSE-brand.txt requires
  for modified versions: the wordmark is replaced with ours and docs/brand/ is
  deleted; Discord/website links point at our URLs.
* "FilmCraft" -> product name in UI/CLI string literals and the translation
  catalogs (keys and values, so translations keep matching).
* App id, window/app name (so settings live apart from a stock FilmCraft),
  binary names (`evstudio`, `evstudio-cli`), icons, NOTICE and README.
* A plain-text credit stays in the About box: "Based on FilmCraft by the
  ArtCraft team", which the brand licence explicitly allows.

The script is strict: every targeted snippet must be found exactly as
expected, or it stops, so an upstream change can't silently produce a
half-branded build.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"
MARKER = "STUDIO_BRAND.json"
CREDIT_TOKEN = "@@UPSTREAM_CREDIT@@"  # keeps "FilmCraft by the ArtCraft team" out of step 4


class RebrandError(SystemExit):
    pass


def fail(msg: str) -> None:
    raise RebrandError(f"rebrand: {msg}")


def rust_str(s: str) -> str:
    """A Rust string literal (JSON escapes minus \\uXXXX, which Rust spells differently)."""
    return json.dumps(s, ensure_ascii=False)


def edit(path: Path, pairs: list[tuple[str, str]], count: int | None = 1) -> None:
    """Replace each `old` (which must occur, `count` times if given) with `new`."""
    text = path.read_text(encoding="utf-8")
    for old, new in pairs:
        n = text.count(old)
        if n == 0 or (count is not None and n != count):
            fail(f"{path}: expected {count or '1+'} of {old!r}, found {n} (upstream changed?)")
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")


_STRING = re.compile(r'"(?:[^"\\\n]|\\.)*"')


def rename_in_strings(path: Path, old: str, new: str) -> int:
    """Replace `old` only inside "..." literals; returns replacements made."""
    text = path.read_text(encoding="utf-8")
    n = 0

    def sub(m: re.Match) -> str:
        nonlocal n
        s = m.group(0)
        if old in s:
            n += s.count(old)
            return s.replace(old, new)
        return s

    out = _STRING.sub(sub, text)
    if n:
        path.write_text(out, encoding="utf-8")
    return n


def rebrand(tree: Path, b: dict, allow_other_commit: bool) -> dict:
    if (tree / MARKER).exists():
        fail(f"{tree} is already rebranded")
    if not (tree / "apps" / "filmcraft" / "Cargo.toml").is_file():
        fail(f"{tree} is not a FilmCraft checkout")
    up = b["upstream"]
    try:
        head = subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        head = "unknown"
    if head != up["commit"] and not allow_other_commit:
        fail(f"checkout is at {head}, brand.json pins {up['commit']} (use --allow-other-commit to try anyway)")

    product, company = b["product"], b["company"]
    ui = tree / "crates" / "ui-egui" / "src"
    app = tree / "apps" / "filmcraft"
    cli = tree / "apps" / "filmcraft-cli"
    host = urlparse(b["website"]).netloc + urlparse(b["website"]).path.rstrip("/")
    stats: dict[str, int] = {}

    # 1. Links (ArtCraft community/website -> ours; GitHub/issues -> our repo).
    edit(ui / "links.rs", [
        ('pub const DISCORD: &str = "https://discord.gg/artcraft";', f'pub const DISCORD: &str = "{b["community"]}";'),
        ('pub const WEBSITE: &str = "https://getartcraft.com";', f'pub const WEBSITE: &str = "{b["website"]}";'),
        ('pub const APP_PAGE: &str = "https://getartcraft.com/apps/filmcraft";', f'pub const APP_PAGE: &str = "{b["website"]}";'),
        ('pub const GITHUB: &str = "https://github.com/storytold/filmcraft";', f'pub const GITHUB: &str = "{b["repo"]}";'),
        ('pub const ISSUES: &str = "https://github.com/storytold/filmcraft/issues";', f'pub const ISSUES: &str = "{b["issues"]}";'),
    ])

    # 2. ArtCraft wording -> ours (before the generic FilmCraft rename).
    community = f"{company} Community"
    labels = [
        ("Join the ArtCraft Discord (discord.gg/artcraft)", community),
        ("Join the ArtCraft Discord…", f"{community}…"),
        ("Join the ArtCraft Discord", community),
        ("ArtCraft Website", f"{company} Website"),
        ("FilmCraft on getartcraft.com", f"{product} Home Page"),
    ]
    for f in (ui / "links.rs", ui / "menus.rs", ui / "header.rs", ui / "panels" / "dialogs.rs"):
        text = f.read_text(encoding="utf-8")
        for old, new in labels:
            text = text.replace(f'"{old}"', f'"{new}"')
        f.write_text(text, encoding="utf-8")
    edit(ui / "panels" / "dialogs.rs", [
        ('"getartcraft.com"', rust_str(host)),
        ('"A clean-room, pure-Rust non-linear video editor. Part of the ArtCraft family."',
         rust_str(f"{product} by {company}. Based on {CREDIT_TOKEN} (MIT OR Apache-2.0).")),
    ])

    # 3. Wordmark: ours instead of the ArtCraft marks; then remove the marks.
    brand_dir = tree / "studio-brand"
    brand_dir.mkdir(exist_ok=True)
    for name in ("wordmark-light.png", "wordmark-dark.png", "icon.svg", "icon-1024.png"):
        shutil.copyfile(ASSETS / name, brand_dir / name)
    edit(ui / "brand.rs", [
        ('include_bytes!("../../../docs/brand/artcraft-logo-white.png")',
         'include_bytes!("../../../studio-brand/wordmark-light.png")'),
        ('include_bytes!("../../../docs/brand/artcraft-logo.png")',
         'include_bytes!("../../../studio-brand/wordmark-dark.png")'),
    ])
    shutil.rmtree(tree / "docs" / "brand", ignore_errors=True)

    # 4. Product name in every UI/CLI string literal, and in the catalogs.
    n = 0
    for root in (ui, app / "src", cli / "src"):
        for f in root.rglob("*.rs"):
            n += rename_in_strings(f, "FilmCraft", product)
            n += rename_in_strings(f, "ArtCraft", company)
    stats["strings"] = n
    n = 0
    for f in (ui / "i18n").glob("*.tsv"):
        lines = f.read_text(encoding="utf-8").splitlines(keepends=True)
        out = []
        for line in lines:
            if line.startswith("#"):
                out.append(line)
                continue
            if "getartcraft" in line or "ArtCraft Discord" in line:
                n += 1
                continue  # those labels changed meaning; fall back to English
            new = line.replace("FilmCraft", product).replace("ArtCraft", company)
            n += new != line
            out.append(new)
        f.write_text("".join(out), encoding="utf-8")
    stats["catalog_lines"] = n
    for f in (ui / "panels" / "dialogs.rs",):
        edit(f, [(CREDIT_TOKEN, f"{up['name']} by {up['authors']}")])

    # 5. CLI name in its help/usage text.
    stats["cli_strings"] = sum(rename_in_strings(f, "filmcraft-cli", b["cli"]) for f in (cli / "src").rglob("*.rs"))

    # 6. App id and binary names.
    edit(app / "src" / "main.rs", [('const APP_ID: &str = "ai.storyteller.filmcraft";',
                                    f'const APP_ID: &str = "{b["app_id"]}";')])
    edit(cli / "Cargo.toml", [('[[bin]]\nname = "filmcraft-cli"', f'[[bin]]\nname = "{b["cli"]}"')])
    cargo = app / "Cargo.toml"
    text = cargo.read_text(encoding="utf-8")
    if "[[bin]]" in text:
        fail(f"{cargo} already declares [[bin]]; update rebrand.py")
    if 'default-run = "filmcraft"' not in text:
        fail(f"{cargo}: default-run not found; update rebrand.py")
    text = text.replace('default-run = "filmcraft"', f'default-run = "{b["binary"]}"')
    text = re.sub(r"(\n\[dependencies\])", f'\n[[bin]]\nname = "{b["binary"]}"\npath = "src/main.rs"\n\\1', text, count=1)
    if f'name = "{b["binary"]}"' not in text:
        fail(f"{cargo}: no [dependencies] section to anchor [[bin]]")
    cargo.write_text(text, encoding="utf-8")

    # 7. Icons: same file names, our artwork.
    icons = tree / "assets" / "app-icon"
    for f in icons.rglob("*"):
        if not f.is_file():
            continue
        if f.suffix == ".png":
            from_png = re.search(r"(\d+)x\1", str(f.relative_to(icons)))
            size = int(from_png.group(1)) if from_png else (512 if "512" in f.name else 1024)
            src = ASSETS / f"icon-{size}.png"
            shutil.copyfile(src if src.exists() else ASSETS / "icon-1024.png", f)
        elif f.suffix == ".svg":
            shutil.copyfile(ASSETS / "icon.svg", f)
        elif f.suffix == ".ico":
            shutil.copyfile(ASSETS / "icon.ico", f)
        elif f.suffix == ".icns":
            shutil.copyfile(ASSETS / "icon.icns", f)
        elif f.name.endswith(".attribution"):
            f.unlink()
    (icons / "LICENSE.txt").write_text(
        f"{product} app icon: original artwork by {company} (studio/make_brand_assets.py in\n"
        f"{b['repo']}). All rights reserved by {company}; not covered by the code licence.\n",
        encoding="utf-8")
    favicon = tree / "apps" / "filmcraft-web" / "web" / "favicon.png"
    if favicon.exists():
        shutil.copyfile(ASSETS / "icon-64.png", favicon)

    # 8. NOTICE, README, marker.
    notice = tree / "NOTICE"
    notice.write_text(
        f"{product}\nCopyright (c) 2026 {company}.\n\n"
        f"{product} is a modified version of {up['name']} by {up['authors']}\n"
        f"({up['repo']}, commit {up['commit']}), used under the MIT License or the Apache\n"
        "License, Version 2.0, at your option (LICENSE-MIT, LICENSE-APACHE). It is not made,\n"
        f"sponsored or endorsed by {up['authors']}; the ArtCraft name and marks are removed.\n"
        f"The {product} name, icon and wordmark belong to {company}.\n\n"
        "---- Original NOTICE ----\n\n" + notice.read_text(encoding="utf-8"), encoding="utf-8")
    (tree / "README.md").write_text(
        f"# {product}\n\n{company}'s video editor: an editable timeline for MediaForge reels,\n"
        f"colour, titles, captions, multicam and export, built in pure Rust.\n\n"
        f"Based on [{up['name']}]({up['repo']}) by {up['authors']} (commit `{up['commit'][:12]}`),\n"
        "MIT OR Apache-2.0. Build and update with `scripts/studio-setup.sh` in\n"
        f"[{b['repo']}]({b['repo']}); see `studio/README.md` there.\n", encoding="utf-8")
    marker = {"brand": b, "upstream_head": head, "stats": stats}
    (tree / MARKER).write_text(json.dumps(marker, indent=1), encoding="utf-8")
    return marker


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("tree", type=Path)
    p.add_argument("--brand", type=Path, default=HERE / "brand.json")
    p.add_argument("--allow-other-commit", action="store_true")
    a = p.parse_args()
    marker = rebrand(a.tree.resolve(), json.loads(a.brand.read_text()), a.allow_other_commit)
    print(json.dumps(marker["stats"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
