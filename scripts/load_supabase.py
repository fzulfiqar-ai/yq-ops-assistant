"""Phase 0 load: cleaned CSVs in data/clean/ -> Supabase (idempotent upserts).

Order:
  1. products   (derived from selling_prices: distinct sku_code + item_name + unit + status)
  2. customers  (derived from orders.customer_name + order_lines.customer_account)
  3. orders, order_lines, stock_movements, ledger_entries, product_profitability, selling_prices

Upserts use each table's natural key (on_conflict) so re-running never double-counts
(data rules 2/6 friendly). Requires the tables created by migrate_supabase.py.

Usage:  python scripts/load_supabase.py
"""
from __future__ import annotations

import math
import re
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
# VOIDED, never deleted: voided_at / void_reason (selling_prices_void_migration.sql), and every
# price view skips them. Workbook imports (scripts/import_workbook_prices.py, source 'YQ_MRN%')
# are a different source of truth and are never touched; nor is a NEWER export already on file.
FOCUS_BOOKS = {"MA_base": "masellingpricebook", "modern_trade": "moderntradesellerbook"}


def focus_book_kind(source_file) -> str | None:
    """The price_book a Focus export file name belongs to ('MA_base' / 'modern_trade'), or None for
    anything else (workbook imports, blanks). The portal stages 'MASellingPriceBook (40).xlsx' as
    'MASellingPriceBook _40_.xlsx' (app/main.py sanitises the name): both spell the same book."""
    n = Path(str(source_file or "")).name.strip().lower()
    for book, stem in FOCUS_BOOKS.items():
        if n.startswith(stem):
            return book
    return None


def focus_book_seq(source_file) -> int:
    """The download counter in a Focus book file name -- '(40)' or the portal's '_40_' -- else 0.
    Later exports carry higher numbers, which is what makes "older" decidable."""
    m = re.search(r"[(_\s](\d+)[)_]", Path(str(source_file or "")).name)
    return int(m.group(1)) if m else 0


def pricebook_key(r: dict) -> tuple:
    """The natural key the upsert uses (pricebook_key_migration.sql), normalised so a CSV row and a
    PostgREST row compare equal: blanks, None and NaN become '', dates keep their first 10 chars."""
    def _s(v) -> str:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return ""
        return str(v).strip()
    return (_s(r.get("sku_code")), _s(r.get("price_book")), _s(r.get("customer_code")),
            _s(r.get("warehouse_name")), _s(r.get("start_date"))[:10])


def stale_pricebook_rows(loaded: list[dict], existing: list[dict]) -> list[dict]:
    """Pure: which live rows the upload just made supersedes.

    loaded   -- the rows just upserted (any price_book; only Focus-book source files count)
    existing -- live (voided_at is null) rows of the same price_book(s): id, price_book,
                source_file and the key columns
    Returns the existing rows to void: same price_book, from a DIFFERENT Focus export of that book
    whose counter is not newer than the loaded file's, and whose key the loaded file does not carry.
    Rows of other sources (workbook imports), rows the file still carries and rows of a newer export
    are left alone."""
    books: dict[str, dict] = {}
    for r in loaded:
        book = r.get("price_book")
        src = str(r.get("source_file") or "")
        if not book or focus_book_kind(src) != book:
            continue
        b = books.setdefault(book, {"src": src, "seq": focus_book_seq(src), "keys": set()})
        if focus_book_seq(src) > b["seq"]:          # two exports of one book in one load: the newest rules
            b["src"], b["seq"] = src, focus_book_seq(src)
        b["keys"].add(pricebook_key(r))
    out: list[dict] = []
    for r in existing:
        b = books.get(r.get("price_book") or "")
        if not b:
            continue
        rsrc = str(r.get("source_file") or "")
        if rsrc == b["src"] or focus_book_kind(rsrc) != r.get("price_book"):
            continue
        if focus_book_seq(rsrc) > b["seq"]:          # a newer export is on file: an older file never voids it
            continue
        if pricebook_key(r) in b["keys"]:
            continue
        out.append(r)
    return out


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


