"""Release r12 "WEKOME live", the shop price: v_catalog.b2c_rate is the item's current Causeway row of the MA price
book, no longer the modern_trade book (owner, 12-Nov-2026; scripts/r12_b2c_causeway_migration.sql and its reverse).

    python -m tests.test_r12_b2c_causeway

Same lightweight runner as tests/test_r11_zte_devices.py (no pytest). No database, no network, no .env: SUPABASE_URL,
SUPABASE_KEY and DATABASE_URL are pinned to ci.invalid BEFORE any import (a worktree under the main checkout would
otherwise find the real .env). Every item code, date and rate below is invented.

Covered:
  * both files, read as text (comments set aside): ASCII only, lock_timeout first, one view (v_catalog), no CASCADE,
    DROP, TRUNCATE, ALTER, COMMIT (apply_sql --rehearse refuses one) or security_invoker; the self-check last, raising
    (the migration's guard first: R7d's category_master must be there); the grants v_catalog has (SELECT for
    yq_readonly, nothing for anon / authenticated) restated as plain statements, never inside a DO block; every
    statement outside a DO block parses as Postgres (sqlglot);
  * the new category: the migration's only row writes are the 'Screen Protector' category (Accessories) and its
    category_master row, each ON CONFLICT DO NOTHING in R7d's seed shape; the reverse's only one deletes that master
    row (the category stays); no product or log row is touched; SCREEN PROTECTOR is app/catalog.py's category and,
    with R7d's nine rows, every catalog category has an analytics category;
  * v_catalog, parsed: the 17 columns of daily_ops2_migration.sql (catalog_migration.sql's 16 + b2c_rate), names and
    order, in the migration and the reverse; every other column the same expression, the same FROM, the b2b lateral
    verbatim; only the b2c lateral differs; the reverse is daily_ops2_migration.sql's statement verbatim;
  * the b2c lateral: book MA_base, warehouse 'Causeway', no customer, every filter of v_price_list_by_book's
    current-row rule (selling_prices_void_migration.sql, parsed: Authorized, a rate above 0, the dates around today,
    not voided), latest start_date then highest id (that view's order without its "blank warehouse first" key), one
    row; modern_trade nowhere in the view; the self-check's cross-check picks with the same filters and order;
  * the rule on invented rows: the lateral's own query, run by sqlglot's executor with today pinned, equals a Python
    twin of v_price_list_by_book's rule: the dealer and Roadshow rows, a customer's row, a modern_trade row, a voided
    (day-first phantom) row, a future and an expired row, a zero rate, an unauthorized row, two rows on one day, a code
    in another case, and no Causeway row at all (NULL: no fallback to modern_trade);
  * no view in scripts/*.sql reads v_catalog (CREATE OR REPLACE leaves every reader as it is); nothing in the files
    reads like a live amount (the repo-clean scan); docs/MIGRATIONS.md names r12 as v_catalog's owner.

Not covered here: running the files on a database. Rehearse on production from the main folder
(python -m scripts.apply_sql scripts/r12_b2c_causeway_migration.sql --rehearse, always rolled back), or replay on the
local scratch cluster docs/MIGRATIONS.md describes (portable PostgreSQL, port 55432).
"""
from __future__ import annotations

import os

os.environ["SUPABASE_URL"] = "https://ci.invalid"          # before ANY import that could read a .env
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"

import io  # noqa: E402
import logging  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
import traceback  # noqa: E402
from datetime import date, datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCRIPTS = ROOT / "scripts"
MIG = SCRIPTS / "r12_b2c_causeway_migration.sql"
REV = SCRIPTS / "r12_b2c_causeway_reverse.sql"
DAILY_OPS2 = SCRIPTS / "daily_ops2_migration.sql"           # v_catalog's owner before r12
CATALOG = SCRIPTS / "catalog_migration.sql"                 # v_catalog's first definition (16 columns)
VOID = SCRIPTS / "selling_prices_void_migration.sql"        # v_price_list_by_book: the current-row rule
VIEW_HEAD = "create or replace view v_catalog as"
COLUMNS = ["item_code", "display_name", "spec", "category", "brand", "division", "dealer_price", "roadshow_price", "rrp",
           "standard_rate", "product_image_url", "package_image_url", "sort_order", "is_active", "updated_at",
           "created_at", "b2c_rate"]
