-- YQ Assist (28-Oct-2026): the marketplace chat's turns and what merchants suggest.
--   Apply:    python -m scripts.apply_sql scripts/market_assist_migration.sql ; python -m scripts.audit_grants
--   Reverse:  drop table if exists shop_feedback; drop table if exists shop_ask_turns;
--             delete from app_settings where key in ('shop_ai_daily_cap', 'shop_ai_voice_enabled', 'shop_ai_photo_enabled');
--   Rehearsed 28-Oct-2026 on a throwaway Postgres with Supabase's default grants imitated: applied,
--   applied again, reversed, re-applied; self-check verified each time.
-- Additive and idempotent: two new tables and three settings; nothing existing changes. Service role
-- only (RLS on, no policies, anon/authenticated revoked), exactly like shop_restock_requests.
-- Text is stored MASKED (app/market_assist.mask: runs of 7+ digits and e-mail addresses removed
-- before the row is written); order numbers are the merchant's own reference and stay.

-- ── 1. every chat turn: what was asked, what the assistant did, how it went ──────────────────
create table if not exists shop_ask_turns (
  id          bigint generated always as identity primary key,
  ts          timestamptz not null default now(),
  session_id  text,
  device_id   text,
  lang        text,                                     -- the page's language: en / ar
  intent      text not null,                            -- find, fit, add, order_status, usual, handoff, feedback, chat
  message     text not null,                            -- masked
  query       text,                                     -- the catalogue words the model read from it
  reply       text,
  item_codes  text[] not null default '{}',
  outcome     text not null check (outcome in ('answered', 'miss', 'fallback', 'handoff', 'capped', 'error')),
  providers   text,                                     -- which free models answered (for the headroom view)
  ms          integer,
  rating      text check (rating in ('up', 'down')),
  ip_hash     text
);
alter table shop_ask_turns enable row level security;
revoke all on shop_ask_turns from anon, authenticated;
create index if not exists shop_ask_turns_ts_idx on shop_ask_turns (ts desc);
create index if not exists shop_ask_turns_outcome_idx on shop_ask_turns (outcome, ts desc);

comment on table shop_ask_turns is
  'YQ Assist chat turns (28-Oct-2026). Masked merchant text, the intent, the item codes shown and the outcome. Read by the portal (Shop Admin) only.';

-- ── 2. ideas, problem reports, questions for the team, and thumbs on an answer ───────────────
create table if not exists shop_feedback (
  id          bigint generated always as identity primary key,
  ts          timestamptz not null default now(),
  kind        text not null check (kind in ('idea', 'problem', 'question', 'up', 'down')),
  text        text,                                     -- masked
  turn_id     bigint references shop_ask_turns(id) on delete set null,
  device_id   text,
  session_id  text,
  lang        text,
  page        text,                                     -- a route template such as /p/:code, never a raw URL
  status      text not null default 'new' check (status in ('new', 'seen', 'done')),
  handled_by  text,
  handled_at  timestamptz,
  ip_hash     text
);
alter table shop_feedback enable row level security;
revoke all on shop_feedback from anon, authenticated;
create index if not exists shop_feedback_status_idx on shop_feedback (status, ts desc);

comment on table shop_feedback is
  'What merchants suggest or report from the marketplace (YQ Assist, 28-Oct-2026). Masked text. Portal: Shop Admin.';

-- ── 3. the daily budget of model-backed chat turns (app/shop.py SETTING_DEFAULTS) ────────────
insert into app_settings (key, value, description) values
  ('shop_ai_daily_cap', '800',
   'YQ Assist: model-backed chat turns per day before the chat falls back to plain search (0 = no cap)'),
  ('shop_ai_voice_enabled', '1',
   'YQ Assist: the microphone in the chat (speech to text); needs shop_market_ai_enabled'),
  ('shop_ai_photo_enabled', '1',
   'YQ Assist: the camera in the chat (a product, its box or a written order list); needs shop_market_ai_enabled')
on conflict (key) do nothing;

-- ── 4. self-check ────────────────────────────────────────────────────────────────────────────
do $$
declare n int;
begin
  select count(*) into n from pg_class c join pg_namespace ns on ns.oid = c.relnamespace
  where ns.nspname = 'public' and c.relname in ('shop_ask_turns', 'shop_feedback') and c.relrowsecurity;
  if n <> 2 then
    raise exception 'market_assist migration FAILED: %/2 tables with RLS on', n;
  end if;
  select count(*) into n from information_schema.role_table_grants
  where table_schema = 'public' and grantee in ('anon', 'authenticated')
    and table_name in ('shop_ask_turns', 'shop_feedback');
  if n > 0 then
    raise exception 'market_assist migration FAILED: % anon/authenticated grants on the assist tables', n;
  end if;
  select count(*) into n from app_settings where key = 'shop_ai_daily_cap';
  if n <> 1 then
    raise exception 'market_assist migration FAILED: the shop_ai_daily_cap setting is missing';
  end if;
  raise notice 'market_assist migration: verified';
end $$;
