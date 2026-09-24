"""R1 security gates (24-Sep-2026) — pure tests, no database.

    python -m tests.test_r1_security

Covers plan §8 S2–S8/S10: the route → gate table, disabled/must_reset logins, the machine key,
verified-token rate-limit buckets, the device-bound phone recognition, server-side event
attribution, the MRN cost gate on /orders/{po_no}/file, the sqlglot SQL allowlist and CORS.
Same lightweight runner as tests/test_v3.py.
"""
from __future__ import annotations

import io
import sys
import time
import traceback
from pathlib import Path

# Windows cp1252 console can't print report characters — force UTF-8.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

import warnings  # noqa: E402
warnings.filterwarnings("ignore")

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


# ── helpers ────────────────────────────────────────────────────────────────────

def _req(headers: dict | None = None, host: str = "10.0.0.9", path: str = "/x", method: str = "GET"):
    from starlette.requests import Request
    scope = {"type": "http", "method": method, "path": path, "query_string": b"",
             "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
             "client": (host, 1234), "server": ("testserver", 80), "scheme": "http"}
    return Request(scope)


def _token(sub: str, secret: str, **claims) -> str:
    import jwt
    payload = {"sub": sub, "email": f"{sub}@example.com", "aud": "authenticated",
               "exp": int(time.time()) + 300, **claims}
    return jwt.encode(payload, secret, algorithm="HS256")


def _creds(token: str):
    from fastapi.security import HTTPAuthorizationCredentials
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


class _Chain:
    """A Supabase query builder stand-in: every method chains, execute() returns `.data`."""

    def __init__(self, data=None):
        self.data = data if data is not None else []
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        def _m(*a, **k):
            self.calls.append((name, a, k))
            return self
        return _m

    def execute(self):
        return self


class _Patched:
    """Temporarily set module attributes; restores on exit (no pytest monkeypatch here)."""

    def __init__(self, *triples):
        self.triples = triples
        self.saved = []

    def __enter__(self):
        for mod, name, value in self.triples:
            self.saved.append((mod, name, getattr(mod, name)))
            setattr(mod, name, value)
        return self

    def __exit__(self, *exc):
        for mod, name, old in reversed(self.saved):
            setattr(mod, name, old)
        return False


def _fake_users(rows: dict):
    """Route get_current_user through in-memory user_roles rows and a stub token decoder
    (token text = the user's local part)."""
    from app import auth, database
    database.invalidate_user_cache()
    return _Patched((auth, "_decode_token", lambda tok: {"sub": "uid-" + tok, "email": tok + "@example.com"}),
                    (database, "_select_user_row", lambda email: rows.get(email)))


# ── the route → gate table ─────────────────────────────────────────────────────

def _gate_names(route) -> list[str]:
    from app import auth
    out = []
    for dep in route.dependant.dependencies:
        c = dep.call
        if c is auth.require_admin:
            out.append("admin")
        elif c is auth.require_agent_or_admin:
            out.append("agent_or_admin")
        elif c is auth.get_caller:
            out.append("caller")
        elif c is auth.get_current_user:
            out.append("user")
        elif getattr(c, "feature", None):
            out.append(f"feature:{c.feature}")
        elif getattr(c, "features", None):
            out.append("any:" + "|".join(c.features))
        else:
            out.append(f"?{getattr(c, '__qualname__', c)}")
    return out


PUBLIC_ROUTES = {"/health", "/team/invite/{token}", "/team/accept"}

