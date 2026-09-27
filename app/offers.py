"""The offer ledger (release R7d, 27-Sep-2026; plan §16 prerequisites, §24 r7_offer_ledger;
audit OFF-2, OFF-3, OFF-4, OFF-6, OFF-9). Offers become MEASURABLE before any offer runs:
discount_rules and shop_campaigns both had 0 rows on 27-Sep-2026 (read-only), so nothing here
changes a live price today.

What it adds (scripts/r7d_offer_ledger_migration.sql creates the storage; every function below
works before it and degrades to the old behaviour):

  * shop_order_discounts — ONE row per (order line, rule) and per (order, cart-level rule), written
    by app.shop.create_order in one insert right after the lines: the rule as it stood when the
    shop ordered (rule_snapshot), line or cart level, the amount placed, the amount after the rep's
    confirmation (amount_confirmed_bhd, refreshed by confirm / amend / deliver-with-changes), and
    whether the margin floor cut it (clamped). price_cart splits a line's discount over the rules
    that made it (a stackable rule on top of a tier) in proportion to what each took off, rounded
    to the fils with the remainder on the last — so the rows of a line add up to its discount
    exactly and no rule is credited with another's money.
  * soft delete — archived_at on discount_rules and shop_campaigns. An archived row is switched off
    and filtered out of every read (pricing, the catalog, the admin lists unless asked). A rule that
    any order used can never be hard-deleted (delete_refusal); a campaign is archived, never deleted,
    once the column exists.
  * the coupon counter — shop_coupon_reserve(rule) is ONE conditional UPDATE (uses < max_uses, live
    window, active, not archived) taken BEFORE the order is written; no row back = the code has just
    run out and the order is refused with a plain message. A cancelled order gives its use back
    (shop_coupon_release); an admin reopen takes it again (forced — the shop already had it).
    Until the RPC exists the old read-then-write counter runs, after the order, as before.
  * hold-outs — a rule's scope may carry bucket {mod, hold}: sha1 of the merchant (customer id, else
    the device) mod `mod`; buckets in `hold` never get the offer. The quote and the order resolve
    the merchant the same way (the device's customer, else the device), so a shop never sees a price
    it is not charged. Automatic offers only: a coupon is typed by the shop, so it never has one.
    The arm travels as exp/var: `experiments` on the quote, meta.exp/meta.var on the 'order' event
    and the arm inside every ledger row's snapshot.
  * shop_badge_log — the badges merchants saw, one row per item per Bahrain day (a shop_jobs job),
    so a clearance or badge effect can be measured later against unbadged aging lines (OFF-6).
  * v_offer_performance — per rule: orders, units, merchants, reps, list value, discount cost, GM
    after discount, clamps (yq_readonly only). With about 200 merchants only large effects are
    detectable; the page says so.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

log = logging.getLogger(__name__)

LINE_KINDS = ("qty_tier", "bundle_price", "salesman_offer")
CART_KINDS = ("cart_value", "coupon")
AUTO_KINDS = ("qty_tier", "bundle_price", "salesman_offer", "cart_value")   # may carry a hold-out bucket
BUCKET_MOD_MAX = 100
ARM_OFFER, ARM_HOLD, ARM_NONE = "offer", "hold", "none"

COUPON_GONE_MSG = "This code is no longer available — it has run out or ended. Remove it to place your order."
RULE_REFERENCED_MSG = "Orders used this offer — archive it instead, so its history stays."
ARCHIVE_NEEDS_MIGRATION_MSG = ("Archiving needs the offer-ledger update (scripts/r7d_offer_ledger_migration.sql). "
                               "Switch the offer off for now.")

# the rule fields a snapshot keeps (what the shop was offered, exactly as it stood)
SNAPSHOT_KEYS = ("id", "name", "kind", "scope", "pct_off", "amount_off_bhd", "fixed_price_bhd", "min_qty",
                 "min_value_bhd", "coupon_code", "stackable", "priority", "starts_at", "ends_at", "max_uses")
_BAHRAIN = timezone(timedelta(hours=3))
_rpc_missing: dict[str, float] = {}
_RPC_RETRY_S = 300.0


def _shop():
    from app import shop
    return shop


def bahrain_day(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(_BAHRAIN).date().isoformat()


# ── probes ────────────────────────────────────────────────────────────────────

def table_ready(table: str, column: str = "id") -> bool:
    """Does `table` exist (with `column`)? Cached like shop.has_column: a hit for 10 minutes, a
    miss for one, so the ledger starts writing within a minute of the migration, no restart."""
    return _shop().has_column(table, column)


def forget_probes() -> None:
    """Tests and a reverse: look again on the next call."""
    _rpc_missing.clear()
    shop = _shop()
    for t, c in (("shop_order_discounts", "id"), ("shop_badge_log", "as_of"), ("discount_rules", "archived_at"),
                 ("shop_campaigns", "archived_at")):
        shop._forget_column(t, c)


def _missing_relation(e: Exception) -> bool:
    msg = str(e)
    return "42P01" in msg or "does not exist" in msg or "PGRST205" in msg or "Could not find the table" in msg


def _missing_function(e: Exception) -> bool:
    msg = str(e)
    return ("PGRST202" in msg or "42883" in msg or "Could not find the function" in msg
            or ("function" in msg and "does not exist" in msg))


# ── hold-out buckets (OFF-9) ──────────────────────────────────────────────────

def norm_bucket(raw) -> dict | None:
    """{mod, hold} or None. mod 2..100; hold = distinct buckets in [0, mod), at least one and never
    all of them (an offer nobody can get is not an experiment). Raises ValueError with a sentence."""
    if raw in (None, "", {}, []):
        return None
    if not isinstance(raw, dict):
        raise ValueError("Hold-out: give mod and hold, e.g. {mod: 10, hold: [0]}.")
    try:
        mod = int(raw.get("mod"))
    except (TypeError, ValueError):
        raise ValueError("Hold-out: mod must be a whole number from 2 to 100.") from None
    if not 2 <= mod <= BUCKET_MOD_MAX:
        raise ValueError("Hold-out: mod must be a whole number from 2 to 100.")
    hold_raw = raw.get("hold")
    if not isinstance(hold_raw, (list, tuple)) or not hold_raw:
        raise ValueError("Hold-out: list at least one held-back bucket.")
    try:
        hold = sorted({int(h) for h in hold_raw})
    except (TypeError, ValueError):
        raise ValueError("Hold-out: buckets are whole numbers.") from None
    if any(h < 0 or h >= mod for h in hold):
        raise ValueError(f"Hold-out: buckets run from 0 to {mod - 1}.")
    if len(hold) >= mod:
        raise ValueError("Hold-out: at least one bucket must get the offer.")
    return {"mod": mod, "hold": hold}


def bucket_of(key: str, mod: int) -> int:
    """sha1 of the merchant key mod `mod` — the same approach as the follow-up hold-out: stable,
    uniform whatever the id looks like, nothing stored."""
    return int(hashlib.sha1(key.encode("utf-8")).hexdigest()[:8], 16) % mod


def bucket_key(customer_id=None, device_id: str | None = None) -> str | None:
    """'c:<customer id>' when the merchant is known, else 'd:<device id>', else None."""
    try:
        cid = int(customer_id) if customer_id not in (None, "") else None
    except (TypeError, ValueError):
        cid = None
    if cid:
        return f"c:{cid}"
    dev = str(device_id or "").strip()[:64]
    return f"d:{dev}" if dev else None


def rule_bucket(rule: dict) -> dict | None:
    scope = rule.get("scope") or {}
    b = scope.get("bucket") if isinstance(scope, dict) else None
    try:
        return norm_bucket(b) if b else None
    except ValueError:
        return None


def rule_arm(rule: dict, key: str | None) -> str | None:
    """None = not an experiment (the rule applies as usual); 'offer' / 'hold' for a bucketed rule;
    a bucketed rule with no merchant key is held back ('none' is what gets logged)."""
    b = rule_bucket(rule)
    if b is None:
        return None
    if not key:
        return ARM_NONE
    return ARM_HOLD if bucket_of(key, b["mod"]) in b["hold"] else ARM_OFFER


def any_bucketed(ctx: dict) -> bool:
    return any(rule_bucket(r) for r in (ctx.get("rules") or []))


def customer_for_device(device_id: str | None) -> int | None:
    """The merchant whose device list holds this device (earned by an order), else None."""
    dev = str(device_id or "").strip()
    if not dev:
        return None
    try:
        rows = (_shop().get_client().table("shop_customers").select("id")
                .contains("device_ids", json.dumps([dev])).limit(1).execute().data or [])   # jsonb: @> '["dev"]'
        return int(rows[0]["id"]) if rows else None
    except Exception as e:  # noqa: BLE001 — no customer → the device itself is the key
        log.debug("customer-by-device lookup failed: %s", e)
        return None


def key_for_device(ctx: dict, device_id: str | None) -> str | None:
    """The hold-out key of a merchant request: the device's customer, else the device. The lookup
    only runs while some live rule has a bucket (none today), so a normal quote costs nothing."""
    if not device_id or not any_bucketed(ctx):
        return None
    return bucket_key(customer_for_device(device_id), device_id)


def experiment_meta(experiments: list[dict] | None) -> dict:
    """meta.exp / meta.var for a shop_events row: 'r12,r15' / 'offer,hold' (short strings only)."""
    ex = [e for e in (experiments or []) if e.get("rule_id") is not None][:6]
    if not ex:
        return {}
    return {"exp": ",".join(f"r{e['rule_id']}" for e in ex)[:120],
            "var": ",".join(str(e.get("var") or ARM_NONE) for e in ex)[:120]}


# ── the ledger rows (pure) ────────────────────────────────────────────────────

def allocate(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """Split `total` (fils) over `weights` in proportion, each share rounded to the fils, the last
    one taking the remainder — so the shares always add up to `total` exactly."""
    shop = _shop()
    n = len(weights)
    if n == 0:
        return []
    total = shop.dmoney(total)
    if n == 1:
        return [total]
    wsum = sum((w for w in weights if w > 0), shop.D0)
    out: list[Decimal] = []
    running = shop.D0
    for i, w in enumerate(weights):
        if i == n - 1:
            out.append(shop.dmoney(total - running))
            break
        share = shop.dmoney(total * (w if w > 0 else shop.D0) / wsum) if wsum > 0 else shop.dmoney(total / n)
        share = min(share, total - running)
        out.append(share)
        running += share
    return out


def snapshot(rule: dict | None, arm: str | None = None) -> dict:
    """The rule as the shop was offered it, JSON-safe; the hold-out arm when it is an experiment."""
    rule = rule or {}
    snap = {}
    for k in SNAPSHOT_KEYS:
        v = rule.get(k)
        if isinstance(v, Decimal):
            v = float(v)
        snap[k] = v
    if arm:
        snap["arm"] = arm
    return json.loads(json.dumps(snap, default=str))


def ledger_rows(order_id: int, quote: dict, line_rows: list[dict], rules: dict, *,
                confirmed: bool = False) -> list[dict]:
    """The shop_order_discounts rows for one order. `quote` = price_cart's answer (its `_ledger`),
    `line_rows` = the inserted shop_order_lines rows in quote order, `rules` = rule id → rule as
    priced. A born-confirmed (rep-placed) order is confirmed at its placed amounts."""
    arms = {e.get("rule_id"): e.get("var") for e in (quote.get("experiments") or [])}
    out = []
    for e in quote.get("_ledger") or []:
        rid = e.get("rule_id")
        if rid is None:
            continue
        line_id = None
        if e.get("level") == "line":
            idx = e.get("line_index")
            row = line_rows[idx] if isinstance(idx, int) and 0 <= idx < len(line_rows) else None
            if row is not None and str(row.get("item_code") or "").upper() == str(e.get("item_code") or "").upper():
                line_id = row.get("id")
        amt = float(_shop().dmoney(e.get("amount_bhd")))
        out.append({
            "order_id": order_id, "line_id": line_id, "item_code": e.get("item_code"),
            "rule_id": rid, "rule_snapshot": snapshot(rules.get(rid) or {"id": rid, "kind": e.get("kind"),
                                                                           "name": e.get("name")}, arms.get(rid)),
            "kind": e.get("kind"), "level": e.get("level"), "amount_bhd": amt,
            "amount_confirmed_bhd": amt if confirmed else None, "clamped": bool(e.get("clamped")),
            "stage": "confirmed" if confirmed else "placed",
        })
    return out


def write_ledger(client, order: dict, quote: dict, line_rows: list[dict], ctx: dict, *, confirmed: bool) -> int:
    """One insert with every ledger row of a new order. Best effort: the order is the real thing
    and is never failed or rolled back for its measurement row; a failure is logged loudly."""
    if not quote.get("_ledger"):
        return 0
    if not table_ready("shop_order_discounts"):
        return 0
    rules = {r.get("id"): r for r in (ctx.get("rules") or [])}
    rows = ledger_rows(int(order["id"]), quote, line_rows, rules, confirmed=confirmed)
    if not rows:
        return 0
    try:
        client.table("shop_order_discounts").insert(rows).execute()
        return len(rows)
    except Exception as e:  # noqa: BLE001
        if _missing_relation(e):
            _shop()._forget_column("shop_order_discounts", "id")
        log.warning("offer ledger: order %s placed but its %d discount row(s) were NOT recorded: %s",
                    order.get("order_no") or order.get("id"), len(rows), e)
        return 0


# ── confirmed amounts (after the rep's confirm / amend / delivery with changes) ──

def confirmed_amounts(order: dict, lines: list[dict], ledger: list[dict]) -> dict[int, float]:
    """Pure: ledger row id → the discount at the order's agreed quantities. Line rows: the line's
    locked unit discount × its confirmed (or, once delivered, delivered) quantity, shared over the
    line's rules in the placed proportions. Cart rows: the order's own cart discount as the order
    heart re-shares it (shop_heart.compute_totals), in the placed proportions."""
    from app import shop_heart
    shop = _shop()
    status = order.get("status")

    def qty_of(ln: dict) -> int:
        if status == "delivered":
            q = shop_heart.qty_delivered_eff(ln, status)
            return q if q is not None else shop_heart.qty_confirmed_eff(ln)
        if status == "new":
            return shop._i(ln.get("qty"))
        return shop_heart.qty_confirmed_eff(ln)

    by_line = {int(ln["id"]): ln for ln in lines if ln.get("id") is not None}
    out: dict[int, float] = {}
    line_groups: dict[int, list[dict]] = {}
    cart_rows: list[dict] = []
    for r in ledger:
        if r.get("id") is None:
            continue
        if r.get("level") == "cart":
            cart_rows.append(r)
        elif r.get("line_id") is not None:
            line_groups.setdefault(int(r["line_id"]), []).append(r)
    for lid, rows in line_groups.items():
        ln = by_line.get(lid)
        if ln is None:
            continue
        try:
            unit = shop_heart.lock_unit(ln)
        except shop.ShopError:
            continue
        lp = shop.dmoney(ln.get("list_price_bhd")) if ln.get("list_price_bhd") is not None else unit
        total = shop.dmoney(max(shop.D0, lp - unit) * qty_of(ln))
        rows = sorted(rows, key=lambda r: int(r["id"]))
        shares = allocate(total, [shop.dmoney(r.get("amount_bhd")) for r in rows])
        for r, s in zip(rows, shares):
            out[int(r["id"])] = float(s)
    if cart_rows:
        t = shop_heart.compute_totals(order, lines, qty_of)
        rows = sorted(cart_rows, key=lambda r: int(r["id"]))
        shares = allocate(t["cart_discount_bhd"], [shop.dmoney(r.get("amount_bhd")) for r in rows])
        for r, s in zip(rows, shares):
            out[int(r["id"])] = float(s)
    return out


def refresh_confirmed(order_id: int) -> int:
    """Rewrite amount_confirmed_bhd / stage on an order's ledger rows. Best effort, never raises:
    called after the order heart has already committed the rep's change."""
    shop = _shop()
    if not table_ready("shop_order_discounts"):
        return 0
    try:
        client = shop.get_client()
        ledger = (client.table("shop_order_discounts").select("id,line_id,level,amount_bhd")
                  .eq("order_id", order_id).execute().data or [])
        if not ledger:
            return 0
        o = shop.get_order(order_id)
        if not o or o.get("status") in ("new", "cancelled"):
            return 0
        amounts = confirmed_amounts(o, o.get("lines") or [], ledger)
        now = shop._iso()
        n = 0
        for rid, amt in amounts.items():
            client.table("shop_order_discounts").update(
                {"amount_confirmed_bhd": amt, "stage": "confirmed", "updated_at": now}).eq("id", rid).execute()
            n += 1
        return n
    except Exception as e:  # noqa: BLE001
        log.warning("offer ledger: confirmed amounts of order %s not refreshed: %s", order_id, e)
        return 0


