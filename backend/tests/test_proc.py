"""Subprocess environment hygiene.

Regression guard for the Resolve/ffmpeg loader clash: Resolve's bundled
libavutil in /opt/resolve/libs makes the system ffmpeg exit 127
("symbol lookup error: ... undefined symbol: av_bessel_i0"). A Resolve export
must not leave that path in os.environ, and media tools must not inherit it
even when the server itself was started with it set.
"""
import os
import subprocess

import pytest

from app.edits import resolve_export
from app.proc import LOADER_VARS, tool_env

RESOLVE_LIBS = "/opt/resolve/libs"


def _snapshot():
    return {k: os.environ.get(k) for k in ("LD_LIBRARY_PATH", "PYTHONPATH")}


def test_tool_env_strips_loader_vars(monkeypatch):
    for var in LOADER_VARS:
        monkeypatch.setenv(var, RESOLVE_LIBS)
    env = tool_env()
    for var in LOADER_VARS:
        assert var not in env, f"{var} leaked into the child environment"
    # Unrelated variables survive.
    monkeypatch.setenv("MF_CANARY", "1")
    assert tool_env().get("MF_CANARY") == "1"


def test_prepared_env_restores_os_environ():
    before = _snapshot()
    with resolve_export._prepared_env():
        assert RESOLVE_LIBS in os.environ["LD_LIBRARY_PATH"]
    assert _snapshot() == before


def test_prepared_env_restores_on_exception():
    before = _snapshot()
    with pytest.raises(RuntimeError):
        with resolve_export._prepared_env():
            raise RuntimeError("import blew up")
    assert _snapshot() == before


def test_resolve_export_does_not_poison_environ(client, approved_plan):
    """The 503 path still runs _load_dvr(); it must leave no residue."""
    before = _snapshot()
    r = client.post("/api/export/resolve", json={"plan_id": approved_plan()})
    assert r.status_code == 503
    assert _snapshot() == before


@pytest.mark.skipif(not os.path.isdir(RESOLVE_LIBS),
                    reason="DaVinci Resolve libs not installed")
def test_ffmpeg_survives_resolve_ld_library_path(monkeypatch, tmp_path):
    """ffmpeg works even when the parent process carries Resolve's libs."""
    monkeypatch.setenv("LD_LIBRARY_PATH", RESOLVE_LIBS)
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
           "-i", "color=c=blue:s=64x64:d=1", str(tmp_path / "out.mp4")]

    poisoned = subprocess.run(cmd, capture_output=True, text=True)
    assert poisoned.returncode == 127, (
        "expected the unsanitized environment to break ffmpeg; if this fails "
        "the loader clash is gone and this guard can be retired"
    )

    clean = subprocess.run(cmd, capture_output=True, text=True, env=tool_env())
    assert clean.returncode == 0, clean.stderr
