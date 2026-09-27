-- Offer ledger + follow-up exposures — release R7d (27-Sep-2026; plan §16 prerequisites, §23 item 4,
-- §24 r7_offer_ledger; audit OFF-2, OFF-3, OFF-4, OFF-6, OFF-9, OFF-16). Additive, idempotent.
--   Rehearse: python -m scripts.apply_sql scripts/r7d_offer_ledger_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/r7d_offer_ledger_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/r7d_offer_ledger_reverse.sql
--   Order:    AFTER scripts/r7c_order_lines_qty_migration.sql — v_offer_performance reads the line cost
--             snapshot (shop_order_lines.unit_cost_bhd) and the delivered quantity; the first block
--             stops with a clear message if they are missing. It reads both through to_jsonb(l), so
--             the r7c reverse still runs with this view in place (tests/test_r7c_order_heart guard).
--
-- Why: offers must be measurable BEFORE any offer runs (0 rules and 0 campaigns on 27-Sep-2026, read
-- only). Nothing here changes a price, an order, a line, an event, a customer or a statement.
--
--   1. discount_rules / shop_campaigns: archived_at + archived_by (soft delete). The API switches a
--      row off when it archives it and filters archived rows out of every read; a rule any order used
--      is never hard-deleted (the API refuses, and the ledger's foreign key refuses too).
--   2. shop_order_discounts: one row per (order line, rule) and per (order, cart-level rule) —
--      rule_snapshot (the rule as the shop was offered it, with the hold-out arm), kind, level
--      line|cart, amount_bhd placed, amount_confirmed_bhd after the rep's confirmation, clamped (the
--      margin floor cut it), stage placed|confirmed. Written by app.shop.create_order in one insert.
--   3. shop_coupon_reserve(rule, force) / shop_coupon_release(rule): the coupon counter as ONE
--      conditional UPDATE each (uses < max_uses, live window, active, not archived). SECURITY DEFINER,
--      service_role only.
--   4. shop_badge_log: the badges merchants saw, one row per item per Bahrain day (a shop_jobs job).
--   5. followup_exposures: the shops each rep was served (and the ones the 20 % hold-out kept off his
--      list), once per rep per day, on his first read of the Due list.
--   6. v_offer_performance (per rule) and v_followup_lift (per week, rep and arm): aggregates only —
--      no shop name, phone or token — granted to yq_readonly ONLY (the portal reads them through
--      run_readonly_query), revoked from anon and authenticated, not security_invoker.
--
-- Every new table: RLS on, no policy (service role only), all privileges revoked from anon and
-- authenticated. The API works before this file runs (probes + fallbacks) and starts using each
-- object within a minute of it existing.

do $$
begin
  if not exists (select 1 from information_schema.columns where table_schema = 'public'
                 and table_name = 'shop_order_lines' and column_name = 'unit_cost_bhd')
     or not exists (select 1 from information_schema.columns where table_schema = 'public'
                    and table_name = 'shop_order_lines' and column_name = 'qty_delivered') then
    raise exception 'r7d_offer_ledger: shop_order_lines.unit_cost_bhd / qty_delivered are missing - apply scripts/r7c_order_lines_qty_migration.sql first';
  end if;
  if to_regclass('public.discount_rules') is null or to_regclass('public.shop_campaigns') is null
     or to_regclass('public.salesmen') is null or to_regclass('public.v_sales') is null then
    raise exception 'r7d_offer_ledger: discount_rules / shop_campaigns / salesmen / v_sales missing - this database is not the shop schema';
  end if;
end $$;

-- ── 1. soft delete ──────────────────────────────────────────────────────────────
alter table discount_rules add column if not exists archived_at timestamptz;
alter table discount_rules add column if not exists archived_by text;
alter table shop_campaigns add column if not exists archived_at timestamptz;
alter table shop_campaigns add column if not exists archived_by text;
comment on column discount_rules.archived_at is
  'R7d soft delete: set = archived (switched off, out of every list and price, kept for its orders). A rule any order used is never deleted.';
