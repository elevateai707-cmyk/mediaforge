"""Phase 4, Strategist: teardowns -> reusable patterns -> original briefs for YOUR offers.

Patterns (deterministic, no LLM): teardowns grouped by their beat sequence
(e.g. problem_callout → credential → price_anchor → cta). A pattern's evidence
score = mean winning score of its source ads + 5 per distinct advertiser using it.
Patterns are ranked by YOUR results first (own_performance_score, set by the
Reporter in Phase 7), then by evidence.

Briefs (Claude): for a site + pattern, 3-5 original briefs. The model sees the
pattern's structure and the teardowns' "why it works" (never the source ads' words),
your brand, and ONLY your verified facts. Its output schema already restricts
fact_key to those facts (Literal), and every brief is re-validated with
facts.check_brief before it's stored. Facts it needed but didn't have come back as
`missing_facts` for you to add, never invented.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, create_model

from . import llm
from .facts import FactSheet, NoUsableFacts, check_brief, claim_model_for
from .schemas import Ad, Angle, AspectRatio, Brief, Hook, Pattern, Shot, Teardown
from .sites import Site, load_site
from .store import LocalStore

COMMAND = "brief"
STEP_LABELS = {
    "hook": "Hook", "problem_callout": "Problem call-out", "agitate": "Agitate", "credential": "Credential",
    "demo": "Demo", "social_proof": "Social proof", "price_anchor": "Price anchor", "offer": "Offer",
    "objection_handle": "Objection handling", "urgency": "Urgency", "cta": "CTA",
}


# --- patterns -------------------------------------------------------------------------
def _collapse(beats: list[str]) -> tuple[str, ...]:
    out: list[str] = []
    for b in beats:
        if not out or out[-1] != b:
            out.append(b)
    return tuple(out)


def pattern_id(beats: tuple[str, ...]) -> str:
    return "pat_" + hashlib.sha1("-".join(beats).encode()).hexdigest()[:10]


def build_patterns(store: LocalStore) -> list[Pattern]:
    """Recompute every pattern from stored teardowns (keeps own_performance_score)."""
    teardowns = store.list("teardown", Teardown)
    ads = {a.id: a for a in store.ads()}
    groups: dict[tuple[str, ...], list[Teardown]] = {}
    for t in teardowns:
        beats = _collapse(t.beats)
        if len(beats) >= 2:
            groups.setdefault(beats, []).append(t)
    out = []
    for beats, tds in groups.items():
        pid = pattern_id(beats)
        prev = store.fetch("pattern", pid, Pattern)
        hooks = Counter(t.hook.type for t in tds)
        angles = Counter(a for t in tds for a in t.angles)
        formats = Counter(t.format for t in tds)
        p = Pattern(
            id=pid, name=" → ".join(STEP_LABELS[b] for b in beats), steps=list(beats),  # type: ignore[arg-type]
            hook_types=[h for h, _ in hooks.most_common()], angles=[a for a, _ in angles.most_common()],
            formats=[f for f, _ in formats.most_common()], source_teardown_ids=sorted(t.ad_id for t in tds),
            own_performance_score=prev.own_performance_score if prev else None,
        )
        src_ads = [ads[t.ad_id] for t in tds if t.ad_id in ads]
        sites = {a.site for a in src_ads}
        mean = sum(a.score.total for a in src_ads if a.score) / max(1, len(src_ads))
        advertisers = len({a.source.advertiser.lower() for a in src_ads})
        evidence = round(mean + 5 * advertisers, 1)
        store.put("pattern", p, id=pid, site=",".join(sorted(sites)), score=evidence)
        out.append(p)
    return out


def ranked_patterns(store: LocalStore, site: Optional[str] = None) -> list[tuple[Pattern, float]]:
    """(pattern, evidence) best first: your own results outrank competitor evidence."""
    rows = store.rows("pattern")
    ranked = []
    for r in rows:
        p = Pattern.model_validate_json(r["payload"])
        if site and r["site"] and site not in r["site"].split(","):
            continue
        ranked.append((p, r["score"] or 0.0))
    ranked.sort(key=lambda pe: (pe[0].own_performance_score is not None, pe[0].own_performance_score or 0, pe[1]),
                reverse=True)
    return ranked


def repeat_counts(store: LocalStore) -> dict[str, int]:
    """Per analyzed ad: other advertisers in the same site whose teardowns share an angle."""
    ads = {a.id: a for a in store.ads()}
    tds = [t for t in store.list("teardown", Teardown) if t.ad_id in ads]
    out = {}
    for t in tds:
        me = ads[t.ad_id]
        others = {ads[o.ad_id].source.advertiser.lower() for o in tds
                  if o.ad_id != t.ad_id and ads[o.ad_id].site == me.site
                  and ads[o.ad_id].source.advertiser.lower() != me.source.advertiser.lower()
                  and set(o.angles) & set(t.angles)}
        out[t.ad_id] = len(others)
    return out


# --- briefs ---------------------------------------------------------------------------
def draft_models(sheet: FactSheet, count: int, today: Optional[date] = None):
    claim = claim_model_for(sheet, today)
    draft = create_model(
        "BriefDraft", __config__=ConfigDict(extra="forbid"),
        hook=(Hook, ...), angle=(Angle, ...), script=(str, ...), shots=(list[Shot], ...),
        cta=(str, ...), claims=(list[claim], ...),  # type: ignore[valid-type]
        aspect_ratios=(list[AspectRatio], ...), missing_facts=(list[str], ...),
    )
    brief_set = create_model("BriefSet", __config__=ConfigDict(extra="forbid"),
                             briefs=(list[draft], Field(min_length=1, max_length=count)))  # type: ignore[valid-type]
    return draft, brief_set


SYSTEM = """You are the Strategist in a small performance-marketing team. You write ORIGINAL ad
briefs for the advertiser's own offer, using a proven STRUCTURE (pattern) learned from other ads.

