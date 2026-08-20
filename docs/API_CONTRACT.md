# MediaForge — API Contract (authoritative)

All endpoints are served by the FastAPI backend at http://localhost:8420.
Frontend must be built against THIS contract; backend must implement it.

## Conventions
- JSON in/out. Timestamps ISO-8601 UTC. Durations in seconds (float).
- Errors: `{"detail": "..."}` with proper HTTP status.
- Long jobs (scan, AI, render) return `job_id` immediately; progress flows
  over the WebSocket below.
- Resumable: every long job persists its state in SQLite and re-runs skip
  already-completed work (hash manifests).

## WebSocket
- Endpoint: `/ws` (ws://localhost:8420/ws)
- Server → client events (JSON):
  - `{"type":"job","job_id":...,"kind":"scan|ai|render|touchup","status":"running|done|error|cancelled","progress":0.0,"message":"..."}`
  - `{"type":"job.batch","job_id":...,"progress":0.0,"done":N,"total":M}`
  - `{"type":"gpu","mode":"cuda|cpu","vram_mb":...,"warning":"..."}` (sent once at connect + on mode change)

## REST endpoints

### System
- `GET /api/health` → `{"status":"ok","gpu":"cuda|cpu","gpu_name":"...","vram_mb":N,"version":"1.0.0"}`
- `GET /api/config` → `{"use_cloud_llm":false,"media_dirs":["..."],"ollama_model":"qwen2.5vl:7b","resolve_available":bool,"watcher_enabled":true,"music_dir":""}`
- `PUT /api/config` body `{media_dirs?, watcher_enabled?, music_dir?, use_cloud_llm?}` → same as GET; dirs persist to SQLite + `.env`
- `POST /api/fs/stat` body `{"path":"/abs"}` → `{exists,is_dir,videos,photos,sample_count}` (absolute paths only)
- `GET /api/places` → `[{city,region,count,lat,lon}]`
- `GET /api/trips` / `GET /api/trips/{id}` / `PUT /api/trips/{id}` `{title}` / `POST /api/trips/{id}/reel`
- `GET /api/music` → scanned tracks from `music_dir`
- `POST /api/edits/plan` body `{intent, trip_id?, auto_approve?}` → includes `parsed_intent` + `match_stats`

### Scan & ingest (Phase 1)
- `POST /api/scan` body `{"paths":["/abs/dir1",...]}` → `{"job_id":"...","kind":"scan"}`
  Recursively indexes supported media (jpg/jpeg/png/heic/heif/mov/mp4/m4v/mkv/
  avi/webm/prores-mov). Skips files already present (SHA-256 manifest).
- `POST /api/scan/cancel` body `{"job_id":"..."}` → `{"ok":true}`
- `POST /api/rescan` — same as scan but force re-check metadata of known assets.
- `GET /api/dedupe` → `{"exact":[{"id":A,"id":B,"path_a":"...","path_b":"..."}],
  "near":[{"id":A,"id":B,"distance":0.08,...}]}` (phash distance < 0.1)
- `POST /api/dedupe/resolve` body `{"pair_id":"...","action":"keep_a|keep_b|delete_b"}`

### Assets
- `GET /api/assets?limit=50&offset=0&kind=photo|video&sort=taken_at|aesthetic|added&order=desc&tag=&face=&q=text&date_from=&date_to=&lat=&lon=&radius_km=`
  → `{"total":N,"items":[{Asset}]}`
  `q` does hybrid search: vector similarity + transcript FTS + caption text.
- `GET /api/assets/{id}` → full Asset incl. scenes summary + tags + faces
- `GET /api/assets/{id}/file` → original bytes (Range-supported for video)
- `GET /api/assets/{id}/thumb` → 512px webp
- `GET /api/assets/{id}/poster` → video poster frame (webp, 10% mark)
- `GET /api/assets/{id}/proxy` → 720p h264 mp4 proxy
- `GET /api/assets/{id}/transcript` → `{"segments":[{"start":..,"end":..,"text":".."}]}`
- `GET /api/assets/{id}/scenes` → `{"scenes":[{"id":..,"start":..,"end":..,"caption":"..","aesthetic":7.2}]}`

Asset JSON shape:
```
{"id":1,"path":"/abs","kind":"photo|video","mime":"image/heic","size":123,
 "width":4032,"height":3024,"duration":12.5,"taken_at":"2026-07-01T10:00:00Z",
 "added_at":"...","camera_make":"Apple","camera_model":"iPhone 15 Pro Max",
 "gps_lat":49.2,"gps_lon":-123.1,"aesthetic_score":7.3,"caption":"beach sunset",
 "status":"indexed","hash":"sha256...","tags":["beach","sunset"],"faces":["Kaleb"],
 "scene_count":4,"has_transcript":true}
```

### Search (Phase 3)
- `GET /api/search?q=beach sunset clips with Kaleb talking&limit=20`
  → `{"query":"...","results":[{Asset}],"took_ms":42}`
  Semantic + face-name + transcript FTS fused ranking.

### Faces (Phase 2/3)
- `GET /api/faces` → `{"clusters":[{"id":1,"name":null,"count":12,"thumb_asset_id":3}]}`
- `POST /api/faces/{cluster_id}/name` body `{"name":"Kaleb"}` → `{"ok":true}`
- `GET /api/faces/{cluster_id}/assets?limit=50` → assets containing that person
- `POST /api/faces/merge` body `{"from_cluster":1,"into_cluster":2}`

### Edit assistant (Phase 4)
- `POST /api/edits/plan` body `{"intent":"60-second highlight reel of the Vancouver trip, upbeat, vertical 9:16 for TikTok"}`
  → `{"plan_id":"...","status":"draft","summary":"...","clips":[{Clip}],"total_duration":60,"target_ratio":"9:16"}`
  Clip: `{"asset_id":1,"scene_id":3,"start":2.0,"end":7.5,"caption":"...","transition":"crossfade","score":8.1}`
  Plan is stored, NOT auto-rendered.
- `GET /api/edits/plan/{plan_id}` → full plan (for review UI)
- `PUT /api/edits/plan/{plan_id}` body `{...clips...}` — user edits before approval
- `POST /api/edits/plan/{plan_id}/approve` → `{"ok":true,"status":"approved"}`  ← hard approval gate
- `GET /api/edits/plans` → history of plans

### Render (Phase 4)
- `POST /api/render` body `{"plan_id":"...","ratio":"9:16|1:1|16:9","music_path":null,"captions":true,"width":1080,"height":1920}`
  → `{"job_id":"...","kind":"render","plan_id":"..."}`
  Renders with FFmpeg: concat + per-clip trims + xfade crossfades + loudnorm +
  center-crop to ratio + burned captions (whisper word timings) + optional beat-sync
  (fade/cut on beat grid when music_path given).
- `GET /api/render/{job_id}` → `{"status":"done|running|error","output_path":"/mediaforge/exports/reel_2026...mp4","progress":0.0}`

### NLE export (Phase 4)
- `POST /api/export/resolve` body `{"plan_id":"..."}` → tries DaVinci Resolve
  Python API (fusionscript/DaVinciResolveScript module). If Resolve is not
  running / module missing → HTTP 503 with helpful message:
  `{"detail":"DaVinci Resolve is not running or the scripting module was not found. Start Resolve once, enable 'External scripting using' = Network/Local in Preferences > System > General, then retry. See docs/RESOLVE.md."}`
  On success: `{"ok":true,"resolve":"v21.0.4","project":"MediaForge-<plan>"}`
  Imports media into bins, builds timeline from clips, adds markers + colored
  clip flags, captions as subtitle track.
- `POST /api/export/fcpxml` body `{"plan_id":"..."}` → `{"ok":true,"path":"/mediaforge/exports/<plan>.fcpxml"}`
  FCPXML 1.10, resources + spine + subtitles.
- `POST /api/export/edl` body `{"plan_id":"..."}` → `{"ok":true,"path":"/mediaforge/exports/<plan>.edl"}`
  CMX3600 EDL.
- `POST /api/export/capcut` body `{"plan_id":"..."}` → `{"ok":true,"path":"/mediaforge/exports/<plan>_capcut/draft_content.json"}` (experimental; doc-marked)

### Photo touch-up (Phase 5)
- `POST /api/touchup` body `{"asset_id":1,"preset":"auto_levels|white_balance|denoise|sharpen|all"}`
  → `{"job_id":"...","kind":"touchup"}`
  Non-destructive: writes to `/mediaforge/exports/touchup/<asset_id>_<preset>.jpg|png|webp`.
- `GET /api/touchup/preview?asset_id=1&preset=auto_levels` → side-by-side or overlay preview image

### Jobs
- `GET /api/jobs?limit=20` → recent jobs
- `GET /api/jobs/{job_id}` → `{"id":"...","kind":"...","status":"...","progress":0.0,"message":"...","created_at":"...","finished_at":"..."}`

## Filesystem layout (backend)
- `/home/bfam/mediaforge/backend/app/` — FastAPI app: `main.py`, `db.py`, `models.py`, `schemas.py`, `config.py`
- `/home/bfam/mediaforge/backend/app/ingest/` — `scanner.py`, `metadata.py`, `thumbs.py`, `dedupe.py`
- `/home/bfam/mediaforge/backend/app/ai/` — `clip.py`, `whisper.py`, `scenes.py`, `faces.py`, `aesthetic.py`, `captions.py`, `ollama_llm.py`, `pipeline.py` (resumable worker)
- `/home/bfam/mediaforge/backend/app/edits/` — `planner.py`, `render.py`, `resolve_export.py`, `fcpxml_export.py`, `edl_export.py`, `capcut_export.py`
- `/home/bfam/mediaforge/backend/app/touchup.py`
- `/home/bfam/mediaforge/backend/app/jobs.py` — in-process asyncio queue + SQLite persistence
- `/home/bfam/mediaforge/backend/app/ws.py`
- `/home/bfam/mediaforge/backend/tests/` — pytest smoke tests (API + ingest + search + render path)
- Data dirs: `/home/bfam/mediaforge/data/` (SQLite `mediaforge.db`, thumbnails, proxies), `/home/bfam/mediaforge/exports/`, `/home/bfam/mediaforge/models/`

## GPU rules
- CLIP/whisper/insightface run on CUDA when `torch.cuda.is_available()`; else CPU with
  a one-time warning surfaced on the WebSocket and /api/health.
- VRAM budget 10GB: CLIP ViT-B-32 ~1.5GB, whisper small.en int8 ~1GB, insightface
  buffalo_sc ~400MB, aesthetic ONNX ~100MB, Ollama qwen2.5vl:7b runs in its own
  process (already VRAM-safe by Ollama). Load models lazily and unload between
  phases when free VRAM < 1GB.
- Ollama captions: `ollama run qwen2.5vl:7b` via HTTP API at 127.0.0.1:11434
  (prompt: "Describe this image for a searchable media library, objects, scene,
  people, mood, 1-2 sentences.").

## Frontend
- Vite dev proxy: `/api` and `/ws` → http://localhost:8420
- Pages: Library (gallery/timeline), Search, Faces, Edit Studio (recipe →
  plan review → approve → render/export), Touch-up, Settings/Scan.
- Dark neon: deep charcoal bg (#0b0c10 family), purple (#a855f7) / pink (#ec4899)
  / red (#ef4444) accents, glassmorphism cards, framer-motion.
