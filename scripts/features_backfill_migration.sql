-- Feature-access expansion backfill.
-- New grantable features (app/features.py): Live Feed, Orders, Stock Movement, Leads, Catalog.
-- Pages used to piggyback on old grants (Feed→"AI Agents", Orders→"Inventory",
-- Leads→"Sales"), so users who could see those pages keep them under the new names.
-- Catalog is NEW — granted explicitly by the admin (or via the salesman role).
-- Idempotent; safe to re-run.

update user_roles set features = (
  select coalesce(to_jsonb(array(
    select distinct f from (
      select jsonb_array_elements_text(features) as f
      union all select 'Orders'         where features ? 'Inventory'
      union all select 'Stock Movement' where features ? 'Inventory'
      union all select 'Live Feed'      where features ? 'AI Agents'
      union all select 'Leads'          where features ? 'Sales'
    ) t
  )), '[]'::jsonb)
)
where jsonb_typeof(features) = 'array';

update app_invites set features = (
  select coalesce(to_jsonb(array(
    select distinct f from (
      select jsonb_array_elements_text(features) as f
      union all select 'Orders'         where features ? 'Inventory'
      union all select 'Stock Movement' where features ? 'Inventory'
      union all select 'Live Feed'      where features ? 'AI Agents'
      union all select 'Leads'          where features ? 'Sales'
    ) t
  )), '[]'::jsonb)
)
where status = 'pending' and jsonb_typeof(features) = 'array';
