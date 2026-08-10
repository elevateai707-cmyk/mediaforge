"""Search endpoint: /api/search shape + hybrid query."""


def test_search_returns_results(client, wait_job, jpeg_library):
    r = client.post("/api/scan", json={"paths": [str(jpeg_library)]})
    assert r.status_code == 200
    job = wait_job(client, r.json()["job_id"])
    assert job["status"] == "done", job

    resp = client.get("/api/search", params={"q": "scan", "limit": 20})
    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == "scan"
    assert isinstance(data["results"], list)
    assert isinstance(data["took_ms"], int)
    assert len(data["results"]) == 3

    # assets endpoint accepts the same hybrid q param
    listing = client.get("/api/assets", params={"q": "scan_b", "limit": 50}).json()
    assert listing["total"] == 1
    assert listing["items"][0]["path"].endswith("scan_b.jpg")


def test_search_requires_query(client):
    assert client.get("/api/search").status_code == 422
    assert client.get("/api/search", params={"q": ""}).status_code == 422
