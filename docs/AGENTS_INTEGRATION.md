# AI agents + PhotoCraft integration

MediaForge can be driven end to end by **Claude Code**, **OpenAI Codex** and **Nous
Hermes Agent**, on the Ubuntu box itself or from a Windows desktop on the LAN, and it
now edits photos with **PhotoCraft**, an open-source, pure-Rust reimplementation of
Photoshop.

```
 Windows desktop                         Ubuntu box
 ┌───────────────────────────┐           ┌────────────────────────────────────────┐
 │ Claude Code · Codex       │           │ Claude Code · Codex · Hermes Agent     │
 │ (Hermes: inside WSL2)     │           │                                        │
 │   │ stdio MCP             │           │   │ stdio MCP                          │
 │   ├─ mediaforge_mcp.py ───┼── HTTP ──►│   ├─ mediaforge_mcp.py ──► FastAPI :8420│
 │   └─ photocraft-cli mcp   │  :8420    │   └─ photocraft-cli mcp      │         │
 │      (local files,        │           │      (local files, sandboxed)│         │
 │       sandboxed roots)    │           │                              ▼         │
 └───────────────────────────┘           │   /api/photocraft/apply → photocraft-cli run
                                         │   (allow-listed commands, exports/ only)│
                                         └────────────────────────────────────────┘
```

## Setup

**Ubuntu** (where MediaForge runs):

```bash
./scripts/setup.sh            # if you haven't already
./scripts/agents-setup.sh     # photocraft-cli + MCP for every installed agent
./scripts/run.sh
```

