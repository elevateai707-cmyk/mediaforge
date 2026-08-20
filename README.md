# MediaForge

Local-first AI media scanner, tagger, search and editor for Ubuntu 24.04.
Point it at your photo/video folders; it indexes everything with local AI
models (no cloud), lets you search by content, face, and spoken words, then
assembles edit recipes you approve before rendering or pushing to an NLE.

```
┌────────────────────────────────────────────────────────────────────────────┐
│                              BROWSER (any device)                          │
│   Library · Search · Faces · Edit Studio · Touch-up · Settings/Scan        │
└───────────────▲──────────────────────────────────────────┬─────────────────┘
                │  HTTP /api + WebSocket /ws                │ built dist/ (or Vite dev :5173)
┌───────────────┴──────────────────────────────────────────▼─────────────────┐
│                        FRONTEND  (React + Vite, dark neon)                 │
└───────────────────────────────▲────────────────────────────────────────────┘
                                │  Vite dev proxy /api,/ws → :8420
┌───────────────────────────────┴────────────────────────────────────────────┐
│                       BACKEND  (FastAPI, uvicorn :8420)                    │
│   ingest/ (scanner, metadata, thumbs, dedupe)   edits/ (planner, render,   │
│   ai/ (clip, whisper, scenes, faces, aesthetic, captions, ollama_llm)      │
│   pipeline.py resumable worker · jobs.py asyncio queue · ws.py events      │
└───────┬───────────────┬────────────────┬───────────────┬───────────────────┘
        │               │                │               │
┌───────▼──────┐ ┌──────▼───────┐ ┌──────▼──────┐ ┌──────▼──────────────────┐
│ SQLite +     │ │ FFmpeg 6.1   │ │ Ollama      │ │ NLE bridge              │
│ sqlite-vec   │ │ thumbs/      │ │ qwen2.5vl:7b│ │ DaVinci Resolve 21 API  │
│ data/        │ │ proxies/     │ │ (HTTP :11434│ │ FCPXML 1.10 · CMX3600   │
│ mediaforge.db│ │ renders      │ │  own proc)  │ │ EDL · CapCut (exper.)   │
└──────────────┘ └──────────────┘ └─────────────┘ └─────────────────────────┘
   CLIP ViT-B-32 · faster-whisper small.en · insightface buffalo_sc ·
   aesthetic ONNX — CUDA on RTX 3080 10 GB, CPU fallback otherwise
```

## Setup

```bash
cd /home/bfam/mediaforge
./scripts/setup.sh     # idempotent; may prompt for sudo (apt + ollama install)
./scripts/run.sh       # starts backend :8420, serves app; prints localhost + LAN URLs
```

`setup.sh` installs system deps (ffmpeg, exiftool), ollama +
`qwen2.5vl:7b` (~7 GB download — give it a few minutes), creates
`backend/.venv`, installs backend deps (`requirements.txt` or editable
`pyproject.toml` — whichever the backend ships), runs `npm install && npm run
build` in `frontend/`, ensures `data/ exports/ models/`, and writes
`.env.example`. Re-running is always safe: finished steps are skipped with
`✓ already` messages.

If the backend files land after you ran setup (parallel build), just re-run
`./scripts/setup.sh` — it will pick up `requirements.txt`/`pyproject.toml`
then.

## Local AI models

| Model | Purpose | Disk | VRAM (resident) | Notes |
|---|---|---|---|---|
| `qwen2.5vl:7b` (Ollama) | image/video captioning | ~7 GB | managed by Ollama | separate process on :11434; lazy-loaded per request |
| CLIP ViT-B-32 | embedding + semantic search | ~600 MB | ~1.5 GB | CUDA when available, else CPU |
| faster-whisper `small.en` (int8) | speech → transcript | ~500 MB | ~1 GB | per-video audio track |
| insightface `buffalo_sc` | face detection/embedding | ~400 MB | ~400 MB | |
| aesthetic ONNX | 0–10 aesthetic score | ~100 MB | ~100 MB | |

**VRAM budget (RTX 3080 10 GB):** total resident footprint stays under
~4 GB because models are loaded lazily and unloaded between pipeline phases
(whenever free VRAM drops below 1 GB). Ollama runs in its own process and
already keeps `qwen2.5vl:7b` within its own VRAM-safe window — it does not
stack with the torch models. No GPU? Everything falls back to CPU with a
one-time warning on `/api/health` and the WebSocket.

## Usage

