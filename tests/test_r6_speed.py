"""Release R6, speed and reliability — tests.

    python -m tests.test_r6_speed

Same lightweight runner as tests/test_v3.py (no pytest). Everything is pure: the catalog route runs on
a synthetic context through FastAPI's TestClient (no Supabase call is ever made — share_token is
stubbed), the upload routes are checked by reading app/main.py's AST, the migration pair is parsed
against app.shop.EVENTS, and the web side is checked from the sources (wrangler config, prefetch,
transport, service-worker config, fonts, CSP, both ErrorBoundaries) plus the edge Worker itself,
run in plain node by web/scripts/market_worker_test.mjs (SKIPS, never fails, when node is missing —
the CI web job runs it for real).
"""
from __future__ import annotations

import ast
import gzip
import io
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# app.main only needs the URL shape at import (the JWKS client); nothing is fetched (ci.yml does the same)
os.environ.setdefault("SUPABASE_URL", "https://ci.invalid")

import warnings  # noqa: E402
warnings.filterwarnings("ignore")

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


class _Patched:
    """Temporarily set module attributes; restores on exit."""

    def __init__(self, *triples):
        self.triples = triples
        self.saved = []

    def __enter__(self):
        for mod, name, value in self.triples:
            self.saved.append((mod, name, getattr(mod, name)))
            setattr(mod, name, value)
        return self

    def __exit__(self, *exc):
        for mod, name, old in reversed(self.saved):
            setattr(mod, name, old)
        return False


# ── a synthetic catalog context (the shape tests/test_shop.py uses) ────────────

def _item(code, price, stock=100, photo=False):
    img = f"https://x/items/{code}-product-1.jpg" if photo else None
    return {"item_code": code, "display_name": code, "spec": f"{code} spec", "category": "CABLE", "brand": "VFAN",
            "standard_rate": price, "b2c_rate": None, "product_image_url": img, "package_image_url": None,
            "sort_order": None, "created_at": "2025-07-03T00:00:00+00:00", "moq": 1, "pack_size": None,
            "stock_qty": stock, "stock_as_of": "2026-09-24", "sold_30d": 0, "prev_30d": 0, "sold_90d": 0,
            "customers_30d": 0}


def _ctx(items=None, **settings):
    from app.shop import SETTING_DEFAULTS
    items = items if items is not None else [_item("X01", 1.0, photo=True), _item("C01", 0.5, stock=0)]
    vals = dict(SETTING_DEFAULTS, shop_market_enabled="1")
    vals.update({k: str(v) for k, v in settings.items()})
    return {"settings": vals, "items": {i["item_code"]: i for i in items}, "order": [i["item_code"] for i in items],
            "costs": {i["item_code"]: 0.001 for i in items}, "rules": [],
            "salesmen": [{"id": 1, "name": "Furqan Ahmed", "referral_code": "furqan", "is_active": True}],
            "loaded_at": "", "share_token": "tok"}


def _market_patch(ctx):
    """Route the catalog code at a synthetic context: no Supabase, no share-token lookup, and a
    settings read on the request path is a FAILURE (the point of the hot-path change)."""
    from app import shop

    def _no_settings(force=False):
        raise AssertionError("shop_settings() was called on the request path")
    return _Patched((shop, "context", lambda force=False: ctx),
                    (shop, "share_token", lambda create=False: None),
                    (shop, "shop_settings", _no_settings))


# ── API hot path ───────────────────────────────────────────────────────────────

