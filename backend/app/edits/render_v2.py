"""Snapshot renderer. Frame-normalized trims, bounded transitions, libass and audio mix."""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

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


def gain_expr(points):
    """ffmpeg volume expression: piecewise-linear dB curve over timeline time."""
    pts = [(g.t, g.db) for g in points]
    expr = f"{pts[-1][1]}"
    for (t0, d0), (t1, d1) in reversed(list(zip(pts, pts[1:]))):
        expr = f"if(lt(t,{t1}),{d0}+({d1}-{d0})*(t-{t0})/{t1 - t0},{expr})"
    expr = f"if(lt(t,{pts[0][0]}),{pts[0][1]},{expr})"
    return f"pow(10,({expr})/20)"


def look_filter(clip):
    """hue filter (with trailing comma) for a clip's B&W / colour-reveal look."""
    if clip.look != "bw":
        return ""
    if clip.reveal_at is None:
        return "hue=s=0,"
    a, d = round(clip.reveal_at, 4), round(clip.reveal_dur, 4)
    return f"hue=s='clip((t-{a})/{d},0,1)',"


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def inset_assets(tmp, n, wt, ht, inset):
    """Rounded-corner alpha mask and (optional) shadow/border frame PNGs.

    Returns (mask_path, frame_path|None, margin_px)."""
    ss = 4
    radius = inset.radius * min(wt, ht)
    mask = Image.new("L", (wt * ss, ht * ss), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, wt * ss - 1, ht * ss - 1), radius=radius * ss, fill=255
    )
    mask_path = Path(tmp) / f"inset{n}_mask.png"
    mask.resize((wt, ht), Image.LANCZOS).save(mask_path)
    margin = 0
    frame_path = None
    if inset.shadow or inset.border:
        margin = int(min(wt, ht) * 0.12) + 2
        w, h = wt + 2 * margin, ht + 2 * margin
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        if inset.shadow:
            sh = Image.new("L", (w, h), 0)
            off = int(margin * 0.35)
            ImageDraw.Draw(sh).rounded_rectangle(
                (margin, margin + off, margin + wt, margin + ht + off),
                radius=radius,
                fill=150,
            )
            sh = sh.filter(ImageFilter.GaussianBlur(margin * 0.32))
            layer.paste((0, 0, 0, 255), (0, 0), sh)
        if inset.border:
            b = max(2, int(min(wt, ht) * 0.012))
            rgb = tuple(int(inset.border[i : i + 2], 16) for i in (1, 3, 5))
            ring = Image.new("L", (w * ss, h * ss), 0)
            d = ImageDraw.Draw(ring)
            d.rounded_rectangle(
                (margin * ss - b * ss, margin * ss - b * ss,
                 (margin + wt) * ss + b * ss, (margin + ht) * ss + b * ss),
                radius=(radius + b) * ss, fill=255,
            )
            d.rounded_rectangle(
                (margin * ss, margin * ss, (margin + wt) * ss, (margin + ht) * ss),
                radius=radius * ss, fill=0,
            )
            layer.paste(rgb + (255,), (0, 0), ring.resize((w, h), Image.LANCZOS))
        frame_path = Path(tmp) / f"inset{n}_frame.png"
        layer.save(frame_path)
    return mask_path, frame_path, margin


