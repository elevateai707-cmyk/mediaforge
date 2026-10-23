"""MediaForge MCP server (stdio). Drives the MediaForge HTTP API.

Story voiceover: mf_create_story_plan -> review -> mf_approve_edit_plan -> mf_render(captions=True).
Workflow: mf_create_edit_plan -> mf_get_edit_plan / mf_update_edit_plan ->
mf_approve_edit_plan -> mf_render or mf_export (fcpxml|edl|capcut|resolve).
"""
import asyncio
import os
import time
from typing import Any, Literal, Optional
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import FastMCP

BASE = os.environ.get("MEDIAFORGE_URL", "http://100.104.197.107:8420").rstrip("/")
# Optional shared token for MediaForge deployments that enable API auth.  It is
# deliberately only ever sent in an HTTP header, never in a URL or an error.
API_TOKEN = os.environ.get("MF_API_TOKEN", "")
# Plan creation can call an LLM, so allow a long read timeout.
TIMEOUT = httpx.Timeout(120.0, connect=10.0)

mcp = FastMCP("mediaforge")


async def _call(method: str, path: str, params: dict | None = None, body: Any = None) -> Any:
    """Call the API; raise RuntimeError with the server's message on any failure."""
    params = {k: v for k, v in (params or {}).items() if v is not None}
    headers = {"Authorization": f"Bearer {API_TOKEN}"} if API_TOKEN else None
    try:
        async with httpx.AsyncClient(base_url=BASE, timeout=TIMEOUT) as c:
            r = await c.request(method, path, params=params, json=body, headers=headers)
    except httpx.HTTPError as e:
        raise RuntimeError(f"MediaForge unreachable at {BASE}: {type(e).__name__}: {e}") from e
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except Exception:
            detail = r.text
        raise RuntimeError(f"MediaForge {method} {path} -> HTTP {r.status_code}: {detail}")
    try:
        return r.json()
    except ValueError:
        return {"raw": r.text}


def _with_urls(asset: dict) -> dict:
    """Add URLs for the binary endpoints (not exposed as tools)."""
    i = asset.get("id")
    if i is not None:
        for n in ("file", "thumb", "poster", "proxy"):
            asset[f"{n}_url"] = f"{BASE}/api/assets/{i}/{n}"
    return asset


def _decorate(data: Any) -> Any:
    if isinstance(data, list):
        return [_with_urls(a) if isinstance(a, dict) and "kind" in a else a for a in data]
    if isinstance(data, dict):
        for k in ("assets", "items", "results"):
            if isinstance(data.get(k), list):
                data[k] = _decorate(data[k])
        if "kind" in data and "path" in data:
            _with_urls(data)
    return data


@mcp.tool()
async def mf_health() -> Any:
    """Check that MediaForge is up (GET /api/health) and return its config (GET /api/config),
    including `resolve_available` (whether DaVinci Resolve scripting is reachable). The health
    result's `ingest` block shows per-folder watcher status, the scan queue (running job,
    pending paths), the last catch-up scan and the AI worker queue."""
    health = await _call("GET", "/api/health")
    try:
        cfg = await _call("GET", "/api/config")
    except RuntimeError:
        cfg = None
    return {"health": health, "config": cfg, "base_url": BASE}


@mcp.tool()
async def mf_search(query: str, limit: int = 20) -> Any:
    """Semantic/text search over the media library (GET /api/search).
    limit 1-100. Results include URLs for file/thumb/poster/proxy when they are assets."""
    return _decorate(await _call("GET", "/api/search", {"q": query, "limit": limit}))


