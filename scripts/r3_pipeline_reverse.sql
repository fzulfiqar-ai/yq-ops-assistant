-- Reverse of scripts/r3_pipeline_migration.sql (release R3b). Idempotent.
--   python -m scripts.apply_sql scripts/r3_pipeline_reverse.sql
--
-- Drops the reconciliation view, the admin audit (plan M12: reverse = drop table — the rows go
-- with it, so export first if they are wanted) and the three shop_orders columns. No order,
-- line, event, customer or salesman row is touched: cancel_reason (text), payment_status,
-- payment_method and focus_invoice_no predate this migration and stay. The running code
-- forgets a dropped column after one failed read OR write (has_column / _forget_column via
-- _select_optional and _update_optional), so the API may still be the R3 build when this runs;
-- rolling the API back first is still the cleaner order.

drop view if exists v_shop_focus_recon;

-- the table goes before the function: both of its triggers (row-level and statement-level)
-- depend on shop_admin_audit_no_rewrite(), and DROP TABLE takes them with it
drop trigger if exists shop_admin_audit_append_only on shop_admin_audit;
drop table if exists shop_admin_audit;
drop function if exists shop_admin_audit_no_rewrite();

alter table shop_orders drop constraint if exists shop_orders_cancel_reason_code_check;
alter table shop_orders drop column if exists cancel_reason_code;
alter table shop_orders drop column if exists paid_at;
alter table shop_orders drop column if exists returned_bhd;

do $$
begin
  if to_regclass('public.shop_admin_audit') is not null or to_regclass('public.v_shop_focus_recon') is not null then
    raise exception 'r3_pipeline reverse: objects still present';
  end if;
  if exists (select 1 from information_schema.columns where table_schema = 'public' and table_name = 'shop_orders'
             and column_name in ('cancel_reason_code', 'paid_at', 'returned_bhd')) then
    raise exception 'r3_pipeline reverse: shop_orders columns still present';
  end if;
  raise notice 'r3_pipeline reverse: ok';
end $$;
