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
comparison figures; without DATABASE_URL the figures recorded on 24-Sep-2026 are used.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import sys
import tempfile
import traceback
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

import warnings  # noqa: E402
warnings.filterwarnings("ignore")

TESTS: list[tuple[str, object]] = []

MAIN = Path(os.environ.get("YQ_MAIN_CHECKOUT") or (ROOT.parents[2] if ROOT.parent.name == "worktrees" else ROOT))
if not os.getenv("DATABASE_URL") and (MAIN / ".env").is_file():
    load_dotenv(MAIN / ".env")     # a worktree has no .env; the main checkout's gives the read-only production figures


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
                 "stock_balance", "ar_ageing", "product_profitability", "selling_prices", "mrn_lines",
                 "mrn_landed_costs", "purchase_costs"]
FOCUS_TABLES = ["orders", "order_lines", "stock_movements", "stock_balance", "ar_ageing", "product_profitability", "selling_prices"]
# merchant names never reach the scratch cluster: masked consistently in the backup copy AND the files
MASK_COLS = {"customers": {"name"}, "orders": {"customer_name"}, "order_lines": {"customer_account"},
             "ar_ageing": {"account"}, "selling_prices": {"customer_name"}}
BLANK_COLS = {"order_lines": {"narration"}, "stock_movements": {"narration"}}
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


def _prod_stats() -> dict | None:
    url = os.getenv("DATABASE_URL")
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
        else:
            assert found[t]["partition"] == cfg["partition"], t


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
    assert scope_of("orders", rows) == {"kind": "span", "col": "order_date", "from": "2026-09-01", "to": "2026-09-03"}
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
    mode, read_only = "fake", False

    def __init__(self, batches=None, ready=True):
        self.batches, self.ready, self.calls = dict(batches or {}), ready, []

    def tables_ready(self):
        return self.ready

    def get_batch(self, batch_id):
        return self.batches.get(batch_id)

    def list_batches(self, limit=20):
        return list(self.batches.values())[:limit]

    def update_batch(self, batch_id, **fields):
        self.batches[batch_id].update(fields)

    def rpc(self, name, args):
        self.calls.append((name, args))
        return {"batch_id": args["p_batch_id"], "status": "committed" if name == "ingest_commit" else "undone", "targets": {}}


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

