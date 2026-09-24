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
import os
import re
import sys
import traceback
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


# ── fixtures ──────────────────────────────────────────────────────────────────

DT = "2026-09-24"


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

@test("followups: the SQL scopes by the rep's Focus name exactly like rep_month_sales; the request path passes it")
def _():
    from app import followups
    sql = followups.SHOPS_SQL
    assert "$1 = ''" in sql and "o.rep = $1" in sql and "o.rep LIKE $1 || ' - %'" in sql
    assert "division = 'Accessories'" in sql and "NOT v.is_giveaway" in sql and "NOT v.is_cash_customer" in sql
    assert "net_bhd" in sql and "revenue_bhd" not in sql, "ex-VAT value only"
    seen: dict = {}

    def fake_run(q, params):
        seen["sql"], seen["params"] = q, params
        return [_shop("Only Mine", days_since=40, gap=20.0, net_180d="600")]
    assert followups.load_shops("Ahmed Aradi", run=fake_run)[0]["customer_name"] == "Only Mine"
    assert seen["params"] == ["Ahmed Aradi"] and seen["sql"] is sql
    assert followups.load_shops(None, run=fake_run) and seen["params"] == [""], "'' = every rep (admin only)"
    calls: list = []
    with _Patched((followups, "load_shops", lambda name, run=None: calls.append(name) or
                   [_shop("Big Overdue", days_since=40, gap=20.0, net_180d="600")]),
                  (followups, "load_regulars", lambda names: {}),
                  (followups, "load_contacts", lambda names: {"Big Overdue": {"phone": "97333000000"}}),
                  (followups, "_display_names", lambda: {})):
        followups.invalidate()
        out = followups.followups("Ahmed Aradi")
        assert calls == ["Ahmed Aradi"] and out["rep"] == "Ahmed Aradi" and out["due"][0]["phone"] == "97333000000"
        followups.followups("Ahmed Aradi")
        assert calls == ["Ahmed Aradi"], "cached for the TTL"
        followups.followups("Ahmed Aradi", force=True)
        assert calls == ["Ahmed Aradi", "Ahmed Aradi"]
        followups.invalidate()


@test("followups: the route gives a rep his own book only, an admin any rep's (or all), an unlinked login nothing")
def _():
    from fastapi.testclient import TestClient
    from app import auth, followups, shop, user_auth
    import app.main as m

    def user(role, email="rep@example.com"):
        return auth.CurrentUser(user_id="uid-" + email, email=email, role=role, status="active", must_reset=False)

    seen: list = []

    def fake_followups(name, include_holdout=False, force=False):
        seen.append((name, include_holdout))
        return {"rep": name or None, "due": [_shop("S", rep=name) | {"top_skus": [], "rank": 1}], "lapsed": [],
                "counts": {"due": 1}, "data_through": DT, "rules": {}}
    reps = {"rep@example.com": {"id": 7, "name": "Ahmed Aradi", "focus_name": "Ahmed Aradi", "referral_code": "ahmed"},
            "noname@example.com": {"id": 8, "name": "New Rep", "focus_name": None, "referral_code": "new"}}
    with _Patched((followups, "followups", fake_followups),
                  (shop, "salesman_for_user", lambda email: reps.get(email)),
                  (user_auth, "_user_row", lambda email: {"email": email, "features": ["Shop Orders"]}),
                  (shop, "_salesman_by_id", lambda sid: {"focus_name": "Faisal Kandikkal"} if int(sid) == 3 else None)):
        c = TestClient(m.app)
        m.app.dependency_overrides[auth.get_current_user] = lambda: user("salesman")
        r = c.get("/shop/me/followups").json()
        assert seen[-1] == ("Ahmed Aradi", False) and r["scope"] == "rep" and r["due"][0]["wa_text"].startswith("Hello, Ahmed from YQ")
        m.app.dependency_overrides[auth.get_current_user] = lambda: user("salesman", "noname@example.com")
        r = c.get("/shop/me/followups").json()
        assert r["due"] == [] and "Focus name" in r["hint"]
        m.app.dependency_overrides[auth.get_current_user] = lambda: user("salesman", "nobody@example.com")
        r = c.get("/shop/me/followups").json()
        assert r["due"] == [] and r["hint"] == shop.UNLINKED_HINT
        m.app.dependency_overrides[auth.get_current_user] = lambda: user("admin", "boss@example.com")
        r = c.get("/shop/me/followups?rep=Faisal%20Kandikkal").json()
        assert seen[-1] == ("Faisal Kandikkal", True) and r["scope"] == "admin"
        c.get("/shop/me/followups?rep=3")
        assert seen[-1] == ("Faisal Kandikkal", True), "an admin may name the rep by salesman id"
        c.get("/shop/me/followups")
        assert seen[-1] == ("", True), "no rep = every shop with its owner, holdouts flagged"
        assert c.get("/shop/me/followups?rep=99").status_code == 404
        m.app.dependency_overrides.clear()


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


