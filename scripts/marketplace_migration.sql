-- YQ Marketplace — merchant identity, salesman attribution, order lifecycle depth, events v2
-- (16-Sep-2026). Idempotent.
-- Apply:  python -m scripts.apply_sql scripts/marketplace_migration.sql      (needs DATABASE_URL)
-- Then:   python -m scripts.audit_grants                                     (must exit 0)
--
-- What this adds (plan: ~/.claude/plans, sections T/U):
--   * shop_customers (+ shop_customer_phones): the merchant record keyed by phone, with the
--     recorded salesman (admin assignment > Focus map placeholder > sticky first touch);
--   * shop_customer_sessions / shop_access_links: verified identity via salesman-issued links
--     (tables now, endpoints in Phase 2); shop_push_subscriptions for Web Push (Phase 2);
--   * shop_reserved_slugs: first URL segments a salesman referral_code may never take;
--   * shop_orders: customer link, device + client_order_id idempotency, attribution source and
--     conflict flag, assignment, "who holds the goods" at Preparing, per-stage timestamps,
--     confirmed totals, payment-ready columns (no payment flow), Focus invoice link;
--     status gains out_for_delivery ("On the way"), source gains market;
--   * shop_order_lines: qty_confirmed + line_status (confirm-with-changes);
--   * shop_events: device_id, customer_id, meta jsonb and the v2 event vocabulary;
--   * salesmen: public storefront card fields; user_roles: storekeeper role;
--   * views: v_customer_regulars (Focus purchase cadence per merchant x SKU), v_shop_assignment_queue,
--     v_shop_search_terms, v_shop_rail_perf; v_shop_orders_agent gains APPENDED columns.
--
-- Ordering matters: shop_customers before the shop_orders FK; CHECKs are widened with
-- drop-if-exists then add (the shop_salesman_mode pattern); CREATE OR REPLACE VIEW can only
-- append columns, so v_shop_orders_agent adds its new columns LAST.
-- Every new table: RLS on, no anon/authenticated policy (service role only). Since the
-- 16-Sep-2026 grant lockdown, postgres's default ACL no longer grants new objects to those
-- roles; the explicit revokes at the end are belt and braces, and the final block verifies.

-- ── 1. merchants ───────────────────────────────────────────────────────────────
create table if not exists shop_customers (
  id                  bigint generated always as identity primary key,
  phone               text not null unique,          -- display primary, normalised digits (973...)
  name                text,
  shop                text,
  area                text,
  email               text,
  salesman_id         bigint references salesmen(id) on delete set null,   -- admin assignment (authoritative)
  assigned_by         text,
  assigned_at         timestamptz,
  focus_salesman_name text,                          -- placeholder for an official Focus mapping
  sticky_salesman_id  bigint references salesmen(id) on delete set null,   -- first-touch rep
  first_ref           text,                          -- the slug / ref the merchant first arrived with
  first_order_at      timestamptz,
  last_order_at       timestamptz,
  orders_count        integer not null default 0,
  total_bhd           numeric(12,3) not null default 0,
  focus_customer_id   bigint references customers(id) on delete set null,  -- links Focus history (regulars)
  device_ids          jsonb not null default '[]'::jsonb,                  -- last 10 device ids seen
  verified_at         timestamptz,
  opted_out           boolean not null default false,
  notes               text,
  created_at          timestamptz default now(),
  updated_at          timestamptz default now()
);
create index if not exists shop_customers_salesman_idx  on shop_customers (salesman_id);
create index if not exists shop_customers_sticky_idx    on shop_customers (sticky_salesman_id);
create index if not exists shop_customers_focus_idx     on shop_customers (focus_customer_id);
alter table shop_customers enable row level security;

-- One shop, several numbers (owner, counter, brother); one number, never two shops.
create table if not exists shop_customer_phones (
  phone        text primary key,
  customer_id  bigint not null references shop_customers(id) on delete cascade,
  is_primary   boolean not null default false,
  label        text,
  created_at   timestamptz default now()
);
create index if not exists shop_customer_phones_customer_idx on shop_customer_phones (customer_id);
alter table shop_customer_phones enable row level security;