# ── the coupon counter (OFF-3) ────────────────────────────────────────────────

def _rpc_known_missing(name: str) -> bool:
    at = _rpc_missing.get(name)
    return bool(at and time.monotonic() - at < _RPC_RETRY_S)


def reserve_coupon(client, rule_id, *, force: bool = False) -> bool | None:
    """Take one use of a coupon atomically. True = taken; False = none left (or the code ended,
    was switched off or archived meanwhile); None = the RPC is not installed yet (the caller keeps
    the old counter). `force` = an admin reopen: the shop already had the code, take it regardless."""
    if rule_id is None:
        return None
    if _rpc_known_missing("shop_coupon_reserve"):
        return None
    try:
        data = client.rpc("shop_coupon_reserve", {"p_rule_id": int(rule_id), "p_force": bool(force)}).execute().data
    except Exception as e:  # noqa: BLE001
        if _missing_function(e):
            _rpc_missing["shop_coupon_reserve"] = time.monotonic()
            return None
        raise
    if isinstance(data, list):
        data = data[0] if data else None
    if isinstance(data, dict):
        data = next(iter(data.values()), None)
    return data is not None


def release_coupon(client, rule_id) -> bool:
    """Give one use back (a cancelled order, or an order that failed after its reservation)."""
    if rule_id is None or _rpc_known_missing("shop_coupon_release"):
        return False
    try:
        client.rpc("shop_coupon_release", {"p_rule_id": int(rule_id)}).execute()
        return True
    except Exception as e:  # noqa: BLE001
        if _missing_function(e):
            _rpc_missing["shop_coupon_release"] = time.monotonic()
        else:
            log.warning("coupon use of rule %s not released: %s", rule_id, e)
        return False