comment on column shop_campaigns.archived_at is
  'R7d soft delete: set = archived (switched off, kept for the record). Campaigns are archived, never deleted.';

-- ── 2. the offer ledger ─────────────────────────────────────────────────────────
create table if not exists shop_order_discounts (
  id                   bigint generated always as identity primary key,
  order_id             bigint not null references shop_orders(id) on delete cascade,
  line_id              bigint references shop_order_lines(id) on delete set null,
  item_code            text,
  rule_id              bigint not null references discount_rules(id),      -- no action: a used rule is never deleted
  rule_snapshot        jsonb not null default '{}'::jsonb,
  kind                 text not null
                       check (kind in ('qty_tier', 'cart_value', 'coupon', 'bundle_price', 'salesman_offer')),
  level                text not null check (level in ('line', 'cart')),
  amount_bhd           numeric(12,3) not null check (amount_bhd >= 0),
  amount_confirmed_bhd numeric(12,3) check (amount_confirmed_bhd is null or amount_confirmed_bhd >= 0),
  clamped              boolean not null default false,
  stage                text not null default 'placed' check (stage in ('placed', 'confirmed')),
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now()
);
create index if not exists shop_order_discounts_order_idx on shop_order_discounts (order_id);
create index if not exists shop_order_discounts_rule_idx on shop_order_discounts (rule_id);
alter table shop_order_discounts enable row level security;
revoke all on shop_order_discounts from anon, authenticated;
comment on table shop_order_discounts is
  'R7d offer ledger: one row per (order line, rule) and per (order, cart-level rule). amount_bhd as placed; amount_confirmed_bhd at the agreed quantities (confirm / amend / delivery with changes). Written by app.shop.create_order, and by app.shop_heart for a line the rep adds or substitutes (born confirmed at its discount when added); service role only.';

-- ── 3. the coupon counter ───────────────────────────────────────────────────────
create or replace function shop_coupon_reserve(p_rule_id bigint, p_force boolean default false)
returns integer language sql security definer set search_path = public as $$
  update discount_rules
     set uses = coalesce(uses, 0) + 1
   where id = p_rule_id
     and kind = 'coupon'
     and (p_force or (
          is_active
          and archived_at is null
          and (max_uses is null or coalesce(uses, 0) < max_uses)
          and (starts_at is null or starts_at <= now())
          and (ends_at is null or ends_at > now())))
  returning uses
$$;
revoke all on function shop_coupon_reserve(bigint, boolean) from public, anon, authenticated;
grant execute on function shop_coupon_reserve(bigint, boolean) to service_role;
comment on function shop_coupon_reserve(bigint, boolean) is
  'R7d: take one use of a coupon atomically; NULL = none left (or ended / off / archived). p_force = an admin reopen of a cancelled order that already had it.';

create or replace function shop_coupon_release(p_rule_id bigint)
returns integer language sql security definer set search_path = public as $$
  update discount_rules set uses = greatest(coalesce(uses, 0) - 1, 0)
   where id = p_rule_id and kind = 'coupon'
  returning uses
$$;
revoke all on function shop_coupon_release(bigint) from public, anon, authenticated;
grant execute on function shop_coupon_release(bigint) to service_role;
comment on function shop_coupon_release(bigint) is
  'R7d: give one coupon use back (a cancelled order, or an order that failed after its reservation). Never below 0.';

-- ── 4. the badges merchants saw ─────────────────────────────────────────────────
create table if not exists shop_badge_log (
  as_of      date not null,
  item_code  text not null,
  badges     text[] not null default '{}',
  stock_qty  numeric,
  sold_90d   numeric,
  price_bhd  numeric(12,3),
  created_at timestamptz not null default now(),
  primary key (as_of, item_code)
);
alter table shop_badge_log enable row level security;
revoke all on shop_badge_log from anon, authenticated;
comment on table shop_badge_log is
  'R7d: one row per catalog item per Bahrain day — the badges shown, the stock and the 90-day units — so a clearance or badge effect can be measured against unbadged aging lines. Service role only.';

