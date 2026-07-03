-- One "low stock" definition platform-wide — velocity-based (v_stock_health),
-- replacing the legacy flat "balance <= 10 units" rule that disagreed with the
-- dashboard/agents and could mislead the assistant. Keeps the balance_qty column
-- name so existing consumers (legacy Streamlit count, LLM SQL) still work.
-- Requires v_stock_health (stock_migration.sql). Idempotent.

drop view if exists v_low_stock cascade;
create view v_low_stock as
select
  item_name,
  current_stock          as balance_qty,
  sold_90d,
  days_cover,
  suggested_reorder_qty,
  status
from v_stock_health
where status in ('urgent_out_of_stock', 'low_stock')
order by days_cover asc nulls first;

grant select on v_low_stock to yq_readonly;