def coupon_rule_for_order(client, order: dict) -> int | None:
    """The coupon rule an order used: its ledger row, else the rule holding its code."""
    if not order.get("coupon_code"):
        return None
    if table_ready("shop_order_discounts"):
        try:
            rows = (client.table("shop_order_discounts").select("rule_id").eq("order_id", order["id"])
                    .eq("kind", "coupon").limit(1).execute().data or [])
            if rows and rows[0].get("rule_id") is not None:
                return int(rows[0]["rule_id"])
        except Exception as e:  # noqa: BLE001
            log.debug("coupon ledger lookup failed: %s", e)
    try:
        rows = (client.table("discount_rules").select("id,kind").eq("coupon_code", str(order["coupon_code"]).upper())
                .limit(1).execute().data or [])
        return int(rows[0]["id"]) if rows else None
    except Exception as e:  # noqa: BLE001
        log.debug("coupon rule lookup failed: %s", e)
        return None


def release_for_order(client, order: dict) -> bool:
    """A cancelled order gives its coupon use back. Only through the RPC: before it exists the old
    counter never gave uses back either, so nothing changes until the migration."""
    rid = coupon_rule_for_order(client, order)
    if rid is None:
        return False
    ok = release_coupon(client, rid)
    if ok:
        _shop().invalidate()
    return ok


