-- Price-book natural key repair (06-Sep-2026).
--
-- THE BUG. scripts/migrate_supabase.py declares:
--     unique (sku_code, price_book, customer_code, start_date)
-- but `customer_code` is NULL on 100% of rows in BOTH price books (measured: 0 of 655 MA_base
-- and 0 of 167 modern_trade rows carry a customer code). Under Postgres' default NULLS
-- DISTINCT, a unique index never matches when any key column is NULL — so the index constrains
-- nothing, the `ON CONFLICT (...)` in scripts/load_supabase.py never fires, and every re-ingest
-- of the price book APPENDS a full duplicate copy (655 + 167 rows per load) instead of upserting.
--
-- `warehouse_name` was also missing from the key, yet it is exactly what distinguishes the three
-- layers inside the MA_base book (blank = dealer/list, 'Causeway' and 'YQ Roadshow' = outlet
-- retail). Without it those three legitimately-different prices collide on the same key.
--
-- THE FIX. Add warehouse_name to the key and declare NULLS NOT DISTINCT (PG 15+; this project
-- runs PG 17), so NULL customer_code and the blank-warehouse dealer rows still deduplicate.
-- NULL is deliberately PRESERVED as the marker for the dealer/list row — scripts/price_list_
-- migration.sql orders by `(warehouse_name IS NULL) DESC` and v_price_change filters on it.
-- Do NOT "tidy" these NULLs into empty strings; those views depend on them.
--
-- ⚠️ STEP 1 DELETES ROWS. Re-runnable, but take a backup first and read the counts it prints.
-- Apply with:  python -m scripts.apply_sql scripts/pricebook_key_migration.sql

-- ── 0. Report the damage before touching anything ────────────────────────────
do $$
declare total bigint; dupes bigint;
begin
  select count(*) into total from selling_prices;
  select count(*) - count(distinct (sku_code, price_book, customer_code, warehouse_name, start_date))
    into dupes from selling_prices;
  raise notice 'selling_prices: % rows, % duplicate rows on the natural key', total, dupes;
end $$;

-- ── 1. Collapse duplicates, keeping the newest row per natural key ───────────
delete from selling_prices a
using selling_prices b
where a.id < b.id
  and a.sku_code       is not distinct from b.sku_code
  and a.price_book     is not distinct from b.price_book
  and a.customer_code  is not distinct from b.customer_code
  and a.warehouse_name is not distinct from b.warehouse_name
  and a.start_date     is not distinct from b.start_date;

-- ── 2. Drop the old, unenforceable constraint (whatever Postgres named it) ────
do $$
declare c text;
begin
  for c in
    select conname from pg_constraint
    where conrelid = 'selling_prices'::regclass and contype = 'u'
  loop
    execute format('alter table selling_prices drop constraint %I', c);
    raise notice 'dropped unique constraint %', c;
  end loop;
end $$;

drop index if exists selling_prices_natural_key;

-- ── 3. The key that actually holds ───────────────────────────────────────────
create unique index selling_prices_natural_key
  on selling_prices (sku_code, price_book, customer_code, warehouse_name, start_date)
  nulls not distinct;

do $$
begin
  raise notice 'selling_prices now carries % rows under the repaired key',
    (select count(*) from selling_prices);
end $$;
