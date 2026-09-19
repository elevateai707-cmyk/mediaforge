import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import commerce as c
from app import commerce_worker as w

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(c,'ROOT',tmp_path)
    monkeypatch.setattr(w,'ROOT',tmp_path)
    monkeypatch.setenv('MF_COMMERCE_SECRET','test-service-'+'a'*32)
    app=FastAPI(); app.include_router(c.router)
    return TestClient(app,headers={'Authorization':'Bearer test-service-'+'a'*32})

def order(client):
    token='customer-secret'
    oid=client.post('/commerce/orders',json={'token_hash':hashlib.sha256(token.encode()).hexdigest()}).json()['id']
    client.post(f'/commerce/orders/{oid}/session',json={'session':'cs_test_'+oid.replace('-','')})
    return oid, {'x-order-token':token}

def event(oid, **kwargs):
    return dict(id='evt_first',type='checkout.session.completed',livemode=False,session='cs_test_'+oid.replace('-',''),order_id=oid,mode='payment',product='ai-reel-pack',currency='cad',amount=14900,paid=True,email='sandbox@example.invalid') | kwargs

def test_unpaid_failure_then_success_never_regresses(client):
    oid,h=order(client)
    assert client.post('/commerce/events',json=event(oid,paid=False)).status_code==200
    assert client.post(f'/commerce/orders/{oid}/submit',headers=h).status_code==403
    assert client.post('/commerce/events',json=event(oid,id='evt_fail',type='checkout.session.async_payment_failed',paid=False)).status_code==200
    assert client.get(f'/commerce/orders/{oid}',headers=h).json()['payment']=='failed'
    client.post('/commerce/events',json=event(oid,id='evt_paid',type='checkout.session.async_payment_succeeded'))
    client.post('/commerce/events',json=event(oid,id='evt_latefail',type='checkout.session.async_payment_failed',paid=False))
    assert client.get(f'/commerce/orders/{oid}',headers=h).json()['payment']=='paid'

def test_duplicate_concurrent_events_and_submit(client):
    oid,h=order(client)
    with ThreadPoolExecutor(6) as pool:
        replies=list(pool.map(lambda _:client.post('/commerce/events',json=event(oid)),range(12)))
    assert all(r.status_code==200 for r in replies)
    assert sum(r.json().get('duplicate',False) for r in replies)==11
    with c.db() as db:
        db.execute('UPDATE orders SET files=? WHERE id=?',(json.dumps([{'name':str(i)} for i in range(10)]),oid))
    for _ in range(3): assert client.post(f'/commerce/orders/{oid}/submit',headers=h).json()['status']=='queued'
    count=[]
    assert w.work_once(lambda *args:count.append(1))
    assert not w.work_once(lambda *args:count.append(1))
    assert count==[1]
    client.post('/commerce/events',json=event(oid,id='evt_other_success',type='checkout.session.async_payment_succeeded'))
    assert client.get(f'/commerce/orders/{oid}',headers=h).json()['status']=='ready'

def test_live_subscription_wrong_amount_and_session_rejected(client):
    oid,h=order(client)
    for changes in ({'livemode':True},{'mode':'subscription'},{'amount':1},{'currency':'usd'},{'product':'monthly-engine'},{'session':'cs_test_other'}):
        assert client.post('/commerce/events',json=event(oid,**changes)).status_code in (400,409)
    assert client.get(f'/commerce/orders/{oid}',headers=h).json()['payment']=='pending'

def test_order_isolation_and_service_auth(client):
    oid,h=order(client)
    assert client.get(f'/commerce/orders/{oid}').status_code==401
    assert client.get(f'/commerce/orders/{oid}',headers={'x-order-token':'wrong'}).status_code==404
    assert client.get(f'/commerce/orders/{oid}',headers=h|{'authorization':'Bearer wrong'}).status_code==401
    assert client.get(f'/commerce/orders/{oid}/download',headers=h).status_code==409
    assert client.post(f'/commerce/orders/{oid}/files',headers=h|{'x-filename':'a.mp4'},content=b'bad').status_code==403

def test_invalid_upload_and_too_few_inputs(client):
    oid,h=order(client);client.post('/commerce/events',json=event(oid))
    assert client.post(f'/commerce/orders/{oid}/files',headers=h|{'x-filename':'../../a.exe'},content=b'bad').status_code==400
    assert client.post(f'/commerce/orders/{oid}/files',headers=h|{'x-filename':'a.mp4'},content=b'bad').status_code==400
    assert client.get(f'/commerce/orders/{oid}',headers=h).json()['status']=='awaiting_upload'
    assert client.post(f'/commerce/orders/{oid}/submit',headers=h).status_code==400

def test_render_failure_retry_without_payment_or_early_download(client):
    oid,h=order(client);client.post('/commerce/events',json=event(oid))
    with c.db() as db:db.execute("UPDATE orders SET status='queued' WHERE id=?",(oid,))
    def fail(*args): raise RuntimeError('encoder unavailable')
    assert w.work_once(fail)
    state=client.get(f'/commerce/orders/{oid}',headers=h).json()
    assert state['status']=='failed' and state['payment']=='paid' and state['attempts']==1
    assert client.get(f'/commerce/orders/{oid}/download',headers=h).status_code==409
    assert client.post(f'/commerce/orders/{oid}/retry',headers=h).status_code==200
    def success(inbox,out,files):
        out.mkdir(parents=True);(out/'ai-reel-pack.zip').write_bytes(b'zip-test')
    assert w.work_once(success)
    assert client.get(f'/commerce/orders/{oid}/download',headers=h).content==b'zip-test'

def test_stream_quota_rejection_recovers_upload_slot(client,monkeypatch):
    oid,h=order(client);client.post('/commerce/events',json=event(oid))
    monkeypatch.setattr(c,'MAX_BYTES',3)
    response=client.post(f'/commerce/orders/{oid}/files',headers=h|{'x-filename':'test.mp4'},content=b'1234')
    assert response.status_code==413
    state=client.get(f'/commerce/orders/{oid}',headers=h).json()
    assert state['status']=='awaiting_upload' and state['files']==0
    assert not list((c.ROOT/'inbox'/oid).iterdir())

def test_retry_limit_and_uploads_locked_after_submit(client):
    oid,h=order(client);client.post('/commerce/events',json=event(oid))
    with c.db() as db:db.execute("UPDATE orders SET status='failed',attempts=3 WHERE id=?",(oid,))
    assert client.post(f'/commerce/orders/{oid}/retry',headers=h).status_code==409
    with c.db() as db:db.execute("UPDATE orders SET status='queued' WHERE id=?",(oid,))
    assert client.post(f'/commerce/orders/{oid}/files',headers=h|{'x-filename':'test.mp4'},content=b'bad').status_code==409
