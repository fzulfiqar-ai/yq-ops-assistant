"""The load's gate snapshot (scripts/prod_gate.py): a dozen statements, the same JSON.

    python -m tests.test_ops_gate

The load takes a gate snapshot before and after it writes (pre_gate / post_gate in scripts/yq_load_go.py). The
snapshot used to send three or four statements for every table and one for every foreign key, one after another:
343 round trips and about 37 s on production (20-Oct-2026). It is now 12 statements inside the same read-only
REPEATABLE READ transaction (about 2.5 s there), and its JSON is what the old code wrote, so `prod_gate compare`
reads an old file beside a new one exactly as before.

Same lightweight runner as the other suites (no pytest). The addresses are pinned to ci.invalid before any import,
and the gate's connection is replaced by a stand-in that answers every statement, old and new, from one invented
database held in memory (no psycopg, no network). Every name and number is invented, built at run time.

Covered:
  * the reference is the old algorithm, word for word (old_snapshot below);
  * the statement count: 12 on a database of production's shape (94 tables, 78 with an integer id, 62 foreign
    keys), where the old algorithm sends 343, as it did on production; 11 without a foreign key;
  * the JSON, byte for byte, on the same database: integer, text, numeric and uuid ids, an empty table, a table
    without an id, quoted table and column names, orphan lines and events, two foreign keys of one name (the later
    count stays, as before), a self-reference, the latest stock and receivables, the price books and the order
    statuses in their order; the stand-in hands the rows of a UNION ALL back in another order, as a parallel plan
    may; an empty database (every table there, no row) too;
  * `snapshot` prints the same line and writes the same file; `compare` of an old file with a new one of the same
    database is PASS with nothing changed, and across a load (a new order, a status moved with its event, more sales
    lines, newer stock, a lost line) old -> new reads exactly as old -> old;
  * names from the catalog are quoted as identifiers: an embedded double quote is doubled;
  * the session is checked read-only first, every statement runs inside the one transaction, and a read-write
    session is refused before anything is read;
  * CI runs this suite;
  * local: on a throwaway Postgres (YQ_LOCAL_PG_GATE, a loopback address with an empty database; SKIP without it,
    as in CI) the old and the new snapshot inside ONE read-only REPEATABLE READ transaction are identical.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import random
import re
import sys
import tempfile
import traceback
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"
os.environ.pop("YQ_MAIN_CHECKOUT", None)

from scripts import prod_gate as gate  # noqa: E402

TESTS: list[tuple[str, object]] = []
LOCAL_ENV = "YQ_LOCAL_PG_GATE"


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


# ── the reference: snapshot() as it was until 20-Oct-2026, word for word but for the connection it is handed ──

def old_snapshot(connect) -> dict:
    _dec = gate._dec
    snap: dict = {"taken_at": datetime.now(timezone.utc).isoformat(), "tables": {}, "orders": {},
                  "customers": {}, "salesmen": {}, "targets": {}, "fk_orphans": {}, "money": {}}
    with connect() as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute("show transaction_read_only")
        assert cur.fetchone()[0] == "on", "gate session is not read-only"
        cur.execute("select table_name from information_schema.tables "
                    "where table_schema='public' and table_type='BASE TABLE' order by 1")
        for (t,) in cur.fetchall():
            cur.execute(f'select count(*) from public."{t}"')
            n = cur.fetchone()[0]
            cur.execute("select 1 from information_schema.columns where table_schema='public' "
                        "and table_name=%s and column_name='id' and data_type in ('bigint','integer','smallint')", (t,))
            mx = None
            if cur.fetchone():
                cur.execute(f'select max(id)::text from public."{t}"')
                mx = cur.fetchone()[0]
            snap["tables"][t] = {"rows": n, "max_id": mx}

        cur.execute("""select id, order_no, md5(coalesce(token,'')), created_at::text, status, customer_id,
                              salesman_id, issued_to_salesman_id, total_bhd, subtotal_bhd, discount_bhd,
                              delivery_bhd, total_confirmed_bhd, source, order_kind, referral_code,
                              payment_status, focus_invoice_no
                       from shop_orders order by id""")
        for r in cur.fetchall():
            snap["orders"][str(r[0])] = {
                "order_no": r[1], "token_hash": r[2], "created_at": r[3], "status": r[4],
                "customer_id": r[5], "salesman_id": r[6], "issued_to_salesman_id": r[7],
                "total_bhd": _dec(r[8]), "subtotal_bhd": _dec(r[9]), "discount_bhd": _dec(r[10]),
                "delivery_bhd": _dec(r[11]), "total_confirmed_bhd": _dec(r[12]), "source": r[13],
                "order_kind": r[14], "referral_code": r[15], "payment_status": r[16],
                "focus_invoice_no": r[17], "lines": {}, "events": [],
            }
        cur.execute("""select id, order_id, item_code, qty, unit_price_bhd, line_total_bhd, qty_confirmed, line_status
                       from shop_order_lines order by id""")
        for lid, oid, code, qty, up, lt, qc, ls in cur.fetchall():
            o = snap["orders"].get(str(oid))
            target = o["lines"] if o else snap.setdefault("orphan_lines", {})
            target[str(lid)] = {"item_code": code, "qty": qty, "unit_price_bhd": _dec(up),
                                "line_total_bhd": _dec(lt), "qty_confirmed": qc, "line_status": ls}
        cur.execute("select id, order_id, event, ts::text from shop_order_events order by id")
        for eid, oid, ev, ts in cur.fetchall():
            o = snap["orders"].get(str(oid))
            (o["events"] if o else snap.setdefault("orphan_events", [])).append([eid, ev, ts])

        cur.execute("""select id, md5(coalesce(phone,'')), salesman_id, sticky_salesman_id, orders_count,
                              total_bhd, created_at::text from shop_customers order by id""")
        for cid, ph, sid, sticky, oc, tot, ca in cur.fetchall():
            snap["customers"][str(cid)] = {"phone_hash": ph, "salesman_id": sid, "sticky_salesman_id": sticky,
                                           "orders_count": oc, "total_bhd": _dec(tot), "created_at": ca}
        cur.execute("select id, referral_code, focus_name, is_active, md5(coalesce(user_email,'')) from salesmen order by id")
        for sid, ref, fn, act, ue in cur.fetchall():
            snap["salesmen"][str(sid)] = {"referral_code": ref, "focus_name": fn, "is_active": act, "login_hash": ue}
        cur.execute("""select salesman, period, team, target_bhd, tier2_bhd, tier3_bhd, kickback_t1, kickback_t2,
                              kickback_t3 from salesman_targets order by salesman, period""")
        for s, p, tm, t1, t2, t3, k1, k2, k3 in cur.fetchall():
            snap["targets"][f"{s}|{p}"] = {"team": tm, "tiers": [_dec(t1), _dec(t2), _dec(t3)],
                                            "rates": [str(k1), str(k2), str(k3)]}

        # generic FK orphan check (single-column FKs)
        cur.execute("""select c.conname, cl.relname, a.attname, pl.relname, pa.attname
                       from pg_constraint c
                       join pg_class cl on cl.oid=c.conrelid join pg_class pl on pl.oid=c.confrelid
                       join pg_namespace n on n.oid=cl.relnamespace
                       join pg_attribute a on a.attrelid=c.conrelid and a.attnum=c.conkey[1]
                       join pg_attribute pa on pa.attrelid=c.confrelid and pa.attnum=c.confkey[1]
                       where c.contype='f' and n.nspname='public' and array_length(c.conkey,1)=1""")
        for name, child, col, parent, pcol in cur.fetchall():
            cur.execute(f'select count(*) from public."{child}" c where c."{col}" is not null and not exists '
                        f'(select 1 from public."{parent}" p where p."{pcol}" = c."{col}")')
            snap["fk_orphans"][name] = cur.fetchone()[0]

        # Focus-side money the reports show (to explain deltas after a data load)
        cur.execute("select count(*), coalesce(sum(gross_bhd),0), coalesce(sum(taxable_bhd),0), max(line_date)::text from order_lines")
        n, g, tx, mx = cur.fetchone()
        snap["money"]["order_lines"] = {"rows": n, "gross": _dec(g), "taxable": _dec(tx), "max_date": mx}
        cur.execute("select count(*), coalesce(sum(gross_bhd),0), max(order_date)::text from orders")
        n, g, mx = cur.fetchone()
        snap["money"]["orders"] = {"rows": n, "gross": _dec(g), "max_date": mx}
        cur.execute("""select as_of_date::text, count(*), coalesce(sum(net_qty),0), coalesce(sum(total_value_bhd),0)
                       from stock_balance where as_of_date = (select max(as_of_date) from stock_balance) group by 1""")
        r = cur.fetchone()
        snap["money"]["stock_balance_latest"] = r and {"as_of": r[0], "rows": r[1], "qty": str(r[2]), "value": _dec(r[3])}
        cur.execute("""select as_of_date::text, count(*), coalesce(sum(balance_bhd),0) from ar_ageing
                       where as_of_date = (select max(as_of_date) from ar_ageing) group by 1""")
        r = cur.fetchone()
        snap["money"]["ar_latest"] = r and {"as_of": r[0], "rows": r[1], "total": _dec(r[2])}
        cur.execute("select price_book, count(*) from selling_prices group by 1 order by 1")
        snap["money"]["selling_prices"] = {pb: n for pb, n in cur.fetchall()}
        cur.execute("select status, count(*), coalesce(sum(total_bhd),0) from shop_orders group by 1 order by 1")
        snap["money"]["shop_orders_by_status"] = {s: [n, _dec(v)] for s, n, v in cur.fetchall()}
    snap["digest"] = hashlib.sha256(json.dumps(snap["orders"], sort_keys=True).encode()).hexdigest()[:16]
    return snap


# ── the invented database and the stand-in connection that answers from it ──────────────────────────────

INT_TYPES = ("bigint", "integer", "smallint")
QUOTED = r'"(?:[^"]|"")+"'                       # an identifier as the new code writes it


class World:
    """An invented database: each table's column types and rows, and the rows the foreign-key catalog query
    returns, in the catalog's own order (it has no ORDER BY)."""

    def __init__(self):
        self.tables: dict[str, dict] = {}
        self.fks: list[tuple[str, str, str, str, str]] = []
        self.read_only = "on"

    def add(self, name: str, cols: dict[str, str], rows=()) -> list[dict]:
        self.tables[name] = {"cols": dict(cols), "rows": [dict(r) for r in rows]}
        return self.tables[name]["rows"]

    def rows(self, name: str) -> list[dict]:
        return self.tables[name]["rows"]


