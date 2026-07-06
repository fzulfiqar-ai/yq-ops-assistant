-- Daily-ops round 2 — owner feedback fixes (2026-07-06):
--   1. v_catalog price was MAX(rate) over ALL history → stale numbers (BE01 showed 3.1,
--      current book says 5.5). Now reads the CURRENT dated price per book:
--      standard_rate = MA_base (B2B), b2c_rate = modern_trade (Causeway/Roadshow B2C) —
--      owner confirmed B2C comes from the uploaded price book too.
--   2. catalog_items had a yq_readonly GRANT but no RLS policy (same trap as
--      salesman_targets) — direct RPC reads silently returned 0 rows.
--   3. v_transfer_flow — Stock Issue ↔ Stock Receive pairing: the two legs share the
--      voucher number with different prefixes (SIO:YQ-26-07-15 ↔ STRV:YQ-26-07-15), so
--      the owner can see "main warehouse → who, and was it received" (or pending).
--   4. Agent scoping: *_agent views the AI agents query instead of the raw views.
--      They exclude the SIM / starter-pack division while app_settings
--      'agent_exclude_sim' = '1' (flip to '0' in Settings to re-enable — no redeploy).
-- Idempotent; apply with:  python -m scripts.apply_migration scripts/daily_ops2_migration.sql

-- ── 1. v_catalog: current dated prices per book (append b2c_rate) ─────────────
create or replace view v_catalog as
select
  ci.item_code,
  ci.display_name,
  ci.spec,
  ci.category,
  ci.brand,
  ci.division,
  ci.dealer_price,
  ci.roadshow_price,
  ci.rrp,
  b2b.price_bhd          as standard_rate,
  ci.product_image_url,
  ci.package_image_url,
  ci.sort_order,
  ci.is_active,
  ci.updated_at,
  ci.created_at,
  b2c.price_bhd          as b2c_rate
from catalog_items ci
left join lateral (
  select price_bhd from v_price_list_by_book
  where sku_code = ci.item_code and price_book = 'MA_base' limit 1
) b2b on true
left join lateral (
  select price_bhd from v_price_list_by_book
  where sku_code = ci.item_code and price_book = 'modern_trade' limit 1
) b2c on true;

-- ── 2. catalog_items RLS gap (grant existed, policy didn't) ───────────────────
drop policy if exists catalog_items_yq_readonly_read on catalog_items;
create policy catalog_items_yq_readonly_read on catalog_items
  for select to yq_readonly using (true);

-- ── 3. Stock Issue ↔ Stock Receive pairing ────────────────────────────────────
-- Legs share the voucher number after the prefix (SIO: / STRV:). One row per
-- issued (voucher, item): where it left, where it was aimed, what actually arrived.
create or replace view v_transfer_flow as
with iss as (
  select split_part(voucher, ':', 2)          as voucher_no,
         min(move_date)                        as issued_on,
         item_name,
         warehouse_name                        as from_warehouse,
         to_warehouse_name                     as to_warehouse,
         sum(coalesce(issued_qty, 0))          as issued_qty,
         sum(coalesce(issued_value_bhd, 0))    as issued_value_bhd
  from stock_movements
  where voucher_type = 'Stock Issue Voucher' and voucher like '%:%'
  group by 1, 3, 4, 5
),
rcv as (
  select split_part(voucher, ':', 2)          as voucher_no,
         item_name,
         max(warehouse_name)                   as received_by,
         max(move_date)                        as received_on,
         sum(coalesce(received_qty, 0))        as received_qty
  from stock_movements
  where voucher_type = 'Stock Receive Voucher' and voucher like '%:%'
  group by 1, 2
)
select i.issued_on, i.voucher_no, i.item_name,
       i.from_warehouse, i.to_warehouse,
       i.issued_qty, i.issued_value_bhd,
       r.received_qty, r.received_by, r.received_on,
       case
         when r.received_qty is null or r.received_qty = 0 then 'pending'
         when r.received_qty >= i.issued_qty               then 'received'
         else 'partial'
       end as status
from iss i
left join rcv r on r.voucher_no = i.voucher_no and r.item_name = i.item_name;

-- ── 4. Agent-scoped views (SIM / starter packs excluded while the toggle is on) ─
-- The toggle lives in app_settings so Settings can flip it live. Item-level rule
-- mirrors v_sales: category division 'SIM' wins, item-name sim/batelco as backstop.
insert into app_settings (key, value, description)
values ('agent_exclude_sim', '1',
        'When 1, AI agents ignore the SIM/starter-pack division (mobile accessories only).')
on conflict (key) do nothing;

create or replace view v_sales_agent as
select * from v_sales
where coalesce((select value from app_settings where key = 'agent_exclude_sim'), '1') <> '1'
   or coalesce(division, 'Accessories') <> 'SIM';

create or replace view v_sales_by_salesman_agent as
select salesman_resolved as salesman,
       count(distinct invoice_no) as orders,
       sum(quantity) as qty,
       sum(revenue_bhd) as revenue_bhd,
       sum(net_bhd) as net_bhd
from v_sales_agent
where salesman_resolved is not null
group by salesman_resolved
order by revenue_bhd desc nulls last;

create or replace view v_sales_by_channel_agent as
select channel,
       count(distinct invoice_no) as orders,
       sum(quantity) as qty,
       sum(revenue_bhd) as revenue_bhd,
       sum(net_bhd) as net_bhd
from v_sales_agent
group by channel;

create or replace view v_sales_by_period_agent as
select date_trunc('month', sale_date)::date as period_month,
       count(distinct invoice_no)  as order_count,
       count(*)                    as line_count,
       sum(quantity)               as total_qty,
       sum(gross_bhd)              as gross_bhd,
       sum(discount_bhd)           as total_discount_bhd,
       sum(coalesce(total_amount_bhd, taxable_bhd, gross_bhd)) as net_revenue_bhd,
       sum(vat_amount_bhd)         as total_vat_bhd
from v_sales_agent
where sale_date is not null
group by 1
order by 1;

create or replace view v_stock_health_agent as
select h.* from v_stock_health h
where coalesce((select value from app_settings where key = 'agent_exclude_sim'), '1') <> '1'
   or not (
     h.item_name ilike '%sim%' or h.item_name ilike '%batelco%'
     or exists (
       select 1 from product_aliases pa
       join products   p on p.id = pa.product_id
       join categories c on c.id = p.category_id
       where pa.alias_text = h.item_name and c.division = 'SIM'
     )
   );

create or replace view v_current_stock_agent as
select cs.* from v_current_stock cs
where coalesce((select value from app_settings where key = 'agent_exclude_sim'), '1') <> '1'
   or not (
     cs.item_name ilike '%sim%' or cs.item_name ilike '%batelco%'
     or exists (select 1 from categories c
                where c.name = cs.category_name and c.division = 'SIM')
   );

-- ── 5. Grants (plain statements — never inside DO blocks) ─────────────────────
grant select on v_catalog, v_transfer_flow, v_sales_agent, v_sales_by_salesman_agent,
  v_sales_by_channel_agent, v_sales_by_period_agent, v_stock_health_agent,
  v_current_stock_agent to yq_readonly;
