"""`adlab demo`: the whole pipeline offline, on made-up sample data, in its own folder.

Nothing real is touched: it writes to data/demo/ (sites + data), uses canned Claude
answers (no API call, $0), no ComfyUI, no ElevenLabs, no GPU. Every sample is labelled
DEMO. Afterwards point `adlab serve` at it to click through the dashboard:

    ADLAB_DATA_DIR=data/demo/data ADLAB_SITES_DIR=data/demo/sites adlab serve
"""
from __future__ import annotations

import os
import textwrap
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from PIL import Image, ImageDraw

from .config import ROOT

SITE = "democo"
TEARDOWNS = [
    {"hook": {"type": "problem_callout", "seconds": 2, "on_screen_text": "DEMO A",
              "visual": "phone screen showing a dead charger app"},
     "angles": ["pain", "trust"], "offer": "free quote", "cta": "book online",
     "timeline": [{"start": 0, "end": 2, "description": "problem shown"},
                  {"start": 2, "end": 7, "description": "credential, then the fix"}],
     "format": "demo", "beats": ["problem_callout", "credential", "cta"], "caption_style": "bold captions",
     "music_energy": "medium",
     "why_it_works": "Shows one specific, visible problem in the first second, then a credential removes the risk."},
    {"hook": {"type": "question", "seconds": 1.5, "on_screen_text": "DEMO B", "visual": "garage at night"},
     "angles": ["trust", "speed"], "offer": "", "cta": "call",
     "timeline": [{"start": 0, "end": 3, "description": "question over a dark garage"},
                  {"start": 3, "end": 8, "description": "licence card, tidy install"}],
     "format": "text_on_screen", "beats": ["problem_callout", "credential", "credential", "cta"],
     "caption_style": "large centred text", "music_energy": "low",
     "why_it_works": "The question makes the viewer self-select; proof arrives before the ask."},
    {"hook": {"type": "demo", "seconds": 2, "on_screen_text": "DEMO C", "visual": "hands mounting a wall unit"},
     "angles": ["desire"], "offer": "", "cta": "learn more",
     "timeline": [{"start": 0, "end": 4, "description": "install time-lapse"},
                  {"start": 4, "end": 6, "description": "finished install, logo"}],
     "format": "demo", "beats": ["demo", "offer", "cta"], "caption_style": "minimal", "music_energy": "high",
     "why_it_works": "Motion from the first frame and a satisfying finished result."},
]
BRIEFS = {"briefs": [
    {"hook": {"type": "problem_callout", "seconds": 2, "visual": "EV charger status light off in a garage"},
     "angle": "trust", "script": "Charger acting up? Have it checked by a Licensed Master Electrician.",
     "shots": [{"index": 1, "seconds": 2.5, "visual": "EV charger on a garage wall, status light off",
                "asset": "generated_image", "on_screen_text": "Charger acting up?"},
               {"index": 2, "seconds": 3, "visual": "tool belt and multimeter on a workbench",
                "asset": "generated_image", "on_screen_text": "Licensed Master Electrician"},
               {"index": 3, "seconds": 2.5, "visual": "map outline of the city", "asset": "text_card",
                "on_screen_text": "Serving Calgary and area"}],
     "cta": "Book a check", "aspect_ratios": ["9:16", "4:5", "1:1"],
     "claims": [{"text": "Licensed Master Electrician", "fact_key": "licensed"},
                {"text": "Serving Calgary and area", "fact_key": "service_area"}],
     "missing_facts": ["typical response time"]},
    {"hook": {"type": "question", "seconds": 1.5, "visual": "dark garage, one light on"},
     "angle": "desire", "script": "Thinking about home charging? Start with a Licensed Master Electrician.",
     "shots": [{"index": 1, "seconds": 3, "visual": "empty garage wall with an outlet", "asset": "generated_image",
                "on_screen_text": "Thinking about home charging?"},
               {"index": 2, "seconds": 3, "visual": "neat wall-mounted charger", "asset": "generated_image",
                "on_screen_text": "Start with a Licensed Master Electrician"}],
     "cta": "Get a quote", "aspect_ratios": ["9:16", "4:5", "1:1"],
     "claims": [{"text": "Licensed Master Electrician", "fact_key": "licensed"}],
     "missing_facts": ["install price"]},
]}


