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
| CLI | `adlab/cli.py` | `adlab init / sites / facts / mf status / mf release / mf smoke / usage` (every later phase is wired in too). |

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

## The weekly loop (all phases) ⚡

**TL;DR**
- One command per step. Every step prints a 3-line TL;DR first.
- `--dry-run` on `analyze`, `brief` and `produce` shows exactly what would be sent. Nothing is spent.
- Try it with no keys: `adlab demo` runs the whole pipeline on made-up DEMO data.

```bash
adlab scout                          # Mon: new winners (drops + APIs)
adlab analyze --site ev1charger      # teardowns of the winners (Claude), patterns rebuilt
adlab brief --site ev1charger        # 3 fact-locked briefs from the best pattern (+ compliance)
adlab produce --dry-run              # shot list, VO script, facts, exact ComfyUI/ElevenLabs payloads
adlab produce                        # 9:16 / 4:5 / 1:1 ads → review queue
adlab review                         # or the dashboard: approve / edit / kill
adlab export                         # upload-ready files + sheet per platform (you upload)
adlab report --import meta.csv       # your results → patterns re-ranked
adlab digest --send                  # Monday email (also automatic via `adlab schedule`)
```

```bash
adlab demo                                           # offline, $0, writes data/demo/
ADLAB_DATA_DIR=data/demo/data ADLAB_SITES_DIR=data/demo/sites adlab serve
cd dashboard && npm install && npm run dev           # http://localhost:3000
```

## Phase 3: Analyst ✅

**TL;DR**
- `adlab analyze` tears down your top-scoring ads: hook (first 3 s), angle, offer, CTA, cuts, captions, music energy, beats, and *why it works*.
- Claude does the vision; pacing is **measured** with FFmpeg, and transcripts come from local Whisper in a throwaway GPU process.
- Structure only: a teardown that quotes the ad is rejected and redone.

| | |
|---|---|
| **Frames** | Dense in the hook (0.2 / 1 / 2 / 3 s), then evenly to the end, max 10, sent at ≤ 1024 px. |
| **Pacing** | FFmpeg scene detection counts cuts; the measured cuts/10 s and length overwrite the model's guess. |
| **Transcript** | `faster-whisper` (`small.en`) in `python -m adlab.workers.whisper_worker`, under the GPU lock, after Media Forge and ComfyUI are told to unload, and only if ≥ 2 GB VRAM is free. Cached in `data/transcripts/`. Install: `pip install -e '.[whisper]'`, or use `--no-whisper`. |
| **Model** | `claude-opus-5-5` (`ADLAB_MODEL`), structured output validated against `TeardownDraft`. **Server-side fallbacks are on** (a capacity error retries on another model, logged); set `ADLAB_LLM_FALLBACKS=0` to pin the model. Refusals and truncations are reported, never half-parsed. |
| **Copy guard** | Any 8-word run copied from the ad's copy or transcript → one retry with a warning, then the ad is marked failed. |
| **After** | Teardowns are stored and embedded in ChromaDB, patterns are rebuilt, and scores get the repeat-pattern signal. |
| **Cost** | Every call goes to `data/logs/usage.jsonl`; `adlab usage` sums it. Estimate: 10 frames ≈ 15k input + ~1.5k output tokens ≈ **$0.09 per ad** at list price; `adlab usage` shows the real number. |

```bash
adlab analyze --site ev1charger --dry-run   # frames, cuts, the exact request; no API call
adlab analyze --site ev1charger --limit 5
adlab hooks "night-time charger problem"    # semantic search over teardowns
adlab patterns                              # beats-based patterns, ranked
```

## Phase 4: Strategist ✅

**TL;DR**
- Patterns = teardowns grouped by their beat sequence (e.g. problem call-out → credential → CTA). Ranked by **your** results first, then by competitor evidence.
- `adlab brief --site S` writes 3–5 original briefs. The output schema only allows your verified fact keys, and each brief is re-validated before it's saved.
- Facts a brief wanted but didn't have come back as **"missing facts"** for you to add. Never filled in.

What Claude sees: the pattern, each source ad's *why it works*, your site summary, brand tone, banned
words and **only your usable facts**. It never sees the source ads' words. Each brief has a hook,
angle, script, numbered shot list (visual, on-screen text, VO line), CTA, claims `{text, fact_key}`
and aspect ratios.

```bash
adlab brief --site ev1charger --dry-run      # request + the fact-key enum; nothing sent
adlab brief --site ev1charger --count 3 --pattern pat_ab12cd34ef
adlab check                                  # re-run compliance on every draft
```

## Phase 5: Producer ✅

**TL;DR**
- `adlab produce` turns PASS briefs into finished MP4s at **9:16, 4:5 and 1:1**: visuals, ElevenLabs VO, burned-in captions, branded end card.
- Visuals come from **your ComfyUI workflows**. Until you set them up, shots become brand text cards and the manifest says so, so the pipeline already runs end to end.
- `--dry-run` prints the shot list, VO script, tagged facts and the exact Media Forge / ComfyUI / ElevenLabs payloads. Zero calls, zero GPU.