-- ── 5. follow-up exposures ──────────────────────────────────────────────────────
create table if not exists followup_exposures (
  id          bigint generated always as identity primary key,
  served_on   date not null,
  salesman_id bigint not null,                -- no foreign key: a measurement row never blocks a rep edit
  shop_key    text not null,                  -- 'f:<Focus customer name>' (app.followups.shop_key)
  kind        text not null check (kind in ('due', 'lapsed')),
  rank        integer,
  holdout     boolean not null,
  created_at  timestamptz not null default now(),
  unique (served_on, salesman_id, shop_key, kind)
);
create index if not exists followup_exposures_rep_idx on followup_exposures (salesman_id, served_on);
alter table followup_exposures enable row level security;
revoke all on followup_exposures from anon, authenticated;
comment on table followup_exposures is
  'R7d: the shops on each rep''s due / lapsed list (holdout = false) and the ones the 20 % hold-out kept off it (holdout = true), once per rep per Bahrain day, rank in the combined ordering. Service role only.';

-- the identity sequences of the two new id columns: nobody but the service role writes them
do $$
declare
  t text;
  s text;
begin
  foreach t in array array['shop_order_discounts', 'followup_exposures'] loop
    s := pg_get_serial_sequence('public.' || t, 'id');
    if s is not null then
      execute format('revoke all on sequence %s from anon, authenticated', s);
    end if;
  end loop;
end $$;

