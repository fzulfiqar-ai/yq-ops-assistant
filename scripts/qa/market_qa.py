"""
YQ Marketplace — browser QA harness (Python Playwright, Chromium).

Walks the marketplace at every viewport we design for, in every state that matters (home, rep
storefront, browse + the destination shelves, category, search, quick order, restock empty / under
the minimum / ready, checkout as a small-order request and as a wholesale order, orders, My YQ,
about, the product panel, the opening moment, an injected campaign) and asserts the rules the v3
redesign must keep: no horizontal overflow, no console/page/request errors, self-hosted fonts and
no Google Fonts, tap targets and input sizes on touch, no cropped product/campaign imagery, no
product in two home rails, the pinned phone search band, the slider's dots and autoplay, the
wholesale-minimum wording and the small-order flow, reveals under reduced motion, the banned
merchandising words, and the sold-out rule ("Sold out" never "Out of stock"; no available card
after the first sold-out card on the home grid, Browse and a category shelf).

Run:  python scripts/qa/market_qa.py --base http://localhost:5174 --out <dir> [--only home,cart_under] [--quick]
Exit: 1 when a hard check fails (warnings never fail the run). See scripts/qa/README.md.

Safety: an order is NEVER placed. POST /public/market/order is intercepted and answered with a
mocked receipt, and so are the order-status read and the phone-recognition lookup behind it.
Every other mutating /public/* call (funnel events, vitals, restock requests, push, cancel) is
answered locally too, so a run against production writes nothing (only the quote POST, which
writes nothing, reaches the API).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PWError
from playwright.sync_api import Page, Route, sync_playwright

try:  # run as a script: scripts/qa is on sys.path
    from readonly_api import qa_receipt
except ImportError:  # imported as scripts.qa.market_qa
    from scripts.qa.readonly_api import qa_receipt

# ── copy under test ────────────────────────────────────────────────────────────────────────────
# Literals mirrored from web/src/market/strings.ts. verify_strings() re-reads that file and warns
# when a literal has drifted, so this harness fails loudly on copy changes instead of silently
# checking nothing.
STR = {
    "search.band": "Search products or codes",
    "nav.home": "Home",
    "nav.browse": "Browse",
    "nav.restock": "Restock",
    "nav.orders": "Orders",
    "nav.me": "My YQ",
    "nav.main": "Main navigation",
    "wholesale.away": "away from your wholesale order",
    "wholesale.keep": "Keep restocking",
    "wholesale.small": "Request a small order",
    "wholesale.ready": "Wholesale order ready",
    "wholesale.fill": "Complete your restock with these",
    "wholesale.closes": "completes your order",
    "small.title": "Small order request",
    "small.send": "Send request",
    "small.received": "Small order request received",
    "placed.title": "Wholesale order received",
    "checkout.place": "Place wholesale order",
    "slides.label": "Promotions",
    "spot.title": "Right now at YQ",
    "cart.mini": "Your restock",
    "home.all": "All products",
    "card.soldOut": "Sold out",
    "card.tellBack": "Tell me when back",
    "states.showMore": "Show more (",
    "shop.notInStock": "Not in stock now",
    "avail.few": "Few left",
    "head.list": "YQ Trade List",
}

# Text that must never reach a merchant's screen: the owner's honest-merchandising rule, and the
# sold-out rule (24-Sep-2026) — zero stock reads "Sold out" (Arabic «نفدت الكمية»), never "Out of stock".
BANNED = r"slow mover|no minimum|Save \d+%|out of stock"

CART_KEY = "yq-shop-cart:market"
SPLASH_KEY = "yq-splash-session"

# --reduced-motion: every state runs under prefers-reduced-motion, so two builds shot one after the
# other show the same frame (no slider or search-hint mid-rotation). Used by design reviews.
REDUCE_ALL = False

# ── viewports ──────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Viewport:
    name: str
    width: int
    height: int
    klass: str  # phone | tablet | desktop
    dpr: float = 1.0
    mobile: bool = False
    touch: bool = False


# R4 (25-Sep-2026): the six design viewports of the Stockbook spec — narrow and large phone, tablet
# portrait, tablet LANDSCAPE (the desktop layout under touch rules: no hover-only reveals, 44px
# targets), laptop and wide. The 1024x768 row is a touch context, so the tap-target and input-size
# probes run on the desktop shell too.
VIEWPORTS: list[Viewport] = [
    Viewport("360x780", 360, 780, "phone", 1, True, True),
    Viewport("430x932", 430, 932, "phone", 2, True, True),
    Viewport("768x1024", 768, 1024, "tablet", 1, False, True),
    Viewport("1024x768", 1024, 768, "desktop", 1, False, True),
    Viewport("1366x768", 1366, 768, "desktop"),
    Viewport("1920x1080", 1920, 1080, "desktop"),
]
QUICK_VIEWPORTS = {"430x932", "1366x768"}
# the one phone and the one desktop that carry the expensive extras (crops, slider timing, campaign)
LEAD_PHONE = "430x932"
LEAD_DESKTOP = "1366x768"

ALL_CLASSES = ("phone", "tablet", "desktop")

# ── states ─────────────────────────────────────────────────────────────────────────────────────

UNDER_CART = [{"item_code": "UK04-C", "qty": 5}]  # 5 × BHD 1.000 = 5.000, minimum is 20.000
MET_CART = [{"item_code": "UK15", "qty": 14}]  # 14 × BHD 1.500 = 21.000


@dataclass
class State:
    key: str
    route: str
    label: str
    cart: list[dict] | None = None
    kind: str = "page"  # page | checkout | opening | campaign
    order_kind: str = "standard"
    needles: tuple[str, ...] = ()  # text that must be on the page
    ready: str | None = None  # selector to wait for
    full_page: bool = False  # full-page shot on the lead phone + desktop
    classes: tuple[str, ...] = ALL_CLASSES
    reduced: bool = False
    lead_only: bool = False  # only the lead phone + lead desktop


def states(slug: str | None) -> list[State]:
    out = [
        State("home", "/", "Home, first visit", ready="main", full_page=True),
        State("shop", "/shop", "Browse", ready="main", full_page=True),
        State("shop_deals", "/shop?f=deals", "Browse · Deals", ready="main"),
        State("shop_clearance", "/shop?f=clearance", "Browse · Last chance", ready="main"),
        State("shop_best", "/shop?f=best", "Browse · Essentials", ready="main"),
        State("shop_moving", "/shop?f=moving", "Browse · Moving fast", ready="main"),
        State("category", "/t/cable", "Category · Cable", ready="main"),
        State("search", "/search", "Search, empty", ready="main"),
        State("search_q", "/search?q=charger", "Search · charger", ready="main"),
        State("quick", "/quick", "Quick order", ready="main"),
        State("cart_empty", "/cart", "Restock, empty", ready="main"),
        State(
            "cart_under",
            "/cart",
            "Restock, under the minimum",
            cart=UNDER_CART,
            needles=(STR["wholesale.away"],),
            ready="main",
            full_page=True,
        ),
        State("cart_met", "/cart", "Restock, minimum met", cart=MET_CART, needles=(STR["wholesale.ready"],), ready="main"),
        State(
            "checkout_small",
            "/checkout",
            "Checkout · small order request",
            cart=UNDER_CART,
            kind="checkout",
            order_kind="small",
            needles=(STR["small.title"],),
            ready="#yq-phone",
        ),
        State(
            "checkout_met",
            "/checkout",
            "Checkout · wholesale order",
            cart=MET_CART,
            kind="checkout",
            order_kind="standard",
            needles=(STR["checkout.place"],),
            ready="#yq-phone",
        ),
        State("orders", "/orders", "My orders", ready="main"),
        State("me", "/me", "My YQ", ready="main"),
        State("about", "/about", "About & help", ready="main"),
        State("about_trade", "/about#trade", "About · trade prices", ready="main"),
        State("product", "/p/UK04-C", "Product · UK04-C (the page on desktop, the sheet on phones)", ready="main"),
        State("brand", "/brands/wekome", "Brand · WEKOME (Coming soon)", ready="main"),
        State("tracking", "/o/qa-token", "Tracking · a mocked order", ready="main"),
        State("campaign", "/", "Home with an injected campaign", kind="campaign", ready="main", lead_only=True),
        State("reduced_home", "/", "Home, reduced motion", ready="main", reduced=True, lead_only=True),
        State(
            "reduced_cart",
            "/cart",
            "Restock under the minimum, reduced motion",
            cart=UNDER_CART,
            ready="main",
            reduced=True,
            lead_only=True,
        ),
        State("opening", "/", "The opening moment", kind="opening", lead_only=True),
    ]
    if slug:
        out.insert(1, State("storefront", "/" + slug, "Rep storefront /" + slug, ready="main"))
    return out


# ── findings ───────────────────────────────────────────────────────────────────────────────────


@dataclass
class Run:
    state: str
    route: str
    viewport: str
    label: str
    findings: list[dict] = field(default_factory=list)
    shots: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class Report:
    def __init__(self, out: Path, base: str) -> None:
        self.out = out
        self.base = base
        self.runs: list[Run] = []
        self.fails: list[dict] = []
        self.warns: list[dict] = []
        self.started = time.time()

    def add(self, run: Run) -> None:
        self.runs.append(run)
        for f in run.findings:
            (self.fails if f["level"] == "fail" else self.warns).append(f)

    def write(self) -> None:
        shots = [s for r in self.runs for s in r.shots]
        data = {
            "base": self.base,
            "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.started)),
            "seconds": round(time.time() - self.started, 1),
            "runs": len(self.runs),
            "shots": len(shots),
            "hard_failures": self.fails,
            "warnings": self.warns,
            "pages": [
                {
                    "state": r.state,
                    "route": r.route,
                    "viewport": r.viewport,
                    "label": r.label,
                    "findings": r.findings,
                    "shots": r.shots,
                    "notes": r.notes,
                }
                for r in self.runs
            ],
        }
        (self.out / "results.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
        self._index(shots)

    def _index(self, shots: list[dict]) -> None:
        lines = [
            "# YQ Marketplace — QA run",
            "",
            "- base: `" + self.base + "`",
            "- started: " + time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.started)),
            "- pages walked: " + str(len(self.runs)) + " · screenshots: " + str(len(shots)),
            "- hard failures: **" + str(len(self.fails)) + "** · warnings: " + str(len(self.warns)),
            "",
        ]
        if self.fails:
            lines += ["## Hard failures", ""]
            for f in self.fails:
                lines.append("- **" + f["check"] + "** · `" + f["route"] + "` · " + f["viewport"] + " · " + f["detail"])
            lines.append("")
        if self.warns:
            lines += ["## Warnings", ""]
            for f in self.warns:
                lines.append("- " + f["check"] + " · `" + f["route"] + "` · " + f["viewport"] + " · " + f["detail"])
            lines.append("")
        lines += ["## Screenshots", "", "| screenshot | route | viewport | state |", "|---|---|---|---|"]
        for s in shots:
            lines.append("| `" + s["path"] + "` | `" + s["route"] + "` | " + s["viewport"] + " | " + s["state"] + " |")
        lines.append("")
        (self.out / "index.md").write_text("\n".join(lines), encoding="utf-8")


def finding(run: Run, level: str, check: str, detail: str, values: Any = None) -> None:
    run.findings.append(
        {
            "level": level,
            "check": check,
            "state": run.state,
            "route": run.route,
            "viewport": run.viewport,
            "detail": detail,
            "values": values,
        }
    )


# ── the in-page probe ──────────────────────────────────────────────────────────────────────────
# One round trip per page: geometry, fonts, imagery, text. Everything that needs the DOM lives
# here so the Python side only turns numbers into findings.

PROBE = r"""
(opts) => {
  const vw = window.innerWidth, vh = window.innerHeight;
  const out = { vw, vh };

  const sel = (el) => {
    if (!el) return '?';
    let s = el.tagName.toLowerCase();
    if (el.id) s += '#' + el.id;
    const cls = (el.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean).slice(0, 3);
    if (cls.length) s += '.' + cls.join('.');
    const al = el.getAttribute('aria-label');
    if (al) s += '[aria-label="' + al.slice(0, 40) + '"]';
    return s;
  };
  const label = (el) => ((el.innerText || el.value || el.getAttribute('aria-label') || '').trim().split('\n')[0] || '').slice(0, 48);
  const shown = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) return null;
    if (cs.pointerEvents === 'none') return null;
    if (el.closest('[aria-hidden="true"]')) return null;
    if (el.closest('[inert]')) return null;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return null;
    return r;
  };
  const inView = (r) => r.bottom > 0 && r.top < vh && r.right > 0 && r.left < vw;

  /* ── horizontal overflow ── */
  const de = document.documentElement, bd = document.body;
  const scrollWidth = Math.max(de.scrollWidth, bd ? bd.scrollWidth : 0);
  out.overflow = { scrollWidth, innerWidth: vw, offenders: [] };
  if (scrollWidth > vw + 1) {
    for (const el of document.querySelectorAll('body *')) {
      const r = el.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) continue;
      if (r.right <= vw + 1 && r.left >= -1) continue;
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden') continue;
      if (cs.position === 'fixed') continue;
      /* inside a scroller (rail, slider track) is not a page overflow */
      let clipped = false;
      for (let p = el.parentElement; p && p !== de; p = p.parentElement) {
        const ps = getComputedStyle(p);
        if (/(hidden|auto|scroll|clip)/.test(ps.overflowX)) { clipped = true; break; }
      }
      if (clipped) continue;
      out.overflow.offenders.push({ sel: sel(el), left: Math.round(r.left), right: Math.round(r.right), width: Math.round(r.width) });
      if (out.overflow.offenders.length >= 6) break;
    }
  }

  /* ── tap targets ──
     A Tailwind hit-area expander (`after:absolute after:-inset-y-1`) grows the real target without
     changing the box, so the effective rect is the box plus any absolutely positioned pseudo with
     negative insets. A checkbox/radio inside a <label> is tapped by the label. */
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
  out.taps = [];
  if (opts.touch) {
    for (const el of document.querySelectorAll('a, button, input, select, textarea, [role="button"]')) {
      if (el.disabled) continue;
      if (el.type === 'hidden') continue;
      let r = shown(el);
      if (!r || !inView(r)) continue;
      /* an inline text link inside a paragraph is read, not tapped as a control */
      if (el.tagName === 'A' && el.closest('p')) continue;
      let target = el;
      if (el.tagName === 'INPUT' && (el.type === 'checkbox' || el.type === 'radio')) {
        const lab = el.closest('label') || (el.id ? document.querySelector('label[for="' + CSS.escape(el.id) + '"]') : null);
        if (lab) { target = lab; r = lab.getBoundingClientRect(); }
      }
      const e = effective(target, r);
      const m = Math.min(e.w, e.h);
      if (m < 44) out.taps.push({ sel: sel(el), text: label(target) || label(el), w: Math.round(e.w), h: Math.round(e.h) });
    }
  }

  /* ── input font size on touch (iOS zooms below 16px) ── */
  out.smallInputs = [];
  if (opts.touch) {
    for (const el of document.querySelectorAll('input, select, textarea')) {
      if (el.type === 'hidden' || el.type === 'checkbox' || el.type === 'radio') continue;
      if (!shown(el)) continue;
      const fs = parseFloat(getComputedStyle(el).fontSize);
      if (fs < 16) out.smallInputs.push({ sel: sel(el), fontSize: fs });
    }
  }

  /* ── cropped imagery: object-fit cover outside avatars ── */
  const circular = (el) => {
    const r = el.getBoundingClientRect();
    const br = getComputedStyle(el).borderRadius || '';
    if (br.includes('50%')) return true;
    const px = parseFloat(br);
    return !isNaN(px) && px >= Math.min(r.width, r.height) / 2 - 0.5;
  };
  out.covers = [];
  for (const img of document.querySelectorAll('img')) {
    if (getComputedStyle(img).objectFit !== 'cover') continue;
    let avatar = false;
    let n = img;
    for (let d = 0; n && d < 3; d++, n = n.parentElement) { if (circular(n)) { avatar = true; break; } }
    const r = img.getBoundingClientRect();
    out.covers.push({
      sel: sel(img),
      src: (img.currentSrc || img.src || '').slice(-80),
      avatar,
      inSlide: Boolean(img.closest('[data-slide-state], [aria-roledescription="slide"]')),
      w: Math.round(r.width), h: Math.round(r.height),
    });
  }

  /* ── text ── */
  const body = document.body ? document.body.innerText : '';
  out.banned = [];
  const re = new RegExp(opts.banned, 'gi');
  let m;
  while ((m = re.exec(body)) && out.banned.length < 5) {
    out.banned.push({ match: m[0], around: body.slice(Math.max(0, m.index - 40), m.index + 60).replace(/\s+/g, ' ') });
  }
  out.missing = (opts.needles || []).filter((t) => !body.includes(t));

  /* ── home: a product may live in one rail only ── */
  const codesIn = (root) => {
    const set = new Set();
    for (const img of root.querySelectorAll('img')) {
      const mm = /thumbs\/(.+?)-product-\d+\.webp/.exec(img.currentSrc || img.src || '');
      if (mm) set.add(decodeURIComponent(mm[1]));
    }
    return [...set];
  };
  out.sections = [];
  const main = document.querySelector('main') || document.body;
  for (const s of main.querySelectorAll('section[aria-labelledby]')) {
    const key = s.getAttribute('aria-labelledby');
    if (key === 'home-all') continue;                       /* the all-products grid repeats on purpose */
    if (s.parentElement && s.parentElement.closest('section[aria-labelledby]')) continue;
    out.sections.push({ key, codes: codesIn(s) });
  }

  /* ── slider ── */
  out.sliders = [];
  for (const sc of document.querySelectorAll('[aria-roledescription="carousel"]')) {
    const slides = sc.querySelectorAll('[aria-roledescription="slide"]').length;
    let dots = sc.querySelectorAll('button[aria-label^="Slide "]').length;
    if (!dots) dots = sc.querySelectorAll('span[aria-hidden="true"] > span').length;
    const active = [...sc.querySelectorAll('[data-slide-state]')].findIndex((e) => e.getAttribute('data-slide-state') === 'active');
    out.sliders.push({ label: sc.getAttribute('aria-label') || '', slides, dots, active });
  }

  /* ── reveals ── */
  const reveals = [...document.querySelectorAll('.reveal')];
  out.reveal = { total: reveals.length, hidden: reveals.filter((e) => Number(getComputedStyle(e).opacity) < 1).map(sel).slice(0, 8) };

  /* ── the phone's pinned search band ── */
  const band = document.querySelector('a[aria-label="' + opts.bandLabel + '"], [aria-label="' + opts.bandLabel + '"]');
  if (band) {
    const r = band.getBoundingClientRect();
    out.band = { top: Math.round(r.top), bottom: Math.round(r.bottom), height: Math.round(r.height) };
  } else {
    out.band = null;
  }

  /* ── the phone's bottom nav ── */
  const nav = document.querySelector('nav[aria-label="' + opts.navLabel + '"]');
  out.nav = nav ? [...nav.querySelectorAll('a')].map((a) => label(a)) : null;

  /* ── the opening overlay ── */
  const sp = document.querySelector('[data-splash]');
  out.splash = sp ? sp.getAttribute('data-splash') : null;

  /* ── desktop aside (the mini cart + spotlight column) ── */
  const aside = document.querySelector('aside');
  out.aside = aside ? { top: Math.round(aside.getBoundingClientRect().top), text: (aside.innerText || '').slice(0, 400) } : null;

  out.mainText = (main.innerText || '');
  return out;
}
"""


FONTS = r"""
async () => {
  try { await document.fonts.ready; } catch (e) { /* older engines */ }
  /* what the stylesheet declares and whether each face loaded: `check()` alone is true for a family
     no @font-face declares, so the positive proof is the declared face's own status */
  const faces = [...document.fonts].map((f) => ({ family: f.family.replace(/^"|"$/g, ''), status: f.status }));
  const declared = (name) => faces.filter((f) => f.family === name);
  const loaded = (name) => { const d = declared(name); return d.length > 0 && d.every((f) => f.status === 'loaded'); };
  const errored = faces.filter((f) => f.status === 'error').map((f) => f.family);
  return {
    instrument: document.fonts.check('16px "Instrument Sans"') && loaded('Instrument Sans'),
    mono: loaded('IBM Plex Mono'),
    monoDeclared: declared('IBM Plex Mono').length > 0,
    sora: declared('Sora').length > 0,
    errored,
  };
}
"""

SCROLL_THROUGH = r"""
async () => {
  const step = Math.round(window.innerHeight * 0.8);
  const end = document.documentElement.scrollHeight;
  for (let y = 0; y < end + step; y += step) {
    window.scrollTo(0, y);
    await new Promise((r) => setTimeout(r, 90));
  }
  await new Promise((r) => setTimeout(r, 250));
  window.scrollTo(0, 0);
  await new Promise((r) => setTimeout(r, 150));
}
"""

COUNT_TEXT = r"""
({ text, root }) => {
  const scope = document.querySelector(root);
  if (!scope) return -1;
  let n = 0;
  for (const el of scope.querySelectorAll('*')) {
    if (el.children.length) continue;
    if ((el.textContent || '').includes(text)) n += 1;
  }
  return n;
}
"""


# ── network ────────────────────────────────────────────────────────────────────────────────────

CORS = {
    "access-control-allow-origin": "*",
    "access-control-allow-headers": "*",
    "access-control-allow-methods": "GET,POST,OPTIONS",
    "cache-control": "no-store",
}

# requests that are allowed to fail (or that we answered ourselves)
ALLOWED_FAILURES = (
    "/public/market/event",   # fire-and-forget funnel ping
    "/public/market/order",   # intercepted: an order is never placed
    "/public/shop/order/",    # intercepted: the mocked receipt
    "/public/market/recognize",
)

# Chromium failure reasons that describe the HARNESS's own lifecycle, never the build under test.
# ERR_NETWORK_IO_SUSPENDED is raised when the network service is suspended out from under an
# in-flight request — i.e. a browser context being torn down between states. The app's own
# background plumbing (the /version.json poll in market/lib/sw.ts, the workbox-window chunk) is
# exactly what is still in the air at that moment, and market/lib/sw.ts already swallows it. Left
# unfiltered it lands as a `console-error` + `request-failed` pair against whichever state happened
# to be closing, which is noise a reader cannot act on.
IGNORED_FAILURE_REASONS = ("net::ERR_NETWORK_IO_SUSPENDED",)

QA_ORDER = {
    "order_no": "QA-0001",
    "token": "qa-token",
    "order_kind": "standard",
    "status": "new",
    "status_label": "Received",
    "assigned": False,
}


def json_route(route: Route, payload: dict, status: int = 200) -> None:
    route.fulfill(status=status, headers=dict(CORS), content_type="application/json", body=json.dumps(payload))


def install_mocks(page: Page, order_kind: str) -> None:
    """The order POST, the receipt read behind it and the phone lookup — never the real thing."""

    def on_order(route: Route) -> None:
        if route.request.method == "OPTIONS":
            route.fulfill(status=204, headers=dict(CORS), body="")
            return
        json_route(route, dict(QA_ORDER, order_kind=order_kind))

    def on_receipt(route: Route) -> None:
        if route.request.method == "OPTIONS":
            route.fulfill(status=204, headers=dict(CORS), body="")
            return
        # the same fictional receipt the local read-only stub serves (QA shop, QA rep, 3 lines)
        json_route(route, dict(qa_receipt(), order_kind=order_kind))

    def on_recognize(route: Route) -> None:
        if route.request.method == "OPTIONS":
            route.fulfill(status=204, headers=dict(CORS), body="")
            return
        json_route(route, {"known": None})

    page.route("**/public/market/order", on_order)
    page.route("**/public/shop/order/**", on_receipt)
    page.route("**/public/market/recognize", on_recognize)


def install_campaign(page: Page, campaign: dict) -> None:
    """Adds one synthetic campaign to the live catalog so the placement rules can be asserted."""

    def on_catalog(route: Route) -> None:
        req = route.request
        path = req.url.split("?")[0]
        # the app reads the same-origin edge copy (/api/market, R6) first and the API's
        # /public/market as the fallback — the synthetic campaign must ride whichever answers
        if req.method != "GET" or not (path.endswith("/public/market") or path.endswith("/api/market")):
            route.continue_()
            return
        try:
            res = route.fetch()
            if "application/json" not in (res.headers.get("content-type") or ""):
                # /api/market with no Worker in front of this host answers index.html; hand it back
                # untouched so the app falls back to the API, where the campaign is added below
                route.fulfill(response=res)
                return
            body = res.json()
        except Exception as exc:  # noqa: BLE001 — a dead API is a run-level failure, reported by the caller
            route.abort()
            raise RuntimeError("catalog fetch failed: " + str(exc)) from exc
        body["campaigns"] = list(body.get("campaigns") or []) + [campaign]
        json_route(route, body)

    page.route("**/public/market*", on_catalog)
    page.route("**/api/market*", on_catalog)


class Net:
    """Console, page errors and every request that did not come back."""

    def __init__(self) -> None:
        self.console: list[str] = []
        self.errors: list[str] = []
        self.failed: list[str] = []
        self.google_fonts: list[str] = []

    def attach(self, page: Page) -> None:
        def on_console(msg) -> None:
            if msg.type != "error":
                return
            # the console half of a torn-down request ("Failed to load resource: net::ERR_…")
            if any(r in msg.text for r in IGNORED_FAILURE_REASONS):
                return
            self.console.append(msg.text[:300])

        def on_pageerror(err) -> None:
            self.errors.append(str(err)[:300])

        def on_failed(req) -> None:
            if any(a in req.url for a in ALLOWED_FAILURES):
                return
            why = req.failure or "failed"
            if any(r in why for r in IGNORED_FAILURE_REASONS):
                return
            self.failed.append(req.method + " " + req.url + " — " + why)

        def on_response(res) -> None:
            if res.status >= 400 and not any(a in res.url for a in ALLOWED_FAILURES):
                self.failed.append(str(res.status) + " " + res.request.method + " " + res.url)

        def on_request(req) -> None:
            if "fonts.googleapis.com" in req.url or "fonts.gstatic.com" in req.url:
                self.google_fonts.append(req.url)

        page.on("console", on_console)
        page.on("pageerror", on_pageerror)
        page.on("requestfailed", on_failed)
        page.on("response", on_response)
        page.on("request", on_request)


# ── shots ──────────────────────────────────────────────────────────────────────────────────────


def shot(run: Run, page: Page, out: Path, name: str, full: bool = False, locator=None, clip: dict | None = None) -> None:
    rel = "shots/" + run.viewport + "/" + run.state + ("__" + name if name else "") + ".png"
    path = out / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if locator is not None:
            locator.screenshot(path=str(path), timeout=8000)
        elif clip is not None:
            page.screenshot(path=str(path), clip=clip, timeout=20000)
        else:
            page.screenshot(path=str(path), full_page=full, timeout=20000)
    except PWError as exc:
        run.notes.append("screenshot " + rel + " failed: " + str(exc)[:120])
        return
    run.shots.append(
        {"path": rel, "route": run.route, "viewport": run.viewport, "state": run.state + (" · " + name if name else ""), "label": run.label}
    )


def crop(run: Run, page: Page, out: Path, name: str, selector: str, index: int = 0) -> None:
    loc = page.locator(selector).nth(index)
    try:
        matches = loc.count()
        visible = bool(matches) and loc.is_visible()
    except PWError as exc:
        run.notes.append("crop " + name + " failed: " + str(exc)[:140])
        return
    if not visible:
        run.notes.append("crop " + name + " skipped (" + selector + " · matches=" + str(matches) + ")")
        return
    # centre it first: an element screenshot composes the viewport, so a card sitting under the
    # floating nav would be captured with the nav on top of it
    try:
        loc.evaluate("(el) => el.scrollIntoView({ block: 'center', inline: 'nearest' })")
        page.wait_for_timeout(250)
    except PWError:
        pass
    shot(run, page, out, name, locator=loc)


# ── page run ───────────────────────────────────────────────────────────────────────────────────


def init_script(st: State) -> str:
    cart = (
        "localStorage.setItem(" + json.dumps(CART_KEY) + ", " + json.dumps(json.dumps(st.cart)) + ");"
        if st.cart
        else "localStorage.removeItem(" + json.dumps(CART_KEY) + ");"
    )
    splash = "" if st.kind == "opening" else "sessionStorage.setItem(" + json.dumps(SPLASH_KEY) + ", '1');"
    return "(() => { try { " + cart + " } catch (e) {} try { " + splash + " } catch (e) {} })();"


def new_context(browser, vp: Viewport, st: State):
    ctx = browser.new_context(
        viewport={"width": vp.width, "height": vp.height},
        device_scale_factor=vp.dpr,
        is_mobile=vp.mobile,
        has_touch=vp.touch,
        service_workers="block",
        reduced_motion="reduce" if (st.reduced or REDUCE_ALL) else "no-preference",
        locale="en-GB",
    )
    ctx.add_init_script(init_script(st))

    # 24-Sep-2026: the harness must never write to production. Page-level mocks (install_mocks)
    # answer the order / receipt / recognize calls; this context-wide net answers EVERY other
    # mutating /public/* call (funnel events, vitals, restock "tell me", push, cancel ...) locally.
    # Only the quote POST passes through: it prices a cart and writes nothing.
    def no_prod_writes(route: Route) -> None:
        req = route.request
        if req.method in ("GET", "HEAD") or req.url.split("?")[0].endswith("/quote"):
            route.fallback()
            return
        if req.method == "OPTIONS":
            route.fulfill(status=204, headers=dict(CORS), body="")
            return
        json_route(route, {"ok": True, "qa": "not sent"})

    ctx.route("**/public/**", no_prod_writes)
    return ctx


def settle(page: Page, st: State, ready: str | None = None) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=12000)
    except PWError:
        pass
    sel = ready or st.ready
    if sel:
        try:
            page.wait_for_selector(sel, timeout=8000, state="attached")
        except PWError:
            pass
    page.wait_for_timeout(800)  # two-phase home render + the reveal pass


def probe(page: Page, vp: Viewport, needles: tuple[str, ...]) -> dict:
    return page.evaluate(
        PROBE,
        {
            "touch": vp.touch,
            "banned": BANNED,
            "needles": list(needles),
            "bandLabel": STR["search.band"],
            "navLabel": STR["nav.main"],
        },
    )


def probe_scan(page: Page, vp: Viewport, needles: tuple[str, ...]) -> dict:
    """
    The geometry checks only mean something for what is on screen, so the page is swept: the probe
    runs at the top and then one screen at a time (up to 6), and the findings are merged. Text is
    merged too — a `content-visibility: auto` section has no text until it is scrolled near.
    """
    p = probe(page, vp, needles)
    taps = {(t["sel"], t["text"]): t for t in p["taps"]}
    inputs = {i["sel"]: i for i in p["smallInputs"]}
    covers = {(c["sel"], c["src"]): c for c in p["covers"]}
    banned = {(b["match"], b["around"]): b for b in p["banned"]}
    missing = set(p["missing"])
    screens = page.evaluate(
        "() => Math.ceil(document.documentElement.scrollHeight / Math.max(1, window.innerHeight))"
    )
    for i in range(1, min(int(screens), 6)):
        page.evaluate("(y) => window.scrollTo(0, y)", int(i * vp.height * 0.85))
        page.wait_for_timeout(350)
        q = probe(page, vp, needles)
        taps.update({(t["sel"], t["text"]): t for t in q["taps"]})
        inputs.update({i2["sel"]: i2 for i2 in q["smallInputs"]})
        covers.update({(c["sel"], c["src"]): c for c in q["covers"]})
        banned.update({(b["match"], b["around"]): b for b in q["banned"]})
        missing &= set(q["missing"])
        if q["overflow"]["scrollWidth"] > p["overflow"]["scrollWidth"]:
            p["overflow"] = q["overflow"]
    page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(300)
    p["taps"] = list(taps.values())
    p["smallInputs"] = list(inputs.values())
    p["covers"] = list(covers.values())
    p["banned"] = list(banned.values())
    p["missing"] = sorted(missing)
    p["screens"] = screens
    return p


def common_checks(run: Run, page: Page, vp: Viewport, st: State, net: Net, p: dict) -> None:
    ov = p["overflow"]
    if ov["scrollWidth"] > ov["innerWidth"] + 1:
        who = ", ".join(o["sel"] + " (right " + str(o["right"]) + "px)" for o in ov["offenders"][:3]) or "no unclipped element found"
        finding(
            run,
            "fail",
            "horizontal-overflow",
            "scrollWidth " + str(ov["scrollWidth"]) + " > innerWidth " + str(ov["innerWidth"]) + " — " + who,
            ov,
        )

    for msg in net.console:
        finding(run, "fail", "console-error", msg)
    for msg in net.errors:
        finding(run, "fail", "page-error", msg)
    for msg in net.failed:
        finding(run, "fail", "request-failed", msg)
    for url in net.google_fonts:
        finding(run, "fail", "google-fonts", "the market build must self-host fonts: " + url)

    fonts = page.evaluate(FONTS)
    if not fonts.get("instrument"):
        finding(run, "fail", "fonts", "Instrument Sans is not loaded (document.fonts)")
    if fonts.get("monoDeclared") and not fonts.get("mono"):
        finding(run, "fail", "fonts", "IBM Plex Mono is declared but did not load (document.fonts)")
    for fam in fonts.get("errored") or []:
        finding(run, "fail", "fonts", "a declared face failed to load: " + str(fam))
    # R4 retired Sora (Instrument Sans carries the display sizes): a build that still declares it is
    # the previous design — reported, never failed, so a BEFORE run can be compared with an AFTER run
    if fonts.get("sora"):
        finding(run, "warn", "fonts", "Sora is still declared (retired by R4: the display face is Instrument Sans)")
    run.notes.append("fonts: instrument=" + str(bool(fonts.get("instrument"))) + " plex-mono=" + str(bool(fonts.get("mono"))) + " sora-declared=" + str(bool(fonts.get("sora"))))

    # one row per control shape and height — a rail of 12 cards, or 22 area chips, is one finding
    groups: dict[tuple[str, int], list[dict]] = {}
    for t in p["taps"]:
        shape = re.sub(r'\[aria-label="[^"]*"\]', "", t["sel"])
        groups.setdefault((shape, t["h"]), []).append(t)
    for (shape, h), hits in sorted(groups.items()):
        widths = sorted({x["w"] for x in hits})
        span = str(widths[0]) + ("–" + str(widths[-1]) if len(widths) > 1 else "")
        size = span + "x" + str(h) + "px"
        texts = [x["text"] for x in hits if x["text"]]
        who = shape + (" ×" + str(len(hits)) if len(hits) > 1 else "") + (" — " + ", ".join(texts[:3]) if texts else "")
        values = {"selector": shape, "widths": widths, "h": h, "count": len(hits), "samples": texts[:6]}
        if min(widths[0], h) < 36:
            finding(run, "fail", "tap-target", "below the 36px floor: " + size + " · " + who, values)
        else:
            finding(run, "warn", "tap-target", "below 44px: " + size + " · " + who, values)

    for i in p["smallInputs"]:
        finding(run, "fail", "input-font-size", str(i["fontSize"]) + "px < 16px on touch · " + i["sel"], i)

    for c in p["covers"]:
        if c["avatar"]:
            continue
        # a slide only crops when the campaign itself asked for image_fit: cover — allowed, but said
        level = "warn" if c["inSlide"] else "fail"
        where = " (a campaign creative with image_fit: cover)" if c["inSlide"] else ""
        finding(
            run,
            level,
            "object-fit-cover",
            "cropped image (" + str(c["w"]) + "x" + str(c["h"]) + ") " + c["sel"] + where + " · " + c["src"],
            c,
        )

    for b in p["banned"]:
        finding(run, "fail", "banned-copy", "“" + b["match"] + "” in: …" + b["around"] + "…", b)

    for t in p["missing"]:
        finding(run, "fail", "missing-copy", "expected text not on the page: “" + t + "”")

    for s in p["sliders"]:
        if s["slides"] > 1 and s["dots"] != s["slides"]:
            finding(
                run,
                "fail",
                "slider-dots",
                "carousel “" + s["label"] + "”: " + str(s["dots"]) + " dots for " + str(s["slides"]) + " slides",
                s,
            )

    if vp.klass == "phone" and p["nav"] is not None:
        want = [STR["nav.home"], STR["nav.browse"], STR["nav.restock"], STR["nav.orders"], STR["nav.me"]]
        got = [n.split(" ")[0] if n.startswith(STR["nav.restock"]) else n for n in p["nav"]]
        if got != want:
            finding(run, "fail", "bottom-nav", "tabs are " + str(got) + ", expected " + str(want))

    if st.reduced:
        run.notes.append("reduced motion: " + str(p["reveal"]["total"]) + " .reveal elements on the page")
        if p["reveal"]["hidden"]:
            finding(
                run,
                "fail",
                "reduced-motion-reveal",
                str(len(p["reveal"]["hidden"])) + " of " + str(p["reveal"]["total"]) + " .reveal elements are transparent without scrolling: "
                + ", ".join(p["reveal"]["hidden"][:3]),
                p["reveal"],
            )
        if p["splash"] not in (None, "still"):
            finding(run, "fail", "reduced-motion-opening", "the opening is animated under reduced motion: data-splash=" + str(p["splash"]))


def home_checks(run: Run, page: Page, vp: Viewport, p: dict) -> None:
    seen: dict[str, str] = {}
    for sec in p["sections"]:
        for code in sec["codes"]:
            if code in seen and seen[code] != sec["key"]:
                finding(
                    run,
                    "fail",
                    "home-duplicate-product",
                    code + " appears in both “" + seen[code] + "” and “" + sec["key"] + "”",
                    {"code": code, "sections": [seen[code], sec["key"]]},
                )
            else:
                seen.setdefault(code, sec["key"])

    if vp.klass == "phone":
        page.evaluate("() => window.scrollTo(0, 800)")
        page.wait_for_timeout(500)
        band = page.evaluate(
            "(label) => { const b = document.querySelector('[aria-label=\"' + label + '\"]');"
            " if (!b) return null; const r = b.getBoundingClientRect();"
            " const cs = getComputedStyle(b); const vis = cs.display !== 'none' && cs.visibility !== 'hidden' && Number(cs.opacity) > 0;"
            " return { top: Math.round(r.top), bottom: Math.round(r.bottom), visible: vis }; }",
            STR["search.band"],
        )
        if not band:
            finding(run, "fail", "pinned-search", "the search band is gone after scrolling 800px")
        elif not band["visible"] or band["top"] > 140 or band["bottom"] < 0:
            finding(
                run,
                "fail",
                "pinned-search",
                "after 800px the search band sits at top " + str(band["top"]) + "px (must stay within the top 140px)",
                band,
            )
        page.evaluate("() => window.scrollTo(0, 0)")
        page.wait_for_timeout(300)


def band_hint_check(run: Run, page: Page) -> None:
    """
    The rotating search hint crossfades inside the pinned band. Watched frame by frame over two
    rotations, two things must hold: no text paints outside the band unless an ancestor clips it,
    and once the crossfade has finished only one hint line is still visible — an outgoing line left
    behind is the ghost above the pill.
    """
    js = (
        "async (label) => { const band = document.querySelector('[aria-label=\"' + label + '\"]'); if (!band) return null;"
        " let escape = null, ghost = null; const t0 = performance.now();"
        " const leaves = () => [...band.querySelectorAll('*')].filter((el) => !el.children.length && (el.textContent || '').trim());"
        " while (performance.now() - t0 < 5600) {"  # two full hint rotations (2.4s each)
        "   await new Promise((r) => requestAnimationFrame(r));"
        "   const b = band.getBoundingClientRect(); const nodes = leaves();"
        "   for (const el of nodes) {"
        "     const r = el.getBoundingClientRect(); if (r.width < 1 || r.height < 1) continue;"
        "     const over = Math.max(b.top - r.top, r.bottom - b.bottom, b.left - r.left, r.right - b.right);"
        "     if (over <= 1) continue;"
        "     let clipped = false;"
        "     for (let p = el.parentElement; p && p !== band.parentElement; p = p.parentElement) {"
        "       if (getComputedStyle(p).overflow !== 'visible') { clipped = true; break; } }"
        "     if (!clipped && (!escape || over > escape.over))"
        "       escape = { text: (el.textContent || '').trim().slice(0, 40), over: Math.round(over) };"
        "   }"
        "   const animated = nodes.filter((el) => el.getAnimations().length);"
        "   const resting = animated.length > 1 && animated.every((el) => el.getAnimations().every((a) => a.playState === 'finished'));"
        "   if (resting) {"
        "     const lit = animated.filter((el) => Number(getComputedStyle(el).opacity) > 0.05);"
        "     if (lit.length > 1) ghost = lit.map((el) => (el.textContent || '').trim().slice(0, 30));"
        "   }"
        " } return { escape, ghost }; }"
    )
    found = page.evaluate(js, STR["search.band"])
    if found is None:
        run.notes.append("no pinned search band at this width")
        return
    if found.get("escape"):
        e = found["escape"]
        finding(
            run,
            "fail",
            "search-band-clip",
            "“" + e["text"] + "” paints " + str(e["over"]) + "px outside the search band and nothing clips it"
            " (the rotating hint needs overflow:hidden)",
            e,
        )
    if found.get("ghost"):
        finding(
            run,
            "fail",
            "search-hint-ghost",
            "the crossfade leaves two hint lines visible at rest: " + " / ".join(found["ghost"]),
            found["ghost"],
        )
    if not found.get("escape") and not found.get("ghost"):
        run.notes.append("search band: the hint crossfade stays inside the pill and leaves one line")


# The sold-out rule (24-Sep-2026): on every listing the sold-out cards come after every available
# one, whatever sort the merchant chose. Each MarketCard carries data-stock (in_stock / low_stock /
# out_of_stock) for exactly this check. The grids page in chunks, so "Show more" is pressed until
# the whole listing is on the page — otherwise the sold-out tail is never rendered and the check
# proves nothing.
SOLDOUT_ROOTS = {"home": 'section[aria-labelledby="home-all"]', "shop": "main", "category": "main"}
# R4: the sold-out lines sit after a ruled divider "Not in stock now · N lines" — never hidden
SOLDOUT_DIVIDER = '.shelf-divider'  # a heading (h2/h3), never role=separator, so it is read out
SOLDOUT_EXPAND_MAX = 12

STOCK_ORDER = r"""
(root) => {
  const scope = document.querySelector(root);
  if (!scope) return null;
  const cards = [...scope.querySelectorAll('article[data-stock]')];
  const states = cards.map((a) => a.getAttribute('data-stock'));
  const firstOut = states.indexOf('out_of_stock');
  const late = [];
  if (firstOut >= 0) {
    states.forEach((s, i) => {
      if (i > firstOut && s !== 'out_of_stock') {
        const code = (cards[i].querySelector('.code') || cards[i].querySelector('[class*="tnum"]') || cards[i]).textContent.trim().slice(0, 24);
        late.push({ index: i, state: s, code });
      }
    });
  }
  return {
    total: states.length,
    available: states.filter((s) => s !== 'out_of_stock').length,
    soldOut: states.filter((s) => s === 'out_of_stock').length,
    firstOut,
    late: late.slice(0, 6),
  };
}
"""


def expand_listing(page: Page) -> int:
    """Presses “Show more” until the listing is complete (bounded). Returns the presses made."""
    presses = 0
    while presses < SOLDOUT_EXPAND_MAX:
        btn = page.get_by_role("button", name=re.compile(r"^" + re.escape(STR["states.showMore"])))
        target = None
        for i in range(btn.count()):
            if btn.nth(i).is_visible():
                target = btn.nth(i)
                break
        if target is None:
            break
        try:
            target.evaluate("(el) => el.scrollIntoView({ block: 'center' })")
            target.click(timeout=4000)
        except PWError:
            break
        presses += 1
        page.wait_for_timeout(350)
    return presses


def soldout_order_check(run: Run, page: Page, root: str) -> None:
    presses = expand_listing(page)
    found = page.evaluate(STOCK_ORDER, root)
    page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(250)
    if not found:
        finding(run, "warn", "soldout-order", "no listing root at " + root + " — the sold-out order was not checked")
        return
    run.notes.append(
        "sold-out order: " + str(found["available"]) + " available · " + str(found["soldOut"]) + " sold out"
        + " (" + str(found["total"]) + " cards, Show more ×" + str(presses) + ")"
    )
    # the R4 divider: present exactly when a sold-out line is rendered, and above the first one
    divider = page.evaluate(
        "([root, sel]) => { const scope = document.querySelector(root); if (!scope) return null;"
        " const d = scope.querySelector(sel); const first = scope.querySelector('article[data-stock=\"out_of_stock\"]');"
        " if (!d) return { present: false, text: '' };"
        " const above = first ? Boolean(d.compareDocumentPosition(first) & Node.DOCUMENT_POSITION_FOLLOWING) : null;"
        " return { present: true, text: (d.textContent || '').trim().slice(0, 60), above }; }",
        [root, SOLDOUT_DIVIDER],
    )
    if divider and found["soldOut"] > 0:
        if not divider["present"]:
            finding(run, "fail", "soldout-divider", "sold-out cards are rendered without the “" + STR["shop.notInStock"] + "” divider")
        elif divider["above"] is False:
            finding(run, "fail", "soldout-divider", "the divider sits below the first sold-out card")
        else:
            run.notes.append("sold-out divider: “" + divider["text"] + "”")
    if found["late"]:
        who = ", ".join(x["code"] + " (#" + str(x["index"]) + ", " + x["state"] + ")" for x in found["late"][:3])
        finding(
            run,
            "fail",
            "soldout-order",
            str(len(found["late"])) + " available card(s) after the first sold-out card (#" + str(found["firstOut"]) + "): " + who,
            found,
        )
    elif found["soldOut"] == 0:
        run.notes.append("sold-out order: no sold-out card rendered on this listing, so the rule was not exercised")


def paste_offers(run: Run, page: Page) -> None:
    """How many times the first-visit home sends the merchant to /quick (the owner asked for ≤2)."""
    links = page.evaluate(
        "() => [...document.querySelectorAll('main a[href^=\"/quick\"], main a[href*=\"/quick?\"]')]"
        ".map((a) => (a.innerText || a.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim().slice(0, 40))"
    )
    run.notes.append("paths to /quick on home: " + str(len(links)) + " — " + json.dumps(links, ensure_ascii=False))
    if len(links) > 2:
        finding(
            run,
            "warn",
            "paste-repeat",
            "home offers “paste your list” " + str(len(links)) + " times: " + ", ".join(x for x in links if x),
            links,
        )


def aside_check(run: Run, page: Page) -> None:
    """The desktop aside is sticky under the header — its heading must not hide behind it."""
    m = page.evaluate(
        "() => { window.scrollTo(0, 600); const a = document.querySelector('aside');"
        " const h = document.querySelector('header'); if (!a || !h) return null;"
        " const ar = a.getBoundingClientRect(), hr = h.getBoundingClientRect();"
        " const first = a.querySelector('h1, h2, h3'); const fr = first ? first.getBoundingClientRect() : null;"
        " return { asideTop: Math.round(ar.top), headerBottom: Math.round(hr.bottom), headerHeight: Math.round(hr.height),"
        "   headingTop: fr ? Math.round(fr.top) : null, heading: first ? (first.innerText || '').trim().slice(0, 40) : null }; }"
    )
    page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(250)
    if not m:
        run.notes.append("no desktop aside at this width")
        return
    run.notes.append("aside top " + str(m["asideTop"]) + "px · sticky header bottom " + str(m["headerBottom"]) + "px")
    if m["asideTop"] < m["headerBottom"] - 1:
        finding(
            run,
            "fail",
            "aside-under-header",
            "the aside starts at " + str(m["asideTop"]) + "px, under a sticky header that ends at " + str(m["headerBottom"])
            + "px — “" + str(m["heading"]) + "” is hidden",
            m,
        )


def reveal_check(run: Run, page: Page) -> None:
    page.evaluate(SCROLL_THROUGH)
    left = page.evaluate(
        "() => [...document.querySelectorAll('.reveal')].filter((e) => Number(getComputedStyle(e).opacity) < 1)"
        ".map((e) => e.tagName.toLowerCase() + '.' + (e.getAttribute('class') || '').split(' ').slice(0, 2).join('.'))"
    )
    if left:
        finding(run, "fail", "reveal-stuck", str(len(left)) + " .reveal elements never became visible: " + ", ".join(left[:4]), left)


def slider_timing(run: Run, page: Page) -> None:
    """
    Autoplay turns the slides on its own, and stops when the merchant touches the track. Measured on
    a freshly loaded page: taking the element screenshots scrolls the track, which the carousel reads
    as a touch and answers with its 8s quiet period.
    """
    page.reload(wait_until="domcontentloaded", timeout=40000)
    try:
        page.wait_for_load_state("networkidle", timeout=12000)
    except PWError:
        pass
    page.wait_for_timeout(900)
    active_js = (
        "() => { const s = document.querySelector('[aria-roledescription=\"carousel\"]'); if (!s) return -1;"
        " const els = [...s.querySelectorAll('[data-slide-state]')];"
        " return els.findIndex((e) => e.getAttribute('data-slide-state') === 'active'); }"
    )
    start = page.evaluate(active_js)
    if start < 0:
        return

    def wait_change(frm: int, budget: float) -> float | None:
        t0 = time.time()
        while time.time() - t0 < budget:
            page.wait_for_timeout(250)
            if page.evaluate(active_js) != frm:
                return round(time.time() - t0, 1)
        return None

    moved = wait_change(start, 7.5)
    if moved is None:
        finding(run, "warn", "slider-autoplay", "the slide did not change within 7.5s (dwell is 6s) — autoplay may be paused")
    else:
        run.notes.append("autoplay advanced after " + str(moved) + "s")

    # The dots row, not a slide: every pixel of the track belongs to the slide's link, and tapping
    # that would navigate. The row is the one aria-hidden span whose children are the empty dots.
    dots = page.evaluate(
        "(n) => { const s = document.querySelector('[aria-roledescription=\"carousel\"]'); if (!s) return null;"
        " const rows = [...s.querySelectorAll('span[aria-hidden=\"true\"]')].filter((e) => e.children.length >= 2"
        "   && [...e.children].every((c) => c.tagName === 'SPAN' && c.children.length === 0));"
        " const row = rows.find((e) => e.children.length === n) || rows[0]; if (!row) return null;"
        " const r = row.getBoundingClientRect(); if (r.width < 1) return null;"
        " return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; }",
        page.evaluate("() => document.querySelectorAll('[aria-roledescription=\"slide\"]').length"),
    )
    if not dots:
        run.notes.append("slider hold not tested: the dots row was not found")
        return
    before = page.url
    page.touchscreen.tap(dots["x"], dots["y"])
    page.wait_for_timeout(250)
    if page.url != before:
        finding(run, "warn", "slider-hold", "the tap on the dots row navigated to " + page.url + " — hold not tested")
        page.go_back()
        page.wait_for_timeout(800)
        return
    held = page.evaluate(active_js)
    again = wait_change(held, 7.0)
    if again is not None:
        finding(
            run,
            "warn",
            "slider-hold",
            "the slider kept turning " + str(again) + "s after a pointerdown (it should hold for 8s)",
        )
    else:
        run.notes.append("autoplay held for 7s after the tap")


def small_order_sheet(run: Run, page: Page, out: Path) -> None:
    btn = page.get_by_role("button", name=STR["wholesale.small"])
    if btn.count() == 0:
        finding(run, "fail", "small-order-cta", "“" + STR["wholesale.small"] + "” is not on the restock page")
        return
    target = None
    for i in range(btn.count()):
        if btn.nth(i).is_visible():
            target = btn.nth(i)
            break
    if target is None:
        finding(run, "fail", "small-order-cta", "“" + STR["wholesale.small"] + "” exists but is not visible")
        return
    target.click()
    page.wait_for_timeout(600)
    body = page.evaluate("() => document.body.innerText")
    if STR["small.title"] not in body:
        finding(run, "fail", "small-order-sheet", "the sheet did not show “" + STR["small.title"] + "”")
    shot(run, page, out, "small-sheet")
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)


def keep_restocking(run: Run, page: Page) -> None:
    for label in (STR["wholesale.keep"], STR["wholesale.small"]):
        n = page.get_by_role("button", name=label).count() + page.get_by_role("link", name=label).count()
        if n == 0:
            finding(run, "fail", "restock-actions", "“" + label + "” is missing from the restock page")


def gap_filler_report(run: Run, page: Page) -> None:
    """Records what the gap fillers actually offer (quantities and the completes-your-order tag)."""
    rows = page.evaluate(
        "(fill) => { const hs = [...document.querySelectorAll('h3')].filter((h) => (h.textContent || '').includes(fill));"
        " if (!hs.length) return null; const ul = hs[0].parentElement.querySelector('ul'); if (!ul) return null;"
        " return [...ul.children].map((li) => (li.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 120)); }",
        STR["wholesale.fill"],
    )
    if rows:
        run.notes.append("gap fillers: " + " | ".join(rows))
        closes = [r for r in rows if STR["wholesale.closes"] in r]
        if len(rows) > 1 and len(closes) == len(rows):
            finding(
                run,
                "warn",
                "gap-filler-mix",
                "every one of the " + str(len(rows)) + " gap fillers is tagged “" + STR["wholesale.closes"] + "” — the basket should mix lines, not force one SKU",
                rows,
            )


def checkout_flow(run: Run, page: Page, st: State, out: Path) -> None:
    page.fill("#yq-phone", "39001122")
    page.fill("#yq-name", "QA Merchant")
    if page.locator("#yq-shop").count():
        page.fill("#yq-shop", "QA Test Shop")
    if page.locator("#yq-area").count():
        page.fill("#yq-area", "Manama")
    else:
        area = page.get_by_role("button", name="Manama", exact=True)
        if area.count():
            area.first.click()
    page.wait_for_timeout(400)
    shot(run, page, out, "form")

    label = STR["small.send"] if st.order_kind == "small" else STR["checkout.place"]
    btn = page.get_by_role("button", name=label)
    target = None
    for i in range(btn.count()):
        b = btn.nth(i)
        if b.is_visible() and b.is_enabled():
            target = b
            break
    if target is None:
        finding(run, "fail", "checkout-submit", "no enabled “" + label + "” button on the checkout")
        return
    target.click()
    try:
        page.wait_for_url(re.compile(r"/o/qa-token"), timeout=15000)
    except PWError:
        finding(run, "fail", "checkout-submit", "the mocked order did not reach the placed screen")
        return
    page.wait_for_timeout(1200)
    body = page.evaluate("() => document.body.innerText")
    want = STR["small.received"] if st.order_kind == "small" else STR["placed.title"]
    if want not in body:
        finding(run, "fail", "placed-screen", "the placed screen does not say “" + want + "”")
    if st.order_kind == "small" and STR["placed.title"] in body:
        finding(run, "fail", "placed-screen", "a small order request is announced as a wholesale order")
    run.route = "/o/qa-token"
    shot(run, page, out, "placed")


def campaign_checks(run: Run, page: Page, title: str) -> None:
    main_n = page.evaluate(COUNT_TEXT, {"text": title, "root": "main"})
    aside_n = page.evaluate(COUNT_TEXT, {"text": title, "root": "aside"})
    if main_n != 1:
        finding(
            run,
            "fail",
            "campaign-once",
            "the injected campaign title renders " + str(main_n) + " times in the main column (expected exactly 1)",
        )
    if aside_n > 0:
        finding(run, "fail", "campaign-aside", "the desktop aside repeats the campaign " + str(aside_n) + " time(s)")
    elif aside_n < 0:
        run.notes.append("no aside at this width — the aside-repeat rule was not exercised")


QA_CAMPAIGN = {
    "id": 9901,
    "title": "QA Last-chance week",
    "line": "Real lines we are clearing",
    "cta_label": "See lines",
    "cta_to": "/shop?f=clearance",
    "placement": ["hero", "strip", "aside"],
    "audience": "all",
    "sponsored": False,
    "product_codes": ["UK04-C", "X13-L", "L12-FL"],
    "canvas": "apricot",
    "image_fit": "contain",
}


# ── one state at one viewport ──────────────────────────────────────────────────────────────────


def run_state(browser, vp: Viewport, st: State, base: str, out: Path, report: Report, lead: bool) -> None:
    run = Run(state=st.key, route=st.route, viewport=vp.name, label=st.label)
    ctx = new_context(browser, vp, st)
    page = ctx.new_page()
    net = Net()
    net.attach(page)
    install_mocks(page, st.order_kind)
    if st.kind == "campaign":
        install_campaign(page, QA_CAMPAIGN)
    try:
        page.goto(base + st.route, wait_until="domcontentloaded", timeout=40000)
        settle(page, st)

        p = probe_scan(page, vp, st.needles)
        common_checks(run, page, vp, st, net, p)

        if st.key in ("home", "storefront", "campaign", "reduced_home"):
            home_checks(run, page, vp, p)
            paste_offers(run, page)
        if st.key == "home" and vp.name == LEAD_PHONE:
            band_hint_check(run, page)
        if st.key == "campaign":
            campaign_checks(run, page, QA_CAMPAIGN["title"])
        if st.key in ("cart_under", "reduced_cart"):
            keep_restocking(run, page)
            gap_filler_report(run, page)
        if st.key == "cart_under" and lead:
            small_order_sheet(run, page, out)
        if vp.klass == "desktop" and st.key in ("home", "shop", "campaign", "storefront"):
            aside_check(run, page)
        if vp.klass == "desktop" and st.key in ("home", "shop", "campaign"):
            reveal_check(run, page)
            page.evaluate("() => window.scrollTo(0, 0)")
            page.wait_for_timeout(300)
        # ── shots ── (before the slider timing, which touches the track)
        if st.kind == "checkout":
            shot(run, page, out, "")
            checkout_flow(run, page, st, out)
        else:
            shot(run, page, out, "")

        if lead and st.full_page:
            page.evaluate(SCROLL_THROUGH)
            shot(run, page, out, "full", full=True)

        if lead and st.key == "home":
            crop(run, page, out, "card-1", "main article", 0)
            crop(run, page, out, "card-2", "main article", 1)
            crop(run, page, out, "slider", '[aria-roledescription="carousel"]', 0)
            if vp.klass == "phone":
                # the band is sticky and its box reaches above the viewport once the brand row has
                # scrolled out, so these two are clipped viewport shots, not element crops
                band = {"x": 0, "y": 0, "width": vp.width, "height": min(200, vp.height)}
                page.evaluate("() => window.scrollTo(0, 0)")
                page.wait_for_timeout(300)
                shot(run, page, out, "header-top", clip=band)
                page.evaluate("() => window.scrollTo(0, 800)")
                page.wait_for_timeout(500)
                shot(run, page, out, "header-scrolled", clip=band)
                page.evaluate("() => window.scrollTo(0, 0)")
        if lead and vp.klass == "phone" and st.key == "cart_under":
            crop(run, page, out, "wholesale-card", "section[aria-labelledby]:has-text(\"" + STR["wholesale.away"] + "\")", 0)

        # last: it holds the track and could follow a slide link
        if st.key == "home" and vp.name == LEAD_PHONE:
            slider_timing(run, page)

        # last of all, on a FRESH page of the same context (same init script, same no-prod-writes
        # net): the sold-out order check presses "Show more" until the whole listing is rendered
        # (~180 cards). Nothing above may see that expanded page — the geometry checks and the
        # viewport / full-page shots were silently taken on it before (a ~30k px capture on a phone).
        if st.key in SOLDOUT_ROOTS:
            fresh = ctx.new_page()
            install_mocks(fresh, st.order_kind)
            try:
                fresh.goto(base + st.route, wait_until="domcontentloaded", timeout=40000)
                settle(fresh, st)
                soldout_order_check(run, fresh, SOLDOUT_ROOTS[st.key])
            finally:
                fresh.close()

    except PWError as exc:
        finding(run, "fail", "page-crash", "Playwright error: " + str(exc)[:300])
    except Exception as exc:  # noqa: BLE001
        finding(run, "fail", "harness-error", type(exc).__name__ + ": " + str(exc)[:300])
    finally:
        try:
            ctx.close()
        except PWError:
            pass
    report.add(run)


def run_opening(browser, vp: Viewport, base: str, out: Path, report: Report) -> None:
    """Plays on a fresh device, never on a reload or in a new tab within 7 days, static under reduced motion."""
    for mode in ("motion", "reduced"):
        st = State("opening", "/", "The opening moment · " + mode, kind="opening", reduced=(mode == "reduced"))
        run = Run(state="opening_" + mode, route="/", viewport=vp.name, label=st.label)
        ctx = new_context(browser, vp, st)
        page = ctx.new_page()
        net = Net()
        net.attach(page)
        install_mocks(page, "standard")
        try:
            t0 = time.time()
            page.goto(base + "/", wait_until="commit", timeout=40000)
            seen = []
            for ms in (150, 400, 700, 1300):
                left = ms / 1000 - (time.time() - t0)
                if left > 0:
                    page.wait_for_timeout(int(left * 1000))
                state = page.evaluate("() => { const s = document.querySelector('[data-splash]'); return s ? s.getAttribute('data-splash') : null; }")
                seen.append({"ms": ms, "splash": state})
                if ms in (150, 700, 1300):
                    shot(run, page, out, str(ms) + "ms")
            run.notes.append("splash samples: " + json.dumps(seen))
            values = [s["splash"] for s in seen]
            if not any(values):
                finding(run, "fail", "opening-plays", "the opening never appeared on a fresh session", seen)
            if mode == "reduced":
                if any(v == "motion" for v in values):
                    finding(run, "fail", "opening-reduced", "the animated opening played under reduced motion", seen)
                elif not any(v == "still" for v in values):
                    finding(run, "warn", "opening-reduced", "no static opening frame was sampled", seen)

            settle(page, st, "main")
            page.reload(wait_until="domcontentloaded", timeout=40000)
            page.wait_for_timeout(700)
            again = page.evaluate("() => { const s = document.querySelector('[data-splash]'); return s ? s.getAttribute('data-splash') : null; }")
            if again:
                finding(run, "fail", "opening-once", "the opening replayed on a reload in the same session (data-splash=" + again + ")")
            shot(run, page, out, "after-reload")

            # A new tab on the same device (every rep or WhatsApp link opens one) is a new session but
            # not a new week: no opening, and the served night frame (#yq-boot) is switched off by
            # catalog-prefetch.js before its first paint (web/src/market/lib/splashGate.ts).
            tab = ctx.new_page()
            install_mocks(tab, "standard")
            tab.goto(base + "/", wait_until="domcontentloaded", timeout=40000)
            boot = tab.evaluate("() => document.documentElement.getAttribute('data-boot')")
            tab.wait_for_timeout(700)
            tab_splash = tab.evaluate("() => { const s = document.querySelector('[data-splash]'); return s ? s.getAttribute('data-splash') : null; }")
            if tab_splash:
                finding(run, "fail", "opening-weekly", "the opening replayed in a new tab within 7 days (data-splash=" + tab_splash + ")")
            if boot != "off":
                finding(run, "fail", "opening-weekly", "the night boot frame was left on for a new tab within 7 days (data-boot=" + str(boot) + ")")
            tab.close()
            for msg in net.errors:
                finding(run, "fail", "page-error", msg)
            for msg in net.console:
                finding(run, "fail", "console-error", msg)
        except PWError as exc:
            finding(run, "fail", "page-crash", "Playwright error: " + str(exc)[:300])
        finally:
            try:
                ctx.close()
            except PWError:
                pass
        report.add(run)


# ── setup helpers ──────────────────────────────────────────────────────────────────────────────


def verify_strings(repo: Path) -> list[str]:
    """The harness checks copy; this checks the harness's copy of that copy."""
    f = repo / "web" / "src" / "market" / "strings.ts"
    if not f.exists():
        return ["strings.ts not found at " + str(f) + " — copy literals were not verified"]
    src = f.read_text(encoding="utf-8")
    drift = []
    for key, value in STR.items():
        needle = value.replace("'", "’") if "'" in value else value
        if value not in src and needle not in src:
            drift.append("strings.ts no longer contains “" + value + "” (" + key + ")")
    return drift