@mcp.tool()
async def mf_list_assets(
    limit: int = 50,
    offset: int = 0,
    kind: Optional[str] = None,
    sort: str = "added",
    order: str = "desc",
    tag: Optional[str] = None,
    face: Optional[str] = None,
    q: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    radius_km: Optional[float] = None,
    city: Optional[str] = None,
    trip_id: Optional[int] = None,
    min_aesthetic: Optional[float] = None,
    path_prefix: Optional[str] = None,
    min_smoothness: Optional[float] = None,
    hyperlapse: Optional[bool] = None,
    daypart: Optional[str] = None,
) -> Any:
    """List/filter library assets (GET /api/assets). limit 1-500, offset for paging.
    Drone motion filters (need mf_analyze_motion first): path_prefix (folder), min_smoothness
    (0-1, steady flying), hyperlapse (true = only hyperlapses, false = exclude), daypart
    ("day", "dusk", "night" or comma list). Analysed assets carry a `motion` object.
    kind e.g. 'video'/'image'; sort/order; tag, face (name), q (text), date_from/date_to,
    geo filter (lat, lon, radius_km), city, trip_id, min_aesthetic. Each asset carries
    file_url/thumb_url/poster_url/proxy_url links."""
    p = dict(limit=limit, offset=offset, kind=kind, sort=sort, order=order, tag=tag, face=face,
             q=q, date_from=date_from, date_to=date_to, lat=lat, lon=lon, radius_km=radius_km,
             city=city, trip_id=trip_id, min_aesthetic=min_aesthetic, path_prefix=path_prefix,
             min_smoothness=min_smoothness, hyperlapse=hyperlapse, daypart=daypart)
    return _decorate(await _call("GET", "/api/assets", p))


@mcp.tool()
async def mf_get_asset(asset_id: int, include_transcript: bool = False, include_scenes: bool = False) -> Any:
    """Get one asset's details (GET /api/assets/{id}), with file/thumb/poster/proxy URLs.
    Optionally also fetch its transcript and/or scene list (scene ids/times are what
    edit-plan clips reference)."""
    out = _with_urls(await _call("GET", f"/api/assets/{asset_id}"))
    if include_transcript:
        out["transcript"] = await _call("GET", f"/api/assets/{asset_id}/transcript")
    if include_scenes:
        out["scenes"] = await _call("GET", f"/api/assets/{asset_id}/scenes")
    return out


@mcp.tool()
async def mf_get_captions(
    asset_id: Optional[int] = None,
    style: Literal["standard", "social_vertical", "accessibility"] = "standard",
    plan_id: Optional[str] = None,
) -> Any:
    """Get generated caption cues, QC, and editable word timings for an asset.

    Calls GET /api/assets/{id}/captions and adds downloadable SRT, VTT, and ASS URLs
    for the selected style. Review the returned QC issues and limitations before using
    captions in a finished deliverable.
    """
    if (asset_id is None) == (plan_id is None):
        raise RuntimeError("provide exactly one of asset_id or plan_id")
    if plan_id is not None:
        return await _call("GET", f"/api/edits/plan/{quote(plan_id, safe='')}/caption-words")
    encoded_id = quote(str(asset_id), safe="")
    captions = await _call("GET", f"/api/assets/{encoded_id}/captions", {"style": style})
    # Cue styling is useful for review, but edits must use the source word
    # timings rather than reconstructing words from formatted cues.
    caption_words = await _call("GET", f"/api/assets/{encoded_id}/caption-words")
    captions["caption_words"] = caption_words
    captions["words"] = caption_words.get("words", []) if isinstance(caption_words, dict) else []
    captions["downloads"] = {
        fmt: f"{BASE}/api/assets/{encoded_id}/captions/{fmt}?style={style}"
        for fmt in ("srt", "vtt", "ass")
    }
    return captions


@mcp.tool()
async def mf_update_captions(asset_id: Optional[int] = None, words: Optional[list[dict]] = None,
                             plan_id: Optional[str] = None) -> Any:
    """Replace reviewed caption words for one asset or story plan (PUT caption-words).
    Provide exactly one source. Words are server-validated and must preserve usable timing."""
    if (asset_id is None) == (plan_id is None):
        raise RuntimeError("provide exactly one of asset_id or plan_id")
    path = (f"/api/edits/plan/{quote(plan_id, safe='')}/caption-words" if plan_id is not None
            else f"/api/assets/{asset_id}/caption-words")
    return await _call("PUT", path, body={"words": words})


@mcp.tool()
async def mf_caption_qc(asset_id: Optional[int] = None,
                        style: Literal["standard", "social_vertical", "accessibility"] = "standard",
                        plan_id: Optional[str] = None) -> Any:
    """Return caption review data. Asset results include server cue QC; story plans return
    their editable word timings for review because their captions are generated from the plan."""
    if plan_id is not None:
        return await mf_get_captions(asset_id, style, plan_id)
    captions = await mf_get_captions(asset_id, style, None)
    return {"qc": captions.get("qc"), "limitations": captions.get("limitations", [])}


