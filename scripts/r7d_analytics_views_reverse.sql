-- Reverse of scripts/r7d_analytics_views_migration.sql (release R7d). Idempotent.
--   python -m scripts.apply_sql scripts/r7d_analytics_views_reverse.sql
--
-- Puts v_shop_search_terms and v_shop_rail_perf back to their 16-Sep-2026 definitions
-- (scripts/marketplace_migration.sql 9c / 9d, read back from production with pg_get_viewdef on
-- 27-Sep-2026) and drops the four views R7d added. Every one of them is a view over the shop tables
-- and holds no data of its own: no order, line, event, customer or statement row is touched.
--
-- Order matters: v_shop_search_terms reads v_shop_search_daily after the migration, so it is put
-- back (reading shop_events again) BEFORE v_shop_search_daily is dropped. v_shop_rail_perf gained
-- four appended columns, which CREATE OR REPLACE cannot take away, so it is dropped and created
-- again with its old six columns and its old grants (nothing depends on it — checked on
-- production 27-Sep-2026; a plain DROP fails loudly if that ever changes, there is no CASCADE).
--
-- The API keeps working: app/shop.py forgets its probe on the first failed read and goes back to
-- reading the raw events (paged), and the page says so.

-- 1. v_shop_search_terms: its 16-Sep definition (all search pings, keystrokes included)
create or replace view v_shop_search_terms as
select lower(trim(meta->>'q'))                                   as term,
       count(*)                                                  as searches,
       count(*) filter (where event = 'search_zero')             as zero_results,
       count(distinct session_id)                                as sessions,
       max(ts)                                                   as last_seen
from shop_events
where event in ('search', 'search_zero') and coalesce(meta->>'q', '') <> ''
group by 1;

comment on view v_shop_search_terms is null;

-- 2. v_shop_rail_perf: its 16-Sep definition (UTC day, six columns)
drop view if exists v_shop_rail_perf;
create view v_shop_rail_perf as
select meta->>'rail'                                             as rail,
       date_trunc('day', ts)::date                               as day,
       count(*) filter (where event = 'rail_click')              as clicks,
       count(*) filter (where event = 'reco_click')              as reco_clicks,
       count(*) filter (where event = 'add')                     as adds,
       count(distinct session_id)                                as sessions
from shop_events
where meta ? 'rail'
group by 1, 2;

revoke all on v_shop_rail_perf from anon, authenticated;
grant select on v_shop_rail_perf to yq_readonly;

-- 3. the views R7d added (nothing depends on them once step 1 has run)
drop view if exists v_merchant_360;
drop view if exists v_shop_vitals;
drop view if exists v_shop_search_daily;
drop view if exists v_shop_funnel_daily;

do $$
declare
  v text;
begin
  foreach v in array array['v_shop_funnel_daily', 'v_shop_search_daily', 'v_shop_vitals', 'v_merchant_360'] loop
    if to_regclass('public.' || v) is not null then
      raise exception 'r7d_analytics_views reverse: % still present', v;
    end if;
  end loop;
  if (select string_agg(a.attname, ',' order by a.attnum) from pg_attribute a
      where a.attrelid = 'public.v_shop_rail_perf'::regclass and a.attnum > 0 and not a.attisdropped)
     <> 'rail,day,clicks,reco_clicks,adds,sessions' then
    raise exception 'r7d_analytics_views reverse: v_shop_rail_perf is not back to its six columns';
  end if;
  if exists (select 1 from information_schema.role_table_grants
             where table_schema = 'public' and table_name in ('v_shop_rail_perf', 'v_shop_search_terms')
               and grantee in ('anon', 'authenticated')) then
    raise exception 'r7d_analytics_views reverse: v_shop_rail_perf / v_shop_search_terms granted to anon/authenticated';
  end if;
  if exists (select 1 from pg_roles where rolname = 'yq_readonly')
     and not (has_table_privilege('yq_readonly', 'public.v_shop_rail_perf', 'SELECT')
              and has_table_privilege('yq_readonly', 'public.v_shop_search_terms', 'SELECT')) then
    raise exception 'r7d_analytics_views reverse: yq_readonly lost v_shop_rail_perf / v_shop_search_terms';
  end if;
  raise notice 'r7d_analytics_views reverse: ok';
end $$;
