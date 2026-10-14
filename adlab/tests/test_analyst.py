"""Phase 3: evidence, copy-guard, Claude teardown (fake client), storage, dry-run."""
from __future__ import annotations

import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from adlab import analyst, ingest as ing, llm, scout, usage
from adlab.schemas import Teardown
from adlab.store import LocalStore
from tests.fakes import FakeAnthropic, teardown_draft
from tests.test_infra import FakeEmbedding
from tests.test_scout import TODAY, make_image

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture()
def store(sites_dir):
    return LocalStore()


def video(path: Path, cuts: bool = True) -> Path:
    """6 s clip; with cuts=True three very different colour segments (2 hard cuts)."""
    src = ("color=c=red:s=360x640:d=2,format=yuv420p[a];color=c=blue:s=360x640:d=2,format=yuv420p[b];"
           "color=c=green:s=360x640:d=2,format=yuv420p[c];[a][b][c]concat=n=3:v=1:a=0") if cuts else \
          "color=c=red:s=360x640:d=6"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, "-f", "lavfi", "-i",
                    "sine=f=440:d=6", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset",
                    "ultrafast", "-c:a", "aac", str(path)], check=True)
    return path


def winner(store, path: Path) -> str:
    r = ing.ingest_media(path, "testco", ing.Sidecar(advertiser="Spark", running_since=date(2026, 5, 1),
                                                       still_running=True, variants=3), store, today=TODAY)
    scout.rescore(store, today=TODAY)
    return r.ad_id


def test_frame_times_dense_in_hook():
    t = analyst.frame_times(20)
    assert t[:4] == [0.2, 1.0, 2.0, 3.0] and len(t) == 10 and t[-1] < 20
    assert analyst.frame_times(2.5) == [0.2, 1.0, 2.0]


def test_copied_run():
    src = ["Get your EV charger installed this week by a licensed Red Seal electrician today"]
    assert analyst.copied_run("they say charger installed this week by a licensed Red Seal electrician", src)
    assert analyst.copied_run("credential shown, then the install", src) is None


@needs_ffmpeg
def test_evidence_measures_pacing(store, tmp_path):
    ad_id = winner(store, video(tmp_path / "v.mp4"))
    ev = analyst.gather(store.get(ad_id), tmp_path / "frames")
    assert ev.kind == "video" and ev.has_audio and abs(ev.duration_s - 6) < 0.3
    assert ev.cuts_per_10s == pytest.approx(2 / ev.duration_s * 10, abs=0.5)
    assert len(ev.frames) == 10 and all(Path(p).is_file() for _, p in ev.frames)


def test_analyze_image_ad_end_to_end(store, tmp_path):
    ad_id = winner(store, make_image(tmp_path / "a.jpg", seed=3))
    fake = FakeAnthropic({"TeardownDraft": [teardown_draft()]})
    llm.set_client(fake)
    coll = None
    try:
        import adlab.vectors as v
        coll = v.collection(v.client(tmp_path / "chroma"), embedding_function=FakeEmbedding())
        items = analyst.run(store=store, coll=coll, whisper=False)
    finally:
        llm.set_client(None)
    assert [i.status for i in items] == ["analyzed"]
    td = store.fetch("teardown", ad_id, Teardown)
    assert td.beats == ["problem_callout", "credential", "demo", "cta"] and td.model == "claude-opus-5-5"
    req = fake.requests[0]
    assert req["model"] == "claude-opus-5-5" and req["output_config"] == {"effort": "high"}
    assert req["fallbacks"] == "default" and req["betas"] == ["server-side-fallback-2026-07-01"]
    blocks = req["messages"][0]["content"]
    assert any(b["type"] == "image" for b in blocks)
    assert "<ad_copy>" in blocks[-1]["text"] and "untrusted" in req["system"]
    assert coll.count() == 1
    assert usage.ledger()[0]["command"] == "analyze" and usage.ledger()[0]["cost_usd"] == 0.078
    assert analyst.candidates(store, None, None, 10) == []        # already analyzed