-- Verified sessions (Phase 2): random 32 bytes, stored hashed, device-bound, sliding 180 days.
create table if not exists shop_customer_sessions (
  id             bigint generated always as identity primary key,
  customer_id    bigint not null references shop_customers(id) on delete cascade,
  token_hash     text not null unique,
  device_id      text,
  ua             text,
  ip_hash        text,
  issued_by      text,
  issued_via     text not null default 'access_link'
                 check (issued_via in ('access_link', 'magic_link', 'otp', 'admin')),
  issued_at      timestamptz default now(),
  last_seen_at   timestamptz,
  expires_at     timestamptz not null,
  revoked_at     timestamptz,
  revoke_reason  text
);
create index if not exists shop_customer_sessions_customer_idx on shop_customer_sessions (customer_id);
create index if not exists shop_customer_sessions_expires_idx  on shop_customer_sessions (expires_at);
alter table shop_customer_sessions enable row level security;

-- One-time access links a salesman/admin issues and sends himself (Phase 2): 24 random bytes,
-- stored hashed, single use, 72 h.
create table if not exists shop_access_links (
  id            bigint generated always as identity primary key,
  customer_id   bigint not null references shop_customers(id) on delete cascade,
  token_hash    text not null unique,
  created_by    text not null,
  created_at    timestamptz default now(),
  expires_at    timestamptz not null,
  used_at       timestamptz,
  used_device   text,
  used_ip_hash  text,
  revoked_at    timestamptz,
  note          text
);
create index if not exists shop_access_links_customer_idx on shop_access_links (customer_id);
alter table shop_access_links enable row level security;

-- Web Push subscriptions (Phase 2). topics: 'order' and 'back_in_stock:<code>'.
create table if not exists shop_push_subscriptions (
  id           bigint generated always as identity primary key,
  customer_id  bigint references shop_customers(id) on delete set null,
  device_id    text not null,
  endpoint     text not null unique,
  p256dh       text not null,
  auth         text not null,
  topics       text[] not null default '{order}',
  ua           text,
  created_at   timestamptz default now(),
  last_ok_at   timestamptz,
  fail_count   integer not null default 0,
  disabled_at  timestamptz
);
create index if not exists shop_push_subscriptions_device_idx on shop_push_subscriptions (device_id);
alter table shop_push_subscriptions enable row level security;

-- ── 2. reserved first URL segments (a referral_code may never shadow an app route) ──
create table if not exists shop_reserved_slugs (
  slug  text primary key,
  note  text
);
alter table shop_reserved_slugs enable row level security;
insert into shop_reserved_slugs (slug) values
  ('c'), ('o'), ('p'), ('t'), ('s'), ('f'), ('search'), ('cart'), ('checkout'), ('orders'), ('order'),
  ('join'), ('api'), ('public'), ('shop'), ('admin'), ('assets'), ('static'), ('me'), ('health'),
  ('share'), ('optout'), ('login'), ('invite'), ('sw.js'), ('version.json'), ('manifest.webmanifest'),
  ('robots.txt'), ('favicon.ico'), ('index.html'), ('catalog'), ('products'), ('product'),
  ('categories'), ('category'), ('account'), ('settings'), ('help'), ('about'), ('contact'), ('yq'),
  ('market'), ('marketplace'), ('track'), ('app'), ('offers'), ('deals'), ('new'), ('brands')
on conflict (slug) do nothing;

-- ── 3. shop_orders: identity, idempotency, attribution, lifecycle, payment-ready ──
alter table shop_orders add column if not exists customer_id            bigint references shop_customers(id) on delete set null;
alter table shop_orders add column if not exists device_id              text;
alter table shop_orders add column if not exists client_order_id        text;
alter table shop_orders add column if not exists session_ref            text;
alter table shop_orders add column if not exists attribution_source     text;
alter table shop_orders add column if not exists attribution_conflict   boolean not null default false;
alter table shop_orders add column if not exists assigned_at            timestamptz;
alter table shop_orders add column if not exists assigned_by            text;
alter table shop_orders add column if not exists issued_to_salesman_id  bigint references salesmen(id) on delete set null;
alter table shop_orders add column if not exists issued_at              timestamptz;
alter table shop_orders add column if not exists expected_delivery      text;
alter table shop_orders add column if not exists confirmed_at           timestamptz;
alter table shop_orders add column if not exists packed_at              timestamptz;
alter table shop_orders add column if not exists out_for_delivery_at    timestamptz;
alter table shop_orders add column if not exists delivered_at           timestamptz;
alter table shop_orders add column if not exists cancelled_at           timestamptz;
alter table shop_orders add column if not exists cancelled_by           text;
alter table shop_orders add column if not exists cancel_reason          text;
alter table shop_orders add column if not exists subtotal_confirmed_bhd numeric(12,3);
alter table shop_orders add column if not exists total_confirmed_bhd    numeric(12,3);
alter table shop_orders add column if not exists payment_status         text not null default 'unpaid';
alter table shop_orders add column if not exists payment_method         text;
alter table shop_orders add column if not exists focus_invoice_no       text;
alter table shop_orders add column if not exists sla_notified_at        timestamptz;

