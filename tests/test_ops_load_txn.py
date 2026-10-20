"""Release 3 of the load speed-up (20-Oct-2026): the Focus tables in ONE database transaction
(scripts/load_txn.py, used by scripts/load_supabase.py --one-transaction).

    python -m tests.test_ops_load_txn

Same lightweight runner as the other suites (no pytest). SUPABASE_URL / SUPABASE_KEY / DATABASE_URL
are pinned to ci.invalid before any import: a worktree would otherwise find the main folder's .env
through python-dotenv's find_dotenv. Every name and number here is invented.

Pure (no database): the hold before the commit and the avoid list's text, identifiers, the request
body text (httpx's own encoder), the SQL the client builds (on a recording connection), the loader's
seams (_guard, one upsert per table, one ledger delete), main() asked for one transaction (no REST
fallback when the session cannot open; rollback, close and the error raised when a table fails; the
commit given the parsed avoid list), and the writer's flag and environment names.

Local (SKIP unless YQ_LOCAL_PG_LOAD_TXN names a scratch cluster on THIS PC, e.g.
postgresql://postgres@localhost:55731 -- the suite drops and creates its own two databases there,
yq_load_txn_rest and yq_load_txn_one):
  * PARITY of the whole loader on invented drops: today's REST semantics (a PostgREST stand-in: one
    transaction per request, the body through json_to_recordset with the columns' types as PostgREST
    v12 reads it, the loader's own 500-row chunks and month-by-month deletes) against the one
    transaction (json_populate_record, in the same slices). Price books (void, revive, the
    mass-void guard), sales (prune, re-line, both span guards), REPLACE_OK / PARTIAL_OK, profitability,
    ledger, receivables, stock: every table, every id and the loader's output the same after every step;
  * a load that fails half-way leaves nothing behind on the one transaction (and part of it on REST);
  * nothing is visible to another session before the commit; the commit waits out an interval it is
    told to avoid; a tolerated failure (a missing table) does not abort the transaction.
Opt-in (YQ_LOAD_TXN_DROPS = real drop folders separated by ';', read in place, oldest first): the same
parity on real Focus exports, through scripts.ingest into a temporary folder.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import traceback
import types
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CI_DATABASE_URL = "postgresql://ci:ci@ci.invalid:5432/ci"
LOCAL_CLUSTER = os.environ.get("YQ_LOCAL_PG_LOAD_TXN", "")
REAL_DROPS = [p for p in os.environ.get("YQ_LOAD_TXN_DROPS", "").split(";") if p.strip()]
os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["SUPABASE_SERVICE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = CI_DATABASE_URL
for _k in ("YQ_LOAD_ONE_TRANSACTION", "YQ_LOAD_COMMIT_AVOID"):
    os.environ.pop(_k, None)

import pandas as pd  # noqa: E402

from scripts import load_supabase as ls  # noqa: E402
from scripts import load_txn  # noqa: E402

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


def raises(kind, fn) -> BaseException:
    try:
        fn()
    except kind as exc:
        return exc
    raise AssertionError(f"expected {kind.__name__}")


# ── pure: the commit's hold, the avoid list, identifiers, the body text ──────────────────────

@test("the hold before the commit: outside every interval none; inside, to its end; back-to-back intervals together")
def _():
    hs = load_txn.hold_seconds
    assert hs(1000, []) == 0 and hs(1000, [(2000, 2100)]) == 0 and hs(2100, [(2000, 2100)]) == 0   # end exclusive
    assert hs(2000, [(2000, 2100)]) == 100 and hs(2050.5, [(2000, 2100)]) == 49.5
    assert hs(2050, [(2000, 2100), (2100, 2150)]) == 100                 # one ends where the next begins
    assert hs(2050, [(2100, 2150), (2000, 2120)]) == 100                 # overlapping, in any order
    assert hs(1999, [(2000, 2100)]) == 0


@test("the avoid list: start-end epoch seconds, sorted; an empty text avoids nothing; anything else is refused whole")
def _():
    pa = load_txn.parse_avoid
    assert pa("") == ([], None) and pa(None) == ([], None) and pa(" , ") == ([], None)
    assert pa("1760941890-1760941995,1760941000-1760941105") == ([(1760941000, 1760941105), (1760941890, 1760941995)], None)
    for bad in ("1760941890", "1760941995-1760941890", "x-y", "1760941890-1760941995;1", "12-34"):
        got, why = pa(bad)
        assert got == [] and why, bad


@test("identifiers: plain lower-case names only; the body text is httpx's own (compact, no NaN, non-ASCII kept)")
def _():
    assert load_txn.ident("order_lines") == '"order_lines"' and load_txn.ident("bucket_0_30") == '"bucket_0_30"'
    for bad in ("Orders", "a b", 'x"y', "drop table", "", None, "1abc", "a;b"):
        raises(ValueError, lambda b=bad: load_txn.ident(b))
    row = {"a": "Café ش", "n": 0.1 + 0.2, "f": 5.0, "none": None, "j": json.dumps({"val_1": None})}
    text = load_txn.body_json(row)
    import httpx._content as hc                     # what supabase-py really sends: httpx's json encoder
    _headers, stream = hc.encode_json(row)
    assert text.encode("utf-8") == b"".join(stream) and "0.30000000000000004" in text and "é" in text
    raises(ValueError, lambda: load_txn.body_json({"x": float("nan")}))
    assert load_txn.payload_columns([{"a": 1, "b": 2}, {"b": 3, "c": 4}]) == ["a", "b", "c"]


# ── pure: the SQL the client builds, on a recording connection ───────────────────────────────

class _Cur:
    def __init__(self, conn):
        self.conn, self.rowcount, self._one = conn, 0, None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.sql.append((" ".join(str(sql).split()), list(params or [])))
        if "from pg_attribute" in sql:
            self._rows = [(c, t) for c, t in self.conn.types.items()]
        elif "json_agg" in sql:
            self._one = ("[]",)
        elif sql.startswith("select count(*)"):
            self._one = (0,)
        self.rowcount = 3

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._one

    @contextlib.contextmanager
    def copy(self, sql):
        rows = []
        self.conn.sql.append((" ".join(sql.split()), rows))
        yield types.SimpleNamespace(write_row=rows.append)


class _Conn:
    def __init__(self, types_: dict):
        self.types, self.sql, self.committed, self.rolled = types_, [], 0, 0

    def cursor(self):
        return _Cur(self)

    @contextlib.contextmanager
    def transaction(self):
        self.sql.append(("SAVEPOINT", []))
        yield
        self.sql.append(("RELEASE", []))

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled += 1

    def close(self):
        pass


SM_TYPES = {"id": "bigint", "move_date": "date", "item_name": "text", "received_qty": "numeric",
            "voided_at": "timestamp with time zone"}


@test("the client's SQL: typed filters, PostgREST's 1000-row cap, safeupdate, one COPY + one INSERT .. ON CONFLICT per upsert")
def _():
    conn = _Conn(SM_TYPES)
    c = load_txn.TxnClient(conn)
    c.table("stock_movements").select("id,item_name").gte("move_date", "2026-09-01").lte("move_date", "2026-09-30") \
        .order("id").range(1000, 1999).execute()
    sql, params = conn.sql[-1]
    assert sql == ("select coalesce(json_agg(_q), '[]'::json)::text from (select _t.\"id\", _t.\"item_name\" from "
                   "public.\"stock_movements\" _t where _t.\"move_date\" >= %s::date and _t.\"move_date\" <= %s::date "
                   "order by _t.\"id\" asc limit 1000 offset 1000) _q"), sql
    assert params == ["2026-09-01", "2026-09-30"]
    c.table("stock_movements").select("item_name").order("move_date", desc=True).limit(1).execute()
    assert conn.sql[-1][0].endswith("order by _t.\"move_date\" desc limit 1) _q")        # no NULLS clause, as postgrest-py
    c.table("stock_movements").select("id").not_.is_("voided_at", "null").execute()
    assert "where _t.\"voided_at\" is not null" in conn.sql[-1][0] and conn.sql[-1][0].endswith("limit 1000) _q")
    assert c.table("stock_movements").delete(count="exact").in_("id", [3, 4]).execute().count == 3
    assert conn.sql[-1] == ('delete from public."stock_movements" _t where _t."id" = any(%s::bigint[])', [["3", "4"]])
    raises(ValueError, lambda: c.table("stock_movements").delete().execute())         # never a whole table
    raises(ValueError, lambda: c.table("stock_movements").update({"item_name": "x"}).execute())
    raises(LookupError, lambda: c.table("stock_movements").select("id").eq("no_such", 1).execute())
    raises(ValueError, lambda: c.table("stock_movements").select("id").not_.eq("item_name", "x"))
    c.table("stock_movements").update({"voided_at": None, "item_name": "x"}).in_("id", [9]).execute()
    sql, params = conn.sql[-1]
    assert sql.startswith('update public."stock_movements" _t set "voided_at" = _b."voided_at", "item_name" = _b."item_name" '
                          'from json_populate_record(null::public."stock_movements", %s::json) _b where _t."id" = any(')
    assert params[0] == '{"voided_at":null,"item_name":"x"}'
    n = c.delete_windows("stock_movements", "move_date", [("2026-08-15", "2026-08-31"), ("2026-09-01", "2026-09-30")])
    assert n == 3 and conn.sql[-1][1] == ["2026-08-15", "2026-08-31", "2026-09-01", "2026-09-30"]
    assert conn.sql[-1][0].count(">= %s::date and") == 2 and " or " in conn.sql[-1][0]
    # one upsert: a temp table that drops at commit, COPY in file order, then ONE server-side loop of the
    # REST path's slices: INSERT .. ON CONFLICT of every body column, `chunk` rows at a time, in order
    conn.sql.clear()
    rows = [{"voucher": f"V{i}", "item_name": "I1", "received_qty": "2.0" if i % 2 else None} for i in range(1201)]
    assert c.bulk_upsert("stock_movements", rows, "voucher,item_name", chunk=500) == 1201
    (create, _), (copy, sent), (loop, _) = conn.sql
    assert create == "create temp table _load_rows_1 (ord bigint primary key, j json not null) on commit drop"
    assert copy == "copy _load_rows_1 (ord, j) from stdin" and sent == [(i, load_txn.body_json(r)) for i, r in enumerate(rows)]
    assert loop == ('do $load$ begin for _k in 0..1200 by 500 loop insert into public."stock_movements" ("voucher", '
                    '"item_name", "received_qty") select _r."voucher", _r."item_name", _r."received_qty" from _load_rows_1 _s, '
                    'json_populate_record(null::public."stock_movements", _s.j) _r where _s.ord >= _k and _s.ord < _k + 500 '
                    'order by _s.ord on conflict ("voucher", "item_name") do update set "voucher" = excluded."voucher", '
                    '"item_name" = excluded."item_name", "received_qty" = excluded."received_qty"; end loop; end $load$'), loop
    assert c.bulk_upsert("stock_movements", [], "voucher") == 0 and len(conn.sql) == 3          # nothing sent for no rows
    raises(ValueError, lambda: c.bulk_upsert("stock_movements", rows[:1], ""))                 # a key is always named
    raises(ValueError, lambda: c.bulk_upsert("stock_movements", rows[:1], "voucher", chunk=0))
    assert len(conn.sql) == 3
    # the commit: at once outside the intervals; waited out inside them, the transaction kept open
    slept, said = [], []
    assert c.commit([(100, 200)], now=lambda: 50.0, sleep=slept.append, say=said.append) == 0 and conn.committed == 1
    assert slept == [] and said == [] and c.commit_sent is True
    assert c.commit([(100, 200), (200, 260)], now=lambda: 150.0, sleep=slept.append, say=said.append) == 110
    assert slept == [110] and conn.committed == 2 and "waits 110 s" in said[0]
    assert conn.sql[-1][0] == "set local idle_in_transaction_session_timeout = '230s'"
    c.commit([(0, 10 ** 10)], now=lambda: 5.0, sleep=slept.append, say=said.append)
    assert slept[-1] == load_txn.HOLD_MAX_S                                             # never longer than this
    # an aborted transaction is never committed (Postgres would answer COMMIT with a silent rollback)
    bad = _Conn(SM_TYPES)
    bad.info = types.SimpleNamespace(transaction_status=types.SimpleNamespace(name="INERROR"))
    d = load_txn.TxnClient(bad)
    exc = raises(RuntimeError, lambda: d.commit([], sleep=slept.append, say=said.append))
    assert "the transaction is INERROR" in str(exc) and bad.committed == 0 and d.commit_sent is False


# ── pure: the loader's seams ─────────────────────────────────────────────────────────────────

class _Bulk:
    """A stand-in for the one-transaction client at the loader's seams."""

    def __init__(self):
        self.calls, self.guards = [], 0

    def bulk_upsert(self, table, rows, on_conflict, chunk=None):
        self.calls.append(("upsert", table, len(rows), on_conflict, chunk))
        return len(rows)

    def delete_windows(self, table, col, windows):
        self.calls.append(("delete_windows", table, col, list(windows)))
        return 7

    @contextlib.contextmanager
    def savepoint(self):
        self.guards += 1
        yield

    def table(self, name):
        raise AssertionError(f"the builder was used for {name}: the one-transaction seams were missed")


