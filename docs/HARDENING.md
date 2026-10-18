# MediaForge hardening and batch workflow

This extends the existing story/caption build rather than replacing it. Work is
on `g15/codex-hardening`; the running user service has **not** been restarted.
Build the frontend and restart the backend together before using these additions.

## Activation

1. Generate a token and append it to the existing environment file without
   displaying it. Run once; if rotating an existing token, replace its existing
   entry in a private editor instead of accumulating duplicates:

   ```bash
   python3 - <<'PY'
   import os, secrets
   path = '/home/g15/mediaforge/.env'
   fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
   try:
       os.fchmod(fd, 0o600)
       os.write(fd, ('\nMF_API_TOKEN=' + secrets.token_urlsafe(48) + '\n').encode())
   finally:
       os.close(fd)
   PY
   ```

2. Privately copy that same token into the existing MediaForge MCP server's `env`
   mapping as `MF_API_TOKEN`. Keep `MEDIAFORGE_URL` unchanged. See `mcp/README.md`
   for the Claude Code/Hermes configuration shape. Never place the token in a URL.
3. Confirm jobs and the AI worker are idle before restarting. Then run:

   ```bash
   cd /home/g15/mediaforge/frontend
   npm run build
   systemctl --user restart mediaforge.service
   systemctl --user is-active mediaforge.service
   ```

4. Reload MediaForge, sign in using the token, and reconnect/reload the MediaForge
   MCP server in Claude Code/Hermes so it discovers the tools and environment.
   A running MCP process does not pick up changed environment automatically.

Authentication is optional for backward compatibility. Without `MF_API_TOKEN`,
the API is still open to clients that can reach it. The browser stores an opaque
HttpOnly, SameSite=Strict cookie, never the raw token in local/session storage.
Sessions last 12 hours and are revoked by logout, process restart or token change.
WebSockets check authorization before broadcasts and at most 15 seconds apart.
Protected HTTP responses use `private, no-store`. Cookies are Secure over HTTPS.
`MF_CORS_ORIGINS` defaults empty; additional cross-origin cookie access requires
an explicit operator setting. Do not add unrelated applications to that list.

## Working with existing media tonight

Find source assets with the existing search/list tools. Build or update a draft,
review its clip selection, and approve it before rendering. Select the desired
output presets in Edit Studio or pass `presets` to `mf_render`; each batch runs
sequentially and returns every output path. Draft changes revoke approval.

Use silence tightening on an asset or an ordinary edit plan to create a **new**
draft. Narrated stories intentionally reject tightening because cutting narration
requires a separate synchronization workflow. Silence comes from local FFmpeg;
filler deletion requires preserved faster-whisper word timings. Historical segment
transcripts do not supply invented word timing. Original source media is untouched.

Review caption words and QC in the asset view or story's Edit Studio panel.
Story word edits retain word count and beat boundaries to preserve emphasis indices
and narration timing. They correct subtitles, not already-recorded spoken audio.
B-roll replacement uses a one-based beat number; trims are fitted to that beat's
voiceover slot or rejected when source footage is too short. Version restore also
returns the plan to draft. Corrupt/mismatched story timelines cannot be approved.

Removable DCIM cards appear as pending imports. Approval copies into a dated library
folder, skips size+SHA256 duplicates and enqueues indexing. Changed card contents
invalidate the pending approval. Source reads use no-follow descriptors. Dismissal
lasts until that candidate detaches/changes or the process restarts. No card writes.

Music defaults to `/srv/iris/media/music` unless a configured music folder overrides
it. Use licensed local files. Metadata uses CPU heuristics and cached results;
rough BPM/mood labels are suggestions, not beat-accurate analysis. `music="auto"`
is explicit; existing renders continue without music by default. Auto selection
requires a usable local track. No music-generation service is involved.

## API additions

All routes below require the configured bearer token or browser session, except
login itself. Existing routes and positional MCP arguments remain compatible.

| Method / route | Input or behavior |
|---|---|
| POST `/api/auth/login` | `{token}` → opaque HttpOnly session |
| GET `/api/auth/session` | Session status and whether auth is enabled |
| POST `/api/auth/logout` | Revoke current browser session |
| POST `/api/edits/tighten` | Exactly one of `asset_id`, `plan_id`; `silence_db=-35`, `min_silence_s=.4`, `remove_fillers=true`, `padding_s=.08` |
| GET/PUT `/api/assets/{id}/caption-words` | Read words/QC; write `{words:[{text,start,end}]}` |
| GET/PUT `/api/edits/plan/{id}/caption-words` | Story words/QC; same write shape, preserve count and beats |
| GET `/api/import/pending` | Pending source, count, bytes and types |
| POST `/api/import/pending/{id}/approve` | Create copy/indexing job |
| POST `/api/import/pending/{id}/dismiss` | Dismiss candidate |
| GET `/api/edits/plan/{id}/versions` | Immutable revision metadata |
| POST `/api/edits/plan/{id}/versions/{version}/restore` | Restore snapshot as draft |
| POST `/api/edits/plan/{id}/swap-broll` | `{beat_index,asset_id}`, one-based beat |
| GET `/api/edits/plan/{id}/reframe-preview?mode=smart` | Approved plan: three crop-box stills per clip; also accepts center |
| POST `/api/render` | Adds `presets`, `reframe`, `safe_zones`, `guide_overlay`, `music="auto"` |
| GET `/api/render/{job_id}` | Adds `output_paths` mapping, retains legacy `output_path` |
| GET `/api/music` | Adds duration, loudness, rough BPM, filename/folder and mood tags |

