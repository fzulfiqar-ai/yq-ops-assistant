"""Phase 0 load: cleaned CSVs in data/clean/ -> Supabase (idempotent upserts).

Order:
  1. products   (derived from selling_prices: distinct sku_code + item_name + unit + status)
  2. customers  (derived from orders.customer_name + order_lines.customer_account)
  3. orders, order_lines, stock_movements, ledger_entries, product_profitability, selling_prices

Upserts use each table's natural key (on_conflict) so re-running never double-counts
(data rules 2/6 friendly). Requires the tables created by migrate_supabase.py.

Usage:  python scripts/load_supabase.py [--staged <folder ingest read this run>]
        (scripts/refresh.py passes --staged; without it Focus price books are loaded but never void
         older rows -- see "Focus price books are snapshots" below. The folder may carry two marker
         files: REPLACE_OK and PARTIAL_OK -- see "Sales are replaced by the file's own date span".)
"""
from __future__ import annotations

import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CLEAN = ROOT / "data" / "clean"
CHUNK = 500

# ── Focus price books are snapshots (R1, 24-Sep-2026) ────────────────────────
# A Focus price-book export is the WHOLE book at export time. Rows an earlier export of the same
# book carried that the newest one no longer does are not history: they are stale, or -- as on
# 14-Sep-2026 -- mis-parsed (165 day-first rows Focus never held, which put fake "Was" pills on the
# market and queued price changes for 5-Oct and 1-Nov). After each Focus book loads, such rows are
# VOIDED, never deleted: voided_at / void_reason (selling_prices_void_migration.sql), and the five
# views that read selling_prices skip them (v_price_list_by_book, v_price_list, v_price_change,
# v_price_history, v_product_margin). Workbook imports (scripts/import_workbook_prices.py, source
# 'YQ_MRN%') are a different source of truth and are never voided by this rule.
#
# Which export is newest? The one just uploaded, BY DEFINITION. The browser's download counter in
# the file name ('(40)', '_36_') is not monotonic (another PC, a cleared Downloads folder), so it is
# never consulted. The void runs only for a book whose file is in the folder ingest read THIS run
# (`--staged <folder>`, passed by scripts/refresh.py); a leftover data/clean/selling_prices.csv
# from an earlier run can therefore never void anything.
#
# Two safety rails: (1) every voided key that a later file carries again is UN-voided right after
# the upsert (the upsert rewrote the row with the file's values, so it is that file's price now);
# (2) the mass-void guard -- a filtered, truncated or mis-parsed export would look like "most of the
# book vanished", so when the stale rows exceed SNAPSHOT_MAX_STALE_SHARE of the book's live Focus
# rows, or the file names fewer than SNAPSHOT_MIN_SKU_COVER of its live SKUs, nothing is voided, the
# log shouts, an audit_log row records the skip and scripts/verify_numbers.py FAILS the refresh
# ("live rows not in file (older exports)" > 0). Every void batch writes one audit_log row too.
FOCUS_BOOKS = {"MA_base": "masellingpricebook", "modern_trade": "moderntradesellerbook"}
SNAPSHOT_MAX_STALE_SHARE = 0.10
SNAPSHOT_MIN_SKU_COVER = 0.90
_SP_KEY_COLS = "id,sku_code,price_book,customer_code,warehouse_name,start_date,source_file"

# ── Sales are replaced by the file's own date span (R2, 24-Sep-2026) ─────────
# orders / order_lines used to be upsert-only, so an invoice Focus deleted or re-lined after an
# export lived on in the database (21-Sep-2026: 2 invoices in the DB that Focus no longer had).
# A Focus sales export is the whole truth for its own date span, exactly like the Stock_ledger,
# so the span is cleared and reloaded -- never a row outside it. Before anything is deleted the
# loader prints every invoice the DB holds inside the span that the file does not carry ("would
# disappear"), and a guard refuses the replace when that list is more than SPAN_MAX_DISAPPEAR_SHARE
# of the span's invoices (and at least SPAN_MIN_DISAPPEAR): a filtered or partial export must not
# wipe a month. The load then falls back to the old upsert-only path, writes an audit_log row and
# scripts/verify_numbers.py FAILS on the row counts, which is the signal to look. A REPLACE_OK
# marker file in the upload folder overrides the guard on purpose.
SPAN_MAX_DISAPPEAR_SHARE = 0.10
SPAN_MIN_DISAPPEAR = 5
REPLACE_OK_MARKER = "REPLACE_OK"
# A Stock_balance snapshot is replaced per (as_of_date, warehouse_name): a one-warehouse export
# never erases the other warehouses of the same day. When the warehouse set of a NEW snapshot is
# smaller than the previous snapshot's, the loader warns, writes an audit_log row and verify FAILS
# ("Stock snapshot warehouses vs previous") -- unless the folder carries a PARTIAL_OK marker file,
# which says the narrower export is intended (D5: the 14-Sep-2026 drop shrank the book from 25
# warehouses to 1 and admin stock value fell 143,105 -> 44,996 without a word).
PARTIAL_OK_MARKER = "PARTIAL_OK"


