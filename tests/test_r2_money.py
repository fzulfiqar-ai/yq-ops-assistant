"""R2c — exact money (plan §9 Step 2 item 6b, critic #12).

    python -m tests.test_r2_money

Pure: no database, no .env, no network (CI runs it with only SUPABASE_URL=https://ci.invalid).
Same lightweight runner as tests/test_shop.py.

What is asserted
  * money()/dmoney(): BHD to the fils, ROUND_HALF_UP, float only at the edge.
  * PROPERTY TEST: 10,000 random carts (random catalogues, quantities, MOQs, stock states, costs,
    qty_tier / bundle_price / salesman_offer / cart_value / coupon rules, referral codes, delivery
    thresholds, minimums, staff/backorder switches) priced by app.shop.price_cart and by an
    INDEPENDENT pure-Decimal reference written here from the business rules (docs/SHOP.md and the
    docstrings), never from the engine's code. Every money field must agree to the fils, and the
    identity  sum(line_total) - cart-level discounts + delivery == total  must hold exactly.
  * tier_progress: the kickback, the gap and the month-to-date figure are exact fils, HALF_UP.
  * confirm_order's stored confirmed prices are the engine's fils figures (no float re-arithmetic).
  * Every money value the engine returns is a float carrying at most three decimals.
"""
from __future__ import annotations

import io
import random
import sys
import traceback
from contextlib import contextmanager
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import warnings  # noqa: E402
warnings.filterwarnings("ignore")

TESTS: list[tuple[str, object]] = []

N_CARTS = 10_000
SEED = 2609          # the release branch's number — reproducible; change it to explore


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


# ── synthetic context (the same shape tests/test_shop.py builds; copied so this file imports nothing from it) ──

def _item(code, price, stock=100, cat="CABLE", moq=1, pack=None, b2c=None):
    return {"item_code": code, "display_name": code, "spec": f"{code} spec", "category": cat, "brand": "VFAN",
            "standard_rate": price, "b2c_rate": b2c, "product_image_url": None, "package_image_url": None,
            "sort_order": None, "created_at": "2025-07-03T00:00:00+00:00", "moq": moq, "pack_size": pack,
            "stock_qty": stock, "stock_as_of": "2026-09-24", "sold_30d": 0, "prev_30d": 0,
            "sold_90d": 0, "customers_30d": 0}


def _rule(id, kind, name=None, item_codes=(), categories=(), referral_codes=(), **kw):
    r = {"id": id, "kind": kind, "name": name or f"rule{id}", "stackable": False, "min_qty": None,
         "min_value_bhd": None, "pct_off": None, "amount_off_bhd": None, "fixed_price_bhd": None,
         "coupon_code": None, "starts_at": None, "ends_at": None, "max_uses": None, "uses": 0, "priority": 100,
         "scope": {"item_codes": [c.upper() for c in item_codes], "categories": [c.upper() for c in categories],
                   "referral_codes": [c.lower() for c in referral_codes]}}
    r.update(kw)
    return r


def _ctx(items, rules=(), costs=None, **settings):
    from app.shop import SETTING_DEFAULTS
    vals = dict(SETTING_DEFAULTS)
    vals.update({k: str(v) for k, v in settings.items()})
    return {"settings": vals, "items": {i["item_code"]: i for i in items},
            "order": [i["item_code"] for i in items],
            "costs": costs if costs is not None else {i["item_code"]: 0.001 for i in items}, "rules": list(rules),
            "salesmen": [{"id": 1, "name": "Furqan Ahmed", "referral_code": "furqan", "is_active": True},
                         {"id": 2, "name": "Harsh Bhatia", "referral_code": "harsh", "is_active": True}],
            "loaded_at": ""}


# ── the independent reference: pure Decimal, from the business rules ──────────

FILS = Decimal("0.001")
ZERO = Decimal(0)


def q3(x) -> Decimal:
    """BHD to the fils, half-up — the only rounding the shop uses."""
    return Decimal(x).quantize(FILS, rounding=ROUND_HALF_UP)


def dec(x) -> Decimal:
    """Exact Decimal of a test value (ints, 3-dp strings, Decimals; floats via their repr)."""
    if x is None:
        return ZERO
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


def _scope_hit(rule, item) -> bool:
    sc = rule["scope"]
    if not sc["item_codes"] and not sc["categories"]:
        return True
    return item["item_code"].upper() in sc["item_codes"] or str(item.get("category") or "OTHER").upper() in sc["categories"]


def _ref_hit(rule, ref) -> bool:
    refs = rule["scope"]["referral_codes"]
    return not refs or (bool(ref) and ref in refs)


