# Local MediaForge -> Resolve editing

## Current edition boundary
Resolve Free is installed. External `scriptapp('Resolve')` returned None while Resolve was running. The installed Blackmagic scripting README describes the external API as Resolve Studio. This integration therefore provides a durable Free handoff plus a direct Studio/API path. It does not claim unattended Free control.

## Free workflow
1. Keep original footage in Rec.709. Scan it with MediaForge; select its asset IDs.
2. MCP `plan_edit(asset_ids, intent, fps, ratio)` uses local Ollama only (no cloud planner). It returns an editable revision and truthfully labels deterministic fallback if the local model fails.
3. Inspect or change trims via `get_edit` / `update_edit`. `preflight_edit` checks source files, timing and LUT ID. The path is cuts, original sound and one creative LUT; captions, mixing, retiming and transitions are rejected rather than silently dropped.
4. `queue_free_edit(plan_id, revision, lut_id, render_preset?)` saves a durable, immutable request. Choose a LUT from `list_luts`. This is not yet an edited timeline.
5. In Resolve, run **Workspace > Scripts > Utility > MediaForge Apply Edit**. Restart Resolve if the newly installed script does not appear. If this menu is unavailable, open Workspace > Console, select Python 3 and run:
   `exec(open('/home/bfam/mediaforge/scripts/resolve_free.py').read())`
6. `free_edit_status(queue_id)` reads the result: `awaiting_resolve`, `processing_or_interrupted`, `timeline_ready`, `rendering`, `completed`, or `error`. If a render was requested, rerun the utility after render finishes to update its status. Do not blindly retry a `processing_or_interrupted` request; inspect the partial project first.

The in-app execution step must be verified on the installed Free edition. If scripting is unavailable there, Studio or a separately tested UI automation path is required. MediaForge does not pretend to control an unavailable API.

## Studio / external API path
Enable external scripting Local in Resolve. `resolve_status` must report `connected:true`. Then use `export_edit` and `render_edit` with an existing render preset; `render_status` verifies job state and nonempty output files. No online publishing. Each export creates a new named project; it never falls back to modifying the current project. Native source import may still fail for codecs unsupported by the installed Linux edition. No automatic transcode is performed.

## Colour and source contract
All generated LUTs expect SDR Rec.709 display-referred input. This direct workflow rejects detected HDR/P3/Log and variable/unknown frame rates. Missing tags cannot identify Log reliably: callers must know footage is Rec.709. No automatic normalization, proxy, or conversion is inserted. Resolve is set to unmanaged DaVinci YRGB, Rec709 Gamma 2.4 output. LUT application is checked, and source-frame trims use each file's frame rate, with end-exclusive seconds converted to inclusive end frames.

## MCP
Standalone stdio server: `scripts/resolve_mcp.py`. Uses MCP SDK 1.x in its own environment. It only calls http://127.0.0.1:8420, and MediaForge keeps its existing loopback Host/Origin protections. Tools: resolve_status, list_luts, list_assets, scan_media, job_status, plan_edit, get_edit, update_edit, preflight_edit, export_edit, render_edit, render_status, queue_free_edit, free_edit_status.

Example client configuration (substitute your actual isolated Python path):
```json
{"mcpServers":{"mediaforge-resolve":{"command":"/home/bfam/mediaforge/.venv-mcp/bin/python","args":["/home/bfam/mediaforge/scripts/resolve_mcp.py"]}}}
```

Keep original footage. The scene planner selects from already indexed MediaForge scenes and metadata; it is not a sports-specific action recognition model. Native edit/render success remains unverified until the in-Resolve utility or external API completes an actual test. Synthetic checks do not prove codec support for every iPhone setting.
