-- Economics v2 + receivables truth + reserved stock (release R2, 24-Sep-2026). Additive, idempotent.
--   Rehearse: python -m scripts.apply_sql scripts/economics_v2_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/economics_v2_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/economics_v2_reverse.sql
--
-- 1. v_product_economics prefers the real MRN receipt cost (mrn_landed_costs, latest by receipt
--    date) and falls back to purchase_costs (effective_date desc, id desc). D4: rows dated to the
--    1st of the month lost to a stale 14-Sep extract, so 16 of the 20 LC1716 SKUs were costed from
--    the wrong source. Columns kept; cost_source / cost_effective_date / cost_doc_no appended.
-- 2. v_product_margin computes its own gross profit and an EX-VAT margin instead of trusting the
--    Focus report: its 'Gross Profit' loses the minus sign on loss items (UK03 20W Charger: net
--    439.80, COGS 483.20, GP shown +43.40) and its 'GP Margin %' is not a percentage (median
--    4,835), so a below-cost alert could never fire. Columns kept; gp_computed_bhd, net_ex_vat_bhd,
--    gp_ex_vat_bhd, margin_ex_vat_pct, is_below_cost, ex_vat_source appended. The ex-VAT net is the
--    day book's own Taxable total for the item when the lines reconcile to the report's net amount
--    (the report is VAT-inclusive: its net equals the lines' gross), else net / 1.1 (the same VAT
--    rule v_sales applies). The per-item day-book totals are one GROUP BY over order_lines
--    (+ an index on order_lines.item_name), not a correlated SUM per item.
-- 3. ar_ageing_totals: Focus's own Grand Total per ageing snapshot (parse_receivables_totals).
--    The export shows every balance positive, so customer credits load as money owed and the row
--    sum overstates the book (24-Sep: rows 9,078.860 vs Focus 8,633.840). No sign is guessed: both
--    figures are shown and the gap is flagged for accounts.
-- 4. v_catalog_reserved (M10, staff only): on_hand / reserved / available / in_transit per active
--    catalog code. reserved = open marketplace orders (new / confirmed, not yet issued, not test)
--    created AFTER the newest stock snapshot's end of day in Bahrain -- the one rule that never
--    double-counts a unit the snapshot already saw leave the shelf (0 units today; the "all open"
--    rule would reserve 251 and double-count van issues); backorder lines never reserve (they
--    were never on hand). in_transit = procurement_orders lines in the in-flight stages
--    (raised / paid and their legacy aliases -- an allow-list, so 'closed' never counts; 0 today:
--    the table is empty). Merchants keep on-hand status; nothing in app/shop.py reads this view.
-- No row is deleted or rewritten anywhere in this file (one index is added on order_lines).

-- ── 1. v_product_economics: MRN first, purchase_costs as the fallback ──────────
create or replace view v_product_economics as
select
  pl.sku_code,
  pl.item_name,
  pl.price_bhd,
  c.cost_bhd,
  round((pl.price_bhd - c.cost_bhd)::numeric, 3)                            as margin_bhd,
  case when pl.price_bhd > 0 and c.cost_bhd is not null
       then round(100.0 * (pl.price_bhd - c.cost_bhd) / pl.price_bhd, 1) end as margin_pct,
  c.cost_source,
  c.cost_effective_date,
  c.cost_doc_no
from v_price_list pl
left join lateral (
  select x.cost_bhd, x.cost_source, x.cost_effective_date, x.cost_doc_no
  from (
    select m.landed_cost_bhd as cost_bhd, 'mrn'::text as cost_source, m.effective_date as cost_effective_date,
           m.doc_no as cost_doc_no, 0 as pri, m.id
      from mrn_landed_costs m
     where upper(m.sku_code) = upper(pl.sku_code) and coalesce(m.landed_cost_bhd, 0) > 0
    union all
    select p.landed_cost_bhd, 'purchase_costs'::text, p.effective_date, p.source_file, 1, p.id
      from purchase_costs p
     where upper(p.sku_code) = upper(pl.sku_code) and coalesce(p.landed_cost_bhd, 0) > 0
  ) x
  order by x.pri, x.cost_effective_date desc nulls last, x.id desc
  limit 1
) c on true;

