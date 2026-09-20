"""Local stdio MCP. Calls loopback MediaForge; does not upload footage."""
from mcp.server.fastmcp import FastMCP
import httpx
mcp=FastMCP('MediaForge Resolve')
BASE='http://127.0.0.1:8420'
def request(method,path,body=None):
    with httpx.Client(base_url=BASE,timeout=240,trust_env=False) as c:
        r=c.request(method,path,json=body)
        if not r.is_success: raise ValueError(f'MediaForge {r.status_code}: {r.text[:2000]}')
        return r.json()
@mcp.tool()
def resolve_status()->dict:
    """Check actual live Resolve connection and installed render presets."""
    return request('GET','/api/resolve/status')
@mcp.tool()
def list_luts()->dict:
    """List original installed Rec709 creative LUT IDs."""
    return request('GET','/api/resolve/luts')
@mcp.tool()
def list_assets(limit:int=50)->dict:
    """List registered footage. Select relevant asset IDs; do not infer colour space."""
    return request('GET',f'/api/assets?limit={max(1,min(limit,100))}')
@mcp.tool()
def scan_media(paths:list[str])->dict:
    """Index user-selected local folders through MediaForge. Returns async job ID."""
    return request('POST','/api/scan',{'paths':paths})
@mcp.tool()
def job_status(job_id:str)->dict:
    """Check scan/analysis job completion before planning."""
    if not job_id.replace('-','').isalnum(): raise ValueError('Invalid job ID')
    return request('GET','/api/jobs/'+job_id)
@mcp.tool()
def plan_edit(asset_ids:list[int],intent:str,fps:int=30,ratio:str='16:9')->dict:
    """Use local Ollama to plan selected footage. Reports deterministic fallback if AI is unavailable. Creates editable cuts-only revision; does not export."""
    return request('POST','/api/resolve/plan',{'asset_ids':asset_ids,'intent':intent,'fps':fps,'ratio':ratio})
@mcp.tool()
def get_edit(plan_id:str)->dict:
    """Read editable project and current revision."""
    if not plan_id.replace('-','').replace('_','').isalnum(): raise ValueError('Invalid plan ID')
    return request('GET','/api/editor/'+plan_id)
@mcp.tool()
def update_edit(plan_id:str,document:dict)->dict:
    """Save revised clip selections/in/out points with matching revision. No source files are modified."""
    if not plan_id.replace('-','').replace('_','').isalnum(): raise ValueError('Invalid plan ID')
    return request('PUT','/api/editor/'+plan_id,document)
@mcp.tool()
def preflight_edit(plan_id:str,revision:int,lut_id:str|None=None)->dict:
    """Validate declared Rec709 sources, trims, unsupported effects, and LUT before touching Resolve. No conversion."""
    return request('POST','/api/resolve/preflight',{'plan_id':plan_id,'revision':revision,'lut_id':lut_id,'source_color':'rec709'})
@mcp.tool()
def export_edit(plan_id:str,revision:int,lut_id:str|None=None)->dict:
    """Build a NEW Resolve project from the exact revision, with source trims and selected LUT. Only use for user-authorized editing of confirmed Rec709 sources. Each invocation creates a separate project; do not blindly retry uncertain calls."""
    return request('POST','/api/resolve/export',{'plan_id':plan_id,'revision':revision,'lut_id':lut_id,'source_color':'rec709'})
@mcp.tool()
def render_edit(export_id:str,preset:str)->dict:
    """Start an authorized native Resolve render using an installed preset. Returns started, not completed. No external upload."""
    return request('POST','/api/resolve/render',{'export_id':export_id,'preset':preset})
@mcp.tool()
def render_status(export_id:str,job_id:str)->dict:
    """Check actual render job completion and output files."""
    if not all(s.replace('-','').isalnum() for s in [export_id,job_id]): raise ValueError('Invalid ID')
    return request('GET',f'/api/resolve/render/{export_id}/{job_id}')
@mcp.tool()
def queue_free_edit(plan_id:str,revision:int,lut_id:str|None=None,render_preset:str|None=None)->dict:
    """Prepare a durable Resolve Free handoff for declared Rec709 footage. User must launch MediaForge Apply Edit INSIDE Resolve. Optional installed render preset starts a native render. Queued is NOT edited or rendered."""
    return request('POST','/api/resolve/queue',{'plan_id':plan_id,'revision':revision,'lut_id':lut_id,'source_color':'rec709','render_preset':render_preset})
@mcp.tool()
def free_edit_status(queue_id:str)->dict:
    """Read durable in-Resolve script result. Run the script again after native rendering to refresh completion status."""
    if len(queue_id)!=32 or not queue_id.isalnum(): raise ValueError('Invalid queue ID')
    return request('GET','/api/resolve/queue/'+queue_id)
if __name__=='__main__':mcp.run(transport='stdio')
