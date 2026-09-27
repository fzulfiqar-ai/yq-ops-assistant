"""R7a stream B5 — the Management role v0 and owner protection (27-Sep-2026) — pure tests, no database.

    python -m tests.test_r7a_rbac

Same lightweight runner as tests/test_r1_security.py. Nothing here reaches a database, Supabase
Auth or the network: user_roles, app_invites, the two audit tables and the auth admin API are an
in-memory stand-in, the token decoder is stubbed (token text = the email's local part), and every
address is synthetic (@example.com).

Covered:
  * features — 'management' is offered (label "Management", six read pages, a hard page limit);
    operations / sales_manager / finance are reserved, not offered; the phone / email masks;
  * the migration — its CHECK accepts every offered, reserved and legacy role, the reverse restores
    the six legacy roles and refuses while a row or a pending invite still uses a new one;
  * the central gate — app.auth refuses every non-GET request from management with
    {"code": "read_only"} except POST /auth/password, and the AI surfaces on GET too; must_reset
    still wins; other roles are untouched;
  * the route sweep — EVERY POST / PUT / PATCH / DELETE route in app.routes answers 403 read_only
    to a management login, except the allowlist (the own password change, which works);
  * company-wide order reads — management lists and opens every order with the merchant's phone and
    email masked, no WhatsApp link, order token or notify recipients, nothing to move; the order
    write routes refuse management even with the central gate bypassed; a rep still sees only his;
  * the page limit — a management row that lists the AI tools, Shop Admin or Marketing is still
    refused them; /me says read_only and lists the six pages; /agents is refused;
  * the team API — unknown role / page / status (400), the page limit (400), the owner (409 on
    role, pages, status, remove, re-invite; a no-op save passes), the last active admin (409),
    a before/after audit row for every change (shop_admin_audit entity 'user', audit_log when that
    table is missing; never a password or token), Management before the migration (400 naming the
    file, before any auth user is touched; a CHECK violation is translated when the probe cannot
    read the constraint), accept_invite under the same rules, /team marks the owner.
"""
from __future__ import annotations

import io
import json
import re
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

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


# ── an in-memory PostgREST + Supabase Auth stand-in ────────────────────────────

LEGACY_ROLES = ("admin", "member", "manager", "viewer", "salesman", "storekeeper")
NEW_ROLES = ("management", "operations", "sales_manager", "finance")


def _check_def(roles) -> str:
    """pg_get_constraintdef's spelling of user_roles_role_check."""
    return "CHECK ((role = ANY (ARRAY[" + ", ".join(f"'{r}'::text" for r in roles) + "])))"


class _CheckViolation(Exception):
    code = "23514"
    message = 'new row for relation "user_roles" violates check constraint "user_roles_role_check"'


class _Query:
    def __init__(self, db: "_FakeDB", table: str):
        self.db, self.table = db, table
        self.op, self.payload, self.filters, self.lim = "select", None, [], None

    def select(self, *_a, **_k):
        self.op = "select"
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, col, val):
        self.filters.append((col, lambda got, v=val: str(got) == str(v)))
        return self

    def ilike(self, col, val):
        self.filters.append((col, lambda got, v=val: str(got or "").lower() == str(v).strip("%").lower()))
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, n):
        self.lim = n
        return self

    def range(self, a, b):
        self.lim = b - a + 1
        return self

    def _match(self, row) -> bool:
        return all(f(row.get(col)) for col, f in self.filters)

    def execute(self):
        db = self.db
        if self.table in db.missing:
            raise RuntimeError(f'relation "public.{self.table}" does not exist')
        rows = db.tables.setdefault(self.table, [])
        if self.op in ("insert", "update"):
            db.writes.append((self.op, self.table, dict(self.payload)))
            if self.table == "user_roles" and "role" in self.payload and self.payload["role"] not in db.allowed_roles:
                raise _CheckViolation(_CheckViolation.message)
        if self.op == "select":
            out = [dict(r) for r in rows if self._match(r)]
            return SimpleNamespace(data=out[: self.lim] if self.lim else out, count=len(out))
        if self.op == "insert":
            r = dict(self.payload)
            r.setdefault("id", len(rows) + 1)
            rows.append(r)
            return SimpleNamespace(data=[dict(r)], count=None)
        if self.op == "update":
            hit = [r for r in rows if self._match(r)]
            for r in hit:
                r.update(self.payload)
            return SimpleNamespace(data=[dict(r) for r in hit], count=None)
        gone = [r for r in rows if self._match(r)]
        db.writes.append(("delete", self.table, {"n": len(gone)}))
        rows[:] = [r for r in rows if not self._match(r)]
        return SimpleNamespace(data=gone, count=None)


class _AuthAdmin:
    def __init__(self, emails):
        self.users = [SimpleNamespace(id=f"uid-{i}", email=e, user_metadata={}) for i, e in enumerate(emails)]
        self.calls: list[tuple] = []

    def list_users(self):
        return list(self.users)

    def create_user(self, attrs):
        self.calls.append(("create", attrs["email"]))
        self.users.append(SimpleNamespace(id=f"uid-{len(self.users)}", email=attrs["email"],
                                          user_metadata=attrs.get("user_metadata") or {}))

    def update_user_by_id(self, uid, attrs):
        self.calls.append(("update", uid, tuple(sorted(attrs))))

    def delete_user(self, uid):
        self.calls.append(("delete", uid))


