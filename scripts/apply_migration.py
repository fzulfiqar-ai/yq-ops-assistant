"""Apply any SQL migration file to Postgres (idempotent DDL the REST key can't run).

Same mechanism as scripts/apply_team_migration.py, generalized. Needs DATABASE_URL
(Supabase → Settings → Database → Connection string → Session pooler, URI form).

Usage:
    python -m scripts.apply_migration scripts/security_migration.sql [more.sql ...]
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
        print("Usage: python -m scripts.apply_migration <file.sql> [more.sql ...]")
        return 1
    url = os.getenv("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL is not set (Supabase → Settings → Database → Session pooler URI).")
        return 1
    try:
        import psycopg  # type: ignore
    except ImportError:
        print("ERROR: needs psycopg →  pip install 'psycopg[binary]'")
        return 1

    files = [ROOT / a if not Path(a).is_absolute() else Path(a) for a in argv]
    missing = [f for f in files if not f.exists()]
    if missing:
        print("ERROR: not found: " + ", ".join(str(m) for m in missing))
        return 1

    with psycopg.connect(url) as conn:
        for f in files:
            print(f"Applying {f.name} …")
            with conn.cursor() as cur:
                cur.execute(f.read_text(encoding="utf-8"))
            conn.commit()
            print(f"  {f.name} applied.")
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
