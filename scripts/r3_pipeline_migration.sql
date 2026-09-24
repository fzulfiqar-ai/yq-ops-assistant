-- Order pipeline states, customer attribution and the admin audit — release R3b (24-Sep-2026,
-- trust plan §9 Step 3 items 5-7; migration M12 + the pipeline columns). Additive, idempotent.
--   Rehearse: python -m scripts.apply_sql scripts/r3_pipeline_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/r3_pipeline_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/r3_pipeline_reverse.sql
--
-- What this adds:
--   1. shop_admin_audit (M12): append-only — who, when, entity, action, before/after jsonb — for every
--      admin write to settings, discount rules, campaigns, salesmen, target imports, upcoming items
--      and merchant attribution (app/shop_audit.py). A trigger refuses UPDATE and DELETE, so even the
--      service role can only add rows.
--   2. shop_orders: cancel_reason_code (the fixed list a staff cancel picks from; the text stays in
--      cancel_reason), paid_at (stamped when payment_status becomes 'paid'), returned_bhd (the sum of
--      'returned' events, for the list pill). payment_status / payment_method / focus_invoice_no
--      already exist (marketplace_migration.sql).
--   3. v_shop_focus_recon: delivered orders against v_sales by Focus invoice number — missing invoice,
--      invoice not found, salesman mismatch, amount mismatch. It joins customer names, so it is
--      service-role only (never yq_readonly, never anon/authenticated).
--
-- Nothing here changes a row: every column is nullable, no UPDATE, no DELETE. The code tolerates
-- these objects being absent (has_column probes / empty lists with a hint), so the API may deploy
-- before or after this runs.

-- ── 1. shop_admin_audit (M12) ─────────────────────────────────────────────────
create table if not exists shop_admin_audit (
  id         bigint generated always as identity primary key,
  at         timestamptz not null default now(),
  actor      text        not null,
  entity     text        not null,     -- settings | discount_rule | campaign | salesman | target | upcoming | shop_customer | order
  entity_id  text,
  action     text        not null,     -- create | update | delete | import | assign
  before     jsonb,
  after      jsonb
);
create index if not exists shop_admin_audit_at_idx     on shop_admin_audit (at desc);
create index if not exists shop_admin_audit_entity_idx on shop_admin_audit (entity, entity_id);
alter table shop_admin_audit enable row level security;      -- service role only; no policies
revoke all on shop_admin_audit from anon, authenticated;

comment on table shop_admin_audit is
  'Append-only admin audit (trust plan M12, 24-Sep-2026): who changed which shop setting, rule, campaign, salesman, target, upcoming item or merchant rep, with the row before and after. Rows are never updated or deleted (trigger).';

create or replace function shop_admin_audit_no_rewrite() returns trigger
language plpgsql as $$
begin
  raise exception 'shop_admin_audit is append-only (row % cannot be % )', old.id, tg_op
    using errcode = 'restrict_violation';
end $$;
revoke all on function shop_admin_audit_no_rewrite() from public, anon, authenticated;

drop trigger if exists shop_admin_audit_append_only on shop_admin_audit;
create trigger shop_admin_audit_append_only
  before update or delete on shop_admin_audit
  for each row execute function shop_admin_audit_no_rewrite();

-- ── 2. shop_orders: the pipeline facts ────────────────────────────────────────
alter table shop_orders add column if not exists cancel_reason_code text;
alter table shop_orders add column if not exists paid_at            timestamptz;
alter table shop_orders add column if not exists returned_bhd       numeric(12,3);

alter table shop_orders drop constraint if exists shop_orders_cancel_reason_code_check;
alter table shop_orders add constraint shop_orders_cancel_reason_code_check
  check (cancel_reason_code is null or cancel_reason_code in
         ('out_of_stock', 'customer_request', 'duplicate', 'test', 'price_issue', 'other'));

comment on column shop_orders.cancel_reason_code is
  'Why a staff cancel happened, from the fixed list (app/shop_pipeline.py CANCEL_REASONS); the merchant''s own cancel is customer_request. The free text stays in cancel_reason.';
comment on column shop_orders.paid_at is
  'When payment_status was last set to paid by POST /shop/orders/{id}/payment (NULL otherwise). The status is a recorded fact, not a lifecycle stage.';
comment on column shop_orders.returned_bhd is
  'Sum of the ''returned'' events on this order (BHD, 3 dp) — a convenience for the list; the events are the record.';

revoke all on shop_orders from anon, authenticated;

