"""CPU-only camera-motion analysis for drone footage.

For every video it records:
  * is_hyperlapse (+ which signal fired): DJI hyperlapses are 25 fps files; a second
    check looks at native-rate consecutive frames (huge frame-to-frame motion or
    failed tracking = temporal discontinuity);
  * daypart (day / dusk / night) from luma statistics;
  * smoothness (0-1) for normal footage: global camera motion is tracked at ~10 fps
    (LK features + a RANSAC similarity transform), and the jerk (third derivative of
    the camera path) is normalised by the typical speed, then penalised for abrupt
    direction reversals and stop-start; a per-second ``profile`` and the smooth
    ``windows`` (>= 3 s) let a long clip with one rough moment still contribute.

Decoding uses a downscaled ffmpeg pipe; nothing here touches the GPU, so it can never
compete with the AI worker for VRAM.
"""
from __future__ import annotations

import json
import logging
import math
import subprocess
import time
from typing import Optional

import numpy as np

from ..proc import tool_env

log = logging.getLogger("mediaforge.motion")

VERSION = 1
ANALYSIS_FPS = 10
ANALYSIS_WIDTH = 320
MAX_ANALYSED_S = 150.0
MIN_WINDOW_S = 3.0
EPS_SPEED = 0.004            # speed floor (fraction of frame width per step) for normalisation
JERK_K = 1.2
HYPER_DISP = 0.025           # per-native-frame centre displacement (fraction of width)
HYPER_FAIL = 0.4             # fraction of native frame pairs that cannot be tracked
HYPER_FPS = 25.0


# ---------------------------------------------------------------------------
# frames
# ---------------------------------------------------------------------------

def _size(width: int, height: int, w: int = ANALYSIS_WIDTH) -> tuple[int, int]:
    return w, max(2, int(round(w * height / max(1, width) / 2)) * 2)


def read_gray(path: str, width: int, height: int, fps: Optional[float], start: float = 0.0,
              duration: Optional[float] = None, threads: int = 2, max_frames: int = 100000) -> np.ndarray:
    """Downscaled grayscale frames as an (n, h, w) uint8 array (``fps=None``: native rate)."""
    w, h = _size(width, height)
    vf = (f"fps={fps}," if fps else "") + f"scale={w}:{h}"
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-threads", str(threads)]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-i", path, "-an", "-vf", vf, "-frames:v", str(max_frames),
            "-pix_fmt", "gray", "-f", "rawvideo", "-"]
    proc = subprocess.run(cmd, capture_output=True, timeout=600, env=tool_env())
    data = np.frombuffer(proc.stdout, dtype=np.uint8)
    n = data.size // (w * h)
    if n == 0:
        return np.zeros((0, h, w), dtype=np.uint8)
    return data[: n * w * h].reshape(n, h, w)


def track_steps(frames: np.ndarray) -> dict:
    """Per-step global motion between consecutive frames.

    Returns arrays (length n-1): dx, dy (centre displacement / frame width), rot (rad),
    ds (scale-1) and ``ok`` (False where tracking failed).
    """
    import cv2
    n, h, w = frames.shape
    dx = np.zeros(max(0, n - 1))
    dy = np.zeros_like(dx)
    rot = np.zeros_like(dx)
    ds = np.zeros_like(dx)
    ok = np.zeros(dx.shape, dtype=bool)
    cx, cy = w / 2.0, h / 2.0
    for i in range(n - 1):
        prev, cur = frames[i], frames[i + 1]
        pts = cv2.goodFeaturesToTrack(prev, maxCorners=300, qualityLevel=0.01, minDistance=8)
        if pts is None or len(pts) < 12:
            continue
        nxt, st, _err = cv2.calcOpticalFlowPyrLK(prev, cur, pts, None, winSize=(21, 21), maxLevel=3)
        if nxt is None:
            continue
        good = st.reshape(-1) == 1
        if good.sum() < 12:
            continue
        m, inl = cv2.estimateAffinePartial2D(pts[good], nxt[good], method=cv2.RANSAC,
                                             ransacReprojThreshold=2.5)
        if m is None or inl is None or inl.sum() < 10:
            continue
        a, b = m[0, 0], m[1, 0]
        scale = math.hypot(a, b)
        ox = m[0, 0] * cx + m[0, 1] * cy + m[0, 2] - cx
        oy = m[1, 0] * cx + m[1, 1] * cy + m[1, 2] - cy
        dx[i], dy[i] = ox / w, oy / w
        rot[i], ds[i] = math.atan2(b, a), scale - 1.0
        ok[i] = True
    return {"dx": dx, "dy": dy, "rot": rot, "ds": ds, "ok": ok}


# ---------------------------------------------------------------------------
# smoothness (pure; works on any tracked path)
# ---------------------------------------------------------------------------

def _fill(a: np.ndarray, ok: np.ndarray) -> np.ndarray:
    """Interpolate over failed steps so one bad pair does not look like a jerk."""
    if ok.all() or not ok.any():
        return a
    idx = np.arange(len(a))
    return np.interp(idx, idx[ok], a[ok])


