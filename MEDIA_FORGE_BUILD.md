# MediaForge — build info

Written for another AI assistant advising on Instagram Reel optimisation.
Machine: Ubuntu, RTX 3080 (10 GB), ffmpeg 7.x with NVENC. As of 2026-09-17.

MediaForge is a local-first FastAPI app (`~/mediaforge`, port 8420) that indexes a
media library, plans edits with an LLM, and renders with ffmpeg. The short-form
Reel work described below is built with **direct ffmpeg scripts**, not the app's
editor, because the app's renderer targets library footage rather than one-clip
hooks.

---

## 1. Current pipeline

**Ingest.** Drone files are read in place from the SD card
(`/media/bfam/5C2B-86B2/DCIM/100MEDIA/`, 304 clips, 82 GB). They are *not*
imported into the library — the main disk is at 94 %. Phone media is a separate
indexed library (1,913 assets, SQLite at `data/mediaforge.db`).

**Orientation.** 178 of the 304 drone clips are 3840×2160 with
`rotation: -90` side data, so they present as 2160×3840 vertical. Resolution
alone is misleading; orientation is read from
`ffprobe -show_entries stream_side_data=rotation`.

**Segment selection — this is currently naive.** One frame is sampled at
`duration/3`, captioned by a vision model, and the clip is cut from that point.
There is no motion analysis. Choosing between clips is done on model scores, not
on movement inside a clip:

```
frame at duration/3  →  qwen/qwen3-vl-32b-instruct (OpenRouter)
                     →  {caption, subject, hook: 1-10, quality: 1-10}
                     →  rank by hook*2 + quality, group by subject
```

Cost measured at ~$0.00008 per frame, ~3 s per call, 8 parallel workers.

**Text rendering.** ASS subtitles burned in via libass
(`-vf subtitles=file.ass`), not drawtext. ASS is used because it supports
animated transforms, which drawtext cannot do.

**Timeline logic.** Text cards are a Python list of
`(start, end, text, style)` tuples written straight to ASS `Dialogue` lines.
**There is no overlap validation** — see §6.

---

## 2. Text rendering code

ASS header actually in use (1080×1920 canvas):

```
[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Big,DejaVu Sans,96,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,2,0,1,7,4,5,70,70,0,1
Style: Gold,DejaVu Sans,96,&H0017A3E8,&H0017A3E8,&H00000000,&H00000000,-1,0,0,0,100,100,2,0,1,7,4,5,70,70,0,1
```

- Colours are ASS `&HAABBGGRR` — byte-reversed from hex RGB.
  `&H0017A3E8` = `#E8A317` (tungsten gold). `#FFD60A` would be `&H000AD6FF`.
- `Outline: 7` with `BorderStyle: 1` is the black stroke (≈7 px at this size).
  `Shadow: 4`.
- `Alignment: 5` = middle-centre; position forced per line with `\pos(540,960)`.
- Font is DejaVu Sans Bold (`/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf`).
  No brand font is installed.

Kinetic pop, per Dialogue line:

```
{\an5\pos(540,960)\fad(90,120)\fscx86\fscy86\t(0,140,\fscx104\fscy104)\t(140,260,\fscx100\fscy100)}TEXT
```

Overshoots to 104 % then settles to 100 % over 260 ms, with a 90 ms fade in.

Word-synced captions (used on longer narrated pieces) use ASS karaoke `\k`
timings, where `PrimaryColour` is the spoken colour and `SecondaryColour` the
not-yet-spoken colour, so words turn gold on the syllable.

---

## 3. Voiceover / audio

- **TTS:** ElevenLabs, `eleven_multilingual_v2`, endpoint
  `/v1/text-to-speech/{voice_id}/with-timestamps`. The user's own cloned voice.
  Settings: `stability 0.35–0.45, similarity_boost 0.8–0.85, style 0.3–0.55`.
- The `with-timestamps` variant returns per-character start/end times, which are
  folded into word timings — that is how captions land on the syllable without
  any transcription step.
- **The API key is scoped.** Text-to-speech works; `/v1/voices`, `/v1/models`,
  `/v1/user` and `/v1/sound-generation` all return 401. Voice ID is therefore
  hard-coded, and **sound effects cannot be generated** on this key.
- **Whooshes are synthesised locally** with ffmpeg:
  `anoisesrc=c=pink` → `highpass=300, lowpass=6000`, amplitude enveloped by
  `volume='0.9*sin(3.14159*t/0.45)':eval=frame`, plus `aecho`. 0.45 s.
- **Mastering:** `highpass=90, acompressor(threshold -18 dB, ratio 3),
  loudnorm I=-14 TP=-1.5 LRA=11` — the −14 LUFS target platforms expect.
- **No music.** Original audio only, deliberately, for a business account.

---

## 4. Export settings

- 1080×1920, 30 fps, H.264 `yuv420p`, `-movflags +faststart`.
- NVENC (`h264_nvenc -preset p5 -cq 24`) with **an automatic libx264 fallback**:
  NVENC intermittently returns `No capable devices found` when another process
  holds the encoder, so every encode retries on CPU.
- Delivery copies are capped at 5.5 Mbps so a 60 s vertical lands under
  Telegram's 50 MB bot limit (~39 MB). Masters stay at CRF 18.
- Loop tail: `tpad=stop_mode=add:stop_duration=0.2:color=black`.
- Duration accounting: the library renderer crossfades 0.3 s per join, so raw
  clip time must be `target + 0.3 × (clips − 1)` or the result lands short.

---

## 5. Agent logic

Two models, both via OpenRouter — **no Hermes/Ollama in this path**. Local
gemma4:e4b exists only as an offline fallback.

- **Edit planning:** `deepseek/deepseek-v4.1-flash`, `response_format:
  json_object`, `reasoning: {enabled: false}`, temperature 0.2. Reasoning off is
  deliberate: it made plans 13× slower and 4.5× pricier for no quality gain
  (5.3 s vs 71 s, $0.0007 vs $0.0034 per plan).
  The prompt hands the model a JSON list of candidate scenes
  (`asset_id, scene_id, start, end, score, caption`) and demands clips only from
  that list, 1–12 s each, summing near the target duration. Returned clips are
  validated against the allowed ids and scene bounds before use.
- **Frame understanding:** `qwen/qwen3-vl-32b-instruct` (see §1). Chosen over
  deepseek-vision, gemini-3.1-flash-lite, gemini-3.8-flash, glm-5.3-flash and
  claude-haiku-4.5 on a same-frames benchmark: it reads on-screen text and names
  concrete objects at roughly a tenth of Gemini 3.8's price.

---

## 6. Known gaps (what to fix)

1. **No overlap validation.** Text cards are written to ASS unchecked. The
   observed double-text came from *word-synced caption lines* on a longer piece,
   where line N ended 0.15 s after line N+1 began; that specific case is now
   clamped (`end = min(end + 0.15, next_start − 0.02)`), but there is still no
   general validator for the card timeline.
2. **No motion-based trimming.** Segment choice is a single frame at
   `duration/3`. Nothing detects push-ins, orbits, takeoff or landing.
3. **No named presets.** Colours, timings and copy are inline constants per
   script, not a reusable preset object.
4. **No timeline.json.** The timeline exists only as Python tuples, so nothing
   else can inspect, diff or validate it.
5. **Speed ramping is not implemented.** Playback speed is constant; the
   "ramp" in existing cuts is a zoom push, not a `setpts` change.