@test("public_catalog_entry: raw + gzip + weak ETag, built once per context and per ref, bytes API unchanged")
def _():
    from app import shop
    ctx = _ctx()
    with _market_patch(ctx):
        e = shop.public_catalog_entry("tok", None)
        assert set(e) == {"raw", "gz", "etag"}, e.keys()
        assert gzip.decompress(e["gz"]) == e["raw"] and len(e["gz"]) < len(e["raw"]) // 2
        assert re.fullmatch(r'W/"[0-9a-f]{24}"', e["etag"]), e["etag"]
        assert shop.public_catalog_entry("tok", None) is e, "the entry is cached for the context"
        assert shop.public_catalog_json("tok", None) is e["raw"], "the bytes API returns the same object"
        f = shop.public_catalog_entry("tok", "furqan")
        assert f is not e and f["etag"] != e["etag"], "a rep link is its own entry with its own ETag"
        assert json.loads(f["raw"])["ref"] and json.loads(e["raw"])["ref"] is None
        assert shop.public_catalog_entry("tok", "nobody") is e, "an unknown ref shares the no-ref copy"
        assert set(ctx["public_json"]) == {"", "furqan"}, "junk refs never grow the cache"
        assert shop.public_catalog_entry("wrong", None) is None and shop.public_catalog_json("wrong") is None
        # the gzip bytes depend on the JSON alone, never on the clock (mtime=0): a second process
        # serving the same catalog produces the same bytes, so the edge revalidates cleanly
        assert shop._entry(e["raw"])["gz"] == e["gz"]


@test("GET /public/market: pre-gzipped body, weak ETag, Vary, and no settings read on the request path")
def _():
    from fastapi.testclient import TestClient
    import app.main as m
    ctx = _ctx()
    with _market_patch(ctx):
        c = TestClient(m.app)
        r = c.get("/public/market", headers={"accept-encoding": "gzip"})
        assert r.status_code == 200, r.text
        assert r.headers["etag"].startswith('W/"') and r.headers["cache-control"].startswith("public, max-age=60")
        assert "Accept-Encoding" in r.headers["vary"]
        assert r.headers["x-robots-tag"] == "noindex, nofollow"
        # httpx transparently decodes; the wire bytes are the entry's own gzip (the middleware did not
        # compress twice — it leaves an already-encoded response alone)
        assert r.headers.get("content-encoding") == "gzip"
        body = r.json()
        assert [i["item_code"] for i in body["items"]] == ["X01", "C01"]
        assert body["items"][0]["thumb_urls"] and set(body["items"][0]["thumb_urls"]) == {"160", "320", "512", "1024"}
        plain = c.get("/public/market", headers={"accept-encoding": "identity"})
        assert plain.status_code == 200 and plain.headers.get("content-encoding") is None
        assert plain.content == ctx["public_json"][""]["raw"], "identity = the raw bytes"
        assert plain.headers["etag"] == r.headers["etag"], "one weak ETag for both representations"


@test("GET /public/market: If-None-Match → 304 with no body (weak or strong tag, any encoding); a stale tag → 200")
def _():
    from fastapi.testclient import TestClient
    import app.main as m
    ctx = _ctx()
    with _market_patch(ctx):
        c = TestClient(m.app)
        etag = c.get("/public/market").headers["etag"]
        r = c.get("/public/market", headers={"if-none-match": etag, "accept-encoding": "gzip"})
        assert r.status_code == 304 and r.content == b"", (r.status_code, len(r.content))
        assert r.headers["etag"] == etag and r.headers["cache-control"].startswith("public, max-age=60")
        assert c.get("/public/market", headers={"if-none-match": etag[2:]}).status_code == 304, "strong form matches (weak comparison)"
        assert c.get("/public/market", headers={"if-none-match": 'W/"old", ' + etag}).status_code == 304, "a list matches"
        assert c.get("/public/market", headers={"if-none-match": "*"}).status_code == 304
        assert c.get("/public/market", headers={"if-none-match": 'W/"stale"'}).status_code == 200
        # a rep link has its own tag: the home tag never answers a storefront request
        assert c.get("/public/market?ref=furqan", headers={"if-none-match": etag}).status_code == 200


