"""Phase 6: Compliance Officer catches inflation, copying, fake proof and policy hits."""
from __future__ import annotations

from datetime import date

import pytest

from adlab import compliance as comp, ingest as ing
from adlab.schemas import Brief, ComplianceReport, Creative
from adlab.store import LocalStore
from tests.conftest import brief_data
from tests.test_scout import TODAY, make_image


@pytest.fixture()
def store(sites_dir):
    return LocalStore()


def brief(**over) -> Brief:
    return Brief.model_validate(brief_data(**over))


def shot(i=1, **over) -> dict:
    s = {"index": i, "seconds": 3, "visual": "garage wall, charger glowing", "asset": "generated_image"}
    s.update(over)
    return s


def scouted(store, tmp_path, seed=1, advertiser="Spark Electric",
            text="Your charger died again? We fix it same day across the whole city, call now"):
    r = ing.ingest_media(make_image(tmp_path / f"s{seed}.jpg", seed=seed), "testco",
                         ing.Sidecar(advertiser=advertiser, ad_text=text, running_since=date(2026, 5, 1)),
                         store, today=TODAY)
    return r.ad_id


def reasons(rep: ComplianceReport, name: str) -> list[str]:
    return next(c.reasons for c in rep.checks if c.name == name)


def test_clean_brief_passes_and_is_saved(store):
    b = brief(shots=[shot(on_screen_text="Installed by a Red Seal Master Electrician")])
    rep = comp.check_brief(b, store)
    assert rep.verdict == "PASS", rep.checks
    row = store.rows("compliance", ref="b1")[0]
    assert row["status"] == "PASS" and row["id"] == "brief:b1"


def test_inflated_claim_with_valid_key_is_rejected(store):
    rep = comp.check_brief(brief(claims=[{"text": "Calgary's best Red Seal Master Electrician",
                                          "fact_key": "red_seal"}]), store)
    assert rep.verdict == "REJECT"
    assert any("inflates" in r for r in reasons(rep, "fact_trace"))
    rep = comp.check_brief(brief(claims=[{"text": "500+ installs by a Red Seal Master Electrician",
                                          "fact_key": "red_seal"}]), store)
    assert any("500+" in r or "500" in r for r in reasons(rep, "fact_trace")) and rep.verdict == "REJECT"


def test_uncited_numbers_and_unusable_keys_rejected(store):
    rep = comp.check_brief(brief(script="Charger dead? Fixed in 24 hours."), store)
    assert rep.verdict == "REJECT" and "['24']" in reasons(rep, "fact_trace")[0]
    rep = comp.check_brief(brief(claims=[{"text": "From $999", "fact_key": "price_from"}]), store)
    assert rep.verdict == "REJECT" and "isn't a usable fact" in reasons(rep, "fact_trace")[0]


def test_drifting_claim_needs_fix(store):
    rep = comp.check_brief(brief(claims=[{"text": "Licensed pros handle wiring permits and inspections",
                                          "fact_key": "red_seal"}]), store)
    assert rep.verdict == "FIX" and "drifts" in reasons(rep, "fact_trace")[0]


def test_copying_a_scouted_ad_is_rejected(store, tmp_path):
    scouted(store, tmp_path)
    rep = comp.check_brief(brief(script="Charger trouble? We fix it same day across the whole city."), store)
    assert rep.verdict == "REJECT"
    assert "we fix it same day across" in reasons(rep, "similarity")[0]
    assert comp.check_brief(brief(), store).verdict == "PASS"


def test_banned_content(store, tmp_path):
    scouted(store, tmp_path, advertiser="Spark Electric")
    cases = {
        "Rated 5 stars by Calgary homeowners": "REJECT",          # testimonial + number
        "Our customers love it": "REJECT",
        "Money-back if it fails": "REJECT",                     # guarantee not in facts
        "Cheaper than Spark Electric": "REJECT",                  # competitor's name
        "Done right, guaranteed": "REJECT",                       # banned word FIX + guarantee REJECT
    }
    for script, want in cases.items():
        assert comp.check_brief(brief(script=script), store).verdict == want, script
    rep = comp.check_brief(brief(script="Get it done right"), store)
    assert rep.verdict == "PASS"
    rep = comp.check_brief(brief(shots=[shot(visual="smiling homeowner next to charger")]), store)
    assert rep.verdict == "REJECT" and "homeowner" in reasons(rep, "banned_content")[0]


def test_banned_word_alone_is_fix(store, sites_dir):
    (sites_dir / "testco" / "brand.yaml").write_text("banned_words: [cheap]\n")
    rep = comp.check_brief(brief(script="Not cheap. Done right."), store)
    assert rep.verdict == "FIX" and "banned word" in reasons(rep, "banned_content")[0]


def test_platform_policy_rules(store):
    rep = comp.check_brief(brief(script="Shocking charger fix. Last chance to book."), store)
    got = reasons(rep, "platform_policy")
    assert rep.verdict == "FIX" and any("(sensational)" in r for r in got) and any("(misleading_urgency)" in r for r in got)


def test_creative_visual_similarity(store, tmp_path):
    scouted(store, tmp_path, seed=3)
    b = brief()
    same = Creative(id="cr1", brief_id="b1", aspect_ratio="9:16", path=str(tmp_path / "s3.jpg"), kind="image", sha256="0" * 64)
    rep = comp.check_creative(same, b, store)
    assert rep.verdict == "REJECT" and "looks like source ad" in reasons(rep, "visual_similarity")[0]
    other = make_image(tmp_path / "ours.jpg", seed=99)
    rep = comp.check_creative(Creative(id="cr2", brief_id="b1", aspect_ratio="9:16", path=str(other), kind="image", sha256="0" * 64),
                              b, store)
    assert rep.verdict == "PASS" and store.rows("compliance", status="PASS")
