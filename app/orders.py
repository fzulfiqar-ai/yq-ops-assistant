"""Order detail — assemble one order's full record by its number (PO number = MRN number).

Joins the three sources on the LEADING product code (the common grain: the PO collapses cable
variants to 'X05', so we aggregate the MRN receipt + sales to match):
  ORDERED   v_po_item        (qty, rate)
  RECEIVED  mrn_lines        (qty, real landed cost — all freight in)   [+ shipments cross-check]
  SELLING   v_sales          (current avg net price)  ->  margin-on-arrival
Adds ordered-vs-received reconciliation (short / over / cost-overrun) and the linked 8-stage
procurement timeline. Read-only + advise-only; po_no is bound as a $N parameter via
run_readonly_query_params — never interpolated into the SQL string.
"""
from __future__ import annotations

import json
import logging
import re
import uuid

from app.db_read import exec_sql, exec_sql_params
from app.database import get_client

log = logging.getLogger(__name__)

_BUCKET = "orders"          # PRIVATE storage bucket for order documents
_SIGNED_TTL = 60 * 60       # 1h signed URLs


# ── File vault — PO PDF / MRN XML / shelf photos, per order ───────────────────

def _ensure_bucket() -> None:
    try:
        get_client().storage.create_bucket(_BUCKET, options={"public": False})
    except Exception:  # noqa: BLE001 — already exists / race
        pass


def store_order_file(po_no: str, kind: str, data: bytes, ext: str,
                     content_type: str, by: str = "", filename: str | None = None) -> str | None:
    """Upload one order document to the private bucket + record it in order_files. Returns the path."""
    _ensure_bucket()
    safe_po = re.sub(r"[^A-Za-z0-9._-]", "_", po_no or "unknown")
    path = f"{safe_po}/{kind}/{uuid.uuid4().hex}{ext}"
    try:
        get_client().storage.from_(_BUCKET).upload(path, data, {"content-type": content_type})
        get_client().table("order_files").insert(
            {"po_no": po_no, "kind": kind, "path": path,
             "filename": filename or f"{kind}{ext}", "uploaded_by": by}).execute()
        return path
    except Exception as e:  # noqa: BLE001
        log.warning("order file store failed (%s/%s): %s", po_no, kind, e)
        return None


def _sign(path: str | None) -> str | None:
    if not path:
        return None
    try:
        res = get_client().storage.from_(_BUCKET).create_signed_url(path, _SIGNED_TTL)
        return (res or {}).get("signedURL") or (res or {}).get("signedUrl")
    except Exception:  # noqa: BLE001
        return None


def files(po_no: str) -> list[dict]:
    """Order documents (signed URLs) for the detail view."""
    try:
        rows = (get_client().table("order_files")
                .select("id,kind,path,filename,uploaded_by,created_at")
                .eq("po_no", po_no).order("created_at").execute().data or [])
    except Exception:  # noqa: BLE001
        return []
    return [{"id": r["id"], "kind": r["kind"], "filename": r.get("filename"), "url": _sign(r["path"]),
             "by": r.get("uploaded_by")} for r in rows]


def pending_orders(overdue_days: int = 30) -> list[dict]:
    """Orders raised but not fully received (goods on the way), oldest first; overdue flagged.
    Uses v_purchase_lifecycle (Stock-ledger receipts) so it doesn't depend on the MRN XML upload."""
    rows = exec_sql(
        "SELECT po_no, MAX(po_date) AS po_date, MAX(vendor) AS vendor, "
        "COUNT(*) FILTER (WHERE status='on_order') AS on_order_lines, COUNT(*) AS lines, "
        "(CURRENT_DATE - MAX(po_date)) AS days_waiting "
        "FROM v_purchase_lifecycle GROUP BY po_no "
        "HAVING COUNT(*) FILTER (WHERE status='on_order') > 0 "
        "ORDER BY MAX(po_date) ASC LIMIT 30") or []
    for r in rows:
        r["overdue"] = int(r.get("days_waiting") or 0) > overdue_days
    return rows


