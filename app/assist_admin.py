"""YQ Assist v2, the back office's reads (29-Oct-2026): the portal page "YQ Assist" (web/src/pages/MerchantVoice.tsx).

Everything is summed here, on the server, from the chat's own log (shop_ask_turns), its tickets (shop_tickets) and
the orders the same phones placed (shop_orders by device): Today's tiles and its "do now" list, conversations (a chat
is its session's turns, not a turn), Insights (what merchants asked that we do not stock, what they asked for while
it was sold out, the policy questions with no approved answer, where the time and the failures are), and the
office's approved policy answers ("Teach the assistant"). Shop Admin only (app/shop_api.py). Merchant text was
masked before it was stored; nothing here reaches a model.
"""
from __future__ import annotations

import json
import logging
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from app import shop

log = logging.getLogger(__name__)

_V1_COLS = "id,ts,session_id,device_id,lang,intent,message,query,reply,item_codes,outcome,providers,ms,rating"
_V2_COLS = _V1_COLS + ",action,detected_lang,topic,customer_id,salesman_id,cards,escalated,ms_understand,ms_answer,ticket_id"
_cols = {"v2": True, "checked": 0.0}
MAX_TURNS = 20000
ORDER_WINDOW_H = 24          # an order this phone placed within a day of chatting counts as "after a chat"
OPEN = ("new", "in_progress", "waiting_shop")


def _since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=max(1, min(int(days), 180)))).isoformat()


def _missing_column(e: Exception) -> bool:
    t = str(e).lower()
    return "column" in t and ("does not exist" in t or "could not find" in t) or "pgrst204" in t


def turns(days: int, session_id: str | None = None) -> list[dict]:
    """The turns of the last `days` (or one session's), newest first, paged past PostgREST's 1,000 rows. The v2
    columns when the migration has run, the assist1 ones before it."""
    client = shop.get_client()
    cols = _V2_COLS if (_cols["v2"] or time.time() - _cols["checked"] > 600) else _V1_COLS
    out: list[dict] = []
    start = 0
    while start < MAX_TURNS:
        try:
            q = client.table("shop_ask_turns").select(cols)
            q = q.eq("session_id", session_id) if session_id else q.gte("ts", _since(days))
            rows = q.order("ts", desc=True).range(start, start + 999).execute().data or []
        except Exception as e:  # noqa: BLE001
            if cols == _V2_COLS and _missing_column(e):
                _cols.update(v2=False, checked=time.time())
                cols = _V1_COLS
                continue
            raise
        out += rows
        if len(rows) < 1000:
            break
        start += 1000
    if cols == _V2_COLS:
        _cols.update(v2=True)
    return out


def _pct(xs: list[int], p: float) -> int | None:
    xs = sorted(x for x in xs if isinstance(x, (int, float)))
    if not xs:
        return None
    return int(xs[min(len(xs) - 1, max(0, round(p * (len(xs) - 1))))])


def _sid(t: dict) -> str:
    return str(t.get("session_id") or f"device:{t.get('device_id') or t.get('id')}")


def _orders_after_chat(rows: list[dict], days: int) -> list[dict]:
    """Orders whose phone chatted in the day before it ordered: [{order_no, created_at, total_bhd, device_id}]."""
    last_turn: dict[str, list[str]] = defaultdict(list)
    for t in rows:
        if t.get("device_id"):
            last_turn[t["device_id"]].append(t["ts"])
    devices = list(last_turn)[:400]
    if not devices:
        return []
    try:
        orders = (shop.get_client().table("shop_orders").select("id,order_no,created_at,total_bhd,status,device_id")
                  .in_("device_id", devices).gte("created_at", _since(days)).execute().data or [])
    except Exception as e:  # noqa: BLE001
        log.debug("assist admin: orders read failed: %s", e)
        return []
    out = []
    for o in orders:
        if o.get("status") == "cancelled":
            continue
        made = shop._parse_ts(o.get("created_at"))
        for ts in last_turn.get(o.get("device_id"), []):
            asked = shop._parse_ts(ts)
            if made and asked and timedelta(0) <= made - asked <= timedelta(hours=ORDER_WINDOW_H):
                out.append(o)
                break
    return out


def _open_tickets() -> list[dict]:
    try:
        return (shop.get_client().table("shop_tickets").select("id,ref,kind,status,created_at,first_seen_at,salesman_id")
                .in_("status", list(OPEN)).order("created_at").limit(500).execute().data or [])
    except Exception as e:  # noqa: BLE001 — no table yet (market_assist_v2_migration.sql)
        log.debug("assist admin: tickets read failed: %s", e)
        return []


def _hours(ts: str | None, now: datetime) -> float | None:
    from app.shop_jobs import business_minutes
    start = shop._parse_ts(ts)
    return round(business_minutes(start, now) / 60, 1) if start else None


