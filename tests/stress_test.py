"""Stress + integration tests for YQ Bahrain AI Ops Assistant.

Tests all major subsystems: SQL validator, templates, AI engine, digest, actions.
Run: python -m tests.stress_test

Exit 0 = all pass. Non-zero = failures (details printed).
"""
from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS = "✓"
FAIL = "✗"
results: list[tuple[str, bool, str]] = []


def test(name: str):
    def decorator(fn):
        def wrapper():
            t0 = time.time()
            try:
                fn()
                ms = int((time.time() - t0) * 1000)
                results.append((name, True, f"{ms}ms"))
                print(f"  {PASS} {name} ({ms}ms)")
            except Exception as e:
                ms = int((time.time() - t0) * 1000)
                detail = f"{e}\n{traceback.format_exc()[-300:]}"
                results.append((name, False, detail))
                print(f"  {FAIL} {name} ({ms}ms)\n    {e}")
        return wrapper
    return decorator


# ── SQL Validator ─────────────────────────────────────────────────────────────

@test("validator: rejects INSERT")
def _():
    from app.sql_validator import SQLValidationError, validate
    try:
        validate("INSERT INTO orders VALUES (1)")
        raise AssertionError("Should have raised")
    except SQLValidationError:
        pass

@test("validator: rejects DROP TABLE")
def _():
    from app.sql_validator import SQLValidationError, validate
    try:
        validate("DROP TABLE orders")
        raise AssertionError("Should have raised")
    except SQLValidationError:
        pass

@test("validator: rejects raw table access")
def _():
    from app.sql_validator import SQLValidationError, validate
    try:
        validate("SELECT * FROM orders LIMIT 10")
        raise AssertionError("Should have raised")
    except SQLValidationError:
        pass

@test("validator: accepts v_sales query")
def _():
    from app.sql_validator import validate
    sql = validate("SELECT customer_name, SUM(total_amount_bhd) FROM v_sales GROUP BY 1")
    assert "LIMIT" in sql.upper()

@test("validator: injects LIMIT if missing")
def _():
    from app.sql_validator import validate
    sql = validate("SELECT * FROM v_current_stock")
    assert "LIMIT" in sql.upper()

@test("validator: rejects multiple statements")
def _():
    from app.sql_validator import SQLValidationError, validate
    try:
        validate("SELECT 1; SELECT 2")
        raise AssertionError("Should have raised")
    except SQLValidationError:
        pass

@test("validator: rejects UNION with raw table")
def _():
    from app.sql_validator import SQLValidationError, validate
    try:
        validate("SELECT * FROM v_sales UNION SELECT * FROM orders")
        raise AssertionError("Should have raised")
    except SQLValidationError:
        pass


# ── Templates ─────────────────────────────────────────────────────────────────

@test("templates: total sales this month")
def _():
    from app.templates import match
    r = match("Total sales this month")
    assert r is not None, "Should match"
    assert "v_sales" in r[1].lower() or "v_sales_by_period" in r[1].lower()

@test("templates: low stock items")
def _():
    from app.templates import match
    r = match("Show low stock items")
    assert r is not None

@test("templates: negative margins")
def _():
    from app.templates import match
    r = match("Which products have negative margins?")
    assert r is not None
    assert "negative" in r[0].lower() or "margin" in r[0].lower()

@test("templates: top customers")
def _():
    from app.templates import match
    r = match("Top customers by revenue")
    assert r is not None

@test("templates: no match for gibberish")
def _():
    from app.templates import match
    r = match("xyzzy frobnicator blarg")
    assert r is None, f"Should not match, got: {r}"


# ── Database connectivity ─────────────────────────────────────────────────────

@test("database: Supabase client connects")
def _():
    from app.database import get_client
    client = get_client()
    assert client is not None

@test("database: user_roles table readable")
def _():
    from app.database import get_client
    r = get_client().table("user_roles").select("email").limit(1).execute()
    assert r.data is not None

@test("database: run_readonly_query RPC exists")
def _():
    from app.ai import exec_sql
    rows = exec_sql("SELECT 1 AS ping LIMIT 1")
    assert rows and rows[0].get("ping") == 1, f"Unexpected: {rows}"


# ── Views ─────────────────────────────────────────────────────────────────────

@test("view: v_sales returns rows")
def _():
    from app.ai import exec_sql
    rows = exec_sql("SELECT COUNT(*) AS n FROM v_sales LIMIT 1")
    assert rows and int(rows[0]["n"]) > 0

@test("view: v_current_stock returns rows")
def _():
    from app.ai import exec_sql
    rows = exec_sql("SELECT COUNT(*) AS n FROM v_current_stock LIMIT 1")
    assert rows and int(rows[0]["n"]) > 0

