"""R8m — Market Intel v2, the quality track: the migration, its reverse, and a replay of both.

    python -m tests.test_r8m_market_quality_replay
    YQ_LOCAL_PG_R8M=postgresql://postgres@127.0.0.1:<port>/<empty db> python -m tests.test_r8m_market_quality_replay

Same lightweight runner as tests/test_r7a_focus.py (no pytest). SUPABASE_URL, SUPABASE_KEY and DATABASE_URL
are pinned to ci.invalid before anything else is imported, nothing here imports the app, and the replay
connects to a loopback address only (refused otherwise, before any connection is made). Every name is
invented ("Supplier Alpha", "Brand X", "Rep One", "Shop Alpha").

Offline (always, CI included):
  * the migration read as text: additive (no DROP TABLE / VIEW / COLUMN, no row written or rewritten, no
    list seeded), every new table created IF NOT EXISTS with RLS on and revoked from anon / authenticated
    (its identity sequence too), the agent view revoked, the one GRANT (agent view -> yq_readonly) a plain
    statement outside every DO block, no CASCADE (only the two ON DELETE CASCADE keys of the link table),
    no security_invoker, no COMMIT (so --rehearse rolls it back), every value list of the contract, R7b's
    views and its GRANT untouched;
  * the reverse read as text: the yq.r8m_drop guard before any drop, no CASCADE, exactly the columns the
    migration adds are dropped (never R7b's yq_item_code), every table after what references it, nothing
    of R7b's dropped;
  * the replay's own guard: only 127.0.0.1 / localhost / ::1, the address pinned; the CI step; the docs.

With YQ_LOCAL_PG_R8M set (a throwaway local Postgres; SKIP without it, as in CI): in ONE transaction that is
always rolled back — a minimal schema with Supabase-style roles and default grants, R7b, R8m twice; R7b's
rows read the new defaults; the reverse runs without the switch while R8m holds nothing and refuses for
each kind of R8m data; the lists, a case, its timeline, actions and a report; the self-check on that data;
every CHECK / NOT NULL / UNIQUE / FK; the transitions NOT in the database; the append-only timeline (the
API's own role included); RLS / revokes as anon, authenticated, service_role and yq_readonly; the agent
view's numbers; the reverse refused, then accepted with the switch (R7b's rows untouched), again, R8m
again, R7b re-applied over it, and R7b's own reverse stopped until R8m is reversed first.
"""
from __future__ import annotations

import io
import os
import re
import sys
import traceback
import uuid
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The project folder's .env (production) is found by walk-up from a worktree: pin everything to ci.invalid
# BEFORE any import, and drop the libpq variables that could move a connection somewhere else.
os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"
for _var in ("PGHOST", "PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
    os.environ.pop(_var, None)

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


MIGRATION = ROOT / "scripts" / "r8m_market_quality_migration.sql"
REVERSE = ROOT / "scripts" / "r8m_market_quality_reverse.sql"
R7B = ROOT / "scripts" / "r7b_market_intel_migration.sql"
R7B_REVERSE = ROOT / "scripts" / "r7b_market_intel_reverse.sql"
CI = ROOT / ".github" / "workflows" / "ci.yml"

NEW_TABLES = ("suppliers", "brands", "market_issue_types", "market_cases", "market_case_events",
              "supplier_actions", "supplier_action_cases", "supplier_reports")
IDENTITY_TABLES = tuple(t for t in NEW_TABLES if t != "supplier_action_cases")
OBS_COLUMNS = ("case_id", "issue_type_id", "report_basis", "qty_affected", "batch_ref", "packaging_version",
               "safety_flag", "client_observed_at", "brand_id", "supplier_id", "brand_text", "supplier_text")
PHOTO_COLUMNS = ("role", "sha256", "thumb_path", "caption", "blurred_path")
ADDED = {"market_observations": set(OBS_COLUMNS), "market_photos": set(PHOTO_COLUMNS), "catalog_items": {"barcode"}}
R8M_INDEXES_ON_OLD_TABLES = ("market_observations_case_idx", "market_observations_issue_idx",
                             "market_observations_supplier_idx", "market_observations_brand_idx",
                             "market_observations_yq_code_idx", "market_observations_safety_idx",
                             "market_photos_sha256_idx", "catalog_items_barcode_idx")
R7B_TABLES = ("market_items", "market_observations", "market_photos", "market_item_decisions")
PERSONAL = ("title", "assignee", "created_by", "updated_by", "changed_by", "actor", "status_reason",
            "verification_reason", "note", "shop_name", "shop_customer_id", "salesman_id", "path", "thumb_path",
            "blurred_path", "caption", "email", "phone")
SELF_CHECK = "-- ── self-check"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _sql_body(sql: str) -> str:
    """The SQL without its -- comments, lower case (a comment may say anything)."""
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines()).lower()


def _do_blocks(body: str) -> list[str]:
    return re.findall(r"\bdo \$\$.*?\$\$;", body, flags=re.S)


def _outside_do(body: str) -> str:
    return re.sub(r"\bdo \$\$.*?\$\$;", "", body, flags=re.S)


def _columns(body: str, verb: str) -> dict[str, set[str]]:
    """{table: columns} of every `alter table [if exists] T ... <verb> column if [not] exists C`."""
    out: dict[str, set[str]] = {}
    for m in re.finditer(r"^alter table (?:if exists )?(\w+)(.*?);", body, flags=re.S | re.M):
        cols = re.findall(verb + r" column if (?:not )?exists (\w+)", m.group(2))
        if cols:
            out.setdefault(m.group(1), set()).update(cols)
    return out


def _pos(body: str, needle: str) -> int:
    i = body.find(needle)
    assert i >= 0, f"missing: {needle}"
    return i


# ═══════════════════════════════════════════════════════════════════════════════
# 1. the migration, read as text
# ═══════════════════════════════════════════════════════════════════════════════

@test("migration: every new table IF NOT EXISTS, RLS on, revoked from anon / authenticated with its identity sequence; the view revoked too")
def _():
    low = _sql_body(_read(MIGRATION))
    for t in NEW_TABLES:
        assert f"create table if not exists {t} (" in low, t
        assert f"alter table {t} enable row level security;" in low, t
        assert f"revoke all on table {t} from anon, authenticated;" in low, t
    for t in IDENTITY_TABLES:
        assert f"revoke all on sequence {t}_id_seq from anon, authenticated;" in low, t
    assert "supplier_action_cases_id_seq" not in low, "the link table has no identity column"
    assert "create or replace view v_market_cases_agent as" in low
    assert "revoke all on v_market_cases_agent from anon, authenticated;" in low
    assert "revoke all on function market_case_events_no_rewrite() from public, anon, authenticated;" in low
    assert "create policy" not in low, "RLS on with NO policy: service role only"


