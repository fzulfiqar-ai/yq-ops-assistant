"""R3a — salesman motivation + admin rollups (24-Sep-2026; plan §5 Salesman/Admin, §7 levers
2/3/5, §9 Step 3 items 1-4).

    python -m tests.test_r3_reps

Same lightweight runner as tests/test_v3.py. The pure tests need no database and no .env (CI
runs them with only SUPABASE_URL set): follow-up ranking, the deterministic holdout, rep scoping,
open-basket grouping, statement transitions on a fake PostgREST client, the attainment rows and
the route → gate table. The last test replays the follow-up and attainment SQL on the local
scratch Postgres (port 55432, synthetic rows, never production) and prints SKIP when that
cluster is not running.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import traceback
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")
os.environ.setdefault("SUPABASE_URL", "https://ci.invalid")   # app.main builds a JWKS URL from it; nothing is fetched

import warnings  # noqa: E402
warnings.filterwarnings("ignore")

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


class _Patched:
    def __init__(self, *triples):
        self.triples, self.saved = triples, []

    def __enter__(self):
        for mod, name, value in self.triples:
            self.saved.append((mod, name, getattr(mod, name)))
            setattr(mod, name, value)
        return self

    def __exit__(self, *exc):
        for mod, name, old in reversed(self.saved):
            setattr(mod, name, old)
        return False


def _raises(fn, exc_type, contains: str | None = None):
    """Run fn; the expected exception (optionally containing `contains`) or an AssertionError."""
    try:
        fn()
    except exc_type as e:
        if contains is not None:
            assert contains in str(e), f"expected {contains!r} in {str(e)!r}"
        return e
    raise AssertionError(f"expected {exc_type.__name__}")


# ── fixtures ──────────────────────────────────────────────────────────────────

DT = "2026-09-24"
TODAY = date(2026, 9, 24)


def _shop(name, rep="Ahmed Aradi - Acc WH", days_since=10, gap=None, net_180d="0", visits=3, rep_visits=None,
          invoices_180d=2):
    return {"customer_name": name, "rep": rep, "rep_visits": rep_visits if rep_visits is not None else visits,
            "visits": visits, "last_date": "2026-09-01", "first_date": "2025-10-01", "days_since": days_since,
            "median_gap": gap, "net_180d": net_180d, "invoices_180d": invoices_180d, "data_through": DT}


def _reg(shop, code, times=5, median_qty=6, due=True, cadence=21.0, days_since=25):
    return {"customer_name": shop, "item_code": code, "times_bought": times, "median_qty": median_qty,
            "cadence_days": cadence, "last_bought": "2026-08-30", "days_since": days_since, "due": due}


# ── follow-ups: ranking ───────────────────────────────────────────────────────

@test("followups: ranked by overdue ratio x monthly value; lapsed split out; fresh counted; no date claims")
def _():
    from app.followups import rank_shops
    shops = [
        _shop("Big Overdue", days_since=40, gap=20.0, net_180d="600"),      # ratio 2.0 x 100/mo = 200
        _shop("Small Overdue", days_since=60, gap=20.0, net_180d="60"),     # ratio 3.0 x 10/mo = 30
        _shop("Just Due", days_since=17, gap=20.0, net_180d="1200"),        # ratio 0.85 x 200 = 170
        _shop("Fresh", days_since=5, gap=20.0, net_180d="6000"),            # ratio 0.25 -> not due
        _shop("Gone", days_since=200, gap=20.0, net_180d="0", visits=12),   # > max(90, 80) -> lapsed
        _shop("Gone Newer", days_since=95, gap=10.0, net_180d="0", visits=2),
    ]
    # the fixture names are all outside the 20 % holdout for this rep (asserted so the test stays honest)
    out = rank_shops(shops, {}, {}, include_holdout=True)
    assert not any(r["holdout"] for r in out["due"] + out["lapsed"]), "pick fixture names outside the holdout"
    assert [r["shop"] for r in out["due"]] == ["Big Overdue", "Just Due", "Small Overdue"], out["due"]
    assert [r["rank"] for r in out["due"]] == [1, 2, 3]
    assert [r["shop"] for r in out["lapsed"]] == ["Gone", "Gone Newer"], "win-back ranks the deeper history first"
    assert out["counts"] == {"shops": 6, "due": 3, "lapsed": 2, "fresh": 1, "holdout": 0}, out["counts"]
    big = out["due"][0]
    assert big["overdue_ratio"] == 2.0 and big["monthly_value_bhd"] == "100.000" and big["gap_source"] == "own"
    assert big["why"] == "Usually every ~20 days · last bought 40 days ago"
    assert out["lapsed"][0]["why"] == "Bought 12 times · last bought 200 days ago"
    for r in out["due"] + out["lapsed"]:
        assert "due on" not in r["why"].lower() and not re.search(r"\d{4}-\d{2}-\d{2}", r["why"]), r["why"]
        assert Decimal(r["monthly_value_bhd"]) == Decimal(r["monthly_value_bhd"]).quantize(Decimal("0.001"))
    assert out["data_through"] == DT and out["rules"]["holdout_share"] == 0.2


@test("followups: a shop with one visit uses the rep's median gap (else 30 d) and says so")
def _():
    from app.followups import rank_shops, DEFAULT_GAP_DAYS
    shops = [_shop("Single", days_since=30, gap=None, visits=1, net_180d="30"),
             _shop("Reg A", days_since=1, gap=14.0), _shop("Reg B", days_since=1, gap=40.0)]
    out = rank_shops(shops, include_holdout=True)
    single = next(r for r in out["due"] + out["lapsed"] if r["shop"] == "Single")
    assert single["gap_days"] == 27.0 and single["gap_source"] == "default", single   # median of 14, 40
    assert single["why"] == "No pattern yet · last bought 30 days ago"
    assert single["overdue_ratio"] == round(30 / 27.0, 2)
    out2 = rank_shops([_shop("Alone", days_since=45, gap=None, visits=1)], include_holdout=True)
    assert out2["rules"]["default_gap_days"] == DEFAULT_GAP_DAYS
    assert out2["due"][0]["gap_days"] == DEFAULT_GAP_DAYS


@test("followups: top 5 DUE SKUs with the median quantity, display names mapped, most-bought first")
def _():
    from app.followups import rank_shops, TOP_SKUS
    regs = {"Big Overdue": [_reg("Big Overdue", f"T{n}", times=20 - n, median_qty=n + 1) for n in range(8)]
            + [_reg("Big Overdue", "X99", times=99, due=False)]}
    out = rank_shops([_shop("Big Overdue", days_since=40, gap=20.0, net_180d="600")], regs,
                     names={"T0": "Cable T0"}, include_holdout=True)
    skus = out["due"][0]["top_skus"]
    assert len(skus) == TOP_SKUS == 5 and [s["item_code"] for s in skus] == ["T0", "T1", "T2", "T3", "T4"]
    assert skus[0]["display_name"] == "Cable T0" and skus[1]["display_name"] == "T1"
    assert [s["median_qty"] for s in skus] == [1, 2, 3, 4, 5]
    assert all(s["item_code"] != "X99" for s in skus), "a SKU that is not due is not suggested"


# ── follow-ups: the holdout ───────────────────────────────────────────────────

@test("holdout: deterministic per (rep, shop), suffix- and case-insensitive, about 20 %, never shown to the rep")
def _():
    from app.followups import HOLDOUT_MOD, is_holdout, rank_shops, rep_base
    assert rep_base("Ahmed Aradi - Acc WH") == "Ahmed Aradi" and rep_base("Ishaq") == "Ishaq"
    assert is_holdout("Ahmed Aradi", "Al Noor Shop") == is_holdout("Ahmed Aradi", "Al Noor Shop")
    assert is_holdout("Ahmed Aradi - Acc WH", "Al Noor Shop") == is_holdout("ahmed aradi", "AL NOOR  SHOP")
    hits = sum(is_holdout("Ahmed Aradi", f"Shop {n}") for n in range(2000))
    assert 0.16 <= hits / 2000 <= 0.24, f"holdout share {hits / 2000:.3f} is not ~1/{HOLDOUT_MOD}"
    # a different rep gets a different split of the same shops
    other = sum(is_holdout("Faisal Kandikkal", f"Shop {n}") for n in range(2000))
    same = sum(is_holdout("Ahmed Aradi", f"Shop {n}") == is_holdout("Faisal Kandikkal", f"Shop {n}") for n in range(2000))
    assert 0.16 <= other / 2000 <= 0.24 and same < 2000
    shops = [_shop(f"Shop {n}", days_since=50, gap=20.0, net_180d="120") for n in range(200)]
    rep_view = rank_shops(shops, include_holdout=False)
    admin_view = rank_shops(shops, include_holdout=True)
    held = {f"Shop {n}" for n in range(200) if is_holdout("Ahmed Aradi", f"Shop {n}")}
    assert held and not any(r["shop"] in held for r in rep_view["due"] + rep_view["lapsed"])
    assert rep_view["counts"]["holdout"] == len(held) and rep_view["counts"]["due"] == 200 - len(held)
    assert {r["shop"] for r in admin_view["due"] if r["holdout"]} == held
    assert admin_view["counts"]["due"] == 200 and admin_view["counts"]["holdout"] == len(held)


# ── follow-ups: scoping ───────────────────────────────────────────────────────

@test("followups: the SQL scopes by the rep's Focus name without LIKE; the company book needs all_reps=True; a blank name raises")
def _():
    from app import followups
    sql = followups.SHOPS_SQL
    assert "$1 = ''" in sql and "o.rep = $1" in sql and "split_part(o.rep, ' - ', 1) = $1" in sql
    assert "LIKE $1" not in sql, "an admin-typed Focus name must not act as a LIKE pattern"
    assert "division = 'Accessories'" in sql and "NOT v.is_giveaway" in sql and "NOT v.is_cash_customer" in sql
    assert "net_bhd" in sql and "revenue_bhd" not in sql, "ex-VAT value only"
    seen: dict = {}

    def fake_run(q, params):
        seen["sql"], seen["params"] = q, params
        return [_shop("Only Mine", days_since=40, gap=20.0, net_180d="600")]
    assert followups.load_shops("Ahmed Aradi", run=fake_run)[0]["customer_name"] == "Only Mine"
    assert seen["params"] == ["Ahmed Aradi"] and seen["sql"] is sql
    assert followups.load_shops("  Ahmed Aradi ", run=fake_run) and seen["params"] == ["Ahmed Aradi"]
    seen.clear()
    for blank in (None, "", "   "):
        _raises(lambda: followups.load_shops(blank, run=fake_run), ValueError, "Focus name")
    assert not seen, "a blank name must not reach the SQL"
    assert followups.load_shops(None, run=fake_run, all_reps=True) and seen["params"] == [""], "'' only with all_reps"
    calls: list = []
    with _Patched((followups, "load_shops", lambda name, run=None, all_reps=False: calls.append((name, all_reps)) or
                   [_shop("Big Overdue", days_since=40, gap=20.0, net_180d="600")]),
                  (followups, "load_regulars", lambda names: {}),
                  (followups, "load_contacts", lambda names: {"Big Overdue": {"phone": "97333000000"}}),
                  (followups, "_display_names", lambda: {})):
        followups.invalidate()
        for blank in (None, "", "  "):
            _raises(lambda: followups.followups(blank), ValueError, "Focus name")
        assert calls == [], "a blank name never loads anything"
        out = followups.followups("Ahmed Aradi")
        assert calls == [("Ahmed Aradi", False)] and out["rep"] == "Ahmed Aradi" and out["due"][0]["phone"] == "97333000000"
        followups.followups("Ahmed Aradi")
        assert calls == [("Ahmed Aradi", False)], "cached for the TTL"
        followups.followups("Ahmed Aradi", force=True)
        assert calls == [("Ahmed Aradi", False)] * 2
        book = followups.followups(None, all_reps=True, include_holdout=True)
        assert calls[-1] == (None, True) and book["rep"] is None
        # the tap guard: only a shop on the rep's current (cached) list counts
        assert followups.on_list("Ahmed Aradi", "big overdue") is True
        assert followups.on_list("Ahmed Aradi", "Somebody Else") is False
        assert followups.on_list("", "Big Overdue") is False and followups.on_list(None, "Big Overdue") is False
        followups.invalidate()


@test("followups: v_customer_regulars is read due-only, ordered and paged, so PostgREST's max-rows cannot truncate it silently")
def _():
    from app import database, followups
    db = _FakeDB()
    db.tables["v_customer_regulars"] = ([_reg("S1", f"C{n:04d}") for n in range(followups.REGULARS_PAGE + 20)]
                                        + [_reg("S1", "NOTDUE", due=False), _reg("S2", "Z1"), _reg("Other", "Q1")])
    with _Patched((database, "get_client", lambda: db)):
        out = followups.load_regulars(["S1", "S2"])
    assert len(out["S1"]) == followups.REGULARS_PAGE + 20 and [r["item_code"] for r in out["S2"]] == ["Z1"]
    assert "Other" not in out and not any(r["item_code"] == "NOTDUE" for r in out["S1"]), "only the asked shops, only due rows"
    sels = [e for e in db.log if e[0] == "select" and e[1] == "v_customer_regulars"]
    assert len(sels) == 2, f"{followups.REGULARS_PAGE + 20} rows must take two pages, got {len(sels)} selects"
    for _kind, _tbl, filters, orders, rng in sels:
        assert ("eq", "due", True) in filters and ("in", "customer_name", ["S1", "S2"]) in filters
        assert orders == [("customer_name", False), ("item_code", False)]
    assert [s[4] for s in sels] == [(0, followups.REGULARS_PAGE - 1), (followups.REGULARS_PAGE, 2 * followups.REGULARS_PAGE - 1)]


@test("followups: the route gives a rep his own book only, an admin any rep's (or all), an unlinked login nothing")
def _():
    from fastapi.testclient import TestClient
    from app import auth, followups, shop, user_auth
    import app.main as m

    def user(role, email="rep@example.com"):
        return auth.CurrentUser(user_id="uid-" + email, email=email, role=role, status="active", must_reset=False)

    seen: list = []

    def fake_followups(name, all_reps=False, include_holdout=False, force=False):
        seen.append((name, all_reps, include_holdout))
        return {"rep": name or None, "due": [_shop("S", rep=name) | {"top_skus": [], "rank": 1}], "lapsed": [],
                "counts": {"due": 1}, "data_through": DT, "rules": {}}
    reps = {"rep@example.com": {"id": 7, "name": "Ahmed Aradi", "focus_name": "Ahmed Aradi", "referral_code": "ahmed"},
            "noname@example.com": {"id": 8, "name": "New Rep", "focus_name": None, "referral_code": "new"},
            "blank@example.com": {"id": 9, "name": "Blank Rep", "focus_name": "   ", "referral_code": "blank"}}
    with _Patched((followups, "followups", fake_followups),
                  (shop, "salesman_for_user", lambda email: reps.get(email)),
                  (user_auth, "_user_row", lambda email: {"email": email, "features": ["Shop Orders"]}),
                  (shop, "_salesman_by_id", lambda sid: {"focus_name": "Faisal Kandikkal"} if int(sid) == 3 else None)):
        c = TestClient(m.app)
        m.app.dependency_overrides[auth.get_current_user] = lambda: user("salesman")
        r = c.get("/shop/me/followups").json()
        assert seen[-1] == ("Ahmed Aradi", False, False) and r["scope"] == "rep" and r["due"][0]["wa_text"].startswith("Hello, Ahmed from YQ")
        for who in ("noname@example.com", "blank@example.com"):
            m.app.dependency_overrides[auth.get_current_user] = lambda who=who: user("salesman", who)
            n = len(seen)
            r = c.get("/shop/me/followups").json()
            assert r["due"] == [] and "Focus name" in r["hint"] and len(seen) == n, "a blank focus_name never reaches followups()"
        m.app.dependency_overrides[auth.get_current_user] = lambda: user("salesman", "nobody@example.com")
        r = c.get("/shop/me/followups").json()
        assert r["due"] == [] and r["hint"] == shop.UNLINKED_HINT
        m.app.dependency_overrides[auth.get_current_user] = lambda: user("admin", "boss@example.com")
        r = c.get("/shop/me/followups?rep=Faisal%20Kandikkal").json()
        assert seen[-1] == ("Faisal Kandikkal", False, True) and r["scope"] == "admin"
        c.get("/shop/me/followups?rep=3")
        assert seen[-1] == ("Faisal Kandikkal", False, True), "an admin may name the rep by salesman id"
        c.get("/shop/me/followups")
        assert seen[-1] == (None, True, True), "no rep = the company book, asked for explicitly (all_reps)"
        assert c.get("/shop/me/followups?rep=99").status_code == 404
        m.app.dependency_overrides.clear()


@test("followups: the tap route is rate-limited and records only a shop on the caller's own list")
def _():
    from fastapi.testclient import TestClient
    from app import auth, database, followups, shop, user_auth
    import app.main as m
    lims = m.limiter._route_limits.get("app.shop_api.shop_me_followups_tap") or []
    assert lims and lims[0].limit.amount == 60 and lims[0].limit.GRANULARITY.name == "minute", "60/minute on the tap route"

    def user(role, email="rep@example.com"):
        return auth.CurrentUser(user_id="uid-" + email, email=email, role=role, status="active", must_reset=False)
    db = _FakeDB()
    asked: list = []
    reps = {"rep@example.com": {"id": 7, "name": "Ahmed Aradi", "focus_name": "Ahmed Aradi", "referral_code": "ahmed"}}
    with _Patched((shop, "salesman_for_user", lambda email: reps.get(email)),
                  (user_auth, "_user_row", lambda email: {"email": email, "features": ["Shop Orders"]}),
                  (followups, "on_list", lambda name, s: asked.append((name, s)) or s == "Big Overdue"),
                  (database, "get_client", lambda: db)):
        c = TestClient(m.app)
        m.app.dependency_overrides[auth.get_current_user] = lambda: user("salesman")
        r = c.post("/shop/me/followups/tap", json={"shop": "Big Overdue", "kind": "due", "channel": "wa", "rank": 1})
        assert r.status_code == 200 and r.json() == {"ok": True}
        r = c.post("/shop/me/followups/tap", json={"shop": "Not Mine", "kind": "due", "channel": "wa"})
        assert r.status_code == 200 and r.json()["ok"] is False
        m.app.dependency_overrides[auth.get_current_user] = lambda: user("salesman", "nobody@example.com")
        r = c.post("/shop/me/followups/tap", json={"shop": "Big Overdue", "kind": "due", "channel": "wa"})
        assert r.json()["ok"] is False, "no salesman row, nothing to measure"
        m.app.dependency_overrides.clear()
    assert asked == [("Ahmed Aradi", "Big Overdue"), ("Ahmed Aradi", "Not Mine")]
    taps = [e for e in db.log if e[0] == "insert" and e[1] == "audit_log"]
    assert len(taps) == 1 and taps[0][2]["event"] == "followup.tap", taps
    detail = taps[0][2]["detail"]
    assert detail["shop"] == "Big Overdue" and detail["rep"] == "Ahmed Aradi" and detail["salesman_id"] == 7


# ── baskets from your link not sent ──────────────────────────────────────────

@test("baskets: add/qty/remove replayed per device, ordered devices dropped, Decimal value, no PII in the payload")
def _():
    from app.followups import group_baskets
    ev = [
        {"ts": "2026-09-20T10:00:00+00:00", "event": "add", "device_id": "dev-A", "item_code": "t02", "meta": {"count": 12}},
        {"ts": "2026-09-20T10:01:00+00:00", "event": "add", "device_id": "dev-A", "item_code": "X05", "meta": {"count": 3}},
        {"ts": "2026-09-20T10:02:00+00:00", "event": "qty", "device_id": "dev-A", "item_code": "X05", "meta": {"count": 5}},
        {"ts": "2026-09-20T10:03:00+00:00", "event": "add", "device_id": "dev-A", "item_code": "Z1", "meta": {}},
        {"ts": "2026-09-20T10:04:00+00:00", "event": "remove", "device_id": "dev-A", "item_code": "Z1", "meta": None},
        {"ts": "2026-09-21T09:00:00+00:00", "event": "add", "device_id": "dev-B", "item_code": "T02", "meta": {"count": 1}},
        {"ts": "2026-09-21T09:05:00+00:00", "event": "order", "device_id": "dev-B", "item_code": None, "meta": None},
        {"ts": "2026-09-22T09:00:00+00:00", "event": "add", "device_id": "dev-C", "item_code": "T02", "meta": {"count": 2}},
        {"ts": "2026-09-22T09:00:00+00:00", "event": "add", "device_id": "", "item_code": "T02", "meta": {"count": 2}},
        {"ts": "2026-09-22T09:00:00+00:00", "event": "add", "device_id": "dev-D", "item_code": "NOPRICE", "meta": {"count": 2}},
    ]
    prices = {"T02": Decimal("2.950"), "X05": Decimal("0.405")}
    out = group_baskets(ev, {"dev-C"}, prices, {"T02": "Cable T02"})
    assert [b["value_bhd"] for b in out] == ["37.425", "0.000"], out   # 12 x 2.950 + 5 x 0.405 = 35.400 + 2.025
    a = out[0]
    assert a["items"] == [{"item_code": "T02", "display_name": "Cable T02", "qty": 12, "line_bhd": "35.400"},
                          {"item_code": "X05", "display_name": "X05", "qty": 5, "line_bhd": "2.025"}]
    assert a["units"] == 17 and a["items_count"] == 2 and a["last_activity"] == "2026-09-20T10:04:00+00:00"
    assert out[1]["items"][0]["item_code"] == "NOPRICE" and out[1]["items"][0]["line_bhd"] is None
    text = str(out)
    for pii in ("dev-A", "dev-D", "device_id", "phone", "customer", "name\":"):
        assert pii not in text, f"{pii} leaked into the basket payload"
    assert len(a["basket"]) == 6
    assert group_baskets(ev, set(), prices, limit=1) and len(group_baskets(ev, set(), prices, limit=1)) == 1


@test("baskets + link week: rep-scoped by referral code / salesman row; no code = empty, never an error; link week cached per rep")
def _():
    from app import followups, shop
    assert followups.baskets_not_sent({"id": 1, "referral_code": ""})["baskets"] == []
    seen: list = []

    def fake_analytics(days, salesman=None):
        seen.append((days, salesman))
        return {"days": days, "since": "2026-09-17", "funnel": {"sessions": 40, "item_views": 30, "adds": 9,
                "checkouts": 4, "conversion_pct": 7.5}, "orders": 3, "cancelled": 1, "value_bhd": 61.2,
                "aov_bhd": 20.4, "customers": 3, "engagement": {"share": 2},
                "top_products": [{"item_code": "T02", "display_name": "Cable", "units": 12, "value_bhd": 35.4}] * 5}
    with _Patched((shop, "analytics", fake_analytics)):
        followups.invalidate()
        out = followups.link_week({"id": 7, "referral_code": "ahmed"})
        assert seen == [(7, {"id": 7, "referral_code": "ahmed"})]
        assert out["orders"] == 3 and out["value_bhd"] == "61.200" and out["aov_bhd"] == "20.400"
        assert out["sessions"] == 40 and out["conversion_pct"] == 7.5 and len(out["top_products"]) == 3
        assert out["shares"] == 2 and out["top_products"][0]["value_bhd"] == "35.400"
        assert followups.link_week({"id": 7, "referral_code": "ahmed"}) == out and len(seen) == 1, "cached: analytics ran once"
        followups.link_week({"id": 8, "referral_code": "other"})
        assert len(seen) == 2, "another rep is another cache entry"
        followups.link_week({"id": 7, "referral_code": "ahmed"}, force=True)
        assert len(seen) == 3
        followups.invalidate()


# ── statements: pure rules ────────────────────────────────────────────────────

@test("statements: transitions are forward only; periods validated; money is 3-dp half-up strings; period helpers")
def _():
    from app import statements as st
    ok = {("draft", "approved"), ("draft", "superseded"), ("approved", "paid"), ("approved", "superseded")}
    for a in st.STATUSES:
        for b in st.STATUSES:
            assert st.can_transition(a, b) == ((a, b) in ok), (a, b)
    assert not st.can_transition(None, "approved") and not st.can_transition("draft", None)
    assert st.valid_period("2026-09") == "2026-09"
    for bad in ("2026-9", "2026-13", "202609", "", None, "2026-00"):
        _raises(lambda: st.valid_period(bad), st.StatementError)
    assert st.s3("85.1555") == "85.156" and st.s3(0.0005) == "0.001" and st.s3(None) == "0.000" and st.s3("1216.51") == "1216.510"
    assert st.total_kickback([{"kickback_bhd": "85.156"}, {"kickback_bhd": "56.244"}, {"kickback_bhd": 28.994}]) == "170.394"
    row = st.public_row({"id": 1, "status": "draft", "sales_bhd": 1216.51, "kickback_bhd": "85.156", "returns_bhd": None,
                         "rate": "0.0700", "target_snapshot": '{"team": "normal"}'})
    assert row["sales_bhd"] == "1216.510" and row["kickback_bhd"] == "85.156" and row["returns_bhd"] is None
    assert row["rate"] == 0.07 and row["target_snapshot"] == {"team": "normal"} and row["next_statuses"] == ["approved", "superseded"]
    assert st.summarize([{"status": "draft", "kickback_bhd": "1.0005"}, {"status": "draft", "kickback_bhd": "2"},
                         {"status": "paid", "kickback_bhd": "3"}]) == {"draft": {"count": 2, "kickback_bhd": "3.001"},
                                                                       "paid": {"count": 1, "kickback_bhd": "3.000"}}
    # the period helpers behind "data to 31 Aug" and "September has not ended"
    assert st.period_end("2026-02").isoformat() == "2026-02-28" and st.period_end("2028-02").isoformat() == "2028-02-29"
    assert st.month_label("2026-09") == "September 2026"
    assert st.cap_data_through("2026-09-24", "2026-08") == "2026-08-31"
    assert st.cap_data_through("2026-09-20", "2026-09") == "2026-09-20" and st.cap_data_through(None, "2026-09") is None
    assert st.cap_data_through("2026-09-24T00:00:00", "2026-09") == "2026-09-24"
    assert st.month_has_ended("2026-08", TODAY) and not st.month_has_ended("2026-09", TODAY) and not st.month_has_ended("2026-10", TODAY)
    assert st.figures({"sales_bhd": "1216.51", "tier_reached": "2", "kickback_bhd": 85.156}) == \
        st.figures({"sales_bhd": 1216.510, "tier_reached": 2, "kickback_bhd": "85.1560"})
    assert st.figures({"sales_bhd": "1216.51", "tier_reached": 2, "kickback_bhd": "85.156"}) != \
        st.figures({"sales_bhd": "1216.51", "tier_reached": 2, "kickback_bhd": "85.157"})
    assert issubclass(st.StatementConflict, st.StatementError)


# ── statements: a fake PostgREST client ──────────────────────────────────────

class _FakeTable:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.op, self.payload, self.filters, self.lim, self.rng = "select", None, [], None, None
        self.orders: list[tuple[str, bool]] = []

    def select(self, *_a, **_k):
        self.op = "select"
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self.filters.append(("in", col, list(vals)))
        return self

    def is_(self, col, val):
        self.filters.append(("is", col, val))
        return self

    def order(self, col, desc=False, **_k):
        self.orders.append((col, bool(desc)))
        return self

    def limit(self, n):
        self.lim = n
        return self

    def range(self, start, end):
        self.rng = (start, end)
        return self

    def _match(self, row):
        for kind, col, val in self.filters:
            if kind == "eq" and str(row.get(col)) != str(val):
                return False
            if kind == "in" and str(row.get(col)) not in {str(v) for v in val}:
                return False
            if kind == "is" and val == "null" and row.get(col) is not None:
                return False
        return True

    def execute(self):
        rows = self.db.tables.setdefault(self.name, [])
        if self.op == "insert":
            if self.db.fail_insert:
                raise self.db.fail_insert
            if self.db.missing_cols and any(k in self.db.missing_cols for k in self.payload):
                raise _PGRST204(next(k for k in self.payload if k in self.db.missing_cols))
            row = {**self.payload, "id": len(rows) + 1}
            rows.append(row)
            self.db.log.append(("insert", self.name, dict(self.payload)))
            return type("R", (), {"data": [dict(row)]})()
        if self.op == "update":
            if self.db.fail_update:
                raise self.db.fail_update
            if self.db.missing_cols and any(k in self.db.missing_cols for k in self.payload):
                raise _PGRST204(next(k for k in self.payload if k in self.db.missing_cols))
            hit = [r for r in rows if self._match(r)]
            for r in hit:
                r.update(self.payload)
            self.db.log.append(("update", self.name, dict(self.payload), list(self.filters)))
            return type("R", (), {"data": [dict(r) for r in hit]})()
        out = [dict(r) for r in rows if self._match(r)]
        for col, desc in reversed(self.orders):          # PostgREST applies order=a,b: a is the primary key
            out.sort(key=lambda r: str(r.get(col) if r.get(col) is not None else ""), reverse=desc)
        if self.rng:
            out = out[self.rng[0]:self.rng[1] + 1]
        if self.lim:
            out = out[:self.lim]
        self.db.log.append(("select", self.name, list(self.filters), list(self.orders), self.rng))
        return type("R", (), {"data": out})()


class _PGRST204(Exception):
    code = "PGRST204"

    def __init__(self, col):
        super().__init__(f"Could not find the '{col}' column of 'salesman_kickback_statements' in the schema cache")


class _Unique(Exception):
    """What supabase-py raises when a partial unique index refuses a write."""
    code = "23505"

    def __init__(self, idx="salesman_kickback_statements_one_closed_idx"):
        super().__init__(f'duplicate key value violates unique constraint "{idx}"')


class _FakeDB:
    def __init__(self, rows=(), missing_cols=()):
        self.tables = {"salesman_kickback_statements": [dict(r) for r in rows]}
        self.missing_cols = set(missing_cols)
        self.fail_insert: Exception | None = None
        self.fail_update: Exception | None = None
        self.log: list = []

    def table(self, name):
        return _FakeTable(self, name)


def _stmt(id, status="draft", salesman="Ahmed Aradi", period="2026-09", data_through=DT, **kw):
    return {"id": id, "salesman": salesman, "salesman_id": 7, "period": period, "basis": "net_ex_vat", "status": status,
            "data_through": data_through, "sales_bhd": "1124.880", "returns_bhd": None, "tier_reached": 1, "rate": 0.05,
            "kickback_bhd": "56.244", "target_snapshot": {"team": "mobile_accessories"}, "note": None,
            "created_by": "close_kickback_month", "created_at": "2026-09-24T08:00:00+00:00", **kw}


def _today_patch():
    from app import shop
    return (shop, "bahrain_today", lambda: TODAY)


@test("statements: approve / paid / supersede move forward only, touch no amount, CAS on the status, audited")
def _():
    from app import audit, statements as st
    db = _FakeDB([_stmt(1, period="2026-08", data_through="2026-08-31"),
                  _stmt(2, "approved", period="2026-07", data_through="2026-07-31", approved_by="a@x",
                        approved_at="2026-08-02T00:00:00+00:00"),
                  _stmt(3, "paid", period="2026-06"), _stmt(4, "snapshot", basis="vat_incl_display"), _stmt(5, "superseded")])
    audits: list = []
    with _Patched((st, "get_client", lambda: db), (audit, "log_event", lambda *a, **k: audits.append((a, k))), _today_patch()):
        row = st.transition(1, "approved", by="boss@example.com")
        assert row["status"] == "approved" and row["approved_by"] == "boss@example.com" and row["approved_at"]
        assert row["kickback_bhd"] == "56.244" and row["sales_bhd"] == "1124.880"
        up = [e for e in db.log if e[0] == "update"][-1]
        assert set(up[2]) == {"status", "approved_by", "approved_at"} and ("eq", "status", "draft") in up[3]
        row = st.transition(2, "paid", by="boss@example.com")
        assert row["status"] == "paid" and row["paid_at"] and row["paid_by"] == "boss@example.com"
        for sid, to in ((3, "approved"), (3, "paid"), (3, "superseded"), (4, "approved"), (5, "paid"), (1, "approved")):
            _raises(lambda: st.transition(sid, to, by="boss@example.com", reason="x"), st.StatementError, "cannot become")
        for to in ("draft", "snapshot", "bogus"):
            _raises(lambda: st.transition(1, to, by="b"), st.StatementError)
        _raises(lambda: st.transition(1, "superseded", by="b"), st.StatementError, "reason")
        e = _raises(lambda: st.transition(99, "approved", by="b"), st.StatementError)
        assert str(e) == "Statement not found."
        # CAS: the row moved between the read and the write → a StatementConflict carrying CONFLICT_MSG
        real_get = st.get_statement

        def stale_get(sid):
            r = real_get(sid)
            return {**r, "status": "draft"} if r and sid == 1 else r
        with _Patched((st, "get_statement", stale_get)):
            e = _raises(lambda: st.transition(1, "approved", by="b"), st.StatementConflict)
            assert str(e) == st.CONFLICT_MSG
        events = [a[1] for a, _k in audits]
        assert events == ["kickback.statement_approved", "kickback.statement_paid"], events
        detail = audits[0][1]["detail"]
        assert detail == {"id": 1, "salesman": "Ahmed Aradi", "period": "2026-08", "basis": "net_ex_vat",
                          "from": "draft", "to": "approved", "kickback_bhd": "56.244", "reason": None}
        amounts = {"sales_bhd", "returns_bhd", "kickback_bhd", "rate", "tier_reached", "target_snapshot"}
        for e in db.log:
            if e[0] == "update":
                assert not amounts & set(e[2]), e


@test("statements: a draft frozen mid-month is never approved after the month ends; a paid month says it is final")
def _():
    from app import audit, statements as st
    db = _FakeDB([_stmt(1, "draft", period="2026-08", data_through="2026-08-21"),       # frozen on the 21st
                  _stmt(2, "draft", period="2026-08", salesman="Karrar Mohamed", data_through="2026-08-31"),
                  _stmt(3, "paid", period="2026-07", data_through="2026-07-31"),
                  _stmt(4, "draft", period="2026-07", data_through="2026-07-31", sales_bhd="1300.000")])
    with _Patched((st, "get_client", lambda: db), (audit, "log_event", lambda *a, **k: None), _today_patch()):
        e = _raises(lambda: st.transition(1, "approved", by="boss@example.com"), st.StatementError)
        assert "only has sales data to 2026-08-21" in str(e) and "2026-08-31" in str(e), str(e)
        assert next(r for r in db.tables["salesman_kickback_statements"] if r["id"] == 1)["status"] == "draft"
        assert st.transition(2, "approved", by="boss@example.com")["status"] == "approved"
        e = _raises(lambda: st.transition(4, "approved", by="boss@example.com"), st.StatementConflict)
        assert "PAID" in str(e) and "final" in str(e) and "Supersede" not in str(e), str(e)


@test("statements: one approved/paid row per rep-month-basis — a second approval is refused and names the row; the running month cannot be approved")
def _():
    from app import audit, statements as st
    db = _FakeDB([_stmt(1, "approved", period="2026-08", data_through="2026-08-31", kickback_bhd="51.250"),
                  _stmt(2, "draft", period="2026-08", data_through="2026-08-31", sales_bhd="1300.000", kickback_bhd="91.000"),
                  _stmt(3, "draft", period="2026-09"),                                            # the running month
                  _stmt(4, "draft", period="2026-08", salesman="Karrar Mohamed"),                 # another rep: free
                  _stmt(5, "draft", period="2026-08", basis="vat_incl_display"),                  # another basis: free
                  _stmt(6, "paid", period="2026-07"), _stmt(7, "draft", period="2026-07"),
                  _stmt(8, "draft", period="2026-10")])
    audits: list = []
    with _Patched((st, "get_client", lambda: db), (audit, "log_event", lambda *a, **k: audits.append((a, k))), _today_patch()):
        e = _raises(lambda: st.transition(2, "approved", by="boss@example.com"), st.StatementConflict)
        assert "#1" in str(e) and "approved" in str(e) and "51.250" in str(e) and "Supersede #1" in str(e), str(e)
        e = _raises(lambda: st.transition(7, "approved", by="boss@example.com"), st.StatementConflict)
        assert "#6" in str(e) and "paid" in str(e)
        e = _raises(lambda: st.transition(3, "approved", by="boss@example.com"), st.StatementError)
        assert "September 2026 has not ended" in str(e) and not isinstance(e, st.StatementConflict)
        _raises(lambda: st.transition(8, "approved", by="boss@example.com"), st.StatementError, "October 2026 has not ended")
        assert st.transition(4, "approved", by="boss@example.com")["status"] == "approved"
        assert st.transition(5, "approved", by="boss@example.com")["status"] == "approved"
        assert not audits[:0] and [a[1] for a, _k in audits] == ["kickback.statement_approved"] * 2, "refusals are not audited moves"
        rows = {r["id"]: r for r in db.tables["salesman_kickback_statements"]}
        assert rows[2]["status"] == "draft" and rows[3]["status"] == "draft" and rows[7]["status"] == "draft"
        # supersede #1, and the same draft approves
        assert st.transition(1, "superseded", by="boss@example.com", reason="August recomputed after the returns load")["status"] == "superseded"
        assert st.transition(2, "approved", by="boss@example.com")["status"] == "approved"
        # the database backstop under a race: 23505 from the partial index is a StatementConflict, not a 500
        db.fail_update = _Unique()
        e = _raises(lambda: st.transition(3, "superseded", by="boss@example.com", reason="x"), st.StatementConflict)
        assert "a moment ago" in str(e)
        db.fail_update = None


@test("statements: the R3 columns are optional — a write naming them is retried without them until the migration runs")
def _():
    from app import audit, statements as st
    db = _FakeDB([_stmt(1, "approved")], missing_cols={"paid_by", "superseded_at", "superseded_by", "superseded_reason",
                                                       "superseded_by_id"})
    with _Patched((st, "get_client", lambda: db), (audit, "log_event", lambda *a, **k: None), _today_patch()):
        row = st.transition(1, "paid", by="boss@example.com")
        assert row["status"] == "paid" and row["paid_at"] and "paid_by" not in row
        ups = [e for e in db.log if e[0] == "update"]
        assert len(ups) == 1 and set(ups[0][2]) == {"status", "paid_at"}


FRESH = [{"salesman": "Ahmed Aradi", "salesman_id": 7, "period": "2026-09", "basis": "net_ex_vat", "data_through": DT,
          "sales_bhd": "1124.880", "returns_bhd": None, "tier_reached": 1, "rate": 0.05, "kickback_bhd": "56.244",
          "target_snapshot": {"team": "mobile_accessories"}},
         {"salesman": "Karrar Mohamed", "salesman_id": 9, "period": "2026-09", "basis": "net_ex_vat", "data_through": DT,
          "sales_bhd": "1216.510", "returns_bhd": None, "tier_reached": 2, "rate": 0.07, "kickback_bhd": "85.156",
          "target_snapshot": {"team": "mobile_accessories"}},
         {"salesman": "Faisal Kandikkal", "salesman_id": 4, "period": "2026-09", "basis": "net_ex_vat", "data_through": DT,
          "sales_bhd": "362.430", "returns_bhd": None, "tier_reached": 3, "rate": 0.08, "kickback_bhd": "28.994",
          "target_snapshot": {"team": "normal"}}]


@test("statements: create_draft freezes the card's rows once — same figures skipped (whatever the data date), stale drafts retired and linked, approved rows untouched but named")
def _():
    from app import audit, statements as st
    db = _FakeDB([_stmt(1, "draft", salesman="Ahmed Aradi", data_through="2026-09-21"),         # same figures, older data → skipped
                  _stmt(2, "draft", salesman="Karrar Mohamed", data_through="2026-09-21"),      # different figures → superseded
                  _stmt(3, "approved", salesman="Faisal Kandikkal", data_through="2026-09-21"),  # closed, differs → noted, untouched
                  _stmt(4, "snapshot", salesman="Karrar Mohamed", basis="vat_incl_display", data_through="2026-09-21")])
    audits: list = []
    with _Patched((st, "get_client", lambda: db), (st, "build_rows", lambda period, basis=None: FRESH),
                  (audit, "log_event", lambda *a, **k: audits.append((a, k)))):
        out = st.create_draft("2026-09", by="boss@example.com", note="September close")
    assert [c["salesman"] for c in out["created"]] == ["Karrar Mohamed", "Faisal Kandikkal"], out["created"]
    assert out["skipped"] == [{"salesman": "Ahmed Aradi", "id": 1,
                               "reason": "#1 (draft, data to 2026-09-21) already carries these figures"}], out["skipped"]
    assert out["superseded"] == [{"salesman": "Karrar Mohamed", "id": 2, "by": 5}]
    assert out["total_kickback_bhd"] == "114.150"
    assert out["created"][0]["note"] is None
    assert out["created"][1]["note"] == "#3 is approved with different figures — approval of this draft is refused until #3 is superseded"
    rows = {r["id"]: r for r in db.tables["salesman_kickback_statements"]}
    assert rows[2]["status"] == "superseded" and rows[2]["superseded_by_id"] == 5 and rows[2]["superseded_by"] == "boss@example.com"
    assert rows[2]["superseded_reason"] == "replaced by a newer draft (data to 2026-09-24)"
    assert rows[3]["status"] == "approved" and rows[4]["status"] == "snapshot" and rows[1]["status"] == "draft"
    assert rows[5]["status"] == "draft" and rows[5]["created_by"] == "boss@example.com" and rows[5]["note"] == "September close"
    assert rows[5]["kickback_bhd"] == "85.156" and rows[5]["sales_bhd"] == "1216.510" and rows[5]["returns_bhd"] is None
    assert len(rows) == 6 and not any(e[0] == "delete" for e in db.log)
    # the stale draft is retired BEFORE the new one is inserted (one chain row per data date), then linked
    seq = [(e[0], e[2].get("status") or e[2].get("superseded_by_id")) for e in db.log if e[0] in ("insert", "update")]
    assert seq == [("update", "superseded"), ("insert", "draft"), ("update", 5), ("insert", "draft")], seq
    link = [e for e in db.log if e[0] == "update" and set(e[2]) == {"superseded_by_id"}][0]
    assert ("eq", "status", "superseded") in link[3] and ("is", "superseded_by_id", "null") in link[3]
    assert audits[-1][0][1] == "kickback.statement" and audits[-1][1]["detail"]["reps"] == 2
    assert audits[-1][1]["detail"]["superseded"] == 1 and audits[-1][1]["detail"]["total_kickback_bhd"] == "114.150"


@test("statements: create_draft on a later data day with nothing changed creates nothing; a skip still retires stale drafts; a same-day approved row with new figures is a skip that says 'supersede first'")
def _():
    from app import audit, statements as st
    # 1) August already approved with today's figures, an old stale draft next to it, a later data day
    db = _FakeDB([_stmt(1, "approved", salesman="Ahmed Aradi", data_through="2026-09-20"),
                  _stmt(2, "draft", salesman="Ahmed Aradi", data_through="2026-09-19", sales_bhd="900.000", kickback_bhd="45.000"),
                  _stmt(3, "paid", salesman="Karrar Mohamed", data_through="2026-09-18", sales_bhd="1216.510", tier_reached=2,
                        rate=0.07, kickback_bhd="85.156"),
                  _stmt(4, "draft", salesman="Faisal Kandikkal", data_through=DT)])                 # same day, different figures
    audits: list = []
    with _Patched((st, "get_client", lambda: db), (st, "build_rows", lambda period, basis=None: FRESH),
                  (audit, "log_event", lambda *a, **k: audits.append((a, k)))):
        out = st.create_draft("2026-09", by="boss@example.com")
    assert [c["salesman"] for c in out["created"]] == ["Faisal Kandikkal"], out
    assert [(s["salesman"], s["id"]) for s in out["skipped"]] == [("Ahmed Aradi", 1), ("Karrar Mohamed", 3)]
    assert out["skipped"][0]["reason"] == "#1 (approved, data to 2026-09-20) already carries these figures"
    assert out["superseded"] == [{"salesman": "Ahmed Aradi", "id": 2, "by": 1}, {"salesman": "Faisal Kandikkal", "id": 4, "by": 5}]
    rows = {r["id"]: r for r in db.tables["salesman_kickback_statements"]}
    assert rows[2]["status"] == "superseded" and rows[2]["superseded_by_id"] == 1 and "#1 already carries them" in rows[2]["superseded_reason"]
    assert rows[4]["status"] == "superseded" and rows[4]["superseded_by_id"] == 5 and rows[5]["status"] == "draft"
    assert rows[1]["status"] == "approved" and rows[3]["status"] == "paid" and len(rows) == 5
    assert audits[-1][1]["detail"] == {"period": "2026-09", "basis": "net_ex_vat", "status": "draft", "reps": 1, "skipped": 2,
                                       "superseded": 2, "total_kickback_bhd": "28.994", "note": None}
    # 2) an approved row on the SAME data date with different figures: the draft cannot be written next to it
    db = _FakeDB([_stmt(1, "approved", salesman="Ahmed Aradi", data_through=DT, sales_bhd="900.000", kickback_bhd="45.000")])
    with _Patched((st, "get_client", lambda: db), (st, "build_rows", lambda period, basis=None: FRESH[:1]),
                  (audit, "log_event", lambda *a, **k: None)):
        out = st.create_draft("2026-09", by="boss@example.com")
    assert out["created"] == [] and out["skipped"][0]["reason"] == \
        "#1 is approved on the same data date with different figures — supersede #1 first, then create the draft again"
    assert len(db.tables["salesman_kickback_statements"]) == 1
    # 3) the database refuses a concurrent duplicate (23505) → a StatementConflict (409), never a 500
    db = _FakeDB()
    db.fail_insert = _Unique("salesman_kickback_statements_one_per_data_date_idx")
    with _Patched((st, "get_client", lambda: db), (st, "build_rows", lambda period, basis=None: FRESH[:1]),
                  (audit, "log_event", lambda *a, **k: None)):
        e = _raises(lambda: st.create_draft("2026-09", by="boss@example.com"), st.StatementConflict)
    assert "Ahmed Aradi" in str(e) and "a moment ago" in str(e)


@test("statements: build_rows caps data_through at the period's last day and refuses a month with no sales loaded")
def _():
    from app import shop, statements as st
    db = _FakeDB()
    db.tables["salesman_targets"] = [{"salesman": "Ahmed Aradi", "period": ""}, {"salesman": "Ahmed Aradi", "period": "2026-08"}]
    db.tables["salesmen"] = [{"id": 7, "focus_name": "Ahmed Aradi"}]
    tgt = {"salesman": "Ahmed Aradi", "period": "", "team": "mobile_accessories", "target_bhd": 500, "tier2_bhd": 1200,
           "tier3_bhd": 2000, "kickback_t1": 0.05, "kickback_t2": 0.07, "kickback_t3": 0.08}
    asked: list = []

    def fake_sales(name, basis=None, period=None):
        asked.append((name, basis, period))
        return 1216.51, DT                    # MAX(sale_date) is global: a September date even for August
    with _Patched((st, "get_client", lambda: db), (shop, "rep_month_sales", fake_sales),
                  (shop, "rep_target", lambda name, period: tgt)):
        aug = st.build_rows("2026-08")
        assert len(aug) == 1 and aug[0]["data_through"] == "2026-08-31", aug
        assert aug[0]["sales_bhd"] == "1216.510" and aug[0]["tier_reached"] == 2 and aug[0]["kickback_bhd"] == "85.156"
        assert aug[0]["salesman_id"] == 7 and aug[0]["basis"] == "net_ex_vat" and aug[0]["returns_bhd"] is None
        assert asked[-1] == ("Ahmed Aradi", "net_ex_vat", "2026-08")
        sep = st.build_rows("2026-09")
        assert sep[0]["data_through"] == DT, "the running month carries the latest loaded day"
        e = _raises(lambda: st.build_rows("2026-10"), st.StatementError)
        assert "October 2026" in str(e) and DT in str(e) and not isinstance(e, st.StatementConflict)
        _raises(lambda: st.build_rows("2026-09", basis="gross"), st.StatementError, "basis")


@test("statements: /shop/me carries last_closed (latest approved/paid) and the open draft (flagged while its month runs); nothing for an unlinked login")
def _():
    from app import statements as st
    db = _FakeDB([_stmt(1, "paid", period="2026-07", kickback_bhd="40.000"),
                  _stmt(2, "approved", period="2026-08", kickback_bhd="51.250", approved_at="2026-09-03T00:00:00+00:00"),
                  _stmt(3, "draft", period="2026-09"), _stmt(4, "superseded", period="2026-09"),
                  _stmt(5, "approved", period="2026-08", salesman="Somebody Else", kickback_bhd="999")])
    with _Patched((st, "get_client", lambda: db), _today_patch()):
        out = st.rep_summary({"focus_name": "Ahmed Aradi"})
        assert out["last_closed"]["period"] == "2026-08" and out["last_closed"]["kickback_bhd"] == "51.250"
        assert out["last_closed"]["status"] == "approved" and out["last_closed"]["approved_at"]
        assert out["draft"]["period"] == "2026-09" and out["draft"]["id"] == 3 and out["draft"]["in_progress"] is True
        assert set(out["last_closed"]) <= {"id", "period", "status", "basis", "data_through", "sales_bhd", "returns_bhd",
                                           "tier_reached", "rate", "kickback_bhd", "approved_at", "paid_at", "created_at"}
        assert st.rep_summary(None) == {"last_closed": None, "draft": None}
        assert st.rep_summary({"focus_name": ""}) == {"last_closed": None, "draft": None}
        db.tables["salesman_kickback_statements"][2]["period"] = "2026-08"
        assert st.rep_summary({"focus_name": "Ahmed Aradi"})["draft"]["in_progress"] is False
    listing = None
    with _Patched((st, "get_client", lambda: db)):
        listing = st.list_statements(period="2026-08")
    assert listing["available"] and {r["id"] for r in listing["statements"]} == {2, 3, 5}
    assert listing["summary"] == {"approved": {"count": 2, "kickback_bhd": "1050.250"}, "draft": {"count": 1, "kickback_bhd": "56.244"}}


@test("statements: the API edge maps StatementConflict to 409, a missing row to 404, any other refusal to 400")
def _():
    from fastapi.testclient import TestClient
    from app import auth, statements as st
    import app.main as m

    def raiser(exc):
        def _t(*a, **k):
            raise exc
        return _t
    c = TestClient(m.app)
    m.app.dependency_overrides[auth.get_current_user] = lambda: auth.CurrentUser(
        user_id="u", email="boss@example.com", role="admin", status="active", must_reset=False)
    try:
        for exc, code in ((st.StatementConflict("Ahmed · August 2026 already has an approved statement (#1)"), 409),
                          (st.StatementConflict(st.CONFLICT_MSG), 409), (st.StatementError(st.CONFLICT_MSG), 409),
                          (st.StatementError("Statement not found."), 404),
                          (st.StatementError("September 2026 has not ended yet"), 400)):
            with _Patched((st, "transition", raiser(exc))):
                r = c.post("/shop/statements/1/approve")
                assert r.status_code == code and r.json()["detail"] == str(exc), (r.status_code, r.text)
        with _Patched((st, "create_draft", raiser(st.StatementConflict("dup")))):
            assert c.post("/shop/statements/draft", json={"period": "2026-08"}).status_code == 409
        with _Patched((st, "build_rows", raiser(st.StatementError("No sales are loaded for October 2026 yet")))):
            assert c.get("/shop/statements/preview?period=2026-10").status_code == 400
    finally:
        m.app.dependency_overrides.clear()


# ── close_kickback_month.py --commit (a fake psycopg connection) ─────────────

class _FakeCursor:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.log.append((" ".join(str(sql).split()), params))

    def fetchone(self):
        return None


class _FakeConn:
    def __init__(self, log):
        self.log, self.exits = log, []

    def __enter__(self):
        return self

    def __exit__(self, et, *a):
        self.exits.append(et)
        return False

    def transaction(self):
        return self

    def cursor(self):
        return _FakeCursor(self.log)


def _psycopg_module():
    """The real psycopg when installed (it is not in requirements.lock, so CI may lack it);
    otherwise a stub the CLI's `import psycopg` resolves to, so `connect` can still be faked."""
    try:
        import psycopg
        return psycopg
    except ImportError:
        import types
        stub = types.ModuleType("psycopg")
        stub.connect = None
        sys.modules["psycopg"] = stub
        return stub


