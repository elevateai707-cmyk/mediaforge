"""Checked, non-destructive Rec709 timelines. No source transcoding or Log conversion."""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import uuid
from fractions import Fraction
from . import project as documents
from . import resolve_export as bridge
from .. import config
from ..proc import tool_env

LUT_ROOT = config.MF_ROOT / 'assets' / 'luts'

def luts():
    return [{'id': p.stem, 'input': 'rec709', 'path': str(p)} for p in sorted(LUT_ROOT.glob('*.cube')) if p.is_file()]

def lut_path(lut_id):
    if lut_id is None: return None
    match = next((x for x in luts() if x['id'] == lut_id), None)
    if not match: raise ValueError('Unknown LUT. Choose an ID from /api/resolve/luts')
    return Path(match['path']).resolve()

def probe(path):
    result = subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',path],capture_output=True,text=True,timeout=30,env=tool_env(),check=True)
    data = json.loads(result.stdout)
    video = next((s for s in data['streams'] if s.get('codec_type')=='video'),None)
    if not video: raise ValueError('Source has no video stream')
    tags = json.dumps(data).lower()
    if any(x in tags for x in ['apple log','applelog','smpte2084','arib-std-b67']) or video.get('color_primaries') in ('bt2020','smpte432'):
        raise ValueError('Log/HDR/P3 source rejected by direct Rec709 workflow. No conversion was applied.')
    fps = float(Fraction(video.get('avg_frame_rate','0/1')))
    nominal = float(Fraction(video.get('r_frame_rate','0/1')))
    if not math.isfinite(fps) or fps<=0 or abs(fps-nominal)>.02:
        raise ValueError('Variable or unknown frame rate: direct frame-accurate conform cannot be guaranteed')
    duration = float(video.get('duration') or data.get('format',{}).get('duration',0))
    if not duration>0: raise ValueError('Unknown source duration')
    return {'fps':fps,'duration':duration,'codec':video.get('codec_name'),'transfer':video.get('color_transfer','unspecified'),'has_audio':any(s.get('codec_type')=='audio' for s in data['streams'])}

def prepare(plan_id, revision, lut_id=None, source_color='rec709'):
    if source_color!='rec709': raise ValueError('This workflow accepts declared Rec709 sources only')
    doc, paths = documents.snapshot(plan_id,approved=False)
    p = documents.Project.model_validate(doc)
    if p.revision!=revision: raise ValueError('Project revision changed; reload before exporting')
    if not p.clips: raise ValueError('Select footage first')
    if p.speech_captions or p.on_video_text or p.music.enabled or p.voice_over.enabled or not p.original_audio.enabled or p.original_audio.volume!=1:
        raise ValueError('Direct Resolve conform currently supports cuts, original audio and one LUT. Disable overlays, captions and audio mixing first.')
    if any(c.speed!=1 or c.transition!='cut' for c in p.clips):
        raise ValueError('Direct Resolve conform requires speed 1 and cut transitions; no effects will be silently dropped')
    lut = lut_path(lut_id)
    info={}
    for aid, src in paths.items():
        if src['kind']!='video' or not Path(src['path']).is_file(): raise ValueError('Only existing registered video sources are supported')
        info[aid]=probe(src['path'])
    fps = p.export.fps or info[p.clips[0].asset_id]['fps']
    clips=[]
    for c in p.clips:
        i=info[c.asset_id]
        if c.end>i['duration']+.001: raise ValueError('Trim exceeds source duration')
        # Resolve endFrame is inclusive. Source in/out are seconds, end-exclusive.
        start=round(c.start*i['fps']); end=round(c.end*i['fps'])-1
        if end<start: raise ValueError('Trim shorter than one source frame')
        clips.append({'asset_id':c.asset_id,'path':paths[c.asset_id]['path'],'start':start,'end':end,'source_fps':i['fps'],'timeline_frames':max(1,round((end-start+1)/i['fps']*fps))})
    ratio=[int(x) for x in p.export.ratio.split(':')]
    height=round(p.export.width*ratio[1]/ratio[0]/2)*2
    return {'plan_id':plan_id,'revision':revision,'fps':fps,'width':p.export.width,'height':height,'clips':clips,'lut':str(lut) if lut else None,'source_color':'rec709','transcoded':False,'notes':['Input Rec709 is declared by the caller; untagged footage cannot be identified reliably.','Cuts, original audio and creative LUT only. No captions, music, retiming or transitions.']}

