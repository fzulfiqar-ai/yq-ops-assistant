"""FastAPI app — Phase 2 / 2.5 / 3.

Endpoints:
  GET  /health                    — liveness (no auth)
  POST /ask                       — AI query (auth)
  GET  /llm/health                — LLM provider status (auth)
  GET  /digest/daily              — daily ops summary (auth)
  GET  /digest/alerts             — low-stock + overdue + margin alerts (auth)
  POST /action                    — submit pending action (auth)
  GET  /actions                   — list actions (auth)
  PATCH /actions/{id}/approve     — approve action (auth)
  PATCH /actions/{id}/reject      — reject action (auth)
  GET  /actions/export            — download approved CSV (auth)
  POST /ingest                    — upload Focus Excel → auto-ingest (auth)
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, File, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.audit import log_event
from app.auth import CurrentUser, get_caller, get_current_user, require_admin
from app.config import settings

limiter = Limiter(key_func=get_remote_address, default_limits=[settings.rate_limit])

app = FastAPI(title="YQ Bahrain Ops Assistant", version="0.3.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Local dev origins are always allowed; production origins come from ALLOWED_ORIGINS.
# Include Vite's fallback ports (5174/5175) so a busy 5173 doesn't break CORS for /me.
_DEV_ORIGINS = [
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:5174", "http://127.0.0.1:5174",
    "http://localhost:5175", "http://127.0.0.1:5175",
    "http://localhost:8501",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(set(settings.allowed_origins) | set(_DEV_ORIGINS)),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _warm_embeddings() -> None:
    """Warm the local embedding model in a background thread so the first chat that uses
    semantic memory isn't slowed by the one-time model load."""
    import threading

    def _warm():
        try:
            from app.embeddings import available
            available()
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=_warm, daemon=True).start()


@app.get("/health")
@limiter.limit(settings.rate_limit)
async def health(request: Request) -> dict:
    return {"status": "ok", "service": "yq-ops-assistant", "version": app.version}


class AskRequest(BaseModel):
    question: str
    model: str | None = None


@app.post("/ask")
@limiter.limit(settings.rate_limit)
async def ask(request: Request, body: AskRequest, user: CurrentUser = Depends(get_current_user)) -> dict:
    from app.ai import ask as ai_ask
    from app.auth import feature_set
    return ai_ask(body.question, user_email=user.email, model_name=body.model,
                  allowed_features=feature_set(user))


@app.post("/ask/stream")
@limiter.limit(settings.rate_limit)
async def ask_stream_endpoint(request: Request, body: AskRequest, user: CurrentUser = Depends(get_current_user)):
    """Token-streaming answer — yields text chunks as they're produced."""
    from fastapi.responses import StreamingResponse
    from app.ai import ask_stream
    from app.auth import feature_set
    feats = feature_set(user)

    def gen():
        try:
            yield from ask_stream(body.question, user_email=user.email, model_name=body.model,
                                  allowed_features=feats)
        except Exception:  # noqa: BLE001
            yield "\nSomething went wrong. Please try again."

    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class OrchestrateRequest(BaseModel):
    question: str
    model: str | None = None
    history: list[dict] | None = None


@app.post("/orchestrate")
@limiter.limit(settings.rate_limit)
async def orchestrate_endpoint(request: Request, body: OrchestrateRequest,
                               user: CurrentUser = Depends(get_current_user)) -> dict:
    """Agentic entry point — routes to specialist agents, synthesizes one briefing."""
    from app.orchestrator import orchestrate
    result = orchestrate(body.question, user, history=body.history, model_name=body.model)
    log_event(user.email, "orchestrate", detail={"mode": result.get("mode"), "agents": result.get("agents_used")})
    return result


