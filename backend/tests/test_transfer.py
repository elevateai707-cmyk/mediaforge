"""Import (upload + server-folder copy) and media export (zip + folder copy)."""
import io
import os
import zipfile
from pathlib import Path

import pytest
from PIL import Image


def _jpeg_bytes(color=(10, 20, 30)):
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture()
def import_root(media_dir, monkeypatch):
    """Point imports at a scratch dir that is itself a media root."""
    from app import persist

    monkeypatch.setenv("MF_IMPORT_ROOT", str(media_dir))
    # other tests persist their own media_dirs; pin the roots for this one
    monkeypatch.setattr(persist, "media_dirs", lambda: [str(media_dir)])
    return media_dir


def _asset_id_for(client, path):
    real = os.path.realpath(path)
    listing = client.get("/api/assets", params={"limit": 200, "sort": "added"}).json()
    for a in listing["items"]:
        if os.path.realpath(a["path"]) == real:
            return a["id"]
    raise AssertionError(f"asset for {path} not indexed")


def test_upload_streams_to_dated_folder_and_scans(client, wait_job, import_root):
    data = _jpeg_bytes((200, 10, 10))
    r = client.post("/api/import/upload", params={"filename": "up_a.jpg"}, content=data)
    assert r.status_code == 200, r.text
    body = r.json()
    saved = Path(body["path"])
    assert saved.read_bytes() == data
    assert saved.parent.parent.name == "imports"
    assert Path(body["folder"]) == saved.parent
    assert not list(saved.parent.glob("*.part"))

    # name collision gets a suffix instead of overwriting
    r2 = client.post("/api/import/upload", params={"filename": "up_a.jpg"},
                     content=_jpeg_bytes((1, 2, 3)))
    assert r2.json()["name"] == "up_a (1).jpg"

    # sidecars are accepted, path tricks are flattened to a basename
    r3 = client.post("/api/import/upload", params={"filename": "../../DJI_0001.SRT"},
                     content=b"1\n00:00:00,000 --> 00:00:01,000\nhi\n")
    assert r3.status_code == 200
    assert Path(r3.json()["path"]).parent == saved.parent
    assert r3.json()["name"] == "DJI_0001.SRT"

    # unsupported type and empty body rejected
    assert client.post("/api/import/upload", params={"filename": "evil.exe"},
                       content=b"x").status_code == 415
    assert client.post("/api/import/upload", params={"filename": "e.jpg"},
                       content=b"").status_code == 422

    # auto-scan of the upload folder picks the photo up
    job = client.post("/api/scan", json={"paths": [body["folder"]]}).json()
    assert wait_job(client, job["job_id"])["status"] == "done"
    assert _asset_id_for(client, body["path"])


def test_import_folder_rejections(client, import_root, tmp_path):
    assert client.post("/api/import/folder", json={"path": ""}).status_code == 422
    assert client.post("/api/import/folder", json={"path": "relative/dir"}).status_code == 422
    assert client.post("/api/import/folder",
                       json={"path": str(tmp_path / "nope")}).status_code == 404
    f = tmp_path / "file.txt"
    f.write_text("x")
    assert client.post("/api/import/folder", json={"path": str(f)}).status_code == 422
    # inside the library -> refuse copying onto itself
    inside = import_root / "already"
    inside.mkdir()
    (inside / "a.jpg").write_bytes(_jpeg_bytes())
    r = client.post("/api/import/folder", json={"path": str(inside)})
    assert r.status_code == 422 and "library" in r.json()["detail"]
    # symlink into the library resolves and is refused too
    link = tmp_path / "link"
    link.symlink_to(inside)
    assert client.post("/api/import/folder", json={"path": str(link)}).status_code == 422
    # empty folder
    empty = tmp_path / "empty"
    empty.mkdir()
    assert client.post("/api/import/folder", json={"path": str(empty)}).status_code == 422
    # only ineligible / hidden files -> 422
    junk = tmp_path / "junk"
    (junk / ".git").mkdir(parents=True)
    (junk / ".git" / "a.jpg").write_bytes(_jpeg_bytes())
    (junk / "tool.sh").write_text("x")
    (junk / ".env.json").write_text("{}")
    r = client.post("/api/import/folder", json={"path": str(junk)})
    assert r.status_code == 422 and "no importable" in r.json()["detail"]


def test_import_folder_insufficient_space(client, import_root, tmp_path, monkeypatch):
    src = tmp_path / "bigsrc"
    src.mkdir()
    (src / "a.jpg").write_bytes(_jpeg_bytes())
    from app import transfer

    monkeypatch.setattr(transfer, "free_bytes", lambda _p: 0)
    r = client.post("/api/import/folder", json={"path": str(src)})
    assert r.status_code == 507


