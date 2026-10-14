"""Phase 4: patterns from teardowns, repeat-pattern feedback, fact-locked briefs, dry-run."""
from __future__ import annotations

from datetime import date

import pytest

from adlab import ingest as ing, llm, scout, strategist, usage
from adlab.schemas import Brief, Pattern, Teardown
from adlab.store import LocalStore
from tests.fakes import FakeAnthropic, teardown_draft
from tests.test_scout import TODAY, make_image


@pytest.fixture()
def store(sites_dir):
    return LocalStore()


def add_analyzed(store, tmp_path, seed: int, advertiser: str, beats, angles=("pain", "trust")) -> str:
    r = ing.ingest_media(make_image(tmp_path / f"{seed}.jpg", seed=seed), "testco",
                         ing.Sidecar(advertiser=advertiser, running_since=date(2026, 5, 1), still_running=True,
                                     variants=3), store, today=TODAY)
    d = teardown_draft(beats=list(beats), angles=list(angles))
    td = Teardown(ad_id=r.ad_id, model="claude-opus-5-5",
                  pacing={"length_s": 12, "cuts_per_10s": 3, "music_energy": "medium"},
                  **{k: v for k, v in d.items() if k not in ("caption_style", "music_energy")})
    store.put("teardown", td, id=r.ad_id, site="testco", ref=r.ad_id)
    return r.ad_id


def brief_draft(**over) -> dict:
    d = {
        "hook": {"type": "problem_callout", "seconds": 2, "visual": "dead charger light at night"},
        "angle": "trust",
        "script": "Charger acting up? Get it fixed by someone qualified.",
        "shots": [{"index": 1, "seconds": 3, "visual": "garage wall unit, light off", "asset": "generated_image",
                   "on_screen_text": "Charger acting up?", "vo_line": "Charger acting up?"},
                  {"index": 2, "seconds": 4, "visual": "tool belt on a workbench", "asset": "generated_image",
                   "on_screen_text": "Installed by a Red Seal Master Electrician"}],
        "cta": "Book a quote",
        "claims": [{"text": "Installed by a Red Seal Master Electrician", "fact_key": "red_seal"}],
        "aspect_ratios": ["9:16", "1:1"],
        "missing_facts": ["install price"],
    }
    d.update(over)
    return d


def test_patterns_group_by_beats_and_rank(store, tmp_path):
    a = add_analyzed(store, tmp_path, 1, "Spark", ["problem_callout", "credential", "cta"])
    b = add_analyzed(store, tmp_path, 2, "Volt", ["problem_callout", "credential", "credential", "cta"])
    add_analyzed(store, tmp_path, 3, "Amp", ["demo", "offer", "cta"], angles=("speed",))
    scout.rescore(store, today=TODAY)
    pats = strategist.build_patterns(store)
    assert len(pats) == 2
    top, evidence = strategist.ranked_patterns(store, "testco")[0]
    assert top.name == "Problem call-out → Credential → CTA" and set(top.source_teardown_ids) == {a, b}
    assert evidence > strategist.ranked_patterns(store, "testco")[1][1]
    # my own results outrank competitor evidence
    weak = strategist.ranked_patterns(store)[1][0]
    weak.own_performance_score = 80
    store.put("pattern", weak, id=weak.id, site="testco", score=1)
    assert strategist.ranked_patterns(store)[0][0].id == weak.id
    assert strategist.build_patterns(store) and store.fetch("pattern", weak.id, Pattern).own_performance_score == 80


def test_repeat_pattern_feeds_scout_scores(store, tmp_path):
    a = add_analyzed(store, tmp_path, 4, "Spark", ["problem_callout", "cta"], angles=("trust",))
    add_analyzed(store, tmp_path, 5, "Volt", ["problem_callout", "cta"], angles=("trust", "speed"))
    add_analyzed(store, tmp_path, 6, "Spark", ["problem_callout", "cta"], angles=("trust",))   # same advertiser
    assert strategist.repeat_counts(store)[a] == 1
    scout.rescore(store, today=TODAY)
    s = store.get(a).score
    assert s.repeat_pattern_count == 1 and s.breakdown["repeat_pattern"] == 5


