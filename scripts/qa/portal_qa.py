"""
YQ Ops Portal — browser QA harness (Python Playwright, Chromium): record once, replay anywhere.

Every portal screen (the office AppShell and the salesman app) sits behind a sign-in, so this
harness works from a RECORDING. The owner signs in ONCE in a window it opens; it walks every page a
signed-in admin can open and saves what each page receives from the API. Every later run REPLAYS
that recording against a local build with a fake session — no login, no network — as often as
needed: before/after design reviews, failure injection, smoothness timings.

  record    python scripts/qa/portal_qa.py --record --base https://ops.yqmarketplace.com \
                --api https://api.yqmarketplace.com --out business_data/design_review/<date>/portal [--rep "<Focus name>"]
  replay    python scripts/qa/portal_qa.py --replay --dist web/dist --rec business_data/design_review/<date>/portal \
                --out business_data/design_review/<date>/after [--as admin|salesman] [--widths 390,1024,1440] \
                [--themes light,dark] [--only receivables,margins] [--port 5175] [--fail-api]
  selftest  python scripts/qa/portal_qa.py --selftest        (the record mode's write guard, then the committed
                                                              SYNTHETIC fixture against web/dist)
  pages     python scripts/qa/portal_qa.py --list

Exit: replay and selftest exit 1 when a hard check fails (warnings never fail a run); record exits 0
when the walk finished. In Git Bash prefix with MSYS_NO_PATHCONV=1. See scripts/qa/README.md,
"Portal harness".

Safety
  record  A headed Chrome on a NON-persistent context (a throw-away profile): nothing about the
          session is saved — no storage_state, no cookies, no tokens — and the token never leaves the
          browser (--rep's extra reads are fetched inside the page). Only the API's GET answers (and
          Supabase storage / REST GETs: the product photos) are written, without any request
          header, under <out>/rec/, and <out> must be a git-ignored business_data/ folder. A signed
          URL's token and any JWT inside a recorded body are replaced with "qa-masked". Every request
          that is not a GET is answered inside the browser ({"ok": true, "qa": "not sent"}) — except
          Supabase's POST /auth/v1/token (the sign-in and the token refresh). Sign-out and password
          changes are never sent: a sign-out would end the owner's other sessions.
  replay  No network: the build is served by `vite preview` on localhost; API and Supabase calls are
          answered from the recording (a miss is answered 404 JSON and logged to misses.json, a photo
          miss gets a grey placeholder); every write is answered locally; the session is a fake one
          that is valid nowhere; the page clock reads the recording's date (--clock real to opt out).
"""

from __future__ import annotations

import argparse
import atexit
import base64
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
import re
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

from playwright.sync_api import Error as PWError
from playwright.sync_api import Page, Route, sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WEB = ROOT / "web"
FIXTURE = HERE / "fixtures" / "portal_min"
REC_FORMAT = "yq-portal-rec/1"
DEFAULT_PORT = 5175

# scripts/qa/smooth.py (the market's smoothness checks, S1-S6) when this checkout has it: its
# generic scroll_jank (S1) and back_scroll (S4) measure a page whose DOCUMENT scrolls. Loaded by
# path, so an unrelated module called "smooth" can never stand in for it.
SMOOTH: Any = None
if (HERE / "smooth.py").exists():
    try:
        _spec = importlib.util.spec_from_file_location("yq_qa_smooth", HERE / "smooth.py")
        if _spec and _spec.loader:
            SMOOTH = importlib.util.module_from_spec(_spec)
            _spec.loader.exec_module(SMOOTH)
    except Exception:  # noqa: BLE001 — a broken smooth.py only costs its two checks
        SMOOTH = None

# ── the fake session ───────────────────────────────────────────────────────────────────────────
# The perf_auth_check.py approach: a Supabase session in localStorage gets the SPA past "am I
# signed in", and the last good /me (yq-me-v1) paints the shell at once. Valid nowhere.
UID = "00000000-0000-4000-8000-00000000qa01"
DEFAULT_ME = {"email": "qa-admin@example.invalid", "role": "admin", "features": [], "full_name": "QA Admin",
              "must_reset": False, "read_only": False}
# app/features.py ROLE_DEFAULT_FEATURES["salesman"]: the salesman app + Spotted
SALESMAN_FEATURES = ["Catalog", "Shop Orders", "Market Intel"]

# ── what the checks look for ───────────────────────────────────────────────────────────────────
# S10: copy that claims "nothing here" — a lie when the data simply did not load.
FALSE_EMPTY = ("No orders yet", "Everything ordered has been received", "No transfers in this window")
# S10: an honest error. The brief's words plus the portal's own LoadError / PickList wording
# ("Could not load …", "Server error (ref …). Please retry.").
AFFORDANCE_TEXT = r"couldn.?t load|could not load|didn.?t load|did not load|try again|retry|something went wrong|server error"
AFFORDANCE_BUTTON = r"^(retry|try again|reload)\b"
# S8: a loader is a spinner (lucide Loader2 .animate-spin), a skeleton (components/ui/skeleton:
# .animate-pulse) or anything marked busy
LOADERS = '.animate-spin, .animate-pulse, [aria-busy="true"], .skeleton'
# --fail-api never fails these: they are the shell (who am I, the warm-up ping), not the page
SHELL_CALLS = ("/me", "/health")
# GET routes that take ?rep= for an admin (app/shop_api.py shop_me_followups): --rep records them
# as that rep and files them under the plain key for a --as salesman replay
REP_ENDPOINTS = ("/shop/me/followups",)
# query parameters that never decide the answer: a signed URL's token, cache busters
VOLATILE_PARAMS = {"token", "_", "cb"}
# what a recorded body may never keep (masked before it is written)
MASKS = (
    (re.compile(r"([?&](?:token|access_token|refresh_token|apikey|X-Amz-Signature)=)[^&\"'\s<>]+"), r"\1qa-masked"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "qa-masked-jwt"),
)
PLACEHOLDER_SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="320" viewBox="0 0 320 320">'
                   b'<rect width="320" height="320" fill="#e9e6f0"/></svg>')

# thresholds
NAV_MS_WARN = 150        # S8: click to the new heading
NAV_LOADERS_WARN = 1     # S8: more loaders than this between click and paint
CHROME_PX_WARN = 200     # S12
INNER_SCROLL_PHONE_LEVEL = "warn"   # S9 on a touch phone


# ── pages ──────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PortalPage:
    key: str
    route: str                     # a pattern ("/orders/:poNo") for a detail page
    label: str
    roles: tuple[str, ...] = ("admin",)
    detail_from: str | None = None  # --record opens the first link matching `link` on this page
    link: str | None = None
    back: str | None = None        # S4: an in-page link to follow and come Back from


# Every route a signed-in admin can open (web/src/App.tsx, inside <ProtectedRoute>), in the order of
# the office menu (web/src/lib/nav.ts NAV), then the pages the menu does not list. verify_routes()
# re-reads both files and warns when a route is missing here or no longer exists there. The salesman
# app's five tabs (SalesmanShell: Today · Catalog · Orders · Shops · Me) plus Market Intel, which the
# salesman default grants, carry the "salesman" role.
PAGES: list[PortalPage] = [
    PortalPage("command", "/command", "Command Centre"),
    PortalPage("shop_orders", "/shop-orders", "Customer orders (salesman: Orders)", ("admin", "salesman")),
    PortalPage("picklist", "/picklist", "Pick list"),
    PortalPage("shop", "/shop", "Order for a shop (salesman: Catalog)", ("admin", "salesman")),
    PortalPage("catalog", "/catalog", "Catalog"),
    PortalPage("upcoming", "/upcoming", "Coming soon"),
    PortalPage("inventory", "/inventory", "Inventory"),
    PortalPage("stock", "/stock", "Stock moves"),
    PortalPage("orders", "/orders", "Purchase orders", back=r"^/orders/[^/?#]+$"),
    PortalPage("order_detail", "/orders/:poNo", "Purchase order (the first on the list)", detail_from="orders", link=r"^/orders/[^/?#]+$"),
    PortalPage("margins", "/margins", "Profitability"),
    PortalPage("prices", "/prices", "Price tracker"),
    PortalPage("receivables", "/receivables", "Receivables"),
    PortalPage("command_team", "/command/team", "Rep performance"),
    PortalPage("salesmen", "/salesmen", "Reps & statements"),
    PortalPage("market_intel", "/market-intel", "Market Intel · board", ("admin", "salesman")),
    PortalPage("mi_review", "/market-intel?tab=review", "Market Intel · review"),
    PortalPage("mi_cases", "/market-intel?tab=cases", "Market Intel · cases", back=r"^/market-intel/cases/\d+"),
    PortalPage("mi_case", "/market-intel/cases/:id", "Market Intel · a case (the first listed)", detail_from="mi_cases", link=r"^/market-intel/cases/\d+$"),
    PortalPage("mi_library", "/market-intel?tab=library", "Market Intel · library"),
    PortalPage("mi_lists", "/market-intel?tab=lists", "Market Intel · lists"),
    PortalPage("mi_reports", "/market-intel?tab=reports", "Market Intel · supplier reports"),
    PortalPage("shop_rules", "/shop-rules", "Offers & rules"),
    PortalPage("dashboard", "/", "Dashboard"),
    PortalPage("sales", "/sales", "Sales"),
    PortalPage("shop_analytics", "/shop-analytics", "Shop analytics", back=r"^/shop-analytics/merchant/[^/?#]+$"),
    PortalPage("merchant", "/shop-analytics/merchant/:id", "Merchant profile (the first listed)", detail_from="shop_analytics", link=r"^/shop-analytics/merchant/[^/?#]+$"),
    PortalPage("data", "/data", "Data upload"),
    PortalPage("team", "/team", "Team & access"),
    PortalPage("assistant", "/assistant", "AI Assistant"),
    PortalPage("agents", "/agents", "AI Agents"),
    PortalPage("feed", "/feed", "Live Feed"),
    PortalPage("leads", "/leads", "Leads"),
    PortalPage("marketing", "/marketing", "Marketing"),
    PortalPage("coaching", "/coaching", "Coach"),
    PortalPage("command_customers", "/command/customers", "Command Centre · customers"),
    PortalPage("finds", "/finds", "Product Finds (off the menu since R7b)"),
    PortalPage("field_notes", "/field-notes", "Field notes (off the menu since R7b)"),
    PortalPage("today", "/today", "Today (the salesman's home)", ("admin", "salesman")),
    PortalPage("customers", "/customers", "Shops (the salesman's book)", ("admin", "salesman")),
    PortalPage("account", "/account", "My account (the salesman's Me)", ("admin", "salesman")),
    PortalPage("settings", "/settings", "Settings / profile"),
]
# the salesman app's tab order (SalesmanShell: Today · Catalog · Orders · Shops · Me), then More
SALESMAN_ORDER = ("today", "shop", "shop_orders", "customers", "account", "market_intel")

# --selftest only: two pages the harness serves itself, so the S10 detector is proven on a page
# that MUST fail (false-empty copy, no error) and one that must pass (an honest error + Retry)
CANARIES: dict[str, tuple[str, str]] = {
    "canary_false_empty": ("/__qa/false-empty", "<main><h1>QA canary</h1><p>No orders yet</p></main>"),
    "canary_error_ok": ("/__qa/error-ok", "<main><h1>QA canary</h1><div role=\"alert\">Couldn't load this page."
                        " <button type=\"button\">Retry</button></div></main>"),
}


def pages_for(role: str) -> list[PortalPage]:
    out = [p for p in PAGES if role in p.roles]
    if role == "salesman":
        out.sort(key=lambda p: SALESMAN_ORDER.index(p.key) if p.key in SALESMAN_ORDER else 99)
    return out


def verify_routes(repo: Path) -> list[str]:
    """The harness walks routes; this checks its list against App.tsx and the office menu."""
    app = repo / "web" / "src" / "App.tsx"
    nav = repo / "web" / "src" / "lib" / "nav.ts"
    if not app.exists():
        return ["App.tsx not found at " + str(app) + " — the route list was not verified"]
    src = app.read_text(encoding="utf-8")
    block = src.split("<ProtectedRoute />", 1)[-1]
    found = set(re.findall(r'<Route\s+path="([^"]+)"', block))
    if re.search(r"<Route\s+index\b", block):
        found.add("")
    app_routes = {"/" + r for r in found if r != "*"}
    walked = {re.sub(r"\?.*$", "", p.route) for p in PAGES}
    drift = []
    for r in sorted(app_routes - walked):
        drift.append("App.tsx has the route " + r + " and the harness does not walk it (add it to PAGES)")
    for r in sorted(walked - app_routes):
        drift.append("the harness walks " + r + " but App.tsx no longer has it")
    if nav.exists():
        for to in sorted(set(re.findall(r"to:\s*'([^']+)'", nav.read_text(encoding="utf-8"))) - walked):
            drift.append("lib/nav.ts links " + to + " and the harness does not walk it")
    return drift


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


def viewport_for(width: int) -> Viewport:
    """Under 768 a touch phone (DPR 2, 844 tall, like 390x844); otherwise a desktop window."""
    if width < 768:
        return Viewport(str(width) + "x844", width, 844, "phone", 2.0, True, True)
    h = {768: 1024, 1024: 768, 1280: 800, 1366: 768, 1440: 900, 1536: 864, 1920: 1080}.get(width, max(700, round(width * 0.625)))
    return Viewport(str(width) + "x" + str(h), width, h, "tablet" if width < 1024 else "desktop")


# ── findings ───────────────────────────────────────────────────────────────────────────────────


@dataclass
class Run:
    page: str
    route: str
    viewport: str
    theme: str
    label: str
    findings: list[dict] = field(default_factory=list)
    shots: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


def finding(run: Run, level: str, check: str, detail: str, values: Any = None) -> None:
    run.findings.append({"level": level, "check": check, "page": run.page, "route": run.route,
                         "viewport": run.viewport, "theme": run.theme, "detail": detail, "values": values})


def keep(run: Run, results: list[tuple[str, str, str, Any]]) -> None:
    """smooth.py-style (level, check, detail, values): a note is a run note, the rest findings."""
    for level, check, detail, values in results:
        if level == "note":
            run.notes.append(detail)
            run.metrics.setdefault(check, values)
        else:
            finding(run, level, check, detail, values)


