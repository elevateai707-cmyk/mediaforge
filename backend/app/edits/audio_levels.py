"""Loudness measurement and gain staging for narrated renders with a music bed.

ElevenLabs narration is quiet (~-29 LUFS integrated) while produced music is
-11..-16 LUFS, so mixing them at their own levels buries the voice. For story
renders with music the voice is lifted to a fixed target and the music bed is
set a fixed number of dB below it, both with static gains computed from
measured integrated loudness (EBU R128 via ffmpeg's ebur128). Sidechain ducking
then adds a modest extra dip while the voice speaks.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Optional

from .. import config
from ..proc import tool_env

log = logging.getLogger("mediaforge.levels")

VOICE_TARGET_LUFS = -16.0
MAX_BOOST_DB = 24.0
MIN_GAIN_DB = -40.0
VOICE_PEAK_LIMIT = 10 ** (-1.5 / 20)      # -1.5 dBTP as a linear limiter ceiling
END_CARD_RISE_DB = 6.0
END_CARD_RISE_S = 0.5
_I_RE = re.compile(r"Integrated loudness:\s*\n\s*I:\s*(-?[0-9.]+|-inf)\s*LUFS")
_cache: dict[tuple, Optional[float]] = {}
_lock = threading.Lock()


def _cache_file() -> Path:
    return config.DATA_DIR / "loudness_cache.json"


def _key(path: str, start: float, duration: Optional[float]) -> tuple:
    st = os.stat(path)
    return (str(path), st.st_size, st.st_mtime_ns, round(start, 2),
            round(duration, 2) if duration else None)


def measure_lufs(path: str, start: float = 0.0, duration: Optional[float] = None) -> Optional[float]:
    """Integrated loudness (LUFS) of a file segment; None if it cannot be measured
    (missing, silent, ffmpeg failure). Cached per file version and segment."""
    try:
        key = _key(path, start, duration)
    except OSError:
        return None
    skey = json.dumps(key)
    with _lock:
        if key in _cache:
            return _cache[key]
        try:
            disk = json.loads(_cache_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            disk = {}
        if skey in disk:
            _cache[key] = disk[skey]
            return disk[skey]
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "info"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-i", path, "-vn", "-af", "ebur128", "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=tool_env())
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("loudness measurement failed for %s: %s", Path(path).name, exc)
        return None
    matches = _I_RE.findall(proc.stderr or "")
    value: Optional[float] = None
    if matches and matches[-1] != "-inf":
        value = float(matches[-1])
        if not math.isfinite(value) or value <= -69.5:
            value = None
    with _lock:
        _cache[key] = value
        try:
            disk[skey] = value
            tmp = _cache_file().with_suffix(".tmp")
            tmp.write_text(json.dumps(disk), encoding="utf-8")
            os.replace(tmp, _cache_file())
        except OSError:
            pass
    return value


def voice_gain_db(voice_lufs: float, target: float = VOICE_TARGET_LUFS) -> float:
    """Static gain taking the narration to ``target`` LUFS."""
    return max(MIN_GAIN_DB, min(MAX_BOOST_DB, target - voice_lufs))


def music_gain_db(music_lufs: float, bed_db: float, voice_target: float = VOICE_TARGET_LUFS) -> float:
    """Static gain putting the music bed ``bed_db`` below the normalised voice."""
    return max(MIN_GAIN_DB, min(MAX_BOOST_DB, voice_target - bed_db - music_lufs))


def plan_levels(voiceover: str, music_path: str, music_start: float, window: float,
                bed_db: Optional[float] = None) -> Optional[dict]:
    """Gains for a story render, or None when the levels cannot be measured (the
    caller then keeps the legacy un-normalised mix)."""
    bed = config.MUSIC_BED_DB if bed_db is None else bed_db
    voice = measure_lufs(voiceover)
    music = measure_lufs(music_path, music_start or 0.0, window)
    if voice is None or music is None:
        log.warning("could not measure voice/music loudness (voice=%s music=%s); "
                    "using the legacy mix", voice, music)
        return None
    out = {"voice_lufs": voice, "music_lufs": music, "bed_db": bed,
           "voice_gain_db": round(voice_gain_db(voice), 2),
           "music_gain_db": round(music_gain_db(music, bed), 2)}
    log.info("music bed levels: voice %.1f LUFS (%+.1f dB), music %.1f LUFS (%+.1f dB), bed %.0f dB under voice",
             voice, out["voice_gain_db"], music, out["music_gain_db"], bed)
    return out


def voice_filter(gain_db: float) -> str:
    """Filter chain fragment: static boost, then a -1.5 dBTP safety limiter."""
    return (f"volume={gain_db:.2f}dB,"
            f"alimiter=limit={VOICE_PEAK_LIMIT:.4f}:level=0:attack=5:release=50")


def music_filter(gain_db: float, card_start: Optional[float]) -> str:
    """Filter chain fragment: static bed gain, plus a +6 dB rise when the end card starts."""
    f = f"volume={gain_db:.2f}dB"
    if card_start is not None:
        rise = 10 ** (END_CARD_RISE_DB / 20)
        f += (f",volume='1+({rise - 1:.4f})*min(max((t-{card_start:.3f})/{END_CARD_RISE_S},0),1)'"
              ":eval=frame")
    return f


# Gentler ducking on top of the normalised voice (about 4-6 dB extra dip).
DUCK_FILTER = "threshold=0.05:ratio=4:attack=15:release=300:makeup=1"