**Windows desktop** (PowerShell, no admin; needs Python 3.10+ and a clone of this repo):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\agents-setup.ps1 -MediaForgeUrl http://<ubuntu-ip>:8420
```

On the Ubuntu box, allow the port if ufw is on: `sudo ufw allow from 192.168.0.0/16 to any port 8420 proto tcp`.

Both scripts are idempotent. They:

1. Download `photocraft-cli` from the official `storytold/photocraft` GitHub release
   and **refuse to install unless its SHA-256 matches the release's `SHA256SUMS.txt`**.
2. Register two MCP servers with each agent found (`agents/register_agents.py`):
   - `mediaforge`: `agents/mediaforge_mcp.py`, stdlib-only Python, talks HTTP to
     `MEDIAFORGE_URL`.
   - `photocraft`: `photocraft-cli mcp`, PhotoCraft's own server for layered,
     interactive work on local files. It may read only `PC_READ_ROOT` / `-ReadRoot`
     (default `~/Pictures`) and write only `PC_WRITE_ROOT` / `-WriteRoot`.
3. Install the `mediaforge` skill (producer playbook) for each agent.

| Agent | MCP config written | Skill | Project instructions |
|---|---|---|---|
| Claude Code | `claude mcp add-json --scope user` (+ `local` on Windows); repo also has `.mcp.json` | `~/.claude/skills/mediaforge/` and `.claude/skills/` | `CLAUDE.md` → `AGENTS.md` |
| Codex | managed block in `~/.codex/config.toml` | `~/.codex/skills/mediaforge/` | `AGENTS.md` |
| Hermes Agent | managed block under `mcp_servers:` in `~/.hermes/config.yaml` | `~/.hermes/skills/mediaforge/` | `AGENTS.md` |

Managed blocks sit between `>>> mediaforge` / `<<< mediaforge` markers. The rest of your
config is untouched, and a `.bak` copy is kept. Hermes needs Linux, macOS or WSL2; on a
Windows desktop, run `agents-setup.sh` inside WSL2.

## What agents can do

`mediaforge` MCP tools (all non-destructive; originals are never modified):

| Tool | Does |
|---|---|
| `mf_status` | health, GPU, library size, PhotoCraft availability, approval mode |
| `mf_search`, `mf_list_assets`, `mf_asset` | semantic/transcript search, structured filters, detail |
| `mf_look`, `mf_view_export` | return images so multimodal agents can *see* before/after |
| `mf_trips`, `mf_places`, `mf_faces`, `mf_duplicates` | library structure (duplicates are read-only) |
| `mf_plan_edit`, `mf_trip_reel`, `mf_get_plan`, `mf_list_plans`, `mf_update_plan` | draft and refine reels |
| `mf_approve_plan` | the human gate (see below) |
| `mf_render`, `mf_export` | MP4 render; Resolve / FCPXML / EDL / CapCut |
| `studio_build` | editable Elevate Vision Studio / FilmCraft project (+ MP4 when approved); see `studio/README.md` |
| `mf_touchup` | built-in quick fixes |
| `pc_recipes`, `pc_commands`, `pc_apply` | PhotoCraft looks and explicit engine steps |
| `mf_scan`, `mf_jobs`, `mf_wait_job` | indexing and background jobs |

MCP prompts: `highlight_reel`, `photo_glowup`, `library_report` (in Claude Code, type
`/mcp__mediaforge__highlight_reel`).

**Approval gate.** By default `mf_approve_plan` doesn't approve. It returns a link to
Edit Studio for you to review. Set `MEDIAFORGE_AGENT_CAN_APPROVE=1` (or
`CAN_APPROVE=1 ./scripts/agents-setup.sh`, `-CanApprove` on Windows) to let agents
approve. This is a guard rail in the MCP layer, not an authentication boundary: anything
that can reach `:8420` can call the HTTP API directly (see *Known gaps*).

## PhotoCraft in the backend

`backend/app/photocraft.py` + routes:

- `GET  /api/photocraft/status`: `{available, path, version, recipes}`
- `GET  /api/photocraft/recipes`: named looks with their exact steps
- `GET  /api/photocraft/commands?filter=`: allow-listed engine commands + param docs
- `POST /api/photocraft/apply` `{asset_id, recipe?, steps?, format, quality}`
  → `{job_id, output_path, output_url, steps}`; progress over `/ws` as kind `photocraft`

Recipes (`auto`, `punchy`, `clean`, `film`, `warm`, `bw`) were verified against
PhotoCraft 0.8.2's engine. The binary is found via `MF_PHOTOCRAFT_CLI`, `PATH`, or
`~/.local/bin/photocraft-cli`.

**Why an allow-list?** PhotoCraft's own MCP server sandboxes file access, but
`photocraft-cli run` (what the backend calls) is a *trusted local* entry point with no
path restrictions (PhotoCraft's `book/src/automation/cli.md`). So MediaForge forwards
only in-memory pixel commands (`image.adjustments.*`, `filter.{blur,sharpen,noise,
stylize,distort,pixelate,render}.*`, auto tone/contrast/color, crop/resize/rotate). It
denies the LUT and match-colour commands, which load files. It rejects any path-like
param key or value, caps steps at 32 and params at 16 KB, runs without a shell under a
timeout, and writes only to `exports/photocraft/` through a temp file and atomic rename.

## Security review of storytold/photocraft (2026-10-14, commit `706638b`)

Reviewed statically before integrating; nothing from the repo was executed except
building `photocraft-cli` from the reviewed source to test this integration.

| Area | Finding |
|---|---|
| Provenance | `github.com/storytold/photocraft`, MIT OR Apache-2.0, part of the ArtCraft apps. Releases are code-signed (Apple notarised, Azure-signed on Windows) and ship `SHA256SUMS.txt`. |
| Dependencies | 800 crates.io packages plus 2 git deps from the same org (`craft-libs`, `winprint`), pinned to exact commits. No custom registries, no `[patch]` overrides. |
| Build scripts | 4 `build.rs` files: compress bundled fonts/i18n and embed Windows resources. No network, no process spawning. No proc-macro crates of its own. |
| Network | No HTTP client in PhotoCraft's own code, no telemetry, no update pings. `reqwest` arrives only via `sigstore` inside `winprint` (Windows printing). |
| Process spawning | Fixed programs only: `fc-list`/`fc-match` (fonts), `sysctl`, `osascript` (monitor profile), and "Reveal in Finder" (`xdg-open`/`explorer`/`open`, arg list, no shell). |
| Obfuscation | No bidi/zero-width "Trojan Source" characters, no long encoded blobs. The only committed binary is a test WASM fixture. |
| Plugins | WebAssembly via `wasmi` with fuel (instruction) and memory limits. |
| Automation | TCP control: loopback only, 256-bit token, 1 MiB request cap, 16 connections. MCP: file access only under explicit read/write roots, symlink-escape safe, fails closed. |
| CI | `pull_request_target` is not used; release secrets live only in the release workflow. |

**Verdict: safe to use, with the caveats below.** It is early-alpha software (its README
says so), so treat outputs as drafts.

Caveats the project documents itself, and how this integration handles them:

- `photocraft-cli run`/`batch` don't restrict paths. MediaForge's allow-list handles this.
- MCP has no per-tool scopes beyond filesystem roots, and MCP stdio input isn't
  byte-capped. Keep `--automation-*-root` narrow (the setup scripts do).
- Bridge mode (`photocraft-cli mcp --bridge`) lets an agent drive the running desktop
  app's UI. It isn't enabled by these scripts.

## Known gaps / next steps

- **MediaForge API has no auth** and listens on `0.0.0.0:8420`. It was LAN-only before
  this change, but agents make it more reachable. Keep it behind your router/ufw.
  Adding an `MF_API_TOKEN` bearer check (MCP server + UI) is the recommended follow-up.
- The Edit Studio / Touch-up UI doesn't expose PhotoCraft recipes yet (API + agents only).
- `pc_apply` runs on the MediaForge machine. For PhotoCraft on files local to the
  Windows desktop, agents use the `photocraft` MCP server instead.
