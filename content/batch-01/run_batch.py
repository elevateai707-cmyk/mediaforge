#!/usr/bin/env python3
"""Batch 01 runner: 30 narrated stories + 30 music-led / silent drone B-roll videos.

Stdlib only (works with ``python3 -I``). Talks to the MediaForge API, copies the
renders and captions into this folder, writes post texts, POSTS.md and QC sheets.

    python3 -I run_batch.py --dry-run               # print every request, send nothing
    python3 -I run_batch.py --only AW-B01           # one or more IDs (comma separated)
    python3 -I run_batch.py                         # everything not yet finished
    python3 -I run_batch.py --rerender narrated-music   # re-render existing story plans (no ElevenLabs)
    python3 -I run_batch.py --rerender AW01,DB04        # ... or just these IDs
    python3 -I run_batch.py --redo AW03             # forget a finished ID and run it again
    python3 -I run_batch.py --make-manifest         # (re)build manifest.json from SCRIPTS.md

Files (all in this folder):
  SCRIPTS.md / MUSIC_PLAN.md  inputs, parsed once into manifest.json (human-editable:
                              fix a script, hook, hint, track or text line there and re-run;
                              delete it or pass --make-manifest to regenerate)
  state.json                  resumable progress (finished IDs are skipped)
  run.log                     log (the API token is never written to it)
  <brand>/<narrated|broll>/   <ID>_<slug>[_voice-only|_silent].mp4, .srt, <ID>_post.txt
  <brand>/POSTS.md            every video file with its post text
  qc/<ID>.png                 contact sheet per video

The API token is read from /home/g15/mediaforge/.env (MF_API_TOKEN) at start-up and only
ever sent as an Authorization header. Videos run strictly one after another.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = "http://100.104.197.107:8420"
ENV_FILE = Path("/home/g15/mediaforge/.env")
MUSIC_DIR = Path("/srv/iris/media/music/suno")
DRONE_PREFIX = "/srv/iris/media/library/blue-drone-1o1/"
BRANDS = {"AW": ("albertaweb", "albertaweb", "AlbertaWeb.Design"),
          "DB": ("buildwithdb", "buildwithdb", "Build With DB")}
VOICE_ONLY_NOTE = "\U0001F3B5 Add trending audio in-app (low volume under voice)"
SILENT_NOTE = "\U0001F3B5 Add trending audio in-app"
BROLL_TAGS = {
    "AW": ["#calgary", "#albertabusiness", "#dronefootage", "#localseo", "#webdesign"],
    "DB": ["#buildwithdb", "#systemscreatefreedom", "#entrepreneur", "#dronefootage", "#automation"],
}
BROLL_HANDLE = {"AW": "albertaweb.design", "DB": "@buildwithdb"}
STORY_TIMEOUT_S = 20 * 60
RENDER_TIMEOUT_S = 60 * 60

log = logging.getLogger("batch01")


# ---------------------------------------------------------------------------
# manifest (parsed from SCRIPTS.md + MUSIC_PLAN assignments transcribed below)
# ---------------------------------------------------------------------------

# Narrated music: ID -> (track file, section). section: default | later | drop
NARRATED_MUSIC = {
    "AW01": ("beyond_the_final_peak.mp3", "default"),
    "AW02": ("saturday_street_slide.mp3", "default"),
    "AW03": ("Grief and Glory.m4a", "default"),
    "AW05": ("432 Hz Rooftop.m4a", "default"),
    "AW06": ("the_three_second_reset.mp3", "default"),
    "AW08": ("ten_minute_reset.mp3", "default"),
    "AW10": ("Sombra do Crime.m4a", "default"),
    "AW12": ("beyond_the_final_peak.mp3", "later"),
    "AW14": ("432 Hz Rooftop.m4a", "drop"),
    "DB03": ("Keep building (short).m4a", "default"),
    "DB04": ("Sombra do Crime.m4a", "default"),
    "DB06": ("Dark Gold Grind.m4a", "default"),
    "DB08": ("Buildremix.m4a", "default"),
    "DB10": ("Dark Gold Grind.m4a", "later"),
    "DB12": ("Grief and Glory.m4a", "default"),
    "DB14": ("the_three_second_reset.mp3", "default"),
}
# B-roll #1-8 music per brand: (track, section, target_seconds)
BROLL_MUSIC = {
    "AW": [("heat_on_the_north-side.mp3", "default", 12), ("heat_on_the_north-side.mp3", "later", 12),
           ("stone_and_steam.mp3", "default", 12), ("stone_and_steam.mp3", "later", 12),
           ("beyond_the_final_peak.mp3", "default", 12), ("saturday_street_slide.mp3", "default", 12),
           ("Golden hour.mp3", "default", 9), ("432 Hz Rooftop.m4a", "default", 12)],
    "DB": [("Keep Building.mp3", "default", 12), ("Keep Building.mp3", "later", 12),
           ("Build it R&B.m4a", "default", 12), ("Build it R&B.m4a", "later", 12),
           ("Buildremix.m4a", "default", 12), ("Dark Gold Grind.m4a", "default", 12),
           ("Keep building (short).m4a", "default", 12), ("Sombra do Crime.m4a", "default", 12)],
}


def slugify(text: str, limit: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:limit].strip("-") or "video"


def parse_hashtags(post: str) -> tuple[str, list[str]]:
    tags = re.findall(r"#\w+", post)
    desc = re.sub(r"\s*#\w+", "", post).strip()
    if len(tags) > 5:
        log.warning("post has %d hashtags; keeping the first 5", len(tags))
    return desc, tags[:5]


def parse_scripts(md: str) -> tuple[list[dict], dict[str, list[str]]]:
    """-> (narrated entries, {"AW": [15 text lines], "DB": [15 text lines]})."""
    videos: list[dict] = []
    cur: dict | None = None
    para: list[str] = []

    def flush_para():
        nonlocal para
        if cur is not None and para:
            cur["paragraphs"].append(" ".join(para).strip())
        para = []

    for raw in md.splitlines():
        line = raw.rstrip()
        m = re.match(r'^### ((?:AW|DB)\d\d) · (AD|ORG) · "(.*)"\s*$', line)
        if m:
            flush_para()
            cur = {"id": m.group(1), "kind": "narrated", "brand": BRANDS[m.group(1)[:2]][0],
                   "type": m.group(2), "name": m.group(3), "hook": "", "hints": [],
                   "paragraphs": [], "post": ""}
            videos.append(cur)
            continue
        if line.startswith("## "):
            flush_para()
            cur = None if not line.startswith("### ") else cur
            if "B-roll videos" in line:
                cur = None
            continue
        if cur is None:
            continue
        if line.startswith("- **Hook overlay:**"):
            cur["hook"] = line.split(":**", 1)[1].strip()
        elif line.startswith("- **B-roll:**"):
            cur["hints"] = [h.strip() for h in line.split(":**", 1)[1].split(" · ") if h.strip()]
        elif line.startswith("- **Post:**"):
            flush_para()
            cur["post"] = line.split(":**", 1)[1].strip()
        elif line.startswith(">"):
            body = line[1:].strip()
            if body:
                para.append(body)
            else:
                flush_para()
        else:
            if para:
                flush_para()
    flush_para()
    table: dict[str, list[str]] = {"AW": [], "DB": []}
    for line in md.splitlines():
        m = re.match(r"^\|\s*(\d+)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|$", line)
        if m:
            table["AW"].append(m.group(2))
            table["DB"].append(m.group(3))
    return videos, table


def emphasise_text_line(text: str) -> str:
    """Mark the longest word (>=5 letters) of a B-roll text line with *asterisks*."""
    words = text.split()
    best, size = -1, 4
    for i, w in enumerate(words):
        letters = re.sub(r"[^A-Za-z]", "", w)
        if len(letters) > size:
            best, size = i, len(letters)
    if best < 0:
        return text
    w = words[best]
    core = re.sub(r"^[^A-Za-z0-9$]+|[^A-Za-z0-9]+$", "", w)
    words[best] = w.replace(core, f"*{core}*", 1) if core else w
    return " ".join(words)


def build_manifest() -> dict:
    videos, table = parse_scripts((HERE / "SCRIPTS.md").read_text(encoding="utf-8"))
    out: list[dict] = []
    for v in videos:
        track, section = NARRATED_MUSIC.get(v["id"], (None, None))
        desc, tags = parse_hashtags(v["post"])
        v.update({"slug": slugify(v["name"]), "music": ({"track": track, "section": section} if track else None),
                  "post_text": desc, "hashtags": tags})
        v["script"] = "\n\n".join(v["paragraphs"])
        out.append(v)
    for key in ("AW", "DB"):
        lines = table[key]
        if len(lines) != 15:
            raise SystemExit(f"expected 15 {key} B-roll text lines in SCRIPTS.md, found {len(lines)}")
        brand, folder, brand_name = BRANDS[key]
        for n, text in enumerate(lines, 1):
            music, target = None, 12
            if n <= 8:
                track, section, target = BROLL_MUSIC[key][n - 1]
                music = {"track": track, "section": section}
            tags = BROLL_TAGS[key]
            out.append({
                "id": f"{key}-B{n:02d}", "kind": "broll", "brand": brand, "name": text,
                "slug": slugify(text), "silent": music is None, "music": music,
                "target_seconds": target, "text_line": emphasise_text_line(text), "hook": None,
                "post_text": f"{text} \U0001F681 {BROLL_HANDLE[key]}", "hashtags": tags[:5],
            })
    return {"api": API, "drone_prefix": DRONE_PREFIX, "music_dir": str(MUSIC_DIR), "videos": out}


def load_manifest(rebuild: bool = False) -> dict:
    path = HERE / "manifest.json"
    if rebuild or not path.exists():
        data = build_manifest()
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"wrote {path} ({len(data['videos'])} videos) - edit it freely, the runner reads it")
        return data
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# beat maps (music start selection)
# ---------------------------------------------------------------------------

def read_beatmap(track: str) -> dict | None:
    f = MUSIC_DIR / ".beats" / f"{track}.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def nearest(values, t):
    return min(values, key=lambda v: abs(v - t)) if values else None


def section_start(bm: dict, which: str, window: float):
    """music_start for a section; None = let MediaForge pick (best section, on a downbeat)."""
    if which == "default":
        return None
    downs = sorted(bm.get("downbeats") or [])
    if not downs:
        return None
    dur = float(bm["duration"])
    latest = max(0.0, dur - window)
    best = float(bm.get("best_start_15s") or 0.0)
    if which == "drop" and bm.get("drop_s") is not None:
        target = float(bm["drop_s"])
        fits = [d for d in downs if d <= latest]
        start = nearest(downs, target)
        if start is not None and start > latest and fits:
            start = fits[-1]
        return start
    # later: ~30 s after the best section, clamped so the window fits; otherwise any
    # downbeat at least 6 s away from the best section
    cands = [best + 30.0, float(bm.get("best_start_30s") or 0.0), downs[0]]
    for c in cands:
        d = nearest([x for x in downs if x <= latest] or downs, min(c, latest))
        if d is not None and abs(d - best) >= 6.0:
            return d
    return None


def bars_per_cut_for(bm: dict | None) -> int:
    return 2 if bm and float(bm.get("bpm") or 0) < 125 else 1


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

class ApiError(Exception):
    pass


def read_token() -> str | None:
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("MF_API_TOKEN="):
                return line.split("=", 1)[1].strip().strip("'\"") or None
    except OSError:
        pass
    return None


class Client:
    def __init__(self, base: str, token: str | None):
        self.base, self._token = base.rstrip("/"), token

    def call(self, method: str, path: str, body=None, timeout: float = 90):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self._token:
            req.add_header("Authorization", f"Bearer {self._token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read().decode("utf-8")).get("detail", "")
            except Exception:  # noqa: BLE001
                detail = ""
            raise ApiError(f"HTTP {e.code} {method} {path}: {detail}"[:600]) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ApiError(f"{method} {path} failed: {type(e).__name__}") from None


def with_retry(label: str, fn):
    """One retry per step."""
    try:
        return fn()
    except (ApiError, RuntimeError) as exc:
        log.warning("%s failed (%s); retrying once in 10 s", label, exc)
        time.sleep(10)
        return fn()


def poll_job(client: Client, job_id: str, timeout: float, what: str) -> dict:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        job = client.call("GET", f"/api/jobs/{job_id}")
        msg = f"{job.get('status')} {int((job.get('progress') or 0) * 100)}% {job.get('message') or ''}"
        if msg != last:
            log.info("%s %s: %s", what, job_id, msg)
            last = msg
        if job["status"] == "done":
            return job
        if job["status"] in ("error", "cancelled", "interrupted"):
            raise RuntimeError(f"{what} job {job_id} {job['status']}: {job.get('message')}")
        time.sleep(4)
    raise RuntimeError(f"{what} job {job_id} timed out")


# ---------------------------------------------------------------------------
# request bodies
# ---------------------------------------------------------------------------

def story_body(v: dict, exclude: list[int]) -> dict:
    paras = v["paragraphs"] if v.get("paragraphs") else v["script"].split("\n\n")
    hints = v.get("hints") or []
    def hint(i: int) -> str:
        h = hints[i % len(hints)]
        return h if "aerial" in h.lower().split() else f"aerial {h}"

    hint_list = [hint(i) for i in range(len(paras))] if hints else None
    flt = {"path_prefixes": [DRONE_PREFIX], "require_aerial": True, "exclude_faces": True}
    if exclude:
        flt["exclude_asset_ids"] = sorted(set(exclude))
    body = {
        "script": "\n\n".join(paras), "ratio": "9:16", "title": f"{v['id']} {v['name']}",
        "brand": v["brand"], "broll_filter": flt, "beat_hints": hint_list,
        "cloud_approved": True, "voice_consent": True,
    }
    if v.get("hook"):
        body["hook_text"] = v["hook"]
    return body


def broll_body(v: dict, exclude: list[int], bm: dict | None, music_start) -> dict:
    flt = {"path_prefixes": [DRONE_PREFIX], "require_aerial": True, "exclude_faces": True}
    if exclude:
        flt["exclude_asset_ids"] = sorted(set(exclude))
    music = v.get("music")
    body = {
        "target_seconds": v.get("target_seconds", 12), "text_line": v.get("text_line", ""),
        "brand": v["brand"], "broll_filter": flt, "silent": bool(v.get("silent")),
        "bars_per_cut": bars_per_cut_for(bm), "ratio": "9:16", "title": f"{v['id']} {v['name']}",
    }
    if v.get("hook"):
        body["hook_text"] = v["hook"]
    if music and not v.get("silent"):
        body["music_path"] = str(MUSIC_DIR / music["track"])
        if music_start is not None:
            body["music_start"] = music_start
    return body


def render_body(plan_id: str, music_path=None, music_start=None) -> dict:
    body = {"plan_id": plan_id, "ratio": "9:16", "width": 1080, "height": 1920, "reframe": "smart"}
    if music_path:
        body["music_path"] = music_path
        if music_start is not None:
            body["music_start"] = music_start
    return body


def estimate_window(v: dict) -> float:
    words = sum(len(p.split()) for p in v.get("paragraphs") or v["script"].split("\n\n"))
    return words / 2.6 + 0.4 * len(v.get("paragraphs") or []) + 2.0


# ---------------------------------------------------------------------------
# outputs
# ---------------------------------------------------------------------------

def out_dir(v: dict) -> Path:
    d = HERE / v["brand"] / ("narrated" if v["kind"] == "narrated" else "broll")
    d.mkdir(parents=True, exist_ok=True)
    return d


def suffix(v: dict) -> str:
    if v["kind"] == "broll":
        return "_silent" if v.get("silent") else ""
    return "" if v.get("music") else "_voice-only"


def file_stem(v: dict) -> str:
    return f"{v['id']}_{v['slug']}{suffix(v)}"


def post_text(v: dict) -> str:
    tags = " ".join((v.get("hashtags") or [])[:5])
    line1 = f"{v.get('post_text', '').strip()} {tags}".strip()
    note = None
    if v["kind"] == "narrated" and not v.get("music"):
        note = VOICE_ONLY_NOTE
    elif v["kind"] == "broll" and v.get("silent"):
        note = SILENT_NOTE
    return line1 + ("\n" + note if note else "") + "\n"


def ffprobe_duration(path: Path) -> float | None:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=60)
        return float(r.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def contact_sheet(video: Path, dest: Path, frames: int = 12) -> bool:
    dur = ffprobe_duration(video)
    if not dur:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vf",
           f"fps={frames / dur:.5f},scale=216:384,tile=6x2:padding=4:color=black",
           "-frames:v", "1", str(dest)]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=300).returncode == 0 and dest.exists()
    except (OSError, subprocess.SubprocessError):
        return False


def write_posts_md(manifest: dict, state: dict) -> None:
    for brand in {v["brand"] for v in manifest["videos"]}:
        lines = [f"# {brand}: video files and post text", ""]
        for v in manifest["videos"]:
            if v["brand"] != brand:
                continue
            st = state.get(v["id"], {})
            rel = Path(st["file"]).relative_to(HERE / brand) if st.get("file") else None
            lines += [f"## {v['id']} · {v['name']}",
                      f"- file: `{rel}`" if rel else "- file: (not rendered yet)",
                      "- post:", "", "```", post_text(v).rstrip(), "```", ""]
        (HERE / brand / "POSTS.md").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# processing
# ---------------------------------------------------------------------------

def plan_asset_ids(client: Client, plan_id: str) -> list[int]:
    plan = client.call("GET", f"/api/edits/plan/{plan_id}")
    return sorted({c["asset_id"] for c in plan.get("clips", [])})


def narrated_excludes(manifest: dict, state: dict, v: dict) -> list[int]:
    """Assets used by this brand's previous 3 narrated videos (in manifest order)."""
    prev = [x for x in manifest["videos"] if x["brand"] == v["brand"] and x["kind"] == "narrated"]
    idx = next(i for i, x in enumerate(prev) if x["id"] == v["id"])
    ids: list[int] = []
    for x in prev[max(0, idx - 3):idx]:
        ids += state.get(x["id"], {}).get("asset_ids", [])
    return sorted(set(ids))


