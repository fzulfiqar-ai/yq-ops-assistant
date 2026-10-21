"""Ops engine: stock planning (python -m scripts.ops stock), on a synthetic world.

    python -m tests.test_ops_stock

Same lightweight runner as the other suites (no pytest). No database, no network, no .env:
SUPABASE_URL, SUPABASE_KEY and DATABASE_URL are pinned to ci.invalid BEFORE any import and
scripts.ops.db is imported first. The stock ledger is a list of movements built here (the reader and
parser of the workbook are tested in tests/test_ops_moves.py); db.rpc, db.session and
ledger.for_audit are replaced by stand-ins that answer from the same invented world, and every
statement they are handed must pass the read-only gate (db.check_sql). All names and numbers are
invented; every folder is a temporary one.

The world (the data date D is Sun 15 Mar 2026; the rate window is the 90 days to it):
  * ITEM-A, a steady seller: two units a day in a van, every day, from 1 Feb 2025; the main store
    issues two a day. Two supplier receipts (1 Feb 2025, 10 Jan 2026) and a van return (1 Mar 2026):
    394 on hand, two a day, cover 197 days; its units sit on the January receipt (200) and the old
    one (194), the van return never making any of them younger.
  * ITEM-B: sold out last June, received again on 30 Jan: in stock on 45 of the 90 days and sold one
    a day on them (a rate of 1.0); 20 on hand, so it is on the order list.
  * ITEM-C: 50 units since 1 Feb 2025 and never sold (no cost on file but its receipt rate).
  * ITEM-N: a new arrival (first received 1 Mar): 4 sold, so its rate rests on the 30-day floor.
  * ITEM-F: a fast mover by last year's sales, out of stock since September.
  * DISP-01: a display item (config), in the headline and nowhere else.
  * SIM-01: moves only in another store, so it is no accessory.
The figures below are worked out by hand from those lines (the comments show the sums).

Covered:
  * the words (reorder, ageing, a code of two words) and the settings: the two optional keys take the
    plan's default and are named, while config.get still refuses them to every other command;
  * the daily rate (units sold in every warehouse ÷ days in stock, never under 30), cover, new
    arrivals, the floor's star;
  * the order list: listed below lead time + low, filled to lead time + high, less goods on order;
    "goods on order not deducted" until a promise line is logged; a promise counts until a receipt
    is dated after it; closed and unknown lines;
  * the lead time: an invoice beside the receipt holding the same quantity of the most of its models
    (never before it, never too long after), the median of the newest ten, the config value below
    three;
  * KPI 1: aggregate cover at cost, the share above the band, last month's end, the bands by count
    and value with new arrivals apart;
  * fast movers: the ranking over the twelve months before the month, availability (a day counts
    when the day-end balance covers a day of sales), the wider basis, out all month;
  * ageing: newest receipt first, van returns never reset age, units no receipt explains, the bands
    add up to the main store, crossed with the last sale, a suggested action on every band;
  * the headline equals the portal (the portal's own statement, word for word), the two figures at
    cost side by side or one when they agree, the ledger set beside the snapshot;
  * one item, a model code that several items share, an unknown code;
  * the whole command: files under exports/ only, a failed read costs its own part, the wording
    guard, no long digit run, every statement a read;
  * the cost seam (cost_reads, accessory_table, apply_costs: gather's reads, the model's items and
    unit costs, the failed reads named in their place) and the model as on an earlier day (build's
    as_of: the same model as the rows, price cuts and promise lines cut by hand; D is that day though
    nothing moved on it; the costs stay today's; the portal's figures and the ledger check only on the
    snapshot's own day; a day outside the ledger is one sentence);
  * the order position beside the main store (the fixture counts the main store only; the plan's
    default counts all three): the vans, the outlets and the transfers in grace count, a van below
    nought reads nought, a transfer past its deadline, damage and samples never; a van's working
    minimum; as on an earlier day; the failure path; the headline's line, the Vans column and its
    note, one item, the facts; never a store's name, never "on the way";
  * the real loaded ledger and stock balance files, when they are on this machine (SKIP otherwise).
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
import time
import traceback
from dataclasses import replace
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

from scripts.ops import db  # noqa: E402  (first, as every command does)
from scripts.ops import __main__ as cli  # noqa: E402
from scripts.ops import config, fmt, ledger, paths, stock  # noqa: E402

from app import metrics, reports  # noqa: E402

TESTS: list[tuple[str, object]] = []

# The project folder (the main checkout when this is a code worktree): only the last test reads it.
REAL_ROOT = paths.data_root()

NINE = re.compile(r"(?<![0-9])[0-9]{9}(?![0-9])")            # shaped like a personal ID number
LONG = re.compile(r"(?<![0-9])[0-9]{15,}(?![0-9])")          # shaped like a card or account number

MAIN, VAN, VAN_B, SIM_STORE = "Main Store", "Van A - Acc WH", "Van B - Acc WH", "Sim Store"
DAMAGE, SAMPLES = "Damage Store", "Samples Store"
D = date(2026, 3, 15)
FIXTURE = {
    "warehouses.main": MAIN, "warehouses.aliases": {}, "warehouses.not_counted": [DAMAGE, SAMPLES],
    "warehouses.damage": DAMAGE,
    "items.bridge_exceptions": {}, "items.not_accessories": [],
    "stock.lead_time_days": 50, "stock.display_items": ["DS1"], "stock.fast_movers": 2,
    "stock.cover_kpi_days": [30, 45], "stock.clear_target_days": 90,
    # release 1's figures stand in every test that does not say otherwise: no rhythm, every lift 1.00
    "stock.order_every_days": 0, "stock.season_lift": {str(m): 1 for m in range(1, 13)},
    # ... and the main store only in the order position (the tests of the vans and the outlets drop this key)
    "stock.position_stores": [], "stock.van_floor_days": 0,
}
stock.RAISE_PARTS = True              # a part that fails fails the test, never stands aside quietly
NAMES = {"A": "ITEM-A Cable Alpha", "B": "ITEM-B Charger Beta", "C": "ITEM-C Holder Gamma",
         "N": "ITEM-N Speaker New", "F": "ITEM-F Fan Out", "DISP": "DISP-01 Counter Display"}
CODES = {"A": "CA1", "B": "CB2", "C": "HG3", "N": "SN4", "F": "FO5", "DISP": "DS1"}
SIM = "SIM-01 Starter Pack SP9"


def L(key: str) -> str:
    """The ledger's name of an item: its stock name, a space, its code."""
    return f"{NAMES[key]} {CODES[key]}"


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


def raises(kind, fn) -> Exception:
    try:
        fn()
    except kind as exc:
        return exc
    raise AssertionError(f"expected {kind.__name__}")


def days(first: date, last: date):
    d = first
    while d <= last:
        yield d
        d += timedelta(days=1)


# ── movements ──────────────────────────────────────────────────────────────────

class Book:
    """Movements in the shape scripts.ops.ledger gives them."""

    def __init__(self):
        self.rows: list[dict] = []
        self.n = 0

    def _row(self, item, day, vtype, wh, voucher, qin=0, qout=0, vin=0, vout=0, to_wh=None):
        self.rows.append({"item": item, "date": day, "voucher": voucher, "vtype": vtype, "wh": wh, "to_wh": to_wh,
                          "qty_in": Decimal(str(qin)), "qty_out": Decimal(str(qout)), "value_in": Decimal(str(vin)),
                          "value_out": Decimal(str(vout)), "narr": None})

    def receipt(self, item, day, units, value, voucher, wh=MAIN):
        self._row(item, day, ledger.SUPPLIER_RECEIPT, wh, voucher, qin=units, vin=value)
        return self

    def issue(self, item, day, units, to=VAN):
        self.n += 1
        self._row(item, day, ledger.ISSUE, MAIN, f"SIO:T-{self.n}", qout=units, to_wh=to)
        self._row(item, day, ledger.RECEIVE, to, f"STRV:T-{self.n}", qin=units)
        return self

    def back(self, item, day, units, frm=VAN):
        """A van sends units back to the main store: no supplier receipt."""
        self.n += 1
        self._row(item, day, ledger.ISSUE, frm, f"SIO:T-{self.n}", qout=units, to_wh=MAIN)
        self._row(item, day, ledger.RECEIVE, MAIN, f"STRV:T-{self.n}", qin=units)
        return self

    def sale(self, item, day, units, wh=VAN):
        self.n += 1
        self._row(item, day, ledger.SALE, wh, f"SI:T-{self.n}", qout=units)
        return self

    def to_damage(self, item, day, units, frm=MAIN, booked=True, booked_on=None):
        """Units found damaged: issued from `frm` to the damage store; its receive leg only when `booked`
        (on `booked_on`, else the same day)."""
        self.n += 1
        self._row(item, day, ledger.ISSUE, frm, f"SIO:T-{self.n}", qout=units, to_wh=DAMAGE)
        if booked:
            self._row(item, booked_on or day, ledger.RECEIVE, DAMAGE, f"STRV:T-{self.n}", qin=units)
        return self

    def move(self, item, day, units, frm, to, received=True):
        """A transfer between any two stores; its receive leg only when `received`."""
        self.n += 1
        self._row(item, day, ledger.ISSUE, frm, f"SIO:T-{self.n}", qout=units, to_wh=to)
        if received:
            self._row(item, day, ledger.RECEIVE, to, f"STRV:T-{self.n}", qin=units)
        return self


def world() -> Book:
    b = Book()
    # the first shipment: ITEM-A, ITEM-C and the display item on one receipt
    b.receipt(L("A"), date(2025, 2, 1), 1000, 500, "MRN:T-25-02-1")
    b.receipt(L("C"), date(2025, 2, 1), 50, 40, "MRN:T-25-02-1")
    b.receipt(L("DISP"), date(2025, 2, 1), 5, 50, "MRN:T-25-02-1")
    b.receipt(SIM, date(2025, 2, 1), 1000, 100, "MRN:T-25-02-9", wh=SIM_STORE)
    b.sale(SIM, date(2025, 2, 2), 10, wh=SIM_STORE)
    for day in days(date(2025, 2, 1), D):                       # ITEM-A: two a day, 408 days
        b.issue(L("A"), day, 2).sale(L("A"), day, 2)
    b.receipt(L("A"), date(2026, 1, 10), 200, 100, "MRN:T-26-01-1")
    b.back(L("A"), date(2026, 3, 1), 10)                        # 1000 + 200 + 10 - 816 = 394
    # ITEM-F: 400 received, two a day for 200 days, out since 16 Sep 2025
    b.receipt(L("F"), date(2025, 3, 1), 400, 600, "MRN:T-25-03-1")
    for day in days(date(2025, 3, 1), date(2025, 9, 16)):
        b.issue(L("F"), day, 2).sale(L("F"), day, 2)
    # ITEM-B: 30 in June, sold out; 100 on 30 Jan, one a day since, and 35 to the second van on 1 Feb
    b.receipt(L("B"), date(2025, 6, 1), 30, 60, "MRN:T-25-06-1")
    for day in days(date(2025, 6, 1), date(2025, 6, 30)):
        b.issue(L("B"), day, 1).sale(L("B"), day, 1)
    b.receipt(L("B"), date(2026, 1, 30), 100, 250, "MRN:T-26-01-2")
    for day in days(date(2026, 1, 30), D):
        b.issue(L("B"), day, 1).sale(L("B"), day, 1)
    b.issue(L("B"), date(2026, 2, 1), 35, to=VAN_B)             # 100 - 45 - 35 = 20
    # ITEM-N: new on 1 Mar, four sold
    b.receipt(L("N"), date(2026, 3, 1), 40, 120, "MRN:T-26-03-1")
    b.issue(L("N"), date(2026, 3, 5), 1).sale(L("N"), date(2026, 3, 5), 1)
    b.issue(L("N"), date(2026, 3, 10), 3).sale(L("N"), date(2026, 3, 10), 3)
    return b