# A slide subhead gets two lines of ~163 px (one line under 300 px, where it is hidden instead), so
# anything past this ends in an ellipsis mid-sentence — and slides.dropsLine is the honesty claim
# that the old price is on every card. The count is substituted at its catalog-sized worst case.
SLIDE_LINE_MAX = 34
SLIDE_LINE_N = 179


def verify_copy_length(repo: Path) -> list[str]:
    """Slide subheads must fit the card at 320 px without being cut mid-sentence."""
    f = repo / "web" / "src" / "market" / "strings.ts"
    if not f.exists():
        return []
    block = re.search(r"\n  slides: \{(.+?)\n  \},", f.read_text(encoding="utf-8"), re.S)
    if not block:
        return ["strings.ts: the slides namespace was not found — slide copy length was not checked"]
    long: list[str] = []
    for key, raw in re.findall(r"^\s*(\w*Line)\s*:\s*(.+?),\s*$", block.group(1), re.M):
        text = re.sub(r"^\([^)]*\)\s*=>\s*", "", raw.strip()).strip("`'\"")
        text = re.sub(r"\$\{plural\(n, '[^']*', '([^']*)'\)\}", str(SLIDE_LINE_N) + r" \1", text)
        text = re.sub(r"\$\{[^}]*\}", str(SLIDE_LINE_N), text)
        if len(text) > SLIDE_LINE_MAX:
            long.append("strings.ts slides." + key + " is " + str(len(text)) + " characters (max " + str(SLIDE_LINE_MAX) + "): “" + text + "” — it would ellipse mid-sentence on a phone slide")
    return long


