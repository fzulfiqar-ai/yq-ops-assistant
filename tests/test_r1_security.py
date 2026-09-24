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
    ("POST", "/orchestrate"): ["feature:AI Assistant"],
    ("POST", "/orchestrate/stream"): ["feature:AI Assistant"],
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
    # R3a (24-Sep-2026): statements are money → admin; attainment is the Salesmen page → Shop Admin;
    # the rep cards are personal → Shop Orders (scoped to the caller's own salesman row in-route)
    ("GET", "/shop/statements"): ["admin"],
    ("GET", "/shop/statements/preview"): ["admin"],
    ("POST", "/shop/statements/draft"): ["admin"],
    ("POST", "/shop/statements/{statement_id}/approve"): ["admin"],
    ("POST", "/shop/statements/{statement_id}/paid"): ["admin"],
    ("POST", "/shop/statements/{statement_id}/supersede"): ["admin"],
    ("GET", "/shop/attainment"): ["feature:Shop Admin"],
    ("GET", "/shop/me/followups"): ["feature:Shop Orders"],
    ("POST", "/shop/me/followups/tap"): ["feature:Shop Orders"],
    ("GET", "/shop/me/baskets"): ["feature:Shop Orders"],
    ("GET", "/shop/me/link-week"): ["feature:Shop Orders"],
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
        assert auth.MUST_RESET_DETAIL["code"] == "password_change_required", "the SPA api layer keys its redirect on this"
        assert "password" in auth.MUST_RESET_DETAIL["message"].lower()
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
        assert database.must_reset_column_absent()
    database.invalidate_user_cache()


class _PGRST204(Exception):
    """supabase-py's APIError shape for an INSERT/UPDATE naming a column PostgREST 14.5 does not know."""
    code = "PGRST204"
    details = None
    hint = None
    message = "Could not find the 'must_reset' column of 'user_roles' in the schema cache"

    def __str__(self):
        return str({"message": self.message, "code": self.code, "hint": None, "details": None})


@test("auth: PGRST204 on a role-row INSERT/UPDATE or the flag clear = column missing, retried without it")
def _():
    from app import database, user_auth
    writes: list[tuple[str, dict]] = []

    class _Tbl:
        def __init__(self):
            self.op = None
            self.payload = None

        def insert(self, payload):
            self.op, self.payload = "insert", payload
            return self

        def update(self, payload):
            self.op, self.payload = "update", payload
            return self

        def eq(self, *a):
            return self

        def execute(self):
            if "must_reset" in self.payload:
                raise _PGRST204()
            writes.append((self.op, dict(self.payload)))
            return self

    class _Client:
        def table(self, name):
            assert name == "user_roles"
            return _Tbl()

    assert database.missing_column_error(_PGRST204(), "must_reset")
    assert not database.missing_column_error(_PGRST204(), "status"), "another column's error must not match"
    assert not database.missing_column_error(RuntimeError("must_reset boom"), "must_reset")
    with _Patched((database, "get_client", lambda: _Client()), (user_auth, "get_client", lambda: _Client()),
                  (database, "_legacy_until", 0.0)):
        user_auth._write_role_row({"email": "n@example.com", "role": "salesman", "features": [], "status": "active",
                                   "must_reset": True}, insert=True)
        assert writes[-1][0] == "insert" and "must_reset" not in writes[-1][1] and writes[-1][1]["role"] == "salesman"
        assert database.must_reset_column_absent(), "a failed write must teach the reader the legacy shape too"
        database._legacy_until = 0.0
        user_auth._write_role_row({"email": "n@example.com", "role": "member", "features": [], "status": "active",
                                   "must_reset": False}, insert=False)
        assert writes[-1][0] == "update" and "must_reset" not in writes[-1][1]
        # known-absent: the payload never even carries the column (one round trip, not two)
        n = len(writes)
        user_auth._write_role_row({"email": "n@example.com", "role": "member", "features": [], "status": "active",
                                   "must_reset": True}, insert=True)
        assert len(writes) == n + 1 and "must_reset" not in writes[-1][1]
        database._legacy_until = 0.0
        user_auth._clear_must_reset("n@example.com")      # PGRST204 inside: no raise, no warning path
        assert database.must_reset_column_absent()
        database._legacy_until = 0.0
    database.invalidate_user_cache()