@test("close_kickback_month --commit: inserts every row plus a JSON-safe audit row (Decimal total as a 3-dp string) in one transaction; dry run never connects")
def _():
    psycopg = _psycopg_module()
    from app import statements as st
    import scripts.close_kickback_month as cli
    log: list = []
    conns: list = []

    def fake_connect(url, **kw):
        conns.append((url, kw))
        c = _FakeConn(log)
        return c
    old_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = "postgresql://fake/none"
    try:
        with _Patched((st, "build_rows", lambda period, basis=None: FRESH), (psycopg, "connect", fake_connect)):
            assert cli.main(["--period", "2026-09"]) == 0 and conns == [], "a dry run never opens a connection"
            assert cli.main(["--period", "2026-09", "--commit", "--note", "September close"]) == 0
    finally:
        if old_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old_url
    assert len(conns) == 1 and conns[0][0] == "postgresql://fake/none"
    inserts = [p for s, p in log if s.startswith("insert into salesman_kickback_statements")]
    assert len(inserts) == 3 and [p[0] for p in inserts] == ["Ahmed Aradi", "Karrar Mohamed", "Faisal Kandikkal"]
    assert inserts[1][6] == "1216.510" and inserts[1][10] == "85.156" and inserts[1][4] == "draft"
    checks = [p for s, p in log if s.startswith("select id, status from salesman_kickback_statements")]
    assert len(checks) == 3 and checks[0][3] == ["draft", "approved", "paid"], "a draft shares its data date with no chain row"
    audit = [p for s, p in log if s.startswith("insert into audit_log")]
    assert len(audit) == 1 and audit[0][1] == "kickback.statement"
    detail = json.loads(audit[0][2])                     # this line raised TypeError before the fix
    assert detail == {"period": "2026-09", "basis": "net_ex_vat", "status": "draft", "reps": 3,
                      "total_kickback_bhd": "170.394", "note": "September close"}
    assert isinstance(detail["total_kickback_bhd"], str) and Decimal(detail["total_kickback_bhd"]) == Decimal("170.394")
    assert [s for s, _p in log][0] == "set local lock_timeout = '2s'"