Presets are `9:16` (1080×1920), `4:5` (1080×1350), `1:1` (1080×1080),
`16:9` (1920×1080). Smart crop uses existing faces, then lightweight CPU motion,
then center. Crop analysis is reused across a batch; rendering stays sequential.
Safe-zone margins are fractional top/bottom/left/right. Vertical social defaults
reserve 12% top, 22% bottom and 14% right. Guide overlays are optional preview aids.
Batch cancellation is checked between analysis and presets, not mid-FFmpeg.

## MCP additions and extensions

- `mf_tighten(asset_id=None, plan_id=None, silence_db=-35, min_silence_s=.4, remove_fillers=True, padding_s=.08)`
- `mf_get_captions(asset_id=None, style="standard", plan_id=None)` preserves asset cues/downloads and includes editable words; exactly one source.
- `mf_update_captions(asset_id=None, words=None, plan_id=None)`
- `mf_caption_qc(asset_id=None, style="standard", plan_id=None)`
- `mf_list_pending_imports()` and `mf_approve_import(import_id)`
- `mf_plan_versions(plan_id)` and `mf_restore_plan(plan_id, version_id)`
- `mf_swap_broll(plan_id, beat_index, asset_id)`
- `mf_list_music()`
- `mf_render` retains existing arguments, adding `presets=None, reframe="smart", safe_zones=None, guide_overlay=False, music=None`.

`mcp/server.py` is canonical. The existing `/home/g15/mediaforge-mcp/server.py`
installation is synchronized at handoff. Direct download links still require an
authenticated browser session or a bearer header from a programmatic client.

## Verification and limits

See STATUS.md for actual suite counts and CODEX_HANDOFF.md for integration details.
All tests use disposable databases/media and mock paid calls. No real library
migration, source deletion, paid generation or platform upload was performed.
The new frontend build is staged outside live `frontend/dist` until activation.
An isolated HTTP server verified login/logout, private media/cache headers, approval
and crop previews. Browser visual QA could not run: the browser returned
`ERR_BLOCKED_BY_CLIENT` for both isolated preview addresses. Review the UI after
activation; production authentication and physical-card behavior remain unverified.

`scripts/audit.sh` does not install or upgrade packages. The retained frontend
lockfile has one high `source-map-js` advisory, GHSA-68fv-2mgg-jv7q (<1.2.2).
A targeted 1.2.2 update was tested and cleared npm audit, then reverted to respect
the no-auto-upgrade instruction. `pip-audit` and `syft` were not installed, so
backend vulnerability auditing and SBOM generation are explicitly incomplete.

## Content batch additions (`g15/content-batch`)

Everything here is local rendering; the only paid call remains the existing
consent-gated ElevenLabs story narration.

- **B-roll source control** — `POST /api/edits/story` and `mf_create_story_plan` accept
  `broll_filter` `{path_prefixes, require_aerial, exclude_faces, min_aesthetic, asset_ids,
  exclude_asset_ids}` and `beat_hints` (one search query per beat). The filter limits the
  candidate pool; search only ranks inside it. Drone footage is under
  `/srv/iris/media/library/blue-drone-1o1/`. `GET /api/assets?path_prefix=` lists by folder.
- **Beat maps** — `scripts/beatmap.py <files|dirs>` writes `<music dir>/.beats/<file>.json`
  (librosa is optional and only needed by that script; use a separate venv, see its docstring).
  The runtime only reads the JSON (`edits/beatmap.py`). A track without a map renders as before.
- **Beat-synced story renders** — with a mapped track, `music_start` (default: best section on a
  downbeat) positions the music, each internal cut moves onto the nearest beat within
  +/-150 ms by adjusting neighbouring clip in/out points (timeline length and voice never
  move), glitch flashes land on the downbeat nearest each beat boundary (within 0.75 s), and
  music fades over the last 0.6 s. `music_start` is also on `POST /api/render` / `mf_render`.
- **Music-led B-roll** — `POST /api/edits/broll` / `mf_create_broll_plan` (see its docstring):
  cuts on downbeats, whole bars, drop clip, kinetic `text_line` on bar 2, optional `hook_text` and
  `brand`, `silent=true` for a 1.6 s cadence with a silent AAC track. The plan's sidecar lives in
  `exports/broll/<plan_id>/broll.json`; render reads music/start from it, ends with a 0.5 s fade,
  and refuses plans whose clips no longer add up to the music timeline.
- **Hook and brand end card** — `hook_text` (<= 90 chars): gradient glitch-in headline 0-1.4 s in
  the upper safe zone (emphasis words that would start earlier fall back to body captions).
  `brand`: ids in `config/brands.yaml`; a 1.8 s card (frozen blurred+darkened last frame, brand
  name, CTA, url/handle) is appended at render time; voiceover/captions finish before it and the
  music continues. Hook and card only appear in the kinetic style, and are not part of
  exported NLE/caption packages.
- Kinetic body captions are 5.5% of the (safe-zone) frame height on portrait output.