@test("migration: the one GRANT is the agent view to yq_readonly, a plain statement (never inside a DO block); R7b's grant and views untouched")
def _():
    mig = _read(MIGRATION)
    low = _sql_body(mig)
    assert re.findall(r"\bgrant\s+[^;]*;", _outside_do(low)) == ["grant select on v_market_cases_agent to yq_readonly;"]
    for block in _do_blocks(low):
        assert not re.search(r"\bgrant\s+(select|insert|update|delete|all|usage|execute)\b", block), \
            "a GRANT to a role must be a plain statement, not inside a DO block"
    assert not re.search(r"\brevoke\b[^;]*\bfrom\b[^;]*\byq_readonly\b", low), "nothing is taken from yq_readonly"
    for v in ("v_market_clusters", "v_market_signals_agent"):
        assert f"create or replace view {v}" not in low and f"drop view if exists {v}" not in low, v
    r7b = _sql_body(_read(R7B))
    assert re.findall(r"grant\s+[^;]*;", r7b) == ["grant select on v_market_signals_agent to yq_readonly;"]
    assert "has_table_privilege('yq_readonly', 'public.v_market_signals_agent', 'select')" in low, \
        "the self-check proves R7b's grant is still there"


@test("migration: additive — no DROP of a table / view / column, no row written or rewritten, no list seeded, no CASCADE, no security_invoker, no COMMIT")
def _():
    mig = _read(MIGRATION)
    low = _sql_body(mig)
    assert re.findall(r"^\s*drop\s+(\w+)", low, flags=re.M) == ["trigger", "trigger"], "only the two triggers are re-created"
    assert not re.search(r"^\s*(insert|update|delete|truncate|copy)\b", low, flags=re.M), "no row of any table is written"
    assert "insert into" not in low and "delete from" not in low, "suppliers / brands / issue types are never seeded here"
    without_fk = re.sub(r"on delete cascade", "", low)
    assert "cascade" not in without_fk, "no CASCADE (only the link table's two ON DELETE CASCADE keys)"
    assert low.count("on delete cascade") == 2
    link = low[_pos(low, "create table if not exists supplier_action_cases ("):]
    link = link[:link.index(");")]
    assert link.count("on delete cascade") == 2, "both cascades belong to supplier_action_cases"
    # never set; the closing self-check names it only to refuse it
    assert "security_invoker" not in _sql_body(mig[:mig.index(SELF_CHECK)])
    assert not re.search(r"security_invoker\s*=|with\s*\(\s*security_invoker", low)
    for sql in (mig, _read(REVERSE)):
        assert not re.search(r"^\s*(commit|end|begin)\s*;", sql, flags=re.I | re.M), "no COMMIT: --rehearse must roll it back"
        assert "set lock_timeout = '2s';" in _sql_body(sql), "a busy catalog fails the file fast instead of queueing"
    # nothing of R7b is altered except the columns it gains
    for t in R7B_TABLES:
        for m in re.finditer(rf"^alter table (?:if exists )?{t}\b(.*?);", low, flags=re.S | re.M):
            assert "drop" not in m.group(1) and "alter column" not in m.group(1), t
    assert "alter table market_items" not in low and "alter table market_item_decisions" not in low


@test("migration: the contract's value lists — 8 statuses, evidence 1-5, severity 1-3, 11 events, 5 kinds, 4 fields, 5 photo roles, bases, methods, action / report states")
def _():
    low = _sql_body(_read(MIGRATION))
    status = low[_pos(low, "status               text        not null default 'submitted'"):]
    status = status[:status.index("),")]
    for s in ("submitted", "under_review", "more_info_required", "verified", "not_substantiated",
              "action_requested", "resolved", "closed"):
        assert f"'{s}'" in status, s
    assert "check (evidence_level between 1 and 5)" in low and "check (severity between 1 and 3)" in low
    assert "evidence_level       smallint    not null default 1" in low
    for e in ("created", "status", "evidence", "question", "answer", "link", "move", "merge", "edit", "action",
              "safety_alert"):
        assert f"'{e}'" in low, e
    assert "check (kind in ('quality', 'packaging', 'demand', 'competitor', 'other'))" in low
    assert "array['qty', 'batch_ref', 'packaging_version', 'label_photo']::text[]" in low
    assert "check (role in ('product', 'packaging', 'label_batch', 'receipt', 'other'))" in low
    assert "default 'product'" in low
    assert "check (report_basis in ('customer_said', 'rep_saw'))" in low and "check (qty_affected > 0)" in low
    assert "check (verification_method in ('sample_inspected', 'photos_sufficient', 'several_shops'))" in low
    assert "check (status in ('open', 'responded', 'resolved', 'cancelled'))" in low
    assert "check (edition in ('supplier', 'internal'))" in low
    assert "check (status in ('draft', 'approved', 'superseded'))" in low
    assert "check (extract(day from month) = 1)" in low, "a report's month is its first day"
    assert "references market_cases(id) on delete set null" in low
    assert "create unique index if not exists suppliers_active_name_key on suppliers (lower(btrim(name))) where active;" in low
    assert "constraint market_issue_types_code_key unique (code)" in low
    assert "before update or delete on market_case_events" in low and "before truncate on market_case_events" in low
    assert "using errcode = 'restrict_violation'" in low


@test("migration: the needed R7b objects are checked FIRST (naming the file), the self-check comes LAST and covers RLS, grants, columns, triggers and the view")
def _():
    mig = _read(MIGRATION)
    low = _sql_body(mig)
    first = _do_blocks(low)[0]
    assert low.index(first) < low.index("create table if not exists"), "the preconditions run before anything is created"
    assert "r7b_market_intel_migration.sql" in first and "catalog_items" in first and "yq_readonly" in first
    assert mig.rstrip().endswith("end $$;") and SELF_CHECK in mig
    check = _sql_body(mig[mig.index(SELF_CHECK):])
    for t in NEW_TABLES:
        assert f"'{t}'" in check, t
    for c in OBS_COLUMNS + PHOTO_COLUMNS:
        assert f"'{c}'" in check, c
    for needle in ("rowsecurity", "pg_policies", "has_sequence_privilege('anon'", "'market_case_events_append_only'",
                   "'market_case_events_no_truncate'", "confdeltype = 'n'", "security_invoker", "'service_role'",
                   "raise notice 'r8m_market_quality: ok"):
        assert needle in check, needle
    view = low[_pos(low, "create or replace view v_market_cases_agent as"):]
    view = view[:view.index(";")]
    for col in PERSONAL:
        assert not re.search(rf"\bc\.{col}\b|\bas {col}\b", view), col
    assert "where c.merged_into is null" in view and "not p.has_people_flag" in view