@test("migration text: one transaction per commit under an advisory lock, replaced rows saved first, totals asserted, service_role only")
def _():
    low = MIGRATION.read_text(encoding="utf-8").lower()
    assert "pg_advisory_xact_lock(hashtext('yq_ingest_commit'))" in low and low.count("pg_advisory_xact_lock") == 2
    assert "create or replace function ingest_commit(p_batch_id bigint, p_expected jsonb)" in low
    assert "create or replace function ingest_undo(p_batch_id bigint, p_actor text default null)" in low
    assert low.count("security definer") == 3, "only ingest_commit, ingest_undo and ingest_stage_analyze are security definer"
    assert "create or replace function ingest_stage_analyze()" in low and "grant  execute on function ingest_stage_analyze() to service_role" in low
    assert "%i is not distinct from %s" not in low and "_ingest_eq(format('l.%i', c.col)" in low, \
        "key matches must stay hash-joinable (_ingest_eq), never IS NOT DISTINCT FROM"
    assert "create or replace function _ingest_eq(a text, b text, typ text)" in low
    assert "grant  execute on function ingest_commit(bigint, jsonb) to service_role" in low
    assert "revoke execute on function ingest_commit(bigint, jsonb) from public, anon, authenticated" in low
    assert "revoke all on ingest_batches, ingest_stage, ingest_replaced from anon, authenticated" in low
    assert "enable row level security" in low and "grant" not in low.split("-- ── 5. grants")[1].split("to service_role")[0].replace("grant  execute", "")
    # every removal / change is copied into ingest_replaced before the row is touched
    assert "insert into ingest_replaced (batch_id, target, action, row) select %s, %l, 'deleted', row from pre" in low
    assert "insert into ingest_replaced (batch_id, target, action, row) select %s, %l, 'voided', row from pre" in low
    assert "case when was_voided then 'revived' else 'updated' end, row from pre" in low
    # the assertions that make a mismatch roll everything back
    for needle in ("mismatch -- preview said", "rows in scope after load", "after load = %, the file says", "are not in the newest export after the void",
                   "were neither matched nor inserted", "natural key(s); dedupe before staging"):
        assert needle in low, needle
    assert "only a previewed batch can be committed" in low and "only a committed batch can be undone" in low
    assert "touched the same % scope after batch" in low, "undo refuses when a later batch overlaps"
    assert "overriding system value" in low, "undo re-inserts deleted rows with their original ids"
    assert "protected  text[] := array['id', 'imported_at', 'created_at', 'updated_at', 'product_id', 'order_id'," in low
    assert "truncate" not in low and "drop table" not in low
    rev = REVERSE.read_text(encoding="utf-8").lower()
    assert "committed ingest batches exist; undo them first" in rev and "drop table if exists ingest_replaced" in rev


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
    import app.database as db
    c = TestClient(m.app)
    assert c.get("/ingest/batches").status_code == 401
    assert c.post("/ingest/batches/1/commit", json={"acknowledged": []}).status_code == 401
    saved = api.backend_factory
    saved_client = db.get_client

    def _no_db():
        # audit_log rows and the cache flush go through app.database.get_client; a worktree under the
        # main checkout finds the real .env (dotenv walks up), so this must never reach production
        raise RuntimeError("no database in this test")
    db.get_client = _no_db
    m.app.dependency_overrides[require_admin] = lambda: CurrentUser(user_id="u", email="admin@example.com", role="admin")
    try:
        fb = _FakeBackend(ready=False)
        api.backend_factory = lambda: fb
        r = c.get("/ingest/batches")
        assert r.status_code == 200 and r.json() == {"batches": [], "migration_applied": False}
        assert c.get("/ingest/batches/1").status_code == 404
        fb2 = _FakeBackend({3: {"id": 3, "status": "previewed", "summary": {"commit_available": False}, "exceptions": []}})
        api.backend_factory = lambda: fb2
        r = c.post("/ingest/batches/3/commit", json={"acknowledged": []})
        assert r.status_code == 200 and r.json()["ok"] is False and "cannot be committed" in r.json()["error"] and fb2.calls == []
        r = c.post("/ingest/batches/3/reject", json={"reason": "wrong day"})
        assert r.status_code == 200 and fb2.batches[3]["status"] == "rejected" and fb2.batches[3]["summary"]["reject_reason"] == "wrong day"
        r = c.post("/ingest/batches/3/undo", json={})
        assert r.status_code == 200 and r.json()["ok"] is True and fb2.calls[-1][0] == "ingest_undo"
    finally:
        api.backend_factory = saved
        db.get_client = saved_client
        m.app.dependency_overrides.pop(require_admin, None)


# ── 5. the replay on the local scratch Postgres ───────────────────────────────

def _run_preview(conn, folder, today="2026-09-24"):
    from app.ingest_batch import PgBackend, run_preview
    be = PgBackend(conn, read_only=False)
    with tempfile.TemporaryDirectory() as d:
        return be, run_preview(folder, be, actor="replay@test", persist=True, out_dir=Path(d) / "p", today=today, row_hook=mask_row)


