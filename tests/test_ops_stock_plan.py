"""Ops engine: the order plan behind the Stock & Order Plan workbook (scripts.ops.stock_plan), on the synthetic
world of tests/test_ops_stock.py.

    python -m tests.test_ops_stock_plan

Same lightweight runner as the other suites (no pytest). No database, no network, no .env: SUPABASE_URL,
SUPABASE_KEY and DATABASE_URL are pinned to ci.invalid BEFORE any import, and the world, its stand-ins for the
reads and the temporary data roots are the ones of tests/test_ops_stock.py (it also sets stdout and stderr to
UTF-8 when it is imported). All names and numbers are invented.

The world (data date Sun 15 Mar 2026), by hand, as the plan sees it:
  * ITEM-A  394 on hand, 197 days of cover               -> Overstock     (384 in the sellable pool: 10 below main)
  * ITEM-B  20 on hand, 20 days, 35 more in the 2nd van  -> Order now     (65 to order)
  * ITEM-C  50 on hand, never sold                       -> No sale 90 d
  * ITEM-N  36 on hand, first received on 1 Mar          -> New arrival
  * ITEM-F  none on hand, last sold in September         -> No stock, no sale
  * DISP-01 the display item                             -> Display
  * ZN9     a catalog code (WEKOME) that never moved     -> Not in main store yet
Every item says VFAN (the catalog's brand) until config or a purchase order says otherwise.

Covered:
  * round6 / units_up at the boundaries;
  * the supplier of an item: config, the newest purchase order, the catalog's brand (VFAN for every catalogued
    item, so an order's vendor must outrank it), else Other; the basis of each; the aliases; suppliers_of;
  * a catalog row of another division is left out; a blank division is an accessory; a code is a row once;
  * the order list by supplier ("Other" last, urgency kept inside a supplier) and the totals per supplier;
  * every item's row (the numbers, the sold windows, the age of the oldest unit, the vans, the damage store),
    the sort, the status of each item and the rule's precedence (a new arrival that has sold out, or has to be
    ordered, shows as that), the boundary of Overstock;
  * a catalog that was not read; a category the catalog lacks; goods on order;
  * all_items' vans (the units the order position counts beside the main store) beside with_vans (the pool's own,
    the v2 file's column), nought where nothing is counted; each order row's beside, in the JSON form too;
  * the screens and files: the Supplier column of /yq-stock reorder, the plan in stock.json, facts_json.
"""
from __future__ import annotations

import json
import os
import re
import sys
import traceback
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"

from tests.test_ops_stock import (  # noqa: E402  (first: it imports scripts.ops.db first, and sets stdout to UTF-8)
    CATALOG_ROWS, CODES, D, DEFAULT_POSITION, NAMES, L, World, data_root, model_of, moving, run, snapshot_rows,
    standing_in, world)
from scripts.ops import config, paths, stock, stock_plan  # noqa: E402

stock.RAISE_PARTS = True              # a part that fails fails the test, never stands aside quietly

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


# ── helpers ───────────────────────────────────────────────────────────────────

def build(w: World | None = None, cfg: dict | None = None):
    """The stock Model of a world, built the way the command builds it (its plan is m.facts["plan"])."""
    with data_root(cfg):
        m, _inp = model_of(w or World())
    return m


def with_cfg(m, **changes):
    """The same model under other settings (suppliers, supplier_aliases)."""
    return replace(m, cfg=replace(m.cfg, **changes))


def plan_of(m, catalog=CATALOG_ROWS, po=None) -> dict:
    """plan_facts on a built model with the catalog and the purchase-order vendors given."""
    return stock_plan.plan_facts(m, m.flows, catalog, po)


def catalog(changes: dict | None = None, extra=(), drop=()) -> list[dict]:
    """The world's catalog with changes {code: {field: value}}, some rows left out and some added."""
    rows = [dict(r, **(changes or {}).get(r["code"], {})) for r in CATALOG_ROWS if r["code"] not in drop]
    return rows + [dict(r) for r in extra]


def row(plan: dict, code: str) -> dict:
    return next(r for r in plan["all_items"] if r["code"] == code)


def sup(plan: dict, key: str) -> tuple[str, str]:
    """(supplier, basis) of one of the world's items, by its key (A, B, C, N, F, DISP)."""
    s = plan["suppliers"][L(key)]
    return s["name"], s["basis"]


def holds(x, kinds) -> bool:
    """Whether a structure holds an object of one of these types anywhere."""
    if isinstance(x, kinds):
        return True
    if isinstance(x, dict):
        return any(holds(k, kinds) or holds(v, kinds) for k, v in x.items())
    if isinstance(x, (list, tuple, set)):
        return any(holds(v, kinds) for v in x)
    return False


def crowded() -> World:
    """Two more items on the order list: 300 of ITEM-A and 30 of ITEM-N found damaged on the data date."""
    b = world()
    b.to_damage(L("A"), D, 300)            # 394 -> 94 on hand, two a day: 47 days of cover, 76 to order
    b.to_damage(L("N"), D, 30)             # 36 -> 6 on hand, 4 in 30 days: 45 days of cover, 6 to order
    return World(book=b, snapshot=snapshot_rows({"A": 94, "N": 6}))


def damaged(**units) -> World:
    """The world with units of some items (A, B, N, ...) found damaged in the main store on the data date."""
    b, held = world(), {}
    left = {"A": 394, "B": 20, "C": 50, "N": 36}
    for key, n in units.items():
        b.to_damage(L(key), D, n)
        held[key] = left[key] - n
    return World(book=b, snapshot=snapshot_rows(held))


# ── the rounding rule ─────────────────────────────────────────────────────────

