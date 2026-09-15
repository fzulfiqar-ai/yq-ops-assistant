-- Close the cost-book read hole (06-Sep-2026). Zero cost, one statement per table.
--
-- THE HOLE. scripts/schema.sql creates, for eleven business tables:
--     create policy <t>_read on <t> for select to authenticated using (true);
-- The SPA signs in to Supabase directly with the publishable key shipped in the browser
-- bundle, so ANY account that can log in -- including a salesman granted only Catalog and
-- Product Finds -- can read those tables straight from PostgREST with devtools, bypassing
-- every FastAPI feature gate and leaving nothing in audit_log.
--
-- What that exposes: product_profitability (COGS and GP% per item), selling_prices (every
-- customer/warehouse rate), purchase_costs (landed cost), plus the full customer list,
-- every order line and the general ledger. That is the entire commercial position of the
-- business, readable by anyone who is ever given a login, and by anyone they lose it to.
--
-- WHY DROPPING THESE IS SAFE (verified, not assumed):
--   1. web/src queries Supabase for AUTH ONLY -- there is no .from() or .rpc() data read
--      anywhere in the SPA. Every figure it shows comes from the FastAPI API.
--   2. The backend talks to Supabase with the SERVICE key, which bypasses RLS entirely.
--   3. The SQL-RPC path runs VIEWS, and a view executes with its OWNER's rights, so view
--      reads are unaffected. Where that path touches base tables directly, yq_readonly has
--      its own policies from scripts/security_migration.sql -- untouched here.
--   4. dashboard/ui.py performs no direct table reads either.
--
-- ROLLBACK (only if something genuinely breaks -- prefer fixing the caller):
--   create policy <t>_read on <t> for select to authenticated using (true);
--
-- Idempotent. Apply with:  python -m scripts.apply_sql scripts/rls_lockdown_migration.sql

do $$
declare
  t text;
  n int := 0;
begin
  foreach t in array array[
    'categories','products','product_aliases','customers','orders','order_lines',
    'stock_movements','ledger_entries','product_profitability','selling_prices','purchase_costs'
  ]
  loop
    -- RLS must stay ON; we remove only the blanket read grant to `authenticated`.
    execute format('alter table %I enable row level security;', t);
    if exists (select 1 from pg_policies
               where schemaname = 'public' and tablename = t and policyname = t || '_read') then
      execute format('drop policy %I on %I;', t || '_read', t);
      n := n + 1;
    end if;
  end loop;
  raise notice 'rls_lockdown: dropped % blanket authenticated-read policies', n;
end $$;

-- app_invites had NO row-level security at all, so a pending invite token -- which is what
-- POST /team/accept exchanges for a working account at the invited role -- was readable by
-- anyone holding the publishable key. Enable RLS and grant nothing: the backend reads this
-- table with the service key, which is unaffected.
do $$
begin
  if to_regclass('public.app_invites') is not null then
    execute 'alter table app_invites enable row level security';
    execute 'drop policy if exists app_invites_read on app_invites';
    raise notice 'rls_lockdown: app_invites RLS enabled, no public policy';
  else
    raise notice 'rls_lockdown: app_invites not present, skipped';
  end if;
end $$;

-- Verification: this must return ZERO rows after the migration.
do $$
declare leftover int;
begin
  select count(*) into leftover
  from pg_policies
  where schemaname = 'public'
    and 'authenticated' = any(roles)
    and tablename in ('categories','products','product_aliases','customers','orders',
                      'order_lines','stock_movements','ledger_entries','product_profitability',
                      'selling_prices','purchase_costs','app_invites');
  if leftover > 0 then
    raise exception 'rls_lockdown FAILED: % authenticated-readable policies remain', leftover;
  end if;
  raise notice 'rls_lockdown: verified -- no authenticated read access to the cost book';
end $$;
