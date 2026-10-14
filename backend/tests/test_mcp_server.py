"""agents/mediaforge_mcp.py: MCP protocol, tool wiring, and the approval gate.

The server's HTTP layer is routed through FastAPI's TestClient so every tool
hits the real backend endpoints.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

MCP_PATH = Path(__file__).resolve().parents[2] / "agents" / "mediaforge_mcp.py"


def _load():
    spec = importlib.util.spec_from_file_location("mediaforge_mcp", MCP_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mcp(client, monkeypatch):
    mod = _load()

    def http(method, path, body=None, query=None, raw=False, timeout=None):
        q = {k: v for k, v in (query or {}).items() if v is not None and v != ""}
        r = client.request(method, path, json=body, params=q)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            raise mod.ToolError(f"MediaForge {method} {path} -> HTTP {r.status_code}: {detail}")
        if raw:
            return r.content, r.headers.get("content-type", "")
        return r.json() if r.content else None

    monkeypatch.setattr(mod, "http", http)
    return mod


def rpc(mod, method, params=None, mid=1):
    return mod.handle({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})


def call(mod, name, **arguments):
    res = rpc(mod, "tools/call", {"name": name, "arguments": arguments})["result"]
    return res, (json.loads(res["content"][0]["text"]) if res["content"][0]["type"] == "text"
                 and not res.get("isError") else None)


def test_initialize_and_list(mcp):
    init = rpc(mcp, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "t", "version": "0"}})["result"]
    assert init["protocolVersion"] == "2025-06-18"
    assert init["serverInfo"]["name"] == "mediaforge"
    assert "tools" in init["capabilities"]
    # Unknown client version -> server's preferred one.
    assert rpc(mcp, "initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] \
        == mcp.PROTOCOL_VERSIONS[0]
    assert mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None

    tools = rpc(mcp, "tools/list")["result"]["tools"]
    names = [t["name"] for t in tools]
    assert len(names) == len(set(names))
    for t in tools:
        schema = t["inputSchema"]
        assert schema["type"] == "object" and schema["additionalProperties"] is False
        assert set(schema["required"]) <= set(schema["properties"])
        assert t["description"] and set(t["annotations"]) == {
            "readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"}
    for must in ("mf_status", "mf_search", "mf_plan_edit", "mf_render", "pc_apply", "mf_look"):
        assert must in names

    assert rpc(mcp, "nope")["error"]["code"] == -32601


def test_prompts(mcp):
    prompts = {p["name"] for p in rpc(mcp, "prompts/list")["result"]["prompts"]}
    assert prompts == {"highlight_reel", "photo_glowup", "library_report"}
    msg = rpc(mcp, "prompts/get", {"name": "highlight_reel", "arguments": {"subject": "Edmonton"}})
    text = msg["result"]["messages"][0]["content"]["text"]
    assert "Edmonton" in text and "30-second tiktok" in text
    assert rpc(mcp, "prompts/get", {"name": "x"})["error"]["code"] == -32602


def test_status_search_and_bad_args(mcp, client, wait_job, media_dir):
    import random

    from PIL import Image

    Image.new("RGB", (64, 48), tuple(random.randrange(256) for _ in range(3))).save(media_dir / "m.jpg")
    job = wait_job(client, client.post("/api/scan", json={"paths": [str(media_dir)]}).json()["job_id"])
    assert job["status"] == "done"

    res, data = call(mcp, "mf_status")
    assert not res.get("isError"), res
    assert data["health"]["status"] == "ok" and data["assets_indexed"] >= 1
    assert data["agent_can_approve"] is False

    res, data = call(mcp, "mf_list_assets", kind="photo", limit=200)
    assert data["items"] and "thumb_url" in data["items"][0]
    aid = next(a["id"] for a in data["items"] if a["path"].startswith(str(media_dir)))

    res = rpc(mcp, "tools/call", {"name": "mf_look", "arguments": {"asset_id": aid}})["result"]
    assert res["content"][0]["type"] == "image" and res["content"][0]["data"], res

    res, _ = call(mcp, "mf_search", query="x", bogus=1)
    assert res["isError"] and "bogus" in res["content"][0]["text"]
    res, _ = call(mcp, "does_not_exist")
    assert res["isError"]
    res, _ = call(mcp, "mf_view_export", output_url="/api/config")
    assert res["isError"]


def test_approval_gate_and_render_refusal(mcp, client, wait_job, jpeg_library, monkeypatch):
    wait_job(client, client.post("/api/scan", json={"paths": [str(jpeg_library)]}).json()["job_id"])
    res, plan = call(mcp, "mf_plan_edit", intent="travel montage 1:1")
    assert not res.get("isError"), res
    assert plan["status"] != "approved" and plan["review_url"].endswith("/studio")
    pid = plan["plan_id"]

    # Default: the agent cannot approve; it gets a hand-off instead.
    res, out = call(mcp, "mf_approve_plan", plan_id=pid)
    assert out["approved"] is False and "review_url" in out
    assert client.get(f"/api/edits/plan/{pid}").json()["status"] != "approved"

    # Rendering an unapproved plan is refused by the backend.
    res, _ = call(mcp, "mf_render", plan_id=pid, wait_s=0)
    assert res["isError"] and "409" in res["content"][0]["text"]

    # Owner opt-in: approval goes through.
    monkeypatch.setattr(mcp, "CAN_APPROVE", True)
    res, out = call(mcp, "mf_approve_plan", plan_id=pid)
    assert not res.get("isError"), res
    assert client.get(f"/api/edits/plan/{pid}").json()["status"] == "approved"


def test_pc_apply_requires_something(mcp):
    res, _ = call(mcp, "pc_apply", asset_id=1)
    assert res["isError"]


def test_stdio_smoke():
    """Real process over stdio, pointed at a closed port: protocol still works."""
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        "not json",
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "mf_status", "arguments": {}}},
    ]
    stdin = "\n".join(m if isinstance(m, str) else json.dumps(m) for m in msgs) + "\n"
    proc = subprocess.run(
        [sys.executable, "-I", str(MCP_PATH)], input=stdin, capture_output=True, text=True, timeout=30,
        env={"MEDIAFORGE_URL": "http://127.0.0.1:9", "MEDIAFORGE_TIMEOUT": "2", "PATH": ""},
    )
    replies = [json.loads(l) for l in proc.stdout.splitlines()]
    assert [r.get("id") for r in replies] == [1, None, 2, 3]
    assert replies[1]["error"]["code"] == -32700
    assert replies[2]["result"]["tools"]
    status = replies[3]["result"]
    assert status["isError"] and "cannot reach MediaForge" in status["content"][0]["text"]
