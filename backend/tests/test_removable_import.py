"""Removable-card discovery is non-destructive until a pending item is approved."""
from __future__ import annotations

from app.ingest.removable import RemovableImportService
from app.ingest.removable import RemovableImportError


def test_removable_poll_and_approved_copy(tmp_path, monkeypatch):
    mount_root = tmp_path / "media" / "user"
    dcim = mount_root / "CARD" / "DCIM" / "100MEDIA"
    dcim.mkdir(parents=True)
    source = dcim / "IMG_0001.JPG"
    source.write_bytes(b"image bytes")
    # Symlinks must never become import candidates.
    (dcim / "escape.jpg").symlink_to(tmp_path / "outside.jpg")
    library = tmp_path / "library"
    library.mkdir()
    from app import transfer
    from app.ingest import removable
    monkeypatch.setattr(transfer, "library_root", lambda: library)
    enqueued = []
    monkeypatch.setattr(removable.scan_queue, "enqueue_threadsafe", lambda paths, **kw: enqueued.append((paths, kw)))
    service = RemovableImportService([mount_root])
    pending = service.poll()
    assert len(pending) == 1
    item = pending[0]
    assert item["file_count"] == 1 and item["types"] == ["jpg"]
    assert not list(library.rglob("*.JPG")), "polling must not copy"
    plan = service.reserve(item["id"])
    assert service.poll() == [], "reserved card must not return to pending on a refresh"
    progress = []
    service.copy(plan, lambda fraction, message: progress.append((fraction, message)), lambda: False)
    copied = list(library.rglob("IMG_0001.JPG"))
    assert len(copied) == 1 and copied[0].read_bytes() == b"image bytes"
    assert enqueued and enqueued[0][1]["source"] == "removable-import"


def test_removable_dismiss_and_detached_card(tmp_path):
    root = tmp_path / "media" / "user"
    dcim = root / "CARD" / "DCIM"
    dcim.mkdir(parents=True)
    (dcim / "A.JPG").write_bytes(b"x")
    service = RemovableImportService([root])
    item = service.poll()[0]
    assert service.dismiss(item["id"])
    assert not service.dismiss(item["id"])
    assert service.poll() == [], "dismissal remains in effect while the card stays mounted"


def test_removable_manifest_change_requires_new_approval(tmp_path):
    root = tmp_path / "media" / "user"
    source = root / "CARD" / "DCIM" / "A.JPG"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"first")
    service = RemovableImportService([root])
    pending = service.poll()[0]
    source.write_bytes(b"changed contents")
    refreshed = service.poll()[0]
    assert refreshed["id"] != pending["id"], "a changed manifest requires a fresh approval id"
    try:
        service.reserve(pending["id"])
    except RemovableImportError as exc:
        assert exc.status in (404, 409)
    else:
        raise AssertionError("changed card content must require a new approval")


def test_removable_open_rejects_symlink_replacement_and_indexes_duplicates_once(tmp_path, monkeypatch):
    root = tmp_path / "media" / "user"
    dcim = root / "CARD" / "DCIM"
    dcim.mkdir(parents=True)
    first, second = dcim / "A.JPG", dcim / "B.JPG"
    first.write_bytes(b"same"); second.write_bytes(b"different")
    library = tmp_path / "library"; library.mkdir()
    from app import transfer
    from app.ingest import removable
    monkeypatch.setattr(transfer, "library_root", lambda: library)
    monkeypatch.setattr(removable.scan_queue, "enqueue_threadsafe", lambda *_a, **_kw: None)
    service = RemovableImportService([root])
    plan = service.reserve(service.poll()[0]["id"])
    calls = []
    original = service._duplicate_index
    monkeypatch.setattr(service, "_duplicate_index", lambda path: calls.append(path) or original(path))
    # Swap after approval: descriptor open must refuse the symlink.
    first.unlink(); first.symlink_to(tmp_path / "outside.jpg")
    try:
        service.copy(plan, lambda *_a: None, lambda: False)
    except RemovableImportError as exc:
        assert exc.status == 409
    else:
        raise AssertionError("symlink replacement must be rejected")
    assert len(calls) == 1


def test_removable_approval_http_uses_manifest_count(client, monkeypatch):
    from app import import_routes

    class Pending:
        def reserve(self, import_id):
            assert import_id == "approved-card"
            return {"source": "/media/user/CARD", "manifest": [{}, {}], "bytes": 123}

        def copy(self, *_args):
            raise AssertionError("job factory should not run in route contract test")

    monkeypatch.setattr(import_routes, "removable_imports", Pending())
    monkeypatch.setattr(import_routes.jobs, "run", lambda kind, _run: "import-job")
    response = client.post("/api/import/pending/approved-card/approve")
    assert response.status_code == 200, response.text
    assert response.json() == {"job_id": "import-job", "kind": "removable_import",
                               "source": "/media/user/CARD", "files": 2, "bytes": 123}
