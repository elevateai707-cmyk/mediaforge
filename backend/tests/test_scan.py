"""Scan flow: 3 JPEGs -> scan job -> assets + thumbnail + file Range."""
import os


def test_scan_creates_assets_and_thumb(client, wait_job, jpeg_library):
    r = client.post("/api/scan", json={"paths": [str(jpeg_library)]})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "scan"
    assert body["job_id"]

    job = wait_job(client, body["job_id"])
    assert job["status"] == "done", job

    listing = client.get("/api/assets", params={"q": "scan", "limit": 50}).json()
    assert listing["total"] == 3
    # The scanner walks the given path; tmp dirs may resolve through symlinks
    # (e.g. /tmp), so compare canonicalized paths. It also skips files whose
    # content hash is already indexed: other tests scan byte-identical
    # fixtures first, so in a full-suite run the rows for this library may
    # live under an earlier library's directory — fall back to the shared
    # scan_*.jpg basename so the assertions below always run on the 3 scanned
    # photos (which have identical content either way).
    real_lib = os.path.realpath(str(jpeg_library))
    items = [
        a for a in listing["items"]
        if real_lib in os.path.realpath(a["path"])
        or os.path.basename(a["path"]).startswith("scan_")
    ]
    assert len(items) == 3
    for item in items:
        assert item["kind"] == "photo"
        assert item["status"] == "pending"
        assert item["mime"] == "image/jpeg"

    item = items[0]

    # detail
    det = client.get(f"/api/assets/{item['id']}")
    assert det.status_code == 200
    assert det.json()["id"] == item["id"]
    assert det.json()["scenes"] == []

    # thumbnail
    tr = client.get(f"/api/assets/{item['id']}/thumb")
    assert tr.status_code == 200
    assert tr.headers["content-type"].startswith("image/webp")
    assert len(tr.content) > 100

    # original file with Range support
    fr = client.get(f"/api/assets/{item['id']}/file", headers={"Range": "bytes=0-9"})
    assert fr.status_code == 206
    assert fr.headers.get("content-range", "").startswith("bytes 0-9/")
    assert fr.headers.get("accept-ranges") == "bytes"

    # full file
    full = client.get(f"/api/assets/{item['id']}/file")
    assert full.status_code == 200
    assert full.headers["content-type"] == "image/jpeg"

    # 404s
    assert client.get("/api/assets/999999").status_code == 404
    assert client.get("/api/assets/999999/thumb").status_code == 404
    assert client.get("/api/assets/999999/file").status_code == 404


def test_scan_requires_paths(client):
    r = client.post("/api/scan", json={"paths": []})
    assert r.status_code == 422