alter table shop_orders drop constraint if exists shop_orders_status_check;
alter table shop_orders add constraint shop_orders_status_check
  check (status in ('new', 'confirmed', 'packed', 'out_for_delivery', 'delivered', 'cancelled'));
alter table shop_orders drop constraint if exists shop_orders_source_check;
alter table shop_orders add constraint shop_orders_source_check
  check (source in ('referral', 'dropdown', 'default', 'salesman', 'market'));
alter table shop_orders drop constraint if exists shop_orders_attribution_source_check;
alter table shop_orders add constraint shop_orders_attribution_source_check
  check (attribution_source is null or attribution_source in
         ('customer_admin', 'focus_map', 'sticky', 'session_ref', 'checkout_pick', 'staff', 'default', 'unassigned'));
alter table shop_orders drop constraint if exists shop_orders_payment_status_check;
alter table shop_orders add constraint shop_orders_payment_status_check
  check (payment_status in ('unpaid', 'partial', 'paid', 'refunded'));

-- A retry after a timeout must never create a second order.
create unique index if not exists shop_orders_client_order_idx
  on shop_orders (device_id, client_order_id) where device_id is not null and client_order_id is not null;
create index if not exists shop_orders_phone_created_idx   on shop_orders (customer_phone, created_at desc);
create index if not exists shop_orders_customer_idx        on shop_orders (customer_id);
create index if not exists shop_orders_status_salesman_idx on shop_orders (status, salesman_id);
create index if not exists shop_orders_device_created_idx  on shop_orders (device_id, created_at desc);
create index if not exists shop_orders_unassigned_idx      on shop_orders (created_at)
  where salesman_id is null and status in ('new', 'confirmed');

-- ── 4. lines: confirm with changes ────────────────────────────────────────────
alter table shop_order_lines add column if not exists qty_confirmed integer;
alter table shop_order_lines add column if not exists line_status   text not null default 'ok';
alter table shop_order_lines add column if not exists note          text;
alter table shop_order_lines drop constraint if exists shop_order_lines_line_status_check;
alter table shop_order_lines add constraint shop_order_lines_line_status_check
  check (line_status in ('ok', 'changed', 'removed', 'backorder'));

-- ── 5. events v2 ──────────────────────────────────────────────────────────────
alter table shop_events add column if not exists device_id   text;
alter table shop_events add column if not exists customer_id bigint;
alter table shop_events add column if not exists meta        jsonb;
alter table shop_events drop constraint if exists shop_events_event_check;
alter table shop_events add constraint shop_events_event_check
  check (event in ('view', 'item', 'add', 'checkout', 'order',
                   'search', 'search_zero', 'remove', 'qty', 'cart', 'checkout_start',
                   'rail_click', 'reco_click', 'share', 'install', 'reorder', 'cancel', 'vitals', 'push_subscribe'));
create index if not exists shop_events_event_ts_idx on shop_events (event, ts desc);
create index if not exists shop_events_session_idx  on shop_events (session_id);
create index if not exists shop_events_device_idx   on shop_events (device_id);

-- ── 6. salesmen: the public storefront card ───────────────────────────────────
alter table salesmen add column if not exists public_profile  boolean not null default true;
alter table salesmen add column if not exists public_whatsapp boolean not null default false;
alter table salesmen add column if not exists title           text;
alter table salesmen add column if not exists photo_url       text;

-- ── 7. storekeeper role ───────────────────────────────────────────────────────
alter table user_roles drop constraint if exists user_roles_role_check;
alter table user_roles add constraint user_roles_role_check
  check (role in ('admin', 'member', 'manager', 'viewer', 'salesman', 'storekeeper'));