@mcp.tool()
async def mf_list_faces() -> Any:
    """List face clusters (GET /api/faces): id, name, count. Use a name with mf_list_assets(face=...)."""
    return await _call("GET", "/api/faces")


@mcp.tool()
async def mf_list_places() -> Any:
    """List known places/locations in the library (GET /api/places)."""
    return await _call("GET", "/api/places")


@mcp.tool()
async def mf_list_trips() -> Any:
    """List auto-detected trips (GET /api/trips). Trip ids feed mf_make_trip_reel,
    mf_create_edit_plan(trip_id) and mf_list_assets(trip_id)."""
    return await _call("GET", "/api/trips")


@mcp.tool()
async def mf_make_trip_reel(trip_id: int, intent: Optional[str] = None) -> Any:
    """Create an edit plan for a trip (POST /api/trips/{id}/reel). Optional intent text.
    Creates a new plan (state-changing); review it, then approve and render/export."""
    return await _call("POST", f"/api/trips/{trip_id}/reel", body={"intent": intent})


@mcp.tool()
async def mf_create_edit_plan(
    intent: str = "30-second highlight reel, upbeat",
    trip_id: Optional[int] = None,
    auto_approve: bool = False,
) -> Any:
    """Create an edit plan from a natural-language intent (POST /api/edits/plan).
    Workflow: create plan -> review (mf_get_edit_plan) / adjust (mf_update_edit_plan) ->
    mf_approve_edit_plan -> mf_render (local video) or mf_export (fcpxml|edl|capcut|resolve).
    Leave auto_approve false unless the user explicitly wants to skip review."""
    return await _call("POST", "/api/edits/plan",
                       body={"intent": intent, "trip_id": trip_id, "auto_approve": auto_approve})