def has_int_id(w: World, t: str) -> bool:
    return w.tables[t]["cols"].get("id") in INT_TYPES


def max_id(w: World, t: str) -> str | None:
    ids = [r["id"] for r in w.rows(t) if r.get("id") is not None]
    return str(max(ids)) if ids else None


def orphans(w: World, child: str, col: str, parent: str, pcol: str) -> int:
    have = {r.get(pcol) for r in w.rows(parent)}
    return sum(1 for r in w.rows(child) if r.get(col) is not None and r.get(col) not in have)


def unquote(q: str) -> str:
    return q[1:-1].replace('""', '"')


def _md5(s) -> str:
    return hashlib.md5((s or "").encode()).hexdigest()


def _sum(rows, col) -> Decimal:                  # coalesce(sum(col), 0)
    return sum((r[col] for r in rows if r.get(col) is not None), Decimal(0))


def _max(values):
    values = [v for v in values if v is not None]
    return max(values) if values else None


def _groups(rows, col) -> list:                  # group by col order by col (NULL last)
    return sorted({r.get(col) for r in rows}, key=lambda k: (k is None, "" if k is None else k))


def by_id(rows) -> list[dict]:
    return sorted(rows, key=lambda r: r["id"])


def money(w: World) -> dict:
    """The six figures the gate reads about the Focus tables and the orders, as Postgres would give them."""
    ol, o, sb, ar = w.rows("order_lines"), w.rows("orders"), w.rows("stock_balance"), w.rows("ar_ageing")
    out = {"order_lines": (len(ol), _sum(ol, "gross_bhd"), _sum(ol, "taxable_bhd"), _max(r.get("line_date") for r in ol)),
           "orders": (len(o), _sum(o, "gross_bhd"), _max(r.get("order_date") for r in o))}
    d = _max(r.get("as_of_date") for r in sb)
    latest = [r for r in sb if d is not None and r.get("as_of_date") == d]
    out["stock"] = [(d, len(latest), _sum(latest, "net_qty"), _sum(latest, "total_value_bhd"))] if latest else []
    d = _max(r.get("as_of_date") for r in ar)
    latest = [r for r in ar if d is not None and r.get("as_of_date") == d]
    out["ar"] = [(d, len(latest), _sum(latest, "balance_bhd"))] if latest else []
    sp, so = w.rows("selling_prices"), w.rows("shop_orders")
    out["books"] = [(k, sum(1 for r in sp if r.get("price_book") == k)) for k in _groups(sp, "price_book")]
    out["statuses"] = [(k, sum(1 for r in so if r.get("status") == k), _sum([r for r in so if r.get("status") == k], "total_bhd"))
                       for k in _groups(so, "status")]
    return out