def marker_present(folder, name: str) -> bool:
    """True when the upload folder carries the marker file `name` (any extension, any case)."""
    try:
        return any(p.name.split(".")[0].upper() == name.upper() for p in Path(folder).iterdir() if p.is_file())
    except (OSError, TypeError):
        return False


def date_span(values) -> tuple[str, str] | None:
    """Pure: (min, max) ISO dates of a column (blanks and NaN ignored), or None when empty."""
    out: list[str] = []
    for v in values or []:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            continue
        s = str(v).strip()[:10]
        if s and s.lower() not in ("nan", "nat", "none"):
            out.append(s)
    return (min(out), max(out)) if out else None


def month_windows(dmin: str, dmax: str) -> list[tuple[str, str]]:
    """Pure: [dmin, dmax] cut into calendar-month windows with real month ends (never '-31')."""
    import calendar
    wins: list[tuple[str, str]] = []
    y, m = int(dmin[:4]), int(dmin[5:7])
    while f"{y:04d}-{m:02d}" <= dmax[:7]:
        lo = max(dmin, f"{y:04d}-{m:02d}-01")
        hi = min(dmax, f"{y:04d}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}")
        wins.append((lo, hi))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return wins


def disappearing_invoices(db_rows: list[dict], file_invoices: set[str]) -> list[dict]:
    """Pure: the DB rows (inside the file's span) whose invoice_no the file does not carry -- the
    invoices a span replace would remove. One entry per invoice, in DB order."""
    seen: set[str] = set()
    out: list[dict] = []
    for r in db_rows:
        inv = str(r.get("invoice_no") or "").strip()
        if not inv or inv in file_invoices or inv in seen:
            continue
        seen.add(inv)
        out.append(r)
    return out


def span_guard(disappearing: list[dict], db_invoices_in_span: int, replace_ok: bool = False) -> str | None:
    """Pure: None when the span replace may proceed, else why it must not."""
    n = len(disappearing)
    if replace_ok or n < SPAN_MIN_DISAPPEAR or db_invoices_in_span <= 0:
        return None
    if n > SPAN_MAX_DISAPPEAR_SHARE * db_invoices_in_span:
        return (f"{n} of the {db_invoices_in_span} invoices the database holds in the file's span are not in "
                f"the file (over {SPAN_MAX_DISAPPEAR_SHARE:.0%}): a filtered or partial export? Add a "
                f"{REPLACE_OK_MARKER} file to the upload folder to replace on purpose")
    return None


def warehouse_set_guard(previous: set[str], new: set[str], partial_ok: bool = False) -> str | None:
    """Pure: None when the new snapshot covers at least the previous snapshot's warehouses (or the
    narrower export was acknowledged with PARTIAL_OK), else the warning text."""
    if partial_ok or not previous:
        return None
    missing = sorted(previous - new)
    if missing and len(new) < len(previous):
        shown = ", ".join(missing[:6]) + (" ..." if len(missing) > 6 else "")
        return (f"the new snapshot covers {len(new)} warehouse(s), the previous one {len(previous)}; "
                f"missing: {shown}. Export all warehouses, or add a {PARTIAL_OK_MARKER} file to the folder "
                f"if the narrower export is intended (verify FAILS until then)")
    return None


def focus_book_kind(source_file) -> str | None:
    """The price_book a Focus export file name belongs to ('MA_base' / 'modern_trade'), or None for
    anything else (workbook imports, blanks). The portal stages 'MASellingPriceBook (40).xlsx' as
    'MASellingPriceBook _40_.xlsx' (app/main.py sanitises the name): both spell the same book."""
    n = Path(str(source_file or "")).name.strip().lower()
    for book, stem in FOCUS_BOOKS.items():
        if n.startswith(stem):
            return book
    return None


def pricebook_key(r: dict) -> tuple:
    """The natural key the upsert uses (pricebook_key_migration.sql), normalised so a CSV row and a
    PostgREST row compare equal: blanks, None and NaN become '', dates keep their first 10 chars."""
    def _s(v) -> str:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return ""
        return str(v).strip()
    return (_s(r.get("sku_code")), _s(r.get("price_book")), _s(r.get("customer_code")),
            _s(r.get("warehouse_name")), _s(r.get("start_date"))[:10])


