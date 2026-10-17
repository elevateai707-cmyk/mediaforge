# MediaForge MCP server

`server.py` is the canonical, versioned source for the stdio MediaForge MCP server.
The active local installation is `/home/g15/mediaforge-mcp/server.py`; keep that external
copy synchronized after every server change, then restart MCP clients so they load it.

The server calls the MediaForge HTTP API at `MEDIAFORGE_URL`. Its default is
`http://100.104.197.107:8420` because the current service binds its Tailscale interface.
Set `MEDIAFORGE_URL` to override it for another trusted host. The API and MCP bridge provide
no authentication, so only use an address inside the same trusted-host boundary.

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