OLD_MONEY = {
    "select count(*), coalesce(sum(gross_bhd),0), coalesce(sum(taxable_bhd),0), max(line_date)::text from order_lines":
        lambda m: [m["order_lines"]],
    "select count(*), coalesce(sum(gross_bhd),0), max(order_date)::text from orders": lambda m: [m["orders"]],
    "select as_of_date::text, count(*), coalesce(sum(net_qty),0), coalesce(sum(total_value_bhd),0) from stock_balance "
    "where as_of_date = (select max(as_of_date) from stock_balance) group by 1": lambda m: m["stock"],
    "select as_of_date::text, count(*), coalesce(sum(balance_bhd),0) from ar_ageing "
    "where as_of_date = (select max(as_of_date) from ar_ageing) group by 1": lambda m: m["ar"],
    "select price_book, count(*) from selling_prices group by 1 order by 1": lambda m: m["books"],
    "select status, count(*), coalesce(sum(total_bhd),0) from shop_orders group by 1 order by 1": lambda m: m["statuses"],
}


def answer(w: World, sql: str, params) -> list[tuple]:
    """What the invented database answers to one statement of the old code or the new."""
    s = " ".join(sql.split())
    if s == "show transaction_read_only":
        return [(w.read_only,)]
    # the six full reads: the same text in the old code and the new
    if s.startswith("select id, order_no, md5(coalesce(token,'')), created_at::text, status, customer_id,"):
        return [(o["id"], o["order_no"], _md5(o["token"]), o["created_at"], o["status"], o["customer_id"],
                 o["salesman_id"], o["issued_to_salesman_id"], o["total_bhd"], o["subtotal_bhd"], o["discount_bhd"],
                 o["delivery_bhd"], o["total_confirmed_bhd"], o["source"], o["order_kind"], o["referral_code"],
                 o["payment_status"], o["focus_invoice_no"]) for o in by_id(w.rows("shop_orders"))]
    if s.startswith("select id, order_id, item_code, qty, unit_price_bhd, line_total_bhd, qty_confirmed, line_status "):
        return [(r["id"], r["order_id"], r["item_code"], r["qty"], r["unit_price_bhd"], r["line_total_bhd"],
                 r["qty_confirmed"], r["line_status"]) for r in by_id(w.rows("shop_order_lines"))]
    if s == "select id, order_id, event, ts::text from shop_order_events order by id":
        return [(r["id"], r["order_id"], r["event"], r["ts"]) for r in by_id(w.rows("shop_order_events"))]
    if s.startswith("select id, md5(coalesce(phone,'')), salesman_id, sticky_salesman_id, orders_count, total_bhd,"):
        return [(r["id"], _md5(r["phone"]), r["salesman_id"], r["sticky_salesman_id"], r["orders_count"],
                 r["total_bhd"], r["created_at"]) for r in by_id(w.rows("shop_customers"))]
    if s == "select id, referral_code, focus_name, is_active, md5(coalesce(user_email,'')) from salesmen order by id":
        return [(r["id"], r["referral_code"], r["focus_name"], r["is_active"], _md5(r["user_email"]))
                for r in by_id(w.rows("salesmen"))]
    if s.startswith("select salesman, period, team, target_bhd, tier2_bhd, tier3_bhd, kickback_t1, kickback_t2,"):
        return [(r["salesman"], r["period"], r["team"], r["target_bhd"], r["tier2_bhd"], r["tier3_bhd"], r["kickback_t1"],
                 r["kickback_t2"], r["kickback_t3"])
                for r in sorted(w.rows("salesman_targets"), key=lambda r: (r["salesman"], r["period"]))]
    if s.startswith("select c.conname, cl.relname, a.attname, pl.relname, pa.attname from pg_constraint c "):
        return list(w.fks)
    # the old algorithm: one statement a table (two or three), one a foreign key, six for the money
    if s == ("select table_name from information_schema.tables where table_schema='public' "
             "and table_type='BASE TABLE' order by 1"):
        return [(t,) for t in sorted(w.tables)]
    if s == ("select 1 from information_schema.columns where table_schema='public' and table_name=%s "
             "and column_name='id' and data_type in ('bigint','integer','smallint')"):
        return [(1,)] if has_int_id(w, params[0]) else []
    m = re.fullmatch(r'select count\(\*\) from public\."([^"]*)" c where c\."([^"]*)" is not null and not exists '
                     r'\(select 1 from public\."([^"]*)" p where p\."([^"]*)" = c\."([^"]*)"\)', s)
    if m and m.group(2) == m.group(5):
        return [(orphans(w, m.group(1), m.group(2), m.group(3), m.group(4)),)]
    m = re.fullmatch(r'select count\(\*\) from public\."([^"]*)"', s)
    if m:
        return [(len(w.rows(m.group(1))),)]
    m = re.fullmatch(r'select max\(id\)::text from public\."([^"]*)"', s)
    if m:
        assert has_int_id(w, m.group(1)), "max(id) asked of a table without an integer id"
        return [(max_id(w, m.group(1)),)]
    if s in OLD_MONEY:
        return OLD_MONEY[s](money(w))
    # the new algorithm: one catalog query, one UNION ALL of the counts, one of the orphan counts, one for the money
    if s.startswith("select t.table_name, t.table_name in (select c.table_name from information_schema.columns c "):
        return [(t, has_int_id(w, t)) for t in sorted(w.tables)]
    branches = s.split(" union all ")
    hits = [re.fullmatch(rf"select (\d+), count\(\*\), (max\(id\)::text|null::text) from public\.({QUOTED})", b)
            for b in branches]
    if all(hits):
        out = []
        for h in hits:
            t = unquote(h.group(3))
            if h.group(2) == "max(id)::text":
                assert has_int_id(w, t), "max(id) asked of a table without an integer id"
            out.append((int(h.group(1)), len(w.rows(t)), max_id(w, t) if h.group(2) == "max(id)::text" else None))
        return out[::-1]                         # a parallel plan may hand the branches back in another order
    hits = [re.fullmatch(rf"select (\d+), count\(\*\) from public\.({QUOTED}) c where c\.({QUOTED}) is not null "
                         rf"and not exists \(select 1 from public\.({QUOTED}) p where p\.({QUOTED}) = c\.({QUOTED})\)", b)
            for b in branches]
    if all(hits):
        out = []
        for h in hits:
            child, col, parent, pcol, col2 = (unquote(h.group(k)) for k in range(2, 7))
            assert col == col2, s[:120]
            out.append((int(h.group(1)), orphans(w, child, col, parent, pcol)))
        return out[::-1]
    if s.startswith("select ol.n, ol.gross, ol.taxable, ol.max_date, o.n, o.gross, o.max_date, "):
        m = money(w)
        stock = m["stock"][0] if m["stock"] else (None,) * 4          # the left join on no row
        ar = m["ar"][0] if m["ar"] else (None,) * 3
        books = tuple(list(c) for c in zip(*m["books"])) or (None,) * 2         # array_agg over no row is NULL
        statuses = tuple(list(c) for c in zip(*m["statuses"])) or (None,) * 3
        return [m["order_lines"] + m["orders"] + stock + ar + books + statuses]
    raise AssertionError(f"the stand-in database does not know this statement: {s[:160]}")