ON_HAND = {"A": 394, "B": 20, "C": 50, "N": 36, "DISP": 5}
SELL_RATE = {"A": 1, "B": 5, "C": 2, "N": 6, "DISP": 1}        # BHD a unit in the stock balance
# The receipts' landed costs (mrn_landed_costs: id, code, cost, receipt date, doc) and the pricing sheet
# (purchase_costs: id, code, cost). The winners: CA1 0.4 (the latest receipt, though not the highest id), SN4 3.2
# (one date: the higher id), HG3 none (a cost of nought); on the sheet DS1 10.0 (the newest row) and FO5 1.4, and
# CA1's 0.45 is never used (it has an MRN cost). CB2 and HG3 take their receipt rate.
MRN_ROWS = [(9, "ca1", "0.3", "2025-02-01", "T-25-02-1"), (5, "CA1", "0.4", "2026-01-10", "T-26-01-1"),
            (3, "SN4", "3.1", "2026-03-01", "T-26-03-1"), (4, "SN4", "3.2", "2026-03-01", "T-26-03-1"),
            (7, "HG3", "0", "2025-02-01", "T-25-02-1")]
SHEET_ROWS = [(20, "CA1", "0.45"), (21, "DS1", "9.5"), (25, "DS1", "10.0"), (22, "FO5", "1.4"), (23, "HG3", "0")]
PORTAL_COST = {"A": 0.45, "B": 2.4, "C": None, "N": 3.1, "DISP": 9.0}
INVOICES = [  # (number, date, model, quantity)
    ("INV-1", "2025-01-01", "ITEM-A", 1000), ("INV-1", "2025-01-01", "ITEM-C", 50), ("INV-1", "2025-01-01", "DISP-01", 5),
    ("INV-2", "2025-01-20", "ITEM-F", 400), ("INV-3", "2025-04-22", "ITEM-B", 30),
    ("INV-4", "2025-11-26", "ITEM-A", 200), ("INV-4", "2025-11-26", "ITEM-B", 100),
    ("INV-5", "2026-02-01", "ITEM-N", 40), ("INV-6", "2026-02-20", "ITEM-Z", 10),
]


def snapshot_rows(on_hand: dict | None = None) -> list[dict]:
    held = dict(ON_HAND, **(on_hand or {}))
    return [{"item_name": NAMES[k], "warehouse_name": MAIN, "net_qty": str(q), "value_bhd": str(q * SELL_RATE[k]),
             "as_of_date": D.isoformat()} for k, q in held.items() if q]


def portal_rows(snap: list[dict]) -> list[dict]:
    """What the portal's statement returns for these rows (one per warehouse; numbers as JSON numbers)."""
    acc: dict[str, dict] = {}
    for r in snap:
        a = acc.setdefault(r["warehouse_name"], {"warehouse_name": r["warehouse_name"], "value_bhd": 0.0, "qty": 0.0,
                                                 "items": 0})
        a["value_bhd"] += float(r["value_bhd"])
        a["qty"] += float(r["net_qty"])
        a["items"] += 1
    return sorted(acc.values(), key=lambda a: -a["value_bhd"])


def stock_cost_rows() -> list[dict]:
    """The Command Centre's stock rows (metrics.STOCK_SQL) for the same snapshot."""
    return [{"item_name": NAMES[k], "division": "Accessories", "status": "healthy", "current_stock": q,
             "sell_value_bhd": q * SELL_RATE[k], "sold_90d": 0, "days_cover": None, "cost_bhd": PORTAL_COST[k],
             "cost_source": "focus_avg" if PORTAL_COST[k] else None} for k, q in ON_HAND.items()]


# The item master (catalog_items): every code of the world, and one the main store never held (an upcoming model).
CATALOG_ROWS = [{"code": CODES[k], "display_name": NAMES[k], "category": cat, "brand": "VFAN", "division": "Accessories",
                 "is_active": True, "hidden": k == "DISP"}
                for k, cat in (("A", "Cables"), ("B", "Chargers"), ("C", "Holders"), ("N", "Speakers"), ("F", "Fans"),
                               ("DISP", "Displays"))] + [
    {"code": "ZN9", "display_name": "ZZ-NEW Speaker Upcoming", "category": "Speakers", "brand": "WEKOME",
     "division": "Accessories", "is_active": True, "hidden": False}]


class World:
    """The reads of the command, answered from the invented world. Every statement must be a read."""

    def __init__(self, book: Book | None = None, snapshot=None, mrn=None, sheet=None, invoices=None, portal_cost=None,
                 fail: dict | None = None, catalog=None, po_vendor=None, cuts=None):
        self.book = book or world()
        self.snapshot = snapshot if snapshot is not None else snapshot_rows()
        self.mrn = list(MRN_ROWS if mrn is None else mrn)
        self.sheet = list(SHEET_ROWS if sheet is None else sheet)
        self.invoices = INVOICES if invoices is None else invoices
        self.portal_cost = stock_cost_rows() if portal_cost is None else portal_cost
        self.catalog = [dict(r) for r in (CATALOG_ROWS if catalog is None else catalog)]
        self.po_vendor = list(po_vendor or [])          # [(code, vendor)]
        self.cuts = list(cuts or [])                    # [(code, item name, changed on, was, now)]
        self.fail = dict(fail or {})
        self.sql: list[str] = []
        self.pg: list[str] = []                      # what went to the read-only session (the rest: the read RPC)

    def rpc(self, sql: str) -> list[dict]:
        db.check_sql(sql)
        self.sql.append(sql)
        if sql in self.fail:
            raise self.fail[sql]
        if sql == stock.SNAPSHOT_SQL:
            return [dict(r) for r in self.snapshot]
        if sql == stock.PORTAL_WAREHOUSE_SQL:
            return portal_rows(self.snapshot)
        if sql == metrics.STOCK_SQL:
            return self.portal_cost
        if sql == stock.PRICE_BOOK_SQL:
            return [{"sku_code": CODES[k], "item_name": NAMES[k]} for k in NAMES]
        if sql == stock.SUPPLIER_SQL:
            return [{"invoice_no": n, "invoice_date": d, "model": m, "qty": str(q), "vendor": "VFAN"}
                    for n, d, m, q in self.invoices]
        if sql == stock.MRN_SQL:
            return [{"id": i, "code": c.upper(), "cost": v, "effective_date": d, "doc_no": doc}
                    for i, c, v, d, doc in self.mrn]
        if sql == stock.CATALOG_SQL:
            return [dict(r) for r in self.catalog]
        if sql == stock.PO_VENDOR_SQL:
            return [{"code": c.upper(), "vendor": v} for c, v in self.po_vendor]
        if sql == stock.PRICE_CUT_SQL:
            return [{"code": c.upper(), "item_name": n, "changed_on": on, "was": str(was), "now": str(now)}
                    for c, n, on, was, now in self.cuts]
        raise AssertionError(f"a statement the stand-in does not know: {sql[:70]}")

    def session(self, **_kw):
        world_ = self

        class Session:
            def q(self, sql, params=None):
                db.check_sql(sql)
                world_.sql.append(sql)
                world_.pg.append(sql)
                if sql in world_.fail:
                    raise world_.fail[sql]
                assert sql == stock.SHEET_SQL, sql[:70]
                return [{"id": i, "code": c.upper(), "cost": v} for i, c, v in world_.sheet]

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return Session()

    def for_audit(self, prefer="auto"):
        rows = self.book.rows
        led = ledger.Ledger(rows=rows, source="Stock_ledger_test.xlsx", first_date=ledger.first_date(rows),
                            last_date=ledger.last_date(rows), sha=None, origin="file")
        return led, {"source": "file", "parity": None,
                     "note": "The stock ledger is read from the file Stock_ledger_test.xlsx: it agrees with what the "
                             "portal holds."}


TEMP_ROOTS: list[Path] = []           # the data roots data_root() made; the command runs in none other


class data_root:
    def __init__(self, cfg: dict | None = None, drop: tuple = ()):
        self.cfg = {k: v for k, v in dict(FIXTURE, **(cfg or {})).items() if k not in drop}

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name).resolve()
        paths.set_data_root(self.root)
        paths.write_text(paths.config_file(), json.dumps(self.cfg))
        config.use(None)
        TEMP_ROOTS.append(self.root)
        return self.root

    def __exit__(self, *exc):
        TEMP_ROOTS.remove(self.root)
        paths.set_data_root(None)
        config.use(None)
        self._tmp.cleanup()
        return False


def in_temp_root() -> None:
    """The command reads config.json and writes its screens under the data root: in a test that is a
    temporary one, never the project folder."""
    assert TEMP_ROOTS and paths.data_root() == TEMP_ROOTS[-1], "a test ran the command outside data_root()"


class standing_in:
    def __init__(self, w: World):
        self.w = w

    def __enter__(self):
        self.saved = (db.rpc, db.session, ledger.for_audit)
        db.rpc, db.session, ledger.for_audit = self.w.rpc, self.w.session, self.w.for_audit
        return self.w

    def __exit__(self, *exc):
        db.rpc, db.session, ledger.for_audit = self.saved
        return False


class Args:
    def __init__(self, *spec, ledger_="auto"):
        self.spec, self.ledger, self.json, self.data_root = list(spec), ledger_, False, None


def run(w: World | None = None, *spec) -> dict:
    in_temp_root()
    with standing_in(w or World()):
        return stock.run(Args(*spec))


def model_of(w: World | None = None):
    in_temp_root()
    with standing_in(w or World()):
        cfg = stock.settings()
        inp = stock.gather()
        return stock.build(inp, cfg), inp


def item(model, key: str):
    return next(it for it in model.items if it.ledger_name == L(key))


# ── words and settings ────────────────────────────────────────────────────────

@test("the words: nothing is the headline, reorder and ageing are views, anything else is an item code (spaces kept)")
def _():
    assert stock.parse_spec([]) == ("headline", None)
    assert stock.parse_spec(["reorder"]) == ("reorder", None) and stock.parse_spec(["REORDER"]) == ("reorder", None)
    assert stock.parse_spec(["ageing"]) == ("ageing", None) and stock.parse_spec(["aging"]) == ("ageing", None)
    assert stock.parse_spec(["X05", "UC"]) == ("item", "X05 UC") and stock.parse_spec(["X05 UC"]) == ("item", "X05 UC")
    assert stock.parse_spec(["reorder", "now"]) == ("item", "reorder now")
    assert "stock" in cli.COMMANDS and cli.COMMANDS["stock"][0] == "scripts.ops.stock"


@test("settings: the two optional keys take the plan's default and are named; config.get still refuses them elsewhere")
def _():
    with data_root(drop=("stock.fast_movers", "stock.cover_kpi_days")):
        cfg = stock.settings()
        assert (cfg.fast_n, cfg.low, cfg.high) == (stock.FAST_MOVERS_DEFAULT, *stock.COVER_KPI_DEFAULT)
        assert set(cfg.defaults_used) == {"stock.fast_movers", "stock.cover_kpi_days"}
        assert cfg.lead_default == 50 and cfg.display == frozenset({"ds1"})
        assert not set(config.missing_keys()) & config.OPTIONAL            # selftest stays clean on the owner's PC
        raises(config.ConfigMissing, lambda: config.get("stock.fast_movers"))  # no silent default for anyone else
    with data_root():
        cfg = stock.settings()
        assert (cfg.fast_n, cfg.low, cfg.high, cfg.defaults_used) == (2, 30, 45, ())
    for bad in ([45, 30], [30], [0, 45], [30, "45"], "30-45"):
        with data_root({"stock.cover_kpi_days": bad}):
            raises(config.ConfigMissing, stock.settings)
    with data_root({"stock.lead_time_days": 0}):
        raises(config.ConfigMissing, stock.settings)
    with data_root(drop=("stock.lead_time_days",)):
        assert "stock.lead_time_days" in config.missing_keys()
        raises(config.ConfigMissing, stock.settings)                      # the lead time is never defaulted
    # the optional keys with no default in config.py are these three (the empty tables and texts are the others)
    assert config.OPTIONAL - set(config.DEFAULTS) == {"stock.fast_movers", "stock.cover_kpi_days",
                                                      "stock.clear_target_days", "stock.order_every_days",
                                                      "stock.position_stores", "stock.van_floor_days",
                                                      "watch.floor_bhd", "watch.act_bhd", "watch.act_days",
                                                      "report.bulk_invoice_bhd", "kpi.weekly_due_weekday",
                                                      "kpi.monthly_due_working_day", "kpi.working_days",
                                                      "kpi.fast_mover_availability_min", "kpi.new_skus_band",
                                                      "kpi.major_purchase_bhd", "kpi.shop_min_bhd"}
    assert set(config.DEFAULTS) <= config.OPTIONAL <= set(config.KEYS)
    with data_root(drop=("stock.clear_target_days", "warehouses.damage")):
        cfg = stock.settings()
        assert cfg.clear_days == stock.CLEAR_DAYS_DEFAULT == 90 and "stock.clear_target_days" in cfg.defaults_used
        assert cfg.damage == "" and cfg.suppliers == {} and cfg.supplier_aliases == {}   # empty defaults, no notice
    with data_root({"stock.suppliers": {" ca1 ": " Local  Trader ", "": "x"},
                    "stock.supplier_aliases": {"VFAN Co": "VFAN"}}):
        cfg = stock.settings()
        assert cfg.suppliers == {"CA1": "Local Trader"} and cfg.supplier_aliases == {"vfan co": "VFAN"}
        assert cfg.damage == DAMAGE and cfg.not_counted == (DAMAGE, SAMPLES)
    with data_root({"stock.clear_target_days": 0}):
        raises(config.ConfigMissing, stock.settings)