class _FakeDB:
    def __init__(self, users: list[dict], *, allowed_roles=LEGACY_ROLES + NEW_ROLES, check_roles=None,
                 missing=(), rpc_error: Exception | None = None, invites: list[dict] | None = None):
        self.tables: dict[str, list[dict]] = {"user_roles": [dict(u) for u in users],
                                              "app_invites": [dict(i) for i in invites or []]}
        self.allowed_roles = tuple(allowed_roles)
        self.check_def = _check_def(check_roles if check_roles is not None else allowed_roles)
        self.missing = set(missing)
        self.rpc_error = rpc_error
        self.writes: list[tuple] = []
        self.rpcs: list[str] = []
        self.auth = SimpleNamespace(admin=_AuthAdmin([u["email"] for u in users]))

    def table(self, name):
        return _Query(self, name)

    def rpc(self, name, params=None):
        self.rpcs.append(name)
        if self.rpc_error is not None:
            raise self.rpc_error
        sql = (params or {}).get("sql_text", "")
        data = [{"def": self.check_def}] if "user_roles_role_check" in sql else []
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=data))

    def rows(self, table):
        return self.tables.get(table, [])

    def audit(self) -> list[dict]:
        return [w[2] for w in self.writes if w[0] == "insert" and w[1] == "shop_admin_audit"]

    def audit_log(self, event: str | None = None) -> list[dict]:
        return [w[2] for w in self.writes if w[0] == "insert" and w[1] == "audit_log"
                and (event is None or w[2].get("event") == event)]


class _world:
    """Point every client lookup (auth, user_auth, the audit helpers, the read-only RPC) at the fake;
    stub the token decoder (token = local part) and the invite email; set the owner list."""

    def __init__(self, fake: _FakeDB, owners=("owner@example.com",)):
        self.fake, self.owners = fake, list(owners)

    def __enter__(self):
        from app import auth, database, db_read, user_auth
        from app.config import settings
        import app.main as m
        self.m = m
        self.old_owners = settings.owner_emails
        settings.owner_emails = self.owners
        user_auth._role_check.update(at=float("-inf"), definition=None)
        database.invalidate_user_cache()
        self.p = _Patched(
            (database, "get_client", lambda: self.fake), (user_auth, "get_client", lambda: self.fake),
            (db_read, "get_client", lambda: self.fake),
            (auth, "_decode_token", lambda tok: {"sub": "uid-" + tok, "email": tok + "@example.com"}),
            (user_auth, "_send_invite_email", lambda *a, **k: {"emailed": False, "reason": "test"}),
            (m.limiter, "enabled", False),
        )
        self.p.__enter__()
        return self.fake

    def __exit__(self, *exc):
        from app import database, user_auth
        from app.config import settings
        self.p.__exit__(*exc)
        settings.owner_emails = self.old_owners
        user_auth._role_check.update(at=float("-inf"), definition=None)
        database.invalidate_user_cache()
        return False


def _client():
    from fastapi.testclient import TestClient
    import app.main as m
    return TestClient(m.app)


def _h(tok: str) -> dict:
    return {"authorization": f"Bearer {tok}"}


def _row(email, role, features=None, status="active", **extra) -> dict:
    return {"email": email, "role": role, "features": list(features or []), "status": status,
            "full_name": email.split("@")[0].title(), **extra}


SIX = ["Dashboard", "Sales", "Margins", "Receivables", "Inventory", "Shop Orders"]


def _team() -> list[dict]:
    from app.features import FEATURES
    return [
        _row("owner@example.com", "admin", FEATURES),
        _row("boss@example.com", "admin", FEATURES),
        _row("mgmt@example.com", "management", SIX),
        _row("rep@example.com", "salesman", ["Catalog", "Shop Orders"]),
        _row("clerk@example.com", "member", ["Dashboard", "Sales"]),
    ]


# ── features ───────────────────────────────────────────────────────────────────

@test("features: management is offered as 'Management' with six read pages and a hard page limit")
def _():
    from app import features as f
    assert "management" in f.ROLES and f.ROLE_LABELS["management"] == "Management"
    assert set(f.ROLE_LABELS) == set(f.ROLES), "every offered role has a label"
    assert f.ROLE_DEFAULT_FEATURES["management"] == SIX
    assert all(x in f.FEATURES for x in SIX)
    assert f.is_read_only("management") and not any(f.is_read_only(r) for r in ("admin", "member", "salesman", "storekeeper"))
    for page in ("AI Agents", "AI Assistant", "Shop Admin", "Marketing", "Leads", "Storekeeper"):
        assert not f.may_hold("management", page), page
    assert all(f.may_hold("management", p) for p in SIX)
    assert all(f.may_hold("member", p) for p in f.FEATURES), "other roles have no limit"
    assert not set(f.RESERVED_ROLES) & set(f.ROLES), "operations / sales_manager / finance are not offered yet"
    assert f.masks_contacts("management") and not f.masks_contacts("admin") and not f.masks_contacts("salesman")


