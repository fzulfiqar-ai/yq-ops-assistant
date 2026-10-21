"""Stock Moves by contents, for the portal (release 2b).

A transfer in Focus is two vouchers that never point at each other: the Stock Issue Voucher of the store the stock
leaves and the Stock Receive Voucher of the store that books it in. Until 2b, GET /stock/flow joined the two on the
voucher number (v_transfer_flow), which Focus does not keep the same on the two legs. Now the portal pairs them by
what they hold, with the very module /yq-moves runs (scripts/ops/pairing.py), over the same rows: the stock ledger's
Stock Issue and Stock Receive Voucher lines of the accessory items (v_transfer_lines, r9_stock_moves_migration.sql).

  PORTAL_STORES       the store rules: the main store and the damage store by their public names. The generic canon
                      of pairing.StoreRules (case and runs of spaces folded, a trailing ' <n>-<n>' code dropped) covers
                      the other spellings, so no alias is needed and the owner's config is never read here
  pairs_from_rows()   the view's rows -> pairing.Result, shaped as scripts/ops/ledger.py shapes the database's rows for
                      /yq-moves: every number through pairing.dec, the narration through the loader's own
                      mask_personal_ids and reduced to its hash by pairing.narr_key
  current()           the Snapshot of the loaded ledger: read once, paired once, kept until invalidate(), which
                      app/datastamp.py drop_process_caches() calls on every data load. Two requests never pair at once
  flow()              GET /stock/flow: the issues of a window, one row per issue line, newest first, at most ROW_CAP of
                      them shown; the summary, the counts and the partners over every one of them
  recon()             GET /stock/recon: v_salesman_stock_recon's rows, each with what the pairing found for its store

THE READ. The read RPC stops a statement at 8 seconds (scripts/security_migration.sql). So the view is read in pages of
consecutive days, at most PAGE_LINES rows each (about a quarter of the ledger's transfer lines in Oct-2026), cut from
one small query of the rows per day; a page asks only for its own date range and a day is never split. The pages are
read side by side (PAGE_WORKERS). The day the data runs to is the stock ledger's last date over every voucher type
(v_stock_daily_movement), the date /yq-moves pairs on, never the last transfer's: an issue with no receipt is in grace
until the day after that date passes its deadline.

PRIVACY. The view holds the two transfer types only; their narration is masked and hashed as it is shaped, and the
words are never kept in the Snapshot, logged or returned.

IMPORTS. scripts.* is imported inside the functions that use it, as everywhere in app/ (PORTAL_STORES is built on its
first use): importing this module, which app/datastamp.py does on every load it notices, loads neither pandas nor
the pairing.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

log = logging.getLogger(__name__)

MAIN_STORE = "Accessories Warehouse"            # public names (scripts/ops/selftest.py GENERIC_NAMES)
DAMAGE_STORE = "Accessories Damage Warehouse"
ROW_CAP = 800                  # the rows /stock/flow shows at most; the summary, counts and partners cover all of them
MAX_DAYS = 365                 # the longest window /stock/flow answers for
PAGE_LINES = 6000              # the most view rows one read asks for: a day is never split, so one larger day is a page alone
PAGE_WORKERS = 3               # pages read side by side
LOCK_WAIT_S = 60.0             # how long a request waits for the build under way before it gives up (503)
FAIL_HOLD_S = 20.0             # a failed build is told at once, for this long, to the requests that waited for it
ZERO = Decimal(0)

UNAVAILABLE = ("Stock moves cannot be worked out right now: the stock ledger's transfers could not be read. "
               "Try again in a minute.")

# The row statuses: what became of an issue (pairing.OUTCOMES without the receipt's own, ORPHAN).
STATUSES = ("received", "late", "elsewhere", "qty_differs", "in_grace", "not_received", "twins")
SUMMARY_KEYS = STATUSES + ("orphans", "aside")            # summary: {key: {legs, qty, vouchers}}
COUNT_KEYS = ("received", "late", "elsewhere", "qty_differs", "in_grace", "not_received", "twin_items", "twin_slots",
              "orphans", "aside", "issued_vouchers", "issued_units")           # counts: pairing.summarise's, + the issues
RECON_KEYS = ("not_received_qty", "qty_differs_vouchers", "elsewhere_vouchers")  # added to each /stock/recon row

# The rows per day and the day the ledger runs to (any voucher type): the plan the pages are cut from. No column is
# aliased `t` (the read RPC wraps the query as `... t`).
PLAN_SQL = (
    'SELECT "date"::text AS day, COUNT(*)::int AS lines, '
    "(SELECT MAX(move_date) FROM v_stock_daily_movement)::text AS data_date "
    'FROM v_transfer_lines GROUP BY "date" ORDER BY "date"'
)
DATA_DATE_SQL = "SELECT MAX(move_date)::text AS data_date FROM v_stock_daily_movement"   # when no transfer is there
# One page: the view's rows of a date range ($1, $2 inclusive). The order does not matter: legs_from_rows sorts.
TRANSFER_LINES_SQL = (
    'SELECT voucher, vtype, "date"::text AS "date", wh, to_wh, item, qty_in, qty_out, value_in, value_out, narration '
    'FROM v_transfer_lines WHERE "date" BETWEEN $1::date AND $2::date'
)


class Unavailable(RuntimeError):
    """The ledger's transfers could not be read or paired. Its text (UNAVAILABLE) is fit for a page; why is logged."""


