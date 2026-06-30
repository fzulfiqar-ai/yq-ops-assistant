"""Ingest Focus Purchase Order PDFs ('Order received/*/PO_*.pdf') into purchase_orders.

Each PO PDF is a clean text table (PO No, PO Date, vendor, line items: Code/Qty/Rate/Gross). The PO
number (YQ-25-09-1) matches its MRN, so the per-item cost-comparison-across-orders + ordered->received
lifecycle work. Re-runnable (upsert by po_no + code).

  python -m scripts.ingest_po ["Order received"]
"""
from __future__ import annotations

import glob
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

import pdfplumber  # noqa: E402
from app.database import get_client  # noqa: E402

# one PO line: "1 F15 <desc...> Nos 80.000 3.025 242.000"
ITEM = re.compile(r"^(\d+)\s+(\S+)\s+(.+?)\s+([A-Za-z]+)\s+([\d,]+\.\d+)\s+([\d,]+\.\d+)\s+([\d,]+\.\d+)$")


def _num(s) -> float | None:
    try:
        return float(str(s).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _date(s: str) -> str | None:
    for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _extract_text(src) -> str:
    import io
    handle = io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else src
    with pdfplumber.open(handle) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


def parse_po_text(text: str, source_file: str = "upload.pdf") -> list[dict]:
    """Parse extracted PO text into line dicts. Reused by the CLI and the upload endpoint."""
    def g(pat: str) -> str | None:
        m = re.search(pat, text)
        return m.group(1) if m else None

    po_no = g(r"PO No\s*:?\s*(\S+)")
    po_date = _date(g(r"PO Date\s*:?\s*(\d{1,2}/\d{1,2}/\d{4})") or "")
    vendor = g(r"Name\s*:?\s*([A-Za-z0-9&._-]+)")
    warehouse = (g(r"Warehouse\s*:?\s*(.+)") or "").strip()

    items: list[dict] = []
    n = 0
    for line in (x.strip() for x in text.splitlines() if x.strip()):
        # Multi-page POs repeat the footer (PREPARED/CHECKED/page no.) and per-page subtotals on
        # EVERY page — skip those lines and keep scanning instead of stopping at the first one
        # (the strict ITEM regex below guarantees only real line items are captured).
        if re.match(r"^(Narration|Taxable|Net Amount|PREPARED|CHECKED|User Name|Page\b|Sub Total|Grand Total|S No)", line):
            continue
        m = ITEM.match(line)
        if m:
            n += 1
            items.append({
                "po_no": po_no, "po_date": po_date, "vendor": vendor, "warehouse": warehouse,
                "line_no": n, "code": m.group(2), "description": m.group(3).strip(),
                "qty": _num(m.group(5)), "rate_bhd": _num(m.group(6)), "gross_bhd": _num(m.group(7)),
                "source_file": source_file,
            })
        elif items and not line.startswith("S No"):
            items[-1]["description"] += " " + line          # description continuation
    return [it for it in items if it.get("po_no") and it.get("code")]


def parse_po(path: str) -> list[dict]:
    return parse_po_text(_extract_text(path), Path(path).name)


def parse_po_bytes(data: bytes, source_file: str = "upload.pdf") -> list[dict]:
    return parse_po_text(_extract_text(data), source_file)


def load_pos(rows: list[dict]) -> int:
    client = get_client()
    for i in range(0, len(rows), 200):
        client.table("purchase_orders").upsert(rows[i:i + 200], on_conflict="po_no,line_no").execute()
    return len(rows)


def main() -> int:
    folder = sys.argv[1] if len(sys.argv) > 1 else "Order received"
    src = folder if Path(folder).is_absolute() else str(ROOT / folder)
    files = sorted({p for p in glob.glob(str(Path(src) / "**" / "*.pdf"), recursive=True)
                    if re.search(r"(^|[\\/])PO[_ ]|purchase.?order", Path(p).name, re.I)
                    and "receipt" not in Path(p).name.lower()})
    if not files:
        print(f"No PO PDFs found under '{folder}'. Name them PO_*.pdf in each order folder.")
        return 1

    all_rows: list[dict] = []
    for f in files:
        try:
            rows = parse_po(f)
            all_rows += rows
            po = rows[0]["po_no"] if rows else "?"
            print(f"  OK  {Path(f).name:46} -> {len(rows):3} lines (PO {po})")
        except Exception as e:  # noqa: BLE001
            print(f"  ERR {Path(f).name}: {e}")
    if not all_rows:
        print("No PO lines parsed.")
        return 1

    client = get_client()
    for i in range(0, len(all_rows), 200):
        client.table("purchase_orders").upsert(all_rows[i:i + 200], on_conflict="po_no,line_no").execute()
    pos = len({r["po_no"] for r in all_rows})
    print(f"Loaded {len(all_rows)} PO lines across {pos} purchase order(s) from {len(files)} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
