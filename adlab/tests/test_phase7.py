"""Phase 7: review gate, export, Reporter (CSV + Meta API) re-ranking, digest, local API."""
from __future__ import annotations

import csv
import json
import shutil
from datetime import date
from pathlib import Path

import httpx
import pytest

from adlab import digest, export, producer, reporter, review, strategist
from adlab.schemas import Brief, Pattern
from tests.test_producer import env  # noqa: F401 - fixture
from tests.test_strategist import add_analyzed

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg needed")
TODAY = date(2026, 10, 14)


@pytest.fixture()
def made(env, tmp_path):  # noqa: F811
    """Two analyzed source ads -> a pattern -> the env brief points at it -> 3 rendered creatives."""
    store = env["store"]
    a = add_analyzed(store, tmp_path, 31, "Spark", ["problem_callout", "credential", "cta"])
    pat = strategist.build_patterns(store)[0]
    b = env["brief"].model_copy(update={"pattern_id": pat.id, "source_ad_ids": [a]})
    store.put("brief", b, id=b.id, site="testco", ref=pat.id, status="draft")
    res = producer.produce(site="testco", store=store)
    assert len(res.creatives) == 3
    return {"store": store, "pattern": pat, "brief": b, "ids": [c["id"] for c in res.creatives], "source": a}


def test_review_gate(made):
    store, ids = made["store"], made["ids"]
    q = review.queue(store)
    assert len(q) == 3 and q[0].learned_from[0].teardown is not None and q[0].manual_checks
    with pytest.raises(review.ReviewError, match="notes are required"):
        review.decide(store, ids[0], "edit")
    review.decide(store, ids[0], "approve", "ship it")
    review.decide(store, ids[1], "kill")
    review.decide(store, ids[2], "edit", "shorter hook")
    assert review.counts(store) == {"approved": 1, "killed": 1, "edit": 1}
    store.set_status("creative", ids[1], "rejected")
    with pytest.raises(review.ReviewError, match="REJECT"):
        review.decide(store, ids[1], "approve")


def test_export_tags_ad_names_and_checks_specs(made, tmp_path):
    store, ids = made["store"], made["ids"]
    for cid in ids:
        review.decide(store, cid, "approve")
    r = export.export(store, out=tmp_path / "exp")
    assert len(r.files["meta"]) == 3 and "tiktok" not in r.files          # 4.5 s < TikTok's 5 s minimum
    assert any("shorter than 5" in s for s in r.skipped)
    rows = list(csv.DictReader(open(r.sheets["meta"])))
    assert {row["ad_name"].split()[0] for row in rows} == {f"[adlab:{i}]" for i in ids}
    assert rows[0]["ai_label"].startswith("ON") and rows[0]["url"] == "https://test.example"
    assert {row["placements"] for row in rows} >= {"reels + stories", "feed"}


def write_csv(path: Path, rows: list[dict]) -> Path:
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return path


def test_csv_import_reranks_patterns(made, tmp_path):
    store, ids, pat = made["store"], made["ids"], made["pattern"]
    # a second pattern with its own brief + creative that performs badly
    other = add_analyzed(store, tmp_path, 32, "Volt", ["demo", "offer", "cta"], angles=("speed",))
    pat2 = next(p for p in strategist.build_patterns(store) if p.id != pat.id)
    b2 = made["brief"].model_copy(update={"id": "br_two", "pattern_id": pat2.id, "source_ad_ids": [other],
                                          "aspect_ratios": ["9:16"]})
    store.put("brief", b2, id=b2.id, site="testco", ref=pat2.id, status="draft")
    c2 = producer.produce(brief_ids=["br_two"], store=store).creatives[0]["id"]
    name = lambda cid: export.ad_name(cid, "testco", "problem_callout", "9:16")  # noqa: E731
    path = write_csv(tmp_path / "meta.csv", [
        {"Ad name": name(ids[0]), "Day": "2026-10-10", "Campaign ID": "c1", "Impressions": "2,000",
         "Link clicks": "60", "Amount spent (CAD)": "40.00", "Leads": "4"},
        {"Ad name": name(ids[0]), "Day": "2026-10-11", "Campaign ID": "c1", "Impressions": "1000",
         "Link clicks": "30", "Amount spent (CAD)": "20.00", "Leads": "2"},
        {"Ad name": name(c2), "Day": "2026-10-11", "Campaign ID": "c1", "Impressions": "3000",
         "Link clicks": "15", "Amount spent (CAD)": "60.00", "Leads": "0"},
        {"Ad name": "someone else's ad", "Day": "2026-10-11", "Campaign ID": "c1", "Impressions": "50",
         "Link clicks": "1", "Amount spent (CAD)": "1", "Leads": "0"},
        {"Ad name": "", "Day": "", "Campaign ID": "", "Impressions": "6050", "Link clicks": "106",
         "Amount spent (CAD)": "121", "Leads": "6"},                               # totals row
    ])
    r = reporter.import_csv(store, path, "meta")
    assert r.stored == 3 and r.unmatched == 1 and "skipped" in r.warnings[0]
    scores = reporter.rerank(store)
    assert scores[pat.id] > 50 > scores[pat2.id]
    rep = reporter.performance(store)
    assert rep.total.leads == 6 and round(rep.total.cpl, 2) == 20.0
    assert rep.by_pattern[0]["key"] == pat.id and rep.by_hook[0]["key"] == "problem_callout"
    # the Strategist now favours what converts for YOU
    assert strategist.ranked_patterns(store)[0][0].id == pat.id
    assert store.fetch("pattern", pat.id, Pattern).own_performance_score == scores[pat.id]
    # re-import is idempotent
    reporter.import_csv(store, path, "meta")
    assert reporter.performance(store).total.leads == 6


