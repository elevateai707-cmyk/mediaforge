"""Batch 02 runner diversity cap and the one-off footage fix script (no API, no renders)."""
import importlib.util
import json
from pathlib import Path

import pytest

BATCH = Path(__file__).resolve().parents[2] / "content" / "batch-02"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, BATCH / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def runner():
    return _load("run_batch")


@pytest.fixture(scope="module")
def fix():
    return _load("fix_footage")


def _manifest(cap=None):
    m = {"videos": [{"id": f"AW{i}", "brand": "albertaweb", "kind": "narrated"} for i in range(16, 22)]
         + [{"id": "AW-B16", "brand": "albertaweb", "kind": "broll"}, {"id": "DB16", "brand": "buildwithdb", "kind": "narrated"}]}
    if cap is not None:
        m["max_asset_uses_per_brand"] = cap
    return m


def _state(**videos):
    return {k: {"status": "done", "asset_ids": v} for k, v in videos.items()}


def test_manifest_has_the_usage_cap_setting():
    m = json.loads((BATCH / "manifest.json").read_text())
    assert m["max_asset_uses_per_brand"] == 2


def test_overused_assets_counts_videos_per_brand(runner):
    st = _state(AW16=[1, 2, 2, 3], AW17=[2, 3], AW18=[3], DB16=[1, 1, 9], **{"AW-B16": [3, 4]})
    m = _manifest()
    assert runner.overused_assets(m, st, "albertaweb") == {2, 3}          # 2: AW16+AW17, 3: four videos
    assert runner.overused_assets(m, st, "albertaweb", skip_id="AW17") == {3}
    assert runner.overused_assets(m, st, "buildwithdb") == set()           # DB16 alone, duplicates count once
    assert runner.overused_assets(_manifest(cap=3), st, "albertaweb") == {3}
    assert runner.overused_assets(_manifest(cap=0), st, "albertaweb") == set()
    st["AW19"] = {"status": "failed", "asset_ids": [5, 5]}
    assert 5 not in runner.overused_assets(m, st, "albertaweb")             # only finished videos count


def test_narrated_and_broll_excludes_apply_the_cap_on_top_of_the_recent_window(runner):
    m = _manifest()
    st = _state(AW16=[1, 2], AW17=[2, 9], AW18=[7], AW19=[8], **{"AW-B16": [4]})
    v = next(x for x in m["videos"] if x["id"] == "AW20")
    ex = runner.narrated_excludes(m, st, v)
    assert 2 in ex                       # used twice in the batch (and in the recent window)
    assert {7, 8, 9} <= set(ex)          # previous three narrated videos
    assert 1 not in ex                   # outside the window and under the cap
    b = {"id": "AW-B17", "brand": "albertaweb", "kind": "broll"}
    m["videos"].append(b)
    sets = runner.broll_exclude_sets(m, st, b)
    assert 2 in sets[0] and 4 in sets[0]                                   # cap + last B-roll
    assert sets[-1] == []                                                  # final fallback: nothing
    assert any(2 in s and 4 not in s for s in sets)                       # relaxed: cap only


# ---------------------------------------------------------------------------
# fix_footage.py
# ---------------------------------------------------------------------------
def _cand(aid, windows, daypart="day", smooth=0.95, aesthetic=5.0):
    return {"id": aid, "path": f"/x/DJI_{aid}.MP4", "aesthetic_score": aesthetic, "faces": [],
            "motion": {"daypart": daypart, "smoothness": smooth, "windows_min": windows}}


def test_window_start_and_overlap(fix):
    assert fix.window_start([[0.0, 3.0, 0.9], [10.0, 20.0, 0.9]], 5.0) == 10.0
    assert fix.window_start([[0.0, 20.0, 0.9]], 5.0) == 1.0               # skips the takeoff second
    assert fix.window_start([[0.0, 5.5, 0.9]], 5.0) == 0.0               # no room to skip the first second
    assert fix.window_start([[0.0, 4.0, 0.9]], 5.0) is None
    assert fix.overlaps(12, 18, 7, None) and not fix.overlaps(0, 5, 7, None) and fix.overlaps(2, 5, 0, None)


