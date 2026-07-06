-- Marketing & Outreach engine (Sales Growth to BHD 10k/month). Idempotent.
--
-- Tables: outreach_queue (drafted touches awaiting human approval), outreach_touches
-- (actual sends — the attribution anchor), marketing_opt_outs, catalog_visits,
-- social_posts (content engine drafts), wa_sessions/wa_messages (Cloud API, Phase 2).
--
-- COMPLIANCE (Bahrain PDPL, Law 30/2018):
--   • B2B outreach to BUSINESS contacts only, legitimately obtained (Focus history,
--     public listings, salesman knowledge). Source + timestamp live in customer_contacts.
--   • Every outbound message carries an opt-out; marketing_opt_outs is checked before
--     drafting AND before sending. A human approves every send (no auto-broadcast).

create table if not exists outreach_queue (
  id            bigint generated always as identity primary key,
  target_type   text not null default 'customer' check (target_type in ('customer','lead')),
  target_name   text not null,                -- canonical customer_name, or lead name
  lead_id       bigint,                       -- references leads(id) when target_type='lead'
  source_agent  text not null,                -- sales_outreach | winback | sales_push | lead_gen | manual
  channel       text not null default 'whatsapp' check (channel in ('whatsapp','email')),
  phone         text,
  email         text,
  wa_link       text,                         -- one-tap wa.me deep link (assist mode)
  subject       text,                         -- email subject line
  message_en    text,
  message_ar    text,
  reason        text,                         -- why now: "42 days past cycle", "lapsed high-value"…
  impact_bhd    numeric,                      -- lifetime / at-stake BHD (ranking)
  status        text not null default 'draft'
                check (status in ('draft','approved','sent','replied','converted','dismissed')),
  fingerprint   text not null,                -- target|source_agent — 14-day dedupe key (checked in code)
  created_at    timestamptz default now(),
  updated_at    timestamptz default now(),
  sent_at       timestamptz,
  sent_by       text
);
create index if not exists outreach_queue_status_idx on outreach_queue (status);
create index if not exists outreach_queue_fp_idx on outreach_queue (fingerprint, created_at desc);
alter table outreach_queue enable row level security;  -- service-role only

-- Every REAL send (email dispatched / wa.me tapped). Attribution anchors here, not on drafts.
create table if not exists outreach_touches (
  id           bigint generated always as identity primary key,
  queue_id     bigint references outreach_queue(id) on delete set null,
  target_name  text not null,
  channel      text not null,
  message      text,
  sent_by      text,
  sent_at      timestamptz default now()
);
create index if not exists outreach_touches_name_idx on outreach_touches (target_name);
alter table outreach_touches enable row level security;

create table if not exists marketing_opt_outs (
  id           bigint generated always as identity primary key,
  target_name  text,
  phone        text,
  email        text,
  channel      text not null default 'all' check (channel in ('all','whatsapp','email')),
  reason       text,
  created_at   timestamptz default now()
);
create index if not exists marketing_opt_outs_name_idx on marketing_opt_outs (target_name);
alter table marketing_opt_outs enable row level security;

-- Public catalog visit log (?src=outreach-{id} ties a visit back to a touch).
create table if not exists catalog_visits (
  id     bigint generated always as identity primary key,
  src    text,                                -- outreach-{id} | campaign-{tag} | direct
  ua     text,
  ts     timestamptz default now()
);
create index if not exists catalog_visits_src_idx on catalog_visits (src);
alter table catalog_visits enable row level security;

-- Content engine drafts (image ads + 9:16 videos rendered from catalog photos).
create table if not exists social_posts (
  id          bigint generated always as identity primary key,
  campaign    text,                           -- clearance | bundle | hero | new_arrival | manual
  item_code   text,
  kind        text not null check (kind in ('image','video')),
  template    text,                           -- hero_card | price_drop | bundle | new_arrival
  caption_en  text,
  caption_ar  text,
  media_url   text not null,                  -- public Supabase storage URL (marketing bucket)
  platforms   jsonb default '[]'::jsonb,      -- ["instagram","facebook","tiktok"]
  status      text not null default 'draft'
              check (status in ('draft','approved','posted','failed','dismissed')),
  meta        jsonb default '{}'::jsonb,      -- render params, post ids, errors
  created_at  timestamptz default now(),
  posted_at   timestamptz
);
create index if not exists social_posts_status_idx on social_posts (status);
alter table social_posts enable row level security;

