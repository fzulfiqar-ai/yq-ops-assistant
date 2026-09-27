"""R7b — the Weekly AI Head (Sprint 6, plan §19 / §20 / §22).

    python -m tests.test_r7b_ai_head

Same lightweight runner as tests/test_r7a_focus.py. The pure tests need no database, no network and no
.env; every name, phone, amount and code in them is synthetic. Covered:

  * the migration and its reverse, read as text: additive, idempotent guards, every new object revoked
    from anon / authenticated, no GRANT <role> TO <role> (ai_head_ro is a member of no role), no base
    table for ai_head_ro, v_agent_customer_regulars never yq_readonly, no security_invoker / CASCADE,
    no password; the fourteen views are the same list everywhere (created, granted, self-checked,
    reversed, read by the pack); the reverse refuses while ai_insights holds the owner's decisions;
  * the mask: phones (8+ digits, spaces allowed) and emails go, order numbers and invoice keys stay;
    the SQL views use the Python pattern verbatim;
  * the pack: AI_HEAD_DATABASE_URL first, DATABASE_URL with a warning, nothing = stop; a statement that
    is not a read is refused before it reaches the database; the weekly loader feeds
    scripts/weekly_report.compute() unchanged; the trust gate (fresh / stale / failing totals / the
    Focus match rate / verify_numbers), month-close readiness, XmR, modified z, Focus context,
    concentration, merchant movement, item signals, the holdout readout; a whole build on a fake
    session writes every file, masks every phone and email, and issues reads only;
  * the loader: validation refusals, the owner's decisions (and --by), the Top-5 gate, the commit on
    a fake connection (insert, status moves, forbidden moves, the missing table), a dry run that
    never connects;
  * GET /management/insights: admin (every status, decided_by), management (approved / done only,
    masked text, no decided_by), everyone else 403, before the migration, bad parameters;
  * the command files, the README's Task Scheduler line, .env.example and .gitignore;
  * a local Postgres replay (SKIPs without the cluster): the migration applied twice on a synthetic
    schema, the ai_head_ro login read-only and blind to base tables, the views masking, the pack built
    through that login, weekly_report.compute() identical through the views and the base tables, the
    ai_insights trigger, and the reverse (refused with rows, then clean, twice).
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
import traceback
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
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


MIGRATION = ROOT / "scripts" / "r7b_ai_head_migration.sql"
REVERSE = ROOT / "scripts" / "r7b_ai_head_reverse.sql"
R7C_MIGRATION = ROOT / "scripts" / "r7c_order_lines_qty_migration.sql"
VIEWS = ["v_agent_shop_orders", "v_agent_shop_lines", "v_agent_shop_events", "v_agent_funnel_daily",
         "v_agent_search_demand", "v_agent_rep_governance", "v_agent_statements", "v_agent_focus_links",
         "v_agent_customer_regulars", "v_agent_focus_sales", "v_agent_items", "v_agent_market_signals",
         "v_agent_data_trust", "v_agent_insights"]
BH = timezone(timedelta(hours=3))


def _sql_code(path: Path) -> str:
    """The file without -- comments (so the prose can mention what the code must never do)."""
    return "\n".join(ln.split("--", 1)[0] for ln in path.read_text(encoding="utf-8").splitlines())


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


# ═══════════════════════════════════════════════════════════════════════════════
# 1. the migration and its reverse, as text
# ═══════════════════════════════════════════════════════════════════════════════

@test("migration: 14 views, the same list created, granted, self-checked, reversed and read by the pack")
def _():
    from scripts.ai_head import pack
    code = _sql_code(MIGRATION)
    created = re.findall(r"create or replace view (v_agent_\w+) as", code)
    assert created == VIEWS, created
    assert list(pack.AGENT_VIEWS) == VIEWS
    grant_ro = re.search(r"grant select on ([^;]*?)\s+to ai_head_ro;", code).group(1)
    assert sorted(re.findall(r"v_agent_\w+", grant_ro)) == sorted(VIEWS), "every view to ai_head_ro"
    grant_yq = re.search(r"grant select on ([^;]*?)\s+to yq_readonly;", code).group(1)
    assert sorted(re.findall(r"v_agent_\w+", grant_yq)) == sorted(set(VIEWS) - {"v_agent_customer_regulars"})
    revoke = re.search(r"revoke all on table (v_agent[^;]*?)\s+from anon, authenticated;", code).group(1)
    assert sorted(re.findall(r"v_agent_\w+", revoke)) == sorted(VIEWS)
    check = re.search(r"views text\[\] := array\[(.*?)\];", code, re.S).group(1)
    assert re.findall(r"'(v_agent_\w+)'", check) == VIEWS
    rev = _sql_code(REVERSE)
    assert sorted(re.findall(r"drop view if exists (v_agent_\w+);", rev)) == sorted(VIEWS)
    assert re.search(r"revoke all on table v_agent_customer_regulars from yq_readonly;", code)


@test("migration: additive, no role-in-role grant, no password, no base table for ai_head_ro, nothing for anon")
def _():
    code = _sql_code(MIGRATION).lower()
    for bad in ("cascade", "drop table", "drop view", "delete from", "truncate",
                "update shop_", "insert into shop_", " password"):
        assert bad not in code, bad
    assert not re.search(r"(with|set)\s*\(\s*security_invoker", code), "views run as their owner"
    for stmt in re.findall(r"(?:create|alter) role [^;]*;", code):
        for power in ("superuser", "createrole", "createdb", "replication", "bypassrls", "password"):
            assert power not in stmt.replace("no" + power, ""), (stmt, power)
    # GRANT <role> TO <role> is never used (supautils crashes on one inside DO; none is needed)
    assert not re.search(r"grant\s+(yq_readonly|ai_head_ro|postgres|service_role)\s+to\b", code)
    # ai_head_ro's only grants: schema usage and SELECT on the views
    grants = [" ".join(g.split()) for g in re.findall(r"grant [^;]*;", code) if "ai_head_ro" in g]
    for g in grants:
        assert (g == "grant usage on schema public to ai_head_ro;"
                or g == "grant execute on function ai_agent_mask(text) to ai_head_ro, yq_readonly, service_role;"
                or (g.startswith("grant select on v_agent_") and g.endswith(" to ai_head_ro;")
                    and set(re.findall(r"v_agent_\w+", g)) <= set(VIEWS))), g
    assert len(grants) == 3, grants
    assert "create role ai_head_ro login inherit connection limit 3" in code
    assert "if not exists (select 1 from pg_roles where rolname = 'ai_head_ro')" in code
    for setting in ("statement_timeout = '15s'", "default_transaction_read_only = on",
                    "idle_in_transaction_session_timeout = '60s'"):
        assert f"alter role ai_head_ro set {setting};" in code, setting
    assert "create table if not exists ai_insights" in code
    assert "alter table ai_insights enable row level security;" in code
    assert "revoke all on table ai_insights from anon, authenticated;" in code
    assert "revoke all on sequence ai_insights_id_seq from anon, authenticated;" in code
    assert "revoke all on function ai_insights_guard() from public, anon, authenticated;" in code
    # every CHECK is dropped-if-exists before it is added (re-runnable)
    for c in re.findall(r"add constraint (ai_insights_\w+)", code):
        assert f"drop constraint if exists {c};" in code, c
    # the self-check proves the dangerous parts
    for needle in ("must be owned by postgres", "must not be security_invoker", "member of no role",
                   "must not read the base table", "carry a contact detail", "read rpcs"):
        assert needle in code, needle


@test("migration: no view column carries a contact detail or raw identifier; free text is masked")
def _():
    code = _sql_code(MIGRATION)
    bodies = dict(re.findall(r"create or replace view (v_agent_\w+) as\n(.*?);\n", code, re.S))
    assert set(bodies) == set(VIEWS)
    outer = {}
    for name, body in bodies.items():
        # the outer select list: after the last top-level 'select' that is not inside a CTE
        tail = body.split("\nselect ", 1)[-1] if name in ("v_agent_search_demand", "v_agent_rep_governance",
                                                           "v_agent_customer_regulars") else body
        outer[name] = tail.split("\nfrom ", 1)[0]
    for name, sel in outer.items():
        for bad in ("customer_phone", "customer_email", ".phone", ".email", "user_email as", ".token", "ip_hash",
                    " ua,", "e.device_id,", "e.session_id,", "created_by", "decided_by", "approved_by", "posted_by",
                    "target_snapshot,", ".note,", "customer_name,"):
            if bad == "customer_name," and name in ("v_agent_focus_sales", "v_agent_customer_regulars"):
                continue          # masked Focus account names, by design
            assert bad not in sel, (name, bad)
    # masked free text: shop names and areas, Focus names and narration, search terms, finds, notes
    for name, n in (("v_agent_shop_orders", 2), ("v_agent_shop_events", 1), ("v_agent_search_demand", 1),
                    ("v_agent_customer_regulars", 1), ("v_agent_focus_sales", 2), ("v_agent_market_signals", 2)):
        assert bodies[name].count("ai_agent_mask(") == n, (name, bodies[name].count("ai_agent_mask("))
    assert "revoke all on function ai_agent_mask(text) from public, anon, authenticated;" in code
    assert "grant execute on function ai_agent_mask(text) to ai_head_ro, yq_readonly, service_role;" in code
    # keys are spelled in letters, so no key can ever look like a phone number
    assert bodies["v_agent_shop_events"].count("'0123456789', 'ghijklmnop'") == 2
    assert "'0123456789', 'ghijklmnop'" in bodies["v_agent_customer_regulars"]


@test("migration: the SQL mask is app.ai_insights.PHONE_PATTERN verbatim, emails first, and self-checked")
def _():
    from app.ai_insights import PHONE_PATTERN
    code = _sql_code(MIGRATION)
    fn = re.search(r"create or replace function ai_agent_mask\(t text\).*?\$\$;", code, re.S).group(0)
    assert fn.count(f"'{PHONE_PATTERN}', '[number]', 'g'") == 1 and "'[email]'" in fn
    assert "regexp_replace(t, '[A-Za-z0-9._%+-]+@" in fn, "the email mask runs first, on the raw text"
    assert "immutable" in fn and "security definer" not in fn
    assert "ai_agent_mask does not mask as documented" in code


@test("review: lines expose added_at_stage / qty_delivered through to_jsonb (appended); a rep's own order never enters a confirm time")
def _():
    code = _sql_code(MIGRATION)
    bodies = dict(re.findall(r"create or replace view (v_agent_\w+) as\n(.*?);\n", code, re.S))
    lines = bodies["v_agent_shop_lines"]
    sel = lines.split("\nfrom shop_order_lines l", 1)[0]
    # appended after rule_ids (CREATE OR REPLACE VIEW only appends), read so the view works before and after r7c
    assert re.search(r"l\.rule_ids,\s*to_jsonb\(l\) ->> 'added_at_stage'\s+as added_at_stage,\s*"
                     r"\(to_jsonb\(l\) ->> 'qty_delivered'\)::integer\s+as qty_delivered\s*$", sel), sel[-300:]
    assert not re.search(r"\bl\.(added_at_stage|qty_delivered)\b", lines), "never a direct column reference"
    gov = bodies["v_agent_rep_governance"]
    for agg in ("as median_confirm_hours_30d", "as max_confirm_hours_30d"):
        part = gov.split(agg, 1)[0].rsplit("filter (", 1)[1]
        assert "o.source is distinct from 'salesman'" in part, (agg, part)
    orders = bodies["v_agent_shop_orders"]
    assert re.search(r"case when o\.source is distinct from 'salesman'\s+then round\(\(extract\(epoch from "
                     r"\(o\.confirmed_at - o\.created_at\)\) / 3600\)::numeric, 2\) end as confirm_hours", orders)
    assert "added_at_stage" in MIGRATION.read_text(encoding="utf-8").split("comment on view v_agent_shop_lines", 1)[1][:600]


@test("review: weekly_report's median time to confirm leaves out the orders reps placed themselves")
def _():
    from scripts import weekly_report as wr
    ws, we = date(2026, 9, 20), date(2026, 9, 26)
    v = _synthetic_views()
    base = {"prev": {"n": 0, "v": 0}, "open_now": [], "events": [], "reps": {}, "focus_max": date(2026, 9, 24),
            "focus": [], "lines": [], "t0": datetime(2026, 9, 20, tzinfo=BH), "t1": datetime(2026, 9, 27, tzinfo=BH)}
    shop_orders = [{"id": o["order_id"], "order_no": o["order_no"], "status": o["status"],
                    "total_bhd": o["total_requested_bhd"], "total_confirmed_bhd": o["total_confirmed_bhd"],
                    "order_kind": o["order_kind"], "customer_id": o["customer_id"], "customer_shop": o["customer_shop"],
                    "customer_name": None, "customer_area": o["customer_area"], "created_at": o["created_at"],
                    "confirmed_at": o["confirmed_at"], "delivered_at": o["delivered_at"],
                    "cancelled_at": o["cancelled_at"], "source": o["source"], "salesman_id": o["salesman_id"],
                    "rep": o["rep"], "focus_name": o["rep_focus_name"]} for o in v["v_agent_shop_orders"]]
    now = datetime(2026, 9, 27, 6, 0, tzinfo=timezone.utc)
    before = wr.compute({**base, "orders": shop_orders}, ws, we, now)
    staff = dict(shop_orders[0], id=77, order_no="YQ-2609-9077", source="salesman", status="confirmed",
                 created_at=_ts("2026-09-25"), confirmed_at=_ts("2026-09-25"), delivered_at=None)
    after = wr.compute({**base, "orders": shop_orders + [staff, dict(staff, id=78, order_no="YQ-2609-9078")]}, ws, we, now)
    assert before["confirm_median_h"] is not None and after["confirm_median_h"] == before["confirm_median_h"], \
        (before["confirm_median_h"], after["confirm_median_h"])
    assert after["confirmed_n"] == before["confirmed_n"], "a born-Confirmed order is not a confirmation"


@test("review: the trust gate prices the requested subtotal as list price x qty of the shop's lines (added lines out)")
def _():
    from scripts.ai_head import pack
    src = _synthetic_views()["v_agent_data_trust"]

    def order(oid, sub, sub_c=None):
        return {"order_id": oid, "order_no": f"YQ-2609-{oid}", "status": "confirmed", "is_test": False,
                "subtotal_bhd": Decimal(sub), "discount_bhd": Decimal("0.500"), "delivery_bhd": Decimal(0),
                "small_order_fee_bhd": None, "total_requested_bhd": Decimal(sub) - Decimal("0.500"),
                "subtotal_confirmed_bhd": None if sub_c is None else Decimal(sub_c), "delivered_at": None,
                "focus_link_state": "none"}

    def line(oid, qty, lp, unit, qc=None, added=None):
        return {"order_id": oid, "qty": qty, "qty_confirmed": qc, "list_price_bhd": Decimal(lp),
                "unit_price_bhd": Decimal(unit), "unit_price_confirmed": Decimal(unit) if qc is not None else None,
                "line_total_bhd": (Decimal(unit) * qty).quantize(Decimal("0.001")),
                "line_total_confirmed": (Decimal(unit) * qc).quantize(Decimal("0.001")) if qc is not None else None,
                "added_at_stage": added}
    # order 1: a line discounted 0.25 a unit (the subtotal is before discounts: 3 x 2.000 = 6.000, not 5.250),
    # confirmed 2 of 3, then the rep ADDED 4 x 1.500 at confirm: the requested subtotal stays 6.000; the
    # confirmed subtotal (compute_totals) = 2 x 2.000 + 4 x 1.500 = 10.000
    orders = [order(1, "6.000", "10.000")]
    lines = [line(1, 3, "2.000", "1.750", qc=2), line(1, 4, "1.500", "1.500", qc=4, added="confirm")]
    by = {c["check"]: c for c in pack.trust_gate(src, date(2026, 9, 27), orders=orders, lines=lines)["checks"]}
    assert by["order_lines_sum"]["status"] == "ok", by["order_lines_sum"]
    assert by["confirmed_lines_sum"]["status"] == "ok", by["confirmed_lines_sum"]
    # the old rule (sum of every line's discounted total) would have failed a correct order twice over
    assert sum(ln["line_total_bhd"] for ln in lines) != orders[0]["subtotal_bhd"]
    # a real mismatch still fails
    bad = pack.trust_gate(src, date(2026, 9, 27), orders=[order(1, "6.500", "10.000")], lines=lines)
    assert {c["check"]: c["status"] for c in bad["checks"]}["order_lines_sum"] == "fail"
    # item demand: what the shops asked for, not what the rep added
    items = [{"item_code": "SKU-Z", "display_name": "Z", "is_active": True, "hidden": False, "stock_qty": Decimal(0),
              "sold_90d": Decimal(0), "restock_requests_30d": 0, "trade_price_bhd": None, "landed_cost_bhd": None}]
    added_only = [{"item_code": "SKU-Z", "qty": 9, "is_test": False, "order_status": "confirmed", "added_at_stage": "confirm"}]
    assert pack.item_signals(items, added_only, [])["sold_out_with_demand"] == []
    asked = [dict(added_only[0], added_at_stage=None)]
    assert pack.item_signals(items, asked, [])["sold_out_with_demand"][0]["marketplace_units_asked_in_pack"] == 9


@test("reverse: drops only what the migration made, refuses while ai_insights holds decisions, no CASCADE")
def _():
    rev = _sql_code(REVERSE).lower()
    assert "cascade" not in rev
    assert "current_setting('yq.ai_insights_drop', true)" in rev and "select count(*) from public.ai_insights" in rev
    assert "execute 'select count(*)" in rev, "dynamic, so a second run (table gone) does not fail to plan"
    assert rev.index("raise exception") < rev.index("drop view if exists"), "the refusal comes before any drop"
    assert "drop table if exists ai_insights;" in rev and "drop function if exists ai_insights_guard();" in rev
    assert "drop role if exists ai_head_ro;" in rev and "drop function if exists ai_agent_mask(text);" in rev
    assert "revoke usage on schema public from ai_head_ro" in rev
    for bad in ("drop table if exists shop", "drop view if exists v_sales", "delete from", "truncate"):
        assert bad not in rev, bad


# ═══════════════════════════════════════════════════════════════════════════════
# 2. the mask
# ═══════════════════════════════════════════════════════════════════════════════

@test("mask: phones and emails go; order numbers, invoice keys, prices and model numbers stay")
def _():
    from app.ai_insights import has_contact, mask_text
    for raw in ("+973 3312 3456", "97333123456", "33123456", "call 3312 3456 now", "3 3 1 2 3 4 5 6"):
        out = mask_text(raw)
        assert "[number]" in out and not re.search(r"\d{8}", out.replace(" ", "")), (raw, out)
        assert has_contact(raw) and not has_contact(out)
    assert mask_text("orders@example.com") == "[email]"
    assert mask_text("mail a.b+c@shop.example.bh today") == "mail [email] today"
    assert mask_text("a12345678@example.com") == "[email]", "the email goes first, whole"
    for keep in ("YQ-2609-0019", "SI-YQ-26-09-110", "SI : SI-YQ-26-09-119", "BHD 1,234.500", "20000mAh",
                 "iPhone 15 Pro 256GB", "1234567", "Type-C 65W"):
        assert mask_text(keep) == keep, keep
        assert not has_contact(keep), keep
    assert mask_text(None) is None and mask_text(12345678) == 12345678 and mask_text("") == ""


# ═══════════════════════════════════════════════════════════════════════════════
# 3. the pack
# ═══════════════════════════════════════════════════════════════════════════════

@test("pack: the ai_head_ro login first, DATABASE_URL with a warning, nothing = stop")
def _():
    from scripts.ai_head import pack
    dsn, mode, warn = pack.choose_dsn({"AI_HEAD_DATABASE_URL": "postgresql://ro", "DATABASE_URL": "postgresql://owner"})
    assert (dsn, mode, warn) == ("postgresql://ro", "ai_head_ro", [])
    dsn, mode, warn = pack.choose_dsn({"DATABASE_URL": "postgresql://owner"})
    assert dsn == "postgresql://owner" and mode == "owner_fallback" and "READ-ONLY" in warn[0]
    dsn, mode, warn = pack.choose_dsn({})
    assert dsn is None and mode == "none"
    try:
        pack.connect({})
        raise AssertionError("connect without a DSN must stop")
    except SystemExit as e:
        assert "AI_HEAD_DATABASE_URL" in str(e)


class _Cur:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    """A psycopg connection stand-in answering the pack's SELECTs from canned rows, keyed on the first
    view (or table) the statement reads."""

    def __init__(self, data: dict[str, list[dict]]):
        self.data, self.sql = data, []

    def execute(self, sql, params=None):
        self.sql.append(sql)
        low = " ".join(sql.split()).lower()
        if "information_schema.tables" in low:
            return _Cur([{"table_name": v} for v in self.data.get("__views__", VIEWS)])
        m = re.search(r"\bfrom (v_agent_\w+|v_sales|stock_balance|shop_events)\b", low)
        key = m.group(1) if m else "?"
        rows = [dict(r) for r in self.data.get(key, [])]
        if key == "v_agent_focus_sales" and "max(sale_date)" in low:
            return _Cur([{"d": max((r["sale_date"] for r in rows), default=None)}])
        if key == "v_agent_shop_orders" and "count(*) as n" in low:
            return _Cur([{"n": 0, "v": Decimal(0)}])
        if key == "v_agent_shop_orders" and "status = any(%s) and not is_test" in low and "total_requested_bhd as total_bhd" in low:
            rows = [{"order_no": r["order_no"], "status": r["status"], "total_bhd": r["total_requested_bhd"],
                     "created_at": r["created_at"], "confirmed_at": r["confirmed_at"], "rep": r["rep"]}
                    for r in rows if r["status"] in ("new", "confirmed", "packed", "out_for_delivery") and not r["is_test"]]
        elif key == "v_agent_shop_orders" and "order_id as id" in low:
            rows = [{"id": r["order_id"], "order_no": r["order_no"], "status": r["status"],
                     "total_bhd": r["total_requested_bhd"], "total_confirmed_bhd": r["total_confirmed_bhd"],
                     "order_kind": r["order_kind"], "customer_id": r["customer_id"], "customer_shop": r["customer_shop"],
                     "customer_name": None, "customer_area": r["customer_area"], "created_at": r["created_at"],
                     "confirmed_at": r["confirmed_at"], "delivered_at": r["delivered_at"],
                     "cancelled_at": r["cancelled_at"], "source": r["source"], "salesman_id": r["salesman_id"],
                     "rep": r["rep"], "focus_name": r["rep_focus_name"]} for r in rows if not r["is_test"]]
        elif key == "v_agent_shop_lines" and "upper(item_code) as code" in low:
            rows = [{"order_id": r["order_id"], "code": r["item_code"].upper(), "display_name": r["display_name"],
                     "qty": r["qty"], "line_total_bhd": r["line_total_bhd"], "category": r["category"] or "Other"}
                    for r in rows]
        elif key == "v_agent_shop_events" and "device_key as device_id" in low:
            rows = [{"ts": r["ts"], "session_id": r["session_key"], "device_id": r["device_key"], "event": r["event"],
                     "referral_code": r["referral_code"],
                     "meta": {"q": r["search_term"]} if r.get("search_term") else None} for r in rows]
        elif key == "v_agent_rep_governance" and "select referral_code, rep" in low:
            rows = [{"referral_code": r["referral_code"], "rep": r["rep"]} for r in rows]
        elif key == "v_agent_focus_sales" and "upper(coalesce(sku_code, item_name)) as code" in low:
            rows = [{"invoice_no": r["invoice_no"], "sale_date": r["sale_date"], "customer_name": r["customer_name"],
                     "is_cash_customer": r["is_cash_customer"], "salesman_raw": r["salesman_raw"],
                     "code": (r["sku_code"] or r["item_name"]).upper(), "quantity": r["quantity"],
                     "taxable_bhd": r["taxable_bhd"], "gross_bhd": r["gross_bhd"], "narration": r["narration"]}
                    for r in rows if r["channel"] == "B2B" and r["division"] == "Accessories" and not r["is_giveaway"]]
        return _Cur(rows)


def _ts(day: str, hh: int = 10) -> datetime:
    return datetime.fromisoformat(f"{day}T{hh:02d}:00:00+03:00")


def _synthetic_views() -> dict[str, list[dict]]:
    """A week (Sun 20 - Sat 26 Sep 2026) of made-up marketplace and Focus data, shaped like the views."""
    def order(oid, no, status, rep_id, rep, total, day, **kw):
        base = {"order_id": oid, "order_no": no, "status": status, "order_kind": "standard", "is_test": False,
                "source": "market", "attribution_source": "link", "attribution_conflict": False, "referral_code": None,
                "src": None, "coupon_code": None, "salesman_id": rep_id, "rep": rep, "rep_focus_name": rep,
                "customer_id": oid + 100, "customer_shop": f"Shop {oid}", "customer_area": "Area",
                "created_at": _ts(day), "created_day": date.fromisoformat(day), "assigned_at": None,
                "confirmed_at": None, "packed_at": None, "out_for_delivery_at": None, "delivered_at": None,
                "cancelled_at": None, "cancel_reason_code": None, "paid_at": None, "updated_at": _ts(day),
                "confirm_hours": None, "deliver_hours": None, "items_count": 1, "units_count": 2,
                "has_backorder": False, "subtotal_bhd": Decimal(total), "discount_bhd": Decimal("0"),
                "delivery_bhd": Decimal("0"), "small_order_fee_bhd": None, "minimum_gap_bhd": None,
                "total_requested_bhd": Decimal(total), "subtotal_confirmed_bhd": None, "total_confirmed_bhd": None,
                "returned_bhd": None, "payment_status": "unpaid", "payment_method": None, "focus_invoice_no": None,
                "focus_links_confirmed": 0, "focus_link_state": "none"}
        base.update(kw)
        return base
    orders = [
        order(1, "YQ-2609-9001", "delivered", 1, "Rep K", "12.000", "2026-09-21",
              confirmed_at=_ts("2026-09-21", 12), delivered_at=_ts("2026-09-22"), focus_link_state="linked",
              focus_links_confirmed=1, subtotal_confirmed_bhd=Decimal("12.000")),
        order(2, "YQ-2609-9002", "new", 2, "Rep F", "8.500", "2026-09-22", customer_shop="Call 3312 3456 shop"),
        order(3, "YQ-2609-9003", "cancelled", 1, "Rep K", "4.000", "2026-09-23", cancelled_at=_ts("2026-09-23", 15)),
        order(4, "YQ-2609-9004", "confirmed", 2, "Rep F", "20.000", "2026-09-24",
              confirmed_at=_ts("2026-09-24", 20), total_requested_bhd=Decimal("21.000")),     # does not add up
    ]
    lines = [
        {"line_id": 11, "order_id": 1, "order_no": "YQ-2609-9001", "order_status": "delivered", "is_test": False,
         "order_created_at": _ts("2026-09-21"), "salesman_id": 1, "item_code": "SKU-A", "display_name": "Cable A",
         "category": "CABLES", "division": "Accessories", "qty": 4, "qty_confirmed": 4, "list_price_bhd": None,
         "unit_price_bhd": Decimal("3.000"), "unit_price_confirmed": Decimal("3.000"), "discount_bhd": Decimal("0"),
         "line_total_bhd": Decimal("12.000"), "line_total_confirmed": Decimal("12.000"), "line_status": "ok",
         "stock_status": "in", "backorder": False, "rule_ids": None},
        {"line_id": 12, "order_id": 2, "order_no": "YQ-2609-9002", "order_status": "new", "is_test": False,
         "order_created_at": _ts("2026-09-22"), "salesman_id": 2, "item_code": "SKU-B", "display_name": "Charger B",
         "category": "CHARGERS", "division": "Accessories", "qty": 1, "qty_confirmed": None, "list_price_bhd": None,
         "unit_price_bhd": Decimal("8.500"), "unit_price_confirmed": None, "discount_bhd": Decimal("0"),
         "line_total_bhd": Decimal("8.500"), "line_total_confirmed": None, "line_status": "ok",
         "stock_status": "out", "backorder": True, "rule_ids": None},
        {"line_id": 13, "order_id": 3, "order_no": "YQ-2609-9003", "order_status": "cancelled", "is_test": False,
         "order_created_at": _ts("2026-09-23"), "salesman_id": 1, "item_code": "SKU-A", "display_name": "Cable A",
         "category": "CABLES", "division": "Accessories", "qty": 1, "qty_confirmed": None, "list_price_bhd": None,
         "unit_price_bhd": Decimal("4.000"), "unit_price_confirmed": None, "discount_bhd": Decimal("0"),
         "line_total_bhd": Decimal("4.000"), "line_total_confirmed": None, "line_status": "ok",
         "stock_status": "in", "backorder": False, "rule_ids": None},
        {"line_id": 14, "order_id": 4, "order_no": "YQ-2609-9004", "order_status": "confirmed", "is_test": False,
         "order_created_at": _ts("2026-09-24"), "salesman_id": 2, "item_code": "SKU-C", "display_name": "Case C",
         "category": "CASES", "division": "Accessories", "qty": 10, "qty_confirmed": 10, "list_price_bhd": None,
         "unit_price_bhd": Decimal("2.000"), "unit_price_confirmed": Decimal("2.000"), "discount_bhd": Decimal("0"),
         "line_total_bhd": Decimal("20.000"), "line_total_confirmed": Decimal("20.000"), "line_status": "ok",
         "stock_status": "in", "backorder": False, "rule_ids": None},
    ]
    events = []
    n = 0
    for day, dev, evs in (("2026-09-21", "aaaa", ["view", "item", "add", "checkout_start", "order"]),
                          ("2026-09-21", "bbbb", ["view", "search"]), ("2026-09-22", "cccc", ["view", "item"]),
                          ("2026-09-24", "aaaa", ["view", "add", "checkout_start", "order"])):
        for ev in evs:
            n += 1
            events.append({"event_id": n, "ts": _ts(day, 9) + timedelta(minutes=n), "day": date.fromisoformat(day),
                           "session_key": "s" + dev, "device_key": dev, "event": ev, "item_code": None,
                           "referral_code": "rep-k" if dev == "aaaa" else None, "salesman_id": None, "src": None,
                           "rail": None, "search_term": "power bank" if ev == "search" else None,
                           "search_results": 0 if ev == "search" else None, "item_count": None})
    focus = []
    fid = 0
    for wk in range(14):                              # 14 weeks of Focus sales for two shops and a cash counter
        for cust, rep, amt, sku, dow in (("Focus Shop One", "Rep K - Acc WH", "50.000", "SKU-A", 1),
                                         ("Focus Shop Two", "Rep F - Acc WH", "30.000", "SKU-B", 3),
                                         ("Cash Customer", "Rep K - Acc WH", "5.000", "SKU-C", 2)):
            d = date(2026, 9, 20) - timedelta(weeks=13 - wk) + timedelta(days=dow)
            if d > date(2026, 9, 24):
                continue
            fid += 1
            focus.append({"line_id": fid, "invoice_no": f"SI : SI-T-{fid}", "sale_date": d, "customer_name": cust,
                          "is_cash_customer": cust.startswith("Cash"), "salesman_raw": rep, "salesman_resolved": rep,
                          "channel": "B2B", "division": "Accessories", "sale_type": "credit", "is_giveaway": False,
                          "sku_code": sku, "item_name": f"{sku} item", "category_name": "CABLES", "quantity": Decimal(4),
                          "rate_bhd": Decimal("1"), "gross_bhd": Decimal(amt) * Decimal("1.1"),
                          "discount_bhd": Decimal(0), "taxable_bhd": Decimal(amt), "vat_amount_bhd": Decimal(0),
                          "revenue_bhd": Decimal(amt) * Decimal("1.1"), "net_bhd": Decimal(amt),
                          "narration": "for YQ-2609-9001 call 3312 3456" if fid == 40 else None})
    # the delivered order 9001 (Rep K, SKU-A x4) lands on a Focus invoice two days later
    focus.append({"line_id": 999, "invoice_no": "SI : SI-T-999", "sale_date": date(2026, 9, 23),
                  "customer_name": "Focus Shop Three", "is_cash_customer": False, "salesman_raw": "Rep K - Acc WH",
                  "salesman_resolved": "Rep K - Acc WH", "channel": "B2B", "division": "Accessories",
                  "sale_type": "credit", "is_giveaway": False, "sku_code": "SKU-A", "item_name": "SKU-A item",
                  "category_name": "CABLES", "quantity": Decimal(4), "rate_bhd": Decimal("2.727"),
                  "gross_bhd": Decimal("12.000"), "discount_bhd": Decimal(0), "taxable_bhd": Decimal("10.909"),
                  "vat_amount_bhd": Decimal("1.091"), "revenue_bhd": Decimal("12.000"), "net_bhd": Decimal("10.909"),
                  "narration": None})
    return {
        "v_agent_shop_orders": orders, "v_agent_shop_lines": lines, "v_agent_shop_events": events,
        "v_agent_focus_sales": focus,
        "v_agent_rep_governance": [{"salesman_id": 1, "rep": "Rep K", "focus_name": "Rep K", "referral_code": "rep-k",
                                    "is_active": True, "has_login": True, "orders_waiting": 0},
                                   {"salesman_id": 2, "rep": "Rep F", "focus_name": "Rep F", "referral_code": "rep-f",
                                    "is_active": True, "has_login": False, "orders_waiting": 1}],
        "v_agent_data_trust": [
            {"source": "focus_sales", "as_of": date(2026, 9, 24), "loaded_at": None, "row_count": 40, "metric": None, "status": None},
            {"source": "focus_sku_match_90d", "as_of": date(2026, 9, 24), "loaded_at": None, "row_count": 40,
             "metric": Decimal("97.5"), "status": None},
            {"source": "stock_balance", "as_of": date(2026, 9, 24), "loaded_at": _ts("2026-09-25"), "row_count": 10,
             "metric": None, "status": None},
            {"source": "ar_ageing_totals", "as_of": None, "loaded_at": None, "row_count": 0, "metric": None, "status": None},
            {"source": "focus_upload", "as_of": date(2026, 9, 25), "loaded_at": _ts("2026-09-25"), "row_count": 5,
             "metric": Decimal("96.2"), "status": "ok"},
            {"source": "marketplace_events", "as_of": date(2026, 9, 26), "loaded_at": _ts("2026-09-26"),
             "row_count": 15, "metric": None, "status": None}],
        "v_agent_search_demand": [{"day": date(2026, 9, 21), "term": "power bank", "searches": 1,
                                   "zero_result_searches": 1, "devices": 1, "max_results": 0, "zero_result": True}],
        "v_agent_items": [
            {"item_code": "SKU-A", "display_name": "Cable A", "category": "CABLES", "is_active": True, "hidden": False,
             "trade_price_bhd": Decimal("3.300"), "stock_qty": Decimal(0), "sold_90d": Decimal(90),
             "restock_requests_30d": 2, "landed_cost_bhd": Decimal("1.000"), "prev_landed_cost_bhd": Decimal("0.800"),
             "cost_date": date(2026, 9, 10), "prev_cost_date": date(2026, 6, 1), "last_sold": date(2026, 9, 23)},
            {"item_code": "SKU-B", "display_name": "Charger B", "category": "CHARGERS", "is_active": True, "hidden": False,
             "trade_price_bhd": Decimal("1.100"), "stock_qty": Decimal(5), "sold_90d": Decimal(90),
             "restock_requests_30d": 0, "landed_cost_bhd": Decimal("1.200"), "prev_landed_cost_bhd": None,
             "cost_date": date(2026, 8, 1), "prev_cost_date": None, "last_sold": date(2026, 9, 22)}],
        "v_agent_market_signals": [{"signal": "field_note", "ref_id": 1, "day": date(2026, 9, 22), "item_code": None,
                                    "label": "Shop asked for a blue case, reach me on orders@example.com",
                                    "category": "request", "n": 1, "sources": 1, "qty": None, "price": None,
                                    "currency": None, "status": None}],
    }


@test("pack: a statement that is not a read never reaches the database")
def _():
    from scripts.ai_head import pack
    conn = _FakeConn({})
    db = pack.ReadOnly(conn, "ai_head_ro", "ai_head_ro", [])
    for bad in ("insert into ai_insights values (1)", "update shop_orders set status = 'x'", "delete from x",
                "grant select on x to anon", "alter role ai_head_ro set x = 1", "call something()",
                "  drop view v_agent_items", "select 1; delete from shop_orders",
                "with d as (delete from ai_insights returning 1) select * from d",
                "select * from v_agent_items for update"):
        try:
            db.q(bad)
            raise AssertionError(f"not refused: {bad}")
        except RuntimeError as e:
            assert "refusing" in str(e)
    assert conn.sql == [], "nothing was sent"
    db.q("select 1")
    db.q("  with x as (select 1) select * from x")
    assert len(conn.sql) == 2


@test("pack: the weekly loader reads through the views and weekly_report.compute() runs unchanged")
def _():
    from scripts.ai_head import pack
    db = pack.ReadOnly(_FakeConn(_synthetic_views()), "ai_head_ro", "ai_head_ro", [])
    ws, we = date(2026, 9, 20), date(2026, 9, 26)
    d, how = pack.load_weekly(db, set(VIEWS), ws, we)
    assert how == "views"
    assert set(d) == {"orders", "lines", "prev", "open_now", "events", "reps", "focus_max", "focus", "t0", "t1"}
    assert all(o["customer_name"] is None for o in d["orders"]), "no person's name through the views"
    now = datetime(2026, 9, 27, 6, 0, tzinfo=timezone.utc)
    m = pack.weekly_metrics(d, ws, we, now)
    assert m["n_orders"] == 4 and m["n_live"] == 3
    assert m["value"] == Decimal("41.500") or m["value"] == Decimal("41.5")
    assert m["visitors"] == 3 and m["via_link"] == 1
    assert [s["label"] for s in m["status"]] == ["Delivered", "Confirmed / on the way", "Waiting for confirmation", "Cancelled"]
    assert [w["order_no"] for w in m["waiting"]] == ["YQ-2609-9002"]
    assert m["zero_searches"] == [("power bank", 1)] or m["zero_searches"] == [] or m["top_searches"]
    assert [x["order_no"] for x in m["impact"]["matches"]] == ["YQ-2609-9001"], m["impact"]["matches"]
    assert m["loaded_from"] if "loaded_from" in m else True
    assert all("<" not in h for h in m["highlights_text"]), "HTML stripped"
    # an ai_head_ro login without the views cannot fall back to base tables
    d2, how2 = pack.load_weekly(db, {"v_agent_shop_orders"}, ws, we)
    assert d2 is None and how2.startswith("missing:")


@test("pack: the trust gate — fresh, partial week, stale, failing totals, match rate, verify_numbers")
def _():
    from scripts.ai_head import pack
    src = _synthetic_views()["v_agent_data_trust"]
    orders = _synthetic_views()["v_agent_shop_orders"]
    lines = _synthetic_views()["v_agent_shop_lines"]
    t = pack.trust_gate(src, date(2026, 9, 27), orders=orders, lines=lines, week_end=date(2026, 9, 26),
                        verify={"result": "pass", "detail": "ALL CHECKS PASS"})
    by = {c["check"]: c for c in t["checks"]}
    assert by["focus_sales_fresh"]["status"] == "ok" and by["focus_sales_fresh"]["value"] == 3
    assert by["focus_covers_week"]["status"] == "warn"
    assert by["ar_loaded"]["status"] == "warn"
    assert by["join_match"]["status"] == "ok" and by["sku_match"]["status"] == "ok"
    assert by["order_totals"]["status"] == "fail" and "YQ-2609-9004" in by["order_totals"]["detail"]
    assert by["order_lines_sum"]["status"] == "ok"
    assert by["focus_match_rate"]["detail"].startswith("1 of 1 orders delivered")
    assert by["verify_numbers"]["status"] == "ok"
    assert t["verdict"] == "fail" and t["lead"].startswith("DATA FAILED A CHECK")
    # a week later nobody uploaded: stale, and it leads
    t2 = pack.trust_gate(src, date(2026, 10, 1))
    assert {c["check"]: c["status"] for c in t2["checks"]}["focus_sales_fresh"] == "stale"
    assert t2["verdict"] == "stale" and t2["lead"].startswith("DATA IS STALE")
    # a join under the 80 % floor fails; verify not run is a caveat, not a pass
    low = [dict(s, metric=Decimal("61.0")) if s["source"] == "focus_upload" else s for s in src]
    t3 = pack.trust_gate(low, date(2026, 9, 27))
    by3 = {c["check"]: c for c in t3["checks"]}
    assert by3["join_match"]["status"] == "fail" and by3["verify_numbers"]["status"] == "warn"
    assert pack.trust_gate([], date(2026, 9, 27))["verdict"] == "fail", "nothing loaded is a failure"


@test("pack: month-close readiness needs an upload on or after the 1st and Focus data to the month end")
def _():
    from scripts.ai_head import pack

    def src(upload: str, as_of: str):
        return [{"source": "focus_sales", "as_of": date.fromisoformat(as_of)},
                {"source": "focus_upload", "loaded_at": _ts(upload), "as_of": date.fromisoformat(upload)}]
    assert pack.monthly_ready(src("2026-10-01", "2026-09-30"), "2026-09")["ready"]
    assert pack.monthly_ready(src("2026-10-02", "2026-09-28"), "2026-09")["ready"], "a month ending on a weekend"
    r = pack.monthly_ready(src("2026-09-30", "2026-09-30"), "2026-09")
    assert not r["ready"] and "01 Oct 2026" in r["why"]
    assert not pack.monthly_ready(src("2026-10-03", "2026-09-20"), "2026-09")["ready"]
    assert pack.month_bounds("2026-12") == (date(2026, 12, 1), date(2026, 12, 31))
    assert pack.month_bounds("2027-02") == (date(2027, 2, 1), date(2027, 2, 28))


@test("pack: XmR judges the last point against the baseline; modified z flags a typo-sized quantity")
def _():
    from scripts.ai_head import pack
    assert not pack.xmr([1, 2, 3], "short")["judged"]
    flat = pack.xmr([100, 102, 98, 101, 99, 100, 101, 100], "flat")
    assert flat["judged"] and flat["signal"] is None
    spike = pack.xmr([100, 102, 98, 101, 99, 100, 101, 400], "spike")
    assert spike["signal"] == "above" and spike["upper"] < Decimal(400)
    drop = pack.xmr([100, 102, 98, 101, 99, 100, 101, 5], "drop")
    assert drop["signal"] == "below"
    z = pack.modified_z([2, 2, 3, 2, 2, 50])
    assert abs(z[-1]) > 3.5 and all(abs(v) < 3.5 for v in z[:-1])
    assert pack.modified_z([5, 5, 5]) == [0.0, 0.0, 0.0]
    lines = [{"item_code": "sku-a", "order_no": f"YQ-{i}", "qty": q, "is_test": False}
             for i, q in enumerate([2, 3, 2, 2, 3, 2, 200])]
    out = pack.qty_outliers(lines)
    assert [o["order_no"] for o in out] == ["YQ-6"] and out[0]["usual_qty"] == 2
    assert pack.qty_outliers(lines[:4]) == [], "too few lines to judge"


@test("pack: Focus context compares a partial week on the same weekdays; concentration and HHI")
def _():
    from scripts.ai_head import pack
    rows = _synthetic_views()["v_agent_focus_sales"]
    ctx = pack.focus_context(rows, date(2026, 9, 20), date(2026, 9, 26))
    assert ctx["partial_week"] and ctx["cover_end"] == date(2026, 9, 23)
    assert len(ctx["weeks"]) == 13 and ctx["weeks"][-1]["week_start"] == date(2026, 9, 20)
    assert all((w["through"] - w["week_start"]).days == 3 for w in ctx["weeks"]), "Sun-Wed every week"
    assert ctx["weeks"][-1]["b2b_accessories_ex_vat"] == Decimal("95.909")     # 50 + 30 + 5 (cash) + 10.909
    assert ctx["weeks"][-1]["b2b_named_shops"] == 3
    reps = {r["rep"]: r for r in ctx["reps"]}
    assert reps["rep k"]["avg_prior_4"] == Decimal("55.000")
    c = pack.concentration([{"customer_name": "a", "net_bhd": 60}, {"customer_name": "b", "net_bhd": 40}])
    assert c["hhi"] == 5200 and c["top10_share_pct"] == Decimal("100.0")
    assert pack.concentration([])["hhi"] is None


@test("pack: merchant movement — new, reactivated, due, at risk, dormant, with reasons")
def _():
    from scripts.ai_head import pack

    def line(cust, day, amt="10", sku="S1"):
        return {"customer_name": cust, "sale_date": date.fromisoformat(day), "channel": "B2B", "division": "Accessories",
                "is_giveaway": False, "is_cash_customer": False, "net_bhd": Decimal(amt), "sku_code": sku,
                "salesman_resolved": "Rep K - Acc WH"}
    rows = [line("Newcomer", "2026-09-22")]
    rows += [line("Regular", d) for d in ("2026-07-01", "2026-07-11", "2026-07-21", "2026-07-31", "2026-08-10")]
    rows += [line("Comeback", "2026-03-01"), line("Comeback", "2026-03-10"), line("Comeback", "2026-09-23")]
    rows += [line("Gone", d) for d in ("2025-12-01", "2025-12-15", "2026-01-01", "2026-01-15")]
    rows += [line("Cash Customer", "2026-09-24")]
    rows[-1]["is_cash_customer"] = True
    rows += [line("Anchor", "2026-09-24")]
    out = {r["customer_name"]: r for r in pack.merchant_movement(rows, date(2026, 9, 20), date(2026, 9, 26))}
    assert "Cash Customer" not in out
    assert out["Newcomer"]["flags"] == "new"
    assert "reactivated" in out["Comeback"]["flags"]
    assert out["Regular"]["flags"].startswith("due") and out["Regular"]["cadence_source"] == "own"
    assert out["Gone"]["flags"] == "dormant"
    assert "overdue > 2x cadence" in out["Regular"]["reasons"]


@test("pack: item signals — sold out with demand, margins on the ex-VAT trade price, below cost, cost moves")
def _():
    from scripts.ai_head import pack
    v = _synthetic_views()
    sig = pack.item_signals(v["v_agent_items"], v["v_agent_shop_lines"], v["v_agent_search_demand"])
    so = sig["sold_out_with_demand"]
    assert [s["item_code"] for s in so] == ["SKU-A"] and so[0]["est_units_lost_per_week"] == Decimal("7.000")
    assert so[0]["marketplace_units_asked_in_pack"] == 4 and so[0]["estimate"], "the cancelled order does not count"
    m = {x["item_code"]: x for x in sig["margins"]}
    assert m["SKU-A"]["trade_ex_vat"] == Decimal("3.000") and m["SKU-A"]["margin_pct"] == Decimal("66.7")
    assert [b["item_code"] for b in sig["below_cost"]] == ["SKU-B"]
    assert sig["cost_moves"][0]["change_pct"] == Decimal("25.0")
    assert sig["cost_coverage"] == {"active_items": 2, "with_landed_cost": 2, "pct": Decimal("100.0")}
    assert sig["zero_result_terms"] == [("power bank", 1)]


@test("pack: the holdout readout splits each rep's shops with app.followups.is_holdout")
def _():
    from app.followups import is_holdout
    from scripts.ai_head import pack
    rows = []
    for i in range(40):
        rows.append({"customer_name": f"Synthetic Shop {i}", "sale_date": date(2026, 9, 24) - timedelta(days=i),
                     "channel": "B2B", "division": "Accessories", "is_giveaway": False, "is_cash_customer": False,
                     "net_bhd": Decimal(1), "salesman_resolved": "Rep K - Acc WH"})
    out = pack.holdout_readout(rows)
    assert len(out) == 1 and out[0]["rep"] == "rep k"
    held = sum(1 for i in range(40) if is_holdout("Rep K - Acc WH", f"Synthetic Shop {i}"))
    assert out[0]["held"] == held and out[0]["shown"] == 40 - held
    assert out[0]["shown_bought"] + out[0]["held_bought"] == 14


@test("pack: a whole build writes every file, masks phones and emails, and only reads")
def _():
    from scripts.ai_head import pack
    conn = _FakeConn(_synthetic_views())
    db = pack.ReadOnly(conn, "ai_head_ro", "ai_head_ro", [])
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "pack"
        man = pack.build(db, date(2026, 9, 20), date(2026, 9, 26), out,
                         now=datetime(2026, 9, 27, 6, 0, tzinfo=timezone.utc))
        names = {p.name for p in out.iterdir()}
        for f in ("manifest.json", "trust.json", "weekly_metrics.json", "focus_context.json", "anomalies.json",
                  "items.json", "shop_orders.csv", "shop_lines.csv", "rep_governance.csv", "merchants.csv",
                  "followups_holdout.csv", "items.csv", "market_signals.csv", "focus_lines_week.csv"):
            assert f in names, f
        assert man["kind"] == "weekly" and man["connection"]["mode"] == "ai_head_ro" and man["views_missing"] == []
        assert man["trust_verdict"] == "fail", "the synthetic order 9004 does not add up"
        blob = "".join(p.read_text(encoding="utf-8") for p in out.iterdir())
        assert "3312 3456" not in blob and "33123456" not in blob, "phones masked"
        assert "orders@example.com" not in blob and "[email]" in blob, "emails masked"
        assert "YQ-2609-9001" in blob, "order numbers survive the mask"
        with (out / "shop_orders.csv").open(encoding="utf-8") as f:
            head = f.readline()
        assert "customer_phone" not in head and "customer_name" not in head
    for sql in conn.sql:
        assert pack.READ_ONLY_SQL.match(sql) and not pack.WRITE_WORDS.search(sql), sql[:60]


@test("pack: the month-close pack (§20) — month vs previous vs trailing, lost-sales estimate, margin drift, readiness")
def _():
    from scripts.ai_head import pack
    v = _synthetic_views()
    rows = v["v_agent_focus_sales"]
    items = v["v_agent_items"]
    mon = pack.monthly_context(rows, "2026-09", items, lambda *a, **k: [])
    b = mon["b2b_accessories_ex_vat"]
    sept = sum((r["net_bhd"] for r in rows if r["sale_date"].month == 9), Decimal(0))
    aug = sum((r["net_bhd"] for r in rows if r["sale_date"].month == 8), Decimal(0))
    assert b["month"] == pack.q3(sept) and b["previous"] == pack.q3(aug)
    assert b["months_in_12m_baseline"] == 3, "June, July, August"
    assert [x["item_code"] for x in mon["lost_sales_estimate"]] == ["SKU-A"] and mon["lost_sales_estimate"][0]["estimate"]
    assert [x["item_code"] for x in mon["margin_drift"]] == ["SKU-A"], "the September receipt moved the cost"
    assert mon["promotion_incrementality"].startswith("not measurable yet")
    with tempfile.TemporaryDirectory() as tmp:
        db = pack.ReadOnly(_FakeConn(v), "ai_head_ro", "ai_head_ro", [])
        man = pack.build(db, date(2026, 9, 1), date(2026, 9, 30), Path(tmp) / "m", month="2026-09",
                         now=datetime(2026, 10, 2, 6, 0, tzinfo=timezone.utc))
        assert man["kind"] == "monthly" and "monthly.json" in man["files"]
        trust = json.loads((Path(tmp) / "m" / "trust.json").read_text(encoding="utf-8"))
        assert trust["monthly"]["ready"] is False and "01 Oct 2026" in trust["monthly"]["why"]
        ctx = json.loads((Path(tmp) / "m" / "focus_context.json").read_text(encoding="utf-8"))
        assert ctx["weeks"][-1]["week_start"] == "2026-09-20", "the month's last complete Sun-Sat week"
    assert pack.main(["--monthly", "2026-13"]) == 2 and pack.main(["--week-ending", "2026-09-25"]) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 4. the loader
# ═══════════════════════════════════════════════════════════════════════════════

def _doc(**over) -> dict:
    items = [
        {"id": "A1", "section": "Top actions", "tag": "RECOMMENDATION", "rank": 1, "confidence": "medium",
         "text": "Clear the orders waiting over 24 hours.", "action": "Reps confirm or cancel by Tuesday.",
         "evidence": {"files": ["weekly_metrics.json"]}},
        {"id": "A2", "section": "Top actions", "tag": "recommendation", "rank": 2, "confidence": "low",
         "text": "Re-share the marketplace link.", "action": "Each rep posts his link once."},
        {"id": "S1", "section": "Sales", "tag": "FACT", "confidence": "high", "text": "B2B accessories BHD 95.909 ex-VAT."},
        {"id": "H1", "section": "Market intelligence", "tag": "LOW-CONFIDENCE HYPOTHESIS", "confidence": "low",
         "text": "Power bank searches may reflect a competitor stock-out."},
    ]
    doc = {"kind": "weekly", "week_ending": "2026-09-26", "data_as_of": "2026-09-24", "items": items}
    doc.update(over)
    return doc


@test("loader: validation refuses bad tags, confidence, ranks, sections, dates and any contact detail")
def _():
    from scripts.ai_head import load_insights as li
    ok = li.validate(_doc())
    assert [i["tag"] for i in ok["items"]] == ["recommendation", "recommendation", "fact", "hypothesis"]
    assert all(len(i["insight_key"]) == 64 for i in ok["items"]) and ok["items"][0]["status"] == "proposed"

    def refused(doc, needle):
        try:
            li.validate(doc)
        except li.InsightError as e:
            assert needle in str(e), (needle, str(e))
            return
        raise AssertionError(f"not refused: {needle}")
    bad = _doc()
    bad["items"][3]["confidence"] = "high"
    refused(bad, "low confidence by definition")
    bad = _doc()
    bad["items"][2]["rank"] = 3
    refused(bad, "only a recommendation takes a rank")
    bad = _doc()
    bad["items"][1]["rank"] = 1
    refused(bad, "rank 1 used twice")
    bad = _doc()
    bad["items"][0]["rank"] = 6
    refused(bad, "rank must be 1-5")
    bad = _doc()
    bad["items"][2]["section"] = "Gossip"
    refused(bad, "section must be one of")
    bad = _doc()
    bad["items"][2]["tag"] = "opinion"
    refused(bad, "tag must be")
    bad = _doc()
    bad["items"][2]["text"] = "Call the shop on 3312 3456."
    refused(bad, "phone number or email")
    bad = _doc()
    bad["items"][0]["evidence"] = {"who": "rep@example.com"}
    refused(bad, "evidence carries a phone number or email")
    bad = _doc()
    bad["items"][1]["id"] = "A1"
    refused(bad, "duplicate id")
    refused(_doc(week_ending="2026-09-25"), "not a Saturday")
    refused(_doc(kind="monthly", week_ending="2026-09-26"), "not the last day of a month")
    li.validate(_doc(kind="monthly", week_ending="2026-09-30"))
    refused(_doc(kind="daily"), "kind must be weekly or monthly")
    try:
        li.validate(_doc(items=[]))
        raise AssertionError("no items must be refused")
    except li.InsightError:
        pass


@test("loader: the owner's decisions — approve, reject, approve the report, done; --by required; Top-5 gate")
def _():
    from scripts.ai_head import load_insights as li
    doc = li.validate(_doc())
    assert li.undecided_top(doc) == ["A1", "A2"]
    try:
        li.decide(doc, approve={"A1"})
        raise AssertionError("--by required")
    except li.InsightError as e:
        assert "--by" in str(e)
    for kw, needle in (({"approve": {"ZZ"}}, "unknown id"), ({"approve": {"A1"}, "reject": {"A1"}}, "at once")):
        try:
            li.decide(li.validate(_doc()), by="owner@example.com", **kw)
            raise AssertionError(needle)
        except li.InsightError as e:
            assert needle in str(e)
    doc = li.decide(li.validate(_doc()), approve={"A1"}, reject={"A2"}, approve_report=True, by="owner@example.com")
    st = {i["id"]: i["status"] for i in doc["items"]}
    assert st == {"A1": "approved", "A2": "rejected", "S1": "approved", "H1": "approved"}
    assert li.undecided_top(doc) == [] and all(i["decided_by"] == "owner@example.com" for i in doc["items"])
    try:
        li.decide(li.validate(_doc()), done={"A1"}, by="owner@example.com")
        raise AssertionError("done needs approved")
    except li.InsightError as e:
        assert "only an approved action" in str(e)
    d2 = _doc()
    d2["items"][0]["status"] = "approved"
    try:
        li.decide(li.validate(d2))
        raise AssertionError("a decided statement needs a decider")
    except li.InsightError as e:
        assert "no decider" in str(e)


class _LCur:
    def __init__(self, db):
        self.db, self.rowcount, self._one = db, 0, None

    def execute(self, sql, params=None):
        low = " ".join(sql.split()).lower()
        self.db.log.append(low)
        if low.startswith("select to_regclass"):
            self._one = (self.db.exists,)
        elif low.startswith("select status from ai_insights"):
            row = self.db.rows.get(params[0])
            self._one = (row["status"],) if row else None
        elif low.startswith("insert into ai_insights"):
            key = params[13]
            if key in self.db.rows:
                self.rowcount = 0
            else:
                self.db.rows[key] = {"status": params[10], "decided_by": params[11], "text": params[4]}
                self.rowcount = 1
        elif low.startswith("update ai_insights set status"):
            st, by, key, have = params
            row = self.db.rows.get(key)
            if row and row["status"] == have:
                row.update(status=st, decided_by=by)
                self.rowcount = 1
            else:
                self.rowcount = 0
        else:
            raise AssertionError("unexpected SQL: " + low[:60])

    def fetchone(self):
        return self._one


class _LConn:
    def __init__(self, exists=True):
        self.exists, self.rows, self.log = exists, {}, []

    def cursor(self):
        return _LCur(self)


@test("loader: commit inserts new statements, moves statuses along the allowed path, refuses the rest")
def _():
    from scripts.ai_head import load_insights as li
    conn = _LConn()
    doc = li.decide(li.validate(_doc()), approve={"A1"}, reject={"A2"}, approve_report=True, by="owner@example.com")
    res = li.commit(conn, doc, "exports/ai_head/2026-09-26/insights.json", "owner@example.com")
    assert res == {"inserted": 4, "status_moved": 0, "unchanged": 0}
    assert {r["status"] for r in conn.rows.values()} == {"approved", "rejected"}
    # the same file again: nothing new, nothing moved
    assert li.commit(conn, doc, "f", "owner@example.com") == {"inserted": 0, "status_moved": 0, "unchanged": 4}
    # a week later: A1 is done
    later = li.decide(li.validate(_doc()), approve={"A1"}, reject={"A2"}, approve_report=True, by="owner@example.com")
    later = li.decide(later, done={"A1"}, by="owner@example.com")
    assert li.commit(conn, later, "f", "owner@example.com") == {"inserted": 0, "status_moved": 1, "unchanged": 3}
    # a rejected action cannot come back as approved
    back = li.decide(li.validate(_doc()), approve={"A1", "A2"}, approve_report=True, by="owner@example.com")
    try:
        li.commit(conn, back, "f", "owner@example.com")
        raise AssertionError("rejected -> approved must be refused")
    except li.InsightError as e:
        assert "A2: rejected -> approved" in str(e)
    try:
        li.commit(_LConn(exists=False), doc, "f", "owner@example.com")
        raise AssertionError("missing table")
    except li.InsightError as e:
        assert "r7b_ai_head_migration.sql" in str(e)


@test("loader: a dry run never connects; --commit refuses an undecided Top 5 before connecting")
def _():
    import psycopg
    from scripts.ai_head import load_insights as li

    def boom(*a, **k):
        raise AssertionError("connected")
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "insights.json"
        f.write_text(json.dumps(_doc()), encoding="utf-8")
        with _Patched((psycopg, "connect", boom)):
            assert li.main([str(f)]) == 0
            assert li.main([str(f), "--approve", "A1", "--by", "owner@example.com"]) == 0
            assert li.main([str(f), "--approve", "A1", "--by", "owner@example.com", "--commit"]) == 2
            bad = Path(tmp) / "bad.json"
            bad.write_text(json.dumps(_doc(week_ending="2026-09-25")), encoding="utf-8")
            assert li.main([str(bad)]) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 5. GET /management/insights
# ═══════════════════════════════════════════════════════════════════════════════

class _MissingTable(Exception):
    code = "PGRST205"


class _IQ:
    def __init__(self, db):
        self.db, self.f, self.lim = db, [], None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self.f.append(lambda r, c=col, v=val: str(r.get(c)) == str(v))
        return self

    def in_(self, col, vals):
        self.f.append(lambda r, c=col, v=tuple(vals): r.get(c) in v)
        return self

    def order(self, col, desc=False, **_k):
        self.sort = (col, desc)
        return self

    def limit(self, n):
        self.lim = n
        return self

    def execute(self):
        if self.db.missing:
            raise _MissingTable("Could not find the table 'public.ai_insights' in the schema cache")
        rows = [dict(r) for r in self.db.rows if all(f(r) for f in self.f)]
        if getattr(self, "sort", None):
            rows.sort(key=lambda r: str(r.get(self.sort[0])), reverse=self.sort[1])
        return SimpleNamespace(data=rows[: self.lim] if self.lim else rows)


class _IClient:
    def __init__(self, rows=(), missing=False, users=None):
        self.rows, self.missing, self.users = list(rows), missing, users or {}

    def table(self, name):
        if name == "user_roles":
            return _Users(self.users)
        assert name == "ai_insights", name
        return _IQ(self)


class _Users:
    def __init__(self, users):
        self.users, self.email = users, None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self.email = val
        return self

    def limit(self, _n):
        return self

    def execute(self):
        row = self.users.get(self.email)
        return SimpleNamespace(data=[row] if row else [])


def _irow(i, week, section, tag, status, rank=None, text=None, by="owner@example.com"):
    return {"id": i, "kind": "weekly", "week_ending": week, "section": section, "tag": tag,
            "text": text or f"Statement {i}.", "evidence": None, "confidence": "medium", "action": None,
            "rank": rank, "status": status, "data_as_of": "2026-09-24",
            "decided_by": None if status == "proposed" else by,
            "decided_at": None if status == "proposed" else "2026-09-27T09:00:00+03:00", "created_at": "x"}


ROWS = [
    _irow(1, "2026-09-26", "Top actions", "recommendation", "approved", rank=2),
    _irow(2, "2026-09-26", "Top actions", "recommendation", "rejected", rank=1),
    _irow(3, "2026-09-26", "Sales", "fact", "approved", text="Reach the shop on 3312 3456."),
    _irow(4, "2026-09-26", "Executive summary", "analysis", "proposed"),
    _irow(5, "2026-09-26", "Top actions", "recommendation", "done", rank=3),
    _irow(6, "2026-09-19", "Sales", "fact", "approved"),
]

USERS = {"boss@example.com": {"email": "boss@example.com", "role": "admin", "features": [], "status": "active"},
         "mgmt@example.com": {"email": "mgmt@example.com", "role": "management",
                              "features": ["Dashboard", "Sales"], "status": "active"},
         "rep@example.com": {"email": "rep@example.com", "role": "salesman", "features": ["Shop Orders"],
                             "status": "active"},
         "clerk@example.com": {"email": "clerk@example.com", "role": "member", "features": ["Dashboard"],
                               "status": "active"}}


def _api(client):
    from fastapi.testclient import TestClient
    import app.main as m
    from app import auth, database
    database.invalidate_user_cache()
    return TestClient(m.app), _Patched(
        (database, "get_client", lambda: client),
        (database, "_select_user_row", lambda email: client.users.get(email)),
        (auth, "_decode_token", lambda tok: {"sub": "uid-" + tok, "email": tok + "@example.com"}),
        (m.limiter, "enabled", False))


def _get(tc, who, **params):
    return tc.get("/management/insights", params=params, headers={"authorization": f"Bearer {who}"})


@test("api: admin sees every status and who decided; management sees approved / done only, masked")
def _():
    client = _IClient(ROWS, users=USERS)
    tc, p = _api(client)
    with p:
        r = _get(tc, "boss")
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["available"] and j["week_ending"] == "2026-09-26" and j["weeks"] == ["2026-09-26", "2026-09-19"]
        assert [i["id"] for i in j["insights"]] == [4, 2, 1, 5, 3], "the §19 order, ranks within a section"
        assert j["insights"][0]["decided_by"] is None and j["insights"][1]["decided_by"] == "owner@example.com"
        assert [a["rank"] for a in j["top_actions"]] == [1, 2, 3]
        r = _get(tc, "boss", status="rejected")
        assert [i["id"] for i in r.json()["insights"]] == [2]
        r = _get(tc, "mgmt")
        assert r.status_code == 200, r.text
        j = r.json()
        assert [i["id"] for i in j["insights"]] == [1, 5, 3]
        assert all("decided_by" not in i for i in j["insights"])
        assert {i["status"] for i in j["insights"]} == {"approved", "done"}
        assert "3312 3456" not in r.text and "[number]" in r.text
        assert j["insights"][2]["tag_label"] == "FACT"
        r = _get(tc, "mgmt", week_ending="2026-09-19")
        assert [i["id"] for i in r.json()["insights"]] == [6]
        r = _get(tc, "mgmt", status="rejected")
        assert {i["status"] for i in r.json()["insights"]} <= {"approved", "done"}, "management cannot widen"


@test("api: everyone else 403; bad parameters 400; before the migration {available: false}")
def _():
    client = _IClient(ROWS, users=USERS)
    tc, p = _api(client)
    with p:
        for who in ("rep", "clerk"):
            r = _get(tc, who)
            assert r.status_code == 403, (who, r.status_code)
        assert tc.get("/management/insights").status_code == 401
        assert _get(tc, "boss", kind="daily").status_code == 400
        assert _get(tc, "boss", week_ending="last week").status_code == 400
        assert _get(tc, "boss", status="maybe").status_code == 400
        assert _get(tc, "mgmt", limit=100000).status_code == 200
        client.missing = True
        r = _get(tc, "mgmt")
        assert r.status_code == 200 and r.json()["available"] is False and "r7b_ai_head_migration" in r.json()["hint"]
        client.missing, client.rows = False, []
        j = _get(tc, "boss").json()
        assert j["available"] and j["week_ending"] is None and j["insights"] == []


@test("api: the route is gated by the login (get_current_user) and a GET, so the read-only gate lets management in")
def _():
    from fastapi.routing import APIRoute
    import app.main as m
    from app import auth
    routes = [r for r in m.app.routes if isinstance(r, APIRoute) and r.path == "/management/insights"]
    assert len(routes) == 1 and routes[0].methods == {"GET"}
    assert [d.call for d in routes[0].dependant.dependencies] == [auth.get_current_user]
    assert not auth.read_only_refuses("GET", "/management/insights")
    assert auth.read_only_refuses("POST", "/management/insights")


# ═══════════════════════════════════════════════════════════════════════════════
# 6. the command files, the README, .env.example, .gitignore
# ═══════════════════════════════════════════════════════════════════════════════

@test("commands: the weekly review follows §19 (sections, tags, trust first, Top 5, never send, load after approval)")
def _():
    from app.ai_insights import SECTIONS
    w = (ROOT / ".claude" / "commands" / "weekly-review.md").read_text(encoding="utf-8")
    assert w.startswith("---\ndescription:")
    for needle in ("python -m scripts.ai_head.pack --verify $ARGUMENTS", "trust.json", "FACT", "ANALYSIS",
                   "RECOMMENDATION", "LOW-CONFIDENCE HYPOTHESIS", "freshness", "Never send anything",
                   "Top 5", "insights.json", "load_insights", "--commit", "only the top 1–3", "yq-sourcing-unit",
                   "python -m scripts.ai_head.render", "#6D4091", "corroborate"):
        assert needle in w, needle
    for s in SECTIONS:
        assert s in w, s
    assert w.index("Step 1") < w.index("Step 13") < w.index("Step 15")
    m = (ROOT / ".claude" / "commands" / "monthly-review.md").read_text(encoding="utf-8")
    for needle in ("--monthly $ARGUMENTS", "ready", "1st of the next", "12-month", "HHI", "lost-sales ESTIMATE",
                   "margin drift", "month-close", "incrementality", '"kind": "monthly"'):
        assert needle in m, needle


@test("README: the Sunday routine and a Task Scheduler line for weekly_report --send --to <list>")
def _():
    r = (ROOT / "scripts" / "ai_head" / "README.md").read_text(encoding="utf-8")
    assert "schtasks /Create" in r and "/SC WEEKLY /D SUN" in r and "weekly_report_task.cmd" in r
    assert "alter role ai_head_ro password" in r and "AI_HEAD_DATABASE_URL" in r and "ai_head_ro.<project-ref>" in r
    cmd = (ROOT / "scripts" / "ai_head" / "weekly_report_task.cmd").read_text(encoding="utf-8")
    assert 'python -m scripts.weekly_report --send --to "%~1"' in cmd
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "# AI_HEAD_DATABASE_URL=postgresql://ai_head_ro.<project-ref>:<password>@" in env
    ign = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "exports/" in ign, "the packs carry commercial data"


# ═══════════════════════════════════════════════════════════════════════════════
# 7. local Postgres replay (SKIPs cleanly without a local cluster)
# ═══════════════════════════════════════════════════════════════════════════════

LOCAL_DSN = os.environ.get("YQ_LOCAL_PG_R7B", os.environ.get("YQ_LOCAL_PG", "postgresql://postgres@localhost:55432/postgres"))
REPLAY_DB = "r7b_ai_head_replay"

SCHEMA = """
create table salesmen (id bigint generated by default as identity primary key, name text not null unique, phone text,
  email text, whatsapp text, user_email text, focus_name text, referral_code text not null unique,
  is_active boolean not null default true);
