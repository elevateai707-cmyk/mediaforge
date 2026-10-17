from unittest.mock import MagicMock
import pytest
from app.edits import resolve_workflow as w,resolve_export as bridge

def spec():
 return {'plan_id':'test','revision':1,'fps':30,'width':1920,'height':1080,'clips':[{'asset_id':1,'path':'/fixture/a.mov','start':60,'end':119,'source_fps':60,'timeline_frames':30},{'asset_id':2,'path':'/fixture/b.mov','start':30,'end':89,'source_fps':30,'timeline_frames':60}],'lut':None,'source_color':'rec709','transcoded':False}
def fake():
 r=MagicMock();pm=r.GetProjectManager.return_value;p=pm.CreateProject.return_value;pm.GetCurrentProject.return_value=None
 tl=p.GetMediaPool.return_value.CreateEmptyTimeline.return_value;tl.GetStartFrame.return_value=86400;tl.GetEndFrame.return_value=86490;tl.GetItemListInTrack.return_value=[MagicMock(),MagicMock()]
 p.GetMediaPool.return_value.ImportMedia.side_effect=[[MagicMock()],[MagicMock()]]
 return r,p,tl

def test_no_duplicates_and_mixed_frame_rates():
 r,p,tl=fake();result=w.conform(spec(),r);mp=p.GetMediaPool.return_value
 assert result['ok'] and result['clip_count']==2
 mp.CreateTimelineFromClips.assert_not_called();calls=mp.AppendToTimeline.call_args_list;assert len(calls)==2
 assert calls[0].args[0][0]['endFrame']==119 and calls[1].args[0][0]['recordFrame']==86430

def test_no_fallback_to_existing_project():
 r,p,_=fake();r.GetProjectManager.return_value.CreateProject.return_value=None
 with pytest.raises(bridge.ResolveUnavailable):w.conform(spec(),r)
 r.GetProjectManager.return_value.LoadProject.assert_not_called()

def test_failed_import_not_success():
 r,p,_=fake();p.GetMediaPool.return_value.ImportMedia.side_effect=[[]]
 with pytest.raises(bridge.ResolveUnavailable):w.conform(spec(),r)

def test_count_and_lut_failure():
 r,p,tl=fake();tl.GetItemListInTrack.return_value=[]
 with pytest.raises(bridge.ResolveUnavailable,match='count'):w.conform(spec(),r)
 r,p,tl=fake();s=spec();s['lut']='/fixture/grade.cube'
 for item in tl.GetItemListInTrack.return_value:
  item.GetNodeGraph.return_value.GetNumNodes.return_value=1;item.GetNodeGraph.return_value.SetLUT.return_value=False
 with pytest.raises(bridge.ResolveUnavailable,match='apply LUT'):w.conform(s,r)

def test_allowlists():
 with pytest.raises(ValueError):w.lut_path('../../secret')
 with pytest.raises(ValueError):w.manifest('../../secret')

def test_preflight_revision_trims_and_effects(monkeypatch,tmp_path):
 source=tmp_path/'clip.mov';source.write_bytes(b'fixture')
 doc={'revision':2,'clips':[{'asset_id':1,'start':1,'end':2,'transition':'cut'}],'export':{'fps':30,'ratio':'16:9'}}
 monkeypatch.setattr(w.documents,'snapshot',lambda *a,**k:(doc,{1:{'path':str(source),'kind':'video'}}));monkeypatch.setattr(w,'probe',lambda p:{'fps':60,'duration':3})
 with pytest.raises(ValueError,match='revision'):w.prepare('x',1)
 s=w.prepare('x',2);assert s['clips'][0]['start']==60 and s['clips'][0]['end']==119 and s['clips'][0]['timeline_frames']==30 and not s['transcoded']
 doc['clips'][0]['speed']=2
 with pytest.raises(ValueError,match='speed'):w.prepare('x',2)

def test_hdr_rejected(monkeypatch):
 import json
 result=MagicMock(stdout=json.dumps({'streams':[{'codec_type':'video','color_transfer':'arib-std-b67','avg_frame_rate':'30/1','r_frame_rate':'30/1','duration':'5'}]}));monkeypatch.setattr(w.subprocess,'run',lambda *a,**k:result)
 with pytest.raises(ValueError,match='Log/HDR'):w.probe('/fixture/clip.mov')

def test_status(client,monkeypatch):
 def fail(**kwargs):raise bridge.ResolveUnavailable('test unavailable')
 monkeypatch.setattr(bridge,'_connect',fail)
 assert client.get('/api/resolve/status').json()['connected'] is False
 assert client.post('/api/resolve/preflight',json={'plan_id':'x','revision':1,'source_color':'log2'}).status_code==422
