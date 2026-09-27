-- Notifications log, release R7a (27-Sep-2026, Sprint 1 "Make it true", reminder hygiene). Additive, idempotent.
--   Rehearse: python -m scripts.apply_sql scripts/r7_notifications_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/r7_notifications_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/r7_notifications_reverse.sql
--
-- Why: every reminder attempt used to be a shop_order_events row event='reminded'. With Resend in
-- testing mode almost none reached a rep, so 262 of the 311 order events (84 %) were "rep not
-- reached" and the order timeline drowned in them. From R7a every alert attempt -- new order,
-- re-send, rep reminder, owner escalation, daily digest, stale-data alert -- is one row per
-- channel here, and the timeline keeps a 'reminded' event only when someone was reached or the
-- chase moved up a level (the first rep try, the first owner escalation). The back-off state
-- (app/shop_jobs.py _reminder_history) reads this table AND the old 'reminded' events, so the
-- hourly retry, the 12 h cadence and the 7-day / 10-reminder cap carry across the switch.
--
-- The old 'reminded' events are kept exactly as they are: nothing here reads, moves or deletes a
-- shop_order_events row, and no other table changes.
--
-- Rows name a recipient only masked (a***@example.com, ***1234) and provider errors are scrubbed
-- of addresses and numbers before they are written. Service role only: RLS on, no policies,
-- nothing granted to anon / authenticated / yq_readonly.
--
-- The code tolerates this table being absent (app/shop_notify.py probes it once and caches the
-- answer: a hit for 10 minutes, a miss for 1). Until it exists the jobs write their per-attempt
-- 'reminded' events exactly as before, so the API may deploy before or after this runs.

create table if not exists shop_notifications (
  id               bigint generated always as identity primary key,
  order_id         bigint references shop_orders(id) on delete cascade,   -- NULL: not about one order (stale data, the daily digest)
  kind             text        not null
                   check (kind in ('new_order', 'reminder', 'escalation', 'owner_digest', 'status_update', 'stale_data', 'retry')),
  channel          text        not null,            -- email | telegram | whatsapp | push | none
  recipient_role   text
                   check (recipient_role in ('rep', 'owner', 'management', 'merchant')),   -- NULL: the fan-out failed before anyone was picked
  recipient_masked text,                            -- a***@example.com, ***1234 -- never the full address
  status           text        not null
                   check (status in ('sent', 'failed', 'skipped')),
  provider         text,                            -- resend | brevo | smtp | telegram | whatsapp_cloud
  error            text,                            -- the provider's reason, scrubbed of addresses and numbers
  level            text,                            -- rep | owner | digest | unassigned (reminders only)
  attempt          int,                             -- 1 = first try of this kind for this recipient role
  detail           jsonb,
  created_at       timestamptz not null default now()
);
create index if not exists shop_notifications_order_idx on shop_notifications (order_id, created_at desc);
create index if not exists shop_notifications_kind_idx  on shop_notifications (kind, created_at desc);
alter table shop_notifications enable row level security;      -- service role only; no policies
revoke all on table shop_notifications from anon, authenticated;
revoke all on sequence shop_notifications_id_seq from anon, authenticated;

comment on table shop_notifications is
  'One row per channel per alert attempt (R7a, 27-Sep-2026): new-order fan-out and its re-sends, rep reminders, owner escalations, the daily digest, stale-data alerts. Recipients masked, provider errors scrubbed. The per-order reminder back-off reads it together with the legacy reminded rows in shop_order_events, which are kept.';
comment on column shop_notifications.channel is
  'email | telegram | whatsapp | push | none (none = nothing was sent: the rep has no email or phone, or the fan-out failed first).';
comment on column shop_notifications.status is
  'sent = the provider accepted it; failed = tried and refused; skipped = the channel is not set up or has no recipient.';

-- ── self-check ─────────────────────────────────────────────────────────────────
do $$
declare
  n int;
  c text;
begin
  if to_regclass('public.shop_notifications') is null then
    raise exception 'r7_notifications: shop_notifications missing';
  end if;
  foreach c in array array['order_id', 'kind', 'channel', 'recipient_role', 'recipient_masked', 'status', 'provider',
                           'error', 'level', 'attempt', 'detail', 'created_at'] loop
    if not exists (select 1 from information_schema.columns
                   where table_schema = 'public' and table_name = 'shop_notifications' and column_name = c) then
      raise exception 'r7_notifications: shop_notifications.% missing', c;
    end if;
  end loop;
  select count(*) into n from pg_constraint
  where conrelid = 'public.shop_notifications'::regclass and contype = 'c'
    and (pg_get_constraintdef(oid) like '%owner_digest%'
      or pg_get_constraintdef(oid) like '%merchant%'
      or pg_get_constraintdef(oid) like '%skipped%');
  if n < 3 then
    raise exception 'r7_notifications: only %/3 CHECK constraints (kind, recipient_role, status) present', n;
  end if;
  if not exists (select 1 from pg_constraint
                 where conrelid = 'public.shop_notifications'::regclass and contype = 'f'
                   and confrelid = 'public.shop_orders'::regclass) then
    raise exception 'r7_notifications: the order_id foreign key to shop_orders is missing';
  end if;
  select count(*) into n from pg_indexes where schemaname = 'public'
    and indexname in ('shop_notifications_order_idx', 'shop_notifications_kind_idx');
  if n < 2 then
    raise exception 'r7_notifications: only %/2 indexes present', n;
  end if;
  if not exists (select 1 from pg_tables where schemaname = 'public' and tablename = 'shop_notifications' and rowsecurity) then
    raise exception 'r7_notifications: shop_notifications must have RLS enabled';
  end if;
  if exists (select 1 from information_schema.role_table_grants
             where table_schema = 'public' and table_name = 'shop_notifications' and grantee in ('anon', 'authenticated')) then
    raise exception 'r7_notifications: shop_notifications must not be granted to anon/authenticated';
  end if;
  if exists (select 1 from information_schema.column_privileges
             where table_schema = 'public' and table_name = 'shop_notifications' and grantee in ('anon', 'authenticated')) then
    raise exception 'r7_notifications: shop_notifications has column grants to anon/authenticated';
  end if;
  if has_sequence_privilege('anon', 'public.shop_notifications_id_seq', 'USAGE,SELECT,UPDATE')
     or has_sequence_privilege('authenticated', 'public.shop_notifications_id_seq', 'USAGE,SELECT,UPDATE') then
    raise exception 'r7_notifications: shop_notifications_id_seq must not be granted to anon/authenticated';
  end if;
  if exists (select 1 from pg_roles where rolname = 'yq_readonly')
     and has_table_privilege('yq_readonly', 'public.shop_notifications', 'SELECT') then
    raise exception 'r7_notifications: shop_notifications names recipients and must not be readable by yq_readonly';
  end if;
  raise notice 'r7_notifications: ok (table, 3 checks, FK, 2 indexes, RLS on, no anon/authenticated/yq_readonly grants)';
end $$;
