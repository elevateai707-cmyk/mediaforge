from pathlib import Path
import json
import uuid

import httpx
import pytest
from app import models
from app.db import SessionLocal
from app.providers import api, comfy, elevenlabs, settings


@pytest.fixture(autouse=True)
def keys(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "SECRET_FILE", tmp_path / "keys.json")
    monkeypatch.delenv("COMFY_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr(comfy, "key", settings.key)
    monkeypatch.setattr(elevenlabs, "key", settings.key)


def test_missing_keys_and_secret_storage(client):
    assert (
        client.post(
            "/api/providers/review",
            json={"provider": "elevenlabs", "script": "hi", "voice": "v", "model": "m"},
        ).status_code
        == 409
    )
    r = client.put(
        "/api/providers/settings",
        json={"comfy_key": "private-test-key", "elevenlabs_key": "second-private-key"},
    )
    assert r.status_code == 200
    assert "private-test-key" not in r.text
    assert settings.SECRET_FILE.stat().st_mode & 0o777 == 0o600


def test_comfy_sdk_actual_v2_contract(monkeypatch):
    settings.set_key("comfy", "test-secret")
    calls = []
    remote = str(uuid.uuid4())

    def handler(req):
        calls.append(req)
        if req.url.path == "/api/object_info":
            assert req.headers["X-API-Key"] == "test-secret"
            return httpx.Response(
                200, json={"SaveImage": {"input": {"required": {"images": ["IMAGE"]}}}}
            )
        assert req.url.path == "/api/v2/jobs"
        assert req.headers["Authorization"] == "Bearer test-secret"
        assert req.headers["Idempotency-Key"] == "stable-id"
        body = json.loads(req.content)
        assert body["workflow"]["1"]["inputs"]["images"] == "hello"
        return httpx.Response(
            201,
            json={
                "id": remote,
                "status": "queued",
                "created_at": "2026-09-16T00:00:00Z",
                "started_at": None,
                "completed_at": None,
                "expires_at": "2026-09-17T00:00:00Z",
                "queue_position": 1,
                "progress": None,
                "outputs": [],
                "error": None,
                "urls": {
                    "self": f"https://cloud.comfy.org/api/v2/jobs/{remote}",
                    "events": "",
                    "cancel": "",
                    "logs": "",
                    "workflow": "",
                },
            },
        )

    real = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda *a, **kw: real(*a, **kw, transport=httpx.MockTransport(handler)),
    )
    result = comfy.submit(
        {
            "workflow": {"1": {"class_type": "SaveImage", "inputs": {"images": "x"}}},
            "mappings": {"prompt": {"node": "1", "input": "images", "type": "text"}},
            "inputs": {"prompt": "hello"},
        },
        {},
        "stable-id",
    )
    assert result == remote
    assert len(calls) == 2


@pytest.mark.parametrize("status", [401, 403, 402, 429, 500])
def test_provider_errors_redacted(monkeypatch, status):
    settings.set_key("elevenlabs", "secret-never-print")

    def request(*a, **kw):
        raise httpx.HTTPStatusError(
            "secret-never-print",
            request=httpx.Request("GET", "https://example.com"),
            response=httpx.Response(status),
        )

    monkeypatch.setattr(elevenlabs, "request", request)
    with pytest.raises(Exception) as e:
        api.test("elevenlabs")
    assert "secret-never-print" not in str(e.value)


def test_timeout_does_not_duplicate(client, monkeypatch):
    settings.set_key("elevenlabs", "fake")
    settings.save_configuration({"max_daily_jobs": 1000})
    body = {
        "provider": "elevenlabs",
        "script": "Uncertain outcome " + uuid.uuid4().hex,
        "voice": "voice",
        "model": "model",
    }
    r = client.post("/api/providers/review", json=body)
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    calls = []

    def timeout(*args):
        calls.append(1)
        raise httpx.ReadTimeout("hidden secret")

    monkeypatch.setattr(elevenlabs, "generate", timeout)
    assert (
        client.post(
            f"/api/providers/jobs/{jid}/generate", json={"confirmed": False}
        ).status_code
        == 422
    )
    client.post(f"/api/providers/jobs/{jid}/generate", json={"confirmed": True})
    import time

    for _ in range(30):
        if client.get(f"/api/providers/jobs/{jid}").json()["status"] == "unknown":
            break
        time.sleep(0.02)
    for _ in range(3):
        client.post(f"/api/providers/jobs/{jid}/generate", json={"confirmed": True})
    assert len(calls) == 1
    assert client.post("/api/providers/review", json=body).json()["id"] == jid