-- ── 2. v_product_margin: computed GP and an ex-VAT margin (existing 15 columns kept) ──
-- The day book's totals per item come from ONE pass over order_lines (the `tx` CTE, GROUP BY
-- item_name) joined once, not a correlated SUM per profitability item: that form re-read the
-- whole table 161 times (365 ms as yq_readonly on production, 1.1-1.4 s on the local copy) on
-- every margins report, agent and digest call, and grew with retained history. The index below
-- serves the same lookup wherever else an item's lines are wanted.
create index if not exists order_lines_item_name_idx on order_lines (item_name);

create or replace view v_product_margin as
with tx as (
  select ol.item_name,
         round(sum(ol.taxable_bhd)::numeric, 3)                                          as taxable,
         round((sum(ol.gross_bhd) - coalesce(sum(ol.discount_bhd), 0))::numeric, 3)      as net_lines
  from order_lines ol
  group by ol.item_name
)
select
  pp.item_name, pp.report_date, pp.gross_bhd, pp.discount_pct, pp.net_amount_bhd, pp.cogs_bhd,
  pp.gross_profit_bhd, pp.gp_margin_pct, pp.misc_charges_bhd, pp.net_profit_bhd, pp.np_margin_pct,
  p.sku_code,
  p.item_name as product_name,
  cat.name    as category_name,
  sp.rate_bhd as list_price_bhd,
  -- appended (R2): the report's own basis with the sign kept, then ex-VAT against COGS
  round((pp.net_amount_bhd - pp.cogs_bhd)::numeric, 3)                                  as gp_computed_bhd,
  x.net_ex_vat_bhd,
  round((x.net_ex_vat_bhd - pp.cogs_bhd)::numeric, 3)                                   as gp_ex_vat_bhd,
  case when x.net_ex_vat_bhd > 0 and pp.cogs_bhd is not null
       then round(100.0 * (x.net_ex_vat_bhd - pp.cogs_bhd) / x.net_ex_vat_bhd, 2) end   as margin_ex_vat_pct,
  (x.net_ex_vat_bhd is not null and pp.cogs_bhd is not null and x.net_ex_vat_bhd < pp.cogs_bhd) as is_below_cost,
  x.ex_vat_source
from product_profitability pp
left join product_aliases pa on pa.alias_text = pp.item_name
left join products p on p.id = pa.product_id
left join categories cat on cat.id = p.category_id
left join lateral (
  select rate_bhd
  from selling_prices
  where sku_code = p.sku_code
    and price_book = 'MA_base'
    and (customer_code is null or customer_code = '')
    and voided_at is null
  order by id desc
  limit 1
) sp on true
left join tx on tx.item_name = pp.item_name
cross join lateral (
  select case when pp.net_amount_bhd is null then null
              when tx.taxable is not null and tx.net_lines is not null
                   and abs(tx.net_lines - pp.net_amount_bhd) <= 0.011 then tx.taxable
              else round((pp.net_amount_bhd / 1.1)::numeric, 3) end     as net_ex_vat_bhd,
         case when pp.net_amount_bhd is null then null
              when tx.taxable is not null and tx.net_lines is not null
                   and abs(tx.net_lines - pp.net_amount_bhd) <= 0.011 then 'day_book'
              else 'vat_rate' end                                        as ex_vat_source
) x
where pp.report_date = (select max(report_date) from product_profitability);

-- ── 3. ar_ageing_totals: Focus's own Grand Total per ageing snapshot ───────────
create table if not exists ar_ageing_totals (
  id               bigint generated always as identity primary key,
  as_of_date       date          not null unique,
  focus_total_bhd  numeric(12,3) not null,      -- the report's Grand Total 'Balance Amount'
  focus_over90_bhd numeric(12,3),               -- Grand Total of the 91-120 ... > 210 buckets
  rows_total_bhd   numeric(12,3),               -- the per-account rows summed at parse time
  source_file      text,
  imported_at      timestamptz   not null default now()
);
alter table ar_ageing_totals enable row level security;
drop policy if exists ar_ageing_totals_yq_readonly_read on ar_ageing_totals;
create policy ar_ageing_totals_yq_readonly_read on ar_ageing_totals for select to yq_readonly using (true);
comment on table ar_ageing_totals is
  'Focus''s own Grand Total of the customer ageing report per as_of_date (R2, 24-Sep-2026). Shown beside the row sum on the Receivables page; the gap is the credit balances the export shows as positive. Never a substitute for the rows.';

