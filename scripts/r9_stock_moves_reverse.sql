-- Reverse of scripts/r9_stock_moves_migration.sql (release 2b, M1). Idempotent.
--   python -m scripts.apply_sql scripts/r9_stock_moves_reverse.sql ; python -m scripts.audit_grants
-- From the MAIN project folder only. If M2 ran (scripts/r9_transfer_flow_drop_migration.sql), reverse it first
-- (scripts/r9_transfer_flow_drop_reverse.sql), so the API that is rolled back to finds v_transfer_flow again.
--
-- What it puts back:
--   * v_transfer_lines is dropped (nothing depends on it: the 2b API reads it by name, and an API rolled back to
--     before 2b never reads it);
--   * v_salesman_stock_recon is the body of scripts/stock_transfer_migration.sql again, verbatim: its rows from the
--     sellers in v_sales, on hand from the stock_balance snapshot. CREATE OR REPLACE VIEW cannot drop the nine
--     columns M1 appended, so the view is dropped and created again (a plain DROP: should a view ever depend on it,
--     the DROP refuses and names it, which is the protection), then given its grants as plain statements: SELECT
--     for yq_readonly (the AI's allowlist reads it), nothing for anon / authenticated (the 16-Sep hotfix's revoke).
--
-- What it CANNOT put back: the stock-ledger narration. M1 replaced the digits of every 9-digit and 15+-digit run in
-- stock_movements.narration (and in the batch importer's copies) with stars, and the database holds them nowhere
-- else; reversing the views does not bring them back. The only way back is the backup taken before M1 (see M1's
-- header): restore the column from it by hand on (voucher, item_name, row_hash). Nothing here touches a row.

set lock_timeout = '2s';      -- a busy view makes the file fail fast, never queue

drop view if exists v_transfer_lines;

drop view if exists v_salesman_stock_recon;

-- scripts/stock_transfer_migration.sql (2), its statement verbatim (one comment line names the MT store generically)
create view v_salesman_stock_recon as
with sellers as (
  select distinct salesman_resolved as wh
  from v_sales
  where salesman_resolved is not null
    and salesman_resolved not ilike 'cash customer%'
),
tin as (   -- stock issued INTO this warehouse (transfers received)
  select to_warehouse_name as wh, sum(issued_qty) as qty, sum(issued_value_bhd) as val
  from stock_movements
  where voucher_type = 'Stock Issue Voucher' and to_warehouse_name is not null
  group by to_warehouse_name
),
tout as (  -- stock issued OUT of this warehouse (returned to main / sent onward)
  select warehouse_name as wh, sum(issued_qty) as qty, sum(issued_value_bhd) as val
  from stock_movements
  where voucher_type = 'Stock Issue Voucher' and to_warehouse_name is not null
  group by warehouse_name
),
shortages as (  -- Focus physical-count shortages booked at this warehouse (the hard signal)
  select warehouse_name as wh,
         sum(coalesce(issued_qty, received_qty, 0))             as qty,
         sum(coalesce(issued_value_bhd, received_value_bhd, 0)) as val
  from stock_movements
  where voucher_type = 'Shortages in Stock'
  group by warehouse_name
),
sold as (
  select salesman_resolved as wh, sum(quantity) as qty, sum(revenue_bhd) as val
  from v_sales
  group by salesman_resolved
),
onhand as (
  select warehouse_name as wh, sum(net_qty) as qty, sum(total_value_bhd) as val
  from stock_balance
  where as_of_date = (select max(as_of_date) from stock_balance)
  group by warehouse_name
)
-- warehouse_type classifies each location (Focus has no such field):
--   hub          = central 'Accessories Warehouse'        (issues stock out by design)
--   damage       = '... Damage ...' stores (accessories/sim damaged stock)
--   sim          = SIM hub
--   modern_trade = the 'multi market' store (YQ's MT channel)
--   facility     = samples / giveaway / other non-selling stores
--   van          = a salesman route or B2C outlet (Causeway / YQ Roadshow) — the accountable seller
-- Only warehouse_type='van' (is_van) rows have a meaningful 'unexplained' gap.
select
  r.*,
  (r.warehouse_type = 'van') as is_van
from (
  select
    s.wh                                                  as salesman,
    case
      when s.wh ilike '%damage%'                                            then 'damage'
      when s.wh ilike '%multi market%'                                      then 'modern_trade'
      when s.wh ilike '%accessories warehouse%'                             then 'hub'
      when s.wh ilike '%sim%'                                               then 'sim'
      when s.wh ilike '%sample%' or s.wh ilike '%giveaway%'
        or s.wh ilike '%inventory%' or s.wh ~* 'warehouse'                  then 'facility'
      else 'van'
    end                                                   as warehouse_type,
    round(coalesce(tin.qty, 0))::numeric                  as transferred_in_qty,
    round(coalesce(tin.val, 0)::numeric, 3)               as transferred_in_value_bhd,
    round(coalesce(tout.qty, 0))::numeric                 as transferred_out_qty,
    round(coalesce(so.qty, 0))::numeric                   as sold_qty,
    round(coalesce(oh.qty, 0))::numeric                   as on_hand_qty,
    round(coalesce(sh.qty, 0))::numeric                   as shortage_qty,
    round(coalesce(sh.val, 0)::numeric, 3)                as shortage_value_bhd,
    round((coalesce(tin.qty, 0) - coalesce(tout.qty, 0))
          - coalesce(so.qty, 0) - coalesce(oh.qty, 0))::numeric as unexplained_qty
  from sellers s
  left join tin       on tin.wh  = s.wh
  left join tout      on tout.wh = s.wh
  left join sold so   on so.wh   = s.wh
  left join onhand oh on oh.wh   = s.wh
  left join shortages sh on sh.wh = s.wh
) r
order by r.shortage_value_bhd desc, r.unexplained_qty desc;

-- grants (plain statements, never inside a DO block)
revoke all on table v_salesman_stock_recon from anon, authenticated;
grant select on v_salesman_stock_recon to yq_readonly;

-- self-check: v_transfer_lines gone; the old eleven columns, nothing appended; the grants
do $$
declare
  got text;
  want constant text :=
    'salesman text, warehouse_type text, transferred_in_qty numeric, transferred_in_value_bhd numeric, '
    || 'transferred_out_qty numeric, sold_qty numeric, on_hand_qty numeric, shortage_qty numeric, '
    || 'shortage_value_bhd numeric, unexplained_qty numeric, is_van boolean';
begin
  if to_regclass('public.v_transfer_lines') is not null then
    raise exception 'r9_stock_moves reverse: v_transfer_lines is still there';
  end if;
  select string_agg(a.attname || ' ' || format_type(a.atttypid, a.atttypmod), ', ' order by a.attnum) into got
    from pg_attribute a
   where a.attrelid = 'public.v_salesman_stock_recon'::regclass and a.attnum > 0 and not a.attisdropped;
  if got is distinct from want then
    raise exception 'r9_stock_moves reverse: v_salesman_stock_recon is (%), expected (%)', got, want;
  end if;
  if exists (select 1 from information_schema.role_table_grants
              where table_schema = 'public' and table_name = 'v_salesman_stock_recon'
                and grantee in ('anon', 'authenticated')) then
    raise exception 'r9_stock_moves reverse: v_salesman_stock_recon must not be granted to anon/authenticated';
  end if;
  if not has_table_privilege('yq_readonly', 'public.v_salesman_stock_recon', 'SELECT') then
    raise exception 'r9_stock_moves reverse: yq_readonly cannot read v_salesman_stock_recon';
  end if;
  raise notice 'r9_stock_moves reverse: ok (v_transfer_lines dropped, v_salesman_stock_recon as stock_transfer_migration.sql has it; the narration stays masked)';
end $$;
