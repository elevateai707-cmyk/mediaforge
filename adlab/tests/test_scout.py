"""Phase 2: ingestion, dedupe, variants, scoring, Meta collector, Supabase mirror, CLI."""
from __future__ import annotations

import json
import os
import shutil
import socket
import stat
import subprocess
import time
from datetime import date
from pathlib import Path

import httpx
import pytest
from PIL import Image, ImageDraw

from adlab import ingest as ing
from adlab import scoring, scout, variants
from adlab.collectors import MetaAdLibraryConfig, ScoutConfig, meta_ad_library
from adlab.config import settings
from adlab.schemas import Ad, Signals, SourceRef
from adlab.store import LocalStore, SupabaseMirror, sync

TODAY = date(2026, 10, 14)


@pytest.fixture()
def store(sites_dir):
    return LocalStore()


def make_image(path: Path, seed: int = 0, size=(540, 960), quality: int = 92) -> Path:
    """A busy picture whose dHash depends on `seed`."""
    im = Image.new("RGB", size, (20, 20, 30))
    d = ImageDraw.Draw(im)
    for i in range(12):
        x = (i * 97 + seed * 131) % size[0]
        y = (i * 173 + seed * 59) % size[1]
        d.rectangle((x, y, x + 120, y + 200), fill=((i * 40 + seed * 70) % 255, 200 - i * 9, (seed * 30) % 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, quality=quality)
    return path


def aged(path: Path) -> Path:
    t = time.time() - 60
    os.utime(path, (t, t))
    return path


# --- helpers ---------------------------------------------------------------------------
def test_clean_url_strips_token_and_trackers():
    u = ing.clean_url("https://WWW.facebook.com/ads/library/?id=42&access_token=SECRET&utm_source=x&fbclid=y")
    assert u == "https://www.facebook.com/ads/library/?id=42"
    assert ing.guess_platform(u) == "meta"
    assert ing.guess_platform("https://ads.tiktok.com/business/creativecenter/x") == "tiktok"
    assert ing.guess_platform(None) == "other"


def test_sidecar_parsing_and_typos():
    sc = ing.Sidecar(running_since="2026-07-01T07:00:00+0000", still_running="yes", variants=3)
    assert sc.running_since == date(2026, 7, 1) and sc.still_running is True
    with pytest.raises(ValueError):
        ing.Sidecar(advertisr="typo")
    with pytest.raises(ValueError):
        ing.Sidecar(still_running="maybe")


# --- media ingest + dedupe ----------------------------------------------------------------
def test_ingest_media_dedupes_by_hash_and_keeps_private_copy(store, tmp_path):
    img = make_image(tmp_path / "a.jpg")
    r1 = ing.ingest_media(img, "testco", ing.Sidecar(advertiser="Acme", running_since=date(2026, 8, 1)),
                          store, today=date(2026, 9, 1))
    assert r1.status == "new" and r1.ad_id == ing.sha256_file(img)
    ad = store.get(r1.ad_id)
    copy = Path(ad.media_paths[0])
    assert copy.is_file() and copy.parent.parent == settings().media_dir
    assert stat.S_IMODE(copy.stat().st_mode) == 0o600
    assert stat.S_IMODE(settings().media_dir.stat().st_mode) == 0o700

    shutil.copy(img, tmp_path / "renamed.jpg")   # same bytes, different name
    r2 = ing.ingest_media(tmp_path / "renamed.jpg", "testco", ing.Sidecar(still_running=True), store, today=TODAY)
    assert r2.status == "updated" and r2.ad_id == r1.ad_id
    ad = store.get(r1.ad_id)
    assert (ad.first_seen, ad.last_seen) == (date(2026, 8, 1), TODAY)
    assert ad.signals.times_seen == 2 and ad.signals.still_running is True
    assert len(ad.media_paths) == 1 and len(store.ads()) == 1
    assert store.media_for([ad.id])[ad.id][0]["dhash"]


def test_ingest_link_is_never_fetched_and_attaches_file_later(store, tmp_path, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("ingest tried to open a network connection")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    url = "https://www.facebook.com/ads/library/?id=777&access_token=SECRET"
    r = ing.ingest_link(url, "testco", sidecar=ing.Sidecar(advertiser="Volt Co"), store=store, today=TODAY)
    assert r.status == "new"
    ad = store.get(r.ad_id)
    assert str(ad.source.url) == "https://www.facebook.com/ads/library/?id=777"
    assert ad.source.captured_via == "manual_link" and ad.source.platform == "meta" and not ad.media_paths

    img = make_image(tmp_path / "saved.png")
    r2 = ing.ingest_link(url, "testco", file=img, store=store, today=TODAY)
    assert r2.status == "updated" and r2.ad_id == r.ad_id
    assert len(store.get(r.ad_id).media_paths) == 1 and len(store.ads()) == 1


def test_stopped_ad_does_not_gain_longevity_it_wasnt_seen_running(store):
    """Seeing a stopped ad today must not credit it with running until today."""
    r = ing.ingest_link("https://x.example/stopped", "testco", sidecar=ing.Sidecar(
        advertiser="A", running_since=date(2026, 7, 15), still_running=False), store=store, today=TODAY)
    ad = store.get(r.ad_id)
    assert ad.last_seen == date(2026, 7, 15)
    assert scoring.score(ad, today=TODAY).breakdown["longevity"] == 0
    ing.ingest_link("https://x.example/stopped", "testco", sidecar=ing.Sidecar(last_seen=date(2026, 9, 1)),
                    store=store, today=TODAY)
    assert store.get(r.ad_id).last_seen == date(2026, 9, 1)            # an explicit stop date counts


def test_unknown_site_and_bad_type_fail(store, tmp_path):
    assert ing.ingest_link("https://x.example/ad", "nosuchsite", store=store).status == "failed"
    bad = tmp_path / "a.gif"
    bad.write_bytes(b"GIF89a")
    assert ing.ingest_media(bad, "testco", store=store).status == "failed"
    assert ing.ingest_link("ftp://x/ad", "testco", store=store).status == "failed"


# --- the incoming folder -----------------------------------------------------------------
def test_process_incoming_routes_and_parks(store, tmp_path):
    inc = settings().incoming_dir
    good = aged(make_image(inc / "testco" / "winner.jpg", seed=1))
    (inc / "testco" / "winner.jpg.yaml").write_text("advertiser: Spark Electric\nplatform: meta\nvariants: 4\n")
    aged(make_image(inc / "loose.jpg", seed=2))                  # no site folder, no sidecar site
    aged(make_image(inc / "testco" / "typo.jpg", seed=3))
    (inc / "testco" / "typo.yaml").write_text("advertizer: oops\n")
    (inc / "testco" / "notes.txt").write_text("hi")
    aged(inc / "testco" / "notes.txt")
    make_image(inc / "testco" / "still-copying.jpg", seed=4)     # too new: waits

    results = ing.process_incoming(store, today=TODAY)
    by_name = {Path(r.source).name: r for r in results}
    assert by_name["winner.jpg"].status == "new"
    ad = store.get(by_name["winner.jpg"].ad_id)
    assert ad.source.advertiser == "Spark Electric" and ad.signals.reported_variants == 4
    for name in ("loose.jpg", "typo.jpg", "notes.txt"):
        assert by_name[name].status == "failed", name
    assert "which site?" in by_name["loose.jpg"].message
    assert "bad sidecar" in by_name["typo.jpg"].message

    done = inc / "_done" / date.today().isoformat()
    failed = inc / "_failed" / date.today().isoformat()
    assert (done / "winner.jpg").is_file() and (done / "winner.jpg.yaml").is_file()
    assert (failed / "loose.jpg.error.txt").is_file()
    assert not good.exists() and (inc / "testco" / "still-copying.jpg").is_file()
    assert ing.process_incoming(store, today=TODAY) == []          # nothing left to do (young file waits)


def test_csv_and_meta_json_rows(store, tmp_path):
    inc = settings().incoming_dir / "testco"
    inc.mkdir(parents=True)
    (inc / "export.csv").write_text(
        "advertiser,ad_text,url,first_seen,still_running,variants\n"
        "Bolt Bros,Charger dead? We fix it today.,https://www.facebook.com/ads/library/?id=1,2026-06-01,yes,3\n"
        "Bolt Bros,,,,,\n")
    (inc / "meta.json").write_text(json.dumps({"data": [
        {"id": "998", "page_name": "EU Installer", "ad_creative_bodies": ["Home charging, sorted."],
         "ad_delivery_start_time": "2026-05-02T07:00:00+0000"}]}))
    (inc / "meta.json.yaml").write_text("platform: meta\n")
    aged(inc / "export.csv"), aged(inc / "meta.json")
    results = ing.process_incoming(store, today=TODAY)
    s = ing.summarize(results)
    assert s == {"new": 2, "updated": 0, "failed": 1}
    eu = next(a for a in store.ads() if a.source.advertiser == "EU Installer")
    assert str(eu.source.url) == "https://www.facebook.com/ads/library/?id=998"
    assert eu.first_seen == date(2026, 5, 2) and eu.source.platform == "meta"
    bolt = next(a for a in store.ads() if a.source.advertiser == "Bolt Bros")
    assert bolt.signals.still_running and bolt.signals.reported_variants == 3


# --- variants + scoring ------------------------------------------------------------------
def test_variant_grouping_visual_and_text(store, tmp_path):
    a = make_image(tmp_path / "a.jpg", seed=5, quality=95)
    with Image.open(a) as im:                                           # same concept, re-encoded smaller
        im.resize((432, 768)).save(tmp_path / "b.jpg", quality=60)
    c = make_image(tmp_path / "c.jpg", seed=9)
    for p, adv in ((a, "Acme"), (tmp_path / "b.jpg", "Acme"), (c, "Acme")):
        ing.ingest_media(p, "testco", ing.Sidecar(advertiser=adv), store, today=TODAY)
    ing.ingest_link("https://x.example/1", "testco", sidecar=ing.Sidecar(
        advertiser="Other", ad_text="Level 2 charger installed this week by a licensed pro"), store=store, today=TODAY)
    ing.ingest_link("https://x.example/2", "testco", sidecar=ing.Sidecar(
        advertiser="Other", ad_text="Level 2 charger installed this week by licensed pros!"), store=store, today=TODAY)

    n, _, ads = scout.rescore(store, today=TODAY)
    groups = {a.id: a.variant_group for a in ads}
    ida, idb, idc = (ing.sha256_file(p) for p in (a, tmp_path / "b.jpg", c))
    assert groups[ida] and groups[ida] == groups[idb]
    assert groups[idc] is None
    other = [a for a in ads if a.source.advertiser == "Other"]
    assert other[0].variant_group and other[0].variant_group == other[1].variant_group
    assert store.get(ida).score.variant_count == 2


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_video_fingerprint(tmp_path):
    out = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=30:duration=2",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", str(out)], check=True)
    h = variants.dhash_video(out)
    assert h and len(h) == 16