create table catalog_items (id bigint generated by default as identity primary key, item_code text not null unique,
  display_name text, spec text, category text, brand text, division text, dealer_price numeric(12,3),
  roadshow_price numeric(12,3), rrp numeric(12,3), is_active boolean not null default true,
  hidden boolean not null default false, moq integer not null default 1, pack_size integer, updated_by text);
create table shop_orders (id bigint generated by default as identity primary key, order_no text not null unique,
  token text not null unique, status text not null default 'new', customer_name text not null,
  customer_phone text not null, customer_shop text, customer_area text, customer_email text, note text,
  salesman_id bigint references salesmen(id), salesman_name text, source text not null default 'market',
  referral_code text, src text, coupon_code text, subtotal_bhd numeric(12,3) not null default 0,
  discount_bhd numeric(12,3) not null default 0, delivery_bhd numeric(12,3) not null default 0,
  total_bhd numeric(12,3) not null default 0, items_count integer not null default 0,
  units_count integer not null default 0, has_backorder boolean not null default false, ip_hash text, ua text,
  created_at timestamptz default now(), updated_at timestamptz default now(), placed_by text, customer_id bigint,
  device_id text, attribution_source text, attribution_conflict boolean, assigned_at timestamptz,
  confirmed_at timestamptz, packed_at timestamptz, out_for_delivery_at timestamptz, delivered_at timestamptz,
  cancelled_at timestamptz, cancel_reason text, cancel_reason_code text, subtotal_confirmed_bhd numeric(12,3),
  total_confirmed_bhd numeric(12,3), payment_status text, payment_method text, focus_invoice_no text,
  order_kind text, small_order_fee_bhd numeric(12,3), minimum_gap_bhd numeric(12,3),
  is_test boolean not null default false, paid_at timestamptz, returned_bhd numeric(12,3));
