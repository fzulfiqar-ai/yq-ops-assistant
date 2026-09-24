-- Reverse of economics_v2_migration.sql: the two views go back to their previous definitions
-- (price_list_migration.sql / selling_prices_void_migration.sql), v_catalog_reserved is dropped and
-- ar_ageing_totals removed. CREATE OR REPLACE cannot remove columns, so the two views are dropped
-- and re-created; nothing depends on either (pg_depend checked on 24-Sep-2026). Take
-- `python -m scripts.db_backup --tables ar_ageing_totals` FIRST if the stored Focus totals matter.
--   python -m scripts.apply_sql scripts/economics_v2_reverse.sql ; python -m scripts.audit_grants

drop view if exists v_catalog_reserved;

drop view if exists v_product_economics;
create view v_product_economics as
select
  pl.sku_code,
  pl.item_name,
  pl.price_bhd,
  pc.landed_cost_bhd                                   as cost_bhd,
  round((pl.price_bhd - pc.landed_cost_bhd)::numeric, 3) as margin_bhd,
  case when pl.price_bhd > 0 and pc.landed_cost_bhd is not null
       then round(100.0 * (pl.price_bhd - pc.landed_cost_bhd) / pl.price_bhd, 1)
  end                                                  as margin_pct
from v_price_list pl
left join lateral (
  select landed_cost_bhd
  from purchase_costs pc
  where pc.sku_code = pl.sku_code
  order by effective_date desc nulls last
  limit 1
) pc on true;

drop view if exists v_product_margin;
create view v_product_margin as
select
  pp.item_name, pp.report_date, pp.gross_bhd, pp.discount_pct, pp.net_amount_bhd, pp.cogs_bhd,
  pp.gross_profit_bhd, pp.gp_margin_pct, pp.misc_charges_bhd, pp.net_profit_bhd, pp.np_margin_pct,
  p.sku_code,
  p.item_name as product_name,
  cat.name    as category_name,
  sp.rate_bhd as list_price_bhd
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
where pp.report_date = (select max(report_date) from product_profitability);

drop table if exists ar_ageing_totals;

delete from app_settings where key = 'alias_autofill_exclude';   -- scripts/alias_autofill.py falls back to its default list

revoke all on v_product_economics, v_product_margin from anon, authenticated;
grant select on v_product_economics, v_product_margin to yq_readonly;

do $$
declare
  cols text[];
begin
  select array_agg(column_name::text order by ordinal_position) into cols
    from information_schema.columns where table_schema = 'public' and table_name = 'v_product_economics';
  if cols <> array['sku_code', 'item_name', 'price_bhd', 'cost_bhd', 'margin_bhd', 'margin_pct'] then
    raise exception 'v_product_economics columns are %, expected the 6 original columns', cols;
  end if;
  select array_agg(column_name::text order by ordinal_position) into cols
    from information_schema.columns where table_schema = 'public' and table_name = 'v_product_margin';
  if array_length(cols, 1) <> 15 then
    raise exception 'v_product_margin has % columns, expected the 15 original ones', array_length(cols, 1);
  end if;
  if to_regclass('public.v_catalog_reserved') is not null or to_regclass('public.ar_ageing_totals') is not null then
    raise exception 'v_catalog_reserved / ar_ageing_totals still present';
  end if;
  if exists (select 1 from information_schema.role_table_grants
              where table_schema = 'public' and table_name in ('v_product_economics', 'v_product_margin')
                and grantee in ('anon', 'authenticated')) then
    raise exception 'views must not be granted to anon/authenticated';
  end if;
end $$;