@test("features: the phone and email masks keep only what identifies nothing")
def _():
    from app.features import mask_email, mask_phone
    assert mask_phone("+973 3312 3456") == "+973 ••••• 456"
    assert mask_phone("97333123456") == "+973 ••••• 456"
    assert mask_phone("33123456") == "••••• 456"
    assert mask_phone("12") == "•••••" and mask_phone(None) is None and mask_phone("") == ""
    assert mask_email("orders@example.com") == "o•••••@example.com"
    assert mask_email("nope") == "•••••" and mask_email(None) is None
    for raw in ("+973 3312 3456", "33123456"):
        assert "3312" not in mask_phone(raw)


@test("migration: the CHECK accepts every offered, reserved and legacy role; the reverse restores six and refuses while used")
def _():
    from app import features as f
    mig = (ROOT / "scripts/r7_rbac_migration.sql").read_text(encoding="utf-8")
    rev = (ROOT / "scripts/r7_rbac_reverse.sql").read_text(encoding="utf-8")

    def check_roles(sql: str) -> set[str]:
        m = re.search(r"add constraint user_roles_role_check\s+check \(role in \(([^)]*)\)\)", sql, re.S)
        assert m, "no user_roles_role_check in the file"
        return set(re.findall(r"'([a-z_]+)'", m.group(1)))

    new = check_roles(mig)
    assert set(f.ROLES) | set(f.RESERVED_ROLES) | set(LEGACY_ROLES) <= new, new
    assert check_roles(rev) == set(LEGACY_ROLES)
    from app.user_auth import _LEGACY_DB_ROLES
    assert _LEGACY_DB_ROLES == set(LEGACY_ROLES), "the probe's legacy set is the reverse's CHECK"
    for sql in (mig, rev):
        low = sql.lower()
        assert "cascade" not in low and "security_invoker" not in low
        assert not re.search(r"grant\s+[^;]*\bto\s+(anon|authenticated)", low), "nothing is granted to the browser roles"
        assert "delete from" not in low and not re.search(r"^\s*update\s", low, re.M), "no row is rewritten"
    assert "raise exception 'r7_rbac reverse refused" in rev and "app_invites" in rev
    assert mig.index("drop constraint if exists user_roles_role_check") < mig.index("add constraint user_roles_role_check")


# ── the central gate (app/auth.py) ─────────────────────────────────────────────

@test("auth: read_only_refuses — reads pass, writes are refused, the own password is allowed, the AI surfaces never")
def _():
    from app.auth import READ_ONLY_WRITE_ALLOWLIST, read_only_refuses
    # R7b: management approves a Market Intel action (plan §7) — the one decision it makes
    assert READ_ONLY_WRITE_ALLOWLIST == {("POST", "/auth/password"), ("POST", "/market-intel/items/{item_id}/approve")}
    for meth in ("GET", "HEAD", "OPTIONS", "get"):
        assert not read_only_refuses(meth, "/shop/orders"), meth
    for meth in ("POST", "PUT", "PATCH", "DELETE"):
        assert read_only_refuses(meth, "/shop/orders/1/status"), meth
    assert not read_only_refuses("POST", "/auth/password")
    assert read_only_refuses("PUT", "/auth/password") and read_only_refuses("POST", "/auth/password/x")
    for p in ("/agents", "/agents/collections", "/ask", "/ask/stream", "/orchestrate", "/assistant/upload", "/field-notes",
              "/coaching", "/coaching/brief"):
        assert read_only_refuses("GET", p), p
    assert not read_only_refuses("GET", "/agentsx"), "a prefix only matches on a segment boundary"


@test("auth: get_current_user refuses a management write with code read_only; must_reset still wins; others untouched")
def _():
    from fastapi import HTTPException
    from starlette.requests import Request
    from app import auth

    def req(method, path):
        return Request({"type": "http", "method": method, "path": path, "query_string": b"", "headers": [],
                        "client": ("10.0.0.9", 1), "server": ("testserver", 80), "scheme": "http"})

    def creds(tok):
        from fastapi.security import HTTPAuthorizationCredentials
        return HTTPAuthorizationCredentials(scheme="Bearer", credentials=tok)

    def outcome(tok, method, path):
        try:
            return auth.get_current_user(req(method, path), creds(tok)).role
        except HTTPException as e:
            return (e.status_code, e.detail.get("code") if isinstance(e.detail, dict) else e.detail)

    users = _team() + [_row("newmgmt@example.com", "management", SIX, must_reset=True)]
    with _world(_FakeDB(users)):
        assert outcome("mgmt", "GET", "/shop/orders") == "management"
        assert outcome("mgmt", "POST", "/shop/orders/1/status") == (403, "read_only")
        assert outcome("mgmt", "DELETE", "/team/rep@example.com") == (403, "read_only")
        assert outcome("mgmt", "POST", "/auth/password") == "management"
        assert outcome("mgmt", "GET", "/agents") == (403, "read_only")
        assert outcome("newmgmt", "POST", "/shop/orders/1/status") == (403, "password_change_required"), \
            "a temporary password is answered first: the SPA sends the login to its password screen"
        assert outcome("newmgmt", "POST", "/auth/password") == "management"
        for tok, role in (("boss", "admin"), ("rep", "salesman"), ("clerk", "member")):
            assert outcome(tok, "POST", "/shop/orders/1/status") == role, tok
            assert outcome(tok, "GET", "/agents") == role, tok


