import pytest
from starlette.websockets import WebSocketDisconnect

from app import auth, config


@pytest.fixture
def secured(monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "test-private-token-not-a-real-secret")
    auth._sessions.clear()
    auth._attempts.clear()
    yield
    auth._sessions.clear()


def test_bearer_and_public_health(client, secured):
    for url in ("/api/assets", "/api/config", "/exports/private.mp4"):
        assert client.get(url).status_code == 401
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["ingest"] is None
    assert not health.json()["warnings"]
    assert client.get("/api/config", headers={"Authorization": f"Bearer {config.API_TOKEN}"}).status_code == 200
    assert client.get("/api/config", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_cookie_media_logout_and_origin(client, secured):
    (config.EXPORTS_DIR / "auth-test.txt").write_text("private")
    try:
        response = client.post("/api/auth/login", json={"token": config.API_TOKEN})
        assert response.status_code == 200
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie
        assert config.API_TOKEN not in cookie
        assert client.get("/exports/auth-test.txt").text == "private"
        assert client.post("/api/auth/logout", headers={"Origin": "http://evil.example"}).status_code == 401
        saved = client.cookies.get(auth.COOKIE)
        assert client.post("/api/auth/logout").status_code == 200
        assert client.get("/exports/auth-test.txt", headers={"Cookie": f"{auth.COOKIE}={saved}"}).status_code == 401
        assert client.post("/api/auth/login", json={"token": config.API_TOKEN}, headers={"Origin": "http://evil.example"}).status_code == 403
    finally:
        client.cookies.clear()
        (config.EXPORTS_DIR / "auth-test.txt").unlink(missing_ok=True)


def test_websocket_guard_and_constant_time(client, secured, monkeypatch):
    original = auth.hmac.compare_digest
    calls = []
    monkeypatch.setattr(auth.hmac, "compare_digest", lambda a, b: calls.append((a, b)) or original(a, b))
    assert auth.token_matches(config.API_TOKEN)
    assert calls
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws"):
            pass
    with client.websocket_connect("/ws", headers={"Authorization": f"Bearer {config.API_TOKEN}"}) as ws:
        ws.send_text("ping")


def test_session_expiry_and_token_rotation(client, secured, monkeypatch):
    client.post("/api/auth/login", json={"token": config.API_TOKEN})
    assert client.get("/api/auth/session").status_code == 200
    monkeypatch.setattr(config, "API_TOKEN", "rotated-test-token")
    assert client.get("/api/auth/session").status_code == 401
    client.cookies.clear()


def test_private_responses_and_default_dev_origin_are_protected(client, secured):
    client.post("/api/auth/login", json={"token": config.API_TOKEN})
    try:
        assert client.get("/api/config").headers["cache-control"] == "private, no-store"
        assert client.get("/api/config", headers={"Origin": "http://localhost:5173"}).status_code == 401
    finally:
        client.cookies.clear()


@pytest.mark.parametrize("invalidate", ["expire", "rotate", "logout"])
def test_connected_websocket_reauthorizes(client, secured, monkeypatch, invalidate):
    monkeypatch.setattr(auth, "WS_RECHECK_SECONDS", .01)
    client.post("/api/auth/login", json={"token": config.API_TOKEN})
    try:
        with client.websocket_connect("/ws") as ws:
            if invalidate == "rotate":
                monkeypatch.setattr(config, "API_TOKEN", "different-token")
            elif invalidate == "expire":
                with auth._lock:
                    for key, value in list(auth._sessions.items()):
                        auth._sessions[key] = (0, value[1])
            else:
                client.post("/api/auth/logout")
            with pytest.raises(WebSocketDisconnect) as closed:
                while True:
                    ws.receive_text()
            assert closed.value.code == 4401
    finally:
        client.cookies.clear()
