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
