"""R7b — Market Intelligence v1 (plan §18: Product Finds + Field Notes merged into one capture).

    python -m tests.test_r7b_market_intel

Same lightweight runner as tests/test_r7a_focus.py: every test runs against an in-memory stand-in for
the PostgREST client and Supabase Storage (eq / is / in / gte filters, real ordering, identity ids,
the client_uuid unique key, missing tables that raise like PostgREST). Nothing here reaches a database,
a storage bucket or the network, and the pure tests pass with no .env. Every name, phone, price and
barcode is synthetic.

Covered:
  * features — 'Market Intel' is a page of its own, in the salesman defaults, grantable to management,
    decoupled from 'AI Assistant' (a rep with it still gets 403 on /ask and /field-notes);
  * the central gate — management's only write is the approve route (a template in the allowlist);
  * cleaning — kind / demand / price (exact Decimal, 3 dp) / barcode / the normalised "seen before" key;
  * photos — magic bytes, a renamed file, a bad WEBP, a corrupt JPEG are refused; EXIF (GPS, orientation)
    is gone after processing and the photo is upright and <= 1600 px; the dHash survives a re-encode
    (Hamming <= 8) and tells two different photos apart;
  * capture — new find, retry (same client_uuid: the stored card, nothing written), seen before (other
    word order, another rep: distinct shops and reps), older than 30 days = a new item, exact barcode,
    already in YQ (and a "Joyroom T10" that is not YQ's T10), photo only, near-duplicate photo, a lost
    insert race, validation; the shop pill's phone resolves the shop and is never stored;
  * before the migration — the capture lands in the legacy Product Finds / Field Notes tables (and a
    retry writes nothing), the board says "not set up yet", a decision answers 409;
  * the board — distinct counts, filters, merged hidden, a rep sees no names and cannot filter by rep;
    the item detail (a rep: only his own sightings; people-flagged photos hidden from him); the review
    queue and the imported library; My signals;
  * decisions — the transition map, Approved needs an action, merge moves the sightings (recorded),
    management approves only an Opportunity and cannot edit, a rep cannot decide; identify; photo flag;
  * system demand signals — zero-result searches folded ("san" -> "sandisk"), restock asks per SKU,
    once per week (a re-run writes nothing), dry run, not scheduled yet;
  * the routes — every /market-intel route behind 'Market Intel', the multipart capture end to end;
  * the migration, its reverse and the grants script read as text; the import script's plan (copy, not
    move; grouped fallback rows; stable ids; dry run by default);
  * a local Postgres replay (SKIPs without a cluster): the migration applied twice, its self-check, the
    unique key, the append-only history, the views' distinct counts, the agent view's grants, the reverse.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

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


MIGRATION = ROOT / "scripts" / "r7b_market_intel_migration.sql"
REVERSE = ROOT / "scripts" / "r7b_market_intel_reverse.sql"
GRANTS = ROOT / "scripts" / "r7b_market_intel_grants.sql"
GRANTS_REVERSE = ROOT / "scripts" / "r7b_market_intel_grants_reverse.sql"


# ── an in-memory PostgREST + Storage stand-in ──────────────────────────────────

def _same(a, b) -> bool:
    return a == b or str(a) == str(b)


def _key(v):
    return (v is None, str(v) if not isinstance(v, (int, float)) else f"{v:020.6f}")


class _Query:
    def __init__(self, db: "_FakeDB", table: str):
        self.db, self.table = db, table
        self.op, self.payload, self.filters = "select", None, []
        self.want_count, self.lim, self.orders = None, None, []

    def select(self, cols="*", count=None, head=False):
        self.op, self.want_count = "select", count
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def delete(self):
        self.op = "delete"
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def is_(self, col, val):
        self.filters.append(("is", col, val))
        return self

    def in_(self, col, vals):
        self.filters.append(("in", col, list(vals)))
        return self

    def gte(self, col, val):
        self.filters.append(("gte", col, val))
        return self

    def ilike(self, col, val):
        self.filters.append(("ilike", col, val))
        return self

    def order(self, col, desc=False, **_kw):
        self.orders.append((col, bool(desc)))
        return self

    def limit(self, n):
        self.lim = n
        return self

    def range(self, a, b):
        self.lim = b - a + 1
        return self

    def _match(self, row: dict) -> bool:
        for kind, col, val in self.filters:
            got = row.get(col)
            if kind == "eq":
                ok = _same(got, val)
            elif kind == "is":
                ok = (got is None) if str(val).lower() == "null" else (bool(got) == (str(val).lower() == "true"))
            elif kind == "in":
                ok = any(_same(got, v) for v in val)
            elif kind == "gte":
                ok = got is not None and str(got) >= str(val)
            elif kind == "ilike":
                rx = "".join(".*" if ch == "%" else "." if ch == "_" else re.escape(ch) for ch in str(val))
                ok = re.fullmatch(rx, str(got or ""), re.I | re.S) is not None
            else:
                ok = True
            if not ok:
                return False
        return True

    def execute(self):
        db = self.db
        db.calls.append((self.op, self.table, self.payload, list(self.filters)))
        fail = db.fail.pop((self.op, self.table), None)
        if fail is not None:
            raise fail
        if self.table in db.missing:
            raise RuntimeError(f'{{"code": "42P01", "message": "relation \\"public.{self.table}\\" does not exist"}}')
        if self.table in db.views:
            rows = db.views[self.table](db)
        else:
            rows = db.tables.setdefault(self.table, [])
        if self.op == "select":
            out = [dict(r) for r in rows if self._match(r)]
            for col, desc in reversed(self.orders):
                out.sort(key=lambda r, c=col: _key(r.get(c)), reverse=desc)
            data = out[: self.lim] if self.lim else out
            return SimpleNamespace(data=data, count=(len(out) if self.want_count else None))
        if self.op == "insert":
            items = self.payload if isinstance(self.payload, list) else [self.payload]
            for p in items:
                for tbl, col in db.unique:
                    if tbl == self.table and p.get(col) is not None and any(_same(r.get(col), p.get(col)) for r in rows):
                        raise RuntimeError('{"code": "23505", "message": "duplicate key value violates unique constraint"}')
            out = []
            for p in items:
                r = dict(p)
                r.setdefault("id", db.next_id(self.table))
                r.setdefault("created_at", db.now_iso())
                rows.append(r)
                out.append(dict(r))
            return SimpleNamespace(data=out, count=None)
        if self.op == "update":
            out = []
            for r in rows:
                if self._match(r):
                    r.update(self.payload)
                    out.append(dict(r))
            return SimpleNamespace(data=out, count=None)
        if self.op == "delete":
            gone = [r for r in rows if self._match(r)]
            rows[:] = [r for r in rows if not self._match(r)]
            return SimpleNamespace(data=gone, count=None)
        return SimpleNamespace(data=[], count=None)


class _Bucket:
    def __init__(self, st: "_Storage", name: str):
        self.st, self.name = st, name

    def upload(self, path, data, opts=None):
        if self.st.fail_upload:
            raise RuntimeError("storage down")
        self.st.objects[(self.name, path)] = (bytes(data), dict(opts or {}))
        self.st.calls.append(("upload", self.name, path))
        return {"Key": path}

    def create_signed_url(self, path, ttl):
        return {"signedURL": f"https://signed.example/{self.name}/{path}?ttl={ttl}"}

    def create_signed_urls(self, paths, ttl):
        self.st.calls.append(("sign", self.name, len(paths)))
        return [{"path": p, "signedURL": f"https://signed.example/{self.name}/{p}?ttl={ttl}"} for p in paths]

    def download(self, path):
        return self.st.objects[(self.name, path)][0]

    def remove(self, paths):
        self.st.calls.append(("remove", self.name, list(paths)))


class _Storage:
    def __init__(self):
        self.objects: dict[tuple[str, str], tuple[bytes, dict]] = {}
        self.calls: list[tuple] = []
        self.fail_upload = False

    def from_(self, name):
        return _Bucket(self, name)

    def create_bucket(self, name, options=None):
        raise RuntimeError("already exists")


MI_TABLES = ("market_items", "market_observations", "market_photos", "market_item_decisions", "v_market_clusters")


class _FakeDB:
    def __init__(self, tables: dict | None = None, missing: set | None = None):
        self.tables: dict[str, list[dict]] = {k: [dict(r) for r in v] for k, v in (tables or {}).items()}
        self.missing: set[str] = set(missing or ())
        self.calls: list[tuple] = []
        self.fail: dict[tuple, Exception] = {}
        self.unique = [("market_observations", "client_uuid")]
        self.views = {"v_market_clusters": _clusters_view}
        self.storage = _Storage()
        self._ids: dict[str, int] = {}
        self.clock = datetime(2026, 9, 27, 9, 0, tzinfo=timezone.utc)

    def now_iso(self) -> str:
        return self.clock.isoformat()

    def table(self, name):
        return _Query(self, name)

    def rpc(self, name, params=None):
        self.calls.append(("rpc", name, params, []))
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=[], count=None))

    def next_id(self, table) -> int:
        cur = max([int(r.get("id") or 0) for r in self.tables.get(table, [])] + [self._ids.get(table, 0)])
        self._ids[table] = cur + 1
        return cur + 1

    def writes(self, table: str | None = None) -> list[tuple]:
        return [c for c in self.calls if c[0] in ("insert", "update", "delete") and (table is None or c[1] == table)]

    def rows(self, table: str) -> list[dict]:
        return self.tables.get(table, [])


def _clusters_view(db: _FakeDB) -> list[dict]:
    """v_market_clusters computed from the fake tables exactly as the SQL does (distinct shops / reps)."""
    obs = db.tables.get("market_observations", [])
    photos = db.tables.get("market_photos", [])
    out = []
    for i in db.tables.get("market_items", []):
        mine = [o for o in obs if o.get("item_id") == i["id"]]
        shops = {("c", o["shop_customer_id"]) if o.get("shop_customer_id") else ("n", str(o.get("shop_name") or "").strip().lower())
                 for o in mine if o.get("shop_customer_id") or str(o.get("shop_name") or "").strip()}
        reps = {("s", o["salesman_id"]) if o.get("salesman_id") else ("e", str(o.get("created_by") or "").lower())
                for o in mine if o.get("source") == "rep" and (o.get("salesman_id") or o.get("created_by"))}
        prices = [Decimal(str(o["price_bhd"])) for o in mine if o.get("price_bhd") is not None]
        seen = sorted(str(o["observed_at"]) for o in mine if o.get("observed_at"))
        ph = [p for p in photos if any(p["observation_id"] == o["id"] for o in mine)]
        cover = sorted([p for p in ph if not p.get("has_people_flag")], key=lambda p: -int(p["id"]))
        out.append({"item_id": i["id"], "kind": i.get("kind"), "title": i.get("title"), "brand": i.get("brand"),
                    "competitor": i.get("competitor"), "category": i.get("category"), "barcode": i.get("barcode"),
                    "yq_item_code": i.get("yq_item_code"), "status": i.get("status"), "action": i.get("action"),
                    "merged_into": i.get("merged_into"), "observations": len(mine), "shops": len(shops),
                    "reps": len(reps), "price_min": str(min(prices)) if prices else None,
                    "price_max": str(max(prices)) if prices else None,
                    "first_seen": seen[0] if seen else i.get("created_at"), "last_seen": seen[-1] if seen else i.get("created_at"),
                    "system_signals": sum(1 for o in mine if o.get("source") == "system"),
                    "near_duplicates": sum(1 for o in mine if o.get("near_duplicate_of")),
                    "demand_qty": sum(int(o["demand_qty"]) for o in mine if o.get("demand_qty")) or None,
                    "photos": len(ph), "photos_people": sum(1 for p in ph if p.get("has_people_flag")),
                    "cover_bucket": cover[0]["bucket"] if cover else None, "cover_path": cover[0]["path"] if cover else None,
                    "has_ai_suggestion": i.get("ai_suggestion") is not None, "ai_confidence": i.get("ai_confidence"),
                    "verified": bool(i.get("verified")), "status_changed_at": i.get("status_changed_at"),
                    "created_at": i.get("created_at"), "updated_at": i.get("updated_at"),
                    "first_note": next((o["note"] for o in sorted(mine, key=lambda o: (str(o.get("observed_at")), o["id"]))
                                        if str(o.get("note") or "").strip()), None)})
    return out


class _Patched:
    def __init__(self, *triples):
        self.triples, self.saved = triples, []

    def __enter__(self):
        for mod, name, value in self.triples:
            self.saved.append((mod, name, getattr(mod, name)))
            setattr(mod, name, value)
        return self

    def __exit__(self, *exc):
        for mod, name, old in reversed(self.saved):
            setattr(mod, name, old)
        return False


# ── synthetic fixtures (no real rep, shop, phone, price or barcode) ─────────────

def _catalog() -> dict:
    return {"items": {
        "X05 UC-1Mtr": {"item_code": "X05 UC-1Mtr", "display_name": "X05 UC 1Mtr Type-C Cable", "category": "CABLE",
                        "brand": "VFAN", "product_image_url": None},
        "T10": {"item_code": "T10", "display_name": "T10 Bluetooth Earbuds", "category": "BLUETOOTH HEADSET",
                "brand": "VFAN", "product_image_url": None},
        "UK12": {"item_code": "UK12", "display_name": "UK12 45W Charger", "category": "CHARGER", "brand": "VFAN",
                 "product_image_url": None},
    }}


USERS = [
    {"email": "boss@example.com", "role": "admin", "features": [], "status": "active"},
    {"email": "rep1@example.com", "role": "salesman", "features": ["Catalog", "Shop Orders", "Market Intel"], "status": "active"},
    {"email": "rep2@example.com", "role": "salesman", "features": ["Catalog", "Shop Orders", "Market Intel"], "status": "active"},
    {"email": "rep3@example.com", "role": "salesman", "features": ["Catalog", "Shop Orders"], "status": "active"},
    {"email": "clerk@example.com", "role": "member", "features": ["Dashboard", "Market Intel"], "status": "active"},
    {"email": "mgmt@example.com", "role": "management",
     "features": ["Dashboard", "Sales", "Margins", "Receivables", "Inventory", "Shop Orders", "Market Intel"], "status": "active"},
]
SALESMEN = [{"id": 1, "name": "Rep One", "referral_code": "rep-one", "is_active": True, "user_email": "rep1@example.com"},
            {"id": 2, "name": "Rep Two", "referral_code": "rep-two", "is_active": True, "user_email": "rep2@example.com"}]
SHOP_CUSTOMERS = [{"id": 7, "phone": "97330000007", "name": "Test Shop Seven", "shop": "Test Shop Seven", "area": "Manama"}]


def _db(missing: set | None = None, **tables) -> _FakeDB:
    base = {"user_roles": USERS, "salesmen": SALESMEN, "shop_customers": SHOP_CUSTOMERS, "audit_log": [],
            "market_items": [], "market_observations": [], "market_photos": [], "market_item_decisions": [],
            "product_finds": [], "field_notes": [], "shop_events": [], "shop_restock_requests": [], "app_settings": []}
    base.update(tables)
    return _FakeDB(base, missing=missing)


class _env:
    """Point every client lookup at the fake, stub the token decoder (token = the email's local part),
    switch the rate limiter off and hand market_intel a synthetic catalog."""

    def __init__(self, fake: _FakeDB, catalog: dict | None = None):
        self.fake = fake
        self.catalog = catalog if catalog is not None else _catalog()

    def __enter__(self):
        from app import auth, database, db_read, market_intel as mi, product_finds, shop, user_auth
        import app.main as m
        self.mi, self.shop, self.database = mi, shop, database
        fake = self.fake
        self.p = _Patched(
            (database, "get_client", lambda: fake), (mi, "get_client", lambda: fake),
            (product_finds, "get_client", lambda: fake), (shop, "get_client", lambda: fake),
            (user_auth, "get_client", lambda: fake), (db_read, "get_client", lambda: fake),
            (auth, "_decode_token", lambda tok: {"sub": "uid-" + tok, "email": tok + "@example.com"}),
            (mi, "_catalog_ctx", lambda: self.catalog),
            (m.limiter, "enabled", False),
        )
        self.p.__enter__()
        mi.forget_probe()
        shop._salesman_cache.clear()
        database.invalidate_user_cache()
        return fake

    def __exit__(self, *exc):
        self.p.__exit__(*exc)
        self.mi.forget_probe()
        self.shop._salesman_cache.clear()
        self.database.invalidate_user_cache()
        return False


def _client():
    from fastapi.testclient import TestClient
    import app.main as m
    return TestClient(m.app)


def _h(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


# ── synthetic photos ────────────────────────────────────────────────────────────

def _scene(w=2400, h=1800, variant=0):
    """A smooth synthetic 'shelf': gradients and blocks (dHash needs structure, not noise)."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(0, h, 4):
        for x in range(0, w, 4):
            v = (x * 255 // w, y * 255 // h, ((x + y) * 255 // (w + h)))
            if variant:
                v = (255 - v[0], v[2], v[1])
            for dy in range(4):
                for dx in range(4):
                    if x + dx < w and y + dy < h:
                        px[x + dx, y + dy] = v
    d = ImageDraw.Draw(img)
    if variant == 0:
        d.rectangle([w * 0.1, h * 0.2, w * 0.45, h * 0.8], fill=(250, 250, 250))
        d.ellipse([w * 0.55, h * 0.15, w * 0.9, h * 0.6], fill=(20, 20, 60))
    else:
        d.rectangle([w * 0.5, h * 0.05, w * 0.95, h * 0.4], fill=(10, 10, 10))
        d.ellipse([w * 0.05, h * 0.5, w * 0.4, h * 0.95], fill=(240, 240, 200))
    return img


_SCENES: dict = {}


def _jpeg(variant=0, gps=True, orientation=6, quality=90, size=(2400, 1800)) -> bytes:
    from PIL import Image
    key = (variant, size)
    if key not in _SCENES:
        _SCENES[key] = _scene(*size, variant=variant)
    img = _SCENES[key]
    exif = Image.Exif()
    exif[0x0112] = orientation              # Orientation
    exif[0x010F] = "SyntheticCam"           # Make
    if gps:
        exif[0x8825] = {1: "N", 2: (26.0, 13.0, 0.0), 3: "E", 4: (50.0, 35.0, 0.0)}
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, exif=exif)
    return buf.getvalue()


def _webp(variant=0) -> bytes:
    from PIL import Image  # noqa: F401
    key = (variant, (800, 600))
    if key not in _SCENES:
        _SCENES[key] = _scene(800, 600, variant=variant)
    buf = io.BytesIO()
    _SCENES[key].save(buf, format="WEBP", quality=75)
    return buf.getvalue()


def _u() -> str:
    return str(uuid.uuid4())


def _cap(actor, photos=(), **fields):
    from app import market_intel as mi
    fields.setdefault("client_uuid", _u())
    return mi.capture(actor, fields, list(photos))


def _raises(fn, want: str, status: int | None = None):
    from app.market_intel import MarketIntelError
    try:
        fn()
    except MarketIntelError as e:
        assert want.lower() in str(e).lower(), (want, str(e))
        if status is not None:
            assert e.status == status, (status, e.status)
        return e
    raise AssertionError(f"expected MarketIntelError containing {want!r}")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. features and the central gate
# ═══════════════════════════════════════════════════════════════════════════════

@test("features: 'Market Intel' is its own page, a salesman default, grantable to management, never 'AI Assistant'")
def _():
    from app import features as f
    assert "Market Intel" in f.FEATURES and "Product Finds" in f.FEATURES
    assert "Market Intel" in f.ROLE_DEFAULT_FEATURES["salesman"]
    assert "AI Assistant" not in f.ROLE_DEFAULT_FEATURES["salesman"]
    assert f.may_hold("management", "Market Intel") and "Market Intel" not in f.ROLE_DEFAULT_FEATURES["management"]
    for page in ("AI Assistant", "AI Agents", "Shop Admin"):
        assert not f.may_hold("management", page)


@test("gate: management's only write is POST /market-intel/items/{id}/approve; every other Market Intel write is refused")
def _():
    from app.auth import read_only_refuses
    assert not read_only_refuses("POST", "/market-intel/items/12/approve")
    assert read_only_refuses("POST", "/market-intel/items/12/approve/x")
    assert read_only_refuses("POST", "/market-intel/items//approve"), "a template segment is never empty"
    for meth, path in (("PATCH", "/market-intel/items/12"), ("POST", "/market-intel/observations"),
                       ("POST", "/market-intel/observations/3/identify"), ("POST", "/market-intel/photos/4/people")):
        assert read_only_refuses(meth, path), (meth, path)
    assert not read_only_refuses("GET", "/market-intel/items") and not read_only_refuses("GET", "/market-intel/items/5")


@test("gate: a rep holding 'Market Intel' still gets 403 on /ask and /field-notes; without the page, 403 on /market-intel")
def _():
    with _env(_db()):
        c = _client()
        for path in ("/field-notes",):
            assert c.get(path, headers=_h("rep1")).status_code == 403, path
        assert c.post("/ask", headers=_h("rep1"), json={"question": "x"}).status_code == 403
        assert c.get("/market-intel/mine", headers=_h("rep1")).status_code == 200
        r = c.get("/market-intel/items", headers=_h("rep3"))
        assert r.status_code == 403 and "Market Intel" in r.text


@test("routes: every /market-intel route is behind feature:Market Intel and nothing else")
def _():
    from fastapi.routing import APIRoute
    import app.main as m
    from app import auth

    def _gate_names(route) -> list[str]:        # tests/test_r1_security.py's reading of a route's gates
        out = []
        for dep in route.dependant.dependencies:
            c = dep.call
            if c is auth.require_admin:
                out.append("admin")
            elif c is auth.get_current_user:
                out.append("user")
            elif getattr(c, "feature", None):
                out.append(f"feature:{c.feature}")
            else:
                out.append(f"?{getattr(c, '__qualname__', c)}")
        return out
    found = {}
    for r in m.app.routes:
        if isinstance(r, APIRoute) and r.path.startswith("/market-intel"):
            for meth in r.methods:
                found[(meth, r.path)] = _gate_names(r)
    want = {("POST", "/market-intel/observations"), ("GET", "/market-intel/mine"), ("GET", "/market-intel/items"),
            ("GET", "/market-intel/items/{item_id}"), ("GET", "/market-intel/review"),
            ("PATCH", "/market-intel/items/{item_id}"), ("POST", "/market-intel/items/{item_id}/approve"),
            ("POST", "/market-intel/observations/{obs_id}/identify"), ("POST", "/market-intel/photos/{photo_id}/people")}
    assert set(found) == want, set(found) ^ want
    assert all(g == ["feature:Market Intel"] for g in found.values()), found


# ═══════════════════════════════════════════════════════════════════════════════
# 2. cleaning
# ═══════════════════════════════════════════════════════════════════════════════

@test("clean: price is an exact 3-dp Decimal; kind, demand and quantity are checked; barcode is digits only")
def _():
    from app import market_intel as mi
    assert mi.money3("2.5") == Decimal("2.500") and mi.money3(" 1,250.1234 ") == Decimal("1250.123")
    assert mi.money3(None) is None and mi.money3("") is None and mi.money3("0") == Decimal("0.000")
    assert isinstance(mi.money3("0.1"), Decimal) and str(mi.money3("0.1") + mi.money3("0.2")) == "0.300"
    for bad in ("abc", "-1", "100000", "NaN", "Infinity"):
        _raises(lambda b=bad: mi.money3(b), "price")
    assert mi.money_out("2.5") == "2.500" and mi.money_out(None) is None
    f = mi.clean_fields({"kind": "COMPETITOR_PRICE", "price_bhd": "3.25", "demand_qty": "12", "category": "cable",
                         "barcode": " 6 901234 567892 ", "note": "  seen\x07 at   the counter  "})
    assert f["kind"] == "competitor_price" and f["price"] == Decimal("3.250") and f["demand_qty"] == 12
    assert f["category"] == "CABLE" and f["barcode"] == "6901234567892" and f["note"] == "seen at the counter"
    assert mi.clean_fields({})["kind"] == "other"
    _raises(lambda: mi.clean_fields({"kind": "rumour"}), "kind")
    _raises(lambda: mi.clean_fields({"demand_level": "huge"}), "demand")
    _raises(lambda: mi.clean_fields({"demand_qty": "2.5"}), "whole number")
    _raises(lambda: mi.clean_fields({"demand_qty": "0"}), "between")
    assert mi.norm_barcode("12345") is None and mi.norm_barcode("abc") is None


@test("clean: the 'seen before' key ignores word order, case, punctuation, prices and stop words; Arabic survives")
def _():
    from app.market_intel import norm_name
    assert norm_name("Anker 20W charger") == norm_name("charger, ANKER 20w") == "20w anker charger"
    assert norm_name("Anker 20W charger 3.500 BD") == norm_name("the anker charger 20W")
    assert norm_name("شاحن سريع") == "سريع شاحن"
    assert norm_name("") is None and norm_name("the a") is None


# ═══════════════════════════════════════════════════════════════════════════════
# 3. photos
# ═══════════════════════════════════════════════════════════════════════════════

@test("photos: EXIF (GPS, device, orientation) is gone, the photo is upright and at most 1600 px")
def _():
    from PIL import Image
    from app.market_intel import MAX_EDGE, process_photo
    raw = _jpeg(gps=True, orientation=6)
    src = Image.open(io.BytesIO(raw))
    assert src.getexif().get_ifd(0x8825), "the fixture carries GPS"
    p = process_photo(raw, "IMG_0001.JPG")
    out = Image.open(io.BytesIO(p["data"]))
    assert len(out.getexif()) == 0, dict(out.getexif())
    assert b"Exif" not in p["data"][:4096] and b"SyntheticCam" not in p["data"]
    assert max(out.size) <= MAX_EDGE and out.size == (p["width"], p["height"])
    assert out.size[1] > out.size[0], "orientation 6 = rotated upright (portrait)"
    assert p["bytes"] == len(p["data"]) and p["content_type"] in ("image/webp", "image/jpeg")
    assert -(1 << 63) <= p["phash"] < (1 << 63)


@test("photos: magic bytes, a renamed file, a fake WEBP, a corrupt JPEG and HEIC are refused")
def _():
    from app.market_intel import process_photo
    _raises(lambda: process_photo(b"hello world, not a photo", "x.jpg"), "valid photo")
    _raises(lambda: process_photo(_jpeg(), "x.png"), "valid photo")
    _raises(lambda: process_photo(b"RIFF\x00\x00\x00\x00WAVEfmt ", "x.webp"), "valid photo")
    _raises(lambda: process_photo(b"\xff\xd8\xff\xe0" + b"\x00" * 64, "x.jpg"), "could not be read")
    _raises(lambda: process_photo(b"\x00\x00\x00\x18ftypheic", "x.heic"), "JPG, PNG or WEBP")
    _raises(lambda: process_photo(_jpeg(), "x.gif"), "JPG, PNG or WEBP")
    assert process_photo(_webp(), "x.webp")["width"] == 800


@test("photos: the dHash survives a re-encode and a resize (Hamming <= 8) and tells two photos apart")
def _():
    from app.market_intel import PHASH_NEAR, hamming, process_photo
    a = process_photo(_jpeg(variant=0, quality=92), "a.jpg")["phash"]
    b = process_photo(_jpeg(variant=0, quality=55, size=(1200, 900), gps=False, orientation=1), "b.jpg")["phash"]
    c = process_photo(_jpeg(variant=1), "c.jpg")["phash"]
    # the first is rotated by its EXIF, the second is not: compare like with like
    a2 = process_photo(_jpeg(variant=0, quality=60, orientation=1), "a2.jpg")["phash"]
    assert hamming(a2, b) <= PHASH_NEAR, hamming(a2, b)
    assert hamming(a2, c) > PHASH_NEAR, hamming(a2, c)
    assert hamming(a, a) == 0 and hamming(None, a) == 64


# ═══════════════════════════════════════════════════════════════════════════════
# 4. capture
# ═══════════════════════════════════════════════════════════════════════════════

@test("capture: a new find — item New, photos in the private bucket, rep + shop attached, the phone never stored")
def _():
    fake = _db()
    with _env(fake):
        card = _cap("rep1@example.com", [("p1.jpg", _jpeg())], kind="new_product", title="Anker 20W charger",
                    brand="Anker", category="charger", price_bhd="3.5", note="On the counter",
                    shop_name="Test Shop Seven", shop_phone="3000 0007", area="Manama")
    assert card["ok"] and card["result"] == "new_find" and card["photos"] == 1, card
    assert "New find saved" in card["message"] and card["item"]["status"] == "new"
    obs = fake.rows("market_observations")[0]
    assert obs["salesman_id"] == 1 and obs["created_by"] == "rep1@example.com" and obs["source"] == "rep"
    assert obs["shop_customer_id"] == 7 and obs["shop_name"] == "Test Shop Seven" and obs["area"] == "Manama"
    assert obs["price_bhd"] == "3.500" and obs["category"] == "CHARGER"
    assert "shop_phone" not in obs and "3000" not in json.dumps(obs) and "97330000007" not in json.dumps(obs)
    item = fake.rows("market_items")[0]
    assert item["status"] == "new" and item["title"] == "Anker 20W charger" and item["norm_key"] == "20w anker charger"
    assert item["obs_count"] == 1 and item["shop_count"] == 1 and item["rep_count"] == 1 and item["price_min"] == "3.500"
    ph = fake.rows("market_photos")[0]
    assert ph["bucket"] == "finds" and ph["path"].startswith("market/2") and ph["phash"] is not None
    assert (ph["bucket"], ph["path"]) in fake.storage.objects
    assert fake.storage.objects[(ph["bucket"], ph["path"])][1]["upsert"] == "false"


@test("capture: a retry with the same client_uuid returns the stored card and writes nothing")
def _():
    fake = _db()
    cu = _u()
    with _env(fake):
        first = _cap("rep1@example.com", [("p.jpg", _jpeg())], client_uuid=cu, title="Brand Q cable", kind="new_product")
        n_writes, n_up = len(fake.writes()), len(fake.storage.objects)
        again = _cap("rep1@example.com", [("p.jpg", _jpeg())], client_uuid=cu, title="Brand Q cable", kind="new_product")
    assert again["replayed"] is True and again["observation_id"] == first["observation_id"]
    assert again["result"] == first["result"] == "new_find" and again["item"]["id"] == first["item"]["id"]
    assert len(fake.writes()) == n_writes and len(fake.storage.objects) == n_up, "a retry writes nothing"


@test("capture: seen before — another rep, other word order, another shop within 30 days: one item, distinct counts")
def _():
    fake = _db()
    with _env(fake):
        a = _cap("rep1@example.com", [("p.jpg", _jpeg())], kind="competitor_price", title="Anker 20W charger",
                 brand="Anker", category="CHARGER", price_bhd="3.000", shop_name="Shop A")
        fake.clock += timedelta(days=3)
        b = _cap("rep2@example.com", [], kind="competitor_price", title="charger anker 20w", brand="anker",
                 category="charger", price_bhd="2.500", shop_name="Shop B")
        # the same rep and shop again: evidence does not inflate
        c = _cap("rep2@example.com", [], kind="competitor_price", title="ANKER charger 20W", brand="Anker",
                 category="CHARGER", price_bhd="2.750", shop_name="shop b ")
    assert a["result"] == "new_find" and b["result"] == c["result"] == "seen_before"
    assert a["item"]["id"] == b["item"]["id"] == c["item"]["id"]
    assert "now 2 shops" in c["message"], c["message"]
    item = fake.rows("market_items")[0]
    assert len(fake.rows("market_items")) == 1
    assert (item["obs_count"], item["shop_count"], item["rep_count"]) == (3, 2, 2)
    assert (item["price_min"], item["price_max"]) == ("2.500", "3.000")


@test("capture: the same words 31 days later, another kind or another brand make a NEW item")
def _():
    from app import market_intel as mi
    fake = _db()
    with _env(fake):
        a = _cap("rep1@example.com", [], kind="new_product", title="Brand Z power bank 10000", brand="Brand Z")
        other_kind = _cap("rep1@example.com", [], kind="promotion", title="Brand Z power bank 10000", brand="Brand Z")
        other_brand = _cap("rep1@example.com", [], kind="new_product", title="Brand Z power bank 10000", brand="Brand Y")
        # age the first item past the window
        for it in fake.rows("market_items"):
            if it["id"] == a["item"]["id"]:
                it["last_seen"] = (datetime.now(timezone.utc) - timedelta(days=mi.SEEN_WINDOW_DAYS + 1)).isoformat()
        late = _cap("rep2@example.com", [], kind="new_product", title="power bank brand z 10000", brand="brand z")
    ids = {a["item"]["id"], other_kind["item"]["id"], other_brand["item"]["id"], late["item"]["id"]}
    assert len(ids) == 4 and late["result"] == "new_find", ids


@test("capture: an exact barcode is the same item whatever it is called; a merged item forwards to its target")
def _():
    fake = _db()
    with _env(fake):
        a = _cap("rep1@example.com", [], kind="new_product", title="Mystery cable", barcode="6901234567892")
        b = _cap("rep2@example.com", [], kind="new_product", title="Totally different name", barcode="690-1234-567892")
        assert a["item"]["id"] == b["item"]["id"] and b["result"] == "seen_before"
        # merge that item into another: a new barcode sighting lands on the target
        t = _cap("rep1@example.com", [], kind="new_product", title="Target item")
        from app import market_intel as mi
        mi.update_item(a["item"]["id"], {"status": "merged", "merged_into": t["item"]["id"], "reason": "same product"},
                       "boss@example.com", "admin")
        c = _cap("rep2@example.com", [], kind="new_product", barcode="6901234567892", note="again")
    assert c["item"]["id"] == t["item"]["id"], c


@test("capture: a typed YQ code or name is 'Already in YQ' (card + item on the SKU); a Joyroom T10 is not YQ's T10")
def _():
    fake = _db()
    with _env(fake):
        a = _cap("rep1@example.com", [], kind="competitor_price", title="x05 uc-1mtr", price_bhd="1.100", shop_name="S1")
        b = _cap("rep2@example.com", [], kind="competitor_price", title="X05 UC 1Mtr", price_bhd="1.250", shop_name="S2")
        c = _cap("rep1@example.com", [], kind="shop_asked", title="UK12 more stock", demand_level="high")
        d = _cap("rep1@example.com", [], kind="new_product", title="Joyroom T10", brand="Joyroom")
        e = _cap("rep1@example.com", [], kind="new_product", title="T10 bluetooth earbuds")
    assert a["result"] == b["result"] == "already_in_yq" and a["yq_item"]["item_code"] == "X05 UC-1Mtr"
    assert a["item"]["id"] == b["item"]["id"] and "We already sell this" in a["message"]
    it = next(i for i in fake.rows("market_items") if i["id"] == a["item"]["id"])
    assert it["yq_item_code"] == "X05 UC-1Mtr" and it["title"] == "X05 UC 1Mtr Type-C Cable" and it["shop_count"] == 2
    assert c["result"] == "already_in_yq" and c["yq_item"]["item_code"] == "UK12" and c["item"]["kind"] == "shop_asked"
    assert c["item"]["id"] != a["item"]["id"], "another kind is another item"
    assert d["result"] == "new_find" and d["yq_item"] is None, d
    assert e["result"] == "already_in_yq" and e["yq_item"]["item_code"] == "T10"


@test("capture: a photo and no text is 'Saved: office will identify' (no item); a near-duplicate photo is flagged")
def _():
    fake = _db()
    with _env(fake):
        a = _cap("rep1@example.com", [("a.jpg", _jpeg(variant=0, orientation=1))])
        b = _cap("rep2@example.com", [("b.jpg", _jpeg(variant=0, quality=50, size=(1200, 900), orientation=1, gps=False))],
                 price_bhd="4")
        c = _cap("rep2@example.com", [("c.jpg", _jpeg(variant=1, orientation=1))])
    assert a["result"] == b["result"] == c["result"] == "saved_for_review" and a["item"] is None
    assert "office will identify" in a["message"].lower()
    rows = {o["id"]: o for o in fake.rows("market_observations")}
    assert all(o["item_id"] is None for o in rows.values())
    assert rows[b["observation_id"]]["near_duplicate_of"] == a["observation_id"] and b["near_duplicate"]
    assert "looks like one sent before" in b["message"]
    assert rows[c["observation_id"]]["near_duplicate_of"] is None and c["near_duplicate"] is None


@test("capture: validation — nothing to save, 5 photos, a bad price; a failed photo upload writes no sighting")
def _():
    fake = _db()
    with _env(fake):
        _raises(lambda: _cap("rep1@example.com", []), "photo or a note")
        _raises(lambda: _cap("rep1@example.com", [("p.jpg", _jpeg())] * 5), "at most 4")
        _raises(lambda: _cap("rep1@example.com", [], note="x", price_bhd="two"), "price")
        _raises(lambda: _cap("rep1@example.com", [("p.jpg", b"not an image")], note="x"), "valid photo")
        fake.storage.fail_upload = True
        _raises(lambda: _cap("rep1@example.com", [("p.jpg", _jpeg())], note="x"), "could not save the photo", 503)
    assert fake.rows("market_observations") == [] and fake.rows("market_items") == []


@test("capture: a lost insert race (23505 on client_uuid) answers the winner's card")
def _():
    from app import market_intel as mi
    fake = _db()
    cu = _u()
    with _env(fake):
        won = _cap("rep1@example.com", [], client_uuid=cu, title="Race item", kind="new_product")
        real = mi._obs_by_uuid
        calls = {"n": 0}

        def first_miss(u):
            calls["n"] += 1
            return None if calls["n"] == 1 else real(u)
        with _Patched((mi, "_obs_by_uuid", first_miss)):
            lost = _cap("rep1@example.com", [], client_uuid=cu, title="Race item", kind="new_product")
    assert lost["replayed"] and lost["observation_id"] == won["observation_id"]
    assert len(fake.rows("market_observations")) == 1 and len(fake.rows("market_items")) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 5. before the migration
# ═══════════════════════════════════════════════════════════════════════════════

@test("before the migration: photos land in Product Finds (grouped by client_uuid), a note in Field Notes; retries write nothing")
def _():
    from app import market_intel as mi
    fake = _db(missing=set(MI_TABLES))
    cu = _u()
    with _env(fake):
        card = _cap("rep1@example.com", [("a.jpg", _jpeg()), ("b.webp", _webp())], client_uuid=cu,
                    kind="competitor_price", title="Brand Q cable", price_bhd="1.5", shop_name="Shop A")
        again = _cap("rep1@example.com", [("a.jpg", _jpeg())], client_uuid=cu, kind="competitor_price", title="Brand Q cable")
        note = _cap("rep1@example.com", [], kind="shop_asked", note="Wants 20 more power banks", shop_name="Shop B")
        note2 = _cap("rep1@example.com", [], kind="shop_asked", note="Wants 20 more power banks", shop_name="Shop B")
        board = mi.board(office=True)
        _raises(lambda: mi.update_item(1, {"status": "researching"}, "boss@example.com", "admin"), "not set up", 409)
        _raises(lambda: mi.item_detail(1, office=True), "not set up", 409)
        assert mi.mine("rep1@example.com")["available"] is False
        assert mi.roll_demand_signals()["ok"] is False
    assert card["fallback"] == "legacy" and card["result"] == "saved_for_review" and card["photos"] == 2
    finds = fake.rows("product_finds")
    assert [f["source_file"] for f in finds] == [f"mi:{cu}:0", f"mi:{cu}:1"]
    assert finds[0]["posted_by"] == "rep1@example.com" and finds[0]["price_bhd"] == "1.500" and finds[0]["name"] == "Brand Q cable"
    assert "[Competitor price]" in finds[0]["note"] and "Shop A" in finds[0]["note"]
    assert all(p.startswith("finds/") for (b, p) in fake.storage.objects if b == "finds")
    assert again["replayed"] and len(finds) == 2
    notes = fake.rows("field_notes")
    assert len(notes) == 1 and notes[0]["category"] == "demand" and note2["replayed"]
    assert board["available"] is False and "r7b_market_intel_migration.sql" in board["hint"] and board["items"] == []


@test("probe: a hit is cached, a missing table met later forgets it (the reverse ran)")
def _():
    from app import market_intel as mi
    fake = _db()
    with _env(fake):
        assert mi.available() is True
        n = len(fake.calls)
        assert mi.available() is True and len(fake.calls) == n, "cached"
        fake.missing |= set(MI_TABLES)
        card = _cap("rep1@example.com", [], note="after the reverse", kind="other")
        assert card["fallback"] == "legacy" and mi.available() is False


# ═══════════════════════════════════════════════════════════════════════════════
# 6. the board, the item, the queue, my signals
# ═══════════════════════════════════════════════════════════════════════════════

def _seeded():
    fake = _db()
    with _env(fake):
        a = _cap("rep1@example.com", [("p.jpg", _jpeg())], kind="competitor_price", title="Anker 20W charger",
                 brand="Anker", category="CHARGER", price_bhd="3", shop_name="Shop A", area="Manama",
                 note="Rep one's private note")
        _cap("rep2@example.com", [], kind="competitor_price", title="anker charger 20w", brand="Anker",
             category="CHARGER", price_bhd="2.5", shop_name="Shop B", area="Riffa")
        b = _cap("rep2@example.com", [], kind="promotion", title="Buy 2 get 1 cables", brand="Hoco", category="CABLE",
                 shop_name="Shop C", area="Riffa")
        u = _cap("rep1@example.com", [("u.jpg", _jpeg(variant=1))])
    return fake, a, b, u


@test("board: office clusters count distinct shops and reps; filters by kind, brand, category, text, rep and area")
def _():
    from app import market_intel as mi
    fake, a, b, _u2 = _seeded()
    with _env(fake):
        out = mi.board(office=True)
        assert out["available"] and out["count"] == 2
        top = next(i for i in out["items"] if i["item_id"] == a["item"]["id"])
        assert (top["observations"], top["shops"], top["reps"]) == (2, 2, 2)
        assert (top["price_min"], top["price_max"]) == ("2.500", "3.000")
        assert top["summary"].startswith("2 sightings across 2 shops by 2 reps, BHD 2.500–3.000, first seen")
        assert top["cover_url"].startswith("https://signed.example/finds/market/") and top["status_label"] == "New"
        assert out["queue"] == {"new_items": 2, "unassigned": 1, "library": 0}
        assert [r["name"] for r in out["facets"]["reps"]] == ["Rep One", "Rep Two"]
        assert out["facets"]["areas"] == ["Manama", "Riffa"] and "Anker" in out["facets"]["brands"]
        f = lambda **kw: [i["item_id"] for i in mi.board(office=True, filters=kw)["items"]]  # noqa: E731
        assert f(kind="promotion") == [b["item"]["id"]]
        assert f(brand="anker") == [a["item"]["id"]] and f(category="cable") == [b["item"]["id"]]
        assert f(q="buy 2") == [b["item"]["id"]] and f(q="nothing like it") == []
        assert set(f(rep="1")) == {a["item"]["id"]} and set(f(area="riffa")) == {a["item"]["id"], b["item"]["id"]}
        _raises(lambda: mi.board(office=True, filters={"rep": "x"}), "unknown rep")
        assert f(status="approved") == []


@test("board: a salesman sees aggregated items with his own marked — no rep names, no rep or area filter")
def _():
    fake, a, b, _u2 = _seeded()
    with _env(fake):
        c = _client()
        r = c.get("/market-intel/items", headers=_h("rep1"))
        assert r.status_code == 200, r.text[:200]
        body = r.json()
        assert body["capabilities"] == {"office": False, "can_review": False, "can_approve": False, "can_capture": True}
        text = json.dumps(body)
        for secret in ("Rep One", "Rep Two", "rep2@example.com", "Shop B", "Shop C", "private note"):
            assert secret not in text, secret
        mine = {i["item_id"]: i["mine"] for i in body["items"]}
        assert mine == {a["item"]["id"]: True, b["item"]["id"]: False}
        assert body["queue"] is None and "reps" not in body["facets"]
        assert body["items"][0]["status_label"] in ("Office will review",)
        # rep / area filters are ignored for a rep (he cannot learn who saw what)
        r2 = c.get("/market-intel/items?rep=2&area=Riffa", headers=_h("rep1")).json()
        assert {i["item_id"] for i in r2["items"]} == {a["item"]["id"], b["item"]["id"]}
        assert c.get("/market-intel/review", headers=_h("rep1")).status_code == 403


@test("board: a note never becomes a title other reps read — the office sees it as the name, reps see only typed names")
def _():
    from app import market_intel as mi
    fake = _db()
    with _env(fake):
        a = _cap("rep1@example.com", [], kind="shop_asked", note="Shop of Mr Example wants 20 cables")
        b = _cap("rep2@example.com", [], kind="shop_asked", note="cables example mr of shop wants 20")
        assert a["result"] == "new_find" and b["result"] == "seen_before" and a["item"]["id"] == b["item"]["id"]
        assert "Mr Example" not in b["message"] and "already reported" in b["message"], b["message"]
        rep = mi.board(office=False, actor="rep2@example.com")
        office = mi.board(office=True)
        detail_rep = mi.item_detail(a["item"]["id"], office=False, actor="rep2@example.com")
        detail_office = mi.item_detail(a["item"]["id"], office=True)
    assert fake.rows("market_items")[0]["title"] is None
    assert rep["items"][0]["title"] is None and "Mr Example" not in json.dumps(rep)
    assert detail_rep["item"]["title"] is None and "Mr Example" not in json.dumps(detail_rep)
    assert office["items"][0]["title"] == "Shop of Mr Example wants 20 cables"
    assert detail_office["item"]["title"] == "Shop of Mr Example wants 20 cables"


@test("item: office sees every sighting with the rep's name, shops, decisions; a rep only his own; flagged photos hidden from him")
def _():
    from app import market_intel as mi
    fake, a, _b, _u2 = _seeded()
    with _env(fake):
        office = mi.item_detail(a["item"]["id"], office=True, actor="boss@example.com")
        assert [o["by"] for o in office["observations"]] == ["Rep Two", "Rep One"]
        assert {s["shop"] for s in office["shops"]} == {"Shop A", "Shop B"} and office["gap"] is True
        assert office["item"]["next_statuses"] == ["researching", "opportunity", "rejected", "merged"]
        assert office["decisions"] == [] and office["ai_suggestion"] is None
        photo_id = fake.rows("market_photos")[0]["id"]
        mi.flag_photo(photo_id, True, "boss@example.com", "admin")
        rep = mi.item_detail(a["item"]["id"], office=False, actor="rep2@example.com")
        assert len(rep["observations"]) == 1 and rep["observations"][0]["mine"] is True
        assert "by" not in rep["observations"][0] and "decisions" not in rep and "shops" not in rep
        rep1 = mi.item_detail(a["item"]["id"], office=False, actor="rep1@example.com")
        assert rep1["observations"][0]["photos"] == [], "a people-flagged photo never reaches a rep"
        again = mi.item_detail(a["item"]["id"], office=True)
        assert again["observations"][1]["photos"][0]["has_people"] is True and again["cluster"]["cover_url"] is None
        # an AI suggestion is shown Unverified until a person verifies it
        for it in fake.rows("market_items"):
            if it["id"] == a["item"]["id"]:
                it.update(ai_suggestion={"brand": "Anker", "model": "A2633"}, ai_confidence=0.62)
        ai = mi.item_detail(a["item"]["id"], office=True)["ai_suggestion"]
        assert ai["label"] == "Unverified" and ai["verified"] is False
        _raises(lambda: mi.item_detail(999, office=True), "does not exist", 404)


@test("review queue: new items + photo-only sightings; the imported Finds library is a separate page")
def _():
    from app import market_intel as mi
    fake, _a, _b, u = _seeded()
    fake.tables["market_observations"].append({"id": 900, "client_uuid": _u(), "source": "import", "kind": "new_product",
                                               "item_id": None, "observed_at": "2026-07-12T13:42:11+00:00",
                                               "legacy_ref": "product_finds:1", "price_bhd": "2.400"})
    with _env(fake):
        q = mi.review_queue()
        assert [o["id"] for o in q["unassigned"]] == [u["observation_id"]] and len(q["new_items"]) == 2
        assert q["unassigned"][0]["photos"][0]["url"].startswith("https://signed.example/finds/")
        lib = mi.review_queue(library=True)
        assert [o["id"] for o in lib["unassigned"]] == [900] and lib["new_items"] == []
        assert lib["unassigned"][0]["by"] == "Imported from Finds" and lib["unassigned"][0]["price_bhd"] == "2.400"


@test("my signals: the rep's own sightings with what became of each, never another rep's")
def _():
    from app import market_intel as mi
    fake, a, _b, u = _seeded()
    with _env(fake):
        mi.update_item(a["item"]["id"], {"status": "opportunity"}, "boss@example.com", "admin")
        out = mi.mine("rep1@example.com")
    assert [o["id"] for o in out["observations"]] == [u["observation_id"], a["observation_id"]]
    labels = {o["id"]: o["status_label"] for o in out["observations"]}
    assert labels[a["observation_id"]] == "Became an opportunity" and labels[u["observation_id"]] == "Office will identify it"
    assert all(o["mine"] for o in out["observations"])


# ═══════════════════════════════════════════════════════════════════════════════
# 7. decisions
# ═══════════════════════════════════════════════════════════════════════════════

@test("decisions: the transition map, Approved needs an action, every move is recorded with who and why")
def _():
    from app import market_intel as mi
    fake, a, _b, _u2 = _seeded()
    iid = a["item"]["id"]
    with _env(fake):
        _raises(lambda: mi.update_item(iid, {"status": "approved", "action": "source"}, "boss@example.com", "admin"),
                "cannot move", 409)
        assert mi.update_item(iid, {"status": "researching", "reason": "checking the distributor"},
                              "clerk@example.com", "member")["status"] == "researching"
        mi.update_item(iid, {"status": "opportunity"}, "clerk@example.com", "member")
        _raises(lambda: mi.update_item(iid, {"status": "approved"}, "boss@example.com", "admin"), "choose the action")
        mi.update_item(iid, {"status": "approved", "action": "price_response", "reason": "match at 2.750"},
                       "boss@example.com", "admin")
        mi.update_item(iid, {"title": "Anker Nano 20W", "barcode": "6901234567892", "yq_item_code": "uk12"},
                       "boss@example.com", "admin")
        _raises(lambda: mi.update_item(iid, {"yq_item_code": "NOPE-1"}, "boss@example.com", "admin"), "not a YQ catalog code")
        _raises(lambda: mi.update_item(iid, {"barcode": "12"}, "boss@example.com", "admin"), "6 to 20 digits")
        _raises(lambda: mi.update_item(iid, {"status": "bogus"}, "boss@example.com", "admin"), "unknown status")
        _raises(lambda: mi.update_item(iid, {"status": "new"}, "rep1@example.com", "salesman"), "only the office", 403)
        _raises(lambda: mi.update_item(iid, {"title": "x"}, "mgmt@example.com", "management"), "only the office", 403)
        assert mi.update_item(iid, {"title": "Anker Nano 20W"}, "boss@example.com", "admin")["changed"] is False
    item = next(i for i in fake.rows("market_items") if i["id"] == iid)
    assert item["status"] == "approved" and item["action"] == "price_response" and item["status_changed_by"] == "boss@example.com"
    assert item["yq_item_code"] == "UK12" and item["norm_key"] == "20w anker nano" and item["barcode"] == "6901234567892"
    dec = [(d["event"], d.get("from_status"), d.get("to_status"), d.get("actor")) for d in fake.rows("market_item_decisions")]
    assert dec[:3] == [("status", "new", "researching", "clerk@example.com"),
                       ("status", "researching", "opportunity", "clerk@example.com"),
                       ("approve", "opportunity", "approved", "boss@example.com")], dec
    assert dec[3][0] == "edit" and fake.rows("market_item_decisions")[0]["reason"] == "checking the distributor"
    assert set(fake.rows("market_item_decisions")[3]["detail"]) == {"title", "barcode", "yq_item_code"}


@test("decisions: merge moves the sightings to the target and records which; a merged item cannot move again")
def _():
    from app import market_intel as mi
    fake, a, b, _u2 = _seeded()
    with _env(fake):
        _raises(lambda: mi.update_item(a["item"]["id"], {"status": "merged", "merged_into": a["item"]["id"]},
                                       "boss@example.com", "admin"), "duplicates")
        r = mi.update_item(b["item"]["id"], {"status": "merged", "merged_into": a["item"]["id"], "reason": "same shelf"},
                           "boss@example.com", "admin")
        assert r["moved_observations"] == 1
        _raises(lambda: mi.update_item(b["item"]["id"], {"status": "new"}, "boss@example.com", "admin"), "cannot move", 409)
        out = mi.board(office=True)
    target = next(i for i in fake.rows("market_items") if i["id"] == a["item"]["id"])
    assert target["obs_count"] == 3 and target["shop_count"] == 3
    merge = [d for d in fake.rows("market_item_decisions") if d["event"] == "merge"][0]
    assert merge["detail"]["merged_into"] == a["item"]["id"] and len(merge["detail"]["moved_observations"]) == 1
    assert [i["item_id"] for i in out["items"]] == [a["item"]["id"]], "merged items leave the board"


@test("decisions: management approves an Opportunity (the one write it has) and cannot edit; a rep cannot approve")
def _():
    from app import market_intel as mi
    fake, a, b, _u2 = _seeded()
    with _env(fake):
        c = _client()
        r = c.post(f"/market-intel/items/{a['item']['id']}/approve", headers=_h("mgmt"), json={"action": "source"})
        assert r.status_code == 409 and "Only an Opportunity" in r.text, r.text[:200]
        mi.update_item(a["item"]["id"], {"status": "opportunity"}, "clerk@example.com", "member")
        r = c.post(f"/market-intel/items/{a['item']['id']}/approve", headers=_h("mgmt"),
                   json={"action": "source", "reason": "worth a sample order"})
        assert r.status_code == 200 and r.json()["status"] == "approved", r.text[:200]
        r = c.patch(f"/market-intel/items/{b['item']['id']}", headers=_h("mgmt"), json={"title": "renamed"})
        assert r.status_code == 403 and r.json()["detail"]["code"] == "read_only"
        r = c.post("/market-intel/observations", headers=_h("mgmt"), data={"note": "x"})
        assert r.status_code == 403 and r.json()["detail"]["code"] == "read_only"
        r = c.post(f"/market-intel/items/{b['item']['id']}/approve", headers=_h("rep1"), json={"action": "ignore"})
        assert r.status_code == 403
        r = c.post(f"/market-intel/items/{b['item']['id']}/approve", headers=_h("mgmt"), json={"action": "sell it"})
        assert r.status_code == 400
        body = c.get("/market-intel/items", headers=_h("mgmt")).json()
        assert body["capabilities"] == {"office": True, "can_review": False, "can_approve": True, "can_capture": False}
    item = next(i for i in fake.rows("market_items") if i["id"] == a["item"]["id"])
    assert item["status"] == "approved" and item["action"] == "source" and item["status_changed_by"] == "mgmt@example.com"
    d = fake.rows("market_item_decisions")[-1]
    assert (d["event"], d["actor_role"], d["action"], d["reason"]) == ("approve", "management", "source", "worth a sample order")
    assert next(i for i in fake.rows("market_items") if i["id"] == b["item"]["id"])["title"] == "Buy 2 get 1 cables"


@test("identify: a photo-only sighting joins an existing item or becomes a new one; photo flag is reviewers only")
def _():
    from app import market_intel as mi
    fake, a, _b, u = _seeded()
    with _env(fake):
        _raises(lambda: mi.identify_observation(u["observation_id"], {"title": "x"}, "rep1@example.com", "salesman"),
                "only the office", 403)
        _raises(lambda: mi.identify_observation(u["observation_id"], {}, "boss@example.com", "admin"), "name the item")
        r = mi.identify_observation(u["observation_id"], {"item_id": a["item"]["id"]}, "boss@example.com", "admin")
        assert r == {"ok": True, "observation_id": u["observation_id"], "item_id": a["item"]["id"], "created": False}
        r2 = mi.identify_observation(u["observation_id"], {"title": "Brand W earbuds", "kind": "new_product",
                                                           "category": "earphone"}, "clerk@example.com", "member")
        assert r2["created"] and r2["item_id"] != a["item"]["id"]
        _raises(lambda: mi.flag_photo(1, True, "rep1@example.com", "salesman"), "only the office", 403)
        _raises(lambda: mi.flag_photo(999, True, "boss@example.com", "admin"), "does not exist", 404)
    moved = next(i for i in fake.rows("market_items") if i["id"] == a["item"]["id"])
    assert moved["obs_count"] == 2, "the first item was recounted after the sighting moved on"
    new = next(i for i in fake.rows("market_items") if i["id"] == r2["item_id"])
    assert new["category"] == "EARPHONE" and new["obs_count"] == 1
    assert [d["event"] for d in fake.rows("market_item_decisions")] == ["identify", "identify"]


@test("AI reading: stays Unverified until a reviewer verifies it; nothing to verify without one; a photo flag is on the history")
def _():
    from app import market_intel as mi
    fake, a, _b, _u2 = _seeded()
    iid = a["item"]["id"]
    with _env(fake):
        _raises(lambda: mi.update_item(iid, {"verified": True}, "boss@example.com", "admin"), "no AI suggestion")
        for it in fake.rows("market_items"):
            if it["id"] == iid:
                it.update(ai_suggestion={"brand": "Anker"}, ai_confidence=0.7, verified=False)
        _raises(lambda: mi.update_item(iid, {"verified": True}, "mgmt@example.com", "management"), "only the office", 403)
        assert mi.update_item(iid, {"verified": True}, "clerk@example.com", "member")["changed"] is True
        assert mi.item_detail(iid, office=True)["ai_suggestion"]["label"] == "Verified"
        pid = fake.rows("market_photos")[0]["id"]
        mi.flag_photo(pid, True, "clerk@example.com", "member")
    dec = fake.rows("market_item_decisions")
    assert dec[0]["detail"] == {"verified": {"from": False, "to": True}} and dec[0]["actor"] == "clerk@example.com"
    assert dec[1]["detail"] == {"photo_people": {"photo_id": pid, "to": True}}


@test("AI Head script: suggestions are validated, ranking is distinct shops x recency, never approved / merged items")
def _():
    from scripts import market_intel_ai_head as ah
    ok, bad = ah.validate_suggestions([
        {"item_id": 1, "suggestion": {"brand": "Brand X"}, "confidence": 0.6234},
        {"item_id": "x", "suggestion": {"a": 1}, "confidence": 0.5},
        {"item_id": 2, "suggestion": {}, "confidence": 0.5},
        {"item_id": 3, "suggestion": {"a": 1}, "confidence": 1.5},
        {"item_id": 4, "suggestion": {"a": "x" * 5000}, "confidence": 0.5},
    ])
    assert ok == [{"item_id": 1, "suggestion": {"brand": "Brand X"}, "confidence": 0.623}] and len(bad) == 4
    assert ah.validate_suggestions({"not": "a list"})[1]
    now = datetime(2026, 9, 27, tzinfo=timezone.utc)
    rows = [
        {"row_type": "item", "item_id": 1, "status": "new", "shops": 5, "observations": 7, "system_signals": 0, "last_seen": now - timedelta(days=14)},
        {"row_type": "item", "item_id": 2, "status": "opportunity", "shops": 2, "observations": 2, "system_signals": 0, "last_seen": now},
        {"row_type": "item", "item_id": 3, "status": "approved", "shops": 9, "observations": 9, "system_signals": 0, "last_seen": now},
        {"row_type": "item", "item_id": 4, "status": "merged", "shops": 9, "observations": 9, "system_signals": 0, "last_seen": now},
        {"row_type": "unassigned", "observation_id": 9, "item_id": None, "status": "new", "shops": 0, "observations": 1,
         "system_signals": 0, "last_seen": now - timedelta(days=70)},
    ]
    ranked = ah.rank(rows, now, top=10)
    assert [r.get("item_id") or r.get("observation_id") for r in ranked] == [2, 1, 9]
    assert ranked[0]["score"] == 2.0 and ranked[1]["score"] == round(5 / 3, 4)
    src = (ROOT / "scripts" / "market_intel_ai_head.py").read_text(encoding="utf-8").lower()
    assert "verified = false" in src and "and not verified" in src and "update market_items set status" not in src


# ═══════════════════════════════════════════════════════════════════════════════
# 8. system demand signals
# ═══════════════════════════════════════════════════════════════════════════════

@test("signals: zero-result searches fold their prefixes, restock asks group per SKU, one sighting per week, dry run writes nothing")
def _():
    from app import market_intel as mi
    now = datetime(2026, 9, 27, 10, 0, tzinfo=timezone.utc)
    ev = []
    for i, q in enumerate(["sandisk", "Sandisk\\", "san", "memory", "mem", "memory", "ok", "123456", "wekome", "weko"]):
        ev.append({"id": i + 1, "event": "search_zero", "ts": (now - timedelta(hours=i)).isoformat(),
                   "session_id": f"s{i % 3}", "meta": {"q": q, "results": 0}})
    ev.append({"id": 99, "event": "search_zero", "ts": (now - timedelta(days=30)).isoformat(), "session_id": "old",
               "meta": {"q": "ancient"}})
    asks = [{"id": 1, "item_code": "UK12", "phone": "97330000001", "device_id": None, "created_at": (now - timedelta(days=1)).isoformat()},
            {"id": 2, "item_code": "UK12", "phone": None, "device_id": "dev-2", "created_at": (now - timedelta(days=2)).isoformat()},
            {"id": 3, "item_code": "UK12", "phone": "97330000001", "device_id": None, "created_at": (now - timedelta(days=2)).isoformat()}]
    fake = _db(shop_events=ev, shop_restock_requests=asks)
    with _env(fake):
        dry = mi.roll_demand_signals(now=now, dry_run=True)
        assert dry["written"] == {"search_zero": 3, "restock_ask": 1} and fake.rows("market_observations") == []
        out = mi.roll_demand_signals(now=now)
        again = mi.roll_demand_signals(now=now + timedelta(hours=5))
    assert out["written"] == {"search_zero": 3, "restock_ask": 1} and out["skipped"] == 0
    assert again["written"] == {"search_zero": 0, "restock_ask": 0} and again["skipped"] == 4
    obs = {o["title"]: o for o in fake.rows("market_observations")}
    assert set(obs) == {"sandisk", "memory", "wekome", "UK12 45W Charger"}, set(obs)
    assert obs["sandisk"]["demand_qty"] == 3 and obs["memory"]["demand_qty"] == 3 and obs["wekome"]["demand_qty"] == 2
    assert all(o["source"] == "system" and o["kind"] == "shop_asked" for o in obs.values())
    uk = obs["UK12 45W Charger"]
    assert uk["signal"] == "restock_ask" and uk["yq_item_code"] == "UK12" and uk["meta"]["distinct"] == 2 and uk["demand_qty"] == 3
    assert "97330000001" not in json.dumps(fake.rows("market_observations")), "no merchant phone is copied"
    assert all(o["item_id"] for o in obs.values()) and len(fake.rows("market_items")) == 4


@test("signals: the next week adds to the same item (seen before), and nothing schedules the roll-up yet")
def _():
    from app import market_intel as mi
    now = datetime(2026, 9, 27, 10, 0, tzinfo=timezone.utc)
    fake = _db(shop_events=[{"id": 1, "event": "search_zero", "ts": now.isoformat(), "session_id": "a", "meta": {"q": "sandisk"}}])
    with _env(fake):
        mi.roll_demand_signals(now=now)
        fake.tables["shop_events"].append({"id": 2, "event": "search_zero", "ts": (now + timedelta(days=7)).isoformat(),
                                          "session_id": "b", "meta": {"q": "sandisk"}})
        mi.roll_demand_signals(now=now + timedelta(days=7))
    assert len(fake.rows("market_observations")) == 2 and len(fake.rows("market_items")) == 1
    assert fake.rows("market_items")[0]["obs_count"] == 2
    jobs = (ROOT / "app" / "shop_jobs.py").read_text(encoding="utf-8")
    assert "roll_demand_signals" not in jobs and "market_intel" not in jobs


# ═══════════════════════════════════════════════════════════════════════════════
# 9. the capture route end to end
# ═══════════════════════════════════════════════════════════════════════════════

@test("route: multipart capture — photos + fields -> result card; the retry is idempotent; audited without the note")
def _():
    fake = _db()
    cu = _u()
    with _env(fake):
        c = _client()
        files = [("photos", ("a.jpg", _jpeg(), "image/jpeg")), ("photos", ("b.webp", _webp(), "image/webp"))]
        data = {"client_uuid": cu, "kind": "competitor_price", "title": "Brand Q 30W charger", "price_bhd": "4.25",
                "note": "cheaper than ours", "shop_name": "Shop A", "shop_phone": "30000007"}
        r = c.post("/market-intel/observations", headers=_h("rep1"), files=files, data=data)
        assert r.status_code == 200, r.text[:300]
        card = r.json()
        assert card["result"] == "new_find" and card["photos"] == 2 and card["client_uuid"] == cu
        r2 = c.post("/market-intel/observations", headers=_h("rep1"), files=files, data=data)
        assert r2.json()["replayed"] is True and r2.json()["observation_id"] == card["observation_id"]
        r3 = c.post("/market-intel/observations", headers=_h("rep1"),
                    files=[("photos", ("x.jpg", b"GIF89a....", "image/jpeg"))], data={"note": "x"})
        assert r3.status_code == 400 and "valid photo" in r3.json()["detail"]
        r4 = c.post("/market-intel/observations", headers=_h("rep1"), files=[("photos", ("a.jpg", _jpeg(), "image/jpeg"))] * 5,
                    data={"note": "x"})
        assert r4.status_code == 400
        r5 = c.post("/market-intel/observations", headers=_h("rep1"), data={"price_bhd": "1"})
        assert r5.status_code == 200 and r5.json()["result"] == "saved_for_review"
        r6 = c.post("/market-intel/observations", headers=_h("rep3"), data={"note": "no page"})
        assert r6.status_code == 403
    assert fake.rows("market_observations")[0]["price_bhd"] == "4.250" and len(fake.rows("market_photos")) == 2
    audits = [a for a in fake.rows("audit_log") if a["event"] == "market_intel.capture"]
    assert len(audits) == 3 and "cheaper" not in json.dumps(audits)


@test("route: PATCH, identify, people flag and review are reviewer / office only over HTTP")
def _():
    fake, a, _b, u = _seeded()
    with _env(fake):
        c = _client()
        assert c.patch(f"/market-intel/items/{a['item']['id']}", headers=_h("rep1"), json={"status": "researching"}).status_code == 403
        r = c.patch(f"/market-intel/items/{a['item']['id']}", headers=_h("clerk"), json={"status": "researching", "reason": "look"})
        assert r.status_code == 200 and r.json()["status"] == "researching"
        assert c.patch("/market-intel/items/999", headers=_h("clerk"), json={"status": "researching"}).status_code == 404
        r = c.post(f"/market-intel/observations/{u['observation_id']}/identify", headers=_h("boss"), json={"item_id": a["item"]["id"]})
        assert r.status_code == 200 and r.json()["item_id"] == a["item"]["id"]
        pid = fake.rows("market_photos")[0]["id"]
        assert c.post(f"/market-intel/photos/{pid}/people", headers=_h("rep1"), json={"flag": True}).status_code == 403
        assert c.post(f"/market-intel/photos/{pid}/people", headers=_h("clerk"), json={"flag": True}).status_code == 200
        rq = c.get("/market-intel/review", headers=_h("mgmt"))
        assert rq.status_code == 200 and rq.json()["capabilities"]["can_review"] is False
        d = c.get(f"/market-intel/items/{a['item']['id']}", headers=_h("clerk")).json()
        assert d["item"]["status"] == "researching" and d["decisions"][0]["actor"] == "clerk"


# ═══════════════════════════════════════════════════════════════════════════════
# 10. the SQL files and the import script, read as text
# ═══════════════════════════════════════════════════════════════════════════════

def _sql_body(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines()).lower()


@test("migration: additive, RLS on, revokes on tables / sequences / views, yq_readonly on the agent view only, no CASCADE")
def _():
    mig = MIGRATION.read_text(encoding="utf-8")
    low = _sql_body(mig)
    for t in ("market_items", "market_observations", "market_photos", "market_item_decisions"):
        assert f"create table if not exists {t}" in low, t
        assert f"alter table {t} enable row level security" in low, t
        assert f"revoke all on table {t} from anon, authenticated" in low, t
        assert f"revoke all on sequence {t}_id_seq from anon, authenticated" in low, t
    for v in ("v_market_clusters", "v_market_signals_agent"):
        assert f"create or replace view {v}" in low and f"revoke all on {v} from anon, authenticated" in low, v
    assert re.findall(r"grant\s+[^;]*;", low) == ["grant select on v_market_signals_agent to yq_readonly;"]
    assert "cascade" not in re.sub(r"on delete cascade", "", low) and "security_invoker" not in low
    assert not re.search(r"\bdrop\s+(table|view|column)\b", low) and "delete from" not in low
    assert not re.search(r"^\s*(truncate|update)\s", low, re.M), "no row of any table is rewritten"
    assert "constraint market_observations_client_uuid_key unique (client_uuid)" in low
    assert "product_finds" not in low and "field_notes" not in low, "the old tables are never touched"
    agent = low[low.index("create or replace view v_market_signals_agent"):low.index("revoke all on v_market_signals_agent")]
    agent = re.sub(r"case when.*?end", "", agent, flags=re.S)     # a shop is COUNTED there (0/1), never shown
    for col in ("created_by", "salesman_id", "shop_name", "shop_customer_id", "o.note", "path", "legacy_ref", "meta"):
        assert col not in agent, col
    for s in ("new", "researching", "opportunity", "approved", "rejected", "merged"):
        assert f"'{s}'" in low
    for k in ("new_product", "competitor_price", "promotion", "shop_asked", "complaint", "other"):
        assert f"'{k}'" in low


@test("reverse: drops the views before the tables and the history before the items, no CASCADE; grants script is reversible")
def _():
    rev = _sql_body(REVERSE.read_text(encoding="utf-8"))
    order = [rev.index(f"drop {k} if exists {n}") for k, n in (("view", "v_market_signals_agent"), ("view", "v_market_clusters"),
                                                                ("table", "market_item_decisions"), ("table", "market_photos"),
                                                                ("table", "market_observations"), ("table", "market_items"))]
    assert order == sorted(order), order
    assert "cascade" not in rev and "product_finds" not in rev.replace("to_regclass('public.product_finds')", "")
    g = _sql_body(GRANTS.read_text(encoding="utf-8"))
    gr = _sql_body(GRANTS_REVERSE.read_text(encoding="utf-8"))
    assert "features || '[\"market intel\"]'::jsonb" in g and "not features ? 'market intel'" in g and "role = 'salesman'" in g
    assert "features - 'market intel'" in gr and "delete" not in g + gr


@test("import: copies every find and note (never moves), groups the fallback rows per capture, stable ids, dry run by default")
def _():
    from scripts import market_intel_import as imp
    finds = [
        {"id": 1, "name": None, "price_bhd": Decimal("2.4"), "currency": "BHD", "note": "Joyroom 20W", "category": None,
         "source": "WhatsApp", "image_path": "finds/aaa.jpg", "status": "new", "promoted_item_code": None,
         "source_file": "IMG-001.jpg", "posted_by": "owner@example.com", "posted_at": datetime(2026, 7, 12, 13, 42, tzinfo=timezone.utc)},
        {"id": 2, "name": "Brand Q cable", "price_bhd": Decimal("1.5"), "currency": "BHD", "note": "[Competitor price] · x",
         "category": "CABLE", "source": "Market Intel", "image_path": "finds/bbb.webp", "status": "reviewing",
         "promoted_item_code": None, "source_file": "mi:11111111-2222-4333-8444-555555555555:0",
         "posted_by": "rep@example.com", "posted_at": datetime(2026, 9, 28, 8, 0, tzinfo=timezone.utc)},
        {"id": 3, "name": "Brand Q cable", "price_bhd": Decimal("1.5"), "currency": "BHD", "note": "[Competitor price] · x",
         "category": "CABLE", "source": "Market Intel", "image_path": "finds/ccc.webp", "status": "new",
         "promoted_item_code": None, "source_file": "mi:11111111-2222-4333-8444-555555555555:1",
         "posted_by": "rep@example.com", "posted_at": datetime(2026, 9, 28, 8, 0, tzinfo=timezone.utc)},
    ]
    notes = [{"id": 2, "note": "ok", "category": "demand", "created_by": "owner@example.com",
              "created_at": datetime(2026, 6, 30, 5, 59, tzinfo=timezone.utc), "image_path": None},
             {"id": 3, "note": "", "category": "demand", "created_by": "owner@example.com",
              "created_at": datetime(2026, 7, 3, 19, 34, tzinfo=timezone.utc), "image_path": "notes/ddd.jpg"}]
    plan = imp.build_plan(finds, notes)
    obs = plan["observations"]
    assert len(obs) == 4 and plan["counts"] == {"finds": 3, "notes": 2, "observations": 4, "photos": 4}
    assert all(o["source"] == "import" and o["item_id"] is None for o in obs)
    one = next(o for o in obs if o["legacy_ref"] == "product_finds:1")
    assert one["price_bhd"] == "2.400" and one["kind"] == "new_product" and one["observed_at"].startswith("2026-07-12")
    assert one["meta"]["legacy_status"] == "new" and one["photos"] == [{"bucket": "finds", "path": "finds/aaa.jpg", "position": 0}]
    grouped = next(o for o in obs if o["legacy_ref"] == "product_finds:2,3")
    assert grouped["client_uuid"] == "11111111-2222-4333-8444-555555555555" and len(grouped["photos"]) == 2
    assert grouped["kind"] == "competitor_price" and grouped["title"] == "Brand Q cable"
    note = next(o for o in obs if o["legacy_ref"] == "field_notes:3")
    assert note["photos"] == [{"bucket": "field-notes", "path": "notes/ddd.jpg", "position": 0}] and note["kind"] == "shop_asked"
    assert imp.build_plan(finds, notes)["observations"][0]["client_uuid"] == obs[0]["client_uuid"], "stable across runs"
    args = imp.parse_args([])
    assert args.apply is False and args.with_phash is False
    src = (ROOT / "scripts" / "market_intel_import.py").read_text(encoding="utf-8").lower()
    assert "delete from" not in src and "update product_finds" not in src and "update field_notes" not in src
    assert "on conflict (client_uuid) do nothing" in src


# ═══════════════════════════════════════════════════════════════════════════════
# 11. local Postgres replay (SKIPs cleanly without a local cluster)
# ═══════════════════════════════════════════════════════════════════════════════

LOCAL_DSN = os.environ.get("YQ_LOCAL_PG_R7B", os.environ.get("YQ_LOCAL_PG", "postgresql://postgres@localhost:55432/postgres"))

MINIMAL_SCHEMA = """
do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then create role anon nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
  if not exists (select 1 from pg_roles where rolname = 'yq_readonly') then create role yq_readonly nologin; end if;
end $$;
create table salesmen (id bigint generated by default as identity primary key, name text not null,
  referral_code text not null unique, user_email text);
create table shop_customers (id bigint generated by default as identity primary key, phone text not null unique,
  name text, shop text, area text);
grant all on all tables in schema public to anon, authenticated;
alter default privileges in schema public grant all on tables to anon, authenticated;
alter default privileges in schema public grant all on sequences to anon, authenticated;
"""


@test("local replay: apply twice, self-check, unique key, append-only history, distinct counts, agent grants, reverse (SKIP without the cluster)")
def _():
    try:
        import psycopg
    except ImportError:
        print("  SKIP: psycopg not installed")
        return
    try:
        conn = psycopg.connect(LOCAL_DSN, connect_timeout=2)
    except Exception as e:  # noqa: BLE001
        print(f"  SKIP: local Postgres unreachable ({type(e).__name__})")
        return
    mig = MIGRATION.read_text(encoding="utf-8")
    rev = REVERSE.read_text(encoding="utf-8")
    try:
        cur = conn.cursor()
        cur.execute("create schema if not exists r7b_scratch")
        cur.execute("select to_regclass('public.market_items')")
        if cur.fetchone()[0] is not None:
            print("  SKIP: this database already has market_items (the replay applies the migration itself)")
            return
        cur.execute("select to_regclass('public.salesmen')")
        if cur.fetchone()[0] is None:
            cur.execute(MINIMAL_SCHEMA)
        cur.execute(mig)
        cur.execute(mig)                      # idempotent
        cur.execute("insert into salesmen (name, referral_code) values ('Rep A', 'r7b-a'), ('Rep B', 'r7b-b') returning id")
        ra, rb = [r[0] for r in cur.fetchall()]
        cur.execute("insert into shop_customers (phone, name) values ('97339999001', 'Shop X') returning id")
        sx = cur.fetchone()[0]
        cur.execute("insert into market_items (kind, title, status) values ('competitor_price', 'Brand Q', 'new') returning id")
        item = cur.fetchone()[0]
        u = [str(uuid.uuid4()) for _ in range(4)]
        cur.execute("""insert into market_observations (item_id, client_uuid, source, kind, salesman_id, shop_customer_id, shop_name,
                         price_bhd, observed_at) values
                       (%s, %s, 'rep', 'competitor_price', %s, %s, null, 2.5, now() - interval '2 days'),
                       (%s, %s, 'rep', 'competitor_price', %s, null, 'Shop Y', 3.0, now() - interval '1 day'),
                       (%s, %s, 'rep', 'competitor_price', %s, %s, null, 2.75, now()),
                       (null, %s, 'rep', 'other', %s, null, null, null, now()) returning id""",
                    (item, u[0], ra, sx, item, u[1], rb, item, u[2], rb, sx, u[3], ra))
        oids = [r[0] for r in cur.fetchall()]
        cur.execute("insert into market_photos (observation_id, bucket, path, phash) values (%s, 'finds', 'market/x.webp', -5), "
                    "(%s, 'finds', 'market/y.webp', 7)", (oids[0], oids[3]))
        cur.execute("savepoint dup")
        try:
            cur.execute("insert into market_observations (client_uuid, kind) values (%s, 'other')", (u[0],))
            raise AssertionError("a second row with the same client_uuid must be refused")
        except psycopg.errors.UniqueViolation:
            cur.execute("rollback to savepoint dup")
        cur.execute("savepoint appr")
        try:
            cur.execute("update market_items set status = 'approved' where id = %s", (item,))
            raise AssertionError("Approved without an action must be refused")
        except psycopg.errors.CheckViolation:
            cur.execute("rollback to savepoint appr")
        cur.execute("insert into market_item_decisions (item_id, event, actor) values (%s, 'status', 'boss@example.com') returning id", (item,))
        did = cur.fetchone()[0]
        for stmt in (f"update market_item_decisions set reason = 'x' where id = {did}",
                     f"delete from market_item_decisions where id = {did}", "truncate market_item_decisions"):
            cur.execute("savepoint ao")
            try:
                cur.execute(stmt)
                raise AssertionError(f"append-only: {stmt}")
            except psycopg.errors.RestrictViolation:
                cur.execute("rollback to savepoint ao")
        cur.execute("select observations, shops, reps, price_min, price_max, photos, cover_path from v_market_clusters where item_id = %s", (item,))
        assert cur.fetchone() == (3, 2, 2, Decimal("2.500"), Decimal("3.000"), 1, "market/x.webp")
        cur.execute("select row_type, count(*) from v_market_signals_agent group by 1 order by 1")
        assert cur.fetchall() == [("item", 1), ("unassigned", 1)]
        cur.execute("""select count(*) from information_schema.role_table_grants where table_schema = 'public'
                         and table_name like '%%market%%' and grantee in ('anon', 'authenticated')""")
        assert cur.fetchone()[0] == 0
        cur.execute("select has_table_privilege('yq_readonly', 'public.v_market_signals_agent', 'SELECT'), "
                    "has_table_privilege('yq_readonly', 'public.v_market_clusters', 'SELECT'), "
                    "has_table_privilege('yq_readonly', 'public.market_observations', 'SELECT')")
        assert cur.fetchone() == (True, False, False)
        cur.execute("set local role yq_readonly")
        cur.execute("select count(*) from v_market_signals_agent")
        assert cur.fetchone()[0] == 2
        cur.execute("reset role")
        cur.execute(mig[mig.lower().index("do $$\ndeclare\n  t text;"):])      # the self-check on seeded data
        cur.execute(rev)
        cur.execute(rev)                      # idempotent
        cur.execute("select to_regclass('public.market_items'), to_regclass('public.v_market_clusters')")
        assert cur.fetchone() == (None, None)
        cur.execute(mig)                      # apply after reverse
        print(f"  replay: ok on {LOCAL_DSN.rsplit('/', 1)[-1]}")
    finally:
        conn.rollback()
        conn.close()


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