@test("round6 and units_up: six decimals half away from nought, then a whole unit up; nought or less is 0")
def _():
    r6, up = stock_plan.round6, stock_plan.units_up
    assert r6(5.0000004) == Decimal("5.000000") and up(5.0000004) == 5       # below the boundary: no extra unit
    assert r6(5.0000006) == Decimal("5.000001") and up(5.0000006) == 6       # above it: a whole unit more
    assert r6(Decimal("5.0000005")) == Decimal("5.000001") and up(Decimal("5.0000005")) == 6   # the half goes up
    assert r6(Decimal("-0.0000005")) == Decimal("-0.000001")                 # half away from nought, both ways
    assert up(5) == 5 and up(Decimal("5.000000")) == 5 and up("2.000001") == 3 and up("0.5") == 1
    assert up(Decimal("0.0000005")) == 1 and up(Decimal("0.0000004")) == 0   # 0.000001 is above nought, 0 is not
    for x in (0, Decimal(0), -1, Decimal("-0.5"), -5.0000006, "-0.0000001"):
        assert up(x) == 0 and isinstance(up(x), Decimal), x
    assert isinstance(r6(1), Decimal) and isinstance(up(1.5), Decimal) and up(1.5) == 2


# ── the supplier ──────────────────────────────────────────────────────────────

@test("the supplier of an item: config, then the newest purchase order, then the catalog's brand, else Other")
def _():
    m = build()
    # the catalog says VFAN for the whole world, as it does for every catalogued item: an order's vendor outranks it
    plan = plan_of(m, CATALOG_ROWS, {"CB2": "Shenzhen Vendor", "HG3": "Shenzhen Vendor", "SN4": "Port Vendor"})
    assert {k: sup(plan, k) for k in NAMES} == {
        "A": ("VFAN", "catalog"), "B": ("Shenzhen Vendor", "purchase order"), "C": ("Shenzhen Vendor", "purchase order"),
        "N": ("Port Vendor", "purchase order"), "F": ("VFAN", "catalog"), "DISP": ("VFAN", "catalog")}
    # no purchase order read, or none for the code: the brand answers
    plan = plan_of(m, CATALOG_ROWS, None)
    assert {k: sup(plan, k) for k in NAMES} == {k: ("VFAN", "catalog") for k in NAMES}
    # every rung of the ladder, in one go
    po = {"CA1": "Order Vendor", "CB2": "Shenzhen Vendor", "SN4": "Port Vendor", "ZN9": "Late Vendor"}
    cat = catalog({"CB2": {"brand": None}, "HG3": {"brand": "   "}, "DS1": {"brand": "Acme"},
                   "FO5": {"division": "Devices"}}, drop=("SN4",))
    mc = with_cfg(m, suppliers={"CA1": "Local Trader"})
    plan = plan_of(mc, cat, po)
    got = {k: sup(plan, k) for k in NAMES}
    assert got == {"A": ("Local Trader", "config"),                # config beats the order and the brand (VFAN)
                   "B": ("Shenzhen Vendor", "purchase order"),     # no brand: the vendor of the newest order
                   "C": ("Other", "none"),                         # a brand of spaces is no brand; no order
                   "N": ("Port Vendor", "purchase order"),         # no catalog row at all
                   "F": ("Other", "none"),                         # a row of another division is no row; no order
                   "DISP": ("Acme", "catalog")}, got               # no order: the brand
    # every row of all_items names the basis it rests on, the catalog code no item has included
    assert {k: row(plan, CODES[k])["supplier_basis"] for k in NAMES} == {k: v[1] for k, v in got.items()}
    assert {k: row(plan, CODES[k])["supplier"] for k in NAMES} == {k: v[0] for k, v in got.items()}
    z = row(plan, "ZN9")
    assert (z["supplier"], z["supplier_basis"]) == ("Late Vendor", "purchase order")     # an upcoming item that is ordered
    z = row(plan_of(mc, cat, None), "ZN9")
    assert (z["supplier"], z["supplier_basis"]) == ("WEKOME", "catalog")                  # not ordered: its brand
    z = row(plan_of(with_cfg(m, suppliers={"ZN9": "Local Trader"}), cat, po), "ZN9")
    assert (z["supplier"], z["supplier_basis"]) == ("Local Trader", "config")
    # no purchase orders read: the rung is skipped, and Other stands where nothing else answers
    got = {k: sup(plan_of(mc, cat, None), k) for k in NAMES}
    assert got["A"] == ("Local Trader", "config") and got["B"] == ("Other", "none") and got["N"] == ("Other", "none")
    assert got["DISP"] == ("Acme", "catalog")
    # no catalog read at all: config and the orders still answer
    got = {k: sup(plan_of(mc, None, po), k) for k in NAMES}
    assert got == {"A": ("Local Trader", "config"), "B": ("Shenzhen Vendor", "purchase order"),
                   "C": ("Other", "none"), "N": ("Port Vendor", "purchase order"),
                   "F": ("Other", "none"), "DISP": ("Other", "none")}, got
    # stock reads the same ladder before there is a plan (suppliers_of), item by item
    plan = plan_of(mc, cat, po)
    assert stock_plan.suppliers_of(m.items, mc.cfg, cat, po) == {
        name: (s["name"], s["basis"]) for name, s in plan["suppliers"].items()}
    assert stock_plan.suppliers_of(m.items, mc.cfg, None, None)[L("B")] == ("Other", "none")
    # supplier_of reads the same table (the reorder view uses it); an unknown name is Other
    assert stock_plan.supplier_of(plan, L("A")) == "Local Trader"
    assert stock_plan.supplier_of(plan, L("B")) == "Shenzhen Vendor"
    assert stock_plan.supplier_of(plan, "no such item") == "Other" and stock_plan.supplier_of({}, L("A")) == "Other"
    assert stock_plan.supplier_of({"suppliers": {"x": {"name": "", "basis": "none"}}}, "x") == "Other"
    # through the build: an order's vendor is the supplier on the order list's rows and in the plan alike
    m3 = build(World(po_vendor=[("cb2", "Shenzhen Vendor")]))
    assert m3.facts["order"][0]["supplier"] == m3.facts["plan"]["order"][0]["supplier"] == "Shenzhen Vendor"
    assert m3.facts["plan"]["suppliers"][L("B")] == {"name": "Shenzhen Vendor", "basis": "purchase order"}
    assert m3.facts["plan"]["suppliers"][L("A")] == {"name": "VFAN", "basis": "catalog"}