@app.post("/orchestrate/stream")
@limiter.limit(settings.rate_limit)
async def orchestrate_stream_endpoint(request: Request, body: OrchestrateRequest,
                                      user: CurrentUser = Depends(get_current_user)):
    """Streaming agentic briefing: routing preamble → per-agent headlines → synthesis."""
    from fastapi.responses import StreamingResponse
    from app.orchestrator import orchestrate_stream
    log_event(user.email, "orchestrate_stream", detail={})

    def gen():
        try:
            yield from orchestrate_stream(body.question, user, history=body.history, model_name=body.model)
        except Exception:  # noqa: BLE001
            yield "\nSomething went wrong. Please try again."

    return StreamingResponse(gen(), media_type="text/plain; charset=utf-8",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/search")
async def global_search(q: str = "", _user: CurrentUser = Depends(get_current_user)) -> list:
    """Global ⌘K search across customers, items and salesmen."""
    from app.reports import search
    return search(q)


@app.post("/agents/{name}/draft-actions")
async def draft_agent_actions(name: str, admin: CurrentUser = Depends(require_admin)) -> dict:
    """Draft pending actions (or bilingual reminders) from an agent — admin only, human-approved.
    Drafted actions land in the existing pending-actions queue (approve/reject/export)."""
    from app.agent_actions import draft_for_agent, draft_reminders
    if name == "collections":
        result = draft_reminders()
        log_event(admin.email, "draft_reminders", detail={"count": result.get("count")})
        return result
    result = draft_for_agent(name, requested_by=admin.email)
    log_event(admin.email, "draft_actions", detail={"agent": name, "drafted": result.get("drafted")})
    return result


@app.get("/report/{key}")
async def report(key: str, user: CurrentUser = Depends(get_current_user)):
    """Read-only data for a portal page, gated by the matching feature."""
    from fastapi import HTTPException
    from app.auth import has_feature
    from app.reports import REPORTS, REPORT_FEATURE
    if key not in REPORTS:
        raise HTTPException(status_code=404, detail=f"Unknown report '{key}'.")
    if not has_feature(user, REPORT_FEATURE[key]):
        raise HTTPException(status_code=403, detail=f"Requires access to '{REPORT_FEATURE[key]}'.")
    return REPORTS[key]()


@app.get("/llm/health")
async def llm_health(_user: CurrentUser = Depends(get_current_user)) -> dict:
    from app.llm_router import health
    return {"providers": health()}


@app.get("/digest/daily")
async def digest_daily(_caller: CurrentUser = Depends(get_caller)) -> dict:
    from app.digest import daily_summary
    return daily_summary()


@app.get("/digest/alerts")
async def digest_alerts(_caller: CurrentUser = Depends(get_caller)) -> dict:
    from app.digest import all_alerts
    return all_alerts()


@app.get("/escalation/check")
def escalation_check(send: bool = True, _caller: CurrentUser = Depends(get_caller)) -> dict:
    """Evaluate the escalation rules and fire the freshly-triggered ones (deduped 24h) to
    email + Telegram. Schedulers (n8n) call this hourly with X-Agent-Key. `send=false` previews."""
    from app.escalation import check
    return check(send=send)


@app.get("/escalation/brief")
def escalation_brief(send: bool = True, _caller: CurrentUser = Depends(get_caller)) -> dict:
    """Run every agent and send ONE combined morning briefing (email + Telegram). Schedulers
    (n8n) call this each morning with X-Agent-Key. `send=false` previews without sending."""
    from app.escalation import daily_brief
    return daily_brief(send=send)


class ScheduleRequest(BaseModel):
    cadence: str  # off | daily | weekly


@app.get("/schedules")
def schedules_list(_admin: CurrentUser = Depends(require_admin)) -> dict:
    """{agent: cadence} for the per-agent scheduler."""
    from app.schedules import get_schedules
    return get_schedules()


@app.post("/schedules/{name}")
def schedule_set(name: str, body: ScheduleRequest, admin: CurrentUser = Depends(require_admin)) -> dict:
    from app.schedules import set_schedule
    r = set_schedule(name, body.cadence, by=admin.email)
    log_event(admin.email, "agent.schedule", detail=r)
    return r


@app.get("/scheduler/run-due")
def scheduler_run_due(send: bool = True, _caller: CurrentUser = Depends(get_caller)) -> dict:
    """Called hourly by n8n: runs + emails the agents due now (08:00 Bahrain, idempotent per day)."""
    from app.schedules import run_due
    return run_due(send=send)


class FieldNoteRequest(BaseModel):
    note: str
    category: str = "other"
    image_path: str | None = None


@app.post("/field-notes")
def field_note_add(body: FieldNoteRequest, user: CurrentUser = Depends(get_current_user)) -> dict:
    """Capture a rep's field observation (+ optional photo); stored + embedded into RAG for recall."""
    from app.field_notes import add_note
    r = add_note(body.note, body.category, by=user.email, image_path=body.image_path)
    log_event(user.email, "field_note", detail={"category": r.get("category"), "ok": r.get("ok"),
                                                "photo": bool(body.image_path)})
    return r


@app.post("/field-notes/photo")
async def field_note_photo(file: UploadFile = File(...), user: CurrentUser = Depends(get_current_user)) -> dict:
    """Upload a field-note photo (reps snap a competitor tag / empty shelf on their phone).
    Validated + stored in a PRIVATE bucket; returns the object path to attach to the note."""
    from app.uploads import MAX_PHOTO_BYTES, PHOTO_TYPES, UploadTooLarge, content_matches, photo_ext, read_capped
    ext = photo_ext(file.filename or "")
    if not ext:
        return {"error": "Please upload a photo (JPG, PNG, WEBP or HEIC)."}
    try:
        data = await read_capped(file, MAX_PHOTO_BYTES)
    except UploadTooLarge as e:
        return {"error": f"Photo too large ({e})."}
    if not content_matches(file.filename or "", data):
        return {"error": "That file isn't a valid image."}
    from app.field_notes import upload_photo
    path = upload_photo(data, ext, PHOTO_TYPES[ext])
    if not path:
        return {"error": "Could not save the photo — try again."}
    log_event(user.email, "field_note_photo", detail={"ext": ext, "bytes": len(data)})
    return {"image_path": path}


@app.get("/field-notes")
def field_notes_list(_user: CurrentUser = Depends(get_current_user)) -> list:
    from app.field_notes import list_notes
    return list_notes()


@app.post("/purchase-orders/upload")
async def po_upload(file: UploadFile = File(...), admin: CurrentUser = Depends(require_admin)) -> dict:
    """Upload a Focus Purchase Order PDF → parse + load. Powers the Orders page (no scripts)."""
    from app.uploads import UploadTooLarge, content_matches, read_capped
    if not (file.filename or "").lower().endswith(".pdf"):
        return {"error": "Please upload a Focus Purchase Order PDF (.pdf)."}
    try:
        data = await read_capped(file)
    except UploadTooLarge as e:
        return {"error": f"File too large ({e})."}
    if not content_matches(file.filename or "", data):
        return {"error": "That file isn't a valid PDF."}
    try:
        from scripts.ingest_po import load_pos, parse_po_bytes
        rows = parse_po_bytes(data, file.filename or "upload.pdf")
    except Exception as e:  # noqa: BLE001
        return {"error": f"Could not read the PDF ({type(e).__name__})."}
    if not rows:
        return {"error": "No PO lines found — is this a Focus Purchase Order PDF? "
                         "(Proforma / packing list: open the order and use 'Attach file'.)"}
    load_pos(rows)
    try:  # keep the PO PDF in the order's file vault
        from app.orders import store_order_file
        store_order_file(rows[0]["po_no"], "po", data, ".pdf", "application/pdf", admin.email)
    except Exception:  # noqa: BLE001
        pass
    log_event(admin.email, "po_upload", detail={"po_no": rows[0]["po_no"], "lines": len(rows)})
    return {"po_no": rows[0]["po_no"], "po_date": rows[0]["po_date"], "vendor": rows[0]["vendor"],
            "lines": len(rows), "value_bhd": round(sum((r.get("gross_bhd") or 0) for r in rows), 3)}


@app.post("/material-receipts/upload")
async def mrn_upload(file: UploadFile = File(...), admin: CurrentUser = Depends(require_admin)) -> dict:
    """Upload a Focus Material Receipt Note XML (Transactions_*.xml) → update the REAL landed costs
    (StockValue ÷ Qty, all freight in), keyed on the full ProdCode. Margins recompute on next read."""
    from app.uploads import UploadTooLarge, read_capped
    if not (file.filename or "").lower().endswith(".xml"):
        return {"error": "Please upload a Focus MRN export (.xml)."}
    try:
        data = await read_capped(file)
    except UploadTooLarge as e:
        return {"error": f"File too large ({e})."}
    if not data.lstrip()[:1] == b"<":
        return {"error": "That file isn't valid XML."}
    try:
        from scripts.ingest_mrn import load_mrn_costs, parse_mrn_bytes
        rows = parse_mrn_bytes(data)
    except Exception as e:  # noqa: BLE001
        return {"error": f"Could not read the MRN XML ({type(e).__name__})."}
    if not rows:
        return {"error": "No received lines found — is this a Focus MRN export?"}
    summary = load_mrn_costs(rows)
    try:  # keep the MRN XML in the order's file vault
        from app.orders import store_order_file
        for doc in summary.get("docs", []):
            store_order_file(doc, "mrn", data, ".xml", "application/xml", admin.email)
    except Exception:  # noqa: BLE001
        pass
    try:  # costs changed → flush cached query results so margins refresh
        from app.ai import flush_cache
        flush_cache()
    except Exception:  # noqa: BLE001
        pass
    log_event(admin.email, "mrn_upload", detail={"docs": summary["docs"], "skus": summary["skus"]})
    return summary


@app.post("/orders/{po_no}/photo")
async def order_photo(po_no: str, file: UploadFile = File(...),
                      user: CurrentUser = Depends(get_current_user)) -> dict:
    """Attach a shelf/shipment photo to an order (rear camera on mobile). Stored in the order vault."""
    from app.uploads import (MAX_PHOTO_BYTES, PHOTO_TYPES, UploadTooLarge,
                             content_matches, photo_ext, read_capped)
    ext = photo_ext(file.filename or "")
    if not ext:
        return {"error": "Please upload a photo (JPG, PNG, WEBP or HEIC)."}
    try:
        data = await read_capped(file, MAX_PHOTO_BYTES)
    except UploadTooLarge as e:
        return {"error": f"Photo too large ({e})."}
    if not content_matches(file.filename or "", data):
        return {"error": "That file isn't a valid image."}
    from app.orders import store_order_file
    path = store_order_file(po_no, "photo", data, ext, PHOTO_TYPES[ext], user.email)
    if not path:
        return {"error": "Could not save the photo — try again."}
    log_event(user.email, "order_photo", detail={"po_no": po_no})
    return {"ok": True}


@app.post("/orders/{po_no}/file")
async def order_file(po_no: str, file: UploadFile = File(...),
                     user: CurrentUser = Depends(get_current_user)) -> dict:
    """Attach ANY related document to an order (proforma invoice, packing list, Excel, photo …),
    so everything about the order lives in one place."""
    import mimetypes
    from app.uploads import UploadTooLarge, content_matches, photo_ext, read_capped
    name = file.filename or ""
    low = name.lower()
    ext = "." + low.rsplit(".", 1)[-1] if "." in low else ""
    if ext not in {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".xlsx", ".xls", ".csv", ".xml"}:
        return {"error": "Allowed: PDF, image, Excel/CSV or XML."}
    try:
        data = await read_capped(file)
    except UploadTooLarge as e:
        return {"error": f"File too large ({e})."}
    if not content_matches(name, data):
        return {"error": "That file doesn't match its type."}
    import re as _re
    is_packing = " pl " in f" {low} " or "packing" in low
    if photo_ext(name):
        kind = "photo"
    elif low.startswith("po_") or "purchase order" in low:
        kind = "po"
    elif "mrn" in low or "receipt" in low or ext == ".xml":
        kind = "mrn"
    elif is_packing:
        kind = "doc"                                  # packing list = a document, not an invoice
    elif ext in (".xls", ".xlsx") or "invoice" in low or "proforma" in low or _re.search(r"vf\d{6}", low):
        kind = "invoice"
    else:
        kind = "doc"

    # Smart processing — actually LOAD the data so the order calculates, not just store the file.
    processed = None
    if ext == ".xml":
        try:
            from scripts.ingest_mrn import load_mrn_costs, parse_mrn_bytes
            rows = parse_mrn_bytes(data)
            if rows:
                load_mrn_costs(rows)
                processed = "landed cost loaded"
                from app.ai import flush_cache
                flush_cache()
        except Exception:  # noqa: BLE001
            pass
    elif kind == "invoice":
        try:
            from app.invoices import load_supplier_prices, parse_invoice
            rows = parse_invoice(data, name)
            if rows:
                load_supplier_prices(rows)
                processed = "supplier prices loaded"
        except Exception:  # noqa: BLE001
            pass

    ct = mimetypes.guess_type(name)[0] or "application/octet-stream"
    from app.orders import store_order_file
    path = store_order_file(po_no, kind, data, ext, ct, user.email, filename=name)
    if not path:
        return {"error": "Could not save the file — try again."}
    log_event(user.email, "order_file", detail={"po_no": po_no, "kind": kind, "processed": processed})
    return {"ok": True, "kind": kind, "processed": processed}


@app.post("/orders/attach-doc")
async def attach_loose_doc(file: UploadFile = File(...),
                           user: CurrentUser = Depends(get_current_user)) -> dict:
    """Attach a shipment document (packing list, etc.) dropped on the main page — auto-matched to its
    order by the VFAN invoice number that already appears on one of the order's files."""
    import mimetypes
    import re as _re
    from app.database import get_client
    from app.uploads import UploadTooLarge, read_capped
    name = file.filename or ""
    ext = "." + name.lower().rsplit(".", 1)[-1] if "." in name else ""
    if ext not in {".pdf", ".xls", ".xlsx", ".csv", ".jpg", ".jpeg", ".png", ".webp", ".heic"}:
        return {"error": "Allowed: PDF, Excel/CSV or image."}
    try:
        data = await read_capped(file)
    except UploadTooLarge as e:
        return {"error": f"File too large ({e})."}
    m = _re.search(r"VF\d{6,}", name, _re.I)
    po_no = None
    if m:
        vf = m.group(0)
        row = (get_client().table("order_files").select("po_no")
               .ilike("filename", f"%{vf}%").limit(1).execute().data or [None])[0]
        po_no = row.get("po_no") if row else None
    if not po_no:
        return {"error": "Couldn't match this to an order — open the order and use 'Attach file'."}
    ct = mimetypes.guess_type(name)[0] or "application/octet-stream"
    from app.orders import store_order_file
    store_order_file(po_no, "doc", data, ext, ct, user.email, filename=name)
    log_event(user.email, "attach_doc", detail={"po_no": po_no, "file": name})
    return {"ok": True, "po_no": po_no}


@app.post("/invoices/upload")
async def invoice_upload(file: UploadFile = File(...), admin: CurrentUser = Depends(require_admin)) -> dict:
    """Upload a VFAN proforma invoice (PDF or .xls) → supplier RMB price history (price-change tracking)."""
    from app.uploads import UploadTooLarge, read_capped
    if not (file.filename or "").lower().endswith((".pdf", ".xls", ".xlsx")):
        return {"error": "Please upload a proforma invoice (PDF or Excel)."}
    try:
        data = await read_capped(file)
    except UploadTooLarge as e:
        return {"error": f"File too large ({e})."}
    try:
        from app.invoices import load_supplier_prices, parse_invoice
        rows = parse_invoice(data, file.filename or "invoice")
    except Exception as e:  # noqa: BLE001
        return {"error": f"Could not read the invoice ({type(e).__name__})."}
    if not rows:
        return {"error": "No price lines found — is this a VFAN proforma invoice?"}
    r = load_supplier_prices(rows)
    log_event(admin.email, "invoice_upload", detail={"invoice": r.get("invoice"), "models": r.get("models")})
    return r


@app.get("/supplier-prices")
def supplier_prices(_user: CurrentUser = Depends(get_current_user)) -> dict:
    """Supplier RMB price history per model — latest vs previous invoice, with the change %."""
    from app.ai import exec_sql
    rows = exec_sql("SELECT model, latest_invoice, latest_date, latest_rmb, latest_list_rmb, "
                    "prev_rmb, prev_date, change_pct, invoice_count FROM v_supplier_price_history "
                    "ORDER BY ABS(COALESCE(change_pct,0)) DESC, model LIMIT 300") or []
    changed = [r for r in rows if r.get("change_pct")]
    return {"count": len(rows), "changed_count": len(changed), "rows": rows}


class OrderExportLine(BaseModel):
    model: str | None = None
    spec: str | None = None
    qty: float | None = None
    unit_price_rmb: float | None = None


class OrderExportRequest(BaseModel):
    vendor: str | None = "VFAN"
    order_ref: str | None = None
    lines: list[OrderExportLine] = []


@app.post("/orders/proposal/export")
def order_proposal_export(body: OrderExportRequest,
                          _user: CurrentUser = Depends(get_current_user)) -> Response:
    """Generate the reviewed proposal as a VFAN-format order .xlsx for the owner to send to the vendor."""
    from datetime import date as _date

    from fastapi import HTTPException

    from app.order_sheet import build_order_xlsx
    lines = [ln.model_dump() for ln in body.lines if (ln.model or ln.qty)]
    if not lines:
        raise HTTPException(status_code=400, detail="No lines to export.")
    data = build_order_xlsx(lines, vendor=body.vendor or "VFAN", order_ref=body.order_ref)
    fn = f"YQ Order {(body.vendor or 'VFAN')} {_date.today().isoformat()}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fn}"'},
    )


