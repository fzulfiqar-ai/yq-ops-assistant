"""Ingest Material Receipt Note (MRN) XML exports → real landed costs.

Each Transactions_*.xml is a Focus MRN. For every received line we take the TRUE all-in landed cost
per unit = StockValue ÷ Quantity (StockValue already includes vendor freight + 3rd-party freight +
customs + misc), plus the pre-freight product cost = Gross ÷ Quantity. Costs are keyed on the FULL
ProdCode (e.g. 'X01 UC') so cable variants never collapse. When a SKU appears in several MRNs we
keep the most RECENT receipt (latest YQ-YY-MM doc number).

CLI:    python -m scripts.ingest_mrn                      # bulk-load the Order received/ folder
API:    parse_mrn_bytes() + load_mrn_costs()              # used by POST /material-receipts/upload
"""
from __future__ import annotations

import glob
import os
import re
from xml.etree import ElementTree as ET

MRN_GLOB = "Order received/*/Transactions_*.xml"


def _doc_month(doc_no: str | None) -> str | None:
    """YQ-26-02-2 → '2026-02-01' (used to pick the latest receipt per SKU)."""
    m = re.match(r"YQ-(\d{2})-(\d{2})-", doc_no or "")
    return f"20{int(m.group(1)):02d}-{int(m.group(2)):02d}-01" if m else None


def _parse_root(root: ET.Element) -> list[dict]:
    out: list[dict] = []
    for trans in root.iter("Transaction"):
        header = trans.find("Header")
        mrn_no = header.findtext("DocNo") if header is not None else None
        # the MRN can be raised under a DIFFERENT number than the PO (received in a later month) —
        # follow the linked PO number from HeaderExtra so received lines attach to the right order.
        po_no = None
        hx = trans.find("HeaderExtra")
        if hx is not None:
            for pair in hx.findall("IdNamePair"):
                if (pair.findtext("Name") or "") == "PONo":
                    po_no = (pair.findtext("Tag") or "").replace("PO:", "").strip() or None
        doc_no = po_no or mrn_no          # KEY received data by the PO number (the order key)
        eff = _doc_month(mrn_no)          # latest-cost ordering uses the receipt (MRN) month
        body = trans.find("BodyData")
        if body is None:
            continue
        for tb in body.findall("TransBody"):
            sales = tb.find("Sales")
            if sales is None:
                continue
            code = (sales.findtext("ProdCode") or "").strip()
            qty = float(sales.findtext("Quantity") or 0)
            stockval = float(sales.findtext("StockValue") or 0)   # all-in (incl. vendor freight)
            gross = float(sales.findtext("Gross") or 0)           # supplier price only
            if not code or qty <= 0:
                continue
            out.append({"code": code, "landed": stockval / qty, "product": gross / qty,
                        "qty": qty, "doc_no": doc_no, "eff": eff})
    return out


def parse_mrn(path: str) -> list[dict]:
    return _parse_root(ET.parse(path).getroot())


def parse_mrn_bytes(data: bytes) -> list[dict]:
    return _parse_root(ET.fromstring(data))


def dedupe_latest(rows: list[dict]) -> list[dict]:
    """Keep the most recent receipt per SKU (so current cost = latest landed cost)."""
    best: dict[str, dict] = {}
    for r in rows:
        k = r["code"].upper()
        if k not in best or (r["eff"] or "") >= (best[k]["eff"] or ""):
            best[k] = r
    return list(best.values())


def load_mrn_costs(rows: list[dict]) -> dict:
    """Upsert via the Supabase client (used by the API): the deduped current-cost lookup
    (`mrn_landed_costs`) AND the full per-receipt detail (`mrn_lines`). Returns a summary."""
    from app.database import get_client
    c = get_client()

    # current cost = latest receipt per SKU
    final = dedupe_latest(rows)
    cost_payload = [{
        "sku_code": r["code"], "landed_cost_bhd": round(r["landed"], 4),
        "product_cost_bhd": round(r["product"], 4), "last_qty": r["qty"],
        "doc_no": r["doc_no"], "effective_date": r["eff"],
    } for r in final]
    if cost_payload:
        c.table("mrn_landed_costs").upsert(cost_payload, on_conflict="sku_code").execute()

    # per-receipt lines (one row per doc+SKU) for order-level detail
    seen: dict[tuple, dict] = {}
    for r in rows:
        if not r.get("doc_no"):
            continue
        # A single receipt can list the SAME doc+SKU on several lines (split boxes / a variant
        # received in multiple lots). SUM their quantities — overwriting silently dropped units and
        # made fully-received orders look short. Keep the first line's per-unit landed/product cost.
        key = (r["doc_no"], r["code"].upper())
        if key in seen:
            seen[key]["qty"] += r["qty"]
        else:
            seen[key] = {
                "doc_no": r["doc_no"], "sku_code": r["code"], "qty": r["qty"],
                "landed_unit_bhd": round(r["landed"], 4), "product_unit_bhd": round(r["product"], 4),
            }
    if seen:
        c.table("mrn_lines").upsert(list(seen.values()), on_conflict="doc_no,sku_code").execute()

    docs = sorted({r["doc_no"] for r in final if r["doc_no"]})
    return {"skus": len(final), "lines": len(rows), "docs": docs}


def main() -> None:
    import psycopg
    from dotenv import load_dotenv
    load_dotenv()

    files = sorted(glob.glob(MRN_GLOB))
    if not files:
        print("No MRN XML files found under 'Order received/'.")
        return
    rows: list[dict] = []
    for f in files:
        try:
            got = parse_mrn(f)
            rows.extend(got)
            print(f"  {os.path.basename(os.path.dirname(f))}: {len(got)} lines  ({os.path.basename(f)})")
        except Exception as e:  # noqa: BLE001
            print(f"  skip {f}: {type(e).__name__}: {e}")
    # ensure both tables exist, then reuse the shared (Supabase) loader for the upserts
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute(open("scripts/mrn_costs_migration.sql", encoding="utf-8").read())
        conn.execute(open("scripts/mrn_lines_migration.sql", encoding="utf-8").read())
    summary = load_mrn_costs(rows)
    print(f"\nLoaded {summary['skus']} unique SKU landed costs + per-receipt lines "
          f"from {len(files)} MRN files ({summary['lines']} total lines).")


if __name__ == "__main__":
    main()
