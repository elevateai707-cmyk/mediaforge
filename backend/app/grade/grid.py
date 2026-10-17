"""Comparison grid (2x2: Balanced + up to 3 looks) and graded previews.

Layout rule: every quadrant is rendered at the source's *native display size*
(rotation applied), never upscaled, capped at 1920x1080-class (a 4K phone clip is
delivered as a 1080-class quadrant).  1920x1080 sources -> 3840x2160 grid;
1080x1920 vertical -> 2160x3840.  ``quad_cap`` overrides the cap.
"""
from __future__ import annotations
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from ..proc import tool_env
from .sampling import matrix_opt, probe, sdr_filter

FONT_CANDIDATES = [
    Path.home() / ".local/share/fonts/mediaforge-styles/MontserratSemiBold.ttf",
    Path.home() / ".local/share/fonts/mediaforge-styles/MontserratMedium.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
]
QUAD_ORDER = [(0, 0), (1, 0), (0, 1), (1, 1)]      # TL, TR, BL, BR


# ---- layout math -------------------------------------------------------------
def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def quad_size(src_w: int, src_h: int, cap: tuple[int, int] = (1920, 1080)) -> tuple[int, int]:
    """Native display size, capped (landscape: fit WxH cap; portrait: fit HxW cap), never upscaled."""
    portrait = src_h > src_w
    cw, ch = (cap[1], cap[0]) if portrait else (cap[0], cap[1])
    k = min(1.0, cw / src_w, ch / src_h)
    return _even(src_w * k), _even(src_h * k)


def grid_layout(qw: int, qh: int) -> dict:
    return {"quad": (qw, qh), "canvas": (2 * qw, 2 * qh),
            "positions": [(cx * qw, cy * qh) for cx, cy in QUAD_ORDER]}


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_") or "x"


def esc(path) -> str:
    """Escape a path for use inside an ffmpeg filtergraph option value."""
    return "'" + str(path).replace("\\", "\\\\").replace("'", "'\\''").replace(":", "\\:") + "'"


# ---- montage selection -------------------------------------------------------
def select_montage(items: list[dict], per_beat: int = 2, seg_len: float = 3.5, max_total: float = 36.0,
                   min_len: float = 2.0, durations: Optional[dict] = None) -> list[dict]:
    """Representative segments: 1..per_beat per scene/beat, one per source file, seg_len s each."""
    durations = durations or {}
    groups: dict = {}
    for it in items:
        if it["beat"] == 0 and any(i["beat"] for i in items):
            continue
        groups.setdefault(it["beat"], []).append(it)
    segs, used = [], 0.0
    for beat in sorted(groups):
        picked, files = [], set()
        for it in sorted(groups[beat], key=lambda i: (-i["confidence"], i["order"])):
            if it["path"] in files:
                continue
            files.add(it["path"])
            picked.append(it)
            if len(picked) >= per_beat:
                break
        for it in sorted(picked, key=lambda i: i["order"]):
            src_dur = durations.get(it["path"], it["end"])
            start = it["start"]
            avail = max(0.0, src_dur - start)
            if avail < min_len:
                start = max(0.0, src_dur - min_len)
                avail = src_dur - start
            dur = round(max(0.5, min(seg_len, avail)), 3)
            if used + dur > max_total:
                continue
            used += dur
            segs.append({"beat": beat, "path": it["path"], "start": round(start, 3), "dur": dur,
                         "label": it["label"], "name": it["name"]})
    return segs


# ---- labels ----------------------------------------------------------------
def _font():
    for f in FONT_CANDIDATES:
        if f.exists():
            return str(f)
    return None


def small_caps_width(draw, text, font_big, font_small, tracking):
    w = 0.0
    for ch in text:
        f = font_big if ch.isupper() or not ch.isalpha() else font_small
        w += draw.textlength(ch.upper(), font=f) + tracking
    return w - tracking


def draw_small_caps(draw, xy, text, font_big, font_small, tracking, fill):
    x, y = xy
    base = font_big.getmetrics()[0]
    for ch in text:
        f = font_big if ch.isupper() or not ch.isalpha() else font_small
        asc = f.getmetrics()[0]
        draw.text((x, y + (base - asc)), ch.upper(), font=f, fill=fill)
        x += draw.textlength(ch.upper(), font=f) + tracking


