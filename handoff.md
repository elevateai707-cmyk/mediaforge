# MediaForge handoff — 2026-08-20

Cursor Grok ran out of credits mid-session. This is the state of `/home/bfam/mediaforge` for Claude Code.

**Do not paste `MEDIAFORGE-FOR-GROK.md` into chat.** Read it from disk if you need the original v2 spec. The addendum that overrode that spec is `MEDIAFORGE-V2-ADDENDUM.md` (untracked — do not commit it).

---

## 0. First things — the app is currently erroring

Live UI: **http://localhost:8420/** (LAN **http://192.168.0.219:8420/**)

The AI worker is looping on a SQLAlchemy 2 error. Logs (`/tmp/mediaforge_backend.log`):

```
sqlalchemy.exc.ArgumentError: Textual SQL expression 'DELETE FROM asset_fts WHE...'
should be explicitly declared as text('DELETE FROM asset_fts WHE...')
pipeline failed for asset 1 / 2 / 3 / 4  (repeats forever)
```

Cause: `_rebuild_fts` in `backend/app/ai/pipeline.py` passed raw SQL strings to `Session.execute`. Failed assets never reach `status='indexed'`, so the worker retries them every 2s and floods the job tray.

**The fix is already in the working tree (uncommitted, and uvicorn was not restarted after it).** `scripts/run.sh` has **no `--reload`**. After you finish the remaining edits:

```bash
# find pid: ss -ltnp | grep 8420
kill $(cat /tmp/mediaforge_backend.pid)   # or kill the python on :8420
./scripts/run.sh
```

Then confirm `/tmp/mediaforge_backend.log` no longer prints `ArgumentError`, and `GET /api/health` is `version: 2.0.0`, `gpu: cuda`.

Same class of bug existed in `backend/app/ai/faces.py` (all `db_session.execute("SELECT...")` calls). Also uncommitted: wrap via `_exec()` + `sqlalchemy.text()`.

---

## 1. Hard constraints (do not violate)

Work only in `/home/bfam/mediaforge`.

- **NEVER** `git checkout .`, `reset --hard`, `clean`, `stash`, or anything that discards the working tree.
- **NEVER** delete or recreate `data/mediaforge.db`. Schema changes = idempotent `ALTER TABLE` in `init_db()` only.
- Keep ffmpeg env hygiene from commit `b6c2a70`: `backend/app/proc.py` `tool_env()` on **every** ffmpeg / ffprobe / exiftool / ollama subprocess. Do not reintroduce Resolve `LD_LIBRARY_PATH` poisoning.
- **Do not rewrite** `backend/app/ai/aesthetic.py` scoring (addendum A3). Placeholder is allowed. You may only change how it *opens* images (HEIC helper).
- **Do not overwrite** `backend/tests/conftest.py`.
- Do not commit `data/` or `MEDIAFORGE-V2-ADDENDUM.md`.
- Offline geocode only (curated metros + `reverse_geocoder` package). No Nominatim-during-scan, no Google, no Mapbox.
- Golden-path GPS stamps:
  - Edmonton `+53.5461-113.4938/`
  - Vancouver `+49.2827-123.1207/`
- Python: `backend/.venv/bin/python` — `cd backend && .venv/bin/pytest -q`
- Frontend: `cd frontend && npm run build` (`tsc -b && vite build`)
- Workspace rule: imports at top of file (no inline imports unless a documented cycle). Several HEIC call-sites currently violate this — fix when you touch them.

---

## 2. Git

- Repo: https://github.com/elevateai707-cmyk/mediaforge (private)
- Branch: `master`, last **pushed** commit:

```
53c5565 feat: ship pass 2 command bar, map, trips, and duplicate review
615ac12 Rebuild the cutting-room UI, add the Forge help guide, and ship a desktop launcher.
3e543f1 v2 step 1a: ISO6709 parser + 61 tests (verified)
615e205 … 991a8e5   v2 steps 1–7 (backend)
```

**Uncommitted** (as of this handoff) — keep all of it:

```
M backend/app/ai/aesthetic.py
M backend/app/ai/captions.py
M backend/app/ai/clip.py
M backend/app/ai/faces.py
M backend/app/ai/ollama_llm.py
M backend/app/ai/pipeline.py
M backend/app/edits/planner.py
M backend/app/ingest/scanner.py
M backend/app/ingest/thumbs.py
M backend/app/touchup.py
M backend/tests/test_pipeline_worker.py
M frontend/src/components/AppShell.tsx
?? backend/app/images.py
?? backend/tests/test_images.py
?? MEDIAFORGE-V2-ADDENDUM.md          # do not commit
?? data/                             # do not commit
```

Do **not** invent a second branch unless asked. Conventional commits (`fix:`, `feat:`).

---

## 3. What is already built (on GitHub through 53c5565)

