"""YQ Assist: the marketplace's AI chat (P1 of the marketplace AI plan, 28-Oct-2026).

One merchant message takes at most two free-model calls through app.llm_router:

  1. UNDERSTAND  chat_json: what the merchant wants (the intent) and the product in the
                 catalogue's English words, whatever language the message is in
  2. HANDLE      Python only, on PUBLIC data: the cached public catalog payload (exactly what the
                 marketplace already shows: never a cost, a margin or a stock quantity) and the
                 orders whose tokens this device holds (the capability the status page uses)
  3. ANSWER      chat: one to three sentences in the merchant's language around the product cards

Order status, "my usual", the hand-off and feedback need no second call: their line comes from the
templates at the end of this file, in the page's language. Prices never come from the model: the
cards show the catalog's own figures, the prompt forbids them, and _scrub_prices() removes any
sentence that still carries one. The model reads the merchant's words masked (phone numbers and
e-mail addresses out) and fenced as data. When no model answers, `fallback` tells the page to run
its own search, so the chat degrades to search and never to an error.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime, timezone

from app import shop
from app.llm_router import AllProvidersFailed, chat, chat_json, last_provider
from app.prompt_guard import FENCE_RULE, fence

log = logging.getLogger(__name__)

INTENTS = ("find", "fit", "add", "order_status", "usual", "handoff", "feedback", "chat")
FEEDBACK_KINDS = ("idea", "problem", "question", "up", "down")
MAX_MESSAGE = 500
MAX_HISTORY = 6
MAX_CARDS = 6
MAX_USUAL = 8
DEFAULT_DAILY_CAP = 800
_LANG_NAMES = {"en": "English", "ar": "Arabic", "ur": "Urdu", "hi": "Hindi", "ml": "Malayalam"}
_STOCK_WORDS = {"in_stock": "in stock", "low_stock": "only a few left", "out_of_stock": "sold out"}


# ── masking: what the merchant typed, before any provider or table sees it ─────────────────────

_INDIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# an order number (YQ-2609-0019, YQ 2609 0019, YQ26090019) is the merchant's own reference: kept
_ORDER_NO = re.compile(r"\bYQ[- ]?[0-9]{4}[- ]?[0-9]{4}\b", re.IGNORECASE)
# a phone or card number: seven digits or more, single spaces or dashes allowed between them
_LONG_NUMBER = re.compile(r"\+?[0-9](?:[ -]?[0-9]){6,}")


def mask(text: str | None) -> str:
    """Phone, card and ID numbers (7+ digits) and e-mail addresses out; order numbers kept."""
    t = str(text or "").translate(_INDIC_DIGITS)
    kept: list[str] = []

    def hold(m: re.Match) -> str:
        kept.append(m.group(0))
        return f"\x00{len(kept) - 1}\x00"

    t = _ORDER_NO.sub(hold, t)
    t = _EMAIL.sub("[email]", t)
    t = _LONG_NUMBER.sub("[number]", t)
    return re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], t)


def _clean_text(s, limit: int) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()[:limit]


# ── the public catalog, parsed once per refresh ────────────────────────────────────────────────

_cat_lock = threading.Lock()
_cat: dict = {"etag": None, "items": [], "by_code": {}, "settings": {}}


def public_catalog() -> dict:
    """{items, by_code (upper-cased), settings}: the public payload every visitor gets, parsed once
    per context refresh. Reading the PUBLIC bytes (not the context) is the guarantee that nothing
    the marketplace does not already show can reach a model."""
    from app.catalog import share_token
    ctx = shop.context()
    tok = ctx.get("share_token") or share_token(create=True)
    entry = shop.public_catalog_entry(tok, None)
    if entry is None:
        return {"items": [], "by_code": {}, "settings": {}}
    with _cat_lock:
        if _cat["etag"] != entry["etag"]:
            data = json.loads(entry["raw"])
            items = data.get("items") or []
            _cat.update(etag=entry["etag"], items=items, settings=data.get("settings") or {},
                        by_code={str(i.get("item_code") or "").upper(): i for i in items})
        return {"items": _cat["items"], "by_code": _cat["by_code"], "settings": _cat["settings"]}


# ── the daily budget (public traffic must never starve the owner's own reports) ────────────────

_cap_lock = threading.Lock()
_cap = {"day": "", "n": 0}


def _daily_cap() -> int:
    raw = (shop.context().get("settings") or {}).get("shop_ai_daily_cap")
    try:
        return max(int(str(raw).strip()), 0)
    except (TypeError, ValueError):
        return DEFAULT_DAILY_CAP


def _take_budget() -> bool:
    """One model-backed turn from today's budget (UTC day, this process). 0 = no cap."""
    cap = _daily_cap()
    day = datetime.now(timezone.utc).date().isoformat()
    with _cap_lock:
        if _cap["day"] != day:
            _cap.update(day=day, n=0)
        if cap and _cap["n"] >= cap:
            return False
        _cap["n"] += 1
        return True


