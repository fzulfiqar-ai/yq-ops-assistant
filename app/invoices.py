"""Parse VFAN proforma invoices (PDF + .xls) → supplier RMB price history.

Both formats share a table: No./Barcode · Model · SPECS · QTY · Unit Price (¥) · DIS 18% · Amount.
We capture the model (leading product code, e.g. F15), the list price and the discounted (net) RMB
price, plus the invoice number/date — so price changes over time are visible (v_supplier_price_history).
"""
from __future__ import annotations

import io
import logging
import re
from datetime import date

log = logging.getLogger(__name__)

_VF = re.compile(r"VF\s*\d{6,}", re.I)
_CODE = re.compile(r"^[A-Za-z]{1,3}\d")            # F15, X05, UK10 …


def _num(x: object) -> float | None:
    s = re.sub(r"[^\d.\-]", "", str(x or "").replace(",", ""))
    try:
        return float(s) if s not in ("", "-", ".") else None
    except ValueError:
        return None


def _invoice_no(text: str) -> str | None:
    m = _VF.search(text or "")
    return m.group(0).replace(" ", "") if m else None


def _date_from_name(name: str) -> str | None:
    """Invoice date from the filename — '20250727 - …' or '… 2026.02.05.xls'."""
    m = re.search(r"(20\d{2})[.\-]?(\d{2})[.\-]?(\d{2})", name or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def _parse_rows(rows: list[list], text: str, filename: str) -> list[dict]:
    # locate the header row (has 'Model' and 'Unit Price')
    header = None
    hdr_i = 0
    for i, r in enumerate(rows):
        cells = [str(c or "").strip().lower() for c in r]
        if any(c == "model" for c in cells) and any("unit price" in c for c in cells):
            header, hdr_i = cells, i
            break
    if header is None:
        return []

    def col(*opts: str) -> int | None:
        for j, c in enumerate(header):
            if any(o in c for o in opts):
                return j
        return None

    mi, si, qi, ui, di = col("model"), col("spec"), col("qty"), col("unit price"), col("dis")
    inv_no = _invoice_no(text)
    inv_date = _date_from_name(filename) or _date_from_name(text)

    out: list[dict] = []
    for r in rows[hdr_i + 1:]:
        model = str(r[mi] or "").strip() if mi is not None and mi < len(r) else ""
        if not model or model.lower() in ("nan", "total") or "aslam" in model.lower() or "shipping" in model.lower():
            continue
        if not _CODE.match(model):
            continue
        out.append({
            "model": model.upper(),
            "spec": (str(r[si]).strip() if si is not None and si < len(r) and r[si] else ""),
            "qty": _num(r[qi]) if qi is not None and qi < len(r) else None,
            "unit_price_rmb": _num(r[ui]) if ui is not None and ui < len(r) else None,
            "net_price_rmb": _num(r[di]) if di is not None and di < len(r) else None,
            "invoice_no": inv_no,
            "invoice_date": inv_date,
            "source_file": filename,
        })
    return out


def parse_invoice_pdf(data: bytes, filename: str) -> list[dict]:
    import pdfplumber
    rows: list[list] = []
    text_parts: list[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for p in pdf.pages:
            text_parts.append(p.extract_text() or "")
            for t in p.extract_tables() or []:
                rows.extend(t)
    return _parse_rows(rows, "\n".join(text_parts), filename)


def parse_invoice_xls(data: bytes, filename: str) -> list[dict]:
    import pandas as pd
    df = pd.read_excel(io.BytesIO(data), header=None)
    rows = df.values.tolist()
    text = "\n".join(str(c) for r in rows for c in r if c is not None)
    return _parse_rows(rows, text, filename)


def parse_invoice(data: bytes, filename: str) -> list[dict]:
    low = (filename or "").lower()
    if low.endswith((".xls", ".xlsx")):
        return parse_invoice_xls(data, filename)
    return parse_invoice_pdf(data, filename)


def load_supplier_prices(rows: list[dict], po_no: str | None = None) -> dict:
    """Upsert parsed invoice lines into supplier_prices. Returns a summary."""
    from app.database import get_client
    rows = [r for r in rows if r.get("model") and r.get("net_price_rmb")]
    if not rows:
        return {"models": 0, "invoice": None}
    # need a stable invoice_no for the unique key; fall back to the date or PO
    inv = rows[0].get("invoice_no") or rows[0].get("invoice_date") or po_no or "UNKNOWN"
    payload, seen = [], set()
    for r in rows:
        key = (inv, r["model"], r.get("spec") or "")
        if key in seen:
            continue
        seen.add(key)
        payload.append({
            "model": r["model"], "spec": r.get("spec"), "qty": r.get("qty"),
            "unit_price_rmb": r.get("unit_price_rmb"), "net_price_rmb": r.get("net_price_rmb"),
            "invoice_no": inv, "invoice_date": r.get("invoice_date"), "source_file": r.get("source_file"),
        })
    try:
        get_client().table("supplier_prices").upsert(payload, on_conflict="invoice_no,model,spec").execute()
    except Exception as e:  # noqa: BLE001
        log.warning("supplier_prices upsert failed: %s", e)
        return {"models": 0, "invoice": inv, "error": "save failed"}
    return {"models": len({p["model"] for p in payload}), "lines": len(payload), "invoice": inv}