COMMIT_RE = re.compile(r"^\s*(commit|end)\s*;", re.I | re.M)       # what apply_sql --rehearse refuses
TODAY = date(2026, 11, 12)

TESTS: list = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


# ── reading SQL (the helpers of tests/test_r10_long_number_mask.py) ───────────────────────────────

def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip(text: str) -> str:
    """SQL without -- comments. No literal in these files holds two dashes, so a line split is enough."""
    return "\n".join(ln.split("--", 1)[0] for ln in text.splitlines())


def _norm(text: str) -> str:
    return " ".join(text.split())


def _statements(text: str) -> list[str]:
    """The statements of a file, comments dropped, each without its ';'. Quotes and $$ bodies are kept whole."""
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
    found = [s for s in _statements(_text(path)) if _norm(s).lower().startswith(head.lower())]
    assert len(found) == 1, (path.name, head, len(found))
    return found[0]


def _do_blocks(path: Path) -> list[str]:
    return [s for s in _statements(_text(path)) if _norm(s).lower().startswith("do $$")]


def _sqlglot():
    import sqlglot
    from sqlglot import exp
    logging.getLogger("sqlglot").setLevel(logging.ERROR)
    return sqlglot, exp


def _view(path: Path):
    """v_catalog's SELECT in a file, parsed."""
    sqlglot, exp = _sqlglot()
    tree = sqlglot.parse_one(_stmt(path, VIEW_HEAD), read="postgres")
    assert isinstance(tree, exp.Create) and tree.this.name == "v_catalog", path.name
    return tree.expression


def _from(select) -> str:
    node = select.args.get("from_") or select.args.get("from")
    return node.sql(dialect="postgres")


def _lateral_query(join):
    """The SELECT inside `left join lateral ( ... ) x on true`."""
    _, exp = _sqlglot()
    lat = join.this
    assert isinstance(lat, exp.Lateral), join.sql()
    inner = lat.this
    return inner.this if isinstance(inner, exp.Subquery) else inner


def _conjuncts(select, drop_tables: bool = True) -> set[str]:
    """The AND-ed conditions of a SELECT's WHERE, each as Postgres SQL, table qualifiers dropped."""
    _, exp = _sqlglot()
    cond = select.args["where"].this
    parts = list(cond.flatten()) if isinstance(cond, exp.And) else [cond]
    out = set()
    for p in parts:
        p = p.copy()
        if drop_tables:
            for col in p.find_all(exp.Column):
                col.set("table", None)
        out.add(p.sql(dialect="postgres"))
    return out


def _order(select) -> list[str]:
    _, exp = _sqlglot()
    out = []
    for o in select.args["order"].expressions:
        o = o.copy()
        for col in o.find_all(exp.Column):
            col.set("table", None)
        out.append(o.sql(dialect="postgres"))
    return out


def _b2b_rule():
    """v_price_list_by_book's CTE (selling_prices_void_migration.sql): its WHERE and its row_number() order."""
    sqlglot, exp = _sqlglot()
    tree = sqlglot.parse_one(_stmt(VOID, "create or replace view v_price_list_by_book as"), read="postgres")
    cte = next(c for c in tree.expression.find_all(exp.CTE) if c.alias == "cur").this
    window = next(cte.find_all(exp.Window))
    order = []
    for o in window.args["order"].expressions:
        order.append(o.sql(dialect="postgres"))
    return _conjuncts(cte), order


# ═══════════════════════════════════════════════════════════════════════════════
# 1. the two files as text
# ═══════════════════════════════════════════════════════════════════════════════

