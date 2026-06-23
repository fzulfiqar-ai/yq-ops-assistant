"""Phase 0 load: cleaned CSVs in data/clean/ -> Supabase (idempotent upserts).

Order:
  1. products   (derived from selling_prices: distinct sku_code + item_name + unit + status)
  2. customers  (derived from orders.customer_name + order_lines.customer_account)
  3. orders, order_lines, stock_movements, ledger_entries, product_profitability, selling_prices

Upserts use each table's natural key (on_conflict) so re-running never double-counts
(data rules 2/6 friendly). Requires the tables created by migrate_supabase.py.

Usage:  python scripts/load_supabase.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CLEAN = ROOT / "data" / "clean"
CHUNK = 500


def _client():
    # imported here so `ingest.py` users without Supabase config aren't forced to set it up
    from app.database import get_client

    return get_client()


def _records(df: pd.DataFrame) -> list[dict]:
    df = df.where(pd.notnull(df), None)
    out = []
    for rec in df.to_dict(orient="records"):
        clean = {}
        for k, v in rec.items():
            if isinstance(v, float) and math.isnan(v):
                v = None
            clean[k] = v
        out.append(clean)
    return out


def _upsert(client, table: str, records: list[dict], on_conflict: str) -> int:
    n = 0
    for i in range(0, len(records), CHUNK):
        batch = records[i : i + CHUNK]
        client.table(table).upsert(batch, on_conflict=on_conflict).execute()
        n += len(batch)
    print(f"  upserted {table:24} {n:7}")
    return n


def _read(name: str) -> pd.DataFrame | None:
    p = CLEAN / f"{name}.csv"
    if not p.exists():
        print(f"  (skip {name}: {p.name} not found)")
        return None
    return pd.read_csv(p, dtype=object)


def main() -> int:
    if not CLEAN.exists():
        print(f"ERROR: {CLEAN} not found. Run scripts/ingest.py first.")
        return 1
    client = _client()
    print("Loading data/clean/ -> Supabase\n" + "=" * 60)

    # 1) products from price books
    sp = _read("selling_prices")
    if sp is not None:
        prods = (
            sp[["sku_code", "item_name", "unit_name", "status"]]
            .dropna(subset=["sku_code"])
            .drop_duplicates(subset=["sku_code"])
        )
        _upsert(client, "products", _records(prods), on_conflict="sku_code")

    # 2) customers from sales
    names: set[str] = set()
    od = _read("orders")
    ol = _read("order_lines")
    if od is not None:
        names |= {x for x in od["customer_name"].dropna().tolist()}
    if ol is not None:
        names |= {x for x in ol["customer_account"].dropna().tolist()}
    if names:
        _upsert(client, "customers",
                [{"name": n} for n in sorted(names)], on_conflict="name")

    # 3) fact + pricing tables
    if od is not None:
        _upsert(client, "orders", _records(od), on_conflict="invoice_no")
    if ol is not None:
        _upsert(client, "order_lines", _records(ol), on_conflict="invoice_no,line_no")
    sm = _read("stock_movements")
    if sm is not None:
        before = len(sm)
        sm = sm.dropna(subset=["item_name"])
        dropped = before - len(sm)
        if dropped:
            print(f"  (dropped {dropped} stock_movements rows with null item_name)")
        _upsert(client, "stock_movements", _records(sm),
                on_conflict="voucher,item_name,row_hash")
    le = _read("ledger_entries")
    if le is not None:
        before = len(le)
        le = le.dropna(subset=["account"])
        le = le.drop_duplicates(subset=["account", "voucher", "row_hash"])
        dropped = before - len(le)
        if dropped:
            print(f"  (dropped {dropped} ledger_entries rows with null account or duplicates)")
        _upsert(client, "ledger_entries", _records(le),
                on_conflict="account,voucher,row_hash")
    pp = _read("product_profitability")
    if pp is not None:
        pp = pp.dropna(subset=["item_name"])
        _upsert(client, "product_profitability", _records(pp),
                on_conflict="item_name,report_date")
    if sp is not None:
        _upsert(client, "selling_prices", _records(sp),
                on_conflict="sku_code,price_book,customer_code,start_date")
    ar = _read("receivables")
    if ar is not None:
        ar = ar.dropna(subset=["account"])
        _upsert(client, "ar_ageing", _records(ar), on_conflict="account,as_of_date")
    sb = _read("stock_balance")
    if sb is not None:
        sb = sb.dropna(subset=["item_name"])
        sb["warehouse_name"] = sb["warehouse_name"].fillna("(unassigned)")
        for col in ("net_qty", "total_value_bhd", "selling_rate_bhd"):
            sb[col] = pd.to_numeric(sb[col], errors="coerce")
        # Focus can list an item twice in one warehouse — aggregate so the upsert
        # key (item, warehouse, as_of) is unique (sum qty/value, average rate).
        sb = (sb.groupby(["item_name", "warehouse_name", "as_of_date"], as_index=False, dropna=False)
                .agg(net_qty=("net_qty", "sum"),
                     total_value_bhd=("total_value_bhd", "sum"),
                     selling_rate_bhd=("selling_rate_bhd", "mean"),
                     source_file=("source_file", "first")))
        # snapshot replace: clear each as_of_date first so a re-parse never leaves
        # stale rows (e.g. items whose key changed) double-counting the total.
        for d in sb["as_of_date"].dropna().unique():
            client.table("stock_balance").delete().eq("as_of_date", str(d)).execute()
        _upsert(client, "stock_balance", _records(sb),
                on_conflict="item_name,warehouse_name,as_of_date")

    print("=" * 60)
    print("Load complete. Run scripts/reconcile_products.py to link item names -> SKUs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
