-- Price Tracker — one row per SKU: selling price (now vs before) + purchase cost
-- (now vs before) + margin then/now, tagged with brand/division/category so the
-- owner can filter "VFAN accessories" vs anything else. Fed automatically by every
-- price-book / MRN / PO upload — no manual upkeep.
-- Idempotent; safe to re-run.

drop view if exists v_price_tracker;
create view v_price_tracker as
with sell_now as (
  select sku_code, item_name, price_bhd as sell_now
  from v_price_list
),
sell_hist as (
  -- 2nd-newest DISTINCT price per SKU = the "before" selling price
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
cost as (
  -- purchase cost now vs before, keyed by the leading code token of the stock name
  select split_part(item_name, ' ', 1) as code,
         max(current_cost_bhd) as cost_now,
         max(prev_cost_bhd)    as cost_prev,
         max(last_bought_on)   as cost_changed_on
  from v_cost_change
  group by 1
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
  c.cost_now,
  c.cost_prev,
  c.cost_changed_on,
  case when c.cost_prev > 0
       then round((c.cost_now - c.cost_prev) / c.cost_prev * 100, 1) end   as cost_change_pct,
  case when s.sell_now > 0 and c.cost_now is not null
       then round((s.sell_now - c.cost_now) / s.sell_now * 100, 1) end     as margin_now_pct,
  case when sh.sell_prev > 0 and c.cost_prev is not null
       then round((sh.sell_prev - c.cost_prev) / sh.sell_prev * 100, 1) end as margin_before_pct
from sell_now s
left join sell_hist sh on sh.sku_code = s.sku_code
left join cost c        on c.code     = s.sku_code
left join catalog_items ci on ci.item_code = s.sku_code;

grant select on v_price_tracker to yq_readonly;
