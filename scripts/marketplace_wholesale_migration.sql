-- Marketplace v2.1 — wholesale order engine (16-Sep-2026).
-- Apply with: python -m scripts.apply_sql scripts/marketplace_wholesale_migration.sql
-- Idempotent.

-- Orders under the wholesale minimum are no longer refused; they are flagged so the rep confirms
-- them case by case (settings shop_small_order_mode = request). Optional handling fee applied at
-- confirmation, in BHD.
alter table shop_orders add column if not exists order_kind text not null default 'standard'
  check (order_kind in ('standard', 'small'));
alter table shop_orders add column if not exists small_order_fee_bhd numeric(12,3);
alter table shop_orders add column if not exists minimum_gap_bhd numeric(12,3);
create index if not exists shop_orders_kind_idx on shop_orders (order_kind) where order_kind <> 'standard';
