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
  * (scripts/category_backfill.py's guard ships with the next API deploy, branch zte-backfill-guard, with its tests);
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
