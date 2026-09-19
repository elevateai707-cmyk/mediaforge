#!/usr/bin/env python
"""Short-form Reel builder: smart trim, exclusive text timeline, VO, loop tail.

    python scripts/reel_builder.py DJI_0464.MP4 --preset contractorPain
    python scripts/reel_builder.py DJI_0464.MP4 --preview      # timeline only
    python scripts/reel_builder.py DJI_0464.MP4 --no-vo

Output: REEL_<name>_test1.mp4 — 1080x1920, 30 fps, 7.5 s of texted footage,
a clean b-roll tail, and a 0.2 s black frame so the loop cuts hard.

Two text cards can never share a timestamp: build_timeline() shifts any
overlap and validate_timeline() refuses to render if one survives.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path

MEDIA_DIRS = [Path("/media/bfam/5C2B-86B2/DCIM/100MEDIA")]
OUT_DIR = Path("/media/bfam/5C2B-86B2/REELS")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
W, H, FPS = 1080, 1920, 30
TEXT_DUR = 7.5          # texted section
TAIL = 2.5              # clean b-roll after the last card (loop counts better than a black flash)
BLACK = 0.0             # set >0 only if you want a hard cut instead of a clean loop
SPEED = 1.05            # 105% playback
ZOOM_FROM, ZOOM_TO = 1.00, 1.12


# ── presets ──────────────────────────────────────────────────────────────────

@dataclass
class Preset:
    name: str
    primary: str        # hex, the accent colour
    secondary: str      # hex, the plain colour
    stroke_px: int
    cards: list         # (start, end, text, "primary"|"secondary")
    script: str
    audience: str


PRESETS = {
    "contractorPain": Preset(
        name="contractorPain",
        primary="#FFD60A",
        secondary="#FFFFFF",
        stroke_px=8,
        cards=[
            (0.0, 1.5, "CALGARY\\NCONTRACTORS", "primary"),
            (1.5, 3.5, "THIS IS WHY\\NYOU'RE NOT BOOKED", "secondary"),
            (3.5, 7.5, "FIX YOUR WEBSITE\\N\\N→ FIX YOUR CALENDAR", "primary"),
        ],
        script="Calgary contractors. This is why you're not booked. "
               "Fix your website. Fix your calendar.",
        audience="Alberta trades: roofers, plumbers, HVAC, general contractors",
    ),
}


# ── 1. smart trim ────────────────────────────────────────────────────────────

def find_best_segment(video_path: str, want: float = TEXT_DUR + TAIL,
                      step: float = 0.5) -> tuple[float, float]:
    """Most dynamic `want` seconds, skipping takeoff and landing.

    Motion is measured as mean absolute frame difference on a 64x36 greyscale
    decimation of the whole file (one pass, cheap even on a 1 GB 4K clip), then
    summed over a sliding window. The outer 10% at each end is excluded because
    that is where takeoff, landing and gimbal settling live.
    """
    dur = probe_duration(video_path)
    if dur <= want:
        return 0.0, dur
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as fh:
        stats = fh.name
    try:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-i", video_path,
             "-vf", f"fps=4,scale=64:36,format=gray,signalstats,"
                    f"metadata=print:key=lavfi.signalstats.YDIF:file={stats}",
             "-an", "-f", "null", "-"],
            check=True, capture_output=True, timeout=900)
        ydif = [float(line.split("=")[1]) for line in open(stats)
                if "YDIF" in line]
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, OSError):
        ydif = []
    finally:
        os.unlink(stats)
    if len(ydif) < 8:                                   # analysis failed: centre cut
        start = max(0.0, (dur - want) / 2)
        return start, start + want
    rate = 4.0                                          # samples per second
    lo, hi = int(len(ydif) * 0.10), int(len(ydif) * 0.90)
    window = int(want * rate)
    best, best_score = lo, -1.0
    for i in range(lo, max(lo + 1, hi - window)):
        seg = ydif[i:i + window]
        if len(seg) < window:
            break
        # steady movement beats one jump cut: mean minus a spike penalty
        mean = sum(seg) / len(seg)
        spike = max(seg) - mean
        score = mean - 0.25 * spike
        if score > best_score:
            best_score, best = score, i
    start = round(best / rate, 2)
    return start, round(min(start + want, dur), 2)


def probe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


# ── 2. exclusive text timeline ───────────────────────────────────────────────

def build_timeline(preset: Preset) -> list[dict]:
    """Cards as dicts, with any overlap shifted so only one is ever visible."""
    cards = []
    for start, end, text, role in preset.cards:
        cards.append({"start": float(start), "end": float(end),
                      "text": text, "role": role})
    cards.sort(key=lambda c: c["start"])
    for i in range(1, len(cards)):
        prev, cur = cards[i - 1], cards[i]
        if cur["start"] < prev["end"]:                  # auto-shift
            cur["start"] = prev["end"]
        if cur["end"] <= cur["start"]:
            cur["end"] = cur["start"] + 0.5
    # a card must clear the screen a hair before the next appears
    for i in range(len(cards) - 1):
        cards[i]["end"] = min(cards[i]["end"], cards[i + 1]["start"] - 0.02)
    return cards


def validate_timeline(cards: list[dict]) -> list[str]:
    """Overlap and sanity warnings. Empty list means safe to render."""
    warnings = []
    for i in range(len(cards) - 1):
        a, b = cards[i], cards[i + 1]
        if a["end"] > b["start"]:
            warnings.append(
                f"OVERLAP: card {i+1} ends {a['end']:.2f}s but card {i+2} "
                f"starts {b['start']:.2f}s")
    for i, c in enumerate(cards, 1):
        if c["end"] - c["start"] < 0.6:
            warnings.append(f"card {i} is only {c['end']-c['start']:.2f}s — too fast to read")
        if c["end"] > TEXT_DUR + 0.01:
            warnings.append(f"card {i} runs past the {TEXT_DUR}s text window")
    return warnings


# ── 3. ASS rendering ─────────────────────────────────────────────────────────

def ass_colour(hex_rgb: str) -> str:
    h = hex_rgb.lstrip("#")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}".upper()      # ASS is BGR


def write_ass(cards: list[dict], preset: Preset, path: Path) -> Path:
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {W}\nPlayResY: {H}\nWrapStyle: 0\n\n"
        "[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,"
        "OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,"
        "Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"Style: primary,DejaVu Sans,110,{ass_colour(preset.primary)},{ass_colour(preset.primary)},"
        f"&H00000000,&H00000000,-1,0,0,0,100,100,2,0,1,{preset.stroke_px},4,5,70,70,0,1\n"
        f"Style: secondary,DejaVu Sans,110,{ass_colour(preset.secondary)},{ass_colour(preset.secondary)},"
        f"&H00000000,&H00000000,-1,0,0,0,100,100,2,0,1,{preset.stroke_px},4,5,70,70,0,1\n\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n")
    def t(x: float) -> str:
        return f"{int(x//3600)}:{int(x%3600//60):02}:{x%60:05.2f}"
    anim = (r"{\an5\pos(540,960)\fad(90,120)\fscx86\fscy86"
            r"\t(0,140,\fscx104\fscy104)\t(140,260,\fscx100\fscy100)}")
    ev = [f"Dialogue: 0,{t(c['start'])},{t(c['end'])},{c['role']},,0,0,0,,{anim}{c['text']}"
          for c in cards]
    path.write_text(head + "\n".join(ev) + "\n")
    return path


# ── 4. audio ─────────────────────────────────────────────────────────────────

def make_whoosh(path: Path) -> Path:
    if path.exists():
        return path
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=d=0.45:c=pink:a=0.55",
         "-af", "highpass=f=300,lowpass=f=6000,"
                "volume='0.9*sin(3.14159*t/0.45)':eval=frame,"
                "afade=t=in:d=0.05,afade=t=out:st=0.33:d=0.12,aecho=0.8:0.6:40:0.25",
         "-ar", "44100", "-ac", "2", str(path), "-y"], check=True, capture_output=True)
    return path


def make_vo(script: str, out: Path, speed: float = 1.0,
            model: str = "eleven_v3", settings: dict | None = None) -> Path | None:
    """ElevenLabs TTS in the user's cloned voice.

    Deliberately does NOT speed the result up and does NOT add a short slapback.
    Both were making a real voice clone sound synthetic: a 1.2x atempo flattens
    the clone's own pacing and adds phase artifacts, and an echo delay under
    ~25 ms is heard as comb filtering (a metallic ring), not as room.
    A 7.5 s card window fits the script at natural pace, so there is nothing to
    compress. `speed` stays available for scripts that genuinely overrun.
    """
    import base64
    import httpx
    key_file = Path.home() / ".elevenlabs-key"
    voice_file = Path.home() / ".elevenlabs-voice-id"
    if not key_file.exists() or not voice_file.exists():
        print("  no ElevenLabs key/voice id — skipping VO")
        return None
    key, voice = key_file.read_text().strip(), voice_file.read_text().strip()
    try:
        r = httpx.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
            headers={"xi-api-key": key, "Content-Type": "application/json"},
            json={"text": script, "model_id": model,
                  "voice_settings": settings or {
                      "stability": 0.5, "similarity_boost": 0.9,
                      "style": 0.2, "use_speaker_boost": True}},
            timeout=180)
    except Exception as exc:                            # noqa: BLE001
        print(f"  VO request failed: {type(exc).__name__}")
        return None
    if r.status_code != 200:
        print(f"  VO failed: HTTP {r.status_code}")
        return None
    raw = out.with_suffix(".raw.mp3")
    raw.write_bytes(r.content)
    # Rumble filter, gentle levelling, broadcast loudness. No time-stretch and no
    # sub-25 ms echo: see the docstring.
    tempo = f"atempo={speed}," if abs(speed - 1.0) > 0.01 else ""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(raw),
         "-af", f"{tempo}highpass=f=85,"
                "acompressor=threshold=-18dB:ratio=2.5:attack=8:release=140,"
                "loudnorm=I=-14:TP=-1.5:LRA=11",
         "-ar", "44100", "-ac", "2", str(out), "-y"], check=True, capture_output=True)
    raw.unlink(missing_ok=True)
    return out


# ── 5. ffmpeg command builder ────────────────────────────────────────────────

def build_video(src: str, start: float, ass_path: Path, out: Path,
                total: float, speed: float = SPEED) -> None:
    """Crop to 9:16, speed change, slow punch-in, burned-in text, black tail."""
    frames = FPS * total * speed          # zoompan counts pre-speed frames
    vf = ",".join([
        "crop='min(iw,ih*9/16)':'min(ih,iw*16/9)'",
        f"zoompan=z='{ZOOM_FROM}+{ZOOM_TO-ZOOM_FROM}*on/{frames:.0f}':d=1:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS}",
        f"setpts={1/speed:.5f}*PTS",       # after zoompan or it is discarded
        f"fps={FPS}",
        f"subtitles={ass_path}",
    ] + ([f"tpad=stop_mode=add:stop_duration={BLACK}:color=black"] if BLACK > 0 else []))
    for enc in (["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "8M", "-maxrate", "12M"],
                ["-c:v", "libx264", "-preset", "medium", "-b:v", "8M", "-maxrate", "12M", "-bufsize", "16M"]):
        r = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", str(start), "-i", src,
             "-t", str(total + BLACK), "-vf", vf, "-an", *enc,
             "-pix_fmt", "yuv420p", str(out), "-y"], capture_output=True, text=True)
        if r.returncode == 0 and out.exists() and out.stat().st_size > 1000:
            return
        print(f"  {enc[1]} failed: {r.stderr.strip()[-160:]}")
    raise SystemExit("video encode failed")


def mux(video: Path, vo: Path | None, whoosh: Path, cards: list[dict],
        out: Path, total: float) -> None:
    inputs = ["-i", str(video)]
    parts, mixin, idx = [], "", 1
    if vo:
        inputs += ["-i", str(vo)]
        parts.append(f"[{idx}:a]adelay=120|120,volume=1.0[vo]")
        mixin += "[vo]"
        idx += 1
    for n, c in enumerate(cards):
        if n == 0:
            continue                                     # no whoosh on the opening card
        inputs += ["-i", str(whoosh)]
        ms = int(c["start"] * 1000)
        parts.append(f"[{idx}:a]adelay={ms}|{ms},volume=0.45[w{n}]")
        mixin += f"[w{n}]"
        idx += 1
    n_in = mixin.count("[")
    if n_in == 0:
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-c", "copy",
                        str(out), "-y"], check=True)
        return
    parts.append(f"{mixin}amix=inputs={n_in}:normalize=0:duration=longest,"
                 f"apad,atrim=0:{total + BLACK}[a]")
    subprocess.run(
        ["ffmpeg", "-v", "error", *inputs, "-filter_complex", ";".join(parts),
         "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", str(out), "-y"], check=True, capture_output=True)


# ── main ─────────────────────────────────────────────────────────────────────

COPY_MODELS = {"deepseek": ("deepseek/deepseek-v4.1-flash", False),
               "muse": ("meta/muse-spark-1.3", True)}     # muse forces reasoning on


def write_copy(which: str, brief: str, preset: Preset) -> tuple[list[dict], str]:
    """Fresh 3-card timeline from an LLM, then through the same validator."""
    import httpx
    model, reasoning = COPY_MODELS[which]
    key = (Path.home() / ".openrouter-key").read_text().strip()
    prompt = (
        "You write on-screen text for a 7.5-second vertical Instagram Reel.\n"
        f"Clip and audience: {brief}\n"
        "Goal: stop the scroll in the first second, then earn a save.\n"
        "Rules: exactly 3 cards. Card 1 lands at 0.0s and reads in under a second. "
        "Max 5 words per card. No hashtags, no emoji. Card 3 is the call to action. "
        "Never invent an offer, discount, guarantee or freebie that was not given to you. "
        "Also write a voiceover of the same words, under 20 words, properly punctuated.\n"
        'Return JSON only: {"cards":[{"start":0.0,"end":1.5,"text":"...","style":"Gold"},'
        '{"start":1.5,"end":3.5,"text":"...","style":"Big"},'
        '{"start":3.5,"end":7.5,"text":"...","style":"Gold"}],"voiceover":"..."}')
    r = httpx.post("https://openrouter.ai/api/v1/chat/completions",
                   headers={"Authorization": f"Bearer {key}"},
                   json={"model": model, "messages": [{"role": "user", "content": prompt}],
                         "response_format": {"type": "json_object"}, "temperature": 0.7,
                         "reasoning": {"enabled": reasoning}}, timeout=240)
    if r.status_code != 200:
        raise SystemExit(f"{which} copy failed: HTTP {r.status_code} {r.text[:160]}")
    body = r.json()
    data = json.loads(body["choices"][0]["message"]["content"])
    cost = (body.get("usage") or {}).get("cost")
    print(f"  copy by {model} (${cost})")
    for c in data.get("cards", []):
        print(f"    {c.get('start')}-{c.get('end')}  {c.get('text','')}")
    tmp = Path(tempfile.mkdtemp()) / "copy.json"
    tmp.write_text(json.dumps(data))
    return load_timeline(tmp, preset)


def load_timeline(path: Path, preset: Preset) -> tuple[list[dict], str, dict]:
    """Accept a hand-written or LLM-written timeline and put it through the
    same shift-and-validate path a preset goes through.

    Expected: {"cards":[{"start":0.0,"end":1.5,"text":"...","style":"Gold"}],
               "voiceover":"..."} — `style` may be Gold/primary or Big/secondary.
    A literal backslash-N in the text marks a line break.
    """
    data = json.loads(path.read_text())
    raw = data.get("cards") or []
    if not raw:
        raise SystemExit(f"{path}: no cards")
    role_map = {"gold": "primary", "primary": "primary",
                "big": "secondary", "secondary": "secondary", "white": "secondary"}
    cards = []
    for i, c in enumerate(raw, 1):
        try:
            start, end = float(c["start"]), float(c["end"])
            text = str(c["text"])
        except (KeyError, TypeError, ValueError):
            raise SystemExit(f"{path}: card {i} needs start, end and text")
        role = role_map.get(str(c.get("style", "primary")).lower(), "primary")
        cards.append({"start": start, "end": end, "text": text, "role": role})
    # same guarantee as a preset: sorted, shifted, 20 ms gap enforced
    shim = Preset(preset.name, preset.primary, preset.secondary, preset.stroke_px,
                  [(c["start"], c["end"], c["text"], c["role"]) for c in cards],
                  data.get("voiceover") or preset.script, preset.audience)
    return build_timeline(shim), shim.script, data.get("vo") or {}


def resolve(name: str) -> str:
    p = Path(name)
    if p.is_file():
        return str(p)
    for d in MEDIA_DIRS:
        if (d / name).is_file():
            return str(d / name)
    raise SystemExit(f"not found: {name}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("video")
    ap.add_argument("--preset", default="contractorPain", choices=sorted(PRESETS))
    ap.add_argument("--preview", action="store_true", help="print timeline.json, render nothing")
    ap.add_argument("--no-vo", action="store_true")
    ap.add_argument("--suffix", default="test1")
    ap.add_argument("--timeline", metavar="FILE",
                    help="external timeline JSON (cards + voiceover) to use instead of the preset")
    ap.add_argument("--copy", choices=("deepseek", "muse"),
                    help="write fresh hook copy with this model instead of the preset's wording")
    ap.add_argument("--brief", default="",
                    help="what the clip shows / who it targets, for --copy")
    args = ap.parse_args()

    preset = PRESETS[args.preset]
    src = resolve(args.video)
    stem = Path(src).stem
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="reel_"))

    script = preset.script
    vo_cfg: dict = {}
    if args.timeline:
        cards, script, vo_cfg = load_timeline(Path(args.timeline), preset)
    elif args.copy:
        cards, script = write_copy(args.copy, args.brief or preset.audience, preset)
    else:
        cards = build_timeline(preset)
    warnings = validate_timeline(cards)
    total = TEXT_DUR + TAIL

    timeline = {
        "source": src, "preset": preset.name, "audience": preset.audience,
        "output": {"width": W, "height": H, "fps": FPS,
                   "text_seconds": TEXT_DUR, "tail_seconds": TAIL,
                   "black_seconds": BLACK, "speed": SPEED,
                   "zoom": [ZOOM_FROM, ZOOM_TO]},
        "cards": cards, "script": script, "warnings": warnings,
    }

    if args.preview:
        print(json.dumps(timeline, indent=2))
        return 1 if warnings else 0

    src_dur = probe_duration(src)
    speed = SPEED
    if src_dur < total * SPEED:
        speed = max(0.55, round(src_dur / total, 3))     # slow-mo to fill
        if src_dur / speed < total:                      # still short: trim the tail
            total = round(src_dur / speed, 2)
        print(f"  source is only {src_dur:.1f}s — playing at {speed:.2f}x, "
              f"total {total:.1f}s")
    timeline["output"]["speed"] = speed
    timeline["output"]["tail_seconds"] = round(total - TEXT_DUR, 2)

    print(f"{stem}: finding the most dynamic {total:.1f}s …")
    start, end = find_best_segment(src, want=total * speed)
    timeline["segment"] = {"start": start, "end": end}
    print(f"  segment {start:.2f}s–{end:.2f}s of {probe_duration(src):.1f}s")

    if warnings:
        for w in warnings:
            print(f"  ⚠ {w}")
        raise SystemExit("timeline has overlaps — refusing to render")

    ass_path = write_ass(cards, preset, work / "cards.ass")
    whoosh = make_whoosh(work / "whoosh.wav")
    vo = None if args.no_vo else make_vo(
        script, work / "vo.wav",
        speed=float(vo_cfg.get("speed", 1.0)),
        model=str(vo_cfg.get("model", "eleven_v3")),
        settings=vo_cfg.get("settings"))

    silent = work / "silent.mp4"
    build_video(src, start, ass_path, silent, total, speed)
    out = OUT_DIR / f"REEL_{stem}_{args.suffix}.mp4"
    mux(silent, vo, whoosh, cards, out, total)

    (OUT_DIR / f"REEL_{stem}_{args.suffix}.timeline.json").write_text(
        json.dumps(timeline, indent=2))
    dur = probe_duration(str(out))
    print(f"  → {out}  {dur:.2f}s  {out.stat().st_size/2**20:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
