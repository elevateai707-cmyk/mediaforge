"""Ollama/torch VRAM contention fixes.

(a) caption requests carry keep_alive / num_gpu so Ollama unloads the VL model
    instead of squatting on the 4 GB card;
(b) a stage skipped because its model is unavailable must NOT advance the
    asset (it used to land in 'indexed' without an embedding, permanently).
"""
from __future__ import annotations

import time

from PIL import Image

from app import config
from app.ai import captions, clip, ollama_llm, pipeline
from app.db import SessionLocal
from app.models import Asset


def test_payload_has_keep_alive_zero_by_default():
    p = ollama_llm.build_payload("m", "hi", images=["x"])
    assert p["keep_alive"] == 0
    assert "num_gpu" not in p["options"]
    assert p["images"] == ["x"]


def test_payload_keep_alive_and_num_gpu(monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_KEEP_ALIVE", "30s")
    p = ollama_llm.build_payload("m", "hi", num_gpu=0)
    assert p["keep_alive"] == "30s"
    assert p["options"]["num_gpu"] == 0


def test_generate_posts_payload(monkeypatch):
    seen = {}

    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"response": "ok"}

    class _Client:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, json=None):
            seen.update(json)
            return _Resp()

    monkeypatch.setattr(ollama_llm, "_client", lambda timeout=None: _Client())
    assert ollama_llm.generate("m", "p", num_gpu=0) == "ok"
    assert seen["keep_alive"] == 0 and seen["options"]["num_gpu"] == 0


def test_caption_retries_on_cpu_when_gpu_fails(monkeypatch, tmp_path):
    img = tmp_path / "a.jpg"
    Image.new("RGB", (8, 8), "red").save(img)
    calls = []

    def _gen(**kw):
        calls.append(kw.get("num_gpu"))
        if kw.get("num_gpu") is None:
            raise RuntimeError("cuda oom")
        return "a red square"

    monkeypatch.setattr(captions, "_ensure_model_ready", lambda: True)
    monkeypatch.setattr(captions, "generate", _gen)
    monkeypatch.setattr(config, "CAPTION_NUM_GPU", None)
    assert captions.try_caption(str(img)) == "a red square"
    assert calls == [None, 0]


def _asset(tmp_path, name="s.jpg"):
    path = tmp_path / name
    Image.new("RGB", (16, 16), "blue").save(path)
    with SessionLocal() as db:
        a = Asset(path=str(path), hash=f"h-{name}-{time.time()}", kind="photo",
                  status="pending")
        db.add(a)
        db.commit()
        return a.id


def test_clip_unavailable_defers_instead_of_indexing(monkeypatch, tmp_path):
    pipeline.clear_quarantine()
    aid = _asset(tmp_path)
    monkeypatch.setattr(clip, "embed_image", lambda _i: None)
    monkeypatch.setattr(pipeline, "_prepare_clip", lambda: None)
    with SessionLocal() as db:
        res = pipeline.process_asset(db, aid)
        a = db.get(Asset, aid)
        assert res["status"] == "pending" and a.status == "pending"
        assert "deferred" in res and "clip unavailable" in a.status_error
        assert res["stages_run"] == []
    # backoff: not offered again immediately, but not quarantined
    assert aid not in pipeline.next_pending_ids(1000)
    assert aid not in pipeline.quarantined_ids()
    pipeline._retry_after[aid] = time.monotonic() - 1
    assert aid in pipeline.next_pending_ids(1000)
    pipeline.clear_quarantine()


def test_unreadable_image_still_advances(monkeypatch, tmp_path):
    pipeline.clear_quarantine()
    path = tmp_path / "bad.jpg"
    path.write_bytes(b"not an image")
    with SessionLocal() as db:
        a = Asset(path=str(path), hash=f"bad-{time.time()}", kind="photo",
                  status="pending")
        db.add(a)
        db.commit()
        monkeypatch.setattr(clip, "embed_image", lambda _i: None)
        monkeypatch.setattr(pipeline, "_prepare_clip", lambda: None)
        res = pipeline.process_asset(db, a.id)
        assert res["status"] == "indexed"


def test_caption_outage_defers_at_faces_done(monkeypatch, tmp_path):
    pipeline.clear_quarantine()
    aid = _asset(tmp_path, "c.jpg")
    monkeypatch.setattr(config, "SKIP_CAPTIONS", False)
    monkeypatch.setattr(clip, "embed_image", lambda _i: [0.0] * 512)
    monkeypatch.setattr(pipeline, "_prepare_clip", lambda: None)
    monkeypatch.setattr(pipeline, "_prepare_caption", lambda: None)
    monkeypatch.setattr(pipeline, "store_embedding", lambda *a, **k: None)

    def _boom(_p):
        raise captions.CaptionUnavailable("oom")

    monkeypatch.setattr(captions, "try_caption", _boom)
    with SessionLocal() as db:
        res = pipeline.process_asset(db, aid)
        a = db.get(Asset, aid)
        assert a.status == "faces_done"
        assert "caption model unavailable" in a.status_error
        assert "deferred" in res
    # once it has deferred MAX times the fallback caption is accepted
    pipeline._deferrals[aid] = pipeline.MAX_CAPTION_DEFERRALS
    with SessionLocal() as db:
        res = pipeline.process_asset(db, aid)
        assert res["status"] == "indexed"
    pipeline.clear_quarantine()
