"""Phase 1 AI query engine.

Flow: cache → template → LLM text-to-SQL → validate → RPC execute → LLM answer → cache
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from app.database import get_client
from app.llm_router import Redactor, chat, chat_stream
from app.sql_validator import FeatureAccessError, SQLValidationError, validate
from app.templates import match as template_match

log = logging.getLogger(__name__)

_VIEW_SCHEMA = """
DATABASE VIEWS — query ONLY these, never raw tables:

v_sales               Sales lines (one row per invoice line)
  sale_date(date), customer_name, salesman_resolved, channel('B2C'|'B2B'),
  is_cash_customer(bool), item_name, sku_code, category_name, quantity,
  revenue_bhd[GROSS, VAT-incl — USE THIS FOR REVENUE], net_bhd[ex-VAT], gross_bhd

v_sales_by_period     Monthly totals (period_month = 1st of month)
  period_month, order_count, total_qty, gross_bhd[revenue], net_revenue_bhd[ex-VAT]

v_sales_by_salesman   Per-salesman totals
  salesman, orders, qty, revenue_bhd[gross], net_bhd

v_sales_by_channel    B2C (Causeway/YQ Roadshow = retail) vs B2B (wholesale)
  channel, orders, qty, revenue_bhd[gross], net_bhd

v_sales_by_category   Sales grouped by product CATEGORY (Focus item-group: Cable, Charger, Power
  Bank, Headphones, Sim…) + division ('Accessories' | 'Telecom'). USE THIS for "sales by category",
  "best/worst category", "Accessories vs Devices/Telecom". (v_sales.category_name is also populated.)
  category_name, division, orders, qty, revenue_bhd[gross], net_bhd

v_top_customers       Customer revenue ranking (gross_bhd = gross revenue)
  customer_name, order_count, total_qty, gross_bhd, total_revenue_bhd, last_order_date

v_current_stock       Current on-hand stock per item+warehouse (warehouse = salesman/location)
  item_name, warehouse_name, balance_qty, balance_value_bhd, avg_rate_bhd,
  as_of_date, category_name

v_stock_health        Velocity-aware stock signal (one row per item)
  item_name, current_stock, sold_30d, sold_90d, avg_daily, days_cover,
  suggested_reorder_qty,
  status('urgent_out_of_stock'|'low_stock'|'dead_stock'|'overstock'|'healthy')

v_inventory_aging     On-hand stock by idleness
  item_name, current_stock, stock_value, last_sold, days_since_sale

v_product_margin      Profitability per item, latest period (Focus COGS basis)
  item_name, category_name, net_amount_bhd, cogs_bhd, gross_profit_bhd,
  gp_margin_pct[below cost if < 0], np_margin_pct

v_price_list          CURRENT SELLING PRICE per product (the price list — one row per SKU).
  sku_code, item_name, price_bhd[current standard selling price], unit_name, price_book
  USE THIS for any "price of X", "how much is …", "cheapest/most expensive item",
  "price list" question. Always SELECT sku_code, item_name AND price_bhd together.

v_price_list_by_book  Selling price per SKU split by price book (for channel-specific prices)
  sku_code, item_name, price_book('MA_base'=standard | 'modern_trade'=retail), price_bhd, unit_name