class StandIn:
    """The connection the gate is handed: every statement is logged, and must run inside the one transaction."""

    def __init__(self, w: World):
        self.w, self.log, self.in_tx, self.transactions, self.closed = w, [], False, 0, False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    @contextlib.contextmanager
    def transaction(self):
        self.transactions += 1
        self.in_tx = True
        try:
            yield self
        finally:
            self.in_tx = False

    def cursor(self):
        return StandInCursor(self)


class StandInCursor:
    def __init__(self, conn: StandIn):
        self.conn, self.result = conn, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        assert self.conn.in_tx, "a statement outside the snapshot's transaction"
        self.conn.log.append(" ".join(sql.split()))
        self.result = list(answer(self.conn.w, sql, params))

    def fetchone(self):
        return self.result.pop(0) if self.result else None

    def fetchall(self):
        out, self.result = self.result, []
        return out


def run_old(w: World) -> tuple[dict, StandIn]:
    conn = StandIn(w)
    return old_snapshot(lambda: conn), conn


def run_new(w: World) -> tuple[dict, StandIn]:
    conn = StandIn(w)
    real, gate._conn = gate._conn, (lambda: conn)
    try:
        return gate.snapshot(), conn
    finally:
        gate._conn = real


def as_json(snap: dict) -> str:
    """The file `prod_gate snapshot` writes, with the clock set aside (the one field two runs never share)."""
    return json.dumps(dict(snap, taken_at="-"), indent=1, sort_keys=True)


def run_main(argv: list[str]) -> tuple[str, int]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = gate.main(argv)
    return buf.getvalue(), code


# ── the worlds ────────────────────────────────────────────────────────────────────────────────────────────

