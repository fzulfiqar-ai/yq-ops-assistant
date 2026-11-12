-- Release r12 "WEKOME live" (12-Nov-2026): the shop (B2C) price is the item's Causeway price, and the analytics side
-- knows WEKOME's new catalog category, SCREEN PROTECTOR.
-- Idempotent: one CREATE OR REPLACE VIEW, the same grants restated, two master-data rows inserted only where missing
-- (categories, category_master), a self-check. No product is re-pointed and no other row is written.
--   Rehearse: python -m scripts.apply_sql scripts/r12_b2c_causeway_migration.sql --rehearse     (runs it, then rolls back)
--   Apply:    python -m scripts.apply_sql scripts/r12_b2c_causeway_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/r12_b2c_causeway_reverse.sql (today's definition back: b2c_rate from the modern_trade book)
-- From the MAIN project folder only (a worktree finds the main folder's .env, which is production), after the branch
-- is merged to main and pulled there, never during a Focus load. The API needs no deploy for it and reads nothing new:
-- its catalog copy (app/shop.py context(), 60 s) and the portal pick the new b2c_rate up by themselves.
--
-- WHY. v_catalog.b2c_rate is the retail (B2C) price beside the trade price standard_rate: the marketplace's retail
-- anchor and the merchant's margin strip (app/shop.py: compare_at_bhd / b2c_bhd), the B2C tier of the portal's
-- Catalog page and its exports (app/catalog.py), the chat's product cards (app/chat_extras.py). Until now it came from
-- the price book 'modern_trade' (ModernTradeSellerBook, last loaded in May 2026). The owner keeps the B2C prices in the
-- MA price book: beside each item's blank-warehouse row (the dealer / B2B price, standard_rate) sit its 'Causeway' and
-- 'YQ Roadshow' rows, the outlet prices (scripts/price_list_migration.sql, "WAREHOUSE LAYERS"). He raised 153 Causeway
-- prices on 9-Nov-2026 and the marketplace still showed May's modern-trade prices. The owner (12-Nov-2026): the shop
-- price is the item's Causeway price.
--
-- WHAT. One view, v_catalog: daily_ops2_migration.sql's statement verbatim but the b2c lateral join. From this release
-- THIS file owns v_catalog (docs/MIGRATIONS.md); re-running daily_ops2_migration.sql would quietly put modern_trade back
-- (same 17 columns).
--   b2c_rate = the item's CURRENT row of price book 'MA_base', warehouse 'Causeway', no customer (customer_code null or
--   blank, as v_product_margin reads "no customer"). "Current" is the rule v_price_list_by_book picks the B2B row with
--   (scripts/selling_prices_void_migration.sql): status 'Authorized'; a rate above 0; start_date on or before today and
--   end_date null or not before today; not voided (voided_at is null: the day-first phantom rows of the 14-Sep upload
--   and the rows of older Focus exports are voided rows, and every price view skips them); then the latest start_date,
--   then the highest id. v_price_list_by_book's first sort key, "blank warehouse first", has nothing to choose between
--   here (the warehouse is fixed) and is left out. The item code matches exactly, as standard_rate's does.
--   No current Causeway row: b2c_rate is NULL (the marketplace then shows no retail price for that item). There is no
--   fallback to modern_trade.
--   standard_rate and the other 15 columns are untouched: names, types and order exactly as before (CREATE OR REPLACE
--   refuses anything else), so app/shop.py, app/catalog.py, app/chat_extras.py, app/whatsapp.py, app/video_gen.py,
--   app/metrics.py and the scripts that read v_catalog keep working. No view is built on v_catalog (scripts/*.sql).
-- THE NEW CATEGORY. app/catalog.py files WEKOME's screen protectors under the catalog category SCREEN PROTECTOR (r12).
-- Every analytics category follows the catalog through category_master (r7d_category_master_migration.sql), which
-- has no row for it, so its products would keep whatever Focus group they carry. This file adds, only where missing
-- (ON CONFLICT DO NOTHING, as r7d seeds the master): a categories row 'Screen Protector', division Accessories, and
-- the master row SCREEN PROTECTOR -> 'Screen Protector'. No product is re-pointed here:
-- scripts/category_backfill.apply_category_master() does that (log first, Accessories only). Needs R7d's
-- category_master (the first block stops and names it otherwise).
-- GRANTS. Unchanged and restated as plain statements: SELECT for yq_readonly (catalog_migration.sql,
-- daily_ops2_migration.sql: the API's exec_sql runs as that role), nothing for anon / authenticated
-- (hotfix_view_grants_migration.sql). Never security_invoker: run_readonly_query runs as yq_readonly, which is
-- granted views, not the tables beneath.
-- SELF-CHECK. The 17 columns in order; the definition reads 'Causeway' and never modern_trade; b2c_rate equals the
-- current Causeway row picked again the way v_price_list_by_book picks one (row_number() over the book) on every
-- catalog item; standard_rate is still v_price_list_by_book's MA_base price on every item; the grants; at least one
-- active item has a Causeway price when any active item has a trade price (a Causeway layer missing or spelt otherwise
-- would blank every shop price: the file refuses instead); SCREEN PROTECTOR has its master row, on an Accessories
-- category. A notice gives the counts (active items, with and without a Causeway price, how many differ from the
-- modern_trade book), the category SCREEN PROTECTOR maps to, and the views on v_catalog (none expected).

set lock_timeout = '2s';      -- a busy v_catalog makes the file fail fast, never queue

-- 0. the category master is R7d's: stop, and name the file, when it is missing -----------------------------------------
do $$
begin
  if to_regclass('public.category_master') is null or to_regclass('public.categories') is null then
    raise exception 'r12_b2c_causeway: category_master / categories missing (apply scripts/r7d_category_master_migration.sql first)';
  end if;
end $$;

-- 1. v_catalog: daily_ops2_migration.sql's statement, the b2c lateral now the current Causeway row ---------------------
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
  b2c.rate_bhd           as b2c_rate
from catalog_items ci
left join lateral (
  select price_bhd from v_price_list_by_book
  where sku_code = ci.item_code and price_book = 'MA_base' limit 1
) b2b on true
left join lateral (
  select sp.rate_bhd
  from selling_prices sp
  where sp.sku_code = ci.item_code
    and sp.price_book = 'MA_base'
    and sp.warehouse_name = 'Causeway'
    and (sp.customer_code is null or sp.customer_code = '')
    and sp.status = 'Authorized'
    and sp.rate_bhd is not null and sp.rate_bhd > 0
    and sp.start_date <= current_date
    and (sp.end_date is null or sp.end_date >= current_date)
    and sp.voided_at is null
  order by sp.start_date desc, sp.id desc
  limit 1
) b2c on true;

-- 2. the grants, unchanged, restated so a re-run can never widen them (plain statements, never in a DO block) --------
revoke all on table v_catalog from anon, authenticated;
grant select on v_catalog to yq_readonly;

-- 3. the analytics category of SCREEN PROTECTOR (WEKOME's screen protectors), each row only where missing ----------
insert into categories (name, division) values ('Screen Protector', 'Accessories')
on conflict (name) do nothing;

insert into category_master (catalog_category, category_id, note)
select m.catalog_category, c.id, m.note
from (values
  ('SCREEN PROTECTOR',  'Screen Protector', 'screen protectors (WEKOME)')
) as m(catalog_category, category_name, note)
join categories c on c.name = m.category_name and c.division = 'Accessories'
on conflict (catalog_category) do nothing;

-- 4. self-check ------------------------------------------------------------------------------------------------------
do $$
declare
  cols      text;
  want      constant text := 'item_code,display_name,spec,category,brand,division,dealer_price,roadshow_price,rrp,'
                             || 'standard_rate,product_image_url,package_image_url,sort_order,is_active,updated_at,'
                             || 'created_at,b2c_rate';
  def       text;
  n_off     int;
  n_b2b     int;
  n_active  int;
  n_traded  int;
  n_priced  int;
  n_moved   int;
  deps      text;
  cat_name  text;
  cat_div   text;
begin
  select string_agg(attname::text, ',' order by attnum) into cols
    from pg_attribute
   where attrelid = 'public.v_catalog'::regclass and attnum > 0 and not attisdropped;
  if cols is distinct from want then
    raise exception 'r12_b2c_causeway: v_catalog has the columns (%), expected (%)', cols, want;
  end if;

  def := pg_get_viewdef('public.v_catalog'::regclass);
  if position('''Causeway''' in def) = 0 or position('modern_trade' in def) > 0 then
    raise exception 'r12_b2c_causeway: v_catalog.b2c_rate does not read the Causeway row (or still reads modern_trade)';
  end if;

  -- the current Causeway row picked again the way v_price_list_by_book picks a book's row: one row_number() pass
  with cur as (
    select sku_code, rate_bhd,
           row_number() over (partition by sku_code order by start_date desc, id desc) as rn
      from selling_prices
     where price_book = 'MA_base'
       and warehouse_name = 'Causeway'
       and (customer_code is null or customer_code = '')
       and status = 'Authorized'
       and rate_bhd is not null and rate_bhd > 0
       and start_date <= current_date
       and (end_date is null or end_date >= current_date)
       and voided_at is null
  )
  select count(*) into n_off
    from v_catalog v
    left join cur on cur.sku_code = v.item_code and cur.rn = 1
   where v.b2c_rate is distinct from cur.rate_bhd;
  if n_off <> 0 then
    raise exception 'r12_b2c_causeway: b2c_rate is not the current Causeway row on % catalog item(s)', n_off;
  end if;

  select count(*) into n_b2b
    from v_catalog v
    left join v_price_list_by_book b on b.sku_code = v.item_code and b.price_book = 'MA_base'
   where v.standard_rate is distinct from b.price_bhd;
  if n_b2b <> 0 then
    raise exception 'r12_b2c_causeway: standard_rate is no longer the MA_base price of v_price_list_by_book on % item(s)', n_b2b;
  end if;

  if exists (select 1 from information_schema.role_table_grants
              where table_schema = 'public' and table_name = 'v_catalog'
                and grantee in ('anon', 'authenticated')) then
    raise exception 'r12_b2c_causeway: v_catalog must not be granted to anon / authenticated';
  end if;
  if exists (select 1 from pg_roles where rolname = 'yq_readonly')
     and not has_table_privilege('yq_readonly', 'public.v_catalog', 'SELECT') then
    raise exception 'r12_b2c_causeway: yq_readonly lost SELECT on v_catalog';
  end if;

  select count(*), count(standard_rate), count(b2c_rate) into n_active, n_traded, n_priced
    from v_catalog where is_active;
  if n_traded > 0 and n_priced = 0 then
    raise exception 'r12_b2c_causeway: no active catalog item has a current Causeway row (% have a trade price): is the MA price book loaded with its Causeway layer, spelt ''Causeway''?', n_traded;
  end if;

  select count(*) into n_moved
    from v_catalog v
    left join v_price_list_by_book mt on mt.sku_code = v.item_code and mt.price_book = 'modern_trade'
   where v.is_active and v.b2c_rate is distinct from mt.price_bhd;

  -- SCREEN PROTECTOR has its master row (this file's, or one the owner made before), on an Accessories category
  select c.name, c.division into cat_name, cat_div
    from category_master cm
    join categories c on c.id = cm.category_id
   where cm.catalog_category = 'SCREEN PROTECTOR';
  if cat_name is null then
    select coalesce(string_agg(coalesce(division, 'no division'), ', '), 'none') into cat_div
      from categories where name = 'Screen Protector';
    raise exception 'r12_b2c_causeway: no category_master row for SCREEN PROTECTOR (the division of a ''Screen Protector'' category already there: %; the master takes an Accessories one only)', cat_div;
  end if;
  if cat_div is distinct from 'Accessories' then
    raise exception 'r12_b2c_causeway: SCREEN PROTECTOR maps to the category % (division %), expected an Accessories category', cat_name, coalesce(cat_div, 'none');
  end if;

  select string_agg(distinct c.relname::text, ', ' order by c.relname::text) into deps
    from pg_depend d
    join pg_rewrite r on r.oid = d.objid
    join pg_class c on c.oid = r.ev_class
   where d.classid = 'pg_rewrite'::regclass
     and d.refobjid = 'public.v_catalog'::regclass
     and c.oid <> 'public.v_catalog'::regclass;

  raise notice 'r12_b2c_causeway: ok; % active catalog item(s): % with a Causeway price, % without (b2c_rate NULL); b2c_rate differs from the modern_trade book on % of them; SCREEN PROTECTOR -> %; views on v_catalog: %',
    n_active, n_priced, n_active - n_priced, n_moved, cat_name, coalesce(deps, 'none');
end $$;
