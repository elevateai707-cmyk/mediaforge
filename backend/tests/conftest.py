"""Shared pytest fixtures for the MediaForge backend.

Environment overrides MUST be applied at import time (before ``app.main`` is
imported) because config.py reads env vars at module import.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

_TMP = Path(tempfile.mkdtemp(prefix="mediaforge_test_"))
_MEDIA = _TMP / "media"
_DATA = _TMP / "data"
_EXPORTS = _TMP / "exports"
for _d in (_MEDIA, _DATA, _EXPORTS):
    _d.mkdir(parents=True, exist_ok=True)

os.environ["MF_DATA_DIR"] = str(_DATA)
os.environ["MF_DB_PATH"] = str(_DATA / "test.db")
os.environ["MF_THUMBS_DIR"] = str(_DATA / "thumbs")
os.environ["MF_PROXIES_DIR"] = str(_DATA / "proxies")
os.environ["MF_RENDER_TMP"] = str(_DATA / "render_tmp")
os.environ["MF_EXPORTS_DIR"] = str(_EXPORTS)
os.environ["MF_TOUCHUP_DIR"] = str(_EXPORTS / "touchup")
os.environ["MEDIA_DIRS"] = str(_MEDIA)
# Keep the AI worker from downloading models during tests.
os.environ["MF_AI_ENABLED"] = "0"
os.environ["MF_ALLOW_OLLAMA_PULL"] = "0"
os.environ["MF_SKIP_WHISPER"] = "1"
os.environ["MF_SKIP_FACES"] = "1"
os.environ["MF_SKIP_CAPTIONS"] = "1"


def _cleanup() -> None:
    shutil.rmtree(_TMP, ignore_errors=True)


import atexit

atexit.register(_cleanup)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
import pytest


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def wait_job():
    def _wait(c, job_id, timeout=120):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = c.get(f"/api/jobs/{job_id}").json()
            if last["status"] in ("done", "error", "cancelled"):
                return last
            time.sleep(0.2)
        raise AssertionError(f"job {job_id} did not finish in {timeout}s: {last}")

    return _wait


@pytest.fixture()
def media_dir():
    """A fresh, unique media directory per test."""
    d = _MEDIA / f"run_{time.time_ns()}"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture()
def jpeg_library(media_dir):
    """Three tiny JPEG photos inside ``media_dir``."""
    from PIL import Image

    for i, name in enumerate(["scan_a.jpg", "scan_b.jpg", "scan_c.jpg"]):
        Image.new("RGB", (320, 240), (20 + i * 50, 60, 100)).save(
            media_dir / name, "JPEG"
        )
    return media_dir


@pytest.fixture()
def video_library(media_dir):
    """Two short 2s colour-bar videos (h264 + aac) inside ``media_dir``."""
    import subprocess

    for name, color in (("render_a.mp4", "blue"), ("render_b.mp4", "red")):
        out = media_dir / name
        subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error",
                "-f", "lavfi", "-i", f"color=c={color}:s=320x180:d=2",
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-shortest", "-pix_fmt", "yuv420p",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-c:a", "aac",
                str(out),
            ],
            check=True, capture_output=True,
        )
    return media_dir


@pytest.fixture(autouse=True)
def _no_ollama(monkeypatch):
    """Planner must never hit the network in tests: force the deterministic
    fallback by stubbing the ollama planning step to return None."""
    import app.edits.planner as planner_mod

    monkeypatch.setattr(planner_mod, "_ollama_plan", lambda intent, db: None)


@pytest.fixture()
def approved_plan(client, wait_job, jpeg_library):
    """Scan the jpeg library and return an approved plan id."""

    def _make():
        r = client.post("/api/scan", json={"paths": [str(jpeg_library)]})
        assert r.status_code == 200
        job = wait_job(client, r.json()["job_id"])
        assert job["status"] == "done", job
        r = client.post("/api/edits/plan", json={"intent": "travel montage"})
        assert r.status_code == 200
        pid = r.json()["plan_id"]
        r = client.post(f"/api/edits/plan/{pid}/approve")
        assert r.status_code == 200
        return pid

    return _make
