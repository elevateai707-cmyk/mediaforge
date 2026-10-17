"""Regression coverage for adding media after setup and cross-machine styles."""
import time
from PIL import Image
from app import models, persist
from app.db import SessionLocal
from app.ingest import watcher
from app.edits.project import Style


def test_settings_enable_watcher_on_server_loop_and_ingest_later(client, tmp_path):
    previous_dirs = persist.media_dirs()
    previous_enabled = persist.watcher_enabled()
    try:
        response = client.put('/api/config', json={'media_dirs': [str(tmp_path)], 'watcher_enabled': True})
        assert response.status_code == 200, response.text
        assert watcher._observer is not None and watcher._observer.is_alive()
        assert persist.media_dirs() == [str(tmp_path)]
        for n in range(2):
            path = tmp_path / f'later-{n}.png'
            Image.new('RGB', (40, 40), (91 + n, 18, 73)).save(path)
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                with SessionLocal() as db:
                    found = db.query(models.Asset).filter_by(path=str(path)).first()
                    if found:
                        break
                time.sleep(.1)
            assert found, f'Watcher did not import {path.name}'
        assert client.put('/api/config', json={'watcher_enabled': False}).status_code == 200
        assert watcher._observer is None
        assert client.put('/api/config', json={'watcher_enabled': True}).status_code == 200
        assert watcher._observer is not None and watcher._observer.is_alive()
    finally:
        client.put('/api/config', json={'media_dirs': previous_dirs, 'watcher_enabled': previous_enabled})


def test_build_identity_and_brand_defaults(client):
    info = client.get('/api/build-info').json()
    assert info['version'] == '2.1.0'
    assert len(info['source_fingerprint']) == 16
    assert info['source_fingerprint'] == client.get('/api/build-info').json()['source_fingerprint']
    assert {'library-upload', 'watched-folders', 'creator-analysis'} <= set(info['features'])
    assert not any(key in info for key in ('api_key', 'media_dirs', 'root'))
    gold = Style(preset='kinetic-db')
    teal = Style(preset='kinetic-teal')
    assert gold.font == teal.font == 'Anton'
    assert gold.color == '#D4AF37' and teal.color == '#48D6CB'
    assert gold.case == 'upper' and gold.key_scale > 1 and gold.background is False
    assert gold.word_mode == 'word' and gold.y == .72
    assert client.get('/api/editor/style-pack').json()['presets']['kinetic-db']['color'] == gold.color