@test("the loader's seams: one upsert per table, the ledger's month windows in one delete, a savepoint only where it tolerates a failure")
def _():
    b = _Bulk()
    with contextlib.redirect_stdout(io.StringIO()) as out:
        assert ls._upsert(b, "order_lines", [{"invoice_no": str(i)} for i in range(1234)], "invoice_no,line_no") == 1234
    assert b.calls == [("upsert", "order_lines", 1234, "invoice_no,line_no", ls.CHUNK)]        # REST's slices
    assert "upserted order_lines                 1234" in out.getvalue()               # the line refresh.py counts
    assert ls._delete_span(b, "stock_movements", "move_date", "2026-08-20", "2026-10-03") == 7
    assert b.calls[-1] == ("delete_windows", "stock_movements", "move_date",
                           [("2026-08-20", "2026-08-31"), ("2026-09-01", "2026-09-30"), ("2026-10-01", "2026-10-03")])
    with ls._guard(b):
        pass
    assert b.guards == 1
    plain = types.SimpleNamespace(table=lambda name: None)                              # a REST client: nothing to undo
    with ls._guard(plain):
        pass
    # every call the loader tolerates failing sits in a guard; nothing else does
    src = (ROOT / "scripts" / "load_supabase.py").read_text(encoding="utf-8")
    assert src.count("with _guard(client):") == 7, src.count("with _guard(client):")
    for guarded in ('client.table("audit_log").insert', "db_rows = _fetch_span(client", "_previous_warehouses(client, as_of)",
                    "_fetch_book_rows(client, book, voided=True)", '_upsert(client, "ar_ageing_totals"',
                    'client.table("ingest_runs").insert', 'client.table("selling_prices").select("voided_at")'):
        at = src.index(guarded)
        assert "with _guard(client):" in src[src.rindex("\n", 0, src.rindex("\n", 0, at)) - 300:at], guarded


