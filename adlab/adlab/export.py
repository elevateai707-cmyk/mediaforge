"""`adlab export`: approved creatives -> upload-ready files + one sheet per platform.

You upload by hand (AdLab never touches an ad account). Each ad name carries
`[adlab:<creative_id>]`; keep it when you upload, because that tag is how the Reporter
matches your platform results back to the creative, its brief and its pattern.

    data/exports/<date>/<platform>/adlab-<creative>-<ratio>.mp4
    data/exports/<date>/<platform>/upload_sheet.csv      ad name, file, placements, copy, CTA, URL, AI label
"""
from __future__ import annotations

import csv
import shutil
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel

from .config import settings
from .producer import probe
from .review import queue
from .sites import load_site
from .store import LocalStore

TAG = "[adlab:{id}]"
SHEET_COLS = ["ad_name", "file", "ratio", "placements", "seconds", "primary_text", "headline", "cta",
              "url", "ai_label", "compliance", "review_notes"]


@lru_cache(maxsize=4)
def _specs(path: str) -> dict:
    return (yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}).get("platforms", {})


def specs() -> dict:
    return _specs(str(settings().config_dir / "platform_specs.yaml"))


def ad_name(creative_id: str, site: str, hook: str, ratio: str) -> str:
    return f"{TAG.format(id=creative_id)} {site} {hook} {ratio}"


class ExportResult(BaseModel):
    folder: Optional[str] = None
    files: dict[str, list[str]] = {}
    sheets: dict[str, str] = {}
    skipped: list[str] = []


def export(store: Optional[LocalStore] = None, platforms: tuple[str, ...] = ("meta", "tiktok"),
           site: Optional[str] = None, out: Optional[Path] = None, today: Optional[date] = None) -> ExportResult:
    store = store or LocalStore()
    items = queue(store, site, statuses=("approved",))
    res = ExportResult()
    if not items:
        res.skipped.append("nothing approved yet: `adlab review` first")
        return res
    root = out or settings().data_dir / "exports" / (today or date.today()).isoformat()
    res.folder = str(root)
    sp = specs()
    for platform in platforms:
        spec = sp.get(platform)
        if not spec:
            res.skipped.append(f"{platform}: no spec in config/platform_specs.yaml")
            continue
        rows = []
        for it in items:
            c, b = it.creative, it.brief
            if b is None:
                continue
            placements = spec["ratios"].get(c.aspect_ratio)
            if not placements:
                res.skipped.append(f"{platform}: {c.id} is {c.aspect_ratio}, not used on {platform}")
                continue
            src = Path(c.path)
            info = probe(src)
            secs = float(info["format"]["duration"])
            mb = src.stat().st_size / 1e6
            problem = (f"{secs:.1f}s is shorter than {spec['min_seconds']}s" if secs < spec["min_seconds"] else
                       f"{secs:.1f}s is longer than {spec['max_seconds']}s" if secs > spec["max_seconds"] else
                       f"{mb:.0f} MB is over {spec['max_mb']} MB" if mb > spec["max_mb"] else "")
            if problem:
                res.skipped.append(f"{platform}: {c.id} {problem}")
                continue
            dest_dir = root / platform
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"adlab-{c.id}-{c.aspect_ratio.replace(':', 'x')}{src.suffix}"
            shutil.copy2(src, dest)
            res.files.setdefault(platform, []).append(str(dest))
            s = load_site(b.site)
            rows.append({
                "ad_name": ad_name(c.id, b.site, b.hook.type, c.aspect_ratio), "file": dest.name,
                "ratio": c.aspect_ratio, "placements": " + ".join(placements), "seconds": f"{secs:.1f}",
                "primary_text": b.script, "headline": b.claims[0].text if b.claims else "", "cta": b.cta,
                "url": f"https://{s.info.domain}", "ai_label": "ON: AI-generated (turn on the platform's AI label)",
                "compliance": it.compliance.verdict if it.compliance else "",
                "review_notes": it.decision.notes if it.decision else "",
            })
        if rows:
            sheet = root / platform / "upload_sheet.csv"
            with sheet.open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=SHEET_COLS)
                w.writeheader()
                w.writerows(rows)
            res.sheets[platform] = str(sheet)
    return res
