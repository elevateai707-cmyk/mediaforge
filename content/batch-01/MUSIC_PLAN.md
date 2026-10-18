# Batch 01: music plan

Tracks: the user's Suno tracks (paid plan, commercially cleared), in `/srv/iris/media/music/suno/`.
Beat maps (tempo, beats, bar downbeats, drop, best 15 s/30 s section) are in `.beats/*.json` next to the tracks.

## Beat-sync rules
- **B-roll (music-led):**
  - Every cut lands on a bar downbeat: 1 bar at 120+ bpm for energetic tracks, 2 bars for cinematic ones.
  - The text line appears on a downbeat.
  - Video length is a whole number of bars, starting at the track's strongest section (`best_start_15s`) and ending on a bar with a 0.5 s fade.
  - If the drop falls inside the window, the best drone shot (a reveal or the highest aesthetic score) starts exactly on the drop.
- **Narrated (voice-led):**
  - The voice sets the beats.
  - Cut points snap to the nearest music beat within ±150 ms, trimming neighbouring clips so the total length and voice sync don't change.
  - The music bed starts on a downbeat and ducks under the voice.
  - The beat-glitch flash lands on the downbeat nearest each beat boundary.

## Assignments

### AlbertaWeb narrated: ADS (voice + Suno instrumental bed)
| Video | Track | BPM | Why |
|---|---|---|---|
| AW01 Does ChatGPT know you exist? | beyond_the_final_peak | 117.5 | cinematic build under the skyline hook |
| AW02 Own it, don't rent it | saturday_street_slide | 117.5 | upbeat and confident |
| AW03 Your website is your truck | Grief and Glory | 136 | driving, epic, trades energy |
| AW05 $299 audit | 432 Hz Rooftop | 129 | clean and steady for a price message |
| AW06 Three seconds | the_three_second_reset | 89 | the name matches the hook, and the tension suits the speed theme |
| AW08 No mystery PDF | ten_minute_reset | 117.5 | calm and trustworthy |
| AW10 Missed calls at night | Sombra do Crime | 143.6 | dark, for night aerials |
| AW12 Retrofit, don't rebuild | beyond_the_final_peak (2nd section) | 117.5 | build/upgrade feel |
| AW14 Agent-ready | 432 Hz Rooftop (drop section) | 129 | modern and techy |

### AlbertaWeb narrated: ORGANIC (voice only; add a trending sound in the app)
AW04, AW07, AW09, AW11, AW13, AW15. Files are named `*_voice-only.mp4`.

### Build With DB narrated: ADS (voice + Suno instrumental bed)
| Video | Track | BPM | Why |
|---|---|---|---|
| DB03 Ignite Gig | Keep building (short) | 123 | on-brand "build" |
| DB04 Your website at 2am | Sombra do Crime | 143.6 | night mood |
| DB06 Lead capture funnel | Dark Gold Grind | 123 | grind energy |
| DB08 Never miss a call | Buildremix | 123 | punchy, short |
| DB10 CRM follow-up | Dark Gold Grind (2nd section) | 123 | consistent brand sound |
| DB12 Start building | Grief and Glory | 136 | aspirational, epic |
| DB14 This video was made by a system | the_three_second_reset | 89 | tension → reveal |

### Build With DB narrated: ORGANIC (voice only)
DB01, DB02, DB05, DB07, DB09, DB11, DB13, DB15. Files are named `*_voice-only.mp4`.

### B-roll: 8 per brand with Suno songs (cut to the beat), 7 per brand silent
| Brand | With music (#1–8) | Silent (#9–15) |
|---|---|---|
| AlbertaWeb | heat_on_the_north-side ×2 (different sections) · stone_and_steam ×2 · beyond_the_final_peak · saturday_street_slide · Golden hour (10 s) · 432 Hz Rooftop | #9–15, named `*_silent.mp4` |
| Build With DB | Keep Building ×2 · Build it R&B ×2 · Buildremix · Dark Gold Grind · Keep building (short) · Sombra do Crime | #9–15, named `*_silent.mp4` |

## Trending audio for the silent and voice-only posts
Researched 2026-10-17. Trends turn over weekly, so I'll refresh this list on delivery day and put a pick in each post description.

**Important:** if @buildwithdb or albertaweb.design is set up as a **Business** account, Instagram and TikTok only allow the **Commercial Music Library** (CML). Popular licensed tracks (Taylor Swift "Patient Zero", ADÉLA "Nicole Kidman", Ingrid Michaelson "Be OK") are for Creator accounts only and can get muted on Business accounts. Options:
1. Switch the organic-heavy account to **Creator** (you keep analytics; ads still work via boosting), or
2. Pick from the CML "trending" filter in-app. For example, "Autumn Colors" by LuLuMusic is trending this week and cleared for business use.

Current picks by vibe (verify in-app before posting):
- **Cinematic drone / reflective:** "Espresso Soul" (Edivieira), smooth and polished. "Vibin" (WXODA), for growth and realisation posts (DB11, DB13, AW15).
- **Confident / main-character:** "Nicole Kidman" (ADÉLA), Creator accounts only (DB01, DB09).
- **Seasonal reach:** "Hello October" (Sugar Lime Blue), "Autumn Colors" (LuLuMusic, CML), for fall drone B-roll.
- Under voice-only posts, set the trending sound to **10–15% volume** so the voice stays clear.

Sources: [SocialPilot TikTok trends](https://www.socialpilot.co/blog/tiktok-trends) · [SocialPilot Reels trends](https://www.socialpilot.co/blog/instagram-reels-trends) · [NewEngen TikTok Oct 2026](https://newengen.com/insights/tiktok-trends/) · [SocialBee Oct 14](https://socialbee.com/blog/trending-instagram-songs-this-week/) · [Buffer Oct 2026](https://buffer.com/resources/trending-audio-instagram/) · [Metricool](https://metricool.com/trending-instagram-songs/)
