"""Phase 5: Producer dry run (zero calls), text-card fallback render, ComfyUI + ElevenLabs path."""
from __future__ import annotations

import io
import json
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
from PIL import Image

from adlab import comfy as comfy_mod, producer, usage
from adlab.config import ROOT, settings
from adlab.schemas import Brief, Creative
from adlab.store import LocalStore
from tests.conftest import brief_data

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg needed")

WORKFLOW = {"3": {"class_type": "KSampler", "inputs": {"seed": "{{seed}}", "steps": 20}},
            "5": {"class_type": "EmptyLatentImage", "inputs": {"width": "{{width}}", "height": "{{height}}"}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{prompt}}"}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{negative}}"}},
            "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "{{filename_prefix}}"}}}


@pytest.fixture()
def env(sites_dir, tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    shutil.copytree(ROOT / "config", cfg, ignore=shutil.ignore_patterns("comfyui"))
    monkeypatch.setenv("ADLAB_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("ADLAB_X264_PRESET", "ultrafast")
    for k in ("COMFYUI_URL", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID"):
        monkeypatch.delenv(k, raising=False)
    store = LocalStore()
    b = Brief.model_validate(brief_data(
        id="br_test", aspect_ratios=["9:16", "4:5", "1:1"],
        shots=[{"index": 1, "seconds": 1, "visual": "garage wall unit, status light off", "asset": "generated_image",
                "on_screen_text": "Charger acting up?", "vo_line": "Charger acting up?"},
               {"index": 2, "seconds": 1, "visual": "certificate on a workbench", "asset": "text_card",
                "on_screen_text": "Installed by a Red Seal Master Electrician"}]))
    store.put("brief", b, id=b.id, site="testco", ref="p1", status="draft")
    return {"store": store, "brief": b, "cfg": cfg}


def no_network(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"dry run made a call: {request.method} {request.url}")


def test_dry_run_prints_payloads_and_spends_nothing(env, monkeypatch):
    (env["cfg"] / "comfyui").mkdir()
    (env["cfg"] / "comfyui" / "image.json").write_text(json.dumps(WORKFLOW))
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_secret")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice123")
    blocked = httpx.Client(transport=httpx.MockTransport(no_network))
    res = producer.produce(site="testco", dry_run=True, store=env["store"], http=blocked,
                           comfy=comfy_mod.ComfyUI("http://comfy:8188", http=blocked))
    plan = res.plans[0]
    assert plan.verdict == "PASS" and plan.will_render and plan.ratios == ["9:16", "4:5", "1:1"]
    assert [s.backend for s in plan.shots] == ["comfyui_image", "text_card"]
    assert plan.vo_script == ["Charger acting up?"]
    assert plan.tagged_facts[0]["fact_key"] == "red_seal"
    p = plan.comfyui_payloads[0]
    wf = p["json"]["prompt"]
    assert p["url"] == "http://comfy:8188/prompt" and len(plan.comfyui_payloads) == 3
    assert wf["5"]["inputs"] == {"width": 768, "height": 1344} and "garage wall unit" in wf["6"]["inputs"]["text"]
    assert "face" in wf["7"]["inputs"]["text"] and isinstance(wf["3"]["inputs"]["seed"], int)
    el = plan.elevenlabs_payloads[0]
    assert el["url"].endswith("/v1/text-to-speech/voice123") and el["headers"]["xi-api-key"] == "***"
    assert el["json"] == {"text": "Charger acting up?", "model_id": "eleven_multilingual_v2"}
    assert plan.mediaforge_calls[0]["url"].endswith("/api/gpu/release")
    assert "sk_secret" not in plan.model_dump_json()
    assert res.batch_id is None and not settings().batches_dir.exists() and usage.ledger() == []
    assert env["store"].rows("compliance") == []          # dry run doesn't even save the pre-check


def test_render_with_text_card_fallback(env):
    res = producer.produce(site="testco", store=env["store"])
    assert res.errors == [] and len(res.creatives) == 3
    store = env["store"]
    for c in res.creatives:
        cr = store.fetch("creative", c["id"], Creative)
        info = producer.probe(Path(cr.path))
        v = next(s for s in info["streams"] if s["codec_type"] == "video")
        assert (v["width"], v["height"]) == producer.RATIO_SIZES[cr.aspect_ratio]
        assert {s["codec_type"] for s in info["streams"]} == {"video", "audio"}
        assert 4.0 <= float(info["format"]["duration"]) <= 4.8          # 1 + 1 + 2.5 end card
        assert "AI-generated" in info["format"]["tags"].get("comment", "")
        assert cr.ai_generated is True and c["status"] == "pending"
    manifest = json.loads(Path(res.manifest).read_text())
    item = manifest["items"][0]
    assert manifest["ai_generated"] and item["ai_generated"] and item["facts_used"][0]["fact_key"] == "red_seal"
    assert any("text card" in f for f in item["fallbacks"]) and item["compliance"]["verdict"] == "PASS"
    assert store.rows("brief", status="produced")


def silent_mp3(seconds: float) -> bytes:
    out = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-t", str(seconds), "-i",
                          "anullsrc=r=44100:cl=mono", "-f", "mp3", "-"], capture_output=True, check=True)
    return out.stdout


