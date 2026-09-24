-- Server-owned must_reset (24-Sep-2026, release R1 security S6). Additive, idempotent.
--   Rehearse: python -m scripts.apply_sql scripts/user_roles_must_reset_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/user_roles_must_reset_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/user_roles_must_reset_reverse.sql
--
-- THE OWNER SCHEDULES THIS FILE. The backfill below flags every SALESMAN login still on the
-- temporary password handed out at invite time (15 today; admins are never flagged). From the
-- moment it runs, each of those reps gets "Set your own password to continue" on their next
-- request and can do nothing else until they set one (the app sends them straight to the
-- password screen). Run it at a time the owner picked and told the reps about — not silently
-- with the code deploy.
--
-- Why: the "temporary password, change it on first login" flag lived only in the Supabase auth
-- user_metadata, which the user can write (supabase.auth.updateUser), and only the SPA banner
-- looked at it. The API now enforces user_roles.must_reset (app/auth.py: 403
-- {"code": "password_change_required"} on every route except /me, /auth/features and
-- POST /auth/password) and clears it only after it set the new password itself
-- (app/user_auth.set_password).
--
-- Deploy order: the API tolerates the column being absent (treated as false; PGRST204 / 42703
-- on a write retries without the column), so deploy the API FIRST, then the web (its password
-- form calls POST /auth/password and both shells redirect on the 403), then apply this file.
-- Nothing here touches views or grants; user_roles stays service-role only
-- (hotfix_view_grants_migration.sql revoked anon/authenticated).

alter table user_roles add column if not exists must_reset boolean not null default false;

comment on column user_roles.must_reset is
  'Server-owned: true = the login must set a new password before any other route answers (app/auth.py). Cleared by the API after POST /auth/password; the auth user_metadata copy is display-only.';

-- One-time backfill from the metadata the temp-password invites wrote, so the reps still on the
-- temporary password are asked to change it. SALESMAN rows only — an admin is never locked out
-- of the portal by this file. Only ever sets TRUE where the metadata says so and the row is
-- still false; a member who already changed their password (the SPA wrote must_reset=false) is
-- left alone. Re-running is a no-op. Skipped when auth.users is not readable from this role
-- (a local scratch database).
do $$
begin
  if to_regclass('auth.users') is not null then
    update user_roles r
       set must_reset = true
      from auth.users u
     where lower(u.email) = lower(r.email)
       and r.role = 'salesman'
       and coalesce(u.raw_user_meta_data ->> 'must_reset', 'false') = 'true'
       and r.must_reset = false;
  end if;
exception when insufficient_privilege then
  raise notice 'auth.users not readable: must_reset backfill skipped';
end $$;

-- Self-check: the column exists with the right shape, no admin was flagged, and the table is
-- still not reachable by the browser roles (neither table nor column grants).
do $$
declare
  col record;
begin
  select data_type, is_nullable, column_default into col
    from information_schema.columns
   where table_schema = 'public' and table_name = 'user_roles' and column_name = 'must_reset';
  if col is null then
    raise exception 'user_roles.must_reset missing';
  end if;
  if col.data_type <> 'boolean' or col.is_nullable <> 'NO' or col.column_default is distinct from 'false' then
    raise exception 'user_roles.must_reset has the wrong shape: % % %', col.data_type, col.is_nullable, col.column_default;
  end if;
  if exists (select 1 from user_roles where role = 'admin' and must_reset) then
    raise exception 'an admin row has must_reset set — this file never flags admins';
  end if;
  if exists (select 1 from information_schema.role_table_grants
             where table_schema = 'public' and table_name = 'user_roles' and grantee in ('anon', 'authenticated')) then
    raise exception 'user_roles must not be granted to anon/authenticated';
  end if;
  if exists (select 1 from information_schema.role_column_grants
             where table_schema = 'public' and table_name = 'user_roles' and grantee in ('anon', 'authenticated')) then
    raise exception 'user_roles columns must not be granted to anon/authenticated';
  end if;
end $$;