EXPECTED_GATES = {
    ("POST", "/ask"): ["feature:AI Assistant"],
    ("POST", "/ask/stream"): ["feature:AI Assistant"],
    ("GET", "/digest/daily"): ["agent_or_admin"],
    ("GET", "/digest/alerts"): ["agent_or_admin"],
    ("GET", "/escalation/check"): ["agent_or_admin"],
    ("GET", "/escalation/brief"): ["agent_or_admin"],
    ("GET", "/events/dispatch"): ["agent_or_admin"],
    ("GET", "/scheduler/run-due"): ["agent_or_admin"],
    ("GET", "/scheduler/shop-jobs"): ["agent_or_admin"],
    ("GET", "/agents"): ["caller"],                   # + in-route AGENT_FEATURE filter for members
    ("GET", "/agents/{name}"): ["caller"],            # + in-route AGENT_FEATURE check for members
    ("GET", "/feed"): ["feature:Live Feed"],
    ("GET", "/actions"): ["admin"],
    ("POST", "/action"): ["feature:AI Agents"],
    ("PATCH", "/actions/{action_id}/approve"): ["admin"],
    ("PATCH", "/actions/{action_id}/reject"): ["admin"],
    ("POST", "/orders/{po_no}/photo"): ["feature:Orders"],
    ("POST", "/orders/{po_no}/file"): ["feature:Orders"],
    ("POST", "/orders/attach-doc"): ["feature:Orders"],
    ("POST", "/orders/verify"): ["feature:Orders"],
    ("POST", "/material-receipts/upload"): ["admin"],
    ("POST", "/invoices/upload"): ["admin"],
    ("GET", "/shop/assignment-queue"): ["admin"],
    ("POST", "/shop/restock/resolve"): ["feature:Shop Orders"],
    ("GET", "/shop/me"): ["feature:Shop Orders"],
    ("GET", "/me"): ["user"],
    ("GET", "/auth/features"): ["user"],
    ("POST", "/auth/password"): ["user"],
    ("GET", "/team"): ["admin"],
    ("POST", "/team/invite"): ["admin"],
    ("PATCH", "/team/{email}"): ["admin"],
    ("DELETE", "/team/{email}"): ["admin"],
    ("POST", "/ingest"): ["admin"],
    ("GET", "/settings/shop"): ["admin"],
    ("PUT", "/settings/shop"): ["admin"],
}


@test("gates: every sensitive route carries the expected dependency; nothing else is ungated")
def _():
    from fastapi.routing import APIRoute
    import app.main as m
    table = {}
    for r in m.app.routes:
        if isinstance(r, APIRoute):
            for meth in r.methods:
                table[(meth, r.path)] = _gate_names(r)
    missing = [k for k in EXPECTED_GATES if k not in table]
    assert not missing, f"routes gone: {missing}"
    wrong = {k: table[k] for k, v in EXPECTED_GATES.items() if table[k] != v}
    assert not wrong, f"gate mismatch: {wrong}"
    ungated = sorted(f"{m} {p}" for (m, p), g in table.items()
                     if not g and p not in PUBLIC_ROUTES and not p.startswith("/public/"))
    assert not ungated, f"ungated routes: {ungated}"
    unknown = sorted(f"{m} {p}: {g}" for (m, p), g in table.items() if any(x.startswith("?") for x in g))
    assert not unknown, f"unrecognised gates: {unknown}"


@test("gates: require_feature tags its closure; the machine key list is unchanged")
def _():
    from app import auth
    assert auth.require_feature("Orders").feature == "Orders"
    assert auth.AGENT_PATH_PREFIXES == ("/agents", "/scheduler", "/escalation", "/digest", "/events")
    assert auth.MUST_RESET_EXEMPT == {"/me", "/auth/features", "/auth/password"}


# ── get_current_user: status + must_reset ──────────────────────────────────────

@test("auth: a disabled row is 403; must_reset is 403 everywhere except /me, /auth/features, /auth/password")
def _():
    from fastapi import HTTPException
    from app import auth, database
    rows = {
        "active@example.com": {"email": "active@example.com", "role": "member", "status": "active", "features": []},
        "off@example.com": {"email": "off@example.com", "role": "member", "status": "disabled", "features": []},
        "temp@example.com": {"email": "temp@example.com", "role": "salesman", "status": "active",
                             "features": ["Catalog"], "must_reset": True},
        "legacy@example.com": {"email": "legacy@example.com", "role": "admin", "status": "active", "features": []},
    }

    def status_of(tok, path="/feed"):
        try:
            auth.get_current_user(_req(path=path), _creds(tok))
            return 200
        except HTTPException as e:
            return (e.status_code, e.detail)

    with _fake_users(rows):
        u = auth.get_current_user(_req(path="/feed"), _creds("active"))
        assert (u.email, u.role, u.status, u.must_reset) == ("active@example.com", "member", "active", False)
        assert status_of("off")[0] == 403, "disabled member must be refused"
        assert status_of("temp") == (403, auth.MUST_RESET_DETAIL)
        for p in ("/me", "/auth/features", "/auth/password"):
            assert auth.get_current_user(_req(path=p), _creds("temp")).must_reset is True, p
        assert status_of("temp", "/shop/me")[0] == 403
        assert auth.get_current_user(_req(path="/feed"), _creds("legacy")).must_reset is False, \
            "a row without the column (pre-migration) must be treated as false"
        assert status_of("norow")[0] == 403
        try:
            auth.get_current_user(_req(path="/feed"), None)
            raise AssertionError("no token must be 401")
        except HTTPException as e:
            assert e.status_code == 401
    database.invalidate_user_cache()