def test_write_briefs_fact_locked(store, tmp_path):
    add_analyzed(store, tmp_path, 7, "Spark", ["problem_callout", "credential", "cta"])
    scout.rescore(store, today=TODAY)
    fake = FakeAnthropic({"BriefSet": [{"briefs": [
        brief_draft(),
        brief_draft(script="Different idea. Fast fix.", hook={"type": "question", "seconds": 1.5, "visual": "x"}),
    ]}]})
    llm.set_client(fake)
    try:
        res = strategist.write_briefs("testco", count=2, store=store, today=TODAY)
    finally:
        llm.set_client(None)
    assert [r.status for r in res] == ["drafted", "drafted"]
    b = res[0].brief
    assert b.site == "testco" and b.missing_facts == ["install price"] and b.source_ad_ids
    assert store.fetch("brief", b.id, Brief).claims[0].fact_key == "red_seal"
    # the output schema itself only allows verified keys
    schema = fake.requests[0]["output_format"].model_json_schema()
    keys = schema["$defs"]["TestcoClaim"]["properties"]["fact_key"]["enum"]
    assert keys == ["red_seal", "service_area"]
    prompt = fake.requests[0]["messages"][0]["content"][0]["text"]
    assert "Installed by a Red Seal Master Electrician" in prompt and "From $999" not in prompt  # unverified hidden
    assert "Charger not working?" not in prompt                 # source ad's on-screen words never sent
    assert usage.ledger()[0]["command"] == "brief"


def test_brief_rejected_with_reason_if_fact_revoked_mid_draft(store, tmp_path, monkeypatch):
    """Defence in depth: briefs are re-validated against the facts as they are at storage time."""
    from adlab import facts as facts_mod

    add_analyzed(store, tmp_path, 8, "Spark", ["problem_callout", "cta"])

    def revoked_check(data, sheet, today=None):
        sheet = sheet.model_copy(deep=True)
        sheet.facts["red_seal"].verified = False          # you un-verified it while Claude was writing
        return facts_mod.check_brief(data, sheet, today)

    monkeypatch.setattr(strategist, "check_brief", revoked_check)
    llm.set_client(FakeAnthropic({"BriefSet": [{"briefs": [brief_draft()]}]}))
    try:
        res = strategist.write_briefs("testco", count=1, store=store, today=TODAY)
    finally:
        llm.set_client(None)
    assert res[0].status == "rejected"
    assert "claim 1" in res[0].problems[0] and "not verified" in res[0].problems[0]
    assert store.rows("brief") == []


def test_blocked_without_facts_or_patterns(store, tmp_path, sites_dir):
    assert strategist.write_briefs("testco", store=store)[0].problems[0].startswith("no patterns yet")
    add_analyzed(store, tmp_path, 9, "Spark", ["problem_callout", "cta"])
    (sites_dir / "testco" / "facts.yaml").write_text("facts:\n  x_y:\n    statement: ''\n")
    res = strategist.write_briefs("testco", store=store)
    assert res[0].status == "blocked" and "no verified facts" in res[0].problems[0]


def test_dry_run_shows_request_and_spends_nothing(store, tmp_path):
    add_analyzed(store, tmp_path, 10, "Spark", ["problem_callout", "credential", "cta"])
    fake = FakeAnthropic({})
    llm.set_client(fake)
    try:
        res = strategist.write_briefs("testco", dry_run=True, store=store, today=TODAY)
    finally:
        llm.set_client(None)
    assert fake.requests == [] and res[0].status == "planned" and usage.ledger() == []
    req = res[0].request
    assert req["output_format"] == "BriefSet" and "red_seal" in str(req["output_schema"])
