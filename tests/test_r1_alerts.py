"""R1 alerts + cron regression tests (24-Sep-2026) — order alerts that reach someone.

    python -m tests.test_r1_alerts

Pure tests, no database, no network: every provider, channel and table is a fake. Covers
app/emailer.py (per-recipient sends, Resend → Brevo → SMTP fall-through, SMTP off on Render),
app/shop_notify.py (separate rep/owner copies, retry keeps delivered channels and is labelled
re-sent, the one notify_failed rule, attempts history survives a broken get_order),
app/shop_jobs.py (unconfirmed_reminder selection + escalation + audit rows for every attempt,
hourly back-off, 07:00-22:00 Bahrain window, reassign resets the cadence, 7-day/10-reminder cap
into a daily digest, notify_retry attempts, stale_data_alert marks sent only on delivery, the run
lease, run_shop_jobs ok:false), app/shop.list_orders flags, app/schedules.py (hour >= RUN_HOUR
with the last_ran guard), the shop_api duplicate guard and the Cloudflare Worker cron files.
Same lightweight runner as tests/test_v3.py.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

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


# ── fakes ──────────────────────────────────────────────────────────────────────

NOW = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)


def ago(**kw) -> str:
    return (NOW - timedelta(**kw)).isoformat()


class _Resp:
    def __init__(self, data, count=None):
        self.data, self.count = data, count


class FakeQuery:
    """Enough of the supabase-py builder for shop_jobs / shop_notify: in-memory filtering on the
    columns the jobs use, writes recorded on the db."""

    def __init__(self, db, table):
        self.db, self.table, self.filters, self.op, self.payload, self._neg = db, table, [], "select", None, False

    def select(self, *a, **k):
        self.op = "select"
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def upsert(self, payload, **k):
        self.op, self.payload = "upsert", payload
        return self

    def delete(self):
        self.op = "delete"
        return self

    @property
    def not_(self):
        self._neg = True
        return self

    def _add(self, kind, col, val):
        self.filters.append((kind, col, val, self._neg))
        self._neg = False
        return self

    def eq(self, c, v): return self._add("eq", c, v)          # noqa: E704
    def is_(self, c, v): return self._add("is", c, v)         # noqa: E704
    def in_(self, c, v): return self._add("in", c, v)         # noqa: E704
    def lt(self, c, v): return self._add("lt", c, v)          # noqa: E704
    def gte(self, c, v): return self._add("gte", c, v)        # noqa: E704
    def like(self, c, v): return self._add("like", c, v)      # noqa: E704
    def order(self, *a, **k): return self                     # noqa: E704
    def limit(self, n): return self                           # noqa: E704
    def range(self, a, b): return self                        # noqa: E704

    def _match(self, r) -> bool:
        for kind, col, val, neg in self.filters:
            v = r.get(col)
            if kind == "eq":
                ok = v == val
            elif kind == "is":
                ok = (v is None) if val == "null" else (v == val)
            elif kind == "in":
                ok = v in val
            elif kind == "lt":
                ok = v is not None and str(v) < str(val)
            elif kind == "gte":
                ok = v is not None and str(v) >= str(val)
            else:
                ok = True
            if neg:
                ok = not ok
            if not ok:
                return False
        return True

    def execute(self):
        rows = self.db.rows.setdefault(self.table, [])
        matched = [r for r in rows if self._match(r)]
        self.db.queries.append((self.table, self.op, list(self.filters)))
        if self.op == "select":
            return _Resp([dict(r) for r in matched], count=len(matched))
        if self.op == "update":
            for r in matched:
                r.update(self.payload)
            self.db.writes.append((self.table, "update", dict(self.payload), [r.get("id") for r in matched]))
            return _Resp([dict(r) for r in matched])
        if self.op == "insert":
            payloads = self.payload if isinstance(self.payload, list) else [self.payload]
            for p in payloads:
                rows.append(dict(p, id=len(rows) + 1, ts=p.get("ts") or NOW.isoformat()))
            self.db.writes.append((self.table, "insert", payloads))
            return _Resp(payloads)
        if self.op == "upsert":
            self.db.writes.append((self.table, "upsert", dict(self.payload)))
            return _Resp([self.payload])
        self.db.writes.append((self.table, "delete", list(self.filters)))
        return _Resp([])


class FakeDB:
    def __init__(self, **tables):
        self.rows: dict[str, list[dict]] = {k: [dict(r) for r in v] for k, v in tables.items()}
        self.writes: list = []
        self.queries: list = []

    def table(self, name):
        return FakeQuery(self, name)

    def written(self, table, op):
        return [w for w in self.writes if w[0] == table and w[1] == op]


@contextmanager
def patched(obj, **attrs):
    """Temporarily set attributes on a module/object (restores even on failure)."""
    missing = object()
    saved = {k: getattr(obj, k, missing) for k in attrs}
    for k, v in attrs.items():
        setattr(obj, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is missing:
                delattr(obj, k)
            else:
                setattr(obj, k, v)


@contextmanager
def env(**vals):
    saved = {k: os.environ.get(k) for k in vals}
    for k, v in vals.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class Channels:
    """Recording stand-ins for shop_notify._email / _telegram / _whatsapp_cloud."""

    def __init__(self, email_ok=True, telegram_ok=False, wa_ok=False, email_fail_for=()):
        self.email_ok, self.telegram_ok, self.wa_ok, self.email_fail_for = email_ok, telegram_ok, wa_ok, set(email_fail_for)
        self.emails: list[tuple[str, str]] = []
        self.telegrams: list[str] = []
        self.whatsapps: list[tuple[str, str]] = []

    def email(self, subject, body, to):
        self.emails.append((subject, to))
        if not to:
            return {"sent": False, "emailed": False, "reason": "no_recipient"}
        ok = self.email_ok and not any(a.strip() in self.email_fail_for for a in to.split(","))
        return {"sent": ok, "emailed": ok, "to": to, "reason": "" if ok else "resend_error 403: testing mode"}

    def telegram(self, text):
        self.telegrams.append(text)
        return {"sent": self.telegram_ok, "reason": "" if self.telegram_ok else "not configured"}

    def whatsapp(self, number, text):
        self.whatsapps.append((number, text))
        return {"sent": self.wa_ok, "reason": "" if self.wa_ok else "cloud_api_not_configured"}

    def patch(self, module):
        return patched(module, _email=self.email, _telegram=self.telegram, _whatsapp_cloud=self.whatsapp)


LEGACY_FAILED = {   # the exact shape on the 16 failed production rows (keys only; no PII)
    "at": "2026-09-22T09:00:00+00:00", "recipients": ["rep@example.com", "owner@example.com"],
    "email": {"sent": False, "emailed": False, "reason": "resend_error 403: testing mode"},
    "telegram": {"sent": False, "reason": ""},
    "whatsapp": {"sent": False, "reason": "cloud_api_not_configured"},
}


# ── emailer ────────────────────────────────────────────────────────────────────

class _HttpResp:
    def __init__(self, status, text="{}"):
        self.status_code, self.text = status, text


def _fake_post(script: dict):
    """requests.post stand-in: script = {provider_host: [status, status, ...]} consumed in order."""
    calls: list[tuple[str, str]] = []

    def post(url, **kw):
        host = "resend" if "resend" in url else "brevo"
        to = kw["json"]["to"]
        to = to[0] if host == "resend" else to[0]["email"]
        calls.append((host, to))
        queue = script.get(host) or [500]
        status = queue.pop(0) if len(queue) > 1 else queue[0]
        return _HttpResp(status, "rejected" if status >= 400 else "{}")
    post.calls = calls
    return post


class _FakeSMTP:
    sent: list[tuple[str, list[str]]] = []
    timeouts: list[int] = []
    fail = False

    def __init__(self, host, port, timeout=0):
        _FakeSMTP.timeouts.append(timeout)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, u, p):
        pass

    def sendmail(self, frm, to, msg):
        if _FakeSMTP.fail:
            raise ConnectionError("smtp down")
        _FakeSMTP.sent.append((frm, list(to)))


@test("emailer: per-recipient send, Resend 403 falls through to Brevo then SMTP")
def _():
    import requests
    from app import emailer
    post = _fake_post({"resend": [403], "brevo": [401]})
    _FakeSMTP.sent, _FakeSMTP.fail, _FakeSMTP.timeouts = [], False, []
    with env(RESEND_API_KEY="k", BREVO_API_KEY="b", SMTP_USER="u@x", SMTP_PASS="p", EMAIL_FROM="YQ <no-reply@yq.test>",
             RENDER=None, EMAIL_SMTP_ENABLED=None), \
            patched(requests, post=post), patched(emailer.smtplib, SMTP=_FakeSMTP):
        assert [n for n, _ in emailer.providers()] == ["resend", "brevo", "smtp"]
        r = emailer.send_html("s", "<p>x</p>", to="rep@x.test, owner@y.test")
    assert r["emailed"] is True and r["via"] == "smtp", r
    assert [x["to"] for x in r["results"]] == ["rep@x.test", "owner@y.test"], r
    assert all(x["tried"] == ["resend", "brevo", "smtp"] and x["emailed"] for x in r["results"]), r
    assert sorted(a for _, a in post.calls) == ["owner@y.test", "owner@y.test", "rep@x.test", "rep@x.test"], post.calls
    assert [to for _, to in _FakeSMTP.sent] == [["rep@x.test"], ["owner@y.test"]], _FakeSMTP.sent
    assert r["to"] == "rep@x.test, owner@y.test" and "failed" not in r
    assert _FakeSMTP.timeouts == [5, 5], "a blocked port must cost 5 s per address, not 20"


@test("emailer: SMTP is not registered on Render (RENDER=true) unless EMAIL_SMTP_ENABLED=1 — the port is blocked")
def _():
    import requests
    from app import emailer
    base = dict(RESEND_API_KEY="k", BREVO_API_KEY=None, SMTP_USER="u@x", SMTP_PASS="p")
    with env(**base, RENDER="true", EMAIL_SMTP_ENABLED=None):
        assert [n for n, _ in emailer.providers()] == ["resend"], emailer.providers()
        assert emailer.smtp_allowed() is False and emailer.smtp_configured() is False
        # a rep rejected by Resend's testing mode fails fast — no 20 s SMTP wait, no smtp in tried
        post = _fake_post({"resend": [403]})
        with patched(requests, post=post):
            r = emailer.send_one("s", "<p>x</p>", "rep@x.test")
        assert r["emailed"] is False and r["tried"] == ["resend"] and "403" in r["reason"], r
    with env(**base, RENDER="true", EMAIL_SMTP_ENABLED="1"):
        assert [n for n, _ in emailer.providers()] == ["resend", "smtp"], "the explicit override brings SMTP back"
    with env(**base, RENDER="TRUE", EMAIL_SMTP_ENABLED="0"):
        assert [n for n, _ in emailer.providers()] == ["resend"]
    with env(**base, RENDER=None, EMAIL_SMTP_ENABLED=None):
        assert [n for n, _ in emailer.providers()] == ["resend", "smtp"], "off Render SMTP stays available"
    assert emailer._SMTP_CONNECT_TIMEOUT_S == 5
    assert "allowed on Render" not in emailer.__doc__ and "allowed on Render" not in (emailer._send_smtp.__doc__ or "")


@test("emailer: one address rejected by Resend (testing mode) never loses the other")
def _():
    import requests
    from app import emailer
    # Resend answers 403 for the rep and 201 for the owner (the account's own address); no fallback configured.
    def post(url, **kw):
        to = kw["json"]["to"][0]
        return _HttpResp(201 if to == "owner@y.test" else 403, "You can only send testing emails to your own email address")
    with env(RESEND_API_KEY="k", BREVO_API_KEY=None, SMTP_USER=None, SMTP_PASS=None), patched(requests, post=post):
        r = emailer.send_html("s", "<p>x</p>", to="rep@x.test,owner@y.test")
    assert r["emailed"] is True and r["to"] == "owner@y.test" and r["via"] == "resend", r
    assert r["failed"] == ["rep@x.test"] and "resend_error 403" in r["reason"], r
    rep = next(x for x in r["results"] if x["to"] == "rep@x.test")
    assert rep["emailed"] is False and rep["tried"] == ["resend"] and "403" in rep["reason"], rep


@test("emailer: nothing configured → every recipient says no_email_provider, nothing raised")
def _():
    from app import emailer
    with env(RESEND_API_KEY=None, BREVO_API_KEY=None, SMTP_USER=None, SMTP_PASS=None):
        assert emailer.providers() == []
        r = emailer.send_html("s", "<p>x</p>", to="a@x.test")
    assert r["emailed"] is False and r["results"][0]["reason"].startswith("no_email_provider"), r
    with env(ALERT_EMAIL_TO=None):
        assert emailer.send_html("s", "<p>x</p>")["reason"].startswith("no_recipient")


@test("notify: notify_failed is the one rule — error-only and a null older than 15 min count as failed")
def _():
    from app.shop_notify import NOTIFY_GRACE_MIN, notify_failed
    assert NOTIFY_GRACE_MIN == 15
    assert notify_failed(LEGACY_FAILED, ago(hours=1), NOW) is True
    assert notify_failed({"at": "x", "error": "boom"}, ago(hours=1), NOW) is True, "raised before any send = nobody told"
    assert notify_failed({}, ago(hours=1), NOW) is True
    assert notify_failed(dict(LEGACY_FAILED, email_owner={"sent": True}), ago(hours=1), NOW) is False
    assert notify_failed(None, ago(minutes=5), NOW) is False, "the background task may still be running"
    assert notify_failed(None, ago(minutes=15), NOW) is True
    assert notify_failed(None, None, NOW) is True and notify_failed(None, "garbage", NOW) is True


@test("emailer: a 429 from Resend is retried once, and a provider exception is a reason not a crash")
def _():
    import requests
    from app import emailer
    post = _fake_post({"resend": [429, 200]})
    with env(RESEND_API_KEY="k", BREVO_API_KEY=None, SMTP_USER=None, SMTP_PASS=None), \
            patched(requests, post=post), patched(emailer, _RATE_LIMIT_PAUSE_S=0.0):
        r = emailer.send_one("s", "<p>x</p>", "a@x.test")
    assert r["emailed"] and r["via"] == "resend" and len(post.calls) == 2, (r, post.calls)

    def boom(url, **kw):
        raise ConnectionError("dns")
    with env(RESEND_API_KEY="k", BREVO_API_KEY=None, SMTP_USER=None, SMTP_PASS=None), patched(requests, post=boom):
        r = emailer.send_one("s", "<p>x</p>", "a@x.test")
    assert r["emailed"] is False and "resend_error: ConnectionError" in r["reason"], r


# ── shop_notify ────────────────────────────────────────────────────────────────

@test("notify: all_channels_failed reads the legacy shape, the new shape and a missing result")
def _():
    from app.shop_notify import all_channels_failed, attempt_count, channel_results
    assert all_channels_failed(LEGACY_FAILED) is True and attempt_count(LEGACY_FAILED) == 1
    assert all_channels_failed(None) is True and attempt_count(None) == 0
    assert all_channels_failed({"at": "x", "error": "boom"}) is True
    new = dict(LEGACY_FAILED, email_rep={"sent": False}, email_owner={"sent": True}, attempts=["a", "b"])
    del new["email"]
    assert all_channels_failed(new) is False and attempt_count(new) == 2
    assert channel_results(new) == {"email_rep": False, "email_owner": True, "telegram": False, "whatsapp": False}
    # the customer's receipt alone does not count as "notified"
    assert all_channels_failed(dict(LEGACY_FAILED, customer_email={"sent": True})) is True


def _order(**over) -> dict:
    o = {"id": 91, "order_no": "YQ-2609-0091", "status": "new", "source": "market", "customer_name": "Test Shop Owner",
         "customer_phone": "33001122", "customer_shop": "Test Shop", "customer_area": "Manama",
         "customer_email": "buyer@shop.test", "total_bhd": 24.5, "subtotal_bhd": 24.5, "units_count": 3,
         "token": "tok" * 8, "notify_result": None, "salesman_id": 4,
         "salesman": {"id": 4, "name": "Ahmed", "email": "ahmed@yq.test", "phone": "39000000"},
         "lines": [{"item_code": "C18", "display_name": "Cable", "qty": 3, "unit_price_bhd": 8.166, "line_total_bhd": 24.5}]}
    o.update(over)
    return o


@test("notify: rep and owner get SEPARATE copies, every channel recorded, attempt 1 persisted")
def _():
    import app.database as database
    import app.shop as shop
    from app import shop_notify
    db = FakeDB(shop_orders=[{"id": 91}])
    ch = Channels(email_ok=True, email_fail_for={"ahmed@yq.test"})
    with env(ALERT_EMAIL_TO="owner@yq.test, ahmed@yq.test", APP_BASE_URL="https://ops.test"), \
            patched(shop, get_order=lambda oid: _order()), patched(database, get_client=lambda: db), ch.patch(shop_notify):
        r = shop_notify.notify_new_order(91)
    assert r["recipients"] == ["ahmed@yq.test", "owner@yq.test"], r["recipients"]     # the rep's address is not doubled
    assert [to for _, to in ch.emails] == ["ahmed@yq.test", "owner@yq.test", "buyer@shop.test"], ch.emails
    assert r["email_rep"]["sent"] is False and r["email_owner"]["sent"] is True and r["customer_email"]["sent"] is True
    assert "email" not in r, "the legacy combined key must not reappear next to the split copies"
    assert r["telegram"]["sent"] is False and r["whatsapp"]["sent"] is False and ch.whatsapps[0][0] == "39000000"
    assert ch.telegrams[0].startswith("Assigned to Ahmed.\nNew order YQ-2609-0091"), ch.telegrams[0]
    assert r["attempts"] == [r["at"]] and r["attempt"] == 1
    assert shop_notify.all_channels_failed(r) is False
    (table, op, payload, ids), = db.written("shop_orders", "update")
    assert ids == [91] and payload["notify_result"] is r and payload["notified_at"]


@test("notify: retry keeps delivered channels (no second receipt to the merchant), counts a legacy row as attempt 1")
def _():
    import app.database as database
    import app.shop as shop
    from app import shop_notify
    prev = dict(LEGACY_FAILED, customer_email={"sent": True, "emailed": True, "to": "buyer@shop.test"})
    db = FakeDB(shop_orders=[{"id": 91}])
    ch = Channels(email_ok=True)
    placed = (datetime.now(timezone.utc) - timedelta(hours=5, minutes=10)).isoformat()
    with env(ALERT_EMAIL_TO="owner@yq.test"), patched(shop, get_order=lambda oid: _order(notify_result=prev, created_at=placed)), \
            patched(database, get_client=lambda: db), ch.patch(shop_notify):
        r = shop_notify.notify_new_order(91, retry=True)
    assert [to for _, to in ch.emails] == ["ahmed@yq.test", "owner@yq.test"], ch.emails   # buyer NOT re-sent
    assert r["customer_email"].get("kept") is True and r["customer_email"]["sent"] is True
    assert r["attempt"] == 2 and r["attempts"] == [LEGACY_FAILED["at"], r["at"]], r["attempts"]
    assert r["email_rep"]["sent"] and r["email_owner"]["sent"] and not shop_notify.all_channels_failed(r)
    # a re-send days later must not read like a fresh order: subject, Telegram and WhatsApp say so
    tag = "Re-sent (first alert did not reach you) · placed 5 h ago"
    assert all(tag in s for s, _ in ch.emails), ch.emails
    assert tag in ch.telegrams[0] and tag in ch.whatsapps[0][1], (ch.telegrams, ch.whatsapps)
    # a first (non-retry) run never carries anything over, even if the old row says sent — and is not labelled re-sent
    ch2 = Channels(email_ok=True)
    with env(ALERT_EMAIL_TO="owner@yq.test"), patched(shop, get_order=lambda oid: _order(notify_result=prev, created_at=placed)), \
            patched(database, get_client=lambda: db), ch2.patch(shop_notify):
        r2 = shop_notify.notify_new_order(91)
    assert "buyer@shop.test" in [to for _, to in ch2.emails] and "kept" not in r2["customer_email"]
    assert not any("Re-sent" in s for s, _ in ch2.emails) and "Re-sent" not in ch2.telegrams[0]


@test("notify: an order the rep placed himself gets no rep copy and no WhatsApp; errors are persisted too")
def _():
    import app.database as database
    import app.shop as shop
    from app import shop_notify
    db = FakeDB(shop_orders=[{"id": 91}])
    ch = Channels(email_ok=True)
    with env(ALERT_EMAIL_TO="owner@yq.test"), patched(shop, get_order=lambda oid: _order(source="salesman", placed_by="ahmed@yq.test", customer_email=None)), \
            patched(database, get_client=lambda: db), ch.patch(shop_notify):
        r = shop_notify.notify_new_order(91)
    assert r["recipients"] == ["owner@yq.test"] and "email_rep" not in r and "whatsapp" not in r, r
    assert r["email_owner"]["sent"] and not ch.whatsapps and not ch.telegrams[0].startswith("Assigned")
    # nothing to email at all → an explicit no_recipient row, still no crash
    with env(ALERT_EMAIL_TO=None), patched(shop, get_order=lambda oid: _order(salesman={"id": 4, "name": "Ahmed"}, customer_email=None)), \
            patched(database, get_client=lambda: db), ch.patch(shop_notify):
        r = shop_notify.notify_new_order(91)
    assert r["email"]["sent"] is False and r["email"]["reason"].startswith("no_recipient"), r
    # get_order raising → error recorded AND the attempt persisted (so retries still stop at 3)
    def boom(oid):
        raise RuntimeError("db down")
    db = FakeDB(shop_orders=[{"id": 91}])     # a row with no history yet
    with patched(shop, get_order=boom), patched(database, get_client=lambda: db), ch.patch(shop_notify):
        r = shop_notify.notify_new_order(91)
    assert r["error"].startswith("RuntimeError") and r["attempt"] == 1 and shop_notify.all_channels_failed(r)
    assert db.written("shop_orders", "update")[-1][2]["notify_result"] is r
    # ... and a persistent get_order failure does NOT reset the history: the stored attempts are read the light way
    db2 = FakeDB(shop_orders=[{"id": 91, "notify_result": dict(LEGACY_FAILED, attempts=["a", "b"])}])
    with patched(shop, get_order=boom), patched(database, get_client=lambda: db2), ch.patch(shop_notify):
        r = shop_notify.notify_new_order(91)
    assert r["attempt"] == 3 and r["attempts"] == ["a", "b", r["at"]], r
    assert shop_notify.attempt_count(r) >= shop_notify.MAX_NOTIFY_ATTEMPTS, "notify_retry can now mark it exhausted"


# ── shop_jobs ──────────────────────────────────────────────────────────────────

REPS = {4: {"id": 4, "name": "Ahmed", "email": "ahmed@yq.test", "phone": "39000000"},
        5: {"id": 5, "name": "Faisal", "email": "", "phone": "39000001"}}


def _jobs_ctx(db, ch: Channels, settings: dict | None = None, now: datetime = NOW):
    """Patch everything unconfirmed_reminder / notify_retry / stale_data_alert reach for.
    NOW is 10:00 UTC = 13:00 Bahrain, inside the 07:00-22:00 send window."""
    import app.shop as shop
    from app import shop_jobs, shop_notify
    vals = dict(shop.SETTING_DEFAULTS, **(settings or {}))

    @contextmanager
    def ctx():
        with patched(shop_jobs, get_client=lambda: db, _now=lambda: now), \
                patched(shop, shop_settings=lambda force=False: vals, _salesman_by_id=lambda sid: REPS.get(sid)), \
                ch.patch(shop_notify), env(ALERT_EMAIL_TO="owner@yq.test", APP_BASE_URL="https://ops.test"):
            yield
    return ctx()


def _events(db) -> dict[int, dict]:
    """{order_id: detail} of the 'reminded' rows written in this run (the last one per order)."""
    return {p["order_id"]: p["detail"] for _, _, ps in db.written("shop_order_events", "insert") for p in ps}


def _unconfirmed_rows() -> list[dict]:
    base = {"status": "new", "customer_shop": "Shop", "customer_area": "Manama", "total_bhd": 30.0,
            "sla_notified_at": None, "source": "market", "salesman_id": 4, "salesman_name": "Ahmed"}
    return [
        dict(base, id=1, order_no="A", created_at=ago(hours=3), assigned_at=None),                       # 3 h, never reminded → rep
        dict(base, id=2, order_no="B", created_at=ago(hours=5), assigned_at=ago(hours=5)),               # 5 h → rep + owner (2x SLA)
        dict(base, id=3, order_no="C", created_at=ago(hours=3), assigned_at=ago(hours=3)),               # reminded 1 h ago → wait
        dict(base, id=4, order_no="D", created_at=ago(hours=1), assigned_at=ago(hours=1)),               # inside the SLA
        dict(base, id=5, order_no="E", created_at=ago(hours=5), assigned_at=ago(minutes=30)),            # assigned 30 min ago: not late
        dict(base, id=6, order_no="F", created_at=ago(hours=6), assigned_at=None, salesman_id=None, salesman_name=None),   # unassigned: other job
        dict(base, id=7, order_no="G", created_at=ago(hours=6), assigned_at=ago(hours=6), status="confirmed"),             # done
        dict(base, id=8, order_no="H", created_at=ago(hours=26), assigned_at=ago(hours=26), salesman_id=5, salesman_name="Faisal"),  # rep without email
    ]


@test("jobs: unconfirmed_reminder picks assigned+new past the SLA from assignment, escalates past 2x, writes 'reminded'")
def _():
    from app import shop_jobs
    db = FakeDB(shop_orders=_unconfirmed_rows(),
                shop_order_events=[{"id": 1, "order_id": 3, "event": "reminded", "ts": ago(hours=1), "detail": {"rep": True, "owner": False}}])
    ch = Channels(email_ok=True)
    with _jobs_ctx(db, ch):
        out = shop_jobs.unconfirmed_reminder()
    assert out["sla_min"] == 120 and out["checked"] == 4, out            # A B C H — D inside the SLA, E assigned 30 min ago, F unassigned, G confirmed
    assert sorted(out["reminded"]) == ["A", "B"], out                    # H's rep has no email and WhatsApp did not deliver
    assert sorted(out["escalated"]) == ["B", "H"], out
    # the query itself only asked for new + assigned rows
    q = next(f for t, op, f in db.queries if t == "shop_orders" and op == "select")
    assert ("eq", "status", "new", False) in q and ("is", "salesman_id", "null", True) in q, q
    # one digest per rep: Ahmed gets A+B by email (+WhatsApp attempt), Faisal has no email → WhatsApp only (not delivered)
    rep_mails = [to for s, to in ch.emails if "waiting for your confirmation" in s]
    assert rep_mails == ["ahmed@yq.test"], ch.emails
    assert out["reps"]["Ahmed"]["sent"] is True and out["reps"]["Faisal"]["sent"] is False
    assert {n for n, _ in ch.whatsapps} == {"39000000", "39000001"}
    # owner escalation: one Telegram + one email naming the rep
    owner_mail = [s for s, to in ch.emails if to == "owner@yq.test"]
    assert len(owner_mail) == 1 and "unconfirmed past 4 h 00 min" in owner_mail[0], owner_mail
    assert any("Faisal" in t and "Ahmed" in t for t in ch.telegrams), ch.telegrams
    assert out["owner"]["sent"] is True
    # audit rows: A (rep), B (rep+owner), H (owner delivered, rep tried and not reached) ; C/D/E/F/G untouched
    events = _events(db)
    assert set(events) == {1, 2, 8}, events
    assert events[1] == {"rep": True, "owner": False, "attempted": {"rep": True, "owner": False}, "attempted_at": NOW.isoformat(),
                         "level": "rep", "age_min": 180, "sla_min": 120}, events[1]
    assert events[2]["rep"] and events[2]["owner"] and events[2]["level"] == "owner" and events[2]["attempted"] == {"rep": True, "owner": True}
    assert events[8] == {"rep": False, "owner": True, "attempted": {"rep": True, "owner": True}, "attempted_at": NOW.isoformat(),
                         "level": "owner", "age_min": 26 * 60, "sla_min": 120}, events[8]
    assert all(p["actor"] == "shop_jobs" and p["event"] == "reminded" for _, _, ps in db.written("shop_order_events", "insert") for p in ps)
    assert not any("resend" in json.dumps(d).lower() for d in events.values()), "provider error text never lands in the timeline"
    stamped = sorted(i for _, _, _, ids in db.written("shop_orders", "update") for i in ids)
    assert stamped == [1, 2, 8], stamped
    assert "digest" not in out and "backoff" not in out


@test("jobs: unconfirmed_reminder — nothing delivered → no stamp, but the attempt is recorded and held for an hour")
def _():
    from app import shop_jobs
    db = FakeDB(shop_orders=_unconfirmed_rows()[:2], shop_order_events=[])
    ch = Channels(email_ok=False)
    with _jobs_ctx(db, ch):
        out = shop_jobs.unconfirmed_reminder()
    assert out["reminded"] == [] and out["escalated"] == [] and out["reason"].startswith("no channel delivered"), out
    assert not db.written("shop_orders", "update"), "sla_notified_at is stamped only when someone was reached"
    events = _events(db)
    assert set(events) == {1, 2} and events[1]["level"] == "attempt", events
    assert events[1] == {"rep": False, "owner": False, "attempted": {"rep": True, "owner": False}, "attempted_at": NOW.isoformat(),
                         "level": "attempt", "age_min": 180, "sla_min": 120}, events[1]
    assert events[2]["attempted"] == {"rep": True, "owner": True} and not events[2]["rep"] and not events[2]["owner"]
    assert len(ch.emails) == 2, ch.emails            # Ahmed's digest + the owner escalation, once each
    # the very next tick (a dead channel): nothing is sent again — back-off, not a 15-minute hammer
    ch2 = Channels(email_ok=True)
    with _jobs_ctx(db, ch2):
        out = shop_jobs.unconfirmed_reminder()
    assert ch2.emails == [] and out["reminded"] == [] and sorted(out["backoff"]) == ["A", "B"] and "reason" not in out, out
    # 61 minutes later the rep channel is tried again (and now delivers)
    ch3 = Channels(email_ok=True)
    with _jobs_ctx(db, ch3, now=NOW + timedelta(minutes=61)):
        out = shop_jobs.unconfirmed_reminder()
    assert sorted(out["reminded"]) == ["A", "B"] and "backoff" not in out, out
    assert sorted(out["escalated"]) == ["A", "B"], out            # A is 241 min from assignment by now: past 2x the SLA
    # the 12 h cadence: an order reminded 13 h ago is due again, one reminded 11 h ago is not
    rows = [dict(_unconfirmed_rows()[0], id=1, order_no="A"), dict(_unconfirmed_rows()[0], id=2, order_no="B")]
    db = FakeDB(shop_orders=rows, shop_order_events=[
        {"id": 1, "order_id": 1, "event": "reminded", "ts": ago(hours=13), "detail": {"rep": True}},
        {"id": 2, "order_id": 2, "event": "reminded", "ts": ago(hours=11), "detail": {"rep": True}}])
    ch = Channels(email_ok=True)
    with _jobs_ctx(db, ch):
        out = shop_jobs.unconfirmed_reminder()
    assert out["reminded"] == ["A"], out
    # a failed attempt 30 min ago holds; one 61 min ago is retried
    db = FakeDB(shop_orders=rows, shop_order_events=[
        {"id": 1, "order_id": 1, "event": "reminded", "ts": ago(minutes=30), "detail": {"rep": False, "owner": False, "attempted": {"rep": True, "owner": False}}},
        {"id": 2, "order_id": 2, "event": "reminded", "ts": ago(minutes=61), "detail": {"rep": False, "owner": False, "attempted": {"rep": True, "owner": False}}}])
    ch = Channels(email_ok=True)
    with _jobs_ctx(db, ch):
        out = shop_jobs.unconfirmed_reminder()
    assert out["reminded"] == ["B"] and out["backoff"] == ["A"], out
    # settings switch it off
    with _jobs_ctx(FakeDB(shop_orders=rows), Channels(), {"shop_confirm_sla_min": "0"}):
        assert shop_jobs.unconfirmed_reminder()["skipped"]


@test("jobs: nudges go out 07:00-22:00 Bahrain only; notify_retry is the first alert and is not gated")
def _():
    import app.database as database
    import app.shop as shop
    from app import shop_jobs
    bahrain = timezone(timedelta(hours=3))

    def rows_at(now: datetime) -> list[dict]:
        base = dict(_unconfirmed_rows()[0])
        return [dict(base, id=1, order_no="A", created_at=(now - timedelta(hours=3)).isoformat(), assigned_at=None),
                dict(base, id=2, order_no="B", created_at=(now - timedelta(hours=5)).isoformat(), assigned_at=(now - timedelta(hours=5)).isoformat()),
                {"id": 9, "order_no": "U", "status": "new", "salesman_id": None, "created_at": (now - timedelta(hours=1)).isoformat(),
                 "sla_notified_at": None, "customer_shop": "Shop", "customer_area": "Riffa", "total_bhd": 12.0}]
    for hour, minute, expect_run in ((6, 59, False), (7, 0, True), (13, 0, True), (21, 59, True), (22, 0, False), (3, 15, False)):
        now = datetime(2026, 9, 24, hour, minute, tzinfo=bahrain).astimezone(timezone.utc)
        assert shop_jobs.in_send_window(now) is expect_run, (hour, minute)
        db = FakeDB(shop_orders=rows_at(now), shop_order_events=[])
        ch = Channels(email_ok=True)
        with _jobs_ctx(db, ch, now=now):
            a = shop_jobs.unconfirmed_reminder()
            b = shop_jobs.unassigned_reminder()
        if expect_run:
            assert a["reminded"] and b["notified"] == 1, (hour, a, b)
        else:
            assert a["skipped"].startswith("outside 07:00-22:00 Bahrain") and b["skipped"].startswith("outside"), (hour, a, b)
            assert ch.emails == [] and not db.writes, (hour, db.writes)
    # notify_retry at 03:15 Bahrain still re-sends a new-order alert nobody got
    now = datetime(2026, 9, 24, 3, 15, tzinfo=bahrain).astimezone(timezone.utc)
    db = FakeDB(shop_orders=[{"id": 1, "order_no": "A", "status": "new", "created_at": (now - timedelta(hours=2)).isoformat(),
                              "notify_result": dict(LEGACY_FAILED)}])
    ch = Channels(email_ok=True)
    with _jobs_ctx(db, ch, now=now), patched(shop, get_order=lambda oid: _order(id=oid, notify_result=dict(LEGACY_FAILED), customer_email=None)), \
            patched(database, get_client=lambda: db):
        out = shop_jobs.notify_retry()
    assert out["retried"] == ["A"], out


@test("jobs: a reassigned order starts its cadence over — reminders older than assigned_at are ignored, age from assignment")
def _():
    from app import shop_jobs
    # created 5 h ago, first rep reminded 4 h ago, handed to Ahmed 3 h ago: Ahmed has never been reminded
    row = dict(_unconfirmed_rows()[0], id=1, order_no="A", created_at=ago(hours=5), assigned_at=ago(hours=3))
    db = FakeDB(shop_orders=[row], shop_order_events=[
        {"id": 1, "order_id": 1, "event": "reminded", "ts": ago(hours=4), "detail": {"rep": True, "owner": True, "level": "owner"}}])
    ch = Channels(email_ok=True)
    with _jobs_ctx(db, ch):
        out = shop_jobs.unconfirmed_reminder()
    assert out["reminded"] == ["A"] and out["escalated"] == [], out     # 3 h from assignment: past the SLA, not yet 2x
    ev = _events(db)[1]
    assert ev["age_min"] == 180 and ev["level"] == "rep", ev                # from assigned_at, not created_at
    assert "waiting 3 h 00 min" in ch.whatsapps[0][1], ch.whatsapps      # the rep's text counts from assignment too
    # the same row with the old reminder AFTER assignment: the 12 h cadence holds
    db = FakeDB(shop_orders=[row], shop_order_events=[
        {"id": 1, "order_id": 1, "event": "reminded", "ts": ago(hours=2), "detail": {"rep": True}}])
    ch = Channels(email_ok=True)
    with _jobs_ctx(db, ch):
        out = shop_jobs.unconfirmed_reminder()
    assert out["reminded"] == [] and ch.emails == [], out


@test("jobs: after 7 days or 10 delivered reminders an order leaves per-order chasing for one daily owner digest")
def _():
    from app import shop_jobs
    base = _unconfirmed_rows()[0]
    old = dict(base, id=1, order_no="OLD", created_at=ago(days=8), assigned_at=ago(days=8))
    many = dict(base, id=2, order_no="MANY", created_at=ago(days=2), assigned_at=ago(days=2))
    fresh = dict(base, id=3, order_no="FRESH", created_at=ago(hours=3), assigned_at=ago(hours=3))
    ten = [{"id": i + 1, "order_id": 2, "event": "reminded", "ts": ago(hours=46 - 4 * i), "detail": {"rep": True}} for i in range(10)]
    db = FakeDB(shop_orders=[old, many, fresh], shop_order_events=ten, app_settings=[])
    ch = Channels(email_ok=True)
    with _jobs_ctx(db, ch):
        out = shop_jobs.unconfirmed_reminder()
    assert out["reminded"] == ["FRESH"] and out["escalated"] == [], out
    assert out["digest"]["orders"] == ["OLD", "MANY"] and out["digest"]["sent"] is True and out["digest"]["email"] is True, out["digest"]
    owner_mail = [s for s, to in ch.emails if to == "owner@yq.test"]
    assert len(owner_mail) == 1 and "daily digest: 2 order(s)" in owner_mail[0], ch.emails
    body = ch.telegrams[0]
    assert "8 days" in body and "OLD" in body and "MANY" in body and "FRESH" not in body and "Ahmed" in body, body
    marker = db.written("app_settings", "upsert")
    assert marker and marker[0][2]["key"] == "shop_unconfirmed_digest_at", marker
    ev = _events(db)
    assert ev[1]["level"] == "digest" and ev[1]["owner"] and not ev[1]["rep"] and ev[2]["level"] == "digest" and ev[3]["level"] == "rep", ev
    # the digest already went out today → skipped; the fresh order's rep cadence is untouched
    db = FakeDB(shop_orders=[old, many], shop_order_events=ten,
                app_settings=[{"key": "shop_unconfirmed_digest_at", "value": ago(hours=3)}])
    ch = Channels(email_ok=True)
    with _jobs_ctx(db, ch):
        out = shop_jobs.unconfirmed_reminder()
    assert out["digest"]["skipped"] and ch.emails == [] and ch.telegrams == [] and not db.writes, out
    # a digest nobody received leaves no marker and no rows, so the next tick tries again
    db = FakeDB(shop_orders=[old], shop_order_events=[], app_settings=[])
    with _jobs_ctx(db, Channels(email_ok=False)):
        out = shop_jobs.unconfirmed_reminder()
    assert out["digest"]["sent"] is False and out["digest"]["reason"].startswith("no channel") and not db.writes, out


@test("jobs: the run lease — one conditional upsert on app_settings, a live lease skips the run, released at the end")
def _():
    from app import shop_jobs
    db = FakeDB(app_settings=[])
    with _jobs_ctx(db, Channels()):
        first = shop_jobs.acquire_lease(db)
        second = shop_jobs.acquire_lease(db)
    assert first["acquired"] is True and first["expires_at"] == "2026-09-24T10:10:00Z", first
    assert second == {"acquired": False, "held_until": "2026-09-24T10:10:00Z"}, second
    row = next(r for r in db.rows["app_settings"] if r["key"] == "shop_jobs_lock")
    assert row["value"] == "2026-09-24T10:10:00Z" and row["updated_by"] == "shop_jobs"
    # a lease from a run that died is taken over once it expired — by the atomic UPDATE ... WHERE value < now
    with _jobs_ctx(db, Channels(), now=NOW + timedelta(minutes=11)):
        third = shop_jobs.acquire_lease(db)
    assert third["acquired"] is True and third["expires_at"] == "2026-09-24T10:21:00Z", third
    conditional = [f for t, op, f in db.queries if t == "app_settings" and op == "update"
                   and ("lt", "value", "2026-09-24T10:11:00Z", False) in f]
    assert conditional, db.queries                                   # the take-over is UPDATE ... WHERE value < now
    with _jobs_ctx(db, Channels()):
        shop_jobs.release_lease(db)
        assert row["value"] == "1970-01-01T00:00:00Z"
        assert shop_jobs.acquire_lease(db)["acquired"] is True
    assert len(db.written("app_settings", "insert")) == 1, "only the first-ever lease is an insert"
    # run_shop_jobs: skipped while another caller holds it, otherwise runs everything and lets go
    db = FakeDB(app_settings=[{"key": "shop_jobs_lock", "value": "2026-09-24T10:05:00Z"}])
    ran: list[str] = []
    jobs = (("fine", lambda: ran.append("fine") or {"checked": 0}),)
    with _jobs_ctx(db, Channels()), patched(shop_jobs, JOBS=jobs):
        out = shop_jobs.run_shop_jobs()
    assert out["ok"] is True and out["skipped"] == "another run holds the lease" and ran == [] and "fine" not in out, out
    db = FakeDB(app_settings=[{"key": "shop_jobs_lock", "value": "1970-01-01T00:00:00Z"}])
    with _jobs_ctx(db, Channels()), patched(shop_jobs, JOBS=jobs):
        out = shop_jobs.run_shop_jobs()
    assert out["ok"] is True and out["fine"] == {"checked": 0} and ran == ["fine"], out
    assert db.rows["app_settings"][0]["value"] == "1970-01-01T00:00:00Z", "released at the end"
    # a job that raises still releases the lease
    def boom():
        raise RuntimeError("x")
    with _jobs_ctx(db, Channels()), patched(shop_jobs, JOBS=(("broken", boom),)):
        out = shop_jobs.run_shop_jobs()
    assert out["ok"] is False and db.rows["app_settings"][0]["value"] == "1970-01-01T00:00:00Z"
    # a lock table that cannot be read never silences the jobs for good (fail-open, error reported)
    class Broken:
        def table(self, name):
            raise ConnectionError("db down")
    with patched(shop_jobs, get_client=lambda: Broken(), _now=lambda: NOW), patched(shop_jobs, JOBS=jobs):
        out = shop_jobs.run_shop_jobs()
    assert out["fine"] == {"checked": 0} and out["lease"]["error"].startswith("ConnectionError"), out


@test("shop: list_orders carries notify_failed + notify_attempts, never the whole notify_result")
def _():
    import app.shop as shop
    now = datetime.now(timezone.utc)
    rows = [
        {"id": 1, "order_no": "A", "status": "new", "created_at": (now - timedelta(hours=2)).isoformat(), "notify_result": dict(LEGACY_FAILED)},
        {"id": 2, "order_no": "B", "status": "new", "created_at": (now - timedelta(hours=2)).isoformat(),
         "notify_result": dict(LEGACY_FAILED, email_owner={"sent": True}, attempts=["a", "b"])},
        {"id": 3, "order_no": "C", "status": "new", "created_at": (now - timedelta(minutes=3)).isoformat(), "notify_result": None},
        {"id": 4, "order_no": "D", "status": "new", "created_at": (now - timedelta(minutes=30)).isoformat(), "notify_result": None},
        {"id": 5, "order_no": "E", "status": "new", "created_at": (now - timedelta(hours=1)).isoformat(), "notify_result": {"at": "x", "error": "boom"}},
        {"id": 6, "order_no": "F", "status": "delivered", "created_at": (now - timedelta(days=3)).isoformat(), "notify_result": dict(LEGACY_FAILED)},
    ]
    db = FakeDB(shop_orders=rows)
    with patched(shop, get_client=lambda: db):
        r = shop.list_orders(status="new")
    got = {o["order_no"]: (o["notify_failed"], o["notify_attempts"]) for o in r["orders"]}
    assert got == {"A": (True, 1), "B": (False, 2), "C": (False, 0), "D": (True, 0), "E": (True, 1)}, got
    assert all("notify_result" not in o for o in r["orders"]), "addresses and provider text stay off the list"
    assert r["count"] == 5 and r["counts"]["new"] == 5 and r["counts"]["delivered"] == 1, r
    with patched(shop, get_client=lambda: db):
        r = shop.list_orders(status="delivered")
    assert r["orders"][0]["notify_failed"] is True, "status is the UI's business: the badge shows it for 'new' only"


@test("api: the legacy and staff order routes skip the fan-out for an idempotent duplicate, like market_order")
def _():
    src = (ROOT / "app" / "shop_api.py").read_text(encoding="utf-8").splitlines()
    calls = [i for i, line in enumerate(src) if "add_task(shop_notify.notify_new_order" in line]
    assert len(calls) == 3, calls     # /public/shop/{token}/order, /public/market/order, /shop/order
    for i in calls:
        assert "if not o.get(\"duplicate\")" in src[i - 1], (i + 1, src[i - 1])


@test("cron: shop-cron.yml keeps workflow_dispatch but no live schedule; the Worker jsonc states the Render hours budget")
def _():
    wf = (ROOT / ".github" / "workflows" / "shop-cron.yml").read_text(encoding="utf-8")
    on_block = wf.split("\non:", 1)[1].split("\npermissions:", 1)[0]
    assert re.search(r"^\s+workflow_dispatch:", on_block, re.M), on_block
    assert not re.search(r"^\s+schedule:", on_block, re.M), "the schedule must stay commented out while the Worker runs"
    assert "re-enable only if the cloudflare keep-warm worker" in wf.lower(), "the note that says when to bring it back"
    jsonc = (ROOT / "web" / "wrangler.keepwarm.jsonc").read_text(encoding="utf-8")
    assert "750" in jsonc and "720-744" in jsonc, "the free-hours budget belongs next to the trigger that spends it"
    doc = (ROOT / "docs" / "SHOP.md").read_text(encoding="utf-8")
    assert "750 free instance-hours" in doc and "07:00–22:00 Bahrain" in doc and "shop_jobs_lock" in doc


@test("jobs: notify_retry re-runs the fan-out for young 'new' orders nobody heard about, stops at 3 attempts")
def _():
    import app.database as database
    import app.shop as shop
    from app import shop_jobs, shop_notify
    exhausted = dict(LEGACY_FAILED, attempts=["a", "b", "c"])
    delivered = dict(LEGACY_FAILED, email_owner={"sent": True})
    rows = [
        {"id": 1, "order_no": "A", "status": "new", "created_at": ago(hours=2), "notify_result": dict(LEGACY_FAILED)},   # retry
        {"id": 2, "order_no": "B", "status": "new", "created_at": ago(hours=2), "notify_result": exhausted},             # exhausted
        {"id": 3, "order_no": "C", "status": "new", "created_at": ago(hours=2), "notify_result": delivered},             # fine
        {"id": 4, "order_no": "D", "status": "new", "created_at": ago(minutes=5), "notify_result": None},               # task may still run
        {"id": 5, "order_no": "E", "status": "new", "created_at": ago(minutes=20), "notify_result": None},              # never notified → retry
        {"id": 6, "order_no": "F", "status": "confirmed", "created_at": ago(hours=2), "notify_result": dict(LEGACY_FAILED)},  # not new
        {"id": 7, "order_no": "G", "status": "new", "created_at": ago(hours=60), "notify_result": dict(LEGACY_FAILED)},  # too old
    ]
    db = FakeDB(shop_orders=rows)
    ch = Channels(email_ok=True)
    orders = {r["id"]: r for r in db.rows["shop_orders"]}     # the live rows: the persisted result lands here
    with _jobs_ctx(db, ch), patched(shop, get_order=lambda oid: _order(id=oid, notify_result=orders[oid]["notify_result"], customer_email=None)), \
            patched(database, get_client=lambda: db):
        out = shop_jobs.notify_retry()
    assert out["retried"] == ["A", "E"] and out["recovered"] == ["A", "E"] and out["exhausted"] == ["B"], out
    assert orders[1]["notify_result"]["attempt"] == 2 and orders[5]["notify_result"]["attempt"] == 1
    assert not shop_notify.all_channels_failed(orders[1]["notify_result"])
    # a third failed run leaves the row exhausted: the next run skips it
    ch = Channels(email_ok=False)
    orders[1]["notify_result"] = dict(LEGACY_FAILED, attempts=["a", "b"])
    with _jobs_ctx(db, ch), patched(shop, get_order=lambda oid: _order(id=oid, notify_result=orders[oid]["notify_result"], customer_email=None)), \
            patched(database, get_client=lambda: db):
        out = shop_jobs.notify_retry()
        assert out["retried"] == ["A"] and out["recovered"] == [] and orders[1]["notify_result"]["attempt"] == 3, out
        out = shop_jobs.notify_retry()
    assert "A" in out["exhausted"] and out["retried"] == [], out


@test("jobs: stale_data_alert marks 'alerted' only when Telegram or the owner email really delivered")
def _():
    import app.shop as shop
    from app import shop_jobs

    def run(ch, marker=None):
        db = FakeDB(app_settings=([{"key": "shop_stale_alerted_at", "value": marker}] if marker else []))
        with _jobs_ctx(db, ch), patched(shop, exec_sql=lambda q: [{"as_of": ago(days=5)}]):
            out = shop_jobs.stale_data_alert()
        return out, db
    out, db = run(Channels(telegram_ok=False, email_ok=False))
    assert out["age_days"] == 5 and out["alerted"] is False and out["telegram"] is False and out["email"] is False, out
    assert "telegram" in out["reason"] and not db.written("app_settings", "upsert"), out
    out, db = run(Channels(telegram_ok=True))
    assert out["alerted"] is True and "email" not in out and db.written("app_settings", "upsert")[0][2]["key"] == "shop_stale_alerted_at"
    out, db = run(Channels(telegram_ok=False, email_ok=True))
    assert out["alerted"] is True and out["email"] is True and db.written("app_settings", "upsert")
    out, db = run(Channels(telegram_ok=True), marker=ago(hours=1))
    assert out["alerted"] is False and out["skipped"] and not db.written("app_settings", "upsert")


@test("jobs: unassigned_reminder stamps only on delivery; run_shop_jobs reports ok:false when a job raises")
def _():
    from app import shop_jobs
    row = {"id": 9, "order_no": "U", "status": "new", "salesman_id": None, "created_at": ago(hours=1), "sla_notified_at": None,
           "customer_shop": "Shop", "customer_area": "Riffa", "total_bhd": 12.0}
    db = FakeDB(shop_orders=[row])
    with _jobs_ctx(db, Channels(email_ok=False)):
        out = shop_jobs.unassigned_reminder()
    assert out["notified"] == 0 and out["sent"] is False and not db.written("shop_orders", "update"), out
    with _jobs_ctx(db, Channels(email_ok=True)):
        out = shop_jobs.unassigned_reminder()
    assert out["notified"] == 1 and out["orders"] == ["U"] and db.written("shop_orders", "update")[0][3] == [9], out

    def boom():
        raise RuntimeError("table missing")
    lock_db = FakeDB(app_settings=[])      # run_shop_jobs takes its lease here — never on the real app_settings
    with _jobs_ctx(lock_db, Channels()), \
            patched(shop_jobs, JOBS=(("fine", lambda: {"checked": 0}), ("broken", boom), ("errored", lambda: {"error": "x"}))):
        out = shop_jobs.run_shop_jobs()
    assert out["ok"] is False and out["errors"] == ["broken", "errored"] and out["broken"]["error"].startswith("RuntimeError"), out
    with _jobs_ctx(lock_db, Channels()), patched(shop_jobs, JOBS=(("fine", lambda: {"checked": 0}),)):
        out = shop_jobs.run_shop_jobs()
    assert out["ok"] is True and "errors" not in out and "lease" not in out


# ── schedules ──────────────────────────────────────────────────────────────────

def _run_due_at(hour: int, minute: int, rows: list[dict], weekday_monday=False):
    """run_due with the Bahrain clock fixed and every collaborator faked. Returns (result, db, ran)."""
    from app import schedules
    base = datetime(2026, 9, 28 if weekday_monday else 24, hour - 3, minute, tzinfo=timezone.utc)   # Bahrain = UTC+3

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return base.astimezone(tz) if tz else base.replace(tzinfo=None)
    db = FakeDB(agent_schedules=rows)
    ran: list[str] = []
    fake_agents = SimpleNamespace(AGENTS={"collections": object(), "inventory": object()},
                                  run_agent=lambda name, triggered_by="": ran.append(name) or {"agent": name})
    saved = sys.modules.get("app.agents")
    sys.modules["app.agents"] = fake_agents
    try:
        import app.emailer as emailer
        with patched(schedules, datetime=FakeDT, get_client=lambda: db), patched(emailer, send_agent=lambda res: {"emailed": True}):
            out = schedules.run_due(send=True)
    finally:
        if saved is None:
            sys.modules.pop("app.agents", None)
        else:
            sys.modules["app.agents"] = saved
    return out, db, ran


@test("schedules: run_due acts at the first call from 08:00 Bahrain onwards, once per day")
def _():
    rows = [{"agent": "collections", "cadence": "daily", "last_ran": None},
            {"agent": "inventory", "cadence": "weekly", "last_ran": None},
            {"agent": "margin", "cadence": "daily", "last_ran": None}]          # not in AGENTS → ignored
    out, db, ran = _run_due_at(7, 55, rows)
    assert out["count"] == 0 and out["skipped"].startswith("before the run hour"), out
    out, db, ran = _run_due_at(11, 20, rows)                                     # the cron missed 08:00 — still runs today
    assert ran == ["collections"] and out["ran"] == [{"agent": "collections", "emailed": True}], (ran, out)
    assert db.written("agent_schedules", "update")[0][2] == {"last_ran": "2026-09-24"}
    out, db, ran = _run_due_at(11, 35, [dict(rows[0], last_ran="2026-09-24")])   # already ran today → nothing
    assert ran == [] and out["count"] == 0, out
    out, db, ran = _run_due_at(9, 0, rows, weekday_monday=True)                  # Monday: weekly joins in
    assert ran == ["collections", "inventory"], ran


# ── settings + Cloudflare Worker cron ─────────────────────────────────────────

@test("settings: the confirm-SLA keys exist with the agreed defaults")
def _():
    from app.shop import SETTING_DEFAULTS, _i
    assert SETTING_DEFAULTS["shop_confirm_sla_min"] == "120" and SETTING_DEFAULTS["shop_confirm_renotify_hours"] == "12"
    assert _i(SETTING_DEFAULTS["shop_confirm_sla_min"]) == 120 and _i(SETTING_DEFAULTS["shop_assign_sla_min"]) == 30


def _jsonc(path: Path) -> dict:
    text = re.sub(r"^\s*//.*$", "", path.read_text(encoding="utf-8"), flags=re.M)   # full-line comments only
    return json.loads(text)


@test("worker: keepwarm cron config + script match the plan (health 03-19 UTC every 10 min, scheduler every 15 min)")
def _():
    web = ROOT / "web"
    cfg = _jsonc(web / "wrangler.keepwarm.jsonc")
    assert cfg["name"] == "yq-keepwarm" and cfg["main"] == "workers/keepwarm.js" and cfg["workers_dev"] is False, cfg
    assert cfg["triggers"]["crons"] == ["*/10 3-19 * * *", "*/15 * * * *"], cfg["triggers"]
    assert cfg["vars"]["API_URL"] == "https://yq-ops-assistant.onrender.com"
    assert cfg["vars"]["SCHEDULER_PATHS"].split(",")[0] == "/scheduler/shop-jobs"
    assert "AGENT_API_KEY" not in json.dumps(cfg["vars"]), "the key is a Worker secret, never a plain var"
    js = (web / cfg["main"]).read_text(encoding="utf-8")
    for needle in ("'*/10 3-19 * * *'", "'*/15 * * * *'", "env.AGENT_API_KEY", "'X-Agent-Key'", "/health",
                   "async scheduled(event, env, ctx)", "AbortSignal.timeout"):
        assert needle in js, needle
    assert "console.log(JSON" not in js and "res.text()" not in js, "never log the response body (order numbers, shop names)"
    node = shutil.which("node")
    if not node:
        print("   SKIP node --check (node not on PATH)")
        return
    r = subprocess.run([node, "--check", str(web / cfg["main"])], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


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
