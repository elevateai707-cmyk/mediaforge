"""`adlab`: one command per step.

    adlab init                      create data/ folders + the ChromaDB collection
    adlab sites                     every site: facts ready, brand gaps
    adlab facts ev1charger          which facts ads may use, and why the rest can't
    adlab mf status                 Media Forge: up?, GPU, engines, capability map
    adlab mf release                free Media Forge's VRAM
    adlab mf smoke                  Phase 1 smoke test (9:16 image, VRAM before/after)
    adlab usage                     token + cost ledger

    adlab ingest <url> [file] --site S  record a pasted Ad Library link (+ the file you saved)
    adlab ingest --scan | --watch       process data/incoming/<site>/ once / continuously
    adlab scout [--site S]              drops + API sources -> variants -> scores -> winners
    adlab ads [--site S]                scouted ads, best first, with the score explained
    adlab sync | schedule               Supabase mirror / weekly API refresh (APScheduler)

    adlab analyze [--site S] [--dry-run]     teardowns of winners (Claude), patterns rebuilt
    adlab hooks "query" | patterns           search teardowns / ranked patterns
    adlab brief --site S [--dry-run]         3-5 fact-locked briefs from the best pattern
    adlab check [BRIEF]                      Compliance Officer: PASS / FIX / REJECT
    adlab produce [--dry-run]                render briefs -> 9:16 / 4:5 / 1:1 ads + manifest
    adlab review [approve|edit|kill ID]      the human gate
    adlab revise [--dry-run]                 rewrite briefs from your Edit notes
    adlab export                             approved ads -> upload-ready files + sheets
    adlab report [--import CSV|--pull-meta]  your results; re-ranks patterns
    adlab digest [--send] | serve            weekly email / local API for the dashboard
    adlab demo                               the whole pipeline offline on DEMO data
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import typer

from . import usage
from .config import settings

app = typer.Typer(no_args_is_help=True, add_completion=False, help="AdLab: ad research -> teardown -> remake.")
mf_app = typer.Typer(no_args_is_help=True, help="Media Forge: status, VRAM release, smoke test.")
app.add_typer(mf_app, name="mf")


def tldr(*lines: str) -> None:
    typer.secho("TL;DR", bold=True)
    for line in lines[:3]:
        typer.echo(f"  • {line}")
    typer.echo()


@app.command()
def init() -> None:
    """Create AdLab's local folders and the teardowns vector collection."""
    from . import vectors

    s = settings()
    s.ensure_dirs()
    coll = vectors.collection(vectors.client(s.chroma_dir))
    tldr(f"data folder ready: {s.data_dir}",
         f"ChromaDB collection '{coll.name}' ready ({coll.count()} teardowns)",
         "next: fill sites/<site>/facts.yaml, then `adlab sites`")


@app.command()
def sites() -> None:
    """List sites with fact readiness and missing brand fields."""
    from .sites import list_sites, load_site

    rows = []
    for slug in list_sites():
        site = load_site(slug)
        usable = site.facts.usable_keys()
        rows.append((slug, site.info, len(usable), len(site.facts.facts), site.brand.missing()))
    ready = [r[0] for r in rows if r[2]]
    tldr(f"{len(rows)} sites configured; {len(ready)} have verified facts ({', '.join(ready) or 'none yet'})",
         "a site can't get briefs until it has at least one verified fact",
         "fill facts.yaml (statement + verified: true) and brand.yaml")
    for slug, info, n_ok, n_all, gaps in rows:
        name = info.name or "(name not set)"
        typer.echo(f"{slug:<16} {info.role:<9} facts {n_ok}/{n_all} usable   {name} · {info.domain}")
        if gaps:
            typer.echo(f"{'':<16} brand missing: {', '.join(gaps)}")


@app.command()
def facts(site: str) -> None:
    """Show which facts ads for SITE may cite, and why the others can't be used yet."""
    from .facts import load_facts

    sheet = load_facts(site)
    ok, bad = sheet.usable_keys(), sheet.unusable()
    tldr(f"{site}: {len(ok)} usable facts, {len(bad)} not usable yet",
         "ads may only make claims that cite a usable fact",
         f"edit sites/{site}/facts.yaml to add or verify facts")
    for k in ok:
        typer.secho(f"  ✓ {k}: {sheet.facts[k].statement}", fg="green")
    for k, why in bad.items():
        typer.secho(f"  ✗ {k}: {why}", fg="yellow")


