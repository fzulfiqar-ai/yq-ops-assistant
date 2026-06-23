"""Apply a .sql file to Postgres via DATABASE_URL (for DDL the REST key can't run).

Usage:
    python -m scripts.apply_sql scripts/dashboard_migration.sql

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
    if not argv:
        print("usage: python -m scripts.apply_sql <file.sql>")
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
    print(f"Applying {sql_path.name} via DATABASE_URL …")
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
