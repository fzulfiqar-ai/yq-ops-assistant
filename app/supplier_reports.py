"""Monthly supplier workbook (Market Intel v2, Phase 2; plan Part F9-F11, build contract §3).

A reviewer picks a supplier and a month; build_draft() gathers that supplier's field reports of the
month into a DRAFT supplier_reports row: counts by issue type, new vs recurring, open corrective
actions, and a narrative of template sentences in which every sentence about a finding cites the
sightings it rests on ([MI-<observation id>]) and every action its id ([CA-<action id>]). While it is
a draft a reviewer may include or exclude rows, withhold a row or a photo from the supplier, and edit
the narrative. Management (or an admin) approves it once the month is over plus 7 days. Approving
FREEZES a manifest on the row (counts.manifest: every row, photo and action exactly as the workbook
prints them, the counts and the narrative) and supersedes the earlier approved report of the same
supplier, month and edition. A download renders that manifest and nothing else, after checking that it
may still go out: no row has been deleted, changed supplier or been found not substantiated; no photo
has been deleted, flagged with people, re-classed as a receipt or changed in storage; no action has
moved to another supplier. Otherwise nothing is served (409). Every download is logged with the file's
sha256. Nothing is ever e-mailed.

Which sightings belong to a supplier: every link a sighting has must point at the same live supplier:
the case's supplier, the case's brand's CURRENT supplier, the sighting's own supplier_id and its
brand's current supplier (merged suppliers and brands followed). A sighting whose links disagree
belongs to nobody. It is left out of every draft with a warning naming it, and it blocks the approval
of a report of any supplier it points at until the office corrects the links. Findings are the
sightings whose issue type (the case's, else the sighting's own) is of kind quality ("Product issues")
or packaging ("Packaging"); a v1 complaint without an issue type counts as quality. The internal
edition adds the competitor and demand sightings. A case found not substantiated (or closed without
ever being verified: no verification method recorded) is left out everywhere, and only a case with a
verification method is shown as "Verified" (or at evidence level 3 and above) and counted as verified.

Rules:
    recurring      the same yq_item_code + issue type in >= 2 cases, or from >= 3 shops, within the 90
                   days that end with the month (a product nobody identified: its own case, >= 3 shops)
    insufficient   no photo, evidence level 1 and a single report (that product + issue in those 90
                   days): named in its own narrative paragraph and listed last, never counted, unless a
                   reviewer includes it (or withholds it)
    month          a Bahrain calendar month (UTC+3); approved from the 8th of the next month (late
                   captures of the last days may still arrive)
    narrative      a template narrative is rewritten from the data at approval; one edited by hand is
                   approved only while the data it was written against is unchanged, it may cite only
                   rows the workbook lists, and every sentence of it that states a finding cites one

Privacy (a supplier edition leaves the company): no shop or customer name, no rep name ("field team"),
no YQ price, cost or margin, no photo flagged with people, no receipt photo, no other supplier's rows,
names or brands, no cancelled action. Every free-text value goes through app.ai_insights.mask_text and
then a stricter local scrub (Scrubber): phone numbers with separators, money amounts (with a currency,
with three decimals, or after a price word) and ONE longest-first pass over every name the database
knows (reps, shops, customers, other suppliers and their brands); only the report's own supplier and
brands keep their names. A SKU the catalog does not confirm prints as "unverified SKU". Photos come
from the private bucket through the app's storage client.

The tables arrive with scripts/r8m_market_quality_migration.sql. Until then available() is False and
every call the routes make raises SupplierReportError(MISSING_HINT, 503), so the API may deploy first.
A database answer about a COLUMN we do not have is a fault in this code: a 500 naming the column,
never the calm "not set up yet".
"""
from __future__ import annotations

import functools
import hashlib
import io
import json
import logging
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from app import market_intel as mi
from app.ai_insights import mask_text
from app.database import get_client

log = logging.getLogger(__name__)

# ── vocabulary ────────────────────────────────────────────────────────────────

EDITIONS = ("supplier", "internal")
EDITION_LABELS = {"supplier": "Supplier copy", "internal": "Internal edition: for YQ only, not for the supplier"}
STATUSES = ("draft", "approved", "superseded")
FINDING_KINDS = ("quality", "packaging")
MARKET_KINDS = ("competitor", "demand")
KIND_WORDS = {"quality": "Product issue", "packaging": "Packaging"}
SHEET_OF = {"quality": "product_issues", "packaging": "packaging", "competitor": "competitor", "demand": "demand"}
SHOWN_STATES = ("included", "insufficient")         # the rows a workbook lists (included = counted)
# a v1 sighting without an issue type: its kind decides (new_product / other are not findings)
V1_KINDS = {"complaint": "quality", "competitor_price": "competitor", "promotion": "competitor",
            "shop_asked": "demand"}
EVIDENCE_WORDS = {1: "Reported", 2: "Evidence submitted", 3: "Inspected & verified by YQ",
                  4: "Supplier confirmed", 5: "Resolution verified"}
VERIFIED_LEVEL = 3
CASE_STATUS_WORDS = {"submitted": "Received", "under_review": "Under review",
                     "more_info_required": "More information requested", "verified": "Verified by YQ",
                     "not_substantiated": "Not substantiated", "action_requested": "Action requested",
                     "resolved": "Resolved", "closed": "Closed"}
OPEN_ACTION_STATUSES = ("open", "responded")
ACTION_STATUS_WORDS = {"open": "Awaiting your response", "responded": "Response received, YQ check pending",
                       "resolved": "Resolved", "cancelled": "Cancelled"}
BASIS_WORDS = {"customer_said": "Customer said", "rep_saw": "Field team saw"}
# a supplier edition shows product, packaging and label photos only: never a receipt (shop names,
# prices) or an 'other' photo; a photo flagged with people is never in either edition
SUPPLIER_PHOTO_ROLES = ("product", "packaging", "label_batch")
ROLE_WORDS = {"product": "Product", "packaging": "Packaging", "label_batch": "Label / batch", "receipt": "Receipt",
              "other": "Other"}
SIGNAL_WORDS = {"search_zero": "Marketplace search found nothing", "restock_ask": "Restock ask on the marketplace"}

RECUR_DAYS, RECUR_CASES, RECUR_SHOPS = 90, 2, 3
APPROVE_AFTER_DAYS = 7            # a month is approved once it is over plus these days (late captures)
BAHRAIN = timezone(timedelta(hours=3))
EVIDENCE_PX = 640                 # the Evidence sheet's JPEG, longest side (shown at half size)
THUMB_PX = 160                    # a finding row's thumbnail
MAX_PHOTOS = 120                  # embedded per workbook (about 60 KB each); the rest are listed, not embedded
MAX_CITES = 12                    # ids cited in one sentence; the sheet lists them all
MAX_NARRATIVE = 20000
MAX_IDS = 5000
FIELD_TEAM = "field team"
SHOP_MASK, SUPPLIER_MASK, BRAND_MASK = "[shop]", "[another supplier]", "[another brand]"
AMOUNT_MASK, NUMBER_MASK = "[amount]", "[number]"
UNVERIFIED_SKU, NO_SKU = "unverified SKU", "not identified"
PHOTO_NOT_AVAILABLE = "photo not available"
PAGE_MAX = 50                     # pages of 1,000 rows per read: past it nothing is built
LINK_ORDER = ("action_id", "case_id")      # supplier_action_cases has no id: its key is (action_id, case_id)
MANIFEST_VERSION = 2

# Who may do what (plan F11): reviewers build and edit drafts; reviewers and management read and
# download; management (and an admin) approve. A salesman never reaches a supplier report.
BUILD_ROLES = mi.REVIEW_ROLES
READ_ROLES = mi.APPROVE_ROLES
APPROVER_ROLES = frozenset({"admin", "management"})
# Management is read-only centrally (app.auth); its approval rides on this template in
# auth.READ_ONLY_WRITE_ALLOWLIST, like the Market Intel item approval.
APPROVE_ROUTE = "/market-intel/supplier-reports/{report_id}/approve"

MIGRATION = "scripts/r8m_market_quality_migration.sql"
MISSING_HINT = "Supplier reports are not set up yet: apply scripts/r8m_market_quality_migration.sql."

MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")