@mf_app.command("status")
def mf_status() -> None:
    """Media Forge status and what it can/can't do for the Producer."""
    from .mediaforge import CAPABILITIES, MediaForge, MediaForgeError, find_studio_cli

    mf = MediaForge()
    try:
        st = mf.status()
        h = st["health"]
        up = f"up at {mf.base_url} · GPU {h.get('gpu')} {h.get('gpu_name') or ''}".strip()
        engines = f"Studio {'✓' if st['studio'].get('available') else '✗'} · PhotoCraft {'✓' if st['photocraft'].get('available') else '✗'}"
    except MediaForgeError as exc:
        up, engines = f"NOT reachable: {exc}", "unknown"
    missing = [c["need"] for c in CAPABILITIES if c["status"] == "missing"]
    tldr(f"Media Forge {up}", f"engines: {engines} · local Studio CLI: {find_studio_cli() or 'not found'}",
         f"missing for ads: {', '.join(missing)}")
    for c in CAPABILITIES:
        mark = {"available": "✓", "partial": "~", "missing": "✗"}[c["status"]]
        typer.echo(f"  {mark} {c['need']:<38} {c['how']}")


@mf_app.command("release")
def mf_release() -> None:
    """Ask Media Forge to unload its GPU models now."""
    from .mediaforge import MediaForge

    typer.echo(json.dumps(MediaForge().release_gpu(), indent=1))


@mf_app.command("smoke")
def mf_smoke(ratio: str = typer.Option("9:16", help="9:16 | 1:1 | 4:5 | 16:9")) -> None:
    """Phase 1 smoke test: one test image at RATIO with VRAM logged before/after."""
    from . import smoke

    r = smoke.run(ratio)
    vb, va = r.get("vram_before"), r.get("vram_after")
    vram = (f"VRAM free {vb['free_mb']} → {va['free_mb']} MB" if vb and va else "VRAM not measured (no NVIDIA GPU)")
    rel = r.get("release")
    mf_line = (r["mediaforge"].get("error") or
               (f"released {', '.join(rel['released']) or 'nothing'}; Ollama "
                f"{'unloaded' if rel['ollama_unloaded'] else 'not unloaded (not running?)'}" if rel
                else "reachable, but VRAM release failed (see warnings)"))
    tldr(f"made {r['image']['size'][0]}x{r['image']['size'][1]} image: {r['image']['path']}",
         vram, f"Media Forge: {mf_line}")
    for w in r["warnings"]:
        typer.secho(f"  ! {w}", fg="yellow")


@app.command("usage")
def usage_cmd(days: int = typer.Option(7, help="look back this many days")) -> None:
    """Token usage and cost per command."""
    rows = usage.ledger(datetime.now(timezone.utc) - timedelta(days=days))
    by_cmd: dict[str, list[dict]] = {}
    for row in rows:
        by_cmd.setdefault(row["command"], []).append(row)
    total = usage.summarize(rows)
    tldr(f"last {days} days: {total['calls']} LLM calls, ${total['cost_usd']:.2f}",
         f"{total['input_tokens']:,} tokens in / {total['output_tokens']:,} out",
         f"{total['unpriced_calls']} calls unpriced" if total["unpriced_calls"] else "all calls priced")
    for cmd, calls in sorted(by_cmd.items()):
        s = usage.summarize(calls)
        typer.echo(f"  {cmd:<20} {s['calls']:>4} calls  ${s['cost_usd']:.4f}")


