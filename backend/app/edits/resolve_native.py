"""Stdlib-only checked Resolve operations; also runs inside Resolve Free."""
from pathlib import Path
import uuid
class NativeError(RuntimeError): pass

def require(value, operation):
    if not value: raise NativeError('Resolve failed: '+operation)
    return value

def conform(spec, resolve):
    pm=require(resolve.GetProjectManager(),'project manager')
    current=pm.GetCurrentProject()
    if current:
        if current.IsRenderingInProgress(): raise ValueError('Resolve is rendering; wait before creating an edit')
        require(pm.SaveProject(),'save existing project before switching')
    name='MF-'+uuid.uuid4().hex[:16]
    project=require(pm.CreateProject(name),'create isolated project')
    try:
        for key,value in [('colorScienceMode','davinciYRGB'),('timelineColorSpace','Rec.709 Gamma 2.4'),('outputColorSpace','Rec.709 Gamma 2.4'),('timelineFrameRate',str(spec['fps'])),('timelineResolutionWidth',str(spec['width'])),('timelineResolutionHeight',str(spec['height']))]:
            require(project.SetSetting(key,value),'set '+key)
        mp=require(project.GetMediaPool(),'media pool')
        media={}
        for clip in spec['clips']:
            if clip['asset_id'] not in media:
                imported=require(mp.ImportMedia([clip['path']]),'import '+Path(clip['path']).name)
                if len(imported)!=1: raise NativeError('Resolve returned unexpected imported media count')
                media[clip['asset_id']]=imported[0]
        tl=require(mp.CreateEmptyTimeline('MediaForge edit'),'create empty timeline')
        require(project.SetCurrentTimeline(tl),'select timeline')
        cursor=int(tl.GetStartFrame())
        for clip in spec['clips']:
            imported=mp.AppendToTimeline([{'mediaPoolItem':media[clip['asset_id']],'startFrame':clip['start'],'endFrame':clip['end'],'recordFrame':cursor}])
            require(imported,'append trimmed source')
            cursor+=clip['timeline_frames']
        items=tl.GetItemListInTrack('video',1) or []
        if len(items)!=len(spec['clips']): raise NativeError('Timeline clip count mismatch')
        if abs(float(tl.GetEndFrame())-cursor)>1: raise NativeError('Timeline duration mismatch')
        if spec['lut']:
            require(project.RefreshLUTList(),'refresh LUT list')
            for item in items:
                graph=require(item.GetNodeGraph(),'clip node graph')
                require(graph.GetNumNodes()>=1,'first colour node')
                require(graph.SetLUT(1,spec['lut']),'apply LUT (must be in a Resolve-discovered LUT directory)')
                actual=graph.GetLUT(1)
                require(actual and Path(actual).name==Path(spec['lut']).name,'verify applied LUT')
        require(pm.SaveProject(),'save completed project')
        return {'ok':True,'project':name,'resolve':resolve.GetVersionString(),'timeline':tl.GetName(),'clip_count':len(items),'frames':cursor-int(tl.GetStartFrame()),**spec}
    except Exception:
        # Preserve partial project for diagnosis; never claim success or append on retry.
        pm.SaveProject()
        raise
