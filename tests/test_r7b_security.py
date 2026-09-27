"""R7b — Sprint 2 "Safe access" (27-Sep-2026, plan §25 P0/P1, §30) — pure tests, no database.

    python -m tests.test_r7b_security

Same lightweight runner as tests/test_r1_security.py. Nothing here reaches a database, Supabase Auth
or the network: user_roles, salesmen, audit_log and the auth admin API are an in-memory stand-in,
the token decoder is stubbed (token text -> claims), and every address, phone and number is
synthetic (@example.com, 3300 0xxx, 90000xxxx).

Covered:
  * forced password change — the migration flags every role but the owner (the owner list equals
    the config default; its closing check refuses a flagged owner); the gate answers 403
    password_change_required on EVERY authenticated route in app.routes except /me, /auth/features
    and POST /auth/password (a full route sweep); an owner is never held; GET /me returns the true
    flag once the column exists (fresh, not the 60 s cache) and false while it is missing;
  * the password change — clears the flag, keeps its answer shape, asks Supabase to sign the OTHER
    sessions out (scope "others", with the caller's own token), refuses the older tokens of those
    sessions from then on (401) while the changing session and a new sign-in go on working; a
    Supabase failure never fails the change; a token without a session id revokes nothing;
  * /docs, /redoc, /openapi.json — present locally, absent with RENDER=true or ENV=production
    (the real app imported in a subprocess);
  * personal ID numbers in narration — the parser masks 9-digit and 15+-digit runs keeping the last
    3, idempotent, order numbers survive and the Focus matcher's own regex still finds them; the
    SQL spelling in both migrations is the parser's pattern; the view migration keeps the 31
    columns of division_payment_migration.sql's v_sales, only narration changes, and its reverse is
    that v_sales verbatim; the optional data step and its no-op reverse;
  * the storekeeper — sees only Confirmed / Preparing / On the way orders, never a phone, email,
    WhatsApp link, order token or notify result; cannot confirm, even holding Shop Orders; its
    order list is status-limited and never searches phones; no shop list;
  * the claim branch is gone — the assign route is admin-only and shop.assign_order refuses anyone
    else before reading anything;
  * render.yaml autoDeploy false; the salesmen login lookup is an exact lower-case match (no
    wildcards, two rows link neither) and its unique index migration; the duplicate-login message;
  * the login trail — one audit_log 'auth.session_seen' per session per process, never the token.
"""
from __future__ import annotations

import copy
import io
import json
import os
import re
import subprocess
import sys
import time
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


def _sql(name: str) -> str:
    return (ROOT / "scripts" / name).read_text(encoding="utf-8")


# ── an in-memory PostgREST + Supabase Auth stand-in ────────────────────────────

class _MissingColumn(Exception):
    code = "42703"
    message = "column user_roles.must_reset does not exist"


class _Query:
    def __init__(self, db: "_FakeDB", table: str):
        self.db, self.table = db, table
        self.op, self.payload, self.filters, self.lim, self.cols = "select", None, [], None, "*"

    def select(self, cols="*", **_k):
        self.op, self.cols = "select", cols
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
        self.filters.append(("eq", col, val))
        return self

    def ilike(self, col, val):
        self.filters.append(("ilike", col, val))
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, n):
        self.lim = n
        return self

    def _match(self, row) -> bool:
        for op, col, val in self.filters:
            got = row.get(col)
            if op == "eq" and str(got) != str(val):
                return False
            if op == "ilike":        # PostgREST ILIKE: % and _ are wildcards, case-insensitive
                pat = "^" + "".join(".*" if ch == "%" else "." if ch == "_" else re.escape(ch)
                                    for ch in str(val)) + "$"
                if not re.match(pat, str(got or ""), re.I):
                    return False
        return True

    def execute(self):
        db = self.db
        db.calls.append((self.table, self.op, list(self.filters), self.cols))
        rows = db.tables.setdefault(self.table, [])
        if self.table == "user_roles" and db.missing_must_reset:
            if (self.op == "select" and "must_reset" in str(self.cols)) or \
                    (self.op in ("insert", "update") and "must_reset" in (self.payload or {})):
                raise _MissingColumn(_MissingColumn.message)
        if self.op == "select":
            out = [dict(r) for r in rows if self._match(r)]
            if self.table == "user_roles" and self.cols != "*":
                keep = [c.strip() for c in str(self.cols).split(",")]
                out = [{k: r[k] for k in keep if k in r} for r in out]
            return SimpleNamespace(data=out[: self.lim] if self.lim else out, count=len(out))
        if self.op == "insert":
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            db.inserts.append((self.table, copy.deepcopy(payload)))
            for p in payload:
                rows.append(dict(p))
            return SimpleNamespace(data=[dict(p) for p in payload], count=None)
        if self.op == "update":
            hit = [r for r in rows if self._match(r)]
            for r in hit:
                r.update(self.payload)
            return SimpleNamespace(data=[dict(r) for r in hit], count=None)
        gone = [r for r in rows if self._match(r)]
        rows[:] = [r for r in rows if not self._match(r)]
        return SimpleNamespace(data=gone, count=None)


