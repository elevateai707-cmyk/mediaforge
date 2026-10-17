"""Preserve word timing without changing the existing library database schema."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from . import config


def save_transcript(asset_id: int, asset_hash: str, result: dict) -> None:
    directory = config.DATA_DIR / "transcripts"
    directory.mkdir(parents=True, exist_ok=True)
    segments = []
    for segment in result.get("segments", []):
        row = dict(segment)
        # Modern whisper adapter returns words with each segment; retain support
        # for old adapters/mocks with a flat word list using word midpoint.
        if "words" not in row:
            row["words"] = [w for w in result.get("words", [])
                            if row["start"] <= (w["start"] + w["end"]) / 2 < row["end"]]
        segments.append(row)
    payload = {"schema_version": 1, "asset_id": asset_id, "asset_hash": asset_hash,
               "timing_source": "faster-whisper", "model": config.WHISPER_MODEL,
               "segments": segments}
    target = directory / f"{int(asset_id)}.json"
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         prefix=".transcript-", delete=False) as stream:
            temp_path = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        temp_path.replace(target)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