def retake_for_order(client, order: dict) -> bool:
    """An admin reopened a cancelled order that used a coupon: the use is taken again (forced)."""
    rid = coupon_rule_for_order(client, order)
    if rid is None:
        return False
    try:
        ok = bool(reserve_coupon(client, rid, force=True))
    except Exception as e:  # noqa: BLE001
        log.warning("coupon use of rule %s not re-taken on reopen: %s", rid, e)
        return False
    if ok:
        _shop().invalidate()
    return ok


# ── references + soft delete (OFF-4) ──────────────────────────────────────────

def rule_references(rule_ids: list[int] | None = None) -> dict[int, int]:
    """rule id → how many orders carry it (the ledger, else the lines' rule_ids). A rule not in
    the answer has none. Reads only; an unreadable source counts as unknown (see referenced())."""
    shop = _shop()
    client = shop.get_client()
    out: dict[int, set] = {}
    if table_ready("shop_order_discounts"):
        q = client.table("shop_order_discounts").select("rule_id,order_id")
        if rule_ids:
            q = q.in_("rule_id", list(rule_ids))
        for r in q.limit(100000).execute().data or []:
            if r.get("rule_id") is not None:
                out.setdefault(int(r["rule_id"]), set()).add(r.get("order_id"))
    q = client.table("shop_order_lines").select("order_id,rule_ids").not_.is_("rule_ids", "null")
    for r in q.limit(100000).execute().data or []:
        ids = r.get("rule_ids")
        if isinstance(ids, str):
            try:
                ids = json.loads(ids)
            except ValueError:
                ids = []
        for rid in ids or []:
            try:
                rid = int(rid)
            except (TypeError, ValueError):
                continue
            if rule_ids is None or rid in rule_ids:
                out.setdefault(rid, set()).add(r.get("order_id"))
    return {k: len(v) for k, v in out.items()}


