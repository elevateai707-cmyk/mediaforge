"""Single local GPU worker. OS lock prevents competing worker processes.
Unfinished renders are safely requeued after a worker restart; attempts bounded.
"""
import fcntl
import json
import time
import subprocess
import os
import signal
import sys
from .commerce import ROOT, db

def bounded_render(inbox, output, files):
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / 'input-manifest.json'
    manifest.write_text(json.dumps(files))
    command = [sys.executable, '-m', 'app.commerce_render', str(inbox), str(output), str(manifest)]
    process = subprocess.Popen(command, start_new_session=True)
    try:
        code = process.wait(timeout=7200)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        raise
    if code:
        raise subprocess.CalledProcessError(code, command)

def work_once(renderer=bounded_render):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        o = c.execute("SELECT * FROM orders WHERE status='queued' AND payment='paid' ORDER BY created LIMIT 1").fetchone()
        if not o: return False
        oid = o['id']
        c.execute("UPDATE orders SET status='rendering',attempts=attempts+1,heartbeat=? WHERE id=?", (time.time(), oid))
    try:
        renderer(ROOT / 'inbox' / oid, ROOT / 'renders' / oid, json.loads(o['files']))
        with db() as c: c.execute("UPDATE orders SET status='ready',error=NULL WHERE id=?", (oid,))
    except Exception:
        with db() as c: c.execute("UPDATE orders SET status='failed',error='Rendering failed. Your payment is retained; retry this job without paying again.' WHERE id=?", (oid,))
        import traceback
        traceback.print_exc()
    return True

def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    with (ROOT / 'worker.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with db() as c:
            c.execute("UPDATE orders SET status=CASE WHEN attempts<3 THEN 'queued' ELSE 'failed' END WHERE status='rendering'")
        while True:
            with db() as c:
                c.execute("UPDATE orders SET status='awaiting_upload' WHERE status='uploading' AND heartbeat<?", (time.time()-3600,))
            if not work_once(): time.sleep(2)

if __name__ == '__main__': main()
