"""Set a Supabase Auth password for a user (no user_roles write).

Works before the team migration — use it to bootstrap a login for review:
  python -m scripts.set_auth_password fzulfiqar@pie-int.com 'YourPassword'
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

from supabase import create_client  # noqa: E402
from app.config import settings  # noqa: E402


def main(email: str, password: str) -> int:
    email = email.strip().lower()
    c = create_client(settings.supabase_url, settings.supabase_key)
    users = c.auth.admin.list_users()
    uid = next((u.id for u in users if (getattr(u, "email", "") or "").lower() == email), None)
    if uid:
        c.auth.admin.update_user_by_id(uid, {"password": password, "email_confirm": True})
        print(f"updated auth password for {email}")
    else:
        c.auth.admin.create_user({"email": email, "password": password, "email_confirm": True})
        print(f"created auth user {email}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: python -m scripts.set_auth_password EMAIL PASSWORD")
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
