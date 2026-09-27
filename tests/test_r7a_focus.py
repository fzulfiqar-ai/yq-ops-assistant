"""R7a — Focus link v1, backend (Sprint 1 "Make it true", plan §24 r7_focus_links, audit D6).

    python -m tests.test_r7a_focus

Same lightweight runner and the same idea as tests/test_r3_pipeline.py: every test runs against an
in-memory stand-in for the PostgREST client (eq / is / in / ilike filters, inserts that hand out
ids, updates that report what they touched, missing tables that raise like PostgREST). Nothing
here reaches a database or the network; the pure tests pass with no .env at all. All data is
synthetic (made-up shops, reps and invoice numbers).

Covered:
  * clean_invoice_no — 'SI : SI-YQ-…', 'SI:SI-YQ-…', ' si-yq-… ' all become the one key the
    views compare, and set_invoice / Delivered store that key;
  * the suggestion list — empty + hint before the migration; grouped per order, best first,
    capped per order, line diff counts, the exact flag, plain-English method labels;
  * accept — the link is confirmed (method / confidence from the suggestion), an empty invoice
    number is filled, an open order moves to Delivered through set_status (compare-and-swap,
    actor focus-recon:<email>, event detail {invoice_key, method, confidence}); a Received order
    passes Confirmed without the confirmed totals being written; no money column moves; a
    cancelled order is refused; a lost race writes no link; a typed number is never overwritten;
    a pair nobody suggested must be in the uploaded ledger; the same accept again writes nothing;
  * reject — stored as rejected, the order untouched, idempotent; reject after accept;
  * audit — audit_log 'shop.focus_link' + shop_admin_audit 'focus_link' on every decision;
  * the routes — admin-gated (a rep gets 403 and nothing is read or written), 404 / 409 / 400;
  * the migration and its reverse, read as text (additive, RLS, revokes, no GRANT / CASCADE /
    security_invoker, the recon's 23 columns kept in order, the R3 view restored verbatim);
  * a local Postgres replay (SKIPs without the cluster): the migration applied inside a rolled-back
    transaction on synthetic data — the three tiers, the ranks, many orders per invoice, rejected
    pairs, the fixed recon, Python ↔ SQL normalisation parity, the self-check and the reverse.
"""
from __future__ import annotations

import io
import os
import re
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

import warnings  # noqa: E402
warnings.filterwarnings("ignore")

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


MIGRATION = ROOT / "scripts" / "r7_focus_links_migration.sql"
REVERSE = ROOT / "scripts" / "r7_focus_links_reverse.sql"


# ── a table-aware fake PostgREST client (tests/test_r3_pipeline.py, trimmed) ────

def _same(a, b) -> bool:
    return a == b or str(a) == str(b)


def _like(value, pattern: str) -> bool:
    rx = "".join(".*" if ch == "%" else "." if ch == "_" else re.escape(ch) for ch in str(pattern))
    return re.fullmatch(rx, str(value or ""), re.I | re.S) is not None


class _Query:
    def __init__(self, db: "_FakeDB", table: str):
        self.db, self.table = db, table
        self.op, self.payload, self.filters, self.negate = "select", None, [], False
        self.cols, self.want_count, self.lim, self.head, self.desc = "*", None, None, False, False

    def select(self, cols="*", count=None, head=False):
        self.op, self.cols, self.want_count, self.head = "select", cols, count, head
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def delete(self):
        self.op = "delete"
        return self

    def _filter(self, kind, col, val):
        self.filters.append((kind, col, val))
        return self

    def eq(self, col, val):
        return self._filter("eq", col, val)

    def is_(self, col, val):
        return self._filter("is", col, val)

    def in_(self, col, vals):
        return self._filter("in", col, list(vals))

    def ilike(self, col, val):
        return self._filter("ilike", col, val)

    def order(self, col, desc=False, **_kw):
        self.desc = bool(desc)
        return self

    def limit(self, n):
        self.lim = n
        return self

    def range(self, a, b):
        self.lim = b - a + 1
        return self

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *a, **k: self

    def _match(self, row: dict) -> bool:
        for kind, col, val in self.filters:
            got = row.get(col)
            if kind == "eq":
                ok = _same(got, val)
            elif kind == "is":
                ok = (got is None) if str(val).lower() == "null" else (bool(got) == (str(val).lower() == "true"))
            elif kind == "in":
                ok = any(_same(got, v) for v in val)
            elif kind == "ilike":
                ok = _like(got, val)
            else:
                ok = True
            if not ok:
                return False
        return True

    def execute(self):
        db = self.db
        db.calls.append((self.op, self.table, self.cols if self.op == "select" else self.payload, list(self.filters)))
        fail = db.fail.pop((self.op, self.table), None)
        if fail is not None:
            if callable(fail) and not isinstance(fail, BaseException):
                fail = fail()
            raise fail  # type: ignore[misc]
        if self.table in db.missing:
            raise RuntimeError(f'{{"code": "42P01", "message": "relation \\"public.{self.table}\\" does not exist"}}')
        rows = db.tables.setdefault(self.table, [])
        if self.op == "select":
            out = [dict(r) for r in rows if self._match(r)]
            if self.desc:
                out.reverse()
            data = [] if self.head else (out[: self.lim] if self.lim else out)
            return SimpleNamespace(data=data, count=(len(out) if self.want_count else None))
        if self.op == "insert":
            items = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            for p in items:
                r = dict(p)
                r.setdefault("id", db.next_id(self.table))
                rows.append(r)
                out.append(dict(r))
            return SimpleNamespace(data=out, count=None)
        if self.op == "update":
            out = []
            for r in rows:
                if self._match(r):
                    r.update(self.payload)
                    out.append(dict(r))
            return SimpleNamespace(data=out, count=None)
        if self.op == "delete":
            gone = [r for r in rows if self._match(r)]
            rows[:] = [r for r in rows if not self._match(r)]
            return SimpleNamespace(data=gone, count=None)
        return SimpleNamespace(data=[], count=None)


class _FakeDB:
    def __init__(self, tables: dict | None = None, missing: set | None = None):
        self.tables: dict[str, list[dict]] = {k: [dict(r) for r in v] for k, v in (tables or {}).items()}
        self.missing: set[str] = set(missing or ())
        self.calls: list[tuple] = []
        self.fail: dict[tuple, object] = {}
        self.rpc_results: dict = {}
        self._ids: dict[str, int] = {}

    def table(self, name):
        return _Query(self, name)

    def rpc(self, name, params=None):
        self.calls.append(("rpc", name, params, []))
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=self.rpc_results.get(name), count=None))

    def next_id(self, table) -> int:
        cur = max([int(r.get("id") or 0) for r in self.tables.get(table, [])] + [self._ids.get(table, 0)])
        self._ids[table] = cur + 1
        return cur + 1

    def writes(self, table: str | None = None) -> list[tuple]:
        return [c for c in self.calls if c[0] in ("insert", "update", "delete") and (table is None or c[1] == table)]

    def rows(self, table: str) -> list[dict]:
        return self.tables.get(table, [])


class _patched:
    """Point app.shop / app.database / app.catalog / app.upcoming at the fake and reset the caches
    the order path and the auth path consult."""

    def __init__(self, fake: _FakeDB):
        self.fake = fake

    def __enter__(self):
        import app.catalog as cat
        import app.database as db
        import app.shop as s
        import app.upcoming as up
        self.mods = (s, db, cat, up)
        self.saved = (s.get_client, db.get_client, cat.get_client, up.get_client)
        s.get_client = db.get_client = cat.get_client = up.get_client = lambda: self.fake
        s._col_cache.clear()
        s._salesman_cache.clear()
        s._settings_cache.update(at=0.0, vals=None)
        db.invalidate_user_cache()
        return self.fake

    def __exit__(self, *_exc):
        s, db, cat, up = self.mods
        s.get_client, db.get_client, cat.get_client, up.get_client = self.saved
        s._col_cache.clear()
        s._salesman_cache.clear()
        s._settings_cache.update(at=0.0, vals=None)
        db.invalidate_user_cache()
        return False


# ── synthetic fixtures (no real shop, rep, phone or invoice) ────────────────────

NOW = datetime.now(timezone.utc)
ADMIN = "admin@example.com"
SALESMEN = [{"id": 1, "name": "Rep One", "referral_code": "rep-one", "is_active": True, "focus_name": "Rep One",
             "user_email": "rep@example.com"}]
MONEY_COLS = ("subtotal_bhd", "discount_bhd", "delivery_bhd", "total_bhd", "subtotal_confirmed_bhd",
              "total_confirmed_bhd", "payment_status", "payment_method", "returned_bhd")


