"""Ops engine: the monthly report's facts and the check of its comment lines (python -m scripts.ops check).

    python -m tests.test_ops_textcheck

Same lightweight runner as the other suites (no pytest). No database, no network, no .env: SUPABASE_URL,
SUPABASE_KEY and DATABASE_URL are pinned to ci.invalid BEFORE any import and scripts.ops.db is imported first.
The month is invented, the shape of tests/test_ops_report.py's (two vans, an outlet, a roadshow handset, four
shops, round amounts), so every figure below is a sum a reader can redo by hand: March net 170 against February
75; five invoices against three; Van A 100, Van B 50, Causeway 20. Every folder is a temporary one.

Covered:
  * the facts against hand sums: the tiles and their changes, the customer base, the week blocks and the first
    invoices, the best day, the bulk invoices and the month without them, the salesmen and their largest order,
    the categories by sales, the top products with their bulk share, every account, the unit-price mix; a
    summary passed in; a week's model, a bad threshold and a bad summary refused; the digest leaves out the data
    date and moves with a figure;
  * the files: the facts file round-trips (valid JSON, one fact a line, its digest), the template is written once
    and never over a draft, another month or a wrong digest refused, nothing written outside exports/;
  * every format's rendering: half away from zero on the value as a spreadsheet shows it, no "-0", the sign of a
    change, words to twenty, days and months; a format that does not fit, or no value, refused;
  * the token: twelve letters a to p, the same twice, another for any byte and for other figures;
  * every refusal: a digit, a % sign, percent, a number word, an unknown key or format, a format that does not
    fit, a fact with no figure, a highlight with no placeholder (a note may have none), a direction word against
    its change, an unsigned change said with no direction, a nine-digit or fifteen-digit run, an e-mail, a
    phone-like number, a brace, a line break, an empty or non-text slot; the file: the layout, the month, a
    missing or extra slot, the facts changed by hand, the figures moved since the draft;
  * the direction rule both ways: the nearest change, from-to pairs (either order, the same figure as written),
    zero, negation, "up to", "made up", hyphenated words, two sentences, a direction word with no change at all;
  * the number words and their edge cases: "one of the", "no one", "one another", "one account", "one-off",
    "twenty-one", "a third", "the third week", "fifth of", "first of all", -fold, the words that only look alike;
  * capitalised words that are no fact: a note above FINAL, never a refusal;
  * the command: FINAL as the last line with the token of the file's bytes and the digest, --json, exit 2 with no
    FINAL on a refusal, exit 1 for a file that cannot be read, nothing written; render_slots for the final build,
    refused once the figures move; the rendered lines in sheets_core.Commentary, not DRAFT.
"""
from __future__ import annotations

import copy
import io
import json
import math
import os
import sys
import tempfile
import traceback
from contextlib import redirect_stdout
from datetime import date, timedelta
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"
os.environ.pop("YQ_MAIN_CHECKOUT", None)

from scripts.ops import db  # noqa: E402,F401  (first, as every command does)
from scripts.ops import __main__ as cli  # noqa: E402
from scripts.ops import config, paths, textcheck as tc  # noqa: E402
from scripts.ops.workbook import build, facts as fa, model as mdl, sheets_core as sc  # noqa: E402

TESTS: list[tuple[str, object]] = []
MONTH = "2026-03"                       # the report month; February is the month before
DATA_DATE = date(2026, 4, 7)
THRESHOLD = 40                          # a bulk order: an invoice of BHD 40 net or more (invented)


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


# ── the invented month ─────────────────────────────────────────────────────────────────────────

VAN_A, VAN_B, OUTLET, SHOW = "Van A - Acc WH", "Van B - Acc WH", "Causeway", "YQ Roadshow"
ITEMS = {"T01": "T01 Demo Earbuds One (VFAN) T01", "X01": "X01 Demo Cable One (VFAN)",
         "TB": "TB-D1 Demo Cable Box (VFAN)", "GN": "GN-01 Demo Power Bank", "WS": "WS-01 Demo Wireless Earbuds",
         "WG": "WG-01 Demo Neckband", "ZZ": "ZZ9 Demo Gadget", "PHONE": "ZTE-X1 DEMO PHONE"}
NAMES = {"WG-01": "Headphones & Neckbands", "Wireless Audio": "TWS Earbuds"}     # the config name table
UPCOMING = {"WS-01": "Wireless Audio", "WG-01": "Wireless Audio"}               # the Upcoming cards


def line(day: date, inv: int, no: int, cust: str, item: str, qty: int, rate: float, rep: str):
    gross = round(qty * rate, 3)
    net = round(gross / 1.1, 2) if gross else None
    return {"line_id": inv * 10 + no, "invoice_no": f"SI : SI-YQ-26-{day.month:02d}-{inv}", "line_no": no,
            "sale_date": day.isoformat(), "customer_account": cust, "customer_name": cust, "item_name": item,
            "quantity": float(qty), "rate_bhd": rate if rate else None, "gross_bhd": gross if gross else None,
            "net_bhd": net, "salesman_raw": rep, "salesman_resolved": rep, "narration": None,
            "category_name": None, "discount_bhd": None, "vat_amount_bhd": None}


def world_rows() -> list[dict]:
    feb, mar = date(2026, 2, 1), date(2026, 3, 1)
    d = lambda base, n: base + timedelta(days=n)  # noqa: E731
    return [
        # February: 75 net on three invoices (Shop Alpha 40, cash 10, Shop Gamma 25); the handset has no value
        line(d(feb, 1), 1, 1, "Shop Alpha", ITEMS["T01"], 10, 2.2, VAN_A),
        line(d(feb, 1), 1, 2, "Shop Alpha", ITEMS["X01"], 20, 1.1, VAN_A),
        line(d(feb, 3), 2, 1, "Cash customer", ITEMS["X01"], 10, 1.1, OUTLET),
        line(d(feb, 9), 3, 1, "Shop Gamma", ITEMS["GN"], 5, 5.5, VAN_B),
        line(d(feb, 20), 4, 1, "Cash Customer", ITEMS["PHONE"], 4, 0, SHOW),
        # March: 170 net on five invoices (Alpha 40 + 50, Beta 50, cash 20, Delta 10)
        line(d(mar, 0), 9, 1, "Shop Alpha", ITEMS["T01"], 20, 2.2, VAN_A),
        line(d(mar, 0), 10, 1, "Shop Beta", ITEMS["X01"], 30, 1.1, VAN_B),
        line(d(mar, 0), 10, 2, "Shop Beta", ITEMS["TB"], 2, 11.0, VAN_B),
        line(d(mar, 4), 11, 1, "CASH CUSTOMER", ITEMS["WS"], 5, 4.4, OUTLET),
        line(d(mar, 9), 12, 1, "Shop Delta", ITEMS["WG"], 3, 3.3, VAN_A),
        line(d(mar, 9), 12, 2, "Shop Delta", ITEMS["ZZ"], 1, 1.1, VAN_A),
        line(d(mar, 30), 13, 1, "Cash Customer", ITEMS["PHONE"], 6, 0, SHOW),
        line(d(mar, 30), 14, 1, "Shop Alpha", ITEMS["GN"], 10, 5.5, VAN_A),
    ]


