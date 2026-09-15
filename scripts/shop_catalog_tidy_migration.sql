-- YQ Shop — catalog tidy-up (15-Sep-2026). Idempotent.
-- Apply:  python -m scripts.apply_sql scripts/shop_catalog_tidy_migration.sql
--
-- Owner review of the live catalog on a phone turned up three data problems:
--
--   1. Shop display stands were listed as products. They are point-of-sale furniture,
--      not something a merchant orders by the piece, so they get a catalog_items.hidden
--      flag rather than is_active = false: the price book still carries them, and the
--      auto-sync would switch is_active straight back on at the next upload.
--
--   2. 19 SKUs sat in "OTHER" and another 26 were filed under a category that had
--      nothing to do with what they are (cables under CAR CHARGER, airpods under
--      CAR CHARGER, a power bank under CABLE). The category chips are the main way a
--      merchant navigates 180 products on a phone, so a wrong chip is a lost sale.
--      Categories below come from app.catalog.classify_category() — the same function
--      that now categorises every NEW SKU the price book introduces, so OTHER cannot
--      silently refill.
--
--   3. Two near-duplicate categories (FOR CAR vs CAR CHARGER, WIRELESS HFS vs
--      BLUETOOTH HEADSET) are folded into one each.
--
-- Final vocabulary (app.catalog.CATEGORY_ORDER):
--   CABLE · CHARGER · CAR CHARGER · POWER BANK · EARPHONE · BLUETOOTH HEADSET ·
--   BLUETOOTH SPEAKER · CAR ACCESSORIES   (+ MISCELLANEOUS, hidden from the shop)

-- ── 1. hide from the shop without lying about the price book ──────────────────
alter table catalog_items add column if not exists hidden boolean not null default false;

comment on column catalog_items.hidden is
  'Owner-hidden: the SKU stays in the item master and the price book mirror, but never '
  'appears in the shop catalog (public link or salesman app). Point-of-sale display '
  'material, samples, anything not sold by the piece.';

update catalog_items set hidden = true, updated_at = now(), updated_by = 'catalog tidy'
where item_code in ('Big Product Display', 'Big Size Cardboard', 'Small Product Display')
  and hidden is distinct from true;

-- ── 2. categories ─────────────────────────────────────────────────────────────
update catalog_items set category = 'BLUETOOTH HEADSET' where item_code in (
  'T10', 'T11', 'T12', 'T16', 'T17', 'T18'
);

update catalog_items set category = 'CABLE' where item_code in (
  'P04 2mtr', 'X01 UM', 'X02-M', 'X05 UC', 'X05-L', 'X16', 'X22 CC 1Mtr', 'X22 CL', 'X24 CC',
  'X24 CC 1Mtr', 'X24 CL', 'X24 CL 1Mtr', 'X26-C', 'X26-L', 'X27 CC', 'X27-C', 'X27-L',
  'X29 CC', 'X29 CL', 'X30 CC', 'X31 CC 1 Mtr', 'X31 TC 1 Mtr', 'X32 CC 1 Mtr', 'X32 CL 1 Mtr',
  'X33 CCC 1.2 Mtr', 'X33 CCL 1.2 Mtr', 'X34 CC 1 Mtr'
);

update catalog_items set category = 'CAR CHARGER' where item_code in (
  'C03', 'C07', 'C10', 'C11', 'C12', 'C13', 'C14', 'C15 CC', 'C15 CL'
);

update catalog_items set category = 'CHARGER' where item_code in (
  'UK10 C', 'W01'
);

update catalog_items set category = 'POWER BANK' where item_code in (
  'K105'
);

-- Anything still unfiled follows the same rules the classifier applies, so the
-- catalog never shows a bare "Other" chip again.
update catalog_items set category = 'CABLE'
where coalesce(category, '') in ('', 'OTHER')
  and (display_name ilike '%cable%' or display_name ilike '%mtr%' or spec ilike '%cable%');