def _auth_calls(dependant, auth) -> bool:
    return any(d.call in (auth.get_current_user, auth.get_caller) or _auth_calls(d, auth) for d in dependant.dependencies)


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


@test("routes: EVERY POST/PUT/PATCH/DELETE route answers 403 read_only to management, except POST /auth/password")
def _():
    from fastapi.routing import APIRoute
    import app.main as m
    from app import auth, user_auth
    mutating: list[tuple[str, str]] = []
    public: list[tuple[str, str]] = []
    for r in m.app.routes:
        if not isinstance(r, APIRoute):
            continue
        for meth in sorted(r.methods & {"POST", "PUT", "PATCH", "DELETE"}):
            (mutating if _auth_calls(r.dependant, auth) else public).append((meth, r.path))
    assert len(mutating) > 80, f"the sweep found only {len(mutating)} routes"
    bad_public = [f"{a} {p}" for a, p in public if not (p.startswith("/public/") or p == "/team/accept")]
    assert not bad_public, f"a write route with no login: {bad_public}"
    assert ("POST", "/auth/password") in mutating
    changed: list[tuple] = []
    with _world(_FakeDB(_team())) as fake, _Patched(
            (user_auth, "check_password", lambda e, p: True),
            (user_auth, "set_password", lambda e, p: changed.append((e, p)) or True),
            (m, "log_event", lambda *a, **k: None)):
        c = _client()
        refused, leaked = [], []
        for meth, path in mutating:
            if (meth, path) in auth.READ_ONLY_WRITE_ALLOWLIST:
                continue
            resp = c.request(meth, _concrete(path), headers=_h("mgmt"), json={})
            code = None
            try:
                code = (resp.json().get("detail") or {}).get("code")
            except Exception:  # noqa: BLE001
                pass
            (refused if resp.status_code == 403 and code == "read_only" else leaked).append(
                (meth, path, resp.status_code, resp.text[:80]))
        assert not leaked, f"management reached: {leaked}"
        assert fake.writes == [], f"a refused request still wrote: {fake.writes}"
        # the allowlist works: the own password change goes through
        r = c.post("/auth/password", headers=_h("mgmt"), json={"password": "a-new-password-1", "current_password": "old-one"})
        assert r.status_code == 200 and changed == [("mgmt@example.com", "a-new-password-1")], r.text[:200]
        # the same sweep sample as an admin is never refused as read_only (the gate is the role's, not the route's)
        r = c.request("PATCH", "/team/rep@example.com", headers=_h("boss"), json={"status": "active"})
        assert r.status_code == 200, r.text[:200]


# ── company-wide order reads, masked ───────────────────────────────────────────

def _orders():
    base = {"status": "new", "customer_email": None, "total_bhd": 25.0, "customer_area": "Manama",
            "lines": [], "events": [], "token": "tok-" + "x" * 30, "notify_result": None}
    return [
        {**base, "id": 11, "order_no": "YQ-2609-0011", "salesman_id": 1, "customer_name": "Shop One",
         "customer_shop": "Shop One", "customer_phone": "+97333000111", "customer_email": "one@example.com",
         # emailer.send_html's real answer shape: addresses in to / results / failed / reason (review R7a)
         "notify_result": {"email_rep": {"sent": True, "to": "rep@example.com", "results": [{"to": "rep@example.com"}]},
                           "customer_email": {"emailed": False, "to": "one@example.com",
                                              "results": [{"to": "one@example.com", "error": "403"}],
                                              "failed": ["one@example.com"],
                                              "reason": "one@example.com: resend_error 403 testing mode"},
                           "recipients": ["rep@example.com", "owner@example.com"]}},
        {**base, "id": 12, "order_no": "YQ-2609-0012", "salesman_id": 2, "customer_name": "Shop Two",
         "customer_shop": "Shop Two", "customer_phone": "33000222", "status": "confirmed"},
        {**base, "id": 13, "order_no": "YQ-2609-0013", "salesman_id": None, "customer_name": "Shop Three",
         "customer_shop": "Shop Three", "customer_phone": "+973 3300 0333"},
    ]


class _shop_stubs:
    """The order routes' shop calls, answered from _orders() (the list honours salesman_id)."""

    def __init__(self):
        self.list_scopes: list = []
        self.search_phone: list = []

    def __enter__(self):
        import copy
        from app import attribution, shop, shop_notify
        rows = _orders()
        by_id = {o["id"]: o for o in rows}

        def list_orders(status=None, q=None, limit=50, offset=0, salesman_id=None, search_phone=True):
            self.list_scopes.append(salesman_id)
            self.search_phone.append(search_phone)
            out = [{k: v for k, v in o.items() if k not in ("lines", "events", "token", "customer_email", "notify_result")}
                   for o in rows if salesman_id is None or o["salesman_id"] == salesman_id]
            return {"orders": out, "count": len(out), "counts": {}, "min_order_bhd": 20.0}

        self.p = _Patched(
            (shop, "list_orders", list_orders),
            (shop, "get_order", lambda oid: copy.deepcopy(by_id.get(int(oid)))),
            (shop, "market_base", lambda: "https://market.example.com"),
            (shop, "salesman_for_user", lambda e: {"id": 1, "referral_code": "one"} if e == "rep@example.com" else None),
            (shop, "recent_customers", lambda sid=None, limit=20: [
                {"name": o["customer_name"], "phone": o["customer_phone"], "email": o["customer_email"]}
                for o in rows if sid is None or o["salesman_id"] == sid]),
            (shop, "analytics", lambda days=30, salesman=None: {"days": days, "orders": 3 if salesman is None else 1}),
            (shop_notify, "salesman_to_customer_wa_url", lambda o, status=None: "https://wa.me/97333000111?text=hi"),
            (attribution, "customer_rep", lambda cid: {"id": cid}),
        )
        self.p.__enter__()
        return self

    def __exit__(self, *exc):
        self.p.__exit__(*exc)
        return False


