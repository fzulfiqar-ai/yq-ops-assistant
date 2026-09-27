-- Release R7d "Merchant ordering" (27-Sep-2026, plan §12 / §23 item 7): shop_events may hold
-- event = 'product_request' — a merchant tapped "tell {rep}" on a marketplace search that found
-- nothing (meta.q = the words; no phone, no free text beyond the query).
--   Rehearse: python -m scripts.apply_sql scripts/r7d_product_request_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/r7d_product_request_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/r7d_product_request_reverse.sql
--
-- The CHECK below is the LIVE constraint as read read-only on 27-Sep-2026 (pg_get_constraintdef:
-- the 19 marketplace values + 'error' from shop_events_error_migration.sql) plus 'product_request'.
-- tests/test_r7d_merchant.py asserts this list equals app.shop.EVENTS exactly.
--
-- The code runs before this file: until it is applied the CHECK refuses the new kind and
-- app/shop.py record_event stores the same request as a 'search_zero' row with
-- meta.where = 'product_request' (the demand signal is never lost; Market Intel's
-- roll_demand_signals reads search_zero today). Deploy order: either.
--
-- Additive and idempotent: the new CHECK is a superset, so every existing row passes validation and
-- re-running the file is a no-op. No new object is created (nothing to grant or revoke); the closing
-- block re-checks that shop_events is still not granted to anon / authenticated.
set lock_timeout = '2s';

alter table shop_events drop constraint if exists shop_events_event_check;
alter table shop_events add constraint shop_events_event_check
  check (event in ('view', 'item', 'add', 'checkout', 'order',
                   'search', 'search_zero', 'remove', 'qty', 'cart', 'checkout_start',
                   'rail_click', 'reco_click', 'share', 'install', 'reorder', 'cancel', 'vitals',
                   'push_subscribe', 'error', 'product_request'));

do $$
begin
  if not exists (select 1 from pg_constraint
                 where conname = 'shop_events_event_check'
                   and conrelid = 'public.shop_events'::regclass
                   and pg_get_constraintdef(oid) like '%''product_request''%'
                   and pg_get_constraintdef(oid) like '%''error''%'
                   and pg_get_constraintdef(oid) like '%''push_subscribe''%'
                   and pg_get_constraintdef(oid) like '%''search_zero''%') then
    raise exception 'shop_events CHECK was not widened to include product_request (or lost an existing value)';
  end if;
  if exists (select 1 from information_schema.role_table_grants
             where table_schema = 'public' and table_name = 'shop_events' and grantee in ('anon', 'authenticated')) then
    raise exception 'shop_events must not be granted to anon/authenticated';
  end if;
end $$;
