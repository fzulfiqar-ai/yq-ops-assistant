"""Browser render check of the office admin pages (polish stream, 28-Oct-2026) — synthetic data only.

    cd web && npm run build && npx vite preview --outDir dist --port 5182 --strictPort
    python web/scripts/admin_render_check.py [--url http://localhost:5182] [--out <dir>] [--only case,case]

A fake Supabase session in localStorage gets the SPA past its sign-in check (the session is valid nowhere —
the same stand-in as scripts/perf_auth_check.py). EVERY request leaves the browser only to the preview server:
the API host (VITE_API_URL in web/.env) is answered from the invented fixtures below, or with a 500 / a delay
for the failure and loading cases; Supabase answers {}; anything else is aborted. No account, no API, no
database, no network. Every shop, rep, code and amount below is invented.

Each case opens one page and checks what it says — a failed read must show its error with Retry, never an empty
answer; a page still loading shows its skeleton, never "nothing here" — and the behaviours that need a browser:
one POST for a double click, a cancel that asks first, a table that keeps its scroll when an order opens and
closes, Load more, the sentinel growing a table, a data upload re-reading the freshness chip, a phone's
sideways-scrolling table. Screenshots go to --out. One JSON line per case; exit 1 if any case failed.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import re
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright

UID = "00000000-0000-4000-8000-00000000qa01"
ME = {"email": "qa-admin@example.com", "role": "admin", "features": [], "full_name": "QA Admin",
      "must_reset": False, "read_only": False}
TODAY = "2026-10-27"


def supabase_ref(web: Path) -> str:
    for line in (web / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("VITE_SUPABASE_URL="):
            return urlparse(line.split("=", 1)[1].strip()).hostname.split(".")[0]
    raise SystemExit("VITE_SUPABASE_URL missing from web/.env")


def api_host(web: Path) -> str:
    for line in (web / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("VITE_API_URL="):
            return urlparse(line.split("=", 1)[1].strip()).netloc
    return "localhost:8001"


def fake_session() -> dict:
    now = int(time.time())
    b64 = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")  # noqa: E731
    jwt = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64({'sub': UID, 'exp': now + 7200, 'role': 'authenticated'})}.x"
    user = {"id": UID, "aud": "authenticated", "role": "authenticated", "email": ME["email"],
            "app_metadata": {}, "user_metadata": {}, "created_at": "2026-09-01T00:00:00Z"}
    return {"access_token": jwt, "token_type": "bearer", "expires_in": 7200, "expires_at": now + 7200,
            "refresh_token": "qa-not-a-token", "user": user}


# ── synthetic fixtures (invented codes, names and amounts) ──────────────────────────────────────────────

def order_row(i: int, status: str = "new") -> dict:
    return {"id": 10_000 - i, "order_no": f"QA-{10_000 - i}", "status": status, "customer_name": f"Test Shop {i}",
            "customer_shop": f"Shop {i}", "customer_area": "Area", "salesman_name": "Rep A", "salesman_id": 1,
            "total_bhd": 21.5, "items_count": 3, "units_count": 12, "created_at": "2026-10-20T08:00:00Z",
            "notify_failed": False, "notify_attempts": 1}


def shop_orders(q: dict) -> dict:
    off = int(q.get("offset", ["0"])[0])
    lim = int(q.get("limit", ["100"])[0])
    total = 342
    rows = [order_row(i) for i in range(off, min(total, off + lim))]
    return {"orders": rows, "count": total, "counts": {"new": 120, "confirmed": 100, "delivered": 100, "cancelled": 22}}


def order_detail(oid: int) -> dict:
    o = order_row(10_000 - oid)
    o.update({"lines": [{"id": 1, "item_code": "TST-A", "display_name": "Test cable", "qty": 4, "unit_price_bhd": 1.5,
                          "line_total_bhd": 6.0}], "events": [], "actions": ["confirm", "cancel"], "subtotal_bhd": 21.5,
              "delivery_bhd": 0, "discount_bhd": 0})
    return o


DASH = {
    "data_as_of": TODAY, "scope": {"division": "Accessories", "text": "Mobile Accessories only (SIM excluded)"},
    "actions": [{"action": "Chase 3 overdue test accounts", "to": "/receivables", "bhd": 120, "urgency": 3},
                {"action": "Reorder TST-A before it sells out", "to": "/inventory", "bhd": 0, "urgency": 2},
                {"action": "Check 2 test transfers not received", "to": "/stock", "bhd": 40, "urgency": 1}],
    "kpis": {"rev_today": 0, "net_today": 0, "orders_today": 0, "rev_yesterday": 0, "orders_yesterday": 0, "rev_mtd": 0,
             "net_mtd": 0, "orders_mtd": 0, "rev_prev_month": 0, "total_receivables": 900, "low_stock_count": 7,
             "overdue_count": 3, "overdue_total_bhd": 120},
    "command": {"sales_mtd": {"key": "sales_mtd", "label": "MTD", "basis": "test basis", "value": 4321, "delta_pct": 5.2, "invoices": 88},
                "sales_day": {"key": "sales_day", "label": "Day", "basis": "test basis", "value": 210, "delta_pct": -3.1, "invoices": 6,
                              "compare": {"value": 220, "label": "26 Oct"}},
                "channels": {"key": "channels", "label": "Channels", "basis": "b", "value": 4321,
                             "chips": [{"label": "B2B", "value": 4000, "share_pct": 92.6}, {"label": "B2C", "value": 321, "share_pct": 7.4}]},
                "ar_total": {"key": "ar", "label": "AR", "basis": "b", "value": 900, "source": "focus_total"},
                "ar_over90": {"key": "ar90", "label": "AR90", "basis": "b", "value": 12.5},
                "compare_basis": "the same 19 business days last month", "focus": {"start": "2026-10-01", "end": TODAY, "label": "1–27 Oct", "business_days": 19},
                "day": {"start": TODAY, "end": TODAY, "label": "27 Oct", "business_days": 1}, "day_compare": "26 Oct"},
    "revenue_trend_acc": [{"period_month": f"2026-{m:02d}-01", "acc_net_bhd": 3000 + m * 100, "invoices": 50, "partial": m == 10,
                           "through": TODAY if m == 10 else None} for m in range(1, 11)],
    "top_customers_acc": [{"customer_name": f"Test Shop {i}", "net_bhd": 500 - i * 40, "invoices": 4} for i in range(5)],
    "by_salesman": [{"salesman": f"Rep {c}", "orders": 10, "qty": 40, "revenue_bhd": 900 - i * 100, "net_bhd": 820 - i * 90}
                    for i, c in enumerate("ABC")],
    "alerts": {"negative_margin_count": 0},
    "daily_mtd": [{"day": f"2026-10-{d:02d}", "acc_bhd": 200 + d, "acc_net_bhd": 180 + d} for d in range(1, 28)],
    "payment_split_acc": {"rows": [{"sale_type": "cash", "net_bhd": 1200, "invoices": 30}, {"sale_type": "credit", "net_bhd": 3121, "invoices": 58}], "giveaway_qty": 4},
    "pace": {"target_bhd": 6000, "mtd_bhd": 4321, "prev_month_bhd": 5000, "projected_bhd": 5100, "target_pct": 72, "on_track": False,
             "business_days_total": 22, "basis_text": "ex-VAT, Accessories, business days"},
    "health": {"gp_bhd": 1200, "gp_pct": 27.8, "ar_overdue_pct": 13.3, "dso_days": 40, "dead_stock_bhd": 300, "dead_stock_count": 9,
               "stock_basis": "cost", "dso_basis": "receivables over sales", "dso_receivables_bhd": 900, "dso_sales_per_day_bhd": 22},
    "movers": {"rising": [{"item_name": f"TST-R{i} test cable", "sold_30d": 40 - i, "sold_90d": 60, "momentum": 2 - i / 10} for i in range(5)],
               "falling": [{"item_name": f"TST-F{i} test charger", "sold_30d": 1, "sold_90d": 30, "momentum": 0.1, "current_stock": 50} for i in range(5)]},
}

FIX = {
    "/me": ME,
    "/auth/features": {"features": []},
    "/freshness": {"focus_to": TODAY, "focus_label": "27 Oct", "focus_days_behind": 1, "stale": False, "marketplace_live": True},
    "/report/dashboard": DASH,
    "/purchase-orders": {"recent": [{"po_no": "QA-PO-1", "po_date": "2026-10-01", "vendor": "Test Vendor", "lines": 4, "value_bhd": 300, "received": True}],
                         "cost_changes": [], "on_order": [],
                         "proposal": {"count": 2, "urgent_count": 1, "vendor_count": 1, "est_total_bhd": 120, "summary": "2 test items to reorder",
                                      "lines": [], "by_vendor": [{"vendor": "Test Vendor", "lines": 2, "est_total_bhd": 120, "draft_message": "test",
                                                                  "items": [{"item_name": f"TST-{c} a long synthetic item name for width", "current_stock": 2, "avg_daily": 1,
                                                                             "days_cover": 2, "suggested_qty": 50, "cover_at_qty_days": 50, "last_vendor": "Test Vendor",
                                                                             "last_rate_bhd": 1.2, "vfan_rmb": 10, "vfan_change_pct": 8, "cost_bhd": 1.2,
                                                                             "sell_bhd": 2.5, "margin_pct": 40, "est_cost_bhd": 60, "flags": [], "urgency": "urgent",
                                                                             "reason": "sells 1 a day, 2 days of cover left"} for c in "AB"]}]}},
    "/procurement/board": {"stages": [{"key": "draft", "label": "Draft"}, {"key": "reviewed", "label": "Reviewed"}, {"key": "ordered", "label": "Ordered"},
                                      {"key": "received", "label": "Received"}, {"key": "closed", "label": "Closed"}, {"key": "cancelled", "label": "Cancelled"}],
                           "orders": [{"id": 7, "ref": "PR-7", "title": "Test reorder", "vendor": "Test Vendor", "stage": "reviewed", "est_value_bhd": 120,
                                       "po_no": None, "note": None, "days_in_stage": 2, "sla_days": 5, "is_stuck": False}],
                           "open_count": 1, "stuck_count": 0, "pipeline_value_bhd": 120, "summary": ""},
    "/supplier-prices": {"count": 1, "changed_count": 1, "rows": [{"model": "TST-A", "latest_rmb": 10, "prev_rmb": 9, "change_pct": 11.1,
                                                                    "latest_date": "2026-10-01", "latest_invoice": "INV-1", "invoice_count": 2}]},
    "/shop/me": {"salesman": None},
    "/shop/assignment-queue": {"orders": [], "count": 0, "sla_min": 120},
    "/shop/focus/candidates": {"rows": [], "count": 0},
    "/stock/daily": {"month": "2026-10", "months": ["2026-10", "2026-09"], "warehouses": ["Main", "Van 1"], "gap_days": 0,
                     "last_data_day": TODAY, "snapshot_deltas": [],
                     "days": [{"day": f"2026-10-{d:02d}", "in_qty": 10, "out_qty": 8, "net_qty": 2, "receipts_qty": 5, "transfer_out_qty": 3,
                               "transfer_in_qty": 3, "sales_qty": 5, "returns_qty": 1, "adjustment_qty": 0, "sales_value_bhd": 20,
                               "transfer_out_value_bhd": 9, "received_value_bhd": 15, "in_value_bhd": 30, "out_value_bhd": 25,
                               "vouchers": 2, "item_lines": 4, "has_data": True} for d in range(1, 28)]},
    "/stock/flow": {"warehouse": "Main", "direction": "out", "days": 30, "rows": [], "count": 0, "summary": {}, "partners": [], "basis": "contents", "as_of": TODAY},
    "/stock/transfers": {"transfers": [{"transfer_date": "2026-10-20", "item_name": "TST-A", "from_warehouse": "Main", "to_warehouse": "Van 1",
                                        "qty": 5, "value_bhd": 6}]},
    "/stock/recon": {"vans": [], "as_of": TODAY},
    "/data/coverage": [{"report": "Sales_day_book", "label": "Sales", "cadence": "daily", "data_until": TODAY, "days_behind": 1, "status": "current"}],
    "/ingest/purge-targets": {"targets": []},
    "/ingest/batches": {"batches": [], "migration_applied": True},
    "/report/inventory": {"rows": [{"item_name": "TST-A", "current_stock": 4, "stock_value": 8, "sold_90d": 30, "days_cover": 4,
                                    "suggested_reorder_qty": 20, "status": "low_stock"}], "by_status": {"low_stock": 1},
                          "stock_value": 8, "stock_qty": 4, "by_warehouse": []},
    "/report/sales": {"trend": [{"period_month": f"2026-{m:02d}-01", "gross_bhd": 3000 + m * 90, "net_revenue_bhd": 2800} for m in range(1, 11)],
                      "by_salesman": [{"salesman": n, "orders": 9, "qty": 30, "revenue_bhd": 900 - i * 120, "net_bhd": 800} for i, n in enumerate(["Rep A", "Rep B", "Rep C"])],
                      "by_channel": [{"channel": "B2B", "orders": 20, "qty": 80, "revenue_bhd": 2000, "net_bhd": 1800}],
                      "top_sellers": [{"item_name": "TST-A", "category_name": "CABLES", "qty": 40, "revenue_bhd": 80}],
                      "top_customers": [{"customer_name": "Test Shop 1", "total_revenue_bhd": 300, "order_count": 4, "last_order_date": TODAY}],
                      "daily_by_salesman": [{"day": f"2026-10-{d:02d}", "salesman": n, "gross_bhd": 30 + d} for d in range(1, 10) for n in ["Rep A", "Rep B", "Rep C"]]},
    "/settings/costing": {"fx_rmb_usd": 7.1, "fx_usd_bhd": 0.376, "dealer_discount": 0.18, "landing_vat_pct": 0.3, "target_markup": 0.7, "monthly_sales_target_bhd": 6000},
    "/settings/shop": {"settings": {"shop_min_order_bhd": "20", "shop_areas": "Area A,Area B", "shop_market_promises": "[]"}},
    "/shop/salesmen": {"salesmen": [{"id": 1, "name": "Rep A"}]},
    "/settings/agents": {"exclude_sim": True},
    "/shop/audit": {"rows": [], "count": 0},
    "/catalog": {"items": [], "count": 0, "categories": []},
    "/report/receivables": {"rows": [{"account": f"Test Account {i}", "group_name": "Trade", "outstanding_bhd": 100 + i,
                                       "overdue_bhd": 10 * (i % 3), "over_90_bhd": 0} for i in range(40)],
                            "total": 4780, "over_90": 0, "count": 40, "overdue_count": 26,
                            "buckets": {"0_30": 3000, "31_60": 1000, "61_90": 780, "over_90": 0}},
}


def answer(path: str, query: dict):
    if path == "/shop/orders":
        return shop_orders(query)
    m = re.fullmatch(r"/shop/orders/(\d+)", path)
    if m:
        return order_detail(int(m.group(1)))
    return FIX.get(path, {})


async def run_case(base: str, web: Path, out: Path, case: dict) -> dict:
    ref = supabase_ref(web)
    api = api_host(web)
    fail = set(case.get("fail", []))
    slow = set(case.get("slow", []))
    hits: list[str] = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        vw = case.get("viewport", (1366, 860))
        ctx = await browser.new_context(viewport={"width": vw[0], "height": vw[1]}, reduced_motion=case.get("reduced", "no-preference"),
                                        has_touch=case.get("touch", False), is_mobile=case.get("mobile", False))
        init = {f"sb-{ref}-auth-token": json.dumps(fake_session()), "yq-intro-v2": "done", "yq-role": "admin",
                "yq-me-v1": json.dumps({"uid": UID, "me": ME, "at": int(time.time() * 1000)})}
        await ctx.add_init_script(
            "(() => { if (sessionStorage.getItem('qa-seeded')) return; sessionStorage.setItem('qa-seeded','1');"
            f" const kv = {json.dumps(init)}; for (const k in kv) localStorage.setItem(k, kv[k]); }})()")

        async def router(route):
            u = urlparse(route.request.url)
            if u.netloc == urlparse(base).netloc:
                return await route.continue_()
            cors = {"access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "*"}
            if route.request.method == "OPTIONS":
                return await route.fulfill(status=204, headers=cors)
            if u.netloc == api:
                hits.append((route.request.method + " " if route.request.method != "GET" else "") + (f"{u.path}?{u.query}" if u.query else u.path))
                key = u.path if not re.fullmatch(r"/shop/orders/\d+", u.path) else "/shop/orders/:id"
                if key in slow:
                    await asyncio.sleep(case.get("delay", 6))
                if key in fail:
                    return await route.fulfill(status=500, headers={**cors, "content-type": "application/json"},
                                               body=json.dumps({"detail": "Server error", "ref": "qa0001"}))
                body = answer(u.path, parse_qs(u.query))
                return await route.fulfill(status=200, headers={**cors, "content-type": "application/json"}, body=json.dumps(body))
            if u.netloc.startswith(ref):
                return await route.fulfill(status=200, headers={**cors, "content-type": "application/json"}, body="{}")
            return await route.abort()

        await ctx.route("**/*", router)
        page = await ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(base.rstrip("/") + case["path"], wait_until="domcontentloaded")
        await page.wait_for_timeout(case.get("wait_ms", 3500))
        result = {"case": case["name"], "ok": True, "notes": [], "_hits": hits}
        try:
            body = (await page.inner_text("body")).lower()
            for s in case.get("expect", []):
                if s.lower() not in body:
                    result["ok"] = False
                    result["notes"].append(f"missing: {s!r}")
            for s in case.get("forbid", []):
                if s.lower() in body:
                    result["ok"] = False
                    result["notes"].append(f"must not show: {s!r}")
            if case.get("act"):
                await case["act"](page, result)
            if case.get("check"):
                await case["check"](page, result)
        except Exception as e:  # noqa: BLE001
            result["ok"] = False
            result["notes"].append(f"error: {e}")
        if errors:
            result["ok"] = False
            result["notes"].append(f"page errors: {errors[:3]}")
        await page.screenshot(path=str(out / f"{case['name']}.png"), full_page=case.get("full", True))
        result["api_calls"] = len(hits)
        result["hits_sample"] = hits[-6:]
        result.pop("_hits", None)
        await page.unroute_all(behavior="ignoreErrors")
        await ctx.unroute_all(behavior="ignoreErrors")
        await browser.close()
    return result


# ── cases ───────────────────────────────────────────────────────────────────────────────────────────

async def shop_orders_load_more(page, result):
    await page.get_by_text("Latest 100 of 342 orders").wait_for(timeout=5000)
    await page.get_by_role("button", name=re.compile(r"Load 100 more")).click()
    await page.wait_for_timeout(1500)
    body = await page.inner_text("body")
    if "Latest 200 of 342 orders" not in body:
        result["ok"] = False
        result["notes"].append("Load more did not reach 200")


async def shop_orders_scroll_kept(page, result):
    scroller = page.locator("table").first.locator("xpath=..")
    await scroller.evaluate("el => el.scrollTo({ top: 1200 })")
    await page.wait_for_timeout(300)
    # an order button already in view at that scroll (Playwright would scroll another one into view)
    name = await scroller.evaluate("""el => { const r = el.getBoundingClientRect();
        const b = [...el.querySelectorAll('tbody button')].find((x) => { const q = x.getBoundingClientRect(); return q.top > r.top + 60 && q.bottom < r.bottom - 10 });
        return b ? b.textContent.trim() : null }""")
    await page.get_by_role("button", name=name, exact=True).first.click()
    await page.wait_for_timeout(1200)
    opened = await scroller.evaluate("el => el.scrollTop")
    await page.get_by_role("button", name="Close").first.click()
    await page.wait_for_timeout(800)
    after = await scroller.evaluate("el => el.scrollTop")
    result["notes"].append(f"clicked {name}; scrollTop at 1200, with the drawer open {opened}, after closing {after}")
    if abs(opened - 1200) > 2 or abs(after - 1200) > 2:
        result["ok"] = False


async def drawer_error(page, result):
    await page.get_by_role("button", name="QA-10000").first.click()
    await page.wait_for_timeout(2500)
    dialog_text = await page.locator("div.fixed.inset-0.z-50").inner_text()
    if "Retry" not in dialog_text:
        result["ok"] = False
        result["notes"].append("the drawer shows no Retry")


async def orders_double_click(page, result):
    btn = page.get_by_role("button", name=re.compile("Start order"))
    await btn.dblclick()
    await btn.click(force=True)
    await page.wait_for_timeout(800)
    disabled = await btn.is_disabled()
    posts = [h for h in result["_hits"] if h.startswith("POST /procurement/orders")]
    result["notes"].append(f"POSTs={len(posts)} disabled_while_saving={disabled}")
    if len(posts) != 1 or not disabled:
        result["ok"] = False


async def pipeline_cancel_asks(page, result):
    await page.get_by_role("button", name="Cancel", exact=True).click()
    await page.wait_for_timeout(300)
    body = await page.inner_text("body")
    posts = [h for h in result["_hits"] if "/advance" in h]
    if "Cancel this order?" not in body or posts:
        result["ok"] = False
        result["notes"].append(f"asked={'Cancel this order?' in body} posts={posts}")
    await page.get_by_role("button", name="Keep it").click()
    await page.wait_for_timeout(300)
    if "Cancel this order?" in await page.inner_text("body"):
        result["ok"] = False
        result["notes"].append("Keep it did not close the question")


async def orders_phone_scroll(page, result):
    sw = await page.evaluate("""() => { const t = document.querySelector('table'); const s = t && t.parentElement;
        return s ? [s.scrollWidth, s.clientWidth, getComputedStyle(s).overflowX] : null }""")
    result["notes"].append(f"proposal table scroller {sw}")
    if not sw or sw[0] <= sw[1] or sw[2] != "auto":
        result["ok"] = False


async def data_upload_refreshes(page, result):
    before = sum(1 for h in result["_hits"] if h == "/freshness")
    await page.set_input_files("input[type=file][multiple]", files=[{"name": "Sales_day_book_test.xlsx", "mimeType":
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "buffer": b"synthetic"}])
    await page.get_by_role("button", name=re.compile(r"Upload & refresh")).click()
    await page.wait_for_timeout(2500)
    after = sum(1 for h in result["_hits"] if h == "/freshness")
    posted = [h for h in result["_hits"] if h.startswith("POST /ingest")]
    result["notes"].append(f"POST /ingest={len(posted)} /freshness reads before={before} after={after}")
    if not posted or after <= before:
        result["ok"] = False


async def table_phone_cards(page, result):
    tables = await page.locator("main table").count()
    cards = await page.get_by_text("On hand", exact=True).count()
    result["notes"].append(f"tables={tables} card labels={cards}")
    if tables != 0 or cards < 1:
        result["ok"] = False


async def receivables_query(page, result):
    shown = await page.locator("main tbody tr").count()
    result["notes"].append(f"rows shown for ?q=Account 1: {shown}")
    if shown < 1 or shown > 11:
        result["ok"] = False


async def table_grows(page, result):
    more = page.get_by_role("button", name=re.compile(r"Load \d+ more"))
    await more.click()
    await page.wait_for_timeout(1200)
    await more.click()
    await page.wait_for_timeout(1500)
    before = await page.locator("main tbody tr").count()
    scroller = page.locator("main table").first.locator("xpath=..")
    for _ in range(6):
        await scroller.evaluate("el => el.scrollTo({ top: el.scrollHeight })")
        await page.wait_for_timeout(400)
    after = await page.locator("main tbody tr").count()
    result["notes"].append(f"rows mounted before scrolling={before} after={after}")
    if before != 150 or after != 300:
        result["ok"] = False


async def sales_rep(page, result):
    pressed = await page.locator("button.bg-primary", has_text="Rep B").count()
    if pressed < 1:
        result["ok"] = False
        result["notes"].append("Rep B is not the selected salesman")


CASES = [
    {"name": "dashboard_ok", "path": "/", "expect": ["Accessories sales this month", "Today's priority actions", "Momentum"]},
    {"name": "dashboard_500", "path": "/", "fail": ["/report/dashboard"], "expect": ["Retry"], "forbid": ["All clear"]},
    {"name": "dashboard_loading", "path": "/", "slow": ["/report/dashboard"], "wait_ms": 1500, "full": False,
     "forbid": ["All clear", "could not be read"]},
    {"name": "orders_ok", "path": "/orders", "expect": ["Purchase orders", "Suggested order"]},
    {"name": "orders_500", "path": "/orders", "fail": ["/purchase-orders"], "expect": ["Retry"],
     "forbid": ["Everything ordered has been received", "No received orders yet"]},
    {"name": "orders_loading", "path": "/orders", "slow": ["/purchase-orders"], "wait_ms": 1500,
     "forbid": ["Everything ordered has been received", "No received orders yet", "Upload 2+ orders"]},
    {"name": "orders_double_click", "path": "/orders", "slow": ["/procurement/orders"], "delay": 4, "check": orders_double_click},
    {"name": "orders_cancel_asks", "path": "/orders", "check": pipeline_cancel_asks},
    {"name": "orders_phone", "path": "/orders", "viewport": (390, 844), "touch": True, "mobile": True, "check": orders_phone_scroll},
    {"name": "shop_orders_ok", "path": "/shop-orders", "expect": ["Customer orders", "Latest 100 of 342 orders"], "act": shop_orders_load_more},
    {"name": "shop_orders_500", "path": "/shop-orders", "fail": ["/shop/orders"], "expect": ["Retry"], "forbid": ["No orders yet"]},
    {"name": "shop_orders_scroll", "path": "/shop-orders", "check": shop_orders_scroll_kept},
    {"name": "shop_order_drawer_500", "path": "/shop-orders", "fail": ["/shop/orders/:id"], "check": drawer_error},
    {"name": "stock_ok", "path": "/stock", "expect": ["Stock moves", "Daily movement", "Transfers"]},
    {"name": "stock_500", "path": "/stock", "fail": ["/stock/transfers", "/stock/daily"], "expect": ["Retry"],
     "forbid": ["No transfers in this window"]},
    {"name": "data_ok", "path": "/data", "expect": ["Data upload", "Data sources"]},
    {"name": "data_upload_refreshes", "path": "/data", "check": data_upload_refreshes},
    {"name": "data_500", "path": "/data", "fail": ["/data/coverage", "/ingest/batches"], "expect": ["Retry"], "forbid": ["Loading…"]},
    {"name": "data_loading", "path": "/data", "slow": ["/data/coverage", "/ingest/batches"], "wait_ms": 1500, "forbid": ["Loading…"]},
    {"name": "inventory_loading", "path": "/inventory", "slow": ["/report/inventory"], "wait_ms": 1500, "full": False},
    {"name": "sales_loading", "path": "/sales", "slow": ["/report/sales"], "wait_ms": 1500, "full": False},
    {"name": "inventory_phone_cards", "path": "/inventory", "viewport": (390, 844), "touch": True, "mobile": True, "check": table_phone_cards},
    {"name": "receivables_initial_query", "path": "/receivables?q=Account%201", "check": receivables_query},
    {"name": "table_grows", "path": "/shop-orders", "check": table_grows},
    {"name": "sales_rep", "path": "/sales?rep=rep%20b", "check": sales_rep},
    {"name": "settings_loading", "path": "/settings", "slow": ["/settings/costing", "/settings/shop", "/settings/agents", "/shop/audit"],
     "wait_ms": 1500, "forbid": ["Loading…"]},
    {"name": "settings_500", "path": "/settings", "fail": ["/settings/costing", "/settings/shop"], "expect": ["Retry"]},
    {"name": "dashboard_phone", "path": "/", "viewport": (390, 844), "touch": True, "mobile": True, "expect": ["Accessories sales this month"]},
    {"name": "dashboard_reduced", "path": "/", "reduced": "reduce", "wait_ms": 1200, "expect": ["4,321"]},
]


async def main_async(a) -> int:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    only = set(a.only.split(",")) if a.only else None
    bad = 0
    for c in CASES:
        if only and c["name"] not in only:
            continue
        r = await run_case(a.url, Path(a.web), out, c)
        bad += 0 if r["ok"] else 1
        print(json.dumps(r))
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:5182")
    ap.add_argument("--web", default=str(Path(__file__).resolve().parents[1]), help="the web/ folder (its .env)")
    ap.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "yq-admin-render"))
    ap.add_argument("--only", default="")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