def broll_exclude_sets(manifest: dict, state: dict, v: dict) -> list[list[int]]:
    """Exclusion attempts, strictest first: all of this brand's narrated assets plus its last
    5 B-rolls; then only the last 5 B-rolls; then nothing."""
    narrated = set()
    broll = []
    for x in manifest["videos"]:
        if x["brand"] != v["brand"] or x["id"] == v["id"]:
            continue
        ids = state.get(x["id"], {}).get("asset_ids", [])
        if x["kind"] == "narrated":
            narrated.update(ids)
        elif state.get(x["id"], {}).get("status") == "done":
            broll.append((x["id"], ids))
    last5 = set()
    for _id, ids in broll[-5:]:
        last5.update(ids)
    return [sorted(narrated | last5), sorted(last5), []]


def finish_render(client: Client, v: dict, plan: dict, plan_id: str, render_req: dict,
                  state: dict, entry: dict) -> None:
    job = with_retry("render", lambda: client.call("POST", "/api/render", render_req))
    log.info("%s render job %s", v["id"], job["job_id"])
    entry["render_job"] = job["job_id"]
    poll_job(client, job["job_id"], RENDER_TIMEOUT_S, f"{v['id']} render")
    status = client.call("GET", f"/api/render/{job['job_id']}")
    output = status.get("output_path")
    if not output or not Path(output).is_file():
        raise RuntimeError(f"render finished but output is missing: {output}")
    stem = file_stem(v)
    dest = out_dir(v) / f"{stem}.mp4"
    shutil.copy2(output, dest)
    entry["file"] = str(dest)
    entry["duration"] = ffprobe_duration(dest)
    story = plan.get("story") or {}
    srt = story.get("srt")
    if v["kind"] == "narrated" and srt and Path(srt).is_file():
        shutil.copy2(srt, out_dir(v) / f"{stem}.srt")
    (out_dir(v) / f"{v['id']}_post.txt").write_text(post_text(v), encoding="utf-8")
    sheet = HERE / "qc" / f"{v['id']}.png"
    entry["qc"] = str(sheet) if contact_sheet(dest, sheet) else None


