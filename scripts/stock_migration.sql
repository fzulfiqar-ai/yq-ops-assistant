-- Stock correctness + smart inventory migration. Idempotent.
-- Fixes: v_current_stock (was 8.6x overstated from the ledger) now sourced from the
-- authoritative Stock_balance_by_warehouse snapshot; v_product_margin now latest-period
-- only. Adds velocity-aware stock health (out-of-stock + fast-moving = urgent reorder).

-- 1) Authoritative current stock (one row per item+warehouse, Focus 'as on date').
create table if not exists stock_balance (
  id               bigint generated always as identity primary key,
  item_name        text not null,
  warehouse_name   text,
  net_qty          numeric,
  selling_rate_bhd numeric,
  total_value_bhd  numeric,
  as_of_date       date,
  source_file      text,
  imported_at      timestamptz default now(),
  unique (item_name, warehouse_name, as_of_date)
);
create index if not exists stock_balance_asof_idx on stock_balance (as_of_date);
alter table stock_balance enable row level security;
drop policy if exists stock_balance_read on stock_balance;
create policy stock_balance_read on stock_balance for select to authenticated using (true);

-- 2) Rebuild the current-stock chain from stock_balance (drop dependents first).
drop view if exists v_low_stock cascade;
drop view if exists v_current_stock cascade;

create view v_current_stock as
select
  sb.item_name,
  sb.warehouse_name,
  sb.net_qty                                   as balance_qty,
  sb.total_value_bhd                           as balance_value_bhd,
  sb.selling_rate_bhd                          as avg_rate_bhd,
  sb.as_of_date,
  p.sku_code,
  p.item_name                                  as product_name,
  cat.name                                     as category_name,
  (sb.net_qty is not null and sb.net_qty <= 10) as is_low_stock
from stock_balance sb
left join product_aliases pa  on pa.alias_text = sb.item_name
left join products        p   on p.id          = pa.product_id
left join categories      cat on cat.id        = p.category_id
where sb.as_of_date = (select max(as_of_date) from stock_balance);

create view v_low_stock as
select item_name, product_name, sku_code, category_name, warehouse_name,
       balance_qty, balance_value_bhd, as_of_date
from v_current_stock
where balance_qty is not null and balance_qty <= 10
order by balance_qty asc;

-- 3) Sales velocity per item (the "is it moving?" signal) from the day-book.
--    KEPT IN SYNC with scripts/revenue_channel_migration.sql (the source of truth) so that
--    re-applying migrations in ANY order yields the same view. Windows anchor to MAX(line_date),
--    never CURRENT_DATE (the server clock can run ahead of the last loaded data).
create or replace view v_item_velocity as
select
  item_name,
  sum(case when line_date > (select max(line_date) from order_lines) - 30 then quantity else 0 end) as sold_30d,
  sum(case when line_date > (select max(line_date) from order_lines) - 90 then quantity else 0 end) as sold_90d,
  sum(quantity)                                                          as sold_total,
  max(line_date)                                                         as last_sold
from order_lines
where item_name is not null and quantity is not null
group by item_name;

-- 4) Smart stock health: combine on-hand with velocity. This is what makes reorder
--    intelligent — a fast mover at zero stock is URGENT (lost sales), a non-mover
--    sitting on stock is DEAD (trapped cash).
--    CANONICAL low-stock definition (KEEP IN SYNC with revenue_channel_migration.sql):
--    days_cover = current_stock / (sold_90d/90); status 'low_stock' when < 30 days of cover,
--    'urgent_out_of_stock' when out of stock and still selling. The verified low-stock count
--    (36) = low_stock + urgent_out_of_stock. Do NOT revert to sold_30d/'reorder_soon' — that
--    silently changes the dashboard KPI and the inventory agent.
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

-- 5) Margin: latest report period only (was summing old + new = double count).
create or replace view v_product_margin as
select
  pp.item_name, pp.report_date, pp.gross_bhd, pp.discount_pct, pp.net_amount_bhd,
  pp.cogs_bhd, pp.gross_profit_bhd, pp.gp_margin_pct, pp.misc_charges_bhd,
  pp.net_profit_bhd, pp.np_margin_pct,
  p.sku_code, p.item_name as product_name, cat.name as category_name,
  sp.rate_bhd as list_price_bhd
from product_profitability pp
left join product_aliases pa  on pa.alias_text = pp.item_name
left join products        p   on p.id          = pa.product_id
left join categories      cat on cat.id        = p.category_id
left join lateral (
    select rate_bhd from selling_prices
    where sku_code = p.sku_code and price_book = 'MA_base'
      and (customer_code is null or customer_code = '')
    order by id desc limit 1
) sp on true
where pp.report_date = (select max(report_date) from product_profitability);

-- 6) Re-grant recreated views to the optional read-only role.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'yq_readonly') then
    grant select on v_current_stock, v_low_stock, v_item_velocity, v_stock_health to yq_readonly;
  end if;
end $$;
