-- Phase C.3 — materialized channel dimension.
-- Idempotent; apply with:  python -m scripts.apply_migration scripts/channel_migration.sql
--
-- Channel (B2C/B2B) was derived at query time by matching salesman names in a CASE inside
-- v_sales. This lifts that into an admin-editable reference table; v_sales now looks the
-- channel up and FALLS BACK to the original CASE, so output is byte-identical to today until
-- someone edits the table (the 51,661 sales metric cannot regress from this change alone).

create table if not exists salesman_channels (
  salesman  text primary key,
  channel   text not null check (channel in ('B2C','B2B')),
  updated_at timestamptz default now()
);

-- Seed the two retail outlets that were the B2C branch of the old CASE.
insert into salesman_channels(salesman, channel) values
  ('Causeway', 'B2C'), ('YQ Roadshow', 'B2C')
on conflict (salesman) do nothing;

-- Rebuild v_sales with the SAME column list/order/types (CREATE OR REPLACE requires it),
-- swapping only the channel expression to coalesce(lookup, original CASE).
create or replace view v_sales as
select
    ol.id                                          as line_id,
    ol.invoice_no,
    ol.line_no,
    coalesce(ol.line_date, o.order_date)           as sale_date,
    o.order_date,
    o.customer_name,
    ol.customer_account,
    o.salesman,
    o.payment_mode,
    o.sales_account_name,
    ol.item_name,
    p.sku_code,
    p.item_name                                    as product_name,
    cat.name                                       as category_name,
    ol.quantity,
    ol.rate_bhd,
    ol.gross_bhd,
    ol.discount_bhd,
    ol.taxable_bhd,
    ol.vat_amount_bhd,
    coalesce(ol.total_amount_bhd, ol.gross_bhd)    as revenue_bhd,
    coalesce(ol.taxable_bhd, ol.gross_bhd / 1.1)   as net_bhd,
    coalesce(ol.total_amount_bhd, ol.gross_bhd)    as total_amount_bhd,
    coalesce(o.salesman, ol.warehouse_name)        as salesman_resolved,
    ol.warehouse_name                              as salesman_raw,
    -- channel: admin-editable lookup, falling back to the original two-outlet rule
    coalesce(sc.channel,
             case when coalesce(o.salesman, ol.warehouse_name) in ('Causeway', 'YQ Roadshow')
                  then 'B2C' else 'B2B' end)        as channel,
    (coalesce(o.customer_name, ol.customer_account) ilike 'cash customer%') as is_cash_customer,
    ol.narration
from order_lines ol
left join orders          o   on o.invoice_no  = ol.invoice_no
left join product_aliases pa  on pa.alias_text = ol.item_name
left join products        p   on p.id          = pa.product_id
left join categories      cat on cat.id        = p.category_id
left join salesman_channels sc on sc.salesman  = coalesce(o.salesman, ol.warehouse_name);

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'yq_readonly') then
    grant select on salesman_channels to yq_readonly;
  end if;
end $$;
