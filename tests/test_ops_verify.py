"""verify_numbers through one read-only database session (load speed, release 2.4, 20-Oct-2026).

    python -m tests.test_ops_verify

Same lightweight runner as the other suites (no pytest). No database and no network: SUPABASE_URL, SUPABASE_KEY
and DATABASE_URL are pinned to ci.invalid before any import, psycopg.connect, the read RPC and the PostgREST client
are replaced by stand-ins, and every name and number is invented at run time.

One invented database is read three ways: as the read RPC answers it (yq_readonly: the views and the granted
tables, never orders or stock_movements, as on production), as PostgREST pages answer it (1000 rows a page, in the
unique order), and as the new session answers it (a stand-in connection that evaluates the session's own SQL).

Covered:
  * run_checks gives identical rows, check by check and figure by figure, through the session and through the
    REST/RPC path; the session run makes no REST or RPC call, the REST run pages orders and stock_movements;
  * whole quantities: the database's exact sum, no rows fetched; a fraction or NaN: the rows in id order, added as
    the page loop adds them; one row read serves the invoice total and the per-day invoice groups;
  * a session that fails mid-run hands that read and every later one to the REST/RPC path: still identical;
  * a relation the RPC cannot read raises as the RPC does, without a query; one the probe did not vouch for is read
    by the RPC itself;
  * the session opens read-only (read_only set before the first statement, transaction_read_only asserted) or not
    at all: no DATABASE_URL, the API host (RENDER), a refused connection, a session that is not read-only, a probe
    without the RPC functions, a database other than the one the RPC reads -- each leaves the REST/RPC path, and
    the connection is closed;
  * run_checks closes the session after the run, also when a check raises;
  * the RPC emulation uses the wrapper of scripts/security_migration.sql and binds $n as text;
  * the probe's verdicts per relation, on production's shape and on shapes the session cannot vouch for.
"""
from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import random
import re
import sys
import tempfile
import traceback
import types
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"
os.environ.pop("RENDER", None)
os.environ.pop("ENV", None)

import warnings  # noqa: E402
warnings.filterwarnings("ignore")

import psycopg  # noqa: E402

from app import db_read  # noqa: E402
from scripts import verify_numbers as vn  # noqa: E402

TESTS: list[tuple[str, object]] = []
MAX_ROWS = 1000                       # PostgREST's page size on this project, as the REST loops assume
MILLI = Decimal("0.001")


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


# ── the invented database ────────────────────────────────────────────────────────────────────────────

def _dsum(values) -> Decimal | int:
    """COALESCE(SUM(x), 0) over numeric values, as Postgres adds them (exactly; NULLs skipped)."""
    got = [v for v in values if v is not None]
    return sum(got[1:], got[0]) if got else 0


def _as_json(rows):
    """What json_agg + json.loads hand back: numeric as its own digits (non-finite ones quoted, as Postgres does)."""
    def enc(o):
        if isinstance(o, Decimal):
            return f"@@{o}@@" if o.is_finite() else str(o)
        raise TypeError(type(o))
    text = re.sub(r'"@@(.*?)@@"', r"\1", json.dumps(rows, default=enc))
    return json.loads(text)


def _match(val, op, want) -> bool:
    if val is None:
        return False
    if op == ">=":
        return val >= want
    if op == "<=":
        return val <= want
    if op == "<":
        return val < want
    if op == "=":
        return val == want
    if op == "in":
        return val in want
    if op == "ilike":
        return str(val).lower().startswith(str(want).lower().rstrip("%"))
    raise AssertionError(op)


