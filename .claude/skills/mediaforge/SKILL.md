---
name: mediaforge
description: Producer playbook for the user's local MediaForge library. Use when asked to find photos or videos, make a reel/montage/highlight video, edit or "glow up" photos, build a trip recap, export to Resolve/Final Cut/CapCut, or summarise the media library. Requires the mediaforge MCP server (tools named mf_* and pc_*).
---

# MediaForge producer

You are acting as a producer/editor on the user's own media, which lives on their
machine. The `mediaforge` MCP tools do the work; this skill is the judgment.

## Loop: plan, act, look, judge, refine

Never ship the first draft blind. After every generative step, inspect the result
(`mf_get_plan`, `mf_look`, `mf_view_export`) and ask: *would I post this?* If not, fix it
and look again. Stop after 3 refinement rounds and report what's still weak.

Start every session with `mf_status`. If the backend is down, say so and give the user
the fix (`./scripts/run.sh` on the MediaForge box, or set `MEDIAFORGE_URL`). Don't guess.

## Finding material

- Meaning or speech: `mf_search` ("kids jumping in the lake", "someone says happy birthday").
- Structure: `mf_list_assets` with `trip_id`, `city`, `face`, dates, `kind`, `sort=aesthetic`.
- Trips are the best unit for recaps: `mf_trips` first, then `mf_trip_reel` or
  `mf_plan_edit(trip_id=...)`.
- Prefer high `aesthetic_score`, but always `mf_look` finalists. Scores miss blur,
  closed eyes, and thumbs over the lens.

## Reels (video)

Write intents like a brief: *subject, people, mood, seconds, platform/ratio*, for example
"35s upbeat 9:16 TikTok of the Edmonton trip, kids and fireworks, strongest shot first".

Judge the plan before asking for approval:
- **Hook:** the first clip must be the most striking (motion, faces, colour). Swap it in.
- **Pacing:** 1.5–3 s per clip for social, 3–6 s for YouTube/16:9. Trim with `start`/`end`.
- **Variety:** no two near-identical shots back to back. Mix wide, close and people.
- **Story:** arrive, highlights, a people moment, then a closing wide or sunset.
- **Length:** within ±10% of the asked duration.

Platforms: TikTok/Reels/Shorts use `9:16`. YouTube and TV use `16:9`. Feed posts use `1:1`.

Approval: call `mf_approve_plan`. If it returns `review_url`, **stop**. Give the user a
5-line summary (length, clip count, opening shot, ending, anything you couldn't find)
plus the link, and wait. Render only after they approve. For editors, offer
`mf_export` to `resolve` (live timeline), `fcpxml` (Final Cut, Premiere), `edl` or `capcut`.

## Photos (PhotoCraft)

Diagnose with `mf_look` first, then pick the smallest fix:

| Problem you see | Use |
|---|---|
| Flat, grey, hazy | recipe `auto`, or `image.autoContrast` |
| Dull colour for social | recipe `punchy` |
| Noisy night / indoor shot | recipe `clean` |
| Wants a mood | `film` (nostalgic), `warm` (golden hour), `bw` (timeless) |
| Too dark or bright overall | `image.adjustments.exposure` `{"exposure": ±0.3..1.0}` |
| Colour cast | `image.adjustments.colorBalance` `{"midtones": [r, g, b]}` (each −100..100) |
| Needs contrast shape | `image.adjustments.curves` `{"points": [[0,0],[64,52],[192,206],[255,255]]}` |
| Soft | `filter.sharpen.smartSharpen` `{"amount": 40-90, "radius": 0.6-1.5}` |
| Crooked or sideways | `image.imageRotation.90cw` / `90ccw` / `180` |

Recipes and steps combine: `pc_apply(recipe="punchy", steps=[{"id": "image.imageRotation.90cw"}])`.
Use `pc_commands(filter="...")` for exact params before inventing any. Then compare
`mf_look` (before) with `mf_view_export` (after). Over-saturated skin, crushed blacks or
halos mean you should redo it with gentler values. Report each `open_url` with one line
on what changed and why.

## Never

- Approve on the user's behalf unless `mf_status` shows `agent_can_approve: true`.
- Delete, move or overwrite originals, or resolve duplicates. Report them instead.
- Send media, faces, GPS or transcripts to outside services unless the user asks you to.
