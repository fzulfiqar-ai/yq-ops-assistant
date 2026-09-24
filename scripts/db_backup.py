"""Table-level CSV backup + guarded restore for the live Supabase Postgres.

The Free plan has no point-in-time recovery, so before any production change we take our
own snapshot. Plain CSV via COPY, one file per table, plus manifest.json -- readable by
anyone, no pg_dump needed.

Backup
    python -m scripts.db_backup                 # the default table set (orders, merchants, prices, targets ...)
    python -m scripts.db_backup --all           # every public table (recommended before a release)
    python -m scripts.db_backup --tables shop_orders,shop_order_lines --out business_data/backups/x

    Every table is read inside ONE read-only REPEATABLE READ transaction, so the files are a
    consistent snapshot (an order created mid-backup can never appear without its lines).
    The manifest records, per table: rows, bytes, column names/types, max(id), max(created_at).

Verify a backup folder (re-reads every CSV, compares with the manifest; no DB needed)
    python -m scripts.db_backup --verify business_data/backups/2026-09-24_prerollout

Restore (dry run unless --yes; one transaction; rolled back on any mismatch)
    python -m scripts.db_backup --restore <dir> --tables shop_orders,shop_order_lines,shop_order_events --yes

    Guards (24-Sep-2026, after the audit found a restore could silently destroy live rows):
      * FK closure -- refuses when a table OUTSIDE --tables references a restored table with
        ON DELETE CASCADE / SET NULL / SET DEFAULT and holds rows (e.g. restoring shop_orders
        alone would cascade away every shop_order_lines row; restoring salesmen alone would
        null every order's salesman_id).
      * Newer live data -- refuses when the live table has rows newer than the backup
        (max id / max created_at), so an old backup can never overwrite orders placed since.
        Override only with --allow-newer, deliberately.
      * Column drift -- COPY uses the CSV header's column list with HEADER MATCH (PG 15+);
        refuses when a NOT NULL column without a default is missing from the backup.
      * Order -- deletes children before parents and loads parents before children
        (computed from pg_constraint), whatever order --tables was given in.
      * Every restored table's count must equal the manifest; every referencing table's
        count must be unchanged; an audit_log row records the restore.

Backups land under business_data/ (gitignored) -- commercial data and merchant PII must
never reach the repo.
"""
from __future__ import annotations

import argparse
import csv
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

csv.field_size_limit(1 << 30)

# The default set: everything a marketplace / pricing / targets change can touch. Every
# public table whose name starts with shop_ is added at run time, so a new shop table is
# never forgotten.
DEFAULT_TABLES = [
    "app_settings", "user_roles", "audit_log", "salesmen", "salesman_targets", "salesman_channels",
    "customers", "catalog_items", "catalog_stock_map", "discount_rules", "catalog_visits",
    "selling_prices", "stock_balance", "mrn_lines", "mrn_landed_costs", "purchase_costs",
    "products", "product_aliases", "procurement_orders",
]
DEL_ACTIONS = {"c": "CASCADE", "n": "SET NULL", "d": "SET DEFAULT"}


def _conn(dsn_env: str = "DATABASE_URL"):
    import psycopg
    url = os.environ.get(dsn_env)
    if not url:
        raise SystemExit(f"{dsn_env} missing in .env (Supabase -> Settings -> Database -> Session pooler URI)")
    return psycopg.connect(url, connect_timeout=30)


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _public_tables(cur) -> list[str]:
    cur.execute("select table_name from information_schema.tables "
                "where table_schema='public' and table_type='BASE TABLE' order by table_name")
    return [r[0] for r in cur.fetchall()]


def _columns(cur, t: str) -> list[dict]:
    cur.execute("""select column_name, data_type, is_nullable, column_default is not null or is_identity = 'YES'
                   from information_schema.columns where table_schema='public' and table_name=%s
                   order by ordinal_position""", (t,))
    return [{"name": n, "type": ty, "nullable": nu == "YES", "has_default": bool(d)} for n, ty, nu, d in cur.fetchall()]


