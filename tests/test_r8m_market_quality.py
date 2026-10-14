"""R8m — Market Intel v2, Phases 0 and 1 (plan Part F): housekeeping, then quality capture and review.

    python -m tests.test_r8m_market_quality

Same hand-rolled runner and the same in-memory PostgREST + Storage stand-in as
tests/test_r7b_market_intel.py (borrowed from it). That fake now answers at most 1,000 rows per select and
honours .range()'s offset, exactly like PostgREST, so a read that forgets to page is caught here.
SUPABASE_URL, SUPABASE_KEY and DATABASE_URL are pinned to ci.invalid and every e-mail provider is silenced
BEFORE any import; the safety e-mail is a stub. Nothing reaches a database, a bucket or the network. Every
name is invented (Shop Alpha, Rep One, Brand X, Supplier X); the SKUs are made up.

Covered:
  * routes — every v2 route behind 'Market Intel' only; management's v2 writes refused centrally;
  * lists — active rows for page holders, ?all=1 an admin's, categories from the catalog's master order;
    create / edit / deactivate / merge (no self or circular merge), a brand rename keeps its old name;
  * the product picker — code / name search, 320 px thumbnails, the shop's own items first (at_shop);
  * capture v2 — a quality / packaging report opens a case or joins the open one (same SKU + issue within
    60 days; never a closed, not substantiated or merged one; never without a SKU), evidence 1 or 2 and up,
    the v2 columns, photo roles / sha256 / thumbnail; a v1 client gets exactly the R7b behaviour;
    client_observed_at's bounds; validation;
  * safety — one alert e-mail per case (claim + outcome events), never a failed capture when the e-mail
    fails or raises, sent after the answer over HTTP, filed under the safety type, alerted even with no
    issue types at all; no shop or rep in the e-mail, the note masked;
  * the workflow — every allowed and refused move (8 x 8), what each move needs, the evidence level only
    up, field edits, the stale-status guard; permissions for salesman, member, admin, management and a
    storekeeper holding the page;
  * the question / answer loop, merge and move with recounts, the minimal supplier action;
  * reading — the cases list (tabs, filters, paging, thumbnails), one case (timeline, photos, actions,
    suggestions), management masked, a rep's view (status words + his open question only);
  * Phase 0 — paged reads past 1,000 rows (board count + paging, review queue, item recount, own items,
    near-duplicates, the roll-up), thumbnails and their fallback, the people-photo copy (only it is ever
    signed unless an admin asks ?raw=1), the backfill and its script, the weekly roll-up job, a Rejected
    item reopened by a new sighting;
  * before R8m — everything still works on the R7b schema and the v2 routes answer 503 with the hint;
  * after the independent review — a capture that stopped half-way is finished by the phone's retry (503
    while it may still be running) or by the shop jobs' safety sweep, with ONE alert (an inserted claim
    the database allows once per case, carried by merges); a rep's picker answers only for his own shops;
    evidence never moves down on a stale read and only as far as the case's state supports (3 with the
    move to Verified, 4 from Action requested on, 5 once Resolved); two cases opened at once converge on
    the oldest; a stale page offset (416) is an empty page; the weekly roll-up keys on its Sunday slot and
    reads from its last run; a merge loop is refused; only a missing table means "not set up"; a brand
    moved to another supplier takes its open cases along and suppliers never pool; My reports name the
    issue; a lost race leaves no orphan; the backfill moves past a broken photo; the storekeeper reads
    masked; and the stand-in itself refuses an unknown column and a second claim;
  * the code — Python 3.11 grammar, no nested f-string, no 3-decimal amount of 1,000 or more.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import re
import sys
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# nothing may reach production: the project folder's .env (found by walking up from a worktree) points at it
os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"
for _k in ("RESEND_API_KEY", "BREVO_API_KEY", "SMTP_USER", "SMTP_PASS", "ALERT_EMAIL_TO", "TELEGRAM_BOT_TOKEN",
           "APP_BASE_URL"):
    os.environ[_k] = ""

# The borrowed suite re-wraps sys.stdout at import; keep both wrappers alive so the earlier one is never
# garbage-collected (which would close the shared buffer).
_KEEP: list = [sys.stdout, sys.stderr]
from tests import test_r7b_market_intel as R  # noqa: E402
_KEEP += [sys.stdout, sys.stderr]

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


# ── the synthetic world ─────────────────────────────────────────────────────────

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
R8M_TABLES = ("market_cases", "market_case_events", "suppliers", "brands", "market_issue_types", "supplier_actions",
              "supplier_action_cases", "supplier_reports")
USERS = R.USERS + [{"email": "store@example.com", "role": "storekeeper", "features": ["Storekeeper", "Market Intel"],
                    "status": "active"}]
SUPPLIERS = [{"id": 1, "name": "Supplier X", "code": "SX", "active": True, "merged_into": None},
             {"id": 2, "name": "Supplier Y", "code": None, "active": True, "merged_into": None},
             {"id": 3, "name": "Supplier Z", "code": None, "active": False, "merged_into": None}]
BRANDS = [{"id": 1, "name": "Brand X", "supplier_id": 1, "aliases": ["BX"], "renamed_from": None, "active": True,
           "merged_into": None},
          {"id": 2, "name": "Brand Y", "supplier_id": 2, "aliases": [], "renamed_from": None, "active": True,
           "merged_into": None}]
ISSUES = [
    {"id": 1, "kind": "quality", "code": "not_charging", "label_en": "Not charging", "label_ar": "لا يشحن",
     "safety_default": False, "fields": ["qty", "batch_ref"], "active": True, "sort": 10},
    {"id": 2, "kind": "quality", "code": "overheating", "label_en": "Gets hot", "label_ar": "يسخن",
     "safety_default": True, "fields": ["qty"], "active": True, "sort": 20},
    {"id": 3, "kind": "packaging", "code": "box_crushed", "label_en": "Box crushed", "label_ar": None,
     "safety_default": False, "fields": ["packaging_version"], "active": True, "sort": 10},
    {"id": 4, "kind": "demand", "code": "asked_for", "label_en": "Asked for", "label_ar": None,
     "safety_default": False, "fields": [], "active": True, "sort": 10},
    {"id": 5, "kind": "quality", "code": "old_issue", "label_en": "Old issue", "label_ar": None,
     "safety_default": False, "fields": [], "active": False, "sort": 99},
]


def _catalog() -> dict:
    return {"items": {
        "C-10": {"item_code": "C-10", "display_name": "C-10 Type-C Cable 1m", "category": "CABLE", "brand": "Brand X",
                 "product_image_url": "https://img.invalid/c10.jpg", "package_image_url": "https://img.invalid/c10p.jpg"},
        "P-22": {"item_code": "P-22", "display_name": "P-22 Power Bank 10000", "category": "POWER BANK",
                 "brand": "Brand X", "product_image_url": None, "package_image_url": None},
        "E-05": {"item_code": "E-05", "display_name": "E-05 Wired Earphone", "category": "EARPHONE", "brand": "Brand Y",
                 "product_image_url": "https://img.invalid/e05.jpg", "package_image_url": None},
    }, "order": ["C-10", "P-22", "E-05"]}


def _qdb(missing: set | None = None, **tables) -> "R._FakeDB":
    base = {"user_roles": USERS, "salesmen": R.SALESMEN, "shop_customers": R.SHOP_CUSTOMERS, "audit_log": [],
            "app_settings": [], "market_items": [], "market_observations": [], "market_photos": [],
            "market_item_decisions": [], "product_finds": [], "field_notes": [], "shop_events": [],
            "shop_restock_requests": [], "shop_orders": [], "shop_order_lines": [],
            "market_cases": [], "market_case_events": [], "suppliers": SUPPLIERS, "brands": BRANDS,
            "market_issue_types": ISSUES, "supplier_actions": [], "supplier_action_cases": []}
    base.update(tables)
    return R._FakeDB(base, missing=missing)


class _Mail:
    """Stands in for app.emailer.send_html: records each call; can answer 'not delivered' or raise."""

    def __init__(self, result: dict | None = None, raises: Exception | None = None):
        self.calls: list[dict] = []
        self.result = result or {"emailed": True, "via": "stub", "to": "owner@example.com"}
        self.raises = raises

    def __call__(self, subject, html, to=None):
        self.calls.append({"subject": subject, "html": html, "to": to})
        if self.raises:
            raise self.raises
        return dict(self.result)


class _qenv(R._env):
    """The R7b environment (fake client everywhere, tokens = the email's local part, limiter off) plus this
    suite's catalog, the e-mail stub and an empty at-shop cache."""

    def __init__(self, fake, mail: _Mail | None = None, catalog: dict | None = None):
        super().__init__(fake, catalog if catalog is not None else _catalog())
        self.mail = mail or _Mail()

    def __enter__(self):
        fake = super().__enter__()
        from app import emailer, market_quality as mq, shop_jobs
        self.pm = R._Patched((emailer, "send_html", self.mail), (shop_jobs, "get_client", lambda: fake))
        self.pm.__enter__()
        mq._at_shop.clear()
        return fake

    def __exit__(self, *exc):
        self.pm.__exit__(*exc)
        return super().__exit__(*exc)


def _cap(actor, photos=(), *, now=None, defer=None, **fields):
    from app import market_intel as mi
    fields.setdefault("client_uuid", str(uuid.uuid4()))
    return mi.capture(actor, fields, list(photos), now=now or NOW, defer=defer)


def _jpg(variant=0):
    return R._jpeg(variant=variant, orientation=1, gps=False, size=(1200, 900))


def _checker(size=(800, 600), sq=20) -> bytes:
    """A sharp black-and-white board: its edges are what a blur has to wipe out."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", size, (255, 255, 255))
    d = ImageDraw.Draw(img)
    for y in range(0, size[1], sq):
        for x in range(0, size[0], sq):
            if (x // sq + y // sq) % 2:
                d.rectangle([x, y, x + sq - 1, y + sq - 1], fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def _events(fake, case_id) -> list[tuple]:
    return [(e["event"], (e.get("detail") or {}).get("email")) for e in fake.rows("market_case_events")
            if e["case_id"] == case_id]


def _case(fake, case_id) -> dict:
    return next(c for c in fake.rows("market_cases") if c["id"] == case_id)


def _seed_case(fake, **kw) -> dict:
    """A case row written straight into the fake (the workflow tests start from any status)."""
    row = {"id": fake.next_id("market_cases"), "yq_item_code": "C-10", "issue_type_id": 1, "brand_id": 1,
           "supplier_id": 1, "status": "submitted", "evidence_level": 1, "safety": False, "title": "Case",
           "first_seen": NOW.isoformat(), "last_seen": NOW.isoformat(), "obs_count": 0, "shop_count": 0,
           "merged_into": None}
    row.update(kw)
    fake.tables.setdefault("market_cases", []).append(row)
    return row


def _seed_action(fake, case_id, **kw) -> dict:
    a = {"id": fake.next_id("supplier_actions"), "supplier_id": 1, "request": "Replace the batch", "owner": "Office",
         "due_date": "2026-10-31", "status": "open", "supplier_response": None, "resolution_check": None}
    a.update(kw)
    fake.tables.setdefault("supplier_actions", []).append(a)
    fake.tables.setdefault("supplier_action_cases", []).append({"action_id": a["id"], "case_id": case_id})
    return a


# ═══════════════════════════════════════════════════════════════════════════════
# 1. routes and gates
# ═══════════════════════════════════════════════════════════════════════════════

V2_ROUTES = {("GET", "/market-intel/lists"), ("POST", "/market-intel/lists/{kind}"),
             ("PATCH", "/market-intel/lists/{kind}/{entry_id}"), ("GET", "/market-intel/products"),
             ("GET", "/market-intel/cases"), ("GET", "/market-intel/cases/{case_id}"),
             ("PATCH", "/market-intel/cases/{case_id}"), ("POST", "/market-intel/cases/{case_id}/question"),
             ("POST", "/market-intel/observations/{obs_id}/answer"), ("POST", "/market-intel/cases/{case_id}/merge"),
             ("POST", "/market-intel/observations/{obs_id}/move"), ("POST", "/market-intel/cases/{case_id}/action"),
             ("PATCH", "/market-intel/actions/{action_id}")}


@test("routes: every v2 route is behind 'Market Intel' and nothing else; management's v2 writes are refused centrally")
def _():
    from fastapi.routing import APIRoute
    import app.main as m
    from app.auth import read_only_refuses
    found = {}
    for r in m.app.routes:
        if isinstance(r, APIRoute) and r.path.startswith("/market-intel"):
            for meth in r.methods:
                found[(meth, r.path)] = [f"feature:{d.call.feature}" if getattr(d.call, "feature", None) else "?"
                                         for d in r.dependant.dependencies]
    assert V2_ROUTES <= set(found), V2_ROUTES - set(found)
    assert all(g == ["feature:Market Intel"] for g in found.values()), found
    for meth, path in V2_ROUTES:
        concrete = re.sub(r"\{[^}]+\}", "7", path)
        assert read_only_refuses(meth, concrete) == (meth != "GET"), (meth, concrete)
    src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert src.index("_register_market_intel(app, limiter)") < src.index("_register_market_quality(app, limiter)")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. the admin lists
# ═══════════════════════════════════════════════════════════════════════════════

@test("lists: page holders read the active rows (a rep too, for the capture sheet); ?all=1 is an admin's; categories in the catalog's order")
def _():
    fake = _qdb()
    with _qenv(fake):
        c = R._client()
        rep = c.get("/market-intel/lists", headers=R._h("rep1"))
        assert rep.status_code == 200, rep.text[:300]
        body = rep.json()
        assert [s["name"] for s in body["suppliers"]] == ["Supplier X", "Supplier Y"]
        assert [b["name"] for b in body["brands"]] == ["Brand X", "Brand Y"] and body["brands"][0]["aliases"] == ["BX"]
        assert [i["code"] for i in body["issue_types"]] == ["not_charging", "overheating", "box_crushed", "asked_for"]
        it = body["issue_types"][1]
        assert it == {"id": 2, "kind": "quality", "code": "overheating", "label_en": "Gets hot", "label_ar": "يسخن",
                      "safety_default": True, "fields": ["qty"], "active": True, "sort": 20}
        assert body["categories"][:3] == ["CABLE", "CHARGER", "CAR CHARGER"] and "POWER BANK" in body["categories"]
        assert body["capabilities"]["can_admin"] is False
        assert len(c.get("/market-intel/lists?all=1", headers=R._h("rep1")).json()["suppliers"]) == 2, "?all=1 is an admin's"
        everything = c.get("/market-intel/lists?all=1", headers=R._h("boss")).json()
        assert len(everything["suppliers"]) == 3 and "old_issue" in [i["code"] for i in everything["issue_types"]]


@test("lists: an admin creates suppliers, brands and issue types; duplicates are 409, bad values 400; a member gets 403")
def _():
    fake = _qdb()
    with _qenv(fake):
        c = R._client()
        r = c.post("/market-intel/lists/suppliers", headers=R._h("boss"), json={"name": "Supplier W", "code": "SW"})
        assert r.status_code == 200 and r.json()["entry"]["name"] == "Supplier W", r.text[:300]
        assert c.post("/market-intel/lists/suppliers", headers=R._h("boss"), json={"name": "supplier x"}).status_code == 409
        assert c.post("/market-intel/lists/suppliers", headers=R._h("boss"), json={"code": "Q"}).status_code == 400
        assert c.post("/market-intel/lists/suppliers", headers=R._h("clerk"), json={"name": "Supplier V"}).status_code == 403
        assert c.post("/market-intel/lists/vendors", headers=R._h("boss"), json={"name": "x"}).status_code == 404
        r = c.post("/market-intel/lists/brands", headers=R._h("boss"),
                   json={"name": "Brand W", "supplier_id": 2, "aliases": ["BW", "bw", " Brand-W "]})
        assert r.status_code == 200 and r.json()["entry"]["aliases"] == ["BW", "Brand-W"], r.text[:300]
        assert c.post("/market-intel/lists/brands", headers=R._h("boss"), json={"name": "bx"}).status_code == 409, \
            "an alias of a live brand is taken"
        assert c.post("/market-intel/lists/brands", headers=R._h("boss"), json={"name": "B", "supplier_id": 99}).status_code == 400
        r = c.post("/market-intel/lists/issue-types", headers=R._h("boss"),
                   json={"kind": "quality", "label_en": "Cable breaks", "label_ar": "الكابل ينقطع",
                         "fields": ["qty", "batch_ref"], "safety_default": False, "sort": 30})
        assert r.status_code == 200 and r.json()["entry"]["code"] == "cable_breaks", r.text[:300]
        assert c.post("/market-intel/lists/issue-types", headers=R._h("boss"),
                      json={"kind": "quality", "label_en": "x", "code": "not_charging"}).status_code == 409
        for bad in ({"kind": "rumour", "label_en": "x"}, {"kind": "quality"}, {"kind": "quality", "label_en": "x", "code": "Bad Code"},
                    {"kind": "quality", "label_en": "x", "fields": ["colour"]}, {"kind": "quality", "label_en": "x", "sort": -1}):
            assert c.post("/market-intel/lists/issue-types", headers=R._h("boss"), json=bad).status_code == 400, bad
        assert c.get("/market-intel/lists", headers=R._h("rep1")).json()["issue_types"][2]["label_en"] == "Cable breaks", \
            "a new issue type reaches the capture sheet with no deploy"
    audits = [a for a in fake.rows("audit_log") if a["event"] == "market_intel.list_create"]
    assert len(audits) == 3 and {a["detail"]["list"] for a in audits} == {"suppliers", "brands", "issue-types"}


@test("lists: a brand rename keeps the old name as an alias + renamed_from; a merge forwards (inactive, merged_into); no self or circular merge")
def _():
    fake = _qdb()
    with _qenv(fake):
        c = R._client()
        r = c.patch("/market-intel/lists/brands/1", headers=R._h("boss"), json={"name": "Brand X Pro"})
        assert r.status_code == 200 and r.json()["changed"], r.text[:300]
        b = next(x for x in fake.rows("brands") if x["id"] == 1)
        assert b["name"] == "Brand X Pro" and b["renamed_from"] == "Brand X" and b["aliases"] == ["BX", "Brand X"]
        assert c.patch("/market-intel/lists/brands/1", headers=R._h("boss"), json={"name": "Brand X Pro"}).json()["changed"] is False
        r = c.patch("/market-intel/lists/brands/2", headers=R._h("boss"), json={"merged_into": 1})
        assert r.status_code == 200 and r.json()["entry"]["merged_into"] == 1 and r.json()["entry"]["active"] is False
        assert c.patch("/market-intel/lists/brands/1", headers=R._h("boss"), json={"merged_into": 1}).status_code == 400
        assert c.patch("/market-intel/lists/brands/1", headers=R._h("boss"), json={"merged_into": 2}).status_code == 409, "a circle"
        assert c.patch("/market-intel/lists/suppliers/2", headers=R._h("boss"), json={"active": False}).json()["entry"]["active"] is False
        assert c.patch("/market-intel/lists/suppliers/1", headers=R._h("clerk"), json={"active": False}).status_code == 403
        assert c.patch("/market-intel/lists/issue-types/1", headers=R._h("boss"), json={"code": "overheating"}).status_code == 409
        r = c.patch("/market-intel/lists/issue-types/1", headers=R._h("boss"), json={"label_ar": "لا يشحن أبدا", "sort": 5})
        assert r.status_code == 200 and r.json()["fields"] == ["label_ar", "sort"]
        # a sighting typed with the OLD name still lands on the renamed brand
        from app import market_quality as mq
        assert mq.Names.load().brand_by_text("brand x")["id"] == 1 and mq.Names.load().brand(2)["id"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 3. the product picker
# ═══════════════════════════════════════════════════════════════════════════════

SHOPS = R.SHOP_CUSTOMERS + [
    {"id": 8, "phone": "97330000008", "name": "Shop Eight", "shop": "Shop Eight", "area": "Riffa", "salesman_id": 2},
    {"id": 9, "phone": "97330000009", "name": "Shop Nine", "shop": "Shop Nine", "area": "Muharraq", "salesman_id": 1}]


@test("products: code and name search with the 320 px thumbnails; the shop's own items (marketplace, 180 days) first with at_shop")
def _():
    old = (NOW - timedelta(days=200)).isoformat()
    fake = _qdb(shop_customers=SHOPS,
                shop_orders=[{"id": 1, "customer_id": 7, "salesman_id": 1, "status": "delivered", "created_at": "2026-10-01T08:00:00+00:00"},
                             {"id": 2, "customer_id": 7, "salesman_id": 1, "status": "cancelled", "created_at": "2026-10-02T08:00:00+00:00"},
                             {"id": 3, "customer_id": 7, "salesman_id": 1, "status": "delivered", "created_at": old},
                             {"id": 4, "customer_id": 8, "salesman_id": 2, "status": "delivered", "created_at": "2026-10-03T08:00:00+00:00"},
                             {"id": 5, "customer_id": 9, "salesman_id": 2, "status": "delivered", "created_at": "2026-10-04T08:00:00+00:00"}],
                shop_order_lines=[{"id": 1, "order_id": 1, "item_code": "E-05"}, {"id": 2, "order_id": 2, "item_code": "P-22"},
                                  {"id": 3, "order_id": 3, "item_code": "P-22"}, {"id": 4, "order_id": 4, "item_code": "C-10"},
                                  {"id": 5, "order_id": 5, "item_code": "P-22"}])
    with _qenv(fake):
        c = R._client()
        got = c.get("/market-intel/products?q=c-10", headers=R._h("rep1"))
        assert got.status_code == 200, got.text[:300]
        top = got.json()[0]
        assert top["code"] == "C-10" and top["name"] == "C-10 Type-C Cable 1m" and top["brand"] == "Brand X"
        assert top["thumb_url"].endswith("/catalog/thumbs/C-10-product-320.webp")
        assert top["package_thumb_url"].endswith("/catalog/thumbs/C-10-package-320.webp") and top["at_shop"] is False
        assert [p["code"] for p in c.get("/market-intel/products?q=power bank", headers=R._h("rep1")).json()] == ["P-22"]
        assert c.get("/market-intel/products?q=P", headers=R._h("rep1")).json()[0]["code"] == "P-22"
        assert c.get("/market-intel/products?q=nothing+like+it", headers=R._h("rep1")).json() == []
        # his own customer (an order he took): the shop's own item first; a cancelled or 200-day-old order does not count
        shop = c.get("/market-intel/products?shop_phone=3000%200007", headers=R._h("rep1")).json()
        assert [(p["code"], p["at_shop"]) for p in shop] == [("E-05", True), ("C-10", False), ("P-22", False)]
        # assigned to him (shop_customers.salesman_id), its orders taken by another rep: still his
        assert c.get("/market-intel/products?shop_id=9&q=p-22", headers=R._h("rep1")).json()[0]["at_shop"] is True
        # another rep's shop, or a phone that is no customer, answers exactly what no shop answers
        none = c.get("/market-intel/products", headers=R._h("rep1")).json()
        for url in ("/market-intel/products?shop_id=8", "/market-intel/products?shop_phone=97330000008",
                    "/market-intel/products?shop_phone=39999999", "/market-intel/products?shop_id=999"):
            assert c.get(url, headers=R._h("rep1")).json() == none, url
        assert c.get("/market-intel/products?shop_id=8&q=c-10", headers=R._h("rep2")).json()[0]["at_shop"] is True
        # the office reads any shop
        assert c.get("/market-intel/products?shop_id=8&q=c-10", headers=R._h("clerk")).json()[0]["at_shop"] is True
        assert c.get("/market-intel/products?shop_id=7&q=e-05", headers=R._h("clerk")).json()[0]["at_shop"] is True
        assert len(c.get("/market-intel/products?limit=1", headers=R._h("rep1")).json()) == 1
        assert c.get("/market-intel/products", headers=R._h("rep3")).status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# 4. capture v2
# ═══════════════════════════════════════════════════════════════════════════════

@test("capture v2: a quality report opens a case (submitted, evidence 2 with a photo) with the v2 fields, the photo's role, sha256 and thumbnail")
def _():
    fake = _qdb()
    cu = str(uuid.uuid4())
    fields = dict(kind="complaint", yq_item_code="c-10", issue_type_id="1", report_basis="customer_said", qty_affected="3",
                  batch_ref="B2608", packaging_version="v2", photo_roles="label_batch", shop_name="Shop Alpha",
                  note="Stops charging after a week", client_uuid=cu)
    with _qenv(fake):
        card = _cap("rep1@example.com", [("p.jpg", _jpg())], **fields)
        n_writes, n_objects = len(fake.writes()), len(fake.storage.objects)
        again = _cap("rep1@example.com", [("p.jpg", _jpg())], **fields)       # the phone's retry queue
    assert again["replayed"] and again["case"]["id"] == card["case"]["id"] and again["observation_id"] == card["observation_id"]
    assert len(fake.writes()) == n_writes and len(fake.storage.objects) == n_objects, "a retry writes nothing"
    assert card["ok"] and card["result"] == "already_in_yq" and card["safety_alerted"] is False, card
    assert card["case"]["joined"] is False and card["case"]["status_words"] == "Received" and card["item"] is None
    assert card["message"] == "Quality report saved. The office will review it."
    case = fake.rows("market_cases")[0]
    assert (case["status"], case["evidence_level"], case["yq_item_code"], case["issue_type_id"]) == ("submitted", 2, "C-10", 1)
    assert case["title"] == "C-10 Type-C Cable 1m · Not charging" and (case["brand_id"], case["supplier_id"]) == (1, 1)
    assert (case["obs_count"], case["shop_count"], case["batch_ref"]) == (1, 1, "B2608")
    obs = fake.rows("market_observations")[0]
    assert obs["case_id"] == case["id"] and obs["item_id"] is None and fake.rows("market_items") == []
    assert (obs["issue_type_id"], obs["qty_affected"], obs["report_basis"], obs["packaging_version"]) == (1, 3, "customer_said", "v2")
    assert (obs["brand_text"], obs["supplier_text"], obs["brand_id"], obs["safety_flag"]) == ("Brand X", "Supplier X", 1, False)
    ph = fake.rows("market_photos")[0]
    assert ph["role"] == "label_batch" and ph["thumb_path"] == ph["path"].replace(".webp", "-t320.webp")
    assert hashlib.sha256(fake.storage.objects[("finds", ph["path"])][0]).hexdigest() == ph["sha256"]
    assert _events(fake, case["id"]) == [("created", None)]


@test("capture v2: the same SKU + issue within 60 days joins the open case (link event, distinct shops); a photo lifts evidence 1 -> 2; else a new case")
def _():
    fake = _qdb()
    with _qenv(fake):
        a = _cap("rep1@example.com", [], kind="complaint", yq_item_code="C-10", issue_type_id="1", shop_name="Shop Alpha",
                 note="Not charging")
        cid = a["case"]["id"]
        assert _case(fake, cid)["evidence_level"] == 1
        # the office renames the case with a shop in it: the next rep's card still names only the product
        fake.tables["market_cases"] = [dict(c, title="Shop Alpha batch problem") if c["id"] == cid else c
                                       for c in fake.rows("market_cases")]
        b = _cap("rep2@example.com", [("p.jpg", _jpg())], kind="complaint", yq_item_code="C-10", issue_type_id="1",
                 shop_name="Shop Beta")
        assert b["result"] == "seen_before" and b["case"]["joined"] and b["case"]["id"] == cid
        assert b["message"] == "Added to an open report: “C-10 Type-C Cable 1m · Not charging” (now 2 shops)."
        assert "Shop Alpha" not in json.dumps(b)
        assert (_case(fake, cid)["obs_count"], _case(fake, cid)["shop_count"], _case(fake, cid)["evidence_level"]) == (2, 2, 2)
        assert _events(fake, cid) == [("created", None), ("evidence", None), ("link", None)]
        ev = [e for e in fake.rows("market_case_events") if e["event"] == "evidence"][0]
        assert ev["detail"]["from"] == 1 and ev["detail"]["to"] == 2
        other_issue = _cap("rep1@example.com", [], kind="complaint", yq_item_code="C-10", issue_type_id="3")
        assert other_issue["case"]["id"] != cid
        # past the window, or closed / not substantiated / merged: a new case each time
        ids = {cid, other_issue["case"]["id"]}
        for change in ({"last_seen": (NOW - timedelta(days=61)).isoformat()}, {"status": "closed"},
                       {"status": "not_substantiated"}, {"merged_into": 999}):
            fake.tables["market_cases"] = [dict(c, **change) if c["id"] in ids else c for c in fake.rows("market_cases")]
            got = _cap("rep2@example.com", [], kind="complaint", yq_item_code="C-10", issue_type_id="1")
            assert got["case"]["id"] not in ids, change
            ids.add(got["case"]["id"])
        fake.tables["market_cases"] = [dict(c, last_seen=(NOW - timedelta(days=59)).isoformat())
                                       if c["id"] == max(ids) else c for c in fake.rows("market_cases")]
        assert _cap("rep2@example.com", [], kind="complaint", yq_item_code="C-10", issue_type_id="1")["case"]["id"] == max(ids), \
            "59 days ago is still within the window"


@test("capture v2: no SKU ('not sure / not ours') opens its own case titled with the typed words; packaging and v1 'complaint' + issue go to cases; a demand type stays a cluster")
def _():
    fake = _qdb()
    with _qenv(fake):
        a = _cap("rep1@example.com", [("p.jpg", _jpg())], kind="complaint", issue_type_id="1", title="Unbranded charger")
        b = _cap("rep2@example.com", [], kind="complaint", issue_type_id="1", title="Unbranded charger")
        assert a["case"]["id"] != b["case"]["id"], "unknown products never pool on the issue alone"
        assert _case(fake, a["case"]["id"])["title"] == "Unbranded charger · Not charging" and a["result"] == "new_find"
        p = _cap("rep1@example.com", [], kind="other", yq_item_code="E-05", issue_type_id="3", packaging_version="v3")
        assert p["case"] and _case(fake, p["case"]["id"])["packaging_version"] == "v3"
        d = _cap("rep1@example.com", [], kind="shop_asked", yq_item_code="E-05", issue_type_id="4", note="Wants 20")
        assert d["case"] is None and d["item"]["kind"] == "shop_asked" and d["result"] == "already_in_yq"
        obs = next(o for o in fake.rows("market_observations") if o["id"] == d["observation_id"])
        assert obs["issue_type_id"] == 4 and obs.get("case_id") is None and obs["item_id"] == d["item"]["id"]
        photo_only = _cap("rep1@example.com", [("q.jpg", _jpg(1))], kind="complaint", issue_type_id="1")
        assert photo_only["result"] == "saved_for_review" and _case(fake, photo_only["case"]["id"])["title"] == \
            "Product not identified · Not charging"


@test("capture v1 client: no v2 field gives exactly the R7b card (a complaint stays a cluster item), with the R8m tables in place")
def _():
    fake = _qdb()
    with _qenv(fake):
        a = _cap("rep1@example.com", [("p.jpg", _jpg())], kind="complaint", title="Brand Q cable breaks", shop_name="Shop Alpha")
        b = _cap("rep2@example.com", [], kind="competitor_price", title="c-10", price_bhd="1.1", shop_name="Shop Beta")
        r = R._client().post("/market-intel/observations", headers=R._h("rep1"),
                             files=[("photos", ("a.jpg", _jpg(), "image/jpeg"))],
                             data={"client_uuid": str(uuid.uuid4()), "kind": "new_product", "title": "Brand Q lamp"})
    assert a["result"] == "new_find" and a["item"]["kind"] == "complaint" and a["case"] is None and a["safety_alerted"] is False
    assert b["result"] == "already_in_yq" and b["yq_item"]["item_code"] == "C-10"
    assert r.status_code == 200 and r.json()["result"] == "new_find" and r.json()["case"] is None, r.text[:300]
    assert fake.rows("market_cases") == [] and len(fake.rows("market_items")) == 3
    first = fake.rows("market_observations")[0]
    assert first.get("case_id") is None and first["issue_type_id"] is None and first["safety_flag"] is False
    assert first["observed_at"] == NOW.isoformat(), "no phone time sent: the server's"


@test("capture: client_observed_at is trusted 7 days back and 10 minutes ahead (observed_at + the column), else the server's time")
def _():
    fake = _qdb()
    cases = [((NOW - timedelta(days=2)), True), ((NOW - timedelta(days=6, hours=23)), True),
             ((NOW + timedelta(minutes=9)), True), ((NOW - timedelta(days=8)), False),
             ((NOW + timedelta(minutes=11)), False)]
    with _qenv(fake):
        for t, ok in cases:
            card = _cap("rep1@example.com", [], kind="other", note="when", client_observed_at=t.isoformat().replace("+00:00", "Z"))
            o = next(x for x in fake.rows("market_observations") if x["id"] == card["observation_id"])
            if ok:
                assert R._same(o["observed_at"], t.isoformat()) and o["client_observed_at"] == t.isoformat(), (t, o)
            else:
                assert o["observed_at"] == NOW.isoformat() and o["client_observed_at"] is None, (t, o)
        junk = _cap("rep1@example.com", [], kind="other", note="when", client_observed_at="yesterday-ish")
        o = next(x for x in fake.rows("market_observations") if x["id"] == junk["observation_id"])
        assert o["observed_at"] == NOW.isoformat() and o["client_observed_at"] is None
    from app.market_intel import client_time
    assert client_time("2026-10-13T09:00:00", NOW) == datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc), "naive = UTC"


@test("capture: a bad v2 value (issue type, brand, supplier, photo role, basis, quantity, SKU) is a 400 and writes nothing")
def _():
    fake = _qdb()
    with _qenv(fake):
        for bad, want in (({"issue_type_id": "99"}, "issue type"), ({"issue_type_id": "x"}, "issue type"),
                          ({"brand_id": "99"}, "brand"), ({"supplier_id": "99"}, "supplier"),
                          ({"photo_roles": "selfie"}, "photo role"), ({"report_basis": "heard"}, "basis"),
                          ({"qty_affected": "0"}, "between"), ({"qty_affected": "2.5"}, "whole number"),
                          ({"yq_item_code": "NOPE-1"}, "not a YQ catalog code"), ({"safety_flag": "maybe"}, "true or false")):
            R._raises(lambda b=bad: _cap("rep1@example.com", [("p.jpg", _jpg())], kind="complaint", note="x", **b), want)
        R._raises(lambda: _cap("rep1@example.com", []), "photo or a note")
    assert fake.rows("market_observations") == [] and fake.rows("market_cases") == [] and fake.storage.objects == {}


# ═══════════════════════════════════════════════════════════════════════════════
# 5. safety
# ═══════════════════════════════════════════════════════════════════════════════

@test("safety: a flag (or the type's safety default) makes a safety case and e-mails ONCE per case; no shop or rep in it, the note masked")
def _():
    fake = _qdb()
    mail = _Mail()
    with _qenv(fake, mail):
        a = _cap("rep1@example.com", [("p.jpg", _jpg())], kind="complaint", yq_item_code="P-22", issue_type_id="1",
                 safety_flag="true", note="Swelled; owner on 3900 1234", shop_name="Shop Alpha", qty_affected="2")
        b = _cap("rep2@example.com", [], kind="complaint", yq_item_code="P-22", issue_type_id="1", safety_flag="1",
                 shop_name="Shop Beta")
        c = _cap("rep1@example.com", [], kind="complaint", yq_item_code="P-22", issue_type_id="2")    # safety default
    assert a["safety_alerted"] and b["safety_alerted"] and c["safety_alerted"] and "Manager alerted." in a["message"]
    assert a["case"]["id"] == b["case"]["id"] != c["case"]["id"]
    assert len(mail.calls) == 2, "one e-mail per case, not per sighting"
    case = _case(fake, a["case"]["id"])
    assert case["safety"] is True and _case(fake, c["case"]["id"])["safety"] is True
    assert _events(fake, case["id"]) == [("created", None), ("safety_alert", "queued"), ("safety_alert", "sent"), ("link", None)]
    sent = mail.calls[0]
    assert "Safety report" in sent["subject"] and "P-22 Power Bank 10000" in sent["subject"]
    for secret in ("Shop Alpha", "Shop Beta", "Rep One", "rep1", "3900 1234"):
        assert secret not in sent["html"] and secret not in sent["subject"], secret
    assert "[number]" in sent["html"] and "Not charging" in sent["html"]
    assert next(o for o in fake.rows("market_observations") if o["id"] == c["observation_id"])["safety_flag"] is True
    audits = [x for x in fake.rows("audit_log") if x["event"] == "market_intel.safety_alert"]
    assert len(audits) == 2 and all(x["detail"]["emailed"] for x in audits)


@test("safety: an e-mail that fails or raises never fails the capture (the outcome is on the case); over HTTP it is sent after the answer")
def _():
    for mail in (_Mail(result={"emailed": False, "reason": "boss@example.com: resend 403"}), _Mail(raises=RuntimeError("smtp down"))):
        fake = _qdb()
        with _qenv(fake, mail):
            card = _cap("rep1@example.com", [], kind="complaint", yq_item_code="C-10", issue_type_id="2", note="smoke")
        assert card["ok"] and card["safety_alerted"] and len(mail.calls) == 1
        out = [e for e in fake.rows("market_case_events") if e["event"] == "safety_alert"][-1]["detail"]
        assert out["email"] == "not_sent" and "boss@example.com" not in json.dumps(out), out
    fake = _qdb()
    mail = _Mail()
    with _qenv(fake, mail):
        r = R._client().post("/market-intel/observations", headers=R._h("rep1"),
                             data={"client_uuid": str(uuid.uuid4()), "kind": "complaint", "yq_item_code": "C-10",
                                   "issue_type_id": "1", "safety_flag": "true", "note": "sparks"})
    assert r.status_code == 200 and r.json()["safety_alerted"] is True, r.text[:300]
    assert len(mail.calls) == 1, "the background task ran after the answer"
    audit = [a for a in fake.rows("audit_log") if a["event"] == "market_intel.capture"][-1]["detail"]
    assert audit["case_id"] == r.json()["case"]["id"] and audit["safety_alerted"] is True and "sparks" not in json.dumps(audit)


@test("safety: without an issue type it is filed under the safety-default type; with no issue types at all it still alerts")
def _():
    fake = _qdb()
    mail = _Mail()
    with _qenv(fake, mail):
        a = _cap("rep1@example.com", [], kind="complaint", yq_item_code="E-05", safety_flag="yes", note="hot")
        assert a["case"] and _case(fake, a["case"]["id"])["issue_type_id"] == 2 and len(mail.calls) == 1
    fake = _qdb(market_issue_types=[])
    mail = _Mail()
    with _qenv(fake, mail):
        b = _cap("rep1@example.com", [], kind="complaint", title="Brand Q charger", safety_flag="yes", note="smoke")
        assert b["case"] is None and b["safety_alerted"] and "Manager alerted." in b["message"]
        assert len(mail.calls) == 1 and b["item"]["kind"] == "complaint"


# ═══════════════════════════════════════════════════════════════════════════════
# 6. the workflow
# ═══════════════════════════════════════════════════════════════════════════════

@test("workflow: every allowed move goes through and every other one is 409 (all 8 x 8 pairs)")
def _():
    from app import market_quality as mq
    allowed = {(a, b) for a, nxt in mq.NEXT_STATUS.items() for b in nxt}
    assert len(allowed) == 17
    needs = {"verified": {"verification_method": "sample_inspected", "verification_reason": "sample failed"},
             "not_substantiated": {"reason": "checked"}, "under_review": {"reason": "checked"}}
    fake = _qdb()
    with _qenv(fake):
        for a in mq.STATUSES:
            for b in mq.STATUSES:
                if a == b:
                    continue
                case = _seed_case(fake, status=a)
                _seed_action(fake, case["id"], supplier_response="replacing", resolution_check="new batch tested")
                body = {"status": b, **needs.get(b, {})}
                if (a, b) in allowed:
                    out = mq.update_case(case["id"], body, "boss@example.com", "admin")
                    assert out["changed"] and _case(fake, case["id"])["status"] == b, (a, b)
                    ev = [e for e in fake.rows("market_case_events") if e["case_id"] == case["id"] and e["event"] == "status"]
                    assert (ev[0]["from_status"], ev[0]["to_status"]) == (a, b)
                else:
                    R._raises(lambda c=case, x=body: mq.update_case(c["id"], x, "boss@example.com", "admin"),
                              "cannot move", 409)
                    assert _case(fake, case["id"])["status"] == a


@test("workflow: what a move needs — verified (method + finding, evidence >= 3), not substantiated / reopen (a reason; closed: admin), action requested (an action), resolved (response + check)")
def _():
    from app import market_quality as mq
    fake = _qdb()
    with _qenv(fake):
        c1 = _seed_case(fake)
        R._raises(lambda: mq.update_case(c1["id"], {"status": "verified"}, "clerk@example.com", "member"), "how it was verified")
        R._raises(lambda: mq.update_case(c1["id"], {"status": "verified", "verification_method": "a hunch"},
                                         "clerk@example.com", "member"), "how it was verified")
        R._raises(lambda: mq.update_case(c1["id"], {"status": "verified", "verification_method": "photos_sufficient"},
                                         "clerk@example.com", "member"), "what the verification found")
        R._raises(lambda: mq.update_case(c1["id"], {"verification_reason": "x"}, "clerk@example.com", "member"),
                  "recorded with the move to verified")
        out = mq.update_case(c1["id"], {"status": "verified", "verification_method": "photos_sufficient",
                                        "verification_reason": "the photos show the burnt port"}, "clerk@example.com", "member")
        row = _case(fake, c1["id"])
        assert out["evidence_level"] == 3 and row["evidence_level"] == 3 and row["verification_method"] == "photos_sufficient"
        st = [e for e in fake.rows("market_case_events") if e["case_id"] == c1["id"] and e["event"] == "status"][0]
        assert st["detail"] == {"verification_method": "photos_sufficient", "verification_reason": "the photos show the burnt port"}
        R._raises(lambda: mq.update_case(c1["id"], {"status": "action_requested"}, "clerk@example.com", "member"),
                  "supplier action first", 409)
        assert mq.update_case(c1["id"], {"verification_reason": "two samples failed"}, "clerk@example.com", "member")["changed"]
        c2 = _seed_case(fake)
        R._raises(lambda: mq.update_case(c2["id"], {"status": "not_substantiated"}, "clerk@example.com", "member"), "say why")
        mq.update_case(c2["id"], {"status": "not_substantiated", "reason": "works when tested"}, "clerk@example.com", "member")
        R._raises(lambda: mq.update_case(c2["id"], {"status": "under_review"}, "clerk@example.com", "member"), "say why")
        mq.update_case(c2["id"], {"status": "closed"}, "clerk@example.com", "member")
        R._raises(lambda: mq.update_case(c2["id"], {"status": "under_review", "reason": "came back"}, "clerk@example.com",
                                         "member"), "only an admin", 403)
        R._raises(lambda: mq.update_case(c2["id"], {"status": "under_review"}, "boss@example.com", "admin"), "say why")
        assert mq.update_case(c2["id"], {"status": "under_review", "reason": "came back"}, "boss@example.com",
                              "admin")["status"] == "under_review"
        c3 = _seed_case(fake, status="action_requested")
        a = _seed_action(fake, c3["id"], supplier_response="replacing the batch")
        R._raises(lambda: mq.update_case(c3["id"], {"status": "resolved"}, "clerk@example.com", "member"),
                  "response and yq's check", 409)
        next(x for x in fake.rows("supplier_actions") if x["id"] == a["id"])["resolution_check"] = "new batch tested"
        assert mq.update_case(c3["id"], {"status": "resolved"}, "clerk@example.com", "member")["status"] == "resolved"


@test("evidence: only moves up (lower is 409, equal no change), and only as far as the case's state supports — 3 with the move to Verified, 4 from Action requested on, 5 once Resolved; every rise is an event")
def _():
    from app import market_quality as mq
    up = lambda case, n: mq.update_case(case["id"], {"evidence_level": n}, "clerk@example.com", "member")  # noqa: E731
    fake = _qdb()
    with _qenv(fake):
        c = _seed_case(fake, evidence_level=1)
        assert up(c, 2)["evidence_level"] == 2, "evidence submitted: a reviewer may say so on any open case"
        R._raises(lambda: up(c, 1), "only moves up", 409)
        assert up(c, 2)["changed"] is False
        R._raises(lambda: up(c, 6), "1 to 5")
        R._raises(lambda: up(c, 3), "verify the case first", 409)        # not by hand: the move to Verified does it
        R._raises(lambda: up(c, 5), "verify the case first", 409)
        assert _case(fake, c["id"])["evidence_level"] == 2
        out = mq.update_case(c["id"], {"status": "verified", "verification_method": "sample_inspected",
                                       "verification_reason": "the sample's port is burnt"}, "clerk@example.com", "member")
        assert out["evidence_level"] == 3
        R._raises(lambda: up(c, 4), "from action requested on", 409)
        _seed_action(fake, c["id"], supplier_response="Replacing the batch", resolution_check="New batch tested")
        mq.update_case(c["id"], {"status": "action_requested"}, "clerk@example.com", "member")
        R._raises(lambda: up(c, 5), "once the case is resolved", 409)
        assert up(c, 4)["evidence_level"] == 4
        out = mq.update_case(c["id"], {"status": "resolved", "evidence_level": 5}, "clerk@example.com", "member")
        assert out["status"] == "resolved" and out["evidence_level"] == 5, "the move and the level together"
        R._raises(lambda: up(c, 3), "only moves up", 409)
        # the levels on offer follow the same rule (the case page shows only these)
        d = _seed_case(fake, evidence_level=1)
        e = _seed_case(fake, evidence_level=3, status="verified", verification_method="photos_sufficient")
        f = _seed_case(fake, evidence_level=3, status="action_requested", verification_method="several_shops")
        g = _seed_case(fake, evidence_level=4, status="closed", verification_method="sample_inspected")
        offer = lambda case, who="clerk": R._client().get(f"/market-intel/cases/{case['id']}",  # noqa: E731
                                                          headers=R._h(who)).json()["case"]["evidence_next"]
        assert (offer(d), offer(e), offer(f), offer(g), offer(d, "mgmt")) == ([2], [], [4], [5], [])
        # verifying a case already at 4 keeps 4 (it only ever rises); not substantiated never gets 3 by hand
        h = _seed_case(fake, evidence_level=4)
        mq.update_case(h["id"], {"status": "verified", "verification_method": "several_shops", "verification_reason": "4 shops"},
                       "clerk@example.com", "member")
        assert _case(fake, h["id"])["evidence_level"] == 4
        n = _seed_case(fake, evidence_level=2)
        mq.update_case(n["id"], {"status": "not_substantiated", "reason": "works when tested"}, "clerk@example.com", "member")
        R._raises(lambda: up(n, 3), "verify the case first", 409)
        # a merge carries a verified duplicate's level only as far as the target's own state supports
        src = _seed_case(fake, evidence_level=3, status="verified", verification_method="sample_inspected",
                         yq_item_code="E-05")
        dst = _seed_case(fake, evidence_level=1, yq_item_code="E-05")
        mq.merge_case(src["id"], dst["id"], "clerk@example.com", "member")
        assert _case(fake, dst["id"])["evidence_level"] == 2
    ev = [(x["detail"]["from"], x["detail"]["to"]) for x in fake.rows("market_case_events")
          if x["case_id"] == c["id"] and x["event"] == "evidence"]
    assert ev == [(1, 2), (2, 3), (3, 4), (4, 5)], ev


@test("PATCH fields: title, severity, assignee, due date, product, brand, supplier, safety; bad values 400; a merged case 409; a stale status 409")
def _():
    from app import market_quality as mq
    fake = _qdb()
    with _qenv(fake):
        c = _seed_case(fake, yq_item_code=None, brand_id=None, supplier_id=None)
        out = mq.update_case(c["id"], {"title": "Charger gets hot", "severity": 2, "assignee": "Office", "due_date": "2026-10-20",
                                       "yq_item_code": "p-22", "brand_id": 1, "supplier_id": 2}, "clerk@example.com", "member")
        row = _case(fake, c["id"])
        assert out["changed"] and (row["title"], row["severity"], row["due_date"], row["yq_item_code"]) == \
            ("Charger gets hot", 2, "2026-10-20", "P-22") and (row["brand_id"], row["supplier_id"]) == (1, 2)
        ed = [e for e in fake.rows("market_case_events") if e["case_id"] == c["id"] and e["event"] == "edit"][0]["detail"]
        assert ed["yq_item_code"] == {"from": None, "to": "P-22"} and ed["severity"] == {"from": None, "to": 2}
        assert mq.update_case(c["id"], {"severity": None, "due_date": None}, "clerk@example.com", "member")["changed"]
        assert _case(fake, c["id"])["severity"] is None
        for bad, want in (({"severity": 4}, "1, 2 or 3"), ({"due_date": "soon"}, "date"), ({"title": ""}, "title"),
                          ({"yq_item_code": "NOPE-1"}, "not a yq catalog code"), ({"brand_id": 99}, "brand"),
                          ({"status": "lost"}, "unknown status")):
            R._raises(lambda b=bad: mq.update_case(c["id"], b, "clerk@example.com", "member"), want)
        m = _seed_case(fake, merged_into=c["id"], status="closed")
        R._raises(lambda: mq.update_case(m["id"], {"title": "x"}, "clerk@example.com", "member"), "merged into", 409)
        real = mq._case

        def stale(cid):                             # another reviewer moved it between the read and the write
            row = real(cid)
            return dict(row, status="submitted") if row else row
        with R._Patched((mq, "_case", stale)):
            fake.tables["market_cases"] = [dict(x, status="under_review") if x["id"] == c["id"] else x
                                           for x in fake.rows("market_cases")]
            R._raises(lambda: mq.update_case(c["id"], {"title": "Again"}, "clerk@example.com", "member"),
                      "changed this case meanwhile", 409)


@test("permissions: salesman, member, admin, management and a storekeeper holding the page — who reads cases, who changes them, who edits the lists")
def _():
    fake = _qdb()
    with _qenv(fake):
        card = _cap("rep1@example.com", [], kind="complaint", yq_item_code="C-10", issue_type_id="1", note="dead")
        cid = card["case"]["id"]
        c = R._client()
        g = lambda who, p="": c.get(f"/market-intel/cases{p}", headers=R._h(who))  # noqa: E731
        patch = lambda who, body: c.patch(f"/market-intel/cases/{cid}", headers=R._h(who), json=body)  # noqa: E731
        assert g("rep1").status_code == 403 and g("rep1", f"/{cid}").status_code == 403
        assert patch("rep1", {"title": "x"}).status_code == 403
        assert c.post(f"/market-intel/cases/{cid}/question", headers=R._h("rep1"), json={"text": "x"}).status_code == 403
        assert c.post("/market-intel/lists/suppliers", headers=R._h("rep1"), json={"name": "S"}).status_code == 403
        assert g("rep3").status_code == 403, "no page, no cases"
        for who in ("clerk", "boss", "mgmt", "store"):
            assert g(who).status_code == 200 and g(who, f"/{cid}").status_code == 200, who
        caps = g("store").json()["capabilities"]
        assert caps == {"office": True, "can_review": False, "can_approve": False, "can_capture": True, "can_admin": False}
        assert patch("store", {"title": "x"}).status_code == 403, "a storekeeper reads, never decides"
        assert c.post("/market-intel/lists/suppliers", headers=R._h("store"), json={"name": "S"}).status_code == 403
        r = patch("mgmt", {"title": "x"})
        assert r.status_code == 403 and r.json()["detail"]["code"] == "read_only"
        r = c.post(f"/market-intel/cases/{cid}/merge", headers=R._h("mgmt"), json={"into_id": 1})
        assert r.status_code == 403 and r.json()["detail"]["code"] == "read_only"
        assert g("mgmt").json()["capabilities"]["can_review"] is False
        r = patch("clerk", {"status": "under_review"})
        assert r.status_code == 200 and r.json()["status"] == "under_review", r.text[:300]
        assert c.post("/market-intel/lists/suppliers", headers=R._h("clerk"), json={"name": "S"}).status_code == 403
        assert c.post("/market-intel/lists/suppliers", headers=R._h("boss"), json={"name": "S"}).status_code == 200
    audits = [a["event"] for a in fake.rows("audit_log")]
    assert "market_intel.case_update" in audits and "market_intel.list_create" in audits


# ═══════════════════════════════════════════════════════════════════════════════
# 7. the question / answer loop
# ═══════════════════════════════════════════════════════════════════════════════

@test("question / answer: the rep reads the office's question in My reports, answers with text + a photo (appended, role, thumbnail, evidence 2), the case is under review again")
def _():
    fake = _qdb()
    with _qenv(fake):
        a = _cap("rep1@example.com", [], kind="complaint", yq_item_code="C-10", issue_type_id="1", shop_name="Shop Alpha")
        b = _cap("rep2@example.com", [], kind="complaint", yq_item_code="C-10", issue_type_id="1", shop_name="Shop Beta")
        cid = a["case"]["id"]
        c = R._client()
        r = c.post(f"/market-intel/cases/{cid}/question", headers=R._h("clerk"), json={"text": "Which batch is on the box?"})
        assert r.status_code == 200 and r.json()["status"] == "more_info_required", r.text[:300]
        mine1 = c.get("/market-intel/mine", headers=R._h("rep1")).json()["observations"][0]
        assert mine1["case_status_words"] == "We need more from you" and mine1["open_question"] == "Which batch is on the box?"
        assert mine1["status_label"] == "We need more from you"
        mine2 = c.get("/market-intel/mine", headers=R._h("rep2")).json()["observations"][0]
        assert mine2["open_question"] == "Which batch is on the box?", "a question to the case reaches every rep on it"
        r = c.post(f"/market-intel/observations/{a['observation_id']}/answer", headers=R._h("rep2"), data={"text": "x"})
        assert r.status_code == 403
        r = c.post(f"/market-intel/observations/{a['observation_id']}/answer", headers=R._h("rep1"),
                   data={"text": "Batch 2608", "role": "label_batch"}, files=[("photo", ("label.jpg", _jpg(1), "image/jpeg"))])
        assert r.status_code == 200 and r.json() == {"ok": True, "observation_id": a["observation_id"],
                                                     "case_status_words": "Being looked at", "photo": True}, r.text[:300]
        case = _case(fake, cid)
        assert case["status"] == "under_review" and case["evidence_level"] == 2
        ph = [p for p in fake.rows("market_photos") if p["observation_id"] == a["observation_id"]]
        assert len(ph) == 1 and ph[0]["role"] == "label_batch" and ph[0]["thumb_path"].endswith("-t320.webp")
        ans = [e for e in fake.rows("market_case_events") if e["event"] == "answer"][0]
        assert ans["detail"] == {"observation_id": a["observation_id"], "text": "Batch 2608", "photo_id": ph[0]["id"]}
        assert ans["actor"] == "rep1@example.com"
        assert c.get("/market-intel/mine", headers=R._h("rep1")).json()["observations"][0]["open_question"] is None
        assert c.get("/market-intel/mine", headers=R._h("rep2")).json()["observations"][0]["open_question"] is None
        r = c.post(f"/market-intel/observations/{a['observation_id']}/answer", headers=R._h("rep1"), data={"text": "again"})
        assert r.status_code == 409, "no open question any more"
        # a question for one sighting reaches only its rep
        c.post(f"/market-intel/cases/{cid}/question", headers=R._h("clerk"),
               json={"text": "Can you send the receipt?", "observation_id": b["observation_id"]})
        assert c.get("/market-intel/mine", headers=R._h("rep1")).json()["observations"][0]["open_question"] is None
        assert c.get("/market-intel/mine", headers=R._h("rep2")).json()["observations"][0]["open_question"] == \
            "Can you send the receipt?"
        r = c.post(f"/market-intel/observations/{a['observation_id']}/answer", headers=R._h("rep1"), data={"text": "x"})
        assert r.status_code == 409
        r = c.post(f"/market-intel/observations/{b['observation_id']}/answer", headers=R._h("rep2"), data={})
        assert r.status_code == 400 and "answer or add a photo" in r.text
    audit = [x for x in fake.rows("audit_log") if x["event"] == "market_intel.case_question"]
    assert len(audit) == 2 and "receipt" not in json.dumps(audit)


@test("question: refused on a verified or merged case and for a sighting that is not on the case; the text is required")
def _():
    from app import market_quality as mq
    fake = _qdb()
    with _qenv(fake):
        v = _seed_case(fake, status="verified")
        R._raises(lambda: mq.ask_question(v["id"], "?", "clerk@example.com", "member"), "cannot ask", 409)
        s = _seed_case(fake)
        R._raises(lambda: mq.ask_question(s["id"], "  ", "clerk@example.com", "member"), "write the question")
        R._raises(lambda: mq.ask_question(s["id"], "?", "clerk@example.com", "member", observation_id=999), "not on this case")
        R._raises(lambda: mq.ask_question(s["id"], "?", "rep1@example.com", "salesman"), "only the office", 403)
        m = _seed_case(fake, merged_into=s["id"], status="closed")
        R._raises(lambda: mq.ask_question(m["id"], "?", "clerk@example.com", "member"), "merged into", 409)
        mq.ask_question(s["id"], "first?", "clerk@example.com", "member")
        mq.ask_question(s["id"], "second?", "clerk@example.com", "member")     # still waiting: no second move
    st = [e for e in fake.rows("market_case_events") if e["case_id"] == s["id"] and e["event"] == "status"]
    assert len(st) == 1 and _case(fake, s["id"])["status"] == "more_info_required"


# ═══════════════════════════════════════════════════════════════════════════════
# 8. merge, move, supplier actions
# ═══════════════════════════════════════════════════════════════════════════════

@test("merge: sightings and actions move, both are recounted, the source closes as merged, evidence and safety carried, no second e-mail; both histories say so")
def _():
    from app import market_quality as mq
    fake = _qdb()
    mail = _Mail()
    with _qenv(fake, mail):
        a = _cap("rep1@example.com", [("p.jpg", _jpg())], kind="complaint", yq_item_code="C-10", issue_type_id="2",
                 shop_name="Shop Alpha")                                   # safety default: e-mail 1
        b = _cap("rep2@example.com", [], kind="complaint", yq_item_code="C-10", issue_type_id="1", shop_name="Shop Beta")
        src, dst = a["case"]["id"], b["case"]["id"]
        act = _seed_action(fake, src)
        r = R._client().post(f"/market-intel/cases/{src}/merge", headers=R._h("clerk"), json={"into_id": dst})
        assert r.status_code == 200 and r.json() == {"ok": True, "case_id": src, "merged_into": dst, "moved_observations": 1}
        s, d = _case(fake, src), _case(fake, dst)
        assert (s["status"], s["merged_into"], s["obs_count"]) == ("closed", dst, 0)
        assert (d["obs_count"], d["shop_count"], d["evidence_level"], d["safety"]) == (2, 2, 2, True)
        assert len(mail.calls) == 1, "the problem was alerted once already"
        assert {x["case_id"] for x in fake.rows("supplier_action_cases") if x["action_id"] == act["id"]} == {src, dst}
        sm = [e for e in fake.rows("market_case_events") if e["case_id"] == src and e["event"] == "merge"][0]
        assert sm["detail"] == {"into": dst, "moved_observations": [a["observation_id"]]} and sm["to_status"] == "closed"
        assert [e["detail"]["from"] for e in fake.rows("market_case_events") if e["case_id"] == dst and e["event"] == "merge"] == [src]
        R._raises(lambda: mq.merge_case(src, dst, "clerk@example.com", "member"), "merged into", 409)
        R._raises(lambda: mq.merge_case(dst, dst, "clerk@example.com", "member"), "another case")
        R._raises(lambda: mq.merge_case(dst, 999, "clerk@example.com", "member"), "does not exist", 404)
        # the rep reads the target's status; a sighting still pointing at the merged-away case follows the merge
        assert R._client().get("/market-intel/mine", headers=R._h("rep1")).json()["observations"][0]["case_status_words"] == "Received"
        mq.ask_question(dst, "Which batch?", "clerk@example.com", "member")
        for o in fake.rows("market_observations"):
            if o["id"] == a["observation_id"]:
                o["case_id"] = src
        mine = R._client().get("/market-intel/mine", headers=R._h("rep1")).json()["observations"][0]
        assert (mine["case_status_words"], mine["open_question"]) == ("We need more from you", "Which batch?")


@test("move: one sighting to another case (or an old v1 complaint onto a case); both recounted, evidence carried, both histories say so")
def _():
    from app import market_quality as mq
    fake = _qdb()
    with _qenv(fake):
        a = _cap("rep1@example.com", [("p.jpg", _jpg())], kind="complaint", yq_item_code="C-10", issue_type_id="1", shop_name="S1")
        b = _cap("rep2@example.com", [], kind="complaint", yq_item_code="E-05", issue_type_id="1", shop_name="S2")
        old = _cap("rep2@example.com", [], kind="complaint", title="Brand Q cable breaks")        # v1: a cluster item
        r = R._client().post(f"/market-intel/observations/{a['observation_id']}/move", headers=R._h("clerk"),
                             json={"case_id": b["case"]["id"]})
        assert r.status_code == 200 and r.json()["from_case"] == a["case"]["id"], r.text[:300]
        assert _case(fake, a["case"]["id"])["obs_count"] == 0 and _case(fake, b["case"]["id"])["obs_count"] == 2
        assert _case(fake, b["case"]["id"])["evidence_level"] == 2, "the moved sighting brought its photo"
        out = mq.move_observation(old["observation_id"], b["case"]["id"], "clerk@example.com", "member")
        assert out["changed"] and out["from_case"] is None and _case(fake, b["case"]["id"])["obs_count"] == 3
        assert mq.move_observation(old["observation_id"], b["case"]["id"], "clerk@example.com", "member")["changed"] is False
        R._raises(lambda: mq.move_observation(old["observation_id"], 999, "clerk@example.com", "member"), "does not exist", 404)
        R._raises(lambda: mq.move_observation(old["observation_id"], b["case"]["id"], "rep1@example.com", "salesman"),
                  "only the office", 403)
    moves = [(e["case_id"], e["detail"]) for e in fake.rows("market_case_events") if e["event"] == "move"]
    assert (b["case"]["id"], {"observation_id": a["observation_id"], "from_case": a["case"]["id"]}) in moves
    assert (a["case"]["id"], {"observation_id": a["observation_id"], "to_case": b["case"]["id"]}) in moves


@test("supplier action: only on a verified case, a supplier is needed, it links and moves the case to Action requested; a response marks it responded; resolved needs YQ's check")
def _():
    fake = _qdb()
    with _qenv(fake):
        card = _cap("rep1@example.com", [], kind="complaint", issue_type_id="1", title="Unbranded cable")    # no brand
        cid = card["case"]["id"]
        c = R._client()
        body = {"request": "Replace the batch", "owner": "Office", "due_date": "2026-10-31"}
        assert c.post(f"/market-intel/cases/{cid}/action", headers=R._h("clerk"), json=body).status_code == 409
        c.patch(f"/market-intel/cases/{cid}", headers=R._h("clerk"),
                json={"status": "verified", "verification_method": "sample_inspected", "verification_reason": "port burnt"})
        r = c.post(f"/market-intel/cases/{cid}/action", headers=R._h("clerk"), json=body)
        assert r.status_code == 400 and "choose the supplier" in r.text.lower()
        r = c.post(f"/market-intel/cases/{cid}/action", headers=R._h("clerk"), json={**body, "supplier_id": 2})
        assert r.status_code == 200 and r.json()["status"] == "action_requested", r.text[:300]
        aid = r.json()["action"]["id"]
        assert r.json()["action"]["supplier"] == "Supplier Y" and _case(fake, cid)["status"] == "action_requested"
        assert [(x["action_id"], x["case_id"]) for x in fake.rows("supplier_action_cases")] == [(aid, cid)]
        r = c.patch(f"/market-intel/actions/{aid}", headers=R._h("clerk"), json={"supplier_response": "Will replace 24 units"})
        assert r.status_code == 200 and r.json()["action"]["status"] == "responded"
        assert c.patch(f"/market-intel/cases/{cid}", headers=R._h("clerk"), json={"status": "resolved"}).status_code == 409
        assert c.patch(f"/market-intel/actions/{aid}", headers=R._h("clerk"), json={"status": "done"}).status_code == 400
        c.patch(f"/market-intel/actions/{aid}", headers=R._h("clerk"), json={"resolution_check": "Tested the new batch: fine"})
        r = c.patch(f"/market-intel/cases/{cid}", headers=R._h("clerk"), json={"status": "resolved"})
        assert r.status_code == 200 and r.json()["status"] == "resolved", r.text[:300]
        assert c.patch(f"/market-intel/actions/{aid}", headers=R._h("mgmt"), json={"status": "resolved"}).status_code == 403
        detail = c.get(f"/market-intel/cases/{cid}", headers=R._h("clerk")).json()
        assert detail["actions"][0]["supplier_response"] == "Will replace 24 units"
    evs = [e["event"] for e in fake.rows("market_case_events") if e["case_id"] == cid]
    assert evs.count("action") == 3 and "status" in evs


# ═══════════════════════════════════════════════════════════════════════════════
# 9. reading
# ═══════════════════════════════════════════════════════════════════════════════

@test("cases list: the tabs (safety, new, need info, identify, all), filters (status, supplier — the brand's too —, brand, issue, dates), paging, thumbnails")
def _():
    fake = _qdb()
    with _qenv(fake):
        s = _cap("rep1@example.com", [("p.jpg", _jpg())], kind="complaint", yq_item_code="P-22", issue_type_id="2")
        n = _cap("rep1@example.com", [], kind="complaint", yq_item_code="E-05", issue_type_id="1")
        u = _cap("rep2@example.com", [], kind="complaint", issue_type_id="3", title="Some box")
        _seed_case(fake, status="more_info_required", brand_id=2, supplier_id=None,
                   last_seen=(NOW - timedelta(days=40)).isoformat(), first_seen=(NOW - timedelta(days=45)).isoformat())
        _seed_case(fake, status="closed", yq_item_code=None)
        _seed_case(fake, status="submitted", merged_into=s["case"]["id"])            # merged: never listed
        c = R._client()
        body = c.get("/market-intel/cases?tab=all", headers=R._h("clerk")).json()
        assert body["count"] == 5 and body["tabs"] == {"safety": 1, "new": 3, "need_info": 1, "identify": 1}, body["tabs"]
        row = next(x for x in body["rows"] if x["id"] == s["case"]["id"])
        assert row["safety"] and row["issue_type"] == {"id": 2, "code": "overheating", "label_en": "Gets hot",
                                                        "label_ar": "يسخن", "kind": "quality"}
        assert (row["brand"], row["supplier"], row["evidence_label"]) == ("Brand X", "Supplier X", "Evidence submitted")
        assert "-t320.webp" in row["thumb_url"] and row["status_label"] == "Submitted"
        ids = lambda q: [x["id"] for x in c.get(f"/market-intel/cases?{q}", headers=R._h("clerk")).json()["rows"]]  # noqa: E731
        assert ids("tab=safety") == [s["case"]["id"]] and ids("tab=identify") == [u["case"]["id"]]
        assert len(ids("tab=need_info")) == 1 and set(ids("tab=new")) == {s["case"]["id"], n["case"]["id"], u["case"]["id"]}
        by_y = ids("tab=all&supplier_id=2")       # E-05's case (Brand Y's supplier) + the case with only a brand
        assert len(by_y) == 2 and n["case"]["id"] in by_y, "the supplier through the case's brand"
        assert set(ids("tab=all&brand_id=1")) >= {s["case"]["id"]} and ids("tab=all&issue_type_id=3") == [u["case"]["id"]]
        assert len(ids("tab=all&status=closed")) == 1
        assert len(ids("tab=all&from=2026-10-01")) == 4 and len(ids("tab=all&to=2026-09-10")) == 1
        assert len(ids("tab=all&limit=2")) == 2 and len(ids("tab=all&limit=2&offset=4")) == 1
        for bad in ("tab=mine", "status=lost", "from=soon", "supplier_id=0"):
            assert c.get(f"/market-intel/cases?{bad}", headers=R._h("clerk")).status_code in (400, 422), bad


@test("case detail: the timeline, sightings with their photos (master + thumbnail), actions, suggestions of the same product / issue, next moves per role")
def _():
    fake = _qdb()
    with _qenv(fake):
        a = _cap("rep1@example.com", [("p.jpg", _jpg())], kind="complaint", yq_item_code="C-10", issue_type_id="1",
                 shop_name="Shop Alpha", note="Dead after a week")
        same_product = _cap("rep2@example.com", [], kind="complaint", yq_item_code="C-10", issue_type_id="3")
        same_issue = _cap("rep2@example.com", [], kind="complaint", yq_item_code="E-05", issue_type_id="1")
        _cap("rep2@example.com", [], kind="complaint", yq_item_code="P-22", issue_type_id="3")       # unrelated
        cid = a["case"]["id"]
        _seed_action(fake, cid)
        c = R._client()
        d = c.get(f"/market-intel/cases/{cid}", headers=R._h("clerk")).json()
        assert d["case"]["title"] == "C-10 Type-C Cable 1m · Not charging" and d["case"]["yq_item"]["item_code"] == "C-10"
        assert d["case"]["next_statuses"] == ["under_review", "more_info_required", "verified", "not_substantiated"]
        assert [e["event"] for e in d["events"]] == ["created"] and d["events"][0]["actor"] == "rep1"
        o = d["observations"][0]
        assert o["by"] == "Rep One" and o["shop_name"] == "Shop Alpha" and o["issue_label"] == "Not charging"
        p = o["photos"][0]
        ph = fake.rows("market_photos")[0]
        assert p["url"].endswith(ph["path"] + "?ttl=3600") and p["thumb_url"].endswith(ph["thumb_path"] + "?ttl=3600")
        assert p["role"] == "product" and p["has_people_flag"] is False and d["case"]["thumb_url"] == p["thumb_url"]
        assert d["actions"][0]["supplier"] == "Supplier X" and d["actions"][0]["status"] == "open"
        sug = {x["id"]: (x["same_product"], x["same_issue"]) for x in d["suggestions"]}
        assert sug == {same_product["case"]["id"]: (True, False), same_issue["case"]["id"]: (False, True)}
        assert d["capabilities"]["can_review"] is True
        assert c.get(f"/market-intel/cases/{cid}", headers=R._h("mgmt")).json()["case"]["next_statuses"] == []
        assert c.get("/market-intel/cases/999", headers=R._h("clerk")).status_code == 404
        closed = _seed_case(fake, status="closed")
        assert c.get(f"/market-intel/cases/{closed['id']}", headers=R._h("clerk")).json()["case"]["next_statuses"] == []
        assert c.get(f"/market-intel/cases/{closed['id']}", headers=R._h("boss")).json()["case"]["next_statuses"] == ["under_review"]


@test("management reads cases masked (titles, notes, questions, actions, the list); the office reads them as typed")
def _():
    fake = _qdb()
    phone, mail = "+973 3900 1234", "owner@shop.example"
    with _qenv(fake):
        a = _cap("rep1@example.com", [], kind="complaint", issue_type_id="1", title=f"Charger from {phone}",
                 note=f"Call {phone} or {mail}", shop_name="Shop 39001234", batch_ref="Batch 38001234")
        cid = a["case"]["id"]
        c = R._client()
        c.post(f"/market-intel/cases/{cid}/question", headers=R._h("clerk"), json={"text": f"Is {mail} the owner?"})
        _seed_action(fake, cid, request=f"Ask {mail}", supplier_response="Call 36001234")
        for url in (f"/market-intel/cases/{cid}", "/market-intel/cases?tab=all"):
            text = c.get(url, headers=R._h("mgmt")).text
            for raw in ("3900 1234", "39001234", "38001234", "36001234", mail):
                assert raw not in text, (url, raw)
        d = c.get(f"/market-intel/cases/{cid}", headers=R._h("mgmt")).json()
        assert d["case"]["title"].startswith("Charger from [number]") and d["observations"][0]["note"] == "Call [number] or [email]"
        q = next(e for e in d["events"] if e["event"] == "question")
        assert q["detail"]["text"] == "Is [email] the owner?" and d["actions"][0]["request"] == "Ask [email]"
        office = c.get(f"/market-intel/cases/{cid}", headers=R._h("clerk")).json()
        assert office["observations"][0]["note"] == f"Call {phone} or {mail}"


@test("a rep's view: My reports carry the status words and his open question only — never the case title, another rep or another shop; the cases are 403")
def _():
    fake = _qdb()
    with _qenv(fake):
        _cap("rep2@example.com", [], kind="complaint", yq_item_code="C-10", issue_type_id="1", shop_name="Shop Beta",
             note="Rep two's private words")
        _cap("rep1@example.com", [], kind="complaint", yq_item_code="C-10", issue_type_id="1", shop_name="Shop Alpha")
        c = R._client()
        body = c.get("/market-intel/mine", headers=R._h("rep1")).json()
        text = json.dumps(body)
        for secret in ("Shop Beta", "Rep Two", "rep2", "private words", "Type-C Cable 1m · Not charging"):
            assert secret not in text, secret
        row = body["observations"][0]
        assert row["case_status_words"] == "Received" and row["open_question"] is None and row["shop_name"] == "Shop Alpha"
        assert c.get("/market-intel/cases", headers=R._h("rep1")).status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# 10. Phase 0: paging, thumbnails, the people photo, backfill, the weekly job, a Rejected item
# ═══════════════════════════════════════════════════════════════════════════════

@test("paging: the fake answers 1,000 rows at most (as PostgREST does); the board counts and pages 1,205 clusters; the queue, a recount, own items, near-duplicates and the roll-up read past 1,000")
def _():
    from app import market_intel as mi
    n_items = 1205
    items = [{"id": i, "kind": "new_product", "title": f"Thing {i}", "status": "new", "created_at": NOW.isoformat(),
              "last_seen": NOW.isoformat()} for i in range(1, n_items + 1)]
    fake = _qdb(market_items=items)
    with _qenv(fake):
        assert len(fake.table("market_items").select("*").limit(5000).execute().data) == 1000, "the cap the bug hid behind"
        board = mi.board(office=True)
        assert board["count"] == n_items and len(board["items"]) == 300
        last = mi.board(office=True, offset=1200, limit=300)
        assert last["count"] == n_items and len(last["items"]) == 5 and last["offset"] == 1200
        assert {i["item_id"] for i in board["items"]}.isdisjoint({i["item_id"] for i in last["items"]})
        r = R._client().get("/market-intel/items?offset=1000&limit=500", headers=R._h("clerk")).json()
        assert r["count"] == n_items and len(r["items"]) == 205
    obs = [{"id": i, "client_uuid": str(uuid.uuid4()), "source": "rep", "kind": "other", "item_id": None,
            "created_by": "rep1@example.com", "observed_at": NOW.isoformat(), "created_at": NOW.isoformat()}
           for i in range(1, 1101)]
    obs += [{"id": 2000 + i, "client_uuid": str(uuid.uuid4()), "source": "rep", "kind": "other", "item_id": 1,
             "created_by": "rep1@example.com", "shop_name": f"Shop {i % 7}", "observed_at": NOW.isoformat(),
             "created_at": NOW.isoformat()} for i in range(1050)]
    photos = [{"id": 1, "observation_id": 1, "bucket": "finds", "path": "market/old.webp", "phash": None}]
    fake = _qdb(market_items=[{"id": 1, "kind": "other", "title": "Popular", "status": "new"}],
                market_observations=obs, market_photos=photos)
    with _qenv(fake):
        q = mi.review_queue()
        assert q["count"] == 1100 and len(q["unassigned"]) == 60 and mi.queue_counts()["unassigned"] == 1100
        assert mi.refresh_item(1)["obs_count"] == 1050 and mi.refresh_item(1)["shop_count"] == 7
        rep = mi.board(office=False, actor="rep1@example.com", filters={"mine": True})
        assert [i["item_id"] for i in rep["items"]] == [1] and rep["items"][0]["mine"] is True
        h = mi.process_photo(_jpg(), "a.jpg")["phash"]
        fake.tables["market_photos"] = [{"id": 1, "observation_id": 1, "bucket": "finds", "path": "market/hit.webp", "phash": h}]
        fake.tables["market_photos"] += [{"id": 1 + i, "observation_id": 1, "bucket": "finds", "path": f"x/{i}.jpg",
                                          "phash": None} for i in range(1, 1201)]
        assert (mi.near_duplicate([h]) or {}).get("photo_id") == 1, "the 1,200 newer hash-less imports no longer hide it"
    ev = [{"id": i, "event": "search_zero", "ts": (NOW - timedelta(minutes=i)).isoformat(), "session_id": f"s{i % 50}",
           "meta": {"q": "sandisk"}} for i in range(1, 1501)]
    fake = _qdb(shop_events=ev)
    with _qenv(fake):
        out = mi.roll_demand_signals(now=NOW)
    assert out["written"]["search_zero"] == 1 and fake.rows("market_observations")[0]["demand_qty"] == 1500
    cases = [{"id": i, "status": "submitted", "issue_type_id": 1, "title": f"Case {i}", "last_seen": NOW.isoformat(),
              "evidence_level": 1, "safety": False, "merged_into": None} for i in range(1, 1051)]
    fake = _qdb(market_cases=cases)
    with _qenv(fake):
        from app import market_quality as mq
        got = mq.cases(tab="new", limit=200, offset=1000)
        assert got["count"] == 1050 and len(got["rows"]) == 50 and got["tabs"]["new"] == 1050


@test("thumbnails: a 320 px WebP next to the master (<stem>-t320.webp, thumb_path); lists sign the thumbnail, the item page the master; no thumbnail -> the master")
def _():
    from PIL import Image
    from app import market_intel as mi
    fake = _qdb()
    with _qenv(fake):
        card = _cap("rep1@example.com", [("p.jpg", R._jpeg())], kind="new_product", title="Brand Q lamp")
        ph = fake.rows("market_photos")[0]
        assert ph["thumb_path"] == mi.derived_path(ph["path"], "-t320", ".webp") and ph["thumb_path"].endswith("-t320.webp")
        data, opts = fake.storage.objects[("finds", ph["thumb_path"])]
        t = Image.open(io.BytesIO(data))
        assert t.format == "WEBP" and max(t.size) == 320 and opts["content-type"] == "image/webp"
        assert len(data) < len(fake.storage.objects[("finds", ph["path"])][0]) / 3
        board = mi.board(office=True)["items"][0]
        assert board["thumb_url"].endswith(ph["thumb_path"] + "?ttl=3600") and board["cover_url"] == board["thumb_url"]
        detail = mi.item_detail(card["item"]["id"], office=True)
        p = detail["observations"][0]["photos"][0]
        assert p["url"].endswith(ph["path"] + "?ttl=3600") and p["thumb_url"].endswith(ph["thumb_path"] + "?ttl=3600")
        assert detail["cluster"]["cover_url"].endswith(ph["path"] + "?ttl=3600"), "the item page shows the master"
        mine = mi.mine("rep1@example.com")["observations"][0]["photos"][0]
        assert mine["url"].endswith("-t320.webp?ttl=3600")
        for row in fake.rows("market_photos"):
            row["thumb_path"] = None                                   # stored before R8m
        board = mi.board(office=True)["items"][0]
        assert board["thumb_url"].endswith(ph["path"] + "?ttl=3600"), "falls back to the master"
    assert mi.derived_path("notes/ddd.jpg", "-blur", ".webp") == "notes/ddd-blur.webp"
    assert mi.derived_path("x", "-t320", ".webp") == "x-t320.webp"


@test("people photo: flagging makes a blurred copy (<stem>-blur.webp); only it is ever signed — url and thumb — anywhere, unless an admin asks ?raw=1 (audited); unflagging signs the master again")
def _():
    from PIL import Image, ImageFilter, ImageStat
    fake = _qdb()
    with _qenv(fake):
        card = _cap("rep1@example.com", [("p.jpg", _checker())], kind="complaint", yq_item_code="C-10", issue_type_id="1",
                    shop_name="Shop Alpha")
        v1 = _cap("rep1@example.com", [("q.jpg", R._jpeg(variant=1))], kind="new_product", title="Brand Q lamp")
        c = R._client()
        for p in list(fake.rows("market_photos")):
            r = c.post(f"/market-intel/photos/{p['id']}/people", headers=R._h("clerk"), json={"flag": True})
            assert r.status_code == 200 and r.json()["blurred"] is True, r.text[:300]
        cp, ip = fake.rows("market_photos")
        assert cp["blurred_path"] == cp["path"].replace(".webp", "-blur.webp")
        blurred = Image.open(io.BytesIO(fake.storage.objects[("finds", cp["blurred_path"])][0])).convert("L")
        master = Image.open(io.BytesIO(fake.storage.objects[("finds", cp["path"])][0])).convert("L")
        edges = lambda im: ImageStat.Stat(im.filter(ImageFilter.FIND_EDGES)).mean[0]  # noqa: E731
        assert edges(blurred) < edges(master) / 2, (edges(blurred), edges(master))
        views = [c.get(f"/market-intel/cases/{card['case']['id']}", headers=R._h("clerk")),
                 c.get("/market-intel/cases?tab=all", headers=R._h("clerk")),
                 c.get(f"/market-intel/items/{v1['item']['id']}", headers=R._h("clerk")),
                 c.get(f"/market-intel/items/{v1['item']['id']}?raw=1", headers=R._h("clerk")),
                 c.get("/market-intel/items", headers=R._h("clerk")),
                 c.get(f"/market-intel/cases/{card['case']['id']}?raw=1", headers=R._h("mgmt")),
                 c.get("/market-intel/mine", headers=R._h("rep1"))]
        for r in views:
            assert r.status_code == 200, (r.request.url, r.text[:200])
            for p in (cp, ip):
                assert p["path"] not in r.text and p["thumb_path"] not in r.text, (str(r.request.url), p["path"])
        case_photo = views[0].json()["observations"][0]["photos"][0]
        assert case_photo["url"] == case_photo["thumb_url"] and case_photo["url"].endswith(cp["blurred_path"] + "?ttl=3600")
        assert case_photo["blurred"] is True and case_photo["has_people_flag"] is True
        assert views[1].json()["rows"][0]["thumb_url"].endswith("-blur.webp?ttl=3600")
        assert views[6].json()["observations"][0]["photos"] == [], "a rep never receives a people photo"
        raw = c.get(f"/market-intel/cases/{card['case']['id']}?raw=1", headers=R._h("boss"))
        assert cp["path"] in raw.text and raw.json()["observations"][0]["photos"][0]["blurred"] is False
        assert c.get(f"/market-intel/items/{v1['item']['id']}?raw=1", headers=R._h("boss")).json()["observations"][0]["photos"][0]["url"] \
            .endswith(ip["path"] + "?ttl=3600")
        assert len([a for a in fake.rows("audit_log") if a["event"] == "market_intel.raw_photos"]) == 2
        c.post(f"/market-intel/photos/{cp['id']}/people", headers=R._h("clerk"), json={"flag": False})
        back = c.get(f"/market-intel/cases/{card['case']['id']}", headers=R._h("clerk")).json()["observations"][0]["photos"][0]
        assert back["url"].endswith(cp["path"] + "?ttl=3600") and back["blurred"] is False
        assert cp["blurred_path"], "the copy is kept"
        flag_ev = [e for e in fake.rows("market_case_events") if e["event"] == "edit" and "photo_people" in e["detail"]]
        assert [e["detail"]["photo_people"]["to"] for e in flag_ev] == [True, False]
        # a copy that cannot be made leaves the photo with no URL at all — never the master
        fake.storage.fail_upload = True
        r = c.post(f"/market-intel/photos/{cp['id']}/people", headers=R._h("clerk"), json={"flag": True})
        assert r.status_code == 200
        fake.storage.fail_upload = False


@test("backfill: the dry run counts and writes nothing; --apply makes the missing thumbnails and blurred copies (masters untouched); the script dry-runs by default")
def _():
    from PIL import Image
    from app import market_intel as mi
    from scripts import market_intel_thumbs as script
    fake = _qdb()
    raw = R._jpeg()
    fake.storage.objects[("finds", "finds/legacy1.jpg")] = (raw, {})
    fake.storage.objects[("field-notes", "notes/n1.jpg")] = (_jpg(1), {})
    fake.tables["market_photos"] = [
        {"id": 1, "observation_id": 1, "bucket": "finds", "path": "finds/legacy1.jpg", "thumb_path": None,
         "has_people_flag": False, "blurred_path": None},
        {"id": 2, "observation_id": 1, "bucket": "field-notes", "path": "notes/n1.jpg", "thumb_path": None,
         "has_people_flag": True, "blurred_path": None},
        {"id": 3, "observation_id": 1, "bucket": "finds", "path": "finds/gone.jpg", "thumb_path": None,
         "has_people_flag": False, "blurred_path": None}]
    with _qenv(fake):
        dry = mi.backfill_thumbnails()
        assert dry == {"ok": True, "dry_run": True, "missing_thumbs": 3, "missing_blurs": 1, "thumbs": 0, "blurs": 0,
                       "failed": []}
        assert fake.writes() == [] and len(fake.storage.objects) == 2
        done = mi.backfill_thumbnails(limit=10, dry_run=False)
    assert (done["thumbs"], done["blurs"], len(done["failed"])) == (2, 1, 1) and done["failed"][0]["photo_id"] == 3
    rows = {p["id"]: p for p in fake.rows("market_photos")}
    assert rows[1]["thumb_path"] == "finds/legacy1-t320.webp" and rows[2]["thumb_path"] == "notes/n1-t320.webp"
    assert rows[2]["blurred_path"] == "notes/n1-blur.webp" and rows[3]["thumb_path"] is None
    assert max(Image.open(io.BytesIO(fake.storage.objects[("finds", "finds/legacy1-t320.webp")][0])).size) == 320
    assert fake.storage.objects[("finds", "finds/legacy1.jpg")][0] == raw, "the master is only read"
    args = script.parse_args([])
    assert args.apply is False and args.limit == 200 and script.parse_args(["--apply", "--limit", "5"]).apply is True
    seen = {}

    def fake_backfill(limit, dry_run):
        seen.update(limit=limit, dry_run=dry_run)
        return {"ok": True, "dry_run": dry_run, "missing_thumbs": 4, "missing_blurs": 0, "thumbs": 0, "blurs": 0, "failed": []}
    buf = io.StringIO()
    with R._Patched((mi, "backfill_thumbnails", fake_backfill)):
        old, sys.stdout = sys.stdout, buf
        try:
            code = script.main([])
        finally:
            sys.stdout = old
    assert code == 0 and seen == {"limit": 200, "dry_run": True} and "Dry run" in buf.getvalue()
    src = (ROOT / "scripts" / "market_intel_thumbs.py").read_text(encoding="utf-8")
    assert "delete" not in src.lower().replace("deleted", "") and '"upsert": "true"' not in src


@test("roll-up job: registered with the shop jobs; due on the first tick after Sunday 06:00 Bahrain, once (marker), the next tick after a missed Sunday; never marked before R7b")
def _():
    from app import shop_jobs
    assert ("market_demand_rollup", shop_jobs.market_demand_rollup) in shop_jobs.JOBS
    assert shop_jobs.JOBS[-1][0] == "market_demand_rollup"
    bh = timezone(timedelta(hours=3))
    at = lambda *a: datetime(*a, tzinfo=bh).astimezone(timezone.utc)  # noqa: E731
    assert shop_jobs.rollup_slot(at(2026, 10, 18, 6, 0)) == datetime(2026, 10, 18, 6, 0, tzinfo=bh)
    assert shop_jobs.rollup_slot(at(2026, 10, 18, 5, 59)) == datetime(2026, 10, 11, 6, 0, tzinfo=bh)
    assert shop_jobs.rollup_slot(at(2026, 10, 14, 12, 0)) == datetime(2026, 10, 11, 6, 0, tzinfo=bh)
    ev = [{"id": 1, "event": "search_zero", "ts": at(2026, 10, 17, 20, 0).isoformat(), "session_id": "a", "meta": {"q": "sandisk"}}]
    fake = _qdb(shop_events=ev)
    with _qenv(fake):
        first = shop_jobs.market_demand_rollup(at(2026, 10, 18, 6, 5))
        assert first["ok"] and first["written"]["search_zero"] == 1, first
        marker = next(s for s in fake.rows("app_settings") if s["key"] == "market_demand_rolled_at")
        assert marker["value"].startswith("2026-10-18T03:05")
        assert "skipped" in shop_jobs.market_demand_rollup(at(2026, 10, 18, 6, 20))
        assert "skipped" in shop_jobs.market_demand_rollup(at(2026, 10, 25, 5, 55)), "before next Sunday 06:00"
        late = shop_jobs.market_demand_rollup(at(2026, 10, 26, 9, 0))          # Sunday 25th missed: Monday's tick
        assert late["ok"] and next(s for s in fake.rows("app_settings")
                                   if s["key"] == "market_demand_rolled_at")["value"].startswith("2026-10-26T06:00")
    fake = _qdb(missing=set(R.MI_TABLES))
    with _qenv(fake):
        out = shop_jobs.market_demand_rollup(at(2026, 10, 18, 6, 5))
        assert "skipped" in out and "r7b_market_intel_migration" in out["skipped"]
    assert [s for s in fake.rows("app_settings") if s["key"] == "market_demand_rolled_at"] == []


@test("a Rejected item: a new sighting with its exact barcode reopens it to Researching with a decision row ('new sighting after rejection')")
def _():
    from app import market_intel as mi
    fake = _qdb()
    with _qenv(fake):
        a = _cap("rep1@example.com", [], kind="new_product", title="Mystery cable", barcode="6901234567892")
        mi.update_item(a["item"]["id"], {"status": "rejected", "reason": "not for us"}, "boss@example.com", "admin")
        b = _cap("rep2@example.com", [], kind="new_product", note="seen again", barcode="6901234567892")
    item = next(i for i in fake.rows("market_items") if i["id"] == a["item"]["id"])
    assert b["item"]["id"] == item["id"] and item["status"] == "researching" and b["item"]["status"] == "researching"
    d = fake.rows("market_item_decisions")[-1]
    assert (d["event"], d["from_status"], d["to_status"], d["reason"], d["actor_role"]) == \
        ("status", "rejected", "researching", "new sighting after rejection", "system")
    assert item["status_reason"] == "new sighting after rejection"


# ═══════════════════════════════════════════════════════════════════════════════
# 11. before the R8m migration
# ═══════════════════════════════════════════════════════════════════════════════

@test("before R8m: the capture saves as v1 (v2 fields ignored, no v2 column written, no thumbnail), the v2 routes answer 503 with the hint, the rest works")
def _():
    from app import market_intel as mi
    fake = _qdb(missing=set(R8M_TABLES))
    mail = _Mail()
    with _qenv(fake, mail):
        card = _cap("rep1@example.com", [("p.jpg", _jpg())], kind="complaint", yq_item_code="C-10", issue_type_id="1",
                    safety_flag="true", qty_affected="2", photo_roles="label_batch",
                    client_observed_at=(NOW - timedelta(hours=1)).isoformat())
        assert card["ok"] and card["case"] is None and card["result"] == "already_in_yq" and card["item"]["kind"] == "complaint"
        assert mail.calls == [], "v2 fields are ignored before the migration"
        picked = _cap("rep1@example.com", [], kind="shop_asked", yq_item_code="e-05")
        assert picked["yq_item"]["item_code"] == "E-05"
        R._raises(lambda: _cap("rep1@example.com", [], kind="complaint", issue_type_id="1"), "photo or a note")
        c = R._client()
        for meth, url, who in (("get", "/market-intel/lists", "rep1"), ("get", "/market-intel/cases", "clerk"),
                               ("get", "/market-intel/cases/1", "clerk"), ("patch", "/market-intel/cases/1", "boss"),
                               ("post", "/market-intel/lists/suppliers", "boss")):
            kw = {"json": {"name": "S", "title": "x"}} if meth != "get" else {}
            r = getattr(c, meth)(url, headers=R._h(who), **kw)
            assert r.status_code == 503 and "r8m_market_quality_migration.sql" in r.text, (url, r.status_code, r.text[:200])
        assert c.get("/market-intel/products?q=c-10", headers=R._h("rep1")).json()[0]["code"] == "C-10"
        mine = c.get("/market-intel/mine", headers=R._h("rep1")).json()["observations"]
        assert len(mine) == 2 and "case_status_words" not in mine[0]
        assert c.get("/market-intel/items", headers=R._h("clerk")).json()["count"] == 2
        pid = fake.rows("market_photos")[0]["id"]
        r = c.post(f"/market-intel/photos/{pid}/people", headers=R._h("clerk"), json={"flag": True})
        assert r.status_code == 200 and "blurred" not in r.json()
        assert mi.backfill_thumbnails()["ok"] is False
    v2_cols = {"case_id", "issue_type_id", "report_basis", "qty_affected", "batch_ref", "packaging_version", "safety_flag",
               "client_observed_at", "brand_id", "supplier_id", "brand_text", "supplier_text", "role", "sha256",
               "thumb_path", "caption", "blurred_path"}
    for op, table, payload, _f in fake.writes():
        for row in (payload if isinstance(payload, list) else [payload]):
            if table in ("market_observations", "market_photos"):
                assert not (set(row or {}) & v2_cols), (table, set(row) & v2_cols)
    assert not any("-t320" in p or "-blur" in p for (_b, p) in fake.storage.objects)
    obs = fake.rows("market_observations")[0]
    assert obs["observed_at"] == (NOW - timedelta(hours=1)).isoformat(), "the phone's time is Phase 0: kept"


# ═══════════════════════════════════════════════════════════════════════════════
# 12. after the independent review (findings 1-10 and the minor ones)
# ═══════════════════════════════════════════════════════════════════════════════

def _fails_once(fake, op: str, table: str):
    fake.fail[(op, table)] = RuntimeError("transient: connection reset")


def _first_attempt(**kw):
    try:
        _cap("rep1@example.com", [], **kw)
    except RuntimeError:
        return
    raise AssertionError("the first attempt must fail half-way")


@test("the stand-in refuses what PostgREST refuses: a column the table does not have (read, filter, order, write), an R8m column before R8m, a second alert claim")
def _():
    fake = _qdb()
    for build in (lambda: fake.table("market_photos").select("id,nope"),
                  lambda: fake.table("market_cases").select("*").eq("nope", 1),
                  lambda: fake.table("market_cases").select("*").order("nope"),
                  lambda: fake.table("market_case_events").insert({"case_id": 1, "event": "edit", "actor": "x",
                                                                   "created_at": NOW.isoformat()}),
                  lambda: fake.table("market_cases").update({"nope": 1}).eq("id", 1)):
        try:
            build().execute()
        except RuntimeError as e:
            assert "42703" in str(e) or "PGRST204" in str(e), str(e)
            continue
        raise AssertionError("an unknown column must be refused")
    before = _qdb(missing=set(R8M_TABLES))
    try:
        before.table("market_observations").insert({"client_uuid": str(uuid.uuid4()), "case_id": 1}).execute()
        raise AssertionError("an R8m column before R8m must be refused")
    except RuntimeError as e:
        assert "PGRST204" in str(e)
    claim = {"case_id": 1, "event": "safety_alert", "actor": "x", "detail": {"email": "queued"}}
    fake.table("market_case_events").insert(dict(claim)).execute()
    fake.table("market_case_events").insert({**claim, "detail": {"email": "sent"}}).execute()     # outcomes: any number
    try:
        fake.table("market_case_events").insert(dict(claim)).execute()
        raise AssertionError("a second claim must be refused")
    except RuntimeError as e:
        assert "23505" in str(e) and "one_alert_claim" in str(e)


@test("review 1: a capture that stopped after its sighting was saved is finished by the phone's retry — its case, its timeline line, ONE alert; safety_alerted only with the claim")
def _():
    fake, mail = _qdb(), _Mail()
    kw = dict(kind="complaint", yq_item_code="P-22", issue_type_id="1", safety_flag="true", note="smoke",
              client_uuid=str(uuid.uuid4()))
    with _qenv(fake, mail):
        _fails_once(fake, "insert", "market_cases")                    # the case was never opened
        _first_attempt(**kw)
        obs = fake.rows("market_observations")[0]
        assert obs["safety_flag"] is True and obs.get("case_id") is None and mail.calls == []
        card = _cap("rep1@example.com", [], **kw)
        again = _cap("rep1@example.com", [], **kw)
    assert card["replayed"] and card["safety_alerted"] and again["safety_alerted"] and "Manager alerted." in card["message"]
    assert len(fake.rows("market_cases")) == 1 and card["case"]["id"] == fake.rows("market_cases")[0]["id"]
    assert len(mail.calls) == 1, "the retry finished it; the second retry sent nothing"
    assert _events(fake, card["case"]["id"]) == [("created", None), ("safety_alert", "queued"), ("safety_alert", "sent")]
    # the sighting joined an open case and made it a safety case, then its first timeline line failed
    fake, mail = _qdb(), _Mail()
    case = _seed_case(fake, yq_item_code="P-22", issue_type_id=1, status="under_review", safety=False)
    kw = dict(kw, client_uuid=str(uuid.uuid4()))
    with _qenv(fake, mail):
        _fails_once(fake, "insert", "market_case_events")
        _first_attempt(**kw)
        card = _cap("rep1@example.com", [], **kw)
    assert card["safety_alerted"] and _case(fake, case["id"])["safety"] is True and len(mail.calls) == 1
    evs = [e["event"] for e in fake.rows("market_case_events") if e["case_id"] == case["id"]]
    assert evs == ["link", "safety_alert", "safety_alert"], evs
    # a non-safety quality report finished the same way says safety_alerted false
    fake, mail = _qdb(), _Mail()
    kw = dict(kind="complaint", yq_item_code="E-05", issue_type_id="3", client_uuid=str(uuid.uuid4()))
    with _qenv(fake, mail):
        _fails_once(fake, "insert", "market_cases")
        _first_attempt(**kw)
        card = _cap("rep1@example.com", [], **kw)
    assert card["case"] and card["safety_alerted"] is False and mail.calls == []


@test("review 1: a half-done capture younger than 20 s may still be running — its replay answers 503 (the phone retries later) and finishes it once it is older")
def _():
    from app import market_intel as mi
    fake, mail = _qdb(), _Mail()
    fake.clock = NOW - timedelta(seconds=5)                         # the sighting's created_at
    kw = dict(kind="complaint", yq_item_code="P-22", issue_type_id="2", note="hot", client_uuid=str(uuid.uuid4()))
    with _qenv(fake, mail):
        _fails_once(fake, "insert", "market_cases")
        _first_attempt(**kw)
        R._raises(lambda: _cap("rep1@example.com", [], **kw), "still saving", 503)
        assert fake.rows("market_cases") == [] and mail.calls == []
        card = mi.capture("rep1@example.com", dict(kw), [], now=NOW + timedelta(seconds=30))
    assert card["case"] and card["safety_alerted"] and len(mail.calls) == 1


@test("review 1: the safety sweep (every tick) finishes a safety report its phone never retried, claims an open safety case left without its claim, and never e-mails a problem twice (merges carry the claim)")
def _():
    from app import market_quality as mq, shop_jobs
    assert ("market_safety_sweep", shop_jobs.market_safety_sweep) in shop_jobs.JOBS
    fake, mail = _qdb(), _Mail()
    with _qenv(fake, mail):
        _fails_once(fake, "insert", "market_cases")
        _first_attempt(kind="complaint", yq_item_code="P-22", issue_type_id="1", safety_flag="true", note="smoke")
        obs = fake.rows("market_observations")[0]
        obs["created_at"] = (NOW - timedelta(seconds=30)).isoformat()
        assert shop_jobs.market_safety_sweep(NOW)["finished"] == [] and mail.calls == [], "a capture may still be running"
        obs["created_at"] = (NOW - timedelta(minutes=3)).isoformat()
        out = shop_jobs.market_safety_sweep(NOW)
        assert out["finished"] == [obs["id"]] and len(mail.calls) == 1
        assert next(o for o in fake.rows("market_observations") if o["id"] == obs["id"])["case_id"]
        assert shop_jobs.market_safety_sweep(NOW)["finished"] == [] and len(mail.calls) == 1, "once"
        # a reviewer's safety flag whose claim was never written
        five = (NOW - timedelta(minutes=5)).isoformat()
        flagged = _seed_case(fake, yq_item_code="E-05", issue_type_id=1, safety=True, updated_at=five)
        _seed_case(fake, yq_item_code="E-05", issue_type_id=3, safety=True, status="closed", updated_at=five)
        out = shop_jobs.market_safety_sweep(NOW)
        assert out["alerted"] == [flagged["id"]] and len(mail.calls) == 2
        assert shop_jobs.market_safety_sweep(NOW)["alerted"] == [] and len(mail.calls) == 2
        # merged into a case that was no safety case: the claim moves with it, no second e-mail
        target = _seed_case(fake, yq_item_code="C-10", issue_type_id=3, safety=False, updated_at=five)
        mq.merge_case(flagged["id"], target["id"], "clerk@example.com", "member")
        assert _case(fake, target["id"])["safety"] is True and mq.alert_claimed(target["id"])
        assert shop_jobs.market_safety_sweep(NOW + timedelta(hours=1))["alerted"] == [] and len(mail.calls) == 2
        later = _cap("rep2@example.com", [], kind="complaint", yq_item_code="C-10", issue_type_id="3", safety_flag="1")
        assert later["case"]["id"] == target["id"] and later["safety_alerted"] and len(mail.calls) == 2
    fake = _qdb(missing=set(R8M_TABLES))
    with _qenv(fake):
        assert "skipped" in shop_jobs.market_safety_sweep(NOW)


@test("review 3: evidence never moves down — a capture that read the case before a reviewer verified it, or a reviewer's stale raise, leave the higher level and write no event")
def _():
    from app import market_intel as mi, market_quality as mq
    fake = _qdb()
    case = _seed_case(fake, yq_item_code="C-10", issue_type_id=1, evidence_level=1)
    real = mi.near_duplicate

    def verified_meanwhile(*a, **k):                # after the capture read the case, before it links
        mq.update_case(case["id"], {"status": "verified", "verification_method": "sample_inspected",
                                    "verification_reason": "sample checked"}, "boss@example.com", "admin")
        return real(*a, **k)
    with _qenv(fake):
        with R._Patched((mi, "near_duplicate", verified_meanwhile)):
            _cap("rep1@example.com", [("p.jpg", _jpg())], kind="complaint", yq_item_code="C-10", issue_type_id="1")
        assert _case(fake, case["id"])["evidence_level"] == 3
        ev = [(e["detail"]["from"], e["detail"]["to"]) for e in fake.rows("market_case_events") if e["event"] == "evidence"]
        assert ev == [(1, 3)], ev
        # a reviewer read level 3 (the supplier asked: 4 is allowed); the database holds 5 by now
        stale = dict(_case(fake, case["id"]), status="action_requested")
        for c in fake.rows("market_cases"):
            if c["id"] == case["id"]:
                c.update(evidence_level=5, status="resolved")
        with R._Patched((mq, "_case", lambda cid: dict(stale) if int(cid) == case["id"] else None)):
            out = mq.update_case(case["id"], {"evidence_level": 4}, "clerk@example.com", "member")
        assert out["changed"] is False and _case(fake, case["id"])["evidence_level"] == 5
        assert len([e for e in fake.rows("market_case_events") if e["event"] == "evidence"]) == 1


@test("review 4: the alert claim is an INSERT the database allows once per case — a second claimant (sighting, reviewer, sweep) sends nothing; without the index the older claim still wins")
def _():
    from app import market_quality as mq
    fake = _qdb()
    case = _seed_case(fake, safety=True)

    class _Blind:
        """The second claimant at the moment of the race: its read of the timeline does not see the first
        claim yet (both read before either wrote) — only the database's index stands between them."""

        def table(self, name):
            q = fake.table(name)
            if name == "market_case_events":
                real = q.execute
                q.execute = lambda: SimpleNamespace(data=[], count=0) if q.op == "select" else real()
            return q

        def __getattr__(self, k):
            return getattr(fake, k)
    with _qenv(fake):
        assert mq.claim_alert(case["id"], "rep1@example.com", 1) is True
        with R._Patched((mq, "_c", lambda: _Blind())):
            assert mq.claim_alert(case["id"], "rep2@example.com", 2) is False, "the race: the index decides"
        assert mq.claim_alert(case["id"], "rep2@example.com", 2) is False
        assert mq.alert_claimed(case["id"])
        fake.unique = [u for u in fake.unique if u[-1] != "market_case_events_one_alert_claim_idx"]
        other = _seed_case(fake, safety=True)
        assert mq.claim_alert(other["id"], "a", 1) is True and mq.claim_alert(other["id"], "b", 2) is False
    src = (ROOT / "scripts" / "r8m_market_quality_migration.sql").read_text(encoding="utf-8")
    assert "market_case_events_one_alert_claim_idx" in src and "detail->>'email' = 'queued'" in src


@test("review 4: two captures of one product + issue that each opened a case at the same moment converge on the oldest — the newer closes as merged into it, its sighting moves, both recounted, one e-mail")
def _():
    from app import market_quality as mq
    fake, mail = _qdb(), _Mail()
    with _qenv(fake, mail):
        with R._Patched((mq, "find_open_case", lambda *a, **k: None)):       # both looked before either existed
            a = _cap("rep1@example.com", [], kind="complaint", yq_item_code="C-10", issue_type_id="1",
                     safety_flag="true", shop_name="Shop Alpha")
            b = _cap("rep2@example.com", [("p.jpg", _jpg())], kind="complaint", yq_item_code="C-10", issue_type_id="1",
                     safety_flag="true", shop_name="Shop Beta")
    keep = a["case"]["id"]
    dup = next(c for c in fake.rows("market_cases") if c["id"] != keep)
    assert b["case"]["id"] == keep and b["case"]["joined"] and b["result"] == "already_in_yq"
    assert (dup["status"], dup["merged_into"], dup["obs_count"]) == ("closed", keep, 0)
    k = _case(fake, keep)
    assert (k["obs_count"], k["shop_count"], k["evidence_level"], k["safety"]) == (2, 2, 2, True)
    assert all(o["case_id"] == keep for o in fake.rows("market_observations"))
    assert next(o for o in fake.rows("market_observations") if o["id"] == b["observation_id"])["result"] == "seen_before"
    assert [e[0] for e in _events(fake, dup["id"])] == ["created", "merge"] and len(mail.calls) == 1
    # another supplier's case is never folded into
    fake = _qdb()
    with _qenv(fake):
        with R._Patched((mq, "find_open_case", lambda *a, **k: None)):
            x = _cap("rep1@example.com", [], kind="complaint", yq_item_code="C-10", issue_type_id="1", supplier_id="2")
            y = _cap("rep2@example.com", [], kind="complaint", yq_item_code="C-10", issue_type_id="1", supplier_id="1")
    assert x["case"]["id"] != y["case"]["id"] and all(not c.get("merged_into") for c in fake.rows("market_cases"))


@test("review 5: a page asked for past the end (a stale offset, PostgREST's 416) answers empty with the true count — the review queue over HTTP too; the paged reads never ask past the end")
def _():
    from app import market_intel as mi
    fake = _qdb()
    with _qenv(fake):
        _cap("rep1@example.com", [("u.jpg", _jpg())])                  # a photo only: waits for the office
        try:
            fake.table("market_observations").select("*", count="exact").range(50, 59).execute()
            raise AssertionError("the stand-in answers a counted offset past the end like PostgREST")
        except RuntimeError as e:
            assert "PGRST103" in str(e) and mi.range_past_end(e)
        q = mi.review_queue(offset=120, limit=60)
        assert q["unassigned"] == [] and q["count"] == 1 and q["offset"] == 120
        r = R._client().get("/market-intel/review?offset=500", headers=R._h("clerk"))
        assert r.status_code == 200 and r.json()["count"] == 1 and r.json()["unassigned"] == []
        assert mi.board(office=True, offset=900)["items"] == []
        from app import market_quality as mq
        assert mq.cases(offset=500)["rows"] == []


@test("review 6: after a missed Sunday the catch-up files the missed week and the next Sunday its own — every search counted once, none lost")
def _():
    from app import shop_jobs
    utc = timezone.utc
    first = {"id": 1, "event": "search_zero", "ts": datetime(2026, 10, 12, 2, 0, tzinfo=utc).isoformat(),
             "session_id": "s1", "meta": {"q": "sandisk"}}
    later = [{"id": 10 + i, "event": "search_zero", "ts": datetime(2026, 10, day, 9, 0, tzinfo=utc).isoformat(),
              "session_id": f"later{i}", "meta": {"q": "sandisk"}} for i, day in enumerate((13, 14, 15, 16, 17))]
    fake = _qdb(shop_events=[first])
    with _qenv(fake):
        a = shop_jobs.market_demand_rollup(now=datetime(2026, 10, 12, 5, 0, tzinfo=utc))     # Monday 08:00 Bahrain
        fake.tables["shop_events"] = [first, *later]
        b = shop_jobs.market_demand_rollup(now=datetime(2026, 10, 18, 3, 5, tzinfo=utc))     # Sunday 06:05 Bahrain
    assert (a["week"], a["written"]["search_zero"]) == ("2026-10-11", 1)
    assert (b["week"], b["written"]["search_zero"]) == ("2026-10-18", 1)
    assert sorted((o.get("meta") or {}).get("count") for o in fake.rows("market_observations")) == [1, 5]


@test("review 7: a list merge whose target sits in a merge loop (or an endless chain) is refused with 409, never walked forever")
def _():
    from app import market_quality as mq
    loop = mq.Names([{"id": 1, "name": "S1", "merged_into": 2}, {"id": 2, "name": "S2", "merged_into": 1},
                     {"id": 3, "name": "S3", "merged_into": None}], [], [])
    R._raises(lambda: mq._merge_target("suppliers", loop, 3, 1), "merge loop", 409)
    chain = mq.Names([{"id": i, "name": f"S{i}", "merged_into": i + 1} for i in range(1, 80)]
                     + [{"id": 80, "name": "S80", "merged_into": None}, {"id": 99, "name": "S99", "merged_into": None}],
                     [], [])
    R._raises(lambda: mq._merge_target("suppliers", chain, 99, 1), "merge loop", 409)
    assert mq._merge_target("suppliers", chain, 99, 75) == 75


@test("review 8: only a missing table means 'not set up' — a blip or a column error is a 503, so a capture is retried, never saved without its v2 fields or in the legacy tables")
def _():
    from app import market_intel as mi
    fake, mail = _qdb(), _Mail()
    kw = dict(kind="complaint", yq_item_code="P-22", issue_type_id="1", safety_flag="true", note="smoke",
              client_uuid=str(uuid.uuid4()))
    with _qenv(fake, mail):
        fake.fail[("select", "market_cases")] = RuntimeError("timeout: the pooler is busy")
        R._raises(lambda: _cap("rep1@example.com", [], **kw), "could not reach", 503)
        assert fake.rows("market_observations") == [] and fake.rows("product_finds") == [] and mail.calls == []
        card = _cap("rep1@example.com", [], **kw)                      # the phone's retry
        assert card["case"] and card["safety_alerted"] and len(mail.calls) == 1
        mi.forget_probe()
        fake.fail[("select", "market_observations")] = R._pgrst("42703", "column market_observations.nope does not exist")
        R._raises(lambda: mi.available(), "could not reach", 503)
        mi.forget_probe()
        fake.fail[("select", "market_observations")] = RuntimeError("connection reset by peer")
        r = R._client().post("/market-intel/observations", headers=R._h("rep1"), data={"note": "x"})
        assert r.status_code == 503 and fake.rows("field_notes") == []
        mi.forget_probe()
        fake.fail[("select", "market_observations")] = RuntimeError("connection reset by peer")
        assert R._client().get("/market-intel/mine", headers=R._h("rep1")).status_code == 503
    assert mi.is_missing_table(R._pgrst("42P01", 'relation "public.x" does not exist'))
    assert mi.is_missing_table(R._pgrst("PGRST205", "Could not find the table 'public.x' in the schema cache"))
    assert not mi.is_missing_table(R._pgrst("42703", 'column "x" of relation "y" does not exist'))
    assert not mi.is_missing_table(R._pgrst("PGRST204", "Could not find the 'x' column of 'y' in the schema cache"))


@test("review 9: a brand moved to another supplier takes its OPEN cases along (an 'edit' by the system), closed ones keep theirs; a sighting never joins another supplier's open case")
def _():
    fake = _qdb()
    with _qenv(fake):
        a = _cap("rep1@example.com", [], kind="complaint", yq_item_code="C-10", issue_type_id="1")    # Brand X: Supplier X
        closed = _seed_case(fake, brand_id=1, supplier_id=1, status="closed")
        c = R._client()
        r = c.patch("/market-intel/lists/brands/1", headers=R._h("boss"), json={"supplier_id": 2})
        assert r.status_code == 200 and r.json()["cases_moved"] == [a["case"]["id"]], r.text[:300]
        assert _case(fake, a["case"]["id"])["supplier_id"] == 2 and _case(fake, closed["id"])["supplier_id"] == 1
        ev = [e for e in fake.rows("market_case_events") if e["case_id"] == a["case"]["id"] and e["event"] == "edit"][0]
        assert ev["actor"] == "system (brand re-linked)" and ev["detail"]["supplier_id"] == {"from": 1, "to": 2}
        b = _cap("rep2@example.com", [], kind="complaint", yq_item_code="C-10", issue_type_id="1")
        assert b["case"]["id"] == a["case"]["id"], "the brand's next sighting (Supplier Y now) joins the moved case"
        x = _cap("rep2@example.com", [], kind="complaint", yq_item_code="C-10", issue_type_id="1", supplier_id="1")
        assert x["case"]["id"] != a["case"]["id"], "Supplier X's sighting never joins Supplier Y's case"


@test("review 10: My reports name the issue each sighting was reported under (issue_type_id, issue_label, issue_label_ar)")
def _():
    fake = _qdb()
    with _qenv(fake):
        _cap("rep1@example.com", [], kind="complaint", yq_item_code="C-10", issue_type_id="1", note="dead")
        _cap("rep1@example.com", [], kind="other", note="plain words")
        rows = R._client().get("/market-intel/mine", headers=R._h("rep1")).json()["observations"]
    by = {r["note"]: r for r in rows}
    assert (by["dead"]["issue_type_id"], by["dead"]["issue_label"], by["dead"]["issue_label_ar"]) == (1, "Not charging", "لا يشحن")
    assert by["plain words"]["issue_type_id"] is None and by["plain words"]["issue_label"] is None


@test("minor: an answer or a supplier action that lost a race (409) leaves no photo row, no uploaded file, no event and no action behind")
def _():
    from app import market_quality as mq
    fake = _qdb()
    real = mq._cas

    def moved_meanwhile(to: str):
        def cas(case_id, status, upd):
            fake.tables["market_cases"] = [dict(c, status=to) if c["id"] == case_id else c for c in fake.rows("market_cases")]
            return real(case_id, status, upd)
        return cas
    with _qenv(fake):
        a = _cap("rep1@example.com", [], kind="complaint", yq_item_code="C-10", issue_type_id="1")
        mq.ask_question(a["case"]["id"], "Which batch?", "clerk@example.com", "member")
        files = set(fake.storage.objects)
        with R._Patched((mq, "_cas", moved_meanwhile("verified"))):
            R._raises(lambda: mq.answer(a["observation_id"], "Batch 2608", ("l.jpg", _jpg(1)), "rep1@example.com"),
                      "changed this case meanwhile", 409)
        assert fake.rows("market_photos") == [] and set(fake.storage.objects) == files
        assert not [e for e in fake.rows("market_case_events") if e["event"] == "answer"]
        v = _seed_case(fake, status="verified")
        with R._Patched((mq, "_cas", moved_meanwhile("closed"))):
            R._raises(lambda: mq.create_action(v["id"], {"request": "Replace the batch", "supplier_id": 1},
                                               "clerk@example.com", "member"), "changed this case meanwhile", 409)
        assert fake.rows("supplier_actions") == [] and fake.rows("supplier_action_cases") == []


@test("move: a safety sighting moved from an alerted case onto another takes it onto the Safety tab with the alert claim — no second e-mail, now or from the sweep")
def _():
    from app import market_quality as mq, shop_jobs
    fake, mail = _qdb(), _Mail()
    with _qenv(fake, mail):
        s = _cap("rep1@example.com", [], kind="complaint", yq_item_code="P-22", issue_type_id="2", note="hot")
        other = _seed_case(fake, yq_item_code="E-05", issue_type_id=1, safety=False,
                           updated_at=(NOW - timedelta(hours=1)).isoformat())
        out = mq.move_observation(s["observation_id"], other["id"], "clerk@example.com", "member")
        assert out["changed"] and _case(fake, other["id"])["safety"] is True and mq.alert_claimed(other["id"])
        assert shop_jobs.market_safety_sweep(NOW + timedelta(hours=2))["alerted"] == []
    assert len(mail.calls) == 1, "the problem was alerted once, under its first case"
    claim = [e for e in fake.rows("market_case_events") if e["case_id"] == other["id"] and e["event"] == "safety_alert"]
    assert claim[0]["detail"]["carried_from"] == s["case"]["id"]


@test("minor: the thumbnail backfill passes over a photo it cannot make and goes on to the next ones")
def _():
    from app import market_intel as mi
    fake = _qdb()
    for name in ("finds/ok1.jpg", "finds/ok2.jpg"):
        fake.storage.objects[("finds", name)] = (_jpg(), {})
    fake.tables["market_photos"] = [
        {"id": 1, "observation_id": 1, "bucket": "finds", "path": "finds/gone.jpg", "thumb_path": None, "has_people_flag": False},
        {"id": 2, "observation_id": 1, "bucket": "finds", "path": "finds/ok1.jpg", "thumb_path": None, "has_people_flag": False},
        {"id": 3, "observation_id": 1, "bucket": "finds", "path": "finds/ok2.jpg", "thumb_path": None, "has_people_flag": False}]
    with _qenv(fake):
        out = mi.backfill_thumbnails(limit=2, dry_run=False)
    assert out["thumbs"] == 2 and [f["photo_id"] for f in out["failed"]] == [1], out
    assert [bool(p.get("thumb_path")) for p in fake.rows("market_photos")] == [False, True, True]


@test("minor: the v1 board, item and review queue mask phones and e-mails for a storekeeper holding the page, as for management")
def _():
    fake = _qdb()
    with _qenv(fake):
        a = _cap("rep1@example.com", [], kind="competitor_price", title="Stock via 33001122", note="call 39001234",
                 shop_name="Shop 38001234")
        c = R._client()
        for url in ("/market-intel/items", f"/market-intel/items/{a['item']['id']}", "/market-intel/review"):
            r = c.get(url, headers=R._h("store"))
            assert r.status_code == 200, (url, r.text[:200])
            for raw in ("33001122", "39001234", "38001234"):
                assert raw not in r.text, (url, raw)
        assert "39001234" in c.get(f"/market-intel/items/{a['item']['id']}", headers=R._h("clerk")).text


# ═══════════════════════════════════════════════════════════════════════════════
# 13. the code itself
# ═══════════════════════════════════════════════════════════════════════════════

FILES = ("app/market_intel.py", "app/market_intel_api.py", "app/market_quality.py", "app/market_quality_api.py",
         "app/shop_jobs.py", "scripts/market_intel_thumbs.py", "tests/test_r7b_market_intel.py",
         "tests/test_r8m_market_quality.py")


@test("code: Python 3.11 grammar, no nested f-string, no 3-decimal amount of 1,000 or more; the CI runs this suite")
def _():
    from scripts.ops import selftest
    for rel in FILES:
        src = (ROOT / rel).read_text(encoding="utf-8")
        tree = ast.parse(src, feature_version=(3, 11))
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                assert not any(isinstance(v, ast.FormattedValue) and any(isinstance(x, ast.JoinedStr) for x in ast.walk(v.value))
                               for v in node.values), f"{rel}: a nested f-string, line {node.lineno}"
        assert not selftest.scan_text(src, []), (rel, selftest.scan_text(src, []))
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    step = ci[ci.index("python -m tests.test_r7b_market_intel"):]
    assert "python -m tests.test_r8m_market_quality" in step.split("\n      - name:", 1)[0]


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
