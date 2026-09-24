-- v_catalog_velocity v2: evidence for honest badges (24-Sep-2026, trust plan M6 / D2). Additive, idempotent.
--   Rehearse: python -m scripts.apply_sql scripts/catalog_velocity_v2_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/catalog_velocity_v2_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/catalog_velocity_v2_reverse.sql
--   After:    shop.invalidate() (or wait for the 60 s catalog cache).
--
-- Why: "Best seller", "Selling fast" and the social-proof line were driven by units alone, and
-- customers_30d counted every customer NAME -- the cash counter and the two outlets included.
-- One shop restocking 200 units looked like a market movement. The view now also carries how
-- many invoices and how many NAMED B2B shops (channel = 'B2B', not the cash customer) bought the
-- item in 30 / 90 days. app/shop.py reads them through _velocity_extra(): the badge floors
-- (shop_best_seller_min_invoices / _min_shops, shop_selling_fast_min_invoices) apply only once
-- these columns exist, so deploying the code before this migration changes nothing.
--
-- Column order is unchanged (item_code, sold_30d, prev_30d, sold_90d, invoices_90d, customers_30d,
-- last_sold); shops_30d and shops_90d are APPENDED, which is the only change CREATE OR REPLACE
-- VIEW allows. Windows still anchor to MAX(sale_date), never CURRENT_DATE (same rule as before).
-- Supersedes the definition in shop_migration.sql (8d).

create or replace view v_catalog_velocity as
with mx as (select max(sale_date) as d from v_sales)
select v.sku_code as item_code,
       sum(case when v.sale_date >  mx.d - 30 then v.quantity else 0 end)                              as sold_30d,
       sum(case when v.sale_date >  mx.d - 60 and v.sale_date <= mx.d - 30 then v.quantity else 0 end) as prev_30d,
       sum(case when v.sale_date >  mx.d - 90 then v.quantity else 0 end)                              as sold_90d,
       count(distinct v.invoice_no)    filter (where v.sale_date > mx.d - 90)                          as invoices_90d,
       count(distinct v.customer_name) filter (where v.sale_date > mx.d - 30)                          as customers_30d,
       max(v.sale_date)                                                                                as last_sold,
       count(distinct v.customer_name) filter (where v.sale_date > mx.d - 30
                                                 and v.channel = 'B2B'
                                                 and coalesce(v.is_cash_customer, false) = false)      as shops_30d,
       count(distinct v.customer_name) filter (where v.sale_date > mx.d - 90
                                                 and v.channel = 'B2B'
                                                 and coalesce(v.is_cash_customer, false) = false)      as shops_90d
from v_sales v cross join mx
where v.sku_code is not null and coalesce(v.quantity, 0) > 0 and coalesce(v.is_giveaway, false) = false
group by v.sku_code;

comment on view v_catalog_velocity is
  'Per catalog code from the sales day book, windows anchored to MAX(sale_date): units (30/60/90 d), '
  'invoices and customer names, plus shops_30d / shops_90d = distinct NAMED B2B shops (no cash counter, '
  'no outlets). Feeds the marketplace badges and social proof (app/shop.py).';

-- the view runs as its owner; keep it away from the publishable keys, keep the assistant's read path
revoke all on v_catalog_velocity from anon, authenticated;
grant select on v_catalog_velocity to yq_readonly;

-- ── settings the badges read (mirrored in app/shop.py SETTING_DEFAULTS; the DB value wins) ──
insert into app_settings (key, value, description) values
  ('shop_clearance_min_age_days',    '180', 'Shop: no Clearance badge on a line younger than this many days - age from its first MA_base price date, else its first sale, else catalog_items.created_at; a new line has had no time to sell'),
  ('shop_best_seller_min_invoices',  '10',  'Shop: Best seller needs at least this many invoices in 90 days (v_catalog_velocity.invoices_90d)'),
  ('shop_best_seller_min_shops',     '5',   'Shop: Best seller needs at least this many named B2B shops in 90 days (v_catalog_velocity.shops_90d)'),
  ('shop_selling_fast_min_invoices', '10',  'Shop: Selling fast needs at least this many invoices in 90 days (v_catalog_velocity.invoices_90d)')
on conflict (key) do nothing;

-- ── self-check ────────────────────────────────────────────────────────────────
do $$
declare
  cols     text[];
  n_bad    int;
  n_keys   int;
  n_grants int;
begin
  select array_agg(column_name::text order by ordinal_position) into cols
    from information_schema.columns
   where table_schema = 'public' and table_name = 'v_catalog_velocity';
  if cols is null or cols <> array['item_code', 'sold_30d', 'prev_30d', 'sold_90d', 'invoices_90d',
                                   'customers_30d', 'last_sold', 'shops_30d', 'shops_90d'] then
    raise exception 'v_catalog_velocity columns are %, expected the 7 original columns then shops_30d, shops_90d', cols;
  end if;

  -- named shops are a subset of customer names, so the new counts can never exceed the old
  select count(*) into n_bad from v_catalog_velocity
   where shops_30d > customers_30d or shops_90d < shops_30d;
  if n_bad <> 0 then
    raise exception '% velocity rows have shops_30d > customers_30d or shops_90d < shops_30d', n_bad;
  end if;

  select count(*) into n_keys from app_settings
   where key in ('shop_clearance_min_age_days', 'shop_best_seller_min_invoices',
                 'shop_best_seller_min_shops', 'shop_selling_fast_min_invoices');
  if n_keys <> 4 then
    raise exception 'expected the 4 badge settings in app_settings, found %', n_keys;
  end if;

  select count(*) into n_grants from information_schema.role_table_grants
   where table_schema = 'public' and table_name in ('v_catalog_velocity', 'app_settings')
     and grantee in ('anon', 'authenticated');
  if n_grants <> 0 then
    raise exception 'v_catalog_velocity / app_settings must not be granted to anon or authenticated (% grants)', n_grants;
  end if;
  if not exists (select 1 from information_schema.role_table_grants
                  where table_schema = 'public' and table_name = 'v_catalog_velocity'
                    and grantee = 'yq_readonly' and privilege_type = 'SELECT') then
    raise exception 'yq_readonly lost SELECT on v_catalog_velocity';
  end if;

  raise notice 'catalog_velocity_v2: shops_30d / shops_90d appended, 4 badge settings seeded, grants clean';
end $$;