class _AuthAdmin:
    def __init__(self, emails, fail_sign_out: bool = False):
        self.users = [SimpleNamespace(id=f"uid-{e.split('@')[0]}", email=e, user_metadata={"must_reset": True})
                      for e in emails]
        self.calls: list[tuple] = []
        self.fail_sign_out = fail_sign_out

    def list_users(self):
        return list(self.users)

    def update_user_by_id(self, uid, attrs):
        self.calls.append(("update", uid, tuple(sorted(attrs))))

    def sign_out(self, jwt, scope="global"):
        self.calls.append(("sign_out", jwt, scope))
        if self.fail_sign_out:
            raise RuntimeError("gotrue unreachable")


class _FakeDB:
    def __init__(self, users: list[dict], *, missing_must_reset=False, fail_sign_out=False, salesmen=None):
        self.tables: dict[str, list[dict]] = {"user_roles": [dict(u) for u in users],
                                              "salesmen": [dict(s) for s in salesmen or []]}
        self.missing_must_reset = missing_must_reset
        self.calls: list[tuple] = []
        self.inserts: list[tuple] = []
        self.auth = SimpleNamespace(admin=_AuthAdmin([u["email"] for u in users], fail_sign_out))

    def table(self, name):
        return _Query(self, name)

    def audit(self, event: str | None = None) -> list[dict]:
        return [p for t, ps in self.inserts if t == "audit_log" for p in ps
                if event is None or p.get("event") == event]

    def row(self, email) -> dict:
        return next(r for r in self.tables["user_roles"] if r["email"] == email)


NOW = int(time.time())


def _claims(tok: str) -> dict:
    """Token text -> claims. '<local>[-<session>[-old|-new]]': session id s-<session>; '-old' was issued
    ten minutes ago, '-new' a second from now, anything else a minute ago."""
    parts = tok.split("-")
    out = {"sub": "uid-" + parts[0], "email": parts[0] + "@example.com", "aal": "aal1",
           "amr": [{"method": "password", "timestamp": NOW - 60}]}
    if len(parts) > 1:
        out["session_id"] = "s-" + parts[1]
    age = {"old": -600, "new": 1}.get(parts[-1], -60) if len(parts) > 2 else -60
    out["iat"] = NOW + age
    return out


class _world:
    """Point every client lookup at the fake; stub the token decoder; set the owner list; switch the
    rate limiter off; forget the per-process session state on the way in and out."""

    def __init__(self, fake: _FakeDB, owners=("owner@example.com",)):
        self.fake, self.owners = fake, list(owners)

    def __enter__(self):
        from app import auth, database, user_auth
        from app.config import settings
        import app.main as m
        self.old_owners = settings.owner_emails
        settings.owner_emails = self.owners
        database.invalidate_user_cache()
        database._legacy_until = 0.0
        auth._ended_sessions.clear()
        auth._seen_sessions.clear()
        self.p = _Patched(
            (database, "get_client", lambda: self.fake), (user_auth, "get_client", lambda: self.fake),
            (auth, "_decode_token", _claims),
            (m.limiter, "enabled", False),
        )
        self.p.__enter__()
        return self.fake

    def __exit__(self, *exc):
        from app import auth, database
        from app.config import settings
        self.p.__exit__(*exc)
        settings.owner_emails = self.old_owners
        database.invalidate_user_cache()
        database._legacy_until = 0.0
        auth._ended_sessions.clear()
        auth._seen_sessions.clear()
        return False


def _client():
    from fastapi.testclient import TestClient
    import app.main as m
    return TestClient(m.app)


def _h(tok: str) -> dict:
    return {"authorization": f"Bearer {tok}"}


def _row(email, role, features=None, **extra) -> dict:
    return {"email": email, "role": role, "features": list(features or []), "status": "active",
            "full_name": email.split("@")[0].title(), **extra}


# ── 1. forced password change: the migration ─────────────────────────────────────

@test("must_reset migration: every role is backfilled, never the owner (list = config default), owner check at the end")
def _():
    from app.config import Settings
    mig = _sql("user_roles_must_reset_migration.sql")
    backfill = re.search(r"update user_roles r.*?;", mig, re.S).group(0)
    assert "r.role" not in backfill, "R7b: the backfill is no longer salesmen-only (admins and management too)"
    assert "<> all (owners)" in backfill and "raw_user_meta_data ->> 'must_reset'" in backfill
    assert "r.must_reset = false" in backfill, "only ever sets true on a row still false"
    lists = re.findall(r"owners text\[\] := array\[([^\]]*)\]", mig)
    assert len(lists) == 2 and lists[0] == lists[1], "the backfill and the check name the same owners"
    listed = set(re.findall(r"'([^']+)'", lists[0]))
    old = os.environ.pop("OWNER_EMAILS", None)
    try:
        assert listed == set(Settings().owner_emails), "the SQL owner list must equal OWNER_EMAILS' default"
    finally:
        if old is not None:
            os.environ["OWNER_EMAILS"] = old
    assert "an owner row has must_reset set" in mig
    assert "add column if not exists must_reset boolean not null default false" in mig
    assert "grant " not in mig.lower().replace("role_table_grants", "").replace("role_column_grants", "")
    assert "drop column if exists must_reset" in _sql("user_roles_must_reset_reverse.sql")


# ── 2. forced password change: the gate covers every route ───────────────────────

def _depends_on_login(dependant) -> bool:
    from app import auth
    for d in dependant.dependencies:
        if d.call in (auth.get_current_user, auth.get_caller) or _depends_on_login(d):
            return True
    return False