def _rule_unit(rule, unit: Decimal) -> Decimal:
    if rule.get("fixed_price_bhd") is not None:
        return min(unit, dec(rule["fixed_price_bhd"]))
    if rule.get("pct_off") is not None:
        return unit * (Decimal(100) - dec(rule["pct_off"])) / Decimal(100)
    if rule.get("amount_off_bhd") is not None:
        return unit - dec(rule["amount_off_bhd"])
    return unit


def reference_quote(raw_lines, coupon_code, referral_code, ctx, *, staff=False, force_backorder=False) -> dict:
    """The rules, restated (docs/SHOP.md, price_cart's docstring, the owner's decisions):

    1. Lines are merged by item code, case-insensitively, keeping the first spelling; the
       catalogue's own spelling wins once resolved.
    2. A line is BLOCKED (kept, priced at zero) when the code is not in the catalogue, the list
       price is missing or zero, the quantity is under the MOQ, or the item is sold out and this
       path takes no backorder (merchants: shop_allow_backorder; staff: shop_allow_backorder_staff;
       force_backorder overrides both). Sold out = stock <= 0; low = stock <= shop_low_stock_units.
    3. Unit price: the list price to the fils; the best (lowest) NON-stackable item rule
       (qty_tier at/above its min_qty, bundle_price, salesman_offer only with a referral that the
       rule names) is applied, then every stackable item rule on top in rule order; the result is
       floored at zero and rounded to the fils ONCE. The margin floor (landed cost x (1+margin)
       x (1+VAT), to the fils) clamps it to min(list, floor). Line total = unit x qty; line
       discount = (list - unit) x qty; the subtotal sums list x qty — each to the fils.
    4. Cart level: the headroom above the floor on floored lines is the cap. A cart_value rule's
       amount is pct/amount of the eligible line totals (lines in its scope), zero under its
       minimum. The best non-stackable automatic rule (largest amount, first wins ties) competes
       with the coupon: both apply if either is stackable, else the larger (a tie goes to the
       coupon); stackable automatic rules always apply after. Each amount is capped by what is
       left of the headroom and rounded to the fils; zero amounts are dropped.
    5. discount = item discounts + cart amounts; net = subtotal - discount; delivery = the fee
       when there are priced lines, a threshold and a fee, and net is under the threshold;
       total = net + delivery. The minimum order and small-order mode never change the money."""
    vals = ctx["settings"]
    key = "shop_allow_backorder_staff" if staff else "shop_allow_backorder"
    allow_bo = force_backorder or str(vals.get(key, "")).strip() in ("1", "true", "True", "yes")
    ref = (referral_code or "").strip().lower() or None
    by_upper = {c.upper(): c for c in ctx["items"]}

    merged: dict[str, list] = {}
    for ln in raw_lines:
        code = str(ln["item_code"]).strip()[:64]
        k = code.upper()
        if k in merged:
            merged[k][1] += int(ln["qty"])
        else:
            merged[k] = [code, int(ln["qty"])]

    priced: list[dict] = []
    blocked = 0
    subtotal = ZERO
    margin = dec(vals.get("shop_min_margin_pct") or "0.2")
    vat = dec(vals.get("shop_vat_rate") or "0")
    for k, (raw_code, qty) in merged.items():
        code = by_upper.get(k)
        it = ctx["items"].get(code) if code else None
        if not it:
            blocked += 1
            continue
        lp_raw = it.get("standard_rate")
        if lp_raw is None or dec(lp_raw) <= 0:
            blocked += 1
            continue
        lp = q3(dec(lp_raw))
        moq = max(int(it.get("moq") or 1), 1)
        stock = dec(it.get("stock_qty"))
        out = stock <= 0
        if qty < moq or (out and not allow_bo):
            blocked += 1
            continue
        cands = []
        for r in ctx["rules"]:
            if r["kind"] not in ("qty_tier", "salesman_offer", "bundle_price"):
                continue
            if not _scope_hit(r, it) or not _ref_hit(r, ref):
                continue
            if r["kind"] == "qty_tier" and qty < int(r.get("min_qty") or 1):
                continue
            if r["kind"] == "salesman_offer" and not (ref and r["scope"]["referral_codes"]):
                continue
            cands.append(r)
        unit = lp
        for r in cands:
            if not r.get("stackable"):
                u = _rule_unit(r, lp)
                if u < unit:
                    unit = u
        for r in cands:
            if r.get("stackable"):
                u = _rule_unit(r, unit)
                if u < unit:
                    unit = u
        unit = q3(max(unit, ZERO))
        cost = (ctx.get("costs") or {}).get(code)
        if cost is None:
            cost = (ctx.get("costs") or {}).get(code.upper())
        floor = None
        if cost is not None and dec(cost) > 0:
            floor = q3(dec(cost) * (1 + margin) * (1 + vat))
            if unit < floor:
                unit = min(lp, floor)
        subtotal += q3(lp * qty)
        priced.append({"code": code, "qty": qty, "lp": lp, "unit": unit, "floor": floor,
                       "line_total": q3(unit * qty), "discount": q3((lp - unit) * qty), "item": it})
    subtotal = q3(subtotal)
    item_discount = q3(sum((p["discount"] for p in priced), ZERO))
    net = q3(subtotal - item_discount)
    cap = sum((max(ZERO, (p["unit"] - p["floor"]) * p["qty"]) for p in priced if p["floor"] is not None), ZERO)

    def amount(rule) -> Decimal:
        eligible = sum((p["line_total"] for p in priced if _scope_hit(rule, p["item"])), ZERO)
        if rule.get("min_value_bhd") is not None and net < dec(rule["min_value_bhd"]):
            return ZERO
        if rule.get("pct_off") is not None:
            return eligible * dec(rule["pct_off"]) / Decimal(100)
        if rule.get("amount_off_bhd") is not None and eligible > 0:
            return min(eligible, dec(rule["amount_off_bhd"]))
        return ZERO

    best, best_amt, stackables = None, ZERO, []
    for r in ctx["rules"]:
        if r["kind"] != "cart_value" or not _ref_hit(r, ref):
            continue
        a = amount(r)
        if a <= 0:
            continue
        if r.get("stackable"):
            stackables.append((r, a))
        elif a > best_amt:
            best, best_amt = r, a
    coupon, coupon_amt = None, ZERO
    cc = (coupon_code or "").strip().upper()
    if cc:
        m = next((r for r in ctx["rules"] if r["kind"] == "coupon" and str(r.get("coupon_code") or "").upper() == cc), None)
        if m and _ref_hit(m, ref):
            a = amount(m)
            if a > 0:
                coupon, coupon_amt = m, a
    plan: list[tuple[dict, Decimal]] = []
    if coupon and best:
        if coupon.get("stackable") or best.get("stackable"):
            plan += [(best, best_amt), (coupon, coupon_amt)]
        elif coupon_amt >= best_amt:
            plan.append((coupon, coupon_amt))
        else:
            plan.append((best, best_amt))
    elif coupon:
        plan.append((coupon, coupon_amt))
    elif best:
        plan.append((best, best_amt))
    plan += stackables
    cart_total = ZERO
    cart_amounts: list[Decimal] = []
    for r, a in plan:
        a = q3(min(a, max(ZERO, cap - cart_total)))
        if a <= 0:
            continue
        cart_total += a
        cart_amounts.append(a)
    discount = q3(item_discount + cart_total)
    net_after = q3(subtotal - discount)
    threshold = q3(dec(vals.get("shop_free_delivery_threshold_bhd") or 0))
    fee = q3(dec(vals.get("shop_delivery_fee_bhd") or 0))
    delivery = fee if (priced and threshold > 0 and fee > 0 and net_after < threshold) else ZERO
    total = q3(net_after + delivery)
    return {"subtotal": subtotal, "discount": discount, "delivery": delivery, "total": total,
            "cart_total": cart_total, "cart_amounts": cart_amounts, "lines": priced, "blocked": blocked,
            "units": sum(p["qty"] for p in priced), "items": len(priced),
            "min_order": q3(dec(vals.get("shop_min_order_bhd") or 0))}


