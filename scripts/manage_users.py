"""Manage dashboard users from the command line (Supabase Auth + user_roles).

Use this to bootstrap your first admin login (the password stays on your machine —
it is never sent through chat or stored in the repo).

  # set / reset a user's password and role (creates the account if missing)
  python -m scripts.manage_users set fzulfiqar@pie-int.com 'YourStrongPassword' admin

  # grant a member specific pages only
  python -m scripts.manage_users set ali@yq.com 'TempPass123' member --features Dashboard,Sales,Inventory

  # list users + pending invites
  python -m scripts.manage_users list

  # remove a user entirely
  python -m scripts.manage_users remove ali@yq.com
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

from app.user_auth import (  # noqa: E402
    FEATURES, ROLES, create_member, list_members, remove_user,
)


def _cmd_set(args) -> int:
    role = args.role if args.role in ROLES else "member"
    if args.features:
        feats = [f.strip() for f in args.features.split(",") if f.strip()]
    else:
        feats = list(FEATURES)  # full access by default
    invalid = [f for f in feats if f not in FEATURES]
    if invalid:
        print(f"Unknown feature(s): {invalid}\nValid: {FEATURES}")
        return 1
    # admin accounts always get every feature
    if role == "admin":
        feats = list(FEATURES)
    create_member(args.email, args.full_name or "", role, feats, args.password,
                  invited_by="cli", must_reset=args.must_reset)
    print(f"OK — {args.email} set as {role} with access: {', '.join(feats)}"
          + ("  (must reset on first login)" if args.must_reset else ""))
    return 0


def _cmd_list(_args) -> int:
    data = list_members()
    print(f"\nUSERS ({len(data['users'])}):")
    for u in data["users"]:
        feats = u.get("features") or []
        print(f"  {u.get('role','?'):<7} {u.get('status','?'):<8} {u.get('email','')}"
              f"  [{', '.join(feats) if feats else '—'}]")
    inv = data.get("invites") or []
    if inv:
        print(f"\nPENDING INVITES ({len(inv)}):")
        for i in inv:
            print(f"  {i.get('role','?'):<7} {i.get('email','')}  expires {i.get('expires_at','')}")
    print()
    return 0


def _cmd_remove(args) -> int:
    remove_user(args.email)
    print(f"Removed {args.email}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Manage YQ portal users.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("set", help="create/update a user's password, role, features")
    s.add_argument("email")
    s.add_argument("password")
    s.add_argument("role", nargs="?", default="member", help="admin | member")
    s.add_argument("--features", default="", help="comma list (members only); default = all")
    s.add_argument("--full-name", default="", dest="full_name")
    s.add_argument("--must-reset", action="store_true",
                   help="force the user to choose a new password on first login")
    s.set_defaults(func=_cmd_set)

    sub.add_parser("list", help="list users + pending invites").set_defaults(func=_cmd_list)

    r = sub.add_parser("remove", help="delete a user")
    r.add_argument("email")
    r.set_defaults(func=_cmd_remove)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
