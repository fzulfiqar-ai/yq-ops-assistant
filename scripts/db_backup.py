"""Table-level CSV backup + restore for the live Supabase Postgres (21-Sep-2026).

The Free plan has no point-in-time recovery, so before any destructive maintenance
(marketplace reset, seed-row deletes, bulk re-loads) we take our own snapshot of the
tables involved. Plain CSV via COPY, one file per table, plus manifest.json with exact
row counts, the git commit and the timestamp -- readable by anyone, no pg_dump needed.

Backup (default table set = everything the launch session touches):
    python -m scripts.db_backup
    python -m scripts.db_backup --tables shop_orders,shop_order_lines --out business_data/backups/x

Restore ONE OR MORE tables from a backup folder (single transaction, row counts asserted):
    python -m scripts.db_backup --restore business_data/backups/2026-09-21_0930 --tables shop_orders,shop_order_lines --yes

Restore replaces the table's current rows with the backup's (DELETE + COPY FROM), then
re-seeds the identity sequence so new inserts do not collide. Without --yes it only
prints what it would do. Restore order matters for foreign keys: pass parents first
(shop_customers before shop_orders before shop_order_lines) -- the script sorts the
default set for you; a custom --tables list is restored in the order given.

Backups land under business_data/ (gitignored) -- commercial data must never reach the
public repo.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

# Parent tables first so a full restore satisfies foreign keys in one pass.
DEFAULT_TABLES = [
    "app_settings", "salesmen", "salesman_targets", "catalog_items", "catalog_stock_map",
    "discount_rules", "shop_campaigns", "shop_reserved_slugs",
    "shop_customers", "shop_customer_phones", "shop_customer_sessions", "shop_access_links",
    "shop_push_subscriptions", "shop_restock_requests",
    "shop_orders", "shop_order_lines", "shop_order_events", "shop_counters",
    "shop_events", "catalog_visits",
    "selling_prices", "stock_balance", "mrn_lines", "mrn_landed_costs", "purchase_costs",
    "products", "product_aliases",
]


def _conn():
    import psycopg
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL missing in .env (Supabase -> Settings -> Database -> Session pooler URI)")
    return psycopg.connect(url, connect_timeout=30)


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def backup(tables: list[str], out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"taken_at": datetime.now(timezone.utc).isoformat(), "git": _git_sha(), "tables": {}}
    with _conn() as conn:
        conn.execute("SET default_transaction_read_only = on")
        for t in tables:
            path = out / f"{t}.csv"
            with conn.cursor() as cur:
                cur.execute("select count(*) from information_schema.tables where table_schema='public' and table_name=%s", (t,))
                if cur.fetchone()[0] == 0:
                    print(f"  skip {t}: not a public table")
                    continue
                with path.open("wb") as fh, cur.copy(f'COPY public."{t}" TO STDOUT WITH (FORMAT csv, HEADER true)') as cp:
                    for chunk in cp:
                        fh.write(chunk)
                cur.execute(f'select count(*) from public."{t}"')
                n = cur.fetchone()[0]
            manifest["tables"][t] = {"rows": n, "bytes": path.stat().st_size}
            print(f"  {t:28s} {n:8d} rows -> {path.name}")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nBackup complete: {out}  ({len(manifest['tables'])} tables, git {manifest['git']})")
    return out


def restore(src: Path, tables: list[str], yes: bool) -> int:
    manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    missing = [t for t in tables if t not in manifest["tables"]]
    if missing:
        print(f"ERROR: not in this backup: {', '.join(missing)}")
        return 1
    print(f"Restore from {src} (taken {manifest['taken_at']}, git {manifest['git']})")
    for t in tables:
        print(f"  {t:28s} {manifest['tables'][t]['rows']:8d} rows will REPLACE the live table")
    if not yes:
        print("\nDry run. Re-run with --yes to apply (single transaction, rolled back on any mismatch).")
        return 0
    with _conn() as conn:
        with conn.transaction():
            for t in tables:
                expected = manifest["tables"][t]["rows"]
                with conn.cursor() as cur:
                    cur.execute(f'DELETE FROM public."{t}"')
                    with (src / f"{t}.csv").open("rb") as fh, \
                            cur.copy(f'COPY public."{t}" FROM STDIN WITH (FORMAT csv, HEADER true)') as cp:
                        while chunk := fh.read(1 << 20):
                            cp.write(chunk)
                    cur.execute(f'select count(*) from public."{t}"')
                    got = cur.fetchone()[0]
                    if got != expected:
                        raise RuntimeError(f"{t}: restored {got} rows, backup manifest says {expected} -- rolled back")
                    # re-seed the identity/serial sequence (no-op for tables without one)
                    cur.execute("""
                        select column_name from information_schema.columns
                        where table_schema='public' and table_name=%s and column_name='id'
                    """, (t,))
                    if cur.fetchone():
                        cur.execute(f"""select setval(pg_get_serial_sequence('public."{t}"','id'),
                                        coalesce((select max(id) from public."{t}"), 0) + 1, false)""")
                print(f"  restored {t:28s} {got:8d} rows")
    print("Restore complete.")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tables", help="comma-separated table list (default: the launch-session set)")
    ap.add_argument("--out", help="backup folder (default business_data/backups/<UTC timestamp>)")
    ap.add_argument("--restore", metavar="DIR", help="restore --tables from this backup folder")
    ap.add_argument("--yes", action="store_true", help="actually apply the restore")
    a = ap.parse_args(argv)
    tables = [t.strip() for t in a.tables.split(",")] if a.tables else list(DEFAULT_TABLES)
    if a.restore:
        return restore(Path(a.restore), tables, a.yes)
    out = Path(a.out) if a.out else ROOT / "business_data" / "backups" / datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    backup(tables, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