@test("replay 240926 on local Postgres: preview == the plan's numbers, commit asserts, result == production's tables")
def _():
    def go(conn):
        from app.ingest_batch import commit_batch
        before = _stats(conn)
        assert before["orders"]["rows"] == 2712 and before["order_lines"]["rows"] == 11804 and before["stock_movements"]["rows"] == 35871, before
        _STATE["checksums_before"] = _checksums(conn)
        be, res = _run_preview(conn, DROP_240926)
        s, ex = res["summary"], res["exceptions"]
        bid = res["batch_id"]
        assert bid and s["migration_applied"] and s["db_mode"] == "pg"
        t = s["targets"]
        # the plan's section-2 preview, exactly
        assert t["orders"]["actions"] == {"inserted": 31, "updated": 1, "unchanged": 2711, "deleted": 0}, t["orders"]["actions"]
        assert t["orders"]["file_rows"] == 2743 and t["orders"]["sums"] == {"gross_bhd": "74238.130"} and t["orders"]["db_sums"] == {"gross_bhd": "72744.930"}
        assert t["order_lines"]["actions"] == {"inserted": 152, "updated": 1, "unchanged": 11803, "deleted": 0}, t["order_lines"]["actions"]
        assert t["orders"]["updated_sample"] == [{"invoice_no": "SI : SI-YQ-26-09-6", "order_date": "2026-09-03"}], "the one re-pointed invoice"
        assert t["order_lines"]["sums"] == {"gross_bhd": "74238.130", "taxable_bhd": "67490.060"}
        # Focus re-costed 148 ledger rows (new row_hash): out with the old, in with the new, net +396 rows, nothing updated in place
        assert t["stock_movements"]["actions"] == {"inserted": 544, "updated": 0, "unchanged": 35723, "deleted": 148}, t["stock_movements"]["actions"]
        assert t["stock_balance"]["actions"] == {"inserted": 132, "updated": 0, "unchanged": 0, "deleted": 0}
        assert t["ar_ageing"]["actions"] == {"inserted": 84, "updated": 0, "unchanged": 0, "deleted": 0}
        assert t["product_profitability"]["actions"] == {"inserted": 161, "updated": 0, "unchanged": 0, "deleted": 0}
        assert [d["key"] for d in s["sales"]["per_day_changed"]] == ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24"], s["sales"]["per_day_changed"]
        assert s["stock"]["missing_warehouses"] == [] and s["stock"]["db_latest_as_of"] == "2026-09-21"
        codes = {e["code"] for e in ex}
        assert "partial_day" in codes and "ar_total_gap" in codes and "mrn_without_cost" in codes
        assert "YQ-26-09-3" in s["mrn"]["without_cost"] and "YQ-26-09-2" in s["mrn"]["with_cost"]
        assert s["commit_available"] is True and s["blocking_codes"] == [], (s["blocking_codes"], [e for e in ex if e["severity"] == "blocking"])
        # no invoice disappears (D8 would be BLOCKING); the ledger's 148 re-costed rows are a warning with the sample
        removed = [e for e in ex if e["code"] == "rows_removed"]
        assert [(e["target"], e["severity"], e["count"]) for e in removed] == [("stock_movements", "warning", 148)], removed
        assert removed[0]["items"][0]["voucher"].startswith("MRN:") and len(removed[0]["items"]) == 50

        out = commit_batch(bid, be, "replay@test", acknowledged=[])
        assert out["status"] == "committed"
        acts = {k: v["actions"] for k, v in out["targets"].items()}
        assert acts["orders"] == t["orders"]["actions"] and acts["stock_movements"] == t["stock_movements"]["actions"], acts
        b = be.get_batch(bid)
        assert b["status"] == "committed" and b["committed_by"] == "replay@test" and b["summary"]["commit"]["orders"]["scope"]["kind"] == "span"
        _STATE["batch_a"] = bid

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
        # ingest_stage carries what each staged row became; ingest_replaced holds only the re-pointed invoice
        st = {r[0]: r[1] for r in conn.execute("select action, count(*) from ingest_stage where batch_id = %s group by 1", (bid,)).fetchall()}
        assert st == {"inserted": 31 + 152 + 544 + 132 + 84 + 161, "updated": 2, "unchanged": 2711 + 11803 + 35723}, st
        rp = sorted(conn.execute("select target, action, count(*) from ingest_replaced where batch_id = %s group by 1, 2", (bid,)).fetchall())
        assert rp == [("order_lines", "updated", 1), ("orders", "updated", 1), ("stock_movements", "deleted", 148)], rp
        assert conn.execute("select count(*) from audit_log where event = 'ingest.batch_commit'").fetchone()[0] == 1
        tm = b["summary"]["commit"]["stock_movements"]["timings"]
        assert set(tm) == {"remove_s", "update_s", "unchanged_s", "insert_s", "assert_s"} and sum(tm.values()) < 60, tm
    _local(go)


