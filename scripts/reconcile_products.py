"""Phase 0 reconciliation: map the long Focus item strings to product SKUs.

Sales / stock / profitability reports refer to items by long descriptive names; the price books
carry the clean Item Code + Item Name. This builds `product_aliases` (alias_text -> product_id)
and prints a MATCH-RATE REPORT so Furqan can see and fix anything unmatched before trusting
totals. Runs offline on data/clean/ by default; add --push to upsert aliases into Supabase.

Match strategy (best-effort, transparent; match_alias() returns the method and a confidence):
  1. exact normalized item_name == product.item_name                       (exact_name, 1.0)
  2. the LONGEST product code that opens the string on a token boundary     (code, 0.9)
     -- 'X24 CC 1Mtr Cable …' is X24 CC 1Mtr, not X24 CC
  3. the normalized product name is a prefix of the string                 (name_prefix, 0.7)
  4. leading token shares the code family before '-' (X02 -> first X02-*)  (code_family, 0.6)

Rule 3 used to compare only the first 40 characters of either side, so a string that shared a
40-character opening with a longer product name -- or a short string that any product name
happened to start with -- was assigned that product. A code is now proposed only when the whole
normalized product name is a prefix of the string (24-Sep-2026, alias backfill D2).

Usage:  python scripts/reconcile_products.py [--push]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CLEAN = ROOT / "data" / "clean"
ALIAS_OUT = CLEAN / "product_aliases.csv"
UNMATCHED_OUT = CLEAN / "unmatched_items.csv"

CONFIDENCE = {"exact_name": 1.0, "code": 0.9, "name_prefix": 0.7, "code_family": 0.6}


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower())


def lead_code(s: str) -> str:
    tok = str(s).strip().split(" ")[0].upper()
    return tok


def build_index(products) -> dict:
    """Lookup tables for match_alias() from (sku_code, item_name) pairs (item_name may be None)."""
    by_name: dict[str, str] = {}
    by_code: dict[str, str] = {}
    code_prefix: dict[str, str] = {}
    codes: list[tuple[str, str]] = []
    for sku, name in products:
        if sku is None or (isinstance(sku, float) and pd.isna(sku)):
            continue
        sku = str(sku).strip()
        if not sku:
            continue
        if name is not None and not (isinstance(name, float) and pd.isna(name)) and norm(name):
            by_name.setdefault(norm(name), sku)
        by_code.setdefault(sku.upper(), sku)
        code_prefix.setdefault(sku.split("-")[0].upper(), sku)
        codes.append((norm(sku), sku))
    codes.sort(key=lambda t: -len(t[0]))            # longest code first: the most specific match wins
    names = sorted(by_name.items(), key=lambda t: -len(t[0]))
    return {"by_name": by_name, "by_code": by_code, "code_prefix": code_prefix, "codes": codes, "names": names}


def match_alias(alias: str, idx: dict) -> tuple[str | None, str, float]:
    """(sku_code, method, confidence) for one item string; (None, 'unmatched', 0.0) when nothing fits."""
    n = norm(alias)
    if not n:
        return None, "unmatched", 0.0
    sku = idx["by_name"].get(n)
    if sku:
        return sku, "exact_name", CONFIDENCE["exact_name"]
    for cn, sku in idx["codes"]:
        if n == cn or n.startswith(cn + " ") or n.startswith(cn + "-") or n.startswith(cn + "/"):
            return sku, "code", CONFIDENCE["code"]
    for pn, sku in idx["names"]:
        if n.startswith(pn):                          # the WHOLE product name opens the string
            return sku, "name_prefix", CONFIDENCE["name_prefix"]
    lc = lead_code(alias)
    sku = idx["code_prefix"].get(lc) or idx["code_prefix"].get(lc.split("-")[0])
    if sku:
        return sku, "code_family", CONFIDENCE["code_family"]
    return None, "unmatched", 0.0


def main(argv: list[str]) -> int:
    sp = CLEAN / "selling_prices.csv"
    if not sp.exists():
        print("ERROR: run scripts/ingest.py first (need data/clean/selling_prices.csv).")
        return 1

    products = pd.read_csv(sp, dtype=object).dropna(subset=["sku_code"]).drop_duplicates("sku_code")
    idx = build_index((r.sku_code, r.item_name) for r in products.itertuples())

    # gather distinct item strings from the fact reports
    aliases: set[str] = set()
    for fname, col in [("order_lines", "item_name"),
                       ("stock_movements", "item_name"),
                       ("product_profitability", "item_name")]:
        p = CLEAN / f"{fname}.csv"
        if p.exists():
            df = pd.read_csv(p, dtype=object)
            aliases |= {x for x in df[col].dropna().tolist()}

    matched: list[tuple[str, str]] = []
    unmatched: list[str] = []
    methods: dict[str, int] = {}
    for a in sorted(aliases):
        sku, method, _conf = match_alias(a, idx)
        if sku:
            matched.append((a, sku))
            methods[method] = methods.get(method, 0) + 1
        else:
            unmatched.append(a)

    total = len(aliases) or 1
    rate = len(matched) / total
    pd.DataFrame(matched, columns=["alias_text", "sku_code"]).to_csv(ALIAS_OUT, index=False)
    pd.DataFrame({"alias_text": unmatched}).to_csv(UNMATCHED_OUT, index=False)

    print("=" * 60)
    print(f"Distinct item strings : {len(aliases)}")
    print(f"Matched to a SKU      : {len(matched)} ({rate:.1%})  "
          + ", ".join(f"{m} {n}" for m, n in sorted(methods.items(), key=lambda t: -CONFIDENCE.get(t[0], 0))))
    print(f"Unmatched             : {len(unmatched)}  -> review {UNMATCHED_OUT.relative_to(ROOT)}")
    print(f"Aliases written       : {ALIAS_OUT.relative_to(ROOT)}")
    if unmatched[:10]:
        print("  sample unmatched:")
        for u in unmatched[:10]:
            print(f"    - {u[:80]}")
    print("=" * 60)

    if "--push" in argv:
        from app.database import get_client

        client = get_client()
        prod = client.table("products").select("id,sku_code").execute().data or []
        id_by_sku = {r["sku_code"]: r["id"] for r in prod}
        recs = [{"alias_text": a, "product_id": id_by_sku.get(s)} for a, s in matched
                if id_by_sku.get(s)]
        for i in range(0, len(recs), 500):
            client.table("product_aliases").upsert(
                recs[i:i + 500], on_conflict="alias_text").execute()
        print(f"Pushed {len(recs)} aliases to Supabase.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