class SupplierReportError(ValueError):
    """A refusal the route turns into an HTTP answer (400 unless `status` says otherwise)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ── availability (the migration may land after the API) ──────────────────────

_probe: dict = {"until": 0.0, "ok": False}
_PROBE_HIT_S, _PROBE_MISS_S = 600.0, 60.0


def available(force: bool = False) -> bool:
    """True when the r8m tables exist. A hit is remembered for 10 minutes, a miss for one."""
    now = time.monotonic()
    if not force and _probe["until"] > now:
        return bool(_probe["ok"])
    try:
        get_client().table("supplier_reports").select("id").limit(1).execute()
        ok = True
    except Exception as e:  # noqa: BLE001 — a missing table (or a blip) means "not yet"
        log.debug("supplier reports probe failed: %s", e)
        ok = False
    _probe.update(until=now + (_PROBE_HIT_S if ok else _PROBE_MISS_S), ok=ok)
    return ok


def forget_probe() -> None:
    _probe.update(until=0.0, ok=False)


_Q_ = r"[\\\"']*"                 # a quote, maybe escaped, as PostgREST's JSON or the SDK's repr shows it
_COLUMN_RXS = (re.compile(rf"column {_Q_}([\w.]+){_Q_}(?: of relation {_Q_}([\w.]+){_Q_})? does not exist", re.I),
               re.compile(rf"could not find the {_Q_}([\w.]+){_Q_} column of {_Q_}([\w.]+){_Q_}", re.I))


def column_error(e: Exception) -> str | None:
    """The column a database error says we asked for and do not have (42703, PGRST204), else None."""
    msg = f"{getattr(e, 'code', '')} {e}"
    if "42703" not in msg and "PGRST204" not in msg and not re.search(r"\bcolumn\b", msg, re.I):
        return None
    for rx in _COLUMN_RXS:
        m = rx.search(msg)
        if m:
            col, table = m.group(1), m.group(2)
            return f"{table}.{col}" if table and "." not in col else col
    return "an unknown column" if ("42703" in msg or "PGRST204" in msg) else None


def missing_table(e: Exception) -> bool:
    """Only a missing TABLE (42P01 / PGRST205) means the migration is not there; a column error never."""
    if column_error(e):
        return False
    msg = f"{getattr(e, 'code', '')} {e}"
    return "42P01" in msg or "PGRST205" in msg or "Could not find the table" in msg \
        or ("relation" in msg and "does not exist" in msg)


def _tables(fn):
    """Every call the routes make: 503 MISSING_HINT before the migration, or when a read meets a
    missing table half-way (the reverse ran); a missing COLUMN is our fault: a 500 that names it."""
    @functools.wraps(fn)
    def run(*a, **k):
        if not available():
            raise SupplierReportError(MISSING_HINT, 503)
        try:
            return fn(*a, **k)
        except SupplierReportError:
            raise
        except Exception as e:  # noqa: BLE001
            col = column_error(e)
            if col:
                log.error("supplier reports: the database has no column %s: %s", col, e)
                raise SupplierReportError(f"Supplier reports asked the database for a column it does not have "
                                          f"({col}). This is a fault in the API, not a missing migration.",
                                          500) from None
            if missing_table(e):
                forget_probe()
                raise SupplierReportError(MISSING_HINT, 503) from None
            raise
    return run


def _need(role: str | None, allowed: frozenset, message: str) -> None:
    if (role or "") not in allowed:
        raise SupplierReportError(message, 403)


# ── small helpers ─────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    return (dt or _now()).astimezone(timezone.utc).isoformat()


def _int(v) -> int | None:
    try:
        return int(v) if v not in (None, "") and not isinstance(v, bool) else None
    except (TypeError, ValueError):
        return None


def _ts(v) -> datetime | None:
    return mi._parse_ts(v)


def _bh_date(v) -> date | None:
    t = _ts(v) if not isinstance(v, datetime) else v
    return t.astimezone(BAHRAIN).date() if t else None


def _date_of(v) -> date | None:
    """A date column ('2026-10-15') or a timestamp, as a Bahrain date."""
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    s = str(v or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            return date.fromisoformat(s)
        except ValueError:
            return None
    return _bh_date(s)


def _day(d: date | None) -> str:
    """'18 Sep 2026'."""
    return f"{d.day} {MONTHS[d.month - 1][:3]} {d.year}" if d else ""


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def parse_month(v, *, now: datetime | None = None, future_ok: bool = False) -> date:
    """'YYYY-MM' (or the stored 'YYYY-MM-01') -> the month's first day. A month that has not started
    (Bahrain time) is refused unless future_ok (a list filter)."""
    m = re.fullmatch(r"\s*(\d{4})-(\d{1,2})(?:-01)?\s*", str(v or ""))
    if not m:
        raise SupplierReportError("The month is YYYY-MM, like 2026-09.")
    y, mo = int(m.group(1)), int(m.group(2))
    if not 1 <= mo <= 12 or y < 2024:
        raise SupplierReportError("The month is YYYY-MM, like 2026-09.")
    first = date(y, mo, 1)
    today = (now or _now()).astimezone(BAHRAIN).date()
    if not future_ok and first > today.replace(day=1):
        raise SupplierReportError("That month has not started yet.")
    return first


def month_key(first: date) -> str:
    return f"{first.year:04d}-{first.month:02d}"


def month_label(first: date) -> str:
    return f"{MONTHS[first.month - 1]} {first.year}"


def month_bounds(first: date) -> tuple[datetime, datetime]:
    """[the first day 00:00, the next month's first day 00:00), Bahrain time."""
    nxt = date(first.year + (1 if first.month == 12 else 0), first.month % 12 + 1, 1)
    return (datetime(first.year, first.month, 1, tzinfo=BAHRAIN),
            datetime(nxt.year, nxt.month, 1, tzinfo=BAHRAIN))


def approvable_from(first: date) -> datetime:
    """When a month's report may be approved: the month is over, plus APPROVE_AFTER_DAYS for the
    captures of its last days that arrive late (the phone's capture time may be up to 7 days back)."""
    return month_bounds(first)[1] + timedelta(days=APPROVE_AFTER_DAYS)


def _who(email) -> str | None:
    """A login as the API shows it: the local part only."""
    s = str(email or "").strip()
    return (s.split("@")[0] or None) if s else None


# ── the scrub: what a supplier must never read ───────────────────────────────

_CUR = r"bhd|bd|fils|dinars?|usd|aed|sar|دينار|د\.ب"
# a phone number however it is written: 7+ digits with spaces, dashes, dots, brackets or slashes
_PHONE = re.compile(r"\(?\+?\(?\d(?:[\s.\-()/]{0,3}\d){6,}")
_AMOUNT = re.compile(rf"(?i)(?:(?<!\w)(?:{_CUR})\.?\s*\d+(?:[.,]\d+)*|\d+(?:[.,]\d+)*\s*(?:{_CUR})(?!\w)"
                     r"|\$\s*\d+(?:[.,]\d+)*|\d+(?:[.,]\d+)*\s*\$)")
_AMOUNT_3DP = re.compile(r"(?<![\w.,])\d+(?:,\d{3})*\.\d{3}(?!\d)")      # 2.500 (thousands with commas too): dinars
_PRICE_WORD = re.compile(
    r"(?i)\b(prices?|priced|pricing|costs?|costing|mrp|rrp|srp|margins?|rates?|paid|worth"
    r"|sell(?:s|ing)?(?:\s+(?:it|them|each|one))?\s+(?:at|for)"
    r"|sold(?:\s+(?:it|them|each|one))?\s+(?:at|for)|retail(?:s|ed|ing)?(?:\s+(?:at|for))?)\b"
    r"((?:\s*(?:of|is|was|at|for|around|about|only|just|=|:|-))*\s*)\d+(?:[.,]\d+)?")
_CURRENCY = re.compile(rf"(?i)(?<!\w)(?:{_CUR})(?!\w)")
_WORD = re.compile(r"\w+")
# names that are system words, never a person, a shop or a supplier (the ops selftest's list, and more)
_GENERIC = frozenset({"cash customer", "customer", "cash", "credit", "total", "sub total", "grand total",
                      "walk in", "walk-in", "walk in customer", "shop", "the shop", "office", "warehouse",
                      "field team", "supplier", "salesman", "yq", "yq bahrain"})


class Scrubber:
    """The stricter local scrub of every supplier-facing text: app.ai_insights.mask_text (e-mails, long
    numbers), then phone numbers with separators -> [number], money -> [amount] (with a currency, with
    three decimals, after a price word), then ONE longest-first pass over every known name: a rep's ->
    'field team', a shop's or a customer's -> [shop], another supplier's -> [another supplier], another
    supplier's brand (name, alias, former name) -> [another brand]; only `keep` (the report's own supplier
    and brands) maps to itself, and it never shields a longer name that contains it ("Royal" kept,
    "Royal Mobile" and "Royal Gulf Trading" masked). Names are found by their first word, so thousands of
    names cost one dictionary lookup per word of text."""

    def __init__(self, *, reps=(), shops=(), others=(), other_brands=(), keep=()):
        self.index: dict[str, list[tuple[re.Pattern, str | None]]] = {}
        seen: set[str] = set()
        groups = ((keep, None, 2), (reps, FIELD_TEAM, 3), (shops, SHOP_MASK, 4), (others, SUPPLIER_MASK, 3),
                  (other_brands, BRAND_MASK, 3))
        for names, repl, min_len in groups:
            for raw in names:
                n = " ".join(str(raw or "").split())
                low = n.lower()
                words = _WORD.findall(low)
                if len(n) < min_len or not words or low in seen or n.replace(" ", "").isdigit() \
                        or (repl is not None and low in _GENERIC):
                    continue
                seen.add(low)          # a name listed twice keeps its first (stricter: keep, rep, shop, ...)
                rx = re.compile(r"(?<!\w)" + r"\s+".join(re.escape(w) for w in n.split(" ")) + r"(?!\w)",
                                re.IGNORECASE)
                self.index.setdefault(words[0], []).append((rx, repl))

    def masks(self, value: str) -> str:
        t = mask_text(value)
        t = _PHONE.sub(NUMBER_MASK, t)
        t = _AMOUNT.sub(AMOUNT_MASK, t)
        t = _AMOUNT_3DP.sub(AMOUNT_MASK, t)
        t = _PRICE_WORD.sub(lambda m: f"{m.group(1)}{m.group(2)}{AMOUNT_MASK}", t)
        return _CURRENCY.sub(AMOUNT_MASK, t)

    def names(self, t: str) -> str:
        spans = []
        for w in {x.lower() for x in _WORD.findall(t)}:
            for rx, repl in self.index.get(w, ()):
                spans += [(m.start(), m.end(), repl) for m in rx.finditer(t)]
        chosen: list[tuple[int, int, str | None]] = []
        for s in sorted(spans, key=lambda s: (s[0] - s[1], s[0])):          # longest first, then leftmost
            if all(s[1] <= c[0] or s[0] >= c[1] for c in chosen):
                chosen.append(s)
        out, pos = [], 0
        for a, b, repl in sorted(chosen):
            out += [t[pos:a], t[a:b] if repl is None else repl]
            pos = b
        return "".join(out + [t[pos:]])

    def __call__(self, value):
        if not isinstance(value, str) or not value:
            return value
        return self.names(self.masks(value))


# ── reads (paged: PostgREST answers 1,000 rows at most) ──────────────────────

def _q(table: str, cols: str = "*"):
    return get_client().table(table).select(cols)


def _all(make, order: tuple[str, ...] = ("id",), max_pages: int | None = None) -> list[dict]:
    """Every row of make() (a fresh select builder) in a total order, 1,000 at a time. A read that would
    need more than max_pages (PAGE_MAX) pages stops the report: a name list read half-way could let a
    name through."""
    from app.shop_analytics import PAGE, paged
    max_pages = max_pages or PAGE_MAX

    def page(a: int) -> list[dict]:
        q = make()
        for col in order:
            q = q.order(col)
        return q.range(a, a + PAGE - 1).execute().data or []
    rows, capped = paged(page, max_pages)
    if capped:
        raise SupplierReportError(f"A list was too long to read in full (over {max_pages * PAGE:,} rows), so nothing "
                                  "was built.", 503)
    return rows


def _by(table: str, col: str, ids, cols: str = "*", order: tuple[str, ...] = ("id",)) -> list[dict]:
    keys = sorted({i for i in (_int(x) for x in ids) if i is not None})
    out: list[dict] = []
    for i in range(0, len(keys), 200):
        chunk = keys[i:i + 200]
        out += _all(lambda chunk=chunk: _q(table, cols).in_(col, chunk), order=order)
    return out


def _names_of(make) -> list[dict]:
    """A name list for the scrub. A table that does not exist is an empty list; any other failure stops
    the report."""
    try:
        return _all(make)
    except SupplierReportError:
        raise
    except Exception as e:  # noqa: BLE001
        if missing_table(e):
            return []
        raise


def _live(sid, by_id: dict[int, dict], hops: int = 6) -> int | None:
    """Follow merged_into to the live row's id."""
    cur = _int(sid)
    seen: set[int] = set()
    while cur is not None and hops > 0 and cur not in seen:
        nxt = _int((by_id.get(cur) or {}).get("merged_into"))
        if nxt is None:
            break
        seen.add(cur)
        cur, hops = nxt, hops - 1
    return cur


def _brand_now(bid, brands: dict[int, dict]) -> int | None:
    """The supplier a brand points at NOW: the live brand's (merges followed), never a stale copy."""
    b = brands.get(_int(bid)) if _int(bid) is not None else None
    if not b:
        return None
    return _int((brands.get(_live(b["id"], brands)) or b).get("supplier_id"))


def _links(o: dict, case: dict | None, brands: dict, suppliers: dict) -> dict[str, int]:
    """{where the link is: the live supplier it points at} for every supplier link a sighting has."""
    c = case or {}
    raw = {"case": _int(c.get("supplier_id")), "case brand": _brand_now(c.get("brand_id"), brands),
           "sighting": _int(o.get("supplier_id")), "sighting brand": _brand_now(o.get("brand_id"), brands)}
    return {k: _live(v, suppliers) for k, v in raw.items() if v is not None}


def _links_words(links: dict[str, int], suppliers: dict) -> str:
    by: dict[int, list[str]] = {}
    for where, sid in links.items():
        by.setdefault(sid, []).append(where)
    return "; ".join(f"{', '.join(w)}: {(suppliers.get(s) or {}).get('name') or f'supplier {s}'}"
                     for s, w in sorted(by.items()))


def _cases_for(ids) -> dict[int, dict]:
    """The cases by id, plus every case they were merged into."""
    out: dict[int, dict] = {}
    want = {i for i in (_int(x) for x in ids) if i is not None}
    for _ in range(6):
        need = sorted(want - set(out))
        if not need:
            break
        for r in _by("market_cases", "id", need):
            out[int(r["id"])] = r
        want = {_int(r.get("merged_into")) for r in out.values()} - {None}
    return out


def _live_case(cid, cases: dict[int, dict]) -> dict | None:
    c = cases.get(_int(cid)) if _int(cid) is not None else None
    hops = 6
    while c and _int(c.get("merged_into")) is not None and hops > 0:
        nxt = cases.get(_int(c["merged_into"]))
        if not nxt:
            break
        c, hops = nxt, hops - 1
    return c


def _level(case: dict | None) -> int:
    try:
        return max(1, min(5, int((case or {}).get("evidence_level") or 1)))
    except (TypeError, ValueError):
        return 1


def _verified(case: dict | None) -> bool:
    """A person verified the case: a verification method is recorded (the evidence level alone can be
    raised without one, so it never stands for "verified" here)."""
    return bool((case or {}).get("verification_method"))


def _dropped(case: dict | None) -> bool:
    """Not substantiated, or closed without ever being verified (no verification method recorded)."""
    if not case:
        return False
    st = case.get("status")
    return st == "not_substantiated" or (st == "closed" and not _verified(case))


def _shown_level(case: dict | None, has_photo: bool) -> int:
    """The evidence level a report shows: the case's, but never 'Inspected & verified by YQ' or above
    unless the case was verified (a method recorded); a sighting without a case: 2 with a photo, else 1."""
    if not case:
        return 2 if has_photo else 1
    level = _level(case)
    return level if level < VERIFIED_LEVEL or _verified(case) else VERIFIED_LEVEL - 1


def _kind(o: dict, case: dict | None, types: dict[int, dict]) -> str:
    for tid in (_int((case or {}).get("issue_type_id")), _int(o.get("issue_type_id"))):
        t = types.get(tid) if tid is not None else None
        if t and t.get("kind"):
            return str(t["kind"])
    return V1_KINDS.get(str(o.get("kind") or ""), "other")


def _shop_key(o: dict) -> str | None:
    if o.get("shop_customer_id"):
        return f"c{o['shop_customer_id']}"
    n = " ".join(str(o.get("shop_name") or "").lower().split())
    return f"n{n}" if n else None


def _eligible(p: dict, edition: str) -> bool:
    if p.get("has_people_flag"):
        return False
    return edition == "internal" or (p.get("role") or "product") in SUPPLIER_PHOTO_ROLES


def scrubber_for(sid: int, suppliers: dict[int, dict], brands: dict[int, dict], shop_words=()) -> Scrubber:
    """The scrub of one supplier's report: every rep, shop and customer name the database knows, the
    shop names typed on sightings, every other supplier's name and code and its brands' names, aliases
    and former names. The supplier's own names (and its merged ones') and its brands' stay."""
    family = {i for i in suppliers if _live(i, suppliers) == sid}

    def brand_words(b: dict) -> list:
        return [b.get("name"), b.get("renamed_from"), *(b.get("aliases") or [])]
    own_brands, other_brands = [], []
    for i, b in brands.items():
        owner = _live(_brand_now(i, brands), suppliers)
        if owner in family:
            own_brands += brand_words(b)
        elif owner is not None:
            other_brands += brand_words(b)
    keep = [suppliers[i].get(k) for i in family for k in ("name", "code")] + own_brands
    others = [r.get(k) for i, r in suppliers.items() if i not in family for k in ("name", "code")]
    reps: list = []
    for r in _names_of(lambda: _q("salesmen")):
        reps += [r.get("name"), r.get("focus_name"), *(r.get("focus_aliases") or [])]
    shops = [r.get(k) for r in _names_of(lambda: _q("shop_customers", "id,name,shop")) for k in ("name", "shop")]
    shops += [r.get("name") for r in _names_of(lambda: _q("customers", "id,name"))]
    return Scrubber(reps=reps, shops=shops + list(shop_words), others=others, other_brands=other_brands, keep=keep)


# ── gather: everything one draft is built from (read-only) ───────────────────

def gather(supplier_id, first: date, edition: str, *, now: datetime | None = None) -> dict:
    """The supplier, its sightings of the month (with the 90 days before for the recurring rule), their
    cases, photos and corrective actions, the rows whose supplier links disagree, and the scrub."""
    now = now or _now()
    if edition not in EDITIONS:
        raise SupplierReportError("The edition is 'supplier' or 'internal'.")
    suppliers = {int(r["id"]): r for r in _all(lambda: _q("suppliers"))}
    sid = _int(supplier_id)
    sup = suppliers.get(sid) if sid is not None else None
    if not sup:
        raise SupplierReportError("That supplier does not exist.", 404)
    live = _live(sid, suppliers)
    if live != sid:
        into = (suppliers.get(live) or {}).get("name") or "another supplier"
        raise SupplierReportError(f"{sup.get('name') or 'This supplier'} was merged into {into}: "
                                  "build that supplier's report.", 409)
    family = sorted(i for i in suppliers if _live(i, suppliers) == sid)
    brands = {int(r["id"]): r for r in _all(lambda: _q("brands"))}
    types = {int(r["id"]): r for r in _all(lambda: _q("market_issue_types"))}
    start, end = month_bounds(first)
    since = end - timedelta(days=RECUR_DAYS)
    raw = _all(lambda: _q("market_observations").gte("observed_at", _iso(since)).lt("observed_at", _iso(end)))
    window: list[tuple[dict, datetime]] = []
    for o in raw:
        t = _ts(o.get("observed_at")) or _ts(o.get("created_at"))
        if t and since <= t < end:
            window.append((o, t))
    cases = _cases_for(o.get("case_id") for o, _t in window)
    items = (mi._catalog_ctx() or {}).get("items") or {}
    codes = {str(k).strip().lower(): str(k) for k in items}       # what the catalog confirms (canonical)

    entries: list[dict] = []
    for o, t in window:
        case = _live_case(o.get("case_id"), cases)
        raw_sku = str((case or {}).get("yq_item_code") or o.get("yq_item_code") or "").strip() or None
        itype = _int((case or {}).get("issue_type_id")) or _int(o.get("issue_type_id"))
        key = ("sku", raw_sku.lower(), itype) if raw_sku else (("case", int(case["id"])) if case else ("obs", int(o["id"])))
        links = _links(o, case, brands, suppliers)
        owners = set(links.values())
        entries.append({"o": o, "ts": t, "case": case, "kind": _kind(o, case, types), "raw_sku": raw_sku,
                        "sku": codes.get(raw_sku.lower()) if raw_sku else None, "itype": itype, "key": key,
                        "links": links, "owner": next(iter(owners)) if len(owners) == 1 else None,
                        "conflict": len(owners) > 1, "dropped": _dropped(case), "shop": _shop_key(o)})
    # the recurring and single-report rules count every sighting of the same product + issue
    stats: dict[tuple, dict] = {}
    for e in entries:
        if e["kind"] in FINDING_KINDS and not e["dropped"]:
            s = stats.setdefault(e["key"], {"cases": set(), "shops": set(), "reports": 0})
            s["cases"].add(f"c{e['case']['id']}" if e["case"] else f"o{e['o']['id']}")
            if e["shop"]:
                s["shops"].add(e["shop"])
            s["reports"] += 1

    kinds = FINDING_KINDS + (MARKET_KINDS if edition == "internal" else ())
    month = [e for e in entries if e["ts"] >= start and not e["dropped"] and e["kind"] in kinds]
    mine = sorted((e for e in month if e["owner"] == sid and not e["conflict"]),
                  key=lambda e: (e["ts"], int(e["o"]["id"])))
    conflicts = [{"id": int(e["o"]["id"]), "links": _links_words(e["links"], suppliers)}
                 for e in sorted(month, key=lambda e: int(e["o"]["id"])) if e["conflict"] and sid in e["links"].values()]
    photos: dict[int, list[dict]] = {}
    for p in _by("market_photos", "observation_id", [e["o"]["id"] for e in mine]):
        photos.setdefault(int(p["observation_id"]), []).append(p)
    for v in photos.values():
        v.sort(key=lambda p: (_int(p.get("position")) or 0, _int(p.get("id")) or 0))

    # corrective actions: the supplier's (and its merged ones'), plus an action without a supplier that
    # is linked to one of this supplier's cases; never another supplier's; never a cancelled one in a
    # supplier edition
    acts = {int(r["id"]): r for r in _all(lambda: _q("supplier_actions").in_("supplier_id", family))}
    case_ids = sorted({int(e["case"]["id"]) for e in mine if e["case"]})
    extra = {_int(r.get("action_id")) for r in _by("supplier_action_cases", "case_id", case_ids, order=LINK_ORDER)} \
        - set(acts) - {None}
    for r in _by("supplier_actions", "id", extra):
        if _int(r.get("supplier_id")) is None:
            acts[int(r["id"])] = r
    action_cases: dict[int, set[int]] = {}
    for r in _by("supplier_action_cases", "action_id", list(acts), order=LINK_ORDER):
        if _int(r.get("action_id")) is not None and _int(r.get("case_id")) is not None:
            action_cases.setdefault(int(r["action_id"]), set()).add(int(r["case_id"]))

    def listed(a: dict) -> bool:
        if edition == "supplier" and a.get("status") == "cancelled":
            return False
        made = _ts(a.get("created_at"))
        if made and made >= end:
            return False
        if a.get("status") in OPEN_ACTION_STATUSES:
            return True
        changed = _ts(a.get("updated_at")) or made
        return bool(changed and start <= changed < end)
    actions = {i: a for i, a in acts.items() if listed(a)}

    scrub = scrubber_for(sid, suppliers, brands, [o.get("shop_name") for o, _t in window])
    return {"supplier": sup, "supplier_id": sid, "first": first, "start": start, "end": end, "since": since,
            "edition": edition, "now": now, "types": types, "entries": mine, "conflicts": conflicts,
            "stats": stats, "photos": photos, "items": items, "actions": actions, "action_cases": action_cases,
            "suppliers": suppliers, "scrub": scrub}


# ── assemble: rows, groups, counts (pure: no reads) ──────────────────────────

def _row_scrub(ctx: dict, e: dict):
    """A row's text scrub: the report's scrub, and a SKU code the catalog does not confirm masked in every
    text of its row (a code kept "as picked" while the catalog could not be read can be any words)."""
    scrub, raw = ctx["scrub"], e["raw_sku"]
    if e["sku"] or not raw or len(raw) < 3:
        return scrub
    rx = re.compile(r"(?<!\w)" + re.escape(raw) + r"(?!\w)", re.I)
    return lambda v: rx.sub(UNVERIFIED_SKU, scrub(v)) if isinstance(v, str) and v else scrub(v)


def _product(ctx: dict, e: dict, case: dict, tx) -> str | None:
    """The catalog's name for a confirmed SKU; else the words typed (an unconfirmed code masked)."""
    if e["sku"]:
        name = (ctx["items"].get(e["sku"]) or {}).get("display_name")
        if name:
            return ctx["scrub"](str(name))
    return tx(mi.clean_text(case.get("title") or e["o"].get("title") or "", 160)) or None


def _sku_label(e: dict) -> str:
    return e["sku"] or (UNVERIFIED_SKU if e["raw_sku"] else NO_SKU)


def _finding_row(ctx: dict, e: dict, hide_photos: set[int]) -> dict:
    o, case, tx = e["o"], e["case"] or {}, _row_scrub(ctx, e)
    oid = int(o["id"])
    t = ctx["types"].get(e["itype"]) or {}
    every = ctx["photos"].get(oid, [])
    shown = [p for p in every if _eligible(p, ctx["edition"]) and int(p["id"]) not in hide_photos]
    level = _shown_level(e["case"], bool(every))
    stat = ctx["stats"].get(e["key"]) or {"cases": set(), "shops": set(), "reports": 1}
    label = t.get("label_en") or ("Unspecified problem" if e["kind"] == "quality" else "Unspecified packaging problem")
    return {"id": oid, "ref": f"MI-{oid}", "date": e["ts"].astimezone(BAHRAIN).date().isoformat(),
            "sku": e["sku"], "sku_label": _sku_label(e), "product": _product(ctx, e, case, tx),
            "issue": {"id": e["itype"], "code": t.get("code"), "label": ctx["scrub"](str(label))},
            "kind": e["kind"], "description": tx(mi.clean_text(o.get("note"), 1000)) or "",
            "qty": _int(o.get("qty_affected")),
            "batch_ref": tx(mi.clean_text(o.get("batch_ref") or case.get("batch_ref"), 80)),
            "packaging_version": tx(mi.clean_text(o.get("packaging_version") or case.get("packaging_version"), 80)),
            "basis": BASIS_WORDS.get(o.get("report_basis")),
            "evidence_level": level, "evidence_label": EVIDENCE_WORDS[level],
            "finding": "Verified" if level >= VERIFIED_LEVEL else "Unverified report",
            "status": CASE_STATUS_WORDS.get(case.get("status"), "Received") if e["case"] else "Received",
            "case_id": _int(case.get("id")) if e["case"] else None,
            "recurring": len(stat["cases"]) >= RECUR_CASES or len(stat["shops"]) >= RECUR_SHOPS,
            "safety": bool(o.get("safety_flag") or case.get("safety")),
            "weak": not every and level == 1 and int(stat["reports"]) <= 1,
            "photos_withheld": len(every) - len(shown),
            "_key": e["key"], "_shop": e["shop"], "_photos": shown, "_every": every, "_tx": tx,
            "_window": (len(stat["cases"]), len(stat["shops"]))}


def _market_row(ctx: dict, e: dict, hide_photos: set[int]) -> dict:
    o, tx = e["o"], _row_scrub(ctx, e)
    oid = int(o["id"])
    every = ctx["photos"].get(oid, [])
    shown = [p for p in every if _eligible(p, ctx["edition"]) and int(p["id"]) not in hide_photos]
    row = {"id": oid, "ref": f"MI-{oid}", "date": e["ts"].astimezone(BAHRAIN).date().isoformat(), "kind": e["kind"],
           "sku": e["sku"], "sku_label": _sku_label(e) if e["raw_sku"] else "",
           "note": tx(mi.clean_text(o.get("note"), 1000)) or "",
           "area": tx(mi.clean_text(o.get("area"), 60)), "weak": False, "photos_withheld": len(every) - len(shown),
           "_key": ("obs", oid), "_shop": e["shop"], "_photos": shown, "_every": every, "_tx": tx}
    if e["kind"] == "competitor":
        row.update(product_seen=tx(mi.clean_text(o.get("title"), 160)),
                   brand=tx(mi.clean_text(o.get("brand_text") or o.get("brand"), 80)),
                   competitor=tx(mi.clean_text(o.get("competitor"), 80)), category=tx(o.get("category")),
                   price_seen=mi.money_out(o.get("price_bhd")))
    else:
        src = o.get("source")
        row.update(source=SIGNAL_WORDS.get(o.get("signal"), "Marketplace") if src == "system" else "Field team",
                   asked_for=tx(mi.clean_text(o.get("title"), 160)),
                   qty=_int(o.get("demand_qty")) or _int(o.get("qty_affected")), demand_level=o.get("demand_level"))
    return row


def _days_open(a: dict, as_of: datetime) -> int | None:
    made = _bh_date(a.get("created_at"))
    if not made:
        return None
    upto = as_of.astimezone(BAHRAIN).date() if a.get("status") in OPEN_ACTION_STATUSES \
        else (_bh_date(a.get("updated_at")) or as_of.astimezone(BAHRAIN).date())
    return max(0, (upto - made).days)


def _ids(values) -> set[int]:
    return {i for i in (_int(x) for x in (values or ())) if i is not None}


def assemble(ctx: dict, included=None, *, withheld_rows=(), withheld_photos=()) -> dict:
    """Rows (state included / insufficient / excluded / withheld), the finding groups, the actions and
    the counts. included=None is a fresh draft: every candidate but the insufficient ones. A withheld
    row is never shown, counted or cited; a withheld photo is never shown."""
    hide_rows, hide_photos = _ids(withheld_rows), _ids(withheld_photos)
    rows = [_finding_row(ctx, e, hide_photos) if e["kind"] in FINDING_KINDS else _market_row(ctx, e, hide_photos)
            for e in ctx["entries"]]
    ids = {r["id"] for r in rows}
    wanted = None if included is None else _ids(included)
    inc = ({r["id"] for r in rows if not r["weak"]} if wanted is None else wanted & ids) - hide_rows
    for r in rows:
        r["state"] = ("withheld" if r["id"] in hide_rows else "included" if r["id"] in inc
                      else "insufficient" if r["weak"] else "excluded")
    counted = [r for r in rows if r["state"] == "included" and r["kind"] in FINDING_KINDS]
    weak = [r for r in rows if r["state"] == "insufficient"]
    market = [r for r in rows if r["kind"] in MARKET_KINDS and r["state"] == "included"]

    as_of = ctx.get("as_of") or ctx["now"]
    actions = []
    for aid, a in sorted(ctx["actions"].items(), key=lambda kv: (kv[1].get("status") not in OPEN_ACTION_STATUSES, kv[0])):
        linked_cases = ctx["action_cases"].get(aid, set())
        due = _date_of(a.get("due_date"))
        actions.append({"id": aid, "ref": f"CA-{aid}", "request": ctx["scrub"](mi.clean_text(a.get("request"), 1000)) or "",
                        "owner": ctx["scrub"](mi.clean_text(a.get("owner"), 120)),
                        "due_date": due.isoformat() if due else None, "status": a.get("status") or "open",
                        "status_words": ACTION_STATUS_WORDS.get(a.get("status") or "open", str(a.get("status") or "")),
                        "supplier_response": ctx["scrub"](mi.clean_text(a.get("supplier_response"), 2000)),
                        "resolution_check": ctx["scrub"](mi.clean_text(a.get("resolution_check"), 1000)),
                        "created_at": a.get("created_at"), "days_open": _days_open(a, as_of),
                        "linked_observation_ids": sorted(r["id"] for r in counted if r["case_id"] in linked_cases),
                        "_cases": linked_cases})
    open_cases = {c for a in actions if a["status"] in OPEN_ACTION_STATUSES for c in a["_cases"]}

    groups: dict[tuple, dict] = {}
    for r in counted:
        groups.setdefault(r["_key"], {"key": r["_key"], "rows": []})["rows"].append(r)
    for g in groups.values():
        rs, top = g["rows"], g["rows"][0]
        g.update(kind=top["kind"], issue=top["issue"], sku=top["sku"], sku_label=top["sku_label"],
                 product=top["product"], recurring=top["recurring"], window=top["_window"], reports=len(rs),
                 shops=len({r["_shop"] for r in rs if r["_shop"]}),
                 verified=[r["id"] for r in rs if r["evidence_level"] >= VERIFIED_LEVEL],
                 safety=any(r["safety"] for r in rs), photos=sum(len(r["_photos"]) for r in rs),
                 versions=sorted({r["packaging_version"] for r in rs if r["packaging_version"]}),
                 batches=sorted({r["batch_ref"] for r in rs if r["batch_ref"]}),
                 open_action=any(r["case_id"] in open_cases for r in rs if r["case_id"] is not None))
    order = sorted(groups.values(), key=lambda g: (not g["recurring"], not g["safety"], -g["reports"],
                                                   str(g["sku_label"]), str(g["issue"]["label"])))

    by_issue: dict[tuple, dict] = {}
    for r in counted:
        b = by_issue.setdefault((r["issue"]["id"], r["kind"]), {
            "issue_type_id": r["issue"]["id"], "code": r["issue"]["code"], "label": r["issue"]["label"],
            "kind": r["kind"], "reports": 0, "shops": set(), "verified": 0, "recurring": False})
        b["reports"] += 1
        if r["_shop"]:
            b["shops"].add(r["_shop"])
        b["verified"] += 1 if r["evidence_level"] >= VERIFIED_LEVEL else 0
        b["recurring"] = b["recurring"] or r["recurring"]
    issues = [{**b, "shops": len(b["shops"])} for b in sorted(by_issue.values(), key=lambda b: (-b["reports"], str(b["label"])))]

    new = [g for g in order if not g["recurring"]]
    rec = [g for g in order if g["recurring"]]
    verified = sum(1 for r in counted if r["evidence_level"] >= VERIFIED_LEVEL)
    counts = {      # version 1: keys are only ever added (the page shows a number it does not know as a tile)
        "version": 1, "month": month_key(ctx["first"]), "edition": ctx["edition"],
        "period": {"from": ctx["start"].date().isoformat(), "to": (ctx["end"] - timedelta(days=1)).date().isoformat(),
                   "timezone": "Bahrain (UTC+3)"},
        "supplier": {"id": ctx["supplier_id"], "name": ctx["supplier"].get("name")},
        "reports": len(counted), "product_issues": sum(1 for r in counted if r["kind"] == "quality"),
        "packaging": sum(1 for r in counted if r["kind"] == "packaging"),
        "shops": len({r["_shop"] for r in counted if r["_shop"]}),
        "verified": verified, "unverified": len(counted) - verified,
        "safety": sum(1 for r in counted if r["safety"]),
        "photos": sum(len(r["_photos"]) for r in counted),
        "new": {"problems": len(new), "reports": sum(g["reports"] for g in new)},
        "recurring": {"problems": len(rec), "reports": sum(g["reports"] for g in rec)},
        "by_issue": issues,
        "insufficient": len(weak), "insufficient_observation_ids": sorted(r["id"] for r in weak),
        "excluded": sum(1 for r in rows if r["state"] == "excluded"),
        "withheld": sum(1 for r in rows if r["state"] == "withheld"),
        "photos_withheld": sum(1 for r in rows for p in r["_every"] if int(p["id"]) in hide_photos),
        "open_actions": sum(1 for a in actions if a["status"] in OPEN_ACTION_STATUSES),
        "action_ids": [a["id"] for a in actions],
        "competitor": sum(1 for r in market if r["kind"] == "competitor"),
        "demand": sum(1 for r in market if r["kind"] == "demand"),
        "rules": {"recurring": f"the same product and issue in >= {RECUR_CASES} cases or from >= {RECUR_SHOPS} "
                               f"shops within {RECUR_DAYS} days",
                  "insufficient": "no photo, evidence level 1 and a single report: listed, not counted"},
    }
    return {"rows": rows, "counted": counted, "insufficient": weak, "market": market, "groups": order,
            "actions": actions, "counts": counts, "included": sorted(inc),
            "missing": sorted((wanted or set()) - ids)}


# ── the narrative: template sentences, every finding with its ids ────────────

def _cite(ids, cap: int = MAX_CITES, sheet: str | None = None) -> str:
    ids = sorted({int(i) for i in ids})
    out = " ".join(f"[MI-{i}]" for i in ids[:cap])
    if len(ids) > cap:
        out += f" and {len(ids) - cap} more" + (f" on the {sheet} sheet" if sheet else "")
    return out


def _quoted(values: list[str], cap: int = 3) -> str:
    shown = ", ".join(f'"{v}"' for v in values[:cap])
    return shown + (f" and {len(values) - cap} more" if len(values) > cap else "")


def _where(issue_label, sku, sku_label, product) -> str:
    issue = f'"{issue_label}"'
    if sku:
        return f"{issue} on {sku} ({product})" if product and product != sku else f"{issue} on {sku}"
    what = "a product with an unverified SKU" if sku_label == UNVERIFIED_SKU else "an unidentified product"
    return f"{issue} on {what}" + (f' ("{product}")' if product else "")


def narrative(ctx: dict, a: dict) -> str:
    """One sentence per line. A line about a finding starts 'Recurring:', 'New:', 'Insufficient
    evidence:', 'We ask:', 'Competitor:' or 'Demand:' and cites [MI-<id>]; an action line starts
    'Open action [CA-<id>]'."""
    c = a["counts"]
    sup = " ".join(str(ctx["supplier"].get("name") or "Supplier").split())
    lines = [f"{sup}: {month_label(ctx['first'])}."
             + (" Internal edition: for YQ only, not for the supplier." if ctx["edition"] == "internal" else "")]
    if c["reports"]:
        s = (f"Field reports counted this month: {c['reports']} ({_plural(c['product_issues'], 'product issue')}, "
             f"{c['packaging']} packaging)")
        if c["shops"]:
            s += f" from {_plural(c['shops'], 'shop')}"
        s += f"; {c['verified']} verified by YQ"
        if c["safety"]:
            s += f"; {c['safety']} flagged as a possible safety issue"
        lines.append(s + ".")
    else:
        lines.append("No product or packaging reports were counted this month.")
    for g in a["groups"]:
        sheet = "Packaging" if g["kind"] == "packaging" else "Product issues"
        s = (f"{'Recurring' if g['recurring'] else 'New'}: "
             f"{_where(g['issue']['label'], g['sku'], g['sku_label'], g['product'])}")
        if g["versions"]:
            s += f", packaging version {_quoted(g['versions'])}"
        if g["batches"]:
            s += f", batch {_quoted(g['batches'])}"
        s += f": {_plural(g['reports'], 'report')}"
        if g["shops"]:
            s += f" from {_plural(g['shops'], 'shop')}"
        s += " " + _cite([r["id"] for r in g["rows"]], sheet=sheet)
        if g["recurring"]:
            cases, shops = g["window"]
            s += f"; {_plural(cases, 'case')}" + (f" from {_plural(shops, 'shop')}" if shops else "") \
                + f" in the last {RECUR_DAYS} days"
        if g["verified"]:
            s += f"; {len(g['verified'])} verified by YQ {_cite(g['verified'], cap=6)}"
        else:
            s += "; not yet verified"
        if g["safety"]:
            s += "; flagged as a possible safety issue"
        s += f"; {_plural(g['photos'], 'photo')} on the Evidence sheet" if g["photos"] else "; no photos shared"
        lines.append(s + ".")
    opened = [x for x in a["actions"] if x["status"] in OPEN_ACTION_STATUSES]
    for x in opened:
        s = f"Open action [CA-{x['id']}]: \"{x['request']}\""
        due = _date_of(x["due_date"])
        if due:
            s += f", due {_day(due)}"
        since = _day(_bh_date(x.get("created_at")))
        if x["status"] == "open":
            s += "; awaiting your response" + (f" since {since}" if since else "")
        else:
            s += "; your response is in and YQ is checking it"
        if x["linked_observation_ids"]:
            s += "; linked reports " + _cite(x["linked_observation_ids"], cap=6)
        lines.append(s + ".")
    if not opened:
        lines.append("Open actions: none.")
    weak_groups: dict[tuple, list[dict]] = {}
    for r in a["insufficient"]:
        weak_groups.setdefault(r["_key"], []).append(r)
    for rs in weak_groups.values():
        top = rs[0]
        many = "one report" if len(rs) == 1 else f"{len(rs)} reports"
        lines.append(f"Insufficient evidence: {many} of "
                     f"{_where(top['issue']['label'], top['sku'], top['sku_label'], top['product'])} "
                     f"{_cite([r['id'] for r in rs])}; no photo, a single report and not verified, so not "
                     "included in the counts.")
    if ctx["edition"] == "internal":
        comp = [r["id"] for r in a["market"] if r["kind"] == "competitor"]
        dem = [r["id"] for r in a["market"] if r["kind"] == "demand"]
        if comp:
            lines.append(f"Competitor: {_plural(len(comp), 'sighting')} of competing products this month "
                         f"{_cite(comp, sheet='Competitor')}.")
        if dem:
            lines.append(f"Demand: {_plural(len(dem), 'request')} for this range this month {_cite(dem, sheet='Demand')}.")
    asks = [g for g in a["groups"] if (g["recurring"] or g["verified"]) and not g["open_action"]]
    if asks:
        parts = [f"{_where(g['issue']['label'], g['sku'], g['sku_label'], g['product'])} {_cite([g['rows'][0]['id']])}"
                 for g in asks[:5]]
        more = f"; and {_plural(len(asks) - 5, 'more problem')}" if len(asks) > 5 else ""
        lines.append("We ask: the root cause and a corrective plan for " + "; ".join(parts) + more + ".")
    return "\n".join(lines)


def facts(a: dict) -> str:
    """A fingerprint of everything the template narrative states (the counts, every problem, the actions,
    the insufficient and market rows). A narrative edited by hand is approved only while it is the same
    as when the narrative was saved."""
    c = a["counts"]
    body = {"counts": {k: c.get(k) for k in ("reports", "product_issues", "packaging", "shops", "verified", "safety",
                                             "photos", "new", "recurring", "insufficient", "open_actions",
                                             "competitor", "demand")},
            "groups": [[g["sku_label"], g["issue"]["label"], g["recurring"], g["reports"], g["shops"],
                        sorted(g["verified"]), g["safety"], g["photos"], g["versions"], g["batches"],
                        sorted(r["id"] for r in g["rows"])] for g in a["groups"]],
            "actions": [[x["id"], x["status"], x["request"], x["due_date"]] for x in a["actions"]],
            "insufficient": sorted(r["id"] for r in a["insufficient"]), "market": sorted(r["id"] for r in a["market"])}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:16]