@test("baskets + link week: rep-scoped by referral code / salesman row; no code = empty, never an error")
def _():
    from app import followups, shop
    assert followups.baskets_not_sent({"id": 1, "referral_code": ""})["baskets"] == []
    seen: dict = {}

    def fake_analytics(days, salesman=None):
        seen["days"], seen["salesman"] = days, salesman
        return {"days": days, "since": "2026-09-17", "funnel": {"sessions": 40, "item_views": 30, "adds": 9,
                "checkouts": 4, "conversion_pct": 7.5}, "orders": 3, "cancelled": 1, "value_bhd": 61.2,
                "aov_bhd": 20.4, "customers": 3, "engagement": {"share": 2},
                "top_products": [{"item_code": "T02", "display_name": "Cable", "units": 12, "value_bhd": 35.4}] * 5}
    with _Patched((shop, "analytics", fake_analytics)):
        out = followups.link_week({"id": 7, "referral_code": "ahmed"})
        assert seen == {"days": 7, "salesman": {"id": 7, "referral_code": "ahmed"}}
        assert out["orders"] == 3 and out["value_bhd"] == "61.200" and out["aov_bhd"] == "20.400"
        assert out["sessions"] == 40 and out["conversion_pct"] == 7.5 and len(out["top_products"]) == 3
        assert out["shares"] == 2 and out["top_products"][0]["value_bhd"] == "35.400"


# ── statements: pure rules ────────────────────────────────────────────────────

@test("statements: transitions are forward only; periods validated; money is 3-dp half-up strings")
def _():
    from app import statements as st
    ok = {("draft", "approved"), ("draft", "superseded"), ("approved", "paid"), ("approved", "superseded")}
    for a in st.STATUSES:
        for b in st.STATUSES:
            assert st.can_transition(a, b) == ((a, b) in ok), (a, b)
    assert not st.can_transition(None, "approved") and not st.can_transition("draft", None)
    assert st.valid_period("2026-09") == "2026-09"
    for bad in ("2026-9", "2026-13", "202609", "", None, "2026-00"):
        try:
            st.valid_period(bad)
            raise AssertionError(bad)
        except st.StatementError:
            pass
    assert st.s3("85.1555") == "85.156" and st.s3(0.0005) == "0.001" and st.s3(None) == "0.000" and st.s3("1216.51") == "1216.510"
    assert st.total_kickback([{"kickback_bhd": "85.156"}, {"kickback_bhd": "56.244"}, {"kickback_bhd": 28.994}]) == "170.394"
    row = st.public_row({"id": 1, "status": "draft", "sales_bhd": 1216.51, "kickback_bhd": "85.156", "returns_bhd": None,
                         "rate": "0.0700", "target_snapshot": '{"team": "normal"}'})
    assert row["sales_bhd"] == "1216.510" and row["kickback_bhd"] == "85.156" and row["returns_bhd"] is None
    assert row["rate"] == 0.07 and row["target_snapshot"] == {"team": "normal"} and row["next_statuses"] == ["approved", "superseded"]
    assert st.summarize([{"status": "draft", "kickback_bhd": "1.0005"}, {"status": "draft", "kickback_bhd": "2"},
                         {"status": "paid", "kickback_bhd": "3"}]) == {"draft": {"count": 2, "kickback_bhd": "3.001"},
                                                                       "paid": {"count": 1, "kickback_bhd": "3.000"}}


# ── statements: a fake PostgREST client ──────────────────────────────────────

