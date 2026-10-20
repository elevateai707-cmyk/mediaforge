"""Ingest reliability: single scan queue, copy safety, job reconciliation,
watcher supervision, AI worker degradation. No real DB/media is touched (see
conftest: everything lives in a temp dir)."""
import os
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from app import models, persist
from app.db import SessionLocal
from app.ingest import scanner, watcher
from app.ingest.scanqueue import MAX_RETRIES, scan_queue
from app.jobs import jobs


@pytest.fixture(autouse=True)
def _remove_created_assets(client):
    """Tests here add assets to the shared session DB; remove them so the
    count-based assertions in other test modules stay exact."""
    from sqlalchemy import text
    with SessionLocal() as db:
        before = db.query(models.Asset.id).order_by(models.Asset.id.desc()).first()
        floor = before[0] if before else 0
    yield
    with SessionLocal() as db:
        for a in db.query(models.Asset).filter(models.Asset.id > floor).all():
            for tbl in ("clip_embeddings", "asset_fts"):
                try:
                    db.execute(text(f"DELETE FROM {tbl} WHERE asset_id = :a"), {"a": a.id})
                except Exception:  # noqa: BLE001
                    pass
            db.query(models.TripAsset).filter_by(asset_id=a.id).delete()
            db.query(models.MotionAnalysis).filter_by(asset_id=a.id).delete()
            db.query(models.FootageExclusion).filter_by(asset_id=a.id).delete()
            db.delete(a)
        db.query(models.ScanSkip).delete()
        db.commit()
    from app.ingest.trips import cluster_trips
    cluster_trips()


@pytest.fixture()
def queue(client, monkeypatch):
    """The real queue with a controllable fake scan function."""
    scan_queue.reset()
    state = SimpleNamespace(calls=[], active=0, max_active=0, entered=threading.Event(),
                            gate=threading.Event(), results=[])
    lock = threading.Lock()

    def fake_scan(paths, progress=None, cancel_flag=None, rescan=False, settle=False):
        with lock:
            state.active += 1
            state.max_active = max(state.max_active, state.active)
        state.calls.append({"paths": sorted(paths), "rescan": rescan, "settle": settle})
        state.entered.set()
        state.gate.wait(10)
        with lock:
            state.active -= 1
        res = {"added": 0, "skipped": len(paths), "errors": [], "deferred": []}
        if state.results:
            res.update(state.results.pop(0))
        return res

    monkeypatch.setattr(scan_queue, "scan_fn", fake_scan)
    yield state
    state.gate.set()
    scan_queue.reset()


def _wait(client, wait_job, job_id):
    return wait_job(client, job_id)


def test_one_scan_at_a_time_and_pending_coalesces(client, wait_job, queue):
    j1 = client.post("/api/scan", json={"paths": ["/x/a"]}).json()["job_id"]
    assert queue.entered.wait(10)
    # while scan 1 runs, many requests (incl. a duplicate) merge into ONE follow-up
    ids = {client.post("/api/scan", json={"paths": [p]}).json()["job_id"]
           for p in ("/x/b", "/x/c", "/x/b", "/x/d")}
    assert len(ids) == 1 and j1 not in ids
    j2 = ids.pop()
    st = scan_queue.state()
    assert st["running_job"] == j1 and st["queued_job"] == j2 and st["pending_paths"] == 3
    assert client.get(f"/api/jobs/{j2}").json()["status"] == "queued"
    queue.gate.set()
    assert wait_job(client, j1)["status"] == "done"
    assert wait_job(client, j2)["status"] == "done"
    assert queue.max_active == 1
    assert [c["paths"] for c in queue.calls] == [["/x/a"], ["/x/b", "/x/c", "/x/d"]]


def test_enqueue_merges_settle_and_rescan_flags(client, wait_job, queue):
    j = client.post("/api/scan", json={"paths": ["/x/first"]}).json()["job_id"]
    assert queue.entered.wait(10)
    loop_calls = []

    async def go():
        a = scan_queue.enqueue(["/x/p"], settle=True, source="watcher")
        b = scan_queue.enqueue(["/x/p"], settle=False, source="api")   # explicit wins
        c = scan_queue.enqueue(["/x/q"], rescan=True, source="api")
        loop_calls.extend([a, b, c])

    import asyncio
    asyncio.run_coroutine_threadsafe(go(), scan_queue._loop).result(10)
    assert len(set(loop_calls)) == 1
    queue.gate.set()
    wait_job(client, loop_calls[0])
    follow = queue.calls[1:]
    assert {(tuple(c["paths"]), c["rescan"], c["settle"]) for c in follow} == {
        (("/x/p",), False, False), (("/x/q",), True, False)}
    wait_job(client, j)