# ═══════════════════════════════════════════════════════════════════════════════
# 2. the reverse, read as text
# ═══════════════════════════════════════════════════════════════════════════════

@test("reverse: the yq.r8m_drop guard runs before anything is dropped, counts every R8m table and column, and names the backup")
def _():
    rev = _read(REVERSE)
    low = _sql_body(rev)
    guard = _do_blocks(low)[0]
    first_change = min(m.start() for m in re.finditer(r"^(drop|alter)\s", low, flags=re.M))
    assert low.index(guard) < first_change
    assert "current_setting('yq.r8m_drop', true)" in guard and "<> 'yes'" in guard and "raise exception" in guard
    for t in NEW_TABLES:
        assert f"'{t}'" in guard, t
    for c in OBS_COLUMNS + PHOTO_COLUMNS + ("barcode",):
        assert c in guard, c
    assert "where safety_flag" in guard and "role <> ''product''" in guard, "defaults are not data"
    message = _do_blocks(rev.lower())[0]          # raw text: the message's own "--tables" is not a comment
    assert "db_backup --tables suppliers,brands," in message and "set yq.r8m_drop = ''yes'' in this session" in message
    assert "set yq.r8m_drop = 'yes';" in rev, "the header says how to drop anyway"


@test("reverse: every drop runs inside the guard's DO block (one statement: a refusal or a lock timeout drops nothing)")
def _():
    body = _sql_body(_read(REVERSE))
    outside = _outside_do(body).lower()
    assert "drop " not in outside and "alter table" not in outside, "a drop outside the guarded block"
    guard = next(b for b in _do_blocks(body) if "yq.r8m_drop" in b)
    assert guard.index("raise exception") < guard.lower().index("drop view if exists v_market_cases_agent")
    for needle in ("drop table if exists suppliers", "alter table if exists market_observations",
                   "drop function if exists market_case_events_no_rewrite"):
        assert needle in guard.lower(), needle


@test("reverse: drops exactly the columns the migration adds (never R7b's yq_item_code), every table after what references it, no CASCADE, nothing of R7b's")
def _():
    mig = _sql_body(_read(MIGRATION))
    low = _sql_body(_read(REVERSE))
    assert _columns(mig, "add") == ADDED
    assert _columns(low, "drop") == ADDED, _columns(low, "drop")
    assert "cascade" not in low and "security_invoker" not in low
    assert not re.search(r"^\s*(insert|update|delete|truncate)\b", low, flags=re.M)
    for t in NEW_TABLES:
        assert f"drop table if exists {t};" in low, t
    assert "drop view if exists v_market_cases_agent;" in low and "drop function if exists market_case_events_no_rewrite();" in low
    for ix in R8M_INDEXES_ON_OLD_TABLES:
        assert f"drop index if exists {ix};" in low, ix
    for t in R7B_TABLES + ("catalog_items", "salesmen", "shop_customers"):
        assert f"drop table if exists {t}" not in low, t
    for v in ("v_market_clusters", "v_market_signals_agent"):
        assert v not in low, v
    assert "alter table if exists market_observations" in low, "a table already gone is skipped, not an error"

    def at(needle):
        return _pos(low, needle)
    assert at("drop view if exists v_market_cases_agent;") < at("alter table if exists market_observations")
    for col, table in (("case_id", "market_cases"), ("issue_type_id", "market_issue_types"),
                       ("brand_id", "brands"), ("supplier_id", "suppliers")):
        assert at(f"drop column if exists {col}") < at(f"drop table if exists {table};"), col
    order = [at(f"drop table if exists {t};") for t in
             ("supplier_reports", "supplier_action_cases", "supplier_actions", "market_case_events", "market_cases",
              "market_issue_types", "brands", "suppliers")]
    assert order == sorted(order), order
    assert at("drop table if exists market_case_events;") < at("drop function if exists market_case_events_no_rewrite();")


# ═══════════════════════════════════════════════════════════════════════════════
# 3. the replay's address guard, CI and the docs
# ═══════════════════════════════════════════════════════════════════════════════

ENV_DSN = "YQ_LOCAL_PG_R8M"
LOOPBACK_HOSTS = {"localhost": "127.0.0.1", "127.0.0.1": "127.0.0.1", "::1": "::1"}
LOOPBACK_ADDRS = {"127.0.0.1", "::1"}


class NotLocal(ValueError):
    """The replay's database is not on this PC: nothing is connected to."""


def local_params(dsn: str) -> dict:
    """Connection parameters for a loopback DSN, with hostaddr PINNED to the loopback address, so no
    environment variable, service file or DNS answer can move the connection. Anything else raises
    NotLocal before a connection is attempted."""
    from psycopg.conninfo import conninfo_to_dict
    try:
        params = conninfo_to_dict(dsn)
    except Exception as exc:  # noqa: BLE001
        raise NotLocal(f"{ENV_DSN} is not a connection string ({type(exc).__name__})") from None
    if params.get("service") or params.get("servicefile"):
        raise NotLocal(f"{ENV_DSN} must not name a service (a service file can point anywhere)")
    host = str(params.get("host") or "").strip().lower()
    if not host or "," in host:
        raise NotLocal(f"{ENV_DSN} must name exactly one host: 127.0.0.1, localhost or ::1")
    if host not in LOOPBACK_HOSTS:
        raise NotLocal(f"{ENV_DSN} must point at this PC (127.0.0.1, localhost or ::1), never another host")
    addr = str(params.get("hostaddr") or "").strip()
    if addr and addr not in LOOPBACK_ADDRS:
        raise NotLocal(f"{ENV_DSN}: hostaddr must be 127.0.0.1 or ::1")
    params["host"] = host
    params["hostaddr"] = addr or LOOPBACK_HOSTS[host]
    params.setdefault("connect_timeout", "3")
    return params


@test("replay guard: only 127.0.0.1 / localhost / ::1, the address pinned; a remote host, a service, two hosts or a remote hostaddr are refused")
def _():
    try:
        import psycopg  # noqa: F401
    except ImportError:
        print("  SKIP: psycopg not installed")
        return
    p = local_params("postgresql://postgres@127.0.0.1:55499/r8m")
    assert (p["host"], p["hostaddr"], p["port"], p["dbname"]) == ("127.0.0.1", "127.0.0.1", "55499", "r8m")
    assert local_params("postgresql://postgres@localhost:55499/r8m")["hostaddr"] == "127.0.0.1"
    assert local_params("postgresql://postgres@[::1]:55499/r8m")["hostaddr"] == "::1"
    assert local_params("host=localhost port=55499 dbname=r8m user=postgres")["hostaddr"] == "127.0.0.1"
    for bad in ("postgresql://postgres@db.example.invalid:5432/postgres",
                "postgresql://postgres.abcdefghijklmnopqrst:secret@pooler.ci.invalid:5432/postgres",
                "postgresql://postgres@10.0.0.5:5432/postgres",
                "postgresql:///r8m",                                     # no host: PGHOST / a socket decides
                "host=127.0.0.1,db.example.invalid dbname=r8m",
                "host=localhost hostaddr=10.0.0.5 dbname=r8m",
                "host=localhost service=prod dbname=r8m",
                "not a dsn = = ="):
        try:
            local_params(bad)
        except NotLocal:
            continue
        raise AssertionError(f"accepted: {bad}")