@test("orders: management lists and opens EVERY order, phone and email masked, no WhatsApp link, token or recipients")
def _():
    raw_digits = re.compile(r"33\s?000\s?(111|222|333)|3300\s?0")
    with _world(_FakeDB(_team())), _shop_stubs() as st:
        c = _client()
        r = c.get("/shop/orders", headers=_h("mgmt"))
        assert r.status_code == 200, r.text[:200]
        body = r.json()
        assert st.list_scopes[-1] is None, "management reads company-wide (no salesman filter)"
        assert st.search_phone[-1] is False, "a masked role never searches phones (digit probing would unmask them)"
        assert [o["id"] for o in body["orders"]] == [11, 12, 13]
        assert [o["customer_phone"] for o in body["orders"]] == ["+973 ••••• 111", "••••• 222", "+973 ••••• 333"]
        assert not raw_digits.search(r.text), "no raw phone in the list payload"
        for oid in (11, 12, 13):          # other reps' orders and the unassigned one alike
            d = c.get(f"/shop/orders/{oid}", headers=_h("mgmt"))
            assert d.status_code == 200, (oid, d.text[:200])
            o = d.json()
            assert "•••••" in o["customer_phone"] and not raw_digits.search(d.text), (oid, o["customer_phone"])
            for k in ("whatsapp_url", "token", "status_url"):
                assert k not in o, (oid, k)
            assert o["next_statuses"] == [], "management has nothing to move"
        o = c.get("/shop/orders/11", headers=_h("mgmt")).json()
        assert o["customer_email"] == "o•••••@example.com"
        nr = o["notify_result"]
        assert "recipients" not in nr, "the notify recipients are not management's"
        assert nr["email_rep"] == {"sent": True, "kept": None, "reason": None}
        assert nr["customer_email"]["sent"] is False and "***@example.com" in nr["customer_email"]["reason"]
        raw = c.get("/shop/orders/11", headers=_h("mgmt")).text
        assert "one@example.com" not in raw and "rep@example.com" not in raw, "no address survives anywhere"
        assert "customer" not in o, "the admin-only shop-rep drawer stays admin-only"
        # an admin still sees everything unmasked; a rep still sees only his own orders
        a = c.get("/shop/orders/12", headers=_h("boss")).json()
        assert a["customer_phone"] == "33000222" and a["whatsapp_url"].startswith("https://wa.me/") and a["token"]
        assert c.get("/shop/orders", headers=_h("boss")).json()["orders"][1]["customer_phone"] == "33000222"
        assert st.search_phone[-1] is True, "an admin still finds an order by phone"
        rep = c.get("/shop/orders", headers=_h("rep")).json()
        assert st.list_scopes[-1] == 1 and [x["id"] for x in rep["orders"]] == [11]
        assert rep["orders"][0]["customer_phone"] == "+97333000111", "a rep's own shops are not masked"
        assert c.get("/shop/orders/12", headers=_h("rep")).status_code == 404
        # the quick-pick and the analytics: masked, company-wide
        cust = c.get("/shop/customers", headers=_h("mgmt")).json()["customers"]
        assert len(cust) == 3 and all("•••••" in x["phone"] for x in cust)
        assert cust[0]["email"] == "o•••••@example.com"
        assert c.get("/shop/analytics", headers=_h("mgmt")).json()["orders"] == 3
        # the writes: refused by the central gate
        for meth, path, body_ in (("POST", "/shop/orders/12/status", {"status": "confirmed"}),
                                  ("POST", "/shop/orders/12/confirm", {"lines": []}),
                                  ("POST", "/shop/orders/13/assign", {"salesman_id": 1})):
            w = c.request(meth, path, headers=_h("mgmt"), json=body_)
            assert w.status_code == 403 and w.json()["detail"]["code"] == "read_only", (path, w.text[:200])


@test("orders: with the central gate bypassed, the order write routes still refuse management (defence in depth)")
def _():
    import app.main as m
    from app import shop
    from app.auth import CurrentUser, get_current_user
    moved: list = []
    try:
        m.app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id="u", email="mgmt@example.com",
                                                                           role="management")
        with _world(_FakeDB(_team())), _shop_stubs(), _Patched(
                (shop, "set_status", lambda *a, **k: moved.append(a) or {}),
                (shop, "confirm_order", lambda *a, **k: moved.append(a) or {})):
            c = _client()
            assert c.post("/shop/orders/12/status", json={"status": "confirmed"}).status_code == 404
            assert c.post("/shop/orders/12/confirm", json={"lines": []}).status_code == 404
            assert c.get("/shop/orders/12").status_code == 200, "reading stays open"
            assert moved == []
    finally:
        m.app.dependency_overrides.pop(get_current_user, None)


