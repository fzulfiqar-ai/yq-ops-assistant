"""The order plan behind the workbook: the supplier of every item, the order list grouped by supplier
with its totals, and every item (the catalog and every item that ever moved in the main store) with
its status. READ-ONLY and pure: scripts.ops.stock gathers the reads and calls plan_facts(...).

The order rule itself stays in scripts.ops.stock (facts["order"]): this module never decides a
quantity of its own, it groups and labels. One rounding rule holds wherever a quantity is worked out
again (the workbook): round6() first, then a whole unit up (units_up()), so Python and the file agree
at a whole-number boundary.

CONTRACT (scripts.ops.stock and the workbook rely on it)
  round6(x) -> Decimal                  x to 6 decimals, half away from nought
  units_up(x) -> Decimal                round6, then up to a whole unit (nought or less: 0)
  plan_facts(model, fl, catalog, po_vendor) -> dict
      model      the stock Model (items, d, cfg.suppliers, cfg.supplier_aliases, facts["order"], facts["fast"])
      fl         stockflow.Flows (with vans: sellable_at(d) - the main store; damage_at(d))
      catalog    catalog_items rows [{code, display_name, category, brand, division, is_active, hidden}] or None
      po_vendor  {code: vendor of its newest purchase order} or None
    {"suppliers": {ledger name: {"name": str, "basis": "config" | "purchase order" | "catalog" | "none"}},
     "order": [order row of facts["order"] + {"supplier": str}],      # by supplier, then most urgent first
     "supplier_totals": [{"supplier", "items", "units", "value", "uncosted"}],
     "all_items": [{"item": Item | None, "code", "name", "category", "supplier", "supplier_basis", "status",
                    "main", "with_vans", "vans", "damage", "on_order", "sold_30", "sold_90", "sold_365",
                    "rate": Rate | None, "cover": Decimal | None, "value": Decimal | None,
                    "oldest_days": int | None, "last_sale", "last_receipt", "rank",
                    "active": bool | None, "hidden": bool | None, "catalog_only": bool}],
     "statuses": {status: count}, "catalog_items": int, "catalog_only": int, "catalog_read": bool}
      Status, the first that holds (also the order they sort in): Display / Not in main store yet (catalog
      only) / Sold out (none on hand, sold in 90 days) / Order now (on the order list) / New arrival / No
      stock, no sale / No sale 90 d / Overstock (cover over 180 days) / OK.
  suppliers_of(items, cfg, catalog, po_vendor) -> {ledger name: (supplier, basis)}      the same ladder, for stock
  supplier_of(facts, ledger_name) -> str
  facts_json(facts) -> dict

HOW IT DECIDES
  * The catalog counts only for the accessories: a row whose division is blank or "Accessories" (any
    case); a row of another division (devices) is left out of every figure. A code is a row once.
  * The supplier of an item is the first that answers: config 'stock.suppliers' (basis "config"), the
    vendor of its newest purchase order ("purchase order"), the brand of its catalog row ("catalog"),
    else "Other" ("none"). The brand comes after the order because the catalog gives every catalogued
    item the brand VFAN by default: it says who supplies an item only where nothing better does. A name
    has its spaces collapsed and then passes through config 'stock.supplier_aliases' (the spelling is
    compared folded: case and spaces ignored). A catalog code no item has moved in the main store is
    judged the same way; every row of all_items names the basis it rests on (supplier_basis).
  * A new arrival that has sold out, or has to be ordered, is shown as that (Sold out, Order now): the
    first-received date only labels an item that needs nothing done.
  * The order list is facts["order"] with each row's supplier, by supplier name (alphabetical, "Other"
    last), the urgency order of facts["order"] kept inside a supplier; the totals follow that order.
  * all_items holds every model item (display items too) and every accessory code of the catalog that
    no model item has (catalog_only); main = the main store's units, with_vans = the units the sellable
    pool holds beyond them (vans, outlets, goods on the way: information only, it can be below nought
    where the ledger returns more from a van than was issued to it; the v2 file's column), vans = the
    units the order position counts beside the main store (facts["position"]: the vans, the outlets and
    between them, never below nought; nought for a display item, a catalog code, or nothing counted),
    damage = the damage store's units.
    sold_30/90/365 are the units sold in that many days to the data date, never below nought (as the
    daily rate counts them). cover = the main store's units over the daily rate (None without a rate);
    value = the main store's units at the item's unit cost (None where it has none). oldest_days = the
    age of the oldest unit on hand on its supplier receipt (None when no receipt explains it).
    Sorted by supplier ("Other" last), then by the order of the statuses above (STATUS_ORDER), then by name.
  * An item with no category in the catalog takes the category its code gives by itself
    (scripts.ops.workbook.categories.by_code), else none.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

Q6 = Decimal("0.000001")
ZERO = Decimal(0)

OTHER = "Other"                                    # the supplier of an item nothing names
# The statuses in the order of the rule (the first that holds) and of the sort.
STATUS_ORDER = ("Display", "Not in main store yet", "Sold out", "Order now", "New arrival", "No stock, no sale",
                "No sale 90 d", "Overstock", "OK")
DISPLAY, NOT_YET, SOLD_OUT, ORDER_NOW, NEW, NO_STOCK, NO_SALE, OVERSTOCK, OK = STATUS_ORDER
WINDOWS = (30, 90, 365)                            # sold_30, sold_90, sold_365: that many days to the data date
ACCESSORY_DIVISIONS = ("", "accessories")          # a catalog division (folded) that counts as an accessory


def round6(x) -> Decimal:
    return Decimal(x).quantize(Q6, rounding=ROUND_HALF_UP)


def units_up(x) -> Decimal:
    v = round6(x)
    return v.to_integral_value(rounding=ROUND_CEILING) if v > 0 else Decimal(0)


# ── small helpers ─────────────────────────────────────────────────────────────

def _stock():
    """scripts.ops.stock, imported when used: it imports this module."""
    from scripts.ops import stock
    return stock


def _clean(text) -> str:
    """A name with its spaces collapsed ('' for nothing)."""
    return " ".join(str(text or "").split())


def _flag(x) -> bool | None:
    """A catalog flag as a bool; None where the catalog says nothing."""
    if x is None or x == "":
        return None
    if isinstance(x, str):
        return x.strip().lower() in ("true", "t", "yes", "y", "1")
    return bool(x)


def _category_rule():
    """The category a code gives by itself (the workbook's rule), for an item the catalog names none for."""
    try:
        from scripts.ops.workbook import categories
    except ImportError:
        return lambda code: None
    return categories.by_code


def _supplier_key(name: str) -> tuple:
    """Suppliers go alphabetically, "Other" last."""
    return (name == OTHER, name.casefold(), name)


def _accessories(catalog, code_key) -> dict | None:
    """{code: catalog row} of the catalog's accessories, the first row of a code standing; None when the
    catalog was not read."""
    if catalog is None:
        return None
    out: dict[str, dict] = {}
    for row in catalog:
        key = code_key(row.get("code"))
        if key and _clean(row.get("division")).casefold() in ACCESSORY_DIVISIONS:
            out.setdefault(key, row)
    return out


def _supplier_picker(cfg, book, po_vendor, st):
    """pick(code key) -> (supplier name, basis): config, then the newest purchase order's vendor, then the
    catalog's brand (VFAN for every catalogued item unless set), else Other; each name collapsed and passed
    through the aliases."""
    fold, code_key = st.fold, st.code_key
    aliases = {fold(k): _clean(v) for k, v in (cfg.supplier_aliases or {}).items() if fold(k) and _clean(v)}
    configured = {code_key(k): _clean(v) for k, v in (cfg.suppliers or {}).items() if code_key(k) and _clean(v)}
    ordered = {code_key(k): _clean(v) for k, v in (po_vendor or {}).items() if code_key(k) and _clean(v)}

    def named(text) -> str:
        name = _clean(text)
        return _clean(aliases.get(fold(name), name)) if name else ""

    def pick(key: str) -> tuple[str, str]:
        brand = (book or {}).get(key, {}).get("brand")
        for text, basis in ((configured.get(key), "config"), (ordered.get(key), "purchase order"), (brand, "catalog")):
            name = named(text)
            if name:
                return name, basis
        return OTHER, "none"

    return pick


def _status(it, rate, cover, listed, d, st) -> str:
    """The first status that holds (STATUS_ORDER), for an item of the model."""
    if it.display:
        return DISPLAY
    selling = rate is not None and rate.rate > 0
    if it.on_hand <= 0 and selling:
        return SOLD_OUT                              # a new arrival that has sold out is sold out
    if it.ledger_name in listed:
        return ORDER_NOW                             # ... and one that has to be ordered, to be ordered
    if st.is_new(it, d):
        return NEW
    if it.on_hand <= 0:
        return NO_STOCK                              # nothing on hand, and (Sold out came first) no sale
    if not selling:
        return NO_SALE                               # on hand, no sale in the rate window
    if cover is not None and cover > st.COVER_LIMITS[1]:
        return OVERSTOCK
    return OK


# ── the plan ──────────────────────────────────────────────────────────────────

def suppliers_of(items, cfg, catalog, po_vendor) -> dict[str, tuple[str, str]]:
    """{ledger name: (supplier, basis)} for these items, by the same ladder as plan_facts: the order rule reads
    it first (each supplier has its own lead time and rhythm), before there is a plan to read it from."""
    st = _stock()
    pick = _supplier_picker(cfg, _accessories(catalog, st.code_key), po_vendor, st)
    return {it.ledger_name: pick(st.code_key(it.code)) for it in items}


def plan_facts(model, fl, catalog, po_vendor) -> dict:
    """The supplier of every item, the order list by supplier with its totals, and every item with its status
    (see the module docstring). Nothing is read and nothing is changed here: the model's own rows are copied."""
    st = _stock()
    d = model.d
    rates = (model.facts.get("fast") or {}).get("rates") or {}
    listed = {x["item"].ledger_name for x in model.facts["order"]}
    beside = ((model.facts.get("position") or {}).get("items")) or {}     # what the order position counts
    book = _accessories(catalog, st.code_key)
    pick = _supplier_picker(model.cfg, book, po_vendor, st)
    category_of = _category_rule()

    rows: list[dict] = []
    suppliers: dict[str, dict] = {}
    for it in model.items:
        key = st.code_key(it.code)
        name, basis = pick(key)
        suppliers[it.ledger_name] = {"name": name, "basis": basis}
        cat = book.get(key) if book is not None and key else None
        flow = fl.get(it.ledger_name) if fl is not None else None
        rate = rates.get(it.ledger_name)
        cover = None                                 # no rate (a display item, or no sale in the window): no cover
        if rate is not None and rate.rate > 0:
            cover = st.cover_of(it.on_hand, rate.rate) if it.on_hand > 0 else ZERO
        chunks = st.allocate(it.on_hand, it.receipts) if it.on_hand > 0 else []
        sold = [max(st.sold_between(it, d - timedelta(days=n - 1), d), ZERO) for n in WINDOWS]
        if it.cost is None:
            value = None
        else:
            value = it.on_hand * it.cost if it.on_hand > 0 else ZERO
        category = (_clean(cat.get("category")) if cat is not None else "") or category_of(it.code) or ""
        rows.append({
            "item": it, "code": it.code or "", "name": it.name, "category": category,
            "supplier": name, "supplier_basis": basis, "status": _status(it, rate, cover, listed, d, st),
            "main": it.on_hand,
            "with_vans": (flow.sellable_at(d) - it.on_hand) if flow is not None else ZERO,
            "vans": (beside.get(it.ledger_name) or {}).get("counted", ZERO),
            "damage": flow.damage_at(d) if flow is not None else ZERO,
            "on_order": it.on_order,
            "sold_30": sold[0], "sold_90": sold[1], "sold_365": sold[2],
            "rate": rate, "cover": cover, "value": value,
            "oldest_days": (d - chunks[-1][0]).days if chunks and chunks[-1][0] is not None else None,
            "last_sale": it.last_sale, "last_receipt": it.last_receipt, "rank": it.rank,
            "active": _flag(cat.get("is_active")) if cat is not None else None,
            "hidden": _flag(cat.get("hidden")) if cat is not None else None,
            "catalog_only": False})

    # the catalog's accessories that never moved in the main store: upcoming and not yet received
    moved = {st.code_key(it.code) for it in model.items}
    only = sorted(key for key in (book or {}) if key not in moved)
    for key in only:
        cat = book[key]
        name, basis = pick(key)
        rows.append({
            "item": None, "code": key, "name": _clean(cat.get("display_name")) or key,
            "category": _clean(cat.get("category")) or category_of(key) or "",
            "supplier": name, "supplier_basis": basis, "status": NOT_YET,
            "main": ZERO, "with_vans": ZERO, "vans": ZERO, "damage": ZERO, "on_order": ZERO,
            "sold_30": ZERO, "sold_90": ZERO, "sold_365": ZERO,
            "rate": None, "cover": None, "value": None, "oldest_days": None,
            "last_sale": None, "last_receipt": None, "rank": None,
            "active": _flag(cat.get("is_active")), "hidden": _flag(cat.get("hidden")), "catalog_only": True})

    rows.sort(key=lambda r: (_supplier_key(r["supplier"]), STATUS_ORDER.index(r["status"]), r["name"].casefold(),
                             r["name"], r["code"]))
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    # the order list: by supplier, the urgency order of facts["order"] kept inside one (sorted() is stable)
    order = sorted((dict(x, supplier=suppliers.get(x["item"].ledger_name, {}).get("name", OTHER))
                    for x in model.facts["order"]), key=lambda x: _supplier_key(x["supplier"]))
    totals: dict[str, dict] = {}
    for x in order:
        t = totals.setdefault(x["supplier"], {"supplier": x["supplier"], "items": 0, "units": ZERO, "value": ZERO,
                                              "uncosted": 0})
        t["items"] += 1
        t["units"] += x["qty"]
        if x["cost_bhd"] is None:
            t["uncosted"] += 1
        else:
            t["value"] += x["cost_bhd"]

    return {"suppliers": suppliers, "order": order, "supplier_totals": list(totals.values()), "all_items": rows,
            "statuses": {s: counts[s] for s in STATUS_ORDER if s in counts},
            "catalog_items": len(book) if book is not None else 0, "catalog_only": len(only),
            "catalog_read": catalog is not None, "built": True}


def supplier_of(facts: dict, ledger_name: str) -> str:
    return ((facts.get("suppliers") or {}).get(ledger_name) or {}).get("name") or "Other"


def _rate_cells(rate) -> dict:
    """A Rate as plain figures: its rate, the units it rests on and the days in stock."""
    if rate is None:
        return {"rate": None, "sold": None, "days_in_stock": None}
    return {"rate": rate.rate, "sold": rate.sold, "days_in_stock": rate.days_in_stock}


def facts_json(facts: dict) -> dict:
    """The plan as the day's JSON file holds it: an item by its name and code, a Rate by its figures, no Item or
    Rate object left (Decimals and dates stay; scripts.ops.stock writes them as text)."""
    order = []
    for x in facts.get("order") or []:
        it = x["item"]
        order.append({"item": it.name, "code": it.code, "supplier": x.get("supplier"), "on_hand": it.on_hand,
                      "on_order": it.on_order, "cost": it.cost, "cost_source": it.cost_source,
                      **_rate_cells(x["rate"]), "cover": x["cover"], "qty": x["qty"], "cost_bhd": x["cost_bhd"],
                      "beside": x.get("beside")})
    every = []
    for row in facts.get("all_items") or []:
        out: dict = {}
        for k, v in row.items():
            if k == "item":
                out["item"] = row["name"]
            elif k == "rate":
                out.update(_rate_cells(v))
            else:
                out[k] = v
        every.append(out)
    return {"built": facts.get("built", True),
            "suppliers": {k: dict(v) for k, v in (facts.get("suppliers") or {}).items()},
            "order": order, "supplier_totals": [dict(t) for t in facts.get("supplier_totals") or []],
            "all_items": every, "statuses": dict(facts.get("statuses") or {}),
            "catalog_items": facts.get("catalog_items", 0), "catalog_only": facts.get("catalog_only", 0),
            "catalog_read": facts.get("catalog_read", False)}