@mcp.tool()
async def mf_create_story_plan(
    script: str,
    voice_id: Optional[str] = None,
    trip_id: Optional[int] = None,
    ratio: Literal["16:9", "9:16", "1:1"] = "16:9",
    title: Optional[str] = None,
    caption_style: Literal["kinetic", "classic"] = "kinetic",
    cloud_approved: bool = False,
    voice_consent: bool = False,
    max_characters: int = 5000,
    broll_filter: Optional[dict] = None,
    beat_hints: Optional[list[str]] = None,
    hook_text: Optional[str] = None,
    brand: Optional[str] = None,
    beat_visuals: Optional[list[Optional[dict]]] = None,
) -> Any:
    """Story voiceover: turn a narration script into a draft edit plan (POST /api/edits/story).
    The script is transmitted to ElevenLabs for synthesis and spends ElevenLabs character quota.
    Call only when the user has authorized this specific transmission/quota use and confirmed
    they are permitted to use the selected voice; this tool does not verify voice ownership.
    The script is split into beats, each beat is spoken in the selected or configured voice, and
    matching drone/aerial clips are picked from the library, trimmed to each beat's audio.
    Waits for the background job (up to ~10 min) and returns the plan plus `story` info
    (voiceover.mp3 and captions.srt paths, beat count).
    Workflow: mf_create_story_plan -> review (mf_get_edit_plan / mf_get_story) ->
    mf_approve_edit_plan -> mf_render(ratio="16:9", width=1920, height=1080, captions=True) (match the plan ratio) for a local video with burned-in word-timed
    captions, or mf_export (resolve|fcpxml|edl|capcut; voiceover.mp3 + captions.srt are written
    next to the export). Do not edit story clips' lengths after creation: they are aligned to the
    voiceover. Mark emphasis words in the script with *asterisks* (e.g. "the city *wakes up*");
    1-2 per beat become big gradient headline words in the kinetic caption style, and the
    asterisks are stripped before the text is spoken. With no markup one word per beat is
    auto-picked. 9:16 is the kinetic style's native format. voice_id defaults to the configured
    voice; trip_id restricts footage to one trip. max_characters is a per-job cap (1-50,000).
    B-roll source control: broll_filter = {path_prefixes: [abs path prefixes], require_aerial: bool,
    exclude_faces: bool, min_aesthetic: float 0-10, asset_ids: [ids], exclude_asset_ids: [ids],
    min_smoothness: float 0-1 (steady flying only, taken from smooth windows), hyperlapse: bool|null,
    daypart: ["day"|"dusk"|"night"]}
    restricts which library videos can be used (drone footage lives under
    /srv/iris/media/library/blue-drone-1o1/; use path_prefixes + require_aerial + exclude_faces
    to avoid selfies/street footage). beat_hints = one search query per beat (shorter lists fall back
    to the beat text) used instead of the beat text when matching footage. hook_text (<=90 chars)
    is a big gradient headline shown 0-1.4 s in the upper safe zone; brand ("albertaweb" or
    "buildwithdb", see config/brands.yaml) appends a 1.8 s end card at render time.
    beat_visuals = one entry per beat, null (smooth drone footage) or {"type": "card", "card": {"kind":
    "headline"|"stat"|"alert"|"terminal"|"timeline", "text": ..., "sub": ...}}: a locally rendered
    news-style motion-graphic card replaces that beat's footage for exactly its voice slot (voice sync
    unchanged; no camera footage needed). headline: text = headline, sub = "severity . Source: X" lines
    separated by " · "; stat: text = the number/word (numbers count up, "7 in 10" gets dots), sub = label
    (" · Source: X"); alert: text = banner, sub = line or 3+ " · " items for a checklist; terminal: text =
    counter label, sub = "counter 1,000 → 10,000 → 1,000,000+" (or "✔ a ✔ b ✔ c" checks); timeline: text =
    2-5 steps separated by " · " ("Oct 17 attacks begin"), sub = title."""
    if not cloud_approved:
        raise RuntimeError("Story creation requires cloud_approved=True for this job before sending the script to ElevenLabs")
    if not voice_consent:
        raise RuntimeError("Story creation requires voice_consent=True confirming permission to use the selected voice")
    if not 1 <= max_characters <= 50000:
        raise RuntimeError("max_characters must be between 1 and 50000")
    if len(script) > max_characters:
        raise RuntimeError(f"script has {len(script)} characters, exceeding this job's max_characters={max_characters}")
    started = await _call("POST", "/api/edits/story", body={
        "script": script, "voice_id": voice_id, "trip_id": trip_id,
        "ratio": ratio, "title": title, "caption_style": caption_style,
        "cloud_approved": cloud_approved, "voice_consent": voice_consent,
        "max_characters": max_characters, "broll_filter": broll_filter,
        "beat_hints": beat_hints, "hook_text": hook_text, "brand": brand,
        "beat_visuals": beat_visuals})
    job_id = started["job_id"]
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        job = await _call("GET", f"/api/jobs/{job_id}")
        if job["status"] == "done":
            plan_id = (job.get("meta") or {}).get("plan_id")
            if not plan_id:
                raise RuntimeError(f"story job {job_id} finished without a plan id")
            return await _call("GET", f"/api/edits/plan/{plan_id}")
        if job["status"] in ("error", "cancelled", "interrupted"):
            raise RuntimeError(f"story job {job_id} {job['status']}: {job.get('message')}")
        await asyncio.sleep(3)
    raise RuntimeError(f"story job {job_id} still running; poll mf_get_job('{job_id}') "
                       "then mf_get_edit_plan with meta.plan_id")


