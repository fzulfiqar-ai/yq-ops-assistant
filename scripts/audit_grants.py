"""Audit (and emit a fix for) PostgREST-facing grants on the public schema.

THE HOLE (found 16-Sep-2026 while planning the marketplace). Supabase grants every object
that postgres creates in public to anon and authenticated by default (pg_default_acl):
SELECT, INSERT, UPDATE, DELETE, TRUNCATE on tables and views, EXECUTE on functions. Tables
are shielded by row-level security. VIEWS ARE NOT: a view runs with its owner's rights unless
security_invoker is set, so v_sales (every line with customer names), v_catalog_cost (landed
cost), stock and margin views were readable by anyone holding the publishable key that ships
in the browser bundle. Three tables had no RLS at all and were writable.

WHY REVOKING IS SAFE (verified, not assumed):
  1. web/src talks to Supabase for AUTH ONLY (no .from()/.rpc() data reads).
  2. The API uses the service key, which bypasses RLS and keeps its own grants.
  3. The AI read path runs run_readonly_query, owned by yq_readonly, which has explicit
     grants on views; views still execute as their owner (postgres). Do NOT flip views to
     security_invoker: that would make them run as yq_readonly and break that path.
  4. The keepalive workflow pings a dedicated keepalive_ping table (SELECT for anon, RLS on,
     no policy -> HTTP 200 with []), created by the emitted migration.

Usage:
    python -m scripts.audit_grants                     # inventory; exit 1 if anything is exposed
    python -m scripts.audit_grants --emit scripts/hotfix_view_grants_migration.sql
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

PUBLIC_ROLES = ("anon", "authenticated")
# The only thing the publishable key may touch in public: an empty ping table for the
# GitHub keepalive job. RLS is on with no policy, so a SELECT returns [] and still counts as
# database activity for Supabase's 7-day pause rule.
ALLOWLIST = {("keepalive_ping", "anon", "SELECT")}
# Blanket "authenticated can read everything" policies left over from scripts/schema.sql.
# With the table grants revoked they are inert; dropping them keeps pg_policies honest.
STALE_POLICIES = [
    ("app_settings", "app_settings_read"),
    ("ar_ageing", "ar_ageing_read"),
    ("catalog_items", "catalog_items_read"),
    ("customer_contacts", "customer_contacts_read"),
    ("salesman_targets", "salesman_targets_read"),
    ("stock_balance", "stock_balance_read"),
    ("user_roles", "user_roles_read"),
]


def q(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


def connect():
    url = os.getenv("DATABASE_URL")
    if not url:
        sys.exit("ERROR: DATABASE_URL not set in .env (Supabase -> Settings -> Database -> Session pooler URI).")
    import psycopg  # type: ignore

    return psycopg.connect(url)


def inventory(cur) -> dict:
    cur.execute("select viewname from pg_views where schemaname = 'public' order by 1")
    views = [r[0] for r in cur.fetchall()]
    cur.execute("select tablename, rowsecurity from pg_tables where schemaname = 'public' order by 1")
    tables = cur.fetchall()
    cur.execute("select sequencename from pg_sequences where schemaname = 'public' order by 1")
    seqs = [r[0] for r in cur.fetchall()]
    cur.execute(
        """
        select p.proname, pg_get_function_identity_arguments(p.oid), r.rolname,
               has_function_privilege('anon', p.oid, 'EXECUTE'),
               has_function_privilege('authenticated', p.oid, 'EXECUTE')
        from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        join pg_roles r on r.oid = p.proowner
        where n.nspname = 'public' and r.rolname = 'postgres'
        order by 1, 2
        """
    )
    funcs = cur.fetchall()
    cur.execute(
        """
        select table_name, grantee, privilege_type
        from information_schema.role_table_grants
        where table_schema = 'public' and grantee in ('anon', 'authenticated')
        order by 1, 2, 3
        """
    )
    grants = [tuple(r) for r in cur.fetchall()]
    cur.execute(
        """
        select tablename, policyname, array_to_string(roles, ',')
        from pg_policies
        where schemaname = 'public' and 'authenticated' = any(roles)
        order by 1, 2
        """
    )
    auth_policies = cur.fetchall()
    cur.execute(
        """
        select t.tablename
        from (select * from pg_tables where schemaname = 'public' offset 0) t
        where has_table_privilege('yq_readonly', format('%I.%I', t.schemaname, t.tablename), 'SELECT')
          and t.tablename <> 'keepalive_ping'
        order by 1
        """
    )
    readonly_tables = [r[0] for r in cur.fetchall()]
    return {
        "views": views,
        "tables": tables,
        "seqs": seqs,
        "funcs": funcs,
        "grants": grants,
        "auth_policies": auth_policies,
        "readonly_tables": readonly_tables,
    }


def exposed(inv: dict) -> list[str]:
    problems: list[str] = []
    for table, grantee, priv in inv["grants"]:
        if (table, grantee, priv) not in ALLOWLIST:
            problems.append(f"grant {priv} on {table} to {grantee}")
    for name, rls in inv["tables"]:
        if not rls:
            problems.append(f"table {name} has no row-level security")
    for name, args, _owner, anon_ok, auth_ok in inv["funcs"]:
        if anon_ok or auth_ok:
            problems.append(f"function {name}({args}) executable by anon/authenticated")
    for table, policy, roles in inv["auth_policies"]:
        problems.append(f"policy {policy} on {table} still targets {roles}")
    return problems


def emit_sql(inv: dict) -> str:
    today = date.today().isoformat()
    out: list[str] = []
    w = out.append
    w(f"-- Close the PostgREST grant hole on the public schema ({today}). Generated by")
    w("-- scripts/audit_grants.py --emit; one plain statement per object, nothing dynamic.")
    w("--")
    w("-- Supabase's default ACL grants every object postgres creates in public to anon and")
    w("-- authenticated. Tables were saved by RLS; VIEWS run with their owner's rights and were")
    w("-- readable with the publishable key (v_sales with customer names, v_catalog_cost, stock,")
    w("-- margins). Three tables had no RLS and were writable. See the header of audit_grants.py")
    w("-- for why revoking is safe. Rollback = GRANT the same privileges back (do not).")
    w("--")
    w("-- Apply:  python -m scripts.apply_sql scripts/hotfix_view_grants_migration.sql")
    w("-- Verify: python -m scripts.audit_grants   (exit 0 = nothing exposed)")
    w("")
    w("-- 1. Row-level security on every table (idempotent; service role bypasses RLS). Where the")
    w("--    assistant's yq_readonly role holds a SELECT grant, keep its read via a policy, mirroring")
    w("--    the existing *_yq_readonly_read pattern from scripts/security_migration.sql.")
    for name, _rls in inv["tables"]:
        if name == "keepalive_ping":
            continue
        w(f"alter table public.{q(name)} enable row level security;")
    for name in inv["readonly_tables"]:
        pol = q(name + "_yq_readonly_read")
        w(f"drop policy if exists {pol} on public.{q(name)};")
        w(f"create policy {pol} on public.{q(name)} for select to yq_readonly using (true);")
    w("")
    w("-- 2. Views: revoke everything from the browser-facing roles.")
    for v in inv["views"]:
        w(f"revoke all on table public.{q(v)} from anon, authenticated;")
    w("")
    w("-- 3. Tables: same. RLS stays on; the API keeps its service-role access.")
    for name, _rls in inv["tables"]:
        if name == "keepalive_ping":
            continue
        w(f"revoke all on table public.{q(name)} from anon, authenticated;")
    w("")
    w("-- 4. Sequences and postgres-owned functions.")
    for s in inv["seqs"]:
        w(f"revoke all on sequence public.{q(s)} from anon, authenticated;")
    w("--    Postgres grants EXECUTE on new functions to PUBLIC, so revoking from anon alone changes")
    w("--    nothing: revoke from PUBLIC and hand it back only to the API's service role.")
    for name, args, _owner, anon_ok, auth_ok in inv["funcs"]:
        if anon_ok or auth_ok:
            w(f"revoke execute on function public.{q(name)}({args}) from public, anon, authenticated;")
            w(f"grant execute on function public.{q(name)}({args}) to service_role;")
    w("")
    w("-- 5. Stale blanket read policies for authenticated (inert once grants are gone).")
    for table, policy in STALE_POLICIES:
        w(f"drop policy if exists {q(policy)} on public.{q(table)};")
    w("")
    w("-- 6. Stop the default ACL from re-granting future objects. Only the defaults owned by")
    w("--    postgres can be changed from this connection; objects created by supabase_admin")
    w("--    (extensions) keep theirs, which is why audit_grants.py must stay in the release check.")
    w("alter default privileges for role postgres in schema public revoke all on tables from anon, authenticated;")
    w("alter default privileges for role postgres in schema public revoke all on sequences from anon, authenticated;")
    w("alter default privileges for role postgres in schema public revoke all on functions from anon, authenticated;")
    w("")
    w("-- 7. The one thing the publishable key may still touch: an empty ping table for the")
    w("--    GitHub keepalive job (.github/workflows/keepalive.yml). RLS on, no policy -> [] + HTTP 200.")
    w("create table if not exists public.keepalive_ping (id smallint primary key);")
    w("alter table public.keepalive_ping enable row level security;")
    w("revoke all on table public.keepalive_ping from anon, authenticated;")
    w("grant select on table public.keepalive_ping to anon;")
    w("")
    w("-- 8. Verification: fails the transaction if anything is still exposed.")
    w("do $$")
    w("declare")
    w("  leftover int;")
    w("  norls int;")
    w("  pol int;")
    w("begin")
    w("  select count(*) into leftover")
    w("  from information_schema.role_table_grants")
    w("  where table_schema = 'public' and grantee in ('anon', 'authenticated')")
    w("    and not (table_name = 'keepalive_ping' and grantee = 'anon' and privilege_type = 'SELECT');")
    w("  if leftover > 0 then")
    w("    raise exception 'grant lockdown FAILED: % anon/authenticated grants remain on public', leftover;")
    w("  end if;")
    w("  select count(*) into norls from pg_tables where schemaname = 'public' and not rowsecurity;")
    w("  if norls > 0 then")
    w("    raise exception 'grant lockdown FAILED: % public tables without RLS', norls;")
    w("  end if;")
    w("  select count(*) into pol from pg_policies where schemaname = 'public' and 'authenticated' = any(roles);")
    w("  if pol > 0 then")
    w("    raise exception 'grant lockdown FAILED: % policies still target authenticated', pol;")
    w("  end if;")
    w("  raise notice 'grant lockdown: verified -- public schema is closed to anon/authenticated (keepalive_ping SELECT only)';")
    w("end $$;")
    w("")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    emit_path = None
    if "--emit" in argv:
        i = argv.index("--emit")
        if i + 1 >= len(argv):
            print("usage: python -m scripts.audit_grants [--emit <file.sql>]")
            return 1
        emit_path = Path(argv[i + 1])
    with connect() as conn:
        with conn.cursor() as cur:
            inv = inventory(cur)
    print(f"public schema: {len(inv['views'])} views, {len(inv['tables'])} tables, {len(inv['seqs'])} sequences, "
          f"{len(inv['funcs'])} postgres-owned functions")
    problems = exposed(inv)
    if emit_path:
        if not emit_path.is_absolute():
            emit_path = ROOT / emit_path
        emit_path.write_text(emit_sql(inv), encoding="utf-8", newline="\n")
        print(f"wrote {emit_path.relative_to(ROOT)} ({len(problems)} exposures to close)")
        return 0
    if problems:
        print(f"EXPOSED: {len(problems)} problems")
        for p in problems[:400]:
            print("  -", p)
        return 1
    print("OK: nothing in public is readable or writable with the publishable key (keepalive_ping SELECT only).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
