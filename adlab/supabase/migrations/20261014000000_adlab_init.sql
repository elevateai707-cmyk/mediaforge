-- AdLab: initial schema (Phase 1).
--
-- Each table keeps the validated Pydantic object in `payload` (jsonb) plus the columns
-- the pipeline filters and joins on. The Python schemas in adlab/schemas.py are the
-- source of truth: rows are written only from validated models.
--
-- Security: RLS is ON with no policies, so the anon/public key can read nothing.
-- AdLab uses the service-role key server-side only. Media files are NOT stored here;
-- they stay in AdLab's private local data/ folder (analysis only).
--
-- Apply: supabase db push   (or paste into the Supabase SQL editor)

create table if not exists ads (
  id             text primary key,                 -- sha256 of media, else of source URL
  site           text not null,
  platform       text not null check (platform in ('meta', 'tiktok', 'other')),
  captured_via   text not null check (captured_via in (
                   'manual_drop', 'manual_link', 'meta_ad_library_api',
                   'tiktok_creative_center_export', 'tiktok_commercial_content_api',
                   'own_account_api')),
  advertiser     text not null,
  source_url     text,
  first_seen     date not null,
  last_seen      date not null,
  variant_group  text,
  score_total    numeric(5, 2) check (score_total between 0 and 100),
  payload        jsonb not null,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  check (last_seen >= first_seen)
);
create index if not exists ads_site_score_idx on ads (site, score_total desc);
create index if not exists ads_variant_group_idx on ads (variant_group);

create table if not exists teardowns (
  id           bigint generated always as identity primary key,
  ad_id        text not null references ads (id) on delete cascade,
  hook_type    text not null check (hook_type in (
                 'question', 'pattern_interrupt', 'bold_claim', 'pov', 'problem_callout', 'demo')),
  format       text not null check (format in (
                 'ugc', 'talking_head', 'demo', 'slideshow', 'text_on_screen', 'before_after')),
  angles       text[] not null,
  model        text not null,
  payload      jsonb not null,
  created_at   timestamptz not null default now()
);
create index if not exists teardowns_ad_idx on teardowns (ad_id);
create index if not exists teardowns_hook_idx on teardowns (hook_type);

create table if not exists patterns (
  id                     text primary key,
  name                   text not null,
  steps                  text[] not null,
  own_performance_score  numeric,             -- set by the Reporter from MY results
  payload                jsonb not null,
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now()
);

create table if not exists briefs (
  id            text primary key,
  site          text not null,
  pattern_id    text not null references patterns (id),
  status        text not null default 'draft' check (status in ('draft', 'rejected', 'produced')),
  fact_keys     text[] not null default '{}',  -- facts its claims cite
  missing_facts text[] not null default '{}',  -- facts it wanted but facts.yaml lacks
  payload       jsonb not null,
  created_at    timestamptz not null default now()
);
create index if not exists briefs_site_idx on briefs (site, created_at desc);

create table if not exists creatives (
  id            text primary key,
  brief_id      text not null references briefs (id) on delete cascade,
  aspect_ratio  text not null check (aspect_ratio in ('9:16', '1:1', '4:5', '16:9')),
  kind          text not null check (kind in ('video', 'image')),
  path          text not null,                 -- local path on the AdLab machine
  sha256        char(64) not null,
  ai_generated  boolean not null default true check (ai_generated),   -- disclosure is mandatory
  review_status text not null default 'pending'
                check (review_status in ('pending', 'approved', 'edit', 'killed')),
  review_notes  text not null default '',
  cost_usd      numeric(10, 4) not null default 0,
  payload       jsonb not null,
  created_at    timestamptz not null default now()
);
create index if not exists creatives_review_idx on creatives (review_status, created_at desc);

create table if not exists compliance_reports (
  id           bigint generated always as identity primary key,
  creative_id  text not null references creatives (id) on delete cascade,
  verdict      text not null check (verdict in ('PASS', 'FIX', 'REJECT')),
  payload      jsonb not null,
  created_at   timestamptz not null default now()
);
create index if not exists compliance_creative_idx on compliance_reports (creative_id, created_at desc);

-- MY ad-account results: the only real conversion data the system learns from.
create table if not exists results (
  id            bigint generated always as identity primary key,
  creative_id   text not null references creatives (id) on delete cascade,
  platform      text not null check (platform in ('meta', 'tiktok')),
  campaign_id   text not null,
  day           date not null,
  impressions   integer not null check (impressions >= 0),
  clicks        integer not null check (clicks >= 0),
  spend_cad     numeric(12, 2) not null check (spend_cad >= 0),
  leads         integer not null default 0 check (leads >= 0),
  conversions   integer not null default 0 check (conversions >= 0),
  payload       jsonb not null,
  unique (creative_id, platform, campaign_id, day)
);

-- Token/cost ledger per command (mirrors data/logs/usage.jsonl).
create table if not exists llm_usage (
  id                  bigint generated always as identity primary key,
  command             text not null,
  provider            text not null,
  model               text not null,
  input_tokens        integer not null default 0,
  output_tokens       integer not null default 0,
  cache_read_tokens   integer not null default 0,
  cache_write_tokens  integer not null default 0,
  cost_usd            numeric(10, 6) not null default 0,
  created_at          timestamptz not null default now()
);

alter table ads                enable row level security;
alter table teardowns          enable row level security;
alter table patterns           enable row level security;
alter table briefs             enable row level security;
alter table creatives          enable row level security;
alter table compliance_reports enable row level security;
alter table results            enable row level security;
alter table llm_usage          enable row level security;
