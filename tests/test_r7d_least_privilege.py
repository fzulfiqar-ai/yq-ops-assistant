"""R7d release review — least privilege (stream SECURITY: findings 4, 5, 6, 19, 20, 23).

    python -m tests.test_r7d_least_privilege

Same lightweight runner as the other suites; it borrows the in-memory stand-ins of
tests/test_r7d_merchant.py (a table-aware fake PostgREST client) and tests/test_r7b_command.py (a fake
read-only RPC answering every Command Centre SQL). Nothing here reaches a database or the network
(CI: SUPABASE_URL=https://ci.invalid). All data is synthetic — made-up shops, reps, items and numbers.

Covered:
  * "Same as last time" never reuses a rep-placed order: its device is the guessable staff:<login
    email>, so a browser sending that device + the order's token gets the plain refusal, nothing is
    written and the merchant's device list does not gain the staff id; a public browser can never
    speak in the staff namespace at all (no retry match on a rep's order, no device-list write,
    no phone recognition);
  * no staff order payload but an admin's carries the merchant's device_id, client_order_id or
    browser fingerprints (they unlock the public idempotency path: the order's token);
  * the Command Centre: cost, margin, gross profit, the below-cost list and stock AT COST only for
    admins and logins holding 'Margins'; the receivables book only with 'Receivables'; the shared
    cached payload is never changed by a narrowed read;
  * GET /report/inventory: per-item cost, stock at cost and a receipt's landed value only for admins
    and 'Margins' holders; the cache stays whole;
  * the web: management's drill links need the page's own grant (node half:
    web/scripts/least_privilege_test.mjs), restricted tiles hidden, Inventory's cost column / tile
    only when the API sends cost.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SUPABASE_URL", "https://ci.invalid")

# Both borrowed suites re-wrap sys.stdout at import; keep every wrapper alive so an earlier one is
# never garbage-collected (which would close the shared buffer).
_KEEP: list = [sys.stdout, sys.stderr]
from tests import test_r7d_merchant as M  # noqa: E402
_KEEP += [sys.stdout, sys.stderr]
from tests import test_r7b_command as C  # noqa: E402
_KEEP += [sys.stdout, sys.stderr]

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


STAFF_DEV = "staff:rep@example.com"


# ═══════════════════════════════════════════════════════════════════════════════
# 1. "Same as last time" and the staff device namespace (finding 4)
# ═══════════════════════════════════════════════════════════════════════════════

@test("reuse: a rep-placed order (device staff:<email>) is never reused — refused, nothing written, no device added")
def _():
    from app import shop
    staff_order = M._prev_order(source="salesman", device=STAFF_DEV, client_order_id="coid-staff")
    fake = M._db(shop_orders=[staff_order],
                 shop_customers=[{"id": 5, "phone": M.PHONE, "name": "Sara Owner", "shop": "Test Shop",
                                  "device_ids": ["dev-A"]}])
    with M._patched(fake):
        M._raises(lambda: shop.create_order(M._body(reuse_token="prev-token-aaaaaaaaaaaa", device_id=STAFF_DEV),
                                            market=True), "Please enter your phone number")
        assert shop.reuse_details("prev-token-aaaaaaaaaaaa", STAFF_DEV) is None
    assert not fake.inserts("shop_orders") and not fake.inserts("shop_order_lines"), "no order placed"
    assert not [c for c in fake.calls if c[0] == "rpc"], "no order number spent"
    cust = fake.rows("shop_customers")[0]
    assert cust["device_ids"] == ["dev-A"], cust


@test("reuse: a source 'salesman' order is refused even when its stored device looks like a merchant's")
def _():
    from app import shop
    fake = M._db(shop_orders=[M._prev_order(source="salesman", device="dev-A")])
    with M._patched(fake):
        assert shop.reuse_details("prev-token-aaaaaaaaaaaa", "dev-A") is None
        M._raises(lambda: shop.create_order(M._body(reuse_token="prev-token-aaaaaaaaaaaa"), market=True),
                  "Please enter your phone number")
    # the merchant's own marketplace order still works (the feature is not switched off)
    fake = M._db(shop_orders=[M._prev_order()])
    with M._patched(fake):
        assert shop.reuse_details("prev-token-aaaaaaaaaaaa", "dev-A")["phone"] == M.PHONE


@test("reuse: end to end — a rep's real order (create_order as staff) cannot be reused with its token and staff device")
def _():
    from app import shop
    fake = M._db()
    with M._patched(fake):
        placed = shop.create_order(M._body(customer={"name": "Sara Owner", "phone": "33001122", "shop": "Test Shop"},
                                           device_id=STAFF_DEV, client_order_id="coid-staff"),
                                   staff_email="rep@example.com")
        row = next(r for r in fake.rows("shop_orders") if r["id"] == placed["id"])
        assert row["device_id"] == STAFF_DEV and row["source"] == "salesman", row
        n_orders = len(fake.rows("shop_orders"))
        M._raises(lambda: shop.create_order(M._body(reuse_token=placed["token"], device_id=STAFF_DEV,
                                                    client_order_id="coid-x"), market=True),
                  "Please enter your phone number")
        assert len(fake.rows("shop_orders")) == n_orders, "no second order in the merchant's name"


@test("public device: staff:<email> from a browser is dropped — no retry match on a rep's order, no device-list write, no recognition")
def _():
    from app import shop
    staff_order = M._prev_order(source="salesman", device=STAFF_DEV, client_order_id="coid-staff",
                                status="confirmed")
    fake = M._db(shop_orders=[staff_order])
    with M._patched(fake), M._quiet_notify():
        o = shop.create_order(M._body(customer={"name": "Ali", "phone": "39001122"}, device_id=STAFF_DEV,
                                      client_order_id="coid-staff"), market=True)
    assert not o.get("duplicate") and o["id"] != staff_order["id"], "the rep's order is never handed back"
    assert o.get("token") != staff_order["token"]
    row = next(r for r in fake.rows("shop_orders") if r["id"] == o["id"])
    assert row["device_id"] is None, row
    for c in fake.rows("shop_customers"):
        assert STAFF_DEV not in (c.get("device_ids") or []), c
    assert shop.public_device(STAFF_DEV) is None and shop.public_device(" STAFF:x@y ") is None
    assert shop.public_device("dev-A") == "dev-A" and shop.public_device("") is None
    # a merchant row that (from before this fix) lists a staff id is still never recognised by it
    fake = M._db(shop_customers=[{"id": 5, "phone": M.PHONE, "name": "Sara Owner", "shop": "Test Shop",
                                  "device_ids": [STAFF_DEV, "dev-A"]}])
    with M._patched(fake):
        assert shop.recognize_phone("33001122", STAFF_DEV) is None
        assert shop.recognize_phone("33001122", "dev-A")["shop"] == "Test Shop"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. staff order payloads: device + idempotency keys for admins only (finding 5)
# ═══════════════════════════════════════════════════════════════════════════════

DEVICE_KEYS = ("device_id", "client_order_id", "ua", "ip_hash")


def _as(rows: dict):
    """(TestClient, patch) authenticating 'Bearer <local part>' as <local part>@example.com."""
    return C._client_as(rows)


def _user(email: str, role: str, features: list[str]) -> dict:
    return {**C._row(email, role), "features": list(features)}


def _order_world():
    o = M._prev_order(id=41, token="order-token-dddddddddddd", status="new", source="market", device="dev-merchant",
                      client_order_id="coid-merchant", ua="Mozilla/5.0 test", ip_hash="iphash-test", salesman_id=1)
    lines = [{**M._line(501, "T02", 10, 2.95), "order_id": 41, "unit_cost_bhd": 1.1, "cost_source": "mrn"}]
    return M._db(shop_orders=[o], shop_order_lines=lines)


@test("orders: management (read-only) never receives device_id / client_order_id / fingerprints — detail and list")
def _():
    rows = {"management@example.com": _user("management@example.com", "management",
                                            ["Dashboard", "Sales", "Margins", "Receivables", "Inventory", "Shop Orders"]),
            "admin@example.com": _user("admin@example.com", "admin", [])}
    fake = _order_world()
    client, p = _as(rows)
    with p, M._patched(fake):
        r = client.get("/shop/orders/41", headers={"Authorization": "Bearer management"})
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        for k in DEVICE_KEYS + ("token", "status_url"):
            assert k not in body, k
        for secret in ("dev-merchant", "coid-merchant", "iphash-test", "order-token-dddddddddddd"):
            assert secret not in r.text, secret
        r = client.get("/shop/orders", headers={"Authorization": "Bearer management"})
        assert r.status_code == 200 and "dev-merchant" not in r.text and "coid-merchant" not in r.text
        # the admin still reads the whole row (support: the retry key and the device behind an order)
        a = client.get("/shop/orders/41", headers={"Authorization": "Bearer admin"}).json()
        assert a["device_id"] == "dev-merchant" and a["client_order_id"] == "coid-merchant", a.get("device_id")


@test("orders: the order's own rep never receives the merchant's device or retry key either")
def _():
    rows = {"rep@example.com": _user("rep@example.com", "salesman", ["Catalog", "Shop Orders"])}
    fake = _order_world()
    client, p = _as(rows)
    with p, M._patched(fake):
        r = client.get("/shop/orders/41", headers={"Authorization": "Bearer rep"})
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        for k in DEVICE_KEYS:
            assert k not in body, k
        assert "dev-merchant" not in r.text and "coid-merchant" not in r.text


# ═══════════════════════════════════════════════════════════════════════════════
# 3. the Command Centre: cost and margin behind 'Margins', AR behind 'Receivables' (6, 20, 23)
# ═══════════════════════════════════════════════════════════════════════════════

MGMT_ALL = ["Dashboard", "Sales", "Margins", "Receivables", "Inventory", "Shop Orders"]
MGMT_NO_MARGINS = ["Dashboard", "Sales", "Receivables", "Inventory", "Shop Orders"]
MGMT_BARE = ["Dashboard", "Sales", "Inventory", "Shop Orders"]
COST_WORDS = ("gp_bhd", "net_ex_vat_bhd", "margin_pct", "costed_net_bhd", "profit.", "Selling below cost",
              "at cost", "Over a year of cover", "No sale in 90 days", "items_uncosted")


def _cc_rows():
    return {"admin@example.com": _user("admin@example.com", "admin", []),
            "full@example.com": _user("full@example.com", "management", MGMT_ALL),
            "nomargin@example.com": _user("nomargin@example.com", "management", MGMT_NO_MARGINS),
            "bare@example.com": _user("bare@example.com", "management", MGMT_BARE)}


def _cc(client, who: str, path: str = "/management/overview?period=mtd") -> dict:
    r = client.get(path, headers={"Authorization": f"Bearer {who}"})
    assert r.status_code == 200, (who, r.text[:300])
    return r.json()


def _keys(ov: dict) -> list[str]:
    return [m["key"] for m in ov["modules"]]


def _items(ov: dict) -> list[str]:
    return [i["key"] for i in ov["modules"][0]["items"]]


@test("command centre: management without 'Margins' gets no profitability, no below-cost item, no stock at cost; admin and a Margins holder do")
def _():
    from app import metrics
    fake = C.FakeRPC()
    metrics.invalidate()
    client, p = _as(_cc_rows())
    with p, C._Patched((metrics, "exec_sql", fake.q), (metrics, "exec_sql_params", fake.qp),
                       (metrics, "_default_salesmen", lambda: [dict(s) for s in C.SALESMEN])):
        admin = _cc(client, "admin")
        assert "profitability" in _keys(admin) and "receivables" in _keys(admin)
        assert "below_cost" in _items(admin) and C._tile(admin, "profit.official")["gp_bhd"] == 376.0
        shape = C._tile(admin, "products.stock_shape")
        assert shape["available"] is True and shape["chips"], shape

        narrow = _cc(client, "nomargin")            # served from the SAME cached payload
        assert "profitability" not in _keys(narrow) and "receivables" in _keys(narrow)
        assert "below_cost" not in _items(narrow) and "unowned_ar" in _items(narrow)
        shape = C._tile(narrow, "products.stock_shape")
        assert shape["available"] is False and shape["value"] is None and shape["restricted"] == "Margins", shape
        assert "chips" not in shape and "items_held" not in shape
        dump = json.dumps(narrow)
        for word in COST_WORDS:
            assert word not in dump, word
        assert not [t for m in narrow["modules"] for t in m.get("tiles") or [] if str(t["key"]).startswith("profit.")]

        full = _cc(client, "full")
        assert "profitability" in _keys(full) and "below_cost" in _items(full)
        assert C._tile(full, "products.stock_shape")["available"] is True

        again = _cc(client, "admin")                # the narrowed read never changed the cache
        assert again == admin
    metrics.invalidate()


@test("command centre: without 'Receivables' the receivables module and the unowned-AR item are left out; /management/attention too")
def _():
    from app import metrics
    fake = C.FakeRPC()
    metrics.invalidate()
    client, p = _as(_cc_rows())
    with p, C._Patched((metrics, "exec_sql", fake.q), (metrics, "exec_sql_params", fake.qp),
                       (metrics, "_default_salesmen", lambda: [dict(s) for s in C.SALESMEN])):
        bare = _cc(client, "bare")
        assert "receivables" not in _keys(bare) and "profitability" not in _keys(bare)
        assert "unowned_ar" not in _items(bare) and "below_cost" not in _items(bare)
        assert "sales" in _keys(bare) and "orders" in _keys(bare), "the rest of the page is whole"
        att = _cc(client, "bare", "/management/attention")
        keys = [i["key"] for i in att["items"]]
        assert "below_cost" not in keys and "unowned_ar" not in keys and "gp_bhd" not in json.dumps(att)
        full = [i["key"] for i in _cc(client, "admin", "/management/attention")["items"]]
        assert "below_cost" in full and "unowned_ar" in full
        full = [i["key"] for i in _cc(client, "full", "/management/attention")["items"]]
        assert "below_cost" in full and "unowned_ar" in full
    metrics.invalidate()


@test("command centre: scope_overview is pure — the input payload is never changed; admin (None) gets it as is")
def _():
    from app import command_api
    ov = C._overview()
    before = json.dumps(ov, sort_keys=True, default=str)
    out = command_api.scope_overview(ov, frozenset())
    assert json.dumps(ov, sort_keys=True, default=str) == before
    assert out is not ov and "profitability" not in _keys(out)
    assert command_api.scope_overview(ov, None) is ov
    assert command_api.scope_overview({"ok": 1}, frozenset()) == {"ok": 1}


# ═══════════════════════════════════════════════════════════════════════════════
# 4. GET /report/inventory: cost only for admins and 'Margins' (finding 19 / 23)
# ═══════════════════════════════════════════════════════════════════════════════

def _inventory_payload() -> dict:
    return {"rows": [{"item_name": "Item A", "current_stock": 10, "stock_value": 30.0, "sold_90d": 4, "days_cover": 75,
                      "suggested_reorder_qty": 0, "status": "healthy", "cost_value_bhd": 15.0, "cost_source": "focus_avg"}],
            "by_status": {"healthy": 1}, "stock_value": 30.0, "stock_value_basis": "selling_price",
            "stock_value_cost": 15.0, "stock_cost": {"cost_value_bhd": 15.0, "dead_cost_bhd": 0.0},
            "stock_qty": 10.0, "by_warehouse": [],
            "recent_receipts": [{"voucher": "MRN:T-1", "received_on": "2026-09-20", "items": 1, "units": 10,
                                 "value_bhd": 11.0}],
            "reserved": {"available": False}}


@test("inventory report: a login without 'Margins' gets no per-item cost, no stock at cost, no receipt value; the cache stays whole")
def _():
    from app import reports
    cached = _inventory_payload()
    before = json.dumps(cached, sort_keys=True)
    rows = {"member@example.com": _user("member@example.com", "member", ["Dashboard", "Sales", "Inventory", "Receivables"]),
            "buyer@example.com": _user("buyer@example.com", "member", ["Inventory", "Margins"]),
            "nomargin@example.com": _user("nomargin@example.com", "management", MGMT_NO_MARGINS),
            "full@example.com": _user("full@example.com", "management", MGMT_ALL),
            "admin@example.com": _user("admin@example.com", "admin", [])}
    client, p = _as(rows)
    with p, C._Patched((reports, "cached_report", lambda key: cached)):
        for who in ("member", "nomargin"):
            r = client.get("/report/inventory", headers={"Authorization": f"Bearer {who}"})
            assert r.status_code == 200, (who, r.text[:200])
            body = r.json()
            assert body["cost_hidden"] is True and "stock_value_cost" not in body and "stock_cost" not in body, who
            assert all("cost_value_bhd" not in x and "cost_source" not in x for x in body["rows"]), who
            assert all("value_bhd" not in x for x in body["recent_receipts"]), who
            assert body["stock_value"] == 30.0 and body["rows"][0]["current_stock"] == 10, "the rest stays"
        for who in ("buyer", "full", "admin"):
            body = client.get("/report/inventory", headers={"Authorization": f"Bearer {who}"}).json()
            assert body["rows"][0]["cost_value_bhd"] == 15.0 and body["stock_value_cost"] == 15.0, who
            assert body["recent_receipts"][0]["value_bhd"] == 11.0 and not body.get("cost_hidden"), who
    assert json.dumps(cached, sort_keys=True) == before, "the shared cached report was changed"


@test("inventory report: other reports pass through unchanged; report_for_viewer on a non-dict is a no-op")
def _():
    from app import reports
    x = {"rows": [{"cost_value_bhd": 1}]}
    assert reports.report_for_viewer("sales", x, sees_cost=False) is x
    assert reports.report_for_viewer("inventory", x, sees_cost=True) is x
    assert reports.report_for_viewer("inventory", None, sees_cost=False) is None


# ═══════════════════════════════════════════════════════════════════════════════
# 5. the web (source checks) + the node half
# ═══════════════════════════════════════════════════════════════════════════════

def _web(rel: str) -> str:
    return (ROOT / "web" / "src" / rel).read_text(encoding="utf-8")


@test("web: drills need the page's grant (nav, Gate, Command Centre); restricted tiles hidden; Inventory cost only when sent")
def _():
    nav = _web("lib/nav.ts")
    assert "export function managementMayOpen(pathname: string, features: readonly string[] = [])" in nav
    assert "{ to: '/prices', feature: 'Margins' }" in nav and "{ to: '/sales', feature: 'Sales' }" in nav
    assert "managementMayOpen(pathname, me?.features || [])" in _web("components/guards.tsx")
    cc = _web("pages/CommandCentre.tsx")
    assert "managementMayOpen(to.split('?')[0], me.features || [])" in cc
    assert "shownTiles(m.tiles).map" in cc and "!t.restricted" in cc
    inv = _web("pages/Inventory.tsx")
    assert "const showCost = !!data && !data.cost_hidden" in inv
    assert "cols={shownCols}" in inv and "c.key !== 'cost_value_bhd'" in inv
    assert "{!showCost ? null : sc ? (" in inv and "showCost && r.value_bhd != null" in inv


@test("node: managementMayOpen needs the page's feature (web/scripts/least_privilege_test.mjs)")
def _():
    node = shutil.which("node")
    script = ROOT / "web" / "scripts" / "least_privilege_test.mjs"
    assert script.exists()
    if not node or not (ROOT / "web" / "node_modules" / "typescript").exists():
        print("        SKIP: node or web/node_modules not available (the web job runs it after npm ci)")
        return
    r = subprocess.run([node, str(script)], cwd=str(ROOT / "web"), capture_output=True, text=True, encoding="utf-8",
                       timeout=180)
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, "node least-privilege tests failed:\n" + out[-2000:]
    assert re.search(r"\b\d+ passed, 0 failed", out), out[-400:]


@test("ci: this suite runs in the backend job and the node half in the web job")
def _():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "python -m tests.test_r7d_least_privilege" in ci
    assert "node scripts/least_privilege_test.mjs" in ci.split("\n  web:\n", 1)[1]


# ── runner ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    passed = failed = 0
    for name, fn in TESTS:
        try:
            fn()
            passed += 1
            print(f"PASS  {name}")
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
