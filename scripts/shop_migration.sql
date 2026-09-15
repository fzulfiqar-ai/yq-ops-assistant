-- YQ Shop — shareable ordering catalog with salesman routing (15-Sep-2026). Idempotent.
-- Apply:  python -m scripts.apply_sql scripts/shop_migration.sql        (needs DATABASE_URL)
--
-- Adds: catalog_items.moq / pack_size; tables salesmen, catalog_stock_map, shop_orders,
-- shop_order_lines, shop_order_events, shop_counters, discount_rules, shop_events;
-- views v_catalog_stock_rows, v_catalog_stock, v_catalog_velocity, v_catalog_pairs, v_catalog_cost,
-- v_shop_unpriced_stock, v_shop_orders_agent, v_shop_order_lines_agent; function shop_next_order_no.
-- Every new table is RLS-enabled with NO anon/authenticated policy (service role only).
-- Contact details are NEVER seeded here (public repo) — add salesmen via POST /shop/salesmen.

-- ── 1. catalog_items: MOQ + pack size (enforced server-side, editable on the Catalog page) ──
alter table catalog_items add column if not exists moq integer not null default 1;
alter table catalog_items add column if not exists pack_size integer;

-- ── 2. salesmen ────────────────────────────────────────────────────────────────
create table if not exists salesmen (
  id              bigint generated always as identity primary key,
  name            text not null unique,
  phone           text,
  email           text,
  whatsapp        text,
  user_email      text,           -- user_roles.email → salesman dashboard scoping
  focus_name      text,           -- Focus "salesman" name (v_sales.salesman_resolved / salesman_targets)
  referral_code   text not null unique,
  is_active       boolean not null default true,
  sort_order      integer,
  notify_email    boolean not null default true,
  notify_whatsapp boolean not null default true,
  created_at      timestamptz default now(),
  updated_at      timestamptz default now()
);
alter table salesmen enable row level security;

-- ── 3. manual stock-name overrides (the rare mismatch the matcher cannot resolve) ──
create table if not exists catalog_stock_map (
  item_code       text primary key,
  stock_item_name text not null,
  note            text,
  updated_at      timestamptz default now()
);
alter table catalog_stock_map enable row level security;

-- ── 4. customer orders ─────────────────────────────────────────────────────────
create table if not exists shop_orders (
  id              bigint generated always as identity primary key,
  order_no        text not null unique,
  token           text not null unique,
  status          text not null default 'new'
                  check (status in ('new','confirmed','packed','delivered','cancelled')),
  customer_name   text not null,
  customer_phone  text not null,
  customer_shop   text,
  customer_area   text,
  customer_email  text,
  note            text,
  salesman_id     bigint references salesmen(id) on delete set null,
  salesman_name   text,
  source          text not null default 'default' check (source in ('referral','dropdown','default')),
  referral_code   text,
  src             text,
  coupon_code     text,
  subtotal_bhd    numeric(12,3) not null default 0,
  discount_bhd    numeric(12,3) not null default 0,
  delivery_bhd    numeric(12,3) not null default 0,
  total_bhd       numeric(12,3) not null default 0,
  items_count     integer not null default 0,
  units_count     integer not null default 0,
  has_backorder   boolean not null default false,
  ip_hash         text,
  ua              text,
  notify_result   jsonb,
  notified_at     timestamptz,
  created_at      timestamptz default now(),
  updated_at      timestamptz default now()
);
create index if not exists shop_orders_created_idx  on shop_orders (created_at desc);
create index if not exists shop_orders_salesman_idx on shop_orders (salesman_id);
create index if not exists shop_orders_status_idx   on shop_orders (status);
alter table shop_orders enable row level security;

create table if not exists shop_order_lines (
  id              bigint generated always as identity primary key,
  order_id        bigint not null references shop_orders(id) on delete cascade,
  item_code       text not null,
  display_name    text,
  spec            text,
  image_url       text,
  qty             integer not null check (qty > 0),
  list_price_bhd  numeric(12,3),
  unit_price_bhd  numeric(12,3) not null,
  discount_bhd    numeric(12,3) not null default 0,
  line_total_bhd  numeric(12,3) not null,
  stock_status    text,
  backorder       boolean not null default false,
  rule_ids        jsonb
);
create index if not exists shop_order_lines_order_idx on shop_order_lines (order_id);
create index if not exists shop_order_lines_item_idx  on shop_order_lines (item_code);
alter table shop_order_lines enable row level security;

create table if not exists shop_order_events (
  id        bigint generated always as identity primary key,
  order_id  bigint not null references shop_orders(id) on delete cascade,
  ts        timestamptz default now(),
  actor     text,
  event     text not null,
  detail    jsonb
);
create index if not exists shop_order_events_order_idx on shop_order_events (order_id);
alter table shop_order_events enable row level security;

-- Atomic per-month order numbers: PREFIX-YYMM-NNNN (prefix from app_settings.shop_order_prefix).
create table if not exists shop_counters (period text primary key, n integer not null default 0);
alter table shop_counters enable row level security;