def _order(id, status="delivered", **over):
    o = {"id": id, "order_no": f"YQ-2609-{9000 + id}", "token": f"tok{id}".ljust(24, "x"), "status": status,
         "customer_name": "Test Shop", "customer_phone": "97300000000", "customer_shop": "Test Shop",
         "salesman_id": 1, "salesman_name": "Rep One", "subtotal_bhd": 12.85, "discount_bhd": 0, "delivery_bhd": 0,
         "total_bhd": 12.85, "subtotal_confirmed_bhd": None, "total_confirmed_bhd": None,
         "payment_status": "unpaid", "payment_method": None, "returned_bhd": None, "focus_invoice_no": None,
         "is_test": False, "customer_id": None, "created_at": (NOW - timedelta(days=2)).isoformat(),
         "delivered_at": None}
    if status == "delivered":
        o.update(subtotal_confirmed_bhd=12.85, total_confirmed_bhd=12.85, delivered_at=(NOW - timedelta(days=1)).isoformat())
    o.update(over)
    return o


def _lines(order_id):
    return [{"id": order_id * 10 + 1, "order_id": order_id, "item_code": "T02", "qty": 3, "unit_price_bhd": 2.95,
             "line_total_bhd": 8.85, "line_status": "ok", "qty_confirmed": None, "backorder": False},
            {"id": order_id * 10 + 2, "order_id": order_id, "item_code": "X05", "qty": 2, "unit_price_bhd": 2.0,
             "line_total_bhd": 4.0, "line_status": "ok", "qty_confirmed": None, "backorder": False}]


def _cand(order_id, key, rank=1, method="auto_items", confidence=0.9, **over):
    r = {"order_id": order_id, "order_no": f"YQ-2609-{9000 + order_id}", "order_status": "delivered",
         "order_created_at": (NOW - timedelta(days=2, minutes=order_id)).isoformat(), "customer_shop": "Test Shop",
         "salesman_id": 1, "salesman_name": "Rep One", "order_total_bhd": 12.85, "typed_invoice_key": None,
         "invoice_key": key, "invoice_date": "2026-09-22", "focus_salesman": "Rep One - Acc WH",
         "focus_customer": "Test Shop WLL", "invoice_total_bhd": 12.85, "invoice_linked_n": 0,
         "invoice_linked_bhd": 0, "invoice_open_bhd": 12.85, "amount_diff_bhd": 0, "method": method,
         "confidence": confidence, "sio_key": None, "overlap_share": 1.0, "lines_order": 2, "lines_invoice": 2,
         "lines_matched": 2, "lines_qty_diff": 0, "lines_price_diff": 0, "lines_missing_on_invoice": 0,
         "lines_extra_on_invoice": 0, "invoice_orders_n": 1, "rank": rank}
    r.update(over)
    return r


def _db(missing: set | None = None, **tables) -> _FakeDB:
    base = {"salesmen": SALESMEN, "app_settings": [], "shop_customers": [], "shop_orders": [],
            "shop_order_lines": [], "shop_order_events": [], "audit_log": [], "shop_admin_audit": [],
            "shop_order_focus_links": [], "v_shop_focus_candidates": [], "v_sales": [], "user_roles": []}
    base.update(tables)
    return _FakeDB(base, missing=missing)


def _raises(fn, want: str):
    from app.shop import ShopError
    try:
        fn()
    except ShopError as e:
        assert want.lower() in str(e).lower(), (want, str(e))
        return e
    raise AssertionError(f"expected ShopError containing {want!r}")


def _events(fake, order_id=None):
    return [e for e in fake.rows("shop_order_events") if order_id is None or e.get("order_id") == order_id]


def _money(row: dict) -> dict:
    return {k: row.get(k) for k in MONEY_COLS}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. one normalised invoice key
# ═══════════════════════════════════════════════════════════════════════════════

NORMALISE_CASES = [
    ("SI : SI-YQ-26-09-119", "SI-YQ-26-09-119"),      # how Focus stores it (order_lines / v_sales)
    ("SI:SI-YQ-26-09-96", "SI-YQ-26-09-96"),          # the stock ledger's voucher form
    ("SI-YQ-26-09-119", "SI-YQ-26-09-119"),           # what staff type
    ("  si-yq-26-09-119 ", "SI-YQ-26-09-119"),
    ("si :  si-yq-26-09-7", "SI-YQ-26-09-7"),
    ("SI :SI-1", "SI-1"),
    ("SIO:YQ-26-09-137", "SIO:YQ-26-09-137"),         # 'SIO:' is not the 'SI :' prefix
    ("SI-26-09-0042", "SI-26-09-0042"),
    ("SI :", None), ("   ", None), ("", None), (None, None),
]


@test("clean_invoice_no: Focus's 'SI : …', the ledger's 'SI:…' and a typed ' si-… ' are the same key; empty is None")
def _():
    from app.shop_pipeline import INVOICE_MAX, clean_invoice_no
    for raw, want in NORMALISE_CASES:
        assert clean_invoice_no(raw) == want, (raw, clean_invoice_no(raw), want)
    assert clean_invoice_no("SI-1\x00\x07") == "SI-1", "control characters are dropped"
    assert len(clean_invoice_no("SI : " + "A" * 80)) == INVOICE_MAX


@test("clean_invoice_no: the Python rule is the SQL rule written in the migration (both views)")
def _():
    from app import shop_pipeline
    sql = MIGRATION.read_text(encoding="utf-8")
    rule = r"upper(regexp_replace(trim("
    assert sql.count(rule) >= 4, "recon inv + typed, candidates ord + fl all normalise the same way"
    assert sql.count(r"'^SI\s*:\s*', '', 'i'") >= 5, "one prefix pattern, case-insensitive, everywhere"
    assert shop_pipeline._SI_PREFIX.pattern == r"^SI\s*:\s*" and shop_pipeline._SI_PREFIX.flags & re.I


@test("set_invoice and Delivered store the normalised key (a pasted 'SI : …' no longer breaks the match)")
def _():
    from app.shop import set_status
    from app.shop_pipeline import set_invoice
    fake = _db(shop_orders=[_order(1), _order(2, status="out_for_delivery")], shop_order_lines=_lines(1) + _lines(2))
    with _patched(fake):
        assert set_invoice(1, "SI : SI-YQ-26-09-7", ADMIN)["focus_invoice_no"] == "SI-YQ-26-09-7"
        n = len(fake.writes())
        set_invoice(1, " si-yq-26-09-7 ", ADMIN)
        assert len(fake.writes()) == n, "the same invoice typed differently is the same number: nothing written"
        o = set_status(2, "delivered", None, actor=ADMIN, focus_invoice_no="si : si-yq-26-09-8")
        assert o["focus_invoice_no"] == "SI-YQ-26-09-8"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. the suggestion list
# ═══════════════════════════════════════════════════════════════════════════════

@test("candidates: empty + hint before the migration (the view is missing), never a 500")
def _():
    from app.shop_pipeline import LINKS_HINT, list_focus_candidates
    fake = _db(missing={"v_shop_focus_candidates"})
    with _patched(fake):
        assert list_focus_candidates() == {"orders": [], "count": 0, "candidates": 0, "hint": LINKS_HINT}


@test("candidates: grouped per order, best first, capped per order, with line diffs, the exact flag and plain labels")
def _():
    from app.shop_pipeline import list_focus_candidates
    rows = [
        # order 1: two invoices; the exact one is rank 1 (rows arrive in any order)
        _cand(1, "SI-T-2", rank=2, lines_matched=0, lines_qty_diff=1, lines_extra_on_invoice=4, lines_invoice=5,
              amount_diff_bhd=8.2, invoice_total_bhd=21.05),
        _cand(1, "SI-T-1", rank=1),
        # order 2: one invoice shared with order 3 (a rep invoiced two orders together)
        _cand(2, "SI-T-3", invoice_orders_n=2, lines_extra_on_invoice=6, amount_diff_bhd=19.1, invoice_total_bhd=31.5,
              order_status="new", confidence=0.875, overlap_share=0.875),
        _cand(3, "SI-T-3", invoice_orders_n=2, method="narration_ref", confidence=1.0, sio_key=None),
        _cand(3, "SI-T-4", rank=2, method="sio_ref", confidence=0.95, sio_key="SIO:YQ-26-09-137"),
    ] + [_cand(4, f"SI-T-{10 + i}", rank=i + 1) for i in range(7)]
    fake = _db(v_shop_focus_candidates=rows)
    with _patched(fake):
        out = list_focus_candidates(per_order=5)
    assert out["count"] == 4 and out["candidates"] == 2 + 1 + 2 + 5 and "hint" not in out and "ledger_as_of" in out
    by = {o["order_id"]: o for o in out["orders"]}
    o1 = by[1]
    assert [c["invoice_key"] for c in o1["candidates"]] == ["SI-T-1", "SI-T-2"], "rank order, not arrival order"
    best, other = o1["candidates"]
    assert best["exact"] is True and other["exact"] is False
    assert other["lines"] == {"order": 2, "invoice": 5, "matched": 0, "qty_diff": 1, "price_diff": 0,
                              "missing_on_invoice": 0, "extra_on_invoice": 4}
    assert other["amount_diff_bhd"] == 8.2 and isinstance(other["invoice_total_bhd"], float)
    assert best["method"] == "auto_items" and best["method_label"] == "Same rep, same items" and best["confidence"] == 0.9
    assert o1["status_label"] == "Delivered" and o1["order_total_bhd"] == 12.85 and o1["order_no"] == "YQ-2609-9001"
    assert by[2]["status"] == "new" and by[2]["status_label"] == "Received"
    assert by[2]["candidates"][0]["invoice_orders_n"] == 2 and by[2]["candidates"][0]["confidence"] == 0.875
    c3 = by[3]["candidates"]
    assert [c["method"] for c in c3] == ["narration_ref", "sio_ref"] and c3[1]["sio_key"] == "SIO:YQ-26-09-137"
    assert c3[0]["method_label"] == "Order number in the invoice note"
    assert c3[1]["method_label"] == "Order number on the stock issue"
    assert len(by[4]["candidates"]) == 5 and by[4]["candidates"][-1]["rank"] == 5, "at most per_order per order"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. accept