@test("pages: a management row that lists the AI tools, Shop Admin or Marketing is still refused them; /me is read_only")
def _():
    from app import reports
    tampered = [r for r in _team() if r["email"] != "mgmt@example.com"] + [
        _row("mgmt@example.com", "management", SIX + ["AI Assistant", "AI Agents", "Shop Admin", "Marketing", "Leads"])]
    with _world(_FakeDB(tampered)), _Patched((reports, "cached_report", lambda key: {"report": key})):
        c = _client()
        me = c.get("/me", headers=_h("mgmt")).json()
        assert me["role"] == "management" and me["read_only"] is True and me["features"] == SIX, me
        assert c.get("/me", headers=_h("boss")).json()["read_only"] is False
        for path in ("/report/sales", "/report/margins", "/report/receivables", "/report/inventory"):
            assert c.get(path, headers=_h("mgmt")).status_code == 200, path
        for path in ("/shop/rules", "/shop/salesmen", "/outreach/queue", "/leads", "/field-notes", "/feed"):
            assert c.get(path, headers=_h("mgmt")).status_code == 403, path
        for path in ("/team", "/settings/shop", "/shop/statements", "/data/coverage", "/shop/audit", "/digest/daily"):
            assert c.get(path, headers=_h("mgmt")).status_code == 403, path
        r = c.get("/agents", headers=_h("mgmt"))
        assert r.status_code == 403 and r.json()["detail"]["code"] == "read_only"
        feats = c.get("/auth/features", headers=_h("mgmt")).json()
        assert feats["role_labels"]["management"] == "Management"
        # R7b: Market Intel is grantable to management (not a default)
        assert feats["role_feature_limits"] == {"management": [f for f in feats["features"] if f in SIX + ["Market Intel"]]}
        assert feats["role_defaults"]["management"] == SIX


# ── the team API ───────────────────────────────────────────────────────────────

@test("team: role, page and status validation — 400, nothing written, no auth user touched")
def _():
    with _world(_FakeDB(_team())) as fake:
        c = _client()
        bad = [
            ("POST", "/team/invite", {"email": "new@example.com", "role": "superuser", "features": []}, "Unknown role"),
            ("POST", "/team/invite", {"email": "new@example.com", "role": "operations", "features": []}, "Unknown role"),
            ("POST", "/team/invite", {"email": "new@example.com", "role": "member", "features": ["Nope"]}, "Unknown page"),
            ("POST", "/team/invite", {"email": "new@example.com", "role": "management",
                                      "features": ["Sales", "AI Assistant"]}, "cannot be given: AI Assistant"),
            ("POST", "/team/invite", {"email": "new@example.com", "role": "member", "features": [],
                                      "method": "carrier-pigeon"}, "temporary password"),
            ("POST", "/team/invite", {"email": "not-an-email", "role": "member", "features": []}, "valid email"),
            ("PATCH", "/team/clerk@example.com", {"role": "root"}, "Unknown role"),
            ("PATCH", "/team/clerk@example.com", {"status": "gone"}, "Unknown status"),
            ("PATCH", "/team/clerk@example.com", {"features": ["Dashboard", "Ghost"]}, "Unknown page"),
            ("PATCH", "/team/mgmt@example.com", {"features": ["Sales", "Shop Admin"]}, "Management cannot be given"),
            ("PATCH", "/team/clerk@example.com", {"role": "management", "features": ["Marketing"]}, "cannot be given"),
        ]
        for meth, path, body, needle in bad:
            r = c.request(meth, path, headers=_h("boss"), json=body)
            assert r.status_code == 400 and needle in r.json()["detail"], (path, body, r.status_code, r.text[:200])
        r = c.patch("/team/ghost@example.com", headers=_h("boss"), json={"status": "active"})
        assert r.status_code == 404, r.text[:200]
        assert [w for w in fake.writes if w[1] in ("user_roles", "app_invites", "shop_admin_audit")] == []
        assert fake.auth.admin.calls == []


@test("team: the owner's role, pages and status never change; the owner is never removed or re-invited (409)")
def _():
    from app.features import FEATURES
    with _world(_FakeDB(_team())) as fake:
        c = _client()
        for body in ({"role": "member"}, {"role": "management"}, {"features": ["Dashboard"]}, {"status": "disabled"}):
            r = c.patch("/team/owner@example.com", headers=_h("boss"), json=body)
            assert r.status_code == 409 and "owner" in r.json()["detail"], (body, r.text[:200])
        r = c.patch("/team/OWNER@example.com", headers=_h("boss"), json={"status": "disabled"})
        assert r.status_code == 409, "the owner list is matched case-insensitively"
        assert c.delete("/team/owner@example.com", headers=_h("boss")).status_code == 409
        for method in ("temp", "email"):
            r = c.post("/team/invite", headers=_h("boss"), json={"email": "owner@example.com", "role": "admin",
                                                                  "method": method})
            assert r.status_code == 409, (method, r.text[:200])
        assert fake.auth.admin.calls == [], "no password reset, ban or delete reached the owner's auth user"
        assert [w for w in fake.writes if w[1] in ("user_roles", "app_invites")] == []
        # a save that changes nothing is not a change
        r = c.patch("/team/owner@example.com", headers=_h("boss"),
                    json={"role": "admin", "features": list(FEATURES), "status": "active"})
        assert r.status_code == 200, r.text[:200]
        # /team marks the owner for the Team page
        users = {u["email"]: u for u in c.get("/team", headers=_h("boss")).json()["users"]}
        assert users["owner@example.com"]["is_owner"] is True and users["boss@example.com"]["is_owner"] is False