create or replace function shop_next_order_no(p_prefix text)
returns text language plpgsql security definer set search_path = public as $$
declare
  v_period text := to_char(now() at time zone 'Asia/Bahrain', 'YYMM');
  v_n integer;
begin
  insert into shop_counters (period, n) values (v_period, 1)
  on conflict (period) do update set n = shop_counters.n + 1
  returning n into v_n;
  return format('%s-%s-%s', coalesce(nullif(trim(p_prefix), ''), 'YQ'), v_period, lpad(v_n::text, 4, '0'));
end $$;
revoke all on function shop_next_order_no(text) from public, anon, authenticated;
grant execute on function shop_next_order_no(text) to service_role;

-- ── 5. discount rules (qty tiers, cart-value, coupons, bundle price, salesman offers) ──
create table if not exists discount_rules (
  id              bigint generated always as identity primary key,
  name            text not null,
  kind            text not null
                  check (kind in ('qty_tier','cart_value','coupon','bundle_price','salesman_offer')),
  scope           jsonb not null default '{}'::jsonb,   -- {item_codes:[], categories:[], referral_codes:[]}
  min_qty         integer,
  min_value_bhd   numeric(12,3),
  pct_off         numeric(6,3),
  amount_off_bhd  numeric(12,3),
  fixed_price_bhd numeric(12,3),
  coupon_code     text unique,
  stackable       boolean not null default false,
  starts_at       timestamptz,
  ends_at         timestamptz,
  max_uses        integer,
  uses            integer not null default 0,
  priority        integer not null default 100,
  is_active       boolean not null default true,
  created_by      text,
  created_at      timestamptz default now(),
  updated_at      timestamptz default now()
);
alter table discount_rules enable row level security;

-- ── 6. funnel events (append-only; hashed IP, no PII) ─────────────────────────
create table if not exists shop_events (
  id            bigint generated always as identity primary key,
  ts            timestamptz default now(),
  session_id    text,
  event         text not null check (event in ('view','item','add','checkout','order')),
  item_code     text,
  referral_code text,
  salesman_id   bigint,
  src           text,
  ua            text,
  ip_hash       text
);
create index if not exists shop_events_ts_idx on shop_events (ts desc);
alter table shop_events enable row level security;

-- ── 7. settings (admin-editable; no business number lives in code) ────────────
insert into app_settings (key, value, description) values
  ('shop_min_margin_pct',             '0.20', 'Shop: floor = landed cost x (1 + this) x (1 + VAT); no discount may price below it'),
  ('shop_vat_rate',                   '0.10', 'Shop: VAT rate included in price-book rates (margin maths use ex-VAT prices)'),
  ('shop_low_stock_units',            '10',   'Shop: "Only a few left" when stock <= this many units'),
  ('shop_low_stock_days_cover',       '30',   'Shop: "Selling fast" badge when days of cover (90d velocity) < this'),
  ('shop_allow_backorder',            '1',    'Shop: 1 = out-of-stock items can be ordered as backorder'),
  ('shop_min_order_bhd',              '0',    'Shop: minimum order value in BHD (0 = none)'),
  ('shop_free_delivery_threshold_bhd','0',    'Shop: free-delivery threshold in BHD for the progress bar (0 = off)'),
  ('shop_delivery_fee_bhd',           '0',    'Shop: delivery fee charged below the free-delivery threshold (0 = none)'),
  ('shop_default_salesman',           '',     'Shop: salesman name used when no referral link and no dropdown choice'),
  ('shop_order_prefix',               'YQ',   'Shop: order number prefix (PREFIX-YYMM-NNNN)'),
  ('shop_social_proof_min_customers', '5',    'Shop: show "Ordered by N shops this month" when N >= this'),
  ('shop_show_retail_compare',        '1',    'Shop: 1 = show the retail (B2C) price as compare-at next to the trade price'),
  ('shop_best_seller_top_n',          '3',    'Shop: "Best seller" badge for the top N items per category (90d units)'),
  ('shop_trending_growth_pct',        '30',   'Shop: "Trending" badge when 30d units exceed the previous 30d by this %'),
  ('shop_trending_min_units',         '10',   'Shop: "Trending" needs at least this many units in the last 30d'),
  ('shop_new_days',                   '30',   'Shop: "New" badge for items first seen in the price book within this many days')
on conflict (key) do nothing;

