"""R7e — price book 41 (effective 28-Sep-2026): open orders get the cuts, and the price in force is read.

    python -m tests.test_r7e_pricebook

Same lightweight runner as tests/test_r7c_order_heart.py, and ITS fakes (imported, not copied): the
in-memory PostgREST stand-in with CAS filters, failure injection and a racing-writer hook, and the
catalog context served from memory. The price-change rows come from a stub of the script's one SQL
seam; backups go to a temp folder. Nothing here reaches a database or the network (CI:
SUPABASE_URL=https://ci.invalid). All data is synthetic — made-up shops, reps, items and numbers.

Covered:
  * scripts/apply_price_drops_to_open_orders (the R7e spec's 12 cases): a dry run writes nothing;
    --commit writes the CONFIRMED layer only (the placed prices and totals stay), one 'amended'
    event the shop sees and one audit row; nothing is ever raised (today's price above the lock, a
    tier line whose engine price is not lower, a line already on the new book); delivered /
    cancelled / test / invoiced / paid orders are left alone, a packed one is lowered; the cart
    discount re-shared pro rata and never grown; a cut under the minimum reported, never cancelled;
    the header compare-and-swap loses to a racing writer; a failed event puts header and lines back;
    a re-run is a no-op; a later Confirm / Amend keeps the lowered price; --reverse puts it back and
    skips an order touched since; 0 cuts / an --expect-cuts mismatch stop before any write; and
    --verify-load's read-only checks. The review fixes: only the cut moves a total (a total on file
    its lines do not add up to, or missing lines, is skipped; lines are read per order past
    PostgREST's 1,000 rows); a Received order the cut takes under the minimum waits for the rep's
    Confirm; --reverse exits 1 unless every order went back; a confirmed Focus link / a Focus
    suggestion holds an order back (--include overrides a suggestion); the offer ledger measures a
    lowered line against its moved list;
  * the price in force — app/orders.py, app/order_verify.py, app/agents.py and app/bi_simulator.py
    read v_price_list_by_book (never MAX(rate_bhd) over selling_prices as today's price), and the
    simulator's price history keeps MAX per start_date over the rows the price views count.
"""
from __future__ import annotations

import tests.test_r7c_order_heart as H  # first: stdout, sys.path and .env are set up there

import io  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
import traceback  # noqa: E402
from contextlib import contextmanager, redirect_stderr, redirect_stdout  # noqa: E402
from datetime import timedelta  # noqa: E402
from pathlib import Path  # noqa: E402

import scripts.apply_price_drops_to_open_orders as P  # noqa: E402

ROOT = H.ROOT
TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


EFF = "2026-09-28"
CUT_T02 = [{"sku_code": "T02", "prev_price_bhd": 2.95, "current_price_bhd": 1.7}]
BY = H.ADMIN


def _ctx(t02=1.7, rules=(), **settings):
    """The catalog after the load: T02 at its new book price, the rest as before."""
    return H._ctx([H._item("T02", t02), H._item("X05", 2.0), H._item("C18", 1.25)], rules=rules, **settings)


@contextmanager
def _env(fake, ctx=None, cuts=None, verify=None):
    """The fake database and context (the heart suite's _patched), the script's SQL seam serving the
    price-change rows (and --verify-load's rows), backups under a temp folder."""
    tmp = Path(tempfile.mkdtemp(prefix="r7e-"))
    served = CUT_T02 if cuts is None else cuts
    vrows = verify or {}

    def sql(q, _params):
        if q.startswith("SELECT sku_code, prev_price_bhd"):
            return [dict(r) for r in served]
        for key, rows in vrows.items():
            if key in q:
                return rows
        return []
    try:
        with H._swap(P, _sql=sql, BACKUPS=tmp), H._patched(fake, ctx if ctx is not None else _ctx()):
            yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run(argv) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = P.main(list(argv))
    return code, buf.getvalue()


def _commit(*extra) -> tuple[int, str]:
    return _run(["--commit", "--by", BY, *extra])


def _folder(tmp: Path) -> Path:
    got = [p for p in tmp.iterdir() if p.is_dir()]
    assert len(got) == 1, got
    return got[0]


def _applied(tmp: Path) -> list:
    return json.loads((_folder(tmp) / "applied.json").read_text(encoding="utf-8"))


# ═══════════════════════════════════════════════════════════════════════════════
# 1. scripts/apply_price_drops_to_open_orders
# ═══════════════════════════════════════════════════════════════════════════════

@test("1 dry run: the table and the footer, nothing written")
def _():
    fake = H._one("new")
    with _env(fake) as tmp:
        code, out = _run([])
        assert list(tmp.iterdir()) == [], "no backup on a dry run"
    assert code == 0 and fake.writes() == [], fake.writes()
    assert "YQ-2609-0001" in out and "37.500 -> 25.000" in out and "-1.250" in out and "-12.500" in out, out
    assert "Orders to lower: 1   lines: 1   BHD reduction: 12.500" in out and "Dry run. Nothing written." in out


