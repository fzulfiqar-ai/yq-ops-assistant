"""Browser render check of the SALESMAN app (ship 2, 30-Oct-2026) — synthetic data only.

    cd web && npm run build && npx vite preview --outDir dist --port 5187 --strictPort
    python web/scripts/sales_render_check.py [--url http://localhost:5187] [--out <dir>]
        [--only case,case@390] [--widths 390,1366] [--allow-pending] [--list]

The reps' field app (components/SalesmanShell.tsx, pages/sales/*, pages/shop/*), checked the way
admin_render_check.py checks the office: a fake Supabase session and a cached salesman /me in
localStorage get the SPA past its sign-in (valid nowhere — the scripts/perf_auth_check.py stand-in).
EVERY request leaves the browser only to the preview server: the API host (VITE_API_URL in web/.env)
is answered inside the browser from the invented fixtures below — or with a 500, a delay or a hold
for the failure and "still checking" cases — Supabase answers {}, anything else is aborted. No
account, no API, no database, no network. Every shop, rep, phone, code and amount is invented.

Each case runs at a phone (390 x 844, touch, mobile) and at a 1366 desktop, in a fresh browser
context (one headless Chromium for the run, closed at the end), and prints ONE JSON line: ok, the
failures, notes, screenshots, the API calls it saw and any call no fixture answered. Exit 1 if any
case failed. Screens are found by role, label and visible text — never by CSS class — so the shell,
sheet and brand changes of the night do not break the checks; only a change in what a rep sees does.

  today              the 5-tab shell renders Today; the tab bar lights only Today
  today_no_catalog   a tab the rep may not open (Catalog, without the feature) is not shown
  catalog            the list, search narrows it, the list pages by 48 and keeps up with a scroll;
                     S13 on the phone: inputs >= 16 px, primary tap targets >= 44 px
  order              pick a shop, add lines; "Checking prices…" and Place order off until the quote
                     answers (S14); a double tap sends ONE order; Orders, stock and the usual basket
                     are read again afterwards, and the order is on the Orders list
  quote_failed       a failed quote: Retry, never BHD 0.000 / "Free", Place order off; Retry prices it
  shop_kept          the shop and its cart survive a reload and the app being closed and opened again
                     (a new tab: sessionStorage gone); 11 h after the pick kept, 13 h gone (the 12-hour rule)
  sign_out           asks first; "Stay signed in" keeps everything; then this phone's carts, shop and
                     search are cleared (the device's own choices and a customer's cart stay)
  toast_over_sheet   S11: a toast while a sheet is open is the top element at its own centre; Undo
                     after removing a line in the checkout puts it back
  chrome             S12 (phone): fixed / stuck chrome <= 200 px while scrolling the catalog with a
                     cart; the desktop's docked order slip stays in view instead
  today_failed / customers_failed / catalog_failed / orders_failed
                     a failed load says so with Try again (never an empty "nothing here"), and Try
                     again really asks again
  back_keeps_place   S4: closing a product keeps the list's place; Back from another tab returns to
                     where the list was (the browser's own restore does it on the base: a "new page
                     opens at the top" change must not take it away). Warns when Back with a product
                     open leaves the catalog.
  tab_keeps_place    S4, per tab: Catalog → Orders tab → Catalog tab returns to where the list was —
                     PENDING on the 2307ac4 base (lands at the top; the per-tab restore arrives with
                     the shell branch): it fails there by design, and --allow-pending keeps it out of
                     the exit code. On an integrated build run it WITHOUT --allow-pending.

Besides its failures a line can carry warnings (reported, never fatal): a design residual for the
owner, such as a floating button that sits on a line's "+" while the list is at rest.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import datetime as dt
import inspect
import json
import re
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import Error as PWError
from playwright.async_api import async_playwright

UID = "00000000-0000-4000-8000-00000000qa02"
# app/features.py ROLE_DEFAULT_FEATURES["salesman"]: the salesman app + Spotted
REP_FEATURES = ["Catalog", "Shop Orders", "Market Intel"]
TODAY = dt.date.today().isoformat()
PHONE, DESK = 390, 1366
VIEWPORTS = {
    PHONE: {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True},
    DESK: {"viewport": {"width": 1366, "height": 768}, "device_scale_factor": 1, "is_mobile": False, "has_touch": False},
}
CORS = {"access-control-allow-origin": "*", "access-control-allow-headers": "*", "access-control-allow-methods": "*"}
# the five tabs of SalesmanShell, by route (the labels may change, the routes are the contract)
TABS = {"/today": "Today", "/shop": "Catalog", "/shop-orders": "Orders", "/customers": "Shops", "/account": "Me"}
# copy that claims "nothing here" — a lie when the data simply did not load
FALSE_EMPTY = ("Nothing waiting", "Nothing due right now", "No shops yet", "No shop matches", "Nothing matches that",
               "No new orders yet", "Nothing here yet", "0 products")
# "did not load", "didn't load", "could not load", "Couldn't load your orders", "failed to load"
ERROR_TEXT = re.compile(r"(?:did|could)(?: no|n.)t load|failed to load", re.I)
RETRY_BUTTON = re.compile(r"^\s*(try again|retry)\b", re.I)
CRASH_TEXT = "Something went wrong"     # components/ErrorBoundary: the portal's crash screen


def env_value(web: Path, key: str) -> str:
    for line in (web / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    return ""


def supabase_ref(web: Path) -> str:
    url = env_value(web, "VITE_SUPABASE_URL")
    if not url:
        raise SystemExit("VITE_SUPABASE_URL missing from web/.env (the build and this check read it)")
    return urlparse(url).hostname.split(".")[0]


def api_host(web: Path) -> str:
    return urlparse(env_value(web, "VITE_API_URL")).netloc or "localhost:8001"


def me_for(features: list[str]) -> dict:
    return {"email": "qa-rep@example.invalid", "role": "salesman", "features": list(features), "full_name": "Test Rep",
            "must_reset": False, "read_only": False}


def fake_session(email: str) -> dict:
    now = int(time.time())
    b64 = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")  # noqa: E731
    jwt = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64({'sub': UID, 'exp': now + 7200, 'role': 'authenticated'})}.x"
    user = {"id": UID, "aud": "authenticated", "role": "authenticated", "email": email,
            "app_metadata": {}, "user_metadata": {}, "created_at": "2026-09-01T00:00:00Z"}
    return {"access_token": jwt, "token_type": "bearer", "expires_in": 7200, "expires_at": now + 7200,
            "refresh_token": "qa-not-a-token", "user": user}


def iso_ago(minutes: float) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes)).isoformat()


# ── synthetic fixtures (invented codes, shops, phones and amounts) ───────────────────────────────

# 180 lines in five categories: the list has to page (48 at a time), and "charger" has to narrow it
CATS = [("CABLES", "A", 60, "Test cable"), ("CHARGERS", "B", 40, "Test charger"), ("AUDIO", "C", 40, "Test earbuds"),
        ("HOLDERS", "D", 20, "Test car holder"), ("POWER BANKS", "E", 20, "Test power bank")]


def catalog_items() -> list[dict]:
    items: list[dict] = []
    for cat, letter, n, name in CATS:
        for i in range(1, n + 1):
            k = len(items)
            sold_out = k % 17 == 16
            qty = 0 if sold_out else (4 if k % 11 == 5 else 30 + k % 40)
            items.append({
                "item_code": f"TST-{letter}{i:03d}", "display_name": f"{name} {i}", "category": cat, "brand": "TST",
                "price_bhd": round(0.5 + (k % 19) * 0.25, 3), "b2c_bhd": None, "stock_qty": qty, "moq": 1, "pack_size": 1,
                "stock_status": "out_of_stock" if sold_out else ("low_stock" if qty < 10 else "in_stock"),
                "badges": ["best_seller"] if k % 23 == 0 else [], "thumb_url": None, "product_image_url": None,
                "spec": f"{name} {i} — synthetic line",
            })
    return items


def book_shops() -> list[dict]:
    out = []
    for i in range(1, 9):
        focus = i <= 5
        name = f"Test Shop {i}"
        out.append({
            "key": f"f:{name}" if focus else f"m:9730000000{i}", "name": name, "focus_name": name if focus else None,
            "contact_name": f"Test Contact {i}", "phone": f"9730000000{i}", "area": f"Test Area {1 + i % 3}", "email": None,
            "sources": ["focus", "app"] if focus else ["app"], "last_focus_date": TODAY if focus else None,
            "last_app_order_at": iso_ago(60 * 24 * i), "last_order_date": TODAY, "app_orders": i % 3, "days_since": i * 3,
            "status": "due" if i <= 2 else "ok", "due": i <= 2, "why": "Usually orders every 2 weeks" if i <= 2 else None,
            "monthly_value_bhd": f"{40 + i * 5}.000", "credit": None, "rank": i,
        })
    return out


SHOP_ME = {
    "salesman": {"id": 9001, "name": "Test Rep", "referral_code": "test-rep", "phone": None, "whatsapp": None, "title": "QA fixture", "photo_url": None},
    "link": "https://shop.example.invalid/test-rep", "qr_url": None,
    "kpis": {"orders_7d": 3, "orders_30d": 12, "value_30d_bhd": 240.0, "customers_30d": 6},
    "focus": None, "hint": None, "last_closed": None, "draft": None,
}


class State:
    """What the synthetic API knows during one case (fresh per case): it changes as the page writes."""

    def __init__(self) -> None:
        self.items = catalog_items()
        self.by_code = {i["item_code"]: i for i in self.items}
        self.shops = book_shops()
        self.orders: list[dict] = [self.order_row(15001, "Test Shop 2", "confirmed", 26.0, 40),
                                   self.order_row(15002, "Test Shop 3", "new", 18.5, 25)]
        self.order_seq = 0
        self.by_client_id: dict[str, dict] = {}

    @staticmethod
    def order_row(oid: int, shop: str, status: str, total: float, minutes: float, source: str = "link") -> dict:
        return {"id": oid, "order_no": f"QA-{oid}", "status": status, "customer_name": "Test Contact", "customer_shop": shop,
                "customer_phone": "97300000009", "customer_area": "Test Area 1", "salesman_name": "Test Rep", "salesman_id": 9001,
                "total_bhd": total, "items_count": 2, "units_count": 6, "created_at": iso_ago(minutes), "updated_at": iso_ago(minutes),
                "source": source, "notify_failed": False, "notify_attempts": 1, "order_kind": "standard"}

    # GET /shop/catalog
    def catalog(self) -> dict:
        return {"company": "Test Co", "brand": "TST", "mode": "salesman", "prices_updated": TODAY, "stock_as_of": TODAY, "stock_fresh": True,
                "categories": [c[0] for c in CATS], "items": self.items, "salesmen": [], "offers": [], "campaigns": [], "pairs": [],
                "settings": {"min_order_bhd": 20, "allow_backorder": True, "small_order_mode": "request", "show_retail_compare": False},
                "me": {"salesman_id": 9001, "salesman_name": "Test Rep", "is_admin": False}}

    # POST /shop/quote — the server prices the cart
    def quote(self, body: dict) -> dict:
        lines, total, units = [], 0.0, 0
        for ln in body.get("lines") or []:
            code, qty = str(ln.get("item_code") or ""), int(ln.get("qty") or 0)
            it = self.by_code.get(code) or {}
            price = float(it.get("price_bhd") or 0)
            lines.append({"item_code": code, "display_name": it.get("display_name"), "qty": qty, "moq": 1, "list_price_bhd": price,
                          "unit_price_bhd": price, "discount_bhd": 0, "line_total_bhd": round(price * qty, 3),
                          "stock_status": it.get("stock_status"), "backorder": False, "applied": [], "warning": None})
            total += price * qty
            units += qty
        total = round(total, 3)
        met = total >= 20
        return {"ok": True, "lines": lines, "subtotal_bhd": total, "discount_bhd": 0, "delivery_bhd": 0, "total_bhd": total,
                "units": units, "items": len(lines), "discounts": [], "warnings": [], "can_submit": True, "min_order_bhd": 20,
                "minimum": {"value_bhd": 20, "remaining_bhd": round(max(0.0, 20 - total), 3), "met": met, "mode": "request",
                            "kind": "standard" if met else "small", "fee_bhd": 0}}

    # POST /shop/order — one order per client_order_id (the server's idempotency rule)
    def place(self, body: dict) -> dict:
        cid = str(body.get("client_order_id") or "")
        if cid and cid in self.by_client_id:
            return dict(self.by_client_id[cid], duplicate=True)
        self.order_seq += 1
        oid = 20000 + self.order_seq
        q = self.quote(body)
        who = body.get("customer") or {}
        row = self.order_row(oid, str(who.get("shop") or who.get("name") or "Test shop"), "confirmed", q["total_bhd"], 0, source="salesman")
        row.update({"items_count": q["items"], "units_count": q["units"]})
        self.orders.insert(0, row)
        for ln in body.get("lines") or []:
            it = self.by_code.get(str(ln.get("item_code")))
            if it and isinstance(it.get("stock_qty"), int):
                it["stock_qty"] = max(0, it["stock_qty"] - int(ln.get("qty") or 0))
        res = {"ok": True, "order_no": row["order_no"], "order_id": oid, "token": "qa-not-a-token", "status_url": None,
               "whatsapp_url": None, "email_url": None, "totals": q, "has_backorder": False, "source": "salesman", "duplicate": False}
        if cid:
            self.by_client_id[cid] = res
        return res

    # GET /shop/orders?status=&limit=&offset=
    def orders_list(self, q: dict) -> dict:
        status = (q.get("status") or [""])[0]
        lim = int((q.get("limit") or ["100"])[0])
        off = int((q.get("offset") or ["0"])[0])
        rows = [o for o in self.orders if not status or o["status"] in status.split(",")]
        return {"orders": rows[off:off + lim], "count": len(rows), "counts": dict(Counter(o["status"] for o in self.orders))}

    # GET /shop/orders/{id}
    def order_detail(self, oid: int) -> dict:
        o = next((x for x in self.orders if x["id"] == oid), None) or self.order_row(oid, "Test Shop 9", "new", 10.0, 30)
        confirmed = o["status"] != "new"
        line = {"id": 1, "item_code": "TST-A004", "display_name": "Test cable 4", "qty": 4, "qty_confirmed": 4 if confirmed else None,
                "qty_delivered": 4 if o["status"] == "delivered" else None, "unit_price_bhd": 1.25, "line_total_bhd": 5.0,
                "line_status": "ok", "disposition": "as_ordered"}
        actions = {"new": ["confirm", "cancel"], "confirmed": ["deliver", "cancel"]}.get(o["status"], [])
        return {**o, "lines": [line], "events": [], "actions": actions, "subtotal_bhd": o["total_bhd"], "discount_bhd": 0, "delivery_bhd": 0,
                "whatsapp_url": None, "shop_told": True, "customer": None}

    # GET /shop/me/book
    def book(self) -> dict:
        return {"shops": self.shops, "count": len(self.shops), "counts": {"due": 2, "focus": 5, "app": 8, "credit": 0},
                "data_through": TODAY, "hint": None, "error": None}

    # GET /shop/me/basket?shop=<book key>
    def basket(self, key: str) -> dict:
        s = next((x for x in self.shops if x["key"] == key), self.shops[0])
        usual = []
        for code, qty in (("TST-A003", 4), ("TST-A010", 2)):
            it = self.by_code[code]
            usual.append({"item_code": code, "display_name": it["display_name"], "qty": qty, "in_catalog": True, "sold_out": False,
                          "price_bhd": f"{it['price_bhd']:.3f}", "times_bought": 5, "due": True})
        shop = {k: s.get(k) for k in ("key", "name", "focus_name", "contact_name", "phone", "area", "email", "last_order_date", "status", "due", "why", "credit")}
        return {"shop": shop, "usual": usual, "last_order": None, "suggested": usual, "suggested_from": "regulars", "available": usual,
                "sold_out": [], "value_bhd": "6.500", "restock_link": None}

    # GET /shop/me/today
    def today(self) -> dict:
        waiting = [o for o in self.orders if o["status"] == "new"]
        due = [{"key": "f:Test Shop 1", "shop": "Test Shop 1", "rep": "Test Rep", "days_since": 16, "gap_days": 14, "gap_source": "own",
                "overdue_ratio": 1.1, "monthly_value_bhd": "45.000", "invoices_180d": 12, "visits": 12, "rep_visits": 10, "last_date": TODAY,
                "first_date": "2026-01-10", "top_skus": [{"item_code": "TST-A003", "display_name": "Test cable 3", "median_qty": 4,
                                                          "times_bought": 5, "cadence_days": 14, "days_since": 16}],
                "phone": "97300000001", "why": "Usually orders every 2 weeks · 16 days ago", "rank": 1, "list": "due", "wa_text": None}]
        return {
            "hint": None, "me": SHOP_ME,
            "money": {"month": TODAY[:7], "mtd_bhd": "1250.000", "tier": 1, "rate": 0.01, "kickback_bhd": "12.500", "next_tier": 2,
                      "next_gap_bhd": "250.000", "next_gain_bhd": "10.000", "days_left": 2, "data_through": TODAY, "data_age_days": 0,
                      "stale": False, "basis": "net_ex_vat", "is_estimate": True, "returns_deducted": False},
            "waiting": [{"id": o["id"], "order_no": o["order_no"], "created_at": o["created_at"], "customer_name": o["customer_name"],
                         "customer_shop": o["customer_shop"], "customer_area": o["customer_area"], "total_bhd": f"{o['total_bhd']:.3f}",
                         "items": o["items_count"], "units": o["units_count"], "order_kind": "standard", "is_test": False,
                         "age_min": 25, "overdue": False} for o in waiting],
            "waiting_count": len(waiting), "in_progress": sum(1 for o in self.orders if o["status"] == "confirmed"), "sla_min": 120,
            "due": due, "due_count": 1, "due_counts": {"shops": 8, "due": 1, "lapsed": 0}, "due_data_through": TODAY, "due_hint": None,
            "baskets": None, "link_week": {"days": 7, "sessions": 9, "adds": 4, "checkouts": 2, "orders": 2, "value_bhd": "40.000",
                                           "conversion_pct": 22, "shares": 3, "top_products": []},
            "restock": [], "chat": [], "errors": {}, "generated_at": iso_ago(0),
        }

    def followups(self) -> dict:
        return {"rep": "Test Rep", "scope": "rep", "due": [], "lapsed": [], "counts": {"shops": 8, "due": 0, "lapsed": 0},
                "data_through": TODAY, "hint": None}

    def answer(self, method: str, key: str, path: str, query: dict, body: dict, me: dict) -> tuple[dict, bool]:
        """(body, answered by a fixture). A path no fixture knows gets {} — and is listed as unanswered."""
        if method == "POST":
            if key == "/shop/quote":
                return self.quote(body), True
            if key == "/shop/order":
                return self.place(body), True
            m = re.fullmatch(r"/shop/orders/(\d+)/(deliver|status)", path)
            if m:
                o = next((x for x in self.orders if x["id"] == int(m.group(1))), None)
                if o:
                    o["status"] = "delivered" if m.group(2) == "deliver" else str(body.get("status") or o["status"])
                return {"ok": True, "order": o}, True
            return {"ok": True, "qa": "not sent"}, key in ("/shop/me/followups/tap", "/shop/events")
        m = re.fullmatch(r"/shop/orders/(\d+)", path)
        if m:
            return self.order_detail(int(m.group(1))), True
        fixed = {
            "/me": me, "/auth/features": {"features": []}, "/health": {"ok": True}, "/shop/me": SHOP_ME,
            "/shop/catalog": None, "/shop/me/today": None, "/shop/me/book": None, "/shop/orders": None, "/shop/me/basket": None,
            "/shop/me/followups": None, "/shop/customers": {"customers": []}, "/shop/upcoming": {"items": [], "settings": {}},
            "/shop/upcoming/interest": {"interest": []}, "/shop/me/baskets": {"days": 7, "baskets": [], "count": 0},
            "/shop/me/link-week": {"days": 7, "sessions": 0, "orders": 0},
        }
        if key not in fixed:
            return {}, False
        if fixed[key] is not None:
            return fixed[key], True
        if key == "/shop/catalog":
            return self.catalog(), True
        if key == "/shop/me/today":
            return self.today(), True
        if key == "/shop/me/book":
            return self.book(), True
        if key == "/shop/orders":
            return self.orders_list(query), True
        if key == "/shop/me/basket":
            return self.basket((query.get("shop") or [""])[0]), True
        return self.followups(), True


def route_key(path: str) -> str:
    return re.sub(r"/\d+(?=/|$)", "/:id", path)


class Api:
    """The API host, answered inside the browser: fixtures, failures (500 until healed), delays and holds."""

    def __init__(self, site: str, api: str, sb_ref: str, me: dict, fail=(), slow=None) -> None:
        self.site, self.api, self.sb_ref, self.me = site, api, sb_ref, me
        self.state = State()
        self.fail = set(fail)
        self.slow = dict(slow or {})
        self.gates: dict[str, asyncio.Event] = {}
        self.hits: list[dict] = []
        self.unanswered: Counter = Counter()
        self.supabase: list[str] = []

    def hold(self, key: str) -> None:
        self.gates[key] = asyncio.Event()

    def release(self, key: str | None = None) -> None:
        for k in [key] if key else list(self.gates):
            ev = self.gates.pop(k, None)
            if ev:
                ev.set()

    def heal(self, key: str) -> None:
        self.fail.discard(key)

    def calls(self, key: str, method: str = "GET", since: float = 0.0, within: float | None = None) -> list[dict]:
        """The requests for `key` made after `since` (and, with `within`, no later than that many seconds after it)."""
        return [h for h in self.hits if h["key"] == key and h["method"] == method and h["t"] > since
                and (within is None or h["t"] <= since + within)]

    async def on_route(self, route) -> None:
        req = route.request
        u = urlparse(req.url)
        if u.netloc == self.site:
            return await route.continue_()
        if req.method == "OPTIONS":
            return await self.fulfill(route, 204, None)
        if u.netloc == self.api:
            key = route_key(u.path)
            try:
                body = req.post_data_json if req.method != "GET" else None
            except Exception:  # noqa: BLE001 — not JSON
                body = None
            hit = {"t": time.monotonic(), "method": req.method, "key": key, "path": u.path, "query": u.query, "body": body, "done": None}
            self.hits.append(hit)
            gate = self.gates.get(key)
            if gate:
                await gate.wait()
            if key in self.slow:
                await asyncio.sleep(self.slow[key])
            # stamped before the answer leaves: whatever the page asks for because of it comes later
            if key in self.fail:
                hit["done"] = time.monotonic()
                await self.fulfill(route, 500, {"detail": "Server error", "ref": "qa0001"})
            else:
                data, known = self.state.answer(req.method, key, u.path, parse_qs(u.query), body if isinstance(body, dict) else {}, self.me)
                if not known:
                    self.unanswered[f"{req.method} {key}"] += 1
                hit["done"] = time.monotonic()
                await self.fulfill(route, 200, data)
            return None
        if u.netloc.startswith(self.sb_ref + "."):
            self.supabase.append(f"{req.method} {u.path}")
            return await self.fulfill(route, 200, {})
        return await route.abort()

    @staticmethod
    async def fulfill(route, status: int, data) -> None:
        try:
            await route.fulfill(status=status, headers={**CORS, "content-type": "application/json"},
                                body="" if data is None else json.dumps(data))
        except PWError:
            pass  # the page gave up on this request (a newer quote aborts the older one)


# ── in-page probes (by role, label and text — never a class) ─────────────────────────────────────

TAB_NAV = r"""
() => {
  const vis = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && Number(cs.opacity) > 0.05
      && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth; };
  const name = (a) => (a.getAttribute('aria-label') || a.innerText || '').replace(/\s+/g, ' ').trim();
  const tabs = ['/today', '/shop', '/shop-orders', '/customers', '/account'];
  const navs = [...document.querySelectorAll('nav, [role="navigation"]')].filter((n) => vis(n)
    && [...n.querySelectorAll('a[href]')].some((a) => tabs.includes(a.getAttribute('href'))));
  return navs.map((n) => ({ label: n.getAttribute('aria-label'),
    links: [...n.querySelectorAll('a[href]')].filter(vis).map((a) => ({ name: name(a), href: a.getAttribute('href'),
      current: a.getAttribute('aria-current'), w: Math.round(a.getBoundingClientRect().width), h: Math.round(a.getBoundingClientRect().height) })) }));
}
"""

# rendered catalog lines: one "+" ("Add TST-…" / "Add 1 more TST-…") per row
ROWS = r"""() => [...document.querySelectorAll('button[aria-label]')].filter((b) => /^Add (\d+ more )?TST-/.test(b.getAttribute('aria-label'))).length"""

# what the controls a rep's thumb needs measure: the box plus a hit-area expander (portal_qa / market_qa rule)
TAP_SIZES = r"""
(patterns) => {
  const res = patterns.map((p) => new RegExp(p));
  const vis = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && Number(cs.opacity) > 0.05
      && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth; };
  const effective = (el, r) => {
    let w = r.width, h = r.height;
    if (getComputedStyle(el).position === 'static') return { w, h };
    for (const pe of ['::before', '::after']) {
      const cs = getComputedStyle(el, pe);
      if (!cs || cs.content === 'none' || cs.position !== 'absolute' || cs.pointerEvents === 'none' || cs.display === 'none') continue;
      const t = parseFloat(cs.top), b = parseFloat(cs.bottom), l = parseFloat(cs.left), rr = parseFloat(cs.right);
      if (!isNaN(t) && t < 0) h += -t;
      if (!isNaN(b) && b < 0) h += -b;
      if (!isNaN(l) && l < 0) w += -l;
      if (!isNaN(rr) && rr < 0) w += -rr;
    }
    return { w, h };
  };
  const name = (el) => (el.getAttribute('aria-label') || el.innerText || el.value || '').replace(/\s+/g, ' ').trim();
  const out = [];
  for (const el of document.querySelectorAll('a[href], button, [role="button"], [role="tab"]')) {
    if (el.disabled || !vis(el)) continue;
    const n = name(el);
    if (!res.some((re) => re.test(n))) continue;
    const e = effective(el, el.getBoundingClientRect());
    out.push({ name: n.slice(0, 60), w: Math.round(e.w), h: Math.round(e.h) });
  }
  const inputs = [...document.querySelectorAll('input, select, textarea')].filter((el) => el.type !== 'hidden' && el.type !== 'checkbox'
    && el.type !== 'radio' && vis(el)).map((el) => ({ name: (el.getAttribute('aria-label') || el.placeholder || el.id || el.type || '').slice(0, 50),
      fontSize: parseFloat(getComputedStyle(el).fontSize) }));
  return { targets: out, inputs };
}
"""

# a tap target in the open (clear of the full-width bars the list scrolls under) that a floating element
# sits on: a tap there lands on the floater (the audit's "+ opens the camera")
COVERED = r"""
(patterns) => {
  const res = patterns.map((p) => new RegExp(p));
  const vw = innerWidth, vh = innerHeight;
  const bars = [];
  for (const el of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(el);
    if (cs.position !== 'fixed' && cs.position !== 'sticky') continue;
    if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) < 0.05) continue;
    const r = el.getBoundingClientRect();
    if (r.width >= vw * 0.8 && r.height < vh * 0.8 && r.bottom > 0 && r.top < vh) bars.push([r.top, r.bottom]);
  }
  const out = [];
  for (const el of document.querySelectorAll('button[aria-label], a[aria-label]')) {
    const n = el.getAttribute('aria-label') || '';
    if (!res.some((re) => re.test(n))) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1 || r.top < 0 || r.bottom > vh) continue;
    if (bars.some(([a, b]) => r.bottom > a && r.top < b)) continue;
    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    if (!hit || el.contains(hit)) continue;
    const ctl = hit.closest('button, a, [role="button"]') || hit;
    out.push({ target: n.slice(0, 40), by: ((ctl.getAttribute('aria-label') || ctl.innerText || ctl.tagName) + '').replace(/\s+/g, ' ').trim().slice(0, 30),
               y: Math.round(r.top) });
  }
  return out;
}
"""

# S11: is this toast the top element at its own centre (above any open sheet)? The pattern is anchored on the
# toast's own text; a toast is a status / alert / live-region item, else the smallest element with that text
# outside every dialog. A sheet is a [role=dialog], a native <dialog open> or [aria-modal].
TOAST_ON_TOP = r"""
(pattern) => {
  const re = new RegExp(pattern);
  const DIALOG = '[role="dialog"], [role="alertdialog"], dialog[open], [aria-modal="true"]';
  const vis = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const mine = (el) => vis(el) && !el.closest(DIALOG) && re.test((el.innerText || '').trim());
  let toast = [...document.querySelectorAll('[role="status"], [role="alert"], [aria-live] > *')].filter(mine)[0];
  const byRole = !!toast;
  if (!toast) {
    const area = (el) => { const r = el.getBoundingClientRect(); return r.width * r.height; };
    toast = [...document.querySelectorAll('body *')].filter(mine).sort((a, b) => area(a) - area(b))[0];
  }
  if (!toast) return { found: false };
  const r = toast.getBoundingClientRect();
  const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
  const describe = (el) => el ? (el.tagName.toLowerCase() + (el.getAttribute('role') ? '[role=' + el.getAttribute('role') + ']' : '')
    + (el.getAttribute('aria-label') ? '[aria-label="' + el.getAttribute('aria-label').slice(0, 30) + '"]' : '')) : null;
  const dialogs = [...document.querySelectorAll(DIALOG)].filter(vis).length;
  return { found: true, byRole, text: (toast.innerText || '').trim().slice(0, 80), onTop: !!hit && toast.contains(hit),
           hit: describe(hit), hitInDialog: !!(hit && hit.closest(DIALOG)), dialogs,
           box: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)] };
}
"""

# S12: what fixed or stuck-sticky chrome covers of the screen, overlaps counted once (portal_qa's CHROME rule)
CHROME = r"""
() => {
  const vh = innerHeight;
  const sel = (el) => { let s = el.tagName.toLowerCase(); const al = el.getAttribute('aria-label'); if (al) s += '[aria-label="' + al.slice(0, 30) + '"]';
    const role = el.getAttribute('role'); if (role) s += '[role=' + role + ']'; return s; };
  const parts = [];
  for (const el of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(el);
    if (cs.position !== 'fixed' && cs.position !== 'sticky') continue;
    if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) < 0.05) continue;
    let nested = false;
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      const ps = getComputedStyle(p).position;
      if (ps === 'fixed' || ps === 'sticky') { nested = true; break; }
    }
    if (nested) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 40 || r.height < 1) continue;
    if (cs.position === 'sticky') {
      const t = parseFloat(cs.top), b = parseFloat(cs.bottom);
      const stuck = (!isNaN(t) && Math.abs(r.top - t) < 2) || (!isNaN(b) && Math.abs(vh - r.bottom - b) < 2);
      if (!stuck) continue;
    }
    const top = Math.max(0, r.top), bottom = Math.min(vh, r.bottom);
    if (bottom - top < 1 || bottom - top > vh * 0.8) continue;
    parts.push({ sel: sel(el), pos: cs.position, top: Math.round(top), bottom: Math.round(bottom), h: Math.round(bottom - top) });
  }
  const iv = parts.map((p) => [p.top, p.bottom]).sort((a, b) => a[0] - b[0]);
  let total = 0, cur = null;
  for (const [a, b] of iv) {
    if (!cur || a > cur[1]) { if (cur) total += cur[1] - cur[0]; cur = [a, b]; } else cur[1] = Math.max(cur[1], b);
  }
  if (cur) total += cur[1] - cur[0];
  return { total: Math.round(total), parts, scrollY: Math.round(window.scrollY) };
}
"""

# a catalog line's "details" button well inside the screen (clear of the bars), for opening a product
ROW_IN_VIEW = r"""
() => { const vh = innerHeight;
  const b = [...document.querySelectorAll('button[aria-label]')].find((x) => /^TST-\w+.* — details$/.test(x.getAttribute('aria-label'))
    && (() => { const r = x.getBoundingClientRect(); return r.width > 0 && r.top > vh * 0.3 && r.bottom < vh * 0.6; })());
  return b ? b.getAttribute('aria-label') : null; }