@app.command()
def ingest(
    url: Optional[str] = typer.Argument(None, help="Ad Library / Creative Center link (never fetched), or a file"),
    file: Optional[Path] = typer.Argument(None, help="the ad file you saved (video or screenshot)"),
    site: Optional[str] = typer.Option(None, help="site slug this ad was scouted for"),
    platform: Optional[str] = typer.Option(None, help="meta | tiktok | other"),
    advertiser: Optional[str] = typer.Option(None),
    running_since: Optional[str] = typer.Option(None, help="YYYY-MM-DD it started running"),
    last_seen: Optional[str] = typer.Option(None, help="YYYY-MM-DD it was last seen running (stopped ads)"),
    still_running: Optional[bool] = typer.Option(None, "--running/--stopped"),
    variants: Optional[int] = typer.Option(None, help="how many versions you saw"),
    rank: Optional[int] = typer.Option(None, help="platform rank (e.g. Creative Center Top Ads #)"),
    text: Optional[str] = typer.Option(None, help="the ad copy, if you have it"),
    notes: Optional[str] = typer.Option(None),
    scan: bool = typer.Option(False, "--scan", help="process data/incoming/ once"),
    watch_: bool = typer.Option(False, "--watch", help="keep processing data/incoming/ (Ctrl+C stops)"),
) -> None:
    """Add ads: a pasted link (+ file), or everything dropped in data/incoming/<site>/."""
    from . import ingest as ing

    if watch_:
        settings().ensure_dirs()
        typer.echo(f"watching {settings().incoming_dir} (drop files into <site>/ folders; Ctrl+C to stop)")
        try:
            ing.watch(on_result=lambda r: typer.echo(f"  {r.status:<7} {Path(r.source).name} {r.message}"))
        except KeyboardInterrupt:
            pass
        return
    if scan:
        results = ing.process_incoming()
    else:
        if not url and not file:
            raise typer.BadParameter("give a link, a file, --scan or --watch")
        if not site:
            raise typer.BadParameter("--site is required (which of your sites is this ad's niche?)")
        try:
            sc = ing.Sidecar(platform=platform, advertiser=advertiser, running_since=running_since,
                             last_seen=last_seen,
                             still_running=still_running, variants=variants, platform_rank=rank,
                             ad_text=text, notes=notes)
        except ValueError as exc:  # pydantic ValidationError is a ValueError
            raise typer.BadParameter(str(exc).splitlines()[-1].strip())
        if url and Path(url).is_file() and file is None:
            url, file = None, Path(url)
        results = [ing.ingest_link(url, site, file, sc) if url else ing.ingest_media(file, site, sc)]
    s = ing.summarize(results)
    tldr(f"{s['new']} new ads, {s['updated']} already known (merged), {s['failed']} failed",
         "nothing was downloaded: links are stored as references only",
         "next: `adlab scout` to group variants and score")
    for r in results:
        if r.status == "failed":
            typer.secho(f"  ✗ {r.source}: {r.message}", fg="red")


@app.command()
def scout(site: Optional[str] = typer.Option(None, help="one site (default: all)"),
          api: bool = typer.Option(True, "--api/--no-api", help="run enabled official API collectors")) -> None:
    """Process drops, run API sources, group variants, score, and show new winners."""
    from . import scout as sc

    r = sc.run(site, collectors=api)
    ing = {"new": 0, "updated": 0, "failed": 0}
    for x in r.ingested:
        ing[x.status] += 1
    stopped = [f"{c.site}: {c.stopped}" for c in r.collectors if c.stopped]
    tldr(f"{len(r.new_winners)} new winners · {r.ads_scored} ads scored",
         f"drops: {ing['new']} new, {ing['updated']} merged, {ing['failed']} failed"
         + (f" · API: {sum(len(c.results) for c in r.collectors)} ads" if r.collectors else ""),
         f"best: {r.top[0].advertiser} ({r.top[0].score:g})" if r.top else "no ads yet: drop some in data/incoming/<site>/")
    for w in r.new_winners:
        typer.secho(f"  ★ NEW {w.score:>5g}  {w.site:<12} {w.advertiser}  ({w.why})", fg="green")
    for line in stopped:
        typer.secho(f"  ! {line}", fg="yellow")
    if r.sync.get("error"):
        typer.secho(f"  ! Supabase sync failed: {r.sync['error']}", fg="yellow")


