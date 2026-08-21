"""CLIP must survive the pipeline's VRAM-pressure unload.

The worker calls clip.unload() whenever free VRAM drops below 1 GB. unload()
used to clear the model but leave the _LOAD_TRIED latch set, so _load()
short-circuited to False forever: every later asset got no embedding and
/api/search lost its text encoder for the rest of the process.
"""
from __future__ import annotations

from app.ai import clip


def test_unload_then_load_again(monkeypatch):
    loads: list[int] = []

    def _fake_load():
        loads.append(1)
        clip._model = object()
        clip._preprocess = object()
        clip._tokenizer = object()
        return True

    original_model = clip._model
    original_tried = clip._LOAD_TRIED
    try:
        clip._model = None
        clip._LOAD_TRIED = True  # simulate "already attempted"
        clip.unload()
        assert clip._LOAD_TRIED is False, "unload must clear the load latch"

        monkeypatch.setattr(clip, "_load", _fake_load)
        assert clip._load() is True
        assert len(loads) == 1
    finally:
        clip._model = original_model
        clip._LOAD_TRIED = original_tried