_CITED_MI = re.compile(r"\bMI-(\d+)\b")
_CITED_CA = re.compile(r"\bCA-(\d+)\b")
# a sentence ends at . ! ? after two lower-case letters, digits or a closing bracket ("Dr." and "Co." do not)
_SENTENCE_END = re.compile(r"(?<=[a-z0-9\])\"']{2}[.!?])\s+(?=[\[\"'(A-Z])")
FINDING_PREFIXES = ("recurring:", "new:", "insufficient evidence:", "we ask:", "competitor:", "demand:", "safety:",
                    "verified:", "finding:", "findings:")
# the template's sentences that state no finding, exactly as narrative() writes them
_PLAIN_SENTENCES = (
    re.compile(r"field reports counted this month: \d+ \(\d+ product issues?, \d+ packaging\)(?: from \d+ shops?)?; "
               r"\d+ verified by yq(?:; \d+ flagged as a possible safety issue)?\."),
    re.compile(r"no product or packaging reports were counted this month\."),
    re.compile(r"open actions: none\."),
    re.compile(r"internal edition: for yq only, not for the supplier\."))


def citation_problems(text: str, a: dict) -> list[str]:
    """Ids a narrative cites that the workbook does not list: an MI id must be a row the workbook shows
    (included or insufficient: never an excluded or withheld one, never another supplier's), a CA id
    one of the actions it lists."""
    shown = {r["id"] for r in a["rows"] if r["state"] in SHOWN_STATES}
    acts = {x["id"] for x in a["actions"]}
    return sorted({f"MI-{i}" for i in map(int, _CITED_MI.findall(text or "")) if i not in shown}
                  | {f"CA-{i}" for i in map(int, _CITED_CA.findall(text or "")) if i not in acts})