@test("must_reset: EVERY authenticated route answers 403 password_change_required except /me, /auth/features, POST /auth/password")
def _():
    from fastapi.routing import APIRoute
    import app.main as m
    from app import auth
    fake = _FakeDB([_row("temp@example.com", "admin", must_reset=True)])
    swept, wrong = 0, []
    with _world(fake):
        c = _client()
        for r in m.app.routes:
            if not isinstance(r, APIRoute) or not _depends_on_login(r.dependant):
                continue
            path = re.sub(r"\{[^}]+\}", "1", r.path)
            for meth in sorted(r.methods):
                if r.path in auth.MUST_RESET_EXEMPT:
                    continue
                swept += 1
                resp = c.request(meth, path, headers=_h("temp-a"))
                detail = (resp.json() or {}).get("detail") if resp.headers.get("content-type", "").startswith("application/json") else None
                if resp.status_code != 403 or not isinstance(detail, dict) or detail.get("code") != "password_change_required":
                    wrong.append((meth, r.path, resp.status_code, str(detail)[:80]))
        assert swept > 150, f"the sweep must cover the whole API, got {swept} routes"
        assert not wrong, f"routes a must_reset login still reaches: {wrong[:10]}"
        # the three exempt routes answer
        assert c.get("/me", headers=_h("temp-a")).json()["must_reset"] is True
        assert c.get("/auth/features", headers=_h("temp-a")).status_code == 200
    assert auth.MUST_RESET_EXEMPT == {"/me", "/auth/features", "/auth/password"}


@test("must_reset: an owner is never held at the password screen (break-glass); /me says false for them")
def _():
    from fastapi import HTTPException
    from starlette.requests import Request
    from app import auth
    fake = _FakeDB([_row("owner@example.com", "admin", must_reset=True), _row("boss@example.com", "admin", must_reset=True)])

    def req(path):
        return Request({"type": "http", "method": "GET", "path": path, "query_string": b"", "headers": [],
                        "client": ("10.0.0.9", 1), "server": ("t", 80), "scheme": "http"})
    from fastapi.security import HTTPAuthorizationCredentials as Cr
    with _world(fake):
        u = auth.get_current_user(req("/feed"), Cr(scheme="Bearer", credentials="owner"))
        assert u.must_reset is False and u.role == "admin"
        try:
            auth.get_current_user(req("/feed"), Cr(scheme="Bearer", credentials="boss"))
            raise AssertionError("a non-owner admin on a temporary password must be held")
        except HTTPException as e:
            assert e.status_code == 403 and e.detail["code"] == "password_change_required"
        c = _client()
        assert c.get("/me", headers=_h("owner")).json()["must_reset"] is False
        assert c.get("/me", headers=_h("boss")).json()["must_reset"] is True


@test("/me: the true flag once the column exists (read fresh, not the 60 s cache); false while the column is missing")
def _():
    from app import database
    fake = _FakeDB([_row("rep@example.com", "salesman", ["Catalog", "Shop Orders"], must_reset=False)])
    with _world(fake):
        c = _client()
        assert c.get("/me", headers=_h("rep")).json()["must_reset"] is False
        # an admin re-invites the rep (flag set in the DB) while the gate's cached row still says false
        fake.row("rep@example.com")["must_reset"] = True
        database._user_cache["rep@example.com"] = (time.time(), {**_row("rep@example.com", "salesman"), "must_reset": False})
        assert c.get("/me", headers=_h("rep")).json()["must_reset"] is True, "/me reads the row as it is now"
        # and the next request is held by the gate (the fresh read refilled the shared cache)
        r = c.get("/shop/catalog", headers=_h("rep"))
        assert r.status_code == 403 and r.json()["detail"]["code"] == "password_change_required"
    # before user_roles_must_reset_migration.sql: the column is missing -> false, nothing enforced
    fake = _FakeDB([_row("rep@example.com", "salesman", ["Catalog", "Shop Orders"])], missing_must_reset=True)
    with _world(fake):
        c = _client()
        me = c.get("/me", headers=_h("rep"))
        assert me.status_code == 200 and me.json()["must_reset"] is False, me.text[:200]
        assert database.must_reset_column_absent(), "the legacy shape is remembered"
    from app.user_auth import must_reset_of
    assert must_reset_of({"must_reset": True}, "x@example.com") is True
    assert must_reset_of({"role": "admin"}, "x@example.com") is False
    assert must_reset_of(None, "x@example.com") is False


# ── 3. the password change signs the other sessions out ─────────────────────────

@test("password change: clears the flag, same answer, Supabase signs the OTHER sessions out; their old tokens get 401")
def _():
    fake = _FakeDB([_row("temp@example.com", "salesman", ["Catalog", "Shop Orders"], must_reset=True)])
    with _world(fake):
        c = _client()
        r = c.post("/auth/password", headers=_h("temp-a"), json={"password": "a-proper-password"})
        assert r.status_code == 200 and r.json() == {"ok": True, "must_reset": False}, r.text[:200]
        assert fake.row("temp@example.com")["must_reset"] is False, "the server-owned flag is cleared"
        calls = fake.auth.admin.calls
        assert ("sign_out", "temp-a", "others") in calls, calls
        assert calls.index(("sign_out", "temp-a", "others")) > 0 and calls[0][0] == "update", \
            "the password is set first, then the other sessions are signed out"
        audit = fake.audit("auth.password_change")
        assert audit and audit[-1]["detail"] == {"forced": True, "other_sessions_signed_out": True}
        assert "temp-a" not in json.dumps(fake.audit()), "no token in any audit row"
        # the session that changed it goes on; an older token of another session is refused
        assert c.get("/me", headers=_h("temp-a")).status_code == 200
        old = c.get("/me", headers=_h("temp-b-old"))
        assert old.status_code == 401 and "signed out" in old.json()["detail"], old.text[:200]
        # a token that other session was issued a few seconds before the change is inside the skew
        assert c.get("/me", headers=_h("temp-c")).status_code == 401, "issued a minute before: refused"
        assert c.get("/me", headers=_h("temp-d-new")).status_code == 200, "a fresh sign-in after the change works"


