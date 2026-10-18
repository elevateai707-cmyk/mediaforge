# Master-plan integration review — 17 October 2026

The supplied **MediaForge Agent — Master Plan & Build Specification v1.0**
describes a new application. This repository already contains the core product.
The integration extends its FastAPI/SQLite backend, React interface, FFmpeg renderer
and existing MCP bridge. It does not create a second app or install new dependencies.

## Decisions

| Proposal | Existing implementation | Decision |
|---|---|---|
| Import folders, mounted devices, watch folders | `transfer.py`, `ingest/scanner.py`, `watcher.py`, serialized scan queue | Keep. Originals are copied or indexed; no new mounting or pairing service. |
| Hashes, dedupe, metadata, thumbnails and proxies | SHA-256 scanner, perceptual dedupe, FFprobe/EXIF and proxy generation | Keep. Reuse recorded hashes in handoff manifests. |
| Local transcription and scene detection | faster-whisper with CPU fallback, PySceneDetect | Keep. Preserve previously discarded word timings in optional JSON sidecars. |
| Caption exports and quality control | Story SRT/ASS and kinetic rendering existed; no general asset caption download/QC | Add SRT/VTT/ASS downloads, three presets and advisory QC in the asset details panel. |
| Non-destructive edit plans, approval, multiple ratios | Existing planner, editable draft plans, approve/render flow | Keep. Add downloadable snapshots of approved plans with provenance. |
| Resolve integration and fallback | Live scripting adapter plus EDL, FCPXML and experimental CapCut export | Keep. No replacement bridge; no new live Resolve operations. External editor compatibility still needs real import checks. |
| Agents and MCP | Separate installed FastMCP stdio server already drives the API | Preserve; add captions/package tools and keep a canonical version in `mcp/`. |
| ElevenLabs voiceover | Already implemented with per-beat narration and timestamps | Add per-job cloud/voice permission attestations, a character cap, and redacted local audit receipts. Add a usable story creation panel that waits for and opens the draft. |
| Local-first network defaults | Installed service deliberately binds its Tailscale address; generic scripts used all interfaces | Preserve deployed binding. Generic launch/run scripts now default to loopback and avoid duplicating the user service. |
| PySide desktop rewrite, new ORM/CLI/framework | Existing browser UI, FastAPI, SQLAlchemy, SQLite and desktop scripts | Leave out: duplicate architecture, no demonstrated need. |
| Stable-TS, WhisperX, diarization, OCR, additional editors | Additional runtimes/models not required for these changes | Defer until measured quality or workflow needs justify them. |
| ComfyUI/cloud generation, voice cloning, publishing | New external actions, not needed for current local caption/handoff work | Leave out. No new providers or paid calls were enabled or used. |
| Phone pairing, camera capture, automatic mounts | Mounted folders already work | Defer native device control; retain explicit user-managed mounts. |
| Immutable audit, complete policy gateway, signed workflows, sandbox, SBOM | Not an established capability of the app | Do not claim completion. This change adds bounded TTS operation guards and audit records, not multi-user authorization or a full security audit. |

## What is available now

- Open a transcribed video in Library to see **Caption exports**, select Standard,
  Social vertical or Accessibility, inspect QC and download SRT/VTT/ASS.
- In Edit Studio, **Story voiceover** requires separate confirmation for sending
  narration to ElevenLabs and using the selected voice. Consent is cleared after
  submitting a job; another job requires fresh confirmation. The character cap
  counts input script characters, not dollars or actual provider billing units.
- An approved plan exposes **Download handoff package**. The ZIP contains the
  plan, source hashes/paths, transcripts, caption formats, caption QC and a file
  checksum manifest. Story plans also include their narration sidecar.
- MCP adds `mf_get_captions` and `mf_handoff_package`; `mf_create_story_plan`
  requires `cloud_approved`, `voice_consent`, and an optional bounded character cap.
  Already-running MCP clients need to reload the updated server process.

## Limits kept visible

- Existing media keeps its segment timings. Fresh transcription saves word timings
  under `DATA_DIR/transcripts/<asset_id>.json`; a full-library rescan was not run.
  Stale or unreadable sidecars fall back to the stored segment transcript.
- Segment-only caption splits estimate cue boundaries. QC flags reading speed,
  line length, short/long duration, overlap and tight gaps; it does not measure
  speech synchronization accuracy, certify accessibility or guarantee platform safe zones.
- For ordinary montage plans, package captions are the plan's **clip labels**, not
  a speech transcript. Their timing follows the existing default FFmpeg crossfades.
  Narrated plans use the existing narration word timings. Missing story data blocks
  a story package instead of silently producing incorrectly timed labels.
- Package styling is independent of existing kinetic effects. The package contains
  no source media, rendered master or voiceover audio. It is a handoff recipe, not
  a self-contained project archive. Source hashes are recorded at ingest, not
  rehashed on download. Packages include local paths/text and should be reviewed
  before sharing.
- Consent is a caller attestation tied to a script hash, voice and model; it is not
  verified identity or voice ownership. Audit JSONL is application-append-only,
  not immutable against the machine owner. The API remains a trusted single-user
  service without an authentication layer. No credentials are copied into packages
  or audit receipts.

## Validation and source checks

Backend tests use temporary databases/media roots with background AI downloads
disabled. Paid TTS is mocked. Coverage includes fail-closed consent/caps, redacted
audit records, word-timing preservation, malformed subtitle text, ZIP checksum
integrity, source preservation and invalid/missing narration data. Existing render
tests exercise real FFmpeg with synthetic media. Frontend build and lint and offline
MCP contract tests are run separately; see `STATUS.md` for final results.

No extra package was selected based on a blanket “safe” label, repository popularity
or an unverified license table. The format/engine references used were the
[WebVTT specification](https://www.w3.org/TR/webvtt1/),
[FFmpeg subtitle-filter documentation](https://ffmpeg.org/ffmpeg-filters.html#subtitles-1)
and [faster-whisper's official repository](https://github.com/SYSTRAN/faster-whisper).
The latter already supports word timestamps; an extra aligner is not required just
to preserve the timings MediaForge was already receiving.
