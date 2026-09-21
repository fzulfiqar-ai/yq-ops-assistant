-- YQ Shop — catalog tidy for shipment LC1716_196 / MRN YQ-26-09-2 (21-Sep-2026). Idempotent.
-- Apply:  python -m scripts.apply_sql scripts/shop_catalog_lc1716_migration.sql
--
-- The price-book sync (app.catalog.sync_from_price_book) created four catalog rows for the
-- SKUs this shipment introduced. Two need the same treatment earlier rows already got:
--
--   1. 'Big Product Display - VF-ZSG01' is point-of-sale furniture (a 1.6 m PVC display),
--      not something a merchant orders by the piece. Same rule as the three display stands
--      hidden by shop_catalog_tidy_migration.sql: hidden, never is_active = false, so the
--      next price-book sync cannot switch it back on.
--
--   2. TB-D10 / TB-D12 are counter display boxes pre-filled with cables (the TB-D1..D9 family
--      already lives under CABLE). The sync filed them under OTHER, which the shop hides
--      behind the category chips; put them with their siblings so merchants can find them.

update catalog_items
   set hidden = true, updated_at = now(), updated_by = 'lc1716 tidy'
 where item_code = 'Big Product Display - VF-ZSG01'
   and hidden is distinct from true;

update catalog_items
   set category = 'CABLE', updated_at = now(), updated_by = 'lc1716 tidy'
 where item_code in ('TB-D10', 'TB-D12')
   and category is distinct from 'CABLE';