@test("2 commit (Received order): the confirmed layer only — T02 10 x 2.950 -> 1.700, total 25.000, one event, one audit row")
def _():
    from app import shop
    fake = H._one("new")
    with _env(fake) as tmp:
        code, out = _commit()
        assert code == 0, out
        heads = [c for c in fake.writes("shop_orders") if c[0] == "update"]
        assert len(heads) == 1 and set(heads[0][2]) == {"total_confirmed_bhd", "subtotal_confirmed_bhd", "updated_at"}
        lw = fake.writes("shop_order_lines")
        assert lw and all(c[0] == "update" and set(c[2]) <= set(P.LINE_MONEY) for c in lw), lw
        row = fake.rows("shop_orders")[0]
        assert (row["status"], row["total_bhd"], row["subtotal_bhd"], row["order_kind"]) == ("new", 37.5, 37.5, "standard")
        assert row["total_confirmed_bhd"] == 25.0 and row["subtotal_confirmed_bhd"] == 37.5, row
        by = H._lines_by(fake)
        assert (by[11]["unit_price_bhd"], by[11]["line_total_bhd"], by[11]["list_price_bhd"]) == (2.95, 29.5, 2.95)
        assert (by[11]["unit_price_confirmed"], by[11]["line_total_confirmed"]) == (1.7, 17.0)
        assert by[12]["unit_price_confirmed"] is None, "an uncut line is not written"
        ev = H._events(fake, 1, "amended")
        assert len(ev) == 1 and ev[0]["actor"] == BY
        d = ev[0]["detail"]
        assert d["note"] == "Price lowered to the 28-Sep price book: T02 2.950 → 1.700. Total BHD 25.000 (was 37.500).", d
        assert d["stage"] == "price_book" and d["from"] == "new" and d["price_book_date"] == EFF
        assert d["lines"] == [{"line_id": 11, "item_code": "T02", "reason": "price", "requested": 10,
                               "before": {"unit_price_bhd": 2.95}, "after": {"unit_price_bhd": 1.7}, "note": None}]
        assert (d["total_before"], d["total_after"], d["adverse"], d["shop_agreed"]) == (37.5, 25.0, [], None)
        aud = fake.rows("shop_admin_audit")
        assert len(aud) == 1 and (aud[0]["actor"], aud[0]["entity"], aud[0]["entity_id"], aud[0]["action"]) == \
            (BY, "order", "1", "update")
        assert aud[0]["before"]["total_confirmed_bhd"] is None and aud[0]["after"]["total_confirmed_bhd"] == 25.0
        assert aud[0]["after"]["reason"] == "price book 2026-09-28"
        assert aud[0]["after"]["lines"] == [{"line_id": 11, "item_code": "T02", "unit_price_confirmed": 1.7,
                                             "line_total_confirmed": 17.0}]
        # the shop's status page: the effective total and the note on the timeline
        pv = shop.public_order_view(shop.get_order(1))
        assert pv["total_effective_bhd"] == 25.0 and pv["total_bhd"] == 37.5, pv
        assert any(t["event"] == "amended" and "T02 2.950 → 1.700" in (t["note"] or "") for t in pv["timeline"])
        t02 = next(ln for ln in pv["lines"] if ln["item_code"] == "T02")
        assert t02["reason_label"] == "Price change" and t02["line_total_confirmed"] == 17.0
        # the backup, before the first write, and applied.json after the order
        folder = _folder(tmp)
        assert folder.name.endswith("_pre-price-drop-orders"), folder.name
        assert {p.name for p in folder.iterdir()} == {"orders.json", "lines.json", "plan.json", "applied.json"}
        saved = json.loads((folder / "orders.json").read_text(encoding="utf-8"))
        assert saved[0]["total_confirmed_bhd"] is None and saved[0]["updated_at"] != row["updated_at"]
        applied = _applied(tmp)
        assert [a["order_id"] for a in applied] == [1] and applied[0]["updated_at"] == row["updated_at"]
        assert f'--reverse "{folder}"' in out


@test("3 never raised: today above the lock, a tier line the engine prices no lower, a line already on the new book")
def _():
    cut = [{"sku_code": "T02", "prev_price_bhd": 2.95, "current_price_bhd": 2.9}]
    orders = [H._order(1, "confirmed", items_count=1),
              H._order(2, "new", subtotal_bhd=29.5, discount_bhd=2.95, total_bhd=26.55, items_count=1),
              H._order(3, "new", items_count=1)]
    lines = [H._line(11, 1, "T02", 10, 1.5, lp=2.95, confirmed=True),      # the rep agreed 1.500 < today's 2.900
             H._line(21, 2, "T02", 10, 2.655, lp=2.95, rule_ids=[5]),       # a 10 %-off tier: no tier today, 2.900
             H._line(31, 3, "T02", 10, 2.9)]                                # placed on the new book
    fake = H._db(shop_orders=orders, shop_order_lines=lines)
    with _env(fake, _ctx(2.9), cuts=cut):
        code, out = _commit()
    assert code == 0 and fake.writes() == [], fake.writes()
    assert "Nothing to lower" in out and "already at or under today's price" in out and "placed on the new book" in out
    # the tier still live: the engine's tier price (2.900 less 10 % = 2.610) IS under the 2.655 lock -> lowered
    rules = [{"id": 5, "name": "10 off at 10", "kind": "qty_tier", "min_qty": 10, "pct_off": 10,
              "scope": {"item_codes": ["T02"], "categories": [], "referral_codes": []}, "stackable": False}]
    fake = H._db(shop_orders=[orders[1]], shop_order_lines=[lines[1]])
    with _env(fake, _ctx(2.9, rules=rules), cuts=cut):
        code, out = _commit()
    ln = H._lines_by(fake)[21]
    assert code == 0 and (ln["unit_price_confirmed"], ln["line_total_confirmed"]) == (2.61, 26.1), (ln, out)
    assert ln["unit_price_bhd"] == 2.655 and fake.rows("shop_orders")[0]["total_confirmed_bhd"] == 26.1