def _fks(cur) -> list[dict]:
    """Every FK in public: child table -> parent table, with its ON DELETE action."""
    cur.execute("""select c.conname, cl.relname as child, pl.relname as parent, c.confdeltype
                   from pg_constraint c
                   join pg_class cl on cl.oid = c.conrelid
                   join pg_class pl on pl.oid = c.confrelid
                   join pg_namespace n on n.oid = cl.relnamespace
                   where c.contype = 'f' and n.nspname = 'public'""")
    return [{"name": a, "child": b, "parent": p, "on_delete": d} for a, b, p, d in cur.fetchall()]


def _maxes(cur, t: str, cols: list[dict]) -> dict:
    out = {}
    types = {c["name"]: c["type"] for c in cols}
    if types.get("id") in ("bigint", "integer", "smallint"):
        cur.execute(f'select max(id)::text from public."{t}"')
        out["max_id"] = cur.fetchone()[0]
    if "created_at" in types:
        cur.execute(f'select max(created_at)::text from public."{t}"')
        out["max_created_at"] = cur.fetchone()[0]
    return out


# ── backup ─────────────────────────────────────────────────────────────────────────────
def backup(tables: list[str] | None, out: Path, all_tables: bool = False) -> Path:
    import psycopg
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"taken_at": datetime.now(timezone.utc).isoformat(), "git": _git_sha(),
                "snapshot": "repeatable read, read only, one transaction", "tables": {}}
    with _conn() as conn:
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        conn.read_only = True
        with conn.transaction(), conn.cursor() as cur:
            cur.execute("show transaction_read_only")
            assert cur.fetchone()[0] == "on", "backup session is not read-only"
            cur.execute("set local time zone 'UTC'")      # timestamps in the CSVs are always +00
            live = _public_tables(cur)
            if all_tables:
                wanted = live
            else:
                base = tables if tables is not None else DEFAULT_TABLES + [t for t in live if t.startswith("shop_")]
                wanted = list(dict.fromkeys(base))
            manifest["fks"] = _fks(cur)
            for t in wanted:
                if t not in live:
                    print(f"  skip {t}: not a public table")
                    continue
                cols = _columns(cur, t)
                path = out / f"{t}.csv"
                with path.open("wb") as fh, cur.copy(f'COPY public."{t}" TO STDOUT WITH (FORMAT csv, HEADER true)') as cp:
                    for chunk in cp:
                        fh.write(chunk)
                cur.execute(f'select count(*) from public."{t}"')          # same snapshot as the COPY
                n = cur.fetchone()[0]
                manifest["tables"][t] = {"rows": n, "bytes": path.stat().st_size, "columns": cols,
                                         **_maxes(cur, t, cols)}
                print(f"  {t:32s} {n:8d} rows -> {path.name}")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    bad = verify(out, quiet=True)
    print(f"\nBackup complete: {out}  ({len(manifest['tables'])} tables, git {manifest['git']}, "
          f"verify {'OK' if bad == 0 else f'FAILED on {bad} table(s)'})")
    if bad:
        raise SystemExit(1)
    return out


# ── verify ─────────────────────────────────────────────────────────────────────────────
def _csv_rows(path: Path) -> tuple[list[str], int, str | None, str | None]:
    """(header, data rows, max id, max created_at) of one backup CSV."""
    with path.open(encoding="utf-8", newline="") as fh:
        rd = csv.reader(fh)
        header = next(rd, [])
        i_id = header.index("id") if "id" in header else None
        i_ca = header.index("created_at") if "created_at" in header else None
        n, mid, mca = 0, None, None
        for row in rd:
            n += 1
            if i_id is not None and row[i_id] != "":
                v = int(row[i_id]) if row[i_id].lstrip("-").isdigit() else None
                if v is not None and (mid is None or v > mid):
                    mid = v
            if i_ca is not None and row[i_ca] and (mca is None or row[i_ca] > mca):
                mca = row[i_ca]
    return header, n, (str(mid) if mid is not None else None), mca