from .resolve_native import NativeError, require
from .resolve_native import conform as native_conform

def conform(spec, resolve):
    try: return native_conform(spec, resolve)
    except NativeError as e: raise bridge.ResolveUnavailable(str(e)) from e


def export(plan_id,revision,lut_id=None,source_color='rec709'):
    spec=prepare(plan_id,revision,lut_id,source_color)
    with bridge._export_lock:
        result=conform(spec,bridge._connect(retries=1))
        token=uuid.uuid4().hex
        folder=config.EXPORTS_DIR/'resolve';folder.mkdir(parents=True,exist_ok=True)
        (folder/(token+'.json')).write_text(json.dumps(result,indent=2))
        return {**result,'export_id':token}

def manifest(export_id):
    if len(export_id)!=32 or any(c not in '0123456789abcdef' for c in export_id): raise ValueError('Invalid export ID')
    file=config.EXPORTS_DIR/'resolve'/(export_id+'.json')
    if not file.is_file(): raise ValueError('Export not found')
    return json.loads(file.read_text())

def render(export_id,preset):
    result=manifest(export_id)
    with bridge._export_lock:
        r=bridge._connect(retries=1);pm=r.GetProjectManager()
        current=pm.GetCurrentProject()
        if current:
            if current.IsRenderingInProgress(): raise ValueError('Resolve is rendering; wait before switching projects')
            require(pm.SaveProject(),'save current project')
        p=require(pm.LoadProject(result['project']),'load exported project')
        if p.IsRenderingInProgress(): raise ValueError('Resolve is already rendering; wait for completion')
        presets=p.GetRenderPresetList() or []
        if preset not in presets: raise ValueError('Choose an installed render preset from status')
        require(p.LoadRenderPreset(preset),'load render preset')
        output=config.EXPORTS_DIR/'resolve'/export_id/uuid.uuid4().hex[:8];output.mkdir(parents=True)
        require(p.SetCurrentRenderMode(1),'single-clip render mode')
        require(p.SetRenderSettings({'TargetDir':str(output),'CustomName':'MediaForge','SelectAllFrames':True,'ExportVideo':True,'ExportAudio':True}),'render settings')
        job=require(p.AddRenderJob(),'queue render')
        require(p.StartRendering([job]),'start render')
        require(pm.SaveProject(),'save render job')
        (config.EXPORTS_DIR/'resolve'/(export_id+'-render-'+hashlib.sha256(job.encode()).hexdigest()+'.json')).write_text(json.dumps({'job_id':job,'output_dir':str(output)}))
        return {'export_id':export_id,'job_id':job,'project':result['project'],'output_dir':str(output),'status':'started'}

def render_status(export_id,job_id):
    result=manifest(export_id)
    with bridge._export_lock:
        p=bridge._connect(retries=1).GetProjectManager().GetCurrentProject()
        if not p or p.GetName()!=result['project']: raise ValueError('Exported project must remain open while checking its render')
        status=require(p.GetRenderJobStatus(job_id),'find render job')
        record=config.EXPORTS_DIR/'resolve'/(export_id+'-render-'+hashlib.sha256(job_id.encode()).hexdigest()+'.json')
        if not record.is_file(): raise ValueError('Unknown render job for this export')
        output=Path(json.loads(record.read_text())['output_dir'])
        files=[str(x) for x in output.rglob('*') if x.is_file() and x.stat().st_size>0]
        return {'job':status,'files':files,'verified_output':status.get('JobStatus')=='Complete' and bool(files)}
