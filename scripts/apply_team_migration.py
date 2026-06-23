"""Apply the team-management migration (scripts/team_management.sql) to Postgres.

Runs the idempotent DDL that the REST/service-role key cannot (ALTER TABLE / CREATE
TABLE), then verifies the new columns + table are visible. One command, no SQL editor.

Needs a direct Postgres connection string in DATABASE_URL — Supabase Dashboard →
Project Settings → Database → Connection string → "Session pooler" (URI form):
    postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres

Usage:
    python -m scripts.apply_team_migration
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

SQL_FILE = ROOT / "scripts" / "team_management.sql"


def main() -> int:
    url = os.getenv("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL is not set.")
        print("Add it to your .env (Supabase → Settings → Database → Connection string → Session pooler).")
        return 1
    try:
        import psycopg  # type: ignore
    except ImportError:
        print("ERROR: needs psycopg →  pip install 'psycopg[binary]'")
        return 1

    sql = SQL_FILE.read_text(encoding="utf-8")
    print(f"Applying {SQL_FILE.name} …")
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
        # Verify the migration actually landed.
        with conn.cursor() as cur:
            cur.execute("""
                select column_name from information_schema.columns
                where table_name = 'user_roles' and column_name in ('features','status','full_name')
            """)
            cols = sorted(r[0] for r in cur.fetchall())
            cur.execute("select to_regclass('public.app_invites')")
            invites = cur.fetchone()[0]
    print(f"  user_roles new columns: {cols}")
    print(f"  app_invites table:      {invites}")
    ok = cols == ["features", "full_name", "status"] and invites == "app_invites"
    print("Migration applied and verified." if ok else "WARNING: verification incomplete — check output above.")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