# ── the store rules (built on first use: scripts.* is imported lazily) ─────────

def portal_stores():
    """PORTAL_STORES: pairing.StoreRules(main=MAIN_STORE, damage=DAMAGE_STORE), built once."""
    stores = globals().get("PORTAL_STORES")
    if stores is None:
        from scripts.ops import pairing
        stores = globals().setdefault("PORTAL_STORES", pairing.StoreRules(main=MAIN_STORE, damage=DAMAGE_STORE))
    return stores


def __getattr__(name: str):
    """PORTAL_STORES as a module attribute, built when it is first asked for (PEP 562)."""
    if name == "PORTAL_STORES":
        return portal_stores()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ── the rows, shaped as /yq-moves shapes them ─────────────────────────────────

def _day(value) -> date:
    """A view row's date: the 'YYYY-MM-DD' text the read RPC sends, or a date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ValueError("v_transfer_lines: a row has no readable date") from None


def shape(rows, mask=None) -> list[dict]:
    """v_transfer_lines rows -> ledger rows as scripts/ops/ledger.py makes them (item, date, voucher, vtype, wh, to_wh,
    qty_in, qty_out, value_in, value_out, narr): the same function of the same cells as ledger._shape on
    stock_movements' columns, so a leg made here equals the leg /yq-moves makes from the database. Every number through
    pairing.dec; the narration of the two transfer types through `mask` (None: the loader's mask_personal_ids) and
    pairing.narr_key, so only its hash is left. A cell's figure and a narration's hash are worked out once."""
    from scripts.ops import pairing
    if mask is None:
        from scripts import ingest           # pandas: imported only when the ledger is really read
        mask = ingest.mask_personal_ids
    narrated = (pairing.ISSUE, pairing.RECEIVE)
    numbers: dict = {}
    hashes: dict = {}

    def num(x) -> Decimal:
        try:
            key = (type(x), x)
            got = numbers.get(key)
        except TypeError:                     # a cell that cannot key a table: dec() says what it is
            return pairing.dec(x)
        if got is None:
            got = numbers[key] = pairing.dec(x)
        return got

    def narr(text) -> str | None:
        try:
            if text in hashes:
                return hashes[text]
        except TypeError:
            return pairing.narr_key(text, mask)
        got = hashes[text] = pairing.narr_key(text, mask)
        return got

    out: list[dict] = []
    for r in rows:
        vtype = str(r["vtype"] or "")
        to_wh = r["to_wh"]
        out.append({
            "item": str(r["item"] or ""),
            "date": _day(r["date"]),
            "voucher": str(r["voucher"] or ""),
            "vtype": vtype,
            "wh": str(r["wh"] or ""),
            "to_wh": str(to_wh) if to_wh else None,
            "qty_in": num(r["qty_in"]),
            "qty_out": num(r["qty_out"]),
            "value_in": num(r["value_in"]),
            "value_out": num(r["value_out"]),
            "narr": narr(r["narration"]) if vtype in narrated else None,
        })
    return out


def _pair(shaped: list[dict], data_date):
    """Shaped rows -> pairing.Result: legs_from_rows with PORTAL_STORES and no scope (the view holds the accessory
    items only), then pair on data_date (a date or its ISO text; None: the rows' own last date)."""
    from scripts.ops import pairing
    legs = pairing.legs_from_rows(shaped, stores=portal_stores(), scope=None)
    return pairing.pair(legs, data_date=_day(data_date) if data_date is not None else legs.last)


def pairs_from_rows(rows, data_date, *, mask=None):
    """The rows of v_transfer_lines paired by their contents: the pairing.Result /yq-moves works out from the same
    rows. data_date: the day the stock ledger runs to (its last date, any voucher type; a date or its ISO text);
    None takes the rows' own last date."""
    return _pair(shape(rows, mask), data_date)


# ── the snapshot ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Snapshot:
    """The pairing of the loaded ledger and what the pages need beside it. Never holds a narration.

    result     pairing.Result of every transfer the view holds; None when it holds none
    data_date  the day the stock ledger runs to (its last date, any voucher type); None for an empty ledger
    names      store (canon) -> its spelling as the ledger writes it most often (the name shown)
    values     (issue number, item) -> the BHD its lines state
    sizes      number of a voucher set aside -> (items, units) as its rows hold them
    lines      rows read; pages: reads it took; seconds: the read and the pairing, together
    settled    every page held the rows the plan counted for it. False: a load moved under the read, so the snapshot
               is answered once and never kept"""
    result: object = None
    data_date: date | None = None
    names: dict = field(default_factory=dict, repr=False, hash=False)
    values: dict = field(default_factory=dict, repr=False, hash=False)
    sizes: dict = field(default_factory=dict, repr=False, hash=False)
    lines: int = 0
    pages: int = 0
    seconds: float = 0.0
    settled: bool = True


def snapshot(rows, data_date, *, pages: int = 0, seconds: float = 0.0, settled: bool = True, mask=None) -> Snapshot:
    """The Snapshot of these view rows, the ledger running to data_date (None: the rows' last date)."""
    from scripts.ops import pairing
    began = time.monotonic()
    shaped = shape(rows, mask)
    if data_date is not None:
        data_date = _day(data_date)
    elif shaped:
        data_date = max(r["date"] for r in shaped)
    result = _pair(shaped, data_date) if shaped else None
    stores = portal_stores()
    canon: dict[str, str] = {}
    spelt: dict[str, Counter] = {}
    values: dict[tuple, Decimal] = {}
    aside = {a.voucher for a in result.aside} if result is not None else set()
    items: dict[str, set] = {}
    units: dict[str, Decimal] = {}
    for r in shaped:
        for name in (r["wh"], r["to_wh"]):
            if name:
                c = canon.get(name)
                if c is None:
                    c = canon[name] = stores.canon(name)
                spelt.setdefault(c, Counter())[name] += 1
        if r["vtype"] == pairing.ISSUE:
            key = (r["voucher"], r["item"])
            values[key] = values.get(key, ZERO) + r["value_in"] + r["value_out"]
        if r["voucher"] in aside:
            items.setdefault(r["voucher"], set()).add(r["item"])
            units[r["voucher"]] = units.get(r["voucher"], ZERO) + r["qty_in"] + r["qty_out"]
    names = {c: min(seen, key=lambda s, seen=seen: (-seen[s], s)) for c, seen in spelt.items()}
    sizes = {v: (len(items[v]), units[v]) for v in items}
    return Snapshot(result=result, data_date=data_date, names=names, values=values, sizes=sizes, lines=len(shaped),
                    pages=pages, seconds=seconds + time.monotonic() - began, settled=settled)


# ── the read: date-range pages, each well inside the read RPC's 8 seconds ─────

def plan_pages(days, cap: int | None = None) -> list[tuple[date, date, int]]:
    """[(day, rows)] in date order -> pages [(first day, last day, rows)]: consecutive days packed while the page
    holds at most `cap` rows (None: PAGE_LINES). A day is never split: one day of more than `cap` rows is a page of
    its own."""
    cap = PAGE_LINES if cap is None else cap
    pages: list[tuple[date, date, int]] = []
    lo = hi = None
    held = 0
    for day, lines in days:
        if lo is not None and held + lines > cap:
            pages.append((lo, hi, held))
            lo, held = None, 0
        if lo is None:
            lo = day
        hi = day
        held += lines
    if lo is not None:
        pages.append((lo, hi, held))
    return pages


def read_lines(query=None, query_params=None) -> tuple[list[dict], date | None, int, bool]:
    """(rows, data date, pages, settled): every row of v_transfer_lines and the day the stock ledger runs to, through
    the read RPC (app.db_read: query = exec_sql, query_params = exec_sql_params). One small query of the rows per day
    first (PLAN_SQL), then the pages (TRANSFER_LINES_SQL, $1..$2 a page's first and last day), side by side.
    settled: every page held the rows the plan counted for it."""
    if query is None or query_params is None:
        from app import db_read
        query = query or db_read.exec_sql
        query_params = query_params or db_read.exec_sql_params
    plan = query(PLAN_SQL) or []
    if plan:
        data_date = plan[0].get("data_date")
    else:
        data_date = (query(DATA_DATE_SQL) or [{}])[0].get("data_date")
    data_date = _day(data_date) if data_date else None
    pages = plan_pages([(_day(p["day"]), int(p["lines"])) for p in plan])

    def one(page: tuple) -> list[dict]:
        first, last, _held = page
        return query_params(TRANSFER_LINES_SQL, [first.isoformat(), last.isoformat()]) or []

    if len(pages) <= 1:
        parts = [one(p) for p in pages]
    else:
        with ThreadPoolExecutor(max_workers=min(PAGE_WORKERS, len(pages)), thread_name_prefix="transfers") as ex:
            parts = list(ex.map(one, pages))
    settled = all(len(part) == page[2] for part, page in zip(parts, pages))
    return [r for part in parts for r in part], data_date, len(pages), settled


# ── the cache: one snapshot per data load ─────────────────────────────────────

_lock = threading.Lock()     # held while a snapshot is built: a second request waits for it and takes its result
_state: dict = {"snap": None, "gen": 0, "failed": None}     # failed: (monotonic time, gen) of the last failed build


def current(read=None) -> Snapshot:
    """The Snapshot of the loaded ledger: read and paired on the first request after a data load, then kept until
    invalidate(). Looks for a load made outside this process first (app/datastamp.py), like every cached reader.
    Two requests never build it at once: one builds, the other waits (LOCK_WAIT_S at most) and takes the result.
    A build that began before an invalidate(), or whose pages did not hold what the plan counted, is answered once
    and never kept. Unavailable when the read or the pairing fails; for FAIL_HOLD_S after that (until the next load),
    every request, the ones that waited for the failed build first, is told so at once instead of reading again.
    read: a stand-in for read_lines."""
    from app import datastamp
    datastamp.sync()
    snap = _state["snap"]
    if snap is not None:
        return snap
    if not _lock.acquire(timeout=LOCK_WAIT_S):
        log.warning("stock moves: still being worked out after %.0f s; this request gives up", LOCK_WAIT_S)
        raise Unavailable(UNAVAILABLE)
    try:
        snap = _state["snap"]
        if snap is not None:
            return snap
        gen = _state["gen"]
        failed = _state["failed"]
        if failed is not None and failed[1] == gen and time.monotonic() - failed[0] < FAIL_HOLD_S:
            raise Unavailable(UNAVAILABLE)
        began = time.monotonic()
        try:
            rows, data_date, pages, settled = (read or read_lines)()
            snap = snapshot(rows, data_date, pages=pages, seconds=time.monotonic() - began, settled=settled)
        except Exception as exc:  # noqa: BLE001 -- told to the page as Unavailable, the kind and words logged
            _state["failed"] = (time.monotonic(), gen)
            log.warning("stock moves: the ledger's transfers could not be read or paired (%s: %s)",
                        type(exc).__name__, str(exc)[:200])
            raise Unavailable(UNAVAILABLE) from exc
        kept = gen == _state["gen"] and snap.settled
        if kept:
            _state["snap"] = snap
            _state["failed"] = None
        result = snap.result
        log.info("stock moves: %d transfer lines in %d page(s), %d issues and %d receipts, read and paired in %.1f s, "
                 "ledger to %s%s", snap.lines, snap.pages, len(result.legs.issues) if result is not None else 0,
                 len(result.legs.receipts) if result is not None else 0, snap.seconds, snap.data_date,
                 "" if kept else "; not kept (a load came in while it was read)")
        return snap
    finally:
        _lock.release()


def invalidate() -> None:
    """Drop the snapshot: the next request reads and pairs the ledger again. app/datastamp.py drop_process_caches()
    calls it on every data load this process notices (and so does app.ai.flush_cache, after an upload through the
    API). A build under way is answered once and never kept."""
    _state["gen"] += 1
    _state["snap"] = None
    _state["failed"] = None


# ── GET /stock/flow ───────────────────────────────────────────────────────────

def _num(x: Decimal) -> int | float:
    """A Decimal as the page reads it: a whole number as an int, else a float (pairing.dec keeps three decimals)."""
    return int(x) if x == x.to_integral_value() else float(x)


def _iso(day: date | None) -> str | None:
    return day.isoformat() if day is not None else None


def _block(vouchers: int = 0, lines: int = 0, units: Decimal = ZERO) -> dict:
    """One summary entry: legs = issue lines, qty = units, vouchers = vouchers (an orphan's lines and units as received;
    a voucher set aside, as its rows hold them)."""
    return {"legs": lines, "qty": _num(units), "vouchers": vouchers}


def _basis(got, pairing) -> str | None:
    """What a row's pairing rests on, its words joined by '+': 'contents' (the same items and units: steps A and B) or
    'overlap' (step C: at least half the items shared), then those of pairing.BASES that held (narration, number,
    date), then 'twins' when the issue is one of identical issues to one store that have fewer receipts than they are
    (which receipt is whose is not known). An issue with no receipt: 'twins' for such a set, else None."""
    if isinstance(got, pairing.Pair):
        words = ["overlap" if got.step == "C" else "contents", *got.basis]
        if got.ambiguous:
            words.append("twins")
        return "+".join(words)
    return "twins" if got.twins else None


def flow(snap: Snapshot, *, days: int = 30, direction: str = "out", warehouse: str | None = MAIN_STORE,
         cap: int = ROW_CAP) -> dict:
    """GET /stock/flow's payload for one store and the `days` days ending on the ledger's last date, both included.

    direction 'out': the issues the store sent, partners by the store that actually received them (the one named when
    none did); 'in': the issues sent to it, partners by the store that sent them. The store is compared through
    PORTAL_STORES.canon.

        {warehouse, direction, days, as_of, basis: "contents",
         rows:     one per issue line, newest first, at most `cap` of them: {issued_on, voucher_no, item_name,
                   from_warehouse, to_warehouse, issued_qty, issued_value_bhd, received_qty, received_by, received_on,
                   receipt_no, status (STATUSES), basis (_basis)}; received_*: the receipt paired with the issue. An
                   issue booked in under two receipts (pairing step S: Pair.also) names both, "STRV a + STRV b", and
                   its received_qty is what the two booked in together (the same store, the same day)
         count:    the issue lines of the window; shown: the rows given (count capped)
         summary:  {status: {legs, qty, vouchers}} for STATUSES, orphans and aside, over EVERY issue of the window
                   (pairing.summarise; aside as the vouchers' rows hold them)
         counts:   pairing.summarise's counts (twin_items, twin_slots included) + issued_vouchers, issued_units
         partners: [{name, qty}]: the 8 largest, by units issued}"""
    from scripts.ops import pairing
    days = max(1, min(int(days), MAX_DAYS))
    direction = "in" if direction == "in" else "out"
    stores = portal_stores()
    store = str(warehouse or "").strip()
    if not stores.canon(store):
        store = MAIN_STORE
    payload = {"warehouse": store, "direction": direction, "days": days, "as_of": _iso(snap.data_date),
               "basis": "contents", "rows": [], "count": 0, "shown": 0,
               "summary": {key: _block() for key in SUMMARY_KEYS}, "counts": dict.fromkeys(COUNT_KEYS, 0),
               "partners": []}
    result = snap.result
    if result is None or snap.data_date is None:
        return payload
    last = snap.data_date
    first = last - timedelta(days=days - 1)
    told = pairing.summarise(result, first=first, last=last, store=store, direction=direction)
    want = told["store"]
    names = snap.names

    def shown_as(canon_name):
        return names.get(canon_name, canon_name) if canon_name else canon_name

    picked = [i for i in result.legs.issues
              if first <= i.day <= last and (i.wh if direction == "out" else i.to) == want]
    rows: list[dict] = []
    count = 0
    partners: dict[str, Decimal] = {}
    for i in reversed(picked):                      # the legs are in order of day, then number: newest first
        got = result.by_issue[i.no]
        receipts = (got.receipt, *got.also) if isinstance(got, pairing.Pair) else ()   # step S: two receipts
        receipt = receipts[0] if receipts else None
        count += len(i.lines)
        partner = ((receipt.wh if receipt is not None else i.to) if direction == "out" else i.wh)
        partners[partner] = partners.get(partner, ZERO) + i.units
        if len(rows) >= cap:                        # counted above; only the rows shown are written out
            continue
        booked: dict[str, Decimal] = {}
        for r in receipts:                          # what the receipts booked in, together
            for item, units in r.lines:
                booked[item] = booked.get(item, ZERO) + units
        receipt_no = " + ".join(r.no for r in receipts) if receipts else None
        received_on = max(r.day for r in receipts).isoformat() if receipts else None
        basis = _basis(got, pairing)
        for item, units in i.lines[:cap - len(rows)]:   # by item
            rows.append({
                "issued_on": i.day.isoformat(),
                "voucher_no": i.no,
                "item_name": item,
                "from_warehouse": shown_as(i.wh),
                "to_warehouse": shown_as(i.to),
                "issued_qty": _num(units),
                "issued_value_bhd": _num(snap.values.get((i.no, item), ZERO)),
                "received_qty": _num(booked.get(item, ZERO)),
                "received_by": shown_as(receipt.wh) if receipt is not None else None,
                "received_on": received_on,
                "receipt_no": receipt_no,
                "status": got.outcome,
                "basis": basis,
            })

    outcomes = told["outcomes"]
    summary = {status: _block(outcomes[status]["vouchers"], outcomes[status]["lines"], outcomes[status]["units"])
               for status in STATUSES}
    orphan = outcomes[pairing.ORPHAN]
    summary["orphans"] = _block(orphan["vouchers"], orphan["lines"], orphan["units"])
    aside = [0, 0, ZERO]
    for a in result.aside:                          # summarise's own reading of a voucher set aside
        if a.day is None or not first <= a.day <= last:
            continue
        if direction == "out":
            mine = a.kind == "issue" and a.wh == want
        else:
            mine = (a.to == want) if a.kind == "issue" else (a.wh == want)
        if mine:
            n_items, n_units = snap.sizes.get(a.voucher, (0, ZERO))
            aside[0] += 1
            aside[1] += n_items
            aside[2] += n_units
    summary["aside"] = _block(*aside)
    counts = dict(told["counts"])
    counts["issued_vouchers"] = told["issues"]["vouchers"]
    counts["issued_units"] = _num(told["issues"]["units"])
    top = sorted(partners.items(), key=lambda kv: (-kv[1], shown_as(kv[0]) or ""))[:8]
    payload.update(rows=rows, count=count, shown=len(rows), summary=summary, counts=counts,
                   partners=[{"name": shown_as(k) or "—", "qty": round(float(v), 1)} for k, v in top])
    return payload


# ── GET /stock/recon ──────────────────────────────────────────────────────────

def store_figures(snap: Snapshot) -> dict[str, dict]:
    """{store (canon): {not_received_qty, qty_differs_vouchers, elsewhere_vouchers}} over every issue the ledger holds,
    each counted for the store the issue names: the units sent there with no receipt after their deadline (for a set
    of identical issues, its missing ones; and an issue sent to its own store that nothing booked back in there, which
    the pairing sets aside, past its deadline: /yq-moves' own reading), the vouchers it booked in with other
    quantities, and the vouchers another store booked in instead. Units in grace are not counted."""
    from scripts.ops import pairing
    figures: dict[str, dict] = {}
    result = snap.result
    if result is None:
        return figures

    def of(store: str) -> dict:
        return figures.setdefault(store, {"not_received_qty": ZERO, "qty_differs_vouchers": 0, "elsewhere_vouchers": 0})

    for o in result.open:
        if o.outcome in (pairing.NOT_RECEIVED, pairing.TWINS):
            of(o.store)["not_received_qty"] += o.missing * o.issues[0].units   # the issues of a set hold the same
    for p in result.pairs:
        if p.outcome == pairing.QTY_DIFFERS:
            of(p.issue.to)["qty_differs_vouchers"] += 1
        elif p.outcome == pairing.ELSEWHERE:
            of(p.issue.to)["elsewhere_vouchers"] += 1
    stores = portal_stores()
    for a in result.aside:                          # sent to its own store, and nothing booked it back in there
        if a.kind != "issue" or a.reason != pairing.OWN_STORE or a.partner or a.day is None or not a.lines:
            continue
        if not pairing.within_grace(a.day, stores.kind(a.wh), result.data_date, result.rules):
            of(a.wh)["not_received_qty"] += sum((units for _item, units in a.lines), ZERO)
    return figures


def recon(rows: list[dict], snap: Snapshot | None) -> dict:
    """GET /stock/recon's payload: {vans: the view's rows in its order, each with RECON_KEYS added from store_figures
    (matched on warehouse_name through PORTAL_STORES.canon; a store the pairing found nothing for has 0), count,
    as_of}. snap None (the pairing could not be worked out): the rows as they are, RECON_KEYS None, as_of None."""
    figures = store_figures(snap) if snap is not None else None
    stores = portal_stores()
    out: list[dict] = []
    for r in rows:
        row = dict(r)
        if figures is None:
            row.update(dict.fromkeys(RECON_KEYS))
        else:
            got = figures.get(stores.canon(row.get("warehouse_name") or row.get("salesman")))
            row["not_received_qty"] = _num(got["not_received_qty"]) if got else 0
            row["qty_differs_vouchers"] = got["qty_differs_vouchers"] if got else 0
            row["elsewhere_vouchers"] = got["elsewhere_vouchers"] if got else 0
        out.append(row)
    return {"vans": out, "count": len(out), "as_of": _iso(snap.data_date) if snap is not None else None}
