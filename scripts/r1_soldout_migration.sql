-- Release R1, the sold-out rule (24-Sep-2026, plan §9 Step 1). Additive, idempotent.
--   Rehearse: python -m scripts.apply_sql scripts/r1_soldout_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/r1_soldout_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/r1_soldout_reverse.sql
--
-- Why: a sold-out line must stay on the shelf (same URL, same record, labelled "Sold out", after
-- every available line) and, for MERCHANTS, must not become a backorder by accident. app/shop.py
-- price_cart reads two switches: shop_allow_backorder for the merchant paths (the marketplace and
-- the legacy token links) and — new here — shop_allow_backorder_staff for the salesman/staff path
-- (/shop/*), so the office can keep ordering a sold-out line in for a shop while merchants see
-- "Tell me when back" instead of Add.
--
-- This file only SEEDS the new key at its code default ('1'); it changes no behaviour. The release
-- step that changes behaviour is the flip of the existing merchant switch, done from the admin
-- Settings page (or the one statement below), and reversible the same way:
--   update app_settings set value = '0', updated_by = 'release R1', updated_at = now()
--    where key = 'shop_allow_backorder';           -- merchants: no backorder → "Tell me when back"

-- "Sold out" is a VERIFIED zero: the Focus "Stock balance by warehouse" report omits zero-balance
-- items (checked read-only 24-Sep-2026: 0 rows with net_qty <= 0 across the 13 stock_balance
-- snapshots since 2026-06-01), so a SKU absent from the latest snapshot has none. That reading is
-- only as good as the snapshot is recent: past shop_stock_fresh_days the status still shows, with
-- the snapshot date beside it (app/shop.py stock_snapshot / sold_out_reason). Code default '3'.
insert into app_settings (key, value, description) values
  ('shop_allow_backorder_staff', '1', 'Shop: 1 = a salesman/staff order (/shop/*) may include a sold-out line as a backorder, whatever shop_allow_backorder says for merchants'),
  ('shop_stock_fresh_days', '3', 'Shop: a stock snapshot older than this many days is stale — "Sold out" still shows, dated with the snapshot day')
on conflict (key) do nothing;

do $$
begin
  if not exists (select 1 from app_settings where key = 'shop_allow_backorder_staff') then
    raise exception 'shop_allow_backorder_staff was not seeded';
  end if;
  if not exists (select 1 from app_settings where key = 'shop_stock_fresh_days') then
    raise exception 'shop_stock_fresh_days was not seeded';
  end if;
  if not exists (select 1 from app_settings where key = 'shop_allow_backorder') then
    raise exception 'shop_allow_backorder (the merchant switch) is missing — shop_migration.sql was never applied';
  end if;
  if exists (select 1 from information_schema.role_table_grants
             where table_schema = 'public' and table_name = 'app_settings' and grantee in ('anon', 'authenticated')) then
    raise exception 'app_settings must not be granted to anon/authenticated';
  end if;
end $$;