class _FakeTxn:
    """TxnClient.open's stand-in for main(): no database, records what main() asks of it."""
    opened: list = []

    def __init__(self, fail_on: str | None = None, commit_fails: bool = False, hold_cut: bool = False):
        self.fail_on, self.statements, self.committed, self.rolled, self.closed = fail_on, 5, None, 0, 0
        self.upserts, self.commit_fails, self.hold_cut, self.commit_sent = [], commit_fails, hold_cut, False

    def bulk_upsert(self, table, rows, on_conflict, chunk=None):
        if table == self.fail_on:
            raise RuntimeError(f"the {table} insert failed")
        self.upserts.append(table)
        return len(rows)

    def delete_windows(self, table, col, windows):
        return 0

    @contextlib.contextmanager
    def savepoint(self):
        yield

    def table(self, name):
        q = types.SimpleNamespace()
        for m in ("select", "order", "limit", "gte", "lte", "lt", "eq", "in_", "range", "delete", "insert", "update"):
            setattr(q, m, lambda *a, **k: q)
        q.execute = lambda: types.SimpleNamespace(data=[], count=0)
        return q

    def commit(self, avoid=None, **k):
        if self.hold_cut:                                # stopped while it held the commit: COMMIT never went out
            raise TimeoutError("the hold was cut short")
        self.commit_sent = True
        if self.commit_fails:
            raise ConnectionError("the server closed the connection")
        self.committed = list(avoid or [])
        return 0.0

    def rollback(self):
        self.rolled += 1

    def close(self):
        self.closed += 1


@contextlib.contextmanager
def _clean_folder(tables: dict):
    tmp = Path(tempfile.mkdtemp(prefix="loadtxn_"))
    saved = ls.CLEAN
    try:
        for name, rows in tables.items():
            pd.DataFrame(rows).to_csv(tmp / f"{name}.csv", index=False, encoding="utf-8")
        ls.CLEAN = tmp
        yield tmp
    finally:
        ls.CLEAN = saved
        shutil.rmtree(tmp, ignore_errors=True)


@contextlib.contextmanager
def _patched_open(factory):
    real_open, real_client = load_txn.TxnClient.open, ls._client
    load_txn.TxnClient.open = classmethod(lambda cls, *a, **k: factory())

    def no_rest():
        raise AssertionError("the REST client was asked for in a one-transaction load")
    ls._client = no_rest
    try:
        yield
    finally:
        load_txn.TxnClient.open, ls._client = real_open, real_client


@test("main() asked for one transaction: never REST; a session that cannot open writes nothing; a failure rolls back, closes, raises")
def _():
    sm = [{"item_name": "Item A1", "move_date": "2026-10-01", "voucher": "V : 1", "row_hash": "h1", "received_qty": "1"}]
    stock = [{"item_name": "Item A1", "warehouse_name": "Store invented", "net_qty": "2", "selling_rate_bhd": "1.5",
              "total_value_bhd": "3", "as_of_date": "2026-10-01", "source_file": "s.xlsx"}]
    with _clean_folder({"stock_movements": sm, "stock_balance": stock}):
        def refuse():
            raise RuntimeError("connection refused")
        with _patched_open(refuse), contextlib.redirect_stdout(io.StringIO()) as out:
            assert ls.main(["--one-transaction"]) == 1
        assert "could not open its database session (connection refused); nothing was written" in out.getvalue()
        # a table fails: rolled back and closed, never committed, the error raised (refresh sees a failed load stage)
        made = []
        with _patched_open(lambda: made.append(_FakeTxn(fail_on="stock_balance")) or made[-1]), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            exc = raises(RuntimeError, lambda: ls.main(["--one-transaction"]))
        t = made[-1]
        assert "the stock_balance insert failed" in str(exc) and t.rolled == 1 and t.closed == 1 and t.committed is None
        assert "one transaction ROLLED BACK (RuntimeError: the stock_balance insert failed): nothing of this load was written" \
            in out.getvalue() and t.upserts == ["stock_movements"]
        # the COMMIT itself gets no answer: whole or nothing, but which is not known, so it never says "nothing"
        with _patched_open(lambda: made.append(_FakeTxn(commit_fails=True)) or made[-1]), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            raises(ConnectionError, lambda: ls.main(["--one-transaction"]))
        t = made[-1]
        assert t.closed == 1 and t.rolled == 0 and "nothing of this load was written" not in out.getvalue()
        assert "the commit got no answer (ConnectionError: the server closed the connection); the database holds this " \
               "load whole or not at all: load it again to be sure" in out.getvalue()
        # ... but stopped while it held the commit (COMMIT never sent): rolled back, and it says nothing was written
        with _patched_open(lambda: made.append(_FakeTxn(hold_cut=True)) or made[-1]), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            raises(TimeoutError, lambda: ls.main(["--one-transaction"]))
        t = made[-1]
        assert t.rolled == 1 and t.closed == 1 and "no answer" not in out.getvalue()
        assert "one transaction ROLLED BACK (TimeoutError: the hold was cut short): nothing of this load was written" \
            in out.getvalue()
        # the environment asks for it too (as the writer's refresh child does); the avoid list reaches the commit
        os.environ[load_txn.ONE_TXN_ENV] = "1"
        os.environ[load_txn.AVOID_ENV] = "1760941890-1760941995"
        try:
            with _patched_open(lambda: made.append(_FakeTxn()) or made[-1]), contextlib.redirect_stdout(io.StringIO()) as out:
                assert ls.main([]) == 0
        finally:
            os.environ.pop(load_txn.ONE_TXN_ENV, None)
            os.environ.pop(load_txn.AVOID_ENV, None)
        t = made[-1]
        assert t.committed == [(1760941890, 1760941995)] and t.closed == 1 and t.rolled == 0
        text = out.getvalue()
        assert "(one transaction: nothing of this load is visible to anyone else until it commits)" in text
        assert "(one transaction: committed, 5 statements in " in text and "Load complete." in text
        assert t.upserts == ["stock_movements", "stock_balance"]
        # an avoid list that cannot be read: said, and the commit is made at once
        os.environ[load_txn.AVOID_ENV] = "soon"
        try:
            with _patched_open(lambda: made.append(_FakeTxn()) or made[-1]), contextlib.redirect_stdout(io.StringIO()) as out:
                assert ls.main(["--one-transaction"]) == 0
        finally:
            os.environ.pop(load_txn.AVOID_ENV, None)
        assert made[-1].committed == [] and "could not be read" in out.getvalue()


@test("the writer's words for the loader: its flag and the two environment names are the loader's own")
def _():
    from scripts import yq_load_go as go
    assert go.ONE_TXN_ARG == ls.ONE_TXN_ARG == "--one-transaction"
    assert go.ONE_TXN_ENV == load_txn.ONE_TXN_ENV and go.AVOID_ENV == load_txn.AVOID_ENV
    got, why = load_txn.parse_avoid(go.avoid_spec(go.commit_avoid(go.datetime(2026, 1, 1, 9, 2, tzinfo=go.timezone.utc))))
    assert why is None and got, "the writer's list reads back whole"