@test("the names pass through config's aliases: spaces collapsed, the spelling folded, whichever rung named them")
def _():
    m = build()
    aliases = {"vfan co": "VFAN", "shenzhen vendor": "Shenzhen Traders", "local trader": "Local Traders Ltd"}
    cat = catalog({"CB2": {"brand": "  Vfan   CO "}, "HG3": {"brand": "Elsewhere Brand"},
                   "SN4": {"brand": "Acme    Works"}})
    mc = with_cfg(m, suppliers={" ca1 ": " local   TRADER "}, supplier_aliases=aliases)
    plan = plan_of(mc, cat, {"hg3": "SHENZHEN   Vendor"})
    assert sup(plan, "A") == ("Local Traders Ltd", "config")           # a key with spaces, a name in odd case
    assert sup(plan, "B") == ("VFAN", "catalog")                       # " Vfan   CO " is the alias "vfan co"
    assert sup(plan, "C") == ("Shenzhen Traders", "purchase order")    # a code and a vendor in any case; it beats the brand
    assert sup(plan, "N") == ("Acme Works", "catalog")                 # no alias: only the spaces are collapsed
    assert sup(plan, "F") == ("VFAN", "catalog")
    # a catalog code no item has is named the same way
    cat2 = catalog(extra=[{"code": "ZN8", "display_name": "Another Upcoming", "category": "Cables", "brand": "vfan CO",
                           "division": "Accessories", "is_active": True, "hidden": False}])
    assert row(plan_of(mc, cat2, None), "ZN8")["supplier"] == "VFAN"
    # through config.json itself (settings() writes the keys as the portal compares codes)
    m2 = build(cfg={"stock.suppliers": {" cb2 ": " Local  Trader "},
                    "stock.supplier_aliases": {"Local TRADER": "Local Traders Ltd"}})
    assert m2.facts["plan"]["suppliers"][L("B")] == {"name": "Local Traders Ltd", "basis": "config"}
    assert m2.facts["plan"]["suppliers"][L("A")] == {"name": "VFAN", "basis": "catalog"}


# ── the catalog ───────────────────────────────────────────────────────────────

@test("the catalog counts only accessories: another division is left out, a blank one is in, a code is a row once")
def _():
    m = build()
    extra = [
        {"code": "DV1", "display_name": "Phone Alpha", "category": "Phones", "brand": "Acme", "division": "Devices",
         "is_active": True, "hidden": False},
        {"code": "BL1", "display_name": "  Blank   Division Cable ", "category": None, "brand": None, "division": None,
         "is_active": None, "hidden": None},
        {"code": "AC1", "display_name": "Upper Case Division", "category": "Cables", "brand": "WEKOME",
         "division": " ACCESSORIES ", "is_active": False, "hidden": True},
        {"code": "zn9", "display_name": "A second row of ZN9", "category": "Other", "brand": "Elsewhere",
         "division": "Accessories", "is_active": False, "hidden": True},
    ]
    plan = plan_of(m, catalog(extra=extra))
    codes = [r["code"] for r in plan["all_items"]]
    assert "DV1" not in codes and len(codes) == len(set(codes)) == 9           # 6 items + ZN9, BL1, AC1
    assert plan["catalog_items"] == 9 and plan["catalog_only"] == 3 and plan["catalog_read"] is True
    z, bl, ac = row(plan, "ZN9"), row(plan, "BL1"), row(plan, "AC1")
    assert z["name"] == "ZZ-NEW Speaker Upcoming" and z["supplier"] == "WEKOME" and z["active"] is True   # the first row
    assert bl["name"] == "Blank Division Cable" and bl["category"] == "" and bl["supplier"] == "Other"     # no brand
    assert bl["active"] is None and bl["hidden"] is None                                                   # says nothing
    assert ac["category"] == "Cables" and ac["supplier"] == "WEKOME" and ac["active"] is False and ac["hidden"] is True
    assert {r["status"] for r in (z, bl, ac)} == {"Not in main store yet"} and all(r["catalog_only"] for r in (z, bl, ac))
    # an item whose only catalog row is another division has no catalog row: no flags, no category, no brand
    plan = plan_of(m, catalog({"CB2": {"division": "Devices"}}))
    b = row(plan, "CB2")
    assert b["active"] is None and b["hidden"] is None and b["category"] == "" and sup(plan, "B") == ("Other", "none")
    assert plan["catalog_items"] == 6 and plan["catalog_only"] == 1            # CB2 is neither counted nor "only"
    assert row(plan, "CA1")["active"] is True and row(plan, "DS1")["hidden"] is True


@test("a catalog that was not read: the model's items only, no flags, the supplier from config or the orders")
def _():
    w = World(fail={stock.CATALOG_SQL: RuntimeError("down")}, po_vendor=[("cb2", "Shenzhen Vendor")])
    m = build(w)
    plan = m.facts["plan"]
    assert plan["catalog_read"] is False and plan["catalog_items"] == 0 and plan["catalog_only"] == 0
    assert sorted(r["code"] for r in plan["all_items"]) == sorted(CODES.values())          # the six, no ZN9
    assert all(r["item"] is not None and not r["catalog_only"] for r in plan["all_items"])
    assert all(r["active"] is None and r["hidden"] is None and r["category"] == "" for r in plan["all_items"])
    assert sup(plan, "B") == ("Shenzhen Vendor", "purchase order")                         # the PO vendor, any case
    assert {sup(plan, k) for k in NAMES if k != "B"} == {("Other", "none")}
    assert {r["code"]: r["supplier_basis"] for r in plan["all_items"]} == {
        c: ("purchase order" if c == "CB2" else "none") for c in CODES.values()}
    assert plan["order"][0]["supplier"] == "Shenzhen Vendor"
    # the reads that failed are named under the screen, and the reorder view still prints
    with data_root():
        text = run(w, "reorder")["text"]
    assert "Not read this time: the catalog (catalog_items) (RuntimeError)." in text and "Shenzhen Vendor" in text
    # no catalog, no purchase orders and no flows at all: the plan still stands (units beside the main store: none)
    bare = stock_plan.plan_facts(m, None, None, None)
    assert len(bare["all_items"]) == 6 and bare["catalog_read"] is False
    assert all(r["with_vans"] == 0 and r["damage"] == 0 for r in bare["all_items"]) and sup(bare, "B") == ("Other", "none")
    # an empty catalog was read: it says nothing, and that is not "not read"
    empty = plan_of(m, [], None)
    assert empty["catalog_read"] is True and empty["catalog_items"] == 0 and len(empty["all_items"]) == 6