# ── random carts ──────────────────────────────────────────────────────────────

CATS = ("CABLE", "CHARGER", "AUDIO", "POWER BANK", "HOLDER")
CODE_SHAPES = ("{L}{n:02d}", "{L}{n:02d} UL-1Mtr", "{L}{n:02d}-C", "{L}{n:02d} CC 2Mtr", "TB-{L}{n}")


def _price(rng: random.Random) -> Decimal:
    kind = rng.random()
    if kind < 0.15:
        return Decimal(rng.randint(5, 999)) / 1000                  # sub-dinar accessory
    if kind < 0.35:
        return Decimal(rng.randint(1, 60))                           # round dinars
    if kind < 0.6:
        return Decimal(rng.randint(10, 6000)) / 100                  # 2-dp book price
    return Decimal(rng.randint(50, 60000)) / 1000                    # 3-dp book price


def random_catalogue(rng: random.Random) -> tuple[list[dict], dict]:
    n = rng.randint(3, 10)
    items, costs = [], {}
    used: set[str] = set()
    for i in range(n):
        while True:
            code = rng.choice(CODE_SHAPES).format(L=rng.choice("XPTUKMF"), n=rng.randint(1, 40))
            if code.upper() not in used:
                used.add(code.upper())
                break
        r = rng.random()
        price = None if r < 0.04 else (0 if r < 0.07 else _price(rng))
        stock = rng.choice([0, 0, rng.randint(1, 10), rng.randint(11, 500), rng.randint(11, 500)])
        moq = rng.choice([1, 1, 1, 6, 12])
        pack = rng.choice([None, None, 6, 12])
        item = _item(code, None if price is None else (str(price) if rng.random() < 0.7 else float(price)),
                     stock=stock, cat=rng.choice(CATS), moq=moq, pack=pack)
        items.append(item)
        if price and rng.random() < 0.7:
            # landed cost as a share of the VAT-inclusive list price: sometimes so high the floor
            # sits above the list price, sometimes tiny; stored under a random casing, like production
            share = Decimal(rng.randint(15, 115)) / 100
            cost = (Decimal(price) * share).quantize(Decimal("0.0001"))
            key = code if rng.random() < 0.6 else code.upper()
            costs[key] = str(cost) if rng.random() < 0.5 else float(cost)
    return items, costs


