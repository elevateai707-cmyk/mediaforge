"""Voice handling for green-screen plans: clean the recording's audio and get word timestamps.

* ``clean_voice`` extracts the audio track and cleans it (high-pass, FFT noise reduction, loudness
  to -16 LUFS); the final render still applies the existing voice/music staging and the -14 LUFS
  master normalisation.
* ``transcribe_words`` decodes audio with ffmpeg into a numpy array (faster-whisper's own loader
  is broken with the installed av version) and runs faster-whisper on the CPU, so it never
  competes for the shared 4 GB GPU. The model is loaded once per process.
* ``group_beats`` turns the word list into beat-sized groups at sentence/pause boundaries.
"""
from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path
from typing import Optional

import numpy as np

from .. import config
from ..proc import tool_env

log = logging.getLogger("mediaforge.speech")

VOICE_FILTER = ("highpass=f=80,afftdn=nr=12:nf=-40:tn=1,"
                "loudnorm=I=-16:TP=-1.5:LRA=11")
_model = None
_lock = threading.Lock()


class SpeechError(RuntimeError):
    pass


def clean_voice(src: str, dest: Path) -> Path:
    """Source video/audio -> cleaned mono 48 kHz WAV."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-nostdin", "-i", src, "-vn", "-map", "0:a:0", "-ac", "1", "-ar", "48000",
           "-af", VOICE_FILTER, str(dest)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=tool_env())
    if proc.returncode != 0 or not dest.is_file():
        raise SpeechError("the recording has no usable audio track: " + (proc.stderr or "")[-200:])
    return dest


def decode_16k(path: str) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-vn", "-ac", "1", "-ar", "16000",
                          "-f", "f32le", "-"], capture_output=True, timeout=900, env=tool_env()).stdout
    return np.frombuffer(raw, dtype=np.float32)


def _whisper():
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel
            try:
                _model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8", local_files_only=True)
            except Exception:  # noqa: BLE001 - not cached yet: allow one download
                _model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
        return _model


def transcribe_words(audio_path: str, language: Optional[str] = "en") -> list[dict]:
    """[{"word", "start", "end"}] (seconds) for a speech recording."""
    audio = decode_16k(audio_path)
    if audio.size < 16000:
        raise SpeechError("no speech audio could be decoded")
    segments, _info = _whisper().transcribe(audio, language=language, beam_size=1, word_timestamps=True,
                                            vad_filter=False, condition_on_previous_text=False)
    words: list[dict] = []
    for seg in segments:
        for w in seg.words or []:
            text = (w.word or "").strip()
            if text:
                words.append({"word": text, "start": float(w.start), "end": float(max(w.end, w.start + 0.02))})
    return words


def group_beats(words: list[dict], total: float, min_s: float = 3.5, max_s: float = 9.0,
                pause_s: float = 0.55) -> list[dict]:
    """Contiguous beats covering [0, total]: [{"start", "end", "words": [beat-local words]}].

    A beat closes at a sentence end once it is at least ``min_s`` long, at a long pause once it is
    >= min_s, or at ``max_s``. The last beat absorbs a short tail.
    """
    words = [{**w, "end": min(w["end"], float(total))} for w in words if w["start"] < float(total) - 0.05]
    if not words:
        return [{"start": 0.0, "end": float(total), "words": []}]
    groups: list[list[dict]] = [[]]
    for i, w in enumerate(words):
        groups[-1].append(w)
        begin = groups[-1][0]["start"]
        nxt = words[i + 1] if i + 1 < len(words) else None
        span = w["end"] - begin
        sentence_end = w["word"][-1] in ".!?"
        gap = (nxt["start"] - w["end"]) if nxt else 0.0
        if nxt and ((sentence_end and span >= min_s) or (gap >= pause_s and span >= min_s) or span >= max_s):
            groups.append([])
    if len(groups) > 1 and groups[-1][-1]["end"] - groups[-1][0]["start"] < 2.0:
        groups[-2].extend(groups.pop())
    beats = []
    for gi, g in enumerate(groups):
        start = 0.0 if gi == 0 else (groups[gi - 1][-1]["end"] + g[0]["start"]) / 2
        beats.append({"start": start, "words": g})
    for gi, b in enumerate(beats):
        b["end"] = beats[gi + 1]["start"] if gi + 1 < len(beats) else float(total)
        b["words"] = [{"word": w["word"], "start": round(w["start"] - b["start"], 3),
                       "end": round(min(w["end"], b["end"]) - b["start"], 3)} for w in b["words"]]
    return beats
