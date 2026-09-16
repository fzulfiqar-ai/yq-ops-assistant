-- Marketplace v2 (16-Sep-2026): two more first-URL-segments a salesman slug may never take.
--   /quick  — the Quick order page
--   /fonts  — the self-hosted font files
-- Mirrors web/src/MarketApp.tsx RESERVED, web/public/catalog-prefetch.js and app/shop.py _RESERVED_FALLBACK.
-- Apply: python -m scripts.apply_sql scripts/marketplace_reserved_quick_migration.sql ; then python -m scripts.audit_grants
insert into public.shop_reserved_slugs (slug)
values ('quick'), ('fonts')
on conflict (slug) do nothing;
