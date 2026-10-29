"""YQ Assist v2 tests (29-Oct-2026): the chat that sells, supports and escalates.

    python -m tests.test_assist_v2

Same lightweight runner and stand-ins as tests/test_market_assist.py (no pytest; nothing is sent anywhere and
nothing is written). The rules pinned here are the owner's: a recommendation is never sold out, a policy answer
is only the office's approved text, a ticket only lands on an order this device holds, and nothing a model sees
is more than the public catalogue already shows.
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

# reconfigure, not a new wrapper: tests.test_market_assist (imported below for its stand-ins) wraps the streams
# itself, and a second wrapper over the same buffer would close it when the first is collected
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import warnings  # noqa: E402
warnings.filterwarnings("ignore")

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


# ── 1. data ────────────────────────────────────────────────────────────────────────────────

@test("migration: tickets + timeline service-role only, sequences revoked, refs by trigger, settings, self-check")
def _():
    sql = (ROOT / "scripts" / "market_assist_v2_migration.sql").read_text(encoding="utf-8").lower()
    for t in ("shop_tickets", "shop_ticket_events"):
        assert f"create table if not exists {t}" in sql
        assert f"alter table {t} enable row level security" in sql
        assert f"revoke all on table {t} from anon, authenticated" in sql
        assert f"revoke all on sequence {t}_id_seq from anon, authenticated" in sql
    assert "before insert on shop_tickets" in sql and "'t-' ||" in sql          # T-YYMM-NNNN
    assert "before update on shop_ticket_events" in sql and "before truncate on shop_ticket_events" in sql
    assert "on delete cascade" in sql                                            # the retention purge
    assert "on shop_tickets (device_id, client_ticket_id)" in sql                 # a double tap is one ticket
    assert "on shop_ticket_events (ticket_id, (detail->>'to'))" in sql            # one reminder per recipient
    assert "grant select on v_assist_open_tickets to yq_readonly" in sql          # the Command Centre's view
    view = sql[sql.index("create or replace view v_assist_open_tickets"):sql.index("revoke all on v_assist_open_tickets")]
    assert not any(c in view for c in ("note", "photos", "customer_id", "device_id", "ip_hash")), view
    for k, v in (("shop_ai_support_enabled", "0"), ("shop_ai_retention_days", "180"),
                 ("shop_ticket_keep_days", "730"), ("shop_ticket_sla_hours", "24"), ("shop_ai_policies", "[]")):
        assert f"('{k}', '{v}'" in sql, k
    assert "on conflict (key) do nothing" in sql and "raise exception" in sql
    assert "'ticket', 'callback', 'ticket_sla'" in sql                          # shop_notifications.kind
    body = sql.split("-- additive", 1)[1]           # the header's reverse recipe may name drops
    assert not any(s in body for s in ("drop table", "drop view", "delete from", "truncate table", "drop column"))


@test("settings: the five v2 defaults match the migration's seeds (no business number lives only in code)")
def _():
    from app import shop
    sql = (ROOT / "scripts" / "market_assist_v2_migration.sql").read_text(encoding="utf-8")
    for k in ("shop_ai_support_enabled", "shop_ai_retention_days", "shop_ticket_keep_days",
              "shop_ticket_sla_hours", "shop_ai_policies"):
        assert k in shop.SETTING_DEFAULTS, k
        assert f"('{k}', '{shop.SETTING_DEFAULTS[k]}'" in sql, k


@test("policies: only known topics, English required, one answer per topic, long text refused, empty clears")
def _():
    from app import shop
    ok = shop.validate_policies('[{"topic": "Payment", "en": " Cash or BenefitPay on delivery. ", "ar": ""}]')
    assert json.loads(ok) == [{"topic": "payment", "en": "Cash or BenefitPay on delivery."}]
    assert shop.validate_policies("") == "[]" and shop.validate_policies(None) == "[]"
    for bad in ('{"topic": "payment"}', '[{"topic": "refunds", "en": "x"}]', '[{"topic": "returns"}]',
                '[{"topic": "returns", "en": "a"}, {"topic": "returns", "en": "b"}]',
                json.dumps([{"topic": "credit", "en": "x" * 601}]), "not json"):
        try:
            shop.validate_policies(bad)
        except shop.ShopError:
            continue
        raise AssertionError(f"accepted: {bad[:40]}")
    pol = shop.policies({"shop_ai_policies": '[{"topic": "returns", "en": "Ask your rep.", "ar": "اسأل مندوبك."}]'})
    assert pol == {"returns": {"en": "Ask your rep.", "ar": "اسأل مندوبك."}}
    assert shop.policies({"shop_ai_policies": "{broken"}) == {} and shop.policies({}) == {}


# ── stand-ins: a shelf with a sold-out best seller and a sold-out offer, which must never be pushed ─────────

from tests.test_market_assist import Rig as _BaseRig, SETTINGS, _ask, _model_text  # noqa: E402


def _it(code, name, cat, spec="", stock="in_stock", price=1.0, moq=1, badges=(), proof=None):
    return {"item_code": code, "display_name": name, "spec": spec, "category": cat, "brand": "VFAN",
            "price_bhd": price, "stock_status": stock, "moq": moq, "pack_size": None, "badges": list(badges),
            "social_proof": proof}


SHELF = [
    _it("UK20", "20W PD charger", "CHARGER", "USB-C PD 20W fast charge", price=1.85, badges=("best_seller",)),
    _it("UK30", "30W PD charger", "CHARGER", "USB-C PD 30W fast charge", price=2.4, stock="out_of_stock",
        badges=("best_seller",)),
    _it("C18", "Car charger 18W", "CAR CHARGER", "Dual USB QC3.0 18W", price=1.4, badges=("trending",)),
    _it("CC1", "USB-C to USB-C cable 1m", "CABLE", "Type-C to Type-C 60W 1m", price=0.95, moq=10, badges=("best_seller",)),
    _it("LT1", "USB-A to Lightning cable", "CABLE", "for iPhone, 2.4A 1m", price=0.9, stock="out_of_stock"),
    _it("LT2", "Lightning cable braided", "CABLE", "for iPhone, 2.4A 1m braided", price=1.1),
    _it("TWS9", "TWS earbuds", "BLUETOOTH HEADSET", "Bluetooth 5.3 earbuds", price=4.5, badges=("new",)),
    _it("PB10", "Power bank 10000mAh", "POWER BANK", "10000mAh 22.5W", price=5.25, badges=("price_drop",)),
    _it("PB20", "Power bank 20000mAh", "POWER BANK", "20000mAh 22.5W", price=8.0, badges=("clearance",)),
    _it("SP1", "Mini speaker", "SPEAKER", "Bluetooth speaker 5W", price=3.0, stock="out_of_stock", badges=("on_offer",)),
    _it("HD1", "Car holder", "HOLDER", "magnetic air-vent holder", price=1.2, stock="low_stock", badges=("selling_fast",),
        proof="Ordered by 9 shops in the last 30 days"),
]


def _shelf_cat():
    return {"items": SHELF, "by_code": {i["item_code"]: i for i in SHELF}, "settings": SETTINGS, "pairs": {},
            "stock_as_of": "2026-10-28"}


class Rig(_BaseRig):
    """tests/test_market_assist.Rig plus the v2 seams: shop settings, the shelf, "my usual" and the quote engine."""

    def __init__(self, understanding=None, reply="", settings=None, usual=None, quote=None, **kw):
        super().__init__(understanding, reply, **kw)
        self.settings, self.usual, self.quote = settings or {}, usual or [], quote

    def __enter__(self):
        super().__enter__()
        from app import market_assist as ma, shop
        self._saved2 = {(shop, "context"): shop.context, (ma, "public_catalog"): ma.public_catalog,
                        (ma, "usual_lines"): ma.usual_lines, (shop, "price_cart"): shop.price_cart}
        shop.context = lambda force=False: {"settings": {"shop_ai_daily_cap": self.cap, **self.settings}}
        ma.public_catalog = _shelf_cat
        ma.usual_lines = lambda cat, tokens: [dict(x) for x in self.usual]

        def fake_quote(lines, *_a, **_k):
            if self.quote is None:
                raise RuntimeError("no quote engine here")
            return self.quote(lines)
        shop.price_cart = fake_quote
        return self

    def __exit__(self, *exc):
        for (mod, name), val in self._saved2.items():
            setattr(mod, name, val)
        super().__exit__(*exc)


def U(intent, **kw):
    base = {"intent": intent, "query": "", "codes": [], "qty": None, "lang": "en", "lines": [], "topic": "",
            "budget": None, "issue": "", "upset": False}
    base.update(kw)
    return base


SOLD_OUT = {i["item_code"] for i in SHELF if i["stock_status"] == "out_of_stock"}


# ── 2. selling: never sold out, always a reason ─────────────────────────────────────────────

@test("top: best sellers first, one category at a time, in stock only (the sold-out best seller never shows)")
def _():
    from app.market_assist import top_picks
    picks = top_picks(_shelf_cat())
    codes = [it["item_code"] for it, _ in picks]
    assert not SOLD_OUT & set(codes), codes
    assert codes[:2] == ["UK20", "CC1"], codes               # the two best sellers, from two categories
    assert dict((it["item_code"], w) for it, w in picks)["C18"] == "trending"
    car = [it["item_code"] for it, _ in top_picks(_shelf_cat(), "car charger")]
    assert car[0] == "C18" and set(car) <= {"C18", "HD1"}, car                              # narrowed (search.ts synonyms)
    assert top_picks(_shelf_cat(), "best selling hdmi projector") == []                      # not stocked: said so
    assert top_picks(_shelf_cat(), "best seller")[0][0]["item_code"] == "UK20"               # names nothing: whole shelf


@test("deals: a real price drop, then offers, then clearance; a sold-out offer is never pushed")
def _():
    from app.market_assist import deal_picks
    codes = [(it["item_code"], w) for it, w in deal_picks(_shelf_cat())]
    assert codes == [("PB10", "price_drop"), ("PB20", "clearance")], codes


@test("tiles: 'what's selling' answers from data with NO model call and no AI budget, logged with its action")
def _():
    from app import market_assist as ma
    with Rig() as rig:
        r = _ask(message="What's selling now", action="top")
        assert rig.sent == [] and ma._cap["n"] == 0
        assert r["intent"] == "top" and not SOLD_OUT & set(r["items"]) and r["cards"][0] == {"code": "UK20", "why": "best_seller"}
        assert rig.client.db["shop_ask_turns"][0]["action"] == "top"
        for action in ("deals", "plan", "usual", "orders", "handoff", "ticket", "tickets"):
            _ask(message=action, action=action)
        assert rig.sent == [], "a tile reached a model"


@test("typed 'what is selling most?': one model call (understand), the template, never a sold-out card")
def _():
    with Rig(U("top", query="")) as rig:
        r = _ask(message="what is selling most these days?")
        assert len(rig.sent) == 1 and r["reply"].startswith("These are selling best") and not SOLD_OUT & set(r["items"])
    with Rig(U("top", query="hdmi projector")) as rig:     # nothing selling for that: falls back to a search
        r = _ask(message="best selling hdmi projector")
        assert r["intent"] == "find" and r["miss"]


@test("sold out asked for: its card stays (marked), in-stock alternatives follow, the model is told about them")
def _():
    with Rig(U("find", query="lightning cable", codes=["LT1"]), reply="LT1 is sold out; LT2 is in stock.") as rig:
        r = _ask(message="LT1 cable for iphone")
        assert r["items"][:2] == ["LT1", "LT2"] and r["alts"] == {"LT1": ["LT2"]}
        assert {"code": "LT1", "why": "sold_out"} in r["cards"] and {"code": "LT2", "why": "alternative"} in r["cards"]
        assert "in stock instead: LT2" in _model_text(rig)
    with Rig(U("find", query="lightning cable", codes=["LT1"]), answer_fails=True) as _rig:
        r = _ask(message="LT1")
        assert r["reply"] == "LT1 is sold out. These are in stock instead."
    from app.market_assist import alternatives_for
    by = _shelf_cat()["by_code"]
    assert "CC1" not in alternatives_for(_shelf_cat(), by["LT1"])        # never a USB-C cable for a Lightning one
    assert alternatives_for(_shelf_cat(), by["UK30"]) == ["UK20"]          # the 20W PD charger for the 30W one


@test("plan: usual first, then what sells in those categories, then deals, then the minimum's own line; never sold out")
def _():
    def quote(lines):
        return {"minimum": {"met": False}, "gap_suggestions": [
            {"item_code": "UK30", "qty": 5}, {"item_code": "HD1", "qty": 10}]}      # a sold-out first: skipped
    with Rig(usual=[{"item_code": "LT1", "qty": 10}, {"item_code": "CC1", "qty": 20}], quote=quote) as rig:
        r = _ask(message="Plan my order", action="plan", order_tokens=["t" * 24])
        whys = [(ln["item_code"], ln["why"]) for ln in r["lines"]]
        assert whys[0] == ("CC1", "usual") and ("LT1", "usual") not in whys          # usual, in stock only
        assert ("PB10", "deal") in whys and ("HD1", "minimum") == whys[-1], whys
        assert not SOLD_OUT & {c for c, _ in whys} and r["plan"]["minimum"] and r["plan"]["usual"] == 1
        assert r["add"] == [{"item_code": ln["item_code"], "qty": ln["qty"]} for ln in r["lines"]]
        assert r["reply"].startswith("Here is a plan in three steps") and rig.sent == []
    with Rig() as _rig:                                     # a new shop: the shelf's best sellers and deals
        r = _ask(message="Plan my order", action="plan")
        assert r["reply"].startswith("Here is a starter plan") and {ln["why"] for ln in r["lines"]} <= {"selling", "deal"}
    with Rig(U("plan", budget=10)) as _rig:                 # a budget trims from the end
        r = _ask(message="I have 10 BD, plan my order")
        assert sum(ln["qty"] * next(i["price_bhd"] for i in SHELF if i["item_code"] == ln["item_code"])
                   for ln in r["lines"]) <= 10 or len(r["lines"]) == 1


@test("policy: only the office's approved words (no answer model); none approved -> the rep; delivery -> its facts")
def _():
    pol = json.dumps([{"topic": "returns", "en": "Report returns to your rep within the visit.", "ar": "أبلغ مندوبك."}])
    with Rig(U("policy", topic="returns"), reply="We refund anything!", settings={"shop_ai_policies": pol}) as rig:
        r = _ask(message="what is your return policy?")
        assert r["reply"] == "Report returns to your rep within the visit." and r["policy"] == {"topic": "returns", "official": True}
        assert len(rig.sent) == 1, "a model wrote a policy answer"
    with Rig(U("policy", topic="returns", lang="ar"), settings={"shop_ai_policies": pol}) as _rig:
        assert _ask(message="سياسة الإرجاع؟", lang="ar")["reply"] == "أبلغ مندوبك."
    with Rig(U("policy", topic="warranty"), reply="2 year warranty!") as rig:
        r = _ask(message="do you give warranty?")
        assert "warranty" not in r["reply"].lower() and r["handoff"]["name"] == "Ali" and r["escalated"]
        assert r["policy"] == {"topic": "warranty", "official": False} and len(rig.sent) == 1
        assert rig.client.db["shop_ask_turns"][0]["topic"] == "warranty"         # "Teach the assistant" reads this
    with Rig(U("policy", topic="delivery"), reply="Delivery is free across Bahrain.") as rig:
        r = _ask(message="is delivery free?")
        assert r["reply"] == "Delivery is free across Bahrain." and len(rig.sent) == 2


@test("escalation: account / credit / price talk -> the rep (+ call back when tickets are on); an upset merchant too")
def _():
    with Rig(U("account")) as _rig:
        r = _ask(message="I want credit and a better price")
        assert r["handoff"]["name"] == "Ali" and r["escalated"] and not r["callback"] and "Ali" in r["reply"]
    with Rig(U("account"), settings={"shop_ai_support_enabled": "1"}) as _rig:
        assert _ask(message="open an account")["callback"]
    with Rig(U("find", query="car charger", upset=True), reply="C18 is a car charger.") as _rig:
        r = _ask(message="WHY IS NOTHING WORKING, car charger!!")
        assert r["items"][0] == "C18" and r["handoff"]["name"] == "Ali" and r["escalated"]


@test("ticket: tickets off -> the rep on WhatsApp; on -> the ticket card with this device's orders and the issue")
def _():
    orders = [{"order_no": "YQ-2610-0007", "token": "t" * 24, "status": "delivered", "status_label": "Delivered",
               "created_at": "2026-10-27", "items": 3, "units": 40, "total_bhd": 21.5, "salesman": "Ali Hassan",
               "customer": {"name": "x"}}]
    with Rig(U("ticket", issue="damaged"), orders=orders) as _rig:
        r = _ask(message="the charger came broken", order_tokens=["t" * 24])
        assert r["ticket"] is None and r["handoff"]["name"] == "Ali" and "Ali" in r["reply"] and r["escalated"]
    with Rig(U("ticket", issue="damaged"), orders=orders, settings={"shop_ai_support_enabled": "1"}) as _rig:
        r = _ask(message="the charger came broken", order_tokens=["t" * 24])
        assert r["ticket"]["issue"] == "damaged" and r["ticket"]["orders"][0]["order_no"] == "YQ-2610-0007"
        assert "customer" not in r["ticket"]["orders"][0] and r["escalated"]


@test("clean_reply: 'out of stock' said the market's way; banned copy, phones, e-mails and links never come out")
def _():
    from app.market_assist import clean_reply
    assert clean_reply("LT1 is out of stock. LT2 is in stock.") == "LT1 is sold out. LT2 is in stock."
    assert clean_reply("UK20 fits. Last chance to buy! Save 20% today.") == "UK20 fits."
    assert clean_reply("Call Ali on 39123456. Or mail ali@yq.bh. See https://x.y/z now. C18 fits.") == "C18 fits."
    assert clean_reply("PB10 costs BHD 5.250. It has 10000mAh.") == "It has 10000mAh."
    assert clean_reply("هذا المنتج غير متوفر في المخزون.") == "هذا المنتج نفدت الكمية."


@test("mask: a ticket reference stays (the merchant's own), a phone number still goes")
def _():
    from app.market_assist import mask
    assert mask("what about T-2610-0012?") == "what about T-2610-0012?"
    assert "39123456" not in mask("T-2610-0012 call 39123456")


@test("understand: topic, budget, issue and upset are read and checked; anything else is dropped")
def _():
    from app import market_assist as ma
    with Rig(U("policy", topic="refunds", budget="lots", issue="exploded", upset="yes")) as _rig:
        u, _ = ma.understand("x", [], None)
        assert u["topic"] == "" and u["budget"] is None and u["issue"] == "" and u["upset"] is False
    with Rig(U("plan", budget="٥٠", topic="payment", issue="damaged", upset=True)) as _rig:
        u, _ = ma.understand("x", [], None)
        assert u["budget"] == 50.0 and u["topic"] == "payment" and u["issue"] == "damaged" and u["upset"] is True


@test("turn log: v2 columns written; a database without them still logs the turn (and stops trying for 10 min)")
def _():
    from app import market_assist as ma
    with Rig(U("find", query="car charger"), reply="C18 fits.") as rig:
        _ask(message="car charger")
        row = rig.client.db["shop_ask_turns"][0]
        assert row["detected_lang"] == "en" and row["ms_understand"] is not None and row["cards"][0]["code"] == "C18"

    class OldTable:
        def __init__(self, db):
            self.db = db

        def insert(self, row):
            if "cards" in row or "escalated" in row:
                raise RuntimeError("PGRST204 Could not find the 'cards' column of 'shop_ask_turns' in the schema cache")
            self.db.append(row)
            return type("E", (), {"execute": lambda s: type("D", (), {"data": [dict(row, id=len(self.db))]})()})()
    saved = dict(ma._v2_log)
    try:
        with Rig(U("find", query="car charger"), reply="C18 fits.") as rig:
            rows: list = []
            rig.client.table = lambda name: OldTable(rows)
            r = _ask(message="car charger")
            assert r["turn_id"] == 1 and rows[0]["intent"] == "find" and ma._v2_log["off_until"] > 0
    finally:
        ma._v2_log.update(saved)


@test("pins: the Python outcomes = the table's check; every template line exists in English and Arabic")
def _():
    from app import market_assist as ma
    sql = (ROOT / "scripts" / "market_assist_v2_migration.sql").read_text(encoding="utf-8")
    check = sql[sql.index("add constraint shop_ask_turns_outcome_check"):]
    check = check[check.index("("):check.index(";")]
    assert tuple(__import__("re").findall(r"'([a-z_]+)'", check)) == ma.OUTCOMES
    assert set(ma.TEMPLATES["en"]) == set(ma.TEMPLATES["ar"])
    for k, v in ma.TEMPLATES["en"].items():
        assert ("{name}" in v) == ("{name}" in ma.TEMPLATES["ar"][k]), k
        assert ("{code}" in v) == ("{code}" in ma.TEMPLATES["ar"][k]), k


def main() -> int:
    passed = failed = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception:  # noqa: BLE001
            print(f"  FAIL  {name}")
            traceback.print_exc(limit=3)
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
