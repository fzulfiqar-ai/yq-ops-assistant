"""Marketplace reset -- wipe the shop's TRANSACTIONAL data for a clean public launch (21-Sep-2026).

    python -m scripts.marketplace_reset                      # dry run: exact row counts, nothing changes
    python -m scripts.marketplace_reset --execute --backup-dir business_data/backups/2026-09-21_0659

What goes (in foreign-key order, one transaction, counts asserted to zero afterwards):
    shop_order_events, shop_order_lines, shop_orders          every order placed so far
    shop_customer_phones, shop_customer_sessions,
    shop_access_links, shop_push_subscriptions,
    shop_restock_requests, shop_customers                     every merchant record
    shop_events, catalog_visits                               browsing / vitals analytics
    shop_counters                                             order numbering restarts at YQ-YYMM-0001
    app_settings.shop_stale_alerted_at                        cleared, so the stale-stock alert re-arms

What stays (master / configuration / auth / Focus data): salesmen, user_roles, catalog_items,
catalog_stock_map, discount_rules, shop_campaigns, shop_reserved_slugs, app_settings (all other
keys), products, prices, stock, sales, receivables, leads, field_notes, order_files, every
storage bucket.

Safety: --execute refuses unless --backup-dir points at a scripts/db_backup.py folder whose
manifest covers every table below (that folder is the rollback: `python -m scripts.db_backup
--restore <dir> --tables <the list printed at the end> --yes`). The run is written to
audit_log so the reset is visible in the portal's history.

LIVE-DATA LOCK (24-Sep-2026): the marketplace is live and holds real orders. --execute now
also refuses while shop_orders has ANY row, unless the owner has explicitly authorised wiping
live orders for this one run and the operator passes --i-understand-this-deletes-live-orders.
An old "start fresh" instruction is not that authorisation.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

# Children before parents. shop_customers last among the merchant tables; shop_orders
# references it (customer_id) so orders go first.
TABLES = [
    "shop_order_events", "shop_order_lines", "shop_orders",
    "shop_customer_phones", "shop_customer_sessions", "shop_access_links",
    "shop_push_subscriptions", "shop_restock_requests", "shop_customers",
    "shop_events", "catalog_visits", "shop_counters",
]
SETTINGS_TO_CLEAR = ["shop_stale_alerted_at"]


def _conn():
    import psycopg
    return psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=30)


def counts(cur) -> dict[str, int]:
    out = {}
    for t in TABLES:
        cur.execute(f'select count(*) from public."{t}"')
        out[t] = cur.fetchone()[0]
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--backup-dir", help="scripts/db_backup.py folder that covers every table above")
    ap.add_argument("--i-understand-this-deletes-live-orders", dest="live_ok", action="store_true",
                    help="required while real orders exist; needs the owner's explicit written go-ahead")
    a = ap.parse_args(argv)

    with _conn() as conn:
        cur = conn.cursor()
        before = counts(cur)
        total = sum(before.values())
        print("Marketplace transactional data right now:")
        for t, n in before.items():
            print(f"  {t:28s} {n:7d}")
        print(f"  {'TOTAL':28s} {total:7d}")
        cur.execute("select key, value from app_settings where key = any(%s)", (SETTINGS_TO_CLEAR,))
        for k, v in cur.fetchall():
            print(f"  app_settings.{k} = {v!r} -> ''")

        if not a.execute:
            print("\nDry run. Nothing changed. Re-run with --execute --backup-dir <folder> to wipe.")
            return 0

        if before.get("shop_orders", 0) and not a.live_ok:
            print(f"\nREFUSED: shop_orders holds {before['shop_orders']} live order(s). The marketplace is live; "
                  f"a reset would delete real transactions. See the LIVE-DATA LOCK note in this script.")
            return 2
        if not a.backup_dir:
            print("\nREFUSED: --execute needs --backup-dir (take one with: python -m scripts.db_backup)")
            return 2
        manifest_path = Path(a.backup_dir) / "manifest.json"
        if not manifest_path.exists():
            print(f"\nREFUSED: no manifest.json in {a.backup_dir}")
            return 2
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        missing = [t for t in TABLES if t not in manifest.get("tables", {})]
        if missing:
            print(f"\nREFUSED: backup does not cover: {', '.join(missing)}")
            return 2
        stale = [t for t in TABLES if manifest["tables"][t]["rows"] < before[t]]
        if stale:
            print(f"\nREFUSED: live tables have MORE rows than the backup ({', '.join(stale)}) -- "
                  f"take a fresh backup first.")
            return 2

        with conn.transaction():
            for t in TABLES:
                cur.execute(f'delete from public."{t}"')
            cur.execute("update app_settings set value = '', updated_by = 'marketplace_reset', updated_at = now() "
                        "where key = any(%s)", (SETTINGS_TO_CLEAR,))
            after = counts(cur)
            left = {t: n for t, n in after.items() if n}
            if left:
                raise RuntimeError(f"rows remain after delete: {left} -- rolled back")
            cur.execute(
                "insert into audit_log (ts, user_email, event, detail) values (now(), %s, %s, %s)",
                ("marketplace_reset", "shop.reset",
                 json.dumps({"deleted": before, "backup": str(a.backup_dir),
                             "at": datetime.now(timezone.utc).isoformat()})))
        print(f"\nReset complete: {total} rows removed across {len(TABLES)} tables. "
              f"Rollback (db_backup orders parents first itself): python -m scripts.db_backup --restore "
              f"{a.backup_dir} --tables {','.join(reversed(TABLES))} --yes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
