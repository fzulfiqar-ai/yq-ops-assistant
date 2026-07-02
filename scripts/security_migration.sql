-- Security hardening — DB-level read-only floor for the SQL RPC path.
-- Idempotent; apply with:  python -m scripts.apply_migration scripts/security_migration.sql
--
-- What this does and why:
--   1. run_readonly_query() is SECURITY DEFINER and executes caller-supplied SQL text.
--      Its read-only property used to rest ENTIRELY on the app-layer validator
--      (app/sql_validator.py) — any bypass (e.g. a raw f-string query path) executed
--      with the definer's full rights. Transferring ownership to the existing
--      yq_readonly role (SELECT-only) makes writes physically impossible at the
--      privilege level, whatever SQL reaches the function.
--   2. Adds run_readonly_query_params() so app code can bind user-supplied values
--      ($1..$8) instead of interpolating them into SQL strings.
--   3. Grants yq_readonly SELECT on every current view + the enumerated business
--      tables the backend queries through the RPC. Deliberately NOT granted:
--      user_roles, app_invites, audit_log, pending_actions, agent_runs, agent_schedules,
--      kb_chunks, query_cache, ingest_runs, field_notes — auth/governance/memory tables
--      stay out of the SQL blast radius (the backend reads those via PostgREST).

-- ── 1. Role (already created by views.sql on most environments) ───────────────
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'yq_readonly') then
    create role yq_readonly nologin;
  end if;
end $$;

grant usage on schema public to yq_readonly;

-- ── 2. Grants: every view (curated derived data) + business tables used via RPC ─
do $$
declare v record;
begin
  for v in select viewname from pg_views where schemaname = 'public' loop
    execute format('grant select on %I to yq_readonly', v.viewname);
  end loop;
end $$;

grant select on
  purchase_orders, mrn_lines, mrn_landed_costs, selling_prices, supplier_prices,
  stock_balance, procurement_orders, procurement_events, order_files
to yq_readonly;

-- RLS is enabled on every table (schema.sql); yq_readonly does not bypass it the
-- way service_role / table owners do, so each granted table needs a read policy.
-- (Views are unaffected: they execute with their owner's rights.)
do $$
declare t text;
begin
  foreach t in array array[
    'purchase_orders','mrn_lines','mrn_landed_costs','selling_prices','supplier_prices',
    'stock_balance','procurement_orders','procurement_events','order_files'
  ] loop
    execute format('drop policy if exists %I on %I', t || '_yq_readonly_read', t);
    execute format(
      'create policy %I on %I for select to yq_readonly using (true)',
      t || '_yq_readonly_read', t);
  end loop;
end $$;

-- Ownership transfer below requires the applying role (postgres) to be a member of
-- yq_readonly. NOTE: must be a PLAIN statement — wrapping this grant in a DO block
-- crashes the Supabase backend (supautils intercepts role grants; observed
-- EDBHANDLEREXITED on PG 17.6, 2026-07).
grant yq_readonly to postgres;

-- ── 3. Re-create the RPC with a statement timeout, then hand it to yq_readonly ─
create or replace function run_readonly_query(sql_text text)
returns json
language plpgsql
security definer
set search_path = public
as $$
declare result json;
begin
    perform set_config('statement_timeout', '8000', true);
    execute format(
        'SELECT COALESCE(json_agg(t), ''[]''::json) FROM (%s) t',
        sql_text
    ) into result;
    return result;
end;
$$;

-- Changing a function's owner requires the NEW owner to hold CREATE on the schema
-- at transfer time; grant it transiently and revoke straight after (ownership sticks).
grant create on schema public to yq_readonly;
alter function run_readonly_query(text) owner to yq_readonly;
revoke execute on function run_readonly_query(text) from public;
revoke execute on function run_readonly_query(text) from anon;
revoke execute on function run_readonly_query(text) from authenticated;
grant  execute on function run_readonly_query(text) to service_role;

-- ── 4. Parameterized variant — binds a jsonb array of values as $1..$8 (text) ──
-- Callers cast in SQL where needed ($1::int, $2::date). IN-lists bind the whole
-- list as ONE jsonb param:  col IN (SELECT jsonb_array_elements_text($2::jsonb))
create or replace function run_readonly_query_params(sql_text text, params jsonb default '[]'::jsonb)
returns json
language plpgsql
security definer
set search_path = public
as $$
declare
    result json;
    p text[];
begin
    perform set_config('statement_timeout', '8000', true);
    select coalesce(array_agg(value), '{}') into p from jsonb_array_elements_text(params);
    execute format(
        'SELECT COALESCE(json_agg(t), ''[]''::json) FROM (%s) t',
        sql_text
    ) into result using p[1], p[2], p[3], p[4], p[5], p[6], p[7], p[8];
    return result;
end;
$$;

alter function run_readonly_query_params(text, jsonb) owner to yq_readonly;
revoke create on schema public from yq_readonly;
revoke execute on function run_readonly_query_params(text, jsonb) from public;
revoke execute on function run_readonly_query_params(text, jsonb) from anon;
revoke execute on function run_readonly_query_params(text, jsonb) from authenticated;
grant  execute on function run_readonly_query_params(text, jsonb) to service_role;
