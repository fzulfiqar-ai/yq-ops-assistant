-- Reverse of shop_upcoming_migration.sql. Only run if the "Coming soon" feature must be removed:
-- take `python -m scripts.db_backup --tables shop_upcoming_items,shop_restock_requests` FIRST
-- (the interest rows hold merchant requests the reps have not answered yet).
--
-- 'brands' stays reserved: it was already in shop_reserved_slugs before this migration
-- (marketplace_migration.sql); only the two redirect slugs are removed.
drop index if exists shop_restock_upcoming_open_idx;
alter table shop_restock_requests drop constraint if exists shop_restock_requests_qty_interest_check;
alter table shop_restock_requests drop column if exists qty_interest;
alter table shop_restock_requests drop column if exists upcoming_id;
delete from shop_reserved_slugs where slug in ('wekome', 'coming-soon');
drop table if exists shop_upcoming_items;