"""

STORAGE = r"""
() => ({ local: Object.fromEntries(Object.keys(localStorage).map((k) => [k, localStorage.getItem(k)])),
         session: Object.fromEntries(Object.keys(sessionStorage).map((k) => [k, sessionStorage.getItem(k)])) })
"""

# ── the case harness ──────────────────────────────────────────────────────────────────────────────


class T:
    """One case at one width: the page, the synthetic API, and what the case found."""

    def __init__(self, page, api: Api, base: str, name: str, width: int, out: Path) -> None:
        self.page, self.api, self.base, self.name, self.width, self.out = page, api, base.rstrip("/"), name, width, out
        self.phone = width < 768
        self.vh = VIEWPORTS[width]["viewport"]["height"]
        self.fails: list[str] = []
        self.warnings: list[str] = []
        self.notes: list[str] = []
        self.shots: list[str] = []

    def check(self, ok: bool, msg: str) -> bool:
        if not ok:
            self.fails.append(msg)
        return ok

    def warn(self, ok: bool, msg: str) -> bool:
        """Reported in the line, never fatal: a design residual for the owner, not a broken flow."""
        if not ok:
            self.warnings.append(msg)
        return ok

    def note(self, msg: str) -> None:
        self.notes.append(msg)

    async def shot(self, label: str = "", full: bool = False) -> None:
        fn = f"{self.name}__{self.width}{'__' + label if label else ''}.png"
        try:
            await self.page.screenshot(path=str(self.out / fn), full_page=full, timeout=15000)
            self.shots.append(fn)
        except PWError as e:
            self.note(f"screenshot {fn} failed: {str(e)[:100]}")

    async def act(self, loc) -> None:
        """A finger on the phone, the mouse on the desktop."""
        if self.phone:
            await loc.tap()
        else:
            await loc.click()

    def vis(self, loc):
        return loc.filter(visible=True).first

    async def wait(self, loc, timeout: float = 6000, state: str = "visible") -> bool:
        try:
            await loc.wait_for(state=state, timeout=timeout)
            return True
        except PWError:
            return False

    async def text(self, loc=None) -> str:
        try:
            return await (loc or self.page.locator("body")).inner_text(timeout=4000)
        except PWError:
            return ""

    async def until(self, fn, timeout: float = 6.0, every: float = 0.1) -> bool:
        """Poll `fn` (a plain predicate or one that returns an awaitable) until it holds or the time is up."""
        end = time.monotonic() + timeout
        while True:
            try:
                v = fn()
                if inspect.isawaitable(v):
                    v = await v
                if v:
                    return True
            except PWError:
                pass
            if time.monotonic() > end:
                return False
            await asyncio.sleep(every)

    async def goto(self, path: str) -> None:
        await self.page.goto(self.base + path, wait_until="domcontentloaded")

    async def shell_ready(self) -> bool:
        """The salesman shell is up: a visible link to /today (the tab bar on a phone, the sidebar on a desk)."""
        return await self.wait(self.vis(self.page.locator('a[href="/today"]')), timeout=20000)

    async def tab_nav(self) -> list[dict]:
        return await self.page.evaluate(TAB_NAV)

    def tab(self, href: str):
        return self.vis(self.page.get_by_role("navigation").filter(visible=True).locator(f'a[href="{href}"]'))

    async def scroll_down(self, total: int, step: int = 120) -> None:
        """A scroll the page's scroll-direction hooks see: wheel steps over the list, then the bars settle."""
        await self.page.mouse.move(VIEWPORTS[self.width]["viewport"]["width"] / 2, self.vh * 0.45)
        done = 0
        while done < total:
            d = min(step, total - done)
            await self.page.mouse.wheel(0, d)
            done += d
            await self.page.wait_for_timeout(35)
        await self.page.wait_for_timeout(450)

    async def scroll_y(self) -> int:
        return int(await self.page.evaluate("() => Math.round(window.scrollY)"))

    async def rows(self) -> int:
        return int(await self.page.evaluate(ROWS))

    async def catalog_ready(self) -> bool:
        """The list is drawn: the first line's "+" ("Add TST-A001", or "Add 1 more TST-A001" when it is in the cart)."""
        return await self.wait(self.vis(self.page.get_by_role("button", name=re.compile(r"^Add (\d+ more )?TST-A001$"))), timeout=20000)

    async def pick_shop(self, name: str = "Test Shop 1") -> bool:
        pill = self.vis(self.page.get_by_role("button", name=re.compile(r"Who is this order for", re.I)))
        if not self.check(await self.wait(pill), "the shop pill (Who is this order for?) is not on screen"):
            return False
        await self.act(pill)
        dlg = self.page.get_by_role("dialog", name=re.compile(r"which shop", re.I))
        if not self.check(await self.wait(dlg), "the shop picker did not open"):
            return False
        row = dlg.get_by_role("button", name=re.compile(rf"\b{re.escape(name)}\b"))
        if not self.check(await self.wait(row.first, timeout=8000), f"{name} is not in the shop picker"):
            return False
        await self.act(row.first)
        self.check(await self.wait(dlg, state="hidden", timeout=5000), "the shop picker did not close after the pick")
        return self.check(await self.wait(self.vis(self.page.get_by_role("button", name=re.compile(rf"\b{re.escape(name)}\b")))),
                          f"{name} is not shown as the shop being ordered for")

    async def add(self, code: str) -> None:
        await self.act(self.page.get_by_role("button", name=re.compile(rf"^Add (\d+ more )?{re.escape(code)}$")).first)

    async def open_checkout(self):
        btn = self.vis(self.page.get_by_role("button", name=re.compile(r"^\s*(Review|Place order)\s*$")))
        if not self.check(await self.wait(btn), "no Review / Place order button to open the checkout"):
            return None
        await self.act(btn)
        dlg = self.page.get_by_role("dialog", name=re.compile(r"New order|Your order"))
        if not self.check(await self.wait(dlg), "the checkout (New order) did not open"):
            return None
        return dlg

    @staticmethod
    def submit_of(dlg):
        return dlg.get_by_role("button", name=re.compile(r"Place order for this shop|Checking prices|Sending|Send order"))

    async def storage(self) -> dict:
        return await self.page.evaluate(STORAGE)

    async def taps(self, patterns: list[str]) -> dict:
        return await self.page.evaluate(TAP_SIZES, patterns)

    async def covered(self, where: str) -> None:
        """Warn when a floating control sits on a line's Qty or "+" in the open part of the screen."""
        hits = await self.page.evaluate(COVERED, [r"^Add (\d+ more )?TST-", r"^Type a quantity of TST-|change quantity$"])
        self.warn(not hits, f"{where}: a floating control covers a line's tap target at rest: {hits[:4]}")

    def s13(self, sizes: dict, where: str) -> None:
        small = [t for t in sizes["targets"] if min(t["w"], t["h"]) < 44]
        self.check(not small, f"S13 {where}: primary tap targets under 44 px: {small[:6]}")
        tiny = [i for i in sizes["inputs"] if i["fontSize"] < 16]
        self.check(not tiny, f"S13 {where}: inputs under 16 px (an iPhone zooms in): {tiny[:6]}")
        self.note(f"S13 {where}: {len(sizes['targets'])} primary targets measured (smallest "
                  f"{min((min(t['w'], t['h']) for t in sizes['targets']), default='-')} px), {len(sizes['inputs'])} inputs "
                  f"(smallest {min((i['fontSize'] for i in sizes['inputs']), default='-')} px)")