@test("auth: re-inviting a disabled (banned) member unbans the auth user in the same call")
def _():
    from app import user_auth
    updates: list[tuple[str, dict]] = []

    class _Admin:
        def update_user_by_id(self, uid, attrs):
            updates.append((uid, attrs))

        def create_user(self, attrs):
            raise AssertionError("an existing auth user must be updated, not re-created")

    class _Client:
        auth = type("A", (), {"admin": _Admin()})()

    existing = type("U", (), {"id": "uid-77", "email": "back@example.com"})()
    with _Patched((user_auth, "get_client", lambda: _Client()), (user_auth, "_find_auth_user", lambda e: existing),
                  (user_auth, "_upsert_role", lambda *a, **k: None)):
        user_auth.create_member("back@example.com", "Back Again", "salesman", ["Catalog"], "Yq-temp1234")
    assert len(updates) == 1 and updates[0][0] == "uid-77"
    assert updates[0][1]["ban_duration"] == "none" and updates[0][1]["password"] == "Yq-temp1234"
    assert updates[0][1]["user_metadata"]["must_reset"] is True


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


@test("auth: /agents/{name} — members only run agents behind their pages; the key and admins may email")
def _():
    from fastapi.testclient import TestClient
    import app.main as m
    from app import agents, auth, database
    from app.config import settings
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
    old_key = settings.agent_api_key
    settings.agent_api_key = "k-test-key-value"
    try:
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
            # the n8n agent flows (n8n_workflows/agents/*.json) call ?email=1 with X-Agent-Key
            hk = {"x-agent-key": "k-test-key-value"}
            r = c.get(f"/agents/{other}?email=1", headers=hk)
            assert r.status_code == 200 and len(mailed) == 2 and r.json().get("email") == {"sent": True}, r.text
            assert c.get(f"/agents/{other}", headers=hk).status_code == 200 and len(mailed) == 2
            assert c.get("/agents", headers=hk).status_code == 200
    finally:
        settings.agent_api_key = old_key
        database.invalidate_user_cache()


# ── rate limiting ──────────────────────────────────────────────────────────────

def _es256_token(kid: str, sub: str = "u9") -> str:
    """A syntactically valid ES256 token naming a `kid` the JWKS does not have (the shape that used
    to trigger a network JWKS refresh from the rate-limit key function)."""
    import jwt
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    return jwt.encode({"sub": sub, "email": f"{sub}@example.com", "aud": "authenticated",
                       "exp": int(time.time()) + 300}, key, algorithm="ES256", headers={"kid": kid})


