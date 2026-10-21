"""Release 2b (R9): the stock moves migrations and the stock-ledger narration masked at parse time.

    python -m tests.test_r9_stock_moves

Same lightweight runner as tests/test_r8n_ai_market_signals.py (no pytest). No database, no network, no .env:
SUPABASE_URL, SUPABASE_KEY and DATABASE_URL are pinned to ci.invalid BEFORE any import (a worktree under the main
checkout would otherwise find the real .env). Every name, number and voucher is invented.

Covered:
  * the six files, read as text (comments set aside): no CASCADE, no security_invoker, no COMMIT (so
    `apply_sql --rehearse` can roll each back), every GRANT a plain statement outside any DO block, a revoke from
    anon / authenticated for every object created or re-created, SELECT for yq_readonly on the two views and
    v_transfer_flow only, nothing at all granted on team_messages; every statement outside a DO block parses as
    Postgres (sqlglot, the version the API pins);
  * M1 (r9_stock_moves_migration.sql): v_transfer_lines has the contract's columns over the issue and receive rows of
    the accessory items; v_salesman_stock_recon keeps the eleven old columns first, in the order and the forms of
    stock_transfer_migration.sql, then appends nine; it reads the stock ledger alone (never stock_balance or v_sales);
    the store kinds are StoreRules.kind's five rules on the folded name, in its order, then the old view's
    modern_trade / facility rules, and never a test for "sim" inside a name: the CASE itself is read out of the file
    and run in Python on sample names, against fixed answers and against scripts/ops/pairing.py StoreRules.kind when
    that module is there; salesman keeps its old meaning, the store name exactly as the ledger and v_sales spell it
    (never a suffix strip), equal to the appended warehouse_name; the data step is r7_narration_mask_data_migration.sql's verbatim on stock_movements and the stock copies of the batch
    importer, and never writes row_hash; the self-check's column lists are the ones pinned here;
  * the reverses mirror their migrations: M1's drops v_transfer_lines and is stock_transfer_migration.sql's statement
    verbatim with plain grants, and says the narration cannot come back; M2's is daily_ops2_migration.sql's
    v_transfer_flow verbatim with its grant and the hotfix's revoke; X1's drops the table;
  * M2 refuses while v_transfer_lines is missing, then drops v_transfer_flow without CASCADE;
  * X1 (r9_team_messages_migration.sql): the five columns, the primary key on run, RLS on, no policy;
  * scripts/ingest.py parse_stock: the narration is masked while row_hash is still made from the raw cells (the
    hashes the parser gave these rows before 2b, pinned); the pairing's narration hash is the same either way;
  * a local Postgres replay, only when YQ_LOCAL_PG_R9 names a loopback cluster (SKIP when it is not set; any other
    address is refused), in one transaction rolled back: the old views as production has them, Supabase-style roles
    and default grants, seeded ledger rows; M1 twice (columns and types, every figure of every store, the kinds, the
    mask, row_hash unchanged, the batch copies, the grants, the read role); the reverse twice (the old view answers
    again, the narration stays masked); M1 again; M2 refused without M1's view, then twice, its reverse twice, M2
    again; X1 twice, its reverse twice, X1 again.
"""
from __future__ import annotations

import os

os.environ["SUPABASE_URL"] = "https://ci.invalid"          # before ANY import that could read a .env
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"

import hashlib  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
import traceback  # noqa: E402
from datetime import date, datetime  # noqa: E402
from decimal import Decimal  # noqa: E402
from pathlib import Path  # noqa: E402

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


SCRIPTS = ROOT / "scripts"
M1 = SCRIPTS / "r9_stock_moves_migration.sql"
M1_REVERSE = SCRIPTS / "r9_stock_moves_reverse.sql"
X1 = SCRIPTS / "r9_team_messages_migration.sql"
X1_REVERSE = SCRIPTS / "r9_team_messages_reverse.sql"
M2 = SCRIPTS / "r9_transfer_flow_drop_migration.sql"
M2_REVERSE = SCRIPTS / "r9_transfer_flow_drop_reverse.sql"
R9_FILES = (M1, M1_REVERSE, X1, X1_REVERSE, M2, M2_REVERSE)
OLD_RECON = SCRIPTS / "stock_transfer_migration.sql"
DAILY_OPS2 = SCRIPTS / "daily_ops2_migration.sql"
HOTFIX = SCRIPTS / "hotfix_view_grants_migration.sql"
R7_DATA = SCRIPTS / "r7_narration_mask_data_migration.sql"

OLD_COLUMNS = ["salesman", "warehouse_type", "transferred_in_qty", "transferred_in_value_bhd", "transferred_out_qty",
               "sold_qty", "on_hand_qty", "shortage_qty", "shortage_value_bhd", "unexplained_qty", "is_van"]
APPENDED = ["warehouse_name", "store_kind", "received_qty", "customer_returns_qty", "excess_qty", "pending_qty",
            "items_held", "first_move", "last_move"]
TYPES = {"salesman": "text", "warehouse_type": "text", "is_van": "boolean", "warehouse_name": "text",
         "store_kind": "text", "items_held": "integer", "first_move": "date", "last_move": "date",
         **{c: "numeric" for c in OLD_COLUMNS[2:10] + APPENDED[2:6]}}
LINES_COLUMNS = ["voucher", "vtype", "date", "wh", "to_wh", "item", "qty_in", "qty_out", "value_in", "value_out",
                 "narration"]
LINES_TYPES = {"date": "date", **{c: "numeric" for c in ("qty_in", "qty_out", "value_in", "value_out")}}
FLOW_COLUMNS = ["issued_on", "voucher_no", "item_name", "from_warehouse", "to_warehouse", "issued_qty",
                "issued_value_bhd", "received_qty", "received_by", "received_on", "status"]
STORE_KINDS = ("main", "damage", "sim", "van", "outlet", "modern_trade", "facility", "other")
OLD_WORDS = ("hub", "damage", "sim", "modern_trade", "facility", "van")
FOLD = "regexp_replace(lower(btrim(regexp_replace({x}, '\\s+', ' ', 'g'))), ' [0-9]+-[0-9]+$', '')"
COMMIT_RE = re.compile(r"^\s*(commit|end)\s*;", re.I | re.M)       # what apply_sql --rehearse refuses


# ── reading SQL ──────────────────────────────────────────────────────────────────

def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip(text: str) -> str:
    """SQL without -- comments (the prose may name what the code must never do). No literal in these files holds
    two dashes, so a line split is enough."""
    return "\n".join(ln.split("--", 1)[0] for ln in text.splitlines())


def _norm(text: str) -> str:
    return " ".join(text.split())


