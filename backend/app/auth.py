"""Optional API bearer authentication and revocable, opaque browser sessions.

Tokens are never placed in URLs or browser storage. Sessions expire after 12h
and are invalidated by process restart or API-token rotation.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import threading
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.requests import HTTPConnection

from . import config

COOKIE = "mf_session"
TTL = 12 * 60 * 60
WS_RECHECK_SECONDS = 15
_lock = threading.RLock()
_sessions: dict[str, tuple[float, str]] = {}
_attempts: dict[str, list[float]] = {}
router = APIRouter(tags=["authentication"])


def _fingerprint() -> str:
    return hashlib.sha256(config.API_TOKEN.encode()).hexdigest()


def token_matches(value: str) -> bool:
    return bool(config.API_TOKEN) and hmac.compare_digest(
        value.encode("utf-8"), config.API_TOKEN.encode("utf-8"))


def _origin_allowed(conn: HTTPConnection) -> bool:
    origin = conn.headers.get("origin")
    if not origin:
        return True
    scheme = "https" if conn.url.scheme in ("https", "wss") else "http"
    own = f"{scheme}://{conn.headers.get('host', '')}"
    return origin == own or origin in config.CORS_ORIGINS


def authorized(conn: HTTPConnection) -> bool:
    if not config.API_TOKEN:
        return True
    header = conn.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return token_matches(header[7:])
    cookie = conn.cookies.get(COOKIE, "")
    if not cookie or len(cookie) > 128:
        return False
    digest = hashlib.sha256(cookie.encode()).hexdigest()
    with _lock:
        value = _sessions.get(digest)
        if not value or value[0] <= time.time() or not hmac.compare_digest(value[1], _fingerprint()):
            _sessions.pop(digest, None)
            return False
    # A same-site cookie alone must not authorize a different-origin write or
    # WebSocket (ports on the same machine are still separate origins).
    return _origin_allowed(conn)


class AuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or not config.API_TOKEN:
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        protected = (path == "/api" or path.startswith("/api/") or
                     path == "/ws" or path == "/exports" or path.startswith("/exports/"))
        public = scope["type"] == "http" and (
            (scope.get("method") == "GET" and path == "/api/health") or
            (scope.get("method") == "POST" and path == "/api/auth/login"))
        if protected and not public and not authorized(HTTPConnection(scope)):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 4401})
            else:
                await JSONResponse({"detail": "Authentication required"}, status_code=401,
                                   headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"})(scope, receive, send)
            return
        async def private_send(message):
            if protected and message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = [(k, v) for k, v in message.get("headers", [])
                                      if k.lower() != b"cache-control"] + [(b"cache-control", b"private, no-store")]
            await send(message)
        await self.app(scope, receive, private_send)


class Login(BaseModel):
    token: str = Field(min_length=1, max_length=4096, repr=False)


@router.post("/api/auth/login")
def login(body: Login, request: Request):
    if not _origin_allowed(request):
        raise HTTPException(403, "Login origin is not allowed")
    if not config.API_TOKEN:
        return {"ok": True, "enabled": False}
    client = request.client.host if request.client else "unknown"
    now = time.time()
    with _lock:
        attempts = [t for t in _attempts.get(client, []) if now - t < 60]
        if len(attempts) >= 10:
            raise HTTPException(429, "Too many login attempts; try again in one minute")
        if len(_attempts) > 1024:
            _attempts.clear()
        _attempts[client] = attempts + [now]
    if not token_matches(body.token):
        raise HTTPException(401, "Invalid token")
    value = secrets.token_urlsafe(32)
    with _lock:
        _attempts.pop(client, None)
        for key, session in list(_sessions.items()):
            if session[0] <= now:
                del _sessions[key]
        if len(_sessions) >= 1024:
            raise HTTPException(429, "Session limit reached")
        _sessions[hashlib.sha256(value.encode()).hexdigest()] = (now + TTL, _fingerprint())
    response = JSONResponse({"ok": True, "enabled": True}, headers={"Cache-Control": "no-store"})
    response.set_cookie(COOKIE, value, max_age=TTL, httponly=True, samesite="strict",
                        secure=request.url.scheme == "https", path="/")
    return response


@router.get("/api/auth/session")
def session():
    return {"ok": True, "enabled": bool(config.API_TOKEN)}


@router.post("/api/auth/logout")
def logout(request: Request):
    cookie = request.cookies.get(COOKIE, "")
    with _lock:
        _sessions.pop(hashlib.sha256(cookie.encode()).hexdigest(), None)
    response = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
    response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict")
    return response


def startup_notice():
    if not config.API_TOKEN:
        logging.getLogger("mediaforge.auth").warning(
            "AUTHENTICATION DISABLED: MF_API_TOKEN is unset. Every device able to reach MediaForge can access the library and control jobs.")