def order_attention(limit: int = 8) -> list[dict]:
    """Latest orders flagged for attention: PO≠MRN reconciliation issues or thin margin-on-arrival."""
    recent = exec_sql("SELECT po_no FROM purchase_orders GROUP BY po_no "
                      f"ORDER BY MAX(po_date) DESC LIMIT {int(limit)}") or []
    out: list[dict] = []
    for r in recent:
        d = detail(r["po_no"], with_files=False)
        if not d.get("found"):
            continue
        rec = d["reconciliation"]
        issues = (len(rec["short"]) + len(rec["over"]) + len(rec["cost_overrun"])
                  + len(rec["not_received"]) + len(rec["not_ordered"]))
        thin = d["margin"]["thin_items"]
        if issues or thin:
            out.append({"po_no": d["po_no"], "vendor": d["vendor"], "status": d["status"],
                        "margin_pct": d["margin"]["order_margin_pct"], "recon": rec["summary"],
                        "thin": thin})
    return out


def detail(po_no: str, with_files: bool = True) -> dict:
    p = str(po_no or "").strip()[:80]
    if not p:
        return {"po_no": po_no, "found": False, "summary": "No order number given."}

    header = (exec_sql_params(
        "SELECT po_no, MAX(po_date) AS po_date, MAX(vendor) AS vendor, "
        "ROUND(SUM(gross_bhd)::numeric,3) AS ordered_value_bhd, COUNT(*) AS line_count "
        "FROM purchase_orders WHERE po_no = $1 GROUP BY po_no", [p]) or [{}])
    head = header[0] if header else {}
    if not head.get("po_no"):
        return {"po_no": po_no, "found": False, "summary": f"No order {po_no} on file."}

    ordered = exec_sql_params(
        "SELECT code, MAX(description) AS description, SUM(qty) AS ordered_qty, "
        "ROUND((SUM(gross_bhd)/NULLIF(SUM(qty),0))::numeric,4) AS ordered_rate "
        "FROM v_po_item WHERE po_no = $1 GROUP BY code", [p]) or []

    received = exec_sql_params(
        "SELECT SPLIT_PART(sku_code,' ',1) AS code, SUM(qty) AS recv_qty, "
        "ROUND((SUM(qty*landed_unit_bhd)/NULLIF(SUM(qty),0))::numeric,4) AS landed_unit, "
        "ROUND((SUM(qty*product_unit_bhd)/NULLIF(SUM(qty),0))::numeric,4) AS product_unit "
        "FROM mrn_lines WHERE doc_no = $1 GROUP BY SPLIT_PART(sku_code,' ',1)", [p]) or []
    recv_by = {r["code"]: r for r in received}

    # selling price per leading code — the AUTHORITATIVE MA Seller Price Book base rate (the list
    # price you sell at), with the realized sales average as a fallback for anything not in the book.
    codes = sorted({r["code"] for r in ordered} | set(recv_by))
    sell_by: dict[str, float] = {}
    if codes:
        names = json.dumps(codes)
        for r in exec_sql_params(
            # Match on the BASE code (first token): the price book lists variants like 'X33 CCL 1.2 Mtr'
            # while an order line is the base 'X33', so an exact match silently missed those prices.
            "SELECT SPLIT_PART(sku_code,' ',1) AS code, MAX(rate_bhd) AS sell FROM selling_prices "
            "WHERE price_book='MA_base' AND warehouse_name IS NULL AND rate_bhd > 0 "
            "AND SPLIT_PART(sku_code,' ',1) IN (SELECT jsonb_array_elements_text($1::jsonb)) "
            "GROUP BY SPLIT_PART(sku_code,' ',1)", [names]) or []:
            if r.get("sell"):
                sell_by[r["code"]] = float(r["sell"])
        missing = [c for c in codes if c not in sell_by]
        if missing:
            mn = json.dumps(missing)
            for r in exec_sql_params(
                "SELECT SPLIT_PART(item_name,' ',1) AS code, "
                "ROUND((SUM(net_bhd)/NULLIF(SUM(quantity),0))::numeric,4) AS sell_unit "
                "FROM v_sales WHERE SPLIT_PART(item_name,' ',1) IN (SELECT jsonb_array_elements_text($1::jsonb)) "
                "GROUP BY SPLIT_PART(item_name,' ',1)", [mn]) or []:
                if r.get("sell_unit"):
                    sell_by[r["code"]] = float(r["sell_unit"])

    # VFAN supplier price (¥, from the proforma invoice) → BHD landed ESTIMATE, used as the cost basis
    # when an order has no MRN landed cost yet, so margin still calculates from the invoice you uploaded.
    vfan_by: dict[str, float] = {}
    if codes:
        from app.settings import all_settings, rmb_to_bhd
        landed_factor = rmb_to_bhd() * (1 + all_settings()["landing_vat_pct"])  # ¥→BHD landed
        for r in (exec_sql_params(
                "SELECT model, latest_rmb FROM v_supplier_price_history "
                "WHERE model IN (SELECT jsonb_array_elements_text($1::jsonb)) AND latest_rmb > 0",
                [json.dumps(codes)]) or []):
            vfan_by[r["model"]] = round(float(r["latest_rmb"]) * landed_factor, 4)

    any_recv = bool(received)
    ordered_codes = {o["code"] for o in ordered}
    lines: list[dict] = []

    # 1) ORDERED items — matched to what was received (or not)
    for o in ordered:
        code = o["code"]
        rv = recv_by.get(code, {})
        oq = float(o.get("ordered_qty") or 0)
        rq = float(rv.get("recv_qty") or 0)
        orate = float(o.get("ordered_rate") or 0)
        landed = float(rv.get("landed_unit") or 0)
        product = float(rv.get("product_unit") or 0)        # supplier price at receipt (pre-freight)
        sell = sell_by.get(code, 0.0)
        # cost basis: real landed cost (MRN) if we have it, else the VFAN ¥ estimate from the invoice
        cost_basis = landed or vfan_by.get(code) or 0.0
        cost_estimated = bool(not landed and vfan_by.get(code))
        margin = ((sell - cost_basis) / sell * 100) if (sell and cost_basis) else None
        # cost variance compares like-for-like: PO rate vs receipt SUPPLIER price (both pre-freight),
        # so the freight loading isn't mistaken for an overrun.
        cost_var = ((product - orate) / orate * 100) if (orate and product) else None
        flags = []
        if cost_estimated:
            flags.append("cost_estimated")
        if not rv:
            if any_recv:
                flags.append("not_received")       # order received, but this item didn't arrive
        elif rq < oq:
            flags.append("short")
        elif rq > oq:
            flags.append("over")
        if cost_var is not None and abs(cost_var) > 5:
            flags.append("cost_overrun" if cost_var > 0 else "cost_drop")
        if margin is not None and margin < 20:
            flags.append("thin_margin")
        lines.append({
            "code": code, "description": (o.get("description") or "").strip(),
            "ordered_qty": oq, "ordered_rate": orate,
            "recv_qty": rq if rv else None, "landed_unit": (cost_basis or None),
            "cost_estimated": cost_estimated,
            "sell_unit": sell or None, "margin_pct": round(margin, 1) if margin is not None else None,
            "cost_variance_pct": round(cost_var, 1) if cost_var is not None else None,
            "flags": flags,
        })

    # 2) RECEIVED-but-NOT-ORDERED items — substitutions/extras added when raising the MRN
    for code, rv in recv_by.items():
        if code in ordered_codes:
            continue
        rq = float(rv.get("recv_qty") or 0)
        landed = float(rv.get("landed_unit") or 0)
        sell = sell_by.get(code, 0.0)
        margin = ((sell - landed) / sell * 100) if (sell and landed) else None
        flags = ["not_ordered"]
        if margin is not None and margin < 20:
            flags.append("thin_margin")
        lines.append({
            "code": code, "description": code,
            "ordered_qty": None, "ordered_rate": None,
            "recv_qty": rq, "landed_unit": landed if landed else None,
            "sell_unit": sell or None, "margin_pct": round(margin, 1) if margin is not None else None,
            "cost_variance_pct": None, "flags": flags,
        })

    # an MRN document on file is proof of receipt even if its cost lines aren't loaded (PDF-only)
    has_mrn_doc = False
    try:
        has_mrn_doc = bool(get_client().table("order_files").select("id")
                           .eq("po_no", po_no).eq("kind", "mrn").limit(1).execute().data)
    except Exception:  # noqa: BLE001
        pass
    # an MRN (line data OR a filed document) means the goods arrived → received. PO-vs-MRN line
    # differences (short / substituted) are shown in the reconciliation banner, not the status.
    status = "received" if (any_recv or has_mrn_doc) else "pending"
    cost_pending = has_mrn_doc and not any_recv

    # margin-on-arrival: use received qty where known, else the ordered qty (estimate from the invoice)
    def _q(l):
        return l["recv_qty"] if l["recv_qty"] is not None else (l["ordered_qty"] or 0)
    rev = sum(_q(l) * (l["sell_unit"] or 0) for l in lines if l["sell_unit"] and l["landed_unit"])
    cogs = sum(_q(l) * (l["landed_unit"] or 0) for l in lines if l["sell_unit"] and l["landed_unit"])
    order_margin = ((rev - cogs) / rev * 100) if rev else None
    thin = [l["code"] for l in lines if "thin_margin" in l["flags"]]
    short = [l["code"] for l in lines if "short" in l["flags"]]
    over = [l["code"] for l in lines if "over" in l["flags"]]
    overrun = [l["code"] for l in lines if "cost_overrun" in l["flags"]]
    not_received = [l["code"] for l in lines if "not_received" in l["flags"]]
    not_ordered = [l["code"] for l in lines if "not_ordered" in l["flags"]]

    recon_bits = []
    if short:
        recon_bits.append(f"{len(short)} short-received")
    if not_received:
        recon_bits.append(f"{len(not_received)} ordered but not received")
    if over:
        recon_bits.append(f"{len(over)} over-received")
    if not_ordered:
        recon_bits.append(f"{len(not_ordered)} received but not on the PO")
    if overrun:
        recon_bits.append(f"{len(overrun)} cost overrun vs order")
    recon_summary = ("PO vs MRN: " + "; ".join(recon_bits)) if recon_bits else (
        "Matches the PO exactly." if any_recv else
        ("MRN on file — add the XML to reconcile line items & cost." if has_mrn_doc
         else "Awaiting receipt (MRN)."))

    # linked 8-stage pipeline order + timeline (if this PO went through the cockpit)
    timeline, stage = [], None
    try:
        link = exec_sql_params(
            "SELECT id, stage FROM procurement_orders WHERE po_no = $1 ORDER BY id DESC LIMIT 1", [p]) or []
        if link:
            stage = link[0].get("stage")
            from app.procurement import get_order
            timeline = (get_order(int(link[0]["id"])) or {}).get("events", [])
    except Exception:  # noqa: BLE001
        pass

    return {
        "found": True,
        "po_no": head.get("po_no"), "po_date": head.get("po_date"), "vendor": head.get("vendor"),
        "ordered_value_bhd": float(head.get("ordered_value_bhd") or 0), "line_count": head.get("line_count"),
        "status": status, "stage": stage, "cost_pending": cost_pending,
        "lines": lines,
        "reconciliation": {"short": short, "over": over, "cost_overrun": overrun,
                           "not_received": not_received, "not_ordered": not_ordered, "summary": recon_summary},
        "margin": {"order_margin_pct": round(order_margin, 1) if order_margin is not None else None,
                   "estimated": any(l.get("cost_estimated") for l in lines),
                   "thin_items": thin,
                   "summary": ((f"~{order_margin:.0f}% margin"
                                + (" (estimated from the invoice — add the MRN XML to confirm)"
                                   if any(l.get("cost_estimated") for l in lines) else " on this shipment")
                                + (f"; thin: {', '.join(thin[:4])}" if thin else "")) if order_margin is not None
                               else ("Received — add the MRN XML or a VFAN invoice to load cost & margin."
                                     if cost_pending else "Margin shows once priced."))},
        "timeline": timeline,
        "files": files(po_no) if with_files else [],
        "draft_message": (f"Hi {head.get('vendor') or 'team'}, following up on our order "
                          f"{head.get('po_no')} ({head.get('line_count')} item(s)). Could you confirm "
                          f"the status and expected dispatch date? Thank you."),
        "summary": (f"{head.get('po_no')} · {head.get('vendor') or 'vendor'} · {status}"
                    + (f" · ~{order_margin:.0f}% margin" if order_margin is not None else "")),
    }
