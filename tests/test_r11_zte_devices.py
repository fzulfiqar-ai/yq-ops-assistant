"""Release r11, "the ZTE handsets are Devices" (owner, 02-Nov-2026): no ZTE sale is ever counted as an accessory sale,
the same as SIM for Batelco (scripts/r11_zte_devices_migration.sql, scripts/category_backfill.py).

    python -m tests.test_r11_zte_devices

Same lightweight runner as tests/test_r10_long_number_mask.py (no pytest). No database, no network, no .env:
SUPABASE_URL, SUPABASE_KEY and DATABASE_URL are pinned to ci.invalid BEFORE any import (a worktree under the main
checkout would otherwise find the real .env). Every customer, amount and invoice is left out; the item names are the
day book's and the ledger's own spellings of the two handsets, and invented accessory names.

Covered:
  * the name rule (division_rules, v_item_division): it matches every spelling of the two handsets (the day book's, the
    ledger's with the code repeated, a later model's) and no accessory name, "Aztec" and "ZTEK" included;
  * the migration, read as text (comments set aside): ASCII only, lock_timeout first, data only (no DROP, CASCADE,
    TRUNCATE, COMMIT, GRANT, REVOKE, view or security_invoker), every INSERT idempotent (ON CONFLICT), the alias never
    taken from another product (DO NOTHING, and a refusal first when one points elsewhere), the category in the Devices
    division, the self-check; every statement outside a DO block parses as Postgres (sqlglot, when installed);
  * the reverse: the same literals, dependents first (rule, aliases, products, category), each delete only of what r11
    added, no DROP or CASCADE, its own self-check;
  * scripts/category_backfill.py (r11) on an in-memory stand-in: the Focus item-group pass never moves a product out of
    a SIM, Devices or Giveaway category (the ZTE handset listed under an accessory group stays a handset), it still
    moves an accessory, and the seed adds new category names only (a curated division is never put back to Telecom);
  * nothing in the two SQL files reads like a live amount (the repo-clean scan of scripts/ops/selftest.py), and the
    release is documented (docs/MIGRATIONS.md, the basis in docs/OPS_SKILLS.md).
"""
from __future__ import annotations

import os

os.environ["SUPABASE_URL"] = "https://ci.invalid"          # before ANY import that could read a .env
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"

import io  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
import traceback  # noqa: E402
import types  # noqa: E402
from pathlib import Path  # noqa: E402

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MIGRATION = ROOT / "scripts" / "r11_zte_devices_migration.sql"
REVERSE = ROOT / "scripts" / "r11_zte_devices_reverse.sql"
PATTERN = r"(^|[^a-z])zte([^a-z]|$)"                       # the SQL literal, run case-insensitively (~*)
NAMES = ("ZTE-A35e MOBILE", "ZTE-A36 MOBILE")               # the day book's item names
CODES = ("ZTE-A35e", "ZTE-A36")

TESTS: list = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


def _sql(path: Path) -> str:
    """The file's SQL with the -- comments set aside (a quote inside a comment is not code)."""
    return re.sub(r"--[^\n]*", "", path.read_text(encoding="utf-8"))


def _statements(sql: str) -> list[str]:
    """The top-level statements, DO blocks kept whole ($$ ... $$ is one string)."""
    out, cur, i, in_dollar = [], "", 0, False
    while i < len(sql):
        if sql.startswith("$$", i):
            in_dollar = not in_dollar
            cur += "$$"
            i += 2
            continue
        ch = sql[i]
        if ch == ";" and not in_dollar:
            if cur.strip():
                out.append(cur.strip())
            cur = ""
        else:
            cur += ch
        i += 1
    if cur.strip():
        out.append(cur.strip())
    return out


def _rule() -> re.Pattern:
    return re.compile(PATTERN, re.I)


# ── the name rule ──────────────────────────────────────────────────────────────────────────────