@test("auth: cached_user_row falls back to the legacy column list until the migration lands")
def _():
    from app import database

    class _Boom(Exception):
        code = "42703"
        message = "column user_roles.must_reset does not exist"

    seen: list[str] = []

    class _Tbl:
        def select(self, cols):
            seen.append(cols)
            self.cols = cols
            return self

        def eq(self, *a):
            return self

        def limit(self, *a):
            return self

        def execute(self):
            if "must_reset" in self.cols:
                raise _Boom("column user_roles.must_reset does not exist")
            return type("R", (), {"data": [{"email": "x@example.com", "role": "member", "status": "active"}]})()

    class _Client:
        def table(self, name):
            assert name == "user_roles"
            return _Tbl()

    database.invalidate_user_cache()
    with _Patched((database, "get_client", lambda: _Client()), (database, "_legacy_until", 0.0)):
        row = database.cached_user_row("x@example.com")
        assert row and row["role"] == "member" and "must_reset" not in row
        assert seen == [database._USER_COLUMNS, database._USER_COLUMNS_LEGACY], seen
        database.invalidate_user_cache()
        database.cached_user_row("x@example.com")
        assert seen[-1] == database._USER_COLUMNS_LEGACY, "the legacy shape must be remembered, not re-probed at once"
    database.invalidate_user_cache()


@test("auth: require_agent_or_admin — the key on automation paths, an admin JWT, nobody else")
def _():
    from fastapi import HTTPException
    from app import auth, database
    from app.config import settings
    rows = {
        "admin@example.com": {"email": "admin@example.com", "role": "admin", "status": "active", "features": []},
        "member@example.com": {"email": "member@example.com", "role": "member", "status": "active", "features": []},
    }
    old_key = settings.agent_api_key
    settings.agent_api_key = "k-test-key-value"
    try:
        with _fake_users(rows):
            agent = auth.get_caller(_req(path="/digest/daily"), None, "k-test-key-value")
            assert agent.role == "agent" and auth.require_agent_or_admin(agent).role == "agent"
            for p in ("/scheduler/shop-jobs", "/events/dispatch", "/agents", "/agents/collections", "/escalation/brief"):
                assert auth.get_caller(_req(path=p), None, "k-test-key-value").role == "agent", p
            try:
                auth.get_caller(_req(path="/feed"), None, "k-test-key-value")
                raise AssertionError("the key must not work off the automation paths")
            except HTTPException as e:
                assert e.status_code == 401
            try:
                auth.get_caller(_req(path="/digest/daily"), None, "wrong-key-value-here")
                raise AssertionError("a wrong key must fall through to (missing) JWT auth")
            except HTTPException as e:
                assert e.status_code == 401
            admin = auth.get_caller(_req(path="/digest/daily"), _creds("admin"), None)
            assert auth.require_agent_or_admin(admin).role == "admin"
            member = auth.get_caller(_req(path="/digest/daily"), _creds("member"), None)
            try:
                auth.require_agent_or_admin(member)
                raise AssertionError("a member JWT must not run digests")
            except HTTPException as e:
                assert e.status_code == 403
    finally:
        settings.agent_api_key = old_key
        database.invalidate_user_cache()