@app.post("/orders/verify")
async def order_verify(file: UploadFile = File(...),
                       user: CurrentUser = Depends(get_current_user)) -> dict:
    """Verify an order .xlsx (price vs last VFAN, math/discount, margin, qty sanity) — the human gate."""
    from app.uploads import UploadTooLarge, read_capped
    if not (file.filename or "").lower().endswith((".xls", ".xlsx")):
        return {"ok": False, "verdict": "unreadable", "lines": [], "flags": 0,
                "summary": "Upload the order as Excel (.xlsx)."}
    try:
        data = await read_capped(file)
    except UploadTooLarge as e:
        return {"ok": False, "verdict": "unreadable", "lines": [], "flags": 0,
                "summary": f"File too large ({e})."}
    try:
        from app.order_verify import verify_order
        rep = verify_order(data, file.filename or "order.xlsx")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "verdict": "unreadable", "lines": [], "flags": 0,
                "summary": f"Could not read the order ({type(e).__name__})."}
    log_event(user.email, "order_verify", detail={"verdict": rep.get("verdict"), "flags": rep.get("flags")})
    return rep


@app.delete("/orders/{po_no}")
def order_delete(po_no: str, admin: CurrentUser = Depends(require_admin)) -> dict:
    """Admin: remove an order and everything tied to it (PO lines, MRN lines, stored files)."""
    from app.database import get_client
    from app.orders import _BUCKET
    c = get_client()
    try:
        files = c.table("order_files").select("path").eq("po_no", po_no).execute().data or []
        if files:
            try:
                c.storage.from_(_BUCKET).remove([f["path"] for f in files])
            except Exception:  # noqa: BLE001
                pass
        c.table("order_files").delete().eq("po_no", po_no).execute()
        c.table("mrn_lines").delete().eq("doc_no", po_no).execute()
        c.table("purchase_orders").delete().eq("po_no", po_no).execute()
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:140]}
    log_event(admin.email, "order_delete", detail={"po_no": po_no})
    return {"ok": True}


