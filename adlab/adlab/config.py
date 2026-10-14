"""Paths and settings. Everything is overridable by env var (tests use temp dirs).

AdLab is local-first: data/ holds the private media copies, ChromaDB, locks and
logs, and is git-ignored. Keys live in .env (see .env.example), never in code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if env.is_file():
        try:
            from dotenv import load_dotenv

            load_dotenv(env, override=False)
        except ImportError:  # pragma: no cover - python-dotenv is a dependency
            pass


_load_dotenv()


def _path(var: str, default: Path) -> Path:
    return Path(os.environ.get(var) or default).expanduser().resolve()


@dataclass(frozen=True)
class Settings:
    sites_dir: Path
    data_dir: Path
    config_dir: Path
    mediaforge_url: str
    studio_cli: str          # evstudio-cli path; auto-found when empty
    default_model: str

    @property
    def incoming_dir(self) -> Path:
        return self.data_dir / "incoming"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"       # private copies, analysis only

    @property
    def chroma_dir(self) -> Path:
        return self.data_dir / "chroma"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def locks_dir(self) -> Path:
        return self.data_dir / "locks"

    @property
    def batches_dir(self) -> Path:
        return self.data_dir / "batches"

    def ensure_dirs(self) -> None:
        for d in (self.incoming_dir, self.media_dir, self.chroma_dir, self.logs_dir,
                  self.locks_dir, self.batches_dir):
            d.mkdir(parents=True, exist_ok=True)


def settings() -> Settings:
    """Fresh Settings from the current environment (cheap; tests monkeypatch env)."""
    return Settings(
        sites_dir=_path("ADLAB_SITES_DIR", ROOT / "sites"),
        data_dir=_path("ADLAB_DATA_DIR", ROOT / "data"),
        config_dir=_path("ADLAB_CONFIG_DIR", ROOT / "config"),
        mediaforge_url=os.environ.get("MEDIAFORGE_URL", "http://127.0.0.1:8420").rstrip("/"),
        studio_cli=os.environ.get("STUDIO_CLI", ""),
        default_model=os.environ.get("ADLAB_MODEL", "claude-opus-5-5"),
    )
