-- Reverse of scripts/r7b_market_intel_migration.sql (release R7b). Idempotent.
--   python -m scripts.apply_sql scripts/r7b_market_intel_reverse.sql ; python -m scripts.audit_grants
--
-- Drops the two views, the four Market Intel tables and the append-only trigger function. The rows go
-- with them, so take
--   python -m scripts.db_backup --tables market_items market_observations market_photos market_item_decisions
-- FIRST if the sightings and decisions are wanted. The photo OBJECTS stay in the private storage bucket
-- (nothing here touches storage), and the legacy product_finds / field_notes tables were never changed:
-- the import only copied from them, so the old boards read exactly as before.
--
-- Order: the agent view depends on the clusters view, the clusters view on the tables; the decisions,
-- photos and observations reference the items. Each is dropped after everything that depends on it,
-- so no CASCADE is needed (nothing outside this release depends on them: checked in pg_depend).
--
-- The API keeps working through it: app/market_intel.py forgets its cached probe the first time a read
-- or a write meets a missing table, then answers "not set up yet" and saves captures to the legacy
-- tables again.

drop view if exists v_market_signals_agent;
drop view if exists v_market_clusters;
drop table if exists market_item_decisions;
drop function if exists market_item_decisions_no_rewrite();
drop table if exists market_photos;
drop table if exists market_observations;
drop table if exists market_items;

do $$
begin
  if to_regclass('public.market_items') is not null or to_regclass('public.market_observations') is not null
     or to_regclass('public.market_photos') is not null or to_regclass('public.market_item_decisions') is not null
     or to_regclass('public.v_market_clusters') is not null or to_regclass('public.v_market_signals_agent') is not null then
    raise exception 'r7b_market_intel reverse: something is still present';
  end if;
  raise notice 'r7b_market_intel reverse: ok';
end $$;