@test("GET /public/market: the switch comes from the cached context — closed marketplace is 404, uncached")
def _():
    from fastapi.testclient import TestClient
    import app.main as m
    with _market_patch(_ctx(shop_market_enabled="0")):
        r = TestClient(m.app).get("/public/market")
        assert r.status_code == 404 and "not open" in r.json()["detail"]


@test("/health: carries the deployed commit (RENDER_GIT_COMMIT, 12 chars) and stays small")
def _():
    from fastapi.testclient import TestClient
    import app.main as m
    old = os.environ.get("RENDER_GIT_COMMIT")
    os.environ["RENDER_GIT_COMMIT"] = "abcdef1234567890abcdef"
    try:
        r = TestClient(m.app).get("/health")
        assert r.status_code == 200
        assert r.json()["commit"] == "abcdef123456" and r.json()["status"] == "ok", r.json()
        assert len(r.content) < 200, "the keep-warm target every 10 minutes stays tiny"
        del os.environ["RENDER_GIT_COMMIT"]
        assert TestClient(m.app).get("/health").json()["commit"] == "", "no env → empty, never a crash"
    finally:
        if old is not None:
            os.environ["RENDER_GIT_COMMIT"] = old


# ── uploads off the event loop ─────────────────────────────────────────────────

UPLOAD_ROUTES = ["/assistant/upload", "/field-notes/photo", "/purchase-orders/upload", "/material-receipts/upload",
                 "/orders/{po_no}/photo", "/orders/{po_no}/file", "/orders/attach-doc", "/invoices/upload",
                 "/orders/verify", "/catalog/{code}/image", "/finds/photo"]


def _route_funcs(src: str) -> dict[str, ast.AsyncFunctionDef | ast.FunctionDef]:
    out = {}
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            for d in node.decorator_list:
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.args and isinstance(d.args[0], ast.Constant):
                    out[d.args[0].value] = node
    return out


@test("every upload route does its parse / encode / storage work in run_in_threadpool, after read_capped")
def _():
    src = _read("app/main.py")
    funcs = _route_funcs(src)
    for path in UPLOAD_ROUTES:
        fn = funcs.get(path)
        assert fn is not None, f"route {path} not found"
        assert isinstance(fn, ast.AsyncFunctionDef), f"{path} must be async (the whole point: it shares the one loop)"
        body = ast.get_source_segment(src, fn)
        assert "read_capped" in body, path
        assert "await run_in_threadpool(" in body, f"{path} still does its work on the event loop"
        assert body.index("read_capped(") < body.index("await run_in_threadpool("), f"{path}: the cap stays first"
    # the campaign image lives in shop_api.py
    api = _read("app/shop_api.py")
    fn = _route_funcs(api)["/shop/campaigns/image"]
    assert isinstance(fn, ast.AsyncFunctionDef) and "await run_in_threadpool(upload_campaign_image, data)" in ast.get_source_segment(api, fn)


@test("WebP: 1024 joins the rendition set; the request path encodes at method 4, the backfill keeps 6")
def _():
    from app import catalog
    assert catalog.THUMB_SIZES == (160, 320, 512, 1024)
    assert catalog.WEBP_METHOD_REQUEST == 4
    assert catalog.thumb_path("X01", "package", 1024) == "thumbs/X01-package-1024.webp"
    src = _read("app/catalog.py")
    assert "upload_thumb(code, kind, data, method=WEBP_METHOD_REQUEST)" in src, "upload_image → method 4"
    assert "make_banner(data, w, method=WEBP_METHOD_REQUEST)" in src, "campaign image → method 4"
    assert 'make_thumb(full_image, s, "WEBP", method=method)' in src
    try:
        from PIL import Image
    except ImportError:
        print("        SKIP encode check: Pillow not installed")
        return
    im = Image.new("RGB", (300, 200), (200, 30, 90))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    fast = catalog.make_thumb(buf.getvalue(), 128, "WEBP", method=4)
    slow = catalog.make_thumb(buf.getvalue(), 128, "WEBP")
    assert fast and fast[:4] == b"RIFF" and b"WEBP" in fast[:16]
    assert slow and slow[:4] == b"RIFF"
    assert Image.open(io.BytesIO(fast)).size == (128, 85)
    big = catalog.make_thumb(buf.getvalue(), 1024, "WEBP", method=4)
    assert Image.open(io.BytesIO(big)).size == (300, 200), "never upscaled past the original"
    # the payload builder follows THUMB_SIZES, so the 1024 key appears without touching shop.py
    from app.shop import _thumb_urls
    urls = _thumb_urls({"item_code": "X01", "product_image_url": "https://x/items/X01-product-1.jpg"})
    assert urls["1024"].endswith("/thumbs/X01-product-1024.webp")