# ═══════════════════════════════════════════════════════════════════════════════

@test("accept (delivered order): confirmed link from the suggestion, the empty invoice number filled, status and money untouched, audited")
def _():
    from app.shop_pipeline import decide_focus_link
    fake = _db(shop_orders=[_order(1)], shop_order_lines=_lines(1),
               v_shop_focus_candidates=[_cand(1, "SI-T-1", confidence=0.9)])
    before = _money(fake.rows("shop_orders")[0])
    with _patched(fake):
        out = decide_focus_link(1, "SI : SI-T-1", "accept", ADMIN)
    assert out["ok"] and out["state"] == "confirmed" and out["invoice_key"] == "SI-T-1" and out["method"] == "auto_items"
    assert out["status"] == "delivered" and out["advanced"] is False and out["focus_invoice_no"] == "SI-T-1"
    link = fake.rows("shop_order_focus_links")[0]
    assert link["order_id"] == 1 and link["invoice_key"] == "SI-T-1" and link["state"] == "confirmed"
    assert link["method"] == "auto_items" and link["confidence"] == 0.9 and link["allocated_bhd"] == 12.85
    assert link["created_by"] == ADMIN and link["decided_by"] == ADMIN and link["decided_at"]
    row = fake.rows("shop_orders")[0]
    assert row["status"] == "delivered" and row["focus_invoice_no"] == "SI-T-1" and _money(row) == before
    ev = _events(fake, 1)
    assert [e["event"] for e in ev] == ["invoice"] and ev[0]["actor"] == f"focus-recon:{ADMIN}"
    assert ev[0]["detail"]["to"] == "SI-T-1" and ev[0]["detail"]["from"] is None and ev[0]["detail"]["method"] == "auto_items"
    a = fake.rows("audit_log")
    assert [x["event"] for x in a] == ["shop.focus_link"] and a[0]["user_email"] == ADMIN
    assert a[0]["detail"]["action"] == "accept" and a[0]["detail"]["invoice_key"] == "SI-T-1"
    s = fake.rows("shop_admin_audit")
    assert len(s) == 1 and s[0]["entity"] == "focus_link" and s[0]["action"] == "accept" and s[0]["entity_id"] == "1:SI-T-1"
    assert s[0]["before"] is None and s[0]["after"]["state"] == "confirmed"
    # the same accept again (a double tap) writes nothing
    n = len(fake.writes())
    with _patched(fake):
        again = decide_focus_link(1, "si-t-1", "accept", ADMIN)
    assert again["state"] == "confirmed" and len(fake.writes()) == n


@test("accept (Received order): through Confirmed to Delivered by set_status — CAS, focus-recon actor, event detail, no confirmed totals written")
def _():
    from app.shop_pipeline import decide_focus_link
    fake = _db(shop_orders=[_order(5, status="new")], shop_order_lines=_lines(5),
               v_shop_focus_candidates=[_cand(5, "SI-T-3", confidence=0.875, order_status="new")])
    before = _money(fake.rows("shop_orders")[0])
    with _patched(fake):
        out = decide_focus_link(5, "SI-T-3", "accept", ADMIN)
    assert out["status"] == "delivered" and out["advanced"] is True and out["status_label"] == "Delivered"
    row = fake.rows("shop_orders")[0]
    assert row["status"] == "delivered" and row["delivered_at"] and row["confirmed_at"]
    assert row["focus_invoice_no"] == "SI-T-3"
    assert _money(row) == before, "no money column written (the confirmed totals stay empty; readers fall back to total_bhd)"
    ev = _events(fake, 5)
    assert [e["event"] for e in ev] == ["status:confirmed", "status:delivered"]
    for e in ev:
        assert e["actor"] == f"focus-recon:{ADMIN}"
        assert e["detail"]["invoice_key"] == "SI-T-3" and e["detail"]["method"] == "auto_items"
        assert e["detail"]["confidence"] == 0.875 and e["detail"]["note"] is None
    assert ev[0]["detail"]["from"] == "new" and ev[1]["detail"]["from"] == "confirmed"
    assert ev[1]["detail"]["focus_invoice_no"] == "SI-T-3"
    # every status write was a compare-and-swap on the status just read
    ups = [c for c in fake.writes("shop_orders") if "status" in (c[2] or {})]
    assert [dict((k, v) for kind, k, v in c[3] if kind == "eq").get("status") for c in ups] == ["new", "confirmed"]
    assert fake.rows("shop_order_focus_links")[0]["state"] == "confirmed"
    assert fake.rows("audit_log")[-1]["detail"]["from_status"] == "new"
    assert fake.rows("audit_log")[-1]["detail"]["to_status"] == "delivered"


@test("accept (On the way, number already typed): Delivered, the typed number is never overwritten; Preparing goes straight to Delivered")
def _():
    from app.shop_pipeline import decide_focus_link
    fake = _db(shop_orders=[_order(6, status="out_for_delivery", focus_invoice_no="SI-T-OTHER"),
                            _order(7, status="packed")],
               shop_order_lines=_lines(6) + _lines(7),
               v_shop_focus_candidates=[_cand(6, "SI-T-6"), _cand(7, "SI-T-7", method="narration_ref", confidence=1.0)])
    with _patched(fake):
        out = decide_focus_link(6, "SI-T-6", "accept", ADMIN)
        assert out["status"] == "delivered" and out["focus_invoice_no"] == "SI-T-OTHER"
        out7 = decide_focus_link(7, "SI-T-7", "accept", ADMIN)
    assert out7["method"] == "narration_ref" and out7["confidence"] == 1.0 and out7["status"] == "delivered"
    assert [e["event"] for e in _events(fake, 7)] == ["status:delivered"]
    assert fake.rows("shop_orders")[1]["focus_invoice_no"] == "SI-T-7"


@test("accept: a cancelled order is refused and never reopened; nothing is written")
def _():
    from app.shop_pipeline import CANCELLED_LINK_MSG, decide_focus_link
    fake = _db(shop_orders=[_order(8, status="cancelled")], shop_order_lines=_lines(8),
               v_sales=[{"invoice_no": "SI : SI-T-8"}])
    with _patched(fake):
        _raises(lambda: decide_focus_link(8, "SI-T-8", "accept", ADMIN), CANCELLED_LINK_MSG)
    assert fake.writes() == [] and fake.rows("shop_orders")[0]["status"] == "cancelled"


@test("accept: a lost race (the order moved after it was read) is the CAS message and writes no link")
def _():
    import app.shop as s
    from app.shop import CAS_CONFLICT_MSG
    from app.shop_pipeline import decide_focus_link
    fake = _db(shop_orders=[_order(9, status="packed")], shop_order_lines=_lines(9),
               v_shop_focus_candidates=[_cand(9, "SI-T-9")])
    real = s.get_order
    reads = {"n": 0}

    def racing(order_id):
        o = real(order_id)
        reads["n"] += 1
        if reads["n"] == 1:           # someone cancels right after decide_focus_link read the order
            fake.rows("shop_orders")[0]["status"] = "cancelled"
        return o
    with _patched(fake):
        s.get_order = racing
        try:
            _raises(lambda: decide_focus_link(9, "SI-T-9", "accept", ADMIN), CAS_CONFLICT_MSG)
        finally:
            s.get_order = real
    assert fake.rows("shop_order_focus_links") == [] and _events(fake, 9) == []
    assert fake.rows("shop_orders")[0]["status"] == "cancelled" and fake.rows("audit_log") == []