def _session_outcome(ts: list[dict], ordered: bool) -> str:
    outs = [t.get("outcome") for t in ts]
    if any(t.get("ticket_id") for t in ts) or "ticket" in outs:
        return "ticket"
    if ordered:
        return "ordered"
    if any(t.get("escalated") for t in ts) or "handoff" in outs:
        return "escalated"
    if "answered" not in outs and any(o in ("error", "fallback", "capped") for o in outs):
        return "failed"
    if "miss" in outs and outs[0] == "miss":
        return "missed"
    return "solved"


def _names(customer_ids: set, salesman_ids: set) -> tuple[dict, dict]:
    client = shop.get_client()
    shops, reps = {}, {}
    try:
        if customer_ids:
            for r in client.table("shop_customers").select("id,shop,name").in_("id", list(customer_ids)[:500]).execute().data or []:
                shops[r["id"]] = r.get("shop") or r.get("name")
        if salesman_ids:
            for r in client.table("salesmen").select("id,name").in_("id", list(salesman_ids)[:200]).execute().data or []:
                reps[r["id"]] = r.get("name")
    except Exception as e:  # noqa: BLE001
        log.debug("assist admin: names read failed: %s", e)
    return shops, reps


def sessions(days: int = 7, outcome: str | None = None, q: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    """One row per chat (its session), newest first: the shop when known, the rep offered, the language, the
    intents, how it ended (ticket, ordered, escalated, failed, missed, solved), the first message, its turns."""
    rows = turns(days)
    ordered_devices = {o.get("device_id") for o in _orders_after_chat(rows, days)}
    by: dict[str, list[dict]] = defaultdict(list)
    for t in rows:
        by[_sid(t)].append(t)
    out = []
    for sid, ts in by.items():
        ts.sort(key=lambda t: t["ts"])
        intents = list(dict.fromkeys(t.get("intent") for t in ts if t.get("intent")))
        out.append({
            "session_id": sid, "first_ts": ts[0]["ts"], "last_ts": ts[-1]["ts"], "turns": len(ts),
            "lang": ts[0].get("detected_lang") or ts[0].get("lang"), "intents": intents,
            "outcome": _session_outcome(ts, ts[0].get("device_id") in ordered_devices),
            "first_message": ts[0].get("message"), "customer_id": next((t.get("customer_id") for t in ts if t.get("customer_id")), None),
            "salesman_id": next((t.get("salesman_id") for t in ts if t.get("salesman_id")), None),
            "down": sum(1 for t in ts if t.get("rating") == "down"), "up": sum(1 for t in ts if t.get("rating") == "up"),
            "ms_max": max((t.get("ms") or 0 for t in ts), default=0),
        })
    if outcome:
        out = [s for s in out if s["outcome"] == outcome]
    if q:
        ql = q.strip().lower()
        hit = {sid for sid, ts in by.items() if any(ql in str(t.get("message") or "").lower() for t in ts)}
        out = [s for s in out if s["session_id"] in hit]
    out.sort(key=lambda s: s["last_ts"], reverse=True)
    total = len(out)
    page = out[offset:offset + max(1, min(limit, 200))]
    shops, reps = _names({s["customer_id"] for s in page if s["customer_id"]}, {s["salesman_id"] for s in page if s["salesman_id"]})
    for s in page:
        s["shop"] = shops.get(s["customer_id"])
        s["rep"] = reps.get(s["salesman_id"])
    return {"rows": page, "total": total, "counts": dict(Counter(s["outcome"] for s in out))}


def session(session_id: str) -> list[dict]:
    """One chat's turns, oldest first, every column the log holds (the drawer replays it)."""
    if session_id.startswith("device:"):
        return []
    rows = turns(180, session_id=session_id)
    rows.sort(key=lambda t: t["ts"])
    for t in rows:
        t.pop("device_id", None)
    return rows


def overview(days: int = 7) -> dict:
    """Today's tiles and the "do now" list."""
    now = datetime.now(timezone.utc)
    rows = turns(days)
    by: dict[str, list[dict]] = defaultdict(list)
    for t in rows:
        by[_sid(t)].append(t)
    orders = _orders_after_chat(rows, days)
    outcomes = Counter(_session_outcome(sorted(ts, key=lambda t: t["ts"]), False) for ts in by.values())
    oc = Counter(t.get("outcome") for t in rows)
    ms = [t.get("ms") for t in rows if t.get("ms") is not None and not t.get("action")]
    tickets = _open_tickets()
    for t in tickets:
        t["hours"] = _hours(t.get("created_at"), now)
    sla = max(shop._i((shop.context().get("settings") or {}).get("shop_ticket_sla_hours"), 24), 1)
    late = [t for t in tickets if t["status"] == "new" and not t.get("first_seen_at") and (t["hours"] or 0) > sla]
    callbacks = [t for t in tickets if t["kind"] in ("callback", "account")]
    sold_out = Counter(c.get("code") for t in rows for c in (t.get("cards") or []) if isinstance(c, dict) and c.get("why") == "sold_out")
    day_ago = (now - timedelta(days=1)).isoformat()
    failed_day = sum(1 for t in rows if t["ts"] >= day_ago and t.get("outcome") in ("error", "fallback", "capped"))
    up = sum(1 for t in rows if t.get("rating") == "up")
    down = sum(1 for t in rows if t.get("rating") == "down")
    conv = len(by)
    return {
        "days": days,
        "tiles": {
            "conversations": conv, "turns": len(rows),
            "solved_pct": round(100 * outcomes.get("solved", 0) / conv) if conv else None,
            "escalated": outcomes.get("escalated", 0) + outcomes.get("ticket", 0),
            "orders_after_chat": len(orders),
            "orders_after_chat_bhd": round(sum(float(o.get("total_bhd") or 0) for o in orders), 3),
            "open_tickets": len(tickets), "oldest_ticket_hours": max((t["hours"] or 0 for t in tickets), default=None),
            "callbacks_waiting": len(callbacks), "misses": oc.get("miss", 0),
            "failed": oc.get("error", 0) + oc.get("fallback", 0) + oc.get("capped", 0),
            "p50_ms": _pct(ms, 0.5), "p95_ms": _pct(ms, 0.95),
            "thumbs_up_pct": round(100 * up / (up + down)) if up + down else None,
        },
        "do_now": {
            "late_tickets": [{"ref": t["ref"], "kind": t["kind"], "hours": t["hours"]} for t in late[:5]],
            "callbacks": [{"ref": t["ref"], "kind": t["kind"], "hours": t["hours"]} for t in callbacks[:5]],
            "sold_out_asks": [{"code": c, "times": n} for c, n in sold_out.most_common(3) if c],
            "failed_last_day": failed_day,
            "sla_hours": sla,
        },
    }


def insights(days: int = 30) -> dict:
    """What the chat data says: intents, asked-for-not-stocked (sourcing), asked-while-sold-out (reorder), policy
    questions with no approved answer ("Teach the assistant"), speed and failures by provider, thumbs down."""
    rows = turns(days)
    by_intent = Counter(t.get("intent") for t in rows if t.get("intent"))
    wanted: dict[str, dict] = {}
    for t in rows:
        if t.get("outcome") == "miss" and t.get("intent") in ("find", "fit", "add", "top", "photo"):
            key = str(t.get("query") or t.get("message") or "").strip().lower()[:120]
            if key:
                w = wanted.setdefault(key, {"asked": key, "times": 0, "last": t["ts"]})
                w["times"] += 1
                w["last"] = max(w["last"], t["ts"])
    sold: Counter = Counter()
    for t in rows:
        for c in t.get("cards") or []:
            if isinstance(c, dict) and c.get("why") == "sold_out" and c.get("code"):
                sold[c["code"]] += 1
    try:
        from app.market_assist import public_catalog
        names = {code: (it.get("display_name") or code) for code, it in (public_catalog().get("by_code") or {}).items()}
    except Exception:  # noqa: BLE001
        names = {}
    teach: dict[str, dict] = {}
    for t in rows:
        if t.get("intent") == "policy" and t.get("outcome") == "handoff":
            topic = t.get("topic") or "other"
            e = teach.setdefault(topic, {"topic": topic, "times": 0, "examples": []})
            e["times"] += 1
            if len(e["examples"]) < 3 and t.get("message"):
                e["examples"].append(t["message"])
    prov: dict[str, dict] = {}
    for t in rows:
        if t.get("action"):
            continue
        name = (t.get("providers") or "none").split(",")[0]
        p = prov.setdefault(name, {"provider": name, "turns": 0, "ms": [], "failed": 0})
        p["turns"] += 1
        if t.get("ms") is not None:
            p["ms"].append(t["ms"])
        if t.get("outcome") in ("error", "fallback", "capped"):
            p["failed"] += 1
    providers = [{"provider": p["provider"], "turns": p["turns"], "p50_ms": _pct(p["ms"], 0.5),
                  "p95_ms": _pct(p["ms"], 0.95), "failed": p["failed"]} for p in prov.values()]
    down = [{"ts": t["ts"], "intent": t.get("intent"), "message": t.get("message"), "reply": t.get("reply")}
            for t in rows if t.get("rating") == "down"][:20]
    return {
        "days": days,
        "intents": [{"intent": k, "turns": v} for k, v in by_intent.most_common()],
        "not_stocked": sorted(wanted.values(), key=lambda w: (-w["times"], w["last"]))[:25],
        "sold_out": [{"code": c, "name": names.get(str(c).upper(), c), "times": n} for c, n in sold.most_common(25)],
        "teach": sorted(teach.values(), key=lambda e: -e["times"]),
        "providers": sorted(providers, key=lambda p: -p["turns"]),
        "thumbs_down": down,
    }


def policies_get() -> dict:
    vals = shop.shop_settings()
    have = shop.policies(vals)
    return {"topics": list(shop.POLICY_TOPICS),
            "answers": [{"topic": k, **v} for k, v in have.items()]}


def policies_put(answers: list, by: str) -> dict:
    shop.update_shop_settings({"shop_ai_policies": json.dumps(answers or [], ensure_ascii=False)}, by=by)
    return policies_get()