@app.command()
def ads(site: Optional[str] = typer.Option(None), limit: int = typer.Option(20)) -> None:
    """Scouted ads, best first, with the score explained."""
    from . import scoring
    from .store import LocalStore

    rows = LocalStore().ads(site)
    threshold = scoring.weights().winner_threshold
    winners = [a for a in rows if a.score and a.score.total >= threshold]
    tldr(f"{len(rows)} ads{' for ' + site if site else ''}, {len(winners)} winners (score >= {threshold:g})",
         "score = longevity + still running + variants + rank (+ repeat pattern from Phase 3)",
         "`adlab scout` refreshes scores")
    for a in rows[:limit]:
        sc = scoring.explain(a.score) if a.score else "not scored yet"
        vg = f" · {a.variant_group}" if a.variant_group else ""
        typer.echo(f"  {('★' if a in winners else ' ')} {a.site:<12} {a.source.advertiser[:28]:<28} "
                   f"{a.source.platform:<6} {a.first_seen}→{a.last_seen}{vg}\n      {sc}  [{a.id[:12]}]")


@app.command("sync")
def sync_cmd() -> None:
    """Push changed ads to Supabase (when SUPABASE_URL + key are set)."""
    from .store import LocalStore, sync

    r = sync(LocalStore())
    typer.echo("Supabase not configured: set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY"
               if not r["configured"] else f"pushed {r['pushed']} ads to Supabase")


@app.command()
def schedule() -> None:
    """Run the weekly API refresh (Mondays 06:47 Alberta time) until Ctrl+C."""
    from . import scout as sc

    sched = sc.scheduler(blocking=True)
    typer.echo("AdLab scheduler running: weekly scout of API sources, Mondays 06:47 America/Edmonton. Ctrl+C stops.")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        pass


def _ids(xs: list[str]) -> str:
    return ", ".join(x[:14] for x in xs)


@app.command()
def analyze(site: Optional[str] = typer.Option(None, help="one site (default: all)"),
            min_score: Optional[float] = typer.Option(None, help="score floor (default: winner threshold)"),
            limit: int = typer.Option(10, help="max ads this run (each one is a Claude call)"),
            dry_run: bool = typer.Option(False, "--dry-run", help="show what would be sent; no API calls"),
            redo: bool = typer.Option(False, "--redo", help="re-analyze ads that already have a teardown"),
            whisper: bool = typer.Option(True, "--whisper/--no-whisper", help="local transcript (GPU subprocess)")) -> None:
    """Teardown of top-scoring ads: hook, angle, offer, CTA, pacing, why it works (structure only)."""
    from . import analyst, scout as sc, strategist
    from .store import LocalStore

    store = LocalStore()
    with usage.track("analyze"):
        items = analyst.run(site, min_score, limit, dry_run=dry_run, redo=redo, whisper=whisper, store=store)
        if not dry_run and any(i.status == "analyzed" for i in items):
            pats = strategist.build_patterns(store)
            sc.rescore(store)        # repeat-pattern signal feeds back into scores
        else:
            pats = []
    done = [i for i in items if i.status == "analyzed"]
    failed = [i for i in items if i.status == "failed"]
    tldr(f"{'planned' if dry_run else 'analyzed'} {len(items) - len(failed)} ads, {len(failed)} failed"
         if items else "nothing to analyze: no un-analyzed winners with media (`adlab ads`)",
         f"{len(pats)} patterns now known" if pats else ("dry run: zero API calls, zero GPU" if dry_run else "no new patterns"),
         "next: `adlab patterns`, then `adlab brief --site <site>`")
    for i in items:
        if i.teardown:
            t = i.teardown
            typer.echo(f"  ✓ {i.advertiser[:24]:<24} {t.hook.type:<16} {'/'.join(t.angles):<18} "
                       f"{t.format:<14} {t.pacing.cuts_per_10s:g} cuts/10s\n      why: {t.why_it_works}")
        elif i.status == "planned":
            typer.echo(f"  · {i.advertiser[:24]:<24} {i.message}")
            typer.echo(json.dumps(i.request, indent=1)[:4000])
        else:
            typer.secho(f"  ✗ {i.advertiser[:24]:<24} {i.message}", fg="red")


@app.command()
def hooks(query: str, n: int = typer.Option(5), site: Optional[str] = typer.Option(None)) -> None:
    """Search past teardowns by meaning ("price objection", "night-time problem")."""
    from . import analyst

    hits = analyst.search_hooks(query, n, site)
    tldr(f"{len(hits)} teardowns match \"{query}\"", "closest first", "structure only: never reuse their words")
    for h in hits:
        m = h["metadata"]
        typer.echo(f"  {h['distance']:.3f}  {m.get('hook_type', '?'):<16} {m.get('advertiser', '?')[:24]:<24} "
                   f"[{h['id'][:12]}]\n         {h['document'][:160]}")