@test("password change: Supabase failing to sign out never fails the change; a token without a session id revokes nothing")
def _():
    fake = _FakeDB([_row("temp@example.com", "salesman", ["Catalog"], must_reset=True)], fail_sign_out=True)
    with _world(fake):
        c = _client()
        r = c.post("/auth/password", headers=_h("temp-a"), json={"password": "a-proper-password"})
        assert r.status_code == 200, r.text[:200]
        assert fake.audit("auth.password_change")[-1]["detail"]["other_sessions_signed_out"] is False
        assert c.get("/me", headers=_h("temp-b-old")).status_code == 401, "the API still cuts the old tokens off"
    fake = _FakeDB([_row("temp@example.com", "salesman", ["Catalog"], must_reset=True)])
    with _world(fake):
        c = _client()
        assert c.post("/auth/password", headers=_h("temp"), json={"password": "a-proper-password"}).status_code == 200
        assert not [x for x in fake.auth.admin.calls if x[0] == "sign_out"], "no session id: nothing to keep apart"
        assert fake.audit("auth.password_change")[-1]["detail"]["other_sessions_signed_out"] is None
    from app import user_auth
    fake = _FakeDB([])
    with _Patched((user_auth, "get_client", lambda: fake)):
        assert user_auth.revoke_other_sessions("") is False and fake.auth.admin.calls == []
        assert user_auth.revoke_other_sessions("tok") is True
        assert fake.auth.admin.calls == [("sign_out", "tok", "others")]


@test("sessions: end_other_sessions keeps the changing session, refuses older tokens of others, lets new ones through")
def _():
    from app import auth
    auth._ended_sessions.clear()
    try:
        t0 = 1_000_000.0
        auth.end_other_sessions("u1", "keep", at=t0)
        with _Patched((auth.time, "time", lambda: t0 + 5)):
            assert auth.session_ended("u1", "other", t0 - 3600) is True
            assert auth.session_ended("u1", "other", t0 - auth.SESSION_CUTOFF_SKEW_S - 1) is True
            assert auth.session_ended("u1", "other", t0 - auth.SESSION_CUTOFF_SKEW_S + 1) is False, "clock skew allowed"
            assert auth.session_ended("u1", "keep", t0 - 3600) is False, "the session that changed it"
            assert auth.session_ended("u1", "other", t0 + 2) is False, "a sign-in after the change"
            assert auth.session_ended("u2", "other", t0 - 3600) is False, "another login is untouched"
            assert auth.session_ended("u1", "", None) is True, "no iat: predates nothing, refused"
        with _Patched((auth.time, "time", lambda: t0 + auth.SESSION_CUTOFF_KEEP_S + 1)):
            assert auth.session_ended("u1", "other", t0 - 3600) is False, "the entry expires"
        auth.end_other_sessions("u3", "")          # no session id: nothing recorded
        assert "u3" not in auth._ended_sessions
    finally:
        auth._ended_sessions.clear()


# ── 4. the login trail ───────────────────────────────────────────────────────────

@test("login trail: one 'auth.session_seen' row per session per process — session id, method, hashed address; never the token")
def _():
    fake = _FakeDB([_row("rep@example.com", "salesman", ["Catalog", "Shop Orders"])])
    with _world(fake):
        c = _client()
        for _ in range(3):
            assert c.get("/me", headers={**_h("rep-a"), "user-agent": "SyntheticBrowser/1.0"}).status_code == 200
        rows = fake.audit("auth.session_seen")
        assert len(rows) == 1, rows
        d = rows[0]["detail"]
        assert rows[0]["user_email"] == "rep@example.com"
        assert d["session_id"] == "s-a" and d["role"] == "salesman" and d["status"] == "active"
        assert d["methods"] == ["password"] and d["aal"] == "aal1" and d["path"] == "/me" and d["method"] == "GET"
        assert d["ua"] == "SyntheticBrowser/1.0"
        assert d["ip_hash"] and "testclient" not in json.dumps(d), "the client address is hashed"
        assert "rep-a" not in json.dumps(rows), "the token is never written"
        c.get("/me", headers=_h("rep-b"))
        assert [r["detail"]["session_id"] for r in fake.audit("auth.session_seen")] == ["s-a", "s-b"]
        c.get("/me", headers=_h("rep"))                       # no session id: nothing to tell requests apart by
        c.get("/me", headers=_h("nobody-z"))                  # a token without a user_roles row: 403, no row
        assert len(fake.audit("auth.session_seen")) == 2
    from app import auth
    assert auth.SESSION_SEEN_EVENT == "auth.session_seen" and auth.SESSION_SEEN_MAX >= 1000
    auth._seen_sessions.clear()
    for i in range(auth.SESSION_SEEN_MAX + 5):
        auth._first_sight(f"s{i}")
    assert len(auth._seen_sessions) == auth.SESSION_SEEN_MAX, "the remembered set is bounded"
    auth._seen_sessions.clear()


