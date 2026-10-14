# Data sources: what AdLab may use, and what each actually returns

Checked 2026-10-14 against the platforms' own documentation. Re-check before relying on
any API: these change. **Rule for every source: official APIs and the platforms' own
tools only. No scraping, no headless browsers, no yt-dlp, no downloading media from the
Ad Library or Creative Center, no fake accounts, no rate-limit workarounds.**

## TL;DR
- **Your Alberta niches = manual drops.** Neither Meta nor TikTok offers an API for
  regular commercial ads shown in Canada.
- **Meta's Ad Library API returns commercial ads only if they reached the EU.** That's
  useful for learning patterns from EU installers and web designers, not for local
  competitors.
- **TikTok:** the Commercial Content API is EU-only and needs an application. Creative
  Center has no official API or export, so it's manual drops only.

## 1. Local ingestion (primary, day one) ✅
Files you save yourself go in `data/incoming/<site>/`: screen recordings, screenshots,
MP4/MOV/WebM, and CSV/JSON you exported from a platform's own tool. Alternatively,
`adlab ingest <url> [file]` records an Ad Library or Creative Center link you pasted.
**AdLab never fetches the URL**: it's stored as the source reference only.

- How you capture the media is up to you and the platform's terms. Screen-recording an ad
  you can see for private analysis is the intended flow. AdLab keeps media private
  (`data/media/`, owner-only permissions) and uses it for analysis only. Phase 5 never
  reuses it.
- Sidecar `<file>.yaml` carries what you know: advertiser, platform, running since,
  variants, rank, notes.

## 2. Meta Ad Library API (enhancement, opt-in) ⚠️ limited
**Source:** Graph API reference *Ads Archive* (v26.0) and *ArchivedAd*.

- **Endpoint:** `GET https://graph.facebook.com/v26.0/ads_archive` with an access token.
- **Coverage, quoted from Meta:** *"Ads that did not reach any location in the EU will only
  return if they are about social issues, elections or politics."*
  - Canada-only commercial ads (your competitors) → **not returned**.
  - Ads that reached the EU → returned for all topics (`ad_type=ALL`, the default). So it
    works for pattern research on, for example, Irish EV-charger installers or web agencies
    (`ad_reached_countries=["IE"]`, English-language).
- **Parameters used:** `ad_type`, `ad_reached_countries`, `search_terms` (≤ 100 chars),
  `search_type`, `search_page_ids` (≤ 10), `ad_active_status`, `ad_delivery_date_min`,
  `media_type`, `languages`, `fields`, `limit`, paging `next`.
- **Fields stored:** `id`, `page_id`, `page_name`, `ad_creative_bodies`,
  `ad_creative_link_titles`, `ad_creative_link_captions`, `ad_creative_link_descriptions`,
  `ad_delivery_start_time`, `ad_delivery_stop_time`, `languages`, `publisher_platforms`.
  Spend and impressions are political-only, so they aren't available here.
- **Not stored:** `ad_snapshot_url`. It embeds your access token, and media behind it is not
  downloaded (hard rule). AdLab stores the public permalink
  `https://www.facebook.com/ads/library/?id=<id>` instead, which you can open yourself and
  screen-record into `data/incoming/` if an ad is worth a teardown.
- **Access:** a Meta developer app plus an access token. Meta's Ad Library API page says
  identity/location confirmation is required. (That page returned 403 to my fetch, so
  confirm the current steps when you set it up.) Set `META_AD_LIBRARY_TOKEN` in `.env`.
- **Rate limits:** error `613` means "exceeded the rate limit". AdLab stops that run and
  logs it. No retries-in-a-loop, no rotation.
- **Scheduling:** weekly refresh via APScheduler (`adlab schedule`), only for sites with
  `meta_ad_library.enabled: true` in `scout.yaml`.

## 3. TikTok Creative Center (manual only) 🖐️
TikTok describes Creative Center (Top Ads etc.) as a free public resource. **I found no
official API or export for it.** The CSV/JSON "exports" online come from third-party
scrapers, which AdLab must not use. So watch Top Ads in your browser, screen-record what's
worth studying, and drop it in `data/incoming/<site>/` with a sidecar (`platform: tiktok`,
`platform_rank:` if it was ranked).

