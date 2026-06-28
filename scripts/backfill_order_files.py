"""One-off: load the existing 'Order received/<order>/' documents into the order file vault —
PO PDFs, MRN PDFs and the Capture*.JPG shelf/shipment photos — so current orders show their files.

Idempotent: skips a file already recorded for that order (matched by filename).

  python -m scripts.backfill_order_files
"""
from __future__ import annotations

import glob
import os
import re

from dotenv import load_dotenv

load_dotenv()

import psycopg  # noqa: E402

from app.database import get_client  # noqa: E402
from app.orders import _ensure_bucket, store_order_file  # noqa: E402

ROOT = "Order received"
_PO = re.compile(r"YQ[_-](\d{2})[_-](\d{2})[_-](\d+)")


def po_from(name: str) -> str | None:
    m = _PO.search(name or "")
    return f"YQ-{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def _existing(po_no: str) -> set[str]:
    try:
        rows = get_client().table("order_files").select("filename").eq("po_no", po_no).execute().data or []
        return {r.get("filename") for r in rows}
    except Exception:  # noqa: BLE001
        return set()


def main() -> None:
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute(open("scripts/order_files_migration.sql", encoding="utf-8").read())
    _ensure_bucket()

    stored = skipped = 0
    for folder in sorted(glob.glob(f"{ROOT}/*/")):
        names = [f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))]
        # the ORDER key is the PO number — a folder's MRN can be raised under a different number,
        # so derive the key from the PO_ file and file EVERY document in the folder under it.
        po_file = next((f for f in names if f.lower().startswith("po_")), None)
        folder_po = po_from(po_file) if po_file else next((po_from(f) for f in names if po_from(f)), None)
        have = _existing(folder_po) if folder_po else set()
        for f in sorted(names):
            low = f.lower()
            if low.startswith("po_") and low.endswith(".pdf"):
                kind, ext, ct = "po", ".pdf", "application/pdf"
            elif low.startswith("material_receipt") and low.endswith(".pdf"):
                kind, ext, ct = "mrn", ".pdf", "application/pdf"
            elif low.startswith("capture") and low.endswith((".jpg", ".jpeg", ".png")):
                kind, ext, ct = "photo", os.path.splitext(f)[1], "image/jpeg"
            elif low.endswith(".xml"):                 # the MRN data export (Transactions_*.xml)
                kind, ext, ct = "mrn", ".xml", "application/xml"
            elif low.endswith(".pdf"):                 # supplier proforma / invoice (dated)
                kind, ext, ct = "invoice", ".pdf", "application/pdf"
            elif low.endswith((".xls", ".xlsx")):      # packing list / selling rate
                kind, ext, ct = "doc", os.path.splitext(f)[1], "application/vnd.ms-excel"
            else:
                continue
            po = folder_po
            if not po:
                continue
            if f in have:
                skipped += 1
                continue
            data = open(os.path.join(folder, f), "rb").read()
            if store_order_file(po, kind, data, ext, ct, by="backfill", filename=f):
                stored += 1
                print(f"  {po:14} {kind:6} {f}")

    print(f"\nBackfilled {stored} order files ({skipped} already present).")


if __name__ == "__main__":
    main()