# ── 5. /docs, /redoc, /openapi.json ───────────────────────────────────────────────

def _routes_in_subprocess(extra_env: dict) -> set[str]:
    env = {k: v for k, v in os.environ.items() if k not in ("RENDER", "ENV")}
    env.update({"SUPABASE_URL": "https://ci.invalid", "PYTHONIOENCODING": "utf-8", **extra_env})
    code = "import json, app.main as m; print('ROUTES=' + json.dumps(sorted({getattr(r, 'path', '') for r in m.app.routes})))"
    out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env, capture_output=True, text=True,
                         timeout=300, encoding="utf-8", errors="replace")
    line = next((ln for ln in out.stdout.splitlines() if ln.startswith("ROUTES=")), None)
    assert line, f"the app did not import: {out.stderr[-600:]}"
    return set(json.loads(line[len("ROUTES="):]))


DOCS = {"/docs", "/redoc", "/openapi.json"}


@test("docs: /docs, /redoc and /openapi.json are served locally and gone with RENDER=true or ENV=production")
def _():
    import app.main as m
    from app.config import is_production_env
    assert m.docs_kwargs(True) == {"docs_url": None, "redoc_url": None, "openapi_url": None}
    assert m.docs_kwargs(False) == {}
    assert is_production_env({"RENDER": "true"}) and is_production_env({"ENV": "Production"})
    assert not is_production_env({}) and not is_production_env({"RENDER": "false", "ENV": "dev"})
    local = {getattr(r, "path", "") for r in m.app.routes}
    assert DOCS <= local, "on a laptop the docs stay"
    assert _client().get("/docs").status_code == 200
    prod = _routes_in_subprocess({"RENDER": "true"})
    assert not (DOCS & prod), f"production still serves {DOCS & prod}"
    assert {"/health", "/me", "/auth/password"} <= prod, "the real API is still there"
    assert not (DOCS & _routes_in_subprocess({"ENV": "production"}))


# ── 6. personal ID numbers in narration ────────────────────────────────────────────

MATCHER = r"([A-Za-z]{2,4})[ -]?([0-9]{4})[ -]?([0-9]{4})"   # r7_focus_links_migration.sql tier 1 / 2


@test("narration: 9-digit and 15+-digit runs keep their last 3; idempotent; other numbers and order numbers untouched")
def _():
    from scripts.ingest import mask_personal_ids as mask
    cases = {
        "CPR 900000123": "CPR ******123",
        "id:900000456/ph 33000111": "id:******456/ph 33000111",
        "card 4000000000000002 ok": "card ************002 ok",
        "acct 400000000000000000009": "acct ************009",
        "900000123": "******123",
        "a900000123b": "a******123b",
        "ten 1234567890 fourteen 12345678901234": "ten 1234567890 fourteen 12345678901234",
        "SI-YQ-26-09-119 inv 00012345": "SI-YQ-26-09-119 inv 00012345",
        "YQ-2609-0019, YQ 2609 0020, YQ26090021": "YQ-2609-0019, YQ 2609 0020, YQ26090021",
        "cpr٩٠٠٠٠٠١٢٣": "cpr٩٠٠٠٠٠١٢٣",          # Arabic-Indic digits: Postgres [0-9] would not mask them either
    }
    for raw, want in cases.items():
        assert mask(raw) == want, (raw, mask(raw))
        assert mask(mask(raw)) == mask(raw), ("idempotent", raw)
    assert mask(None) is None and mask("") == ""


@test("narration: the Focus matcher still finds every typed order number after masking, next to a CPR")
def _():
    from scripts.ingest import mask_personal_ids as mask
    assert MATCHER in _sql("r7_focus_links_migration.sql"), "the matcher regex moved: re-check this test"
    typed = "YQ-2609-0019 CPR 900000123, YQ 2609 0020 / YQ26090021 card 4000000000000002 yq-2609-0022"
    found = {f"{a.upper()}-{b}-{c}" for a, b, c in re.findall(MATCHER, mask(typed))}
    assert {"YQ-2609-0019", "YQ-2609-0020", "YQ-2609-0021", "YQ-2609-0022"} <= found, found
    assert not any("0000" in f for f in found), "no masked digit forms a fake order number"


@test("narration: the parser masks the Sales Day Book narration at parse time")
def _():
    from datetime import datetime
    import pandas as pd
    from scripts import ingest
    header = ingest.EXPECTED_HEADERS["order_lines"]
    grid = pd.DataFrame([["Sales Day Book"] + [None] * 12, header,
                         [datetime(2026, 9, 20), "SI : SI-YQ-26-09-001", "Test Shop", "Cable A", 2, 1.5, 3, 0,
                          2.727, 0.273, 3, "YQ-2609-0019 CPR 900000123", "Rep One"],
                         [datetime(2026, 9, 20), "SI : SI-YQ-26-09-001", "Test Shop", "Cable A", 1, 1.5, 1.5, 0,
                          1.364, 0.136, 1.5, None, "Rep One"]], dtype=object)
    rows = ingest.parse_order_lines(grid, "Sales_day_book.xlsx")
    assert [r["narration"] for r in rows] == ["YQ-2609-0019 CPR ******123", None]
    assert rows[0]["gross_bhd"] == 3 and rows[0]["invoice_no"] == "SI : SI-YQ-26-09-001"