@test("replay: a second preview of 240926 after the load reports 0 inserts / 0 removals on every target")
def _():
    def go(conn):
        be, res = _run_preview(conn, DROP_240926)
        t = res["summary"]["targets"]
        for tbl, v in t.items():
            assert v["actions"]["inserted"] == 0 and v["actions"].get("deleted", 0) == 0 and v["actions"]["updated"] == 0, (tbl, v["actions"])
            assert v["actions"]["unchanged"] == v["file_rows"], (tbl, v["actions"], v["file_rows"])
        assert res["summary"]["sales"]["per_day_changed"] == [] and res["summary"]["stock"]["per_warehouse"][0]["delta_qty"] == "0.000"
        be.update_batch(res["batch_id"], status="rejected")
    _local(go)


@test("replay: a commit whose expected totals do not match RAISES and leaves every table byte-identical")
def _():
    def go(conn):
        from app.ingest_batch import PgBackend, expected_from_summary
        be = PgBackend(conn, read_only=False)
        _, res = _run_preview(conn, DROP_240926)
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
        be.update_batch(bid, status="rejected")
    _local(go)


@test("replay: undo refuses while a later batch overlaps the span; after that batch is undone, undo restores the pre-load state exactly")
def _():
    def go(conn):
        import psycopg
        from app.ingest_batch import PgBackend, commit_batch, undo_batch
        be = PgBackend(conn, read_only=False)
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
        assert out["targets"]["orders"]["actions"]["deleted"] == n23 and out["targets"]["orders"]["scope"] == {"kind": "span", "col": "order_date", "from": "2026-09-23", "to": "2026-09-23"}
        assert conn.execute("select count(*) from orders where order_date = '2026-09-23'").fetchone()[0] == 1
        try:
            undo_batch(a, be, "replay@test")
            raise AssertionError("undo of A must be refused while B overlaps")
        except Exception as e:  # noqa: BLE001
            assert f"batch {b} touched the same orders scope after batch {a}" in str(e), str(e)[:200]
        u = undo_batch(b, be, "replay@test")
        assert u["targets"]["orders"] == {"removed_inserts": 1, "restored": 0, "reinserted": n23}
        assert conn.execute("select count(*) from orders where order_date = '2026-09-23'").fetchone()[0] == n23
        assert conn.execute("select count(*) from orders where invoice_no = 'SI : R2-TEST'").fetchone()[0] == 0
        after_a = _stats(conn)
        assert after_a["orders"]["rows"] == 2743, "B's undo must give back the state A left"
        u = undo_batch(a, be, "replay@test")
        assert u["targets"]["orders"] == {"removed_inserts": 31, "restored": 1, "reinserted": 0}, u["targets"]["orders"]
        assert u["targets"]["stock_movements"] == {"removed_inserts": 544, "restored": 0, "reinserted": 148}, u["targets"]["stock_movements"]
        assert _checksums(conn) == _STATE["checksums_before"], "undo must restore the pre-load tables byte for byte"
        assert be.get_batch(a)["status"] == "undone" and be.get_batch(b)["status"] == "undone"
        try:
            undo_batch(a, be, "replay@test")
            raise AssertionError("an undone batch cannot be undone twice")
        except psycopg.Error as e:
            assert "only a committed batch can be undone" in str(e)
        # the batch, its staged rows and its replaced rows are all still there (history, never deleted)
        assert conn.execute("select count(*) from ingest_replaced where batch_id = %s", (a,)).fetchone()[0] == 1 + 1 + 148
        assert conn.execute("select count(*) from ingest_stage where batch_id = %s", (a,)).fetchone()[0] == 2743 + 11956 + 36267 + 132 + 84 + 161
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
            traceback.print_exc(limit=3)
            failed += 1
    conn = _STATE.get("conn")
    if conn is not None:
        conn.close()
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
