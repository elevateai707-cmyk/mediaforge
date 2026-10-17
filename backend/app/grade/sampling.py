"""ffprobe + frame sampling with an SDR (Rec.709) guarantee."""
from __future__ import annotations
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from fractions import Fraction
from pathlib import Path
import numpy as np

from ..proc import tool_env

HDR_TRANSFERS = ("arib-std-b67", "smpte2084")
# Same chain the editor renderer uses for HLG/PQ -> SDR (render_v2.py).
HDR_TO_SDR = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
              "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv")
VIDEO_EXT = {".mov", ".mp4", ".m4v", ".mkv", ".avi", ".mts", ".mxf", ".webm"}


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, env=tool_env(), **kw)


def probe(path) -> dict:
    """Display-oriented facts about a video file (rotation applied)."""
    r = run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            text=True, timeout=60)
    if r.returncode != 0:
        raise ValueError(f"ffprobe failed for {path}: {r.stderr[-200:]}")
    data = json.loads(r.stdout)
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not v:
        raise ValueError(f"no video stream in {path}")
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(round(float(sd["rotation"])))
    rot = int(v.get("tags", {}).get("rotate", rot) or rot)
    w, h = int(v["width"]), int(v["height"])
    if abs(rot) % 180 == 90:
        w, h = h, w
    try:
        fps = float(Fraction(v.get("avg_frame_rate") or v.get("r_frame_rate") or "30/1"))
    except Exception:
        fps = 30.0
    if fps <= 0:
        fps = float(Fraction(v.get("r_frame_rate", "30/1")))
    fmt = data.get("format", {})
    dur = float(v.get("duration") or fmt.get("duration") or 0)
    created = (fmt.get("tags", {}) or {}).get("creation_time") or (v.get("tags", {}) or {}).get("creation_time")
    taken = None
    if created:
        try:
            taken = datetime.fromisoformat(created.replace("Z", "+00:00")).replace(tzinfo=None).isoformat()
        except ValueError:
            taken = None
    return {
        "path": str(path), "width": w, "height": h, "rotation": rot, "fps": fps,
        "codec": v.get("codec_name"), "pix_fmt": v.get("pix_fmt"),
        "transfer": v.get("color_transfer", "unspecified"), "primaries": v.get("color_primaries"),
        "space": v.get("color_space"), "duration": dur,
        "has_audio": any(s.get("codec_type") == "audio" for s in data["streams"]),
        "hdr": v.get("color_transfer") in HDR_TRANSFERS, "taken_at": taken,
    }


def sdr_filter(info: dict) -> str:
    """Filter-chain prefix that yields Rec.709 SDR frames ('' when already SDR)."""
    return HDR_TO_SDR + "," if info.get("hdr") else ""


def matrix_opt(info: dict) -> str:
    """Pin the YUV->RGB matrix to 709 for untagged HD sources (sws guesses 601)."""
    if info.get("hdr"):
        return ""
    if info.get("space") in (None, "unknown", "unspecified") and info["height"] >= 720:
        return ":in_color_matrix=bt709"
    return ""


def frame_size(info: dict, long_side: int) -> tuple[int, int]:
    w, h = info["width"], info["height"]
    k = long_side / max(w, h)
    return max(2, int(round(w * k / 2)) * 2), max(2, int(round(h * k / 2)) * 2)


def _one_frame(info: dict, t: float, size: tuple[int, int]):
    w, h = size
    vf = f"{sdr_filter(info)}scale={w}:{h}:flags=area{matrix_opt(info)},format=rgb24"
    r = run(["ffmpeg", "-v", "error", "-ss", f"{max(t, 0):.3f}", "-i", info["path"], "-frames:v", "1",
             "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], timeout=180)
    if r.returncode != 0 or len(r.stdout) < w * h * 3:
        return None
    return np.frombuffer(r.stdout[: w * h * 3], dtype=np.uint8).reshape(h, w, 3).astype(np.float32) / 255.0


def sample_frames(info: dict, start: float, end: float, n: int = 4, long_side: int = 288,
                  workers: int = 4) -> list[np.ndarray]:
    """n frames evenly spread inside [start, end) as float RGB in [0,1]."""
    end = max(end, start + 0.04)
    if info.get("duration"):
        end = min(end, info["duration"])
        start = min(start, max(0.0, end - 0.04))
    ts = [start + (i + 0.5) / n * (end - start) for i in range(n)]
    size = frame_size(info, long_side)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        frames = list(ex.map(lambda t: _one_frame(info, t, size), ts))
    return [f for f in frames if f is not None]


def load_image(path, long_side: int = 512) -> np.ndarray:
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    im.thumbnail((long_side, long_side))
    return np.asarray(im, dtype=np.float32) / 255.0


def pixels(frames: list[np.ndarray], per_frame: int, rng: np.random.Generator) -> np.ndarray:
    """Random pixel sample pooled over frames -> (n,3)."""
    out = []
    for f in frames:
        flat = f.reshape(-1, 3)
        idx = rng.choice(len(flat), size=min(per_frame, len(flat)), replace=False)
        out.append(flat[idx])
    return np.concatenate(out) if out else np.zeros((0, 3), np.float32)