@test("auth: /agents/{name} — members only run agents behind their pages and never email")
def _():
    from fastapi.testclient import TestClient
    import app.main as m
    from app import agents, auth, database
    from app.orchestrator import AGENT_FEATURE
    name = next(n for n, f in AGENT_FEATURE.items() if f == "Receivables")
    other = next(n for n, f in AGENT_FEATURE.items() if f == "Sales")
    rows = {
        "rec@example.com": {"email": "rec@example.com", "role": "member", "status": "active", "features": ["Receivables"]},
        "boss@example.com": {"email": "boss@example.com", "role": "admin", "status": "active", "features": []},
    }
    ran: list[str] = []
    mailed: list[dict] = []

    def _run(n, *a, **k):
        if n not in agents.AGENTS:
            raise KeyError(n)
        ran.append(n)
        return {"agent": n, "summary": "ok"}

    from app import emailer
    with _fake_users(rows), _Patched((agents, "run_agent", _run), (m, "log_event", lambda *a, **k: None),
                                     (emailer, "send_agent", lambda r: mailed.append(r) or {"sent": True})):
        c = TestClient(m.app)
        h = {"authorization": "Bearer rec"}
        assert c.get(f"/agents/{name}", headers=h).status_code == 200
        assert c.get(f"/agents/{other}", headers=h).status_code == 403
        assert c.get("/agents/not-an-agent", headers=h).status_code == 403
        assert c.get(f"/agents/{name}?email=true", headers=h).status_code == 200 and not mailed, \
            "a member's email=true must be ignored"
        r = c.get("/agents", headers=h)
        listed = {a["name"] for a in r.json()}
        assert r.status_code == 200 and name in listed and other not in listed, "the roster follows the same rule"
        assert listed == {n for n, f in AGENT_FEATURE.items() if f == "Receivables"}, listed
        assert c.get("/digest/daily", headers=h).status_code == 403
        hb = {"authorization": "Bearer boss"}
        assert c.get(f"/agents/{other}?email=true", headers=hb).status_code == 200 and len(mailed) == 1
        assert c.get("/agents/not-an-agent", headers=hb).status_code == 404
        r = c.get("/agents", headers=hb)
        assert r.status_code == 200 and len(r.json()) == len(agents.AGENTS)
    database.invalidate_user_cache()


# ── rate limiting ──────────────────────────────────────────────────────────────

@test("ratelimit: junk bearers share the IP bucket, a verified token owns one, /public and /team are IP-only")
def _():
    from app import ratelimit
    from app.config import settings
    old = (settings.trusted_proxy_hops, settings.supabase_jwt_secret)
    ratelimit.reset_cache()
    try:
        settings.trusted_proxy_hops = 1
        settings.supabase_jwt_secret = "unit-test-secret"
        xff = {"x-forwarded-for": "1.2.3.4"}
        junk = ["Bearer eyJhbGciOi.first-token.signature-1234567890",
                "Bearer eyJhbGciOi.other-token.signature-1234567890", "Bearer x", "Bearer " + "a" * 400]
        keys = {ratelimit.rate_limit_key(_req({"authorization": j, **xff})) for j in junk}
        assert keys == {"1.2.3.4"}, keys
        good = _token("u1", "unit-test-secret")
        k = ratelimit.rate_limit_key(_req({"authorization": "Bearer " + good, **xff}))
        assert k.startswith("u:"), k
        assert ratelimit.rate_limit_key(_req({"authorization": "Bearer " + good, "x-forwarded-for": "8.8.8.8"})) == k
        assert ratelimit.rate_limit_key(_req({"authorization": "Bearer " + _token("u2", "unit-test-secret"), **xff})) != k
        assert ratelimit.rate_limit_key(_req({"authorization": "Bearer " + _token("u1", "other-secret"), **xff})) == "1.2.3.4"
        expired = _token("u1", "unit-test-secret", exp=int(time.time()) - 30)
        assert ratelimit.rate_limit_key(_req({"authorization": "Bearer " + expired, **xff})) == "1.2.3.4"
        wrong_aud = _token("u1", "unit-test-secret", aud="anon")
        assert ratelimit.rate_limit_key(_req({"authorization": "Bearer " + wrong_aud, **xff})) == "1.2.3.4"
        for p in ("/public/market/order", "/public/market/recognize", "/team/accept", "/team/invite/abc", "/health"):
            assert ratelimit.rate_limit_key(_req({"authorization": "Bearer " + good, **xff}, path=p)) == "1.2.3.4", p
        # remembered: neither a verified nor a rejected token is decoded again
        with _Patched((ratelimit, "_verify", lambda t: (_ for _ in ()).throw(AssertionError("re-verified")))):
            assert ratelimit.rate_limit_key(_req({"authorization": "Bearer " + good, **xff})) == k
            assert ratelimit.rate_limit_key(_req({"authorization": junk[0], **xff})) == "1.2.3.4"
        assert ratelimit.rate_limit_key(_req(xff)) == "1.2.3.4"
        assert ratelimit.rate_limit_key(_req({})) == "10.0.0.9"
    finally:
        settings.trusted_proxy_hops, settings.supabase_jwt_secret = old
        ratelimit.reset_cache()


# ── recognise + events ─────────────────────────────────────────────────────────