@test("accept by hand (nobody suggested it): must be in the uploaded ledger; a failed lookup says so; method manual")
def _():
    from app.shop_pipeline import LEDGER_CHECK_MSG, decide_focus_link
    fake = _db(shop_orders=[_order(10), _order(11)], shop_order_lines=_lines(10) + _lines(11),
               v_sales=[{"invoice_no": "SI : SI-T-10"}, {"invoice_no": "SI : SI-T-100"}, {"invoice_no": "SI : XSI-T_1"}])
    with _patched(fake):
        _raises(lambda: decide_focus_link(10, "SI-T-1", "accept", ADMIN), "not in the uploaded Focus sales")
        _raises(lambda: decide_focus_link(10, "SI-T_1", "accept", ADMIN), "not in the uploaded Focus sales")
        assert fake.rows("shop_order_focus_links") == []
        out = decide_focus_link(10, "si-t-10", "accept", ADMIN, note="rep confirmed on the phone")
        assert out["method"] == "manual" and out["confidence"] is None and out["method_label"] == "Entered by hand"
        link = fake.rows("shop_order_focus_links")[0]
        assert link["method"] == "manual" and link["confidence"] is None and link["note"] == "rep confirmed on the phone"
        fake.fail[("select", "v_sales")] = RuntimeError("upstream timeout")
        _raises(lambda: decide_focus_link(11, "SI-T-100", "accept", ADMIN), LEDGER_CHECK_MSG)
    assert len(fake.rows("shop_order_focus_links")) == 1


@test("accept: a concurrent first decision on the same pair (unique violation) becomes an update, not a 500")
def _():
    from app.shop_pipeline import decide_focus_link
    fake = _db(shop_orders=[_order(12)], shop_order_lines=_lines(12), v_shop_focus_candidates=[_cand(12, "SI-T-12")])

    def lost_race():
        fake.tables["shop_order_focus_links"].append({"id": 77, "order_id": 12, "invoice_key": "SI-T-12",
                                                      "state": "rejected", "method": "auto_items"})
        return RuntimeError('{"code": "23505", "message": "duplicate key value violates unique constraint"}')
    fake.fail[("insert", "shop_order_focus_links")] = lost_race
    with _patched(fake):
        out = decide_focus_link(12, "SI-T-12", "accept", ADMIN)
    rows = fake.rows("shop_order_focus_links")
    assert len(rows) == 1 and rows[0]["id"] == 77 and rows[0]["state"] == "confirmed" and out["state"] == "confirmed"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. reject, bad input, before the migration
# ═══════════════════════════════════════════════════════════════════════════════

@test("reject: stored as rejected (never suggested again), the order untouched, idempotent; reject after accept keeps the status")
def _():
    from app.shop_pipeline import decide_focus_link
    fake = _db(shop_orders=[_order(13, status="new"), _order(14)], shop_order_lines=_lines(13) + _lines(14),
               v_shop_focus_candidates=[_cand(13, "SI-T-13", order_status="new"), _cand(14, "SI-T-14")])
    before = dict(fake.rows("shop_orders")[0])
    with _patched(fake):
        out = decide_focus_link(13, "SI-T-13", "reject", ADMIN, note="different shop")
        assert out["state"] == "rejected" and out["status"] == "new" and out["advanced"] is False
        assert fake.rows("shop_orders")[0] == before and _events(fake, 13) == []
        link = fake.rows("shop_order_focus_links")[0]
        assert link["state"] == "rejected" and link["note"] == "different shop" and link["allocated_bhd"] is None
        n = len(fake.writes())
        decide_focus_link(13, "SI-T-13", "reject", ADMIN)
        assert len(fake.writes()) == n, "the same reject again writes nothing"
        decide_focus_link(14, "SI-T-14", "accept", ADMIN)
        out = decide_focus_link(14, "SI-T-14", "reject", ADMIN)
    assert out["state"] == "rejected" and out["status"] == "delivered", "an undo never moves the order back"
    assert fake.rows("shop_orders")[1]["focus_invoice_no"] == "SI-T-14", "the order row is never rewritten"
    acts = [(a["entity_id"], a["action"]) for a in fake.rows("shop_admin_audit")]
    assert acts == [("13:SI-T-13", "reject"), ("14:SI-T-14", "accept"), ("14:SI-T-14", "reject")], acts
    assert fake.rows("shop_admin_audit")[-1]["before"]["state"] == "confirmed"


@test("decide: unknown order, bad action, empty key; before the migration a clear hint and nothing written")
def _():
    from app.shop_pipeline import LINKS_HINT, decide_focus_link
    fake = _db(shop_orders=[_order(15)], shop_order_lines=_lines(15))
    with _patched(fake):
        _raises(lambda: decide_focus_link(404, "SI-T-1", "accept", ADMIN), "Order not found.")
        _raises(lambda: decide_focus_link(15, "SI-T-1", "maybe", ADMIN), "accept or reject")
        _raises(lambda: decide_focus_link(15, "SI : ", "accept", ADMIN), "invoice number")
    assert fake.writes() == []
    fake = _db(missing={"shop_order_focus_links", "v_shop_focus_candidates"}, shop_orders=[_order(15)],
               shop_order_lines=_lines(15), v_sales=[{"invoice_no": "SI : SI-T-1"}])
    with _patched(fake):
        _raises(lambda: decide_focus_link(15, "SI-T-1", "accept", ADMIN), LINKS_HINT)
    assert fake.writes() == [] and fake.rows("shop_orders")[0]["focus_invoice_no"] is None


# ═══════════════════════════════════════════════════════════════════════════════
# 5. routes and gates
# ═══════════════════════════════════════════════════════════════════════════════

ROUTES = {("GET", "/shop/focus/candidates"), ("POST", "/shop/orders/{order_id}/focus-link")}


@test("gates: both routes are require_admin (like /shop/focus-recon); a rep gets 403 and nothing is read or written")
def _():
    from fastapi.routing import APIRoute
    from fastapi.testclient import TestClient
    import app.main as m
    from app import auth
    from app.auth import CurrentUser, get_current_user
    table = {}
    for r in m.app.routes:
        if isinstance(r, APIRoute):
            for meth in r.methods:
                table[(meth, r.path)] = [d.call for d in r.dependant.dependencies]
    for key in ROUTES | {("GET", "/shop/focus-recon")}:
        assert key in table, f"route missing: {key}"
        assert auth.require_admin in table[key], f"{key} must be admin-gated"
    m.app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id="r", email="rep@example.com", role="salesman")
    try:
        fake = _db(shop_orders=[_order(1, status="new")], shop_order_lines=_lines(1),
                   v_shop_focus_candidates=[_cand(1, "SI-T-1")])
        with _patched(fake):
            c = TestClient(m.app)
            calls = [c.get("/shop/focus/candidates"),
                     c.post("/shop/orders/1/focus-link", json={"invoice_key": "SI-T-1", "action": "accept"})]
            assert [r.status_code for r in calls] == [403, 403], [(r.status_code, r.text[:80]) for r in calls]
        assert fake.writes() == [] and not [x for x in fake.calls if x[1] in ("v_shop_focus_candidates", "shop_orders")]
    finally:
        m.app.dependency_overrides.pop(get_current_user, None)