class Report:
    def __init__(self, out: Path, header: dict) -> None:
        self.out = out
        self.header = header
        self.runs: list[Run] = []
        self.fails: list[dict] = []
        self.warns: list[dict] = []
        self.nav: list[dict] = []
        self.paint: list[dict] = []
        self.misses: list[dict] = []
        self.started = time.time()

    def add(self, run: Run) -> None:
        self.runs.append(run)
        for f in run.findings:
            (self.fails if f["level"] == "fail" else self.warns).append(f)

    def write(self) -> None:
        shots = [s for r in self.runs for s in r.shots]
        data = {
            **self.header,
            "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.started)),
            "seconds": round(time.time() - self.started, 1),
            "runs": len(self.runs),
            "shots": len(shots),
            "misses": len([m for m in self.misses if m["kind"] in ("miss", "near")]),
            "hard_failures": self.fails,
            "warnings": self.warns,
            "nav": self.nav,
            "first_paint": self.paint,
            "pages": [{"page": r.page, "route": r.route, "viewport": r.viewport, "theme": r.theme, "label": r.label,
                       "findings": r.findings, "shots": r.shots, "notes": r.notes, "metrics": r.metrics} for r in self.runs],
        }
        (self.out / "results.json").write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
        (self.out / "misses.json").write_text(json.dumps(self.misses, indent=1), encoding="utf-8")
        self._index(shots)

    def _index(self, shots: list[dict]) -> None:
        h = self.header
        lines = [
            "# YQ Ops Portal — QA run (" + str(h.get("mode")) + ")",
            "",
            "- build: `" + str(h.get("dist")) + "` · recording: `" + str(h.get("rec")) + "` (" + str(h.get("recorded_at")) + ("; SYNTHETIC" if h.get("synthetic") else "") + ")",
            "- as: **" + str(h.get("as")) + "** · widths: " + ", ".join(str(w) for w in h.get("widths", [])) + " · themes: " + ", ".join(h.get("themes", []))
            + (" · **--fail-api** (every API call of the page answers 500)" if h.get("fail_api") else ""),
            "- started: " + time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.started)),
            "- pages walked: " + str(len([r for r in self.runs if r.page not in ("summary", "setup", "nav")])) + " · screenshots: " + str(len(shots)),
            "- hard failures: **" + str(len(self.fails)) + "** · warnings: " + str(len(self.warns))
            + " · misses: " + str(len([m for m in self.misses if m["kind"] in ("miss", "near")])) + " (misses.json)",
            "",
        ]
        if self.fails:
            lines += ["## Hard failures", ""]
            for f in self.fails:
                lines.append("- **" + f["check"] + "** · `" + f["route"] + "` · " + f["viewport"] + " · " + f["theme"] + " · " + f["detail"])
            lines.append("")
        if self.warns:
            lines += ["## Warnings", ""]
            for f in self.warns:
                lines.append("- " + f["check"] + " · `" + f["route"] + "` · " + f["viewport"] + " · " + f["theme"] + " · " + f["detail"])
            lines.append("")
        if self.nav:
            lines += ["## Click to paint (S8)", "", "API answers are instant here, so this is the app's own cost: the click, the exit animation, the page's code, its render.", "",
                      "| width | from → to | paint ms | settled ms | loaders | kinds |", "|---|---|---|---|---|---|"]
            for n in self.nav:
                lines.append("| " + str(n["width"]) + " | " + n["from"] + " → " + n["to"] + " | " + str(n.get("paint_ms")) + " | "
                             + str(n.get("settled_ms")) + " | " + str(n.get("loaders")) + " | " + ", ".join(n.get("kinds") or []) + " |")
            lines.append("")
        if self.paint:
            lines += ["## First paint (S7)", "", "| width | theme | page | white frames before the app | for ms | app painted at ms | app background |", "|---|---|---|---|---|---|---|"]
            for p in self.paint:
                lines.append("| " + str(p["width"]) + " | " + p["theme"] + " | " + p["page"] + " | " + str(p["white_frames"]) + " | "
                             + str(p.get("white_ms")) + " | " + str(p.get("app_paint_ms")) + " | " + str(p.get("app_bg")) + " |")
            lines.append("")
        scroll = [(r.page, r.viewport, r.metrics.get("scroller")) for r in self.runs if r.metrics.get("scroller")]
        if scroll:
            lines += ["## Who scrolls (S9)", "", "| page | viewport | scroller |", "|---|---|---|"]
            for page, vp, s in scroll:
                lines.append("| " + page + " | " + vp + " | " + str(s) + " |")
            lines.append("")
        misses = [m for m in self.misses if m["kind"] in ("miss", "near")]
        if misses:
            lines += ["## Misses (not in the recording)", "", "| request | kind | pages | count |", "|---|---|---|---|"]
            for m in misses[:40]:
                lines.append("| `" + m["key"] + "` | " + m["kind"] + (" → `" + m["served"] + "`" if m.get("served") else "") + " | "
                             + ", ".join(m["pages"][:4]) + " | " + str(m["count"]) + " |")
            if len(misses) > 40:
                lines.append("| … " + str(len(misses) - 40) + " more in misses.json | | | |")
            lines.append("")
        lines += ["## Screenshots", "", "| screenshot | page | route | width | theme |", "|---|---|---|---|---|"]
        for s in shots:
            lines.append("| `" + s["path"] + "` | " + s["page"] + " | `" + s["route"] + "` | " + str(s["width"]) + " | " + s["theme"] + " |")
        lines.append("")
        (self.out / "index.md").write_text("\n".join(lines), encoding="utf-8")


# ── the recording ──────────────────────────────────────────────────────────────────────────────


def rec_key(method: str, path: str, query: str, kind: str = "api") -> str:
    """METHOD [kind:]path?sorted-query — the volatile parameters left out."""
    q = sorted((k, v) for k, v in parse_qsl(query, keep_blank_values=True) if k not in VOLATILE_PARAMS)
    prefix = "" if kind == "api" else kind + ":"
    return method.upper() + " " + prefix + (path or "/") + ("?" + urlencode(q) if q else "")


def _split_key(key: str) -> tuple[str, str, dict[str, str]]:
    method, rest = key.split(" ", 1)
    path, _, query = rest.partition("?")
    return method, path, dict(parse_qsl(query, keep_blank_values=True))


def ext_for(ctype: str) -> str:
    c = (ctype or "").split(";")[0].strip().lower()
    return {"application/json": ".json", "image/webp": ".webp", "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
            "image/svg+xml": ".svg", "text/html": ".html", "text/plain": ".txt", "text/csv": ".csv", "application/pdf": ".pdf"}.get(c, ".bin")


def mask_body(body: bytes, ctype: str) -> tuple[bytes, int]:
    """Signed-URL tokens and JWTs out of a text body; binary bodies pass untouched."""
    c = (ctype or "").lower()
    if not any(t in c for t in ("json", "text", "javascript", "xml", "html", "csv")):
        return body, 0
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return body, 0
    n = 0
    for rx, repl in MASKS:
        text, k = rx.subn(repl, text)
        n += k
    return text.encode("utf-8"), n


class Recording:
    """<dir>/rec/index.json + one body file per answer (api/…, supabase/…)."""

    def __init__(self, root: Path, index: dict) -> None:
        self.root = root
        self.index = index
        self._cache: dict[str, bytes] = {}

    @classmethod
    def open(cls, folder: Path) -> "Recording":
        root = folder / "rec" if (folder / "rec" / "index.json").exists() or folder.name != "rec" else folder
        idx = root / "index.json"
        if not idx.exists():
            raise SystemExit("no recording at " + str(idx) + " (pass the folder that holds rec/)")
        data = json.loads(idx.read_text(encoding="utf-8"))
        if data.get("format") != REC_FORMAT:
            raise SystemExit("unknown recording format " + str(data.get("format")) + " in " + str(idx))
        return cls(root, data)

    @classmethod
    def create(cls, root: Path, base: str, api: str) -> "Recording":
        if (root / "index.json").exists():
            rec = cls.open(root.parent)
            rec.index.setdefault("notes", []).append("merged by a later --record at " + dt.datetime.now().isoformat(timespec="seconds"))
            return rec
        now = dt.datetime.now().astimezone()
        return cls(root, {"format": REC_FORMAT, "synthetic": False, "recorded_at": now.isoformat(timespec="seconds"),
                          "base": base, "api": api, "supabase_host": None,
                          "signed_in_role": None, "rep": None, "pages": [], "entries": {}, "as": {"salesman": {}},
                          "blocked_writes": [], "notes": []})

    def recorded_ms(self) -> int | None:
        """The recording's instant (its ISO recorded_at), for the replay's page clock."""
        try:
            return int(dt.datetime.fromisoformat(str(self.index.get("recorded_at"))).timestamp() * 1000)
        except ValueError:
            return None

    @property
    def entries(self) -> dict[str, dict]:
        return self.index["entries"]

    def put(self, key: str, kind: str, status: int, ctype: str, body: bytes) -> tuple[dict, int]:
        body, masked = mask_body(body, ctype)
        slug = re.sub(r"[^A-Za-z0-9]+", "_", key.split(" ", 1)[1].split("?")[0]).strip("_")[:60] or "root"
        name = kind + "/" + slug + "-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:10] + ext_for(ctype)
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        entry = {"file": name, "status": status, "content_type": ctype, "bytes": len(body), "origin": kind}
        self.entries[key] = entry
        self._cache.pop(name, None)
        return entry, masked

    def body(self, entry: dict) -> bytes:
        name = entry["file"]
        if name not in self._cache:
            self._cache[name] = (self.root / name).read_bytes()
        return self._cache[name]

    def json_of(self, key: str) -> Any:
        e = self.entries.get(key)
        if not e:
            return None
        try:
            return json.loads(self.body(e).decode("utf-8"))
        except (ValueError, OSError):
            return None

    def lookup(self, key: str, role: str) -> tuple[dict | None, str, str | None]:
        """(entry, 'hit' | 'near' | 'miss', the key served). The role's overlay wins (--rep); a miss
        falls back to the same path with the closest query (a date window recorded on another day)."""
        overlay = self.index.get("as", {}).get(role) or {}
        if key in overlay:
            return overlay[key], "hit", key
        if key in self.entries:
            return self.entries[key], "hit", key
        method, path, query = _split_key(key)
        best, best_score = None, -1
        for k in list(overlay) + list(self.entries):
            m2, p2, q2 = _split_key(k)
            if m2 != method or p2 != path:
                continue
            score = sum(1 for n, v in query.items() if q2.get(n) == v) * 2 + sum(1 for n in query if n in q2) - abs(len(q2) - len(query))
            if score > best_score:
                best, best_score = k, score
        if best is not None:
            return (overlay.get(best) or self.entries[best]), "near", best
        return None, "miss", None

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "index.json").write_text(json.dumps(self.index, indent=1, sort_keys=False), encoding="utf-8")


