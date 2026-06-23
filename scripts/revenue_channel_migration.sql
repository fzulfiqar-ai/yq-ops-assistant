-- Revenue standardization + channel/salesman + smart stock health. Idempotent.
-- Supersedes the v_sales / velocity / stock-health parts of scripts/views.sql.
--
-- Revenue is exposed BOTH ways everywhere:
--   revenue_bhd = COALESCE(total_amount_bhd, gross_bhd)   -- Gross, VAT-incl (= verified 51,661)
--   net_bhd     = COALESCE(taxable_bhd, gross_bhd / 1.1)  -- ex-VAT (~46,965)
-- All time windows anchor to MAX(line_date) (the data's latest day), never CURRENT_DATE,
-- because the system clock can run ahead of the last loaded data.

-- 1) v_sales — enriched lines + both revenue measures (DROP+CREATE: column set changes).
drop view if exists v_sales cascade;
create view v_sales as
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
    coalesce(ol.total_amount_bhd, ol.gross_bhd)    as revenue_bhd,   -- Gross (VAT-incl) headline
    coalesce(ol.taxable_bhd, ol.gross_bhd / 1.1)   as net_bhd,       -- ex-VAT
    coalesce(ol.total_amount_bhd, ol.gross_bhd)    as total_amount_bhd, -- back-compat = gross now
    coalesce(o.salesman, ol.warehouse_name)        as salesman_resolved,
    ol.warehouse_name                              as salesman_raw,
    -- channel: only these two outlets are B2C/retail; every other route is B2B/wholesale
    case when coalesce(o.salesman, ol.warehouse_name) in ('Causeway', 'YQ Roadshow')
         then 'B2C' else 'B2B' end                 as channel,
    -- a real named account vs the walk-in "Cash Customer" bucket (28% of sales)
    (coalesce(o.customer_name, ol.customer_account) ilike 'cash customer%') as is_cash_customer,
    ol.narration
from order_lines ol
left join orders          o   on o.invoice_no  = ol.invoice_no
left join product_aliases pa  on pa.alias_text = ol.item_name
left join products        p   on p.id          = pa.product_id
left join categories      cat on cat.id        = p.category_id;

-- 2) Sales by salesman (value + volume) and by channel (B2C/B2B).
create or replace view v_sales_by_salesman as
select
    salesman_resolved                          as salesman,
    count(distinct invoice_no)                 as orders,
    sum(quantity)                              as qty,
    sum(revenue_bhd)                           as revenue_bhd,
    sum(net_bhd)                               as net_bhd
from v_sales
where salesman_resolved is not null
group by salesman_resolved
order by revenue_bhd desc nulls last;

create or replace view v_sales_by_channel as
select
    channel,
    count(distinct invoice_no)                 as orders,
    sum(quantity)                              as qty,
    sum(revenue_bhd)                           as revenue_bhd,
    sum(net_bhd)                               as net_bhd
from v_sales
group by channel;

-- 3) Item velocity — 90-day window anchored to the data's latest date.
create or replace view v_item_velocity as
select
    item_name,
    sum(case when line_date > (select max(line_date) from order_lines) - 30 then quantity else 0 end) as sold_30d,
    sum(case when line_date > (select max(line_date) from order_lines) - 90 then quantity else 0 end) as sold_90d,
    sum(quantity)                              as sold_total,
    max(line_date)                             as last_sold
from order_lines
where item_name is not null and quantity is not null
group by item_name;

-- 4) Smart stock health — matches the Monthly workbook: days_cover = stock / (sold_90d/90),
--    alert when < 30 days. Out-of-stock + still-selling = urgent (lost sales).
create or replace view v_stock_health as
with stock as (
  select item_name, sum(net_qty) as current_stock, sum(total_value_bhd) as stock_value
  from stock_balance
  where as_of_date = (select max(as_of_date) from stock_balance)
  group by item_name
)
select
  coalesce(s.item_name, v.item_name)           as item_name,
  coalesce(s.current_stock, 0)                 as current_stock,
  coalesce(s.stock_value, 0)                   as stock_value,
  coalesce(v.sold_30d, 0)                      as sold_30d,
  coalesce(v.sold_90d, 0)                      as sold_90d,
  round(coalesce(v.sold_90d, 0) / 90.0, 3)     as avg_daily,
  v.last_sold,
  case when coalesce(v.sold_90d, 0) > 0
       then round(coalesce(s.current_stock, 0) / (v.sold_90d / 90.0), 1)
       else null end                           as days_cover,
  greatest(ceil(coalesce(v.sold_90d, 0) / 3.0) - coalesce(s.current_stock, 0), 0) as suggested_reorder_qty,
  case
    when coalesce(s.current_stock, 0) <= 0 and coalesce(v.sold_90d, 0) > 0 then 'urgent_out_of_stock'
    when coalesce(v.sold_90d, 0) > 0 and s.current_stock / (v.sold_90d / 90.0) < 30 then 'low_stock'
    when coalesce(v.sold_90d, 0) = 0 and coalesce(s.current_stock, 0) > 0 then 'dead_stock'
    when coalesce(v.sold_90d, 0) > 0 and s.current_stock / (v.sold_90d / 90.0) > 120 then 'overstock'
    else 'healthy'
  end                                          as status
from stock s
full outer join v_item_velocity v on v.item_name = s.item_name;

-- 5) Inventory aging — on-hand items by days since last sale (capital sitting idle).
create or replace view v_inventory_aging as
with stock as (
  select item_name, sum(net_qty) as current_stock, sum(total_value_bhd) as stock_value
  from stock_balance
  where as_of_date = (select max(as_of_date) from stock_balance)
  group by item_name
)
select
  s.item_name,
  s.current_stock,
  s.stock_value,
  v.last_sold,
  ((select max(line_date) from order_lines) - v.last_sold) as days_since_sale
from stock s
left join v_item_velocity v on v.item_name = s.item_name
where s.current_stock > 0
order by days_since_sale desc nulls first;

-- 6) Re-grant recreated/new views to the read-only role.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'yq_readonly') then
    grant select on
      v_sales, v_sales_by_salesman, v_sales_by_channel,
      v_item_velocity, v_stock_health, v_inventory_aging
    to yq_readonly;
  end if;
end $$;