@needs_ffmpeg
def test_measured_pacing_overrides_model(store, tmp_path):
    ad_id = winner(store, video(tmp_path / "v.mp4"))
    llm.set_client(FakeAnthropic({"TeardownDraft": [teardown_draft()]}))
    try:
        analyst.run(store=store, embed=False, whisper=False)
    finally:
        llm.set_client(None)
    td = store.fetch("teardown", ad_id, Teardown)
    assert abs(td.pacing.length_s - 6) < 0.3 and td.pacing.cuts_per_10s > 2


def test_copy_guard_retries_then_refuses(store, tmp_path):
    img = make_image(tmp_path / "c.jpg", seed=4)
    r = ing.ingest_media(img, "testco", ing.Sidecar(
        advertiser="Spark", running_since=date(2026, 5, 1), still_running=True, variants=3,
        ad_text="Your EV charger installed this week by a licensed Red Seal electrician, guaranteed"), store, today=TODAY)
    scout.rescore(store, today=TODAY)
    copying = teardown_draft(why_it_works="It says your EV charger installed this week by a licensed Red Seal electrician")
    fake = FakeAnthropic({"TeardownDraft": [copying, teardown_draft()]})
    llm.set_client(fake)
    try:
        items = analyst.run(store=store, embed=False, whisper=False)
    finally:
        llm.set_client(None)
    assert items[0].status == "analyzed" and len(fake.requests) == 2
    assert "own words" in fake.requests[1]["messages"][0]["content"][-1]["text"]

    llm.set_client(FakeAnthropic({"TeardownDraft": [copying, copying]}))
    try:
        items = analyst.run(store=store, embed=False, whisper=False, redo=True)
    finally:
        llm.set_client(None)
    assert items[0].status == "failed" and "copying" in items[0].message


def test_refusal_and_link_only_ads(store, tmp_path):
    winner(store, make_image(tmp_path / "r.jpg", seed=8))
    ing.ingest_link("https://x.example/l", "testco", sidecar=ing.Sidecar(
        advertiser="L", running_since=date(2026, 1, 1), still_running=True, variants=5), store=store, today=TODAY)
    scout.rescore(store, today=TODAY)
    llm.set_client(FakeAnthropic({"TeardownDraft": ["refusal"]}))
    try:
        items = analyst.run(store=store, embed=False, whisper=False)
    finally:
        llm.set_client(None)
    assert [i.status for i in items] == ["failed"]        # link-only ad isn't a candidate (no media)
    assert "declined" in items[0].message


def test_dry_run_makes_no_call(store, tmp_path):
    winner(store, make_image(tmp_path / "d.jpg", seed=9))
    fake = FakeAnthropic({})
    llm.set_client(fake)
    try:
        items = analyst.run(store=store, dry_run=True)
    finally:
        llm.set_client(None)
    assert fake.requests == [] and items[0].status == "planned"
    req = items[0].request
    assert req["output_format"] == "TeardownDraft" and req["model"] == "claude-opus-5-5"
    assert any(p.get("type") == "image" and p["bytes"] > 0 for p in req["messages"][0]["content"])
    assert usage.ledger() == []


@needs_ffmpeg
def test_whisper_runs_ephemeral_and_degrades_cleanly(store, tmp_path, monkeypatch):
    """Whisper runs in its own process under the GPU lock, after asking neighbours to free VRAM."""
    import importlib.util

    from adlab import gpu

    monkeypatch.setenv("MEDIAFORGE_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(gpu, "vram", lambda: None)
    ad_id = winner(store, video(tmp_path / "w.mp4"))
    ev = analyst.gather(store.get(ad_id), tmp_path / "frames")
    analyst.transcribe(ev)
    assert any("Media Forge release skipped" in w for w in ev.warnings)
    if importlib.util.find_spec("faster_whisper") is None:
        assert ev.transcript is None and any("faster-whisper not installed" in w for w in ev.warnings)
    log = (tmp_path / "data" / "logs" / "gpu.jsonl").read_text()
    assert f"whisper:{ad_id[:12]}" in log