@test("routes: an admin lists suggestions and accepts one (200, Delivered, no merchant message); 404 / 409 / 400; hint before the migration")
def _():
    from fastapi.testclient import TestClient
    import app.main as m
    import app.shop_notify as notify
    from app.auth import CurrentUser, get_current_user
    m.app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id="a", email=ADMIN, role="admin")
    saved = notify.notify_status
    sent: list = []
    notify.notify_status = lambda *a, **k: sent.append(a)
    try:
        fake = _db(shop_orders=[_order(1, status="new"), _order(2, status="cancelled")],
                   shop_order_lines=_lines(1) + _lines(2),
                   v_shop_focus_candidates=[_cand(1, "SI-T-1", order_status="new"), _cand(1, "SI-T-2", rank=2)])
        with _patched(fake):
            c = TestClient(m.app)
            r = c.get("/shop/focus/candidates")
            assert r.status_code == 200, r.text[:200]
            body = r.json()
            assert body["count"] == 1 and [x["invoice_key"] for x in body["orders"][0]["candidates"]] == ["SI-T-1", "SI-T-2"]
            r = c.post("/shop/orders/1/focus-link", json={"invoice_key": "SI : SI-T-1", "action": "accept"})
            assert r.status_code == 200, r.text[:200]
            j = r.json()
            assert j["status"] == "delivered" and j["advanced"] and j["invoice_key"] == "SI-T-1" and j["state"] == "confirmed"
            assert sent == [], "an accept never messages the merchant"
            r = c.post("/shop/orders/1/focus-link", json={"invoice_key": "SI-T-2", "action": "reject"})
            assert r.status_code == 200 and r.json()["state"] == "rejected"
            r = c.post("/shop/orders/404/focus-link", json={"invoice_key": "SI-T-1", "action": "accept"})
            assert r.status_code == 404
            r = c.post("/shop/orders/1/focus-link", json={"invoice_key": "SI-T-1", "action": "later"})
            assert r.status_code == 400 and "accept or reject" in r.json()["detail"]
            r = c.post("/shop/orders/2/focus-link", json={"invoice_key": "SI-T-1", "action": "accept"})
            assert r.status_code == 400 and "cancelled" in r.json()["detail"]
            r = c.post("/shop/orders/1/focus-link", json={"invoice_key": "S" * 60, "action": "accept"})
            assert r.status_code == 422, "the key is capped like the invoice field"
        # a lost compare-and-swap is a 409 so the drawer can say "refresh"
        import app.shop as s
        fake = _db(shop_orders=[_order(3, status="packed")], shop_order_lines=_lines(3),
                   v_shop_focus_candidates=[_cand(3, "SI-T-3")])
        real = s.get_order
        n = {"reads": 0}

        def racing(order_id):
            o = real(order_id)
            n["reads"] += 1
            if n["reads"] == 1:
                fake.rows("shop_orders")[0]["status"] = "delivered"
            return o
        with _patched(fake):
            s.get_order = racing
            try:
                r = TestClient(m.app).post("/shop/orders/3/focus-link", json={"invoice_key": "SI-T-3", "action": "accept"})
            finally:
                s.get_order = real
            assert r.status_code == 409, r.text[:200]
        fake = _db(missing={"v_shop_focus_candidates", "shop_order_focus_links"}, shop_orders=[_order(1)],
                   shop_order_lines=_lines(1))
        with _patched(fake):
            c = TestClient(m.app)
            r = c.get("/shop/focus/candidates")
            assert r.status_code == 200 and r.json()["orders"] == [] and "r7_focus_links_migration" in r.json()["hint"]
            r = c.post("/shop/orders/1/focus-link", json={"invoice_key": "SI-T-1", "action": "reject"})
            assert r.status_code == 400 and "r7_focus_links_migration" in r.json()["detail"]
    finally:
        notify.notify_status = saved
        m.app.dependency_overrides.pop(get_current_user, None)


@test("recon list: the R7a columns pass through (and read as empty before the migration)")
def _():
    from app.shop_pipeline import focus_recon
    base = {"order_id": 1, "order_no": "YQ-1", "delivered_at": "2026-09-22", "customer_shop": "A", "salesman_name": "Rep One",
            "salesman_focus_name": "Rep One", "order_total_bhd": 1.5, "payment_status": "unpaid", "focus_invoice_no": "SI-T-2",
            "invoice_total_bhd": 17.8, "invoice_date": "2026-09-22", "focus_salesman": "Rep One - Acc WH",
            "missing_invoice": False, "invoice_not_found": False, "salesman_mismatch": False, "amount_mismatch": False,
            "amount_diff_bhd": 0, "is_test": False, "invoice_reused": False}
    fake = _db(v_shop_focus_recon=[dict(base, invoice_keys="SI-T-2", link_state="confirmed", linked_orders_n=2,
                                        linked_orders_total_bhd=17.8), dict(base, order_id=2, order_no="YQ-2")])
    with _patched(fake):
        r = focus_recon()
    by = {x["order_no"]: x for x in r["rows"]}
    a, b = by["YQ-1"], by["YQ-2"]
    assert a["flags"] == [] and a["invoice_keys"] == "SI-T-2" and a["link_state"] == "confirmed"
    assert a["linked_orders_n"] == 2 and a["linked_orders_total_bhd"] == 17.8
    assert b["invoice_keys"] is None and b["linked_orders_n"] == 0 and b["linked_orders_total_bhd"] is None


# ═══════════════════════════════════════════════════════════════════════════════
# 6. the migration and its reverse, as text
# ═══════════════════════════════════════════════════════════════════════════════

R3_RECON_COLUMNS = ["order_id", "order_no", "status", "delivered_at", "customer_shop", "salesman_id", "salesman_name",
                    "salesman_focus_name", "order_total_bhd", "payment_status", "focus_invoice_no", "invoice_total_bhd",
                    "invoice_net_bhd", "focus_salesman", "focus_customer", "invoice_date", "missing_invoice",
                    "invoice_not_found", "salesman_mismatch", "amount_mismatch", "amount_diff_bhd", "is_test",
                    "invoice_reused"]
R7_APPENDED = ["linked_orders_n", "linked_orders_total_bhd", "invoice_keys", "link_state"]


def _view_body(sql: str, view: str) -> str:
    m = re.search(rf"create (?:or replace )?view {view} as\n(.*?);\n", sql, re.S | re.I)
    assert m, view
    return m.group(1)