def run_narrated(client: Client, manifest: dict, state: dict, v: dict, entry: dict) -> None:
    body = story_body(v, narrated_excludes(manifest, state, v))
    started = with_retry("story", lambda: client.call("POST", "/api/edits/story", body))
    def _wait():
        return poll_job(client, started["job_id"], STORY_TIMEOUT_S, f"{v['id']} story")
    job = _wait()
    plan_id = (job.get("meta") or {}).get("plan_id")
    if not plan_id:
        raise RuntimeError("story job finished without a plan id")
    entry["plan_id"] = plan_id
    plan = client.call("GET", f"/api/edits/plan/{plan_id}")
    entry["asset_ids"] = sorted({c["asset_id"] for c in plan.get("clips", [])})
    with_retry("approve", lambda: client.call("POST", f"/api/edits/plan/{plan_id}/approve"))
    music_path, music_start = narrated_music_args(v, plan)
    finish_render(client, v, plan, plan_id, render_body(plan_id, music_path, music_start), state, entry)


def narrated_music_args(v: dict, plan: dict):
    """(music_path, music_start) for a narrated video, exactly as the manifest defines them."""
    music_path = music_start = None
    if v.get("music"):
        track = v["music"]["track"]
        music_path = str(MUSIC_DIR / track)
        bm = read_beatmap(track)
        if bm and v["music"].get("section", "default") != "default":
            music_start = section_start(bm, v["music"]["section"], float(plan["total_duration"]) + 2.0)
    return music_path, music_start