def test_deferred_files_are_retried_then_given_up(client, wait_job, queue):
    queue.gate.set()
    d = scanner.Deferred("/x/growing.mp4", 0.05, "file still changing")
    queue.results.append({"deferred": [d]})
    j = client.post("/api/scan", json={"paths": ["/x"]}).json()["job_id"]
    wait_job(client, j)
    deadline = time.time() + 10
    while len(queue.calls) < 2 and time.time() < deadline:
        time.sleep(0.05)
    assert queue.calls[1]["paths"] == ["/x/growing.mp4"] and queue.calls[1]["settle"] is True
    # bounded retries: after MAX_RETRIES the file is reported as given up
    scan_queue.reset()
    for _ in range(MAX_RETRIES + 1):
        scan_queue._handle_deferred([scanner.Deferred("/x/never.mp4", 0.01, "unreadable")])
    assert "/x/never.mp4" in scan_queue.gave_up


# ---------------------------------------------------------------------------
# scanner copy-safety
# ---------------------------------------------------------------------------
def test_ignored_names_and_hidden_dirs(tmp_path):
    for name in (".hidden.jpg", "~$lock.jpg", "._apple.jpg", "x.jpg.part", "a.crdownload",
                 ".DS_Store", "t.tmp", "Thumbs.db"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / ".Trash-1000").mkdir()
    (tmp_path / ".Trash-1000" / "inner.jpg").write_bytes(b"x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "good.jpg").write_bytes(b"x")
    (tmp_path / "top.mp4").write_bytes(b"x")
    names = [p.name for p in scanner._collect_files([str(tmp_path)])]
    assert names == ["good.jpg", "top.mp4"]
    assert scanner._collect_files([str(tmp_path / ".hidden.jpg")]) == []
    # same file via two overlapping inputs is collected once
    assert len(scanner._collect_files([str(tmp_path), str(tmp_path / "sub")])) == 2


def test_watcher_ignores_temp_events(monkeypatch):
    watcher.stop()
    watcher._pending.clear()
    ev = lambda p, d=False, t="created": SimpleNamespace(  # noqa: E731
        src_path=p, dest_path="", is_directory=d, event_type=t)
    for p in ("/m/.x.jpg", "/m/a.jpg.part", "/m/~$a.jpg", "/m/._a.jpg", "/m/a.txt", "/m/b.crdownload"):
        watcher._note(ev(p))
    watcher._note(ev("/m/.cache", d=True))
    watcher._note(ev("/m/dir", d=True, t="modified"))
    assert not watcher._pending
    watcher._note(ev("/m/ok.mp4"))
    assert watcher._pending == {"/m/ok.mp4"}
    watcher.stop()
    watcher._pending.clear()


def test_settle_defers_fresh_file_then_ingests(client, media_dir, jpeg_library, monkeypatch):
    f = media_dir / "fresh_settle.jpg"
    from PIL import Image
    Image.new("RGB", (64, 48), (1, 2, 3)).save(f, "JPEG")
    monkeypatch.setattr(scanner, "SETTLE_SECONDS", 0.4)
    res = scanner.scan_paths([str(f)], settle=True)
    assert res["added"] == 0 and [d.path for d in res["deferred"]] == [str(f)]
    with SessionLocal() as db:
        assert db.query(models.Asset).filter_by(path=str(f)).count() == 0
    time.sleep(0.6)
    res = scanner.scan_paths([str(f)], settle=True)
    assert res["added"] == 1 and not res["deferred"]
    # explicit scans (no settle) are not delayed
    g = media_dir / "explicit.jpg"
    Image.new("RGB", (64, 48), (9, 9, 9)).save(g, "JPEG")
    assert scanner.scan_paths([str(g)])["added"] == 1


def test_unreadable_video_is_deferred_not_marked_bad(client, media_dir):
    f = media_dir / "half_copied.mp4"
    f.write_bytes(os.urandom(4096))          # no moov atom: ffprobe cannot read it
    res = scanner.scan_paths([str(f)])
    assert res["added"] == 0 and res["deferred"][0].delay == scanner.DEFER_INCOMPLETE_S
    with SessionLocal() as db:
        assert db.query(models.Asset).filter_by(path=str(f)).count() == 0
    # once the file is complete it is picked up by the next attempt
    f.unlink()
    import subprocess
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=red:s=64x64:d=1", "-pix_fmt", "yuv420p", str(f)], check=True)
    assert scanner.scan_paths([str(f)])["added"] == 1


def test_known_and_duplicate_files_are_not_rehashed(client, media_dir, monkeypatch):
    from PIL import Image
    a, b = media_dir / "orig.jpg", media_dir / "copy_of_orig.jpg"
    Image.new("RGB", (80, 60), (200, 10, 10)).save(a, "JPEG")
    shutil.copyfile(a, b)
    first = scanner.scan_paths([str(media_dir)])
    assert first["added"] == 1 and first["skipped"] == 1       # copy = duplicate by hash

    def boom(_path):
        raise AssertionError("catch-up scan must not re-hash known files")

    monkeypatch.setattr(scanner, "sha256_of", boom)
    again = scanner.scan_paths([str(media_dir)])
    assert again["added"] == 0 and again["skipped"] == 2 and not again["errors"]


def test_scan_leaves_no_checked_out_connections(client, media_dir, jpeg_library):
    from app.db import _engine
    scanner.scan_paths([str(jpeg_library)], rescan=True)
    scanner.scan_paths([str(jpeg_library)])
    assert _engine.pool.checkedout() == 0


# ---------------------------------------------------------------------------
# job reconciliation / cleanup
# ---------------------------------------------------------------------------
def _job(db, jid, kind, status, age_days=0.0):
    ts = datetime.now(timezone.utc) - timedelta(days=age_days)
    db.add(models.Job(id=jid, kind=kind, status=status, message="x", created_at=ts,
                      finished_at=ts if status in ("done", "error") else None))


def test_startup_reconciliation(client):
    with SessionLocal() as db:
        _job(db, "ghost-run", "scan", "running")
        _job(db, "ghost-que", "scan", "queued")
        _job(db, "was-done", "scan", "done")
        _job(db, "ai-worker-x", "ai", "running")
        db.commit()
    n = jobs.reconcile_startup()
    assert n >= 2
    out = {j: client.get(f"/api/jobs/{j}").json() for j in ("ghost-run", "ghost-que", "was-done")}
    assert out["ghost-run"]["status"] == "interrupted"
    assert out["ghost-run"]["message"] == "interrupted by restart"
    assert out["ghost-que"]["status"] == "interrupted"
    assert out["was-done"]["status"] == "done"


def test_job_cleanup_endpoint(client):
    with SessionLocal() as db:
        _job(db, "c-ghost", "scan", "running")
        _job(db, "c-old", "scan", "done", age_days=40)
        _job(db, "c-new", "scan", "done", age_days=1)
        db.commit()
    r = client.post("/api/jobs/cleanup", json={"delete_finished_older_than_days": 30})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["interrupted"] >= 1 and body["deleted"] >= 1
    assert client.get("/api/jobs/c-ghost").json()["status"] == "interrupted"
    assert client.get("/api/jobs/c-old").status_code == 404
    assert client.get("/api/jobs/c-new").status_code == 200


def test_ai_worker_row_is_not_a_listed_job(client):
    from app.ai import pipeline
    with SessionLocal() as db:
        db.merge(models.Job(id=pipeline.AI_JOB_ID, kind="ai", status="running", message="x"))
        db.commit()
    pipeline._ensure_ai_job_row()
    assert client.get(f"/api/jobs/{pipeline.AI_JOB_ID}").json()["status"] == "done"
    assert all(j["id"] != pipeline.AI_JOB_ID for j in client.get("/api/jobs?limit=200").json())
    h = client.get("/api/health").json()
    assert h["ingest"]["ai_worker"]["state"] in ("idle", "working", "starting", "disabled")
    assert "scan_queue" in h["ingest"] and "watcher" in h["ingest"]


# ---------------------------------------------------------------------------
# watcher supervision
# ---------------------------------------------------------------------------
@pytest.fixture()
def watched(tmp_path, monkeypatch):
    d = tmp_path / "usb"
    d.mkdir()
    calls = []
    monkeypatch.setattr(persist, "media_dirs", lambda: [str(d)])
    monkeypatch.setattr(persist, "watcher_enabled", lambda: True)
    monkeypatch.setattr(scan_queue, "enqueue_threadsafe",
                        lambda paths, **kw: calls.append((list(paths), kw)) or True)
    monkeypatch.setattr(watcher, "DEBOUNCE_S", 0.1)
    yield d, calls
    watcher.stop()
    watcher._dirs.clear()


def test_watcher_status_and_event_enqueue(watched):
    d, calls = watched
    assert watcher.start()
    st = watcher.status()
    assert st["dirs"][str(d)]["status"] == "watching" and st["observer_alive"]
    (d / "new_clip.mp4").write_bytes(b"x" * 100)
    deadline = time.time() + 10
    while not calls and time.time() < deadline:
        time.sleep(0.1)
    assert calls, "watcher never enqueued"
    paths, kw = calls[0]
    assert str(d / "new_clip.mp4") in paths and kw["settle"] is True


def test_supervisor_restarts_on_remount_and_vanish(watched, monkeypatch):
    d, calls = watched
    assert watcher.start()
    obs1 = watcher._observer
    assert watcher.check_once()["restarted"] == []           # steady state: nothing to do

    # remount: same path, different device/mount identity
    real = watcher._identity
    monkeypatch.setattr(watcher, "_identity", lambda p: (999, 2, "99") if p == str(d) else real(p))
    did = watcher.check_once()
    assert did["restarted"] == [str(d)]
    assert watcher._observer is not obs1 and watcher._observer.is_alive()
    assert calls[-1][0] == [str(d)] and calls[-1][1]["source"] == "watcher-restart"
    info = watcher.status()["dirs"][str(d)]
    assert info["status"] == "watching" and info["restarted_at"]
    assert watcher.check_once()["restarted"] == []           # stable under the new identity

    # drive vanishes -> marked missing, no pointless restart
    monkeypatch.setattr(watcher, "_identity", real)
    shutil.rmtree(d)
    n = len(calls)
    assert watcher.check_once()["restarted"] == []
    assert watcher.status()["dirs"][str(d)]["status"] == "missing"
    # ... and comes back (even with identical inode numbers) -> restart + catch-up scan
    d.mkdir()
    assert watcher.check_once()["restarted"] == [str(d)]
    assert len(calls) == n + 1 and calls[-1][0] == [str(d)]
    assert watcher.status()["dirs"][str(d)]["status"] == "watching"


def test_supervisor_restarts_dead_observer_and_runs_safety_scan(watched, monkeypatch):
    d, calls = watched
    assert watcher.start()
    watcher._observer.stop()
    watcher._observer.join(2)
    assert watcher.check_once()["restarted"] == [str(d)]
    assert watcher._observer.is_alive()
    # periodic safety net
    assert watcher.check_once(now=time.monotonic() + watcher.SAFETY_SCAN_S + 1)["safety_scan"]
    assert calls[-1][1]["source"] == "periodic" and calls[-1][0] == [str(d)]
    assert scan_queue.last_catchup


# ---------------------------------------------------------------------------
# AI worker: a failing CLIP must not wedge the queue
# ---------------------------------------------------------------------------
def test_clip_unavailable_still_indexes_then_backfills(client, media_dir, monkeypatch):
    from PIL import Image
    from app.ai import clip, pipeline
    f = media_dir / "oom_photo.jpg"
    Image.new("RGB", (64, 48), (5, 80, 160)).save(f, "JPEG")
    assert scanner.scan_paths([str(f)])["added"] == 1
    with SessionLocal() as db:
        aid = db.query(models.Asset).filter_by(path=str(f)).one().id

    monkeypatch.setattr(clip, "embed_image", lambda img: None)
    monkeypatch.setattr(clip, "model_error", lambda: "CUDA out of memory")
    pipeline.clear_quarantine()
    statuses = []
    for _ in range(pipeline.MAX_CLIP_DEFERRALS + 1):
        pipeline._retry_after.clear()
        with SessionLocal() as db:
            statuses.append(pipeline.process_asset(db, aid)["status"])
    assert statuses[:pipeline.MAX_CLIP_DEFERRALS] == ["pending"] * pipeline.MAX_CLIP_DEFERRALS
    assert statuses[-1] == "indexed"
    with SessionLocal() as db:
        a = db.get(models.Asset, aid)
        assert a.status == "indexed" and a.status_error.startswith(pipeline.NO_EMBED_PREFIX)
        assert "CUDA out of memory" in a.status_error
    assert pipeline.worker_status()["indexed_without_embedding"] >= 1

    # CLIP comes back: the idle loop re-embeds it and clears the flag
    monkeypatch.setattr(clip, "embed_image",
                        lambda img: (np.ones(512, dtype=np.float32) / 22.6).tolist())
    pipeline._retry_after.clear()
    while pipeline._backfill_one():
        pass
    with SessionLocal() as db:
        assert db.get(models.Asset, aid).status_error is None
