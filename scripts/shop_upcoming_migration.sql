-- WEKOME "Coming soon" (24-Sep-2026, trust plan §6b, M11 — release R1b). Additive, idempotent.
--   Rehearse: python -m scripts.apply_sql scripts/shop_upcoming_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/shop_upcoming_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/shop_upcoming_reverse.sql
--
-- Why: the WEKOME opening order (34 models) lands in ~2 weeks and the owner wants the range
-- announced before it arrives, without a price. This table holds the announced cards — brand,
-- model code, copy in EN/AR, variant chips, photos, the expected month, a status — and NOTHING
-- about money or stock: it has NO price, cost or quantity column at all (the self-check below
-- refuses any). Interest ("notify me when it lands") reuses shop_restock_requests, which gains a
-- nullable link to the card and an optional, non-binding quantity.
--
-- Rules the app keeps (app/upcoming.py): only `published` rows are public; a row whose
-- catalog_item_code names a live catalog item retires automatically; a passed expected_month
-- reads "Arriving soon". Reserved slugs: /brands, /wekome and /coming-soon are marketplace pages,
-- so a rep's referral code can never take them (mirrored in MarketApp.tsx RESERVED,
-- public/catalog-prefetch.js and app/shop.py _RESERVED_FALLBACK).

-- ── 1. the announced cards ────────────────────────────────────────────────────
create table if not exists shop_upcoming_items (
  id                 bigint generated always as identity primary key,
  brand              text        not null,
  model_code         text        not null,
  category           text,
  name_en            text        not null,
  name_ar            text,
  spec_en            text,
  spec_ar            text,
  variants           jsonb       not null default '[]'::jsonb,   -- [{label, label_ar, comps}] — colours/connectors/sizes, never counts
  photo_url          text,
  photo_thumb_urls   jsonb,                                       -- {"160": url, "320": url, "512": url} (WebP, catalog bucket, upcoming/)
  box_url            text,
  shipment_ref       text,                                        -- invoice number(s); internal, never public
  expected_month     date,                                        -- first day of the arrival month; the label derives from it
  expected_label_en  text,                                        -- optional override of the derived label
  expected_label_ar  text,
  status             text        not null default 'draft'
                     check (status in ('draft', 'published', 'arrived', 'withdrawn')),
  catalog_item_code  text,                                        -- set on arrival: links the card to the live catalog item (auto-retire)
  sort_order         int,
  created_by         text,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  unique (brand, model_code)
);
alter table shop_upcoming_items enable row level security;   -- service role only; no policies
revoke all on shop_upcoming_items from anon, authenticated;
create index if not exists shop_upcoming_items_status_idx on shop_upcoming_items (status, sort_order);

comment on table shop_upcoming_items is
  'Announced ("Coming soon") models before they land (24-Sep-2026). Copy, chips and photos only: no price, cost or quantity column, by design. Published rows reach the marketplace through app/upcoming.py PUBLIC_FIELDS.';
comment on column shop_upcoming_items.catalog_item_code is
  'Linked on arrival. While the linked catalog item is active the card is retired automatically and its URL shows the live product.';

-- ── 2. interest reuses the restock table ─────────────────────────────────────
alter table shop_restock_requests add column if not exists upcoming_id  bigint references shop_upcoming_items(id) on delete set null;
alter table shop_restock_requests add column if not exists qty_interest int;   -- optional, "no commitment"; never an order
do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'shop_restock_requests_qty_interest_check') then
    alter table shop_restock_requests
      add constraint shop_restock_requests_qty_interest_check check (qty_interest is null or qty_interest > 0);
  end if;
end $$;
create index if not exists shop_restock_upcoming_open_idx
  on shop_restock_requests (upcoming_id) where upcoming_id is not null and notified_at is null;

comment on column shop_restock_requests.upcoming_id is
  'Set when the request is "notify me when it lands" for an announced (coming soon) card rather than a sold-out SKU; item_code then carries BRAND:MODEL.';

-- ── 3. reserved slugs (the marketplace pages) ────────────────────────────────
insert into shop_reserved_slugs (slug, note) values
  ('brands',      'marketplace /brands/{brand} (24-Sep-2026)'),
  ('wekome',      'redirects to /brands/wekome'),
  ('coming-soon', 'redirects to /brands/wekome')
on conflict (slug) do nothing;

-- ── 4. verification: fails the transaction if anything is off ────────────────
do $$
declare
  n int;
begin
  if to_regclass('public.shop_upcoming_items') is null then
    raise exception 'shop_upcoming migration FAILED: shop_upcoming_items missing';
  end if;
  -- the table must never carry money or stock: no column may even look like one
  select count(*) into n from information_schema.columns
  where table_schema = 'public' and table_name = 'shop_upcoming_items'
    and column_name ~* '(price|cost|qty|quantity|amount|value|margin|rmb|usd|bhd|pcs|carton)';
  if n > 0 then
    raise exception 'shop_upcoming migration FAILED: shop_upcoming_items has % price/cost/quantity column(s)', n;
  end if;
  select count(*) into n from information_schema.columns
  where table_schema = 'public' and table_name = 'shop_upcoming_items'
    and column_name in ('brand', 'model_code', 'name_en', 'name_ar', 'spec_en', 'spec_ar', 'variants', 'photo_url',
                        'photo_thumb_urls', 'box_url', 'shipment_ref', 'expected_month', 'expected_label_en',
                        'expected_label_ar', 'status', 'catalog_item_code', 'sort_order', 'created_by',
                        'created_at', 'updated_at');
  if n <> 20 then
    raise exception 'shop_upcoming migration FAILED: shop_upcoming_items has %/20 expected columns', n;
  end if;
  select count(*) into n from pg_constraint
  where conrelid = 'public.shop_upcoming_items'::regclass and contype = 'u'
    and pg_get_constraintdef(oid) like '%(brand, model_code)%';
  if n <> 1 then
    raise exception 'shop_upcoming migration FAILED: unique (brand, model_code) missing';
  end if;
  select count(*) into n from information_schema.columns
  where table_schema = 'public' and table_name = 'shop_restock_requests' and column_name in ('upcoming_id', 'qty_interest');
  if n <> 2 then
    raise exception 'shop_upcoming migration FAILED: shop_restock_requests has %/2 new columns', n;
  end if;
  select count(*) into n from shop_reserved_slugs where slug in ('brands', 'wekome', 'coming-soon');
  if n <> 3 then
    raise exception 'shop_upcoming migration FAILED: %/3 reserved slugs present', n;
  end if;
  select count(*) into n from pg_class c join pg_namespace ns on ns.oid = c.relnamespace
  where ns.nspname = 'public' and c.relname = 'shop_upcoming_items' and not c.relrowsecurity;
  if n > 0 then
    raise exception 'shop_upcoming migration FAILED: shop_upcoming_items without RLS';
  end if;
  select count(*) into n from information_schema.role_table_grants
  where table_schema = 'public' and grantee in ('anon', 'authenticated')
    and table_name in ('shop_upcoming_items', 'shop_restock_requests', 'shop_reserved_slugs');
  if n > 0 then
    raise exception 'shop_upcoming migration FAILED: % anon/authenticated grants on upcoming/restock/reserved tables', n;
  end if;
  raise notice 'shop_upcoming migration: verified';
end $$;