def make_label_canvas(path, canvas: tuple[int, int], quad: tuple[int, int], labels: list[str]) -> Path:
    """One transparent PNG with the four labels (clean small caps on a soft pill)."""
    from PIL import Image, ImageDraw, ImageFont
    cw, ch = canvas
    qw, qh = quad
    short = min(qw, qh)
    size = max(14, int(round(short * 0.034)))
    ff = _font()
    big = ImageFont.truetype(ff, size) if ff else ImageFont.load_default()
    small = ImageFont.truetype(ff, int(size * 0.8)) if ff else big
    tracking = size * 0.14
    img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad_x, pad_y, margin = int(size * 0.7), int(size * 0.45), int(short * 0.035)
    for (x0, y0), text in zip([(cx * qw, cy * qh) for cx, cy in QUAD_ORDER], labels):
        tw = small_caps_width(d, text, big, small, tracking)
        th = big.getmetrics()[0] + big.getmetrics()[1]
        bx, by = x0 + margin, y0 + qh - margin - th - 2 * pad_y
        d.rounded_rectangle([bx, by, bx + tw + 2 * pad_x, by + th + 2 * pad_y], radius=int(size * 0.5), fill=(0, 0, 0, 120))
        draw_small_caps(d, (bx + pad_x, by + pad_y), text, big, small, tracking, (255, 255, 255, 235))
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    img.save(p)
    return p


# ---- encoding ----------------------------------------------------------------
_ENC: Optional[list] = None


def encoder_args(w: int, h: int) -> list[str]:
    """h264_nvenc when it works at this size (quick probe), else libx264."""
    global _ENC
    key = (w, h)
    if _ENC is None:
        _ENC = []
    for k, v in _ENC:
        if k == key:
            return v
    nv = ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0", "-profile:v", "high"]
    r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color=c=gray:s={w}x{h}:d=0.2:r=10",
                        "-frames:v", "2", *nv, "-f", "null", "-"], capture_output=True, env=tool_env())
    args = nv if r.returncode == 0 else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "17"]
    _ENC.append((key, args))
    return args


COLOR_TAGS = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
TO_YUV = "scale=flags=lanczos+accurate_rnd+full_chroma_int:out_color_matrix=bt709:out_range=tv,format=yuv420p"


def _src_chain(info: dict, qw: int, qh: int, fps: int) -> str:
    return (f"{sdr_filter(info)}scale={qw}:{qh}:force_original_aspect_ratio=increase:flags=lanczos+accurate_rnd"
            f"{matrix_opt(info)},crop={qw}:{qh},fps={fps},setsar=1,format=gbrp10le")


def _run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, env=tool_env())
    if r.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + r.stderr[-600:] + "\n" + " ".join(cmd[:12]))


def render_grid_piece(seg: dict, info: dict, variants: list[list[Path]], quad: tuple[int, int], fps: int,
                      labels_png: Path, out: Path) -> Path:
    """One 2x2 segment. variants[i] = ordered .cube files for quadrant i (corr first)."""
    qw, qh = quad
    chain = _src_chain(info, qw, qh, fps)
    parts = [f"[0:v]{chain},split={len(variants)}" + "".join(f"[s{i}]" for i in range(len(variants)))]
    tails = []
    for i, cubes in enumerate(variants):
        f = ",".join(f"lut3d=file={esc(c)}:interp=tetrahedral" for c in cubes)
        parts.append(f"[s{i}]{f},{TO_YUV}[q{i}]")
        tails.append(f"[q{i}]")
    layout = "0_0|w0_0|0_h0|w0_h0"
    parts.append("".join(tails) + f"xstack=inputs={len(variants)}:layout={layout}[g]")
    parts.append("[g][1:v]overlay=0:0:format=auto,format=yuv420p[v]")
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{seg['start']:.3f}", "-t", f"{seg['dur']:.3f}", "-i", seg["path"],
           "-loop", "1", "-framerate", str(fps), "-i", str(labels_png),
           "-filter_complex", ";".join(parts), "-map", "[v]", "-t", f"{seg['dur']:.3f}", "-an",
           *encoder_args(2 * qw, 2 * qh), *COLOR_TAGS, "-r", str(fps), str(out)]
    _run(cmd)
    return out


def concat(pieces: list[Path], out: Path) -> Path:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p}'\n" for p in pieces))
    _run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
          "-movflags", "+faststart", str(out)])
    lst.unlink()
    return out


