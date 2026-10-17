#!/usr/bin/env python3
"""Make media safe for DaVinci Resolve (free build, Linux).

Free Resolve on Linux ships no AAC audio decoder and no consumer H.264/HEVC
decoder. Feeding it iPhone/drone footage produces a decode-failure storm
(36k+ `IO.Audio` errors in one observed session) that takes the app down.

This conditions clips first: video -> DNxHR, audio -> PCM, data tracks dropped.
Only condition the clips an edit actually uses; the source library is 292 GB.
"""
from __future__ import annotations
import argparse, json, os, shutil, subprocess, sys
from pathlib import Path

PASSTHROUGH_VIDEO = {"prores", "dnxhd"}          # Resolve decodes these natively
PASSTHROUGH_AUDIO = {"pcm_s16le", "pcm_s24le", "pcm_f32le"}
# Keyed on pixel count, not height: a 1288x1610 vertical clip is not "taller
# than 4K" and must not land on the highest-bitrate profile.
QUALITY = {"proxy": "dnxhr_lb", "edit": "dnxhr_sq", "finish": "dnxhr_hq"}


def clean_env() -> dict:
    """Resolve leaks /opt/resolve/libs into LD_LIBRARY_PATH, which makes every
    ffmpeg child exit 127. Strip it before spawning."""
    env = dict(os.environ)
    parts = [p for p in env.get("LD_LIBRARY_PATH", "").split(os.pathsep)
             if p and "/opt/resolve" not in p]
    if parts:
        env["LD_LIBRARY_PATH"] = os.pathsep.join(parts)
    else:
        env.pop("LD_LIBRARY_PATH", None)
    return env


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=index,codec_type,codec_name,height,width", "-of", "json", str(path)],
        capture_output=True, text=True, env=clean_env(), check=True).stdout
    streams = json.loads(out).get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    return {"video": video, "audio": audio, "streams": streams}


def plan(info: dict, quality: str = "proxy") -> tuple[bool, str, str]:
    """Return (needs_work, video_action, audio_action)."""
    v, a = info["video"], info["audio"]
    vcodec = (v or {}).get("codec_name", "")
    acodec = (a or {}).get("codec_name", "")
    v_action = "copy" if vcodec in PASSTHROUGH_VIDEO else QUALITY[quality]
    a_action = "none" if a is None else ("copy" if acodec in PASSTHROUGH_AUDIO else "pcm_s16le")
    needs = v_action != "copy" or a_action == "pcm_s16le"
    return needs, v_action, a_action


def condition(src: Path, outdir: Path, force: bool = False, quality: str = "proxy", *,
              start: float | None = None, duration: float | None = None, vf: str | None = None,
              name: str | None = None, force_video: bool = False) -> dict:
    """Condition one clip (or just ``start``/``duration`` of it, named ``name``).

    ``vf`` is an extra video filter chain (e.g. HDR->SDR tonemap) and, like
    ``force_video`` (rotated sources: DNxHR cannot carry a rotation flag, so the
    frames are rotated by ffmpeg), forces a video transcode.
    """
    info = probe(src)
    needs, v_action, a_action = plan(info, quality)
    if (vf or force_video) and v_action == "copy":
        v_action, needs = QUALITY[quality], True
    dst = outdir / ((name or src.stem) + ".mov")

    if not needs:
        return {"src": str(src), "dst": str(src), "action": "already-safe",
                "video": v_action, "audio": a_action}
    if dst.exists() and not force and dst.stat().st_mtime >= src.stat().st_mtime:
        return {"src": str(src), "dst": str(dst), "action": "cached",
                "video": v_action, "audio": a_action}

    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if start is not None:
        cmd += ["-ss", f"{max(start, 0):.3f}"]                    # input seek: fast, frame-exact for intra codecs
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-i", str(src), "-map", "0:v:0"]
    if info["audio"] is not None:
        cmd += ["-map", "0:a:0"]          # first audio track only; drops data tracks
    if v_action == "copy":
        cmd += ["-c:v", "copy"]
    else:
        if vf:
            cmd += ["-vf", vf]
        cmd += ["-c:v", "dnxhd", "-profile:v", v_action, "-pix_fmt", "yuv422p"]
    if a_action == "none":
        pass
    elif a_action == "copy":
        cmd += ["-c:a", "copy"]
    else:
        cmd += ["-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2"]
    tmp = dst.with_suffix(".mov.part")
    cmd += ["-f", "mov", str(tmp)]   # .part defeats extension-based muxer detection

    outdir.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(cmd, capture_output=True, text=True, env=clean_env())
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        return {"src": str(src), "dst": None, "action": "failed",
                "error": (r.stderr or "")[-600:], "cmd": " ".join(cmd)}
    tmp.replace(dst)
    return {"src": str(src), "dst": str(dst), "action": "conditioned",
            "video": v_action, "audio": a_action,
            "size_in": src.stat().st_size, "size_out": dst.stat().st_size}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="+", type=Path)
    ap.add_argument("--outdir", type=Path,
                    default=Path.home() / "mediaforge" / "data" / "resolve-ready")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--quality", choices=sorted(QUALITY), default="proxy",
                    help="proxy=DNxHR LB (default), edit=SQ, finish=HQ")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("ffmpeg/ffprobe not found on PATH", file=sys.stderr)
        return 2

    results = []
    for p in args.paths:
        if not p.exists():
            results.append({"src": str(p), "action": "missing"})
            continue
        if args.dry_run:
            info = probe(p)
            needs, v, a = plan(info, args.quality)
            results.append({"src": str(p), "action": "would-condition" if needs else "already-safe",
                            "video": v, "audio": a})
        else:
            results.append(condition(p, args.outdir, args.force, args.quality))
    print(json.dumps(results, indent=2))
    return 0 if all(r.get("action") != "failed" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
