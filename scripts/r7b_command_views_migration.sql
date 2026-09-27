-- Command Centre order view — release R7b, Sprint 4 "Management Command Centre" (plan §8, §24).
-- Additive, idempotent.
--   Rehearse: python -m scripts.apply_sql scripts/r7b_command_views_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/r7b_command_views_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/r7b_command_views_reverse.sql
--   Order:    after r7_focus_links_migration.sql (it reads shop_order_focus_links; the first block
--             below stops with a clear message if that table is missing).
--
-- Why: the Command Centre (app/metrics.py) reads everything through the read-only RPC as
-- yq_readonly. The only marketplace order view that role may read, v_shop_orders_agent, has no
-- is_test flag, no confirmed quantities and no Focus link, so test orders would count, the
-- accepted rate could not be measured and the invoice match rate would be empty. This adds ONE
-- view with exactly those facts per order and nothing personal:
--
--   v_command_orders — one row per marketplace order: status, source, who placed it, the rep
--     (id + name), whether a merchant account is attached (has_customer, a flag — never the id,
--     the name or the phone), is_test, the lifecycle timestamps, the order and confirmed totals,
--     units ordered vs units confirmed and the line count (one GROUP BY over shop_order_lines),
--     whether a Focus invoice number was typed (has_invoice_no, a flag — never the number), and the
--     confirmed Focus links (count + first decision time) from shop_order_focus_links.
--
-- No shop name, merchant name, phone, email, address, token, IP or invoice number is in it. It is
-- granted to yq_readonly ONLY (the Command Centre's read path) and revoked from anon and
-- authenticated. Not security_invoker: run_readonly_query runs as yq_readonly, which is granted
-- the view and not the tables beneath it (docs/MIGRATIONS.md, 16-Sep-2026 rules).
--
-- Nothing here writes a row. The API probes the view and, until it exists, reads
-- v_shop_orders_agent instead (test orders not excluded, no accepted rate, the invoice match rate
-- empty) and says so on the page, so the API may deploy before this runs.

do $$
begin
  if to_regclass('public.shop_order_focus_links') is null then
    raise exception 'r7b_command_views: shop_order_focus_links is missing - apply scripts/r7_focus_links_migration.sql first';
  end if;
end $$;

create or replace view v_command_orders as
select o.id,
       o.order_no,
       o.status,
       o.source,
       o.placed_by,
       o.salesman_id,
       o.salesman_name,
       (o.customer_id is not null)                                      as has_customer,
       coalesce(o.is_test, false)                                       as is_test,
       o.created_at,
       o.assigned_at,
       o.confirmed_at,
       o.delivered_at,
       o.cancelled_at,
       o.total_bhd,
       o.total_confirmed_bhd,
       coalesce(l.units_ordered, 0)                                     as units_ordered,
       l.units_confirmed,
       coalesce(l.lines_n, 0)                                           as lines_n,
       (nullif(btrim(coalesce(o.focus_invoice_no, '')), '') is not null) as has_invoice_no,
       coalesce(k.links_n, 0)                                           as focus_links_n,
       k.first_linked_at
from shop_orders o
left join (select order_id,
                  sum(qty)            as units_ordered,
                  sum(qty_confirmed)  as units_confirmed,     -- NULL until the order is confirmed
                  count(*)            as lines_n
           from shop_order_lines
           group by order_id) l on l.order_id = o.id
left join (select order_id,
                  count(*)                               as links_n,
                  min(coalesce(decided_at, created_at))  as first_linked_at
           from shop_order_focus_links
           where state = 'confirmed'
           group by order_id) k on k.order_id = o.id;

revoke all on v_command_orders from anon, authenticated;
grant select on v_command_orders to yq_readonly;

comment on view v_command_orders is
  'Command Centre (R7b): one row per marketplace order with status, rep, is_test, lifecycle times, units ordered vs confirmed and confirmed Focus links. No names, phones, tokens or invoice numbers. yq_readonly only.';
comment on column v_command_orders.units_confirmed is
  'Sum of shop_order_lines.qty_confirmed: NULL until the order is confirmed; a removed line counts 0.';
comment on column v_command_orders.focus_links_n is
  'Confirmed rows in shop_order_focus_links for this order (a person accepted the Focus invoice).';

-- ── self-check ─────────────────────────────────────────────────────────────────
do $$
declare
  c text;
begin
  if to_regclass('public.v_command_orders') is null then
    raise exception 'r7b_command_views: v_command_orders missing';
  end if;
  foreach c in array array['id', 'order_no', 'status', 'source', 'placed_by', 'salesman_id', 'salesman_name',
                           'has_customer', 'is_test', 'created_at', 'assigned_at', 'confirmed_at', 'delivered_at',
                           'cancelled_at', 'total_bhd', 'total_confirmed_bhd', 'units_ordered', 'units_confirmed',
                           'lines_n', 'has_invoice_no', 'focus_links_n', 'first_linked_at'] loop
    if not exists (select 1 from information_schema.columns
                   where table_schema = 'public' and table_name = 'v_command_orders' and column_name = c) then
      raise exception 'r7b_command_views: v_command_orders.% missing', c;
    end if;
  end loop;
  -- nothing personal travels in this view
  foreach c in array array['customer_name', 'customer_phone', 'customer_email', 'customer_shop', 'customer_area',
                           'customer_id', 'token', 'ip_hash', 'ua', 'device_id', 'focus_invoice_no', 'note'] loop
    if exists (select 1 from information_schema.columns
               where table_schema = 'public' and table_name = 'v_command_orders' and column_name = c) then
      raise exception 'r7b_command_views: v_command_orders must not carry %', c;
    end if;
  end loop;
  if exists (select 1 from pg_class where oid = 'public.v_command_orders'::regclass
             and coalesce(reloptions::text, '') like '%security_invoker%') then
    raise exception 'r7b_command_views: v_command_orders must not be security_invoker (yq_readonly reads the view, not the tables)';
  end if;
  if exists (select 1 from information_schema.role_table_grants
             where table_schema = 'public' and table_name = 'v_command_orders' and grantee in ('anon', 'authenticated')) then
    raise exception 'r7b_command_views: v_command_orders must not be granted to anon/authenticated';
  end if;
  if exists (select 1 from pg_roles where rolname = 'yq_readonly')
     and not has_table_privilege('yq_readonly', 'public.v_command_orders', 'SELECT') then
    raise exception 'r7b_command_views: yq_readonly cannot read v_command_orders';
  end if;
  -- one row per order, never more (the two joins are pre-aggregated per order)
  if (select count(*) from v_command_orders) <> (select count(*) from shop_orders) then
    raise exception 'r7b_command_views: v_command_orders does not have one row per shop order';
  end if;
  raise notice 'r7b_command_views: ok (v_command_orders, 22 columns, nothing personal, yq_readonly only)';
end $$;
