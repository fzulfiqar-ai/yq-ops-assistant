"""Read-only preview of a Focus drop folder against the live database (release R2a).

    python -m scripts.ingest_preview 240926
    python -m scripts.ingest_preview "210926/As On date Reports" --out business_data/ingest_previews/210926
    python -m scripts.ingest_preview <folder> --no-db          # file-side figures only
    python -m scripts.ingest_preview <folder> --today 2026-09-24

Reuses scripts/ingest.py's parsers and the loader rules (app/ingest_batch.py) and reports, per
target table: insert / update / unchanged / would-remove counts, per-day and per-salesman sales
totals vs the database, price changes vs the live book, stock deltas and the warehouse set,
unaliased item names, unclassified divisions, MRN DocNos already loaded (re-import blocked) and
cost coverage. Writes summary.md + exceptions.csv (+ summary.json) to
business_data/ingest_previews/<timestamp>/ (gitignored).

READ-ONLY by construction: the psycopg session is opened read-only (conn.read_only = True), the
parsed rows are staged in a TEMP table that vanishes with the session, and no batch row is
created. Needs DATABASE_URL in .env (the session-pooler URI); without it, or with --no-db, the
file-side preview is still written. Exit 0 = no blocking exceptions, 2 = blocking exceptions,
1 = error.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

import warnings  # noqa: E402
warnings.filterwarnings("ignore")


def _say(s: str) -> None:
    try:
        print(s)
    except UnicodeEncodeError:
        print(s.encode("ascii", "replace").decode())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only preview of a Focus drop folder")
    ap.add_argument("folder")
    ap.add_argument("--out", help="output folder (default business_data/ingest_previews/<ts>)")
    ap.add_argument("--no-db", action="store_true", help="file-side figures only")
    ap.add_argument("--dsn", help="Postgres URL to preview against (default DATABASE_URL from .env)")
    ap.add_argument("--today", help="the 'today' used for current-price selection (default: the DB's date)")
    args = ap.parse_args(argv)

    from app.ingest_batch import BLOCKING, PgBackend, run_preview

    folder = Path(args.folder)
    if not folder.is_absolute():
        folder = ROOT / folder
    if not folder.is_dir():
        _say(f"ERROR: folder not found: {folder}")
        return 1
    out = Path(args.out) if args.out else None
    if out is not None and not out.is_absolute():
        out = ROOT / out

    backend = None
    conn = None
    dsn = None if args.no_db else (args.dsn or os.getenv("DATABASE_URL"))
    if dsn:
        try:
            import psycopg
            conn = psycopg.connect(dsn, connect_timeout=30)
            backend = PgBackend(conn, read_only=True)
            with conn.cursor() as cur:
                cur.execute("set statement_timeout = '120s'")
        except Exception as e:  # noqa: BLE001
            _say(f"WARNING: no database ({str(e)[:120]}); file-side preview only")
            backend = None
    elif not args.no_db:
        _say("WARNING: DATABASE_URL not set; file-side preview only (--no-db to silence)")

    today = args.today
    if backend is not None and not today:
        try:
            today = str(backend.query("select current_date::text as d")[0]["d"])[:10]
        except Exception:  # noqa: BLE001
            today = None

    try:
        res = run_preview(folder, backend, actor="ingest_preview", persist=False, out_dir=out, today=today)
    finally:
        if conn is not None:
            conn.rollback()   # nothing to keep: the session was read-only and the temp table dies with it
            conn.close()

    s, exceptions = res["summary"], res["exceptions"]
    _say("=" * 78)
    _say(f"Preview of {s['folder']}  (db: {s['db_mode']}, migration applied: {s.get('migration_applied')})")
    _say("=" * 78)
    for f in s["files"]:
        _say(f"  {f['file'][:60]:60} -> {f['target']:22} {f.get('rows', f.get('raw_rows')):>7} rows")
    for i in s["ignored"]:
        _say(f"  ignored {i['file'][:52]:52} ({i['reason']})")
    _say("-" * 78)
    _say(f"  {'target':22} {'file':>7} {'insert':>7} {'update':>7} {'same':>7} {'remove':>7}  file sums / DB sums in scope")
    for t, v in s["targets"].items():
        a = v.get("actions") or {}
        rem = a.get("deleted", a.get("voided", ""))
        sums = ", ".join(f"{k}={x}" for k, x in (v.get("sums") or {}).items())
        dbs = ", ".join(f"{k}={x}" for k, x in (v.get("db_sums") or {}).items())
        _say(f"  {t:22} {v['file_rows']:>7} {a.get('inserted', '-'):>7} {a.get('updated', '-'):>7} "
             f"{a.get('unchanged', '-'):>7} {rem if rem != '' else '-':>7}  {sums}" + (f" | DB {v.get('db_rows')} rows: {dbs}" if dbs else ""))
    sales = s.get("sales") or {}
    if sales.get("per_day_changed") is not None:
        _say("-" * 78)
        _say(f"  days that change: {len(sales['per_day_changed'])}; salesmen that change: {len(sales['per_salesman_changed'])}")
        for d in sales["per_day_changed"][-8:]:
            _say(f"    {d['key']}: file {d['file_rows']} lines / {d['file_gross']} vs DB {d['db_rows']} / {d['db_gross']} (delta {d['delta_gross']})")
    st = s.get("stock") or {}
    if st.get("per_warehouse"):
        _say("-" * 78)
        for w in st["per_warehouse"]:
            _say(f"  stock {w['warehouse']}: file {w['file_qty']} u / {w['file_value']} vs DB({st['db_latest_as_of']}) "
                 f"{w['db_qty']} u / {w['db_value']}; delta {w['delta_qty']} u / {w['delta_value']}")
    for book, p in (s.get("prices") or {}).items():
        if isinstance(p, dict) and "file_skus" in p:
            _say(f"  prices {book}: {p['file_skus']} SKUs, {p['unchanged']} unchanged, {len(p['changes'])} changes, "
                 f"{len(p['new_skus'])} new, {len(p['dropped_skus'])} dropped")
    _say("-" * 78)
    for e in exceptions:
        _say(f"  [{e['severity']:8}] {e['code']:28} {e['message'][:120]}")
    _say("-" * 78)
    blocking = s.get("blocking_codes") or []
    _say(f"  preview complete and commit-able: {s.get('commit_available')}"
         + (f"   blocking exceptions to acknowledge before a commit: {blocking}" if blocking else "   no blocking exceptions")
         + "   (this preview created no batch; commit from the Data page)")
    if res.get("preview_dir"):
        _say(f"  written: {res['preview_dir']}")
    return 2 if any(e["severity"] == BLOCKING for e in exceptions) else 0


if __name__ == "__main__":
    raise SystemExit(main())
