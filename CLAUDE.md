@AGENTS.md

## Claude Code specifics

- The `mediaforge` MCP server is registered for this project in `.mcp.json`; approve it
  when prompted. `/mcp` shows its status. Run `scripts/agents-setup.sh` once per machine
  to install `photocraft-cli` and also register the `photocraft` server.
- The `mediaforge` skill (`.claude/skills/mediaforge/`) loads automatically for media
  requests ("make a reel of…", "fix these photos…").
- MCP prompts appear as `/mcp__mediaforge__highlight_reel`, `/mcp__mediaforge__photo_glowup`,
  `/mcp__mediaforge__library_report`.