MIG_WRITES = [
    "insert into categories (name, division) values ('Screen Protector', 'Accessories') on conflict (name) do nothing",
    "insert into category_master (catalog_category, category_id, note) select m.catalog_category, c.id, m.note from "
    "(values ('SCREEN PROTECTOR', 'Screen Protector', 'screen protectors (WEKOME)') ) as m(catalog_category, "
    "category_name, note) join categories c on c.name = m.category_name and c.division = 'Accessories' on conflict "
    "(catalog_category) do nothing",
]
REV_WRITES = [
    "delete from category_master cm using categories c where cm.catalog_category = 'SCREEN PROTECTOR' and c.id = "
    "cm.category_id and c.name = 'Screen Protector'",
]


@test("files: ASCII only; lock_timeout first; one view; no CASCADE, DROP, TRUNCATE, ALTER, COMMIT or security_invoker; the self-check last; the grants plain")
def _():
    for path in (MIG, REV):
        assert path.read_bytes().isascii(), f"{path.name}: ASCII only, so no client encoding can change a byte of it"
        text = _text(path)
        stmts = _statements(text)
        assert _norm(stmts[0]).lower() == "set lock_timeout = '2s'", (path.name, stmts[0][:60])
        code = _strip(text).lower()
        for word in ("cascade", "security_invoker", "drop ", "truncate", "alter ", "commit"):
            assert word not in code, (path.name, word)
        assert not COMMIT_RE.search(text), f"{path.name}: apply_sql --rehearse would refuse it"
        views = [s for s in stmts if _norm(s).lower().startswith("create ")]
        assert len(views) == 1 and _norm(views[0]).lower().startswith(VIEW_HEAD), f"{path.name}: one view, v_catalog"
        blocks = _do_blocks(path)
        assert blocks and stmts[-1] == blocks[-1], f"{path.name}: the self-check runs last"
        assert all("raise exception" in b.lower() for b in blocks), path.name
        for block in blocks:
            assert not re.search(r"\b(grant|revoke|insert|update|delete)\s", block.lower()), \
                (path.name, "a GRANT, REVOKE or row write inside a DO block")
        plain = [_norm(s).lower() for s in stmts if re.match(r"\s*(grant|revoke)\s", s, re.I)]
        assert plain == ["revoke all on table v_catalog from anon, authenticated",
                         "grant select on v_catalog to yq_readonly"], (path.name, plain)
    # the migration: a guard first (R7d's master must be there), the self-check last
    blocks = _do_blocks(MIG)
    stmts = _statements(_text(MIG))
    assert len(blocks) == 2 and stmts[1] == blocks[0] and stmts[-1] == blocks[1], "the guard first, the self-check last"
    assert "to_regclass('public.category_master') is null" in blocks[0] and "r7d_category_master_migration.sql" in blocks[0]
    assert len(_do_blocks(REV)) == 1


@test("rows: the migration only inserts the category and its master row, ON CONFLICT DO NOTHING; the reverse only deletes that master row; no product is touched")
def _():
    for path, want in ((MIG, MIG_WRITES), (REV, REV_WRITES)):
        writes = [_norm(s).lower() for s in _statements(_text(path)) if re.match(r"\s*(insert|update|delete)\s", s, re.I)]
        assert writes == [w.lower() for w in want], (path.name, writes)
        assert not re.search(r"\b(update|insert into|delete from)\s+products\b", _strip(_text(path)).lower()), path.name
        assert "category_master_log" not in _strip(_text(path)), f"{path.name}: the log is category_backfill's to write"
    assert "delete from categories" not in _strip(_text(REV)).lower(), "the category stays: products may point at it"