@test("ratelimit: the key function never decodes — unknown tokens share the IP bucket, a token earns its own only after get_current_user verified it")
def _():
    from jwt import PyJWKClient
    from app import auth, database, ratelimit
    from app.config import settings
    old = (settings.trusted_proxy_hops, settings.supabase_jwt_secret)
    ratelimit.reset_cache()
    fetches: list[str] = []

    def _no_decode(tok):
        raise AssertionError("rate_limit_key must never decode a token")

    def _no_fetch(self):
        fetches.append(self.uri)
        raise AssertionError("rate_limit_key must never fetch the JWKS")

    try:
        settings.trusted_proxy_hops = 1
        settings.supabase_jwt_secret = "unit-test-secret"
        xff = {"x-forwarded-for": "1.2.3.4"}
        good = _token("u1", "unit-test-secret")
        unknown_kid = [_es256_token(f"kid-{i}") for i in range(5)]
        junk = ["Bearer eyJhbGciOi.first-token.signature-1234567890",
                "Bearer eyJhbGciOi.other-token.signature-1234567890", "Bearer x", "Bearer " + "a" * 400,
                "Bearer " + good, "Bearer " + _token("u1", "unit-test-secret", exp=int(time.time()) - 30),
                *("Bearer " + t for t in unknown_kid)]
        with _Patched((auth, "_decode_token", _no_decode), (PyJWKClient, "fetch_data", _no_fetch)):
            keys = {ratelimit.rate_limit_key(_req({"authorization": j, **xff})) for j in junk}
            assert keys == {"1.2.3.4"}, keys
            assert fetches == [], "an unknown-kid ES256 token caused a JWKS fetch from the key function"
            assert ratelimit.rate_limit_key(_req(xff)) == "1.2.3.4"
            assert ratelimit.rate_limit_key(_req({})) == "10.0.0.9"

        # get_current_user (threadpool) is the only place a token becomes a user bucket
        # (_fake_users decodes token text → "<text>@example.com"; real tokens are far longer than 20 chars)
        t = "user-one-access-token-0001"
        rows = {f"{t}@example.com": {"email": f"{t}@example.com", "role": "member", "status": "active", "features": []}}
        with _fake_users(rows):
            auth.get_current_user(_req(path="/feed"), _creds(t))
        with _Patched((auth, "_decode_token", _no_decode), (PyJWKClient, "fetch_data", _no_fetch)):
            k = ratelimit.rate_limit_key(_req({"authorization": "Bearer " + t, **xff}))
            assert k.startswith("u:"), k
            assert ratelimit.rate_limit_key(_req({"authorization": "Bearer " + t, "x-forwarded-for": "8.8.8.8"})) == k
            assert ratelimit.rate_limit_key(_req({"authorization": "Bearer " + t + "x", **xff})) == "1.2.3.4", "a near-miss is not the user"
            for p in ("/public/market/order", "/public/market/recognize", "/team/accept", "/team/invite/abc", "/health"):
                assert ratelimit.rate_limit_key(_req({"authorization": "Bearer " + t, **xff}, path=p)) == "1.2.3.4", p
            assert fetches == []
        # a decoded token with no role row (403) still earned its bucket — it IS a real token of
        # this project — but a token that never decoded never does
        with _fake_users({}):
            try:
                auth.get_current_user(_req(path="/feed"), _creds("nobody-access-token-0002"))
            except Exception:  # noqa: BLE001 — 403, no role row
                pass
        with _Patched((auth, "_decode_token", _no_decode)):
            assert ratelimit.rate_limit_key(_req({"authorization": "Bearer never-decoded-token-0003", **xff})) == "1.2.3.4"
        # the bounded cache never grows past its cap
        for i in range(ratelimit._CACHE_MAX + 50):
            ratelimit.remember_verified(f"token-number-{i:06d}-padding-to-length")
        assert len(ratelimit._verified) <= ratelimit._CACHE_MAX
    finally:
        settings.trusted_proxy_hops, settings.supabase_jwt_secret = old
        ratelimit.reset_cache()
        database.invalidate_user_cache()