-- ── 4. v_catalog_reserved: on-hand / reserved / available / in-transit (staff) ──
create or replace view v_catalog_reserved as
with snap as (
  select max(as_of_date) as as_of from stock_balance
),
open_lines as (
  select upper(l.item_code)                                   as code,
         sum(coalesce(l.qty_confirmed, l.qty))::numeric       as reserved,
         count(distinct o.id)                                 as open_orders
  from shop_order_lines l
  join shop_orders o on o.id = l.order_id
  cross join snap
  where o.status in ('new', 'confirmed')
    and o.issued_at is null
    and coalesce(o.is_test, false) = false
    -- a backorder line is a unit that was never on hand: it must not reserve shelf stock
    -- (it pushed `available` negative); removed / cancelled lines are not open either
    and coalesce(l.line_status, 'ok') not in ('removed', 'cancelled', 'backorder')
    and o.created_at > ((snap.as_of + 1)::timestamp at time zone 'Asia/Bahrain')
  group by 1
),
po_lines as (
  select coalesce(ln->>'item_code', ln->>'code', ln->>'sku_code', ln->>'sku', ln->>'model', ln->>'item',
                  ln->>'item_name')                                                     as ref,
         coalesce(nullif(ln->>'qty', '')::numeric, nullif(ln->>'quantity', '')::numeric, 0) as qty
  from procurement_orders po
  cross join lateral jsonb_array_elements(case when jsonb_typeof(po.lines) = 'array' then po.lines
                                               else '[]'::jsonb end) ln
  -- raised with the vendor and not yet received: an ALLOW-list of the in-flight stages
  -- (app/procurement.py STAGES + the legacy aliases it still resolves). A deny-list let every
  -- 'closed' order -- the stage after 'received' -- count as in transit forever.
  where po.stage in ('raised', 'paid', 'invoiced', 'advance_paid', 'po_raised')
),
transit as (
  select coalesce(
           (select ci.item_code from catalog_items ci where upper(ci.item_code) = upper(pl.ref) limit 1),
           (select p.sku_code from product_aliases pa join products p on p.id = pa.product_id
             where pa.alias_text = pl.ref limit 1)
         )                              as item_code,
         sum(pl.qty)                    as in_transit
  from po_lines pl
  where pl.ref is not null and pl.qty > 0
  group by 1
)
select ci.item_code,
       coalesce(s.stock_qty, 0)::numeric                              as on_hand,
       coalesce(r.reserved, 0)::numeric                               as reserved,
       (coalesce(s.stock_qty, 0) - coalesce(r.reserved, 0))::numeric  as available,
       coalesce(t.in_transit, 0)::numeric                             as in_transit,
       coalesce(r.open_orders, 0)::int                                as open_orders,
       (select as_of from snap)                                       as stock_as_of
from catalog_items ci
left join v_catalog_stock s on s.item_code = ci.item_code
left join open_lines r on r.code = upper(ci.item_code)
left join transit t on t.item_code = ci.item_code
where ci.is_active;

-- ── 4b. The alias autofill's hold-back list (scripts/alias_autofill.py, run after every load) ──
-- Exact-name aliases are written automatically; the codes here never are. Seeded with the owner's
-- 24-Sep-2026 hold-back (near-duplicate SKUs not yet merged). There is NO settings endpoint for
-- this key (app/settings.py accepts the numeric costing keys, app/shop_api.py the shop_* keys):
-- it is edited by the developer with one statement --
--   update app_settings set value = 'X24 CC 1Mtr,X24 CL 1Mtr' where key = 'alias_autofill_exclude';
-- (comma-separated catalog codes, case-insensitive).
insert into app_settings (key, value, description)
values ('alias_autofill_exclude', 'X24 CC 1Mtr,X24 CL 1Mtr',
        'Comma-separated catalog codes the after-load alias autofill must never map a Focus sales string to (owner-held near-duplicates).')
on conflict (key) do nothing;

-- ── 5. Grants (plain statements, never inside DO blocks) ────────────────────────
revoke all on v_product_economics, v_product_margin, v_catalog_reserved, ar_ageing_totals from anon, authenticated;
grant select on v_product_economics, v_product_margin, v_catalog_reserved, ar_ageing_totals to yq_readonly;