@test("the ZTE rule matches every spelling of the handsets and no accessory name")
def _():
    rule = _rule()
    for name in NAMES + ("ZTE-A35e MOBILE ZTE-A35e", "ZTE-A36 MOBILE ZTE-A36", "ZTE A55 MOBILE", "zte-blade v50",
                         "MOBILE ZTE"):
        assert rule.search(name), name
    for name in ("X22 CC 1Mtr Aluminium Alloy+Braided Cable (Type c To Type c)", "T06 Airpord Type-C Port",
                 "UK05 Qualcomm QC3.0 Charger USB Port", "AZTEC Holder", "ZTEK Adapter", "BS07 BT Speaker",
                 "F15 PB 10000mah", "SIM Starter Pack", "Gazte cable"):
        assert not rule.search(name), name


# ── the migration ──────────────────────────────────────────────────────────────────────────────

@test("the migration: ASCII, lock_timeout first, data only, every INSERT idempotent, never a view or a grant")
def _():
    raw = MIGRATION.read_text(encoding="utf-8")
    assert raw.isascii()
    sql = _sql(MIGRATION)
    low = " ".join(sql.lower().split())
    stmts = _statements(sql)
    assert stmts[0].lower().startswith("set lock_timeout"), stmts[0]
    for word in (" drop ", "cascade", "truncate", "commit", " grant ", " revoke ", "create or replace view",
                 "create view", "security_invoker", "alter table", "delete from", " update products set"):
        assert word not in f" {low} ", word
    inserts = [s for s in stmts if s.lower().startswith("insert into")]
    assert [s.split()[2] for s in inserts] == ["categories", "products", "product_aliases", "division_rules"]
    assert all("on conflict" in s.lower() for s in inserts)
    alias = next(s for s in inserts if s.split()[2] == "product_aliases").lower()
    assert "on conflict (alias_text) do nothing" in " ".join(alias.split()), "a curated alias always wins"
    category = " ".join(next(s for s in inserts if s.split()[2] == "categories").split())
    assert "('Mobile Handsets', 'Devices')" in category


@test("the migration: refuses an alias that points elsewhere, then proves every ZTE line and name is Devices")
def _():
    stmts = _statements(_sql(MIGRATION))
    blocks = [s for s in stmts if s.lower().startswith("do $$")]
    assert len(blocks) == 2, len(blocks)
    first, last = (" ".join(b.split()) for b in blocks)
    assert stmts.index(blocks[0]) == 1, "the alias refusal runs before anything is written"
    assert "raise exception" in first and "not in ('ZTE-A35e', 'ZTE-A36')" in first
    assert stmts.index(blocks[1]) == len(stmts) - 1, "the self-check runs last"
    for want in ("from v_sales v", "from v_item_division d", "c.division = 'Devices'", "raise exception",
                 "resolved <> 2", "outside <> 0"):
        assert want in last, want


@test("the migration and the reverse name the same codes, names and pattern")
def _():
    for path in (MIGRATION, REVERSE):
        sql = _sql(path)
        for lit in NAMES + CODES + ("Mobile Handsets",):
            assert f"'{lit}'" in sql, (path.name, lit)
        found = set(re.findall(r"'(\(\^\|\[\^a-z\]\)zte\(\[\^a-z\]\|\$\))'", sql))
        assert found == {PATTERN}, (path.name, found)
    mig = _sql(MIGRATION)
    assert f"('{PATTERN}', 'Devices', 25," in " ".join(mig.split())
    assert mig.count(PATTERN) == 3, "the rule, and the two self-check lookups"


