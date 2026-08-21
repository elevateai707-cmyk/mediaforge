"""HEIC open via pillow-heif — captions must not crash on iPhone stills."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.images import open_rgb

HEIC = Path("/home/bfam/iphone-media/100APPLE/IMG_0001.HEIC")


@pytest.mark.skipif(not HEIC.is_file(), reason="sample HEIC not on this machine")
def test_open_rgb_heic():
    img = open_rgb(str(HEIC))
    assert img.mode == "RGB"
    assert img.size[0] > 0 and img.size[1] > 0