def seed_script(kv: dict, session: dict, selection: dict | None) -> str:
    """localStorage / sessionStorage once per tab (a reload keeps what the page did since); the shop pick is
    dated by the page's own clock, `ago_ms` before now."""
    sel = "null" if selection is None else json.dumps(selection)
    return ("(() => { try { if (sessionStorage.getItem('qa-seeded')) return; sessionStorage.setItem('qa-seeded', '1');"
            f" const kv = {json.dumps(kv)}; for (const k in kv) localStorage.setItem(k, kv[k]);"
            f" const ss = {json.dumps(session)}; for (const k in ss) sessionStorage.setItem(k, ss[k]);"
            f" const sel = {sel}; if (sel) localStorage.setItem('yq-staff-customer', JSON.stringify({{ c: sel.c, at: Date.now() - sel.ago_ms }}));"
            " } catch (e) { /* about:blank */ } })()")


def customer(i: int = 1) -> dict:
    """The book row of Test Shop i as the catalog orders for it (pages/sales/lib.customerOf)."""
    s = book_shops()[i - 1]
    return {"key": s["key"], "focus_name": s["focus_name"], "name": s["contact_name"], "shop": s["name"], "phone": s["phone"],
            "area": s["area"], "email": None}


def cart_key(i: int = 1) -> str:
    return f"yq-shop-cart:staff:{book_shops()[i - 1]['key']}"