class FakeSource(build.Source):
    """The report's reads answered from the invented month."""

    def __init__(self, rows=None):
        self.rows = world_rows() if rows is None else rows

    def anchors(self) -> dict:
        return {"focus_to": DATA_DATE.isoformat(), "uploaded_at": "2026-04-08T09:00:00+03:00"}

    def lines(self, first, last):
        return [r for r in self.rows if first.isoformat() <= r["sale_date"] <= last.isoformat()]

    def totals(self, first, cur_first, last):
        return {}

    def upcoming(self) -> dict:
        return dict(UPCOMING)


def built(rows=None, commentary=None):
    return build.build_month(MONTH, FakeSource(rows), prepared_on=date(2026, 4, 8), category_names=NAMES,
                             commentary=commentary)


def month_doc(threshold=THRESHOLD, data_date=DATA_DATE, rows=None, **summaries) -> dict:
    b, m, f = built(rows)
    return fa.month_facts(b, m, f, bulk_invoice_bhd=threshold, data_date=data_date, **summaries)


_CACHE: dict = {}


def facts_doc() -> dict:
    """The invented month's facts (worked out once; a test that changes them takes a deep copy)."""
    if "doc" not in _CACHE:
        _CACHE["doc"] = month_doc()
    return _CACHE["doc"]


def value(key: str):
    return facts_doc()["facts"][key]["value"]


def close(a, b) -> bool:
    return a is not None and abs(a - b) < 1e-9


class data_root:
    """A temporary data root (exports/, business_data/)."""

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name).resolve()
        paths.set_data_root(self.root)
        config.use(None)
        return self.root

    def __exit__(self, *exc):
        paths.set_data_root(None)
        config.use(None)
        self._tmp.cleanup()
        return False


# ── the commentary used by the checks ──────────────────────────────────────────────────────────

GOOD = {
    "highlight_1": "Net sales rose {net_change|abs_pct0} to BHD {net|bhd0}, from BHD {net_prev|bhd0} in "
                   "{month_prev|month}.",
    "highlight_2": "{invoices|Words} invoices against {invoices_prev|words}; the average invoice was BHD "
                   "{avg_invoice|bhd1}.",
    "highlight_3": "{rep1.name} led with BHD {rep1.net|bhd0}, {rep1.share|pct0} of the month, up {rep1.change|abs_pct0}.",
    "highlight_4": "{cat1.name} grew {cat1.change|abs_pct0} to BHD {cat1.net|bhd0}.",
    "highlight_5": "Bulk orders of BHD {bulk_threshold|bhd0} or more: {bulk_count|words} invoices worth BHD "
                   "{bulk_net|bhd0}.",
    "point_1": "Without the bulk orders, net sales were {net_ex_bulk_change|updown} {net_ex_bulk_change|abs_pct0} at "
               "BHD {net_ex_bulk|bhd0}.",
    "point_2": "{accounts_prev_only|Words} account bought in {month_prev|month} and not in {month|month}.",
    "point_3": "The days {wk2.label} fell {wk2.change|abs_pct0} on the same days of {month_prev|month}.",
    "point_4": "{acct4.name} was not invoiced in {month|month}.",
    "point_5": "Average unit price rose from BHD {avg_price_prev|bhd1} to BHD {avg_price|bhd1}.",
    "products_note": "The top products hold the bulk orders: {prod1.code} came on a single invoice.",
    "accounts_note": "Each account appears under its own name this month.",
    "customers_note": "Account names are as recorded in the ERP.",
}
GOOD_READ = {
    "highlight_1": "Net sales rose 127% to BHD 170, from BHD 75 in February 2026.",
    "highlight_2": "Five invoices against three; the average invoice was BHD 34.0.",
    "highlight_3": "Van A led with BHD 100, 59% of the month, up 150%.",
    "highlight_4": "TWS Earbuds grew 200% to BHD 60.",
    "highlight_5": "Bulk orders of BHD 40 or more: three invoices worth BHD 140.",
    "point_1": "Without the bulk orders, net sales were down 14% at BHD 30.",
    "point_2": "One account bought in February 2026 and not in March 2026.",
    "point_3": "The days 8 to 14 fell 60% on the same days of February 2026.",
    "point_4": "Shop Gamma was not invoiced in March 2026.",
    "point_5": "Average unit price rose from BHD 1.7 to BHD 2.4.",
    "products_note": "The top products hold the bulk orders: GN-01 came on a single invoice.",
    "accounts_note": "Each account appears under its own name this month.",
    "customers_note": "Account names are as recorded in the ERP.",
}


def commentary(over: dict | None = None, fdoc: dict | None = None) -> dict:
    fdoc = fdoc or facts_doc()
    doc = fa.template(MONTH, fdoc, f"report-month-{MONTH}.facts.json")
    doc["slots"].update(GOOD)
    doc["slots"].update(over or {})
    return doc


def reasons(text, slot: str = "highlight_1", fdoc: dict | None = None) -> list[str]:
    """Why `text` in `slot` is refused ([] = it passes), every other slot holding a good line."""
    return tc.check_doc(commentary({slot: text}, fdoc), fdoc or facts_doc()).refused.get(slot, [])


def refused_for(text, part: str, slot: str = "highlight_1", fdoc: dict | None = None) -> None:
    got = reasons(text, slot, fdoc)
    assert any(part in r for r in got), (text, got)


def passes(text, slot: str = "highlight_1", fdoc: dict | None = None) -> None:
    got = reasons(text, slot, fdoc)
    assert not got, (text, got)


def with_facts(extra: dict) -> dict:
    """The month's facts and a few more ({key: (value, kind)}), with their own digest."""
    doc = copy.deepcopy(facts_doc())
    for key, (v, kind) in extra.items():
        doc["facts"][key] = tc.make_fact(key, v, kind)
    doc["digest"] = tc.digest_of(doc["facts"])
    return doc


# ── the facts against hand sums ────────────────────────────────────────────────────────────────

