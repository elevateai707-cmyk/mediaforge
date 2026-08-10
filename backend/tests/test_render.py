"""Render pipeline: plan -> approve gate -> render job -> h264/yuv420p probe."""
import json
import os
import subprocess
import time


def _probe_video(path):
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", path],
        capture_output=True, text=True, check=True,
    )
    streams = json.loads(probe.stdout)["streams"]
    return [s for s in streams if s["codec_type"] == "video"][0]


def test_render_h264_yuv420p(client, wait_job, video_library):
    # scan the videos
    r = client.post("/api/scan", json={"paths": [str(video_library)]})
    assert r.status_code == 200
    job = wait_job(client, r.json()["job_id"])
    assert job["status"] == "done", job

    # create a plan
    r = client.post("/api/edits/plan", json={"intent": "quick reel"})
    assert r.status_code == 200
    plan = r.json()
    assert plan["status"] == "draft"
    assert plan["clips"], f"expected clips, got {plan}"
    pid = plan["plan_id"]
    assert plan["total_duration"] > 0

    # render gate: unapproved plan -> 409
    r = client.post(
        "/api/render",
        json={"plan_id": pid, "ratio": "9:16", "width": 360, "height": 640,
              "captions": False},
    )
    assert r.status_code == 409
    assert "not approved" in r.json()["detail"]

    # approve
    r = client.post(f"/api/edits/plan/{pid}/approve")
    assert r.status_code == 200
    assert r.json()["status"] == "approved"

    # render
    r = client.post(
        "/api/render",
        json={"plan_id": pid, "ratio": "9:16", "width": 360, "height": 640,
              "captions": False},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "render"
    job_id = body["job_id"]

    # poll until done
    deadline = time.time() + 180
    status = None
    while time.time() < deadline:
        status = client.get(f"/api/render/{job_id}").json()
        if status["status"] in ("done", "error"):
            break
        time.sleep(0.5)
    assert status is not None
    assert status["status"] == "done", status

    out = status["output_path"]
    assert out and os.path.isfile(out), f"render output missing: {out}"

    vs = _probe_video(out)
    assert vs["codec_name"] == "h264", vs
    assert vs["pix_fmt"] == "yuv420p", vs

    # plan editing returns to draft (approval gate re-armed)
    r = client.post(f"/api/edits/plan/{pid}/approve")
    assert r.status_code == 200


def test_render_unknown_job(client):
    assert client.get("/api/render/doesnotexist").status_code == 404