def uncited_findings(text: str, a: dict) -> list[str]:
    """The sentences of a narrative that state a finding without citing one. A sentence states a finding
    when it starts like a finding line ('New:', 'Recurring:', 'We ask:', ...) or names a confirmed SKU of
    the report, its catalog name or one of the report's issues; it must then cite an [MI-..]. An 'Open
    action' sentence must cite a [CA-..]. The template's own count and header sentences, exactly as it
    writes them, state no finding."""
    shown = [r for r in a["rows"] if r["state"] in SHOWN_STATES]
    keys = {str(r["sku"]).lower() for r in shown if r.get("sku")}
    keys |= {str(r["product"]).lower() for r in shown if r.get("sku") and len(str(r.get("product") or "")) >= 6}
    labels = {x for x in (str((r.get("issue") or {}).get("label") or "").lower() for r in shown) if len(x) >= 5}
    c = a["counts"]
    sup = " ".join(str((c.get("supplier") or {}).get("name") or "Supplier").split())
    header = f"{sup}: {month_label(parse_month(c['month'], future_ok=True))}.".lower() if c.get("month") else None
    out: list[str] = []
    for line in str(text or "").split("\n"):
        for s in _SENTENCE_END.split(line.strip()):
            s = s.strip()
            low = s.lower()
            if not low or low == header or any(rx.fullmatch(low) for rx in _PLAIN_SENTENCES):
                continue
            if re.match(r"open action\b(?!s)", low):
                if not _CITED_CA.search(s):
                    out.append(s)
                continue
            states = low.startswith(FINDING_PREFIXES) \
                or any(re.search(r"(?<!\w)" + re.escape(k) + r"(?!\w)", low) for k in keys) \
                or any(lab in low for lab in labels)
            if states and not _CITED_MI.search(s):
                out.append(s)
    return out