def _pick_scope(rng: random.Random, items):
    r = rng.random()
    if r < 0.5:
        return {"item_codes": [it["item_code"] for it in rng.sample(items, k=min(len(items), rng.randint(1, 2)))]}
    if r < 0.75:
        return {"categories": [rng.choice(CATS)]}
    return {}


def _discount_kw(rng: random.Random, price_hint: Decimal | None):
    r = rng.random()
    if r < 0.5:
        return {"pct_off": rng.choice([5, 7.5, 10, 12.5, 15, 20, 33, 40, 50, 66.67])}
    if r < 0.8:
        return {"amount_off_bhd": str(Decimal(rng.randint(5, 1500)) / 1000)}
    base = price_hint if price_hint else Decimal("1.5")
    return {"fixed_price_bhd": str((base * Decimal(rng.randint(40, 110)) / 100).quantize(Decimal("0.0001")))}


def random_rules(rng: random.Random, items) -> tuple[list[dict], list[str]]:
    rules, coupons = [], []
    rid = 1
    for _ in range(rng.randint(0, 6)):
        kind = rng.choice(["qty_tier", "qty_tier", "cart_value", "cart_value", "coupon", "salesman_offer", "bundle_price"])
        scope = _pick_scope(rng, items)
        refs = ["furqan"] if rng.random() < 0.15 else []
        hint = next((dec(it["standard_rate"]) for it in items
                     if it["item_code"] in scope.get("item_codes", []) and it.get("standard_rate")), None)
        kw: dict = {"stackable": rng.random() < 0.25}
        if kind == "qty_tier":
            kw.update(min_qty=rng.choice([6, 12, 24, 48, 100]), **_discount_kw(rng, hint))
        elif kind == "bundle_price":
            kw.update(fixed_price_bhd=_discount_kw(rng, hint).get("fixed_price_bhd", "1.000"))
        elif kind == "salesman_offer":
            refs = [rng.choice(["furqan", "harsh"])]
            kw.update(**{k: v for k, v in _discount_kw(rng, hint).items() if k != "fixed_price_bhd"} or {"pct_off": 10})
        elif kind == "cart_value":
            kw.update(min_value_bhd=rng.choice([None, 10, 25, 50, 100, 200]),
                      **{k: v for k, v in _discount_kw(rng, None).items() if k != "fixed_price_bhd"} or {"pct_off": 5})
        else:
            code = f"SAVE{rid}"
            coupons.append(code)
            kw.update(coupon_code=code, min_value_bhd=rng.choice([None, None, 20, 60]),
                      **{k: v for k, v in _discount_kw(rng, None).items() if k != "fixed_price_bhd"} or {"pct_off": 10})
        rules.append(_rule(rid, kind, item_codes=scope.get("item_codes", ()), categories=scope.get("categories", ()),
                           referral_codes=refs, **kw))
        rid += 1
    return rules, coupons


def random_settings(rng: random.Random) -> dict:
    return {
        "shop_min_margin_pct": rng.choice(["0", "0.10", "0.20", "0.35"]),
        "shop_vat_rate": rng.choice(["0", "0.10", "0.10"]),
        "shop_free_delivery_threshold_bhd": rng.choice(["0", "0", "20", "50"]),
        "shop_delivery_fee_bhd": rng.choice(["0", "1.5", "2.000"]),
        "shop_min_order_bhd": rng.choice(["0", "0", "20"]),
        "shop_small_order_mode": rng.choice(["request", "allow", "block"]),
        "shop_allow_backorder": rng.choice(["0", "1"]),
        "shop_allow_backorder_staff": rng.choice(["0", "1"]),
        "shop_low_stock_units": "10",
        "shop_gap_suggestions": "3",
    }


