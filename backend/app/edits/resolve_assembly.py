"""Stdlib-only Resolve operations for script assembly + look application.

Runs INSIDE Resolve Free (via scripts/resolve_free.py, installed as
Workspace > Scripts > Utility > MediaForge Apply Edit) and in unit tests against
a mocked Resolve object.  Every call used here exists in the installed
/opt/resolve/Developer/Scripting/README.txt: CreateProject, ImportMedia,
CreateEmptyTimeline, AppendToTimeline, Timeline.AddMarker/DuplicateTimeline,
TimelineItem.SetName/SetClipColor/GetNodeGraph, Graph.SetLUT/GetLUT,
Project.RefreshLUTList.  One LUT node per clip carries correction+look baked
together (Graph has no AddNode on Free), previewed identically in ffmpeg.
"""
from pathlib import Path
import os
import shutil

MASTER_LUT_DIR = '/opt/resolve/LUT'
BEAT_CLIP_COLORS = ['Blue', 'Teal', 'Orange', 'Purple', 'Green', 'Pink', 'Yellow', 'Navy']
BEAT_MARKER_COLORS = ['Blue', 'Cyan', 'Yellow', 'Purple', 'Green', 'Pink', 'Sky', 'Lemon']


class AssemblyError(RuntimeError): pass


def require(value, operation):
    if not value: raise AssemblyError('Resolve failed: '+operation)
    return value


def _unique(call, base, tries=30):
    """Create something whose name must be unique: base, 'base 2', 'base 3'..."""
    for n in range(1, tries+1):
        name=base if n==1 else '%s %d'%(base,n)
        made=call(name)
        if made: return made,name
    raise AssemblyError('Resolve failed: find a free name for '+base)


def _find_timeline(project, name):
    for i in range(1,int(project.GetTimelineCount())+1):
        tl=project.GetTimelineByIndex(i)
        if tl and tl.GetName()==name: return tl
    return None


def build_project(spec, resolve):
    """New project + one timeline: clips in assembly order, labelled, with markers."""
    pm=require(resolve.GetProjectManager(),'project manager')
    current=pm.GetCurrentProject()
    if current:
        if current.IsRenderingInProgress(): raise ValueError('Resolve is rendering; wait before creating an assembly')
        require(pm.SaveProject(),'save existing project before switching')
    project,pname=_unique(pm.CreateProject,spec['project_name'])
    try:
        for key,value in [('colorScienceMode','davinciYRGB'),('timelineColorSpace','Rec.709 Gamma 2.4'),('outputColorSpace','Rec.709 Gamma 2.4'),('timelineFrameRate',str(spec['fps'])),('timelineResolutionWidth',str(spec['width'])),('timelineResolutionHeight',str(spec['height']))]:
            require(project.SetSetting(key,value),'set '+key)
        mp=require(project.GetMediaPool(),'media pool')
        media={}
        for it in spec['items']:
            if it['path'] not in media:
                imported=require(mp.ImportMedia([it['path']]),'import '+Path(it['path']).name)
                if len(imported)!=1: raise AssemblyError('Resolve returned unexpected imported media count for '+it['path'])
                media[it['path']]=imported[0]
        tl,tname=_unique(lambda n: mp.CreateEmptyTimeline(n),spec['timeline_name'])
        require(project.SetCurrentTimeline(tl),'select timeline')
        start=int(tl.GetStartFrame()); cursor=start
        offsets=[]
        for it in spec['items']:
            appended=mp.AppendToTimeline([{'mediaPoolItem':media[it['path']],'startFrame':it['start_frame'],'endFrame':it['end_frame'],'recordFrame':cursor}])
            require(appended,'append '+it['label'])
            offsets.append(cursor-start)
            cursor+=it['timeline_frames']
        items=tl.GetItemListInTrack('video',1) or []
        if len(items)!=len(spec['items']): raise AssemblyError('Timeline clip count mismatch')
        if abs(float(tl.GetEndFrame())-cursor)>1: raise AssemblyError('Timeline duration mismatch')
        soft={'names':0,'colors':0,'markers':0}   # labelling is a convenience: count failures, never abort
        for it,tli,off in zip(spec['items'],items,offsets):
            text='%s · %s'%(it['label'],Path(it['path']).stem)
            soft['names']+=0 if tli.SetName(text) else 1
            beat=int(it.get('beat',0))
            soft['colors']+=0 if tli.SetClipColor(BEAT_CLIP_COLORS[beat%len(BEAT_CLIP_COLORS)] if beat else 'Chocolate') else 1
            note='confidence %.2f · %s'%(float(it.get('confidence',0)),it.get('note') or Path(it['path']).name)
            soft['markers']+=0 if tl.AddMarker(off,BEAT_MARKER_COLORS[beat%len(BEAT_MARKER_COLORS)] if beat else 'Red',it['label'],note,1,'mf:%s'%it['order']) else 1
        require(pm.SaveProject(),'save completed project')
        return {'ok':True,'project':pname,'timeline':tname,'resolve':resolve.GetVersionString(),'clip_count':len(items),'frames':cursor-start,'label_failures':soft,'job':spec['job']}
    except Exception:
        pm.SaveProject()   # keep the partial project for diagnosis
        raise


