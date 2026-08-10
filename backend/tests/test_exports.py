"""Exports: fcpxml + edl + capcut produce files; resolve -> 503 exact message."""
import os

from app.edits.resolve_export import RESOLVE_UNAVAILABLE_MSG


def test_export_fcpxml(client, approved_plan):
    pid = approved_plan()
    r = client.post("/api/export/fcpxml", json={"plan_id": pid})
    assert r.status_code == 200
    path = r.json()["path"]
    assert os.path.isfile(path)
    with open(path, encoding="utf-8") as f:
        content = f.read()
    assert "<fcpxml" in content


def test_export_edl(client, approved_plan):
    pid = approved_plan()
    r = client.post("/api/export/edl", json={"plan_id": pid})
    assert r.status_code == 200
    path = r.json()["path"]
    assert os.path.isfile(path)
    with open(path, encoding="utf-8") as f:
        content = f.read()
    assert "TITLE:" in content


def test_export_capcut(client, approved_plan):
    pid = approved_plan()
    r = client.post("/api/export/capcut", json={"plan_id": pid})
    assert r.status_code == 200
    path = r.json()["path"]
    assert os.path.isfile(path)
    assert os.path.getsize(path) > 100


def test_export_resolve_unavailable_503(client, approved_plan):
    pid = approved_plan()
    r = client.post("/api/export/resolve", json={"plan_id": pid})
    assert r.status_code == 503
    assert r.json()["detail"] == RESOLVE_UNAVAILABLE_MSG


def test_export_unknown_plan_404(client):
    for ep in ("/api/export/fcpxml", "/api/export/edl", "/api/export/capcut"):
        r = client.post(ep, json={"plan_id": "nope"})
        assert r.status_code == 404, ep
