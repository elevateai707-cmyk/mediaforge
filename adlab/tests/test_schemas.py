"""Stage contracts reject malformed or unsafe data."""
from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from adlab.schemas import Ad, ComplianceReport, Creative, Pattern, Result, Scene, Teardown
from adlab.config import ROOT
from adlab.sites import Brand, list_sites, load_site

SHA = "a" * 64


def teardown(**over):
    data = {
        "ad_id": "ad1",
        "hook": {"type": "question", "seconds": 2.5, "visual": "close-up of a panel"},
        "angles": ["savings", "trust"],
        "timeline": [{"start": 0, "end": 3, "description": "problem shown"},
                     {"start": 3, "end": 12, "description": "credential + price"}],
        "format": "talking_head",
        "pacing": {"length_s": 15, "cuts_per_10s": 4, "music_energy": "medium"},
        "why_it_works": "Names a specific local pain, then removes risk with a credential.",
        "model": "claude-opus-5-5",
    }
    data.update(over)
    return Teardown.model_validate(data)


def test_teardown_valid_and_strict():
    assert teardown().hook.type == "question"
    with pytest.raises(ValidationError):
        teardown(hook={"type": "question", "seconds": 5, "visual": "x"})   # hook is 1-3 s
    with pytest.raises(ValidationError):
        teardown(script_lines=["their exact words"])                       # unknown field
    with pytest.raises(ValidationError):
        teardown(angles=["vibes"])


def test_scene_order():
    with pytest.raises(ValidationError):
        Scene(start=3, end=2, description="x")


def test_creative_must_be_flagged_ai_generated():
    c = Creative(id="c1", brief_id="b1", aspect_ratio="9:16", path="x.mp4", kind="video", sha256=SHA)
    assert c.ai_generated is True and "AI" in c.ai_disclosure
    with pytest.raises(ValidationError):
        Creative(id="c1", brief_id="b1", aspect_ratio="9:16", path="x.mp4", kind="video",
                 sha256=SHA, ai_generated=False)
    with pytest.raises(ValidationError):
        Creative(id="c1", brief_id="b1", aspect_ratio="9:16", path="x.mp4", kind="video", sha256="nothex")


def test_compliance_verdict_consistent():
    ok = {"name": "fact_trace", "passed": True}
    bad = {"name": "similarity", "passed": False, "reasons": ["script 92% similar to ad1"]}
    assert ComplianceReport(creative_id="c1", verdict="PASS", checks=[ok]).verdict == "PASS"
    assert ComplianceReport(creative_id="c1", verdict="REJECT", checks=[ok, bad]).verdict == "REJECT"
    with pytest.raises(ValidationError):
        ComplianceReport(creative_id="c1", verdict="PASS", checks=[ok, bad])
    with pytest.raises(ValidationError):
        ComplianceReport(creative_id="c1", verdict="FIX", checks=[ok])


def test_ad_dates_and_pattern():
    src = {"platform": "meta", "captured_via": "manual_link", "advertiser": "Acme Electric",
           "url": "https://www.facebook.com/ads/library/?id=1"}
    with pytest.raises(ValidationError):
        Ad(id="ad123456", site="ev1charger", source=src, first_seen=date(2026, 5, 2), last_seen=date(2026, 5, 1))
    with pytest.raises(ValidationError):
        Pattern(id="p", name="x", steps=["hook"], source_teardown_ids=["t1"])  # >= 2 steps


def test_result_rates():
    r = Result(creative_id="c1", platform="meta", campaign_id="1", day=date(2026, 10, 1),
               impressions=1000, clicks=25, spend_cad=50, leads=5)
    assert (r.ctr, r.cpc, r.cpl) == (0.025, 2.0, 10.0)
    assert Result(creative_id="c1", platform="tiktok", campaign_id="1", day=date(2026, 10, 1),
                  impressions=0, clicks=0, spend_cad=0).ctr is None


def test_brand_colours_validated():
    with pytest.raises(ValidationError):
        Brand(colors={"primary": "red"})


def test_all_site_templates_load():
    for slug in list_sites(ROOT / "sites"):
        site = load_site(slug, ROOT / "sites")
        assert site.info.domain and "guaranteed" in site.brand.banned_words
