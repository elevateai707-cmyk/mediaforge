# MediaForge integration handoff

## Objective and decisions

User asked to compare a supplied MediaForge master plan with the existing app,
adopt useful gaps, and omit redundant/unneeded features. Completed the selected
integration without replacing the app or introducing additional dependencies.
Read `docs/MASTER_PLAN_REVIEW.md` and `STATUS.md` before extending it.

## Implementation map

- `backend/app/subtitles.py`: pure cue generation, QC and SRT/VTT/ASS serializers.
- `backend/app/caption_routes.py`: asset caption document/download endpoints.
- `backend/app/transcripts.py` and `ai/pipeline.py`: atomic optional sidecar
  persistence; DB transcript remains usable if the sidecar cannot be saved.
- `backend/app/edits/package.py`: approved-plan ZIP snapshots and provenance.
  Never reads source paths from the manifest. Story sidecar loss is a 409.
- `backend/app/policy.py`, `edits/story.py`, schemas/main: per-job paid narration
  guard and redacted append-only receipts. Trusted local API, not authentication.
- `frontend/src/components/AssetLightbox.tsx`: caption controls and QC.
- `frontend/src/pages/EditStudioPage.tsx`: story consent, polling, draft loading,
  package download. Consent clears after each accepted job.
- `mcp/`: canonical bridge source and offline tests; mirrored to the pre-existing
  `/home/g15/mediaforge-mcp/` installation.
- `scripts/launch.sh` / `run.sh`: portable roots, loopback defaults, avoids duplicate
  backend when the installed user service is active.

## Runtime and testing

Service: `systemctl --user status mediaforge.service`.
Existing endpoint: `http://100.104.197.107:8420` (Tailscale).
Do not change this binding or read/print `.env` secrets casually.
Before a restart, check jobs and AI worker state; do not interrupt media work.

Run backend from `backend/`: `.venv/bin/python -m pytest -q`.
Run frontend from `frontend/`: `npm run build && npm run lint`.
Run MCP from `mcp/`: `/home/g15/mediaforge-mcp/.venv/bin/python -m unittest test_offline`.
Latest complete results: 206 backend tests passed, 1 skipped; 4 MCP tests passed;
frontend build/lint passed. See STATUS for live checks and existing warnings.

## Known boundaries

Old assets use stored segment timing unless explicitly retranscribed. Never
trigger full reanalysis simply to populate sidecars. Package media is referenced,
not bundled; montage captions are clip labels. Character caps are not USD budgets.
Voice consent is self-attested, not verified ownership. Existing Resolve/import
and dedupe authorization are not redesigned. The generic desktop entry/setup
templates still deserve a separate packaging cleanup if installation portability
becomes a task. No remaining implementation work is required for this selected
integration; future features are listed as deferred in the review.