def _void_superseded_prices(client, loaded: list[dict]) -> int:
    """Void live selling_prices rows an older Focus export of the same book left behind (see
    stale_pricebook_rows). Returns how many were voided; 0 with a printed reason when the void
    columns are not there yet (the migration has not run) -- the load itself is unaffected."""
    books = sorted({r.get("price_book") for r in loaded
                    if r.get("price_book") and focus_book_kind(r.get("source_file")) == r.get("price_book")})
    if not books:
        return 0
    existing: list[dict] = []
    for book in books:
        off = 0
        while True:
            try:
                page = (client.table("selling_prices")
                        .select("id,sku_code,price_book,customer_code,warehouse_name,start_date,source_file")
                        .eq("price_book", book).is_("voided_at", "null").order("id")
                        .range(off, off + 999).execute().data or [])
            except Exception as e:  # noqa: BLE001 — voided_at missing until selling_prices_void_migration.sql
                print(f"  (selling_prices snapshot rule skipped: {str(e)[:120]} -- apply "
                      f"scripts/selling_prices_void_migration.sql)")
                return 0
            existing += page
            if len(page) < 1000:
                break
            off += 1000
    stale = stale_pricebook_rows(loaded, existing)
    if not stale:
        print("  (selling_prices: no superseded Focus book rows to void)")
        return 0
    now = datetime.now(timezone.utc).isoformat()
    srcs = {r["price_book"]: str(r["source_file"]) for r in loaded
            if focus_book_kind(r.get("source_file")) == r.get("price_book")}
    by_book: dict[str, list[int]] = {}
    for r in stale:
        by_book.setdefault(str(r["price_book"]), []).append(int(r["id"]))
    for book, ids in by_book.items():
        reason = f"superseded by {srcs.get(book, '?')} (Focus book snapshot: key not in the newer export)"
        for i in range(0, len(ids), CHUNK):
            (client.table("selling_prices").update({"voided_at": now, "void_reason": reason})
             .in_("id", ids[i:i + CHUNK]).execute())
        print(f"  voided   selling_prices {len(ids):7}  ({book}: rows of older Focus books not in {srcs.get(book)})")
    return len(stale)


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


def main() -> int:
    if not CLEAN.exists():
        print(f"ERROR: {CLEAN} not found. Run scripts/ingest.py first.")
        return 1
    client = _client()
    print("Loading data/clean/ -> Supabase\n" + "=" * 60)

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

    # 3) fact + pricing tables
    if od is not None:
        _upsert(client, "orders", _records(od), on_conflict="invoice_no")
    if ol is not None:
        _upsert(client, "order_lines", _records(ol), on_conflict="invoice_no,line_no")
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
        dts = sorted(sm["move_date"].dropna().astype(str).unique())
        if dts:
            dmin, dmax = dts[0][:10], dts[-1][:10]
            import calendar
            months = sorted({d[:7] for d in dts})
            removed = 0
            for ym in months:
                y, m = int(ym[:4]), int(ym[5:7])
                lo = max(dmin, f"{ym}-01")
                hi = min(dmax, f"{ym}-{calendar.monthrange(y, m)[1]:02d}")   # real month end, not "-31"
                r = (client.table("stock_movements").delete()
                     .gte("move_date", lo).lte("move_date", hi).execute())
                removed += len(r.data or [])
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
        # Each Focus book is a snapshot: rows an older export of the same book carried that this
        # one does not are voided (never deleted) -- see stale_pricebook_rows.
        _void_superseded_prices(client, sp_recs)
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
    sb = _read("stock_balance")
    if sb is not None:
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
        # snapshot replace: clear each as_of_date first so a re-parse never leaves
        # stale rows (e.g. items whose key changed) double-counting the total.
        for d in sb["as_of_date"].dropna().unique():
            client.table("stock_balance").delete().eq("as_of_date", str(d)).execute()
        _upsert(client, "stock_balance", _records(sb),
                on_conflict="item_name,warehouse_name,as_of_date")

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
