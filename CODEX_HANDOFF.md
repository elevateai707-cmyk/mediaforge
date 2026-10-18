# MediaForge handoff to Claude Code

## Request and state

User asked to integrate useful missing parts of the supplied review, then notify
Claude Code so they can remotely control it for editing existing media and batch
content creation for TikTok/Instagram. Do not start uploads or paid generation
from this handoff. The user intends to direct that work in Claude.

Repository: `/home/g15/mediaforge`, branch `g15/codex-hardening`, based on previous
integration `556eafd` (on top of Claude's `b9ecf1b`). No merge/rebase/push.
Read `docs/HARDENING.md` for exact setup/API/MCP contracts and `STATUS.md` for tests.

## Activation still required

No service restart in this pass, per the supplied review. The live frontend is
kept at `556eafd` to match that running backend; new builds are staged outside it.
Before activation, inspect jobs/AI worker state and avoid interrupting media work.
Then follow the build/token/MCP/restart instructions in `docs/HARDENING.md`.
Do not claim the running service is protected until MF_API_TOKEN is configured
and the new backend is loaded. This agent never read `.env` or API keys.

## Implementation map

- `backend/app/auth.py`, config/main/ws: bearer token, revocable opaque cookie,
  login/session/logout, same-origin checks, no-store and ongoing WS authorization.
- `edits/plan_history.py`, planner/story/package: atomic snapshots, durable draft
  revocation before sidecar changes, restore, story sync validation, per-plan
  serialization and beat B-roll replacement. Package caption timing follows cuts.
- `caption_editor.py`, caption_routes: validated word edits and subtitle export,
  historical segment fallback, story emphasis preservation and failure rollback.
- `ingest/removable.py`, import_routes: manifest-bound approval, descriptor reads
  with no-follow traversal, dated copies, duplicate hashes and scan queue handoff.
- `edits/tighten.py`, tighten_routes: local silence/filler detection, new drafts,
  source-transition preservation; story tightening deliberately rejects.
- `edits/reframe.py`, render/kinetic: CPU face/motion crop paths, preview stills,
  social safe zones, batch presets, consistent plan revisions and unique outputs.
  Mixed transition audio uses delayed envelopes at exact visual offsets; tests
  analyze real output tone timing, not just FFmpeg exit status.
- `ingest/music.py`: scalable local metadata cache, confined paths, mood/BPM
  heuristics; optional auto music loops/trims/fades and existing ducking.
- Frontend AuthGate, CaptionWordEditor, PendingImports, PlanTools, EditStudio:
  authenticated media, review controls, all batch output links and logout cleanup.
- `mcp/server.py` is canonical; existing installed bridge is mirrored at handoff.
- `scripts/audit.sh`: optional pip-audit/Syft and npm audit, no automatic upgrades.

## Verification and limits

Tests use conftest temporary DB/media, disabled AI downloads, and no paid calls.
Run backend from `backend/`: `.venv/bin/python -m pytest -q`.
Build frontend with an external `--outDir` until activation; ordinary `npm run
build` writes the live static files, which would mismatch the old running API.
MCP tests: from `mcp/`, `/home/g15/mediaforge-mcp/.venv/bin/python -m unittest test_offline`.

Do not confuse passing tests with deployment. Browser visual validation was
blocked by the browser, though isolated HTTP checks passed. Physical removable
cards and production token setup are not verified. Retain the one documented npm
advisory pending an explicitly approved dependency change; optional backend/SBOM
audit tools are missing. Final checks: 259 backend tests passed (1 skipped), 9 MCP tests passed; frontend build/lint passed with documented warnings.

## Commits

- `556eafd`: earlier selected master-plan integration (already completed).
- `2e766e5`: authentication, edit history/captions/imports, CPU reframe and rendering, tests.
- `864f719`: frontend login and review/import/batch controls.
- `8f1a7d9`: backward-compatible MCP extensions and dependency audit script.

Repository changes are committed locally only. No merge, rebase or push.