@mcp.tool()
async def mf_create_broll_plan(
    music_path: Optional[str] = None,
    music_start: Optional[float] = None,
    target_seconds: float = 30.0,
    bars_per_cut: Literal[1, 2] = 1,
    text_line: str = "",
    brand: Optional[str] = None,
    broll_filter: Optional[dict] = None,
    silent: bool = False,
    asset_ids: Optional[list[int]] = None,
    hook_text: Optional[str] = None,
    ratio: Literal["9:16", "16:9", "4:5", "1:1"] = "9:16",
    title: Optional[str] = None,
    seed: Optional[int] = None,
) -> Any:
    """Music-led drone B-roll plan (POST /api/edits/broll): no narration, no paid calls.
    Cuts land exactly on the music's downbeats (needs a beat map in <music dir>/.beats/, made by
    scripts/beatmap.py); the length is the whole number of bars nearest target_seconds
    (bars_per_cut 1 or 2 bars per shot). B-roll videos are at least 30 s (target_seconds 30-90,
    default 30); the track must be long enough (looping is not allowed). music_path omitted = a random mapped track from the
    music folder (seed makes it repeatable); music_start omitted = the track's best section,
    moved onto a downbeat. If the track's drop falls inside the window, the highest-aesthetic
    clip starts on it. Clips are never reused while unused ones exist, are >= 2 s long and start
    >= 1 s in (no takeoff wobble). text_line is a kinetic one-word-per-beat caption appearing on
    the downbeat of bar 2 (mark emphasis with *asterisks*); hook_text is a 0-1.4 s gradient
    headline; brand ("albertaweb" / "buildwithdb", config/brands.yaml) adds a 1.8 s end card.
    broll_filter = {path_prefixes, require_aerial, exclude_faces, min_aesthetic, asset_ids,
    exclude_asset_ids, min_smoothness, hyperlapse, daypart} picks the footage. By default B-roll
    excludes hyperlapses and requires min_smoothness 0.8 (steady, flowing flight; shots are taken
    only from the smooth windows of a clip); pass {"min_smoothness": null, "hyperlapse": null} to
    switch that off. Needs mf_analyze_motion to have run. If the pool is short, other smooth ranges
    of the same clip are used before any range repeats (drone library: /srv/iris/media/library/blue-drone-1o1/);
    asset_ids also sets priority order. silent=true skips music: a cut every ~1.6 s, length =
    target_seconds, and the render carries a silent AAC track for platform compatibility.
    Returns the draft plan (with a `broll` summary). Workflow: review -> mf_approve_edit_plan ->
    mf_render(plan_id) (the plan's own ratio and music are used; captions/effects/reframe options
    apply as for story renders) -> repeat with different music_path/seed/footage for a batch."""
    return await _call("POST", "/api/edits/broll", body={
        "music_path": music_path, "music_start": music_start, "target_seconds": target_seconds,
        "bars_per_cut": bars_per_cut, "text_line": text_line, "brand": brand,
        "broll_filter": broll_filter, "silent": silent, "asset_ids": asset_ids,
        "hook_text": hook_text, "ratio": ratio, "title": title, "seed": seed})


@mcp.tool()
async def mf_create_compilation(
    daypart: Literal["day", "dusk", "night"],
    include_dusk_in: Optional[Literal["day", "night"]] = None,
    music_path: Optional[str] = None,
    music_start: Optional[float] = None,
    brand: Optional[str] = None,
    hook_text: Optional[str] = None,
    text_line: str = "",
    ratio: Literal["9:16", "16:9", "4:5", "1:1"] = "9:16",
    order: Literal["time", "flow"] = "time",
    min_clip_s: float = 0.8,
    min_seconds: Optional[float] = None,
    title: Optional[str] = None,
) -> Any:
    """Compile ALL hyperlapse clips of one daypart into a single draft plan (POST
    /api/edits/compilation). Hyperlapses are exempt from the smoothness rule; every clip is used
    whole (only black head/tail trimmed), duplicates dropped, ordered by taken_at ("time") or visual
    flow ("flow"). With music_path (needs a beat map) each cut is nudged onto a downbeat by trimming
    <= 0.4 s per clip end; nothing is sped up. include_dusk_in="day" (or "night") folds dusk clips
    into that compilation. The natural length is returned in plan.broll.natural_seconds; looping is
    never allowed. If it is under 30 s a warning is returned; min_seconds (e.g. 30) applies one uniform
    slowdown (at most 0.6x, frames blended) to reach it. hook_text / text_line / brand work as for
    mf_create_broll_plan. Then mf_approve_edit_plan -> mf_render(plan_id). Needs mf_analyze_motion."""
    return await _call("POST", "/api/edits/compilation", body={
        "daypart": daypart, "include_dusk_in": include_dusk_in, "music_path": music_path,
        "music_start": music_start, "brand": brand, "hook_text": hook_text, "text_line": text_line,
        "ratio": ratio, "order": order, "min_clip_s": min_clip_s, "min_seconds": min_seconds,
        "title": title})


