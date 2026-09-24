"""Apply a .sql file to Postgres via DATABASE_URL (for DDL the REST key can't run).

Usage:
    python -m scripts.apply_sql scripts/dashboard_migration.sql
    python -m scripts.apply_sql scripts/x_migration.sql --rehearse   # run it, then ROLL BACK

--rehearse (24-Sep-2026 release protocol) executes the file on production inside one
transaction with lock_timeout 2s / statement_timeout 60s and always rolls back, so a
migration is proven against the real schema before it is applied. A real apply uses the same
timeouts so a lock wait can never stall checkout.

DATABASE_URL comes from .env — Supabase → Settings → Database → Connection string
→ Session pooler (URI).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()


def main(argv: list[str]) -> int:
    rehearse = "--rehearse" in argv
    argv = [a for a in argv if a != "--rehearse"]
    if not argv:
        print("usage: python -m scripts.apply_sql <file.sql> [--rehearse]")
        return 1
    url = os.getenv("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL not set in .env (Supabase → Settings → Database → Session pooler URI).")
        return 1
    try:
        import psycopg  # type: ignore
    except ImportError:
        print("ERROR: needs psycopg →  pip install 'psycopg[binary]'")
        return 1

    sql_path = Path(argv[0])
    if not sql_path.is_absolute():
        sql_path = ROOT / sql_path
    sql = sql_path.read_text(encoding="utf-8")
    import re
    if rehearse and re.search(r"^\s*(commit|end)\s*;", sql, re.I | re.M):
        print("REFUSED: the file contains its own COMMIT, so a rehearsal could not roll it back.")
        return 1
    print(f"{'Rehearsing (will ROLL BACK)' if rehearse else 'Applying'} {sql_path.name} via DATABASE_URL …")
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("set lock_timeout = '2s'")
            cur.execute("set statement_timeout = '60s'")
            cur.execute(sql)
        if rehearse:
            conn.rollback()
            print("Rehearsal OK: every statement ran; nothing was committed.")
            return 0
        conn.commit()
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