# ── local: a scratch cluster on this PC ──────────────────────────────────────────────────────

FOCUS = ["categories", "products", "customers", "orders", "order_lines", "stock_movements", "ledger_entries",
         "product_profitability", "selling_prices", "ar_ageing", "ar_ageing_totals", "stock_balance", "audit_log",
         "ingest_runs"]
GENERATED = {"id", "imported_at", "created_at", "updated_at", "ts", "started_at", "finished_at"}
CLOCK_SET = {"voided_at"}                  # set by the loader from the clock: compared as set / not set
REST_DB, ONE_DB = "yq_load_txn_rest", "yq_load_txn_one"
# the Focus tables as the repository defines them: scripts/schema.sql and the table DDL of dashboard_, stock_,
# economics_v2_, selling_prices_void_, pricebook_key_, customer_dimension_ and category_migration.sql
DDL = """
create table categories (id bigint generated always as identity primary key, name text unique not null,
  created_at timestamptz default now(), division text);
create table products (id bigint generated always as identity primary key, sku_code text unique not null, item_name text,
  category_id bigint references categories(id), unit_name text, status text, created_at timestamptz default now(),
  updated_at timestamptz default now());
create table customers (id bigint generated always as identity primary key, name text unique not null,
  created_at timestamptz default now(), area text, segment text);
create table orders (id bigint generated always as identity primary key, invoice_no text unique not null, order_date date,
  customer_id bigint references customers(id), customer_name text, gross_bhd numeric, salesman text, payment_mode text,
  sales_account_name text, source_file text, imported_at timestamptz default now());
create table order_lines (id bigint generated always as identity primary key, invoice_no text not null,
  order_id bigint references orders(id), line_no int, line_date date, customer_account text, item_name text,
  product_id bigint references products(id), quantity numeric, rate_bhd numeric, gross_bhd numeric, discount_bhd numeric,
  taxable_bhd numeric, vat_amount_bhd numeric, total_amount_bhd numeric, warehouse_name text, narration text,
  source_file text, imported_at timestamptz default now(), unique (invoice_no, line_no));
create table stock_movements (id bigint generated always as identity primary key, item_name text not null,
  product_id bigint references products(id), move_date date, voucher text, voucher_type text, received_qty numeric,
  received_rate_bhd numeric, issued_qty numeric, issued_rate_bhd numeric, balance_qty numeric, received_value_bhd numeric,
  issued_value_bhd numeric, balance_value_bhd numeric, avg_rate_bhd numeric, warehouse_name text, to_warehouse_name text,
  narration text, row_hash text, source_file text, imported_at timestamptz default now(), unique (voucher, item_name, row_hash));
create table ledger_entries (id bigint generated always as identity primary key, account text not null, entry_date date,
  voucher text, counter_account text, debit_bhd numeric, credit_bhd numeric, balance_bhd numeric, currency text,
  payment_mode text, salesman text, narration text, row_hash text, source_file text, imported_at timestamptz default now(),
  unique (account, voucher, row_hash));
create table product_profitability (id bigint generated always as identity primary key, item_name text not null,
  product_id bigint references products(id), report_date date, gross_bhd numeric, discount_pct numeric,
  net_amount_bhd numeric, cogs_bhd numeric, gross_profit_bhd numeric, gp_margin_pct numeric, misc_charges_bhd numeric,
  net_profit_bhd numeric, np_margin_pct numeric, source_file text, imported_at timestamptz default now(),
  unique (item_name, report_date));
create table selling_prices (id bigint generated always as identity primary key, item_name text, sku_code text,
  product_id bigint references products(id), customer_name text, customer_code text, warehouse_name text,
  warehouse_code text, price_book text, currency text, start_date date, end_date date, min_qty numeric, max_qty numeric,
  unit_name text, rate_bhd numeric, price_tiers jsonb, status text, narration text, source_file text,
  imported_at timestamptz default now(), voided_at timestamptz, void_reason text);
create unique index selling_prices_natural_key on selling_prices
  (sku_code, price_book, customer_code, warehouse_name, start_date) nulls not distinct;
create table ar_ageing (id bigint generated always as identity primary key, account text not null, account_code text,
  group_name text, balance_bhd numeric, bucket_0_30 numeric, bucket_31_60 numeric, bucket_61_90 numeric,
  bucket_91_120 numeric, bucket_121_150 numeric, bucket_151_180 numeric, bucket_181_210 numeric, bucket_over_210 numeric,
  total_bhd numeric, last_receipt_date date, as_of_date date, source_file text, imported_at timestamptz default now(),
  customer_id bigint, unique (account, as_of_date));
create table ar_ageing_totals (id bigint generated always as identity primary key, as_of_date date not null unique,
  focus_total_bhd numeric(12,3) not null, focus_over90_bhd numeric(12,3), rows_total_bhd numeric(12,3), source_file text,
  imported_at timestamptz not null default now());
create table stock_balance (id bigint generated always as identity primary key, item_name text not null,
  warehouse_name text, net_qty numeric, selling_rate_bhd numeric, total_value_bhd numeric, as_of_date date,
  source_file text, imported_at timestamptz default now(), unique (item_name, warehouse_name, as_of_date));
create table audit_log (id bigint generated always as identity primary key, ts timestamptz default now(),
  user_email text, event text, question text, sql_used text, detail jsonb);
create table ingest_runs (id bigint generated always as identity primary key, started_at timestamptz default now(),
  finished_at timestamptz, status text, file text, rows_in int, rows_loaded int, join_match_pct numeric, errors text);
"""


def _local() -> str | None:
    """The scratch cluster's address, or None (the test SKIPs, saying why). Only this PC: a database
    of any other host is never touched."""
    if not LOCAL_CLUSTER:
        print("    SKIP: YQ_LOCAL_PG_LOAD_TXN is not set (a scratch cluster on this PC)")
        return None
    try:
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
    except ImportError:
        print("    SKIP: psycopg is not installed")
        return None
    host = str(conninfo_to_dict(LOCAL_CLUSTER).get("host") or "")
    if host not in ("localhost", "127.0.0.1", "::1"):
        raise AssertionError(f"YQ_LOCAL_PG_LOAD_TXN must name a cluster on this PC, not {host!r}")
    try:
        psycopg.connect(LOCAL_CLUSTER, dbname="postgres", connect_timeout=3).close()
    except Exception as exc:  # noqa: BLE001
        print(f"    SKIP: the scratch cluster does not answer ({str(exc).splitlines()[0][:80]})")
        return None
    return LOCAL_CLUSTER


def _db(name: str) -> str:
    from psycopg.conninfo import make_conninfo
    return make_conninfo(LOCAL_CLUSTER, dbname=name)


def _fresh(*names: str) -> None:
    import psycopg
    for name in names:
        with psycopg.connect(LOCAL_CLUSTER, dbname="postgres", autocommit=True) as c:
            c.execute(f"drop database if exists {name} with (force)")
            c.execute(f"create database {name}")
        with psycopg.connect(_db(name), autocommit=True) as c:
            c.execute(DDL)


