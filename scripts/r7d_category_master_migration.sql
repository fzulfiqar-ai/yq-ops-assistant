-- Category master — release R7d "Profitability + master data" (plan §24 r7_master_data, §28, 27-Sep-2026).
-- Additive, idempotent, reviewed data migration (every product it re-points is logged first).
--   Dry run:  the SELECT under "Dry run" below (read-only; paste it into the SQL editor)
--   Rehearse: python -m scripts.apply_sql scripts/r7d_category_master_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/r7d_category_master_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/r7d_category_master_reverse.sql
--
-- Why: every analytics category (v_sales.category_name, v_product_margin.category_name,
-- v_sales_by_category, the division of v_item_division) comes from products.category_id, which the
-- Focus item-group report (scripts/category_backfill.py) fills. Those groups are wrong for 18 SKUs
-- (power banks filed under Cable, earbuds and cables under Car Charger) and empty for 40. The
-- marketplace list — catalog_items.category, which the owner curates and app/catalog.py
-- classify_category() keeps clean — is right. So the catalog decides:
--
--   category_master      catalog category → analytics category (a categories row). One row per
--                        marketplace category; the analytics names stay the ones every report
--                        already shows (BLUETOOTH HEADSET → Wireless HFs, EARPHONE → Headphones, …).
--   category_master_log  one row per product re-pointed (old and new category_id, when): the
--                        reverse restores from it, and scripts/category_backfill.py writes to it too.
--   products.category_id re-pointed to the master's category for every product whose code is a
--                        catalog item, where the two differ. Only Accessories move: a product in a
--                        SIM / Devices / Giveaway category is never touched, and the target must be an
--                        Accessories category, so no invoice changes division (v_sales.division).
--
-- scripts/category_backfill.py (the Focus item-group refresh) applies the same master after its own
-- pass once this table exists, so the next Multi_level report cannot put the Focus groups back.
--
-- Preview (read-only, 27-Sep-2026): 186 products, 146 with a category, 40 without; every one of the
-- 186 codes is a catalog item. 58 products move:
--   40 uncategorised → CABLE 20 (TB-D1, TB-D10, TB-D12, X01 UM, X02-M, X05 UC, X22 CC 1Mtr, X24 CC 1Mtr,
--      X24 CL 1Mtr, X26-C, X26-L, X27-C, X27-L, X31 CC 1 Mtr, X31 TC 1 Mtr, X32 CC 1 Mtr, X32 CL 1 Mtr,
--      X33 CCC 1.2 Mtr, X33 CCL 1.2 Mtr, X34 CC 1 Mtr), POWER BANK 6 (F06, F07, F10, F14, F17, F20),
--      BLUETOOTH HEADSET 5 (BE01, T13, T14, T17, T18), EARPHONE 3 (M09, M13, M22), CAR CHARGER 2
--      (C10, C11), CHARGER 2 (UK07, W01), BLUETOOTH SPEAKER 1 (BS08), MISCELLANEOUS 1
--      (Big Product Display - VF-ZSG01);
--   18 in the wrong Focus group: Cable → Power Bank 4 (F04, F16, F25, K105); Car Charger → Cable 10
--      (P04 2mtr, X05-L, X16, X22 CL, X24 CC, X24 CL, X27 CC, X29 CC, X29 CL, X30 CC); Car Charger →
--      Wireless HFs 3 (T02, T10, T16); Car Charger → Charger 1 (UK10 C).
-- The 40 uncategorised products' 325 day-book lines (BHD 1,954.610 ex-VAT) already count as
-- Accessories (v_sales falls back to Accessories), so no division total, target or kickback moves.
--
-- Dry run (read-only — what this file would re-point; run it before applying):
--   select p.sku_code, cur.name as focus_group, ci.category as catalog_category, cat.name as new_category
--   from products p
--   join catalog_items ci on upper(ci.item_code) = upper(p.sku_code)
--   join (values ('CABLE','Cable'), ('CHARGER','Charger'), ('CAR CHARGER','Car Charger'),
--                ('POWER BANK','Power Bank'), ('EARPHONE','Headphones'), ('BLUETOOTH HEADSET','Wireless HFs'),
--                ('BLUETOOTH SPEAKER','Wireless Speaker'), ('CAR ACCESSORIES','Car Accessories'),
--                ('MISCELLANEOUS','Miscellaneous')) m(catalog_category, category_name)
--     on m.catalog_category = ci.category
--   join categories cat on cat.name = m.category_name
--   left join categories cur on cur.id = p.category_id
--   where p.category_id is distinct from cat.id
--     and coalesce(cur.division, 'Accessories') = 'Accessories' and cat.division = 'Accessories'
--     and coalesce(ci.division, 'Accessories') = 'Accessories'
--   order by 3, 1;
--
-- Nothing is deleted. No order, line, event, customer or statement row is written: products is master
-- data, and every change to it is in category_master_log.