def require_private(out: Path) -> str | None:
    """None when real data may be written to `out`; otherwise why not. It must sit in a
    business_data/ folder and, inside a Git work tree, Git must ignore it."""
    out = out.resolve()
    if "business_data" not in [p.lower() for p in out.parts]:
        return str(out) + " is not inside a business_data/ folder (real data is written only there; it is git-ignored)"
    probe = out
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        top = subprocess.run(["git", "-C", str(probe), "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=20)
        if top.returncode == 0:
            ign = subprocess.run(["git", "-C", top.stdout.strip(), "check-ignore", "-q", str(out)], capture_output=True, timeout=20)
            if ign.returncode != 0:
                return str(out) + " is inside a Git work tree and Git does NOT ignore it — refusing to write real data there"
    except (OSError, subprocess.SubprocessError):
        pass
    return None


# ── record ─────────────────────────────────────────────────────────────────────────────────────

SCROLL_TO_BOTTOM = r"""
async () => {
  /* the page's scroller: the document, else the biggest overflow:auto box (the office shell's <main>) */
  const doc = document.scrollingElement || document.documentElement;
  let el = null;
  let best = 0;
  for (const c of document.querySelectorAll('body *')) {
    if (c.scrollHeight <= c.clientHeight + 1) continue;
    if (!/(auto|scroll|overlay)/.test(getComputedStyle(c).overflowY)) continue;
    const r = c.getBoundingClientRect();
    /* the page's own scroller is wide (the S9 rule): never the sidebar's menu */
    if (r.width < innerWidth * 0.5 || r.height < innerHeight * 0.4) continue;
    if (r.width * r.height > best) { best = r.width * r.height; el = c; }
  }
  const box = doc.scrollHeight > innerHeight + 1 && (!el || doc.scrollHeight >= el.scrollHeight) ? null : el;
  const get = () => (box ? box.scrollTop : window.scrollY);
  const set = (y) => (box ? (box.scrollTop = y) : window.scrollTo(0, y));
  const max = () => (box ? box.scrollHeight - box.clientHeight : doc.scrollHeight - innerHeight);
  for (let i = 0; i < 40 && get() < max() - 2; i++) {
    set(get() + Math.round((box ? box.clientHeight : innerHeight) * 0.8));
    await new Promise((r) => setTimeout(r, 220));
  }
  await new Promise((r) => setTimeout(r, 300));
  const reached = get();
  set(0);
  return { scroller: box ? box.tagName.toLowerCase() : 'document', reached, max: max() };
}
"""

FIRST_LINK = r"""
(pattern) => {
  const re = new RegExp(pattern);
  const vis = (a) => { const r = a.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const a = [...document.querySelectorAll('main a[href], a[href]')].find((x) => re.test(x.getAttribute('href') || '') && vis(x));
  return a ? a.getAttribute('href') : null;
}
"""

# --rep: one GET as that rep, fetched INSIDE the page so the token never reaches Python
IN_PAGE_GET = r"""
async (url) => {
  let tok = null;
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (!k || !/^sb-.+-auth-token$/.test(k)) continue;
      const s = JSON.parse(localStorage.getItem(k) || 'null');
      tok = (s && (s.access_token || (s.currentSession && s.currentSession.access_token))) || null;
    }
  } catch (e) { /* storage blocked */ }
  try {
    const r = await fetch(url, { headers: tok ? { Authorization: 'Bearer ' + tok } : {} });
    return { status: r.status };
  } catch (e) {
    return { status: 0, error: String(e).slice(0, 120) };
  }
}
"""


class Recorder:
    """The record mode's one route: GETs to the API (and Supabase REST/storage) are fetched, saved
    and handed back; every other non-GET is answered here; Supabase's sign-in and refresh pass."""

    def __init__(self, rec: Recording, api: str, site: str) -> None:
        self.rec = rec
        self.api = api.rstrip("/")
        self.api_origin = _origin(self.api)
        self.api_path = urlsplit(self.api).path.rstrip("/")
        self.site_origin = _origin(site)
        self.current = "sign-in"
        self.recording = False       # nothing is written before the sign-in is confirmed
        self.per_page: dict[str, list[str]] = {}
        self.blocked: list[dict] = []
        self.inflight = 0
        self.saved = 0
        self.saved_bytes = 0
        self.masked = 0
        self.throttled = 0
        self.errors: list[str] = []
        self.me: dict | None = None
        self.supabase_host: str | None = None

    def kind(self, origin: str) -> str:
        if origin == self.api_origin:
            return "api"
        if re.match(r"^https://[a-z0-9]+\.supabase\.co$", origin):
            self.supabase_host = urlsplit(origin).hostname
            return "supabase"
        return "site" if origin == self.site_origin else "other"

    def block(self, route: Route, why: str) -> None:
        req = route.request
        self.blocked.append({"method": req.method, "path": urlsplit(req.url).path, "page": self.current, "why": why})
        if req.method == "OPTIONS":
            route.fulfill(status=204, headers=_cors(req), body="")
            return
        route.fulfill(status=200, headers=_cors(req), content_type="application/json", body=json.dumps({"ok": True, "qa": "not sent"}))

    def on_route(self, route: Route) -> None:
        req = route.request
        u = urlsplit(req.url)
        kind = self.kind(_origin(req.url))
        method = req.method.upper()
        try:
            if kind == "supabase" and u.path.startswith("/auth/v1/"):
                # the session itself: never recorded. Only the sign-in / refresh POST is sent; a
                # sign-out (it would end the owner's other sessions) or a password change never is.
                if method == "GET" or (method == "POST" and u.path == "/auth/v1/token"):
                    route.continue_()
                else:
                    self.block(route, "supabase auth write")
                return
            if method in ("GET", "HEAD"):
                if kind == "api" or (kind == "supabase" and u.path.startswith(("/rest/v1/", "/storage/v1/"))):
                    self.fetch_and_save(route, kind, u)
                else:
                    route.continue_()
                return
            self.block(route, "write")
        except Exception as exc:  # noqa: BLE001 — a GET may go on to the network; a write never does
            self.errors.append(method + " " + u.path + ": " + type(exc).__name__ + " " + str(exc)[:160])
            try:
                if method in ("GET", "HEAD"):
                    route.continue_()
                else:
                    route.abort()
            except Exception:  # noqa: BLE001 — already handled
                pass

    def fetch_and_save(self, route: Route, kind: str, u) -> None:
        req = route.request
        self.inflight += 1
        try:
            try:
                resp = route.fetch(timeout=90000)
            except PWError as exc:
                self.errors.append("fetch " + u.path + ": " + str(exc)[:160])
                route.continue_()
                return
            body = resp.body()
            ctype = resp.headers.get("content-type", "")
            path = u.path[len(self.api_path):] if kind == "api" and self.api_path and u.path.startswith(self.api_path) else u.path
            key = rec_key(req.method, path, u.query, kind)
            if kind == "api" and path == "/me" and resp.status == 200:
                try:
                    self.me = json.loads(body.decode("utf-8"))   # the sign-in signal; only its role is ever printed
                except ValueError:
                    pass
            if resp.status == 429:
                self.throttled += 1      # the API's rate limit (300/minute per user): the walk backs off
            good = self.rec.entries.get(key)
            keep_good = resp.status >= 400 and good is not None and int(good.get("status") or 0) < 400
            if self.recording and req.method.upper() == "GET" and resp.status != 304 and not keep_good:
                try:
                    _, masked = self.rec.put(key, kind, resp.status, ctype, body)
                except OSError as exc:   # a full or locked disk costs this answer, never the walk
                    self.errors.append("save " + key + ": " + str(exc)[:120])
                else:
                    self.saved += 1
                    self.saved_bytes += len(body)
                    self.masked += masked
                    self.per_page.setdefault(self.current, [])
                    if key not in self.per_page[self.current]:
                        self.per_page[self.current].append(key)
            route.fulfill(response=resp, body=body)
        finally:
            self.inflight -= 1


def _origin(url: str) -> str:
    u = urlsplit(url)
    return u.scheme + "://" + u.netloc


def _cors(req) -> dict[str, str]:
    origin = (req.headers or {}).get("origin") or "*"
    return {"access-control-allow-origin": origin, "access-control-allow-headers": "authorization, content-type, *",
            "access-control-allow-methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS", "access-control-expose-headers": "*",
            "cache-control": "no-store", "vary": "origin"}


def wait_quiet(page: Page, busy, quiet_ms: int = 900, max_ms: int = 25000) -> None:
    """Until nothing the recorder handles has been in flight for quiet_ms (bounded)."""
    t0 = time.time()
    calm_since = None
    while (time.time() - t0) * 1000 < max_ms:
        page.wait_for_timeout(120)
        if busy() == 0:
            calm_since = calm_since or time.time()
            if (time.time() - calm_since) * 1000 >= quiet_ms:
                return
        else:
            calm_since = None


def launch_headed(pw):
    try:
        return pw.chromium.launch(channel="chrome", headless=False)
    except PWError:
        print("(system Chrome not found: using Playwright's Chromium)", flush=True)
        return pw.chromium.launch(headless=False)


def record(args) -> int:
    base = args.base.rstrip("/")
    api = args.api.rstrip("/")
    out = Path(args.out).resolve()
    why = require_private(out)
    if why:
        print("refusing: " + why, file=sys.stderr)
        return 2
    rec = Recording.create(out / "rec", base, api)
    recorder = Recorder(rec, api, base)
    only = {k.strip() for k in (args.only or "").split(",") if k.strip()}
    wait_s = max(60, int(args.wait_min * 60))
    walked: list[dict] = []
    details: dict[str, str] = {}
    t_start = time.time()
    signed_in = False

    with sync_playwright() as pw:
        browser = launch_headed(pw)
        # NON-persistent: a throw-away profile, deleted when the context closes. Nothing about the
        # session is ever read out of it (no storage_state, no cookies, no tokens).
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, service_workers="block", locale="en-GB")
        ctx.route("**/*", recorder.on_route)
        page = ctx.new_page()
        try:
            page.goto(base + "/login", wait_until="domcontentloaded", timeout=90000)
            print("", flush=True)
            print("A Chrome window is open at " + base + "/login.", flush=True)
            print("Sign in there (up to " + str(wait_s // 60) + " minutes). Only the sign-in itself is sent; the walk starts by itself.", flush=True)
            deadline = time.time() + wait_s
            while time.time() < deadline:
                if page.is_closed():
                    print("the window was closed before sign-in — nothing was recorded", file=sys.stderr)
                    return 2
                path = urlsplit(page.url).path
                if recorder.me and recorder.me.get("role") and not path.startswith("/login"):
                    break
                page.wait_for_timeout(1000)
            else:
                print("no sign-in within " + str(wait_s // 60) + " minutes — nothing was recorded", file=sys.stderr)
                return 2
            signed_in = True
            recorder.recording = True
            role = str(recorder.me.get("role"))
            rec.index["signed_in_role"] = role
            print("signed in (role: " + role + ")" + ("" if role == "admin" else " — not an admin: pages it may not open record their 'no access' screen"), flush=True)
            wait_quiet(page, lambda: recorder.inflight, 1500, 30000)

            todo = [p for p in PAGES if not only or p.key in only]
            print("walking " + str(len(todo)) + " pages — please leave the window alone until it closes", flush=True)
            for pg in todo:
                route = pg.route
                if pg.detail_from:
                    if pg.key not in details:
                        walked.append({"key": pg.key, "route": pg.route, "status": "skipped: no link on " + str(pg.detail_from)})
                        print("· " + pg.key.ljust(18) + " skipped (no link on " + str(pg.detail_from) + ")", flush=True)
                        continue
                    route = details[pg.key]
                if page.is_closed():
                    raise RuntimeError("the window was closed during the walk")
                recorder.current = pg.key
                t0 = time.time()
                try:
                    for attempt in (1, 2):
                        throttled = recorder.throttled
                        page.goto(base + route, wait_until="domcontentloaded", timeout=90000)
                        wait_quiet(page, lambda: recorder.inflight, 1200, 40000)
                        try:
                            page.wait_for_selector("main h1, h1", timeout=6000)
                        except PWError:
                            pass
                        scrolled = page.evaluate(SCROLL_TO_BOTTOM)
                        wait_quiet(page, lambda: recorder.inflight, 900, 25000)
                        if recorder.throttled == throttled or attempt == 2:
                            break
                        # 429: the API's 300/minute. A good answer already saved is never replaced by a
                        # 429; the page is walked once more after the window has passed
                        print("· " + pg.key.ljust(18) + " the API asked to slow down (429) — waiting a minute, then walking it again", flush=True)
                        for _ in range(61):
                            page.wait_for_timeout(1000)
                    for d in PAGES:
                        if d.detail_from == pg.key and d.link:
                            href = page.evaluate(FIRST_LINK, d.link)
                            if href:
                                details[d.key] = href
                except PWError as exc:
                    if page.is_closed():
                        raise RuntimeError("the window was closed during the walk") from exc
                    walked.append({"key": pg.key, "route": route, "status": "error: " + str(exc)[:160]})
                    print("· " + pg.key.ljust(18) + " " + route.ljust(34) + " ERROR " + str(exc)[:100], flush=True)
                    continue
                n = len(recorder.per_page.get(pg.key, []))
                walked.append({"key": pg.key, "route": route, "status": "walked", "requests": recorder.per_page.get(pg.key, []),
                               "scroller": scrolled.get("scroller"), "seconds": round(time.time() - t0, 1)})
                print("· " + pg.key.ljust(18) + " " + route.ljust(34) + " " + str(n) + " answers", flush=True)

            if args.rep:
                recorder.current = "rep"
                for path in REP_ENDPOINTS:
                    q = urlencode({"rep": args.rep})
                    res = page.evaluate(IN_PAGE_GET, api + path + "?" + q)
                    key = rec_key("GET", path, q)
                    plain = rec_key("GET", path, "")
                    if res.get("status") == 200 and key in rec.entries:
                        rec.index.setdefault("as", {}).setdefault("salesman", {})[plain] = rec.entries[key]
                        print("· rep " + path + "?rep=… saved for the salesman replay", flush=True)
                    else:
                        rec.index["notes"].append("--rep " + path + " answered " + str(res.get("status")) + " — not used")
                        print("· rep " + path + " answered " + str(res.get("status")) + " (not used)", flush=True)
                rec.index["rep"] = args.rep
        except (PWError, RuntimeError) as exc:
            print("walk stopped: " + str(exc)[:200], file=sys.stderr)
            rec.index["notes"].append("walk stopped early: " + str(exc)[:200])
        finally:
            recorder.recording = False
            if signed_in:
                prev = {p["key"]: p for p in rec.index.get("pages", [])}
                for w in walked:
                    prev[w["key"]] = w
                rec.index["pages"] = [prev[p.key] for p in PAGES if p.key in prev]
                rec.index["supabase_host"] = recorder.supabase_host or rec.index.get("supabase_host")
                rec.index["blocked_writes"] = (rec.index.get("blocked_writes") or []) + recorder.blocked
                rec.save()
            try:
                ctx.close()   # the profile, its storage and the session go with it
                browser.close()
            except PWError:
                pass

    done = [w for w in walked if w["status"] == "walked"]
    distinct = sorted({b["method"] + " " + b["path"] for b in recorder.blocked})
    print("")
    print("pages walked " + str(len(done)) + " of " + str(len(walked)) + " · answers saved " + str(recorder.saved)
          + " (" + str(len(rec.entries)) + " distinct, " + str(round(recorder.saved_bytes / 1e6, 1)) + " MB)"
          + " · writes blocked " + str(len(recorder.blocked)) + " · tokens masked " + str(recorder.masked)
          + (" · rate-limited answers " + str(recorder.throttled) if recorder.throttled else "")
          + " · " + str(round(time.time() - t_start)) + " s")
    still_429 = sorted(k for k, e in rec.entries.items() if int(e.get("status") or 0) == 429)
    for k in still_429[:10]:
        print("  still rate-limited (walk that page again with --only): " + k)
    for d in distinct[:20]:
        print("  blocked: " + d)
    for e in recorder.errors[:10]:
        print("  error: " + e)
    print("wrote " + str(rec.root / "index.json") + " — the browser is closed and nothing about the session was kept")
    return 0


# ── replay plumbing ────────────────────────────────────────────────────────────────────────────


def wait_http(url: str, seconds: float) -> bool:
    t0 = time.time()
    while time.time() - t0 < seconds:
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.4)
    return False


def port_busy(port: int) -> bool:
    for host in ("127.0.0.1", "::1"):
        try:
            with socket.create_connection((host, port), timeout=0.4):
                return True
        except OSError:
            continue
    return False


class Preview:
    """`vite preview` of one built folder on --port, started and always stopped (taskkill /T /F on
    Windows, as review_run.py does). It refuses a busy port, and checks that the page it serves is
    the dist's own index.html."""

    def __init__(self, dist: Path, port: int, log: Path) -> None:
        self.dist = dist
        self.port = port
        self.log = log
        self.base = "http://localhost:" + str(port)
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> "Preview":
        if port_busy(self.port):
            raise SystemExit("port " + str(self.port) + " is busy — stop whatever serves it or pass --port")
        self.log.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.log, "w", encoding="utf-8")  # noqa: SIM115 — closed with the process
        self.proc = subprocess.Popen("npx vite preview --outDir " + json.dumps(str(self.dist)) + " --port " + str(self.port) + " --strictPort",
                                     shell=True, cwd=str(WEB), stdout=fh, stderr=subprocess.STDOUT)
        atexit.register(self.__exit__, None, None, None)   # a crash or Ctrl+C never leaves it serving
        if not wait_http(self.base + "/", 90):
            self.__exit__(None, None, None)
            raise SystemExit("vite preview did not come up on " + self.base + " (see " + str(self.log) + ")")
        want = re.search(r'src="(/assets/index-[^"]+\.js)"', (self.dist / "index.html").read_text(encoding="utf-8"))
        with urllib.request.urlopen(self.base + "/", timeout=10) as r:
            served = r.read().decode("utf-8", "replace")
        if want and want.group(1) not in served:
            self.__exit__(None, None, None)
            raise SystemExit(self.base + " does not serve " + str(self.dist) + " (another server on the port?)")
        return self

    def __exit__(self, *exc) -> None:
        if self.proc and self.proc.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True)
            else:
                self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pass
        self.proc = None


def api_of_dist(dist: Path) -> str:
    html = (dist / "index.html").read_text(encoding="utf-8")
    m = re.search(r'data-api="([^"]*)"', html)
    api = (m.group(1) if m else "").strip().rstrip("/")
    if not api or api.startswith("%"):
        raise SystemExit(str(dist) + " was built without VITE_API_URL (web/.env): the harness cannot tell its API calls apart")
    return api


def supabase_of_dist(dist: Path, rec: Recording) -> str | None:
    for js in sorted((dist / "assets").glob("*.js")):
        m = re.search(r"https://([a-z0-9]{12,40})\.supabase\.co", js.read_text(encoding="utf-8", errors="replace"))
        if m:
            return m.group(1) + ".supabase.co"
    host = rec.index.get("supabase_host")
    if host:
        return host
    env = WEB / ".env"
    if env.exists():
        m = re.search(r"VITE_SUPABASE_URL=https://([a-z0-9]+)\.supabase\.co", env.read_text(encoding="utf-8"))
        if m:
            return m.group(1) + ".supabase.co"
    return None


def fake_session(now_s: int) -> dict:
    b64 = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")  # noqa: E731
    jwt = b64({"alg": "none", "typ": "JWT"}) + "." + b64({"sub": UID, "exp": now_s + 7200, "role": "authenticated"}) + ".x"
    user = {"id": UID, "aud": "authenticated", "role": "authenticated", "email": "qa@example.invalid",
            "app_metadata": {}, "user_metadata": {}, "created_at": "2026-01-01T00:00:00Z"}
    return {"access_token": jwt, "token_type": "bearer", "expires_in": 7200, "expires_at": now_s + 7200,
            "refresh_token": "qa-not-a-token", "user": user}


# The page clock (Date only — timers, rAF and performance.now stay real, so animations and the
# timing checks are untouched) starts at the recording's instant and runs. Storage is seeded once per
# tab, so a reload keeps what the app changed. __qaPaint marks the first frame with anything in #root
# and the first visible h1 (S7).
INIT = r"""
((cfg) => {
  if (location.origin !== cfg.origin) return;
  if (cfg.clockMs) {
    const D = Date, shift = cfg.clockMs - D.now();
    function QaDate(...a) {
      if (!new.target) return new D(D.now() + shift).toString();
      return a.length ? new D(...a) : new D(D.now() + shift);
    }
    QaDate.prototype = D.prototype;
    QaDate.now = () => D.now() + shift;
    QaDate.parse = D.parse;
    QaDate.UTC = D.UTC;
    Object.defineProperty(QaDate, 'name', { value: 'Date' });
    globalThis.Date = QaDate;
  }
  try {
    if (!sessionStorage.getItem('qa-seeded')) {
      sessionStorage.setItem('qa-seeded', '1');
      for (const k of cfg.remove) localStorage.removeItem(k);
      for (const k in cfg.kv) localStorage.setItem(k, cfg.kv[k]);
    }
  } catch (e) { /* storage blocked */ }
  window.__qaPaint = {};
  const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const t0 = performance.now();
  const loop = () => {
    const root = document.getElementById('root');
    if (!__qaPaint.root && root && root.firstElementChild && [...root.querySelectorAll('*')].some(vis)) __qaPaint.root = performance.now();
    const h = document.querySelector('main h1, h1');
    if (!__qaPaint.h1 && h && vis(h)) __qaPaint.h1 = performance.now();
    if (!__qaPaint.h1 && performance.now() - t0 < 20000) requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
})
"""


@dataclass
class ReplayCfg:
    dist: Path
    rec_dir: Path
    out: Path
    as_role: str = "admin"
    widths: list[int] = field(default_factory=lambda: [390, 1024, 1440])
    themes: list[str] = field(default_factory=lambda: ["light", "dark"])
    only: set[str] = field(default_factory=set)
    port: int = DEFAULT_PORT
    fail_api: bool = False
    clock: str = "recorded"
    smooth: bool = True
    nav: bool = True
    canaries: bool = False
    probe_miss: bool = False


class Replayer:
    """One context's network: the preview's files pass; the API and Supabase are answered from the
    recording; writes are answered here; anything else is refused locally. Never the network."""

    def __init__(self, rec: Recording, api: str, sb_host: str | None, site: str, cfg: ReplayCfg, me: dict) -> None:
        self.rec = rec
        self.api = api
        self.api_origin = _origin(api)
        self.api_path = urlsplit(api).path.rstrip("/")
        self.sb_origin = "https://" + sb_host if sb_host else None
        self.site = site
        self.cfg = cfg
        self.me = me
        self.page_of: dict[Any, str] = {}
        self.current = "-"
        self.misses: dict[tuple[str, str], dict] = {}
        self.errors_answered: set[str] = set()     # URLs the harness itself answered >= 400
        self.recorded_errors: dict[str, set[str]] = {}
        self.blocked: list[dict] = []
        self.injected = 0
        self.hits = 0
        self.quoted = 0
        self.handler_errors: list[str] = []
        self.probe_done = False
        self.last_routed = time.time()   # settle(): the page is quiet once nothing was asked for a while
        self.catalog_prices: dict[str, float] | None = None

    # attribution: which walked page asked
    def who(self, route: Route) -> str:
        try:
            return self.page_of.get(route.request.frame.page, self.current)
        except PWError:
            return self.current

    def miss(self, key: str, kind: str, page: str, served: str | None = None) -> None:
        m = self.misses.setdefault((kind, key), {"key": key, "kind": kind, "pages": [], "count": 0, "served": served})
        m["count"] += 1
        if page not in m["pages"]:
            m["pages"].append(page)

    def answer(self, route: Route, status: int, ctype: str, body: bytes | str) -> None:
        if status >= 400:
            self.errors_answered.add(route.request.url)
        route.fulfill(status=status, headers=_cors(route.request), content_type=ctype, body=body)

    def on_route(self, route: Route) -> None:
        self.last_routed = time.time()
        req = route.request
        url = req.url
        u = urlsplit(url)
        origin = _origin(url)
        method = req.method.upper()
        page = self.who(route)
        try:
            if origin == self.site:
                if u.path.startswith("/__qa/") and self.cfg.canaries:
                    for path, html in CANARIES.values():
                        if u.path == path:
                            route.fulfill(status=200, content_type="text/html", body="<!doctype html><html><body>" + html + "</body></html>")
                            return
                if method in ("GET", "HEAD"):
                    route.continue_()
                else:
                    self.blocked.append({"method": method, "path": u.path, "page": page})
                    self.answer(route, 200, "application/json", json.dumps({"ok": True, "qa": "not sent"}))
                return
            if origin == self.api_origin and (not self.api_path or u.path.startswith(self.api_path)):
                self.on_api(route, u.path[len(self.api_path):] or "/", u.query, method, page)
                return
            if self.sb_origin and origin == self.sb_origin:
                self.on_supabase(route, u, method, page)
                return
            # anything else would leave the machine: answered here, logged
            self.miss(method + " " + origin + u.path, "external", page)
            if req.resource_type == "image":
                self.answer(route, 200, "image/svg+xml", PLACEHOLDER_SVG)
            else:
                self.answer(route, 404, "text/plain", "qa: external request not sent")
        except Exception as exc:  # noqa: BLE001 — never leave a request hanging, never let it out
            self.handler_errors.append(method + " " + u.path + ": " + type(exc).__name__ + " " + str(exc)[:160])
            try:
                self.answer(route, 500, "application/json", json.dumps({"detail": "qa: harness error"}))
            except Exception:  # noqa: BLE001 — already answered
                pass

    def on_api(self, route: Route, path: str, query: str, method: str, page: str) -> None:
        if method == "OPTIONS":
            route.fulfill(status=204, headers=_cors(route.request), body="")
            return
        if method not in ("GET", "HEAD"):
            self.blocked.append({"method": method, "path": path, "page": page})
            if path.endswith("/quote"):
                self.quoted += 1
                self.answer(route, 200, "application/json", json.dumps(self.quote(route)))
            else:
                self.answer(route, 200, "application/json", json.dumps({"ok": True, "qa": "not sent"}))
            return
        if path == "/me":
            self.answer(route, 200, "application/json", json.dumps(self.me))
            return
        if self.cfg.fail_api and path not in SHELL_CALLS:
            self.injected += 1
            self.answer(route, 500, "application/json", json.dumps({"detail": "Server error", "ref": "qa-fail-api"}))
            return
        key = rec_key("GET", path, query)
        entry, kind, served = self.rec.lookup(key, self.cfg.as_role)
        if entry is None:
            self.miss(key, "miss", page)
            self.answer(route, 404, "application/json", json.dumps({"detail": "qa: not in the recording", "key": key}))
            return
        if kind == "near":
            self.miss(key, "near", page, served)
        self.hits += 1
        status = int(entry.get("status") or 200)
        if status >= 400:
            self.recorded_errors.setdefault(page, set()).add(key + " → " + str(status))
        self.answer(route, status, entry.get("content_type") or "application/json", self.rec.body(entry))

    def on_supabase(self, route: Route, u, method: str, page: str) -> None:
        if method == "OPTIONS":
            route.fulfill(status=204, headers=_cors(route.request), body="")
            return
        if u.path.startswith("/auth/v1/"):
            now_s = int(time.time())
            if u.path == "/auth/v1/token":
                body: Any = fake_session(now_s)
            elif u.path == "/auth/v1/user":
                body = fake_session(now_s)["user"]
            else:
                body = {}
            self.answer(route, 200, "application/json", json.dumps(body))
            return
        if method not in ("GET", "HEAD"):
            self.blocked.append({"method": method, "path": u.path, "page": page})
            self.answer(route, 200, "application/json", "{}")
            return
        key = rec_key("GET", u.path, u.query, "supabase")
        entry, kind, served = self.rec.lookup(key, self.cfg.as_role)
        if entry is not None and int(entry.get("status") or 200) < 400:
            self.answer(route, int(entry.get("status") or 200), entry.get("content_type") or "application/octet-stream", self.rec.body(entry))
            return
        if u.path.startswith("/storage/v1/"):
            self.miss(key, "asset", page)
            self.answer(route, 200, "image/svg+xml", PLACEHOLDER_SVG)
            return
        self.miss(key, "miss", page)
        self.answer(route, 404, "application/json", json.dumps({"message": "qa: not in the recording"}))

    def quote(self, route: Route) -> dict:
        """POST …/quote prices a cart and writes nothing; the recording cannot hold an answer for a
        cart that did not exist, so the replay prices it from the recorded staff catalog."""
        if self.catalog_prices is None:
            self.catalog_prices = {}
            data = self.rec.json_of("GET /shop/catalog") or {}
            for it in data.get("items") or []:
                if it.get("item_code") and it.get("price_bhd") is not None:
                    self.catalog_prices[str(it["item_code"])] = float(it["price_bhd"])
        try:
            body = route.request.post_data_json or {}
        except (ValueError, PWError):
            body = {}
        lines, total, units = [], 0.0, 0
        for ln in body.get("lines") or []:
            code, qty = str(ln.get("item_code") or ""), int(ln.get("qty") or 0)
            price = self.catalog_prices.get(code, 0.0)
            lines.append({"item_code": code, "qty": qty, "unit_price_bhd": price, "list_price_bhd": price, "line_total_bhd": round(price * qty, 3)})
            total += price * qty
            units += qty
        total = round(total, 3)
        return {"ok": True, "qa": "priced by the replay", "lines": lines, "subtotal_bhd": total, "discount_bhd": 0, "total_bhd": total,
                "units": units, "items": len(lines), "discounts": [], "warnings": [], "can_submit": True}

    def misses_list(self) -> list[dict]:
        order = {"miss": 0, "near": 1, "asset": 2, "external": 3}
        return sorted(self.misses.values(), key=lambda m: (order.get(m["kind"], 9), -m["count"], m["key"]))


# ── in-page probes ─────────────────────────────────────────────────────────────────────────────
# One round trip each; the Python side only turns numbers into findings.

PROBE = r"""
(opts) => {
  const vw = innerWidth, vh = innerHeight;
  const out = { vw, vh };
  const de = document.documentElement, bd = document.body;
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
  const label = (el) => ((el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim().split('\n')[0] || '').slice(0, 48);
  const shown = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) return null;
    if (cs.pointerEvents === 'none') return null;
    if (el.closest('[aria-hidden="true"]') || el.closest('[inert]')) return null;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return null;
    return r;
  };
  const inView = (r) => r.bottom > 0 && r.top < vh && r.right > 0 && r.left < vw;

  /* ── S9: who scrolls — the document, or the biggest overflow:auto box (the office shell's <main>) ── */
  const docEl = document.scrollingElement || de;
  let inner = null;
  for (const el of document.querySelectorAll('body *')) {
    /* cheap first: a box that cannot scroll now is skipped, except a <main> built to scroll */
    if (el.scrollHeight <= el.clientHeight + 1 && el.tagName !== 'MAIN') continue;
    const cs = getComputedStyle(el);
    if (!/(auto|scroll|overlay)/.test(cs.overflowY)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < vw * 0.5 || r.height < vh * 0.4) continue;
    const area = r.width * r.height;
    if (!inner || area > inner.area) inner = { el, area };
  }
  document.querySelectorAll('[data-qa-scroller]').forEach((e) => e.removeAttribute('data-qa-scroller'));
  out.scroller = { docScroll: Math.max(0, Math.round(docEl.scrollHeight - vh)), inner: null };
  if (inner) {
    inner.el.setAttribute('data-qa-scroller', '1');
    out.scroller.inner = { sel: sel(inner.el), tag: inner.el.tagName.toLowerCase(), scroll: Math.max(0, inner.el.scrollHeight - inner.el.clientHeight), clientHeight: inner.el.clientHeight };
  }

  /* ── horizontal overflow of the document ── */
  const scrollWidth = Math.max(de.scrollWidth, bd ? bd.scrollWidth : 0);
  out.overflow = { scrollWidth, innerWidth: vw, offenders: [] };
  if (scrollWidth > vw + 1) {
    for (const el of document.querySelectorAll('body *')) {
      const r = el.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) continue;
      if (r.right <= vw + 1 && r.left >= -1) continue;
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden' || cs.position === 'fixed') continue;
      let clipped = false;
      for (let p = el.parentElement; p && p !== de; p = p.parentElement) {
        if (/(hidden|auto|scroll|clip)/.test(getComputedStyle(p).overflowX)) { clipped = true; break; }
      }
      if (clipped) continue;
      out.overflow.offenders.push({ sel: sel(el), left: Math.round(r.left), right: Math.round(r.right), width: Math.round(r.width) });
      if (out.overflow.offenders.length >= 6) break;
    }
  }

  /* ── content cut off: wider than a page-wide overflow-x:hidden frame (the shell's <main> hides
     what a document would have shown as overflow) ── */
  out.clipped = [];
  const main = document.querySelector('main');
  if (main) {
    const mr = main.getBoundingClientRect();
    const mw = mr.width;
    const found = [];
    for (const el of main.querySelectorAll('*')) {
      const r = el.getBoundingClientRect();
      if (r.width < 1 || r.height < 1 || r.right <= mr.right + 1) continue;
      let clip = null;
      for (let p = el.parentElement; p && p !== de; p = p.parentElement) {
        const ox = getComputedStyle(p).overflowX;
        if (ox !== 'visible') { clip = { p, ox }; break; }
      }
      if (!clip || /(auto|scroll)/.test(clip.ox)) continue;
      const cr = clip.p.getBoundingClientRect();
      if (cr.width < mw * 0.9 || r.right <= cr.right + 1) continue;
      const cs = getComputedStyle(el);
      if (cs.display === 'none' || cs.visibility === 'hidden' || cs.position === 'fixed' || cs.position === 'absolute') continue;
      if (found.some((f) => f.contains(el))) continue;
      found.push(el);
      out.clipped.push({ sel: sel(el), text: label(el), right: Math.round(r.right), frameRight: Math.round(cr.right), frame: sel(clip.p) });
      if (out.clipped.length >= 6) break;
    }
  }

  /* ── tap targets and input sizes on touch (market_qa's rules: the box plus a hit-area expander) ── */
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
  out.smallInputs = [];
  if (opts.touch) {
    for (const el of document.querySelectorAll('a, button, input, select, textarea, [role="button"]')) {
      if (el.disabled || el.type === 'hidden') continue;
      let r = shown(el);
      if (!r || !inView(r)) continue;
      if (el.tagName === 'A' && el.closest('p')) continue;
      let target = el;
      if (el.tagName === 'INPUT' && (el.type === 'checkbox' || el.type === 'radio')) {
        const lab = el.closest('label') || (el.id ? document.querySelector('label[for="' + CSS.escape(el.id) + '"]') : null);
        if (lab) { target = lab; r = lab.getBoundingClientRect(); }
      }
      const e = effective(target, r);
      if (Math.min(e.w, e.h) < 44) out.taps.push({ sel: sel(el), text: label(target) || label(el), w: Math.round(e.w), h: Math.round(e.h) });
    }
    for (const el of document.querySelectorAll('input, select, textarea')) {
      if (el.type === 'hidden' || el.type === 'checkbox' || el.type === 'radio') continue;
      if (!shown(el)) continue;
      const fs = parseFloat(getComputedStyle(el).fontSize);
      if (fs < 16) out.smallInputs.push({ sel: sel(el), fontSize: fs });
    }
  }

  const h1 = [...document.querySelectorAll('main h1, h1')].find((h) => shown(h));
  out.heading = h1 ? (h1.innerText || '').trim().slice(0, 80) : null;
  /* which shell rendered: the salesman app (SalesmanShell), the office (AppShell), or neither */
  out.shell = document.querySelector('[data-shell="sales"]') ? 'salesman' : document.querySelector('main.overflow-y-auto, aside nav') ? 'office' : 'none';
  return out;
}
"""

SCROLL_BY = r"""
(y) => {
  const box = document.querySelector('[data-qa-scroller]');
  const doc = document.scrollingElement || document.documentElement;
  if (doc.scrollHeight > innerHeight + 1 || !box) { window.scrollTo(0, y); return window.scrollY; }
  box.scrollTop = y;
  return box.scrollTop;
}
"""

# S10: an honest error on the page, or a false "nothing here"
AFFORDANCE = r"""
({ text, button, falseEmpty }) => {
  const vis = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && Number(cs.opacity) > 0.05; };
  const scope = document.querySelector('main') || document.body;
  const body = (scope && scope.innerText) || '';
  const m = new RegExp(text, 'i').exec(body);
  const bre = new RegExp(button, 'i');
  const btn = [...scope.querySelectorAll('button, [role="button"], a')].find((b) => vis(b) && bre.test((b.innerText || b.getAttribute('aria-label') || '').trim()));
  return {
    text: m ? body.slice(Math.max(0, m.index - 40), m.index + 60).replace(/\s+/g, ' ').trim() : null,
    button: btn ? (btn.innerText || btn.getAttribute('aria-label') || '').trim().slice(0, 40) : null,
    alerts: [...scope.querySelectorAll('[role="alert"]')].filter(vis).map((a) => (a.innerText || '').trim().slice(0, 80)),
    falseEmpty: falseEmpty.filter((s) => body.includes(s)),
    chars: body.length,
  };
}
"""

# S12: what fixed or stuck-sticky chrome covers of the screen (overlaps counted once)
CHROME = r"""
() => {
  const vh = innerHeight;
  const sel = (el) => { let s = el.tagName.toLowerCase(); const c = (el.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean).slice(0, 2);
    if (c.length) s += '.' + c.join('.'); const al = el.getAttribute('aria-label'); if (al) s += '[aria-label="' + al.slice(0, 30) + '"]'; return s; };
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

# S8: from a menu click to the new page's heading, counting the loaders on the way
NAV_HOP = r"""
async ({ href, loaders, timeout }) => {
  const vis = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth
      && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  /* a menu link may sit below the fold of a long sidebar: it needs a box on screen horizontally */
  const usable = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && r.right > 0 && r.left < innerWidth && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const opacity = (el) => { let o = 1; for (let n = el; n && n.nodeType === 1; n = n.parentElement) o *= Number(getComputedStyle(n).opacity); return o; };
  const links = [...document.querySelectorAll('a[href]')].filter((a) => a.getAttribute('href') === href && usable(a) && opacity(a) > 0.5);
  const link = links.find((a) => a.closest('nav')) || links.find((a) => a.closest('aside, header')) || null;
  if (!link) return { error: 'no visible menu link to ' + href };
  link.scrollIntoView({ block: 'nearest' });
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  const oldHeads = new Set(document.querySelectorAll('h1, h2'));
  const before = new Set([...document.querySelectorAll(loaders)].filter(vis));
  const firstSeen = [];
  const seen = new Set();
  let lastMut = performance.now();
  const mo = new MutationObserver(() => { lastMut = performance.now(); });
  mo.observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true });
  const kindOf = (el) => (el.matches('.animate-spin') ? 'spinner' : el.matches('.animate-pulse, .skeleton') ? 'skeleton' : 'busy');
  const t0 = performance.now();
  link.click();
  let paint = null, settled = null, lastLoader = -1, heading = null, headEl = null;
  while (performance.now() - t0 < timeout) {
    await new Promise((r) => requestAnimationFrame(r));
    const t = performance.now() - t0;
    const now = [...document.querySelectorAll(loaders)].filter((el) => !before.has(el) && vis(el) && opacity(el) > 0.05);
    for (const el of now) if (!seen.has(el)) { seen.add(el); firstSeen.push({ t, kind: kindOf(el) }); }
    if (now.length) lastLoader = t;
    if (!headEl || !headEl.isConnected) {
      headEl = [...document.querySelectorAll('main h1, h1')].find((h) => !oldHeads.has(h) && vis(h))
        || [...document.querySelectorAll('main h2')].find((h) => !oldHeads.has(h) && vis(h)) || null;
    }
    if (headEl && paint === null && opacity(headEl) > 0.05) { paint = t; heading = (headEl.innerText || '').trim().slice(0, 60); }
    const calm = !now.length && t - lastLoader > 250;
    if (calm && headEl && opacity(headEl) >= 0.99) { settled = t; break; }
    if (calm && !headEl && t > 400 && performance.now() - lastMut > 400) { settled = t; break; }
  }
  mo.disconnect();
  /* a loader = loaders of one kind first seen within 120 ms of each other (six skeleton bars are one) */
  const groups = [];
  for (const s of firstSeen) {
    const g = groups.find((x) => x.kind === s.kind && s.t - x.last <= 120);
    if (g) g.last = s.t; else groups.push({ kind: s.kind, first: s.t, last: s.t });
  }
  return { paint: paint === null ? null : Math.round(paint), settled: settled === null ? null : Math.round(settled),
           loaders: groups.length, kinds: groups.map((g) => g.kind + '@' + Math.round(g.first)), heading, path: location.pathname + location.search };
}
"""

# S1 for a page whose content scrolls inside a box (smooth.scroll_jank drives the document)
SCROLL_FRAMES_IN = r"""
async ({ speed, px }) => {
  const box = document.querySelector('[data-qa-scroller]');
  if (!box) return null;
  const end = Math.min(box.scrollHeight - box.clientHeight, px);
  const deltas = [];
  let last = performance.now();
  await new Promise((done) => {
    const t0 = performance.now(), dur = Math.max(1, end) / speed * 1000;
    const step = (t) => {
      deltas.push(t - last); last = t;
      const k = Math.min(1, (t - t0) / dur);
      box.scrollTop = end * k;
      k < 1 ? requestAnimationFrame(step) : done();
    };
    requestAnimationFrame(step);
  });
  deltas.shift();
  box.scrollTop = 0;
  const s = deltas.slice().sort((a, b) => a - b);
  const q = (p) => (s.length ? s[Math.min(s.length - 1, Math.floor(p * s.length))] : 0);
  return { end, frames: s.length, p50: +q(0.5).toFixed(1), p95: +q(0.95).toFixed(1),
           max: s.length ? +s[s.length - 1].toFixed(1) : 0, over50: s.filter((d) => d > 50).length };
}
"""


# ── checks ─────────────────────────────────────────────────────────────────────────────────────


class Net:
    """Console errors, page errors and failed requests — minus what the harness answered on purpose
    (a miss, an injected 500, a recorded error status), which is accounted for elsewhere."""

    IGNORED = ("net::ERR_ABORTED", "net::ERR_NETWORK_IO_SUSPENDED")

    def __init__(self, rp: Replayer) -> None:
        self.rp = rp
        self.console: list[str] = []
        self.errors: list[str] = []
        self.failed: list[str] = []

    def attach(self, page: Page) -> None:
        def on_console(msg) -> None:
            if msg.type != "error":
                return
            loc = ""
            try:
                loc = (msg.location or {}).get("url") or ""
            except PWError:
                pass
            if msg.text.startswith("Failed to load resource") and loc in self.rp.errors_answered:
                return
            if any(r in msg.text for r in self.IGNORED):
                return
            self.console.append(msg.text[:300] + ((" @ " + loc[:120]) if loc else ""))

        def on_pageerror(err) -> None:
            self.errors.append(str(err)[:300])

        def on_failed(req) -> None:
            why = req.failure or "failed"
            if any(r in why for r in self.IGNORED):
                return
            self.failed.append(req.method + " " + req.url[:160] + " — " + why)

        def on_response(res) -> None:
            if res.status >= 400 and res.url not in self.rp.errors_answered:
                self.failed.append(str(res.status) + " " + res.request.method + " " + res.url[:160])

        page.on("console", on_console)
        page.on("pageerror", on_pageerror)
        page.on("requestfailed", on_failed)
        page.on("response", on_response)


def probe(page: Page, vp: Viewport) -> dict:
    return page.evaluate(PROBE, {"touch": vp.touch})


def probe_scan(page: Page, vp: Viewport) -> dict:
    """The geometry only means something for what is on screen: the probe runs at the top and then
    one screen at a time down the page's own scroller (up to 5), and the findings are merged."""
    p = probe(page, vp)
    taps = {(t["sel"], t["text"]): t for t in p["taps"]}
    inputs = {i["sel"]: i for i in p["smallInputs"]}
    clipped = {c["sel"]: c for c in p["clipped"]}
    s = p["scroller"]
    span = max(s["docScroll"], (s["inner"] or {}).get("scroll", 0))
    for i in range(1, min(6, 1 + int(span / max(1, vp.height * 0.85)) + 1)):
        page.evaluate(SCROLL_BY, int(i * vp.height * 0.85))
        page.wait_for_timeout(260)
        q = probe(page, vp)
        taps.update({(t["sel"], t["text"]): t for t in q["taps"]})
        inputs.update({x["sel"]: x for x in q["smallInputs"]})
        clipped.update({c["sel"]: c for c in q["clipped"]})
        if q["overflow"]["scrollWidth"] > p["overflow"]["scrollWidth"]:
            p["overflow"] = q["overflow"]
    page.evaluate(SCROLL_BY, 0)
    page.wait_for_timeout(200)
    p["taps"], p["smallInputs"], p["clipped"] = list(taps.values()), list(inputs.values()), list(clipped.values())
    return p


