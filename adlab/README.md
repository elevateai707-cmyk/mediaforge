# AdLab

Your small, local marketing department: **find winning ads → learn why they work →
write original briefs for your offers → produce them through Media Forge → check
them → learn from your own results.** Deterministic Python stages with strict
Pydantic contracts between them; LLMs do specific jobs, never free-form chat.

```
Scout ─► Analyst ─► Strategist ─► Producer ─► Compliance ─► Review (you) ─► Reporter
 Ad     Teardown    Pattern/Brief   Creative    Report        approve/kill     Result ─┐
  ▲                                                                                     │
  └──────────────── patterns re-ranked by what converts for YOU ◄─────────────────────┘
```

**Hard rules, enforced in code:**
- Zero fabrication: claims must cite a verified fact.
- Learn structure, never copy.
- Official data sources only.
- `--dry-run` comes first.
- Every creative is flagged as AI-generated.
- Nothing is uploaded or spends money without your approval.

## Phase 1: skeleton + Media Forge interface ✅

**TL;DR**
- Schemas for every stage, the fact checker, the Supabase schema, ChromaDB, the GPU lock and the cost ledger are built and tested (31 tests).
- Media Forge is wired in (status, VRAM release, test image). It **can't generate images, video or voiceover**, so see the question below.
- Next for you: fill `sites/*/facts.yaml` + `brand.yaml`, and answer the ComfyUI question.

### Setup (Ubuntu)
```bash
cd adlab
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp .env.example .env            # fill keys as phases need them
.venv/bin/adlab init
```

### Demo
```bash
adlab sites                 # 5 sites, facts usable, brand gaps
adlab facts ev1charger      # which claims ads may make (none until you verify)
adlab mf status             # Media Forge up? + what it can/can't do for ads
adlab mf smoke              # 9:16 test image, VRAM logged before/after
.venv/bin/pytest -q         # 31 tests
```