def core_world(seed: int = 7) -> World:
    """Every table the gate reads, with the cases that matter (the module's list). Invented, built at run time."""
    rnd = random.Random(seed)

    def amt() -> Decimal:                        # an invented amount under a hundred, with three decimals
        return Decimal(rnd.randint(0, 99_999)).scaleb(-3)

    def day(d: int) -> str:
        return (date(2026, 1, 1) + timedelta(days=d)).isoformat()

    def stamp(d: int, minute: int) -> str:
        return f"{day(d)} {9 + minute // 60:02d}:{minute % 60:02d}:00+00"

    w = World()
    w.add("salesmen", {"id": "bigint"},
          [{"id": i, "referral_code": f"ref{i:02d}", "focus_name": f"rep-{i}", "is_active": i != 3,
            "user_email": None if i == 2 else f"rep{i}@example.invalid"} for i in range(1, 5)])
    w.add("shop_customers", {"id": "bigint"},
          [{"id": i, "phone": None if i == 4 else f"phone-{seed}-{i}", "salesman_id": sid, "sticky_salesman_id": sticky,
            "orders_count": rnd.randint(0, 9), "total_bhd": amt(), "created_at": stamp(i, 3 * i)}
           for i, sid, sticky in zip(range(1, 7), (1, 2, None, 7, 8, 1), (None, 1, None, None, 2, None))])
    statuses = ("new", "confirmed", "packed", "out_for_delivery", "delivered", "cancelled", "new", "confirmed")
    w.add("shop_orders", {"id": "bigint"},
          [{"id": i, "order_no": f"T-{i:04d}", "token": None if i == 5 else f"tok-{seed}-{i}", "created_at": stamp(i, 7 * i),
            "status": st, "customer_id": None if i == 3 else 1 + i % 6, "salesman_id": (1, 2, None, 3, 4, 1, 2, None)[i - 1],
            "issued_to_salesman_id": None if i % 3 else 2, "total_bhd": amt(), "subtotal_bhd": amt(),
            "discount_bhd": amt(), "delivery_bhd": amt(), "total_confirmed_bhd": None if st == "new" else amt(),
            "source": ("referral", "dropdown", "default")[i % 3], "order_kind": "order" if i % 4 else "restock",
            "referral_code": f"ref{1 + i % 4:02d}" if i % 2 else None, "payment_status": "paid" if i % 2 else "unpaid",
            "focus_invoice_no": f"SI : {100 + i}" if st == "delivered" else None}
           for i, st in enumerate(statuses, start=1)])
    lines, events = [], []
    for oid in [o["id"] for o in w.rows("shop_orders")] + [99]:           # order 99 does not exist: an orphan line
        for _ in range(1 if oid == 99 else rnd.randint(1, 3)):
            qty = rnd.randint(1, 20)
            lines.append({"id": len(lines) + 1, "order_id": oid, "item_code": f"IT{rnd.randint(1, 60):03d}", "qty": qty,
                          "unit_price_bhd": amt(), "line_total_bhd": amt(), "qty_confirmed": None if oid % 2 else qty,
                          "line_status": None if oid % 2 else "ok"})
    for oid in [o["id"] for o in w.rows("shop_orders")] + [98]:           # and 98: an orphan event
        for ev in ("placed", "assigned")[: 1 + oid % 2]:
            events.append({"id": len(events) + 1, "order_id": oid, "event": ev, "ts": stamp(oid % 28, len(events))})
    w.add("shop_order_lines", {"id": "bigint"}, lines)
    w.add("shop_order_events", {"id": "bigint"}, events)
    w.add("salesman_targets", {"salesman": "text", "period": "text"},           # no id at all
          [{"salesman": f"rep-{s}", "period": p, "team": "field" if s % 2 else "van", "target_bhd": amt(),
            "tier2_bhd": amt(), "tier3_bhd": amt(), "kickback_t1": Decimal(1) / 2, "kickback_t2": Decimal(3) / 4,
            "kickback_t3": Decimal(1)} for s in (2, 1, 3) for p in ("2026-02", "2026-01")])
    w.add("audit_log", {"id": "bigint", "parent_id": "bigint"},              # 77 points nowhere: an orphan of itself
          [{"id": i, "parent_id": p} for i, p in zip(range(1, 6), (None, 1, 2, 77, None))])
    w.add("user_roles", {"id": "uuid"}, [{"id": f"00000000-0000-4000-8000-00000000000{i}"} for i in (1, 2)])
    w.add("shop_restock_requests", {"id": "integer"})                        # empty
    w.add("order_lines", {"id": "bigint"},
          [{"id": i, "gross_bhd": None if i in (4, 9) else amt(), "taxable_bhd": None if i == 9 else amt(),
            "line_date": day(rnd.randint(0, 30))} for i in range(1, 21)])
    w.add("orders", {"id": "bigint"}, [{"id": i, "gross_bhd": amt(), "order_date": day(rnd.randint(0, 30))} for i in range(1, 7)])
    w.add("stock_balance", {"id": "bigint"},
          [{"id": i, "as_of_date": day(10 if i <= 4 else 12), "net_qty": Decimal(rnd.randint(-5, 80)),
            "total_value_bhd": amt()} for i in range(1, 11)])
    w.add("ar_ageing", {"id": "bigint"},
          [{"id": i, "as_of_date": day(11 if i <= 2 else 12), "balance_bhd": amt() - 50} for i in range(1, 7)])
    w.add("selling_prices", {"id": "bigint"}, [{"id": i, "price_book": ("dealer", "B2B", "B2C")[i % 3]} for i in range(1, 10)])
    w.add("MixedCase", {"id": "smallint"}, [{"id": i} for i in (1, 2, 3)])
    w.add("odd name", {"id": "text", "rep id": "bigint"}, [{"id": "a", "rep id": 2}, {"id": "b", "rep id": 9}])
    w.add("empty_int", {"id": "integer"})
    w.add("numeric_id", {"id": "numeric"}, [{"id": Decimal(3) / 2}])
    w.fks = [("shop_order_lines_order_id_fkey", "shop_order_lines", "order_id", "shop_orders", "id"),
             ("fk_rep", "shop_orders", "salesman_id", "salesmen", "id"),
             ("shop_order_events_order_id_fkey", "shop_order_events", "order_id", "shop_orders", "id"),
             ("fk_rep", "shop_customers", "salesman_id", "salesmen", "id"),      # the same name: the later count stays
             ("shop_customers_sticky_fkey", "shop_customers", "sticky_salesman_id", "salesmen", "id"),
             ("odd_fk", "odd name", "rep id", "MixedCase", "id"),
             ("audit_log_parent_fkey", "audit_log", "parent_id", "audit_log", "id")]
    return w


def production_shaped() -> World:
    """The core world grown to production's shape on 20-Oct-2026: 94 tables, 78 of them with an integer id, 62
    foreign keys."""
    w = core_world(11)
    rnd = random.Random(94)
    names = [f"t_{i:02d}" for i in range(94 - len(w.tables))]
    need_id = 78 - sum(has_int_id(w, t) for t in w.tables)
    for i, t in enumerate(names):
        if i < need_id:
            w.add(t, {"id": rnd.choice(INT_TYPES)}, [{"id": k} for k in range(1, rnd.randint(0, 12) + 1)])
        else:
            kind = rnd.choice([None, "uuid", "text"])
            w.add(t, {} if kind is None else {"id": kind}, [{} for _ in range(rnd.randint(0, 5))])
    parents = [t for t in names[:need_id] if w.rows(t)]
    for k in range(62 - len(w.fks)):
        child, parent, col = names[k % len(names)], rnd.choice(parents), f"ref_{k}"
        ids = [r["id"] for r in w.rows(parent)]
        for r in w.rows(child):
            r[col] = rnd.choice(ids + [None, 10_000 + k])                   # now and then a row that points nowhere
        w.tables[child]["cols"][col] = "bigint"
        w.fks.append((f"{child}_{col}_fkey", child, col, parent, "id"))
    return w


