"""One hands-off job: assemble -> correct -> looks -> grid -> Resolve queue -> report.

``run_job`` runs steps 1-4 (+ the Resolve hand-off) unattended and then waits for
the user's look choice; ``apply_look`` does step 5 (queue the in-Resolve grade
and/or render an ffmpeg preview).  All state lives in <job>/job.json.
"""
from __future__ import annotations
import json
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .. import config
from . import assemble as asm
from . import correct, cube, grid, looks
from .sampling import load_image, pixels, probe, sample_frames, sdr_filter
from .colorspace import luma

JOBS_ROOT = config.MF_ROOT / "jobs"
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
MASTER_LUT_DIR = "/opt/resolve/LUT"
JOB_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
Progress = Callable[[str, str], None]


def job_dir(out) -> Path:
    s = str(out)
    if "/" in s:
        return Path(s).expanduser().resolve()
    if not JOB_RE.match(s):
        raise ValueError("job name: letters, digits, . _ - only (max 64)")
    return JOBS_ROOT / s


def load_job(out) -> dict:
    p = job_dir(out) / "job.json"
    if not p.is_file():
        raise ValueError(f"no job at {p.parent}")
    return json.loads(p.read_text())


def _save(jd: Path, state: dict) -> None:
    tmp = jd / "job.json.part"
    tmp.write_text(json.dumps(state, indent=2, default=_json_default))
    tmp.replace(jd / "job.json")


def _json_default(o):
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, Path):
        return str(o)
    raise TypeError(type(o))


class Steps:
    def __init__(self, jd: Path, state: dict, progress: Optional[Progress]):
        self.jd, self.state, self.progress = jd, state, progress
        state.setdefault("steps", [])

    def __call__(self, name: str, fn, *a, **kw):
        if self.progress:
            self.progress(name, "start")
        t0 = time.perf_counter()
        self.state["current_step"] = name
        _save(self.jd, self.state)
        try:
            out = fn(*a, **kw)
        except Exception as e:
            self.state["steps"].append({"step": name, "seconds": round(time.perf_counter() - t0, 2), "error": str(e)[:400]})
            self.state["status"] = "error"
            self.state["error"] = f"{name}: {e}"[:600]
            _save(self.jd, self.state)
            raise
        dt = round(time.perf_counter() - t0, 2)
        self.state["steps"].append({"step": name, "seconds": dt})
        _save(self.jd, self.state)
        if self.progress:
            self.progress(name, f"done in {dt}s")
        return out


# ---- step implementations ----------------------------------------------------
def _ingest(refs, use_library, transcribe):
    uniq = sorted({r.path for r in refs})
    with ThreadPoolExecutor(max_workers=6) as ex:
        probes = dict(zip(uniq, ex.map(probe, uniq)))
    clips = [asm.describe(r, probes[r.path], use_library=use_library, transcribe=transcribe) for r in refs]
    return clips, probes


def _sample(clips, probes, frames_per_seg, per_frame_px, seed=11):
    """Pixel pool per source file + per-clip median luma (night/day cue)."""
    rng = np.random.default_rng(seed)
    by_file: dict[str, list] = {}
    for c in clips:
        by_file.setdefault(c.path, []).append(c)
    pools: dict[str, np.ndarray] = {}
    for path, cs in by_file.items():
        px = []
        for c in cs:
            fr = sample_frames(probes[path], c.start, c.end, n=frames_per_seg)
            if not fr:
                continue
            p = pixels(fr, per_frame_px, rng)
            c.luma = round(float(np.median(luma(p))), 4)
            px.append(p)
        if px:
            pools[path] = np.concatenate(px)
    return pools


def _corrections(plan, pools, luts_dir):
    scene_of, first_beat = {}, {}
    for it in plan["items"]:
        first_beat.setdefault(it["path"], it["beat"])
    for path in pools:
        scene_of[path] = first_beat.get(path, 0)
    # beat 0 (unassigned / singles) must not be "matched" together
    scene_keys = {p: (f"s{b}" if b else f"u:{p}") for p, b in scene_of.items()}
    corr = correct.estimate_scene(pools, scene_keys)
    used, files = set(), {}
    for path, c in corr.items():
        base = grid.slug(Path(path).stem)
        name = base if base not in used else f"{base}_{len(used)}"
        used.add(name)
        files[path] = str(cube.write_cube(luts_dir / f"{name}.cube", correct.correction_lut(c), f"MF balance {Path(path).name}"))
    return corr, files


