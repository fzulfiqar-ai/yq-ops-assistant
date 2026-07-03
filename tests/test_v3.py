"""v3 regression tests — costing chain, reconciled totals, divisions, catalog, tracker.

    python -m tests.test_v3

Lightweight runner (same spirit as tests/stress_test.py) — no pytest dependency.
Live-DB tests are read-only.
"""
from __future__ import annotations

import io
import sys
import traceback
from pathlib import Path

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


# ── costing chain (pure math vs the owner's VFAN pricing sheet) ────────────────

@test("costing: C18 chain matches the owner's sheet (base→landed→sell)")
def _():
    from app.settings import DEFAULTS
    list_rmb = 9.5
    net = list_rmb / (1 + DEFAULTS["dealer_discount"])            # 8.0508
    base = net / DEFAULTS["fx_rmb_usd"] * DEFAULTS["fx_usd_bhd"]  # 0.44677
    landed = base * (1 + DEFAULTS["landing_vat_pct"])             # 0.58080
    sell = landed * (1 + DEFAULTS["target_markup"])               # 0.98737
    assert abs(base - 0.44677) < 0.0005, f"base {base}"
    assert abs(landed - 0.58080) < 0.0007, f"landed {landed}"
    assert abs(sell - 0.98737) < 0.0015, f"sell {sell}"


@test("costing: rmb_to_bhd uses the USD leg")
def _():
    from app.settings import DEFAULTS, rmb_to_bhd
    assert abs(rmb_to_bhd() - DEFAULTS["fx_usd_bhd"] / DEFAULTS["fx_rmb_usd"]) < 1e-9


# ── reconciled truth (live DB, read-only) ──────────────────────────────────────

@test("truth: v_sales total still equals the Focus 03/07 report (52,909.84)")
def _():
    from app.db_read import exec_sql
    t = float(exec_sql("SELECT COALESCE(SUM(revenue_bhd),0) AS s FROM v_sales")[0]["s"])
    assert abs(t - 52909.84) < 0.5, f"total {t}"


@test("truth: cash + credit == total (no leakage in sale_type)")
def _():
    from app.db_read import exec_sql
    rows = exec_sql("SELECT sale_type, SUM(revenue_bhd) AS s FROM v_sales GROUP BY 1")
    assert {r["sale_type"] for r in rows} <= {"cash", "credit"}
    assert abs(sum(float(r["s"]) for r in rows) - 52909.84) < 0.5


@test("truth: divisions sum to total; SIM detected")
def _():
    from app.db_read import exec_sql
    rows = exec_sql("SELECT division, SUM(revenue_bhd) AS s FROM v_sales GROUP BY 1")
    assert abs(sum(float(r["s"]) for r in rows) - 52909.84) < 0.5
    assert any(r["division"] == "SIM" for r in rows), "SIM division missing"


@test("truth: collections agent total == dashboard overdue (same basis)")
def _():
    from app.agents import collections
    from app.digest import daily_summary
    c = collections()
    s = daily_summary()
    assert abs(c["total_overdue_bhd"] - s["overdue_receivables_bhd"]) < 0.01, \
        f"agent {c['total_overdue_bhd']} vs dashboard {s['overdue_receivables_bhd']}"
    assert c["count"] == s["overdue_accounts"]


# ── feature access ─────────────────────────────────────────────────────────────

@test("access: single feature source has the new pages + salesman role")
def _():
    from app.features import FEATURES, ROLE_DEFAULT_FEATURES, ROLES
    for f in ("Live Feed", "Orders", "Stock Movement", "Leads", "Catalog"):
        assert f in FEATURES, f"missing feature {f}"
    assert "salesman" in ROLES
    assert ROLE_DEFAULT_FEATURES["salesman"] == ["Catalog"]
    from app.user_auth import FEATURES as REEXP
    assert REEXP is FEATURES, "user_auth must re-export app.features.FEATURES"


# ── catalog ────────────────────────────────────────────────────────────────────

@test("catalog: covers every active price-book SKU (auto-sync has nothing to add)")
def _():
    from app.catalog import sync_from_price_book
    assert sync_from_price_book() == 0, "price-book SKUs missing from catalog"


@test("catalog: v_catalog serves items with live standard rates + photos")
def _():
    from app.db_read import exec_sql
    r = exec_sql("SELECT COUNT(*) AS n, COUNT(product_image_url) AS imgs, "
                 "COUNT(standard_rate) AS rated FROM v_catalog")[0]
    assert int(r["n"]) >= 190, f"only {r['n']} items"
    assert int(r["imgs"]) >= 75, f"only {r['imgs']} photos"
    assert int(r["rated"]) >= 150, f"only {r['rated']} live rates"


# ── price tracker ──────────────────────────────────────────────────────────────

@test("tracker: v_price_tracker has history + margins")
def _():
    from app.db_read import exec_sql
    r = exec_sql("SELECT COUNT(*) AS n, COUNT(sell_prev) AS prev, "
                 "COUNT(margin_now_pct) AS m FROM v_price_tracker")[0]
    assert int(r["n"]) >= 150 and int(r["prev"]) >= 100 and int(r["m"]) >= 30, str(r)


# ── agents ────────────────────────────────────────────────────────────────────

@test("agents: 35 registered; growth trio runs with real output")
def _():
    from app.agents import AGENTS, growth_plan, marketing, sales_outreach, sales_push
    assert len(AGENTS) >= 35
    for name in ("sales_push", "sales_outreach", "growth_plan", "marketing"):
        assert name in AGENTS
    assert sales_push()["count"] >= 0
    assert "drafts" in sales_outreach()
    assert marketing()["count"] == 3
    gp = growth_plan()
    assert gp["count"] >= 1 and gp["plan"][0].get("move")


def main() -> int:
    passed = failed = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception:  # noqa: BLE001
            print(f"  FAIL  {name}")
            traceback.print_exc(limit=2)
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
