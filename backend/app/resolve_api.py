"""Local-only AI plan -> checked Resolve timeline -> native render."""
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from . import models
from .db import SessionLocal
from .edits import planner,project,resolve_export as bridge,resolve_workflow as workflow
router=APIRouter(prefix='/api/resolve')
class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')
class Plan(Strict):
    asset_ids:list[int]=Field(min_length=1,max_length=100)
    intent:str=Field(min_length=1,max_length=4000)
    fps:int=Field(default=30,ge=15,le=120)
    ratio:Literal['16:9','9:16','1:1']='16:9'
class Export(Strict):
    plan_id:str
    revision:int=Field(ge=1)
    lut_id:str|None=None
    source_color:Literal['rec709']='rec709'
class Render(Strict):
    export_id:str
    preset:str

def call(fn,*args,**kwargs):
    try: return fn(*args,**kwargs)
    except ValueError as e: raise HTTPException(409,str(e))
    except bridge.ResolveUnavailable as e: raise HTTPException(503,str(e))

@router.get('/luts')
def luts(): return {'luts':workflow.luts(),'input':'rec709','manual_conversion_required':False}

@router.get('/status')
def status():
    with bridge._export_lock:
        try:
            r=bridge._connect(retries=1);p=r.GetProjectManager().GetCurrentProject()
            return {'connected':True,'version':r.GetVersionString(),'project':p.GetName() if p else None,'render_presets':p.GetRenderPresetList() if p else [],'workflow':'rec709-direct','conversions':False}
        except bridge.ResolveUnavailable as e: return {'connected':False,'reason':str(e),'workflow':'rec709-direct'}

@router.post('/plan')
def plan(req:Plan):
    # Explicit asset selection; never select unrelated library footage.
    with SessionLocal() as db:
        for aid in req.asset_ids:
            a=db.get(models.Asset,aid)
            if not a or a.kind!='video': raise HTTPException(422,'Select registered video assets only')
    draft=call(planner.create_plan,req.intent,selected_ids=req.asset_ids,local_only=True)
    if not draft['clips']: raise HTTPException(409,'No matching scenes; scan/analyse the selected media first')
    doc=project.Project.model_validate(project.get_project(draft['plan_id'])['project'])
    for c in doc.clips: c.transition='cut'
    doc.export.ratio=req.ratio;doc.export.fps=req.fps;doc.export.hdr='reject'
    saved=call(project.save_project,draft['plan_id'],doc)
    return {'plan_id':draft['plan_id'],**saved,'planner_source':draft.get('match_stats',{}).get('planner_source','unknown'),'notice':'Cuts and original audio; review or adjust the returned editable project. Export takes an explicit revision.'}

@router.post('/preflight')
def preflight(req:Export): return call(workflow.prepare,**req.model_dump())
@router.post('/export')
def export(req:Export): return call(workflow.export,**req.model_dump())
@router.post('/render')
def render(req:Render): return call(workflow.render,**req.model_dump())
@router.get('/render/{export_id}/{job_id}')
def render_status(export_id:str,job_id:str): return call(workflow.render_status,export_id,job_id)

class Queue(Export):
    render_preset:str|None=None
@router.post('/queue')
def queue(req:Queue):
    from . import resolve_queue
    args=req.model_dump(exclude={'render_preset'})
    spec=call(workflow.prepare,**args)
    return call(resolve_queue.enqueue,spec,req.render_preset)
@router.get('/queue/{queue_id}')
def queue_status(queue_id:str):
    from . import resolve_queue
    return call(resolve_queue.status,queue_id)