-- ── 6. Self-check ──────────────────────────────────────────────────────────────
do $$
declare
  cols     text[];
  n        int;
  uk03     record;
begin
  if to_regclass('public.ar_ageing_totals') is null then
    raise exception 'ar_ageing_totals missing';
  end if;
  if to_regclass('public.v_catalog_reserved') is null then
    raise exception 'v_catalog_reserved missing';
  end if;
  if not exists (select 1 from app_settings where key = 'alias_autofill_exclude') then
    raise exception 'app_settings.alias_autofill_exclude missing';
  end if;
  -- existing columns kept in place, new ones appended
  select array_agg(column_name::text order by ordinal_position) into cols
    from information_schema.columns where table_schema = 'public' and table_name = 'v_product_economics';
  if cols[1:6] <> array['sku_code', 'item_name', 'price_bhd', 'cost_bhd', 'margin_bhd', 'margin_pct']
     or cols[7:9] <> array['cost_source', 'cost_effective_date', 'cost_doc_no'] then
    raise exception 'v_product_economics columns are %', cols;
  end if;
  select array_agg(column_name::text order by ordinal_position) into cols
    from information_schema.columns where table_schema = 'public' and table_name = 'v_product_margin';
  if cols[1:15] <> array['item_name', 'report_date', 'gross_bhd', 'discount_pct', 'net_amount_bhd', 'cogs_bhd',
                         'gross_profit_bhd', 'gp_margin_pct', 'misc_charges_bhd', 'net_profit_bhd', 'np_margin_pct',
                         'sku_code', 'product_name', 'category_name', 'list_price_bhd']
     or cols[16:21] <> array['gp_computed_bhd', 'net_ex_vat_bhd', 'gp_ex_vat_bhd', 'margin_ex_vat_pct',
                             'is_below_cost', 'ex_vat_source'] then
    raise exception 'v_product_margin columns are %', cols;
  end if;
  -- the computed GP is net - COGS on every costed row (the report's sign is never used)
  select count(*) into n from v_product_margin
   where cogs_bhd is not null and net_amount_bhd is not null
     and gp_computed_bhd <> round((net_amount_bhd - cogs_bhd)::numeric, 3);
  if n <> 0 then
    raise exception 'gp_computed_bhd disagrees with net - cogs on % rows', n;
  end if;
  -- a loss item is flagged: UK03 20W Charger (the case the audit named) when present
  select * into uk03 from v_product_margin
   where item_name like 'UK03 20W Charger (USB%' and cogs_bhd is not null and net_ex_vat_bhd is not null
   order by item_name limit 1;
  if found and uk03.net_ex_vat_bhd < uk03.cogs_bhd and not uk03.is_below_cost then
    raise exception 'UK03 20W Charger sells below cost (ex-VAT % vs COGS %) but is_below_cost is false',
      uk03.net_ex_vat_bhd, uk03.cogs_bhd;
  end if;
  select count(*) into n from v_product_margin where is_below_cost and gp_ex_vat_bhd >= 0;
  if n <> 0 then
    raise exception 'is_below_cost set on % rows with a non-negative ex-VAT GP', n;
  end if;
  -- every SKU with a real MRN cost is costed from the MRN, never the fallback
  select count(*) into n
    from v_product_economics e
   where e.cost_source <> 'mrn'
     and exists (select 1 from mrn_landed_costs m where upper(m.sku_code) = upper(e.sku_code)
                                                    and coalesce(m.landed_cost_bhd, 0) > 0);
  if n <> 0 then
    raise exception 'v_product_economics uses the fallback on % SKUs that have an MRN cost', n;
  end if;
  -- reserved stock is never negative and available is on-hand minus reserved
  select count(*) into n from v_catalog_reserved where reserved < 0 or available <> on_hand - reserved;
  if n <> 0 then
    raise exception 'v_catalog_reserved arithmetic off on % rows', n;
  end if;
  if exists (select 1 from information_schema.role_table_grants
              where table_schema = 'public'
                and table_name in ('v_product_economics', 'v_product_margin', 'v_catalog_reserved', 'ar_ageing_totals')
                and grantee in ('anon', 'authenticated')) then
    raise exception 'economics v2 objects must not be granted to anon/authenticated';
  end if;
end $$;
