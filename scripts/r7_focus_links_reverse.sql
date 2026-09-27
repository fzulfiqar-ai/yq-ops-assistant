-- Reverse of scripts/r7_focus_links_migration.sql (release R7a). Idempotent.
--   python -m scripts.apply_sql scripts/r7_focus_links_reverse.sql --rehearse
--   python -m scripts.apply_sql scripts/r7_focus_links_reverse.sql
--
-- Drops v_shop_focus_candidates and shop_order_focus_links, and puts v_shop_focus_recon back to
-- its R3 definition (scripts/r3_pipeline_migration.sql §3, copied verbatim below). The link rows go
-- with the table — every decision is also on audit_log ('shop.focus_link') and shop_admin_audit
-- (entity 'focus_link'), so export the table first only if the rows themselves are wanted.
--
-- No order, line or event is touched: a focus_invoice_no set by an accept and a status moved to
-- Delivered by an accept stay (they are order facts with their own events; the R3 view reads the
-- invoice number again). The running code answers "not available yet" once the table and the
-- candidates view are gone (list_focus_candidates: empty + hint; decide_focus_link: 400 + hint),
-- so the API may still be the R7a build when this runs.
--
-- v_shop_focus_recon is dropped and created again rather than replaced: CREATE OR REPLACE VIEW
-- cannot remove the four columns R7a appended. No view depends on it (checked 27-Sep-2026 in
-- pg_depend); if one ever does, this DROP fails loudly — never add CASCADE to get past it.

drop view if exists v_shop_focus_candidates;
drop view if exists v_shop_focus_recon;

create view v_shop_focus_recon as
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
),
dup as (                                                  -- one Focus invoice on several delivered orders
  select upper(trim(focus_invoice_no))   as invoice_key,
         count(*)                        as orders_n
  from shop_orders
  where status = 'delivered' and focus_invoice_no is not null and trim(focus_invoice_no) <> ''
  group by upper(trim(focus_invoice_no))
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
       o.is_test,
       -- appended last: CREATE OR REPLACE VIEW may only add columns at the end
       (coalesce(d.orders_n, 0) > 1)                                                as invoice_reused
from shop_orders o
left join salesmen s on s.id = o.salesman_id
left join inv i on i.invoice_key = upper(trim(o.focus_invoice_no))
left join dup d on d.invoice_key = upper(trim(o.focus_invoice_no))
where o.status = 'delivered';

revoke all on v_shop_focus_recon from anon, authenticated;

comment on view v_shop_focus_recon is
  'Delivered marketplace orders against the Focus sales ledger (v_sales) by focus_invoice_no: missing invoice, invoice not found in the uploaded ledger, salesman mismatch (Focus salesman vs the rep''s focus_name), amount mismatch (> 0.005 BHD, VAT-inclusive both sides), invoice_reused (the same invoice number on more than one delivered order). Carries customer names: service role only.';

drop index if exists shop_order_focus_links_invoice_idx;
drop table if exists shop_order_focus_links;

do $$
begin
  if to_regclass('public.shop_order_focus_links') is not null or to_regclass('public.v_shop_focus_candidates') is not null then
    raise exception 'r7_focus_links reverse: objects still present';
  end if;
  if to_regclass('public.v_shop_focus_recon') is null then
    raise exception 'r7_focus_links reverse: v_shop_focus_recon (R3) missing';
  end if;
  if exists (select 1 from information_schema.columns where table_schema = 'public' and table_name = 'v_shop_focus_recon'
             and column_name in ('linked_orders_n', 'linked_orders_total_bhd', 'invoice_keys', 'link_state')) then
    raise exception 'r7_focus_links reverse: v_shop_focus_recon still has the R7a columns';
  end if;
  if exists (select 1 from information_schema.role_table_grants
             where table_schema = 'public' and table_name = 'v_shop_focus_recon' and grantee in ('anon', 'authenticated')) then
    raise exception 'r7_focus_links reverse: v_shop_focus_recon must not be granted to anon/authenticated';
  end if;
  raise notice 'r7_focus_links reverse: ok (links table and candidates view dropped, v_shop_focus_recon back to R3)';
end $$;