@app.command()
def patterns(site: Optional[str] = typer.Option(None)) -> None:
    """Reusable patterns from teardowns, ranked: YOUR results first, then competitor evidence."""
    from . import strategist
    from .store import LocalStore

    store = LocalStore()
    strategist.build_patterns(store)
    ranked = strategist.ranked_patterns(store, site)
    mine = [p for p, _ in ranked if p.own_performance_score is not None]
    tldr(f"{len(ranked)} patterns{' for ' + site if site else ''}",
         f"{len(mine)} scored on YOUR results" if mine else "none scored on your results yet (`adlab report`)",
         f"top: {ranked[0][0].name}" if ranked else "run `adlab analyze` first")
    for p, ev in ranked:
        own = f"you {p.own_performance_score:g}" if p.own_performance_score is not None else "you  -"
        typer.echo(f"  {own:<9} evidence {ev:>5g}  {p.name}\n      hooks {', '.join(p.hook_types)} · "
                   f"angles {', '.join(p.angles)} · {len(p.source_teardown_ids)} ads  [{p.id}]")


@app.command()
def brief(site: str = typer.Option(..., help="site slug, e.g. ev1charger"),
          pattern: Optional[str] = typer.Option(None, help="pattern id or name (default: best ranked)"),
          count: int = typer.Option(3, min=1, max=5),
          ratios: str = typer.Option("9:16,4:5,1:1"),
          dry_run: bool = typer.Option(False, "--dry-run", help="show the request + allowed fact keys; no API call")) -> None:
    """Write 3-5 original briefs for SITE from a winning pattern, every claim tied to a fact."""
    from . import compliance, strategist

    with usage.track("brief"):
        res = strategist.write_briefs(site, pattern, count, [r.strip() for r in ratios.split(",")], dry_run=dry_run)
    drafted = [r for r in res if r.status == "drafted"]
    checks = {r.brief.id: compliance.check_brief(r.brief) for r in drafted if r.brief}
    missing = sorted({m for r in drafted if r.brief for m in r.brief.missing_facts})
    if res and res[0].status in ("blocked", "planned"):
        tldr(res[0].status + (": " + res[0].problems[0] if res[0].problems else ": nothing sent, nothing spent"),
             "fact keys in the schema are your verified facts only",
             "next: `adlab brief --site " + site + "` without --dry-run")
        if res[0].request:
            typer.echo(json.dumps(res[0].request, indent=1))
        return
    v = [c.verdict for c in checks.values()]
    tldr(f"{len(drafted)} briefs drafted, {len(res) - len(drafted)} rejected by the fact check",
         f"compliance: {v.count('PASS')} PASS · {v.count('FIX')} FIX · {v.count('REJECT')} REJECT",
         f"facts wanted but missing (add to facts.yaml): {', '.join(missing)}" if missing
         else "next: `adlab produce --dry-run`")
    for r in res:
        if r.brief:
            c = checks[r.brief.id]
            typer.echo(f"  [{c.verdict}] {r.brief.id}  {r.brief.hook.type} · {r.brief.angle}\n      {r.brief.script}")
            for reason in (x for ch in c.checks for x in ch.reasons):
                typer.secho(f"      {reason}", fg="yellow")
        else:
            typer.secho(f"  ✗ rejected: {'; '.join(r.problems)}", fg="red")


@app.command()
def revise(site: Optional[str] = typer.Option(None),
           dry_run: bool = typer.Option(False, "--dry-run", help="show the request; no API call")) -> None:
    """Rewrite briefs from your Edit notes (still fact-locked), ready for `adlab produce`."""
    from . import compliance, strategist

    with usage.track("revise"):
        revs = strategist.revise_briefs(site, dry_run=dry_run)
    done = [r for r in revs if r.result.status == "drafted"]
    missing = sorted({m for r in done if r.result.brief for m in r.result.brief.missing_facts})
    tldr(f"{len(revs)} briefs with Edit notes · {len(done)} revised" if revs else "no ads marked Edit",
         "dry run: nothing sent" if dry_run else ("old ads marked 'revised' (out of the queue)" if done
                                                   else "nothing changed"),
         f"facts your notes need (add to facts.yaml): {', '.join(missing)}" if missing
         else "next: `adlab produce`")
    for r in revs:
        typer.echo(f"  {r.old_brief_id}: {r.result.status}  notes: {' / '.join(r.notes)}")
        if r.result.brief:
            c = compliance.check_brief(r.result.brief)
            typer.echo(f"    → [{c.verdict}] {r.result.brief.id}  {r.result.brief.script}")
        for p in r.result.problems:
            typer.secho(f"    ✗ {p}", fg="red")
        if r.result.request:
            typer.echo(json.dumps(r.result.request, indent=1)[:4000])


