"""System endpoints: /api/health and /api/config."""


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["gpu"] in ("cuda", "cpu")
    assert isinstance(data["gpu_name"], str)
    assert isinstance(data["vram_mb"], int)
    assert isinstance(data["version"], str) and data["version"]
    assert isinstance(data["models"], dict)
    assert isinstance(data["warnings"], list)


def test_config(client):
    r = client.get("/api/config")
    assert r.status_code == 200
    data = r.json()
    assert data["use_cloud_llm"] is False
    assert isinstance(data["media_dirs"], list) and data["media_dirs"]
    assert data["ollama_model"] == "qwen2.5vl:7b"
    assert isinstance(data["resolve_available"], bool)


def test_gpu_release(client, monkeypatch):
    """Unloads every model, asks Ollama to evict, reports VRAM + running jobs."""
    from app.ai import clip, faces, ollama_llm, whisper

    calls = []
    for name, mod in (("clip", clip), ("whisper", whisper), ("faces", faces)):
        monkeypatch.setattr(mod, "unload", lambda n=name: calls.append(n))
    monkeypatch.setattr(ollama_llm, "unload_model", lambda model=None: calls.append("ollama") or True)
    r = client.post("/api/gpu/release")
    assert r.status_code == 200, r.text
    data = r.json()
    assert calls == ["clip", "whisper", "faces", "ollama"]
    assert data["released"] == ["clip", "whisper", "faces"] and data["ollama_unloaded"] is True
    assert data["gpu"] in ("cuda", "cpu") and isinstance(data["running_jobs"], list)
    if data["gpu"] == "cpu":
        assert data["vram_free_mb_before"] is None and data["vram_free_mb_after"] is None