def _clean_narrative(v) -> str:
    s = str(v or "").replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", s)
    s = "\n".join(" ".join(line.split()) for line in s.split("\n")).strip()
    if len(s) > MAX_NARRATIVE:
        raise SupplierReportError(f"The narrative is limited to {MAX_NARRATIVE:,} characters.")
    return s


def _check_narrative(text: str, a: dict, status: int) -> None:
    bad = citation_problems(text, a)
    if bad:
        raise SupplierReportError("The narrative cites " + ", ".join(bad[:5]) + ", which "
                                  + ("is" if len(bad) == 1 else "are") + " not listed in this report.", status)
    loose = uncited_findings(text, a)
    if loose:
        raise SupplierReportError("Every sentence that states a finding cites the reports it rests on ([MI-..], "
                                  f"an action [CA-..]). Add one to: \"{loose[0][:120]}\"", status)


# ── the API's shapes ──────────────────────────────────────────────────────────

def _photo_out(p: dict, signed: dict, scrub) -> dict:
    path = p.get("thumb_path") or p.get("path")
    role = p.get("role") or "product"
    return {"ref": f"PH-{p['id']}", "id": int(p["id"]), "role": role, "role_words": ROLE_WORDS.get(role, "Product"),
            "caption": scrub(mi.clean_text(p.get("caption"), 200)), "thumb_url": signed.get((p.get("bucket"), path))}


def _row_out(r: dict, signed: dict, scrub) -> dict:
    out = {k: v for k, v in r.items() if not k.startswith("_") and k != "weak"}
    out["photos"] = [_photo_out(p, signed, r.get("_tx") or scrub) for p in r["_photos"]]
    return out


def _meta(rep: dict) -> dict:
    c = rep.get("counts") or {}
    return {"narrative_source": c.get("narrative_source") or "template", "facts_at_edit": c.get("facts_at_edit"),
            "withheld_observation_ids": sorted(_ids(c.get("withheld_observation_ids"))),
            "withheld_photo_ids": sorted(_ids(c.get("withheld_photo_ids"))), "built_at": c.get("built_at")}


def _report_out(rep: dict, suppliers: dict | None = None, *, scrub=None) -> dict:
    counts = rep.get("counts") or {}
    sup = (suppliers or {}).get(_int(rep.get("supplier_id"))) or counts.get("supplier") or {}
    first = _date_of(rep.get("month"))
    downloads = [{"at": d.get("at"), "by": _who(d.get("by")), "sha256": d.get("sha256")}
                 for d in (rep.get("downloads") or []) if isinstance(d, dict)]
    text = rep.get("narrative") or ""
    if scrub is not None:                       # management reads it masked, like every free text
        text = "\n".join(scrub(line) for line in text.split("\n"))
    meta = _meta(rep)
    return {"id": rep.get("id"), "supplier_id": rep.get("supplier_id"),
            "supplier": {"id": rep.get("supplier_id"), "name": sup.get("name"), "code": sup.get("code")},
            "month": month_key(first) if first else None, "month_label": month_label(first) if first else None,
            "approvable_from": approvable_from(first).date().isoformat() if first else None,
            "edition": rep.get("edition"), "status": rep.get("status"), "narrative": text,
            "narrative_source": meta["narrative_source"],
            "counts": {k: v for k, v in counts.items() if k not in ("supplier", "manifest")},
            "included_observation_ids": sorted(_ids(rep.get("included_observation_ids"))),
            "insufficient_observation_ids": counts.get("insufficient_observation_ids") or [],
            "withheld_observation_ids": meta["withheld_observation_ids"],
            "withheld_photo_ids": meta["withheld_photo_ids"], "frozen": bool(counts.get("manifest")),
            "approved_by": _who(rep.get("approved_by")), "approved_at": rep.get("approved_at"),
            "file_sha256": rep.get("file_sha256"), "downloads": downloads,
            "created_at": rep.get("created_at"), "created_by": _who(rep.get("created_by")),
            "updated_at": rep.get("updated_at"), "updated_by": _who(rep.get("updated_by"))}


def _mask_for(role: str | None):
    from app.features import masks_contacts
    return masks_contacts(role)


def _preview(ctx: dict, a: dict, rep: dict, *, role: str | None = None, now: datetime | None = None) -> dict:
    """A draft: every candidate row (included / insufficient / excluded / withheld) and the actions, as a
    supplier-safe preview (the same masked words the workbook prints), with what stands in the way of
    approval as warnings."""
    pairs = [(p.get("bucket"), p.get("thumb_path") or p.get("path")) for r in a["rows"] for p in r["_photos"]]
    signed = mi.sign_many(pairs) if pairs else {}
    scrub = ctx["scrub"]
    rows: dict[str, list[dict]] = {"product_issues": [], "packaging": []}
    if ctx["edition"] == "internal":
        rows.update(competitor=[], demand=[])
    rank = {"included": 0, "insufficient": 1, "excluded": 2, "withheld": 3}
    for r in sorted(a["rows"], key=lambda r: (rank[r["state"]], r["date"], r["id"])):
        rows[SHEET_OF[r["kind"]]].append(_row_out(r, signed, scrub))
    label = month_label(ctx["first"])
    warnings = [f"MI-{i} no longer belongs to this supplier's {label} reports; edit the rows or build a new "
                "report." for i in a["missing"]]
    warnings += [f"MI-{c['id']} is left out: its supplier links disagree ({c['links']}). Correct the case's or the "
                 "sighting's supplier or brand, then rebuild; the report cannot be approved until then."
                 for c in ctx["conflicts"]]
    opens = approvable_from(ctx["first"])
    if rep.get("status") == "draft" and (now or _now()) < opens:
        warnings.append(f"{label} is not closed yet: reports can still arrive until {_day((opens - timedelta(days=1)).date())}, "
                        f"so the counts may change. It can be approved from {_day(opens.date())}.")
    meta = _meta(rep)
    if rep.get("status") == "draft" and meta["narrative_source"] == "edited" and meta["facts_at_edit"] != facts(a):
        warnings.append("The data changed since the narrative was edited: read it again and save it (or rebuild) "
                        "before the report is approved.")
    return {"available": True, "frozen": False,
            "report": _report_out(rep, {ctx["supplier_id"]: ctx["supplier"]}, scrub=scrub if _mask_for(role) else None),
            "rows": rows,
            "groups": [{"sku": g["sku"], "sku_label": g["sku_label"], "product": g["product"], "issue": g["issue"],
                        "kind": g["kind"], "recurring": g["recurring"], "reports": g["reports"], "shops": g["shops"],
                        "verified": len(g["verified"]), "safety": g["safety"],
                        "observation_ids": [r["id"] for r in g["rows"]]} for g in a["groups"]],
            "actions": [{k: v for k, v in x.items() if not k.startswith("_")} for x in a["actions"]],
            "live_counts": a["counts"], "warnings": warnings}


# ── the routes' work ──────────────────────────────────────────────────────────

def _report(report_id) -> dict:
    rid = _int(report_id)
    got: list[dict] = []
    if rid is not None:
        got = get_client().table("supplier_reports").select("*").eq("id", rid).limit(1).execute().data or []
    if not got:
        raise SupplierReportError("That supplier report does not exist.", 404)
    return got[0]


def _first_of(rep: dict) -> date:
    first = _date_of(rep.get("month"))
    if not first:
        raise SupplierReportError("That report has no month.", 409)
    return first.replace(day=1)


def _ctx_for(rep: dict, *, now: datetime | None = None) -> dict:
    return gather(rep.get("supplier_id"), _first_of(rep), str(rep.get("edition") or "supplier"), now=now)


def _assembled(ctx: dict, rep: dict) -> dict:
    meta = _meta(rep)
    return assemble(ctx, rep.get("included_observation_ids") or [], withheld_rows=meta["withheld_observation_ids"],
                    withheld_photos=meta["withheld_photo_ids"])


@_tables
def build_draft(supplier_id, month, edition: str, actor: str, role: str, *, now: datetime | None = None) -> dict:
    """Build the month's draft for a supplier and edition. An existing draft of the same supplier, month
    and edition is rebuilt in place: hand edits, withheld rows and photos are reset ("rebuilt": true)."""
    _need(role, BUILD_ROLES, "Only the office can build supplier reports.")
    now = now or _now()
    edition = str(edition or "supplier").strip().lower()
    if edition not in EDITIONS:
        raise SupplierReportError("The edition is 'supplier' or 'internal'.")
    first = parse_month(month, now=now)
    ctx = gather(supplier_id, first, edition, now=now)
    a = assemble(ctx)
    counts = {**a["counts"], "narrative_source": "template", "facts_at_edit": None, "withheld_observation_ids": [],
              "withheld_photo_ids": [], "built_at": _iso(now)}
    text = narrative(ctx, a)
    c = get_client()
    drafts = (c.table("supplier_reports").select("*").eq("supplier_id", ctx["supplier_id"])
              .eq("month", first.isoformat()).eq("edition", edition).eq("status", "draft")
              .order("id", desc=True).limit(1).execute().data or [])
    body = {"narrative": text, "included_observation_ids": a["included"], "counts": counts,
            "updated_by": actor or None, "updated_at": _iso(now)}
    if drafts:
        got = c.table("supplier_reports").update(body).eq("id", drafts[0]["id"]).eq("status", "draft").execute().data
        if got is not None and len(got) == 0:
            raise SupplierReportError("Someone changed this report meanwhile. Reload and try again.", 409)
        rep = (got or [{**drafts[0], **body}])[0]
        rebuilt = True
    else:
        row = {"supplier_id": ctx["supplier_id"], "month": first.isoformat(), "edition": edition, "status": "draft",
               "created_by": actor or None, **body}
        rep = (c.table("supplier_reports").insert(row).execute().data or [row])[0]
        rebuilt = False
    return {**_preview(ctx, a, rep, role=role, now=now), "rebuilt": rebuilt}


@_tables
def list_reports(role: str, *, supplier_id=None, month=None, edition=None, status=None, offset: int = 0,
                 limit: int = 50) -> dict:
    _need(role, READ_ROLES, "Supplier reports are for the office and management.")
    lim = max(1, min(int(limit or 50), 200))
    off = max(0, int(offset or 0))
    q = get_client().table("supplier_reports").select("*", count="exact")
    if supplier_id not in (None, ""):
        if _int(supplier_id) is None:
            raise SupplierReportError("Unknown supplier.")
        q = q.eq("supplier_id", _int(supplier_id))
    if month not in (None, ""):
        q = q.eq("month", parse_month(month, future_ok=True).isoformat())
    if edition not in (None, ""):
        if edition not in EDITIONS:
            raise SupplierReportError("The edition is 'supplier' or 'internal'.")
        q = q.eq("edition", edition)
    if status not in (None, ""):
        if status not in STATUSES:
            raise SupplierReportError("The status is draft, approved or superseded.")
        q = q.eq("status", status)
    res = q.order("month", desc=True).order("id", desc=True).range(off, off + lim - 1).execute()
    rows = res.data or []
    sup_ids = sorted({_int(r.get("supplier_id")) for r in rows} - {None})
    suppliers = {int(s["id"]): s for s in _by("suppliers", "id", sup_ids)} if sup_ids else {}
    out = []
    for r in rows:
        o = _report_out(r, suppliers)
        o.pop("narrative", None)
        o["downloads"] = len(o["downloads"])
        out.append(o)
    total = res.count if getattr(res, "count", None) is not None else off + len(rows)
    return {"available": True, "rows": out, "count": int(total), "offset": off, "limit": lim}


