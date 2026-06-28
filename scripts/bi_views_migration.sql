-- NEXT#10 — distributor BI views a daily operator needs. Additive + idempotent (CREATE OR
-- REPLACE), built only on existing semantic views so the verified numbers are untouched.
--   v_margin_leakage         — where discounting is eroding revenue, by item
--   v_sales_by_month_channel — B2C vs B2B mix OVER TIME (not just a today pie)
--   v_basket_affinity        — products bought together (cross-sell / bundles)
--   v_vendor_scorecard       — vendor spend, recency & cost-creep

-- 1) Margin leakage: discount as a share of gross, per item (biggest leaks first).
create or replace view v_margin_leakage as
select
  item_name,
  category_name,
  count(distinct invoice_no)                                            as orders,
  round(sum(gross_bhd)::numeric, 3)                                     as gross_bhd,
  round(sum(discount_bhd)::numeric, 3)                                  as discount_bhd,
  round((sum(discount_bhd) / nullif(sum(gross_bhd), 0) * 100)::numeric, 1) as discount_pct
from v_sales
where item_name is not null
group by item_name, category_name
having sum(discount_bhd) > 0
order by discount_bhd desc;

-- 2) Channel mix over time — monthly B2C/B2B split.
create or replace view v_sales_by_month_channel as
select
  date_trunc('month', sale_date)::date  as period_month,
  channel,
  count(distinct invoice_no)            as orders,
  sum(quantity)                         as qty,
  round(sum(revenue_bhd)::numeric, 3)   as revenue_bhd,
  round(sum(net_bhd)::numeric, 3)       as net_bhd
from v_sales
where sale_date is not null
group by 1, 2
order by 1, 2;

-- 3) Basket affinity — co-occurrence of items in the same invoice (cross-sell pairs).
create or replace view v_basket_affinity as
select
  a.item_name                           as item_a,
  b.item_name                           as item_b,
  count(distinct a.invoice_no)          as bought_together,
  round(sum(b.revenue_bhd)::numeric, 3) as attach_revenue_bhd
from v_sales a
join v_sales b
  on a.invoice_no = b.invoice_no and a.item_name < b.item_name
where a.item_name is not null and b.item_name is not null
group by 1, 2
having count(distinct a.invoice_no) >= 3;

-- 4) Vendor scorecard — spend, recency and cost-creep per supplier.
create or replace view v_vendor_scorecard as
with spend as (
  select vendor, count(*) as lines, sum(cost_bhd) as spend_bhd, max(purchased_on) as last_order
  from v_purchase_history where vendor is not null group by vendor
), creep as (
  select vendor,
         count(*) filter (where cost_change_pct > 0) as items_up,
         round(avg(cost_change_pct) filter (where cost_change_pct > 0)::numeric, 1) as avg_cost_up_pct
  from v_cost_change where vendor is not null group by vendor
)
select
  s.vendor,
  s.lines,
  round(s.spend_bhd::numeric, 3)        as spend_bhd,
  s.last_order,
  coalesce(c.items_up, 0)               as items_cost_up,
  coalesce(c.avg_cost_up_pct, 0)        as avg_cost_up_pct
from spend s
left join creep c on c.vendor = s.vendor
order by s.spend_bhd desc nulls last;

-- Grant to the optional read-only role if present.
do $$
begin
  if exists (select 1 from pg_roles where rolname = 'yq_readonly') then
    grant select on v_margin_leakage, v_sales_by_month_channel, v_basket_affinity, v_vendor_scorecard
      to yq_readonly;
  end if;
end $$;