## 4. TikTok Commercial Content API (eligibility check only) 📝
From TikTok's developer page: data covers **EU countries only** in the current phase
(applicants can be anywhere). It requires a TikTok for Developers account with an official
email and an application form, with a review in about 2 working days. Ads stay accessible
while running and for one year after.
- **For your Alberta niches:** no Canadian data, so it adds nothing local. As with Meta, it
  could help EU pattern research. Not built; apply only if you want EU TikTok patterns.

## 5. Your own ad accounts (Phase 7) ✅ ground truth
The only real conversion data AdLab learns from. Read-only; AdLab never creates, edits or
pays for anything in your ad accounts.

- **Meta Marketing API, Insights** (`adlab report --pull-meta`):
  `GET https://graph.facebook.com/v25.0/act_<AD_ACCOUNT_ID>/insights` with `level=ad`,
  `time_increment=1`, `time_range={"since","until"}`, `fields=ad_id,ad_name,campaign_id,
  impressions,clicks,spend,actions,date_start,date_stop,account_currency`, paging via
  `paging.next`. Needs a token with **`ads_read`** on your ad account (`META_ADS_TOKEN`,
  `META_AD_ACCOUNT_ID`). Version is set in `config/report.yaml`. Leads = the first of
  `lead`, `onsite_conversion.lead_grouped`, `offsite_conversion.fb_pixel_lead` that's
  present (Meta reports overlapping types, so they're never summed).
- **Meta Ads Manager CSV export** (`adlab report --import file.csv --platform meta`): no
  token needed. Export *by day* and *by ad* with Ad name, Day, Impressions, Link clicks,
  Amount spent, Leads.
- **TikTok Ads Manager CSV export** (`adlab report --import file.csv --platform tiktok`).
  I couldn't verify TikTok's reporting API field names against official docs, so it's not
  wired; the CSV export from your own Ads Manager is the official route here. Column names
  are matched loosely (`config/report.yaml → csv_columns`): add yours if they differ.
- **Matching:** `adlab export` names every ad `[adlab:<creative_id>] …`. Keep that tag in
  the ad name when you upload; rows without it are counted as "unmatched" and ignored.

## 6. Services the Producer and digest call (Phases 5, 7)
| Service | Call | Notes |
|---|---|---|
| **ComfyUI** (yours, local) | `POST /prompt {prompt, client_id}` → `GET /history/<id>` → `GET /view` · `POST /free` | Your API-format workflows in `config/comfyui/`. Unloaded after each batch. |
| **ElevenLabs** | `POST https://api.elevenlabs.io/v1/text-to-speech/<voice_id>?output_format=mp3_44100_128`, header `xi-api-key`, body `{text, model_id}` | Stock voice or your own clone only, never a real person's voice. One call per VO line. Price per 1k chars in `config/pricing.yaml → services`. |
| **Resend** | `POST https://api.resend.com/emails`, `Authorization: Bearer re_…`, body `{from, to, subject, html, text}` | Weekly digest. `DIGEST_FROM` must be on a domain you verified in Resend. |

## "Winning" signals we can legitimately compute
Competitors' conversions aren't public, so the score uses proxies
(`config/scoring.yaml`, editable):

| Signal | Source | Points |
|---|---|---|
| Longevity (30 / 60 / 90+ days running) | `running_since` you note, API start/stop dates | 15 / 25 / 35 |
| Still running | your sidecar, API stop date blank | +5 |
| Variant count (same concept, multiple versions) | grouping by similar visuals/text per advertiser, or `variants:` you note | 5 per extra variant, max 25 |
| Platform rank (e.g. Creative Center Top Ads position) | `platform_rank:` you note | top-10: 20, top-50: 12, top-100: 6 |
| Repeat pattern across advertisers | needs teardowns → **Phase 3** | max 15 |

Longevity counts from the earliest date AdLab knows. If you don't note `running_since`, the
clock starts on the day you first dropped the ad.

Sources: [Meta Ads Archive reference](https://developers.facebook.com/docs/graph-api/reference/ads_archive/),
[Meta ArchivedAd reference](https://developers.facebook.com/docs/graph-api/reference/archived-ad/),
[TikTok Commercial Content API](https://developers.tiktok.com/products/commercial-content-api),
[TikTok: About Creative Center](https://ads.tiktok.com/resources/help/article/creative-center).
