"""Golden-path ingest: ISO6709 GPS tags → Edmonton plan, never Vancouver."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.proc import tool_env

ROOT = Path("/tmp/mf_golden")


def _have_exiftool() -> bool:
    return shutil.which("exiftool") is not None or Path.home().joinpath(".local/bin/exiftool").is_file()


def _tiny_video(path: Path, color: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"color=c={color}:s=320x180:d=3",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-shortest", "-pix_fmt", "yuv420p",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
            str(path),
        ],
        check=True, capture_output=True, env=tool_env(),
    )


def _stamp_iso6709(path: Path, coord: str) -> None:
    exe = shutil.which("exiftool") or str(Path.home() / ".local/bin/exiftool")
    proc = subprocess.run(
        [
            exe, "-overwrite_original", "-api", "QuickTimeUTC",
            f"-Keys:GPSCoordinates={coord}",
            str(path),
        ],
        capture_output=True, text=True, env=tool_env(),
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout


@pytest.mark.skipif(not _have_exiftool(), reason="exiftool not installed")
def test_golden_path_edmonton_iso6709(client, wait_job):
    edm_dir = ROOT / "Edmonton Trip"
    yvr_dir = ROOT / "Vancouver"
    clip1 = edm_dir / "clip1.mov"
    clip2 = yvr_dir / "clip2.mov"
    _tiny_video(clip1, "blue")
    _tiny_video(clip2, "red")
    _stamp_iso6709(clip1, "+53.5461-113.4938/")
    _stamp_iso6709(clip2, "+49.2827-123.1207/")

    r = client.post("/api/scan", json={"paths": [str(ROOT)]})
    assert r.status_code == 200
    job = wait_job(client, r.json()["job_id"])
    assert job["status"] == "done", job

    listing = client.get("/api/assets", params={"limit": 200}).json()
    by_name = {Path(a["path"]).name: a for a in listing["items"]}
    assert "clip1.mov" in by_name
    edm = by_name["clip1.mov"]
    assert edm["city"] == "Edmonton", edm
    assert edm.get("location_source") == "gps", edm
    assert abs(float(edm["gps_lat"]) - 53.5461) < 0.02

    yvr = by_name["clip2.mov"]
    assert yvr["city"] == "Vancouver", yvr

    plan = client.post(
        "/api/edits/plan",
        json={"intent": "make a highlight reel for tiktok of my trip to edmonton 9:16"},
    ).json()
    parsed = plan.get("parsed_intent") or {}
    assert parsed.get("place") == "Edmonton"
    assert parsed.get("ratio") == "9:16"
    assert float(parsed.get("duration_s") or 0) == 30
    clip_ids = {c["asset_id"] for c in plan["clips"]}
    assert clip_ids, plan
    assert edm["id"] in clip_ids
    assert yvr["id"] not in clip_ids

    trips = client.get("/api/trips").json()
    assert any(t.get("city") == "Edmonton" for t in trips), trips

    approve = client.post(f"/api/edits/plan/{plan['plan_id']}/approve")
    assert approve.status_code == 200
    rend = client.post(
        "/api/render",
        json={"plan_id": plan["plan_id"], "ratio": "9:16", "width": 720, "height": 1280,
              "captions": False},
    )
    assert rend.status_code == 200
    done = wait_job(client, rend.json()["job_id"], timeout=180)
    assert done["status"] == "done", done
    status = client.get(f"/api/render/{rend.json()['job_id']}").json()
    out = status.get("output_path")
    assert out and Path(out).is_file(), status
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", out],
        capture_output=True, text=True, env=tool_env(), check=True,
    )
    streams = json.loads(probe.stdout).get("streams") or []
    video = next(s for s in streams if s.get("codec_type") == "video")
    w, h = int(video["width"]), int(video["height"])
    assert w / h == pytest.approx(9 / 16, rel=0.05)
