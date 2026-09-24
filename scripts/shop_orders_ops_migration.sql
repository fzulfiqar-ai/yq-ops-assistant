-- Order write safety, release R1 (24-Sep-2026, trust plan M2). Additive, idempotent.
--   Rehearse: python -m scripts.apply_sql scripts/shop_orders_ops_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/shop_orders_ops_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/shop_orders_ops_reverse.sql
--
-- Why: the 17 live orders include two placed while testing (90 and 97). Plan §3: nothing on an
-- order is ever deleted — a test order is FLAGGED and the figures (analytics, rep KPIs, the
-- checkout quick-pick) leave it out. The alert retry (A) and the confirmed-values email (R4)
-- need their own columns. Every column has a default or is nullable, so no row changes.
--
-- The code tolerates these columns being absent (app/shop.py has_column probes the table once
-- and degrades to the old shape), so this can land before or after the R1 deploy. The test
-- flag itself (PATCH /shop/orders/{id}/test) answers 400 until the column exists.

-- ── shop_orders ────────────────────────────────────────────────────────────────
alter table shop_orders add column if not exists is_test             boolean     not null default false;
alter table shop_orders add column if not exists confirm_notified_at timestamptz;
alter table shop_orders add column if not exists notify_attempts     integer     not null default 0;

comment on column shop_orders.is_test is
  'Placed while testing (orders 90 and 97). Stays on record, never deleted; excluded from every figure. Set via PATCH /shop/orders/{id}/test (admin).';
comment on column shop_orders.confirm_notified_at is
  'When the merchant was told the order is confirmed (with confirmed values); NULL = not yet / every channel failed.';
comment on column shop_orders.notify_attempts is
  'Delivery attempts for the new-order alert (shop_jobs.notify_retry stops at 3).';

-- ── shop_order_lines: the confirmed price per line (tiers move with confirmed quantities) ──
alter table shop_order_lines add column if not exists unit_price_confirmed numeric(12,3);
alter table shop_order_lines add column if not exists line_total_confirmed numeric(12,3);

comment on column shop_order_lines.unit_price_confirmed is
  'Unit price at the confirmed quantity (re-priced by price_cart at confirm); NULL until confirmed.';
comment on column shop_order_lines.line_total_confirmed is
  'qty_confirmed x unit_price_confirmed as re-priced at confirm; NULL until confirmed.';

-- Both tables are RLS-on, service-role only (shop_migration.sql). Re-assert: a new column
-- inherits the table grant, so nothing changes here — this just makes the self-check honest.
revoke all on shop_orders, shop_order_lines from anon, authenticated;

-- ── self-check ─────────────────────────────────────────────────────────────────
do $$
declare
  want  text[] := array['is_test', 'confirm_notified_at', 'notify_attempts'];
  c     text;
begin
  foreach c in array want loop
    if not exists (select 1 from information_schema.columns
                   where table_schema = 'public' and table_name = 'shop_orders' and column_name = c) then
      raise exception 'shop_orders.% missing', c;
    end if;
  end loop;
  foreach c in array array['unit_price_confirmed', 'line_total_confirmed'] loop
    if not exists (select 1 from information_schema.columns
                   where table_schema = 'public' and table_name = 'shop_order_lines' and column_name = c) then
      raise exception 'shop_order_lines.% missing', c;
    end if;
  end loop;
  if exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'shop_orders' and column_name = 'is_test'
               and (is_nullable = 'YES' or column_default is distinct from 'false')) then
    raise exception 'shop_orders.is_test must be NOT NULL DEFAULT false';
  end if;
  if exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'shop_orders' and column_name = 'notify_attempts'
               and (is_nullable = 'YES' or column_default is distinct from '0')) then
    raise exception 'shop_orders.notify_attempts must be NOT NULL DEFAULT 0';
  end if;
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
  raise notice 'shop_orders_ops: ok (5 columns present, defaults right, no anon/authenticated grants)';
end $$;