class CannedClaude:
    """Same surface as anthropic.Anthropic().beta.messages.parse, canned answers, 0 tokens."""

    def __init__(self) -> None:
        self.queue = {"TeardownDraft": list(TEARDOWNS), "BriefSet": [BRIEFS]}
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kw: Any):
        cls = kw["output_format"]
        data = self.queue[cls.__name__].pop(0)
        usage = SimpleNamespace(input_tokens=0, output_tokens=0, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        return SimpleNamespace(stop_reason="end_turn", stop_details=None, usage=usage, model="demo-canned",
                               parsed_output=cls.model_validate(data))


def _sample_ad(path: Path, seed: int, label: str) -> Path:
    im = Image.new("RGB", (540, 960), (24, 26, 34))
    d = ImageDraw.Draw(im)
    for i in range(10):
        x, y = (i * 83 + seed * 151) % 540, (i * 137 + seed * 71) % 960
        d.ellipse((x, y, x + 140, y + 140), fill=((seed * 80 + i * 20) % 255, 120 + i * 10, (seed * 40) % 255))
    d.text((24, 24), f"DEMO SAMPLE AD {label}", fill=(255, 255, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, quality=90)
    return path


def run(root: Path | None = None) -> dict:
    root = (root or ROOT / "data" / "demo").resolve()
    os.environ["ADLAB_DATA_DIR"] = str(root / "data")
    os.environ["ADLAB_SITES_DIR"] = str(root / "sites")
    for k in ("COMFYUI_URL", "ELEVENLABS_API_KEY"):
        os.environ.pop(k, None)
    from . import analyst, ingest as ing, llm, producer, scout, strategist
    from .store import LocalStore

    site = root / "sites" / SITE
    site.mkdir(parents=True, exist_ok=True)
    (site / "site.yaml").write_text("name: Demo Electric (DEMO)\ndomain: demo.example\nrole: primary\n"
                                    "region: Calgary, AB\nsummary: EV charger installs (made-up demo site)\n")
    (site / "brand.yaml").write_text("colors: {primary: '#0F2A3D', text: '#FFFFFF'}\ntone: [plain, confident]\n"
                                     "banned_words: [cheap, guaranteed]\nend_card_text: demo.example\n")
    (site / "facts.yaml").write_text(textwrap.dedent("""\
        facts:
          licensed:
            statement: "Licensed Master Electrician"
            verified: true
            source: "DEMO"
          service_area:
            statement: "Serving Calgary and area"
            verified: true
          price_from:
            statement: "Installs from $X"
            verified: false
    """))
    store = LocalStore()
    for i, label in enumerate("ABC", 1):
        img = _sample_ad(root / "samples" / f"ad_{label}.jpg", i, label)
        ing.ingest_media(img, SITE, ing.Sidecar(advertiser=f"Demo Competitor {label}", platform="meta",
                                                running_since=date(2026, 6, 1), still_running=True, variants=3,
                                                ad_text=f"DEMO sample ad {label}"), store)
    scout.rescore(store)
    llm.set_client(CannedClaude())  # type: ignore[arg-type]
    try:
        analyzed = analyst.run(SITE, min_score=0, limit=3, whisper=False, store=store, embed=False, redo=True)
        strategist.build_patterns(store)
        scout.rescore(store)
        briefs = strategist.write_briefs(SITE, count=2, store=store)
    finally:
        llm.set_client(None)
    made = producer.produce(site=SITE, store=store, motion=False)
    return {"root": str(root), "analyzed": sum(a.status == "analyzed" for a in analyzed),
            "briefs": [b.status for b in briefs], "creatives": len(made.creatives), "manifest": made.manifest,
            "errors": made.errors + [a.message for a in analyzed if a.status == "failed"]}