def test_import_folder_copies_and_scans(client, wait_job, import_root, tmp_path):
    src = tmp_path / "my drone"
    (src / "sub").mkdir(parents=True)
    img = _jpeg_bytes((9, 99, 199))
    (src / "sub" / "DJI_0002.JPG").write_bytes(img)
    (src / "DJI_0002.SRT").write_text("srt")
    (src / "notes.htm").write_text("<html></html>")
    (src / "run.sh").write_text("x")
    (src / "secret.pem").write_text("x")
    (src / ".hidden.jpg").write_bytes(img)
    (src / ".ssh").mkdir()
    (src / ".ssh" / "id_rsa.srt").write_text("key")
    r = client.post("/api/import/folder", json={"path": str(src)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "import" and body["files"] == 3
    assert body["skipped"] == 4
    dest = Path(body["dest"])
    assert dest.parent == import_root and dest.name == "my drone"
    job = wait_job(client, body["job_id"])
    assert job["status"] == "done", job
    assert (dest / "sub" / "DJI_0002.JPG").read_bytes() == img
    assert (dest / "DJI_0002.SRT").read_text() == "srt"
    assert (dest / "notes.htm").exists()
    assert not (dest / "run.sh").exists() and not (dest / "secret.pem").exists()
    assert not (dest / ".hidden.jpg").exists() and not (dest / ".ssh").exists()
    assert not list(dest.rglob("*.part"))
    assert (src / "sub" / "DJI_0002.JPG").exists()  # source untouched
    # importing the same folder again lands in a fresh directory
    r2 = client.post("/api/import/folder", json={"path": str(src)})
    assert Path(r2.json()["dest"]).name == "my drone (1)"
    wait_job(client, r2.json()["job_id"])


def _scanned_pair(client, wait_job, media_dir, tag):
    ids, paths = [], []
    for i in range(2):
        p = media_dir / f"{tag}_{i}.jpg"
        p.write_bytes(_jpeg_bytes((40 * i + 5, 7, hash(tag) % 200)) + os.urandom(16))
        (media_dir / f"{tag}_{i}.SRT").write_text(f"sub{i}")
        paths.append(p)
    job = client.post("/api/scan", json={"paths": [str(media_dir)]}).json()
    assert wait_job(client, job["job_id"])["status"] == "done"
    for p in paths:
        ids.append(_asset_id_for(client, p))
    return ids, paths


def test_single_download_and_zip_export(client, wait_job, media_dir):
    ids, paths = _scanned_pair(client, wait_job, media_dir, "zipx")

    r = client.get(f"/api/assets/{ids[0]}/download")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    assert r.content == paths[0].read_bytes()
    assert client.get("/api/assets/999999/download").status_code == 404

    r = client.get("/api/export/media/zip", params={"ids": ",".join(map(str, ids))})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert ".zip" in r.headers["content-disposition"]
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert zf.testzip() is None
    names = sorted(zf.namelist())
    assert names == sorted([p.name for p in paths] + [p.with_suffix(".SRT").name for p in paths])
    assert all(i.compress_type == zipfile.ZIP_STORED for i in zf.infolist())
    assert zf.read(paths[1].name) == paths[1].read_bytes()
    assert zf.read(paths[0].with_suffix(".SRT").name) == b"sub0"

    # sidecars can be switched off
    r = client.get("/api/export/media/zip", params={"ids": str(ids[0]), "sidecars": "false"})
    assert zipfile.ZipFile(io.BytesIO(r.content)).namelist() == [paths[0].name]

    assert client.get("/api/export/media/zip", params={"ids": "999999"}).status_code == 404
    assert client.get("/api/export/media/zip", params={"ids": "abc"}).status_code == 422


def test_export_copy_to_folder(client, wait_job, import_root, tmp_path):
    media_dir = import_root
    ids, paths = _scanned_pair(client, wait_job, media_dir, "cpx")
    dest = tmp_path / "out" / "exp"
    r = client.post("/api/export/media/copy", json={"asset_ids": ids, "dest": str(dest)})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "export" and r.json()["files"] == 4
    assert wait_job(client, r.json()["job_id"])["status"] == "done"
    assert sorted(os.listdir(dest)) == sorted(
        [p.name for p in paths] + [p.with_suffix(".SRT").name for p in paths])
    assert (dest / paths[0].name).read_bytes() == paths[0].read_bytes()

    # destination inside the library is refused; relative path refused
    assert client.post("/api/export/media/copy", json={
        "asset_ids": ids, "dest": str(media_dir / "x")}).status_code == 422
    assert client.post("/api/export/media/copy", json={
        "asset_ids": ids, "dest": "rel/dir"}).status_code == 422
    assert client.post("/api/export/media/copy", json={
        "asset_ids": [999999]}).status_code == 404