def inset_graph(o, n, idx, base, width, height, fps, total, tmp, cmd, filters):
    """Append inputs + filters compositing one inset overlay on `base`; returns
    (new label, next input index)."""
    ins = o.inset
    start, end = o.start, min(total, o.end)
    dur = max(0.1, end - start)
    wt = max(2, round(ins.w * width / 2) * 2)
    ht = max(2, round(ins.h * height / 2) * 2)
    mask, frame, m = inset_assets(tmp, n, wt, ht, ins)
    is_image = Path(o.media).suffix.lower() in IMAGE_SUFFIXES
    if is_image:
        cmd += ["-loop", "1", "-framerate", str(fps), "-t", f"{dur + 1:.3f}", "-i", o.media]
    else:
        cmd += ["-ss", f"{ins.src_start:.3f}", "-t", f"{dur + 1:.3f}", "-i", o.media]
    cmd += ["-loop", "1", "-framerate", str(fps), "-t", f"{dur + 1:.3f}", "-i", str(mask)]
    media_i, mask_i, frame_i = idx, idx + 1, None
    idx += 2
    if frame:
        cmd += ["-loop", "1", "-framerate", str(fps), "-t", f"{dur + 1:.3f}", "-i", str(frame)]
        frame_i = idx
        idx += 1
    gray = "hue=s=0," if ins.gray else ""
    shift = f"setpts=PTS-STARTPTS+{start:.4f}/TB"
    ms = ins.anim_ms / 1000
    fade = ""
    if ins.anim != "none" and ms > 0:
        fade = (
            f",fade=t=in:st={start:.4f}:d={ms}:alpha=1"
            f",fade=t=out:st={max(start, end - ms):.4f}:d={ms}:alpha=1"
        )
    filters.append(
        f"[{media_i}:v]scale={wt}:{ht}:force_original_aspect_ratio=increase,crop={wt}:{ht},"
        f"fps={fps},tpad=stop_mode=clone:stop=300,{gray}setsar=1,format=yuva420p[im{n}]"
    )
    filters.append(f"[{mask_i}:v]format=gray[mk{n}]")
    filters.append(f"[im{n}][mk{n}]alphamerge,{shift}{fade}[tile{n}]")
    x, y = round(ins.x * width), round(ins.y * height)
    ey = str(y)
    if ins.anim in ("slide-up", "slide-down", "pop"):
        sign = {"slide-up": "+", "slide-down": "-", "pop": "+"}[ins.anim]
        amp = round((0.06 if ins.anim != "pop" else 0.02) * height)
        ey = f"{y}{sign}pow(1-clip((t-{start:.4f})/{max(ms, 0.05)},0,1),2)*{amp}"
    enable = f"enable='between(t,{start:.4f},{end:.4f})'"
    label = base
    if frame:
        filters.append(f"[{frame_i}:v]format=yuva420p,{shift}{fade}[fr{n}]")
        filters.append(
            f"[{label}][fr{n}]overlay=x={x - m}:y='{ey}-{m}':{enable}:eof_action=pass[ov{n}f]"
        )
        label = f"ov{n}f"
    filters.append(
        f"[{label}][tile{n}]overlay=x={x}:y='{ey}':{enable}:eof_action=pass[ov{n}]"
    )
    return f"ov{n}", idx


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
            # Seek close to the clip's in-point instead of decoding the whole file up
            # to it (4K ProRes/HEVC sources are minutes long). The trim below is
            # relative to the seek point; frame selection is unchanged.
            seek = 0.0
            if photo:
                cmd += ["-loop", "1", "-t", str(item["duration"]), "-i", path]
            elif c.start > 1.5:
                seek = round(c.start - 1.0, 6)
                cmd += ["-ss", str(seek), "-t", str(round(c.end - seek + 1.0, 6)), "-i", path]
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
                f"trim=start={round(c.start - seek, 6)}:end={round(c.end - seek, 6)},setpts=(PTS-STARTPTS)/{c.speed}"
                if not photo
                else f"trim=duration={item['duration']},setpts=PTS-STARTPTS"
            )
            framing = (
                f"scale={width}:{height}:force_original_aspect_ratio=increase:force_divisible_by=2,crop={width}:{height}"
                if p.export.fit == "cover" else
                f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
            )
            filters.append(
                f"[{idx}:v]{trim},{tone}{look_filter(c)}fps={fps},tpad=stop_mode=clone:stop=3,trim=end_frame={round(item['duration'] * fps)},{framing},setsar=1,format=yuv420p,settb=AVTB[v{i}]"
            )
            audio = any(s["codec_type"] == "audio" for s in streams)
            if audio and not photo and not c.mute:
                speed = c.speed
                tempos = []
                while speed > 2:
                    tempos.append("atempo=2")
                    speed /= 2
                while speed < 0.5:
                    tempos.append("atempo=0.5")
                    speed /= 0.5
                # atempo=1 still runs WSOLA and jitters timing by a few ms.
                if speed != 1:
                    tempos.append(f"atempo={speed}")
                filters.append(
                    f"[{idx}:a]atrim=start={round(c.start - seek, 6)}:end={round(c.end - seek, 6)},asetpts=PTS-STARTPTS,{''.join(t + ',' for t in tempos)}aresample=48000,aformat=channel_layouts=stereo,apad,atrim=duration={item['duration']}[a{i}]"
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
                    f"[{v}][v{i}]xfade=transition=fade:duration={item['overlap']}:offset={max(0, item['start'] - 0.0005):.6f}[vx{i}]"
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
            if track.gain_points:
                f += f",volume=volume='{gain_expr(track.gain_points)}':eval=frame"
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
        level = "" if p.export.auto_level else ":level=0"
        filters.append(f"[mix]alimiter=limit={p.export.limit}{level}[aout]")
        if p.on_video_text:
            insets = [
                o
                for o in p.timeline_overlays
                if o.kind == "inset" and o.start < total and o.inset
            ]
            for n, o in enumerate(insets):
                v, idx = inset_graph(
                    o, n, idx, v, width, height, fps, total, tmp, cmd, filters
                )
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
