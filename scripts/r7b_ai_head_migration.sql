-- Weekly AI Head, release R7b (Sprint 6, plan §19 / §20 / §22). Additive, idempotent.
--   Rehearse: python -m scripts.apply_sql scripts/r7b_ai_head_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/r7b_ai_head_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/r7b_ai_head_reverse.sql
--   Needs:    shop_migration, marketplace_migration (v_customer_regulars), kickback_statements + r3_statements,
--             catalog_velocity_v2, product_finds, field_notes and r7_focus_links_migration.sql
--             (shop_order_focus_links). The first block below refuses with the file to apply if one is missing.
--
-- Why: the owner runs a weekly (and monthly) business review in Claude Code on his own PC
-- (.claude/commands/weekly-review.md -> python -m scripts.ai_head.pack). The pack must read the
-- marketplace, the reps, the statements, the Focus link and Focus sales WITHOUT the service key and
-- without any base table, and never see a phone number or an email address.
--
-- What this adds (nothing here writes, moves or deletes a business row):
--   1. Fourteen narrow read-only views, v_agent_*, owned by postgres (so they run with the owner's
--      rights and need no base-table grant; never security_invoker). No column carries a phone, an
--      email, an order token, an IP hash, a user agent or a raw device / session id; free text that
--      could hold a contact (shop names and areas, Focus names and narration, search terms, finds, field
--      notes) passes ai_agent_mask(): email addresses -> '[email]', runs of 8+ digits -> '[number]'
--      (the digit rule is app/ai_insights.PHONE_PATTERN, which the pack applies again on the way out).
--        v_agent_shop_orders       order header facts: status timestamps, requested vs confirmed totals,
--                                  rep, area, source, is_test, the Focus invoice link state
--        v_agent_shop_lines        order lines: qty vs qty_confirmed vs qty_delivered, line totals, line_status,
--                                  added_at_stage (a rep-added / substitute line, R7c; read through to_jsonb
--                                  so the view works before and after r7c_order_lines_qty_migration.sql)
--        v_agent_shop_events       the merchant funnel stream with md5 device / session keys (lets the pack
--                                  reuse scripts/weekly_report.py compute() unchanged)
--        v_agent_funnel_daily      per Bahrain day x referral_code: visitors, item views, adds, checkouts, orders
--        v_agent_search_demand     per day x term: searches (typing-in-progress removed), zero-result flag
--        v_agent_rep_governance    per rep: orders waiting, confirm times, last order action, link visits, taps
--        v_agent_statements        kickback statements without who-approved / notes / target_snapshot emails
--        v_agent_focus_links       order <-> invoice decisions: invoice_key, method, state, confidence
--        v_agent_customer_regulars Focus cadence per shop x SKU with the owning rep and a strict due flag
--        v_agent_focus_sales       Focus sales lines (v_sales) with the narration masked
--        v_agent_items             catalog item + stock + velocity + latest landed cost + restock asks
--        v_agent_market_signals    restock requests (counted, never the phone), product finds, field notes
--        v_agent_data_trust        as-on date / last load per source + the last upload's join match rate
--        v_agent_insights          what the AI Head proposed and what the owner decided (ai_insights)
--   2. ai_insights: one row per statement the owner approved for loading (scripts/ai_head/load_insights.py
--      --commit), read by GET /management/insights (admin + management). RLS on, service role only.
--      A trigger keeps the words immutable: only status / decided_by / decided_at / updated_at change.
--   3. LOGIN role ai_head_ro WITHOUT a password (it cannot log in until the owner sets one):
--        ALTER ROLE ai_head_ro PASSWORD '<a long random secret>';      -- Supabase SQL editor, once
--      statement_timeout 15 s, default_transaction_read_only on, idle-in-transaction 60 s, 3 connections.
--      It holds SELECT on the v_agent_* views and USAGE on schema public -- nothing else.
--      Connect through the session pooler with the user name  ai_head_ro.<project-ref>  (the same host
--      and port as DATABASE_URL) and put that URI in .env as AI_HEAD_DATABASE_URL (see
--      scripts/ai_head/README.md). Rotate: ALTER ROLE ai_head_ro PASSWORD '<new>'. Lock out at once:
--      ALTER ROLE ai_head_ro NOLOGIN.
--
-- DELIBERATE DEVIATION from the plan's wording ("a member of yq_readonly"), checked on production
-- 27-Sep-2026 (read-only): yq_readonly holds SELECT on customer_contacts (phone, email) and leads
-- (phone, email, address), and it OWNS the two SECURITY DEFINER RPCs run_readonly_query(text) and
-- run_readonly_query_params(text, jsonb). A member inherits the owner's rights: with the password it
-- could DROP those functions (the portal's reports and the assistant stop) or GRANT EXECUTE on them to
-- anon (every yq_readonly table, phones included, readable with the publishable key -- and that grant
-- would survive a password rotation). So ai_head_ro is a member of NO role and gets its own grants.
-- The views are ALSO granted to yq_readonly (the assistant's read path) except
-- v_agent_customer_regulars, which keeps the standing rule for v_customer_regulars (shop names with
-- cadence: never yq_readonly).
--
-- Every role statement is a plain top-level statement except CREATE ROLE (wrapped in DO for
-- idempotency, the pattern security_migration.sql proved on production); a GRANT <role> TO <role> is
-- never used (supautils crashes the backend on one inside DO, and none is needed).
-- A later change to a view may only APPEND columns (CREATE OR REPLACE VIEW cannot drop or reorder).

-- ── 0. dependencies ──────────────────────────────────────────────────────────────
do $$
declare
  need text[][] := array[
    array['shop_orders', 'shop_migration.sql'],
    array['shop_order_lines', 'shop_migration.sql'],
    array['shop_order_events', 'shop_migration.sql'],
    array['shop_events', 'shop_migration.sql'],
    array['salesmen', 'shop_migration.sql'],
    array['catalog_items', 'catalog_migration.sql'],
    array['v_sales', 'division_payment_migration.sql'],
    array['v_customer_regulars', 'marketplace_migration.sql'],
    array['v_catalog_stock', 'shop_migration.sql'],
    array['v_catalog_velocity', 'catalog_velocity_v2_migration.sql'],
    array['v_catalog_cost', 'shop_migration.sql'],
    array['salesman_kickback_statements', 'kickback_statements_migration.sql'],
    array['shop_order_focus_links', 'r7_focus_links_migration.sql'],
    array['shop_restock_requests', 'marketplace_campaigns_migration.sql'],
    array['product_finds', 'product_finds_migration.sql'],
    array['field_notes', 'field_notes_migration.sql'],
    array['stock_balance', 'stock_migration.sql'],
    array['ar_ageing_totals', 'economics_v2_migration.sql'],
    array['mrn_landed_costs', 'mrn_costs_migration.sql'],
    array['ingest_runs', 'schema.sql'],
    array['audit_log', 'schema.sql']];
  i int;
begin
  for i in 1 .. array_length(need, 1) loop
    if to_regclass('public.' || need[i][1]) is null then
      raise exception 'r7b_ai_head: % is missing -- apply scripts/% first', need[i][1], need[i][2];
    end if;
  end loop;
  if not exists (select 1 from pg_roles where rolname = 'yq_readonly') then
    raise exception 'r7b_ai_head: role yq_readonly is missing -- apply scripts/security_migration.sql first';
  end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    raise exception 'r7b_ai_head: role service_role is missing -- this file is written for the Supabase database';
  end if;
  if not exists (select 1 from information_schema.columns where table_schema = 'public'
                 and table_name = 'salesman_kickback_statements' and column_name = 'superseded_by_id') then
    raise exception 'r7b_ai_head: salesman_kickback_statements has no R3a columns -- apply scripts/r3_statements_migration.sql first';
  end if;
end $$;

-- ── 1. ai_insights (what the owner approved for loading) ─────────────────────────
create table if not exists ai_insights (
  id           bigint generated always as identity primary key,
  kind         text        not null default 'weekly',   -- weekly | monthly
  week_ending  date        not null,                    -- the Saturday that ends the week; a monthly review: the month's last day
  section      text        not null,                    -- the report section (Sales, Order health, ... , Data quality, Top actions)
  tag          text        not null,                    -- fact | analysis | recommendation | hypothesis (LOW-CONFIDENCE HYPOTHESIS)
  text         text        not null,                    -- the statement exactly as the owner approved it
  evidence     jsonb,                                   -- pack files / figures it rests on
  confidence   text,                                    -- high | medium | low
  action       text,                                    -- the proposed action (recommendations)
  rank         int,                                     -- 1..5 = position in "Top 5 actions for approval"
  data_as_of   date,                                    -- the freshness stamp: Focus data through this date
  status       text        not null default 'proposed', -- proposed | approved | rejected | done
  decided_by   text,
  decided_at   timestamptz,
  insight_key  text        not null,                    -- sha256 of kind|week_ending|section|tag|text (idempotent loads)
  source_file  text,                                    -- exports/ai_head/<date>/insights.json (local path, informative)
  created_by   text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  constraint ai_insights_insight_key unique (insight_key)
);

alter table ai_insights drop constraint if exists ai_insights_kind_check;
alter table ai_insights add constraint ai_insights_kind_check check (kind in ('weekly', 'monthly'));
alter table ai_insights drop constraint if exists ai_insights_tag_check;
alter table ai_insights add constraint ai_insights_tag_check
  check (tag in ('fact', 'analysis', 'recommendation', 'hypothesis'));
alter table ai_insights drop constraint if exists ai_insights_confidence_check;
alter table ai_insights add constraint ai_insights_confidence_check
  check (confidence is null or confidence in ('high', 'medium', 'low'));
alter table ai_insights drop constraint if exists ai_insights_status_check;
alter table ai_insights add constraint ai_insights_status_check
  check (status in ('proposed', 'approved', 'rejected', 'done'));
alter table ai_insights drop constraint if exists ai_insights_rank_check;
alter table ai_insights add constraint ai_insights_rank_check check (rank is null or rank between 1 and 5);
alter table ai_insights drop constraint if exists ai_insights_text_check;
alter table ai_insights add constraint ai_insights_text_check
  check (length(btrim(text)) between 1 and 2000 and length(btrim(section)) between 1 and 80);
-- a decision says who and when; an undecided row says neither
alter table ai_insights drop constraint if exists ai_insights_decided_check;
alter table ai_insights add constraint ai_insights_decided_check
  check ((status = 'proposed') = (decided_at is null));

create index if not exists ai_insights_week_idx on ai_insights (kind, week_ending desc, rank);

alter table ai_insights enable row level security;      -- service role only; no policies
revoke all on table ai_insights from anon, authenticated;
revoke all on sequence ai_insights_id_seq from anon, authenticated;

-- the words the owner approved never change after loading: a correction is a new row
create or replace function ai_insights_guard() returns trigger
language plpgsql set search_path = public as $$
begin
  if new.kind is distinct from old.kind or new.week_ending is distinct from old.week_ending
     or new.section is distinct from old.section or new.tag is distinct from old.tag
     or new.text is distinct from old.text or new.evidence is distinct from old.evidence
     or new.confidence is distinct from old.confidence or new.action is distinct from old.action
     or new.rank is distinct from old.rank or new.data_as_of is distinct from old.data_as_of
     or new.insight_key is distinct from old.insight_key or new.created_at is distinct from old.created_at
     or new.created_by is distinct from old.created_by then
    raise exception 'ai_insights: only status, decided_by, decided_at and updated_at may change (load a new row to correct the text)';
  end if;
  if old.status in ('rejected', 'done') and new.status is distinct from old.status then
    raise exception 'ai_insights: a % insight is final', old.status;
  end if;
  if old.status = 'approved' and new.status not in ('approved', 'done') then
    raise exception 'ai_insights: an approved insight can only move to done';
  end if;
  new.updated_at := now();
  return new;
end $$;
revoke all on function ai_insights_guard() from public, anon, authenticated;

drop trigger if exists ai_insights_guard on ai_insights;
create trigger ai_insights_guard before update on ai_insights
  for each row execute function ai_insights_guard();

comment on table ai_insights is
  'Weekly / monthly AI Head statements the owner approved for loading (R7b). Loaded by scripts/ai_head/load_insights.py --commit from exports/ai_head/<date>/insights.json; read by GET /management/insights. Text is immutable (trigger ai_insights_guard); status proposed -> approved | rejected, approved -> done. Service role only.';

-- ── 2. the read-only login (no password: the owner sets it) ──────────────────────
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'ai_head_ro') then
    create role ai_head_ro login inherit connection limit 3;
  end if;
end $$;
alter role ai_head_ro set statement_timeout = '15s';
alter role ai_head_ro set default_transaction_read_only = on;
alter role ai_head_ro set idle_in_transaction_session_timeout = '60s';
grant usage on schema public to ai_head_ro;

-- ── 3. views ─────────────────────────────────────────────────────────────────────
-- The one mask every free-text column goes through: email addresses -> '[email]', then every run of
-- 8+ digits (single spaces allowed between them) -> '[number]'. The digit pattern is
-- app/ai_insights.PHONE_PATTERN verbatim; order numbers (YQ-2609-0019) and invoice keys
-- (SI-YQ-26-09-110) survive it. A view's reader needs EXECUTE on the functions it calls, hence the grants.
create or replace function ai_agent_mask(t text) returns text
language sql immutable parallel safe as $$
  select regexp_replace(
           regexp_replace(t, '[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+', '[email]', 'g'),
           '\+?\d(?: ?\d){7,}', '[number]', 'g')
$$;
revoke all on function ai_agent_mask(text) from public, anon, authenticated;
grant execute on function ai_agent_mask(text) to ai_head_ro, yq_readonly, service_role;
comment on function ai_agent_mask(text) is
  'AI Head (R7b): masks emails (-> [email]) and 8+ digit runs (-> [number]) in the free text the v_agent_* views expose.';

-- 3a. order headers
create or replace view v_agent_shop_orders as
select o.id                                                           as order_id,
       o.order_no,
       o.status,
       o.order_kind,
       coalesce(o.is_test, false)                                     as is_test,
       o.source,
       o.attribution_source,
       o.attribution_conflict,
       o.referral_code,
       o.src,
       o.coupon_code,
       o.salesman_id,
       s.name                                                         as rep,
       s.focus_name                                                   as rep_focus_name,
       o.customer_id,
       ai_agent_mask(o.customer_shop)                               as customer_shop,
       ai_agent_mask(o.customer_area)                               as customer_area,
       o.created_at,
       (o.created_at at time zone 'Asia/Bahrain')::date               as created_day,
       o.assigned_at,
       o.confirmed_at,
       o.packed_at,
       o.out_for_delivery_at,
       o.delivered_at,
       o.cancelled_at,
       o.cancel_reason_code,
       o.paid_at,
       o.updated_at,
       -- Received -> Confirmed; NULL on an order a rep placed himself (source 'salesman': born Confirmed, R7c)
       case when o.source is distinct from 'salesman'
            then round((extract(epoch from (o.confirmed_at - o.created_at)) / 3600)::numeric, 2) end as confirm_hours,
       round((extract(epoch from (o.delivered_at - o.created_at)) / 3600)::numeric, 2) as deliver_hours,
       o.items_count,
       o.units_count,
       o.has_backorder,
       o.subtotal_bhd,
       o.discount_bhd,
       o.delivery_bhd,
       o.small_order_fee_bhd,
       o.minimum_gap_bhd,
       o.total_bhd                                                    as total_requested_bhd,
       o.subtotal_confirmed_bhd,
       o.total_confirmed_bhd,
       o.returned_bhd,
       o.payment_status,
       o.payment_method,
       o.focus_invoice_no,
       coalesce(fl.confirmed_n, 0)                                    as focus_links_confirmed,
       case when coalesce(fl.confirmed_n, 0) > 0 then 'linked'
            when nullif(btrim(o.focus_invoice_no), '') is not null then 'typed'
            else 'none' end                                           as focus_link_state
from shop_orders o
left join salesmen s on s.id = o.salesman_id
left join (select order_id, count(*) as confirmed_n
             from shop_order_focus_links where state = 'confirmed' group by order_id) fl on fl.order_id = o.id;

comment on view v_agent_shop_orders is
  'AI Head (R7b): one row per marketplace order, no contact details (no name, phone, email, note, token, IP, device). total_requested_bhd = as the shop asked; *_confirmed_bhd = after the rep confirmed. confirm_hours is NULL for source ''salesman'' (a rep''s own order is born Confirmed: no Received -> Confirmed wait). focus_link_state: linked (a confirmed shop_order_focus_links row) | typed (focus_invoice_no only) | none. ai_head_ro + yq_readonly.';

-- 3b. order lines
create or replace view v_agent_shop_lines as
select l.id                                   as line_id,
       l.order_id,
       o.order_no,
       o.status                               as order_status,
       coalesce(o.is_test, false)             as is_test,
       o.created_at                           as order_created_at,
       o.salesman_id,
       l.item_code,
       l.display_name,
       ci.category,
       ci.division,
       l.qty,
       l.qty_confirmed,
       l.list_price_bhd,
       l.unit_price_bhd,
       l.unit_price_confirmed,
       l.discount_bhd,
       l.line_total_bhd,
       l.line_total_confirmed,
       l.line_status,
       l.stock_status,
       l.backorder,
       l.rule_ids,
       -- appended (R7b review): R7c's columns through to_jsonb(l) -- NULL before
       -- r7c_order_lines_qty_migration.sql, and r7c's reverse can still drop them (no column is pinned)
       to_jsonb(l) ->> 'added_at_stage'               as added_at_stage,
       (to_jsonb(l) ->> 'qty_delivered')::integer     as qty_delivered
from shop_order_lines l
join shop_orders o on o.id = l.order_id
left join lateral (select c.category, c.division from catalog_items c
                    where upper(c.item_code) = upper(l.item_code)
                    order by (c.item_code = l.item_code) desc, c.id limit 1) ci on true;

comment on view v_agent_shop_lines is
  'AI Head (R7b): marketplace order lines, requested (qty, line_total_bhd), confirmed (qty_confirmed, line_total_confirmed) and delivered (qty_delivered), line_status, the catalog category. added_at_stage set = a line the rep added or substituted (R7c), NOT something the shop asked for: leave it out of demand and of the requested subtotal. ai_head_ro + yq_readonly.';

-- 3c. the funnel stream, pseudonymous
create or replace view v_agent_shop_events as
select e.id                                             as event_id,
       e.ts,
       (e.ts at time zone 'Asia/Bahrain')::date         as day,
       translate(md5('yq-agent:' || e.session_id), '0123456789', 'ghijklmnop') as session_key,
       translate(md5('yq-agent:' || e.device_id), '0123456789', 'ghijklmnop')  as device_key,
       e.event,
       e.item_code,
       e.referral_code,
       e.salesman_id,
       e.src,
       e.meta ->> 'rail'                                as rail,
       case when e.event in ('search', 'search_zero')
            then ai_agent_mask(lower(btrim(e.meta ->> 'q'))) end                                          as search_term,
       case when e.meta ->> 'results' ~ '^\d{1,9}$' then (e.meta ->> 'results')::int end            as search_results,
       case when e.meta ->> 'count' ~ '^\d{1,9}$' then (e.meta ->> 'count')::int end                as item_count
from shop_events e
where e.event in ('view', 'item', 'add', 'remove', 'qty', 'cart', 'checkout_start', 'order',
                  'search', 'search_zero', 'rail_click', 'share');

comment on view v_agent_shop_events is
  'AI Head (R7b): merchant funnel events with md5 device / session keys spelled in letters a-p (never the raw ids, IP hash or user agent; letters so no key can look like a phone number); vitals and error telemetry left out; search terms with 8+ digit runs masked. ai_head_ro + yq_readonly.';

-- 3d. funnel per Bahrain day x referral_code (NULL = arrived without a rep link)
create or replace view v_agent_funnel_daily as
select (e.ts at time zone 'Asia/Bahrain')::date                                  as day,
       e.referral_code,
       count(distinct e.device_id) filter (where e.event = 'view')             as visitors,
       count(distinct e.session_id) filter (where e.event = 'view')            as sessions,
       count(*) filter (where e.event = 'item')                                as item_views,
       count(distinct e.device_id) filter (where e.event = 'item')             as item_viewers,
       count(*) filter (where e.event = 'add')                                 as adds,
       count(distinct e.device_id) filter (where e.event = 'add')              as adders,
       count(distinct e.device_id) filter (where e.event = 'checkout_start')   as checkout_starters,
       count(*) filter (where e.event = 'order')                               as orders,
       count(distinct e.device_id) filter (where e.event = 'order')            as ordering_devices,
       count(*) filter (where e.event = 'search')                              as searches,
       count(*) filter (where e.event = 'search_zero')                         as zero_result_searches
from shop_events e
where e.event in ('view', 'item', 'add', 'checkout_start', 'order', 'search', 'search_zero')
group by 1, 2;

comment on view v_agent_funnel_daily is
  'AI Head (R7b): per Bahrain day and referral_code (NULL = no rep link) the distinct devices at each funnel step plus event counts. Distinct counts do not add across days: use v_agent_shop_events for a week. ai_head_ro + yq_readonly.';

-- 3e. search demand; a query followed within 30 s on the same device by a longer one that starts
--     with it ('san' -> 'sandisk') is typing in progress, not a search (weekly_report.py's rule)
create or replace view v_agent_search_demand as
with s as (
  select e.ts, e.device_id, e.event,
         lower(btrim(e.meta ->> 'q'))                                                       as q,
         case when e.meta ->> 'results' ~ '^\d{1,9}$' then (e.meta ->> 'results')::int end  as results,
         lead(lower(btrim(e.meta ->> 'q'))) over w                                          as next_q,
         lead(e.ts) over w                                                                  as next_ts
  from shop_events e
  where e.event in ('search', 'search_zero') and coalesce(btrim(e.meta ->> 'q'), '') <> ''
  window w as (partition by coalesce(e.device_id, e.session_id, e.id::text) order by e.ts, e.id)
),
kept as (
  select * from s
  where not (next_q is not null and next_ts - ts <= interval '30 seconds' and next_q <> q
             and length(next_q) > length(q) and left(next_q, length(q)) = q)
)
select (ts at time zone 'Asia/Bahrain')::date                                        as day,
       ai_agent_mask(q)                                                                as term,
       count(*)                                                                       as searches,
       count(*) filter (where event = 'search_zero' or results = 0)                  as zero_result_searches,
       count(distinct device_id)                                                      as devices,
       max(results)                                                                   as max_results,
       bool_and(event = 'search_zero' or coalesce(results, 1) = 0)                   as zero_result
from kept
group by 1, 2;

comment on view v_agent_search_demand is
  'AI Head (R7b): what merchants searched per Bahrain day, typing-in-progress removed; zero_result = every search of that term that day found nothing (a sourcing lead). ai_head_ro + yq_readonly.';

-- 3f. rep governance (marketplace data only; live windows anchor to now())
create or replace view v_agent_rep_governance as
with o as (
  select * from shop_orders where not coalesce(is_test, false) and salesman_id is not null
),
per as (
  select o.salesman_id,
         count(*) filter (where o.status = 'new')                                                    as orders_waiting,
         min(o.created_at) filter (where o.status = 'new')                                           as oldest_waiting_since,
         count(*) filter (where o.status = 'new' and o.created_at < now() - interval '24 hours')     as waiting_over_24h,
         count(*) filter (where o.status in ('confirmed', 'packed', 'out_for_delivery'))             as orders_in_progress,
         count(*) filter (where o.created_at >= now() - interval '7 days')                           as orders_7d,
         count(*) filter (where o.created_at >= now() - interval '30 days')                          as orders_30d,
         count(*) filter (where o.confirmed_at >= now() - interval '30 days')                        as confirmed_30d,
         count(*) filter (where o.delivered_at >= now() - interval '30 days')                        as delivered_30d,
         count(*) filter (where o.cancelled_at >= now() - interval '30 days')                        as cancelled_30d,
         -- Received -> Confirmed only: a rep's own order (source 'salesman') is born Confirmed and never waited
         percentile_cont(0.5) within group (order by extract(epoch from (o.confirmed_at - o.created_at)) / 3600)
           filter (where o.confirmed_at >= now() - interval '30 days'
                     and o.source is distinct from 'salesman')                                      as median_confirm_hours_30d,
         max(extract(epoch from (o.confirmed_at - o.created_at)) / 3600)
           filter (where o.confirmed_at >= now() - interval '30 days'
                     and o.source is distinct from 'salesman')                                      as max_confirm_hours_30d,
         max(o.created_at)                                                                           as last_order_at
  from o
  group by o.salesman_id
),
act as (    -- the rep's own order actions (confirm, status moves): his last sign of life in the shop data
  select lower(btrim(e.actor))                                           as actor,
         max(e.ts)                                                       as last_action_at,
         count(*) filter (where e.ts >= now() - interval '7 days')       as actions_7d
  from shop_order_events e
  where e.actor like '%@%'
  group by 1
),
link as (
  select e.referral_code,
         count(distinct e.device_id) filter (where e.ts >= now() - interval '7 days')   as link_visitors_7d,
         count(distinct e.device_id) filter (where e.ts >= now() - interval '30 days')  as link_visitors_30d,
         max(e.ts)                                                                       as last_link_visit_at
  from shop_events e
  where e.event = 'view' and e.referral_code is not null
  group by 1
),
taps as (
  select (a.detail ->> 'salesman_id')::bigint                            as salesman_id,
         count(*) filter (where a.ts >= now() - interval '7 days')       as followup_taps_7d,
         count(*) filter (where a.ts >= now() - interval '30 days')      as followup_taps_30d
  from audit_log a
  where a.event = 'followup.tap' and a.detail ->> 'salesman_id' ~ '^\d{1,18}$'
  group by 1
)
select s.id                                                                  as salesman_id,
       s.name                                                                as rep,
       s.focus_name,
       s.referral_code,
       s.is_active,
       (nullif(btrim(s.user_email), '') is not null)                         as has_login,
       coalesce(p.orders_waiting, 0)                                         as orders_waiting,
       coalesce(p.waiting_over_24h, 0)                                       as waiting_over_24h,
       round((extract(epoch from (now() - p.oldest_waiting_since)) / 3600)::numeric, 1) as oldest_waiting_hours,
       coalesce(p.orders_in_progress, 0)                                     as orders_in_progress,
       coalesce(p.orders_7d, 0)                                              as orders_7d,
       coalesce(p.orders_30d, 0)                                             as orders_30d,
       coalesce(p.confirmed_30d, 0)                                          as confirmed_30d,
       coalesce(p.delivered_30d, 0)                                          as delivered_30d,
       coalesce(p.cancelled_30d, 0)                                          as cancelled_30d,
       round(p.median_confirm_hours_30d::numeric, 2)                         as median_confirm_hours_30d,
       round(p.max_confirm_hours_30d::numeric, 2)                            as max_confirm_hours_30d,
       p.last_order_at,
       a.last_action_at                                                      as last_order_action_at,
       coalesce(a.actions_7d, 0)                                             as order_actions_7d,
       coalesce(l.link_visitors_7d, 0)                                       as link_visitors_7d,
       coalesce(l.link_visitors_30d, 0)                                      as link_visitors_30d,
       l.last_link_visit_at,
       coalesce(t.followup_taps_7d, 0)                                       as followup_taps_7d,
       coalesce(t.followup_taps_30d, 0)                                      as followup_taps_30d
from salesmen s
left join per  p on p.salesman_id = s.id
left join act  a on a.actor = lower(btrim(s.user_email))
left join link l on l.referral_code = s.referral_code
left join taps t on t.salesman_id = s.id;

comment on view v_agent_rep_governance is
  'AI Head (R7b): per rep, from shop data only: orders waiting (and over 24 h), confirm times over 30 days (Received -> Confirmed; his own born-Confirmed orders, source ''salesman'', left out), the last time his login moved an order (last_order_action_at: the nearest thing to a last sign-in the shop records), rep-link visitors and follow-up taps. No phone or email (has_login is a flag). ai_head_ro + yq_readonly.';

-- 3g. kickback statements without personal details
create or replace view v_agent_statements as
select k.id                                           as statement_id,
       k.salesman                                     as rep,
       k.salesman_id,
       k.period,
       k.basis,
       k.status,
       k.data_through,
       k.sales_bhd,
       k.returns_bhd,
       k.tier_reached,
       k.rate,
       k.kickback_bhd,
       -- plain numeric, rounded (a numeric(12,3) cast would fail the whole view on one oversized value)
       case when k.target_snapshot ->> 'target_bhd' ~ '^-?\d{1,15}(\.\d+)?$'
            then round((k.target_snapshot ->> 'target_bhd')::numeric, 3) end as target_bhd,
       case when k.target_snapshot ->> 'tier2_bhd' ~ '^-?\d{1,15}(\.\d+)?$'
            then round((k.target_snapshot ->> 'tier2_bhd')::numeric, 3) end  as tier2_bhd,
       case when k.target_snapshot ->> 'tier3_bhd' ~ '^-?\d{1,15}(\.\d+)?$'
            then round((k.target_snapshot ->> 'tier3_bhd')::numeric, 3) end  as tier3_bhd,
       k.target_snapshot ->> 'team'                   as team,
       k.created_at,
       k.approved_at,
       k.paid_at,
       k.superseded_at,
       k.superseded_by_id
from salesman_kickback_statements k;

comment on view v_agent_statements is
  'AI Head (R7b): kickback statement rows (draft / approved / paid / superseded / snapshot) with the target and tiers they used; no created_by / approved_by / paid_by / superseded_by logins, no note, no raw target_snapshot (it names the editor). ai_head_ro + yq_readonly.';

-- 3h. order <-> Focus invoice decisions
create or replace view v_agent_focus_links as
select l.id            as link_id,
       l.order_id,
       o.order_no,
       l.invoice_key,
       l.sio_key,
       l.method,
       l.state,
       l.confidence,
       l.allocated_bhd,
       l.created_at,
       l.decided_at
from shop_order_focus_links l
left join shop_orders o on o.id = l.order_id;

comment on view v_agent_focus_links is
  'AI Head (R7b): which Focus invoice covers which marketplace order (R7a links): invoice_key, method (narration_ref | sio_ref | auto_items | manual), state (suggested | confirmed | rejected), confidence. No note, no created_by / decided_by. ai_head_ro + yq_readonly.';

-- 3i. Focus cadence per shop x SKU, with the owning rep and a strict due flag (plan §21)
create or replace view v_agent_customer_regulars as
with mx as (select max(sale_date) as d from v_sales),
cash as (
  select customer_name, bool_or(coalesce(is_cash_customer, false)) as is_cash
  from v_sales where customer_name is not null group by 1
),
owner as (
  select distinct on (customer_name) customer_name, rep as owner_rep
  from (select v.customer_name, v.salesman_resolved as rep, count(distinct v.invoice_no) as n, max(v.sale_date) as last_d
          from v_sales v cross join mx
         where v.customer_name is not null and v.sale_date > mx.d - 365
         group by 1, 2) x
  order by customer_name, n desc, last_d desc, rep
)
select translate(left(md5(lower(r.customer_name)), 12), '0123456789', 'ghijklmnop')   as shop_key,
       ai_agent_mask(r.customer_name)                                                      as customer_name,
       o.owner_rep,
       r.item_code,
       r.times_bought,
       r.median_qty,
       r.cadence_days,
       r.first_bought,
       r.last_bought,
       r.days_since,
       r.due,
       (r.times_bought >= 4 and r.cadence_days is not null
        and r.days_since > 1.5 * r.cadence_days)                                         as due_strict
from v_customer_regulars r
left join cash  c on c.customer_name = r.customer_name
left join owner o on o.customer_name = r.customer_name
where not coalesce(c.is_cash, false);

comment on view v_agent_customer_regulars is
  'AI Head (R7b): v_customer_regulars (Focus cadence per shop x SKU, >= 2 purchases) without cash accounts, with shop_key (md5 of the lower-cased name, 12 characters spelled in letters a-p, stable across weeks), the owning rep (most invoices in 365 days) and due_strict (>= 4 purchases and quiet for more than 1.5 x the usual gap, plan §21) beside the view''s own due (80 % of the gap). Shop names: ai_head_ro ONLY, never yq_readonly.';

-- 3j. Focus sales lines, narration masked
create or replace view v_agent_focus_sales as
select v.line_id,
       v.invoice_no,
       v.sale_date,
       ai_agent_mask(v.customer_name)                               as customer_name,
       v.is_cash_customer,
       v.salesman_raw,
       v.salesman_resolved,
       v.channel,
       v.division,
       v.sale_type,
       v.is_giveaway,
       v.sku_code,
       v.item_name,
       v.category_name,
       v.quantity,
       v.rate_bhd,
       v.gross_bhd,
       v.discount_bhd,
       v.taxable_bhd,
       v.vat_amount_bhd,
       v.revenue_bhd,
       v.net_bhd,
       ai_agent_mask(v.narration)                                   as narration
from v_sales v;

comment on view v_agent_focus_sales is
  'AI Head (R7b): v_sales line by line (the same revenue basis) with 8+ digit runs in the customer name and the invoice narration masked; order numbers (YQ-2609-0019) and invoice keys survive the mask. ai_head_ro + yq_readonly.';

-- 3k. one row per catalog item: stock, velocity, latest landed cost, restock asks
create or replace view v_agent_items as
select ci.item_code,
       ci.display_name,
       ci.category,
       ci.brand,
       ci.division,
       ci.is_active,
       coalesce(ci.hidden, false)                        as hidden,
       ci.dealer_price                                   as trade_price_bhd,     -- B2B price book, VAT incl.
       ci.roadshow_price                                 as retail_price_bhd,    -- B2C book, VAT incl.
       ci.rrp                                            as rrp_bhd,
       ci.moq,
       ci.pack_size,
       st.stock_qty,
       st.as_of_date                                     as stock_as_of,
       st.match_source                                   as stock_match_source,
       ve.sold_30d,
       ve.prev_30d,
       ve.sold_90d,
       ve.invoices_90d,
       ve.shops_30d,
       ve.shops_90d,
       ve.last_sold,
       co.landed_cost_bhd,
       co.effective_date                                 as cost_date,
       pc.landed_cost_bhd                                as prev_landed_cost_bhd,
       pc.effective_date                                 as prev_cost_date,
       coalesce(rr.requests_30d, 0)                      as restock_requests_30d,
       coalesce(rr.devices_30d, 0)                       as restock_devices_30d,
       coalesce(rr.qty_interest_30d, 0)                  as restock_qty_interest_30d
from catalog_items ci
left join v_catalog_stock    st on st.item_code = ci.item_code
left join v_catalog_velocity ve on ve.item_code = ci.item_code
left join v_catalog_cost     co on co.item_code = ci.item_code
left join lateral (select m.landed_cost_bhd, m.effective_date      -- the receipt before the latest (margin drift)
                     from mrn_landed_costs m
                    where m.sku_code = ci.item_code
                    order by m.effective_date desc nulls last, m.id desc
                    offset 1 limit 1) pc on true
left join (select r.item_code,
                  count(*)                                  as requests_30d,
                  count(distinct r.device_id)               as devices_30d,
                  sum(coalesce(r.qty_interest, 0))          as qty_interest_30d
             from shop_restock_requests r
            where r.created_at >= now() - interval '30 days'
            group by 1) rr on rr.item_code = ci.item_code;

comment on view v_agent_items is
  'AI Head (R7b): every catalog item with stock (latest snapshot), Focus velocity (30/60/90 d, anchored to the last sale date), the latest MRN landed cost and the one before it (margin drift), and marketplace restock requests in 30 days (counted; never the phone). Prices are the price-book rates, VAT included. ai_head_ro + yq_readonly.';

-- 3l. market signals: restock asks (counted), product finds, field notes
create or replace view v_agent_market_signals as
select 'restock_request'::text                                          as signal,
       null::bigint                                                     as ref_id,
       (r.created_at at time zone 'Asia/Bahrain')::date                 as day,
       r.item_code,
       null::text                                                       as label,
       null::text                                                       as category,
       count(*)::int                                                    as n,
       count(distinct r.device_id)::int                                 as sources,
       sum(coalesce(r.qty_interest, 0))::int                            as qty,
       null::numeric(12,3)                                              as price,
       null::text                                                       as currency,
       null::text                                                       as status
from shop_restock_requests r
group by 3, 4
union all
select 'product_find', f.id, (f.posted_at at time zone 'Asia/Bahrain')::date, f.promoted_item_code,
       ai_agent_mask(left(f.name, 200)), f.category, 1, 1, null,
       f.price_bhd, f.currency, f.status
from product_finds f
union all
select 'field_note', n.id, (n.created_at at time zone 'Asia/Bahrain')::date, null,
       ai_agent_mask(left(n.note, 500)), n.category, 1, 1, null,
       null, null, null
from field_notes n;

comment on view v_agent_market_signals is
  'AI Head (R7b): market signals, each a REPORT until two shops or reps corroborate it (plan §18.9): restock_request (per day x item: requests, distinct devices, quantity of interest; never the phone), product_find (name, category, price as posted, status), field_note (the note, masked, first 500 chars). No poster / creator. ai_head_ro + yq_readonly.';

-- 3m. data trust: as-on date and last load per source
create or replace view v_agent_data_trust as
select 'focus_sales'::text                                 as source,
       max(v.sale_date)                                    as as_of,
       null::timestamptz                                   as loaded_at,
       count(*)::bigint                                    as row_count,
       null::numeric                                       as metric,
       null::text                                          as status
from v_sales v
union all
select 'focus_sku_match_90d', max(v.sale_date), null, count(*),
       round(100.0 * count(*) filter (where v.sku_code is not null) / nullif(count(*), 0), 1), null
from v_sales v
where v.sale_date > (select max(sale_date) from v_sales) - 90
union all
select 'stock_balance', max(sb.as_of_date), max(sb.imported_at),
       count(*) filter (where sb.as_of_date = (select max(as_of_date) from stock_balance)), null, null
from stock_balance sb
union all
select 'ar_ageing_totals', max(a.as_of_date), max(a.imported_at), count(*), null, null
from ar_ageing_totals a
union all
select 'mrn_landed_costs', max(m.effective_date), max(m.created_at), count(*), null, null
from mrn_landed_costs m
union all
select 'focus_upload',
       (max(r.finished_at) filter (where r.status = 'ok') at time zone 'Asia/Bahrain')::date,
       max(r.finished_at) filter (where r.status = 'ok'),
       count(*) filter (where r.status = 'ok'),
       (select r2.join_match_pct from ingest_runs r2
         where r2.status = 'ok' and r2.join_match_pct is not null
         order by r2.finished_at desc nulls last, r2.id desc limit 1),
       (select r3.status from ingest_runs r3 order by r3.finished_at desc nulls last, r3.id desc limit 1)
from ingest_runs r
union all
select 'marketplace_orders', (max(o.created_at) at time zone 'Asia/Bahrain')::date, max(o.created_at), count(*), null, null
from shop_orders o
where not coalesce(o.is_test, false)
union all
select 'marketplace_events', (max(e.ts) at time zone 'Asia/Bahrain')::date, max(e.ts), count(*), null, null
from shop_events e;

comment on view v_agent_data_trust is
  'AI Head (R7b): the data trust gate. One row per source: as_of (the data date), loaded_at (the load time), row_count; focus_sku_match_90d.metric = % of the last 90 days of Focus lines mapped to a SKU; focus_upload.metric = the last ok upload''s voucher<->invoice join match %, status = the latest run''s status. ai_head_ro + yq_readonly.';

-- 3n. what the AI Head proposed and what the owner decided
create or replace view v_agent_insights as
select i.id as insight_id, i.kind, i.week_ending, i.section, i.tag, i.text, i.evidence, i.confidence, i.action,
       i.rank, i.data_as_of, i.status, i.decided_at, i.created_at
from ai_insights i;

comment on view v_agent_insights is
  'AI Head (R7b): ai_insights without decided_by / created_by, so the next review can follow up last week''s approved actions. ai_head_ro + yq_readonly.';

-- ── 4. grants (plain statements) ─────────────────────────────────────────────────
revoke all on table v_agent_shop_orders, v_agent_shop_lines, v_agent_shop_events, v_agent_funnel_daily,
                    v_agent_search_demand, v_agent_rep_governance, v_agent_statements, v_agent_focus_links,
                    v_agent_customer_regulars, v_agent_focus_sales, v_agent_items, v_agent_market_signals,
                    v_agent_data_trust, v_agent_insights
  from anon, authenticated;
grant select on v_agent_shop_orders, v_agent_shop_lines, v_agent_shop_events, v_agent_funnel_daily,
                v_agent_search_demand, v_agent_rep_governance, v_agent_statements, v_agent_focus_links,
                v_agent_customer_regulars, v_agent_focus_sales, v_agent_items, v_agent_market_signals,
                v_agent_data_trust, v_agent_insights
  to ai_head_ro;
grant select on v_agent_shop_orders, v_agent_shop_lines, v_agent_shop_events, v_agent_funnel_daily,
                v_agent_search_demand, v_agent_rep_governance, v_agent_statements, v_agent_focus_links,
                v_agent_focus_sales, v_agent_items, v_agent_market_signals, v_agent_data_trust, v_agent_insights
  to yq_readonly;
-- the standing rule for shop names with cadence (marketplace_migration.sql 9a): never yq_readonly
revoke all on table v_agent_customer_regulars from yq_readonly;

-- ── 5. self-check: fails the whole transaction if anything is off ─────────────────
do $$
declare
  v text;
  n int;
  views text[] := array['v_agent_shop_orders', 'v_agent_shop_lines', 'v_agent_shop_events', 'v_agent_funnel_daily',
                        'v_agent_search_demand', 'v_agent_rep_governance', 'v_agent_statements', 'v_agent_focus_links',
                        'v_agent_customer_regulars', 'v_agent_focus_sales', 'v_agent_items', 'v_agent_market_signals',
                        'v_agent_data_trust', 'v_agent_insights'];
begin
  foreach v in array views loop
    if to_regclass('public.' || v) is null then
      raise exception 'r7b_ai_head: % missing', v;
    end if;
    if (select pg_get_userbyid(c.relowner) from pg_class c where c.oid = ('public.' || v)::regclass) <> 'postgres' then
      raise exception 'r7b_ai_head: % must be owned by postgres (apply as postgres)', v;
    end if;
    if exists (select 1 from pg_class c where c.oid = ('public.' || v)::regclass
               and coalesce(array_to_string(c.reloptions, ','), '') ilike '%security_invoker%') then
      raise exception 'r7b_ai_head: % must not be security_invoker', v;
    end if;
    if not has_table_privilege('ai_head_ro', 'public.' || v, 'SELECT') then
      raise exception 'r7b_ai_head: ai_head_ro cannot read %', v;
    end if;
    if has_table_privilege('ai_head_ro', 'public.' || v, 'INSERT')
       or has_table_privilege('ai_head_ro', 'public.' || v, 'UPDATE')
       or has_table_privilege('ai_head_ro', 'public.' || v, 'DELETE') then
      raise exception 'r7b_ai_head: ai_head_ro may write to %', v;
    end if;
    if v <> 'v_agent_customer_regulars' and not has_table_privilege('yq_readonly', 'public.' || v, 'SELECT') then
      raise exception 'r7b_ai_head: yq_readonly cannot read %', v;
    end if;
    execute format('select count(*) from public.%I', v) into n;     -- every view answers
  end loop;
  if has_table_privilege('yq_readonly', 'public.v_agent_customer_regulars', 'SELECT') then
    raise exception 'r7b_ai_head: v_agent_customer_regulars carries shop names and must not be readable by yq_readonly';
  end if;
  -- no contact detail or raw identifier in any v_agent_* column
  select count(*) into n from information_schema.columns
  where table_schema = 'public' and table_name like 'v\_agent\_%'
    and (column_name ilike '%phone%' or column_name ilike '%email%' or column_name ilike '%whatsapp%'
         or column_name in ('token', 'ip_hash', 'ua', 'device_id', 'session_id', 'note', 'created_by',
                            'decided_by', 'approved_by', 'paid_by', 'superseded_by', 'posted_by', 'user_email',
                            'target_snapshot', 'address', 'contact_name'));
  if n > 0 then
    raise exception 'r7b_ai_head: % v_agent_* column(s) carry a contact detail or raw identifier', n;
  end if;
  if exists (select 1 from information_schema.role_table_grants
             where table_schema = 'public' and grantee in ('anon', 'authenticated')
               and (table_name like 'v\_agent\_%' or table_name = 'ai_insights')) then
    raise exception 'r7b_ai_head: a v_agent_* view or ai_insights is granted to anon/authenticated';
  end if;
  -- the login: exists, can log in, no power, member of nothing, read-only defaults
  if not exists (select 1 from pg_roles where rolname = 'ai_head_ro' and rolcanlogin and not rolsuper
                 and not rolcreaterole and not rolcreatedb and not rolreplication and not rolbypassrls) then
    raise exception 'r7b_ai_head: ai_head_ro must be a plain LOGIN role (no superuser / createrole / createdb / replication / bypassrls)';
  end if;
  if exists (select 1 from pg_auth_members m join pg_roles r on r.oid = m.member where r.rolname = 'ai_head_ro') then
    raise exception 'r7b_ai_head: ai_head_ro must be a member of no role (yq_readonly owns SECURITY DEFINER functions and reads phones) -- REVOKE <role> FROM ai_head_ro';
  end if;
  select count(*) into n from pg_db_role_setting s join pg_roles r on r.oid = s.setrole
  where r.rolname = 'ai_head_ro' and s.setdatabase = 0
    and 'statement_timeout=15s' = any(s.setconfig) and 'default_transaction_read_only=on' = any(s.setconfig);
  if n <> 1 then
    raise exception 'r7b_ai_head: ai_head_ro must carry statement_timeout=15s and default_transaction_read_only=on';
  end if;
  foreach v in array array['shop_orders', 'shop_order_lines', 'shop_events', 'salesmen', 'shop_customers',
                           'customer_contacts', 'leads', 'user_roles', 'ai_insights', 'audit_log'] loop
    if to_regclass('public.' || v) is not null and has_table_privilege('ai_head_ro', 'public.' || v, 'SELECT') then
      raise exception 'r7b_ai_head: ai_head_ro must not read the base table %', v;
    end if;
  end loop;
  if exists (select 1 from pg_proc p join pg_namespace s on s.oid = p.pronamespace
             where s.nspname = 'public' and p.proname like 'run\_readonly\_query%'
               and has_function_privilege('ai_head_ro', p.oid, 'EXECUTE')) then
    raise exception 'r7b_ai_head: ai_head_ro must not execute the assistant''s read RPCs (they run as yq_readonly)';
  end if;
  -- the mask: callable by the readers, never by the browser roles
  if not has_function_privilege('ai_head_ro', 'public.ai_agent_mask(text)', 'EXECUTE')
     or not has_function_privilege('yq_readonly', 'public.ai_agent_mask(text)', 'EXECUTE') then
    raise exception 'r7b_ai_head: ai_head_ro / yq_readonly cannot execute ai_agent_mask(text)';
  end if;
  if has_function_privilege('anon', 'public.ai_agent_mask(text)', 'EXECUTE')
     or has_function_privilege('authenticated', 'public.ai_agent_mask(text)', 'EXECUTE') then
    raise exception 'r7b_ai_head: ai_agent_mask(text) executable by anon/authenticated';
  end if;
  if ai_agent_mask('call +973 3312 3456 or a.b@example.com about YQ-2609-0019 / SI-YQ-26-09-110')
     <> 'call [number] or [email] about YQ-2609-0019 / SI-YQ-26-09-110' then
    raise exception 'r7b_ai_head: ai_agent_mask does not mask as documented';
  end if;
  -- ai_insights: RLS, CHECKs, the guard
  if not exists (select 1 from pg_tables where schemaname = 'public' and tablename = 'ai_insights' and rowsecurity) then
    raise exception 'r7b_ai_head: ai_insights must have RLS enabled';
  end if;
  select count(*) into n from pg_constraint
  where conrelid = 'public.ai_insights'::regclass and contype = 'c'
    and conname in ('ai_insights_kind_check', 'ai_insights_tag_check', 'ai_insights_confidence_check',
                    'ai_insights_status_check', 'ai_insights_rank_check', 'ai_insights_text_check',
                    'ai_insights_decided_check');
  if n <> 7 then
    raise exception 'r7b_ai_head: ai_insights has %/7 CHECK constraints', n;
  end if;
  if not exists (select 1 from pg_trigger where tgrelid = 'public.ai_insights'::regclass
                 and tgname = 'ai_insights_guard' and not tgisinternal) then
    raise exception 'r7b_ai_head: the ai_insights_guard trigger is missing';
  end if;
  if has_function_privilege('anon', 'public.ai_insights_guard()', 'EXECUTE')
     or has_function_privilege('authenticated', 'public.ai_insights_guard()', 'EXECUTE') then
    raise exception 'r7b_ai_head: ai_insights_guard() executable by anon/authenticated';
  end if;
  raise notice 'r7b_ai_head: ok (14 v_agent_* views owned by postgres, ai_head_ro read-only on them alone, ai_insights guarded, nothing for anon/authenticated)';
end $$;