Hard rules (a compliance check enforces them; breaking one gets the brief rejected):
1. Zero fabrication. Every factual statement (credentials, prices, areas, guarantees, times,
   numbers, rebates) must be a `claims` entry citing one of the provided fact keys, and its text
   must say no more than that fact's statement (reuse the statement's wording). Numbers, prices
   and percentages may appear in script, on-screen text or VO ONLY if they appear in a cited fact.
   No testimonials, reviews, ratings, customer counts, "as seen on", or invented results.
   If a brief would need a fact that isn't provided, don't write it: list it in `missing_facts`.
2. Learn the pattern, never copy. Use the beats and the reasons it works; write new words.
   Never mention or imitate competitors, their brands, slogans, music or people.
3. People: visuals may not present any person as a real customer or show real, identifiable
   people. Use objects, places, hands at work, text cards, or the brand's own footage.
4. Respect the brand's tone and never use its banned words.
Shots: number from 1, 1-6 s each, total 15-30 s; asset is one of generated_image,
generated_video, text_card, brand_asset, own_footage. Write vo_line only where a voice speaks.
Each brief must differ meaningfully (different hook angle or visual idea)."""


def brief_prompt(site: Site, pattern: Pattern, teardowns: list[Teardown], count: int,
                 ratios: list[str]) -> str:
    facts = {k: site.facts.facts[k].statement for k in site.facts.usable_keys()}
    why = [f"- ({t.format}, hook: {t.hook.type}) {t.why_it_works}" for t in teardowns[:6]]
    return "\n\n".join([
        f"Write {count} briefs for {site.info.name or site.slug} ({site.info.domain}). "
        f"{site.info.summary or ''} Region: {site.info.region or 'n/a'}.",
        f"Pattern: {pattern.name}\nBeats in order: {', '.join(pattern.steps)}\n"
        f"Hooks that worked: {', '.join(pattern.hook_types)}. Angles: {', '.join(pattern.angles)}. "
        f"Formats: {', '.join(pattern.formats)}.",
        "Why it works in the source ads (structure only):\n" + "\n".join(why),
        "Verified facts you may cite (fact_key: exact statement):\n" + json.dumps(facts, indent=1),
        f"Brand tone: {', '.join(site.brand.tone) or 'plain, confident, local'}. "
        f"Banned words: {', '.join(site.brand.banned_words) or 'none'}. "
        f"End card: {site.brand.end_card_text or site.info.domain}.",
        f"Aspect ratios to plan for: {', '.join(ratios)}.",
    ])


class BriefResult(BaseModel):
    status: Literal["drafted", "rejected", "planned", "blocked"]
    brief: Optional[Brief] = None
    problems: list[str] = []
    request: Optional[dict] = None


def write_briefs(site_slug: str, pattern_ref: Optional[str] = None, count: int = 3,
                 ratios: Optional[list[str]] = None, dry_run: bool = False,
                 store: Optional[LocalStore] = None, today: Optional[date] = None) -> list[BriefResult]:
    store = store or LocalStore()
    ratios = ratios or ["9:16", "4:5", "1:1"]
    site = load_site(site_slug)
    if not store.rows("pattern"):
        build_patterns(store)
    ranked = ranked_patterns(store, site_slug) or ranked_patterns(store)
    if not ranked:
        return [BriefResult(status="blocked", problems=["no patterns yet: run `adlab analyze` on some winners first"])]
    pattern = next((p for p, _ in ranked if pattern_ref in (p.id, p.name)), None) if pattern_ref else ranked[0][0]
    if pattern is None:
        return [BriefResult(status="blocked", problems=[f"unknown pattern {pattern_ref!r}"])]
    tds = [t for i in pattern.source_teardown_ids if (t := store.fetch("teardown", i, Teardown))]
    try:
        _, brief_set = draft_models(site.facts, count, today)
    except NoUsableFacts as exc:
        return [BriefResult(status="blocked", problems=[str(exc)])]
    content = [{"type": "text", "text": brief_prompt(site, pattern, tds, count, ratios)}]
    if dry_run:
        kw = llm.request_kwargs(SYSTEM, content, brief_set, effort="high")
        shown = llm.redacted(kw)
        shown["output_schema"] = brief_set.model_json_schema()
        return [BriefResult(status="planned", request=shown)]

    drafts = llm.parse(COMMAND, SYSTEM, content, brief_set, effort="high")
    return [_store_draft(d.model_dump(), site, pattern, store, today) for d in drafts.briefs]  # type: ignore[attr-defined]


def _store_draft(data: dict, site: Site, pattern: Pattern, store: LocalStore,
                 today: Optional[date]) -> BriefResult:
    """Validate a model-written draft against the facts as they are NOW, then store it."""
    bid = "br_" + hashlib.sha1(f"{site.slug}|{pattern.id}|{data['script']}".encode()).hexdigest()[:12]
    data.update(id=bid, site=site.slug, pattern_id=pattern.id, source_ad_ids=pattern.source_teardown_ids)
    data["shots"] = [dict(s, index=i) for i, s in enumerate(data["shots"], 1)]  # renumber 1..n
    check = check_brief(data, site.facts, today)
    if not check.ok:
        return BriefResult(status="rejected", problems=[
            *(f"claim {p.index + 1} \"{p.text}\": {p.problem}" for p in check.claim_problems),
            *check.other_errors])
    brief = Brief.model_validate(check.brief.model_dump())  # type: ignore[union-attr]
    store.put("brief", brief, id=bid, site=site.slug, ref=pattern.id, status="draft")
    return BriefResult(status="drafted", brief=brief)


# --- revisions from your Edit notes ----------------------------------------------------
REVISE_SYSTEM = SYSTEM + """