# ── 1. understand ─────────────────────────────────────────────────────────────────────────────

_UNDERSTAND = """You read messages that shop owners send to YQ Bahrain's wholesale marketplace for mobile accessories \
(chargers, cables, car chargers, power banks, wired earphones, Bluetooth headsets and earbuds, speakers, holders).
Messages can be in any language: English, Arabic, Urdu, Hindi, Malayalam or a mix.
Reply with ONE JSON object and nothing else:
{"intent": "...", "query": "...", "codes": [], "qty": null, "lang": "en"}
intent is exactly one of:
- "find": wants a product, asks what we have or what is new
- "fit": asks whether a product fits or works with a phone or device, or about a product's spec
- "add": asks to add or order a quantity of a product
- "order_status": asks about an order already placed (status, delivery, where is my order)
- "usual": wants their usual items or to repeat an earlier order
- "handoff": wants a person, their salesman or representative, a call, or to complain to someone
- "feedback": gives a suggestion or idea, or reports a problem with the website
- "chat": a greeting or thanks, or a question about delivery, the minimum order, payment or how ordering works
query: the product in plain English catalogue words, for example "fast charger usb-c 20w", "lightning cable", \
"car charger", "tws earbuds", "power bank 10000mah"; keep phone models as written, for example "iphone 15", \
"samsung a54". Empty string when no product is meant.
When a phone is named, add its connector to query: iPhone 15 and newer, recent iPads and almost every Android phone \
charge by USB-C ("usb-c"); iPhone 14 and older charge by Lightning ("lightning"); old Android phones may use micro USB.
codes: product codes written in the message exactly as written (for example "UK20", "C18"), otherwise [].
qty: the number of pieces asked for, otherwise null.
lang: the language of the message: "en", "ar", "ur", "hi", "ml" or "other"."""


def _valid_understanding(d: dict) -> bool:
    return (d.get("intent") in INTENTS and isinstance(d.get("query", ""), str)
            and isinstance(d.get("codes", []), list))


def understand(message: str, history: list[dict], item: dict | None) -> tuple[dict, str]:
    parts = []
    if history:
        lines = "\n".join(f"{h['role']}: {h['text']}" for h in history)
        parts.append("Earlier in this chat:\n" + fence(lines, "history", 3000))
    if item:
        parts.append(f"The merchant has this product open: {item.get('item_code')} ({item.get('display_name') or ''}).")
    parts.append("The new message:\n" + fence(message, "message", 800))
    msgs = [{"role": "system", "content": _UNDERSTAND + "\n" + FENCE_RULE},
            {"role": "user", "content": "\n\n".join(parts)}]
    data, prov = chat_json(msgs, validate=_valid_understanding, tier=1, task="assist", max_tokens=200,
                           request_timeout=7.0, max_providers=3)
    qty = data.get("qty")
    try:
        qty = int(qty) if qty not in (None, "") else None
    except (TypeError, ValueError):
        qty = None
    lang = str(data.get("lang") or "").strip().lower()
    return {
        "intent": data["intent"],
        "query": _clean_text(data.get("query"), 120),
        "codes": [_clean_text(c, 32) for c in (data.get("codes") or []) if isinstance(c, str)][:6],
        "qty": qty if qty and qty > 0 else None,
        "lang": lang if lang in _LANG_NAMES or lang == "other" else "",
    }, prov


