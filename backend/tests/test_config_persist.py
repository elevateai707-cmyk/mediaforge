"""Config persist, fs/stat, and music listing."""
from pathlib import Path


def test_config_put_persists_dirs(client, tmp_path):
    folder = tmp_path / "library"
    folder.mkdir()
    r = client.put("/api/config", json={"media_dirs": [str(folder)], "watcher_enabled": False})
    assert r.status_code == 200
    body = r.json()
    assert str(folder) in body["media_dirs"]
    assert body["watcher_enabled"] is False

    again = client.get("/api/config").json()
    assert str(folder) in again["media_dirs"]


def test_fs_stat_counts_media(client, jpeg_library):
    r = client.post("/api/fs/stat", json={"path": str(jpeg_library)})
    assert r.status_code == 200
    body = r.json()
    assert body["exists"] is True
    assert body["is_dir"] is True
    assert body["photos"] == 3
    assert body["sample_count"] == 3


def test_fs_stat_rejects_relative(client):
    r = client.post("/api/fs/stat", json={"path": "relative/path"})
    assert r.status_code == 422


def test_music_empty_by_default(client):
    r = client.get("/api/music")
    assert r.status_code == 200
    assert r.json() == []


def test_music_lists_tracks(client, tmp_path):
    track = tmp_path / "song.mp3"
    track.write_bytes(b"ID3")
    r = client.put("/api/config", json={"music_dir": str(tmp_path)})
    assert r.status_code == 200
    listing = client.get("/api/music").json()
    assert any(t["path"] == str(track.resolve()) for t in listing)