-- ── 6a. v_offer_performance ─────────────────────────────────────────────────────
-- Counted orders: not cancelled, not flagged test. Quantities: as ordered while Received, confirmed
-- once confirmed, delivered once delivered. Money is VAT-inclusive like the price book; revenue ex-VAT
-- divides by 1 + shop_vat_rate (default 0.10); cost = the landed cost snapshotted on the line (R7c).
-- A line-level offer is judged on its own line (after that line's discounts); a cart-level offer on
-- the whole order after every discount. GM only over lines with a cost; uncosted_lines says how many
-- were left out. orders / units / merchants / reps count only rows where the offer gave money.
create or replace view v_offer_performance as
with vat as (
  select coalesce((select case when btrim(value) ~ '^[0-9]+(\.[0-9]+)?$' then btrim(value)::numeric end
                   from app_settings where key = 'shop_vat_rate'), 0.10) as r
),
live as (
  select o.id, o.status, o.salesman_id, o.customer_id, o.customer_phone, o.created_at
  from shop_orders o
  where o.status <> 'cancelled' and not coalesce(o.is_test, false)
),
ln as (
  select l.id, l.order_id,
         coalesce(l.list_price_bhd, l.unit_price_bhd, 0)       as list_price,
         coalesce(l.unit_price_confirmed, l.unit_price_bhd, 0)  as unit_price,
         (r.j ->> 'unit_cost_bhd')::numeric                     as unit_cost,
         case when o.status = 'delivered' then coalesce((r.j ->> 'qty_delivered')::integer, l.qty_confirmed, l.qty)
              when o.status = 'new' then l.qty
              else coalesce(l.qty_confirmed, l.qty) end         as qty_eff
  from shop_order_lines l join live o on o.id = l.order_id
  -- the R7c columns through to_jsonb(l), as v_command_orders does: a whole-row reference pins no
  -- column, so r7c_order_lines_qty_reverse.sql's DROP COLUMN never meets this view (the lines then
  -- read as uncosted and as confirmed, instead of the reverse failing)
  cross join lateral (select to_jsonb(l) as j) r
),
ord as (
  select ln.order_id,
         sum(ln.list_price * ln.qty_eff)                                           as list_value,
         sum(ln.unit_price * ln.qty_eff)                                           as items_value,
         sum(ln.qty_eff)                                                           as units,
         sum(ln.unit_cost * ln.qty_eff) filter (where ln.unit_cost is not null)    as cost,
         sum(ln.unit_price * ln.qty_eff) filter (where ln.unit_cost is not null)   as items_costed,
         count(*) filter (where ln.unit_cost is null and ln.qty_eff > 0)           as uncosted_lines
  from ln group by ln.order_id
),
cart as (
  select d.order_id, sum(coalesce(d.amount_confirmed_bhd, d.amount_bhd)) as cart_discount
  from shop_order_discounts d join live o on o.id = d.order_id
  where d.level = 'cart'
  group by d.order_id
),
per_row as (
  select d.rule_id, d.order_id, d.clamped,
         coalesce(d.amount_confirmed_bhd, d.amount_bhd)                          as discount,
         d.amount_bhd                                                            as discount_placed,
         o.salesman_id,
         coalesce(o.customer_id::text, o.customer_phone)                         as merchant,
         o.created_at,
         case when d.level = 'line' then l.qty_eff else ord.units end            as units,
         case when d.level = 'line' then l.list_price * l.qty_eff else ord.list_value end as list_value,
         case when d.level = 'line'
              then case when l.unit_cost is not null then l.unit_price * l.qty_eff end
              else ord.items_costed
                   - coalesce(cart.cart_discount, 0) * coalesce(ord.items_costed / nullif(ord.items_value, 0), 0)
         end                                                                     as revenue_costed,
         case when d.level = 'line' then l.unit_cost * l.qty_eff else ord.cost end as cost,
         case when d.level = 'line'
              then case when l.unit_cost is null and coalesce(l.qty_eff, 0) > 0 then 1 else 0 end
              else coalesce(ord.uncosted_lines, 0) end                           as uncosted_lines
  from shop_order_discounts d
  join live o on o.id = d.order_id
  left join ln l on l.id = d.line_id
  left join ord on ord.order_id = d.order_id
  left join cart on cart.order_id = d.order_id
),
agg as (
  select p.rule_id,
         count(distinct p.order_id) filter (where p.discount > 0)                  as orders,
         coalesce(sum(p.units) filter (where p.discount > 0), 0)                   as units,
         count(distinct p.merchant) filter (where p.discount > 0)                  as merchants,
         count(distinct p.salesman_id) filter (where p.discount > 0)               as reps,
         round(coalesce(sum(p.list_value) filter (where p.discount > 0), 0), 3)    as list_value_bhd,
         round(coalesce(sum(p.discount), 0), 3)                                    as discount_bhd,
         round(coalesce(sum(p.discount_placed), 0), 3)                             as discount_placed_bhd,
         round(sum(p.revenue_costed) filter (where p.discount > 0) / (1 + (select r from vat)), 3) as revenue_ex_vat_bhd,
         round(sum(p.cost) filter (where p.discount > 0), 3)                       as cost_bhd,
         coalesce(sum(p.uncosted_lines) filter (where p.discount > 0), 0)          as uncosted_lines,
         count(*) filter (where p.clamped)                                         as clamp_count,
         min(p.created_at) filter (where p.discount > 0)                           as first_order_at,
         max(p.created_at) filter (where p.discount > 0)                           as last_order_at
  from per_row p
  group by p.rule_id
)
select r.id                                                         as rule_id,
       r.name,
       r.kind,
       case when r.kind in ('cart_value', 'coupon') then 'cart' else 'line' end as level,
       r.is_active,
       r.archived_at,
       r.starts_at,
       r.ends_at,
       r.max_uses,
       coalesce(r.uses, 0)                                          as uses,
       coalesce(a.orders, 0)                                        as orders,
       coalesce(a.units, 0)                                         as units,
       coalesce(a.merchants, 0)                                     as merchants,
       coalesce(a.reps, 0)                                          as reps,
       coalesce(a.list_value_bhd, 0)                                as list_value_bhd,
       coalesce(a.discount_bhd, 0)                                  as discount_bhd,
       coalesce(a.discount_placed_bhd, 0)                           as discount_placed_bhd,
       a.revenue_ex_vat_bhd,
       a.cost_bhd,
       round(a.revenue_ex_vat_bhd - a.cost_bhd, 3)                  as gm_bhd,
       round(100 * (a.revenue_ex_vat_bhd - a.cost_bhd) / nullif(a.revenue_ex_vat_bhd, 0), 1) as gm_pct,
       coalesce(a.uncosted_lines, 0)                                as uncosted_lines,
       coalesce(a.clamp_count, 0)                                   as clamp_count,
       a.first_order_at,
       a.last_order_at