class _FakeTable:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.op, self.payload, self.filters, self.lim = "select", None, [], None
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

    def order(self, col, desc=False, **_k):
        self.orders.append((col, bool(desc)))
        return self

    def limit(self, n):
        self.lim = n
        return self

    def _match(self, row):
        for kind, col, val in self.filters:
            if kind == "eq" and str(row.get(col)) != str(val):
                return False
            if kind == "in" and str(row.get(col)) not in {str(v) for v in val}:
                return False
        return True

    def execute(self):
        rows = self.db.tables.setdefault(self.name, [])
        if self.op == "insert":
            if self.db.missing_cols and any(k in self.db.missing_cols for k in self.payload):
                raise _PGRST204(next(k for k in self.payload if k in self.db.missing_cols))
            row = {**self.payload, "id": len(rows) + 1}
            rows.append(row)
            self.db.log.append(("insert", self.name, dict(self.payload)))
            return type("R", (), {"data": [dict(row)]})()
        if self.op == "update":
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
        if self.lim:
            out = out[:self.lim]
        return type("R", (), {"data": out})()


class _PGRST204(Exception):
    code = "PGRST204"

    def __init__(self, col):
        super().__init__(f"Could not find the '{col}' column of 'salesman_kickback_statements' in the schema cache")


class _FakeDB:
    def __init__(self, rows=(), missing_cols=()):
        self.tables = {"salesman_kickback_statements": [dict(r) for r in rows]}
        self.missing_cols = set(missing_cols)
        self.log: list = []

    def table(self, name):
        return _FakeTable(self, name)


def _stmt(id, status="draft", salesman="Ahmed Aradi", period="2026-09", data_through=DT, **kw):
    return {"id": id, "salesman": salesman, "salesman_id": 7, "period": period, "basis": "net_ex_vat", "status": status,
            "data_through": data_through, "sales_bhd": "1124.880", "returns_bhd": None, "tier_reached": 1, "rate": 0.05,
            "kickback_bhd": "56.244", "target_snapshot": {"team": "mobile_accessories"}, "note": None,
            "created_by": "close_kickback_month", "created_at": "2026-09-24T08:00:00+00:00", **kw}


@test("statements: approve / paid / supersede move forward only, touch no amount, CAS on the status, audited")
def _():
    from app import audit, statements as st
    db = _FakeDB([_stmt(1), _stmt(2, "approved", approved_by="a@x", approved_at="2026-09-25T00:00:00+00:00"),
                  _stmt(3, "paid"), _stmt(4, "snapshot", basis="vat_incl_display"), _stmt(5, "superseded")])
    audits: list = []
    with _Patched((st, "get_client", lambda: db), (audit, "log_event", lambda *a, **k: audits.append((a, k)))):
        row = st.transition(1, "approved", by="boss@example.com")
        assert row["status"] == "approved" and row["approved_by"] == "boss@example.com" and row["approved_at"]
        assert row["kickback_bhd"] == "56.244" and row["sales_bhd"] == "1124.880"
        up = [e for e in db.log if e[0] == "update"][-1]
        assert set(up[2]) == {"status", "approved_by", "approved_at"} and ("eq", "status", "draft") in up[3]
        row = st.transition(2, "paid", by="boss@example.com")
        assert row["status"] == "paid" and row["paid_at"] and row["paid_by"] == "boss@example.com"
        for sid, to in ((3, "approved"), (3, "paid"), (3, "superseded"), (4, "approved"), (5, "paid"), (1, "approved")):
            try:
                st.transition(sid, to, by="boss@example.com", reason="x")
                raise AssertionError(f"{sid} -> {to} must be refused")
            except st.StatementError as e:
                assert "cannot become" in str(e), str(e)
        for to in ("draft", "snapshot", "bogus"):
            try:
                st.transition(1, to, by="b")
                raise AssertionError(to)
            except st.StatementError:
                pass
        try:
            st.transition(1, "superseded", by="b")
            raise AssertionError("supersede needs a reason")
        except st.StatementError as e:
            assert "reason" in str(e)
        try:
            st.transition(99, "approved", by="b")
            raise AssertionError("missing id")
        except st.StatementError as e:
            assert str(e) == "Statement not found."
        # CAS: the row moved between the read and the write
        real_get = st.get_statement

        def stale_get(sid):
            r = real_get(sid)
            return {**r, "status": "draft"} if r and sid == 1 else r
        with _Patched((st, "get_statement", stale_get)):
            try:
                st.transition(1, "approved", by="b")
                raise AssertionError("must conflict")
            except st.StatementError as e:
                assert str(e) == st.CONFLICT_MSG
        events = [a[1] for a, _k in audits]
        assert events == ["kickback.statement_approved", "kickback.statement_paid"], events
        detail = audits[0][1]["detail"]
        assert detail == {"id": 1, "salesman": "Ahmed Aradi", "period": "2026-09", "basis": "net_ex_vat",
                          "from": "draft", "to": "approved", "kickback_bhd": "56.244", "reason": None}
        amounts = {"sales_bhd", "returns_bhd", "kickback_bhd", "rate", "tier_reached", "target_snapshot"}
        for e in db.log:
            if e[0] == "update":
                assert not amounts & set(e[2]), e