@test("a category the catalog lacks comes from the item's code, where the code gives one")
def _():
    from scripts.ops.workbook import categories
    wall, power = categories.by_code("UK12"), categories.by_code("F12")
    assert wall and power and wall != power
    m = build()
    next(it for it in m.items if it.ledger_name == L("A")).code = "UK12"              # an item with a code of the rule's
    next(it for it in m.items if it.ledger_name == L("B")).code = "UK13"
    cat = catalog(extra=[
        {"code": "UK13", "display_name": "Charger With A Card Category", "category": "Chargers", "brand": "VFAN",
         "division": "Accessories", "is_active": True, "hidden": False},
        {"code": "F12-NEW", "display_name": "Upcoming Bank", "category": "  ", "brand": "WEKOME",
         "division": "Accessories", "is_active": True, "hidden": False}], drop=("CA1", "CB2"))
    plan = plan_of(m, cat)
    assert row(plan, "UK12")["category"] == wall                                       # no catalog row at all
    assert row(plan, "UK13")["category"] == "Chargers"                                 # the catalog's word wins
    assert row(plan, "F12-NEW")["category"] == power and row(plan, "F12-NEW")["catalog_only"]    # blank: the code's
    assert row(plan, "HG3")["category"] == "Holders"                                   # a code the rule does not know


# ── the order list by supplier ────────────────────────────────────────────────

@test("the order list by supplier: alphabetical, Other last, the urgency order kept inside a supplier")
def _():
    m = build(crowded(), {"stock.suppliers": {"CA1": "Alpha Supply", "SN4": "Alpha Supply"}})
    # the order rule is stock's: by cover, most urgent first, each row naming its supplier by the same ladder
    assert [(x["item"].code, x["supplier"]) for x in m.facts["order"]] == [
        ("CB2", "VFAN"), ("SN4", "Alpha Supply"), ("CA1", "Alpha Supply")]
    assert [x["qty"] for x in m.facts["order"]] == [65, 6, 76]
    # the plan holds those rows by supplier: VFAN after Alpha Supply, the most urgent first inside a supplier
    plan = m.facts["plan"]
    assert [(x["item"].code, x["supplier"]) for x in plan["order"]] == [
        ("SN4", "Alpha Supply"), ("CA1", "Alpha Supply"), ("CB2", "VFAN")]
    assert all(plan["suppliers"][x["item"].ledger_name]["name"] == x["supplier"] for x in m.facts["order"])
    before = [dict(x) for x in m.facts["order"]]
    cat = catalog({"CB2": {"brand": None}})                                       # ITEM-B has no brand: Other
    plan = plan_of(m, cat)
    assert [(x["item"].code, x["supplier"]) for x in plan["order"]] == [
        ("SN4", "Alpha Supply"), ("CA1", "Alpha Supply"), ("CB2", "Other")]       # B is the most urgent, and last
    # the plan names the supplier from the catalog it is given, in its own copy of each row; stock's rows stay as built
    assert [dict(x) for x in m.facts["order"]] == before and m.facts["order"][0]["supplier"] == "VFAN"
    assert not any(p is q for p in plan["order"] for q in m.facts["order"])
    assert plan["order"][0]["cover"] == m.facts["order"][1]["cover"] and plan["order"][2]["qty"] == 65
    assert plan["supplier_totals"] == [
        {"supplier": "Alpha Supply", "items": 2, "units": Decimal(82), "value": Decimal("49.600"), "uncosted": 0},
        {"supplier": "Other", "items": 1, "units": Decimal(65), "value": Decimal("162.500"), "uncosted": 0}]
    assert [t["supplier"] for t in plan["supplier_totals"]] == ["Alpha Supply", "Other"]    # the rows' order
    # three suppliers: "Other" sorts after Zeta although it comes before it in the alphabet
    cat = catalog({"CA1": {"brand": "Alpha Supply"}, "CB2": {"brand": "Zeta Trading"}, "SN4": {"brand": None}})
    plan = plan_of(with_cfg(m, suppliers={}), cat)
    assert [(x["item"].code, x["supplier"]) for x in plan["order"]] == [
        ("CA1", "Alpha Supply"), ("CB2", "Zeta Trading"), ("SN4", "Other")]
    assert [t["supplier"] for t in plan["supplier_totals"]] == ["Alpha Supply", "Zeta Trading", "Other"]
    assert [(t["items"], t["units"]) for t in plan["supplier_totals"]] == [(1, 76), (1, 65), (1, 6)]
    # run twice: the same answer (nothing is kept or changed between calls)
    again = plan_of(with_cfg(m, suppliers={}), cat)
    pairs = [(x["item"].code, x["supplier"]) for x in plan["order"]]
    assert [(x["item"].code, x["supplier"]) for x in again["order"]] == pairs


