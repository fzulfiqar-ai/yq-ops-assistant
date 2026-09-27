-- Order heart — Sprint 3 / R7c (27-Sep-2026, owner-simplified flow). Additive, idempotent.
--   Rehearse: python -m scripts.apply_sql scripts/r7c_order_lines_qty_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/r7c_order_lines_qty_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/r7c_order_lines_qty_reverse.sql
--
-- What a rep or a shop sees: Received → Confirmed → Delivered (or Cancelled). Every line keeps
-- three numbers — qty (requested, never changed), qty_confirmed, qty_delivered (new) — and every
-- change carries a reason chip. The rep may add or substitute lines (priced at the current book);
-- the landed cost the pricing engine floored a line with is snapshotted on the line.
--
-- Live constraint names read read-only on 27-Sep-2026 before this was written:
--   shop_order_lines_line_status_check   CHECK (line_status IN ('ok','changed','removed','backorder'))
--   shop_orders_cancel_reason_code_check CHECK (cancel_reason_code IS NULL OR IN
--                                         ('out_of_stock','customer_request','duplicate','test','price_issue','other'))
-- Both are WIDENED here (every value they allowed stays allowed). No row is written, no column
-- rewritten: every new column is nullable with no default, so existing orders, lines and events
-- are untouched. The code runs before this file (app/shop_heart.py probes the columns and maps the
-- new line statuses to the old ones), so the API may deploy first.
--
-- The two R7b read views that count units (v_command_orders, r7b_command_views_migration.sql, and
-- v_agent_shop_lines, r7b_ai_head_migration.sql) read added_at_stage and qty_delivered through
-- to_jsonb(l): nothing here recreates them. Before this file every line reads as requested and
-- delivered = confirmed; after it a rep-added / substitute line is counted apart from the shop's
-- request. Either file may run first, and the reverse below can drop the columns without touching
-- either view (a whole-row reference pins no column).

-- ── shop_order_lines: delivered quantity, the change's reason, substitutes, added lines, cost ──
alter table shop_order_lines add column if not exists qty_delivered        integer;
alter table shop_order_lines add column if not exists change_reason        text;
alter table shop_order_lines add column if not exists substitute_item_code text;
alter table shop_order_lines add column if not exists substitute_for_line  bigint;
alter table shop_order_lines add column if not exists added_at_stage       text;
alter table shop_order_lines add column if not exists unit_cost_bhd        numeric(12,4);
alter table shop_order_lines add column if not exists cost_source          text;

alter table shop_order_lines drop constraint if exists shop_order_lines_qty_delivered_check;
alter table shop_order_lines add constraint shop_order_lines_qty_delivered_check
  check (qty_delivered is null or qty_delivered >= 0);

alter table shop_order_lines drop constraint if exists shop_order_lines_change_reason_check;
alter table shop_order_lines add constraint shop_order_lines_change_reason_check
  check (change_reason is null or change_reason in
         ('out_of_stock', 'discontinued', 'price', 'customer_changed', 'substituted', 'damaged', 'other'));

alter table shop_order_lines drop constraint if exists shop_order_lines_added_at_stage_check;
alter table shop_order_lines add constraint shop_order_lines_added_at_stage_check
  check (added_at_stage is null or added_at_stage in ('confirm', 'amend', 'delivery'));

alter table shop_order_lines drop constraint if exists shop_order_lines_unit_cost_bhd_check;
alter table shop_order_lines add constraint shop_order_lines_unit_cost_bhd_check
  check (unit_cost_bhd is null or unit_cost_bhd >= 0);

-- a substitute points at the line it replaces (same table; set null if that line ever goes)
alter table shop_order_lines drop constraint if exists shop_order_lines_substitute_for_line_fkey;
alter table shop_order_lines add constraint shop_order_lines_substitute_for_line_fkey
  foreign key (substitute_for_line) references shop_order_lines(id) on delete set null;

-- widened: the three R7c dispositions join the four that exist
alter table shop_order_lines drop constraint if exists shop_order_lines_line_status_check;
alter table shop_order_lines add constraint shop_order_lines_line_status_check
  check (line_status in ('ok', 'changed', 'removed', 'backorder', 'substituted', 'added', 'unavailable'));

comment on column shop_order_lines.qty_delivered is
  'What was handed over (R7c). Plain Delivered = qty_confirmed; "Deliver with changes" records the real count (a difference carries change_reason). NULL until delivered.';
comment on column shop_order_lines.change_reason is
  'Why this line differs from the request (the rep''s chip): out_of_stock, discontinued, price, customer_changed, substituted, damaged, other. The shop reads a public label, never the internal note.';
comment on column shop_order_lines.substitute_item_code is
  'On a substituted line (qty_confirmed 0): the item that replaced it.';
comment on column shop_order_lines.substitute_for_line is
  'On a substitute line: the id of the line it replaces.';
