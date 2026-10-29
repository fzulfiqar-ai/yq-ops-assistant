-- YQ Assist v2 (29-Oct-2026): the tickets the chat raises, their timeline, and turns that say who and how fast.
--   Apply:    python -m scripts.apply_sql scripts/market_assist_v2_migration.sql --rehearse
--             python -m scripts.apply_sql scripts/market_assist_v2_migration.sql ; python -m scripts.audit_grants
--   Reverse:  drop view if exists v_assist_open_tickets; alter table shop_ask_turns drop column if exists ticket_id;
--             drop table if exists shop_ticket_events; drop table if exists shop_tickets;
--             drop function if exists shop_tickets_set_ref(); drop function if exists shop_ticket_events_no_rewrite();
--             (the new shop_ask_turns columns, the widened checks and the settings can stay: nothing else reads them)
-- Additive and idempotent: two new tables, new nullable columns on shop_ask_turns, two widened checks and
-- five settings; nothing existing changes meaning. Service role only (RLS on, no policies, anon/authenticated
-- revoked, sequences too), exactly like shop_ask_turns. Merchant text is stored MASKED (app/market_assist.mask).
-- The code tolerates every piece being absent (the turn log and the ticket calls fail soft), so the API may
-- deploy before or after this runs.

-- ── 1. tickets: a damaged, wrong or missing item, a delivery problem, a call-back or account request ──
create table if not exists shop_tickets (
  id              bigint generated always as identity primary key,
  ref             text unique,                              -- T-2610-0012, set by the trigger below; the merchant's reference
  kind            text not null
                  check (kind in ('damaged', 'wrong_item', 'missing', 'not_working', 'delivery', 'return',
                                  'callback', 'account', 'other')),
  status          text not null default 'new'
                  check (status in ('new', 'in_progress', 'waiting_shop', 'resolved', 'rejected')),
  order_id        bigint references shop_orders(id) on delete set null,
  lines           jsonb not null default '[]'::jsonb,       -- [{line_id, item_code, qty}] checked against that order
  note            text,                                     -- the merchant's words, masked
  photos          jsonb not null default '[]'::jsonb,       -- object paths in the PRIVATE bucket 'assist-tickets'
  customer_id     bigint references shop_customers(id) on delete set null,
  salesman_id     bigint references salesmen(id) on delete set null,   -- who owns it (owner 29-Oct: the order's rep)
  device_id       text,
  session_id      text,
  lang            text,
  resolution      text check (resolution in ('replaced', 'credit_note', 'returned', 'answered', 'not_valid', 'other')),
  case_id         bigint,                                   -- a Market Intel quality case it was added to (market_cases.id)
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  first_seen_at   timestamptz,                              -- the rep or the office first opened it
  resolved_at     timestamptz,
  client_ticket_id text,                                    -- the page's own id: a double tap or a retry is one ticket
  ip_hash         text
);
alter table shop_tickets enable row level security;        -- service role only; no policies
revoke all on table shop_tickets from anon, authenticated;
revoke all on sequence shop_tickets_id_seq from anon, authenticated;
create index if not exists shop_tickets_status_idx   on shop_tickets (status, created_at desc);
create index if not exists shop_tickets_salesman_idx on shop_tickets (salesman_id, status);
create index if not exists shop_tickets_order_idx    on shop_tickets (order_id);
create index if not exists shop_tickets_device_idx   on shop_tickets (device_id, created_at desc);
create unique index if not exists shop_tickets_client_idx on shop_tickets (device_id, client_ticket_id)
  where client_ticket_id is not null;

comment on table shop_tickets is
  'YQ Assist v2 (29-Oct-2026): what a merchant reported in the chat (damaged, wrong or missing items, delivery, a call-back, an account request), only on orders that device holds. Owned by the order''s rep; the owner watches. Masked text, private photos. Kept 730 days after closing (shop_ticket_keep_days).';

-- the reference: T-<Bahrain YYMM>-<id, at least 4 digits>; identity defaults are applied before BEFORE triggers
create or replace function shop_tickets_set_ref() returns trigger
language plpgsql set search_path = '' as $$
begin
  if new.ref is null then
    new.ref := 'T-' || to_char(new.created_at at time zone 'Asia/Bahrain', 'YYMM') || '-' || lpad(new.id::text, 4, '0');
  end if;
  return new;
end $$;
revoke all on function shop_tickets_set_ref() from public, anon, authenticated;

drop trigger if exists shop_tickets_ref on shop_tickets;
create trigger shop_tickets_ref
  before insert on shop_tickets
  for each row execute function shop_tickets_set_ref();

-- ── 2. the ticket's timeline: every change with who and when; rows are never rewritten ──────────
create table if not exists shop_ticket_events (
  id          bigint      generated always as identity primary key,
  ticket_id   bigint      not null references shop_tickets(id) on delete cascade,  -- the retention purge removes both
  at          timestamptz not null default now(),
  actor       text        not null,                         -- merchant | system | the staff login's e-mail
  event       text        not null
              check (event in ('created', 'seen', 'status', 'assign', 'note', 'photo', 'resolve', 'case', 'reminder', 'notify')),
  from_status text,
  to_status   text,
  detail      jsonb       default '{}'::jsonb
);
create index if not exists shop_ticket_events_ticket_idx on shop_ticket_events (ticket_id, at);
alter table shop_ticket_events enable row level security;
revoke all on table shop_ticket_events from anon, authenticated;
revoke all on sequence shop_ticket_events_id_seq from anon, authenticated;

comment on table shop_ticket_events is
  'YQ Assist v2: the timeline of each chat ticket. A row is never changed (trigger); rows leave only with their ticket, through the retention purge.';

create or replace function shop_ticket_events_no_rewrite() returns trigger
language plpgsql set search_path = '' as $$
begin
  if tg_op = 'TRUNCATE' then
    raise exception 'shop_ticket_events cannot be truncated' using errcode = 'restrict_violation';
  end if;
  raise exception 'shop_ticket_events row % cannot be changed', old.id using errcode = 'restrict_violation';
end $$;
revoke all on function shop_ticket_events_no_rewrite() from public, anon, authenticated;

drop trigger if exists shop_ticket_events_no_update on shop_ticket_events;
create trigger shop_ticket_events_no_update
  before update on shop_ticket_events
  for each row execute function shop_ticket_events_no_rewrite();

drop trigger if exists shop_ticket_events_no_truncate on shop_ticket_events;
create trigger shop_ticket_events_no_truncate
  before truncate on shop_ticket_events
  for each statement execute function shop_ticket_events_no_rewrite();

-- one SLA reminder claim per ticket and recipient (the rep first, then the owner): two scheduler runs racing can
-- never send two
create unique index if not exists shop_ticket_events_one_reminder_idx on shop_ticket_events (ticket_id, (detail->>'to'))
  where event = 'reminder';

-- the Command Centre's view (app/metrics.py reads as yq_readonly): the open tickets by reference, kind, status,
-- age and rep only; never the note, the photos, the shop or the device
create or replace view v_assist_open_tickets as
select t.ref, t.kind, t.status, t.created_at, t.first_seen_at, t.salesman_id
from shop_tickets t
where t.status in ('new', 'in_progress', 'waiting_shop');
revoke all on v_assist_open_tickets from anon, authenticated;
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'yq_readonly') then
    grant select on v_assist_open_tickets to yq_readonly;
  end if;