@app.delete("/orders/files/{file_id}")
def order_file_delete(file_id: int, admin: CurrentUser = Depends(require_admin)) -> dict:
    """Admin: remove a single attached file from an order."""
    from app.database import get_client
    from app.orders import _BUCKET
    c = get_client()
    try:
        row = (c.table("order_files").select("path").eq("id", file_id).limit(1).execute().data or [None])[0]
        if row and row.get("path"):
            try:
                c.storage.from_(_BUCKET).remove([row["path"]])
            except Exception:  # noqa: BLE001
                pass
        c.table("order_files").delete().eq("id", file_id).execute()
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:140]}
    log_event(admin.email, "order_file_delete", detail={"file_id": file_id})
    return {"ok": True}


@app.get("/purchase-orders")
def po_list(_user: CurrentUser = Depends(get_current_user)) -> dict:
    """Orders page data: recent orders, per-item cost change across orders, and what's on order."""
    from app.ai import exec_sql

    def q(sql: str) -> list:
        try:
            return exec_sql(sql)
        except Exception:  # noqa: BLE001
            return []

    try:
        from app.agents import reorder_proposal
        proposal = reorder_proposal()
    except Exception:  # noqa: BLE001
        proposal = {"count": 0, "summary": "", "lines": [], "by_vendor": []}

    return {
        "proposal": proposal,
        "recent": q("SELECT o.po_no, MAX(o.po_date) AS po_date, MAX(o.vendor) AS vendor, COUNT(*) AS lines, "
                    "ROUND(SUM(o.gross_bhd)::numeric,3) AS value_bhd, "
                    "(EXISTS(SELECT 1 FROM mrn_lines m WHERE m.doc_no = o.po_no) "
                    " OR EXISTS(SELECT 1 FROM order_files f WHERE f.po_no = o.po_no AND f.kind='mrn')) AS received "
                    "FROM purchase_orders o GROUP BY o.po_no ORDER BY MAX(o.po_date) DESC LIMIT 50"),
        "cost_changes": q("SELECT item_code, description, prev_rate_bhd, current_rate_bhd, "
                          "rate_change_pct, prev_ordered, last_ordered FROM v_po_cost_change "
                          "ORDER BY ABS(rate_change_pct) DESC NULLS LAST LIMIT 40"),
        "on_order": q("SELECT po_no, code, qty_ordered, rate_bhd, po_date FROM v_purchase_lifecycle "
                      "WHERE status = 'on_order' ORDER BY po_date DESC LIMIT 40"),
    }