@test("statements: the R3 columns are optional — a write naming them is retried without them until the migration runs")
def _():
    from app import audit, statements as st
    db = _FakeDB([_stmt(1, "approved")], missing_cols={"paid_by", "superseded_at", "superseded_by", "superseded_reason",
                                                       "superseded_by_id"})
    with _Patched((st, "get_client", lambda: db), (audit, "log_event", lambda *a, **k: None)):
        row = st.transition(1, "paid", by="boss@example.com")
        assert row["status"] == "paid" and row["paid_at"] and "paid_by" not in row
        ups = [e for e in db.log if e[0] == "update"]
        assert len(ups) == 1 and set(ups[0][2]) == {"status", "paid_at"}


@test("statements: create_draft freezes the card's rows once — identical drafts skipped, older drafts superseded, approved rows untouched")
def _():
    from app import audit, statements as st
    fresh = [{"salesman": "Ahmed Aradi", "salesman_id": 7, "period": "2026-09", "basis": "net_ex_vat", "data_through": DT,
              "sales_bhd": "1124.880", "returns_bhd": None, "tier_reached": 1, "rate": 0.05, "kickback_bhd": "56.244",
              "target_snapshot": {"team": "mobile_accessories"}},
             {"salesman": "Karrar Mohamed", "salesman_id": 9, "period": "2026-09", "basis": "net_ex_vat", "data_through": DT,
              "sales_bhd": "1216.510", "returns_bhd": None, "tier_reached": 2, "rate": 0.07, "kickback_bhd": "85.156",
              "target_snapshot": {"team": "mobile_accessories"}},
             {"salesman": "Faisal Kandikkal", "salesman_id": 4, "period": "2026-09", "basis": "net_ex_vat", "data_through": DT,
              "sales_bhd": "362.430", "returns_bhd": None, "tier_reached": 3, "rate": 0.08, "kickback_bhd": "28.994",
              "target_snapshot": {"team": "normal"}}]
    db = _FakeDB([_stmt(1, "draft", salesman="Ahmed Aradi", data_through=DT),                 # identical → skipped
                  _stmt(2, "draft", salesman="Karrar Mohamed", data_through="2026-09-21"),   # older → superseded
                  _stmt(3, "approved", salesman="Faisal Kandikkal", data_through="2026-09-21"),   # closed → noted, untouched
                  _stmt(4, "snapshot", salesman="Karrar Mohamed", basis="vat_incl_display", data_through="2026-09-21")])
    audits: list = []
    with _Patched((st, "get_client", lambda: db), (st, "build_rows", lambda period, basis=None: fresh),
                  (audit, "log_event", lambda *a, **k: audits.append((a, k)))):
        out = st.create_draft("2026-09", by="boss@example.com", note="September close")
    assert [c["salesman"] for c in out["created"]] == ["Karrar Mohamed", "Faisal Kandikkal"], out["created"]
    assert out["skipped"] == [{"salesman": "Ahmed Aradi", "id": 1, "reason": "a draft statement with the same data date exists"}]
    assert out["superseded"] == [{"salesman": "Karrar Mohamed", "id": 2, "by": 5}]
    assert out["total_kickback_bhd"] == "114.150" and out["created"][1]["note"].startswith("1 approved/paid statement")
    rows = {r["id"]: r for r in db.tables["salesman_kickback_statements"]}
    assert rows[2]["status"] == "superseded" and rows[2]["superseded_by_id"] == 5 and rows[2]["superseded_by"] == "boss@example.com"
    assert rows[3]["status"] == "approved" and rows[4]["status"] == "snapshot" and rows[1]["status"] == "draft"
    assert rows[5]["status"] == "draft" and rows[5]["created_by"] == "boss@example.com" and rows[5]["note"] == "September close"
    assert rows[5]["kickback_bhd"] == "85.156" and rows[5]["sales_bhd"] == "1216.510" and rows[5]["returns_bhd"] is None
    assert len(rows) == 6 and not any(e[0] == "delete" for e in db.log)
    assert audits[-1][0][1] == "kickback.statement" and audits[-1][1]["detail"]["reps"] == 2
    assert audits[-1][1]["detail"]["superseded"] == 1 and audits[-1][1]["detail"]["total_kickback_bhd"] == "114.150"