@_tables
def get_report(report_id, role: str, *, now: datetime | None = None) -> dict:
    """The preview. A draft is read live; an approved or superseded report shows its frozen manifest
    (and, approved, what would stop its download)."""
    _need(role, READ_ROLES, "Supplier reports are for the office and management.")
    rep = _report(report_id)
    manifest = (rep.get("counts") or {}).get("manifest")
    if rep.get("status") != "draft" and manifest:
        return _frozen_view(rep, manifest, role)
    ctx = _ctx_for(rep, now=now)
    out = _preview(ctx, _assembled(ctx, rep), rep, role=role, now=now)
    if rep.get("status") != "draft":
        out["warnings"].append("This report was approved before frozen copies existed: build and approve it again "
                               "to download it.")
    return out


def _id_list(changes: dict, key: str, pool: set[int], prefix: str, what: str, label: str) -> list[int] | None:
    raw = changes.get(key)
    if raw is None:
        return None
    vals = []
    for x in raw:
        if _int(x) is None:
            raise SupplierReportError(f"{key} holds {what} ids (numbers).")
        vals.append(int(x))
    if len(vals) > MAX_IDS:
        raise SupplierReportError(f"At most {MAX_IDS:,} ids.")
    foreign = sorted(set(vals) - pool)
    if foreign:
        shown = ", ".join(f"{prefix}-{i}" for i in foreign[:5])
        raise SupplierReportError(f"{shown} {'is' if len(foreign) == 1 else 'are'} not among this supplier's "
                                  f"{what}s for {label}.")
    return sorted(set(vals))


@_tables
def update_draft(report_id, changes: dict, actor: str, role: str, *, now: datetime | None = None) -> dict:
    """Reviewers, a draft only: {narrative?, included_observation_ids?, withheld_observation_ids?,
    withheld_photo_ids?}. Every id must be one of this supplier's sightings (photos) of the month. A
    narrative may cite only rows the workbook lists, and each sentence of it that states a finding must
    cite one. New rows with an untouched (template) narrative rewrite it; one edited by hand is kept."""
    _need(role, BUILD_ROLES, "Only the office can edit supplier reports.")
    now = now or _now()
    rep = _report(report_id)
    if rep.get("status") != "draft":
        raise SupplierReportError(f"Only a draft can be edited (this report is {rep.get('status')}).", 409)
    ctx = _ctx_for(rep, now=now)
    meta = _meta(rep)
    label = month_label(ctx["first"])
    known = {int(e["o"]["id"]) for e in ctx["entries"]}
    photo_ids = {int(p["id"]) for ps in ctx["photos"].values() for p in ps}
    included = _id_list(changes, "included_observation_ids", known, "MI", "report", label)
    hide_rows = _id_list(changes, "withheld_observation_ids", known, "MI", "report", label)
    hide_photos = _id_list(changes, "withheld_photo_ids", photo_ids, "PH", "photo", label)
    rows_changed = any(x is not None for x in (included, hide_rows, hide_photos))
    if included is None:
        included = sorted(_ids(rep.get("included_observation_ids")))
    hide_rows = meta["withheld_observation_ids"] if hide_rows is None else hide_rows
    hide_photos = meta["withheld_photo_ids"] if hide_photos is None else hide_photos
    a = assemble(ctx, included, withheld_rows=hide_rows, withheld_photos=hide_photos)
    source, facts_at_edit, text = meta["narrative_source"], meta["facts_at_edit"], rep.get("narrative") or ""
    warnings: list[str] = []
    if changes.get("narrative") is not None:
        text = _clean_narrative(changes["narrative"])
        _check_narrative(text, a, 400)
        source, facts_at_edit = "edited", facts(a)
    elif rows_changed and source == "template":
        text = narrative(ctx, a)
    elif rows_changed:
        warnings.append("The narrative was edited by hand, so it was kept: read it again and save it (it must "
                        "match the rows) before the report can be approved.")
    body = {"narrative": text, "included_observation_ids": a["included"],
            "counts": {**a["counts"], "narrative_source": source, "facts_at_edit": facts_at_edit,
                       "withheld_observation_ids": sorted(_ids(hide_rows)), "withheld_photo_ids": sorted(_ids(hide_photos)),
                       "built_at": meta["built_at"]},
            "updated_by": actor or None, "updated_at": _iso(now)}
    got = (get_client().table("supplier_reports").update(body).eq("id", rep["id"]).eq("status", "draft")
           .execute().data)
    if got is not None and len(got) == 0:
        raise SupplierReportError("Someone changed this report meanwhile. Reload and try again.", 409)
    out = _preview(ctx, a, (got or [{**rep, **body}])[0], role=role, now=now)
    out["warnings"] += warnings
    return out


def _restore(ids: list[int], actor: str) -> None:
    """Put back the reports an approval superseded before it failed. One that cannot go back (a newer
    approval stands: the one-approved index refuses) stays superseded, which is then right."""
    for i in ids:
        try:
            get_client().table("supplier_reports").update({"status": "approved", "updated_by": actor or None,
                                                           "updated_at": _iso()}) \
                .eq("id", i).eq("status", "superseded").execute()
        except Exception as e:  # noqa: BLE001
            log.warning("supplier reports: report %s not put back after a failed approval: %s", i, e)


@_tables
def approve(report_id, actor: str, role: str, *, now: datetime | None = None) -> dict:
    """Management (or an admin): a draft -> approved, once its month is over plus 7 days. The data is
    read again: a row whose links now disagree, or that left this supplier, stops it; a template
    narrative is rewritten from the data as it is now, one edited by hand must still match it. The
    workbook's content is frozen on the row (counts.manifest); the earlier approved report of the same
    supplier, month and edition becomes superseded. A race lost to another approval is a 409; any
    failure puts the superseded report back."""
    _need(role, APPROVER_ROLES, "Only management (or an admin) approves supplier reports.")
    now = now or _now()
    rep = _report(report_id)
    if rep.get("status") != "draft":
        raise SupplierReportError(f"Only a draft can be approved (this report is {rep.get('status')}).", 409)
    first = _first_of(rep)
    opens = approvable_from(first)
    if now < opens:
        raise SupplierReportError(f"{month_label(first)} can be approved from {_day(opens.date())}: the month must be "
                                  f"over, plus {APPROVE_AFTER_DAYS} days for the reports that arrive late.", 409)
    ctx = _ctx_for(rep, now=now)
    a = _assembled(ctx, rep)
    if a["missing"]:
        shown = ", ".join(f"MI-{i}" for i in a["missing"][:5])
        raise SupplierReportError(f"{shown} no longer belong{'s' if len(a['missing']) == 1 else ''} to this supplier's "
                                  "reports for the month: edit the rows or build a new report.", 409)
    if ctx["conflicts"]:
        shown = ", ".join(f"MI-{c['id']}" for c in ctx["conflicts"][:10])
        raise SupplierReportError(f"The supplier links of {shown} disagree: correct them (the case's or the sighting's "
                                  "supplier or brand) before this report is approved.", 409)
    meta = _meta(rep)
    if meta["narrative_source"] == "template":
        text = narrative(ctx, a)
    else:
        text = rep.get("narrative") or ""
        if meta["facts_at_edit"] != facts(a):
            raise SupplierReportError("The data changed since the narrative was edited by hand: read it again and "
                                      "save it (or rebuild the draft) before approving.", 409)
    _check_narrative(text, a, 409)
    manifest = freeze(ctx, a, text, now)
    counts = {**a["counts"], "narrative_source": meta["narrative_source"], "facts_at_edit": meta["facts_at_edit"],
              "withheld_observation_ids": meta["withheld_observation_ids"],
              "withheld_photo_ids": meta["withheld_photo_ids"], "built_at": meta["built_at"], "manifest": manifest}
    c = get_client()
    olds = [r for r in (c.table("supplier_reports").select("id,status").eq("supplier_id", rep["supplier_id"])
                        .eq("month", rep["month"]).eq("edition", rep["edition"]).eq("status", "approved")
                        .execute().data or []) if int(r["id"]) != int(rep["id"])]
    superseded: list[int] = []
    try:
        # supersede first (the one-approved index is never in the way), then approve
        for r in olds:
            got = (c.table("supplier_reports").update({"status": "superseded", "updated_by": actor or None,
                                                       "updated_at": _iso(now)})
                   .eq("id", r["id"]).eq("status", "approved").execute().data)
            if got is None or len(got) > 0:
                superseded.append(int(r["id"]))
        got = (c.table("supplier_reports").update({"status": "approved", "approved_by": actor or None,
                                                   "approved_at": _iso(now), "narrative": text, "counts": counts,
                                                   "included_observation_ids": a["included"],
                                                   "updated_by": actor or None, "updated_at": _iso(now)})
               .eq("id", rep["id"]).eq("status", "draft").execute().data)
        if got is not None and len(got) == 0:
            raise SupplierReportError("Someone changed this report meanwhile. Reload and try again.", 409)
    except Exception as e:  # noqa: BLE001 — whatever failed, the month is left as it was
        _restore(superseded, actor)
        if isinstance(e, SupplierReportError):
            raise
        if mi._unique_violation(e):
            raise SupplierReportError("Another approval of this supplier's month went through first. Reload and look "
                                      "at the approved report.", 409) from None
        raise
    lost = [f"PH-{p['id']}" for p in manifest["photos"] if p["state"] == "unavailable"]
    warnings = [f"{', '.join(lost[:10])} could not be read at approval and show as \"{PHOTO_NOT_AVAILABLE}\": if that "
                "was a passing storage fault, build and approve the report again."] if lost else []
    return {"ok": True, "id": int(rep["id"]), "status": "approved", "approved_at": _iso(now),
            "approved_by": _who(actor), "superseded": superseded, "warnings": warnings}


def filename(rep: dict, supplier: dict) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(supplier.get("code") or supplier.get("name") or "")).strip("-")[:40]
    first = _date_of(rep.get("month"))
    return (f"YQ-supplier-report-{slug or 'supplier-' + str(rep.get('supplier_id'))}-"
            f"{month_key(first) if first else 'month'}-{rep.get('edition') or 'supplier'}.xlsx")


@_tables
def download(report_id, actor: str, role: str, *, now: datetime | None = None) -> dict:
    """Reviewers and management, an approved report only: the workbook of its frozen manifest. Refused
    (nothing served) when what was approved may no longer go out, when a photo cannot be read back
    exactly as approved, or when the download cannot be recorded ({at, by, sha256}; file_sha256)."""
    _need(role, READ_ROLES, "Supplier reports are for the office and management.")
    now = now or _now()
    rep = _report(report_id)
    if rep.get("status") != "approved":
        raise SupplierReportError(f"Only an approved report can be downloaded (this report is {rep.get('status')}).",
                                  409)
    m = (rep.get("counts") or {}).get("manifest")
    if not isinstance(m, dict) or m.get("version") != MANIFEST_VERSION:
        raise SupplierReportError("This report was approved before frozen copies existed: build and approve it again.",
                                  409)
    check = frozen_check(m)
    if check["problems"]:
        raise SupplierReportError("What was approved can no longer go out: " + "; ".join(check["problems"][:5])
                                  + ". Build and approve a new report.", 409)
    pics = fetch_frozen(m["photos"])
    data = render_workbook(m, pics, check["scrub"])
    sha = hashlib.sha256(data).hexdigest()
    entry = {"at": _iso(now), "by": actor or None, "sha256": sha}
    c = get_client()
    for _attempt in range(3):
        cur = _report(rep["id"])
        if cur.get("status") != "approved":
            raise SupplierReportError("This report was superseded meanwhile. Download the newer one.", 409)
        q = c.table("supplier_reports").update({"downloads": list(cur.get("downloads") or []) + [entry],
                                                "file_sha256": sha, "updated_at": _iso(_now())}) \
            .eq("id", cur["id"]).eq("status", "approved")
        if cur.get("updated_at"):
            q = q.eq("updated_at", cur["updated_at"])
        got = q.execute().data
        if got is None or len(got) > 0:
            return {"filename": filename(rep, m.get("supplier") or {}), "data": data, "sha256": sha, "bytes": len(data),
                    "report_id": int(rep["id"]), "edition": m.get("edition"), "month": m.get("month")}
    raise SupplierReportError("The download could not be recorded. Try again.", 409)


# ── the frozen manifest: what an approval sends, and only that ───────────────

def freeze(ctx: dict, a: dict, text: str, at: datetime) -> dict:
    """Everything the workbook prints, as approved: the listed rows (masked words, photo ids), each photo
    as the object read back now (its path and the sha256 of its bytes), the actions, the counts and the
    narrative (masked). The download renders this and reads nothing else but the photos it names."""
    scrub = ctx["scrub"]
    listed = sorted((r for r in a["rows"] if r["state"] in SHOWN_STATES),
                    key=lambda r: (r["state"] != "included", r["date"], r["id"]))
    photos = [(p, r.get("_tx") or scrub) for r in listed for p in r["_photos"]]
    got = fetch_originals([p for p, _tx in photos[:MAX_PHOTOS]])
    frozen_photos = []
    for i, (p, tx) in enumerate(photos):
        o = got.get(int(p["id"])) if i < MAX_PHOTOS else None
        frozen_photos.append({"id": int(p["id"]), "observation_id": int(p["observation_id"]),
                              "role": p.get("role") or "product", "caption": tx(mi.clean_text(p.get("caption"), 200)) or "",
                              "bucket": p.get("bucket"), "path": (o or {}).get("path"), "sha256": (o or {}).get("sha256"),
                              "shown_sha": (str(p.get("sha256") or (o or {}).get("master_sha") or "")[:12]) or None,
                              "state": "embedded" if o else ("unavailable" if i < MAX_PHOTOS else "over_limit")})
    rows = []
    for r in listed:
        out = _row_out(r, {}, scrub)
        out.pop("photos", None)
        out["photo_ids"] = [int(p["id"]) for p in r["_photos"]]
        out["sheet"] = SHEET_OF[r["kind"]]
        rows.append(out)
    return {"version": MANIFEST_VERSION, "edition": ctx["edition"], "month": month_key(ctx["first"]),
            "supplier": {"id": ctx["supplier_id"], "name": ctx["supplier"].get("name"), "code": ctx["supplier"].get("code")},
            "approved_at": _iso(at), "narrative": "\n".join(scrub(line) for line in text.split("\n")),
            "counts": {k: v for k, v in a["counts"].items() if k != "supplier"},
            "groups": [{"sku": g["sku"], "sku_label": g["sku_label"], "product": g["product"], "issue": g["issue"],
                        "kind": g["kind"], "recurring": g["recurring"], "reports": g["reports"], "shops": g["shops"],
                        "verified": len(g["verified"]), "safety": g["safety"],
                        "observation_ids": [r["id"] for r in g["rows"]]} for g in a["groups"]],
            "rows": rows, "photos": frozen_photos,
            "actions": [{k: v for k, v in x.items() if not k.startswith("_")} for x in a["actions"]]}