-- ── 8. settings (admin-editable; no business number lives in code) ───────────
insert into app_settings (key, value, description) values
  ('shop_sticky_days',       '90', 'Marketplace: days after the last order during which the recorded salesman beats a different rep link'),
  ('shop_public_tiers',      '1',  'Marketplace: 1 = show volume-tier prices to anonymous visitors'),
  ('shop_phone_daily_cap',   '10', 'Marketplace: max orders per phone number per 24 h'),
  ('shop_device_daily_cap',  '20', 'Marketplace: max orders per device per 24 h'),
  ('shop_assign_sla_min',    '30', 'Marketplace: minutes before an unassigned order is re-alerted to admins'),
  ('shop_market_enabled',    '1',  'Marketplace: 1 = the token-less public marketplace endpoints are on'),
  ('shop_areas',             'Manama,Muharraq,Riffa,Isa Town,Hamad Town,Sitra,Budaiya,Saar,Hidd,Jidhafs,Sanabis,Aali,Zallaq,Salmabad,Tubli,Seef,Juffair,Adliya,Gudaibiya,Hoora,Galali,Arad,Busaiteen,Askar',
                                  'Marketplace: comma-separated area list offered at checkout')
on conflict (key) do nothing;

-- ── 9. views ──────────────────────────────────────────────────────────────────
-- 9a. Focus purchase cadence per merchant x SKU: what a shop buys, how many, how often, and
--     whether it is due. Distinct sale dates so one invoice split over lines counts once;
--     giveaways excluded; windows anchor to MAX(sale_date) like every other sales view.
--     Carries customer names -> NEVER granted to yq_readonly or anyone but the service role.
create or replace view v_customer_regulars as
with mx as (select max(sale_date) as d from v_sales),
base as (
  select distinct v.customer_name, v.sku_code, v.sale_date, v.quantity
  from v_sales v
  where v.sku_code is not null and coalesce(v.quantity, 0) > 0
    and coalesce(v.is_giveaway, false) = false and v.customer_name is not null
),
agg as (
  select customer_name, sku_code,
         count(distinct sale_date)                                  as times_bought,
         percentile_cont(0.5) within group (order by quantity)      as median_qty,
         min(sale_date)                                             as first_bought,
         max(sale_date)                                             as last_bought
  from base
  group by 1, 2
),
gaps as (
  select customer_name, sku_code,
         percentile_cont(0.5) within group (order by gap) as median_days_between
  from (
    select customer_name, sku_code,
           sale_date - lag(sale_date) over (partition by customer_name, sku_code order by sale_date) as gap
    from (select distinct customer_name, sku_code, sale_date from base) d
  ) g
  where gap > 0
  group by 1, 2
)
select a.customer_name,
       a.sku_code                                   as item_code,
       a.times_bought,
       greatest(1, round(a.median_qty))::int        as median_qty,
       g.median_days_between::numeric(8,1)          as cadence_days,
       a.first_bought,
       a.last_bought,
       (mx.d - a.last_bought)                       as days_since,
       (g.median_days_between is not null
        and (mx.d - a.last_bought) >= 0.8 * g.median_days_between) as due
from agg a
cross join mx
left join gaps g on g.customer_name = a.customer_name and g.sku_code = a.sku_code
where a.times_bought >= 2;

-- 9b. Unassigned orders for the admin queue (PII-free).
create or replace view v_shop_assignment_queue as
select id, order_no, status, created_at, customer_shop, customer_area, total_bhd, units_count, items_count,
       session_ref, referral_code, src, attribution_source, attribution_conflict, sla_notified_at,
       round(extract(epoch from (now() - created_at)) / 60)::int as age_min
from shop_orders
where salesman_id is null and status in ('new', 'confirmed');

-- 9c. What merchants search for, and what returns nothing (feeds the synonym map and range gaps).
create or replace view v_shop_search_terms as
select lower(trim(meta->>'q'))                                   as term,
       count(*)                                                  as searches,
       count(*) filter (where event = 'search_zero')             as zero_results,
       count(distinct session_id)                                as sessions,
       max(ts)                                                   as last_seen
from shop_events
where event in ('search', 'search_zero') and coalesce(meta->>'q', '') <> ''
group by 1;