# ── the rate, cover and the order list ────────────────────────────────────────

@test("the daily rate: units sold in every warehouse ÷ the days in stock in the main store, never fewer than 30")
def _():
    with data_root():
        m, _inp = model_of()
    rates = m.facts["fast"]["rates"]
    a, b, n = rates[L("A")], rates[L("B")], rates[L("N")]
    assert (a.sold, a.days_in_stock, a.rate) == (180, 90, 2)               # sold in the van, never in the main store
    assert (b.sold, b.days_in_stock, b.rate) == (45, 45, 1)                # out for 45 days: they do not count
    assert (n.sold, n.days_in_stock) == (4, 15) and n.rate == Decimal(4) / 30 and n.floored
    assert not a.floored and not b.floored
    assert rates[L("C")].rate == 0 and rates[L("F")].rate == 0
    assert L("DISP") not in rates                                          # display items are not planned
    assert SIM not in {it.ledger_name for it in m.items}                   # never in the main store: no accessory
    assert item(m, "A").on_hand == 394 and item(m, "B").on_hand == 20


@test("the order list: below lead time + low it is listed, filled to lead time + high, in whole units, at cost")
def _():
    with data_root():
        m, _inp = model_of()
    f = m.facts
    assert f["lead"]["days"] == 40 and f["trigger_days"] == 70 and f["fill_days"] == 85
    assert [x["item"].ledger_name for x in f["order"]] == [L("B")]         # A covers 197 days, C, F sell nothing
    x = f["order"][0]
    assert x["cover"] == 20 and x["qty"] == 65                             # 85 x 1 - 20
    assert x["cost_bhd"] == Decimal("162.500") and item(m, "B").cost_source == "receipt"   # 65 x 250/100
    # a rate that is not whole: the quantity is rounded up
    w = world()
    w.sale(L("B"), D, 1)                                                    # 46 sold: 46/45 a day
    with data_root():
        m2, _ = model_of(World(book=w))
    q = next(x for x in m2.facts["order"] if x["item"].ledger_name == L("B"))["qty"]
    assert q == 67, q                                                       # 85 x 46/45 - 20 = 66.9 -> 67
    with data_root({"stock.display_items": ["CB2"]}):
        m3, _ = model_of()
    assert not m3.facts["order"]                                            # a display item is never ordered


@test("goods on order: not deducted until a promise is logged; one counts until a receipt is dated after it")
def _():
    with data_root() as root:
        text = run(World())["text"]
        assert "goods on order not deducted" in text
        log = root / "business_data" / "ops" / "kpi_log.jsonl"
        lines = [
            {"id": 1, "kind": "promise", "ref": "p1", "item_code": "cb2", "qty": 30, "event_date": "2026-03-01"},
            {"id": 2, "kind": "promise", "ref": "p2", "item_code": "CB2", "qty": 50, "event_date": "2026-01-01"},
            {"id": 3, "kind": "promise", "ref": "p3", "item_code": "CB2", "qty": 99, "event_date": "2026-03-02",
             "state": "closed"},
            {"id": 4, "kind": "promise", "ref": "p4", "item_code": "NOPE-9", "qty": 5, "event_date": "2026-03-02"},
            {"id": 5, "kind": "quote", "ref": "q1", "item_code": "CB2", "qty": 500},
        ]
        paths.write_text(log, "\n".join(json.dumps(x) for x in lines) + "\nnot json\n")
        with standing_in(World()):
            m = stock.build(stock.gather(), stock.settings())
        oo = m.facts["on_order"]
        assert (oo["logged"], oo["counted"], oo["unmatched"]) == (3, 1, 1), oo   # p2 arrived on 30 Jan; p3 closed
        assert item(m, "B").on_order == 30
        assert m.facts["order"][0]["qty"] == 35                             # 85 - 20 - 30
        out = run(World(), "reorder")["text"]
        assert "goods on order deducted (1 promise line of the KPI log)" in out
        assert "1 promise line of the KPI log matches no item, to check." in out


@test("the lead time: an invoice beside the receipt that holds the same quantity of the most of its models")
def _():
    rec = {"R1": {"date": date(2026, 1, 10), "codes": {"M1": Decimal(10), "M2": Decimal(5)}},
           "R2": {"date": date(2026, 2, 1), "codes": {"M1": Decimal(10), "M2": Decimal(5), "M3": Decimal(7)}},
           "R0": {"date": date(2025, 12, 1), "codes": {"M1": Decimal(10), "M2": Decimal(5), "M3": Decimal(7)}}}

    def inv(number, day, *models):
        return [{"invoice_no": number, "invoice_date": day, "model": m, "qty": str(q)} for m, q in models]

    got = stock.measure_lead_times(inv("I1", "2025-12-20", ("M1", 10), ("M2", 5), ("M3", 7)), rec)
    assert [(g["receipt"], g["days"], g["same"]) for g in got] == [("R2", 43, 3)]     # R0 is before it: never
    got = stock.measure_lead_times(inv("I2", "2025-12-20", ("M1", 10), ("M2", 5)), rec)
    assert [g["receipt"] for g in got] == ["R1"]                                     # a tie: the earlier one
    assert stock.measure_lead_times(inv("I3", "2025-12-20", ("M1", 10), ("M2", 6), ("M3", 8)), rec) == []  # 1 of 3
    assert stock.measure_lead_times(inv("I4", "2025-04-01", ("M1", 10)), rec) == []  # more than LEAD_MAX_DAYS
    assert [g["receipt"] for g in stock.measure_lead_times(inv("I4", "2025-06-01", ("M1", 10)), rec)] == ["R0"]
    two = inv("I5", "2025-12-20", ("M1", 4), ("M1", 6), ("M2", 5))                   # one model on two lines
    assert [g["same"] for g in stock.measure_lead_times(two, rec)] == [2]
    # fewer than three: the config value; three or more: the median of the newest ten
    shots = [{"days": n, "receipt_date": date(2025, 1, 1) + timedelta(days=i), "invoice_date": None, "invoice": str(i)}
             for i, n in enumerate([90, 10, 20, 30, 40, 50, 60, 70, 80, 15, 25, 35])]
    assert stock.lead_time(shots[:2], 56) == {"days": 56, "basis": "config", "shown": shots[:2], "default": 56}
    assert stock.lead_time(shots[:3], 56)["days"] == 20                              # 10, 20, 90
    four = stock.lead_time(shots[:4], 56)
    assert four["days"] == 25 and four["basis"] == "measured"                        # (20 + 30) / 2
    ten = stock.lead_time(shots, 56)
    assert [s["invoice"] for s in ten["shown"]] == [str(i) for i in range(2, 12)]    # the newest ten
    assert ten["days"] == 38                                                          # 35 and 40 -> 37.5 -> 38
    with data_root():
        m, _inp = model_of()
    lead = m.facts["lead"]
    assert lead["basis"] == "measured" and lead["days"] == 40                         # 28, 31, 40, 40, 45
    assert [x["receipt"] for x in lead["shown"]] == ["MRN:T-25-02-1", "MRN:T-25-03-1", "MRN:T-25-06-1",
                                                     "MRN:T-26-01-1", "MRN:T-26-03-1"]
    with data_root():
        m, _inp = model_of(World(invoices=INVOICES[:4]))
    assert m.facts["lead"]["basis"] == "config" and m.facts["lead"]["days"] == 50


# ── at cost ───────────────────────────────────────────────────────────────────

@test("at cost: the MRN landed cost (the latest receipt date, then the highest id), the pricing sheet only where no MRN")
def _():
    got = stock.mrn_costs([
        {"id": 9, "code": "ca1", "cost": "0.3", "effective_date": "2025-02-01", "doc_no": "R-OLD"},
        {"id": 5, "code": "CA1", "cost": "0.4", "effective_date": "2026-01-10", "doc_no": "R-NEW"},
        {"id": 3, "code": "SN4", "cost": "3.1", "effective_date": "2026-03-01"},
        {"id": 4, "code": "SN4", "cost": "3.2", "effective_date": "2026-03-01"},
        {"id": 7, "code": "HG3", "cost": "0", "effective_date": "2026-03-01"},
        {"id": 2, "code": "UND", "cost": "1.0", "effective_date": None},
        {"id": 1, "code": "UND", "cost": "1.5", "effective_date": "2025-01-01"},
        {"id": 11, "code": "NUL", "cost": "2.0", "effective_date": None},
        {"id": 12, "code": "NUL", "cost": "2.5", "effective_date": None},
        {"id": 13, "code": None, "cost": "9.0", "effective_date": "2026-01-01"}])
    assert got["CA1"]["cost"] == Decimal("0.4") and got["CA1"]["doc_no"] == "R-NEW"   # the date wins over a higher id
    assert got["SN4"]["cost"] == Decimal("3.2")                                         # one date: the higher id
    assert got["UND"]["cost"] == Decimal("1.5") and got["NUL"]["cost"] == Decimal("2.5")   # undated last (NULLS LAST)
    assert set(got) == {"CA1", "SN4", "UND", "NUL"}                                    # nought and no code: never
    sheet = stock.sheet_costs([{"id": 21, "code": "DS1", "cost": "9.5"}, {"id": 25, "code": "ds1", "cost": "10.0"},
                               {"id": 26, "code": "DS1", "cost": "0"}, {"id": 23, "code": "HG3", "cost": "0"}])
    assert sheet == {"DS1": {"cost": Decimal("10.0"), "id": 25}}, sheet                # the newest row with a cost
    with data_root():
        m, _inp = model_of()
    src = {key: (item(m, key).cost_source, item(m, key).cost) for key in ("A", "B", "C", "N", "F", "DISP")}
    assert src == {"A": ("mrn", Decimal("0.4")), "N": ("mrn", Decimal("3.2")), "DISP": ("sheet", Decimal("10.0")),
                   "F": ("sheet", Decimal("1.4")), "B": ("receipt", Decimal("2.5")), "C": ("receipt", Decimal("0.8"))}, src
    assert m.facts["cost_basis"] == {"mrn": 2, "sheet": 2, "receipt": 2, "none": 0}    # every accessory item
    head = m.facts["headline"]
    assert (head["cost_mrn_items"], head["cost_sheet_items"], head["cost_receipt_items"], head["uncosted_items"]) \
        == (2, 1, 2, 0)                                                                 # the items held
    with data_root():
        reorder, ageing = run(World(), "reorder")["text"], run(World(), "ageing")["text"]
    assert "BHD 162.500 at cost (the latest receipt rate where neither: 1 item)." in reorder, reorder
    # the ageing counts its own items (the display item is left out of it): A and ITEM-N on MRN, B and C on receipts
    assert "BHD at landed cost (MRN; the latest receipt rate where neither: 2 items), by the days since" in ageing, ageing


@test("the MRN rule is the portal's own: v_product_economics takes mrn_landed_costs first, newest receipt, then id")
def _():
    text = " ".join((ROOT / "scripts" / "r7d_margin_exvat_migration.sql").read_text(encoding="utf-8").lower().split())
    view = text[text.index("create or replace view v_product_economics as"):]
    view = view[:view.index(") c on true")]
    mrn, sheet = view.index("from mrn_landed_costs m"), view.index("from purchase_costs p")
    assert mrn < sheet and "'mrn'::text as cost_source" in view[:mrn] and "0 as pri" in view[:mrn]
    assert "where upper(m.sku_code) = upper(pl.sku_code) and coalesce(m.landed_cost_bhd, 0) > 0" in view
    assert "order by x.pri, x.cost_effective_date desc nulls last, x.id desc limit 1" in view
    for word in ("mrn_landed_costs", "effective_date", "doc_no", " id"):
        assert word in stock.MRN_SQL, word                         # what mrn_costs() needs to apply that order
    assert "purchase_costs" in stock.SHEET_SQL and " id" in stock.SHEET_SQL