create table shop_order_lines (id bigint generated by default as identity primary key,
  order_id bigint not null references shop_orders(id), item_code text not null, display_name text, spec text,
  image_url text, qty integer not null, qty_confirmed integer, list_price_bhd numeric(12,3),
  unit_price_bhd numeric(12,3) not null, unit_price_confirmed numeric(12,3), discount_bhd numeric(12,3) not null default 0,
  line_total_bhd numeric(12,3) not null, line_total_confirmed numeric(12,3), line_status text, stock_status text,
  backorder boolean not null default false, rule_ids jsonb, note text);
create table shop_order_events (id bigint generated by default as identity primary key, order_id bigint not null
  references shop_orders(id), ts timestamptz default now(), actor text, event text not null, detail jsonb);
create table shop_events (id bigint generated by default as identity primary key, ts timestamptz default now(),
  session_id text, event text not null, item_code text, referral_code text, salesman_id bigint, src text, ua text,
  ip_hash text, device_id text, customer_id bigint, meta jsonb);
create table shop_order_focus_links (id bigint generated always as identity primary key,
  order_id bigint not null references shop_orders(id), invoice_key text not null, sio_key text, method text not null,
  confidence numeric(4,3), allocated_bhd numeric(12,3), state text not null default 'suggested', note text,
  created_by text, created_at timestamptz not null default now(), decided_by text, decided_at timestamptz);
