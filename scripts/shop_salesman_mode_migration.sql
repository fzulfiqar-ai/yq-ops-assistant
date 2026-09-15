-- YQ Shop — salesman mode (15-Sep-2026). Idempotent.
-- Apply:  python -m scripts.apply_sql scripts/shop_salesman_mode_migration.sql
--
-- Salesmen (and admins) can now place an order FOR a shop from the logged-in catalog:
--   * shop_orders.source gains the value 'salesman';
--   * shop_orders.placed_by records the login email of the staff member who placed it;
--   * v_shop_orders_agent gains placed_by (APPENDED last — CREATE OR REPLACE VIEW cannot reorder columns).

alter table shop_orders drop constraint if exists shop_orders_source_check;
alter table shop_orders
  add constraint shop_orders_source_check
  check (source in ('referral', 'dropdown', 'default', 'salesman'));

alter table shop_orders add column if not exists placed_by text;
create index if not exists shop_orders_placed_by_idx on shop_orders (placed_by);

create or replace view v_shop_orders_agent as
select id, order_no, status, customer_shop, customer_area, salesman_name, source, referral_code, coupon_code,
       subtotal_bhd, discount_bhd, delivery_bhd, total_bhd, items_count, units_count, has_backorder,
       created_at, updated_at,
       placed_by
from shop_orders;

grant select on v_shop_orders_agent to yq_readonly;

-- The portal has offered the 'salesman' role since July (app/features.py), but the DB check on
-- user_roles was never widened, so creating a salesman login failed with user_roles_role_check.
alter table user_roles drop constraint if exists user_roles_role_check;
alter table user_roles
  add constraint user_roles_role_check
  check (role in ('admin', 'member', 'manager', 'viewer', 'salesman'));