def net_checks(run: Run, net: Net, fail_api: bool) -> None:
    """After everything the page went through (load, sweep, S10, S12, S1, S4)."""
    for msg in dict.fromkeys(net.console):
        if not fail_api:
            finding(run, "fail", "console-error", msg)
        elif "Unhandled error" in msg:
            # components/ErrorBoundary: the page crashed on the failure. Its "Something went wrong ·
            # Reload" screen would pass S10, so the crash is its own hard finding
            finding(run, "fail", "error-boundary", "S10: the failure crashed the page into the error screen — " + msg)
        else:
            # under --fail-api the 500s are the point: a line the page logs about them is advisory
            finding(run, "warn", "console-error", msg)
    for msg in dict.fromkeys(net.errors):
        finding(run, "fail", "page-error", msg)
    for msg in dict.fromkeys(net.failed):
        finding(run, "fail", "request-failed", msg)


def common_checks(run: Run, p: dict) -> None:
    ov = p["overflow"]
    if ov["scrollWidth"] > ov["innerWidth"] + 1:
        who = ", ".join(o["sel"] + " (right " + str(o["right"]) + "px)" for o in ov["offenders"][:3]) or "no unclipped element found"
        finding(run, "fail", "horizontal-overflow", "scrollWidth " + str(ov["scrollWidth"]) + " > innerWidth " + str(ov["innerWidth"]) + " — " + who, ov)
    for c in p["clipped"][:3]:
        finding(run, "warn", "content-clipped", "cut off at the right edge of " + c["frame"] + " (" + str(c["right"]) + "px > " + str(c["frameRight"]) + "px): "
                + c["sel"] + (" “" + c["text"] + "”" if c["text"] else ""), c)

    groups: dict[tuple[str, int], list[dict]] = {}
    for t in p["taps"]:
        shape = re.sub(r'\[aria-label="[^"]*"\]', "", t["sel"])
        groups.setdefault((shape, t["h"]), []).append(t)
    for (shape, h), hits in sorted(groups.items()):
        widths = sorted({x["w"] for x in hits})
        size = str(widths[0]) + ("–" + str(widths[-1]) if len(widths) > 1 else "") + "x" + str(h) + "px"
        texts = [x["text"] for x in hits if x["text"]]
        who = shape + (" ×" + str(len(hits)) if len(hits) > 1 else "") + (" — " + ", ".join(texts[:3]) if texts else "")
        values = {"selector": shape, "widths": widths, "h": h, "count": len(hits), "samples": texts[:6]}
        if min(widths[0], h) < 36:
            finding(run, "fail", "tap-target", "below the 36px floor: " + size + " · " + who, values)
        else:
            finding(run, "warn", "tap-target", "below 44px: " + size + " · " + who, values)
    for i in p["smallInputs"]:
        finding(run, "fail", "input-font-size", str(i["fontSize"]) + "px < 16px on touch · " + i["sel"], i)


