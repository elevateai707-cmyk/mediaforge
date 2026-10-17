"""HTTP surface for script-assembly + grade jobs (mounted under /api/resolve/grade)."""
import json
import threading
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .grade import job as gjob

router = APIRouter(prefix="/grade")
_running: dict[str, dict] = {}
_lock = threading.Lock()
HOME = Path.home().resolve()


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JobReq(Strict):
    out: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    clips: str = Field(min_length=1, max_length=1000)
    script: Optional[str] = Field(default=None, max_length=1000, description="path to a script/shot-list text file")
    script_text: Optional[str] = Field(default=None, max_length=20000)
    refs: Optional[str] = Field(default=None, max_length=1000)
    transcribe: bool = False
    look_method: str = Field(default="reinhard", pattern="^(reinhard|sot)$")
    strength: float = Field(default=0.8, ge=0, le=1)
    skin_protect: float = Field(default=0.7, ge=0, le=1)
    resolve_quality: str = Field(default="proxy", pattern="^(proxy|edit|finish)$")
    queue: bool = True


class LookReq(Strict):
    look: str = Field(min_length=1, max_length=80)
    queue: bool = True
    preview: bool = True


def _safe(p: Optional[str], what: str) -> Optional[str]:
    """Inputs must live under the owner's home; never arbitrary system paths."""
    if p is None:
        return None
    r = Path(p).expanduser().resolve()
    if HOME != r and HOME not in r.parents:
        raise HTTPException(422, f"{what} must be inside {HOME}")
    if not r.exists():
        raise HTTPException(422, f"{what} not found: {p}")
    return str(r)


@router.post("/jobs", status_code=202)
def start(req: JobReq):
    clips, refs, script = _safe(req.clips, "clips"), _safe(req.refs, "refs"), _safe(req.script, "script")
    if req.script and req.script_text:
        raise HTTPException(422, "send script OR script_text")
    with _lock:
        if _running.get(req.out, {}).get("state") == "running":
            raise HTTPException(409, "job already running")
        _running[req.out] = {"state": "running", "step": "starting", "error": None}
    if req.script_text:
        d = gjob.job_dir(req.out)
        d.mkdir(parents=True, exist_ok=True)
        (d / "script_input.txt").write_text(req.script_text)
        script = str(d / "script_input.txt")

    def progress(step, msg):
        _running[req.out]["step"] = f"{step}: {msg}"

    def work():
        try:
            gjob.run_job(clips, req.out, script=script, refs=refs, transcribe=req.transcribe, look_method=req.look_method,
                         strength=req.strength, skin_protect=req.skin_protect, resolve_quality=req.resolve_quality,
                         queue=req.queue, progress=progress)
            _running[req.out]["state"] = "finished"
        except Exception as e:                       # surfaced via GET; job.json carries the step that failed
            _running[req.out].update(state="error", error=str(e)[:500])

    threading.Thread(target=work, name=f"grade-{req.out}", daemon=True).start()
    return {"job": req.out, "status": "running", "poll": f"/api/resolve/grade/jobs/{req.out}"}


@router.get("/jobs")
def list_jobs():
    out = []
    for p in sorted(gjob.JOBS_ROOT.glob("*/job.json")):
        try:
            d = json.loads(p.read_text())
            out.append({"job": d["job"], "status": d.get("status"), "looks": list(d.get("looks", {}))})
        except Exception:
            continue
    return {"jobs": out}


@router.get("/jobs/{name}")
def get_job(name: str):
    try:
        state = gjob.load_job(name)
    except ValueError as e:
        if name in _running:
            return {"job": name, **_running[name]}
        raise HTTPException(404, str(e))
    live = _running.get(name, {})
    return {"job": name, "status": state.get("status"), "live": live, "steps": state.get("steps", []),
            "looks": list(state.get("looks", {})), "grid": state.get("grid"), "assembly": state.get("assembly"),
            "resolve": {k: v for k, v in state.get("resolve", {}).items() if k != "spec"},
            "applied": state.get("applied", {}), "report": str(Path(state["dir"]) / "report.md")}


@router.post("/jobs/{name}/apply_look")
def apply_look(name: str, req: LookReq):
    try:
        gjob.load_job(name)
        return gjob.apply_look(name, req.look, queue=req.queue, preview=req.preview)
    except ValueError as e:
        raise HTTPException(409, str(e))
