-- Phase C.2 + C.4 — returns views + customer LTV (RFM).
-- Idempotent; apply with:  python -m scripts.apply_migration scripts/returns_ltv_views_migration.sql
-- Depends on: customer_dimension_migration.sql, channel_migration.sql (v_sales).
--
-- Returns source: stock_movements voucher_type='Sales Return' (17 rows today). order_lines has
-- NO negative quantities, so there is nothing to UNION from the day book (verified) — returns
-- flow only through the stock ledger, and the returned quantity lands in received_qty.

create or replace view v_returns as
select
    sm.move_date                                   as return_date,
    sm.item_name,
    split_part(sm.item_name, ' ', 1)               as code,
    -- vendor tag extracted the same way as v_vendor_scorecard (…(VFAN)… style)
    substring(sm.item_name from '\(([A-Za-z]{2,8})\)') as vendor,
    coalesce(sm.received_qty, 0)                    as qty,
    sm.warehouse_name,
    sm.voucher
from stock_movements sm
where sm.voucher_type = 'Sales Return';

-- Return rate per item over the trailing 180 days (only items with enough sales to be meaningful).
-- Joined on the LEADING CODE (first token), not the full item_name: the stock ledger and the day
-- book name the same product differently (verified: 0/17 exact-match, 15/17 code-match), exactly
-- like every other cross-source join in this schema.
create or replace view v_return_rates as
with r as (
    select code, sum(qty) as ret_qty
    from v_returns
    where return_date >= (select max(sale_date) from v_sales) - 180
    group by code
), s as (
    select
        split_part(item_name, ' ', 1)              as code,
        max(item_name)                             as item_name,
        max(substring(item_name from '\(([A-Za-z]{2,8})\)')) as vendor,
        sum(quantity)                              as sold_qty
    from v_sales
    where sale_date >= (select max(sale_date) from v_sales) - 180
      and item_name is not null
    group by split_part(item_name, ' ', 1)
)
select
    s.item_name,
    s.code,
    s.vendor,
    coalesce(r.ret_qty, 0)                          as ret_qty,
    s.sold_qty,
    round(100.0 * coalesce(r.ret_qty, 0) / nullif(s.sold_qty, 0), 2) as return_rate_pct
from s
left join r using (code)
where s.sold_qty >= 10;

-- Customer LTV / RFM. Keyed to a stable customer_id via customer_aliases (falls back to the
-- customer_name text when unresolved). Excludes the walk-in "Cash Customer" bucket.
create or replace view v_customer_ltv as
with base as (
    select
        coalesce(ca.customer_id, -1)               as customer_id,
        s.customer_name,
        count(distinct s.invoice_no)               as frequency,
        sum(s.net_bhd)                             as monetary_bhd,
        max(s.sale_date)                           as last_order,
        min(s.sale_date)                           as first_order,
        (select max(sale_date) from v_sales) - max(s.sale_date) as recency_days
    from v_sales s
    left join customer_aliases ca on ca.alias = lower(s.customer_name)
    where s.customer_name is not null
      and s.customer_name not ilike 'cash customer%'
    group by coalesce(ca.customer_id, -1), s.customer_name
), scored as (
    select *,
        ntile(5) over (order by recency_days desc) as r_score,   -- lower recency_days = more recent = higher score
        ntile(5) over (order by frequency)         as f_score,
        ntile(5) over (order by monetary_bhd)      as m_score
    from base
)
select
    customer_id, customer_name, frequency,
    round(monetary_bhd::numeric, 3)                as monetary_bhd,
    last_order, first_order, recency_days,
    r_score, f_score, m_score,
    (r_score + f_score + m_score)                  as rfm_total,
    case
        when r_score >= 4 and f_score >= 4 then 'champion'
        when f_score >= 4                  then 'loyal'
        when r_score <= 2 and m_score >= 4 then 'at_risk_high_value'
        when r_score <= 2                  then 'lapsed'
        else 'developing'
    end                                            as segment
from scored;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'yq_readonly') then
    grant select on v_returns, v_return_rates, v_customer_ltv to yq_readonly;
  end if;
end $$;
