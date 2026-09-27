-- Margins on the EX-VAT price — release R7d "Profitability + master data" (plan §17, 27-Sep-2026).
-- Additive, idempotent (CREATE OR REPLACE + COMMENT only; no table, no row is written).
--   Rehearse: python -m scripts.apply_sql scripts/r7d_margin_exvat_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/r7d_margin_exvat_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/r7d_margin_exvat_reverse.sql
--   Order:    after economics_v2_migration.sql (v_product_economics' R2 body) and
--             price_tracker_migration.sql. Once applied, THIS file owns both views
--             (docs/MIGRATIONS.md): never re-run price_tracker_migration.sql after it — that file
--             DROPs and re-creates v_price_tracker with the VAT-inclusive margin and without grants.
--
-- Why: the price book is VAT-inclusive (owner's workbook; app/shop.py _floor_d says the same), but
-- v_product_economics.margin_pct / margin_bhd and v_price_tracker.margin_now_pct /
-- margin_before_pct divided the VAT-INCLUSIVE price straight into the cost, so every unit margin
-- the Price Tracker, the agents and the AI assistant read was overstated by the VAT: a 3.000 item
-- costing 1.870 read 37.7 % instead of 31.4 %. Every other margin figure (v_product_margin
-- margin_ex_vat_pct, v_landed_margin on v_sales.net_bhd, the Command Centre) is already ex-VAT.
--
-- What changes (column names, types and order are exactly the live ones — read read-only from
-- pg_attribute on 27-Sep-2026; nothing is appended, so the reverse is a plain CREATE OR REPLACE):
--   v_product_economics (9 columns): margin_bhd = price ÷ (1 + VAT) − cost; margin_pct on that
--     ex-VAT price, written (price − cost × (1 + VAT)) ÷ price — the same number with a single
--     division, so it rounds exactly like app/margin_truth.py ex_vat_margin_pct(). price_bhd, cost_bhd and the cost choice (MRN first, then purchase_costs) are
--     verbatim.
--   v_price_tracker (16 columns): margin_now_pct / margin_before_pct on sell_now / sell_prev ÷
--     (1 + VAT). Every other column and the cost chain (PO rate → MRN → supplier estimate) verbatim.
--   VAT = app_settings.shop_vat_rate (the key the marketplace prices with, 0.10 today), read with a
--     guarded cast so a malformed setting falls back to 0.10 instead of breaking the view.
-- The same formula is app/margin_truth.py ex_vat_margin_pct(): the API recomputes the tracker's
-- margins with it, so the Price Tracker page is ex-VAT whether or not this file has run.
--
-- Column COMMENTs say which figures are ex-VAT, which are at SELLING price and which Focus column
-- is not a percentage (v_product_margin.gp_margin_pct), so the SQL assistant reads the basis too.
--
-- Preview (read-only, 27-Sep-2026): v_product_economics 186 SKUs, 179 costed; the ex-VAT margin is
-- lower on every costed row (T06 at 3.000 / 1.983: 33.9 % → 27.3 %). v_price_tracker 186 rows,
-- 83 with a cost. Grants unchanged (yq_readonly SELECT; anon / authenticated none).

-- ── 1. v_product_economics: the unit margin on the ex-VAT price ───────────────────────────────
create or replace view v_product_economics as
with vat as (
  select coalesce((select case when s.value ~ '^\s*[0-9]*\.?[0-9]+\s*$' then s.value::numeric end
                     from app_settings s where s.key = 'shop_vat_rate' limit 1), 0.10) as rate
)
select
  pl.sku_code,
  pl.item_name,
  pl.price_bhd,
  c.cost_bhd,
  round((pl.price_bhd / (1 + vat.rate) - c.cost_bhd)::numeric, 3)                   as margin_bhd,
  case when pl.price_bhd > 0 and c.cost_bhd is not null
       then round(100.0 * (pl.price_bhd - c.cost_bhd * (1 + vat.rate)) / pl.price_bhd, 1) end  as margin_pct,
  c.cost_source,
  c.cost_effective_date,
  c.cost_doc_no
from v_price_list pl
cross join vat
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

-- ── 2. v_price_tracker: margins now / before on the ex-VAT selling price ──────────────────────
create or replace view v_price_tracker as
with sell_now as (
  select sku_code, item_name, price_bhd as sell_now
  from v_price_list
),
sell_hist as (
  select sku_code, price_bhd as sell_prev, effective_from as sell_changed_on
  from (
    select sku_code, price_bhd, effective_from,
           row_number() over (partition by sku_code order by effective_from desc) as rn
    from (
      select distinct on (sku_code, price_bhd) sku_code, price_bhd, effective_from
      from v_price_history
      where price_book = 'MA_base'
      order by sku_code, price_bhd, effective_from desc
    ) d
  ) h
  where rn = 2
),
po as (  -- PO rates keyed by item code (already code-level in v_po_cost_change)
  select item_code as code,
         max(current_rate_bhd) as cost_now,
         max(prev_rate_bhd)    as cost_prev,
         max(last_ordered)     as bought_on
  from v_po_cost_change
  group by 1
),
mrn as (  -- MRN landed costs keyed by leading code token of the stock name
  select split_part(item_name, ' ', 1) as code,
         max(current_cost_bhd) as cost_now,
         max(prev_cost_bhd)    as cost_prev,
         max(last_bought_on)   as bought_on
  from v_cost_change
  group by 1
),
sup as (  -- vendor PI list price (RMB) — converted with the settings chain as an ESTIMATE
  select model as code,
         max(latest_rmb) as latest_rmb
  from v_supplier_price_history
  where latest_rmb > 0
  group by 1
),
fx as (  -- owner's costing chain from app_settings (fallbacks = current defaults)
  select
    coalesce((select value::numeric from app_settings where key = 'fx_usd_bhd'), 0.37744)
      / coalesce((select value::numeric from app_settings where key = 'fx_rmb_usd'), 6.8)
      * (1 + coalesce((select value::numeric from app_settings where key = 'landing_vat_pct'), 0.30))
      as rmb_landed_factor
),
vat as (  -- R7d: the selling price is VAT-inclusive; margins are taken on price ÷ (1 + VAT)
  select coalesce((select case when s.value ~ '^\s*[0-9]*\.?[0-9]+\s*$' then s.value::numeric end
                     from app_settings s where s.key = 'shop_vat_rate' limit 1), 0.10) as rate
)
select
  s.sku_code,
  coalesce(ci.display_name, s.item_name)                        as item_name,
  coalesce(ci.brand,
           case when s.item_name ilike '%vfan%' then 'VFAN' else 'Other' end) as brand,
  coalesce(ci.division, 'Accessories')                          as division,
  coalesce(ci.category, 'OTHER')                                as category,
  s.sell_now,
  sh.sell_prev,
  sh.sell_changed_on,
  case when sh.sell_prev > 0
       then round((s.sell_now - sh.sell_prev) / sh.sell_prev * 100, 1) end as sell_change_pct,
  coalesce(po.cost_now, mrn.cost_now,
           round((sup.latest_rmb * fx.rmb_landed_factor)::numeric, 4))     as cost_now,
  coalesce(po.cost_prev, mrn.cost_prev)                                    as cost_prev,
  coalesce(po.bought_on, mrn.bought_on)                                    as last_bought_on,
  case
    when po.cost_now  is not null then 'po'
    when mrn.cost_now is not null then 'mrn'
    when sup.latest_rmb is not null then 'supplier_est'
  end                                                                      as cost_source,
  case when coalesce(po.cost_prev, mrn.cost_prev) > 0
       then round((coalesce(po.cost_now, mrn.cost_now) - coalesce(po.cost_prev, mrn.cost_prev))
                  / coalesce(po.cost_prev, mrn.cost_prev) * 100, 1) end    as cost_change_pct,
  case when s.sell_now > 0
            and coalesce(po.cost_now, mrn.cost_now, sup.latest_rmb * fx.rmb_landed_factor) is not null
       then round((s.sell_now
                   - coalesce(po.cost_now, mrn.cost_now, round((sup.latest_rmb * fx.rmb_landed_factor)::numeric, 4))
                     * (1 + vat.rate))
                  / s.sell_now * 100, 1) end                                as margin_now_pct,
  case when sh.sell_prev > 0 and coalesce(po.cost_prev, mrn.cost_prev) is not null
       then round((sh.sell_prev - coalesce(po.cost_prev, mrn.cost_prev) * (1 + vat.rate))
                  / sh.sell_prev * 100, 1) end                              as margin_before_pct
from sell_now s
cross join fx
cross join vat
left join sell_hist sh on sh.sku_code = s.sku_code
left join po  on po.code  = s.sku_code
left join mrn on mrn.code = s.sku_code
left join sup on sup.code = s.sku_code
left join catalog_items ci on ci.item_code = s.sku_code;

-- ── 3. grants: unchanged, restated so a re-run can never widen them ──────────────────────────
revoke all on v_product_economics, v_price_tracker from anon, authenticated;
grant select on v_product_economics, v_price_tracker to yq_readonly;

-- ── 4. the basis of every margin and stock-value column, where the SQL assistant reads it ────
comment on column v_product_economics.price_bhd is
  'Current book selling price per unit, VAT-INCLUSIVE (the price book).';
comment on column v_product_economics.margin_bhd is
  'Unit gross margin in BHD on the EX-VAT price: price_bhd / (1 + app_settings.shop_vat_rate) - cost_bhd (R7d).';
comment on column v_product_economics.margin_pct is
  'Unit gross margin % on the EX-VAT price: (price_bhd / (1 + shop_vat_rate) - cost_bhd) / (price_bhd / (1 + shop_vat_rate)) x 100, computed as (price_bhd - cost_bhd x (1 + shop_vat_rate)) / price_bhd x 100 (R7d).';
comment on column v_price_tracker.sell_now is 'Current book selling price, VAT-INCLUSIVE.';
comment on column v_price_tracker.sell_prev is 'Previous book selling price, VAT-INCLUSIVE.';
comment on column v_price_tracker.margin_now_pct is
  'Margin % now on the EX-VAT selling price: (sell_now / (1 + shop_vat_rate) - cost_now) / (sell_now / (1 + shop_vat_rate)) x 100 (R7d).';
comment on column v_price_tracker.margin_before_pct is
  'Margin % before, on the EX-VAT previous price: (sell_prev / (1 + shop_vat_rate) - cost_prev) / (sell_prev / (1 + shop_vat_rate)) x 100 (R7d).';
comment on column v_product_margin.gp_margin_pct is
  'Focus''s own "GP Margin %" column. NOT a percentage (median 4,835) and not ex-VAT: use margin_ex_vat_pct.';
comment on column v_product_margin.gross_profit_bhd is
  'Focus''s own "Gross Profit" column: loses the minus sign on loss items. Use gp_ex_vat_bhd.';
comment on column v_product_margin.margin_ex_vat_pct is
  'THE official gross margin % per item: ex-VAT sales vs Focus COGS on the latest profitability report.';
comment on column v_landed_margin.gp_margin_pct is
  'Gross margin % on ex-VAT sales (v_sales.net_bhd) vs the MRN landed cost; covers items with a receipt only.';
comment on column v_stock_health.stock_value is
  'Stock value at SELLING price (Focus selling rate x qty), not at cost.';
comment on column v_inventory_aging.stock_value is
  'Stock value at SELLING price (Focus selling rate x qty), not at cost.';
comment on column v_division_summary.stock_value_bhd is
  'Stock value at SELLING price (Focus selling rate x qty), not at cost.';

-- ── 5. checks ─────────────────────────────────────────────────────────────────────────────────
do $$
declare
  n int;
begin
  select count(*) into n from pg_attribute
   where attrelid = 'public.v_product_economics'::regclass and attnum > 0 and not attisdropped;
  if n <> 9 then
    raise exception 'r7d_margin_exvat: v_product_economics has % columns, expected 9', n;
  end if;
  select count(*) into n from pg_attribute
   where attrelid = 'public.v_price_tracker'::regclass and attnum > 0 and not attisdropped;
  if n <> 16 then
    raise exception 'r7d_margin_exvat: v_price_tracker has % columns, expected 16', n;
  end if;
  if exists (select 1 from information_schema.role_table_grants
              where table_schema = 'public' and table_name in ('v_product_economics', 'v_price_tracker')
                and grantee in ('anon', 'authenticated')) then
    raise exception 'r7d_margin_exvat: a margin view is granted to anon/authenticated';
  end if;
  if not has_table_privilege('yq_readonly', 'public.v_product_economics', 'select')
     or not has_table_privilege('yq_readonly', 'public.v_price_tracker', 'select') then
    raise exception 'r7d_margin_exvat: yq_readonly lost SELECT on a margin view';
  end if;
  raise notice 'r7d_margin_exvat: ok';
end $$;