# ── cases ─────────────────────────────────────────────────────────────────────────────────────────

async def case_today(t: T) -> None:
    await t.goto("/today")
    if not t.check(await t.shell_ready(), "the salesman shell did not render"):
        return
    h1 = t.vis(t.page.get_by_role("heading", level=1))
    t.check(await t.wait(h1), "Today has no heading")
    t.check("Test" in (await t.text(h1)), "the greeting does not name the rep (Good …, Test)")
    for s in ("To confirm", "accessories sold this month", "Test Shop 3", "Due this week"):
        t.check(await t.wait(t.vis(t.page.get_by_text(s)), timeout=6000), f"Today does not show {s!r}")
    navs = await t.tab_nav()
    t.check(len(navs) == 1, f"one tab bar on screen, found {len(navs)}: {[n.get('label') for n in navs]}")
    if navs:
        links = navs[0]["links"]
        hrefs = [a["href"] for a in links]
        for href, label in TABS.items():
            t.check(hrefs.count(href) == 1, f"the {label} tab ({href}) shows {hrefs.count(href)} times in the tab bar")
        lit = [a for a in links if a["current"] == "page"]
        t.check([a["href"] for a in lit] == ["/today"], f"the tab bar lights {[a['name'] for a in lit]}, not only Today")
        t.note("tabs: " + ", ".join(f"{a['name']}{' (lit)' if a['current'] == 'page' else ''}" for a in links))
        if t.phone:
            small = [a for a in links if a["href"] in TABS and min(a["w"], a["h"]) < 44]
            t.check(not small, f"S13: tab links under 44 px: {small}")
    await t.shot(full=True)


