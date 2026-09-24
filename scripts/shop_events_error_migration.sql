-- Release R6 (24-Sep-2026, plan §9 Step 6 "error telemetry via M3"): shop_events may hold event = 'error'.
--   Rehearse: python -m scripts.apply_sql scripts/shop_events_error_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/shop_events_error_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/shop_events_error_reverse.sql
--
-- Why: app/shop.py EVENTS has carried 'error' since R1 and both ErrorBoundaries now send one
-- (web/src/market/ui/ErrorBoundary.tsx, web/src/components/ErrorBoundary.tsx: build, error class,
-- scrubbed message, route template — no PII), but the table's CHECK never allowed it, so every such
-- insert failed quietly (record_event logs at debug and returns False) and no client error has ever
-- reached the table. The CHECK below is the LIVE constraint as read on 24-Sep-2026 (read-only,
-- pg_get_constraintdef): the 19 marketplace_migration.sql values, unchanged since, plus 'error'.
-- Nothing R1 added to the DB touched this constraint; tests/test_r6_speed.py asserts this list equals
-- app.shop.EVENTS exactly, so the two cannot drift apart again.
--
-- Additive and idempotent: the new CHECK is a superset, so every existing row passes validation and
-- re-running the file is a no-op. Rows: ~2,600 (24-Sep-2026); the ALTER scans them once, sub-second.
set lock_timeout = '2s';

alter table shop_events drop constraint if exists shop_events_event_check;
alter table shop_events add constraint shop_events_event_check
  check (event in ('view', 'item', 'add', 'checkout', 'order',
                   'search', 'search_zero', 'remove', 'qty', 'cart', 'checkout_start',
                   'rail_click', 'reco_click', 'share', 'install', 'reorder', 'cancel', 'vitals',
                   'push_subscribe', 'error'));

do $$
begin
  if not exists (select 1 from pg_constraint
                 where conname = 'shop_events_event_check'
                   and conrelid = 'public.shop_events'::regclass
                   and pg_get_constraintdef(oid) like '%''error''%'
                   and pg_get_constraintdef(oid) like '%''push_subscribe''%'
                   and pg_get_constraintdef(oid) like '%''vitals''%') then
    raise exception 'shop_events CHECK was not widened to include error (or lost an existing value)';
  end if;
  if exists (select 1 from information_schema.role_table_grants
             where table_schema = 'public' and table_name = 'shop_events' and grantee in ('anon', 'authenticated')) then
    raise exception 'shop_events must not be granted to anon/authenticated';
  end if;
end $$;
