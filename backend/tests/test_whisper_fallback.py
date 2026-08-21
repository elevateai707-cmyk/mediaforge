"""Whisper must degrade instead of silently transcribing nothing.

Both regressions here produced an empty transcript table across a whole
library while /api/health still reported whisper as 'loaded'.
"""
from __future__ import annotations

import pytest

from app.ai import whisper


class _Seg:
    def __init__(self):
        self.start, self.end, self.text = 0.0, 1.0, "hello"
        self.words = []


def _reset():
    whisper._model = None
    whisper._LOAD_TRIED = False
    whisper._CUDA_BROKEN = False
    whisper._device = "cpu"


def test_no_audio_stream_returns_none_without_calling_model(tmp_path, monkeypatch):
    """A video with no audio track must skip cleanly, not raise."""
    _reset()
    media = tmp_path / "silent.mp4"
    media.write_bytes(b"stub")
    monkeypatch.setattr(whisper, "has_audio", lambda _p: False)

    def _fail():
        raise AssertionError("model must not be loaded for an audio-less file")

    monkeypatch.setattr(whisper, "available", _fail)
    assert whisper.transcribe(str(media)) is None


def test_cuda_failure_falls_back_to_cpu_and_retries(tmp_path, monkeypatch):
    """CTranslate2 only dlopens libcublas at inference — recover there."""
    _reset()
    media = tmp_path / "clip.mov"
    media.write_bytes(b"stub")
    monkeypatch.setattr(whisper, "has_audio", lambda _p: True)
    monkeypatch.setattr(whisper, "gpu_device", lambda: "cuda")

    built: list[str] = []

    class _Model:
        def __init__(self, device):
            self.device = device

        def transcribe(self, _path, **_kw):
            if self.device == "cuda":
                raise RuntimeError(
                    "Library libcublas.so.12 is not found or cannot be loaded")
            return iter([_Seg()]), None

    def _build(device):
        built.append(device)
        return _Model(device)

    monkeypatch.setattr(whisper, "_build", _build)

    result = whisper.transcribe(str(media))
    assert result is not None, "should have retried on CPU"
    assert result["segments"][0]["text"] == "hello"
    assert built == ["cuda", "cpu"]
    assert whisper.device() == "cpu"
    assert whisper._CUDA_BROKEN is True
    _reset()


def test_unload_allows_reload(tmp_path, monkeypatch):
    """unload() means 'free VRAM', not 'never load again'."""
    _reset()
    monkeypatch.setattr(whisper, "gpu_device", lambda: "cpu")
    monkeypatch.setattr(whisper, "_build", lambda device: object())

    assert whisper.available() is True
    whisper.unload()
    assert whisper._model is None
    assert whisper.available() is True, "model must reload after unload"
    _reset()


def test_cuda_broken_survives_unload(tmp_path, monkeypatch):
    """A box with an unusable GPU stack reloads straight onto CPU."""
    _reset()
    monkeypatch.setattr(whisper, "gpu_device", lambda: "cuda")
    built: list[str] = []
    monkeypatch.setattr(whisper, "_build",
                        lambda device: built.append(device) or object())

    whisper._CUDA_BROKEN = True
    assert whisper.available() is True
    whisper.unload()
    assert whisper.available() is True
    assert built == ["cpu", "cpu"], f"never retry a proven-dead GPU: {built}"
    _reset()
