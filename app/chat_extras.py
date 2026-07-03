"""Chat extras — product-photo cards and uploaded-document Q&A.

Photo path is fully deterministic (no LLM): "show me a picture of T02" looks the item
up in v_catalog and returns a photo context-card in the SAME ⟦card:…⟧ marker format
the orchestrator already streams, so the frontend needs no new protocol.

Document path: the owner uploads a PDF/Excel/CSV in the Assistant; text is extracted
server-side, kept 24h in chat_uploads (owner-scoped), and answered over directly by
the synthesis LLM — the document NEVER touches the SQL generator.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from app.database import get_client
from app.db_read import exec_sql_params

log = logging.getLogger(__name__)

# ── product photo cards ────────────────────────────────────────────────────────

_PHOTO_INTENT = re.compile(r"\b(photo|picture|image|pic|pics|show (me )?(the )?)\b|صور[ةه]?", re.I)
_CODE_TOKEN = re.compile(r"\b[A-Za-z]{1,3}-?\d+[A-Za-z0-9-]*\b")


def _item_card(it: dict) -> dict:
    metrics: dict = {}
    if it.get("rrp") is not None:
        metrics["rrp_bhd"] = float(it["rrp"])
    if it.get("standard_rate") is not None:
        metrics["book_bhd"] = float(it["standard_rate"])
    return {
        "kind": "item", "agent": "catalog",
        "title": it.get("item_code") or "",
        "summary": (it.get("spec") or it.get("display_name") or "").split("\n")[0][:140],
        "metrics": metrics, "rows": [], "delta": None,
        "image_url": it.get("product_image_url"),
        "package_image_url": it.get("package_image_url"),
    }


def _marker(cards: list[dict]) -> str:
    payload = json.dumps(cards, default=str)
    return "⟦card:" + base64.b64encode(payload.encode("utf-8")).decode("ascii") + "⟧"


def photo_answer(question: str) -> str | None:
    """Deterministic 'send me the picture of X' reply (text + photo card), or None."""
    if not _PHOTO_INTENT.search(question):
        return None
    tokens = [t.upper() for t in _CODE_TOKEN.findall(question)][:4]
    rows: list[dict] = []
    if tokens:
        rows = exec_sql_params(
            "SELECT item_code, display_name, spec, rrp, standard_rate, "
            "product_image_url, package_image_url FROM v_catalog "
            "WHERE is_active AND product_image_url IS NOT NULL AND ("
            "  item_code = ANY(SELECT jsonb_array_elements_text($1::jsonb))"
            "  OR item_code ILIKE (SELECT jsonb_array_elements_text($1::jsonb) LIMIT 1) || ' %'"
            ") LIMIT 3", [json.dumps(tokens)]) or []
    if not rows:
        # fall back to a name search over the remaining words ("power bank", "airpord")
        words = re.sub(r"[^a-z0-9 ]", " ", question.lower())
        for stop in ("photo", "picture", "image", "pic", "show", "me", "the", "of", "a",
                     "send", "please", "can", "you", "product"):
            words = re.sub(rf"\b{stop}\b", " ", words)
        needle = " ".join(words.split())[:40].strip()
        if len(needle) >= 3:
            rows = exec_sql_params(
                "SELECT item_code, display_name, spec, rrp, standard_rate, "
                "product_image_url, package_image_url FROM v_catalog "
                "WHERE is_active AND product_image_url IS NOT NULL "
                "AND (display_name ILIKE '%' || $1 || '%' OR spec ILIKE '%' || $1 || '%') "
                "LIMIT 3", [needle]) or []
    if not rows:
        return None
    cards = [_item_card(r) for r in rows]
    names = ", ".join(f"**{r['item_code']}**" for r in rows)
    text = (f"Here {'it is' if len(rows) == 1 else 'they are'} — {names}. "
            "Tap the card to view; use Catalog → Share to send it to a customer with prices.")
    return _marker(cards) + "\n" + text


# ── uploaded documents ─────────────────────────────────────────────────────────

MAX_DOC_CHARS = 12_000


def extract_text(data: bytes, filename: str) -> str:
    """Text from pdf / xlsx / csv / txt — capped so a big file can't blow the context."""
    name = (filename or "").lower()
    try:
        if name.endswith(".pdf"):
            import pdfplumber
            out = []
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                for page in pdf.pages[:20]:
                    out.append(page.extract_text() or "")
                    for tbl in page.extract_tables() or []:
                        out.append("\n".join(" | ".join(str(c or "") for c in row) for row in tbl))
            return "\n".join(out)[:MAX_DOC_CHARS]
        if name.endswith((".xlsx", ".xls")):
            import pandas as pd
            sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, header=None)
            out = []
            for sheet, df in list(sheets.items())[:5]:
                out.append(f"## Sheet: {sheet}\n" + df.to_csv(index=False, header=False))
            return "\n".join(out)[:MAX_DOC_CHARS]
        if name.endswith(".csv"):
            return data.decode("utf-8", "replace")[:MAX_DOC_CHARS]
        return data.decode("utf-8", "replace")[:MAX_DOC_CHARS]
    except Exception as e:  # noqa: BLE001
        log.warning("doc extract failed for %s: %s", filename, e)
        return ""


def store_doc(user_email: str, filename: str, text: str) -> str | None:
    try:
        r = get_client().table("chat_uploads").insert({
            "user_email": user_email, "filename": filename, "text_content": text,
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
        }).execute()
        return (r.data or [{}])[0].get("id")
    except Exception as e:  # noqa: BLE001
        log.warning("store_doc failed: %s", e)
        return None


def fetch_doc(doc_id: str, user_email: str) -> dict | None:
    """Owner-scoped + unexpired, or None."""
    try:
        r = get_client().table("chat_uploads").select("id,filename,text_content,expires_at") \
            .eq("id", doc_id).eq("user_email", user_email).limit(1).execute().data
        d = (r or [None])[0]
        if not d:
            return None
        if str(d.get("expires_at", "")) < datetime.now(timezone.utc).isoformat():
            return None
        return d
    except Exception:  # noqa: BLE001
        return None


def answer_doc_stream(question: str, doc: dict, history: list[dict] | None = None,
                      model_name: str | None = None):
    """Stream an answer grounded ONLY in the uploaded document (+ chat history)."""
    from app.llm_router import chat_stream
    from app.prompt_guard import FENCE_RULE, fence
    msgs = [{
        "role": "system",
        "content": (
            "You are YQ Bahrain's business analyst. Answer the question using ONLY the "
            "uploaded document below (a supplier invoice, price list, statement, or similar). "
            "Quote figures exactly; use BHD formatting where relevant; say plainly when the "
            "document doesn't contain the answer. Be concise. " + FENCE_RULE
        ),
    }]
    for h in (history or [])[-4:]:
        msgs.append({"role": str(h.get("role", "user")), "content": str(h.get("content", ""))[:400]})
    msgs.append({
        "role": "user",
        "content": (f"Question: {question}\n\nUPLOADED DOCUMENT «{doc.get('filename')}»:\n"
                    + fence(str(doc.get("text_content") or ""), "document")),
    })
    yield from chat_stream(msgs, tier=2, temperature=0.2, max_tokens=700,
                           model_name=model_name, task="doc_qa", request_timeout=20)