@test("the new category: SCREEN PROTECTOR is app/catalog.py's; with R7d's nine master rows every catalog category has an analytics category")
def _():
    from app.catalog import CATEGORY_ORDER
    assert "SCREEN PROTECTOR" in CATEGORY_ORDER, CATEGORY_ORDER
    r7d = _text(SCRIPTS / "r7d_category_master_migration.sql")
    seeded = r7d[r7d.index("insert into category_master"):r7d.index("on conflict (catalog_category) do nothing")]
    master = {m for m in re.findall(r"^\s*\('([A-Z][A-Z ]+)',", seeded, re.M)}
    assert "SCREEN PROTECTOR" not in master and len(master) == 9, master
    assert set(CATEGORY_ORDER) <= master | {"SCREEN PROTECTOR"}, set(CATEGORY_ORDER) - master
    # the same shape as R7d's seed: a VALUES list joined to its Accessories category by name
    assert "join categories c on c.name = m.category_name and c.division = 'Accessories'" in _norm(seeded)
    assert "join categories c on c.name = m.category_name and c.division = 'Accessories'" in MIG_WRITES[1]


@test("files: the grants are the ones v_catalog has today (catalog / daily_ops2 grant yq_readonly, the hotfix revokes anon)")
def _():
    assert re.search(r"grant select on v_catalog to yq_readonly;", _text(CATALOG))
    assert re.search(r"grant select on v_catalog, v_transfer_flow,.*?to yq_readonly;", _text(DAILY_OPS2), re.S)
    assert 'revoke all on table public."v_catalog" from anon, authenticated;' in _text(SCRIPTS / "hotfix_view_grants_migration.sql")


@test("sqlglot: every statement outside a DO block parses as Postgres")
def _():
    sqlglot, exp = _sqlglot()
    for path in (MIG, REV):
        for stmt in _statements(_text(path)):
            if _norm(stmt).lower().startswith("do $$"):
                continue
            tree = sqlglot.parse_one(stmt, read="postgres")
            assert not isinstance(tree, exp.Command), (path.name, _norm(stmt)[:80])


# ═══════════════════════════════════════════════════════════════════════════════
# 2. v_catalog, parsed
# ═══════════════════════════════════════════════════════════════════════════════

@test("v_catalog: the 17 columns of daily_ops2_migration.sql, names and order, in the migration and the reverse")
def _():
    for path in (DAILY_OPS2, MIG, REV):
        assert [e.alias_or_name for e in _view(path).expressions] == COLUMNS, path.name
    first = [e.alias_or_name for e in _view(CATALOG).expressions]
    assert first + ["b2c_rate"] == COLUMNS, "catalog_migration.sql's 16 columns, b2c_rate appended (daily_ops2)"


@test("v_catalog: daily_ops2_migration.sql's statement but the b2c lateral; the reverse is daily_ops2's statement verbatim")
def _():
    old, new = _view(DAILY_OPS2), _view(MIG)
    for o, n in zip(old.expressions, new.expressions):
        if o.alias_or_name == "b2c_rate":
            assert (o.sql(), n.sql()) == ("b2c.price_bhd AS b2c_rate", "b2c.rate_bhd AS b2c_rate"), (o.sql(), n.sql())
        else:
            assert o.sql() == n.sql(), o.alias_or_name
    assert _from(old) == _from(new) == "FROM catalog_items AS ci"
    oj, nj = old.args["joins"], new.args["joins"]
    assert len(oj) == len(nj) == 2
    assert oj[0].sql() == nj[0].sql(), "the b2b lateral (standard_rate) is daily_ops2's, verbatim"
    assert "v_price_list_by_book" in oj[0].sql() and "'MA_base'" in oj[0].sql()
    assert "'modern_trade'" in oj[1].sql(), "daily_ops2's b2c read the modern_trade book"
    assert "modern_trade" not in _stmt(MIG, VIEW_HEAD), "no fallback to modern_trade"
    assert _norm(_stmt(REV, VIEW_HEAD)) == _norm(_stmt(DAILY_OPS2, VIEW_HEAD)), "the reverse restores today's view"