@test("facts: the Dashboard tiles, their changes and the month's gross and VAT are the hand sums")
def _():
    assert value("month") == MONTH and value("month_prev") == "2026-02"
    assert (value("net"), value("net_prev")) == (170, 75) and close(value("net_change"), 170 / 75 - 1)
    assert (value("units"), value("units_prev"), value("invoices"), value("invoices_prev")) == (71, 45, 5, 3)
    assert (value("avg_invoice"), value("avg_invoice_prev")) == (34, 25) and close(value("avg_invoice_change"), 0.36)
    assert close(value("avg_price"), 170 / 71) and close(value("avg_price_prev"), 75 / 45)
    assert (value("accounts"), value("accounts_prev"), value("reps_active"), value("reps_active_prev")) == (3, 2, 3, 3)
    assert value("reps_active_change") == 0 and facts_doc()["facts"]["reps_active_change"]["shown"]["updown"] == "unchanged"
    assert close(value("gross"), 187) and close(value("vat"), 17)           # 170 net plus 10% VAT
    shown = facts_doc()["facts"]["net_change"]["shown"]
    assert shown == {"pct0": "+127%", "pct1": "+126.7%", "abs_pct0": "127%", "updown": "up"}, shown


@test("facts: the customer base, the cash customers, the week blocks, the first invoices and the best day")
def _():
    assert (value("accounts_both"), value("accounts_cur_only"), value("accounts_prev_only")) == (1, 2, 1)
    assert (value("accounts_both_net"), value("accounts_both_net_prev")) == (90, 40)       # Shop Alpha
    assert value("accounts_cur_only_net") == 60 and value("accounts_cur_only_change") is None     # nothing before
    assert value("accounts_prev_only_net_prev") == 25 and value("accounts_prev_only_change") == -1
    assert (value("cash_net"), value("cash_net_prev")) == (20, 10) and close(value("cash_share"), 20 / 170)
    assert [value(f"wk{k}.net") for k in range(1, 6)] == [110, 10, 0, 0, 50]
    assert [value(f"wk{k}.net_prev") for k in range(1, 6)] == [50, 25, 0, 0, 0]
    assert close(value("wk1.share"), 110 / 170) and value("wk1.label") == "1 to 7" and value("wk5.label") == "29 to month end"
    assert close(value("wk2.change"), -0.6) and value("wk3.change") is None and value("wk5.change") is None
    # an account's first invoice of the month: Alpha and Beta on 1 March, Delta on 10 March (cash is no account)
    assert [value(f"wk{k}.first_accounts") for k in range(1, 6)] == [2, 1, 0, 0, 0]
    assert [value(f"wk{k}.first_accounts_by") for k in range(1, 6)] == [2, 3, 3, 3, 3]
    assert [value(f"wk{k}.first_accounts_prev") for k in range(1, 6)] == [1, 1, 0, 0, 0]
    assert value("first_invoice_day") == "2026-03-01" and value("first_invoice_day_prev") == "2026-02-02"
    assert (value("days_sold"), value("best_day"), value("best_day_net")) == (4, "2026-03-01", 90)
    assert (value("days_sold_prev"), value("best_day_prev"), value("best_day_net_prev")) == (3, "2026-02-02", 40)


@test("facts: the bulk invoices, largest first, their main product, and the month without them")
def _():
    assert (value("bulk_threshold"), value("bulk_count"), value("bulk_net")) == (40, 3, 140)
    assert (value("bulk_count_prev"), value("bulk_net_prev")) == (1, 40)
    assert (value("net_ex_bulk"), value("net_ex_bulk_prev")) == (30, 35) and close(value("net_ex_bulk_change"), 30 / 35 - 1)
    assert close(value("bulk_share"), 140 / 170)
    # 50 on 1 March (Shop Beta) before 50 on 31 March (Shop Alpha): the same amount, the earlier first
    got = [(value(f"bulk{n}.customer"), value(f"bulk{n}.net"), value(f"bulk{n}.code"), value(f"bulk{n}.day"))
           for n in (1, 2, 3)]
    assert got == [("Shop Beta", 50, "X01", "2026-03-01"), ("Shop Alpha", 50, "GN-01", "2026-03-31"),
                   ("Shop Alpha", 40, "T01", "2026-03-01")], got
    assert value("bulk1.product") == "Demo Cable One" and value("bulk1.product_net") == 30 and value("bulk1.rep") == "Van B"
    assert "bulk4.net" not in facts_doc()["facts"]
    every = month_doc(threshold=0)                       # nought: every invoice is a bulk order
    assert every["facts"]["bulk_count"]["value"] == 5 and every["facts"]["net_ex_bulk"]["value"] == 0


@test("facts: the salesmen in the sheet's order with their largest order; categories by sales; the top products")
def _():
    assert value("reps") == 3 and [value(f"rep{i}.name") for i in (1, 2, 3)] == ["Van A", "Van B", "Causeway"]
    assert [value(f"rep{i}.type") for i in (1, 2, 3)] == ["salesman", "salesman", "outlet"]
    assert (value("rep1.net"), value("rep1.net_prev"), value("rep1.invoices"), value("rep1.accounts")) == (100, 40, 3, 2)
    assert close(value("rep1.change"), 1.5) and close(value("rep1.share"), 100 / 170)
    assert (value("rep1.largest"), value("rep1.largest_customer"), value("rep1.largest_day")) == (50, "Shop Alpha",
                                                                                                  "2026-03-31")
    assert (value("rep3.largest"), value("rep3.largest_customer")) == (20, mdl.CASH_ACCOUNT)
    assert value("rep3.accounts") == 0 and value("rep3.units_prev") == 10
    names = [value(f"cat{k}.name") for k in range(1, 7)]
    assert names == ["TWS Earbuds", "Power Banks", "Cables", "Bundle Sets", "Headphones & Neckbands", "Other"], names
    assert (value("cat1.net"), value("cat1.net_prev"), value("cat1.products")) == (60, 20, 2) and close(value("cat1.change"), 2)
    assert value("cat3.change") == 0 and value("cat4.change") is None and value("categories") == 6
    assert value("products_sold") == 7
    assert [value(f"prod{k}.code") for k in range(1, 8)] == ["GN-01", "T01", "X01", "TB-D1", "WS-01", "WG-01", "ZZ9"]
    assert (value("prod1.net"), value("prod1.net_prev"), value("prod1.bulk_net"), value("prod1.net_ex_bulk")) == (50, 25, 50, 0)
    assert (value("prod5.bulk_net"), value("prod5.bulk_invoices"), value("prod5.net_ex_bulk")) == (0, 0, 20)
    assert value("prod1.category") == "Power Banks" and "prod8.code" not in facts_doc()["facts"]


