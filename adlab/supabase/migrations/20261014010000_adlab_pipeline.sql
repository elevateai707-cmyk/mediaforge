-- AdLab Phases 3-7: let `adlab sync` mirror teardowns, patterns, briefs, creatives,
-- compliance reports, review decisions and results (upserts keyed on natural ids).
-- Same rules as the init migration: payload = the validated Pydantic object, RLS on,
-- no policies (service-role key only), no media in the database.

-- statuses the pipeline now uses
alter table briefs drop constraint if exists briefs_status_check;
alter table briefs add constraint briefs_status_check
  check (status in ('draft', 'rejected', 'needs_fix', 'produced', 'revised'));
alter table creatives drop constraint if exists creatives_review_status_check;
alter table creatives add constraint creatives_review_status_check
  check (review_status in ('pending', 'approved', 'edit', 'killed', 'rejected', 'revised'));

-- one teardown per ad (re-analysis replaces it)
alter table teardowns add constraint teardowns_ad_id_key unique (ad_id);

-- compliance reports get AdLab's id so re-checks upsert instead of piling up
alter table compliance_reports add column if not exists report_id text;
create unique index if not exists compliance_reports_report_id_key on compliance_reports (report_id);

-- your approve / edit / kill decisions (the human gate)
create table if not exists reviews (
  creative_id  text primary key references creatives (id) on delete cascade,
  decision     text not null check (decision in ('approve', 'edit', 'kill')),
  notes        text not null default '',
  decided_at   timestamptz not null,
  payload      jsonb not null
);

alter table reviews enable row level security;

-- cost ledger mirror: one row per data/logs/usage.jsonl line (re-syncs are idempotent);
-- unpriced calls stay NULL rather than pretending they cost $0
alter table llm_usage add column if not exists entry_key text;
alter table llm_usage add column if not exists units numeric;
create unique index if not exists llm_usage_entry_key on llm_usage (entry_key);
alter table llm_usage alter column cost_usd drop not null;
alter table llm_usage alter column cost_usd drop default;