class World:
    """One invented database. REFUSED is what the read RPC may not read, as on production."""
    REFUSED = {"orders", "stock_movements"}

    def __init__(self, seed: int = 20261020, n_lines: int = 1500, n_orders: int = 1300, n_moves: int = 2600):
        rnd = self.rnd = random.Random(seed)
        self.log: list[tuple[str, str]] = []
        self.days = [(date(2026, 9, 1) + timedelta(days=i)).isoformat() for i in range(30)]
        self.today = "2026-09-30"
        self.fingerprint = {"s": "postgres 1760000000.25", "tz": "UTC", "ds": "ISO, MDY", "efd": "0"}
        reps = [f"Route {i:02d}" for i in range(1, 7)] + [None]

        def ids(n):
            out = list(range(1, n + 1))
            rnd.shuffle(out)                       # ids never follow the dates: the order of the adds matters
            return out

        def money(lo, hi):
            return Decimal(rnd.randint(lo, hi)) * MILLI

        t: dict[str, list[dict]] = {}
        li = ids(n_lines)
        t["v_sales"] = []
        for i in range(n_lines):
            gross = money(100, 99999)
            t["v_sales"].append({"line_id": li[i], "sale_date": rnd.choice(self.days), "gross_bhd": gross,
                                 "revenue_bhd": (gross / Decimal("1.1")).quantize(MILLI), "salesman_raw": rnd.choice(reps)})
        oi = ids(n_orders)
        t["orders"] = [{"id": oi[i], "order_date": rnd.choice(self.days),
                        "gross_bhd": None if i % 401 == 7 else money(500, 250000)} for i in range(n_orders)]
        mi = ids(n_moves)
        t["stock_movements"] = [{"id": mi[i], "move_date": rnd.choice(self.days),
                                 "received_qty": None if i % 9 == 4 else Decimal(rnd.randint(0, 60)),
                                 "issued_qty": None if i % 11 == 3 else Decimal(rnd.randint(0, 40))} for i in range(n_moves)]
        whs = ["Main Store", "Van North", "Van South"]
        t["stock_balance"] = []
        n = 0
        for day, wset in (("2026-09-29", whs), ("2026-09-30", whs[:2])):     # the newest snapshot lost a warehouse
            for wh in wset:
                for k in range(40):
                    n += 1
                    t["stock_balance"].append({"id": n, "as_of_date": day, "warehouse_name": wh, "item_name": f"ITEM-{k:03d}",
                                               "total_value_bhd": money(0, 90000)})
        t["v_receivables"] = [{"outstanding_bhd": money(-5000, 400000)} for _ in range(60)]
        ar_sum = _dsum(r["outstanding_bhd"] for r in t["v_receivables"])
        t["ar_ageing_totals"] = [{"as_of_date": self.today, "focus_total_bhd": ar_sum - Decimal("12.345")}]
        t["v_price_list_by_book"] = [{"price_book": "MA_base", "sku_code": f"SKU-{k:02d}", "price_bhd": money(200, 9000)}
                                     for k in range(12)]
        t["selling_prices"] = [{"id": k + 1, "sku_code": f"SKU-{k:02d}", "price_book": "MA_base", "customer_code": None,
                                "warehouse_name": None, "start_date": "2026-03-01", "source_file": "MASellingPriceBook (9).xlsx",
                                "voided_at": None} for k in range(12)]
        t["selling_prices"].append({"id": 99, "sku_code": "SKU-OLD", "price_book": "MA_base", "customer_code": None,
                                    "warehouse_name": None, "start_date": "2026-01-05",
                                    "source_file": "MASellingPriceBook (3).xlsx", "voided_at": None})
        b2c = _dsum(r["revenue_bhd"] for r in t["v_sales"] if r["salesman_raw"] == "Route 01")
        b2b = _dsum(r["revenue_bhd"] for r in t["v_sales"] if r["salesman_raw"] != "Route 01")
        t["v_sales_by_channel"] = [{"channel": "B2B", "revenue_bhd": b2b}, {"channel": "B2C", "revenue_bhd": b2c}]
        self.t = t

    # what the report files say (parsed rows): mostly the database, with a few honest differences so that the
    # failing checks and their notes are compared too
    def files(self) -> dict:
        t = self.t
        ol = [{"line_date": r["sale_date"], "warehouse_name": r["salesman_raw"], "gross_bhd": float(r["gross_bhd"])}
              for r in sorted(t["v_sales"], key=lambda r: r["line_id"])]
        ol[5]["gross_bhd"] += 0.02                       # a day and a salesman off by two fils
        del ol[17]                                       # a line the file does not carry
        od = [{"order_date": r["order_date"], "gross_bhd": float(r["gross_bhd"] or 0)} for r in t["orders"]]
        sm = [{"move_date": r["move_date"], "received_qty": float(r["received_qty"] or 0),
               "issued_qty": float(r["issued_qty"] or 0)} for r in t["stock_movements"]]
        ar = [{"balance_bhd": float(r["outstanding_bhd"])} for r in t["v_receivables"]]
        sb = [{"item_name": r["item_name"], "warehouse_name": r["warehouse_name"], "as_of_date": r["as_of_date"],
               "total_value_bhd": float(r["total_value_bhd"])} for r in t["stock_balance"] if r["as_of_date"] == self.today]
        pb = [{"sku_code": r["sku_code"], "item_name": r["sku_code"], "price_book": "MA_base", "customer_code": None,
               "warehouse_name": None, "start_date": "2026-03-01", "end_date": None, "rate_bhd": float(r["price_bhd"]),
               "status": "Authorized", "source_file": "MASellingPriceBook (9).xlsx"} for r in t["v_price_list_by_book"]]
        pb[0]["rate_bhd"] += 0.5                         # one price the database does not show
        totals = {"as_of_date": self.today, "focus_total_bhd": float(t["ar_ageing_totals"][0]["focus_total_bhd"])}
        return {"ol": ol, "od": od, "sm": sm, "ar": ar, "sb": sb, "pb": pb, "totals": totals}

    # ── the read RPC (yq_readonly) ──
    def rpc(self, sql, params=None):
        self.log.append(("rpc", sql))
        for rel in re.findall(r"\bFROM\s+([a-z_]+)", sql, re.I):
            if rel in self.REFUSED:
                raise RuntimeError(f"permission denied for table {rel}")
        return _as_json(self.rpc_eval(sql, [str(p) for p in (params or [])]))

    def _span(self, rel, dc=None, lo=None, hi=None):
        rows = self.t[rel]
        return [r for r in rows if r.get(dc) is not None and lo <= r[dc] <= hi] if dc else rows

    def rpc_eval(self, sql: str, p: list[str]) -> list[dict]:
        """verify_numbers' own SQL, evaluated over the invented tables (the shapes it sends, nothing else)."""
        m = re.fullmatch(r"SELECT COALESCE\(SUM\((\w+)\),0\) AS s FROM (\w+)"
                         r"(?: WHERE (\w+) >= \$1::date AND \w+ <= \$2::date)?", sql)
        if m:
            col, rel, dc = m.groups()
            return [{"s": _dsum(r.get(col) for r in (self._span(rel, dc, p[0], p[1]) if dc else self.t[rel]))}]
        m = re.fullmatch(r"SELECT (?:(\w+)::text|COALESCE\((\w+), '\(none\)'\)) AS k, COALESCE\(SUM\((\w+)\),0\) AS s, "
                         r"COUNT\(\*\) AS n FROM (\w+) WHERE (\w+) >= \$1::date AND \w+ <= \$2::date GROUP BY 1", sql)
        if m:
            kdate, kname, col, rel, dc = m.groups()
            out: dict = {}
            for r in self._span(rel, dc, p[0], p[1]):
                k = r[kdate] if kdate else (r[kname] if r[kname] is not None else "(none)")
                out.setdefault(k, []).append(r.get(col))
            return [{"k": k, "s": _dsum(v), "n": len(v)} for k, v in out.items()]
        if sql == "SELECT MAX(as_of_date)::text AS d FROM stock_balance WHERE as_of_date < $1::date":
            got = [r["as_of_date"] for r in self.t["stock_balance"] if r["as_of_date"] < p[0]]
            return [{"d": max(got) if got else None}]
        if sql == ("SELECT DISTINCT COALESCE(warehouse_name, '(unassigned)') AS w FROM stock_balance "
                   "WHERE as_of_date = $1::date"):
            return [{"w": w} for w in sorted({r["warehouse_name"] or "(unassigned)"
                                              for r in self.t["stock_balance"] if r["as_of_date"] == p[0]})]
        m = re.fullmatch(r"SELECT (COUNT\(\*\)|COALESCE\(SUM\(total_value_bhd\), 0\)) AS s FROM stock_balance WHERE "
                         r"as_of_date = \$1::date AND COALESCE\(warehouse_name, '\(unassigned\)'\) IN "
                         r"\(SELECT jsonb_array_elements_text\(\$2::jsonb\)\)", sql)
        if m:
            whs = set(json.loads(p[1]))
            rows = [r for r in self.t["stock_balance"] if r["as_of_date"] == p[0] and (r["warehouse_name"] or "(unassigned)") in whs]
            return [{"s": len(rows) if m.group(1).startswith("COUNT") else _dsum(r["total_value_bhd"] for r in rows)}]
        if sql == "SELECT COALESCE(MAX(focus_total_bhd),0) AS s FROM ar_ageing_totals WHERE as_of_date = $1::date":
            got = [r["focus_total_bhd"] for r in self.t["ar_ageing_totals"] if r["as_of_date"] == p[0]]
            return [{"s": max(got) if got else 0}]
        if sql == "SELECT CURRENT_DATE::text AS d":
            return [{"d": self.today}]
        if sql == "SELECT sku_code, price_bhd FROM v_price_list_by_book WHERE price_book = $1":
            return [{"sku_code": r["sku_code"], "price_bhd": r["price_bhd"]} for r in self.t["v_price_list_by_book"]
                    if r["price_book"] == p[0]]
        m = re.fullmatch(r"SELECT sku_code, price_book, customer_code, warehouse_name, start_date::text AS start_date, "
                         r"source_file FROM selling_prices WHERE price_book = \$1 AND source_file ILIKE \$2"
                         r"( AND voided_at IS NULL)?", sql)
        if m:
            keys = ("sku_code", "price_book", "customer_code", "warehouse_name", "start_date", "source_file")
            return [{k: r[k] for k in keys} for r in self.t["selling_prices"]
                    if r["price_book"] == p[0] and _match(r["source_file"], "ilike", p[1])
                    and (not m.group(1) or r["voided_at"] is None)]
        if sql == vn._FINGERPRINT_SQL:
            return [dict(self.fingerprint)]
        raise AssertionError("the stand-in RPC does not know this SQL: " + sql)

    # ── the session (a stand-in connection evaluates the session's own SQL) ──
    def probe_rows(self) -> list[dict]:
        return [{"rel": rel, "present": True, "rpc_ok": True, "rpc_all": rel not in self.REFUSED,
                 "rpc_refused": rel in self.REFUSED, "rest_all": True, "mine_all": True} for rel in vn._RELATIONS]

    def pg_eval(self, sql: str, params: list, conn) -> list[dict] | None:
        s = " ".join(sql.split())
        if s.lower().startswith("set "):
            return None
        if s == "show transaction_read_only":
            return [{"transaction_read_only": conn.ro_state}]
        if s == " ".join(vn._PROBE_SQL.split()):
            assert params == [list(vn._RELATIONS), vn._RPC_SOURCE], params
            return conn.probe
        if s == " ".join(vn._FINGERPRINT_SQL.split()):
            return [dict(conn.fingerprint)]
        m = re.fullmatch(r"SELECT COUNT\(\*\) AS n, SUM\((\w+)\) AS s, COALESCE\(bool_and\(\1 = trunc\(\1\) AND \1 <> 'NaN'\), "
                         r"true\) AND COALESCE\(SUM\(abs\(\1\)\), 0\) < 9007199254740992 AS exact FROM (\w+)"
                         r"(?: WHERE (\w+) >= %s AND \3 <= %s)?", s)
        if m:
            col, rel, dc = m.groups()
            rows = self._span(rel, dc, *params) if dc else self.t[rel]
            vals = [r.get(col) for r in rows if r.get(col) is not None]
            exact = all(not v.is_nan() and v == v.to_integral_value() for v in vals) and sum(abs(v) for v in vals) < 2 ** 53
            return [{"n": len(rows), "s": _dsum(vals) if vals else None, "exact": exact}]
        m = re.fullmatch(r"SELECT COALESCE\(json_agg\(t(?: ORDER BY t\.(\w+))?\), '\[\]'::json\) FROM "
                         r"\(SELECT ([\w, ]+) FROM (\w+)(?: WHERE (\w+) >= %s AND \4 <= %s)?\) t", s)
        if m:                                             # the REST rows: plain columns, the unique order
            order, cols, rel, dc = m.groups()
            rows = self._span(rel, dc, *params) if dc else list(self.t[rel])
            if order:
                rows = sorted(rows, key=lambda r: r[order])
            names = [c.strip() for c in cols.split(",")]
            return [{"coalesce": _as_json([{c: r.get(c) for c in names} for r in rows])}]
        m = re.fullmatch(r"SELECT COALESCE\(json_agg\(t\), '\[\]'::json\) FROM \((.*)\) t", s)
        if m:                                             # the RPC's wrapper around verify_numbers' own SQL
            inner, n = m.group(1), iter(range(1, 99))
            inner = re.sub(r"%s::text", lambda _m: f"${next(n)}", inner).replace("%%", "%")
            return [{"coalesce": _as_json(self.rpc_eval(inner, [str(x) for x in params]))}]
        raise AssertionError("the stand-in session does not know this SQL: " + s)