def ad(**over) -> Ad:
    base = dict(id="x" * 16, site="testco", first_seen=date(2026, 6, 1), last_seen=date(2026, 6, 10),
                source=SourceRef(platform="meta", captured_via="manual_drop", advertiser="A"))
    base.update(over)
    return Ad(**base)


def test_scoring_breakdown():
    s = scoring.score(ad(signals=Signals(still_running=True, platform_rank=7)), group_size=3, today=TODAY)
    assert s.longevity_days == (TODAY - date(2026, 6, 1)).days
    assert s.breakdown == {"longevity": 35, "still_running": 5, "variants": 10, "platform_rank": 20, "repeat_pattern": 0}
    assert s.total == 70 and scoring.explain(s) == "70 = longevity 35 + still running 5 + variants 10 + platform rank 20"
    stopped = scoring.score(ad(signals=Signals(still_running=False)), today=TODAY)
    assert stopped.longevity_days == 9 and stopped.total == 0
    big = scoring.score(ad(signals=Signals(still_running=True, reported_variants=20, platform_rank=1)),
                        repeat_pattern_count=10, today=TODAY)
    assert big.total == 100


def test_new_winners_reported_once(store, tmp_path):
    img = make_image(tmp_path / "w.jpg", seed=11)
    ing.ingest_media(img, "testco", ing.Sidecar(advertiser="Spark", running_since=date(2026, 5, 1),
                                                 still_running=True, variants=3), store, today=TODAY)
    first = scout.run(store=store, today=TODAY, collectors=False)
    assert [w.advertiser for w in first.new_winners] == ["Spark"] and first.top[0].score == 50
    again = scout.run(store=store, today=TODAY, collectors=False)
    assert again.new_winners == [] and again.top[0].score == 50