@app.command()
def check(brief_id: Optional[str] = typer.Argument(None), site: Optional[str] = typer.Option(None)) -> None:
    """Re-run the Compliance Officer on one brief or every draft brief."""
    from . import compliance
    from .schemas import Brief
    from .store import LocalStore

    store = LocalStore()
    briefs = [store.fetch("brief", brief_id, Brief)] if brief_id else store.list("brief", Brief, site=site)
    reports = [(b, compliance.check_brief(b, store)) for b in briefs if b]
    v = [r.verdict for _, r in reports]
    tldr(f"{len(reports)} briefs checked: {v.count('PASS')} PASS · {v.count('FIX')} FIX · {v.count('REJECT')} REJECT",
         "checks: fact trace · similarity to source ads · banned content · platform policy",
         "edit config/policy.yaml to tune the platform rules")
    for b, r in reports:
        typer.echo(f"  [{r.verdict}] {b.id} {b.script[:70]}")
        for reason in (x for ch in r.checks for x in ch.reasons):
            typer.secho(f"      {reason}", fg="yellow" if reason.startswith("[FIX]") else "red")


@app.command()
def produce(site: Optional[str] = typer.Option(None),
            brief_id: Optional[list[str]] = typer.Option(None, "--brief", help="brief id (repeatable)"),
            ratios: Optional[str] = typer.Option(None, help="e.g. 9:16,4:5,1:1 (default: the brief's)"),
            dry_run: bool = typer.Option(False, "--dry-run", help="shot list, VO, facts, payloads; nothing spent"),
            allow_fix: bool = typer.Option(False, "--allow-fix", help="also render FIX briefs (flagged for review)"),
            limit: int = typer.Option(5)) -> None:
    """Render draft briefs into finished ads (9:16 / 4:5 / 1:1) for your review queue."""
    from . import producer

    with usage.track("produce"):
        r = producer.produce(site, brief_id, [x.strip() for x in ratios.split(",")] if ratios else None,
                             dry_run=dry_run, allow_fix=allow_fix, limit=limit)
    will = [p for p in r.plans if p.will_render]
    if dry_run:
        tldr(f"{len(will)}/{len(r.plans)} briefs would render ({sum(len(p.ratios) for p in will)} ads)",
             "dry run: zero API calls, zero GPU, zero renders",
             "next: `adlab produce` (same flags, no --dry-run)")
        for p in r.plans:
            typer.secho(f"\n■ {p.brief_id}  [{p.verdict}]  ratios {', '.join(p.ratios)}  "
                        f"est. ${p.estimated_cost_usd if p.estimated_cost_usd is not None else '?'}", bold=True)
            for n in p.notes + p.reasons:
                typer.secho(f"  ! {n}", fg="yellow")
            typer.echo("  shots:")
            for s in p.shots:
                typer.echo(f"    {s.index}. {s.seconds:g}s {s.asset} -> {s.backend}: {s.visual}"
                           + (f" | text: {s.on_screen_text}" if s.on_screen_text else "")
                           + (f" | VO: {s.vo_line}" if s.vo_line else "") + (f"  ({s.note})" if s.note else ""))
            typer.echo("  VO script: " + (" / ".join(p.vo_script) or "(none)"))
            typer.echo("  facts: " + "; ".join(f"{f['fact_key']} = \"{f['statement']}\"" for f in p.tagged_facts))
            if p.missing_facts:
                typer.secho("  missing facts (add to facts.yaml): " + ", ".join(p.missing_facts), fg="yellow")
            for title, items in (("Media Forge", p.mediaforge_calls), ("ComfyUI", p.comfyui_payloads),
                                 ("ElevenLabs", p.elevenlabs_payloads)):
                if items:
                    typer.echo(f"  {title} payloads:")
                    typer.echo("    " + json.dumps(items, indent=1).replace("\n", "\n    "))
        return
    tldr(f"{len(r.creatives)} ads rendered into the review queue" + (f", {len(r.errors)} briefs failed" if r.errors else ""),
         f"skipped: {'; '.join(r.skipped)}" if r.skipped else "every ad is flagged AI-generated in its metadata",
         f"manifest: {r.manifest}" if r.manifest else "next: `adlab review`")
    for c in r.creatives:
        typer.echo(f"  [{c['verdict']}] {c['id']} {c['ratio']:<5} {c['path']}")
    for e in r.errors:
        typer.secho(f"  ✗ {e}", fg="red")


