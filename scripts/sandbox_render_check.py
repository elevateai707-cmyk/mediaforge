"""Controlled real encoder failure + retry for the locally signed fixture E2E.
Run from backend: .venv/bin/python ../scripts/sandbox_render_check.py
"""
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.commerce import db,ROOT
from app.commerce_worker import work_once
access=json.loads(Path('/home/bfam/store-review/sandbox-evidence/access.json').read_text());oid=access['id']
with db() as c:
    o=c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
    assert o['status']=='queued' and o['attempts']==0
    files=json.loads(o['files'])
p=ROOT/'inbox'/oid/files[0]['name']; saved=p.with_suffix('.failure-test-backup');p.rename(saved)
try:
    assert work_once()
    with db() as c: state=dict(c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone())
    assert state['status']=='failed' and state['payment']=='paid' and state['attempts']==1
    try:
        urllib.request.urlopen(urllib.request.Request(f'http://localhost:3012/api/orders/{oid}/download',headers={'Cookie':access['cookie']}))
        raise AssertionError('Failed render was downloadable')
    except urllib.error.HTTPError as exc: assert exc.code==409
    print('REAL RENDER FAILURE: paid order retained; download denied; attempt 1',flush=True)
finally:
    saved.rename(p)
r=urllib.request.urlopen(urllib.request.Request(f'http://localhost:3012/api/orders/{oid}/retry',method='POST',headers={'Cookie':access['cookie']}))
assert r.status==200
print('RETRY: same paid order queued; rendering real footage now',flush=True)
assert work_once()
with db() as c:
    o=dict(c.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone())
    print({k:o[k] for k in ('id','status','attempts','error')},flush=True)
assert o['status']=='ready' and o['attempts']==2