def random_cart(rng: random.Random, items, coupons):
    lines = []
    for _ in range(rng.randint(1, 7)):
        if rng.random() < 0.08:
            code = "ZZ99"
        else:
            code = rng.choice(items)["item_code"]
            if rng.random() < 0.3:
                code = code.lower() if rng.random() < 0.5 else f" {code.upper()} "
        qty = rng.choice([1, 2, 3, 5, 6, 10, 12, 24, 36, 48, rng.randint(1, 150), rng.randint(100, 999)])
        lines.append({"item_code": code, "qty": qty})
    r = rng.random()
    coupon = None
    if coupons and r < 0.4:
        coupon = rng.choice(coupons)
        if rng.random() < 0.2:
            coupon = coupon.lower()
    elif r < 0.5:
        coupon = "NOPE"
    ref = rng.choice([None, None, None, "furqan", "furqan", "harsh"])
    return lines, coupon, ref, rng.random() < 0.2, rng.random() < 0.1


def _f2d(v) -> Decimal:
    """A float the engine returned, read back exactly (the shortest repr of a 3-dp float is its 3-dp text)."""
    assert isinstance(v, float), f"money must reach the edge as a float, got {type(v).__name__}: {v!r}"
    d = Decimal(repr(v))
    assert d == d.quantize(FILS), f"more than three decimals at the edge: {v!r}"
    return d


# ── tests ─────────────────────────────────────────────────────────────────────

@test("money: dmoney rounds half-up to the fils and money() is its float at the edge")
def _():
    from app.shop import D0, dmoney, edge_floats, money
    assert dmoney("0.0005") == Decimal("0.001") and dmoney("0.0004") == Decimal("0.000")
    assert dmoney("2.6665") == Decimal("2.667") and dmoney("2.6664") == Decimal("2.666")
    assert dmoney(2.95) == Decimal("2.950") and dmoney(None) == D0 and dmoney("junk") == D0
    assert money("0.0005") == 0.001 and money(None) == 0.0 and isinstance(money(1), float)
    # a whole rule the float engine could not keep: 0.1 + 0.2 summed as Decimals is exactly 0.3
    assert sum((dmoney("0.1"), dmoney("0.2")), D0) == Decimal("0.300")
    out = edge_floats({"a": Decimal("1.500"), "b": [Decimal("0.001"), {"c": Decimal("2")}], "d": "x", "e": 3})
    assert out == {"a": 1.5, "b": [0.001, {"c": 2.0}], "d": "x", "e": 3}
    assert all(isinstance(v, float) for v in (out["a"], out["b"][0], out["b"][1]["c"]))


@test("pricing: the engine's own fixtures still price to the fils (list, tier, floor, cart rule, coupon)")
def _():
    from app.shop import price_cart
    q = price_cart([{"item_code": "t02", "qty": 2}], ctx=_ctx([_item("T02", 2.95)]))
    assert q["subtotal_bhd"] == 5.9 and q["total_bhd"] == 5.9 and isinstance(q["total_bhd"], float)
    ctx = _ctx([_item("T02", 2.95)], rules=[_rule(5, "qty_tier", min_qty=12, pct_off=10, item_codes=["T02"])])
    ln = price_cart([{"item_code": "T02", "qty": 12}], ctx=ctx)["lines"][0]
    assert (ln["unit_price_bhd"], ln["discount_bhd"], ln["line_total_bhd"]) == (2.655, 3.54, 31.86), ln
    # a half-fils unit price rounds UP (2.6655 -> 2.666), the float engine's behaviour on this very number
    ctx = _ctx([_item("T02", "2.961")], rules=[_rule(5, "qty_tier", min_qty=12, pct_off=10, item_codes=["T02"])])
    ln = price_cart([{"item_code": "T02", "qty": 12}], ctx=ctx)["lines"][0]
    assert ln["unit_price_bhd"] == 2.665 and ln["line_total_bhd"] == 31.98, ln     # 2.961 x 0.9 = 2.6649 -> 2.665
    ctx = _ctx([_item("T02", "2.962")], rules=[_rule(5, "qty_tier", min_qty=12, pct_off=10, item_codes=["T02"])])
    ln = price_cart([{"item_code": "T02", "qty": 12}], ctx=ctx)["lines"][0]
    assert ln["unit_price_bhd"] == 2.666, ln                                        # 2.962 x 0.9 = 2.6658 -> 2.666


@test("pricing: stacked percentages compound on the exact value and round once, where the unit is booked")
def _():
    from app.shop import price_cart
    # 10% then 15% stackable on 2.95: 2.95 x 0.9 x 0.85 = 2.25675 exactly -> 2.257 (half-up on the 4th place)
    ctx = _ctx([_item("T02", 2.95)], rules=[_rule(5, "qty_tier", min_qty=1, pct_off=10, item_codes=["T02"]),
                                             _rule(6, "qty_tier", min_qty=1, pct_off=15, item_codes=["T02"], stackable=True)])
    q = price_cart([{"item_code": "T02", "qty": 3}], ctx=ctx)
    ln = q["lines"][0]
    assert ln["unit_price_bhd"] == 2.257 and ln["line_total_bhd"] == 6.771 and ln["discount_bhd"] == 2.079, ln
    assert [a["rule_id"] for a in ln["applied"]] == [5, 6]
    assert q["subtotal_bhd"] == 8.85 and q["discount_bhd"] == 2.079 and q["total_bhd"] == 6.771