# ── KPI 1 ─────────────────────────────────────────────────────────────────────

@test("KPI 1: aggregate cover at cost, the share above the band, last month's end, new arrivals apart")
def _():
    with data_root():
        m, _inp = model_of()
    k = m.facts["kpi_cover"]
    # now: A 394 x 0.4 + B 20 x 2.5 + C 50 x 0.8 = 247.6 against a day of 2 x 0.4 + 1 x 2.5 = 3.3
    assert k["now"]["value"] == Decimal("247.6") and k["now"]["days"] == Decimal("247.6") / Decimal("3.3")
    assert k["now"]["above_value"] == Decimal("197.6") and k["now"]["new_items"] == 1    # A and C; ITEM-N apart
    # 28 Feb: A 414, B 35, C 50 (ITEM-N not yet received): 293.1 against 3.3
    assert k["last_month_end"] == date(2026, 2, 28) and k["last"]["value"] == Decimal("293.1")
    assert k["last"]["days"] == Decimal("293.1") / Decimal("3.3")
    bands = {b["band"]: (b["items"], b["units"], b["value"]) for b in m.facts["bands"] if b["items"]}
    assert bands == {"under 30 days": (1, 20, Decimal("50.0")), "over 180 to 365": (1, 394, Decimal("157.6")),
                     "no sale in 90 days": (1, 50, Decimal("40.0")), "new arrivals (apart)": (1, 36, Decimal("115.2"))}
    assert sum(b["units"] for b in m.facts["bands"]) == 505 - 5                          # the main store less display
    with data_root():
        text = run(World())["text"]
    assert "Aggregate cover 75 days (on Sat 28 Feb: 89 days, -14); 79.8% of the value is above 45 days" in text, text
    assert "The last supplier receipt (MRN:T-26-03-1, Sun 01 Mar) holds 10.0 months of sales of its items" in text


# ── fast movers ───────────────────────────────────────────────────────────────

@test("fast movers: last year's best sellers by units; a day counts when its end covers a day of sales")
def _():
    with data_root():
        m, _inp = model_of()
    fa = m.facts["fast"]
    assert [it.ledger_name for it in fa["list"]] == [L("A"), L("F")]                    # 730 and 400 units
    assert (fa["rank_first"], fa["rank_last"]) == (date(2025, 3, 1), date(2026, 2, 28)) and not fa["partial"]
    assert item(m, "B").rank == 3 and item(m, "B").sold_rank == 60 and item(m, "N").rank is None
    now, last = fa["now"], fa["last"]
    assert (now["available"], now["sku_days"], now["days"]) == (15, 30, 15)             # A every day, F never
    assert [it.ledger_name for it in now["out_all"]] == [L("F")]
    assert (now["wide_items"], now["wide_available"], now["wide_sku_days"]) == (3, 45, 45)   # A, B and ITEM-N
    assert (last["available"], last["sku_days"]) == (28, 56) and [it.ledger_name for it in last["out_all"]] == [L("F")]
    # a day-end balance below one day of sales does not count: ITEM-B down to 1 unit with a rate of 2
    it = stock.Item(ledger_name="x", name="x", code="x", series=[Decimal(5), Decimal(1), Decimal(0), Decimal(2)])
    assert stock.available_days(it, date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 4), Decimal(2)) == 2
    assert stock.available_days(it, date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 4), Decimal("0.2")) == 3
    with data_root():
        text = run(World())["text"]
    for line in ("FAST MOVERS  the 2 best sellers by units, Mar 2025 to Feb 2026",
                 "This month so far: 50.0% of SKU-days available (every item that sold: 100.0%)",
                 "Feb 2026 (that month's fast movers): 50.0% of SKU-days available (every item that sold: 100.0%)",
                 "Out all of Feb 2026: ITEM-F Fan Out", "Out every day this month: ITEM-F Fan Out"):
        assert line in text, line


# ── ageing ────────────────────────────────────────────────────────────────────

@test("ageing: newest receipt first, a van return never makes stock younger, the bands add up to the main store")
def _():
    receipts = [(date(2025, 2, 1), "R1", Decimal(1000), ZERO_V), (date(2026, 1, 10), "R2", Decimal(200), ZERO_V)]
    assert stock.allocate(Decimal(394), receipts) == [(date(2026, 1, 10), "R2", Decimal(200)),
                                                     (date(2025, 2, 1), "R1", Decimal(194))]
    assert stock.allocate(Decimal(1300), receipts)[-1] == (None, None, Decimal(100))     # no receipt explains these
    assert stock.allocate(Decimal(0), receipts) == [] and stock.allocate(Decimal(-3), receipts) == []
    with data_root():
        m, _inp = model_of()
    ag = m.facts["ageing"]
    rows = {r["band"]: (len(r["items"]), r["units"], r["value"]) for r in ag["rows"] if r["units"]}
    # up to 90 days: A 200 (10 Jan; the 10 returned on 1 Mar are NOT young), B 20, ITEM-N 36
    assert rows == {"up to 90 days": (3, 256, Decimal("245.2")), "over 365 days": (2, 244, Decimal("117.6"))}, rows
    assert ag["units"] + ag["display_units"] == ag["main_units"] == 505
    sold30, never = 0, 3
    assert ag["cells"][3][never]["units"] == 50 and ag["cells"][3][sold30]["units"] == 194   # C never sold; A today
    assert [x["item"].ledger_name for x in ag["oldest"]] == [L("A"), L("C")]
    with data_root():
        text = run(World(), "ageing")["text"]
        head = run(World())["text"]
    assert "The bands add up to 505 units, the same as the main store's 505 units." in text
    assert "display items (left out)" in text
    for action in (stock.AGE_ACTIONS[0], stock.AGE_ACTIONS[3]):
        assert action in text
    assert "still selling: order no more until it runs down" in text and "not selling: push it through the sales team" in text
    for word in ("mark it down", "bundle", "cut the price", "lower the price", "markdown"):   # the only lever is the
        assert word not in text.lower(), word                                                 # sales team (20-Oct)
    assert "AGEING  32.4% of the value at cost has been in stock over 180 days" in head          # 117.6 / 362.8


ZERO_V = Decimal(0)


# ── the sellable pool and the damage store (scripts.ops.stockflow) ───────────

@test("stockflow: the pool is main + vans + goods on the way; damage counts on the issue leg; booked and left apart")
def _():
    from scripts.ops import stockflow
    x = "ITEM-X Test Flow XX1"
    b = Book()
    b.receipt(x, date(2026, 1, 1), 100, 50, "MRN:T-X-1")
    b.move(x, date(2026, 1, 2), 30, MAIN, VAN)                          # inside the pool: nothing changes
    b.sale(x, date(2026, 1, 3), 5, wh=VAN)                              # 95
    b.move(x, date(2026, 1, 4), 10, MAIN, VAN_B, received=False)        # on its way: still in the pool
    b.to_damage(x, date(2026, 1, 5), 4)                                 # from the main store, booked: 91
    b.to_damage(x, date(2026, 1, 6), 3, frm=VAN, booked=False)          # from a van, never booked: 88
    b.move(x, date(2026, 1, 7), 2, MAIN, SAMPLES)                       # out to the samples store: 86
    b.move(x, date(2026, 1, 8), 1, SAMPLES, MAIN)                       # one comes back: 87
    b._row(x, date(2026, 1, 9), ledger.ISSUE, MAIN, "SIO:T-NODEST", qout=1, to_wh=None)   # no destination: stays
    b._row(x, date(2026, 1, 10), ledger.COUNT_SHORT, DAMAGE, "SH:T-1", qout=1)            # one written off there
    b.sale("OTHER Item YY2", date(2026, 1, 3), 9, wh=VAN)               # an item not asked for: passed over
    with data_root():
        fl = stockflow.flows(b.rows, [x], DAMAGE, [DAMAGE, SAMPLES], date(2026, 1, 10))
    f = fl.get(x)
    assert fl.damage_store == DAMAGE and fl.damage_found and fl.no_destination == 1
    assert set(fl.outside) == {DAMAGE, SAMPLES} and "OTHER Item YY2" not in fl.items
    assert f.sellable_at(date(2025, 12, 31)) == 0 and f.sellable_at(date(2026, 1, 1)) == 100
    assert f.sellable_at(date(2026, 1, 4)) == 95                        # the unreceived transfer stays in the pool
    assert f.sellable_at(date(2026, 1, 10)) == 87, f.sellable_at(date(2026, 1, 10))
    # the main store: 100 - 30 - 10 - 4 - 2 + 1 - 1 = 54 (every voucher there, receive legs included)
    assert f.main_at(date(2026, 1, 10)) == 54
    assert [(d_, src, u) for d_, src, u in f.sent_damage] == [(date(2026, 1, 5), "main", 4),
                                                             (date(2026, 1, 6), "elsewhere", 3)]
    assert f.sent_between(None, date(2026, 1, 10)) == 7 and f.sent_between(None, date(2026, 1, 10), "main") == 4
    assert f.booked_between(None, date(2026, 1, 10)) == 4 and f.left_between(None, date(2026, 1, 10)) == 1
    assert f.damage_at(date(2026, 1, 9)) == 4 and f.damage_at(date(2026, 1, 10)) == 3
    assert f.sellers == {VAN: 5}
    # no damage store set up: nothing is damage, and the store is outside the pool only if not_counted says so
    with data_root():
        fl2 = stockflow.flows(b.rows, [x], "", [], date(2026, 1, 10))
    assert fl2.damage_store is None and not fl2.damage_found and fl2.get(x).sent_damage == []


# ── the headline ──────────────────────────────────────────────────────────────

@test("the headline is the portal's: its own statement word for word, the main store's row; at cost side by side")
def _():
    seen: list[str] = []
    saved = reports.exec_sql
    reports.exec_sql = lambda sql, *a, **kw: seen.append(sql) or []
    try:
        reports.stock_by_warehouse()
    finally:
        reports.exec_sql = saved
    assert seen == [stock.PORTAL_WAREHOUSE_SQL], "the Inventory page reads the stock per warehouse differently now"
    with data_root():
        out = run(World())
    text = out["text"]
    assert out["ok"] and out["data_date"] == D.isoformat() and out["command"] == "stock"
    assert "STOCK  main store, Main Store: stock as on Sun 15 Mar, ledger to Sun 15 Mar" in text
    assert "  5 items, 505 units, BHD 815.000 at selling price (the portal's figures)" in text
    # 394 x 0.4 + 20 x 2.5 + 50 x 0.8 + 36 x 3.2 + 5 x 10 (MRN for A and N, the sheet for the display, receipt rate B, C)
    assert ("  At landed cost BHD 412.800 (MRN; the pricing sheet where no MRN: 1 item; the latest receipt rate where "
            "neither: 2 items)") in text, text
    assert ("  The portal at cost: BHD 381.900 (Focus average cost, else the latest landed cost; 1 item without a cost "
            "there)") in text
    assert "The ledger agrees with the stock balance on all 5 items (Sun 15 Mar)." in text
    assert "Display items: 1 item, 5 units, left out of cover, the order list and the ageing." in text
    # the fixture counts the main store only: the second van's 35 of ITEM-B (35 x 2.5) are shown and left out
    assert ("  Vans and outlets: 35 units in 1 van (BHD 87.500), nothing in the outlets, nothing between stores; not "
            "counted in the order list (config 'stock.position_stores' is empty).") in text, text
    assert stock.vans_beside(None) == [] and "Vans" not in next(ln for ln in text.splitlines() if "Item" in ln
                                                                 and "Code" in ln)
    assert "ORDER LIST  lead time 40 days, measured on the last 5 supplier invoices (config 50); goods on order not " \
           "deducted" in text
    assert "1 item below 70 days of cover; ordered up to 85 days: 65 units, BHD 162.500 at cost" in text
    # the same figure on both bases: one line
    same = [dict(r, cost_bhd=float(c) if c is not None else None) for r, c in
            zip(stock_cost_rows(), ["0.4", "2.5", "0.8", "3.2", "10.0"])]
    with data_root():
        text = run(World(portal_cost=same))["text"]
    assert "The portal's figure at cost is the same." in text and "The portal at cost:" not in text
    # the ledger and the snapshot disagree on one item: said, to check
    with data_root():
        text = run(World(snapshot=snapshot_rows({"A": 393})))["text"]
    assert "The ledger differs from the stock balance on 1 item, to check: ITEM-A Cable Alpha" in text
    assert "  5 items, 504 units" in text                                                  # the headline stays the portal's