class _RestRequest:
    """One PostgREST request, as PostgREST v12 builds it, in a transaction of its own (autocommit).
    Written apart from scripts/load_txn.py, so that one mistake cannot sit on both sides of the parity."""

    def __init__(self, conn, table, tally):
        self.conn, self.t, self.tally = conn, table, tally
        self.op, self.cols, self.where, self.params, self.order_by = "select", "*", [], [], []
        self.lim = self.off = None
        self.neg, self.payload, self.on_conflict = False, None, ""

    def select(self, *columns, count=None, head=None):
        self.op, self.cols = "select", ",".join(columns) if columns else "*"
        return self

    def _f(self, col, op, value):
        self.where.append(f'"public"."{self.t}"."{col}" {op} %s')       # untyped text: the column's type decides
        self.params.append(str(value))
        return self

    def eq(self, c, v):
        return self._f(c, "=", v)

    def gte(self, c, v):
        return self._f(c, ">=", v)

    def lte(self, c, v):
        return self._f(c, "<=", v)

    def lt(self, c, v):
        return self._f(c, "<", v)

    def in_(self, col, values):
        self.where.append(f'"public"."{self.t}"."{col}" = ANY(%s)')
        self.params.append("{" + ",".join('"' + str(v) + '"' for v in values) + "}")
        return self

    def is_(self, col, value):
        neg, self.neg = self.neg, False
        self.where.append(f'"public"."{self.t}"."{col}" IS {"NOT " if neg else ""}NULL')
        return self

    @property
    def not_(self):
        self.neg = True
        return self

    def order(self, column, *, desc=False, nullsfirst=None, foreign_table=None):
        self.order_by.append(f'"public"."{self.t}"."{column}" {"DESC" if desc else "ASC"}')
        return self

    def limit(self, n, **k):
        self.lim = int(n)
        return self

    def range(self, a, b, foreign_table=None):
        self.off, self.lim = int(a), int(b) - int(a) + 1
        return self

    def delete(self, **k):
        self.op = "delete"
        return self

    def insert(self, json_, **k):
        self.op, self.payload = "insert", json_
        return self

    def update(self, json_, **k):
        self.op, self.payload = "update", json_
        return self

    def upsert(self, json_, *, on_conflict="", **k):
        self.op, self.payload, self.on_conflict = "upsert", json_, on_conflict
        return self

    def execute(self):
        self.tally[0] += 1
        cur, t = self.conn.cursor(), f'"public"."{self.t}"'
        w = (" WHERE " + " AND ".join(self.where)) if self.where else ""
        if self.op == "select":
            cols = "*" if self.cols in ("", "*") else ", ".join(f'{t}."{c.strip()}"' for c in self.cols.split(","))
            sql = f"SELECT {cols} FROM {t}{w}" + (" ORDER BY " + ", ".join(self.order_by) if self.order_by else "")
            sql += f" LIMIT {min(self.lim, 1000) if self.lim is not None else 1000}" + (f" OFFSET {self.off}" if self.off else "")
            cur.execute(f"SELECT coalesce(json_agg(_postgrest_t), '[]')::text FROM ({sql}) _postgrest_t", self.params)
            return types.SimpleNamespace(data=json.loads(cur.fetchone()[0]), count=None)
        if self.op == "delete":
            assert w, "safeupdate"
            cur.execute(f"DELETE FROM {t}{w}", self.params)
            return types.SimpleNamespace(data=[], count=cur.rowcount)
        body = json.dumps(self.payload if isinstance(self.payload, list) else [self.payload],
                          ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        rows = self.payload if isinstance(self.payload, list) else [self.payload]
        cols = list(dict.fromkeys(k for r in rows for k in r))
        # PostgREST v12 reads a body with json_to_recordset and the columns' types from its schema cache
        # (the client reads it with json_populate_record: two functions, so the parity compares the two)
        typed = dict(cur.execute("SELECT a.attname, format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
                                 "WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped",
                                 (f"public.{self.t}",)).fetchall())
        recordset = ("json_to_recordset(%s::json) AS pgrst_body("
                     + ", ".join(f'"{c}" {typed[c]}' for c in cols) + ")")
        if self.op == "update":
            assert w, "safeupdate"
            sets = ", ".join(f'"{c}" = pgrst_body."{c}"' for c in self.payload)
            cur.execute(f"UPDATE {t} SET {sets} FROM (SELECT * FROM {recordset}) pgrst_body{w}", [body] + self.params)
            return types.SimpleNamespace(data=[], count=cur.rowcount)
        sql = (f'INSERT INTO {t} ({", ".join(chr(34) + c + chr(34) for c in cols)}) SELECT '
               f'{", ".join("pgrst_body." + chr(34) + c + chr(34) for c in cols)} FROM {recordset}')
        if self.op == "upsert" and self.on_conflict:
            keys = [k.strip() for k in self.on_conflict.split(",")]
            sql += (f' ON CONFLICT ({", ".join(chr(34) + k + chr(34) for k in keys)}) DO UPDATE SET '
                    + ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in cols))
        cur.execute(sql, [body])
        return types.SimpleNamespace(data=[], count=cur.rowcount)


class _Rest:
    def __init__(self, conn):
        self.conn, self.tally = conn, [0]

    def table(self, name):
        return _RestRequest(self.conn, name, self.tally)


def _rows(conn, table: str, with_ids: bool) -> list[str]:
    cols = [r[0] for r in conn.execute("select column_name from information_schema.columns where table_schema = 'public' "
                                       "and table_name = %s order by ordinal_position", (table,)).fetchall()]
    keep = [c for c in cols if (c == "id" and with_ids) or c not in GENERATED]
    sel = ", ".join(f"({c} is not null)::text" if c in CLOCK_SET else f"{c}::text" for c in keep)
    # one text per row: the row literal tells NULL (an empty field) from '' (a quoted empty string)
    return sorted(r[0] for r in conn.execute(f"select (row({sel}))::text from {table}").fetchall())


def _differences(tables=FOCUS) -> list[str]:
    import psycopg
    out = []
    with psycopg.connect(_db(REST_DB)) as a, psycopg.connect(_db(ONE_DB)) as b:
        for t in tables:
            ra, rb = _rows(a, t, True), _rows(b, t, True)
            if ra != rb:
                out.append(f"{t}: {len(ra)} rows by REST, {len(rb)} by one transaction; "
                           f"e.g. REST only {sorted(set(ra) - set(rb))[:2]}, one only {sorted(set(rb) - set(ra))[:2]}")
    return out


def _both(staged: Path | None) -> tuple[str, str, int, int]:
    """The loader through REST, then through one transaction, on the same data/clean: (outputs, tallies)."""
    import psycopg
    argv = ["--staged", str(staged)] if staged is not None else []
    rest_conn = psycopg.connect(_db(REST_DB), autocommit=True)
    rest = _Rest(rest_conn)
    saved = ls._client
    ls._client = lambda: rest
    try:
        with contextlib.redirect_stdout(io.StringIO()) as out_a:
            assert ls.main(argv) == 0
    finally:
        ls._client = saved
        rest_conn.close()
    seen = {}
    real_open = load_txn.TxnClient.open
    load_txn.TxnClient.open = classmethod(lambda cls, *a, **k: seen.setdefault("c", real_open.__func__(cls, _db(ONE_DB))))
    os.environ[load_txn.ONE_TXN_ENV] = "1"
    try:
        with contextlib.redirect_stdout(io.StringIO()) as out_b:
            assert ls.main(argv) == 0
    finally:
        os.environ.pop(load_txn.ONE_TXN_ENV, None)
        load_txn.TxnClient.open = real_open
    return out_a.getvalue(), out_b.getvalue(), rest.tally[0], seen["c"].statements


