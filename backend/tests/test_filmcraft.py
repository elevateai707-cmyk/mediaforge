"""FilmCraft bridge: plan -> .fcproj (+ MP4), the approval gate, and phase wiring.

A fake ``filmcraft-cli`` records every script it is asked to run and answers
like the real one (ids, results), so the command sequence is checked anywhere.
``test_real_cli_end_to_end`` drives a real build when one is installed
(MF_FILMCRAFT_CLI_REAL or filmcraft-cli / evstudio-cli on PATH).
"""
from __future__ import annotations

import json
import os
import random
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from app import filmcraft

FAKE_CLI = r'''#!{python}
import json, sys
args = sys.argv[1:]
log = {log!r}
def rec(x):
    with open(log, "a") as fh: fh.write(json.dumps(x) + "\n")
if args[:1] == ["--version"]:
    print("filmcraft-cli 0.7.2 (fake)"); sys.exit(0)
if "probe" in args:
    path = args[args.index("probe") + 1]
    rec({{"probe": path}})
    if "undecodable" in path: sys.stderr.write("unsupported codec\n"); sys.exit(1)
    print(json.dumps({{"kind": "Movie"}})); sys.exit(0)
if "run" in args:
    steps = [json.loads(l) for l in open(args[args.index("run") + 1]) if l.strip()]
    rec({{"run": steps, "args": args}})
    nid = 100
    for i, s in enumerate(steps, 1):
        cid, p = s["id"], s["params"]
        res = None
        if cid == "file.import": res = {{"errors": [], "items": list(range(1, len(p["paths"]) + 1))}}
        elif cid == "file.newSequence": res = {{"sequence": 50}}
        elif cid == "timeline.place": nid += 2; res = {{"clips": [nid, nid + 1]}}
        elif cid == "graphics.newText": nid += 2; res = {{"clip": nid, "layer": 0}}
        elif cid == "sequence.applyVideoTransition" and {fail_dissolve}:
            print(json.dumps({{"id": cid, "line": i, "ok": False, "error": "no handles"}})); continue
        print(json.dumps({{"id": cid, "line": i, "ok": True, "result": res}}))
    if "--save-as" in args: open(args[args.index("--save-as") + 1], "w").write("{{}}")
    sys.exit(0)
if "export" in args:
    out = args[args.index("export") + 1]
    rec({{"export": out, "args": args}})
    open(out, "wb").write(b"\0\0\0\x18ftypmp42")
    print(json.dumps({{"path": out}})); sys.exit(0)
sys.exit(2)
'''