@test("CI: the api job runs this suite (its replay SKIPs there: no Postgres)")
def _():
    ci = _read(CI)
    api = ci.split("\n  web:", 1)[0]
    assert "python -m tests.test_r8m_market_quality_replay" in api
    assert api.index("python -m tests.test_r7b_market_intel") < api.index("python -m tests.test_r8m_market_quality_replay")


@test("docs: MIGRATIONS.md lists both files with the switch, SCHEMA.md the eight tables, MARKET_INTEL.md a v2 section, the how-to in English and Arabic")
def _():
    mig = _read(ROOT / "docs" / "MIGRATIONS.md")
    assert "r8m_market_quality_migration.sql" in mig and "r8m_market_quality_reverse.sql" in mig
    assert "yq.r8m_drop" in mig and "built, not applied" in mig
    schema = _read(ROOT / "docs" / "SCHEMA.md")
    for t in NEW_TABLES + R7B_TABLES:
        assert f"`{t}`" in schema or f"### {t}" in schema, t
    mi = _read(ROOT / "docs" / "MARKET_INTEL.md")
    assert "## v2" in mi and "MARKET_INTEL_HOWTO.md" in mi
    howto = _read(ROOT / "docs" / "MARKET_INTEL_HOWTO.md")
    assert "Spotted" in howto and re.search(r"[؀-ۿ]{3,}", howto), "English and Arabic"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. the local Postgres replay (SKIP without YQ_LOCAL_PG_R8M; never anything but this PC)
# ═══════════════════════════════════════════════════════════════════════════════

MINIMAL_SCHEMA = """
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then create role service_role nologin bypassrls; end if;
  if not exists (select 1 from pg_roles where rolname = 'yq_readonly') then create role yq_readonly nologin; end if;
end $$;
create table salesmen (id bigint generated by default as identity primary key, name text not null,
  referral_code text not null unique, user_email text);
create table shop_customers (id bigint generated by default as identity primary key, phone text not null unique,
  name text, shop text, area text);
create table catalog_items (id bigint generated always as identity primary key, item_code text not null unique,
  display_name text, category text, brand text, is_active boolean default true,
  hidden boolean not null default false, created_at timestamptz default now(), updated_at timestamptz default now());
alter table salesmen enable row level security;
alter table shop_customers enable row level security;
alter table catalog_items enable row level security;
grant select on catalog_items to yq_readonly;
create policy catalog_items_yq_readonly_read on catalog_items for select to yq_readonly using (true);
-- Supabase's default ACL before the 16-Sep hotfix: every NEW object in public is granted to anon,
-- authenticated and service_role, so only the migrations' own revokes keep the new objects closed
alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;
alter default privileges in schema public grant all on functions to anon, authenticated, service_role;
"""

PRESENT_CHECK = """
select string_agg(c.relname, ', ' order by c.relname)
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relname in ('salesmen', 'shop_customers', 'catalog_items', 'market_items',
  'market_observations', 'market_photos', 'suppliers', 'brands', 'market_cases')
"""


def _step(text: str) -> None:
    print(f"  ok    {text}")


def _refused(cur, sql: str, params, exc, contains: str | None = None):
    """Run one statement in a savepoint; the database must refuse it with `exc`. Rolled back either way."""
    cur.execute("savepoint r8m_refused")
    try:
        cur.execute(sql, params)
    except exc as e:
        cur.execute("rollback to savepoint r8m_refused")
        if contains:
            assert contains in str(e), (contains, str(e)[:300])
        return e
    raise AssertionError(f"accepted, expected {exc.__name__}: {' '.join(sql.split())[:120]}")


def _one(cur, sql: str, params=None):
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row is not None and len(row) == 1 else row


def _present(cur, name: str) -> bool:
    return _one(cur, "select to_regclass(%s) is not null", (f"public.{name}",))


def _has_column(cur, table: str, column: str) -> bool:
    return _one(cur, "select exists (select 1 from information_schema.columns where table_schema = 'public' "
                     "and table_name = %s and column_name = %s)", (table, column))


def _gone(cur) -> None:
    """Nothing R8m added is left; everything of R7b's is."""
    for t in NEW_TABLES + ("v_market_cases_agent",):
        assert not _present(cur, t), t
    for table, cols in ADDED.items():
        for c in cols:
            assert not _has_column(cur, table, c), f"{table}.{c}"
    assert _one(cur, "select count(*) from pg_indexes where schemaname = 'public' and indexname = any(%s)",
                (list(R8M_INDEXES_ON_OLD_TABLES),)) == 0
    assert _one(cur, "select count(*) from pg_proc where proname = 'market_case_events_no_rewrite'") == 0
    for t in R7B_TABLES + ("catalog_items", "v_market_clusters", "v_market_signals_agent"):
        assert _present(cur, t), t
    assert _has_column(cur, "market_observations", "yq_item_code"), "R7b's column stays"


