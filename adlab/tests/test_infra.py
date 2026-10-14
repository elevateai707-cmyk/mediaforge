"""Supabase schema, ChromaDB, GPU lock, cost ledger, Media Forge wrapper, CLI."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import httpx
import pytest

from adlab import gpu, usage, vectors
from adlab.config import ROOT, settings
from adlab.mediaforge import MediaForge, MediaForgeError, find_studio_cli, png_size, render_still
from tests.test_schemas import teardown

MIGRATION = ROOT / "supabase" / "migrations" / "20261014000000_adlab_init.sql"
TABLES = {"ads", "teardowns", "patterns", "briefs", "creatives", "compliance_reports", "results", "llm_usage"}


# --- Supabase ------------------------------------------------------------------
def test_migration_parses_and_locks_every_table():
    pglast = pytest.importorskip("pglast")
    sql = MIGRATION.read_text()
    stmts = pglast.parse_sql(sql)
    created = {s.stmt.relation.relname for s in stmts if type(s.stmt).__name__ == "CreateStmt"}
    assert created == TABLES
    rls = {s.stmt.relation.relname for s in stmts
           if type(s.stmt).__name__ == "AlterTableStmt" and "enable row level security" in
           sql[s.stmt_location:s.stmt_location + s.stmt_len].lower()}
    assert rls == TABLES, "every table must have RLS enabled"
    assert "check (ai_generated)" in sql


# --- ChromaDB -----------------------------------------------------------------
class FakeEmbedding:
    """Deterministic bag-of-words embedding (no model download in tests)."""
    VOCAB = ["question", "problem_callout", "savings", "trust", "speed", "talking_head", "demo", "ugc"]

    def __call__(self, input):  # noqa: A002 - chroma's signature
        return [[float(doc.count(w)) + 0.01 for w in self.VOCAB] for doc in input]

    def embed_query(self, input):  # noqa: A002
        return self(input)

    def embed_documents(self, input):  # noqa: A002
        return self(input)

    @staticmethod
    def name() -> str:
        return "adlab-fake"

    def is_legacy(self) -> bool:
        return False

    def default_space(self) -> str:
        return "cosine"

    def supported_spaces(self) -> list[str]:
        return ["cosine"]

    def get_config(self) -> dict:
        return {}

    @staticmethod
    def build_from_config(config):
        return FakeEmbedding()


def test_teardown_collection_roundtrip(tmp_path):
    chroma = vectors.client(tmp_path / "chroma")
    coll = vectors.collection(chroma, embedding_function=FakeEmbedding())
    vectors.add_teardown(coll, "t1", teardown(), site="ev1charger", score=81)
    vectors.add_teardown(coll, "t2", teardown(ad_id="ad2", hook={"type": "demo", "seconds": 1, "visual": "x"},
                                              angles=["speed"], format="demo"), site="albertaweb")
    hits = vectors.search(coll, "question savings trust", n=2)
    assert hits[0]["id"] == "t1" and hits[0]["metadata"]["score"] == 81
    only = vectors.search(coll, "anything", n=2, where={"site": "albertaweb"})
    assert [h["id"] for h in only] == ["t2"]
    doc = vectors.teardown_document(teardown())
    assert "Hook: question" in doc and "Why it works" in doc


# --- GPU lock -------------------------------------------------------------------
def test_gpu_job_logs_and_is_exclusive(monkeypatch):
    monkeypatch.setattr(gpu, "vram", lambda: {"used_mb": 1000, "free_mb": 9000, "total_mb": 10240})
    with gpu.gpu_job("first") as rec:
        rec["note"] = "x"
        with pytest.raises(TimeoutError):
            with gpu.gpu_job("second", wait_s=0.3):
                pass
    lines = [json.loads(l) for l in (settings().logs_dir / "gpu.jsonl").read_text().splitlines()]
    first = next(l for l in lines if l["job"] == "first")
    assert first["ok"] and first["vram_before"]["free_mb"] == 9000 and first["note"] == "x"


def test_gpu_job_refuses_when_vram_low(monkeypatch):
    monkeypatch.setattr(gpu, "vram", lambda: {"used_mb": 9500, "free_mb": 740, "total_mb": 10240})
    with pytest.raises(RuntimeError, match="only 740 MB free"):
        with gpu.gpu_job("whisper", min_free_mb=3000):
            pass


def test_run_ephemeral(monkeypatch):
    monkeypatch.setattr(gpu, "vram", lambda: None)
    proc = gpu.run_ephemeral(["python3", "-c", "print('hi')"], "echo")
    assert proc.returncode == 0 and proc.stdout.strip() == "hi"


# --- cost ledger -----------------------------------------------------------------
def test_cost_and_ledger():
    assert usage.cost_usd("claude-opus-5-5", 1_000_000, 100_000) == 6.0
    assert usage.cost_usd("claude-opus-5-5", 0, 0, cache_read_tokens=1_000_000) == 0.2
    assert usage.cost_usd("not-a-model", 10, 10) is None
    assert usage.cost_usd("claude-haiku-5-5", 10, 10) is not None
    assert usage.cost_usd("claude-haiku-5-5", 10, 10, cache_read_tokens=5) is None  # unknown, not guessed
    printed = []
    with usage.track("analyze", echo=printed.append):
        usage.record("analyze", "anthropic", "claude-opus-5-5", 20_000, 3_000)
        usage.record("analyze", "anthropic", "mystery-model", 1, 1)
    assert "analyze: 2 calls" in printed[0] and "$0.1400" in printed[0] and "1 unpriced" in printed[0]
    assert len(usage.ledger()) == 2


# --- Media Forge -------------------------------------------------------------------
def _mf(handler) -> MediaForge:
    mf = MediaForge("http://mf.test")
    mf._http = httpx.Client(base_url="http://mf.test", transport=httpx.MockTransport(handler))
    return mf


def test_mediaforge_release_and_errors():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/gpu/release" and req.method == "POST":
            return httpx.Response(200, json={"released": ["clip"], "vram_free_mb_after": 9000})
        if req.url.path == "/api/health":
            return httpx.Response(200, json={"status": "ok", "gpu": "cuda"})
        return httpx.Response(404, json={"detail": "Not Found"})

    mf = _mf(handler)
    assert mf.release_gpu()["released"] == ["clip"]
    st = mf.status()
    assert st["health"]["gpu"] == "cuda" and st["studio"]["available"] is False
    with pytest.raises(MediaForgeError, match="unreachable"):
        MediaForge("http://127.0.0.1:9").health()


@pytest.mark.skipif(find_studio_cli() is None, reason="Elevate Vision Studio CLI not installed")
@pytest.mark.parametrize("ratio, size", [("9:16", (1080, 1920)), ("4:5", (1080, 1350))])
def test_render_still(tmp_path, ratio, size):
    out = render_still(["AdLab test"], ratio, tmp_path / "x.png")
    assert png_size(out) == size


def test_render_still_without_cli(monkeypatch, tmp_path):
    monkeypatch.setenv("STUDIO_CLI", str(tmp_path / "nope"))
    with pytest.raises(MediaForgeError, match="not found"):
        render_still(["x"], "9:16", tmp_path / "x.png")


# --- CLI ---------------------------------------------------------------------------
def test_cli_sites_facts_and_pipeline_commands(sites_dir):
    from typer.testing import CliRunner

    from adlab.cli import app

    r = CliRunner().invoke(app, ["sites"])
    assert r.exit_code == 0, r.output
    assert r.output.startswith("TL;DR") and "testco" in r.output and "facts 2/4 usable" in r.output
    r = CliRunner().invoke(app, ["facts", "testco"])
    assert "✓ red_seal" in r.output and "✗ price_from: not verified" in r.output
    r = CliRunner().invoke(app, ["brief", "--site", "testco"])
    assert r.exit_code == 0 and "blocked: no patterns yet" in r.output
    for cmd in (["review"], ["produce", "--dry-run"], ["export"], ["report"], ["check"], ["digest"]):
        r = CliRunner().invoke(app, cmd)
        assert r.exit_code == 0 and (r.output.startswith("TL;DR") or r.output.startswith("AdLab weekly")), (cmd, r.output)
