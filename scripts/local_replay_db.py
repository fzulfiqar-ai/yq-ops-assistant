"""Build a LOCAL replay database from production: schema + masked, non-PII data (R2, 24-Sep-2026).

    python -m scripts.local_replay_db --db r2_loader            # create / refresh (drops the local DB first)
    python -m scripts.local_replay_db --db r2_loader --schema-only

Production is read only here: `pg_dump --schema-only` of the public schema and SELECTs inside a
READ ONLY connection. The local cluster is the portable PostgreSQL in %LOCALAPPDATA%\\yq-tools
(port 55432, trust auth, user postgres) that the backup drill uses; the 'drill' database is never
touched. tests/test_r2_loader.py replays the loader, the verify checks and economics_v2_migration.sql
against the result and rolls every transaction back, so the copy stays as built.

What is copied (COPY, identity ids kept, sequences re-seeded): categories, products, product_aliases,
orders, order_lines, stock_balance, stock_movements, product_profitability, selling_prices,
purchase_costs, mrn_landed_costs, mrn_lines, catalog_items, catalog_stock_map, division_rules,
app_settings (keys that look like secrets or contacts excluded), ingest_runs (none), audit_log (none).
What is masked: every merchant name column (orders.customer_name, order_lines.customer_account,
ar_ageing.account, selling_prices.customer_name) becomes a stable 'CUST-<hash>' so joins and
sums still work; stock_movements.narration is dropped. Never copied: customers, ledger_entries,
salesmen, user_roles, every shop_* / marketing / lead / outreach / wa_* / customer_* table, kb_chunks
(pgvector) and audit_log.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PG_BIN = Path(os.environ.get("LOCALAPPDATA", "")) / "yq-tools" / "pgsql" / "bin"
LOCAL_PORT = int(os.environ.get("YQ_LOCAL_PG_PORT", "55432"))
LOCAL_URL = f"postgresql://postgres@localhost:{LOCAL_PORT}"
ROLES = ("anon", "authenticated", "service_role", "yq_readonly", "supabase_admin")

# (table, select-list with masking, order column)
_MASK_NAME = "'CUST-' || left(md5({col}), 10)"
COPY_TABLES: list[tuple[str, list[tuple[str, str]]]] = [
    ("categories", []),
    ("products", []),
    ("product_aliases", []),
    ("orders", [("customer_name", _MASK_NAME.format(col="customer_name")), ("customer_id", "NULL::bigint")]),
    ("order_lines", [("customer_account", _MASK_NAME.format(col="customer_account"))]),
    ("stock_balance", []),
    ("stock_movements", [("narration", "NULL::text")]),
    ("product_profitability", []),
    ("selling_prices", [("customer_name", _MASK_NAME.format(col="customer_name"))]),
    ("purchase_costs", []),
    ("mrn_landed_costs", []),
    ("mrn_lines", []),
    ("catalog_items", []),
    ("catalog_stock_map", []),
    ("division_rules", []),
    ("ar_ageing", [("account", _MASK_NAME.format(col="account"))]),
    ("app_settings", []),
]
_SETTINGS_FILTER = ("WHERE key NOT ILIKE '%token%' AND key NOT ILIKE '%key%' AND key NOT ILIKE '%secret%' "
                    "AND key NOT ILIKE '%pass%' AND key NOT ILIKE '%phone%' AND key NOT ILIKE '%email%' "
                    "AND key NOT ILIKE '%number%' AND key NOT ILIKE '%url%'")


def _psql(db: str, sql: str | None = None, file: str | None = None, on_error_stop: bool = True) -> subprocess.CompletedProcess:
    cmd = [str(PG_BIN / "psql.exe"), "-h", "localhost", "-p", str(LOCAL_PORT), "-U", "postgres", "-d", db,
           "-v", f"ON_ERROR_STOP={'1' if on_error_stop else '0'}", "-q"]
    if file:
        cmd += ["-f", file]
    else:
        cmd += ["-c", sql or ""]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def local_reachable() -> bool:
    r = subprocess.run([str(PG_BIN / "pg_isready.exe"), "-h", "localhost", "-p", str(LOCAL_PORT)],
                       capture_output=True, text=True)
    return r.returncode == 0


def build(db: str, schema_only: bool = False, out_dir: Path | None = None) -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    if not os.environ.get("DATABASE_URL") and os.environ.get("YQ_ENV_FILE"):
        load_dotenv(os.environ["YQ_ENV_FILE"])       # a worktree has no .env of its own
    src = os.environ.get("DATABASE_URL")
    if not src:
        print("ERROR: DATABASE_URL not set (.env, or YQ_ENV_FILE=<path to the main checkout's .env>)")
        return 1
    if not local_reachable():
        print(f"ERROR: local Postgres on port {LOCAL_PORT} is not reachable (start it with pg_ctl, see the module doc)")
        return 1
    if db == "drill":
        print("REFUSED: the 'drill' database belongs to the backup drill")
        return 1
    out_dir = out_dir or (ROOT / "data" / "_replay")
    out_dir.mkdir(parents=True, exist_ok=True)
    schema_file = out_dir / f"{db}_schema.sql"

    t0 = time.time()
    # roles the dumped policies reference (NOLOGIN; nothing else)
    for role in ROLES:
        _psql("postgres", f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') "
                          f"THEN CREATE ROLE {role} NOLOGIN; END IF; END $$;")
    _psql("postgres", f"DROP DATABASE IF EXISTS {db}")
    r = _psql("postgres", f"CREATE DATABASE {db}")
    if r.returncode != 0:
        print("createdb failed:", r.stderr[:300])
        return 1

    # schema from production (read only), public schema only; kb_chunks needs pgvector -> excluded
    r = subprocess.run([str(PG_BIN / "pg_dump.exe"), "--schema-only", "--schema=public", "--no-owner",
                        "--no-privileges", "--no-comments", "--exclude-table=public.kb_chunks",
                        "-f", str(schema_file), src], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print("pg_dump failed:", r.stderr[:400])
        return 1
    # `match_kb` references the vector type: drop that function from the file rather than the whole restore
    txt = schema_file.read_text(encoding="utf-8")
    cleaned, skip = [], False
    for line in txt.splitlines():
        if line.startswith("CREATE FUNCTION public.match_kb") or line.startswith("CREATE OR REPLACE FUNCTION public.match_kb"):
            skip = True
        if not skip:
            cleaned.append(line)
        elif line.strip().endswith("$$;") or line.strip() == "$$;":
            skip = False
    schema_file.write_text("\n".join(cleaned) + "\n", encoding="utf-8")
    r = _psql(db, file=str(schema_file), on_error_stop=False)
    errs = [ln for ln in (r.stderr or "").splitlines() if "ERROR" in ln]
    print(f"schema restored into {db} ({len(errs)} statement errors tolerated) in {time.time() - t0:.1f}s")
    for e in errs[:8]:
        print("   ", e[:160])

    if schema_only:
        return 0

    import psycopg
    prod = psycopg.connect(src)
    prod.read_only = True
    local = psycopg.connect(f"{LOCAL_URL}/{db}", autocommit=False)
    try:
        with prod.cursor() as pc, local.cursor() as lc:
            for table, masks in COPY_TABLES:
                pc.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' "
                           "AND table_name=%s ORDER BY ordinal_position", (table,))
                cols = [r[0] for r in pc.fetchall()]
                if not cols:
                    print(f"  skip {table}: not in production")
                    continue
                masked = dict(masks)
                select = ", ".join(masked.get(c, f'"{c}"') + f' AS "{c}"' for c in cols)
                where = _SETTINGS_FILTER if table == "app_settings" else ""
                col_list = ", ".join(f'"{c}"' for c in cols)
                lc.execute(f'TRUNCATE TABLE "{table}" CASCADE')
                n = 0
                with pc.copy(f"COPY (SELECT {select} FROM \"{table}\" {where} ORDER BY 1) TO STDOUT") as src_copy, \
                        lc.copy(f'COPY "{table}" ({col_list}) FROM STDIN') as dst_copy:
                    for chunk in src_copy:
                        dst_copy.write(chunk)
                lc.execute(f'SELECT COUNT(*) FROM "{table}"')
                n = lc.fetchone()[0]
                if "id" in cols:
                    lc.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE(MAX(id), 0) + 1, false) "
                               f"FROM \"{table}\"")
                print(f"  copied {table:24} {n:7} rows" + (f"  (masked: {', '.join(masked)})" if masked else ""))
        local.commit()
    finally:
        prod.close()
        local.close()
    print(f"done in {time.time() - t0:.1f}s -> {LOCAL_URL}/{db}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", default="r2_loader")
    ap.add_argument("--schema-only", action="store_true")
    a = ap.parse_args(argv)
    return build(a.db, a.schema_only)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