def _looks(pools, corr, ref_paths, luts_dir, method, strength, skin_protect, rng_seed=5):
    rng = np.random.default_rng(rng_seed)
    pool = []
    for path, px in pools.items():
        sub = px[rng.choice(len(px), size=min(len(px), 4000), replace=False)]
        pool.append(correct.apply_correction(corr[path], sub.astype(np.float64)))
    footage = np.concatenate(pool)
    out, used = {}, set()
    for rp in ref_paths:
        base = grid.slug(Path(rp).stem)
        name = base if base not in used else f"{base}_{len(used)}"
        used.add(name)
        ref_px = load_image(rp).reshape(-1, 3).astype(np.float64)
        if len(ref_px) > 60000:
            ref_px = ref_px[rng.choice(len(ref_px), size=60000, replace=False)]
        lut, info = looks.build_look(footage, ref_px, method=method, strength=strength, skin_protect=skin_protect)
        f = cube.write_cube(luts_dir / f"{name}.cube", lut, f"MF look {name} (ref {Path(rp).name})")
        out[name] = {"lut": str(f), "reference": str(rp), "info": info,
                     "ref_lab": {k: np.round(v, 2).tolist() for k, v in looks.lab_stats(ref_px).items()}}
    return out


STD_FPS = [23.976, 24.0, 25.0, 29.97, 30.0, 47.952, 48.0, 50.0, 59.94, 60.0, 119.88, 120.0]


def snap_fps(fps: float) -> float:
    """iPhone clips report avg rates like 23.93; snap to the nominal rate when within 0.5%."""
    best = min(STD_FPS, key=lambda f: abs(f - fps))
    return best if abs(best - fps) / best < 0.005 else fps


def _resolve_prepare(jd, name, plan, probes, quality, fps_hint=None):
    """Condition only the used ranges into the job dir and build the assembly spec."""
    import importlib.util
    spec_ = importlib.util.spec_from_file_location("resolve_condition", config.MF_ROOT / "scripts" / "resolve_condition.py")
    rc = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(rc)
    outdir = jd / "conditioned"
    handle = 0.5
    fps_votes = Counter()
    items, actions = [], Counter()

    def one(it):
        info = probes[it["path"]]
        hdr = info["hdr"]
        rot = info["rotation"] % 180 != 0
        start, end = it["start"], it["end"]
        sub0 = max(0.0, start - handle)
        dur = (end - sub0) + handle
        stem = grid.slug(Path(it["path"]).stem)
        res = rc.condition(Path(it["path"]), outdir, quality=quality, start=sub0, duration=dur, vf=(sdr_filter(info).rstrip(",") or None),
                           name=f"{stem}_{int(round(start * 1000)):06d}_{int(round((end - start) * 1000)):05d}", force_video=rot)
        return it, res, sub0

    with ThreadPoolExecutor(max_workers=3) as ex:
        results = list(ex.map(one, plan["items"]))
    for it, res, sub0 in results:
        if res["action"] == "failed":
            raise RuntimeError("conditioning failed: " + res.get("error", "")[:300])
        actions[res["action"]] += 1
        info = probes[it["path"]]
        fps = snap_fps(info["fps"])
        fps_votes[round(fps)] += 1
        trimmed = res["dst"] != it["path"]
        base = 0.0 if not trimmed else sub0
        sf = int(round((it["start"] - base) * fps))
        ef = sf + max(1, int(round((it["end"] - it["start"]) * fps))) - 1
        it["resolve_path"] = res["dst"]
        items.append({"order": it["order"], "label": it["label"], "beat": it["beat"], "take": it["take"], "path": res["dst"],
                      "start_frame": sf, "end_frame": ef, "src_fps": fps, "confidence": it["confidence"], "note": it["name"]})
    tl_fps = fps_hint or (fps_votes.most_common(1)[0][0] if fps_votes else 30)
    for it in items:
        it["timeline_frames"] = max(1, round((it["end_frame"] - it["start_frame"] + 1) / it["src_fps"] * tl_fps))
    first = probes[plan["items"][0]["path"]]
    w, h = grid.quad_size(first["width"], first["height"], (1920, 1080))
    spec = {"job": name, "project_name": f"{name} Assembly", "timeline_name": f"{name} Assembly",
            "fps": tl_fps, "width": w, "height": h, "items": items}
    return spec, dict(actions)


