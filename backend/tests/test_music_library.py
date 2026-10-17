from __future__ import annotations

from app.ingest import music


def test_list_tracks_remains_backward_compatible_and_adds_metadata(monkeypatch, tmp_path):
    track = tmp_path / "upbeat" / "morning.wav"
    track.parent.mkdir()
    track.write_bytes(b"not decoded in this unit test")
    monkeypatch.setattr(music, "_metadata", lambda child, root: {
        "duration": 42.0, "loudness": -14.2, "rough_bpm": 120.0,
        "filename": child.name, "folder": "upbeat", "mood_tags": ["upbeat"],
    })
    tracks = music.list_tracks(str(tmp_path))
    assert tracks == [{
        "name": "morning", "path": str(track.resolve()), "ext": ".wav",
        "duration": 42.0, "loudness": -14.2, "rough_bpm": 120.0,
        "filename": "morning.wav", "folder": "upbeat", "mood_tags": ["upbeat"],
    }]


def test_select_track_is_deterministic_and_prefers_mood(monkeypatch):
    tracks = [
        {"name": "quiet", "path": "/music/a.wav", "mood_tags": ["chill"], "duration": 90},
        {"name": "party", "path": "/music/b.wav", "mood_tags": ["upbeat"], "duration": 20},
        {"name": "party-long", "path": "/music/c.wav", "mood_tags": ["upbeat"], "duration": 90},
    ]
    monkeypatch.setattr(music, "list_tracks", lambda _dir: tracks)
    assert music.select_track("/music", mood="upbeat", duration=60)["name"] == "party-long"
    assert music.select_track("/music", mood="upbeat", duration=60)["name"] == "party-long"


def test_list_tracks_rejects_symlinks_outside_configured_root(monkeypatch, tmp_path):
    inside = tmp_path / "inside.wav"
    outside = tmp_path.parent / "outside.wav"
    inside.write_bytes(b"inside")
    outside.write_bytes(b"outside")
    link = tmp_path / "escape.wav"
    link.symlink_to(outside)
    monkeypatch.setattr(music, "_metadata", lambda child, root: {"filename": child.name})
    assert [track["path"] for track in music.list_tracks(str(tmp_path))] == [str(inside.resolve())]


def test_large_music_library_cache_does_not_evict_and_reanalyze(monkeypatch, tmp_path):
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(music.config, "DATA_DIR", cache_dir)
    for index in range(300):
        (tmp_path / f"track-{index:03d}.wav").write_bytes(b"x")
    calls = []
    monkeypatch.setattr(music, "_loudness_and_bpm", lambda _track: calls.append(1) or (-12.0, 120.0))
    monkeypatch.setattr(music, "_ffprobe_duration", lambda _track: 10.0)
    assert len(music.list_tracks(str(tmp_path))) == 300
    assert len(calls) == 300
    assert len(music.list_tracks(str(tmp_path))) == 300
    assert len(calls) == 300
