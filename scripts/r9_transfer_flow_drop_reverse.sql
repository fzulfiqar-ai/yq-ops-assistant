-- Reverse of scripts/r9_transfer_flow_drop_migration.sql (release 2b, M2). Idempotent.
--   python -m scripts.apply_sql scripts/r9_transfer_flow_drop_reverse.sql ; python -m scripts.audit_grants
-- From the MAIN project folder only. Run it BEFORE rolling the API back to a release older than 2b (that API's
-- GET /stock/flow reads this view), and before scripts/r9_stock_moves_reverse.sql when both are reversed.
--
-- Re-creates v_transfer_flow exactly as daily_ops2_migration.sql (3) defines it, the pairing by voucher number,
-- with the grants it had: SELECT for yq_readonly (daily_ops2's plain grant), nothing for anon / authenticated (the
-- 16-Sep hotfix's revoke, hotfix_view_grants_migration.sql). It reads stock_movements only; no row is touched.

set lock_timeout = '2s';      -- fail fast, never queue

-- daily_ops2_migration.sql (3), verbatim
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

-- grants (plain statements, never inside a DO block): the hotfix's revoke, then daily_ops2's grant
revoke all on table public."v_transfer_flow" from anon, authenticated;
grant select on v_transfer_flow to yq_readonly;

do $$
declare
  got text;
  want constant text := 'issued_on, voucher_no, item_name, from_warehouse, to_warehouse, issued_qty, issued_value_bhd, '
                        || 'received_qty, received_by, received_on, status';
begin
  select string_agg(a.attname, ', ' order by a.attnum) into got
    from pg_attribute a
   where a.attrelid = 'public.v_transfer_flow'::regclass and a.attnum > 0 and not a.attisdropped;
  if got is distinct from want then
    raise exception 'r9_transfer_flow_drop reverse: v_transfer_flow is (%), expected (%)', got, want;
  end if;
  if exists (select 1 from information_schema.role_table_grants
              where table_schema = 'public' and table_name = 'v_transfer_flow'
                and grantee in ('anon', 'authenticated')) then
    raise exception 'r9_transfer_flow_drop reverse: v_transfer_flow must not be granted to anon/authenticated';
  end if;
  if not has_table_privilege('yq_readonly', 'public.v_transfer_flow', 'SELECT') then
    raise exception 'r9_transfer_flow_drop reverse: yq_readonly cannot read v_transfer_flow';
  end if;
  raise notice 'r9_transfer_flow_drop reverse: ok (v_transfer_flow as daily_ops2_migration.sql has it, with its grants)';
end $$;