def _smooth(x: np.ndarray, sigma: float) -> np.ndarray:
    if len(x) < 3:
        return x
    r = max(1, int(3 * sigma))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    pad = np.pad(x, (r, r), mode="edge")
    return np.convolve(pad, k, mode="valid")


def smoothness_from_steps(steps: dict, fps: float = ANALYSIS_FPS) -> dict:
    """Score 0-1 (1 = steady, flowing camera) from tracked per-step motion."""
    ok = steps["ok"]
    n = len(ok)
    out = {"smoothness": None, "jerk_norm": None, "reversals_per_s": 0.0, "stopstart_per_s": 0.0,
           "mean_speed": 0.0, "profile": [], "windows": [], "track_ok": float(ok.mean()) if n else 0.0}
    if n < int(fps * 1.0) or ok.mean() < 0.5:
        return out
    # Rotation / zoom expressed as the displacement they cause at the frame edge.
    chans = np.stack([_fill(steps["dx"], ok), _fill(steps["dy"], ok),
                      0.5 * _fill(steps["rot"], ok), 0.5 * _fill(steps["ds"], ok)])
    v = np.stack([_smooth(c, 0.7) for c in chans])          # drop pixel-level tracking noise
    jerk = np.diff(v, n=2, axis=1)                          # third derivative of position
    jn = np.sqrt((jerk ** 2).sum(axis=0))
    speed = np.sqrt((v[:2] ** 2).sum(axis=0))
    ref = float(speed.mean()) + EPS_SPEED
    jerk_norm = float(np.sqrt((jn ** 2).mean()) / ref)

    # abrupt reversals of the dominant horizontal/vertical motion
    rev = 0
    for axis in (0, 1):
        s = np.sign(v[axis]) * (np.abs(v[axis]) > 0.01)
        nz = s[s != 0]
        rev += int((nz[1:] != nz[:-1]).sum()) if len(nz) > 1 else 0
    # stop-start: moving <-> still transitions
    moving = speed > 0.012
    still = speed < 0.003
    state_full = np.where(moving, 1, np.where(still, 0, -1))
    state = state_full[state_full >= 0]
    stops = int((state[1:] != state[:-1]).sum()) if len(state) > 1 else 0
    seconds = n / fps
    rev_s, stop_s = rev / seconds, stops / seconds
    score = math.exp(-JERK_K * jerk_norm) * max(0.0, 1.0 - 0.35 * rev_s - 0.30 * stop_s)
    out.update(smoothness=round(float(min(1.0, max(0.0, score))), 3), jerk_norm=round(jerk_norm, 3),
               reversals_per_s=round(rev_s, 3), stopstart_per_s=round(stop_s, 3),
               mean_speed=round(float(speed.mean()), 5))

    # per-second local score (jerk over +-1 s relative to local speed, plus local reversals)
    per = int(round(fps))
    local = []
    for sec in range(int(seconds)):
        a, b = max(0, sec * per - per // 2), min(len(jn), (sec + 1) * per + per // 2)
        if b - a < 3:
            continue
        lref = float(speed[a:min(b, len(speed))].mean()) + EPS_SPEED
        lj = float(np.sqrt((jn[a:b] ** 2).mean()) / lref)
        lrev = 0
        for axis in (0, 1):
            s = np.sign(v[axis][a:b]) * (np.abs(v[axis][a:b]) > 0.01)
            nz = s[s != 0]
            lrev += int((nz[1:] != nz[:-1]).sum()) if len(nz) > 1 else 0
        st = state_full[a:min(b, len(state_full))]
        st = st[st >= 0]
        lstop = int((st[1:] != st[:-1]).sum()) if len(st) > 1 else 0
        local.append(round(math.exp(-JERK_K * lj)
                           * max(0.0, 1.0 - 0.35 * lrev * per / (b - a) - 0.30 * lstop * per / (b - a)), 3))
    out["profile"] = local
    out["windows"] = windows_from_profile(local, 0.6, 1.0)
    return out


def windows_from_profile(profile: list[float], min_score: float, seconds_per_bin: float = 1.0,
                         min_len: float = MIN_WINDOW_S) -> list[list[float]]:
    """Runs of bins scoring >= min_score that last >= min_len s -> [[start, end, mean score]]."""
    wins: list[list[float]] = []
    i, n = 0, len(profile)
    while i < n:
        if profile[i] >= min_score:
            j = i
            while j < n and profile[j] >= min_score:
                j += 1
            if (j - i) * seconds_per_bin >= min_len:
                wins.append([round(i * seconds_per_bin, 2), round(j * seconds_per_bin, 2),
                             round(float(np.mean(profile[i:j])), 3)])
            i = j
        else:
            i += 1
    return wins


# ---------------------------------------------------------------------------
# daypart / hyperlapse
# ---------------------------------------------------------------------------

def luma_stats(frames: np.ndarray) -> dict:
    if len(frames) == 0:
        return {}
    idx = np.linspace(0, len(frames) - 1, num=min(12, len(frames))).astype(int)
    sample = frames[idx].astype(np.float32)
    flat = sample.reshape(-1)
    return {"luma_mean": round(float(flat.mean()), 2), "luma_p5": round(float(np.percentile(flat, 5)), 1),
            "luma_p95": round(float(np.percentile(flat, 95)), 1),
            "highlight_frac": round(float((flat > 235).mean()), 4),
            "shadow_frac": round(float((flat < 35).mean()), 4)}


def classify_daypart(stats: dict) -> Optional[str]:
    """day / dusk / night from luma statistics (night: dark overall, often with bright lights)."""
    if not stats:
        return None
    mean, shadow = stats["luma_mean"], stats["shadow_frac"]
    if mean < 62 or (mean < 80 and shadow > 0.55):
        return "night"
    if mean < 100 or (mean < 115 and shadow > 0.35):
        return "dusk"
    return "day"


def hyperlapse_signals(fps: Optional[float], disc_disp: Optional[float], disc_fail: Optional[float]) -> tuple[bool, Optional[str]]:
    fps25 = fps is not None and abs(float(fps) - HYPER_FPS) < 0.05
    disc = bool((disc_disp is not None and disc_disp >= HYPER_DISP) or
                (disc_fail is not None and disc_fail >= HYPER_FAIL))
    if fps25 and disc:
        return True, "fps25+discontinuity"
    if fps25:
        return True, "fps25"
    if disc:
        return True, "discontinuity"
    return False, None


def native_discontinuity(path: str, width: int, height: int, duration: float) -> tuple[Optional[float], Optional[float]]:
    """Median per-frame displacement and tracking-failure rate over short native-rate samples."""
    spots = [0.5] if duration < 4 else [0.25, 0.5, 0.75]
    disp: list[float] = []
    fails = 0
    pairs = 0
    for frac in spots:
        start = max(0.0, duration * frac - 0.5)
        frames = read_gray(path, width, height, None, start=start, duration=1.0, max_frames=26)
        if len(frames) < 4:
            continue
        st = track_steps(frames)
        pairs += len(st["ok"])
        fails += int((~st["ok"]).sum())
        mag = np.hypot(st["dx"], st["dy"])[st["ok"]]
        disp.extend(mag.tolist())
    if not pairs:
        return None, None
    return (round(float(np.median(disp)), 5) if disp else None), round(fails / pairs, 3)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def parse_fps(value: Optional[str]) -> Optional[float]:
    try:
        if value and "/" in value:
            a, b = value.split("/")
            return float(a) / float(b)
        return float(value) if value else None
    except (ValueError, ZeroDivisionError):
        return None


def probe_fps(path: str) -> tuple[Optional[float], Optional[float]]:
    """(nominal fps, average fps). The nominal rate (r_frame_rate) is what the camera recorded
    (25 for DJI hyperlapses); an average well below it means dropped frames = stutter."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=avg_frame_rate,r_frame_rate", "-of", "json", path],
                           capture_output=True, text=True, timeout=60, env=tool_env())
        s = json.loads(r.stdout)["streams"][0]
        nominal = parse_fps(s.get("r_frame_rate")) or parse_fps(s.get("avg_frame_rate"))
        return nominal, parse_fps(s.get("avg_frame_rate")) or nominal
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
        return None, None


def analyze_video(path: str, width: int, height: int, duration: float,
                  fps: Optional[float] = None) -> dict:
    """Full analysis record for one video file (never raises; errors land in ``error``)."""
    t0 = time.monotonic()
    rec: dict = {"version": VERSION, "fps": None, "fps_avg": None, "is_hyperlapse": False, "hyper_signal": None,
                 "disc_disp": None, "disc_fail": None, "daypart": None, "smoothness": None,
                 "profile": [], "windows": [], "error": None}
    try:
        avg = None
        if fps is None:
            fps, avg = probe_fps(path)
        rec["fps"] = round(fps, 3) if fps else None
        rec["fps_avg"] = round(avg, 3) if avg else rec["fps"]
        rec["disc_disp"], rec["disc_fail"] = native_discontinuity(path, width, height, duration)
        hyper, signal = hyperlapse_signals(fps, rec["disc_disp"], rec["disc_fail"])
        rec["is_hyperlapse"], rec["hyper_signal"] = hyper, signal
        span = min(duration, MAX_ANALYSED_S)
        frames = read_gray(path, width, height, ANALYSIS_FPS, duration=span)
        stats = luma_stats(frames)
        rec.update(stats)
        rec["daypart"] = classify_daypart(stats)
        if not hyper and len(frames) >= 8:
            rec.update(smoothness_from_steps(track_steps(frames), ANALYSIS_FPS))
            if rec["smoothness"] is not None and fps and avg and avg < 0.9 * fps:
                # dropped frames play back as stutter: the footage cannot be called smooth
                rec["smoothness"] = round(rec["smoothness"] * max(0.2, avg / (0.9 * fps)), 3)
    except Exception as exc:  # noqa: BLE001 - analysis must never take a worker down
        log.warning("motion analysis failed for %s: %s", path, exc)
        rec["error"] = f"{type(exc).__name__}: {exc}"[:300]
    rec["runtime_s"] = round(time.monotonic() - t0, 2)
    return rec