def referenced(rule: dict, refs: dict[int, int] | None = None) -> bool:
    """A rule some order used: a coupon use counted, a ledger row or a line naming it. When the
    references cannot be read, it counts as referenced — a wrong Delete is worse than a missing one."""
    if int(rule.get("uses") or 0) > 0:
        return True
    try:
        refs = refs if refs is not None else rule_references([int(rule["id"])])
    except Exception as e:  # noqa: BLE001
        log.warning("rule %s references unreadable, treated as used: %s", rule.get("id"), e)
        return True
    return int(refs.get(int(rule["id"]), 0)) > 0


def archive(table: str, row_id: int, by: str, *, restore: bool = False) -> dict:
    """Archive (switched off + archived_at) or restore (archived_at cleared, still off) a rule or a
    campaign. Raises ShopError before the migration or for an unknown row."""
    shop = _shop()
    if table not in ("discount_rules", "shop_campaigns"):
        raise ValueError(table)
    if not table_ready(table, "archived_at"):
        raise shop.ShopError(ARCHIVE_NEEDS_MIGRATION_MSG)
    client = shop.get_client()
    got = client.table(table).select("*").eq("id", row_id).limit(1).execute().data
    if not got:
        raise shop.ShopError("Offer not found." if table == "discount_rules" else "Campaign not found.")
    upd = ({"archived_at": None, "archived_by": None} if restore
           else {"archived_at": shop._iso(), "archived_by": (by or "")[:160] or None, "is_active": False})
    upd["updated_at"] = shop._iso()
    out = client.table(table).update(upd).eq("id", row_id).execute().data
    shop.invalidate()
    return (out or [dict(got[0], **upd)])[0]


