"""Ingest VFAN proforma invoices → supplier_prices (RMB price history).

Reads the dated invoice PDFs in each 'Order received/<order>/' folder and the PI .xls files under
'Shipments/'. Re-runnable (upsert by invoice_no + model + spec).

  python -m scripts.ingest_invoices
"""
from __future__ import annotations

import glob
import os

from dotenv import load_dotenv

load_dotenv()

import psycopg  # noqa: E402

from app.invoices import load_supplier_prices, parse_invoice  # noqa: E402


def _candidate_files() -> list[str]:
    pats = [
        "Order received/*/*Invoice*.pdf",
        "Order received/*/*Power Bank*.pdf",
        "Order received/*/*Accessories*.pdf",
        "Shipments/**/*PI*.xls",
        "Shipments/**/*PI*.xlsx",
    ]
    out: set[str] = set()
    for p in pats:
        out.update(glob.glob(p, recursive=True))
    # never treat a Focus PO / MRN as an invoice
    return sorted(f for f in out if not os.path.basename(f).lower().startswith(("po_", "material_receipt")))


def main() -> None:
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute(open("scripts/supplier_prices_migration.sql", encoding="utf-8").read())

    files = _candidate_files()
    if not files:
        print("No invoice files found.")
        return
    loaded = 0
    for f in files:
        try:
            rows = parse_invoice(open(f, "rb").read(), os.path.basename(f))
            r = load_supplier_prices(rows)
            loaded += 1 if r.get("models") else 0
            print(f"  {os.path.basename(f)[:48]:48} -> {r.get('models', 0):>2} models  (inv {r.get('invoice')})")
        except Exception as e:  # noqa: BLE001
            print(f"  skip {os.path.basename(f)[:48]}: {type(e).__name__}: {e}")
    print(f"\nParsed {len(files)} invoice files; {loaded} loaded prices.")


if __name__ == "__main__":
    main()