def rerender_narrated(client: Client, v: dict, entry: dict) -> None:
    """Render an already-created story plan again (new mix/levels) without recreating the
    story, so no ElevenLabs call is made. Overwrites the mp4, srt, post text and QC sheet."""
    plan_id = entry.get("plan_id")
    if not plan_id:
        raise RuntimeError("no plan_id in the state file; run the video normally first")
    plan = client.call("GET", f"/api/edits/plan/{plan_id}")
    with_retry("approve", lambda: client.call("POST", f"/api/edits/plan/{plan_id}/approve"))
    music_path, music_start = narrated_music_args(v, plan)
    finish_render(client, v, plan, plan_id, render_body(plan_id, music_path, music_start), {}, entry)


def run_broll(client: Client, manifest: dict, state: dict, v: dict, entry: dict) -> None:
    music = v.get("music") if not v.get("silent") else None
    bm = read_beatmap(music["track"]) if music else None
    window = float(v.get("target_seconds", 12))
    music_start = section_start(bm, music["section"], window) if (bm and music) else None
    plan = None
    last_error = None
    for attempt, exclude in enumerate(broll_exclude_sets(manifest, state, v)):
        body = broll_body(v, exclude, bm, music_start)
        try:
            candidate = with_retry("broll plan", lambda: client.call("POST", "/api/edits/broll", body))
        except ApiError as exc:
            last_error = exc
            log.warning("%s: plan with %d excluded assets failed: %s", v["id"], len(exclude), exc)
            continue
        side = client.call("GET", f"/api/edits/{candidate['plan_id']}/broll")
        if side.get("reused_assets") and attempt < 2:
            log.info("%s: %d reused asset(s) with %d excluded; relaxing the exclusion",
                     v["id"], side["reused_assets"], len(exclude))
            plan = candidate  # keep as a last resort
            continue
        plan = candidate
        break
    if plan is None:
        raise RuntimeError(f"could not create a B-roll plan: {last_error}")
    plan_id = plan["plan_id"]
    entry["plan_id"] = plan_id
    entry["asset_ids"] = sorted({c["asset_id"] for c in plan.get("clips", [])})
    with_retry("approve", lambda: client.call("POST", f"/api/edits/plan/{plan_id}/approve"))
    finish_render(client, v, plan, plan_id, render_body(plan_id), state, entry)


