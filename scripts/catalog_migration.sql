-- Catalog / Item Master — the in-platform version of the Catelog.xlsx the owner
-- shares with salesmen: item + spec + 3 price tiers + product & package photos,
-- grouped by category. Photos live in the public 'catalog' storage bucket.
-- standard_rate is NOT stored — v_catalog reads it live from the price book
-- (selling_prices MA_base), so a price-book upload updates the catalog instantly.
-- Idempotent; safe to re-run.

create table if not exists catalog_items (
  id                 bigint generated always as identity primary key,
  item_code          text not null unique,
  display_name       text,
  spec               text,
  category           text,
  brand              text default 'VFAN',
  division           text default 'Accessories',
  dealer_price       numeric,
  roadshow_price     numeric,
  rrp                numeric,
  product_image_url  text,
  package_image_url  text,
  sort_order         int,
  is_active          boolean default true,
  updated_by         text,
  updated_at         timestamptz default now(),
  created_at         timestamptz default now()
);
create index if not exists catalog_items_category_idx on catalog_items (category, sort_order);

alter table catalog_items enable row level security;
drop policy if exists catalog_items_read on catalog_items;
create policy catalog_items_read on catalog_items for select to authenticated using (true);

-- Live standard selling rate from the MA price book (same source order-verify uses).
create or replace view v_catalog as
select
  ci.item_code,
  ci.display_name,
  ci.spec,
  ci.category,
  ci.brand,
  ci.division,
  ci.dealer_price,
  ci.roadshow_price,
  ci.rrp,
  sp.rate_bhd            as standard_rate,
  ci.product_image_url,
  ci.package_image_url,
  ci.sort_order,
  ci.is_active,
  ci.updated_at
from catalog_items ci
left join lateral (
  select max(rate_bhd) as rate_bhd
  from selling_prices
  where price_book = 'MA_base' and warehouse_name is null and rate_bhd > 0
    and sku_code = ci.item_code
) sp on true;

grant select on catalog_items to yq_readonly;
grant select on v_catalog to yq_readonly;