# ── 2. handle (no model) ─────────────────────────────────────────────────────────────────────

def _find(cat: dict, understood: dict, item: dict | None) -> list[dict]:
    from app.market_search import search
    out: list[dict] = []
    if item:
        out.append(item)
    for c in understood.get("codes") or []:
        it = cat["by_code"].get(c.upper())
        if it and it not in out:
            out.append(it)
    query = understood.get("query") or ""
    if query and len(out) < MAX_CARDS:
        for it in search(cat["items"], query, limit=MAX_CARDS):
            if it not in out:
                out.append(it)
    return out[:MAX_CARDS]


def _orderable(it: dict, settings: dict) -> bool:
    return it.get("stock_status") != "out_of_stock" or bool(settings.get("allow_backorder"))


def _qty_for(it: dict, asked: int | None) -> int:
    moq = max(int(it.get("moq") or 1), 1)
    q = asked or moq
    return max(moq, min(int(q), shop.MAX_QTY))


def usual_lines(cat: dict, tokens: list[str]) -> list[dict]:
    """The items this device orders most often (only its own orders: it holds their tokens), each
    with the quantity it usually takes, best first; items no longer in the public catalog are
    skipped. Read-only."""
    toks = [t for t in tokens if isinstance(t, str) and 16 <= len(t) <= 64][:20]
    if not toks:
        return []
    c = shop.get_client()
    orders = (c.table("shop_orders").select("id,status").in_("token", toks).execute().data or [])
    ids = [o["id"] for o in orders if o.get("status") != "cancelled"]
    if not ids:
        return []
    rows = (c.table("shop_order_lines").select("order_id,item_code,qty").in_("order_id", ids).execute().data or [])
    seen: dict[str, dict] = {}
    for r in rows:
        code = str(r.get("item_code") or "").upper()
        it = cat["by_code"].get(code)
        if not it:
            continue
        s = seen.setdefault(it["item_code"], {"orders": set(), "qtys": []})
        s["orders"].add(r["order_id"])
        s["qtys"].append(int(r.get("qty") or 0))
    ranked = sorted(seen.items(), key=lambda kv: (-len(kv[1]["orders"]), -sum(kv[1]["qtys"])))
    out = []
    for code, s in ranked[:MAX_USUAL]:
        qtys = sorted(q for q in s["qtys"] if q > 0)
        typical = qtys[len(qtys) // 2] if qtys else None
        out.append({"item_code": code, "qty": _qty_for(cat["by_code"][code.upper()], typical)})
    return out


def contact(tokens: list[str], referral_code: str | None) -> dict | None:
    """Whom the merchant talks to: the rep on this device's latest order (the status page already
    shows it to the token holder), else the storefront rep who opted in to WhatsApp, else the
    office number. {name, whatsapp_url} or None."""
    from app.shop_notify import wa_url
    hello = "Hello, I have a question from the YQ Marketplace."
    try:
        latest = shop.orders_by_tokens(tokens)[:1]
        if latest:
            full = shop.get_order_by_token(latest[0]["token"])
            sm = (shop.public_order_view(full) or {}).get("salesman") if full else None
            if sm and sm.get("whatsapp_url"):
                return {"name": sm.get("first_name"), "whatsapp_url": sm["whatsapp_url"]}
    except Exception as e:  # noqa: BLE001 — a lookup that fails falls through to the next contact
        log.debug("assist: order contact lookup failed: %s", e)
    card = shop.rep_card(shop.context(), referral_code) if referral_code else None
    if card and card.get("whatsapp_url"):
        return {"name": card.get("first_name"), "whatsapp_url": card["whatsapp_url"]}
    from app.config import settings as cfg
    from app.customer_contacts import wa_digits
    digits = wa_digits(cfg.wa_human_number) if cfg.wa_human_number else None
    url = wa_url(digits, hello) if digits else None
    return {"name": "YQ Bahrain", "whatsapp_url": url} if url else None


# ── 3. answer ────────────────────────────────────────────────────────────────────────────────

_ANSWER = """You are YQ Assistant on YQ Bahrain's wholesale marketplace (yqmarketplace.com), talking to a shop owner in Bahrain.
Write one to three short sentences in {language}. Plain text: no lists, no markdown, no greeting unless they greeted you.
Use ONLY the facts in the <data> blocks.
- Never write a price, a discount, a currency amount or a stock quantity: the product cards under your reply show them.
- Name products by their code. Never describe a product that is not in the facts.
- When the facts do not settle whether something fits a phone, say you are not sure and suggest asking their YQ representative.
- Never promise delivery dates, stock arrivals or special prices.
- When no product matched, say so in one sentence and offer to pass the request to their representative."""


def _facts_products(items: list[dict]) -> str:
    rows = []
    for it in items:
        spec = _clean_text(it.get("spec"), 220)
        rows.append(f"- {it.get('item_code')} · {_clean_text(it.get('display_name'), 80)} · "
                    f"{it.get('category') or ''} · {_STOCK_WORDS.get(it.get('stock_status'), '')}"
                    + (f" · spec: {spec}" if spec else ""))
    return "Products found, best match first:\n" + ("\n".join(rows) if rows else "(none)")


def _facts_policy(settings: dict) -> tuple[str, set[str]]:
    """The shop's own rules as facts, and the amounts the reply may name (only these)."""
    lines, amounts = [], set()
    mn = float(settings.get("min_order_bhd") or 0)
    if mn > 0:
        lines.append(f"Wholesale minimum order value: BHD {mn:g}. "
                     + ("Orders under it are sent as a small-order request the representative confirms."
                        if settings.get("small_order_mode") == "request" else ""))
        amounts.add(f"{mn:g}")
    fee = float(settings.get("delivery_fee_bhd") or 0)
    free = float(settings.get("free_delivery_threshold_bhd") or 0)
    if fee > 0:
        lines.append(f"Delivery fee: BHD {fee:g}" + (f", free from BHD {free:g}." if free > 0 else "."))
        amounts.update({f"{fee:g}", f"{free:g}"})
    for p in settings.get("promises") or []:
        if isinstance(p, dict) and p.get("en"):
            lines.append(str(p["en"]))
    lines.append("Every order is confirmed by the shop's YQ sales representative before delivery.")
    lines.append("Shops can search the catalogue, add items to the cart, send the order, and track it on the Orders page.")
    return "Shop facts:\n" + "\n".join(f"- {x}" for x in lines), amounts


_CURRENCY = re.compile(r"(?i)\b(bhd|bd|dinars?|fils|usd|aed|sar)\b|\$|د\.?ب|دينار|فلس")
_NUMBER = re.compile(r"[0-9]+(?:[.,][0-9]+)?")
_SENTENCE_END = re.compile(r"(?<=[.!?؟۔])\s+|\n+")     # a decimal point is not a sentence end


def _scrub_prices(text: str, allowed: set[str] | None = None) -> str:
    """Drop every sentence that names a currency with an amount the facts did not give."""
    allowed = allowed or set()
    keep = []
    for s in _SENTENCE_END.split(text.translate(_INDIC_DIGITS)):
        if _CURRENCY.search(s) and any(n.replace(",", ".") not in allowed
                                       and f"{float(n.replace(',', '.')):g}" not in allowed
                                       for n in _NUMBER.findall(s)):
            continue
        keep.append(s.strip())
    return " ".join(k for k in keep if k)[:600]


def answer(message: str, lang: str, facts: str, allowed: set[str] | None = None) -> tuple[str, str]:
    language = _LANG_NAMES.get(lang, "the same language as the merchant's message")
    msgs = [{"role": "system", "content": _ANSWER.format(language=language) + "\n" + FENCE_RULE},
            {"role": "user", "content": fence(facts, "catalog", 4000) + "\n\nThe merchant wrote:\n"
             + fence(message, "message", 800)}]
    text = chat(msgs, tier=2, task="assist", temperature=0.3, max_tokens=220, request_timeout=8,
                max_429_retries=0, max_providers=3)
    return _scrub_prices(text or "", allowed), last_provider()


# ── the turn ─────────────────────────────────────────────────────────────────────────────────

def _lang_for_templates(understood_lang: str, ui_lang: str) -> str:
    return "ar" if (understood_lang == "ar" or (understood_lang not in ("en",) and ui_lang == "ar")) else "en"


def _result(intent: str, reply: str, **kw) -> dict:
    out = {"intent": intent, "reply": reply, "items": [], "add": [], "orders": [], "handoff": None,
           "fallback": False, "miss": False, "turn_id": None}
    out.update(kw)
    return out


def ask(body: dict, ip: str | None = None) -> dict:
    """One chat turn. `body` is the validated request (see app/shop_api.py AskRequest)."""
    t0 = time.monotonic()
    message = mask(_clean_text(body.get("message"), MAX_MESSAGE))
    ui_lang = "ar" if body.get("lang") == "ar" else "en"
    history = [{"role": "assistant" if h.get("role") == "assistant" else "user",
                "text": mask(_clean_text(h.get("text"), MAX_MESSAGE))}
               for h in (body.get("history") or [])[-MAX_HISTORY:] if isinstance(h, dict)]
    tokens = [str(t) for t in (body.get("order_tokens") or []) if isinstance(t, str)][:20]
    ref = _clean_text(body.get("referral_code"), 32).lower() or None
    cat = public_catalog()
    item = cat["by_code"].get(_clean_text(body.get("item_code"), 64).upper()) if body.get("item_code") else None
    log_row = {"session_id": _clean_text(body.get("session_id"), 64) or None,
               "device_id": _clean_text(body.get("device_id"), 64) or None,
               "lang": ui_lang, "message": message, "ip": ip}

    if not _take_budget():
        res = _result("find", "", fallback=True)
        return _finish(res, log_row, "capped", "", "", t0)

    try:
        understood, prov1 = understand(message, history, item)
    except AllProvidersFailed as e:
        log.info("assist: no model understood the message: %s", e)
        res = _result("find", "", fallback=True)
        return _finish(res, log_row, "fallback", "", "", t0)

    intent = understood["intent"]
    tl = _lang_for_templates(understood["lang"], ui_lang)
    T = TEMPLATES[tl]
    query = understood["query"]

    if intent == "order_status":
        orders = shop.orders_by_tokens(tokens)[:5] if tokens else []
        res = _result(intent, T["orders"] if orders else T["no_orders"], orders=[
            {k: o.get(k) for k in ("order_no", "token", "status", "status_label", "created_at", "items", "units",
                                   "total_bhd", "salesman")} for o in orders])
        if not orders:
            res["handoff"] = contact(tokens, ref)
        return _finish(res, log_row, "answered", prov1, query, t0)

    if intent == "usual":
        lines = usual_lines(cat, tokens) if tokens else []
        add = [ln for ln in lines if _orderable(cat["by_code"][ln["item_code"].upper()], cat["settings"])]
        res = _result(intent, T["usual"] if lines else T["no_usual"],
                      items=[ln["item_code"] for ln in lines][:MAX_CARDS], add=add)
        return _finish(res, log_row, "answered" if lines else "miss", prov1, query, t0)

    if intent == "handoff":
        who = contact(tokens, ref)
        res = _result(intent, (T["handoff"].format(name=who["name"]) if who else T["no_handoff"]), handoff=who)
        return _finish(res, log_row, "handoff", prov1, query, t0)

    if intent == "feedback":
        save_feedback({"kind": "idea", "text": message, "device_id": log_row["device_id"],
                       "session_id": log_row["session_id"], "lang": ui_lang, "page": "assist"}, ip)
        return _finish(_result(intent, T["thanks"]), log_row, "answered", prov1, query, t0)

    if intent == "chat":
        facts, amounts = _facts_policy(cat["settings"])
        try:
            text, prov2 = answer(message, understood["lang"] or ui_lang, facts, amounts)
        except AllProvidersFailed:
            text, prov2 = "", ""
        return _finish(_result(intent, text or T["help"]), log_row, "answered", prov1, query, t0, prov2)

    # find · fit · add: the catalogue
    found = _find(cat, understood, item)
    add: list[dict] = []
    if intent == "add" and found:
        first = found[0]
        if _orderable(first, cat["settings"]):
            add = [{"item_code": first["item_code"], "qty": _qty_for(first, understood["qty"])}]
    if not found:
        _record_miss(body, query or message)
        res = _result(intent, T["miss"], miss=True, handoff=contact(tokens, ref))
        return _finish(res, log_row, "miss", prov1, query, t0)
    text, prov2 = "", ""
    if time.monotonic() - t0 < 10:
        try:
            text, prov2 = answer(message, understood["lang"] or ui_lang, _facts_products(found))
        except AllProvidersFailed:
            text = ""
    res = _result(intent, text or T["found"], items=[it["item_code"] for it in found], add=add)
    return _finish(res, log_row, "answered", prov1, query, t0, prov2)


def _record_miss(body: dict, words: str) -> None:
    """A product ask the catalogue could not answer: the same demand signal as a search that found
    nothing (market_intel.roll_demand_signals reads search_zero), marked where it came from."""
    try:
        shop.record_event({"event": "search_zero", "device_id": body.get("device_id"),
                           "session_id": body.get("session_id"), "referral_code": body.get("referral_code"),
                           "meta": {"q": mask(words)[:120], "where": "ask"}})
    except Exception as e:  # noqa: BLE001
        log.debug("assist: miss event failed: %s", e)


def _finish(res: dict, row: dict, outcome: str, prov1: str, query: str, t0: float, prov2: str = "") -> dict:
    """Log the turn (masked) and hand back its id for the thumbs. A missing table (the migration not
    applied yet) costs the log only, never the answer."""
    ms = int((time.monotonic() - t0) * 1000)
    rec = {"session_id": row["session_id"], "device_id": row["device_id"], "lang": row["lang"],
           "intent": res["intent"], "message": row["message"], "query": query or None,
           "reply": res["reply"] or None, "item_codes": res["items"], "outcome": outcome,
           "providers": ",".join(p for p in (prov1, prov2) if p) or None, "ms": ms,
           "ip_hash": shop._ip_hash(row.get("ip"))}
    try:
        r = shop.get_client().table("shop_ask_turns").insert(rec).execute().data
        res["turn_id"] = r[0]["id"] if r else None
    except Exception as e:  # noqa: BLE001
        log.debug("assist: turn log failed: %s", e)
    return res


def save_feedback(body: dict, ip: str | None = None) -> bool:
    """A merchant's idea, problem report, question or thumbs. Text masked; a thumbs also marks its turn."""
    kind = str(body.get("kind") or "").strip().lower()
    if kind not in FEEDBACK_KINDS:
        return False
    text = mask(_clean_text(body.get("text"), 1000)) or None
    if kind in ("idea", "problem", "question") and not text:
        return False
    try:
        turn_id = int(body.get("turn_id")) if body.get("turn_id") not in (None, "") else None
    except (TypeError, ValueError):
        turn_id = None
    row = {"kind": kind, "text": text, "turn_id": turn_id,
           "device_id": _clean_text(body.get("device_id"), 64) or None,
           "session_id": _clean_text(body.get("session_id"), 64) or None,
           "lang": "ar" if body.get("lang") == "ar" else "en",
           "page": _clean_text(body.get("page"), 80) or None, "ip_hash": shop._ip_hash(ip)}
    try:
        c = shop.get_client()
        c.table("shop_feedback").insert(row).execute()
        if turn_id and kind in ("up", "down"):
            q = c.table("shop_ask_turns").update({"rating": kind}).eq("id", turn_id)
            if row["device_id"]:
                q = q.eq("device_id", row["device_id"])      # only the device that asked rates its turn
            q.execute()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("assist: feedback not stored: %s", e)
        return False


# ── portal reads (Shop Admin) ────────────────────────────────────────────────────────────────

def recent_turns(days: int = 14, limit: int = 200) -> list[dict]:
    since = datetime.fromtimestamp(time.time() - max(1, min(days, 90)) * 86400, timezone.utc).isoformat()
    return (shop.get_client().table("shop_ask_turns")
            .select("id,ts,lang,intent,message,query,reply,item_codes,outcome,providers,ms,rating")
            .gte("ts", since).order("ts", desc=True).limit(max(1, min(limit, 500))).execute().data or [])


def list_feedback(status: str | None = None, limit: int = 200) -> list[dict]:
    q = shop.get_client().table("shop_feedback").select("id,ts,kind,text,turn_id,lang,page,status,handled_by,handled_at")
    if status in ("new", "seen", "done"):
        q = q.eq("status", status)
    return q.order("ts", desc=True).limit(max(1, min(limit, 500))).execute().data or []


def set_feedback_status(feedback_id: int, status: str, by: str) -> dict | None:
    if status not in ("new", "seen", "done"):
        raise shop.ShopError("Status must be new, seen or done.")
    r = (shop.get_client().table("shop_feedback")
         .update({"status": status, "handled_by": by, "handled_at": datetime.now(timezone.utc).isoformat()})
         .eq("id", feedback_id).execute().data)
    return r[0] if r else None


# ── the lines that need no model, in the page's language ─────────────────────────────────────

TEMPLATES = {
    "en": {
        "found": "Here is what matches in our catalogue.",
        "miss": "I couldn't find that in our catalogue. Your representative can check whether YQ can bring it in.",
        "orders": "Here are the latest orders placed from this phone.",
        "no_orders": "I can't see any orders placed from this phone. If you ordered on another phone, your representative can check it.",
        "usual": "These are the items you order most. Check the quantities, then add them in one tap.",
        "no_usual": "I can't see earlier orders on this phone yet. Search for an item and I'll help you add it.",
        "handoff": "Tap below to message {name} on WhatsApp. Your question goes with it.",
        "no_handoff": "Your YQ representative confirms every order and will contact you. You can also leave your question here.",
        "thanks": "Thank you. Your idea goes straight to the YQ team.",
        "help": "I can find products, check whether something fits a phone, show your orders or connect you with your representative.",
    },
    "ar": {
        "found": "هذا ما يطابق طلبك في الكتالوج.",
        "miss": "لم أجد هذا المنتج في الكتالوج. يمكن لمندوبك أن يتحقق إن كانت YQ تستطيع توفيره.",
        "orders": "هذه آخر الطلبات المرسلة من هذا الهاتف.",
        "no_orders": "لا أرى طلبات مرسلة من هذا الهاتف. إن طلبت من هاتف آخر فمندوبك يستطيع التحقق منها.",
        "usual": "هذه المنتجات التي تطلبها أكثر من غيرها. راجع الكميات ثم أضفها بلمسة واحدة.",
        "no_usual": "لا أرى طلبات سابقة على هذا الهاتف بعد. ابحث عن منتج وسأساعدك في إضافته.",
        "handoff": "اضغط أدناه لمراسلة {name} على واتساب، وسيصله سؤالك معها.",
        "no_handoff": "مندوب YQ يؤكد كل طلب وسيتواصل معك. ويمكنك أيضًا ترك سؤالك هنا.",
        "thanks": "شكرًا لك. فكرتك تصل مباشرة إلى فريق YQ.",
        "help": "أستطيع البحث عن المنتجات، والتأكد من توافقها مع الهاتف، وعرض طلباتك، وإيصالك بمندوبك.",
    },
}
