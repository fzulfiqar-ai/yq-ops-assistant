-- Category division + a sales-by-category rollup. Idempotent.
-- Categories themselves come from Focus's item-groups (seeded by scripts/category_backfill.py from
-- the Multi_level_stock_movement report) — never hardcoded in app code. This adds a coarse division
-- (Accessories vs Telecom/Devices) and a ready rollup view for "sales by category / by division".

alter table categories add column if not exists division text;

-- Sales grouped by Focus item-group (category) + coarse division. category_name flows from
-- products -> categories (populated once products.category_id is backfilled).
create or replace view v_sales_by_category as
select
  coalesce(s.category_name, 'Uncategorised') as category_name,
  c.division,
  count(distinct s.invoice_no)               as orders,
  sum(s.quantity)                            as qty,
  sum(s.revenue_bhd)                         as revenue_bhd,
  sum(s.net_bhd)                             as net_bhd
from v_sales s
left join categories c on c.name = s.category_name
group by coalesce(s.category_name, 'Uncategorised'), c.division
order by revenue_bhd desc nulls last;

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'yq_readonly') then
    grant select on v_sales_by_category to yq_readonly;
  end if;
end $$;