@test("b2c lateral: MA_base, warehouse 'Causeway', no customer, v_price_list_by_book's current-row rule, latest start then highest id, one row")
def _():
    q = _lateral_query(_view(MIG).args["joins"][1])
    assert _from(q) == "FROM selling_prices AS sp"
    rule, window_order = _b2b_rule()
    assert rule == {"status = 'Authorized'", "rate_bhd IS NOT NULL", "rate_bhd > 0", "start_date <= CURRENT_DATE",
                    "end_date IS NULL OR end_date >= CURRENT_DATE", "voided_at IS NULL"}, rule
    mine = _conjuncts(q)
    assert rule <= mine, f"a filter of v_price_list_by_book's rule is missing: {rule - mine}"
    assert mine - rule == {"sku_code = item_code", "price_book = 'MA_base'", "warehouse_name = 'Causeway'",
                           "customer_code IS NULL OR customer_code = ''"}, mine - rule
    assert _conjuncts(q, drop_tables=False) >= {"sp.sku_code = ci.item_code"}, "the code matches exactly, as standard_rate's"
    assert window_order == ["(warehouse_name IS NULL) DESC", "start_date DESC", "id DESC"], window_order
    assert _order(q) == window_order[1:] == ["start_date DESC", "id DESC"], _order(q)
    assert q.args["limit"].sql() == "LIMIT 1"
    # "no customer" is read the way v_product_margin reads it (selling_prices_void_migration.sql)
    assert "(customer_code is null or customer_code = '')" in _norm(_stmt(VOID, "create or replace view v_product_margin as"))


@test("self-check: the cross-check picks the Causeway row with the lateral's filters and order; the column list, the grants, the refusal")
def _():
    sqlglot, exp = _sqlglot()
    block = _do_blocks(MIG)[-1]                       # the self-check (the first block is the guard)
    m = re.search(r"with cur as \((.*?)\)\s*select count\(\*\) into n_off", block, re.S)
    assert m, "the cross-check CTE moved: re-read this test"
    cte = sqlglot.parse_one(m.group(1), read="postgres")
    lateral = _lateral_query(_view(MIG).args["joins"][1])
    assert _conjuncts(cte) == _conjuncts(lateral) - {"sku_code = item_code"}, "the cross-check filters drifted"
    window = next(cte.find_all(exp.Window))
    assert [o.sql(dialect="postgres") for o in window.args["partition_by"]] == ["sku_code"]
    assert [o.sql(dialect="postgres") for o in window.args["order"].expressions] == ["start_date DESC", "id DESC"]
    check = _norm(block)
    want = ",".join(COLUMNS)
    got = "".join(re.findall(r"'([a-z0-9_,]+)'", check[check.index("want constant text :="):check.index("def text;")]))
    assert got == want, got
    for piece in ("if cols is distinct from want then raise exception",
                  "if position('''Causeway''' in def) = 0 or position('modern_trade' in def) > 0 then raise exception",
                  "if n_off <> 0 then raise exception", "if n_b2b <> 0 then raise exception",
                  "left join v_price_list_by_book b on b.sku_code = v.item_code and b.price_book = 'MA_base'",
                  "grantee in ('anon', 'authenticated')) then raise exception",
                  "not has_table_privilege('yq_readonly', 'public.v_catalog', 'SELECT') then raise exception",
                  "if n_traded > 0 and n_priced = 0 then raise exception",
                  "d.refobjid = 'public.v_catalog'::regclass",
                  "join categories c on c.id = cm.category_id where cm.catalog_category = 'SCREEN PROTECTOR'; "
                  "if cat_name is null then",
                  "if cat_div is distinct from 'Accessories' then raise exception"):
        assert piece in check, piece
    assert re.search(r"if cat_name is null then .*?raise exception", check), "a missing master row refuses the file"
    rev = _norm(_do_blocks(REV)[0])
    assert "if position('modern_trade' in def) = 0 or position('''Causeway''' in def) > 0 then raise exception" in rev
    assert "if cols is distinct from want then raise exception" in rev
    assert ("where cm.catalog_category = 'SCREEN PROTECTOR' and c.name = 'Screen Protector') then raise exception"
            in rev), "the reverse proves its master row is gone"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. the rule on invented rows (sqlglot's executor runs the lateral's own query)
