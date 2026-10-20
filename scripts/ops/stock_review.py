"""Smarter ageing: every old, unsold or price-cut item, its pace and how soon it clears, whether its
price cut helped, and what the sales team must sell of it each week. The owner's rules (20-Oct-2026,
his price-cut review): the clear target is config 'stock.clear_target_days' (the plan's default 90);
the only lever is the sales team, so no screen here ever suggests a further price cut or a bundle.
READ-ONLY and pure: scripts.ops.stock gathers the reads and calls review_facts(model, fl, cuts).
Every line it returns passes fmt.careful; no screen line names a salesman (the sellers are for the
owner's own workbook only).

CONTRACT (scripts.ops.stock and the workbook rely on it)
  review_facts(model, fl, cuts) -> dict
      model  the stock Model (items, d, cfg.clear_days, facts["fast"]["rates"], facts["ageing"])
      fl     stockflow.Flows for the same items (sellable_at, main_at, sellers)
      cuts   v_price_change rows of price CUTS, [{code, item_name, changed_on, was, now}] (text), or None
             when the read failed
    {"clear_days": int,
     "rows": [{"item": Item, "sellable": Decimal, "main": Decimal, "over_180": Decimal,
               "value": Decimal | None,                  # sellable x landed cost
               "last_sale": date | None,
               "cut": {"date": date, "was": Decimal, "now": Decimal, "pct": Decimal} | None,
               "pace_basis": "cut" | "30 days", "pace_days": int, "pace_week": Decimal,
               "days_to_clear": Decimal | None,
               "status": "Cleared" | "Not moving" | "On track" | "Slow",
               "before_week": Decimal | None,            # units a week in the 90 days before the cut
               "change": Decimal | None,                 # pace since ÷ pace before (None: nothing sold before)
               "stock_at_cut": Decimal | None, "sold_since_cut": Decimal | None, "pct_sold": Decimal | None,
               "push_week": int | None,                  # units a week that clear it within clear_days
               "action": str,                            # "Push sales team: N a week" | "Keep selling" | ...
               "sellers": [(store, units)]}],            # the 3 stores that sold most in 90 days (owner's file)
     "batches": [{"date": date, "items": int, "counts": {status: n}, "stock_at_cut": Decimal, "sold": Decimal,
                  "pct_sold": Decimal | None, "before_week": Decimal, "since_week": Decimal,
                  "change": Decimal | None}],            # one per cut date in the last 180 days, newest first
     "trend": [{"month_end": date, "over_180_value": Decimal, "value": Decimal, "share": Decimal | None}],
     "counts": {status: n}, "push_units_week": int}
  headline_lines(facts) -> list[str]        the AGEING block of the plain /yq-stock screen (title + 3 lines)
  review_lines(facts, model) -> list[str]   appended to the /yq-stock ageing view
  item_lines(facts, ledger_name) -> list[str]   0 to 2 lines for /yq-stock <code>
  facts_json(facts) -> dict                 the facts saved beside the screen (items by name and code, no seller)
"""
from __future__ import annotations


def review_facts(model, fl, cuts) -> dict:
    """Not built yet (package P3)."""
    return {"clear_days": model.cfg.clear_days, "rows": [], "batches": [], "trend": [], "counts": {},
            "push_units_week": 0, "built": False}


def headline_lines(facts: dict) -> list[str]:
    return []


def review_lines(facts: dict, model) -> list[str]:
    return []


def item_lines(facts: dict, ledger_name: str) -> list[str]:
    return []


def facts_json(facts: dict) -> dict:
    return {"clear_days": facts.get("clear_days"), "built": facts.get("built", True)}