# ── RUM + error telemetry ──────────────────────────────────────────────────────

def _ts_keys(block: str) -> list[str]:
    """Object-literal keys, `key: value` or the shorthand `key,` (never a `...spread`)."""
    return re.findall(r"^\s+([a-z_]+)(?::|,)", block, re.M)


@test("_META_KEYS: the vitals bag (exactly 12 keys, core first) and the error bag are whitelisted")
def _():
    from app.shop import EVENTS, _META_KEYS
    src = _read("web/src/market/lib/vitals.ts")
    bag = _ts_keys(src.split("meta: compact({", 1)[1].split("})", 1)[0])
    attr = _ts_keys(src.split("lcpAttr = {", 1)[1].split("}", 1)[0])
    keys = bag + attr
    assert keys[:3] == ["lcp", "inp", "cls"], keys
    assert len(keys) == 12 and len(set(keys)) == 12, keys
    missing = [k for k in keys if k not in _META_KEYS]
    assert not missing, missing
    for k in ("route", "vp", "catalog_ms", "catalog_src", "lcp_el", "lcp_ttfb", "lcp_delay", "lcp_load", "lcp_render", "build"):
        assert k in _META_KEYS, k
    err = _read("web/src/lib/errorMeta.ts")
    for k in ("where", "build", "code", "reason", "route"):
        assert k in _META_KEYS and f"{k}:" in err.split("export interface ErrorMeta", 1)[1].split("}", 1)[0]
    assert "error" in EVENTS and "vitals" in EVENTS


@test("vitals.ts: attribution build, CLS reportAllChanges, pagehide beacon even with no catalog, route templates")
def _():
    src = _read("web/src/market/lib/vitals.ts")
    assert "from 'web-vitals/attribution'" in src
    assert "onCLS(" in src and "{ reportAllChanges: true }" in src
    assert "window.addEventListener('pagehide', flush)" in src
    assert "catalog_src: note?.src || 'none'" in src, "an abandoned cold visit still reports"
    assert "if (sent) return" in src and "(m.lcp == null && m.inp == null && m.cls == null)" not in src, "the beacon no longer needs a metric"
    assert "a.timeToFirstByte" in src and "a.resourceLoadDelay" in src and "a.resourceLoadDuration" in src and "a.elementRenderDelay" in src
    assert "routeName(window.location.pathname)" in src and "'/:slug'" in src and "'/t/:category'" in src
    # the rum note is its own module (no import cycle marketApi → vitals → events → marketApi)
    rum = _read("web/src/market/lib/rum.ts")
    assert "import" not in rum.split("*/", 1)[1], "rum.ts imports nothing"
    assert "noteCatalog" in _read("web/src/market/lib/marketApi.ts")
    assert "web-vitals" in json.loads(_read("web/package.json"))["dependencies"]