create table salesman_kickback_statements (id bigint generated always as identity primary key, salesman text not null,
  salesman_id bigint, period text not null, basis text not null, status text not null, data_through date,
  sales_bhd numeric(12,3) not null, returns_bhd numeric(12,3), tier_reached int not null, rate numeric(6,4) not null,
  kickback_bhd numeric(12,3) not null, target_snapshot jsonb not null, note text, created_by text not null,
  created_at timestamptz not null default now(), approved_by text, approved_at timestamptz, paid_at timestamptz,
  paid_by text, superseded_at timestamptz, superseded_by text, superseded_reason text, superseded_by_id bigint);
create table shop_restock_requests (id bigint generated always as identity primary key, item_code text, phone text,
  device_id text, referral_code text, created_at timestamptz default now(), notified_at timestamptz,
  upcoming_id bigint, qty_interest integer);
create table product_finds (id bigint generated always as identity primary key, name text, price_bhd numeric,
  currency text, note text, category text, source text, image_path text, status text, promoted_item_code text,
  source_file text, posted_by text, posted_at timestamptz, reviewed_by text, updated_at timestamptz);
create table field_notes (id bigint generated always as identity primary key, note text, category text,
  created_by text, created_at timestamptz default now(), image_path text);
create table stock_balance (id bigint generated always as identity primary key, item_name text, warehouse_name text,
  net_qty numeric, selling_rate_bhd numeric, total_value_bhd numeric, as_of_date date, source_file text,
  imported_at timestamptz default now());
