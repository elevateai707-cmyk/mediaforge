"""A partially-written still must not be reported as a model outage.

iPhone libraries contain stills whose import was interrupted. Pillow raises
OSError("tile cannot extend outside image" / "image file is truncated") on
those. That error escaped caption_image's encode step and marked the whole
Ollama model 'unavailable', so /api/health reported an outage — and the
warning banner stuck — because of one bad file.
"""
from __future__ import annotations

import io

import pytest
from PIL import Image

from app.ai import captions, gpu
from app.images import open_rgb


@pytest.fixture
def truncated_jpeg(tmp_path):
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), (120, 30, 200)).save(buf, "JPEG", quality=95)
    data = buf.getvalue()
    path = tmp_path / "truncated.jpg"
    path.write_bytes(data[: int(len(data) * 0.55)])
    return str(path)


def test_open_rgb_reads_a_truncated_file(truncated_jpeg):
    img = open_rgb(truncated_jpeg)
    assert img.size == (800, 600)
    assert img.mode == "RGB"


def test_encode_failure_does_not_mark_ollama_unavailable(truncated_jpeg,
                                                         monkeypatch):
    """An unreadable file falls back to a local caption, model stays up."""
    gpu.set_model_status("ollama", "loaded")
    monkeypatch.setattr(captions, "_ensure_model_ready", lambda: True)

    def _boom(_path, max_side=1024):
        raise OSError("tile cannot extend outside image")

    monkeypatch.setattr(captions, "image_to_b64", _boom)

    def _never(*_a, **_kw):
        raise AssertionError("generate() must not run without an encoded image")

    monkeypatch.setattr(captions, "generate", _never)

    caption = captions.caption_image(truncated_jpeg)
    assert caption, "must still return a fallback caption"
    assert gpu.model_status("ollama")["status"] == "loaded"
