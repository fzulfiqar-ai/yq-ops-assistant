-- Daily ops — storekeeper daily stock movement + full purchase-price timeline,
-- plus the salesman_targets RLS gap fix (the yq_readonly RPC reads the table
-- directly for attainment, but the only policy was `to authenticated`, so
-- per-salesman targets came back empty through /report/* queries).
-- Idempotent; apply with:  python -m scripts.apply_migration scripts/daily_ops_migration.sql

-- ── 1. Daily stock movement (per warehouse, per voucher type) ─────────────────
-- One row per (day, warehouse, voucher type) from the Stock_ledger export.
-- /stock/daily zero-fills the calendar and splits receipts/transfers/sales on top.
create or replace view v_stock_daily_movement as
select move_date,
       warehouse_name,
       voucher_type,
       sum(coalesce(received_qty, 0))       as in_qty,
       sum(coalesce(issued_qty, 0))         as out_qty,
       sum(coalesce(received_value_bhd, 0)) as in_value_bhd,
       sum(coalesce(issued_value_bhd, 0))   as out_value_bhd,
       count(distinct voucher)              as vouchers,
       count(distinct item_name)            as items,
       count(*)                             as lines
from stock_movements
where move_date is not null
group by move_date, warehouse_name, voucher_type;

-- ── 2. Purchase price timeline (all events, all sources) ──────────────────────
-- The full "what did we pay, when, to whom" history per SKU — merges the three
-- cost sources with the same code conventions v_price_tracker already uses:
--   po               = purchase-order rate (BHD, code matches sku_code exactly)
--   mrn              = goods-received landed rate (BHD, keyed by leading name token)
--   supplier_invoice = vendor proforma price (RMB; UI converts via the fx settings chain)
create or replace view v_purchase_price_events as
select code                              as sku_code,
       'po'::text                        as source,
       po_date                           as event_date,
       vendor,
       po_no                             as ref_no,
       qty,
       rate_bhd                          as unit_cost_bhd,
       null::numeric                     as unit_price_rmb,
       description                       as detail
from v_po_item
where code is not null
union all
select split_part(item_name, ' ', 1),
       'mrn',
       purchased_on,
       vendor,
       mrn_no,
       qty,
       cost_bhd,
       null,
       item_name
from v_purchase_history
union all
select model,
       'supplier_invoice',
       invoice_date,
       coalesce(vendor, 'VFAN'),
       invoice_no,
       qty,
       null,
       net_price_rmb,
       trim(coalesce(model, '') || ' ' || coalesce(spec, ''))
from supplier_prices
where model is not null and net_price_rmb > 0;

-- ── 3. salesman_targets RLS fix ────────────────────────────────────────────────
drop policy if exists salesman_targets_yq_readonly_read on salesman_targets;
create policy salesman_targets_yq_readonly_read on salesman_targets
  for select to yq_readonly using (true);

-- ── 4. Grants (plain statements — role grants inside DO blocks crash supautils) ─
grant select on v_stock_daily_movement, v_purchase_price_events to yq_readonly;