def png_bytes(size=(768, 1344)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (40, 90, 160)).save(buf, "PNG")
    return buf.getvalue()


def test_render_with_comfyui_and_voiceover(env, monkeypatch):
    (env["cfg"] / "comfyui").mkdir()
    (env["cfg"] / "comfyui" / "image.json").write_text(json.dumps(WORKFLOW))
    (env["cfg"] / "pricing.yaml").write_text((env["cfg"] / "pricing.yaml").read_text().replace(
        "elevenlabs_per_1k_chars: null", "elevenlabs_per_1k_chars: 0.30"))
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_secret")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice123")
    calls: list[str] = []
    mp3 = silent_mp3(1.6)

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(f"{req.method} {req.url.path}")
        if req.url.path == "/prompt":
            body = json.loads(req.content)
            assert body["prompt"]["6"]["inputs"]["text"].startswith("garage wall unit")
            return httpx.Response(200, json={"prompt_id": "pid1", "number": 1})
        if req.url.path == "/history/pid1":
            return httpx.Response(200, json={"pid1": {"status": {"status_str": "success"}, "outputs": {
                "9": {"images": [{"filename": "gen_00001_.png", "subfolder": "", "type": "output"}]}}}})
        if req.url.path == "/view":
            return httpx.Response(200, content=png_bytes())
        if req.url.path == "/free":
            return httpx.Response(200, json={})
        if req.url.path == "/v1/text-to-speech/voice123":
            assert req.headers["xi-api-key"] == "sk_secret"
            return httpx.Response(200, content=mp3, headers={"content-type": "audio/mpeg"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))

    class FakeMF:
        released = 0

        def release_gpu(self):
            FakeMF.released += 1
            return {"ok": True}

    res = producer.produce(site="testco", store=env["store"], http=client, mediaforge=FakeMF(),
                           comfy=comfy_mod.ComfyUI("http://comfy:8188", http=client), motion=False)
    assert res.errors == [] and len(res.creatives) == 3
    assert calls.count("POST /prompt") == 3 and calls.count("POST /v1/text-to-speech/voice123") == 1
    assert calls[-1] == "POST /free" and FakeMF.released == 1
    dur = producer.duration(Path(res.creatives[0]["path"]))
    assert dur >= 1.6 + 0.25 + 1 + 2.5 - 0.1               # shot 1 stretched to fit its VO
    gpu_log = (settings().logs_dir / "gpu.jsonl").read_text().splitlines()
    assert len(gpu_log) == 3 and all(json.loads(l)["ok"] for l in gpu_log)
    line = [l for l in usage.ledger() if l["provider"] == "elevenlabs"][0]
    assert line["cost_usd"] == round(len("Charger acting up?") / 1000 * 0.30, 6)
    manifest = json.loads(Path(res.manifest).read_text())
    assert manifest["items"][0]["voiceover"] is True and manifest["items"][0]["fallbacks"] == []


def test_rejected_brief_is_never_rendered(env):
    bad = Brief.model_validate(brief_data(id="br_bad", script="Rated 5 stars by our customers"))
    env["store"].put("brief", bad, id=bad.id, site="testco", ref="p1", status="draft")
    res = producer.produce(brief_ids=["br_bad"], store=env["store"])
    assert res.creatives == [] and "REJECT" in res.skipped[0]
    assert env["store"].rows("brief", status="rejected")[0]["id"] == "br_bad"
