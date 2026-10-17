"""In-Resolve assembly / apply_look against a mocked Resolve object + the Utility script."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.edits import resolve_assembly as ra

ROOT = Path(__file__).resolve().parents[2]


def load_script(name):
    s = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


def spec(n=3):
    return {"job": "demo", "project_name": "demo Assembly", "timeline_name": "demo Assembly", "fps": 30, "width": 1080, "height": 1920,
            "items": [{"order": i + 1, "label": f"Beat {1 + i // 2:02d} · take {1 + i % 2}", "beat": 1 + i // 2, "take": 1 + i % 2,
                       "path": f"/jobs/demo/conditioned/clip{i}.mov", "start_frame": 15, "end_frame": 44 + i, "timeline_frames": 30 + i,
                       "confidence": 0.8, "note": f"clip{i}.mov"} for i in range(n)]}


def fake_resolve(n=3, create_side_effect=None):
    r = MagicMock()
    pm = r.GetProjectManager.return_value
    pm.GetCurrentProject.return_value = None
    p = MagicMock()
    pm.CreateProject.side_effect = create_side_effect or [p]
    p.SetSetting.return_value = True
    mp = p.GetMediaPool.return_value
    mp.ImportMedia.side_effect = lambda paths: [MagicMock()]
    tl = MagicMock()
    mp.CreateEmptyTimeline.return_value = tl
    tl.GetStartFrame.return_value = 86400
    total = sum(30 + i for i in range(n))
    tl.GetEndFrame.return_value = 86400 + total
    items = [MagicMock() for _ in range(n)]
    for it in items:
        it.SetName.return_value = True
        it.SetClipColor.return_value = True
    tl.GetItemListInTrack.return_value = items
    tl.AddMarker.return_value = True
    p.SetCurrentTimeline.return_value = True
    r.GetVersionString.return_value = "21.1"
    return r, pm, p, mp, tl, items


def test_build_project_order_markers_labels():
    r, pm, p, mp, tl, items = fake_resolve()
    out = ra.build_project(spec(), r)
    assert out["ok"] and out["project"] == "demo Assembly" and out["clip_count"] == 3 and out["label_failures"] == {"names": 0, "colors": 0, "markers": 0}
    calls = mp.AppendToTimeline.call_args_list
    assert [c.args[0][0]["recordFrame"] for c in calls] == [86400, 86430, 86461]          # cursor advances by timeline_frames
    assert [c.args[0][0]["endFrame"] for c in calls] == [44, 45, 46]
    marks = [c.args for c in tl.AddMarker.call_args_list]
    assert [m[0] for m in marks] == [0, 30, 61] and marks[1][2] == "Beat 01 · take 2"
    assert items[0].SetName.call_args.args[0] == "Beat 01 · take 1 · clip0"
    assert items[2].SetClipColor.call_args.args[0] == ra.BEAT_CLIP_COLORS[2 % len(ra.BEAT_CLIP_COLORS)]
    p.SetSetting.assert_any_call("timelineResolutionHeight", "1920")
    pm.SaveProject.assert_called()


def test_project_name_collision_gets_suffix():
    r2, pm2, p3, mp3, tl3, items3 = fake_resolve()
    pm2.CreateProject.side_effect = [None, p3]
    out = ra.build_project(spec(), r2)
    assert out["project"] == "demo Assembly 2"
    assert [c.args[0] for c in pm2.CreateProject.call_args_list] == ["demo Assembly", "demo Assembly 2"]


def test_build_project_fails_loudly():
    r, pm, p, mp, tl, items = fake_resolve()
    mp.ImportMedia.side_effect = lambda paths: []
    with pytest.raises(ra.AssemblyError, match="import"):
        ra.build_project(spec(), r)
    pm.SaveProject.assert_called()                          # partial project preserved
    r, pm, p, mp, tl, items = fake_resolve()
    tl.GetItemListInTrack.return_value = items[:2]
    with pytest.raises(ra.AssemblyError, match="count"):
        ra.build_project(spec(), r)
    r, pm, p, mp, tl, items = fake_resolve()
    busy = MagicMock()
    busy.IsRenderingInProgress.return_value = True
    pm.GetCurrentProject.return_value = busy
    with pytest.raises(ValueError, match="rendering"):
        ra.build_project(spec(), r)
    r, pm, p, mp, tl, items = fake_resolve()
    mp.AppendToTimeline.return_value = []
    with pytest.raises(ra.AssemblyError, match="append"):
        ra.build_project(spec(), r)


def look_spec(tmp_path, n=3):
    items = []
    for i in range(n):
        lut = tmp_path / "applied" / f"clip{i}__warm.cube"
        lut.parent.mkdir(exist_ok=True)
        lut.write_text("LUT_3D_SIZE 2\n" + "0 0 0\n" * 8)
        items.append({"order": i + 1, "path": f"/jobs/demo/conditioned/clip{i}.mov", "lut": str(lut)})
    return {"job": "demo", "look": "warm", "project_name": "demo Assembly", "source_timeline": "demo Assembly",
            "new_timeline": "demo · Warm", "lut_dir": str(tmp_path / "resolve_lut" / "demo"), "items": items}


def fake_look_resolve(spec_, set_lut=True):
    r = MagicMock()
    r.GetVersionString.return_value = "21.1"
    pm = r.GetProjectManager.return_value
    proj = MagicMock()
    proj.GetName.return_value = "demo Assembly"
    pm.GetCurrentProject.return_value = proj
    proj.IsRenderingInProgress.return_value = False
    src = MagicMock()
    src.GetName.return_value = "demo Assembly"
    proj.GetTimelineCount.return_value = 1
    proj.GetTimelineByIndex.return_value = src
    dup = MagicMock()
    src.DuplicateTimeline.return_value = dup
    items = []
    for it in spec_["items"]:
        tli = MagicMock()
        tli.GetMediaPoolItem.return_value.GetName.return_value = Path(it["path"]).name
        g = tli.GetNodeGraph.return_value
        g.GetNumNodes.return_value = 1
        g.SetLUT.return_value = set_lut
        g.GetLUT.side_effect = lambda n, it=it: "demo/" + Path(it["lut"]).name
        items.append(tli)
    dup.GetItemListInTrack.return_value = items
    return r, pm, proj, src, dup, items


def test_apply_look_sets_lut_on_every_clip(tmp_path):
    s = look_spec(tmp_path)
    r, pm, proj, src, dup, items = fake_look_resolve(s)
    out = ra.apply_look(s, r)
    assert out["ok"] and out["graded"] == 3 and out["timeline"] == "demo · Warm"
    src.DuplicateTimeline.assert_called_once_with("demo · Warm")                       # source timeline stays ungraded
    for it, tli in zip(s["items"], items):
        installed = tmp_path / "resolve_lut" / "demo" / Path(it["lut"]).name
        assert installed.is_file()                                                      # LUT copied into the Resolve LUT folder
        tli.GetNodeGraph.return_value.SetLUT.assert_called_once_with(1, str(installed))
    proj.RefreshLUTList.assert_called_once()


def test_apply_look_failure_modes(tmp_path):
    s = look_spec(tmp_path)
    r, pm, proj, src, dup, items = fake_look_resolve(s, set_lut=False)
    with pytest.raises(ra.AssemblyError, match="0/3"):
        ra.apply_look(s, r)
    r, pm, proj, src, dup, items = fake_look_resolve(s)
    items[1].GetMediaPoolItem.return_value.GetName.return_value = "other.mov"
    with pytest.raises(ra.AssemblyError, match="mismatch"):
        ra.apply_look(s, r)
    r, pm, proj, src, dup, items = fake_look_resolve(s)
    dup.GetItemListInTrack.return_value = items[:2]
    with pytest.raises(ra.AssemblyError, match="count"):
        ra.apply_look(s, r)
    r, pm, proj, src, dup, items = fake_look_resolve(s)
    proj.GetTimelineCount.return_value = 0
    with pytest.raises(ra.AssemblyError, match="find timeline"):
        ra.apply_look(s, r)


def test_apply_look_loads_other_project(tmp_path):
    s = look_spec(tmp_path)
    r, pm, proj, src, dup, items = fake_look_resolve(s)
    other = MagicMock()
    other.GetName.return_value = "something else"
    other.IsRenderingInProgress.return_value = False
    pm.GetCurrentProject.return_value = other
    pm.LoadProject.return_value = proj
    ra.apply_look(s, r)
    pm.LoadProject.assert_called_once_with("demo Assembly")


def run_utility(tmp_path, kind, payload, resolve_obj):
    inst = load_script("install_resolve_utility")
    queue = tmp_path / "queue"
    queue.mkdir(exist_ok=True)
    src = inst.generate(ROOT, queue)                         # the generated Utility script text
    qid = "a" * 32
    (queue / f"{qid}.pending.json").write_text(json.dumps({"queue_id": qid, "kind": kind, "spec": payload, "render_preset": None}))
    ns = {"__name__": "__main__", "resolve": resolve_obj}
    exec(compile(src, "MediaForge Apply Edit.py", "exec"), ns)
    return json.loads((queue / f"{qid}.result.json").read_text())


def test_generated_utility_script_runs_assembly_and_look(tmp_path):
    r, *_ = fake_resolve()
    res = run_utility(tmp_path, "assembly", spec(), r)
    assert res["status"] == "timeline_ready" and res["project"] == "demo Assembly" and res["clip_count"] == 3
    s = look_spec(tmp_path)
    r2, *_ = fake_look_resolve(s)
    res2 = run_utility(tmp_path, "apply_look", s, r2)
    assert res2["status"] == "look_applied" and res2["graded"] == 3
    inst = load_script("install_resolve_utility")
    text = inst.generate(Path("/x/y"), Path("/q"))
    assert "ROOT=Path('/x/y')" in text and "QUEUE=Path('/q')" in text


def test_utility_script_old_queue_files_still_conform(tmp_path):
    """No 'kind' => the original conform path (back-compat)."""
    inst = load_script("install_resolve_utility")
    queue = tmp_path / "q"
    queue.mkdir()
    src = inst.generate(ROOT, queue)
    r = MagicMock()
    pm = r.GetProjectManager.return_value
    pm.GetCurrentProject.return_value = None
    pm.CreateProject.return_value = None                     # conform fails loudly -> result.error
    (queue / ("b" * 32 + ".pending.json")).write_text(json.dumps({"queue_id": "b" * 32, "spec": {"fps": 30, "width": 1920, "height": 1080, "clips": [], "lut": None}}))
    with pytest.raises(Exception):
        exec(compile(src, "x", "exec"), {"__name__": "__main__", "resolve": r})
    assert json.loads((queue / ("b" * 32 + ".result.json")).read_text())["status"] == "error"


def test_enqueue_job(tmp_path):
    from app import resolve_queue
    q = resolve_queue.enqueue_job("assembly", spec(), job="demo")
    f = Path(q["pending_file"])
    d = json.loads(f.read_text())
    assert d["kind"] == "assembly" and d["spec"]["job"] == "demo" and d["status"] == "awaiting_resolve"
    assert resolve_queue.status(q["queue_id"])["status"] == "awaiting_resolve"
    with pytest.raises(ValueError):
        resolve_queue.enqueue_job("rm -rf", {})