def _select_list(sql: str) -> list[str]:
    """The v_sales select list, one normalised expression per output column."""
    body = re.search(r"create or replace view v_sales as\s*select(.*?)\bfrom order_lines ol", sql, re.S | re.I).group(1)
    body = re.sub(r"--[^\n]*", "", body)
    items, depth, cur = [], 0, ""
    for ch in body:
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            items.append(cur)
            cur = ""
        else:
            cur += ch
    items.append(cur)
    return [" ".join(i.split()) for i in items if i.strip()]


@test("narration view: division_payment's v_sales with ONLY narration changed; masked with the parser's own patterns")
def _():
    from scripts import ingest
    mig, rev, base = _sql("r7_narration_mask_migration.sql"), _sql("r7_narration_mask_reverse.sql"), \
        _sql("division_payment_migration.sql")
    new, old = _select_list(mig), _select_list(base)
    assert len(new) == len(old) == 31
    diff = [(i, o, n) for i, (o, n) in enumerate(zip(old, new)) if o != n]
    assert len(diff) == 1 and diff[0][1] == "ol.narration" and diff[0][2].endswith("as narration"), diff
    assert _select_list(rev) == old, "the reverse is division_payment's v_sales verbatim"
    tail = lambda s: re.search(r"\bfrom order_lines ol.*?;", s, re.S).group(0)  # noqa: E731
    assert " ".join(tail(mig).split()) == " ".join(tail(base).split()) == " ".join(tail(rev).split())
    # the SQL spelling of the two patterns is the parser's, replacement stars included
    for f in ("r7_narration_mask_migration.sql", "r7_narration_mask_data_migration.sql"):
        s = _sql(f)
        assert f"'{ingest.PERSONAL_ID_LONG.pattern}', '{ingest.MASK_LONG}\\1', 'g'" in s, f
        assert f"'{ingest.PERSONAL_ID_CPR.pattern}', '{ingest.MASK_CPR}\\1', 'g'" in s, f
        assert s.index(ingest.PERSONAL_ID_LONG.pattern) < s.index(ingest.PERSONAL_ID_CPR.pattern), \
            "15+ first, then 9 (the parser's order)"
    for f in ("r7_narration_mask_migration.sql", "r7_narration_mask_reverse.sql", "r7_narration_mask_data_migration.sql",
              "r7_narration_mask_data_reverse.sql", "r7_salesmen_login_unique_migration.sql",
              "r7_salesmen_login_unique_reverse.sql", "user_roles_must_reset_migration.sql"):
        low = re.sub(r"--[^\n]*", "", _sql(f)).lower()          # the statements, not the comments
        assert "cascade" not in low and "security_invoker" not in low and not re.search(r"\bgrant\s", low), f
        assert "drop view" not in low and "drop table" not in low, f
    for f in ("r7_narration_mask_migration.sql", "r7_narration_mask_reverse.sql"):
        assert "revoke all on table v_sales from anon, authenticated;" in _sql(f)
        assert "revoke all on table v_sales_agent from anon, authenticated;" in _sql(f)
    assert "raise exception" in _sql("r7_narration_mask_data_reverse.sql"), "the data reverse never pretends"
    assert "db_backup --tables order_lines" in _sql("r7_narration_mask_data_migration.sql")
    assert "r7_narration_mask_migration.sql" in (ROOT / "docs/MIGRATIONS.md").read_text(encoding="utf-8")


# ── 7. the storekeeper ───────────────────────────────────────────────────────────

def _orders() -> dict[int, dict]:
    out = {}
    for oid, st in ((21, "new"), (22, "confirmed"), (23, "packed"), (24, "out_for_delivery"),
                    (25, "delivered"), (26, "cancelled")):
        out[oid] = {"id": oid, "order_no": f"YQ-2609-00{oid}", "status": st, "salesman_id": 1,
                    "salesman_name": "Rep One", "customer_name": f"Shop {oid}", "customer_shop": f"Shop {oid}",
                    "customer_area": "Manama", "customer_phone": f"+9733300{oid:04d}",
                    "customer_email": f"shop{oid}@example.com", "token": "tok-" + "y" * 28 + str(oid),
                    "notify_result": {"email_rep": {"sent": True, "to": "rep@example.com"},
                                      "recipients": ["rep@example.com"]},
                    "ua": "SyntheticBrowser", "ip_hash": "f" * 32, "device_id": "dev-1", "client_order_id": "c-1",
                    "lines": [{"id": 1, "item_code": "C1", "qty": 2}], "events": [],
                    "salesman": {"id": 1, "name": "Rep One"}, "total_bhd": 25.0}
    return out