@app.get("/orders/{po_no}")
def order_detail(po_no: str, _user: CurrentUser = Depends(get_current_user)) -> dict:
    """One order's full record (PO=MRN number): ordered vs received, real landed cost, margin-on-
    arrival, reconciliation flags, and the linked 8-stage pipeline timeline."""
    from app.orders import detail
    return detail(po_no)


# ── Procurement workflow (Phase 3): pipeline board + stage transitions ────────

class ProcOrderRequest(BaseModel):
    title: str
    vendor: str | None = None
    est_value_bhd: float | None = None
    lines: list[dict] | None = None
    note: str | None = None
    stage: str | None = None  # a verified order can open straight at 'reviewed'


class ProcAdvanceRequest(BaseModel):
    stage: str
    note: str | None = None
    po_no: str | None = None


@app.get("/procurement/board")
def procurement_board(_user: CurrentUser = Depends(get_current_user)) -> dict:
    """The procurement pipeline: open orders by stage, days-in-stage, and stuck flags."""
    from app.procurement import board
    return board()


@app.get("/procurement/orders/{order_id}")
def procurement_get(order_id: int, _user: CurrentUser = Depends(get_current_user)) -> dict:
    """One order + its full stage-transition timeline."""
    from app.procurement import get_order
    return get_order(order_id)