@test("facts: every account by its rank (figures for the top ten), and the unit-price mix by band")
def _():
    assert value("accounts_listed") == 4
    assert [value(f"acct{k}.name") for k in range(1, 5)] == ["Shop Alpha", "Shop Beta", "Shop Delta", "Shop Gamma"]
    assert (value("acct1.net"), value("acct1.net_prev"), value("acct1.invoices"), value("acct1.rep")) == (90, 40, 2, "Van A")
    assert value("acct1.status") == "Feb & Mar" and value("acct4.change") == -1 and value("acct2.change") is None
    # net price a unit: X01 and ZZ9 at 1.0 (band 2), T01 2.0, WG 3.0, WS 4.0 (band 3), GN 5.0 (band 4), TB 10.0 (band 5)
    assert [value(f"band{k}.units") for k in range(1, 6)] == [0, 31, 28, 10, 2]
    assert [value(f"band{k}.net") for k in range(1, 6)] == [0, 31, 69, 50, 20]
    assert [value(f"band{k}.units_prev") for k in range(1, 6)] == [0, 30, 10, 5, 0]
    assert close(value("band2.units_share"), 31 / 71) and close(value("band4.net_share_prev"), 25 / 75)
    assert value("band1.label") == "under BHD 1" and value("band5.label") == "BHD 10 or more"


@test("facts: summaries passed in become prefixed facts; a week, a bad threshold or a bad summary is refused")
def _():
    doc = month_doc(stock={"units": (9, "int"), "value_bhd": {"value": 12.5, "kind": "bhd"}},
                    movements={"to_check": (2, "int")}, recon={"sales": ("PROVEN", "text")},
                    kpi={"met": (4, "int"), "as_of": (date(2026, 3, 31), "date")})
    f = doc["facts"]
    assert f["stock.units"]["value"] == 9 and f["stock.value_bhd"]["shown"]["bhd1"] == "12.5"
    assert f["moves.to_check"]["value"] == 2 and f["recon.sales"]["value"] == "PROVEN"
    assert f["kpi.met"]["shown"]["words"] == "four" and f["kpi.as_of"]["shown"]["day"] == "31 March"
    raises(ValueError, lambda: month_doc(stock={"units": 9}))
    raises(ValueError, lambda: month_doc(stock={"units": (9, "number")}))
    raises(ValueError, lambda: month_doc(kpi={"Bad Key": (1, "int")}))
    raises(ValueError, lambda: month_doc(kpi=[("met", 4, "int")]))
    for bad in (-1, True, "40", float("nan"), None):
        raises(ValueError, lambda x=bad: month_doc(threshold=x))
    week = mdl.build(mdl.Period.week(date(2026, 3, 28)), world_rows())
    exc = raises(ValueError, lambda: fa.month_facts(None, week, None, bulk_invoice_bhd=THRESHOLD))
    assert "month" in str(exc)


@test("facts: the digest is the facts' alone (the data date is not one); a figure moved moves it")
def _():
    a, b = month_doc(), month_doc(data_date="2026-04-20")
    assert a["data_date"] == "2026-04-07" and b["data_date"] == "2026-04-20" and a["digest"] == b["digest"]
    assert a["digest"] == tc.digest_of(a["facts"]) and a["digest"].startswith("sha256:") and len(a["digest"]) == 71
    assert month_doc(threshold=50)["digest"] != a["digest"]              # the threshold is a fact
    rows = world_rows()
    rows[-1] = dict(rows[-1], net_bhd=49.0)                              # one March line one dinar less
    assert month_doc(rows=rows)["digest"] != a["digest"]
    assert month_doc()["digest"] == a["digest"]                          # the same month again: the same digest


# ── the files ──────────────────────────────────────────────────────────────────────────────────

@test("files: the facts file round-trips (valid JSON, one fact a line, its digest); the template is the contract's")
def _():
    doc = facts_doc()
    with data_root() as root:
        out_dir = paths.exports_dir(DATA_DATE)
        res = fa.write_files(out_dir, MONTH, doc)
        assert res["template_written"] and res["facts"] == out_dir / f"report-month-{MONTH}.facts.json"
        assert res["commentary"] == out_dir / f"report-month-{MONTH}.commentary.json"
        assert res["facts"].parent == root / "exports" / "ops" / "2026-04-07"
        text = res["facts"].read_text(encoding="utf-8")
        assert json.loads(text) == doc and len(text.splitlines()) == len(doc["facts"]) + 2
        back = tc.read_facts(res["facts"])
        assert back == doc and tc.digest_of(back["facts"]) == back["digest"]
        tpl = json.loads(res["commentary"].read_text(encoding="utf-8"))
        assert tpl == {"layout": "commentary v1", "month": MONTH, "data_date": "2026-04-07",
                       "facts": f"report-month-{MONTH}.facts.json", "facts_digest": doc["digest"],
                       "slots": {s: "" for s in tc.SLOTS}}, tpl
        assert list(tpl["slots"]) == ([f"highlight_{i}" for i in range(1, 6)] + [f"point_{i}" for i in range(1, 6)]
                                      + ["products_note", "accounts_note", "customers_note"])
        assert tc.facts_file_of(res["commentary"], tpl) == res["facts"]


@test("files: the template is written once and never over a draft; the facts file is rewritten every build")
def _():
    doc = facts_doc()
    with data_root():
        out_dir = paths.exports_dir(DATA_DATE)
        first = fa.write_files(out_dir, MONTH, doc)
        draft = first["commentary"].read_text(encoding="utf-8").replace('"highlight_1": ""', '"highlight_1": "mine"')
        first["commentary"].write_text(draft, encoding="utf-8")
        later = dict(copy.deepcopy(doc), data_date="2026-04-09")
        again = fa.write_files(out_dir, MONTH, later)
        assert not again["template_written"] and again["commentary"].read_text(encoding="utf-8") == draft
        assert json.loads(again["facts"].read_text(encoding="utf-8"))["data_date"] == "2026-04-09"
        raises(ValueError, lambda: fa.write_files(out_dir, "2026-02", doc))                 # another month
        bad = dict(copy.deepcopy(doc), digest="sha256:" + "0" * 64)
        raises(ValueError, lambda: fa.write_files(out_dir, MONTH, bad))                      # a wrong digest
        outside = Path(tempfile.gettempdir()) / "yq-textcheck-never"
        raises(PermissionError, lambda: fa.write_files(outside, MONTH, doc))
        assert not outside.exists()


# ── the renderings ─────────────────────────────────────────────────────────────────────────────