@test("both ErrorBoundaries report: the market through track('error'), the portal/public door straight to the API")
def _():
    market = _read("web/src/market/ui/ErrorBoundary.tsx")
    assert "track('error'" in market and "errorMeta('market'" in market and "componentDidCatch" in market
    portal = _read("web/src/components/ErrorBoundary.tsx")
    assert "reportError(this.props.where || 'portal', error)" in portal
    assert 'where="public"' in _read("web/src/PublicApp.tsx")
    meta = _read("web/src/lib/errorMeta.ts")
    assert "__BUILD_ID__" in meta and "/public/market/event" in meta and "keepalive: true" in meta
    assert "<url>" in meta and "<email>" in meta and r"\d{6,}" in meta, "messages are scrubbed of links, e-mails and long digit runs"
    assert ".slice(0, 120)" in meta
    for banned in ("@/lib/api", "@/lib/supabase", "@/lib/auth"):
        assert banned not in meta, f"errorMeta must not pull {banned} into the public/market bundles"


@test("shop_events CHECK migration: the live 19 values + 'error' == app.shop.EVENTS; the reverse restores the 19")
def _():
    from app.shop import EVENTS

    def values(sql: str) -> list[str]:
        m = re.search(r"check \(event in \((.*?)\)\)", sql, re.S)
        assert m, "no CHECK list"
        return re.findall(r"'([a-z_]+)'", m.group(1))
    mig = _read("scripts/shop_events_error_migration.sql")
    rev = _read("scripts/shop_events_error_reverse.sql")
    assert values(mig) == list(EVENTS), (values(mig), EVENTS)
    assert "error" in values(mig) and len(values(mig)) == 20
    assert values(rev) == [e for e in EVENTS if e != "error"] and len(values(rev)) == 19
    for name in ("view", "vitals", "push_subscribe", "reorder", "cancel"):
        assert name in values(mig) and name in values(rev), f"{name} kept"
    assert not re.search(r"^\s*(commit|end)\s*;", mig, re.I | re.M), "no COMMIT: apply_sql --rehearse must be able to roll it back"
    assert "drop constraint if exists shop_events_event_check" in mig and "raise exception" in mig
    assert rev.index("delete from shop_events where event = 'error'") < rev.index("add constraint"), "rows first, then the narrower CHECK"
    assert "shop_events_error_reverse.sql" in mig


# ── edge catalog + web ─────────────────────────────────────────────────────────

def _jsonc(rel: str) -> dict:
    txt = re.sub(r"//[^\n]*", "", _read(rel))
    return json.loads(txt)


@test("wrangler.market.jsonc: Worker main + ASSETS binding, NO run_worker_first (files and navigations never spend an invocation); routes unchanged")
def _():
    cfg = _jsonc("web/wrangler.market.jsonc")
    assert cfg["main"] == "workers/market.js" and cfg["name"] == "yq-marketplace"
    a = cfg["assets"]
    assert a["directory"] == "./dist-market" and a["binding"] == "ASSETS"
    assert a["not_found_handling"] == "single-page-application", "a navigation to an unknown path is the asset layer's index.html, free"
    # review of 24-Sep-2026: /assets/* and /fonts/* in run_worker_first = every chunk a billed
    # invocation and, past Workers Free (100k/day), a 429 instead of the file; and ANY run_worker_first
    # array turns on static routing, under which the SPA fallback answers every miss itself and the
    # Worker never sees a missing chunk. With no list, a non-navigation miss (a chunk fetch, the
    # /api/market fetch) reaches the Worker and a navigation gets index.html for free.
    assert "run_worker_first" not in a, a
    assert cfg["compatibility_date"] >= "2025-04-01", "assets_navigation_prefers_asset_serving must be on"
    assert [r["pattern"] for r in cfg["routes"]] == ["yqmarketplace.com", "www.yqmarketplace.com"]
    assert (ROOT / "web/workers/market.js").exists()
    assert _jsonc("web/wrangler.portal.jsonc").get("main") is None, "the portal stays assets-only"


