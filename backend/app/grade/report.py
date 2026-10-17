"""job report.md (timings per step, assembly table, LUTs, Resolve click-steps)."""
from __future__ import annotations
from pathlib import Path


def _mb(n):
    return f"{n / 1048576:.1f} MB"


def write_report(jd: Path, state: dict, plan: dict) -> Path:
    L = []
    a = L.append
    name = state["job"]
    a(f"# Grade job `{name}`\n")
    a(f"- status: **{state.get('status')}**  ·  started {state.get('started')}  ·  total {state.get('total_seconds', '…')} s")
    a(f"- folder: `{state['dir']}`")
    a(f"- inputs: clips `{state['inputs']['clips']}` · script `{state['inputs']['script']}` · refs `{state['inputs']['refs']}`")
    o = state["options"]
    a(f"- look method `{o['look_method']}` · strength {o['strength']} · skin protect {o['skin_protect']} · Resolve media `DNxHR {o['resolve_quality']}`\n")

    a("## Timings per step\n")
    a("| step | seconds |\n|---|---:|")
    for s in state.get("steps", []):
        a(f"| {s['step']} | {s['seconds']}{'  ERROR ' + s['error'] if s.get('error') else ''} |")
    a("")

    a(f"## 1. Script assembly ({plan['mode']})\n")
    sm = plan["summary"]
    a(f"{sm['clips']} clips · mean confidence {sm['mean_confidence']} · unassigned {sm['unassigned']}\n")
    if plan["beats"]:
        a("Beats:\n")
        for b in plan["beats"]:
            n = sm["per_beat"].get(str(b["index"]), {}).get("clips", 0)
            a(f"{b['index']}. **{b['title']}** — {n} clip(s)")
        a("")
    a("| # | label | clip | range | conf | matched | caption |\n|---:|---|---|---|---:|---|---|")
    for it in plan["items"]:
        flag = " ⚠" if it["confidence"] < 0.5 else ""
        a(f"| {it['order']} | {it['label']} | {it['name']} | {it['start']:.2f}–{it['end']:.2f} | {it['confidence']:.2f}{flag} | "
          f"{', '.join(it['matched_terms'])} | {it['caption'][:70].replace('|', '/')} |")
    a("\n⚠ = low confidence: check the marker in Resolve.\n")

    a("## 2. Colour-correction pass\n")
    a("Per source file: white balance (weighted gray-world + white-patch, damped), exposure toward a scene target, gentle black/white-point and S-curve. "
      "Clips in one beat are pulled toward the beat's median exposure and WB so the scene matches. One `.cube` per clip in `luts/corr/`.\n")
    a("| clip | WB gains (R,G,B) | stops | cast before→after | median luma before→after |\n|---|---|---:|---|---|")
    for p, c in state.get("corrections", {}).items():
        b, af = c["stats"]["before"], c["stats"]["after"]
        a(f"| {Path(p).name} | {', '.join(f'{g:.2f}' for g in c['gains'])} | {c['stops']:+.2f} | {b['cast']:.3f}→{af['cast']:.3f} | {b['median']:.2f}→{af['median']:.2f} |")
    a("")

    a("## 3. Reference-image looks\n")
    if state.get("looks"):
        a("| look / LUT | reference image | method | strength | skin protect |\n|---|---|---|---:|---:|")
        for n, v in state["looks"].items():
            a(f"| `{n}.cube` | `{Path(v['reference']).name}` | {v['info']['method']} | {v['info']['strength']} | {v['info']['skin_protect']} |")
        a("\nLUTs live in `luts/looks/`; they sit ON TOP of each clip's correction (baked together per clip when applied).\n")
    else:
        a("No reference images supplied; only the Balanced correction exists.\n")

    a("## 4. Comparison grid\n")
    g = state.get("grid")
    if g:
        a(f"- `grid_4k.mp4` — canvas {g['canvas'][0]}×{g['canvas'][1]}, each quadrant {g['quad'][0]}×{g['quad'][1]} (native display size, capped to 1080-class), "
          f"{g['seconds']} s montage from {g['segments']} segments, {_mb(g['bytes'])}")
        a(f"- `grid_tg.mp4` — {g['telegram']['size'][0]}×{g['telegram']['size'][1]}, {_mb(g['telegram']['bytes'])} (Telegram-sized)")
        a(f"- quadrants: {' · '.join(g['labels'])}\n")
    else:
        a("(no grid rendered)\n")

    r = state.get("resolve")
    a("## 5. Resolve Free hand-off\n")
    if r:
        sp = r["spec"]
        a(f"Queued project **{sp['project_name']}** / timeline **{sp['timeline_name']}**: {len(sp['items'])} clips, {sp['width']}×{sp['height']} @ {sp['fps']} fps. "
          f"Media conditioned for Resolve free into `conditioned/` (only the used ranges, +0.5 s handles): {r['conditioning']}.\n")
        q = r.get("queue")
        if q:
            a(f"Queue id `{q['queue_id']}` — file `{q['pending_file']}` (copy: `resolve_assembly.json`)\n")
        a("**Click-steps (do this in Resolve):**\n")
        a("1. Open DaVinci Resolve (Free). Any project is fine; it will not be touched except being saved.")
        a("2. Menu **Workspace → Scripts → Utility → MediaForge Apply Edit**. (If the entry is missing, restart Resolve once; or install it from this checkout with `python scripts/install_resolve_utility.py`.)")
        a(f"3. Wait for the console line `MediaForge: timeline_ready {sp['project_name']}`. Open the project **{sp['project_name']}** (Project Manager) if it is not already open; the timeline is **{sp['timeline_name']}**.")
        a("4. Each clip is named `Beat NN · take K · file`, coloured per beat, with a marker carrying the match confidence.")
        a(f"5. Watch `grid_4k.mp4`, pick a look, then tell MediaForge: `scripts/assemble_and_grade.py --out {name} --apply-look <look|Balanced>` "
          "(queues it), and run **MediaForge Apply Edit** again: it duplicates the timeline as `<job> · <look>` and sets the baked LUT on every clip.\n")
    for lk, ap in state.get("applied", {}).items():
        a(f"### Applied: {lk}\n")
        a(f"- {ap['clips_graded']} per-clip LUTs in `{ap['luts_dir']}`; spec `{ap['spec_file']}`")
        if ap.get("queue"):
            a(f"- queued `{ap['queue']['queue_id']}`")
        if ap.get("preview"):
            a(f"- preview `{Path(ap['preview']['path']).name}` {ap['preview']['size'][0]}×{ap['preview']['size'][1]}, {_mb(ap['preview']['bytes'])}")
        a("")
    p = Path(jd) / "report.md"
    p.write_text("\n".join(L) + "\n")
    return p
