"""HEIC decode integration: opt in with a local MF_TEST_HEIC sample."""
from __future__ import annotations
import os
from pathlib import Path
import pytest
from app.images import open_rgb


def test_open_rgb_heic():
    # Never probe a machine-specific (possibly disconnected) media mount during
    # collection. Test media remains private and optional on every installation.
    sample = os.environ.get('MF_TEST_HEIC')
    if not sample:
        pytest.skip('set MF_TEST_HEIC to a local sample to test HEIC decoding')
    path = Path(sample)
    assert path.is_file(), 'MF_TEST_HEIC must name an existing local file'
    img = open_rgb(str(path))
    assert img.mode == 'RGB'
    assert img.size[0] > 0 and img.size[1] > 0
