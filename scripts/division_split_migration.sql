-- Separate ACCESSORIES from SIM everywhere (06-Sep-2026). Zero cost: views + one small table.
--
-- WHY. The Batelco/SIM starter-pack stock is OWNED (owner-confirmed 06-Sep-2026), not held on
-- consignment. It is roughly half the stock book and about two percent of revenue, so it
-- dominates every inventory, dead-stock and working-capital figure while contributing almost
-- no sales. Until now the only response was app_settings.agent_exclude_sim, a HIDDEN boolean
-- defaulting to ON: the agents silently dropped SIM while the Dashboard still counted it, so
-- the two surfaces disagreed with nothing on screen to explain the gap.
-- Hiding half the balance sheet is not a division strategy.
--
-- WHAT THIS DOES.
--   1. division_rules     -- the ONE place item->division is decided, as DATA not code. Three
--      different rules existed before (categories.division, a Python regex in app/agents.py,
--      an inline ilike in daily_ops2_migration.sql) and they could disagree.
--   2. v_item_division    -- resolves every item name to a division through that one table.
--   3. v_stock_health     -- gains a division column (APPENDED, so dependents keep working).
--   4. v_division_summary -- stock and sales side by side per division. Every figure here is
--      COMPUTED from the data; there is no hardcoded business constant anywhere below.
--
-- Idempotent. Apply with:  python -m scripts.apply_sql scripts/division_split_migration.sql

-- 1. Division rules as data -------------------------------------------------
create table if not exists division_rules (
  id         bigint generated always as identity primary key,
  pattern    text not null,
  division   text not null,
  priority   int  not null default 100,
  note       text,
  created_at timestamptz default now(),
  unique (pattern, division)
);

insert into division_rules (pattern, division, priority, note) values
  ('batelco|starter[[:space:]]*pack|(^|[^a-z])sim([^a-z]|$)', 'SIM', 10,
   'Telecom starter packs and SIMs. Owned stock, near-zero turn - keep visible, never hidden.'),
  ('giveaway|give[[:space:]]away|gift|free[[:space:]]stock', 'Giveaway', 20,
   'Promotional stock issued at no charge; must never count as revenue.'),
  ('device|handset|smart[[:space:]]*phone', 'Devices', 30,
   'Handsets and devices - a different margin profile from accessories.')
on conflict (pattern, division) do nothing;

-- 2. One canonical item -> division resolver --------------------------------
-- Precedence: the curated category division wins, then the name rules, then Accessories
-- as the residual. Nothing is hardcoded in application code.
create or replace view v_item_division as
with named as (
  select distinct item_name from stock_balance where item_name is not null
  union
  select distinct item_name from order_lines  where item_name is not null
),
by_category as (
  select n.item_name, c.division
  from named n
  left join product_aliases pa on pa.alias_text = n.item_name
  left join products p         on p.id = pa.product_id
  left join categories c       on c.id = p.category_id
),
by_rule as (
  select n.item_name,
         (select r.division from division_rules r
          where n.item_name ~* r.pattern
          order by r.priority, r.id limit 1) as division
  from named n
)
select
  n.item_name,
  coalesce(nullif(bc.division, ''), br.division, 'Accessories') as division
from named n
left join by_category bc on bc.item_name = n.item_name
left join by_rule     br on br.item_name = n.item_name;

-- 3. v_stock_health gains a division column (APPENDED) ----------------------
-- NOTE: the full-outer-join on item_name between two different Focus reports is a known
-- defect (stock_balance names and order_lines names do not fully agree). It is deliberately
-- NOT changed here; this migration only adds the division dimension.
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
  end                                          as status,
  coalesce(d.division, 'Accessories')          as division
from stock s
full outer join v_item_velocity v on v.item_name = s.item_name
left join v_item_division d on d.item_name = coalesce(s.item_name, v.item_name);

-- 4. Stock and sales side by side, per division -----------------------------
-- months_of_cover = this division's stock value divided by its OWN average monthly revenue,
-- measured over the actual span of the loaded sales data. No assumed period, no constant.
create or replace view v_division_summary as
with stk as (
  select coalesce(d.division, 'Accessories') as division,
         sum(sb.net_qty)                     as stock_units,
         sum(sb.total_value_bhd)             as stock_value_bhd,
         count(distinct sb.item_name)        as stock_skus
  from stock_balance sb
  left join v_item_division d on d.item_name = sb.item_name
  where sb.as_of_date = (select max(as_of_date) from stock_balance)
  group by 1
),
sal as (
  select coalesce(division, 'Accessories') as division,
         sum(revenue_bhd)                  as revenue_bhd,
         sum(net_bhd)                      as net_ex_vat_bhd,
         count(distinct invoice_no)        as invoices
  from v_sales
  where item_name is not null
  group by 1
),
span as (
  select greatest(
           extract(epoch from (max(sale_date)::timestamp - min(sale_date)::timestamp))
             / (60*60*24*30.44), 1) as months
  from v_sales where sale_date is not null
)
select
  coalesce(stk.division, sal.division)             as division,
  coalesce(stk.stock_value_bhd, 0)                 as stock_value_bhd,
  coalesce(stk.stock_units, 0)                     as stock_units,
  coalesce(stk.stock_skus, 0)                      as stock_skus,
  coalesce(sal.revenue_bhd, 0)                     as revenue_bhd,
  coalesce(sal.net_ex_vat_bhd, 0)                  as net_ex_vat_bhd,
  coalesce(sal.invoices, 0)                        as invoices,
  round((coalesce(stk.stock_value_bhd,0) * 100.0
         / nullif(sum(coalesce(stk.stock_value_bhd,0)) over (), 0))::numeric, 1) as stock_share_pct,
  round((coalesce(sal.revenue_bhd,0) * 100.0
         / nullif(sum(coalesce(sal.revenue_bhd,0)) over (), 0))::numeric, 1)     as revenue_share_pct,
  round((coalesce(stk.stock_value_bhd,0)
         / nullif(coalesce(sal.revenue_bhd,0) / (select months from span), 0))::numeric, 1)
                                                   as months_of_cover
from stk
full outer join sal on sal.division = stk.division;

grant select on division_rules, v_item_division, v_division_summary to yq_readonly;

do $$
begin
  raise notice 'division_split: % rules, % items resolved',
    (select count(*) from division_rules), (select count(*) from v_item_division);
end $$;
