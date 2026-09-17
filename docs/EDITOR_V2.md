# MediaForge editor v2

The editor keeps local planning and rendering independent of paid services. Originals remain untouched. Projects are versioned JSON documents; preview and final export use the same validated schema and renderer. The default server is **localhost only**, including provider routes and WebSocket connections. Run a single backend worker so local GPU scheduling and provider monitoring share one coordinator.

## Install and run

```bash
cd /home/bfam/mediaforge
backend/.venv/bin/pip install -r backend/requirements.txt
# Optional, only for Comfy Cloud:
backend/.venv/bin/pip install -r backend/requirements-cloud.txt
npm ci --prefix frontend
npm run build --prefix frontend
./scripts/run.sh
```

Open http://localhost:8420/studio. Stop an older running MediaForge process before restarting with the new version; the launch script does not replace an existing process. The unrelated `ignitemerch` application is not involved.

For remote use, keep the application bound to loopback and forward the port:

```bash
ssh -L 8420:127.0.0.1:8420 your-user@ubuntu-machine
```

Open `http://localhost:8420` on the client. Direct unauthenticated LAN access is deliberately rejected. Do not expose this app through a public reverse proxy. Keys are never returned by an API response or stored in browser local storage. Settings writes them to `data/provider-secrets.json` with owner-only permissions (0600). Environment keys take precedence over saved keys. Back up this file privately if needed; never commit it.

## Editing flow

1. Import a folder/file or select registered footage. City/trip filtering remains part of planning, including when sources are selected explicitly.
2. Describe the desired edit and generate the plan with the existing local Ollama/deterministic planner.
3. Review the clips. Trim, reorder with keyboard-accessible Move buttons, set speed, and choose cuts/crossfades. Crossfades are bounded to half the neighboring clip lengths, at most 0.3 seconds.
4. Adjust speech captions, on-video text, post copy, and audio. Save the project. The two prominent visibility switches save immediately and never transcribe or generate anything.
5. Generate a local rendered preview, then approve the current revision and render. Editing stays available after approval. A change invalidates approval; a running render continues using its own immutable snapshot. A preview from an older revision is not presented as the current preview.

The source-reference player is explicitly not the finished preview. Rendered previews use the final rendering engine at reduced resolution. Finished outputs are downloadable and retain their revision in job metadata. Interrupted renders can be restarted from their saved snapshot; FFmpeg does not continue from an arbitrary partially encoded frame.

### Three distinct text features

- **Speech captions:** source-speech or narration timings, editable independently of visibility. Original timings are mapped after trimming, order changes, speed adjustment and transition overlaps. Narration timings follow the narration start offset on the edited timeline.
- **On-video text:** titles, labels and CTAs, with per-overlay timing/style. Original `EditClip.caption` text is imported here and kept in project revisions. Asset and scene descriptions remain available for indexing/planning.
- **Post title, description, hashtags:** separate, collapsible fields with individual Copy and Copy all. Copies contain plain text only. Nothing is rendered unless explicitly converted into an overlay.

Speech captions off means no burned or embedded speech subtitles. SRT/VTT/ASS export is a separate, explicit checkbox. Burned-in text cannot be toggled off in a finished video. Select **Also export a clean master** to receive an additional output without speech captions or overlays.

## Local transcription

Profiles use faster-whisper's multilingual `base` (fast), `small` (balanced), and `large-v3` (higher accuracy, more resources). They are choices, not a claim that one model is universally best. Existing automatic indexing retains its configured `MF_WHISPER_MODEL` default, and now stores word timing. Previously indexed segment-only material can be retranscribed explicitly to obtain words.

The machine used for validation has an RTX 3080, 10,240 MiB VRAM. Models use int8 by default, release app-owned CLIP/Whisper instances between incompatible workloads, request unloading resident Ollama models, and serialize app-owned heavy workloads. Explicit transcription falls back to CPU if Ollama cannot be released or free VRAM is insufficient. CUDA runtime errors and OOM also fall back to CPU. Independently launched external GPU programs are not under this scheduler's control.

First use of a profile may download model weights. The CPU fallback is slower, particularly for large-v3. Language can be automatic or explicitly selected. VAD and low-confidence/no-speech filtering reduce unwanted music/silence transcripts; model output still needs review. The app never substitutes scene descriptions as dialogue. Empty/uncertain results are surfaced instead of fabricated captions.

