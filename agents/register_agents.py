#!/usr/bin/env python3
"""Register MediaForge (and PhotoCraft) with Claude Code, Codex and Hermes Agent.

Cross-platform (Ubuntu + Windows), stdlib only, idempotent. Called by
scripts/agents-setup.sh and scripts/agents-setup.ps1; safe to run by hand:

    python3 agents/register_agents.py --url http://127.0.0.1:8420
    python3 agents/register_agents.py --url http://192.168.1.50:8420 \
        --photocraft-cli ~/.local/bin/photocraft-cli \
        --pc-read-root ~/Pictures --pc-write-root ~/Pictures/PhotoCraft-Agent

What it does, per agent that is installed (or whose config dir exists):

* Claude Code: ``claude mcp add --scope user`` for ``mediaforge`` (+ ``photocraft``),
  plus ``--scope local`` in this repo on Windows (the committed .mcp.json says
  ``python3``, which Windows usually lacks); skill to ~/.claude/skills/.
* Codex: a managed ``[mcp_servers.*]`` block in ~/.codex/config.toml; skill to
  ~/.codex/skills/.
* Hermes Agent: managed entries under ``mcp_servers:`` in ~/.hermes/config.yaml;
  skill to ~/.hermes/skills/. (Hermes runs on Linux/macOS/WSL2, not native Windows.)

Managed blocks sit between ``>>> mediaforge`` / ``<<< mediaforge`` markers and are
rewritten on every run; anything else in those files is left alone.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MCP_SCRIPT = ROOT / "agents" / "mediaforge_mcp.py"
SKILL = ROOT / ".claude" / "skills" / "mediaforge" / "SKILL.md"
# Managed-block name: "mediaforge" normally, "evstudio" for --studio-only, so
# registering the opt-in Studio server never drops the others (and vice versa).
BLOCK = "mediaforge"


def begin() -> str:
    return f"# >>> {BLOCK} (managed by agents/register_agents.py; edits here are overwritten) >>>"


def end() -> str:
    return f"# <<< {BLOCK} <<<"
IS_WINDOWS = os.name == "nt"


def say(mark: str, msg: str) -> None:
    print(f"  {mark} {msg}")


# ---------------------------------------------------------------------------
# server definitions
# ---------------------------------------------------------------------------
def servers(a: argparse.Namespace) -> dict[str, dict]:
    env = {"MEDIAFORGE_URL": a.url}
    if a.can_approve:
        env["MEDIAFORGE_AGENT_CAN_APPROVE"] = "1"
    out = {"mediaforge": {"command": a.python, "args": [str(MCP_SCRIPT)], "env": env}}
    if a.photocraft_cli:
        args = ["mcp"]
        if a.pc_read_root:
            args += ["--automation-read-root", a.pc_read_root]
        if a.pc_write_root:
            Path(a.pc_write_root).mkdir(parents=True, exist_ok=True)
            args += ["--automation-write-root", a.pc_write_root]
        out["photocraft"] = {"command": a.photocraft_cli, "args": args, "env": {}}
    if a.studio_cli:
        # Opt-in only: FilmCraft's MCP server has no folder sandbox. Agents get
        # sandboxed Studio builds through mediaforge's studio_build tool anyway.
        out["evstudio"] = {"command": a.studio_cli, "args": ["mcp"], "env": {}}
    if a.studio_only:
        out = {k: v for k, v in out.items() if k == "evstudio"}
    return out


def install_skill(dest_root: Path, dry: bool) -> None:
    dest = dest_root / "mediaforge" / "SKILL.md"
    if dry:
        say("→", f"would copy skill to {dest}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SKILL, dest)
    say("✓", f"skill → {dest}")


def strip_managed(text: str) -> str:
    return re.sub(rf"[ \t]*{re.escape(begin())}.*?{re.escape(end())}[ \t]*\n?", "", text, flags=re.S)


def write(path: Path, text: str, dry: bool) -> None:
    if dry:
        say("→", f"would write {path}:\n{text}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copyfile(path, path.with_suffix(path.suffix + ".bak"))
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Claude Code
# ---------------------------------------------------------------------------
def claude(a: argparse.Namespace, defs: dict[str, dict]) -> None:
    exe = shutil.which("claude")
    if exe is None:
        say("!", "Claude Code CLI not found; skipped (install: npm i -g @anthropic-ai/claude-code)")
        return
    scopes = [("user", None)]
    if IS_WINDOWS:
        scopes.append(("local", str(ROOT)))
    for name, d in defs.items():
        for scope, cwd in scopes:
            def run(*args: str) -> subprocess.CompletedProcess:
                return subprocess.run([exe, "mcp", *args], cwd=cwd, capture_output=True, text=True)
            if a.dry_run:
                say("→", f"would register {name} ({scope}) in Claude Code")
                continue
            run("remove", "--scope", scope, name)  # idempotent re-register
            spec = {"type": "stdio", "command": d["command"], "args": d["args"], "env": d["env"]}
            r = run("add-json", "--scope", scope, name, json.dumps(spec))
            if r.returncode == 0:
                say("✓", f"Claude Code: {name} ({scope} scope)")
            else:
                say("✗", f"Claude Code: {name} failed: {(r.stderr or r.stdout).strip()}")
    install_skill(Path.home() / ".claude" / "skills", a.dry_run)


# ---------------------------------------------------------------------------
# Codex (~/.codex/config.toml)
# ---------------------------------------------------------------------------
def codex(a: argparse.Namespace, defs: dict[str, dict]) -> None:
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    if shutil.which("codex") is None and not home.exists():
        say("!", "Codex not found; skipped (install: npm i -g @openai/codex)")
        return
    cfg = home / "config.toml"
    text = strip_managed(cfg.read_text(encoding="utf-8")) if cfg.exists() else ""
    lines = [begin()]
    for name, d in defs.items():
        if re.search(rf"^\[mcp_servers\.{name}\]", text, flags=re.M):
            say("!", f"Codex: [mcp_servers.{name}] already defined by you in {cfg}; left as is")
            continue
        # JSON string escapes are valid TOML basic strings (Windows paths included).
        lines += [f"[mcp_servers.{name}]",
                  f"command = {json.dumps(d['command'])}",
                  f"args = [{', '.join(json.dumps(x) for x in d['args'])}]"]
        if d["env"]:
            lines.append("env = { " + ", ".join(f"{json.dumps(k)} = {json.dumps(v)}"
                                                for k, v in d["env"].items()) + " }")
        lines += ["startup_timeout_sec = 20", "tool_timeout_sec = 900", ""]
    lines.append(end())
    write(cfg, text.rstrip() + "\n\n" + "\n".join(lines) + "\n", a.dry_run)
    if not a.dry_run:
        say("✓", f"Codex: {', '.join(defs)} → {cfg}")
    install_skill(home / "skills", a.dry_run)


# ---------------------------------------------------------------------------
# Hermes Agent (~/.hermes/config.yaml)
# ---------------------------------------------------------------------------
def hermes(a: argparse.Namespace, defs: dict[str, dict]) -> None:
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    if shutil.which("hermes") is None and not home.exists():
        hint = " (on Windows, run Hermes inside WSL2 and run agents-setup.sh there)" if IS_WINDOWS else ""
        say("!", f"Hermes Agent not found; skipped{hint}")
        return
    cfg = home / "config.yaml"
    text = strip_managed(cfg.read_text(encoding="utf-8")) if cfg.exists() else ""
    block = ["  " + begin()]
    for name, d in defs.items():
        if re.search(rf"^  {name}:\s*$", text, flags=re.M):
            say("!", f"Hermes: mcp_servers.{name} already defined by you in {cfg}; left as is")
            continue
        block += [f"  {name}:",
                  f"    command: {json.dumps(d['command'])}",
                  f"    args: [{', '.join(json.dumps(x) for x in d['args'])}]",
                  "    env: {" + ", ".join(f"{json.dumps(k)}: {json.dumps(v)}" for k, v in d["env"].items()) + "}",
                  "    timeout: 900"]
    block.append("  " + end())
    body = "\n".join(block)
    if re.search(r"^mcp_servers:[ \t]*\{[ \t]*\}[ \t]*$", text, flags=re.M):
        text = re.sub(r"^mcp_servers:[ \t]*\{[ \t]*\}[ \t]*$", "mcp_servers:", text, flags=re.M)
    if re.search(r"^mcp_servers:[ \t]*$", text, flags=re.M):
        text = re.sub(r"^mcp_servers:[ \t]*$", lambda m: "mcp_servers:\n" + body, text, count=1, flags=re.M)
    else:
        text = text.rstrip() + ("\n\n" if text.strip() else "") + "mcp_servers:\n" + body + "\n"
    write(cfg, text.rstrip() + "\n", a.dry_run)
    if not a.dry_run:
        say("✓", f"Hermes: {', '.join(defs)} → {cfg}")
    install_skill(home / "skills", a.dry_run)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--url", default=os.environ.get("MEDIAFORGE_URL", "http://127.0.0.1:8420"),
                   help="MediaForge backend URL (another machine on the LAN is fine)")
    p.add_argument("--python", default=sys.executable,
                   help="interpreter the agents should launch the MCP server with")
    p.add_argument("--photocraft-cli", help="path to photocraft-cli; registers the photocraft MCP server")
    p.add_argument("--pc-read-root", help="folder the photocraft MCP server may read")
    p.add_argument("--pc-write-root", help="folder the photocraft MCP server may write")
    p.add_argument("--studio-cli", help="evstudio-cli path: ALSO register its raw MCP server (not sandboxed)")
    p.add_argument("--studio-only", action="store_true", help="register only the evstudio server")
    p.add_argument("--agents", default="claude,codex,hermes")
    p.add_argument("--can-approve", action="store_true",
                   help="let agents approve edit plans themselves (default: human approves)")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    for attr in ("pc_read_root", "pc_write_root", "photocraft_cli", "studio_cli"):
        v = getattr(a, attr)
        if v:
            setattr(a, attr, str(Path(v).expanduser().resolve()))
    global BLOCK
    if a.studio_only:
        BLOCK = "evstudio"
    defs = servers(a)
    wanted = {x.strip() for x in a.agents.split(",") if x.strip()}
    for name, fn in (("claude", claude), ("codex", codex), ("hermes", hermes)):
        if name in wanted:
            fn(a, defs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