@test("recognize: device-bound — only a device that already ordered as the merchant is told anything, nothing is written")
def _():
    from app import shop
    cust = {"id": 7, "name": "Ali Hasan", "shop": "Ali Mobiles", "area": "Manama", "orders_count": 3,
            "device_ids": ["dev-A"], "email": "secret@example.com"}

    class _NoWrite:
        def table(self, *_a, **_k):
            raise AssertionError("recognize_phone must not touch the DB beyond the lookup")

    with _Patched((shop, "_customer_by_phone", lambda phone: cust if phone == "97339000000" else None),
                  (shop, "get_client", lambda: _NoWrite())):
        r = shop.recognize_phone("39000000", "dev-A")
        assert r == {"shop": "Ali Mobiles", "area": "Manama", "first_name": "Ali", "orders_count": 3}, r
        assert shop.recognize_phone("+973 3900 0000", "dev-A") == r
        assert shop.recognize_phone("39000000", "dev-B") is None, "a stranger's device learns nothing"
        assert shop.recognize_phone("39000000", None) is None
        assert shop.recognize_phone("39000000", "") is None
        assert shop.recognize_phone("39111111", "dev-A") is None
        assert cust["device_ids"] == ["dev-A"], "asking must never enrol a device"


@test("events: the rep is derived from the ref on the server; a client-sent salesman_id is ignored")
def _():
    from app import shop
    inserted: list[dict] = []

    class _Tbl:
        def insert(self, row):
            inserted.append(row)
            return self

        def execute(self):
            return self

    class _Client:
        def table(self, name):
            assert name == "shop_events"
            return _Tbl()

    ctx = {"salesmen": [{"id": 5, "name": "Karrar", "referral_code": "karrar"}], "settings": {}, "items": {}}
    with _Patched((shop, "get_client", lambda: _Client()), (shop, "context", lambda force=False: ctx)):
        assert shop.record_event({"event": "view", "referral_code": "KARRAR", "salesman_id": 999, "device_id": "d1"})
        assert inserted[-1]["salesman_id"] == 5 and inserted[-1]["referral_code"] == "karrar"
        assert shop.record_event({"event": "view", "salesman_id": 999})
        assert inserted[-1]["salesman_id"] is None
        assert shop.record_event({"event": "add", "referral_code": "nobody", "salesman_id": 5})
        assert inserted[-1]["salesman_id"] is None and inserted[-1]["referral_code"] == "nobody"
        assert shop.record_event({"event": "cancel", "salesman_id": 999}, salesman_id=5), "the cancel route's rep is trusted"
        assert inserted[-1]["salesman_id"] == 5
        assert not shop.record_event({"event": "bogus"}) and len(inserted) == 4


# ── shop scoping ───────────────────────────────────────────────────────────────

@test("shop: unlinked non-admin /shop/me is empty + hint; restock resolve is scoped to the rep's code")
def _():
    from app import shop
    with _Patched((shop, "salesman_for_user", lambda email: None),
                  (shop, "get_client", lambda: (_ for _ in ()).throw(AssertionError("no company totals for a rep")))):
        r = shop.me_payload("rep@example.com")
        assert r["salesman"] is None and r["hint"] == shop.UNLINKED_HINT
        assert r["kpis"] == {"orders_7d": 0, "orders_30d": 0, "value_30d_bhd": 0.0, "customers_30d": 0}
        assert r["focus"] is None and r["link"] is None
    chain = _Chain(data=[])
    with _Patched((shop, "salesman_for_user", lambda email: None), (shop, "get_client", lambda: chain)):
        r = shop.me_payload("boss@example.com", is_admin=True)
        assert "hint" not in r and r["kpis"]["orders_30d"] == 0, "an admin still sees company totals"

    q = _Chain(data=[{"id": 1}])
    with _Patched((shop, "get_client", lambda: q)):
        assert shop.resolve_restock([1, 2, 3], referral_code="Karrar") == 1
        assert ("eq", ("referral_code", "karrar"), {}) in q.calls, q.calls
        assert ("in_", ("id", [1, 2, 3]), {}) in q.calls
    q2 = _Chain(data=[{"id": 1}, {"id": 2}])
    with _Patched((shop, "get_client", lambda: q2)):
        assert shop.resolve_restock([1, 2]) == 2
        assert not any(c[0] == "eq" for c in q2.calls), "an admin resolves any row"
        assert shop.resolve_restock([]) == 0


# ── /orders/{po_no}/file ───────────────────────────────────────────────────────

