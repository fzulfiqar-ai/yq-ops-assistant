"""The Stock & Order Plan workbook: one editable file per data date, for the owner to work on and share.
Built from the stock Model's facts (scripts.ops.stock, stock_plan, stock_review, stock_damage) with
the report engine (book.py: every formula carries its cached value; save never overwrites).

CONTRACT (scripts.ops.stock relies on it)
  day_file(model, force=False) -> dict
      {"path": str | None, "made": bool, "made_at": "HH:MM" | None, "error": str | None, "controls": [str]}
      One file per data date under exports/reports/stock/, named "YQ Stock and Order Plan <YYYY-MM-DD>.xlsx":
      when one exists it is not built again (made False, path and its time given) unless force, which takes
      the next free " (2)". A failure (XlsxWriter missing, a formula the evaluator refuses, a name taken) is
      one sentence in "error", never an exception: the screen always prints.
"""
from __future__ import annotations


def day_file(model, force: bool = False) -> dict:
    """Not built yet (package P5)."""
    return {"path": None, "made": False, "made_at": None, "error": None, "controls": [], "built": False}