class FakeRest:
    """PostgREST as the REST loops use it: filters, one unique order, pages of at most MAX_ROWS rows."""

    def __init__(self, world: World):
        self.w = world

    def table(self, name):
        return _Q(self.w, name)


class _Q:
    def __init__(self, world, name):
        self.w, self.name = world, name
        self.cols, self.filters, self.ord, self.lo, self.hi, self.lim = [], [], None, None, None, None

    def select(self, cols, **_):
        self.cols = [c.strip() for c in cols.split(",")]
        return self

    def order(self, col, desc=False):
        self.ord = (col, desc)
        return self

    def _f(self, col, op, val):
        self.filters.append((col, op, val))
        return self

    def gte(self, c, v):
        return self._f(c, ">=", v)

    def lte(self, c, v):
        return self._f(c, "<=", v)

    def lt(self, c, v):
        return self._f(c, "<", v)

    def eq(self, c, v):
        return self._f(c, "=", v)

    def in_(self, c, vals):
        return self._f(c, "in", list(vals))

    def ilike(self, c, v):
        return self._f(c, "ilike", v)

    def range(self, a, b):
        self.lo, self.hi = a, b
        return self

    def limit(self, n):
        self.lim = n
        return self

    def execute(self):
        self.w.log.append(("rest", self.name))
        rows = self.w.t[self.name]
        for c, op, v in self.filters:
            rows = [r for r in rows if _match(r.get(c), op, v)]
        if self.ord:
            rows = sorted(rows, key=lambda r: r[self.ord[0]], reverse=self.ord[1])
        start = self.lo or 0
        n = (self.hi - start + 1) if self.hi is not None else (self.lim or MAX_ROWS)
        rows = rows[start:start + min(n, MAX_ROWS)]
        return types.SimpleNamespace(data=_as_json([{c: r.get(c) for c in self.cols} for r in rows]))


