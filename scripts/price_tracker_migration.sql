-- Price Tracker v2 — one row per SKU: selling price (now vs before) + PURCHASE cost
-- (now vs before) with margins, tagged by brand/division/category.
-- v2 fixes "purchase side mostly empty": cost now comes from a COALESCE chain —
--   1. v_po_cost_change      (purchase-order rate, current vs previous — widest coverage)
--   2. v_cost_change         (MRN landed cost, current vs previous)
--   3. v_supplier_price_history (vendor PI price in RMB — flagged as an estimate)
-- cost_source tells the UI which one fed the row. Idempotent; safe to re-run.

drop view if exists v_price_tracker;
create view v_price_tracker as
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
  case when s.sell_now > 0 and coalesce(po.cost_now, mrn.cost_now,
                                        sup.latest_rmb * fx.rmb_landed_factor) is not null
       then round((s.sell_now - coalesce(po.cost_now, mrn.cost_now,
                                         round((sup.latest_rmb * fx.rmb_landed_factor)::numeric, 4)))
                  / s.sell_now * 100, 1) end                               as margin_now_pct,
  case when sh.sell_prev > 0 and coalesce(po.cost_prev, mrn.cost_prev) is not null
       then round((sh.sell_prev - coalesce(po.cost_prev, mrn.cost_prev))
                  / sh.sell_prev * 100, 1) end                             as margin_before_pct
from sell_now s
cross join fx
left join sell_hist sh on sh.sku_code = s.sku_code
left join po           on po.code     = s.sku_code
left join mrn          on mrn.code    = s.sku_code
left join sup          on sup.code    = s.sku_code
left join catalog_items ci on ci.item_code = s.sku_code;

grant select on v_price_tracker to yq_readonly;