def after_a_load(seed: int = 7, lose_a_line: bool = False) -> World:
    """The core world a day later: a new order with its line and event, an order confirmed with its event, more sales
    lines, a newer stock date; and, when asked, a line of an existing order gone (a protected change)."""
    w = core_world(seed)
    orders, lines, events = w.rows("shop_orders"), w.rows("shop_order_lines"), w.rows("shop_order_events")
    first_new = next(o for o in orders if o["status"] == "new")
    first_new["status"], first_new["total_confirmed_bhd"] = "confirmed", first_new["total_bhd"]
    events.append({"id": len(events) + 1, "order_id": first_new["id"], "event": "confirmed", "ts": "2026-02-01 10:00:00+00"})
    oid = max(o["id"] for o in orders) + 1
    orders.append(dict(orders[1], id=oid, order_no=f"T-{oid:04d}", token=f"tok-next-{oid}", status="new",
                       total_confirmed_bhd=None, created_at="2026-02-01 10:05:00+00"))
    lines.append(dict(lines[0], id=max(r["id"] for r in lines) + 1, order_id=oid))
    events.append({"id": len(events) + 1, "order_id": oid, "event": "placed", "ts": "2026-02-01 10:05:00+00"})
    ol = w.rows("order_lines")
    ol.extend(dict(ol[k], id=len(ol) + k + 1, line_date="2026-02-01") for k in range(5))
    sb = w.rows("stock_balance")
    sb.extend(dict(sb[k], id=len(sb) + k + 1, as_of_date="2026-02-01") for k in range(3))
    if lose_a_line:
        lines.remove(next(r for r in lines if r["order_id"] == 2))
    return w


# ── the tests ─────────────────────────────────────────────────────────────────────────────────────────────

@test("a dozen statements whatever the number of tables and foreign keys: 12 where the old algorithm sends 343 "
      "(production's shape on 20-Oct-2026: 94 tables, 78 with an integer id, 62 foreign keys)")
def _():
    w = production_shaped()
    assert len(w.tables) == 94 and sum(has_int_id(w, t) for t in w.tables) == 78 and len(w.fks) == 62
    (old, old_conn), (new, new_conn) = run_old(w), run_new(w)
    assert len(old_conn.log) == 1 + 1 + 94 * 2 + 78 + 6 + 1 + 62 + 6 == 343, len(old_conn.log)
    assert len(new_conn.log) == 12, len(new_conn.log)
    assert as_json(old) == as_json(new)
    assert sum(1 for v in new["fk_orphans"].values() if v) > 1, "some keys point nowhere: the counts are compared too"


@test("the JSON is byte for byte the old algorithm's: every kind of id, quoted names, orphans, one constraint name "
      "twice, a self-reference, the latest stock and receivables, the books and statuses in their order")
def _():
    for seed in (7, 8, 9):
        w = core_world(seed)
        (old, _), (new, conn) = run_old(w), run_new(w)
        assert as_json(old) == as_json(new), seed
        assert list(old) == list(new) and old["digest"] == new["digest"]
        assert list(old["money"]["shop_orders_by_status"]) == list(new["money"]["shop_orders_by_status"]), \
            "the printed line shows this dict in its own order"
        assert len(conn.log) == 12
    # what the cases come to here (a stand-in that answered nothing could not pass)
    t = new["tables"]
    assert t["MixedCase"] == {"rows": 3, "max_id": "3"} and t["empty_int"] == {"rows": 0, "max_id": None}
    assert t["shop_restock_requests"] == {"rows": 0, "max_id": None} and t["salesman_targets"]["max_id"] is None
    assert t["user_roles"]["max_id"] is None and t["odd name"]["max_id"] is None and t["numeric_id"]["max_id"] is None
    assert t["audit_log"] == {"rows": 5, "max_id": "5"} and t["salesman_targets"]["rows"] == 6
    assert new["fk_orphans"]["fk_rep"] == 2, "the later of the two constraints of that name counts, as before"
    assert new["fk_orphans"]["odd_fk"] == 1 and new["fk_orphans"]["audit_log_parent_fkey"] == 1
    assert new["fk_orphans"]["shop_order_lines_order_id_fkey"] == 1 and list(new["orphan_lines"])
    assert new["fk_orphans"]["shop_customers_sticky_fkey"] == 0 and new["orphan_events"][0][1] == "placed"
    m = new["money"]
    assert m["stock_balance_latest"]["as_of"] == "2026-01-13" and m["stock_balance_latest"]["rows"] == 6
    assert m["ar_latest"]["as_of"] == "2026-01-13" and m["ar_latest"]["rows"] == 4
    assert list(m["selling_prices"]) == ["B2B", "B2C", "dealer"] and sum(m["selling_prices"].values()) == 9
    assert list(m["shop_orders_by_status"]) == sorted({"new", "confirmed", "packed", "out_for_delivery", "delivered", "cancelled"})


@test("an empty database (every table there, no row) gives the old JSON; without a foreign key it is 11 statements")
def _():
    w = core_world()
    for t in w.tables.values():
        t["rows"].clear()
    (old, _), (new, conn) = run_old(w), run_new(w)
    assert as_json(old) == as_json(new) and len(conn.log) == 12
    m = new["money"]
    assert m["stock_balance_latest"] is None and m["ar_latest"] is None
    assert m["selling_prices"] == {} and m["shop_orders_by_status"] == {}
    assert m["order_lines"] == {"rows": 0, "gross": "0.000", "taxable": "0.000", "max_date": None}
    w.fks = []
    (old, _), (new, conn) = run_old(w), run_new(w)
    assert as_json(old) == as_json(new) and new["fk_orphans"] == {} and len(conn.log) == 11