@test("4 which orders: delivered / cancelled / test / invoiced / paid left alone (the last two: a credit note); packed lowered")
def _():
    orders = [H._order(1, "delivered"), H._order(2, "cancelled"), H._order(3, "new", is_test=True),
              H._order(4, "confirmed", focus_invoice_no="INV-26-0001"), H._order(5, "packed"),
              H._order(6, "confirmed", payment_status="paid")]
    lines = [ln for i in range(1, 7) for ln in H._lines(i, confirmed=i not in (2, 3))]
    fake = H._db(shop_orders=orders, shop_order_lines=lines)
    before = {o["id"]: dict(o) for o in fake.rows("shop_orders")}
    lines_before = {ln["id"]: dict(ln) for ln in fake.rows("shop_order_lines")}
    with _env(fake):
        code, out = _commit()
    assert code == 0, out
    rows = {o["id"]: o for o in fake.rows("shop_orders")}
    assert rows[5]["status"] == "packed" and rows[5]["total_confirmed_bhd"] == 25.0
    assert H._lines_by(fake)[51]["unit_price_confirmed"] == 1.7
    for i in (1, 2, 3, 4, 6):
        assert rows[i] == before[i], i
        assert all(H._lines_by(fake)[lid] == lines_before[lid] for lid in (i * 10 + 1, i * 10 + 2)), i
    assert [e["order_id"] for e in H._events(fake, name="amended")] == [5]
    assert f"skipped YQ-2609-0004: {P.CREDIT_NOTE}" in out and f"skipped YQ-2609-0006: {P.CREDIT_NOTE}" in out
    assert "skipped YQ-2609-0003: test order" in out
    assert "YQ-2609-0001" not in out and "YQ-2609-0002" not in out, "delivered / cancelled are not open orders"


@test("5 cart discount: re-shared pro rata, never grown; total = items - cart + delivery to the fils; delivery as ordered")
def _():
    orders = [H._order(1, "new", discount_bhd=3.0, delivery_bhd=1.5, total_bhd=36.0),
              H._order(2, "confirmed", discount_bhd=1.0, delivery_bhd=1.5, total_bhd=38.0, total_confirmed_bhd=38.0)]
    fake = H._db(shop_orders=orders, shop_order_lines=H._lines(1) + H._lines(2, confirmed=True))
    with _env(fake):
        code, out = _commit()
    assert code == 0, out
    by = H._lines_by(fake)
    for oid, want, want_cart in ((1, "24.500", "2.000"), (2, "25.833", "0.667")):
        row = next(o for o in fake.rows("shop_orders") if o["id"] == oid)
        items = sum((H.D(ln["line_total_confirmed"] if ln["line_total_confirmed"] is not None else ln["line_total_bhd"])
                     for ln in by.values() if ln["order_id"] == oid), H.D("0"))
        total = H.D(row["total_confirmed_bhd"])
        cart = items + H.D(row["delivery_bhd"]) - total
        assert items == H.D("25.000") and total == H.D(want) and cart == H.D(want_cart), (oid, items, total, cart)
        assert cart <= H.D(row["discount_bhd"]), "the cart discount never grows"
        assert (row["discount_bhd"], row["delivery_bhd"]) == (orders[oid - 1]["discount_bhd"], 1.5), "placed as placed"
    assert "is not what its lines add up to" not in out


@test("5b a total on file its lines do not add up to, or fewer lines than the order was placed with: skipped, nothing written")
def _():
    # review: the new total used to follow the lines, taking the gap off the order with the cut
    # (confirmed 60.000 on 37.500 of lines, cut 12.500 -> written 25.000)
    orders = [H._order(1, "new", total_bhd=40.0), H._order(2, "confirmed", total_confirmed_bhd=60.0),
              H._order(3, "confirmed", items_count=3), H._order(4, "confirmed")]
    fake = H._db(shop_orders=orders, shop_order_lines=[ln for i in range(1, 5) for ln in H._lines(i, i != 1)])
    with _env(fake):
        code, out = _run([])
        assert code == 0, out
        assert ("skipped YQ-2609-0001: the total on file 40.000 is not what its lines add up to (37.500): "
                "check by hand") in out, out
        assert "skipped YQ-2609-0002: the total on file 60.000 is not what its lines add up to (37.500)" in out
        assert "skipped YQ-2609-0003: 2 of the 3 lines it was placed with read: check by hand" in out
        assert "Orders to lower: 1   lines: 1   BHD reduction: 12.500" in out, out
        code, out = _commit()
    assert code == 0, out
    rows = {o["id"]: o for o in fake.rows("shop_orders")}
    assert (rows[1]["total_confirmed_bhd"], rows[2]["total_confirmed_bhd"], rows[3]["total_confirmed_bhd"]) == \
        (None, 60.0, 37.5)
    assert rows[4]["total_confirmed_bhd"] == 25.0
    assert [e["order_id"] for e in H._events(fake, name="amended")] == [4]