def _mrn_xml(doc_no: str, po_no: str | None = None) -> bytes:
    po = f"<IdNamePair><Name>PONo</Name><Tag>PO:{po_no}</Tag></IdNamePair>" if po_no else ""
    return (f"<Root><Transaction><Header><DocNo>{doc_no}</DocNo></Header><HeaderExtra>{po}</HeaderExtra>"
            "<BodyData><TransBody><Sales><ProdCode>X01</ProdCode><Quantity>10</Quantity>"
            "<StockValue>5</StockValue><Gross>4</Gross></Sales></TransBody></BodyData>"
            "</Transaction></Root>").encode()


@test("orders: the MRN cost gate — PO must match the URL, receipt month at most next month")
def _():
    from datetime import date
    from app.main import _mrn_cost_gate
    today = date(2026, 9, 24)
    assert _mrn_cost_gate(_mrn_xml("YQ-26-09-2", "YQ-26-09-2"), "YQ-26-09-2", today) == (True, None)
    assert _mrn_cost_gate(_mrn_xml("YQ-26-10-1", "YQ-26-10-1"), "yq-26-10-1 ", today)[0], "next month, any case"
    assert _mrn_cost_gate(_mrn_xml("YQ-25-09-1", "YQ-25-09-1"), "YQ-25-09-1", today)[0], "an old receipt is fine"
    assert _mrn_cost_gate(_mrn_xml("YQ-27-01-1", "YQ-27-01-1"), "YQ-27-01-1", date(2026, 12, 5))[0], "Dec → Jan"
    for xml, po in ((_mrn_xml("YQ-26-11-1", "YQ-26-11-1"), "YQ-26-11-1"),      # two months ahead
                    (_mrn_xml("YQ-99-01-1", "YQ-99-01-1"), "YQ-99-01-1"),      # a fake future DocNo
                    (_mrn_xml("YQ-26-09-2", "YQ-26-09-1"), "YQ-26-09-2"),      # another order's receipt
                    (_mrn_xml("YQ-26-09-2"), "YQ-26-09-2"),                    # no PO number
                    (_mrn_xml("MRN-77", "YQ-26-09-2"), "YQ-26-09-2"),          # not a YQ receipt number
                    (_mrn_xml("YQ-26-13-1", "YQ-26-13-1"), "YQ-26-13-1"),      # month 13
                    (b"<Root></Root>", "YQ-26-09-2"), (b"not xml at all", "YQ-26-09-2")):
        ok, why = _mrn_cost_gate(xml, po, today)
        assert not ok and why, (xml[:40], po, why)


@test("orders: /orders/{po_no}/file — members store only; admins load costs only through the gate, audited")
def _():
    from fastapi.testclient import TestClient
    import app.main as m
    from app import database, orders
    from scripts import ingest_mrn
    rows = {
        "ops@example.com": {"email": "ops@example.com", "role": "member", "status": "active", "features": ["Orders"]},
        "boss@example.com": {"email": "boss@example.com", "role": "admin", "status": "active", "features": []},
        "rep@example.com": {"email": "rep@example.com", "role": "salesman", "status": "active", "features": ["Catalog"]},
    }
    stored: list[tuple] = []
    loaded: list[list] = []
    audit: list[tuple] = []
    with _fake_users(rows), _Patched(
            (orders, "store_order_file", lambda po, kind, data, ext, ct, by, filename=None: stored.append((po, kind, by)) or "p"),
            (ingest_mrn, "load_mrn_costs", lambda r: loaded.append(r) or {"docs": ["YQ-26-09-2"], "skus": 1, "lines": 1,
                                                                            "purchase_costs_month": "2026-09-01",
                                                                            "purchase_costs_rows": 1}),
            (m, "log_event", lambda email, ev, **k: audit.append((email, ev, k.get("detail")))),
    ):
        c = TestClient(m.app)
        good = _mrn_xml("YQ-26-09-2", "YQ-26-09-2")
        files = {"file": ("Transactions_1.xml", good, "application/xml")}
        r = c.post("/orders/YQ-26-09-2/file", headers={"authorization": "Bearer rep"}, files=files)
        assert r.status_code == 403, r.text
        r = c.post("/orders/YQ-26-09-2/file", headers={"authorization": "Bearer ops"}, files=files)
        assert r.status_code == 200 and r.json()["ok"] and r.json()["processed"] is None, r.text
        assert "admin" in r.json()["skipped"] and not loaded and stored[-1] == ("YQ-26-09-2", "mrn", "ops@example.com")
        r = c.post("/orders/YQ-26-09-1/file", headers={"authorization": "Bearer boss"}, files=files)
        assert r.status_code == 200 and r.json()["processed"] is None and "PO YQ-26-09-2" in r.json()["skipped"], r.text
        assert not loaded, "a receipt for another order must not load"
        r = c.post("/orders/YQ-26-09-2/file", headers={"authorization": "Bearer boss"}, files=files)
        assert r.status_code == 200 and r.json()["processed"] == "landed cost loaded", r.text
        assert len(loaded) == 1 and loaded[0][0]["code"] == "X01"
        cost_rows = [a for a in audit if a[1] == "cost_load"]
        assert len(cost_rows) == 1 and cost_rows[0][0] == "boss@example.com"
        assert cost_rows[0][2]["po_no"] == "YQ-26-09-2" and cost_rows[0][2]["skus"] == 1 and cost_rows[0][2]["source"] == "mrn"
    database.invalidate_user_cache()