| | |
|---|---|
| **Gate** | Compliance runs first. REJECT never renders. FIX renders only with `--allow-fix`. |
| **Visuals** | `generated_image` / `generated_video` → `config/comfyui/image.json` / `video.json` (your API-format exports with `{{placeholders}}`, see `config/comfyui/README.md`). Negative prompt bans faces, people, text, logos and brand names. `text_card` / `brand_asset` → PIL cards in your colours and logo. `own_footage` → files in `sites/<site>/footage/`. |
| **GPU** | Media Forge unloads first; one ComfyUI job at a time under the file lock; VRAM logged per job in `data/logs/gpu.jsonl`; ComfyUI `/free` after the batch. 3 retries with backoff, every retry logged. |
| **VO** | One ElevenLabs call per VO line, shared across ratios; a shot stretches to fit its line. No key → silent ad, noted. No music (nothing reused). |
| **Assembly** | FFmpeg: cover-crop, slow push-in on stills, captions in the safe zone (above the Reels/TikTok UI), H.264/AAC 30 fps, faststart, MP4 metadata `comment` = AI-generated. |
| **Output** | `data/batches/<batch>/<brief>/<brief>_9x16.mp4` … + `manifest.json`: brief, pattern, the source ads it learned from, facts used, AI flag + disclosure, fallbacks, cost. |
| **After** | Each file is re-checked (adds visual similarity vs the source ads) and lands in the review queue as `pending`. |

```bash
adlab produce --dry-run
adlab produce --site ev1charger --limit 3
adlab produce --brief br_1234abcd5678 --ratios 9:16
```

## Phase 6: Compliance Officer ✅

**TL;DR**
- Every brief and every rendered file gets **PASS / FIX / REJECT**, with the exact reason for each problem.
- Catches inflated claims even when they cite a real fact key, copied phrasing, fake social proof, and other brands.
- Platform rules live in `config/policy.yaml` (editable starter rules + the manual checklist shown in review).

| Check | REJECT when | FIX when |
|---|---|---|
| **Fact trace** | claim cites an unusable key · claim adds a number or superlative its fact doesn't have · any number/$/% anywhere in the copy that no cited fact contains | claim wording drifts from its fact |
| **Similarity** | a 6-word run from a scouted ad (copy, transcript, on-screen text) · ≥ 25% shared 4-word phrases · a frame within dHash distance 8 of a source ad's media | |
| **Banned content** | testimonials, reviews, ratings, "customers love", "as seen on" · guarantees/warranty not in a cited fact · a visual showing customers/homeowners/real people · a competitor's or listed brand's name | a brand banned word |
| **Platform policy** | rules marked REJECT | personal attributes (Meta), sensational, absolute claims, fake urgency |

## Phase 7: Review, export, Reporter, digest, dashboard ✅

**TL;DR**
- Nothing uploads or spends. You approve, edit (with notes) or kill each ad; `adlab export` writes upload-ready files and a sheet per platform; you upload.
- Your results (Meta API or Meta/TikTok CSV exports) re-rank patterns, so new briefs lean on what converts **for you**.
- Monday digest by email: top 5 new hooks, top 3 ads to approve, what's converting. Dashboard home = those 3 things.

| | |
|---|---|
| **Review** | `adlab review` (queue, best bet first) · `adlab review approve <id>` · `edit <id> "notes"` · `kill <id>`. A compliance REJECT can't be approved. |
| **Export** | `data/exports/<date>/<platform>/adlab-<id>-<ratio>.mp4` + `upload_sheet.csv` (ad name with `[adlab:<id>]` tag, placements, primary text, headline, CTA, URL, AI label ON). Checked against `config/platform_specs.yaml` (starter values: confirm them). |
| **Reporter** | `adlab report --import meta.csv` (or `--platform tiktok`), or `--pull-meta` (Insights API, `ads_read`). Matched by the `[adlab:…]` tag. CTR, CPC, CPL, conversions by pattern, hook type and ad. A pattern with ≥ 1,000 of your impressions gets `own_performance_score` (50 = your average; CTR and CPL vs your account), which ranks first for the Strategist. |
| **Digest** | `adlab digest` previews; `--send` emails via Resend. `adlab schedule` sends it Mondays 07:13 Alberta time. |
| **Dashboard** | `adlab serve` (FastAPI, 127.0.0.1:8430) + `dashboard/` (Next.js, Tailwind, shadcn-style UI). Home: 3 cards. Review: video, compliance reasons, the ad, the teardowns it learned from, the pre-upload checklist, and Approve / Edit / Kill (keys **A / E / K**, **J** next). Results: tables + Export. Set `ADLAB_API_TOKEN` before exposing it beyond localhost. |
| **Supabase** | `adlab sync` now mirrors teardowns, patterns, briefs, creatives, compliance reports, review decisions and results (`supabase/migrations/20261014010000_adlab_pipeline.sql`). |

```bash
cp deploy/adlab-api.service ~/.config/systemd/user/ && systemctl --user enable --now adlab-api
cd dashboard && npm install && npm run build && npm start      # http://localhost:3000
```

### Tests
`.venv/bin/pytest -q` runs 83 tests (2 more with the Studio CLI installed): fake Claude, fake
ComfyUI/ElevenLabs/Meta/Resend over `httpx.MockTransport`, and real FFmpeg renders.
`cd dashboard && npm run build` typechecks the dashboard.

## What I need from you (Phases 3–7)
1. **ComfyUI:** URL + `image.json` / `video.json` API-format workflows (see `config/comfyui/README.md`). Until then: text-card placeholders.
2. **Facts:** verified facts per site. No facts → no briefs (by design). Watch for "missing facts" in `adlab brief`.
3. **Keys:** `ANTHROPIC_API_KEY` (analyze/brief), `ELEVENLABS_API_KEY` + a voice ID (your own or stock, never a real person's voice), `RESEND_API_KEY` + a verified sender domain + `DIGEST_TO`. Optional: `META_ADS_TOKEN` + `META_AD_ACCOUNT_ID` (`ads_read`).
4. **Prices:** ElevenLabs $/1k characters for your plan in `config/pricing.yaml → services` (left blank, not guessed).
5. **Check the starter rules:** `config/policy.yaml` and `config/platform_specs.yaml` against Meta's and TikTok's current pages.
6. **Brand:** logo file + colours in each `brand.yaml` (used on cards and end cards); optional `voice_id`.