# ── one item ──────────────────────────────────────────────────────────────────

@test("one item: by its code, a model code several items share lists them, an unknown code is one sentence")
def _():
    with data_root():
        text = run(World(), "cb2")["text"]
        assert text.startswith("ITEM  CB2: ITEM-B Charger Beta")
        assert "Main store, end of Sun 15 Mar: 20 units; BHD 100.000 at selling price" in text
        assert "latest receipt rate 2.500 a unit" in text
        assert "Sold in the last 90 days: 45 units, in stock at the end of 45 of those days: 1.00 a day; cover 20 days" in text
        assert "Order list: order 65 units (BHD 162.500 at cost)" in text
        assert "MRN:T-26-01-2" in text and "MRN:T-25-06-1" in text
        text = run(World(), "item-a")["text"]
        assert text.startswith("ITEM  CA1: ITEM-A Cable Alpha") and "rank 1 (a fast mover)" in text
        new = run(World(), "SN4")["text"]
        assert "0.13* a day" in new and "the rate divides by 30" in new
        assert "(MRN landed cost 3.200 a unit, receipt T-26-03-1 of Sun 01 Mar)" in new, new
        sheet = run(World(), "DS1")["text"]
        assert "(the pricing sheet 10.000 a unit: no MRN landed cost for DS1)" in sheet, sheet
        assert "neither an MRN landed cost nor a pricing-sheet cost for CB2" in run(World(), "CB2")["text"]
        many = run(World(), "item")["text"]
        assert many.startswith("5 items answer to 'item': type one of these codes"), many[:80]
        out = run(World(), "zz-9")
        assert not out["ok"] and out["exit"] == 1 and "No accessory item has the code 'zz-9'" in out["text"]
        disp = run(World(), "DS1")["text"]
        assert "(a display item)" in disp and "Order list: display items are left out." in disp


# ── the whole command ─────────────────────────────────────────────────────────

@test("the command: files under exports/ only, its facts parse, the words are careful, no long digit run")
def _():
    with data_root() as root:
        before = {p for p in root.rglob("*") if p.is_file()}
        outs = [run(World(), *spec) for spec in ((), ("reorder",), ("ageing",), ("CB2",))]
        after = {p for p in root.rglob("*") if p.is_file()} - before
        folder = root / "exports" / "ops" / D.isoformat()
        books = {p for p in after if p.parent == root / "exports" / "reports" / "stock"}
        after -= books
        assert after == {folder / f"{stem}.{ext}" for stem in ("stock", "stock-reorder", "stock-ageing", "stock-item-cb2")
                         for ext in ("txt", "json")}, sorted(str(p) for p in after)
        # the headline makes the day's workbook once (the other views make none); the stub of P0 makes none at all
        assert {p.name for p in books} <= {f"YQ Stock and Order Plan {D.isoformat()}.xlsx"}, sorted(p.name for p in books)
        for out in outs:
            assert out["ok"] and out["files"] and all(Path(f).parent == folder for f in out["files"])
            text = out["text"]
            assert not NINE.search(text) and not LONG.search(text)
            for line in text.splitlines():
                fmt.careful(line)
        facts = json.loads((folder / "stock.json").read_text(encoding="utf-8"))
        assert facts["headline"]["units"] == "505" and facts["lead"]["days"] == 40 and facts["view"] == "headline"
        assert facts["order"][0]["code"] == "CB2" and facts["order"][0]["qty"] == "65"
        assert (folder / "stock.txt").read_text(encoding="utf-8").rstrip("\n") == outs[0]["text"]
        flags = dict(outs[0]["flags"])
        book = flags.pop("file", None)                 # the day's workbook, once its builder makes one
        assert flags == {"view": "headline", "order_items": 1, "lead_basis": "measured", "goods_on_order": False}
        assert book is None or Path(book).name == f"YQ Stock and Order Plan {D.isoformat()}.xlsx", book
        reorder = outs[1]["text"]
        assert "No purchase order is drafted." in reorder
        assert "LEAD TIME  40 days: the median of the last 5 supplier invoices to their receipt" in reorder
        assert "INV-4" in reorder and "1 of 2 models same quantity" in reorder and "INV-6" not in reorder


@test("the reads: every statement is a read; a failed optional read costs its own part, named under the screen")
def _():
    for sql in (stock.SNAPSHOT_SQL, stock.PORTAL_WAREHOUSE_SQL, stock.PRICE_BOOK_SQL, stock.SUPPLIER_SQL,
                stock.MRN_SQL, stock.SHEET_SQL, metrics.STOCK_SQL, stock.CATALOG_SQL, stock.PO_VENDOR_SQL,
                stock.PRICE_CUT_SQL):
        db.check_sql(sql)
    w = World()
    with data_root():
        run(w)
    assert w.sql.count(stock.SNAPSHOT_SQL) == 1
    assert w.pg == [stock.SHEET_SQL] and stock.MRN_SQL in w.sql      # purchase_costs on the session; the MRN on the RPC
    # the MRN landed costs not read: the sheet stands in for A (0.45), and ITEM-N, which the sheet lacks, takes its
    # receipt rate: 394 x 0.45 + 36 x 3 + 5 x 10 + 20 x 2.5 + 50 x 0.8
    w = World(fail={stock.MRN_SQL: RuntimeError("down")})
    with data_root():
        text = run(w)["text"]
    assert "Not read this time: the MRN landed costs (mrn_landed_costs) (RuntimeError)." in text
    assert ("The MRN landed costs could not be read: the pricing sheet stands in where it has the item, else the "
            "latest receipt rate.") in text
    assert ("At landed cost BHD 425.300 (the pricing sheet where no MRN: 2 items; the latest receipt rate where "
            "neither: 3 items)") in text, text
    w = World(fail={stock.SUPPLIER_SQL: RuntimeError("down"), stock.MRN_SQL: RuntimeError("down"),
                    stock.SHEET_SQL: RuntimeError("down")})
    with data_root():
        text = run(w)["text"]
    assert "Not read this time: the supplier invoices (lead time) (RuntimeError)." in text
    assert "Not read this time: the pricing sheet (purchase_costs) (RuntimeError)." in text
    assert "The pricing sheet could not be read: an item with no MRN landed cost takes the latest receipt rate." in text
    assert "lead time 50 days (config: too few supplier invoices matched to a receipt to measure it)" in text
    # 394 x 0.5 + 20 x 2.5 + 50 x 0.8 + 36 x 3 + 5 x 10, every one a receipt rate: no landed cost was read
    assert "  At cost BHD 445.000 (the latest receipt rate where neither: 5 items)" in text, text
    w = World(fail={stock.SNAPSHOT_SQL: RuntimeError("down")})
    with data_root():
        raises(RuntimeError, lambda: run(w))                                             # the snapshot is required
    with data_root(drop=("stock.fast_movers", "stock.cover_kpi_days")):
        text = run(World())["text"]
    assert "(config 'stock.fast_movers' and 'stock.cover_kpi_days' not set in config.json: the plan's default is used)" \
        in text


@test("an empty ledger is one sentence; the module never imports a client or names the writer")
def _():
    with data_root():
        out = run(World(book=Book()))
    assert not out["ok"] and out["exit"] == 1 and "No stock ledger is loaded" in out["text"]
    for module in ("stock.py", "stockflow.py", "stock_damage.py", "stock_review.py", "stock_plan.py", "stock_season.py",
                   "stock_orders.py", "workbook/stock_book.py", "workbook/readback.py", "workbook/stock_team.py"):
        f = ROOT / "scripts" / "ops" / module
        if not f.is_file():
            continue
        src = f.read_text(encoding="utf-8")
        for word in ("import psycopg", "import httpx", "supabase", "subprocess", "yq_load_go", ".table(", "insert",
                     "upsert"):
            assert word not in src, (module, word)


# ── release 2: the rhythm, the season, goods on order, an order placed ───────

class OptArgs(Args):
    """The command's options as argparse gives them: --line (repeated), --date, --file, --commit."""

    def __init__(self, *spec, line=(), date_=None, file=None, commit=None):
        super().__init__(*spec)
        self.line, self.date, self.file, self.commit = list(line), date_, file, commit


def run_opts(w: World | None = None, *spec, **kw) -> dict:
    in_temp_root()
    with standing_in(w or World()):
        return stock.run(OptArgs(*spec, **kw))


@test("release 2: a monthly rhythm adds its days to the join and the fill (B: 40 + 30 + 30 = 100, ordered up to 115)")
def _():
    with data_root({"stock.order_every_days": 30}):
        m, _inp = model_of()
        text = run(World())["text"]
    f = m.facts
    (row,) = f["order"]
    # ITEM-B: a rate of 1.00 a day, 20 on hand, nothing on the way: 115 x 1 - 20 = 95 (release 1: 85 - 20 = 65)
    assert (row["item"].code, row["qty"], row["trig_days"], row["fill_days"], row["supplier"]) == ("CB2", 95, 100, 115,
                                                                                                  "VFAN"), row
    assert f["supplier_rules"]["VFAN"]["lead"] == 40 and f["supplier_rules"]["VFAN"]["every"] == 30
    assert ("Rule: an item is listed when its demand reaches its stock and goods on the way within the lead time + 30 "
            "days to the next order + 30 = 100 days, and ordered up to 115 days") in text, text
    with data_root({"stock.order_every_days": 30, "stock.supplier_every_days": {"VFAN": 0}}):
        m, _inp = model_of()
    assert m.facts["order"][0]["qty"] == 65                     # a supplier's own rhythm wins over the setting
    for bad in (-1, "30"):
        with data_root({"stock.order_every_days": bad}):
            raises(config.ConfigMissing, stock.settings)
    with data_root({"stock.supplier_lead_days": {"VFAN": 0}}):
        raises(config.ConfigMissing, stock.settings)


@test("release 2: the season lifts each day's demand by its month (2.00 everywhere: B joins at 140 and takes 150)")
def _():
    with data_root({"stock.season_lift": {str(k): 2 for k in range(1, 13)}}):
        m, _inp = model_of()
        reorder = run(World(), "reorder")["text"]
    (row,) = m.facts["order"]
    # every day counts twice: fill 85 days -> 170 days of demand at 1.00 a day, less the 20 on hand = 150
    assert (row["item"].code, row["qty"]) == ("CB2", 150), row
    assert m.facts["season"]["applied"] and all(v["basis"] == "config" for v in m.facts["season"]["lifts"].values())
    assert "SEASON  each month's lift" in reorder and "Apr 2.00 (set)" in reorder, reorder
    assert "SUPPLIERS  each supplier's rule" in reorder and "VFAN" in reorder


@test("release 2: goods on order count until the item's later receipts pay them, the oldest line first")
def _():
    with data_root():
        paths.write_text(stock.kpi_log_file(), json.dumps(
            {"kind": "promise", "ref": "vfan:2026-03-01:CB2", "supplier": "VFAN", "item_code": "CB2", "qty": "30",
             "event_date": "2026-03-01", "logged_at": "2026-03-01T10:00:00", "state": "open", "source": "typed"}) + "\n")
        m, _inp = model_of()
        reorder = run(World(), "reorder")["text"]
    b = item(m, "B")
    # B's last receipt (30 Jan) is before the line: all 30 are due; 85 - (20 + 30) = 35 to order
    assert b.on_order == 30 and m.facts["order"][0]["qty"] == 35 and m.facts["on_order"]["counted"] == 1
    assert "goods on order deducted (1 promise line of the KPI log)" in reorder
    assert "ON THE WAY  1 logged line with goods still due (deducted from the order list)" in reorder, reorder