### Backend v2 (steps 1–7 + ISO 6709 follow-up)

- Offline gazetteer: `backend/app/geo/gazetteer.py` + `cities.json`. `haversine_km` lives here.
- ISO 6709 parser: `backend/app/geo/iso6709.py` (`GeoPoint`, `parse()`, `parse_point()`). **61 tests** in `backend/tests/test_iso6709.py`. Applied from `/home/bfam/Documents/CURSOR-FOLLOWUP.md`.
- Package is still **`reverse_geocoder`** (addendum A6). Do not switch to Penman `reverse-geocode`.
- iPhone GPS via exiftool `Keys:GPSCoordinates` + ffprobe, always `tool_env()`.
- Exiftool lookup: `shutil.which`, then `~/.local/bin/exiftool`, then `/usr/bin/exiftool`. User-local install is `/home/bfam/.local/opt/exiftool` with symlink `~/.local/bin/exiftool` (system apt install failed; no sudo).
- Place columns + trips tables via `ALTER TABLE` in `init_db()`.
- Worker uses **real asset ids** (bug B1 fixed). VRAM `unload()` is called between stages under pressure.
- Intent parser: `backend/app/edits/intent.py`. Golden prompt  
  `"make a highlight reel for tiktok of my trip to edmonton 9:16"`  
  → place Edmonton, ratio 9:16, duration 30s, platform tiktok.  
  Explicit `widen radius 80km` is parsed (`_parse_radius`).
- Planner **never mixes cities**. Empty place match returns an honest empty plan, not Vancouver clips.
- Config persist + folder watcher (`PUT /api/config`, `backend/app/ingest/watcher.py`).
- Places/trips APIs: `GET /api/places`, `GET /api/trips`, `POST /api/trips/{id}/reel`.

### Frontend (cutting-room + pass 2)

Theme: warm black, tungsten `#e8a317`, teal `#4ebdb8`, Fraunces + Figtree.

- Shell: grouped nav, mobile bar, grain, jobs tray, Forge help (`?`). `HelpChat.tsx` + `help-kb.ts`.
- **Command bar** first-focus on `/` — `frontend/src/components/CommandBar.tsx`. Reel-like text → `/studio` with `state.intent`; else `/search?q=`.
- **Trip cards** — `TripCards.tsx`. “Make reel” → `POST /api/trips/{id}/reel`.
- **Map** — `pages/MapPage.tsx`, Leaflet + OSM tiles, **no Mapbox**. `leaflet@1.9.4` + `react-leaflet@4.2.1`.
- Library: city chips, `?trip=` / `?city=` query params, infinite scroll.
- Studio: seeds from `location.state` (`intent` or `plan`); “Widen radius”; **720p proxy player** for the selected clip.
- Settings: folder watcher switch.
- **Duplicates** page: `/dedupe` → `GET /api/dedupe` + resolve.
- Desktop launcher: `scripts/launch.sh`, `scripts/MediaForge.desktop`, icons under `frontend/public/`.

### Verified before this crash

- `cd backend && .venv/bin/pytest -q` → **105 passed** (will be 106 once `test_rebuild_fts_uses_bound_sql` is included).
- Golden-path test `tests/test_golden_path.py` passed with real exiftool ISO6709 stamps on `/tmp/mf_golden`.
- Live `GET /api/health` → `2.0.0`, `cuda`, RTX 3080.
- Live `POST /api/edits/plan` with the Edmonton TikTok intent parsed correctly. Earlier in the day the live DB had **0 GPS**; the UI later showed **Edmonton (82)** and Calgary trips — a rescan happened. Re-curl the plan after the FTS fix:

```bash
curl -s http://127.0.0.1:8420/api/edits/plan \
  -H 'Content-Type: application/json' \
  -d '{"intent":"make a highlight reel for tiktok of my trip to edmonton 9:16"}'
```

Expect `parsed_intent.place == Edmonton`, `ratio == 9:16`, `duration_s == 30`, clips only from Edmonton (or honest empty + widen, never another city).

---

## 4. Uncommitted WIP (finish this first)

### A. SQLAlchemy `text()` — MUST ship, then restart uvicorn

- `backend/app/ai/pipeline.py` `_rebuild_fts` — already wrapped with `text()`.
- `backend/app/ai/faces.py` — `_exec()` helper wrapping cluster SQL. Also CUDA providers (`gpu.cuda_available()`), not the old `gpu.available` / `gpu.provider` (those never existed; health once showed insightface unavailable because of it).
- `backend/tests/test_pipeline_worker.py` — `test_rebuild_fts_uses_bound_sql`.

Face video frames previously called `thumbs.extract_frame` with the **wrong signature**. Now writes to `config.THUMBS_DIR / {id} / face_{25,50,75}.jpg` and `extract_frame(path, out, at=t)`.