# ── SQL validator ──────────────────────────────────────────────────────────────

@test("sql: bypasses are rejected — quoted names, schema names, subqueries, CTE shadowing, comma joins, INTO")
def _():
    from app.sql_validator import SQLValidationError, validate
    cases = [
        'SELECT * FROM "v_sales"',
        'SELECT * FROM public.v_sales',
        'SELECT * FROM (SELECT * FROM user_roles) x',
        'WITH v_sales AS (SELECT * FROM user_roles) SELECT * FROM v_sales',
        'WITH "v_sales" AS (SELECT 1) SELECT * FROM v_sales',
        'SELECT * FROM v_sales, user_roles',
        'SELECT * FROM v_sales CROSS JOIN user_roles',
        'SELECT * FROM v_sales JOIN user_roles USING (email)',
        'SELECT * FROM v_sales UNION SELECT * FROM orders',
        'SELECT * INTO newt FROM v_sales',
        'SELECT * FROM v_sales FOR UPDATE',
        'SELECT 1; SELECT 2',
        'SELECT * FROM v_sales WHERE item_code IN (SELECT item_code FROM secret)',
        'SELECT * FROM v_sales WHERE EXISTS (SELECT 1 FROM "user_roles")',
        'SELECT (SELECT COUNT(*) FROM audit_log) AS n FROM v_sales',
        'SELECT * FROM v_sales s LEFT JOIN "public"."user_roles" u ON true',
        'SELECT * FROM v_sales s WHERE s.x = ANY(SELECT y FROM z)',
        'SELECT * FROM v_sales s, LATERAL (SELECT * FROM user_roles l) q',
        'SELECT pg_sleep(10) FROM v_sales',
        "SELECT query_to_xml('select * from user_roles', true, false, '') FROM v_sales",
        'TABLE v_sales',
        'SELECT * FROM v_sales WHERE 1=1) UNION (SELECT * FROM user_roles',
        'SELECT * FROM v_sales -- ; DROP TABLE x',
        'DELETE FROM v_sales',
    ]
    for sql in cases:
        try:
            validate(sql)
            raise AssertionError(f"accepted: {sql}")
        except SQLValidationError:
            pass