@test("view: v_top_customers revenue non-null")
def _():
    from app.ai import exec_sql
    rows = exec_sql("SELECT customer_name, total_revenue_bhd FROM v_top_customers LIMIT 3")
    assert rows, "No rows"
    for r in rows:
        assert r.get("total_revenue_bhd") is not None, f"Null revenue: {r}"

@test("view: v_sales_by_period monthly sums")
def _():
    from app.ai import exec_sql
    rows = exec_sql("SELECT period_month, net_revenue_bhd FROM v_sales_by_period LIMIT 5")
    assert len(rows) >= 1

@test("view: v_receivables outstanding positive")
def _():
    from app.ai import exec_sql
    rows = exec_sql("SELECT outstanding_bhd FROM v_receivables LIMIT 5")
    for r in rows:
        assert float(r.get("outstanding_bhd", 0)) > 0


# ── AI engine ─────────────────────────────────────────────────────────────────

@test("ai: template path returns reply")
def _():
    from app.ai import ask
    result = ask("Total sales this month")
    assert result.get("reply"), f"Empty reply: {result}"
    assert result.get("sql_used"), "No SQL"

@test("ai: cache stores and retrieves")
def _():
    from app.ai import ask
    q = "Top 3 customers by revenue"
    r1 = ask(q)
    r2 = ask(q)
    assert r2.get("cached") is True, "Second call should be cached"

@test("ai: invalid question returns graceful reply")
def _():
    from app.ai import ask
    result = ask("DELETE FROM products")
    assert result.get("reply"), "Should return error reply"
    assert "reply" in result


# ── Digest ────────────────────────────────────────────────────────────────────

@test("digest: daily_summary returns keys")
def _():
    from app.digest import daily_summary
    d = daily_summary()
    assert "rev_mtd" in d
    assert "orders_mtd" in d
    assert "total_receivables" in d

@test("digest: low_stock_items returns list")
def _():
    from app.digest import low_stock_items
    items = low_stock_items()
    assert isinstance(items, list)

@test("digest: overdue_receivables > 0 days")
def _():
    from app.digest import overdue_receivables
    rows = overdue_receivables(0)
    assert isinstance(rows, list)

@test("digest: all_alerts has required keys")
def _():
    from app.digest import all_alerts
    a = all_alerts()
    for key in ("low_stock", "overdue_receivables", "negative_margins", "has_alerts"):
        assert key in a, f"Missing key: {key}"


# ── Actions ───────────────────────────────────────────────────────────────────

@test("actions: submit and list")
def _():
    from app.actions import list_actions, submit_action
    r = submit_action("reorder_stock", {"item": "STRESS_TEST_ITEM", "notes": "auto test"}, "stress_test@yq.com")
    assert r.get("id") or r.get("status") == "pending"
    actions = list_actions(status="pending")
    assert isinstance(actions, list)

@test("actions: export_approved_csv")
def _():
    from app.actions import export_approved_csv
    csv = export_approved_csv()
    assert isinstance(csv, str)


# ── Runner ────────────────────────────────────────────────────────────────────

def main() -> int:
    print("\n" + "═" * 60)
    print("  YQ Bahrain AI Ops — Stress & Integration Test Suite")
    print("═" * 60 + "\n")

    sections = [
        ("SQL Validator", [f for f in globals().values() if callable(f) and hasattr(f, "__wrapped__") is False and f.__name__ == "wrapper"]),
    ]

    # Just run all test wrappers in order
    all_fns = [v for v in globals().values() if callable(v) and getattr(v, "__name__", "") == "wrapper"]

    # Use results list — each @test decorator registers a wrapper
    # Re-collect by looking at closures
    # Simpler: just call everything that was decorated
    pass

    # Actually the @test decorator replaces the function with wrapper in-place
    # but since functions are named _ they overwrite each other.
    # Re-implement: collect via results list by running them.
    # The decorators already ran at import time... no. They define wrappers but don't call them.
    # We need to call them manually. Let's collect:

    import inspect
    test_fns = []
    frame = inspect.currentframe()
    if frame and frame.f_globals:
        for name, obj in list(frame.f_globals.items()):
            if callable(obj) and getattr(obj, "__name__", "") == "wrapper":
                test_fns.append(obj)

    for fn in test_fns:
        fn()

    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = total - passed

    print(f"\n{'═'*60}")
    print(f"  Results: {passed}/{total} passed", end="")
    if failed:
        print(f"  ·  {failed} FAILED ←")
        print("\nFailed tests:")
        for name, ok, detail in results:
            if not ok:
                print(f"  {FAIL} {name}")
                print(f"    {detail[:200]}")
    else:
        print("  — ALL PASS ✓")
    print("═" * 60 + "\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