# --- Meta Ad Library collector ---------------------------------------------------------------
def meta_cfg(**over) -> ScoutConfig:
    return ScoutConfig(keywords=["ev charger install"],
                       meta_ad_library=MetaAdLibraryConfig(enabled=True, **over))


def test_meta_collector_maps_pages_and_never_stores_token(store):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url)
        assert req.url.path == "/v26.0/ads_archive"
        q = dict(req.url.params)
        assert q["access_token"] == "TOKEN123" and q["ad_type"] == "ALL"
        assert json.loads(q["ad_reached_countries"]) == ["IE"]
        if "after" not in q:
            return httpx.Response(200, json={
                "data": [{"id": "1", "page_name": "Dublin EV", "ad_creative_bodies": ["Charge at home."],
                          "ad_delivery_start_time": "2026-04-01T00:00:00+0000", "publisher_platforms": ["facebook"]}],
                "paging": {"next": str(req.url.copy_merge_params({"after": "c2"}))}})
        return httpx.Response(200, json={"data": [
            {"id": "2", "page_name": "Cork Sparks", "ad_creative_bodies": ["Installed in a day."],
             "ad_delivery_start_time": "2026-03-01T00:00:00+0000", "ad_delivery_stop_time": "2026-05-01T00:00:00+0000"}]})

    run = meta_ad_library("testco", meta_cfg(), store=store, token="TOKEN123",
                          http=httpx.Client(transport=httpx.MockTransport(handler)), today=TODAY, pause_s=0)
    assert run.stopped is None and run.pages == 2 and len(run.results) == 2
    dublin = next(a for a in store.ads() if a.source.advertiser == "Dublin EV")
    cork = next(a for a in store.ads() if a.source.advertiser == "Cork Sparks")
    assert dublin.signals.still_running is True and dublin.source.captured_via == "meta_ad_library_api"
    assert cork.signals.still_running is False and cork.last_seen == date(2026, 5, 1)
    assert str(dublin.source.url) == "https://www.facebook.com/ads/library/?id=1"
    assert b"TOKEN123" not in (settings().data_dir / "adlab.db").read_bytes()