-- WhatsApp Cloud API sessions (Phase 2 — Mode B). One row per WhatsApp user (wa_id).
create table if not exists wa_sessions (
  wa_id             text primary key,          -- digits, e.g. 97333112233
  customer_name     text,                      -- matched via customer_contacts.phone
  profile_name      text,                      -- WhatsApp push name
  opt_in            boolean default false,     -- documented consent for utility templates
  last_inbound_at   timestamptz,               -- start/refresh of the free 24h service window
  last_outbound_at  timestamptz,
  auto_replies_hour int default 0,             -- circuit breaker counter (reset hourly)
  created_at        timestamptz default now(),
  updated_at        timestamptz default now()
);
alter table wa_sessions enable row level security;

create table if not exists wa_messages (
  id         bigint generated always as identity primary key,
  wa_id      text not null,
  direction  text not null check (direction in ('in','out')),
  body       text,
  msg_type   text default 'text',
  meta       jsonb default '{}'::jsonb,
  ts         timestamptz default now()
);
create index if not exists wa_messages_waid_idx on wa_messages (wa_id, ts desc);
alter table wa_messages enable row level security;

-- ── Attribution + KPI views ──────────────────────────────────────────────────

-- Touch → revenue in the following 14 days (first order date + BHD). Conservative:
-- any order inside the window counts for that touch; overlapping touches both see it.
create or replace view v_outreach_attribution as
select
  t.id                    as touch_id,
  t.target_name,
  t.channel,
  t.sent_at::date         as touch_date,
  (select min(s.sale_date) from v_sales s
    where s.customer_name = t.target_name
      and s.sale_date >  t.sent_at::date
      and s.sale_date <= t.sent_at::date + 14)                       as first_order,
  coalesce((select sum(s.revenue_bhd) from v_sales s
    where s.customer_name = t.target_name
      and s.sale_date >  t.sent_at::date
      and s.sale_date <= t.sent_at::date + 14), 0)                   as attributed_bhd
from outreach_touches t;

-- Weekly marketing funnel: touches → converted targets → attributed BHD.
create or replace view v_marketing_kpis as
select
  date_trunc('week', a.touch_date)::date                             as week,
  count(*)                                                           as touches,
  count(distinct a.target_name)                                      as targets,
  count(*) filter (where a.channel = 'whatsapp')                     as wa_touches,
  count(*) filter (where a.channel = 'email')                        as email_touches,
  count(distinct a.target_name) filter (where a.first_order is not null) as converted_targets,
  round(sum(a.attributed_bhd)::numeric, 0)                           as attributed_bhd
from v_outreach_attribution a
group by 1;

-- Month pace vs the BHD 10,000 target (drives the Results tab headline).
create or replace view v_month_pace as
select
  to_char(date_trunc('month', current_date), 'YYYY-MM')              as month,
  round(coalesce(sum(s.revenue_bhd), 0)::numeric, 0)                 as revenue_bhd,
  extract(day from current_date)::int                                as day_of_month,
  round((coalesce(sum(s.revenue_bhd), 0)
         / greatest(extract(day from current_date), 1)
         * extract(day from (date_trunc('month', current_date) + interval '1 month - 1 day')))::numeric, 0)
                                                                     as projected_bhd
from v_sales s
where s.sale_date >= date_trunc('month', current_date);

-- Read-only role (agents query views through the SQL RPC). Plain statements — a GRANT
-- inside a DO block has bitten this project before (see docs/CLAUDE.md gotcha).
grant select on v_outreach_attribution to yq_readonly;
grant select on v_marketing_kpis to yq_readonly;
grant select on v_month_pace to yq_readonly;