comment on column shop_order_lines.added_at_stage is
  'Set on a line the rep added (confirm / amend / delivery) — not requested by the shop; qty = qty_confirmed = the quantity added, priced at the book of that moment.';
comment on column shop_order_lines.unit_cost_bhd is
  'Landed cost per unit when the line was written (MRN, else purchase_costs — see cost_source): margin on the cost as it was.';
comment on column shop_order_lines.cost_source is
  'Where unit_cost_bhd came from: mrn | purchase_costs (app/shop.py _load_costs).';

-- ── shop_orders: the ETA as a date, the admin's undo ──────────────────────────────────────────
alter table shop_orders add column if not exists expected_delivery_date date;
alter table shop_orders add column if not exists reopened_at            timestamptz;
alter table shop_orders add column if not exists reopened_by            text;
alter table shop_orders add column if not exists reopen_reason          text;

-- widened: a staff cancel may now say the order stays under the minimum order value
alter table shop_orders drop constraint if exists shop_orders_cancel_reason_code_check;
alter table shop_orders add constraint shop_orders_cancel_reason_code_check
  check (cancel_reason_code is null or cancel_reason_code in
         ('out_of_stock', 'customer_request', 'duplicate', 'test', 'price_issue', 'below_minimum', 'other'));

comment on column shop_orders.expected_delivery_date is
  'The ETA chip resolved to a Bahrain calendar date (Today / Tomorrow / a weekday / a picked date); NULL for "with my next visit". expected_delivery keeps the words.';
comment on column shop_orders.reopened_at is
  'Last time an admin reopened this order (Delivered → Confirmed or Cancelled → Received, within 7 days, reason required). The ''reopened'' event and shop_admin_audit hold every one.';
comment on column shop_orders.reopened_by is 'The admin who last reopened the order.';
comment on column shop_orders.reopen_reason is 'Why the order was last reopened.';

-- Both tables are RLS-on, service-role only (shop_migration.sql). A new column inherits the table
-- grant, so nothing changes here — re-asserted so the self-check below is honest.
revoke all on shop_orders, shop_order_lines from anon, authenticated;

-- ── self-check ─────────────────────────────────────────────────────────────────
do $$
declare
  c   text;
  def text;
begin
  foreach c in array array['qty_delivered', 'change_reason', 'substitute_item_code', 'substitute_for_line',
                           'added_at_stage', 'unit_cost_bhd', 'cost_source'] loop
    if not exists (select 1 from information_schema.columns
                   where table_schema = 'public' and table_name = 'shop_order_lines' and column_name = c
                     and is_nullable = 'YES' and column_default is null) then
      raise exception 'shop_order_lines.% missing (or not a plain nullable column)', c;
    end if;
  end loop;
  foreach c in array array['expected_delivery_date', 'reopened_at', 'reopened_by', 'reopen_reason'] loop
    if not exists (select 1 from information_schema.columns
                   where table_schema = 'public' and table_name = 'shop_orders' and column_name = c
                     and is_nullable = 'YES' and column_default is null) then
      raise exception 'shop_orders.% missing (or not a plain nullable column)', c;
    end if;
  end loop;
  select pg_get_constraintdef(oid) into def from pg_constraint
   where conrelid = 'shop_order_lines'::regclass and conname = 'shop_order_lines_line_status_check';
  foreach c in array array['ok', 'changed', 'removed', 'backorder', 'substituted', 'added', 'unavailable'] loop
    if def is null or position('''' || c || '''' in def) = 0 then
      raise exception 'shop_order_lines_line_status_check does not allow %', c;
    end if;
  end loop;
  select pg_get_constraintdef(oid) into def from pg_constraint
   where conrelid = 'shop_orders'::regclass and conname = 'shop_orders_cancel_reason_code_check';
  foreach c in array array['out_of_stock', 'customer_request', 'duplicate', 'test', 'price_issue',
                           'below_minimum', 'other'] loop
    if def is null or position('''' || c || '''' in def) = 0 then
      raise exception 'shop_orders_cancel_reason_code_check does not allow %', c;
    end if;
  end loop;
  if exists (select 1 from information_schema.role_table_grants
             where table_schema = 'public' and table_name in ('shop_orders', 'shop_order_lines')
               and grantee in ('anon', 'authenticated')) then
    raise exception 'shop_orders / shop_order_lines must not be granted to anon/authenticated';
  end if;
  if exists (select 1 from information_schema.column_privileges
             where table_schema = 'public' and table_name in ('shop_orders', 'shop_order_lines')
               and grantee in ('anon', 'authenticated')) then
    raise exception 'shop_orders / shop_order_lines have column grants to anon/authenticated';
  end if;
  raise notice 'r7c_order_lines_qty: ok (11 columns, 2 checks widened, 5 added, no anon/authenticated grants)';
end $$;