async def case_today_no_catalog(t: T) -> None:
    await t.goto("/today")
    if not t.check(await t.shell_ready(), "the salesman shell did not render"):
        return
    t.check(await t.wait(t.vis(t.page.get_by_text("To confirm"))), "Today did not render for a rep without the catalog")
    navs = await t.tab_nav()
    links = navs[0]["links"] if navs else []
    hrefs = [a["href"] for a in links]
    t.check("/shop" not in hrefs, "the Catalog tab shows for a rep who may not open the catalog")
    t.check(not any(re.search(r"\bCatalog\b", a["name"]) for a in links), "a link named Catalog shows in the tab bar")
    for href in ("/today", "/shop-orders", "/customers", "/account"):
        t.check(href in hrefs, f"the {TABS[href]} tab is missing")
    lit = [a["href"] for a in links if a["current"] == "page"]
    t.check(lit == ["/today"], f"the tab bar lights {lit}, not only Today")
    t.note("tabs: " + ", ".join(f"{a['name']}{' (lit)' if a['current'] == 'page' else ''}" for a in links))
    await t.shot()


async def case_catalog(t: T) -> None:
    await t.goto("/shop")
    if not t.check(await t.catalog_ready(), "the catalog list did not render (no Add TST-A001)"):
        return
    body = await t.text()
    t.check(re.search(r"\b180\s+products\b", body) is not None, "the count does not read 180 products")
    n0 = await t.rows()
    t.check(0 < n0 <= 60, f"the first paint draws {n0} of 180 lines (it pages by 48: never all 180)")
    t.note(f"lines drawn at first: {n0}")
    if t.phone:
        t.s13(await t.taps([r"^Add (\d+ more )?TST-", r"^Type a quantity of TST-|change quantity$", r"Who is this order for", r"^Filters"]),
              "catalog")
        await t.covered("catalog")
    await t.shot("list")
    # search narrows the list (and every line it keeps is a charger)
    box = t.vis(t.page.get_by_role("searchbox"))
    if t.check(await t.wait(box), "no search field on the catalog"):
        await box.fill("charger")
        ok = await t.until(lambda: _count_is(t, r"\b40\s+products?\s+of\s+180\b"), timeout=4)
        t.check(ok, f"searching 'charger' does not narrow the list to 40 of 180 ({_count_line(await t.text())!r})")
        codes = await t.page.evaluate(r"""() => [...document.querySelectorAll('button[aria-label]')].map((b) => b.getAttribute('aria-label'))
            .filter((l) => /^Add (\d+ more )?TST-/.test(l)).map((l) => l.replace(/^Add (\d+ more )?/, ''))""")
        t.check(codes and all(c.startswith("TST-B") for c in codes), f"the narrowed list holds other lines: {[c for c in codes if not c.startswith('TST-B')][:5]}")
        await t.shot("search")
        clear = t.vis(t.page.get_by_role("button", name="Clear search"))
        if await t.wait(clear, timeout=2000):
            await t.act(clear)
        else:
            await box.fill("")
        t.check(await t.until(lambda: _count_is(t, r"\b180\s+products\b"), timeout=4), "clearing the search does not bring back 180 lines")
    # the list pages as the end nears, ahead of the finger: scroll to 600 px above the end, the next 48 are drawn
    grew = [await t.rows()]
    for _ in range(6):
        y_end = await t.page.evaluate("() => document.documentElement.scrollHeight - innerHeight")
        await t.scroll_down(max(0, int(y_end) - await t.scroll_y() - 600), step=400)
        await t.page.wait_for_timeout(250)
        grew.append(await t.rows())
        if grew[-1] >= 180:
            break
    t.note(f"lines drawn while scrolling: {grew}")
    t.check(grew[-1] == 180 and all(b >= a for a, b in zip(grew, grew[1:])) and len(set(grew)) > 2,
            f"the list did not keep up with the scroll (lines drawn: {grew}; it should page 48 → 96 → … → 180)")
    await t.shot("end")


async def _count_is(t: T, pattern: str) -> bool:
    return re.search(pattern, await t.text()) is not None


def _count_line(body: str) -> str:
    m = re.search(r"\d+\s+products?(\s+of\s+\d+)?", body)
    return m.group(0) if m else ""