def _replay(conn, psycopg) -> None:
    E = psycopg.errors
    mig, rev, r7b, r7b_rev = _read(MIGRATION), _read(REVERSE), _read(R7B), _read(R7B_REVERSE)
    notices: list[str] = []
    conn.add_notice_handler(lambda diag: notices.append(diag.message_primary or ""))
    cur = conn.cursor()

    # ── 0. a minimal schema, R7b, R8m twice ──
    cur.execute(MINIMAL_SCHEMA)
    cur.execute(r7b)
    for made_by_hand in ("create table suppliers (id int)", "alter table catalog_items add column barcode text",
                         "alter table market_photos add column thumb_path text"):
        cur.execute("savepoint by_hand")
        cur.execute(made_by_hand)
        _refused(cur, mig, None, E.RaiseException, contains="made by hand")
        cur.execute("rollback to savepoint by_hand")
    _step("a first apply refuses to adopt a table or a column of the same name made by hand")
    cur.execute(mig)
    cur.execute(mig)                                          # idempotent
    assert sum("r8m_market_quality: ok" in n for n in notices) == 2, notices
    _step("minimal schema (Supabase-style roles and default grants) + R7b + R8m applied twice, self-check passed twice")

    # ── 1. R7b's rows, as the v1 capture writes them, read the new defaults ──
    cur.execute("insert into salesmen (name, referral_code) values ('Rep One', 'r8m-one'), ('Rep Two', 'r8m-two') returning id")
    rep1, rep2 = [r[0] for r in cur.fetchall()]
    cur.execute("insert into shop_customers (phone, name, shop) values ('ci-0001', 'Shop Alpha', 'Shop Alpha'), "
                "('ci-0002', 'Shop Beta', 'Shop Beta') returning id")
    shop1, shop2 = [r[0] for r in cur.fetchall()]
    cur.execute("insert into catalog_items (item_code, display_name, category, brand) values "
                "('T-10', 'Cable T-10', 'CABLE', 'Brand X'), ('C-20', 'Charger C-20', 'CHARGER', 'Brand X')")
    u = [str(uuid.uuid4()) for _ in range(3)]
    cur.execute("""insert into market_observations (client_uuid, source, kind, salesman_id, shop_customer_id, title, note,
                                                    yq_item_code, observed_at) values
                     (%s, 'rep', 'complaint', %s, %s, 'Cable T-10', 'stops charging after a week', 'T-10', now() - interval '1 day'),
                     (%s, 'rep', 'new_product', %s, null, 'Brand Y lamp', null, null, now()),
                     (%s, 'rep', 'complaint', %s, %s, 'Charger C-20', 'gets hot', 'C-20', now())
                   returning id""", (u[0], rep1, shop1, u[1], rep2, u[2], rep2, shop2))
    o1, o2, o3 = [r[0] for r in cur.fetchall()]
    cur.execute("insert into market_photos (observation_id, bucket, path, position) values "
                "(%s, 'finds', 'market/2026/10/a.webp', 0), (%s, 'finds', 'market/2026/10/b.webp', 1), "
                "(%s, 'finds', 'market/2026/10/c.webp', 0), (%s, 'finds', 'market/2026/10/d.webp', 0)", (o1, o1, o2, o3))
    assert _one(cur, "select array_agg(distinct role) from market_photos") == ["product"]
    assert _one(cur, "select count(*) from market_observations where safety_flag or case_id is not null "
                     "or issue_type_id is not null or report_basis is not null") == 0
    cur.execute("insert into market_photos (observation_id, path, role) values (%s, 'market/2026/10/e.webp', null)", (o2,))
    _step("R7b rows read the R8m defaults (photo role 'product', safety_flag false, links NULL); an explicit NULL role is allowed")

    # ── 2. the reverse while R8m holds nothing: no switch needed, R7b's rows untouched ──
    cur.execute("savepoint empty_reverse")
    cur.execute(rev)
    _gone(cur)
    assert _one(cur, "select count(*) from market_observations") == 3 and _one(cur, "select count(*) from market_photos") == 5
    cur.execute("rollback to savepoint empty_reverse")
    assert _present(cur, "market_cases") and _has_column(cur, "catalog_items", "barcode")
    _step("reverse WITHOUT the switch on an empty R8m: accepted (R7b's 3 sightings and 5 photos do not count, untouched)")

    # ── 3. each kind of R8m data alone makes the reverse refuse ──
    kinds = (("a supplier row", "insert into suppliers (name) values ('Supplier Alpha')", None),
             ("a catalog barcode", "update catalog_items set barcode = '0000000000017' where item_code = 'T-10'", None),
             ("a photo role", "update market_photos set role = 'packaging' where path = 'market/2026/10/b.webp'", None),
             ("a thumbnail", "update market_photos set thumb_path = 'market/2026/10/a-t320.webp' where path = 'market/2026/10/a.webp'", None),
             ("a safety flag", "update market_observations set safety_flag = true where id = %s", (o3,)),
             ("a batch", "update market_observations set batch_ref = 'B-01' where id = %s", (o1,)),
             ("a phone capture time", "update market_observations set client_observed_at = now() where id = %s", (o2,)))
    for label, sql, params in kinds:
        cur.execute("savepoint one_kind")
        cur.execute(sql, params)
        _refused(cur, rev, None, E.RaiseException, contains="yq.r8m_drop")
        cur.execute("rollback to savepoint one_kind")
    _step(f"reverse REFUSED for each kind of R8m data alone ({', '.join(k[0] for k in kinds)})")

    # ── 4. the lists, cases, timeline, actions and a report, as the v2 API writes them ──
    cur.execute("insert into suppliers (name, code, created_by) values ('Supplier Alpha', 'SA', 'boss@example.com'), "
                "('Supplier Beta', null, 'boss@example.com') returning id")
    sa, sb = [r[0] for r in cur.fetchall()]
    cur.execute("insert into brands (name, supplier_id, aliases) values ('Brand X', %s, '{BrandX}'), ('Brand Y', %s, default) "
                "returning id", (sa, sb))
    bx, by = [r[0] for r in cur.fetchall()]
    assert _one(cur, "select aliases from brands where id = %s", (by,)) == []
    cur.execute("""insert into market_issue_types (kind, code, label_en, label_ar, safety_default, fields, sort) values
                     ('quality', 'not_charging', 'Not charging', 'لا يشحن', false, '{qty,batch_ref}', 10),
                     ('quality', 'heat_smoke', 'Heat / smoke / swelling', 'حرارة أو دخان أو انتفاخ', true, '{qty,batch_ref,label_photo}', 5),
                     ('packaging', 'box_crushed', 'Box crushed', 'علبة مضغوطة', false, '{packaging_version}', 20)
                   returning id""")
    it_nc, it_heat, it_box = [r[0] for r in cur.fetchall()]
    cur.execute("""insert into market_cases (yq_item_code, brand_id, supplier_id, issue_type_id, title, first_seen, last_seen,
                                             obs_count, shop_count, evidence_level) values
                     ('T-10', %s, %s, %s, 'Cable T-10: Not charging', now() - interval '1 day', now(), 1, 1, 2)
                   returning id, status, evidence_level, safety""", (bx, sa, it_nc))
    case1, st, ev, safe = cur.fetchone()
    assert (st, ev, safe) == ("submitted", 2, False), "defaults: submitted, not safety"
    cur.execute("insert into market_cases (yq_item_code, issue_type_id, title, safety, severity, last_seen, obs_count, shop_count) "
                "values ('C-20', %s, 'Charger C-20: Heat / smoke / swelling', true, 3, now(), 1, 1) returning id", (it_heat,))
    case2 = cur.fetchone()[0]
    cur.execute("insert into market_cases (issue_type_id, title, merged_into) values (%s, 'Duplicate of case 1', %s) returning id",
                (it_nc, case1))
    case_dup = cur.fetchone()[0]
    cur.execute("""update market_observations set case_id = %s, issue_type_id = %s, report_basis = 'customer_said', qty_affected = 3,
                     batch_ref = 'B-2608', brand_id = %s, supplier_id = %s, brand_text = 'Brand X', supplier_text = 'Supplier Alpha',
                     client_observed_at = now() - interval '26 hours' where id = %s""", (case1, it_nc, bx, sa, o1))
    cur.execute("update market_observations set case_id = %s, issue_type_id = %s, report_basis = 'rep_saw', safety_flag = true "
                "where id = %s", (case2, it_heat, o3))
    cur.execute("update market_photos set role = 'label_batch', sha256 = repeat('a', 64), thumb_path = 'market/2026/10/b-t320.webp' "
                "where path = 'market/2026/10/b.webp'")
    cur.execute("update market_photos set has_people_flag = true, blurred_path = 'market/2026/10/d-blur.webp' "
                "where path = 'market/2026/10/d.webp'")
    cur.execute("insert into market_case_events (case_id, actor, event, to_status, detail) values "
                "(%s, 'rep1@example.com', 'created', 'submitted', jsonb_build_object('observation_id', %s::bigint)), "
                "(%s, 'rep2@example.com', 'created', 'submitted', '{}'), (%s, 'system', 'safety_alert', null, default) "
                "returning id, at", (case1, o1, case2, case2))
    events = cur.fetchall()
    ev1 = events[0][0]
    assert all(at is not None for _id, at in events)
    assert _one(cur, "select detail from market_case_events where id = %s", (events[2][0],)) == {}
    cur.execute("insert into supplier_actions (supplier_id, request, owner, due_date) values "
                "(%s, 'Root cause and corrective plan for T-10', 'clerk@example.com', current_date + 14), "
                "(%s, 'Replace the C-20 units of batch B-1', 'clerk@example.com', current_date + 7) returning id, status", (sa, sb))
    (act1, act1_status), (act2, _s) = cur.fetchall()
    assert act1_status == "open"
    cur.execute("update supplier_actions set status = 'resolved', supplier_response = 'replaced', "
                "resolution_check = 'samples inspected' where id = %s", (act2,))
    cur.execute("insert into supplier_action_cases (action_id, case_id, created_by) values (%s, %s, 'clerk@example.com'), "
                "(%s, %s, 'clerk@example.com')", (act1, case1, act2, case2))
    cur.execute("insert into supplier_reports (supplier_id, month, narrative, included_observation_ids, counts) values "
                "(%s, '2026-10-01', %s, array[%s::bigint], '{\"quality\": 1}') returning edition, status, downloads",
                (sa, f"Field reports this month: 1 [MI-{o1}]. Open action [CA-{act1}].", o1))
    assert cur.fetchone() == ("supplier", "draft", [])
    cur.execute("update catalog_items set barcode = '0000000000017' where item_code = 'T-10'")
    _step("lists, 3 cases (one merged), the timeline, 2 supplier actions with case links, a draft report; "
          "the new sighting / photo / barcode columns hold values")

    # ── 5. the migration's self-check on that data ──
    cur.execute(mig[mig.index(SELF_CHECK):])
    assert sum("r8m_market_quality: ok" in n for n in notices) == 3
    _step("the self-check passes on seeded data")

    # ── 6. every value rule: CHECK / NOT NULL / UNIQUE / FK ──
    bad = (
        ("insert into market_cases (issue_type_id, title, status) values (%s, 'x', 'open')", (it_nc,), E.CheckViolation),
        ("insert into market_cases (issue_type_id, title, evidence_level) values (%s, 'x', 0)", (it_nc,), E.CheckViolation),
        ("insert into market_cases (issue_type_id, title, evidence_level) values (%s, 'x', 6)", (it_nc,), E.CheckViolation),
        ("insert into market_cases (issue_type_id, title, severity) values (%s, 'x', 4)", (it_nc,), E.CheckViolation),
        ("update market_cases set verification_method = 'guess' where id = %s", (case1,), E.CheckViolation),
        ("insert into market_cases (title) values ('no issue type')", None, E.NotNullViolation),
        ("insert into market_cases (issue_type_id) values (%s)", (it_nc,), E.NotNullViolation),
        ("insert into market_cases (issue_type_id, title) values (%s, '   ')", (it_nc,), E.CheckViolation),
        ("update market_cases set merged_into = id where id = %s", (case1,), E.CheckViolation),
        ("update market_cases set obs_count = -1 where id = %s", (case1,), E.CheckViolation),
        ("update market_cases set status = null where id = %s", (case1,), E.NotNullViolation),
        ("insert into market_cases (issue_type_id, title) values (999999, 'x')", None, E.ForeignKeyViolation),
        ("insert into market_issue_types (kind, code, label_en) values ('price', 'price_gap', 'Price')", None, E.CheckViolation),
        ("insert into market_issue_types (kind, code, label_en) values ('quality', 'Not Charging', 'x')", None, E.CheckViolation),
        ("insert into market_issue_types (kind, code, label_en) values ('quality', 'not_charging', 'Again')", None, E.UniqueViolation),
        ("insert into market_issue_types (kind, code, label_en, fields) values ('quality', 'q2', 'Q', '{colour}')", None, E.CheckViolation),
        ("insert into market_issue_types (code, label_en) values ('q3', 'Q')", None, E.NotNullViolation),
        ("insert into market_issue_types (kind, code, label_en) values ('quality', 'q4', ' ')", None, E.CheckViolation),
        ("insert into suppliers (name) values ('  SUPPLIER ALPHA ')", None, E.UniqueViolation),
        ("insert into suppliers (name) values ('  ')", None, E.CheckViolation),
        ("update suppliers set merged_into = id where id = %s", (sa,), E.CheckViolation),
        ("insert into brands (name, supplier_id) values ('Brand Z', 999999)", None, E.ForeignKeyViolation),
        ("update market_observations set report_basis = 'heard' where id = %s", (o1,), E.CheckViolation),
        ("update market_observations set qty_affected = 0 where id = %s", (o1,), E.CheckViolation),
        ("update market_observations set safety_flag = null where id = %s", (o1,), E.NotNullViolation),
        ("update market_observations set case_id = 999999 where id = %s", (o2,), E.ForeignKeyViolation),
        ("update market_observations set issue_type_id = 999999 where id = %s", (o2,), E.ForeignKeyViolation),
        ("update market_photos set role = 'selfie' where path = 'market/2026/10/a.webp'", None, E.CheckViolation),
        ("update supplier_actions set status = 'done' where id = %s", (act1,), E.CheckViolation),
        ("insert into supplier_actions (supplier_id) values (%s)", (sa,), E.NotNullViolation),
        ("insert into supplier_reports (supplier_id, month, edition) values (%s, '2026-10-01', 'public')", (sa,), E.CheckViolation),
        ("insert into supplier_reports (supplier_id, month, status) values (%s, '2026-10-01', 'sent')", (sa,), E.CheckViolation),
        ("insert into supplier_reports (supplier_id, month) values (%s, '2026-10-15')", (sa,), E.CheckViolation),
        ("insert into supplier_reports (month) values ('2026-10-01')", None, E.NotNullViolation),
        ("insert into market_case_events (case_id, actor, event) values (%s, 'x', 'deleted')", (case1,), E.CheckViolation),
        ("insert into market_case_events (case_id, event) values (%s, 'edit')", (case1,), E.NotNullViolation),
        ("insert into market_case_events (case_id, actor) values (%s, 'x')", (case1,), E.NotNullViolation),
        ("insert into market_case_events (case_id, actor, event) values (999999, 'x', 'edit')", None, E.ForeignKeyViolation),
        ("insert into supplier_action_cases (action_id, case_id) values (%s, %s)", (act1, case1), E.UniqueViolation),
    )
    for sql, params, exc in bad:
        _refused(cur, sql, params, exc)
    cur.execute("savepoint one_claim")
    claim = ("insert into market_case_events (case_id, actor, event, detail) values "
             "(%s, 'system', 'safety_alert', '{\"email\": \"queued\"}')")
    cur.execute(claim, (case1,))
    _refused(cur, claim, (case1,), E.UniqueViolation)
    cur.execute("insert into market_case_events (case_id, actor, event, detail) values "
                "(%s, 'system', 'safety_alert', '{\"email\": \"sent\"}'), "
                "(%s, 'system', 'safety_alert', '{\"email\": \"not_sent\"}')", (case1, case1))   # outcomes are free
    cur.execute(claim, (case2,))                                                              # another case claims its own
    cur.execute("rollback to savepoint one_claim")
    _step("one safety-alert claim per case: a second claim is refused; outcomes and other cases are not")
    cur.execute("savepoint one_approved")
    cur.execute("insert into supplier_reports (supplier_id, month, status) values (%s, '2026-11-01', 'approved')", (sa,))
    _refused(cur, "insert into supplier_reports (supplier_id, month, status) values (%s, '2026-11-01', 'approved')", (sa,),
             E.UniqueViolation)
    cur.execute("insert into supplier_reports (supplier_id, month, status) values (%s, '2026-11-01', 'superseded'), "
                "(%s, '2026-11-01', 'superseded')", (sa, sa))                 # superseded ones may pile up
    cur.execute("insert into supplier_reports (supplier_id, month, edition, status) values "
                "(%s, '2026-11-01', 'internal', 'approved')", (sa,))           # the other edition is its own
    cur.execute("rollback to savepoint one_approved")
    _step("one approved report per supplier, month and edition: a second is refused; superseded ones and the "
          "other edition are not")
    cur.execute("insert into suppliers (name, active) values ('supplier alpha', false)")       # an inactive duplicate is fine
    cur.execute("insert into supplier_reports (supplier_id, month, edition) values (%s, '2026-10-01', 'internal')", (sa,))
    _step(f"{len(bad)} bad values refused (statuses, evidence 1-5, severity, methods, kinds, slugs, fields, roles, "
          "bases, qty, states, editions, the 1st of the month, NOT NULLs, uniques, FKs); an inactive duplicate name is allowed")

    # ── 7. the workflow belongs to the API: the database lets any status follow any other ──
    for s in ("closed", "submitted", "resolved", "more_info_required", "verified"):
        cur.execute("update market_cases set status = %s where id = %s", (s, case1))
    cur.execute("update market_cases set evidence_level = 5 where id = %s", (case1,))
    cur.execute("update market_cases set evidence_level = 1, verification_method = null where id = %s", (case1,))
    cur.execute("update market_cases set status = 'submitted', evidence_level = 2 where id = %s", (case1,))
    _step("transitions, 'evidence only goes up' and what Verified needs are NOT in the database (the API holds them)")

    # ── 8. the timeline is append-only, for the API's own role too ──
    for stmt in (f"update market_case_events set actor = 'x' where id = {ev1}",
                 f"delete from market_case_events where id = {ev1}", "truncate market_case_events"):
        _refused(cur, stmt, None, E.RestrictViolation, contains="append-only")
    cur.execute("savepoint as_api")
    cur.execute("set local role service_role")
    cur.execute("insert into market_case_events (case_id, actor, event, from_status, to_status) "
                "values (%s, 'clerk@example.com', 'status', 'submitted', 'under_review')", (case1,))
    _refused(cur, f"delete from market_case_events where id = {ev1}", None, E.RestrictViolation)
    cur.execute("rollback to savepoint as_api")
    _refused(cur, "delete from market_cases where id = %s", (case1,), E.ForeignKeyViolation)
    _step("the case timeline refuses UPDATE / DELETE / TRUNCATE (service_role too); a case with a timeline cannot be deleted")

    # ── 9. what a delete does to the links ──
    cur.execute("insert into market_cases (issue_type_id, title) values (%s, 'No timeline yet') returning id", (it_box,))
    case3 = cur.fetchone()[0]
    cur.execute("update market_observations set case_id = %s where id = %s", (case3, o2))
    cur.execute("delete from market_cases where id = %s", (case3,))
    assert _one(cur, "select case_id from market_observations where id = %s", (o2,)) is None, "on delete set null"
    cur.execute("savepoint drop_action")
    cur.execute("delete from supplier_actions where id = %s", (act2,))
    assert _one(cur, "select count(*) from supplier_action_cases where action_id = %s", (act2,)) == 0, "links go with the action"
    cur.execute("rollback to savepoint drop_action")
    _refused(cur, "delete from suppliers where id = %s", (sa,), E.ForeignKeyViolation)
    _refused(cur, "delete from market_issue_types where id = %s", (it_nc,), E.ForeignKeyViolation)
    _refused(cur, "delete from brands where id = %s", (bx,), E.ForeignKeyViolation)
    cur.execute("savepoint drop_supplier")
    cur.execute("delete from supplier_action_cases where action_id = %s", (act2,))
    cur.execute("delete from supplier_actions where id = %s", (act2,))
    cur.execute("delete from suppliers where id = %s", (sb,))
    assert _one(cur, "select supplier_id from brands where id = %s", (by,)) is None, "brands.supplier_id: on delete set null"
    cur.execute("rollback to savepoint drop_supplier")
    _step("a removed case clears the sighting's link; an action's case links go with it; a supplier, brand or issue type "
          "in use cannot be deleted (deactivate or merge); a brand outlives its supplier")

    # ── 10. closed to the publishable key; the API reads and writes; yq_readonly reads the agent view only ──
    for t in NEW_TABLES:
        assert _one(cur, "select relrowsecurity from pg_class where oid = %s::regclass", (f"public.{t}",)) is True, t
        assert _one(cur, "select count(*) from pg_policies where schemaname = 'public' and tablename = %s", (t,)) == 0, t
        for role in ("anon", "authenticated"):
            assert not _one(cur, "select has_table_privilege(%s, %s, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')",
                            (role, f"public.{t}")), (role, t)
        assert not _one(cur, "select has_table_privilege('yq_readonly', %s, 'SELECT')", (f"public.{t}",)), t
    for t in IDENTITY_TABLES:
        for role in ("anon", "authenticated"):
            assert not _one(cur, "select has_sequence_privilege(%s, %s, 'USAGE,SELECT,UPDATE')", (role, f"public.{t}_id_seq")), t
    for role in ("anon", "authenticated"):
        assert not _one(cur, "select has_table_privilege(%s, 'public.v_market_cases_agent', 'SELECT')", (role,))
        assert not _one(cur, "select has_function_privilege(%s, 'public.market_case_events_no_rewrite()', 'EXECUTE')", (role,))
        cur.execute("savepoint as_public")
        cur.execute(f"set local role {role}")
        for stmt in ("select count(*) from market_cases", "select count(*) from v_market_cases_agent",
                     "insert into suppliers (name) values ('Supplier Gamma')"):
            _refused(cur, stmt, None, E.InsufficientPrivilege)
        cur.execute("rollback to savepoint as_public")
    cur.execute("savepoint as_api")
    cur.execute("set local role service_role")
    cur.execute("insert into suppliers (name) values ('Supplier Gamma') returning id")
    assert _one(cur, "select count(*) from suppliers where active") == 3
    cur.execute("rollback to savepoint as_api")
    cur.execute("savepoint as_readonly")
    cur.execute("set local role yq_readonly")
    cur.execute("select case_id, issue_code, brand, supplier, usable_photos, open_actions, safety from v_market_cases_agent "
                "order by case_id")
    rows = cur.fetchall()
    assert rows == [(case1, "not_charging", "Brand X", "Supplier Alpha", 2, 1, False),
                    (case2, "heat_smoke", None, None, 0, 0, True)], rows
    assert case_dup not in [r[0] for r in rows], "a merged case is left out"
    assert _one(cur, "select count(*) from v_market_signals_agent") >= 1, "R7b's grant still works"
    for stmt in ("select count(*) from market_cases", "select count(*) from suppliers", "select count(*) from v_market_clusters"):
        _refused(cur, stmt, None, E.InsufficientPrivilege)
    cur.execute("rollback to savepoint as_readonly")
    cur.execute("select column_name from information_schema.columns where table_schema = 'public' "
                "and table_name = 'v_market_cases_agent'")
    cols = {r[0] for r in cur.fetchall()}
    assert not cols & set(PERSONAL), cols & set(PERSONAL)
    assert _one(cur, "select coalesce(array_to_string(reloptions, ','), '') from pg_class "
                     "where oid = 'public.v_market_cases_agent'::regclass") == ""
    _step("RLS on and no policy on the 8 tables; anon / authenticated refused on tables, sequences, view and trigger function; "
          "service_role reads and writes; yq_readonly reads the agent view (people-flagged photos, merged cases and "
          "closed actions left out) and R7b's view, nothing else")

    # ── 11. the reverse refuses while R8m holds data ──
    _refused(cur, rev, None, E.RaiseException, contains="yq.r8m_drop")
    assert _present(cur, "market_cases")
    _step("reverse REFUSED with the cases, lists, timeline, actions and report in place")

    # ── 12. with the switch it drops everything R8m added and nothing of R7b's ──
    cur.execute("set local yq.r8m_drop = 'yes'")
    cur.execute(rev)
    _gone(cur)
    assert _one(cur, "select count(*) from market_observations") == 3 and _one(cur, "select count(*) from market_photos") == 5
    assert _one(cur, "select yq_item_code from market_observations where id = %s", (o1,)) == "T-10"
    assert _one(cur, "select count(*) from catalog_items") == 2
    assert _one(cur, "select has_table_privilege('yq_readonly', 'public.v_market_signals_agent', 'SELECT')") is True
    cur.execute(rev)                                          # again: nothing left, no error
    cur.execute("set local yq.r8m_drop = 'no'")
    cur.execute(rev)                                          # and without the switch: nothing to count
    assert sum("r8m_market_quality reverse: ok" in n for n in notices) == 4, notices
    _step("with SET yq.r8m_drop = 'yes': everything R8m added is gone, R7b's sightings, photos, codes and grant untouched; "
          "the reverse runs again (with and without the switch)")

    # ── 13. R8m again after the reverse, R7b re-applied over it, R7b's reverse stopped by R8m ──
    cur.execute(mig)
    cur.execute(mig)
    cur.execute(r7b)
    assert _present(cur, "v_market_cases_agent") and _has_column(cur, "market_photos", "role")
    assert _one(cur, "select array_agg(distinct role) from market_photos") == ["product"], "re-added: every photo reads 'product'"
    _refused(cur, r7b_rev, None, E.DependentObjectsStillExist)
    assert sum("r8m_market_quality: ok" in n for n in notices) == 5
    _step("R8m applied twice again after the reverse; R7b re-applied over R8m; R7b's own reverse is stopped "
          "(without CASCADE) until R8m is reversed first")


