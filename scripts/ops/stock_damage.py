"""Damage stock: what the damage store holds, how much is sent to it for every 100 units sold and
whether that is rising, what was sent and never booked in, and the items it happens to most.
READ-ONLY and pure: scripts.ops.stock gathers the reads and calls damage_facts(model, fl); this module
works the figures out and words them. Every line it returns passes fmt.careful (stock.run guards them
again); it never names a person or a van: a line marked "to check" says what differs, never who.

CONTRACT (scripts.ops.stock and the workbook rely on it)
  damage_facts(model, fl) -> dict
      model  the stock Model (model.items, model.d, model.cfg.damage, model.facts["fast"]["rates"])
      fl     stockflow.Flows for the same items
    {"set_up": bool,                 # False when config 'warehouses.damage' names no store
     "store": str | None,            # the damage store's one name
     "found": bool,                  # the ledger moved stock in it at least once
     "now": {"units", "value", "uncosted_units", "items", "share_of_main"},   # Decimal; share a fraction or None
     "months": [{"month": date (the 1st), "sold", "sent", "sent_main", "sent_elsewhere", "booked",
                 "per100": Decimal | None, "partial": bool}],   # the last 12 months, oldest first; the newest
                                                                 # is the month of D (partial when D is no month end)
     "trend": {"word": "rising" | "falling" | "steady" | None, "this": Decimal | None,
               "prior3": Decimal | None, "why_none": str | None},
     "not_booked": [{"item": Item, "units": Decimal, "last_sent": date}],   # sent up to D-7, not booked by D
     "not_booked_units": Decimal,
     "nothing_left_since": date | None,   # the first booking, while nothing ever went out of the store
     "left_units": Decimal,               # units that went out of the store, ever
     "by_item": [{"item": Item, "in_store": Decimal, "value": Decimal | None, "sent_90": Decimal,
                  "sold_90": Decimal, "per100_90": Decimal | None, "quality_check": bool}],
     "company_per100_90": Decimal | None}
  headline_lines(facts) -> list[str]        the DAMAGE block of the plain /yq-stock screen (title + 3 lines)
  damage_lines(facts, model) -> list[str]   the full /yq-stock damage view (stock.py adds the footer)
  item_lines(facts, ledger_name) -> list[str]   0 to 2 lines for /yq-stock <code>
  facts_json(facts) -> dict                 the facts saved beside the screen (items by name and code)
"""
from __future__ import annotations


def damage_facts(model, fl) -> dict:
    """Not built yet (package P2): the damage store is not set up."""
    return {"set_up": False, "store": None, "found": False}


def headline_lines(facts: dict) -> list[str]:
    return []


def damage_lines(facts: dict, model) -> list[str]:
    return ["DAMAGE  not built yet"]


def item_lines(facts: dict, ledger_name: str) -> list[str]:
    return []


def facts_json(facts: dict) -> dict:
    return {k: v for k, v in facts.items() if k in ("set_up", "store", "found")}