def test_pick_replacement_rules(fix):
    cands = [_cand(1, [[0, 20, 0.9]], daypart="night"), _cand(2, [[0, 20, 0.9]]), _cand(3, [[0, 3, 0.9]]),
             _cand(4, [[0, 20, 0.9]]), _cand(5, [[0, 20, 0.9]])]
    # same daypart first, then least used
    new, start = fix.pick_replacement(cands, 5.0, "day", banned=set(), counts={2: 1}, cap=2)
    assert new["id"] == 4 and start == 1.0                                 # 2 is used once, 4 is unused; 3 too short
    # banned (already in the video) and capped assets are skipped
    new, _ = fix.pick_replacement(cands, 5.0, "day", banned={4}, counts={2: 2, 5: 1}, cap=2)
    assert new["id"] == 5
    # falls back to another daypart when no same-daypart clip is available
    new, _ = fix.pick_replacement(cands, 5.0, "dusk", banned={2, 4, 5}, counts={}, cap=2)
    assert new["id"] == 1
    assert fix.pick_replacement(cands, 5.0, "day", banned={1, 2, 4, 5}, counts={}, cap=2) is None


class FakeClient:
    def __init__(self, calls):
        self.calls = calls
        self.excluded = []

    def call(self, method, path, body=None, params=None, timeout=90):
        self.calls.append((method, path, body))
        if path == "/api/assets/excluded":
            return {"excluded": self.excluded}
        if path == "/api/assets" and method == "GET":
            return {"total": 3, "items": [_cand(50, [[0, 30, 0.95]]), _cand(51, [[0, 30, 0.95]]), _cand(52, [[0, 30, 0.95]])]}
        if path.startswith("/api/assets/2133") and method == "POST":
            return {}
        if path in ("/api/assets/2133", "/api/assets/2132"):
            return {"id": int(path.rsplit("/", 1)[1]), "motion": {"daypart": "day"}}
        if path.startswith("/api/edits/plan/") and path.endswith("swap-broll"):
            return {}
        if path.startswith("/api/edits/plan/"):
            pid = path.rsplit("/", 1)[1]
            return {"clips": [{"asset_id": a} for a in {"pA": [2133, 7], "pB": [2132, 8], "pC": [2132, 9]}[pid]]}
        if path.endswith("/story"):
            pid = path.split("/")[3]
            old = {"pA": 2133, "pB": 2132, "pC": 2132}[pid]
            start = 12.0 if old == 2133 else 2.0
            return {"beats": [{"index": 1, "slot": 3.0, "clips": [{"asset_id": 7, "start": 1, "end": 4}]},
                              {"index": 2, "slot": 4.0, "clips": [{"asset_id": old, "start": start, "end": start + 4}]}]}
        raise AssertionError(path)


