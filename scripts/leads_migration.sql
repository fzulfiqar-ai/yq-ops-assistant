-- LATER — free-first B2B lead pipeline (Three-Tiered Brain · Tier 2). Advise-only: it drafts a
-- call/visit list; a human acts. Idempotent.
--
-- COMPLIANCE (respect these — see app/leadgen.py):
--   • OpenStreetMap/Overpass data is ODbL → attribution required wherever leads are shown.
--   • Google Places (optional) restricts caching/retention → store only minimal business fields.
--   • Bahrain PDPL (Law 30/2018): store BUSINESS contact info for B2B outreach only; no personal PII.

create table if not exists leads (
  id          bigint generated always as identity primary key,
  name        text not null,
  category    text,                       -- shop type: mobile_phone | electronics | department_store ...
  area        text,                       -- block / area / city in Bahrain
  phone       text,
  website     text,
  lat         numeric,
  lon         numeric,
  source      text default 'overpass',    -- overpass | places | manual
  source_ref  text,                       -- OSM element id etc. — dedupe key
  fit_score   int  default 0,             -- 0-100: how well they fit our fast movers / channel
  status      text not null default 'new'
              check (status in ('new','contacted','visited','quoted','ordered','rejected')),
  notes       text,
  assigned_to text,
  created_at  timestamptz default now(),
  updated_at  timestamptz default now(),
  unique (source, source_ref)             -- OSM re-imports are no-ops
);
create index if not exists leads_status_idx on leads (status);
create index if not exists leads_fit_idx on leads (fit_score desc);
alter table leads enable row level security;  -- service-role only (backend manages it)

-- Outcome log — the feedback loop. Every status change / visit / order feeds conversion metrics
-- AND can be embedded into pgvector so targeting + coaching get smarter over time.
create table if not exists lead_events (
  id       bigint generated always as identity primary key,
  lead_id  bigint references leads(id) on delete cascade,
  event    text not null,                 -- created | status_change | note | outcome
  detail   jsonb default '{}'::jsonb,
  by       text,
  ts       timestamptz default now()
);
create index if not exists lead_events_lead_idx on lead_events (lead_id);
alter table lead_events enable row level security;

-- Pipeline + ROI instrumentation (leads → visits → orders conversion, by stage).
create or replace view v_lead_pipeline as
select
  status,
  count(*)                          as leads,
  round(coalesce(avg(fit_score), 0)::numeric, 0) as avg_fit
from leads
group by status;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'yq_readonly') then
    grant select on v_lead_pipeline to yq_readonly;
  end if;
end $$;