@mcp.tool()
async def mf_analyze_motion(force: bool = False, asset_ids: Optional[list[int]] = None,
                            path_prefix: Optional[str] = None) -> Any:
    """Start the CPU-only drone motion analysis job (POST /api/motion/analyze): per video it records
    hyperlapse (25 fps / frame-discontinuity), daypart (day/dusk/night) and a 0-1 smoothness score
    with smooth windows. Skips already-analysed assets unless force=true. New drone assets are
    analysed automatically when the AI worker is idle. Track with mf_get_job; GET /api/motion/status
    (mf_motion_status) shows progress. Safe to run any time (no GPU)."""
    return await _call("POST", "/api/motion/analyze", body={
        "force": force, "asset_ids": asset_ids, "path_prefix": path_prefix})


@mcp.tool()
async def mf_motion_status() -> Any:
    """How many drone videos have motion analysis (GET /api/motion/status), by class."""
    return await _call("GET", "/api/motion/status")


@mcp.tool()
async def mf_get_story(plan_id: str) -> Any:
    """Get a story plan's sidecar (GET /api/edits/{id}/story): beats with text, audio
    durations, word timings and chosen clips, voice_id, voiceover/SRT paths."""
    return await _call("GET", f"/api/edits/{plan_id}/story")


@mcp.tool()
async def mf_get_edit_plan(plan_id: str) -> Any:
    """Get an edit plan (GET /api/edits/plan/{id}): status, clips, duration, ratio, match stats."""
    return await _call("GET", f"/api/edits/plan/{plan_id}")


@mcp.tool()
async def mf_handoff_package(plan_id: str) -> Any:
    """Return the download URL for an approved plan's handoff ZIP.

    The package contains edit-plan metadata, provenance, and captions; source media is not
    bundled. This checks the plan's current status first and returns a URL only when approved.
    """
    encoded_id = quote(plan_id, safe="")
    plan = await _call("GET", f"/api/edits/plan/{encoded_id}")
    if plan.get("status") != "approved":
        raise RuntimeError(f"handoff package requires an approved plan; plan {plan_id!r} is {plan.get('status', 'unknown')!r}")
    return {
        "plan_id": plan_id,
        "download_url": f"{BASE}/api/edits/plan/{encoded_id}/package",
        "contents": "edit plan, provenance, and captions; source media is not included",
    }


@mcp.tool()
async def mf_update_edit_plan(plan_id: str, clips: list[dict]) -> Any:
    """Replace a plan's clip list (PUT /api/edits/plan/{id}). Send the FULL list; each clip:
    {asset_id:int, start:float, end:float, scene_id?:int, caption?:str,
     transition?:str (default 'crossfade'), score?:float}. Fetch the plan first, edit, send back."""
    return await _call("PUT", f"/api/edits/plan/{plan_id}", body={"clips": clips})


@mcp.tool()
async def mf_plan_versions(plan_id: str) -> Any:
    """List saved review revisions for an edit plan (GET /api/edits/plan/{id}/versions)."""
    return await _call("GET", f"/api/edits/plan/{quote(plan_id, safe='')}/versions")


@mcp.tool()
async def mf_restore_plan(plan_id: str, version_id: str) -> Any:
    """Restore an edit-plan revision into a new draft state (POST .../restore)."""
    return await _call("POST", f"/api/edits/plan/{quote(plan_id, safe='')}/versions/{quote(str(version_id), safe='')}/restore")


@mcp.tool()
async def mf_swap_broll(plan_id: str, beat_index: int, asset_id: int, start: Optional[float] = None) -> Any:
    """Swap a story beat's B-roll while preserving its voiceover duration
    (POST /api/edits/plan/{id}/swap-broll). start = where in the replacement the beat's footage
    begins (default 0, or the earliest start that avoids excluded footage); excluded ranges are
    refused. The plan returns to draft: approve it again before rendering."""
    body = {"beat_index": beat_index, "asset_id": asset_id}
    if start is not None:
        body["start"] = start
    return await _call("POST", f"/api/edits/plan/{quote(plan_id, safe='')}/swap-broll", body=body)