@test("pricing: sub-dinar prices and large quantities never drift (0.005 x 999, 0.145 x 7 x 50%)")
def _():
    from app.shop import price_cart
    q = price_cart([{"item_code": "A", "qty": 999}], ctx=_ctx([_item("A", "0.005")]))
    assert q["subtotal_bhd"] == 4.995 and q["total_bhd"] == 4.995, q
    ctx = _ctx([_item("B", "0.145")], rules=[_rule(1, "qty_tier", min_qty=1, pct_off=50, item_codes=["B"])])
    ln = price_cart([{"item_code": "B", "qty": 7}], ctx=ctx)["lines"][0]
    # 0.0725 -> 0.073 half-up; 7 x 0.073 = 0.511; discount 7 x (0.145 - 0.073) = 0.504; 0.511 + 0.504 = 1.015 = 7 x 0.145
    assert (ln["unit_price_bhd"], ln["line_total_bhd"], ln["discount_bhd"]) == (0.073, 0.511, 0.504), ln


@test("pricing: item_tiers publishes the same fils unit price the engine books at that quantity")
def _():
    from app.shop import item_tiers, price_cart
    rng = random.Random(SEED + 1)
    for _ in range(300):
        items, costs = random_catalogue(rng)
        rules, _c = random_rules(rng, items)
        ctx = _ctx(items, rules=[r for r in rules if r["kind"] == "qty_tier" and not r.get("stackable")
                                 and not r["scope"]["referral_codes"]], costs={}, shop_allow_backorder="1")
        for it in items:
            if not it.get("standard_rate") or dec(it["standard_rate"]) <= 0:
                continue
            for t in item_tiers(ctx, it):
                q = price_cart([{"item_code": it["item_code"], "qty": max(t["min_qty"], it["moq"] or 1)}], ctx=ctx)
                ln = q["lines"][0]
                if ln["unavailable"]:
                    continue
                assert ln["unit_price_bhd"] <= t["unit_price_bhd"], (it["item_code"], t, ln)
                assert _f2d(t["unit_price_bhd"]) == _f2d(t["unit_price_bhd"]).quantize(FILS)