@test("auth: an unknown kid refreshes the JWKS at most once per minute per process")
def _():
    import jwt
    from jwt.algorithms import ECAlgorithm
    from cryptography.hazmat.primitives.asymmetric import ec
    from app import auth
    known = ec.generate_private_key(ec.SECP256R1())
    jwks = {"keys": [{**ECAlgorithm.to_jwk(known.public_key(), as_dict=True), "kid": "known-kid", "use": "sig", "alg": "ES256"}]}
    fetches: list[float] = []

    class _Client(auth._ThrottledJWKClient):
        def fetch_data(self):
            fetches.append(time.monotonic())
            if self.jwk_set_cache is not None:
                self.jwk_set_cache.put(jwks)
            return jwks

    c = _Client("https://example.invalid/auth/v1/.well-known/jwks.json", cache_keys=True)
    good = jwt.encode({"sub": "ok", "aud": "authenticated", "exp": int(time.time()) + 300}, known,
                      algorithm="ES256", headers={"kid": "known-kid"})
    assert c.get_signing_key_from_jwt(good).key_id == "known-kid"
    assert len(fetches) == 1, "the first lookup fills the cache"
    for i in range(5):
        try:
            c.get_signing_key_from_jwt(_es256_token(f"bogus-{i}"))
            raise AssertionError("an unknown kid must not verify")
        except jwt.exceptions.PyJWKClientError:
            pass
    assert len(fetches) == 2, f"5 unknown kids must cost ONE refresh, not {len(fetches) - 1}"
    c._last_refresh -= auth.JWKS_REFRESH_MIN_INTERVAL_S + 1        # the window elapsed
    if getattr(c, "_last_successful_fetch", None) is not None:   # PyJWT >= 2.14 has its own cooldown too
        c._last_successful_fetch -= max(getattr(c, "cooldown_duration", 0), auth.JWKS_REFRESH_MIN_INTERVAL_S) + 1
    try:
        c.get_signing_key_from_jwt(_es256_token("bogus-later"))
    except jwt.exceptions.PyJWKClientError:
        pass
    assert len(fetches) == 3, "after the window one more refresh is allowed"
    assert c.get_signing_key_from_jwt(good).key_id == "known-kid" and len(fetches) == 3, "known keys still resolve from cache"
    assert isinstance(auth._jwks_client(), auth._ThrottledJWKClient), "the app's client must be the throttled one"


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
    # create_order's own 'order' event passes the ORDER's rep as the trusted keyword (a checkout
    # pick or the default rep has no referral code to derive it from) and never in the body
    import ast, inspect
    fn = ast.parse(inspect.getsource(shop.create_order))
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "record_event"]
    assert len(calls) == 1, "create_order records exactly one funnel event"
    call = calls[0]
    assert any(k.arg == "salesman_id" for k in call.keywords), "create_order must pass salesman_id= as the trusted keyword"
    body = call.args[0]
    assert isinstance(body, ast.Dict) and "salesman_id" not in [getattr(k, "value", None) for k in body.keys], \
        "salesman_id in the body is ignored by record_event — it must not be there"


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
        # a FAILED load (purchase_costs upsert blew up after mrn_lines were written) is audited too
        with _Patched((ingest_mrn, "load_mrn_costs", lambda r: (_ for _ in ()).throw(RuntimeError("purchase_costs upsert: 23505")))):
            r = c.post("/orders/YQ-26-09-2/file", headers={"authorization": "Bearer boss"}, files=files)
        assert r.status_code == 200 and r.json()["ok"] and r.json()["processed"] is None, r.text
        assert "loading costs failed" in r.json()["skipped"] and "23505" in r.json()["skipped"]
        failed = [a for a in audit if a[1] == "cost_load_failed"]
        assert len(failed) == 1 and failed[0][0] == "boss@example.com"
        assert failed[0][2]["po_no"] == "YQ-26-09-2" and failed[0][2]["source"] == "mrn" and "23505" in failed[0][2]["error"]
        assert stored[-1] == ("YQ-26-09-2", "mrn", "boss@example.com"), "the file is still stored"
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
        # a CTE body that is not a SELECT: sqlglot parses `(TABLE t)` as a Column named TABLE
        # aliased to the relation — no exp.Table node for the allowlist to see (review of R1)
        'WITH x AS (TABLE customer_contacts) SELECT * FROM x',
        'WITH x AS MATERIALIZED (TABLE customer_contacts) SELECT * FROM x',
        'WITH x AS NOT MATERIALIZED (TABLE customer_contacts) SELECT * FROM x',
        'WITH x AS (table app_settings) SELECT * FROM x',
        'WITH x AS ((SELECT * FROM v_sales)) SELECT * FROM x',
        'SELECT * FROM (TABLE leads) t',
        # LIMIT / FETCH operands that are not a plain whole number
        'SELECT * FROM v_sales LIMIT (SELECT 100000)',
        'SELECT * FROM (SELECT * FROM v_sales LIMIT 5) t LIMIT (SELECT 100000)',
        'SELECT * FROM v_sales LIMIT 5 + 5',
        "SELECT * FROM v_sales LIMIT '5'",
        'SELECT * FROM v_sales LIMIT -5',
        'SELECT * FROM v_sales LIMIT 1e3',
        'SELECT * FROM v_sales FETCH FIRST (SELECT 1) ROWS ONLY',
        'SELECT * FROM v_sales FETCH FIRST 10 PERCENT ROWS ONLY',
    ]
    for sql in cases:
        try:
            validate(sql)
            raise AssertionError(f"accepted: {sql}")
        except SQLValidationError:
            pass
    for sql in ('WITH x AS (TABLE customer_contacts) SELECT * FROM x',
                'WITH x AS MATERIALIZED (TABLE customer_contacts) SELECT * FROM x'):
        try:
            validate(sql, {"Catalog"})
            raise AssertionError(f"accepted under feature scope: {sql}")
        except SQLValidationError:
            pass