async def case_order(t: T) -> None:
    api = t.api
    await t.goto("/shop")
    if not t.check(await t.catalog_ready(), "the catalog did not render"):
        return
    if not await t.pick_shop():
        return
    api.hold("/shop/quote")                          # the price server takes its time: the cart must wait for it
    await t.add("TST-A001")
    await t.add("TST-A002")
    dlg = await t.open_checkout()
    if not dlg:
        return
    await t.page.wait_for_timeout(700)               # past useQuote's 400 ms wait: the request is out, held
    t.check(bool(api.calls("/shop/quote", "POST")), "no quote was asked for the cart")
    text = await t.text(dlg)
    submit = t.submit_of(dlg)
    t.check(re.search(r"Checking prices", text) is not None, "S14: the checkout does not say Checking prices… before the quote")
    t.check(await submit.is_disabled(), "S14: Place order is pressable before the server priced the cart")
    t.check("0.000" not in text, "S14: the checkout shows BHD 0.000 before the quote")
    t.check(re.search(r"\bFree\b", text) is None, "S14: the checkout shows delivery Free before the quote")
    if t.phone:
        t.s13(await t.taps([r"Place order for this shop|Checking prices", r"^Close$"]), "checkout")
    await t.shot("checking")
    api.release("/shop/quote")
    ready = await t.until(lambda: submit.is_enabled(), timeout=6)
    t.check(ready, "S14: Place order did not become pressable once the quote answered")
    text = await t.text(dlg)
    t.check("BHD 1.250" in text, "the checkout does not show the server's total (BHD 1.250)")
    await t.shot("priced")
    if not ready:
        return
    # a double tap while the order is on its way sends ONE order
    api.slow["/shop/order"] = 1.5
    if t.phone:
        await submit.tap()
        await submit.tap(force=True)
    else:
        await submit.dblclick()
        await submit.click(force=True)
    placed = await t.wait(t.page.get_by_role("heading", name=re.compile(r"Order placed")), timeout=10000)
    t.check(placed, "the order-placed screen did not show")
    posts = api.calls("/shop/order", "POST")
    ids = sorted({str((p["body"] or {}).get("client_order_id")) for p in posts})
    t.note(f"POST /shop/order sent {len(posts)} time(s), client_order_id {ids}")
    t.check(len(posts) == 1, f"a double tap sent {len(posts)} orders")
    if not posts or not placed:
        return
    t.check(await t.wait(t.vis(t.page.get_by_text("QA-20001"))), "the order number is not on the order-placed screen")
    await t.shot("placed")
    done = posts[0]["done"] or posts[0]["t"]
    await t.page.wait_for_timeout(1500)
    # because of the order, not a timer: within 6 s of its answer (the Orders badge also polls every 60 s)
    re_orders = api.calls("/shop/orders", "GET", since=done, within=6)
    re_stock = api.calls("/shop/catalog", "GET", since=done, within=6)
    t.check(bool(re_orders), "after the order the Orders list (its count) was not read again")
    t.check(bool(re_stock), "after the order the catalog's stock was not read again")
    # the usual basket: picking the same shop again asks for it again (inside its 2-minute cache)
    cont = t.vis(t.page.get_by_role("button", name=re.compile(r"^\s*Continue")))
    if t.check(await t.wait(cont), "no Continue on the order-placed screen"):
        await t.act(cont)
        if await t.catalog_ready() and await t.pick_shop():
            ok = await t.until(lambda: bool(api.calls("/shop/me/basket", "GET", since=done)), timeout=4)
            t.check(ok, "after the order the shop's usual basket was not read again (a stale cache)")
    # the new order is on the Orders list (Confirmed: a rep's own order is born confirmed)
    orders_tab = t.tab("/shop-orders")
    if t.check(await t.wait(orders_tab), "no Orders tab"):
        await t.act(orders_tab)
        stage = t.vis(t.page.get_by_role("tab", name=re.compile(r"^\s*Confirmed")))
        if t.check(await t.wait(stage, timeout=8000), "the Orders stages did not render"):
            await t.act(stage)
            t.check(await t.wait(t.vis(t.page.get_by_text("QA-20001")), timeout=8000), "the new order QA-20001 is not on the Orders list")
            await t.shot("orders")


async def case_quote_failed(t: T) -> None:
    api = t.api
    api.fail.add("/shop/quote")
    await t.goto("/shop")
    if not t.check(await t.catalog_ready(), "the catalog did not render"):
        return
    if not await t.pick_shop():
        return
    await t.add("TST-A001")
    dlg = await t.open_checkout()
    if not dlg:
        return
    retry = dlg.get_by_role("button", name=RETRY_BUTTON)
    t.check(await t.wait(retry.first, timeout=8000), "a failed quote offers no Retry")
    text = await t.text(dlg)
    t.check("0.000" not in text, "a failed quote shows BHD 0.000")
    t.check(re.search(r"\bFree\b", text) is None, "a failed quote shows delivery Free")
    t.check(await t.submit_of(dlg).is_disabled(), "Place order is pressable with no price for the cart")
    t.note("the checkout says: " + " / ".join(x for x in re.findall(r"[^\n]*(?:not checked|Server error|try again|could not)[^\n]*", text, re.I)[:3]))
    await t.shot("failed")
    api.heal("/shop/quote")
    if await t.wait(retry.first, timeout=1000):
        await t.act(retry.first)
    ok = await t.until(lambda: t.submit_of(dlg).is_enabled(), timeout=6)
    t.check(ok, "Retry did not price the cart (Place order stays off)")
    t.check("BHD 0.500" in await t.text(dlg), "after Retry the checkout does not show the server's total (BHD 0.500)")
    await t.shot("retried")


async def case_shop_kept(t: T) -> None:
    await t.goto("/shop")
    if not t.check(await t.catalog_ready(), "the catalog did not render"):
        return
    if not await t.pick_shop():
        return
    await t.add("TST-A005")
    await t.page.wait_for_timeout(500)
    pill = lambda: t.vis(t.page.get_by_role("button", name=re.compile(r"\bTest Shop 1\b")))  # noqa: E731
    await t.page.reload(wait_until="domcontentloaded")
    t.check(await t.catalog_ready(), "the catalog did not come back after the reload")
    t.check(await t.wait(pill(), timeout=6000), "after a reload the shop being ordered for is gone")
    t.check(await t.until(lambda: _count_is(t, r"\b1 line\b"), timeout=4), "after a reload the shop's cart line is gone")
    await t.shot("reloaded")
    # the phone closes the app on a WhatsApp detour: a new tab of the same browser (localStorage stays,
    # sessionStorage is gone) — the audit's case, which a reload does not reach
    old = t.page
    t.page = await old.context.new_page()
    t.page.set_default_navigation_timeout(45000)
    await old.close()
    await t.goto("/shop")
    t.check(await t.catalog_ready(), "the catalog did not open again after the app was closed")
    t.check(await t.wait(pill(), timeout=6000), "after the app was closed and opened again the shop being ordered for is gone")
    t.check(await t.until(lambda: _count_is(t, r"\b1 line\b"), timeout=4), "after the app was closed and opened again the shop's cart line is gone")
    await t.shot("reopened")
    for hours, kept in ((11, True), (13, False)):
        await t.page.evaluate("(ms) => { const k = 'yq-staff-customer'; const v = JSON.parse(localStorage.getItem(k) || 'null');"
                              " if (v) { v.at = Date.now() - ms; localStorage.setItem(k, JSON.stringify(v)); } }", hours * 3600_000)
        await t.page.reload(wait_until="domcontentloaded")
        await t.catalog_ready()
        await t.page.wait_for_timeout(400)
        shows = await t.wait(pill(), timeout=3000 if kept else 1200)
        stored = (await t.storage())["local"].get("yq-staff-customer")
        if kept:
            t.check(shows, f"a shop picked {hours} h ago is gone (the rule keeps it 12 h)")
        else:
            t.check(not shows, f"a shop picked {hours} h ago is still the shop being ordered for (the rule is 12 h)")
            t.check(await t.wait(t.vis(t.page.get_by_role("button", name=re.compile(r"Who is this order for", re.I))), timeout=3000),
                    "after 12 h the catalog does not ask which shop the order is for")
            t.check(stored is None, "the expired pick is still stored")
    await t.shot("expired")


def rep_keys(st: dict) -> dict:
    """What sign-out takes off the phone (pages/sales/repDevice): the rep's carts, the shop, the catalog's search."""
    local = {k: v for k, v in st["local"].items() if k.startswith("yq-shop-cart:staff") or k == "yq-staff-customer"}
    return {**local, **{k: v for k, v in st["session"].items() if k == "yq-staff-catalog-browse"}}