end $$;
comment on view v_assist_open_tickets is
  'YQ Assist v2: the open chat tickets for the Command Centre attention item: reference, kind, status, age and rep id only. yq_readonly only; not security_invoker (the role reads the view, not the table).';

-- ── 3. turns that say who asked, who was offered, what was shown and where the time went ────────
alter table shop_ask_turns add column if not exists action        text;      -- a tile / chip tapped (no model call)
alter table shop_ask_turns add column if not exists detected_lang text;      -- the model's reading: en ar ur hi ml other
alter table shop_ask_turns add column if not exists topic         text;      -- a policy question's topic
alter table shop_ask_turns add column if not exists customer_id   bigint;    -- the shop, when this device has ordered
alter table shop_ask_turns add column if not exists salesman_id   bigint;    -- the rep the chat offered
alter table shop_ask_turns add column if not exists order_ids     bigint[] not null default '{}';
alter table shop_ask_turns add column if not exists cards         jsonb;     -- [{code, why}] in the order shown
alter table shop_ask_turns add column if not exists escalated     boolean not null default false;
alter table shop_ask_turns add column if not exists ms_understand integer;
alter table shop_ask_turns add column if not exists ms_handle     integer;
alter table shop_ask_turns add column if not exists ms_answer     integer;
alter table shop_ask_turns add column if not exists ticket_id     bigint references shop_tickets(id) on delete set null;
create index if not exists shop_ask_turns_session_idx on shop_ask_turns (session_id, ts);

alter table shop_ask_turns drop constraint if exists shop_ask_turns_outcome_check;
alter table shop_ask_turns add constraint shop_ask_turns_outcome_check
  check (outcome in ('answered', 'miss', 'fallback', 'handoff', 'capped', 'error', 'ticket'));

-- ── 4. alerts: a new ticket, a call-back request, a ticket past its SLA ───────────────────────
do $$
begin
  if to_regclass('public.shop_notifications') is not null then
    alter table shop_notifications drop constraint if exists shop_notifications_kind_check;
    alter table shop_notifications add constraint shop_notifications_kind_check
      check (kind in ('new_order', 'reminder', 'escalation', 'owner_digest', 'status_update', 'stale_data', 'retry',
                      'ticket', 'callback', 'ticket_sla'));
  end if;
end $$;

