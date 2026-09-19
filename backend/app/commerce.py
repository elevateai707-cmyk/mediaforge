"""Sandbox-only commerce ledger. All routes require a private service credential.

Run worker separately: python -m app.commerce_worker
SQLite transactions serialize payment/upload/finalize transitions; uploaded files
are immutable once a job is queued. This module never trusts browser source paths.
"""
import hashlib
import hmac
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

ROOT = Path(os.environ.get('MF_COMMERCE_ROOT', str(Path(__file__).resolve().parents[2] / 'data/commerce-sandbox')))
MAX_BYTES = 2 * 1024**3
MAX_ORDER_BYTES = 20 * 1024**3
MAX_FILES = 40

def db():
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    ROOT.chmod(0o700)
    c = sqlite3.connect(ROOT / 'orders.sqlite', timeout=30)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS orders (
      id TEXT PRIMARY KEY, token_hash TEXT NOT NULL, created REAL NOT NULL,
      session TEXT UNIQUE, payment TEXT NOT NULL DEFAULT 'pending',
      status TEXT NOT NULL DEFAULT 'awaiting_payment', email TEXT,
      attempts INTEGER NOT NULL DEFAULT 0, error TEXT, heartbeat REAL,
      files TEXT NOT NULL DEFAULT '[]');
    CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, created REAL NOT NULL);
    ''')
    return c

def require_service(authorization: str = Header(default='')):
    secret = os.environ.get('MF_COMMERCE_SECRET', '')
    if len(secret) < 32 or not hmac.compare_digest(authorization, 'Bearer ' + secret):
        raise HTTPException(401, 'Service authentication required')

router = APIRouter(prefix='/commerce', dependencies=[Depends(require_service)])

def get_order(c, oid, token=None):
    row = c.execute('SELECT * FROM orders WHERE id=?', (oid,)).fetchone()
    if not row or (token is not None and not hmac.compare_digest(row['token_hash'], hashlib.sha256(token.encode()).hexdigest())):
        raise HTTPException(404, 'Order not found')
    return dict(row)

def owned(c, oid, token):
    if not token:
        raise HTTPException(401, 'Order access required')
    return get_order(c, oid, token)

class NewOrder(BaseModel):
    token_hash: str = Field(pattern=r'^[a-f0-9]{64}$')

@router.post('/orders')
def new_order(body: NewOrder):
    oid = str(uuid.uuid4())
    with db() as c:
        c.execute('INSERT INTO orders(id,token_hash,created) VALUES (?,?,?)', (oid, body.token_hash, time.time()))
    return {'id': oid}

class SessionBinding(BaseModel):
    session: str = Field(pattern=r'^cs_test_[A-Za-z0-9]+$')

@router.post('/orders/{oid}/session')
def bind(oid: str, body: SessionBinding):
    with db() as c:
        order = get_order(c, oid)
        if order['session'] and order['session'] != body.session:
            raise HTTPException(409, 'Session already bound')
        c.execute('UPDATE orders SET session=? WHERE id=?', (body.session, oid))
    return {'ok': True}

class PaymentEvent(BaseModel):
    id: str
    type: str
    livemode: bool
    session: str
    order_id: str
    mode: str
    product: str
    currency: str
    amount: int
    paid: bool
    email: str = ''

@router.post('/events')
def payment_event(e: PaymentEvent):
    if e.livemode or not e.session.startswith('cs_test_') or e.product != 'ai-reel-pack' or e.mode != 'payment' or e.currency != 'cad' or e.amount != 14900:
        raise HTTPException(400, 'Only the CAD 149 sandbox Reel Pack is enabled')
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        order = get_order(c, e.order_id)
        if order['session'] != e.session:
            raise HTTPException(409, 'Checkout session mismatch')
        if c.execute('SELECT 1 FROM events WHERE id=?', (e.id,)).fetchone():
            return {'duplicate': True}
        if e.type in ('checkout.session.completed', 'checkout.session.async_payment_succeeded') and e.paid:
            c.execute("UPDATE orders SET payment='paid',status=CASE WHEN status='awaiting_payment' THEN 'awaiting_upload' ELSE status END,email=? WHERE id=?", (e.email, e.order_id))
        elif e.type in ('checkout.session.async_payment_failed', 'checkout.session.expired') and order['payment'] != 'paid':
            c.execute("UPDATE orders SET payment='failed' WHERE id=?", (e.order_id,))
        c.execute('INSERT INTO events VALUES (?,?)', (e.id, time.time()))
    return {'received': True}

@router.get('/orders/{oid}')
def status(oid: str, x_order_token: str = Header(default='')):
    with db() as c:
        o = owned(c, oid, x_order_token)
    return {k: o[k] for k in ('id','payment','status','attempts','error')} | {'files': len(json.loads(o['files'])), 'sandbox': True}

@router.post('/orders/{oid}/files')
async def upload(oid: str, request: Request, x_order_token: str = Header(default=''), x_filename: str = Header(default='')):
    # Claim one upload slot without holding a DB transaction during network I/O.
    suffix = Path(x_filename).suffix.lower()
    if suffix not in ('.mp4', '.mov'):
        raise HTTPException(400, 'MP4 or MOV required')
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        o = owned(c, oid, x_order_token)
        if o['payment'] != 'paid': raise HTTPException(403, 'Confirmed payment required')
        if o['status'] != 'awaiting_upload': raise HTTPException(409, 'Order is not accepting files')
        files = json.loads(o['files'])
        if len(files) >= MAX_FILES: raise HTTPException(413, 'File limit reached')
        c.execute("UPDATE orders SET status='uploading',heartbeat=? WHERE id=?", (time.time(), oid))
    folder = ROOT / 'inbox' / oid
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / (uuid.uuid4().hex + suffix)
    part = dest.with_suffix('.part')
    size = 0
    try:
        with part.open('xb') as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_BYTES or size + sum(x['size'] for x in files) > MAX_ORDER_BYTES:
                    raise HTTPException(413, 'Upload quota exceeded')
                f.write(chunk)
                with db() as c: c.execute('UPDATE orders SET heartbeat=? WHERE id=?', (time.time(), oid))
        from .commerce_render import probe
        info = probe(part)
        if not {'mov','mp4'} & set(info['format'].split(',')) or size == 0 or info['duration'] < 10.5 or info['width'] < 320 or info['height'] < 320:
            raise HTTPException(400, 'Video must have at least 10.5 seconds of usable footage')
        part.rename(dest)
        files.append({'name': dest.name, 'size': size, 'duration': info['duration']})
        with db() as c:
            c.execute("UPDATE orders SET files=?,status='awaiting_upload',error=NULL WHERE id=?", (json.dumps(files), oid))
        return {'files': len(files)}
    except Exception as exc:
        part.unlink(missing_ok=True)
        dest.unlink(missing_ok=True)
        with db() as c: c.execute("UPDATE orders SET status='awaiting_upload' WHERE id=? AND status='uploading'", (oid,))
        if isinstance(exc, HTTPException): raise
        raise HTTPException(400, 'Invalid or unreadable media') from exc

@router.post('/orders/{oid}/submit')
def submit(oid: str, x_order_token: str = Header(default='')):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        o = owned(c, oid, x_order_token)
        if o['payment'] != 'paid': raise HTTPException(403, 'Confirmed payment required')
        if o['status'] in ('queued','rendering','ready'): return {'status': o['status']}
        if o['status'] != 'awaiting_upload': raise HTTPException(409, 'Order cannot be submitted')
        if len(json.loads(o['files'])) < 10: raise HTTPException(400, 'Upload at least ten eligible clips')
        c.execute("UPDATE orders SET status='queued',error=NULL WHERE id=?", (oid,))
    return {'status': 'queued'}

@router.post('/orders/{oid}/retry')
def retry(oid: str, x_order_token: str = Header(default='')):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        o = owned(c, oid, x_order_token)
        if o['payment'] != 'paid' or o['status'] != 'failed' or o['attempts'] >= 3:
            raise HTTPException(409, 'Retry unavailable')
        c.execute("UPDATE orders SET status='queued',error=NULL WHERE id=?", (oid,))
    return {'status': 'queued'}

@router.get('/orders/{oid}/download')
def download(oid: str, x_order_token: str = Header(default='')):
    with db() as c: o = owned(c, oid, x_order_token)
    if o['payment'] != 'paid' or o['status'] != 'ready': raise HTTPException(409, 'Download is not ready')
    return FileResponse(ROOT / 'renders' / oid / 'ai-reel-pack.zip', media_type='application/zip', filename='ai-reel-pack.zip', headers={'Cache-Control':'private, no-store'})
