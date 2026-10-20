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
    {"suppliers": {ledger name: {"name": str, "basis": "config" | "catalog" | "purchase order" | "none"}},
     "order": [order row of facts["order"] + {"supplier": str}],      # by supplier, then most urgent first
     "supplier_totals": [{"supplier", "items", "units", "value", "uncosted"}],
     "all_items": [{"item": Item | None, "code", "name", "category", "supplier", "status",
                    "main", "with_vans", "damage", "on_order", "sold_30", "sold_90", "sold_365",
                    "rate": Rate | None, "cover": Decimal | None, "value": Decimal | None,
                    "oldest_days": int | None, "last_sale", "last_receipt", "rank",
                    "active": bool | None, "hidden": bool | None, "catalog_only": bool}],
     "statuses": {status: count}, "catalog_items": int, "catalog_only": int, "catalog_read": bool}
      Status, the first that holds: Display / Not in main store yet (catalog only) / New arrival / Sold out
      (none on hand, sold in 90 days) / Order now (on the order list) / No stock, no sale / No sale 90 d /
      Overstock (cover over 180 days) / OK.
  supplier_of(facts, ledger_name) -> str
  facts_json(facts) -> dict
"""
from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

Q6 = Decimal("0.000001")


def round6(x) -> Decimal:
    return Decimal(x).quantize(Q6, rounding=ROUND_HALF_UP)


def units_up(x) -> Decimal:
    v = round6(x)
    return v.to_integral_value(rounding=ROUND_CEILING) if v > 0 else Decimal(0)


def plan_facts(model, fl, catalog, po_vendor) -> dict:
    """Not built yet (package P4): every item's supplier is 'Other'."""
    suppliers = {it.ledger_name: {"name": "Other", "basis": "none"} for it in model.items}
    order = [dict(x, supplier="Other") for x in model.facts["order"]]
    return {"suppliers": suppliers, "order": order, "supplier_totals": [], "all_items": [], "statuses": {},
            "catalog_items": 0, "catalog_only": 0, "catalog_read": catalog is not None, "built": False}


def supplier_of(facts: dict, ledger_name: str) -> str:
    return ((facts.get("suppliers") or {}).get(ledger_name) or {}).get("name") or "Other"


def facts_json(facts: dict) -> dict:
    return {"built": facts.get("built", True), "statuses": facts.get("statuses", {})}
