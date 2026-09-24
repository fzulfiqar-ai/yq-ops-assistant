-- Reverse of catalog_velocity_v2_migration.sql: the 7-column v_catalog_velocity from
-- shop_migration.sql (8d) and the four badge settings removed. app/shop.py falls back to the
-- volume-only badge rules on its own when the columns are gone.
--   python -m scripts.apply_sql scripts/catalog_velocity_v2_reverse.sql ; python -m scripts.audit_grants
-- Nothing depends on v_catalog_velocity (checked in pg_depend on 24-Sep-2026), so a plain DROP is
-- safe; CREATE OR REPLACE cannot remove columns, which is why the view is dropped and re-created.

drop view if exists v_catalog_velocity;

create view v_catalog_velocity as
with mx as (select max(sale_date) as d from v_sales)
select v.sku_code as item_code,
       sum(case when v.sale_date >  mx.d - 30 then v.quantity else 0 end)                              as sold_30d,
       sum(case when v.sale_date >  mx.d - 60 and v.sale_date <= mx.d - 30 then v.quantity else 0 end) as prev_30d,
       sum(case when v.sale_date >  mx.d - 90 then v.quantity else 0 end)                              as sold_90d,
       count(distinct v.invoice_no)    filter (where v.sale_date > mx.d - 90)                          as invoices_90d,
       count(distinct v.customer_name) filter (where v.sale_date > mx.d - 30)                          as customers_30d,
       max(v.sale_date)                                                                                as last_sold
from v_sales v cross join mx
where v.sku_code is not null and coalesce(v.quantity, 0) > 0 and coalesce(v.is_giveaway, false) = false
group by v.sku_code;

revoke all on v_catalog_velocity from anon, authenticated;
grant select on v_catalog_velocity to yq_readonly;

delete from app_settings
 where key in ('shop_clearance_min_age_days', 'shop_best_seller_min_invoices',
               'shop_best_seller_min_shops', 'shop_selling_fast_min_invoices');

do $$
declare
  cols     text[];
  n_grants int;
begin
  select array_agg(column_name::text order by ordinal_position) into cols
    from information_schema.columns
   where table_schema = 'public' and table_name = 'v_catalog_velocity';
  if cols <> array['item_code', 'sold_30d', 'prev_30d', 'sold_90d', 'invoices_90d', 'customers_30d', 'last_sold'] then
    raise exception 'v_catalog_velocity columns are %, expected the 7 original columns', cols;
  end if;
  select count(*) into n_grants from information_schema.role_table_grants
   where table_schema = 'public' and table_name = 'v_catalog_velocity'
     and grantee in ('anon', 'authenticated');
  if n_grants <> 0 then
    raise exception 'v_catalog_velocity must not be granted to anon or authenticated (% grants)', n_grants;
  end if;
end $$;
