"""Local-only default, origin/Host checks, server-side provider secrets."""

from pathlib import Path
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

from . import config, models, persist


class LocalAccess:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        peer = scope.get("client") or ("", 0)
        host = urlsplit("//" + headers.get(b"host", b"").decode()).hostname
        origin = headers.get(b"origin", b"").decode()
        allowed = {"localhost", "127.0.0.1", "::1", "testserver"}
        good = peer[0] in (allowed | {"testclient"}) and host in allowed
        scheme = "https" if scope.get("scheme") in ("https", "wss") else "http"
        if origin:
            o = urlsplit(origin)
            good = (
                good
                and o.scheme in ("http", "https")
                and o.hostname in allowed
                and (
                    origin in config.CORS_ORIGINS
                    or origin == f"{scheme}://{headers.get(b'host', b'').decode()}"
                )
            )
        if not good:
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 1008})
            return await JSONResponse(
                {
                    "detail": "MediaForge is local-only. Open localhost or use an SSH tunnel."
                },
                status_code=403,
            )(scope, receive, send)
        return await self.app(scope, receive, send)


def media_path(raw, db):
    p = Path(raw).expanduser()
    if not p.is_absolute() or ".." in p.parts:
        raise ValueError("Use an absolute media path")
    p = p.resolve()
    roots = [config.EXPORTS_DIR, config.DATA_DIR / "generated"]
    # Registered media and configured music directories only.
    if db.query(models.Asset).filter_by(path=str(p)).first():
        return p
    music = persist.music_dir()
    if music:
        roots.append(Path(music).resolve())
    if not any(p.is_relative_to(r) for r in roots):
        raise ValueError(
            "Choose registered media or a file in the configured music folder"
        )
    if not p.is_file() or p.suffix.lower() not in {
        ".mp3",
        ".wav",
        ".m4a",
        ".aac",
        ".ogg",
        ".flac",
        ".mp4",
        ".mov",
        ".webm",
    }:
        raise ValueError("Unsupported or missing media file")
    return p
