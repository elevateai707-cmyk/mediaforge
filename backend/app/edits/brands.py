"""Brand definitions for end cards (config/brands.yaml, read-only at runtime).

``MF_BRANDS_FILE`` overrides the path (tests). A missing/unreadable file falls
back to the two built-in brands so renders never depend on the file existing.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

from .. import config

log = logging.getLogger("mediaforge.brands")

END_CARD_SECONDS = 1.8
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

DEFAULT_BRANDS: dict[str, dict[str, Any]] = {
    "albertaweb": {"name": "AlbertaWeb.Design", "handle": "albertaweb.design",
                   "url": "albertaweb.design", "cta": "Run the live scan",
                   "colors": ["#19C6E6", "#3A6CF4"], "logo_path": None},
    "buildwithdb": {"name": "Build With DB", "handle": "@buildwithdb",
                    "url": "buildwithdb.ca", "cta": "Systems Create Freedom",
                    "colors": ["#C9A227", "#111111"], "logo_path": None},
}


class BrandError(ValueError):
    """Unknown or invalid brand (mapped to HTTP 422)."""


def brands_file() -> Path:
    return Path(os.environ.get("MF_BRANDS_FILE") or config.MF_ROOT / "config" / "brands.yaml")


def _clean(brand_id: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise BrandError(f"brand {brand_id!r} is not a mapping")
    colors = [str(c) for c in (raw.get("colors") or []) if _HEX.match(str(c))]
    if not colors:
        colors = list(DEFAULT_BRANDS["albertaweb"]["colors"])
    if len(colors) == 1:
        colors = colors * 2
    logo = raw.get("logo_path")
    return {
        "id": brand_id,
        "name": str(raw.get("name") or brand_id)[:60],
        "handle": str(raw.get("handle") or "")[:60],
        "url": str(raw.get("url") or "")[:80],
        "cta": str(raw.get("cta") or "")[:80],
        "colors": colors[:3],
        "logo_path": str(logo) if logo else None,
    }


def load_brands() -> dict[str, dict[str, Any]]:
    data: dict[str, Any] = dict(DEFAULT_BRANDS)
    path = brands_file()
    if path.is_file():
        try:
            import yaml
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                data = loaded
        except Exception as exc:  # noqa: BLE001 - never break renders over config
            log.warning("could not read %s: %s; using built-in brands", path, exc)
    out: dict[str, dict[str, Any]] = {}
    for key, raw in data.items():
        try:
            out[str(key)] = _clean(str(key), raw)
        except BrandError as exc:
            log.warning("skipping brand: %s", exc)
    return out


def get_brand(brand_id: Optional[str]) -> Optional[dict[str, Any]]:
    if not brand_id:
        return None
    brands = load_brands()
    if brand_id not in brands:
        raise BrandError(f"unknown brand {brand_id!r}; known: {', '.join(sorted(brands)) or 'none'}")
    return brands[brand_id]