def test_csv_missing_columns_explained(made, tmp_path):
    path = write_csv(tmp_path / "bad.csv", [{"Name": "x", "When": "2026-10-10"}])
    with pytest.raises(reporter.ReportError, match="can't find column"):
        reporter.import_csv(made["store"], path, "tiktok")


def test_meta_insights_pull_paginates(made, monkeypatch):
    monkeypatch.setenv("META_ADS_TOKEN", "tok")
    monkeypatch.setenv("META_AD_ACCOUNT_ID", "123")
    cid = made["ids"][0]
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url)
        if "after" not in str(req.url):
            assert req.url.path == "/v25.0/act_123/insights" and req.url.params["level"] == "ad"
            assert json.loads(req.url.params["time_range"]) == {"since": "2026-10-07", "until": "2026-10-13"}
            return httpx.Response(200, json={"data": [{
                "ad_id": "1", "ad_name": f"[adlab:{cid}] testco x 9:16", "campaign_id": "c9", "impressions": "1500",
                "clicks": "45", "spend": "30.5", "date_start": "2026-10-12", "date_stop": "2026-10-12",
                "account_currency": "CAD", "actions": [{"action_type": "lead", "value": "3"},
                                                       {"action_type": "onsite_conversion.lead_grouped", "value": "3"}]}],
                "paging": {"next": "https://graph.facebook.com/v25.0/act_123/insights?after=abc&access_token=tok"}})
        return httpx.Response(200, json={"data": [{
            "ad_name": "untagged", "campaign_id": "c9", "impressions": "10", "clicks": "0", "spend": "1",
            "date_start": "2026-10-12", "account_currency": "CAD"}], "paging": {}})

    r = reporter.pull_meta(made["store"], 7, http=httpx.Client(transport=httpx.MockTransport(handler)), today=TODAY)
    assert len(seen) == 2 and r.stored == 1 and r.unmatched == 1
    assert reporter.performance(made["store"]).total.leads == 3          # overlapping action types not summed


def test_digest_and_resend(made, monkeypatch):
    d = digest.build(made["store"], today=date.today())
    assert len(d.to_approve) == 3 and d.hooks and len(d.tldr) == 3 and "TOP 5 NEW HOOKS" in d.text
    assert "<h3>" in d.html
    with pytest.raises(RuntimeError, match="RESEND_API_KEY"):
        digest.send(d)
    monkeypatch.setenv("RESEND_API_KEY", "re_x")
    monkeypatch.setenv("DIGEST_FROM", "AdLab <adlab@mail.test.example>")
    monkeypatch.setenv("DIGEST_TO", "me@test.example")

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url == "https://api.resend.com/emails" and req.headers["authorization"] == "Bearer re_x"
        body = json.loads(req.content)
        assert body["to"] == ["me@test.example"] and body["subject"].startswith("AdLab weekly")
        return httpx.Response(200, json={"id": "em_1"})

    assert digest.send(d, http=httpx.Client(transport=httpx.MockTransport(handler))) == "em_1"