def verify(src: Path, quiet: bool = False) -> int:
    manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    bad = 0
    for t, meta in manifest["tables"].items():
        path = src / f"{t}.csv"
        if not path.exists():
            print(f"  MISSING {t}.csv")
            bad += 1
            continue
        header, n, _, _ = _csv_rows(path)
        ok = n == meta["rows"]
        if "columns" in meta:
            ok = ok and header == [c["name"] for c in meta["columns"]]
        if not ok:
            bad += 1
            print(f"  MISMATCH {t}: csv {n} rows vs manifest {meta['rows']}")
        elif not quiet:
            print(f"  ok {t:32s} {n:8d}")
    if not quiet:
        print(f"\nVerify {'OK' if bad == 0 else 'FAILED'}: {len(manifest['tables'])} tables, {bad} problem(s)")
    return bad


# ── restore ────────────────────────────────────────────────────────────────────────────
def _topo(tables: list[str], fks: list[dict]) -> list[str]:
    """Parents before children, limited to `tables` (self-references ignored)."""
    deps = {t: {f["parent"] for f in fks if f["child"] == t and f["parent"] in tables and f["parent"] != t}
            for t in tables}
    order, seen = [], set()

    def visit(t, stack=()):
        if t in seen:
            return
        if t in stack:
            raise SystemExit(f"FK cycle among {stack + (t,)}; restore these tables manually")
        for p in sorted(deps[t]):
            visit(p, stack + (t,))
        seen.add(t)
        order.append(t)

    for t in tables:
        visit(t)
    return order