def install_luts(spec):
    """Copy the job's per-clip LUTs into a Resolve-discovered LUT folder; returns {src: installed path}."""
    target=spec.get('lut_dir')
    mapping={}
    for it in spec['items']:
        src=it['lut']
        if not target: mapping[src]=src; continue
        os.makedirs(target,exist_ok=True)
        dst=os.path.join(target,os.path.basename(src))
        if not os.path.exists(dst) or os.path.getsize(dst)!=os.path.getsize(src): shutil.copy2(src,dst)
        mapping[src]=dst
    return mapping


def apply_look(spec, resolve):
    """Duplicate the assembly timeline and apply each clip's baked correction+look LUT to node 1."""
    pm=require(resolve.GetProjectManager(),'project manager')
    current=pm.GetCurrentProject()
    if not current or current.GetName()!=spec['project_name']:
        if current:
            if current.IsRenderingInProgress(): raise ValueError('Resolve is rendering; wait before applying a look')
            require(pm.SaveProject(),'save current project before switching')
        current=require(pm.LoadProject(spec['project_name']),'open project '+spec['project_name'])
    project=current
    src=require(_find_timeline(project,spec['source_timeline']),'find timeline '+spec['source_timeline'])
    require(project.SetCurrentTimeline(src),'select source timeline')
    tl,tname=_unique(lambda n: src.DuplicateTimeline(n),spec['new_timeline'])
    require(project.SetCurrentTimeline(tl),'select graded timeline')
    mapping=install_luts(spec)
    require(project.RefreshLUTList(),'refresh LUT list')
    items=tl.GetItemListInTrack('video',1) or []
    if len(items)!=len(spec['items']): raise AssemblyError('Timeline clip count %d != plan %d; was the assembly edited?'%(len(items),len(spec['items'])))
    failures=[]
    for it,tli in zip(spec['items'],items):
        mpi=tli.GetMediaPoolItem()
        got=mpi.GetName() if mpi else None
        if isinstance(got,str) and Path(got).stem.lower()!=Path(it['path']).stem.lower():
            failures.append({'order':it['order'],'error':'clip mismatch: timeline has %s, plan expects %s'%(got,Path(it['path']).name)}); continue
        graph=tli.GetNodeGraph()
        if not graph or graph.GetNumNodes()<1: failures.append({'order':it['order'],'error':'no colour node'}); continue
        lut=mapping[it['lut']]
        ok=graph.SetLUT(1,lut)
        if not ok and lut.startswith(MASTER_LUT_DIR+'/'): ok=graph.SetLUT(1,lut[len(MASTER_LUT_DIR)+1:])   # relative to master LUT path
        actual=graph.GetLUT(1) if ok else None
        if not (ok and actual and Path(actual).name==Path(lut).name):
            failures.append({'order':it['order'],'error':'SetLUT failed or did not verify for '+Path(lut).name})
    require(pm.SaveProject(),'save project')
    if failures: raise AssemblyError('Applied %d/%d LUTs; failures: %s'%(len(items)-len(failures),len(items),failures[:5]))
    return {'ok':True,'project':project.GetName(),'timeline':tname,'look':spec['look'],'graded':len(items),'resolve':resolve.GetVersionString(),'job':spec['job']}
