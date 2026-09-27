-- Reverse of scripts/r7c_order_lines_qty_migration.sql (R7c order heart). Idempotent.
--   python -m scripts.apply_sql scripts/r7c_order_lines_qty_reverse.sql
--
-- The dropped columns hold what was delivered, why each line changed, which line replaced which, the
-- cost snapshots and who reopened an order and why: live order history. While ANY line or order holds
-- an R7c value this file REFUSES, so a routine reverse can never lose it. To drop them anyway, back
-- them up first and say so in the same session:
--   python -m scripts.db_backup --tables shop_orders,shop_order_lines
--   set yq.r7c_drop = 'yes';   -- then run this file in that same session
-- On tables where every R7c column is still empty the file runs without the switch.
-- No row is written or deleted. The two widened checks are put back to their pre-R7c lists
-- NOT VALID: lines already written as 'substituted' / 'added' / 'unavailable' and cancels coded
-- 'below_minimum' stay exactly as they are (never rewritten); only NEW writes are held to the old
-- lists again. RESTART THE API AFTER REVERSING: has_column() remembers a hit for 10 minutes, and
-- until then the editor may still write an R7c status the narrowed check refuses.
-- No view pins the dropped columns: v_command_orders and v_agent_shop_lines (R7b) read
-- added_at_stage / qty_delivered through to_jsonb(l), so they keep answering (every line then
-- reads as requested again, delivered = confirmed) and nothing has to be restored first.

do $$
declare
  n bigint := 0;
  m bigint;
  c text;
begin
  -- dynamic SQL, one column at a time: after a partial reverse a column may already be gone
  foreach c in array array['qty_delivered', 'change_reason', 'substitute_item_code', 'substitute_for_line',
                           'added_at_stage', 'unit_cost_bhd', 'cost_source'] loop
    if exists (select 1 from information_schema.columns where table_schema = 'public'
               and table_name = 'shop_order_lines' and column_name = c) then
      execute format('select count(*) from public.shop_order_lines where %I is not null', c) into m;
      n := n + m;
    end if;
  end loop;
  foreach c in array array['expected_delivery_date', 'reopened_at', 'reopened_by', 'reopen_reason'] loop
    if exists (select 1 from information_schema.columns where table_schema = 'public'
               and table_name = 'shop_orders' and column_name = c) then
      execute format('select count(*) from public.shop_orders where %I is not null', c) into m;
      n := n + m;
    end if;
  end loop;
  if n > 0 and coalesce(current_setting('yq.r7c_drop', true), '') <> 'yes' then
    raise exception 'r7c_order_lines_qty reverse: % R7c value(s) on order lines / orders (delivered qty, change reasons, substitute links, cost snapshots, added-at stage, reopen who / why, expected delivery) would be lost. Back them up (python -m scripts.db_backup --tables shop_orders,shop_order_lines) and run SET yq.r7c_drop = ''yes'' in this session to drop them.', n;
  end if;
end $$;

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
