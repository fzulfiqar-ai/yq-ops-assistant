-- Marketplace v3 (17-Sep-2026): campaign creatives that are never cropped.
-- Apply with: python -m scripts.apply_sql scripts/marketplace_campaign_creative_migration.sql
-- Then:       python -m scripts.audit_grants
-- Idempotent and additive (every new column has a default or is nullable), so the code already in
-- production keeps working before and after it. No GRANT: shop_campaigns is service-role only
-- (RLS on, no policies — scripts/marketplace_campaigns_migration.sql).
--
--   image_url_600  the 600 w rendition the upload route already returns (srcset 600/1200)
--   image_fit      how an uploaded photo is framed: contain (default, never cropped) | cover
--   product_codes  a composed creative: up to 3 catalog item codes laid on a canvas (≤3 enforced by
--                  app.shop.validate_campaign; codes keep the price book's spelling)
--   canvas         the creative's tint: lilac (default) | apricot | mint | plum | night

alter table shop_campaigns add column if not exists image_url_600 text;
alter table shop_campaigns add column if not exists image_fit     text not null default 'contain';
alter table shop_campaigns add column if not exists product_codes text[];
alter table shop_campaigns add column if not exists canvas        text not null default 'lilac';

-- Checks are added separately so a re-run never stacks a second, auto-named constraint.
alter table shop_campaigns drop constraint if exists shop_campaigns_image_fit_check;
alter table shop_campaigns add constraint shop_campaigns_image_fit_check
  check (image_fit in ('contain', 'cover'));
alter table shop_campaigns drop constraint if exists shop_campaigns_canvas_check;
alter table shop_campaigns add constraint shop_campaigns_canvas_check
  check (canvas in ('lilac', 'apricot', 'mint', 'plum', 'night'));

-- Verify: every column present, both checks in place, nothing granted to the publishable key.
do $$
declare
  missing text;
begin
  select string_agg(c, ', ') into missing
  from unnest(array['image_url_600', 'image_fit', 'product_codes', 'canvas']) as c
  where not exists (select 1 from information_schema.columns
                    where table_schema = 'public' and table_name = 'shop_campaigns' and column_name = c);
  if missing is not null then
    raise exception 'shop_campaigns is missing column(s): %', missing;
  end if;
  if (select count(*) from pg_constraint
      where conrelid = 'public.shop_campaigns'::regclass
        and conname in ('shop_campaigns_image_fit_check', 'shop_campaigns_canvas_check')) <> 2 then
    raise exception 'shop_campaigns creative checks are missing';
  end if;
  if exists (select 1 from information_schema.role_table_grants
             where table_schema = 'public' and table_name = 'shop_campaigns'
               and grantee in ('anon', 'authenticated')) then
    raise exception 'shop_campaigns is granted to anon/authenticated — run python -m scripts.audit_grants';
  end if;
end $$;