### What's in it
| Piece | File | What it guarantees |
|---|---|---|
| Stage schemas | `adlab/schemas.py` | `Ad → Teardown → Pattern → Brief → Creative → ComplianceReport → Result`. Unknown fields rejected. `Creative.ai_generated` can only be `true`. |
| Facts | `adlab/facts.py`, `sites/*/facts.yaml` | Brief claims are `{text, fact_key}` with `fact_key: Literal[verified keys]`. Unknown, unverified or expired keys fail validation, and `check_brief()` names the broken claim. **Templates ship with zero usable facts on purpose.** |
| Brand | `sites/*/brand.yaml` | Colours (#RRGGBB), fonts, logo, tone, end card, banned words. |
| Supabase | `supabase/migrations/20261014000000_adlab_init.sql` | ads, teardowns, patterns, briefs, creatives, compliance_reports, results, llm_usage. RLS on everywhere (service key only); `ai_generated` must be true. |
| Vectors | `adlab/vectors.py` | ChromaDB `teardowns` collection, embedding structure (not copy), filterable by site, hook and format. Local CPU embeddings. |
| GPU | `adlab/gpu.py` | One GPU job at a time (file lock), `nvidia-smi` VRAM logged before/after, refuses to start when VRAM is too low, ephemeral subprocesses for local models. |
| Cost | `adlab/usage.py`, `config/pricing.yaml` | Tokens and $ per command; unknown prices are flagged, never guessed. |
| Media Forge | `adlab/mediaforge.py`, `docs/mediaforge_interface.md` | Status, VRAM release (`POST /api/gpu/release`, added to Media Forge for this), stills at 9:16/1:1/4:5/16:9 via Elevate Vision Studio. |
| CLI | `adlab/cli.py` | `adlab init / sites / facts / mf status / mf release / mf smoke / usage`; later phases are stubs. |

### What I need from you
1. **Where does ComfyUI live?** Media Forge has no image/video generator. Give me its URL
   and folder, and export your still-image and short-video workflows as **API-format
   JSON** into `config/comfyui/`. Or point me at the other "Media Forge" if there is one.
   Details: `docs/mediaforge_interface.md` → Open question.
2. **Facts:** fill `sites/ev1charger/facts.yaml` and `sites/albertaweb/facts.yaml`
   (exact wording + `verified: true` + `source`). Check `adlab facts <site>`.
3. **Portfolio sites:** one line each on what retrofitnetwork.ca, electrifyhq.com and
   velvetthrone.ca sell (`site.yaml → summary`). I left them blank rather than guess.
4. **Brand:** colours, fonts, logo file, and tone in each `brand.yaml`.
5. **Keys (when we get there):** Anthropic (Phase 3), Supabase (Phase 2),
   ElevenLabs (Phase 5), Resend (Phase 7).

## Phase 2: Scout ✅

**TL;DR**
- Drop ads you saved into `data/incoming/<site>/` (or paste a link with `adlab ingest`).
  AdLab stores, dedupes, groups variants and scores them. **Nothing is downloaded.**
- `adlab scout` → new winners + the top 5. `adlab ads` → everything, with each score explained.
- Official APIs give **no Canadian commercial ads** (`docs/data_sources.md`), so manual drops are the main source.

### Feed it
```bash
# 1. Drop files (video or screenshot) into the site's folder, with an optional sidecar:
data/incoming/ev1charger/rival-reel.mp4
data/incoming/ev1charger/rival-reel.mp4.yaml      # all fields optional:
#   advertiser: Rival Electric
#   platform: meta            # meta | tiktok | other
#   url: https://www.facebook.com/ads/library/?id=...
#   running_since: 2026-06-20
#   still_running: yes
#   last_seen: 2026-09-30     # for stopped ads, if you know when
#   variants: 3               # versions you saw
#   platform_rank: 8          # e.g. Creative Center Top Ads position
#   ad_text: "..."            # the copy, if you have it
#   notes: seen in Calgary feed

# 2. Or paste a link (+ the file you saved). The link is a reference only, never fetched:
adlab ingest "https://www.facebook.com/ads/library/?id=123" ~/Videos/ad.mp4 \
    --site ev1charger --advertiser "Rival Electric" --running-since 2026-06-20 --running

# 3. CSV/JSON you exported from a platform's own tool: drop it in the site's folder too.
#    Columns like advertiser, ad_text, url, first_seen, last_seen, still_running, variants, rank
#    (Meta API JSON {"data": [...]} works as-is).

adlab ingest --scan      # process the folder once  (or --watch to keep going)
adlab scout              # drops + enabled APIs → variants → scores → new winners
adlab ads --site ev1charger
```
Processed drops move to `incoming/_done/<date>/`. Problems go to `incoming/_failed/<date>/`
with a `.error.txt` saying why (for example, "which site?").

### How it decides
| | |
|---|---|
| **Dedupe** | Same file bytes (SHA-256) = same ad, even renamed. A known link attaches new files to its ad. Re-seeing an ad extends its dates instead of duplicating it. |
| **Variants** | Same advertiser + near-identical visual (dHash of image / video frame, ≤ 10 bits apart) or copy (≥ 80% similar) = same concept. Re-encodes and resizes are caught. |
| **Score** (`config/scoring.yaml`) | Longevity 30/60/90 days → 15/25/35, still running +5, +5 per extra variant (max 25), platform rank top 10/50/100 → 20/12/6, repeat pattern (Phase 3, max 15). Winner ≥ 50. A stopped ad only counts the days it was *known* to run. |
| **Privacy** | Media copies live in `data/media/` with owner-only permissions, for analysis only. Tokens and click-trackers are stripped from every stored link. |
| **Storage** | Local SQLite (`data/adlab.db`) is the source of truth. `adlab sync` mirrors it to Supabase when keys are set (scout runs sync automatically). |

### Official API (optional): Meta Ad Library
Only returns commercial ads that **reached the EU**, so it's good for pattern research on
Irish/EU installers and agencies, not Alberta competitors. To use it: set `META_AD_LIBRARY_TOKEN`
in `.env`, then `enabled: true` in `sites/<site>/scout.yaml`. It runs on each `adlab scout`
and weekly via `adlab schedule`. It never downloads media and stops on rate limits.

### Run it in the background (systemd user services)
```bash
cp deploy/adlab-watch.service deploy/adlab-schedule.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now adlab-watch adlab-schedule
```

### What I need from you (Phase 2)
1. **Real drops:** 5–10 ads per primary site you think are working (screen recordings or
   screenshots + a sidecar with `running_since` if you can see it). That's the fuel for Phase 3.
2. **Competitors:** names (and Meta Page IDs if you have them) in each `scout.yaml`.
3. **Optional:** a Meta Ad Library API token, if you want EU pattern research.
4. **Optional:** Supabase URL + service-role key to start mirroring.

Phase 3 (Analyst: teardowns with Claude vision + Whisper) starts when you say go.