from discount_rules r
left join agg a on a.rule_id = r.id;

revoke all on v_offer_performance from anon, authenticated;
grant select on v_offer_performance to yq_readonly;
comment on view v_offer_performance is
  'R7d: per discount rule (archived included) - orders, units, merchants, reps, list value, discount cost (confirmed, else placed), revenue ex-VAT, landed cost, GM after discount, clamps. Cancelled and test orders excluded. Aggregates only. yq_readonly only.';

-- ── 6b. v_followup_lift ─────────────────────────────────────────────────────────
-- One exposure per (week, rep, shop): the first day that week the shop was served (or held). Outcome:
-- Focus Accessories invoices (no cash customers, no giveaways) within 14 days AFTER that day, by any
-- rep. `complete` = every exposure's 14 days are inside the sales data already loaded. `tapped` = the
-- rep tapped the shop (audit_log 'followup.tap') within the same 14 days — served arm only by nature.
create or replace view v_followup_lift as
with mx as (select max(sale_date) as d from v_sales),
ex as (
  select date_trunc('week', e.served_on)::date                        as week_start,
         e.salesman_id,
         e.shop_key,
         substr(e.shop_key, 3)                                         as shop,
         min(e.served_on)                                              as served_on,
         bool_or(e.holdout)                                            as holdout
  from followup_exposures e
  where e.shop_key like 'f:%'
  group by 1, 2, 3, 4
),
sales as (
  select v.customer_name, v.sale_date, count(distinct v.invoice_no) as invoices, sum(v.net_bhd) as net_bhd
  from v_sales v
  where v.division = 'Accessories' and not coalesce(v.is_giveaway, false) and not coalesce(v.is_cash_customer, false)
    and v.customer_name is not null
    and v.sale_date > (select coalesce(min(served_on), current_date) from followup_exposures)
  group by 1, 2
),
taps as (
  select (a.detail->>'salesman_id')           as salesman_id,
         (a.detail->>'shop')                  as shop,
         (a.ts at time zone 'Asia/Bahrain')::date as tap_day
  from audit_log a
  where a.event = 'followup.tap'
    and a.ts >= (select coalesce(min(served_on), current_date) from followup_exposures)
),
outcome as (
  select x.week_start, x.salesman_id, x.holdout, x.served_on,
         coalesce(sum(s.invoices), 0)                                   as invoices,
         coalesce(sum(s.net_bhd), 0)                                    as net_bhd,
         exists (select 1 from taps t
                 where t.salesman_id = x.salesman_id::text and t.shop = x.shop
                   and t.tap_day between x.served_on and x.served_on + 14) as tapped
  from ex x
  left join sales s on s.customer_name = x.shop
                   and s.sale_date > x.served_on and s.sale_date <= x.served_on + 14
  group by x.week_start, x.salesman_id, x.shop_key, x.shop, x.holdout, x.served_on
)
select o.week_start,
       o.salesman_id,
       sm.name                                                          as salesman_name,
       case when o.holdout then 'holdout' else 'served' end             as arm,
       count(*)                                                         as shops,
       count(*) filter (where o.invoices > 0)                           as bought_14d,
       round(100.0 * count(*) filter (where o.invoices > 0) / nullif(count(*), 0), 1) as buy_rate_pct,
       round(sum(o.net_bhd), 3)                                         as net_bhd_14d,
       round(sum(o.net_bhd) / nullif(count(*), 0), 3)                   as net_bhd_per_shop,
       count(*) filter (where o.tapped)                                 as tapped,
       bool_and(o.served_on + 14 <= mx.d)                               as complete,
       mx.d                                                             as data_through
