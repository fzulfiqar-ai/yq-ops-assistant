"""Which row of the Categories sheet an item sits in: the rule of the Sep 2026 workbook (its Notes sheet).

  1. The item code is the first word of the Focus item name; its longest alphabetic prefix (the
     letters it starts with: GN of GN-08C, UK of UK12, TB of TB-D3) is looked up in CODE_RULE. TB is
     its own row, Bundle Sets (a display group), and is never read as T for TWS earbuds.
  2. A code the rule does not know takes the category of its card: the Upcoming card (the
     "Coming soon" list) or the catalog card, through the name table in config
     (report.category_names: {card category or item code: Categories row}). A code key wins over a
     card key, so one card category that holds two kinds of product (WEKOME's "Wireless Audio": a
     neckband and TWS earbuds) can still put each code in its own row.
  3. An item none of whose lines in the two months carries a value is not an accessory sale: it goes
     to the handset row (Notes "Handset category"), which the workbook leaves out of units, invoice,
     account and product counts (the Sep file: ZTE handsets issued through the roadshow at no value).
  4. Otherwise Other.

The words of the Notes paragraph are generated from the rule in force (rule_paragraph()).
"""
from __future__ import annotations

import re

HANDSET = "Mobile Handsets"
OTHER = "Other"
BUNDLES = "Bundle Sets"

# longest alphabetic code prefix -> Categories row
CODE_RULE: dict[str, str] = {
    "T": "TWS Earbuds", "M": "Wired Earphones", "X": "Cables", "P": "Cables", "L": "Cables",
    "UK": "Wall Chargers", "C": "Car Chargers", "F": "Power Banks", "G": "Power Banks", "GN": "Power Banks",
    "K": "Power Banks", "BE": "Headphones & Neckbands", "BS": "Bluetooth Speakers", "H": "Phone Holders",
    "TB": BUNDLES,
}
# how each row is named inside a sentence of the Notes sheet
PROSE: dict[str, str] = {
    "TWS Earbuds": "TWS earbuds", "Wired Earphones": "wired earphones", "Cables": "cables",
    "Wall Chargers": "wall chargers", "Car Chargers": "car chargers", "Power Banks": "power banks",
    "Headphones & Neckbands": "headphones and neckbands", "Bluetooth Speakers": "speakers",
    "Phone Holders": "phone holders", BUNDLES: "bundle sets (a display group)",
}
_LEAD = re.compile(r"^[A-Za-z]+")


def item_code(item_name: str) -> str:
    """The item code: the first word of the Focus item name."""
    parts = str(item_name or "").split()
    return parts[0] if parts else ""


def code_prefix(code: str) -> str:
    """The longest alphabetic prefix of a code, upper case ('' for a code that starts with a digit)."""
    m = _LEAD.match(str(code or ""))
    return m.group(0).upper() if m else ""


def by_code(code: str) -> str | None:
    return CODE_RULE.get(code_prefix(code))


def resolve(code: str, cards: list[str] | tuple = (), names: dict | None = None,
            no_value: bool = False) -> tuple[str, str]:
    """(Categories row, how it was found): 'code', 'name table', 'no value' or 'none'."""
    hit = by_code(code)
    if hit:
        return hit, "code"
    table = {str(k).strip().casefold(): str(v).strip() for k, v in (names or {}).items()
             if str(k).strip() and str(v).strip()}
    for key in [code] + [c for c in cards if c]:
        row = table.get(str(key).strip().casefold())
        if row:
            return row, "name table"
    if no_value:
        return HANDSET, "no value"
    return OTHER, "none"


def _join(words: list[str]) -> str:
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def rule_paragraph(names: dict | None = None) -> str:
    """The Notes sentence on the category rule, in the rule's own order of rows."""
    rows: dict[str, list[str]] = {}
    for prefix, row in CODE_RULE.items():
        rows.setdefault(row, []).append(prefix)
    parts = [f"{_join(prefixes)} = {PROSE.get(row, row.lower())}" for row, prefixes in rows.items()]
    text = ("Product category comes from the item code prefix: " + ", ".join(parts) + ". "
            "The category of each item is shown on the Products sheet.")
    extra = {k: v for k, v in (names or {}).items() if str(k).strip() and str(v).strip()}
    if extra:
        pairs = "; ".join(f"{k} = {v}" for k, v in sorted(extra.items(), key=lambda kv: str(kv[0]).lower()))
        text += (" A code the rule does not know takes the category of its Upcoming or catalog card: "
                 + pairs + ".")
    return text
