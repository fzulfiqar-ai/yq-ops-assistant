# -*- coding: utf-8 -*-
"""Stress + integration tests for YQ Bahrain AI Ops Assistant.

Tests: SQL validator, templates, DB connectivity, views, AI engine, digest, actions.
Run:   python -m tests.stress_test
Exit:  0 = all pass, 1 = failures
"""
from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

results: list[tuple[str, bool, str]] = []
_REGISTRY: list = []   # ordered list of (name, fn) — populated by @test decorator


def test(name: str):
    def decorator(fn):
        def wrapper():
            t0 = time.time()
            try:
                fn()
                ms = int((time.time() - t0) * 1000)
                results.append((name, True, f"{ms}ms"))
                print(f"  PASS  {name} ({ms}ms)")
            except Exception as e:
                ms = int((time.time() - t0) * 1000)
                detail = traceback.format_exc()[-400:]
                results.append((name, False, f"{e}\n{detail}"))
                print(f"  FAIL  {name} ({ms}ms) -- {e}")
        _REGISTRY.append(wrapper)
        return wrapper
    return decorator


# ── SQL Validator ─────────────────────────────────────────────────────────────

@test("validator: rejects INSERT")
def _():
    from app.sql_validator import SQLValidationError, validate
    try:
        validate("INSERT INTO orders VALUES (1)")
        raise AssertionError("Should have raised SQLValidationError")
    except SQLValidationError:
        pass


@test("validator: rejects DROP TABLE")
def _():
    from app.sql_validator import SQLValidationError, validate
    try:
        validate("DROP TABLE orders")
        raise AssertionError("Should have raised SQLValidationError")
    except SQLValidationError:
        pass


@test("validator: rejects raw table access")
def _():
    from app.sql_validator import SQLValidationError, validate
    try:
        validate("SELECT * FROM orders LIMIT 10")
        raise AssertionError("Should have raised SQLValidationError")
    except SQLValidationError:
        pass


@test("validator: accepts v_sales query")
def _():
    from app.sql_validator import validate
    sql = validate("SELECT customer_name, SUM(total_amount_bhd) FROM v_sales GROUP BY 1")
    assert "LIMIT" in sql.upper(), f"No LIMIT injected: {sql}"


@test("validator: injects LIMIT if missing")
def _():
    from app.sql_validator import validate
    sql = validate("SELECT * FROM v_current_stock")
    assert "LIMIT" in sql.upper(), f"No LIMIT: {sql}"


@test("validator: rejects multiple statements")
def _():
    from app.sql_validator import SQLValidationError, validate
    try:
        validate("SELECT 1; SELECT 2")
        raise AssertionError("Should have raised SQLValidationError")
    except SQLValidationError:
        pass


@test("validator: rejects UNION with raw table")
def _():
    from app.sql_validator import SQLValidationError, validate
    try:
        validate("SELECT * FROM v_sales UNION SELECT * FROM orders")
        raise AssertionError("Should have raised SQLValidationError")
    except SQLValidationError:
        pass


# ── Templates ─────────────────────────────────────────────────────────────────

@test("templates: total sales this month")
def _():
    from app.templates import match
    r = match("Total sales this month")
    assert r is not None, "Should match"


@test("templates: low stock items")
def _():
    from app.templates import match
    r = match("Show low stock items")
    assert r is not None, "Should match"


@test("templates: negative margins")
def _():
    from app.templates import match
    r = match("Which products have negative margins?")
    assert r is not None, "Should match"


@test("templates: top customers")
def _():
    from app.templates import match
    r = match("Top customers by revenue")
    assert r is not None, "Should match"


@test("templates: no match for gibberish")
def _():
    from app.templates import match
    r = match("xyzzy frobnicator blarg 12345")
    assert r is None, f"Should NOT match, got: {r}"


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


@test("database: run_readonly_query RPC works")
def _():
    from app.ai import exec_sql
    rows = exec_sql("SELECT 1 AS ping LIMIT 1")
    assert rows and rows[0].get("ping") == 1, f"Unexpected result: {rows}"


# ── Views ─────────────────────────────────────────────────────────────────────

@test("view: v_sales has rows")
def _():
    from app.ai import exec_sql
    rows = exec_sql("SELECT COUNT(*) AS n FROM v_sales LIMIT 1")
    assert rows and int(rows[0]["n"]) > 0, f"Empty v_sales: {rows}"


@test("view: v_current_stock has rows")
def _():
    from app.ai import exec_sql
    rows = exec_sql("SELECT COUNT(*) AS n FROM v_current_stock LIMIT 1")
    assert rows and int(rows[0]["n"]) > 0, f"Empty v_current_stock: {rows}"


@test("view: v_top_customers revenue non-null")
def _():
    from app.ai import exec_sql
    rows = exec_sql("SELECT customer_name, total_revenue_bhd FROM v_top_customers LIMIT 3")
    assert rows, "v_top_customers returned no rows"
    for r in rows:
        assert r.get("total_revenue_bhd") is not None, f"Null revenue in row: {r}"