async def case_sign_out(t: T) -> None:
    await t.goto("/account")
    if not t.check(await t.shell_ready(), "the salesman shell did not render"):
        return
    t.check(await t.wait(t.vis(t.page.get_by_role("heading", name=re.compile(r"My account")))), "the Me page did not render")
    before = await t.storage()
    out = t.vis(t.page.get_by_role("button", name=re.compile(r"^\s*Sign out\s*$")))
    if not t.check(await t.wait(out), "no Sign out button"):
        return
    await t.act(out)
    dlg = t.page.get_by_role("dialog", name=re.compile(r"Sign out\?"))
    if not t.check(await t.wait(dlg), "Sign out did not ask first (no Sign out? question)"):
        return
    text = await t.text(dlg)
    t.check(re.search(r"2 orders in progress on this phone \(3 lines\)", text) is not None,
            f"the question does not say what is in progress on the phone ({text[:120]!r})")
    mid = await t.storage()
    t.check(rep_keys(mid) == rep_keys(before), "the question already cleared something before the answer")
    t.check(not any("logout" in s for s in t.api.supabase), "the session ended before the answer")
    await t.shot("question")
    await t.act(dlg.get_by_role("button", name=re.compile(r"Stay signed in")))
    t.check(await t.wait(dlg, state="hidden", timeout=5000), "Stay signed in did not close the question")
    after_stay = await t.storage()
    t.check(after_stay["local"].get(cart_key(1)) is not None and "yq-staff-customer" in after_stay["local"],
            "Stay signed in still cleared the carts or the shop")
    await t.act(out)
    if not t.check(await t.wait(dlg), "the question did not open the second time"):
        return
    await t.act(dlg.get_by_role("button", name=re.compile(r"^\s*Sign out\s*$")))
    try:
        await t.page.wait_for_url(re.compile(r"/login"), timeout=10000)
    except PWError:
        t.check(False, "signing out did not reach the login page")
    end = await t.storage()
    left = [k for k in end["local"] if k.startswith("yq-shop-cart:staff") or k == "yq-staff-customer" or k == "yq-me-v1"]
    t.check(not left, f"after sign-out this phone still holds {left}")
    t.check("yq-staff-catalog-browse" not in end["session"], "after sign-out the catalog's remembered search stays")
    t.check(end["local"].get("yq-staff-view") == "list", "sign-out removed the device's own list/grid choice")
    t.check(end["local"].get("yq-shop-cart:qa-public-token") is not None, "sign-out removed a customer's share-link cart")
    t.note(f"Supabase calls: {t.api.supabase[-3:]}")
    await t.shot("signed_out")


async def cart_for_shop(t: T) -> bool:
    """The catalog with Test Shop 1 picked and two lines in its cart, built the way a rep does it."""
    await t.goto("/shop")
    if not t.check(await t.catalog_ready(), "the catalog did not render"):
        return False
    if not await t.pick_shop():
        return False
    await t.add("TST-A001")
    await t.add("TST-A002")
    return t.check(await t.until(lambda: _count_is(t, r"\b2 lines\b"), timeout=5), "the two lines do not show as the shop's order")


async def case_toast_over_sheet(t: T) -> None:
    api = t.api
    if not await cart_for_shop(t):
        return
    dlg = await t.open_checkout()
    if not dlg:
        return
    t.check(await t.until(lambda: t.submit_of(dlg).is_enabled(), timeout=6), "the cart was not priced")
    rm = dlg.get_by_role("button", name="Remove TST-A001", exact=True)
    if not t.check(await t.wait(rm), "no Remove TST-A001 in the checkout"):
        return
    await t.act(rm)
    t.check(await t.wait(rm, state="hidden", timeout=4000), "the line did not leave the checkout")
    found = {}
    ok = await t.until(lambda: _toast(t, r"^Removed TST-A001 ", found), timeout=4)
    t.check(ok, f"S11: the Undo toast is not on top of the open checkout: {found.get('v')}")
    await t.shot("undo_toast")
    undo = t.vis(t.page.get_by_role("button", name=re.compile(r"^\s*Undo\s*$")))
    if t.check(await t.wait(undo), "no Undo after removing a line"):
        await t.act(undo)
        t.check(await t.wait(rm, timeout=4000), "Undo did not put TST-A001 back in the checkout")
        t.check(re.search(r"2 products", await t.text(dlg)) is not None, "after Undo the checkout does not hold 2 products")
    # the app's own toasts: "Delivered." from an open order sheet
    await t.goto("/shop-orders?open=15001")
    sheet = t.page.get_by_role("dialog", name=re.compile(r"QA-15001"))
    if not t.check(await t.wait(sheet, timeout=10000), "the order sheet QA-15001 did not open"):
        return
    delivered = sheet.get_by_role("button", name=re.compile(r"^\s*Delivered\s*$"))
    if not t.check(await t.wait(delivered), "no Delivered in the order sheet"):
        return
    await t.act(delivered)
    found = {}
    ok = await t.until(lambda: _toast(t, r"^Delivered\.", found), timeout=5)
    t.check(ok, f"S11: the Delivered toast is not on top of the open order sheet: {found.get('v')}")
    t.check(bool(api.calls("/shop/orders/:id/deliver", "POST")), "Delivered sent nothing")
    await t.shot("sheet_toast")


async def _toast(t: T, pattern: str, found: dict) -> bool:
    """One sample of the toast's stacking; the evidence kept is the last sample that saw the toast (a toast
    under a sheet is found, not on top, until it times out — the miss after that is not the finding)."""
    v = await t.page.evaluate(TOAST_ON_TOP, pattern)
    if v.get("found") or "v" not in found:
        found["v"] = v
    return bool(v.get("found") and v.get("onTop") and v.get("dialogs", 0) >= 1)


async def case_chrome(t: T) -> None:
    if not await cart_for_shop(t):
        return
    await t.page.evaluate("() => window.scrollTo(0, 0)")
    await t.page.wait_for_timeout(400)
    if t.phone:
        await t.covered("catalog with a cart")
        t.s13(await t.taps([r"^\s*Review\s*$"]), "order bar")
        worst = []
        for total in (400, 800):
            await t.scroll_down(total)
            c = await t.page.evaluate(CHROME)
            worst.append(c)
            t.note(f"S12 at {c['scrollY']} px: chrome covers {c['total']} px — " + ", ".join(f"{p['sel']} {p['h']}px" for p in c["parts"][:6]))
            if total == 400:
                await t.shot("scrolled")
        t.check(all(c["total"] <= 200 for c in worst), f"S12: fixed chrome covers {[c['total'] for c in worst]} px (limit 200) while scrolling")
    else:
        await t.scroll_down(1200, step=300)
        slip = t.page.get_by_role("complementary", name=re.compile(r"Order slip"))
        box = await slip.bounding_box() if await t.wait(slip, timeout=3000) else None
        t.check(bool(box) and box["y"] >= -1 and box["y"] + 60 < t.vh, f"the docked order slip scrolled away ({box})")
        place = slip.get_by_role("button", name=re.compile(r"^\s*Place order\s*$"))
        pb = await place.bounding_box() if await t.wait(place, timeout=2000) else None
        t.check(bool(pb) and pb["y"] + pb["height"] <= t.vh, f"Place order is not in view after scrolling ({pb})")
        t.note(f"scrolled to {await t.scroll_y()} px; slip at {box and round(box['y'])} px, Place order bottom {pb and round(pb['y'] + pb['height'])} px")
        await t.shot("scrolled")


def failed_load(path: str, key: str, after: str, forbid: tuple[str, ...]):
    async def run(t: T) -> None:
        t.api.fail.add(key)
        await t.goto(path)
        if not t.check(await t.shell_ready(), "the salesman shell did not render"):
            return
        retry = t.vis(t.page.get_by_role("button", name=RETRY_BUTTON))
        t.check(await t.wait(retry, timeout=10000), f"a failed {key} offers no Try again")
        body = await t.text()
        m = ERROR_TEXT.search(body)
        t.check(m is not None, f"a failed {key} does not say it did not load")
        if m:
            t.note("it says: " + body[max(0, body.rfind("\n", 0, m.start()) + 1): body.find("\n", m.end()) if body.find("\n", m.end()) > 0 else None][:90])
        lies = [s for s in dict.fromkeys(forbid + FALSE_EMPTY) if re.search(rf"(?<!\w){re.escape(s)}", body)]
        t.check(not lies, f"a failed {key} reads as empty: {lies}")
        await t.shot("failed")
        t.api.heal(key)
        if await t.wait(retry, timeout=1000):
            asked = len(t.api.calls(key))
            await t.act(retry)
            t.check(await t.wait(t.vis(t.page.get_by_text(after)), timeout=8000), f"Try again did not bring the page back (no {after!r})")
            t.check(len(t.api.calls(key)) > asked, "Try again did not ask the server again")
            await t.shot("retried")
    return run


async def scrolled_catalog(t: T) -> int | None:
    """Today, then the Catalog tab (so the history has an entry before it), the list scrolled 1500 px: where it is."""
    await t.goto("/today")
    if not t.check(await t.shell_ready(), "the salesman shell did not render"):
        return None
    await t.act(t.tab("/shop"))
    if not t.check(await t.catalog_ready(), "the catalog did not render from its tab"):
        return None
    await t.scroll_down(1500, step=250)
    y0 = await t.scroll_y()
    t.note(f"the list scrolled to {y0} px")
    return y0 if t.check(y0 > 800, f"the list did not scroll ({y0} px)") else None


async def open_product(t: T):
    """Open the product of a line in the middle of the screen; its sheet, or None."""
    label = await t.page.evaluate(ROW_IN_VIEW)
    if not t.check(bool(label), "no product line in view to open"):
        return None
    await t.act(t.page.get_by_role("button", name=label, exact=True))
    code = label.split(" ")[0].rstrip(",")
    sheet = t.page.get_by_role("dialog", name=re.compile(re.escape(code)))
    return sheet if t.check(await t.wait(sheet), f"the product {code} did not open") else None


async def back_to_list(t: T, y0: int, how: str) -> None:
    t.check(await t.catalog_ready(), f"{how} did not return to the catalog")
    await t.page.wait_for_timeout(700)
    y = await t.scroll_y()
    t.note(f"{how}: {y} px")
    t.check(abs(y - y0) <= 24, f"S4: {how} lands at {y} px, not where the list was ({y0} px)")