@test("local replay: R7b, R8m twice, every rule, the reverse refused / accepted with the switch, R8m again (SKIP without YQ_LOCAL_PG_R8M)")
def _():
    dsn = os.environ.get(ENV_DSN, "").strip()
    if not dsn:
        print(f"  SKIP: {ENV_DSN} is not set (a throwaway local Postgres, e.g. postgresql://postgres@127.0.0.1:55432/r8m)")
        return
    try:
        import psycopg
    except ImportError:
        print("  SKIP: psycopg not installed")
        return
    params = local_params(dsn)                       # NotLocal: refused before any connection is made
    conn = psycopg.connect(**params)
    try:
        cur = conn.cursor()
        addr = _one(cur, "select host(inet_server_addr())")
        assert addr in LOOPBACK_ADDRS, "the server answered from an address that is not this PC"
        present = _one(cur, PRESENT_CHECK)
        assert not present, f"{ENV_DSN} must point at an EMPTY database (this one already has: {present})"
        assert _one(cur, "show server_encoding") == "UTF8", "the replay database must be UTF8 (initdb -E UTF8), like Supabase"
        conn.rollback()
        print(f"  replay on {params['host']}:{params.get('port', '5432')}/{params.get('dbname', '')} "
              f"(PostgreSQL {conn.info.server_version // 10000}), one transaction, rolled back")
        _replay(conn, psycopg)
    finally:
        conn.rollback()
        conn.close()


def main() -> int:
    passed = failed = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"PASS  {name}")
            passed += 1
        except Exception:  # noqa: BLE001
            print(f"FAIL  {name}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