You are revising ONE existing brief because the advertiser reviewed the rendered ad and asked
for changes. Apply their notes. The hard rules above still win over the notes: if a note asks
for a claim, number, testimonial or guarantee that no provided fact supports, don't write it;
add what's needed to `missing_facts` instead. Keep everything the notes don't ask to change."""


class Revision(BaseModel):
    old_brief_id: str
    notes: list[str]
    result: BriefResult


def edit_requests(store: LocalStore, site: Optional[str] = None) -> dict[str, list[tuple[str, str]]]:
    """brief id -> [(creative id, your notes)] for every creative you marked Edit."""
    out: dict[str, list[tuple[str, str]]] = {}
    for r in store.rows("creative", site=site, status="edit"):
        rv = store.rows("review", ref=r["ref"])
        note = next((json.loads(x["payload"])["notes"] for x in rv if x["id"] == r["id"]), "")
        out.setdefault(r["ref"], []).append((r["id"], note))
    return out


def revise_prompt(site: Site, brief: Brief, notes: list[str]) -> str:
    facts = {k: site.facts.facts[k].statement for k in site.facts.usable_keys()}
    current = brief.model_dump(include={"hook", "angle", "script", "shots", "cta", "claims", "aspect_ratios",
                                        "missing_facts"})
    return "\n\n".join([
        f"Revise this brief for {site.info.name or site.slug} ({site.info.domain}).",
        "Current brief:\n" + json.dumps(current, indent=1),
        "The advertiser's notes (apply them):\n" + "\n".join(f"- {n}" for n in notes),
        "Verified facts you may cite (fact_key: exact statement):\n" + json.dumps(facts, indent=1),
        f"Brand tone: {', '.join(site.brand.tone) or 'plain, confident, local'}. "
        f"Banned words: {', '.join(site.brand.banned_words) or 'none'}.",
    ])


def revise_briefs(site_slug: Optional[str] = None, dry_run: bool = False, store: Optional[LocalStore] = None,
                  today: Optional[date] = None) -> list[Revision]:
    """One revised brief per brief with Edit notes. Old brief + its edited ads -> 'revised'."""
    store = store or LocalStore()
    out = []
    for brief_id, items in edit_requests(store, site_slug).items():
        old = store.fetch("brief", brief_id, Brief)
        if old is None:
            continue
        notes = sorted({n for _, n in items if n})
        site = load_site(old.site)
        pattern = store.fetch("pattern", old.pattern_id, Pattern)
        if pattern is None:
            out.append(Revision(old_brief_id=brief_id, notes=notes, result=BriefResult(
                status="blocked", problems=[f"pattern {old.pattern_id} is gone: write a new brief instead"])))
            continue
        try:
            _, brief_set = draft_models(site.facts, 1, today)
        except NoUsableFacts as exc:
            out.append(Revision(old_brief_id=brief_id, notes=notes,
                                result=BriefResult(status="blocked", problems=[str(exc)])))
            continue
        content = [{"type": "text", "text": revise_prompt(site, old, notes)}]
        if dry_run:
            shown = llm.redacted(llm.request_kwargs(REVISE_SYSTEM, content, brief_set, effort="high"))
            out.append(Revision(old_brief_id=brief_id, notes=notes,
                                result=BriefResult(status="planned", request=shown)))
            continue
        drafts = llm.parse("revise", REVISE_SYSTEM, content, brief_set, effort="high")
        res = _store_draft(drafts.briefs[0].model_dump(), site, pattern, store, today)  # type: ignore[attr-defined]
        if res.status == "drafted":
            store.set_status("brief", brief_id, "revised")
            for cid, _ in items:
                store.set_status("creative", cid, "revised")
        out.append(Revision(old_brief_id=brief_id, notes=notes, result=res))
    return out