@test("workers/market.js: Cache API, origin rules from Cache-Control, 3 s soft timeout, non-200 never stored, 404 no-store")
def _():
    src = _read("web/workers/market.js")
    assert "caches.default" in src and "ORIGIN_SOFT_MS = 3000" in src
    assert "'https://yq-ops-assistant.onrender.com'" in src and "'/public/market'" in src and "'/api/market'" in src
    assert "stale-while-revalidate" in src and "stale-if-error" in src
    assert "if (fresh.status !== 200) return passThrough(fresh)" in src, "a non-200 is never stored"
    assert "if (!/application\\/json/i.test(type)) return null" in src, "a non-JSON 200 is passed through, never relabelled"
    # review of 24-Sep-2026: a 404/410 is the origin's answer (store closed) — copy deleted, never covered
    assert "function isGone(status)" in src and "status === 404 || status === 410" in src
    assert src.count("await remove(cache, key)") == 2, "deleted in the soft-timeout branch AND in the background refresh"
    assert "res.status >= 500 || res.status === 429" in src, "the last good copy covers 5xx, 429 and the synthetic 504 only"
    assert "ORIGIN_BG_MS = 25000" in src and "ORIGIN_HARD_MS" not in src.split("async function revalidate", 1)[1].split("\n}", 1)[0], "background work fits the ~30 s waitUntil window"
    assert "x-yq-grace-until" in src and "async function mark(" in src, "a soft failure opens a grace window for the visitors behind"
    assert "x-yq-reps" in src and "async function canonicalRef(" in src, "unknown slugs fold onto the no-ref copy"
    assert "'if-none-match'" in src and "etagMatches(" in src
    assert "'cache-control': 'no-store'" in src and "status: 404" in src
    assert "cache: 'no-store'" in src, "the subrequest bypasses Cloudflare's transparent fetch cache"
    assert "'/assets/'" in src and "'/fonts/'" in src and "env.ASSETS.fetch(request)" in src
    assert re.search(r"SLUG = /\^\[a-z0-9\]\[a-z0-9-\]\{1,31\}\$/", src), "ref validated with the slug shape the prefetch uses"


@test("web/scripts/market_worker_test.mjs: the Worker's cache states, timeouts and 404s pass in node")
def _():
    node = shutil.which("node")
    script = ROOT / "web" / "scripts" / "market_worker_test.mjs"
    if not node:
        print("        SKIP: node not available (ci.yml runs it in the web job)")
        return
    r = subprocess.run([node, str(script)], cwd=str(ROOT / "web"), capture_output=True, text=True, encoding="utf-8", timeout=180)
    if r.returncode != 0:
        print(r.stdout[-3000:])
        print(r.stderr[-1500:])
    assert r.returncode == 0, "market_worker_test.mjs failed"
    assert "all passed" in r.stdout


@test("catalog-prefetch.js + marketApi.ts: same-origin /api/market first, the API as the fallback, one shared key")
def _():
    pre = _read("web/public/catalog-prefetch.js")
    assert "url = '/api/market' + qs" in pre and "direct = api + '/public/market' + qs" in pre
    assert "indexOf('application/json') >= 0" in pre, "index.html from a host with no Worker is not a catalog"
    assert "'pre-edge-'" in pre and "'pre-api'" in pre and "window.__yqCatalog = early" in pre
    assert "x-yq-cache" in pre
    assert "reserved = { search: 1, cart: 1, checkout: 1, orders: 1, p: 1, t: 1, o: 1, c: 1, join: 1, shop: 1, me: 1, quick: 1, about: 1, help: 1, ask: 1, saved: 1, brands: 1, wekome: 1, 'coming-soon': 1 }" in pre, "reserved slugs untouched"
    api = _read("web/src/market/lib/marketApi.ts")
    assert "return `/api/market${ref ? `?ref=${encodeURIComponent(ref)}` : ''}`" in api
    assert "return `/public/market${ref ? `?ref=${encodeURIComponent(ref)}` : ''}`" in api
    assert "early.url === path" in api and "noteCatalog(early.src || 'pre')" in api
    assert "request<CatalogPayload>(marketApiPath(ref))" in api and "noteCatalog('api')" in api
    assert ".includes('application/json')" in api
    assert "src?: string" in _read("web/src/lib/shopApi.ts")
    qa = _read("scripts/qa/market_qa.py")
    assert 'page.route("**/api/market*", on_catalog)' in qa and 'path.endswith("/api/market")' in qa