def frozen_check(m: dict) -> dict:
    """What would stop a frozen manifest going out now ({"problems": [...], "scrub", "current_photos"}): a
    row deleted, no longer this supplier's alone, or found not substantiated; a photo deleted, flagged
    with people, moved, or of a role this edition never shows; an action deleted or moved to another
    supplier. Also the scrub as it stands now (a name the database learnt since is masked too)."""
    sid = int(m["supplier"]["id"])
    edition = m.get("edition") or "supplier"
    suppliers = {int(r["id"]): r for r in _all(lambda: _q("suppliers"))}
    brands = {int(r["id"]): r for r in _all(lambda: _q("brands"))}
    obs = {int(o["id"]): o for o in _by("market_observations", "id", [r["id"] for r in m["rows"]])}
    cases = _cases_for(o.get("case_id") for o in obs.values())
    name = (suppliers.get(sid) or {}).get("name") or "this supplier"
    problems: list[str] = []
    for r in m["rows"]:
        o = obs.get(int(r["id"]))
        if not o:
            problems.append(f"MI-{r['id']} has been deleted")
            continue
        case = _live_case(o.get("case_id"), cases)
        links = _links(o, case, brands, suppliers)
        if set(links.values()) != {sid}:
            problems.append(f"MI-{r['id']} is no longer {name}'s alone ({_links_words(links, suppliers) or 'no supplier'})")
        elif _dropped(case):
            problems.append(f"MI-{r['id']} is no longer a finding (its case was found not substantiated)")
    current = {int(p["id"]): p for p in _by("market_photos", "id", [p["id"] for p in m["photos"]])}
    for p in m["photos"]:
        now_p = current.get(int(p["id"]))
        if not now_p:
            problems.append(f"PH-{p['id']} has been deleted")
        elif now_p.get("has_people_flag"):
            problems.append(f"PH-{p['id']} is now flagged with people")
        elif _int(now_p.get("observation_id")) != int(p["observation_id"]):
            problems.append(f"PH-{p['id']} was moved to another report")
        elif not _eligible(now_p, edition):
            problems.append(f"PH-{p['id']} is now a {(now_p.get('role') or 'product')} photo, which this edition never shows")
    acts = {int(x["id"]): x for x in _by("supplier_actions", "id", [x["id"] for x in m["actions"]])}
    family = {i for i in suppliers if _live(i, suppliers) == sid}
    for x in m["actions"]:
        cur = acts.get(int(x["id"]))
        if not cur:
            problems.append(f"CA-{x['id']} has been deleted")
        elif _int(cur.get("supplier_id")) is not None and _live(cur.get("supplier_id"), suppliers) not in family:
            problems.append(f"CA-{x['id']} now belongs to another supplier")
    scrub = scrubber_for(sid, suppliers, brands, [o.get("shop_name") for o in obs.values()])
    return {"problems": problems, "scrub": scrub, "current_photos": current}


def _frozen_view(rep: dict, m: dict, role: str | None) -> dict:
    """An approved (or superseded) report as it was approved: the frozen rows and actions, thumbnails of
    the frozen photos that are still fit to show, and (approved) what would stop its download."""
    check = frozen_check(m) if rep.get("status") == "approved" else None
    current = (check or {}).get("current_photos")
    if current is None:
        current = {int(p["id"]): p for p in _by("market_photos", "id", [p["id"] for p in m["photos"]])}
    fit = {int(p["id"]): current[int(p["id"])] for p in m["photos"]
           if int(p["id"]) in current and _eligible(current[int(p["id"])], m.get("edition") or "supplier")}
    signed = mi.sign_many([(p.get("bucket"), p.get("thumb_path") or p.get("path")) for p in fit.values()]) if fit else {}
    photos = {int(p["id"]): p for p in m["photos"]}
    rows: dict[str, list[dict]] = {"product_issues": [], "packaging": []}
    if m.get("edition") == "internal":
        rows.update(competitor=[], demand=[])
    for r in m["rows"]:
        out = {k: v for k, v in r.items() if k not in ("photo_ids", "sheet")}
        out["photos"] = []
        for pid in r.get("photo_ids") or []:
            fp, cur = photos.get(int(pid)) or {}, fit.get(int(pid))
            role_ = fp.get("role") or "product"
            out["photos"].append({"ref": f"PH-{pid}", "id": int(pid), "role": role_, "role_words": ROLE_WORDS.get(role_, "Product"),
                                  "caption": fp.get("caption"),
                                  "thumb_url": signed.get((cur.get("bucket"), cur.get("thumb_path") or cur.get("path"))) if cur else None})
        rows.setdefault(r.get("sheet") or "product_issues", []).append(out)
    report = _report_out(rep, {int(m["supplier"]["id"]): m["supplier"]})
    report["narrative"] = m.get("narrative") or ""
    if _mask_for(role) and check:
        report["narrative"] = "\n".join(check["scrub"](line) for line in report["narrative"].split("\n"))
    return {"available": True, "frozen": True, "report": report, "rows": rows, "groups": m.get("groups") or [],
            "actions": m.get("actions") or [], "live_counts": None,
            "warnings": [f"Cannot be downloaded: {p}." for p in (check or {}).get("problems", [])]}


# ── photos ────────────────────────────────────────────────────────────────────

def _jpeg(data: bytes, px: int) -> bytes | None:
    """A photo as a JPEG of at most px on its longest side, without metadata; None if unreadable."""
    from PIL import Image, ImageOps
    try:
        img = Image.open(io.BytesIO(data))
        if img.size[0] * img.size[1] > mi.MAX_PIXELS:
            return None
        img.draft("RGB", (px, px))
        img.load()
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "P", "PA"):
            rgba = img.convert("RGBA")
            base = Image.new("RGB", rgba.size, (255, 255, 255))
            base.paste(rgba, mask=rgba.getchannel("A"))
            img = base
        elif img.mode != "RGB":
            img = img.convert("RGB")
        img.thumbnail((px, px), Image.Resampling.LANCZOS)
        img.info = {}
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=72, optimize=True)
        return out.getvalue()
    except Exception:  # noqa: BLE001 — truncated, corrupt or not a picture
        return None


def _object(bucket, path) -> bytes | None:
    if bucket not in mi.BUCKETS or not path:
        return None
    try:
        raw = get_client().storage.from_(bucket).download(path)
    except Exception as e:  # noqa: BLE001
        log.info("supplier report: %s/%s not fetched: %s", bucket, path, e)
        return None
    return bytes(raw) if raw else None


def fetch_originals(photos: list[dict]) -> dict[int, dict | None]:
    """At approval: {photo id: {"path", "sha256", "master_sha"} or None} — the master read from the
    private bucket (its 320 px thumbnail when the master cannot be read), checked to be a picture."""
    def one(p: dict):
        for path, master in ((p.get("path"), True), (p.get("thumb_path"), False)):
            raw = _object(p.get("bucket"), path)
            if raw is None or _jpeg(raw, THUMB_PX) is None:
                continue
            sha = hashlib.sha256(raw).hexdigest()
            return int(p["id"]), {"path": path, "sha256": sha, "master_sha": sha if master else None}
        return int(p["id"]), None
    if not photos:
        return {}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="sr-photo") as ex:
        return dict(ex.map(one, photos))


def fetch_frozen(photos: list[dict]) -> dict[int, dict]:
    """At download: every embedded photo of a manifest read back from the object it was approved from,
    byte for byte (sha256), as {id: {"evidence": 640 px JPEG, "thumb": 160 px JPEG}}. A photo changed in
    storage is a 409; one that cannot be read now is a 503 (try again): nothing is served either way."""
    want = [p for p in photos if p.get("state") == "embedded"]

    def one(p: dict):
        raw = _object(p.get("bucket"), p.get("path"))
        if raw is None:
            return int(p["id"]), "missing"
        if hashlib.sha256(raw).hexdigest() != p.get("sha256"):
            return int(p["id"]), "changed"
        ev, th = _jpeg(raw, EVIDENCE_PX), _jpeg(raw, THUMB_PX)
        return int(p["id"]), ({"evidence": ev, "thumb": th} if ev and th else "missing")
    if not want:
        return {}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="sr-photo") as ex:
        got = dict(ex.map(one, want))
    changed = sorted(i for i, v in got.items() if v == "changed")
    if changed:
        raise SupplierReportError(", ".join(f"PH-{i}" for i in changed[:5]) + " changed in storage since the report "
                                  "was approved: build and approve a new report.", 409)
    missing = sorted(i for i, v in got.items() if v == "missing")
    if missing:
        raise SupplierReportError(", ".join(f"PH-{i}" for i in missing[:5]) + " could not be read from storage just "
                                  "now: try the download again.", 503)
    return got


# ── the workbook (rendered from a frozen manifest only) ──────────────────────

FINDING_COLUMNS = [("ID", 10), ("Date", 11), ("SKU", 14), ("Product", 30), ("Issue", 24), ("Finding", 17),
                   ("Description", 46), ("Qty", 6), ("Batch / packaging", 20), ("Basis", 15),
                   ("Evidence level", 22), ("Status", 18), ("Photo", 16), ("Evidence refs", 18)]
PACKAGING_COLUMNS = FINDING_COLUMNS[:9] + [("Packaging version", 16)] + FINDING_COLUMNS[9:]
EVIDENCE_COLUMNS = [("Photo ref", 11), ("Report ID", 11), ("SKU", 14), ("Issue", 24), ("Role", 14), ("Caption", 34),
                    ("SHA-256 (short)", 16), ("Photo", 48)]
ACTION_COLUMNS = [("Action", 9), ("Request", 42), ("Owner", 16), ("Due", 12), ("Supplier response", 36),
                  ("YQ check", 30), ("Status", 24), ("Days open", 10), ("Linked reports", 22)]
COMPETITOR_COLUMNS = [("ID", 10), ("Date", 11), ("Product seen", 30), ("Brand", 14), ("Competitor", 18),
                      ("Category", 14), ("Price seen (BHD)", 14), ("YQ SKU", 14), ("Note", 40), ("Area", 14),
                      ("Photo", 16)]
DEMAND_COLUMNS = [("ID", 10), ("Date", 11), ("Source", 26), ("Asked for", 30), ("YQ SKU", 14), ("Qty asked", 9),
                  ("Demand level", 12), ("Note", 40), ("Area", 14)]
SHEETS = {"supplier": ["Summary", "Product issues", "Packaging", "Evidence", "Corrective actions"],
          "internal": ["Summary", "Product issues", "Packaging", "Evidence", "Corrective actions", "Competitor",
                       "Demand"]}
SHOWN_THUMB_PX = 96               # a finding row's picture as drawn in the sheet
SHOWN_EVIDENCE_PX = 320           # the Evidence sheet's picture as drawn (the file keeps 640 px)


