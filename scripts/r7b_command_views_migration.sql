-- Command Centre order view — release R7b, Sprint 4 "Management Command Centre" (plan §8, §24).
-- Additive, idempotent.
--   Rehearse: python -m scripts.apply_sql scripts/r7b_command_views_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/r7b_command_views_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/r7b_command_views_reverse.sql
--   Order:    after r7_focus_links_migration.sql (it reads shop_order_focus_links and
--             v_shop_focus_candidates; the first block below stops with a clear message if either is
--             missing). Before or after r7c_order_lines_qty_migration.sql — either order works (below).
--             Reverse THIS file before r7_focus_links_reverse.sql (that one drops what this view reads).
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
--     whether a Focus invoice number was typed (has_invoice_no, a flag — never the number), the
--     confirmed Focus links (count + first decision time) from shop_order_focus_links, and
--     (appended) units the rep added, units delivered and has_suggestion.
--
-- Units are the SHOP's request: units_ordered / units_confirmed / units_delivered count only the
-- lines the shop asked for. A line the rep added or a substitute he put in (R7c:
-- shop_order_lines.added_at_stage is set) is counted apart in units_added, so the accepted rate
-- (confirmed ÷ ordered) never reads above what the shop asked for. added_at_stage and qty_delivered
-- arrive with r7c_order_lines_qty_migration.sql; the view reads them through to_jsonb(l) (a
-- missing key is NULL), so the same definition answers before r7c (every line is requested,
-- delivered = confirmed), after it, and after r7c's reverse — and neither file has to touch the
-- other (the whole-row reference does not pin a column, so r7c's reverse can drop them).
-- Each requested line counts at most what the shop asked for: a rep who confirms 20 on a line of 10
-- (the editor allows it) adds 10 to units_confirmed and the other 10 to units_raised, so the
-- accepted rate can never read above 100 % or hide a cut on another line; units_delivered is capped
-- the same way.
--
-- reopened_at (appended; R7c, read through to_jsonb(o) so it is NULL before r7c and pins nothing):
-- an order reopened from Cancelled is Received again, and its confirm / waiting clocks run from the
-- reopen, not from the original created_at (app/metrics.py).
--
-- has_suggestion: v_shop_focus_candidates has a rank-1 row for the order — a Focus invoice was
-- FOUND for it and waits for a person to accept or reject it. The Command Centre tells "awaiting
-- acceptance" apart from "no Focus invoice found" with it; only accepted links (focus_links_n)
-- count as matched.
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
  if to_regclass('public.shop_order_focus_links') is null or to_regclass('public.v_shop_focus_candidates') is null then
    raise exception 'r7b_command_views: shop_order_focus_links / v_shop_focus_candidates is missing - apply scripts/r7_focus_links_migration.sql first';
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
       k.first_linked_at,
       -- appended (R7b review): rep-added lines apart, the delivered quantity, a found-but-unaccepted invoice
       coalesce(l.units_added, 0)                                       as units_added,
       l.units_delivered                                                as units_delivered,
       (c.order_id is not null)                                         as has_suggestion,
       -- appended (R7b review, round 2): units confirmed beyond the request; the reopen time (R7c)
       coalesce(l.units_raised, 0)                                      as units_raised,
       (to_jsonb(o) ->> 'reopened_at')::timestamptz                     as reopened_at
from shop_orders o
left join (select l.order_id,
                  -- the shop's request only: a line the rep added (added_at_stage set, R7c) is counted apart;
                  -- each requested line counts at most its requested qty (the surplus is units_raised)
                  sum(l.qty) filter (where r.j ->> 'added_at_stage' is null)            as units_ordered,
                  sum(case when l.qty_confirmed is null then null else least(l.qty_confirmed, l.qty) end)
                    filter (where r.j ->> 'added_at_stage' is null)                     as units_confirmed,  -- NULL until confirmed
                  count(*)                                                              as lines_n,
                  sum(coalesce(l.qty_confirmed, l.qty))
                    filter (where r.j ->> 'added_at_stage' is not null)                 as units_added,
                  sum(case when coalesce((r.j ->> 'qty_delivered')::integer, l.qty_confirmed) is null then null
                           else least(coalesce((r.j ->> 'qty_delivered')::integer, l.qty_confirmed), l.qty) end)
                    filter (where r.j ->> 'added_at_stage' is null)                     as units_delivered,
                  sum(greatest(coalesce(l.qty_confirmed, 0) - l.qty, 0))
                    filter (where r.j ->> 'added_at_stage' is null)                     as units_raised
           from shop_order_lines l
           -- to_jsonb(l): the R7c columns read as NULL until r7c_order_lines_qty_migration.sql adds them
           cross join lateral (select to_jsonb(l) as j) r
           group by l.order_id) l on l.order_id = o.id
left join (select order_id,
                  count(*)                               as links_n,
                  min(coalesce(decided_at, created_at))  as first_linked_at
           from shop_order_focus_links
           where state = 'confirmed'
           group by order_id) k on k.order_id = o.id
left join (select distinct order_id
           from v_shop_focus_candidates
           where rank = 1) c on c.order_id = o.id;

revoke all on v_command_orders from anon, authenticated;
grant select on v_command_orders to yq_readonly;

comment on view v_command_orders is
  'Command Centre (R7b): one row per marketplace order with status, rep, is_test, lifecycle times, the units the shop asked for vs confirmed vs delivered (rep-added lines apart in units_added), confirmed Focus links and whether a Focus invoice was found and awaits acceptance (has_suggestion). No names, phones, tokens or invoice numbers. yq_readonly only.';
comment on column v_command_orders.units_ordered is
  'Sum of shop_order_lines.qty over the lines the shop asked for (added_at_stage NULL); a line the rep added or substituted is in units_added.';
comment on column v_command_orders.units_confirmed is
  'Sum of shop_order_lines.qty_confirmed over the lines the shop asked for, each line capped at its requested qty (the surplus is units_raised): NULL until the order is confirmed; a removed or substituted line counts 0.';
comment on column v_command_orders.focus_links_n is
  'Confirmed rows in shop_order_focus_links for this order (a person accepted the Focus invoice).';
comment on column v_command_orders.units_added is
  'Units on lines the rep added or substituted (added_at_stage set, R7c), at their confirmed quantity; 0 before r7c.';
comment on column v_command_orders.units_delivered is
  'Delivered units of the lines the shop asked for: qty_delivered, else qty_confirmed (plain Delivered, or before r7c), each line capped at its requested qty. Read it on a delivered order.';
comment on column v_command_orders.has_suggestion is
  'v_shop_focus_candidates has a rank-1 row for the order: a Focus invoice was found and awaits a person''s accept / reject. False once a link is confirmed (the candidates view stops suggesting).';
comment on column v_command_orders.units_raised is
  'Units a rep confirmed BEYOND what the shop asked for on its own lines (qty_confirmed above qty); kept out of units_confirmed so the accepted rate never passes 100 %.';
comment on column v_command_orders.reopened_at is
  'When an admin last reopened the order (R7c shop_orders.reopened_at, read through to_jsonb; NULL before r7c). The confirm and waiting clocks of a reopened order start here.';

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
                           'lines_n', 'has_invoice_no', 'focus_links_n', 'first_linked_at', 'units_added',
                           'units_delivered', 'has_suggestion', 'units_raised', 'reopened_at'] loop
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
  raise notice 'r7b_command_views: ok (v_command_orders, 27 columns, nothing personal, yq_readonly only)';
end $$;
