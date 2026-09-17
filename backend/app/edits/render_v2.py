"""Snapshot renderer. Frame-normalized trims, bounded transitions, libass and audio mix."""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from .. import config
from ..proc import tool_env
from .project import Project, timeline
from .render import RenderError, probe_json, probe_fps
from .subtitles import ass_document, sidecar


def capabilities():
    def output(args):
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", *args],
            capture_output=True,
            text=True,
            env=tool_env(),
            timeout=15,
        )
        return r.stdout + r.stderr

    filters = output(["-filters"])
    enc = output(["-encoders"])
    # Verify NVENC can actually initialize; encoder listing alone is insufficient.
    nv = False
    if "h264_nvenc" in enc:
        r = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=s=64x64:d=0.1",
                "-c:v",
                "h264_nvenc",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            env=tool_env(),
            timeout=15,
        )
        nv = r.returncode == 0
    return {
        "libass": " ass " in filters,
        "tonemap": " tonemap " in filters and " zscale " in filters,
        "codecs": ["libx264"] + (["h264_nvenc"] if nv else []),
    }


def run_ffmpeg(cmd, progress, cancel, duration):
    with tempfile.TemporaryFile(mode="w+t") as errors:
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=errors, text=True, env=tool_env()
        )
        import selectors

        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        start = time.monotonic()
        try:
            while process.poll() is None:
                if cancel.cancelled:
                    raise asyncio.CancelledError()
                if time.monotonic() - start > 7200:
                    raise RenderError("Render exceeded two hours")
                for key, _ in selector.select(0.2):
                    line = key.fileobj.readline().strip()
                    if line.startswith("out_time_us="):
                        try:
                            progress(
                                min(
                                    0.98,
                                    float(line.split("=")[1])
                                    / 1e6
                                    / max(duration, 0.1),
                                ),
                                "Encoding video",
                            )
                        except ValueError:
                            pass
            if process.returncode:
                errors.seek(0)
                raise RenderError(errors.read()[-2500:])
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            selector.close()
            process.stdout.close()


