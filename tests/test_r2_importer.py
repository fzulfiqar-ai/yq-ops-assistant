"""R2a transactional batch importer (24-Sep-2026): the loader rules before staging, Decimal
totals, the shared diff SQL, the migration's contract (one transaction, advisory lock, asserted
totals, service_role-only RPCs), the preview's exceptions and the routes -- plus a REPLAY on the
local scratch Postgres: production's Focus tables as they were before the 240926 load (the
2026-09-24_pre-r0 backup, merchant names masked), the batch importer run on 240926/, the result
compared with production's current tables (counts, money sums, per-day sales), then a second
preview (0 inserts), a tampered commit (raises, nothing changes), the undo-overlap refusal and a
full undo back to the byte-identical starting state.

    python -m tests.test_r2_importer

Same lightweight runner as tests/test_v3.py (no pytest). The pure tests need no database and no
.env (CI runs them with SUPABASE_URL=https://ci.invalid only). The local-Postgres tests SKIP,
printing why, when the scratch cluster on port 55432, the backup folder, the cached schema or the
240926 drop is not there. Production is only ever READ (SELECT, read-only session) to fetch the
comparison figures; without a DATABASE_URL in the main checkout's .env the figures recorded on
24-Sep-2026 are used.

Production hygiene: nothing in this module can reach production through the app. No .env is
loaded into the environment; SUPABASE_URL and DATABASE_URL are pinned to ci.invalid before any
app import (a worktree under the main checkout would otherwise find the real .env), and the real
DATABASE_URL is read by _prod_stats() alone, straight from the .env file into a read-only session.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import sys
import tempfile
import time
import traceback
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_SHELL_DATABASE_URL = os.environ.get("DATABASE_URL")            # only ever used read-only, by _prod_stats()
os.environ.setdefault("SUPABASE_URL", "https://ci.invalid")     # app.database.get_client() can build nothing real
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"   # DirectBackend.open() can reach nothing real

import warnings  # noqa: E402
warnings.filterwarnings("ignore")

TESTS: list[tuple[str, object]] = []

MAIN = Path(os.environ.get("YQ_MAIN_CHECKOUT") or (ROOT.parents[2] if ROOT.parent.name == "worktrees" else ROOT))


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


MIGRATION = ROOT / "scripts" / "ingest_batches_migration.sql"
REVERSE = ROOT / "scripts" / "ingest_batches_reverse.sql"

# ── local replay cluster ──────────────────────────────────────────────────────
PG_PORT = int(os.environ.get("YQ_LOCAL_PG_PORT", "55432"))
PG_DB = "r2_import"
PG_ADMIN_DSN = f"host=localhost port={PG_PORT} user=postgres dbname=postgres connect_timeout=3"
PG_DSN = f"host=localhost port={PG_PORT} user=postgres dbname={PG_DB} connect_timeout=3"
BACKUP = MAIN / "business_data" / "backups" / "2026-09-24_pre-r0"
SCHEMA_CACHE = MAIN / "business_data" / "ingest_previews" / "_schema" / "prod_tables_schema.sql"
DROP_240926 = MAIN / "240926"
DROP_210926 = MAIN / "210926" / "As On date Reports"
REPLAY_TABLES = ["categories", "products", "product_aliases", "customers", "orders", "order_lines", "stock_movements",
                 "ledger_entries", "stock_balance", "ar_ageing", "product_profitability", "selling_prices", "mrn_lines",
                 "mrn_landed_costs", "purchase_costs", "ingest_runs"]
FOCUS_TABLES = ["orders", "order_lines", "stock_movements", "ledger_entries", "stock_balance", "ar_ageing",
                "product_profitability", "selling_prices"]
# merchant names never reach the scratch cluster: masked consistently in the backup copy AND the files
MASK_COLS = {"customers": {"name"}, "orders": {"customer_name"}, "order_lines": {"customer_account"},
             "ar_ageing": {"account"}, "selling_prices": {"customer_name"}, "ledger_entries": {"account", "counter_account"}}
BLANK_COLS = {"order_lines": {"narration"}, "stock_movements": {"narration"}, "ledger_entries": {"narration"}}
# the API's session budget the replay runs under (INGEST_STATEMENT_TIMEOUT_S defaults to 600 s on the
# API; a fifth of that here, so a regression towards the old minutes-long nested loops fails the test)
REPLAY_TIMEOUT_S = 120
# production's Focus tables after the 240926 load (read 24-Sep-2026 12:5x UTC, read-only), the
# fallback when DATABASE_URL is not available for a live read
PROD_AFTER_240926 = {
    "orders": {"rows": 2743, "gross_bhd": "74238.130"},
    "order_lines": {"rows": 11956, "gross_bhd": "74238.130", "taxable_bhd": "67490.060"},
    "stock_movements": {"rows": 36267, "received_qty": "219477.000", "issued_qty": "156575.000"},
    "stock_balance": {"as_of": "2026-09-24", "rows": 132, "net_qty": "45516.000", "total_value_bhd": "67489.310"},
    "ar_ageing": {"as_of": "2026-09-24", "rows": 84, "balance_bhd": "9078.860"},
    "product_profitability": {"as_of": "2026-09-24", "rows": 161, "gross_bhd": "64904.830"},
    "per_day_tail": {"2026-09-20": (45, "334.800"), "2026-09-21": (73, "421.650"), "2026-09-22": (47, "594.450"),
                     "2026-09-23": (70, "482.250"), "2026-09-24": (10, "184.900")},
}
_STATE: dict = {}   # shared between the local tests (connection, checksums, batch ids)


def _mask(v):
    if v in (None, ""):
        return None
    return "M-" + hashlib.sha1(str(v).encode("utf-8")).hexdigest()[:12]


def mask_row(target: str, row: dict) -> dict:
    r = dict(row)
    for c in MASK_COLS.get(target, ()):
        if c in r:
            r[c] = _mask(r[c])
    for c in BLANK_COLS.get(target, ()):
        if c in r:
            r[c] = None
    return r


def _local_reason() -> str | None:
    try:
        import psycopg
        with psycopg.connect(PG_ADMIN_DSN) as c:
            c.execute("select 1")
    except Exception as e:  # noqa: BLE001
        return f"local Postgres on port {PG_PORT} unreachable ({str(e)[:60]})"
    if not BACKUP.is_dir():
        return f"backup folder missing: {BACKUP}"
    if not SCHEMA_CACHE.is_file():
        return f"schema cache missing: {SCHEMA_CACHE} (pg_dump --schema-only of the Focus tables)"
    if not DROP_240926.is_dir():
        return f"drop folder missing: {DROP_240926}"
    return None


def _bootstrap():
    """A fresh r2_import database: production's table schema (cached dump), the batch migration,
    the pre-240926 backup (masked) and the identity sequences set. Never touches 'drill'."""
    import psycopg
    from psycopg import sql as psql
    with psycopg.connect(PG_ADMIN_DSN, autocommit=True) as admin:
        for role in ("anon", "authenticated", "service_role", "yq_readonly"):
            admin.execute(f"do $$ begin if not exists (select 1 from pg_roles where rolname = '{role}') then "
                          f"create role {role} nologin; end if; end $$")
        admin.execute(psql.SQL("drop database if exists {} with (force)").format(psql.Identifier(PG_DB)))
        admin.execute(psql.SQL("create database {}").format(psql.Identifier(PG_DB)))
    conn = psycopg.connect(PG_DSN, autocommit=True)
    schema = "\n".join(ln for ln in SCHEMA_CACHE.read_text(encoding="utf-8").splitlines() if not ln.startswith("\\"))
    conn.execute(schema)
    conn.execute("set search_path = public")
    conn.execute(MIGRATION.read_text(encoding="utf-8"))
    for t in REPLAY_TABLES:
        p = BACKUP / f"{t}.csv"
        if not p.is_file():
            continue
        with p.open(encoding="utf-8", newline="") as fh:
            rd = csv.reader(fh)
            header = next(rd)
            cols = ", ".join(f'"{c}"' for c in header)
            with conn.cursor() as cur:
                with cur.copy(f'copy "{t}" ({cols}) from stdin') as cp:
                    for rec in rd:
                        vals = [None if v == "" else v for v in rec]
                        for i, c in enumerate(header):
                            if c in MASK_COLS.get(t, ()):
                                vals[i] = _mask(vals[i])
                            elif c in BLANK_COLS.get(t, ()):
                                vals[i] = None
                        cp.write_row(vals)
        conn.execute(f"select setval(pg_get_serial_sequence('{t}', 'id'), coalesce(max(id), 1)) from \"{t}\"")
    return conn


def _stats(conn) -> dict:
    """Counts and money sums of the Focus tables, the shape PROD_AFTER_240926 uses."""
    q = lambda s: conn.execute(s).fetchone()  # noqa: E731
    out: dict = {}
    r = q("select count(*), round(coalesce(sum(gross_bhd),0)::numeric,3)::text from orders")
    out["orders"] = {"rows": r[0], "gross_bhd": r[1]}
    r = q("select count(*), round(coalesce(sum(gross_bhd),0)::numeric,3)::text, round(coalesce(sum(taxable_bhd),0)::numeric,3)::text from order_lines")
    out["order_lines"] = {"rows": r[0], "gross_bhd": r[1], "taxable_bhd": r[2]}
    r = q("select count(*), round(coalesce(sum(received_qty),0)::numeric,3)::text, round(coalesce(sum(issued_qty),0)::numeric,3)::text from stock_movements")
    out["stock_movements"] = {"rows": r[0], "received_qty": r[1], "issued_qty": r[2]}
    r = q("select count(*), count(distinct account), round(coalesce(sum(debit_bhd),0)::numeric,3)::text from ledger_entries")
    out["ledger_entries"] = {"rows": r[0], "accounts": r[1], "debit_bhd": r[2]}
    r = q("select max(as_of_date)::text, count(*) filter (where as_of_date = (select max(as_of_date) from stock_balance)), "
          "round(coalesce(sum(net_qty) filter (where as_of_date = (select max(as_of_date) from stock_balance)),0)::numeric,3)::text, "
          "round(coalesce(sum(total_value_bhd) filter (where as_of_date = (select max(as_of_date) from stock_balance)),0)::numeric,3)::text from stock_balance")
    out["stock_balance"] = {"as_of": r[0], "rows": r[1], "net_qty": r[2], "total_value_bhd": r[3]}
    r = q("select max(as_of_date)::text, count(*) filter (where as_of_date = (select max(as_of_date) from ar_ageing)), "
          "round(coalesce(sum(balance_bhd) filter (where as_of_date = (select max(as_of_date) from ar_ageing)),0)::numeric,3)::text from ar_ageing")
    out["ar_ageing"] = {"as_of": r[0], "rows": r[1], "balance_bhd": r[2]}
    r = q("select max(report_date)::text, count(*) filter (where report_date = (select max(report_date) from product_profitability)), "
          "round(coalesce(sum(gross_bhd) filter (where report_date = (select max(report_date) from product_profitability)),0)::numeric,3)::text from product_profitability")
    out["product_profitability"] = {"as_of": r[0], "rows": r[1], "gross_bhd": r[2]}
    out["per_day"] = {str(d): (n, g) for d, n, g in conn.execute(
        "select line_date::text, count(*), round(sum(gross_bhd)::numeric,3)::text from order_lines group by 1").fetchall()}
    return out


def _prod_url() -> str | None:
    """The real DATABASE_URL, from the .env FILE (never the environment this module pinned)."""
    try:
        from dotenv import dotenv_values
        for p in (MAIN / ".env", ROOT / ".env"):
            if p.is_file():
                u = dotenv_values(p).get("DATABASE_URL")
                if u:
                    return u
    except Exception:  # noqa: BLE001
        pass
    return _SHELL_DATABASE_URL


def _prod_stats() -> dict | None:
    url = _prod_url()
    if not url:
        return None
    try:
        import psycopg
        conn = psycopg.connect(url, connect_timeout=30)
        conn.read_only = True
        try:
            return _stats(conn)
        finally:
            conn.rollback()
            conn.close()
    except Exception as e:  # noqa: BLE001
        print(f"    (production read-only stats unavailable: {str(e)[:80]}; using the recorded figures)")
        return None


def _checksums(conn) -> dict[str, str]:
    return {t: conn.execute(f"select md5(coalesce(string_agg(to_jsonb(x)::text, '|' order by id), '')) from \"{t}\" x").fetchone()[0]
            for t in FOCUS_TABLES}


def _local(fn):
    """Run fn(conn) on the shared bootstrapped cluster, SKIP (print) when it is not available."""
    reason = _STATE.get("reason")
    if reason is None and "conn" not in _STATE:
        reason = _local_reason()
        _STATE["reason"] = reason
        if reason is None:
            print("    bootstrapping the local r2_import database from the pre-r0 backup ...")
            _STATE["conn"] = _bootstrap()
    if reason:
        print(f"    SKIP: {reason}")
        return
    fn(_STATE["conn"])


# ── 1. the per-target rules, one definition on each side ─────────────────────

@test("config: TARGETS in app/ingest_batch.py equals _ingest_target_cfg in the migration, key by key")
def _():
    import re
    from app.ingest_batch import TARGETS
    sql = MIGRATION.read_text(encoding="utf-8")
    body = sql.split("create or replace function _ingest_target_cfg", 1)[1].split("$$;", 1)[0]
    found = {}
    for m in re.finditer(r"when '(\w+)' then\s*'(\{.*?\})'::jsonb", body, re.S):
        found[m.group(1)] = json.loads(m.group(2))
    assert set(found) == set(TARGETS), (set(found) ^ set(TARGETS))
    for t, cfg in TARGETS.items():
        for k in ("keys", "scope", "money"):
            assert found[t][k] == cfg[k], (t, k, found[t][k], cfg[k])
        if cfg["scope"] == "span":
            assert found[t]["span_col"] == cfg["span_col"], t
            assert found[t].get("within") == cfg.get("within"), (t, found[t].get("within"), cfg.get("within"))
        else:
            assert found[t]["partition"] == cfg["partition"], t
            assert "within" not in found[t] and "within" not in cfg, t
    assert TARGETS["ledger_entries"]["within"] == ["account"] and TARGETS["stock_movements"]["within"] == ["item_name"]
    assert "within" not in TARGETS["orders"] and "within" not in TARGETS["order_lines"], "invoices: the whole span is the truth"


@test("prepare: ar_ageing folds two accounts under one name (sums), stock_balance fills the warehouse and aggregates")
def _():
    from app.ingest_batch import prepare
    rows, notes = prepare("ar_ageing", [
        {"account": "STAR", "account_code": "52517-42", "group_name": None, "balance_bhd": 10.5, "bucket_0_30": 10.5,
         "total_bhd": 10.5, "as_of_date": "2026-09-24", "source_file": "x", "last_receipt_date": None},
        {"account": "STAR", "account_code": "52517-12", "group_name": "Retail", "balance_bhd": 0.25, "bucket_0_30": None,
         "total_bhd": 0.25, "as_of_date": "2026-09-24", "source_file": "x", "last_receipt_date": "2026-09-01"},
        {"account": None, "balance_bhd": 99.0, "as_of_date": "2026-09-24"},
    ])
    assert notes == {"dropped_null_account": 1, "aggregated_same_name": 1}, notes
    assert len(rows) == 1 and rows[0]["balance_bhd"] == 10.75 and rows[0]["bucket_0_30"] == 10.5
    assert rows[0]["bucket_31_60"] == 0.0, "pandas .sum() of no values is 0, as the old loader wrote it (never NULL)"
    assert rows[0]["account_code"] == "52517-42" and rows[0]["group_name"] == "Retail" and rows[0]["last_receipt_date"] == "2026-09-01"
    rows, notes = prepare("stock_balance", [
        {"item_name": "A", "warehouse_name": None, "net_qty": 2.0, "selling_rate_bhd": 1.5, "total_value_bhd": 3.0, "as_of_date": "2026-09-24", "source_file": "f"},
        {"item_name": "A", "warehouse_name": None, "net_qty": 1.0, "selling_rate_bhd": 2.5, "total_value_bhd": 2.5, "as_of_date": "2026-09-24", "source_file": "f"},
        {"item_name": "B", "warehouse_name": "Accessories Warehouse", "net_qty": 5.0, "selling_rate_bhd": 4.0, "total_value_bhd": 20.0, "as_of_date": "2026-09-24", "source_file": "f"},
    ])
    assert notes["aggregated_same_item"] == 1 and len(rows) == 2
    a = next(r for r in rows if r["item_name"] == "A")
    assert a["warehouse_name"] == "(unassigned)" and a["net_qty"] == 3.0 and a["total_value_bhd"] == 5.5 and a["selling_rate_bhd"] == 2.0
    assert next(r for r in rows if r["item_name"] == "B")["selling_rate_bhd"] == 4.0


@test("prepare: stock ledger drops null items and keeps the FIRST duplicate key; price book keeps the LAST; orders fold")
def _():
    from app.ingest_batch import prepare
    rows, notes = prepare("stock_movements", [
        {"item_name": None, "voucher": "SI : 1", "row_hash": "a", "move_date": "2026-09-01"},
        {"item_name": "X", "voucher": "SI : 1", "row_hash": "h", "move_date": "2026-09-01", "received_qty": 1.0},
        {"item_name": "X", "voucher": "SI : 1", "row_hash": "h", "move_date": "2026-09-01", "received_qty": 2.0},
    ])
    assert notes == {"dropped_null_item": 1, "folded": 1} and len(rows) == 1 and rows[0]["received_qty"] == 1.0
    rows, notes = prepare("selling_prices", [
        {"sku_code": "T02", "price_book": "MA_base", "customer_code": None, "warehouse_name": None, "start_date": "2026-03-26", "rate_bhd": 3.55},
        {"sku_code": "T02", "price_book": "MA_base", "customer_code": None, "warehouse_name": None, "start_date": "2026-03-26", "rate_bhd": 2.95},
    ])
    assert notes == {"folded": 1} and rows[0]["rate_bhd"] == 2.95
    rows, notes = prepare("orders", [{"invoice_no": "SI : 1", "gross_bhd": 1.0}, {"invoice_no": None}, {"invoice_no": "SI : 1", "gross_bhd": 2.0}])
    assert len(rows) == 1 and rows[0]["gross_bhd"] == 2.0 and notes == {"folded": 1}


@test("money: file_totals is exact Decimal, 3 dp ROUND_HALF_UP, undated rows sit outside a span; scope_of per kind")
def _():
    from app.ingest_batch import dec, file_totals, money, scope_of
    rows = [{"invoice_no": "a", "order_date": "2026-09-01", "gross_bhd": 0.1}, {"invoice_no": "b", "order_date": "2026-09-03", "gross_bhd": 0.2},
            {"invoice_no": "c", "order_date": None, "gross_bhd": 100.0}, {"invoice_no": "d", "order_date": "2026-09-02", "gross_bhd": 0.0005}]
    t = file_totals("orders", rows)
    assert t == {"rows": 3, "sums": {"gross_bhd": "0.301"}}, t          # float 0.1+0.2 would be 0.30000000000000004
    assert money(Decimal("1.0005")) == "1.001" and money(Decimal("-1.0005")) == "-1.001" and money(None) == "0.000"
    assert dec(float("nan")) is None and dec("2.0") == Decimal("2.0") and dec(15.6) == Decimal("15.6") and dec(True) is None
    assert scope_of("orders", rows) == {"kind": "span", "col": "order_date", "from": "2026-09-01", "to": "2026-09-03", "within": {}}
    led = [{"account": "A", "entry_date": "2026-09-01"}, {"account": "B", "entry_date": "2026-09-02"}, {"account": "A", "entry_date": "2026-09-03"}]
    assert scope_of("ledger_entries", led) == {"kind": "span", "col": "entry_date", "from": "2026-09-01", "to": "2026-09-03", "within": {"account": 2}}
    sb = [{"item_name": "A", "warehouse_name": "W1", "as_of_date": "2026-09-24"}, {"item_name": "B", "warehouse_name": "W2", "as_of_date": "2026-09-24"}]
    assert scope_of("stock_balance", sb) == {"kind": "partition", "cols": ["as_of_date", "warehouse_name"],
                                             "partitions": [["2026-09-24", "W1"], ["2026-09-24", "W2"]]}
    sp = [{"price_book": "MA_base", "source_file": "MASellingPriceBook (40).xlsx"}]
    assert scope_of("selling_prices", sp) == {"kind": "book", "books": ["MA_base"], "files": ["MASellingPriceBook (40).xlsx"]}
    assert file_totals("stock_balance", [{"net_qty": 1.5, "total_value_bhd": None}, {"net_qty": None, "total_value_bhd": 2.25}]) == \
        {"rows": 2, "sums": {"net_qty": "1.500", "total_value_bhd": "2.250"}}
    from app.ingest_batch import prepare
    only_qty, _ = prepare("stock_balance", [{"item_name": "A", "warehouse_name": "W", "net_qty": 2.0, "selling_rate_bhd": None,
                                             "total_value_bhd": None, "as_of_date": "2026-09-24", "source_file": "f"}])
    assert only_qty[0]["total_value_bhd"] == 0.0 and only_qty[0]["selling_rate_bhd"] is None, only_qty


@test("diff sql: the same predicates the commit builds (NULL-safe hashable key equality, is distinct from on payload), scoped per kind")
def _():
    from app.ingest_batch import PROTECTED, _eq, diff_sql
    assert _eq('L."a"', "(s.row->>'a')::text", "text") == "coalesce(L.\"a\"::text, '') = coalesce((s.row->>'a')::text::text, '')"
    assert _eq("L.d", "x", "date") == "coalesce(L.d, '0001-01-01'::date) = coalesce(x, '0001-01-01'::date)"
    assert _eq("L.n", "x", "integer") == "coalesce(L.n, -9223372036854775807) = coalesce(x, -9223372036854775807)"
    assert "is not distinct from" not in diff_sql("orders", {"invoice_no": "text", "order_date": "date", "gross_bhd": "numeric", "id": "bigint"},
                                                  ["invoice_no", "order_date", "gross_bhd"], {"kind": "span", "col": "order_date", "from": "a", "to": "b"})[0], \
        "IS NOT DISTINCT FROM cannot be hash-joined (a 36k x 36k nested loop, measured in minutes)"
    types = {"id": "bigint", "invoice_no": "text", "line_no": "integer", "line_date": "date", "gross_bhd": "numeric",
             "imported_at": "timestamp with time zone", "product_id": "bigint", "customer_account": "text"}
    payload = [c for c in ("invoice_no", "line_no", "line_date", "customer_account", "gross_bhd") if c not in PROTECTED]
    sql, params = diff_sql("order_lines", types, payload, {"kind": "span", "col": "line_date", "from": "2025-09-21", "to": "2026-09-24"})
    assert params == ["2025-09-21", "2026-09-24"]
    assert "coalesce(L.\"invoice_no\"::text, '') = coalesce((s.row->>'invoice_no')::text::text, '') and coalesce(L.\"line_no\", -9223372036854775807) = coalesce((s.row->>'line_no')::integer, -9223372036854775807)" in sql
    assert "L.\"gross_bhd\" is distinct from (s.row->>'gross_bhd')::numeric" in sql
    assert 'L."line_date" between $3::date and $4::date' in sql and "batch_id = $1::bigint and target = $2" in sql
    assert "product_id" not in sql and "imported_at" not in sql, "protected columns never take part"
    assert max(int(m) for m in __import__("re").findall(r"\$(\d+)", sql)) <= 8, "the read-only RPC binds $1..$8"
    assert " in (select distinct" not in sql, "invoices have no within-set: the whole span is the file's truth"
    # a span with a within-set: the Ledger is the truth for its span only for the accounts in the file
    sql, params = diff_sql("ledger_entries", {"id": "bigint", "account": "text", "voucher": "text", "row_hash": "text", "entry_date": "date",
                                              "debit_bhd": "numeric"}, ["account", "voucher", "row_hash", "entry_date", "debit_bhd"],
                           {"kind": "span", "col": "entry_date", "from": "2026-01-01", "to": "2026-09-24", "within": {"account": 1}})
    assert 'L."entry_date" between $3::date and $4::date and L."account" in (select distinct sw.row->>\'account\' from s sw)' in sql
    sql, _ = diff_sql("stock_movements", {"id": "bigint", "voucher": "text", "item_name": "text", "row_hash": "text", "move_date": "date"},
                      ["voucher", "item_name", "row_hash", "move_date"], {"kind": "span", "col": "move_date", "from": "a", "to": "b"})
    assert 'and L."item_name" in (select distinct sw.row->>\'item_name\' from s sw)' in sql
    sql, params = diff_sql("stock_balance", {"item_name": "text", "warehouse_name": "text", "as_of_date": "date", "net_qty": "numeric", "id": "bigint"},
                           ["item_name", "warehouse_name", "as_of_date", "net_qty"], {"kind": "partition"})
    assert params == [] and ("exists (select 1 from s sp where coalesce(L.\"as_of_date\", '0001-01-01'::date) = coalesce((sp.row->>'as_of_date')::date, '0001-01-01'::date) "
                             "and coalesce(L.\"warehouse_name\"::text, '') = coalesce((sp.row->>'warehouse_name')::text::text, ''))") in sql
    types = {"id": "bigint", "sku_code": "text", "price_book": "text", "customer_code": "text", "warehouse_name": "text", "start_date": "date",
             "rate_bhd": "numeric", "price_tiers": "jsonb", "source_file": "text", "voided_at": "timestamp with time zone"}
    sql, params = diff_sql("selling_prices", types, ["sku_code", "price_book", "customer_code", "warehouse_name", "start_date", "rate_bhd", "price_tiers", "source_file"],
                           {"kind": "book", "books": ["MA_base"], "files": ["MASellingPriceBook (40).xlsx"]})
    assert params == ['{"MA_base"}', '{"MASellingPriceBook (40).xlsx"}']
    assert "L.voided_at is null and L.source_file <> all($4::text[])" in sql and "masellingpricebook%" in sql
    assert "L.\"price_tiers\" is distinct from (s.row->'price_tiers')" in sql, "jsonb compares as jsonb, no cast"
    assert "L.voided_at is not null as was_voided" in sql


@test("expected: built from the preview's file totals + actions; refuses a target without a diff")
def _():
    from app.ingest_batch import expected_from_summary
    s = {"targets": {"orders": {"file_rows": 3, "sums": {"gross_bhd": "1.000"}, "actions": {"inserted": 1, "updated": 0, "unchanged": 2, "deleted": 0}}}}
    e = expected_from_summary(s, "a@b")
    assert e == {"actor": "a@b", "targets": {"orders": {"rows": 3, "sums": {"gross_bhd": "1.000"},
                                                        "actions": {"inserted": 1, "updated": 0, "unchanged": 2, "deleted": 0}}}}
    try:
        expected_from_summary({"targets": {"orders": {"file_rows": 3, "sums": {}}}}, None)
        raise AssertionError("must refuse")
    except ValueError as err:
        assert "no database diff" in str(err)


# ── 2. the preview and the commit gate (no database) ─────────────────────────

class _FakeBackend:
    """Just enough of Backend for the gate tests: batches in memory, no SQL."""
    mode, read_only, stage_source = "fake", False, "table"

    def __init__(self, batches=None, ready=True, fail_sql=None):
        self.batches, self.ready, self.calls, self.closed = dict(batches or {}), ready, [], 0
        self.fail_sql = fail_sql          # a substring: any query containing it raises

    def tables_ready(self):
        return self.ready

    def can_stage(self):
        return self.ready

    def get_batch(self, batch_id):
        return self.batches.get(batch_id)

    def list_batches(self, limit=20):
        return list(self.batches.values())[:limit]

    def update_batch(self, batch_id, **fields):
        self.batches[batch_id].update(fields)

    def merge_summary(self, batch_id, patch, only_status=None):
        b = self.batches.get(batch_id)
        if not b or (only_status and b.get("status") != only_status):
            return False
        b["summary"] = (b.get("summary") or {}) | patch
        return True

    def reject(self, batch_id, patch):
        b = self.batches.get(batch_id)
        if not b or b.get("status") != "previewed" or self.batches.get(("race", batch_id)):
            return False
        b["status"] = "rejected"
        b["summary"] = (b.get("summary") or {}) | patch
        return True

    def prune(self):
        return {"superseded": 0, "stage_rows_deleted": 0}

    def storage(self):
        return {"stage_bytes": 0, "replaced_bytes": 0, "stage_rows": 0, "committed_batches": 0}

    def close(self):
        self.closed += 1

    def rpc(self, name, args):
        self.calls.append((name, args))
        return {"batch_id": args["p_batch_id"], "status": "committed" if name == "ingest_commit" else "undone", "targets": {}}

    def query(self, sql, params=None):
        self.calls.append(("query", sql[:60]))
        if self.fail_sql and self.fail_sql in sql:
            raise RuntimeError(f"simulated failure on {self.fail_sql}")
        if "information_schema.columns" in sql:
            return []
        return []


@test("gate: commit refuses a non-previewed batch, an un-diffed preview and unacknowledged blocking codes; then calls the RPC")
def _():
    from app.ingest_batch import CommitRefused, commit_batch
    good = {"id": 7, "status": "previewed",
            "summary": {"commit_available": True, "targets": {"orders": {"file_rows": 1, "sums": {"gross_bhd": "2.000"},
                                                                          "actions": {"inserted": 1, "updated": 0, "unchanged": 0, "deleted": 0}}}},
            "exceptions": [{"code": "rows_removed", "severity": "blocking"}, {"code": "partial_day", "severity": "info"}]}
    fb = _FakeBackend({7: good, 8: dict(good, id=8, status="committed"), 9: dict(good, id=9, summary={"commit_available": False})})
    for bid, msg in ((8, "only a previewed"), (9, "cannot be committed"), (7, "acknowledge the blocking")):
        try:
            commit_batch(bid, fb, "a@b", acknowledged=[])
            raise AssertionError(f"batch {bid} must be refused")
        except CommitRefused as e:
            assert msg in str(e), (bid, str(e))
    assert fb.calls == []
    out = commit_batch(7, fb, "a@b", acknowledged=["rows_removed", "something_else"])
    assert out["status"] == "committed"
    name, args = fb.calls[-1]
    assert name == "ingest_commit" and args["p_batch_id"] == 7
    assert args["p_expected"]["actor"] == "a@b" and args["p_expected"]["acknowledged"] == ["rows_removed", "something_else"]
    assert args["p_expected"]["targets"]["orders"]["rows"] == 1 and args["p_expected"]["targets"]["orders"]["actions"]["inserted"] == 1


@test("gate: HARD_BLOCKING codes (join < 80 %, a guard that could not run, a failed diff) cannot be acknowledged away")
def _():
    from app.ingest_batch import HARD_BLOCKING, CommitRefused, commit_batch
    assert {"sales_join_below_80", "check_failed", "db_diff_failed", "empty_report", "snapshot_without_date"} <= HARD_BLOCKING
    for code in sorted(HARD_BLOCKING):
        # even a summary that (wrongly) says commit_available is refused when a hard code is on the list
        b = {"id": 1, "status": "previewed",
             "summary": {"commit_available": True, "targets": {"orders": {"file_rows": 1, "sums": {"gross_bhd": "2.000"},
                                                                           "actions": {"inserted": 1, "updated": 0, "unchanged": 0, "deleted": 0}}}},
             "exceptions": [{"code": code, "severity": "blocking"}]}
        fb = _FakeBackend({1: b})
        try:
            commit_batch(1, fb, "a@b", acknowledged=[code, "rows_removed"])
            raise AssertionError(f"{code} must not be acknowledgeable")
        except CommitRefused as e:
            assert "no acknowledgement changes that" in str(e) and code in str(e), (code, str(e))
        assert fb.calls == []


@test("preview guards fail CLOSED: a guard whose query throws adds a hard-blocking check_failed instead of vanishing")
def _():
    from app.ingest_batch import BLOCKING, Parsed, build_preview, prepare
    ol, _ = prepare("order_lines", [{"invoice_no": "SI : 1", "line_no": 1, "line_date": "2026-09-24", "gross_bhd": 10.0, "taxable_bhd": 9.0,
                                     "warehouse_name": "Rep A"}])
    od, _ = prepare("orders", [{"invoice_no": "SI : 1", "order_date": "2026-09-24", "gross_bhd": 10.0}])
    sb, _ = prepare("stock_balance", [{"item_name": "A", "warehouse_name": "W", "net_qty": 1.0, "selling_rate_bhd": 1.0, "total_value_bhd": 1.0,
                                       "as_of_date": "2026-09-24", "source_file": "f"}])
    sp, _ = prepare("selling_prices", [{"sku_code": "T02", "price_book": "MA_base", "customer_code": None, "warehouse_name": None,
                                        "start_date": "2026-03-26", "rate_bhd": 3.55, "source_file": "MASellingPriceBook (1).xlsx"}])
    for fail, target, check in (("from order_lines", "order_lines", "days_shrink"), ("from stock_balance", "stock_balance", "warehouse_set_shrinks"),
                                ("v_price_list_by_book", "selling_prices", "snapshot_void_guard")):
        p = Parsed(folder="x")
        p.rows = {"orders": od, "order_lines": ol, "stock_balance": sb, "selling_prices": sp}
        p.as_on = {"stock_balance": "2026-09-24"}
        fb = _FakeBackend(ready=False, fail_sql=fail)      # ready=False: no per-row diff, the guards still run
        s, ex = build_preview(p, fb, 0, today="2026-09-24")
        cf = [e for e in ex if e["code"] == "check_failed"]
        assert cf and cf[0]["severity"] == BLOCKING and cf[0]["target"] == target and cf[0]["check"] == check, (fail, cf)
        assert "check_failed" in s["hard_blocking_codes"] and s["commit_available"] is False
    # the void-share half of the mass-void guard needs no second query: it comes from the diff's own counts
    src = (ROOT / "app" / "ingest_batch.py").read_text(encoding="utf-8")
    assert "removed > SNAPSHOT_MAX_STALE_SHARE * db_rows" in src


@test("removals: a span replace that removes more than 5 % of the rows in scope, or more than it inserts, is BLOCKING")
def _():
    src = (ROOT / "app" / "ingest_batch.py").read_text(encoding="utf-8")
    assert "REMOVED_MAX_SHARE = 0.05" in src and "removed > REMOVED_MAX_SHARE * db_rows or removed > inserted" in src
    assert 'if target in ("orders", "order_lines"):' in src, "invoices removed are always blocking"


@test("parse_folder lists a .csv (and any non-Excel file) under ignored instead of silently dropping it")
def _():
    from app.ingest_batch import parse_folder
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "Sales_day_book1.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        (Path(d) / "notes.txt").write_text("x", encoding="utf-8")
        out = parse_folder(d)
        names = {i["file"]: i["reason"] for i in out.ignored}
        assert "Sales_day_book1.csv" in names and "not an Excel export" in names["Sales_day_book1.csv"], names
        assert "notes.txt" in names and out.rows == {} and out.files == []


@test("DirectBackend.open() without DATABASE_URL raises DirectDbUnavailable (the routes say so; nothing else breaks)")
def _():
    from app.ingest_batch import DEFAULT_STATEMENT_TIMEOUT_S, DirectBackend, DirectDbUnavailable
    saved = os.environ.pop("DATABASE_URL", None)
    try:
        try:
            DirectBackend.open()
            raise AssertionError("must raise")
        except DirectDbUnavailable as e:
            assert "DATABASE_URL" in str(e)
    finally:
        if saved is not None:
            os.environ["DATABASE_URL"] = saved
    assert DEFAULT_STATEMENT_TIMEOUT_S == 600
    src = (ROOT / "app" / "ingest_batch.py").read_text(encoding="utf-8")
    assert "set statement_timeout = '{int(statement_timeout_s)}s'" in src and "PostgREST" in src
    api_src = (ROOT / "app" / "ingest_batch_api.py").read_text(encoding="utf-8")
    assert "exec_sql" not in api_src and ".rpc(" not in api_src and "get_client().rpc" not in src, "the batch path never goes through PostgREST RPCs"


@test("preview without a database: file totals, the join gate, the AR grand-total gap, the partial day; commit unavailable")
def _():
    from app.ingest_batch import BLOCKING, Parsed, build_preview, prepare
    p = Parsed(folder="x")
    od, _ = prepare("orders", [{"invoice_no": "SI : 1", "order_date": "2026-09-24", "gross_bhd": 10.0},
                               {"invoice_no": "SI : 2", "order_date": "2026-09-24", "gross_bhd": 5.5}])
    ol, _ = prepare("order_lines", [{"invoice_no": "SI : 1", "line_no": 1, "line_date": "2026-09-24", "gross_bhd": 10.0, "taxable_bhd": 9.0, "warehouse_name": "Rep A"},
                                    {"invoice_no": "SI : 9", "line_no": 1, "line_date": "2026-09-24", "gross_bhd": 5.5, "taxable_bhd": 5.0, "warehouse_name": "Rep B"},
                                    {"invoice_no": "SI : 8", "line_no": 1, "line_date": "2026-09-24", "gross_bhd": 5.5, "taxable_bhd": 5.0, "warehouse_name": "Rep B"}])
    ar, _ = prepare("ar_ageing", [{"account": "A", "balance_bhd": 40.0, "as_of_date": "2026-09-24"}, {"account": "B", "balance_bhd": 2.5, "as_of_date": "2026-09-24"}])
    p.rows = {"orders": od, "order_lines": ol, "ar_ageing": ar}
    p.as_on = {"ar_ageing": "2026-09-24"}
    p.ar_grand_total = Decimal("40.00")
    p.files = [{"file": "f", "target": "orders", "report": "Summary_sales_register", "rows": 2}]
    s, ex = build_preview(p, None, 0, today="2026-09-24")
    codes = {e["code"]: e for e in ex}
    assert s["targets"]["orders"]["file_rows"] == 2 and s["targets"]["orders"]["sums"] == {"gross_bhd": "15.500"}
    assert s["targets"]["order_lines"]["sums"] == {"gross_bhd": "21.000", "taxable_bhd": "19.000"}
    assert s["join"] == {"overlap": 1, "denom": 3, "pct": round(1 / 3, 4)}
    assert codes["sales_join_below_80"]["severity"] == BLOCKING and set(codes["sales_join_below_80"]["items"]) == {"SI : 2", "SI : 8", "SI : 9"}
    assert codes["ar_total_gap"]["severity"] == "warning" and s["receivables"] == {"rows_sum": "42.500", "focus_grand_total": "40.000", "gap": "2.500"}
    assert "partial_day" in codes and "db_comparison_unavailable" in codes
    assert s["commit_available"] is False and s["migration_applied"] is False and s["blocking_codes"] == ["sales_join_below_80"]
    assert s["hard_blocking_codes"] == ["sales_join_below_80"], "data rule 3: a join under 80 % is a HARD FAIL, never acknowledgeable"
    assert s["targets"]["orders"].get("actions") is None, "no database, no per-row diff"


@test("preview files: summary.md and exceptions.csv (+ summary.json) land in the folder given")
def _():
    from app.ingest_batch import render_summary_md, write_preview_files
    summary = {"folder": "240926", "generated_at": "t", "db_mode": "none", "migration_applied": False, "files": [{"file": "a.xlsx", "target": "orders", "rows": 2}],
               "ignored": [], "targets": {"orders": {"file_rows": 2, "sums": {"gross_bhd": "15.500"}, "scope": {"kind": "span", "col": "order_date", "from": "a", "to": "b"},
                                                     "actions": {"inserted": 1, "updated": 0, "unchanged": 1, "deleted": 0}, "db_rows": 1, "db_sums": {"gross_bhd": "10.000"}}},
               "blocking_codes": [], "commit_available": False}
    ex = [{"code": "partial_day", "severity": "info", "target": "order_lines", "message": "m", "count": None, "items": []}]
    md = render_summary_md(summary, ex)
    assert "| orders | 2 | 1 | 0 | 1 | 0 | gross_bhd=15.500 | 1 | gross_bhd=10.000 |" in md and "[info] partial_day (order_lines): m" in md
    with tempfile.TemporaryDirectory() as d:
        out = write_preview_files(summary, ex, Path(d) / "p")
        assert (out / "summary.md").is_file() and (out / "summary.json").is_file()
        rows = list(csv.reader((out / "exceptions.csv").open(encoding="utf-8")))
        assert rows[0] == ["severity", "code", "target", "count", "message", "items"] and rows[1][:3] == ["info", "partial_day", "order_lines"]


# ── 3. the migration's contract ───────────────────────────────────────────────

@test("migration text: one transaction per commit under an advisory lock, replaced rows saved first, totals asserted, owner-only RPCs, no yq_readonly widening")
def _():
    low = MIGRATION.read_text(encoding="utf-8").lower()
    assert "pg_advisory_xact_lock(hashtext('yq_ingest_commit'))" in low and low.count("pg_advisory_xact_lock") == 2
    assert "create or replace function ingest_commit(p_batch_id bigint, p_expected jsonb)" in low
    assert "create or replace function ingest_undo(p_batch_id bigint, p_actor text default null)" in low
    assert "create or replace function ingest_prune(p_stale interval default interval '2 days')" in low
    assert low.count("security definer") == 4, "only ingest_commit, ingest_undo, ingest_prune and ingest_stage_analyze are security definer"
    assert "%i is not distinct from %s" not in low and "_ingest_eq(format('l.%i', c.col)" in low, \
        "key matches must stay hash-joinable (_ingest_eq), never IS NOT DISTINCT FROM"
    assert "create or replace function _ingest_eq(a text, b text, typ text)" in low
    # grants: nothing to anyone. The API runs the RPCs over DATABASE_URL (PostgREST caps an RPC at 8 s);
    # yq_readonly (the AI's SQL role) keeps its narrow allowlist
    assert "grant " not in low and "grant\t" not in low, "the migration grants nothing"
    assert "grant select" not in low and "create policy" not in low and "to yq_readonly;" not in low
    for fn in ("ingest_commit(bigint, jsonb)", "ingest_undo(bigint, text)", "ingest_prune(interval)", "ingest_stage_analyze()"):
        assert f"revoke execute on function {fn} from public, anon, authenticated, service_role" in low, fn
    assert "revoke all on ingest_batches, ingest_stage, ingest_replaced from anon, authenticated" in low
    assert "enable row level security" in low
    assert "'service_role', 'yq_readonly')) then" in low, "the self-check refuses if any of those roles can execute an ingest RPC"
    assert "yq_readonly must not gain select on the raw focus tables" in low
    # the span scope is narrowed to the accounts / items the file carries
    assert '"within":["account"]' in low and '"within":["item_name"]' in low
    assert "and l.%i in (select distinct sw.row->>%l from ingest_stage sw where sw.batch_id = %s and sw.target = %l)" in low
    # every removal / change is copied into ingest_replaced before the row is touched
    assert "insert into ingest_replaced (batch_id, target, action, row) select %s, %l, 'deleted', row from pre" in low
    assert "insert into ingest_replaced (batch_id, target, action, row) select %s, %l, 'voided', row from pre" in low
    assert "case when was_voided then 'revived' else 'updated' end, row from pre" in low
    # the assertions that make a mismatch roll everything back
    for needle in ("mismatch -- preview said", "rows in scope after load", "after load = %, the file says", "are not in the newest export after the void",
                   "were neither matched nor inserted", "natural key(s); dedupe before staging"):
        assert needle in low, needle
    assert "only a previewed batch can be committed" in low and "only a committed batch can be undone" in low
    # undo proves the rows are still the batch's BEFORE it changes anything
    assert "committed_at > b.committed_at" in low, "overlap is by commit time, not id"
    assert "touched the same % scope after batch" in low, "undo refuses when a later batch overlaps"
    assert "a refresh outside the batch path loaded data after batch" in low and "not like 'batch %'" in low
    assert "no longer read as batch % wrote them" in low and "were removed or un-voided since" in low
    assert "were loaded again since (same key, new row)" in low
    assert low.index("no longer read as batch % wrote them") < low.index("-- 4. reverse, target by target"), "checks come before any change"
    assert "overriding system value" in low, "undo re-inserts deleted rows with their original ids"
    # retention
    assert "delete from ingest_stage where batch_id = p_batch_id and action = 'unchanged'" in low, "commit drops the unchanged stage rows"
    assert "delete from ingest_stage where batch_id = p_batch_id;" in low, "undo drops the batch's stage rows"
    assert "b.status in ('rejected', 'failed', 'undone')" in low and "pg_total_relation_size('public.ingest_stage')" in low
    assert "delete from ingest_replaced" not in low, "ingest_replaced is never pruned"
    assert "protected  text[] := array['id', 'imported_at', 'created_at', 'updated_at', 'product_id', 'order_id'," in low
    assert "truncate" not in low and "drop table" not in low
    rev = REVERSE.read_text(encoding="utf-8").lower()
    assert "committed ingest batches exist; undo them first" in rev and "drop table if exists ingest_replaced" in rev
    assert "drop function if exists ingest_prune(interval)" in rev and "yq_readonly" not in rev.replace("grants nothing to yq_readonly", "")


# ── 4. routes ─────────────────────────────────────────────────────────────────

@test("routes: registered on the app, admin-only, and honest before the migration (no batches, commit unavailable)")
def _():
    from fastapi.testclient import TestClient
    import app.ingest_batch_api as api
    import app.main as m
    from app.auth import CurrentUser, require_admin
    paths = {r.path for r in m.app.routes}
    for p in ("/ingest/preview", "/ingest/batches", "/ingest/batches/{batch_id}", "/ingest/batches/{batch_id}/commit",
              "/ingest/batches/{batch_id}/undo", "/ingest/batches/{batch_id}/reject", "/ingest"):
        assert p in paths, p
    import app.ai as ai
    import app.audit as aud
    import app.catalog as cat
    import app.database as db
    import app.notify as ntf
    from app.ingest_batch import DirectDbUnavailable
    c = TestClient(m.app)
    assert c.get("/ingest/batches").status_code == 401
    assert c.post("/ingest/batches/1/commit", json={"acknowledged": []}).status_code == 401
    saved = api.backend_factory
    saved_client, saved_flush, saved_log, saved_sync, saved_notify = db.get_client, ai.flush_cache, aud.log_event, cat.sync_from_price_book, ntf.notify
    seen: dict = {"flush": 0, "log": [], "sync": 0, "notify": []}
    ntf.notify = lambda subject, body, html=None: seen["notify"].append(subject) or {}

    def _no_db():
        # belt and braces: this module pins SUPABASE_URL to ci.invalid before any app import, AND
        # every side effect the routes reach (audit rows, the answer-cache flush, the catalog
        # sync) is patched at its module attribute -- app/ingest_batch_api.py looks them up at
        # call time, so no earlier import of app.ai can bind a real flush_cache into the routes
        raise RuntimeError("no database in this test")
    db.get_client = _no_db
    ai.flush_cache = lambda: seen.__setitem__("flush", seen["flush"] + 1)
    aud.log_event = lambda email, event, **kw: seen["log"].append(event)
    cat.sync_from_price_book = lambda: seen.__setitem__("sync", seen["sync"] + 1) or 0
    m.app.dependency_overrides[require_admin] = lambda: CurrentUser(user_id="u", email="admin@example.com", role="admin")
    try:
        fb = _FakeBackend(ready=False)
        api.backend_factory = lambda: fb
        r = c.get("/ingest/batches")
        assert r.status_code == 200 and r.json() == {"batches": [], "migration_applied": False} and fb.closed == 1
        assert c.get("/ingest/batches/1").status_code == 404 and fb.closed == 2, "every route closes the session it opened"
        fb2 = _FakeBackend({3: {"id": 3, "status": "previewed", "summary": {"commit_available": False}, "exceptions": []}})
        api.backend_factory = lambda: fb2
        r = c.post("/ingest/batches/3/commit", json={"acknowledged": []})
        assert r.status_code == 200 and r.json()["ok"] is False and "cannot be committed" in r.json()["error"] and fb2.calls == []
        r = c.post("/ingest/batches/3/reject", json={"reason": "wrong day"})
        assert r.status_code == 200 and fb2.batches[3]["status"] == "rejected" and fb2.batches[3]["summary"]["reject_reason"] == "wrong day"
        assert seen["log"][-1] == "ingest.reject"
        # the race: the batch read as 'previewed', but a commit lands before the reject's UPDATE —
        # the conditional update matches nothing, the answer is 409 and nothing is overwritten
        fb2.batches[4] = {"id": 4, "status": "previewed", "summary": {"commit": {"ok": True}}, "exceptions": []}
        fb2.batches[("race", 4)] = True
        logs_before = len(seen["log"])
        r = c.post("/ingest/batches/4/reject", json={"reason": "clicked Discard after a lost response"})
        assert r.status_code == 409, (r.status_code, r.text)
        assert fb2.batches[4]["summary"] == {"commit": {"ok": True}} and len(seen["log"]) == logs_before
        del fb2.batches[("race", 4)], fb2.batches[4]
        r = c.post("/ingest/batches/3/undo", json={})
        assert r.status_code == 200 and r.json()["ok"] is True and fb2.calls[-1][0] == "ingest_undo"
        assert seen["flush"] == 1 and seen["log"][-1] == "ingest.undo" and seen["sync"] == 0, seen
        # an undo whose result names selling_prices re-syncs the marketplace catalog (commit does too)
        fb3 = _FakeBackend({4: {"id": 4, "status": "committed", "summary": {}, "exceptions": []}})
        fb3.rpc = lambda name, args: {"batch_id": 4, "status": "undone", "targets": {"selling_prices": {"removed_inserts": 1}}}
        api.backend_factory = lambda: fb3
        r = c.post("/ingest/batches/4/undo", json={"reason": "wrong book"})
        assert r.status_code == 200 and r.json()["ok"] is True and seen["sync"] == 1 and r.json()["after"]["catalog_synced"] == 0
        # a commit whose RPC raises: reported as 'did not complete', the error merged server-side
        # only while the batch is still previewed, and never as 'rolled back' on faith
        def good(i):
            return {"id": i, "status": "previewed",
                    "summary": {"commit_available": True, "targets": {"orders": {"file_rows": 1, "sums": {"gross_bhd": "2.000"},
                                                                                  "actions": {"inserted": 1, "updated": 0, "unchanged": 0, "deleted": 0}}}},
                    "exceptions": []}
        fb4 = _FakeBackend({5: good(5)})

        def _boom(name, args):
            raise RuntimeError("canceling statement due to statement timeout")
        fb4.rpc = _boom
        api.backend_factory = lambda: fb4
        r = c.post("/ingest/batches/5/commit", json={"acknowledged": []})
        assert r.status_code == 200 and r.json()["ok"] is False
        assert r.json()["error"].startswith("Commit did not complete: canceling statement") and "rolled back" not in r.json()["error"][:30]
        assert fb4.batches[5]["summary"]["last_commit_error"].startswith("canceling statement") and seen["log"][-1] == "ingest.commit_failed"
        # ... and when the batch turns out committed (the response was lost), it is reported as such
        fb5 = _FakeBackend({6: good(6)})

        def _lost(name, args):
            fb5.batches[6]["status"] = "committed"
            fb5.batches[6]["summary"] = fb5.batches[6]["summary"] | {"commit": {"orders": {"actions": {"inserted": 1}}}}
            raise RuntimeError("connection closed")
        fb5.rpc = _lost
        api.backend_factory = lambda: fb5
        r = c.post("/ingest/batches/6/commit", json={"acknowledged": []})
        assert r.status_code == 200 and r.json()["ok"] is True and "response was lost" in r.json()["result"]["note"], r.json()
        assert "last_commit_error" not in fb5.batches[6]["summary"]
        assert seen["notify"] == ["YQ - batch 6 committed"] and r.json()["after"]["not_run"] == ["verify_numbers", "category_backfill"]
        # no DATABASE_URL on the API: the routes say so; nothing raises through
        def _unavailable():
            raise DirectDbUnavailable("DATABASE_URL is not set on this API")
        api.backend_factory = _unavailable
        r = c.get("/ingest/batches")
        assert r.status_code == 200 and r.json()["migration_applied"] is False and "DATABASE_URL" in r.json()["error"]
        r = c.post("/ingest/batches/5/commit", json={"acknowledged": []})
        assert r.status_code == 200 and r.json()["ok"] is False and r.json()["error"].startswith("Batch importer unavailable")
        assert c.get("/ingest/batches/5").status_code == 503
    finally:
        api.backend_factory = saved
        db.get_client, ai.flush_cache, aud.log_event, cat.sync_from_price_book, ntf.notify = saved_client, saved_flush, saved_log, saved_sync, saved_notify
        m.app.dependency_overrides.pop(require_admin, None)


# ── 5. the replay on the local scratch Postgres ───────────────────────────────

def _api_backend():
    """The API's own backend class over the scratch cluster, under a FIFTH of the API's default
    statement budget: what the routes will run on production, timeout included."""
    from app.ingest_batch import DirectBackend
    be = DirectBackend.open(PG_DSN, statement_timeout_s=REPLAY_TIMEOUT_S)
    assert be.statement_timeout_s == REPLAY_TIMEOUT_S and be.mode == "direct" and not be.read_only
    return be


def _run_preview(conn, folder, today="2026-09-24"):
    from app.ingest_batch import run_preview
    be = _api_backend()
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory() as d:
        res = run_preview(folder, be, actor="replay@test", persist=True, out_dir=Path(d) / "p", today=today, row_hook=mask_row)
    res["seconds"] = round(time.perf_counter() - t0, 1)
    return be, res


def _synthetic_batch(be, target, rows):
    """Stage `rows` as a batch of one target, preview its diff and leave it 'previewed' with the
    totals a commit will assert -- the same contract run_preview() writes, without a file."""
    from app.ingest_batch import _target_diff, file_totals, scope_of
    bid = be.new_batch([{"file": "synthetic", "target": target, "rows": len(rows)}], "replay@test")
    be.stage(bid, target, rows)
    be.after_stage(bid)
    d = _target_diff(be, bid, target, rows, scope_of(target, rows))
    tot = file_totals(target, rows)
    be.update_batch(bid, status="previewed", exceptions=[], summary={
        "commit_available": True, "targets": {target: {"file_rows": tot["rows"], "sums": tot["sums"], "actions": d["actions"]}}})
    return bid, d


@test("replay 240926 on local Postgres (the API's DirectBackend, 120 s budget): preview == the plan's numbers, commit asserts, result == production's tables")
def _():
    def go(conn):
        from app.ingest_batch import commit_batch
        before = _stats(conn)
        assert before["orders"]["rows"] == 2712 and before["order_lines"]["rows"] == 11804 and before["stock_movements"]["rows"] == 35871, before
        assert before["ledger_entries"]["rows"] == 51261 and before["ledger_entries"]["accounts"] > 250, before["ledger_entries"]
        _STATE["checksums_before"] = _checksums(conn)
        be, res = _run_preview(conn, DROP_240926)
        s, ex = res["summary"], res["exceptions"]
        bid = res["batch_id"]
        assert bid and s["migration_applied"] and s["db_mode"] == "direct"
        assert res["seconds"] < REPLAY_TIMEOUT_S, res["seconds"]
        assert s["storage"]["stage_rows"] >= 51343 and s["pruned"]["stage_rows_deleted"] == 0, (s["storage"], s["pruned"])
        t = s["targets"]
        # the plan's section-2 preview, exactly
        assert t["orders"]["actions"] == {"inserted": 31, "updated": 1, "unchanged": 2711, "deleted": 0}, t["orders"]["actions"]
        assert t["orders"]["file_rows"] == 2743 and t["orders"]["sums"] == {"gross_bhd": "74238.130"} and t["orders"]["db_sums"] == {"gross_bhd": "72744.930"}
        assert t["order_lines"]["actions"] == {"inserted": 152, "updated": 1, "unchanged": 11803, "deleted": 0}, t["order_lines"]["actions"]
        assert t["orders"]["updated_sample"] == [{"invoice_no": "SI : SI-YQ-26-09-6", "order_date": "2026-09-03"}], "the one re-pointed invoice"
        assert t["order_lines"]["sums"] == {"gross_bhd": "74238.130", "taxable_bhd": "67490.060"}
        # Focus re-costed 148 ledger rows (new row_hash): out with the old, in with the new, net +396 rows, nothing updated in place
        assert t["stock_movements"]["actions"] == {"inserted": 544, "updated": 0, "unchanged": 35723, "deleted": 148}, t["stock_movements"]["actions"]
        assert t["stock_movements"]["scope"]["within"] == {"item_name": 175}, "the ledger's scope is its span x the items it carries"
        assert t["stock_balance"]["actions"] == {"inserted": 132, "updated": 0, "unchanged": 0, "deleted": 0}
        assert t["ar_ageing"]["actions"] == {"inserted": 84, "updated": 0, "unchanged": 0, "deleted": 0}
        assert t["product_profitability"]["actions"] == {"inserted": 161, "updated": 0, "unchanged": 0, "deleted": 0}
        assert [d["key"] for d in s["sales"]["per_day_changed"]] == ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"], s["sales"]["per_day_changed"]
        assert s["stock"]["missing_warehouses"] == [] and s["stock"]["db_latest_as_of"] == "2026-09-21"
        codes = {e["code"] for e in ex}
        assert "partial_day" in codes and "ar_total_gap" in codes and "mrn_without_cost" in codes
        assert "YQ-26-09-3" in s["mrn"]["without_cost"] and "YQ-26-09-2" in s["mrn"]["with_cost"]
        assert s["commit_available"] is True and s["blocking_codes"] == [] and s["hard_blocking_codes"] == [], \
            (s["blocking_codes"], [e for e in ex if e["severity"] == "blocking"])
        # no invoice disappears (D8 would be BLOCKING); the ledger's 148 re-costed rows are under both removal
        # thresholds (5 % of the 35,871 rows in scope; the 544 it inserts), so a warning with the sample
        removed = [e for e in ex if e["code"] == "rows_removed"]
        assert [(e["target"], e["severity"], e["count"]) for e in removed] == [("stock_movements", "warning", 148)], removed
        assert removed[0]["items"][0]["voucher"].startswith("MRN:") and len(removed[0]["items"]) == 50

        t0 = time.perf_counter()
        out = commit_batch(bid, be, "replay@test", acknowledged=[])
        commit_s = round(time.perf_counter() - t0, 1)
        assert commit_s < REPLAY_TIMEOUT_S, commit_s
        assert out["status"] == "committed"
        acts = {k: v["actions"] for k, v in out["targets"].items()}
        assert acts["orders"] == t["orders"]["actions"] and acts["stock_movements"] == t["stock_movements"]["actions"], acts
        assert out["stage_rows_pruned"] == 2711 + 11803 + 35723, out["stage_rows_pruned"]
        b = be.get_batch(bid)
        assert b["status"] == "committed" and b["committed_by"] == "replay@test" and b["summary"]["commit"]["orders"]["scope"]["kind"] == "span"
        assert b["summary"]["commit"]["stock_movements"]["scope"]["within"] == {"item_name": 175}
        assert set(b["summary"]["commit"]["orders"]["columns"]) >= {"invoice_no", "order_date", "gross_bhd", "source_file"}, "undo compares these"
        print(f"    API backend, {REPLAY_TIMEOUT_S} s statement budget: preview+stage {res['seconds']} s, commit {commit_s} s")
        _STATE["batch_a"] = bid
        _STATE["timings"] = {"preview_s": res["seconds"], "commit_s": commit_s}

        after = _stats(conn)
        prod = _prod_stats() or PROD_AFTER_240926
        src = "production (live, read-only)" if prod is not PROD_AFTER_240926 else "the recorded production figures"
        for tbl in ("orders", "order_lines", "stock_movements", "stock_balance", "ar_ageing", "product_profitability"):
            for k, v in prod[tbl].items():
                assert str(after[tbl][k]) == str(v), (tbl, k, after[tbl][k], v)
        if "per_day" in prod:
            assert after["per_day"] == prod["per_day"], "per-day sales differ from production"
        else:
            for d, v in prod["per_day_tail"].items():
                assert after["per_day"][d] == v, (d, after["per_day"][d], v)
        print(f"    replay == {src}: orders {after['orders']}, lines {after['order_lines']}, ledger {after['stock_movements']}")
        # ingest_stage keeps what the batch WROTE (undo verifies these rows); the unchanged rows are gone
        st = {r[0]: r[1] for r in conn.execute("select action, count(*) from ingest_stage where batch_id = %s group by 1", (bid,)).fetchall()}
        assert st == {"inserted": 31 + 152 + 544 + 132 + 84 + 161, "updated": 2}, st
        rp = sorted(conn.execute("select target, action, count(*) from ingest_replaced where batch_id = %s group by 1, 2", (bid,)).fetchall())
        assert rp == [("order_lines", "updated", 1), ("orders", "updated", 1), ("stock_movements", "deleted", 148)], rp
        assert conn.execute("select count(*) from audit_log where event = 'ingest.batch_commit'").fetchone()[0] == 1
        tm = b["summary"]["commit"]["stock_movements"]["timings"]
        assert set(tm) == {"remove_s", "update_s", "unchanged_s", "insert_s", "assert_s"} and sum(tm.values()) < 60, tm
        assert after["ledger_entries"] == before["ledger_entries"], "no Ledger in the drop: ledger_entries untouched"
    _local(go)


@test("replay: a per-account Ledger export is the truth for ITS accounts only -- other accounts' rows in the span survive")
def _():
    def go(conn):
        from app.ingest_batch import BLOCKING, Parsed, build_preview, commit_batch, undo_batch
        be = _api_backend()
        before = _stats(conn)["ledger_entries"]
        acct = conn.execute("select account from ledger_entries group by 1 having count(*) between 8 and 60 "
                            "and count(distinct entry_date) > 2 order by count(*) desc limit 1").fetchone()[0]
        cols = ["account", "entry_date", "voucher", "counter_account", "debit_bhd", "credit_bhd", "balance_bhd", "currency",
                "payment_mode", "salesman", "narration", "row_hash", "source_file"]
        raw = conn.execute(f"select {', '.join(cols)} from ledger_entries where account = %s order by id", (acct,)).fetchall()
        rows = [{c: (str(v) if c == "entry_date" else float(v) if c in ("debit_bhd", "credit_bhd", "balance_bhd") and v is not None else v)
                 for c, v in zip(cols, r)} for r in raw]
        span = conn.execute("select min(entry_date)::text, max(entry_date)::text from ledger_entries where account = %s", (acct,)).fetchone()
        others_in_span = conn.execute("select count(*) from ledger_entries where account <> %s and entry_date between %s::date and %s::date",
                                      (acct, span[0], span[1])).fetchone()[0]
        assert others_in_span > 100, "the scenario needs other accounts inside this account's span"
        # 1. the whole account re-exported: nothing changes, nothing is removed -- and the commit proves it
        bid, d = _synthetic_batch(be, "ledger_entries", rows)
        assert d["actions"] == {"inserted": 0, "updated": 0, "unchanged": len(rows), "deleted": 0}, d["actions"]
        assert d["db_rows"] == len(rows), "rows in scope = this account's rows in its span, not every account's"
        out = commit_batch(bid, be, "replay@test")
        assert out["targets"]["ledger_entries"]["actions"]["deleted"] == 0 and out["targets"]["ledger_entries"]["rows"] == len(rows)
        assert out["targets"]["ledger_entries"]["scope"]["within"] == {"account": 1}
        assert _stats(conn)["ledger_entries"] == before, "51,261 rows, every account still there"
        u = undo_batch(bid, be, "replay@test")
        assert u["targets"]["ledger_entries"] == {"removed_inserts": 0, "restored": 0, "reinserted": 0}
        # 2. the account with its middle third missing (rows INSIDE the file's span): those would go --
        #    more than 5 % of the rows in scope, so BLOCKING -- and still only this account's rows are in scope
        n = len(rows)
        half = rows[: n // 3] + rows[2 * n // 3:]
        bid2, d2 = _synthetic_batch(be, "ledger_entries", half)
        p = Parsed(folder="half")
        p.rows = {"ledger_entries": half}
        s, ex = build_preview(p, be, bid2, today="2026-09-24")
        rem = [e for e in ex if e["code"] == "rows_removed"]
        gone = conn.execute("select count(*) from ledger_entries where account = %s and entry_date between %s::date and %s::date",
                            (acct, min(r["entry_date"] for r in half), max(r["entry_date"] for r in half))).fetchone()[0] - len(half)
        assert rem and rem[0]["severity"] == BLOCKING and rem[0]["count"] == gone and gone > 0, (rem, gone)
        assert s["targets"]["ledger_entries"]["db_rows"] < others_in_span, "other accounts are not in scope"
        assert s["commit_available"] is True and "rows_removed" in s["blocking_codes"] and "rows_removed" not in s["hard_blocking_codes"]
        be.update_batch(bid2, status="rejected")
        assert _checksums(conn)["ledger_entries"] == _STATE["checksums_before"]["ledger_entries"]
        be.close()
    _local(go)


@test("replay: a Stock_ledger filtered to one item touches that item's rows only (the 175-item export keeps its full-span replace)")
def _():
    def go(conn):
        from app.ingest_batch import commit_batch, parse_folder, undo_batch
        be = _api_backend()
        total = conn.execute("select count(*) from stock_movements").fetchone()[0]
        rows = parse_folder(DROP_240926, row_hook=mask_row).rows["stock_movements"]
        item = max({r["item_name"] for r in rows}, key=lambda i: sum(1 for r in rows if r["item_name"] == i))
        mine = [r for r in rows if r["item_name"] == item]
        assert 20 < len(mine) < len(rows) / 2
        dated = sum(1 for r in mine if r.get("move_date"))
        bid, d = _synthetic_batch(be, "stock_movements", mine)
        assert d["actions"] == {"inserted": 0, "updated": 0, "unchanged": len(mine), "deleted": 0} and d["db_rows"] == dated, (d["actions"], d["db_rows"])
        out = commit_batch(bid, be, "replay@test")
        assert out["targets"]["stock_movements"]["scope"]["within"] == {"item_name": 1}
        assert conn.execute("select count(*) from stock_movements").fetchone()[0] == total, "36,267 rows: the other 174 items untouched"
        undo_batch(bid, be, "replay@test")
        assert conn.execute("select count(*) from stock_movements").fetchone()[0] == total
        be.close()
    _local(go)


@test("replay: undo refuses when a row the batch wrote was changed since, or a refresh outside the batch path ran after the commit")
def _():
    def go(conn):
        import psycopg
        from app.ingest_batch import undo_batch
        be = _api_backend()
        a = _STATE["batch_a"]
        # a. one inserted invoice edited in place (what the old POST /ingest upsert does): refused, nothing changed
        rid = conn.execute("select row_id from ingest_stage where batch_id = %s and target = 'orders' and action = 'inserted' limit 1", (a,)).fetchone()[0]
        before = _checksums(conn)
        conn.execute("update orders set gross_bhd = gross_bhd + 1 where id = %s", (rid,))
        try:
            undo_batch(a, be, "replay@test")
            raise AssertionError("undo must refuse: the row no longer reads as the batch wrote it")
        except psycopg.Error as e:
            assert "no longer read as batch" in str(e) and "orders" in str(e), str(e)[:200]
        conn.execute("update orders set gross_bhd = gross_bhd - 1 where id = %s", (rid,))
        assert _checksums(conn) == before and be.get_batch(a)["status"] == "committed"
        # b. one updated line's source_file rewritten (a re-export through the old path looks like this): refused
        rid2 = conn.execute("select row_id from ingest_stage where batch_id = %s and target = 'order_lines' and action = 'updated' limit 1", (a,)).fetchone()[0]
        old_sf = conn.execute("select source_file from order_lines where id = %s", (rid2,)).fetchone()[0]
        conn.execute("update order_lines set source_file = 'Sales_day_book999.xlsx' where id = %s", (rid2,))
        try:
            undo_batch(a, be, "replay@test")
            raise AssertionError("must refuse")
        except psycopg.Error as e:
            assert "no longer read as batch" in str(e) and "order_lines" in str(e), str(e)[:200]
        conn.execute("update order_lines set source_file = %s where id = %s", (old_sf, rid2))
        # c. a deleted ledger row loaded again under a new id (same key): refused, or undo would duplicate it
        r = conn.execute("select row from ingest_replaced where batch_id = %s and target = 'stock_movements' and action = 'deleted' limit 1", (a,)).fetchone()[0]
        conn.execute("insert into stock_movements (voucher, item_name, row_hash, move_date) values (%s, %s, %s, %s::date)",
                     (r["voucher"], r["item_name"], r["row_hash"], r["move_date"]))
        try:
            undo_batch(a, be, "replay@test")
            raise AssertionError("must refuse")
        except psycopg.Error as e:
            assert "were loaded again since (same key, new row)" in str(e), str(e)[:200]
        conn.execute("delete from stock_movements where voucher = %s and item_name = %s and row_hash = %s and id <> %s",
                     (r["voucher"], r["item_name"], r["row_hash"], r["id"]))
        # d. the default refresh path recorded an ok run after the commit: refused (its upserts leave no trace per row)
        conn.execute("insert into ingest_runs (finished_at, status, file) values (now(), 'ok', 'refresh Sales_day_book1.xlsx (data as of 2026-09-24)')")
        try:
            undo_batch(a, be, "replay@test")
            raise AssertionError("must refuse")
        except psycopg.Error as e:
            assert "a refresh outside the batch path loaded data after batch" in str(e), str(e)[:200]
        conn.execute("delete from ingest_runs where file like 'refresh Sales_day_book1%%'")
        # the batch path's own ingest_runs row ('batch N committed ...') is not a refresh outside the path
        conn.execute("insert into ingest_runs (finished_at, status, file) values (now(), 'ok', %s)", (f"batch {a} committed by replay@test (data as of x)",))
        assert _checksums(conn) == before and be.get_batch(a)["status"] == "committed"
        be.close()
    _local(go)


@test("replay: a second preview of 240926 after the load reports 0 inserts / 0 removals on every target; rejecting it prunes its rows")
def _():
    def go(conn):
        be, res = _run_preview(conn, DROP_240926)
        t = res["summary"]["targets"]
        for tbl, v in t.items():
            assert v["actions"]["inserted"] == 0 and v["actions"].get("deleted", 0) == 0 and v["actions"]["updated"] == 0, (tbl, v["actions"])
            assert v["actions"]["unchanged"] == v["file_rows"], (tbl, v["actions"], v["file_rows"])
        assert res["summary"]["sales"]["per_day_changed"] == [] and res["summary"]["stock"]["per_warehouse"][0]["delta_qty"] == "0.000"
        bid = res["batch_id"]
        n = conn.execute("select count(*) from ingest_stage where batch_id = %s", (bid,)).fetchone()[0]
        assert n == 2743 + 11956 + 36267 + 132 + 84 + 161, n
        be.update_batch(bid, status="rejected")
        pr = be.prune()
        assert pr["stage_rows_deleted"] >= n and conn.execute("select count(*) from ingest_stage where batch_id = %s", (bid,)).fetchone()[0] == 0, pr
        assert pr["stage_rows"] == conn.execute("select count(*) from ingest_stage").fetchone()[0] and pr["stage_bytes"] > 0
        # a preview left for over two days is superseded by the next prune
        conn.execute("update ingest_batches set created_at = now() - interval '3 days' where id = %s", (bid,))
        conn.execute("update ingest_batches set status = 'previewed' where id = %s", (bid,))
        pr = be.prune()
        assert pr["superseded"] == 1 and be.get_batch(bid)["status"] == "rejected" and "superseded" in be.get_batch(bid)["summary"]["reject_reason"]
        be.close()
    _local(go)


@test("replay: a commit whose expected totals do not match RAISES and leaves every table byte-identical")
def _():
    def go(conn):
        from app.ingest_batch import expected_from_summary
        be, res = _run_preview(conn, DROP_240926)
        bid = res["batch_id"]
        before = _checksums(conn)
        exp = expected_from_summary(res["summary"], "tamper@test")
        exp["targets"]["orders"]["rows"] += 1
        try:
            be.rpc("ingest_commit", {"p_batch_id": bid, "p_expected": exp})
            raise AssertionError("must raise")
        except Exception as e:  # noqa: BLE001
            assert "rows in scope after load" in str(e), str(e)[:200]
        exp = expected_from_summary(res["summary"], "tamper@test")
        exp["targets"]["order_lines"]["sums"]["gross_bhd"] = "1.000"
        try:
            be.rpc("ingest_commit", {"p_batch_id": bid, "p_expected": exp})
            raise AssertionError("must raise")
        except Exception as e:  # noqa: BLE001
            assert "the file says" in str(e), str(e)[:200]
        exp = expected_from_summary(res["summary"], "tamper@test")
        exp["targets"]["stock_movements"]["actions"]["inserted"] = 5
        try:
            be.rpc("ingest_commit", {"p_batch_id": bid, "p_expected": exp})
            raise AssertionError("must raise")
        except Exception as e:  # noqa: BLE001
            assert "mismatch -- preview said 5" in str(e), str(e)[:200]
        assert _checksums(conn) == before, "a failed commit must change nothing"
        assert be.get_batch(bid)["status"] == "previewed"
        assert conn.execute("select count(*) from ingest_stage where batch_id = %s and action is not null", (bid,)).fetchone()[0] == 0
        # the same statement-timeout the API sets ends a runaway commit the same way: RAISE, full rollback
        be.conn.execute("set statement_timeout = '100ms'")
        try:
            be.rpc("ingest_commit", {"p_batch_id": bid, "p_expected": expected_from_summary(res["summary"], "slow@test")})
            raise AssertionError("must time out")
        except Exception as e:  # noqa: BLE001
            assert "statement timeout" in str(e), str(e)[:200]
        be.conn.execute(f"set statement_timeout = '{REPLAY_TIMEOUT_S}s'")
        assert _checksums(conn) == before and be.get_batch(bid)["status"] == "previewed"
        be.update_batch(bid, status="rejected")
        be.close()
    _local(go)


@test("replay: undo refuses while a later batch overlaps the span; after that batch is undone, undo restores the pre-load state exactly")
def _():
    def go(conn):
        import psycopg
        from app.ingest_batch import commit_batch, undo_batch
        be = _api_backend()
        a = _STATE["batch_a"]
        # batch B: one synthetic invoice inside A's span (staged directly; the preview's diff is what commit asserts)
        b = be.new_batch([{"file": "synthetic", "target": "orders", "rows": 1}], "replay@test")
        be.stage(b, "orders", [{"invoice_no": "SI : R2-TEST", "order_date": "2026-09-23", "customer_name": "M-test", "gross_bhd": 1.25,
                                "salesman": "Test", "payment_mode": "Cash", "sales_account_name": None, "source_file": "synthetic"}])
        be.update_batch(b, status="previewed", summary={"commit_available": True, "targets": {"orders": {
            "file_rows": 1, "sums": {"gross_bhd": "1.250"}, "actions": {"inserted": 1, "updated": 0, "unchanged": 0, "deleted": 0}}}}, exceptions=[])
        try:
            commit_batch(b, be, "replay@test")
            raise AssertionError("a one-invoice span replace must remove the day's other invoices, so the promised 0 deletes must fail")
        except Exception as e:  # noqa: BLE001
            assert "deleted mismatch -- preview said 0" in str(e), str(e)[:200]
        n23 = conn.execute("select count(*) from orders where order_date = '2026-09-23'").fetchone()[0]
        be.update_batch(b, summary={"commit_available": True, "targets": {"orders": {
            "file_rows": 1, "sums": {"gross_bhd": "1.250"}, "actions": {"inserted": 1, "updated": 0, "unchanged": 0, "deleted": n23}}}})
        out = commit_batch(b, be, "replay@test")
        assert out["targets"]["orders"]["actions"]["deleted"] == n23, out["targets"]["orders"]
        assert out["targets"]["orders"]["scope"] == {"kind": "span", "col": "order_date", "from": "2026-09-23", "to": "2026-09-23", "within": {}}, \
            out["targets"]["orders"]["scope"]
        assert conn.execute("select count(*) from orders where order_date = '2026-09-23'").fetchone()[0] == 1
        try:
            undo_batch(a, be, "replay@test")
            raise AssertionError("undo of A must be refused while B overlaps")
        except Exception as e:  # noqa: BLE001
            assert f"batch {b} touched the same orders scope after batch {a}" in str(e), str(e)[:200]
        # the overlap is by COMMIT TIME: with B's commit stamped before A's, B no longer counts as 'later'
        # (its rows would then be caught by the row checks instead); put it back and the refusal returns
        conn.execute("update ingest_batches set committed_at = (select committed_at - interval '1 minute' from ingest_batches where id = %s) where id = %s", (a, b))
        try:
            undo_batch(a, be, "replay@test")
            raise AssertionError("must still refuse: B's invoice sits where A's deleted rows would come back")
        except Exception as e:  # noqa: BLE001
            assert "touched the same" not in str(e) and "no longer read as batch" in str(e), str(e)[:200]
        conn.execute("update ingest_batches set committed_at = now() where id = %s", (b,))
        u = undo_batch(b, be, "replay@test")
        assert u["targets"]["orders"] == {"removed_inserts": 1, "restored": 0, "reinserted": n23}
        assert conn.execute("select count(*) from orders where order_date = '2026-09-23'").fetchone()[0] == n23
        assert conn.execute("select count(*) from orders where invoice_no = 'SI : R2-TEST'").fetchone()[0] == 0
        after_a = _stats(conn)
        assert after_a["orders"]["rows"] == 2743, "B's undo must give back the state A left"
        t0 = time.perf_counter()
        u = undo_batch(a, be, "replay@test")
        undo_s = round(time.perf_counter() - t0, 1)
        assert u["targets"]["orders"] == {"removed_inserts": 31, "restored": 1, "reinserted": 0}, u["targets"]["orders"]
        assert u["targets"]["stock_movements"] == {"removed_inserts": 544, "restored": 0, "reinserted": 148}, u["targets"]["stock_movements"]
        assert _checksums(conn) == _STATE["checksums_before"], "undo must restore the pre-load tables byte for byte"
        assert be.get_batch(a)["status"] == "undone" and be.get_batch(b)["status"] == "undone"
        print(f"    undo of the full drop: {undo_s} s (budget {REPLAY_TIMEOUT_S} s)")
        try:
            undo_batch(a, be, "replay@test")
            raise AssertionError("an undone batch cannot be undone twice")
        except psycopg.Error as e:
            assert "only a committed batch can be undone" in str(e)
        # the batch and its replaced rows stay (history); its staged rows had no further use and are gone
        assert conn.execute("select count(*) from ingest_replaced where batch_id = %s", (a,)).fetchone()[0] == 1 + 1 + 148
        assert conn.execute("select count(*) from ingest_stage where batch_id = %s", (a,)).fetchone()[0] == 0
        assert be.get_batch(a)["summary"]["stage_rows_pruned"] == 2743 + 11956 + 36267 + 132 + 84 + 161
        be.close()
    _local(go)


@test("replay: the read-only CLI previews with the rows as one jsonb parameter, creates no batch; commit is refused on that connection")
def _():
    def go(conn):
        import psycopg
        from app.ingest_batch import PgBackend
        from scripts.ingest_preview import main as cli
        n_batches = conn.execute("select count(*) from ingest_batches").fetchone()[0]
        with tempfile.TemporaryDirectory() as d:
            rc = cli([str(DROP_240926), "--dsn", PG_DSN, "--out", str(Path(d) / "p"), "--today", "2026-09-24"])
            assert rc == 0, rc
            md = (Path(d) / "p" / "summary.md").read_text(encoding="utf-8")
            # the CLI parses the real files while this cluster holds MASKED merchant names, so every
            # row with a masked column reads as "updated" here; the key-based counts are exact
            assert "| orders | 2743 | 31 |" in md and "| stock_balance | 132 | 132 | 0 | 0 | 0 |" in md, md[:900]
            assert "| product_profitability | 161 | 161 | 0 | 0 | 0 |" in md and "| stock_movements | 36267 | 544 |" in md, md[:1200]
            assert "Commit available: True" in md and "[warning] rows_removed (stock_movements): Stock_ledger: 148" in md
            assert (Path(d) / "p" / "exceptions.csv").is_file()
        assert conn.execute("select count(*) from ingest_batches").fetchone()[0] == n_batches, "the CLI never creates a batch"
        assert conn.execute("select count(*) from ingest_stage where batch_id = 0").fetchone()[0] == 0, "the CLI stages nothing in the database"
        ro = psycopg.connect(PG_DSN)
        try:
            be = PgBackend(ro, read_only=True)
            try:
                be.rpc("ingest_commit", {"p_batch_id": 1, "p_expected": {}})
                raise AssertionError("read-only backend must refuse")
            except RuntimeError as e:
                assert "read-only" in str(e)
        finally:
            ro.close()
    _local(go)


def main() -> int:
    passed = failed = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception:  # noqa: BLE001
            print(f"  FAIL  {name}")
            traceback.print_exc(limit=6)
            failed += 1
    conn = _STATE.get("conn")
    if conn is not None:
        conn.close()
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