async def case_back_keeps_place(t: T) -> None:
    y0 = await scrolled_catalog(t)
    if y0 is None:
        return
    sheet = await open_product(t)
    if sheet:
        await t.shot("product")
        await t.act(sheet.get_by_role("button", name="Close", exact=True).first)
        t.check(await t.wait(sheet, state="hidden", timeout=5000), "the product sheet did not close")
        await t.page.wait_for_timeout(300)
        y1 = await t.scroll_y()
        t.note(f"after closing the product: {y1} px")
        t.check(abs(y1 - y0) <= 24, f"S4: closing a product moved the list from {y0} to {y1} px")
    await t.act(t.tab("/shop-orders"))
    t.check(await t.wait(t.vis(t.page.get_by_role("heading", name=re.compile(r"^\s*Orders\s*$"))), timeout=8000), "the Orders tab did not open")
    await t.page.go_back(wait_until="domcontentloaded")
    await back_to_list(t, y0, "Back from the Orders tab")
    await t.shot("back")
    # last, as it leaves the history elsewhere: the phone's Back while a product is open
    sheet = await open_product(t)
    if sheet:
        await t.page.go_back(wait_until="domcontentloaded")
        await t.page.wait_for_timeout(600)
        path = await t.page.evaluate("() => location.pathname")
        t.warn(path == "/shop", f"Back with a product open leaves the catalog for {path} (the sheet takes no history entry)")
        t.note(f"Back with a product open: on {path}, sheet {'open' if await sheet.is_visible() else 'closed'}")


async def case_tab_keeps_place(t: T) -> None:
    y0 = await scrolled_catalog(t)
    if y0 is None:
        return
    await t.act(t.tab("/shop-orders"))
    t.check(await t.wait(t.vis(t.page.get_by_role("heading", name=re.compile(r"^\s*Orders\s*$"))), timeout=8000), "the Orders tab did not open")
    await t.act(t.tab("/shop"))
    await back_to_list(t, y0, "the Catalog tab after the Orders tab")
    await t.shot("tab_back")


SEL1 = {"c": customer(1), "ago_ms": 0}
CART1 = [{"item_code": "TST-A001", "qty": 1}, {"item_code": "TST-A002", "qty": 1}]

CASES = [
    {"name": "today", "run": case_today},
    {"name": "today_no_catalog", "run": case_today_no_catalog, "features": ["Shop Orders", "Market Intel"]},
    {"name": "catalog", "run": case_catalog},
    {"name": "order", "run": case_order, "timeout": 120},
    {"name": "quote_failed", "run": case_quote_failed},
    {"name": "shop_kept", "run": case_shop_kept},
    {"name": "sign_out", "run": case_sign_out, "selection": SEL1,
     "kv": {cart_key(1): json.dumps(CART1), "yq-shop-cart:staff": json.dumps([{"item_code": "TST-A007", "qty": 2}]),
            "yq-shop-cart:qa-public-token": json.dumps([{"item_code": "TST-A008", "qty": 1}]), "yq-staff-view": "list"},
     "session": {"yq-staff-catalog-browse": json.dumps({"q": "cable", "cat": "All", "sort": "featured"})}},
    {"name": "toast_over_sheet", "run": case_toast_over_sheet},
    {"name": "chrome", "run": case_chrome},
    {"name": "today_failed", "run": failed_load("/today", "/shop/me/today", "To confirm", ("Nothing waiting",))},
    {"name": "customers_failed", "run": failed_load("/customers", "/shop/me/book", "Test Shop 1", ("No shops yet",))},
    {"name": "catalog_failed", "run": failed_load("/shop", "/shop/catalog", "TST-A001", ("Nothing matches that",))},
    {"name": "orders_failed", "run": failed_load("/shop-orders", "/shop/orders", "Test Shop 3", ("No new orders yet",))},
    {"name": "back_keeps_place", "run": case_back_keeps_place},
    {"name": "tab_keeps_place", "run": case_tab_keeps_place,
     "pending": "expected after integration: the per-tab scroll restore (S4) lands with tonight's shell branch, not on the 2307ac4 base"},
]


# ── the runner ────────────────────────────────────────────────────────────────────────────────────

async def run_case(browser, base: str, web: Path, out: Path, case: dict, width: int) -> dict:
    site = urlparse(base).netloc
    me = me_for(case.get("features", REP_FEATURES))
    api = Api(site, api_host(web), supabase_ref(web), me)
    ctx = await browser.new_context(**VIEWPORTS[width], service_workers="block", locale="en-GB", color_scheme="light",
                                    reduced_motion="no-preference")
    kv = {f"sb-{api.sb_ref}-auth-token": json.dumps(fake_session(me["email"])), "yq-intro-v2": "done", "yq-role": "salesman",
          "yq-me-v1": json.dumps({"uid": UID, "me": me, "at": int(time.time() * 1000)}), **case.get("kv", {})}
    await ctx.add_init_script(seed_script(kv, case.get("session", {}), case.get("selection")))
    await ctx.route("**/*", api.on_route)
    errors: list[str] = []
    console: list[str] = []
    watched: set[int] = set()

    def watch(p) -> None:
        if id(p) in watched:
            return
        watched.add(id(p))
        # generous on a busy machine (the night's builds share it): a slow first paint is not a failure, a wrong one is
        p.set_default_timeout(10000)
        p.set_default_navigation_timeout(45000)
        p.on("pageerror", lambda e: errors.append(str(e)[:200]))
        p.on("console", lambda m: console.append(m.text[:200]) if m.type == "error" else None)

    ctx.on("page", watch)          # every tab of the case, also one a case opens (the app closed and opened again)
    page = await ctx.new_page()
    watch(page)
    t = T(page, api, base, case["name"], width, out)
    started = time.monotonic()
    try:
        await asyncio.wait_for(case["run"](t), timeout=case.get("timeout", 75))
    except asyncio.TimeoutError:
        t.check(False, f"the case timed out after {case.get('timeout', 75)} s")
    except Exception as e:  # noqa: BLE001 — a step Playwright could not drive is a failure, with its words
        t.check(False, f"error: {type(e).__name__}: {str(e)[:240]}")
    try:
        if CRASH_TEXT in await t.text():
            t.check(False, "the page fell into the portal's error screen (Something went wrong)")
    except PWError:
        pass
    if not t.shots:
        await t.shot()
    api.release()
    t.check(not errors, f"page errors: {errors[:3]}")
    # a 500 the case asked for, and the requests it aborted on purpose, are the only failed loads allowed
    noisy = [c for c in console if not re.search(r"Failed to load resource|net::ERR_FAILED|status of 500", c)]
    t.check(not noisy, f"console errors: {noisy[:3]}")
    result = {"case": case["name"], "width": width, "ok": not t.fails, "fails": t.fails, "warnings": t.warnings, "notes": t.notes, "shots": t.shots,
              "seconds": round(time.monotonic() - started, 1), "api_calls": len(api.hits),
              "unanswered": dict(api.unanswered)}
    if case.get("pending"):
        result["pending"] = case["pending"]
        if not t.fails:
            result["notes"].append("this pending case passes now: drop its pending mark")
    try:
        await ctx.unroute_all(behavior="ignoreErrors")
        await ctx.close()
    except PWError:
        pass
    return result


def selected(only: str, widths: list[int]) -> list[tuple[dict, int]]:
    want = [w.strip() for w in only.split(",") if w.strip()]
    out = []
    for c in CASES:
        for w in widths:
            if want and not any(x == c["name"] or x == f"{c['name']}@{w}" for x in want):
                continue
            out.append((c, w))
    return out


async def main_async(a) -> int:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    widths = [int(w) for w in a.widths.split(",") if w.strip()]
    for w in widths:
        if w not in VIEWPORTS:
            raise SystemExit(f"--widths: {w} is not one of {sorted(VIEWPORTS)}")
    todo = selected(a.only, widths)
    if not todo:
        raise SystemExit("--only matches no case (python web/scripts/sales_render_check.py --list)")
    failed, pending = [], []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            for c, w in todo:
                r = await run_case(browser, a.url, Path(a.web), out, c, w)
                print(json.dumps(r), flush=True)
                if not r["ok"]:
                    (pending if r.get("pending") else failed).append(f"{c['name']}@{w}")
        finally:
            await browser.close()
    counted = failed + ([] if a.allow_pending else pending)
    print(json.dumps({"summary": True, "cases": len(todo), "passed": len(todo) - len(failed) - len(pending), "failed": failed,
                      "pending_failed": pending, "pending_counted": not a.allow_pending, "out": str(out)}), flush=True)
    return 1 if counted else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default="http://localhost:5187")
    ap.add_argument("--web", default=str(Path(__file__).resolve().parents[1]), help="the web/ folder (its .env)")
    ap.add_argument("--out", default=str(Path(tempfile.gettempdir()) / "yq-sales-render"))
    ap.add_argument("--only", default="", help="case names, or case@width (e.g. order,chrome@390)")
    ap.add_argument("--widths", default=f"{PHONE},{DESK}")
    ap.add_argument("--allow-pending", action="store_true", help="a failing PENDING case (S4 on the base) does not fail the run")
    ap.add_argument("--list", action="store_true", help="list the cases and exit")
    a = ap.parse_args()
    if a.list:
        for c in CASES:
            print(c["name"] + (f"  (pending: {c['pending']})" if c.get("pending") else ""))
        return 0
    return asyncio.run(main_async(a))


if __name__ == "__main__":
    sys.exit(main())