@test("`prod_gate snapshot` prints the same line and writes the same file as before (the line the load's worker reads)")
def _():
    w = core_world()
    real_gates, real_snapshot, real_conn = gate.GATES, gate.snapshot, gate._conn
    with tempfile.TemporaryDirectory() as tmp:
        try:
            gate.GATES, gate.snapshot = Path(tmp) / "old", (lambda: old_snapshot(lambda: StandIn(w)))
            old_out, old_code = run_main(["snapshot", "--label", "pre-data-010126"])
            gate.GATES, gate.snapshot, gate._conn = Path(tmp) / "new", real_snapshot, (lambda: StandIn(w))
            new_out, new_code = run_main(["snapshot", "--label", "pre-data-010126"])
        finally:
            gate.GATES, gate.snapshot, gate._conn = real_gates, real_snapshot, real_conn
        named = re.compile(r"^snapshot \d{4}-\d{2}-\d{2}_\d{6}_pre-data-010126\.json: ")
        assert named.match(old_out) and named.match(new_out), (old_out, new_out)
        assert named.sub("", old_out) == named.sub("", new_out), (old_out, new_out)
        assert old_code == new_code == 1, "FK orphans: exit 1, as before"
        (old_file,), (new_file,) = (sorted((Path(tmp) / d).glob("*_pre-data-010126.json")) for d in ("old", "new"))
        clock = re.compile(r'"taken_at": "[^"]*"')
        assert clock.sub("", old_file.read_text(encoding="utf-8")) == clock.sub("", new_file.read_text(encoding="utf-8"))


def write(folder: Path, name: str, snap: dict) -> str:
    path = folder / name
    path.write_text(json.dumps(snap, indent=1, sort_keys=True), encoding="utf-8")        # as `snapshot` writes it
    return str(path)


def compared(a: str, b: str) -> tuple[str, int]:
    out, code = run_main(["compare", a, b])
    return out.split("\n", 1)[1], code                   # past the line that names the two files


@test("compare: an old file beside a new one of the same database is PASS with nothing changed; across a load, "
      "old -> new reads exactly as old -> old (deltas, and the failure of a lost line)")
def _():
    v1 = core_world()
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        a_old, a_new = write(folder, "a_old.json", run_old(v1)[0]), write(folder, "a_new.json", run_new(v1)[0])
        for a, b in ((a_old, a_new), (a_new, a_old)):
            out, code = compared(a, b)
            assert code == 0 and out.strip() == "PASS: 0 failure(s), 0 explained delta(s)", out
        for lose in (False, True):
            v2 = after_a_load(lose_a_line=lose)
            b_old, b_new = write(folder, "b_old.json", run_old(v2)[0]), write(folder, "b_new.json", run_new(v2)[0])
            reference = compared(a_old, b_old)
            assert compared(a_old, b_new) == reference and compared(a_new, b_old) == reference
            assert compared(a_new, b_new) == reference
            out, code = reference
            assert "delta: new order T-0009 (new, BHD " in out and "(with event)" in out and "money order_lines" in out
            assert "money stock_balance_latest" in out and "shop_orders: 8 -> 9 (+1)" in out
            if lose:
                assert code == 1 and "DISAPPEARED" in out and "\nFAIL: 1 failure(s)" in out, out
            else:
                assert code == 0 and "\nPASS: 0 failure(s)" in out, out


@test("names from the catalog are quoted as identifiers: an embedded double quote is doubled")
def _():
    assert gate._ident("shop_orders") == '"shop_orders"' and gate._ident("odd name") == '"odd name"'
    assert gate._ident('Odd"name') == '"Odd""name"' and gate._ident('""') == '""""""'
    w = core_world()
    w.add('Odd"name', {"id": "integer"}, [{"id": 4, 'rep"id': 1}, {"id": 6, 'rep"id': 12}])
    w.fks.append(("quote_fk", 'Odd"name', 'rep"id', "salesmen", "id"))
    snap, conn = run_new(w)
    assert snap["tables"]['Odd"name'] == {"rows": 2, "max_id": "6"} and snap["fk_orphans"]["quote_fk"] == 1
    sent = " ".join(conn.log)
    assert 'from public."Odd""name"' in sent and 'c."rep""id" is not null' in sent and len(conn.log) == 12


@test("the session is checked read-only first and every statement runs inside ONE transaction; a read-write "
      "session is refused before anything is read")
def _():
    w = core_world()
    _snap, conn = run_new(w)
    assert conn.log[0] == "show transaction_read_only" and conn.transactions == 1 and conn.closed
    w.read_only = "off"
    conn = StandIn(w)
    real, gate._conn = gate._conn, (lambda: conn)
    try:
        exc = raises(AssertionError, gate.snapshot)
    finally:
        gate._conn = real
    assert "not read-only" in str(exc) and conn.log == ["show transaction_read_only"]


@test("CI runs this suite")
def _():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    loop = next(line for line in ci.splitlines() if line.strip().startswith("for suite in"))
    assert re.search(r"\bgate\b", loop), loop


# ── local: the real thing on a throwaway Postgres (SKIP without YQ_LOCAL_PG_GATE, as in CI) ──────────────────