# ═══════════════════════════════════════════════════════════════════════════════

_COLS = ("id", "sku_code", "price_book", "warehouse_name", "customer_code", "status", "rate_bhd", "start_date",
         "end_date", "voided_at")
_VOIDED = datetime(2026, 9, 24, tzinfo=timezone.utc)
# (id, code, book, warehouse, customer, status, rate, start, end, voided) -- every code and rate is invented
_ROWS = [
    # T1: the dealer row and the Roadshow row are newer; a Causeway row starts tomorrow: the current Causeway row wins
    (101, "T1", "MA_base", None, None, "Authorized", 1.0, "2026-11-10", None, None),
    (102, "T1", "MA_base", "YQ Roadshow", None, "Authorized", 3.5, "2026-11-11", None, None),
    (103, "T1", "MA_base", "Causeway", None, "Authorized", 2.0, "2026-06-01", None, None),
    (104, "T1", "MA_base", "Causeway", None, "Authorized", 2.5, "2026-11-09", None, None),
    (105, "T1", "MA_base", "Causeway", None, "Authorized", 9.0, "2026-11-13", None, None),
    # T2: two Causeway rows on one day: the highest id
    (201, "T2", "MA_base", "Causeway", None, "Authorized", 1.25, "2026-10-01", None, None),
    (202, "T2", "MA_base", "Causeway", None, "Authorized", 1.75, "2026-10-01", None, None),
    # T3: the newest Causeway row is voided (a day-first phantom): the older live one
    (301, "T3", "MA_base", "Causeway", None, "Authorized", 4.0, "2026-09-06", None, _VOIDED),
    (302, "T3", "MA_base", "Causeway", None, "Authorized", 3.0, "2026-06-09", None, None),
    # T4: the newest ended yesterday; one ending today still counts
    (401, "T4", "MA_base", "Causeway", None, "Authorized", 6.0, "2026-11-01", "2026-11-11", None),
    (402, "T4", "MA_base", "Causeway", None, "Authorized", 5.0, "2026-10-01", "2026-11-12", None),
    # T5: an unauthorized and a zero-rated row are never current
    (501, "T5", "MA_base", "Causeway", None, "Pending", 8.0, "2026-11-11", None, None),
    (502, "T5", "MA_base", "Causeway", None, "Authorized", 0.0, "2026-11-10", None, None),
    (503, "T5", "MA_base", "Causeway", None, "Authorized", 7.0, "2026-05-01", None, None),
    # T6: a customer's own Causeway price is not the shop price; a blank customer code is no customer
    (601, "T6", "MA_base", "Causeway", "CUST-01", "Authorized", 1.1, "2026-11-11", None, None),
    (602, "T6", "MA_base", "Causeway", "", "Authorized", 1.2, "2026-08-01", None, None),
    # T7: only the modern_trade book and the dealer row: NULL, never a fallback
    (701, "T7", "modern_trade", "Causeway", None, "Authorized", 2.2, "2026-05-01", None, None),
    (702, "T7", "MA_base", None, None, "Authorized", 1.0, "2026-05-01", None, None),
    # T8: a row that starts today is current
    (801, "T8", "MA_base", "Causeway", None, "Authorized", 1.6, "2026-11-12", None, None),
    # t9 (lower case): the code matches exactly, as standard_rate's does, so T9 has no Causeway price
    (901, "t9", "MA_base", "Causeway", None, "Authorized", 1.9, "2026-11-01", None, None),
]
_WANT = {"T1": 2.5, "T2": 1.75, "T3": 3.0, "T4": 5.0, "T5": 7.0, "T6": 1.2, "T7": None, "T8": 1.6, "T9": None,
         "T10": None}


