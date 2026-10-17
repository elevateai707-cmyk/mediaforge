"""HTTP plumbing for grade jobs (heavy work is stubbed; core logic has its own tests)."""
import time
from app.grade import job as gjob
from app import grade_api


def test_start_validation(client, tmp_path):
    r = client.post("/api/resolve/grade/jobs", json={"out": "../evil", "clips": str(tmp_path)})
    assert r.status_code == 422
    r = client.post("/api/resolve/grade/jobs", json={"out": "ok", "clips": "/etc"})
    assert r.status_code == 422 and "inside" in r.text
    r = client.post("/api/resolve/grade/jobs", json={"out": "ok", "clips": str(grade_api.HOME / "definitely_missing_dir_xyz")})
    assert r.status_code == 422
    r = client.post("/api/resolve/grade/jobs", json={"out": "ok", "clips": str(grade_api.HOME), "bogus": 1})
    assert r.status_code == 422


def test_job_lifecycle_and_apply(client, monkeypatch, tmp_path):
    calls = {}
    monkeypatch.setattr(gjob, "JOBS_ROOT", tmp_path / "jobs")

    def fake_run(clips, out, **kw):
        calls["run"] = (clips, out, kw)
        kw["progress"]("1 ingest", "start")
        jd = gjob.job_dir(out)
        jd.mkdir(parents=True, exist_ok=True)
        (jd / "job.json").write_text('{"job": "%s", "dir": "%s", "status": "awaiting_look_choice", "looks": {"warm": {}}, "steps": [], "resolve": {"spec": {}}}' % (out, jd))
        return {}

    def fake_apply(out, look, **kw):
        calls["apply"] = (out, look, kw)
        if look == "nope":
            raise ValueError("unknown look 'nope'")
        return {"job": out, "look": look}

    monkeypatch.setattr(gjob, "run_job", fake_run)
    monkeypatch.setattr(gjob, "apply_look", fake_apply)
    home_dir = grade_api.HOME
    r = client.post("/api/resolve/grade/jobs", json={"out": "t1", "clips": str(home_dir), "script_text": "1. a\n2. b", "queue": False})
    assert r.status_code == 202
    for _ in range(50):
        if grade_api._running["t1"]["state"] != "running":
            break
        time.sleep(0.05)
    assert grade_api._running["t1"]["state"] == "finished"
    assert calls["run"][2]["queue"] is False and calls["run"][2]["script"].endswith("script_input.txt")
    d = client.get("/api/resolve/grade/jobs/t1").json()
    assert d["status"] == "awaiting_look_choice" and d["looks"] == ["warm"]
    assert [j["job"] for j in client.get("/api/resolve/grade/jobs").json()["jobs"]] == ["t1"]
    assert client.get("/api/resolve/grade/jobs/nonexistent").status_code == 404
    ok = client.post("/api/resolve/grade/jobs/t1/apply_look", json={"look": "warm", "preview": False})
    assert ok.status_code == 200 and calls["apply"][2]["preview"] is False
    assert client.post("/api/resolve/grade/jobs/t1/apply_look", json={"look": "nope"}).status_code == 409