def _same_output(a: str, b: str) -> bool:
    def norm(text):
        return [ln for ln in text.splitlines() if not ln.startswith("  (one transaction")]
    return norm(a) == norm(b)


# invented drops: three reps, eight items, two books
REPS = ["Rep R1", "Rep R2", "Rep R3"]
OCT = [f"2026-10-{d:02d}" for d in range(1, 13)]


def _tiers(rate: float) -> str:
    return json.dumps({f"val_{n}": (round(rate * (1 - n / 100.0), 3) if n <= 3 else None) for n in range(1, 14)})


def _book(book: str, src: str, skus: dict, layers=(None,)) -> list[dict]:
    rows = []
    for sku, rate in skus.items():
        for wh in layers:
            r = rate if wh is None else round(rate * 1.25, 3)
            rows.append({"item_name": f"Item {sku} invented", "sku_code": sku, "customer_name": None, "customer_code": None,
                         "warehouse_name": wh, "warehouse_code": None, "price_book": book, "currency": "BHD",
                         "start_date": "2026-01-01", "end_date": "2027-12-31" if sku.endswith("3") else None,
                         "min_qty": 1.0, "max_qty": None, "unit_name": "PCS", "rate_bhd": r, "price_tiers": _tiers(r),
                         "status": "Authorized", "narration": None, "source_file": src})
    return rows


def _sales(days, drop=frozenset(), skip_rep=None, relined=frozenset()):
    orders, lines, n = [], [], 0
    for d in days:
        for _k in range(4):
            n += 1
            rep = REPS[n % 3]
            if n in drop or rep == skip_rep:
                continue
            inv, gross = f"SI : {9000 + n}", round(1.1 * n + 0.35, 3)
            orders.append({"invoice_no": inv, "order_date": d, "customer_name": f"Shop {n % 7} invented", "gross_bhd": gross,
                           "salesman": rep, "payment_mode": "Cash" if n % 2 else "Credit",
                           "sales_account_name": "Cash - Sales" if n % 2 else "Credit - Sales", "source_file": "Register1.xlsx"})
            for ln in range(1, 3 + (1 if n in relined else 0)):
                lines.append({"invoice_no": inv, "line_no": ln, "line_date": d, "customer_account": f"Shop {n % 7} invented",
                              "item_name": f"Item S0{ln} invented", "quantity": float(ln), "rate_bhd": 0.5 + ln,
                              "gross_bhd": round(gross / 2, 3), "discount_bhd": None, "taxable_bhd": round(gross / 2.2, 3),
                              "vat_amount_bhd": 0.1 * ln, "total_amount_bhd": round(gross / 2, 3), "narration": None,
                              "warehouse_name": rep, "source_file": "Day_book1.xlsx"})
    return orders, lines


def _ledger(days, bad=False):
    rows = []
    for i, d in enumerate(days):
        for item in ("Item S01 invented", "Item S02 invented", "Item S03 invented"):
            rows.append({"item_name": item, "move_date": d, "voucher": f"SIO : {500 + i}", "received_qty": None,
                         "received_rate_bhd": None, "issued_qty": 2.0 + i, "issued_rate_bhd": 0.4, "balance_qty": 40.0 - i,
                         "received_value_bhd": None, "issued_value_bhd": 0.8, "balance_value_bhd": 16.0, "avg_rate_bhd": 0.4,
                         "warehouse_name": "Store invented", "to_warehouse_name": REPS[i % 3], "narration": None,
                         "voucher_type": "Stock Issue Voucher", "row_hash": f"h{item[5:8]}{d}{i}", "source_file": "Ledger1.xlsx"})
    if bad:
        rows[-1]["received_qty"] = "twelve"          # not a number: the ledger's insert fails after the sales are written
    return rows


def _stock(as_of, warehouses):
    rows = [{"item_name": f"Item S0{i} invented", "warehouse_name": w, "net_qty": "7", "selling_rate_bhd": "1.1",
             "total_value_bhd": "7.7", "as_of_date": as_of, "source_file": "Balance1.xlsx"}
            for w in warehouses for i in (1, 2, 3)]
    rows.append({"item_name": "Item S01 invented", "warehouse_name": warehouses[0], "net_qty": "3", "selling_rate_bhd": "1.3",
                 "total_value_bhd": "3.9", "as_of_date": as_of, "source_file": "Balance1.xlsx"})     # listed twice: summed
    return rows


def _ageing(as_of):
    rows = [{"account": f"Shop {i} invented", "account_code": f"A-{i}", "group_name": "Trade", "balance_bhd": 10.5 + i,
             "bucket_0_30": 5.0, "bucket_31_60": 5.5 + i, "bucket_61_90": None, "bucket_91_120": None, "bucket_121_150": None,
             "bucket_151_180": None, "bucket_181_210": None, "bucket_over_210": None, "total_bhd": 10.5 + i,
             "last_receipt_date": "2026-10-01", "as_of_date": as_of, "source_file": "Ageing1.xlsx"} for i in range(6)]
    rows.append(dict(rows[2], account_code="A-2b", balance_bhd=1.25, total_bhd=1.25))           # one name, two codes: summed
    return rows, [{"as_of_date": as_of, "focus_total_bhd": 81.25, "focus_over90_bhd": 0.0, "rows_total_bhd": 81.25,
                   "source_file": "Ageing1.xlsx"}]


def _profit(day, bump=0.0):
    rows = [{"item_name": f"Item S0{i} invented", "report_date": day, "gross_bhd": 12.5 * i + bump, "discount_pct": None,
             "net_amount_bhd": 11.0 * i, "cogs_bhd": 7.0 * i, "gross_profit_bhd": 4.0 * i, "gp_margin_pct": 36.36,
             "misc_charges_bhd": None, "net_profit_bhd": 4.0 * i, "np_margin_pct": 36.36, "source_file": "Profit1.xlsx"}
            for i in range(1, 9)]
    return rows + [dict(rows[0], gross_bhd=99.5)]                                               # listed twice: the last kept


def _cash(days):
    rows = [{"account": acct, "entry_date": d, "voucher": f"RV : {700 + i}", "counter_account": "Shop 1 invented",
             "debit_bhd": 15.0 + i, "credit_bhd": None, "balance_bhd": 100.0 + i, "currency": "BHD", "payment_mode": "Cash",
             "salesman": REPS[i % 3], "narration": None, "row_hash": f"le{acct[:4]}{d}{i}", "source_file": "Accounts1.xlsx"}
            for i, d in enumerate(days) for acct in ("Till invented", "Bank invented")]
    return rows + [dict(rows[0])]                                                               # an exact repeat: dropped


SKUS = {f"S{i:02d}": round(0.75 + i * 0.4, 3) for i in range(1, 13)}
LAYERS = (None, "Causeway", "YQ Roadshow")