@app.post("/procurement/orders")
def procurement_create(body: ProcOrderRequest, admin: CurrentUser = Depends(require_admin)) -> dict:
    """Open a procurement order (e.g. from an AI reorder proposal vendor group)."""
    from app.procurement import create_order
    r = create_order(body.title, body.vendor, body.est_value_bhd, body.lines, body.note,
                     actor=admin.email, stage=body.stage)
    log_event(admin.email, "proc_create", detail={"ref": r.get("ref"), "vendor": body.vendor})
    return r


@app.post("/procurement/orders/{order_id}/advance")
def procurement_advance(order_id: int, body: ProcAdvanceRequest,
                        admin: CurrentUser = Depends(require_admin)) -> dict:
    """Move an order to a new stage (logs the transition + links a Focus PO when given)."""
    from app.procurement import advance
    try:
        r = advance(order_id, body.stage, body.note, body.po_no, actor=admin.email)
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=str(e))
    log_event(admin.email, "proc_advance", detail={"order_id": order_id, "stage": body.stage})
    return r


# ── Lead generation (Tier 2): free B2B prospecting from OpenStreetMap ──────────

class LeadStatusRequest(BaseModel):
    status: str


@app.get("/leads")
def leads_list(status: str | None = None, _user: CurrentUser = Depends(get_current_user)) -> dict:
    """The leads pipeline + the prioritised list (highest-fit first)."""
    from app.leadgen import ATTRIBUTION, list_leads, pipeline
    return {"pipeline": pipeline(), "leads": list_leads(status=status, limit=200), "attribution": ATTRIBUTION}


@app.post("/leads/discover")
def leads_discover(admin: CurrentUser = Depends(require_admin)) -> dict:
    """Discover new B2B leads from OpenStreetMap (free), dedupe vs our customers, score + import."""
    from app.leadgen import discover_and_import
    res = discover_and_import()
    log_event(admin.email, "leads_discover", detail=res)
    return res


@app.post("/leads/{lead_id}/status")
def leads_set_status(lead_id: int, body: LeadStatusRequest,
                     user: CurrentUser = Depends(get_current_user)) -> dict:
    """Advance a lead through the pipeline (new→contacted→visited→quoted→ordered/rejected)."""
    from app.leadgen import set_status
    ok = set_status(int(lead_id), body.status, by=user.email)
    if not ok:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Invalid lead status.")
    return {"ok": True, "status": body.status}


# ── Coaching Brain (Tier 3): per-account pre-visit brief for reps ──────────────

@app.get("/coaching/accounts")
def coaching_accounts(_user: CurrentUser = Depends(get_current_user)) -> list:
    """Named accounts (by revenue) for the rep to pick before a call/visit."""
    from app.coaching import accounts
    return accounts()


@app.get("/coaching/brief")
def coaching_brief(account: str, _user: CurrentUser = Depends(get_current_user)) -> dict:
    """The pre-visit brief: what they buy, owe, what to cross-sell + talking points."""
    from app.coaching import brief
    return brief(account)


@app.get("/agents")
async def agents_list(_caller: CurrentUser = Depends(get_caller)) -> list:
    from app.agents import list_agents
    return list_agents()


@app.get("/agents/{name}")
async def agents_run(name: str, email: bool = False, caller: CurrentUser = Depends(get_caller)) -> dict:
    from app.agents import run_agent
    try:
        result = run_agent(name)
    except KeyError:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Unknown agent '{name}'.")
    if email:
        from app.emailer import send_agent
        result["email"] = send_agent(result)
    log_event(caller.email, "agent", detail={"agent": name, "summary": result.get("summary")})
    return result


@app.get("/me")
async def me(user: CurrentUser = Depends(get_current_user)) -> dict:
    """Identity + access for the SPA: role + granted feature pages."""
    role, features, full_name = user.role, [], ""
    try:
        from app.user_auth import _user_row
        row = _user_row(user.email)
        if row:
            role = row.get("role", role)
            features = row.get("features") or []
            full_name = row.get("full_name") or ""
    except Exception:  # columns may predate the team migration
        pass
    return {"email": user.email, "role": role, "features": features, "full_name": full_name}