@mcp.tool()
async def mf_exclude_footage(asset_id: int, ranges: Optional[list[list[Optional[float]]]] = None,
                             all: bool = False, reason: str = "", clear: bool = False) -> Any:
    """Never use footage again (POST /api/assets/{id}/exclude): all=true excludes the whole clip,
    or ranges=[[start, end], ...] in seconds (end null = to the end of the clip), e.g. a private
    house or people in shot, or an over-used shot. Every selection path (story, B-roll, compilation,
    swap) avoids excluded ranges and the renderer refuses plans that still contain them (swap those
    clips out with mf_swap_broll). clear=true lifts the asset's exclusions (DELETE). Asset listings
    show an `excluded` field; mf_list_excluded_footage lists them all."""
    if clear:
        return await _call("DELETE", f"/api/assets/{asset_id}/exclude")
    return await _call("POST", f"/api/assets/{asset_id}/exclude",
                       body={"ranges": ranges, "all": all, "reason": reason})


@mcp.tool()
async def mf_list_excluded_footage() -> Any:
    """All excluded footage ranges (GET /api/assets/excluded)."""
    return await _call("GET", "/api/assets/excluded")


@mcp.tool()
async def mf_tighten(asset_id: Optional[int] = None, plan_id: Optional[str] = None,
                     silence_db: float = -35, min_silence_s: float = .4,
                     remove_fillers: bool = True, padding_s: float = .08) -> Any:
    """Create a new draft plan that removes detected silence and verified Whisper fillers.
    Exactly one of asset_id or plan_id is required; source media remains unchanged."""
    return await _call("POST", "/api/edits/tighten", body={
        "asset_id": asset_id, "plan_id": plan_id, "silence_db": silence_db,
        "min_silence_s": min_silence_s, "remove_fillers": remove_fillers,
        "padding_s": padding_s,
    })


@mcp.tool()
async def mf_approve_edit_plan(plan_id: str) -> Any:
    """Approve a plan (POST /api/edits/plan/{id}/approve). Required before render/export."""
    return await _call("POST", f"/api/edits/plan/{plan_id}/approve")


@mcp.tool()
async def mf_list_edit_plans(limit: int = 50) -> Any:
    """List edit plans, newest first (GET /api/edits/plans). limit 1-200."""
    return await _call("GET", "/api/edits/plans", {"limit": limit})


@mcp.tool()
async def mf_render(
    plan_id: str,
    ratio: Optional[Literal["9:16", "16:9", "1:1"]] = None,
    music_path: Optional[str] = None,
    captions: bool = True,
    width: Optional[int] = None,
    height: Optional[int] = None,
    caption_style: Optional[Literal["kinetic", "classic"]] = None,
    effects: bool = True,
    presets: list[str] | None = None,
    reframe: str = "smart",
    safe_zones: dict | None = None,
    guide_overlay: bool = False,
    music: Optional[str] = None,
    music_start: Optional[float] = None,
) -> Any:
    """Start a render job for an approved plan (POST /api/render). Returns a job id;
    poll with mf_render_status. Leave ratio/width/height unset for the default: a story plan
    renders in its own ratio (1920x1080 for 16:9), other plans 9:16 at 1080x1920. If you set
    them, keep ratio and width/height consistent (e.g. 16:9 with 1920x1080).
    music_path is a server-side file path (see the API's /api/music). Leave music unset
    to preserve the existing no-music behavior; pass music="auto" only to select a track.
    Story plans only: caption_style "kinetic" (default; one word at a time, gradient glitch
    emphasis words, from the user's kinetic caption style) or "classic" (lower-third SRT).
    effects (kinetic only, default true) adds a warm grade, slow push-in on each clip and RGB
    glitch cuts at beat boundaries. Both are ignored for non-story plans.
    Beat sync: when the music track has a beat map (<music dir>/.beats/<file>.json, made by
    scripts/beatmap.py), story renders start the track at music_start seconds (default: the
    track's best section, on a downbeat), snap clip cuts to the nearest music beat within
    +/-150 ms without moving the voice, flash the glitch on downbeats and fade the music over
    the last 0.6 s. Tracks without a beat map render exactly as before."""
    return await _call("POST", "/api/render", body=dict(
        plan_id=plan_id, ratio=ratio, music_path=music_path, captions=captions,
        width=width, height=height, caption_style=caption_style, effects=effects,
        presets=presets, reframe=reframe, safe_zones=safe_zones,
        guide_overlay=guide_overlay, music=music, music_start=music_start))


@mcp.tool()
async def mf_render_status(job_id: str) -> Any:
    """Status/progress/output of a render job (GET /api/render/{job_id})."""
    return await _call("GET", f"/api/render/{job_id}")