class FakeConn:
    """A psycopg connection over the invented database. read_only must be set before any statement."""

    def __init__(self, world: World, ro_state: str = "on", fail_after: int | None = None, probe=None, fingerprint=None):
        self.w, self.ro_state, self.fail_after = world, ro_state, fail_after
        self.probe = world.probe_rows() if probe is None else probe
        self.fingerprint = dict(world.fingerprint if fingerprint is None else fingerprint)
        self.statements: list[str] = []
        self._ro = False
        self.closed = False
        self.rolled_back = 0

    @property
    def read_only(self):
        return self._ro

    @read_only.setter
    def read_only(self, value):
        assert not self.statements, "read_only was set after a statement had run"
        self._ro = value

    def cursor(self, row_factory=None):
        return _Cur(self)

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed = True


class _Cur:
    def __init__(self, conn: FakeConn):
        self.c, self.rows, self.description = conn, None, None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        c = self.c
        if c.closed:
            raise psycopg.OperationalError("the connection is closed")
        c.statements.append(sql)
        c.w.log.append(("pg", sql))
        if c.fail_after is not None and len(c.statements) > c.fail_after:
            raise psycopg.OperationalError("server closed the connection unexpectedly")
        assert c.read_only or sql.lower().startswith("show"), "a statement ran before the session was read-only"
        self.rows = c.w.pg_eval(sql, list(params or []), c)
        self.description = None if self.rows is None else [("col",)]

    def fetchall(self):
        return list(self.rows or [])

    def fetchone(self):
        return (self.rows or [None])[0]


