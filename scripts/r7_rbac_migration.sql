-- Management role v0 — release R7a (Sprint 1 "Make it true", stream B5, 27-Sep-2026). Additive, idempotent.
--   Rehearse: python -m scripts.apply_sql scripts/r7_rbac_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/r7_rbac_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/r7_rbac_reverse.sql
--
-- What this changes: user_roles_role_check also accepts management, operations, sales_manager and
-- finance. On production (read 27-Sep-2026) it accepted admin, member, manager, viewer, salesman and
-- storekeeper; every one of those stays. Only 'management' is offered by the API (app/features.ROLES:
-- company-wide read, every write refused in app/auth.py). operations / sales_manager / finance are
-- reserved for the capability layer (plan §7) and the team API refuses them (400) until then.
--
-- Nothing else is needed: the team audit writes entity 'user' to shop_admin_audit, which has no CHECK
-- on entity; user_roles stays service-role only (no grant here). No row is updated or deleted.
--
-- Deploy order: either. Until this runs, making someone Management answers 400 "The Management role
-- is not switched on in the database yet" (app/user_auth.role_enabled_in_db reads this constraint
-- through the read-only RPC, before any auth user or password is touched); nothing else changes.

alter table user_roles drop constraint if exists user_roles_role_check;
alter table user_roles add constraint user_roles_role_check
  check (role in ('admin', 'member', 'manager', 'viewer', 'salesman', 'storekeeper',
                  'management', 'operations', 'sales_manager', 'finance'));

-- Self-check: every role the API offers or reserves is accepted, the old ones still are, and the
-- table is still out of the browser roles' reach.
do $$
declare
  def text;
  r text;
begin
  select pg_get_constraintdef(oid) into def from pg_constraint
   where conrelid = 'public.user_roles'::regclass and conname = 'user_roles_role_check';
  if def is null then
    raise exception 'r7_rbac: user_roles_role_check missing';
  end if;
  foreach r in array array['admin', 'member', 'manager', 'viewer', 'salesman', 'storekeeper',
                           'management', 'operations', 'sales_manager', 'finance'] loop
    if position(quote_literal(r) in def) = 0 then
      raise exception 'r7_rbac: role % not accepted by user_roles_role_check (%)', r, def;
    end if;
  end loop;
  if exists (select 1 from information_schema.role_table_grants
             where table_schema = 'public' and table_name = 'user_roles' and grantee in ('anon', 'authenticated')) then
    raise exception 'r7_rbac: user_roles must not be granted to anon/authenticated';
  end if;
  raise notice 'r7_rbac: ok';
end $$;