def live_rows(rows: list[dict]) -> list[dict]:
    """The rows nobody archived (works before the column exists: nothing is archived then)."""
    return [r for r in rows or [] if not r.get("archived_at")]


# ── the per-rule readout (v_offer_performance) ────────────────────────────────

PERF_SQL = ("SELECT rule_id, name, kind, level, is_active, archived_at::text AS archived_at, starts_at::text AS starts_at, "
            "ends_at::text AS ends_at, max_uses, uses, orders, units, merchants, reps, list_value_bhd, discount_bhd, "
            "discount_placed_bhd, revenue_ex_vat_bhd, cost_bhd, gm_bhd, gm_pct, uncosted_lines, clamp_count, "
            "first_order_at::text AS first_order_at, last_order_at::text AS last_order_at "
            "FROM v_offer_performance ORDER BY orders DESC, rule_id DESC")


def performance() -> dict:
    """GET /shop/offers/performance: one row per rule. `ran` = at least one counted order used an
    offer; `available` False until the migration (the page then says the ledger is not on yet)."""
    from app.db_read import exec_sql
    try:
        rows = exec_sql(PERF_SQL) or []
    except Exception as e:  # noqa: BLE001 — the view arrives with scripts/r7d_offer_ledger_migration.sql
        log.info("v_offer_performance unavailable: %s", e)
        return {"available": False, "ran": False, "rows": [], "note": "The offer ledger is not switched on yet."}
    return {"available": True, "ran": any(int(r.get("orders") or 0) > 0 for r in rows), "rows": rows,
            "note": ("About 200 merchants order through the marketplace: only a large effect can be told apart "
                     "from chance. Hold-out rules compare like with like.")}