# ── Team & access management (admin) ─────────────────────────────────────────

class InviteRequest(BaseModel):
    email: str
    full_name: str = ""
    role: str = "member"
    features: list[str] = []
    method: str = "temp"  # "temp" (set a temp password) | "email" (send a link)


class UpdateAccessRequest(BaseModel):
    role: str | None = None
    features: list[str] | None = None
    status: str | None = None


class AcceptRequest(BaseModel):
    token: str
    password: str
    full_name: str | None = None


@app.get("/team")
async def team_list(_admin: CurrentUser = Depends(require_admin)) -> dict:
    from app.user_auth import list_members
    return list_members()


@app.post("/team/invite")
async def team_invite(body: InviteRequest, admin: CurrentUser = Depends(require_admin)) -> dict:
    from app.user_auth import FEATURES, create_email_invite, create_member, generate_temp_password
    grant = body.features if body.role == "member" else list(FEATURES)
    if body.method == "email":
        res = create_email_invite(body.email, body.full_name, body.role, grant, invited_by=admin.email)
        log_event(admin.email, "team.invite", detail={"email": body.email, "mode": "email"})
        return {"mode": "email", **res}
    tmp = generate_temp_password()
    create_member(body.email, body.full_name, body.role, grant, tmp, invited_by=admin.email, must_reset=True)
    log_event(admin.email, "team.invite", detail={"email": body.email, "mode": "temp"})
    return {"mode": "temp", "email": body.email, "temp_password": tmp}


@app.patch("/team/{email}")
async def team_update(email: str, body: UpdateAccessRequest, admin: CurrentUser = Depends(require_admin)) -> dict:
    from app.user_auth import update_access
    update_access(email, role=body.role, features=body.features, status=body.status)
    log_event(admin.email, "team.update", detail={"email": email})
    return {"ok": True}


@app.delete("/team/{email}")
async def team_remove(email: str, admin: CurrentUser = Depends(require_admin)) -> dict:
    from fastapi import HTTPException
    from app.user_auth import remove_user
    if email.strip().lower() == admin.email:
        raise HTTPException(status_code=400, detail="You cannot remove your own account.")
    remove_user(email)
    log_event(admin.email, "team.remove", detail={"email": email})
    return {"ok": True}


@app.get("/team/invite/{token}")
async def team_invite_info(token: str) -> dict:
    from fastapi import HTTPException
    from app.user_auth import get_invite
    inv = get_invite(token)
    if not inv:
        raise HTTPException(status_code=404, detail="Invalid or expired invite.")
    return {"email": inv["email"], "role": inv["role"], "features": inv.get("features") or []}


@app.post("/team/accept")
async def team_accept(body: AcceptRequest) -> dict:
    from fastapi import HTTPException
    from app.user_auth import accept_invite
    res = accept_invite(body.token, body.password, body.full_name)
    if not res:
        raise HTTPException(status_code=400, detail="Invalid or expired invite.")
    return res


class ActionRequest(BaseModel):
    action_type: str
    payload: dict
    notes: str = ""


@app.post("/action")
async def submit_action(body: ActionRequest, user: CurrentUser = Depends(get_current_user)) -> dict:
    from app.actions import submit_action as _submit
    result = _submit(body.action_type, {**body.payload, "notes": body.notes}, requested_by=user.email)
    log_event(user.email, "action.submit", detail={"action_type": body.action_type})
    return result


@app.get("/actions")
async def list_actions(status: str | None = None, _user: CurrentUser = Depends(get_current_user)) -> list:
    from app.actions import list_actions as _list
    return _list(status=status)


@app.patch("/actions/{action_id}/approve")
async def approve_action(action_id: int, user: CurrentUser = Depends(require_admin)) -> dict:
    from app.actions import approve_action as _approve
    result = _approve(action_id, approved_by=user.email)
    log_event(user.email, "action.approve", detail={"action_id": action_id})
    return result


@app.patch("/actions/{action_id}/reject")
async def reject_action(action_id: int, reason: str = "", user: CurrentUser = Depends(require_admin)) -> dict:
    from app.actions import reject_action as _reject
    result = _reject(action_id, approved_by=user.email, reason=reason)
    log_event(user.email, "action.reject", detail={"action_id": action_id, "reason": reason})
    return result


@app.get("/actions/export")
async def export_actions(_user: CurrentUser = Depends(require_admin)) -> Response:
    from app.actions import export_approved_csv
    return Response(content=export_approved_csv(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=approved_actions.csv"})


@app.get("/ingest/purge-targets")
def ingest_purge_targets(_admin: CurrentUser = Depends(require_admin)) -> dict:
    """The reports that can be purged by date (for the Data-page repair tool)."""
    from app.data_admin import targets
    return {"targets": targets()}


class PurgeRequest(BaseModel):
    report: str
    date_from: str | None = None
    date_to: str | None = None
    blanks: bool = False


@app.post("/ingest/purge")
def ingest_purge(body: PurgeRequest, admin: CurrentUser = Depends(require_admin)) -> dict:
    """Admin: remove a report's rows for a date/range (or its null-date junk) so a wrong upload can be
    deleted and re-uploaded. Flushes the answer cache so the dashboard recomputes."""
    from app.data_admin import PURGE_TARGETS, purge_by_date
    if body.report not in PURGE_TARGETS:
        return {"error": "Unknown report."}
    if not body.blanks and not body.date_from:
        return {"error": "Pick a date, or choose the blank/no-date rows."}
    try:
        n = purge_by_date(body.report, body.date_from, body.date_to, body.blanks)
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:160]}
    try:
        from app.ai import flush_cache
        flush_cache()
    except Exception:  # noqa: BLE001
        pass
    log_event(admin.email, "ingest_purge", detail={
        "report": body.report, "from": body.date_from, "to": body.date_to,
        "blanks": body.blanks, "deleted": n})
    return {"ok": True, "deleted": n, "report": body.report}