-- ── 3. v_shop_focus_recon: delivered orders vs Focus by invoice ───────────────
create or replace view v_shop_focus_recon as
with inv as (
  select upper(trim(invoice_no))         as invoice_key,
         min(invoice_no)                 as invoice_no,
         sum(revenue_bhd)                as invoice_total_bhd,     -- VAT-inclusive, like the order total
         sum(net_bhd)                    as invoice_net_bhd,
         max(salesman_resolved)          as focus_salesman,
         max(customer_name)              as focus_customer,
         min(sale_date)                  as invoice_date
  from v_sales
  where invoice_no is not null and trim(invoice_no) <> ''
  group by upper(trim(invoice_no))
)
select o.id                                              as order_id,
       o.order_no,
       o.status,
       o.delivered_at,
       o.customer_shop,
       o.salesman_id,
       o.salesman_name,
       s.focus_name                                      as salesman_focus_name,
       coalesce(o.total_confirmed_bhd, o.total_bhd)      as order_total_bhd,
       o.payment_status,
       o.focus_invoice_no,
       i.invoice_total_bhd,
       i.invoice_net_bhd,
       i.focus_salesman,
       i.focus_customer,
       i.invoice_date,
       (o.focus_invoice_no is null)                                                 as missing_invoice,
       (o.focus_invoice_no is not null and i.invoice_key is null)                   as invoice_not_found,
       (i.invoice_key is not null
        and lower(coalesce(i.focus_salesman, '')) <> lower(coalesce(s.focus_name, o.salesman_name, '')))
                                                                                    as salesman_mismatch,
       (i.invoice_key is not null
        and abs(coalesce(i.invoice_total_bhd, 0) - coalesce(o.total_confirmed_bhd, o.total_bhd, 0)) > 0.005)
                                                                                    as amount_mismatch,
       case when i.invoice_key is not null
            then round(coalesce(i.invoice_total_bhd, 0) - coalesce(o.total_confirmed_bhd, o.total_bhd, 0), 3) end
                                                                                    as amount_diff_bhd,
       o.is_test
from shop_orders o
left join salesmen s on s.id = o.salesman_id
left join inv i on i.invoice_key = upper(trim(o.focus_invoice_no))
where o.status = 'delivered';

revoke all on v_shop_focus_recon from anon, authenticated;

comment on view v_shop_focus_recon is
  'Delivered marketplace orders against the Focus sales ledger (v_sales) by focus_invoice_no: missing invoice, invoice not found, salesman mismatch (Focus salesman vs the rep''s focus_name), amount mismatch (> 0.005 BHD, VAT-inclusive both sides). Carries customer names: service role only.';

-- ── self-check ─────────────────────────────────────────────────────────────────
do $$
declare
  c text;
begin
  if to_regclass('public.shop_admin_audit') is null then
    raise exception 'shop_admin_audit missing';
  end if;
  foreach c in array array['id', 'at', 'actor', 'entity', 'entity_id', 'action', 'before', 'after'] loop
    if not exists (select 1 from information_schema.columns
                   where table_schema = 'public' and table_name = 'shop_admin_audit' and column_name = c) then
      raise exception 'shop_admin_audit.% missing', c;
    end if;
  end loop;
  if not exists (select 1 from pg_trigger where tgname = 'shop_admin_audit_append_only'
                 and tgrelid = 'public.shop_admin_audit'::regclass) then
    raise exception 'shop_admin_audit append-only trigger missing';
  end if;
  if not exists (select 1 from pg_tables where schemaname = 'public' and tablename = 'shop_admin_audit' and rowsecurity) then
    raise exception 'shop_admin_audit must have RLS enabled';
  end if;
  foreach c in array array['cancel_reason_code', 'paid_at', 'returned_bhd'] loop
    if not exists (select 1 from information_schema.columns
                   where table_schema = 'public' and table_name = 'shop_orders' and column_name = c) then
      raise exception 'shop_orders.% missing', c;
    end if;
  end loop;
  if not exists (select 1 from pg_constraint where conname = 'shop_orders_cancel_reason_code_check'
                 and pg_get_constraintdef(oid) like '%price_issue%') then
    raise exception 'shop_orders cancel_reason_code CHECK missing';
  end if;
  if to_regclass('public.v_shop_focus_recon') is null then
    raise exception 'v_shop_focus_recon missing';
  end if;
  -- the view must answer (it depends on v_sales and the delivered orders); a delivered order
  -- without an invoice reads as missing_invoice, never as a mismatch
  if exists (select 1 from v_shop_focus_recon where focus_invoice_no is null and not missing_invoice) then
    raise exception 'v_shop_focus_recon: a delivered order without an invoice must read missing_invoice';
  end if;
  if exists (select 1 from v_shop_focus_recon where focus_invoice_no is null and (salesman_mismatch or amount_mismatch)) then
    raise exception 'v_shop_focus_recon: no invoice means no mismatch flags';
  end if;
  if exists (select 1 from information_schema.role_table_grants
             where table_schema = 'public' and table_name in ('shop_admin_audit', 'v_shop_focus_recon', 'shop_orders')
               and grantee in ('anon', 'authenticated')) then
    raise exception 'shop_admin_audit / v_shop_focus_recon / shop_orders must not be granted to anon/authenticated';
  end if;
  if has_table_privilege('yq_readonly', 'public.v_shop_focus_recon', 'SELECT') then
    raise exception 'v_shop_focus_recon carries customer names and must not be readable by yq_readonly';
  end if;
  raise notice 'r3_pipeline: ok (shop_admin_audit append-only, 3 shop_orders columns, v_shop_focus_recon answers, no anon/authenticated/yq_readonly grants)';
end $$;