def _steps():
    """(tag, {csv: rows}, staged file names, markers): each a load the loader must make the same way twice."""
    a_sp = _book("MA_base", "MASellingPriceBook _41_.xlsx", SKUS, LAYERS) + _book(
        "modern_trade", "ModernTradeSellerBook _7_.xlsx", {k: v for k, v in SKUS.items() if int(k[1:]) <= 5})
    a_sp.append(dict(a_sp[0], rate_bhd=9.99, end_date="2026-06-30"))                       # one key twice: the last kept
    o, l = _sales(OCT[:10])
    ag, tot = _ageing("2026-10-10")
    out = [("A first load", {"selling_prices": a_sp, "orders": o, "order_lines": l, "stock_movements": _ledger(OCT[:10]),
                             "stock_balance": _stock("2026-10-10", ["Store invented", "Van A", "Van B"]), "receivables": ag,
                             "receivables_totals": tot, "product_profitability": _profit("2026-10-09"),
                             "ledger_entries": _cash(OCT[:10])},
            ["MASellingPriceBook _41_.xlsx", "ModernTradeSellerBook _7_.xlsx", "Day_book1.xlsx"], ())]
    b = {k: v for k, v in SKUS.items() if k != "S12"}
    b.update(S05=4.4, S13=5.15)
    o, l = _sales(OCT[:11], drop={3}, relined={7})
    ag, tot = _ageing("2026-10-11")
    out.append(("B void, prune, re-line", {"selling_prices": _book("MA_base", "MASellingPriceBook _42_.xlsx", b, LAYERS),
                                           "orders": o, "order_lines": l, "stock_movements": _ledger(OCT[:11]),
                                           "stock_balance": _stock("2026-10-11", ["Store invented", "Van A", "Van B"]),
                                           "receivables": ag, "receivables_totals": tot,
                                           "product_profitability": _profit("2026-10-09", 1.5), "ledger_entries": _cash(OCT[:11])},
                ["MASellingPriceBook _42_.xlsx", "Day_book1.xlsx"], ()))
    c_skus = {k: v for k, v in SKUS.items() if int(k[1:]) > 6}
    o, l = _sales(OCT[:11], drop={3}, relined={7}, skip_rep="Rep R3")
    out.append(("C revive, mass-void guard, salesman guard, narrower stock",
                {"selling_prices": _book("MA_base", "MASellingPriceBook _43_.xlsx", c_skus, LAYERS), "orders": o,
                 "order_lines": l, "stock_balance": _stock("2026-10-12", ["Store invented", "Van A"])},
                ["MASellingPriceBook _43_.xlsx", "Day_book1.xlsx"], ()))
    d = {"orders": o, "order_lines": l, "stock_balance": _stock("2026-10-12", ["Store invented", "Van A"])}
    out.append(("D the same, replace and partial on purpose", d, ["Day_book1.xlsx"], ("REPLACE_OK", "PARTIAL_OK")))
    out.append(("E D once more", dict(d), ["Day_book1.xlsx"], ("REPLACE_OK", "PARTIAL_OK")))
    return out


@contextlib.contextmanager
def _drop(tables: dict, files: list[str], markers=()):
    """data/clean holding `tables` as CSVs, and a staged folder holding the file names (and markers)."""
    tmp = Path(tempfile.mkdtemp(prefix="loadtxn_"))
    clean, staged = tmp / "clean", tmp / "staged"
    clean.mkdir()
    staged.mkdir()
    for name, rows in tables.items():
        pd.DataFrame(rows).to_csv(clean / f"{name}.csv", index=False, encoding="utf-8")
    for f in list(files) + list(markers):
        (staged / f).write_bytes(b"")
    saved = ls.CLEAN
    ls.CLEAN = clean
    try:
        yield staged
    finally:
        ls.CLEAN = saved
        shutil.rmtree(tmp, ignore_errors=True)


@test("local: PARITY on invented drops -- REST and one transaction give the same rows, ids and loader output after every step")
def _():
    if not _local():
        return
    _fresh(REST_DB, ONE_DB)
    for tag, tables, files, markers in _steps():
        with _drop(tables, files, markers) as staged:
            out_a, out_b, requests, statements = _both(staged)
        diff = _differences()
        assert not diff, (tag, diff)
        assert _same_output(out_a, out_b), (tag, out_a[-1500:], out_b[-1500:])
        assert "(one transaction: committed, " in out_b, out_b[-600:]
        print(f"    {tag}: identical ({requests} REST requests, {statements} statements in one transaction)")
    import psycopg
    with psycopg.connect(_db(ONE_DB)) as c:
        events = dict(c.execute("select event, count(*) from audit_log group by 1").fetchall())
        tiers = c.execute("select distinct jsonb_typeof(price_tiers) from selling_prices").fetchall()
    # every path of the loader was taken: prune, the two guards, void, the mass-void guard, the narrower stock
    for event in ("orders.span_replace", "order_lines.span_replace_skipped", "selling_prices.void",
                  "selling_prices.void_skipped", "stock_balance.partial_snapshot"):
        assert events.get(event), (event, events)
    assert tiers == [("string",)], "a price book's tiers stay the JSON text PostgREST stores, not a parsed object"


@test("local: a load that fails half-way leaves nothing on the one transaction (REST kept the sales and lost the ledger's span)")
def _():
    if not _local():
        return
    import psycopg
    _fresh(REST_DB, ONE_DB)
    tag, tables, files, markers = _steps()[0]
    with _drop(tables, files, markers) as staged:
        _both(staged)
    with psycopg.connect(_db(ONE_DB)) as c:
        before = {t: _rows(c, t, True) for t in FOCUS}
    o, l = _sales(OCT[:12], drop={2})
    with _drop({"orders": o, "order_lines": l, "stock_movements": _ledger(OCT[:12], bad=True)}, files) as staged:
        rest_conn = psycopg.connect(_db(REST_DB), autocommit=True)
        saved = ls._client
        ls._client = lambda: _Rest(rest_conn)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                raises(Exception, lambda: ls.main(["--staged", str(staged)]))
        finally:
            ls._client = saved
            rest_conn.close()
        real_open = load_txn.TxnClient.open
        load_txn.TxnClient.open = classmethod(lambda cls, *a, **k: real_open.__func__(cls, _db(ONE_DB)))
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                exc = raises(Exception, lambda: ls.main(["--staged", str(staged), "--one-transaction"]))
        finally:
            load_txn.TxnClient.open = real_open
    assert "twelve" in str(exc) and "ROLLED BACK" in out.getvalue() and "nothing of this load was written" in out.getvalue()
    with psycopg.connect(_db(ONE_DB)) as c:
        assert {t: _rows(c, t, True) for t in FOCUS} == before, "the one transaction left part of a failed load behind"
    with psycopg.connect(_db(REST_DB)) as c:
        ledger_left = c.execute("select count(*) from stock_movements").fetchone()[0]
        orders_now = c.execute("select count(*) from orders").fetchone()[0]
    assert ledger_left == 0 and orders_now > len(before["orders"]), (ledger_left, orders_now)


