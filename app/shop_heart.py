"""The order heart — Sprint 3 (R7c, 27-Sep-2026), as the owner simplified it: "as simple as
possible; most of the time the shop will not press OK, so the salesman must be able to".

What a rep or a shop ever sees or taps:  Received → Confirmed → Delivered   (or Cancelled).
The DB enum keeps packed / out_for_delivery (the storekeeper's pick-list stamps); every reader
shows them as "Confirmed" (visible_status / status_label) and no rep or desk button offers them.

Every line keeps three numbers:
  * qty            — what the shop requested. Never changed.
  * qty_confirmed  — what the rep confirmed (0 = unavailable; a substituted line is 0 and names
                     its substitute in substitute_item_code).
  * qty_delivered  — what was handed over (plain Delivered = qty_confirmed).
  Unavailable = requested − confirmed is derived, never stored. Every change carries a reason
  chip (CHANGE_REASONS); 'other' needs a note.

One editor serves Confirm (from Received) and Amend (after confirming): reduce, remove (0),
substitute, mark a backorder, add a line. Deliver is one tap (delivered = confirmed) or
"Deliver with changes" (what was really handed over + a reason, lines added at the shop).

Shop agreement — no shop-side approval, no pending state, no timeout. When a change is ADVERSE
to the shop the rep must tick "Shop agreed" and say how (whatsapp | phone | visit) or the call is
refused (ADVERSE_MSG); the tick is recorded on the event. Adverse = a substitute at a different
price, a price increase, a backorder, or a cut that takes the order below shop_min_order_bhd.
A plain stock reduction needs no tick.

Price lock — the shop pays the price it ordered at. A quantity change re-prices the line at the
ORDERED unit price (never today's book, never a silent zero for an item that has since become
unlisted or unpriced); the order's own cart-level discount (coupon / cart rule, as placed) is
shared pro rata over the original lines — a substitute takes the share of the line it replaced —
and never grows past the discount as placed; delivery stays as ordered. Lines added or substituted
by the rep are priced at the current book through shop.price_cart — and refused outright when
the book has no price for them.

Money is Decimal end to end (shop.dmoney, 3 dp, ROUND_HALF_UP): for every order
    total = Σ line totals − cart share + delivery        (exactly, to the fils)
at confirmed quantities after Confirm/Amend and at delivered quantities after Deliver-with-
changes (the delivered value becomes the order's agreed total). effective_money() — confirmed ??
original — is the one rule every total on an order is read with.

Nothing here works only after the migration (scripts/r7c_order_lines_qty_migration.sql):
confirm / amend with reductions and removals, plain Delivered, cancel, reopen and "shop told"
all run before it (the new columns are probed and skipped, the new line statuses map to the
old ones). Adding, substituting and delivering-with-changes need the new columns and say so.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

log = logging.getLogger(__name__)

# ── vocabulary ────────────────────────────────────────────────────────────────

# reason code → the chip a rep taps (order = chip layout); stored in shop_order_lines.change_reason
CHANGE_REASONS: dict[str, str] = {
    "out_of_stock": "Out of stock",
    "discontinued": "Discontinued",
    "price": "Price",
    "customer_changed": "Shop changed it",
    "substituted": "Substituted",
    "damaged": "Damaged",
    "other": "Other",
}
# what the SHOP may read beside a changed line — the reason's public label, never the rep's note.
# 'other' adds nothing beyond the numbers themselves.
PUBLIC_REASONS: dict[str, str] = {
    "out_of_stock": "Out of stock",
    "discontinued": "No longer available",
    "price": "Price change",
    "customer_changed": "As you asked",
    "substituted": "Replaced with a similar item",
    "damaged": "Damaged in stock",
}
REASON_NOTE_MIN = 3
AGREED_VIA: tuple[str, ...] = ("whatsapp", "phone", "visit")
NOTIFY_CHANNELS: tuple[str, ...] = ("whatsapp", "phone", "visit", "email")
ADDED_STAGES: tuple[str, ...] = ("confirm", "amend", "delivery")
# orders a rep may amend or deliver (packed / out_for_delivery read as Confirmed)
OPEN_CONFIRMED: tuple[str, ...] = ("confirmed", "packed", "out_for_delivery")
REOPEN_DAYS = 7
REOPEN_TO = {"delivered": "confirmed", "cancelled": "new"}
REOPEN_REASON_MIN = 3

# line statuses: 'removed' (pre-R7c) and 'unavailable' / 'substituted' all mean "confirmed 0"
LINE_OUT: tuple[str, ...] = ("removed", "unavailable", "substituted")
# before the migration the DB check allows only ok / changed / removed / backorder
_PRE_R7C_STATUS = {"unavailable": "removed", "substituted": "removed", "added": "ok"}

# the owner's three visible stages (plus Cancelled)
VISIBLE_STATUS = {"new": "new", "confirmed": "confirmed", "packed": "confirmed", "out_for_delivery": "confirmed",
                  "delivered": "delivered", "cancelled": "cancelled"}
VISIBLE_LABELS = {"new": "Received", "confirmed": "Confirmed", "delivered": "Delivered", "cancelled": "Cancelled"}
VISIBLE_STEPS: tuple[str, ...] = ("new", "confirmed", "delivered")

ADVERSE_MSG = "Tick 'Shop agreed' — this change costs the shop more or goes below the minimum"
AGREED_VIA_MSG = "Say how the shop agreed: WhatsApp, phone or visit."
REASON_REQUIRED_MSG = ("Pick a reason for {code} (out of stock, discontinued, price, shop changed it, substituted, "
                       "damaged or other).")
REASON_NOTE_MSG = "Say why in the note for {code} when the reason is 'other'."
MIGRATION_MSG = ("{what} needs scripts/r7c_order_lines_qty_migration.sql — until it is applied, reduce or remove "
                 "lines instead.")
USE_CONFIRM_MSG = "Use Confirm to confirm an order — every line gets its confirmed quantity there."
STAMP_ONLY_MSG = "Preparing / On the way are pick-list stamps — only the storekeeper sets them."
ALL_REMOVED_MSG = "Every line was removed — cancel the order instead."
REOPEN_WINDOW_MSG = f"Too late to reopen — an order can be reopened within {REOPEN_DAYS} days."
REOPEN_REASON_MSG = "Say why the order is reopened."
REOPEN_STATUS_MSG = "Only a delivered or cancelled order can be reopened."

# optional columns: probed, written only when present, dropped from a write that meets 42703 / PGRST204
LINE_R7C_COLS: tuple[str, ...] = ("qty_delivered", "change_reason", "substitute_item_code", "substitute_for_line",
                                  "added_at_stage", "unit_cost_bhd", "cost_source")
LINE_PRICED_COLS: tuple[str, ...] = ("unit_price_confirmed", "line_total_confirmed")
LINE_OPTIONAL: tuple[str, ...] = LINE_R7C_COLS + LINE_PRICED_COLS
ORDER_R7C_COLS: tuple[str, ...] = ("expected_delivery_date", "reopened_at", "reopened_by", "reopen_reason")
ORDER_OPTIONAL: tuple[str, ...] = ORDER_R7C_COLS + ("cancel_reason_code",)

_COST_Q = Decimal("0.0001")


def _shop():
    from app import shop
    return shop


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── readiness probes (the migration is applied by hand) ──────────────────────

def lines_ready() -> bool:
    """shop_order_lines has the R7c columns — and so the widened line_status check."""
    return _shop().has_column("shop_order_lines", "change_reason")


def orders_ready() -> bool:
    """shop_orders has the R7c columns — and so the widened cancel_reason_code check."""
    return _shop().has_column("shop_orders", "reopened_at")


def db_line_status(status: str, ready: bool) -> str:
    """The value the database accepts: the R7c statuses map to the old ones before the migration."""
    return status if ready else _PRE_R7C_STATUS.get(status, status)


# ── visible status ────────────────────────────────────────────────────────────

def visible_status(status: str | None) -> str:
    return VISIBLE_STATUS.get(str(status or ""), str(status or "new"))


def status_label(status: str | None) -> str:
    """Received / Confirmed / Delivered / Cancelled — packed and out_for_delivery read as Confirmed."""
    return VISIBLE_LABELS.get(visible_status(status), str(status or ""))


def order_steps(o: dict) -> list[dict]:
    """The three stages a shop sees, with done / current flags and the time each was reached. A
    cancelled order keeps nothing current (it carries `cancelled` on the order itself)."""
    vis = visible_status(o.get("status"))
    reached = VISIBLE_STEPS.index(vis) if vis in VISIBLE_STEPS else -1
    at_key = {"new": "created_at", "confirmed": "confirmed_at", "delivered": "delivered_at"}
    last = len(VISIBLE_STEPS) - 1
    out = []
    for i, s in enumerate(VISIBLE_STEPS):
        out.append({"status": s, "label": VISIBLE_LABELS[s],
                    "done": reached > i or (reached == last and i == last),
                    "current": reached == i,
                    "at": o.get(at_key[s]) if reached >= i else None})
    return out


def next_statuses(status: str | None, role: str | None, *, read_only: bool = False) -> list[str]:
    """The status moves a role is OFFERED. Reps and the desk: the forward step only — Received →
    Confirmed (through POST …/confirm), Confirmed → Delivered — plus Cancel. The storekeeper keeps
    his pick-list stamps. Management moves nothing."""
    s = _shop()
    st = str(status or "")
    if read_only:
        return []
    if role == "storekeeper":
        allowed = s.ROLE_STATUSES.get("storekeeper", ())
        return [x for x in s.NEXT_STATUS.get(st, ()) if x in allowed]
    if st == "new":
        return ["confirmed", "cancelled"]
    if st in OPEN_CONFIRMED:
        return ["delivered", "cancelled"]
    return []


def reopen_deadline(o: dict) -> datetime | None:
    """Until when an admin may reopen this delivered / cancelled order (None = not reopenable)."""
    st = o.get("status")
    if st not in REOPEN_TO:
        return None
    shop = _shop()
    since = shop._parse_ts(o.get("delivered_at" if st == "delivered" else "cancelled_at")) \
        or shop._parse_ts(o.get("updated_at"))
    return (since + timedelta(days=REOPEN_DAYS)) if since else None


def actions(o: dict, role: str | None, *, is_admin: bool = False, read_only: bool = False,
            now: datetime | None = None) -> list[str]:
    """What the drawer offers, as action keys: confirm · amend · deliver · cancel · reopen ·
    tell_shop. 'deliver' is both the one tap and "Deliver with changes" (POST …/deliver)."""
    if read_only:
        return []
    st = o.get("status")
    if role == "storekeeper":
        return []
    out: list[str] = []
    if st == "new":
        out += ["confirm", "cancel"]
    elif st in OPEN_CONFIRMED:
        out += ["deliver", "amend", "cancel"]
    elif is_admin:
        until = reopen_deadline(o)
        if until and (now or _now()) <= until:
            out.append("reopen")
    if o.get("customer_phone"):
        out.append("tell_shop")
    return out


# ── money ─────────────────────────────────────────────────────────────────────

def effective_money(confirmed, original) -> float:
    """confirmed ?? original, to the fils — the ONE rule every total on an order is read with."""
    return _shop().money(confirmed if confirmed is not None else original)


def order_total(o: dict) -> float:
    return effective_money(o.get("total_confirmed_bhd"), o.get("total_bhd"))


def is_added(ln: dict) -> bool:
    """A line the rep added (or a substitute) — not one the shop requested."""
    return bool(ln.get("added_at_stage")) or (ln.get("line_status") == "added")


def lock_unit(ln: dict) -> Decimal:
    """The price lock: the unit price this line is sold at — the confirmed unit once there is one
    (the price the shop was told), else the ordered one. Never today's book; never a silent zero:
    a line without any price on file is refused, not zeroed."""
    shop = _shop()
    for k in ("unit_price_confirmed", "unit_price_bhd", "list_price_bhd"):
        if ln.get(k) is not None:
            return shop.dmoney(ln[k])
    raise shop.ShopError(f"{ln.get('item_code')} has no price on this order — remove it or ask the office.")


def qty_confirmed_eff(ln: dict) -> int:
    """Confirmed quantity as it stands: the stored one, else the requested one (0 for a line out)."""
    shop = _shop()
    qc = ln.get("qty_confirmed")
    if qc is None:
        return 0 if (ln.get("line_status") or "ok") in LINE_OUT else shop._i(ln.get("qty"))
    return max(0, shop._i(qc))


def qty_delivered_eff(ln: dict, order_status: str | None) -> int | None:
    """Delivered quantity: the stored one, else — on a delivered order — the confirmed one (plain
    Delivered, or a delivery recorded before the column existed). None while not delivered."""
    if ln.get("qty_delivered") is not None:
        return max(0, _shop()._i(ln["qty_delivered"]))
    if order_status == "delivered":
        return qty_confirmed_eff(ln)
    return None


def carries_share(ln: dict, by_id: dict, _hops: int = 0) -> bool:
    """Does this line share the order's cart-level discount? A requested line does; a substitute
    (substitute_for_line set) inherits the share of the line it replaced — so a substitute of a
    requested line does, a substitute of a plain added line does not; any other added line never."""
    if not is_added(ln):
        return True
    sf = ln.get("substitute_for_line")
    if sf is None or _hops > 20:
        return False
    try:
        parent = by_id.get(int(sf))
    except (TypeError, ValueError):
        return False
    return parent is not None and carries_share(parent, by_id, _hops + 1)


def compute_totals(order: dict, lines: list[dict], qty_of) -> dict:
    """Pure: the order's money at the quantities `qty_of(line)` gives. Every line at its locked unit
    price; the ORIGINAL lines — and a substitute in the place of one (carries_share) — share the
    order's own cart-level discount (as placed) pro rata, never more than the discount as placed nor
    more than those lines' value; other added lines carry none; delivery stays as ordered.
    Decimal, 3 dp:
        total_bhd == items_bhd − cart_discount_bhd + delivery_bhd       (exactly)."""
    shop = _shop()
    D0 = shop.D0
    dm = shop.dmoney
    orig = [ln for ln in lines if not is_added(ln)]
    by_id: dict = {}
    for ln in lines:
        if ln.get("id") is not None:
            try:
                by_id[int(ln["id"])] = ln
            except (TypeError, ValueError):
                pass
    s0 = sum((dm(ln.get("line_total_bhd")) for ln in orig), D0)
    line_disc0 = sum((dm(ln.get("discount_bhd")) for ln in orig), D0)
    cart0 = max(D0, dm(shop._d(order.get("discount_bhd")) - line_disc0))
    per: list[dict] = []
    items_total = s1_orig = subtotal = D0
    units = items = 0
    for ln in lines:
        q = max(0, int(qty_of(ln)))
        unit = lock_unit(ln)
        lt = dm(unit * q)
        lp = dm(ln["list_price_bhd"]) if ln.get("list_price_bhd") is not None else unit
        subtotal += dm(lp * q)
        items_total += lt
        if carries_share(ln, by_id):
            s1_orig += lt
        if q > 0:
            units += q
            items += 1
        per.append({"line": ln, "qty": q, "unit": unit, "total": lt})
    cart = D0
    if cart0 > 0 and s0 > 0:
        # a pricier substitute or a raised quantity never grows the discount past what was placed
        cart = min(dm(cart0 * s1_orig / s0), cart0, s1_orig)
    delivery = dm(order.get("delivery_bhd"))
    total = dm(items_total - cart + delivery)
    return {"lines": per, "items_bhd": dm(items_total), "cart_discount_bhd": cart, "delivery_bhd": delivery,
            "subtotal_bhd": dm(subtotal), "discount_bhd": dm(subtotal - items_total + cart),
            "total_bhd": total, "units": units, "items": items}


def _totals_out(t: dict) -> dict:
    """compute_totals at the JSON edge (floats of the exact 3-dp figures)."""
    shop = _shop()
    return shop.edge_floats({k: v for k, v in t.items() if k != "lines"})


# ── expected delivery ─────────────────────────────────────────────────────────

_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
             "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_ISO_DAY = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def resolve_eta(text: str | None, explicit=None, today: date | None = None) -> date | None:
    """The ETA chip (or typed text) as a Bahrain calendar date: Today / Tomorrow / Day after
    tomorrow, a weekday name (its next occurrence), or an ISO date. 'With my next visit' and
    anything else unknown = None. `explicit` (an ISO date from a date picker) wins when valid —
    today up to 90 days ahead."""
    shop = _shop()
    t0 = today or shop.bahrain_today()

    def ok(d: date | None) -> date | None:
        return d if d is not None and t0 <= d <= t0 + timedelta(days=90) else None
    if explicit:
        try:
            got = ok(explicit if isinstance(explicit, date) else date.fromisoformat(str(explicit).strip()[:10]))
        except ValueError:
            got = None
        if got:
            return got
    s = re.sub(r"\s+", " ", str(text or "").strip().lower())
    if not s:
        return None
    if "day after tomorrow" in s or "بعد غد" in s:
        return t0 + timedelta(days=2)
    if s.startswith("today") or s == "اليوم":
        return t0
    if s.startswith("tomorrow") or s.startswith("غد"):
        return t0 + timedelta(days=1)
    m = _ISO_DAY.search(s)
    if m:
        try:
            return ok(date.fromisoformat(m.group(1)))
        except ValueError:
            return None
    word = s.split(" ")[0].strip(",.")
    if word in _WEEKDAYS:
        ahead = (_WEEKDAYS[word] - t0.weekday()) % 7 or 7
        return t0 + timedelta(days=ahead)
    return None


# ── reasons and the shop's agreement ──────────────────────────────────────────

def _reason(raw, code: str, note: str | None, default: str | None = None) -> str:
    shop = _shop()
    r = str(raw or "").strip().lower() or (default or "")
    if r not in CHANGE_REASONS:
        raise shop.ShopError(REASON_REQUIRED_MSG.format(code=code))
    if r == "other" and len(note or "") < REASON_NOTE_MIN:
        raise shop.ShopError(REASON_NOTE_MSG.format(code=code))
    return r


LEGACY_NOTE = "pre-R7c client"


def is_legacy_confirm(changes, added_lines, stage: str) -> bool:
    """A Confirm from an order desk that predates R7c (the deploy window: the API is new, a phone
    still holds the old page): changed lines, not one reason chip, no added line, no substitute.
    Such a request is accepted with reason 'other' and the note LEGACY_NOTE instead of refused —
    an adverse change still needs "Shop agreed". Amend did not exist before R7c: never legacy."""
    if stage != "confirm" or added_lines:
        return False
    chs = [c or {} for c in (changes or [])]
    if not chs:
        return False
    return not any(c.get("reason") or c.get("change_reason") or c.get("substitute_item_code") for c in chs)


def is_stale(o: dict, expected_updated_at) -> bool:
    """The client's compare-and-swap (R7c review): it sends the order's updated_at as it read it
    (`expected_updated_at`); a different value on the order now means someone else wrote in between
    and the edit must lose (CAS_CONFLICT_MSG, 409). None / '' = an older client: not checked."""
    if expected_updated_at in (None, ""):
        return False
    shop = _shop()
    cur = o.get("updated_at")
    a, b = shop._parse_ts(expected_updated_at), shop._parse_ts(cur)
    if a is not None and b is not None:
        return a != b
    return str(expected_updated_at).strip() != str(cur or "").strip()


def clean_agreed(raw) -> dict | None:
    """{'via': whatsapp | phone | visit} → the validated via, or None when nothing was ticked."""
    if raw in (None, "", {}, False):
        return None
    via = raw.get("via") if isinstance(raw, dict) else raw
    via = str(via or "").strip().lower()
    if via not in AGREED_VIA:
        raise _shop().ShopError(AGREED_VIA_MSG)
    return {"via": via}


def public_reason(code) -> str | None:
    return PUBLIC_REASONS.get(str(code or "").strip().lower())


# ── pricing a line the rep adds (current book) ────────────────────────────────

def _cost_fields(ctx: dict, code: str) -> dict:
    shop = _shop()
    cost = shop.cost_for(ctx, code)
    if cost is None:
        return {"unit_cost_bhd": None, "cost_source": None}
    return {"unit_cost_bhd": float(Decimal(str(cost)).quantize(_COST_Q)), "cost_source": shop.cost_source_for(ctx, code)}


def price_new_line(o: dict, code: str, qty: int, ctx: dict) -> dict:
    """A line the rep adds or substitutes, priced at TODAY's book through the one pricing engine
    (item rules, the rep's offers, the margin floor). Refused — never a zero — when the book cannot
    price it (unlisted, price on request, below its MOQ)."""
    shop = _shop()
    # one line on its own is no cart: the minimum (and its gap-filler search) does not apply here
    one = {**ctx, "settings": {**(ctx.get("settings") or {}), "shop_min_order_bhd": "0"}}
    q = shop.price_cart([{"item_code": code, "qty": qty}], None, o.get("referral_code"), ctx=one,
                        staff=True, force_backorder=True)
    lines = q.get("lines") or []
    if not lines:
        raise shop.ShopError(f"{code} can't be added.")
    pl = lines[0]
    if pl.get("unavailable") or not pl.get("unit_price_bhd"):
        raise shop.ShopError(f"{pl.get('item_code') or code}: {pl.get('blocked_reason') or 'no trade price'} "
                             f"It can't be added.")
    return pl


def _new_row(o: dict, pl: dict, qty: int, *, stage: str, reason: str, note: str | None, ctx: dict,
             substitute_for: int | None, delivered: bool) -> dict:
    """The shop_order_lines row for an added / substitute line (every R7c column: only written once
    the migration is in). Requested = confirmed (= delivered at the door) = the quantity added."""
    shop = _shop()
    unit, total = shop.dmoney(pl["unit_price_bhd"]), shop.dmoney(pl["line_total_bhd"])
    row = {"order_id": o["id"], "item_code": pl["item_code"], "display_name": pl.get("display_name"),
           "spec": pl.get("spec"), "image_url": pl.get("image_url"), "qty": qty,
           "list_price_bhd": shop.money(pl.get("list_price_bhd")), "unit_price_bhd": float(unit),
           "discount_bhd": shop.money(pl.get("discount_bhd")), "line_total_bhd": float(total),
           "stock_status": pl.get("stock_status"), "backorder": bool(pl.get("backorder")) and not delivered,
           "line_status": "added", "rule_ids": [a["rule_id"] for a in pl.get("applied") or []] or None,
           "qty_confirmed": qty, "unit_price_confirmed": float(unit), "line_total_confirmed": float(total),
           "change_reason": reason, "substitute_for_line": substitute_for, "added_at_stage": stage,
           "note": note or None}
    row.update(_cost_fields(ctx, pl["item_code"]))
    if delivered:
        row["qty_delivered"] = qty
    return row


# ── the editor: one plan behind Confirm and Amend ─────────────────────────────

def _snapshot(ln: dict) -> dict:
    return {"qty_confirmed": qty_confirmed_eff(ln), "line_status": ln.get("line_status") or "ok",
            "substitute_item_code": ln.get("substitute_item_code")}


def _derive_status(ln: dict, qc: int, *, substituted: bool, mark_backorder: bool) -> str:
    if substituted:
        return "substituted"
    if qc <= 0:
        return "unavailable"
    if is_added(ln):
        return "added"
    if mark_backorder:
        return "backorder"
    if qc != _shop()._i(ln.get("qty")):
        return "changed"
    return "backorder" if ln.get("backorder") else "ok"


def plan_edit(o: dict, changes, added_lines, *, stage: str, ctx: dict, ready: bool, shop_agreed=None) -> dict:
    """Decide a Confirm / Amend in memory (nothing is written here): the per-line updates, the new
    rows, the money, which changes are adverse, and the event's per-line before/after. Raises
    ShopError for anything the rep must fix — an unknown line, a missing reason, an unpriceable
    added item, every line removed, or an adverse change without "Shop agreed"."""
    shop = _shop()
    if stage not in ("confirm", "amend"):
        raise ValueError(stage)
    by_id = {int(ln["id"]): ln for ln in o.get("lines") or []}
    state = {lid: {**_snapshot(ln), "reason": None, "note": None, "mark_backorder": False, "sub": None}
             for lid, ln in by_id.items()}
    seen: set[int] = set()
    subs: list[tuple[int, str, int, str | None]] = []       # (line_id, code, qty, note)
    legacy = is_legacy_confirm(changes, added_lines, stage)
    for ch in (changes or []):
        ch = ch or {}
        lid = shop._i(ch.get("line_id"))
        ln = by_id.get(lid)
        if not ln:
            raise shop.ShopError("Unknown order line.")
        if lid in seen:
            raise shop.ShopError(f"{ln['item_code']} is listed twice — one change per line.")
        seen.add(lid)
        code = ln["item_code"]
        st_in = str(ch.get("line_status") or "").strip().lower()
        note = shop.clean(ch.get("note"), 200) or None
        sub_code = shop.clean(ch.get("substitute_item_code"), 64) or None
        cur = state[lid]
        if sub_code:
            if not ready:
                raise shop.ShopError(MIGRATION_MSG.format(what="Substituting a line"))
            if sub_code.upper() == str(code).upper():
                raise shop.ShopError(f"Pick a different item to substitute {code}.")
            sq = shop._i(ch.get("substitute_qty")) or shop._i(ch.get("qty_confirmed")) or cur["qty_confirmed"] \
                or shop._i(ln.get("qty"))
            if sq <= 0 or sq > shop.MAX_QTY:
                raise shop.ShopError(f"Substitute quantity for {code} must be between 1 and {shop.MAX_QTY}.")
            qc = 0
            subs.append((lid, sub_code, sq, note))
        elif st_in in ("removed", "unavailable"):
            qc = 0
        else:
            raw = ch.get("qty_confirmed")
            qc = cur["qty_confirmed"] if raw is None else shop._i(raw, -1)
            if qc < 0:
                raise shop.ShopError(f"Confirmed quantity for {code} must be 0 or more.")
            if qc > shop.MAX_QTY:
                raise shop.ShopError(f"Confirmed quantity for {code} is too large.")
        mark_bo = (st_in == "backorder" or bool(ch.get("backorder"))) and qc > 0
        status_after = _derive_status(ln, qc, substituted=bool(sub_code), mark_backorder=mark_bo)
        changed = (qc != cur["qty_confirmed"] or status_after != cur["line_status"] or bool(sub_code)
                   or (not sub_code and cur["substitute_item_code"] and qc > 0))
        if changed and legacy:
            # an order desk from before R7c sends no reason chip: 'other', and the note says so
            note = (LEGACY_NOTE + (f": {note}" if note else ""))[:200]
            cur["reason"] = _reason("other", code, note)
        elif changed:
            cur["reason"] = _reason(ch.get("reason") or ch.get("change_reason"), code, note,
                                    default=("substituted" if sub_code else None))
        cur.update(qty_confirmed=qc, line_status=status_after, note=note, mark_backorder=mark_bo,
                   sub=sub_code, changed=changed)
    # a line re-substituted or restored: the substitute it had goes (confirmed 0, 'unavailable') in
    # the same plan — never two live lines in the place of one
    kids: dict[int, list[int]] = {}
    for kid_id, kl in by_id.items():
        if kl.get("substitute_for_line") is not None:
            kids.setdefault(shop._i(kl.get("substitute_for_line")), []).append(kid_id)

    def live_substitutes(lid: int) -> list[int]:
        """The lines standing in the place of `lid` now: its substitutes, and — where a substitute was
        itself substituted — that one's, down the chain."""
        out, stack, visited = [], list(kids.get(lid, [])), {lid}
        while stack:
            k = stack.pop()
            if k in visited:
                continue
            visited.add(k)
            if state[k]["qty_confirmed"] > 0:
                out.append(k)
            else:
                stack.extend(kids.get(k, []))
        return sorted(out)
    for lid in sorted(seen):
        cur = state[lid]
        if not cur.get("changed") or not (cur["sub"] or cur["qty_confirmed"] > 0):
            continue
        for kid_id in live_substitutes(lid):
            k = state[kid_id]
            if kid_id in seen and k.get("changed"):      # sent unchanged = not a choice; changed = one
                raise shop.ShopError(f"{by_id[kid_id]['item_code']} replaces {by_id[lid]['item_code']} — "
                                     f"keep one of them, not both.")
            k.update(qty_confirmed=0, line_status="unavailable", reason=cur["reason"], note=cur["note"],
                     mark_backorder=False, sub=None, changed=True)
    # added lines (a new request, priced at today's book)
    news: list[dict] = []
    for sub_lid, sub_code, sq, note in subs:
        pl = price_new_line(o, sub_code, sq, ctx)
        news.append(_new_row(o, pl, sq, stage=stage, reason="substituted", note=note, ctx=ctx,
                             substitute_for=sub_lid, delivered=False) | {"_pl": pl})
        state[sub_lid]["sub"] = pl["item_code"]
    for a in (added_lines or []):
        a = a or {}
        if not ready:
            raise shop.ShopError(MIGRATION_MSG.format(what="Adding a line"))
        code = shop.clean(a.get("item_code"), 64)
        qty = shop._i(a.get("qty"))
        if not code:
            raise shop.ShopError("Pick the item to add.")
        if qty <= 0 or qty > shop.MAX_QTY:
            raise shop.ShopError(f"Quantity for {code} must be between 1 and {shop.MAX_QTY}.")
        note = shop.clean(a.get("note"), 200) or None
        reason = _reason(a.get("reason"), code, note, default="customer_changed")
        pl = price_new_line(o, code, qty, ctx)
        news.append(_new_row(o, pl, qty, stage=stage, reason=reason, note=note, ctx=ctx, substitute_for=None,
                             delivered=False) | {"_pl": pl})
    # the money, before and after
    after_lines = []
    for lid, ln in by_id.items():
        s = state[lid]
        after_lines.append({**ln, "qty_confirmed": s["qty_confirmed"], "line_status": s["line_status"]})
    after_lines += [{k: v for k, v in r.items() if k != "_pl"} for r in news]
    if not any(shop._i(x.get("qty_confirmed")) > 0 for x in after_lines):
        raise shop.ShopError(ALL_REMOVED_MSG)
    totals = compute_totals(o, after_lines, lambda x: shop._i(x.get("qty_confirmed")))
    delivery = shop.dmoney(o.get("delivery_bhd"))
    before_total = shop.dmoney(o.get("total_bhd") if stage == "confirm" or o.get("total_confirmed_bhd") is None
                               else o.get("total_confirmed_bhd"))
    # adverse to the shop → the rep's "Shop agreed" tick is required
    adverse: list[dict] = []
    for r in news:
        pl = r["_pl"]
        if r.get("substitute_for_line"):
            orig = by_id[int(r["substitute_for_line"])]
            u0, u1 = lock_unit(orig), shop.dmoney(pl["unit_price_bhd"])
            if u1 != u0:
                adverse.append({"kind": "substitute_price", "item_code": orig["item_code"],
                                "substitute": pl["item_code"], "from": float(u0), "to": float(u1)})
        if pl.get("backorder"):
            adverse.append({"kind": "backorder", "item_code": pl["item_code"]})
    for lid, ln in by_id.items():
        s = state[lid]
        if s["mark_backorder"] and not ln.get("backorder") and (ln.get("line_status") or "ok") != "backorder":
            adverse.append({"kind": "backorder", "item_code": ln["item_code"]})
    for x in totals["lines"]:
        ln = x["line"]
        if ln.get("id") in by_id and x["qty"] > 0 and x["unit"] > lock_unit(by_id[ln["id"]]):
            adverse.append({"kind": "price_increase", "item_code": ln["item_code"]})   # never under the lock
    min_order = shop.dmoney((ctx.get("settings") or {}).get("shop_min_order_bhd"))
    if min_order > 0 and (before_total - delivery) >= min_order > (totals["total_bhd"] - delivery):
        adverse.append({"kind": "below_minimum", "minimum_bhd": float(min_order),
                        "from": float(before_total), "to": float(totals["total_bhd"])})
    agreed = clean_agreed(shop_agreed)
    if adverse and not agreed:
        raise shop.ShopError(ADVERSE_MSG)
    # the writes, decided
    has_priced = shop.has_column("shop_order_lines", LINE_PRICED_COLS[0])
    per_total = {x["line"].get("id"): x for x in totals["lines"] if x["line"].get("id") in by_id}
    line_updates: dict[int, dict] = {}
    event_lines: list[dict] = []
    changed_legacy: list[dict] = []
    removed_legacy: list[str] = []
    for lid, ln in by_id.items():
        s = state[lid]
        upd: dict = {}
        if ln.get("qty_confirmed") is None or shop._i(ln.get("qty_confirmed")) != s["qty_confirmed"]:
            upd["qty_confirmed"] = s["qty_confirmed"]
        db_status = db_line_status(s["line_status"], ready)
        if (ln.get("line_status") or "ok") != db_status:
            upd["line_status"] = db_status
        if s.get("changed"):
            if s["note"] is not None:
                upd["note"] = s["note"]
            if ready:
                upd["change_reason"] = s["reason"]
                upd["substitute_item_code"] = s["sub"]
        if has_priced:
            pt = per_total[lid]
            unit, lt = float(pt["unit"]), float(pt["total"])
            if ln.get("unit_price_confirmed") is None or shop.money(ln.get("unit_price_confirmed")) != unit:
                upd["unit_price_confirmed"] = unit
            if ln.get("line_total_confirmed") is None or shop.money(ln.get("line_total_confirmed")) != lt:
                upd["line_total_confirmed"] = lt
        if upd:
            line_updates[lid] = upd
        if s.get("changed"):
            b = _snapshot(ln)
            event_lines.append({"line_id": lid, "item_code": ln["item_code"],
                                "before": {"qty_confirmed": b["qty_confirmed"], "line_status": b["line_status"]},
                                "after": {"qty_confirmed": s["qty_confirmed"], "line_status": s["line_status"]},
                                "requested": shop._i(ln.get("qty")), "reason": s["reason"], "note": s["note"],
                                "substitute_item_code": s["sub"], "unit_price_bhd": float(lock_unit(ln))})
            if s["qty_confirmed"] == 0:
                removed_legacy.append(ln["item_code"])
            elif s["qty_confirmed"] != shop._i(ln.get("qty")):
                changed_legacy.append({"item_code": ln["item_code"], "from": shop._i(ln.get("qty")),
                                       "to": s["qty_confirmed"]})
    new_rows = [{k: v for k, v in r.items() if k != "_pl"} for r in news]
    added_event = [{"item_code": r["item_code"], "qty": r["qty"], "unit_price_bhd": r["unit_price_bhd"],
                    "line_total_bhd": r["line_total_bhd"], "reason": r["change_reason"],
                    "substitute_for_line": r.get("substitute_for_line")} for r in new_rows]
    return {"line_updates": line_updates, "new_rows": new_rows, "totals": totals, "adverse": adverse,
            "agreed": agreed, "event_lines": event_lines, "added": added_event, "changed": changed_legacy,
            "removed": removed_legacy, "before_total": before_total,
            "legacy": legacy and bool(event_lines)}


# ── writes: header compare-and-swap, then lines, then the event (reverted on failure) ──

def pin_updated(q, updated_at):
    """Compare-and-swap on updated_at as well as the status: a NULL is matched with IS NULL."""
    return q.is_("updated_at", "null") if updated_at is None else q.eq("updated_at", updated_at)


def _write_line(client, lid: int, upd: dict, dropped: set) -> None:
    """One line update; an optional column that vanished under a cached probe is forgotten and the
    update runs once more without it (and every later line skips it)."""
    shop = _shop()
    upd = {k: v for k, v in upd.items() if k not in dropped}
    if not upd:
        return
    try:
        client.table("shop_order_lines").update(upd).eq("id", lid).execute()
    except Exception as e:  # noqa: BLE001 — only the missing-column case is retried
        named = [c for c in upd if c in LINE_OPTIONAL]
        if not named or not shop._missing_column(e):
            raise
        for c in named:
            shop._forget_column("shop_order_lines", c)
            dropped.add(c)
        log.warning("shop_order_lines.%s vanished after a cached hit — writing without it: %s", ", ".join(named), e)
        rest = {k: v for k, v in upd.items() if k not in named}
        if rest:
            client.table("shop_order_lines").update(rest).eq("id", lid).execute()


def _insert_lines(client, rows: list[dict]) -> list[dict]:
    shop = _shop()
    if not rows:
        return []
    try:
        return client.table("shop_order_lines").insert(rows).execute().data or []
    except Exception as e:  # noqa: BLE001 — only the missing-column case is retried
        named = sorted({c for r in rows for c in r if c in LINE_OPTIONAL})
        if not named or not shop._missing_column(e):
            raise
        for c in named:
            shop._forget_column("shop_order_lines", c)
        log.warning("shop_order_lines R7c columns vanished after a cached hit — inserting without them: %s", e)
        return client.table("shop_order_lines").insert(
            [{k: v for k, v in r.items() if k not in named} for r in rows]).execute().data or []


def write_lines(client, before_lines: dict[int, dict], line_updates: dict[int, dict], new_rows: list[dict]):
    """Apply line updates and inserts. On success returns undo() (puts every touched line back and
    removes the rows this call inserted); on failure undoes its own partial work and re-raises."""
    dropped: set = set()
    done: list[int] = []
    inserted: list[int] = []

    def undo() -> None:
        for lid in done:
            ln = before_lines.get(lid) or {}
            back = {k: ln.get(k) for k in line_updates[lid]}
            try:
                _write_line(client, lid, back, dropped)
            except Exception as e:  # noqa: BLE001 — best effort; the header revert is what matters
                log.error("line %s could not be put back: %s", lid, e)
        for rid in inserted:
            try:     # a row this very request inserted and nobody has seen: not an order line yet
                client.table("shop_order_lines").delete().eq("id", rid).execute()
            except Exception as e:  # noqa: BLE001
                log.error("added line %s could not be removed after a failed write: %s", rid, e)
    try:
        for lid, upd in line_updates.items():
            _write_line(client, lid, upd, dropped)
            done.append(lid)
        got = _insert_lines(client, new_rows)
        inserted += [r["id"] for r in got if r.get("id") is not None]
        return undo, got
    except Exception:
        undo()
        raise


def _revert_header(order_id: int, back: dict, *, status: str, updated_at: str, order_no=None, err=None,
                   pins: dict | None = None) -> None:
    """Put the header back after a failure that followed its swap — itself a compare-and-swap on
    the status and the updated_at (and any `pins`, e.g. the confirmed_at) just written, so nobody
    else's later move is ever undone."""
    shop = _shop()

    def _run(q):
        q = q.eq("id", order_id).eq("status", status).eq("updated_at", updated_at)
        for k, v in (pins or {}).items():
            q = q.eq(k, v)
        return q.execute().data or []
    try:
        done = shop._update_optional("shop_orders", back, ORDER_OPTIONAL, _run)
        log.error("order %s: write failed after the header swap (%s) — header %s", order_no or order_id, err,
                  "reverted" if done else "NOT reverted (moved again already)")
    except Exception as e2:  # noqa: BLE001
        log.error("order %s: write failed (%s) and the header revert failed too: %s", order_no or order_id, err, e2)


def _edit(order_id: int, changes, expected_delivery, note, actor: str, *, stage: str, added_lines=None,
          shop_agreed=None, expected_delivery_date=None, expected_updated_at=None) -> dict:
    shop = _shop()
    o = shop.get_order(order_id)
    if not o:
        raise shop.ShopError("Order not found.")
    if is_stale(o, expected_updated_at):          # the editor was opened on an older read
        raise shop.ShopError(shop.CAS_CONFLICT_MSG)
    st = o["status"]
    if stage == "confirm" and st != "new":
        if st in OPEN_CONFIRMED:
            raise shop.ShopError("This order is already confirmed — use Amend to change it.")
        raise shop.ShopError(f"Cannot confirm an order that is {status_label(st).lower()}.")
    if stage == "amend" and st not in OPEN_CONFIRMED:
        if st == "new":
            raise shop.ShopError("Confirm the order first — Amend changes a confirmed order.")
        raise shop.ShopError(f"A {status_label(st).lower()} order can't be amended"
                             + (" — an admin can reopen it first." if st in REOPEN_TO else "."))
    ctx = shop.context()
    ready = lines_ready()
    plan = plan_edit(o, changes, added_lines, stage=stage, ctx=ctx, ready=ready, shop_agreed=shop_agreed)
    totals = plan["totals"]
    now = shop._iso()
    eta_text = shop.clean(expected_delivery, 120) or None
    upd_o: dict = {"updated_at": now,
                   "subtotal_confirmed_bhd": float(totals["subtotal_bhd"]),
                   "total_confirmed_bhd": float(totals["total_bhd"])}
    if stage == "confirm":
        upd_o.update(status="confirmed", confirmed_at=now, expected_delivery=eta_text)
    elif eta_text is not None:
        upd_o["expected_delivery"] = eta_text
    eta_date = resolve_eta(eta_text, expected_delivery_date)
    if (eta_text is not None or expected_delivery_date or stage == "confirm") \
            and shop.has_column("shop_orders", "expected_delivery_date"):
        upd_o["expected_delivery_date"] = eta_date.isoformat() if eta_date else None
    client = shop.get_client()
    swapped = shop._update_optional(
        "shop_orders", upd_o, ORDER_OPTIONAL,
        lambda q: pin_updated(q.eq("id", order_id).eq("status", st), o.get("updated_at")).execute().data or [])
    if not swapped:
        raise shop.ShopError(shop.CAS_CONFLICT_MSG)
    agreed = dict(plan["agreed"], by=actor, at=now, **({"for": [a["kind"] for a in plan["adverse"]]}
                                                        if plan["adverse"] else {})) if plan["agreed"] else None
    detail = {"note": shop.clean(note, 500) or None, "from": st, "stage": stage,
              "changed": plan["changed"], "removed": plan["removed"],
              "lines": plan["event_lines"], "added": plan["added"],
              "adverse": [a["kind"] for a in plan["adverse"]], "shop_agreed": agreed,
              "total_before": float(plan["before_total"]), "total_after": float(totals["total_bhd"]),
              "expected_delivery": upd_o.get("expected_delivery", o.get("expected_delivery")),
              "expected_delivery_date": eta_date.isoformat() if eta_date else None}
    if plan.get("legacy"):
        detail["legacy_client"] = True          # reasons defaulted to 'other' (is_legacy_confirm)
    before_lines = {int(ln["id"]): ln for ln in o.get("lines") or []}
    undo = None
    try:
        undo, _got = write_lines(client, before_lines, plan["line_updates"], plan["new_rows"])
        client.table("shop_order_events").insert({
            "order_id": order_id, "actor": actor, "event": "status:confirmed" if stage == "confirm" else "amended",
            "detail": detail}).execute()
    except Exception as e:  # noqa: BLE001 — whatever it was, lines and header go back first
        if undo:
            undo()
        back = {k: o.get(k) for k in upd_o}
        _revert_header(order_id, back, status=upd_o.get("status", st), updated_at=now,
                       order_no=o.get("order_no"), err=e,
                       pins={"confirmed_at": now} if stage == "confirm" else None)
        raise
    if stage == "confirm":
        from app import attribution
        attribution.settle_sticky(client, o, "confirmed")     # the assigned rep confirmed: first-touch rep settles
    out = shop.get_order(order_id) or {}
    out["totals"] = _totals_out(totals)
    out["changed"], out["removed"] = plan["changed"], plan["removed"]
    out["adverse"], out["shop_agreed"] = plan["adverse"], agreed
    return out


def confirm_order(order_id: int, changes, expected_delivery: str | None, note: str | None, actor: str, *,
                  added_lines=None, shop_agreed=None, expected_delivery_date=None,
                  expected_updated_at=None) -> dict:
    """Received → Confirmed with the editor (reduce, remove, substitute, backorder, add a line).
    Compare-and-swap on the status and updated_at read (and on the client's own read, when it sends
    `expected_updated_at`); lines and the 'status:confirmed' event follow, and any failure puts the
    header and every touched line back."""
    return _edit(order_id, changes, expected_delivery, note, actor, stage="confirm", added_lines=added_lines,
                 shop_agreed=shop_agreed, expected_delivery_date=expected_delivery_date,
                 expected_updated_at=expected_updated_at)


def amend_order(order_id: int, changes, expected_delivery: str | None, note: str | None, actor: str, *,
                added_lines=None, shop_agreed=None, expected_delivery_date=None,
                expected_updated_at=None) -> dict:
    """The same editor on a confirmed order (confirmed / packed / out_for_delivery): the status
    stays, the confirmed figures move, an 'amended' event carries the per-line before/after."""
    return _edit(order_id, changes, expected_delivery, note, actor, stage="amend", added_lines=added_lines,
                 shop_agreed=shop_agreed, expected_delivery_date=expected_delivery_date,
                 expected_updated_at=expected_updated_at)


# ── Delivered ─────────────────────────────────────────────────────────────────

def fill_delivered(client, o: dict) -> int:
    """Plain Delivered: qty_delivered = qty_confirmed on every line still without one. Best effort
    (a line left NULL reads as its confirmed quantity through qty_delivered_eff anyway)."""
    shop = _shop()
    if not shop.has_column("shop_order_lines", "qty_delivered"):
        return 0
    n = 0
    dropped: set = set()
    for ln in o.get("lines") or []:
        if ln.get("qty_delivered") is not None:
            continue
        try:
            _write_line(client, int(ln["id"]), {"qty_delivered": qty_confirmed_eff(ln)}, dropped)
            n += 1
        except Exception as e:  # noqa: BLE001 — the status move already happened
            log.warning("qty_delivered for line %s not written: %s", ln.get("id"), e)
            break
        if dropped:
            break
    return n


def deliver_with_changes(order_id: int, lines=None, added=None, shop_agreed=None, actor: str = "",
                         note: str | None = None, focus_invoice_no: str | None = None,
                         expected_updated_at=None) -> dict:
    """Confirmed → Delivered, recording what was really handed over. `lines` = [{line_id,
    qty_delivered, reason?, note?}] (a line not named is delivered as confirmed; any difference
    needs a reason); `added` = [{item_code, qty, reason?}] lines added at the shop (current book).
    The delivered value becomes the order's agreed total; a delivery that takes the order below the
    minimum needs "Shop agreed". Then Delivered through shop.set_status (compare-and-swap)."""
    shop = _shop()
    o = shop.get_order(order_id)
    if not o:
        raise shop.ShopError("Order not found.")
    if is_stale(o, expected_updated_at):          # the sheet was opened on an older read
        raise shop.ShopError(shop.CAS_CONFLICT_MSG)
    st = o["status"]
    if st == "new":
        raise shop.ShopError("Confirm the order first — then deliver it.")
    if st not in OPEN_CONFIRMED:
        raise shop.ShopError(f"This order is already {status_label(st).lower()}.")
    lines, added = list(lines or []), list(added or [])
    by_id = {int(ln["id"]): ln for ln in o.get("lines") or []}
    qd: dict[int, int] = {lid: qty_confirmed_eff(ln) for lid, ln in by_id.items()}
    reasons: dict[int, tuple[str, str | None]] = {}
    seen: set[int] = set()
    for x in lines:
        x = x or {}
        lid = shop._i(x.get("line_id"))
        ln = by_id.get(lid)
        if not ln:
            raise shop.ShopError("Unknown order line.")
        if lid in seen:
            raise shop.ShopError(f"{ln['item_code']} is listed twice — one entry per line.")
        seen.add(lid)
        raw = x.get("qty_delivered")
        q = qty_confirmed_eff(ln) if raw is None else shop._i(raw, -1)
        if q < 0 or q > shop.MAX_QTY:
            raise shop.ShopError(f"Delivered quantity for {ln['item_code']} must be between 0 and {shop.MAX_QTY}.")
        n = shop.clean(x.get("note"), 200) or None
        if q != qty_confirmed_eff(ln):
            reasons[lid] = (_reason(x.get("reason"), ln["item_code"], n), n)
        qd[lid] = q
    if not reasons and not added:        # nothing differs from the confirmation: the one tap
        agreed0 = clean_agreed(shop_agreed)
        return shop.set_status(order_id, "delivered", note, actor, expected_status=st,
                               focus_invoice_no=focus_invoice_no, pin_updated=True,
                               expected_updated_at=o.get("updated_at"),
                               detail_extra=({"shop_agreed": dict(agreed0, by=actor)} if agreed0 else None))
    if not lines_ready():
        raise shop.ShopError(MIGRATION_MSG.format(what="Delivering with changes"))
    ctx = shop.context()
    news: list[dict] = []
    for a in added:
        a = a or {}
        code = shop.clean(a.get("item_code"), 64)
        qty = shop._i(a.get("qty"))
        if not code:
            raise shop.ShopError("Pick the item to add.")
        if qty <= 0 or qty > shop.MAX_QTY:
            raise shop.ShopError(f"Quantity for {code} must be between 1 and {shop.MAX_QTY}.")
        n = shop.clean(a.get("note"), 200) or None
        reason = _reason(a.get("reason"), code, n, default="customer_changed")
        pl = price_new_line(o, code, qty, ctx)
        news.append(_new_row(o, pl, qty, stage="delivery", reason=reason, note=n, ctx=ctx, substitute_for=None,
                             delivered=True))
    all_lines = [{**ln, "qty_delivered": qd[lid]} for lid, ln in by_id.items()] + news
    if not any(shop._i(x.get("qty_delivered")) > 0 for x in all_lines):
        raise shop.ShopError("Nothing was handed over — cancel the order instead.")
    totals = compute_totals(o, all_lines, lambda x: shop._i(x.get("qty_delivered")))
    delivery = shop.dmoney(o.get("delivery_bhd"))
    before_total = shop.dmoney(o.get("total_confirmed_bhd") if o.get("total_confirmed_bhd") is not None
                               else o.get("total_bhd"))
    min_order = shop.dmoney((ctx.get("settings") or {}).get("shop_min_order_bhd"))
    adverse = []
    if min_order > 0 and (before_total - delivery) >= min_order > (totals["total_bhd"] - delivery):
        adverse.append({"kind": "below_minimum", "minimum_bhd": float(min_order),
                        "from": float(before_total), "to": float(totals["total_bhd"])})
    agreed = clean_agreed(shop_agreed)
    if adverse and not agreed:
        raise shop.ShopError(ADVERSE_MSG)
    line_updates: dict[int, dict] = {}
    event_lines: list[dict] = []
    for lid, ln in by_id.items():
        upd: dict = {}
        if ln.get("qty_delivered") is None or shop._i(ln.get("qty_delivered")) != qd[lid]:
            upd["qty_delivered"] = qd[lid]
        if lid in reasons:
            upd["change_reason"] = reasons[lid][0]
            if reasons[lid][1] is not None:
                upd["note"] = reasons[lid][1]
            event_lines.append({"line_id": lid, "item_code": ln["item_code"], "qty_confirmed": qty_confirmed_eff(ln),
                                "qty_delivered": qd[lid], "reason": reasons[lid][0], "note": reasons[lid][1]})
        if upd:
            line_updates[lid] = upd
    now_total = totals["total_bhd"]
    extra_upd: dict = {}
    if o.get("total_confirmed_bhd") is None or shop.dmoney(o.get("total_confirmed_bhd")) != now_total:
        extra_upd = {"total_confirmed_bhd": float(now_total), "subtotal_confirmed_bhd": float(totals["subtotal_bhd"])}
    agreed_ev = dict(agreed, by=actor, **({"for": [a["kind"] for a in adverse]} if adverse else {})) if agreed else None
    detail = {"delivered": event_lines,
              "added": [{"item_code": r["item_code"], "qty": r["qty"], "unit_price_bhd": r["unit_price_bhd"],
                         "line_total_bhd": r["line_total_bhd"], "reason": r["change_reason"]} for r in news],
              "adverse": [a["kind"] for a in adverse], "shop_agreed": agreed_ev,
              "total_before": float(before_total), "total_after": float(now_total), "with_changes": True}
    before_lines = dict(by_id)

    def after_swap(client):
        undo, _got = write_lines(client, before_lines, line_updates, news)
        return undo
    out = shop.set_status(order_id, "delivered", note, actor, expected_status=st, focus_invoice_no=focus_invoice_no,
                          pin_updated=True, expected_updated_at=o.get("updated_at"), detail_extra=detail,
                          extra_upd=extra_upd, after_swap=after_swap)
    out["totals"] = _totals_out(totals)
    out["adverse"], out["shop_agreed"] = adverse, agreed_ev
    return out


# ── reopen (admin, within 7 days, reason required) ────────────────────────────

def reopen_order(order_id: int, reason: str | None, actor: str, now: datetime | None = None) -> dict:
    """Undo a wrong Delivered (→ Confirmed) or Cancelled (→ Received) within REOPEN_DAYS, with a
    reason. Compare-and-swap on status + updated_at; a 'reopened' event (internal) and a
    shop_admin_audit row carry who, when, why and what the order looked like before."""
    shop = _shop()
    from app import shop_audit
    why = shop.clean(reason, 300)
    if len(why) < REOPEN_REASON_MIN:
        raise shop.ShopError(REOPEN_REASON_MSG)
    o = shop.get_order(order_id)
    if not o:
        raise shop.ShopError("Order not found.")
    st = o["status"]
    if st not in REOPEN_TO:
        raise shop.ShopError(REOPEN_STATUS_MSG)
    until = reopen_deadline(o)
    t = now or _now()
    if not until or t > until:
        raise shop.ShopError(REOPEN_WINDOW_MSG)
    to = REOPEN_TO[st]
    iso = t.isoformat()
    upd: dict = {"status": to, "updated_at": iso}
    keep_keys = ["status", "updated_at"]
    if st == "delivered":
        upd["delivered_at"] = None
        keep_keys.append("delivered_at")
    else:
        upd.update(cancelled_at=None, cancelled_by=None, cancel_reason=None)
        keep_keys += ["cancelled_at", "cancelled_by", "cancel_reason"]
        if shop.has_column("shop_orders", "cancel_reason_code"):
            upd["cancel_reason_code"] = None
            keep_keys.append("cancel_reason_code")
    if orders_ready():
        upd.update(reopened_at=iso, reopened_by=actor, reopen_reason=why)
    # a delivery undone: the lines go back to "not delivered yet", the money to the confirmed state.
    # A line added AT the door was never confirmed: it goes out (confirmed 0, 'unavailable', reason
    # 'other' with the reopen reason) — re-add it with Amend if the shop still wants it.
    line_updates: dict[int, dict] = {}
    before_lines = {int(ln["id"]): ln for ln in o.get("lines") or []}
    if st == "delivered" and lines_ready():
        has_priced = shop.has_column("shop_order_lines", LINE_PRICED_COLS[1])
        for lid, ln in before_lines.items():
            lu: dict = {}
            if ln.get("qty_delivered") is not None:
                lu["qty_delivered"] = None
            if ln.get("added_at_stage") == "delivery" and (
                    qty_confirmed_eff(ln) > 0 or (ln.get("line_status") or "ok") != "unavailable"):
                lu.update(qty_confirmed=0, line_status="unavailable", change_reason="other",
                          note=f"Reopened: {why}"[:200])
                if has_priced:
                    lu["line_total_confirmed"] = 0.0
            if lu:
                line_updates[lid] = lu
        after_lines = [{**ln, **line_updates.get(lid, {})} for lid, ln in before_lines.items()]
        try:
            t2 = compute_totals(o, after_lines, qty_confirmed_eff)
            if o.get("total_confirmed_bhd") is not None and shop.dmoney(o["total_confirmed_bhd"]) != t2["total_bhd"]:
                upd.update(total_confirmed_bhd=float(t2["total_bhd"]), subtotal_confirmed_bhd=float(t2["subtotal_bhd"]))
        except shop.ShopError as e:          # a line without any price: keep the total as it stands
            log.warning("reopen of %s keeps its total: %s", o.get("order_no"), e)
    before = {k: o.get(k) for k in keep_keys + [k for k in ("total_confirmed_bhd", "subtotal_confirmed_bhd",
                                                            "focus_invoice_no", "delivered_at", "cancel_reason_code")
                                                if k not in keep_keys]}
    client = shop.get_client()
    swapped = shop._update_optional(
        "shop_orders", upd, ORDER_OPTIONAL,
        lambda q: pin_updated(q.eq("id", order_id).eq("status", st), o.get("updated_at")).execute().data or [])
    if not swapped:
        raise shop.ShopError(shop.CAS_CONFLICT_MSG)
    undo = None
    try:
        undo, _ = write_lines(client, before_lines, line_updates, [])
        client.table("shop_order_events").insert({
            "order_id": order_id, "actor": actor, "event": "reopened",
            "detail": {"from": st, "to": to, "reason": why, "before": before,
                       "lines": [{"line_id": lid, "item_code": before_lines[lid].get("item_code"),
                                  **{k: before_lines[lid].get(k) for k in line_updates[lid]}}
                                 for lid in line_updates]}}).execute()
    except Exception as e:  # noqa: BLE001
        if undo:
            undo()
        _revert_header(order_id, {k: o.get(k) for k in upd}, status=to, updated_at=iso,
                       order_no=o.get("order_no"), err=e)
        raise
    after = {k: upd.get(k, o.get(k)) for k in before}
    shop_audit.record(actor, "order", order_id, "reopen", before, {**after, "reason": why})
    return shop.get_order(order_id) or {}


# ── "Tell the shop" (the rep's WhatsApp tap, logged) ──────────────────────────

def record_customer_notified(order_id: int, channel: str | None, actor: str) -> dict:
    """Log that the rep told the shop (his tap on the prefilled WhatsApp — or a call / visit): a
    'customer_notified' event, and a shop_notifications row once that table exists."""
    shop = _shop()
    ch = str(channel or "whatsapp").strip().lower()
    if ch not in NOTIFY_CHANNELS:
        raise shop.ShopError("Channel must be whatsapp, phone, visit or email.")
    o = shop.get_order(order_id)
    if not o:
        raise shop.ShopError("Order not found.")
    now = shop._iso()
    detail = {"channel": ch, "status": o.get("status"), "status_label": status_label(o.get("status")),
              "total_bhd": order_total(o)}
    shop.get_client().table("shop_order_events").insert({
        "order_id": order_id, "actor": actor, "event": "customer_notified", "detail": detail}).execute()
    logged = False
    try:
        from app import shop_notify
        # kind 'status_update' (the live check has no 'customer_notified'); provider says it was
        # the rep's own tap / call / visit, not a provider send
        row = shop_notify.notification_row(
            "status_update", ch, "merchant", {"sent": True}, order_id=order_id, to=None,
            detail={"via": "rep_tap", "status": o.get("status"), "by": actor}, at=now)
        row["provider"] = "rep_tap"
        logged = shop_notify.log_notifications([row])
    except Exception as e:  # noqa: BLE001 — the event is the record; the log row is a convenience
        log.debug("customer_notified log row not written: %s", e)
    return {"ok": True, "order_id": order_id, "channel": ch, "notified_at": now, "logged": bool(logged)}


def _born_event(e: dict) -> bool:
    d = (e or {}).get("detail") if isinstance((e or {}).get("detail"), dict) else {}
    return str((e or {}).get("event") or "") == "status:confirmed" and bool(d.get("born_confirmed"))


def born_confirmed(o: dict | None, events=None) -> bool:
    """An order the rep placed IN the shop (source 'salesman' — born Confirmed since R7c, its
    'status:confirmed' event says born_confirmed): the shop was there when it was placed."""
    if (o or {}).get("source") == "salesman":
        return True
    evs = events if events is not None else (o or {}).get("events")
    return any(_born_event(e) for e in evs or [])


def notify_state(events, order: dict | None = None) -> dict:
    """{customer_notified_at, shop_told}: was the shop told AFTER the latest step (a status move or
    an amendment)? Drives the drawer's "Shop not told yet" chip. A born-confirmed staff order
    (born_confirmed) starts as told: its 'created' and born 'status:confirmed' happened in front of
    the shop, so only a later step (an amendment, Delivered, a cancel) asks for a tap."""
    born = born_confirmed(order, events)
    last_step = last_told = None
    for e in events or []:
        ev = str((e or {}).get("event") or "")
        ts = (e or {}).get("ts")
        if ev == "customer_notified":
            last_told = ts or last_told
        elif born and (ev == "created" or _born_event(e)):
            continue
        elif ev.startswith("status:") or ev in ("amended", "created", "reopened"):
            last_step = ts or last_step
    shop = _shop()
    if born and not last_step:
        return {"customer_notified_at": last_told, "shop_told": True}
    told = bool(last_told) and (not last_step or (shop._parse_ts(last_told) or datetime.min.replace(tzinfo=timezone.utc))
                                >= (shop._parse_ts(last_step) or datetime.min.replace(tzinfo=timezone.utc)))
    return {"customer_notified_at": last_told, "shop_told": told}


# ── payloads ──────────────────────────────────────────────────────────────────

def _event_reason(events, line_id) -> str | None:
    """The latest reason recorded for a line on the events (before the change_reason column)."""
    got = None
    for e in events or []:
        d = (e or {}).get("detail") if isinstance((e or {}).get("detail"), dict) else {}
        for x in (d.get("lines") or []) + (d.get("delivered") or []):
            if isinstance(x, dict) and x.get("line_id") == line_id and x.get("reason"):
                got = x["reason"]
    return got


def disposition(ln: dict) -> str:
    """as_ordered · reduced · increased · unavailable · substituted · added · backorder"""
    st = ln.get("line_status") or "ok"
    if st == "substituted" or (st in LINE_OUT and ln.get("substitute_item_code")):
        return "substituted"
    if is_added(ln):
        # an added line (or a substitute) taken out again — its line restored, re-substituted, or a
        # delivery-added line after a reopen — is gone, not "added"
        return "unavailable" if st in LINE_OUT or qty_confirmed_eff(ln) <= 0 else "added"
    qc = qty_confirmed_eff(ln)
    if qc <= 0:
        return "unavailable"
    q = _shop()._i(ln.get("qty"))
    if qc < q:
        return "reduced"
    if qc > q:
        return "increased"
    return "backorder" if st == "backorder" else "as_ordered"


def line_view(ln: dict, o: dict, *, public: bool = False) -> dict:
    """One line with its three numbers, confirmed and delivered money, disposition and the PUBLIC
    reason label. `public` = the shop's status page: no ids, no cost, no internal note."""
    shop = _shop()
    status = o.get("status")
    # confirmed figures exist once the order was confirmed (a Received order — or one cancelled
    # while still Received — has none)
    started = status in OPEN_CONFIRMED or status == "delivered" or bool(o.get("confirmed_at"))
    qc = qty_confirmed_eff(ln) if (started or ln.get("qty_confirmed") is not None) else None
    qd = qty_delivered_eff(ln, status)
    try:
        unit = lock_unit(ln)
    except shop.ShopError:
        unit = None
    upc = ln.get("unit_price_confirmed")
    ltc = ln.get("line_total_confirmed")
    if qc is not None and unit is not None:
        upc = shop.money(upc) if upc is not None else float(unit)
        ltc = shop.money(ltc) if ltc is not None else float(shop.dmoney(unit * qc))
    code = ln.get("change_reason") or (_event_reason(o.get("events"), ln.get("id")) if ln.get("id") else None)
    by_id = {x.get("id"): x for x in o.get("lines") or []}
    sub_for = ln.get("substitute_for_line")
    out = {"item_code": ln.get("item_code"), "display_name": ln.get("display_name"),
           "qty": shop._i(ln.get("qty")), "qty_confirmed": qc, "qty_delivered": qd,
           "qty_unavailable": (max(0, shop._i(ln.get("qty")) - qc) if qc is not None and not is_added(ln) else 0),
           "unit_price_bhd": shop.money(ln.get("unit_price_bhd")), "line_total_bhd": shop.money(ln.get("line_total_bhd")),
           "unit_price_confirmed": upc, "line_total_confirmed": ltc,
           "line_total_delivered": (float(shop.dmoney(unit * qd)) if qd is not None and unit is not None else None),
           "line_status": ln.get("line_status") or "ok", "disposition": disposition(ln),
           "reason_label": public_reason(code), "substitute_item_code": ln.get("substitute_item_code"),
           "substitute_for": (by_id.get(sub_for) or {}).get("item_code") if sub_for else None,
           "added_at_stage": ln.get("added_at_stage"),
           "stock_status": ln.get("stock_status"), "backorder": bool(ln.get("backorder")),
           "image_url": ln.get("image_url")}
    if not public:
        out.update(id=ln.get("id"), change_reason=code, substitute_for_line=sub_for, note=ln.get("note"))
    return out


def order_money(o: dict) -> dict:
    """The order's totals for every payload: as ordered, confirmed, and the effective one."""
    shop = _shop()
    tc = o.get("total_confirmed_bhd")
    return {"total_bhd": shop.money(o.get("total_bhd")),
            "total_confirmed_bhd": shop.money(tc) if tc is not None else None,
            "total_effective_bhd": order_total(o)}