@mcp.tool()
async def mf_export(plan_id: str, format: Literal["fcpxml", "edl", "capcut", "resolve"]) -> Any:
    """Export an approved plan (POST /api/export/{format}). fcpxml/edl/capcut write a file
    on the MediaForge host and return its path. 'resolve' pushes the plan into a RUNNING
    DaVinci Resolve (project, bins, timeline, markers, subtitles); it errors with a setup
    message (HTTP 503) if Resolve isn't running or external scripting is disabled
    (Resolve > Preferences > General > External scripting using = Local)."""
    return await _call("POST", f"/api/export/{format}", body={"plan_id": plan_id})


@mcp.tool()
async def mf_list_jobs(limit: int = 20) -> Any:
    """List background jobs (scan, analysis, render...) (GET /api/jobs). limit 1-200."""
    return await _call("GET", "/api/jobs", {"limit": limit})


@mcp.tool()
async def mf_get_job(job_id: str) -> Any:
    """Get one background job's status (GET /api/jobs/{id})."""
    return await _call("GET", f"/api/jobs/{job_id}")


@mcp.tool()
async def mf_scan(paths: Optional[list[str]] = None, rescan: bool = False) -> Any:
    """Start a library scan job (POST /api/scan), or a full re-analysis with rescan=true
    (POST /api/rescan). paths: server-side directories/files; omit to use configured media
    dirs. Heavy and state-changing: only run when the user asks. Track with mf_get_job.
    Scans share one queue: only one runs at a time and overlapping requests merge into a single
    follow-up job (the returned job id is the one that will cover your paths). To add a new
    folder from outside the library use mf_import. Job status can be queued|running|done|error|
    cancelled|interrupted."""
    return await _call("POST", "/api/rescan" if rescan else "/api/scan", body={"paths": paths or []})


@mcp.tool()
async def mf_import(path: str, copy: bool = True) -> Any:
    """Add media to the library at any time. path: an ABSOLUTE server-side folder (or a single
    file). A folder outside the library is COPIED into it (POST /api/import/folder) and then
    scanned; a folder/file already inside the library (or copy=false) is just scanned in place
    (POST /api/scan). Everything funnels into one scan queue (one scan at a time; requests made
    during a scan merge into a single follow-up), files still being copied are deferred until
    stable, and the folder watcher + a periodic safety scan also pick up new files by themselves.
    Returns a job id; track with mf_get_job. State-changing: only run when the user asks."""
    if copy:
        try:
            return await _call("POST", "/api/import/folder", body={"path": path})
        except RuntimeError as e:
            msg = str(e)
            if "already inside the library" not in msg and "not a directory" not in msg:
                raise
    return await _call("POST", "/api/scan", body={"paths": [path]})


@mcp.tool()
async def mf_list_pending_imports() -> Any:
    """List removable-media imports awaiting explicit approval (GET /api/import/pending)."""
    return await _call("GET", "/api/import/pending")


@mcp.tool()
async def mf_approve_import(import_id: str) -> Any:
    """Approve one previously listed removable-media import (POST /api/import/pending/{id}/approve)."""
    return await _call("POST", f"/api/import/pending/{quote(import_id, safe='')}/approve")


@mcp.tool()
async def mf_list_music() -> Any:
    """List local music tracks and their lightweight analysis (GET /api/music)."""
    return await _call("GET", "/api/music")


@mcp.tool()
async def mf_cleanup_jobs(older_than_minutes: float = 0, delete_finished_older_than_days: Optional[float] = None) -> Any:
    """Housekeeping for the job list (POST /api/jobs/cleanup): marks ghost jobs (still
    'running'/'queued' but with no live task, e.g. left over from a crash or restart) as
    'interrupted', skipping ones younger than older_than_minutes, and optionally DELETES finished
    jobs (done/error/cancelled/interrupted) older than delete_finished_older_than_days. Returns
    {interrupted, deleted}. Deleting is irreversible: only pass the days value when asked."""
    return await _call("POST", "/api/jobs/cleanup", body={
        "older_than_minutes": older_than_minutes,
        "delete_finished_older_than_days": delete_finished_older_than_days})


if __name__ == "__main__":
    mcp.run()