@test("5c an order's lines are read on their own: PostgREST's 1,000-row page never cuts one off")
def _():
    # 30 orders x 38 lines = 1,140 lines: one batched read of 50 orders came back cut at 1,000 rows,
    # order 27 with 12 of its lines (lowered to their partial sum) and 28-30 with none (missed)
    orders, lines = [], []
    for i in range(1, 31):
        orders.append(H._order(i, "confirmed", subtotal_bhd=103.5, total_bhd=103.5, subtotal_confirmed_bhd=103.5,
                               total_confirmed_bhd=103.5, items_count=38, units_count=47))
        lines.append(H._line(i * 100, i, "T02", 10, 2.95, confirmed=True))
        lines += [H._line(i * 100 + k, i, "X05", 1, 2.0, confirmed=True) for k in range(1, 38)]
    fake = H._db(shop_orders=orders, shop_order_lines=lines)
    real = H._Query.execute

    def capped(self):                    # PostgREST's max-rows (app/followups.py): a response stops at 1,000
        got = real(self)
        if self.op == "select" and got.data and len(got.data) > 1000:
            got.data = got.data[:1000]
        return got
    with H._swap(H._Query, execute=capped), _env(fake):
        code, out = _commit()
    assert code == 0, out
    assert "Orders to lower: 30   lines: 30   BHD reduction: 375.000" in out, out
    assert sorted({o["total_confirmed_bhd"] for o in fake.rows("shop_orders")}) == [91.0], "each order 12.500 lower"


@test("6 minimum: a Confirmed order cut under BHD 20 is lowered and reported (BELOW MIN), never cancelled; an order already under it says so")
def _():
    orders = [H._order(3, "confirmed", subtotal_bhd=23.6, total_bhd=23.6, subtotal_confirmed_bhd=23.6,
                       total_confirmed_bhd=23.6, items_count=1),
              H._order(2, "confirmed", subtotal_bhd=14.75, total_bhd=14.75, subtotal_confirmed_bhd=14.75,
                       total_confirmed_bhd=14.75, items_count=1)]
    lines = [H._line(31, 3, "T02", 8, 2.95, confirmed=True), H._line(21, 2, "T02", 5, 2.95, confirmed=True)]
    fake = H._db(shop_orders=orders, shop_order_lines=lines)
    ctx = _ctx()
    with _env(fake, ctx):
        cuts = P.load_cuts(EFF)
        plans = {o["id"]: P.plan_order(o, ctx, cuts) for o in P.load_open_orders()}
        assert (plans[3]["min"], plans[2]["min"]) == ("BELOW MIN", "already small"), plans
        assert plans[3]["skip"] is None and plans[2]["skip"] is None
        code, out = _run([])
        assert "BELOW MIN (reported, never cancelled): YQ-2609-0003" in out, out
        code, out = _commit()
    assert code == 0, out
    row = next(o for o in fake.rows("shop_orders") if o["id"] == 3)
    assert (row["status"], row["order_kind"], row["total_confirmed_bhd"], row["cancelled_at"]) == \
        ("confirmed", "standard", 13.6, None)
    assert next(o for o in fake.rows("shop_orders") if o["id"] == 2)["total_confirmed_bhd"] == 8.5


@test("6b minimum: a Received order the cut takes under BHD 20 is skipped (its Confirm would need 'Shop agreed'); lowered once the rep confirms")
def _():
    from app import shop_heart
    # review: T02 3 x 2.950 + X05 6 x 2.000 = 20.850 -> 17.100; the rep's Confirm compares with the
    # placed total and asked for the tick although the rep changed nothing
    orders = [H._order(1, "new", subtotal_bhd=20.85, total_bhd=20.85, units_count=9)]
    lines = [H._line(11, 1, "T02", 3, 2.95), H._line(12, 1, "X05", 6, 2.0)]
    fake = H._db(shop_orders=orders, shop_order_lines=lines)
    with _env(fake):
        code, out = _commit()
        assert code == 0 and f"skipped YQ-2609-0001: {P.BELOW_MIN_NEW}" in out, out
        assert "Nothing to lower. Nothing written." in out and fake.writes() == [], fake.writes()
        # the rep confirms as placed, no tick; a re-run lowers it, and an Amend needs no tick either
        got = shop_heart.confirm_order(1, [], "Tomorrow", None, actor=H.REP)
        assert got["totals"]["total_bhd"] == 20.85
        assert H._events(fake, 1, "status:confirmed")[0]["detail"]["adverse"] == []
        code, out = _commit()
        assert code == 0 and "BELOW MIN (reported, never cancelled): YQ-2609-0001" in out, out
        assert fake.rows("shop_orders")[0]["total_confirmed_bhd"] == 17.1
        shop_heart.amend_order(1, [{"line_id": 12, "qty_confirmed": 5, "reason": "out_of_stock"}], None, None,
                               actor=H.REP)
    row = fake.rows("shop_orders")[0]
    assert (row["status"], row["total_confirmed_bhd"]) == ("confirmed", 15.1), row