@test("supplier totals: units, the value where the cost is known, and the rows without a cost counted apart")
def _():
    m = build(crowded(), {"stock.suppliers": {"CA1": "Alpha Supply", "SN4": "Alpha Supply"}})
    rows = [dict(x) for x in m.facts["order"]]
    by = {x["item"].code: x for x in rows}
    by["SN4"]["cost_bhd"] = None                                                 # no cost on file for ITEM-N
    by["CB2"]["cost_bhd"] = None
    plan = stock_plan.plan_facts(replace(m, facts=dict(m.facts, order=rows)), m.flows, CATALOG_ROWS, None)
    got = {t["supplier"]: t for t in plan["supplier_totals"]}
    assert got["Alpha Supply"] == {"supplier": "Alpha Supply", "items": 2, "units": Decimal(82),
                                   "value": Decimal("30.400"), "uncosted": 1}      # only ITEM-A's 76 x 0.4 is valued
    assert got["VFAN"] == {"supplier": "VFAN", "items": 1, "units": Decimal(65), "value": Decimal(0), "uncosted": 1}
    assert isinstance(got["VFAN"]["value"], Decimal) and isinstance(got["VFAN"]["units"], Decimal)
    # no order, no totals
    none = stock_plan.plan_facts(replace(m, facts=dict(m.facts, order=[])), m.flows, CATALOG_ROWS, None)
    assert none["order"] == [] and none["supplier_totals"] == []
    # on the plain world: one row, VFAN, the whole of the list in the headline
    plain = build().facts["plan"]
    assert plain["supplier_totals"] == [{"supplier": "VFAN", "items": 1, "units": Decimal(65),
                                         "value": Decimal("162.500"), "uncosted": 0}]


# ── every item ────────────────────────────────────────────────────────────────

@test("all_items on the world: every item with its numbers, sorted by supplier, then status, then name")
def _():
    m = build()
    plan = m.facts["plan"]
    rows = plan["all_items"]
    assert plan["built"] is True and plan["catalog_read"] is True
    assert plan["catalog_items"] == 7 and plan["catalog_only"] == 1
    # the six items and the catalog code no item has; VFAN before WEKOME; inside VFAN, the status order
    assert [r["code"] for r in rows] == ["DS1", "CB2", "SN4", "FO5", "HG3", "CA1", "ZN9"]
    assert [r["status"] for r in rows] == ["Display", "Order now", "New arrival", "No stock, no sale", "No sale 90 d",
                                           "Overstock", "Not in main store yet"]
    assert plan["statuses"] == {"Display": 1, "Not in main store yet": 1, "New arrival": 1, "Order now": 1,
                                "No stock, no sale": 1, "No sale 90 d": 1, "Overstock": 1}
    assert list(plan["statuses"]) == [s for s in stock_plan.STATUS_ORDER if s in plan["statuses"]]
    assert sum(plan["statuses"].values()) == len(rows)
    assert [r["supplier"] for r in rows] == ["VFAN"] * 6 + ["WEKOME"]
    assert [r["supplier_basis"] for r in rows] == ["catalog"] * 7             # nothing but the catalog's brand answers
    assert stock_plan.STATUS_ORDER == ("Display", "Not in main store yet", "Sold out", "Order now", "New arrival",
                                       "No stock, no sale", "No sale 90 d", "Overstock", "OK")
    old = date(2025, 2, 1)
    cols = ("main", "with_vans", "damage", "on_order", "sold_30", "sold_90", "sold_365", "value", "oldest_days",
            "last_sale", "last_receipt", "rank", "category", "active", "hidden", "name")
    want = {
        # 394 on hand; the sellable pool holds 384 (10 units came back from a van that never held them);
        # two a day in the van for 408 days: 60 / 180 / 730 in 30 / 90 / 365 days; the old receipt is the oldest unit
        "CA1": (394, -10, 0, 0, 60, 180, 730, Decimal("157.6"), (D - old).days, D, date(2026, 1, 10), 1,
                "Cables", True, False, NAMES["A"]),
        # 20 on hand and 35 in the second van; 1 a day since 30 Jan and 30 in June (365 days still holds them)
        "CB2": (20, 35, 0, 0, 30, 45, 75, Decimal("50.0"), (D - date(2026, 1, 30)).days, D, date(2026, 1, 30), 3,
                "Chargers", True, False, NAMES["B"]),
        "HG3": (50, 0, 0, 0, 0, 0, 0, Decimal("40.0"), (D - old).days, None, old, None, "Holders", True, False, NAMES["C"]),
        "SN4": (36, 0, 0, 0, 4, 4, 4, Decimal("115.2"), 14, date(2026, 3, 10), date(2026, 3, 1), None,
                "Speakers", True, False, NAMES["N"]),
        # out since September: last sold on 16 Sep, 2 a day from 16 Mar to 16 Sep (185 days) inside the 365
        "FO5": (0, 0, 0, 0, 0, 0, 370, Decimal(0), None, date(2025, 9, 16), date(2025, 3, 1), 2, "Fans", True, False,
                NAMES["F"]),
        "DS1": (5, 0, 0, 0, 0, 0, 0, Decimal("50.0"), (D - old).days, None, old, None, "Displays", True, True,
                NAMES["DISP"]),
    }
    for code, expected in want.items():
        r = row(plan, code)
        assert tuple(r[c] for c in cols) == expected, (code, tuple(r[c] for c in cols))
        assert r["item"] is not None and r["item"].code == code and r["catalog_only"] is False and r["code"] == code
        for c in ("main", "with_vans", "damage", "on_order", "sold_30", "sold_90", "sold_365"):
            assert isinstance(r[c], Decimal), (code, c)                      # Decimals throughout
        assert r["value"] is None or isinstance(r["value"], Decimal)
    # the cover: units on hand over the daily rate; none without a rate (a display item, no sale in 90 days)
    assert row(plan, "CA1")["cover"] == 197 and row(plan, "CB2")["cover"] == 20
    assert abs(row(plan, "SN4")["cover"] - 270) < Decimal("1e-15")           # 36 over 4/30
    assert [row(plan, c)["cover"] for c in ("HG3", "FO5", "DS1", "ZN9")] == [None] * 4
    rates = m.facts["fast"]["rates"]
    assert row(plan, "CA1")["rate"] is rates[L("A")] and row(plan, "CB2")["rate"].rate == 1
    assert row(plan, "DS1")["rate"] is None and row(plan, "ZN9")["rate"] is None     # a display item has no rate
    assert row(plan, "FO5")["rate"].rate == 0 and row(plan, "HG3")["rate"].rate == 0
    # the catalog code no item has
    z = row(plan, "ZN9")
    assert z["item"] is None and z["catalog_only"] is True and z["name"] == "ZZ-NEW Speaker Upcoming"
    assert (z["category"], z["supplier"], z["status"], z["active"], z["hidden"]) == (
        "Speakers", "WEKOME", "Not in main store yet", True, False)
    counts = ("main", "with_vans", "damage", "on_order", "sold_30", "sold_90", "sold_365")
    assert [z[c] for c in counts] == [0] * 7 and all(isinstance(z[c], Decimal) for c in counts)
    assert [z[c] for c in ("rate", "cover", "value", "oldest_days", "last_sale", "last_receipt", "rank")] == [None] * 7