def _output_columns(body: str) -> list[str]:
    """The output column names of a view body, in order: the top-level (depth-0) SELECT list.
    Comments go first (one in the recon's list holds a comma inside parentheses)."""
    body = re.sub(r"--[^\n]*", "", body)
    depth, start, i = 0, None, 0
    low = body.lower()
    while i < len(low):
        ch = low[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and low.startswith("select", i) and (i == 0 or not low[i - 1].isalnum()):
            start = i + len("select")
        elif depth == 0 and start is not None and low.startswith("\nfrom ", i):
            items, buf, d = [], "", 0
            for c in body[start:i]:
                if c == "(":
                    d += 1
                elif c == ")":
                    d -= 1
                if c == "," and d == 0:
                    items.append(buf)
                    buf = ""
                else:
                    buf += c
            items.append(buf)
            out = []
            for it in items:
                it = re.sub(r"--[^\n]*", "", it).strip()
                m = re.search(r"\bas\s+([a-z_][a-z0-9_]*)\s*$", it, re.I)
                out.append(m.group(1) if m else it.split(".")[-1].strip())
            return out
        i += 1
    raise AssertionError("no top-level select list")


@test("migration: additive table with checks + RLS + revokes (sequence too), no GRANT / CASCADE / security_invoker / row writes, a self-check")
def _():
    sql = MIGRATION.read_text(encoding="utf-8")
    low = sql.lower()
    assert "create table if not exists shop_order_focus_links" in low
    assert "references shop_orders(id)" in low and "on delete cascade" not in low
    for col in ("invoice_key    text          not null", "sio_key", "confidence     numeric(4,3)",
                "allocated_bhd  numeric(12,3)", "state          text          not null default 'suggested'"):
        assert col in low, col
    assert "unique (order_id, invoice_key)" in low
    for chk in ("('narration_ref', 'sio_ref', 'auto_items', 'manual')", "('suggested', 'confirmed', 'rejected')"):
        assert chk in low, chk
    assert "alter table shop_order_focus_links enable row level security" in low
    for obj in ("shop_order_focus_links", "sequence shop_order_focus_links_id_seq", "v_shop_focus_recon",
                "v_shop_focus_candidates"):
        assert f"revoke all on {obj} from anon, authenticated" in low, obj
    assert not re.search(r"^\s*grant\s", low, re.M), "no GRANT (service role only; never yq_readonly: customer names)"
    assert "cascade" not in re.sub(r"--[^\n]*", "", low), "never CASCADE"
    assert "security_invoker" not in low
    code = re.sub(r"--[^\n]*", "", low)
    assert not re.search(r"^\s*(insert into|update\s+\w+\s+set|delete from|truncate)\b", code, re.M), \
        "no row is written by the migration"
    assert low.count("do $$") == 1 and "raise exception" in low.split("self-check")[-1]
    assert not re.search(r"^\s*(commit|end)\s*;", low, re.M), "rehearsable (no COMMIT)"
    check = low.split("self-check")[-1]
    for name in ("shop_order_focus_links", "v_shop_focus_candidates", "yq_readonly", "anon"):
        assert name in check, name


@test("migration: v_shop_focus_recon keeps its 23 columns in order and appends 4; the rep is compared suffix-aware; many orders per invoice")
def _():
    sql = MIGRATION.read_text(encoding="utf-8")
    body = _view_body(sql, "v_shop_focus_recon")
    cols = _output_columns(body)
    assert cols == R3_RECON_COLUMNS + R7_APPENDED, cols
    r3 = (ROOT / "scripts" / "r3_pipeline_migration.sql").read_text(encoding="utf-8")
    assert _output_columns(_view_body(r3, "v_shop_focus_recon")) == R3_RECON_COLUMNS
    low = body.lower()
    assert "coalesce(o.total_confirmed_bhd, o.total_bhd)      as order_total_bhd" in low, \
        "same expression, same type (numeric(12,3)) — CREATE OR REPLACE refuses a type change"
    assert "starts_with(lower(coalesce(i.focus_salesman, ''))" in low and "|| ' - ')" in low
    assert "shop_order_focus_links" in low and "l.state = 'confirmed'" in low and "r.state = 'rejected'" in low
    assert "g.orders_total_bhd" in low, "the invoice total is compared with the sum of the orders on it"


@test("migration: v_shop_focus_candidates scores the three tiers, the window, the 60 % share, the cap, rejected pairs and full allocation")
def _():
    low = _view_body(MIGRATION.read_text(encoding="utf-8"), "v_shop_focus_candidates").lower()
    assert "o.status in ('new', 'confirmed', 'packed', 'out_for_delivery', 'delivered')" in low
    assert "not coalesce(o.is_test, false)" in low and "l.state = 'confirmed'" in low
    assert "when 'narration_ref' then 1.000 when 'sio_ref' then 0.950" in low and "least(0.900, t.overlap_share)" in low
    assert "sc.overlap_share >= 0.6" in low and ">= 0.6 * s.skus" in low
    assert "between o.created_day - 1 and o.created_day + 7" in low
    assert "between s.sio_date and s.sio_date + 2" in low
    assert "voucher_type = 'stock issue voucher'" in low
    assert "r.state = 'rejected'" in low and "i.invoice_total_bhd - coalesce(a.linked_bhd, 0) <= 0.005" in low
    assert "at time zone 'asia/bahrain'" in low
    for col in ("lines_matched", "lines_qty_diff", "lines_price_diff", "lines_missing_on_invoice",
                "lines_extra_on_invoice", "invoice_orders_n", "rank", "sio_key", "method", "confidence"):
        assert col in _output_columns(_view_body(MIGRATION.read_text(encoding="utf-8"), "v_shop_focus_candidates")), col
    assert "narration" not in _output_columns(_view_body(MIGRATION.read_text(encoding="utf-8"), "v_shop_focus_candidates")), \
        "the free-text narration (it can carry ID numbers) never leaves the view"


@test("reverse: drops the candidates view and the table, restores the R3 recon verbatim, never CASCADE / row deletes")
def _():
    rev = REVERSE.read_text(encoding="utf-8")
    rl = rev.lower()
    for stmt in ("drop view if exists v_shop_focus_candidates", "drop view if exists v_shop_focus_recon",
                 "drop table if exists shop_order_focus_links", "revoke all on v_shop_focus_recon from anon, authenticated"):
        assert stmt in rl, stmt
    assert rl.index("drop view if exists v_shop_focus_recon") < rl.index("drop table if exists shop_order_focus_links")
    code = re.sub(r"--[^\n]*", "", rl)
    assert "cascade" not in code and "delete from" not in code and "truncate" not in code and "update " not in code
    r3 = (ROOT / "scripts" / "r3_pipeline_migration.sql").read_text(encoding="utf-8")
    norm = lambda s: re.sub(r"\s+", " ", s).strip()  # noqa: E731
    assert norm(_view_body(rev, "v_shop_focus_recon")) == norm(_view_body(r3, "v_shop_focus_recon")), \
        "the reverse re-creates exactly the R3 view"
    assert "do $$" in rl and "raise exception" in rl


# ═══════════════════════════════════════════════════════════════════════════════
# 7. local Postgres replay (SKIPs cleanly without a local cluster)
# ═══════════════════════════════════════════════════════════════════════════════

# Any scratch database will do: everything runs inside ONE transaction that is rolled back. On the
# drill cluster's schema copy (tests/test_r3_pipeline.py) the real tables are used; on an empty
# database a minimal synthetic schema is created first (v_sales is taken from its canonical file).
LOCAL_DSN = os.environ.get("YQ_LOCAL_PG_R7", os.environ.get("YQ_LOCAL_PG", "postgresql://postgres@localhost:55432/r3_pipeline"))

MINIMAL_SCHEMA = """
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'yq_readonly') then create role yq_readonly nologin; end if;
end $$;
create table salesmen (id bigint generated by default as identity primary key, name text not null unique,
  focus_name text, referral_code text not null unique, is_active boolean not null default true, user_email text);
create table shop_orders (id bigint generated by default as identity primary key, order_no text not null unique,
  token text not null unique, status text not null default 'new', customer_name text not null,
  customer_phone text not null, customer_shop text, salesman_id bigint references salesmen(id), salesman_name text,
  subtotal_bhd numeric(12,3) not null default 0, total_bhd numeric(12,3) not null default 0,
  subtotal_confirmed_bhd numeric(12,3), total_confirmed_bhd numeric(12,3), payment_status text default 'unpaid',
  focus_invoice_no text, is_test boolean not null default false, created_at timestamptz default now(),
  delivered_at timestamptz);
create table shop_order_lines (id bigint generated by default as identity primary key,
  order_id bigint not null references shop_orders(id) on delete cascade, item_code text not null,
  qty integer not null check (qty > 0), unit_price_bhd numeric(12,3) not null, line_total_bhd numeric(12,3) not null,
  backorder boolean not null default false, qty_confirmed integer, line_status text, unit_price_confirmed numeric(12,3));
create table categories (id bigint generated by default as identity primary key, name text, division text);
create table products (id bigint generated by default as identity primary key, sku_code text unique not null,
  item_name text, category_id bigint references categories(id));
create table product_aliases (id bigint generated by default as identity primary key,
  product_id bigint references products(id), alias_text text unique not null);
create table orders (id bigint generated by default as identity primary key, invoice_no text unique not null,
  order_date date, customer_name text, salesman text, payment_mode text, sales_account_name text);
create table order_lines (id bigint generated by default as identity primary key, invoice_no text not null,
  order_id bigint references orders(id), line_no int, line_date date, customer_account text, item_name text,
  quantity numeric, rate_bhd numeric, gross_bhd numeric, discount_bhd numeric, taxable_bhd numeric,
  vat_amount_bhd numeric, total_amount_bhd numeric, warehouse_name text, narration text, unique (invoice_no, line_no));
create table salesman_channels (salesman text primary key, channel text);
create table stock_movements (id bigint generated by default as identity primary key, item_name text not null,
  product_id bigint, move_date date, voucher text, voucher_type text, issued_qty numeric, received_qty numeric,
  warehouse_name text, to_warehouse_name text, narration text, row_hash text);
create table catalog_stock_map (item_code text primary key, stock_item_name text not null);
"""


def _v_sales_ddl() -> str:
    sql = (ROOT / "scripts" / "division_payment_migration.sql").read_text(encoding="utf-8")
    m = re.search(r"create or replace view v_sales as\n.*?;\n", sql, re.S | re.I)
    assert m
    return m.group(0)


def _seed(cur) -> None:
    """Made-up reps, shops, SKUs, quantities and prices, arranged in the shapes the real proof showed:
    one-line orders, two orders on one invoice, an order with backorder lines, identical orders from two
    shops, order numbers in a note, a stock issue naming an order, typed numbers."""
    cur.execute("""
        insert into salesmen (id, name, referral_code, focus_name) overriding system value values
          (9701, 'Rep K', 'rep-k-r7', 'Rep K'), (9702, 'Rep F', 'rep-f-r7', 'Rep F'),
          (9703, 'Rep A', 'rep-a-r7', 'Rep A'), (9704, 'Rep M', 'rep-m-r7', 'Rep M')""")
    orders = [
        # id,  order_no,        status,     rep,  total,  created (Bahrain time), typed, test
        (9801, 'YQ-2609-9801', 'delivered', 9701, 2.000, '2026-09-21 10:25+03', None, False),
        (9802, 'YQ-2609-9802', 'delivered', 9701, 1.500, '2026-09-21 10:33+03', None, False),
        (9803, 'YQ-2609-9803', 'delivered', 9701, 16.300, '2026-09-21 10:38+03', None, False),
        (9806, 'YQ-2609-9806', 'delivered', 9701, 1.500, '2026-09-22 08:01+03', None, False),
        (9805, 'YQ-2609-9805', 'new', 9702, 14.000, '2026-09-21 13:07+03', None, False),
        (9809, 'YQ-2609-9809', 'new', 9702, 4.300, '2026-09-22 10:13+03', None, False),
        (9812, 'YQ-2609-9812', 'confirmed', 9703, 5.000, '2026-09-22 09:00+03', None, False),
        (9813, 'YQ-2609-9813', 'packed', 9703, 4.000, '2026-09-22 09:10+03', None, False),
        (9814, 'YQ-2609-9814', 'out_for_delivery', 9703, 6.000, '2026-09-22 09:20+03', None, False),
        (9815, 'YQ-2609-9815', 'delivered', 9701, 9.000, '2026-09-22 09:30+03', 'si : si-t-6', False),
        (9816, 'YQ-2609-9816', 'delivered', 9701, 9.000, '2026-09-22 09:40+03', 'SI-T-6', False),
        (9817, 'YQ-2609-9817', 'delivered', 9701, 3.000, '2026-09-22 09:50+03', 'SI-T-7', False),
        (9818, 'YQ-2609-9818', 'delivered', 9701, 2.000, '2026-09-21 11:00+03', None, True),     # test order
        (9819, 'YQ-2609-9819', 'cancelled', 9701, 2.000, '2026-09-21 11:05+03', None, False),
    ]
    for oid, no, st, rep, tot, created, typed, is_test in orders:
        cur.execute("""
            insert into shop_orders (id, order_no, token, status, customer_name, customer_phone, customer_shop,
                                     salesman_id, salesman_name, subtotal_bhd, total_bhd, total_confirmed_bhd,
                                     focus_invoice_no, is_test, created_at, delivered_at)
            overriding system value
            values (%s, %s, %s, %s, 'Synthetic', '97300000000', %s, %s, (select name from salesmen where id = %s),
                    %s, %s, %s, %s, %s, %s::timestamptz, case when %s = 'delivered' then now() end)""",
                    (oid, no, f"tok-r7-{oid}".ljust(24, "x"), st, f"Shop {oid}", rep, rep, tot, tot,
                     tot if st in ("delivered", "confirmed", "packed", "out_for_delivery") else None,
                     typed, is_test, created, st))
    lines = [
        (9801, "SKU-A", 4, 0.500, False), (9802, "SKU-B 1M", 5, 0.300, False),
        (9803, "SKU-C", 2, 1.000, False), (9803, "SKU-D", 3, 0.800, False), (9803, "SKU-E", 1, 2.500, False),
        (9803, "SKU-F", 4, 0.250, False), (9803, "SKU-G", 2, 1.200, False), (9803, "SKU-H", 5, 0.600, False),
        (9803, "SKU-J", 1, 3.000, False),
        (9806, "SKU-B 1M", 5, 0.300, False),                                       # same lines as 9802
        (9805, "SKU-K", 2, 1.100, False), (9805, "SKU-L", 2, 0.900, False), (9805, "SKU-M", 1, 1.300, False),
        (9805, "SKU-N", 2, 1.700, False), (9805, "SKU-P", 1, 2.100, True), (9805, "SKU-Q", 2, 1.600, True),  # backorder
        (9809, "SKU-M", 2, 1.300, False), (9809, "SKU-N", 1, 1.700, False),
        (9812, "Z01", 1, 5.000, False), (9813, "Z02", 1, 4.000, False), (9814, "Z03", 1, 6.000, False),
        (9815, "SKU-R", 3, 3.000, False), (9816, "SKU-R", 3, 3.000, False), (9817, "SKU-S", 1, 3.000, False),
        (9818, "SKU-A", 4, 0.500, False), (9819, "SKU-A", 4, 0.500, False),
    ]
    for oid, code, qty, price, bo in lines:
        cur.execute("""insert into shop_order_lines (order_id, item_code, qty, unit_price_bhd, line_total_bhd, backorder, line_status)
                       values (%s, %s, %s, %s, %s, %s, %s)""",
                    (oid, code, qty, price, round(qty * price, 3), bo, "backorder" if bo else "ok"))
    skus = ["SKU-A", "SKU-B 1M", "SKU-C", "SKU-D", "SKU-E", "SKU-F", "SKU-G", "SKU-H", "SKU-J", "SKU-K", "SKU-L",
            "SKU-M", "SKU-N", "SKU-R", "SKU-S", "A1", "B1", "C1", "Z01", "Z02"]
    for i, sku in enumerate(skus):
        cur.execute("insert into products (id, sku_code, item_name) overriding system value values (%s, %s, %s)",
                    (97000 + i, sku, f"{sku} synthetic item"))
        cur.execute("insert into product_aliases (product_id, alias_text) values (%s, %s)",
                    (97000 + i, f"{sku} Synthetic Item (VFAN)"))
    invoices = [
        # key, date, Focus salesman, narration, [(sku, qty, rate)]
        ("SI-T-1", "2026-09-22", "Rep K - Acc WH", None, [("SKU-A", 4, 0.5)]),
        ("SI-T-2", "2026-09-22", "Rep K - Acc WH", None,           # 9802 + 9803 on one invoice (17.800)
         [("SKU-B 1M", 5, 0.3), ("SKU-C", 2, 1.0), ("SKU-D", 3, 0.8), ("SKU-E", 1, 2.5), ("SKU-F", 4, 0.25),
          ("SKU-G", 2, 1.2), ("SKU-H", 5, 0.6), ("SKU-J", 1, 3.0)]),
        ("SI-T-3", "2026-09-23", "Rep F - Acc WH", None,           # 9805 + 9809 on one invoice
         [("SKU-K", 2, 1.1), ("SKU-L", 2, 0.9), ("SKU-M", 3, 1.3), ("SKU-N", 3, 1.7)]),
        ("SI-T-4", "2026-09-23", "Rep X - Acc WH", "Orders YQ-2609-9812, yq 2609 9813", [("A1", 1, 5.0)]),
        ("SI-T-5", "2026-09-24", "Rep M - Acc WH", None, [("A1", 2, 1.0), ("B1", 1, 2.0), ("C1", 1, 3.0)]),
        ("SI-T-6", "2026-09-22", "Rep K - Acc WH", None, [("SKU-R", 3, 3.0)]),
        ("SI-T-7", "2026-09-22", "Rep Q - SIM WH", None, [("SKU-S", 1, 3.0)]),
        ("SI-T-8", "2026-09-22", "Rep K - Acc WH", None, [("SKU-A", 4, 0.5)]),   # a decoy for 9801, rejected below
    ]
    for key, day, rep, narr, items in invoices:
        cur.execute("insert into orders (invoice_no, order_date, customer_name, salesman) values (%s, %s, %s, %s)",
                    (f"SI : {key}", day, f"Focus customer {key}", rep))
        for n, (sku, qty, rate) in enumerate(items, 1):
            cur.execute("""insert into order_lines (invoice_no, line_no, line_date, item_name, quantity, rate_bhd,
                                                    gross_bhd, warehouse_name, narration)
                           values (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (f"SI : {key}", n, day, f"{sku} Synthetic Item (VFAN)", qty, rate, round(qty * rate, 3), rep,
                         narr if n == 1 else None))
    # a Stock Issue Voucher naming 9814 moves A1 + B1 to Rep M; Rep M's SI-T-5 follows a day later
    for item in ("A1", "B1"):
        cur.execute("""insert into stock_movements (item_name, move_date, voucher, voucher_type, issued_qty,
                                                    warehouse_name, to_warehouse_name, narration)
                       values (%s, '2026-09-23', 'SIO:T-900', 'Stock Issue Voucher', 1, 'Main Warehouse',
                               'Rep M - Acc WH', 'for YQ-2609-9814')""", (f"{item} Synthetic Item (VFAN)",))


@test("local replay: tiers, ranks, many-to-many, rejected pairs, the fixed recon, Python↔SQL parity, self-check and reverse (SKIP without the cluster)")
def _():
    try:
        import psycopg
    except ImportError:
        print("  SKIP: psycopg not installed")
        return
    try:
        conn = psycopg.connect(LOCAL_DSN, connect_timeout=2)
    except Exception as e:  # noqa: BLE001 — no local cluster / no scratch database here
        print(f"  SKIP: local Postgres unreachable ({type(e).__name__})")
        return
    from app.shop_pipeline import clean_invoice_no
    try:
        cur = conn.cursor()
        cur.execute("select to_regclass('public.shop_orders'), to_regclass('public.shop_order_focus_links')")
        have_orders, have_links = cur.fetchone()
        if have_links is not None:
            print("  SKIP: this database already has shop_order_focus_links (the replay applies the migration itself)")
            return
        if have_orders is None:
            cur.execute(MINIMAL_SCHEMA)
            cur.execute(_v_sales_ddl())
        cur.execute("select to_regclass('public.v_shop_focus_recon')")
        if cur.fetchone()[0] is None:
            # the R3 view first, so the migration's CREATE OR REPLACE proves it keeps every column's type
            r3 = (ROOT / "scripts" / "r3_pipeline_migration.sql").read_text(encoding="utf-8")
            cur.execute("create view v_shop_focus_recon as\n" + _view_body(r3, "v_shop_focus_recon"))
        mig = MIGRATION.read_text(encoding="utf-8")
        cur.execute(mig)                                  # the self-check runs on the empty tables
        _seed(cur)
        # a rejected decoy pair, then read the suggestions
        cur.execute("""insert into shop_order_focus_links (order_id, invoice_key, method, state, created_by)
                       values (9801, 'SI-T-8', 'auto_items', 'rejected', 'r7@example.com')""")

        def floats(rows):
            return [tuple(float(v) if isinstance(v, Decimal) else v for v in r) for r in rows]

        def cands():
            t = time.perf_counter()
            cur.execute("""select order_no, invoice_key, method, confidence, rank, sio_key, lines_matched,
                                  lines_qty_diff, lines_missing_on_invoice, lines_extra_on_invoice, invoice_orders_n,
                                  overlap_share
                           from v_shop_focus_candidates where order_no like 'YQ-2609-98%%' order by order_no, rank""")
            rows = floats(cur.fetchall())
            return rows, (time.perf_counter() - t) * 1000
        rows, ms = cands()
        assert ms < 500, f"candidates took {ms:.0f} ms"
        top = {r[0]: r for r in rows if r[4] == 1}
        allp = {(r[0], r[1]): r for r in rows}
        # auto_items: same rep (suffix), window, >= 60 % of the SKUs; exact one-liners rank 1
        assert top["YQ-2609-9801"][1:4] == ("SI-T-1", "auto_items", 0.9) and top["YQ-2609-9801"][6] == 1, top["YQ-2609-9801"]
        assert ("YQ-2609-9801", "SI-T-8") not in allp, "a rejected pair is never suggested again"
        assert top["YQ-2609-9802"][1] == "SI-T-2" and top["YQ-2609-9803"][1] == "SI-T-2", "two orders, one invoice"
        assert top["YQ-2609-9803"][6] == 7 and top["YQ-2609-9803"][9] == 1 and top["YQ-2609-9803"][10] >= 2
        assert top["YQ-2609-9806"][1] == "SI-T-2", "on items alone 9806 looks like 9802 — only a person / the note can tell"
        # the share counts the lines expected to ship: 9805's two backorder lines do not dilute it
        r5 = top["YQ-2609-9805"]
        assert r5[1] == "SI-T-3" and float(r5[11]) == 1.0 and r5[8] == 2 and r5[7] == 2, r5
        assert top["YQ-2609-9809"][1] == "SI-T-3"
        # narration_ref: two order numbers in one note, written two ways, another rep, no item overlap
        for no in ("YQ-2609-9812", "YQ-2609-9813"):
            assert top[no][1:4] == ("SI-T-4", "narration_ref", 1.0), top.get(no)
        # sio_ref: the stock issue names 9814; the SI by the same rep (another rep than the order's) follows
        assert top["YQ-2609-9814"][1:4] == ("SI-T-5", "sio_ref", 0.95) and top["YQ-2609-9814"][5] == "SIO:T-900"
        # the typed number ranks first; a test order and a cancelled order get nothing
        assert top["YQ-2609-9815"][1] == "SI-T-6" and top["YQ-2609-9816"][1] == "SI-T-6"
        assert "YQ-2609-9818" not in top and "YQ-2609-9819" not in top
        # the recon before any decision: typed numbers normalised, suffix-aware rep, two typed orders on one invoice
        cur.execute("""select order_no, focus_invoice_no, missing_invoice, invoice_not_found, salesman_mismatch,
                              amount_mismatch, amount_diff_bhd, invoice_reused, linked_orders_n, link_state
                       from v_shop_focus_recon where order_no like 'YQ-2609-98%%' order by order_no""")
        rec = {r[0]: r[1:] for r in floats(cur.fetchall())}
        assert rec["YQ-2609-9815"] == ("SI-T-6", False, False, False, True, -9.0, True, 2, "typed"), rec["YQ-2609-9815"]
        assert rec["YQ-2609-9817"][:4] == ("SI-T-7", False, False, True), "Rep Q's invoice on Rep K's order"
        assert rec["YQ-2609-9801"][:2] == (None, True) and "YQ-2609-9818" in rec and "YQ-2609-9819" not in rec
        # accept 9802 + 9803 on SI-T-2 and 9815 on SI-T-6; reject 9816's typed SI-T-6
        cur.execute("""insert into shop_order_focus_links (order_id, invoice_key, method, confidence, state, created_by) values
                         (9802, 'SI-T-2', 'auto_items', 0.9, 'confirmed', 'r7@example.com'),
                         (9803, 'SI-T-2', 'auto_items', 0.9, 'confirmed', 'r7@example.com'),
                         (9815, 'SI-T-6', 'auto_items', 0.9, 'confirmed', 'r7@example.com'),
                         (9816, 'SI-T-6', 'manual', null, 'rejected', 'r7@example.com')""")
        rows, _ = cands()
        pairs = {(r[0], r[1]) for r in rows}
        assert not {p for p in pairs if p[0] in ("YQ-2609-9802", "YQ-2609-9803", "YQ-2609-9815")}, "linked orders leave the list"
        assert ("YQ-2609-9806", "SI-T-2") not in pairs, "SI-T-2 is fully covered by confirmed links: not offered to 9806"
        assert ("YQ-2609-9816", "SI-T-6") not in pairs
        cur.execute("""select order_no, focus_invoice_no, missing_invoice, amount_mismatch, amount_diff_bhd, invoice_reused,
                              linked_orders_n, linked_orders_total_bhd, link_state, salesman_mismatch
                       from v_shop_focus_recon where order_no like 'YQ-2609-98%%' order by order_no""")
        rec = {r[0]: r[1:] for r in floats(cur.fetchall())}
        for no in ("YQ-2609-9802", "YQ-2609-9803"):
            assert rec[no][:6] == ("SI-T-2", False, False, 0, False, 2), rec[no]
            assert float(rec[no][6]) == 17.8 and rec[no][7] == "confirmed" and rec[no][8] is False, rec[no]
        assert rec["YQ-2609-9815"][:5] == ("SI-T-6", False, False, 0, False), rec["YQ-2609-9815"]
        assert rec["YQ-2609-9816"][:2] == (None, True), "a rejected typed number stops counting (the row keeps it)"
        cur.execute("select focus_invoice_no from shop_orders where id = 9816")
        assert cur.fetchone()[0] == "SI-T-6"
        # the invoice-key CHECK: only the normalised form is stored
        cur.execute("savepoint k")
        try:
            cur.execute("insert into shop_order_focus_links (order_id, invoice_key, method) values (9806, 'SI : SI-T-2', 'manual')")
            raise AssertionError("a raw 'SI : …' key must be refused")
        except psycopg.Error as e:
            assert "invoice_key" in str(e), str(e)
            cur.execute("rollback to savepoint k")
        # Python and SQL normalise identically
        for raw, _want in NORMALISE_CASES:
            if raw is None:
                continue
            cur.execute(r"select nullif(upper(regexp_replace(trim(%s), '^SI\s*:\s*', '', 'i')), '')", (raw,))
            assert cur.fetchone()[0] == clean_invoice_no(raw), raw
        # the self-check holds on the seeded data too
        cur.execute(mig[mig.lower().index("do $$"):])
        # grants: nothing for anon / authenticated / yq_readonly
        cur.execute("""select count(*) from information_schema.role_table_grants where table_schema = 'public'
                         and table_name in ('shop_order_focus_links', 'v_shop_focus_recon', 'v_shop_focus_candidates')
                         and grantee in ('anon', 'authenticated', 'yq_readonly')""")
        assert cur.fetchone()[0] == 0
        # the reverse puts the R3 view back and drops the rest; the orders are untouched
        cur.execute("select md5(string_agg(row(o.*)::text, '|' order by id)) from shop_orders o where id between 9801 and 9819")
        digest = cur.fetchone()[0]
        cur.execute(REVERSE.read_text(encoding="utf-8"))
        cur.execute("select to_regclass('public.shop_order_focus_links'), to_regclass('public.v_shop_focus_candidates')")
        assert cur.fetchone() == (None, None)
        cur.execute("""select count(*) from information_schema.columns
                       where table_schema = 'public' and table_name = 'v_shop_focus_recon'""")
        assert cur.fetchone()[0] == 23
        cur.execute("select md5(string_agg(row(o.*)::text, '|' order by id)) from shop_orders o where id between 9801 and 9819")
        assert cur.fetchone()[0] == digest
        print(f"  replay: candidates in {ms:.1f} ms on {LOCAL_DSN.rsplit('/', 1)[-1]}")
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