# ── shop_badge_log (OFF-6) ────────────────────────────────────────────────────

_badge_done: dict[str, bool] = {}


def log_badges_daily(now: datetime | None = None) -> dict:
    """Once per Bahrain day: the badges every catalog item wore, its stock and 90-day sales. A
    shop_jobs job (every 15 min): the first run of the day writes, the rest see today's rows and
    stop. Idempotent — the primary key (as_of, item_code) refuses a second copy."""
    shop = _shop()
    day = bahrain_day(now)
    if _badge_done.get(day):
        return {"as_of": day, "skipped": "already logged today"}
    if not table_ready("shop_badge_log", "as_of"):
        return {"as_of": day, "skipped": "shop_badge_log not installed"}
    client = shop.get_client()
    try:
        have = client.table("shop_badge_log").select("item_code").eq("as_of", day).limit(1).execute().data
    except Exception as e:  # noqa: BLE001
        return {"as_of": day, "error": f"{type(e).__name__}: {e}"[:160]}
    if have:
        _badge_done[day] = True
        return {"as_of": day, "skipped": "already logged today"}
    ctx = shop.context()
    badges = shop._badges(ctx)
    rows = []
    for code in ctx.get("order") or []:
        it = ctx["items"].get(code) or {}
        rows.append({"as_of": day, "item_code": code, "badges": badges.get(code, []),
                     "stock_qty": it.get("stock_qty"), "sold_90d": it.get("sold_90d"),
                     "price_bhd": shop.money(it.get("standard_rate")) if it.get("standard_rate") is not None else None})
    if not rows:
        return {"as_of": day, "rows": 0}
    try:
        for i in range(0, len(rows), 500):
            client.table("shop_badge_log").upsert(rows[i:i + 500], on_conflict="as_of,item_code",
                                                  ignore_duplicates=True).execute()
    except Exception as e:  # noqa: BLE001
        return {"as_of": day, "error": f"{type(e).__name__}: {e}"[:160]}
    _badge_done[day] = True
    return {"as_of": day, "rows": len(rows), "badged": sum(1 for r in rows if r["badges"])}