def _make_fake(tmp_path, monkeypatch, fail_dissolve=False):
    log = tmp_path / "calls.jsonl"
    cli = tmp_path / "filmcraft-cli"
    cli.write_text(FAKE_CLI.format(python=sys.executable, log=str(log), fail_dissolve=fail_dissolve))
    cli.chmod(cli.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("MF_FILMCRAFT_CLI", str(cli))
    return log


@pytest.fixture()
def fake_cli(tmp_path, monkeypatch):
    return _make_fake(tmp_path, monkeypatch)


def _calls(log: Path) -> list[dict]:
    return [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []


def _library(client, wait_job, media_dir, videos=True, heic=True) -> dict[str, int]:
    """Unique media (scans dedupe by hash): a JPEG, optionally HEIC + 2 videos."""
    from PIL import Image

    Image.new("RGB", (640, 480), tuple(random.randrange(256) for _ in range(3))).save(media_dir / "still.jpg")
    if heic:
        import pillow_heif

        pillow_heif.register_heif_opener()
        Image.new("RGB", (400, 300), tuple(random.randrange(256) for _ in range(3))).save(media_dir / "phone.heic")
    if videos:
        for name, size in (("land.mp4", "320x180"), ("vert.mp4", "180x320")):
            color = "#%06x" % random.randrange(1 << 24)
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s={size}:d=3:r=30",
                 "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-shortest", "-pix_fmt", "yuv420p",
                 "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(media_dir / name)],
                check=True, capture_output=True)
    job = wait_job(client, client.post("/api/scan", json={"paths": [str(media_dir)]}).json()["job_id"])
    assert job["status"] == "done", job
    items = client.get("/api/assets", params={"limit": 500}).json()["items"]
    return {Path(a["path"]).name: a["id"] for a in items if a["path"].startswith(str(media_dir))}


def _plan(client, ids: dict[str, int], approve: bool) -> str:
    pid = client.post("/api/edits/plan", json={"intent": "test reel"}).json()["plan_id"]
    clips = []
    for name, caption in (("land.mp4", "Arrival"), ("phone.heic", None), ("vert.mp4", "Night"), ("still.jpg", "")):
        if name in ids:
            end = 2.0 if name.endswith(("mp4",)) else 1.5
            clips.append({"asset_id": ids[name], "start": 0.5 if name.endswith("mp4") else 0.0,
                          "end": end, "caption": caption, "transition": "crossfade"})
    r = client.put(f"/api/edits/plan/{pid}", json={"clips": clips})
    assert r.status_code == 200, r.text
    if approve:
        assert client.post(f"/api/edits/plan/{pid}/approve").status_code == 200
    return pid


# --- unit --------------------------------------------------------------------
def test_ticks():
    assert filmcraft.ticks(1) == filmcraft.TICKS_PER_SECOND
    assert filmcraft.ticks(0.5) == filmcraft.TICKS_PER_SECOND // 2


def test_null_caption_is_not_the_word_none(client, wait_job, media_dir):
    """Regression: PUT with caption null used to store the string "None"."""
    ids = _library(client, wait_job, media_dir, videos=False, heic=False)
    pid = _plan(client, ids, approve=False)
    clips = client.get(f"/api/edits/plan/{pid}").json()["clips"]
    assert all(c["caption"] in (None, "") for c in clips if c["asset_id"] == ids["still.jpg"])
    assert all(c["caption"] != "None" for c in clips)


# --- API with the fake CLI -----------------------------------------------------
def test_status(client, fake_cli, monkeypatch):
    assert client.get("/api/filmcraft/status").json()["available"] is True
    monkeypatch.setenv("MF_FILMCRAFT_CLI", "/nope/filmcraft-cli")
    assert client.get("/api/filmcraft/status").json()["available"] is False
    assert client.post("/api/export/filmcraft", json={"plan_id": "x"}).status_code == 503


def test_project_export_builds_expected_timeline(client, wait_job, media_dir, fake_cli):
    ids = _library(client, wait_job, media_dir)
    pid = _plan(client, ids, approve=False)

    r = client.post("/api/export/filmcraft", json={"plan_id": pid, "ratio": "9:16"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "filmcraft" and body["output_path"] is None
    assert wait_job(client, body["job_id"])["status"] == "done"
    assert Path(body["project_path"]).is_file()

    runs = [c for c in _calls(fake_cli) if "run" in c]
    assert len(runs) == 3
    create, timeline, look = (r["run"] for r in runs)
    assert "--save-as" in runs[0]["args"] and "--save" in runs[1]["args"]

    imported = create[1]["params"]["paths"]
    heic_jpg = [p for p in imported if p.endswith(f"{ids['phone.heic']}.jpg")]
    assert heic_jpg and Path(heic_jpg[0]).is_file(), "HEIC must be converted to JPEG"
    assert not any(p.endswith(".heic") for p in imported)

    seq = timeline[0]["params"]
    assert (seq["width"], seq["height"], seq["fps"]) == (1080, 1920, 30)
    places = [s["params"] for s in timeline if s["id"] == "timeline.place"]
    assert len(places) == 4
    assert places[0]["time"] == 0 and places[0]["sourceIn"] == filmcraft.ticks(0.5)
    assert places[0]["duration"] == filmcraft.ticks(1.5)
    assert places[1]["time"] == filmcraft.ticks(1.5) and "sourceIn" not in places[1]  # still
    texts = [s["params"]["text"] for s in timeline if s["id"] == "graphics.newText"]
    assert texts == ["Arrival", "Night"], "empty/null captions get no text clip"

    ids_used = [s["id"] for s in look]
    assert ids_used[0] == "clip.fillFrame" and len(look[0]["params"]["clips"]) == 4
    assert ids_used.count("sequence.applyVideoTransition") == 3
    assert ids_used.count("graphics.set") == 2
    assert not [c for c in _calls(fake_cli) if "export" in c]

    summary = client.get(f"/exports/filmcraft/{pid}/build.json").json()
    assert summary["clips"] == 4 and summary["captions"] == 2 and summary["warnings"] == []


def test_render_requires_approval_then_exports(client, wait_job, media_dir, fake_cli):
    ids = _library(client, wait_job, media_dir, heic=False)
    pid = _plan(client, ids, approve=False)
    assert client.post("/api/export/filmcraft", json={"plan_id": pid, "render": True}).status_code == 409
    assert client.post(f"/api/edits/plan/{pid}/approve").status_code == 200

    body = client.post("/api/export/filmcraft", json={"plan_id": pid, "render": True, "ratio": "16:9",
                                                      "captions": False}).json()
    assert wait_job(client, body["job_id"])["status"] == "done"
    assert body["output_path"].endswith("_16x9.mp4") and Path(body["output_path"]).is_file()
    export = [c for c in _calls(fake_cli) if "export" in c][-1]
    assert export["args"][export["args"].index("--format") + 1] == "h264"
    timeline = [c for c in _calls(fake_cli) if "run" in c][-2]["run"]
    assert timeline[0]["params"]["width"] == 1920
    assert not [s for s in timeline if s["id"] == "graphics.newText"]


def test_bad_requests(client, wait_job, media_dir, fake_cli):
    assert client.post("/api/export/filmcraft", json={"plan_id": "nope"}).status_code == 404
    ids = _library(client, wait_job, media_dir, videos=False, heic=False)
    pid = _plan(client, ids, approve=False)
    assert client.post("/api/export/filmcraft", json={"plan_id": pid, "ratio": "4:3"}).status_code == 422


def test_cosmetic_failures_become_warnings(client, wait_job, media_dir, tmp_path, monkeypatch):
    _make_fake(tmp_path, monkeypatch, fail_dissolve=True)
    ids = _library(client, wait_job, media_dir, heic=False)
    pid = _plan(client, ids, approve=False)
    body = client.post("/api/export/filmcraft", json={"plan_id": pid}).json()
    assert wait_job(client, body["job_id"])["status"] == "done"
    warnings = client.get(f"/exports/filmcraft/{pid}/build.json").json()["warnings"]
    assert warnings and all("no handles" in w for w in warnings)


def test_undecodable_video_falls_back_to_proxy(client, wait_job, media_dir, fake_cli):
    ids = _library(client, wait_job, media_dir, heic=False)
    src = media_dir / "land.mp4"
    bad = media_dir / "undecodable.mp4"
    shutil.copyfile(src, bad)
    bad.write_bytes(bad.read_bytes() + os.urandom(8))  # new hash so the scan indexes it
    ids = _library(client, wait_job, media_dir, videos=False, heic=False) | ids
    items = client.get("/api/assets", params={"limit": 500}).json()["items"]
    bad_id = next(a["id"] for a in items if a["path"] == str(bad))
    from app import config
    proxy = config.PROXIES_DIR / str(bad_id) / "proxy.mp4"
    proxy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, proxy)

    pid = client.post("/api/edits/plan", json={"intent": "x"}).json()["plan_id"]
    client.put(f"/api/edits/plan/{pid}", json={"clips": [
        {"asset_id": bad_id, "start": 0, "end": 1, "caption": None, "transition": "crossfade"}]})
    body = client.post("/api/export/filmcraft", json={"plan_id": pid}).json()
    assert wait_job(client, body["job_id"])["status"] == "done"
    create = [c for c in _calls(fake_cli) if "run" in c][-3]["run"]
    assert create[1]["params"]["paths"] == [str(proxy)]
    assert "proxy" in client.get(f"/exports/filmcraft/{pid}/build.json").json()["warnings"][0]


# --- real engine ---------------------------------------------------------------
def _real_cli():
    cand = os.environ.get("MF_FILMCRAFT_CLI_REAL") or shutil.which("filmcraft-cli") or shutil.which("evstudio-cli")
    return cand if cand and os.path.isfile(cand) else None


@pytest.mark.skipif(_real_cli() is None, reason="real filmcraft-cli not installed")
def test_real_cli_end_to_end(client, wait_job, media_dir, monkeypatch):
    monkeypatch.setenv("MF_FILMCRAFT_CLI", _real_cli())
    ids = _library(client, wait_job, media_dir)
    pid = _plan(client, ids, approve=True)
    body = client.post("/api/export/filmcraft", json={"plan_id": pid, "render": True, "ratio": "1:1"}).json()
    job = wait_job(client, body["job_id"], timeout=900)
    assert job["status"] == "done", job
    summary = client.get(f"/exports/filmcraft/{pid}/build.json").json()
    assert summary["clips"] == 4 and summary["captions"] == 2
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height:format=duration",
                            "-of", "json", body["output_path"]], capture_output=True, text=True, check=True)
    info = json.loads(probe.stdout)
    video = next(s for s in info["streams"] if s["codec_name"] == "h264")
    assert (video["width"], video["height"]) == (1080, 1080)
    assert abs(float(info["format"]["duration"]) - summary["seconds"]) < 0.2