# ── attainment ────────────────────────────────────────────────────────────────

@test("attainment: one SQL (Accessories, no giveaways, net_bhd, `name - %` match) → tier_progress per rep; no-target reps flagged")
def _():
    from app import reports
    sql = reports.ATTAINMENT_SQL
    assert "division = 'Accessories'" in sql and "NOT v.is_giveaway" in sql and "SUM(v.net_bhd)" in sql
    assert "s.rep LIKE tg.salesman || ' - %'" in sql and "t.period IN ('', per.period)" in sql and "FULL OUTER JOIN" in sql
    tgt = {"target_period": "", "team": "mobile_accessories", "target_bhd": 500, "tier2_bhd": 1200, "tier3_bhd": 2000,
           "kickback_t1": 0.05, "kickback_t2": 0.07, "kickback_t3": 0.08, "period": "2026-09", "data_through": DT}
    rows = [
        {"salesman": "Ishaq", **tgt, "target_bhd": 100, "tier2_bhd": 200, "tier3_bhd": 300, "team": "normal",
         "net_bhd": "94.180", "gross_bhd": "103.600", "invoices": 4, "shops": 0, "last_sale": DT, "no_target": False},
        {"salesman": "Karrar Mohamed", **tgt, "net_bhd": "1216.510", "gross_bhd": "1338.190", "invoices": 23, "shops": 7,
         "last_sale": "2026-09-23", "no_target": False},
        {"salesman": "Causeway", "target_period": None, "team": None, "target_bhd": None, "tier2_bhd": None, "tier3_bhd": None,
         "kickback_t1": None, "kickback_t2": None, "kickback_t3": None, "net_bhd": "84.560", "gross_bhd": "93.000",
         "invoices": 1, "shops": 0, "last_sale": "2026-09-01", "no_target": True, "period": "2026-09", "data_through": DT},
        {"salesman": "Moideen KP", **tgt, "target_period": "2026-09", "net_bhd": 0, "gross_bhd": 0, "invoices": 0, "shops": 0,
         "last_sale": None, "no_target": False},
    ]
    salesmen = [{"id": 9, "name": "Karrar M.", "focus_name": "Karrar Mohamed", "is_active": True, "referral_code": "karrar"}]
    out = reports.attainment_rows(rows, salesmen, today=date(2026, 9, 24))
    assert [r["salesman"] for r in out] == ["Karrar Mohamed", "Ishaq", "Causeway", "Moideen KP"]
    k = out[0]
    assert k["salesman_id"] == 9 and k["name"] == "Karrar M." and k["referral_code"] == "karrar" and k["team"] == "mobile_accessories"
    assert k["tier_reached"] == 2 and k["kickback_bhd"] == 85.156 and k["kickback_pct"] == 0.07 and k["next_tier"]["n"] == 3
    assert k["net_bhd"] == 1216.51 and k["basis"] == "net_ex_vat" and k["days_left"] == 6 and k["target_period"] == ""
    i = out[1]
    assert i["tier_reached"] == 0 and i["kickback_bhd"] == 0.0 and i["next_tier"]["gap_bhd"] == 5.82
    c = out[2]
    assert c["no_target"] is True and c["tier"] is None and c["tier_reached"] is None and c["name"] == "Causeway"
    m = out[3]
    assert m["target_period"] == "2026-09" and m["tier"]["mtd_bhd"] == 0.0 and m["progress_pct"] == 0.0
    top = reports.top_salesmen_from_attainment(out)
    assert [t["salesman"] for t in top] == ["Karrar Mohamed", "Ishaq", "Causeway"], "zero months drop out of the widget"
    assert top[0] == {"salesman": "Karrar Mohamed", "orders": 23, "qty": None, "revenue_bhd": 1338.19, "net_bhd": 1216.51,
                      "no_target": False}
    assert top[2]["no_target"] is True
    # the SQL failing is an error flag, not an empty month
    with _Patched((reports, "exec_sql", lambda sql: (_ for _ in ()).throw(RuntimeError("boom")))):
        res = reports.salesman_attainment_result()
    assert res["rows"] == [] and res["error"] == reports.ATTAINMENT_ERROR
    assert reports.salesman_attainment() == []