class _shop:
    """The order routes' shop calls, answered from _orders()."""

    def __enter__(self):
        from app import attribution, shop, shop_notify
        rows = _orders()
        self.lists, self.moved, self.confirmed = [], [], []

        def list_orders(status=None, q=None, limit=50, offset=0, salesman_id=None, search_phone=True):
            self.lists.append({"status": status, "salesman_id": salesman_id, "search_phone": search_phone})
            want = [s for s in str(status or "").split(",") if s]
            out = [copy.deepcopy(o) for o in rows.values()
                   if (not want or o["status"] in want) and (salesman_id is None or o["salesman_id"] == salesman_id)]
            for o in out:
                o.pop("lines"), o.pop("events")
            counts = {s: sum(1 for o in rows.values() if o["status"] == s) for s in shop.STATUSES}
            return {"orders": out, "count": len(out), "counts": counts, "min_order_bhd": 20.0}

        def set_status(order_id, status, note=None, actor=None, allowed=None, **_k):
            if allowed is not None and status not in allowed:
                raise shop.ShopError("not allowed")
            self.moved.append((order_id, status, actor))
            return {**copy.deepcopy(rows[order_id]), "status": status}

        self.p = _Patched(
            (shop, "list_orders", list_orders),
            (shop, "get_order", lambda oid: copy.deepcopy(rows.get(int(oid)))),
            (shop, "set_status", set_status),
            (shop, "confirm_order", lambda oid, *a, **k: self.confirmed.append(oid) or
             {**copy.deepcopy(rows[int(oid)]), "status": "confirmed", "totals": {}, "changed": [], "removed": []}),
            (shop, "market_base", lambda: "https://market.example.com"),
            (shop, "salesman_for_user", lambda e: {"id": 1, "referral_code": "one"} if e == "rep@example.com" else None),
            (shop, "recent_customers", lambda sid=None, limit=20: [
                {"name": o["customer_name"], "phone": o["customer_phone"], "email": o["customer_email"]}
                for o in rows.values() if sid is None or o["salesman_id"] == sid]),
            (shop, "picklist", lambda salesman_id=None: {"groups": [], "totals_by_item": [], "count": 2}),
            (shop_notify, "salesman_to_customer_wa_url", lambda o, status=None: "https://wa.me/97333000022?text=hi"),
            (shop_notify, "notify_status", lambda *a, **k: None),
            (attribution, "customer_rep", lambda cid: {"id": cid}),
        )
        self.p.__enter__()
        return self

    def __exit__(self, *exc):
        self.p.__exit__(*exc)
        return False


SECRET_KEYS = ("customer_phone", "customer_email", "whatsapp_url", "token", "status_url", "notify_result",
               "ua", "ip_hash", "device_id", "client_order_id")
PHONE_DIGITS = re.compile(r"3300\s?00\d\d")


def _staff() -> list[dict]:
    from app.features import FEATURES
    return [_row("boss@example.com", "admin", FEATURES), _row("store@example.com", "storekeeper", ["Storekeeper"]),
            _row("store2@example.com", "storekeeper", ["Storekeeper", "Shop Orders"]),
            _row("rep@example.com", "salesman", ["Catalog", "Shop Orders"])]


@test("storekeeper: opens only Confirmed / Preparing / On the way orders, and never a phone, email, link or token")
def _():
    from app import features, shop
    assert features.strips_contacts("storekeeper") and not features.strips_contacts("management")
    assert shop.ROLE_VISIBLE_STATUSES["storekeeper"] == ("confirmed", "packed", "out_for_delivery")
    with _world(_FakeDB(_staff())), _shop():
        c = _client()
        for oid, seen in ((21, False), (22, True), (23, True), (24, True), (25, False), (26, False)):
            r = c.get(f"/shop/orders/{oid}", headers=_h("store"))
            assert r.status_code == (200 if seen else 404), (oid, r.status_code, r.text[:120])
            if seen:
                o = r.json()
                assert not [k for k in SECRET_KEYS if k in o], (oid, [k for k in SECRET_KEYS if k in o])
                assert not PHONE_DIGITS.search(r.text) and "@example.com" not in r.text.replace("rep@example.com", ""), oid
                assert o["customer_shop"] == f"Shop {oid}" and o["lines"], "the goods and the shop stay"
                assert set(o["next_statuses"]) <= {"packed", "out_for_delivery"}
        # an admin still gets everything, a rep his own order unmasked
        a = c.get("/shop/orders/21", headers=_h("boss")).json()
        assert a["customer_phone"] == "+97333000021" and a["token"] and a["whatsapp_url"]
        assert c.get("/shop/orders/21", headers=_h("rep")).json()["customer_phone"] == "+97333000021"


@test("storekeeper: moves goods (answer stripped too), cannot confirm even with Shop Orders; a Received order is not his")
def _():
    with _world(_FakeDB(_staff())), _shop() as st:
        c = _client()
        r = c.post("/shop/orders/22/status", headers=_h("store"), json={"status": "packed"})
        assert r.status_code == 200, r.text[:200]
        o = r.json()["order"]
        assert o["status"] == "packed" and not [k for k in SECRET_KEYS if k in o], [k for k in SECRET_KEYS if k in o]
        assert st.moved == [(22, "packed", "store@example.com")]
        assert c.post("/shop/orders/22/status", headers=_h("store"), json={"status": "delivered"}).status_code == 400
        assert c.post("/shop/orders/21/status", headers=_h("store"), json={"status": "packed"}).status_code == 404
        for who in ("store", "store2"):
            w = c.post("/shop/orders/22/confirm", headers=_h(who), json={"lines": []})
            assert w.status_code in (403, 404), (who, w.status_code)
        w = c.post("/shop/orders/22/confirm", headers=_h("store2"), json={"lines": []})
        assert w.status_code == 403 and "cannot confirm" in w.json()["detail"], w.text[:200]
        assert st.confirmed == [], "nothing was confirmed"
        assert c.post("/shop/orders/21/confirm", headers=_h("rep"), json={"lines": []}).status_code == 200, \
            "a rep still confirms his own order"
        assert st.confirmed == [21]