@test("release 2: ordered previews the lines and a token; only --commit with that token logs; cancel undoes it")
def _():
    lines = ["CB2=30", "CA1=10"]
    with data_root():
        prev = run_opts(World(), "ordered", "VFAN", line=lines, date_="2026-03-10")
        assert prev["ok"] and prev["text"].startswith("ORDER PLACED  VFAN, ordered on "), prev["text"]
        assert "Nothing is logged yet" in prev["text"] and not stock.kpi_log_file().exists()
        token = re.search(r"--commit ([a-p]{12})", prev["text"]).group(1)
        bad = run_opts(World(), "ordered", "VFAN", line=lines, date_="2026-03-10", commit="abcdefghijkl")
        assert not bad["ok"] and "changed since the preview: nothing was logged" in bad["text"], bad["text"]
        assert not stock.kpi_log_file().exists()
        done = run_opts(World(), "ordered", "VFAN", line=lines, date_="2026-03-10", commit=token)
        assert done["ok"] and "Logged: 2 lines in the KPI log" in done["text"], done["text"]
        assert {r["item_code"] for r in stock.read_promises()} == {"CB2", "CA1"}
        again = run_opts(World(), "ordered", "VFAN", line=lines, date_="2026-03-10")
        assert "Nothing to log: the KPI log already holds this order as it is." in again["text"], again["text"]
        m, _inp = model_of()
        assert item(m, "B").on_order == 30 and item(m, "A").on_order == 10
        # cancelled: the same preview and token; the lines no longer count
        prev = run_opts(World(), "cancel", "VFAN", date_="2026-03-10")
        assert prev["text"].startswith("ORDER CANCELLED  VFAN") and "cancelled" in prev["text"]
        token = re.search(r"--commit ([a-p]{12})", prev["text"]).group(1)
        assert "Logged: 2 lines" in run_opts(World(), "cancel", "VFAN", date_="2026-03-10", commit=token)["text"]
        m, _inp = model_of()
        assert item(m, "B").on_order == 0 and item(m, "A").on_order == 0
        # the words that refuse: no supplier, a line not typed CODE=QTY, a date that is no date
        for spec, kw, said in ((("ordered",), {"line": lines}, "Name the supplier"),
                               (("ordered", "VFAN"), {"line": ["CB2"]}, "typed CODE=QTY"),
                               (("ordered", "VFAN"), {"line": lines, "date_": "10-03-2026"}, "written YYYY-MM-DD")):
            out = run_opts(World(), *spec, **kw)
            assert not out["ok"] and said in out["text"], (spec, out["text"])
    src = (ROOT / "scripts" / "ops" / "stock.py").read_text(encoding="utf-8")
    assert "Nothing is ever sent to a supplier" in src or "never sent to a supplier" in src


@test("a code worktree never makes the owner's day file: without a named data root it says why, the screen still prints")
def _():
    with data_root():
        m, _inp = model_of()
        saved = (paths.is_worktree, paths._override)
        try:
            paths.is_worktree = lambda: True
            paths._override = None                      # as a run from a worktree finds the main folder by itself
            res = stock.day_file(m)
        finally:
            paths.is_worktree, paths._override = saved
        assert res["path"] is None and not res["made"] and "code worktree" in res["error"], res
        assert stock.file_lines(res)[0].startswith("FILE  not made this time: this is a code worktree")


# ── the cost seam, and the model as on an earlier day ────────────────────────

def facts_of(m) -> dict:
    """A model's facts as the day's JSON file holds them (stock.facts_out), through JSON and back."""
    return json.loads(json.dumps(stock.facts_out(m, "headline", None)))


# What a model as on another day than the snapshot's leaves out: the portal's figures and the ledger check.
PORTAL_KEYS = ("items", "units", "sell_bhd", "found", "other_warehouses", "uncosted_sell_bhd", "portal_cost_bhd",
               "portal_uncosted_items", "not_compared")


def beside_the_snapshot_out(facts: dict) -> dict:
    """The facts less the day's marker, the ledger check and the portal's figures of the headline."""
    out = {k: v for k, v in facts.items() if k not in ("as_of", "ledger_check")}
    out["headline"] = {k: v for k, v in facts["headline"].items() if k not in PORTAL_KEYS}
    return out


def cut_by_hand(inp, day: date):
    """The inputs as they stood at the end of `day`, cut by hand: the ledger rows, the price cuts and the KPI log's
    promise lines (by their order date, else the day they were logged) dated up to it."""
    iso = day.isoformat()
    return replace(inp, rows=[r for r in inp.rows if r["date"] <= day],
                   price_cuts=None if inp.price_cuts is None else [c for c in inp.price_cuts if c["changed_on"] <= iso],
                   promises=None if inp.promises is None else
                   [p for p in inp.promises if (p.get("event_date") or p["logged_at"][:10]) <= iso])


# The KPI log of these tests: a line ordered before 28 Feb, one after it (both before D), one with no order date.
LOG_LINES = [
    {"kind": "promise", "ref": "vfan:2026-02-10:CB2", "supplier": "VFAN", "item_code": "CB2", "qty": "30",
     "event_date": "2026-02-10", "logged_at": "2026-02-10T10:00:00", "state": "open", "source": "typed"},
    {"kind": "promise", "ref": "vfan:2026-03-05:CA1", "supplier": "VFAN", "item_code": "CA1", "qty": "40",
     "event_date": "2026-03-05", "logged_at": "2026-03-05T10:00:00", "state": "open", "source": "typed"},
    {"kind": "promise", "ref": "p-undated", "item_code": "HG3", "qty": "5", "logged_at": "2026-02-01T09:00:00"},
]
# Two price cuts: ITEM-A's before 28 Feb, ITEM-C's after it.
CUT_ROWS = [("CA1", NAMES["A"], "2026-01-20", "1.0", "0.8"), ("HG3", NAMES["C"], "2026-03-02", "2", "1.5")]


@test("the cost seam: cost_reads is gather's four reads; accessory_table and apply_costs give the model's items and costs")
def _():
    w = World()
    with data_root():
        with standing_in(w):
            costs = stock.cost_reads()
            inp = stock.gather()
        m = stock.build(inp, stock.settings())
        table, bridge, main_snap = stock.accessory_table(inp.rows, inp.price_book, inp.snapshot)
        bare = list(stock.accessory_table(inp.rows, inp.price_book, inp.snapshot)[0].values())
        in_main = [r for r in inp.snapshot if ledger.is_main(r["warehouse_name"])]
        known = bridge.get(L("A"))
    assert set(costs) == {"price_book", "snapshot", "mrn", "sheet", "unread"} and costs["unread"] == []
    assert (costs["price_book"], costs["snapshot"], costs["mrn"], costs["sheet"]) == (inp.price_book, inp.snapshot,
                                                                                     inp.mrn, inp.sheet)
    assert costs["mrn"]["CA1"]["cost"] == Decimal("0.4") and costs["sheet"]["DS1"]["cost"] == Decimal("10.0")
    assert w.pg == [stock.SHEET_SQL] * 2                     # the pricing sheet on the read-only session, both times
    # every accessory by its ledger name, with the model's code, name, sales and receipts; the rest is build()'s
    assert list(table) == [it.ledger_name for it in m.items] == sorted(table)
    assert main_snap == in_main and len(main_snap) == 5 and known == NAMES["A"]
    for it in m.items:
        t = table[it.ledger_name]
        assert (t.code, t.name, t.sales, t.last_sale, t.receipts) == (it.code, it.name, it.sales, it.last_sale,
                                                                      it.receipts), it.ledger_name
        assert not t.display and t.cost is None and t.series == [] and t.on_hand == 0
    assert item(m, "DISP").display                           # the display flag is set by build()
    stock.apply_costs(table, inp.mrn, inp.sheet)
    assert {n: (t.cost, t.cost_source, t.cost_ref) for n, t in table.items()} == {
        it.ledger_name: (it.cost, it.cost_source, it.cost_ref) for it in m.items}
    # the Items themselves will do; with nothing read, each takes its newest receipt's rate (ITEM-A 100 / 200)
    stock.apply_costs(bare, None, None)
    assert {it.code: (it.cost_source, it.cost) for it in bare} == {
        "CA1": ("receipt", Decimal("0.5")), "CB2": ("receipt", Decimal("2.5")), "HG3": ("receipt", Decimal("0.8")),
        "SN4": ("receipt", Decimal(3)), "FO5": ("receipt", Decimal("1.5")), "DS1": ("receipt", Decimal(10))}
    # a cost read that fails is None and named in gather()'s words; the snapshot is required
    with data_root():
        with standing_in(World(fail={stock.MRN_SQL: RuntimeError("down")})):
            costs = stock.cost_reads()
        with standing_in(World(fail={stock.SNAPSHOT_SQL: RuntimeError("down")})):
            raises(RuntimeError, stock.cost_reads)
        down = (metrics.STOCK_SQL, stock.PRICE_BOOK_SQL, stock.SUPPLIER_SQL, stock.MRN_SQL, stock.SHEET_SQL,
                stock.PRICE_CUT_SQL)
        with standing_in(World(fail={sql: RuntimeError("down") for sql in down})):
            inp = stock.gather()
    assert costs["mrn"] is None and costs["unread"] == ["the MRN landed costs (mrn_landed_costs) (RuntimeError)"]
    assert costs["price_book"] and costs["sheet"] and costs["snapshot"]
    # gather() names each failed read in its own place among the others, as before the cost reads moved
    assert inp.unread == [f"{words} (RuntimeError)" for words in (
        "the portal's figure at cost", "the price book (item codes)", "the supplier invoices (lead time)",
        "the MRN landed costs (mrn_landed_costs)", "the pricing sheet (purchase_costs)",
        "the price book's changes (price cuts)")], inp.unread
    assert (inp.price_book, inp.mrn, inp.sheet, inp.price_cuts) == (None, None, None, None) and inp.snapshot


@test("as on a day: as_of None is today's model; the ledger's last day, the snapshot's own, adds only its marker")
def _():
    with data_root():
        with standing_in(World()):
            cfg, inp = stock.settings(), stock.gather()
        today, none = stock.build(inp, cfg), stock.build(inp, cfg, as_of=None)
        last, typed = stock.build(inp, cfg, as_of=D), stock.build(inp, cfg, as_of=D.isoformat())
        screens = [(view(none, "n"), view(today, "n")) for view in (stock.headline_lines, stock.reorder_lines,
                                                                     stock.ageing_lines, stock.damage_view_lines)]
        head = stock.headline_lines(last, "n")
    assert facts_of(none) == facts_of(today) and "as_of" not in today.facts and all(a == b for a, b in screens)
    got = facts_of(last)
    marker = got.pop("as_of")
    assert got == facts_of(today), "cut at its own last day, the ledger is today's model"
    assert marker == {"date": D.isoformat(), "ledger_to": D.isoformat(), "snapshot_as_of": D.isoformat(),
                      "compared": True, "not_compared": None, "cost_words": "at today's landed cost"}, marker
    assert facts_of(typed) == facts_of(last)                 # a day as text is the same day
    assert head[0] == ("STOCK  main store, Main Store: as on Sun 15 Mar, the ledger cut at the end of that day (it runs "
                       "to Sun 15 Mar); stock balance as on Sun 15 Mar"), head[0]
    assert "  5 items, 505 units, BHD 815.000 at selling price (the portal's figures)" in head
    assert head[2].startswith("  At today's landed cost BHD 412.800 (MRN;"), head[2]
    assert "  The ledger agrees with the stock balance on all 5 items (Sun 15 Mar)." in head
    assert ("As on Sun 15 Mar: the ledger is cut at the end of that day (it runs to Sun 15 Mar), and every unit is "
            "valued at today's landed cost.") in head


