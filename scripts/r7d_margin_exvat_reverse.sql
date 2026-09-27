-- Reverse of scripts/r7d_margin_exvat_migration.sql (release R7d). Idempotent.
--   python -m scripts.apply_sql scripts/r7d_margin_exvat_reverse.sql
--
-- Puts back the VAT-inclusive margin formula in v_product_economics and v_price_tracker (the bodies
-- below are the live definitions read read-only with pg_get_viewdef on 27-Sep-2026: v_product_economics
-- from economics_v2_migration.sql, v_price_tracker from price_tracker_migration.sql). The migration
-- appended no column, so this is a plain CREATE OR REPLACE: no DROP, no CASCADE, grants untouched.
-- The column comments the migration added are cleared (there were none before it).
-- No table and no row is touched. The API keeps working: app/main.py recomputes the Price
-- Tracker's margins on the ex-VAT price either way (app/margin_truth.py apply_ex_vat_margins).

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
po as (
  select item_code as code,
         max(current_rate_bhd) as cost_now,
         max(prev_rate_bhd)    as cost_prev,
         max(last_ordered)     as bought_on
  from v_po_cost_change
  group by 1
),
mrn as (
  select split_part(item_name, ' ', 1) as code,
         max(current_cost_bhd) as cost_now,
         max(prev_cost_bhd)    as cost_prev,
         max(last_bought_on)   as bought_on
  from v_cost_change
  group by 1
),
sup as (
  select model as code,
         max(latest_rmb) as latest_rmb
  from v_supplier_price_history
  where latest_rmb > 0
  group by 1
),
fx as (
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
  case when s.sell_now > 0
            and coalesce(po.cost_now, mrn.cost_now, sup.latest_rmb * fx.rmb_landed_factor) is not null
       then round((s.sell_now - coalesce(po.cost_now, mrn.cost_now,
                   round((sup.latest_rmb * fx.rmb_landed_factor)::numeric, 4))) / s.sell_now * 100, 1) end
                                                                           as margin_now_pct,
  case when sh.sell_prev > 0 and coalesce(po.cost_prev, mrn.cost_prev) is not null
       then round((sh.sell_prev - coalesce(po.cost_prev, mrn.cost_prev)) / sh.sell_prev * 100, 1) end
                                                                           as margin_before_pct
from sell_now s
cross join fx
left join sell_hist sh on sh.sku_code = s.sku_code
left join po  on po.code  = s.sku_code
left join mrn on mrn.code = s.sku_code
left join sup on sup.code = s.sku_code
left join catalog_items ci on ci.item_code = s.sku_code;

revoke all on v_product_economics, v_price_tracker from anon, authenticated;
grant select on v_product_economics, v_price_tracker to yq_readonly;

comment on column v_product_economics.price_bhd is null;
comment on column v_product_economics.margin_bhd is null;
comment on column v_product_economics.margin_pct is null;
comment on column v_price_tracker.sell_now is null;
comment on column v_price_tracker.sell_prev is null;
comment on column v_price_tracker.margin_now_pct is null;
comment on column v_price_tracker.margin_before_pct is null;
comment on column v_product_margin.gp_margin_pct is null;
comment on column v_product_margin.gross_profit_bhd is null;
comment on column v_product_margin.margin_ex_vat_pct is null;
comment on column v_landed_margin.gp_margin_pct is null;
comment on column v_stock_health.stock_value is null;
comment on column v_inventory_aging.stock_value is null;
comment on column v_division_summary.stock_value_bhd is null;

do $$
begin
  if exists (select 1 from information_schema.role_table_grants
              where table_schema = 'public' and table_name in ('v_product_economics', 'v_price_tracker')
                and grantee in ('anon', 'authenticated')) then
    raise exception 'r7d_margin_exvat reverse: a margin view is granted to anon/authenticated';
  end if;
  raise notice 'r7d_margin_exvat reverse: ok';
end $$;