@test("7 compare-and-swap: a write between the read and the swap wins — the order is skipped, nothing written")
def _():
    fake = H._one("new")
    bumped = (H.NOW - timedelta(minutes=5)).isoformat()
    fake.after_select["shop_orders"] = lambda n: fake.rows("shop_orders")[0].update(updated_at=bumped) if n == 1 else None
    lines_before = [dict(ln) for ln in fake.rows("shop_order_lines")]
    with _env(fake) as tmp:
        code, out = _commit()
        assert _applied(tmp) == []
    assert code == 1 and "YQ-2609-0001: changed since read — re-run" in out, out
    assert fake.writes("shop_order_lines") == [] and fake.writes("shop_order_events") == []
    assert fake.rows("shop_admin_audit") == [] and fake.rows("shop_order_lines") == lines_before
    row = fake.rows("shop_orders")[0]
    assert row["updated_at"] == bumped and row["total_confirmed_bhd"] is None


@test("8 a failed event insert puts the header and the lines back (the heart's undo), no audit row, not in applied.json")
def _():
    fake = H._one("confirmed")
    fake.fail[("insert", "shop_order_events")] = RuntimeError("events table is down")
    order_before = dict(fake.rows("shop_orders")[0])
    lines_before = [dict(ln) for ln in fake.rows("shop_order_lines")]
    with _env(fake) as tmp:
        code, out = _commit()
        assert _applied(tmp) == []
    assert code == 1 and "YQ-2609-0001: failed: events table is down" in out, out
    assert fake.writes("shop_order_lines"), "the line was written, then put back"
    assert fake.rows("shop_orders")[0] == order_before
    assert fake.rows("shop_order_lines") == lines_before
    assert fake.rows("shop_admin_audit") == [] and H._events(fake) == []


@test("9 a re-run is a no-op: the lowered lines are at today's price, nothing written, no second backup")
def _():
    fake = H._one("confirmed")
    with _env(fake) as tmp:
        c1, _ = _commit()
        n = len(fake.writes())
        c2, out = _commit()
        assert len([p for p in tmp.iterdir()]) == 1
    assert c1 == 0 and c2 == 0 and len(fake.writes()) == n
    assert "Orders to lower: 0   lines: 0   BHD reduction: 0.000" in out and "Nothing to lower" in out, out
    assert len(H._events(fake, 1, "amended")) == 1


@test("10 the heart keeps the lowered price: a later Confirm confirms at 1.700, an Amend re-prices at 1.700")
def _():
    from app import shop_heart
    fake = H._one("new")
    with _env(fake):
        assert _commit()[0] == 0
        got = shop_heart.confirm_order(1, [], "Tomorrow", None, actor=H.REP)
        assert got["totals"]["total_bhd"] == 25.0
        row = fake.rows("shop_orders")[0]
        assert (row["status"], row["total_confirmed_bhd"], row["total_bhd"]) == ("confirmed", 25.0, 37.5)
        by = H._lines_by(fake)
        assert (by[11]["unit_price_confirmed"], by[11]["line_total_confirmed"]) == (1.7, 17.0)
        assert H._events(fake, 1, "status:confirmed")[0]["detail"]["adverse"] == [], "25.000 is over the minimum"
        shop_heart.amend_order(1, [{"line_id": 11, "qty_confirmed": 8, "reason": "out_of_stock"}], None, None,
                               actor=H.REP)
    by = H._lines_by(fake)
    assert (by[11]["unit_price_confirmed"], by[11]["line_total_confirmed"]) == (1.7, 13.6)
    assert fake.rows("shop_orders")[0]["total_confirmed_bhd"] == 21.6


