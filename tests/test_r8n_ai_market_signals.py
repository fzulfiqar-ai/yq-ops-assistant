"""R8 — Market Intel v2: the weekly AI Head reads Market Intel (plan Part F1 / F8).

    python -m tests.test_r8n_ai_market_signals

Same lightweight runner as tests/test_r7b_market_intel.py. No database, no network, no .env: SUPABASE_URL,
SUPABASE_KEY, DATABASE_URL and AI_HEAD_DATABASE_URL are pinned to ci.invalid BEFORE any import (a worktree
under the main checkout would otherwise find the real .env through python-dotenv's walk-up), and the AI
pack runs on a stand-in connection and a stand-in photo signer. Every name, phone, price and code is
invented.

Covered:
  * scripts/r8n_ai_market_signals_migration.sql, read as text: v_agent_market_signals keeps the twelve r7b
    columns first (names and order read from r7b_ai_head_migration.sql itself) and appends ten; it reads
    market_items / market_observations / market_photos and never product_finds / field_notes or an r8m
    object; no output column names a shop, a rep, a login, a note, an area, a competitor, a barcode, meta
    or a photo path, and the shop / rep keys are only ever counted; the typed title and brand pass
    ai_agent_mask(); the grants are r7b_ai_head's, as plain statements; no CASCADE, no DROP, no
    security_invoker, no row written; the dependency block and the self-check;
  * every reader of the view (scripts/ai_head/pack.py's two queries) names only columns it keeps;
  * the reverse: r7b's statement and comment verbatim, the same grants, a plain DROP, its own checks;
  * the AI pack (scripts/market_intel_ai_head.py): photo-only sightings are listed for a reading, with
    photo links and their observation IDs, never a people-flagged photo; every row names the sightings to
    cite; a suggestion must cite its own target's sightings and carry no phone number or email (a barcode
    goes under "barcode"); it is stored Unverified (an item: verified = false; a photo-only sighting:
    meta.ai_suggestion while it is still unidentified); a verified or merged item and a sighting
    identified meanwhile are left alone; no statement writes a status, an item_id or a merge; a dry run
    reads only, on a read-only connection;
  * the weekly review command and CI;
  * a local Postgres replay, only when YQ_LOCAL_PG_R8N names a loopback cluster (SKIP when it is not set;
    any other address is refused): r7b's market tables and r7b's view on seeded sightings, this migration
    twice, the rows and counts, nothing personal in any cell, ai_head_ro and yq_readonly read it (and still
    no base table), the pack's own queries, r7b's statement refused after it, the AI pack and its
    suggestions on real tables, the reverse twice (r7b again), the migration again. All rolled back.
"""
from __future__ import annotations

import os