-- ── 8. views ───────────────────────────────────────────────────────────────────
-- 8a. one row per stock row in the LATEST snapshot with the catalog code it belongs to.
--     Precedence: manual map → product_aliases → longest normalised-prefix match (all catalog
--     codes, active or not, so a retired variant never steals stock from an active one).
--     Validated 15-Sep-2026 against the owner's per-code stock sheet: 138/158 exact, 0 false positives.
create or replace view v_catalog_stock_rows as
with snap as (
  select sb.item_name, sb.warehouse_name, sb.net_qty, sb.total_value_bhd, sb.as_of_date,
         regexp_replace(upper(sb.item_name), '[^A-Z0-9]', '', 'g') as nk,
         p.sku_code as alias_code
  from stock_balance sb
  left join product_aliases pa on pa.alias_text = sb.item_name
  left join products        p  on p.id = pa.product_id
  where sb.as_of_date = (select max(as_of_date) from stock_balance)
),
codes as (
  select item_code, is_active, regexp_replace(upper(item_code), '[^A-Z0-9]', '', 'g') as k
  from catalog_items
)
select
  s.item_name, s.warehouse_name, s.net_qty, s.total_value_bhd, s.as_of_date,
  coalesce(
    (select m.item_code from catalog_stock_map m where m.stock_item_name = s.item_name limit 1),
    (select c.item_code from codes c where c.item_code = s.alias_code limit 1),
    (select c.item_code from codes c where c.k <> '' and s.nk like c.k || '%'
       order by length(c.k) desc, c.item_code limit 1)
  ) as item_code,
  case
    when exists (select 1 from catalog_stock_map m where m.stock_item_name = s.item_name) then 'manual'
    when exists (select 1 from codes c where c.item_code = s.alias_code) then 'alias'
    when exists (select 1 from codes c where c.k <> '' and s.nk like c.k || '%') then 'prefix'
    else 'none'
  end as match_source
from snap s;

-- 8b. stock per catalog code (quantities stay server-side; the API only emits a status).
create or replace view v_catalog_stock as
select item_code,
       sum(net_qty)          as stock_qty,
       min(match_source)     as match_source,
       max(as_of_date)       as as_of_date
from v_catalog_stock_rows
where item_code is not null
group by item_code;

-- 8c. stock that cannot be sold online: no active catalog code (not in the current price book).
create or replace view v_shop_unpriced_stock as
select r.item_name, r.warehouse_name, r.net_qty as stock_qty, r.total_value_bhd as value_bhd,
       r.item_code as matched_code, r.match_source, r.as_of_date
from v_catalog_stock_rows r
left join catalog_items ci on ci.item_code = r.item_code
where r.item_code is null or coalesce(ci.is_active, false) = false;

-- 8d. velocity per catalog code from the sales day book (v_sales.sku_code), giveaways excluded.
--     Windows anchor to MAX(sale_date), never CURRENT_DATE (same rule as v_item_velocity).
create or replace view v_catalog_velocity as
with mx as (select max(sale_date) as d from v_sales)
select v.sku_code as item_code,
       sum(case when v.sale_date >  mx.d - 30 then v.quantity else 0 end)                              as sold_30d,
       sum(case when v.sale_date >  mx.d - 60 and v.sale_date <= mx.d - 30 then v.quantity else 0 end) as prev_30d,
       sum(case when v.sale_date >  mx.d - 90 then v.quantity else 0 end)                              as sold_90d,
       count(distinct v.invoice_no)    filter (where v.sale_date > mx.d - 90)                          as invoices_90d,
       count(distinct v.customer_name) filter (where v.sale_date > mx.d - 30)                          as customers_30d,
       max(v.sale_date)                                                                                as last_sold
from v_sales v cross join mx
where v.sku_code is not null and coalesce(v.quantity, 0) > 0 and coalesce(v.is_giveaway, false) = false
group by v.sku_code;

-- 8e. co-purchase pairs (180d): the data behind "frequently bought together".
--     Pairs seen on a single invoice are noise and dropped (data-quality floor, not a business rule).
create or replace view v_catalog_pairs as
with mx as (select max(sale_date) as d from v_sales),
inv as (
  select distinct v.invoice_no, v.sku_code
  from v_sales v cross join mx
  where v.sku_code is not null and v.sale_date > mx.d - 180 and coalesce(v.quantity, 0) > 0
)
select a.sku_code as item_a, b.sku_code as item_b, count(*) as n_invoices
from inv a join inv b on a.invoice_no = b.invoice_no and a.sku_code < b.sku_code
group by 1, 2
having count(*) >= 2;

-- 8f. latest landed cost per catalog code (MRN uploads). Never granted to agents/public.
create or replace view v_catalog_cost as
select distinct on (sku_code)
       sku_code as item_code, landed_cost_bhd, product_cost_bhd, effective_date, doc_no
from mrn_landed_costs
order by sku_code, effective_date desc nulls last, id desc;

-- 8g. PII-free order views for the assistant / agents.
create or replace view v_shop_orders_agent as
select id, order_no, status, customer_shop, customer_area, salesman_name, source, referral_code, coupon_code,
       subtotal_bhd, discount_bhd, delivery_bhd, total_bhd, items_count, units_count, has_backorder,
       created_at, updated_at
from shop_orders;

create or replace view v_shop_order_lines_agent as
select l.id, l.order_id, o.order_no, o.status, o.salesman_name, o.created_at,
       l.item_code, l.display_name, l.qty, l.list_price_bhd, l.unit_price_bhd, l.discount_bhd,
       l.line_total_bhd, l.stock_status, l.backorder
from shop_order_lines l join shop_orders o on o.id = l.order_id;

-- ── 9. read-only role: views only (plain statements — the role exists in prod) ──
grant select on v_catalog_stock_rows, v_catalog_stock, v_shop_unpriced_stock, v_catalog_velocity,
                v_catalog_pairs, v_shop_orders_agent, v_shop_order_lines_agent to yq_readonly;