def render_snapshot(document, paths, progress, cancel, preview=False):
    p = Project.model_validate(document)
    tl = timeline(p)
    if not tl:
        raise RenderError("Empty timeline")
    total = tl[-1]["start"] + tl[-1]["duration"]
    first = paths.get(p.clips[0].asset_id) or paths[str(p.clips[0].asset_id)]
    fps = p.export.fps or probe_fps(first["path"])
    width = min(480, p.export.width) if preview else p.export.width
    height = (
        round(width * ({"9:16": 16 / 9, "16:9": 9 / 16, "1:1": 1}[p.export.ratio]) / 2)
        * 2
    )
    cap = capabilities()
    codec = p.export.codec if p.export.codec in cap["codecs"] else "libx264"
    root = config.EXPORTS_DIR
    root.mkdir(parents=True, exist_ok=True)
    ident = uuid.uuid4().hex
    out = root / f"{'preview' if preview else 'reel'}_{ident}.mp4"
    incomplete = root / f".{ident}.partial.mp4"
    with tempfile.TemporaryDirectory(prefix="mf_render_") as tmp:
        cmd = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-nostdin",
            "-filter_complex_threads",
            "1",
        ]
        filters = []
        idx = 0
        for i, item in enumerate(tl):
            c = item["clip"]
            a = paths.get(c.asset_id) or paths[str(c.asset_id)]
            path = a["path"]
            photo = a["kind"] == "photo"
            info = probe_json(path)
            streams = info.get("streams", [])
            if not streams:
                raise RenderError("Source cannot be decoded")
            video = next((s for s in streams if s["codec_type"] == "video"), {})
            if photo:
                cmd += ["-loop", "1", "-t", str(item["duration"]), "-i", path]
            else:
                cmd += ["-i", path]
            tone = ""
            if video.get("color_transfer") in ("smpte2084", "arib-std-b67"):
                if p.export.hdr == "reject" or not cap["tonemap"]:
                    raise RenderError(
                        "HDR source needs zscale/tonemap support or an SDR source"
                    )
                tone = "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,"
            trim = (
                f"trim=start={c.start}:end={c.end},setpts=(PTS-STARTPTS)/{c.speed}"
                if not photo
                else f"trim=duration={item['duration']},setpts=PTS-STARTPTS"
            )
            filters.append(
                f"[{idx}:v]{trim},{tone}fps={fps},scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p,settb=AVTB[v{i}]"
            )
            audio = any(s["codec_type"] == "audio" for s in streams)
            if audio and not photo:
                speed = c.speed
                tempos = []
                while speed > 2:
                    tempos.append("atempo=2")
                    speed /= 2
                while speed < 0.5:
                    tempos.append("atempo=0.5")
                    speed /= 0.5
                tempos.append(f"atempo={speed}")
                filters.append(
                    f"[{idx}:a]atrim=start={c.start}:end={c.end},asetpts=PTS-STARTPTS,{','.join(tempos)},aresample=48000,aformat=channel_layouts=stereo,apad,atrim=duration={item['duration']}[a{i}]"
                )
            else:
                filters.append(
                    f"anullsrc=r=48000:cl=stereo,atrim=duration={item['duration']}[a{i}]"
                )
            idx += 1
        v = "v0"
        a = "a0"
        for i, item in enumerate(tl[1:], 1):
            if item["overlap"]:
                filters.append(
                    f"[{v}][v{i}]xfade=transition=fade:duration={item['overlap']}:offset={item['start']}[vx{i}]"
                )
                filters.append(f"[{a}][a{i}]acrossfade=d={item['overlap']}[ax{i}]")
            else:
                filters.append(f"[{v}][v{i}]concat=n=2:v=1:a=0[vx{i}]")
                filters.append(f"[{a}][a{i}]concat=n=2:v=0:a=1[ax{i}]")
            v = f"vx{i}"
            a = f"ax{i}"

        def audio_filter(label, track, name):
            volume = track.volume if track.enabled else 0
            f = f"[{label}]volume={volume}"
            if track.fade_in:
                f += f",afade=t=in:st={track.start}:d={track.fade_in}"
            if track.fade_out:
                f += f",afade=t=out:st={max(0, total - track.fade_out)}:d={track.fade_out}"
            filters.append(f + "[" + name + "]")

        audio_filter(a, p.original_audio, "original")
        audio_labels = ["original"]
        for name, track in [("narration", p.voice_over), ("music", p.music)]:
            if not track.enabled or not track.path:
                continue
            if name == "music":
                cmd += ["-stream_loop", "-1"]
            cmd += ["-i", track.path]
            filters.append(
                f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,adelay={round(track.start * 1000)}:all=1,apad,atrim=duration={total}[{name}raw]"
            )
            idx += 1
            audio_filter(name + "raw", track, name)
            if name == "narration":
                audio_labels.append(name)
        filters.append(
            "".join(f"[{x}]" for x in audio_labels)
            + f"amix=inputs={len(audio_labels)}:duration=longest:normalize=0[voice]"
        )
        if p.music.enabled and p.music.path:
            if p.ducking:
                filters.append("[voice]asplit=2[voiceMix][voiceKey]")
                filters.append(
                    f"[music][voiceKey]sidechaincompress=threshold=0.03:ratio={p.ducking_ratio}:attack=20:release=250[duck]"
                )
                filters.append(
                    "[voiceMix][duck]amix=inputs=2:normalize=0:duration=first[mix]"
                )
            else:
                filters.append(
                    "[voice][music]amix=inputs=2:normalize=0:duration=first[mix]"
                )
        else:
            filters.append("[voice]anull[mix]")
        filters.append("[mix]alimiter=limit=0.95[aout]")
        if p.speech_captions or p.on_video_text:
            if not cap["libass"]:
                raise RenderError("FFmpeg requires libass for enabled text")
            ass = Path(tmp) / "text.ass"
            ass.write_text(
                ass_document(p, width, height, p.speech_captions, p.on_video_text)
            )
            filters.append(f"[{v}]ass=filename='{ass}'[vout]")
        else:
            filters.append(f"[{v}]null[vout]")
        cmd += [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            codec,
        ]
        cmd += (
            ["-crf", "18", "-preset", "fast" if preview else "medium"]
            if codec == "libx264"
            else ["-cq", "19", "-preset", "p5"]
        )
        cmd += [
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-t",
            str(total),
            "-metadata:s:v",
            "rotate=0",
            "-movflags",
            "+faststart",
            "-progress",
            "pipe:1",
            str(incomplete),
        ]
        try:
            run_ffmpeg(cmd, progress, cancel, total)
            if cancel.cancelled:
                raise asyncio.CancelledError()
            incomplete.replace(out)
        finally:
            incomplete.unlink(missing_ok=True)
    outputs = {"video": str(out), "revision": p.revision, "preview": preview}
    if not preview:
        for kind in p.export.sidecars:
            path = out.with_suffix("." + kind)
            path.write_text(sidecar(p, kind), encoding="utf8")
            outputs[kind] = str(path)
        if p.export.clean_master and (p.speech_captions or p.on_video_text):
            clean = p.model_copy(deep=True)
            clean.speech_captions = False
            clean.on_video_text = False
            clean.export.clean_master = False
            clean.export.sidecars = []
            outputs["clean_master"] = render_snapshot(
                clean.model_dump(), paths, progress, cancel
            )["video"]
    return outputs