@test("team: the last active admin is never demoted, disabled or removed (409); with a second admin it can be")
def _():
    from app import user_auth
    from app.features import FEATURES
    users = [_row("boss@example.com", "admin", FEATURES), _row("ops@example.com", "admin", FEATURES, status="disabled"),
             _row("rep@example.com", "salesman", ["Catalog"])]
    with _world(_FakeDB(users), owners=()) as fake:
        c = _client()
        for body in ({"role": "member"}, {"role": "management"}, {"status": "disabled"}):
            r = c.patch("/team/boss@example.com", headers=_h("boss"), json=body)
            assert r.status_code == 409 and "active admin" in r.json()["detail"], (body, r.text[:200])
        assert c.delete("/team/boss@example.com", headers=_h("boss")).status_code == 400, "never your own account"
        # the rule itself, for a removal by anyone (the API can only reach it through the self rule)
        try:
            user_auth.check_team_change("boss@example.com", fake.rows("user_roles")[0], remove=True)
            raise AssertionError("removing the last active admin must be refused")
        except user_auth.TeamChangeRefused as e:
            assert e.status == 409
        # a disabled admin does not count; re-activating one does
        assert c.patch("/team/ops@example.com", headers=_h("boss"), json={"status": "active"}).status_code == 200
        assert c.patch("/team/boss@example.com", headers=_h("boss"), json={"role": "member"}).status_code == 200
        assert c.get("/team", headers=_h("boss")).status_code == 403, "the demotion took effect at once"
        r = c.patch("/team/ops@example.com", headers=_h("ops"), json={"status": "disabled"})
        assert r.status_code == 409, "ops is now the last active admin"
        # demoting a non-admin never needs another admin
        assert c.patch("/team/rep@example.com", headers=_h("ops"), json={"status": "disabled"}).status_code == 200


@test("team: every change leaves one before/after row (entity user); never a password or a token")
def _():
    with _world(_FakeDB(_team())) as fake:
        c = _client()
        r = c.post("/team/invite", headers=_h("boss"), json={"email": "Exec@Example.com", "full_name": "Exec",
                                                              "role": "management", "features": SIX})
        assert r.status_code == 200 and r.json()["mode"] == "temp" and r.json()["temp_password"], r.text[:200]
        tmp = r.json()["temp_password"]
        row = next(u for u in fake.rows("user_roles") if u["email"] == "exec@example.com")
        assert row["role"] == "management" and row["features"] == SIX
        assert c.patch("/team/exec@example.com", headers=_h("boss"), json={"features": ["Dashboard", "Sales"]}).status_code == 200
        assert c.patch("/team/exec@example.com", headers=_h("boss"), json={"features": ["Dashboard", "Sales"]}).status_code == 200
        r = c.post("/team/invite", headers=_h("boss"), json={"email": "later@example.com", "role": "member",
                                                              "features": ["Dashboard"], "method": "email"})
        assert r.status_code == 200 and r.json()["mode"] == "email"
        token = r.json()["token"]
        assert c.delete("/team/clerk@example.com", headers=_h("boss")).status_code == 200
        audit = fake.audit()
        assert [(a["entity"], a["entity_id"], a["action"]) for a in audit] == [
            ("user", "exec@example.com", "create"), ("user", "exec@example.com", "update"),
            ("user", "later@example.com", "create"), ("user", "clerk@example.com", "delete")], \
            "one row per change; the repeated save moved nothing and wrote nothing"
        assert all(a["actor"] == "boss@example.com" for a in audit)
        assert audit[0]["before"] is None and audit[0]["after"]["role"] == "management"
        assert audit[1]["before"]["features"] == SIX and audit[1]["after"]["features"] == ["Dashboard", "Sales"]
        assert audit[2]["after"]["status"] == "invited"
        assert audit[3]["before"]["role"] == "member" and audit[3]["after"] is None
        dump = json.dumps(audit)
        assert tmp not in dump and token not in dump and "password" not in dump and "token" not in dump
    # shop_admin_audit missing (not migrated / down): the same facts on audit_log
    with _world(_FakeDB(_team(), missing={"shop_admin_audit"})) as fake:
        r = _client().patch("/team/clerk@example.com", headers=_h("boss"), json={"status": "disabled"})
        assert r.status_code == 200, r.text[:200]
        rows = fake.audit_log("team.audit")
        assert len(rows) == 1 and rows[0]["user_email"] == "boss@example.com"
        d = rows[0]["detail"]
        assert d["action"] == "update" and d["before"]["status"] == "active" and d["after"]["status"] == "disabled"


