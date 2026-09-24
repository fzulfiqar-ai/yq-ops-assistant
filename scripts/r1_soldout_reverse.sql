-- Reverse of r1_soldout_migration.sql: removes the staff backorder switch (the code default '1'
-- then applies, so the salesman path keeps its backorder either way). The merchant switch is not
-- touched here — if the release flipped it, put it back from Settings or with:
--   update app_settings set value = '1', updated_by = 'rollback R1', updated_at = now()
--    where key = 'shop_allow_backorder';
delete from app_settings where key in ('shop_allow_backorder_staff', 'shop_stock_fresh_days');