def test_meta_collector_stops_on_rate_limit_and_guards(store):
    def handler(req):
        return httpx.Response(400, json={"error": {"code": 613, "message": "Calls to this api have exceeded the rate limit."}})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    run = meta_ad_library("testco", meta_cfg(), store=store, token="t", http=http, pause_s=0)
    assert "rate limit" in run.stopped and run.pages == 0
    assert meta_ad_library("testco", ScoutConfig(), store=store, token="t").stopped == "disabled in scout.yaml"
    assert "TOKEN not set" in meta_ad_library("testco", meta_cfg(), store=store, token="").stopped


# --- Supabase mirror + scheduler + CLI ----------------------------------------------------------
def test_supabase_sync(store, tmp_path):
    ing.ingest_link("https://x.example/ad", "testco", sidecar=ing.Sidecar(advertiser="A"), store=store, today=TODAY)
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"], seen["headers"], seen["body"] = req.url, req.headers, json.loads(req.content)
        return httpx.Response(201)

    mirror = SupabaseMirror("https://proj.supabase.co", "service-key", http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert sync(store, mirror) == {"configured": True, "pushed": 1}
    assert seen["url"].path == "/rest/v1/ads" and seen["url"].params["on_conflict"] == "id"
    assert seen["headers"]["apikey"] == "service-key" and "merge-duplicates" in seen["headers"]["prefer"]
    row = seen["body"][0]
    assert row["advertiser"] == "A" and row["captured_via"] == "manual_link" and row["payload"]["site"] == "testco"
    assert sync(store, mirror)["pushed"] == 0                      # nothing changed since
    assert sync(store, SupabaseMirror("", ""))["configured"] is False


def test_weekly_schedule():
    sched = scout.scheduler(blocking=False)
    job = sched.get_job("weekly_api_refresh")
    trigger = str(job.trigger)
    assert "day_of_week='mon'" in trigger and "hour='6'" in trigger and "minute='47'" in trigger
    assert str(job.trigger.timezone) == "America/Edmonton"


def test_cli_ingest_and_scout(sites_dir, tmp_path):
    from typer.testing import CliRunner

    from adlab.cli import app

    img = make_image(tmp_path / "ad.png", seed=21)
    r = CliRunner().invoke(app, ["ingest", "https://www.facebook.com/ads/library/?id=5", str(img),
                                 "--site", "testco", "--advertiser", "Watt Now", "--running-since", "2026-06-01",
                                 "--running", "--variants", "3"])
    assert r.exit_code == 0, r.output
    assert r.output.startswith("TL;DR") and "1 new ads" in r.output
    r = CliRunner().invoke(app, ["scout", "--no-api"])
    assert r.exit_code == 0, r.output
    assert "1 new winners" in r.output and "Watt Now" in r.output
    r = CliRunner().invoke(app, ["ads"])
    assert "★" in r.output and "longevity 35" in r.output
    r = CliRunner().invoke(app, ["ingest", "https://x.example/a", "--site", "testco", "--platform", "myspace"])
    assert r.exit_code == 2 and "Traceback" not in r.output
