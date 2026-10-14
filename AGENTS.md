# AGENTS.md — MediaForge

Instructions for AI coding agents (Claude Code, Codex, Hermes Agent, and anything else
that reads `AGENTS.md`). Two kinds of work happen here; know which one you're doing.

1. **Using MediaForge** (find footage, plan/render reels, edit photos): use the
   `mediaforge` MCP tools. Don't call the HTTP API with curl when a tool exists.
2. **Changing MediaForge's code**: follow the repo map and rules below.

## Using MediaForge through MCP

`agents/mediaforge_mcp.py` is a stdlib-only MCP server (no pip install) that wraps the
backend's HTTP API. It works from the Ubuntu box itself or from a Windows desktop on the
LAN (`MEDIAFORGE_URL=http://<ubuntu-ip>:8420`). Setup: `scripts/agents-setup.sh`
(Ubuntu) or `scripts/agents-setup.ps1` (Windows).

Golden workflow:

1. `mf_status`: is the backend up, is the library indexed, is PhotoCraft installed?
2. Find material: `mf_search` (meaning, speech, captions), `mf_list_assets` (structured
   filters), `mf_trips`, `mf_places`, `mf_faces`. Use `mf_look` to actually see a shot.
3. Video: `mf_plan_edit` with a precise intent (place, people, mood, seconds, ratio), then
   refine with `mf_update_plan`, then `mf_approve_plan`, then `mf_render` or `mf_export`.
   When the user wants to keep editing, `studio_build` turns the plan into an editable
   Elevate Vision Studio project (`.fcproj`), and `render=true` also exports the MP4.
4. Photos: `pc_apply` with a recipe (`auto`, `punchy`, `clean`, `film`, `warm`, `bw`) or
   explicit PhotoCraft steps (`pc_commands` lists them). Then compare `mf_look` (before)
   with `mf_view_export` (after), and redo anything that got worse.

MCP prompts `highlight_reel`, `photo_glowup` and `library_report` encode these flows.

### Hard rules for agents

- **Approval is the human's call.** `mf_approve_plan` hands back a review link unless the
  owner set `MEDIAFORGE_AGENT_CAN_APPROVE=1`. When you get a link, stop and show it.
  Never work around this: no `auto_approve`, no raw `POST .../approve`.
- **Originals are read-only.** Every edit writes a new file under `exports/`. Never
  delete, move or overwrite anything in the user's media folders. Duplicates are
  reported (`mf_duplicates`), not resolved; the user resolves them in the Dedupe page.
- **PhotoCraft runs inside an allow-list.** `pc_apply` only forwards in-memory
  adjustments and filters. Don't try to pass file paths, LUT files or `file.*` commands.
  For layered, interactive work on local files, use the separate `photocraft` MCP server,
  which is confined to the folders it was started with.
- **Studio's raw MCP server is not sandboxed.** Use `studio_build`. Only use the
  `evstudio` MCP server if the owner registered it, and keep it to project files under
  `exports/filmcraft/`.
- **Everything runs locally.** Don't upload media or transcripts to third-party
  services unless the user explicitly asks you to.

## Repo map

```
backend/app/        FastAPI app (main.py = every route), config.py (env knobs)
  ai/               CLIP, whisper, faces, aesthetic, captions (Ollama), pipeline worker
  ingest/           scanner, metadata/exiftool, thumbs, dedupe, trips, watcher
  edits/            intent parser, planner, FFmpeg render, Resolve/FCPXML/EDL/CapCut export
  photocraft.py     photocraft-cli bridge: allow-list, recipes, apply job
  filmcraft.py      plan -> FilmCraft / Elevate Vision Studio project (+ MP4)
backend/tests/      pytest (conftest isolates data/exports into a temp MF_ROOT)
frontend/           React + Vite UI (pages/, components/, lib/api.ts)
agents/             mediaforge_mcp.py (MCP server), configs for Codex/Hermes
.claude/skills/     mediaforge skill (also installed for Codex/Hermes by setup scripts)
studio/             Elevate Vision Studio: brand.json, rebrand.py, assets/, README
scripts/            setup.sh, run.sh, agents-setup.sh/.ps1, studio-setup.sh/.ps1
docs/               API_CONTRACT.md, RESOLVE.md, AGENTS_INTEGRATION.md
adlab/              AdLab: ad research -> teardown -> remake (own README, venv, tests)
ignitemerch/        separate Next.js storefront (its own README); don't mix changes
```

## Build, run, test

```bash
./scripts/setup.sh                       # idempotent system + venv + frontend build
./scripts/run.sh                         # backend :8420 serving the built UI
cd backend && .venv/bin/python -m pytest -q          # full suite (no GPU/models needed)
cd backend && .venv/bin/python -m pytest -q tests/test_photocraft.py tests/test_mcp_server.py
cd frontend && npm run build             # typecheck + bundle
```

Tests must never touch real data: `tests/conftest.py` points every `MF_*` dir at a temp
root and disables model downloads and Ollama. Keep it that way. New tests that scan
files should create unique content, because scans dedupe by SHA-256.

## Code conventions

- Python 3.12, type hints, module docstring explaining *why*. Match neighbouring style.
- External tools (ffmpeg, exiftool, photocraft-cli) run via `subprocess` with arg lists,
  never `shell=True`, with `env=proc.tool_env()` and a timeout.
- Long work runs as a job: `jobs.run(kind, factory)`, which streams progress over `/ws`.
- New endpoints go in `main.py` with a Pydantic model in `schemas.py` and a contract
  line in `docs/API_CONTRACT.md`. If agents should use it, add an MCP tool in
  `agents/mediaforge_mcp.py` and a test in `tests/test_mcp_server.py`.
- VRAM is shared with Ollama on a small card. Load models lazily and release them
  (see `ai/gpu.py`).
- Never commit `data/`, `exports/`, `models/`, `.env`, or media.
