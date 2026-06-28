-- Inter-warehouse stock transfers + per-salesman stock reconciliation. Idempotent.
--
-- Focus flow:  Stock Request Voucher -> Stock Issue Voucher -> Stock Receive Voucher
--              (-> Stock Transfer). A request is optional; a transfer can happen directly.
-- Each salesman IS a warehouse: the central "Accessories Warehouse" ISSUES stock to a
-- salesman's van warehouse, which is then drawn down by his sales. stock_movements already
-- carries warehouse_name (from), to_warehouse_name (to), issued_qty and voucher_type
-- (parsed from the Stock_ledger export) — these views finally EXPOSE that movement so the
-- AI can answer "what did we give salesman X" and "is any salesman losing/leaking stock".
--
-- voucher_type values present in the data (col "Voucher name" of Stock_ledger):
--   'Sales Invoice', 'Stock Issue Voucher', 'Stock Receive Voucher', 'Material Receipt Note',
--   'Sales Return', 'Shortages in Stock', 'Excesses in Stocks'.

-- 1) v_stock_transfers — one row per ISSUE leg (the authoritative from->to record). The matching
--    'Stock Receive Voucher' is the mirror and is intentionally excluded to avoid double counting.
create or replace view v_stock_transfers as
select
  sm.move_date            as transfer_date,
  sm.voucher,
  sm.item_name,
  sm.product_id,
  sm.warehouse_name       as from_warehouse,
  sm.to_warehouse_name    as to_warehouse,
  sm.issued_qty           as qty,
  sm.issued_value_bhd     as value_bhd,
  sm.narration
from stock_movements sm
where sm.voucher_type = 'Stock Issue Voucher'
  and sm.to_warehouse_name is not null
  and sm.issued_qty is not null
  and sm.issued_qty > 0;

-- 2) v_salesman_stock_recon — per salesman/van warehouse, reconcile what we ISSUED to them
--    against what they SOLD, sent back, and still HOLD. Two signals:
--      * shortage_value_bhd  — Focus's OWN physical-count 'Shortages in Stock' (HARD leakage).
--      * unexplained_qty     — net stock that entered the van but is neither sold, returned,
--                              nor on hand (SOFT signal; can include pre-window opening stock).
--    Reconciled in units; values shown for materiality. Only real selling routes (anyone who
--    books sales) are reconciled — the central/special warehouses are excluded automatically.
drop view if exists v_salesman_stock_recon cascade;  -- column set evolves; replace can't reorder
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
--   modern_trade = 'MULTI MARKET W.L.L' (YQ's MT channel)
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

-- 3) Grant to the optional read-only role (defense-in-depth; matches the other migrations).
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'yq_readonly') then
    grant select on v_stock_transfers, v_salesman_stock_recon to yq_readonly;
  end if;
end $$;
