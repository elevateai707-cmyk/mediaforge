# Media Forge interface (for AdLab's Producer)

Inspected 2026-10-14 from the Media Forge repo (`backend/app/main.py`, `ai/*`, `edits/*`,
`photocraft.py`, `filmcraft.py`, `docs/API_CONTRACT.md`). Everything below is what the
code does today; nothing is assumed.

## TL;DR
- **Media Forge edits and renders media you already have.** It does **not** generate
  images or video from prompts: there is **no ComfyUI** and no diffusion model in it.
- **No voiceover either.** AdLab will call ElevenLabs directly.
- **Open question for you:** is your ComfyUI a separate install, and where does it live?
  (See the end of this doc.)

## How to call it
- HTTP API on `MEDIAFORGE_URL` (default `http://127.0.0.1:8420`), JSON in/out, no auth,
  bound to 0.0.0.0 by `scripts/run.sh`. Long work returns a `job_id`; poll
  `GET /api/jobs/{id}` (or listen on `/ws`).
- AdLab wrapper: `adlab/mediaforge.py` → `MediaForge().health() / status() / release_gpu()`.
- Elevate Vision Studio (Media Forge's FilmCraft-based editor) also has a local CLI,
  `evstudio-cli`. AdLab uses it directly for stills: `render_still()`.

## Capability by Producer need

| Need | In Media Forge? | How |
|---|---|---|
| **Image (prompt → image)** | ✗ **No** | Nothing generates images. PhotoCraft (`POST /api/photocraft/apply`) only *edits* photos already in the library: recipes `auto/punchy/clean/film/warm/bw` or allow-listed engine steps. |
| **Short video (generate)** | ✗ **No** | Nothing generates footage. |
| **Short video (assemble)** | ✓ | From library assets only: `POST /api/edits/plan {"intent": "..."}` → `PUT /api/edits/plan/{id}` (clip list) → approval → `POST /api/render {"plan_id","ratio","captions":true,"music_path"?}` → MP4 (H.264, crossfades, loudnorm, centre-crop to ratio). Or `POST /api/export/filmcraft` for an editable Studio project (+ MP4 with `render:true`). |
| **Voiceover** | ✗ **No** | Not in Media Forge. AdLab Phase 5 will call ElevenLabs and mux the audio. |
| **Captions** | ✓ | Per-clip caption text is burned in by `/api/render` (drawtext, ~40-char wrap) and styled in Studio projects (centred, outlined, backed). Transcripts of existing videos come from faster-whisper `small.en` during indexing (`GET /api/assets/{id}/transcript`). |
| **Aspect ratios** | ~ Partial | `/api/render` and Studio export: **9:16, 16:9, 1:1**. **4:5 is not supported** there yet. Stills at any of 9:16/1:1/4:5/16:9 via `evstudio-cli` (`render_still`). |
| **Logo / end card** | ~ Partial | No end-card feature. Studio can place text/graphics; a branded end card would be a Studio title or a `render_still` card appended in Phase 5. |

### Approval gate (important for the Producer)
Media Forge only renders **approved** plans (`409` otherwise), and its agents can't
self-approve unless `MEDIAFORGE_AGENT_CAN_APPROVE=1`. AdLab has its own human review
queue (Phase 7). Phase 5 will need a decision: either AdLab renders with its own FFmpeg
step from generated assets (simplest, no Media Forge plan), or Media Forge plans are
approved by you as part of AdLab review. I'd recommend the first.

## Models: what loads, where, and how to free VRAM

| Model | Runs in | VRAM | Loaded | Unloaded |
|---|---|---|---|---|
| CLIP ViT-B-32 | Media Forge process (torch) | ~1.5 GB | lazily on first embed/search | `clip.unload()`; automatically between pipeline phases when free VRAM < 1 GB |
| faster-whisper `small.en` int8 | Media Forge process (CTranslate2) | ~1 GB | lazily per transcription | `whisper.unload()` |
| insightface `buffalo_sc` | Media Forge process (onnxruntime) | ~0.4 GB | lazily | `faces.unload()` |
| qwen2.5vl:7b (captions) | **Ollama** (separate process, :11434) | Ollama-managed | per request | after every request: `keep_alive=0` by default (`MF_OLLAMA_KEEP_ALIVE`); or `ollama_llm.unload_model()` |
| PhotoCraft / Studio | CLI subprocess per job | none (CPU) | per job | process exits |

**Freeing VRAM from AdLab:** `POST /api/gpu/release` (added for AdLab). It unloads CLIP,
whisper and faces, evicts the Ollama caption model, empties the CUDA cache, and returns:

```json
{"released": ["clip", "whisper", "faces"], "ollama_unloaded": true, "gpu": "cuda",
 "vram_total_mb": 10240, "vram_free_mb_before": 3100, "vram_free_mb_after": 9400,
 "running_jobs": []}
```

Models reload lazily on next use, which is the same path Media Forge's pipeline already
takes between phases. If `running_jobs` lists an `ai` job, the indexer may reload a model
immediately. AdLab's `gpu_job()` re-reads `nvidia-smi` after the release, so the log
shows what was actually freed. Python: `MediaForge().release_gpu()`. CLI: `adlab mf release`.

There is **no `/free` endpoint in Media Forge**. That name is ComfyUI's own API. If your
ComfyUI is a separate service, AdLab needs to call its unload too (see below).

## Smoke test (Phase 1)
`adlab mf smoke`: Media Forge status → GPU lock → VRAM (nvidia-smi) → `release_gpu()` →
a **9:16 test card rendered by Elevate Vision Studio** → VRAM again → JSON report in
`data/logs/smoke-*.json`. It's a text still, not an AI image, because Media Forge has no
generator. The ComfyUI image joins the smoke test once you answer the question below.

## Producer decision (Phase 5, built)
AdLab renders with its **own FFmpeg step** from generated assets (the option recommended
above), so no Media Forge plan approval is involved. Per batch the Producer:
1. `POST /api/gpu/release` once (Media Forge unloads CLIP/whisper/faces/Ollama), logged;
2. runs each ComfyUI job under AdLab's GPU file lock with `nvidia-smi` logged before/after
   (`data/logs/gpu.jsonl`), `ADLAB_COMFY_MIN_FREE_MB` as the floor;
3. calls ComfyUI `POST /free` when the batch is done.
Elevate Vision Studio's `render_still` is still used by `adlab mf smoke`; ads use PIL cards.

## Still open: your ComfyUI
Until you answer these, generated shots fall back to brand text cards (labelled in the
manifest), so the whole pipeline already runs end to end:
1. Is ComfyUI installed separately on the Ubuntu box? Its URL (default
   `http://127.0.0.1:8188`)? Set `COMFYUI_URL`.
2. Which workflows do you use for (a) a still image and (b) short video? Export each with
   **"Save (API Format)"** to `adlab/config/comfyui/image.json` and `video.json`, with the
   placeholders listed in `config/comfyui/README.md`.
3. Or is there a different "Media Forge" (another repo or folder) that does generation?
