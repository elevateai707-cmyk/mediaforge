"""Ephemeral Whisper worker: `python -m adlab.workers.whisper_worker MEDIA OUT.json [MODEL]`.

Runs in its own process so the model's VRAM is returned the moment it exits;
AdLab never keeps a model resident. Exit codes: 0 ok, 3 faster-whisper not
installed (`pip install 'adlab[whisper]'`), 1 any other failure.
"""
from __future__ import annotations

import json
import sys


def main() -> int:
    media, out = sys.argv[1], sys.argv[2]
    model_name = sys.argv[3] if len(sys.argv) > 3 else "small.en"
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("faster-whisper not installed", file=sys.stderr)
        return 3
    try:
        try:
            model = WhisperModel(model_name, device="cuda", compute_type="int8_float16")
        except Exception:  # no CUDA / no cuDNN: CPU is slower but works
            model = WhisperModel(model_name, device="cpu", compute_type="int8")
        segments, info = model.transcribe(media, vad_filter=True)
        data = {"language": info.language,
                "segments": [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
                             for s in segments]}
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return 0
    except Exception as exc:  # noqa: BLE001 - reported to the parent via stderr
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