# ---- public: run_job -----------------------------------------------------------
def run_job(clips, out, script=None, refs=None, *, transcribe=False, use_library=True, look_method="reinhard",
            strength=0.8, skin_protect=0.7, per_beat=2, seg_len=3.5, max_montage=36.0, fps=30,
            resolve_quality="proxy", queue=True, grid_cap=(1920, 1080), progress: Optional[Progress] = None,
            min_confidence=0.25, grid_looks: Optional[list[str]] = None) -> dict:
    jd = job_dir(out)
    name = jd.name
    jd.mkdir(parents=True, exist_ok=True)
    (jd / "luts" / "corr").mkdir(parents=True, exist_ok=True)
    (jd / "luts" / "looks").mkdir(parents=True, exist_ok=True)
    state: dict = {"job": name, "dir": str(jd), "status": "running", "started": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "options": {"look_method": look_method, "strength": strength, "skin_protect": skin_protect,
                               "resolve_quality": resolve_quality, "transcribe": transcribe},
                   "inputs": {"clips": str(clips) if not isinstance(clips, list) else "<list>", "script": str(script) if script else None, "refs": str(refs) if refs else None}}
    step = Steps(jd, state, progress)
    t_all = time.perf_counter()

    refs_in = asm.collect_clips(clips)
    if not refs_in:
        raise ValueError("no video clips found")
    beats = []
    if script:
        sp = Path(script)
        text = sp.read_text() if (len(str(script)) < 400 and sp.is_file()) else str(script)
        beats = asm.parse_script(text)
        (jd / "script.txt").write_text(text)
    ref_imgs = []
    if refs:
        rp = Path(refs)
        ref_imgs = sorted(p for p in rp.iterdir() if p.suffix.lower() in IMAGE_EXT) if rp.is_dir() else [rp]

    clip_infos, probes = step("1 ingest+probe", _ingest, refs_in, use_library, transcribe)
    pools = step("2 sample frames", _sample, clip_infos, probes, 3, 2500)
    plan = step("3 script assembly", asm.assemble, clip_infos, beats or None, min_confidence)
    (jd / "assembly.json").write_text(json.dumps(plan, indent=2, default=_json_default))
    state["assembly"] = plan["summary"]
    state["assembly_mode"] = plan["mode"]

    corr, corr_files = step("4 colour correction", _corrections, plan, pools, jd / "luts" / "corr")
    state["corrections"] = {p: dict(c.to_dict(), lut=corr_files[p]) for p, c in corr.items()}
    (jd / "grade.json").write_text(json.dumps(state["corrections"], indent=2, default=_json_default))

    look_info = {}
    if ref_imgs:
        look_info = step("5 reference looks", _looks, pools, corr, ref_imgs, jd / "luts" / "looks", look_method, strength, skin_protect)
    state["looks"] = look_info

    durations = {p: probes[p]["duration"] for p in probes}
    segs = grid.select_montage(plan["items"], per_beat=per_beat, seg_len=seg_len, max_total=max_montage, durations=durations)
    state["montage"] = segs
    chosen = [(n, Path(v["lut"])) for n, v in look_info.items() if not grid_looks or n in grid_looks][:3]
    if segs:
        gres = step("6 comparison grid", grid.render_grid, jd, segs, {p: Path(f) for p, f in corr_files.items()}, chosen,
                    jd / "grid_4k.mp4", None, fps, 2, grid_cap)
        tg = step("6b telegram copy", grid.telegram_copy, jd / "grid_4k.mp4", jd / "grid_tg.mp4")
        state["grid"] = dict(gres, telegram=tg)

    rspec, actions = step("7 resolve prepare+queue", _resolve_prepare, jd, name, plan, probes, resolve_quality)
    (jd / "assembly.json").write_text(json.dumps(plan, indent=2, default=_json_default))   # now with resolve_path
    state["resolve"] = {"spec": rspec, "conditioning": actions, "quality": resolve_quality}
    (jd / "resolve_assembly.json").write_text(json.dumps(rspec, indent=2))
    if queue:
        from .. import resolve_queue
        q = resolve_queue.enqueue_job("assembly", rspec, "In Resolve: Workspace > Scripts > Utility > MediaForge Apply Edit.", job=name)
        state["resolve"]["queue"] = q
    state["total_seconds"] = round(time.perf_counter() - t_all, 2)
    state["status"] = "awaiting_look_choice" if look_info else "done"
    state.pop("current_step", None)
    from .report import write_report
    write_report(jd, state, plan)
    _save(jd, state)
    return state