create table ar_ageing_totals (id bigint generated always as identity primary key, as_of_date date,
  focus_total_bhd numeric, focus_over90_bhd numeric, rows_total_bhd numeric, source_file text,
  imported_at timestamptz default now());
create table mrn_landed_costs (id bigint generated always as identity primary key, sku_code text,
  landed_cost_bhd numeric, last_qty numeric, doc_no text, effective_date date, created_at timestamptz default now(),
  product_cost_bhd numeric);
create table ingest_runs (id bigint generated always as identity primary key, started_at timestamptz default now(),
  finished_at timestamptz, status text, file text, rows_in int, rows_loaded int, join_match_pct numeric, errors text);
create table audit_log (id bigint generated always as identity primary key, ts timestamptz default now(),
  user_email text, event text, question text, sql_used text, detail jsonb);
create table customer_contacts (customer_name text primary key, phone text, email text);
create table categories (id bigint generated by default as identity primary key, name text, division text);
create table products (id bigint generated by default as identity primary key, sku_code text unique not null,
  item_name text, category_id bigint references categories(id));
create table product_aliases (id bigint generated by default as identity primary key,
  product_id bigint references products(id), alias_text text unique not null);
create table orders (id bigint generated by default as identity primary key, invoice_no text unique not null,
  order_date date, customer_name text, salesman text, payment_mode text, sales_account_name text);