def _statements(text: str) -> list[str]:
    """The statements of a file, comments dropped, each without its ';'. Quotes and $$ bodies are kept whole, so
    a ';' inside a DO block or a literal never splits one."""
    out, cur, i, n = [], [], 0, len(text)
    while i < n:
        if text.startswith("--", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        ch = text[i]
        if ch == "'":
            j = i + 1
            while j < n:
                if text[j] == "'" and text[j + 1:j + 2] == "'":
                    j += 2
                    continue
                if text[j] == "'":
                    break
                j += 1
            cur.append(text[i:j + 1])
            i = j + 1
            continue
        if text.startswith("$$", i):
            j = text.index("$$", i + 2)
            cur.append(text[i:j + 2])
            i = j + 2
            continue
        if ch == ";":
            stmt = "".join(cur).strip()
            if stmt:
                out.append(stmt)
            cur = []
        else:
            cur.append(ch)
        i += 1
    stmt = "".join(cur).strip()
    if stmt:
        out.append(stmt)
    return out


def _stmt(path: Path, head: str) -> str:
    """The one statement of a file that starts with `head` (normalised whitespace, any case)."""
    found = [s for s in _statements(_text(path)) if _norm(s).lower().startswith(head.lower())]
    assert len(found) == 1, (path.name, head, len(found))
    return found[0]


def _do_blocks(path: Path) -> list[str]:
    return [s for s in _statements(_text(path)) if _norm(s).lower().startswith("do $$")]


def _top_level(sql: str):
    """(index, char, depth) for each character outside string literals; depth counts parentheses."""
    depth, i, n = 0, 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            j = sql.index("'", i + 1)
            while sql[j + 1:j + 2] == "'":
                j = sql.index("'", j + 2)
            i = j + 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        yield i, ch, depth
        i += 1


def _final_select_items(stmt: str) -> list[str]:
    """The items of a view's outermost select list (after its CTEs): split at top-level commas, up to the
    top-level FROM."""
    low = stmt.lower()
    starts = [i for i, ch, d in _top_level(stmt) if d == 0 and low.startswith("select", i)
              and not low[i - 1:i].isalnum() and not low[i + 6:i + 7].isalnum()]
    begin = starts[-1] + len("select")
    items, cur_start, end = [], begin, None
    for i, ch, d in _top_level(stmt):
        if i < begin or d != 0:
            continue
        if ch == ",":
            items.append(stmt[cur_start:i])
            cur_start = i + 1
        elif low.startswith("from", i) and low[i - 1:i].isspace() and low[i + 4:i + 5].isspace():
            end = i
            break
    items.append(stmt[cur_start:end])
    return [_norm(x) for x in items]


def _alias(item: str) -> str:
    m = re.search(r"\bas\s+(\w+)$", item) or re.fullmatch(r"(?:\w+\.)?(\w+)", item)
    assert m, item
    return m.group(1)


# ── the store-kind CASE and the salesman regex, read out of M1 and run in Python ──────

def _like(pattern: str, value: str) -> bool:
    rx = "".join(".*" if c == "%" else "." if c == "_" else re.escape(c) for c in pattern)
    return re.fullmatch(rx, value, re.S) is not None


def _kind_rules() -> tuple[list[tuple[list[tuple[str, object]], str]], str]:
    """The `kinds` CASE of v_salesman_stock_recon: [(the terms of one WHEN, joined by OR), kind], and the ELSE."""
    stmt = _norm(_stmt(M1, "create or replace view v_salesman_stock_recon as"))
    m = re.search(r"kinds as \( select wh, case (.*?) else '(\w+)' end as store_kind from folded \)", stmt)
    assert m, "the kinds CTE moved: re-read this test"
    rules = []
    for cond, kind in re.findall(r"when (.*?) then '(\w+)'", m.group(1)):
        terms: list[tuple[str, object]] = []
        for term in re.split(r"\s+or\s+", cond.strip()):
            if t := re.fullmatch(r"k = '([^']*)'", term):
                terms.append(("eq", t.group(1)))
            elif t := re.fullmatch(r"k like '([^']*)'", term):
                terms.append(("like", t.group(1)))
            elif t := re.fullmatch(r"k in \((.*)\)", term):
                terms.append(("in", tuple(re.findall(r"'([^']*)'", t.group(1)))))
            else:
                raise AssertionError(f"a WHEN term this twin does not read: {term!r}")
        rules.append((terms, kind))
    return rules, m.group(2)


def _fold(name: str) -> str:
    """The SQL fold (FOLD) in Python: runs of spaces to one, trimmed, lower case, a trailing ' <n>-<n>' dropped."""
    return re.sub(r" [0-9]+-[0-9]+$", "", " ".join(str(name).split()).lower())


def sql_kind(name: str) -> str:
    rules, otherwise = _kind_rules()
    k = _fold(name)
    for terms, kind in rules:
        for op, arg in terms:
            if (op == "eq" and k == arg) or (op == "like" and _like(arg, k)) or (op == "in" and k in arg):
                return kind
    return otherwise


# Names as a ledger could spell them (all invented apart from the stores the repository already names). The
# answers are the contract: StoreRules.kind's five, the old view's two, else other.
KIND_SAMPLES = {
    "Accessories Warehouse": "main",
    "accessories  WAREHOUSE": "main",                # case and spaces folded
    "Accessories Warehouse 10-1": "main",            # the code one report writes after the name, dropped
    "Accessories Damage Warehouse": "damage",
    "Van Q - SIM WH": "sim",
    "SIM Warehouse": "sim",
    "Van A - Acc WH": "van",
    "Rep Simba - Acc WH": "van",                     # the old ilike '%sim%' put this van among the SIM stores
    "Kasim Demo - Acc WH": "van",
    "VAN B  -  ACC  WH": "van",
    "Van C - Acc WH 2-1": "van",
    "Causeway": "outlet",
    "YQ Roadshow": "outlet",
    "Multi Market Demo": "modern_trade",
    "Samples Warehouse - Demo": "facility",
    "Postpaid Giveaway Demo": "facility",
    "Devices - Inventory Demo": "facility",
    "SIM Damage Warehouse": "facility",              # neither the damage store nor a SIM store by the explicit rules
    "Sim Test Store": "other",                       # the letters "sim" in a name are no rule
    "Expo Stand Demo": "other",
    "Van D-Acc WH": "other",                         # not the ' - Acc WH' suffix
}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. the six files as text
# ═══════════════════════════════════════════════════════════════════════════════

@test("files: no CASCADE, no security_invoker, no COMMIT (rehearsable), a self-check in each; GRANTs plain, never in a DO block")
def _():
    for path in R9_FILES:
        assert path.is_file(), path.name
        code = _strip(_text(path)).lower()
        assert "cascade" not in code, path.name
        assert "security_invoker" not in code, path.name
        assert not COMMIT_RE.search(_text(path)), f"{path.name}: apply_sql --rehearse would refuse it"
        assert "raise exception" in code and _do_blocks(path), f"{path.name}: no self-check"
        for block in _do_blocks(path):
            assert not re.search(r"\bgrant\s", block.lower()), f"{path.name}: a GRANT inside a DO block"
            assert not re.search(r"\brevoke\s", block.lower()), f"{path.name}: a REVOKE inside a DO block"
        for stmt in _statements(_text(path)):
            low = _norm(stmt).lower()
            if low.startswith("drop "):
                assert re.fullmatch(r"drop (view|table) if exists \w+", low), (path.name, low)


@test("grants: a revoke from anon / authenticated for every object created; SELECT for yq_readonly on the views only; none on team_messages")
def _():
    def plain(path):
        return [_norm(s).lower() for s in _statements(_text(path)) if re.match(r"\s*(grant|revoke)\s", s, re.I)]
    assert plain(M1) == ["revoke all on table v_transfer_lines from anon, authenticated",
                         "revoke all on table v_salesman_stock_recon from anon, authenticated",
                         "grant select on v_transfer_lines to yq_readonly",
                         "grant select on v_salesman_stock_recon to yq_readonly"], plain(M1)
    assert plain(M1_REVERSE) == ["revoke all on table v_salesman_stock_recon from anon, authenticated",
                                 "grant select on v_salesman_stock_recon to yq_readonly"], plain(M1_REVERSE)
    assert plain(X1) == ["revoke all on table team_messages from anon, authenticated"], plain(X1)
    assert plain(X1_REVERSE) == [] and plain(M2) == []
    assert plain(M2_REVERSE) == ['revoke all on table public."v_transfer_flow" from anon, authenticated',
                                 "grant select on v_transfer_flow to yq_readonly"], plain(M2_REVERSE)
    # the reverse's revoke is the hotfix's own statement; its grant is daily_ops2's (a list there)
    assert 'revoke all on table public."v_transfer_flow" from anon, authenticated;' in _text(HOTFIX)
    assert re.search(r"grant select on v_catalog, v_transfer_flow,.*?to yq_readonly;", _text(DAILY_OPS2), re.S)
    # what each file creates, each closed to the browser roles above
    created = {p.name: re.findall(r"create (?:or replace )?(?:view|table)(?: if not exists)? (\w+)", _strip(_text(p)))
               for p in R9_FILES}
    assert created == {M1.name: ["v_transfer_lines", "v_salesman_stock_recon"],
                       M1_REVERSE.name: ["v_salesman_stock_recon"], X1.name: ["team_messages"], X1_REVERSE.name: [],
                       M2.name: [], M2_REVERSE.name: ["v_transfer_flow"]}, created


@test("sqlglot: every statement outside a DO block parses as Postgres (only ALTER TABLE ... ROW LEVEL SECURITY is a bare command)")
def _():
    import sqlglot
    from sqlglot import exp
    logging.getLogger("sqlglot").setLevel(logging.ERROR)
    for path in R9_FILES:
        for stmt in _statements(_text(path)):
            if _norm(stmt).lower().startswith("do $$"):
                continue
            tree = sqlglot.parse_one(stmt, read="postgres")
            if isinstance(tree, exp.Command):
                assert re.fullmatch(r"alter table \w+ enable row level security", _norm(stmt).lower()), \
                    (path.name, _norm(stmt)[:80])


# ═══════════════════════════════════════════════════════════════════════════════
# 2. M1: v_transfer_lines and v_salesman_stock_recon
# ═══════════════════════════════════════════════════════════════════════════════

@test("v_transfer_lines: the contract's columns; the issue and receive rows of the accessory items; narration as stored")
def _():
    stmt = _stmt(M1, "create or replace view v_transfer_lines as")
    items = _final_select_items(stmt)
    assert [_alias(i) for i in items] == LINES_COLUMNS, items
    assert items == ["sm.voucher", "sm.voucher_type as vtype", "sm.move_date as date", "sm.warehouse_name as wh",
                     "sm.to_warehouse_name as to_wh", "sm.item_name as item", "sm.received_qty as qty_in",
                     "sm.issued_qty as qty_out", "sm.received_value_bhd as value_in",
                     "sm.issued_value_bhd as value_out", "sm.narration"], items
    low = _norm(stmt).lower()
    assert "where sm.voucher_type in ('stock issue voucher', 'stock receive voucher') and sm.move_date is not null" in low
    # an accessory = an item that has EVER moved in the main store (the folded name, as the recon reads it)
    assert FOLD.format(x="n.wh") + " = 'accessories warehouse'" in _norm(stmt)
    assert "join main_names m on m.wh = sm.warehouse_name" in low and "join acc on acc.item_name = sm.item_name" in low
    assert "regexp_replace(sm.narration" not in low, "narration as stored: the data step masks it"
    assert not re.search(r"\b(stock_balance|v_sales|order_lines)\b", low)


@test("v_salesman_stock_recon: the eleven old columns first, in stock_transfer_migration.sql's order and forms, then the nine appended")
def _():
    old = _stmt(OLD_RECON, "create view v_salesman_stock_recon as")
    # the old order, read from the old file: the inner list in order, then is_van after r.*
    at = [_norm(old).index(f" as {c}") for c in OLD_COLUMNS[:-1]]
    assert at == sorted(at), "the old file's order"
    assert "select r.*, (r.warehouse_type = 'van') as is_van from (" in _norm(old)
    new = _stmt(M1, "create or replace view v_salesman_stock_recon as")
    items = _final_select_items(new)
    assert [_alias(i) for i in items] == OLD_COLUMNS + APPENDED, [_alias(i) for i in items]
    expr = {_alias(i): re.sub(r"\s+as\s+\w+$", "", i) for i in items}
    # the forms that make the old types: round(...)::numeric for units, round(...::numeric, 3) for BHD
    for c in ("transferred_in_qty", "transferred_out_qty", "sold_qty", "on_hand_qty", "shortage_qty",
              "unexplained_qty", "received_qty", "customer_returns_qty", "excess_qty", "pending_qty"):
        assert re.fullmatch(r"round\(.*\)::numeric", expr[c]), (c, expr[c])
    for c in ("transferred_in_value_bhd", "shortage_value_bhd"):
        assert re.fullmatch(r"round\(coalesce\(\w+\.\w+, 0\)::numeric, 3\)", expr[c]), (c, expr[c])
    assert expr["is_van"] == "(k.store_kind in ('van', 'outlet', 'other'))"
    assert expr["items_held"] == "coalesce(h.items, 0)::int"
    assert expr["first_move"] == "least(o.first_move, tin.first_move)"
    assert expr["last_move"] == "greatest(o.last_move, tin.last_move)"
    # the old unexplained formula, on the new inputs
    assert expr["unexplained_qty"] == ("round((coalesce(tin.qty, 0) - coalesce(o.tout_qty, 0)) - coalesce(o.sold_qty, 0) "
                                       "- coalesce(o.on_hand, 0))::numeric")
    assert expr["pending_qty"] == "round(coalesce(tin.qty, 0) - coalesce(o.rcv_qty, 0))::numeric"
    # the self-check's lists are the columns and types pinned here
    code = _norm(_strip(_text(M1)))
    want = ", ".join(f"{c} {TYPES[c]}" for c in OLD_COLUMNS + APPENDED)
    got = re.search(r"want_recon constant text := (.*?);", code).group(1)
    assert "".join(re.findall(r"'([^']*)'", got)) == want, got
    got = re.search(r"want_lines constant text := (.*?);", code).group(1)
    assert "".join(re.findall(r"'([^']*)'", got)) == ", ".join(f"{c} {LINES_TYPES.get(c, 'text')}" for c in LINES_COLUMNS)
    rev = _norm(_strip(_text(M1_REVERSE)))
    got = re.search(r"want constant text := (.*?);", rev).group(1)
    assert "".join(re.findall(r"'([^']*)'", got)) == ", ".join(f"{c} {TYPES[c]}" for c in OLD_COLUMNS)


@test("v_salesman_stock_recon reads the stock ledger alone: on hand = received - issued, every figure from stock_movements")
def _():
    stmt = _norm(_stmt(M1, "create or replace view v_salesman_stock_recon as")).lower()
    assert not re.search(r"\b(stock_balance|v_sales|order_lines|orders)\b", stmt), "the ledger only"
    for piece in (
        "sum(q_in) - sum(q_out) as on_hand",
        "sum(q_out) filter (where voucher_type = 'stock issue voucher' and to_warehouse_name is not null) as tout_qty",
        "sum(q_in) filter (where voucher_type = 'stock receive voucher') as rcv_qty",
        "sum(q_out - q_in) filter (where voucher_type = 'sales invoice') as sold_qty",
        "sum(q_in - q_out) filter (where voucher_type = 'sales return') as ret_qty",
        "sum(coalesce(issued_qty, received_qty, 0)) filter (where voucher_type = 'shortages in stock') as short_qty",
        "sum(coalesce(issued_value_bhd, received_value_bhd, 0)) filter (where voucher_type = 'shortages in stock') as short_val",
        "sum(coalesce(received_qty, issued_qty, 0)) filter (where voucher_type = 'excesses in stocks') as excess_qty",
        "where voucher_type = 'stock issue voucher' and to_warehouse_name is not null group by to_warehouse_name",
        "having sum(q_in) - sum(q_out) > 0",
        "join kinds m on m.wh = sm.warehouse_name where m.store_kind = 'main'",
        "join acc on acc.item_name = sm.item_name",
        "from stores s join kinds k on k.wh = s.wh left join own o on o.wh = s.wh left join tin on tin.wh = s.wh "
        "left join held h on h.wh = s.wh",
        "order by shortage_value_bhd desc, unexplained_qty desc, warehouse_name",
    ):
        assert piece in stmt, piece
    # the old shortage formula is the old file's own
    assert "sum(coalesce(issued_qty, received_qty, 0))" in _norm(_text(OLD_RECON)).lower()


@test("store kinds: StoreRules.kind's five rules on the folded name, in its order, then the old modern_trade / facility; never 'sim' inside a name")
def _():
    stmt = _norm(_stmt(M1, "create or replace view v_salesman_stock_recon as"))
    assert FOLD.format(x="wh") + " as k" in stmt, "the fold is StoreRules.canon's (no aliases)"
    rules, otherwise = _kind_rules()
    assert [(terms, kind) for terms, kind in rules] == [
        ([("eq", "accessories warehouse")], "main"),
        ([("eq", "accessories damage warehouse")], "damage"),
        ([("like", "% - sim wh"), ("eq", "sim warehouse")], "sim"),
        ([("like", "% - acc wh")], "van"),
        ([("in", ("causeway", "yq roadshow"))], "outlet"),
        ([("like", "%multi market%")], "modern_trade"),
        ([("like", "%sample%"), ("like", "%giveaway%"), ("like", "%inventory%"), ("like", "%warehouse%")], "facility"),
    ], rules
    assert otherwise == "other"
    assert set(kind for _t, kind in rules) | {otherwise} == set(STORE_KINDS)
    # the old view's own two rules, which this keeps (ilike there = like on the folded name here)
    old = _norm(_text(OLD_RECON)).lower()
    assert "when s.wh ilike '%multi market%' then 'modern_trade'" in old
    assert ("when s.wh ilike '%sample%' or s.wh ilike '%giveaway%' or s.wh ilike '%inventory%' or s.wh ~* 'warehouse' "
            "then 'facility'") in old
    # the trap is gone: no substring test for sim, no case-insensitive match left to slip one in
    code = _strip(_text(M1)).lower()
    assert "%sim%" not in code and "ilike" not in code and "~*" not in code
    # the old words, kept for the readers of warehouse_type
    assert "case k.store_kind when 'main' then 'hub' when 'outlet' then 'van' when 'other' then 'van' else k.store_kind end as warehouse_type" in stmt
    for name, want in KIND_SAMPLES.items():
        assert sql_kind(name) == want, (name, sql_kind(name), want)


@test("store kinds: the SQL CASE agrees with scripts/ops/pairing.py StoreRules.kind on every sample (SKIP before that module exists)")
def _():
    if not (SCRIPTS / "ops" / "pairing.py").is_file():
        print("  SKIP: scripts/ops/pairing.py is not on this branch yet (release A brings it)")
        return
    from scripts.ops.pairing import StoreRules
    rules = StoreRules(main="Accessories Warehouse", damage="Accessories Damage Warehouse")
    for name in KIND_SAMPLES:
        mine = sql_kind(name)
        theirs = rules.kind(name)
        # the SQL's two extra kinds are the old view's own; StoreRules has no such kind and calls them other
        assert (mine if mine not in ("modern_trade", "facility") else "other") == theirs, (name, mine, theirs)
        assert rules.canon(name) == _fold(name), (name, rules.canon(name), _fold(name))


@test("salesman: its old meaning, the store name exactly as the ledger and v_sales spell it (' - Acc WH' kept), = warehouse_name")
def _():
    # the old view took it from v_sales.salesman_resolved, which spells a van as the ledger does: never stripped
    old = _norm(_stmt(OLD_RECON, "create view v_salesman_stock_recon as")).lower()
    assert "select distinct salesman_resolved as wh from v_sales" in old and "s.wh as salesman" in old
    stmt = _stmt(M1, "create or replace view v_salesman_stock_recon as")
    expr = {_alias(i): re.sub(r"\s+as\s+\w+$", "", i) for i in _final_select_items(stmt)}
    assert expr["salesman"] == expr["warehouse_name"] == "k.wh", (expr["salesman"], expr["warehouse_name"])
    code = _norm(_strip(_text(M1))).lower()
    assert "regexp_replace(k.wh" not in code and "acc\\s+wh" not in code, "no suffix strip anywhere"
    assert "or salesman is distinct from warehouse_name) then" in _norm(_do_blocks(M1)[-1]), "the self-check holds it"


@test("data step: r7_narration_mask_data_migration.sql's mask verbatim, on stock_movements and the stock copies; row_hash never written")
def _():
    r7_update = _stmt(R7_DATA, "update order_lines")
    r9_update = _stmt(M1, "update stock_movements")
    assert _norm(r9_update) == _norm(r7_update).replace("update order_lines", "update stock_movements", 1)
    r7_block = next(b for b in _do_blocks(R7_DATA) if "update ingest_stage" in b)
    r9_block = next(b for b in _do_blocks(M1) if "update ingest_stage" in b)
    assert _norm(r9_block) == _norm(r7_block).replace("target = 'order_lines'", "target = 'stock_movements'")
    assert _norm(r9_block).count("target = 'stock_movements'") == 2 and "order_lines" not in r9_block
    for piece in (r9_update, r9_block):
        assert "row_hash" not in piece, "the data step never touches row_hash"
    # every statement that writes, and what it writes
    writes = [_norm(s).lower()[:40] for s in _statements(_text(M1)) if re.match(r"\s*(update|insert|delete)\s", s, re.I)]
    assert writes == ["update stock_movements set narration = r"], writes
    # the self-check reads each copy table only when it exists, in its own IF (one expression would be planned whole)
    check = _norm(_do_blocks(M1)[-1])
    for t in ("ingest_stage", "ingest_replaced"):
        assert f"if to_regclass('public.{t}') is not null then if exists (select 1 from {t} where target = 'stock_movements'" in check


@test("reverse M1: drops v_transfer_lines; stock_transfer_migration.sql's statement verbatim; plain grants; the narration cannot come back")
def _():
    stmts = [_norm(s).lower() for s in _statements(_text(M1_REVERSE))]
    assert "drop view if exists v_transfer_lines" in stmts and "drop view if exists v_salesman_stock_recon" in stmts
    assert stmts.index("drop view if exists v_salesman_stock_recon") < next(
        i for i, s in enumerate(stmts) if s.startswith("create view v_salesman_stock_recon"))
    assert _norm(_stmt(M1_REVERSE, "create view v_salesman_stock_recon as")) == \
        _norm(_stmt(OLD_RECON, "create view v_salesman_stock_recon as")), "the old statement, verbatim"
    head = _text(M1_REVERSE)
    assert "CANNOT put back" in head and "narration" in head and "backup taken before M1" in head
    assert not any(s.startswith(("update", "insert", "delete")) for s in stmts), "the reverse touches no row"


@test("X1: team_messages(run pk, data_date, text, sha256, created_at), RLS on, no policy, nothing granted; the reverse drops it")
def _():
    ddl = _norm(_stmt(X1, "create table if not exists team_messages")).lower()
    assert ddl == ("create table if not exists team_messages ( run text primary key, data_date date not null, "
                   "text text not null, sha256 text not null, created_at timestamptz not null default now() )"), ddl
    stmts = [_norm(s).lower() for s in _statements(_text(X1))]
    assert "alter table team_messages enable row level security" in stmts
    # enabled, never forced: the poster writes with the owner login and there is no policy (a forced RLS would shut it out)
    assert not any("force row level security" in s for s in stmts if not s.startswith("do $$")), \
        "RLS is enabled, never forced"
    assert not any(s.startswith(("grant", "create policy")) for s in stmts)
    check = _norm(_do_blocks(X1)[-1]).lower()
    for piece in ("primary key (run)", "rowsecurity", "pg_policies", "has_table_privilege('yq_readonly', 'public.team_messages', 'select')",
                  "'run text, data_date date, text text, sha256 text, created_at timestamp with time zone'",
                  "if (select relforcerowsecurity from pg_class where oid = 'public.team_messages'::regclass) then",
                  "pg_get_userbyid(relowner)"):
        assert piece in check, piece
    # the writer's contract, written where the next reader looks: the upsert never sets created_at
    head = _text(X1)
    assert "never this column" in head and "NOT FORCED" in head and "sha256(text) equals" in head
    rev = [_norm(s).lower() for s in _statements(_text(X1_REVERSE)) if not s.lower().startswith(("do $$", "set "))]
    assert rev == ["drop table if exists team_messages"], rev


@test("M2: refuses while v_transfer_lines is missing, then a plain drop of v_transfer_flow; its reverse is daily_ops2's view verbatim")
def _():
    stmts = [_norm(s).lower() for s in _statements(_text(M2))]
    guard = next(i for i, s in enumerate(stmts) if "to_regclass('public.v_transfer_lines') is null" in s)
    assert guard < stmts.index("drop view if exists v_transfer_flow"), "the guard comes first"
    assert [s for s in stmts if s.startswith("drop")] == ["drop view if exists v_transfer_flow"]
    assert _norm(_stmt(M2_REVERSE, "create or replace view v_transfer_flow as")) == \
        _norm(_stmt(DAILY_OPS2, "create or replace view v_transfer_flow as")), "daily_ops2's statement, verbatim"
    assert ", ".join(FLOW_COLUMNS) in "".join(re.findall(r"'([^']*)'", re.search(
        r"want constant text := (.*?);", _norm(_strip(_text(M2_REVERSE)))).group(1)))


# ═══════════════════════════════════════════════════════════════════════════════
# 3. scripts/ingest.py parse_stock
# ═══════════════════════════════════════════════════════════════════════════════

ITEM = "Cable C1 (TST)"
RAW_NARRATION = "for van A CPR 900000123 card 4000000000000002"
# the row_hash parse_stock gave each of these rows BEFORE release 2b (computed on 2217a20's parser)
GOLDEN_HASHES = {"SIO:T-26-10-1": "1660341267db7139", "STRV:T-26-10-1": "d69117dfa494f363",
                 "SIO:T-26-10-2": "e72729b6b0822138"}


def _ledger_grid():
    import pandas as pd
    from scripts import ingest
    return pd.DataFrame([
        ["Stock Ledger"] + [None] * 14,
        ingest.EXPECTED_HEADERS["stock_movements"],
        [ITEM] + [None] * 14,
        [datetime(2026, 10, 2), "SIO:T-26-10-1", None, None, 30, 0.5, 70, None, 15, 35, 0.5,
         "Accessories Warehouse", "Van A - Acc WH", RAW_NARRATION, "Stock Issue Voucher"],
        [datetime(2026, 10, 3), "STRV:T-26-10-1", 30, None, None, None, 30, None, None, None, None,
         "Van A - Acc WH", None, RAW_NARRATION, "Stock Receive Voucher"],
        [datetime(2026, 10, 4), "SIO:T-26-10-2", None, None, 5, 0.5, 65, None, 2.5, 32.5, 0.5,
         "Accessories Warehouse", "Van A - Acc WH", "order YQ-2610-0019", "Stock Issue Voucher"],
        ["Sub Total"] + [None] * 14,
    ], dtype=object)


@test("parse_stock: the narration is masked at parse time; row_hash is still made from the raw cells (the pre-2b hashes)")
def _():
    from scripts import ingest
    grid = _ledger_grid()
    rows = ingest.parse_stock(grid, "Stock_ledger.xlsx")
    assert [r["voucher"] for r in rows] == list(GOLDEN_HASHES)
    assert [r["narration"] for r in rows] == ["for van A CPR ******123 card ************002"] * 2 + ["order YQ-2610-0019"]
    assert {r["voucher"]: r["row_hash"] for r in rows} == GOLDEN_HASHES, "row_hash changed: every stored key would differ"
    for i, r in zip((3, 4, 5), rows):
        cells = list(grid.iloc[i])
        raw = [ingest.norm_date(cells[0]), ingest.txt(cells[1]), ITEM, *cells[2:15]]
        assert raw[14] == cells[13], "raw[14] is the narration cell"
        assert r["row_hash"] == ingest.row_hash(raw), "made from the raw cells"
        if r["narration"] != cells[13]:
            masked = raw[:14] + [r["narration"]] + raw[15:]
            assert r["row_hash"] != ingest.row_hash(masked), "and of the raw narration, never the masked one"
    assert rows[0]["voucher_type"] == "Stock Issue Voucher" and rows[0]["issued_qty"] == 30
    assert rows[0]["warehouse_name"] == "Accessories Warehouse" and rows[0]["to_warehouse_name"] == "Van A - Acc WH"


@test("pairing: a transfer narration hashes the same whether it was masked at parse time or not (ledger masks first)")
def _():
    from scripts import ingest
    from scripts.ops import ledger
    masked = ingest.mask_personal_ids(RAW_NARRATION)
    assert masked != RAW_NARRATION
    assert ledger._narr_key(RAW_NARRATION, ingest.mask_personal_ids) == ledger._narr_key(masked, ingest.mask_personal_ids)
    assert ingest.mask_personal_ids(masked) == masked, "masking twice changes nothing"


# ═══════════════════════════════════════════════════════════════════════════════
# 4. local Postgres replay (only on a loopback cluster named by YQ_LOCAL_PG_R9)
# ═══════════════════════════════════════════════════════════════════════════════

LOOPBACK = {"localhost": "127.0.0.1", "127.0.0.1": "127.0.0.1", "::1": "::1"}


def _local_dsn(env=None) -> tuple[str | None, str | None, str]:
    """(dsn, hostaddr, why): the replay's cluster, from YQ_LOCAL_PG_R9 only, and only on this machine's loopback:
    an explicit host, no service file, no other hostaddr. (None, None, why) otherwise."""
    env = os.environ if env is None else env
    dsn = (env.get("YQ_LOCAL_PG_R9") or "").strip()
    if not dsn:
        return None, None, "YQ_LOCAL_PG_R9 is not set"
    try:
        from psycopg.conninfo import conninfo_to_dict
        info = conninfo_to_dict(dsn)
    except Exception:  # noqa: BLE001
        return None, None, "YQ_LOCAL_PG_R9 is not a connection string"
    host = str(info.get("host") or "")
    if "service" in info or host not in LOOPBACK or str(info.get("hostaddr") or LOOPBACK[host]) != LOOPBACK[host]:
        return None, None, "YQ_LOCAL_PG_R9 must name this machine's loopback (127.0.0.1, ::1 or localhost)"
    return dsn, LOOPBACK[host], ""


@test("replay guard: only a loopback cluster named by YQ_LOCAL_PG_R9; nothing set = SKIP, anything else refused")
def _():
    for dsn, ok in (("", None), ("postgresql://postgres@127.0.0.1:55432/r9", "127.0.0.1"),
                    ("postgresql://postgres@localhost:55432/r9", "127.0.0.1"), ("host=::1 port=55432 dbname=r9", "::1"),
                    ("postgresql://postgres@db.example.invalid:5432/postgres", None),
                    ("postgresql://postgres@127.0.0.1:5432/x?hostaddr=10.0.0.5", None), ("service=prod", None),
                    ("postgresql:///x", None), ("not a dsn ===", None)):
        got_dsn, addr, why = _local_dsn({"YQ_LOCAL_PG_R9": dsn})
        assert addr == ok and (got_dsn is not None) == (ok is not None) and (why == "") == (ok is not None), (dsn, why)
    assert _local_dsn({})[2] == "YQ_LOCAL_PG_R9 is not set"


# Supabase-style roles, and the default ACL production had before the 16-Sep hotfix (every object postgres makes in
# public granted to the API roles), so only the files' own revokes close a view or a table to anon / authenticated.
# stock_movements as scripts/schema.sql has it (the products FK left out); v_sales and stock_balance as stand-ins
# with the columns the old view reads; the batch importer's two copy tables as ingest_batches_migration.sql has them.
REPLAY_SCHEMA = """
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then create role service_role nologin bypassrls; end if;
  if not exists (select 1 from pg_roles where rolname = 'yq_readonly') then create role yq_readonly nologin; end if;
end $$;
alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
create table stock_movements (
  id bigint generated always as identity primary key, item_name text not null, product_id bigint, move_date date,
  voucher text, voucher_type text, received_qty numeric, received_rate_bhd numeric, issued_qty numeric,
  issued_rate_bhd numeric, balance_qty numeric, received_value_bhd numeric, issued_value_bhd numeric,
  balance_value_bhd numeric, avg_rate_bhd numeric, warehouse_name text, to_warehouse_name text, narration text,
  row_hash text, source_file text, imported_at timestamptz default now(), unique (voucher, item_name, row_hash));
create table stock_balance (id bigint generated always as identity primary key, item_name text not null,
  warehouse_name text, net_qty numeric, total_value_bhd numeric, as_of_date date);
create table sales_stub (salesman_resolved text, quantity numeric, revenue_bhd numeric);
create view v_sales as select salesman_resolved, quantity, revenue_bhd from sales_stub;
create table ingest_batches (id bigint generated always as identity primary key);
create table ingest_stage (id bigint generated always as identity primary key,
  batch_id bigint not null references ingest_batches(id) on delete restrict, target text not null,
  row jsonb not null, action text, row_id bigint);
create table ingest_replaced (id bigint generated always as identity primary key,
  batch_id bigint not null references ingest_batches(id) on delete restrict, target text not null,
  action text not null check (action in ('deleted', 'updated', 'revived', 'voided')), row jsonb not null);
"""

ISSUE, RECEIVE = "Stock Issue Voucher", "Stock Receive Voucher"
ACC1, ACC2, SIMI = "Cable C1 (TST)", "Charger K2 (TST)", "Starter Pack S9 (TST)"
MAIN, VAN, SIMBA, SIMVAN, SIMWH = ("Accessories Warehouse", "Van A - Acc WH", "Rep Simba - Acc WH", "Van Q - SIM WH",
                                   "SIM Warehouse")
OUTLET, DMG, MT, OTHER, FAC = ("Causeway", "Accessories Damage Warehouse", "Multi Market Demo", "Expo Stand Demo",
                               "Samples Warehouse - Demo")
D = lambda day: date(2026, 10, day)  # noqa: E731
N = lambda x: None if x is None else Decimal(str(x))  # noqa: E731
SEED = [  # date, voucher, type, store, sent to, item, units in, units out, BHD in, BHD out, narration, row_hash
    (D(1), "MRN:T-26-10-1", "Material Receipt Note", MAIN, None, ACC1, 100, None, 50, None, None, "h01"),
    (D(1), "MRN:T-26-10-1", "Material Receipt Note", MAIN, None, ACC2, 40, None, 80, None, None, "h02"),
    (D(2), "SIO:T-26-10-1", ISSUE, MAIN, VAN, ACC1, None, 30, None, 15, "for van A CPR 900000123", "h03"),
    (D(2), "STRV:T-26-10-1", RECEIVE, VAN, None, ACC1, 30, None, None, None, "for van A CPR 900000123", "h04"),
    (D(3), "SI:T-26-10-1", "Sales Invoice", VAN, None, ACC1, None, 12, None, 6, None, "h05"),
    (D(4), "SR:T-26-10-1", "Sales Return", VAN, None, ACC1, 2, None, 1, None, None, "h06"),
    (D(5), "SIS:T-26-10-1", "Shortages in Stock", VAN, None, ACC1, None, 1, None, 0.5, None, "h07"),
    (D(5), "SIO:T-26-10-2", ISSUE, MAIN, SIMBA, ACC2, None, 10, None, 20, "card 4000000000000002 YQ-2610-0019", "h08"),
    (D(6), "SIO:T-26-10-3", ISSUE, VAN, MAIN, ACC1, None, 5, None, 2.5, "back to main", "h09"),
    (D(6), "STRV:T-26-10-3", RECEIVE, MAIN, None, ACC1, 5, None, 2.5, None, "back to main", "h10"),
    (D(6), "SIO:T-26-10-4", ISSUE, MAIN, OUTLET, ACC2, None, 5, None, 10, None, "h11"),
    (D(6), "STRV:T-26-10-4", RECEIVE, OUTLET, None, ACC2, 5, None, None, None, None, "h12"),
    (D(7), "SI:T-26-10-2", "Sales Invoice", OUTLET, None, ACC2, None, 2, None, 4, None, "h13"),
    (D(7), "SIO:T-26-10-5", ISSUE, MAIN, DMG, ACC1, None, 3, None, 1.5, None, "h14"),
    (D(7), "STRV:T-26-10-5", RECEIVE, DMG, None, ACC1, 3, None, None, None, None, "h15"),
    (D(8), "EXS:T-26-10-1", "Excesses in Stocks", MAIN, None, ACC2, 1, None, 2, None, None, "h16"),
    (D(8), "SIO:T-26-10-6", ISSUE, MAIN, SIMVAN, ACC2, None, 1, None, 2, None, "h17"),
    (D(8), "STRV:T-26-10-6", RECEIVE, SIMVAN, None, ACC2, 1, None, None, None, None, "h18"),
    (D(9), "SIO:T-26-10-7", ISSUE, MAIN, MT, ACC1, None, 4, None, 2, None, "h19"),
    (D(9), "STRV:T-26-10-7", RECEIVE, MT, None, ACC1, 4, None, None, None, None, "h20"),
    (D(9), "SIO:T-26-10-8", ISSUE, MAIN, OTHER, ACC1, None, 1, None, 0.5, None, "h21"),
    (D(10), "STRV:T-26-10-8", RECEIVE, OTHER, None, ACC1, 1, None, None, None, None, "h22"),
    (D(1), "MRN:T-26-10-2", "Material Receipt Note", SIMWH, None, SIMI, 50, None, 25, None, None, "h23"),
    (D(2), "SIO:T-26-10-9", ISSUE, SIMWH, SIMVAN, SIMI, None, 20, None, 10, "sim CPR 900000777", "h24"),
    (D(2), "STRV:T-26-10-9", RECEIVE, SIMVAN, None, SIMI, 20, None, None, None, None, "h25"),
    (D(3), "SI:T-26-10-3", "Sales Invoice", SIMVAN, None, SIMI, None, 3, None, 3, "cust CPR 900000888", "h26"),
    (D(9), "SIO:T-26-10-10", ISSUE, MAIN, FAC, ACC1, None, 2, None, 1, None, "h27"),
    (D(10), "STRV:T-26-10-10", RECEIVE, FAC, None, ACC1, 2, None, None, None, None, "h28"),
]
FIGURES = ("salesman", "store_kind", "warehouse_type", "is_van", "transferred_in_qty", "transferred_in_value_bhd",
           "transferred_out_qty", "received_qty", "pending_qty", "sold_qty", "customer_returns_qty", "shortage_qty",
           "shortage_value_bhd", "excess_qty", "on_hand_qty", "unexplained_qty", "items_held", "first_move", "last_move")
# worked by hand from SEED (accessory items only: the starter pack never moved in the main store)
WANT = {
    MAIN: (MAIN, "main", "hub", False, 5, 2.5, 56, 5, 0, 0, 0, 0, 0, 1, 90, -141, 2, D(1), D(9)),
    VAN: (VAN, "van", "van", True, 30, 15, 5, 30, 0, 12, 2, 1, 0.5, 0, 14, -1, 1, D(2), D(6)),
    SIMBA: (SIMBA, "van", "van", True, 10, 20, 0, 0, 10, 0, 0, 0, 0, 0, 0, 10, 0, D(5), D(5)),
    OUTLET: (OUTLET, "outlet", "van", True, 5, 10, 0, 5, 0, 2, 0, 0, 0, 0, 3, 0, 1, D(6), D(7)),
    DMG: (DMG, "damage", "damage", False, 3, 1.5, 0, 3, 0, 0, 0, 0, 0, 0, 3, 0, 1, D(7), D(7)),
    SIMVAN: (SIMVAN, "sim", "sim", False, 1, 2, 0, 1, 0, 0, 0, 0, 0, 0, 1, 0, 1, D(8), D(8)),
    MT: (MT, "modern_trade", "modern_trade", False, 4, 2, 0, 4, 0, 0, 0, 0, 0, 0, 4, 0, 1, D(9), D(9)),
    OTHER: (OTHER, "other", "van", True, 1, 0.5, 0, 1, 0, 0, 0, 0, 0, 0, 1, 0, 1, D(9), D(10)),
    FAC: (FAC, "facility", "facility", False, 2, 1, 0, 2, 0, 0, 0, 0, 0, 0, 2, 0, 1, D(9), D(10)),
}
WANT_ORDER = [VAN, SIMBA, DMG, OUTLET, OTHER, MT, FAC, SIMVAN, MAIN]   # shortage BHD, then the gap, then the name


def _attrs(cur, view: str) -> list[str]:
    cur.execute("""select a.attname || ' ' || format_type(a.atttypid, a.atttypmod) from pg_attribute a
                    where a.attrelid = %s::regclass and a.attnum > 0 and not a.attisdropped order by a.attnum""",
                (f"public.{view}",))
    return [r[0] for r in cur.fetchall()]


def _can(cur, role: str, rel: str, priv: str = "SELECT") -> bool:
    cur.execute("select has_table_privilege(%s, %s, %s)", (role, f"public.{rel}", priv))
    return cur.fetchone()[0]


def _recon(cur) -> list[dict]:
    cur.execute(f"select warehouse_name, {', '.join(FIGURES)} from v_salesman_stock_recon")
    return [dict(zip(("warehouse_name", *FIGURES), r)) for r in cur.fetchall()]


def _check_m1(cur, hashes) -> None:
    """Everything M1 promises, on the seeded ledger."""
    from scripts.ingest import mask_personal_ids
    assert _attrs(cur, "v_salesman_stock_recon") == [f"{c} {TYPES[c]}" for c in OLD_COLUMNS + APPENDED]
    assert _attrs(cur, "v_transfer_lines") == [f"{c} {LINES_TYPES.get(c, 'text')}" for c in LINES_COLUMNS]
    rows = _recon(cur)
    assert [r["warehouse_name"] for r in rows] == WANT_ORDER, [r["warehouse_name"] for r in rows]
    for r in rows:
        assert tuple(r[k] for k in FIGURES) == WANT[r["warehouse_name"]], (r["warehouse_name"], r)
    for name, want in KIND_SAMPLES.items():          # the twin read the same CASE the database just ran
        if name in WANT:
            assert WANT[name][1] == want == sql_kind(name)
    cur.execute("select row_hash, narration from stock_movements order by id")
    stored = cur.fetchall()
    assert [h for h, _n in stored] == hashes, "row_hash untouched"
    raw = {s[11]: s[10] for s in SEED}
    assert {h: n for h, n in stored} == {h: mask_personal_ids(n) for h, n in raw.items()}
    assert dict(stored)["h08"] == "card ************002 YQ-2610-0019", "the order number survives"
    cur.execute("select target, row ->> 'narration' from ingest_stage order by id")
    assert cur.fetchall() == [("stock_movements", "for van A CPR ******123"), ("order_lines", "CPR 900000999")], \
        "the stock copy masked; an order-lines copy is r7's, untouched here"
    cur.execute("select row ->> 'narration', row ->> 'row_hash' from ingest_replaced order by id")
    assert cur.fetchall() == [("card ************002", "h99")]
    cur.execute("select vtype, item, count(*) from v_transfer_lines group by 1, 2 order by 1, 2")
    assert cur.fetchall() == [(ISSUE, ACC1, 6), (ISSUE, ACC2, 3), (RECEIVE, ACC1, 6), (RECEIVE, ACC2, 2)]
    cur.execute("select date, wh, to_wh, qty_in, qty_out, value_in, value_out, narration from v_transfer_lines "
                "where voucher = 'SIO:T-26-10-1'")
    assert cur.fetchall() == [(D(2), MAIN, VAN, None, Decimal(30), None, Decimal(15), "for van A CPR ******123")]
    for view in ("v_transfer_lines", "v_salesman_stock_recon"):
        assert _can(cur, "yq_readonly", view)
        for role in ("anon", "authenticated"):
            assert not _can(cur, role, view), (role, view)
    # the read RPC's role reads both views (they run as their owner) and still no table beneath them
    cur.execute("set local role yq_readonly")
    cur.execute("select (select count(*) from v_transfer_lines), (select count(*) from v_salesman_stock_recon)")
    assert cur.fetchone() == (17, 9)
    cur.execute("reset role")
    assert not _can(cur, "yq_readonly", "stock_movements")


@test("local replay: M1 twice, its reverse twice, M1 again; M2 guarded, twice, reversed twice, again; X1 twice, reversed twice, again (SKIP without YQ_LOCAL_PG_R9)")
def _():
    dsn, hostaddr, why = _local_dsn()
    if dsn is None:
        if why == "YQ_LOCAL_PG_R9 is not set":
            print("  SKIP: YQ_LOCAL_PG_R9 is not set (an empty database on a loopback cluster; see the module docstring)")
            return
        raise AssertionError("refusing to connect: " + why)
    try:
        import psycopg
    except ImportError:
        print("  SKIP: psycopg not installed")
        return
    conn = psycopg.connect(dsn, hostaddr=hostaddr, connect_timeout=3)
    try:
        cur = conn.cursor()
        cur.execute("select current_user, to_regclass('public.stock_movements'), to_regclass('public.v_sales')")
        user, has_sm, has_sales = cur.fetchone()
        if user != "postgres" or has_sm is not None or has_sales is not None:
            print(f"  SKIP: the replay needs an empty database and the postgres superuser (views are owned by "
                  f"postgres); got {user}, stock_movements={has_sm}, v_sales={has_sales}")
            return
        cur.execute(REPLAY_SCHEMA)
        # production as it stands: the old recon (stock_transfer_migration.sql, as it is) and v_transfer_flow
        cur.execute(_text(OLD_RECON))
        cur.execute(_stmt(DAILY_OPS2, "create or replace view v_transfer_flow as"))
        cur.execute("grant select on v_transfer_flow to yq_readonly")
        cur.executemany(
            "insert into stock_movements (move_date, voucher, voucher_type, warehouse_name, to_warehouse_name, item_name, "
            "received_qty, issued_qty, received_value_bhd, issued_value_bhd, narration, row_hash) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [(*s[:6], N(s[6]), N(s[7]), N(s[8]), N(s[9]), s[10], s[11]) for s in SEED])
        cur.execute("insert into sales_stub values ('Van A - Acc WH', 12, 6), ('Causeway', 2, 4)")
        cur.execute("insert into ingest_batches default values returning id")
        batch = cur.fetchone()[0]
        cur.execute("insert into ingest_stage (batch_id, target, row) values (%s, 'stock_movements', %s::jsonb), "
                    "(%s, 'order_lines', %s::jsonb)",
                    (batch, json.dumps({"voucher": "SIO:T-26-10-1", "narration": "for van A CPR 900000123",
                                        "row_hash": "h03"}),
                     batch, json.dumps({"invoice_no": "SI : SI-T-1", "narration": "CPR 900000999"})))
        cur.execute("insert into ingest_replaced (batch_id, target, action, row) values (%s, 'stock_movements', "
                    "'deleted', %s::jsonb)",
                    (batch, json.dumps({"voucher": "SI:T-26-10-9", "narration": "card 4000000000000002",
                                        "row_hash": "h99"})))
        old_cols = _attrs(cur, "v_salesman_stock_recon")
        assert old_cols == [f"{c} {TYPES[c]}" for c in OLD_COLUMNS], old_cols
        flow_cols = _attrs(cur, "v_transfer_flow")
        assert [c.split(" ")[0] for c in flow_cols] == FLOW_COLUMNS
        assert _can(cur, "anon", "v_salesman_stock_recon"), "the replay's default grant exposes it before M1"
        cur.execute("select id, row_hash from stock_movements order by id")
        hashes = [h for _i, h in cur.fetchall()]
        m1, m1r = _text(M1), _text(M1_REVERSE)

        cur.execute(m1)
        _check_m1(cur, hashes)
        cur.execute(m1)                                   # idempotent: the same views, nothing masked twice
        _check_m1(cur, hashes)

        cur.execute(m1r)
        cur.execute(m1r)
        assert cur.execute("select to_regclass('public.v_transfer_lines')").fetchone()[0] is None
        assert _attrs(cur, "v_salesman_stock_recon") == old_cols
        cur.execute("select salesman, warehouse_type, is_van from v_salesman_stock_recon order by salesman")
        assert cur.fetchall() == [("Causeway", "van", True), ("Van A - Acc WH", "van", True)], "the old view answers"
        assert _can(cur, "yq_readonly", "v_salesman_stock_recon") and not _can(cur, "anon", "v_salesman_stock_recon")
        cur.execute("select count(*) from stock_movements where narration ~ '(^|[^0-9])[0-9]{9}([^0-9]|$)'")
        assert cur.fetchone()[0] == 0, "the reverse cannot unmask"
        cur.execute(m1)
        _check_m1(cur, hashes)

        # M2: refused while v_transfer_lines is missing (scaffolding inside a savepoint), then applied
        cur.execute("savepoint m2_guard")
        cur.execute("drop view v_transfer_lines")
        try:
            cur.execute(_text(M2))
            raise AssertionError("M2 must refuse without v_transfer_lines")
        except psycopg.errors.RaiseException as e:
            assert "v_transfer_lines is missing" in str(e), e
        cur.execute("rollback to savepoint m2_guard")
        cur.execute(_text(M2))
        cur.execute(_text(M2))
        assert cur.execute("select to_regclass('public.v_transfer_flow')").fetchone()[0] is None
        cur.execute(_text(M2_REVERSE))
        cur.execute(_text(M2_REVERSE))
        assert _attrs(cur, "v_transfer_flow") == flow_cols
        assert _can(cur, "yq_readonly", "v_transfer_flow") and not _can(cur, "anon", "v_transfer_flow")
        cur.execute("select voucher_no, status from v_transfer_flow where voucher_no = 'T-26-10-2'")
        assert cur.fetchall() == [("T-26-10-2", "pending")], "the old pairing answers again"
        cur.execute(_text(M2))
        assert cur.execute("select to_regclass('public.v_transfer_flow')").fetchone()[0] is None

        # X1
        cur.execute(_text(X1))
        cur.execute(_text(X1))
        # the poster's upsert (scripts/yq_team_post.py): run, data_date, text, sha256, never created_at
        upsert = ("insert into team_messages (run, data_date, text, sha256) values (%s, %s, %s, %s) "
                  "on conflict (run) do update set data_date = excluded.data_date, text = excluded.text, "
                  "sha256 = excluded.sha256")
        first, again = "Team: synthetic line one\n", "Team: synthetic line two\n"
        cur.execute(upsert, ("run-1", date(2026, 10, 20), first, hashlib.sha256(first.encode()).hexdigest()))
        # now() is the transaction's start for every statement here: date the first post earlier, as a real one would be
        cur.execute("update team_messages set created_at = created_at - interval '1 day' where run = 'run-1' "
                    "returning created_at")
        born = cur.fetchone()[0]
        cur.execute(upsert, ("run-1", date(2026, 10, 21), again, hashlib.sha256(again.encode()).hexdigest()))
        cur.execute("select data_date, text, created_at, encode(sha256(convert_to(text, 'UTF8')), 'hex') = sha256 "
                    "from team_messages where run = 'run-1'")
        assert cur.fetchone() == (date(2026, 10, 21), again, born, True), "kept byte-exact; created_at the first post's"
        cur.execute("savepoint x1_dup")
        try:
            cur.execute("insert into team_messages (run, data_date, text, sha256) values ('run-1', '2026-10-21', 'x', 'y')")
            raise AssertionError("one message per run")
        except psycopg.errors.UniqueViolation:
            cur.execute("rollback to savepoint x1_dup")
        cur.execute("select relrowsecurity, relforcerowsecurity, pg_get_userbyid(relowner) from pg_class "
                    "where oid = 'public.team_messages'::regclass")
        assert cur.fetchone() == (True, False, "postgres"), "RLS enabled, not forced, owned by the poster's login"
        cur.execute("select count(*) from pg_policies where tablename = 'team_messages'")
        assert cur.fetchone()[0] == 0
        for role in ("anon", "authenticated"):
            for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert not _can(cur, role, "team_messages", priv), (role, priv)
        assert not _can(cur, "yq_readonly", "team_messages")
        assert _can(cur, "service_role", "team_messages") and _can(cur, "service_role", "team_messages", "INSERT")
        cur.execute(_text(X1_REVERSE))
        cur.execute(_text(X1_REVERSE))
        assert cur.execute("select to_regclass('public.team_messages')").fetchone()[0] is None
        cur.execute(_text(X1))
        assert cur.execute("select to_regclass('public.team_messages')").fetchone()[0] is not None
        print("  replay: M1 x2, reverse x2, M1; M2 refused, x2, reverse x2, M2; X1 x2, reverse x2, X1 - all rolled back")
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
