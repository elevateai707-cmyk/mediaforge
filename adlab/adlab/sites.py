"""Sites to advertise: one folder per site under sites/ (add a folder to add a site).

    sites/<site>/site.yaml    what the site is (name, domain, role)
    sites/<site>/facts.yaml   the only claims ads may make (see facts.py)
    sites/<site>/brand.yaml   colours, fonts, logo, tone, banned words
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import settings
from .facts import FactSheet, load_facts

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SiteInfo(_M):
    name: str = ""             # blank until you fill it in (`adlab sites` shows it)
    domain: str = Field(min_length=3)
    role: Literal["primary", "portfolio"] = "primary"
    region: str = ""
    summary: str = ""          # one line, in your words: what the site sells


class Colors(_M):
    primary: str = ""
    secondary: str = ""
    background: str = ""
    text: str = ""

    @field_validator("*")
    @classmethod
    def _hex(cls, v: str) -> str:
        if v and not HEX.match(v):
            raise ValueError(f"colour {v!r} must be #RRGGBB")
        return v


class Brand(_M):
    colors: Colors = Colors()
    fonts: list[str] = []
    logo_path: str = ""        # relative to the site folder
    tone: list[str] = []
    banned_words: list[str] = []
    end_card_text: str = ""
    voice_id: str = ""         # ElevenLabs voice (stock or your own clone, never a real person's)

    def logo(self, site_dir: Path) -> Optional[Path]:
        if not self.logo_path:
            return None
        p = (site_dir / self.logo_path).resolve()
        return p if p.is_file() else None

    def missing(self) -> list[str]:
        """What's still unfilled (for `adlab sites`)."""
        gaps = [f"colors.{k}" for k, v in self.colors.model_dump().items() if not v]
        if not self.fonts:
            gaps.append("fonts")
        if not self.logo_path:
            gaps.append("logo_path")
        if not self.tone:
            gaps.append("tone")
        return gaps


class Site(_M):
    slug: str
    dir: Path
    info: SiteInfo
    brand: Brand
    facts: FactSheet


def _yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping")
    return data


def load_site(slug: str, sites_dir: Optional[Path] = None) -> Site:
    base = (sites_dir or settings().sites_dir) / slug
    return Site(
        slug=slug, dir=base,
        info=SiteInfo(**_yaml(base / "site.yaml")),
        brand=Brand(**_yaml(base / "brand.yaml")),
        facts=load_facts(slug, base.parent),
    )


def list_sites(sites_dir: Optional[Path] = None) -> list[str]:
    root = sites_dir or settings().sites_dir
    return sorted(p.name for p in root.iterdir() if (p / "site.yaml").is_file()) if root.is_dir() else []
