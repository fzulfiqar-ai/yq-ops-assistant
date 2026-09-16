-- Marketplace v2.1 (16-Sep-2026): /about is a page, never a storefront slug.
-- Apply with: python -m scripts.apply_sql scripts/marketplace_reserved_about_migration.sql
insert into public.shop_reserved_slugs (slug)
values ('about'), ('help'), ('ask'), ('saved')
on conflict do nothing;