def restore(src: Path, tables: list[str], yes: bool, allow_newer: bool = False,
            dsn_env: str = "DATABASE_URL") -> int:
    manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    missing = [t for t in tables if t not in manifest["tables"]]
    if missing:
        print(f"ERROR: not in this backup: {', '.join(missing)}")
        return 1
    if verify(src, quiet=True):
        print("ERROR: the backup folder does not match its manifest (run --verify); refusing.")
        return 1
    print(f"Restore from {src} (taken {manifest['taken_at']}, git {manifest['git']})"
          f"{'' if dsn_env == 'DATABASE_URL' else f' into {dsn_env}'}")
    problems: list[str] = []
    with _conn(dsn_env) as conn:
        with conn.cursor() as cur:
            fks = _fks(cur)
            order = _topo(tables, fks)
            # 1. FK closure: children outside the set that a DELETE would cascade into / null out.
            closure: dict[str, int] = {}
            for f in fks:
                if f["parent"] in tables and f["child"] not in tables:
                    cur.execute(f'select count(*) from public."{f["child"]}"')
                    n = cur.fetchone()[0]
                    closure[f["child"]] = n
                    if n and f["on_delete"] in DEL_ACTIONS:
                        problems.append(f"{f['child']} ({n} rows) references {f['parent']} ON DELETE "
                                        f"{DEL_ACTIONS[f['on_delete']]}: restoring {f['parent']} without it would "
                                        f"{'delete' if f['on_delete'] == 'c' else 'blank the link on'} those rows. "
                                        f"Add {f['child']} to --tables.")
            # 2. Newer live data + column drift, per table.
            for t in order:
                header, n_csv, csv_max_id, csv_max_ca = _csv_rows(src / f"{t}.csv")
                live_cols = _columns(cur, t)
                live_names = [c["name"] for c in live_cols]
                cur.execute(f'select count(*) from public."{t}"')
                n_live = cur.fetchone()[0]
                lm = _maxes(cur, t, live_cols)
                print(f"  {t:32s} live {n_live:8d} -> backup {n_csv:8d}  "
                      f"max id {lm.get('max_id')} -> {csv_max_id}")
                newer = []
                if lm.get("max_id") and str(lm["max_id"]).isdigit() and (
                        csv_max_id is None or int(lm["max_id"]) > int(csv_max_id)):
                    newer.append(f"max id {lm['max_id']} > backup {csv_max_id}")
                if lm.get("max_created_at"):
                    # compare as timestamptz in SQL: the CSV and the live session may print different zones
                    if csv_max_ca is None:
                        newer.append(f"max created_at {lm['max_created_at']} (backup has none)")
                    else:
                        cur.execute(f'select exists(select 1 from public."{t}" where created_at > %s::timestamptz)',
                                    (csv_max_ca,))
                        if cur.fetchone()[0]:
                            newer.append(f"max created_at {lm['max_created_at']} > backup {csv_max_ca}")
                if newer and not allow_newer:
                    problems.append(f"{t}: live data is NEWER than the backup ({'; '.join(newer)}). "
                                    f"Restoring would delete rows created since the backup.")
                unknown = [c for c in header if c not in live_names]
                if unknown:
                    problems.append(f"{t}: backup has columns the live table lacks: {', '.join(unknown)}")
                need = [c["name"] for c in live_cols if not c["nullable"] and not c["has_default"]
                        and c["name"] not in header]
                if need:
                    problems.append(f"{t}: live NOT NULL columns missing from the backup: {', '.join(need)}")
        if problems:
            print("\nREFUSED:")
            for p in problems:
                print(f"  - {p}")
            return 2
        if not yes:
            print(f"\nDry run OK (order: {', '.join(order)}). Re-run with --yes to apply "
                  f"(single transaction, rolled back on any mismatch).")
            return 0
        with conn.transaction(), conn.cursor() as cur:
            cur.execute("set local lock_timeout = '5s'")
            for t in reversed(order):                       # children first
                cur.execute(f'DELETE FROM public."{t}"')
            for t in order:                                 # parents first
                header, _, _, _ = _csv_rows(src / f"{t}.csv")
                cols = ", ".join(f'"{c}"' for c in header)
                with (src / f"{t}.csv").open("rb") as fh, \
                        cur.copy(f'COPY public."{t}" ({cols}) FROM STDIN WITH (FORMAT csv, HEADER MATCH)') as cp:
                    while chunk := fh.read(1 << 20):
                        cp.write(chunk)
                cur.execute(f'select count(*) from public."{t}"')
                got = cur.fetchone()[0]
                if got != manifest["tables"][t]["rows"]:
                    raise RuntimeError(f"{t}: restored {got} rows, manifest says {manifest['tables'][t]['rows']} -- rolled back")
                has_int_id = any(c["name"] == "id" and c["type"] in ("bigint", "integer", "smallint")
                                 for c in _columns(cur, t))
                seq = None
                if has_int_id:
                    cur.execute("select pg_get_serial_sequence(%s, 'id')", (f'public."{t}"',))
                    seq = cur.fetchone()
                if seq and seq[0]:
                    cur.execute(f"select setval(%s, coalesce((select max(id) from public.\"{t}\"), 0) + 1, false)", (seq[0],))
                print(f"  restored {t:32s} {got:8d} rows")
            for child, n_before in closure.items():
                cur.execute(f'select count(*) from public."{child}"')
                if cur.fetchone()[0] != n_before:
                    raise RuntimeError(f"{child} changed during the restore -- rolled back")
            cur.execute("insert into audit_log (ts, user_email, event, detail) values (now(), %s, %s, %s)",
                        ("db_backup", "db.restore", json.dumps({"from": str(src), "tables": order,
                                                                "taken_at": manifest["taken_at"]})))
    print("Restore complete.")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tables", help="comma-separated table list (default: the marketplace/pricing/targets set)")
    ap.add_argument("--all", action="store_true", help="back up every public table")
    ap.add_argument("--out", help="backup folder (default business_data/backups/<UTC timestamp>)")
    ap.add_argument("--verify", metavar="DIR", help="re-read a backup folder and compare it with its manifest")
    ap.add_argument("--restore", metavar="DIR", help="restore --tables from this backup folder")
    ap.add_argument("--yes", action="store_true", help="actually apply the restore")
    ap.add_argument("--allow-newer", action="store_true",
                    help="DANGER: restore even though live rows are newer than the backup")
    ap.add_argument("--dsn-env", default="DATABASE_URL",
                    help="env var holding the target DSN for --restore (e.g. a scratch DB for a drill)")
    a = ap.parse_args(argv)
    tables = [t.strip() for t in a.tables.split(",") if t.strip()] if a.tables else None
    if a.verify:
        return 1 if verify(Path(a.verify)) else 0
    if a.restore:
        if not tables:
            print("ERROR: --restore needs an explicit --tables list")
            return 1
        return restore(Path(a.restore), tables, a.yes, a.allow_newer, a.dsn_env)
    out = Path(a.out) if a.out else ROOT / "business_data" / "backups" / datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    backup(tables, out, a.all)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
