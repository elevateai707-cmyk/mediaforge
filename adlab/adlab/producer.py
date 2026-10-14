"""Phase 5, Producer: a compliant brief -> finished ads at 9:16 / 4:5 / 1:1.

Per brief, in order:
1. Compliance pre-check (compliance.check_brief). REJECT never renders; FIX renders
   only with allow_fix (the ad goes to review flagged FIX).
2. Visuals per shot and ratio:
   * generated_image / generated_video -> YOUR ComfyUI workflow
     (config/comfyui/image.json / video.json, see comfy.py), one GPU job at a time
     under the file lock, VRAM logged, Media Forge asked to unload first. Without
     ComfyUI: a brand-coloured text card, and the manifest says so.
   * text_card -> brand card (PIL). brand_asset -> your logo card.
   * own_footage -> the next clip/photo in sites/<site>/footage/ (yours only).
3. Voiceover: one ElevenLabs call per shot with a vo_line (shared by every ratio);
   a shot lasts max(planned seconds, its VO + 0.25 s). No key -> silent ad, noted.
4. FFmpeg: each shot -> segment (cover-crop, slow push-in on stills, burned-in
   caption in the platforms' safe zone, VO), then concat + branded end card, H.264/AAC,
   faststart, MP4 metadata flagging it AI-generated.
5. Compliance post-check on the file (adds visual similarity vs the source ads).
6. data/batches/<batch>/manifest.json: brief, pattern, source ads it learned from,
   facts used, AI flag + disclosure, fallbacks, cost. Creatives land in the review
   queue (status "pending"); nothing is uploaded anywhere.

Dry run: everything up to step 2's payloads, zero API calls, zero GPU, zero renders.
Never reused from a source ad: footage, images, script lines, music (there is no
music track), logos, names, faces or voices.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Optional

import httpx
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel

from . import comfy as comfy_mod
from . import compliance, usage
from .config import settings
from .mediaforge import RATIO_SIZES, MediaForge, MediaForgeError
from .schemas import Brief, CostLine, Creative, Pattern, Shot
from .sites import Site, load_site
from .store import LocalStore

COMMAND = "produce"
FPS = 30
END_CARD_S = 2.5
GEN_SIZES = {"9:16": (768, 1344), "4:5": (896, 1120), "1:1": (1024, 1024), "16:9": (1344, 768)}
NEGATIVE = ("people, person, face, faces, crowd, identifiable person, celebrity, text, words, letters, "
            "caption, watermark, logo, brand name, trademark, signage")
ELEVEN_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm"}
AI_NOTE = "AI-generated with AdLab. Apply the platform's AI-content label when uploading."

Backend = Literal["comfyui_image", "comfyui_video", "text_card", "logo_card", "own_footage"]


class ProduceError(RuntimeError):
    pass


# --- settings -----------------------------------------------------------------------------
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def x264_preset() -> str:
    return os.environ.get("ADLAB_X264_PRESET", "medium")


def eleven_key() -> str:
    return os.environ.get("ELEVENLABS_API_KEY", "")


def eleven_model() -> str:
    return os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2")


def voice_for(site: Site) -> str:
    return site.brand.voice_id or os.environ.get("ELEVENLABS_VOICE_ID", "")


# --- plan (shared by dry run and real run) ------------------------------------------------
class ShotPlan(BaseModel):
    index: int
    seconds: float
    asset: str
    backend: Backend
    visual: str
    on_screen_text: str = ""
    vo_line: str = ""
    note: str = ""


class BriefPlan(BaseModel):
    brief_id: str
    site: str
    verdict: str
    reasons: list[str] = []
    will_render: bool
    ratios: list[str]
    shots: list[ShotPlan] = []
    vo_script: list[str] = []
    tagged_facts: list[dict] = []
    missing_facts: list[str] = []
    mediaforge_calls: list[dict] = []
    comfyui_payloads: list[dict] = []
    elevenlabs_payloads: list[dict] = []
    estimated_cost_usd: Optional[float] = 0.0
    notes: list[str] = []


def seed_for(brief_id: str, index: int) -> int:
    return int(hashlib.sha1(f"{brief_id}:{index}".encode()).hexdigest()[:8], 16)


def gen_prompt(shot: Shot, site: Site) -> str:
    colours = ", ".join(c for c in (site.brand.colors.primary, site.brand.colors.secondary) if c)
    return (f"{shot.visual}. Clean commercial photograph, natural light, uncluttered background"
            + (f", accent colours {colours}" if colours else "") + ".")


def footage(site: Site) -> list[Path]:
    d = site.dir / "footage"
    return sorted(p for p in d.iterdir() if p.suffix.lower() in IMAGE_EXT | VIDEO_EXT) if d.is_dir() else []


def choose_backend(shot: Shot, comfy_ready: dict[str, bool], own: list[Path]) -> tuple[Backend, str]:
    if shot.asset == "generated_video":
        if comfy_ready["video"]:
            return "comfyui_video", ""
        if comfy_ready["image"]:
            return "comfyui_image", "no video workflow: still image with a slow push-in"
        return "text_card", "ComfyUI not set up: brand text card placeholder"
    if shot.asset == "generated_image":
        if comfy_ready["image"]:
            return "comfyui_image", ""
        return "text_card", "ComfyUI not set up: brand text card placeholder"
    if shot.asset == "brand_asset":
        return "logo_card", ""
    if shot.asset == "own_footage":
        if own:
            return "own_footage", ""
        return "text_card", "no files in sites/<site>/footage/: text card placeholder"
    return "text_card", ""


def comfy_values(b: Brief, shot: Shot, ratio: str, site: Site, seconds: float) -> dict[str, Any]:
    w, h = GEN_SIZES[ratio]
    fps = _env_int("ADLAB_GEN_FPS", 16)
    return {"prompt": gen_prompt(shot, site), "negative": NEGATIVE, "width": w, "height": h,
            "seed": seed_for(b.id, shot.index), "seconds": seconds, "fps": fps,
            "frames": max(1, round(seconds * fps)) + 1,
            "filename_prefix": f"adlab_{b.id}_s{shot.index}_{ratio.replace(':', 'x')}"}


def eleven_request(text: str, voice_id: str) -> dict:
    return {"method": "POST", "url": ELEVEN_URL.format(voice_id=voice_id or "<ELEVENLABS_VOICE_ID>"),
            "headers": {"xi-api-key": "***" if eleven_key() else "<ELEVENLABS_API_KEY>",
                        "accept": "audio/mpeg"},
            "params": {"output_format": "mp3_44100_128"},
            "json": {"text": text, "model_id": eleven_model()}}


def plan_brief(b: Brief, site: Site, store: LocalStore, ratios: Optional[list[str]], allow_fix: bool,
               comfy: comfy_mod.ComfyUI, save_check: bool) -> BriefPlan:
    report = compliance.check_brief(b, store, site, save=save_check)
    reasons = [r for c in report.checks for r in c.reasons]
    will = report.verdict == "PASS" or (report.verdict == "FIX" and allow_fix)
    want = [r for r in (ratios or b.aspect_ratios) if r in RATIO_SIZES]
    flows = {k: comfy.configured and comfy_mod.load_workflow(k) is not None for k in ("image", "video")}
    own = footage(site)
    plan = BriefPlan(brief_id=b.id, site=b.site, verdict=report.verdict, reasons=reasons, will_render=will,
                     ratios=want, missing_facts=b.missing_facts,
                     tagged_facts=[{"text": c.text, "fact_key": c.fact_key,
                                    "statement": site.facts.facts[c.fact_key].statement}
                                   for c in b.claims if c.fact_key in site.facts.facts])
    if not will:
        plan.notes.append("REJECT: not rendered" if report.verdict == "REJECT"
                          else "FIX: not rendered (rewrite it, or pass --allow-fix to render for review)")
    if not comfy.configured:
        plan.notes.append("COMFYUI_URL not set: generated shots become brand text cards")
    elif not all(flows.values()):
        missing = [k for k, ok in flows.items() if not ok]
        plan.notes.append(f"missing ComfyUI workflow(s): {', '.join(f'config/comfyui/{m}.json' for m in missing)}")
    voice = voice_for(site)
    if not eleven_key() or not voice:
        plan.notes.append("no VO: set ELEVENLABS_API_KEY and a voice (brand.yaml voice_id or ELEVENLABS_VOICE_ID)")
    chars = 0
    for s in b.shots:
        backend, note = choose_backend(s, flows, own)
        plan.shots.append(ShotPlan(index=s.index, seconds=s.seconds, asset=s.asset, backend=backend, visual=s.visual,
                                   on_screen_text=s.on_screen_text, vo_line=s.vo_line, note=note))
        if s.vo_line:
            plan.vo_script.append(s.vo_line)
            plan.elevenlabs_payloads.append({"shot": s.index, **eleven_request(s.vo_line, voice)})
            chars += len(s.vo_line)
        if backend.startswith("comfyui"):
            wf_name = "video" if backend == "comfyui_video" else "image"
            for ratio in want:
                wf = comfy_mod.fill(comfy_mod.load_workflow(wf_name) or {}, comfy_values(b, s, ratio, site, s.seconds))
                plan.comfyui_payloads.append({"shot": s.index, "ratio": ratio, "workflow": wf_name,
                                              "method": "POST", "url": f"{comfy.url}/prompt",
                                              "json": comfy.payload(wf)})
    if plan.comfyui_payloads:
        plan.mediaforge_calls.append({"method": "POST", "url": f"{settings().mediaforge_url}/api/gpu/release",
                                      "when": "once, before the first ComfyUI job"})
    price = usage.service_price("elevenlabs_per_1k_chars")
    plan.estimated_cost_usd = round(chars / 1000 * price, 4) if price is not None else (0.0 if not chars else None)
    return plan


# --- small helpers -------------------------------------------------------------------------
def retry(fn: Callable[[], Any], what: str, attempts: int = 3, base_s: float = 2.0, log: Optional[Callable] = None):
    for i in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - retried, then re-raised
            if log:
                log({"event": "retry", "what": what, "attempt": i, "error": f"{type(exc).__name__}: {exc}"})
            if i == attempts:
                raise
            time.sleep(base_s * 2 ** (i - 1))


def _hex(c: str, default: tuple[int, int, int]) -> tuple[int, int, int]:
    if c and len(c) == 7:
        return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]
    return default


def font(site: Site, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    cands = sorted((site.dir / "fonts").glob("*.[ot]tf")) if (site.dir / "fonts").is_dir() else []
    if site.brand.fonts and shutil.which("fc-match"):
        try:
            out = subprocess.run(["fc-match", "-f", "%{file}", f"{site.brand.fonts[0]}:bold"],
                                 capture_output=True, text=True, timeout=10).stdout.strip()
            if out:
                cands.append(Path(out))
        except (subprocess.SubprocessError, OSError):
            pass
    cands += [Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
              Path("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")]
    for p in cands:
        if p.is_file():
            try:
                return ImageFont.truetype(str(p), size)
            except OSError:
                continue
    return ImageFont.load_default(size)


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt, max_w: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=fnt) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + ([cur] if cur else [])


def _text_block(im: Image.Image, text: str, site: Site, size: int, centre_y: float, fill, box=None) -> None:
    w, h = im.size
    d = ImageDraw.Draw(im)
    fnt = font(site, size)
    lines = wrap(d, text, fnt, int(w * 0.78))
    lh = int(size * 1.25)
    top = int(h * centre_y - lh * len(lines) / 2)
    if box is not None:
        widest = max(d.textlength(l, font=fnt) for l in lines)
        pad = int(size * 0.45)
        d.rounded_rectangle((w / 2 - widest / 2 - pad, top - pad, w / 2 + widest / 2 + pad,
                             top + lh * len(lines) + pad * 0.6), radius=pad, fill=box)
    for i, line in enumerate(lines):
        d.text((w / 2, top + i * lh), line, font=fnt, fill=fill, anchor="ma")


def caption_y(ratio: str) -> float:
    # TikTok/Reels UI covers the bottom ~25% and right edge of 9:16; keep text above it.
    return 0.62 if ratio == "9:16" else 0.74


def caption_png(text: str, ratio: str, site: Site, out: Path) -> Path:
    w, h = RATIO_SIZES[ratio]
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    _text_block(im, text, site, round(min(w, h) * 0.058), caption_y(ratio), (255, 255, 255, 255),
                box=(0, 0, 0, 165))
    im.save(out)
    return out


def card_png(lines: list[str], ratio: str, site: Site, out: Path, logo: Optional[Path] = None) -> Path:
    w, h = RATIO_SIZES[ratio]
    bg = _hex(site.brand.colors.primary or site.brand.colors.background, (17, 17, 17))
    fg = _hex(site.brand.colors.text, (255, 255, 255))
    im = Image.new("RGB", (w, h), bg)
    y = 0.42
    if logo:
        lg = Image.open(logo).convert("RGBA")
        lg.thumbnail((int(w * 0.45), int(h * 0.18)))
        im.paste(lg, ((w - lg.width) // 2, int(h * 0.30) - lg.height // 2), lg)
        y = 0.52
    base = round(min(w, h) * 0.075)
    for i, text in enumerate(t for t in lines if t):
        _text_block(im, text, site, base if i == 0 else round(base * 0.6), y + i * 0.13, fg)
    im.save(out)
    return out


def _run(argv: list[str], what: str, timeout: float = 600) -> None:
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise ProduceError(f"{what} failed: {(proc.stderr or proc.stdout).strip()[-400:]}")


def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise ProduceError("ffmpeg not found (sudo apt install ffmpeg)")
    return exe


def probe(path: Path) -> dict:
    exe = shutil.which("ffprobe")
    if not exe:
        raise ProduceError("ffprobe not found")
    out = subprocess.run([exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise ProduceError(f"ffprobe {path.name}: {out.stderr.strip()[-200:]}")
    return json.loads(out.stdout)


def duration(path: Path) -> float:
    return float(probe(path)["format"].get("duration") or 0)


def segment(visual: Path, out: Path, ratio: str, seconds: float, caption: Optional[Path], audio: Optional[Path],
            motion: bool) -> Path:
    """One shot -> H.264/AAC segment at the ratio's size (cover-crop, caption overlay, VO padded)."""
    w, h = RATIO_SIZES[ratio]
    still = visual.suffix.lower() in IMAGE_EXT
    frames = max(1, round(seconds * FPS))
    argv = [ffmpeg(), "-y", "-v", "error", "-nostdin"]
    argv += (["-loop", "1", "-framerate", str(FPS), "-t", f"{seconds:.3f}", "-i", str(visual)] if still and not motion
             else ["-i", str(visual)] if still
             else ["-stream_loop", "-1", "-t", f"{seconds:.3f}", "-i", str(visual)])
    inputs = 1
    if caption:
        argv += ["-loop", "1", "-framerate", str(FPS), "-t", f"{seconds:.3f}", "-i", str(caption)]
        inputs += 1
    if audio:
        argv += ["-i", str(audio)]
    else:
        argv += ["-f", "lavfi", "-t", f"{seconds:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    a_idx = inputs
    cover = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"
    if still and motion:
        v = (f"[0:v]scale={int(w * 1.5)}:{int(h * 1.5)}:force_original_aspect_ratio=increase,"
             f"crop={int(w * 1.5)}:{int(h * 1.5)},zoompan=z='min(zoom+0.0007,1.08)':d={frames}"
             f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={FPS},setsar=1[bg]")
    else:
        v = f"[0:v]{cover},fps={FPS}[bg]"
    v += ";[bg][1:v]overlay=0:0:shortest=1[cv]" if caption else ";[bg]null[cv]"
    v += f";[cv]format=yuv420p,trim=duration={seconds:.3f},setpts=PTS-STARTPTS[v]"
    a = (f";[{a_idx}:a]aresample=48000,aformat=channel_layouts=stereo,apad,"
         f"atrim=duration={seconds:.3f},asetpts=PTS-STARTPTS[a]")
    argv += ["-filter_complex", v + a, "-map", "[v]", "-map", "[a]", "-r", str(FPS),
             "-c:v", "libx264", "-preset", x264_preset(), "-crf", "20", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-t", f"{seconds:.3f}", str(out)]
    _run(argv, f"segment {out.name}")
    return out


def concat(segments: list[Path], out: Path, metadata: dict[str, str]) -> Path:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in segments), encoding="utf-8")
    argv = [ffmpeg(), "-y", "-v", "error", "-nostdin", "-f", "concat", "-safe", "0", "-i", str(lst),
            "-c", "copy", "-movflags", "+faststart"]
    for k, v in metadata.items():
        argv += ["-metadata", f"{k}={v}"]
    _run(argv + [str(out)], f"concat {out.name}")
    lst.unlink(missing_ok=True)
    return out


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --- one brief, for real ---------------------------------------------------------------------
class Producer:
    def __init__(self, store: LocalStore, batch_dir: Path, comfy: Optional[comfy_mod.ComfyUI] = None,
                 http: Optional[httpx.Client] = None, mediaforge: Optional[MediaForge] = None,
                 motion: bool = True):
        self.store = store
        self.dir = batch_dir
        self.comfy = comfy or comfy_mod.ComfyUI()
        self.http = http or httpx.Client(timeout=120)
        self.mf = mediaforge
        self.motion = motion
        self._released = False
        self.log_path = batch_dir / "log.jsonl"

    def log(self, rec: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        rec = {"at": datetime.now(timezone.utc).isoformat(), **rec}
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")

    def _release_neighbours(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            (self.mf or MediaForge()).release_gpu()
            self.log({"event": "mediaforge_release", "ok": True})
        except MediaForgeError as exc:
            self.log({"event": "mediaforge_release", "ok": False, "error": str(exc)})

    def generate(self, b: Brief, shot: Shot, ratio: str, site: Site, backend: Backend, seconds: float,
                 dest: Path) -> Path:
        from .gpu import gpu_job

        self._release_neighbours()
        name = "video" if backend == "comfyui_video" else "image"
        wf = comfy_mod.fill(comfy_mod.load_workflow(name) or {}, comfy_values(b, shot, ratio, site, seconds))

        def once() -> Path:
            with gpu_job(f"comfyui:{name}:{b.id}:s{shot.index}:{ratio}",
                         min_free_mb=_env_int("ADLAB_COMFY_MIN_FREE_MB", 0)) as rec:
                files = self.comfy.run(wf, dest, timeout_s=_env_int("ADLAB_COMFY_TIMEOUT_S", 900))
                rec["outputs"] = [f.name for f in files]
            ext = VIDEO_EXT | {".gif", ".webp"} if name == "video" else IMAGE_EXT
            pick = next((f for f in files if f.suffix.lower() in ext), files[0])
            return pick

        return retry(once, f"comfyui {b.id} shot {shot.index} {ratio}", log=self.log)

    def voiceover(self, line: str, voice: str, out: Path) -> Path:
        if out.is_file():
            return out
        req = eleven_request(line, voice)
        headers = dict(req["headers"], **{"xi-api-key": eleven_key()})

        def once() -> Path:
            r = self.http.post(req["url"], params=req["params"], json=req["json"], headers=headers)
            if r.status_code >= 400:
                raise ProduceError(f"ElevenLabs {r.status_code}: {r.text[:200]}")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(r.content)
            return out

        retry(once, f"elevenlabs {out.name}", log=self.log)
        usage.record_service(COMMAND, "elevenlabs", eleven_model(), len(line) / 1000,
                             usage.service_price("elevenlabs_per_1k_chars"))
        return out

    def render(self, b: Brief, plan: BriefPlan, site: Site) -> list[tuple[Creative, dict]]:
        work = self.dir / b.id
        (work / "assets").mkdir(parents=True, exist_ok=True)
        voice = voice_for(site)
        costs: list[CostLine] = []
        fallbacks = [f"shot {s.index}: {s.note}" for s in plan.shots if s.note]
        # VO once per shot (shared by every ratio); a shot is at least as long as its line
        vo: dict[int, Path] = {}
        if eleven_key() and voice:
            for s in b.shots:
                if s.vo_line:
                    vo[s.index] = self.voiceover(s.vo_line, voice, work / "vo" / f"shot_{s.index}.mp3")
            chars = sum(len(s.vo_line) for s in b.shots if s.index in vo)
            price = usage.service_price("elevenlabs_per_1k_chars")
            costs.append(CostLine(provider="elevenlabs", usd=round(chars / 1000 * (price or 0), 4),
                                  item=f"{chars} characters" + ("" if price is not None else
                                                                " (unpriced: set services.elevenlabs_per_1k_chars)")))
        lengths = {s.index: max(s.seconds, (duration(vo[s.index]) + 0.25) if s.index in vo else 0) for s in b.shots}
        own = footage(site)
        logo = site.brand.logo(site.dir)
        out: list[tuple[Creative, dict]] = []
        for ratio in plan.ratios:
            segs: list[Path] = []
            for sp, s in zip(plan.shots, b.shots):
                stem = work / "assets" / f"s{s.index}_{ratio.replace(':', 'x')}"
                caption = s.on_screen_text
                if sp.backend.startswith("comfyui"):
                    visual = self.generate(b, s, ratio, site, sp.backend, lengths[s.index], work / "assets" / "gen")
                elif sp.backend == "own_footage":
                    visual = own[(s.index - 1) % len(own)]
                elif sp.backend == "logo_card":
                    visual = card_png([s.on_screen_text], ratio, site, stem.with_suffix(".png"), logo)
                    caption = ""
                else:
                    visual = card_png([s.on_screen_text], ratio, site, stem.with_suffix(".png"))
                    caption = ""
                cap = caption_png(caption, ratio, site, stem.with_name(stem.name + "_cap.png")) if caption else None
                segs.append(segment(visual, stem.with_suffix(".mp4"), ratio, lengths[s.index], cap, vo.get(s.index),
                                    motion=self.motion and sp.backend == "comfyui_image"))
            end = card_png([site.brand.end_card_text or site.info.domain, b.cta], ratio, site,
                           work / "assets" / f"end_{ratio.replace(':', 'x')}.png", logo)
            segs.append(segment(end, work / "assets" / f"end_{ratio.replace(':', 'x')}.mp4", ratio, END_CARD_S,
                                None, None, motion=False))
            final = work / f"{b.id}_{ratio.replace(':', 'x')}.mp4"
            cid = "cr_" + hashlib.sha1(f"{b.id}|{ratio}".encode()).hexdigest()[:12]
            concat(segs, final, {"comment": f"{AI_NOTE} creative={cid}", "description": AI_NOTE,
                                 "title": f"AdLab {cid}"})
            info = probe(final)
            v = next(st for st in info["streams"] if st["codec_type"] == "video")
            if (v["width"], v["height"]) != RATIO_SIZES[ratio]:
                raise ProduceError(f"{final.name}: got {v['width']}x{v['height']}, wanted {RATIO_SIZES[ratio]}")
            creative = Creative(id=cid, brief_id=b.id, aspect_ratio=ratio, path=str(final), kind="video",  # type: ignore[arg-type]
                                sha256=sha256(final), facts_used=sorted({c.fact_key for c in b.claims}),
                                source_ad_ids=b.source_ad_ids,
                                cost=costs if not out else [])  # VO cost counted once per brief
            out.append((creative, {"seconds": round(float(info["format"]["duration"]), 2),
                                   "size": f"{v['width']}x{v['height']}", "fallbacks": fallbacks,
                                   "voiceover": bool(vo)}))
        return out


# --- batch -----------------------------------------------------------------------------------
class ProduceResult(BaseModel):
    batch_id: Optional[str] = None
    batch_dir: Optional[str] = None
    plans: list[BriefPlan] = []
    creatives: list[dict] = []
    skipped: list[str] = []
    errors: list[str] = []
    manifest: Optional[str] = None


def pick_briefs(store: LocalStore, site: Optional[str], brief_ids: Optional[list[str]], limit: int) -> list[Brief]:
    if brief_ids:
        found = [store.fetch("brief", i, Brief) for i in brief_ids]
        missing = [i for i, b in zip(brief_ids, found) if b is None]
        if missing:
            raise ProduceError(f"unknown brief id(s): {', '.join(missing)}")
        return [b for b in found if b]
    return store.list("brief", Brief, site=site, status="draft")[:limit]


def produce(site: Optional[str] = None, brief_ids: Optional[list[str]] = None, ratios: Optional[list[str]] = None,
            dry_run: bool = False, allow_fix: bool = False, limit: int = 5, store: Optional[LocalStore] = None,
            comfy: Optional[comfy_mod.ComfyUI] = None, http: Optional[httpx.Client] = None,
            mediaforge: Optional[MediaForge] = None, motion: bool = True) -> ProduceResult:
    store = store or LocalStore()
    comfy = comfy or comfy_mod.ComfyUI()
    briefs = pick_briefs(store, site, brief_ids, limit)
    res = ProduceResult()
    if not briefs:
        res.skipped.append("no draft briefs: run `adlab brief --site <site>` first")
        return res
    sites: dict[str, Site] = {}
    for b in briefs:
        s = sites.setdefault(b.site, load_site(b.site))
        res.plans.append(plan_brief(b, s, store, ratios, allow_fix, comfy, save_check=not dry_run))
    if dry_run:
        return res

    batch_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + hashlib.sha1(
        "|".join(b.id for b in briefs).encode()).hexdigest()[:6]
    batch_dir = settings().batches_dir / batch_id
    prod = Producer(store, batch_dir, comfy=comfy, http=http, mediaforge=mediaforge, motion=motion)
    res.batch_id, res.batch_dir = batch_id, str(batch_dir)
    items = []
    with usage.track(COMMAND, echo=lambda *_: None):
        for b, plan in zip(briefs, res.plans):
            if not plan.will_render:
                res.skipped.append(f"{b.id}: compliance {plan.verdict}")
                store.set_status("brief", b.id, "rejected" if plan.verdict == "REJECT" else "needs_fix")
                continue
            site_obj = sites[b.site]
            prod.log({"event": "brief_start", "brief": b.id, "ratios": plan.ratios})
            try:
                made = prod.render(b, plan, site_obj)
            except Exception as exc:  # noqa: BLE001 - one bad brief never sinks the batch
                prod.log({"event": "brief_failed", "brief": b.id, "error": f"{type(exc).__name__}: {exc}"})
                res.errors.append(f"{b.id}: {type(exc).__name__}: {exc}")
                continue
            pattern = store.fetch("pattern", b.pattern_id, Pattern)
            sources = [a for i in b.source_ad_ids if (a := store.get(i))]
            for creative, meta in made:
                report = compliance.check_creative(creative, b, store)
                status = "rejected" if report.verdict == "REJECT" else "pending"
                store.put("creative", creative, id=creative.id, site=b.site, ref=b.id, status=status)
                entry = {
                    "creative": json.loads(creative.model_dump_json()), **meta,
                    "compliance": {"verdict": report.verdict,
                                   "reasons": [r for c in report.checks for r in c.reasons]},
                    "status": status,
                    "brief": json.loads(b.model_dump_json()),
                    "pattern": json.loads(pattern.model_dump_json()) if pattern else None,
                    "learned_from": [{"ad_id": a.id, "advertiser": a.source.advertiser, "platform": a.source.platform,
                                      "url": str(a.source.url) if a.source.url else None} for a in sources],
                    "facts_used": plan.tagged_facts,
                    "ai_generated": True, "ai_disclosure": creative.ai_disclosure,
                    "cost_usd": round(sum(c.usd for c in creative.cost), 4),
                }
                items.append(entry)
                res.creatives.append({"id": creative.id, "brief_id": b.id, "ratio": creative.aspect_ratio,
                                      "path": creative.path, "verdict": report.verdict, "status": status})
            store.set_status("brief", b.id, "produced")
            prod.log({"event": "brief_done", "brief": b.id, "creatives": [c.id for c, _ in made]})
    if prod._released and comfy.configured:
        try:
            comfy.free()   # hand the VRAM back once the batch is done
        except comfy_mod.ComfyError as exc:
            prod.log({"event": "comfy_free", "ok": False, "error": str(exc)})
    manifest = {"batch_id": batch_id, "created_at": datetime.now(timezone.utc).isoformat(),
                "ai_generated": True, "note": AI_NOTE, "items": items, "skipped": res.skipped, "errors": res.errors,
                "cost_usd": round(sum(i["cost_usd"] for i in items), 4)}
    batch_dir.mkdir(parents=True, exist_ok=True)
    (batch_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    res.manifest = str(batch_dir / "manifest.json")
    return res