def _table() -> list[dict]:
    out = []
    for r in _ROWS:
        row = dict(zip(_COLS, r))
        row["start_date"] = date.fromisoformat(row["start_date"])
        row["end_date"] = date.fromisoformat(row["end_date"]) if row["end_date"] else None
        out.append(row)
    return out


def _twin(rows: list[dict], code: str, today: date):
    """v_price_list_by_book's current-row rule in Python, for the Causeway layer of MA_base with no customer."""
    live = [r for r in rows
            if r["sku_code"] == code and r["price_book"] == "MA_base" and r["warehouse_name"] == "Causeway"
            and (r["customer_code"] is None or r["customer_code"] == "")
            and r["status"] == "Authorized" and r["rate_bhd"] is not None and r["rate_bhd"] > 0
            and r["start_date"] <= today and (r["end_date"] is None or r["end_date"] >= today)
            and r["voided_at"] is None]
    live.sort(key=lambda r: (r["start_date"], r["id"]), reverse=True)
    return live[0]["rate_bhd"] if live else None


@test("the rule on invented rows: the lateral's own query (sqlglot's executor, today pinned) = the Python twin of v_price_list_by_book's rule")
def _():
    _, exp = _sqlglot()
    from sqlglot.executor import execute
    query = _lateral_query(_view(MIG).args["joins"][1])
    table = _table()
    for code, want in _WANT.items():
        q = query.copy()
        q = q.transform(lambda n: exp.Literal.string(code) if isinstance(n, exp.Column) and n.table == "ci" else n)
        q = q.transform(lambda n: exp.Cast(this=exp.Literal.string(TODAY.isoformat()), to=exp.DataType.build("date"))
                        if isinstance(n, exp.CurrentDate) else n)
        rows = execute(q, tables={"selling_prices": table}).rows
        got = rows[0][0] if rows else None
        assert got == want == _twin(table, code, TODAY), (code, got, want, _twin(table, code, TODAY))


# ═══════════════════════════════════════════════════════════════════════════════
# 4. readers, amounts, documents
# ═══════════════════════════════════════════════════════════════════════════════

@test("no view in scripts/*.sql reads v_catalog: CREATE OR REPLACE with the same columns leaves every reader as it is")
def _():
    readers = []
    for path in sorted(SCRIPTS.glob("*.sql")):
        code = _strip(_text(path))
        for name, body in re.findall(r"create\s+(?:or\s+replace\s+)?view\s+(\w+)\s+as\b(.*?);", code, re.I | re.S):
            if name.lower() != "v_catalog" and re.search(r"\bv_catalog\b", body, re.I):
                readers.append(f"{path.name}: {name}")
    assert not readers, readers


@test("nothing in the files reads like a live amount (the repo-clean scan)")
def _():
    from scripts.ops import selftest
    for path in (MIG, REV, Path(__file__)):
        assert not selftest.scan_text(path.read_text(encoding="utf-8"), []), path.name


@test("documented: docs/MIGRATIONS.md makes r12 v_catalog's owner and names both files")
def _():
    doc = _text(ROOT / "docs" / "MIGRATIONS.md")
    row = next((ln for ln in doc.splitlines() if ln.startswith("| `v_catalog` |")), "")
    assert "r12_b2c_causeway_migration.sql" in row and "daily_ops2_migration.sql" in row, row
    assert "r12_b2c_causeway_reverse.sql" in doc
    section = doc[doc.index('## Release r12 "WEKOME live"'):]
    assert "category_master" in section and "Screen Protector" in section, "the new category is documented"
    head = " ".join(ln.strip().lstrip("-").strip() for ln in _text(MIG).splitlines())
    assert "From the MAIN project folder only" in head and "--rehearse" in head and "audit_grants" in head
    assert "no fallback to modern_trade" in head.lower()


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