@test("team: Management before the migration — 400 naming the file before any auth user is touched")
def _():
    from app import user_auth
    # the probe reads the legacy CHECK: refused up front
    with _world(_FakeDB(_team(), allowed_roles=LEGACY_ROLES)) as fake:
        c = _client()
        r = c.post("/team/invite", headers=_h("boss"), json={"email": "exec@example.com", "role": "management",
                                                              "features": SIX})
        assert r.status_code == 400 and "r7_rbac_migration.sql" in r.json()["detail"] and "Management" in r.json()["detail"]
        r = c.post("/team/invite", headers=_h("boss"), json={"email": "exec@example.com", "role": "management",
                                                              "features": SIX, "method": "email"})
        assert r.status_code == 400
        r = c.patch("/team/clerk@example.com", headers=_h("boss"), json={"role": "management"})
        assert r.status_code == 400 and "r7_rbac_migration.sql" in r.json()["detail"]
        assert fake.auth.admin.calls == [] and [w for w in fake.writes if w[1] in ("user_roles", "app_invites")] == []
        assert fake.rpcs == ["run_readonly_query"], "the constraint is read once, then cached"
        # a legacy role never needs the probe; a member invite still works
        r = c.post("/team/invite", headers=_h("boss"), json={"email": "m2@example.com", "role": "member",
                                                              "features": ["Dashboard"]})
        assert r.status_code == 200 and fake.rpcs == ["run_readonly_query"]
    # the probe cannot read the constraint: the write goes ahead and the CHECK violation is translated
    with _world(_FakeDB(_team(), allowed_roles=LEGACY_ROLES, rpc_error=RuntimeError("rpc down"))) as fake:
        r = _client().patch("/team/clerk@example.com", headers=_h("boss"), json={"role": "management"})
        assert r.status_code == 400 and "r7_rbac_migration.sql" in r.json()["detail"], r.text[:200]
        assert next(u for u in fake.rows("user_roles") if u["email"] == "clerk@example.com")["role"] == "member"
        assert fake.audit() == []
    # after the migration: member -> management keeps only the pages management may hold
    with _world(_FakeDB(_team())) as fake:
        r = _client().patch("/team/clerk@example.com", headers=_h("boss"), json={"role": "management"})
        assert r.status_code == 200, r.text[:200]
        row = next(u for u in fake.rows("user_roles") if u["email"] == "clerk@example.com")
        assert row["role"] == "management" and row["features"] == ["Dashboard", "Sales"]
        # admin -> management without a page list: the pages management may hold (the six + Market
        # Intel, R7b), never the admin's whole list
        r = _client().patch("/team/boss@example.com", headers=_h("owner"), json={"role": "management"})
        assert r.status_code == 200
        row = next(u for u in fake.rows("user_roles") if u["email"] == "boss@example.com")
        assert row["role"] == "management" and sorted(row["features"]) == sorted(SIX + ["Market Intel"])
    assert user_auth.role_enabled_in_db("salesman") is True


@test("team: accept_invite runs under the same rules — an owner's row and the last admin are refused, the invite stays pending")
def _():
    from app.features import FEATURES
    invites = [
        {"id": "i1", "email": "owner@example.com", "role": "member", "features": ["Dashboard"], "status": "pending",
         "token": "t-owner-" + "x" * 30, "invited_by": "old@example.com"},
        {"id": "i2", "email": "boss@example.com", "role": "member", "features": ["Dashboard"], "status": "pending",
         "token": "t-boss-" + "x" * 30, "invited_by": "old@example.com"},
        {"id": "i3", "email": "fresh@example.com", "role": "management", "features": SIX, "status": "pending",
         "token": "t-fresh-" + "x" * 30, "invited_by": "boss@example.com"},
    ]
    users = [_row("owner@example.com", "admin", FEATURES), _row("boss@example.com", "admin", FEATURES)]
    with _world(_FakeDB(users, invites=invites), owners=("owner@example.com",)) as fake:
        c = _client()
        r = c.post("/team/accept", json={"token": invites[0]["token"], "password": "a-long-password"})
        assert r.status_code == 409, r.text[:200]
        assert fake.rows("app_invites")[0]["status"] == "pending" and fake.auth.admin.calls == []
        r = c.post("/team/accept", json={"token": invites[2]["token"], "password": "a-long-password"})
        assert r.status_code == 200 and r.json()["role"] == "management", r.text[:200]
        assert fake.rows("app_invites")[2]["status"] == "accepted"
        a = fake.audit()[-1]
        assert (a["entity"], a["entity_id"], a["action"], a["actor"]) == ("user", "fresh@example.com", "create",
                                                                          "fresh@example.com")
    solo = [_row("boss@example.com", "admin", FEATURES)]
    with _world(_FakeDB(solo, invites=invites), owners=()) as fake:
        r = _client().post("/team/accept", json={"token": invites[1]["token"], "password": "a-long-password"})
        assert r.status_code == 409 and "active admin" in r.json()["detail"], r.text[:200]
        assert fake.rows("user_roles")[0]["role"] == "admin"


def main() -> int:
    passed = failed = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception:  # noqa: BLE001
            print(f"  FAIL  {name}")
            traceback.print_exc(limit=4)
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