create table order_lines (id bigint generated by default as identity primary key, invoice_no text not null,
  order_id bigint references orders(id), line_no int, line_date date, customer_account text, item_name text,
  quantity numeric, rate_bhd numeric, gross_bhd numeric, discount_bhd numeric, taxable_bhd numeric,
  vat_amount_bhd numeric, total_amount_bhd numeric, warehouse_name text, narration text, unique (invoice_no, line_no));
create table salesman_channels (salesman text primary key, channel text);
grant usage on schema public to yq_readonly;
grant select on customer_contacts to yq_readonly;
"""


def _extract_view(path: str, name: str) -> str:
    sql = (ROOT / "scripts" / path).read_text(encoding="utf-8")
    m = re.search(rf"create or replace view {name} as\n.*?;\n", sql, re.S | re.I)
    assert m, (path, name)
    return m.group(0)


def _replay_schema() -> str:
    return (SCHEMA + _extract_view("division_payment_migration.sql", "v_sales")
            + _extract_view("marketplace_migration.sql", "v_customer_regulars")
            + _extract_view("catalog_velocity_v2_migration.sql", "v_catalog_velocity")
            + _extract_view("shop_migration.sql", "v_catalog_cost")
            + """create or replace view v_catalog_stock as
                 select p.sku_code as item_code, sum(sb.net_qty) as stock_qty, 'alias'::text as match_source,
                        max(sb.as_of_date) as as_of_date
                   from stock_balance sb join product_aliases pa on pa.alias_text = sb.item_name
                   join products p on p.id = pa.product_id
                  where sb.as_of_date = (select max(as_of_date) from stock_balance)
                  group by 1;\n""")


def _seed(cur) -> None:
    """Made-up reps, shops, SKUs and money. One phone in every free-text column the views mask."""
    cur.execute("""insert into salesmen (id, name, phone, email, user_email, focus_name, referral_code) values
        (1, 'Rep K', '+973 3300 0001', 'repk@example.com', 'repk@example.com', 'Rep K', 'rep-k'),
        (2, 'Rep F', '+973 3300 0002', null, null, 'Rep F', 'rep-f')""")
    cur.execute("""insert into categories (id, name, division) values (1, 'CABLES', 'Accessories'), (2, 'SIM', 'SIM')""")
    for i, sku in enumerate(["SKU-A", "SKU-B", "SKU-C"], 1):
        cur.execute("insert into products (id, sku_code, item_name, category_id) values (%s, %s, %s, 1)",
                    (i, sku, f"{sku} item"))
        cur.execute("insert into product_aliases (product_id, alias_text) values (%s, %s)", (i, f"{sku} Synthetic"))
        cur.execute("""insert into catalog_items (item_code, display_name, category, division, dealer_price, is_active)
                       values (%s, %s, 'CABLES', 'Accessories', %s, true)""", (sku, f"{sku} item", 3.3 * i))
    cur.execute("""insert into stock_balance (item_name, warehouse_name, net_qty, as_of_date) values
        ('SKU-A Synthetic', 'Main', 0, '2026-09-24'), ('SKU-B Synthetic', 'Main', 12, '2026-09-24')""")
    cur.execute("""insert into mrn_landed_costs (sku_code, landed_cost_bhd, effective_date) values
        ('SKU-A', 0.8, '2026-06-01'), ('SKU-A', 1.0, '2026-09-10'), ('SKU-B', 2.0, '2026-08-01')""")
    cur.execute("""insert into ingest_runs (finished_at, status, join_match_pct) values
        ('2026-09-25 08:00+03', 'ok', 96.5)""")
    orders = [
        # id, no, status, rep, shop, total, created, confirmed, delivered, test
        (101, "YQ-2609-0101", "delivered", 1, "Shop One", "12.000", "2026-09-21 10:00+03", "2026-09-21 12:00+03",
         "2026-09-22 10:00+03", False),
        (102, "YQ-2609-0102", "new", 2, "Shop Two 3312 3456", "8.500", "2026-09-22 11:00+03", None, None, False),
        (103, "YQ-2609-0103", "cancelled", 1, "Shop One", "4.000", "2026-09-23 09:00+03", None, None, False),
        (104, "YQ-2609-0104", "confirmed", 2, "Shop Four", "20.000", "2026-09-24 09:30+03", "2026-09-24 20:00+03",
         None, False),
        (105, "YQ-2609-0105", "new", 1, "Test shop", "1.000", "2026-09-24 10:00+03", None, None, True),
        (106, "YQ-2609-0106", "delivered", 1, "Shop Six", "6.000", "2026-09-15 10:00+03", "2026-09-15 11:00+03",
         "2026-09-16 10:00+03", False),
    ]
    for oid, no, st, rep, shop, total, created, conf, deliv, test in orders:
        cur.execute("""insert into shop_orders (id, order_no, token, status, customer_name, customer_phone, customer_email,
                         customer_shop, customer_area, salesman_id, salesman_name, subtotal_bhd, total_bhd, created_at,
                         confirmed_at, delivered_at, cancelled_at, is_test, order_kind, customer_id, note, device_id,
                         ip_hash, ua)
                       values (%s, %s, %s, %s, 'Person Name', '+973 3399 9999', 'person@example.com', %s, 'Area', %s,
                               %s, %s, %s, %s, %s, %s, case when %s = 'cancelled' then %s::timestamptz end, %s,
                               'standard', %s, 'call me on 3399 9999', 'dev-raw', 'iphash', 'agent')""",
                    (oid, no, f"tok-{oid}-xxxxxxxxxxxxxxxx", st, shop, rep, "Rep K" if rep == 1 else "Rep F",
                     total, total, created, conf, deliv, st, created, test, oid + 1000))
    lines = [(101, "SKU-A", 4, "3.000"), (102, "SKU-B", 1, "8.500"), (103, "SKU-A", 1, "4.000"),
             (104, "SKU-C", 10, "2.000"), (105, "SKU-A", 1, "1.000"), (106, "SKU-B", 2, "3.000")]
    for oid, sku, qty, price in lines:
        cur.execute("""insert into shop_order_lines (order_id, item_code, display_name, qty, unit_price_bhd, line_total_bhd,
                         line_status, note) values (%s, %s, %s, %s, %s, %s, 'ok', 'phone 3399 9999')""",
                    (oid, sku, f"{sku} item", qty, price, round(qty * float(price), 3)))
    cur.execute("""insert into shop_order_events (order_id, ts, actor, event) values
        (101, '2026-09-21 12:00+03', 'repk@example.com', 'status:confirmed'),
        (101, '2026-09-22 10:00+03', 'repk@example.com', 'status:delivered')""")
    cur.execute("""insert into shop_order_focus_links (order_id, invoice_key, method, state, confidence, note, created_by,
                     decided_by, decided_at) values (101, 'SI-T-900', 'auto_items', 'confirmed', 0.9, 'x',
                     'boss@example.com', 'boss@example.com', '2026-09-24 10:00+03')""")
    n = 0
    for day, dev, evs, ref in (("2026-09-21", "dev-1", ["view", "item", "add", "checkout_start", "order"], "rep-k"),
                               ("2026-09-21", "dev-2", ["view", "search", "vitals"], None),
                               ("2026-09-22", "dev-3", ["view", "item", "search_zero"], None),
                               ("2026-09-24", "dev-1", ["view", "add", "checkout_start", "order"], "rep-k")):
        for ev in evs:
            n += 1
            meta = None
            if ev == "search":
                meta = json.dumps({"q": "Power Bank", "results": 3})
            elif ev == "search_zero":
                meta = json.dumps({"q": "call 3312 3456", "results": 0})
            cur.execute("""insert into shop_events (ts, session_id, device_id, event, referral_code, meta, ip_hash, ua)
                           values (%s::timestamptz + %s * interval '1 minute', %s, %s, %s, %s, %s::jsonb, 'ip', 'ua')""",
                        (f"{day} 09:00+03", n, "sess-" + dev, dev, ev, ref, meta))
    cur.execute("""insert into shop_restock_requests (item_code, phone, device_id, qty_interest, created_at) values
        ('SKU-A', '+973 3388 8888', 'dev-9', 5, now() - interval '2 days')""")
    cur.execute("""insert into product_finds (name, price_bhd, category, status, posted_by, posted_at) values
        ('Blue case, seller 3377 7777', 1.5, 'CASES', 'new', 'rep@example.com', '2026-09-22 10:00+03')""")
    cur.execute("""insert into field_notes (note, category, created_by, created_at) values
        ('Shop wants magsafe; owner on 3366 6666 or owner@example.com', 'request', 'rep@example.com', '2026-09-23 10:00+03')""")
    cur.execute("""insert into salesman_kickback_statements (salesman, salesman_id, period, basis, status, data_through,
                     sales_bhd, tier_reached, rate, kickback_bhd, target_snapshot, note, created_by, approved_by)
                   values ('Rep K', 1, '2026-09', 'net_ex_vat', 'draft', '2026-09-24', 100, 1, 0.01, 1,
                           '{"target_bhd": 500, "tier2_bhd": 700, "tier3_bhd": 900, "team": "B2B", "updated_by": "boss@example.com"}',
                           'note', 'boss@example.com', null)""")
    cur.execute("""insert into audit_log (ts, user_email, event, detail) values
        (now() - interval '1 day', 'repk@example.com', 'followup.tap', '{"salesman_id": 1, "shop": "Shop One"}')""")
    cur.execute("insert into customer_contacts values ('Focus Shop One', '+973 3355 5555', 'shop@example.com')")
    # Focus: 13 weeks of invoices for two shops + a cash counter; the delivered order 0101 lands on SI-T-900
    inv = 0
    for wk in range(13):
        for cust, rep, amt, sku, dow in (("Focus Shop One", "Rep K - Acc WH", 50, "SKU-A", 1),
                                         ("Focus Shop Two", "Rep F - Acc WH", 30, "SKU-B", 3),
                                         ("Cash Customer", "Rep K - Acc WH", 5, "SKU-C", 2)):
            d = date(2026, 9, 20) - timedelta(weeks=12 - wk) + timedelta(days=dow)
            if d > date(2026, 9, 24):
                continue
            inv += 1
            no = f"SI : SI-T-{inv}"
            cur.execute("insert into orders (invoice_no, order_date, customer_name, salesman) values (%s, %s, %s, %s)",
                        (no, d, cust, rep))
            cur.execute("""insert into order_lines (invoice_no, line_no, line_date, item_name, quantity, rate_bhd, gross_bhd,
                             taxable_bhd, warehouse_name, narration) values (%s, 1, %s, %s, 4, %s, %s, %s, %s, %s)""",
                        (no, d, f"{sku} Synthetic", amt / 4, round(amt * 1.1, 3), amt, rep,
                         "ring 3344 4444" if inv == 3 else None))
    cur.execute("insert into orders (invoice_no, order_date, customer_name, salesman) values "
                "('SI : SI-T-900', '2026-09-23', 'Focus Shop Three', 'Rep K - Acc WH')")
    cur.execute("""insert into order_lines (invoice_no, line_no, line_date, item_name, quantity, rate_bhd, gross_bhd,
                     taxable_bhd, warehouse_name, narration)
                   values ('SI : SI-T-900', 1, '2026-09-23', 'SKU-A Synthetic', 4, 2.727, 12.0, 10.909, 'Rep K - Acc WH',
                           'for YQ-2609-0101, shop 3322 2222')""")


def _cmp_summary(m: dict) -> dict:
    imp = m["impact"]
    return {k: m[k] for k in ("n_orders", "n_live", "value", "merchants", "aov", "prev_n", "prev_v", "repeat_merchants",
                              "confirm_median_h", "confirmed_n", "visitors", "sessions", "funnel", "via_link", "direct",
                              "orders_via_link", "days", "top_searches", "zero_searches", "products", "categories",
                              "shops", "reps")} | {
        "status": [(k, sorted(o["order_no"] for o in v)) for k, v in m["status"]],
        "waiting": sorted(w["order_no"] for w in m["waiting"]),
        "small": sorted(o["order_no"] for o in m["small"]),
        "matches": sorted((mt["order"]["order_no"], mt["invoice"]) for mt in imp["matches"].values()),
        "impact": {k: imp[k] for k in ("matched_taxable", "matched_gross", "week_taxable", "week_invoices", "base",
                                       "existing", "new_named", "walk_in", "invoices")}}


@test("local replay: migration twice, ai_head_ro read-only and blind, views masked, pack + compute parity, trigger, reverse (SKIP without the cluster)")
def _():
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:
        print("  SKIP: psycopg not installed")
        return
    try:
        admin = psycopg.connect(LOCAL_DSN, autocommit=True, connect_timeout=2)
    except Exception as e:  # noqa: BLE001
        print(f"  SKIP: local Postgres unreachable ({type(e).__name__})")
        return
    from psycopg.conninfo import make_conninfo
    from scripts import weekly_report as wr
    from scripts.ai_head import load_insights as li
    from scripts.ai_head import pack

    db_dsn = make_conninfo(LOCAL_DSN, dbname=REPLAY_DB)
    ro_dsn = make_conninfo(LOCAL_DSN, dbname=REPLAY_DB, user="ai_head_ro", password=None)
    for role in ("anon", "authenticated", "yq_readonly", "service_role"):
        admin.execute(f"do $$ begin if not exists (select 1 from pg_roles where rolname = '{role}') "
                      f"then create role {role} nologin; end if; end $$")
    admin.execute(f"drop database if exists {REPLAY_DB}")
    admin.execute(f"create database {REPLAY_DB}")
    mig, rev = MIGRATION.read_text(encoding="utf-8"), REVERSE.read_text(encoding="utf-8")
    try:
        with psycopg.connect(db_dsn, autocommit=True) as c:
            c.execute(_replay_schema())
            with c.transaction():
                _seed(c.cursor())
            c.execute(mig)
            c.execute(mig)                                    # idempotent: applied twice, self-check passes twice
            views = {r[0] for r in c.execute("select viewname from pg_views where viewname like 'v_agent_%'")}
            assert views == set(VIEWS)
            assert c.execute("select count(*) from pg_auth_members m join pg_roles r on r.oid = m.member "
                             "where r.rolname = 'ai_head_ro'").fetchone()[0] == 0

        # the login: read-only by default, 15 s, blind to base tables, cannot write even when it asks to
        with psycopg.connect(ro_dsn, row_factory=dict_row) as ro:
            assert ro.execute("show default_transaction_read_only").fetchone()["default_transaction_read_only"] == "on"
            assert ro.execute("show statement_timeout").fetchone()["statement_timeout"] == "15s"
            for v in VIEWS:
                ro.execute(f"select count(*) from {v}")
            for base in ("shop_orders", "customer_contacts", "v_sales", "salesmen", "ai_insights"):
                try:
                    ro.execute(f"select 1 from {base} limit 1")
                    raise AssertionError(f"ai_head_ro read {base}")
                except psycopg.errors.InsufficientPrivilege:
                    ro.rollback()
            ro.rollback()
            ro.execute("set transaction read write")
            try:
                ro.execute("insert into ai_insights (week_ending, section, tag, text, insight_key) "
                           "values ('2026-09-26', 'Sales', 'fact', 'x', 'k')")
                raise AssertionError("ai_head_ro wrote")
            except psycopg.errors.InsufficientPrivilege:
                ro.rollback()
            # nothing in any view looks like a phone or an email; keys are letters
            leaks = []
            for v in VIEWS:
                for row in ro.execute(f"select * from {v}").fetchall():
                    for k, val in row.items():
                        if isinstance(val, str) and (pack.mask_text(val) != val):
                            leaks.append((v, k, val))
            ro.rollback()
            assert not leaks, leaks
            keys = ro.execute("select device_key, session_key from v_agent_shop_events limit 1").fetchone()
            assert re.fullmatch(r"[a-p]{32}", keys["device_key"]) and re.fullmatch(r"[a-p]{32}", keys["session_key"])
            gov = {r["rep"]: r for r in ro.execute("select * from v_agent_rep_governance").fetchall()}
            assert gov["Rep K"]["last_order_action_at"] is not None and gov["Rep K"]["followup_taps_7d"] == 1
            assert gov["Rep F"]["orders_waiting"] == 1 and gov["Rep F"]["has_login"] is False
            dem = {r["term"]: r for r in ro.execute("select * from v_agent_search_demand").fetchall()}
            assert dem["power bank"]["zero_result"] is False and dem["call [number]"]["zero_result"] is True
            st = ro.execute("select * from v_agent_statements").fetchone()
            assert st["target_bhd"] == Decimal("500.000") and "updated_by" not in json.dumps(st, default=str)
            it = {r["item_code"]: r for r in ro.execute("select * from v_agent_items").fetchall()}
            assert it["SKU-A"]["prev_landed_cost_bhd"] == Decimal("0.8") and it["SKU-A"]["restock_requests_30d"] == 1
            tr = {r["source"]: r for r in ro.execute("select * from v_agent_data_trust").fetchall()}
            assert tr["focus_upload"]["metric"] == Decimal("96.5") and tr["focus_sales"]["as_of"] == date(2026, 9, 23)
            ro.rollback()

        # the pack through the login; compute() through the views == compute() through the base tables
        now = datetime(2026, 9, 27, 6, 0, tzinfo=timezone.utc)
        ws, we = date(2026, 9, 20), date(2026, 9, 26)
        with psycopg.connect(ro_dsn, row_factory=dict_row) as ro:
            ro.read_only = True
            db = pack.ReadOnly(ro, "ai_head_ro", "ai_head_ro", [])
            assert db.available() == set(VIEWS)
            d_views, how = pack.load_weekly(db, db.available(), ws, we)
            assert how == "views"
            with tempfile.TemporaryDirectory() as tmp:
                man = pack.build(db, ws, we, Path(tmp) / "p", now=now)
                assert man["views_missing"] == [] and man["connection"]["role"] == "ai_head_ro"
                blob = "".join(p.read_text(encoding="utf-8") for p in (Path(tmp) / "p").iterdir())
                for secret in ("3312 3456", "3399 9999", "3388 8888", "3377 7777", "3366 6666", "3322 2222",
                               "person@example.com", "owner@example.com", "Person Name", "dev-raw", "tok-101"):
                    assert secret not in blob, secret
            ro.rollback()
        old = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = db_dsn
        try:
            with psycopg.connect(db_dsn, row_factory=dict_row) as own:
                own.read_only = True
                d_base, how_b = pack.load_weekly(pack.ReadOnly(own, "owner_fallback", "postgres", []), set(), ws, we)
            assert how_b == "base_tables"
        finally:
            if old is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = old
        a, b = wr.compute(d_views, ws, we, now), wr.compute(d_base, ws, we, now)
        sa, sb = _cmp_summary(a), _cmp_summary(b)
        diff = {k: (sa[k], sb[k]) for k in sa if sa[k] != sb[k]}
        assert not diff, diff
        assert sa["matches"] == [("YQ-2609-0101", "SI : SI-T-900")], sa["matches"]
        assert sa["n_orders"] == 4 and sa["visitors"] == 3

        # review (rolled back): a rep's own born-Confirmed order never enters his confirm times, and the lines view
        # carries added_at_stage before r7c (NULL) and after the real r7c migration (the trust gate leaves it out)
        with psycopg.connect(db_dsn, row_factory=dict_row) as own:
            gov0 = {r["rep"]: r for r in own.execute("select * from v_agent_rep_governance").fetchall()}
            own.execute("""insert into shop_orders (id, order_no, token, status, customer_name, customer_phone, salesman_id,
                             salesman_name, source, subtotal_bhd, total_bhd, created_at, confirmed_at, order_kind)
                           values (190, 'YQ-2609-0190', 'tok-190-xxxxxxxxxxxxxxxx', 'confirmed', 'P', '+973 3300 0000', 1,
                                   'Rep K', 'salesman', 5, 5, now() - interval '1 day', now() - interval '1 day',
                                   'standard')""")
            gov1 = {r["rep"]: r for r in own.execute("select * from v_agent_rep_governance").fetchall()}
            for k in ("median_confirm_hours_30d", "max_confirm_hours_30d"):
                assert gov1["Rep K"][k] == gov0["Rep K"][k], (k, gov0["Rep K"][k], gov1["Rep K"][k])
            assert gov1["Rep K"]["confirmed_30d"] == gov0["Rep K"]["confirmed_30d"] + 1, "still an order, confirmed"
            assert own.execute("select confirm_hours from v_agent_shop_orders where order_id = 190").fetchone() == \
                {"confirm_hours": None}
            assert own.execute("select count(*) as n from v_agent_shop_lines "
                               "where added_at_stage is not null or qty_delivered is not null").fetchone()["n"] == 0
            own.execute(R7C_MIGRATION.read_text(encoding="utf-8"))
            own.execute("""insert into shop_order_lines (order_id, item_code, display_name, qty, qty_confirmed, unit_price_bhd,
                             list_price_bhd, line_total_bhd, line_status, added_at_stage)
                           values (104, 'SKU-A', 'SKU-A item', 2, 2, 3.0, 3.0, 6.0, 'added', 'confirm')""")
            got = own.execute("select added_at_stage, qty_delivered from v_agent_shop_lines where order_id = 104 "
                              "order by line_id").fetchall()
            assert got == [{"added_at_stage": None, "qty_delivered": None},
                           {"added_at_stage": "confirm", "qty_delivered": None}], got
            t = pack.trust_gate([], date(2026, 9, 27),
                                orders=own.execute("select * from v_agent_shop_orders where order_id = 104").fetchall(),
                                lines=own.execute("select * from v_agent_shop_lines where order_id = 104").fetchall())
            assert {c["check"]: c["status"] for c in t["checks"]}["order_lines_sum"] == "ok", t["checks"]
            own.rollback()

        # ai_insights: load, idempotent, a status move, the words immutable, a final status stays final
        doc = li.decide(li.validate(_doc()), approve={"A1"}, reject={"A2"}, approve_report=True, by="owner@example.com")
        with psycopg.connect(db_dsn) as own:
            assert li.commit(own, doc, "f", "owner@example.com")["inserted"] == 4
            own.commit()
            assert li.commit(own, doc, "f", "owner@example.com") == {"inserted": 0, "status_moved": 0, "unchanged": 4}
            own.commit()
            for bad in ("update ai_insights set text = 'changed' where tag = 'fact'",
                        "update ai_insights set status = 'approved' where status = 'rejected'",
                        "update ai_insights set status = 'proposed', decided_at = null, decided_by = null where status = 'approved'"):
                try:
                    own.execute(bad)
                    raise AssertionError("allowed: " + bad)
                except (psycopg.errors.RaiseException, psycopg.errors.CheckViolation):
                    own.rollback()
            own.execute("update ai_insights set status = 'done', decided_at = now() where status = 'approved' and tag = 'recommendation'")
            own.commit()
        with psycopg.connect(ro_dsn, row_factory=dict_row) as ro:
            rows = ro.execute("select * from v_agent_insights order by insight_id").fetchall()
            assert [r["status"] for r in rows] == ["done", "rejected", "approved", "approved"]
            assert "decided_by" not in rows[0]
            ro.rollback()

        # the reverse: refused with the owner's decisions, then (said so) clean, twice; then the migration again
        with psycopg.connect(db_dsn, autocommit=True) as c:
            try:
                c.execute(rev)
                raise AssertionError("the reverse dropped the owner's decisions")
            except psycopg.errors.RaiseException as e:
                assert "ai_insights has 4 row(s)" in str(e)
            assert c.execute("select to_regclass('public.v_agent_items')").fetchone()[0] is not None
            with c.transaction():
                c.execute("set local yq.ai_insights_drop = 'yes'")
                c.execute(rev)
            c.execute(rev)
            assert c.execute("select count(*) from pg_roles where rolname = 'ai_head_ro'").fetchone()[0] == 0
            assert c.execute("select count(*) from shop_orders").fetchone()[0] == 6, "base rows untouched"
            c.execute(mig)
            c.execute(rev)
    finally:
        admin.execute(f"drop database if exists {REPLAY_DB}")
        admin.execute("do $$ begin if exists (select 1 from pg_roles where rolname = 'ai_head_ro') "
                      "then drop role ai_head_ro; end if; end $$")
        admin.close()


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
