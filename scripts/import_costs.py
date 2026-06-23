"""Load landed purchase costs into purchase_costs (Rule 2: versioned, INSERT-or-IGNORE).

The table is unique on (sku_code, effective_date); the "current" cost for a SKU is the
row with the latest effective_date (MAX(id) tiebreak). We never overwrite history — each
import is a dated snapshot.

Default is a DRY RUN that just shows what it detected. Add --commit to write.

  # preview detected columns + sample rows
  python -m scripts.import_costs "roadmap_sources/raw/pricing/Mobile Accessories - Cost Pricing  RRP.xlsx"

  # write, dating the snapshot
  python -m scripts.import_costs "<file.xlsx>" --effective-date 2026-06-01 --commit

  # override auto-detected columns if needed
  python -m scripts.import_costs "<file.xlsx>" --sku-col "Item Code" --cost-col "Landed Cost" --commit
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app.database import get_client  # noqa: E402

SKU_HINTS = ("sku", "item code", "itemcode", "code", "barcode", "part")
COST_HINTS = ("landed", "cost", "purchase", "buy", "rrp", "price")


def _detect(columns: list[str], hints: tuple[str, ...]) -> str | None:
    low = {c: str(c).strip().lower() for c in columns}
    # exact-ish: prefer the earliest hint that appears
    for h in hints:
        for c, cl in low.items():
            if h in cl:
                return c
    return None


def load(path: Path, effective: str, sku_col: str | None, cost_col: str | None, commit: bool) -> int:
    df = pd.read_excel(path)
    df.columns = [str(c).strip() for c in df.columns]
    sku_col = sku_col or _detect(list(df.columns), SKU_HINTS)
    cost_col = cost_col or _detect(list(df.columns), COST_HINTS)

    print(f"file        : {path.name}")
    print(f"columns     : {list(df.columns)}")
    print(f"sku column  : {sku_col}")
    print(f"cost column : {cost_col}")
    print(f"effective   : {effective}")
    if not sku_col or not cost_col:
        print("\nCould not auto-detect the SKU and/or cost column. Re-run with "
              "--sku-col and --cost-col.")
        return 1

    rows = []
    for _, r in df.iterrows():
        sku = str(r[sku_col]).strip()
        cost = pd.to_numeric(r[cost_col], errors="coerce")
        if not sku or sku.lower() == "nan" or pd.isna(cost):
            continue
        rows.append({
            "sku_code": sku,
            "landed_cost_bhd": float(cost),
            "currency": "BHD",
            "effective_date": effective,
            "source_file": path.name,
        })

    print(f"\nparsed {len(rows)} valid cost rows")
    for s in rows[:5]:
        print("   ", s["sku_code"], "->", s["landed_cost_bhd"])

    if not commit:
        print("\nDRY RUN — nothing written. Re-run with --commit to load.")
        return 0

    client = get_client()
    # INSERT-or-IGNORE on the (sku_code, effective_date) unique key — never overwrite a snapshot.
    client.table("purchase_costs").upsert(
        rows, on_conflict="sku_code,effective_date", ignore_duplicates=True
    ).execute()
    print(f"\n✓ wrote {len(rows)} rows to purchase_costs (effective {effective}).")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Load purchase_costs from a pricing sheet.")
    ap.add_argument("file")
    ap.add_argument("--effective-date", default=date.today().isoformat(), dest="eff")
    ap.add_argument("--sku-col", default=None)
    ap.add_argument("--cost-col", default=None)
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args()
    p = Path(a.file)
    if not p.exists():
        print(f"file not found: {p}")
        return 1
    return load(p, a.eff, a.sku_col, a.cost_col, a.commit)


if __name__ == "__main__":
    raise SystemExit(main())
