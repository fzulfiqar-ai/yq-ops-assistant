"""UI-vs-source check (R2c; plan §9 Step 2 item 7, critic #12).

    python -m scripts.ui_source_check                      # production, 25 SKUs, 5 reps
    python -m scripts.ui_source_check --skus 40 --reps 8 --seed 7
    python -m scripts.ui_source_check --no-browser         # files vs API payload / DB only

What it compares — the SOURCE FILES the owner dropped (never the database's copy of them) against
what a merchant and a rep actually see:

  1. PRICE.  The latest MASellingPriceBook in the newest drop folder that carries one, read with the
     repo's own parser (scripts/ingest.parse_pricebook, month-first dates) and reduced with the live
     book's own rule (v_price_list_by_book: Authorized, rate > 0, in force today, the base row
     without a warehouse first, then the latest start date, then the latest row). Compared with the
     "BHD x.xxx /pc" rendered on the marketplace grid card AND in the product panel (/p/{code}).
  2. STOCK STATUS.  The newest Stock_balance_by_warehouse file, matched to catalog codes with the
     same precedence as v_catalog_stock_rows (manual map, product alias, longest normalised prefix
     over ALL catalog codes) — absent from the file = sold out (Focus omits zero-balance rows).
     Status = in stock / low (<= shop_low_stock_units) / sold out, compared with the card's
     data-stock and its chips, and with the panel's status chip.
  3. REP MONTH-TO-DATE (ex-VAT, Accessories, giveaways out — the owner's kickback basis).  Computed
     from the newest Sales_day_book FILE (salesman = Summary_sales_register's, else the day book's
     "Warehouse Name"; division from the item-alias map, SIM by name; net = Taxable, else Gross/1.1),
     compared with app.shop.rep_month_sales — the number the rep's Today card is built from.

Safety: GET-only on the web and the API. Every non-GET browser request (funnel events, vitals,
restock asks, orders, quotes) is answered by a local stub, exactly like scripts/qa/market_qa.py,
and counted in the report; the database is opened READ ONLY (SET TRANSACTION READ ONLY) for the
reference tables only (catalog codes, aliases, the stock map, one setting, the reps' target rows).
No order, event, row or file on the server is ever written. Nothing here prints a secret or a
merchant's details: the report names SKUs and reps (staff), never customers.

Output: business_data/ui_source_checks/<ts>.md (gitignored) with every comparison, plus a one-line
verdict on stdout. Exit 0 = every comparison matched, 1 = at least one mismatch, 2 = could not run.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import random
import re
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

DEFAULT_BASE = "https://yqmarketplace.com"
DEFAULT_API = "https://yq-ops-assistant.onrender.com"
FILS = Decimal("0.001")
ZERO = Decimal(0)
DROP_DIR = re.compile(r"^\d{6}$")              # DDMMYY, the owner's drop folders
CONFLICT = re.compile(r"-Conflict|_conflict", re.I)
STOCK_LABELS = {"in_stock": "In stock", "low_stock": "Only a few left", "out_of_stock": "Sold out"}
SIM_NAME = re.compile(r"sim|batelco", re.I)
CART_KEY = "yq-shop-cart:market"
SPLASH_KEY = "yq-splash-session"


# ── small helpers ──────────────────────────────────────────────────────────────

def q3(x) -> Decimal:
    return Decimal(str(x)).quantize(FILS, rounding=ROUND_HALF_UP)


def bahrain_today() -> date:
    return (datetime.now(timezone.utc) + timedelta(hours=3)).date()


def load_env() -> Path | None:
    """The first .env walking up from the repo root (a worktree inherits the main checkout's)."""
    from dotenv import load_dotenv
    for p in [ROOT, *ROOT.parents]:
        f = p / ".env"
        if f.is_file():
            load_dotenv(f)
            return f
    return None


def default_data_root() -> Path:
    for p in [ROOT, *ROOT.parents]:
        if any(c.is_dir() and DROP_DIR.match(c.name) for c in p.iterdir()):
            return p
    return ROOT


def drop_date(name: str) -> date | None:
    try:
        return datetime.strptime(name, "%d%m%y").date()
    except ValueError:
        return None


def find_drops(data_root: Path) -> list[tuple[date, Path]]:
    out = []
    for c in data_root.iterdir():
        if c.is_dir() and DROP_DIR.match(c.name):
            d = drop_date(c.name)
            if d:
                out.append((d, c))
    return sorted(out, key=lambda t: t[0], reverse=True)


def newest_file(drops: list[tuple[date, Path]], needle: str) -> tuple[Path, date] | None:
    """The newest drop folder that has a file whose name contains `needle` (case-insensitive);
    inside it the highest '(n)' export counter, then the newest mtime. Conflict copies are skipped."""
    for d, folder in drops:
        cands = [p for p in folder.rglob("*.xlsx")
                 if needle.lower() in p.name.lower() and not CONFLICT.search(p.name) and not p.name.startswith("~$")]
        if cands:
            def key(p: Path):
                m = re.search(r"\((\d+)\)", p.name)
                return (int(m.group(1)) if m else -1, p.stat().st_mtime)
            return max(cands, key=key), d
    return None


def rel(p: Path, root: Path) -> str:
    try:
        return str(p.relative_to(root))
    except ValueError:
        return str(p)


def mtime(p: Path) -> str:
    return datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")


# ── the source files ───────────────────────────────────────────────────────────

def price_from_book(path: Path, today: date) -> tuple[dict[str, dict], dict]:
    """sku_code -> the row v_price_list_by_book would pick for MA_base today (rate as Decimal)."""
    import ingest
    grid = ingest.read_grid(path)
    rows = ingest.parse_pricebook(grid, path.name, "MA_base")
    live = []
    t = today.isoformat()
    for i, r in enumerate(rows):
        if str(r.get("status") or "").strip().lower() != "authorized":
            continue
        if r.get("rate_bhd") is None or float(r["rate_bhd"]) <= 0:
            continue
        if not r.get("start_date") or r["start_date"] > t:
            continue
        if r.get("end_date") and r["end_date"] < t:
            continue
        live.append((i, r))
    by: dict[str, dict] = {}
    for i, r in sorted(live, key=lambda ir: (ir[1]["warehouse_name"] is None, ir[1]["start_date"], ir[0]), reverse=True):
        code = str(r.get("sku_code") or "").strip()
        if code and code not in by:
            by[code] = {"rate": q3(r["rate_bhd"]), "start": r["start_date"], "end": r["end_date"],
                        "warehouse": r["warehouse_name"], "row": i + 2}
    meta = {"rows": len(rows), "live_rows": len(live), "skus": len(by),
            "statuses": sorted({str(r.get("status")) for r in rows})}
    return by, meta


def stock_rows(path: Path) -> tuple[list[dict], str | None]:
    import ingest
    grid = ingest.read_grid(path)
    rows = ingest.parse_stock_balance(grid, path.name)
    as_of = next((r["as_of_date"] for r in rows if r.get("as_of_date")), None) or ingest.report_date_from_title(grid)
    return rows, as_of


def norm_key(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def stock_by_code(rows: list[dict], all_codes: list[str], alias_sku: dict[str, str], manual: dict[str, str]) -> dict[str, dict]:
    """v_catalog_stock_rows in Python: per stock row the catalog code it belongs to (manual map, then
    product alias, then the LONGEST normalised code prefix over every catalog code), summed per code."""
    code_set = set(all_codes)
    keyed = sorted(((norm_key(c), c) for c in all_codes if norm_key(c)), key=lambda t: (-len(t[0]), t[1]))
    out: dict[str, dict] = defaultdict(lambda: {"qty": ZERO, "rows": [], "source": set()})
    for r in rows:
        name = r.get("item_name") or ""
        code = manual.get(name)
        src = "manual" if code else None
        if not code:
            a = alias_sku.get(name)
            if a and a in code_set:
                code, src = a, "alias"
        if not code:
            nk = norm_key(name)
            for k, c in keyed:
                if nk.startswith(k):
                    code, src = c, "prefix"
                    break
        if not code:
            continue
        out[code]["qty"] += Decimal(str(r.get("net_qty") or 0))
        out[code]["rows"].append(name)
        out[code]["source"].add(src)
    return out


def status_for(qty: Decimal, low_units: int) -> str:
    if qty <= 0:
        return "out_of_stock"
    if qty <= low_units:
        return "low_stock"
    return "in_stock"


def daybook(path: Path) -> list[dict]:
    import ingest
    return ingest.parse_order_lines(ingest.read_grid(path), path.name)


def register(path: Path | None) -> dict[str, str]:
    if not path:
        return {}
    import ingest
    rows = ingest.parse_orders(ingest.read_grid(path), path.name)
    return {r["invoice_no"]: r["salesman"] for r in rows if r.get("invoice_no") and r.get("salesman")}


def line_division(item_name: str | None, alias_division: dict[str, str | None]) -> str:
    """v_sales.division: the item group's division when it is one of the four, else SIM by name,
    else Accessories."""
    name = item_name or ""
    d = alias_division.get(name)
    if d in ("SIM", "Giveaway", "Devices", "Accessories"):
        return d
    if SIM_NAME.search(name):
        return "SIM"
    return "Accessories"


def line_is_giveaway(ln: dict, alias_division: dict[str, str | None]) -> bool:
    if alias_division.get(ln.get("item_name") or "") == "Giveaway":
        return True
    return float(ln.get("rate_bhd") or 0) == 0 and float(ln.get("gross_bhd") or 0) == 0 and float(ln.get("quantity") or 0) > 0


def line_net(ln: dict) -> Decimal | None:
    """v_sales.net_bhd = COALESCE(taxable_bhd, gross_bhd / 1.1); a line with neither adds nothing."""
    if ln.get("taxable_bhd") is not None:
        return Decimal(str(ln["taxable_bhd"]))
    if ln.get("gross_bhd") is not None:
        return Decimal(str(ln["gross_bhd"])) / Decimal("1.1")
    return None


def rep_matches(salesman: str | None, focus_name: str) -> bool:
    s = salesman or ""
    return s == focus_name or s.startswith(focus_name + " - ")


def rep_mtd_from_file(lines: list[dict], reg: dict[str, str], focus_name: str, period: str,
                      alias_division: dict[str, str | None], through: str | None = None) -> tuple[Decimal, int]:
    total, n = ZERO, 0
    for ln in lines:
        d = ln.get("line_date") or ""
        if not d.startswith(period) or (through and d > through):
            continue
        salesman = reg.get(ln.get("invoice_no") or "") or ln.get("warehouse_name")
        if not rep_matches(salesman, focus_name):
            continue
        if line_division(ln.get("item_name"), alias_division) != "Accessories":
            continue
        if line_is_giveaway(ln, alias_division):
            continue
        net = line_net(ln)
        if net is None:
            continue
        total += net
        n += 1
    return total, n


# ── read-only database reference tables ────────────────────────────────────────

def db_reference() -> dict:
    """Catalog codes, the stock map, item aliases with their division, the low-stock setting and
    the reps' target rows — SELECTs inside one READ ONLY transaction; nothing else is touched."""
    import psycopg
    out: dict = {}
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=30) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("select item_code from catalog_items")
            out["codes"] = [r[0] for r in cur.fetchall()]
            try:
                cur.execute("select stock_item_name, item_code from catalog_stock_map")
                out["manual"] = {r[0]: r[1] for r in cur.fetchall()}
            except Exception:  # noqa: BLE001 — the map is optional
                conn.rollback()
                conn.read_only = True
                cur.execute("SET TRANSACTION READ ONLY")
                out["manual"] = {}
            cur.execute("""select pa.alias_text, p.sku_code, c.division
                             from product_aliases pa
                             join products p on p.id = pa.product_id
                             left join categories c on c.id = p.category_id""")
            rows = cur.fetchall()
            out["alias_sku"] = {r[0]: r[1] for r in rows if r[1]}
            out["alias_division"] = {r[0]: r[2] for r in rows}
            cur.execute("select value from app_settings where key = 'shop_low_stock_units'")
            r = cur.fetchone()
            out["low_units"] = int(float(r[0])) if r and r[0] else 10
            cur.execute("select salesman, period from salesman_targets order by salesman, period desc")
            out["target_names"] = sorted({r[0] for r in cur.fetchall() if r[0]})
            cur.execute("select max(sale_date)::text from v_sales")
            out["db_sale_date"] = (cur.fetchone() or [None])[0]
            cur.execute("select max(as_of_date)::text from stock_balance")
            out["db_stock_as_of"] = (cur.fetchone() or [None])[0]
    return out


# ── the marketplace ────────────────────────────────────────────────────────────

def get_json(url: str, timeout: int = 120) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "yq-ui-source-check/1 (GET only)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def sample_codes(items: list[dict], n: int, seed: int) -> list[str]:
    """A seeded sample across the three stock states, so sold-out and low lines are always in it."""
    rng = random.Random(seed)
    pool = list(items)
    rng.shuffle(pool)
    by = defaultdict(list)
    for it in pool:
        by[it.get("stock_status") or "in_stock"].append(it["item_code"])
    quota = {"out_of_stock": max(1, n * 3 // 10), "low_stock": max(1, n // 6)}
    picked: list[str] = []
    for st, k in quota.items():
        picked += by[st][:k]
    for code in by["in_stock"] + by["low_stock"][quota["low_stock"]:] + by["out_of_stock"][quota["out_of_stock"]:]:
        if len(picked) >= n:
            break
        if code not in picked:
            picked.append(code)
    return picked[:n]


GRID_JS = """
() => Array.from(document.querySelectorAll('article[data-stock]')).map((a) => {
  const kick = a.querySelector('span.tnum.text-ink-2');
  const text = (a.textContent || '').replace(/\\s+/g, ' ');
  const m = text.match(/BHD\\s*(\\d+\\.\\d{3})\\s*\\/pc/);
  const leaves = Array.from(a.querySelectorAll('*')).filter((e) => !e.children.length).map((e) => (e.textContent || '').trim());
  return {
    code: kick ? kick.textContent.trim() : null,
    price: m ? m[1] : null,
    stock: a.getAttribute('data-stock'),
    soldOut: leaves.some((t) => /^Sold out/.test(t)),
    low: leaves.some((t) => t === 'Only a few left'),
    compact: a.classList.contains('cv-compact'),
  };
})
"""

PANEL_JS = """
() => {
  const d = document.querySelector('[role="dialog"]');
  if (!d) return null;
  const els = Array.from(d.querySelectorAll('span,p,div,button,td'));
  const priceEl = els.find((e) => e.classList.contains('text-2xl') && /^BHD \\d+\\.\\d{3}$/.test((e.textContent || '').trim()));
  const codeEl = d.querySelector('p span.tnum.text-ink');
  const statusEl = els.find((e) => e.children.length <= 2 && /^(In stock|Only a few left|Sold out)/.test((e.textContent || '').trim()) && (e.textContent || '').trim().length < 48);
  return {
    code: codeEl ? codeEl.textContent.trim() : null,
    price: priceEl ? priceEl.textContent.trim().replace('BHD ', '') : null,
    status: statusEl ? statusEl.textContent.trim() : null,
    onRequest: /Price on request/.test(d.textContent || ''),
  };
}
"""


class Browser:
    """Chromium via Playwright, GET only: every other method is answered locally and counted."""

    def __init__(self, base: str, headed: bool = False):
        from playwright.sync_api import sync_playwright
        self.base = base.rstrip("/")
        self.stubbed: list[str] = []
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(headless=not headed)
        self.ctx = self.browser.new_context(viewport={"width": 1440, "height": 900}, service_workers="block",
                                            locale="en-GB", user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                                                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                                                                        "Chrome/128.0 Safari/537.36 yq-ui-source-check qa"))
        self.ctx.add_init_script(
            "(() => { try { localStorage.removeItem(" + json.dumps(CART_KEY) + "); } catch (e) {}"
            " try { sessionStorage.setItem(" + json.dumps(SPLASH_KEY) + ", '1'); } catch (e) {} })();")

        def guard(route):
            req = route.request
            if req.method in ("GET", "HEAD"):
                route.continue_()
                return
            self.stubbed.append(req.method + " " + req.url.split("?")[0])
            if req.method == "OPTIONS":
                route.fulfill(status=204, headers={"access-control-allow-origin": "*", "access-control-allow-headers": "*",
                                                   "access-control-allow-methods": "GET,POST,OPTIONS"}, body="")
                return
            route.fulfill(status=200, headers={"access-control-allow-origin": "*"}, content_type="application/json",
                          body=json.dumps({"ok": True, "qa": "not sent"}))

        self.ctx.route("**/*", guard)
        self.page = self.ctx.new_page()

    def close(self):
        try:
            self.ctx.close()
            self.browser.close()
        finally:
            self._pw.stop()

    def grid(self) -> dict[str, list[dict]]:
        """Every card on /shop after all 'Show more' pages, grouped by code."""
        from playwright.sync_api import Error as PWError
        p = self.page
        p.goto(self.base + "/shop", wait_until="domcontentloaded", timeout=90000)
        p.wait_for_selector("article[data-stock]", timeout=90000, state="attached")
        for _ in range(60):
            btn = p.get_by_role("button", name=re.compile(r"^Show more"))
            try:
                if not btn.count() or not btn.first.is_visible():
                    break
                btn.first.scroll_into_view_if_needed(timeout=5000)
                btn.first.click(timeout=5000)
                p.wait_for_timeout(350)
            except PWError:
                break
        p.wait_for_timeout(600)
        cards = p.evaluate(GRID_JS)
        by: dict[str, list[dict]] = defaultdict(list)
        for c in cards:
            if c.get("code"):
                by[c["code"]].append(c)
        return by

    def panel(self, code: str) -> dict | None:
        from playwright.sync_api import Error as PWError
        p = self.page
        from urllib.parse import quote
        p.goto(self.base + "/p/" + quote(code, safe=""), wait_until="domcontentloaded", timeout=90000)
        try:
            p.wait_for_selector('[role="dialog"]', timeout=45000, state="attached")
            p.wait_for_function(
                "() => { const d = document.querySelector('[role=\"dialog\"]'); return d && (/BHD \\d+\\.\\d{3}/.test(d.textContent) || /Price on request/.test(d.textContent)); }",
                timeout=30000)
        except PWError:
            return None
        p.wait_for_timeout(300)
        return p.evaluate(PANEL_JS)


# ── report ─────────────────────────────────────────────────────────────────────

def md_escape(s) -> str:
    return str("" if s is None else s).replace("|", "\\|")


def fmt(d: Decimal | None) -> str:
    return "—" if d is None else f"{q3(d):.3f}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=DEFAULT_BASE, help="the marketplace origin (GET only)")
    ap.add_argument("--api", default=DEFAULT_API, help="the API origin for the catalog payload (GET only)")
    ap.add_argument("--data-root", default=None, help="folder holding the DDMMYY drop folders (default: found upwards)")
    ap.add_argument("--skus", type=int, default=25)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--seed", type=int, default=None, help="sample seed (default: today's date, so a rerun today samples the same SKUs)")
    ap.add_argument("--period", default=None, help="YYYY-MM for the rep month (default: the month of the file's latest sale)")
    ap.add_argument("--out", default=None, help="report folder (default: <repo>/business_data/ui_source_checks)")
    ap.add_argument("--no-browser", action="store_true", help="skip the rendered checks (files vs payload and API only)")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args(argv)

    started = datetime.now(timezone.utc)
    today = bahrain_today()
    env_file = load_env()
    data_root = Path(a.data_root) if a.data_root else default_data_root()
    out_dir = Path(a.out) if a.out else ROOT / "business_data" / "ui_source_checks"
    out_dir.mkdir(parents=True, exist_ok=True)
    seed = a.seed if a.seed is not None else int(today.strftime("%Y%m%d"))
    notes: list[str] = []
    fails = 0

    # 1. the files
    drops = find_drops(data_root)
    if not drops:
        print(f"no DDMMYY drop folders under {data_root}", file=sys.stderr)
        return 2
    pb = newest_file(drops, "MASellingPriceBook")
    sb = newest_file(drops, "Stock_balance_by_warehouse")
    dbk = newest_file(drops, "Sales_day_book")
    reg = newest_file(drops, "Summary_sales_register")
    if not (pb and sb and dbk):
        print(f"missing source file(s): pricebook={pb} stock={sb} daybook={dbk}", file=sys.stderr)
        return 2
    if reg and reg[1] != dbk[1]:
        notes.append(f"the Summary_sales_register is from another drop ({reg[1]}) than the day book ({dbk[1]}): the day book's Warehouse Name is used for every line")
        reg = None
    print(f"price book : {rel(pb[0], data_root)}  (drop {pb[1]}, saved {mtime(pb[0])})")
    print(f"stock      : {rel(sb[0], data_root)}  (drop {sb[1]}, saved {mtime(sb[0])})")
    print(f"day book   : {rel(dbk[0], data_root)}  (drop {dbk[1]}, saved {mtime(dbk[0])})")
    prices, pb_meta = price_from_book(pb[0], today)
    srows, stock_as_of = stock_rows(sb[0])
    lines = daybook(dbk[0])
    reg_map = register(reg[0]) if reg else {}
    file_max_sale = max((ln.get("line_date") or "" for ln in lines), default="") or None
    period = a.period or (file_max_sale or today.isoformat())[:7]

    # 2. read-only reference tables
    try:
        ref = db_reference()
    except Exception as e:  # noqa: BLE001
        print(f"database reference read failed (read-only): {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    stock = stock_by_code(srows, ref["codes"], ref["alias_sku"], ref["manual"])
    unmatched = [r["item_name"] for r in srows if not any(r["item_name"] in v["rows"] for v in stock.values())]

    # 3. the payload (GET)
    try:
        payload = get_json(a.api.rstrip("/") + "/public/market")
    except Exception as e:  # noqa: BLE001
        print(f"GET /public/market failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    items = payload.get("items") or []
    by_code = {it["item_code"]: it for it in items}
    codes = sample_codes(items, a.skus, seed)

    # 4. the rendered marketplace (GET only; every other method stubbed)
    grid: dict[str, list[dict]] = {}
    panels: dict[str, dict | None] = {}
    browser_note = None
    stubbed: list[str] = []
    if not a.no_browser:
        try:
            br = Browser(a.base, headed=a.headed)
        except Exception as e:  # noqa: BLE001
            browser_note = f"browser unavailable: {type(e).__name__}: {e}"
        else:
            try:
                grid = br.grid()
                print(f"grid       : {sum(len(v) for v in grid.values())} cards, {len(grid)} codes on {a.base}/shop")
                for i, code in enumerate(codes, 1):
                    panels[code] = br.panel(code)
                    print(f"panel {i:>2}/{len(codes)}: {code:<18} {'ok' if panels[code] else 'NOT RENDERED'}")
            except Exception as e:  # noqa: BLE001
                browser_note = f"browser run stopped: {type(e).__name__}: {str(e)[:200]}"
            finally:
                stubbed = list(br.stubbed)
                br.close()
    else:
        browser_note = "rendered checks skipped (--no-browser)"
    if browser_note:
        notes.append(browser_note)

    # 5. compare prices and stock
    sku_rows = []
    for code in codes:
        it = by_code.get(code) or {}
        fp = prices.get(code) or prices.get(next((k for k in prices if k.upper() == code.upper()), ""), None)
        file_price = fp["rate"] if fp else None
        api_price = q3(it["price_bhd"]) if it.get("price_bhd") is not None else None
        st = stock.get(code)
        file_qty = st["qty"] if st else ZERO
        file_status = status_for(file_qty, ref["low_units"])
        api_status = it.get("stock_status")
        cards = grid.get(code) or []
        grid_price = {c["price"] for c in cards if c.get("price")}
        grid_status = set()
        for c in cards:
            grid_status.add("out_of_stock" if (c["stock"] == "out_of_stock" or c["soldOut"]) else
                            "low_stock" if (c["stock"] == "low_stock" or c["low"]) else "in_stock")
        pn = panels.get(code)
        panel_price = pn.get("price") if pn else None
        panel_status = None
        if pn and pn.get("status"):
            s = pn["status"]
            panel_status = ("out_of_stock" if s.startswith("Sold out") else "low_stock" if s.startswith("Only a few") else "in_stock")
        problems = []
        want = f"{file_price:.3f}" if file_price is not None else None
        if want is None:
            problems.append("no live MA_base row in the file")
        if api_price is not None and want is not None and f"{api_price:.3f}" != want:
            problems.append(f"API price {api_price:.3f} != file {want}")
        if cards and grid_price != ({want} if want else set()):
            problems.append(f"grid price {sorted(grid_price) or 'none'} != file {want}")
        if pn and want and panel_price != want:
            problems.append(f"panel price {panel_price} != file {want}")
        if api_status and api_status != file_status:
            problems.append(f"API status {api_status} != file {file_status}")
        if cards and grid_status != {file_status}:
            problems.append(f"grid status {sorted(grid_status)} != file {file_status}")
        if pn and panel_status and panel_status != file_status:
            problems.append(f"panel status {panel_status} != file {file_status}")
        if not a.no_browser and not browser_note:
            if not cards:
                problems.append("not on the grid")
            if not pn:
                problems.append("panel did not render")
        if problems:
            fails += 1
        sku_rows.append({
            "code": code, "file_price": file_price, "api_price": api_price,
            "grid_price": ", ".join(sorted(grid_price)) if grid_price else ("—" if cards else "n/a"),
            "panel_price": panel_price or ("—" if pn else "n/a"),
            "file_qty": file_qty, "match": "/".join(sorted(st["source"])) if st else "absent",
            "file_status": file_status, "api_status": api_status,
            "grid_status": ", ".join(STOCK_LABELS.get(s, s) for s in sorted(grid_status)) if grid_status else "n/a",
            "panel_status": STOCK_LABELS.get(panel_status, panel_status) if panel_status else ("—" if pn else "n/a"),
            "result": "PASS" if not problems else "FAIL: " + "; ".join(problems),
        })

    # 6. the reps
    rep_rows = []
    names = ref["target_names"] or sorted({(ln.get("warehouse_name") or "").split(" - ")[0] for ln in lines if ln.get("warehouse_name")})
    ranked = sorted(names, key=lambda n: rep_mtd_from_file(lines, reg_map, n, period, ref["alias_division"])[0], reverse=True)
    picked_reps = ranked[:a.reps]
    api_err = None
    try:
        from app.shop import rep_month_sales
    except Exception as e:  # noqa: BLE001
        api_err = f"app.shop import failed: {type(e).__name__}: {e}"
        rep_month_sales = None  # noqa: N806
    for name in picked_reps:
        file_amt, n_lines = rep_mtd_from_file(lines, reg_map, name, period, ref["alias_division"])
        api_amt = api_date = None
        err = api_err
        if rep_month_sales:
            try:
                amt, api_date = rep_month_sales(name, period=period)
                api_amt = Decimal(str(amt))
            except Exception as e:  # noqa: BLE001
                err = f"{type(e).__name__}: {str(e)[:120]}"
        through = None
        file_through = None
        if api_date and file_max_sale and api_date[:10] < file_max_sale:
            through = api_date[:10]
            file_through = rep_mtd_from_file(lines, reg_map, name, period, ref["alias_division"], through=through)[0]
        delta = (q3(file_amt) - q3(api_amt)) if api_amt is not None else None
        ok = delta is not None and abs(delta) <= Decimal("0.0015")
        lag_ok = (file_through is not None and api_amt is not None and abs(q3(file_through) - q3(api_amt)) <= Decimal("0.0015"))
        if not ok and not lag_ok:
            fails += 1
        rep_rows.append({"name": name, "lines": n_lines, "file": file_amt, "api": api_amt, "api_date": api_date,
                         "delta": delta, "file_through": file_through, "through": through,
                         "result": ("PASS" if ok else
                                    f"PASS to {through} (the API's data lags the file; the file's full month is {fmt(file_amt)})" if lag_ok else
                                    f"FAIL: {err}" if err else "FAIL")})

    # 7. the report
    ts = started.strftime("%Y-%m-%dT%H%M%SZ")
    path = out_dir / f"{ts}.md"
    L: list[str] = []
    L.append(f"# UI vs source check — {started.strftime('%Y-%m-%d %H:%M')} UTC ({today} Bahrain)")
    L.append("")
    L.append(f"**Verdict: {'ALL MATCH' if fails == 0 else f'{fails} MISMATCH(ES)'}** — {len(sku_rows)} SKUs (price + stock status, grid + panel) and {len(rep_rows)} reps (ex-VAT month-to-date, {period}).")
    L.append("")
    L.append("## Sources")
    L.append("")
    L.append("| Source | File | Drop | Saved | Notes |")
    L.append("|---|---|---|---|---|")
    L.append(f"| Price (MA_base) | `{md_escape(rel(pb[0], data_root))}` | {pb[1]} | {mtime(pb[0])} | {pb_meta['rows']} rows, {pb_meta['live_rows']} in force today, {pb_meta['skus']} SKUs; statuses {pb_meta['statuses']} |")
    L.append(f"| Stock | `{md_escape(rel(sb[0], data_root))}` | {sb[1]} | {mtime(sb[0])} | as on {stock_as_of}; {len(srows)} rows; {len(stock)} catalog codes matched; {len(unmatched)} rows unmatched |")
    L.append(f"| Sales | `{md_escape(rel(dbk[0], data_root))}` | {dbk[1]} | {mtime(dbk[0])} | {len(lines)} lines to {file_max_sale}; salesman from the register on {len(reg_map)} invoices |")
    L.append(f"| Marketplace | {a.base} (grid `/shop`, panel `/p/{{code}}`) | | | payload: {len(items)} items, stock as of {payload.get('stock_as_of')}, prices updated {payload.get('prices_updated')}, fresh={payload.get('stock_fresh')} |")
    L.append(f"| API / DB | {a.api} (`GET /public/market`); `app.shop.rep_month_sales` (read-only RPC); reference SELECTs READ ONLY | | | DB latest sale {ref['db_sale_date']}, DB stock as of {ref['db_stock_as_of']}, low-stock threshold {ref['low_units']} units |")
    L.append("")
    L.append(f"Sample seed {seed}; env `{md_escape(env_file.name if env_file else '(none)')}`; non-GET browser requests answered locally: **{len(stubbed)}**"
             + (" (" + ", ".join(sorted({s for s in stubbed})[:8]) + ("…" if len(set(stubbed)) > 8 else "") + ")" if stubbed else "") + ".")
    L.append("")
    L.append("## Prices and stock status — file vs marketplace")
    L.append("")
    L.append("Price = the MA_base row in force today, base row first (v_price_list_by_book's rule) — never the database's copy. "
             "Stock = the Stock_balance file summed per catalog code (manual map → alias → longest code prefix); absent = sold out; "
             f"low = ≤ {ref['low_units']} units.")
    L.append("")
    L.append("| # | SKU | Price file | API | Grid | Panel | Stock file (units, match) | Status file | API | Grid | Panel | Result |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(sku_rows, 1):
        L.append(f"| {i} | `{md_escape(r['code'])}` | {fmt(r['file_price'])} | {fmt(r['api_price'])} | {md_escape(r['grid_price'])} | {md_escape(r['panel_price'])} "
                 f"| {r['file_qty']:.0f} ({r['match']}) | {STOCK_LABELS[r['file_status']]} | {STOCK_LABELS.get(r['api_status'] or '', r['api_status'] or '—')} "
                 f"| {md_escape(r['grid_status'])} | {md_escape(r['panel_status'])} | {md_escape(r['result'])} |")
    L.append("")
    L.append(f"## Reps — ex-VAT month-to-date {period}, Accessories only, giveaways out — file vs API")
    L.append("")
    L.append("File = Σ COALESCE(Taxable, Gross/1.1) over the day book's lines of the month for the rep (register salesman, else Warehouse Name; "
             "`name` or `name - …`), division Accessories (item-alias map; SIM by name), zero-priced giveaways out. "
             "API = `app.shop.rep_month_sales(name, period)` — what the Today card and the kickback statement use. Tolerance one fils.")
    L.append("")
    L.append("| Rep | File lines | File MTD | API MTD | API data to | Δ (file − API) | Result |")
    L.append("|---|---|---|---|---|---|---|")
    for r in rep_rows:
        L.append(f"| {md_escape(r['name'])} | {r['lines']} | {fmt(r['file'])} | {fmt(r['api'])} | {r['api_date'] or '—'} | {fmt(r['delta']) if r['delta'] is not None else '—'} | {md_escape(r['result'])} |")
    L.append("")
    if unmatched:
        L.append("## Stock rows with no catalog code (not sold online; listed so nothing is silently dropped)")
        L.append("")
        for n in unmatched[:40]:
            L.append(f"- {md_escape(n)}")
        if len(unmatched) > 40:
            L.append(f"- … and {len(unmatched) - 40} more")
        L.append("")
    if notes:
        L.append("## Notes")
        L.append("")
        for n in notes:
            L.append(f"- {md_escape(n)}")
        L.append("")
    L.append("## Method and safety")
    L.append("")
    L.append("- GET only: the grid and the panels were read with Playwright (Chromium, 1440×900); every non-GET request the page made "
             "was answered by a local stub (counted above) — no event, restock ask, quote or order reached the server.")
    L.append("- The database was opened READ ONLY (`SET TRANSACTION READ ONLY`) for reference tables only: catalog codes, the stock map, "
             "item aliases with their division, one setting and the reps' target rows. The rep API figure comes through the read-only RPC.")
    L.append("- Files were parsed with the repo's own parsers (`scripts/ingest.py`); the selection rules above are restated in this script, "
             "not read back from the views, so a view bug shows up here as a mismatch.")
    L.append(f"- Run from `{md_escape(str(ROOT))}` in {time.strftime('%H:%M:%S', time.gmtime((datetime.now(timezone.utc) - started).seconds))}.")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\n{'ALL MATCH' if fails == 0 else f'{fails} MISMATCH(ES)'} — {len(sku_rows)} SKUs, {len(rep_rows)} reps; report {path}")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
