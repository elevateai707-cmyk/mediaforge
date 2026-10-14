"""Phase 6, Compliance Officer: every brief and every creative, before you see it.

Four checks, each producing reasons tagged [REJECT] or [FIX]:

* fact_trace: each claim's text may only say what its fact says (no numbers or
  content the statement lacks, so a valid key on an inflated claim is caught), and
  every number / $ / % anywhere in the copy must come from a cited fact.
* similarity: our copy vs every scouted ad of the site (ad text, transcript, on-screen
  text): no 6+ word verbatim run, and under 25% shared 4-word phrases. Creatives also
  get a visual check: frame dHash vs the source ads' media.
* banned_content: brand banned words, testimonials/reviews/ratings, guarantees not in
  a cited fact, people presented as customers, competitors' and other brands' names.
* platform_policy: config/policy.yaml (editable starter rules).

Verdict: any [REJECT] -> REJECT; any [FIX] -> FIX; else PASS.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

import yaml
from pydantic import BaseModel

from .config import settings
from .schemas import Brief, CheckResult, ComplianceReport, Creative, Teardown
from .sites import Site, load_site
from .store import LocalStore
from .variants import dhash_image, hamming

STOP = set("a an the and or of to for in on at by with your you our we is are be this that it its from as "
           "get got all any".split())
SUPERLATIVES = re.compile(r"\b(best|top|#1|number one|fastest|cheapest|lowest|leading|most trusted|only)\b", re.I)
NUM = re.compile(r"(?:\$\s?)?\d[\d,]*(?:\.\d+)?\s?(?:%|k\b|x\b)?", re.I)
TESTIMONIAL = re.compile(
    r"(\breviews?\b|\brated\b|\bratings?\b|\bstars?\b|★|\btestimonials?\b|\b(customers?|clients?|homeowners?) "
    r"(say|love|agree|rave)\b|\bas seen on\b|\btrusted by\b|\bhappy (customers|clients)\b|"
    r"[\"“][^\"”]{8,}[\"”]\s*[-–—]\s*[A-Z][a-z]+)", re.I)
GUARANTEE = re.compile(r"\b(guarantee[ds]?|money[- ]back|risk[- ]free|warrant(y|ied))\b", re.I)
PERSON_AS_CUSTOMER = re.compile(r"\b(customer|client|homeowner|testimonial|reviewer|real person|celebrity|influencer)s?\b", re.I)
MAX_VERBATIM_RUN = 6
MAX_SHARED_4GRAMS = 0.25
VISUAL_MAX_HAMMING = 8


class Policy(BaseModel):
    rules: list[dict] = []
    manual_checks: list[str] = []
    other_brands: list[str] = []


@lru_cache(maxsize=4)
def _policy(path: str) -> Policy:
    p = Path(path)
    return Policy(**(yaml.safe_load(p.read_text(encoding="utf-8")) or {})) if p.is_file() else Policy()


def policy() -> Policy:
    return _policy(str(settings().config_dir / "policy.yaml"))


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9$%#']+", text.lower())


def numbers(text: str) -> set[str]:
    return {re.sub(r"[\s,]", "", m.group(0)).lower().lstrip("$") for m in NUM.finditer(text) if any(c.isdigit() for c in m.group(0))}


def copy_lines(b: Brief) -> list[tuple[str, str]]:
    """(where, text) for every piece of copy a viewer sees or hears."""
    out = [("script", b.script), ("cta", b.cta), ("hook", b.hook.on_screen_text)]
    for s in b.shots:
        out += [(f"shot {s.index} text", s.on_screen_text), (f"shot {s.index} VO", s.vo_line)]
    return [(w, t) for w, t in out if t]


# --- sources ----------------------------------------------------------------------------
class SourceText(BaseModel):
    ad_id: str
    advertiser: str
    text: str


def source_texts(store: LocalStore, site: str) -> list[SourceText]:
    out = []
    for ad in store.ads(site):
        parts = [ad.ad_text]
        tr = settings().data_dir / "transcripts" / f"{ad.id}.json"
        if tr.is_file():
            parts += [s["text"] for s in json.loads(tr.read_text())["segments"]]
        td = store.fetch("teardown", ad.id, Teardown)
        if td:
            parts += [td.hook.on_screen_text] + [s.on_screen_text for s in td.timeline]
        text = " ".join(p for p in parts if p)
        if text.strip():
            out.append(SourceText(ad_id=ad.id, advertiser=ad.source.advertiser, text=text))
    return out


def competitor_names(store: LocalStore, site: str) -> set[str]:
    names = {a.source.advertiser.strip() for a in store.ads(site)}
    names |= set(policy().other_brands)
    return {n for n in names if n and n.lower() != "unknown" and len(n) >= 3}


# --- checks -------------------------------------------------------------------------------
def fact_trace(b: Brief, site: Site) -> CheckResult:
    reasons = []
    usable = set(site.facts.usable_keys())
    cited_numbers: set[str] = set()
    for i, c in enumerate(b.claims, 1):
        fact = site.facts.facts.get(c.fact_key)
        if fact is None or c.fact_key not in usable:
            reasons.append(f"[REJECT] claim {i} cites {c.fact_key!r}, which isn't a usable fact")
            continue
        st = fact.statement
        cited_numbers |= numbers(st)
        extra_nums = numbers(c.text) - numbers(st)
        if extra_nums:
            reasons.append(f"[REJECT] claim {i} \"{c.text}\" has numbers {sorted(extra_nums)} that fact "
                           f"{c.fact_key!r} doesn't: \"{st}\"")
        new_words = {w for w in words(c.text) if w not in STOP} - set(words(st))
        sup = SUPERLATIVES.search(c.text)
        if sup and not SUPERLATIVES.search(st):
            reasons.append(f"[REJECT] claim {i} inflates fact {c.fact_key!r} with \"{sup.group(0)}\"")
        elif len(new_words) > max(1, len(set(words(c.text))) // 4):
            reasons.append(f"[FIX] claim {i} \"{c.text}\" drifts from its fact (\"{st}\"): reuse the fact's wording")
    for where, text in copy_lines(b):
        stray = numbers(text) - cited_numbers
        if stray:
            reasons.append(f"[REJECT] {where} uses {sorted(stray)} but no cited fact contains it")
    return CheckResult(name="fact_trace", passed=not reasons, reasons=reasons)


def text_similarity(b: Brief, sources: list[SourceText]) -> CheckResult:
    ours = " ".join(t for _, t in copy_lines(b))
    ow = words(ours)
    our4 = {tuple(ow[i:i + 4]) for i in range(len(ow) - 3)}
    reasons = []
    for s in sources:
        sw = words(s.text)
        run_grams = {tuple(sw[i:i + MAX_VERBATIM_RUN]) for i in range(len(sw) - MAX_VERBATIM_RUN + 1)}
        hit = next((" ".join(ow[i:i + MAX_VERBATIM_RUN]) for i in range(len(ow) - MAX_VERBATIM_RUN + 1)
                    if tuple(ow[i:i + MAX_VERBATIM_RUN]) in run_grams), None)
        if hit:
            reasons.append(f"[REJECT] copies \"{hit}\" from {s.advertiser}'s ad [{s.ad_id[:12]}]")
            continue
        src4 = {tuple(sw[i:i + 4]) for i in range(len(sw) - 3)}
        if our4:
            shared = len(our4 & src4) / len(our4)
            if shared >= MAX_SHARED_4GRAMS:
                reasons.append(f"[REJECT] {shared:.0%} of our phrases appear in {s.advertiser}'s ad [{s.ad_id[:12]}]")
    return CheckResult(name="similarity", passed=not reasons, reasons=reasons)


def banned_content(b: Brief, site: Site, brands: Iterable[str]) -> CheckResult:
    reasons = []
    cited = " ".join(site.facts.facts[c.fact_key].statement for c in b.claims if c.fact_key in site.facts.facts)
    for where, text in copy_lines(b):
        low = text.lower()
        for w in site.brand.banned_words:
            if re.search(rf"(?<![\w#]){re.escape(w.lower())}(?!\w)", low) and w.lower() not in cited.lower():
                reasons.append(f"[FIX] {where}: banned word \"{w}\"")
        t = TESTIMONIAL.search(text)
        if t:
            reasons.append(f"[REJECT] {where}: testimonial / review / social-proof language \"{t.group(0)}\"")
        g = GUARANTEE.search(text)
        if g and not GUARANTEE.search(cited):
            reasons.append(f"[REJECT] {where}: \"{g.group(0)}\" isn't backed by a cited fact")
    for s in b.shots:
        p = PERSON_AS_CUSTOMER.search(s.visual)
        if p:
            reasons.append(f"[REJECT] shot {s.index} visual depicts a {p.group(0)}: no synthetic or real people as customers")
    everything = " ".join([t for _, t in copy_lines(b)] + [s.visual for s in b.shots]).lower()
    for name in brands:
        if re.search(rf"(?<!\w){re.escape(name.lower())}(?!\w)", everything):
            reasons.append(f"[REJECT] mentions another brand/advertiser: \"{name}\"")
    return CheckResult(name="banned_content", passed=not reasons, reasons=reasons)


def platform_policy(b: Brief, platforms: Iterable[str] = ("meta", "tiktok")) -> CheckResult:
    reasons = []
    wanted = set(platforms)
    for rule in policy().rules:
        if not wanted & set(rule.get("platforms", [])):
            continue
        rx = re.compile(rule["pattern"], re.I)
        for where, text in copy_lines(b):
            if rx.search(text):
                reasons.append(f"[{rule.get('severity', 'FIX')}] {where}: {rule['message']} ({rule['id']})")
    return CheckResult(name="platform_policy", passed=not reasons, reasons=reasons)


def visual_similarity(creative: Creative, store: LocalStore, site: str) -> CheckResult:
    ours = frame_hashes(Path(creative.path))
    theirs = {}
    media = store.media_for(a.id for a in store.ads(site))
    for ad_id, rows in media.items():
        for m in rows:
            if m["dhash"]:
                theirs.setdefault(ad_id, []).append(m["dhash"])
    reasons = []
    for ad_id, hashes in theirs.items():
        d = min((hamming(a, b) for a in ours for b in hashes), default=64)
        if d <= VISUAL_MAX_HAMMING:
            reasons.append(f"[REJECT] a frame looks like source ad [{ad_id[:12]}] (dHash distance {d})")
    return CheckResult(name="visual_similarity", passed=not reasons, reasons=reasons)


def frame_hashes(path: Path, samples: int = 4) -> list[str]:
    if path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
        return [dhash_image(path)]
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return []
    out = []
    with tempfile.TemporaryDirectory(prefix="adlab_cmp_") as tmp:
        proc = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-i", str(path), "-vf",
                               f"fps=1,scale=256:-2", "-frames:v", str(samples), f"{tmp}/f%02d.png"],
                              capture_output=True, timeout=120)
        if proc.returncode == 0:
            out = [dhash_image(p) for p in sorted(Path(tmp).glob("f*.png"))]
    return out


def verdict(checks: list[CheckResult]) -> str:
    reasons = [r for c in checks for r in c.reasons]
    if any(r.startswith("[REJECT]") for r in reasons):
        return "REJECT"
    return "FIX" if reasons else "PASS"


def check_brief(b: Brief, store: Optional[LocalStore] = None, site: Optional[Site] = None,
                save: bool = True) -> ComplianceReport:
    store = store or LocalStore()
    site = site or load_site(b.site)
    checks = [fact_trace(b, site), text_similarity(b, source_texts(store, b.site)),
              banned_content(b, site, competitor_names(store, b.site)), platform_policy(b)]
    report = ComplianceReport(creative_id=f"brief:{b.id}", verdict=verdict(checks), checks=checks)  # type: ignore[arg-type]
    if save:
        store.put("compliance", report, id=f"brief:{b.id}", site=b.site, ref=b.id, status=report.verdict)
    return report


def check_creative(c: Creative, b: Brief, store: Optional[LocalStore] = None) -> ComplianceReport:
    store = store or LocalStore()
    pre = check_brief(b, store, save=False)
    checks = pre.checks + [visual_similarity(c, store, b.site)]
    report = ComplianceReport(creative_id=c.id, verdict=verdict(checks), checks=checks)  # type: ignore[arg-type]
    store.put("compliance", report, id=c.id, site=b.site, ref=b.id, status=report.verdict)
    return report