def test_fix_script_dry_run_and_real_run(fix, tmp_path, monkeypatch, capsys):
    (tmp_path / "manifest.json").write_text(json.dumps(
        {"max_asset_uses_per_brand": 2, "videos": [
            {"id": "AW17", "brand": "albertaweb", "kind": "narrated"},
            {"id": "AW20", "brand": "albertaweb", "kind": "narrated"},
            {"id": "AW16", "brand": "albertaweb", "kind": "narrated"}]}))
    (tmp_path / "state.json").write_text(json.dumps(
        {"AW17": {"status": "done", "plan_id": "pA"}, "AW20": {"status": "done", "plan_id": "pB"},
         "AW16": {"status": "done", "plan_id": "pC"}}))
    monkeypatch.setattr(fix, "HERE", tmp_path)
    calls = []
    monkeypatch.setattr(fix, "Client", lambda base, token: FakeClient(calls))
    monkeypatch.setattr(fix, "read_token", lambda: "x")
    ran = []
    monkeypatch.setattr(fix.subprocess, "run", lambda cmd, **k: ran.append(cmd) or type("R", (), {"returncode": 0})())

    import sys
    monkeypatch.setattr(sys, "argv", ["fix_footage.py", "--dry-run", "--only", "AW17,AW20,AW16"])
    assert fix.main() == 0
    out = capsys.readouterr().out
    assert "AW17" in out and "AW20" in out and "AW16" not in out.split("old asset")[1]       # AW16 keeps 2132
    assert not [c for c in calls if c[0] in ("POST", "DELETE")] and not ran                  # read-only

    calls.clear()
    monkeypatch.setattr(sys, "argv", ["fix_footage.py", "--only", "AW17,AW20,AW16"])
    assert fix.main() == 0
    posts = [(p, b) for m, p, b in calls if m == "POST"]
    assert posts[0][0] == "/api/assets/2133/exclude" and posts[0][1]["ranges"] == [[7.0, None]]
    swaps = [(p, b) for p, b in posts if p.endswith("swap-broll")]
    assert {p.split("/")[4] for p, _ in swaps} == {"pA", "pB"}            # AW16's plan (pC) untouched
    for _p, b in swaps:
        assert b["beat_index"] == 2 and b["asset_id"] in (50, 51, 52) and b["start"] >= 1.0
    assert ran and ran[0][-2:] == ["--rerender", "AW17,AW20"]


# ---------------------------------------------------------------------------
# batch 03: [CARD ...] markup, music lines and B-roll tables
# ---------------------------------------------------------------------------
def _load03():
    spec = importlib.util.spec_from_file_location("run_batch03", BATCH.parent / "batch-03" / "run_batch.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_batch03_card_and_music_parsing():
    rb = _load03()
    card = rb.parse_card("[CARD terminal: exploit attempts | counter 1,000 → 10,000 → 1,000,000+]")
    assert card == {"type": "card", "card": {"kind": "terminal", "text": "exploit attempts",
                                              "sub": "counter 1,000 → 10,000 → 1,000,000+"}}
    assert rb.parse_card("[CARD stat: Millions | exploit attempts after disclosure · Source: Cloudflare]")["card"]["sub"] \
        .endswith("Source: Cloudflare")
    assert rb.parse_card("[CARD alert: HEADLINE only]")["card"]["sub"] == ""
    assert rb.parse_card("not a card") is None
    assert rb.parse_music("Sombra do Crime.m4a (tense)") == {"track": "Sombra do Crime.m4a", "section": "default"}
    assert rb.parse_music("none (voice-only, add trending audio)") is None and rb.parse_music("silent") is None


def test_batch03_manifest_matches_the_scripts():
    m = json.loads((BATCH.parent / "batch-03" / "manifest.json").read_text())
    vids = {v["id"]: v for v in m["videos"]}
    assert [v["id"] for v in m["videos"] if v["kind"] == "narrated"] == ["AW31", "AW32", "AW33", "DB31", "DB32", "DB33"]
    aw31 = vids["AW31"]
    kinds = [(x["card"]["kind"] if x else None) for x in aw31["beat_visuals"]]
    assert kinds == ["stat", "headline", None, "alert", None, None] and len(aw31["paragraphs"]) == len(aw31["beat_visuals"])
    assert aw31["music"]["track"] == "Sombra do Crime.m4a" and vids["AW32"]["music"] is None
    assert vids["DB33"]["beat_visuals"][3]["card"]["sub"] == "✔ backups ✔ updates ✔ alerts"
    assert vids["AW-B25"]["silent"] and vids["AW-B24"]["music"]["track"] == "heat_on_the_north-side.mp3"
    assert all(len(v["hashtags"]) <= 5 for v in m["videos"]) and vids["DB-B26"]["target_seconds"] == 32
    # every narrated card is accepted by the backend validator
    import sys
    sys.path.insert(0, str(BATCH.parents[1] / "backend"))
    from app.edits import cards
    for v in m["videos"]:
        if v["kind"] == "narrated" and v.get("beat_visuals"):
            cards.validate_beat_visuals(v["beat_visuals"], len(v["paragraphs"]))
