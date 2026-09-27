"""R7a reminder hygiene + notifications log (Sprint 1 "Make it true", 27-Sep-2026).

    python -m tests.test_r7a_reminders

Pure tests: no database, no network, no .env needed. Every channel is a recording fake and the
PostgREST client is an in-memory stand-in (real eq/in/is/lt/gte filters, newest-first order,
inserts that hand out ids, a table that can be "not migrated yet" and raise PGRST205 like
PostgREST, and a column list that can lack shop_orders.is_test and raise 42703). All data is
synthetic.

Covered:
  * business_minutes / is_weekend — Friday and Saturday left out on Bahrain dates, not UTC ones;
  * the weekend gate — no rep, owner, digest or stale-data nudge on Friday or Saturday, Sunday
    07:00 picks up, notify_retry (the first alert) is not gated;
  * the SLA clock — overdue and the owner escalation count business minutes only (unconfirmed and
    unassigned); the 7-day cap stays on calendar days;
  * staff-placed (source 'salesman') and test orders are never chased, a NULL source still is,
    and a database without shop_orders.is_test is read without it;
  * with shop_notifications: one row per channel per attempt (masked, scrubbed, attempt numbers),
    a 'reminded' event only when someone was reached or the chase moved up a level, the event
    detail keeps the shape the order drawer reads, events are only ever inserted;
  * the back-off reads the log AND the legacy events (union, de-duplicated by attempted_at):
    hourly retry, 12 h cadence, the 10-reminder cap, reassignment, unassigned rows ignored;
  * without the table (before the migration) every attempt is an event exactly as before, and a
    reverse under a cached probe falls back within the same run;
  * the new-order fan-out logs one row per notify_result channel (new_order / retry / error)
    without changing what is sent; notify_retry's re-send is kind 'retry';
  * the unassigned nudge, the daily digest and the stale-data alert are logged; stale data waits
    for the send window (it once emailed the owner at 03:01);
  * the migration / reverse files: additive, RLS on, closed to anon/authenticated, check lists
    cover every kind the code writes, no CASCADE, no shop_order_events statement.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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


# ── clock ──────────────────────────────────────────────────────────────────────

BH = timezone(timedelta(hours=3))
WED, THU, FRI, SAT, SUN = 23, 24, 25, 26, 27          # September 2026


def bh(day: int, hour: int, minute: int = 0) -> datetime:
    """A Bahrain wall-clock time in September 2026, as UTC (what _now() returns)."""
    return datetime(2026, 9, day, hour, minute, tzinfo=BH).astimezone(timezone.utc)


NOW = bh(THU, 13)          # Thursday 13:00 Bahrain: a working day, inside the send window


def iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat()


# ── an in-memory PostgREST stand-in ────────────────────────────────────────────

class ApiError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{{'code': '{code}', 'message': \"{message}\"}}")
        self.code = code


class _Query:
    def __init__(self, db: "FakeDB", table: str):
        self.db, self.table = db, table
        self.op, self.payload, self.cols, self.filters, self._neg = "select", None, "*", [], False
        self.desc, self.lim = False, None

    def select(self, cols="*", **_kw):
        self.op, self.cols = "select", cols
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def upsert(self, payload, **_kw):
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
    def in_(self, c, v): return self._add("in", c, list(v))   # noqa: E704
    def lt(self, c, v): return self._add("lt", c, v)          # noqa: E704
    def gte(self, c, v): return self._add("gte", c, v)        # noqa: E704

    def order(self, col, desc=False, **_kw):
        self.desc = bool(desc)
        return self

    def limit(self, n):
        self.lim = n
        return self

    def _match(self, r: dict) -> bool:
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
        db = self.db
        db.calls.append((self.table, self.op, self.cols if self.op == "select" else None, list(self.filters)))
        if (self.op, self.table) in db.fail:
            raise db.fail[(self.op, self.table)]
        if self.table in db.missing:
            raise ApiError("PGRST205", f"Could not find the table 'public.{self.table}' in the schema cache")
        known = db.columns.get(self.table)
        if self.op == "select" and known is not None and self.cols != "*":
            for c in (x.strip() for x in str(self.cols).split(",")):
                if c and c not in known:
                    raise ApiError("42703", f"column {self.table}.{c} does not exist")
        rows = db.rows.setdefault(self.table, [])
        if self.op == "select":
            out = [dict(r) for r in rows if self._match(r)]
            if self.desc:
                out.reverse()
            return SimpleNamespace(data=out[: self.lim] if self.lim else out)
        if self.op == "insert":
            items = self.payload if isinstance(self.payload, list) else [self.payload]
            for p in items:
                r = dict(p, id=len(rows) + 1)
                if self.table == "shop_order_events":
                    r.setdefault("ts", iso(db.now))           # the DB default now()
                rows.append(r)
            db.writes.append((self.table, "insert", [dict(p) for p in items]))
            return SimpleNamespace(data=items)
        if self.op == "update":
            hit = [r for r in rows if self._match(r)]
            for r in hit:
                r.update(self.payload)
            db.writes.append((self.table, "update", dict(self.payload)))
            return SimpleNamespace(data=[dict(r) for r in hit])
        if self.op == "upsert":
            row = dict(self.payload)
            cur = next((r for r in rows if "key" in row and r.get("key") == row["key"]), None)
            if cur:
                cur.update(row)
            else:
                rows.append(row)
            db.writes.append((self.table, "upsert", row))
            return SimpleNamespace(data=[row])
        db.writes.append((self.table, "delete", list(self.filters)))
        return SimpleNamespace(data=[])


class FakeDB:
    def __init__(self, missing=(), columns=None, **tables):
        self.rows: dict[str, list[dict]] = {k: [dict(r) for r in v] for k, v in tables.items()}
        self.missing: set[str] = set(missing)
        self.columns: dict[str, set] = {k: set(v) for k, v in (columns or {}).items()}
        self.fail: dict[tuple, Exception] = {}
        self.writes: list[tuple] = []
        self.calls: list[tuple] = []
        self.now = NOW

    def table(self, name):
        return _Query(self, name)

    def written(self, table: str, op: str = "insert") -> list:
        return [w[2] for w in self.writes if w[0] == table and w[1] == op]

    def events(self) -> list[dict]:
        return [r for r in self.rows.get("shop_order_events", []) if r.get("actor") == "shop_jobs"]

    def notes(self) -> list[dict]:
        return list(self.rows.get("shop_notifications", []))


@contextmanager
def patched(obj, **attrs):
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
        return {"sent": ok, "emailed": ok, "to": to, "via": "resend" if ok else None,
                "reason": "" if ok else "resend_error 403: You can only send testing emails to your own email address (owner@example.test)"}

    def telegram(self, text):
        self.telegrams.append(text)
        return {"sent": self.telegram_ok, "reason": "" if self.telegram_ok else "not configured"}

    def whatsapp(self, number, text):
        self.whatsapps.append((number, text))
        return {"sent": self.wa_ok, "reason": "" if self.wa_ok else "cloud_api_not_configured"}

    def patch(self, module):
        return patched(module, _email=self.email, _telegram=self.telegram, _whatsapp_cloud=self.whatsapp)


REPS = {4: {"id": 4, "name": "Rep One", "email": "rep1@example.test", "phone": "39000001"},
        5: {"id": 5, "name": "Rep Two", "email": "", "phone": "39000002"}}
OWNER = "owner@example.test"


def ctx(db: FakeDB, ch: Channels, now: datetime = NOW, settings: dict | None = None, fresh_probe: bool = True,
        exec_sql=None):
    """Patch everything the jobs and the fan-out reach for. fresh_probe=False keeps the cached
    shop_notifications probe (to act out a reverse under a cached hit)."""
    import app.database as database
    import app.shop as shop
    from app import shop_jobs, shop_notify
    vals = dict(shop.SETTING_DEFAULTS, **(settings or {}))
    db.now = now
    if fresh_probe:
        shop_notify.forget_notifications_probe()

    @contextmanager
    def _ctx():
        extra = {"exec_sql": exec_sql} if exec_sql else {}
        with patched(shop_jobs, get_client=lambda: db, _now=lambda: now), patched(database, get_client=lambda: db), \
                patched(shop, shop_settings=lambda force=False: vals, _salesman_by_id=lambda sid: REPS.get(sid), **extra), \
                ch.patch(shop_notify), env(ALERT_EMAIL_TO=OWNER, APP_BASE_URL="https://ops.example.test"):
            yield
    return _ctx()


def row(i: int, **over) -> dict:
    r = {"id": i, "order_no": f"T-{i:03d}", "status": "new", "customer_shop": "Test Shop", "customer_area": "Manama",
         "total_bhd": 10.0, "sla_notified_at": None, "source": "market", "salesman_id": 4, "salesman_name": "Rep One",
         "is_test": False, "created_at": iso(NOW - timedelta(hours=6)), "assigned_at": None}
    r.update(over)
    return r


def note(order_id: int | None, at: datetime, role: str = "rep", status: str = "failed", kind: str = "reminder",
         level: str | None = None, channel: str = "email") -> dict:
    return {"order_id": order_id, "kind": kind, "channel": channel, "recipient_role": role, "status": status,
            "level": level or role, "created_at": iso(at)}


def legacy(order_id: int, at: datetime, rep=True, owner=False, attempted=None, attempted_at=None) -> dict:
    d = {"rep": rep, "owner": owner}
    if attempted is not None:
        d["attempted"] = attempted
    if attempted_at is not None:
        d["attempted_at"] = iso(attempted_at)
    return {"order_id": order_id, "event": "reminded", "actor": "shop_jobs", "ts": iso(at), "detail": d}


# ── the clock ──────────────────────────────────────────────────────────────────

@test("clock: business_minutes leaves Friday and Saturday out, on Bahrain dates (not UTC)")
def _():
    from app.shop_jobs import business_minutes, is_weekend
    assert business_minutes(bh(WED, 10), bh(WED, 12)) == 120
    assert business_minutes(bh(THU, 20), bh(SUN, 0)) == 240, "Thursday 20:00 to Sunday 00:00 is 4 working hours"
    assert business_minutes(bh(THU, 23), bh(SUN, 1)) == 120
    assert business_minutes(bh(FRI, 10), bh(SAT, 22)) == 0
    assert business_minutes(bh(17, 12), bh(SUN, 12)) == 720 + 5 * 1440 + 720, "two weekends, both left out"
    # Friday begins at 00:00 Bahrain = 21:00 UTC Thursday: a UTC-date reading would count 3 more hours
    assert business_minutes(bh(THU, 22), bh(FRI, 3)) == 120
    assert business_minutes(bh(SUN, 5), bh(SUN, 5)) == 0 and business_minutes(bh(SUN, 6), bh(SUN, 5)) == 0
    assert business_minutes(None, bh(SUN, 5)) == 0 and business_minutes(bh(SUN, 5), None) == 0
    assert bh(FRI, 0, 30).weekday() == 3, "the UTC date is still Thursday"
    assert is_weekend(bh(FRI, 0, 30)) and is_weekend(bh(SAT, 23, 59))
    assert not is_weekend(bh(SUN, 0, 0)) and not is_weekend(bh(THU, 23, 59))


# ── the weekend gate ───────────────────────────────────────────────────────────

@test("weekend: no rep, owner, digest or stale-data nudge on Friday or Saturday; Sunday 07:00 picks up")
def _():
    from app import shop_jobs

    def world(now: datetime):
        long_ago = iso(bh(WED, 10) - timedelta(days=8))              # past the 7-day cap: the daily digest
        db = FakeDB(shop_orders=[row(1, assigned_at=iso(bh(WED, 10)), created_at=iso(bh(WED, 9))),
                                 row(2, salesman_id=None, salesman_name=None, created_at=iso(bh(WED, 9))),
                                 row(3, assigned_at=long_ago, created_at=long_ago)],
                    shop_order_events=[], app_settings=[])
        stock = lambda q: [{"as_of": (now - timedelta(days=5)).date().isoformat()}]     # noqa: E731
        return db, stock
    for day, hour, runs, why in ((FRI, 10, False, "Bahrain weekend (Friday)"), (SAT, 21, False, "Bahrain weekend (Saturday)"),
                                 (SAT, 23, False, "outside 07:00-22:00"), (SUN, 7, True, ""), (THU, 21, True, "")):
        now = bh(day, hour)
        db, stock = world(now)
        ch = Channels(email_ok=True)
        with ctx(db, ch, now, exec_sql=stock):
            a = shop_jobs.unconfirmed_reminder()
            b = shop_jobs.unassigned_reminder()
            c = shop_jobs.stale_data_alert()
        if runs:
            assert a["reminded"] == ["T-001"] and b["orders"] == ["T-002"] and c["alerted"] is True, (day, hour, a, b, c)
            assert a["digest"]["orders"] == ["T-003"] and a["digest"]["sent"] is True, a
        else:
            assert why in a["skipped"] and why in b["skipped"] and why in c["skipped"], (day, hour, a, b, c)
            assert c["alerted"] is False and c["age_days"] == 5, c
            assert ch.emails == [] and ch.telegrams == [] and ch.whatsapps == [] and db.writes == [], (day, hour, db.writes)


@test("weekend: notify_retry is the first alert of a new order and still re-sends on a Friday")
def _():
    import app.shop as shop
    from app import shop_jobs
    now = bh(FRI, 10)
    failed = {"at": iso(now - timedelta(hours=2)), "email_rep": {"sent": False, "reason": "resend_error 403"},
              "email_owner": {"sent": False, "reason": "resend_error 403"}, "telegram": {"sent": False}}
    db = FakeDB(shop_orders=[{"id": 91, "order_no": "T-091", "status": "new", "created_at": iso(now - timedelta(hours=2)),
                              "notify_result": failed}])
    ch = Channels(email_ok=True)
    with ctx(db, ch, now), patched(shop, get_order=lambda oid: _order(notify_result=failed, created_at=iso(now - timedelta(hours=2)))):
        out = shop_jobs.notify_retry()
    assert out["retried"] == ["T-091"] and out["recovered"] == ["T-091"], out
    kinds = {n["kind"] for n in db.notes()}
    assert kinds == {"retry"}, kinds


# ── the SLA clock ──────────────────────────────────────────────────────────────

@test("sla: overdue and the owner escalation count business minutes; the 7-day cap stays on calendar days")
def _():
    from app import shop_jobs
    now = bh(SUN, 7)
    rows = [row(1, order_no="A", assigned_at=iso(bh(THU, 20))),      # 240 + 420 = 660 working min (calendar 59 h)
            row(2, order_no="B", assigned_at=iso(bh(THU, 23))),      # 60 + 420 = 480: not overdue at a 600 SLA
            row(3, order_no="C", assigned_at=iso(bh(WED, 10)))]      # 840 + 1440 + 420 = 2700: past 2x the SLA
    db = FakeDB(shop_orders=rows, shop_order_events=[], app_settings=[])
    ch = Channels(email_ok=True)
    with ctx(db, ch, now, settings={"shop_confirm_sla_min": "600"}):
        out = shop_jobs.unconfirmed_reminder()
    assert out["checked"] == 2 and sorted(out["reminded"]) == ["A", "C"] and out["escalated"] == ["C"], out
    ev = {e["order_id"]: e["detail"] for e in db.events()}
    assert ev[1]["age_min"] == 660 and ev[3]["age_min"] == 2700 and 2 not in ev, ev
    # unassigned: the same clock
    rows = [row(11, order_no="U1", salesman_id=None, salesman_name=None, created_at=iso(bh(THU, 21))),   # 180 + 420 = 600
            row(12, order_no="U2", salesman_id=None, salesman_name=None, created_at=iso(bh(FRI, 9)))]    # 420 (calendar 46 h)
    db = FakeDB(shop_orders=rows, app_settings=[])
    with ctx(db, Channels(email_ok=True), now, settings={"shop_assign_sla_min": "600"}):
        out = shop_jobs.unassigned_reminder()
    assert out["orders"] == ["U1"] and out["checked"] == 1, out
    # the cap: assigned 8 calendar days ago = 6 working days, and still out of per-order chasing
    old = row(21, order_no="OLD", assigned_at=iso(bh(THU, 13) - timedelta(days=8)))
    assert shop_jobs.business_minutes(bh(THU, 13) - timedelta(days=8), bh(THU, 13)) == 6 * 1440
    db = FakeDB(shop_orders=[old], shop_order_events=[], app_settings=[])
    ch = Channels(email_ok=True)
    with ctx(db, ch, NOW):
        out = shop_jobs.unconfirmed_reminder()
    assert out["reminded"] == [] and out["digest"]["orders"] == ["OLD"] and out["digest"]["sent"] is True, out


# ── who is chased ──────────────────────────────────────────────────────────────

@test("scope: staff-placed and test orders are never chased (unconfirmed + unassigned); a NULL source still is")
def _():
    from app import shop_jobs
    assigned = iso(NOW - timedelta(hours=3))
    rows = [row(1, order_no="M", assigned_at=assigned), row(2, order_no="N", source=None, assigned_at=assigned),
            row(3, order_no="S", source="salesman", assigned_at=assigned),
            row(4, order_no="X", is_test=True, assigned_at=assigned)]
    db = FakeDB(shop_orders=rows, shop_order_events=[], app_settings=[])
    ch = Channels(email_ok=True)
    with ctx(db, ch):
        out = shop_jobs.unconfirmed_reminder()
    assert sorted(out["reminded"]) == ["M", "N"] and out["excluded"] == {"staff": 1, "test": 1}, out
    assert {e["order_id"] for e in db.events()} == {1, 2} and {n["order_id"] for n in db.notes()} == {1, 2}
    body = " ".join(t for _, t in ch.whatsapps)
    assert "• M ·" in body and "• N ·" in body and "• S ·" not in body and "• X ·" not in body, body
    q = next(c for c in db.calls if c[0] == "shop_orders" and c[1] == "select")
    assert "is_test" in q[2] and "source" in q[2], q
    # the unassigned nudge leaves them out too
    un = [dict(r, salesman_id=None, salesman_name=None) for r in rows]
    db = FakeDB(shop_orders=un, app_settings=[])
    with ctx(db, Channels(email_ok=True)):
        out = shop_jobs.unassigned_reminder()
    assert out["orders"] == ["M", "N"] and out["excluded"] == {"staff": 1, "test": 1}, out
    # a database without shop_orders.is_test (pre-R1): the read retries without it, staff still left out
    cols = {"id", "order_no", "status", "customer_shop", "customer_area", "total_bhd", "created_at", "assigned_at",
            "sla_notified_at", "salesman_id", "salesman_name", "source"}
    bare = [{k: v for k, v in r.items() if k != "is_test"} for r in rows[:3]]
    db = FakeDB(columns={"shop_orders": cols}, shop_orders=bare, shop_order_events=[], app_settings=[])
    with ctx(db, Channels(email_ok=True)):
        out = shop_jobs.unconfirmed_reminder()
    assert sorted(out["reminded"]) == ["M", "N"] and out["excluded"] == {"staff": 1}, out
    selects = [c[2] for c in db.calls if c[0] == "shop_orders" and c[1] == "select"]
    assert "is_test" in selects[0] and "is_test" not in selects[1], selects


# ── the notifications log ──────────────────────────────────────────────────────

EVENT_KEYS = {"rep", "owner", "attempted", "attempted_at", "level", "age_min", "sla_min"}


@test("log: one row per channel per attempt; an event only when someone was reached or the chase moved up a level")
def _():
    from app import shop_jobs
    rows = [row(1, order_no="A", assigned_at=iso(NOW - timedelta(hours=3))),     # rep only (180 min)
            row(2, order_no="B", assigned_at=iso(NOW - timedelta(hours=5)))]     # rep + owner (300 min)
    db = FakeDB(shop_orders=rows, shop_order_events=[], app_settings=[])
    # run 1 — nobody reachable: both are first tries → a timeline event each (level 'attempt')
    with ctx(db, Channels(email_ok=False), NOW):
        out = shop_jobs.unconfirmed_reminder()
    assert out["reminded"] == [] and out["reason"].startswith("no channel delivered"), out
    assert len(db.written("shop_notifications")) == 1, "one insert for the whole run, not one per order"
    first = db.notes()
    got = sorted((n["order_id"], n["kind"], n["channel"], n["recipient_role"], n["status"], n["attempt"]) for n in first)
    assert got == [(1, "reminder", "email", "rep", "failed", 1), (1, "reminder", "whatsapp", "rep", "skipped", 1),
                   (2, "escalation", "email", "owner", "failed", 1), (2, "escalation", "telegram", "owner", "skipped", 1),
                   (2, "reminder", "email", "rep", "failed", 1), (2, "reminder", "whatsapp", "rep", "skipped", 1)], got
    assert all(n["created_at"] == iso(NOW) and n["detail"] == {"age_min": {1: 180, 2: 300}[n["order_id"]], "sla_min": 120}
               for n in first), first
    by = {(n["order_id"], n["channel"], n["recipient_role"]): n for n in first}
    assert by[(1, "email", "rep")]["recipient_masked"] == "r***@example.test" and by[(1, "whatsapp", "rep")]["recipient_masked"] == "***0001"
    assert by[(2, "email", "owner")]["recipient_masked"] == "o***@example.test" and by[(2, "email", "owner")]["level"] == "owner"
    assert "o***@example.test" in by[(1, "email", "rep")]["error"] and by[(1, "whatsapp", "rep")]["error"] == "cloud_api_not_configured"
    blob = json.dumps(db.notes())
    assert "rep1@example.test" not in blob and "owner@example.test" not in blob and "39000001" not in blob, blob
    ev = db.events()
    assert [e["order_id"] for e in ev] == [1, 2] and all(e["detail"]["level"] == "attempt" for e in ev), ev
    assert all(set(e["detail"]) == EVENT_KEYS for e in ev), "the order drawer reads this shape"
    # run 2 (+61 min) — still nobody: B's rep and owner were tried before → no event; A reaches 2x the
    # SLA (241 min) and escalates to the owner for the first time → one event (moved up a level)
    with ctx(db, Channels(email_ok=False), NOW + timedelta(minutes=61)):
        shop_jobs.unconfirmed_reminder()
    ev = db.events()
    assert [e["order_id"] for e in ev] == [1, 2, 1] and ev[2]["detail"]["attempted"] == {"rep": True, "owner": True}, ev
    assert len(db.notes()) == 6 + 8
    # run 3 (+122 min) — still nobody, no level change → the log grows, the timeline does not
    with ctx(db, Channels(email_ok=False), NOW + timedelta(minutes=122)):
        shop_jobs.unconfirmed_reminder()
    assert len(db.events()) == 3 and len(db.notes()) == 6 + 8 + 8, (len(db.events()), len(db.notes()))
    # run 4 (+183 min) — the email works now: both reached → an event each
    with ctx(db, Channels(email_ok=True), NOW + timedelta(minutes=183)):
        out = shop_jobs.unconfirmed_reminder()
    assert sorted(out["reminded"]) == ["A", "B"] and sorted(out["escalated"]) == ["A", "B"], out
    ev = db.events()
    assert len(ev) == 5 and ev[3]["detail"]["level"] == "owner" and ev[4]["detail"]["rep"] is True, ev
    b_rep = sorted(n["attempt"] for n in db.notes() if n["order_id"] == 2 and n["channel"] == "email" and n["recipient_role"] == "rep")
    a_owner = sorted(n["attempt"] for n in db.notes() if n["order_id"] == 1 and n["channel"] == "email" and n["recipient_role"] == "owner")
    assert b_rep == [1, 2, 3, 4] and a_owner == [1, 2, 3], (b_rep, a_owner)
    # 4 attempts on 2 orders used to be 8 timeline rows; now 5 — and the old rows are never rewritten
    assert not [w for w in db.writes if w[0] == "shop_order_events" and w[1] != "insert"], db.writes


@test("log: the back-off reads the log and the legacy events together (hourly retry, 12 h cadence, reassignment)")
def _():
    from app import shop_jobs
    a3 = iso(NOW - timedelta(hours=3))
    # a failed rep try in the log only: 30 min ago holds (back-off), 61 min ago is retried
    db = FakeDB(shop_orders=[row(1, order_no="A", assigned_at=a3), row(2, order_no="B", assigned_at=a3)],
                shop_notifications=[note(1, NOW - timedelta(minutes=30)), note(2, NOW - timedelta(minutes=61))],
                shop_order_events=[], app_settings=[])
    with ctx(db, Channels(email_ok=True)):
        out = shop_jobs.unconfirmed_reminder()
    assert out["reminded"] == ["B"] and out["backoff"] == ["A"], out
    # the 12 h cadence from the log: delivered 11 h ago waits, 13 h ago is due again
    a26 = iso(NOW - timedelta(hours=26))
    db = FakeDB(shop_orders=[row(1, order_no="A", assigned_at=a26), row(2, order_no="B", assigned_at=a26)],
                shop_notifications=[note(1, NOW - timedelta(hours=11), status="sent"),
                                    note(1, NOW - timedelta(hours=11), channel="whatsapp", status="skipped"),
                                    note(2, NOW - timedelta(hours=13), status="sent"),
                                    note(1, NOW - timedelta(hours=11), role="owner", kind="escalation", status="sent"),
                                    note(2, NOW - timedelta(hours=13), role="owner", kind="escalation", status="sent")],
                shop_order_events=[], app_settings=[])
    with ctx(db, Channels(email_ok=True)):
        out = shop_jobs.unconfirmed_reminder()
    assert out["reminded"] == ["B"] and out["escalated"] == ["B"], out
    # a legacy failed event 30 min ago still holds after the switch (nothing in the log yet)
    db = FakeDB(shop_orders=[row(1, order_no="A", assigned_at=a3)], shop_notifications=[],
                shop_order_events=[legacy(1, NOW - timedelta(minutes=30), rep=False, attempted={"rep": True, "owner": False})],
                app_settings=[])
    ch = Channels(email_ok=True)
    with ctx(db, ch):
        out = shop_jobs.unconfirmed_reminder()
    assert out["backoff"] == ["A"] and ch.emails == [] and db.writes == [], out
    # reassignment: log rows from before assigned_at belong to the previous rep; the unassigned
    # nudge (level 'unassigned') is about the queue and never holds a rep
    db = FakeDB(shop_orders=[row(1, order_no="A", assigned_at=a3)],
                shop_notifications=[note(1, NOW - timedelta(hours=4), status="sent"),
                                    note(1, NOW - timedelta(minutes=20), role="owner", status="sent", level="unassigned",
                                         channel="telegram")],
                shop_order_events=[], app_settings=[])
    with ctx(db, Channels(email_ok=True)):
        out = shop_jobs.unconfirmed_reminder()
    assert out["reminded"] == ["A"] and "backoff" not in out, out
    new = [n for n in db.notes() if n["created_at"] == iso(NOW)]
    assert {n["attempt"] for n in new} == {1}, "attempt counts start over for the new rep"


@test("log: the 10-reminder cap counts the log and the legacy events once each (an echoed event is not counted twice)")
def _():
    from app import shop_jobs
    a3d = iso(NOW - timedelta(days=3))
    # M: 6 delivered nudges before the switch (events) + 4 after it (log) = 10 → the daily digest
    m_events = [legacy(1, NOW - timedelta(hours=h), attempted={"rep": True, "owner": False},
                       attempted_at=NOW - timedelta(hours=h)) for h in (70, 64, 58, 52, 46, 40)]
    m_notes = [note(1, NOW - timedelta(hours=h), status="sent") for h in (34, 28, 22, 16)]
    # D: 5 delivered nudges in the log, each echoed by its timeline event (same attempted_at) = 5, not 10
    hours = (70, 57, 44, 31, 18)
    d_notes = [note(2, NOW - timedelta(hours=h), status="sent") for h in hours]
    d_events = [legacy(2, NOW - timedelta(hours=h) + timedelta(seconds=1), attempted={"rep": True, "owner": False},
                       attempted_at=NOW - timedelta(hours=h)) for h in hours]
    db = FakeDB(shop_orders=[row(1, order_no="M", assigned_at=a3d), row(2, order_no="D", assigned_at=a3d)],
                shop_notifications=m_notes + d_notes, shop_order_events=m_events + d_events, app_settings=[])
    ch = Channels(email_ok=True)
    with ctx(db, ch):
        hist = shop_jobs._reminder_history(db, db.rows["shop_orders"])
        out = shop_jobs.unconfirmed_reminder()
    assert hist[1]["count"] == 10 and hist[2]["count"] == 5 and hist[2]["rep_tries"] == 5, hist
    assert out["digest"]["orders"] == ["M"] and out["reminded"] == ["D"], out
    # the digest is one owner_digest row per channel, not per order, naming the order ids in detail
    dig = [n for n in db.notes() if n["kind"] == "owner_digest"]
    assert {(n["channel"], n["status"], n["order_id"]) for n in dig} == {("telegram", "skipped", None), ("email", "sent", None)}, dig
    assert all(n["detail"] == {"order_ids": [1]} and n["level"] == "digest" for n in dig), dig


@test("fallback: before the migration every attempt is an event, exactly as before R7a")
def _():
    from app import shop_jobs
    rows = [row(1, order_no="A", assigned_at=iso(NOW - timedelta(hours=3))),
            row(2, order_no="B", assigned_at=iso(NOW - timedelta(hours=5)))]
    db = FakeDB(missing={"shop_notifications"}, shop_orders=rows, shop_order_events=[], app_settings=[])
    for minutes, expect in ((0, 2), (30, 2), (61, 4), (122, 6)):
        ch = Channels(email_ok=False)
        with ctx(db, ch, NOW + timedelta(minutes=minutes)):
            out = shop_jobs.unconfirmed_reminder()
        assert len(db.events()) == expect, (minutes, len(db.events()), out)
        if minutes == 30:
            assert sorted(out["backoff"]) == ["A", "B"] and ch.emails == [], out     # the events alone hold it
    assert not db.written("shop_notifications"), "nothing is written to a table that is not there"
    assert all(set(e["detail"]) == EVENT_KEYS and e["detail"]["level"] == "attempt" for e in db.events())
    # the new-order fan-out answers exactly as before too
    from app import shop_notify
    import app.shop as shop
    ch = Channels(email_ok=True)
    with ctx(db, ch), patched(shop, get_order=lambda oid: _order()):
        r = shop_notify.notify_new_order(91)
    assert "error" not in r and r["email_rep"]["sent"] and not db.written("shop_notifications"), r
    # the events read failing → no history at all: sla_notified_at decides and every attempt is an event
    db = FakeDB(shop_orders=[row(1, order_no="A", assigned_at=iso(NOW - timedelta(minutes=150)))],
                shop_order_events=[], app_settings=[])
    db.fail[("select", "shop_order_events")] = RuntimeError("timeout")
    with ctx(db, Channels(email_ok=False)):
        shop_jobs.unconfirmed_reminder()
    with ctx(db, Channels(email_ok=False), NOW + timedelta(minutes=61)):
        shop_jobs.unconfirmed_reminder()
    assert len(db.events()) == 2 and len(db.notes()) == 4, (db.events(), db.notes())


@test("fallback: a reverse under a cached probe falls back within the same run (events again, nothing raised)")
def _():
    from app import shop_jobs, shop_notify
    rows = [row(2, order_no="B", assigned_at=iso(NOW - timedelta(hours=5)))]
    db = FakeDB(shop_orders=rows, shop_order_events=[], app_settings=[])
    with ctx(db, Channels(email_ok=False), NOW):
        shop_jobs.unconfirmed_reminder()
    assert len(db.events()) == 1 and len(db.notes()) == 4
    assert shop_notify.notifications_ready(db) is True, "the probe is cached as a hit"
    db.missing.add("shop_notifications")        # r7_notifications_reverse.sql ran
    with ctx(db, Channels(email_ok=False), NOW + timedelta(minutes=61), fresh_probe=False):
        out = shop_jobs.unconfirmed_reminder()
    ev = db.events()
    assert len(ev) == 2 and ev[1]["detail"]["attempted"] == {"rep": True, "owner": True}, (ev, out)
    assert shop_notify._log_probe["ok"] is False, "the stale hit is forgotten"
    # log_notifications alone: a cached hit, then the insert meets the missing table → False, probe dropped
    db2 = FakeDB()
    shop_notify.forget_notifications_probe()
    assert shop_notify.notifications_ready(db2) is True
    db2.missing.add("shop_notifications")
    assert shop_notify.log_notifications([{"kind": "reminder"}], db2) is False
    assert shop_notify._log_probe["ok"] is False and shop_notify.log_notifications([], db2) is False


# ── masking and statuses ───────────────────────────────────────────────────────

@test("log rows: recipients masked, provider errors scrubbed, sent / failed / skipped mapped from the channel result")
def _():
    import app.notify as notify
    from app import shop_notify as sn
    assert sn.mask_recipient("rep1@example.test") == "r***@example.test"
    assert sn.mask_recipient("owner@example.test, boss@example.test") == "o***@example.test, b***@example.test"
    assert sn.mask_recipient("+973 3900 0001") == "***0001" and sn.mask_recipient(None) is None and sn.mask_recipient("") is None
    s = sn.scrub("resend_error 403: You can only send testing emails to your own email address (owner@example.test)")
    assert "o***@example.test" in s and "owner@" not in s and "403" in s, s
    assert sn.scrub("whatsapp 400 for 97339000001 on 2026-09") == "whatsapp 400 for ***0001 on 2026-09"
    assert sn.scrub(None) is None and len(sn.scrub("x" * 500)) == 240
    st = lambda ch, res: sn.notification_row("reminder", ch, "rep", res)["status"]     # noqa: E731
    assert st("email", {"sent": True}) == "sent" and st("email", {"sent": False, "reason": "resend_error 403"}) == "failed"
    assert st("whatsapp", {"sent": False, "reason": "cloud_api_not_configured"}) == "skipped"
    assert st("email", {"sent": False, "reason": "no_recipient"}) == "skipped"
    assert st("email", {"sent": False, "reason": "ALERT_EMAIL_TO unset"}) == "skipped"
    assert st("none", {"sent": False, "reason": "rep has no email or phone on file"}) == "skipped"
    assert st("email", {"sent": False, "reason": "a@x.test: no_email_provider (set RESEND_API_KEY ...)"}) == "skipped"
    assert st("email", None) == "failed"
    with patched(notify, telegram_enabled=lambda: False):
        assert st("telegram", {"sent": False}) == "skipped", "send_telegram answers a bare False when not set up"
    with patched(notify, telegram_enabled=lambda: True):
        assert st("telegram", {"sent": False}) == "failed"
    r = sn.notification_row("reminder", "email", "rep", {"sent": True, "via": "resend"}, order_id=7, to="rep1@example.test",
                            level="rep", attempt=2, detail={"age_min": 130}, at=iso(NOW))
    assert r == {"order_id": 7, "kind": "reminder", "channel": "email", "recipient_role": "rep",
                 "recipient_masked": "r***@example.test", "status": "sent", "provider": "resend", "error": None,
                 "level": "rep", "attempt": 2, "detail": {"age_min": 130}, "created_at": iso(NOW)}, r
    # every row carries created_at (a bulk insert fills a missing key with NULL, which NOT NULL refuses)
    keys = {tuple(sorted(sn.notification_row(k, "email", "rep", {"sent": True}, **kw))) for k, kw in
            (("reminder", {"at": iso(NOW)}), ("stale_data", {}), ("new_order", {"order_id": 1, "to": "a@b.test"}))}
    assert len(keys) == 1 and "created_at" in next(iter(keys)), keys
    assert sn.notification_row("stale_data", "telegram", "owner", {"sent": True})["created_at"] > iso(NOW)
    assert sn.notification_row("x", "email", "owner", {"sent": False, "results": [{"tried": ["resend", "brevo"]}]})["provider"] == "resend/brevo"
    assert sn.notification_row("x", "whatsapp", "rep", {"sent": True})["provider"] == "whatsapp_cloud"


# ── the new-order fan-out ──────────────────────────────────────────────────────

def _order(**over) -> dict:
    o = {"id": 91, "order_no": "T-091", "status": "new", "source": "market", "customer_name": "Test Buyer",
         "customer_phone": "33000000", "customer_shop": "Test Shop", "customer_area": "Manama",
         "customer_email": "buyer@example.test", "total_bhd": 24.5, "subtotal_bhd": 24.5, "units_count": 3,
         "token": "tok" * 8, "notify_result": None, "salesman_id": 4, "created_at": iso(NOW - timedelta(minutes=1)),
         "salesman": {"id": 4, "name": "Rep One", "email": "rep1@example.test", "phone": "39000001"},
         "lines": [{"item_code": "T01", "display_name": "Test cable", "qty": 3, "unit_price_bhd": 8.166, "line_total_bhd": 24.5}]}
    o.update(over)
    return o


@test("fan-out: one row per notify_result channel (kind new_order), and exactly the same sends as without the log")
def _():
    import app.shop as shop
    from app import shop_notify
    sent: dict[str, tuple] = {}
    results: dict[str, dict] = {}
    for label, missing in (("with", ()), ("without", ("shop_notifications",))):
        db = FakeDB(missing=missing, shop_orders=[{"id": 91}])
        ch = Channels(email_ok=True, email_fail_for={"rep1@example.test"})
        with ctx(db, ch), patched(shop, get_order=lambda oid: _order()):
            results[label] = shop_notify.notify_new_order(91)
        sent[label] = (ch.emails, ch.telegrams, ch.whatsapps)
        if label == "with":
            rows = db.notes()
    assert sent["with"] == sent["without"], "the log never changes what is sent"
    strip = lambda r: {k: v for k, v in r.items() if k not in ("at", "attempts")}      # noqa: E731
    assert strip(results["with"]) == strip(results["without"])
    got = sorted((n["channel"], n["recipient_role"], n["status"], n["recipient_masked"]) for n in rows)
    assert got == [("email", "merchant", "sent", "b***@example.test"), ("email", "owner", "sent", "o***@example.test"),
                   ("email", "rep", "failed", "r***@example.test"), ("telegram", "owner", "skipped", None),
                   ("whatsapp", "rep", "skipped", "***0001")], got
    r = results["with"]
    assert all(n["kind"] == "new_order" and n["order_id"] == 91 and n["attempt"] == 1 and n["created_at"] == r["at"]
               for n in rows), rows
    assert {n["detail"]["key"] for n in rows} == {"email_rep", "email_owner", "customer_email", "telegram", "whatsapp"}
    blob = json.dumps(rows)
    assert "rep1@example.test" not in blob and "buyer@example.test" not in blob and "39000001" not in blob, blob


@test("fan-out: a re-send is kind 'retry' and skips kept channels; an early failure is one 'none' row; staff orders log no rep row")
def _():
    import app.shop as shop
    from app import shop_notify
    prev = {"at": iso(NOW - timedelta(hours=2)), "email_rep": {"sent": False, "reason": "resend_error 403"},
            "email_owner": {"sent": False, "reason": "resend_error 403"}, "telegram": {"sent": False},
            "customer_email": {"sent": True, "emailed": True}}
    db = FakeDB(shop_orders=[{"id": 91}])
    ch = Channels(email_ok=True)
    with ctx(db, ch), patched(shop, get_order=lambda oid: _order(notify_result=prev)):
        r = shop_notify.notify_new_order(91, retry=True)
    assert r["customer_email"].get("kept") and r["attempt"] == 2
    rows = db.notes()
    assert {n["kind"] for n in rows} == {"retry"} and {n["attempt"] for n in rows} == {2}
    assert "merchant" not in {n["recipient_role"] for n in rows}, "a kept channel was not sent again, so it has no row"
    # get_order raising: the fan-out never started — one row says so, the error scrubbed, no recipient role

    def boom(oid):
        raise RuntimeError("db down for rep1@example.test")
    db = FakeDB(shop_orders=[{"id": 91}])
    with ctx(db, Channels()), patched(shop, get_order=boom):
        r = shop_notify.notify_new_order(91)
    (n,) = db.notes()
    assert (n["channel"], n["recipient_role"], n["status"], n["kind"]) == ("none", None, "failed", "new_order"), n
    assert n["error"] == "RuntimeError: db down for r***@example.test" and n["detail"] == {"key": "error"}, n
    # a staff-placed order: the rep gets no copy, so there is no rep row
    db = FakeDB(shop_orders=[{"id": 91}])
    with ctx(db, Channels(email_ok=True)), patched(shop, get_order=lambda oid: _order(source="salesman", customer_email=None)):
        shop_notify.notify_new_order(91)
    assert {(n["channel"], n["recipient_role"]) for n in db.notes()} == {("email", "owner"), ("telegram", "owner")}
    # the log itself failing never costs the alert
    db = FakeDB(shop_orders=[{"id": 91}])
    db.fail[("insert", "shop_notifications")] = RuntimeError("insert refused")
    with ctx(db, Channels(email_ok=True)), patched(shop, get_order=lambda oid: _order()):
        r = shop_notify.notify_new_order(91)
    assert "error" not in r and r["email_rep"]["sent"] and db.written("shop_orders", "update"), r


# ── the other nudges ───────────────────────────────────────────────────────────

@test("other nudges: the unassigned nudge and the stale-data alert are logged; stale data waits for the send window")
def _():
    from app import shop_jobs
    db = FakeDB(shop_orders=[row(1, order_no="U1", salesman_id=None, salesman_name=None, created_at=iso(NOW - timedelta(hours=1))),
                             row(2, order_no="U2", salesman_id=None, salesman_name=None, created_at=iso(NOW - timedelta(hours=2)))])
    with ctx(db, Channels(email_ok=True)):
        out = shop_jobs.unassigned_reminder()
    assert out["notified"] == 2, out
    got = sorted((n["order_id"], n["kind"], n["level"], n["channel"], n["status"]) for n in db.notes())
    assert got == [(1, "reminder", "unassigned", "email", "sent"), (1, "reminder", "unassigned", "telegram", "skipped"),
                   (2, "reminder", "unassigned", "email", "sent"), (2, "reminder", "unassigned", "telegram", "skipped")], got
    assert not db.events(), "the unassigned nudge never wrote timeline rows and still does not"

    def stock(now):
        return lambda q: [{"as_of": (now - timedelta(days=5)).date().isoformat()}]
    # 03:01 Bahrain on a Thursday: the hour it once emailed the owner — now it waits
    night = bh(THU, 3, 1)
    db = FakeDB(app_settings=[])
    ch = Channels(email_ok=True)
    with ctx(db, ch, night, exec_sql=stock(night)):
        out = shop_jobs.stale_data_alert()
    assert out["skipped"].startswith("outside 07:00-22:00") and out["alerted"] is False, out
    assert ch.emails == [] and ch.telegrams == [] and db.writes == [], db.writes
    # inside the window: Telegram (not set up) then the owner email; one stale_data row each, no order
    db = FakeDB(app_settings=[])
    with ctx(db, Channels(email_ok=True), NOW, exec_sql=stock(NOW)):
        out = shop_jobs.stale_data_alert()
    assert out["alerted"] is True and out["email"] is True, out
    got = sorted((n["kind"], n["channel"], n["status"], n["order_id"], n["recipient_role"]) for n in db.notes())
    assert got == [("stale_data", "email", "sent", None, "owner"), ("stale_data", "telegram", "skipped", None, "owner")], got
    assert all(n["detail"] == {"age_days": 5} for n in db.notes())


# ── the migration files ────────────────────────────────────────────────────────

def _code(sql: str) -> str:
    """The SQL without -- comments (so a word in a comment never passes or fails a check)."""
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines()).lower()


def _statements(sql: str) -> str:
    """_code without string literals either (COMMENT ON texts, RAISE messages, check values)."""
    return re.sub(r"'[^']*'", "''", _code(sql))


@test("migration: additive shop_notifications, RLS on, closed to anon/authenticated; the checks cover what the code writes")
def _():
    mig = (ROOT / "scripts" / "r7_notifications_migration.sql").read_text(encoding="utf-8")
    rev = (ROOT / "scripts" / "r7_notifications_reverse.sql").read_text(encoding="utf-8")
    m = _code(mig)
    for needle in ("create table if not exists shop_notifications",
                   "order_id         bigint references shop_orders(id) on delete cascade",
                   "alter table shop_notifications enable row level security",
                   "revoke all on table shop_notifications from anon, authenticated",
                   "revoke all on sequence shop_notifications_id_seq from anon, authenticated",
                   "create index if not exists shop_notifications_order_idx on shop_notifications (order_id, created_at desc)",
                   "create index if not exists shop_notifications_kind_idx  on shop_notifications (kind, created_at desc)"):
        assert needle in m, needle
    ms, rs = _statements(mig), _statements(rev)
    assert "security_invoker" not in ms and not re.search(r"^\s*grant\s", ms, re.M), "nothing is granted"
    assert "shop_order_events" not in ms and "shop_order_events" not in rs, "the legacy events are never touched"
    assert not re.search(r"\b(delete|update|truncate|insert)\b\s", ms.replace("on delete cascade", "")), "additive only"
    assert "drop table if exists shop_notifications;" in rs and "cascade" not in rs, rs
    assert len(re.findall(r"\bdrop\b", rs)) == 1, "the reverse drops the log table and nothing else"

    def allowed(col: str) -> set[str]:
        body = re.search(col + r"\s+in\s*\(([^)]*)\)", m).group(1)
        return set(re.findall(r"'(\w+)'", body))
    kinds, roles, statuses = allowed("kind"), allowed("recipient_role"), allowed("status")
    assert kinds == {"new_order", "reminder", "escalation", "owner_digest", "status_update", "stale_data", "retry"}, kinds
    assert roles == {"rep", "owner", "management", "merchant"} and statuses == {"sent", "failed", "skipped"}
    src = (ROOT / "app" / "shop_jobs.py").read_text(encoding="utf-8") + (ROOT / "app" / "shop_notify.py").read_text(encoding="utf-8")
    used = set(re.findall(r"(?:notification_row|_owner_rows)\(\s*\"(\w+)\"", src)) | {"new_order", "retry"}
    assert used <= kinds and {"reminder", "escalation", "owner_digest", "stale_data"} <= used, (used, kinds)
    assert '"retry" if retry else "new_order"' in src


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