def test_durable_comfy_poll_and_cancel(client, monkeypatch, tmp_path):
    jid = str(uuid.uuid4())
    with SessionLocal() as db:
        db.add(
            models.ProviderJob(
                id=jid,
                provider="comfy",
                cache_key=jid,
                status="running",
                remote_id="persisted-remote",
                request_json="{}",
            )
        )
        db.commit()
    calls = []
    monkeypatch.setattr(
        comfy,
        "poll",
        lambda remote, directory: (
            calls.append(remote) or {"status": "succeeded", "assets": []}
        ),
    )
    api.poll_one(jid)
    api.poll_one(jid)
    assert calls == ["persisted-remote"]
    assert client.get(f"/api/providers/jobs/{jid}").json()["status"] == "succeeded"
    # A reviewed job can be cancelled without contacting either provider.
    settings.set_key("elevenlabs", "fake")
    r = client.post(
        "/api/providers/review",
        json={
            "provider": "elevenlabs",
            "script": "cancel " + jid,
            "voice": "v",
            "model": "m",
        },
    )
    assert (
        client.post("/api/providers/jobs/" + r.json()["id"] + "/cancel").json()[
            "status"
        ]
        == "canceled"
    )


def test_alignment_and_workflow_validation():
    cues = elevenlabs.alignment_cues(
        {
            "characters": list("Hi, café!"),
            "character_start_times_seconds": [i * 0.1 for i in range(9)],
            "character_end_times_seconds": [(i + 1) * 0.1 for i in range(9)],
        }
    )
    assert cues[0]["text"] == "Hi, café!"
    assert cues[0]["end"] == pytest.approx(0.9)
    with pytest.raises(ValueError):
        elevenlabs.alignment_cues(
            {
                "characters": ["x"],
                "character_start_times_seconds": [],
                "character_end_times_seconds": [1],
            }
        )
    with pytest.raises(ValueError):
        comfy.validate({"nodes": []}, {})
    with pytest.raises(ValueError):
        comfy.validate({"1": {"class_type": "Missing", "inputs": {}}}, {}, {})
    with pytest.raises(ValueError):
        comfy.validate(
            {"1": {"class_type": "Test", "inputs": {}}},
            {"x": {"node": "2", "input": "image", "type": "asset"}},
        )


def test_local_count_limit(client):
    settings.set_key("elevenlabs", "fake")
    settings.save_configuration({"max_daily_jobs": 0})
    r = client.post(
        "/api/providers/review",
        json={
            "provider": "elevenlabs",
            "script": uuid.uuid4().hex,
            "voice": "v",
            "model": "m",
        },
    )
    assert (
        client.post(
            "/api/providers/jobs/" + r.json()["id"] + "/generate",
            json={"confirmed": True},
        ).status_code
        == 429
    )
    settings.save_configuration({"max_daily_jobs": 10})


def test_elevenlabs_wire_contract_and_cache(client, monkeypatch, tmp_path):
    import base64

    settings.set_key("elevenlabs", "fake-key")
    settings.save_configuration({"max_daily_jobs": 1000})
    # Timestamp parsing tested against the real response shape; audio probe is isolated.
    from app.edits import render

    monkeypatch.setattr(render, "probe_json", lambda _: {"format": {"duration": "1"}})
    observed = []

    def handler(req):
        observed.append(req)
        assert req.url.path == "/v1/text-to-speech/voice-id/with-timestamps"
        assert req.headers["xi-api-key"] == "fake-key"
        assert json.loads(req.content)["model_id"] == "model-id"
        return httpx.Response(
            200,
            json={
                "audio_base64": base64.b64encode(b"fixture-audio").decode(),
                "alignment": {
                    "characters": ["H", "i"],
                    "character_start_times_seconds": [0, 0.2],
                    "character_end_times_seconds": [0.2, 0.4],
                },
            },
        )

    real = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda *a, **kw: real(*a, **kw, transport=httpx.MockTransport(handler)),
    )
    body = {"script": "Hi", "voice": "voice-id", "model": "model-id", "settings": {}}
    result = elevenlabs.generate(body, tmp_path)
    assert result["cues"][0]["text"] == "Hi"
    assert Path(result["assets"][0]).read_bytes() == b"fixture-audio"
    assert len(observed) == 1


def test_rejected_submission_can_be_explicitly_retried(client, monkeypatch):
    settings.set_key("elevenlabs", "fake")
    settings.save_configuration({"max_daily_jobs": 1000})
    body = {
        "provider": "elevenlabs",
        "script": "reject " + uuid.uuid4().hex,
        "voice": "v",
        "model": "m",
    }
    jid = client.post("/api/providers/review", json=body).json()["id"]

    def reject(*_):
        raise httpx.HTTPStatusError(
            "bad credentials",
            request=httpx.Request("POST", "https://example.com"),
            response=httpx.Response(401),
        )

    monkeypatch.setattr(elevenlabs, "generate", reject)
    api.generate_one(jid)
    assert client.get(f"/api/providers/jobs/{jid}").json()["status"] == "rejected"
    called = []
    monkeypatch.setattr(
        elevenlabs,
        "generate",
        lambda *_: called.append(1) or {"status": "succeeded", "assets": []},
    )
    client.post(f"/api/providers/jobs/{jid}/generate", json={"confirmed": True})
    import time

    for _ in range(30):
        if client.get(f"/api/providers/jobs/{jid}").json()["status"] == "succeeded":
            break
        time.sleep(0.02)
    assert called == [1]