### B. HEIC — in progress, not verified on the live worker

iPhone stills (`/home/bfam/iphone-media/100APPLE/IMG_0001.HEIC`) made Ollama/PIL throw `cannot identify image file`. New module:

- `backend/app/images.py` — `ensure_heif()` + `open_rgb()`
- Wired into clip / captions fallback / aesthetic fallback / ollama `image_to_b64` / thumbs / scanner phash / touchup
- `backend/tests/test_images.py` — skips if that HEIC is missing

**Check `pillow-heif` is in the venv.** If not: `backend/.venv/bin/pip install pillow-heif`. Then run `test_images.py`.

Several of these call-sites still use **inline imports** (`from app.images import open_rgb` inside functions). Move them to module top when you touch the files.

### C. Ollama planner timeouts

`backend/app/edits/planner.py` `_ollama_plan` now uses `retries=1, timeout=45.0`. `ollama_llm.generate` gained a `timeout=` arg. Intent: stop Studio from hanging on a dead/slow Ollama. Confirm a plan still falls back locally if Ollama is down.

### D. GPU badge

`frontend/src/components/AppShell.tsx`: if WS is connected but no gpu event yet, show **“GPU…”** instead of a red **CPU** badge. Health is CUDA; the red CPU badge was a false alarm. After changing this, `cd frontend && npm run build` (backend serves `frontend/dist`; no Vite in prod).

---

## 5. What is not done (honest leftover)

| Item | Status |
|---|---|
| Restart uvicorn so FTS/HEIC fixes load | **Not done** — this is why the UI still errors |
| Commit + push the uncommitted WIP | Not done |
| Worker skip/backoff after N failures | Not done. Until FTS is live it retries forever |
| HEIC captions on the live worker | Code written; not proven after restart |
| Virtualized library grid | Infinite scroll only |
| Touch-up strength slider | Presets only (`/touchup`) |
| `GEOCODER_PROVIDER` hook | Out of scope (stay offline) |
| B6 real aesthetic weights | Dropped by addendum |
| Switch `reverse_geocoder` → Penman | Do not do this |
| Unused `cmdk` | Already removed from `frontend/package.json` |

---

## 6. How to run / verify

```bash
# tests
cd /home/bfam/mediaforge/backend && .venv/bin/pytest -q

# frontend dist (required for :8420 UI)
cd /home/bfam/mediaforge/frontend && npm run build

# restart API (no reload)
kill $(cat /tmp/mediaforge_backend.pid) 2>/dev/null
/home/bfam/mediaforge/scripts/run.sh

# health + golden intent
curl -sf http://127.0.0.1:8420/api/health
curl -s http://127.0.0.1:8420/api/edits/plan \
  -H 'Content-Type: application/json' \
  -d '{"intent":"make a highlight reel for tiktok of my trip to edmonton 9:16"}'

# watch the worker
tail -f /tmp/mediaforge_backend.log
```

Do not report “verified” for anything you did not actually run.

---

## 7. Suggested order for Claude Code

1. Keep every uncommitted file. Do not revert.
2. Finish HEIC helper (venv package, top-level imports, `pytest tests/test_images.py tests/test_pipeline_worker.py`).
3. Full `pytest -q`. Fix anything red.
4. `npm run build`.
5. Restart uvicorn. Confirm FTS `ArgumentError` is gone and the AI job is indexing, not looping on assets 1–4.
6. Curl the Edmonton TikTok plan against the live library. Clips must be Edmonton-only.
7. Commit (user must ask, or they already want this shipped):  
   `fix: stop FTS SQLAlchemy crash and open iPhone HEIC stills`  
   Do not add `data/` or the addendum. Then `git push origin master` only if asked.
8. Only after that: leftover UX (virtualized grid, touch-up slider) if time.

---

## 8. Key paths

| Path | Why |
|---|---|
| `backend/app/ai/pipeline.py` | Worker + FTS crash |
| `backend/app/ai/faces.py` | Face SQL + video frame extract |
| `backend/app/images.py` | HEIC open (new) |
| `backend/app/geo/iso6709.py` | iPhone GPS parser |
| `backend/app/edits/intent.py` | Edmonton / radius parse |
| `backend/app/edits/planner.py` | Place prefilter + Ollama timeout |
| `backend/app/proc.py` | `tool_env()` — keep |
| `frontend/src/components/CommandBar.tsx` | Pass 2 command bar |
| `frontend/src/pages/MapPage.tsx` | OSM map |
| `scripts/run.sh` | Start :8420, serves `frontend/dist` |
| `/tmp/mediaforge_backend.log` | Live errors |

User media: `/home/bfam/iphone-media` (HEIC + MOV). SQLite: `data/mediaforge.db` (real library — treat as sacred).