@contextlib.contextmanager
def swap(obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


@contextlib.contextmanager
def files_and_rest(world: World):
    """run_checks over the invented reports, with the REST/RPC path reading the invented database."""
    f = world.files()
    found = {"sales_day_book": "Sales_day_book1.xlsx", "summary_sales_register": "Summary_sales_register1.xlsx",
             "stock_ledger": "Stock_ledger1.xlsx", "customer_summary_ageing_by_due_date": "Customer_summary_ageing1.xlsx",
             "stock_balance_by_warehouse": "Stock_balance_by_warehouse1.xlsx"}
    stack = contextlib.ExitStack()
    with stack:
        for name, value in (
                ("_find", lambda key, src: found.get(key)),
                ("_find_newest_book", lambda key, src: "MASellingPriceBook (9).xlsx" if key == "masellingpricebook" else None),
                ("export_date", lambda path: "2026-09-28"),
                ("read_grid", lambda path: path),
                ("parse_order_lines", lambda grid, src: [dict(r) for r in f["ol"]]),
                ("parse_orders", lambda grid, src: [dict(r) for r in f["od"]]),
                ("parse_stock", lambda grid, src: [dict(r) for r in f["sm"]]),
                ("parse_receivables", lambda grid, src: [dict(r) for r in f["ar"]]),
                ("parse_receivables_totals", lambda grid, src: dict(f["totals"])),
                ("parse_stock_balance", lambda grid, src: [dict(r) for r in f["sb"]]),
                ("parse_pricebook", lambda grid, src, book: [dict(r) for r in f["pb"]]),
                ("get_client", lambda: FakeRest(world))):
            stack.enter_context(swap(vn, name, value))
        stack.enter_context(swap(db_read, "exec_sql", lambda sql: world.rpc(sql)))
        stack.enter_context(swap(db_read, "exec_sql_params", lambda sql, params: world.rpc(sql, params)))
        with tempfile.TemporaryDirectory() as d:
            yield Path(d)


def session(conn: FakeConn, probe=None):
    """A session over `conn` as _Session.open() leaves it: read-only before the first statement."""
    conn.read_only = True
    return vn._Session(conn, conn.probe if probe is None else probe)


def session_on(conn: FakeConn):
    """run_checks' session opener, answering with a session over `conn` (production's probe)."""
    return swap(vn, "_open_session", lambda: session(conn))


def span_filters(span):
    dc, lo, hi = span
    return [lambda q: q.gte(dc, lo), lambda q: q.lte(dc, hi)]


# ── 1. identical results ─────────────────────────────────────────────────────────────────────────────

@test("run_checks: the session and the REST/RPC path give identical rows on the same data, figure by figure")
def _():
    w = World()
    with files_and_rest(w) as src:
        rest = vn.run_checks(src, session=False)
        rest_log, w.log[:] = list(w.log), []
        conn = FakeConn(w)
        with session_on(conn):
            ses = vn.run_checks(src)
        ses_log = list(w.log)
    assert repr(ses) == repr(rest), "\n".join(f"{a}\n{b}" for a, b in zip(ses[1], rest[1]) if repr(a) != repr(b))
    assert not [x for x in ses_log if x[0] != "pg"], "the session run made a REST or RPC call"
    sent = [q for _, q in ses_log]                      # the read plan: the REST reads where the RPC is refused
    assert [re.search(r"AS exact FROM (\w+)", q).group(1) for q in sent if "AS exact FROM" in q] == \
        ["orders", "stock_movements", "stock_movements"]
    rows_reads = [m.group(1) for q in sent if "%s::text" not in q             # the RPC binds every $n as text
                  for m in [re.search(r"\) FROM \(SELECT [\w, ]+ FROM (\w+)", q)] if m]
    assert rows_reads == ["orders", "v_sales_by_channel"], ("one rows read for the invoices (money is not exact), "
                                                           "one for the channels", rows_reads)
    assert not [q for q in sent if re.search(r"FROM \((?!SELECT [\w, ]+ FROM).*\bFROM (orders|stock_movements)\b", q)]
    assert sum(1 for x in rest_log if x == ("rest", "stock_movements")) == 6, "2 sums x 3 pages of the ledger"
    assert sum(1 for x in rest_log if x == ("rest", "orders")) == 4, "the invoice total and the day groups, 2 pages each"
    assert conn.closed and conn.rolled_back == 1, "the read-only transaction is rolled back and closed after the run"
    ok, rows = ses
    by = {r["metric"].split(" (")[0].split(":")[0]: r for r in rows}
    families = ("Sales gross BHD", "Invoice headers gross BHD", "Sales per day", "Sales per salesman", "Invoices per day",
                "Stock movements units", "Receivables BHD", "Channel = sales gross")
    assert all(any(m.startswith(f) for m in by) for f in families), sorted(by)
    assert any(r["metric"].startswith("Stock snapshot 2026-09-30: previous warehouses missing") and not r["passed"] for r in rows)
    assert any(r["metric"].startswith("MA_base price = file") and not r["passed"] for r in rows)
    assert any(r["metric"] == "MA_base live rows not in file (older exports)" and r["db"] == 1.0 for r in rows)
    assert any(r["metric"].startswith("Receivables Focus total 2026-09-30 stored") and "gap" in r.get("note", "") for r in rows)
    assert not ok and any("days off" in r.get("note", "") for r in rows) and any("salesmen off" in r.get("note", "") for r in rows)


@test("stock_movements: whole quantities take the exact database sum (no rows fetched); a fraction or NaN fetches the "
      "rows in id order and adds them as the page loop does")
def _():
    w = World()
    conn = FakeConn(w)
    s = session(conn)
    span = ("move_date", w.days[2], w.days[24])
    with swap(vn, "get_client", lambda: FakeRest(w)):
        for col in ("received_qty", "issued_qty"):
            want = vn._rest_sum("stock_movements", col, filters=span_filters(span))
            before = len(conn.statements)
            got = s.rest_sum("stock_movements", col, span)
            assert repr(got) == repr(want), (got, want)
            assert len(conn.statements) == before + 1, "whole numbers: the aggregate alone, no rows"
        for r in w.t["stock_movements"][::5]:              # tenths: floating point no longer adds them exactly
            r["received_qty"] = Decimal(w.rnd.randint(1, 99)) / 10
        w.t["stock_movements"][3]["issued_qty"] = Decimal("NaN")   # the page loop reads NaN as 0
        for col in ("received_qty", "issued_qty"):
            want = vn._rest_sum("stock_movements", col, filters=span_filters(span))
            before = len(conn.statements)
            got = s.rest_sum("stock_movements", col, span)
            assert repr(got) == repr(want), (col, got, want)
            assert len(conn.statements) == before + 2, "not exact: the aggregate, then the rows in id order"
        empty = ("move_date", "2027-01-01", "2027-01-31")
        assert repr(s.rest_sum("stock_movements", "received_qty", empty)) == \
            repr(vn._rest_sum("stock_movements", "received_qty", filters=span_filters(empty))) == "0"
        for r in w.t["stock_movements"]:
            r["issued_qty"] = None                         # every value NULL: 0.0, as the loop's 0.0 + 0.0 ...
        assert repr(s.rest_sum("stock_movements", "issued_qty", span)) == \
            repr(vn._rest_sum("stock_movements", "issued_qty", filters=span_filters(span))) == "0.0"


@test("orders: one row read serves the invoice total and the per-day groups; both equal the page loops")
def _():
    w = World()
    conn = FakeConn(w)
    s = session(conn)
    span = ("order_date", w.days[0], w.days[-1])
    sql = ("SELECT order_date::text AS k, COALESCE(SUM(gross_bhd),0) AS s, COUNT(*) AS n FROM orders "
           "WHERE order_date >= $1::date AND order_date <= $2::date GROUP BY 1")
    with swap(vn, "get_client", lambda: FakeRest(w)), swap(db_read, "exec_sql_params", lambda q, p: w.rpc(q, p)):
        want_sum = vn._rest_sum("orders", "gross_bhd", filters=span_filters(span))
        want_groups = vn._db_groups(sql, [span[1], span[2]], rest=("orders", "order_date", "gross_bhd", "order_date"))
    assert ("rpc", sql) in w.log, "the RPC was asked first and refused, as on production"
    before = len(conn.statements)
    got_sum = s.rest_sum("orders", "gross_bhd", span)
    got_groups = s.rest_groups("orders", "order_date", "gross_bhd", span)
    assert repr(got_sum) == repr(want_sum) and repr(sorted(got_groups.items())) == repr(sorted(want_groups.items()))
    assert len(conn.statements) == before + 2, "the aggregate (not exact: money) and ONE rows read for both"
    assert len(want_groups) == 30 and sum(n for _, n in want_groups.values()) == len(w.t["orders"])


# ── 2. fallbacks ─────────────────────────────────────────────────────────────────────────────────────

@test("a session that fails mid-run hands that read and every later one to the REST/RPC path: the rows stay identical")
def _():
    w = World()
    with files_and_rest(w) as src:
        rest = vn.run_checks(src, session=False)
        for k in (0, 1, 4, 7, 11):
            w.log[:] = []
            conn = FakeConn(w, fail_after=k)
            with session_on(conn):
                ses = vn.run_checks(src)
            assert repr(ses) == repr(rest), k
            assert conn.closed, k
            first_old = next(i for i, x in enumerate(w.log) if x[0] != "pg")
            assert all(x[0] != "pg" for x in w.log[first_old:]), "nothing goes back to a failed session"
            assert sum(1 for x in w.log if x[0] == "pg") == k + 1, (k, "the failed statement is the last one sent")


@test("a relation the RPC cannot read raises as the RPC does, without a query; one the probe did not vouch for is "
      "read by the RPC itself")
def _():
    w = World()
    conn = FakeConn(w)
    s = session(conn, [r for r in conn.probe if r["rel"] != "ar_ageing_totals"])
    try:
        s.rpc("SELECT COALESCE(SUM(gross_bhd),0) AS s FROM orders", None)
        raise AssertionError("orders must be refused")
    except RuntimeError as e:
        assert "permission denied for table orders" in str(e)
    assert conn.statements == [], "the refusal needs no round trip"
    ar_sql = "SELECT COALESCE(MAX(focus_total_bhd),0) AS s FROM ar_ageing_totals WHERE as_of_date = $1::date"
    try:
        s.rpc(ar_sql, [w.today])
        raise AssertionError("a relation outside the probe must go to the RPC itself")
    except vn._Fallback:
        pass
    token = vn._SESSION.set(s)
    try:
        with swap(db_read, "exec_sql_params", lambda q, p: w.rpc(q, p)), swap(db_read, "exec_sql", lambda q: w.rpc(q)):
            assert vn._sql_sum(ar_sql, [w.today]) == float(w.t["ar_ageing_totals"][0]["focus_total_bhd"])
            assert ("rpc", ar_sql) in w.log
            assert vn._sql_sum("SELECT COALESCE(SUM(gross_bhd),0) AS s FROM orders", None) is None   # as the RPC: None
            assert vn._db_today() == w.today and conn.statements, "CURRENT_DATE through the session"
    finally:
        vn._SESSION.reset(token)
    for k in ("present", "rpc_ok", "rpc_all", "mine_all"):        # each one alone sends v_sales to the RPC itself
        broken = [dict(r, **{k: False}) if r["rel"] == "v_sales" else r for r in conn.probe]
        t = session(FakeConn(w), broken)
        assert "v_sales" not in t.rpc_reads | t.rpc_refuses, k


@test("the session opens read-only or not at all; every refusal leaves the REST/RPC path and closes the connection")
def _():
    w = World()
    seen: list[dict] = []

    def opener(conn):
        def connect(dsn, **kw):
            seen.append(dict(kw, dsn=dsn))
            return conn
        return connect

    with swap(db_read, "exec_sql", lambda q: w.rpc(q)), swap(db_read, "exec_sql_params", lambda q, p: w.rpc(q, p)):
        conn = FakeConn(w)
        with swap(psycopg, "connect", opener(conn)):
            s = vn._open_session()
        assert isinstance(s, vn._Session) and conn.read_only is True and not conn.closed
        assert seen[-1]["autocommit"] is False and seen[-1]["dsn"] == os.environ["DATABASE_URL"]
        assert seen[-1]["connect_timeout"] <= 10
        assert conn.statements[:3] == ["set statement_timeout = '8s'", "set idle_in_transaction_session_timeout = '300s'",
                                       "show transaction_read_only"], conn.statements
        assert s.rpc_reads == set(vn._RELATIONS) - World.REFUSED and s.rpc_refuses == World.REFUSED
        assert ("rpc", vn._FINGERPRINT_SQL) in w.log, "the RPC was asked which database it reads"
        s.close()
        assert conn.closed and conn.rolled_back == 1
        cases = {
            "not read-only": FakeConn(w, ro_state="off"),
            "no RPC functions": FakeConn(w, probe=[dict(r, rpc_ok=False) for r in w.probe_rows()]),
            "another database": FakeConn(w, fingerprint=dict(w.fingerprint, s="postgres 1111.5")),
            "other settings": FakeConn(w, fingerprint=dict(w.fingerprint, tz="Asia/Bahrain")),
            "fails at once": FakeConn(w, fail_after=0),
        }
        for why, c in cases.items():
            with swap(psycopg, "connect", opener(c)):
                assert vn._open_session() is None, why
            assert c.closed, why

        def refused(dsn, **kw):
            raise psycopg.OperationalError('connection to server at "pooler.ci.invalid" (192.0.2.7), port 5432 failed: '
                                           'FATAL: password authentication failed for user "postgres.abcdef"')
        said = io.StringIO()
        handler = logging.StreamHandler(said)
        vn.log.addHandler(handler)
        try:
            with swap(psycopg, "connect", refused):
                assert vn._open_session() is None
        finally:
            vn.log.removeHandler(handler)
        assert "OperationalError" in said.getvalue(), said.getvalue()
        assert not re.search(r"pooler|192\.0\.2|postgres\.abc", said.getvalue()), "the database address is never logged"
        calls = len(seen)
        with swap(psycopg, "connect", opener(FakeConn(w))):
            os.environ["RENDER"] = "true"                  # the API host keeps the REST/RPC path
            try:
                assert vn._open_session() is None
            finally:
                os.environ.pop("RENDER", None)
            saved, os.environ["DATABASE_URL"] = os.environ["DATABASE_URL"], ""
            try:
                assert vn._open_session() is None
            finally:
                os.environ["DATABASE_URL"] = saved
        assert len(seen) == calls, "no connection was even attempted"


@test("run_checks closes the session after the run, also when a check raises; no session outlives the run")
def _():
    w = World()
    with files_and_rest(w) as src:
        conn = FakeConn(w)

        def boom(grid, src_name):
            raise ValueError("a report that does not parse")
        with session_on(conn), swap(vn, "parse_orders", boom):
            try:
                vn.run_checks(src)
                raise AssertionError("the parse error must reach the caller, as before")
            except ValueError:
                pass
        assert conn.closed and conn.rolled_back == 1 and vn._SESSION.get() is None
        w.log[:] = []
        with swap(vn, "_open_session", lambda: (_ for _ in ()).throw(AssertionError("session=False opens nothing"))):
            vn.run_checks(src, session=False)
        assert w.log and not [x for x in w.log if x[0] == "pg"]


# ── 3. the contract with the RPC and the probe ───────────────────────────────────────────────────────

@test("the RPC emulation: scripts/security_migration.sql's wrapper, $n bound as text, a literal % doubled for psycopg")
def _():
    mig = (ROOT / "scripts" / "security_migration.sql").read_text(encoding="utf-8")
    assert mig.count(vn._RPC_SOURCE) == 2, "both read RPCs wrap the SQL exactly so"
    assert vn._RPC_SOURCE == "'" + vn._RPC_WRAPPER.replace("'", "''") + "'"
    assert vn._pg_sql("SELECT a FROM b WHERE d >= $1::date AND e = $2 AND f LIKE 'x%'", ["2026-09-01", 5]) == \
        ("SELECT a FROM b WHERE d >= %s::text::date AND e = %s::text AND f LIKE 'x%%'", ["2026-09-01", "5"])
    assert vn._pg_sql("SELECT CURRENT_DATE::text AS d", None) == ("SELECT CURRENT_DATE::text AS d", [])
    assert "%" not in vn._PROBE_SQL.replace("%s", ""), "the probe's only placeholders are its two parameters"
    assert vn._PROBE_SQL.count("%s") == 2


@test("the probe's verdicts: production's shape reads views and granted tables through the RPC, orders and "
      "stock_movements over REST; anything it cannot vouch for goes the old way")
def _():
    w = World()
    s = session(FakeConn(w), w.probe_rows())
    assert s.rpc_reads == {"v_sales", "stock_balance", "v_receivables", "ar_ageing_totals", "v_price_list_by_book",
                           "selling_prices", "v_sales_by_channel"}
    assert s.rpc_refuses == {"orders", "stock_movements"} and s.rest_reads == set(vn._RELATIONS)
    # the RPC's answer cannot be vouched for (granted, but row security may hide rows): the RPC itself is asked;
    # the REST rows stay the session's, since they are read only after that RPC has really failed
    rows = [dict(r, rpc_all=False, rpc_refused=False) if r["rel"] in ("orders", "v_sales") else r for r in w.probe_rows()]
    t = session(FakeConn(w), rows)
    assert not {"orders", "v_sales"} & (t.rpc_reads | t.rpc_refuses) and {"orders", "v_sales"} <= t.rest_reads
    for change in ({"present": False}, {"mine_all": False}, {"rest_all": False}):
        rows = [dict(r, **change) if r["rel"] in ("orders", "v_sales") else r for r in w.probe_rows()]
        t = session(FakeConn(w), rows)
        assert not {"orders", "v_sales"} & t.rest_reads, change
        if "present" in change:
            assert not {"orders", "v_sales"} & (t.rpc_reads | t.rpc_refuses), change
        if "mine_all" in change:                        # the refusal is yq_readonly's alone: it still stands
            assert "v_sales" not in t.rpc_reads and "orders" in t.rpc_refuses, change
        try:
            t.rest_sum("orders", "gross_bhd", ("order_date", w.days[0], w.days[1]))
            raise AssertionError(change)
        except vn._Fallback:
            pass


@test("CI runs this suite; repo-clean allows the files of this change; the load's callers take the session")
def _():
    from scripts.ops import selftest
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    loop = next(line for line in ci.splitlines() if line.strip().startswith("for suite in"))
    assert re.search(r"\bverify\b", loop), loop
    for rel in ("scripts/verify_numbers.py", "tests/test_ops_verify.py", "scripts/ops/selftest.py", ".github/workflows/ci.yml"):
        assert any(rel == p or rel.startswith(p) for p in selftest.ALLOWED_PATHS), rel
    for caller in ("refresh.py", "reconcile_check.py"):          # both run inside every load
        text = (ROOT / "scripts" / caller).read_text(encoding="utf-8")
        assert "run_checks(" in text and "session=False" not in text, caller


def main() -> int:
    passed = failed = 0
    for name, fn in TESTS:
        try:
            fn()
            passed += 1
            print(f"PASS  {name}")
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
