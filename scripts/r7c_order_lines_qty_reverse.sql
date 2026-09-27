-- Reverse of scripts/r7c_order_lines_qty_migration.sql (R7c order heart). Idempotent.
--   python -m scripts.apply_sql scripts/r7c_order_lines_qty_reverse.sql
--
-- Take a backup FIRST — the dropped columns hold what was delivered, why each line changed, which
-- line replaced which and the cost snapshots:
--   python -m scripts.db_backup --tables shop_orders,shop_order_lines
-- No row is written or deleted. The two widened checks are put back to their pre-R7c lists
-- NOT VALID: lines already written as 'substituted' / 'added' / 'unavailable' and cancels coded
-- 'below_minimum' stay exactly as they are (never rewritten); only NEW writes are held to the old
-- lists again. RESTART THE API AFTER REVERSING: has_column() remembers a hit for 10 minutes, and
-- until then the editor may still write an R7c status the narrowed check refuses.

alter table shop_order_lines drop constraint if exists shop_order_lines_substitute_for_line_fkey;
alter table shop_order_lines drop constraint if exists shop_order_lines_unit_cost_bhd_check;
alter table shop_order_lines drop constraint if exists shop_order_lines_added_at_stage_check;
alter table shop_order_lines drop constraint if exists shop_order_lines_change_reason_check;
alter table shop_order_lines drop constraint if exists shop_order_lines_qty_delivered_check;

alter table shop_order_lines drop column if exists cost_source;
alter table shop_order_lines drop column if exists unit_cost_bhd;
alter table shop_order_lines drop column if exists added_at_stage;
alter table shop_order_lines drop column if exists substitute_for_line;
alter table shop_order_lines drop column if exists substitute_item_code;
alter table shop_order_lines drop column if exists change_reason;
alter table shop_order_lines drop column if exists qty_delivered;

alter table shop_order_lines drop constraint if exists shop_order_lines_line_status_check;
alter table shop_order_lines add constraint shop_order_lines_line_status_check
  check (line_status in ('ok', 'changed', 'removed', 'backorder')) not valid;

alter table shop_orders drop column if exists reopen_reason;
alter table shop_orders drop column if exists reopened_by;
alter table shop_orders drop column if exists reopened_at;
alter table shop_orders drop column if exists expected_delivery_date;

alter table shop_orders drop constraint if exists shop_orders_cancel_reason_code_check;
alter table shop_orders add constraint shop_orders_cancel_reason_code_check
  check (cancel_reason_code is null or cancel_reason_code in
         ('out_of_stock', 'customer_request', 'duplicate', 'test', 'price_issue', 'other')) not valid;

revoke all on shop_orders, shop_order_lines from anon, authenticated;

do $$
begin
  if exists (select 1 from information_schema.columns
             where table_schema = 'public'
               and ((table_name = 'shop_order_lines' and column_name in
                     ('qty_delivered', 'change_reason', 'substitute_item_code', 'substitute_for_line',
                      'added_at_stage', 'unit_cost_bhd', 'cost_source'))
                 or (table_name = 'shop_orders' and column_name in
                     ('expected_delivery_date', 'reopened_at', 'reopened_by', 'reopen_reason')))) then
    raise exception 'r7c_order_lines_qty reverse: a column is still present';
  end if;
  if not exists (select 1 from pg_constraint where conrelid = 'shop_order_lines'::regclass
                 and conname = 'shop_order_lines_line_status_check')
     or not exists (select 1 from pg_constraint where conrelid = 'shop_orders'::regclass
                    and conname = 'shop_orders_cancel_reason_code_check') then
    raise exception 'r7c_order_lines_qty reverse: a check was not put back';
  end if;
  raise notice 'r7c_order_lines_qty reverse: ok';
end $$;
