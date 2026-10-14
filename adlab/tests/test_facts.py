"""Zero fabrication: a brief may only cite verified, unexpired facts."""
from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from adlab.config import ROOT
from adlab.facts import NoUsableFacts, brief_model_for, check_brief, load_facts
from adlab.sites import list_sites
from tests.conftest import brief_data

TODAY = date(2026, 10, 14)


def test_usable_keys_only_verified_and_unexpired(sites_dir):
    sheet = load_facts("testco")
    assert sheet.usable_keys(TODAY) == ["red_seal", "service_area"]
    assert sheet.unusable(TODAY) == {"price_from": "not verified", "spring_promo": "expired 2020-04-30"}


def test_valid_brief_passes(sites_dir):
    model = brief_model_for(load_facts("testco"), TODAY)
    brief = model.model_validate(brief_data())
    assert brief.claims[0].fact_key == "red_seal"


def test_unknown_fact_key_is_rejected(sites_dir):
    """The core rule: an invented fact can't get past validation."""
    model = brief_model_for(load_facts("testco"), TODAY)
    bad = brief_data(claims=[{"text": "Rated 5 stars by 500 customers", "fact_key": "five_star_reviews"}])
    with pytest.raises(ValidationError) as exc:
        model.model_validate(bad)
    assert exc.value.errors()[0]["loc"] == ("claims", 0, "fact_key")


@pytest.mark.parametrize("key, reason", [
    ("five_star_reviews", "unknown fact 'five_star_reviews'"),
    ("price_from", "exists but isn't usable (not verified)"),
    ("spring_promo", "exists but isn't usable (expired 2020-04-30)"),
    ("", "claim has no fact_key"),
])
def test_check_brief_names_the_broken_claim(sites_dir, key, reason):
    claims = [{"text": "Red Seal", "fact_key": "red_seal"}, {"text": "the bad one", "fact_key": key}]
    res = check_brief(brief_data(claims=claims), load_facts("testco"), TODAY)
    assert not res.ok and res.brief is None
    assert len(res.claim_problems) == 1
    p = res.claim_problems[0]
    assert (p.index, p.text) == (1, "the bad one") and reason in p.problem


def test_missing_fact_key_field_rejected(sites_dir):
    res = check_brief(brief_data(claims=[{"text": "Fast installs"}]), load_facts("testco"), TODAY)
    assert not res.ok and "every factual claim must cite a fact" in res.claim_problems[0].problem


def test_brief_for_another_site_rejected(sites_dir):
    res = check_brief(brief_data(site="ev1charger"), load_facts("testco"), TODAY)
    assert not res.ok and any(e.startswith("site:") for e in res.other_errors)


def test_no_usable_facts_refuses_to_build_a_model(sites_dir, tmp_path):
    (sites_dir / "empty").mkdir()
    (sites_dir / "empty" / "facts.yaml").write_text("facts:\n  x_y:\n    statement: ''\n")
    with pytest.raises(NoUsableFacts):
        brief_model_for(load_facts("empty"), TODAY)


def test_bad_fact_key_names_rejected(sites_dir):
    (sites_dir / "bad").mkdir()
    (sites_dir / "bad" / "facts.yaml").write_text("facts:\n  Bad-Key:\n    statement: x\n")
    with pytest.raises(ValidationError):
        load_facts("bad")


def test_shipped_templates_have_no_usable_facts():
    """Templates must never ship a pre-verified claim: you verify every fact yourself."""
    real = ROOT / "sites"
    slugs = list_sites(real)
    assert set(slugs) == {"albertaweb", "ev1charger", "retrofitnetwork", "electrifyhq", "velvetthrone"}
    for slug in slugs:
        sheet = load_facts(slug, real)
        assert sheet.facts, slug
        assert sheet.usable_keys(TODAY) == [], f"{slug} ships a verified fact"
