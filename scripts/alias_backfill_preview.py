"""Alias backfill preview (R1, D2 -- 24-Sep-2026): which sales item strings carry no catalog code,
what the matcher proposes for each, and what the gap is worth. READ-ONLY unless --commit.

    python -m scripts.alias_backfill_preview                  # print proposals, write nothing
    python -m scripts.alias_backfill_preview --csv out.csv    # also write the table
    python -m scripts.alias_backfill_preview --commit         # insert product_aliases for proposals
                                                              # at or above --min-confidence (0.9)

Why: v_sales.sku_code comes from product_aliases (alias_text = the exact Focus item string). Item
names that arrived after the last reconcile (July's VFAN lines: T17, T18, X24 / X31 … variants) sell
well but count as nothing -- so their catalog rows looked like slow stock and wore "Last chance".
Accessories and SIM are listed separately: SIM never counts toward targets, so a SIM alias only
tidies reporting. Matching is scripts.reconcile_products.match_alias (method + confidence shown).

--commit uses the service key (app.database.get_client), upserts on alias_text, records one
audit_log row and then calls shop.invalidate() so the market's velocity picks the sales up.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from scripts.reconcile_products import build_index, match_alias  # noqa: E402

UNALIASED_SQL = """
SELECT item_name, division,
       COUNT(*)                                                           AS lines,
       COALESCE(SUM(quantity), 0)                                         AS units,
       COALESCE(SUM(revenue_bhd), 0)                                      AS rev_365,
       COALESCE(SUM(CASE WHEN sale_date >= CURRENT_DATE - 90 THEN revenue_bhd ELSE 0 END), 0) AS rev_90,
       MAX(sale_date)::text                                               AS last_sold
FROM v_sales
WHERE sku_code IS NULL AND sale_date >= CURRENT_DATE - 365
GROUP BY item_name, division
ORDER BY division, rev_90 DESC, rev_365 DESC
"""


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def propose(unaliased: list[dict], products: list[dict]) -> list[dict]:
    """Pure: one proposal row per unaliased item string.
    products: [{id, sku_code, item_name}] -- the alias target (v_sales joins products)."""
    idx = build_index((p.get("sku_code"), p.get("item_name")) for p in products)
    id_by_sku = {str(p["sku_code"]): p["id"] for p in products if p.get("sku_code")}
    out: list[dict] = []
    for r in unaliased:
        alias = str(r.get("item_name") or "")
        sku, method, conf = match_alias(alias, idx)
        out.append({
            "division": r.get("division") or "",
            "item_name": alias,
            "proposed_code": sku,
            "product_id": id_by_sku.get(sku) if sku else None,
            "method": method,
            "confidence": conf,
            "lines": int(_f(r.get("lines"))),
            "units": _f(r.get("units")),
            "rev_90": round(_f(r.get("rev_90")), 3),
            "rev_365": round(_f(r.get("rev_365")), 3),
            "last_sold": r.get("last_sold") or "",
        })
    return out


def _print(rows: list[dict], min_conf: float) -> None:
    for div in ("Accessories", "SIM", ""):
        block = [r for r in rows if r["division"] == div] if div else [r for r in rows if r["division"] not in ("Accessories", "SIM")]
        if not block:
            continue
        label = div or "Other divisions"
        print("=" * 118)
        print(f"{label}: {len(block)} unaliased item strings  "
              f"(90-day revenue BHD {sum(r['rev_90'] for r in block):,.3f}, 365-day BHD {sum(r['rev_365'] for r in block):,.3f})")
        print("-" * 118)
        print(f"{'item string':52} {'proposed':14} {'method':12} {'conf':>4} {'lines':>5} {'rev 90d':>10} {'rev 365d':>10}  last")
        for r in block:
            code = r["proposed_code"] or "-"
            flag = "" if r["proposed_code"] is None else ("" if r["product_id"] else " (no products row)")
            print(f"{r['item_name'][:52]:52} {code[:14]:14} {r['method']:12} {r['confidence']:>4.1f} "
                  f"{r['lines']:>5} {r['rev_90']:>10,.3f} {r['rev_365']:>10,.3f}  {r['last_sold']}{flag}")
    ok = [r for r in rows if r["proposed_code"] and r["product_id"] and r["confidence"] >= min_conf]
    low = [r for r in rows if r["proposed_code"] and r["product_id"] and r["confidence"] < min_conf]
    none = [r for r in rows if not (r["proposed_code"] and r["product_id"])]
    print("=" * 118)
    print(f"Would insert at confidence >= {min_conf:.1f}: {len(ok)} aliases "
          f"(90-day revenue BHD {sum(r['rev_90'] for r in ok):,.3f} becomes attributable)")
    if low:
        print(f"Below the bar (review by hand): {len(low)}")
    if none:
        print(f"No catalog code (add the SKU to the price book first): {len(none)} -- "
              + ", ".join(r["item_name"][:40] for r in none[:8]) + (" …" if len(none) > 8 else ""))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--commit", action="store_true", help="insert product_aliases rows (otherwise read-only)")
    ap.add_argument("--min-confidence", type=float, default=0.9)
    ap.add_argument("--csv", help="also write the proposal table to this path")
    args = ap.parse_args(argv)

    from app.db_read import exec_sql
    unaliased = exec_sql(UNALIASED_SQL) or []
    products = exec_sql("SELECT id, sku_code, item_name FROM products WHERE sku_code IS NOT NULL") or []
    rows = propose(unaliased, products)
    _print(rows, args.min_confidence)

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["item_name"])
            w.writeheader()
            w.writerows(rows)
        print(f"wrote {args.csv}")

    if not args.commit:
        print("(preview only -- nothing written; add --commit to insert the aliases)")
        return 0

    picked = [r for r in rows if r["proposed_code"] and r["product_id"] and r["confidence"] >= args.min_confidence]
    if not picked:
        print("nothing to commit")
        return 0
    from app.database import get_client
    client = get_client()
    recs = [{"alias_text": r["item_name"], "product_id": r["product_id"]} for r in picked]
    for i in range(0, len(recs), 500):
        client.table("product_aliases").upsert(recs[i:i + 500], on_conflict="alias_text").execute()
    try:
        client.table("audit_log").insert({
            "user_email": "alias_backfill", "event": "product_aliases.backfill",
            "question": f"alias_backfill_preview --commit --min-confidence {args.min_confidence}",
            "detail": {"inserted": len(recs), "codes": sorted({r["proposed_code"] for r in picked}),
                       "rev_90_bhd": round(sum(r["rev_90"] for r in picked), 3)},
        }).execute()
    except Exception as e:  # noqa: BLE001
        print(f"(audit_log not written: {e})")
    try:
        from app.shop import invalidate
        invalidate()
    except Exception:  # noqa: BLE001
        pass
    print(f"inserted {len(recs)} aliases; shop cache invalidated")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
