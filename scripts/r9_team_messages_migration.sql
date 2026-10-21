-- Release 2b (R9, 21-Oct-2026), X1: the team message, kept for the Command Centre's Copy button. Additive, idempotent.
--   Rehearse: python -m scripts.apply_sql scripts/r9_team_messages_migration.sql --rehearse
--   Apply:    python -m scripts.apply_sql scripts/r9_team_messages_migration.sql ; python -m scripts.audit_grants
--   Reverse:  scripts/r9_team_messages_reverse.sql
-- From the MAIN project folder only (a worktree finds the main folder's .env, which is production).
--
-- One row per load run: after a load's delivery step, scripts/yq_team_post.py stores the team message that
-- /yq-brief team prints (the text the owner copies to the salesmen's group) once its delivery manifest's checksum and
-- the privacy check have passed; GET /command/team-message (admin and management, behind _require_command) reads the
-- newest row, and the Command Centre shows it with a Copy button. Nothing is ever sent from here.
--   run         the load run the message was made for (the primary key: one message per run)
--   data_date   the Focus data date the figures stand on
--   text        the message as the owner copies it, trailing newline kept (salesmen's names and their figures)
--   sha256      the checksum of text: sha256(text) equals this column
--   created_at  when the run's message was FIRST stored: the poster's upsert on run sets run, data_date, text and
--               sha256 and never this column, so a re-post keeps the first post's time
-- Who reaches it: the poster connects with the owner login (DATABASE_URL), which owns the table; the API reads it with
-- the service key (service_role bypasses RLS). So RLS is ENABLED but NOT FORCED (a forced RLS with no policy would
-- shut the owner's poster out too) and there is no policy: anon / authenticated get nothing (revoked below), and NO
-- grant to yq_readonly: the text names people with their sales figures, and the AI's read role never needs it.
-- Nothing reads it before the 2b API: either order works.

set lock_timeout = '2s';      -- fail fast, never queue

create table if not exists team_messages (
  run        text        primary key,
  data_date  date        not null,
  text       text        not null,
  sha256     text        not null,
  created_at timestamptz not null default now()
);
alter table team_messages enable row level security;     -- enabled, never forced: the owner's poster writes; no policy
revoke all on table team_messages from anon, authenticated;

comment on table team_messages is
  'The team message of each load run (X1, release 2b): upserted on run by scripts/yq_team_post.py (the owner login) after the delivery step, read by GET /command/team-message (service role) for the Command Centre Copy button. RLS enabled, not forced, no policy (names and figures): never anon, authenticated or yq_readonly.';
comment on column team_messages.text is
  'The message as the owner copies it, trailing newline kept; sha256(text) equals the sha256 column.';
comment on column team_messages.created_at is
  'When the run''s message was first stored: the upsert on run never sets it, so a re-post keeps the first time.';

-- ── self-check ─────────────────────────────────────────────────────────────────
do $$
declare
  got text;
  want constant text := 'run text, data_date date, text text, sha256 text, created_at timestamp with time zone';
begin
  if to_regclass('public.team_messages') is null then
    raise exception 'r9_team_messages: team_messages missing';
  end if;
  select string_agg(a.attname || ' ' || format_type(a.atttypid, a.atttypmod), ', ' order by a.attnum) into got
    from pg_attribute a
   where a.attrelid = 'public.team_messages'::regclass and a.attnum > 0 and not a.attisdropped;
  if got is distinct from want then
    raise exception 'r9_team_messages: team_messages is (%), expected (%)', got, want;
  end if;
  if not exists (select 1 from pg_constraint c
                  where c.conrelid = 'public.team_messages'::regclass and c.contype = 'p'
                    and pg_get_constraintdef(c.oid) = 'PRIMARY KEY (run)') then
    raise exception 'r9_team_messages: the primary key is not (run)';
  end if;
  if exists (select 1 from information_schema.columns
              where table_schema = 'public' and table_name = 'team_messages' and is_nullable = 'YES') then
    raise exception 'r9_team_messages: every column must be not null';
  end if;
  if not exists (select 1 from pg_tables where schemaname = 'public' and tablename = 'team_messages' and rowsecurity) then
    raise exception 'r9_team_messages: team_messages must have RLS enabled';
  end if;
  if (select relforcerowsecurity from pg_class where oid = 'public.team_messages'::regclass) then
    raise exception 'r9_team_messages: team_messages must not FORCE row level security (the owner login posts the message, with no policy)';
  end if;
  if (select pg_get_userbyid(relowner) from pg_class where oid = 'public.team_messages'::regclass) is distinct from current_user then
    raise exception 'r9_team_messages: team_messages must be owned by the login that applies this file (the poster''s login)';
  end if;
  if exists (select 1 from pg_policies where schemaname = 'public' and tablename = 'team_messages') then
    raise exception 'r9_team_messages: team_messages must have no policy (service role only)';
  end if;
  if exists (select 1 from information_schema.role_table_grants
              where table_schema = 'public' and table_name = 'team_messages' and grantee in ('anon', 'authenticated')) then
    raise exception 'r9_team_messages: team_messages must not be granted to anon/authenticated';
  end if;
  if exists (select 1 from information_schema.column_privileges
              where table_schema = 'public' and table_name = 'team_messages' and grantee in ('anon', 'authenticated')) then
    raise exception 'r9_team_messages: team_messages has column grants to anon/authenticated';
  end if;
  if exists (select 1 from pg_roles where rolname = 'yq_readonly')
     and has_table_privilege('yq_readonly', 'public.team_messages', 'SELECT') then
    raise exception 'r9_team_messages: team_messages names people with their figures and must not be readable by yq_readonly';
  end if;
  -- the API runs as service_role: it must be able to use the table (Supabase's default grant).
  -- One privilege per call: a comma list answers "any of them".
  if exists (select 1 from pg_roles where rolname = 'service_role')
     and not (has_table_privilege('service_role', 'public.team_messages', 'SELECT')
              and has_table_privilege('service_role', 'public.team_messages', 'INSERT')) then
    raise exception 'r9_team_messages: service_role (the API) cannot read and write team_messages (check the default privileges)';
  end if;
  raise notice 'r9_team_messages: ok (table, primary key on run, RLS on, no policy, nothing for anon/authenticated/yq_readonly)';
end $$;
