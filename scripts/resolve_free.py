"""Run INSIDE Resolve (Workspace > Scripts > Utility > MediaForge Apply Edit).
Processes one queued edit or checks a prior native render. No pip dependency.
"""
import importlib.util
import json
import os
from pathlib import Path
ROOT=Path('/home/bfam/mediaforge')
spec=importlib.util.spec_from_file_location('mediaforge_native',ROOT/'backend/app/edits/resolve_native.py')
native=importlib.util.module_from_spec(spec);spec.loader.exec_module(native)
QUEUE=ROOT/'data/resolve_queue'

def write_result(path,data):
 temp=path.with_suffix('.tmp');temp.write_text(json.dumps(data,indent=2));temp.chmod(0o600);os.replace(temp,path)

def run(resolve):
 if resolve is None:raise RuntimeError('Run this script inside Resolve with a Resolve object')
 QUEUE.mkdir(parents=True,exist_ok=True)
 # Refresh results for a render in the currently open project; never switch away mid-render.
 current=resolve.GetProjectManager().GetCurrentProject()
 for result in QUEUE.glob('*.result.json'):
  data=json.loads(result.read_text())
  if data.get('status')=='rendering' and current and data['project']==current.GetName():
   job=current.GetRenderJobStatus(data['job_id']);data['render_job']=job
   files=[str(p) for p in Path(data['output_dir']).rglob('*') if p.is_file() and p.stat().st_size>0]
   if job.get('JobStatus')=='Complete':data.update(status='completed' if files else 'error',files=files)
   elif job.get('JobStatus') in ('Failed','Cancelled'):data['status']='error'
   write_result(result,data)
 if current and current.IsRenderingInProgress(): print('MediaForge: render still in progress.');return
 pending=sorted(QUEUE.glob('*.pending.json'),key=lambda p:p.stat().st_mtime)
 if not pending:print('MediaForge: no pending edit; render statuses refreshed.');return
 path=pending[0];running=path.with_name(path.name.replace('.pending.','.running.'))
 try:os.rename(path,running)
 except FileNotFoundError:print('Queue item already claimed.');return
 data=json.loads(running.read_text());result=path.with_name(path.name.replace('.pending.','.result.'))
 try:
  output=native.conform(data['spec'],resolve);output.update(queue_id=data['queue_id'],status='timeline_ready')
  if data.get('render_preset'):
   p=resolve.GetProjectManager().GetCurrentProject();preset=data['render_preset']
   native.require(preset in (p.GetRenderPresetList() or []),'find installed render preset '+preset)
   native.require(p.LoadRenderPreset(preset),'load render preset')
   out=Path(data['output_dir']);out.mkdir(parents=True,exist_ok=False)
   native.require(p.SetCurrentRenderMode(1),'single clip mode')
   native.require(p.SetRenderSettings({'TargetDir':str(out),'CustomName':'MediaForge','SelectAllFrames':True,'ExportVideo':True,'ExportAudio':True}),'set render output')
   job=native.require(p.AddRenderJob(),'queue render');native.require(p.StartRendering([job]),'start render')
   output.update(status='rendering',job_id=job,output_dir=str(out))
  write_result(result,output)
  print('MediaForge:',output['status'],output['project'])
 except Exception as exc:
  write_result(result,{'queue_id':data['queue_id'],'status':'error','error':str(exc),'notice':'Partial project may exist. Inspect before queueing a new attempt.'});raise

if __name__=='__main__':
 app=globals().get('resolve')
 if app is None:
  try:app=bmd.scriptapp('Resolve')
  except NameError:
   import DaVinciResolveScript as dvr
   app=dvr.scriptapp('Resolve')
 run(app)