def render_grid(job_dir: Path, segments: list[dict], corr_cubes: dict, look_cubes: list[tuple[str, Path]],
                out: Path, quad: Optional[tuple[int, int]] = None, fps: int = 30, workers: int = 2,
                cap: tuple[int, int] = (1920, 1080)) -> dict:
    """Labelled 2x2: Balanced + up to 3 looks, over the montage segments."""
    looks = look_cubes[:3]
    infos = {s["path"]: probe(s["path"]) for s in segments}
    if quad is None:
        first = infos[segments[0]["path"]]
        quad = quad_size(first["width"], first["height"], cap)
    names = ["Balanced"] + [n for n, _ in looks]
    pretty = [re.sub(r"[_-]+", " ", n).strip().title() if n != "Balanced" else "Balanced" for n in names]
    lay = grid_layout(*quad)
    work = Path(job_dir) / "grid_work"
    work.mkdir(parents=True, exist_ok=True)
    png = make_label_canvas(work / "labels.png", lay["canvas"], quad, pretty)
    jobs = []
    for i, s in enumerate(segments):
        corr = corr_cubes[s["path"]]
        variants = [[corr]] + [[corr, c] for _, c in looks]
        while len(variants) < 4:                    # fewer than 3 looks: repeat Balanced so the grid stays 2x2
            variants.append([corr])
        jobs.append((s, infos[s["path"]], variants, work / f"piece_{i:03d}.mp4"))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        pieces = list(ex.map(lambda j: render_grid_piece(j[0], j[1], j[2], quad, fps, png, j[3]), jobs))
    concat(pieces, out)
    shutil.rmtree(work, ignore_errors=True)
    return {"path": str(out), "canvas": lay["canvas"], "quad": quad, "segments": len(segments),
            "seconds": round(sum(s["dur"] for s in segments), 2), "labels": pretty, "bytes": out.stat().st_size}


def telegram_copy(src: Path, out: Path, max_mb: float = 46.0, long_side: int = 1920) -> dict:
    """Downscaled copy that fits Telegram's upload limit (iterates the bitrate down)."""
    info = probe(src)
    dur = max(info["duration"], 0.5)
    k = min(1.0, long_side / max(info["width"], info["height"]))
    w, h = _even(info["width"] * k), _even(info["height"] * k)
    kbps = int(max_mb * 8 * 1024 * 0.92 / dur)
    for _ in range(4):
        _run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", f"scale={w}:{h}:flags=lanczos", "-an",
              "-c:v", "libx264", "-preset", "medium", "-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.4)}k",
              "-bufsize", f"{kbps * 2}k", "-pix_fmt", "yuv420p", *COLOR_TAGS, "-movflags", "+faststart", str(out)])
        if out.stat().st_size <= max_mb * 1024 * 1024:
            break
        kbps = int(kbps * 0.8)
    return {"path": str(out), "size": [w, h], "bytes": out.stat().st_size}


# ---- graded preview of the full assembly ------------------------------------
def render_graded_clip(item: dict, info: dict, cubes: list[Path], size: tuple[int, int], fps: int, out: Path) -> Path:
    qw, qh = size
    chain = _src_chain(info, qw, qh, fps)
    f = ",".join(f"lut3d=file={esc(c)}:interp=tetrahedral" for c in cubes)
    vf = f"{chain}" + (f",{f}" if f else "") + f",{TO_YUV}"
    dur = max(0.2, item["end"] - item["start"])
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{item['start']:.3f}", "-t", f"{dur:.3f}", "-i", item["path"],
           "-vf", vf, "-an", *encoder_args(qw, qh), *COLOR_TAGS, "-r", str(fps), str(out)]
    _run(cmd)
    return out


def render_preview(items: list[dict], cubes_for: dict, out: Path, work: Path, long_side: int = 1280,
                   fps: int = 30, workers: int = 2) -> dict:
    """Graded assembly preview: each item through its LUT chain, in assembly order."""
    work.mkdir(parents=True, exist_ok=True)
    infos = {p: probe(p) for p in {i["path"] for i in items}}
    first = infos[items[0]["path"]]
    size = quad_size(first["width"], first["height"], (long_side, int(long_side * 9 / 16)))
    jobs = [(it, infos[it["path"]], cubes_for[it["path"]], work / f"clip_{n:03d}.mp4") for n, it in enumerate(items)]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        pieces = list(ex.map(lambda j: render_graded_clip(j[0], j[1], j[2], size, fps, j[3]), jobs))
    concat(pieces, out)
    shutil.rmtree(work, ignore_errors=True)
    return {"path": str(out), "size": list(size), "clips": len(items), "bytes": out.stat().st_size}