-- ── 5. settings (app/shop.py SETTING_DEFAULTS; owner 29-Oct-2026) ─────────────────────────────
insert into app_settings (key, value, description) values
  ('shop_ai_support_enabled', '0',
   'YQ Assist: merchants can report damaged, wrong or missing items and ask for a call-back in the chat (tickets)'),
  ('shop_ai_retention_days', '180',
   'YQ Assist: chat turns older than this many days are deleted (Bahrain PDPL: personal data only as long as needed)'),
  ('shop_ticket_keep_days', '730',
   'YQ Assist: closed tickets and their photos are deleted this many days after closing'),
  ('shop_ticket_sla_hours', '24',
   'YQ Assist: working hours a new ticket or call-back may wait before its rep, then the owner, is reminded'),
  ('shop_ai_policies', '[]',
   'YQ Assist: the answers the office approved for policy questions (returns, warranty, payment, credit, delivery...), JSON [{topic, en, ar}]; a topic without one is handed to the rep')
on conflict (key) do nothing;

-- ── 6. self-check ────────────────────────────────────────────────────────────────────────────
do $$
declare n int;
begin
  select count(*) into n from pg_class c join pg_namespace ns on ns.oid = c.relnamespace
  where ns.nspname = 'public' and c.relname in ('shop_tickets', 'shop_ticket_events') and c.relrowsecurity;
  if n <> 2 then
    raise exception 'market_assist_v2 migration FAILED: %/2 tables with RLS on', n;
  end if;
  select count(*) into n from information_schema.role_table_grants
  where table_schema = 'public' and grantee in ('anon', 'authenticated')
    and table_name in ('shop_tickets', 'shop_ticket_events', 'shop_ask_turns');
  if n > 0 then
    raise exception 'market_assist_v2 migration FAILED: % anon/authenticated grants on the assist tables', n;
  end if;
  if has_sequence_privilege('anon', 'public.shop_tickets_id_seq', 'USAGE,SELECT,UPDATE')
     or has_sequence_privilege('authenticated', 'public.shop_tickets_id_seq', 'USAGE,SELECT,UPDATE')
     or has_sequence_privilege('anon', 'public.shop_ticket_events_id_seq', 'USAGE,SELECT,UPDATE')
     or has_sequence_privilege('authenticated', 'public.shop_ticket_events_id_seq', 'USAGE,SELECT,UPDATE') then
    raise exception 'market_assist_v2 migration FAILED: a ticket sequence is granted to anon/authenticated';
  end if;
  if exists (select 1 from pg_roles where rolname = 'yq_readonly')
     and (has_table_privilege('yq_readonly', 'public.shop_tickets', 'SELECT')
       or has_table_privilege('yq_readonly', 'public.shop_ticket_events', 'SELECT')) then
    raise exception 'market_assist_v2 migration FAILED: tickets hold merchant reports and must not be readable by yq_readonly';
  end if;
  if exists (select 1 from pg_roles where rolname = 'yq_readonly')
     and not has_table_privilege('yq_readonly', 'public.v_assist_open_tickets', 'SELECT') then
    raise exception 'market_assist_v2 migration FAILED: yq_readonly cannot read v_assist_open_tickets';
  end if;
  if exists (select 1 from pg_class where oid = 'public.v_assist_open_tickets'::regclass
             and coalesce(reloptions::text, '') like '%security_invoker%') then
    raise exception 'market_assist_v2 migration FAILED: v_assist_open_tickets must not be security_invoker';
  end if;
  if exists (select 1 from information_schema.role_table_grants
             where table_schema = 'public' and table_name = 'v_assist_open_tickets' and grantee in ('anon', 'authenticated')) then
    raise exception 'market_assist_v2 migration FAILED: v_assist_open_tickets is granted to anon/authenticated';
  end if;
  select count(*) into n from information_schema.columns
  where table_schema = 'public' and table_name = 'shop_ask_turns'
    and column_name in ('action', 'detected_lang', 'topic', 'customer_id', 'salesman_id', 'order_ids', 'cards',
                        'escalated', 'ms_understand', 'ms_handle', 'ms_answer', 'ticket_id');
  if n <> 12 then
    raise exception 'market_assist_v2 migration FAILED: %/12 new shop_ask_turns columns', n;
  end if;
  select count(*) into n from app_settings
  where key in ('shop_ai_support_enabled', 'shop_ai_retention_days', 'shop_ticket_keep_days',
                'shop_ticket_sla_hours', 'shop_ai_policies');
  if n <> 5 then
    raise exception 'market_assist_v2 migration FAILED: %/5 settings', n;
  end if;
  select count(*) into n from pg_trigger
  where tgname in ('shop_tickets_ref', 'shop_ticket_events_no_update', 'shop_ticket_events_no_truncate');
  if n <> 3 then
    raise exception 'market_assist_v2 migration FAILED: %/3 triggers', n;
  end if;
  raise notice 'market_assist_v2 migration: verified';
end $$;
