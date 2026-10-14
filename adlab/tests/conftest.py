"""Isolate every test from real data: temp data dir, temp sites dir."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ADLAB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("STUDIO_CLI", raising=False)
    yield


@pytest.fixture()
def sites_dir(tmp_path, monkeypatch) -> Path:
    """One site 'testco' with two verified facts, one unverified, one expired."""
    d = tmp_path / "sites" / "testco"
    d.mkdir(parents=True)
    (d / "site.yaml").write_text("name: Test Co\ndomain: test.example\nrole: primary\n")
    (d / "brand.yaml").write_text("colors: {primary: '#112233'}\nbanned_words: [guaranteed]\n")
    (d / "facts.yaml").write_text(textwrap.dedent("""
        facts:
          red_seal:
            statement: "Installed by a Red Seal Master Electrician"
            verified: true
            source: "cert #123"
          service_area:
            statement: "Serving Calgary"
            verified: true
          price_from:
            statement: "From $999"
            verified: false
          spring_promo:
            statement: "10% off in April"
            verified: true
            expires: 2020-04-30
    """))
    monkeypatch.setenv("ADLAB_SITES_DIR", str(tmp_path / "sites"))
    return tmp_path / "sites"


def brief_data(**over) -> dict:
    data = {
        "id": "b1", "site": "testco", "pattern_id": "p1", "source_ad_ids": ["ad1"],
        "hook": {"type": "problem_callout", "seconds": 2, "visual": "a dead charger light"},
        "angle": "trust",
        "script": "Charger acting up? Get it done right.",
        "shots": [{"index": 1, "seconds": 3, "visual": "garage wall", "asset": "generated_image"}],
        "cta": "Book a quote",
        "claims": [{"text": "Installed by a Red Seal Master Electrician", "fact_key": "red_seal"}],
        "aspect_ratios": ["9:16"],
    }
    data.update(over)
    return data