@test("local: nothing is seen by another session before the commit; the commit waits out the interval it is told to avoid")
def _():
    if not _local():
        return
    import psycopg
    _fresh(ONE_DB)
    c = load_txn.TxnClient.open(_db(ONE_DB))
    watcher = psycopg.connect(_db(ONE_DB), autocommit=True)
    try:
        # the transaction's own limits (SET LOCAL: they end with it, whatever pooler hands out the backend)
        assert [c.fetch_one(f"show {g}") for g in ("statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout",
                                                   "work_mem")] == ["10min", "30s", "5min", "32MB"]
        assert c.fetch_one("show transaction_read_only") == "off" and c.started_at is not None
        c.bulk_upsert("customers", [{"name": f"Shop {i} invented"} for i in range(50)], "name")
        assert c.table("customers").select("name").execute().data[0]["name"] == "Shop 0 invented"   # its own writes: seen
        assert watcher.execute("select count(*) from customers").fetchone()[0] == 0
        seen_in_hold = []

        def sleep(s):
            seen_in_hold.append((round(s, 1), watcher.execute("select count(*) from customers").fetchone()[0]))
            time.sleep(min(s, 0.2))
        t0 = time.time()
        waited = c.commit([(int(t0) - 5, int(t0) + 3)], sleep=sleep, say=lambda _s: None)
        assert 2 <= waited <= 3.5 and seen_in_hold and seen_in_hold[0][1] == 0, (waited, seen_in_hold)
        assert watcher.execute("select count(*) from customers").fetchone()[0] == 50
    finally:
        c.close()
        watcher.close()


@test("local: a tolerated failure (a table that is not there) does not abort the one transaction; an untolerated one rolls it back")
def _():
    if not _local():
        return
    import psycopg
    _fresh(ONE_DB)
    with psycopg.connect(_db(ONE_DB), autocommit=True) as a:
        a.execute("drop table audit_log")
    c = load_txn.TxnClient.open(_db(ONE_DB))
    try:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            ls._audit(c, "x.y", "q", {"k": 1})                                    # guarded: says so and goes on
        assert "audit_log not written" in out.getvalue()
        c.bulk_upsert("customers", [{"name": "Shop A invented"}], "name")       # the transaction is still usable
        raises(Exception, lambda: c.bulk_upsert("customers", [{"name": "Shop B invented", "no_such_column": 1}], "name"))
        c.rollback()
    finally:
        c.close()
    with psycopg.connect(_db(ONE_DB)) as b:
        assert b.execute("select count(*) from customers").fetchone()[0] == 0


@test("local: a key the file repeats -- in a later slice the later row wins on both paths; inside one slice both stop, the one transaction leaving nothing")
def _():
    if not _local():
        return
    import psycopg
    _fresh(REST_DB, ONE_DB)

    def register(n: int, repeat_at: int) -> list[dict]:
        rows = [{"invoice_no": f"SI : {7000 + i}", "order_date": "2026-10-05", "customer_name": "Shop 1 invented",
                 "gross_bhd": 1.5 + i, "salesman": REPS[i % 3], "payment_mode": "Cash", "sales_account_name": "Cash - Sales",
                 "source_file": "Register1.xlsx"} for i in range(n)]
        rows.insert(repeat_at, dict(rows[4], gross_bhd=99.25))         # invoice SI : 7004 listed twice
        return rows

    with _drop({"orders": register(1001, 1001)}, ["Register1.xlsx"]) as staged:     # the repeat in the third slice
        _both(staged)
    assert not _differences(), _differences()
    for db in (REST_DB, ONE_DB):
        with psycopg.connect(_db(db)) as c:
            assert c.execute("select gross_bhd from orders where invoice_no = 'SI : 7004'").fetchone()[0] == 99.25, db
    with psycopg.connect(_db(ONE_DB)) as c:
        before = {t: _rows(c, t, True) for t in FOCUS}
    with _drop({"orders": register(1001, 10)}, ["Register1.xlsx"]) as staged:       # the repeat in the first slice
        rest_conn = psycopg.connect(_db(REST_DB), autocommit=True)
        saved = ls._client
        ls._client = lambda: _Rest(rest_conn)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                over_rest = raises(Exception, lambda: ls.main(["--staged", str(staged)]))
        finally:
            ls._client = saved
            rest_conn.close()
        real_open = load_txn.TxnClient.open
        load_txn.TxnClient.open = classmethod(lambda cls, *a, **k: real_open.__func__(cls, _db(ONE_DB)))
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                in_one = raises(Exception, lambda: ls.main(["--staged", str(staged), "--one-transaction"]))
        finally:
            load_txn.TxnClient.open = real_open
    for exc in (over_rest, in_one):
        assert "cannot affect row a second time" in str(exc), exc
    with psycopg.connect(_db(ONE_DB)) as c:
        assert {t: _rows(c, t, True) for t in FOCUS} == before


@test("local: a failure in the middle of a COPY rolls back to its savepoint; an aborted transaction is never committed")
def _():
    if not _local():
        return
    import psycopg
    _fresh(ONE_DB)
    c = load_txn.TxnClient.open(_db(ONE_DB))
    try:
        c.bulk_upsert("customers", [{"name": "Shop A invented"}], "name")
        with contextlib.suppress(Exception):
            with c.savepoint():                  # the second row cannot be written as JSON: the COPY stops half-way
                c.bulk_upsert("customers", [{"name": "Shop B invented"}, {"name": {"a", "set"}}], "name")
        c.bulk_upsert("customers", [{"name": "Shop C invented"}], "name")       # the transaction goes on
        assert c.commit([], sleep=lambda s: None, say=lambda s: None) == 0
    finally:
        c.close()
    with psycopg.connect(_db(ONE_DB)) as b:
        assert [r[0] for r in b.execute("select name from customers order by name")] == ["Shop A invented", "Shop C invented"]
    c = load_txn.TxnClient.open(_db(ONE_DB))
    try:
        c.bulk_upsert("customers", [{"name": "Shop D invented"}], "name")
        raises(Exception, lambda: c.run("select no_such_column from customers"))    # unguarded: the transaction aborts
        exc = raises(RuntimeError, lambda: c.commit([], sleep=lambda s: None, say=lambda s: None))
        assert "the transaction is INERROR" in str(exc) and c.commit_sent is False
    finally:
        c.rollback()
        c.close()
    with psycopg.connect(_db(ONE_DB)) as b:
        assert b.execute("select count(*) from customers where name = 'Shop D invented'").fetchone()[0] == 0


@test("opt-in: PARITY on real Focus drops (YQ_LOAD_TXN_DROPS), read in place, oldest first")
def _():
    if not REAL_DROPS:
        print("    SKIP: YQ_LOAD_TXN_DROPS is not set (real drop folders, separated by ';')")
        return
    if not _local():
        return
    from scripts import ingest
    _fresh(REST_DB, ONE_DB)
    tmp = Path(tempfile.mkdtemp(prefix="loadtxn_real_"))
    saved = (ingest.OUT_DIR, ls.CLEAN, list(sys.argv))
    try:
        for i, folder in enumerate(REAL_DROPS):
            folder = Path(folder.strip())
            clean = tmp / f"{i}"
            ingest.OUT_DIR = ls.CLEAN = clean
            sys.argv = ["ingest", str(folder)]
            with contextlib.redirect_stdout(io.StringIO()) as said:
                assert ingest.main() == 0, said.getvalue()[-600:]
            t0 = time.perf_counter()
            out_a, out_b, requests, statements = _both(folder)
            diff = _differences()
            assert not diff, (folder.name, diff)
            assert _same_output(out_a, out_b), folder.name
            print(f"    {folder.name}: identical ({requests} REST requests, {statements} statements; "
                  f"{time.perf_counter() - t0:.1f} s for both)")
    finally:
        ingest.OUT_DIR, ls.CLEAN, sys.argv = saved
        shutil.rmtree(tmp, ignore_errors=True)


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
    os.environ["DATABASE_URL"] = CI_DATABASE_URL
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
