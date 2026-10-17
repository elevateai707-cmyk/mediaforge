import asyncio
import io
from pathlib import Path

import pytest
from PIL import Image
from app import creator, models, config
from app.db import SessionLocal


def photo(client, color='blue'):
    data = io.BytesIO()
    Image.new('RGB', (320, 240), color).save(data, 'JPEG')
    r = client.post('/api/creator/upload?filename=example.jpg', content=data.getvalue(), headers={'Content-Type': 'application/octet-stream'})
    assert r.status_code == 200, r.text
    return r.json()['asset_id'], data.getvalue()


def test_upload_duplicate_empty_and_bad_media(client):
    aid, data = photo(client)
    r = client.post('/api/creator/upload?filename=again.jpg', content=data)
    assert r.json() == {'asset_id': aid, 'duplicate': True}
    assert client.post('/api/creator/upload?filename=bad.exe', content=b'x').status_code == 422
    assert client.post('/api/creator/upload?filename=empty.jpg', content=b'').status_code == 422
    assert client.post('/api/creator/upload?filename=broken.jpg', content=b'not an image').status_code == 422
    assert len(list((config.DATA_DIR / 'uploads').glob('*.jpg'))) == 1


def test_brief_fallback_persists_honest_template(client, wait_job, monkeypatch):
    from app.ai import ollama_llm
    monkeypatch.setattr(creator.persist, 'use_cloud_llm', lambda: False)
    def unavailable(*a, **k): raise RuntimeError('offline')
    monkeypatch.setattr(ollama_llm, 'generate', unavailable)
    r = client.post('/api/creator/brainstorm', json={'topic': 'A new workshop', 'camera': 'neo', 'seconds': 24})
    job = wait_job(client, r.json()['job_id'])
    assert job['status'] == 'done', job
    brief = job['meta']['result']
    assert brief['source'] == 'template' and brief['notice']
    assert len(brief['creative']['hooks']) == 3
    assert sum(s['seconds'] for s in brief['creative']['shots']) == 24
    assert 'no obstacle avoidance' in brief['camera']['settings']
    assert '#D4AF37' in ' '.join(brief['creative']['edit_notes'])
    assert brief['id'] in [b['id'] for b in client.get('/api/creator/briefs').json()]


def test_ai_brief_timing_normalized(client, wait_job, monkeypatch):
    from app.ai import ollama_llm
    result = creator.creative_fallback(creator.BriefRequest(topic='Test original footage', seconds=60))
    monkeypatch.setattr(creator.persist, 'use_cloud_llm', lambda: False)
    monkeypatch.setattr(ollama_llm, 'generate', lambda *a, **k: result.model_dump_json())
    r = client.post('/api/creator/brainstorm', json={'topic': 'Test original footage', 'seconds': 20})
    b = wait_job(client, r.json()['job_id'])['meta']['result']
    assert b['source'] == 'local AI'
    assert sum(s['seconds'] for s in b['creative']['shots']) == 20


def test_analysis_coverage_cache_and_missing_model(client, wait_job, monkeypatch):
    aid, _ = photo(client, 'green')
    monkeypatch.setattr(creator, 'vision', lambda path: ('Green test surface', 'mock local vision'))
    r = client.post('/api/creator/analyse', json={'asset_ids': [aid]})
    assert wait_job(client, r.json()['job_id'])['status'] == 'done'
    report = client.get(f'/api/creator/analysis/{aid}').json()
    assert report['status'] == 'complete' and report['coverage']['visual_samples'] == 1
    def fail(path): raise RuntimeError('missing model')
    monkeypatch.setattr(creator, 'vision', fail)
    assert creator.analyse_one(creator.assets_for([aid])[0]) == report
    refreshed = creator.analyse_one(creator.assets_for([aid])[0], refresh=True)
    assert refreshed['status'] == 'partial' and refreshed['coverage']['visual_samples'] == 0
    assert refreshed['warnings'] and refreshed['samples'][0]['caption'] is None


def test_batch_isolates_errors_and_cancel_releases_lock(client, wait_job, monkeypatch):
    aid, _ = photo(client, 'red')
    def fail(*args): raise ValueError('Missing drive')
    monkeypatch.setattr(creator, 'analyse_one', fail)
    r = client.post('/api/creator/analyse', json={'asset_ids': [aid]})
    job = wait_job(client, r.json()['job_id'])
    assert len(job['meta']['result']['errors']) == 1
    assert not creator.ANALYSIS_LOCK.locked()
    def cancelled(*args): raise asyncio.CancelledError()
    monkeypatch.setattr(creator, 'analyse_one', cancelled)
    r = client.post('/api/creator/analyse', json={'asset_ids': [aid]})
    assert wait_job(client, r.json()['job_id'])['status'] == 'cancelled'
    assert not creator.ANALYSIS_LOCK.locked()
    assert client.post('/api/creator/analyse', json={}).status_code == 422
    assert client.post('/api/creator/jobs/notfound/cancel').status_code == 404


def test_broll_bounds_brand_approval_and_original_untouched(client):
    aid, _ = photo(client, 'yellow')
    original = creator.assets_for([aid])[0]
    before = Path(original['path']).read_bytes()
    r = client.post('/api/creator/broll', json={'asset_ids': [aid], 'days': 2, 'seconds': 15, 'fit': 'cover'})
    assert r.status_code == 200, r.text
    assert len(r.json()['plans']) == 2
    for plan in r.json()['plans']:
        assert plan['duration'] == 3  # never pad unknown footage to the requested duration
        env = client.get('/api/editor/' + plan['plan_id']).json()
        assert env['approved_revision'] is None
        doc = env['project']
        assert doc['export']['fit'] == 'cover'
        assert doc['caption_style']['color'] == '#D4AF37'
        assert doc['caption_style']['preset'] == 'kinetic-db'
        assert client.post('/api/editor/' + plan['plan_id'] + '/render').status_code == 409
    assert Path(original['path']).read_bytes() == before


def test_reference_media_cannot_enter_production(client):
    aid, _ = photo(client, 'orange')
    with SessionLocal() as db:
        a = db.get(models.Asset, aid); original = a.path
        a.path = '/example/video-hook-references/examples/reference.mp4'; db.commit()
    try:
        assert client.post('/api/creator/broll', json={'asset_ids': [aid]}).status_code == 422
        assert client.post('/api/creator/still', json={'asset_id': aid}).status_code == 422
        assert client.post('/api/creator/brainstorm', json={'asset_ids': [aid], 'topic': 'test idea'}).status_code == 422
    finally:
        with SessionLocal() as db:
            db.get(models.Asset, aid).path = original; db.commit()


def test_still_crop_registered(client):
    aid, _ = photo(client, 'purple')
    r = client.post('/api/creator/still', json={'asset_id': aid, 'ratio': '4:5'})
    assert r.status_code == 200, r.text
    path = creator.assets_for([r.json()['asset_id']])[0]['path']
    with Image.open(path) as im: assert im.size == (192, 240)
    assert client.post('/api/creator/still', json={'asset_id': aid, 'at': -1}).status_code == 422


def test_resolve_does_not_silently_drop_crop(monkeypatch):
    from app.edits import resolve_workflow as w
    doc = {'revision': 2, 'clips': [{'asset_id': 1, 'start': 0, 'end': 2, 'transition': 'cut'}], 'export': {'fit': 'cover'}}
    monkeypatch.setattr(w.documents, 'snapshot', lambda *a, **k: (doc, {}))
    with pytest.raises(ValueError, match='Fit framing'): w.prepare('fixture', 2)
