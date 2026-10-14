"""`adlab`: one command per step.

    adlab init                      create data/ folders + the ChromaDB collection
    adlab sites                     every site: facts ready, brand gaps
    adlab facts ev1charger          which facts ads may use, and why the rest can't
    adlab mf status                 Media Forge: up?, GPU, engines, capability map
    adlab mf release                free Media Forge's VRAM
    adlab mf smoke                  Phase 1 smoke test (9:16 image, VRAM before/after)
    adlab usage                     token + cost ledger

    adlab ingest | scout | analyze | brief | produce | review | report   (Phases 2-7)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
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


def _phase(n: int, what: str):
    def cmd(site: Optional[str] = typer.Option(None, help="site slug")) -> None:
        typer.secho(f"Not built yet: {what} arrives in Phase {n}.", fg="yellow")
        raise typer.Exit(2)
    cmd.__doc__ = f"(Phase {n}) {what}"
    return cmd


for _name, _n, _what in (
    ("ingest", 2, "ingest a dropped ad file / pasted Ad Library link"),
    ("scout", 2, "find and score winning ads"),
    ("analyze", 3, "teardown: why high-scoring ads work"),
    ("brief", 4, "write fact-checked briefs from patterns"),
    ("produce", 5, "render briefs through Media Forge"),
    ("review", 7, "review queue: approve / edit / kill"),
    ("report", 7, "what converts for you"),
):
    app.command(_name)(_phase(_n, _what))


def main() -> None:  # console_scripts entry point
    app()


if __name__ == "__main__":
    main()