def find_slug(api: str) -> str | None:
    try:
        with urllib.request.urlopen(api + "/public/market", timeout=10) as r:
            data = json.load(r)
    except Exception:  # noqa: BLE001
        return None
    # R3: the public payload lists pickable reps as id + name only (no referral codes). A rep's
    # slug defaults to the slugified first name (app.shop.slugify), so try that; an older API
    # still carries referral_code and is used as before. Every candidate is verified via ?ref=.
    for s in data.get("salesmen") or []:
        first = str(s.get("name") or "").strip().split(" ")[0].lower()
        guess = re.sub(r"[^a-z0-9]+", "-", first).strip("-")
        for code in ((s.get("referral_code") or "").strip().lower(), guess):
            if not code:
                continue
            try:
                with urllib.request.urlopen(api + "/public/market?ref=" + code, timeout=10) as r2:
                    if (json.load(r2) or {}).get("ref"):
                        return code
            except Exception:  # noqa: BLE001
                continue
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="YQ Marketplace browser QA harness")
    ap.add_argument("--base", default="http://localhost:5174", help="the preview or production origin")
    ap.add_argument("--api", default="http://127.0.0.1:8001", help="the API the preview talks to (used to find a rep slug)")
    ap.add_argument("--out", required=True, help="output directory for results.json, index.md and the PNGs")
    ap.add_argument("--only", default="", help="comma-separated state keys (see results.json / README)")
    ap.add_argument("--quick", action="store_true", help="only the lead viewports, 430x932 and 1366x768")
    ap.add_argument("--list", action="store_true", help="print the states and exit")
    ap.add_argument("--reduced-motion", action="store_true", help="run every state under prefers-reduced-motion (stable frames for before/after reviews)")
    args = ap.parse_args()
    global REDUCE_ALL
    REDUCE_ALL = args.reduced_motion

    base = args.base.rstrip("/")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[2]

    slug = find_slug(args.api.rstrip("/"))
    all_states = states(slug)
    if args.list:
        for s in all_states:
            print(s.key.ljust(18), s.route.ljust(22), s.label)
        return 0

    only = {k.strip() for k in args.only.split(",") if k.strip()}
    picked = [s for s in all_states if not only or s.key in only]
    if only:
        unknown = only - {s.key for s in all_states}
        if unknown:
            print("unknown state(s): " + ", ".join(sorted(unknown)), file=sys.stderr)
            return 2
    viewports = [v for v in VIEWPORTS if not args.quick or v.name in QUICK_VIEWPORTS]

    report = Report(out, base)
    setup = Run(state="setup", route="-", viewport="-", label="harness setup")
    if not slug:
        finding(setup, "warn", "storefront", "no rep slug answered /public/market?ref=… — the storefront state was skipped")
    for msg in verify_strings(repo):
        finding(setup, "warn", "copy-drift", msg)
    for msg in verify_copy_length(repo):
        finding(setup, "fail", "copy-length", msg)
    report.add(setup)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for vp in viewports:
                lead = vp.name in (LEAD_PHONE, LEAD_DESKTOP)
                for st in picked:
                    if vp.klass not in st.classes:
                        continue
                    if st.lead_only and not lead:
                        continue
                    if st.kind == "opening":
                        run_opening(browser, vp, base, out, report)
                        continue
                    print("· " + vp.name.ljust(9) + " " + st.key.ljust(16) + " " + st.route, flush=True)
                    run_state(browser, vp, st, base, out, report, lead)
        finally:
            browser.close()

    report.write()
    print("")
    print("runs " + str(len(report.runs)) + " · shots " + str(sum(len(r.shots) for r in report.runs)))
    print("HARD FAILURES: " + str(len(report.fails)) + " · warnings: " + str(len(report.warns)))
    for f in report.fails[:40]:
        print("  FAIL " + f["check"] + " · " + f["viewport"] + " · " + f["route"] + " · " + f["detail"][:160])
    print("wrote " + str(out / "results.json") + " and " + str(out / "index.md"))
    return 1 if report.fails else 0


if __name__ == "__main__":
    sys.exit(main())