@test("statements: /shop/me carries last_closed (latest approved/paid) and the open draft; nothing for an unlinked login")
def _():
    from app import statements as st
    db = _FakeDB([_stmt(1, "paid", period="2026-07", kickback_bhd="40.000"),
                  _stmt(2, "approved", period="2026-08", kickback_bhd="51.250", approved_at="2026-09-03T00:00:00+00:00"),
                  _stmt(3, "draft", period="2026-09"), _stmt(4, "superseded", period="2026-09"),
                  _stmt(5, "approved", period="2026-08", salesman="Somebody Else", kickback_bhd="999")])
    with _Patched((st, "get_client", lambda: db)):
        out = st.rep_summary({"focus_name": "Ahmed Aradi"})
        assert out["last_closed"]["period"] == "2026-08" and out["last_closed"]["kickback_bhd"] == "51.250"
        assert out["last_closed"]["status"] == "approved" and out["last_closed"]["approved_at"]
        assert out["draft"]["period"] == "2026-09" and out["draft"]["id"] == 3
        assert set(out["last_closed"]) <= {"id", "period", "status", "basis", "data_through", "sales_bhd", "returns_bhd",
                                           "tier_reached", "rate", "kickback_bhd", "approved_at", "paid_at", "created_at"}
        assert st.rep_summary(None) == {"last_closed": None, "draft": None}
        assert st.rep_summary({"focus_name": ""}) == {"last_closed": None, "draft": None}
    listing = None
    with _Patched((st, "get_client", lambda: db)):
        listing = st.list_statements(period="2026-08")
    assert listing["available"] and {r["id"] for r in listing["statements"]} == {2, 5}
    assert listing["summary"] == {"approved": {"count": 2, "kickback_bhd": "1050.250"}}


# ── attainment ────────────────────────────────────────────────────────────────

@test("attainment: one SQL (Accessories, no giveaways, net_bhd, `name - %` match) → tier_progress per rep; no-target reps flagged")
def _():
    from datetime import date
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
                      "no_target": False, "tier_reached": 2}
    assert top[2]["no_target"] is True


@test("attainment + dashboard: the payload keeps by_salesman (now Accessories, current month) plus its scope")
def _():
    from app import reports
    att = [{"salesman": "Karrar Mohamed", "invoices": 23, "gross_bhd": 1338.19, "net_bhd": 1216.51, "no_target": False,
            "tier_reached": 2, "period": "2026-09", "data_through": DT}]
    s = {"rev_today": 0, "net_today": 0, "orders_today": 0, "rev_yesterday": 0, "orders_yesterday": 0, "rev_mtd": 0,
         "net_mtd": 0, "orders_mtd": 0, "rev_prev_month": 0, "total_receivables": 0, "overdue_accounts": 0,
         "overdue_receivables_bhd": 0, "current_receivables_bhd": 0, "top_customers": [], "data_date": DT}
    a = {"low_stock_count": 0, "negative_margin_count": 0}
    out = reports._assemble_dashboard({"s": s, "a": a, "health": {}, "movers": {}, "trend": [], "channel": [],
                                       "agents": [], "fresh": {"stale": False, "days_behind": 0}, "daily_mtd": [],
                                       "split": {"by_payment": [], "by_division": []}, "attainment": att})
    assert out["by_salesman"][0]["salesman"] == "Karrar Mohamed" and out["by_salesman"][0]["net_bhd"] == 1216.51
    assert out["by_salesman_scope"] == {"division": "Accessories", "basis": "net_ex_vat", "period": "2026-09", "data_through": DT}
    assert out["attainment"] is att


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
    import psycopg
    try:
        admin = psycopg.connect(LOCAL_DSN, connect_timeout=3, autocommit=True)
    except Exception:  # noqa: BLE001
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
    from datetime import date, timedelta
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
        every = {r["customer_name"]: r["rep"] for r in followups.load_shops("", run=run)}
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