v_product_economics   Per-SKU PRICE vs COST = unit margin (use for "margin on X", "price vs
  cost", "thin/loss-making at current price"). cost_bhd null if no purchase cost on file.
  sku_code, item_name, price_bhd, cost_bhd[landed cost], margin_bhd, margin_pct

v_price_history       Prices CHANGE over time — full dated history per SKU/book (use for "did
  X's price change", "old/previous price", "price trend"). Newest = current.
  sku_code, item_name, price_book, effective_from, effective_to, price_bhd, unit_name

v_purchase_history    What we BOUGHT (goods received) — dated purchase cost per item + VENDOR.
  Use for "when did we last buy X", "what did we pay", "which vendor", "what did we order".
  item_name, purchased_on, qty, cost_bhd[paid per unit], value_bhd, vendor, mrn_no, warehouse_name

v_cost_change         Latest vs previous PURCHASE cost per item (use for "did our cost go up/
  down", "biggest cost increases", "cost trend"). Real priced lines only.
  item_name, vendor, last_bought_on, current_cost_bhd, prev_cost_bhd, cost_delta_bhd, cost_change_pct

v_price_change        Latest vs previous SELLING price per SKU (use for "which prices changed",
  "biggest price increases/cuts", "what changed recently"). Only SKUs whose price changed.
  sku_code, item_name, changed_on, current_price_bhd, prev_price_bhd, price_delta_bhd, price_change_pct

v_receivables         Trade-debtor balances + ageing (positive = owes YQ)
  account, group_name, outstanding_bhd, overdue_bhd[31+ days], over_90_bhd,
  b_0_30, b_31_60, b_61_90, b_91_120, b_121_150, b_151_180, b_181_210, b_over_210

v_stock_transfers     INTERNAL inter-warehouse stock transfers (Stock Issue Vouchers): central
  'Accessories Warehouse' -> a salesman's van warehouse (and returns the other way). Use for
  "what stock did we give/issue salesman X", "transfers to/from a warehouse", "who received the
  most stock", "stock moved between warehouses". from_warehouse / to_warehouse are warehouse
  (= salesman) names. NOTE: this is INTERNAL movement, NOT vendor purchases — for "bought /
  received from vendor / MRN" use v_purchase_history instead.
  transfer_date, voucher, item_name, from_warehouse, to_warehouse, qty, value_bhd

v_salesman_stock_recon  Per-salesman stock reconciliation: stock ISSUED to each van warehouse
  vs SOLD / returned / still on hand, plus Focus 'Shortages in Stock'. Use for "stock shortages
  by salesman", "is any salesman losing/leaking stock", "shrinkage", "unaccounted stock".
  salesman, transferred_in_qty, transferred_in_value_bhd, transferred_out_qty, sold_qty,
  on_hand_qty, shortage_qty, shortage_value_bhd[HARD leakage signal, BHD],
  unexplained_qty[issued - returned - sold - on-hand; SOFT signal]

v_po_price_history    Per-item PURCHASE-ORDER history — every PO line for an item, newest first. Use
  for "what did we pay/order item X for across orders", "F15 order/price history", "price we paid over
  time". item_code, description, vendor, po_no, po_date, qty, rate_bhd[ordered unit price], recency

v_po_cost_change      Latest vs previous PO rate per item = cost change ACROSS ORDERS (the manual
  "did it get cheaper/dearer since last order"). Use for "which items got more/less expensive across
  our orders", "biggest PO cost increases". item_code, current_rate_bhd, prev_rate_bhd, rate_delta_bhd,
  rate_change_pct, last_ordered, prev_ordered

v_purchase_lifecycle  PO ordered -> received lifecycle (each PO line matched to its MRN receipt). Use
  for "what's on order / not yet received", "did order X arrive". po_no, po_date, code, qty_ordered,
  rate_bhd, received_on, status('received'|'on_order')

RULES:
- Return ONLY the SQL SELECT — no markdown, no explanation, no code blocks.
- Use ILIKE '%value%' for text searches (case-insensitive).
- Always include LIMIT (max 200). Default LIMIT 50.
- Currency is BHD. Revenue = revenue_bhd (gross). Receivables total = SUM(outstanding_bhd).
- DATA IS HISTORICAL: "today"/"this month"/"latest" must anchor to the data's last day:
  use (SELECT MAX(sale_date) FROM v_sales), NOT CURRENT_DATE.
  This month = sale_date >= DATE_TRUNC('month',(SELECT MAX(sale_date) FROM v_sales)).
- For "customers" exclude the walk-in bucket: customer_name NOT ILIKE 'cash customer%'.
- Below cost = v_product_margin WHERE gp_margin_pct < 0. Low stock = v_stock_health
  WHERE status IN ('urgent_out_of_stock','low_stock').
TERMINOLOGY:
- "price" / "how much" / "selling price" -> v_price_list.price_bhd (current). Always return
  sku_code + item_name + price_bhd. "did the price change / old price" -> v_price_history.
- "margin / profit on a SKU / price vs cost" -> v_product_economics (price_bhd vs cost_bhd).
- "cost / landed cost / what we pay" -> v_product_economics.cost_bhd.
- "merchant" / "shop" / "client" = a CUSTOMER -> v_top_customers / v_sales.customer_name /
  v_receivables.account. "Modern trade" merchants -> price_book='modern_trade'.
- A SKU is identified by sku_code; always show it alongside item_name for product answers.
PRODUCT NAME MATCHING (names are abbreviated/messy):
- Match on the SHORT model/SKU token, not the full description. e.g. "F30 power bank" ->
  item_name ILIKE '%F30%'  (the data says "F30 PB ..."). "X17 cable" -> ILIKE '%X17%'.
- Common abbreviations in item names: PB=power bank, CL=cable, CC=car charger, UC=USB-C,
  ML=micro/lightning, UL=USB-lightning, NB=neckband, BE/HP=headphone, WC=wireless charger.
  Expand the user's words to these when searching (e.g. "power bank" -> also try '%PB%').
"""


def _cache_key(q: str, allowed_features: set[str] | None = None) -> str:
    # scope the cache by the caller's feature set so a member never sees another scope's cached
    # answer ("all" for admins/agent — they share one cache).
    sig = "all" if allowed_features is None else ",".join(sorted(allowed_features))
    return hashlib.md5((q.strip().lower() + "|" + sig).encode()).hexdigest()


def _check_cache(client, key: str) -> dict | None:
    try:
        r = (
            client.table("query_cache")
            .select("reply,sql_used")
            .eq("query_hash", key)
            .gt("expires_at", datetime.now(timezone.utc).isoformat())
            .limit(1)
            .execute()
        )
        return r.data[0] if r.data else None
    except Exception:
        return None


def _store_cache(client, key: str, question: str, reply: str, sql: str, rows: list) -> None:
    try:
        expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
        client.table("query_cache").upsert(
            {
                "query_hash": key,
                "question": question,
                "reply": reply,
                "sql_used": sql,
                "raw_data": json.dumps(rows[:100], default=str),
                "expires_at": expires,
            },
            on_conflict="query_hash",
        ).execute()
    except Exception as e:
        log.warning("cache write failed: %s", e)


def flush_cache() -> int:
    """Clear the text-to-SQL answer cache. MUST be called after any data refresh / view
    change, or stale answers persist (the cache key is just the question text)."""
    try:
        get_client().table("query_cache").delete().neq("query_hash", "").execute()
        return 1
    except Exception as e:
        log.warning("cache flush failed: %s", e)
        return 0


# Re-exported so the many existing `from app.ai import exec_sql` imports keep working;
# the implementation now lives in app.db_read (data access without the LLM engine).
from app.db_read import exec_sql, exec_sql_params  # noqa: E402,F401


# Latest loaded sale date for user-facing copy — cached 5 min so greetings/fallbacks
# don't cost a query, and never goes stale across uploads (unlike a hardcoded date).
_data_date_cache: dict[str, Any] = {"d": "", "at": 0.0}


def _data_date_str() -> str:
    now = time.time()
    if _data_date_cache["d"] and now - _data_date_cache["at"] < 300:
        return _data_date_cache["d"]
    label = "the latest upload"
    try:
        rows = exec_sql("SELECT MAX(sale_date) AS d FROM v_sales LIMIT 1")
        raw = (rows or [{}])[0].get("d")
        if raw:
            label = datetime.fromisoformat(str(raw)[:10]).strftime("%d %b %Y")
    except Exception:  # noqa: BLE001
        pass
    _data_date_cache.update(d=label, at=now)
    return label


# Shared with prompt_guard (single implementation); kept under the old name because
# orchestrator/mcp_server import `_entity_names` from here.
from app.prompt_guard import FENCE_RULE, entity_names as _entity_names, fence  # noqa: E402


def _fmt_hist(history: list[dict] | None, n: int = 6) -> str:
    """Compact recent turns for follow-up resolution ('only top 5', 'for them')."""
    if not history:
        return ""
    turns = [h for h in history if not h.get("pending")][-n:]
    return "\n".join(f"{h.get('role')}: {str(h.get('content', ''))[:200]}" for h in turns)


def _llm_sql(question: str, model_name: str | None = None, history: list[dict] | None = None,
             error_hint: str | None = None) -> str:
    """Call LLM to generate SQL for the question. Returns raw SQL. `error_hint` (the previous
    SQL + DB error) drives a one-shot self-repair when a generated query fails."""
    msgs = [
        {
            "role": "system",
            "content": (
                "You are a PostgreSQL query generator for YQ Bahrain Mobile Accessories. "
                "Generate ONE SELECT query using ONLY the views listed. "
                "Return ONLY the SQL — no markdown fences, no explanation.\n\n"
                + _VIEW_SCHEMA
            ),
        },
    ]
    hist = _fmt_hist(history)
    if hist:
        msgs.append({"role": "system", "content":
                     "Recent conversation — resolve follow-ups (e.g. 'only the top 5', "
                     "'break it down', 'for them') against it:\n" + hist})
    if error_hint:
        msgs.append({"role": "system", "content":
                     "Your previous query FAILED. Fix the column/view name and return corrected SQL "
                     "(check each column exists on that view in the schema above):\n" + error_hint})
    msgs.append({"role": "user", "content": question})
    # SQL generation = a 'code' task; cap latency so a slow free model can't hang the request
    raw = chat(msgs, tier=2, temperature=0.1, max_tokens=400, model_name=model_name,
               task="sql", request_timeout=10, max_429_retries=0, max_providers=3)
    raw = re.sub(r"```\w*\s*", "", raw, flags=re.IGNORECASE)
    return raw.replace("```", "").strip().rstrip(";")


def _render_rows(rows: list[dict], limit: int = 20) -> str:
    """Deterministic local render of result rows — the safety net when the LLM answer comes
    back empty (a reasoning model can emit no content). The user never gets a blank reply.
    Shown to the authenticated user only (real data, like the template formatter — no egress)."""
    if not rows:
        return ""
    cols = list(rows[0].keys())
    lines = [f"**{len(rows)} result(s)** — showing {min(len(rows), limit)}:", ""]
    for i, r in enumerate(rows[:limit], 1):
        parts = []
        for c in cols[:6]:
            v = r.get(c)
            if v is None:
                continue
            if isinstance(v, float):
                v = f"{v:,.3f}".rstrip("0").rstrip(".")
            parts.append(f"{c}: {v}")
        lines.append(f"{i}. " + " · ".join(parts))
    return "\n".join(lines)


def _llm_answer(question: str, rows: list[dict], redactor: Redactor,
                model_name: str | None = None, history: list[dict] | None = None) -> str:
    """Format SQL result rows into a concise business answer via LLM."""
    if not rows:
        return (
            "I checked the records but didn't find anything matching that. The data runs "
            f"to **{_data_date_str()}** — try rephrasing, or ask about sales, a salesman, "
            "stock health, margins, or who owes us money."
        )
    row_text = json.dumps(rows[:50], indent=2, default=str)
    redacted_rows = redactor.redact(row_text, _entity_names(rows))
    msgs = [
        {
            "role": "system",
            "content": (
                "You are a sharp, confident business analyst for YQ Bahrain Mobile Accessories. "
                "Answer the question directly and concisely from the data, then add ONE short "
                "insight line ('Insight: …') only if it's genuinely useful. "
                "Format BHD as 'BHD X,XXX.XX'. Use bullet points for lists. Bold key numbers. "
                "Never invent data not present in the result. Be crisp — no filler. "
                + FENCE_RULE
            ),
        },
    ]
    hist = _fmt_hist(history)
    if hist:
        msgs.append({"role": "system", "content": "Recent conversation for context:\n" + hist})
    msgs.append({"role": "user",
                 "content": f"Question: {question}\n\n{fence(redacted_rows, 'sql_rows')}"})
    answer = chat(msgs, tier=2, temperature=0.3, max_tokens=600, model_name=model_name,
                  task="synthesis", request_timeout=12, max_429_retries=0, max_providers=3)
    answer = redactor.restore(answer)
    # A reasoning model can return empty content — never show a blank reply when we HAVE rows.
    return answer if answer.strip() else _render_rows(rows)


def _fmt_template(label: str, rows: list[dict]) -> str:
    """Format template SQL result as clean markdown — no LLM needed."""
    if not rows:
        return f"**{label}** — no data found."
    if len(rows) == 1:
        r = rows[0]
        parts = [f"**{label}**", ""]
        for k, v in r.items():
            if v is None:
                continue
            lbl = k.replace("_", " ").title()
            val = f"BHD {float(v):,.2f}" if isinstance(v, (int, float)) and "bhd" in k.lower() else str(v)
            parts.append(f"- **{lbl}:** {val}")
        return "\n".join(parts)
    lines = [f"**{label}** — {len(rows)} results", ""]
    key0 = list(rows[0].keys())[0]
    key1 = list(rows[0].keys())[1] if len(rows[0]) > 1 else None
    for i, r in enumerate(rows[:25], 1):
        name = str(r.get(key0, ""))[:60]
        suffix = ""
        if key1 and r.get(key1) is not None:
            v = r[key1]
            suffix = f" — **BHD {float(v):,.2f}**" if isinstance(v, (int, float)) and "bhd" in key1.lower() else f" — {v}"
        lines.append(f"{i}. {name}{suffix}")
    if len(rows) > 25:
        lines.append(f"_… and {len(rows) - 25} more items_")
    return "\n".join(lines)


_GREETING = re.compile(r"^\s*(hi|hey+|hello|h?ello|yo|salam|salaam|hola|good\s*(morning|afternoon|evening)|howdy)\b", re.I)
_HOWAREYOU = re.compile(r"\b(how are you|how'?re you|how are things|how'?s it going|how do you do|what'?s up|hows you)\b", re.I)
# Bare acknowledgements / fillers — must NOT trigger a fabricated SQL query.
_ACK = re.compile(r"^\s*(ok|okay|okie|k|kk|cool|sure|alright|alrighty|got ?it|noted|yep|yup|yeah|ya|yes|no|nope|nah|fine|right|hmm+|mhm|hm|oh|ah|i see|makes sense|good|nice one)\b[.!,\s]*$", re.I)
_THANKS = re.compile(r"\b(thanks|thank you|thx|shukran|appreciate|great|awesome|perfect|nice work)\b", re.I)
_IDENTITY = re.compile(r"\b(who are you|what are you|your name|what can you do|help me|what do you do|capabilities|how do you work|tell me about (your ?self|you)|about your ?self|introduce your ?self)\b", re.I)


def _smalltalk(question: str) -> str | None:
    """Respond to greetings / identity / thanks conversationally — no SQL.

    Keeps the assistant feeling intelligent and human instead of returning
    'no data' to a simple 'hi'."""
    q = question.strip()
    if len(q) <= 60 and _GREETING.search(q) and not re.search(r"\d|sale|stock|revenue|customer|margin|owe|debtor", q, re.I):
        return (
            "Hello — I'm your YQ Bahrain operations analyst. I have your live "
            f"Mobile Accessories data (as of **{_data_date_str()}**): sales, salesmen, stock, "
            "margins and receivables.\n\n"
            "Try asking me:\n"
            "- **Who's our top salesman this month?**\n"
            "- **Which fast movers are out of stock?**\n"
            "- **Who owes us the most, and how overdue?**\n"
            "- **What's our gross margin?**\n\n"
            "What would you like to know?"
        )
    if _IDENTITY.search(q):
        return (
            "I'm the YQ Bahrain AI analyst. I read your live Focus ERP data and answer "
            "in plain English — sales & revenue (gross and ex-VAT), salesman and B2C/B2B "
            "performance, stock health and reorder needs, product margins, and customer "
            "receivables with ageing. Ask me anything operational and I'll pull the numbers "
            "and the insight. What shall we look at?"
        )
    if _HOWAREYOU.search(q) and len(q) <= 50:
        return (
            "Doing great and ready to dig into your numbers. The data's fresh to "
            "**22 Jun 2026**. Want the day's headline — top salesman, low stock, who owes "
            "us money, or margins?"
        )
    if _ACK.match(q):
        return (
            "Sure — what would you like to look at? For example: **top salesman**, "
            "**low stock**, **who owes us money**, or **gross margin**. Or just ask in your "
            "own words."
        )
    if _THANKS.search(q) and len(q) <= 40:
        return "Anytime. Ask me anything else about sales, stock, margins or receivables."
    return None


def ask(question: str, user_email: str = "system", model_name: str | None = None,
        history: list[dict] | None = None, allowed_features: set[str] | None = None) -> dict[str, Any]:
    """Answer a natural-language question about YQ Bahrain operations.

    `allowed_features` feature-scopes the query for non-admins (None = unrestricted).
    Returns dict with: reply, sql_used, cached, row_count
    """
    client = get_client()
    key = _cache_key(question, allowed_features)

    # 0 — conversational (greetings / identity / thanks) — never run SQL on these
    chat_reply = _smalltalk(question)
    if chat_reply:
        return {"reply": chat_reply, "sql_used": "", "cached": False, "row_count": 0}

    # 1 — cache hit (scoped by feature set, so no cross-scope leak)
    hit = _check_cache(client, key)
    if hit:
        return {"reply": hit["reply"], "sql_used": hit.get("sql_used", ""), "cached": True, "row_count": 0}

    redactor = Redactor()
    sql_used = ""
    rows: list[dict] = []

    try:
        # 2 — deterministic template
        tmpl = template_match(question)
        if tmpl:
            label, raw_sql = tmpl
            sql_used = validate(raw_sql, allowed_features)
            rows = exec_sql(sql_used)
            reply = _fmt_template(label, rows)

        else:
            # 3 — LLM text-to-SQL (one-shot self-repair if the generated SQL hits a DB error)
            redacted_q = redactor.redact(question, [])
            raw_sql = _llm_sql(redacted_q, model_name, history)
            sql_used = validate(raw_sql, allowed_features)
            try:
                rows = exec_sql(sql_used)
            except Exception as ex:  # noqa: BLE001 — feed the error back once to fix the column/view
                raw_sql = _llm_sql(redacted_q, model_name, history, error_hint=f"{sql_used}\n-- error: {ex}")
                sql_used = validate(raw_sql, allowed_features)
                rows = exec_sql(sql_used)
            reply = _llm_answer(question, rows, redactor, model_name, history)

    except FeatureAccessError as e:
        return {"reply": f"🔒 {e}", "sql_used": "", "cached": False, "row_count": 0}
    except SQLValidationError as e:
        return {"reply": f"I couldn't generate a valid query. ({e})", "sql_used": sql_used, "cached": False, "row_count": 0}
    except Exception as e:
        log.exception("ask() error: %s", e)
        return {"reply": "Something went wrong. Please try rephrasing your question.", "sql_used": sql_used, "cached": False, "row_count": 0}

    if reply and reply.strip():  # never cache an empty answer (would serve blank for 7 days)
        _store_cache(client, key, question, reply, sql_used, rows)
    return {"reply": reply, "sql_used": sql_used, "cached": False, "row_count": len(rows)}


def _stream_words(text: str):
    """Yield a ready-made reply in word-sized chunks for a typed feel."""
    for i, w in enumerate(text.split(" ")):
        yield w if i == 0 else " " + w
        time.sleep(0.012)


_TOK_TAIL = re.compile(r"CUST_\d*$")


def _stream_restore(chunks, restore):
    """Wrap a token stream so redaction tokens (CUST_n) are restored to real names
    even when a token is split across chunks: hold back a trailing partial 'CUST_…'
    until it completes, then restore and flush."""
    buf = ""
    for ch in chunks:
        buf += ch
        m = _TOK_TAIL.search(buf)
        if m and m.end() == len(buf):  # possible partial token at the tail — keep buffering
            safe, buf = buf[:m.start()], buf[m.start():]
        else:
            safe, buf = buf, ""
        if safe:
            yield restore(safe)
    if buf:
        yield restore(buf)


def ask_stream(question: str, user_email: str = "system", model_name: str | None = None,
               history: list[dict] | None = None, allowed_features: set[str] | None = None):
    """Streaming variant of ask(): yields answer text as it is produced.

    `allowed_features` feature-scopes the query for non-admins (None = unrestricted).
    Conversational/cached/template replies are word-streamed for a consistent typed
    feel; LLM answers are true token-streamed from the provider."""
    client = get_client()

    chat_reply = _smalltalk(question)
    if chat_reply:
        yield from _stream_words(chat_reply)
        return

    key = _cache_key(question, allowed_features)
    hit = _check_cache(client, key)
    if hit:
        yield from _stream_words(hit["reply"])
        return

    redactor = Redactor()
    try:
        tmpl = template_match(question)
        if tmpl:
            label, raw_sql = tmpl
            sql_used = validate(raw_sql, allowed_features)
            rows = exec_sql(sql_used)
            reply = _fmt_template(label, rows)
            yield from _stream_words(reply)
            _store_cache(client, key, question, reply, sql_used, rows)
            return

        redacted_q = redactor.redact(question, [])
        raw_sql = _llm_sql(redacted_q, model_name, history)
        try:
            sql_used = validate(raw_sql, allowed_features)
        except FeatureAccessError as e:
            yield from _stream_words(f"🔒 {e}")
            return
        except SQLValidationError as e:
            yield from _stream_words(f"I couldn't turn that into a valid query. ({e})")
            return
        try:
            rows = exec_sql(sql_used)
        except Exception as ex:  # noqa: BLE001 — one-shot self-repair on a DB error
            raw_sql = _llm_sql(redacted_q, model_name, history, error_hint=f"{sql_used}\n-- error: {ex}")
            sql_used = validate(raw_sql, allowed_features)
            rows = exec_sql(sql_used)
        if not rows:
            yield from _stream_words(
                "I checked the records but didn't find anything matching that. The data "
                "runs to 22 Jun 2026 — try rephrasing, or ask about sales, a salesman, "
                "stock health, margins, or who owes us money."
            )
            return

        row_text = redactor.redact(json.dumps(rows[:50], indent=2, default=str), _entity_names(rows))
        msgs = [
            {"role": "system", "content": (
                "You are a sharp, confident business analyst for YQ Bahrain Mobile Accessories. "
                "Answer the question directly and concisely from the data, then add ONE short "
                "insight line ('Insight: …') only if it's genuinely useful. Format BHD as "
                "'BHD X,XXX.XX'. Use bullet points for lists. Bold key numbers. Never invent "
                "data not present. Be crisp — no filler. " + FENCE_RULE)},
        ]
        hist = _fmt_hist(history)
        if hist:
            msgs.append({"role": "system", "content": "Recent conversation for context:\n" + hist})
        try:  # semantic memory: glossary / past briefings / decisions (redacted + fenced)
            from app.knowledge import recall_text
            mem = recall_text(question, k=3)
            if mem:
                msgs.append({"role": "system", "content":
                             "Background from memory (use if relevant):\n"
                             + fence(redactor.redact(mem, _entity_names(rows)), "memory")})
        except Exception:  # noqa: BLE001
            pass
        msgs.append({"role": "user",
                     "content": f"Question: {question}\n\n{fence(row_text, 'sql_rows')}"})
        full = ""
        for piece in _stream_restore(
            chat_stream(msgs, tier=2, model_name=model_name, task="synthesis",
                        request_timeout=12, max_providers=3), redactor.restore):
            full += piece
            yield piece
        if full.strip():
            _store_cache(client, key, question, full, sql_used, rows)
        else:  # LLM streamed nothing — fall back to a deterministic render so the reply isn't blank
            fb = _render_rows(rows)
            if fb:
                yield from _stream_words(fb)
                _store_cache(client, key, question, fb, sql_used, rows)
    except FeatureAccessError as e:  # e.g. a feature-restricted template
        yield from _stream_words(f"🔒 {e}")
    except Exception as e:  # noqa: BLE001
        log.exception("ask_stream error: %s", e)
        yield "\nSomething went wrong. Please try rephrasing your question."