from outcome o
cross join mx
left join salesmen sm on sm.id = o.salesman_id
group by o.week_start, o.salesman_id, sm.name, o.holdout, mx.d;

revoke all on v_followup_lift from anon, authenticated;
grant select on v_followup_lift to yq_readonly;
comment on view v_followup_lift is
  'R7d: follow-up hold-out readout per week, rep and arm (served / holdout): shops, bought within 14 days (Focus Accessories invoices), BHD, taps, complete = the 14-day windows are all inside the loaded sales. No shop names. yq_readonly only.';

-- ── self-check ──────────────────────────────────────────────────────────────────
do $$
declare
  o text;
  c text;
begin
  foreach o in array array['shop_order_discounts', 'shop_badge_log', 'followup_exposures',
                           'v_offer_performance', 'v_followup_lift'] loop
    if to_regclass('public.' || o) is null then
      raise exception 'r7d_offer_ledger: % missing', o;
    end if;
    if exists (select 1 from information_schema.role_table_grants
               where table_schema = 'public' and table_name = o and grantee in ('anon', 'authenticated')) then
      raise exception 'r7d_offer_ledger: % must not be granted to anon/authenticated', o;
    end if;
  end loop;
  foreach o in array array['shop_order_discounts', 'shop_badge_log', 'followup_exposures'] loop
    if not (select relrowsecurity from pg_class where oid = ('public.' || o)::regclass) then
      raise exception 'r7d_offer_ledger: % must have row level security on', o;
    end if;
  end loop;
  foreach o in array array['v_offer_performance', 'v_followup_lift'] loop
    if exists (select 1 from pg_class where oid = ('public.' || o)::regclass
               and coalesce(reloptions::text, '') like '%security_invoker%') then
      raise exception 'r7d_offer_ledger: % must not be security_invoker (yq_readonly reads the view, not the tables)', o;
    end if;
    if exists (select 1 from pg_roles where rolname = 'yq_readonly')
       and not has_table_privilege('yq_readonly', 'public.' || o, 'SELECT') then
      raise exception 'r7d_offer_ledger: yq_readonly cannot read %', o;
    end if;
  end loop;
  -- nothing personal travels in the two views
  foreach c in array array['customer_name', 'customer_phone', 'customer_email', 'customer_id', 'shop', 'shop_key',
                           'token', 'phone', 'email', 'device_id'] loop
    if exists (select 1 from information_schema.columns where table_schema = 'public'
               and table_name in ('v_offer_performance', 'v_followup_lift') and column_name = c) then
      raise exception 'r7d_offer_ledger: a view must not carry %', c;
    end if;
  end loop;
  foreach c in array array['archived_at', 'archived_by'] loop
    if not exists (select 1 from information_schema.columns where table_schema = 'public'
                   and table_name = 'discount_rules' and column_name = c)
       or not exists (select 1 from information_schema.columns where table_schema = 'public'
                      and table_name = 'shop_campaigns' and column_name = c) then
      raise exception 'r7d_offer_ledger: %.% missing', 'discount_rules / shop_campaigns', c;
    end if;
  end loop;
  foreach o in array array['anon', 'authenticated'] loop
    if exists (select 1 from pg_roles where rolname = o)
       and (has_function_privilege(o, 'public.shop_coupon_reserve(bigint, boolean)', 'EXECUTE')
            or has_function_privilege(o, 'public.shop_coupon_release(bigint)', 'EXECUTE')) then
      raise exception 'r7d_offer_ledger: the coupon functions must be service_role only (% can run them)', o;
    end if;
  end loop;
  -- one row per rule, never more
  if (select count(*) from v_offer_performance) <> (select count(*) from discount_rules) then
    raise exception 'r7d_offer_ledger: v_offer_performance does not have one row per rule';
  end if;
  raise notice 'r7d_offer_ledger: ok (ledger, coupon RPCs, badge log, exposures, two yq_readonly views)';
end $$;