LOCAL_SCHEMA = (
    "create table salesmen (id bigint primary key, referral_code text, focus_name text, is_active boolean, user_email text)",
    "create table shop_customers (id bigint primary key, phone text, salesman_id bigint, sticky_salesman_id bigint, "
    "orders_count integer, total_bhd numeric(12,3), created_at timestamptz)",
    "create table shop_orders (id bigint primary key, order_no text, token text, created_at timestamptz, status text, "
    "customer_id bigint, salesman_id bigint, issued_to_salesman_id bigint, total_bhd numeric(12,3), "
    "subtotal_bhd numeric(12,3), discount_bhd numeric(12,3), delivery_bhd numeric(12,3), total_confirmed_bhd numeric(12,3), "
    "source text, order_kind text, referral_code text, payment_status text, focus_invoice_no text)",
    "create table shop_order_lines (id bigint primary key, order_id bigint, item_code text, qty integer, "
    "unit_price_bhd numeric(12,3), line_total_bhd numeric(12,3), qty_confirmed integer, line_status text)",
    "create table shop_order_events (id bigint primary key, order_id bigint, event text, ts timestamptz)",
    "create table salesman_targets (salesman text, period text, team text, target_bhd numeric, tier2_bhd numeric, "
    "tier3_bhd numeric, kickback_t1 numeric, kickback_t2 numeric, kickback_t3 numeric, primary key (salesman, period))",
    "create table audit_log (id bigint primary key, parent_id bigint)",
    "create table user_roles (id uuid primary key)",
    "create table shop_restock_requests (id integer primary key)",
    "create table order_lines (id bigint primary key, gross_bhd numeric, taxable_bhd numeric, line_date date)",
    "create table orders (id bigint primary key, gross_bhd numeric, order_date date)",
    "create table stock_balance (id bigint primary key, as_of_date date, net_qty numeric, total_value_bhd numeric)",
    "create table ar_ageing (id bigint primary key, as_of_date date, balance_bhd numeric)",
    "create table selling_prices (id bigint primary key, price_book text)",
    'create table "MixedCase" (id smallint primary key)',
    'create table "odd name" (id text primary key, "rep id" bigint)',
    "create table empty_int (id integer primary key)",
    "create table numeric_id (id numeric primary key)",
    "create table pair_parent (a integer, b integer, primary key (a, b))",           # a two-column key: not counted
    "create table pair_child (id integer primary key, a integer, b integer, foreign key (a, b) references pair_parent (a, b))",
)


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def local_schema(w: World) -> list[str]:
    """The tables of a world: the core ones as production has them, the others from their column types (an integer
    id is the key other tables point at)."""
    out = list(LOCAL_SCHEMA)
    core = {re.match(r'create table ("[^"]+"|\w+)', s).group(1).strip('"') for s in LOCAL_SCHEMA}
    for name, table in w.tables.items():
        if name not in core:
            cols = [f"{_q(c)} {kind}" + (" primary key" if c == "id" and kind in INT_TYPES else "")
                    for c, kind in table["cols"].items()]
            out.append(f"create table public.{_q(name)} ({', '.join(cols)})")
    return out


def local_pair(conn, w: World) -> tuple[dict, dict, list, list, int]:
    """The world written into the empty database inside ONE REPEATABLE READ transaction, the transaction made
    read-only, then the old snapshot and the new one in it. The caller rolls it back."""
    with conn.cursor() as cur:
        cur.execute("select count(*) from information_schema.tables where table_schema='public'")
        assert cur.fetchone()[0] == 0, "the database is not empty"
        schema = local_schema(w)
        for statement in schema:
            cur.execute(statement)
        for name, table in w.tables.items():
            for row in table["rows"]:
                cols = list(row)
                if not cols:
                    cur.execute(f"insert into public.{_q(name)} default values")
                    continue
                cur.execute(f"insert into public.{_q(name)} ({', '.join(_q(c) for c in cols)}) "
                            f"values ({', '.join(['%s'] * len(cols))})", [row[c] for c in cols])
        for name, child, col, parent, pcol in w.fks:         # NOT VALID: the orphans stay
            cur.execute(f"alter table public.{_q(child)} add constraint {_q(name)} foreign key ({_q(col)}) "
                        f"references public.{_q(parent)} ({_q(pcol)}) not valid")
        cur.execute("set transaction read only")
    old_log, new_log = [], []
    old = old_snapshot(lambda: Shared(conn, old_log))
    real, gate._conn = gate._conn, (lambda: Shared(conn, new_log))
    try:
        new = gate.snapshot()
    finally:
        gate._conn = real
    return old, new, old_log, new_log, len(schema)


class Shared:
    """One open connection handed to a snapshot as its own: never closed or committed by it (its transaction is a
    savepoint inside the one already open), every statement counted."""

    def __init__(self, conn, log: list):
        self.conn, self.log = conn, log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def transaction(self):
        return self.conn.transaction()

    def cursor(self):
        return Counted(self.conn.cursor(), self.log)


class Counted:
    def __init__(self, cur, log: list):
        self.cur, self.log = cur, log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.cur.close()
        return False

    def execute(self, sql, params=None):
        self.log.append(sql)
        return self.cur.execute(sql, params)

    def fetchone(self):
        return self.cur.fetchone()

    def fetchall(self):
        return self.cur.fetchall()


@test("local: on a throwaway Postgres the old and the new snapshot inside ONE read-only REPEATABLE READ transaction "
      "are identical: the core world, the same tables empty, production's shape (SKIP without YQ_LOCAL_PG_GATE)")
def _():
    dsn = os.environ.get(LOCAL_ENV, "")
    if not dsn:
        print(f"  SKIP  (no {LOCAL_ENV}: a loopback Postgres with an empty database)")
        return
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    if conninfo_to_dict(dsn).get("host") not in ("localhost", "127.0.0.1", "::1"):
        print(f"  SKIP  ({LOCAL_ENV} is not a loopback address: refused)")
        return
    emptied = core_world()
    for table in emptied.tables.values():
        table["rows"].clear()
    conn = psycopg.connect(dsn, connect_timeout=10)
    try:
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        for label, w in (("core", core_world()), ("empty", emptied), ("production's shape", production_shaped())):
            try:
                old, new, old_log, new_log, tables = local_pair(conn, w)
            finally:
                conn.rollback()                  # everything above was this one transaction: nothing stays
            assert as_json(old) == as_json(new), label
            assert list(old["money"]["shop_orders_by_status"]) == list(new["money"]["shop_orders_by_status"]), label
            assert len(new["tables"]) == tables and len(new_log) == 12, (label, len(new["tables"]), len(new_log))
            assert "pair_child_a_b_fkey" not in new["fk_orphans"], "a two-column key is not counted, as before"
            print(f"  local ({label}): {tables} tables, {len(w.fks)} foreign keys, old {len(old_log)} statements, "
                  f"new {len(new_log)}")
            if label == "core":
                assert new["fk_orphans"]["odd_fk"] == 1 and new["fk_orphans"]["audit_log_parent_fkey"] == 1
                assert new["money"]["stock_balance_latest"]["rows"] == 6
                assert list(new["money"]["selling_prices"]) == ["B2B", "B2C", "dealer"]
            if label == "empty":
                assert new["money"]["stock_balance_latest"] is None and new["money"]["shop_orders_by_status"] == {}
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
