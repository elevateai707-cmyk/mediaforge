#!/usr/bin/env python3
"""Speech-QC helper for run_batch.py (run with the MediaForge venv python).

Reads one JSON object per line on stdin: {"video": "/path/file.mp4"} and answers with
{"text": "..."} or {"error": "..."}. Loads faster-whisper "small" (CPU, int8) once, on the first
request. Audio is decoded with ffmpeg into a float32 numpy array and handed to the model directly,
because faster-whisper's own loader (PyAV) is broken with the installed av version.
"""
import json
import subprocess
import sys

import numpy as np

_model = None


def model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        try:
            _model = WhisperModel("small", device="cpu", compute_type="int8", local_files_only=True)
        except Exception:  # noqa: BLE001 - not cached yet: allow one download
            _model = WhisperModel("small", device="cpu", compute_type="int8")
    return _model


def decode(path: str) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-vn", "-ac", "1", "-ar", "16000",
                          "-f", "f32le", "-"], capture_output=True, timeout=300).stdout
    return np.frombuffer(raw, dtype=np.float32)


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            audio = decode(json.loads(line)["video"])
            if audio.size < 16000:
                raise RuntimeError("no audio decoded")
            segments, _info = model().transcribe(audio, language="en", beam_size=1, vad_filter=False,
                                                 condition_on_previous_text=False)
            reply = {"text": " ".join(s.text.strip() for s in segments)}
        except Exception as exc:  # noqa: BLE001
            reply = {"error": f"{type(exc).__name__}: {exc}"[:300]}
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