@test("the age of the oldest unit: on its supplier receipt; none when nothing is on hand or no receipt explains it")
def _():
    # 7 units come back from a van to ITEM-C, which no supplier receipt explains (its 50 are on the one receipt)
    b = world()
    b.back(L("C"), date(2026, 3, 2), 7)
    m = build(World(book=b, snapshot=snapshot_rows({"C": 57})))
    plan = m.facts["plan"]
    c = row(plan, "HG3")
    assert c["main"] == 57 and c["oldest_days"] is None and c["with_vans"] == -7       # the oldest unit has no receipt
    assert row(plan, "CA1")["oldest_days"] == (D - date(2025, 2, 1)).days == 407       # A: 200 on January, 194 on Feb 2025
    assert row(plan, "CB2")["oldest_days"] == (D - date(2026, 1, 30)).days == 44       # B: all 20 on the January receipt
    assert row(plan, "FO5")["oldest_days"] is None and row(plan, "FO5")["main"] == 0   # nothing on hand
    assert row(plan, "SN4")["oldest_days"] == 14


@test("the damage store and the vans: damage_at(d) and the sellable pool beyond the main store, per item")
def _():
    m = build(crowded())
    plan = m.facts["plan"]
    a, n, b = row(plan, "CA1"), row(plan, "SN4"), row(plan, "CB2")
    # 300 of A went to the damage store: the main store 94, the pool 84 (394 - 10 back from a van, less the 300)
    assert (a["main"], a["damage"], a["with_vans"]) == (94, 300, -10)
    assert (n["main"], n["damage"], n["with_vans"]) == (6, 30, 0)
    assert (b["main"], b["damage"], b["with_vans"]) == (20, 0, 35)                     # the second van holds 35
    fl = m.flows
    assert a["with_vans"] == fl.get(L("A")).sellable_at(D) - a["main"] and a["damage"] == fl.get(L("A")).damage_at(D)
    assert row(plan, "DS1")["damage"] == 0 and row(plan, "HG3")["with_vans"] == 0
    # ITEM-N is a new arrival, but it has to be ordered: it shows as that
    assert row(plan, "CA1")["status"] == "Order now" and row(plan, "SN4")["status"] == "Order now"
    assert plan["statuses"]["Order now"] == 3 and "New arrival" not in plan["statuses"]
    # within a supplier: the status order, then the name (ITEM-A, ITEM-B and ITEM-N are all Order now)
    assert [r["code"] for r in plan["all_items"]] == ["DS1", "CA1", "CB2", "SN4", "FO5", "HG3", "ZN9"]


@test("the statuses the plain world lacks: Sold out, OK, and the rule's precedence; Overstock begins above 180 days")
def _():
    # B sold out (45 sold in the 44 days it was in stock), A at exactly 180 days, N a new arrival with nothing left
    m = build(damaged(B=20, A=34, N=36))
    plan = m.facts["plan"]
    got = {r["code"]: r["status"] for r in plan["all_items"]}
    assert got == {"CB2": "Sold out", "SN4": "Sold out", "CA1": "OK", "FO5": "No stock, no sale",
                   "HG3": "No sale 90 d", "DS1": "Display", "ZN9": "Not in main store yet"}, got
    b, a = row(plan, "CB2"), row(plan, "CA1")
    assert b["main"] == 0 and b["cover"] == 0 and b["rate"].rate > 0 and b["value"] == 0   # sold out, with demand: cover 0
    assert a["main"] == 360 and a["cover"] == 180 and a["rate"].rate == 2                  # not above 180: OK
    # Sold out outranks New arrival and Order now, though both items are on the order list as well
    listed = {x["item"].code for x in m.facts["order"]}
    assert listed == {"CB2", "SN4"} and plan["statuses"]["Sold out"] == 2 and "New arrival" not in plan["statuses"]
    assert row(plan, "SN4")["cover"] == 0 and stock.is_new(next(it for it in m.items if it.code == "SN4"), D)
    assert [r["code"] for r in plan["all_items"]] == ["DS1", "CB2", "SN4", "FO5", "HG3", "CA1", "ZN9"]
    assert [r["status"] for r in plan["all_items"]][:3] == ["Display", "Sold out", "Sold out"]
    # one more day of cover than 180 is Overstock
    plan = build(damaged(A=32)).facts["plan"]
    a = row(plan, "CA1")
    assert (a["main"], a["cover"], a["status"]) == (362, 181, "Overstock")
    # and every status of the rule has been seen across the worlds
    seen = set(build().facts["plan"]["statuses"]) | set(build(damaged(B=20, A=34)).facts["plan"]["statuses"])
    assert seen == set(stock_plan.STATUS_ORDER), set(stock_plan.STATUS_ORDER) - seen


