"""Persisted config KV (SQLite system_settings + optional .env sync)."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from . import config, models
from .db import SessionLocal

log = logging.getLogger("mediaforge.settings")

KEY_DIRS = "media_dirs"
KEY_WATCHER = "watcher_enabled"
KEY_MUSIC = "music_dir"
KEY_CLOUD = "use_cloud_llm"


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    with SessionLocal() as db:
        row = db.get(models.SystemSetting, key)
        return row.value if row is not None else default


def set_setting(key: str, value: Optional[str]) -> None:
    with SessionLocal() as db:
        row = db.get(models.SystemSetting, key)
        if row is None:
            db.add(models.SystemSetting(key=key, value=value))
        else:
            row.value = value
        db.commit()


def persisted_media_dirs() -> list[str]:
    raw = get_setting(KEY_DIRS)
    if raw:
        try:
            dirs = json.loads(raw)
            if isinstance(dirs, list):
                return [str(d).strip() for d in dirs if str(d).strip()]
        except json.JSONDecodeError:
            pass
    return []


def media_dirs() -> list[str]:
    stored = persisted_media_dirs()
    if stored:
        return stored
    return [d for d in config.MEDIA_DIRS if d]


def set_media_dirs(dirs: list[str]) -> list[str]:
    cleaned = []
    for d in dirs:
        text = str(d).strip()
        if not text:
            continue
        cleaned.append(str(Path(text).expanduser()))
    set_setting(KEY_DIRS, json.dumps(cleaned))
    config.MEDIA_DIRS[:] = cleaned
    _write_env_key("MEDIA_DIRS", ",".join(cleaned))
    return cleaned


def watcher_enabled() -> bool:
    raw = get_setting(KEY_WATCHER)
    if raw is None:
        return config.WATCHER_ENABLED
    return raw.strip() not in ("0", "false", "off", "")


def set_watcher_enabled(enabled: bool) -> bool:
    set_setting(KEY_WATCHER, "1" if enabled else "0")
    config.WATCHER_ENABLED = enabled
    return enabled


def music_dir() -> str:
    return (get_setting(KEY_MUSIC) or config.MUSIC_DIR or "").strip()


def set_music_dir(path: str) -> str:
    value = str(Path(path).expanduser()) if path.strip() else ""
    set_setting(KEY_MUSIC, value)
    config.MUSIC_DIR = value
    return value


def use_cloud_llm() -> bool:
    raw = get_setting(KEY_CLOUD)
    if raw is None:
        return config.USE_CLOUD_LLM
    return raw.strip() in ("1", "true", "yes")


def set_use_cloud_llm(enabled: bool) -> bool:
    set_setting(KEY_CLOUD, "1" if enabled else "0")
    config.USE_CLOUD_LLM = enabled
    return enabled


def _write_env_key(key: str, value: str) -> None:
    if os_environ_skip():
        return
    env_path = config.MF_ROOT / ".env"
    try:
        lines: list[str] = []
        if env_path.exists():
            lines = env_path.read_text(encoding="utf-8").splitlines()
        found = False
        out: list[str] = []
        prefix = f"{key}="
        for line in lines:
            if line.startswith(prefix) or line.startswith(f"export {prefix}"):
                out.append(f"{key}={value}")
                found = True
            else:
                out.append(line)
        if not found:
            out.append(f"{key}={value}")
        env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    except Exception as exc:
        log.warning("could not update %s in .env: %s", key, exc)


def os_environ_skip() -> bool:
    return os.environ.get("MF_SKIP_ENV_WRITE", "0") in ("1", "true", "yes")