@test("sql: legitimate shapes pass, every template passes, LIMIT is injected/clamped on the outer query")
def _():
    from app.sql_validator import MAX_ROWS, validate
    from app.templates import TEMPLATES
    for t in TEMPLATES:
        validate(t["sql"])
    ok = [
        'SELECT * FROM V_SALES',
        'WITH t AS (SELECT * FROM v_sales) SELECT * FROM t JOIN v_current_stock USING (item_code)',
        "SELECT date_trunc('month', sale_date)::date AS m, SUM(revenue_bhd) FROM v_sales "
        "WHERE sale_date > CURRENT_DATE - interval '90 days' GROUP BY 1 ORDER BY 1",
        'SELECT * FROM v_sales s, LATERAL (SELECT * FROM v_low_stock l WHERE l.item_code = s.item_code) q',
        "SELECT * FROM v_sales WHERE customer_name = 'O''Brien'",
        'SELECT s.item_code, COUNT(*) FILTER (WHERE s.revenue_bhd > 0) AS n, '
        'RANK() OVER (ORDER BY SUM(s.revenue_bhd) DESC) r FROM v_sales s GROUP BY 1',
        'SELECT DISTINCT ON (customer_name) customer_name, sale_date FROM v_sales ORDER BY customer_name, sale_date DESC NULLS LAST',
        "SELECT g.d::date FROM generate_series(CURRENT_DATE - 30, CURRENT_DATE, interval '1 day') AS g(d) "
        "LEFT JOIN v_sales s ON s.sale_date = g.d::date",
        'WITH RECURSIVE r AS (SELECT 1 AS n UNION ALL SELECT n+1 FROM r WHERE n < 5) SELECT * FROM r',
        'SELECT * FROM v_sales WHERE item_code IN (SELECT item_code FROM v_low_stock)',
        'SELECT * FROM v_sales UNION ALL SELECT * FROM v_sales',
    ]
    for sql in ok:
        assert validate(sql).endswith(f"LIMIT {MAX_ROWS}"), sql
    assert validate("SELECT * FROM v_sales LIMIT 5000").endswith(f"LIMIT {MAX_ROWS}")
    assert validate("SELECT * FROM v_sales LIMIT 5").endswith("LIMIT 5")
    assert validate("SELECT * FROM (SELECT * FROM v_sales LIMIT 5) t").endswith(f"LIMIT 5) t LIMIT {MAX_ROWS}")
    assert validate("SELECT * FROM v_sales ORDER BY 1 LIMIT 10 OFFSET 5").endswith("LIMIT 10 OFFSET 5")
    assert validate("SELECT * FROM v_sales LIMIT ALL").endswith(f"LIMIT {MAX_ROWS}")
    assert validate("SELECT * FROM v_sales FETCH FIRST 500 ROWS ONLY").endswith(f"FETCH FIRST {MAX_ROWS} ROWS ONLY")


@test("sql: feature scoping sees references inside subqueries and CTE bodies")
def _():
    from app.sql_validator import FeatureAccessError, validate
    for sql in ("SELECT * FROM v_sales WHERE item_code IN (SELECT item_code FROM v_current_stock)",
                "WITH s AS (SELECT * FROM v_current_stock) SELECT * FROM v_sales",
                "SELECT (SELECT COUNT(*) FROM v_receivables) FROM v_sales"):
        try:
            validate(sql, {"Sales"})
            raise AssertionError(f"not scoped: {sql}")
        except FeatureAccessError:
            pass
    assert validate("SELECT * FROM v_sales", {"Sales"})
    assert validate("SELECT * FROM v_sales", None)


# ── CORS ───────────────────────────────────────────────────────────────────────

@test("cors: no credentials, explicit methods and headers, download filename exposed")
def _():
    from fastapi.middleware.cors import CORSMiddleware
    import app.main as m
    cors = next(mw for mw in m.app.user_middleware if mw.cls is CORSMiddleware)
    k = cors.kwargs
    assert k["allow_credentials"] is False
    assert set(k["allow_methods"]) == {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
    assert "*" not in k["allow_headers"]
    assert {"Authorization", "Content-Type", "X-Agent-Key"} <= set(k["allow_headers"])
    assert "Content-Disposition" in k["expose_headers"]
    # the localhost dev origins stay, production origins come from ALLOWED_ORIGINS untouched
    from app.config import settings
    assert set(settings.allowed_origins) <= set(k["allow_origins"]) and "http://localhost:5173" in k["allow_origins"]


@test("auth: POST /auth/password is a must_reset-exempt route that clears the flag through set_password")
def _():
    from fastapi.testclient import TestClient
    import app.main as m
    from app import database, user_auth
    rows = {"temp@example.com": {"email": "temp@example.com", "role": "salesman", "status": "active",
                                 "features": ["Catalog"], "must_reset": True}}
    changed: list[tuple] = []
    with _fake_users(rows), _Patched((user_auth, "set_password", lambda e, p: changed.append((e, p)) or True),
                                     (m, "log_event", lambda *a, **k: None)):
        c = TestClient(m.app)
        h = {"authorization": "Bearer temp"}
        assert c.get("/shop/catalog", headers=h).status_code == 403, "everything else is closed"
        assert c.get("/me", headers=h).json()["must_reset"] is True
        assert c.post("/auth/password", headers=h, json={"password": "short"}).status_code == 400
        r = c.post("/auth/password", headers=h, json={"password": "a-proper-password"})
        assert r.status_code == 200 and r.json() == {"ok": True, "must_reset": False}, r.text
        assert changed == [("temp@example.com", "a-proper-password")]
    database.invalidate_user_cache()


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