def is_dayfirst_twin(phantom: dict, twin: dict) -> bool:
    """The rule selling_prices_void_migration.sql applies: `twin` is the month-first reading of the
    day-first `phantom` -- same SKU / book / customer / warehouse / rate, and twin.start_date is
    phantom.start_date with day and month swapped (so day <= 12 and day != month)."""
    def _s(v) -> str:
        return "" if v is None or (isinstance(v, float) and math.isnan(v)) else str(v).strip()
    if any(_s(phantom.get(k)) != _s(twin.get(k))
           for k in ("sku_code", "price_book", "customer_code", "warehouse_name")):
        return False
    try:
        if abs(float(phantom.get("rate_bhd")) - float(twin.get("rate_bhd"))) > 1e-9:
            return False
        p = datetime.strptime(_s(phantom.get("start_date"))[:10], "%Y-%m-%d").date()
        t = datetime.strptime(_s(twin.get("start_date"))[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return False
    if p.day > 12 or p.day == p.month:
        return False
    return (t.year, t.month, t.day) == (p.year, p.day, p.month)


def _fname(v) -> str:
    return Path(str(v or "")).name.strip()


def staged_files(folder) -> set[str]:
    """The workbook names in the folder ingest read this run (the portal's data/_upload staging
    folder, or the folder given to scripts.refresh). A missing folder stages nothing."""
    try:
        return {p.name for p in Path(folder).glob("*.xls*")}
    except (OSError, TypeError):
        return set()


def loaded_focus_books(loaded: list[dict], staged: set[str] | None = None) -> dict[str, dict]:
    """Pure: the Focus books in a load -> {price_book: {srcs, keys, skus}}.
    staged -- names of the files ingest read THIS run; a book whose file is not among them is left
    out, so a leftover CSV never drives the snapshot rule. None = no staging information."""
    books: dict[str, dict] = {}
    for r in loaded:
        book = r.get("price_book")
        src = _fname(r.get("source_file"))
        if not book or focus_book_kind(src) != book:
            continue
        if staged is not None and src not in staged:
            continue
        b = books.setdefault(book, {"srcs": set(), "keys": set(), "skus": set()})
        b["srcs"].add(src)
        b["keys"].add(pricebook_key(r))
        sku = str(r.get("sku_code") or "").strip()
        if sku:
            b["skus"].add(sku)
    return books


def stale_pricebook_rows(loaded: list[dict], existing: list[dict], staged: set[str] | None = None) -> list[dict]:
    """Pure: which live rows the upload just made supersedes.

    loaded   -- the rows just upserted (any price_book; only Focus-book source files count)
    existing -- live (voided_at is null) rows of the same price_book(s): id, price_book,
                source_file and the key columns
    staged   -- file names ingest read this run (see loaded_focus_books); None = every Focus book
    The loaded file is the newest export of its book by definition. Returns the existing rows of
    the same price_book that came from a DIFFERENT Focus export of that book and whose key the
    loaded file does not carry. Rows of other sources (workbook imports), rows the file still
    carries and rows of other books are left alone."""
    books = loaded_focus_books(loaded, staged)
    out: list[dict] = []
    for r in existing:
        b = books.get(r.get("price_book") or "")
        if not b:
            continue
        rsrc = _fname(r.get("source_file"))
        if rsrc in b["srcs"] or focus_book_kind(rsrc) != r.get("price_book"):
            continue
        if pricebook_key(r) in b["keys"]:
            continue
        out.append(r)
    return out


def revived_pricebook_rows(loaded: list[dict], voided: list[dict], staged: set[str] | None = None) -> list[dict]:
    """Pure: voided rows whose key the file just loaded carries. The upsert has already rewritten
    each of them with the file's values (rate, source_file, ...), so they are that file's live
    prices and must come back -- a real future price that lands on a voided key (say C01 dated
    2026-11-01) would otherwise stay invisible and the SKU would drop off the market."""
    books = loaded_focus_books(loaded, staged)
    out: list[dict] = []
    for r in voided:
        b = books.get(r.get("price_book") or "")
        if b and pricebook_key(r) in b["keys"]:
            out.append(r)
    return out


def snapshot_guard(stale: list[dict], live_focus: list[dict], file_skus: set[str]) -> str | None:
    """Pure: None when the void may proceed, else the reason it must not. `live_focus` are the
    book's live rows from Focus exports (the population the void draws from)."""
    if not stale:
        return None
    n_live = len(live_focus)
    if n_live and len(stale) > SNAPSHOT_MAX_STALE_SHARE * n_live:
        return (f"{len(stale)} stale rows are more than {SNAPSHOT_MAX_STALE_SHARE:.0%} of the book's "
                f"{n_live} live rows")
    live_skus = {str(r.get("sku_code") or "").strip() for r in live_focus} - {""}
    if live_skus and len(live_skus & file_skus) < SNAPSHOT_MIN_SKU_COVER * len(live_skus):
        return (f"the file names {len(live_skus & file_skus)} of the book's {len(live_skus)} live SKUs "
                f"(under {SNAPSHOT_MIN_SKU_COVER:.0%}): a filtered or truncated export?")
    return None


def void_columns_present(client) -> bool:
    """True once selling_prices_void_migration.sql has added voided_at / void_reason."""
    try:
        client.table("selling_prices").select("voided_at").limit(1).execute()
        return True
    except Exception:  # noqa: BLE001
        return False


def _fetch_book_rows(client, book: str, voided: bool) -> list[dict]:
    """Every row of `book` that is voided (True) or live (False): id, source_file and the key
    columns. Raises when the void columns are missing."""
    out: list[dict] = []
    off = 0
    while True:
        q = client.table("selling_prices").select(_SP_KEY_COLS).eq("price_book", book)
        q = q.not_.is_("voided_at", "null") if voided else q.is_("voided_at", "null")
        page = q.order("id").range(off, off + 999).execute().data or []
        out += page
        if len(page) < 1000:
            break
        off += 1000
    return out


def _set_void(client, ids: list[int], voided_at: str | None, reason: str | None) -> None:
    for i in range(0, len(ids), CHUNK):
        (client.table("selling_prices").update({"voided_at": voided_at, "void_reason": reason})
         .in_("id", ids[i:i + CHUNK]).execute())


def _audit(client, event: str, question: str, detail: dict) -> None:
    try:
        client.table("audit_log").insert({"user_email": "load_supabase", "event": event,
                                          "question": question, "detail": detail}).execute()
    except Exception as e:  # noqa: BLE001
        print(f"  (audit_log not written: {str(e)[:120]})")


def _apply_pricebook_snapshot(client, loaded: list[dict], staged: set[str] | None) -> dict:
    """After the selling_prices upsert: un-void every row the file carries again, then void the
    rows an older Focus export of the same book left behind (stale_pricebook_rows), guarded by
    snapshot_guard. Returns {"revived": n, "voided": n, "skipped": {book: reason}}. Prints a skip
    reason and changes nothing while the void columns are missing (the migration has not run) --
    the load itself is unaffected."""
    res: dict = {"revived": 0, "voided": 0, "skipped": {}}
    all_books = loaded_focus_books(loaded)            # every Focus book in the load: un-void applies to all
    if not all_books:
        return res
    for book, b in sorted(all_books.items()):
        file = ", ".join(sorted(b["srcs"]))
        try:
            voided = _fetch_book_rows(client, book, voided=True)
        except Exception as e:  # noqa: BLE001
            msg = str(e)[:160]
            hint = " -- apply scripts/selling_prices_void_migration.sql" if "voided_at" in msg else ""
            print(f"  (selling_prices snapshot rule skipped for {book}: {msg}{hint})")
            res["skipped"][book] = msg
            continue
        revive = [r for r in revived_pricebook_rows(loaded, voided) if r.get("price_book") == book]
        if revive:
            _set_void(client, [int(r["id"]) for r in revive], None, None)
            res["revived"] += len(revive)
            print(f"  revived  selling_prices {len(revive):7}  ({book}: voided keys carried again by {file})")
        # the void: only for a book whose file ingest read THIS run
        if staged is None:
            print(f"  (selling_prices: {book} void skipped -- no --staged folder; run through scripts.refresh)")
            res["skipped"][book] = "no staged folder"
            continue
        if not (b["srcs"] & staged):
            print(f"  (selling_prices: {book} void skipped -- {file} is not in this run's upload)")
            res["skipped"][book] = "file not staged this run"
            continue
        live = _fetch_book_rows(client, book, voided=False)
        stale = stale_pricebook_rows(loaded, live, staged)
        live_focus = [r for r in live if focus_book_kind(r.get("source_file")) == book]
        reason = snapshot_guard(stale, live_focus, b["skus"])
        if reason:
            print(f"  !! WARNING: {book} snapshot void SKIPPED -- {reason}. Nothing was voided. "
                  f"Check {file} is a complete, unfiltered export (verify will FAIL until it is).")
            _audit(client, "selling_prices.void_skipped", f"{book}: {file}",
                   {"book": book, "file": file, "reason": reason, "stale_rows": len(stale),
                    "live_rows": len(live_focus), "file_skus": len(b["skus"]),
                    "skus": sorted({str(r.get("sku_code")) for r in stale})[:200]})
            res["skipped"][book] = reason
            continue
        if not stale:
            print(f"  (selling_prices: {book} -- no superseded Focus book rows to void)")
            continue
        now = datetime.now(timezone.utc).isoformat()
        why = f"superseded by {file} (Focus book snapshot: key not in the newest export)"
        _set_void(client, [int(r["id"]) for r in stale], now, why)
        skus = sorted({str(r.get("sku_code")) for r in stale})
        _audit(client, "selling_prices.void", f"{book}: {file}",
               {"book": book, "file": file, "count": len(stale), "skus": skus,
                "sources": sorted({_fname(r.get("source_file")) for r in stale})})
        res["voided"] += len(stale)
        print(f"  voided   selling_prices {len(stale):7}  ({book}: rows of older Focus books not in {file}; "
              f"{len(skus)} SKUs; audit_log row written)")
    return res


def _client():
    # imported here so `ingest.py` users without Supabase config aren't forced to set it up
    from app.database import get_client

    return get_client()


def _records(df: pd.DataFrame) -> list[dict]:
    df = df.where(pd.notnull(df), None)
    out = []
    for rec in df.to_dict(orient="records"):
        clean = {}
        for k, v in rec.items():
            if isinstance(v, float) and math.isnan(v):
                v = None
            clean[k] = v
        out.append(clean)
    return out


def _upsert(client, table: str, records: list[dict], on_conflict: str) -> int:
    n = 0
    for i in range(0, len(records), CHUNK):
        batch = records[i : i + CHUNK]
        client.table(table).upsert(batch, on_conflict=on_conflict).execute()
        n += len(batch)
    print(f"  upserted {table:24} {n:7}")
    return n


def _read(name: str) -> pd.DataFrame | None:
    p = CLEAN / f"{name}.csv"
    if not p.exists():
        print(f"  (skip {name}: {p.name} not found)")
        return None
    return pd.read_csv(p, dtype=object)


def _fetch_span(client, table: str, cols: str, datecol: str, lo: str, hi: str) -> list[dict]:
    """Every row of `table` with `datecol` in [lo, hi], paged in id order (stable pages)."""
    out: list[dict] = []
    off = 0
    while True:
        page = (client.table(table).select(cols).gte(datecol, lo).lte(datecol, hi)
                .order("id").range(off, off + 999).execute().data or [])
        out += page
        if len(page) < 1000:
            break
        off += 1000
    return out


def _delete_span(client, table: str, datecol: str, dmin: str, dmax: str) -> int:
    """Delete every row with datecol in [dmin, dmax], month by month (a single PostgREST call
    never has to return tens of thousands of rows). Returns the number removed."""
    removed = 0
    for lo, hi in month_windows(dmin, dmax):
        try:
            r = client.table(table).delete(count="exact").gte(datecol, lo).lte(datecol, hi).execute()
            n = r.count if getattr(r, "count", None) is not None else len(r.data or [])
        except TypeError:  # a client without the count keyword
            r = client.table(table).delete().gte(datecol, lo).lte(datecol, hi).execute()
            n = len(r.data or [])
        removed += int(n or 0)
    return removed


def _sales_span_replace(client, table: str, df: pd.DataFrame, datecol: str, replace_ok: bool) -> dict:
    """Clear `table` inside the file's own date span before the upsert (see the header note).
    Prints the invoices that would disappear; the guard turns a suspicious replace into an
    upsert-only load with an audit_log row. Returns {span, disappearing, removed, skipped}."""
    res: dict = {"span": None, "disappearing": [], "removed": 0, "skipped": None}
    span = date_span(df[datecol].tolist()) if datecol in df.columns else None
    if not span:
        print(f"  ({table}: no dates in the file -- upsert only)")
        return res
    dmin, dmax = span
    res["span"] = span
    file_inv = {str(v).strip() for v in df["invoice_no"].dropna().tolist()}
    cols = "id,invoice_no," + datecol + (",gross_bhd" if table == "orders" else "")
    try:
        db_rows = _fetch_span(client, table, cols, datecol, dmin, dmax)
    except Exception as e:  # noqa: BLE001 -- cannot see the span: never delete blind
        print(f"  !! {table}: could not read the database span {dmin}..{dmax} ({str(e)[:120]}); upsert only")
        res["skipped"] = f"span read failed: {str(e)[:120]}"
        return res
    db_inv = {str(r.get("invoice_no") or "").strip() for r in db_rows} - {""}
    gone = disappearing_invoices(db_rows, file_inv)
    res["disappearing"] = gone
    print(f"  {table}: file span {dmin}..{dmax}: {len(file_inv)} invoices in file, {len(db_inv)} in DB, "
          f"{len(gone)} would disappear")
    for r in gone[:50]:
        extra = f"  gross {r.get('gross_bhd')}" if table == "orders" else ""
        print(f"      - {r.get('invoice_no')}  {str(r.get(datecol) or '')[:10]}{extra}")
    if len(gone) > 50:
        print(f"      ... and {len(gone) - 50} more")
    reason = span_guard(gone, len(db_inv), replace_ok)
    if reason:
        print(f"  !! WARNING: {table} span replace SKIPPED -- {reason}. Loaded as upsert only; the rows above "
              f"stay in the database and verify will FAIL on the row counts.")
        _audit(client, f"{table}.span_replace_skipped", f"{dmin}..{dmax}",
               {"table": table, "span": [dmin, dmax], "reason": reason, "db_invoices": len(db_inv),
                "file_invoices": len(file_inv), "disappearing": [str(r.get("invoice_no")) for r in gone][:200]})
        res["skipped"] = reason
        return res
    res["removed"] = _delete_span(client, table, datecol, dmin, dmax)
    print(f"  ({table}: cleared {res['removed']} rows in {dmin}..{dmax} before reload)")
    if gone:
        _audit(client, f"{table}.span_replace", f"{dmin}..{dmax}",
               {"table": table, "span": [dmin, dmax], "removed_rows": res["removed"],
                "disappearing": [str(r.get("invoice_no")) for r in gone][:200]})
    return res


def _previous_warehouses(client, as_of: str) -> tuple[str | None, set[str]]:
    """(as_of_date, warehouse set) of the latest stock_balance snapshot strictly before `as_of`."""
    r = (client.table("stock_balance").select("as_of_date").lt("as_of_date", as_of)
         .order("as_of_date", desc=True).limit(1).execute().data or [])
    prev = (r or [{}])[0].get("as_of_date")
    if not prev:
        return None, set()
    prev = str(prev)[:10]
    names: set[str] = set()
    off = 0
    while True:
        page = (client.table("stock_balance").select("warehouse_name").eq("as_of_date", prev)
                .order("id").range(off, off + 999).execute().data or [])
        names |= {str(x.get("warehouse_name") or "(unassigned)") for x in page}
        if len(page) < 1000:
            break
        off += 1000
    return prev, names


def _stock_balance_replace(client, sb: pd.DataFrame, partial_ok: bool) -> dict:
    """Load a Stock_balance snapshot: aggregate duplicate item rows, warn (audit row) when the
    warehouse set shrank against the previous snapshot, then replace per (as_of_date,
    warehouse_name) and upsert. Returns {pairs, warnings}."""
    sb = sb.dropna(subset=["item_name"])
    sb["warehouse_name"] = sb["warehouse_name"].fillna("(unassigned)")
    for col in ("net_qty", "total_value_bhd", "selling_rate_bhd"):
        sb[col] = pd.to_numeric(sb[col], errors="coerce")
    # Focus can list an item twice in one warehouse — aggregate so the upsert
    # key (item, warehouse, as_of) is unique (sum qty/value, average rate).
    sb = (sb.groupby(["item_name", "warehouse_name", "as_of_date"], as_index=False, dropna=False)
            .agg(net_qty=("net_qty", "sum"),
                 total_value_bhd=("total_value_bhd", "sum"),
                 selling_rate_bhd=("selling_rate_bhd", "mean"),
                 source_file=("source_file", "first")))
    # Snapshot replace per (as_of_date, warehouse_name): a re-parse never leaves stale rows
    # of a warehouse the file covers, and a one-warehouse export never erases the others.
    pairs = sorted({(str(d)[:10], str(w)) for d, w in
                    zip(sb["as_of_date"].tolist(), sb["warehouse_name"].tolist()) if d is not None
                    and not (isinstance(d, float) and math.isnan(d))})
    warnings: list[str] = []
    for as_of in sorted({d for d, _ in pairs}):
        new_set = {w for d, w in pairs if d == as_of}
        try:
            prev_date, prev_set = _previous_warehouses(client, as_of)
        except Exception as e:  # noqa: BLE001
            prev_date, prev_set = None, set()
            print(f"  (stock_balance: previous snapshot not readable: {str(e)[:100]})")
        reason = warehouse_set_guard(prev_set, new_set, partial_ok)
        if reason:
            print(f"  !! WARNING: stock_balance {as_of} vs {prev_date}: {reason}")
            warnings.append(reason)
            _audit(client, "stock_balance.partial_snapshot", f"{as_of} vs {prev_date}",
                   {"as_of_date": as_of, "previous_as_of": prev_date, "new_warehouses": sorted(new_set),
                    "previous_warehouses": sorted(prev_set), "reason": reason})
        else:
            print(f"  (stock_balance {as_of}: {len(new_set)} warehouse(s)"
                  + (f"; previous snapshot {prev_date} had {len(prev_set)}" if prev_date else "") + ")")
    for as_of, wh in pairs:
        client.table("stock_balance").delete().eq("as_of_date", as_of).eq("warehouse_name", wh).execute()
    _upsert(client, "stock_balance", _records(sb), on_conflict="item_name,warehouse_name,as_of_date")
    return {"pairs": pairs, "warnings": warnings}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    staged: set[str] | None = None
    staged_folder = None
    if "--staged" in argv:
        staged_folder = argv[argv.index("--staged") + 1]
        staged = staged_files(staged_folder)
    replace_ok = marker_present(staged_folder, REPLACE_OK_MARKER) if staged_folder else False
    partial_ok = marker_present(staged_folder, PARTIAL_OK_MARKER) if staged_folder else False
    if not CLEAN.exists():
        print(f"ERROR: {CLEAN} not found. Run scripts/ingest.py first.")
        return 1
    client = _client()
    print("Loading data/clean/ -> Supabase\n" + "=" * 60)
    if staged is not None:
        print(f"  (staged this run: {', '.join(sorted(staged)) or 'nothing'}"
              f"{'; REPLACE_OK' if replace_ok else ''}{'; PARTIAL_OK' if partial_ok else ''})")

    # 1) products from price books
    sp = _read("selling_prices")
    if sp is not None:
        prods = (
            sp[["sku_code", "item_name", "unit_name", "status"]]
            .dropna(subset=["sku_code"])
            .drop_duplicates(subset=["sku_code"])
        )
        _upsert(client, "products", _records(prods), on_conflict="sku_code")

    # 2) customers from sales
    names: set[str] = set()
    od = _read("orders")
    ol = _read("order_lines")
    if od is not None:
        names |= {x for x in od["customer_name"].dropna().tolist()}
    if ol is not None:
        names |= {x for x in ol["customer_account"].dropna().tolist()}
    if names:
        _upsert(client, "customers",
                [{"name": n} for n in sorted(names)], on_conflict="name")

    # 3) fact + pricing tables. Sales headers and lines are replaced inside the file's own date
    #    span (see the note at the top): lines first, then headers, each by its own file's span.
    if ol is not None:
        ol = ol.dropna(subset=["invoice_no"])
        _sales_span_replace(client, "order_lines", ol, "line_date", replace_ok)
        _upsert(client, "order_lines", _records(ol), on_conflict="invoice_no,line_no")
    if od is not None:
        od = od.dropna(subset=["invoice_no"])
        _sales_span_replace(client, "orders", od, "order_date", replace_ok)
        _upsert(client, "orders", _records(od), on_conflict="invoice_no")
    sm = _read("stock_movements")
    if sm is not None:
        before = len(sm)
        sm = sm.dropna(subset=["item_name"])
        dropped = before - len(sm)
        if dropped:
            print(f"  (dropped {dropped} stock_movements rows with null item_name)")
        # Range replace, not append. The upsert key ends in row_hash, and the hash covers Focus's
        # running balance / average-rate columns, which Focus RECALCULATES retroactively (a later
        # receipt re-costs earlier rows). Every re-export of an overlapping window therefore
        # produced new hashes and the same voucher lines landed again: on 21-Sep-2026 the table
        # held 2.04x the units the ledger reported. Focus filters the export by date, so the file
        # is the whole truth for its own date span -- clear that span first, then load, exactly
        # like stock_balance / ar_ageing replace per as_of_date. Deleted month by month so a
        # single PostgREST call never has to return tens of thousands of rows.
        sm = sm.drop_duplicates(subset=["voucher", "item_name", "row_hash"])
        span = date_span(sm["move_date"].tolist())
        if span:
            dmin, dmax = span
            removed = _delete_span(client, "stock_movements", "move_date", dmin, dmax)
            print(f"  (stock_movements: cleared {removed} rows in {dmin}..{dmax} before reload)")
        _upsert(client, "stock_movements", _records(sm),
                on_conflict="voucher,item_name,row_hash")
    le = _read("ledger_entries")
    if le is not None:
        before = len(le)
        le = le.dropna(subset=["account"])
        le = le.drop_duplicates(subset=["account", "voucher", "row_hash"])
        dropped = before - len(le)
        if dropped:
            print(f"  (dropped {dropped} ledger_entries rows with null account or duplicates)")
        _upsert(client, "ledger_entries", _records(le),
                on_conflict="account,voucher,row_hash")
    pp = _read("product_profitability")
    if pp is not None:
        pp = pp.dropna(subset=["item_name"])
        # Focus can list the same product twice — keep one so the upsert key (item, report_date)
        # is unique (avoids "ON CONFLICT cannot affect row a second time").
        pp = pp.drop_duplicates(subset=["item_name", "report_date"], keep="last")
        _upsert(client, "product_profitability", _records(pp),
                on_conflict="item_name,report_date")
    if sp is not None:
        # warehouse_name is part of the key: the MA_base book holds three layers per SKU
        # (blank = dealer/list, 'Causeway'/'YQ Roadshow' = outlet retail). Without it they
        # collide. Requires scripts/pricebook_key_migration.sql (NULLS NOT DISTINCT) — without
        # that index this ON CONFLICT silently degrades to an INSERT and duplicates every row.
        #
        # The key deliberately excludes end_date, and Focus CAN export two rows that differ only
        # there (21-Sep-2026: T02 at 3.55 ending 2027-11-10 AND 2.95 open-ended, both starting
        # 2026-03-26). Two rows with one key inside a single upsert batch is exactly Postgres's
        # "ON CONFLICT DO UPDATE command cannot affect row a second time", which aborted the whole
        # daily load. Keep the LAST row per key -- Focus lists the newest entry last, and that is
        # the row the DB already held -- and say how many were folded so the log explains itself.
        key = ["sku_code", "price_book", "customer_code", "warehouse_name", "start_date"]
        before = len(sp)
        sp = sp.drop_duplicates(subset=key, keep="last")
        if before - len(sp):
            print(f"  (folded {before - len(sp)} selling_prices rows that repeat the same "
                  f"SKU/book/customer/warehouse/start_date -- kept the last one)")
        sp_recs = _records(sp)
        _upsert(client, "selling_prices", sp_recs, on_conflict=",".join(key))
        # Each Focus book is a snapshot: keys the file carries again come back to life, rows an
        # older export of the same book carried that this one does not are voided (never deleted),
        # only for a book staged this run and never en masse -- see _apply_pricebook_snapshot.
        _apply_pricebook_snapshot(client, sp_recs, staged)
    ar = _read("receivables")
    if ar is not None:
        ar = ar.dropna(subset=["account"])
        # Focus can list two accounts under one display NAME (different account codes, e.g. STAR LINE
        # MOBILES under 52517-42 and 52517-12). The upsert key is (account, as_of_date), so aggregate
        # by name first — SUM the balances/buckets (never drop a real balance), keep first for text.
        num_cols = [c for c in ("balance_bhd", "bucket_0_30", "bucket_31_60", "bucket_61_90",
                                "bucket_91_120", "bucket_121_150", "bucket_151_180", "bucket_181_210",
                                "bucket_over_210", "total_bhd") if c in ar.columns]
        for c in num_cols:
            ar[c] = pd.to_numeric(ar[c], errors="coerce")
        txt_cols = [c for c in ar.columns if c not in num_cols and c not in ("account", "as_of_date")]
        aggs = {c: (c, "sum") for c in num_cols}
        aggs.update({c: (c, "first") for c in txt_cols})
        ar = ar.groupby(["account", "as_of_date"], as_index=False, dropna=False).agg(**aggs)
        # snapshot replace: clear each as_of_date first so a re-parse never leaves stale accounts.
        for d in ar["as_of_date"].dropna().unique():
            client.table("ar_ageing").delete().eq("as_of_date", str(d)).execute()
        _upsert(client, "ar_ageing", _records(ar), on_conflict="account,as_of_date")
    art = _read("receivables_totals")
    if art is not None:
        # Focus's own Grand Total per snapshot (scripts/ingest.py parse_receivables_totals). The
        # table arrives with economics_v2_migration.sql; before it, the figure is printed only.
        art = art.dropna(subset=["as_of_date"])
        recs = []
        for r in _records(art):
            recs.append({"as_of_date": str(r["as_of_date"])[:10],
                         "focus_total_bhd": r.get("focus_total_bhd"), "focus_over90_bhd": r.get("focus_over90_bhd"),
                         "rows_total_bhd": r.get("rows_total_bhd"), "source_file": r.get("source_file")})
        try:
            _upsert(client, "ar_ageing_totals", recs, on_conflict="as_of_date")
        except Exception as e:  # noqa: BLE001
            print(f"  (ar_ageing_totals not written: {str(e)[:120]} -- apply scripts/economics_v2_migration.sql)")
            for r in recs:
                print(f"    Focus Grand Total {r['as_of_date']}: BHD {float(r['focus_total_bhd'] or 0):,.3f} "
                      f"(rows sum {float(r['rows_total_bhd'] or 0):,.3f})")
    sb = _read("stock_balance")
    if sb is not None:
        _stock_balance_replace(client, sb, partial_ok)

    # Record the load for the "Data as of" freshness banner (best-effort).
    try:
        latest = client.table("order_lines").select("line_date").order(
            "line_date", desc=True).limit(1).execute().data
        data_date = (latest or [{}])[0].get("line_date")
        client.table("ingest_runs").insert({
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "status": "ok",
            "file": f"Focus refresh (data as of {data_date})",
            "rows_loaded": (ol is not None and len(ol)) or 0,
        }).execute()
    except Exception as e:
        print(f"  (ingest_runs not recorded: {e})")

    print("=" * 60)
    print("Load complete. Run scripts/reconcile_products.py to link item names -> SKUs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