@test("vite.config.ts: the SW caches the same-origin catalog, precache drops the PNGs and the portal fonts")
def _():
    src = _read("web/vite.config.ts")
    assert r"urlPattern: /^https?:\/\/[^/]+\/api\/market(\?.*)?$/" in src
    assert "urlPattern: new RegExp(`^${api}/public/market(\\\\?.*)?$`)" in src, "the fallback path keeps its rule"
    assert "globPatterns: ['**/*.{js,css,html,ico,svg,webp,webmanifest,woff2}']" in src, "no png in the precache"
    assert "includeAssets: []" in src
    assert "'fonts/inter-*.woff2', 'fonts/space-grotesk-*.woff2'" in src
    assert "navigateFallbackDenylist: [/^\\/public\\//, /^\\/api\\//, /\\/version\\.json$/]" in src
    assert "<!-- portal-fonts" in src, "marketHtml swaps the portal font block for the market's"


@test("portal: self-hosted Inter + Space Grotesk, no Google Fonts anywhere, CSP without the font hosts (both files)")
def _():
    html = _read("web/index.html")
    assert "fonts.googleapis.com" not in html and "fonts.gstatic.com" not in html
    assert 'href="/fonts/inter-v1.woff2"' in html and 'href="/fonts/space-grotesk-v1.woff2"' in html
    assert "<!-- portal-fonts" in html and "<!-- /portal-fonts -->" in html
    css = _read("web/src/index.css")
    assert "font-family: 'Inter';" in css and "url('/fonts/inter-v1.woff2')" in css
    assert "font-family: 'Space Grotesk';" in css and "url('/fonts/space-grotesk-v1.woff2')" in css
    for f in ("inter-v1.woff2", "space-grotesk-v1.woff2"):
        p = ROOT / "web/public/fonts" / f
        assert p.exists() and p.read_bytes()[:4] == b"wOF2", f
        assert p.stat().st_size < 60_000, f"{f}: the latin subset only"
    assert "SIL Open Font License" in _read("web/public/fonts/OFL-inter.txt")
    assert "SIL Open Font License" in _read("web/public/fonts/OFL-space-grotesk.txt")
    headers = _read("web/public/_headers")
    vercel = _read("web/vercel.json")
    csp_h = re.search(r"Content-Security-Policy: (.*)", headers).group(1).strip()
    csp_v = json.loads(vercel)
    csp_v = next(h["value"] for r in csp_v["headers"] for h in r["headers"] if h["key"] == "Content-Security-Policy")
    assert csp_h == csp_v, "the two CSP copies must stay identical"
    assert "fonts.g" not in csp_h and "font-src 'self';" in csp_h and "style-src 'self' 'unsafe-inline';" in csp_h
    assert "connect-src 'self' https://cloudflareinsights.com https://yq-ops-assistant.onrender.com" in csp_h
    # the market's own fonts are untouched
    assert "instrument-sans-v1.woff2" in _read("web/vite.config.ts")


@test("portal main.tsx: the vite:preloadError reload handler, once per session — same as the market's")
def _():
    portal = _read("web/src/main.tsx")
    market = _read("web/src/main.market.tsx")
    for src in (portal, market):
        assert "window.addEventListener('vite:preloadError'" in src
        assert "sessionStorage.getItem('yq-preload-reload') === '1'" in src
        assert "event.preventDefault()" in src and "window.location.reload()" in src