@test("view: v_sales_by_period has monthly rows")
def _():
    from app.ai import exec_sql
    rows = exec_sql("SELECT period_month, net_revenue_bhd FROM v_sales_by_period LIMIT 5")
    assert len(rows) >= 1, "v_sales_by_period returned no rows"


@test("view: v_receivables outstanding positive")
def _():
    from app.ai import exec_sql
    rows = exec_sql("SELECT outstanding_bhd FROM v_receivables LIMIT 5")
    for r in rows:
        assert float(r.get("outstanding_bhd", 0)) > 0, f"Non-positive outstanding: {r}"


@test("view: v_low_stock is velocity-based (urgent/low only)")
def _():
    from app.ai import exec_sql
    rows = exec_sql("SELECT item_name, balance_qty, status FROM v_low_stock LIMIT 5")
    for r in rows:
        assert r.get("status") in ("urgent_out_of_stock", "low_stock"), \
            f"unexpected status in v_low_stock: {r}"


# ── AI engine ─────────────────────────────────────────────────────────────────

@test("ai: template path returns reply and SQL")
def _():
    from app.ai import ask
    result = ask("Total sales this month")
    assert result.get("reply"), f"Empty reply: {result}"
    assert result.get("sql_used"), f"No SQL returned: {result}"


@test("ai: result is cached on second call")
def _():
    from app.ai import ask
    q = "Top customers by revenue"
    ask(q)  # prime cache
    r2 = ask(q)
    assert r2.get("cached") is True, "Second call should be cached"


@test("ai: SQL injection blocked gracefully")
def _():
    from app.ai import ask
    result = ask("DELETE FROM products WHERE 1=1")
    assert result.get("reply"), "Should return a reply even for bad input"


@test("ai: unknown question returns reply without crash")
def _():
    from app.ai import ask
    result = ask("What is the weather in Bahrain today?")
    assert "reply" in result, "Must always return reply key"


# ── Digest ────────────────────────────────────────────────────────────────────

@test("digest: daily_summary has required keys")
def _():
    from app.digest import daily_summary
    d = daily_summary()
    for k in ("rev_mtd", "orders_mtd", "total_receivables", "top_customers"):
        assert k in d, f"Missing key: {k}"


@test("digest: rev_mtd is numeric")
def _():
    from app.digest import daily_summary
    d = daily_summary()
    assert isinstance(d["rev_mtd"], (int, float)), f"rev_mtd not numeric: {d['rev_mtd']}"


@test("digest: low_stock_items returns list")
def _():
    from app.digest import low_stock_items
    items = low_stock_items()
    assert isinstance(items, list), f"Expected list, got {type(items)}"


@test("digest: all_alerts has required keys")
def _():
    from app.digest import all_alerts
    a = all_alerts()
    for k in ("low_stock", "overdue_receivables", "negative_margins", "has_alerts"):
        assert k in a, f"Missing key: {k}"


# ── Actions ───────────────────────────────────────────────────────────────────

@test("actions: submit creates pending action")
def _():
    from app.actions import list_actions, submit_action
    submit_action("reorder_stock", {"item": "STRESS_TEST", "notes": "auto"}, "stress@yq.com")
    actions = list_actions(status="pending")
    assert isinstance(actions, list)
    assert len(actions) >= 1


@test("actions: export_approved_csv returns string")
def _():
    from app.actions import export_approved_csv
    csv = export_approved_csv()
    assert isinstance(csv, str)


# ── Runner ────────────────────────────────────────────────────────────────────

def main() -> int:
    print("\n" + "=" * 60)
    print("  YQ Bahrain AI Ops - Stress & Integration Tests")
    print("=" * 60 + "\n")

    for fn in _REGISTRY:
        fn()

    # Wipe test data so stress tests never pollute production
    try:
        from app.database import get_client
        c = get_client()
        c.table("pending_actions").delete().eq("requested_by", "stress@yq.com").execute()
        c.table("query_cache").delete().in_("question", [
            "Top customers by revenue", "Total sales this month",
            "Top 3 customers by revenue", "DELETE FROM products WHERE 1=1",
            "What is the weather in Bahrain today?",
        ]).execute()
        print("  [cleanup] Test data wiped from Supabase.")
    except Exception as e:
        print(f"  [cleanup] Warning: could not wipe test data: {e}")

    total  = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = total - passed

    print(f"\n{'='*60}")
    if failed:
        print(f"  Results: {passed}/{total} passed -- {failed} FAILED")
        print("\nFailed tests:")
        for name, ok, detail in results:
            if not ok:
                print(f"  FAIL  {name}")
                print(f"        {str(detail)[:300]}")
    else:
        print(f"  Results: {passed}/{total} -- ALL PASS")
    print("=" * 60 + "\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