@test("the status rule on items made by hand: the first that holds; a new arrival ranks below Sold out and Order now")
def _():
    young, aged = D - timedelta(days=10), D - timedelta(days=200)

    def status(on_hand, rate, first=aged, listed=False, cover=None, display=False):
        it = stock.Item(ledger_name="x", name="x", code="x", display=display, on_hand=Decimal(on_hand),
                        receipts=[(first, "R1", Decimal(5), Decimal(5))])
        r = None if rate is None else stock.Rate(sold=Decimal(rate) * 90, days_in_stock=90, rate=Decimal(rate))
        return stock_plan._status(it, r, cover, {"x"} if listed else set(), D, stock)

    table = [
        ("a display item, though new, sold out and on the list", dict(on_hand=0, rate=1, first=young, listed=True,
                                                                      display=True), "Display"),
        ("new, none on hand, selling", dict(on_hand=0, rate=1, first=young), "Sold out"),
        ("new, sold out and on the list", dict(on_hand=0, rate=1, first=young, listed=True), "Sold out"),
        ("new, on the list", dict(on_hand=5, rate=1, first=young, listed=True, cover=Decimal(5)), "Order now"),
        ("new, nothing to do", dict(on_hand=5, rate=1, first=young, cover=Decimal(5)), "New arrival"),
        ("new, none on hand, nothing sold yet", dict(on_hand=0, rate=0, first=young), "New arrival"),
        ("new, on hand, nothing sold yet", dict(on_hand=5, rate=0, first=young), "New arrival"),
        ("new, no rate at all", dict(on_hand=5, rate=None, first=young), "New arrival"),
        ("old, none on hand, selling", dict(on_hand=0, rate=1), "Sold out"),
        ("old, below nought, selling", dict(on_hand=-3, rate=1), "Sold out"),
        ("old, on the list", dict(on_hand=5, rate=1, listed=True, cover=Decimal(5)), "Order now"),
        ("old, none on hand, no sale", dict(on_hand=0, rate=0), "No stock, no sale"),
        ("old, none on hand, no rate at all", dict(on_hand=0, rate=None), "No stock, no sale"),
        ("old, on hand, no sale", dict(on_hand=5, rate=0), "No sale 90 d"),
        ("old, 181 days of cover", dict(on_hand=181, rate=1, cover=Decimal(181)), "Overstock"),
        ("old, 180 days of cover", dict(on_hand=180, rate=1, cover=Decimal(180)), "OK"),
        ("old, selling, not on the list", dict(on_hand=5, rate=1, cover=Decimal(5)), "OK"),
    ]
    for what, kw, want in table:
        assert status(**kw) == want, (what, status(**kw))
    assert stock.is_new(stock.Item(ledger_name="n", name="n", code="n", receipts=[(young, "R", Decimal(1), Decimal(1))]), D)
    assert not stock.is_new(stock.Item(ledger_name="o", name="o", code="o", receipts=[(aged, "R", Decimal(1), Decimal(1))]), D)


@test("goods on order show on the item and still count for the status; the order list is stock's own")
def _():
    with data_root():
        promise = {"id": 1, "kind": "promise", "ref": "p1", "item_code": "CB2", "qty": 30, "event_date": "2026-03-01"}
        paths.write_text(stock.kpi_log_file(), json.dumps(promise) + "\n")
        with standing_in(World()):
            m = stock.build(stock.gather(), stock.settings())
    plan = m.facts["plan"]
    b = row(plan, "CB2")
    assert b["on_order"] == 30 and b["status"] == "Order now" and b["cover"] == 20     # cover is of the units on hand
    assert plan["order"][0]["qty"] == m.facts["order"][0]["qty"] == 35               # 85 - 20 - 30
    assert row(plan, "CA1")["on_order"] == 0 and row(plan, "ZN9")["on_order"] == 0


# ── the screens and the files ─────────────────────────────────────────────────

@test("the reorder view shows each item's supplier; the facts file of the day holds the plan")
def _():
    with data_root() as root:
        out = run(World(), "reorder")
        # the plan in the facts file written beside the screen (every view writes it)
        facts_file = next(Path(f) for f in out["files"] if f.endswith("stock-reorder.json"))
        assert facts_file.parent == root / "exports" / "ops" / D.isoformat()
        facts = json.loads(facts_file.read_text(encoding="utf-8"))
    text = out["text"]
    header = next(ln for ln in text.splitlines() if "Supplier" in ln and "Item" in ln and "Code" in ln)
    line = next(ln for ln in text.splitlines() if "ITEM-B Charger Beta" in ln and "CB2" in ln)
    assert header.index("Code") < header.index("Supplier") < header.index("On hand")
    assert "VFAN" in line and "Other" not in line
    # the vendor of the newest purchase order outranks the catalog's brand; config's word outranks both
    with data_root():
        text = run(World(po_vendor=[("cb2", "Shenzhen Vendor")]), "reorder")["text"]
    line = next(ln for ln in text.splitlines() if "ITEM-B Charger Beta" in ln and "CB2" in ln)
    assert "Shenzhen Vendor" in line and "VFAN" not in line
    with data_root({"stock.suppliers": {"CB2": "Local Trader"}}):
        text = run(World(po_vendor=[("cb2", "Shenzhen Vendor")]), "reorder")["text"]
    line = next(ln for ln in text.splitlines() if "ITEM-B Charger Beta" in ln and "CB2" in ln)
    assert "Local Trader" in line and "VFAN" not in line and "Shenzhen Vendor" not in line
    plan = facts["plan"]
    assert "error" not in plan and plan["built"] is True and plan["catalog_read"] is True
    assert plan["catalog_items"] == 7 and plan["catalog_only"] == 1
    assert plan["statuses"]["Overstock"] == 1 and sum(plan["statuses"].values()) == 7 == len(plan["all_items"])
    x = plan["order"][0]
    assert (x["item"], x["code"], x["supplier"], Decimal(x["qty"]), Decimal(x["cost_bhd"])) == (
        NAMES["B"], "CB2", "VFAN", 65, Decimal("162.5"))
    assert Decimal(x["rate"]) == 1 and Decimal(x["sold"]) == 45 and x["days_in_stock"] == 45 and Decimal(x["cover"]) == 20
    assert plan["supplier_totals"] == [{"supplier": "VFAN", "items": 1, "units": "65", "value": "162.500", "uncosted": 0}]
    first, last = plan["all_items"][0], plan["all_items"][-1]
    assert (first["item"], first["code"], first["status"], first["rate"], first["cover"]) == (
        NAMES["DISP"], "DS1", "Display", None, None)
    assert (last["item"], last["code"], last["status"], last["catalog_only"]) == (
        "ZZ-NEW Speaker Upcoming", "ZN9", "Not in main store yet", True)
    assert (first["supplier"], first["supplier_basis"], last["supplier"], last["supplier_basis"]) == (
        "VFAN", "catalog", "WEKOME", "catalog")
    assert plan["suppliers"][L("B")] == {"name": "VFAN", "basis": "catalog"}


