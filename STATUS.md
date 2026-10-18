# MediaForge status

Updated 17 October 2026 (America/Edmonton).

## Integration state

The earlier master-plan integration is committed at `556eafd`. Claude's follow-up
adds optional API/session authentication, non-destructive tightening, editable
captions/QC, approved removable imports, CPU smart crops, batch exports/social safe
zones, edit revisions/B-roll synchronization and local music selection.
Work is on `g15/codex-hardening`. Activation instructions: `docs/HARDENING.md`.

**The running mediaforge.service has not been restarted.** Its frontend files
still match the previous backend. New frontend output is staged in the Codex
work directory; run the documented build/restart together to activate.
No token was generated or installed, no secrets were read, and no real database
or source media was changed by tests. No paid narration or social upload occurred.

## Verification checkpoint

- Before this hardening pass: 206 backend tests passed, 1 skipped.
- Final full run: **259 passed, 1 skipped**, four existing deprecation warnings.
- Final suite includes measured safe-zone bounds, crop-path interpolation and missing-voiceover regressions.
- MCP: 9 offline tests passed; legacy render calls retain no-music behavior.
- Frontend: staged production build and lint passed. Bundle-size advisory and
  three Fast Refresh warnings (two existing, one AuthGate export) remain.
- Isolated HTTP smoke passed: public health, unauthenticated denial, session
  login, plan approval, three crop previews, private cache policy, cookie media,
  logout and denial after logout.
- Browser visual QA unavailable: isolated preview URLs returned
  `ERR_BLOCKED_BY_CLIENT`. Production auth/physical cards are not live-tested.
- Read-only rereview cleared auth, approval durability, batch locking, unique
  output paths, descriptor-based imports, reframe geometry and audio sync.

## Dependency audit

`scripts/audit.sh` ran and returned 1: the retained lockfile reports one high
source-map-js advisory, GHSA-68fv-2mgg-jv7q (<1.2.2). A targeted 1.2.2 update was
tested successfully but reverted to honor the no-auto-upgrade instruction.
`pip-audit` and `syft` are absent; backend auditing and SBOM creation were skipped,
not passed. No dependency upgrades are included.

## Operational limits

Auth remains optional/unset until configured. Sessions are process-local and
restart revokes them. History/import approvals are local. Filler cuts require
real preserved whisper timing; narrated stories reject tightening. Story caption
edits preserve word count and do not regenerate narration. Music mood/BPM and
smart crop are heuristics to review before upload. Batch cancellation occurs
between presets. Claude must reconnect its MCP client after bridge updates.

## Commits

- `556eafd`: earlier selected master-plan integration (already completed).
- `2e766e5`: authentication, edit history/captions/imports, CPU reframe and rendering, tests.
- `864f719`: frontend login and review/import/batch controls.
- `8f1a7d9`: backward-compatible MCP extensions and dependency audit script.

Repository changes are committed locally only. No merge, rebase or push.