def test_api(made, monkeypatch):
    from fastapi.testclient import TestClient

    from adlab.api import app

    cl = TestClient(app)
    home = cl.get("/api/home").json()
    assert home["to_review"]["count"] == 3 and len(home["tldr"]) == 3
    cid = cl.get("/api/queue").json()[0]["id"]
    detail = cl.get(f"/api/creatives/{cid}").json()
    assert detail["compliance"]["verdict"] == "PASS" and detail["learned_from"][0]["teardown"]
    v = cl.get(f"/api/creatives/{cid}/video")
    assert v.status_code == 200 and v.headers["content-type"] == "video/mp4"
    assert cl.post(f"/api/creatives/{cid}/decision", json={"decision": "edit"}).status_code == 409
    assert cl.post(f"/api/creatives/{cid}/decision", json={"decision": "approve"}).json()["decision"] == "approve"
    assert cl.get("/api/queue?status=approved").json()[0]["id"] == cid
    assert cl.get("/api/patterns").json()[0]["id"] == made["pattern"].id
    monkeypatch.setenv("ADLAB_API_TOKEN", "s3cret")
    assert cl.get("/api/home").status_code == 401
    assert cl.get("/api/home", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert cl.get(f"/api/creatives/{cid}/video?token=s3cret").status_code == 200


def test_supabase_mirrors_pipeline_in_fk_order(made):
    from adlab.store import SupabaseMirror, sync

    store = made["store"]
    review.decide(store, made["ids"][0], "approve", "go")
    posts: list[tuple[str, str, list]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        posts.append((req.url.path.rsplit("/", 1)[-1], req.url.params["on_conflict"], json.loads(req.content)))
        return httpx.Response(201)

    mirror = SupabaseMirror("https://p.supabase.co", "k", http=httpx.Client(transport=httpx.MockTransport(handler)))
    out = sync(store, mirror)
    tables = [p[0] for p in posts]
    assert tables == ["ads", "teardowns", "patterns", "briefs", "creatives", "compliance_reports", "reviews"]
    body = dict((p[0], p[2]) for p in posts)
    assert {r["review_status"] for r in body["creatives"]} == {"approved", "pending"}
    assert all(r["ai_generated"] for r in body["creatives"])
    assert not any(r["report_id"].startswith("brief:") for r in body["compliance_reports"])
    assert body["reviews"][0]["notes"] == "go" and out["creatives"] == 3
    posts.clear()
    assert sync(store, mirror) == {"configured": True, "pushed": 0} and posts == []


def test_edit_notes_become_a_revised_brief(made):
    from adlab import llm
    from tests.fakes import FakeAnthropic
    from tests.test_strategist import brief_draft

    store, ids = made["store"], made["ids"]
    review.decide(store, ids[0], "edit", "lead with the credential")
    review.decide(store, ids[1], "edit", "lead with the credential")
    review.decide(store, ids[2], "approve")
    reqs = strategist.edit_requests(store)
    assert set(reqs) == {made["brief"].id}
    assert set(reqs[made["brief"].id]) == {(ids[0], "lead with the credential"), (ids[1], "lead with the credential")}
    fake = FakeAnthropic({"BriefSet": [{"briefs": [brief_draft(script="Red Seal first. Charger acting up?")]}]})
    llm.set_client(fake)
    try:
        dry = strategist.revise_briefs(dry_run=True, store=store)
        assert fake.requests == [] and dry[0].result.status == "planned"
        revs = strategist.revise_briefs(store=store)
    finally:
        llm.set_client(None)
    prompt = fake.requests[0]["messages"][0]["content"][0]["text"]
    assert "lead with the credential" in prompt and made["brief"].script in prompt
    new = revs[0].result.brief
    assert revs[0].result.status == "drafted" and new.pattern_id == made["pattern"].id
    assert store.rows("brief", status="revised")[0]["id"] == made["brief"].id
    assert {r["id"] for r in store.rows("creative", status="revised")} == {ids[0], ids[1]}
    assert review.counts(store) == {"approved": 1, "revised": 2}
    assert strategist.revise_briefs(store=store) == []                  # nothing left to revise


def test_usage_ledger_mirrors_once(made):
    from adlab import usage
    from adlab.store import SupabaseMirror, sync

    usage.record("brief", "anthropic", "claude-opus-5-5", 1000, 100)
    usage.record_service("produce", "elevenlabs", "eleven_multilingual_v2", 0.02, None)
    got = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/llm_usage"):
            got.extend(json.loads(req.content))
            assert req.url.params["on_conflict"] == "entry_key"
        return httpx.Response(201)

    mirror = SupabaseMirror("https://p.supabase.co", "k", http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert sync(made["store"], mirror)["llm_usage"] == 2
    assert got[0]["cost_usd"] == 0.006 and got[1]["cost_usd"] is None and got[1]["units"] == 0.02
    assert "llm_usage" not in sync(made["store"], mirror) and len(got) == 2
