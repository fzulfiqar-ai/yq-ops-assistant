-- Team & access management migration (Step 3).
-- Run ONCE in the Supabase SQL editor (Dashboard → SQL Editor → New query → paste → Run).
-- Safe to re-run: every statement is IF NOT EXISTS / idempotent.

-- 1) Extend user_roles with per-user feature access + lifecycle fields.
alter table user_roles
  add column if not exists features   jsonb  not null default '[]'::jsonb,
  add column if not exists status     text   not null default 'active',
  add column if not exists invited_by text,
  add column if not exists full_name  text;

-- email must be unique so we can upsert by it.
create unique index if not exists user_roles_email_key on user_roles (lower(email));

-- 2) Pending invitations (admin invites a member → they set their own password).
create table if not exists app_invites (
  id          uuid        primary key default gen_random_uuid(),
  email       text        not null,
  role        text        not null default 'member',
  features    jsonb       not null default '[]'::jsonb,
  token       text        not null unique,
  status      text        not null default 'pending',   -- pending | accepted | revoked | expired
  invited_by  text,
  full_name   text,
  created_at  timestamptz not null default now(),
  expires_at  timestamptz not null default (now() + interval '7 days'),
  accepted_at timestamptz
);

create index if not exists app_invites_token_idx  on app_invites (token);
create index if not exists app_invites_email_idx  on app_invites (lower(email));

-- 3) Allow the new role set. The Phase 0 schema only permitted
--    ('admin','manager','viewer'); the team system uses 'member', so the original
--    CHECK constraint must be relaxed or member invites fail with a 23514 violation.
alter table user_roles drop constraint if exists user_roles_role_check;
alter table user_roles
  add constraint user_roles_role_check check (role in ('admin','member','manager','viewer'));

-- 4) Existing accounts: make sure the two seed admins have full access.
update user_roles
  set features = '["Dashboard","AI Agents","AI Assistant","Inventory","Sales","Margins","Receivables","Team"]'::jsonb,
      status   = 'active'
  where role = 'admin';