def _top_rows(sql: str) -> int:
    """The OUTER row cap of validated SQL, read back through the parser (not a regex)."""
    import sqlglot
    from sqlglot import exp
    top = sqlglot.parse_one(sql, read="postgres").args.get("limit")
    node = top.expression if isinstance(top, exp.Limit) else top.args.get("count")
    return int(node.this)


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
        out = validate(sql)
        assert out.endswith(f"LIMIT {MAX_ROWS}") and _top_rows(out) == MAX_ROWS, sql
    assert _top_rows(validate("SELECT * FROM v_sales LIMIT 5000")) == MAX_ROWS
    assert _top_rows(validate("SELECT * FROM v_sales LIMIT 5")) == 5
    out = validate("SELECT * FROM (SELECT * FROM v_sales LIMIT 5) t")
    assert out.endswith(f"LIMIT 5) AS t LIMIT {MAX_ROWS}") and _top_rows(out) == MAX_ROWS, out
    assert validate("SELECT * FROM v_sales ORDER BY 1 LIMIT 10 OFFSET 5").endswith("LIMIT 10 OFFSET 5")
    assert _top_rows(validate("SELECT * FROM v_sales LIMIT ALL")) == MAX_ROWS
    assert validate("SELECT * FROM v_sales FETCH FIRST 500 ROWS ONLY").endswith(f"FETCH FIRST {MAX_ROWS} ROWS ONLY")
    assert validate("SELECT * FROM v_sales FETCH NEXT 5 ROWS ONLY").endswith("FETCH NEXT 5 ROWS ONLY")
    assert _top_rows(validate("SELECT * FROM v_sales LIMIT 0")) == 0
    # the three textual-clamp bypasses from the review, now clamped on the tree
    out = validate("SELECT * FROM (SELECT * FROM v_sales FETCH FIRST 10 ROWS ONLY) t FETCH FIRST 100000 ROWS ONLY")
    assert _top_rows(out) == MAX_ROWS and "FETCH FIRST 10 ROWS ONLY" in out, out
    out = validate("SELECT * FROM v_sales LIMIT 100000 /* LIMIT 5 */")
    assert _top_rows(out) == MAX_ROWS and "100000" not in out, out
    out = validate("SELECT * FROM v_sales LIMIT 100000 -- LIMIT 5")
    assert _top_rows(out) == MAX_ROWS and "100000" not in out, out
    out = validate("SELECT * FROM v_sales UNION ALL SELECT * FROM v_sales LIMIT 9999")
    assert _top_rows(out) == MAX_ROWS, out
    out = validate("SELECT * FROM v_sales LIMIT 5000 OFFSET 3")
    assert _top_rows(out) == MAX_ROWS and out.endswith("OFFSET 3"), out
    # a trailing line comment can no longer swallow the injected LIMIT
    out = validate("SELECT * FROM v_sales -- top sellers")
    assert _top_rows(out) == MAX_ROWS and "--" not in out, out


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


