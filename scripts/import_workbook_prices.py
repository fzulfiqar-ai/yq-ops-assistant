"""Load the owner's B2B selling prices from a landed-cost / price workbook into the price book.

    python -m scripts.import_workbook_prices "<path>.xlsx" [--sheet "Price & Margin"] [--date 2026-09-14] [--dry-run]

Why: the owner maintains a workbook (YQ_MRN_Landing_Cost_Analysis) whose "Selling Price incl VAT"
column is the authoritative trade (B2B) price. The catalog reads prices ONLY from the price book
(selling_prices → v_price_list_by_book → v_catalog), so this script feeds the workbook into that
chain instead of touching the catalog: one MA_base dealer row per changed SKU (customer_code NULL,
warehouse_name NULL — the dealer/list layer), dated --date (default: the workbook's modified date).
Pricing stays versioned (a change is a NEW dated row, never an overwrite — feedback_db_design rule 2)
and a later Focus price-book upload with a newer start_date supersedes these rows automatically.

Idempotent: SKUs whose live price already equals the workbook price are skipped; re-running with the
same --date updates the same row (natural key sku_code+price_book+customer_code+warehouse_name+start_date).
Only SKUs that exist in catalog_items are touched; nothing is created for unknown codes.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

import warnings  # noqa: E402
warnings.filterwarnings("ignore")


def read_workbook(path: Path, sheet: str) -> dict[str, tuple[float, str | None]]:
    import openpyxl
    ws = openpyxl.load_workbook(path, read_only=True, data_only=True)[sheet]
    rows = list(ws.iter_rows(values_only=True))
    hdr_i = next(i for i, r in enumerate(rows) if r and "Item Code" in [str(c or "") for c in r])
    hdr = [str(c or "") for c in rows[hdr_i]]
    ix = {h: i for i, h in enumerate(hdr)}
    price_col = next(h for h in hdr if h.startswith("Selling Price incl VAT"))
    since_col = next((h for h in hdr if "In Force" in h or "Effective" in h), None)
    out: dict[str, tuple[float, str | None]] = {}
    for r in rows[hdr_i + 1:]:
        code = r[ix["Item Code"]]
        if not code or str(code).strip().upper() == "TOTAL":
            continue
        try:
            price = float(r[ix[price_col]] or 0)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        since = r[ix[since_col]] if since_col else None
        out[str(code).strip().upper()] = (round(price, 3), str(since) if since else None)
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--sheet", default="Price & Margin")
    ap.add_argument("--date", default=None, help="start_date for the new rows (YYYY-MM-DD)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    path = Path(a.xlsx)
    if not path.exists():
        print(f"ERROR: {path} not found")
        return 1
    start = a.date or datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()
    date.fromisoformat(start)  # validate

    from app.database import get_client
    from app.db_read import exec_sql

    sheet = read_workbook(path, a.sheet)
    client = get_client()
    cat = {r["item_code"].upper(): r for r in
           (client.table("catalog_items").select("item_code,display_name,spec").execute().data or [])}
    live = {str(r["item_code"]).upper(): (float(r["price_bhd"]) if r.get("price_bhd") is not None else None)
            for r in exec_sql("SELECT sku_code AS item_code, price_bhd FROM v_price_list_by_book "
                              "WHERE price_book = 'MA_base'") or []}
    changes = []
    for code, (price, since) in sheet.items():
        if code not in cat:
            continue
        cur = live.get(code)
        if cur is not None and abs(cur - price) < 0.0005:
            continue
        changes.append((code, cur, price, since))
    print(f"workbook rows: {len(sheet)} · in catalog: {sum(1 for c in sheet if c in cat)} · "
          f"price changes: {len(changes)} · start_date {start}")
    for code, cur, price, since in changes:
        print(f"  {code:<16} {cur if cur is not None else '—':>7} -> {price:<7} (workbook in force since {since})")
    if a.dry_run or not changes:
        print("DRY RUN — nothing written" if a.dry_run else "nothing to do")
        return 0

    now = datetime.now(timezone.utc).isoformat()
    written = 0
    for code, cur, price, since in changes:
        it = cat[code]
        row = {
            "item_name": it.get("spec") or it.get("display_name") or code,
            "sku_code": it["item_code"],   # the catalog's exact casing (v_catalog joins on it)
            "price_book": "MA_base", "currency": "Bahraini dinar",
            "start_date": start, "min_qty": 0, "max_qty": 0, "unit_name": "Nos",
            "rate_bhd": price, "price_tiers": json.dumps({"val_1": price}),
            "status": "Authorized", "source_file": path.name, "imported_at": now,
            "narration": f"owner workbook price (in force since {since})" if since else "owner workbook price",
        }
        existing = (client.table("selling_prices").select("id").eq("sku_code", it["item_code"]).eq("price_book", "MA_base")
                    .is_("customer_code", "null").is_("warehouse_name", "null").eq("start_date", start)
                    .limit(1).execute().data or [])
        if existing:
            client.table("selling_prices").update(row).eq("id", existing[0]["id"]).execute()
        else:
            client.table("selling_prices").insert(row).execute()
        written += 1
    print(f"written: {written} dealer rows dated {start}")
    try:
        from app.ai import flush_cache
        flush_cache()   # also drops the shop payload cache
    except Exception as e:  # noqa: BLE001
        print("cache flush skipped:", e)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
