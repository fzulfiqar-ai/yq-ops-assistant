-- Reverse of scripts/r7b_ai_head_migration.sql (release R7b, Weekly AI Head). Idempotent.
--   python -m scripts.apply_sql scripts/r7b_ai_head_reverse.sql
--
-- Drops the fourteen v_agent_* views, their mask function ai_agent_mask(text), the ai_head_ro login
-- and -- only when it is empty or you say so -- the ai_insights table. Nothing else is touched: no
-- order, line, event, customer, rep or statement row; the base tables and every view the portal reads
-- stay exactly as they are. Nothing depends on the v_agent_* views, so no CASCADE.
--
-- ai_insights holds the owner's decisions (approved / rejected / done). While it has rows this file
-- REFUSES, so a routine reverse can never lose them. To drop it anyway, back it up first and say so
-- in the same session:
--   python -m scripts.db_backup --tables ai_insights --out business_data/backups/<date>_ai_insights
--   set yq.ai_insights_drop = 'yes';   -- then run this file in that same session
-- The API keeps working through a reverse: GET /management/insights answers {"available": false}
-- until the table is back, and scripts/ai_head/pack.py falls back to DATABASE_URL (read-only).
--
-- ai_head_ro: its open sessions are not killed (Postgres lets them finish); ALTER ROLE ai_head_ro
-- NOLOGIN first if you want it locked out at once. If someone granted it more by hand, DROP ROLE
-- refuses and names what is left: revoke that first (never DROP OWNED / CASCADE to get past it).

do $$
declare
  n bigint := 0;
begin
  if to_regclass('public.ai_insights') is not null then          -- dynamic: the table may be gone already
    execute 'select count(*) from public.ai_insights' into n;
  end if;
  if n > 0 and coalesce(current_setting('yq.ai_insights_drop', true), '') <> 'yes' then
    raise exception 'r7b_ai_head reverse: ai_insights has % row(s) (the owner''s decisions). Back it up (python -m scripts.db_backup --tables ai_insights) and run SET yq.ai_insights_drop = ''yes'' in this session to drop it.', n;
  end if;
end $$;

drop view if exists v_agent_insights;
drop view if exists v_agent_data_trust;
drop view if exists v_agent_market_signals;
drop view if exists v_agent_items;
drop view if exists v_agent_focus_sales;
drop view if exists v_agent_customer_regulars;
drop view if exists v_agent_focus_links;
drop view if exists v_agent_statements;
drop view if exists v_agent_rep_governance;
drop view if exists v_agent_search_demand;
drop view if exists v_agent_funnel_daily;
drop view if exists v_agent_shop_events;
drop view if exists v_agent_shop_lines;
drop view if exists v_agent_shop_orders;

drop function if exists ai_agent_mask(text);
drop table if exists ai_insights;           -- its trigger goes with it
drop function if exists ai_insights_guard();

-- the login: its schema grant, then the role (it owns nothing and holds no other privilege)
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'ai_head_ro') then
    execute 'revoke usage on schema public from ai_head_ro';
  end if;
end $$;
drop role if exists ai_head_ro;

do $$
declare
  v text;
begin
  foreach v in array array['v_agent_shop_orders', 'v_agent_shop_lines', 'v_agent_shop_events', 'v_agent_funnel_daily',
                           'v_agent_search_demand', 'v_agent_rep_governance', 'v_agent_statements',
                           'v_agent_focus_links', 'v_agent_customer_regulars', 'v_agent_focus_sales', 'v_agent_items',
                           'v_agent_market_signals', 'v_agent_data_trust', 'v_agent_insights', 'ai_insights'] loop
    if to_regclass('public.' || v) is not null then
      raise exception 'r7b_ai_head reverse: % still present', v;
    end if;
  end loop;
  if exists (select 1 from pg_roles where rolname = 'ai_head_ro') then
    raise exception 'r7b_ai_head reverse: role ai_head_ro still present';
  end if;
  if exists (select 1 from pg_proc where proname in ('ai_agent_mask', 'ai_insights_guard')
             and pronamespace = 'public'::regnamespace) then
    raise exception 'r7b_ai_head reverse: a function of the migration is still present';
  end if;
  if to_regclass('public.shop_orders') is null or to_regclass('public.v_sales') is null
     or to_regclass('public.v_customer_regulars') is null then
    raise exception 'r7b_ai_head reverse: a base object is missing -- this file must never touch one';
  end if;
  raise notice 'r7b_ai_head reverse: ok (views, ai_insights and ai_head_ro gone; base objects untouched)';
end $$;
