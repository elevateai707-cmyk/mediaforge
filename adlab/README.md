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

Phase 2 (Scout: local ingestion first) starts when you say go.