review_app = typer.Typer(invoke_without_command=True, help="Review queue: approve / edit / kill each ad.")
app.add_typer(review_app, name="review")


@review_app.callback()
def review_list(ctx: typer.Context, site: Optional[str] = typer.Option(None),
                status: str = typer.Option("pending", help="pending | edit | approved | killed | rejected | revised")) -> None:
    """Show the queue (default) or decide: `adlab review approve <id>`."""
    if ctx.invoked_subcommand:
        return
    from . import review as rv
    from .store import LocalStore

    store = LocalStore()
    items = rv.queue(store, site, statuses=tuple(status.split(",")))
    n = rv.counts(store, site)
    tldr(f"{len(items)} ads {status}" + (f" · top: {items[0].creative.id}" if items else ""),
         " · ".join(f"{k} {v}" for k, v in sorted(n.items())) or "no ads produced yet",
         "approve / edit / kill: `adlab review approve <id>` (or the dashboard: `adlab serve`)")
    for i in items:
        b = i.brief
        verdict = i.compliance.verdict if i.compliance else "?"
        typer.echo(f"  [{verdict}] {i.creative.id} {i.creative.aspect_ratio:<5} {b.site if b else ''} "
                   f"{b.hook.type if b else ''}\n      \"{b.script if b else ''}\"\n      {i.creative.path}")


def _decide(cid: str, decision: str, notes: str) -> None:
    from . import review as rv
    from .store import LocalStore

    try:
        d = rv.decide(LocalStore(), cid, decision, notes)  # type: ignore[arg-type]
    except rv.ReviewError as exc:
        typer.secho(f"✗ {exc}", fg="red")
        raise typer.Exit(1)
    typer.echo(f"{cid}: {rv.STATUS[d.decision]}" + (f" ({d.notes})" if d.notes else ""))


@review_app.command("approve")
def review_approve(creative_id: str, notes: str = typer.Option("")) -> None:
    """Mark ready for export (nothing is uploaded)."""
    _decide(creative_id, "approve", notes)


@review_app.command("edit")
def review_edit(creative_id: str, notes: str = typer.Argument(..., help="what to change")) -> None:
    """Send back with notes."""
    _decide(creative_id, "edit", notes)


@review_app.command("kill")
def review_kill(creative_id: str, notes: str = typer.Option("")) -> None:
    """Never run this one."""
    _decide(creative_id, "kill", notes)


@app.command("export")
def export_cmd(platform: Optional[list[str]] = typer.Option(None, help="meta / tiktok (repeatable)"),
               site: Optional[str] = typer.Option(None)) -> None:
    """Approved ads -> upload-ready files + upload sheet per platform. You upload them."""
    from . import export as ex

    r = ex.export(platforms=tuple(platform or ("meta", "tiktok")), site=site)
    tldr(f"{sum(len(v) for v in r.files.values())} files exported" + (f" to {r.folder}" if r.folder else ""),
         "keep the [adlab:cr_…] tag in each ad name: the Reporter uses it",
         "turn ON the platform's AI-content label for every ad")
    for p, sheet in r.sheets.items():
        typer.echo(f"  {p}: {len(r.files[p])} files · sheet {sheet}")
    for s in r.skipped:
        typer.secho(f"  ! {s}", fg="yellow")


