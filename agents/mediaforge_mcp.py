#!/usr/bin/env python3
"""MediaForge MCP server: lets Claude Code, Codex and Hermes Agent drive MediaForge.

Pure standard library (Python 3.10+), so it runs unchanged on Ubuntu and on a
Windows desktop with no pip install. It speaks MCP over stdio (newline-delimited
JSON-RPC 2.0) and talks to a MediaForge backend over HTTP, which may be this
machine or another one on the LAN:

    MEDIAFORGE_URL              default http://127.0.0.1:8420
    MEDIAFORGE_AGENT_CAN_APPROVE  "1" lets the agent approve edit plans itself.
                                Default off: approval is the human gate before
                                anything renders, so the agent hands the plan
                                to you with a link instead.
    MEDIAFORGE_TIMEOUT          per-request seconds (default 60)

Register it with an agent (scripts/agents-setup.sh / .ps1 do this for you):

    claude mcp add mediaforge -- python3 agents/mediaforge_mcp.py
    codex mcp add mediaforge -- python3 /abs/path/agents/mediaforge_mcp.py
    # Hermes: ~/.hermes/config.yaml -> mcp_servers: {mediaforge: {command: python3, args: [...]}}

Every tool is non-destructive: MediaForge never modifies originals. Renders,
exports and PhotoCraft edits write new files under the backend's exports/.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

SERVER_NAME = "mediaforge"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

BASE_URL = os.environ.get("MEDIAFORGE_URL", "http://127.0.0.1:8420").rstrip("/")
CAN_APPROVE = os.environ.get("MEDIAFORGE_AGENT_CAN_APPROVE", "0") == "1"
TIMEOUT = float(os.environ.get("MEDIAFORGE_TIMEOUT", "60"))
MAX_IMAGE_BYTES = 1_500_000

RATIO_SIZES = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}


class ToolError(Exception):
    """Reported to the agent as an isError tool result."""


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _url(path: str, query: dict[str, Any] | None = None) -> str:
    q = {k: v for k, v in (query or {}).items() if v is not None and v != ""}
    return BASE_URL + path + (("?" + urllib.parse.urlencode(q)) if q else "")


def http(method: str, path: str, body: Any = None, query: dict[str, Any] | None = None,
         raw: bool = False, timeout: float | None = None) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(_url(path, query), data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as resp:
            payload = resp.read()
            if raw:
                return payload, resp.headers.get("Content-Type", "")
            return json.loads(payload) if payload else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        try:
            detail = json.loads(detail).get("detail", detail)
        except (ValueError, AttributeError):
            pass
        raise ToolError(f"MediaForge {method} {path} -> HTTP {exc.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise ToolError(
            f"cannot reach MediaForge at {BASE_URL} ({exc}). Start it with ./scripts/run.sh, "
            "or set MEDIAFORGE_URL to the machine running it."
        ) from None


# Trim asset rows so search results don't flood the agent's context.
_ASSET_KEYS = ("id", "kind", "path", "taken_at", "duration", "width", "height", "caption",
               "aesthetic_score", "city", "country", "faces", "tags", "gps_lat", "gps_lon")


def _slim(asset: dict[str, Any]) -> dict[str, Any]:
    out = {k: asset[k] for k in _ASSET_KEYS if asset.get(k) not in (None, [], "")}
    out["thumb_url"] = f"{BASE_URL}/api/assets/{asset['id']}/thumb"
    return out


def _ui(path: str = "") -> str:
    return f"{BASE_URL}/{path.lstrip('/')}"


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------
TOOLS: dict[str, dict[str, Any]] = {}


def tool(name: str, description: str, props: dict[str, Any] | None = None,
         required: tuple[str, ...] = (), read_only: bool = True, title: str = ""):
    def deco(fn: Callable[..., Any]):
        TOOLS[name] = {
            "fn": fn,
            "spec": {
                "name": name,
                "title": title or name,
                "description": description,
                "inputSchema": {"type": "object", "properties": props or {},
                                "required": list(required), "additionalProperties": False},
                "annotations": {"readOnlyHint": read_only, "destructiveHint": False,
                                "idempotentHint": read_only, "openWorldHint": False},
            },
        }
        return fn
    return deco


def _wait_job(job_id: str, timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + max(1.0, min(timeout_s, 1800.0))
    job = http("GET", f"/api/jobs/{job_id}")
    while job["status"] not in ("done", "error", "cancelled") and time.monotonic() < deadline:
        time.sleep(1.0)
        job = http("GET", f"/api/jobs/{job_id}")
    return job


@tool("mf_status", "Health check: GPU, AI models, media folders, and whether PhotoCraft is "
      "installed. Call this first in a session.", title="MediaForge status")
def mf_status() -> Any:
    health = http("GET", "/api/health")
    cfg = http("GET", "/api/config")
    try:
        pc = http("GET", "/api/photocraft/status")
    except ToolError as exc:
        pc = {"available": False, "error": str(exc)}
    try:
        studio = http("GET", "/api/filmcraft/status")
    except ToolError as exc:
        studio = {"available": False, "error": str(exc)}
    total = http("GET", "/api/assets", query={"limit": 1})["total"]
    return {"url": BASE_URL, "health": health, "config": cfg, "photocraft": pc,
            "studio": studio, "assets_indexed": total, "agent_can_approve": CAN_APPROVE}


@tool("mf_search", "Semantic + transcript + caption search over the indexed library "
      "(e.g. 'sunset on the beach', 'someone saying happy birthday'). Returns slim asset rows.",
      {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}},
      ("query",), title="Search media")
def mf_search(query: str, limit: int = 20) -> Any:
    res = http("GET", "/api/search", query={"q": query, "limit": limit})
    return {"query": query, "took_ms": res.get("took_ms"), "results": [_slim(a) for a in res["results"]]}


@tool("mf_list_assets", "Filter the library by kind, face name, city, trip, tag, date range or GPS "
      "radius. Use for structured questions ('all videos from the Edmonton trip').",
      {"kind": {"type": "string", "enum": ["photo", "video"]}, "face": {"type": "string"},
       "city": {"type": "string"}, "trip_id": {"type": "integer"}, "tag": {"type": "string"},
       "date_from": {"type": "string", "description": "ISO date"}, "date_to": {"type": "string"},
       "lat": {"type": "number"}, "lon": {"type": "number"}, "radius_km": {"type": "number"},
       "sort": {"type": "string", "enum": ["added", "taken_at", "aesthetic"], "default": "added"},
       "order": {"type": "string", "enum": ["desc", "asc"], "default": "desc"},
       "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
       "offset": {"type": "integer", "minimum": 0, "default": 0}}, title="List assets")
def mf_list_assets(**kw: Any) -> Any:
    res = http("GET", "/api/assets", query=kw)
    return {"total": res["total"], "items": [_slim(a) for a in res["items"]]}


@tool("mf_asset", "Full detail for one asset; optionally its transcript and detected scenes.",
      {"asset_id": {"type": "integer"}, "transcript": {"type": "boolean", "default": False},
       "scenes": {"type": "boolean", "default": False}}, ("asset_id",), title="Asset detail")
def mf_asset(asset_id: int, transcript: bool = False, scenes: bool = False) -> Any:
    out = {"asset": http("GET", f"/api/assets/{asset_id}")}
    if transcript:
        out["transcript"] = http("GET", f"/api/assets/{asset_id}/transcript")["segments"]
    if scenes:
        out["scenes"] = http("GET", f"/api/assets/{asset_id}/scenes")["scenes"]
    return out


@tool("mf_look", "See an asset: returns its thumbnail as an image so you can judge composition, "
      "exposure and colour before choosing an edit.",
      {"asset_id": {"type": "integer"}}, ("asset_id",), title="Look at asset")
def mf_look(asset_id: int) -> Any:
    data, ctype = http("GET", f"/api/assets/{asset_id}/thumb", raw=True)
    return _image_result(data, ctype or "image/jpeg", f"asset {asset_id} thumbnail")


@tool("mf_view_export", "See a file MediaForge produced (a PhotoCraft edit or touch-up), by the "
      "output_url a tool returned. Use it to compare before/after.",
      {"output_url": {"type": "string", "description": "e.g. /exports/photocraft/12_punchy_ab12.jpg"}},
      ("output_url",), title="View export")
def mf_view_export(output_url: str) -> Any:
    path = urllib.parse.urlparse(output_url).path
    if not path.startswith("/exports/") or ".." in path:
        raise ToolError("output_url must be a /exports/... path returned by a MediaForge tool")
    data, ctype = http("GET", path, raw=True)
    if len(data) > MAX_IMAGE_BYTES:
        return {"text": f"{len(data) // 1024} KB is too large to inline; open {_ui(path)}"}
    return _image_result(data, ctype or "image/jpeg", path)


@tool("mf_trips", "Trips auto-clustered from GPS + time (city, dates, asset counts).", title="Trips")
def mf_trips() -> Any:
    return http("GET", "/api/trips")


@tool("mf_places", "Cities/countries in the library with asset counts.", title="Places")
def mf_places() -> Any:
    return http("GET", "/api/places")


@tool("mf_faces", "Face clusters (named and unnamed) with counts.", title="Faces")
def mf_faces() -> Any:
    return http("GET", "/api/faces")


@tool("mf_plan_edit", "Draft an edit plan (clip list, transitions, captions, duration, aspect) from "
      "plain English, e.g. '45s upbeat TikTok reel of Vancouver with the kids, 9:16'. Optionally "
      "restrict to one trip. The plan starts unapproved.",
      {"intent": {"type": "string"}, "trip_id": {"type": "integer"}}, ("intent",),
      read_only=False, title="Plan an edit")
def mf_plan_edit(intent: str, trip_id: int | None = None) -> Any:
    plan = http("POST", "/api/edits/plan", {"intent": intent, "trip_id": trip_id, "auto_approve": False})
    plan["review_url"] = _ui("studio")
    return plan


@tool("mf_trip_reel", "One-shot highlight-reel plan for a trip (defaults to a 9:16 TikTok reel).",
      {"trip_id": {"type": "integer"}, "intent": {"type": "string"}}, ("trip_id",),
      read_only=False, title="Trip reel")
def mf_trip_reel(trip_id: int, intent: str | None = None) -> Any:
    return http("POST", f"/api/trips/{trip_id}/reel", {"intent": intent})


@tool("mf_get_plan", "Fetch an edit plan by id.", {"plan_id": {"type": "string"}}, ("plan_id",),
      title="Get plan")
def mf_get_plan(plan_id: str) -> Any:
    return http("GET", f"/api/edits/plan/{plan_id}")


@tool("mf_list_plans", "Recent edit plans and their status.",
      {"limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20}}, title="List plans")
def mf_list_plans(limit: int = 20) -> Any:
    return http("GET", "/api/edits/plans", query={"limit": limit})


@tool("mf_update_plan", "Replace a plan's clip list (reorder, trim start/end, swap clips, change "
      "captions/transitions). Editing resets nothing else; approval is still required to render.",
      {"plan_id": {"type": "string"},
       "clips": {"type": "array", "items": {"type": "object", "properties": {
           "asset_id": {"type": "integer"}, "scene_id": {"type": ["integer", "null"]},
           "start": {"type": "number"}, "end": {"type": "number"},
           "caption": {"type": ["string", "null"]},
           "transition": {"type": "string", "enum": ["crossfade", "fade"]},
           "score": {"type": "number"}}, "required": ["asset_id", "start", "end"]}}},
      ("plan_id", "clips"), read_only=False, title="Update plan")
def mf_update_plan(plan_id: str, clips: list[dict[str, Any]]) -> Any:
    return http("PUT", f"/api/edits/plan/{plan_id}", {"clips": clips})


@tool("mf_approve_plan", "Approve a plan so it can render. Approval is the human gate: unless the "
      "owner enabled MEDIAFORGE_AGENT_CAN_APPROVE=1, this returns a review link to hand to the user "
      "instead of approving.",
      {"plan_id": {"type": "string"}}, ("plan_id",), read_only=False, title="Approve plan")
def mf_approve_plan(plan_id: str) -> Any:
    if not CAN_APPROVE:
        plan = http("GET", f"/api/edits/plan/{plan_id}")
        return {"approved": False, "plan_id": plan_id, "status": plan["status"],
                "action_required": "Ask the user to review and approve this plan in Edit Studio, "
                                   "then call mf_render.",
                "review_url": _ui("studio")}
    return http("POST", f"/api/edits/plan/{plan_id}/approve")


@tool("mf_render", "Render an APPROVED plan to MP4 with FFmpeg (crossfades, loudnorm, ratio crop, "
      "burned captions). Waits up to wait_s seconds and returns the output path.",
      {"plan_id": {"type": "string"}, "ratio": {"type": "string", "enum": list(RATIO_SIZES), "default": "9:16"},
       "captions": {"type": "boolean", "default": True}, "music_path": {"type": "string"},
       "wait_s": {"type": "integer", "minimum": 0, "maximum": 1800, "default": 600}},
      ("plan_id",), read_only=False, title="Render")
def mf_render(plan_id: str, ratio: str = "9:16", captions: bool = True,
              music_path: str | None = None, wait_s: int = 600) -> Any:
    w, h = RATIO_SIZES[ratio]
    job = http("POST", "/api/render", {"plan_id": plan_id, "ratio": ratio, "captions": captions,
                                       "music_path": music_path, "width": w, "height": h})
    if wait_s <= 0:
        return job
    _wait_job(job["job_id"], wait_s)
    return http("GET", f"/api/render/{job['job_id']}") | {"job_id": job["job_id"]}


@tool("mf_export", "Export a plan to an NLE: DaVinci Resolve (live timeline), FCPXML 1.10, CMX3600 "
      "EDL, or CapCut draft (experimental).",
      {"plan_id": {"type": "string"},
       "target": {"type": "string", "enum": ["resolve", "fcpxml", "edl", "capcut"]}},
      ("plan_id", "target"), read_only=False, title="Export to NLE")
def mf_export(plan_id: str, target: str) -> Any:
    return http("POST", f"/api/export/{target}", {"plan_id": plan_id})


@tool("studio_build", "Turn an edit plan into an editable Elevate Vision Studio / FilmCraft project "
      "(.fcproj: clips trimmed and placed, frame-filled to the ratio, cross dissolves, styled "
      "captions) so the user can finish it in a real editor. render=true also exports H.264 MP4 "
      "and needs an APPROVED plan. Prefer this over mf_export when the user wants to keep editing.",
      {"plan_id": {"type": "string"},
       "ratio": {"type": "string", "enum": list(RATIO_SIZES), "description": "default: the plan's ratio"},
       "captions": {"type": "boolean", "default": True},
       "render": {"type": "boolean", "default": False},
       "wait_s": {"type": "integer", "minimum": 0, "maximum": 3600, "default": 900}},
      ("plan_id",), read_only=False, title="Build Studio project")
def studio_build(plan_id: str, ratio: str | None = None, captions: bool = True,
                 render: bool = False, wait_s: int = 900) -> Any:
    res = http("POST", "/api/export/filmcraft", {"plan_id": plan_id, "ratio": ratio,
                                                 "captions": captions, "render": render})
    if wait_s <= 0:
        return res
    job = _wait_job(res["job_id"], wait_s)
    if job["status"] != "done":
        raise ToolError(f"Studio build {job['status']}: {job.get('message')}")
    summary = http("GET", f"/exports/filmcraft/{plan_id}/build.json")
    summary["open_with"] = "Open project_path in Elevate Vision Studio (or FilmCraft) to keep editing."
    if summary.get("output_path"):
        summary["output_url"] = _ui(f"exports/filmcraft/{plan_id}/{summary['output_path'].replace(chr(92), '/').rsplit('/', 1)[-1]}")
    return summary


@tool("mf_touchup", "Quick built-in photo fix (auto_levels, white_balance, denoise, sharpen, all). "
      "For pro looks use pc_apply.",
      {"asset_id": {"type": "integer"},
       "preset": {"type": "string", "enum": ["auto_levels", "white_balance", "denoise", "sharpen", "all"]}},
      ("asset_id", "preset"), read_only=False, title="Touch-up")
def mf_touchup(asset_id: int, preset: str) -> Any:
    job = http("POST", "/api/touchup", {"asset_id": asset_id, "preset": preset})
    return _wait_job(job["job_id"], 120)


@tool("pc_recipes", "PhotoCraft looks available by name (auto, punchy, clean, film, warm, bw) "
      "with the exact engine steps each runs.", title="PhotoCraft recipes")
def pc_recipes() -> Any:
    return http("GET", "/api/photocraft/recipes")


@tool("pc_commands", "PhotoCraft engine commands MediaForge allows (adjustments, filters, crop/"
      "resize/rotate) with parameter docs. Filter by text, e.g. 'curves', 'blur', 'vibrance'.",
      {"filter": {"type": "string", "default": ""}}, title="PhotoCraft commands")
def pc_commands(filter: str = "") -> Any:
    return http("GET", "/api/photocraft/commands", query={"filter": filter}, timeout=60)


@tool("pc_apply", "Edit a photo with PhotoCraft (Photoshop-grade engine): a named recipe, explicit "
      "steps [{id, params}], or a recipe followed by extra steps. Writes a NEW file; the original "
      "is never touched. Identical edits are cached. Returns output_url for mf_view_export.",
      {"asset_id": {"type": "integer"},
       "recipe": {"type": "string", "enum": ["auto", "punchy", "clean", "film", "warm", "bw"]},
       "steps": {"type": "array", "items": {"type": "object", "properties": {
           "id": {"type": "string"}, "params": {"type": "object"}}, "required": ["id"]}},
       "format": {"type": "string", "enum": ["jpg", "png", "webp", "tif", "psd"], "default": "jpg"},
       "quality": {"type": "integer", "minimum": 1, "maximum": 100, "default": 92}},
      ("asset_id",), read_only=False, title="PhotoCraft edit")
def pc_apply(asset_id: int, recipe: str | None = None, steps: list[dict[str, Any]] | None = None,
             format: str = "jpg", quality: int = 92) -> Any:
    if not recipe and not steps:
        raise ToolError("give a recipe, steps, or both")
    res = http("POST", "/api/photocraft/apply", {"asset_id": asset_id, "recipe": recipe,
                                                 "steps": steps or [], "format": format,
                                                 "quality": quality})
    job = _wait_job(res["job_id"], 300)
    res["status"] = job["status"]
    res["message"] = job.get("message")
    res["open_url"] = _ui(res["output_url"])
    if job["status"] != "done":
        raise ToolError(f"PhotoCraft job {job['status']}: {job.get('message')}")
    return res


@tool("mf_scan", "Index new folders (absolute paths on the MediaForge machine). Re-scans skip known "
      "files. Returns a job id; AI indexing continues in the background.",
      {"paths": {"type": "array", "items": {"type": "string"}, "minItems": 1}}, ("paths",),
      read_only=False, title="Scan folders")
def mf_scan(paths: list[str]) -> Any:
    return http("POST", "/api/scan", {"paths": paths})


@tool("mf_jobs", "Recent background jobs (scan, ai, render, touchup, photocraft).",
      {"limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20}}, title="Jobs")
def mf_jobs(limit: int = 20) -> Any:
    return http("GET", "/api/jobs", query={"limit": limit})


@tool("mf_wait_job", "Wait for a background job to finish (polls once a second).",
      {"job_id": {"type": "string"}, "timeout_s": {"type": "integer", "minimum": 1, "maximum": 1800, "default": 300}},
      ("job_id",), title="Wait for job")
def mf_wait_job(job_id: str, timeout_s: int = 300) -> Any:
    return _wait_job(job_id, timeout_s)


@tool("mf_duplicates", "Exact (hash) and near (perceptual) duplicate pairs. Read-only: resolving "
      "duplicates is left to the user in the Dedupe page.", title="Duplicates")
def mf_duplicates() -> Any:
    return http("GET", "/api/dedupe") | {"review_url": _ui("dedupe")}


def _image_result(data: bytes, mime: str, label: str) -> dict[str, Any]:
    return {"_content": [
        {"type": "image", "data": base64.b64encode(data).decode(), "mimeType": mime.split(";")[0]},
        {"type": "text", "text": label},
    ]}


# ---------------------------------------------------------------------------
# prompts: reusable agent workflows
# ---------------------------------------------------------------------------
PROMPTS: dict[str, dict[str, Any]] = {
    "highlight_reel": {
        "description": "Find the best moments of a trip/place/person and produce a reel.",
        "arguments": [
            {"name": "subject", "description": "trip, city, person or theme", "required": True},
            {"name": "platform", "description": "tiktok | reels | youtube | square", "required": False},
            {"name": "seconds", "description": "target length", "required": False},
        ],
        "template": (
            "Make a {seconds}-second {platform} highlight reel about: {subject}.\n"
            "1. mf_status, then mf_trips / mf_search to find the matching footage.\n"
            "2. mf_plan_edit with a precise intent (place, mood, length, aspect ratio).\n"
            "3. Inspect weak clips with mf_look; swap or trim them with mf_update_plan until every "
            "clip earns its place (sharp, well-exposed, varied, strongest shot first).\n"
            "4. mf_approve_plan. If it returns a review_url, stop and show me the plan summary and "
            "link; continue once I approve.\n"
            "5. mf_render with the right ratio and report the output path."
        ),
        "defaults": {"platform": "tiktok", "seconds": "30"},
    },
    "photo_glowup": {
        "description": "Pick the best photos for a theme and give them a pro edit with PhotoCraft.",
        "arguments": [
            {"name": "query", "description": "what to look for", "required": True},
            {"name": "look", "description": "auto | punchy | clean | film | warm | bw | describe it",
             "required": False},
            {"name": "count", "description": "how many photos", "required": False},
        ],
        "template": (
            "Find the {count} best photos for '{query}' and edit them with a '{look}' look.\n"
            "1. mf_search (kind photo) and rank by aesthetic_score; mf_look at the finalists.\n"
            "2. For each, pc_apply with the closest recipe; if the look isn't a recipe, use "
            "pc_commands to build explicit steps (curves, vibrance, colorBalance, smartSharpen...).\n"
            "3. mf_view_export each result next to mf_look of the original; redo any that got worse.\n"
            "4. Report the output_url of every edit with one line on what you changed."
        ),
        "defaults": {"look": "punchy", "count": "5"},
    },
    "library_report": {
        "description": "Summarise what's in the library and suggest projects.",
        "arguments": [],
        "template": (
            "Use mf_status, mf_trips, mf_places, mf_faces and mf_duplicates to summarise the library: "
            "size, trips, people, places, duplicate waste. Then suggest three edit projects with the "
            "exact mf_plan_edit intent you'd use for each."
        ),
        "defaults": {},
    },
}


def _prompt_get(name: str, args: dict[str, Any]) -> dict[str, Any]:
    p = PROMPTS.get(name)
    if p is None:
        raise KeyError(name)
    values = dict(p["defaults"])
    values.update({k: v for k, v in (args or {}).items() if v})
    for a in p["arguments"]:
        values.setdefault(a["name"], "")
    return {"description": p["description"],
            "messages": [{"role": "user", "content": {"type": "text", "text": p["template"].format(**values)}}]}


# ---------------------------------------------------------------------------
# JSON-RPC / MCP plumbing
# ---------------------------------------------------------------------------
def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    entry = TOOLS.get(name)
    if entry is None:
        return {"content": [{"type": "text", "text": f"unknown tool {name!r}"}], "isError": True}
    allowed = entry["spec"]["inputSchema"]["properties"]
    unknown = sorted(set(arguments) - set(allowed))
    if unknown:
        return {"content": [{"type": "text", "text": f"unknown argument(s): {', '.join(unknown)}"}],
                "isError": True}
    try:
        result = entry["fn"](**arguments)
    except ToolError as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    except TypeError as exc:
        return {"content": [{"type": "text", "text": f"bad arguments: {exc}"}], "isError": True}
    if isinstance(result, dict) and "_content" in result:
        return {"content": result["_content"]}
    text = json.dumps(result, indent=1, default=str)
    return {"content": [{"type": "text", "text": text}]}


def handle(msg: dict[str, Any]) -> dict[str, Any] | None:
    method = msg.get("method")
    mid = msg.get("id")
    params = msg.get("params") or {}
    if mid is None:  # notification (initialized, cancelled...)
        return None

    def ok(result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    if method == "initialize":
        asked = params.get("protocolVersion")
        return ok({
            "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}, "prompts": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": (
                "MediaForge is the user's local AI media library and editor. Start with mf_status. "
                "Find footage with mf_search/mf_list_assets/mf_trips, plan edits with mf_plan_edit, "
                "and render only approved plans. Use pc_apply for Photoshop-grade photo edits and "
                "mf_look/mf_view_export to check results visually. Originals are never modified."
            ),
        })
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": [t["spec"] for t in TOOLS.values()]})
    if method == "tools/call":
        return ok(call_tool(params.get("name", ""), params.get("arguments") or {}))
    if method == "prompts/list":
        return ok({"prompts": [{"name": n, "description": p["description"], "arguments": p["arguments"]}
                               for n, p in PROMPTS.items()]})
    if method == "prompts/get":
        try:
            return ok(_prompt_get(params.get("name", ""), params.get("arguments") or {}))
        except KeyError:
            return err(-32602, f"unknown prompt {params.get('name')!r}")
    if method in ("resources/list", "resources/templates/list"):
        return ok({"resources": []} if method == "resources/list" else {"resourceTemplates": []})
    return err(-32601, f"method not found: {method}")


def main() -> None:
    # Binary-safe stdio on Windows (no CRLF translation, UTF-8 always).
    stdin = open(sys.stdin.fileno(), "r", encoding="utf-8", newline="\n", closefd=False)
    stdout = open(sys.stdout.fileno(), "w", encoding="utf-8", newline="\n", closefd=False)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
        else:
            try:
                reply = handle(msg) if isinstance(msg, dict) else {
                    "jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "batch not supported"}}
            except Exception as exc:  # noqa: BLE001 - never kill the server
                reply = {"jsonrpc": "2.0", "id": msg.get("id"),
                         "error": {"code": -32603, "message": f"{type(exc).__name__}: {exc}"}}
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()


if __name__ == "__main__":
    main()