@app.post("/ingest")
async def ingest_file(
    files: list[UploadFile] = File(...),
    user: CurrentUser = Depends(require_admin),
) -> dict:
    """Upload one or more Focus exports from the dashboard → one verified refresh + briefing.

    Each upload is staged FRESH (Focus export filenames vary per export, so a persistent folder
    would pile up old reports); data/clean is cleared too so a partial upload never reloads a stale
    table. Upload the full daily set together for a complete refresh; upload a subset to refresh
    just those reports (the rest keep their last-known data in Supabase)."""
    import re
    import shutil
    from pathlib import Path

    from app.uploads import MAX_INGEST_FILES, UploadTooLarge, content_matches, read_capped
    if len(files) > MAX_INGEST_FILES:
        return {"error": f"Too many files (max {MAX_INGEST_FILES}). Upload the daily Focus set."}
    ROOT = Path(__file__).resolve().parents[1]
    staging = ROOT / "data" / "_upload"
    clean = ROOT / "data" / "clean"
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(clean, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for f in files:
        suffix = (Path(f.filename or "upload.xlsx").suffix or ".xlsx").lower()
        if suffix not in (".xlsx", ".xls", ".csv"):
            return {"error": f"{f.filename}: unsupported type '{suffix}'. Use .xlsx, .xls or .csv."}
        try:
            contents = await read_capped(f)
        except UploadTooLarge as e:
            return {"error": f"{f.filename}: too large ({e})."}
        if not content_matches(f.filename or "", contents):
            return {"error": f"{f.filename}: content doesn't match a {suffix} file."}
        # Save under the REAL (sanitized) filename so scripts.ingest classify() recognises the type.
        safe_name = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(f.filename or f"upload{suffix}").name)
        (staging / safe_name).write_bytes(contents)
        saved.append(safe_name)
    # Smart validation: keep only GENUINE Focus reports (filename classify + 'YQ Bahrain' title
    # sniff). Anything else is ignored and reported back — never loaded.
    from scripts.ingest import classify, sniff_focus
    recognised: list[dict] = []
    ignored: list[dict] = []
    for name in saved:
        p = staging / name
        kind = classify(name)
        if kind is None:
            ignored.append({"file": name, "reason": "not a recognised Focus report"})
            p.unlink(missing_ok=True)
        elif kind.startswith("skip:"):
            ignored.append({"file": name, "reason": kind[5:]})
            p.unlink(missing_ok=True)
        elif not kind.startswith("selling_prices") and not sniff_focus(p):
            # Price books (MASellingPriceBook / ModernTradeSellerBook) are reliably classified by
            # filename and legitimately have NO 'YQ Bahrain' title block — exempt them from the
            # Focus-title sniff so a genuine price-book upload isn't wrongly ignored.
            ignored.append({"file": name, "reason": "does not look like a Focus export (no 'YQ Bahrain' title)"})
            p.unlink(missing_ok=True)
        else:
            recognised.append({"file": name, "report": kind})
    if not recognised:
        log_event(user.email, "ingest", detail={"recognised": 0, "ignored": [i["file"] for i in ignored]})
        return {"files": saved, "recognised": [], "ignored": ignored, "ok": False,
                "error": "No recognised Focus reports in the upload — nothing was loaded."}

    # Verified refresh engine: ingest (de-dup by type) -> load -> flush cache -> verify ->
    # what-changed -> briefing -> ingest_runs. (Synchronous; admin-only, infrequent.)
    from scripts.refresh import refresh
    res = refresh(folder=str(staging), send=True)
    log_event(user.email, "ingest", detail={
        "recognised": [r["report"] for r in recognised],
        "ignored": [i["file"] for i in ignored], "ok": res.get("ok"),
    })
    try:
        from app.reports import coverage
        cov = coverage()
    except Exception:
        cov = []
    return {
        "files": saved,
        "recognised": recognised,
        "ignored": ignored,
        "ok": res.get("ok"),
        "data_as_of": res.get("data_as_of"),
        "verify_pass": (res.get("verify") or {}).get("ok"),
        "verify": res.get("verify"),
        "changes": res.get("changes"),
        "error": res.get("error"),
        "coverage": cov,
        "uploaded_by": user.email,
    }


@app.get("/data/coverage")
def data_coverage(user: CurrentUser = Depends(require_admin)) -> list[dict]:
    """Per-report data freshness for the Data-page upload panel (Zoho-style)."""
    from app.reports import coverage
    return coverage()