Word timestamps feed phrase segmentation and the Active-word preset. This preset shows one active word at a time. Clean and Bold presets show readable phrases. Fonts, size, colour, outline, background, position, line count and safe margins are editable. Long phrases split into successive readable groups; unusually long words wrap. Manual text/timing correction clears stale word alignment for the affected cue. Individual word boundaries can be refined manually; a higher-quality retranscription provides fresh model alignment. There is no separate forced-alignment model installed by this change.

## Comfy Cloud connection

1. Create a Comfy API key through the official platform/account flow and save it in **Settings → Optional cloud accounts** (or set `COMFY_API_KEY`).
2. Install `backend/requirements-cloud.txt` and run the connection test. It checks Cloud node metadata and reports availability; it does not submit a workflow or invent a credit balance.
3. In Comfy Cloud, build/test a workflow available to your subscription and export **Workflow (API)** JSON.
4. Paste named workflow configurations into the settings JSON editor, embedding that exported graph and mapping only the inputs to expose. Example shape (replace the graph/node IDs with your actual tested workflow):

```json
{
  "My B-roll": {
    "workflow": {"6": {"class_type": "CLIPTextEncode", "inputs": {"text": "Original prompt", "clip": ["4", 1]}}, "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "YOUR_AVAILABLE_CHECKPOINT"}}},
    "mappings": {"prompt": {"node": "6", "input": "text", "type": "text"}}
  }
}
```

This illustrates the mapping structure, **not a runnable output-producing workflow**. Include the entire exported Cloud graph, including its output nodes. For a selected image/video input use `{"node":"YOUR_LOAD_NODE","input":"image","type":"asset"}` with the input name from that actual workflow. Unexposed settings remain the values in the exported graph. Only workflows you configure appear in the editor; generated B-roll, image-to-video and intros/outros depend on that workflow, available nodes/models and your account.

5. Select source assets and input text in Studio, review exactly which files will upload (local limit: 100 MB per selected file), then click **Generate using my subscription**. No automatic library uploads occur.
6. Preview completed outputs, download them, and explicitly add selected visual results to the sequence.

Execution uses pinned `comfy-sdk==0.3.0` and the current v2 job/asset API on `https://cloud.comfy.org`. The separate documented v1 `/api/object_info` endpoint is used only for Cloud node compatibility metadata with its `X-API-Key` authentication; v2 execution uses Bearer authentication through the SDK. Native/local ComfyUI execution and legacy `/prompt` submission are not used. The adapter refuses a `COMFY_BASE_URL` override to another service.