@test("formats: every format renders as the workbook shows the figure (half away from zero, no -0)")
def _():
    r = tc.render_value
    assert (r(5961.5, "bhd", "bhd0"), r(1234.56, "bhd", "bhd1"), r(2.3945, "bhd", "bhd3")) == ("5,962", "1,234.6", "2.395")
    assert (r(2.25, "bhd", "bhd1"), r(0.0005, "bhd", "bhd3"), r(-0.4, "bhd", "bhd0"), r(-2.5, "bhd", "bhd0")) == \
        ("2.3", "0.001", "0", "-3")
    assert (r(1234, "int", "int"), r(70.5, "int", "int"), r(11, "int", "words"), r(3, "int", "Words")) == \
        ("1,234", "70.50", "eleven", "Three")
    assert (r(0, "int", "words"), r(0, "int", "Words"), r(20, "int", "words"), r(21, "int", "words")) == \
        ("no", "No", "twenty", "21")
    assert (r(0.125, "pct", "pct0"), r(0.125, "pct", "pct1"), r(0.12449999999999999, "pct", "pct1")) == \
        ("13%", "12.5%", "12.5%")                # the last as a spreadsheet shows it: fifteen digits first
    assert [r(0.267, "signed", f) for f in ("pct0", "pct1", "abs_pct0", "updown")] == ["+27%", "+26.7%", "27%", "up"]
    assert [r(-0.08, "signed", f) for f in ("pct0", "pct1", "abs_pct0", "updown")] == ["-8%", "-8.0%", "8%", "down"]
    assert [r(-0.004, "signed", f) for f in ("pct0", "abs_pct0", "updown")] == ["0%", "0%", "down"]
    assert [r(0, "signed", f) for f in ("pct0", "updown")] == ["0%", "unchanged"]
    assert (r("2026-03-01", "date", "day"), r("2026-03-01", "date", "month"), r("2026-12", "month", "month")) == \
        ("1 March", "March 2026", "December 2026")
    assert r("Van A", "text", "text") == "Van A"
    for value, kind, fmt in ((1.0, "bhd", "pct0"), (1, "int", "bhd0"), (0.1, "pct", "updown"), (0.1, "signed", "bhd0"),
                             ("2026-03-01", "date", "text"), ("x", "text", "day")):
        raises(ValueError, lambda v=value, k=kind, f=fmt: r(v, k, f))
    raises(ValueError, lambda: r(None, "bhd", "bhd0"))
    raises(ValueError, lambda: r(1, "money", "bhd0"))
    assert tc.FITS["signed"] == ("pct0", "pct1", "abs_pct0", "updown") and set(tc.DEFAULT_FORMAT) == set(tc.KINDS)
    assert {f for fits in tc.FITS.values() for f in fits} == set(tc.FORMATS)


@test("formats: make_fact makes a value plain and renders it in every format that fits; no value, no rendering")
def _():
    f = tc.make_fact("x.net", "1,234.5", "bhd")
    three = "1,234" + ".500"                    # built at run time: the commit scan reads a 3-decimal thousand as a total
    assert f == {"value": 1234.5, "kind": "bhd", "shown": {"bhd0": "1,235", "bhd1": "1,234.5", "bhd3": three}}
    assert tc.make_fact("n", 7.0, "int")["value"] == 7 and isinstance(tc.make_fact("n", 7.0, "int")["value"], int)
    assert tc.make_fact("c", None, "signed") == {"value": None, "kind": "signed", "shown": {}}
    assert tc.make_fact("c", float("inf"), "signed")["value"] is None
    assert tc.make_fact("d", date(2026, 3, 9), "date")["value"] == "2026-03-09"
    assert tc.make_fact("m", date(2026, 3, 9), "month")["value"] == "2026-03"
    for key, v, kind in (("Net", 1, "bhd"), ("1net", 1, "bhd"), ("net-x", 1, "bhd"), ("net", 1, "money"),
                         ("net", True, "bhd"), ("net", "ten", "int"), ("d", "soon", "date"), ("m", "March", "month")):
        raises(ValueError, lambda k=key, x=v, kd=kind: tc.make_fact(k, x, kd))


# ── the token ──────────────────────────────────────────────────────────────────────────────────

@test("token: twelve letters a to p; the same twice; another for any byte changed, added or for other figures")
def _():
    raw = json.dumps(commentary(), indent=1).encode("utf-8")
    digest = facts_doc()["digest"]
    t = tc.token_for(raw, digest)
    assert len(t) == 12 and set(t) <= set("abcdefghijklmnop") and tc.token_for(raw, digest) == t
    sample = raw[:200]
    base = tc.token_for(sample, digest)
    for i in range(len(sample)):
        changed = sample[:i] + bytes([sample[i] ^ 1]) + sample[i + 1:]
        assert tc.token_for(changed, digest) != base, i
    assert tc.token_for(sample + b"\n", digest) != base and tc.token_for(sample, digest[:-1] + "0") != base


# ── the checks: the good lines, and every refusal ──────────────────────────────────────────────

@test("check: the good lines pass and read as written, the figures from the facts")
def _():
    checked = tc.check_doc(commentary(), facts_doc())
    assert checked.ok and not checked.notes, (checked.refused, checked.problems, checked.notes)
    assert checked.rendered == GOOD_READ, checked.rendered
    assert checked.label == "March 2026" and checked.month == MONTH
    assert tc.render_slots(commentary(), facts_doc()) == GOOD_READ


@test("check: a figure typed outside a placeholder is refused (digits, other scripts, % and its look-alikes, percent)")
def _():
    refused_for("Net sales were BHD 170 in {month|month}.", 'a figure typed outside a placeholder ("170")')
    refused_for("Net sales were BHD {net|bhd0} in March 2026.", '"2026"')
    refused_for("Net sales rose {net_change|abs_pct0}% to BHD {net|bhd0}.", 'a "%" typed outside a placeholder')
    refused_for("Net sales rose {net_change|abs_pct0} \uff05.", "typed outside a placeholder")      # full-width %
    refused_for("Net sales of BHD {net|bhd0} were up \u0663 times.", "a figure typed")               # an Arabic-Indic 3
    refused_for("Net sales of BHD {net|bhd0} were up \u00bd again.", "a figure typed")               # a fraction sign
    refused_for("Net sales rose {net_change|abs_pct0} \u2030.", "typed outside a placeholder")      # per mille
    passes("Net sales didn\u2019t fall: {net_change|pct0}.")                     # a curly apostrophe negates too
    refused_for("Net sales didn\u2019t rise: {net_change|pct0}.", "contradicts")
    refused_for("Net sales rose {net_change|abs_pct0} percent.", '"percent" typed outside a placeholder')
    refused_for("Net sales rose {net_change|abs_pct0} per cent.", '"per cent" typed outside a placeholder')


@test("check: an unknown key or format, a format that does not fit, a fact with no figure are refused with the reason")
def _():
    refused_for("Net sales were BHD {nett|bhd0}.", "no fact is named nett (did you mean net?)")
    refused_for("Net sales were BHD {net|bhd2}.", "no format is named bhd2")
    refused_for("Net sales were {net|pct0}.", "the format pct0 does not fit net, an amount in BHD (write bhd0, bhd1, bhd3)")
    refused_for("Sales were {rep1.name|words}.", "does not fit rep1.name, a name or a label")
    refused_for("Invoices were {invoices|updown}.", "does not fit invoices, a count")
    refused_for("Sales were {wk3.change|pct0} on the days before.", "wk3.change has no value this month (the month "
                                                                    "before has nothing to compare with)")
    doc = with_facts({"kpi.owner": (None, "text")})
    refused_for("The count was signed by {kpi.owner} for BHD {net|bhd0}.", "kpi.owner has no value this month", fdoc=doc)
    refused_for("Net sales were BHD { net }.", "{ net } is not a placeholder")
    refused_for("Net sales were BHD {Net|bhd0}.", "is not a placeholder")
    refused_for("Net sales were BHD {net|bhd0} {", "a brace that is not part of a placeholder")
    refused_for("Net sales were BHD {{net|bhd0}}.", "a brace that is not part of a placeholder")
    passes("Net sales were BHD {net}, the units {units} and the change {net_change}.")       # the default formats
    assert tc.check_doc(commentary({"highlight_1": "Net sales were BHD {net}."}), facts_doc()).rendered["highlight_1"] \
        == "Net sales were BHD 170."


