-- Reverse of scripts/r7_rbac_migration.sql (release R7a). Idempotent.
--   python -m scripts.apply_sql scripts/r7_rbac_reverse.sql
--
-- Restores user_roles_role_check to the six roles it accepted before (admin, member, manager, viewer,
-- salesman, storekeeper). REFUSES while any login holds, or any pending invite names, a role the old
-- CHECK does not accept: change those people's role on the Team page first (this file never rewrites
-- a user_roles row). The API keeps working either way; making someone Management then answers 400
-- again, naming the migration.

do $$
declare
  n int;
begin
  select count(*) into n from user_roles
   where role in ('management', 'operations', 'sales_manager', 'finance');
  if n > 0 then
    raise exception 'r7_rbac reverse refused: % user_roles row(s) use management / operations / sales_manager / finance', n;
  end if;
  if to_regclass('public.app_invites') is not null then
    select count(*) into n from app_invites
     where status = 'pending' and role in ('management', 'operations', 'sales_manager', 'finance');
    if n > 0 then
      raise exception 'r7_rbac reverse refused: % pending invite(s) use a role the previous CHECK does not accept', n;
    end if;
  end if;
end $$;

alter table user_roles drop constraint if exists user_roles_role_check;
alter table user_roles add constraint user_roles_role_check
  check (role in ('admin', 'member', 'manager', 'viewer', 'salesman', 'storekeeper'));

do $$
declare
  def text;
begin
  select pg_get_constraintdef(oid) into def from pg_constraint
   where conrelid = 'public.user_roles'::regclass and conname = 'user_roles_role_check';
  if def is null or position(quote_literal('storekeeper') in def) = 0 or position(quote_literal('management') in def) > 0 then
    raise exception 'r7_rbac reverse: user_roles_role_check not restored (%)', def;
  end if;
  raise notice 'r7_rbac reverse: ok';
end $$;