@test("facts_json: items by name and code, a Rate by its figures, nothing an encoder cannot write")
def _():
    m = build(crowded())
    plan = m.facts["plan"]
    assert holds(plan, (stock.Item,)) and holds(plan, (stock.Rate,))                 # the plan holds the objects ...
    js = stock_plan.facts_json(plan)
    assert not holds(js, (stock.Item, stock.Rate))                                   # ... its JSON form does not
    text = json.dumps(stock._jsonable(js))                                           # as stock.facts_out writes it
    back = json.loads(text)
    assert back["built"] is True and back["catalog_read"] is True and back["catalog_only"] == 1
    assert set(back) == {"built", "suppliers", "order", "supplier_totals", "all_items", "statuses", "catalog_items",
                         "catalog_only", "catalog_read"}
    assert [(x["item"], x["code"], x["supplier"]) for x in back["order"]] == [
        (NAMES[k], CODES[k], "VFAN") for k in ("B", "N", "A")]
    o = back["order"][1]
    assert (o["on_hand"], o["on_order"], o["qty"], o["cost_bhd"]) == ("6", "0", "6", "19.200") and o["cost_source"] == "mrn"
    assert Decimal(o["rate"]) == Decimal(4) / 30 and Decimal(o["sold"]) == 4 and o["days_in_stock"] == 15
    rows = {r["code"]: r for r in back["all_items"]}
    assert len(rows) == len(plan["all_items"]) == 7
    assert rows["CA1"]["item"] == NAMES["A"] == rows["CA1"]["name"] and rows["CA1"]["damage"] == "300"
    assert rows["CA1"]["last_sale"] == D.isoformat() and rows["CA1"]["rank"] == 1 and rows["CA1"]["status"] == "Order now"
    assert rows["CA1"]["supplier"] == "VFAN" and rows["CA1"]["supplier_basis"] == "catalog"
    assert rows["ZN9"]["supplier"] == "WEKOME" and rows["ZN9"]["supplier_basis"] == "catalog"
    assert Decimal(rows["CA1"]["rate"]) == 2 and Decimal(rows["CA1"]["sold"]) == 180 and rows["CA1"]["days_in_stock"] == 90
    assert rows["DS1"]["rate"] is None and rows["DS1"]["sold"] is None and rows["DS1"]["days_in_stock"] is None
    assert rows["ZN9"]["item"] == "ZZ-NEW Speaker Upcoming" and rows["ZN9"]["catalog_only"] is True
    assert back["statuses"] == plan["statuses"] and back["suppliers"][L("A")] == {"name": "VFAN", "basis": "catalog"}
    # a facts dict that holds nothing (a plan that could not be worked out) still gives the same shape
    empty = stock_plan.facts_json({})
    assert empty["all_items"] == [] and empty["order"] == [] and empty["statuses"] == {} and empty["built"] is True
    assert empty["catalog_read"] is False and json.dumps(stock._jsonable(empty))


@test("all_items' vans: the units the order position counts beside the main store; with_vans stays the pool's own "
      "(the v2 file's column); nought for a display item, a catalog code, or nothing counted")
def _():
    with data_root(drop=DEFAULT_POSITION):
        m, _inp = model_of(moving())
    plan = m.facts["plan"]
    vans = {r["code"]: r["vans"] for r in plan["all_items"]}
    # ITEM-B 35 in the second van; ITEM-C 5 in the outlet and 8 in grace; ITEM-N 2 received and 2 in grace; ITEM-A's
    # van at -10 reads nought; the display item and the catalog code count nothing
    assert vans == {"CA1": 0, "CB2": 35, "HG3": 13, "SN4": 4, "FO5": 0, "DS1": 0, "ZN9": 0}, vans
    assert all(isinstance(v, Decimal) for v in vans.values())
    assert {c: vans[c] for c in vans if c != "ZN9"} == {
        CODES[k]: (m.facts["position"]["items"].get(L(k)) or {}).get("counted", 0) for k in NAMES}
    # the pool beside the main store, information only: ITEM-C's 4 past their deadline are still in it (17 = 5 + 8 +
    # 4), ITEM-A's van return below nought (-10); the order position counts neither
    assert (row(plan, "HG3")["with_vans"], row(plan, "CA1")["with_vans"]) == (17, -10)
    assert plan["order"][0]["beside"] == m.facts["order"][0]["beside"] == 35
    js = stock_plan.facts_json(plan)
    assert js["order"][0]["beside"] == 35 and {r["code"]: r["vans"] for r in js["all_items"]} == vans
    # the main store only: nothing is counted beside it, every row says nought
    with data_root():
        m0, _inp = model_of(moving())
    assert {r["vans"] for r in m0.facts["plan"]["all_items"]} == {0} and m0.facts["plan"]["order"][0]["beside"] == 0
    # a model with no position at all (as a hand-made one): nought everywhere
    bare = stock_plan.plan_facts(replace(m0, facts={k: v for k, v in m0.facts.items() if k != "position"}), m0.flows,
                                 CATALOG_ROWS, None)
    assert {r["vans"] for r in bare["all_items"]} == {0}


@test("the module is pure: no client, no writer, and it imports stock only when it runs (stock imports it)")
def _():
    src = (ROOT / "scripts" / "ops" / "stock_plan.py").read_text(encoding="utf-8")
    for word in ("import psycopg", "import httpx", "supabase", "subprocess", "yq_load_go", ".table(", "insert", "upsert",
                 "open(", "write_text"):
        assert word not in src, word
    assert not re.search(r"^(from scripts\.ops import .*\bstock\b|import scripts\.ops\.stock\b|from scripts\.ops\.stock\b)",
                         src, re.M), "a top-level import of stock would be circular"
    assert "from scripts.ops import stock" in src                                    # ... it is done inside a function
    assert stock.stock_plan is stock_plan                                            # the module stock calls is this one


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