# ---- public: apply_look ---------------------------------------------------------
def _combined_luts(state, look: str, jd: Path) -> dict:
    """{source path: baked correction+look .cube}. 'Balanced' = correction only."""
    luts = {}
    look_lut = None
    if look != "Balanced":
        if look not in state["looks"]:
            raise ValueError(f"unknown look '{look}'. Choose Balanced or one of: {', '.join(state['looks'])}")
        look_lut, _ = cube.read_cube(state["looks"][look]["lut"])
    outdir = jd / "luts" / "applied" / grid.slug(look)
    for path, c in state["corrections"].items():
        corr_lut, _ = cube.read_cube(c["lut"])
        final = corr_lut if look_lut is None else cube.compose(corr_lut, look_lut)
        luts[path] = str(cube.write_cube(outdir / f"{Path(c['lut']).stem}__{grid.slug(look)}.cube", final, f"MF {look} {Path(path).name}"))
    return luts


def apply_look(out, look: str, *, queue=True, preview=True, preview_long_side=1280, lut_dir: Optional[str] = None,
               progress: Optional[Progress] = None) -> dict:
    jd = job_dir(out)
    state = load_job(out)
    plan = json.loads((jd / "assembly.json").read_text())
    t0 = time.perf_counter()
    luts = _combined_luts(state, look, jd)
    res = {"job": state["job"], "look": look, "luts_dir": str(jd / "luts" / "applied" / grid.slug(look)), "clips_graded": len(plan["items"])}
    spec = {"job": state["job"], "look": look, "project_name": state["resolve"]["spec"]["project_name"],
            "source_timeline": state["resolve"]["spec"]["timeline_name"],
            "new_timeline": f"{state['job']} · {look.replace('_', ' ').title() if look != 'Balanced' else 'Balanced'}",
            "lut_dir": lut_dir or f"{MASTER_LUT_DIR}/MediaForge/{grid.slug(state['job'])}",
            "items": [{"order": it["order"], "path": it.get("resolve_path", it["path"]), "lut": luts[it["path"]]} for it in plan["items"]]}
    (jd / f"resolve_apply_{grid.slug(look)}.json").write_text(json.dumps(spec, indent=2))
    res["spec_file"] = str(jd / f"resolve_apply_{grid.slug(look)}.json")
    if queue:
        from .. import resolve_queue
        res["queue"] = resolve_queue.enqueue_job("apply_look", spec, "In Resolve: Workspace > Scripts > Utility > MediaForge Apply Edit (the assembly project must already exist).", job=state["job"], look=look)
    if preview:
        chains = {}
        for path, c in state["corrections"].items():
            chains[path] = [Path(c["lut"])] + ([Path(state["looks"][look]["lut"])] if look != "Balanced" else [])
        pv = grid.render_preview(plan["items"], chains, jd / f"preview_{grid.slug(look)}.mp4", jd / f"preview_work_{grid.slug(look)}",
                                 preview_long_side, fps=30)
        res["preview"] = pv
    res["seconds"] = round(time.perf_counter() - t0, 2)
    state.setdefault("applied", {})[look] = res
    state["status"] = "look_applied" if queue else state.get("status")
    _save(jd, state)
    from .report import write_report
    write_report(jd, state, plan)
    return res
