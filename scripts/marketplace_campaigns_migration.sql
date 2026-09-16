-- Marketplace v2.1 (16-Sep-2026): campaigns (the honest "ads" layer) + restock requests.
-- Apply with: python -m scripts.apply_sql scripts/marketplace_campaigns_migration.sql
-- Then:       python -m scripts.audit_grants
-- Idempotent. Service-role only (RLS on, no policies) — exactly like discount_rules.

-- ── 1. campaigns: a promotion an admin schedules; the market renders it where `placement` says ──
create table if not exists shop_campaigns (
  id            bigint generated always as identity primary key,
  title         text not null,
  title_ar      text,
  line          text,
  line_ar       text,
  image_url     text,
  cta_label     text,
  cta_label_ar  text,
  cta_to        text not null default '/shop',            -- a market path (/shop?f=clearance, /p/X01, /quick) or https URL
  placement     text[] not null default '{strip}',        -- subset of {hero,strip,aside,category}
  category      text,                                     -- for placement 'category': which category page
  audience      text not null default 'all' check (audience in ('all','recognized','new')),
  rule_id       bigint references discount_rules(id) on delete set null,   -- the real offer behind it, if any
  sponsored     boolean not null default false,           -- supplier-paid slot: always labelled "Sponsored"
  sponsor_name  text,
  starts_at     timestamptz,
  ends_at       timestamptz,
  is_active     boolean not null default true,
  sort_order    integer not null default 100,
  created_by    text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
alter table shop_campaigns enable row level security;
create index if not exists shop_campaigns_live_idx on shop_campaigns (is_active, starts_at, ends_at);

-- ── 2. "tell me when back": a merchant asks for a sold-out product; the rep sees the list ──
create table if not exists shop_restock_requests (
  id            bigint generated always as identity primary key,
  item_code     text not null,
  phone         text,
  device_id     text,
  referral_code text,
  created_at    timestamptz not null default now(),
  notified_at   timestamptz
);
alter table shop_restock_requests enable row level security;
create index if not exists shop_restock_open_idx on shop_restock_requests (item_code) where notified_at is null;
create unique index if not exists shop_restock_device_item_open_idx
  on shop_restock_requests (device_id, item_code) where notified_at is null and device_id is not null;