@test("11 --reverse: the old confirmed layer back (NULL on a Received order), 'Price change undone'; an order touched since is skipped")
def _():
    orders = [H._order(1, "new"), H._order(2, "confirmed"), H._order(3, "confirmed")]
    fake = H._db(shop_orders=orders, shop_order_lines=H._lines(1) + H._lines(2, True) + H._lines(3, True))
    before = {o["id"]: dict(o) for o in fake.rows("shop_orders")}
    lines_before = {ln["id"]: dict(ln) for ln in fake.rows("shop_order_lines")}
    with _env(fake) as tmp:
        assert _commit()[0] == 0
        folder = _folder(tmp)
        assert [a["order_id"] for a in _applied(tmp)] == [1, 2, 3]
        next(o for o in fake.rows("shop_orders") if o["id"] == 3)["updated_at"] = (H.NOW + timedelta(minutes=1)).isoformat()
        n = len(fake.writes())
        code, out = _run(["--reverse", str(folder)])
        assert code == 0 and len(fake.writes()) == n, "a reverse without --commit writes nothing"
        assert "Price change undone: T02 1.700 → 2.950. Total BHD 37.500 (was 25.000)." in out, out
        assert "YQ-2609-0003: touched since the price change, skipped" in out
        assert "1 would be left as it is (adjust by hand): YQ-2609-0003" in out, out
        code, out = _run(["--reverse", str(folder), "--commit", "--by", BY])
    # an undo that left an order lowered is not a success (review: it exited 0)
    assert code == 1 and "2 put back, 1 not (adjust by hand): YQ-2609-0003" in out, out
    rows = {o["id"]: o for o in fake.rows("shop_orders")}
    by = H._lines_by(fake)
    money = ("total_confirmed_bhd", "subtotal_confirmed_bhd", "total_bhd", "status")
    for oid in (1, 2):
        assert {k: rows[oid][k] for k in money} == {k: before[oid][k] for k in money}, oid
        for lid in (oid * 10 + 1, oid * 10 + 2):
            assert {k: by[lid][k] for k in P.LINE_MONEY} == {k: lines_before[lid][k] for k in P.LINE_MONEY}, lid
    assert rows[1]["total_confirmed_bhd"] is None and by[11]["unit_price_confirmed"] is None
    assert rows[3]["total_confirmed_bhd"] == 25.0 and by[31]["unit_price_confirmed"] == 1.7, "touched since: left alone"
    undone = [e for e in H._events(fake, name="amended") if e["detail"].get("undo")]
    assert sorted(e["order_id"] for e in undone) == [1, 2]
    d = next(e for e in undone if e["order_id"] == 2)["detail"]
    assert d["lines"][0]["before"] == {"unit_price_bhd": 1.7} and d["lines"][0]["after"] == {"unit_price_bhd": 2.95}
    assert (d["total_before"], d["total_after"]) == (25.0, 37.5)
    assert len(fake.rows("shop_admin_audit")) == 5 and fake.rows("shop_admin_audit")[-1]["after"]["reason"] == \
        "price book 2026-09-28 undone"


@test("11b --reverse exits 1 when an order is gone or a line moved since the price change; 0 only when every order went back")
def _():
    orders = [H._order(1, "confirmed"), H._order(2, "confirmed")]
    fake = H._db(shop_orders=orders, shop_order_lines=H._lines(1, True) + H._lines(2, True))
    with _env(fake) as tmp:
        assert _commit()[0] == 0
        folder = _folder(tmp)
        H._lines_by(fake)[11]["unit_price_confirmed"] = 1.6            # by hand, updated_at left as it was
        fake.tables["shop_orders"] = [o for o in fake.rows("shop_orders") if o["id"] != 2]
        n = len(fake.writes())
        code, out = _run(["--reverse", str(folder), "--commit", "--by", BY])
        assert code == 1 and len(fake.writes()) == n, out
        assert "YQ-2609-0001: a line moved since the price change, skipped" in out and "YQ-2609-0002: gone" in out
        assert "0 put back, 2 not (adjust by hand): YQ-2609-0001, YQ-2609-0002" in out, out
    fake = H._one("confirmed")
    with _env(fake) as tmp:
        assert _commit()[0] == 0
        code, out = _run(["--reverse", str(_folder(tmp)), "--commit", "--by", BY])
    assert code == 0 and "1 put back, 0 not." in out and fake.rows("shop_orders")[0]["total_confirmed_bhd"] == 37.5, out


@test("12 guards: 0 cuts -> exit 2 'load the price book first'; an --expect-cuts mismatch aborts before any write; --commit needs --by")
def _():
    fake = H._one("new")
    with _env(fake, cuts=[]) as tmp:
        code, out = _commit()
        assert list(tmp.iterdir()) == []
    assert code == 2 and "load the price book first" in out and fake.writes() == [], out
    fake = H._one("new")
    with _env(fake) as tmp:
        code, out = _commit("--expect-cuts", "34")
        assert list(tmp.iterdir()) == []
        ok, _ = _commit("--expect-cuts", "1")
    assert code == 3 and "1 cuts dated 2026-09-28, expected 34" in out and ok == 0
    assert fake.rows("shop_orders")[0]["total_confirmed_bhd"] == 25.0, "the matching count went ahead"
    fake = H._one("new")
    with _env(fake), redirect_stderr(io.StringIO()):
        try:
            P.main(["--commit"])
            raise AssertionError("--commit without --by must refuse")
        except SystemExit as e:
            assert e.code == 2
    assert fake.writes() == []