-- 9d. Rail performance per day (which home sections earn their place).
create or replace view v_shop_rail_perf as
select meta->>'rail'                                             as rail,
       date_trunc('day', ts)::date                               as day,
       count(*) filter (where event = 'rail_click')              as clicks,
       count(*) filter (where event = 'reco_click')              as reco_clicks,
       count(*) filter (where event = 'add')                     as adds,
       count(distinct session_id)                                as sessions
from shop_events
where meta ? 'rail'
group by 1, 2;

-- 9e. PII-free order view for the assistant: new columns APPENDED after placed_by.
create or replace view v_shop_orders_agent as
select id, order_no, status, customer_shop, customer_area, salesman_name, source, referral_code, coupon_code,
       subtotal_bhd, discount_bhd, delivery_bhd, total_bhd, items_count, units_count, has_backorder,
       created_at, updated_at,
       placed_by,
       attribution_source, attribution_conflict, assigned_at, expected_delivery, payment_status,
       confirmed_at, delivered_at, cancelled_at
from shop_orders;

-- ── 10. grants (plain statements; the role exists in prod) ────────────────────
grant select on v_shop_assignment_queue, v_shop_search_terms, v_shop_rail_perf, v_shop_orders_agent to yq_readonly;
revoke all on table shop_customers, shop_customer_phones, shop_customer_sessions, shop_access_links,
                    shop_push_subscriptions, shop_reserved_slugs from anon, authenticated;
revoke all on table v_customer_regulars, v_shop_assignment_queue, v_shop_search_terms, v_shop_rail_perf,
                    v_shop_orders_agent from anon, authenticated;

-- ── 11. verification: fails the transaction if anything is off ────────────────
do $$
declare
  n int;
begin
  if to_regclass('public.shop_customers') is null or to_regclass('public.shop_customer_phones') is null
     or to_regclass('public.shop_reserved_slugs') is null or to_regclass('public.v_customer_regulars') is null then
    raise exception 'marketplace migration FAILED: a new table or view is missing';
  end if;
  select count(*) into n from information_schema.columns
  where table_schema = 'public' and table_name = 'shop_orders'
    and column_name in ('customer_id', 'client_order_id', 'attribution_source', 'expected_delivery',
                        'payment_status', 'out_for_delivery_at', 'issued_to_salesman_id', 'cancel_reason');
  if n < 8 then
    raise exception 'marketplace migration FAILED: shop_orders has %/8 new columns', n;
  end if;
  select count(*) into n from pg_constraint
  where conname = 'shop_orders_status_check' and pg_get_constraintdef(oid) like '%out_for_delivery%';
  if n <> 1 then
    raise exception 'marketplace migration FAILED: status CHECK does not include out_for_delivery';
  end if;
  select count(*) into n from pg_constraint
  where conname = 'shop_events_event_check' and pg_get_constraintdef(oid) like '%search_zero%';
  if n <> 1 then
    raise exception 'marketplace migration FAILED: shop_events CHECK not widened';
  end if;
  select count(*) into n from pg_constraint
  where conname = 'user_roles_role_check' and pg_get_constraintdef(oid) like '%storekeeper%';
  if n <> 1 then
    raise exception 'marketplace migration FAILED: storekeeper role not allowed';
  end if;
  select count(*) into n from pg_tables
  where schemaname = 'public' and not rowsecurity
    and tablename in ('shop_customers', 'shop_customer_phones', 'shop_customer_sessions', 'shop_access_links',
                      'shop_push_subscriptions', 'shop_reserved_slugs');
  if n > 0 then
    raise exception 'marketplace migration FAILED: % new tables without RLS', n;
  end if;
  select count(*) into n from information_schema.role_table_grants
  where table_schema = 'public' and grantee in ('anon', 'authenticated')
    and table_name in ('shop_customers', 'shop_customer_phones', 'shop_customer_sessions', 'shop_access_links',
                       'shop_push_subscriptions', 'shop_reserved_slugs', 'v_customer_regulars',
                       'v_shop_assignment_queue', 'v_shop_search_terms', 'v_shop_rail_perf', 'v_shop_orders_agent');
  if n > 0 then
    raise exception 'marketplace migration FAILED: % anon/authenticated grants on new objects', n;
  end if;
  raise notice 'marketplace migration: verified';
end $$;