def render_workbook(m: dict, pics: dict[int, dict], scrub=None) -> bytes:
    """The supplier (or internal) workbook of a frozen manifest. Every free-text cell is masked again
    with the scrub as it stands (`scrub`; masking only ever adds). The bytes are stable for the same
    manifest and photos (every date in the file is the approval time)."""
    from openpyxl import Workbook
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    scrub = scrub or (lambda v: v)
    edition = m.get("edition") or "supplier"
    first = parse_month(m["month"], future_ok=True)
    start, end = month_bounds(first)
    stamp = _ts(m.get("approved_at")) or _now()
    head_fill = PatternFill("solid", fgColor="6D28D9")
    head_font = Font(color="FFFFFF", bold=True)
    bold = Font(bold=True)
    top = Alignment(vertical="top", wrap_text=True)
    photos = {int(p["id"]): p for p in m.get("photos") or []}
    rows = m.get("rows") or []
    finding_rows = [r for r in rows if r.get("kind") in FINDING_KINDS]
    market_rows = [r for r in rows if r.get("kind") in MARKET_KINDS]

    wb = Workbook()
    wb.properties.creator = "YQ Bahrain"
    wb.properties.lastModifiedBy = "YQ Bahrain"
    wb.properties.title = f"Supplier report {m['month']}"
    wb.properties.created = stamp.astimezone(timezone.utc).replace(tzinfo=None)

    def put(ws, row: int, col: int, value, *, text: bool = False):
        v = scrub(value) if text and isinstance(value, str) else value
        if isinstance(v, str):
            v = ILLEGAL_CHARACTERS_RE.sub("", v)      # a control character would make openpyxl refuse the file
        cell = ws.cell(row=row, column=col, value=v)
        if isinstance(v, str) and v.startswith("="):
            cell.data_type = "s"          # typed text is never a formula
        cell.alignment = top
        return cell

    def head(ws, row: int, cols: list[tuple[str, int]], *, widths: bool = True) -> None:
        for j, (h, w) in enumerate(cols, start=1):
            cell = ws.cell(row=row, column=j, value=h)
            cell.fill, cell.font = head_fill, head_font
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            if widths:
                ws.column_dimensions[get_column_letter(j)].width = w

    def picture(ws, row: int, col: int, jpeg: bytes, shown: int) -> float:
        im = XLImage(io.BytesIO(jpeg))
        scale = shown / max(im.width, im.height, 1)
        im.width, im.height = max(1, int(im.width * scale)), max(1, int(im.height * scale))
        ws.add_image(im, f"{get_column_letter(col)}{row}")
        return im.height * 0.75 + 6

    def refs(r: dict) -> str:
        return ", ".join(f"PH-{i}" for i in r.get("photo_ids") or []) or "none"

    def photo_cell(ws, row: int, col: int, r: dict) -> None:
        ids = r.get("photo_ids") or []
        if not ids:
            put(ws, row, col, "no photo shared" if r.get("photos_withheld") else "no photo")
            return
        fp = photos.get(int(ids[0])) or {}
        got = pics.get(int(ids[0]))
        if got and got.get("thumb"):
            ws.row_dimensions[row].height = max(ws.row_dimensions[row].height or 15,
                                                picture(ws, row, col, got["thumb"], SHOWN_THUMB_PX))
        else:
            put(ws, row, col, "see the Evidence sheet" if fp.get("state") == "over_limit" else PHOTO_NOT_AVAILABLE)

    # 1. Summary
    ws = wb.active
    ws.title = "Summary"
    for col, w in zip("ABCDEF", (36, 30, 12, 12, 12, 18)):
        ws.column_dimensions[col].width = w
    c = m.get("counts") or {}
    sup_name = " ".join(str((m.get("supplier") or {}).get("name") or "Supplier").split())
    put(ws, 1, 1, f"{sup_name}: product feedback, {month_label(first)}", text=True).font = Font(bold=True, size=14)
    facts_rows = [("Period", f"{_day(start.date())} to {_day((end - timedelta(days=1)).date())} (Bahrain time)"),
                  ("Edition", EDITION_LABELS[edition]), ("Status", f"Approved {_day(_bh_date(m.get('approved_at')))}"),
                  ("Reports counted", c.get("reports", 0)), ("Product issues", c.get("product_issues", 0)),
                  ("Packaging", c.get("packaging", 0)), ("Shops reporting", c.get("shops", 0)),
                  ("New problems", f"{(c.get('new') or {}).get('problems', 0)} "
                                   f"({_plural((c.get('new') or {}).get('reports', 0), 'report')})"),
                  ("Recurring problems", f"{(c.get('recurring') or {}).get('problems', 0)} "
                                         f"({_plural((c.get('recurring') or {}).get('reports', 0), 'report')})"),
                  ("Verified by YQ", c.get("verified", 0)), ("Possible safety issues", c.get("safety", 0)),
                  ("Open corrective actions", c.get("open_actions", 0)),
                  ("Not counted: insufficient evidence", c.get("insufficient", 0))]
    if edition == "internal":
        facts_rows += [("Competitor sightings", c.get("competitor", 0)), ("Demand requests", c.get("demand", 0))]
    r = 3
    for label, value in facts_rows:
        put(ws, r, 1, label).font = bold
        put(ws, r, 2, value)
        r += 1
    r += 1
    put(ws, r, 1, "Counts by issue").font = Font(bold=True, size=12)
    r += 1
    head(ws, r, [("Issue", 0), ("Kind", 0), ("Reports", 0), ("Shops", 0), ("Verified", 0), ("New or recurring", 0)],
         widths=False)
    r += 1
    if not c.get("by_issue"):
        put(ws, r, 1, "No reports counted this month.")
        r += 1
    for b in c.get("by_issue") or []:
        put(ws, r, 1, b.get("label"), text=True)
        put(ws, r, 2, KIND_WORDS.get(b.get("kind"), b.get("kind")))
        put(ws, r, 3, b.get("reports"))
        put(ws, r, 4, b.get("shops"))
        put(ws, r, 5, b.get("verified"))
        put(ws, r, 6, "Recurring" if b.get("recurring") else "New")
        r += 1
    r += 1
    put(ws, r, 1, "Narrative").font = Font(bold=True, size=12)
    r += 1
    for line in [x for x in str(m.get("narrative") or "").split("\n") if x.strip()] or ["(no narrative)"]:
        put(ws, r, 1, line, text=True)
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
        ws.row_dimensions[r].height = min(409, 15 * max(1, -(-len(line) // 105)))   # Excel's tallest row
        r += 1
    r += 1
    put(ws, r, 1, "Open corrective actions").font = Font(bold=True, size=12)
    r += 1
    head(ws, r, [("Action", 0), ("Request", 0), ("Due", 0), ("Days open", 0), ("Status", 0), ("Linked reports", 0)],
         widths=False)
    r += 1
    actions = m.get("actions") or []
    opened = [x for x in actions if x.get("status") in OPEN_ACTION_STATUSES]
    if not opened:
        put(ws, r, 1, "None.")
        r += 1
    for x in opened:
        put(ws, r, 1, x.get("ref"))
        put(ws, r, 2, x.get("request"), text=True)
        put(ws, r, 3, x.get("due_date") or "")
        put(ws, r, 4, x.get("days_open"))
        put(ws, r, 5, x.get("status_words"))
        put(ws, r, 6, ", ".join(f"MI-{i}" for i in x.get("linked_observation_ids") or []) or "none")
        r += 1
    r += 1
    put(ws, r, 1, "How to read this report").font = Font(bold=True, size=12)
    r += 1
    notes = ["IDs: MI = a field report, PH = a photo, CA = a corrective action. Every sentence about a finding "
             "cites the reports it rests on.",
             "Evidence levels: " + " > ".join(EVIDENCE_WORDS[i] for i in sorted(EVIDENCE_WORDS)) + ". "
             "'Verified' = inspected and verified by YQ or better; 'Unverified report' = not verified yet.",
             f"Recurring = the same product and issue in {RECUR_CASES} or more cases, or from {RECUR_SHOPS} or "
             f"more shops, within {RECUR_DAYS} days. Insufficient evidence (no photo, a single report, not "
             "verified) is listed but not counted.",
             "Reports come from the YQ field team. Shop and staff names are not shared; contact details and "
             "amounts are masked. Original photos are available on request."]
    for line in notes:
        put(ws, r, 1, line)
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
        ws.row_dimensions[r].height = 30
        r += 1

    # 2-3. Product issues, Packaging
    for title, kind, cols in (("Product issues", "quality", FINDING_COLUMNS), ("Packaging", "packaging", PACKAGING_COLUMNS)):
        ws = wb.create_sheet(title)
        head(ws, 1, cols)
        ws.freeze_panes = "A2"
        names = [h for h, _w in cols]
        row = 2
        for x in [y for y in finding_rows if y.get("kind") == kind]:
            batch = "; ".join(v for v in (f"batch {x['batch_ref']}" if x.get("batch_ref") else "",
                                          f"pack {x['packaging_version']}" if x.get("packaging_version") else "") if v)
            values = {"ID": x.get("ref"), "Date": x.get("date"), "SKU": x.get("sku_label"),
                      "Product": x.get("product") or "", "Issue": (x.get("issue") or {}).get("label"),
                      "Finding": "Insufficient evidence (not counted)" if x.get("state") == "insufficient" else x.get("finding"),
                      "Description": x.get("description"), "Qty": x.get("qty"), "Batch / packaging": batch,
                      "Packaging version": x.get("packaging_version") or "", "Basis": x.get("basis") or "",
                      "Evidence level": x.get("evidence_label"), "Status": x.get("status"), "Evidence refs": refs(x)}
            for j, name in enumerate(names, start=1):
                if name == "Photo":
                    photo_cell(ws, row, j, x)
                else:
                    put(ws, row, j, values.get(name), text=name in ("SKU", "Product", "Issue", "Description",
                                                                  "Batch / packaging", "Packaging version"))
            row += 1
        if row == 2:
            put(ws, 2, 1, "No reports this month.")

    # 4. Evidence: one row per photo
    ws = wb.create_sheet("Evidence")
    head(ws, 1, EVIDENCE_COLUMNS)
    ws.freeze_panes = "A2"
    row = 2
    for x in finding_rows + market_rows:
        for pid in x.get("photo_ids") or []:
            fp = photos.get(int(pid)) or {"id": pid, "state": "unavailable"}
            put(ws, row, 1, f"PH-{pid}")
            put(ws, row, 2, x.get("ref"))
            put(ws, row, 3, x.get("sku_label") or NO_SKU, text=True)
            put(ws, row, 4, (x.get("issue") or {}).get("label") or KIND_WORDS.get(x.get("kind"), str(x.get("kind")).title()),
                text=True)
            put(ws, row, 5, ROLE_WORDS.get(fp.get("role") or "product", "Product"))
            put(ws, row, 6, fp.get("caption") or "", text=True)
            put(ws, row, 7, fp.get("shown_sha") or "-")
            got = pics.get(int(pid))
            if fp.get("state") == "over_limit":
                put(ws, row, 8, f"not embedded: the workbook holds at most {MAX_PHOTOS} photos")
            elif not got:
                put(ws, row, 8, PHOTO_NOT_AVAILABLE)
            else:
                ws.row_dimensions[row].height = picture(ws, row, 8, got["evidence"], SHOWN_EVIDENCE_PX)
            row += 1
    if row == 2:
        put(ws, 2, 1, "No photos this month.")

    # 5. Corrective actions
    ws = wb.create_sheet("Corrective actions")
    head(ws, 1, ACTION_COLUMNS)
    ws.freeze_panes = "A2"
    row = 2
    for x in actions:
        for j, (v, text) in enumerate(((x.get("ref"), False), (x.get("request"), True), (x.get("owner") or "", True),
                                       (x.get("due_date") or "", False), (x.get("supplier_response") or "", True),
                                       (x.get("resolution_check") or "", True), (x.get("status_words"), False),
                                       (x.get("days_open"), False),
                                       (", ".join(f"MI-{i}" for i in x.get("linked_observation_ids") or []) or "none",
                                        False)), start=1):
            put(ws, row, j, v, text=text)
        row += 1
    if row == 2:
        put(ws, 2, 1, "No corrective actions for this period.")

    # 6-7. Internal edition: Competitor, Demand
    if edition == "internal":
        ws = wb.create_sheet("Competitor")
        head(ws, 1, COMPETITOR_COLUMNS)
        ws.freeze_panes = "A2"
        row = 2
        for x in [y for y in market_rows if y.get("kind") == "competitor"]:
            for j, (v, text) in enumerate(((x.get("ref"), False), (x.get("date"), False), (x.get("product_seen") or "", True),
                                           (x.get("brand") or "", True), (x.get("competitor") or "", True),
                                           (x.get("category") or "", True), (x.get("price_seen") or "", False),
                                           (x.get("sku_label") or "", True), (x.get("note"), True),
                                           (x.get("area") or "", True)), start=1):
                put(ws, row, j, v, text=text)
            photo_cell(ws, row, len(COMPETITOR_COLUMNS), x)
            row += 1
        if row == 2:
            put(ws, 2, 1, "No competitor sightings this month.")
        ws = wb.create_sheet("Demand")
        head(ws, 1, DEMAND_COLUMNS)
        ws.freeze_panes = "A2"
        row = 2
        for x in [y for y in market_rows if y.get("kind") == "demand"]:
            for j, (v, text) in enumerate(((x.get("ref"), False), (x.get("date"), False), (x.get("source"), False),
                                           (x.get("asked_for") or "", True), (x.get("sku_label") or "", True),
                                           (x.get("qty"), False), (x.get("demand_level") or "", False),
                                           (x.get("note"), True), (x.get("area") or "", True)), start=1):
                put(ws, row, j, v, text=text)
            row += 1
        if row == 2:
            put(ws, 2, 1, "No demand requests this month.")

    buf = io.BytesIO()
    wb.save(buf)
    return _stable_zip(buf.getvalue(), stamp)


def _stable_zip(data: bytes, when: datetime) -> bytes:
    """The saved workbook with every zip entry and both document dates set to `when`, so the same
    manifest and photos rebuild byte for byte (the logged sha256 then names the content)."""
    t = when.astimezone(timezone.utc)
    stamp = (max(1980, t.year), t.month, t.day, t.hour, t.minute, t.second)
    iso = t.strftime("%Y-%m-%dT%H:%M:%SZ").encode()
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            body = src.read(info.filename)
            if info.filename == "docProps/core.xml":
                body = re.sub(rb"(<dcterms:(?:created|modified)[^>]*>)[^<]*(</dcterms:)",
                              lambda mm: mm.group(1) + iso + mm.group(2), body)
            elif info.filename == "docProps/app.xml":           # the file names its sender, not its library
                body = re.sub(rb"<Application>[^<]*</Application>", b"<Application>YQ Bahrain</Application>", body)
                body = re.sub(rb"<AppVersion>[^<]*</AppVersion>", b"", body)
            zi = zipfile.ZipInfo(info.filename, date_time=stamp)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = info.external_attr
            dst.writestr(zi, body)
    return out.getvalue()