def _keys(obj, out: set):
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(str(k))
            _keys(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _keys(v, out)
    return out


@test("attainment + dashboard: by_salesman (Accessories, current month) plus its scope; no kickback, tier or referral code leaves with the Dashboard payload")
def _():
    from app import reports
    att = [{"salesman": "Karrar Mohamed", "salesman_id": 9, "referral_code": "karrar", "invoices": 23, "gross_bhd": 1338.19,
            "net_bhd": 1216.51, "no_target": False, "tier_reached": 2, "kickback_bhd": 85.156, "kickback_pct": 0.07,
            "progress_pct": 60.8, "tier": {"kickback_bhd": 85.156}, "period": "2026-09", "data_through": DT}]
    s = {"rev_today": 0, "net_today": 0, "orders_today": 0, "rev_yesterday": 0, "orders_yesterday": 0, "rev_mtd": 0,
         "net_mtd": 0, "orders_mtd": 0, "rev_prev_month": 0, "total_receivables": 0, "overdue_accounts": 0,
         "overdue_receivables_bhd": 0, "current_receivables_bhd": 0, "top_customers": [], "data_date": DT}
    a = {"low_stock_count": 0, "negative_margin_count": 0}
    base = {"s": s, "a": a, "health": {}, "movers": {}, "trend": [], "channel": [], "agents": [],
            "fresh": {"stale": False, "days_behind": 0}, "daily_mtd": [], "split": {"by_payment": [], "by_division": []}}
    out = reports._assemble_dashboard({**base, "attainment": {"rows": att, "error": None}})
    assert out["by_salesman"] == [{"salesman": "Karrar Mohamed", "orders": 23, "qty": None, "revenue_bhd": 1338.19,
                                   "net_bhd": 1216.51, "no_target": False}]
    assert out["by_salesman_scope"] == {"division": "Accessories", "basis": "net_ex_vat", "period": "2026-09",
                                        "data_through": DT, "error": None}
    assert out["attainment"] == [], "the per-rep rows stay behind Shop Admin (/shop/attainment)"
    keys = _keys(out, set())
    for secret in ("kickback_bhd", "kickback_pct", "referral_code", "salesman_id", "tier_reached", "progress_pct"):
        assert secret not in keys, f"{secret} must not travel with the Dashboard payload"
    err = reports._assemble_dashboard({**base, "attainment": {"rows": [], "error": reports.ATTAINMENT_ERROR}})
    assert err["by_salesman"] == [] and err["by_salesman_scope"]["error"] == reports.ATTAINMENT_ERROR
    legacy = reports._assemble_dashboard({**base, "attainment": att})        # a bare list is tolerated
    assert legacy["by_salesman"][0]["salesman"] == "Karrar Mohamed" and legacy["attainment"] == []


# ── routes: the gate table for everything R3a added ──────────────────────────

@test("gates: statements are admin-only, attainment is Shop Admin, the rep cards are Shop Orders")
def _():
    from fastapi.routing import APIRoute
    import app.main as m
    from app import auth
    want = {
        ("GET", "/shop/statements"): "admin", ("GET", "/shop/statements/preview"): "admin",
        ("POST", "/shop/statements/draft"): "admin", ("POST", "/shop/statements/{statement_id}/approve"): "admin",
        ("POST", "/shop/statements/{statement_id}/paid"): "admin",
        ("POST", "/shop/statements/{statement_id}/supersede"): "admin",
        ("GET", "/shop/attainment"): "feature:Shop Admin",
        ("GET", "/shop/me"): "feature:Shop Orders", ("GET", "/shop/me/followups"): "feature:Shop Orders",
        ("POST", "/shop/me/followups/tap"): "feature:Shop Orders", ("GET", "/shop/me/baskets"): "feature:Shop Orders",
        ("GET", "/shop/me/link-week"): "feature:Shop Orders",
    }
    table = {}
    for r in m.app.routes:
        if isinstance(r, APIRoute):
            for meth in r.methods:
                gates = []
                for dep in r.dependant.dependencies:
                    c = dep.call
                    gates.append("admin" if c is auth.require_admin else f"feature:{c.feature}" if getattr(c, "feature", None) else "?")
                table[(meth, r.path)] = gates
    for k, g in want.items():
        assert table.get(k) == [g], (k, table.get(k))


# ── local Postgres replay (SKIP when the scratch cluster is off) ─────────────

LOCAL_DSN = os.getenv("YQ_SCRATCH_PG", "postgresql://postgres@localhost:55432/postgres")


def _local_conn(dbname: str):
    try:
        import psycopg
        admin = psycopg.connect(LOCAL_DSN, connect_timeout=3, autocommit=True)
    except Exception:  # noqa: BLE001 — no driver or no cluster: the caller prints SKIP
        return None
    with admin:
        n = admin.execute("select 1 from pg_database where datname = %s", (dbname,)).fetchone()
        if not n:
            admin.execute(f'create database "{dbname}"')
    return psycopg.connect(LOCAL_DSN.rsplit("/", 1)[0] + f"/{dbname}", connect_timeout=3)


def _v_sales_ddl() -> str:
    src = (ROOT / "scripts" / "division_payment_migration.sql").read_text(encoding="utf-8")
    src = re.sub(r"--[^\n]*", "", src)            # a comment inside the body carries a ';'
    m = re.search(r"create or replace view v_sales as.*?;", src, re.S | re.I)
    assert m, "the canonical v_sales definition must be in division_payment_migration.sql"
    return m.group(0)


_SCHEMA = """
drop view if exists v_sales;
drop table if exists order_lines, orders, product_aliases, products, categories, salesman_channels, salesman_targets;
create table categories (id bigint primary key, name text, division text);
create table products (id bigint primary key, sku_code text, item_name text, category_id bigint);
create table product_aliases (alias_text text, product_id bigint);
create table salesman_channels (salesman text, channel text);
create table orders (invoice_no text primary key, order_date date, customer_name text, salesman text,
                     payment_mode text, sales_account_name text);
create table order_lines (id bigint generated always as identity primary key, invoice_no text, line_no int,
  line_date date, customer_account text, item_name text, quantity numeric, rate_bhd numeric, gross_bhd numeric,
  discount_bhd numeric, taxable_bhd numeric, vat_amount_bhd numeric, total_amount_bhd numeric,
  warehouse_name text, narration text);
create table salesman_targets (salesman text, period text not null default '', team text, target_bhd numeric,
  tier2_bhd numeric, tier3_bhd numeric, kickback_t1 numeric, kickback_t2 numeric, kickback_t3 numeric,
  primary key (salesman, period));
"""


@test("local Postgres: the follow-up and attainment SQL replayed on synthetic Focus rows (owner by majority, suffix match, cadence)")
def _():
    from datetime import timedelta
    from app import followups, reports
    conn = _local_conn("r3_reps")
    if conn is None:
        print("SKIP (scratch Postgres on 55432 not reachable)")
        return
    A, F = "Ahmed Aradi - Acc WH", "Faisal Kandikkal - Acc WH"
    mx = date(2026, 9, 24)
    with conn.transaction(), conn.cursor() as cur:      # psycopg3: `with conn:` would CLOSE it at the end
        cur.execute(_SCHEMA)
        cur.execute("insert into categories values (1, 'CABLES', 'Accessories'), (2, 'SIM CARDS', 'SIM'), (3, 'GIFT', 'Giveaway')")
        cur.execute("insert into products values (1, 'T02', 'T02 cable', 1), (2, 'SIM1', 'Batelco SIM', 2), (3, 'G1', 'Gift', 3)")
        cur.execute("insert into product_aliases values ('T02 CABLE 1M', 1), ('BATELCO SIM', 2), ('GIFT BAG', 3)")
        cur.execute("insert into salesman_channels values ('Causeway', 'B2C')")
        cur.execute("""insert into salesman_targets values
            ('Ahmed Aradi', '', 'mobile_accessories', 500, 1200, 2000, 0.05, 0.07, 0.08),
            ('Ahmed Aradi', '2026-09', 'mobile_accessories', 400, 1200, 2000, 0.05, 0.07, 0.08),
            ('Faisal Kandikkal', '', 'normal', 100, 200, 300, 0.05, 0.07, 0.08),
            ('Quiet Rep', '', 'normal', 100, 200, 300, 0.05, 0.07, 0.08)""")
        n = 0

        def sale(shop, rep, d, taxable, item="T02 CABLE 1M", qty=5, cash=False):
            nonlocal n
            n += 1
            inv = f"SI-{n:04d}"
            cur.execute("insert into orders values (%s, %s, %s, %s, %s, %s)",
                        (inv, d, "Cash Customer" if cash else shop, rep, "Cash" if cash else "Credit", None))
            cur.execute("""insert into order_lines (invoice_no, line_no, line_date, customer_account, item_name, quantity,
                           rate_bhd, gross_bhd, discount_bhd, taxable_bhd, vat_amount_bhd, total_amount_bhd, warehouse_name)
                           values (%s, 1, %s, %s, %s, %s, %s, %s, 0, %s, %s, %s, %s)""",
                        (inv, d, shop, item, qty, taxable / qty if qty else 0, taxable * 1.1, taxable, taxable * 0.1,
                         taxable * 1.1, rep))
        # Shop A: Ahmed 5 visits every 20 days (15-Aug back to 27-May) + one on 2-Sep; Faisal 2 → Ahmed owns it
        for k in range(5):
            sale("Shop A", A, mx - timedelta(days=40 + 20 * k), 100)
        sale("Shop A", A, date(2026, 9, 2), 700)
        sale("Shop A", F, mx - timedelta(days=45), 20)
        sale("Shop A", F, mx - timedelta(days=200), 20)             # inside 365 d, outside 180 d
        # Shop B: Faisal only — 3 visits every 30 days (the last one today, so MAX(sale_date) = 24-Sep) + one on 3-Sep
        for k in range(3):
            sale("Shop B", F, mx - timedelta(days=30 * k), 50)
        sale("Shop B", F, date(2026, 9, 3), 120)
        # Shop C: one visit by Ahmed 200 d ago (lapsed)
        sale("Shop C", A, mx - timedelta(days=200), 30)
        # SIM, giveaway and cash rows must count nowhere (the SIM/gift rows are the newest dates on Shop A)
        sale("Shop A", A, mx - timedelta(days=3), 500, item="BATELCO SIM", qty=100)
        sale("Shop A", A, mx - timedelta(days=3), 0, item="GIFT BAG", qty=10)
        sale("Cash", A, mx - timedelta(days=60), 999, cash=True)
        # an outlet with no target row still shows in the attainment (flagged)
        sale("Outlet", "Causeway", date(2026, 9, 1), 84.56)
        cur.execute(_v_sales_ddl())

    def run(sql, params):
        q = sql.replace("%", "%%")
        for i, _p in enumerate(params, 1):
            q = q.replace(f"${i}", f"%(p{i})s")
        with conn.cursor() as cur:
            cur.execute(q, {f"p{i}": p for i, p in enumerate(params, 1)})
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    try:
        mine = {r["customer_name"]: r for r in followups.load_shops("Ahmed Aradi", run=run)}
        assert set(mine) == {"Shop A", "Shop C"}, set(mine)
        a = mine["Shop A"]
        assert a["rep"] == A and a["rep_visits"] == 6 and a["visits"] == 8, a       # Ahmed 6 dates + Faisal 2
        assert int(a["days_since"]) == 22, a["days_since"]                          # 2-Sep: the SIM/gift day does not count
        assert float(a["median_gap"]) == 20.0, a["median_gap"]
        assert Decimal(str(a["net_180d"])) == Decimal("1220.000"), a["net_180d"]     # 700 + 5x100 + Faisal 20
        c = mine["Shop C"]
        assert c["median_gap"] is None and int(c["days_since"]) == 200 and Decimal(str(c["net_180d"])) == 0
        theirs = {r["customer_name"] for r in followups.load_shops("Faisal Kandikkal", run=run)}
        assert theirs == {"Shop B"}, theirs
        # a name that would have been a LIKE pattern matches nothing; a prefix matches nothing
        assert followups.load_shops("Ahmed%", run=run) == [] and followups.load_shops("Ahmed", run=run) == []
        assert followups.load_shops("_hmed Aradi", run=run) == []
        every = {r["customer_name"]: r["rep"] for r in followups.load_shops(None, run=run, all_reps=True)}
        assert every == {"Shop A": A, "Shop B": F, "Shop C": A, "Outlet": "Causeway"}, every
        ranked = followups.rank_shops(followups.load_shops("Ahmed Aradi", run=run), include_holdout=True)
        assert [r["shop"] for r in ranked["lapsed"]] == ["Shop C"] and ranked["data_through"] == "2026-09-24"
        a_row = ranked["due"][0]
        assert a_row["shop"] == "Shop A" and a_row["overdue_ratio"] == 1.1 and a_row["monthly_value_bhd"] == "203.333"
        att = {r["salesman"]: r for r in run(reports.ATTAINMENT_SQL, [])}
        assert set(att) == {"Ahmed Aradi", "Faisal Kandikkal", "Quiet Rep", "Causeway"}, set(att)
        ah = att["Ahmed Aradi"]
        assert ah["target_period"] == "2026-09" and float(ah["target_bhd"]) == 400, "the month row beats the standing row"
        assert Decimal(str(ah["net_bhd"])) == Decimal("700.000") and int(ah["invoices"]) == 1 and int(ah["shops"]) == 1
        assert Decimal(str(att["Faisal Kandikkal"]["net_bhd"])) == Decimal("170.000") and not att["Faisal Kandikkal"]["no_target"]
        assert att["Causeway"]["no_target"] is True and Decimal(str(att["Causeway"]["net_bhd"])) == Decimal("84.560")
        assert att["Quiet Rep"]["no_target"] is False and Decimal(str(att["Quiet Rep"]["net_bhd"])) == 0
        assert all(r["period"] == "2026-09" and r["data_through"] == "2026-09-24" for r in att.values())
        rows = reports.attainment_rows(list(att.values()), [], today=date(2026, 9, 24))
        assert [r["salesman"] for r in rows] == ["Ahmed Aradi", "Faisal Kandikkal", "Causeway", "Quiet Rep"]
        assert rows[0]["tier_reached"] == 1 and rows[0]["kickback_bhd"] == 35.0 and rows[1]["tier_reached"] == 1
    finally:
        conn.close()


@test("local Postgres: r3_statements_migration.sql replaces the M8 constraint with the two partial indexes; reverse restores it")
def _():
    conn = _local_conn("r3_stmt")
    if conn is None:
        print("SKIP (scratch Postgres on 55432 not reachable)")
        return
    import psycopg
    conn.autocommit = True
    m8 = (ROOT / "scripts" / "kickback_statements_migration.sql").read_text(encoding="utf-8")
    mig = (ROOT / "scripts" / "r3_statements_migration.sql").read_text(encoding="utf-8")
    rev = (ROOT / "scripts" / "r3_statements_reverse.sql").read_text(encoding="utf-8")
    cur = conn.cursor()
    try:
        for role in ("anon", "authenticated"):
            if not cur.execute("select 1 from pg_roles where rolname = %s", (role,)).fetchone():
                cur.execute(f"create role {role} nologin")
        cur.execute("drop table if exists salesman_kickback_statements cascade; drop table if exists salesmen cascade")
        cur.execute("create table salesmen (id bigint generated always as identity primary key, name text, focus_name text)")
        cur.execute(m8)
        cur.execute(mig)
        cur.execute(mig)                                                   # idempotent
        con = cur.execute("select conname from pg_constraint where conrelid = 'salesman_kickback_statements'::regclass and contype = 'u'").fetchall()
        assert con == [], con
        idx = {r[0] for r in cur.execute("select indexname from pg_indexes where tablename = 'salesman_kickback_statements'").fetchall()}
        assert {"salesman_kickback_statements_one_closed_idx", "salesman_kickback_statements_one_per_data_date_idx",
                "salesman_kickback_statements_rep_status_idx"} <= idx, idx

        def ins(status, dt="2026-09-21", salesman="A", basis="net_ex_vat"):
            cur.execute("""insert into salesman_kickback_statements (salesman, period, basis, status, data_through, sales_bhd,
                           tier_reached, rate, kickback_bhd, target_snapshot, created_by)
                           values (%s, '2026-09', %s, %s, %s, 1, 1, 0.05, 0.05, '{}', 't') returning id""", (salesman, basis, status, dt))
            return cur.fetchone()[0]

        def refused(fn, index):
            try:
                fn()
            except psycopg.errors.UniqueViolation as e:
                assert e.diag.constraint_name == index, e.diag.constraint_name
                return
            raise AssertionError(f"expected {index} to refuse")
        ins("superseded"); ins("superseded")                              # the M8 collision, now allowed
        ins("snapshot"); ins("draft")                                      # a snapshot never blocks the draft
        refused(lambda: ins("draft"), "salesman_kickback_statements_one_per_data_date_idx")
        ins("approved", dt="2026-09-22")
        refused(lambda: ins("paid", dt="2026-09-23"), "salesman_kickback_statements_one_closed_idx")
        refused(lambda: ins("approved", dt="2026-09-24"), "salesman_kickback_statements_one_closed_idx")
        ins("approved", dt="2026-09-22", basis="vat_incl_display"); ins("approved", dt="2026-09-22", salesman="B")
        cur.execute(rev)
        con = cur.execute("select conname from pg_constraint where conrelid = 'salesman_kickback_statements'::regclass and contype = 'u'").fetchall()
        assert con == [], "with two superseded rows on one date the M8 constraint cannot come back (a NOTICE says so)"
        cur.execute("delete from salesman_kickback_statements where status = 'superseded'")
        cur.execute(rev)
        con = cur.execute("select conname from pg_constraint where conrelid = 'salesman_kickback_statements'::regclass and contype = 'u'").fetchall()
        assert con == [("salesman_kickback_statements_salesman_period_basis_status_d_key",)], con
        cur.execute(mig)                                                   # forward again
    finally:
        conn.close()


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
