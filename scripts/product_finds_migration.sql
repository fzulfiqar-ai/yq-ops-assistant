-- Product Finds — "new / unique products" the sales team spots in the field and used to dump
-- into a WhatsApp group. Now captured in the portal (photo + price + note), browsable as a
-- gallery by management, shareable via a tokenized public link (like the catalog) and
-- promotable into the real catalog.
--
-- Photos live in a PRIVATE Supabase Storage bucket ('finds'); this table stores only the
-- object PATH and the API hands out short-lived SIGNED URLs at read time (Bahrain PDPL:
-- competitor tags / shop-fronts / people may appear). The bucket is created by the API on
-- first upload (app/product_finds.ensure_bucket) and the share token lives in app_settings
-- (key 'finds_share_token'), so nothing else to set up in the Supabase dashboard.
-- Idempotent — safe to run more than once.

create table if not exists product_finds (
  id                 bigint generated always as identity primary key,
  name               text,                 -- product name (nullable; sales may not know)
  price_bhd          numeric,              -- price seen in the field (nullable)
  currency           text default 'BHD',
  note               text,                 -- free text: where seen, MOQ, competitor, etc.
  category           text,                 -- optional CABLE / CHARGER / ...
  source             text,                 -- e.g. 'WhatsApp', a vendor / shop name
  image_path         text not null,        -- object path in the private 'finds' bucket
  status             text not null default 'new'
                       check (status in ('new','reviewing','promoted','archived')),
  promoted_item_code text,                 -- catalog_items.item_code once promoted
  source_file        text,                 -- original filename (seed dedupe); null for portal uploads
  posted_by          text,                 -- user email (stamped server-side)
  posted_at          timestamptz default now(),
  reviewed_by        text,
  updated_at         timestamptz default now()
);
create index if not exists product_finds_time   on product_finds (posted_at desc);
create index if not exists product_finds_status on product_finds (status);
-- seed dedupe: one row per imported source file (portal uploads leave source_file null)
create unique index if not exists product_finds_source_file
  on product_finds (source_file) where source_file is not null;

alter table product_finds enable row level security;  -- backend (service role) manages it

-- BI/read views may read finds later. Object-privilege grant is fine inside PL/pgSQL (the
-- earlier gotcha was ROLE-membership grants); guard on the role existing so this migration
-- still applies cleanly in an environment without the read-only role.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'yq_readonly') then
    grant select on product_finds to yq_readonly;
  end if;
end $$;