1. **Scan** — Settings/Scan: add absolute paths to folders (e.g.
   `MEDIA_DIRS=/home/bfam/Videos,/home/bfam/Pictures` in `.env`), start a
   scan job. Supported: jpg/jpeg/png/heic/heif/mov/mp4/m4v/mkv/avi/webm/
   prores-mov. SHA-256 manifests make re-scans skip known files.

**Golden path:** after a scan, `POST /api/edits/plan` with
`"make a highlight reel for tiktok of my trip to edmonton 9:16"` must parse
Edmonton / 9:16 / 30s and return only Edmonton clips.
2. **AI indexing** — the resumable pipeline extracts metadata (EXIF via
   exiftool, GPS, camera, taken-at), generates thumbnails/proxies (FFmpeg),
   embeds frames (CLIP), transcribes speech (whisper), detects faces
   (insightface), scores aesthetics, and captions key scenes (Ollama
   qwen2.5vl). Progress streams over the WebSocket.
3. **Search** — free-text hybrid search fuses vector similarity + transcript
   full-text + caption text, filterable by face name, tags, date range, GPS
   radius. `/api/dedupe` finds exact (hash) and near (phash) duplicates.
4. **Edit recipe** — Edit Studio: describe an intent ("60-second highlight
   reel of the Vancouver trip, upbeat, vertical 9:16 for TikTok") and the
   planner returns a clip list with transitions, captions and a duration
   target. Adjust clips with PUT, then **approve** — the hard gate before any
   render/export happens.
5. **Render / export** — render with FFmpeg (trims, crossfades, loudnorm,
   ratio crop, burned captions, optional beat-sync music); or export to an
   NLE: DaVinci Resolve (live timeline build), FCPXML 1.10, or CMX3600 EDL.

## NLE export

- **DaVinci Resolve 21 (Linux)** — `POST /api/export/resolve` builds a real
  Resolve project via the Python scripting API: media into bins, timeline
  from clips, markers, colored clip flags, captions as a subtitle track.
  Requirements and setup: see [docs/RESOLVE.md](docs/RESOLVE.md). Resolve
  must be running with external scripting enabled, or you get a 503 with
  directions.
- **FCPXML / EDL** — universal interchange: import into Resolve, Premiere,
  Final Cut, Kdenlive, Shotcut, etc. These are the recommended path when the
  live Resolve API is unavailable.
- **CapCut** — `POST /api/export/capcut` writes a `draft_content.json`
  project folder, **experimental**: CapCut's desktop project format changes
  between versions and is not officially documented. If the CapCut import
  breaks, export FCPXML/EDL and import that instead — that is the supported
  CapCut path.

## Troubleshooting

- **No CUDA / CPU fallback** — `/api/health` and the WebSocket emit
  `gpu:"cpu"` plus a warning. Indexing is slower but works. Verify with
  `nvidia-smi`; the setup summary prints the GPU mode.
- **Ollama not running** — captions stall / `/api/config` shows an error.
  Check `curl -s http://127.0.0.1:11434/api/tags`; if empty, start
  `ollama serve` (setup.sh does this automatically). Log:
  `/tmp/mediaforge_ollama.log`.
- **exiftool missing** — metadata (dates, GPS, camera) comes back empty.
  `apt-get install -y libimage-exiftool-perl`, or re-run setup.sh.
- **sqlite-vec extension fails to load** — vector search falls back to a
  cosine scan over in-memory embeddings (works, slower on big libraries).
  Check the backend log for the exact load error.
- **Port conflicts** — backend wants :8420, Vite dev :5173. If either is
  taken, run.sh reports it (or the health curl fails) — `ss -ltnp | grep 8420`
  to find the squatter.
- **DaVinci Resolve export returns 503** — Resolve not running, or external
  scripting disabled. See docs/RESOLVE.md.
- **Setup left a stale pid** — run.sh removes dead pid files in
  `/tmp/mediaforge_*.pid` automatically.

## Security note

MediaForge is **local-first**: everything runs on this machine, nothing
leaves it unless you export files yourself. `run.sh` binds the backend to
`0.0.0.0:8420` so you can browse from your phone/tablet on the LAN — that
means anyone on your network can reach the API and read indexed content. If
you are on an untrusted network, either browse only via
`http://localhost:8420`, or firewall the port:

```bash
sudo ufw allow 8420/tcp     # only if you want LAN access
sudo ufw deny 8420/tcp      # revert to localhost-only
```

There is no auth layer yet — treat 0.0.0.0 exposure as deliberate and
bounded to trusted networks.