@test("images: the Lightbox and the package view walk a 1024/512 WebP chain that ends with the original")
def _():
    photos = _read("web/src/market/lib/photos.ts")
    assert "PRODUCT_512 = /-product-512\\.webp$/" in photos
    assert "`-package-${size}.webp`" in photos and "'-product-1024.webp'" in photos
    assert "cssPx * dpr > 560 ? 1024 : 512" in photos
    from app.catalog import thumb_path
    # the derivation and the server naming agree
    assert thumb_path("UK 15/A", "product", 512) == "thumbs/UK_15_A-product-512.webp"
    assert re.sub(r"-product-512\.webp$", "-package-1024.webp", thumb_path("UK 15/A", "product", 512)) == thumb_path("UK 15/A", "package", 1024)
    lb = _read("web/src/market/components/Lightbox.tsx")
    assert "srcs: string[]" in lb and "onError=" in lb and "p.srcs[step]" in lb
    assert "item.product_image_url ||" not in lb
    panel = _read("web/src/market/components/ProductPanel.tsx")
    assert "largePhotoChain(item, 'package', phone ? 272 : 416)" in panel
    assert "largePhotoChain(item, 'product', screenPx())" in panel and "largePhotoChain(item, 'package', screenPx())" in panel
    assert "{ src: item.product_image_url" not in panel, "the original is never the first candidate"


@test("docs/RELEASE.md: the release protocol and the rollback runbook name the real tools")
def _():
    doc = _read("docs/RELEASE.md")
    for needle in ("render_deploy.py", "wrangler versions upload", "wrangler versions deploy", "@100%",
                   "db_backup", "--restore", "prod_gate", "compare", "apply_sql", "--rehearse", "audit_grants",
                   "/health", "market_qa.py", "shop_events_error_migration.sql", "make_market_thumbs",
                   "Stop rule", "Rollback",
                   # review of 24-Sep-2026
                   "Sec-Fetch-Mode: cors", "Sec-Fetch-Mode: navigate", "Workers Free", "429", "catalog_src",
                   "percentile_cont", "deploys the web at push time"):
        assert needle in doc, needle
    assert "curl -sI https://yq-ops-assistant" not in doc, "FastAPI answers HEAD with 405: the API smoke is a GET"
    assert "-D - -H 'Accept-Encoding: gzip'" in doc and "If-None-Match: <that etag>" in doc
    api_step = doc.split("### 3. API", 1)[1].split("### 4. Web", 1)[0]
    assert api_step.index("make_market_thumbs --only-missing") < api_step.index("render_deploy deploy --commit"), "thumbs before the API deploy, in that order on the page too"


@test("review fixes: guarded catalog parses, the package chain ends on the product photo, analytics carries catalog_src + the LCP phases")
def _():
    api = _read("web/src/market/lib/marketApi.ts")
    assert api.count("JSON.parse(") == 2 and len(re.findall(r"try \{\s*(?:const )?data = JSON\.parse\(", api)) == 2, "both parses are guarded"
    assert "return fetchMarket(ref)" in api and "request<CatalogPayload>(marketApiPath(ref))" in api, "a body that does not parse falls back to the API"
    photos = _read("web/src/market/lib/photos.ts")
    assert "kind === 'package' ? item?.product_image_url : null, item?.thumb_url" in photos, "a broken package original ends on the product photo, not the empty tile"
    body = _read("app/shop.py").split("def analytics(", 1)[1].split("\n    return {", 1)[0]
    for k in ('"samples"', '"visits"', '"lcp_ttfb_ms_p75"', '"lcp_load_ms_p75"', '"lcp_render_ms_p75"', '"catalog_ms_p75"', '"catalog_src"'):
        assert k in body, k
    assert 'for k in ("lcp", "inp", "cls")' in body, "samples = beacons that carry a metric; visits = every beacon"


def main() -> int:
    passed = failed = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception:  # noqa: BLE001
            print(f"  FAIL  {name}")
            traceback.print_exc(limit=3)
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
