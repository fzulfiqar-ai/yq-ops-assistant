-- Reverse of shop_orders_ops_migration.sql (R1 M2). Only if the columns must go: dropping
-- is_test forgets which orders were tests (90 and 97) and dropping the confirmed prices
-- loses what the rep confirmed per line — take
--   python -m scripts.db_backup --tables shop_orders,shop_order_lines
-- FIRST. No deploy is needed to reverse, but RESTART THE API AFTER REVERSING: has_column()
-- remembers a hit for 10 minutes, and until then every reader that meets 42703 / PGRST204
-- forgets the probe and re-reads without the column (one extra round trip per reader, once);
-- a restart clears the cache outright.
alter table shop_order_lines drop column if exists line_total_confirmed;
alter table shop_order_lines drop column if exists unit_price_confirmed;
alter table shop_orders      drop column if exists notify_attempts;
alter table shop_orders      drop column if exists confirm_notified_at;
alter table shop_orders      drop column if exists is_test;

do $$
begin
  if exists (select 1 from information_schema.columns
             where table_schema = 'public'
               and ((table_name = 'shop_orders' and column_name in ('is_test', 'confirm_notified_at', 'notify_attempts'))
                 or (table_name = 'shop_order_lines' and column_name in ('unit_price_confirmed', 'line_total_confirmed')))) then
    raise exception 'shop_orders_ops reverse: a column is still present';
  end if;
  raise notice 'shop_orders_ops reverse: ok';
end $$;