@test("13 --verify-load: a, b, c, e PASS on a good load; a wrong count, stale sales or a missing Was is a FAIL (exit 1)")
def _():
    good = {"FILTER": [{"cuts": 1, "rises": 0}], "JOIN v_price_list_by_book": [{"n": 1}],
            "FROM v_sales": [{"d": "2026-09-27"}]}
    ctx = _ctx()
    ctx["drops"] = {"T02": {"was": 2.95, "now": 1.7, "on": EFF}}
    fake = H._one("new")
    with _env(fake, ctx, verify=good):
        code, out = _run(["--verify-load", "--expect-cuts", "1"])
        assert code == 0 and "ALL PASS" in out and out.count("PASS  ") == 5, out
        code, out = _run(["--verify-load", "--expect-cuts", "34"])
        assert code == 1 and "FAIL  a. cuts loaded" in out, out
    with _env(fake, ctx, verify={**good, "FROM v_sales": [{"d": "2026-09-20"}]}):
        code, out = _run(["--verify-load"])
        assert code == 1 and "FAIL  c. freshness" in out, out
    with _env(fake, _ctx(), verify=good):          # the drop not in the context: no Was on the card
        code, out = _run(["--verify-load"])
        assert code == 1 and "FAIL  e. catalog Was/Now: 0 of 1" in out, out
    assert fake.writes() == [], "--verify-load is read-only"


@test("14 Focus may have billed it: a confirmed link is a credit note; a suggestion is skipped unless --include names the order; unreadable = refused")
def _():
    # review: 'invoiced' was only focus_invoice_no / payment_status — an order whose Focus SI names it
    # (a narration_ref suggestion nobody accepted yet) was lowered under the invoice's price
    orders = [H._order(i, "confirmed") for i in range(1, 6)]
    cands = [{"order_id": 1, "invoice_key": "SI-YQ-26-09-101", "method": "narration_ref", "confidence": 1.0, "rank": 1},
             {"order_id": 4, "invoice_key": "SI-YQ-26-09-104", "method": "auto_items", "confidence": 0.9, "rank": 1}]
    links = [{"order_id": 2, "invoice_key": "SI-YQ-26-09-102", "state": "confirmed", "method": "manual"},
             {"order_id": 3, "invoice_key": "SI-YQ-26-09-103", "state": "rejected", "method": "auto_items"}]
    fake = H._db(shop_orders=orders, shop_order_lines=[ln for i in range(1, 6) for ln in H._lines(i, True)],
                 v_shop_focus_candidates=cands, shop_order_focus_links=links)
    lowered = lambda: sorted(o["id"] for o in fake.rows("shop_orders") if o["total_confirmed_bhd"] == 25.0)  # noqa: E731
    with _env(fake):
        code, out = _run([])
        assert code == 0 and lowered() == [], out
        assert ("skipped YQ-2609-0001: possibly invoiced in Focus (SI-YQ-26-09-101, Order number in the invoice "
                "note): credit note, or --include YQ-2609-0001") in out, out
        assert f"skipped YQ-2609-0002: {P.CREDIT_NOTE} (linked to SI-YQ-26-09-102)" in out, out
        assert "skipped YQ-2609-0004: possibly invoiced in Focus (SI-YQ-26-09-104, Same rep, same items)" in out
        assert "YQ-2609-0003:" not in out.split("Orders to lower")[1], "a rejected pair is no sign of an invoice"
        code, out = _commit()
        assert code == 0 and lowered() == [3, 5], out
        # the operator looked: 0004's suggestion is another sale — named, it is lowered; a confirmed link never
        code, out = _commit("--include", "yq-2609-0004", "--include", "YQ-2609-0002")
        assert code == 0 and lowered() == [3, 4, 5], out
        assert f"skipped YQ-2609-0002: {P.CREDIT_NOTE} (linked to SI-YQ-26-09-102)" in out
    # the links / suggestions cannot be read: nothing is lowered blind
    fake = H._db(shop_orders=[H._order(1, "confirmed")], shop_order_lines=H._lines(1, True))
    fake.fail[("select", "v_shop_focus_candidates")] = RuntimeError("statement timeout")
    with _env(fake) as tmp:
        code, out = _commit()
        assert list(tmp.iterdir()) == []
    assert code == 2 and "REFUSED: could not read the Focus links / suggestions" in out, out
    assert fake.writes() == []


