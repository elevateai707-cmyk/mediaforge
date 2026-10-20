#!/usr/bin/env python3
"""One-off footage fix for batch 02 (stdlib only; works with ``python3 -I``).

  A. PRIVACY: asset 2133 (DJI_0138) from ~7 s to its end shows a private house with identifiable
     people. Excluded from every future edit (range [7, end]) and swapped out of the narrated
     videos that use it: AW17, AW21, AW25, AW30, DB18, DB22, DB29.
  B. OVERUSE: asset 2132 (DJI_0137) stays in AW16 and DB16 only; it is swapped out of AW20, AW24,
     AW28, DB20, DB24 and DB29.

For each affected beat a replacement is picked from the library: drone footage, not a hyperlapse,
smoothness >= 0.85 inside a smooth window long enough for the beat, no detected faces, not excluded,
not already in that video, the same daypart as the removed clip when possible, and the LEAST used
across that brand's batch-02 videos (counted live from the plans; no replacement may end up used
more than max_asset_uses_per_brand times by the brand, default 2). The swap keeps the voiceover
timing (POST /api/edits/plan/{id}/swap-broll with beat_index and the chosen start). Afterwards every
changed video is re-rendered with ``run_batch.py --rerender`` (re-approves the plan; no ElevenLabs).

    python3 -I fix_footage.py --dry-run     # reads the API, prints the table, changes nothing
    python3 -I fix_footage.py               # excludes 2133[7,end], swaps, re-renders
    python3 -I fix_footage.py --no-rerender # swap only
    python3 -I fix_footage.py --only AW17,DB29

Needs the MediaForge build with footage exclusions (GET /api/assets/excluded). The token is read
from /home/g15/mediaforge/.env and only sent as an Authorization header.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV_FILE = Path("/home/g15/mediaforge/.env")
DRONE_PREFIX = "/srv/iris/media/library/blue-drone-1o1/"
MIN_SMOOTH = 0.85
PRIVACY_ASSET = 2133
PRIVACY_RANGE = (7.0, None)             # seconds; None = to the end of the clip
OVERUSED_ASSET = 2132
A_VIDEOS = ["AW17", "AW21", "AW25", "AW30", "DB18", "DB22", "DB29"]
B_VIDEOS = ["AW20", "AW24", "AW28", "DB20", "DB24", "DB29"]          # 2132 stays in AW16 and DB16
TAKEOFF_SKIP = 1.0


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

    def call(self, method: str, path: str, body=None, params: dict | None = None, timeout: float = 90):
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
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
            raise ApiError(f"HTTP {e.code} {method} {path}: {detail}"[:500]) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ApiError(f"{method} {path} failed: {type(exc).__name__}") from None


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------

def overlaps(a0: float, a1: float, b0: float, b1: float | None) -> bool:
    return a0 < (b1 if b1 is not None else float("inf")) and a1 > b0


def window_start(windows: list, need: float) -> float | None:
    """Start of ``need`` seconds inside the first smooth window long enough (skipping takeoff wobble)."""
    for w in windows or []:
        w0, w1 = float(w[0]), float(w[1])
        a = max(w0, TAKEOFF_SKIP) if w1 - max(w0, TAKEOFF_SKIP) >= need else w0
        if w1 - a >= need - 1e-6:
            return round(a, 3)
    return None


def pick_replacement(cands: list[dict], need: float, daypart: str | None, banned: set[int],
                     counts: dict[int, int], cap: int) -> tuple[dict, float] | None:
    """Best candidate: allowed, enough smooth window, same daypart first, least used, smoothest."""
    scored = []
    for c in cands:
        if c["id"] in banned or counts.get(c["id"], 0) >= cap:
            continue
        m = c.get("motion") or {}
        start = window_start(m.get("windows_min") or m.get("windows"), need)
        if start is None:
            continue
        same = (c.get("motion") or {}).get("daypart") == daypart if daypart else True
        scored.append((0 if same else 1, counts.get(c["id"], 0), -(c["motion"].get("smoothness") or 0),
                       -(c.get("aesthetic_score") or 0), c["id"], c, start))
    if not scored:
        return None
    best = min(scored, key=lambda t: t[:5])
    return best[5], best[6]


# ---------------------------------------------------------------------------
# API data
# ---------------------------------------------------------------------------

def load_candidates(client: Client) -> list[dict]:
    items, offset = [], 0
    while True:
        page = client.call("GET", "/api/assets", params={
            "kind": "video", "path_prefix": DRONE_PREFIX, "hyperlapse": "false",
            "min_smoothness": MIN_SMOOTH, "limit": 200, "offset": offset})
        items += page["items"]
        offset += 200
        if offset >= page["total"]:
            break
    good = []
    for a in items:
        if a.get("faces"):                      # people were detected in this clip: never auto-pick
            continue
        ex = a.get("excluded") or []
        if any(e["start"] <= 0 and e["end"] is None for e in ex):
            continue
        good.append(a)
    return good


def brand_of(video_id: str) -> str:
    return "albertaweb" if video_id.startswith("AW") else "buildwithdb"


def current_usage(client: Client, manifest: dict, state: dict) -> tuple[dict[str, dict[int, int]], dict[str, set[int]]]:
    """({brand: {asset: number of that brand's videos using it}}, {video: {assets}}) from live plans."""
    usage: dict[str, dict[int, int]] = {"albertaweb": {}, "buildwithdb": {}}
    per_video: dict[str, set[int]] = {}
    for v in manifest["videos"]:
        e = state.get(v["id"], {})
        if e.get("status") != "done" or not e.get("plan_id"):
            continue
        plan = client.call("GET", f"/api/edits/plan/{e['plan_id']}")
        assets = {c["asset_id"] for c in plan.get("clips", [])}
        per_video[v["id"]] = assets
        for a in assets:
            usage[v["brand"]][a] = usage[v["brand"]].get(a, 0) + 1
    return usage, per_video


def beats_to_fix(client: Client, plan_id: str, asset_id: int, rng: tuple[float, float | None] | None) -> list[dict]:
    story = client.call("GET", f"/api/edits/{plan_id}/story")
    out = []
    for b in story["beats"]:
        for c in b["clips"]:
            if c["asset_id"] == asset_id and (rng is None or overlaps(c["start"], c["end"], rng[0], rng[1])):
                out.append({"beat": b["index"], "slot": float(b["slot"]), "clip": c})
                break
    return out


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="read-only: print the planned swaps")
    ap.add_argument("--no-rerender", action="store_true", help="swap only; do not re-render")
    ap.add_argument("--no-exclude", action="store_true", help="do not (re)apply the exclusion of asset 2133 [7, end]")
    ap.add_argument("--only", help="comma separated video IDs to process")
    ap.add_argument("--state", default="state.json")
    ap.add_argument("--api", help="override the API base URL")
    args = ap.parse_args()

    manifest = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    state = json.loads((HERE / args.state).read_text(encoding="utf-8"))
    client = Client(args.api or manifest.get("api", "http://100.104.197.107:8420"), read_token())
    cap = int(manifest.get("max_asset_uses_per_brand", 2))
    only = {x.strip() for x in args.only.split(",")} if args.only else None

    try:
        excluded = client.call("GET", "/api/assets/excluded")["excluded"]
    except ApiError as exc:
        if not args.dry_run:
            print(f"This MediaForge build has no footage exclusions yet ({exc}). Restart it on the "
                  "footage-guards build first.", file=sys.stderr)
            return 1
        excluded = []
        print("NOTE: this MediaForge build has no footage exclusions yet; the dry run uses the older "
              "motion windows (score >= 0.6) as an approximation. Run the real thing after the restart.\n")
    covered = any(e["asset_id"] == PRIVACY_ASSET and any(r["start"] <= PRIVACY_RANGE[0] and r["end"] is None
                                                         for r in e["ranges"]) for e in excluded)
    if not covered:
        if args.dry_run or args.no_exclude:
            print(f"[{'dry-run' if args.dry_run else 'skipped'}] would exclude asset {PRIVACY_ASSET} from "
                  f"{PRIVACY_RANGE[0]:.0f} s to the end (private house, identifiable people)")
        else:
            client.call("POST", f"/api/assets/{PRIVACY_ASSET}/exclude", body={
                "ranges": [[PRIVACY_RANGE[0], None]], "reason": "private house with identifiable people (privacy)"})
            print(f"excluded asset {PRIVACY_ASSET} from {PRIVACY_RANGE[0]:.0f} s to the end")
    else:
        print(f"asset {PRIVACY_ASSET} [{PRIVACY_RANGE[0]:.0f}, end] is already excluded")

    cands = load_candidates(client)
    usage, per_video = current_usage(client, manifest, state)
    print(f"{len(cands)} candidate assets (drone, not hyperlapse, smoothness >= {MIN_SMOOTH}, no faces); "
          f"cap {cap} uses per brand\n")

    jobs = [(vid, PRIVACY_ASSET, PRIVACY_RANGE, "privacy") for vid in A_VIDEOS] + \
           [(vid, OVERUSED_ASSET, None, "overuse") for vid in B_VIDEOS]
    jobs.sort(key=lambda j: (j[0], j[3]))
    rows, changed, problems = [], [], []
    for vid, old_asset, rng, why in jobs:
        if only and vid not in only:
            continue
        e = state.get(vid, {})
        if e.get("status") != "done" or not e.get("plan_id"):
            problems.append(f"{vid}: not rendered in this state file")
            continue
        brand = brand_of(vid)
        for item in beats_to_fix(client, e["plan_id"], old_asset, rng):
            removed = client.call("GET", f"/api/assets/{old_asset}")
            daypart = (removed.get("motion") or {}).get("daypart")
            banned = set(per_video.get(vid, set())) | {PRIVACY_ASSET, OVERUSED_ASSET}
            picked = pick_replacement(cands, item["slot"], daypart, banned, usage[brand], cap)
            old = item["clip"]
            if picked is None:
                problems.append(f"{vid} beat {item['beat']}: no replacement found (need {item['slot']:.1f} s)")
                rows.append((vid, item["beat"], f"{old_asset} {old['start']:.1f}-{old['end']:.1f}", "NONE", "-"))
                continue
            new, start = picked
            usage[brand][new["id"]] = usage[brand].get(new["id"], 0) + 1
            usage[brand][old_asset] = max(0, usage[brand].get(old_asset, 1) - 1)
            per_video.setdefault(vid, set()).add(new["id"])
            rows.append((vid, item["beat"], f"{old_asset} {old['start']:.1f}-{old['end']:.1f}",
                         f"{new['id']} {start:.1f}-{start + item['slot']:.1f} ({Path(new['path']).name}, "
                         f"{(new.get('motion') or {}).get('daypart')})", str(usage[brand][new["id"]])))
            if not args.dry_run:
                client.call("POST", f"/api/edits/plan/{e['plan_id']}/swap-broll", body={
                    "beat_index": item["beat"], "asset_id": new["id"], "start": start})
                if vid not in changed:
                    changed.append(vid)
    if only and not rows:
        print("nothing to do for", ", ".join(sorted(only)))
    print(f"{'video':<7} {'beat':>4}  {'old asset / range':<22} {'new asset / range':<60} brand uses")
    for r in rows:
        print(f"{r[0]:<7} {r[1]:>4}  {r[2]:<22} {r[3]:<60} {r[4]:>5}")
    for p in problems:
        print("PROBLEM:", p)
    if args.dry_run:
        print("\ndry run: nothing was changed. Affected videos to re-render:", ", ".join(sorted({r[0] for r in rows})))
        return 1 if problems else 0
    if changed and not args.no_rerender:
        print("\nre-rendering:", ", ".join(changed))
        proc = subprocess.run([sys.executable, "-I", str(HERE / "run_batch.py"), "--rerender", ",".join(changed)])
        return proc.returncode or (1 if problems else 0)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
