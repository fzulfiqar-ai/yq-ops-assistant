-- Reverse of scripts/r7b_command_views_migration.sql (release R7b). Idempotent.
--   python -m scripts.apply_sql scripts/r7b_command_views_reverse.sql
--
-- Drops v_command_orders. It is a view over shop_orders / shop_order_lines /
-- shop_order_focus_links and holds no data of its own: no order, line, event, link, customer or
-- statement row is touched. Nothing depends on it (the Command Centre reads it through the RPC),
-- so no CASCADE.
--
-- The API keeps working through it: app/metrics.py forgets its cached probe on the first failed
-- read and goes back to v_shop_orders_agent (test orders not excluded, no accepted rate, the
-- invoice match rate empty), and the page says so.

drop view if exists v_command_orders;

do $$
begin
  if to_regclass('public.v_command_orders') is not null then
    raise exception 'r7b_command_views reverse: v_command_orders still present';
  end if;
  raise notice 'r7b_command_views reverse: ok';
end $$;