@test("auth: POST /auth/password — must_reset-exempt, clears the flag; the current password is required otherwise")
def _():
    from fastapi.testclient import TestClient
    import app.main as m
    from app import database, user_auth
    rows = {"temp@example.com": {"email": "temp@example.com", "role": "salesman", "status": "active",
                                 "features": ["Catalog"], "must_reset": True},
            "settled@example.com": {"email": "settled@example.com", "role": "member", "status": "active",
                                    "features": ["Sales"], "must_reset": False}}
    changed: list[tuple] = []
    checked: list[tuple] = []

    def _check(email, pw):
        checked.append((email, pw))
        return pw == "the-current-one"

    with _fake_users(rows), _Patched((user_auth, "set_password", lambda e, p: changed.append((e, p)) or True),
                                     (user_auth, "check_password", _check),
                                     (m, "log_event", lambda *a, **k: None)):
        c = TestClient(m.app)
        h = {"authorization": "Bearer temp"}
        r = c.get("/shop/catalog", headers=h)
        assert r.status_code == 403 and r.json()["detail"]["code"] == "password_change_required", r.text
        assert c.get("/me", headers=h).json()["must_reset"] is True
        assert c.post("/auth/password", headers=h, json={"password": "short"}).status_code == 400
        r = c.post("/auth/password", headers=h, json={"password": "a-proper-password"})
        assert r.status_code == 200 and r.json() == {"ok": True, "must_reset": False}, r.text
        assert changed == [("temp@example.com", "a-proper-password")]
        assert checked == [], "a temporary-password login is not asked for the current password"
        # a settled login must prove the current password: a stolen token alone changes nothing
        hs = {"authorization": "Bearer settled"}
        r = c.post("/auth/password", headers=hs, json={"password": "a-proper-password"})
        assert r.status_code == 400 and "current" in r.json()["detail"].lower(), r.text
        r = c.post("/auth/password", headers=hs, json={"password": "a-proper-password", "current_password": "wrong-guess"})
        assert r.status_code == 403, r.text
        assert len(changed) == 1, "nothing changed on a wrong current password"
        r = c.post("/auth/password", headers=hs, json={"password": "a-proper-password", "current_password": "the-current-one"})
        assert r.status_code == 200, r.text
        assert changed[-1] == ("settled@example.com", "a-proper-password")
        assert checked == [("settled@example.com", "wrong-guess"), ("settled@example.com", "the-current-one")]
    database.invalidate_user_cache()


@test("sql: escape strings, dollar quoting and backslashes are refused; the SQL that runs re-validates to the same relations")
def _():
    from app.sql_validator import SQLValidationError, validate
    bad = [
        "SELECT E'a\\\\' AS x FROM v_sales",              # the E-string rewrite the re-review found
        "SELECT e'x' FROM v_sales",
        "SELECT U&'d\\0061t' FROM v_sales",
        "SELECT $$x$$ FROM v_sales",
        "SELECT $tag$x$tag$ FROM v_sales",
        "SELECT 'a\\b' FROM v_sales",
    ]
    for q in bad:
        try:
            validate(q)
        except SQLValidationError:
            continue
        raise AssertionError(f"accepted: {q!r}")
    ok = validate("SELECT item_name, 'Apple' AS brand FROM v_sales WHERE item_name ILIKE 'e%'")
    assert ok.upper().startswith("SELECT") and "LIMIT" in ok.upper(), ok


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