@test("the reverse: dependents first, only what r11 added, no DROP or CASCADE, its own self-check")
def _():
    raw = REVERSE.read_text(encoding="utf-8")
    assert raw.isascii()
    sql = _sql(REVERSE)
    low = " ".join(sql.lower().split())
    for word in (" drop ", "cascade", "truncate", "commit", " grant ", " revoke ", "insert into", "create "):
        assert word not in f" {low} ", word
    stmts = _statements(sql)
    assert stmts[0].lower().startswith("set lock_timeout")
    deletes = [" ".join(s.split()) for s in stmts if s.lower().startswith("delete from")]
    assert [d.split()[2] for d in deletes] == ["division_rules", "product_aliases", "products", "categories"]
    assert "and division = 'Devices'" in deletes[0]
    assert "and product_id in (select p.id from products p where p.sku_code in ('ZTE-A35e', 'ZTE-A36'))" in deletes[1]
    assert "not exists (select 1 from product_aliases pa where pa.product_id = p.id)" in deletes[2]
    assert "c.division = 'Devices'" in deletes[3] and "not exists (select 1 from products p" in deletes[3]
    assert stmts[-1].lower().startswith("do $$") and "raise exception" in stmts[-1].lower()


@test("every statement outside a DO block parses as Postgres (sqlglot)")
def _():
    try:
        import sqlglot
    except ImportError:
        print("  SKIP: sqlglot is not installed")
        return
    for path in (MIGRATION, REVERSE):
        for s in _statements(_sql(path)):
            if s.lower().startswith(("do $$", "set ")):
                continue
            sqlglot.parse_one(s, read="postgres")


@test("nothing in the two files reads like a live amount (the repo-clean scan)")
def _():
    from scripts.ops import selftest
    for path in (MIGRATION, REVERSE, Path(__file__)):
        assert not selftest.scan_text(path.read_text(encoding="utf-8"), []), path.name


# ── the category backfill (r11) ────────────────────────────────────────────────────────────────

CATS = [{"id": 1, "name": "Batelco TRA Devices", "division": "Devices"},
        {"id": 2, "name": "Cable", "division": "Accessories"},
        {"id": 8, "name": "Postpaid Giveaway", "division": "Giveaway"},
        {"id": 10, "name": "Sim", "division": "SIM"},
        {"id": 12, "name": "Mobile Handsets", "division": "Devices"},
        {"id": 13, "name": "Uncurated", "division": None}]
PRODUCTS = [{"id": 104, "sku_code": "SIMKIT", "item_name": "SIMKIT thing", "category_id": 10},
            {"id": 105, "sku_code": "GIFTBOX", "item_name": "GIFTBOX thing", "category_id": 8},
            {"id": 106, "sku_code": "TRA1", "item_name": "TRA1 thing", "category_id": 1},
            {"id": 107, "sku_code": "OLD", "item_name": "OLD thing", "category_id": 13},
            {"id": 108, "sku_code": "NOCAT", "item_name": "NOCAT thing", "category_id": None},
            {"id": 109, "sku_code": "ZTE-A35e", "item_name": "ZTE-A35e MOBILE", "category_id": 12}]