@test("storekeeper with Shop Orders: the list is status-limited, company-wide, stripped, never searched by phone; no shops")
def _():
    with _world(_FakeDB(_staff())), _shop() as st:
        c = _client()
        r = c.get("/shop/orders", headers=_h("store2"))
        assert r.status_code == 200, r.text[:200]
        body = r.json()
        assert st.lists[-1] == {"status": "confirmed,packed,out_for_delivery", "salesman_id": None, "search_phone": False}
        assert sorted(o["id"] for o in body["orders"]) == [22, 23, 24]
        assert not PHONE_DIGITS.search(r.text) and "shop2" not in r.text
        assert body["counts"]["new"] == 0 and body["counts"]["delivered"] == 0 and body["counts"]["confirmed"] == 1
        r = c.get("/shop/orders?status=new", headers=_h("store2"))
        assert r.json()["orders"] == [] and st.lists[-1]["status"] != "new", "a status it may not see is not even asked"
        c.get("/shop/orders?status=packed,delivered&q=3300", headers=_h("store2"))
        assert st.lists[-1] == {"status": "packed", "salesman_id": None, "search_phone": False}
        assert c.get("/shop/customers", headers=_h("store2")).json()["customers"] == []
        assert c.get("/shop/picklist", headers=_h("store")).status_code == 200
        assert c.get("/shop/orders", headers=_h("store")).status_code == 403, "without Shop Orders: no list at all"


# ── 8. the claim branch is gone ───────────────────────────────────────────────────

@test("assign: admin-only route; shop.assign_order refuses a non-admin before reading anything")
def _():
    import inspect
    from fastapi.routing import APIRoute
    import app.main as m
    from app import auth, shop
    route = next(r for r in m.app.routes if isinstance(r, APIRoute) and r.path == "/shop/orders/{order_id}/assign")
    assert [d.call for d in route.dependant.dependencies] == [auth.require_admin]
    assert "actor_salesman_id" not in inspect.signature(shop.assign_order).parameters
    reads: list = []
    with _Patched((shop, "get_order", lambda oid: reads.append(oid) or {"id": oid, "status": "new", "salesman_id": None})):
        try:
            shop.assign_order(21, 1, actor="rep@example.com", reason=None, is_admin=False)
            raise AssertionError("a salesman must not claim an order")
        except shop.ShopError as e:
            assert str(e) == shop.ASSIGN_ADMIN_ONLY_MSG
        assert reads == []
    with _world(_FakeDB(_staff())), _shop():
        c = _client()
        r = c.post("/shop/orders/21/assign", headers=_h("rep"), json={"salesman_id": 1})
        assert r.status_code == 403, r.text[:200]
        assert c.post("/shop/orders/22/assign", headers=_h("store2"), json={"salesman_id": 1}).status_code == 403


# ── 9. salesmen login lookup, the unique index, render.yaml ───────────────────────

@test("salesman_for_user: exact lower-case match (no ILIKE wildcards); two rows sharing a login link neither")
def _():
    from app import shop
    sm = [{"id": 1, "name": "Rep One", "user_email": "axb@example.com"},
          {"id": 2, "name": "Rep Two", "user_email": "rep@example.com"}]
    fake = _FakeDB([], salesmen=sm)
    with _Patched((shop, "get_client", lambda: fake)):
        shop._salesman_cache.clear()
        assert shop.salesman_for_user("a_b@example.com") is None, "'_' is not a wildcard any more"
        assert shop.salesman_for_user("  REP@Example.com ")["id"] == 2
        assert ("salesmen", "select", [("eq", "user_email", "rep@example.com")], "*") in fake.calls
        assert not [c for c in fake.calls if any(f[0] == "ilike" for f in c[2])]
        assert shop.salesman_for_user("") is None and shop.salesman_for_user("   ") is None
        fake.tables["salesmen"].append({"id": 3, "name": "Rep Three", "user_email": "twin@example.com"})
        fake.tables["salesmen"].append({"id": 4, "name": "Rep Four", "user_email": "twin@example.com"})
        shop._salesman_cache.clear()
        assert shop.salesman_for_user("twin@example.com") is None
    shop._salesman_cache.clear()

    class _Dup:
        def table(self, _n):
            return self

        def update(self, _p):
            return self

        def eq(self, *_a):
            return self

        def execute(self):
            raise RuntimeError('duplicate key value violates unique constraint "salesmen_user_email_lower_key"')
    with _Patched((shop, "get_client", lambda: _Dup())):
        try:
            shop.upsert_salesman({"user_email": "Rep@Example.com"}, salesman_id=2)
            raise AssertionError("a login linked twice must be refused")
        except shop.ShopError as e:
            assert "already linked to another salesman" in str(e)


@test("salesmen unique index migration: duplicate guard first, unique on lower(user_email) where not null; reverse drops it")
def _():
    mig, rev = _sql("r7_salesmen_login_unique_migration.sql"), _sql("r7_salesmen_login_unique_reverse.sql")
    assert mig.index("more than one salesman") < mig.index("create unique index")
    assert re.search(r"create unique index if not exists salesmen_user_email_lower_key\s+on salesmen \(lower\(user_email\)\) "
                     r"where user_email is not null;", mig)
    assert "drop index if exists salesmen_user_email_lower_key;" in rev


@test("render.yaml: autoDeploy is false (deploys only through scripts/render_deploy.py)")
def _():
    text = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert re.search(r"^\s*autoDeploy:\s*false\s*$", text, re.M), "autoDeploy must be false"
    assert not re.search(r"^\s*autoDeploy:\s*true", text, re.M)
    assert "render_deploy" in text


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
