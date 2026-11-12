-- Reverse of scripts/r12_b2c_causeway_migration.sql (release r12 "WEKOME live", 12-Nov-2026). Idempotent.
--   Rehearse: python -m scripts.apply_sql scripts/r12_b2c_causeway_reverse.sql --rehearse     (runs it, then rolls back)
--   Apply:    python -m scripts.apply_sql scripts/r12_b2c_causeway_reverse.sql ; python -m scripts.audit_grants
-- From the MAIN project folder only.
--
-- What it puts back: v_catalog as daily_ops2_migration.sql has it, that file's statement verbatim: b2c_rate is again
-- the modern_trade book's current price (v_price_list_by_book), standard_rate the MA_base one, as before r12.
-- CREATE OR REPLACE with the same 17 columns (names, types, order), so every reader keeps working; the grants are
-- restated unchanged (SELECT for yq_readonly, nothing for anon / authenticated). The Causeway rows stay in
-- selling_prices, only the view stops reading them. Nothing to undo in the API; its catalog copy (app/shop.py
-- context(), 60 s) picks the old b2c_rate up by itself.
-- The new category: only the category_master row r12 seeded is deleted (SCREEN PROTECTOR -> 'Screen Protector'; a row
-- mapping it elsewhere is someone else's and stays). The 'Screen Protector' categories row stays: products may point at
-- it (scripts/category_backfill.apply_category_master() re-points them, with category_master_log). No other row is
-- written or deleted.

set lock_timeout = '2s';      -- a busy v_catalog makes the file fail fast, never queue

-- daily_ops2_migration.sql's statement, verbatim
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
  b2b.price_bhd          as standard_rate,
  ci.product_image_url,
  ci.package_image_url,
  ci.sort_order,
  ci.is_active,
  ci.updated_at,
  ci.created_at,
  b2c.price_bhd          as b2c_rate
from catalog_items ci
left join lateral (
  select price_bhd from v_price_list_by_book
  where sku_code = ci.item_code and price_book = 'MA_base' limit 1
) b2b on true
left join lateral (
  select price_bhd from v_price_list_by_book
  where sku_code = ci.item_code and price_book = 'modern_trade' limit 1
) b2c on true;

-- the grants, unchanged, restated (plain statements, never in a DO block)
revoke all on table v_catalog from anon, authenticated;
grant select on v_catalog to yq_readonly;

-- the master row r12 seeded; the 'Screen Protector' category stays (products may point at it)
delete from category_master cm
 using categories c
 where cm.catalog_category = 'SCREEN PROTECTOR'
   and c.id = cm.category_id
   and c.name = 'Screen Protector';

-- self-check: the 17 columns, the modern_trade book again (never the Causeway row), the grants, the master row gone
do $$
declare
  cols  text;
  want  constant text := 'item_code,display_name,spec,category,brand,division,dealer_price,roadshow_price,rrp,'
                         || 'standard_rate,product_image_url,package_image_url,sort_order,is_active,updated_at,'
                         || 'created_at,b2c_rate';
  def   text;
begin
  select string_agg(attname::text, ',' order by attnum) into cols
    from pg_attribute
   where attrelid = 'public.v_catalog'::regclass and attnum > 0 and not attisdropped;
  if cols is distinct from want then
    raise exception 'r12_b2c_causeway reverse: v_catalog has the columns (%), expected (%)', cols, want;
  end if;
  def := pg_get_viewdef('public.v_catalog'::regclass);
  if position('modern_trade' in def) = 0 or position('''Causeway''' in def) > 0 then
    raise exception 'r12_b2c_causeway reverse: v_catalog.b2c_rate does not read the modern_trade book again';
  end if;
  if exists (select 1 from information_schema.role_table_grants
              where table_schema = 'public' and table_name = 'v_catalog'
                and grantee in ('anon', 'authenticated')) then
    raise exception 'r12_b2c_causeway reverse: v_catalog must not be granted to anon / authenticated';
  end if;
  if exists (select 1 from pg_roles where rolname = 'yq_readonly')
     and not has_table_privilege('yq_readonly', 'public.v_catalog', 'SELECT') then
    raise exception 'r12_b2c_causeway reverse: yq_readonly lost SELECT on v_catalog';
  end if;
  if exists (select 1 from category_master cm
             join categories c on c.id = cm.category_id
             where cm.catalog_category = 'SCREEN PROTECTOR' and c.name = 'Screen Protector') then
    raise exception 'r12_b2c_causeway reverse: the category_master row SCREEN PROTECTOR -> Screen Protector is still there';
  end if;
  raise notice 'r12_b2c_causeway reverse: ok (v_catalog as daily_ops2_migration.sql has it: b2c_rate from the modern_trade book; the SCREEN PROTECTOR master row gone, the category kept)';
end $$;