class _Q:
    def __init__(self, store, name):
        self.store, self.name, self.op, self.payload, self.ids, self.kw = store, name, "select", None, None, {}

    def select(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def range(self, a, b):
        self.rng = (a, b)
        return self

    def upsert(self, row, **k):
        self.op, self.payload, self.kw = "upsert", row, dict(k)
        return self

    def update(self, vals):
        self.op, self.payload = "update", vals
        return self

    def in_(self, col, ids):
        self.ids = list(ids)
        return self

    def execute(self):
        rows = self.store.tables[self.name]
        if self.op == "select":
            a, b = getattr(self, "rng", (0, 10 ** 6))
            return types.SimpleNamespace(data=[dict(r) for r in rows][a:b + 1])
        if self.op == "update":
            self.store.writes.append((self.name, "update", dict(self.payload), list(self.ids)))
            for r in rows:
                if r.get("id") in self.ids:
                    r.update(self.payload)
            return types.SimpleNamespace(data=[])
        if self.op == "upsert":
            # PostgREST: ignore_duplicates -> ON CONFLICT DO NOTHING; otherwise the row overwrites the one there
            self.store.writes.append((self.name, "upsert", dict(self.payload), dict(self.kw)))
            key = self.kw.get("on_conflict")
            there = next((r for r in rows if key and r.get(key) == self.payload.get(key)), None)
            if there is None:
                rows.append({"id": 100 + len(rows), **self.payload})
            elif not self.kw.get("ignore_duplicates"):
                there.update(self.payload)
            return types.SimpleNamespace(data=[])
        raise AssertionError(self.op)


class _Store:
    def __init__(self, tables):
        self.tables, self.writes = tables, []

    def table(self, name):
        if name not in self.tables:
            raise RuntimeError(f'relation "{name}" does not exist')
        return _Q(self, name)


def _store() -> _Store:
    return _Store({"categories": [dict(c) for c in CATS], "products": [dict(p) for p in PRODUCTS],
                   "product_aliases": [{"alias_text": "ZTE-A35e MOBILE", "product_id": 109}]})


@test("held_products: every product under a SIM, Devices or Giveaway category, none under Accessories or none")
def _():
    from scripts import category_backfill as cb
    assert cb.HELD_DIVISIONS == ("SIM", "Devices", "Giveaway")
    assert cb.held_products(CATS, PRODUCTS) == {104, 105, 106, 109}


@test("the Focus pass: the ZTE handset listed under Cable stays a handset; SIM, giveaway and device products stay; "
      "an accessory still moves; the seed never re-divides a category already there")
def _():
    from scripts import category_backfill as cb
    st = _store()
    pairs = [("ZTE-A35e MOBILE", "Cable"), ("SIMKIT thing", "Cable"), ("GIFTBOX thing", "Cable"),
             ("TRA1 thing", "Batelco TRA Devices"), ("OLD thing", "Cable"), ("NOCAT thing", "Cable"),
             ("NEW thing", "Brand New Group"), ("SIMKIT thing", "Sim"), ("GIFTBOX thing", "Postpaid Giveaway")]
    saved = (cb.parse_categories, cb.get_client)
    cb.parse_categories, cb.get_client = (lambda folder: ("x.xlsx", pairs)), (lambda: st)
    try:
        out = cb.backfill("anywhere")
    finally:
        cb.parse_categories, cb.get_client = saved
    assert out["ok"] and out["category_master"]["ok"] is False          # no master table here: Focus groups stand
    cats = {p["id"]: p["category_id"] for p in st.tables["products"]}
    assert cats[109] == 12, "the ZTE handset must stay under Mobile Handsets (Devices)"
    assert (cats[104], cats[105], cats[106]) == (10, 8, 1), "SIM, giveaway and device products keep their category"
    assert (cats[107], cats[108]) == (2, 2), "an accessory (curated or not) still takes its Focus group"
    moved = {pid for w in st.writes if w[0] == "products" and w[1] == "update" for pid in w[3]}
    assert moved == {107, 108}, moved
    seeds = [w for w in st.writes if w[0] == "categories" and w[1] == "upsert"]
    assert seeds and all(w[3] == {"on_conflict": "name", "ignore_duplicates": True} for w in seeds), seeds
    division = {r["name"]: r["division"] for r in st.tables["categories"]}
    assert (division["Sim"], division["Postpaid Giveaway"], division["Batelco TRA Devices"]) == (
        "SIM", "Giveaway", "Devices"), "a curated division is never put back to the coarse seed"
    assert division["Brand New Group"] == "Accessories", "a new group still gets the coarse seed"


# ── the documents ──────────────────────────────────────────────────────────────────────────────

@test("the release is documented: MIGRATIONS.md names both files, OPS_SKILLS.md's basis names the handsets")
def _():
    mig = (ROOT / "docs" / "MIGRATIONS.md").read_text(encoding="utf-8")
    assert "r11_zte_devices_migration.sql" in mig and "r11_zte_devices_reverse.sql" in mig
    ops = (ROOT / "docs" / "OPS_SKILLS.md").read_text(encoding="utf-8")
    basis = ops[ops.index("- **The basis.**"):][:900]
    assert "ZTE" in basis and "Devices" in basis, basis


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