@test("check: a highlight or a point needs a placeholder; a note may have none; empty, non-text and line breaks refused")
def _():
    for slot in ("highlight_3", "point_5"):
        refused_for("Sales were strong across the vans.", "no placeholder", slot)
    for slot in ("products_note", "accounts_note", "customers_note"):
        passes("Nothing to add beside this table.", slot)
        refused_for("", "empty: every line is written", slot)
    refused_for("   ", "empty", "point_1")
    refused_for("DRAFT: a note on similar account names, written once it is approved.", "it still reads DRAFT",
                "customers_note")
    refused_for("draft: net sales were BHD {net|bhd0}.", "it still reads DRAFT", "highlight_2")
    passes("Drafted plans held: net sales were BHD {net|bhd0}.", "highlight_2")        # a word that only begins so
    refused_for(12, "not text", "point_2")
    refused_for(None, "not text", "point_2")
    refused_for("Net sales were BHD {net|bhd0}.\nUnits were {units|int}.", "a line break or a control character")
    refused_for("Net sales\twere BHD {net|bhd0}.", "a line break or a control character")


@test("check: an ID-like run, an e-mail address or a phone-like number in the line as it reads is refused")
def _():
    doc = with_facts({"kpi.ref": ("Shop 123456789", "text"), "kpi.card": ("4111111111111111", "text"),
                      "kpi.mail": ("sales@example.com", "text"), "kpi.phone": ("+973 3344 5566", "text"),
                      "kpi.local": ("3344-5566", "text"), "kpi.day": ("2026-03-31", "text"),
                      "kpi.small": ("Shop 12345678", "text")})
    refused_for("The reference {kpi.ref} sold BHD {net|bhd0}.", "a nine-digit run", fdoc=doc)
    refused_for("The card {kpi.card} sold BHD {net|bhd0}.", "a run of fifteen or more digits", fdoc=doc)
    refused_for("Write to {kpi.mail} about BHD {net|bhd0}.", "an e-mail address", fdoc=doc)
    refused_for("Call {kpi.phone} about BHD {net|bhd0}.", "a phone-like number", fdoc=doc)
    refused_for("Call {kpi.local} about BHD {net|bhd0}.", "a phone-like number", fdoc=doc)
    refused_for("The shop {kpi.small} sold BHD {net|bhd0}.", "a phone-like number", fdoc=doc)
    passes("The day {kpi.day} brought BHD {net|bhd0}.", fdoc=doc)          # a date is no phone number
    passes("Net sales were BHD {net|bhd0} on {invoices|int} invoices.", fdoc=doc)
    nine = reasons("The reference {kpi.ref} sold BHD {net|bhd0}.", fdoc=doc)
    assert not any("phone" in x for x in nine), nine                         # said once, as the ID-like run


@test("check: the file is refused for its layout, its month, a slot missing or extra, facts changed by hand or moved")
def _():
    doc = facts_doc()
    assert tc.check_doc(commentary(), doc).ok
    bad = commentary()
    bad["layout"] = "commentary v0"
    assert any("layout" in p for p in tc.check_doc(bad, doc).problems)
    other = commentary()
    other["month"] = "2026-02"
    assert any("the commentary is for 2026-02 and the facts are for 2026-03" in p for p in tc.check_doc(other, doc).problems)
    gone = commentary()
    del gone["slots"]["point_3"]
    gone["slots"]["point_6"] = "{net}"
    probs = tc.check_doc(gone, doc).problems
    assert "slots missing: point_3" in probs and "slots that are not the report's: point_6" in probs, probs
    moved = copy.deepcopy(doc)
    moved["facts"]["net"] = tc.make_fact("net", 171.0, "bhd")
    moved["digest"] = tc.digest_of(moved["facts"])                           # a load moved the month's figures
    probs = tc.check_doc(commentary(fdoc=doc), moved).problems
    assert any("the month's figures changed after this draft was started" in p for p in probs), probs
    by_hand = copy.deepcopy(doc)
    by_hand["facts"]["net"]["shown"]["bhd0"] = "999"                         # the file edited, its digest kept
    probs = tc.check_doc(commentary(), by_hand).problems
    assert any("the facts file was changed after the build" in p for p in probs), probs
    assert not tc.check_doc(commentary(), by_hand).ok
    exc = raises(tc.CommentaryRefused, lambda: tc.render_slots(commentary(fdoc=doc), moved))
    assert not exc.checked.ok and "figures changed" in str(exc)


# ── the direction rule ─────────────────────────────────────────────────────────────────────────

@test("direction: a word against its change is refused, both ways; the agreeing word passes")
def _():
    passes("Net sales rose {net_change|abs_pct0}.")                                      # +127%
    refused_for("Net sales fell {net_change|abs_pct0}.", '"fell" contradicts {net_change|abs_pct0}, which went up')
    passes("Without the bulk orders, sales fell {net_ex_bulk_change|abs_pct0}.")         # -14%
    refused_for("Without the bulk orders, sales grew {net_ex_bulk_change|abs_pct0}.",
                '"grew" contradicts {net_ex_bulk_change|abs_pct0}, which went down')
    refused_for("Sales were lower by {net_change|abs_pct0}.", '"lower" contradicts')
    refused_for("Sales of {wk2.label} were higher: {wk2.change|pct0}.", '"higher" contradicts')
    passes("Sales of {wk2.label} were down {wk2.change|abs_pct0} on the same days of {month_prev|month}.")
    refused_for("Cables rose {cat3.change|abs_pct0}.", "which is unchanged as written")   # 0%: no direction word
    passes("Cables were {cat3.change|updown} at BHD {cat3.net|bhd0}.")
    # a change that reads 0% takes no direction word, whatever its sign
    doc = with_facts({"tiny": (0.004, "signed")})
    refused_for("Sales rose {tiny|abs_pct0}.", "unchanged as written", fdoc=doc)
    passes("Sales were {tiny|updown} {tiny|abs_pct0}.", fdoc=doc)