@test(f"PROPERTY: {N_CARTS:,} random carts equal a pure-Decimal reference to the fils; sum(lines) - discounts + delivery == total")
def _():
    from app.shop import price_cart
    rng = random.Random(SEED)
    checked = lines_checked = discounted = capped = blocked_seen = delivery_seen = 0
    for n in range(N_CARTS):
        items, costs = random_catalogue(rng)
        rules, coupons = random_rules(rng, items)
        ctx = _ctx(items, rules=rules, costs=costs, **random_settings(rng))
        lines, coupon, ref, staff, force = random_cart(rng, items, coupons)
        q = price_cart(lines, coupon, ref, ctx=ctx, staff=staff, force_backorder=force)
        want = reference_quote(lines, coupon, ref, ctx, staff=staff, force_backorder=force)
        where = f"cart #{n}: lines={lines} coupon={coupon} ref={ref} staff={staff} force={force} rules={[f'{r["id"]}:{r["kind"]}' for r in rules]}"
        # headline money, to the fils
        got = {k: _f2d(q[k]) for k in ("subtotal_bhd", "discount_bhd", "delivery_bhd", "total_bhd", "min_order_bhd")}
        assert got["subtotal_bhd"] == want["subtotal"], f"subtotal {got} vs {want['subtotal']} — {where}"
        assert got["discount_bhd"] == want["discount"], f"discount {got} vs {want['discount']} — {where}"
        assert got["delivery_bhd"] == want["delivery"], f"delivery {got} vs {want['delivery']} — {where}"
        assert got["total_bhd"] == want["total"], f"total {got} vs {want['total']} — {where}"
        assert got["min_order_bhd"] == want["min_order"], where
        assert q["units"] == want["units"] and q["items"] == want["items"], where
        # every line, to the fils, in cart order
        priced = [ln for ln in q["lines"] if not ln["unavailable"]]
        assert len(priced) == len(want["lines"]), f"priced lines {len(priced)} vs {len(want['lines'])} — {where}"
        assert sum(1 for ln in q["lines"] if ln["unavailable"]) == want["blocked"], where
        for ln, ref_ln in zip(priced, want["lines"]):
            assert ln["item_code"] == ref_ln["code"] and ln["qty"] == ref_ln["qty"], where
            assert _f2d(ln["list_price_bhd"]) == ref_ln["lp"], f"list {ln} vs {ref_ln} — {where}"
            assert _f2d(ln["unit_price_bhd"]) == ref_ln["unit"], f"unit {ln} vs {ref_ln} — {where}"
            assert _f2d(ln["line_total_bhd"]) == ref_ln["line_total"], f"line total {ln} vs {ref_ln} — {where}"
            assert _f2d(ln["discount_bhd"]) == ref_ln["discount"], f"line discount {ln} vs {ref_ln} — {where}"
            lines_checked += 1
        for ln in q["lines"]:
            if ln["unavailable"]:
                assert (ln["unit_price_bhd"], ln["line_total_bhd"], ln["discount_bhd"]) == (0.0, 0.0, 0.0), where
        # cart-level amounts, one by one, and the identity to the fils
        cart_amts = [_f2d(d["amount_bhd"]) for d in q["discounts"] if d["kind"] in ("cart_value", "coupon")]
        assert cart_amts == want["cart_amounts"], f"cart amounts {cart_amts} vs {want['cart_amounts']} — {where}"
        lines_sum = sum((_f2d(ln["line_total_bhd"]) for ln in q["lines"]), ZERO)
        assert lines_sum - sum(cart_amts, ZERO) + got["delivery_bhd"] == got["total_bhd"], \
            f"identity: {lines_sum} - {sum(cart_amts, ZERO)} + {got['delivery_bhd']} != {got['total_bhd']} — {where}"
        assert got["subtotal_bhd"] - got["discount_bhd"] + got["delivery_bhd"] == got["total_bhd"], where
        # the other money-shaped fields are 3-dp floats too
        if q["progress"]:
            _f2d(q["progress"]["threshold_bhd"]); _f2d(q["progress"]["remaining_bhd"])
        if q["minimum"]:
            _f2d(q["minimum"]["value_bhd"]); _f2d(q["minimum"]["remaining_bhd"]); _f2d(q["minimum"]["fee_bhd"])
            for g in q["gap_suggestions"]:
                _f2d(g["unit_price_bhd"]); _f2d(g["value_bhd"])
        checked += 1
        discounted += bool(cart_amts)
        capped += any(c.startswith("cart:") for c in q["_clamped"])
        blocked_seen += want["blocked"] > 0
        delivery_seen += want["delivery"] > 0
    assert checked == N_CARTS
    # the generator really exercised the branches (not 10,000 plain carts)
    assert lines_checked > N_CARTS and discounted > N_CARTS // 20 and capped > 50 and blocked_seen > N_CARTS // 10 \
        and delivery_seen > 100, (lines_checked, discounted, capped, blocked_seen, delivery_seen)
    print(f"      {checked:,} carts · {lines_checked:,} priced lines · {discounted:,} with cart-level money · "
          f"{capped:,} floor-capped · {blocked_seen:,} with a blocked line · {delivery_seen:,} charged delivery")


@test("targets: tier_progress is exact fils, half-up, floats only at the edge")
def _():
    from app.shop import tier_progress
    row = {"team": "mobile_accessories", "target_bhd": "500", "tier2_bhd": 1200, "tier3_bhd": 2000.0,
           "kickback_t1": 0.05, "kickback_t2": "0.07", "kickback_t3": 0.08}
    t = tier_progress(row, 1216.51, "2026-09-24")
    assert t["kickback_bhd"] == 85.156 and t["mtd_bhd"] == 1216.51 and t["tier_reached"] == 2, t   # 85.1557 -> 85.156
    assert t["next_tier"]["gap_bhd"] == 783.49 and t["next_tier"]["bhd"] == 2000.0
    assert all(isinstance(v, float) for v in (t["kickback_bhd"], t["mtd_bhd"], t["next_tier"]["gap_bhd"]))
    assert [x["bhd"] for x in t["tiers"]] == [500.0, 1200.0, 2000.0] and all(isinstance(x["bhd"], float) for x in t["tiers"])
    # a half-fils kickback rounds up: 10.007 x 5% = 0.50035 -> 0.500; 10.01 x 5% = 0.5005 -> 0.501
    assert tier_progress({"target_bhd": 1, "kickback_t1": 0.05}, 10.007, None)["kickback_bhd"] == 0.5
    assert tier_progress({"target_bhd": 1, "kickback_t1": 0.05}, 10.01, None)["kickback_bhd"] == 0.501
    # the float engine could not say 0.1 + 0.2 reaches 0.3; the threshold test is exact now
    assert tier_progress({"target_bhd": "0.3", "kickback_t1": 0.05}, Decimal("0.1") + Decimal("0.2"), None)["tier_reached"] == 1
    assert tier_progress({"target_bhd": "0.3", "kickback_t1": 0.05}, "0.3", None)["tier_reached"] == 1
    # progress is a tenth of a percent, half-up: 1216.51 / 2000 = 60.8255% -> 60.8; 1217 / 2000 = 60.85 -> 60.9
    assert t["progress_pct"] == 60.8 and tier_progress(row, 1217, "2026-09-24")["progress_pct"] == 60.9
    assert tier_progress(row, 2600, "2026-09-24")["progress_pct"] == 100.0
    # a negative or junk month-to-date reads as zero, never as an error
    z = tier_progress(row, -5, None)
    assert z["mtd_bhd"] == 0.0 and z["tier_reached"] == 0 and z["kickback_bhd"] == 0.0
    assert tier_progress(row, "n/a", None)["mtd_bhd"] == 0.0