os.environ["SUPABASE_URL"] = "https://ci.invalid"          # before ANY import that could read a .env
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"
os.environ["AI_HEAD_DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"

import ast  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import traceback  # noqa: E402
from contextlib import redirect_stdout  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
from decimal import Decimal  # noqa: E402
from pathlib import Path  # noqa: E402
from types import SimpleNamespace  # noqa: E402

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


MIGRATION = ROOT / "scripts" / "r8n_ai_market_signals_migration.sql"
REVERSE = ROOT / "scripts" / "r8n_ai_market_signals_reverse.sql"
R7B_AI_HEAD = ROOT / "scripts" / "r7b_ai_head_migration.sql"
R7B_MARKET_INTEL = ROOT / "scripts" / "r7b_market_intel_migration.sql"
AI_PACK = ROOT / "scripts" / "market_intel_ai_head.py"
PACK = ROOT / "scripts" / "ai_head" / "pack.py"
WEEKLY = ROOT / ".claude" / "commands" / "weekly-review.md"
CI = ROOT / ".github" / "workflows" / "ci.yml"

R7B_COLUMNS = ["signal", "ref_id", "day", "item_code", "label", "category", "n", "sources", "qty", "price",
               "currency", "status"]
APPENDED = ["kind", "brand", "reps", "photos", "system_signals", "item_shops", "item_reps", "price_max",
            "ai_reading", "observation_ids"]
TYPES = {"signal": "text", "ref_id": "bigint", "day": "date", "item_code": "text", "label": "text",
         "category": "text", "n": "integer", "sources": "integer", "qty": "integer", "price": "numeric",
         "currency": "text", "status": "text", "kind": "text", "brand": "text", "reps": "integer",
         "photos": "integer", "system_signals": "integer", "item_shops": "integer", "item_reps": "integer",
         "price_max": "numeric", "ai_reading": "text", "observation_ids": "bigint[]"}
# a column of this view may never carry one of these (r7b_ai_head's list plus Market Intel's own)
PERSONAL = {"phone", "email", "whatsapp", "shop_name", "shop_customer_id", "salesman_id", "salesman",
            "created_by", "posted_by", "user_email", "note", "area", "competitor", "barcode", "barcode_raw",
            "meta", "path", "bucket", "legacy_ref", "client_uuid", "device_id", "session_id", "token",
            "ip_hash", "ua", "address", "contact_name", "rep", "shop", "customer_name"}
GRANTS = ["revoke all on table v_agent_market_signals from anon, authenticated;",
          "grant select on v_agent_market_signals to ai_head_ro;",
          "grant select on v_agent_market_signals to yq_readonly;"]
F8_LABELS = {"Verified fact", "Unverified report", "AI suggestion", "Recommended action"}


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


def _strip(text: str) -> str:
    """SQL without -- comments (the prose may name what the code must never do)."""
    return "\n".join(ln.split("--", 1)[0] for ln in text.splitlines())


def _sql_code(path: Path) -> str:
    return _strip(path.read_text(encoding="utf-8"))


def _view_stmt(path: Path) -> str:
    """The CREATE OR REPLACE VIEW v_agent_market_signals statement of a file, as written (comments kept)."""
    m = re.search(r"create or replace view v_agent_market_signals as\n.*?;\n", path.read_text(encoding="utf-8"), re.S)
    assert m, path.name
    return m.group(0)


def _view_comment(path: Path) -> str:
    m = re.search(r"comment on view v_agent_market_signals is\n.*?';\n", path.read_text(encoding="utf-8"), re.S)
    assert m, path.name
    return m.group(0)


def _first_branch_columns(stmt: str) -> list[str]:
    """A union view's output names = its first branch's select list, one item per line ('... as name' or a
    bare 'r.item_code')."""
    code = _strip(stmt)
    seg = code[code.index("select 'restock_request'::text"):code.index("\nfrom shop_restock_requests r")]
    cols = []
    for ln in seg.splitlines():
        ln = ln.strip()
        if ln.startswith("select "):
            ln = ln[len("select "):].strip()
        m = re.search(r"\bas\s+(\w+),?$", ln) or re.fullmatch(r"(?:\w+\.)?(\w+),?", ln)
        if m:
            cols.append(m.group(1))
    return cols


def _do_blocks(code: str) -> list[str]:
    return re.findall(r"do \$\$(.*?)\$\$;", code, re.S)


def _outside_do(code: str) -> str:
    return re.sub(r"do \$\$.*?\$\$;", "", code, flags=re.S)


def _grants(code: str) -> list[str]:
    return [" ".join(s.split()) for s in re.findall(r"\b(?:grant|revoke)\s[^;]*;", _outside_do(code))]


# ═══════════════════════════════════════════════════════════════════════════════
# 1. the migration, as text
# ═══════════════════════════════════════════════════════════════════════════════

@test("migration: r7b's twelve columns stay first in r7b's own order, ten are appended, the self-check names all 22")
def _():
    r7b = _first_branch_columns(_view_stmt(R7B_AI_HEAD))
    assert r7b == R7B_COLUMNS, r7b
    mine = _first_branch_columns(_view_stmt(MIGRATION))
    assert mine == R7B_COLUMNS + APPENDED, mine
    assert mine[:12] == r7b, "CREATE OR REPLACE VIEW keeps names and order; it can only append"
    want = re.search(r"want text\[\] := array\[(.*?)\];", _sql_code(MIGRATION), re.S).group(1)
    pairs = re.findall(r"'(\w+) ([\w\[\]]+)'", want)
    assert [p[0] for p in pairs] == mine
    assert dict(pairs) == TYPES, "the r7b union resolves price to plain numeric (product_finds.price_bhd has no typmod)"
    stmt = _strip(_view_stmt(MIGRATION))
    assert re.search(r"null::numeric\s+as price,", stmt) and re.search(r"null::numeric\s+as price_max,", stmt)
    assert "numeric(12,3)" not in stmt, "a typmod on price would refuse CREATE OR REPLACE over the r7b view"


@test("migration: reads market_items / market_observations / market_photos and the restock asks; never the old boards or an r8m object")
def _():
    stmt = _strip(_view_stmt(MIGRATION)).lower()
    for t in ("market_items", "market_observations", "market_photos", "shop_restock_requests"):
        assert re.search(rf"\b(from|join) {t}\b", stmt), t
    for t in ("product_finds", "field_notes", "v_market_clusters", "v_market_signals_agent"):
        assert t not in stmt, t
    code = _sql_code(MIGRATION).lower()
    for r8m in ("market_cases", "market_case_events", "market_issue_types", "supplier", "brands", "brand_id",
                "case_id", "issue_type_id", "safety_flag", "report_basis", "qty_affected", "batch_ref",
                "packaging_version", "client_observed_at", "thumb_path", "blurred_path", "caption", "sha256",
                "evidence_level", "to_jsonb"):
        assert r8m not in code, r8m
    for s in ("'restock_request'", "'market_item'", "'market_report'", "'market_unidentified'"):
        assert s in stmt, s
    # a merged item is left out (its sightings moved to the target); system demand is counted apart
    assert "where o.item_id is null or i.status <> 'merged'" in stmt
    assert stmt.count("b.source <> 'system'") >= 10 and "(count(*) filter (where b.source = 'system'))::int" in stmt
    # the photos a report may count never include one flagged with people
    assert "count(*) filter (where not p.has_people_flag) as usable" in stmt


@test("migration: no output column names a shop, a rep, a login, a note, an area, a competitor, a barcode, meta or a path; shops and reps are only counted")
def _():
    cols = _first_branch_columns(_view_stmt(MIGRATION))
    assert not set(cols) & PERSONAL, set(cols) & PERSONAL
    stmt = _strip(_view_stmt(MIGRATION))
    shop_key = re.findall(r"coalesce\('c' \|\| o\.shop_customer_id::text, 'n' \|\| lower\(nullif\(btrim\(o\.shop_name\), ''\)\)\)\s+as shop_key", stmt)
    rep_key = re.findall(r"case when o\.source = 'rep'\s+then coalesce\('s' \|\| o\.salesman_id::text, "
                         r"'e' \|\| lower\(nullif\(btrim\(o\.created_by\), ''\)\)\) end as rep_key", stmt)
    assert len(shop_key) == 1 and len(rep_key) == 1, "the two counting keys, exactly as v_market_clusters counts"
    rest = stmt.replace(shop_key[0], "").replace(rep_key[0], "")
    metas = re.findall(r"o\.meta -> 'ai_suggestion'", rest)
    assert len(metas) == 2, "meta is read for the AI-reading flag only"
    rest = rest.replace("o.meta -> 'ai_suggestion'", "")
    for col in ("shop_name", "shop_customer_id", "salesman_id", "created_by", "note", "area", "competitor",
                "barcode", "barcode_raw", "path", "bucket", "legacy_ref", "client_uuid", "meta", "phone", "email",
                "user_email", "session_id"):
        assert not re.search(rf"\b{col}\b", rest), col
    assert rest.count("device_id") == 1 and "count(distinct r.device_id)::int" in rest, "devices: counted, as in r7b"
    # the keys only ever feed a count (or a 0/1 for one sighting), never a column
    uses = list(re.finditer(r"\bb\.(shop_key|rep_key)\b", stmt))
    assert len(uses) >= 8, len(uses)
    for m in uses:
        before, after = stmt[max(0, m.start() - 15):m.start()], stmt[m.end():m.end() + 18]
        assert before.endswith("count(distinct ") or after.startswith(" is not null)::int"), (before, after)


@test("migration: the typed title and brand pass ai_agent_mask(); nothing else is free text")
def _():
    stmt = _strip(_view_stmt(MIGRATION))
    union = stmt[stmt.index("select 'restock_request'::text"):]
    found = list(re.finditer(r"\b[ib]\.(title|brand)\b", union))
    assert len(found) == 4, [m.group(0) for m in found]
    for m in found:
        assert union[m.start() - 19:m.start()] == "ai_agent_mask(left(", union[m.start() - 25:m.end() + 8]
    assert union.count("ai_agent_mask(left(") == 4
    # the mask is r7b_ai_head's (an 8+ digit run and an email go); the self-check proves no number survives
    assert "ai_agent_mask(text) is missing" in _sql_code(MIGRATION)
    assert "a label or a brand carries an unmasked number" in _sql_code(MIGRATION)


@test("migration: the grants are r7b_ai_head's (anon / authenticated revoked; SELECT for ai_head_ro and yq_readonly), plain statements, nothing else")
def _():
    code = _sql_code(MIGRATION).lower()
    assert _grants(code) == GRANTS, _grants(code)
    for body in _do_blocks(code):
        assert not re.search(r"\b(grant|revoke)\s", body), "a GRANT to a role is a plain statement, never inside DO"
    r7b = _sql_code(R7B_AI_HEAD).lower()
    ro = re.search(r"grant select on ([^;]*?)\s+to ai_head_ro;", r7b).group(1)
    yq = re.search(r"grant select on ([^;]*?)\s+to yq_readonly;", r7b).group(1)
    rv = re.search(r"revoke all on table (v_agent[^;]*?)\s+from anon, authenticated;", r7b).group(1)
    for lst in (ro, yq, rv):
        assert "v_agent_market_signals" in lst, "r7b_ai_head grants this view the same way"
    assert not re.search(r"grant\s+(yq_readonly|ai_head_ro|postgres|service_role)\s+to\b", code), "no role in a role"


@test("migration: additive and safe (no CASCADE, DROP, security_invoker or written row); dependencies first, a self-check last")
def _():
    code = _sql_code(MIGRATION).lower()
    for bad in ("cascade", "drop ", "delete from", "truncate", "insert into", "update ", "alter table", "alter view",
                "create table", "create role", "alter role", "set role", "password", "security definer"):
        assert bad not in code, bad
    assert not re.search(r"(with|set)\s*\(\s*security_invoker", code), "the view runs as its owner"
    assert code.count("create or replace view") == 1 and "create or replace view v_agent_market_signals as" in code
    blocks = _do_blocks(code)
    assert len(blocks) == 2, len(blocks)
    deps, check = blocks
    for needle in ("'market_items', 'r7b_market_intel_migration.sql'", "'market_observations', 'r7b_market_intel_migration.sql'",
                   "'market_photos', 'r7b_market_intel_migration.sql'", "'v_agent_market_signals', 'r7b_ai_head_migration.sql'",
                   "'shop_restock_requests'", "to_regprocedure('public.ai_agent_mask(text)')", "rolname = 'ai_head_ro'",
                   "rolname = 'yq_readonly'", "apply scripts/% first"):
        assert needle in deps, needle
    for needle in ("must be owned by postgres", "must not be security_invoker", "does not read", "still reads product_finds",
                   "must read v_agent_market_signals", "may write to v_agent_market_signals", "granted to anon/authenticated",
                   "carry a person, a contact or a raw identifier", "the market rows count", "unmasked number"):
        assert needle in check, needle
    assert code.index("do $$") < code.index("create or replace view") < code.index("revoke all on table") \
        < code.rindex("do $$"), "dependencies, the view, the grants, then the self-check"


@test("readers: scripts/ai_head/pack.py's queries on v_agent_market_signals name only columns the view keeps")
def _():
    src = PACK.read_text(encoding="utf-8")
    agent_views = re.search(r"AGENT_VIEWS = \((.*?)\)", src, re.S).group(1)
    assert '"v_agent_market_signals"' in agent_views
    sqls = [n.value for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and "from v_agent_market_signals" in n.value]
    assert len(sqls) == 2, sqls
    cols = set(R7B_COLUMNS)              # the readers predate R8: they must keep working on the r7b names alone
    words = {"select", "from", "where", "and", "between", "order", "by", "desc", "asc", "group", "having", "as",
             "coalesce", "sum", "s", "v_agent_market_signals", "what", "reports"}
    for sql in sqls:
        unknown = {t for t in re.findall(r"[a-z_]+", sql.lower()) if t not in words | cols}
        assert not unknown, (sql, unknown)
    assert any("order by day desc, signal" in s for s in sqls) and any("coalesce(item_code, label)" in s for s in sqls)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. the reverse, as text
# ═══════════════════════════════════════════════════════════════════════════════

@test("reverse: r7b's statement and comment verbatim, the same three grants, a plain DROP (no CASCADE), its own checks")
def _():
    assert _view_stmt(REVERSE) == _view_stmt(R7B_AI_HEAD), "the r7b body, byte for byte"
    assert _view_comment(REVERSE) == _view_comment(R7B_AI_HEAD)
    assert _first_branch_columns(_view_stmt(REVERSE)) == R7B_COLUMNS
    code = _sql_code(REVERSE).lower()
    assert code.count("drop view if exists v_agent_market_signals;") == 1
    for bad in ("cascade", "drop table", "drop function", "drop role", "delete from", "truncate", "update ",
                "insert into", "alter ", "create role", "password"):
        assert bad not in code, bad
    assert _grants(code) == GRANTS, _grants(code)
    top = _outside_do(code)
    assert top.index("drop view if exists v_agent_market_signals;") < top.index("create or replace view v_agent_market_signals as") \
        < top.index("grant select on v_agent_market_signals to ai_head_ro;")
    blocks = _do_blocks(code)
    assert len(blocks) == 2, len(blocks)
    assert "'product_finds', 'field_notes', 'shop_restock_requests'" in blocks[0] and "to_regprocedure" in blocks[0]
    for needle in ("expected the r7b columns", "must be owned by postgres", "must not be security_invoker",
                   "is not the r7b body", "granted to anon/authenticated", "may write to"):
        assert needle in blocks[1], needle
    assert code.index("do $$") < code.index("drop view") < code.rindex("do $$")


# ═══════════════════════════════════════════════════════════════════════════════
# 3. the AI pack: a stand-in connection and photo signer
# ═══════════════════════════════════════════════════════════════════════════════

T0 = datetime(2026, 10, 14, 6, 0, tzinfo=timezone.utc)


class _World:
    """Just enough of the Market Intel tables for scripts/market_intel_ai_head.py: it answers the script's
    own reads and applies its two guarded UPDATEs and two INSERTs, recording every statement. An UPDATE
    without its guard in the WHERE fails the test."""

    def __init__(self):
        self.items = {
            12: {"id": 12, "title": "Brand Q 65W", "status": "new", "verified": False, "ai_suggestion": None, "ai_confidence": None},
            13: {"id": 13, "title": "Brand R cable", "status": "researching", "verified": True,
                 "ai_suggestion": {"brand": "Brand R"}, "ai_confidence": Decimal("0.800")},
            14: {"id": 14, "title": "Brand S", "status": "merged", "verified": False, "ai_suggestion": None, "ai_confidence": None},
            15: {"id": 15, "title": "Brand T", "status": "new", "verified": False, "ai_suggestion": None, "ai_confidence": None},
        }
        self.obs = {o["id"]: o for o in (
            {"id": 101, "item_id": 12, "observed_at": T0 - timedelta(days=2), "meta": None},
            {"id": 102, "item_id": 12, "observed_at": T0 - timedelta(days=1), "meta": None},
            {"id": 103, "item_id": 13, "observed_at": T0 - timedelta(days=3), "meta": None},
            {"id": 104, "item_id": 15, "observed_at": T0 - timedelta(days=20), "meta": None},
            {"id": 230, "item_id": None, "observed_at": T0 - timedelta(hours=5), "meta": {"legacy": "kept"}},
            {"id": 231, "item_id": None, "observed_at": T0 - timedelta(hours=1), "meta": None},
            {"id": 232, "item_id": None, "observed_at": T0, "meta": None},
            {"id": 233, "item_id": None, "observed_at": T0, "meta": None},
            {"id": 234, "item_id": 15, "observed_at": T0, "meta": None},
        )}
        self.photos = [
            {"id": 1, "observation_id": 101, "bucket": "finds", "path": "market/2026/10/p1.webp", "position": 0, "has_people_flag": False},
            {"id": 2, "observation_id": 101, "bucket": "finds", "path": "market/2026/10/people-p2.webp", "position": 1, "has_people_flag": True},
            {"id": 3, "observation_id": 230, "bucket": "finds", "path": "market/2026/10/p3.webp", "position": 0, "has_people_flag": False},
            {"id": 4, "observation_id": 231, "bucket": "finds", "path": "market/2026/10/people-p4.webp", "position": 0, "has_people_flag": True},
            {"id": 5, "observation_id": 231, "bucket": "finds", "path": "market/2026/10/p5.webp", "position": 1, "has_people_flag": False},
        ]
        base = {"row_type": None, "item_id": None, "observation_id": None, "kind": "other", "title": None, "brand": None,
                "competitor": None, "category": None, "barcode": None, "yq_item_code": None, "status": "new", "action": None,
                "observations": 1, "shops": 0, "reps": 1, "price_min": None, "price_max": None, "first_seen": None,
                "last_seen": None, "system_signals": 0, "demand_qty": None, "usable_photos": 0,
                "has_ai_suggestion": False, "ai_confidence": None, "verified": False}
        self.signals = [  # v_market_signals_agent: no rep, shop, note, login or photo path
            {**base, "row_type": "item", "item_id": 12, "kind": "new_product", "title": "Brand Q 65W", "observations": 2,
             "shops": 2, "reps": 2, "price_min": Decimal("2.500"), "last_seen": T0 - timedelta(days=1), "usable_photos": 1},
            {**base, "row_type": "item", "item_id": 15, "title": "Brand T", "shops": 1, "last_seen": T0 - timedelta(days=20)},
            {**base, "row_type": "unassigned", "observation_id": 230, "last_seen": T0 - timedelta(hours=5), "usable_photos": 1},
            {**base, "row_type": "unassigned", "observation_id": 231, "last_seen": T0 - timedelta(hours=1), "usable_photos": 1},
            {**base, "row_type": "unassigned", "observation_id": 232, "yq_item_code": "SKU-C", "last_seen": T0, "usable_photos": 1},
            {**base, "row_type": "unassigned", "observation_id": 233, "last_seen": T0, "usable_photos": 0},
        ]
        self.decisions: list[dict] = []
        self.audit: list[dict] = []
        self.statements: list[tuple[str, object]] = []
        self.read_only: list[bool] = []
        self.commits = 0
        self.rollbacks = 0

    def run(self, sql: str, params):
        s = " ".join(sql.split()).lower()
        self.statements.append((s, params))
        if s.startswith("select * from v_market_signals_agent"):
            return list(self.signals[0]), self.signals, len(self.signals)
        if s.startswith("select id, item_id from market_observations where item_id = any("):
            rows = [o for o in self.obs.values() if o["item_id"] in params[0]]
            if "order by observed_at desc, id desc" in s:
                rows.sort(key=lambda o: (o["observed_at"], o["id"]), reverse=True)
            return ["id", "item_id"], rows, len(rows)
        if s.startswith("select o.id as observation_id, o.item_id, p.bucket, p.path from market_photos p"):
            items, sightings = set(params[0]), set(params[1])
            out = []
            for p in self.photos:
                o = self.obs[p["observation_id"]]
                if not (o["item_id"] in items or o["id"] in sightings):
                    continue
                if "not p.has_people_flag" in s and p["has_people_flag"]:
                    continue
                out.append({"observation_id": o["id"], "item_id": o["item_id"], "bucket": p["bucket"], "path": p["path"],
                            "_k": (o["observed_at"], o["id"], -p["position"], -p["id"])})
            out.sort(key=lambda r: r["_k"], reverse=True)
            return ["observation_id", "item_id", "bucket", "path"], out, len(out)
        if s.startswith("select id, title, status, verified, ai_suggestion, ai_confidence from market_items where id = any("):
            rows = [dict(self.items[i]) for i in params[0] if i in self.items]
            return ["id", "title", "status", "verified", "ai_suggestion", "ai_confidence"], rows, len(rows)
        if s.startswith("select id, item_id, meta from market_observations where id = any("):
            rows = [dict(self.obs[i]) for i in params[0] if i in self.obs]
            return ["id", "item_id", "meta"], rows, len(rows)
        if s.startswith("update market_items set ai_suggestion = %s::jsonb, ai_confidence = %s, verified = false,"):
            assert s.endswith("where id = %s and not verified and status <> 'merged'"), s
            it = self.items.get(params[2])
            if not it or it["verified"] or it["status"] == "merged":
                return None, [], 0
            it.update(ai_suggestion=json.loads(params[0]), ai_confidence=Decimal(str(params[1])), verified=False)
            return None, [], 1
        if s.startswith("insert into market_item_decisions (item_id, event, actor, actor_role, detail)"):
            self.decisions.append({"item_id": params[0], "event": "edit", "actor": "ai-head", "detail": json.loads(params[1])})
            return None, [], 1
        if s.startswith("update market_observations set meta = coalesce(meta, '{}'::jsonb) || jsonb_build_object('ai_suggestion', %s::jsonb)"):
            assert "where id = %s and item_id is null" in s, s
            o = self.obs.get(params[1])
            if not o or o["item_id"] is not None:
                return None, [], 0
            o["meta"] = {**(o["meta"] or {}), "ai_suggestion": json.loads(params[0])}
            return None, [], 1
        if s.startswith("insert into audit_log (user_email, event, detail)"):
            self.audit.append({"event": "market_intel.ai_suggestion", "detail": json.loads(params[0])})
            return None, [], 1
        raise AssertionError("unexpected statement: " + s[:120])


class _Cursor:
    def __init__(self, world: _World):
        self.world, self.description, self.rowcount, self._rows = world, None, -1, []

    def execute(self, sql, params=None):
        cols, rows, count = self.world.run(sql, params)
        self.description = [SimpleNamespace(name=c) for c in cols] if cols is not None else None
        self._rows = [tuple(r.get(c) for c in cols) for r in rows] if cols is not None else []
        self.rowcount = count

    def fetchall(self):
        return list(self._rows)


class _Conn:
    def __init__(self, world: _World, read_only: bool):
        self.world = world
        world.read_only.append(read_only)

    def cursor(self):
        return _Cursor(self.world)

    def commit(self):
        self.world.commits += 1

    def rollback(self):
        self.world.rollbacks += 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _signer(log: list):
    def sign(pairs):
        log.extend(pairs)
        return {p: "https://signed.invalid/" + p[1] for p in pairs}
    return sign


def _run(world: _World, argv: list[str], signed: list | None = None) -> tuple[int, str]:
    from scripts import market_intel_ai_head as ah
    buf = io.StringIO()
    with _Patched((ah, "_connect", lambda read_only: _Conn(world, read_only)),
                  (ah, "_sign", _signer(signed if signed is not None else []))):
        with redirect_stdout(buf):
            rc = ah.main(argv)
    return rc, buf.getvalue()


@test("AI pack: photo-only sightings (no item, no YQ product, a usable photo) are listed for a reading, newest first")
def _():
    from scripts import market_intel_ai_head as ah
    w = _World()
    assert [r["observation_id"] for r in ah.to_identify(w.signals)] == [231, 230], "232 is a YQ product, 233 has no usable photo"
    # the r7b ranking is unchanged: an un-identified sighting still competes on distinct shops x recency
    assert [ah._key(r) for r in ah.rank(w.signals, T0, top=3)][0] == ("item", 12)
    assert set(ah.LABELS) == F8_LABELS


@test("AI pack: every row names the sightings to cite; photo links carry their observation ID, never a people-flagged photo; reads only")
def _():
    w = _World()
    signed: list = []
    with tempfile.TemporaryDirectory() as tmp:
        rc, out = _run(w, ["pack", "--photos", "--top", "1", "--out", tmp], signed)
        assert rc == 0, out
        pack = json.loads((Path(tmp) / "market_intel.json").read_text(encoding="utf-8"))
    assert w.read_only == [True] and w.commits == 0 and w.rollbacks == 1
    assert all(s.startswith("select ") for s, _ in w.statements), [s[:40] for s, _ in w.statements]
    assert [(e["row_type"], e.get("item_id")) for e in pack["items"]] == [("item", 12)]
    item = pack["items"][0]
    assert item["observation_ids"] == [102, 101], "newest first"
    assert item["photos"] == [{"observation_id": 101, "url": "https://signed.invalid/market/2026/10/p1.webp"}]
    assert item["label"] == "Unverified report" and item["corroborated"] is True and item["needs_identification"] is False
    assert [e["observation_id"] for e in pack["to_identify"]] == [231, 230]
    for e in pack["to_identify"]:
        assert e["observation_ids"] == [e["observation_id"]] and e["needs_identification"] is True
        assert e["label"] == "Unverified report" and e["corroborated"] is False
    assert pack["to_identify"][0]["photos"] == [{"observation_id": 231, "url": "https://signed.invalid/market/2026/10/p5.webp"}]
    assert pack["to_identify"][1]["photos"] == [{"observation_id": 230, "url": "https://signed.invalid/market/2026/10/p3.webp"}]
    assert signed and not any("people" in p[1] for p in signed), signed
    assert set(pack["labels"]) == F8_LABELS and "[MI-<id>]" in pack["cite"]
    assert all("observation_ids" in r and r["confidence"] <= 1 for r in pack["suggest_format"])
    assert pack["totals"] == {"clusters": 2, "unidentified": 4, "photo_only": 2}
    assert "1 ranked + 2 to identify" in out
    # without --photos: no link is signed, the citations are still there
    w2, signed2 = _World(), []
    with tempfile.TemporaryDirectory() as tmp:
        assert _run(w2, ["pack", "--top", "1", "--out", tmp], signed2)[0] == 0
        pack2 = json.loads((Path(tmp) / "market_intel.json").read_text(encoding="utf-8"))
    assert signed2 == [] and "photos" not in pack2["items"][0] and pack2["items"][0]["observation_ids"] == [102, 101]


@test("AI pack: a suggestion names one target, cites 1-50 sightings (101 or MI-101), carries no phone or email; r7b's shape still validates")
def _():
    from scripts import market_intel_ai_head as ah
    ok, bad = ah.validate_suggestions([
        {"item_id": 1, "suggestion": {"brand": "Brand X"}, "confidence": 0.6234},         # r7b's shape (cites checked later)
        {"item_id": 2, "observation_ids": [105, "MI-101", 105], "suggestion": {"model": "Q1"}, "confidence": 0.5},
        {"observation_id": 230, "observation_ids": ["mi-230"], "suggestion": {"brand": "Brand Z"}, "confidence": 0.4},
        {"item_id": 3, "observation_id": 230, "suggestion": {"a": 1}, "confidence": 0.5},   # two targets
        {"suggestion": {"a": 1}, "confidence": 0.5},                                        # no target
        {"observation_id": "x", "suggestion": {"a": 1}, "confidence": 0.5},
        {"item_id": 4, "observation_ids": [], "suggestion": {"a": 1}, "confidence": 0.5},
        {"item_id": 5, "observation_ids": [0], "suggestion": {"a": 1}, "confidence": 0.5},
        {"item_id": 6, "observation_ids": [True], "suggestion": {"a": 1}, "confidence": 0.5},
        {"item_id": 7, "observation_ids": "101", "suggestion": {"a": 1}, "confidence": 0.5},
        {"item_id": 8, "observation_ids": list(range(1, 52)), "suggestion": {"a": 1}, "confidence": 0.5},
        {"item_id": 9, "observation_ids": [1], "suggestion": {"sightings": ["MI-1"]}, "confidence": 0.5},
        {"item_id": 10, "observation_ids": [1], "suggestion": {"a": 1}, "confidence": 1.2},
        # no phone number or email in a reading (the shared rule); a barcode is a product code, under "barcode"
        {"item_id": 21, "observation_ids": [1], "suggestion": {"visible_text": "call 3300 0001"}, "confidence": 0.5},
        {"item_id": 22, "observation_ids": [1], "suggestion": {"visible_text": "zed@example.com"}, "confidence": 0.5},
        {"item_id": 23, "observation_ids": [1], "suggestion": {"model": "6971234567890"}, "confidence": 0.5},
        {"item_id": 24, "observation_ids": [1], "suggestion": {"specs": ["65W", "33000001"]}, "confidence": 0.5},
        {"item_id": 25, "observation_ids": [1], "suggestion": {"barcode": "6971234567890", "brand": "Brand Q"}, "confidence": 0.5},
        {"item_id": 26, "observation_ids": [1], "suggestion": {"barcode": 6971234567890}, "confidence": 0.5},
    ])
    assert ok == [{"item_id": 1, "suggestion": {"brand": "Brand X"}, "confidence": 0.623},
                  {"item_id": 2, "suggestion": {"model": "Q1"}, "confidence": 0.5, "observation_ids": [101, 105]},
                  {"observation_id": 230, "suggestion": {"brand": "Brand Z"}, "confidence": 0.4, "observation_ids": [230]},
                  {"item_id": 25, "suggestion": {"barcode": "6971234567890", "brand": "Brand Q"}, "confidence": 0.5,
                   "observation_ids": [1]},
                  {"item_id": 26, "suggestion": {"barcode": 6971234567890}, "confidence": 0.5, "observation_ids": [1]}], ok
    assert len(bad) == 14, bad
    assert any("name one target" in b for b in bad) and any("'sightings' is filled from observation_ids" in b for b in bad)
    assert sum("carries a phone number or an email address" in b for b in bad) == 4, bad
    # the evidence rule: at least one sighting, only the target's own
    row = {"item_id": 12, "observation_ids": [101, 105], "suggestion": {"brand": "Brand Q"}, "confidence": 0.62}
    assert ah.cite_problem(row, {101, 105, 106}, "item 12") is None
    assert "cites MI-230, not one of its own sightings" in ah.cite_problem({**row, "observation_ids": [101, 230]}, {101, 105}, "item 12")
    assert "cites no sighting" in ah.cite_problem({"item_id": 12, "suggestion": {"a": 1}, "confidence": 0.5}, {101}, "item 12")
    # stored with its citations, and as Unverified on a photo-only sighting
    assert ah.stored_suggestion(row) == {"brand": "Brand Q", "sightings": ["MI-101", "MI-105"]}
    r = ah.sighting_reading({"observation_id": 230, "observation_ids": [230], "suggestion": {"brand": "Brand Z"},
                             "confidence": 0.4}, "2026-10-14T06:00:00+00:00")
    assert r == {"suggestion": {"brand": "Brand Z", "sightings": ["MI-230"]}, "confidence": 0.4, "verified": False,
                 "label": "Unverified", "by": "ai-head", "at": "2026-10-14T06:00:00+00:00"}


def _suggestions(tmp: str) -> str:
    rows = [
        {"item_id": 12, "observation_ids": [101, "MI-102"], "suggestion": {"brand": "Brand Q", "model": "Q65"}, "confidence": 0.7},
        {"observation_id": 230, "observation_ids": [230], "suggestion": {"brand": "Brand Z"}, "confidence": 0.4},
        {"item_id": 12, "observation_ids": [230], "suggestion": {"brand": "Brand Q"}, "confidence": 0.5},   # not its sighting
        {"item_id": 13, "observation_ids": [103], "suggestion": {"brand": "Brand R"}, "confidence": 0.9},   # a person verified
        {"item_id": 14, "observation_ids": [1], "suggestion": {"brand": "Brand S"}, "confidence": 0.9},     # merged
        {"item_id": 15, "suggestion": {"brand": "Brand T"}, "confidence": 0.9},                             # cites nothing
        {"observation_id": 231, "observation_ids": [231, 101], "suggestion": {"brand": "Brand Y"}, "confidence": 0.3},
        {"observation_id": 234, "observation_ids": [234], "suggestion": {"brand": "Brand W"}, "confidence": 0.3},  # identified
        {"observation_id": 999, "observation_ids": [999], "suggestion": {"brand": "Brand V"}, "confidence": 0.3},
        {"item_id": 77, "observation_ids": [1], "suggestion": {"brand": "Brand U"}, "confidence": 0.3},
    ]
    path = Path(tmp) / "market_suggestions.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return str(path)


@test("AI pack: suggest is a dry run on a read-only connection by default; it says why each refused row is left alone")
def _():
    w = _World()
    before = json.dumps([w.items, {k: v["meta"] for k, v in w.obs.items()}], default=str)
    with tempfile.TemporaryDirectory() as tmp:
        rc, out = _run(w, ["suggest", _suggestions(tmp)])
    assert rc == 0, out
    assert w.read_only == [True], "the dry run connects read-only"
    assert w.commits == 0 and w.rollbacks == 1
    assert all(s.startswith("select ") for s, _ in w.statements)
    assert json.dumps([w.items, {k: v["meta"] for k, v in w.obs.items()}], default=str) == before
    for needle in ("item 12: cites MI-230, not one of its own sightings", "item 13 (Brand R cable) was verified by a person",
                   "item 14 was merged", "item 15: cites no sighting", "sighting MI-231: cites MI-101, not one of its own",
                   "sighting MI-234 was identified meanwhile (item 15)", "sighting MI-999 does not exist",
                   "item 77 does not exist", "item 12: confidence 0.70, citing MI-101, MI-102",
                   "sighting MI-230: confidence 0.40, citing MI-230", "Dry run: 2 suggestion(s) would be written as Unverified"):
        assert needle in out, (needle, out)


@test("AI pack: --apply stores each reading Unverified with its sightings; never a status, an item_id or a merge; a re-run writes nothing")
def _():
    w = _World()
    with tempfile.TemporaryDirectory() as tmp:
        path = _suggestions(tmp)
        rc, out = _run(w, ["suggest", path, "--apply"])
        assert rc == 0, out
        assert w.read_only == [False] and w.commits == 1
        it = w.items[12]
        assert it["ai_suggestion"] == {"brand": "Brand Q", "model": "Q65", "sightings": ["MI-101", "MI-102"]}
        assert it["ai_confidence"] == Decimal("0.7") and it["verified"] is False and it["status"] == "new"
        assert w.items[13]["ai_suggestion"] == {"brand": "Brand R"} and w.items[13]["verified"] is True
        assert w.decisions == [{"item_id": 12, "event": "edit", "actor": "ai-head",
                                "detail": {"ai_suggestion": {"to": "Unverified", "confidence": 0.7, "sightings": [101, 102]}}}]
        o = w.obs[230]
        assert o["item_id"] is None and o["meta"]["legacy"] == "kept", "the office identifies; meta is merged, not replaced"
        reading = o["meta"]["ai_suggestion"]
        assert reading["suggestion"] == {"brand": "Brand Z", "sightings": ["MI-230"]} and reading["confidence"] == 0.4
        assert reading["verified"] is False and reading["label"] == "Unverified" and reading["by"] == "ai-head"
        assert w.obs[231]["meta"] is None and w.obs[234]["meta"] is None
        assert w.audit == [{"event": "market_intel.ai_suggestion",
                            "detail": {"observation_id": 230, "to": "Unverified", "confidence": 0.4, "sightings": [230]}}]
        writes = [s for s, _ in w.statements if not s.startswith("select ")]
        assert len(writes) == 4, writes
        for s in writes:
            assert s.split(" where ")[0].count("status") == 0 and "item_id =" not in s.split(" where ")[0], s
            assert "merged_into" not in s and "verified = true" not in s and "delete" not in s, s
        assert "wrote 2 suggestion(s), all Unverified" in out
        # the same file again: both already carry it, nothing is written
        n = len(w.statements)
        rc, out = _run(w, ["suggest", path, "--apply"])
        assert rc == 0 and "unchanged: item 12 already carries this suggestion" in out
        assert "unchanged: sighting MI-230 already carries this suggestion" in out
        assert all(s.startswith("select ") for s, _ in w.statements[n:]) and "wrote 0 suggestion(s)" in out
    # a file that holds nothing valid writes nothing and says so
    w3 = _World()
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "bad.json"
        p.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
        rc, out = _run(w3, ["suggest", str(p), "--apply"])
    assert rc == 1 and "nothing to write" in out and w3.statements == [] and w3.read_only == []


@test("AI pack (source): every UPDATE is guarded and sets only the reading; inserts only the history; people-flagged photos never signed")
def _():
    src = AI_PACK.read_text(encoding="utf-8")
    sqls = [" ".join(n.value.split()).lower() for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and re.match(r"\s*(select|update|insert|delete|with)\b", n.value, re.I)]
    updates = sorted(s for s in sqls if s.startswith("update "))
    assert updates == [
        "update market_items set ai_suggestion = %s::jsonb, ai_confidence = %s, verified = false, updated_at = now(), "
        "updated_by = 'ai-head' where id = %s and not verified and status <> 'merged'",
        "update market_observations set meta = coalesce(meta, '{}'::jsonb) || jsonb_build_object('ai_suggestion', %s::jsonb) "
        "where id = %s and item_id is null and (meta is null or jsonb_typeof(meta) = 'object')"], updates
    inserts = sorted(s.split("(")[0].strip() for s in sqls if s.startswith("insert "))
    assert inserts == ["insert into audit_log", "insert into market_item_decisions"], inserts
    assert not any(s.startswith("delete") for s in sqls)
    low = src.lower()
    assert "verified = false" in low and "and not verified" in low and "update market_items set status" not in low
    assert "not p.has_people_flag" in low and "conn.read_only = true" in low
    assert "read_only=not args.apply" in src, "the dry run connects read-only"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. the weekly review command and CI
# ═══════════════════════════════════════════════════════════════════════════════

@test("weekly review: market_signals.csv is Market Intel now; photo readings are drafted, dry-run, and stored only after the owner's yes")
def _():
    w = WEEKLY.read_text(encoding="utf-8")
    assert "product finds, field notes" not in w and "Field reports, product finds" not in w
    for needle in ("v_agent_market_signals", "market_item", "market_report", "market_unidentified", "observation_ids",
                   "[MI-", "system_signals", "python -m scripts.market_intel_ai_head pack --photos", "to_identify",
                   "suggest_format", "market_suggestions.json", "Verified fact", "Unverified report", "AI suggestion",
                   "Recommended action", "never tagged FACT", "corroborate"):
        assert needle in w, needle
    step15 = w.index("## Step 15")
    dry = w.index("python -m scripts.market_intel_ai_head suggest exports/ai_head/<week-ending>/market_suggestions.json\n")
    assert w.index("## Step 0") < dry < w.index("## Step 13"), "the dry run sits in step 9"
    applies = [m.start() for m in re.finditer(r"market_suggestions\.json --apply", w)]
    assert applies and all(p > step15 for p in applies), "the write only in Step 15"
    assert "Only on his yes" in w[step15:] and "never sets a status" in w[step15:]


@test("CI: the api job runs this suite next to the r7b Market Intel suite")
def _():
    ci = CI.read_text(encoding="utf-8")
    api = ci[ci.index("\n  api:"):ci.index("\n  web:")]
    assert "python -m tests.test_r8n_ai_market_signals" in api
    assert api.index("python -m tests.test_r7b_market_intel") < api.index("python -m tests.test_r8n_ai_market_signals")


# ═══════════════════════════════════════════════════════════════════════════════
# 5. local Postgres replay (only on a loopback cluster named by YQ_LOCAL_PG_R8N)
# ═══════════════════════════════════════════════════════════════════════════════

LOOPBACK = {"localhost": "127.0.0.1", "127.0.0.1": "127.0.0.1", "::1": "::1"}


def _local_dsn(env=None) -> tuple[str | None, str | None, str]:
    """(dsn, hostaddr, why): the replay's cluster, from YQ_LOCAL_PG_R8N only, and only on this machine's
    loopback: an explicit host, no service file, no other hostaddr. (None, None, why) otherwise."""
    env = os.environ if env is None else env
    dsn = (env.get("YQ_LOCAL_PG_R8N") or "").strip()
    if not dsn:
        return None, None, "YQ_LOCAL_PG_R8N is not set"
    try:
        from psycopg.conninfo import conninfo_to_dict
        info = conninfo_to_dict(dsn)
    except Exception:  # noqa: BLE001
        return None, None, "YQ_LOCAL_PG_R8N is not a connection string"
    host = str(info.get("host") or "")
    if "service" in info or host not in LOOPBACK or str(info.get("hostaddr") or LOOPBACK[host]) != LOOPBACK[host]:
        return None, None, "YQ_LOCAL_PG_R8N must name this machine's loopback (127.0.0.1, ::1 or localhost)"
    return dsn, LOOPBACK[host], ""


@test("replay guard: only a loopback cluster named by YQ_LOCAL_PG_R8N; nothing set = SKIP, anything else refused")
def _():
    for dsn, ok in (("", None), ("postgresql://postgres@127.0.0.1:55471/x", "127.0.0.1"),
                    ("postgresql://postgres@localhost:55471/x", "127.0.0.1"), ("host=::1 port=55471 dbname=x", "::1"),
                    ("postgresql://postgres@db.example.invalid:5432/postgres", None),
                    ("postgresql://postgres@127.0.0.1:5432/x?hostaddr=10.0.0.5", None), ("service=prod", None),
                    ("postgresql:///x", None), ("not a dsn ===", None)):
        got_dsn, addr, why = _local_dsn({"YQ_LOCAL_PG_R8N": dsn})
        assert addr == ok and (got_dsn is not None) == (ok is not None) and (why == "") == (ok is not None), (dsn, why)
    assert _local_dsn({})[2] == "YQ_LOCAL_PG_R8N is not set"


REPLAY_SCHEMA = """
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then create role service_role nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'yq_readonly') then create role yq_readonly nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'ai_head_ro') then
    create role ai_head_ro login inherit connection limit 3;
  end if;
end $$;
create table salesmen (id bigint generated by default as identity primary key, name text not null,
  referral_code text not null unique, user_email text);
create table shop_customers (id bigint generated by default as identity primary key, phone text not null unique,
  name text, shop text, area text);
create table shop_restock_requests (id bigint generated always as identity primary key, item_code text not null,
  phone text, device_id text, referral_code text, created_at timestamptz not null default now(),
  notified_at timestamptz, qty_interest integer);
create table product_finds (id bigint generated always as identity primary key, name text, price_bhd numeric,
  currency text default 'BHD', note text, category text, status text not null default 'new', promoted_item_code text,
  posted_by text, posted_at timestamptz default now());
create table field_notes (id bigint generated always as identity primary key, note text not null, category text,
  created_by text, created_at timestamptz not null default now());
create table audit_log (id bigint generated always as identity primary key, ts timestamptz default now(),
  user_email text, event text, question text, sql_used text, detail jsonb);
grant usage on schema public to ai_head_ro;
grant all on all tables in schema public to anon, authenticated;
alter default privileges in schema public grant all on tables to anon, authenticated;
alter default privileges in schema public grant all on sequences to anon, authenticated;
"""
R7B_GRANTS = "\n".join(GRANTS) + "\n"


def _r7b_ai_head_part() -> str:
    """What r7b_ai_head_migration.sql gives this view, taken from that file: the mask and its grants, the 3l
    view and its comment, the view's grants."""
    src = R7B_AI_HEAD.read_text(encoding="utf-8")
    mask = re.search(r"create or replace function ai_agent_mask\(t text\).*?\$\$;\n", src, re.S).group(0)
    return (mask + "revoke all on function ai_agent_mask(text) from public, anon, authenticated;\n"
            "grant execute on function ai_agent_mask(text) to ai_head_ro, yq_readonly, service_role;\n"
            + _view_stmt(R7B_AI_HEAD) + _view_comment(R7B_AI_HEAD) + R7B_GRANTS)


def _seed(cur) -> dict:
    """Invented reps, shops and sightings, with a name, a phone and an email in every column the view must
    never show. Returns the ids."""
    ids: dict = {}
    cur.execute("insert into salesmen (name, referral_code, user_email) values "
                "('Rep One', 'r8n-one', 'repone@example.com'), ('Rep Two', 'r8n-two', 'reptwo@example.com') returning id")
    ids["r1"], ids["r2"] = [x[0] for x in cur.fetchall()]
    cur.execute("insert into shop_customers (phone, name, shop, area) values "
                "('97300000001', 'Person Zed', 'Shop Alpha', 'Area Nine') returning id")
    ids["sa"] = cur.fetchone()[0]
    cur.execute("insert into shop_restock_requests (item_code, phone, device_id, created_at, qty_interest) values "
                "('SKU-A', '97300000002', 'dev-1', '2026-10-13 09:00+03', 3), "
                "('SKU-A', null, 'dev-2', '2026-10-13 10:00+03', null), "
                "('SKU-B', null, 'dev-1', '2026-10-14 09:00+03', 1)")
    cur.execute("insert into product_finds (name, price_bhd, category, posted_by, posted_at) values "
                "('Old board find', 1.5, 'CABLE', 'repone@example.com', '2026-07-20 10:00+03')")
    cur.execute("insert into field_notes (note, category, created_by) values ('Old board note', 'demand', 'repone@example.com')")
    cur.execute("insert into market_items (kind, title, brand, category, status, ai_suggestion, ai_confidence) values "
                "('competitor_price', 'Brand Q 65W charger, ask 3300 0001', 'Brand Q', 'CHARGER', 'new', %s::jsonb, 0.5) "
                "returning id", (json.dumps({"brand": "Brand Q"}),))
    ids["A"] = cur.fetchone()[0]
    cur.execute("insert into market_items (kind, title, category, yq_item_code, status) values "
                "('shop_asked', 'usb c hub', 'HUB', 'SKU-A', 'researching') returning id")
    ids["S"] = cur.fetchone()[0]
    cur.execute("insert into market_items (kind, title, status, merged_into) values ('other', 'Dup of Q', 'merged', %s) "
                "returning id", (ids["A"],))
    ids["M"] = cur.fetchone()[0]
    note = "call Person Zed on +973 3300 0001 or zed@example.com"
    rows = [  # name, item, source, signal, kind, rep, login, shop id, shop name, title, competitor, price, qty, yq, legacy, when
        ("o1", ids["A"], "rep", None, "competitor_price", ids["r1"], "repone@example.com", ids["sa"], None, "Brand Q 65W",
         "Shop Beta", "2.5", None, None, None, "2026-10-11 10:00+03"),
        ("o2", ids["A"], "rep", None, "competitor_price", ids["r2"], "reptwo@example.com", None, "Shop Gamma", "Brand Q 65W",
         "Shop Beta", "3.0", None, None, None, "2026-10-13 01:30+03"),
        ("o3", ids["A"], "rep", None, "competitor_price", ids["r2"], "reptwo@example.com", ids["sa"], None, "Brand Q 65W",
         None, "2.75", None, None, None, "2026-10-13 09:00+03"),
        ("o4", ids["A"], "system", "restock_ask", "shop_asked", None, None, None, None, "Brand Q 65W", None, None, 4, None,
         None, "2026-10-13 10:00+03"),
        ("o5", None, "rep", None, "other", ids["r1"], "repone@example.com", None, None, None, None, None, None, None, None,
         "2026-10-14 09:00+03"),
        ("o6", None, "rep", None, "complaint", ids["r1"], "repone@example.com", ids["sa"], None, "stops charging", None, None,
         None, "SKU-C", None, "2026-10-14 10:00+03"),
        ("o7", None, "rep", None, "complaint", ids["r2"], "reptwo@example.com", None, "Shop Delta", None, None, None, None,
         "SKU-C", None, "2026-10-14 11:00+03"),
        ("o8", None, "import", None, "new_product", None, "repone@example.com", None, None, "Old find 3300 0001 99", None,
         "1.5", None, None, "product_finds:1", "2026-07-20 10:00+03"),
        ("o9", ids["M"], "rep", None, "other", ids["r1"], "repone@example.com", None, "Shop Gamma", "Dup of Q", None, None,
         None, None, None, "2026-10-14 12:00+03"),
        ("o10", ids["S"], "system", "search_zero", "shop_asked", None, None, None, None, "usb c hub", None, None, 2, "SKU-A",
         None, "2026-10-14 08:00+03"),
    ]
    for (name, item, source, signal, kind, rep, login, shop_id, shop_name, title, competitor, price, qty, yq, legacy,
         when) in rows:
        cur.execute("""insert into market_observations (item_id, client_uuid, source, signal, kind, salesman_id, created_by,
                         shop_customer_id, shop_name, area, title, brand, competitor, category, note, price_bhd, demand_qty,
                         yq_item_code, legacy_ref, observed_at)
                       values (%s, gen_random_uuid(), %s, %s, %s, %s, %s, %s, %s, 'Area Nine', %s, %s, %s, 'CHARGER', %s, %s, %s,
                               %s, %s, %s) returning id""",
                    (item, source, signal, kind, rep, login, shop_id, shop_name, title,
                     "Brand Q" if item == ids["A"] else None, competitor, note, price, qty, yq, legacy, when))
        ids[name] = cur.fetchone()[0]
    cur.execute("insert into market_photos (observation_id, bucket, path, phash, has_people_flag) values "
                "(%s, 'finds', 'market/2026/10/o1.webp', 5, false), (%s, 'finds', 'market/2026/10/o5.webp', 7, false), "
                "(%s, 'finds', 'market/2026/10/o5-people.webp', 9, true)", (ids["o1"], ids["o5"], ids["o5"]))
    return ids


def _view_rows(cur) -> dict:
    cur.execute("select " + ", ".join(R7B_COLUMNS + APPENDED).replace("day,", "day::text,", 1)
                + " from v_agent_market_signals")
    cols = [d.name for d in cur.description]
    out: dict = {}
    for r in cur.fetchall():
        row = dict(zip(cols, r))
        out[(row["signal"], row["ref_id"], row["day"], row["item_code"])] = row
    return out


class _Nested:
    """The replay's open transaction lent to the AI pack: its commit / rollback become a savepoint's release /
    rollback, and leaving its with-block never closes the replay's connection."""

    def __init__(self, conn, read_only: bool, log: list):
        self.conn = conn
        log.append(read_only)
        conn.execute("savepoint ai_pack")

    def cursor(self):
        return self.conn.cursor()

    def commit(self):
        self.conn.execute("release savepoint ai_pack")
        self.conn.execute("savepoint ai_pack")

    def rollback(self):
        self.conn.execute("rollback to savepoint ai_pack")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@test("local replay: r7b on seeded sightings, r8n twice, rows / counts / privacy / grants, the pack's queries, the AI pack, reverse twice, r8n again (SKIP without YQ_LOCAL_PG_R8N)")
def _():
    dsn, hostaddr, why = _local_dsn()
    if dsn is None:
        if why == "YQ_LOCAL_PG_R8N is not set":
            print("  SKIP: YQ_LOCAL_PG_R8N is not set (a throwaway loopback cluster; see the module docstring)")
            return
        raise AssertionError("refusing to connect: " + why)
    try:
        import psycopg
    except ImportError:
        print("  SKIP: psycopg not installed")
        return
    from scripts import market_intel_ai_head as ah
    conn = psycopg.connect(dsn, hostaddr=hostaddr, connect_timeout=3)
    mig, rev = MIGRATION.read_text(encoding="utf-8"), REVERSE.read_text(encoding="utf-8")
    try:
        cur = conn.cursor()
        cur.execute("select current_user, to_regclass('public.market_items'), to_regclass('public.salesmen')")
        user, has_mi, has_sm = cur.fetchone()
        if user != "postgres" or has_mi is not None or has_sm is not None:
            print(f"  SKIP: the replay needs an empty database and the postgres superuser (views are owned by postgres); "
                  f"got {user}, market_items={has_mi}, salesmen={has_sm}")
            return
        cur.execute(REPLAY_SCHEMA)
        cur.execute(R7B_MARKET_INTEL.read_text(encoding="utf-8"))
        cur.execute(_r7b_ai_head_part())
        ids = _seed(cur)
        # the problem: r7b's view reads the old boards, so not one Market Intel sighting reaches the AI Head
        cur.execute("select signal, count(*) from v_agent_market_signals group by 1 order by 1")
        assert cur.fetchall() == [("field_note", 1), ("product_find", 1), ("restock_request", 2)]
        # a missing dependency stops it before anything changes (scaffolding inside a savepoint)
        cur.execute("savepoint dep")
        cur.execute("drop view v_agent_market_signals")
        try:
            cur.execute(mig)
            raise AssertionError("r8n must refuse without r7b_ai_head's view")
        except psycopg.errors.RaiseException as e:
            assert "v_agent_market_signals is missing: apply scripts/r7b_ai_head_migration.sql first" in str(e), e
            cur.execute("rollback to savepoint dep")
        cur.execute(mig)
        cur.execute(mig)                       # idempotent
        cur.execute("""select a.attname || ' ' || format_type(a.atttypid, a.atttypmod) from pg_attribute a
                        where a.attrelid = 'public.v_agent_market_signals'::regclass and a.attnum > 0 and not a.attisdropped
                        order by a.attnum""")
        assert [r[0] for r in cur.fetchall()] == [f"{c} {TYPES[c]}" for c in R7B_COLUMNS + APPENDED]
        v = _view_rows(cur)
        A, S, o = ids["A"], ids["S"], ids
        a11 = v[("market_item", A, "2026-10-11", None)]
        assert (a11["n"], a11["sources"], a11["reps"], a11["photos"], a11["system_signals"], a11["qty"]) == (1, 1, 1, 1, 0, 0)
        assert (a11["price"], a11["price_max"], a11["currency"], a11["status"], a11["kind"], a11["brand"]) == \
            (Decimal("2.500"), Decimal("2.500"), "BHD", "new", "competitor_price", "Brand Q")
        assert (a11["item_shops"], a11["item_reps"], a11["ai_reading"], a11["observation_ids"]) == (2, 2, "unverified", [o["o1"]])
        assert a11["label"] == "Brand Q 65W charger, ask [number]", a11["label"]
        a13 = v[("market_item", A, "2026-10-13", None)]   # 01:30 Bahrain is still the 12th in UTC: the Bahrain day counts
        assert (a13["n"], a13["sources"], a13["reps"], a13["photos"], a13["system_signals"]) == (2, 2, 1, 0, 1)
        assert (a13["price"], a13["price_max"], a13["observation_ids"]) == (Decimal("2.750"), Decimal("3.000"), [o["o2"], o["o3"]])
        s14 = v[("market_item", S, "2026-10-14", "SKU-A")]
        assert (s14["n"], s14["sources"], s14["system_signals"], s14["observation_ids"], s14["price"], s14["status"]) == \
            (0, 0, 1, [], None, "researching"), s14
        rep = v[("market_report", None, "2026-10-14", "SKU-C")]
        assert (rep["n"], rep["sources"], rep["reps"], rep["kind"], rep["label"], rep["observation_ids"]) == \
            (2, 2, 2, "complaint", None, [o["o6"], o["o7"]])
        u5 = v[("market_unidentified", o["o5"], "2026-10-14", None)]
        assert (u5["n"], u5["sources"], u5["reps"], u5["photos"], u5["observation_ids"], u5["ai_reading"]) == \
            (1, 0, 1, 1, [o["o5"]], None), "the people-flagged photo never counts"
        u8 = v[("market_unidentified", o["o8"], "2026-07-20", None)]
        assert (u8["reps"], u8["label"], u8["price"]) == (0, "Old find [number]", Decimal("1.500"))
        assert not any(k[1] == ids["M"] for k in v), "a merged item has no row"
        assert v[("restock_request", None, "2026-10-13", "SKU-A")]["n"] == 2
        assert v[("restock_request", None, "2026-10-13", "SKU-A")]["qty"] == 3
        assert {k[0] for k in v} == {"restock_request", "market_item", "market_report", "market_unidentified"}
        assert sum(r["n"] for k, r in v.items() if k[0].startswith("market_")) == 7, "every field sighting, the merged one aside"
        # nothing personal in any cell
        cur.execute("select * from v_agent_market_signals")
        cells = json.dumps([list(r) for r in cur.fetchall()], default=str)
        for leak in ("Rep One", "Rep Two", "repone", "reptwo", "Person Zed", "Shop Alpha", "Shop Beta", "Shop Gamma",
                     "Shop Delta", "Area Nine", "zed@", "3300 0001", "97300000001", "97300000002", "dev-1", ".webp",
                     "market/2026", "stops charging", "Old board"):
            assert leak not in cells, leak
        # who may read it: the AI Head's login and the assistant's role, never the browser roles; no base table
        cur.execute("""select count(*) from information_schema.role_table_grants where table_schema = 'public'
                         and table_name = 'v_agent_market_signals' and grantee in ('anon', 'authenticated')""")
        assert cur.fetchone()[0] == 0
        weekly, monthly = sorted((n.value for n in ast.walk(ast.parse(PACK.read_text(encoding="utf-8")))
                                  if isinstance(n, ast.Constant) and isinstance(n.value, str)
                                  and "from v_agent_market_signals" in n.value), key=lambda s: "coalesce" in s)
        for role in ("ai_head_ro", "yq_readonly"):
            cur.execute(f"set local role {role}")
            cur.execute(weekly, (datetime(2026, 9, 16).date(),))
            assert len(cur.fetchall()) == sum(1 for k in v if k[2] >= "2026-09-16")
            cur.execute(monthly, (datetime(2026, 10, 1).date(), datetime(2026, 10, 31).date()))
            got = {(r[0], r[1], int(r[2])) for r in cur.fetchall()}
            assert {("market_item", "Brand Q 65W charger, ask [number]", 3), ("market_report", "SKU-C", 2),
                    ("restock_request", "SKU-A", 2)} <= got, got
            cur.execute("savepoint base")
            try:
                cur.execute("select count(*) from market_observations")
                raise AssertionError(f"{role} must not read market_observations")
            except psycopg.errors.InsufficientPrivilege:
                cur.execute("rollback to savepoint base")
            cur.execute("reset role")
        # re-running r7b's statement after r8n fails loudly (it would put the old boards back)
        cur.execute("savepoint r7b_again")
        try:
            cur.execute(_view_stmt(R7B_AI_HEAD))
            raise AssertionError("r7b's view statement must be refused after r8n")
        except psycopg.errors.InvalidTableDefinition:
            cur.execute("rollback to savepoint r7b_again")
        # the AI pack on real tables: the photo-only sighting gets its photo (never the people one) and its IDs
        signed: list = []
        opened: list = []
        with _Patched((ah, "_connect", lambda read_only: _Nested(conn, read_only, opened)), (ah, "_sign", _signer(signed))):
            with tempfile.TemporaryDirectory() as tmp:
                with redirect_stdout(io.StringIO()):
                    assert ah.main(["pack", "--photos", "--top", "1", "--out", tmp]) == 0
                pack = json.loads((Path(tmp) / "market_intel.json").read_text(encoding="utf-8"))
                entries = {(e["row_type"], e.get("item_id") or e.get("observation_id")): e
                           for e in pack["items"] + pack["to_identify"]}
                e5 = entries[("unassigned", o["o5"])]
                assert e5["observation_ids"] == [o["o5"]] and e5["needs_identification"] is True
                assert e5["photos"] == [{"observation_id": o["o5"], "url": "https://signed.invalid/market/2026/10/o5.webp"}]
                assert not any("people" in p[1] for p in signed)
                if ("item", A) in entries:
                    assert set(entries[("item", A)]["observation_ids"]) == {o["o1"], o["o2"], o["o3"], o["o4"]}
                path = Path(tmp) / "market_suggestions.json"
                path.write_text(json.dumps([
                    {"item_id": A, "observation_ids": [o["o1"], o["o2"]], "suggestion": {"brand": "Brand Q", "model": "Q65"},
                     "confidence": 0.7},
                    {"observation_id": o["o5"], "observation_ids": [o["o5"]], "suggestion": {"brand": "Brand Z"}, "confidence": 0.4},
                    {"item_id": A, "observation_ids": [o["o5"]], "suggestion": {"brand": "Brand Q"}, "confidence": 0.5},
                ]), encoding="utf-8")
                with redirect_stdout(io.StringIO()):
                    assert ah.main(["suggest", str(path)]) == 0                   # dry run
                cur.execute("select ai_suggestion from market_items where id = %s", (A,))
                assert cur.fetchone()[0] == {"brand": "Brand Q"}, "a dry run writes nothing"
                buf = io.StringIO()
                with redirect_stdout(buf):
                    assert ah.main(["suggest", str(path), "--apply"]) == 0
                assert "wrote 2 suggestion(s)" in buf.getvalue() and "cites MI-" in buf.getvalue()
                assert opened == [True, True, False], opened
                cur.execute("select ai_suggestion, ai_confidence, verified, status from market_items where id = %s", (A,))
                assert cur.fetchone() == ({"brand": "Brand Q", "model": "Q65", "sightings": [f"MI-{o['o1']}", f"MI-{o['o2']}"]},
                                          Decimal("0.700"), False, "new")
                cur.execute("select item_id, meta -> 'ai_suggestion' from market_observations where id = %s", (o["o5"],))
                item_id, reading = cur.fetchone()
                assert item_id is None and reading["verified"] is False and reading["label"] == "Unverified"
                assert reading["suggestion"] == {"brand": "Brand Z", "sightings": [f"MI-{o['o5']}"]}
                cur.execute("select event, actor, detail from market_item_decisions where item_id = %s", (A,))
                assert cur.fetchall() == [("edit", "ai-head", {"ai_suggestion": {"to": "Unverified", "confidence": 0.7,
                                                                                  "sightings": [o["o1"], o["o2"]]}})]
                cur.execute("select count(*) from audit_log where user_email = 'ai-head' and event = 'market_intel.ai_suggestion'")
                assert cur.fetchone()[0] == 1
                v = _view_rows(cur)
                assert v[("market_unidentified", o["o5"], "2026-10-14", None)]["ai_reading"] == "unverified"
                # a reviewer verifies the item: the AI never writes over it again
                cur.execute("update market_items set verified = true where id = %s", (A,))
                path.write_text(json.dumps([{"item_id": A, "observation_ids": [o["o1"]], "suggestion": {"brand": "Brand X"},
                                             "confidence": 0.9}]), encoding="utf-8")
                with redirect_stdout(io.StringIO()):
                    assert ah.main(["suggest", str(path), "--apply"]) == 0
                cur.execute("select ai_suggestion ->> 'model' from market_items where id = %s", (A,))
                assert cur.fetchone()[0] == "Q65"
                assert _view_rows(cur)[("market_item", A, "2026-10-11", None)]["ai_reading"] == "verified"
        # the reverse: r7b's view again (twice), with its grants, the browser roles shut out after the re-create
        cur.execute(rev)
        cur.execute(rev)
        cur.execute("""select a.attname::text from pg_attribute a where a.attrelid = 'public.v_agent_market_signals'::regclass
                        and a.attnum > 0 and not a.attisdropped order by a.attnum""")
        assert [r[0] for r in cur.fetchall()] == R7B_COLUMNS
        cur.execute("select signal, count(*) from v_agent_market_signals group by 1 order by 1")
        assert cur.fetchall() == [("field_note", 1), ("product_find", 1), ("restock_request", 2)]
        cur.execute("""select has_table_privilege('ai_head_ro', 'public.v_agent_market_signals', 'SELECT'),
                              has_table_privilege('yq_readonly', 'public.v_agent_market_signals', 'SELECT'),
                              has_table_privilege('anon', 'public.v_agent_market_signals', 'SELECT')""")
        assert cur.fetchone() == (True, True, False)
        cur.execute(mig)                       # and forward again over the r7b body
        assert sum(r["n"] for k, r in _view_rows(cur).items() if k[0].startswith("market_")) == 7
        print(f"  replay: ok on {dsn.rsplit('/', 1)[-1]}")
    finally:
        conn.rollback()
        conn.close()


def main() -> int:
    passed = failed = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"PASS  {name}")
            passed += 1
        except Exception:  # noqa: BLE001
            print(f"FAIL  {name}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
