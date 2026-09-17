# Editor v2 validation — 2026-09-16

Tests ran on Ubuntu with the repository's Python 3.12 virtual environment, system FFmpeg/libass and an RTX 3080 with 10 GB VRAM. Tests use isolated databases and synthetic media; the user's production database and original library were not migrated or changed during verification.

## Automated checks

- Full backend suite: **149 passed**, including the atomic-backup regression. The editor/provider targeted suite also passed independently (25 tests). Existing geography, deterministic planning, NLE exports, CUDA fallback and subprocess environment tests remain passing.
- Frontend TypeScript and production Vite build: passed. Existing large-bundle warning remains.
- Frontend lint: no errors; two existing Fast Refresh warnings in `ui/button.tsx` and `ui/badge.tsx`.
- Python undefined-name checks: passed.
- Playwright on isolated port 8421: all four switch combinations survive reload; preview generation, approval, final render, editing after approval, mobile 390px layout without horizontal overflow, Settings, and no uncaught browser exceptions. Same-origin WebSocket accepted. Plain-text Copy all, Unicode and saved post fields were verified after reload.

Backend coverage includes revision conflicts and approval invalidation, immutable snapshots, protection against legacy render bypass, subtitle trim/order/speed/crossfade mapping, Unicode/apostrophes/ASS injection escaping, long text, silence and missing audio, render cancellation cleanup, local-only origin/path validation, atomic pre-migration database backup, provider missing credentials, 401/402/403/429 responses, timeout ambiguity, explicit retry after definitive rejection, caching, daily job-count limit, persisted remote monitoring and cancellation.

Comfy tests exercise the installed **comfy-sdk 0.3.0** against mocked HTTP transport, including v2 authentication and submission idempotency. Cloud node metadata uses its separately documented v1 header. ElevenLabs mocks exercise the timestamped audio request and response. No live provider generation, subscription credits, workflow compatibility within the user's account, or voice entitlement was tested. No credits were spent.

## Render evidence

Run the reproducible offline sample generator:

```bash
PYTHONPATH=backend backend/.venv/bin/python scripts/verify_editor_render.py
```

It creates a unique directory under `exports/editor-v2-samples`, with four MP4s, PNG frame extracts and a manifest. It uses synthetic tone/video and explicitly authored fixture text, not inferred dialogue. The reviewed run is `exports/editor-v2-samples/894a509693`.

All four frames were visually inspected: clean has no text; speech-only has bottom captions; overlay-only has the top title; both has both. Automated decoded-frame comparisons confirm differences. FFprobe verifies duration, audio presence and no embedded subtitle stream. Additional real renders cover reordered/speed-adjusted clips, bounded crossfades, silent sources, photos, portrait/square output, original-audio mute, voice/music mixing, ducking/fades, clean master and explicit subtitle sidecars.

A separate local CPU run used cached faster-whisper `base` with English and an FFmpeg/flite synthesized spoken passage. It returned the expected sentence and 15 word timestamps over approximately 6.38 seconds. No external Ollama model was interrupted for this check. This is a synchronization smoke test, not an accuracy benchmark on real field recordings.

## Boundaries

- Providers are implemented and mock-tested, but require keys and a compatible account workflow for live verification.
- Model quality, multilingual accuracy, HDR tone-mapping appearance, phone rotation/VFR edge cases and NVENC performance were not exhaustively benchmarked across the user's library.
- Active-word style displays one word at a time. Forced alignment is not separately installed; word timing can be corrected manually or refreshed by explicit transcription.
- Preview is an asynchronous FFmpeg render, not a live frame-accurate NLE canvas.
- Existing NLE exporters do not carry every v2 styled-text, speed and audio-mix feature; see the fidelity table in `EDITOR_V2.md`.
- Daily job counts are local usage controls, not a guaranteed monetary ceiling. Ambiguous paid submissions are never automatically repeated.
- No voice cloning, automatic narration time-stretch, authenticated public hosting, or deployment to unrelated services was added.