class _Query:
    """The smallest PostgREST stand-in confirm_order needs: select/update/insert/eq/execute."""

    def __init__(self, db, table):
        self.db, self.table, self.op, self.payload, self.filters = db, table, "select", None, []

    def select(self, *_a, **_k):
        self.op = "select"
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        rows = self.db.setdefault(self.table, [])
        hit = [r for r in rows if all(r.get(c) == v for c, v in self.filters)]
        if self.op == "update":
            for r in hit:
                r.update(self.payload)
            return type("R", (), {"data": hit})()
        if self.op == "insert":
            rows.append(dict(self.payload))
            return type("R", (), {"data": [self.payload]})()
        return type("R", (), {"data": hit})()


@contextmanager
def _confirm_env(db, ctx):
    from app import shop
    client = type("C", (), {"table": lambda self, t: _Query(db, t)})()
    saved = (shop.get_client, shop.has_column, shop.context)
    shop.get_client = lambda: client
    shop.has_column = lambda table, column: True
    shop.context = lambda force=False: ctx
    try:
        yield
    finally:
        shop.get_client, shop.has_column, shop.context = saved


@test("confirm: the confirmed unit and line totals stored are the engine's fils figures (no float re-arithmetic)")
def _():
    from app.shop import confirm_order
    ctx = _ctx([_item("T02", "2.961"), _item("X05", "0.145")],
               rules=[_rule(5, "qty_tier", min_qty=12, pct_off=10, item_codes=["T02"]),
                      _rule(6, "qty_tier", min_qty=7, pct_off=50, item_codes=["X05"])])
    db = {"shop_orders": [{"id": 1, "order_no": "YQ-2609-0001", "status": "new", "subtotal_bhd": 30.0, "total_bhd": 30.0,
                           "coupon_code": None, "referral_code": None}],
          "shop_order_lines": [{"id": 11, "order_id": 1, "item_code": "T02", "qty": 3, "qty_confirmed": None, "line_status": "ok"},
                               {"id": 12, "order_id": 1, "item_code": "X05", "qty": 7, "qty_confirmed": None, "line_status": "ok"}],
          "shop_order_events": []}
    with _confirm_env(db, ctx):
        out = confirm_order(1, [{"line_id": 11, "qty_confirmed": 12}], "Tomorrow", None, actor="rep@example.com")
    by = {ln["id"]: ln for ln in db["shop_order_lines"]}
    assert by[11]["unit_price_confirmed"] == 2.665 and by[11]["line_total_confirmed"] == 31.98, by[11]   # 2.6649 -> 2.665
    assert by[12]["unit_price_confirmed"] == 0.073 and by[12]["line_total_confirmed"] == 0.511, by[12]   # 0.0725 -> 0.073
    hdr = db["shop_orders"][0]
    assert hdr["status"] == "confirmed" and hdr["total_confirmed_bhd"] == 32.491 and hdr["subtotal_confirmed_bhd"] == 36.547, hdr
    assert out["totals"]["total_bhd"] == 32.491 and all(isinstance(v, float) for v in
                                                           (hdr["total_confirmed_bhd"], by[11]["unit_price_confirmed"]))
    assert _f2d(hdr["total_confirmed_bhd"]) == sum((_f2d(by[i]["line_total_confirmed"]) for i in (11, 12)), ZERO)


@test("api shape: a quote is JSON-serialisable and carries no Decimal anywhere")
def _():
    import json
    from app.shop import price_cart
    ctx = _ctx([_item("T02", 2.95, moq=6), _item("X05", "0.4", stock=0)],
               rules=[_rule(7, "cart_value", "5% over 5", min_value_bhd=5, pct_off=5),
                      _rule(8, "coupon", "TEN", coupon_code="TEN", pct_off=10)],
               shop_min_order_bhd="20", shop_free_delivery_threshold_bhd="0", shop_allow_backorder="0")
    q = price_cart([{"item_code": "T02", "qty": 6}, {"item_code": "X05", "qty": 3}], coupon_code="TEN", ctx=ctx)
    text = json.dumps(q)     # a Decimal would raise TypeError here
    assert '"total_bhd": 15.93' in text and q["lines"][1]["unavailable"] and q["lines"][1]["list_price_bhd"] == 0.4
    assert q["minimum"]["remaining_bhd"] == 4.07 and q["minimum"]["value_bhd"] == 20.0


def main() -> int:
    failed = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
