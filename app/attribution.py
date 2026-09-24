"""Customer-level attribution (trust plan §9 Step 3 item 6; audit C-06/C-07, critic #8).

Who a merchant "belongs to" is decided here, separately from the per-order routing in
app.shop.resolve_salesman, and every rule has one home:

  * pickable(rep)      — which reps the PUBLIC checkout may list or accept as a pick: active,
                         public_profile on, and a linked portal login (a rep with no login
                         cannot confirm an order, so the merchant must not be able to choose
                         him). The public list carries id + name only — never a referral code.
  * staff_conflict     — a salesman placing an order for a shop whose recorded rep is someone
                         else: the order is flagged (attribution_conflict) and an event says so;
                         nothing switches silently.
  * settle_sticky      — the merchant's sticky (first-touch) rep is written when the assigned
                         rep CONFIRMS or DELIVERS, not when the order is requested: until then
                         the proposed rep is only remembered as `first_ref`. An existing sticky
                         value is never overwritten here: the write is one conditional UPDATE
                         (… where sticky_salesman_id is null and salesman_id is null), so two
                         reps confirming a new merchant's orders at the same moment cannot both
                         win — the first commit does. A test order never settles anything.
  * assign_customer    — the admin's explicit, reasoned assignment (shop_customers.salesman_id):
                         audit_log 'shop.customer_assign' {from, to, reason} + a shop_admin_audit
                         row. The admin assignment beats every other rule in resolve_salesman.

Every writer here tolerates the memory layer being unavailable: an order or a status move
never fails because the merchant record could not be updated.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app import database

log = logging.getLogger(__name__)

REASON_MIN = 3
STICKY_SETTLE_STATUSES = ("confirmed", "delivered")


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _i(x, default: int = 0) -> int:
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return default


# ── the public rep picker ─────────────────────────────────────────────────────

def pickable(rep: dict | None) -> bool:
    """May a merchant choose this rep at checkout? Active, public profile, and a linked login."""
    if not rep:
        return False
    if rep.get("is_active") is False:
        return False
    if rep.get("public_profile") is False:
        return False
    return bool(str(rep.get("user_email") or "").strip())


def public_reps(ctx: dict) -> list[dict]:
    """The checkout picker: id + name of every pickable rep, in roster order. No referral codes,
    phones, emails or Focus names ever leave through the public payload."""
    return [{"id": s["id"], "name": s["name"]} for s in (ctx.get("salesmen") or []) if pickable(s)]


# ── staff orders for a shop that is someone else's ─────────────────────────────

def recorded_rep_id(cust: dict | None) -> int | None:
    """The rep on file for a merchant: the admin assignment first, else the sticky rep."""
    if not cust:
        return None
    return _i(cust.get("salesman_id")) or _i(cust.get("sticky_salesman_id")) or None


def staff_conflict(cust: dict | None, sm: dict | None) -> bool:
    """True when a staff member orders for a shop whose recorded rep is a different salesman."""
    rec = recorded_rep_id(cust)
    return bool(rec and sm and _i(sm.get("id")) and _i(sm.get("id")) != rec)


# ── the sticky rep settles at confirm / deliver ───────────────────────────────

def settle_sticky(client, order: dict, status: str | None = None) -> bool:
    """Write shop_customers.sticky_salesman_id = the order's rep, once, when the order reaches
    Confirmed or Delivered. Returns True only when a value was written. Never raises.

    The write is a single conditional UPDATE — the row must still have NO rep on file (no admin
    assignment, no earlier settle) at the moment the database applies it — so a read-then-write
    race between two reps cannot make the last writer win. Zero rows changed = already on file
    (or no such merchant) = False. A test order (is_test) never decides who a shop belongs to."""
    if status is not None and status not in STICKY_SETTLE_STATUSES:
        return False
    if (order or {}).get("is_test"):
        return False
    cid, sid = _i((order or {}).get("customer_id")), _i((order or {}).get("salesman_id"))
    if not cid or not sid:
        return False
    try:
        done = (client.table("shop_customers").update({"sticky_salesman_id": sid, "updated_at": _iso()})
                .eq("id", cid).is_("salesman_id", "null").is_("sticky_salesman_id", "null").execute().data or [])
        return bool(done)
    except Exception as e:  # noqa: BLE001 — the status move already happened
        log.warning("sticky settle for order %s failed: %s", (order or {}).get("order_no"), e)
        return False


# ── the admin's explicit assignment ───────────────────────────────────────────

def customer_rep(customer_id) -> dict | None:
    """{id, salesman_id, salesman_name, sticky_salesman_id, sticky_name, first_ref, shop} for the
    order drawer — names resolved from the roster; None when unknown or unavailable."""
    cid = _i(customer_id)
    if not cid:
        return None
    try:
        got = (database.get_client().table("shop_customers")
               .select("id,shop,salesman_id,sticky_salesman_id,first_ref,assigned_by,assigned_at")
               .eq("id", cid).limit(1).execute().data or [])
    except Exception as e:  # noqa: BLE001
        log.debug("customer_rep %s failed: %s", cid, e)
        return None
    if not got:
        return None
    c = got[0]
    from app import shop
    names = {int(s["id"]): s.get("name") for s in shop.context().get("salesmen") or []}
    return {"id": c["id"], "shop": c.get("shop"), "salesman_id": c.get("salesman_id"),
            "salesman_name": names.get(_i(c.get("salesman_id"))),
            "sticky_salesman_id": c.get("sticky_salesman_id"),
            "sticky_name": names.get(_i(c.get("sticky_salesman_id"))),
            "first_ref": c.get("first_ref"), "assigned_by": c.get("assigned_by"), "assigned_at": c.get("assigned_at")}


def assign_customer(customer_id, salesman_id, actor: str, reason: str | None) -> dict:
    """Admin: make `salesman_id` the merchant's rep. A reason is required (it is what the audit
    shows next to the change). Raises app.shop.ShopError with a readable message."""
    from app import shop
    from app.audit import log_event
    from app import shop_audit
    why = shop.clean(reason, 300)
    if len(why) < REASON_MIN:
        raise shop.ShopError("A reason is required to change a shop's rep.")
    cid = _i(customer_id)
    if not cid:
        raise shop.ShopError("Unknown merchant.")
    sm = shop._salesman_by(shop.context(), salesman_id) or shop._salesman_by_id(salesman_id)
    if not sm or not sm.get("is_active", True):
        raise shop.ShopError("Unknown or inactive salesman.")
    client = database.get_client()
    got = client.table("shop_customers").select("*").eq("id", cid).limit(1).execute().data or []
    if not got:
        raise shop.ShopError("Unknown merchant.")
    cust = got[0]
    prev_id = _i(cust.get("salesman_id")) or None
    names = {int(s["id"]): s.get("name") for s in shop.context().get("salesmen") or []}
    prev_name = names.get(prev_id) if prev_id else None
    now = _iso()
    if prev_id == int(sm["id"]):
        return {"customer_id": cid, "from": prev_id, "to": int(sm["id"]), "to_name": sm.get("name"),
                "changed": False, "shop": cust.get("shop")}
    upd = {"salesman_id": int(sm["id"]), "assigned_by": actor, "assigned_at": now, "updated_at": now}
    done = client.table("shop_customers").update(upd).eq("id", cid).execute().data or []
    if not done:
        raise shop.ShopError("Unknown merchant.")
    detail = {"customer_id": cid, "from": prev_id, "from_name": prev_name, "to": int(sm["id"]),
              "to_name": sm.get("name"), "reason": why}
    log_event(actor, "shop.customer_assign", detail=detail)
    before = {k: cust.get(k) for k in ("salesman_id", "assigned_by", "assigned_at", "sticky_salesman_id")}
    after = {**{k: upd.get(k) for k in ("salesman_id", "assigned_by", "assigned_at")},
             "sticky_salesman_id": cust.get("sticky_salesman_id"), "reason": why}
    shop_audit.record(actor, "shop_customer", cid, "assign", before, after)
    return {"customer_id": cid, "from": prev_id, "from_name": prev_name, "to": int(sm["id"]),
            "to_name": sm.get("name"), "changed": True, "shop": cust.get("shop"), "reason": why}
