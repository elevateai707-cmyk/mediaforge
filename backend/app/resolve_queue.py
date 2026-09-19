"""Durable, owner-local queue for scripts launched inside Resolve Free."""
import json,os,uuid
from pathlib import Path
from . import config
from .edits import resolve_workflow as workflow

def root():
 p=config.DATA_DIR/'resolve_queue';p.mkdir(parents=True,exist_ok=True,mode=0o700);return p

def enqueue(spec,render_preset=None):
 uid=uuid.uuid4().hex
 data={'queue_id':uid,'spec':spec,'render_preset':render_preset,'output_dir':str(config.EXPORTS_DIR/'resolve'/uid),'status':'awaiting_resolve'}
 p=root()/(uid+'.pending.json');temp=p.with_suffix('.tmp');temp.write_text(json.dumps(data));temp.chmod(0o600);os.replace(temp,p)
 return {'queue_id':uid,'status':'awaiting_resolve','next_step':'In Resolve: Workspace > Scripts > Utility > MediaForge Apply Edit. If unavailable in this edition, use Workspace > Console, Python 3, to run the installed script.','render_requested':bool(render_preset)}

def status(uid):
 if len(uid)!=32 or any(c not in '0123456789abcdef' for c in uid):raise ValueError('Invalid queue ID')
 for state in ('result','running','pending'):
  p=root()/(uid+'.'+state+'.json')
  if p.is_file():
   data=json.loads(p.read_text());return {'queue_id':uid,'status':{'result':data.get('status','unknown'),'running':'processing_or_interrupted','pending':'awaiting_resolve'}[state],**({'result':data} if state=='result' else {})}
 raise ValueError('Queue item not found')
