-- Server-owned must_reset (24-Sep-2026, release R1 security S6; widened to every role but the owner
-- in release R7b "Safe access", 27-Sep-2026 — plan §25 P0). Additive, idempotent. NOT applied yet.
--   Rehearse: python -m scripts.apply_sql scripts/user_roles_must_reset_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/user_roles_must_reset_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/user_roles_must_reset_reverse.sql
--
-- THE OWNER SCHEDULES THIS FILE. The backfill below flags every login still on the temporary
-- password handed out at invite time — salesmen AND admins (and any other role), never an owner.
-- Read-only on production 27-Sep-2026: 16 rows would be flagged (15 of the 16 salesmen and 1 of
-- the 3 admins); the owner's own row says false and would never be flagged anyway. From the moment
-- it runs, each of those people gets "Set your own password to continue" on their next request and
-- can do nothing else until they set one (the app sends them straight to the password screen).
-- Run it at a quiet hour the owner picked and announced to the reps and the office — not silently
-- with the code deploy.
--
-- The owner is never forced (the break-glass login, plan §32): this file skips the addresses below
-- and its closing check refuses to finish if one of them is flagged; app/auth.py lets an owner
-- (settings.owner_emails, env OWNER_EMAILS) through whatever the column says, and GET /me reports
-- false for them. KEEP THE LIST BELOW EQUAL TO OWNER_EMAILS on the Render service (default
-- fzulfiqar@pie-int.com, app/config.py).
--
-- Why: the "temporary password, change it on first login" flag lived only in the Supabase auth
-- user_metadata, which the user can write (supabase.auth.updateUser), and only the SPA banner
-- looked at it. The API now enforces user_roles.must_reset (app/auth.py: 403
-- {"code": "password_change_required"} on every route except /me, /auth/features and
-- POST /auth/password) and clears it only after it set the new password itself
-- (app/user_auth.set_password); since R7b that change also signs the login's other sessions out.
--
-- Deploy order: the API tolerates the column being absent (treated as false; PGRST204 / 42703
-- on a write retries without the column), so deploy the API FIRST, then the web (its password
-- form calls POST /auth/password and both shells redirect on the 403), then apply this file.
-- Nothing here touches views or grants; user_roles stays service-role only
-- (hotfix_view_grants_migration.sql revoked anon/authenticated).

alter table user_roles add column if not exists must_reset boolean not null default false;

comment on column user_roles.must_reset is
  'Server-owned: true = the login must set a new password before any other route answers (app/auth.py; never enforced for an owner). Cleared by the API after POST /auth/password; the auth user_metadata copy is display-only.';

-- One-time backfill from the metadata the temp-password invites wrote, so everyone still on the
-- temporary password is asked to change it. Every role; never an owner. Only ever sets TRUE where
-- the metadata says so and the row is still false; a member who already changed their password
-- (the SPA wrote must_reset=false) is left alone. Re-running is a no-op. Skipped when auth.users is
-- not readable from this role (a local scratch database).
do $$
declare
  owners text[] := array['fzulfiqar@pie-int.com'];     -- = OWNER_EMAILS, lower case
begin
  if to_regclass('auth.users') is not null then
    update user_roles r
       set must_reset = true
      from auth.users u
     where lower(u.email) = lower(r.email)
       and lower(r.email) <> all (owners)
       and coalesce(u.raw_user_meta_data ->> 'must_reset', 'false') = 'true'
       and r.must_reset = false;
  end if;
exception when insufficient_privilege then
  raise notice 'auth.users not readable: must_reset backfill skipped';
end $$;

-- Self-check: the column exists with the right shape, no owner was flagged, and the table is
-- still not reachable by the browser roles (neither table nor column grants).
do $$
declare
  col record;
  owners text[] := array['fzulfiqar@pie-int.com'];     -- keep equal to the list above
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
  if exists (select 1 from user_roles where must_reset and lower(email) = any (owners)) then
    raise exception 'an owner row has must_reset set: the owner is never forced (break-glass login)';
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