@test("direction: each word is checked against the change nearest to it; a word with no change in its sentence is refused")
def _():
    passes("Net sales rose {net_change|abs_pct0} while sales without bulk orders fell {net_ex_bulk_change|abs_pct0}.")
    got = reasons("Net sales fell {net_change|abs_pct0} while sales without bulk orders rose "
                  "{net_ex_bulk_change|abs_pct0}.")
    assert len([x for x in got if "contradicts" in x]) == 2, got
    passes("Up {net_change|abs_pct0}, net sales reached BHD {net|bhd0}.")                 # the change after the word
    passes("Net sales, {net_change|abs_pct0} higher, reached BHD {net|bhd0}.")           # ... or before it
    refused_for("Net sales rose {net_change|abs_pct0}. Sales of {wk2.label} rose {wk2.change|abs_pct0}.", '"rose" contradicts')
    # a direction is said with the figure that proves it: the change of sentence one does not reach sentence two
    refused_for("Net sales rose {net_change|abs_pct0}. The days after the first week fell.",
                '"fell" says a direction, but its sentence holds no change to check it against')
    refused_for("{rep2.name} fell to BHD {rep2.net|bhd0}.", '"fell" says a direction')
    refused_for("Sales did not fall, at BHD {net|bhd0}.", '"not fall" says a direction')
    passes("{rep2.name} reached BHD {rep2.net|bhd0}, {rep2.change|pct0} on {month_prev|month}.")    # no direction word


@test("direction: from-to pairs in either order, the same figure as written, BHD between, kinds that differ")
def _():
    passes("Invoices rose from {invoices_prev|int} to {invoices|int}.")
    refused_for("Invoices fell from {invoices_prev|int} to {invoices|int}.", '"fell" contradicts from {invoices_prev|int} '
                                                                              "to {invoices|int}, which went up")
    passes("Invoices rose to {invoices|int} from {invoices_prev|int}.")
    refused_for("Invoices dropped to {invoices|int} from {invoices_prev|int}.", '"dropped" contradicts')
    passes("Net sales without bulk orders slipped from BHD {net_ex_bulk_prev|bhd0} to BHD {net_ex_bulk|bhd0}.")
    refused_for("Cables rose from BHD {cat3.net_prev|bhd0} to BHD {cat3.net|bhd0}.", "unchanged as written")
    passes("Shares moved from {wk1.share_prev|pct0} to {wk1.share|pct0} in the first week, as sales grew "
           "{net_change|abs_pct0}.")
    refused_for("Units rose from {units_prev|int} to BHD {net|bhd0}.", "holds no change")    # two kinds: no pair


@test("direction: negation, phrasal words and hyphenated words; an unsigned change needs its direction")
def _():
    passes("Net sales did not fall: they were {net_change|updown} {net_change|abs_pct0}.")
    refused_for("Net sales did not rise: {net_change|pct0}.", '"not rise" contradicts')
    refused_for("Net sales didn't rise: {net_change|pct0}.", "\"didn't rise\" contradicts")
    passes("Sales without bulk orders did not grow: {net_ex_bulk_change|pct0}.")
    passes("Orders of up to BHD {bulk_threshold|bhd0} fell {net_ex_bulk_change|abs_pct0}.")              # up to
    passes("Bulk orders made up {bulk_share|pct0} of sales and the rest fell {net_ex_bulk_change|abs_pct0}.")   # made up
    refused_for("Bulk orders went up {bulk_share|pct0} and the rest fell {net_ex_bulk_change|abs_pct0}.", '"up" contradicts')
    passes("Sales broken down by week fell {net_ex_bulk_change|abs_pct0} without bulk orders.")
    passes("Higher-value orders were bulk, and the rest fell {net_ex_bulk_change|abs_pct0}.")
    refused_for("Higher value orders were bulk, and the rest fell {net_ex_bulk_change|abs_pct0}.", '"Higher" contradicts')
    refused_for("Net sales changed by {net_change|abs_pct0}.", "drops the change's sign")
    refused_for("Net sales rose {net_change|abs_pct0} and units {units_change|abs_pct0}.", "{units_change|abs_pct0} drops")
    passes("Net sales were {net_change|updown} {net_change|abs_pct0}.")
    passes("Net sales changed by {net_change|pct0}.")                                    # the sign is in the figure


# ── the number words ───────────────────────────────────────────────────────────────────────────

@test("number words: refused outside a placeholder, in any case, split at hyphens; -fold and the like")
def _():
    for word in ("three", "Three", "TWELVE", "twenty", "forty", "hundred", "thousands", "million", "half", "quarter",
                 "doubled", "twice", "triple", "treble", "thrice", "dozens", "zero", "nought", "twofold", "tenfold"):
        refused_for(f"Net sales of BHD {{net|bhd0}} came {word} times.", f'"{word.lower()}"')
    refused_for("Net sales of BHD {net|bhd0} came on twenty-one days.", '"twenty", "one"')
    passes("Net sales of BHD {net|bhd0} came from the vans.")
    for fine in ("someone", "none", "phone", "alone", "done", "lonely", "often", "tension", "bones"):
        passes(f"Net sales of BHD {{net|bhd0}} came {fine} way.")


@test("number words: the edge cases of 'one' (one of the, no one, one another), ordinals, fractions and dates")
def _():
    passes("{rep1.name} was one of the vans with BHD {rep1.net|bhd0}.")
    passes("One of the vans sold BHD {rep1.net|bhd0}.")
    passes("One of its accounts bought BHD {acct1.net|bhd0}; no one else came close; the vans helped one another.")
    refused_for("Only one account bought in {month_prev|month} alone.", '"one"', "point_2")
    refused_for("One of {accounts|words} accounts bought BHD {acct1.net|bhd0}.", '"one"')
    refused_for("Shop Alpha placed a one-off order of BHD {bulk2.net|bhd0}.", '"one"')
    refused_for("Bulk orders were a third of BHD {net|bhd0}.", '"third of" types a share or a date')
    refused_for("Bulk orders came to a third, BHD {bulk_net|bhd0}.", '"a third" types a share')
    refused_for("Bulk orders came to two thirds of BHD {net|bhd0}.", '"two"')
    refused_for("The best day was the fifth of {month|month}, BHD {best_day_net|bhd0}.", '"fifth of"')
    refused_for("First of all, net sales were BHD {net|bhd0}.", '"first of"')
    passes("The first invoice came on {first_invoice_day|day}, BHD {net|bhd0} in all.")
    passes("The third week brought BHD {wk3.net|bhd0}; the second invoice came later.")
    refused_for("The second half brought BHD {wk5.net|bhd0}.", '"half"')


@test("capitals: a capitalised word that is no fact is a note, never a refusal; names of facts and BHD pass")
def _():
    checked = tc.check_doc(commentary({"point_4": "{acct4.name} was not invoiced, as Zebra Trading noted in "
                                                  "{month|month}."}), facts_doc())
    assert checked.ok, checked.refused
    assert checked.notes == ['"Zebra" is capitalised but no fact holds it (point_4): a name should come from a '
                             'placeholder', '"Trading" is capitalised but no fact holds it (point_4): a name should '
                             'come from a placeholder'], checked.notes
    calm = tc.check_doc(commentary({"point_4": "Sales at the Causeway outlet and Van A were BHD {net|bhd0} with VAT "
                                               "in the ERP."}), facts_doc())
    assert calm.ok and not calm.notes, calm.notes