def dry_run(manifest: dict, wanted: list[dict]) -> None:
    print(f"DRY RUN: {len(wanted)} video(s); nothing is sent. API {manifest.get('api', API)}\n")
    for v in wanted:
        print(f"=== {v['id']}  {v['kind']}  {v['name']!r}  -> {v['brand']}/{file_stem(v)}.mp4")
        if v["kind"] == "narrated":
            ex = "<asset ids of this brand's previous 3 narrated videos>"
            body = story_body(v, [])
            body["broll_filter"]["exclude_asset_ids"] = ex
            print("POST /api/edits/story", json.dumps(body, ensure_ascii=False, indent=1))
            ms = None
            if v.get("music"):
                bm = read_beatmap(v["music"]["track"])
                if bm and v["music"].get("section", "default") != "default":
                    ms = section_start(bm, v["music"]["section"], estimate_window(v))
            print("POST /api/edits/plan/<plan_id>/approve")
            print("POST /api/render", json.dumps(
                render_body("<plan_id>", str(MUSIC_DIR / v["music"]["track"]) if v.get("music") else None, ms)))
        else:
            music = v.get("music") if not v.get("silent") else None
            bm = read_beatmap(music["track"]) if music else None
            ms = section_start(bm, music["section"], float(v.get("target_seconds", 12))) if (bm and music) else None
            body = broll_body(v, [], bm, ms)
            body["broll_filter"]["exclude_asset_ids"] = "<this brand's narrated + last 5 B-roll assets>"
            print("POST /api/edits/broll", json.dumps(body, ensure_ascii=False, indent=1))
            print("POST /api/edits/plan/<plan_id>/approve")
            print("POST /api/render", json.dumps(render_body("<plan_id>")))
        print("post.txt:\n  " + post_text(v).replace("\n", "\n  ").rstrip())
        print()
    narr = [v for v in wanted if v["kind"] == "narrated"]
    print("SUMMARY: narrated %d (music %d, voice-only %d) | B-roll %d (music %d, silent %d)" % (
        len(narr), sum(1 for v in narr if v.get("music")), sum(1 for v in narr if not v.get("music")),
        len(wanted) - len(narr), sum(1 for v in wanted if v["kind"] == "broll" and not v.get("silent")),
        sum(1 for v in wanted if v["kind"] == "broll" and v.get("silent"))))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", help="comma separated IDs, e.g. AW01,AW-B01")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rerender", help="comma separated narrated IDs (or 'narrated-music' = every narrated "
                    "video with music) to render again from their existing plan: no story is recreated, "
                    "so no ElevenLabs quota is used")
    ap.add_argument("--redo", help="comma separated IDs to forget in the state file and run again")
    ap.add_argument("--make-manifest", action="store_true", help="rebuild manifest.json from SCRIPTS.md")
    ap.add_argument("--state", default="state.json", help="state file name inside this folder")
    ap.add_argument("--api", help="override the API base URL")
    args = ap.parse_args()

    manifest = load_manifest(rebuild=args.make_manifest)
    if args.make_manifest and not (args.only or args.dry_run):
        return 0
    videos = manifest["videos"]
    known = {v["id"] for v in videos}
    only = [x.strip() for x in args.only.split(",")] if args.only else None
    rerender: list[str] | None = None
    if args.rerender:
        if args.rerender.strip() == "narrated-music":
            rerender = [v["id"] for v in videos if v["kind"] == "narrated" and v.get("music")]
        else:
            rerender = [x.strip() for x in args.rerender.split(",") if x.strip()]
        bad = [x for x in rerender if x not in known or next(v for v in videos if v["id"] == x)["kind"] != "narrated"]
        if bad:
            print("--rerender needs narrated IDs; not valid: " + ", ".join(bad), file=sys.stderr)
            return 2
        only = rerender
    if only:
        bad = [x for x in only if x not in known]
        if bad:
            print("unknown ID(s): " + ", ".join(bad), file=sys.stderr)
            return 2
    wanted = [v for v in videos if not only or v["id"] in only]
    if args.dry_run and rerender is not None:
        st = json.loads((HERE / args.state).read_text(encoding="utf-8")) if (HERE / args.state).exists() else {}
        print(f"DRY RUN (re-render, no story recreated): {len(wanted)} video(s)")
        for v in wanted:
            pid = st.get(v["id"], {}).get("plan_id")
            ms = None
            if v.get("music"):
                bm = read_beatmap(v["music"]["track"])
                if bm and v["music"].get("section", "default") != "default":
                    ms = section_start(bm, v["music"]["section"], estimate_window(v))
            print(f"{v['id']:<6} plan {pid or 'MISSING in state.json'}  -> POST /api/render",
                  json.dumps(render_body(pid or "<plan_id>", str(MUSIC_DIR / v["music"]["track"]) if v.get("music") else None, ms)),
                  f"  overwrites {v['brand']}/narrated/{file_stem(v)}.mp4")
        return 0
    if args.dry_run:
        dry_run(manifest, wanted)
        return 0

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(HERE / "run.log", encoding="utf-8"),
                                  logging.StreamHandler(sys.stdout)])
    state_path = HERE / args.state
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    for x in (args.redo or "").split(","):
        if x.strip():
            state.pop(x.strip(), None)
    token = read_token()
    if not token:
        log.warning("no MF_API_TOKEN found; requests are sent without authentication")
    client = Client(args.api or manifest.get("api", API), token)
    try:
        client.call("GET", "/api/health", timeout=20)
    except ApiError as exc:
        log.error("MediaForge is not reachable: %s", exc)
        return 1

    def save():
        tmp = state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, state_path)

    for v in wanted:
        if rerender is None and state.get(v["id"], {}).get("status") == "done":
            log.info("%s already done, skipping", v["id"])
            continue
        if rerender is not None:
            old = state.get(v["id"], {})
            entry = state[v["id"]] = dict(old, status="running", error=None,
                                          started=datetime.now(timezone.utc).isoformat(), rerender=True)
        else:
            entry = state[v["id"]] = {"status": "running", "started": datetime.now(timezone.utc).isoformat()}
        save()
        log.info("=== %s %s (%s)%s", v["id"], v["name"], v["kind"], " [re-render]" if rerender is not None else "")
        try:
            if rerender is not None:
                rerender_narrated(client, v, entry)
            else:
                (run_narrated if v["kind"] == "narrated" else run_broll)(client, manifest, state, v, entry)
            entry["status"] = "done"
        except Exception as exc:  # noqa: BLE001 - keep going with the next video
            entry["status"] = "failed"
            entry["error"] = str(exc)[:500]
            log.error("%s FAILED: %s", v["id"], exc)
            if rerender is not None and old.get("file"):
                entry["status"] = "done"          # the earlier render is still on disk
                entry["error"] = f"re-render failed: {exc}"[:500]
        entry["finished"] = datetime.now(timezone.utc).isoformat()
        save()
        write_posts_md(manifest, state)

    write_posts_md(manifest, state)
    print("\nID        status   dur(s)  file")
    for v in wanted:
        e = state.get(v["id"], {})
        f = Path(e["file"]).name if e.get("file") else (e.get("error") or "")[:70]
        d = f"{e['duration']:.1f}" if e.get("duration") else "-"
        print(f"{v['id']:<9} {e.get('status', 'skipped'):<8} {d:>6}  {f}")
    failed = [v["id"] for v in wanted if state.get(v["id"], {}).get("status") == "failed"]
    if failed:
        print("\nfailed: " + ", ".join(failed) + "  (re-run to retry only those)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
