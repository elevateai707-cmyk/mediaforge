# MediaForge MCP server

`server.py` is the canonical, versioned source for the stdio MediaForge MCP server.

The server calls the MediaForge HTTP API at `MEDIAFORGE_URL`. Its default is
`http://100.104.197.107:8420` because the current service binds its Tailscale interface.
Set `MEDIAFORGE_URL` to override it for another trusted host. If the server enables API
authentication, set `MF_API_TOKEN`; the bridge sends it only as an HTTP `Authorization: Bearer`
header. Do not put the token in `MEDIAFORGE_URL` or any URL. Direct media and caption download
URLs returned by tools require a browser session that is authorized with the same server.

Example MCP configuration uses placeholders only:

```json
{
  "mcpServers": {
    "mediaforge": {
      "command": "/path/to/python",
      "args": ["/path/to/mediaforge/mcp/server.py"],
      "env": {
        "MEDIAFORGE_URL": "https://mediaforge.example.invalid",
        "MF_API_TOKEN": "<MediaForge bearer token>"
      }
    }
  }
}
```

Use the same command, arguments, and environment mapping in Claude Code or Hermes's MCP
server configuration. Omit `MF_API_TOKEN` when the local server has authentication disabled.

Install dependencies with `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`.
Run the offline contract suite from this directory with
`/home/g15/mediaforge-mcp/.venv/bin/python -m unittest test_offline` (or use your own
venv with the requirements installed). It monkeypatches HTTP calls and never
contacts MediaForge or ElevenLabs.

The caption tool returns QC data and direct SRT/VTT/ASS download URLs. The handoff-package tool
returns a ZIP link only after it confirms the plan is approved; the ZIP includes plan metadata,
provenance, and captions, not source media. Story creation requires explicit per-job consent for
ElevenLabs script transmission/quota use and confirmation that the caller is permitted to use the
selected voice; it does not verify ownership.

`mf_render` leaves music disabled unless a server-side `music_path` is supplied or `music="auto"`
is explicitly requested. Asset caption responses include both rendered cues and the raw
`caption_words`/`words` timing data used for edits.

The supplied frontend lockfile currently retains the reported `source-map-js` high advisory to
avoid unapproved package changes. The targeted 1.2.2 patch has been tested as the known
remediation and can be applied when dependency-update approval is granted.