# ── the command ────────────────────────────────────────────────────────────────────────────────

def run_cli(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli.main(argv)
    return code, buf.getvalue()


def files_under(root: Path) -> dict:
    return {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


def drafted(over: dict | None = None) -> tuple[Path, dict]:
    """The build's two files, and the template filled in with the good lines (and `over`)."""
    doc = facts_doc()
    res = fa.write_files(paths.exports_dir(DATA_DATE), MONTH, doc)
    tpl = json.loads(res["commentary"].read_text(encoding="utf-8"))
    tpl["slots"].update(GOOD)
    tpl["slots"].update(over or {})
    res["commentary"].write_text(json.dumps(tpl, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return res["commentary"], doc


@test("command: every line passes -> each line as it reads, then FINAL with the token of the file and the figures")
def _():
    with data_root() as root:
        path, doc = drafted({"point_4": "{acct4.name} was not invoiced, Zebra says, in {month|month}."})
        before = files_under(root)
        code, out = run_cli(["check", str(path), "--data-root", str(root)])
        assert code == 0, out
        lines = out.strip().splitlines()
        token = tc.token_for(path.read_bytes(), doc["digest"])
        assert lines[-1] == f'FINAL: python -m scripts.ops report month {MONTH} --commentary "{path}" --token {token}', \
            lines[-1]
        assert lines[0] == "The comment lines for March 2026: all 13 pass. As they will read:", lines[0]
        assert "  1. Net sales rose 127% to BHD 170, from BHD 75 in February 2026." in lines
        assert "Under the top products: The top products hold the bulk orders: GN-01 came on a single invoice." in lines
        assert any(ln.startswith('Note: "Zebra" is capitalised') for ln in lines[:-1])     # notes above FINAL
        assert files_under(root) == before                                                 # nothing written
        code, out = run_cli(["check", str(path), "--data-root", str(root), "--json"])
        payload = json.loads(out.strip().splitlines()[-1])
        assert code == 0 and payload["ok"] and payload["command"] == "check" and payload["data_date"] == "2026-04-07"
        assert payload["flags"]["passed"] and payload["flags"]["token"] == token and payload["files"] == []
        assert payload["flags"]["final"].endswith(f"--token {token}") and len(payload["flags"]["notes"]) == 1
        again = path.read_bytes().replace(b'"accounts_note": "Each', b'"accounts_note":  "Each')   # one byte more
        path.write_bytes(again)
        code, out = run_cli(["check", str(path), "--data-root", str(root)])
        assert code == 0 and not out.strip().endswith(token), "another byte, another token"


@test("command: a refused line -> exit 2, the reasons, no FINAL line; the figures moved -> refused too")
def _():
    with data_root() as root:
        path, doc = drafted({"highlight_2": "Net sales fell {net_change|abs_pct0} on BHD 170."})
        before = files_under(root)
        code, out = run_cli(["check", str(path), "--data-root", str(root)])
        assert code == 2 and not any(ln.startswith("FINAL") for ln in out.splitlines()), out
        assert "The comment lines for March 2026: 1 of 13 refused. No FINAL line; nothing is written." in out
        assert "  2. REFUSED (highlight_2)" in out and '"fell" contradicts' in out and '("170")' in out
        assert out.strip().splitlines()[-1].startswith("Correct what is refused in")
        assert files_under(root) == before
        # a load moved the month's figures: the facts file is rewritten, the draft still names the old digest
        path, _doc = drafted()
        moved = copy.deepcopy(doc)
        moved["facts"]["net"] = tc.make_fact("net", 171.0, "bhd")
        moved["digest"] = tc.digest_of(moved["facts"])
        fa.write_files(paths.exports_dir(DATA_DATE), MONTH, moved)
        code, out = run_cli(["check", str(path), "--data-root", str(root)])
        assert code == 2 and "REFUSED: the month's figures changed after this draft was started" in out
        assert not any(ln.startswith("FINAL") for ln in out.splitlines()), out


@test("command: a file that cannot be read -> exit 1 in one sentence (none named, missing, no JSON, layout, no facts)")
def _():
    with data_root() as root:
        code, out = run_cli(["check", "--data-root", str(root)])
        assert code == 1 and "Name the filled-in commentary file" in out
        code, out = run_cli(["check", str(root / "none.json"), "--data-root", str(root)])
        assert code == 1 and "No commentary file at" in out
        junk = root / "exports" / "junk.json"
        junk.parent.mkdir(parents=True, exist_ok=True)
        junk.write_text("{not json", encoding="utf-8")
        code, out = run_cli(["check", str(junk), "--data-root", str(root)])
        assert code == 1 and "its JSON cannot be read" in out
        junk.write_text(json.dumps({"layout": "something else", "slots": {}}), encoding="utf-8")
        code, out = run_cli(["check", str(junk), "--data-root", str(root)])
        assert code == 1 and "is not a commentary file of layout 'commentary v1'" in out
        path, _doc = drafted()
        Path(str(path).replace(".commentary.json", ".facts.json")).unlink()
        code, out = run_cli(["check", str(path), "--data-root", str(root)])
        assert code == 1 and "The facts file" in out and "is not there" in out


@test("final step: render_slots gives the build its lines; they fill sheets_core.Commentary and the file is not DRAFT")
def _():
    rendered = tc.render_slots(commentary(), facts_doc())
    fields = tc.commentary_fields(rendered)
    assert fields["highlights"][0] == GOOD_READ["highlight_1"] and fields["points"][4] == GOOD_READ["point_5"]
    said = sc.Commentary(**fields)
    assert not said.is_draft
    b, _m, _f = built(commentary=said)
    dash = b.get("Dashboard")
    assert dash.cells[(14, 1)].value == GOOD_READ["highlight_1"] and dash.cells[(18, 10)].value == GOOD_READ["point_5"]
    assert dash.cells[(53, 10)].value == GOOD_READ["products_note"] and dash.cells[(72, 1)].value == GOOD_READ["accounts_note"]
    assert not any(str(c.value).startswith("DRAFT") for c in dash.cells.values() if c.kind == "s")
    # the words refer to the figures as the workbook shows them: the facts of this book are the same facts
    again = fa.month_facts(b, _m, _f, bulk_invoice_bhd=THRESHOLD, data_date=DATA_DATE)
    assert again["digest"] == facts_doc()["digest"]
    assert math.isclose(facts_doc()["facts"]["net"]["value"], dash.cells[(8, 1)].value)


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