@app.command()
def report(pull_meta: bool = typer.Option(False, "--pull-meta", help="Meta Insights API (META_ADS_TOKEN)"),
           import_csv: Optional[Path] = typer.Option(None, "--import", help="Ads Manager CSV export"),
           platform: str = typer.Option("meta", help="meta | tiktok (for --import)"),
           days: int = typer.Option(28), site: Optional[str] = typer.Option(None)) -> None:
    """What converts for YOU: results by pattern, hook and ad; re-ranks patterns."""
    from . import reporter
    from .store import LocalStore

    store = LocalStore()
    notes = []
    if pull_meta:
        r = reporter.pull_meta(store, days=days)
        notes += [f"Meta: {r.stored} rows stored, {r.unmatched} without an [adlab:] tag"] + r.warnings
    if import_csv:
        r = reporter.import_csv(store, import_csv, platform)  # type: ignore[arg-type]
        notes += [f"{import_csv.name}: {r.stored} rows stored, {r.unmatched} without an [adlab:] tag"] + r.warnings
        if r.unknown_creatives:
            notes.append(f"unknown creative ids: {_ids(r.unknown_creatives)}")
    scores = reporter.rerank(store)
    rep = reporter.performance(store, site, days)
    t = rep.total
    best = rep.by_pattern[0] if rep.by_pattern else None
    money = lambda v: f"${v:,.2f}" if v is not None else "n/a"  # noqa: E731
    tldr(f"last {days} days: {money(t.spend)} spent · {t.leads} leads · CPL {money(t.cpl)} · "
         f"CTR {t.ctr * 100:.2f}%" if t.ctr is not None else f"no results in the last {days} days",
         f"best pattern: {best['label']} (CPL {money(best['cpl'])})" if best else "import results: --import or --pull-meta",
         f"{sum(1 for v in scores.values() if v is not None)} patterns re-ranked on your results")
    for n in notes:
        typer.echo(f"  · {n}")
    for title, rows in (("by pattern", rep.by_pattern), ("by hook", rep.by_hook), ("by ad", rep.by_creative[:10])):
        if rows:
            typer.secho(f"  {title}", bold=True)
            for r in rows:
                ctr = f"{r['ctr'] * 100:.2f}%" if r["ctr"] is not None else "n/a"
                typer.echo(f"    {r['label'][:44]:<44} CTR {ctr:>7} · CPC {money(r['cpc']):>7} · "
                           f"CPL {money(r['cpl']):>8} · {r['leads']} leads")


@app.command()
def digest(send: bool = typer.Option(False, "--send", help="email it via Resend"),
           dashboard_url: str = typer.Option("", help="link in the email")) -> None:
    """Weekly digest: top 5 new hooks, top 3 ads to approve, what's converting."""
    from . import digest as dg

    d = dg.build(dashboard_url=dashboard_url)
    if send:
        typer.echo(f"sent ({dg.send(d)}) to {', '.join(dg.resend_payload(d)['to'])}")
        return
    typer.echo(d.subject + "\n")
    typer.echo(d.text)


@app.command()
def demo(folder: Optional[Path] = typer.Option(None, help="default: data/demo")) -> None:
    """Run the whole pipeline offline on made-up DEMO data ($0, no keys, no GPU)."""
    from . import demo as dm

    r = dm.run(folder)
    root = r["root"]
    tldr(f"demo: {r['analyzed']} teardowns, briefs {r['briefs']}, {r['creatives']} ads rendered",
         f"all in {root} (canned Claude answers, text-card visuals, no VO)",
         "see it: run the serve command below, then `npm run dev` in adlab/dashboard")
    typer.echo(f"  ADLAB_DATA_DIR={root}/data ADLAB_SITES_DIR={root}/sites adlab serve")
    for e in r["errors"]:
        typer.secho(f"  ✗ {e}", fg="red")


@app.command()
def serve(host: str = typer.Option("127.0.0.1"), port: int = typer.Option(8430)) -> None:
    """Local API for the dashboard (adlab/dashboard)."""
    import os

    import uvicorn

    if host not in ("127.0.0.1", "localhost", "::1") and not os.environ.get("ADLAB_API_TOKEN"):
        typer.secho("refusing to bind beyond localhost without ADLAB_API_TOKEN set", fg="red")
        raise typer.Exit(2)
    uvicorn.run("adlab.api:app", host=host, port=port, log_level="info")


def main() -> None:  # console_scripts entry point
    app()


if __name__ == "__main__":
    main()
