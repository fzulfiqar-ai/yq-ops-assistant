"""Phase 0 reconciliation: map the long Focus item strings to product SKUs.

Sales / stock / profitability reports refer to items by long descriptive names; the price books
carry the clean Item Code + Item Name. This builds `product_aliases` (alias_text -> product_id)
and prints a MATCH-RATE REPORT so Furqan can see and fix anything unmatched before trusting
totals. Runs offline on data/clean/ by default; add --push to upsert aliases into Supabase.

Match strategy (best-effort, transparent):
  1. exact normalized item_name == product.item_name
  2. leading code token (e.g. 'F19', 'X02', 'H09') == product.sku_code (or its prefix)
  3. normalized prefix containment

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


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower())


def lead_code(s: str) -> str:
    tok = str(s).strip().split(" ")[0].upper()
    return tok


def main(argv: list[str]) -> int:
    sp = CLEAN / "selling_prices.csv"
    if not sp.exists():
        print("ERROR: run scripts/ingest.py first (need data/clean/selling_prices.csv).")
        return 1

    products = pd.read_csv(sp, dtype=object).dropna(subset=["sku_code"]).drop_duplicates("sku_code")
    by_name = {norm(r.item_name): r.sku_code for r in products.itertuples() if pd.notnull(r.item_name)}
    by_code = {str(r.sku_code).upper(): r.sku_code for r in products.itertuples()}
    code_prefix = {}
    for r in products.itertuples():
        code_prefix.setdefault(str(r.sku_code).split("-")[0].upper(), r.sku_code)

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
    for a in sorted(aliases):
        n = norm(a)
        sku = by_name.get(n)
        if sku is None:
            lc = lead_code(a)
            sku = by_code.get(lc) or code_prefix.get(lc) or code_prefix.get(lc.split("-")[0])
        if sku is None:  # prefix containment fallback
            for pn, psku in by_name.items():
                if pn and (n.startswith(pn[:40]) or pn.startswith(n[:40])):
                    sku = psku
                    break
        if sku:
            matched.append((a, sku))
        else:
            unmatched.append(a)

    total = len(aliases) or 1
    rate = len(matched) / total
    pd.DataFrame(matched, columns=["alias_text", "sku_code"]).to_csv(ALIAS_OUT, index=False)
    pd.DataFrame({"alias_text": unmatched}).to_csv(UNMATCHED_OUT, index=False)

    print("=" * 60)
    print(f"Distinct item strings : {len(aliases)}")
    print(f"Matched to a SKU      : {len(matched)} ({rate:.1%})")
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