Sources: [Cloud deployment and subscription/API access](https://docs.comfy.org/development/deploy/cloud), [v2 API overview](https://docs.comfy.org/api-reference/v2/overview), [SDK guide](https://docs.comfy.org/development/api-development/sdks), [Cloud metadata API](https://docs.comfy.org/development/cloud/api-reference).

### Billing and recovery

Each request gets a durable local ID before a billable submission. Comfy receives that ID as the idempotency key. Its current v2 contract rejects duplicate key reuse rather than replaying a previous response. Consequently an uncertain timeout is **not automatically resubmitted**, including after 24 hours. Open Comfy history, identify the corresponding job, then attach its provider job ID to resume monitoring. This can require manual reconciliation when the original response was lost; the app does not guess which job is yours.

Known provider IDs resume polling after restart. Completed files are cached locally with request settings and provider provenance in SQLite. Cancellation is sent to Comfy when a remote ID exists; unavailable cancellation is explained. Failed/expired remote jobs retain their state. Expired downloads may no longer be recoverable from the provider. Definitive credential/credit/validation rejection can be reviewed and explicitly retried; unknown submissions cannot.

## ElevenLabs connection

Save `ELEVENLABS_API_KEY` in server environment or Settings. In Studio, load voices/models from your account, write a script, choose supported settings, and review either a short preview (first 250 characters) or full generation. Both require explicit billable confirmation. Usage counts are shown only when returned by the subscription endpoint. A monetary estimate is not invented.

Timestamped speech uses `/v1/text-to-speech/{voice_id}/with-timestamps`. Character alignment is validated and grouped into timed words/phrases. Generated audio is downloadable; choose **Use narration & timing** to add it to the project. If timing is absent, local transcription is used and its limitation is reported. Voice cloning is not implemented. Successful requests are cached by script, voice, model and settings; replaying them does not bill again. An ambiguous ElevenLabs timeout requires provider-history reconciliation and is never automatically regenerated.

Optional cloud transcription is a separate explicit action. Select one video and a Scribe model, review the upload, then generate. Account/model access is checked by the provider; the UI does not claim that every subscription supports every model. Local transcription remains usable without a key. There is no automatic paid fallback.

Sources: [Timestamped speech](https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps), [Speech-to-text](https://elevenlabs.io/docs/api-reference/speech-to-text/convert).

## Audio and rendering

Original audio, narration and music have separate enable/volume/fade controls. Added audio has a timeline start offset. Music loops to the edit length and can duck under the speech mix at a configurable ratio. Narration mismatch is shown with both durations: trim/extend the sequence, change the narration offset, or explicitly regenerate a revised script. Audio beyond the edit ends at the edit boundary; short narration leaves the remaining mix in place.

FFmpeg normalizes VFR input to the selected output frame rate, or the first source's detected rate when FPS is 0. Rotation is applied before layout; aspect ratio is preserved with padding. HDR requires supported zscale/tonemap conversion to SDR or the render fails clearly. Default export is high-quality H.264/AAC with software encoding; NVENC appears only after a successful encoder initialization check, with software fallback if unavailable. Scaling does not recover missing source detail.

Temporary partial MP4s are removed on failure/cancellation. Completed outputs are retained. Clean-master export is a second render; if interrupted, an already completed first output remains in the exports directory.

## Database migration and recovery

On first startup against an existing database, `init_db()` takes an online SQLite backup (including WAL data) at `data/mediaforge.db.pre-editor-v2.bak`, with mode 0600, **before** creating editor tables. New tables are additive: `project_documents`, `project_revisions`, and `provider_jobs`. Existing assets, scene descriptions, transcripts, plans and exports are retained. Project conversion is lazy when opening an old plan; visibility defaults off, and old clip descriptions migrate to overlays. No retranscription is needed for migration.

A saved project revision is appended to `project_revisions`; approval stores an immutable full document separately. Render jobs persist their source paths and approved document. Stale revisions return 409 rather than overwriting another tab. `/api/editor/schema` and `docs/project-v2.schema.json` describe the versioned format. Once migrated, legacy plan mutation/render endpoints direct callers to editor-v2 routes to prevent bypassing the approved project configuration.

Stop the application before restoring a backup. Preserve the current database and its WAL/SHM files together, then restore the backup under the configured database filename. Do not copy an old WAL file onto the restored database. An SQLite backup does not back up original media or generated output files; preserve those directories separately.

## NLE export fidelity

| Export | Preserved | Not represented by these existing exporters |
| --- | --- | --- |
| FCPXML | Source paths, trim/order and exporter-supported transitions | v2 styled overlays, speech captions, narration/music mix, speed retiming |
| CMX EDL | Source sequence and trim points within EDL limits | Styled text, multitrack audio mixing, v2 speed retiming |
| CapCut JSON | Existing source-sequence JSON export | Guaranteed current CapCut import compatibility or full effect round-trip |
| Resolve scripting | Existing timeline import when Resolve is available | v2 text/audio effects or guaranteed frame-identical round-trip |

Use a rendered master plus explicit SRT/VTT/ASS for features not carried by the NLE format. Export controls explain these limits; they do not claim complete round-trip fidelity.

## Verification and remaining boundaries

See `docs/EDITOR_V2_VALIDATION.md` for actual tests and sample-render results. Provider verification uses mocked HTTP contracts by default. No live Comfy/ElevenLabs credits were spent during implementation, and no subscription-specific workflow, credit amount or voice entitlement was verified live. Configure and test your chosen Cloud workflow in your account before relying on its output.

Large-v3 quality and multilingual accuracy are not benchmarked on your full library. Automatic forced alignment, browser-only real-time effect preview, automatic narration stretch, and voice cloning are not included. Local previews are background FFmpeg renders, not an interactive frame-by-frame NLE. Advanced edits are saved explicitly; the two visibility switches save immediately.