do $$
begin
  if to_regclass('public.catalog_items') is null or to_regclass('public.categories') is null
     or to_regclass('public.products') is null then
    raise exception 'r7d_category_master: catalog_items / categories / products missing';
  end if;
end $$;

-- ── 1. the master: marketplace category → analytics category ──────────────────────────────────
create table if not exists category_master (
  catalog_category text        primary key,
  category_id      bigint      not null references categories(id),
  note             text,
  created_at       timestamptz not null default now()
);
alter table category_master enable row level security;
revoke all on category_master from anon, authenticated;
comment on table category_master is
  'R7d: the marketplace category (catalog_items.category) each analytics category follows. products.category_id is re-pointed to category_id for every catalog code (Accessories only); scripts/category_backfill.py applies it after the Focus item-group pass.';

insert into category_master (catalog_category, category_id, note)
select m.catalog_category, c.id, m.note
from (values
  ('CABLE',             'Cable',            'data / charging cables and converters'),
  ('CHARGER',           'Charger',          'wall chargers and adapters'),
  ('CAR CHARGER',       'Car Charger',      'in-car chargers'),
  ('POWER BANK',        'Power Bank',       'Focus files some of these under Cable'),
  ('EARPHONE',          'Headphones',       'wired in-ear'),
  ('BLUETOOTH HEADSET', 'Wireless HFs',     'airpods, TWS buds, neckbands'),
  ('BLUETOOTH SPEAKER', 'Wireless Speaker', 'speakers'),
  ('CAR ACCESSORIES',   'Car Accessories',  'holders and mounts'),
  ('MISCELLANEOUS',     'Miscellaneous',    'display stands and the rest')
) as m(catalog_category, category_name, note)
join categories c on c.name = m.category_name and c.division = 'Accessories'
on conflict (catalog_category) do nothing;

-- ── 2. the log every re-point is written to first ─────────────────────────────────────────────
create table if not exists category_master_log (
  id               bigint      generated always as identity primary key,
  product_id       bigint      not null,
  sku_code         text,
  old_category_id  bigint,
  new_category_id  bigint      not null,
  source           text        not null default 'r7d_category_master_migration',
  applied_at       timestamptz not null default now()
);
create index if not exists category_master_log_product_idx on category_master_log (product_id, id);
alter table category_master_log enable row level security;
revoke all on category_master_log from anon, authenticated;
comment on table category_master_log is
  'R7d: one row per products.category_id change made by the category master (the migration or scripts/category_backfill.py). The reverse restores old_category_id from the first row per product.';

-- ── 3. re-point: log first, then update, in one statement ─────────────────────────────────────
with plan as (
  select distinct on (p.id)
         p.id as product_id, p.sku_code, p.category_id as old_category_id, cm.category_id as new_category_id
  from products p
  join catalog_items ci   on upper(ci.item_code) = upper(p.sku_code)
  join category_master cm on cm.catalog_category = ci.category
  join categories cat     on cat.id = cm.category_id
  left join categories cur on cur.id = p.category_id
  where p.category_id is distinct from cm.category_id
    and coalesce(cur.division, 'Accessories') = 'Accessories'
    and cat.division = 'Accessories'
    and coalesce(ci.division, 'Accessories') = 'Accessories'
  order by p.id, ci.id
),
logged as (
  insert into category_master_log (product_id, sku_code, old_category_id, new_category_id)
  select product_id, sku_code, old_category_id, new_category_id from plan
  returning product_id, new_category_id
)
update products p
   set category_id = l.new_category_id, updated_at = now()
  from logged l
 where p.id = l.product_id;

-- ── 4. checks ─────────────────────────────────────────────────────────────────────────────────
do $$
declare
  n_master int;
  n_left   int;
begin
  select count(*) into n_master from category_master;
  if n_master = 0 then
    raise exception 'r7d_category_master: no master row was seeded (categories names changed?)';
  end if;
  -- nothing mapped is left in a different Accessories category
  select count(*) into n_left
  from products p
  join catalog_items ci   on upper(ci.item_code) = upper(p.sku_code)
  join category_master cm on cm.catalog_category = ci.category
  join categories cat     on cat.id = cm.category_id
  left join categories cur on cur.id = p.category_id
  where p.category_id is distinct from cm.category_id
    and coalesce(cur.division, 'Accessories') = 'Accessories' and cat.division = 'Accessories'
    and coalesce(ci.division, 'Accessories') = 'Accessories';
  if n_left > 0 then
    raise exception 'r7d_category_master: % mapped products still differ from the master', n_left;
  end if;
  if exists (select 1 from information_schema.role_table_grants
              where table_schema = 'public' and table_name in ('category_master', 'category_master_log')
                and grantee in ('anon', 'authenticated')) then
    raise exception 'r7d_category_master: a master table is granted to anon/authenticated';
  end if;
  raise notice 'r7d_category_master: ok (% master rows)', n_master;
end $$;