@test("as on a day in the past: the model of the rows, price cuts and promise lines cut by hand; the portal set aside")
def _():
    feb = date(2026, 2, 28)
    with data_root():
        paths.write_text(stock.kpi_log_file(), "\n".join(json.dumps(x) for x in LOG_LINES) + "\n")
        with standing_in(World(cuts=CUT_ROWS)):
            cfg, inp = stock.settings(), stock.gather()
        today, past, hand = stock.build(inp, cfg), stock.build(inp, cfg, as_of=feb), stock.build(cut_by_hand(inp, feb), cfg)
        head, item_b = stock.headline_lines(past, "n"), stock.item_lines(past, "CB2", "n")
        ageing = stock.ageing_lines(past, "n")
    assert past.d == hand.d == feb and past.facts["data_date"] == feb
    assert beside_the_snapshot_out(facts_of(past)) == beside_the_snapshot_out(facts_of(hand))
    # 28 Feb by hand: A 1200 - 2 a day for 393 days = 414; B 100 - 30 - 35 = 35; ITEM-N arrives on 1 Mar
    assert [item(past, k).on_hand for k in ("A", "B", "C", "DISP")] == [414, 35, 50, 5]
    assert L("N") not in {it.ledger_name for it in past.items} and L("N") in {it.ledger_name for it in today.items}
    # the promise line ordered on 5 Mar is not yet on order; the undated one is counted by the day it was logged
    oo, oo_now = past.facts["on_order"], today.facts["on_order"]
    assert (oo["logged"], oo["counted"], oo["unmatched"]) == (2, 1, 1) and (oo_now["logged"], oo_now["counted"]) == (3, 2)
    assert (item(past, "B").on_order, item(past, "A").on_order, item(today, "A").on_order) == (30, 0, 40)
    # ITEM-C's cut of 2 Mar is not yet made: neither used nor waiting
    cut_of = {r["item"].code: r["cut"] for r in past.facts["review"]["rows"]}
    cut_now = {r["item"].code: r["cut"] for r in today.facts["review"]["rows"]}
    assert cut_of["CA1"]["date"] == date(2026, 1, 20) and cut_of["HG3"] is None and cut_now["HG3"]["date"] == date(2026, 3, 2)
    assert past.facts["review"]["cuts_later"] == 0
    # the snapshot is as on 15 Mar: the portal's figures and the ledger check are not set beside 28 Feb
    words = "not compared: the snapshot is not as on Sat 28 Feb"
    assert past.facts["as_of"] == {"date": feb, "ledger_to": D, "snapshot_as_of": D, "compared": False,
                                   "not_compared": words, "cost_words": "at today's landed cost"}
    h, chk = past.facts["headline"], past.facts["ledger_check"]
    assert [h[k] for k in PORTAL_KEYS] == [None, None, None, False, None, None, None, None, words], h
    assert (chk["agree"], chk["differ"], chk["compared_on"], chk["unknown_snapshot_items"], chk["not_compared"]) == (
        None, [], None, [], words)
    # 414 x 0.4 + 35 x 2.5 + 50 x 0.8 + 5 x 10, at today's costs
    assert h["cost_bhd"] == hand.facts["headline"]["cost_bhd"] == Decimal("343.100")
    assert head[0] == ("STOCK  main store, Main Store: as on Sat 28 Feb, the ledger cut at the end of that day (it runs "
                       "to Sun 15 Mar); stock balance as on Sun 15 Mar"), head[0]
    assert head[1] == f"  The portal's figures and the ledger check: {words}.", head[1]
    assert head[2].startswith("  At today's landed cost BHD 343.100 (MRN;"), head[2]
    for said in ("The ledger agrees", "The ledger differs", "The portal at cost", "The portal's figure at cost",
                 "at selling price", "match no accessory"):
        assert not any(said in ln for ln in head + item_b), said
    assert item_b[1] == ("  Main store, end of Sat 28 Feb: 35 units; at cost BHD 87.500 (the latest receipt rate 2.500 a "
                         "unit: neither an MRN landed cost nor a pricing-sheet cost for CB2)"), item_b[1]
    note = ("As on Sat 28 Feb: the ledger is cut at the end of that day (it runs to Sun 15 Mar), and every unit is "
            "valued at today's landed cost.")
    assert note in head and note in item_b and note in ageing
    for line in head + item_b + ageing:
        fmt.careful(line)
    # with a stock balance as on 28 Feb (made by hand from the ledger), everything is set beside it, as on that day
    snap = [dict(r, as_of_date=feb.isoformat()) for r in snapshot_rows({"A": 414, "B": 35, "N": 0})]
    with data_root():
        paths.write_text(stock.kpi_log_file(), "\n".join(json.dumps(x) for x in LOG_LINES) + "\n")
        with standing_in(World(cuts=CUT_ROWS, snapshot=snap)):
            cfg, inp = stock.settings(), stock.gather()
        past, hand = stock.build(inp, cfg, as_of=feb), stock.build(cut_by_hand(inp, feb), cfg)
        head = stock.headline_lines(past, "n")
    got = facts_of(past)
    assert got.pop("as_of")["compared"] is True and got == facts_of(hand)
    chk = past.facts["ledger_check"]
    assert (chk["agree"], chk["differ"], chk["compared_on"]) == (4, [], feb) and "not_compared" not in chk
    assert "  4 items, 504 units, BHD 694.000 at selling price (the portal's figures)" in head, head[:3]   # 414+175+100+5
    assert "  The ledger agrees with the stock balance on all 4 items (Sat 28 Feb)." in head


@test("as on a day nothing moved: D is that day; a unit keeps today's cost; a day outside the ledger is one sentence")
def _():
    fri = date(2026, 2, 20)                                  # a Friday: in this world nothing moves on it
    b = world()
    b.rows = [r for r in b.rows if r["date"] != fri]
    with data_root():
        with standing_in(World(book=b)):
            cfg, inp = stock.settings(), stock.gather()
        m, typed = stock.build(inp, cfg, as_of=fri), stock.build(inp, cfg, as_of=fri.isoformat())
    assert ledger.last_date([r for r in inp.rows if r["date"] <= fri]) == fri - timedelta(days=1)
    assert m.d == fri and m.facts["data_date"] == fri and facts_of(typed) == facts_of(m)
    a = item(m, "A")
    # 1200 - 2 a day from 1 Feb 2025 to 19 Feb 2026 (384 days) = 432, on the Thursday and on the Friday
    assert len(a.series) == (fri - m.series_start).days + 1 and a.on_hand == a.series[-2] == 432
    r = m.facts["fast"]["rates"][L("A")]
    assert (r.sold, r.days_in_stock) == (178, 90)            # the 90 days to the Friday, which sold nothing
    # costs stay today's: before ITEM-B's receipt of 30 Jan (2.50 a unit) the ledger knows only June's (60 / 30)
    jan = date(2026, 1, 20)
    with data_root():
        with standing_in(World()):
            cfg, inp = stock.settings(), stock.gather()
        past, hand = stock.build(inp, cfg, as_of=jan), stock.build(cut_by_hand(inp, jan), cfg)
        for day, said in ((D + timedelta(days=1), "The stock ledger runs to 15 March 2026: it cannot show the stock as "
                                                  "on 16 March 2026."),
                          (date(2025, 1, 31), "The stock ledger starts on 1 February 2025: it cannot show the stock as "
                                              "on 31 January 2025."),
                          ("31-01-2026", "The date is written YYYY-MM-DD: '31-01-2026' is not one.")):
            exc = raises(stock.StockInput, lambda day=day: stock.build(inp, cfg, as_of=day))
            assert str(exc) == said, str(exc)
        exc = raises(stock.StockInput, lambda: stock.build(replace(inp, rows=[]), cfg, as_of=D))
        assert "No stock ledger is loaded" in str(exc)
    assert (item(past, "B").cost_source, item(past, "B").cost) == ("receipt", Decimal("2.5"))
    assert item(hand, "B").cost == Decimal(2) and item(hand, "B").cost_source == "receipt"
    assert item(past, "A").cost == item(hand, "A").cost == Decimal("0.4")      # an MRN cost is today's either way
    assert past.facts["as_of"]["cost_words"] == "at today's landed cost"


# ── the order position: the vans, the outlets and the stock between them ─────

DEFAULT_POSITION = ("stock.position_stores", "stock.van_floor_days")   # dropped: the plan's defaults (all, no floor)
OUTLET = "Causeway"                                                    # an outlet, as pairing.StoreRules.kind knows it


def late_receipt(b: Book, item_name: str, day: date, units, frm: str, to: str, booked_on: date) -> Book:
    """A transfer whose receive leg is booked on a later day than its issue."""
    b.n += 1
    b._row(item_name, day, ledger.ISSUE, frm, f"SIO:T-{b.n}", qout=units, to_wh=to)
    b._row(item_name, booked_on, ledger.RECEIVE, to, f"STRV:T-{b.n}", qin=units)
    return b


def moving() -> World:
    """The world with stock beside the main store in every part (and the snapshot as the main store then stands):
      ITEM-C  5 to the outlet the day before D (received); 8 to the first van on D, not received yet (in grace: due
              Mon 16 Mar); 4 to the second van on Tue 10 Mar, never received (past its deadline: not counted)
      ITEM-N  two identical issues of 2 to the second van on D and one receipt of 2: one slot of the twins in grace
      ITEM-A  3 to the damage store and 2 to the samples on D, neither received: never counted
    Beside: ITEM-B 35 in the second van; ITEM-A's first van stands at -10 (a return it never held), read as 0."""
    b = world()
    b.move(L("C"), D - timedelta(days=1), 5, MAIN, OUTLET)
    b.move(L("C"), D, 8, MAIN, VAN, received=False)
    b.move(L("C"), D - timedelta(days=5), 4, MAIN, VAN_B, received=False)
    b.move(L("N"), D, 2, MAIN, VAN_B)
    b.move(L("N"), D, 2, MAIN, VAN_B, received=False)
    b.to_damage(L("A"), D, 3, booked=False)
    b.move(L("A"), D, 2, MAIN, SAMPLES, received=False)
    return World(book=b, snapshot=snapshot_rows({"A": 389, "C": 33, "N": 32}))


def zeros(**units) -> dict:
    """A position row of facts["position"]["items"]: the parts given, the rest nought."""
    row = {"vans": Decimal(0), "outlets": Decimal(0), "transit": Decimal(0), "counted": Decimal(0), "clamped": 0}
    row.update({k: (Decimal(v) if k != "clamped" else v) for k, v in units.items()})
    return row


@test("the position (the plan's default, all three): the vans, the outlets and in-grace transfers count; a van below "
      "nought reads nought; a transfer past its deadline, damage and samples never")
def _():
    with data_root(drop=DEFAULT_POSITION):
        m, _inp = model_of(moving())
        cfg = stock.settings()
    assert cfg.position_stores == ("vans", "outlets", "transit") and cfg.floor_days == 0
    assert {"stock.position_stores", "stock.van_floor_days"} <= set(cfg.defaults_used)
    vo = m.facts["position"]
    assert (vo["stores"], vo["floor_days"], vo["error"]) == (["vans", "outlets", "transit"], 0, None)
    assert vo["items"] == {
        L("A"): zeros(clamped=1),                                  # the first van at -10: read as 0, nothing counted
        L("B"): zeros(vans=35, counted=35),                        # the second van's 35
        L("C"): zeros(outlets=5, transit=8, counted=13),           # 5 in the outlet, 8 still in grace (4 past it: out)
        L("N"): zeros(vans=2, transit=2, counted=4)}, vo["items"]  # 2 received, the twin's missing slot in grace
    # 35 x 2.5 + 13 x 0.8 + 4 x 3.2; the second van and the outlet hold some (the first van holds nothing)
    assert vo["totals"] == {"vans": 37, "outlets": 5, "transit": 10, "counted": 52, "vans_n": 1, "outlets_n": 1,
                            "stores_n": 2, "cost_bhd": Decimal("110.700"), "uncosted": 0}, vo["totals"]
    # the order position: ITEM-B 20 + 35 + nothing on order = 55 days at 1.00 a day, filled to 85: 30 (was 65)
    (row,) = m.facts["order"]
    assert (row["item"].code, row["beside"], row["position"], row["cover"], row["qty"]) == ("CB2", 35, 55, 55, 30), row
    assert m.facts["plan"]["order"][0]["beside"] == 35
    # the rate, the days in stock, KPI 1 cover and the ageing stay the main store's
    r = m.facts["fast"]["rates"][L("B")]
    assert (r.sold, r.days_in_stock, r.rate) == (45, 45, 1) and m.facts["ageing"]["main_units"] == 389 + 20 + 33 + 32 + 5
    assert stock.vans_beside(m) == m.facts["headline"]["vans"] == [
        {"kind": "vans", "stores": 1, "units": 37, "cost_bhd": Decimal("93.900"), "uncosted": 0, "counted": True},
        {"kind": "outlets", "stores": 1, "units": 5, "cost_bhd": Decimal("4.000"), "uncosted": 0, "counted": True},
        {"kind": "between stores", "stores": None, "units": 10, "cost_bhd": Decimal("12.800"), "uncosted": 0,
         "counted": True}]
    # the main store only ([]): the same units are shown, nothing is counted, ITEM-B orders its 65 again
    with data_root():
        m0, _inp = model_of(moving())
    assert m0.facts["position"]["stores"] == [] and m0.facts["position"]["totals"]["counted"] == 0
    assert m0.facts["position"]["items"][L("B")] == zeros(vans=35)
    assert [(x["item"].code, x["beside"], x["qty"]) for x in m0.facts["order"]] == [("CB2", 0, 65)]
    assert [k["counted"] for k in stock.vans_beside(m0)] == [False] * 3


@test("the position on the screens: the headline's line and a Vans column with its note, the reorder view, one item; "
      "never a van's name, never 'on the way'")