def s9_scroller(run: Run, p: dict) -> dict:
    """S9: which element scrolls the page. On a touch phone an inner scroller costs the collapsing
    address bar, the tap-the-status-bar-to-top gesture and the browser's own Back restore."""
    s = p["scroller"]
    inner = s.get("inner")
    if s["docScroll"] > 1 or not inner:
        what = {"by": "document", "scroll_px": s["docScroll"]}
        run.notes.append("S9 scroller: the document (" + str(s["docScroll"]) + "px to scroll)")
    else:
        what = {"by": inner["sel"], "scroll_px": inner["scroll"]}
        run.notes.append("S9 scroller: " + inner["sel"] + " (an inner box, " + str(inner["scroll"]) + "px to scroll; the document does not scroll)")
    run.metrics["scroller"] = what["by"] + " · " + str(what["scroll_px"]) + "px"
    return {"inner": what["by"] != "document", **what}


def frame_stats(data_b64: str) -> dict:
    """A screencast frame: its dominant colour, how much of it is that colour, its luminance."""
    from PIL import Image  # Pillow is a project requirement (requirements.txt)

    img = Image.open(io.BytesIO(base64.b64decode(data_b64)))
    img.draft("RGB", (64, 64))   # JPEG: decode at 1/8 scale — a spinning loader streams 60 frames a second
    img = img.convert("RGB").resize((24, 24))
    raw = img.tobytes()
    px = [(raw[i], raw[i + 1], raw[i + 2]) for i in range(0, len(raw), 3)]
    dom = Counter((r // 6, g // 6, b // 6) for r, g, b in px).most_common(1)[0][0]
    dom_rgb = tuple(c * 6 + 3 for c in dom)
    near = sum(1 for q in px if all(abs(q[k] - dom_rgb[k]) <= 12 for k in range(3))) / len(px)
    lum = 0.2126 * dom_rgb[0] + 0.7152 * dom_rgb[1] + 0.0722 * dom_rgb[2]
    return {"dominant": dom_rgb, "uniform": round(near, 3), "white": min(dom_rgb) >= 235, "lum": round(lum)}


class Screencast:
    """S7: the frames Chromium actually composites from the navigation on (CDP Page.startScreencast)."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self.frames: list[tuple[float, str]] = []
        self.cdp = None
        self.live = False

    def start(self) -> None:
        try:
            cdp = self.page.context.new_cdp_session(self.page)
        except PWError:
            return

        def on_frame(ev: dict) -> None:
            # a frame can land while stop() is in flight: the session is this closure's, never None
            if not self.live:
                return
            self.frames.append((float(ev.get("metadata", {}).get("timestamp") or 0), ev.get("data") or ""))
            try:
                cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})
            except Exception:  # noqa: BLE001 — the session closing under the ack
                pass

        try:
            cdp.on("Page.screencastFrame", on_frame)
            self.live = True
            cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 80, "maxWidth": 400, "maxHeight": 400, "everyNthFrame": 1})
            self.cdp = cdp
        except PWError:
            self.live = False

    def stop(self) -> None:
        cdp, self.cdp = self.cdp, None
        self.live = False
        if not cdp:
            return
        try:
            cdp.send("Page.stopScreencast")
            cdp.detach()
        except Exception:  # noqa: BLE001 — the page may be closing
            pass


def s7_first_paint(run: Run, page: Page, cast: Screencast, vp: Viewport, theme: str) -> dict | None:
    """S7: was any frame between navigation and the app's first paint plain white? A near-white,
    near-uniform frame before a DARK app paints is the flash (the theme lands after the first
    paint); before a light app it is only the app's own background, so it is a note."""
    try:
        origin = float(page.evaluate("() => performance.timeOrigin"))
        marks = page.evaluate("() => window.__qaPaint || {}")
    except PWError:
        return None
    frames = [(round(ts * 1000 - origin), data) for ts, data in cast.frames if ts and data and ts * 1000 >= origin - 5]
    if not frames:
        run.notes.append("S7: no screencast frame was captured")
        return None
    app_at = marks.get("root") or marks.get("h1")
    blank = lambda s: s["white"] and s["uniform"] >= 0.97  # noqa: E731
    # decode what matters: every frame up to the app's first paint, then until the first non-white
    # one (how long the white lasted), and the last frame (the app's own background)
    stats: list[tuple[int, dict]] = []
    for t, d in frames:
        if app_at is not None and t > app_at + 20 and (not stats or not blank(stats[-1][1])):
            break
        stats.append((t, frame_stats(d)))
    final = frame_stats(frames[-1][1])
    pre = [(t, s) for t, s in stats if app_at is None or t <= app_at + 20]
    white = [(t, s) for t, s in pre if blank(s)]
    dark_app = final["lum"] < 110
    white_ms = None
    if white:
        later = [t for t, s in stats if t > white[-1][0] and not blank(s)]
        white_ms = (later[0] if later else white[-1][0]) - white[0][0]
    stats.append((frames[-1][0], final))
    row = {"width": vp.width, "theme": theme, "page": run.page, "white_frames": len(white), "white_ms": white_ms,
           "first_white_at": white[0][0] if white else None, "app_paint_ms": round(app_at) if app_at else None,
           "heading_ms": round(marks["h1"]) if marks.get("h1") else None, "app_bg": "rgb" + str(final["dominant"]),
           "dark_app": dark_app, "frames": len(stats)}
    run.metrics["first_paint"] = row
    if white and dark_app:
        run.notes.append("S7: " + str(len(white)) + " near-white frame(s) for ~" + str(white_ms) + " ms before the dark app painted at "
                         + str(row["app_paint_ms"]) + " ms (first at " + str(white[0][0]) + " ms, " + "rgb" + str(white[0][1]["dominant"]) + ")")
    elif white:
        run.notes.append("S7: " + str(len(white)) + " light blank frame(s) before the app painted at " + str(row["app_paint_ms"])
                         + " ms — the app's own light background, no flash")
    else:
        run.notes.append("S7: no white frame before the app painted at " + str(row["app_paint_ms"]) + " ms")
    return row


def s10_fail_api(run: Run, page: Page) -> None:
    """S10: every API call of the page answered 500 — the page must say so (couldn't load / try
    again / retry / something went wrong, or a Retry button) and must never claim it is empty."""
    found: dict = {}
    t0 = time.time()
    while time.time() - t0 < 9:
        page.wait_for_timeout(500)
        found = page.evaluate(AFFORDANCE, {"text": AFFORDANCE_TEXT, "button": AFFORDANCE_BUTTON, "falseEmpty": list(FALSE_EMPTY)})
        if (found.get("text") or found.get("button")) and time.time() - t0 > 2.5:
            break
    run.metrics["fail_api"] = found
    if found.get("falseEmpty"):
        finding(run, "fail", "fail-api-false-empty", "S10: with every API call failing the page says “" + "”, “".join(found["falseEmpty"])
                + "” — an empty state is a lie when the data did not load", found)
    if not (found.get("text") or found.get("button")):
        finding(run, "fail", "fail-api-no-error", "S10: with every API call answering 500 the page shows no error (no couldn't load / try again / retry / "
                "something went wrong, no Retry button)" + (" — role=alert: " + "; ".join(found.get("alerts") or []) if found.get("alerts") else ""), found)
    elif not found.get("falseEmpty"):
        run.notes.append("S10: the page says “" + str(found.get("text") or "") + "”" + (" with a “" + str(found.get("button")) + "” button" if found.get("button") else ""))


def s12_chrome(run: Run, page: Page) -> None:
    """S12: the salesman on a phone with a cart: how much of the screen fixed and sticky chrome
    covers once the page is scrolled 400 px."""
    can = page.evaluate("() => (document.scrollingElement || document.documentElement).scrollHeight - innerHeight")
    page.evaluate("() => window.scrollTo(0, 400)")
    page.wait_for_timeout(450)
    c = page.evaluate(CHROME)
    page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(200)
    run.metrics["chrome"] = c
    parts = ", ".join(x["sel"] + " " + str(x["h"]) + "px" for x in c["parts"][:5])
    short = "" if can >= 400 else " (the page scrolls only " + str(max(0, int(can))) + "px)"
    if c["total"] > CHROME_PX_WARN:
        finding(run, "warn", "chrome-height", "S12: fixed/sticky chrome covers " + str(c["total"]) + "px of " + str(page.viewport_size["height"])
                + "px after scrolling 400px" + short + ": " + parts, c)
    else:
        run.notes.append("S12: fixed/sticky chrome covers " + str(c["total"]) + "px after scrolling 400px" + short + ": " + parts)


def s1_jank(run: Run, page: Page, s9: dict) -> None:
    """S1: frame times of a scripted scroll at 4x CPU — smooth.scroll_jank for a document scroller,
    the same measure on the inner box otherwise."""
    if s9["scroll_px"] < 600:
        run.notes.append("S1: the page scrolls only " + str(s9["scroll_px"]) + "px — not measured")
        return
    if not s9["inner"] and SMOOTH is not None:
        keep(run, SMOOTH.scroll_jank(page, px=3000))
        return
    if not s9["inner"]:
        run.notes.append("S1: smooth.py is not in this checkout — the document-scroller measure was skipped")
        return
    cdp = page.context.new_cdp_session(page)
    try:
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": 4})
        r = page.evaluate(SCROLL_FRAMES_IN, {"speed": 900, "px": 3000})
    finally:
        try:
            cdp.send("Emulation.setCPUThrottlingRate", {"rate": 1})
            cdp.detach()
        except PWError:
            pass
    if not r:
        return
    detail = ("S1 scroll (inside " + s9["by"] + ") at 4x CPU: p95 " + str(r["p95"]) + " ms, " + str(r["over50"]) + " frame(s) over 50 ms, worst "
              + str(r["max"]) + " ms (" + str(r["frames"]) + " frames)")
    keep(run, [("warn" if r["p95"] > 20 or r["over50"] else "note", "scroll-jank", detail, r)])


def s4_back(run: Run, page: Page, pg: PortalPage, s9: dict) -> None:
    """S4: scroll, follow the page's in-content link, press Back, compare. smooth.back_scroll for a
    document scroller; the inner box's scrollTop otherwise (the office shell's <main> persists)."""
    if not pg.back:
        return
    if s9["scroll_px"] < 500:
        run.notes.append("S4: the page scrolls only " + str(s9["scroll_px"]) + "px — Back not measured")
        return
    y = min(1200, s9["scroll_px"] - 100)
    if not s9["inner"]:
        if SMOOTH is not None:
            keep(run, SMOOTH.back_scroll(page, y=y, link=pg.back))
        else:
            run.notes.append("S4: smooth.py is not in this checkout — the document-scroller measure was skipped")
        return
    page.evaluate(SCROLL_BY, y)
    page.wait_for_timeout(600)
    before = page.evaluate("() => { const b = document.querySelector('[data-qa-scroller]'); return b ? Math.round(b.scrollTop) : -1; }")
    url0 = page.url
    href = page.evaluate(
        "(p) => { const re = new RegExp(p); const b = document.querySelector('[data-qa-scroller]') || document;"
        " const vis = (a) => { const r = a.getBoundingClientRect(); return r.height > 0 && r.top >= 0 && r.bottom <= innerHeight; };"
        " const a = [...b.querySelectorAll('a[href]')].find((x) => re.test(x.getAttribute('href') || '') && vis(x))"
        " || [...b.querySelectorAll('a[href]')].find((x) => re.test(x.getAttribute('href') || ''));"
        " if (!a) return null; a.click(); return a.getAttribute('href'); }", pg.back)
    if not href:
        run.notes.append("S4: no link matching " + pg.back + " on the page — Back not measured")
        page.evaluate(SCROLL_BY, 0)
        return
    try:
        page.wait_for_url(lambda u: u != url0, timeout=8000)
    except PWError:
        run.notes.append("S4: following " + href + " did not change the page — not measured")
        return
    page.wait_for_timeout(1200)
    page.go_back(wait_until="commit")
    page.wait_for_timeout(1500)
    after = page.evaluate("() => { const b = document.querySelector('[data-qa-scroller]'); return b ? Math.round(b.scrollTop) : -1; }")
    vals = {"before": before, "after": after, "via": href, "scroller": s9["by"]}
    if abs(after - before) > 24:
        finding(run, "warn", "back-scroll", "S4: Back from " + href + " landed at " + str(after) + "px in " + s9["by"] + ", not where the page was ("
                + str(before) + "px)", vals)
    else:
        run.notes.append("S4: Back from " + href + " returned to " + str(after) + "px (" + s9["by"] + ")")


# ── replay ─────────────────────────────────────────────────────────────────────────────────────


def effective_me(rec: Recording, role: str) -> dict:
    me = dict(DEFAULT_ME)
    got = rec.json_of("GET /me")
    if isinstance(got, dict) and got.get("role"):
        me.update({k: got[k] for k in ("email", "full_name", "features") if k in got})
    me.update({"role": role, "must_reset": False, "read_only": False})
    if role == "salesman":
        me["features"] = list(SALESMAN_FEATURES)
        if rec.index.get("rep") and not str(rec.index["rep"]).isdigit():
            me["full_name"] = str(rec.index["rep"])
    return me


def seed_cart(rec: Recording) -> list[dict] | None:
    """S12's cart: two lines of the recorded staff catalog (the first two in-stock codes)."""
    data = rec.json_of("GET /shop/catalog") or {}
    codes = [str(i["item_code"]) for i in (data.get("items") or []) if i.get("item_code")][:2]
    return [{"item_code": c, "qty": 3} for c in codes] or None


def plan_pages(rec: Recording, cfg: ReplayCfg) -> tuple[list[tuple[PortalPage, str]], list[str]]:
    """(page, route) to walk: what the recording walked, for this role, narrowed by --only; a page
    --only names that the recording lacks is walked anyway (its calls are misses)."""
    recorded = {p["key"]: p for p in rec.index.get("pages", []) if p.get("status", "walked") == "walked"}
    notes: list[str] = []
    out: list[tuple[PortalPage, str]] = []
    role_pages = pages_for(cfg.as_role)
    for pg in role_pages:
        if cfg.only and pg.key not in cfg.only:
            continue
        if pg.key in recorded:
            out.append((pg, recorded[pg.key].get("route") or pg.route))
        elif cfg.only:
            if pg.detail_from:
                notes.append(pg.key + ": a detail page the recording did not reach — skipped")
            else:
                out.append((pg, pg.route))
                notes.append(pg.key + ": not in the recording — walked anyway, its calls are misses")
    if cfg.only:
        unknown = cfg.only - {p.key for p in role_pages} - (set(CANARIES) if cfg.canaries else set())
        for k in sorted(unknown):
            notes.append(k + ": not a " + cfg.as_role + " page (python scripts/qa/portal_qa.py --list)")
    if cfg.canaries:
        for key, (path, _) in CANARIES.items():
            out.append((PortalPage(key, path, "selftest canary " + key, ("canary",)), path))
    return out, notes


def new_context(browser, vp: Viewport, theme: str, rp: Replayer, kv: dict, site: str, clock_ms: int | None):
    ctx = browser.new_context(viewport={"width": vp.width, "height": vp.height}, device_scale_factor=vp.dpr, is_mobile=vp.mobile,
                              has_touch=vp.touch, service_workers="block", locale="en-GB", color_scheme="dark" if theme == "dark" else "light",
                              reduced_motion="no-preference")
    remove = ["yq-shop-cart:staff", "yq-staff-customer"]
    ctx.add_init_script(INIT + "(" + json.dumps({"origin": site, "clockMs": clock_ms, "kv": kv, "remove": remove}) + ");")
    ctx.route("**/*", rp.on_route)
    return ctx


def shot(run: Run, page: Page, out: Path, vp: Viewport, theme: str, name: str = "") -> None:
    rel = "shots/" + str(vp.width) + "/" + theme + "/" + run.page + ("__" + name if name else "") + ".png"
    path = out / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(path), timeout=20000)
    except PWError as exc:
        run.notes.append("screenshot " + rel + " failed: " + str(exc)[:120])
        return
    run.shots.append({"path": rel, "page": run.page, "route": run.route, "width": vp.width, "theme": theme, "label": run.label})


def settle(page: Page, rp: Replayer, quiet_ms: int = 600, max_ms: int = 12000) -> None:
    """The page has loaded and asked for nothing for quiet_ms (bounded), its heading shows, and the
    shell's page fade has run. Not Playwright's networkidle: the portal's warm-up fetch('/health')
    never reads its body, and a fulfilled answer nobody reads never "finishes" — networkidle would
    wait out its whole timeout on every page."""
    try:
        page.wait_for_load_state("load", timeout=max_ms)
    except PWError:
        pass
    t0 = time.time()
    while (time.time() - t0) * 1000 < max_ms:
        page.wait_for_timeout(100)
        if (time.time() - rp.last_routed) * 1000 >= quiet_ms:
            break
    try:
        page.wait_for_selector("main h1, h1", timeout=4000, state="visible")
    except PWError:
        pass
    page.wait_for_timeout(450)   # the shell's 280 ms page fade, React Query's first render


def walk_page(browser_ctx, rp: Replayer, cfg: ReplayCfg, report: Report, vp: Viewport, theme: str, pg: PortalPage, route: str,
              site: str, lead_phone: bool, lead_any: bool, first_theme: bool) -> dict:
    run = Run(page=pg.key, route=route, viewport=vp.name, theme=theme, label=pg.label)
    page = browser_ctx.new_page()
    rp.page_of[page] = pg.key
    rp.current = pg.key
    net = Net(rp)
    net.attach(page)
    cast = Screencast(page)
    info: dict = {"heading": None, "s7": None, "s9": None}
    timing: dict[str, int] = {}
    t_mark = [time.time()]

    def lap(name: str) -> None:
        now = time.time()
        timing[name] = round((now - t_mark[0]) * 1000)
        t_mark[0] = now

    run.metrics["timing_ms"] = timing
    try:
        if not cfg.fail_api and pg.key not in CANARIES:
            cast.start()
        page.goto(site + route, wait_until="domcontentloaded", timeout=45000)
        lap("load")
        if cast.cdp:
            # only the frames up to the app's first paint matter: stop once the heading shows (or
            # the shell has stood for 1.5 s without one), so a page that animates for ever does not
            # stream frames through the whole settle
            try:
                page.wait_for_function("() => { const p = window.__qaPaint || {};"
                                       " return p.h1 || (p.root && performance.now() - p.root > 1500); }", timeout=9000, polling=50)
            except PWError:
                pass
            page.wait_for_timeout(200)
            cast.stop()
            lap("paint")
        settle(page, rp)
        lap("settle")
        if cast.frames:
            info["s7"] = s7_first_paint(run, page, cast, vp, theme)
            lap("s7")
        if cfg.probe_miss and not rp.probe_done:
            # --selftest: one request the fixture cannot hold, to prove the miss log end to end
            rp.probe_done = True
            page.evaluate("async (u) => { try { await fetch(u); } catch (e) {} }", rp.api + "/__qa/selftest-miss")
            page.wait_for_timeout(300)
        p = probe_scan(page, vp)
        info["heading"] = p.get("heading")
        run.metrics["shell"] = p.get("shell")
        run.metrics["heading"] = p.get("heading")
        common_checks(run, p)
        if pg.key not in CANARIES:
            info["s9"] = s9_scroller(run, p)
        lap("probe")
        shot(run, page, cfg.out, vp, theme)
        lap("shot")
        if cfg.fail_api:
            s10_fail_api(run, page)
            lap("s10")
        for line in sorted(rp.recorded_errors.get(pg.key, set())):
            finding(run, "warn", "recorded-error", "the recording holds an error answer (production answered so): " + line)
        if cfg.as_role == "salesman" and vp.klass == "phone" and not cfg.fail_api:
            try:
                s12_chrome(run, page)
            except PWError as exc:
                finding(run, "warn", "harness-error", "S12: " + str(exc)[:200])
        if cfg.smooth and first_theme and not cfg.fail_api and info["s9"]:
            try:
                if lead_phone:
                    s1_jank(run, page, info["s9"])
                if lead_any:
                    s4_back(run, page, pg, info["s9"])
            except PWError as exc:
                finding(run, "warn", "harness-error", "S1/S4: " + str(exc)[:200])
    except PWError as exc:
        finding(run, "fail", "page-crash", "Playwright error: " + str(exc)[:300])
    except Exception as exc:  # noqa: BLE001
        finding(run, "fail", "harness-error", type(exc).__name__ + ": " + str(exc)[:300])
    finally:
        cast.stop()
        net_checks(run, net, cfg.fail_api)
        try:
            page.close()
        except PWError:
            pass
    report.add(run)
    return info


def nav_chain(ctx, rp: Replayer, report: Report, vp: Viewport, theme: str, walk: list[tuple[PortalPage, str]], site: str) -> list[dict]:
    """S8: one tab, the menu from page to page in menu order; each hop timed from the click."""
    hops: list[dict] = []
    menu = [(pg, route) for pg, route in walk if pg.key not in CANARIES and not pg.detail_from and "?" not in route]
    if len(menu) < 2:
        return hops
    run = Run(page="nav", route="(menu)", viewport=vp.name, theme=theme, label="S8 click to paint")
    page = ctx.new_page()
    rp.page_of[page] = "nav"
    net = Net(rp)
    net.attach(page)
    try:
        page.goto(site + menu[0][1], wait_until="domcontentloaded", timeout=45000)
        settle(page, rp)
        prev = menu[0][0].key
        for pg, route in menu[1:]:
            rp.current = pg.key
            rp.page_of[page] = pg.key
            res = page.evaluate(NAV_HOP, {"href": route, "loaders": LOADERS, "timeout": 8000})
            if res.get("error") and vp.klass != "desktop":
                # the office drawer on a phone or tablet: open it, then measure from the link's click
                opener = page.locator('header button[title="Menu"]')
                if opener.count() and opener.first.is_visible():
                    opener.first.click()
                    page.wait_for_timeout(450)
                    res = page.evaluate(NAV_HOP, {"href": route, "loaders": LOADERS, "timeout": 8000})
            if res.get("error"):
                run.notes.append("S8 " + prev + " → " + pg.key + ": " + res["error"] + " — reloaded there instead")
                page.goto(site + route, wait_until="domcontentloaded", timeout=45000)
                settle(page, rp)
                prev = pg.key
                continue
            hop = {"width": vp.width, "theme": theme, "from": prev, "to": pg.key, "paint_ms": res.get("paint"), "settled_ms": res.get("settled"),
                   "loaders": res.get("loaders"), "kinds": res.get("kinds"), "heading": res.get("heading")}
            hops.append(hop)
            run.notes.append("S8 " + prev + " → " + pg.key + ": paint " + str(res.get("paint")) + " ms, settled " + str(res.get("settled"))
                             + " ms, loaders " + str(res.get("loaders")) + (" (" + ", ".join(res.get("kinds") or []) + ")" if res.get("kinds") else ""))
            page.wait_for_timeout(500)
            prev = pg.key
    except PWError as exc:
        finding(run, "warn", "harness-error", "S8 chain stopped: " + str(exc)[:200])
    finally:
        net_checks(run, net, False)
        try:
            page.close()
        except PWError:
            pass
    report.add(run)
    return hops


def summarize(report: Report, vp: Viewport, theme: str, infos: list[tuple[PortalPage, dict]], hops: list[dict]) -> None:
    """Shell-wide findings, one per width and theme, so a property of the shell is one line, not 40."""
    run = Run(page="summary", route="-", viewport=vp.name, theme=theme, label="the shell at this width and theme")
    s7 = [(pg, i["s7"]) for pg, i in infos if i.get("s7")]
    flash = [(pg, r) for pg, r in s7 if r["white_frames"] and r["dark_app"]]
    if flash:
        ms = [r["white_ms"] or 0 for _, r in flash]
        worst = max(flash, key=lambda x: x[1]["white_ms"] or 0)
        finding(run, "warn", "first-paint-white", "S7: a near-white frame is on screen before the dark app paints on " + str(len(flash)) + " of "
                + str(len(s7)) + " pages (median " + str(round(statistics.median(ms))) + " ms, longest " + str(worst[1]["white_ms"]) + " ms on "
                + worst[0].key + ") — the theme is applied after the first paint", {"pages": [pg.key for pg, _ in flash], "ms": ms})
    s9 = [(pg, i["s9"]) for pg, i in infos if i.get("s9")]
    inner = [(pg, s) for pg, s in s9 if s["inner"] and s["scroll_px"] > 0]
    if inner and vp.klass == "phone":
        finding(run, INNER_SCROLL_PHONE_LEVEL, "inner-scroller", "S9: on a " + str(vp.width) + " touch phone " + str(len(inner)) + " of " + str(len(s9))
                + " pages scroll inside " + inner[0][1]["by"] + ", not the document (no collapsing address bar, no tap-the-top-to-scroll, no browser Back restore)",
                {"pages": [pg.key for pg, _ in inner]})
    slow = [h for h in hops if (h["paint_ms"] or 10 ** 6) > NAV_MS_WARN]
    many = [h for h in hops if (h["loaders"] or 0) > NAV_LOADERS_WARN]
    if hops and (slow or many):
        paints = [h["paint_ms"] for h in hops if h["paint_ms"] is not None]
        worst = sorted(slow, key=lambda h: -(h["paint_ms"] or 10 ** 6))[:3]
        finding(run, "warn", "click-to-paint", "S8: " + str(len(slow)) + " of " + str(len(hops)) + " menu clicks take over " + str(NAV_MS_WARN)
                + " ms to the new heading (median " + (str(round(statistics.median(paints))) if paints else "—") + " ms; slowest "
                + ", ".join(h["from"] + "→" + h["to"] + " " + str(h["paint_ms"]) + " ms" for h in worst) + "); " + str(len(many))
                + " show more than " + str(NAV_LOADERS_WARN) + " loader", {"slow": slow, "many_loaders": many})
    if run.findings:
        report.add(run)


def replay(cfg: ReplayCfg, preview: Preview | None = None) -> Report:
    rec = Recording.open(cfg.rec_dir)
    cfg.out.mkdir(parents=True, exist_ok=True)
    api = api_of_dist(cfg.dist)
    sb_host = supabase_of_dist(cfg.dist, rec)
    sb_ref = sb_host.split(".")[0] if sb_host else None
    me = effective_me(rec, cfg.as_role)
    clock_ms = rec.recorded_ms() if cfg.clock == "recorded" else None
    now_s = (clock_ms // 1000) if clock_ms else int(time.time())
    walk, plan_notes = plan_pages(rec, cfg)
    main_js = re.search(r'src="(/assets/index-[^"]+\.js)"', (cfg.dist / "index.html").read_text(encoding="utf-8"))
    header = {"mode": "replay" + (" --fail-api" if cfg.fail_api else ""), "dist": str(cfg.dist), "build_entry": main_js.group(1) if main_js else None,
              "rec": str(rec.root), "recorded_at": rec.index.get("recorded_at"), "synthetic": bool(rec.index.get("synthetic")),
              "as": cfg.as_role, "widths": cfg.widths, "themes": cfg.themes, "fail_api": cfg.fail_api, "api": api, "supabase": sb_host,
              "clock": "recorded" if clock_ms else "real", "smooth_py": SMOOTH is not None}
    report = Report(cfg.out, header)
    setup = Run(page="setup", route="-", viewport="-", theme="-", label="harness setup")
    for msg in verify_routes(ROOT):
        finding(setup, "warn", "route-drift", msg)
    for msg in plan_notes:
        setup.notes.append(msg)
    if not sb_ref:
        finding(setup, "warn", "supabase", "no Supabase project found in the build — the fake session cannot be placed")
    report.add(setup)

    kv = {"yq-me-v1": json.dumps({"uid": UID, "me": me, "at": now_s * 1000}), "yq-role": me["role"], "yq-intro-v2": "done",
          "yq-collapsed": "0", "yq-sales-rail": "0"}
    if sb_ref:
        kv["sb-" + sb_ref + "-auth-token"] = json.dumps(fake_session(now_s))
    cart = seed_cart(rec) if cfg.as_role == "salesman" else None

    owned = preview is None
    pv = preview or Preview(cfg.dist, cfg.port, cfg.out / "preview.log")
    if owned:
        pv.__enter__()
    try:
        site = pv.base
        viewports = [viewport_for(w) for w in cfg.widths]
        lead_phone = next((v.name for v in viewports if v.klass == "phone"), None)
        lead_desk = next((v.name for v in viewports if v.klass == "desktop"), None)
        rps: list[Replayer] = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                for vp in viewports:
                    for ti, theme in enumerate(cfg.themes):
                        rp = Replayer(rec, api, sb_host, site, cfg, me)
                        rps.append(rp)
                        page_kv = dict(kv, **{"yq-theme": theme})
                        if cart and vp.klass == "phone":
                            page_kv["yq-shop-cart:staff"] = json.dumps(cart)
                        ctx = new_context(browser, vp, theme, rp, page_kv, site, clock_ms)
                        infos: list[tuple[PortalPage, dict]] = []
                        try:
                            for pg, route in walk:
                                print("· " + vp.name.ljust(9) + " " + theme.ljust(5) + " " + pg.key.ljust(18) + " " + route, flush=True)
                                infos.append((pg, walk_page(ctx, rp, cfg, report, vp, theme, pg, route, site,
                                                            vp.name == lead_phone, vp.name in (lead_phone, lead_desk), ti == 0)))
                            hops: list[dict] = []
                            if cfg.nav and ti == 0 and not cfg.fail_api:
                                print("· " + vp.name.ljust(9) + " " + theme.ljust(5) + " S8 menu chain", flush=True)
                                hops = nav_chain(ctx, rp, report, vp, theme, walk, site)
                                report.nav += hops
                            report.paint += [i["s7"] for _, i in infos if i.get("s7")]
                            summarize(report, vp, theme, infos, hops)
                        finally:
                            try:
                                ctx.close()
                            except PWError:
                                pass
            finally:
                browser.close()
    finally:
        if owned:
            pv.__exit__(None, None, None)

    merged: dict[tuple[str, str], dict] = {}
    for rp in rps:
        for m in rp.misses_list():
            k = (m["kind"], m["key"])
            if k in merged:
                merged[k]["count"] += m["count"]
                merged[k]["pages"] += [p for p in m["pages"] if p not in merged[k]["pages"]]
            else:
                merged[k] = {**m, "pages": list(m["pages"])}
    order = {"miss": 0, "near": 1, "asset": 2, "external": 3}
    report.misses = sorted(merged.values(), key=lambda m: (order.get(m["kind"], 9), -m["count"], m["key"]))
    handler_errors = sorted({e for rp in rps for e in rp.handler_errors})
    if handler_errors:
        bad = Run(page="harness", route="-", viewport="-", theme="-", label="the replay's request handler")
        for e in handler_errors[:10]:
            finding(bad, "fail", "harness-error", "a request the replay could not answer from the recording (answered 500): " + e)
        report.add(bad)
    report.header.update({"answers_served": sum(r.hits for r in rps), "writes_blocked": sum(len(r.blocked) for r in rps),
                          "writes": sorted({b["method"] + " " + b["path"] for r in rps for b in r.blocked}),
                          "quotes_priced": sum(r.quoted for r in rps), "injected_500": sum(r.injected for r in rps)})
    report.write()
    return report


def print_summary(report: Report) -> None:
    h = report.header
    print("")
    print("runs " + str(len(report.runs)) + " · shots " + str(sum(len(r.shots) for r in report.runs)) + " · answers served " + str(h.get("answers_served"))
          + " · misses " + str(len([m for m in report.misses if m["kind"] in ("miss", "near")])) + " · writes blocked " + str(h.get("writes_blocked"))
          + (" · injected 500s " + str(h.get("injected_500")) if h.get("fail_api") else ""))
    print("HARD FAILURES: " + str(len(report.fails)) + " · warnings: " + str(len(report.warns)))
    for f in report.fails[:40]:
        print("  FAIL " + f["check"] + " · " + f["viewport"] + " · " + f["theme"] + " · " + f["route"] + " · " + f["detail"][:160])
    print("wrote " + str(report.out / "results.json") + ", index.md and misses.json")


# ── selftest ───────────────────────────────────────────────────────────────────────────────────

# --selftest, phase 0: every way a page can write, fired at the "API" through the record mode's own
# route. A real fetch, a form POST (a navigation), a keepalive fetch, a beacon, an XHR.
GUARD_FIRE = r"""
async (api) => {
  const out = [];
  const go = async (name, fn) => { try { await fn(); out.push(name); } catch (e) { out.push(name + ' (' + String(e).slice(0, 40) + ')'); } };
  await go('fetch POST', () => fetch(api + '/qa-write/fetch-post', { method: 'POST', body: '{}', headers: { 'content-type': 'application/json' } }));
  await go('fetch PUT', () => fetch(api + '/qa-write/fetch-put', { method: 'PUT', body: '{}' }));
  await go('fetch PATCH', () => fetch(api + '/qa-write/fetch-patch', { method: 'PATCH', body: '{}' }));
  await go('fetch DELETE', () => fetch(api + '/qa-write/fetch-delete', { method: 'DELETE' }));
  await go('keepalive POST', () => fetch(api + '/qa-write/keepalive', { method: 'POST', body: '{}', keepalive: true }));
  await go('sendBeacon', async () => { if (!navigator.sendBeacon(api + '/qa-write/beacon', '{}')) throw new Error('refused'); });
  await go('XHR POST', () => new Promise((ok, bad) => { const x = new XMLHttpRequest(); x.open('POST', api + '/qa-write/xhr');
    x.onload = ok; x.onerror = () => bad(new Error('xhr')); x.send('{}'); }));
  await go('GET', () => fetch(api + '/qa-read?b=2&a=1', { headers: { Authorization: 'Bearer qa-guard-token' } }).then((r) => r.text()));
  await new Promise((r) => setTimeout(r, 800));
  return out;
}
"""


def record_guard(tmp: Path) -> dict:
    """The record mode's safety, proven on this machine with nothing real: a throw-away listener on a
    free port plays the API (it serves the page too, so no other port is touched); the Recorder's
    route sits in front of a headless browser; every write shape is fired at it. The listener must
    receive GETs only; every write must be answered in the browser; the GET must be saved with its
    signed-URL token and JWT masked and no request header anywhere."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    got: list[tuple[str, str]] = []
    secret_jwt = "eyJhbGciOiJub25lIn0.eyJzdWIiOiJxYS1ndWFyZCJ9.c2lnbmF0dXJlLXFhLWd1YXJk"

    class Api(BaseHTTPRequestHandler):
        def log_message(self, *a: Any) -> None:
            pass

        def _answer(self) -> None:
            n = int(self.headers.get("content-length") or 0)
            if n:
                self.rfile.read(n)
            got.append((self.command, self.path))
            if self.path.startswith("/page"):
                body, ctype = b"<!doctype html><html><body><main><h1>QA guard</h1></main></body></html>", "text/html"
            else:
                body = json.dumps({"qa": "guard", "photo": "https://abcdefghijklmnop.supabase.co/storage/v1/object/sign/x/a.jpg?token=QASECRET1&w=1",
                                   "jwt": secret_jwt}).encode()
                ctype = "application/json"
            self.send_response(200)
            self.send_header("content-type", ctype)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = _answer

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Api)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    api = "http://127.0.0.1:" + str(srv.server_address[1])
    rec = Recording(tmp / "rec", {"format": REC_FORMAT, "entries": {}})
    recorder = Recorder(rec, api, "http://127.0.0.1:9")
    recorder.recording = True
    recorder.current = "guard"
    fired: list[str] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                ctx = browser.new_context(service_workers="block")
                ctx.route("**/*", recorder.on_route)
                page = ctx.new_page()
                page.goto(api + "/page", wait_until="domcontentloaded", timeout=30000)
                fired = page.evaluate(GUARD_FIRE, api)
                page.evaluate("(api) => { const f = document.createElement('form'); f.method = 'POST'; f.action = api + '/qa-write/form';"
                              " document.body.appendChild(f); f.submit(); }", api)
                page.wait_for_timeout(1500)
                fired.append("form POST")
            finally:
                browser.close()
    finally:
        srv.shutdown()
    saved = rec.entries.get("GET /qa-read?a=1&b=2")
    text = (rec.root / saved["file"]).read_text(encoding="utf-8") if saved else ""
    # the GET carried "Authorization: Bearer qa-guard-token": no file may hold it
    leaked_headers = [f.name for f in rec.root.rglob("*") if f.is_file() and "qa-guard-token" in f.read_text(encoding="utf-8", errors="ignore")]
    return {"fired": fired, "reached": [m + " " + p for m, p in got if m not in ("GET", "HEAD")],
            "blocked": sorted({b["method"] + " " + b["path"] for b in recorder.blocked}), "saved": bool(saved),
            "masked": bool(saved) and "QASECRET1" not in text and secret_jwt not in text and "qa-masked" in text,
            "leaked_headers": leaked_headers, "errors": recorder.errors}


def selftest(args) -> int:
    """Proves the record mode's write guard (record_guard), then replays the committed SYNTHETIC
    fixture against web/dist (built beforehand: nothing is built here) and checks the harness end to
    end: screens made, misses logged, the S8 chain measured; under --fail-api the canary that must
    fail is caught while the honest one passes; as a salesman on a phone the salesman app renders,
    the seeded cart is priced and S12 measures the chrome."""
    dist = Path(args.dist).resolve() if args.dist else WEB / "dist"
    if not (dist / "index.html").exists():
        print("no build at " + str(dist) + " — run `npm run build` in web/ first (the selftest builds nothing)", file=sys.stderr)
        return 2
    out = Path(args.out).resolve() if args.out else Path(tempfile.mkdtemp(prefix="portal_qa_selftest_"))
    pages = ["receivables", "margins", "picklist", "account", "orders"]
    rep_pages = ["shop", "account"]
    t0 = time.time()
    checks: list[tuple[bool, str]] = []
    print("— 0/3 the record mode's write guard, against a throw-away local listener", flush=True)
    guard = record_guard(out / "guard")
    checks.append((not guard["reached"], "--record: no write reached the API (" + str(len(guard["fired"])) + " shapes fired: " + ", ".join(guard["fired"])
                   + ")" + ("" if not guard["reached"] else " — REACHED: " + ", ".join(guard["reached"]))))
    checks.append((len(guard["blocked"]) >= 8, "--record: every write was answered inside the browser (" + str(len(guard["blocked"])) + " blocked)"))
    checks.append((guard["saved"] and guard["masked"] and not guard["leaked_headers"], "--record: the GET was saved under its sorted key, its signed-URL"
                   " token and JWT masked, no request header in any file"))
    with Preview(dist, args.port, out / "preview.log") as pv:
        print("— 1/3 replay as admin, 1440, light + dark", flush=True)
        normal = replay(ReplayCfg(dist=dist, rec_dir=FIXTURE, out=out / "replay", widths=[1440], themes=["light", "dark"], only=set(pages),
                                  port=args.port, smooth=True, probe_miss=True), preview=pv)
        print("— 2/3 --fail-api, with the two canaries", flush=True)
        failing = replay(ReplayCfg(dist=dist, rec_dir=FIXTURE, out=out / "fail-api", widths=[1440], themes=["light"], only=set(pages),
                                   port=args.port, fail_api=True, smooth=False, nav=False, canaries=True), preview=pv)
        print("— 3/3 --as salesman, 390 phone, a seeded cart", flush=True)
        rep = replay(ReplayCfg(dist=dist, rec_dir=FIXTURE, out=out / "salesman", as_role="salesman", widths=[390], themes=["light"],
                               only=set(rep_pages), port=args.port, smooth=False), preview=pv)
    shots = {s["page"] + "/" + s["theme"]: (normal.out / s["path"]) for r in normal.runs for s in r.shots}
    for p in pages:
        for th in ("light", "dark"):
            f = shots.get(p + "/" + th)
            checks.append((bool(f and f.exists() and f.stat().st_size > 2000), "screenshot " + p + " " + th + (" → " + str(f) if f else "")))
    crashes = [f for f in normal.fails + failing.fails if f["check"] in ("page-crash", "harness-error")]
    checks.append((not crashes, "no page crash or harness error" + ("" if not crashes else ": " + crashes[0]["detail"][:120])))
    misses = json.loads((normal.out / "misses.json").read_text(encoding="utf-8"))
    probe_hit = any(m["key"] == "GET /__qa/selftest-miss" for m in misses)
    checks.append((probe_hit, "misses.json logs the deliberate miss GET /__qa/selftest-miss (" + str(len(misses)) + " entries in all)"))
    console_from_miss = [f for f in normal.fails if f["check"] == "console-error" and "selftest-miss" in f["detail"]]
    checks.append((not console_from_miss, "a miss is not double-counted as a console error"))
    hops = [h for h in normal.nav if h.get("paint_ms") is not None]
    checks.append((len(hops) >= 2, "S8 measured " + str(len(hops)) + " menu hops: " + "; ".join(h["from"] + "→" + h["to"] + " " + str(h["paint_ms"]) + " ms" for h in hops)))
    s7 = [p for p in normal.paint if p]
    checks.append((len(s7) >= len(pages), "S7 sampled the first paint on " + str(len(s7)) + " page loads"))
    s9 = [r for r in normal.runs if r.metrics.get("scroller")]
    checks.append((len(s9) >= len(pages), "S9 named the scroller on " + str(len(s9)) + " page loads"))
    s4 = [n for r in normal.runs if r.page == "orders" for n in r.notes if n.startswith("S4: Back")] + \
         [f["detail"] for f in normal.warns if f["check"] == "back-scroll"]
    checks.append((bool(s4), "S4 followed a purchase order and came Back: " + (s4[0][:110] if s4 else "not measured")))
    caught = [f for f in failing.fails if f["page"] == "canary_false_empty" and f["check"].startswith("fail-api")]
    checks.append((bool(caught), "--fail-api catches the deliberately failing page (canary_false_empty): " + (caught[0]["detail"][:90] if caught else "NOT caught")))
    honest = [f for f in failing.fails if f["page"] == "canary_error_ok" and f["check"].startswith("fail-api")]
    checks.append((not honest, "--fail-api passes the honest error page (canary_error_ok)"))
    s10 = [r for r in failing.runs if r.page in pages and "fail_api" in r.metrics]
    checks.append((len(s10) == len(pages), "S10 ran on all " + str(len(pages)) + " fixture pages"))
    checks.append((int(failing.header.get("injected_500") or 0) > 0, "--fail-api answered " + str(failing.header.get("injected_500")) + " API calls with 500"))
    rep_runs = [r for r in rep.runs if r.page in rep_pages]
    shells = sorted({str(r.metrics.get("shell")) for r in rep_runs})
    checks.append((shells == ["salesman"], "--as salesman renders the salesman app on " + ", ".join(r.page for r in rep_runs) + " (shell: " + ", ".join(shells) + ")"))
    chrome = [(r.page, (r.metrics.get("chrome") or {}).get("total")) for r in rep_runs]
    checks.append((all(c is not None for _, c in chrome) and len(chrome) == len(rep_pages), "S12 measured the fixed/sticky chrome: "
                   + ", ".join(p + " " + str(c) + " px" for p, c in chrome)))
    checks.append((int(rep.header.get("quotes_priced") or 0) > 0, "the seeded cart was priced by the replay (" + str(rep.header.get("quotes_priced"))
                   + " quote call(s) answered locally, never sent)"))
    rep_hops = [h for h in rep.nav if h.get("paint_ms") is not None]
    checks.append((len(rep_hops) >= 1, "S8 measured the salesman tab bar: " + "; ".join(h["from"] + "→" + h["to"] + " " + str(h["paint_ms"]) + " ms" for h in rep_hops)))
    rep_crash = [f for f in rep.fails if f["check"] in ("page-crash", "harness-error", "page-error")]
    checks.append((not rep_crash, "no crash in the salesman pass" + ("" if not rep_crash else ": " + rep_crash[0]["detail"][:120])))

    print("")
    print("SELFTEST — " + str(round(time.time() - t0)) + " s · output " + str(out))
    for ok, what in checks:
        print("  " + ("PASS " if ok else "FAIL ") + what)
    print("")
    print("what the replay found (fixture pages, 1440, light + dark):")
    for f in normal.fails + normal.warns:
        print("  " + f["level"].upper().ljust(5) + " " + f["check"] + " · " + f["page"] + " · " + f["theme"] + " · " + f["detail"][:150])
    print("what --fail-api found:")
    for r in failing.runs:
        if r.page in pages or r.page in CANARIES:
            s = [f["check"] for f in r.findings if f["check"].startswith("fail-api")]
            print("  " + r.page.ljust(20) + " " + ("FAIL " + ", ".join(s) if s else "ok   " + "; ".join(n for n in r.notes if n.startswith("S10"))[:110]))
    print("what the salesman pass found (390 phone, light):")
    for f in rep.fails + rep.warns:
        print("  " + f["level"].upper().ljust(5) + " " + f["check"] + " · " + f["page"] + " · " + f["detail"][:150])
    ok = all(c for c, _ in checks)
    print("")
    print("SELFTEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


# ── main ───────────────────────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description="YQ Ops Portal QA harness: record once with the owner's sign-in, replay without it")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--record", action="store_true", help="open a headed Chrome, wait for the owner's sign-in, walk and record every page")
    mode.add_argument("--replay", action="store_true", help="serve a build and walk it against a recording")
    mode.add_argument("--selftest", action="store_true", help="replay the committed synthetic fixture against web/dist and check the harness")
    mode.add_argument("--list", action="store_true", help="print the pages and exit")
    ap.add_argument("--base", default="https://ops.yqmarketplace.com", help="--record: the portal to sign in to")
    ap.add_argument("--api", default="https://api.yqmarketplace.com", help="--record: the API whose GET answers are recorded")
    ap.add_argument("--rep", default="", help="--record: also fetch the endpoints that take ?rep= as this rep (a Focus name or a salesman id), for --as salesman")
    ap.add_argument("--wait-min", type=float, default=10, help="--record: minutes to wait for the sign-in (default 10)")
    ap.add_argument("--out", default="", help="output folder: --record writes <out>/rec/; --replay writes results.json, index.md, misses.json, shots/")
    ap.add_argument("--dist", default="", help="--replay: a built portal folder (web/dist)")
    ap.add_argument("--rec", default="", help="--replay: the folder that holds rec/ (what --record wrote)")
    ap.add_argument("--as", dest="as_role", choices=("admin", "salesman"), default="admin", help="--replay: who the fake /me says you are")
    ap.add_argument("--widths", default="390,1024,1440", help="--replay: comma-separated widths; under 768 is a touch phone (390x844)")
    ap.add_argument("--themes", default="light,dark", help="--replay: light, dark or both")
    ap.add_argument("--only", default="", help="comma-separated page keys (--list)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="the vite preview port (default 5175)")
    ap.add_argument("--fail-api", action="store_true", help="--replay: answer every API call of the page with 500 (S10)")
    ap.add_argument("--clock", choices=("recorded", "real"), default="recorded", help="--replay: the page's date — the recording's (default) or today")
    ap.add_argument("--no-smooth", action="store_true", help="--replay: skip S1 scroll jank and S4 Back")
    ap.add_argument("--no-nav", action="store_true", help="--replay: skip the S8 menu chain")
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # a cp1252 console must not crash on "→"
        except (AttributeError, ValueError):
            pass

    if args.list:
        for p in PAGES:
            print(p.key.ljust(18), p.route.ljust(30), ",".join(p.roles).ljust(16), p.label)
        return 0
    if args.record:
        if not args.out:
            ap.error("--record needs --out (a business_data/ folder)")
        return record(args)
    if args.selftest:
        return selftest(args)

    if not (args.dist and args.rec and args.out):
        ap.error("--replay needs --dist, --rec and --out")
    dist, out = Path(args.dist).resolve(), Path(args.out).resolve()
    if not (dist / "index.html").exists():
        print("no build at " + str(dist) + " (npm run build in web/)", file=sys.stderr)
        return 2
    rec = Recording.open(Path(args.rec).resolve())
    if not rec.index.get("synthetic"):
        why = require_private(out)
        if why:
            print("refusing: a replay of a real recording holds real data — " + why, file=sys.stderr)
            return 2
    themes = [t.strip() for t in args.themes.split(",") if t.strip() in ("light", "dark")]
    cfg = ReplayCfg(dist=dist, rec_dir=Path(args.rec).resolve(), out=out, as_role=args.as_role,
                    widths=[int(w) for w in args.widths.split(",") if w.strip()], themes=themes or ["light"],
                    only={k.strip() for k in args.only.split(",") if k.strip()}, port=args.port, fail_api=args.fail_api,
                    clock=args.clock, smooth=not args.no_smooth, nav=not args.no_nav)
    report = replay(cfg)
    print_summary(report)
    return 1 if report.fails else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("interrupted — the preview server and the browser are closed", file=sys.stderr)
        sys.exit(130)