@test("15 offer ledger: a lowered tier line's rule spend is measured against the moved list (1.700), never the book's cut (14.200)")
def _():
    from app import shop
    # review: list 2.950, placed 2.655 (10 % at 10), lowered to 1.530 (1.700 less 10 %); the ledger
    # measured (placed list - lowered unit) x qty = 14.200 on the next Confirm / Amend
    rules = [{"id": 5, "name": "10 off at 10", "kind": "qty_tier", "min_qty": 10, "pct_off": 10,
              "scope": {"item_codes": ["T02"], "categories": [], "referral_codes": []}, "stackable": False}]
    orders = [H._order(1, "confirmed", discount_bhd=2.95, total_bhd=34.55, total_confirmed_bhd=34.55),
              H._order(2, "new", discount_bhd=2.95, total_bhd=34.55)]
    lines = [H._line(11, 1, "T02", 10, 2.655, lp=2.95, confirmed=True, rule_ids=[5]),
             H._line(12, 1, "X05", 4, 2.0, confirmed=True),
             H._line(21, 2, "T02", 10, 2.655, lp=2.95, rule_ids=[5]), H._line(22, 2, "X05", 4, 2.0)]
    ledger = [{"id": 1, "order_id": 1, "line_id": 11, "item_code": "T02", "rule_id": 5, "kind": "qty_tier",
               "level": "line", "amount_bhd": 2.95, "amount_confirmed_bhd": 2.95, "stage": "confirmed"},
              {"id": 2, "order_id": 2, "line_id": 21, "item_code": "T02", "rule_id": 5, "kind": "qty_tier",
               "level": "line", "amount_bhd": 2.95, "amount_confirmed_bhd": None, "stage": "placed"}]
    fake = H._db(shop_orders=orders, shop_order_lines=lines, shop_order_discounts=ledger)
    amt = lambda rid: next(r for r in fake.rows("shop_order_discounts") if r["id"] == rid)["amount_confirmed_bhd"]  # noqa: E731
    with _env(fake, _ctx(rules=rules)):
        code, out = _commit()
        assert code == 0, out
        by = H._lines_by(fake)
        assert (by[11]["unit_price_confirmed"], by[21]["unit_price_confirmed"]) == (1.53, 1.53)
        assert amt(1) == 1.7 and (amt(2), fake.rows("shop_order_discounts")[1]["stage"]) == (None, "placed")
        shop.amend_order(1, [{"line_id": 11, "qty_confirmed": 8, "reason": "out_of_stock"}], None, None, actor=H.REP)
        assert amt(1) == 1.36, amt(1)                 # (1.700 - 1.530) x 8
        shop.confirm_order(2, [], "Tomorrow", None, actor=H.REP)
        assert amt(2) == 1.7, amt(2)
    # a line never lowered is measured as before: the placed list less the locked unit
    from app.offers import confirmed_amounts
    o = {"id": 9, "status": "confirmed", "discount_bhd": 2.95, "delivery_bhd": 0}
    ln = H._line(91, 9, "T02", 10, 2.655, lp=2.95, confirmed=True, rule_ids=[5])
    assert confirmed_amounts(o, [ln], [{"id": 7, "line_id": 91, "level": "line", "amount_bhd": 2.95}]) == {7: 2.95}


# ═══════════════════════════════════════════════════════════════════════════════
# 2. the price in force: v_price_list_by_book, never MAX(rate_bhd) over the history
# ═══════════════════════════════════════════════════════════════════════════════

PRICE_READERS = ("app/orders.py", "app/order_verify.py", "app/agents.py", "app/bi_simulator.py")


@test("price in force (source): orders / order_verify / agents / bi_simulator read v_price_list_by_book, no MAX(rate_bhd) AS s|sell")
def _():
    for rel in PRICE_READERS:
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert not re.search(r"MAX\(rate_bhd\)\s+AS\s+(s|sell)\b", src, re.I), rel
        assert "FROM v_price_list_by_book" in src, rel
        assert "price_book='MA_base'" in src, rel
    sim = (ROOT / "app" / "bi_simulator.py").read_text(encoding="utf-8")
    base = sim.split("def _current_baseline", 1)[1].split("\ndef ", 1)[0]
    assert "FROM v_price_list_by_book" in base and "FROM selling_prices" not in base, base
    # the price HISTORY stays MAX per start_date, over the rows the price views count
    epochs = sim.split("def _epochs", 1)[1].split("\ndef ", 1)[0]
    assert "MAX(rate_bhd) AS rate FROM selling_prices" in epochs and "GROUP BY start_date" in epochs
    for f in ("warehouse_name IS NULL", "voided_at IS NULL", "status='Authorized'", "start_date<=CURRENT_DATE"):
        assert f in epochs, f


class _Stop(Exception):
    pass


@test("price in force (SQL): the PO detail's selling price and the simulator's baseline are read from the view")
def _():
    import app.bi_simulator as sim
    import app.orders as orders
    seen: list[str] = []

    def fake_sql(sql, params):
        seen.append(sql)
        if "FROM purchase_orders" in sql:
            return [{"po_no": "PO-1", "po_date": "2026-09-01", "vendor": "V", "ordered_value_bhd": 10, "line_count": 1}]
        if "FROM v_po_item" in sql:
            return [{"code": "T02", "description": "T02 cable", "ordered_qty": 10, "ordered_rate": 1.0}]
        if "v_price_list_by_book" in sql or "selling_prices" in sql:
            raise _Stop()           # the read under test: stop before the rest of the page is built
        return []
    with H._swap(orders, exec_sql_params=fake_sql):
        try:
            orders.detail("PO-1", with_files=False)
            raise AssertionError("the selling-price read never ran")
        except _Stop:
            pass
    sell = seen[-1]
    assert "FROM v_price_list_by_book" in sell and "price_book='MA_base'" in sell and "rate_bhd" not in sell, sell
    assert "SPLIT_PART(sku_code,' ',1) IN (SELECT jsonb_array_elements_text($1::jsonb))" in sell

    got: list[tuple[str, list]] = []

    def sim_sql(sql, params):
        got.append((sql, params))
        return [{"rate_bhd": 1.7}] if "v_price_list_by_book" in sql else [{"q": 30}]
    with H._swap(sim, exec_sql_params=sim_sql):
        price, monthly = sim._current_baseline("T02")
    assert (price, monthly) == (1.7, 10.0)
    assert "FROM v_price_list_by_book" in got[0][0] and "ORDER BY (UPPER(sku_code)=$1) DESC" in got[0][0]
    assert got[0][1] == ["T02"]


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