def _():
    with data_root(drop=DEFAULT_POSITION) as root:
        head = run(moving())["text"]
        reorder = run(moving(), "reorder")["text"]
        items = {code: run(moving(), code)["text"] for code in ("CB2", "HG3", "CA1", "SN4", "FO5", "DS1")}
        facts = json.loads((root / "exports" / "ops" / D.isoformat() / "stock.json").read_text(encoding="utf-8"))
    assert ("  Vans and outlets: 37 units in 1 van (BHD 93.900), 5 units in 1 outlet (BHD 4.000), 10 units between "
            "stores (BHD 12.800); the order list counts them.") in head, head
    lines = head.splitlines()
    at = lines.index(next(ln for ln in lines if ln.split()[:2] == ["Item", "Code"]))
    note = ("  Vans = the item's units in the vans and the outlets and between stores (each store never below "
            "nought), counted with its main-store stock in Cover and Order")
    assert lines[at].split() == ["Item", "Code", "On", "hand", "Vans", "A", "day", "Cover", "Order", "At", "cost"]
    assert lines[at + 1].split() == ["ITEM-B", "Charger", "Beta", "CB2", "20", "35", "1.00", "55", "30", "75.000"]
    assert lines[at + 2] == note and note in reorder
    header = next(ln for ln in reorder.splitlines() if "Supplier" in ln and "Vans" in ln)
    assert header.index("On hand") < header.index("Vans") < header.index("On order")
    assert items["CB2"].splitlines()[2] == "  Vans and outlets: 35 in the vans; 35 counted in its order position."
    assert "  Vans and outlets: 5 in the outlets, 8 between stores; 13 counted in its order position." in items["HG3"]
    assert ("  Vans and outlets: none of it beside the main store; 1 van or outlet below nought in the ledger, read as "
            "nought, to check.") in items["CA1"]
    assert "  Vans and outlets: 2 in the vans, 2 between stores; 4 counted in its order position." in items["SN4"]
    assert "  Vans and outlets: none of it beside the main store." in items["FO5"]
    assert "Vans and outlets" not in items["DS1"]                       # a display item is never in the position
    # the facts beside the screen: the position, each order row's units beside the main store, no store's name
    assert facts["position"]["stores"] == ["vans", "outlets", "transit"] and facts["order"][0]["beside"] == "35"
    assert facts["position"]["totals"]["counted"] == "52" and facts["plan"]["order"][0]["beside"] == "35"
    shown = (json.dumps(facts["position"]) + json.dumps(facts["headline"]["vans"]) + head + reorder
             + "".join(items.values()))
    for name in (VAN, VAN_B, OUTLET):                 # a van is named after its salesman: never a store's name
        assert name.casefold() not in shown.casefold(), name
    for text in [head, reorder] + list(items.values()):
        assert "on the way" not in "\n".join(ln for ln in text.splitlines() if "Vans" in ln or "between" in ln)
        for line in text.splitlines():
            fmt.careful(line)
    # some parts only: the words say which; the order counts those only
    with data_root({"stock.position_stores": ["vans"]}):
        m, _inp = model_of(moving())
        text = stock.headline_lines(m, "n")
    assert m.facts["position"]["stores"] == ["vans"] and m.facts["position"]["totals"]["counted"] == 37
    assert m.facts["position"]["totals"]["stores_n"] == 1 and m.facts["order"][0]["beside"] == 35
    assert any(ln.endswith("; the order list counts the vans only.") for ln in text), text
    assert "  Vans = the item's units in the vans (each store never below nought), counted with its main-store stock " \
           "in Cover and Order" in text
    with data_root({"stock.position_stores": ["transit", "outlets"]}):
        m, _inp = model_of(moving())
        text = stock.headline_lines(m, "n")
    assert m.facts["position"]["stores"] == ["outlets", "transit"] and m.facts["position"]["totals"]["stores_n"] == 1
    assert [x["beside"] for x in m.facts["order"]] == [0] and m.facts["order"][0]["qty"] == 65
    assert any(ln.endswith("; the order list counts the outlets and the stock between stores only.") for ln in text)


@test("a van's working minimum: ceil(days x its own sales over 90 days / 90) is kept back, the vans only; below it the "
      "van reads nought")
def _():
    b = world()
    b.move(L("B"), D, 10, MAIN, VAN)                  # the first van holds 10 of ITEM-B and sold 45 in the 90 days
    b.move(L("C"), D, 5, MAIN, OUTLET).sale(L("C"), D, 1, wh=OUTLET)      # an outlet that sells: no floor there
    w = World(book=b, snapshot=snapshot_rows({"B": 10, "C": 45}))
    got = {}
    for days in (0, 2, 30):
        with data_root({"stock.position_stores": ["vans", "outlets", "transit"], "stock.van_floor_days": days}):
            m, _inp = model_of(w)
            got[days] = (m.facts["position"]["items"][L("B")], m.facts["position"]["items"][L("C")],
                         stock.headline_lines(m, "n"))
    # 2 days: ceil(2 x 45 / 90) = 1 kept back of the first van's 10; the second van's 35 sold nothing: none kept
    assert got[0][0] == zeros(vans=45, counted=45) and got[2][0] == zeros(vans=44, counted=44)
    # 30 days: ceil(30 x 45 / 90) = 15 is more than its 10: the van reads 0 (and the first van's ITEM-A stays at 0)
    assert got[30][0] == zeros(vans=35, counted=35, clamped=1)
    assert got[0][1] == got[30][1] == zeros(outlets=4, counted=4)                       # the outlet keeps nothing
    assert any("; each van keeps 2 days of its own sales back (config 'stock.van_floor_days')." in ln
               for ln in got[2][2])
    assert not any("each van keeps" in ln for ln in got[0][2])


@test("the position as on an earlier day (build's as_of): the vans, the outlets and the transfers as they stood then")
def _():
    b = world()
    thu, sat = date(2026, 3, 12), date(2026, 3, 14)     # due on the Saturday after a Thursday's issue
    late_receipt(b, L("C"), thu, 6, MAIN, VAN, booked_on=sat)
    with data_root(drop=DEFAULT_POSITION):
        with standing_in(World(book=b, snapshot=snapshot_rows({"C": 44}))):
            cfg, inp = stock.settings(), stock.gather()
        at = {day: stock.build(inp, cfg, as_of=day).facts["position"]["items"]
              for day in (date(2026, 1, 31), date(2026, 2, 1), thu, sat)}
        now = stock.build(inp, cfg).facts["position"]["items"]
    assert L("B") not in at[date(2026, 1, 31)] and at[date(2026, 2, 1)][L("B")] == zeros(vans=35, counted=35)
    assert at[thu][L("C")] == zeros(transit=6, counted=6)               # issued, not yet booked in: in grace
    assert at[sat][L("C")] == now[L("C")] == zeros(vans=6, counted=6)  # booked in by the van, which has not sold it


@test("the position that cannot be worked out counts the main store only and says so; the tests' mode raises")
def _():
    from scripts.ops import position as pos
    saved = (pos.position_beside_main, stock.RAISE_PARTS)

    def down(*_a, **_kw):
        raise RuntimeError("the pairing stopped")

    try:
        pos.position_beside_main = down
        with data_root(drop=DEFAULT_POSITION):
            raises(RuntimeError, lambda: model_of(moving()))          # RAISE_PARTS: a part that fails fails the test
            stock.RAISE_PARTS = False
            m, _inp = model_of(moving())
            head, reorder, one = (stock.headline_lines(m, "n"), stock.reorder_lines(m, "n"),
                                  stock.item_lines(m, "CB2", "n"))
            out = run(moving())
    finally:
        pos.position_beside_main, stock.RAISE_PARTS = saved
    vo = m.facts["position"]
    assert vo["error"] == "RuntimeError" and vo["items"] == {} and vo["totals"]["counted"] == 0
    assert vo["stores"] == ["vans", "outlets", "transit"]
    assert [(x["item"].code, x["beside"], x["qty"]) for x in m.facts["order"]] == [("CB2", 0, 65)]   # the main store's
    said = "  Vans and outlets could not be counted this time: the order list counts the main store only."
    assert said in head and said in reorder and said in one and stock.vans_beside(m) == []
    headers = [ln.split() for ln in head + reorder if ln.split()[:2] in (["Item", "Code"], ["#", "Item"])]
    assert len(headers) == 2 and not any("Vans" in h for h in headers)          # no Vans column
    assert any(w.startswith("the stock beside the main store could not be worked out: RuntimeError") for w in m.warnings)
    assert out["ok"] and said.strip() in out["text"]
    assert any("could not be worked out" in w for w in out["warnings"])


# ── the real files (read-only; SKIP when they are not on this machine) ────────

@test("real files (SKIP without a loaded folder): ledger = stock balance on every item, the ageing adds up, the list holds")
def _():
    base = REAL_ROOT / "business_data"
    folders = sorted((p for p in base.glob("Focus ERP 20*") if p.is_dir()), key=lambda p: p.name) if base.is_dir() else []
    book = ledger.find_file(folders[-1]) if folders else None
    balance = next((p for p in folders[-1].iterdir() if p.is_file() and not p.name.startswith("~$")
                    and "stock_balance" in p.name.lower()), None) if folders else None
    cfg_file = base / "ops" / "config.json"
    if book is None or balance is None or not cfg_file.is_file():
        print("  SKIP: no loaded Focus folder with a stock ledger and a stock balance on this machine")
        return
    real_cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    plan_defaults = tuple(k for k in config.OPTIONAL if k not in real_cfg)     # as on the owner's PC
    from scripts import ingest                              # pandas, only here
    with data_root(real_cfg, drop=plan_defaults):           # the cache goes to a temporary folder
        t0 = time.perf_counter()
        led = ledger.load(book)
        snap = [{"item_name": r["item_name"], "warehouse_name": r["warehouse_name"], "net_qty": str(r["net_qty"] or 0),
                 "value_bhd": str(r["total_value_bhd"] or 0), "as_of_date": str(r["as_of_date"] or "")}
                for r in ingest.parse_stock_balance(ingest.read_grid(balance), balance.name)]
        inp = stock.Inputs(rows=led.rows, ledger_note="(real files)", snapshot=snap, portal_warehouses=portal_rows(snap),
                           mrn={}, sheet={}, price_book=None, invoices=None, promises=None)
        m = stock.build(inp, stock.settings())
        t1 = time.perf_counter()
        f = m.facts
        chk = f["ledger_check"]
        main_units = sum(Decimal(r["net_qty"]) for r in snap if ledger.is_main(r["warehouse_name"]))
        assert not chk["differ"] and not chk["unknown_snapshot_items"], (len(chk["differ"]), chk["unknown_snapshot_items"][:3])
        assert f["headline"]["units"] == main_units == f["ageing"]["main_units"]
        assert f["ageing"]["units"] + f["ageing"]["display_units"] == main_units, "the ageing does not add up"
        assert sum(b["units"] for b in f["bands"]) + f["ageing"]["display_units"] == main_units
        from scripts.ops import stock_season
        lifts = f["season"]["lifts"]
        assert all(x["qty"] > 0 and stock_season.joins(x["position"], x["rate6"], m.d, x["trig_days"], lifts)
                   for x in f["order"])
        text = "\n".join(stock.headline_lines(m, inp.ledger_note) + stock.reorder_lines(m, inp.ledger_note)
                         + stock.ageing_lines(m, inp.ledger_note))
        assert not NINE.search(text) and not LONG.search(text)
        # as on the last day of last month (a month-end sheet): its ageing adds up to the ledger's own balance there
        end = stock.month_start(m.d) - timedelta(days=1)
        t2 = time.perf_counter()
        past = stock.build(inp, stock.settings(), as_of=end)
        t3 = time.perf_counter()
        names = {it.ledger_name for it in past.items}
        held = sum((max(v, Decimal(0)) for (wh, name), v in ledger.balances(led.rows, as_of=end).items()
                    if name in names and ledger.is_main(wh)), Decimal(0))
        ag = past.facts["ageing"]
        assert past.d == end and ag["units"] + ag["display_units"] == ag["main_units"] == held, (ag["main_units"], held)
        assert names <= {it.ledger_name for it in m.items} and past.facts["as_of"]["compared"] == (m.stock_as_of == end)
        print(f"    {len(m.items)} accessory items, {chk['agree']} agree with the stock balance, "
              f"{len(f['order'])} on the order list, ageing adds up; {t1 - t0:.1f}s; as on {end}: "
              f"{len(past.items)} items, ageing adds up; {t3 - t2:.1f}s")


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
    paths.set_data_root(None)
    config.use(None)
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
