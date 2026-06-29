"""Per-user authentication + team management, backed by Supabase Auth.

Replaces the old single shared dashboard password (`DASHBOARD_SECRET` / "yq2024").
Every user has a real Supabase Auth account — Supabase handles password hashing and
brute-force lockout — while their role + per-page feature access live in `user_roles`.

Two onboarding paths (admin chooses per invite):
  • temp password  — admin creates the account now with a generated password and a
    `must_reset` flag; the member is forced to set their own password on first login.
  • email invite   — a row in `app_invites` + a link the member opens to set their
    own password (works once a sending domain is verified in Resend).

A FRESH client is used for sign-in so the cached service-role client
(`app.database.get_client`) is never re-authenticated as the signing-in user.
"""
from __future__ import annotations

import logging
import os
import secrets
import string
from datetime import datetime, timezone

from supabase import create_client

from app.config import settings
from app.database import get_client

log = logging.getLogger(__name__)

# Grantable features — each maps to a portal nav page. "Team" is admin-only and is
# added implicitly for admins; it is never granted to members.
FEATURES: list[str] = [
    "Dashboard", "AI Agents", "AI Assistant",
    "Inventory", "Sales", "Margins", "Receivables",
]
ROLES: list[str] = ["admin", "member"]


# ── clients / helpers ────────────────────────────────────────────────────────

def _fresh_client():
    """Throwaway client — never the cached service client (sign-in mutates auth state)."""
    return create_client(settings.supabase_url, settings.supabase_key)


def _user_row(email: str) -> dict | None:
    email = (email or "").strip().lower()
    r = get_client().table("user_roles").select(
        "email,role,features,status,full_name"
    ).eq("email", email).limit(1).execute()
    return (r.data or [None])[0]


def _find_auth_user(email: str):
    """Find a Supabase Auth user by email (None if absent)."""
    email = (email or "").strip().lower()
    try:
        users = get_client().auth.admin.list_users()
    except Exception as e:
        log.warning("list_users failed: %s", e)
        return None
    for u in users:
        if (getattr(u, "email", "") or "").lower() == email:
            return u
    return None


def _app_base_url() -> str:
    return os.getenv("APP_BASE_URL", "").rstrip("/")


def generate_temp_password() -> str:
    """A readable, strong temporary password (e.g. 'Yq-7fK2bQ9x')."""
    alphabet = string.ascii_letters + string.digits
    return "Yq-" + "".join(secrets.choice(alphabet) for _ in range(8))


# ── sign in ──────────────────────────────────────────────────────────────────

def verify_login(email: str, password: str) -> dict | None:
    """Return the session dict if credentials are valid AND the user is active.

    dict = {email, role, features, full_name, must_reset}. Returns None for empty
    input, bad password, a disabled account, or an account with no user_roles row.
    """
    email = (email or "").strip().lower()
    if not email or not password:
        return None
    try:
        sess = _fresh_client().auth.sign_in_with_password({"email": email, "password": password})
    except Exception:
        return None
    if not (sess and getattr(sess, "session", None) and sess.session.access_token):
        return None
    row = _user_row(email)
    if not row or row.get("status", "active") != "active":
        log.warning("login: %s authenticated but not provisioned/active", email)
        return None
    meta = (getattr(sess, "user", None) and getattr(sess.user, "user_metadata", None)) or {}
    return {
        "email": email,
        "role": row.get("role", "member"),
        "features": row.get("features") or [],
        "full_name": row.get("full_name") or meta.get("full_name") or "",
        "must_reset": bool(meta.get("must_reset")),
    }


# ── user provisioning ────────────────────────────────────────────────────────

def _upsert_role(email: str, role: str, features: list[str],
                 full_name: str = "", invited_by: str = "", status: str = "active") -> None:
    email = email.strip().lower()
    row: dict = {"email": email, "role": role, "features": features, "status": status}
    if full_name:
        row["full_name"] = full_name
    if invited_by:
        row["invited_by"] = invited_by
    client = get_client()
    if _user_row(email):
        client.table("user_roles").update(row).eq("email", email).execute()
    else:
        client.table("user_roles").insert(row).execute()


def create_member(email: str, full_name: str, role: str, features: list[str],
                  password: str, invited_by: str = "", must_reset: bool = True) -> dict:
    """Create (or update) a Supabase Auth user + their user_roles row."""
    email = email.strip().lower()
    if role not in ROLES:
        role = "member"
    meta = {"must_reset": must_reset, "full_name": full_name}
    client = get_client()
    existing = _find_auth_user(email)
    if existing:
        client.auth.admin.update_user_by_id(existing.id, {"password": password, "user_metadata": meta})
    else:
        client.auth.admin.create_user(
            {"email": email, "password": password, "email_confirm": True, "user_metadata": meta}
        )
    _upsert_role(email, role, features, full_name, invited_by, status="active")
    return {"email": email, "role": role, "features": features, "full_name": full_name}


def set_password(email: str, password: str) -> bool:
    """Set a user's password and clear the must_reset flag (used by force-reset)."""
    u = _find_auth_user(email)
    if not u:
        return False
    meta = dict(getattr(u, "user_metadata", None) or {})
    meta["must_reset"] = False
    get_client().auth.admin.update_user_by_id(u.id, {"password": password, "user_metadata": meta})
    return True


def update_access(email: str, role: str | None = None,
                  features: list[str] | None = None, status: str | None = None) -> None:
    upd: dict = {}
    if role is not None:
        upd["role"] = role
    if features is not None:
        upd["features"] = features
    if status is not None:
        upd["status"] = status
    if upd:
        get_client().table("user_roles").update(upd).eq("email", email.strip().lower()).execute()


def remove_user(email: str) -> None:
    """Delete the Auth user and the user_roles row."""
    email = email.strip().lower()
    u = _find_auth_user(email)
    if u:
        try:
            get_client().auth.admin.delete_user(u.id)
        except Exception as e:
            log.warning("delete_user failed for %s: %s", email, e)
    get_client().table("user_roles").delete().eq("email", email).execute()


def list_members() -> dict:
    """Return {'users': [...active accounts...], 'invites': [...pending invites...]}."""
    client = get_client()
    users = client.table("user_roles").select(
        "email,role,features,status,full_name"
    ).order("role").execute().data or []
    try:
        invites = client.table("app_invites").select(
            "email,role,features,full_name,status,expires_at,token"
        ).eq("status", "pending").execute().data or []
    except Exception:
        invites = []
    return {"users": users, "invites": invites}


# ── email-invite path ────────────────────────────────────────────────────────

def create_email_invite(email: str, full_name: str, role: str,
                        features: list[str], invited_by: str = "") -> dict:
    """Create a pending invite + email the set-password link. Returns status dict."""
    email = email.strip().lower()
    if role not in ROLES:
        role = "member"
    token = secrets.token_urlsafe(32)
    get_client().table("app_invites").insert({
        "email": email, "role": role, "features": features,
        "full_name": full_name, "token": token, "invited_by": invited_by,
        "status": "pending",
    }).execute()
    base = _app_base_url()
    link = f"{base}/invite?token={token}" if base else f"/invite?token={token}"
    email_status = _send_invite_email(email, full_name, role, link)
    return {"token": token, "link": link, "email": email_status}


def get_invite(token: str) -> dict | None:
    """Fetch a pending, non-expired invite by token."""
    if not token:
        return None
    r = get_client().table("app_invites").select("*").eq("token", token).eq(
        "status", "pending"
    ).limit(1).execute()
    inv = (r.data or [None])[0]
    if not inv:
        return None
    exp = inv.get("expires_at")
    if exp:
        try:
            if datetime.fromisoformat(exp.replace("Z", "+00:00")) < datetime.now(timezone.utc):
                return None
        except Exception:
            pass
    return inv


def accept_invite(token: str, password: str, full_name: str | None = None) -> dict | None:
    """Member sets their password → create the account + mark invite accepted."""
    inv = get_invite(token)
    if not inv:
        return None
    email = inv["email"].strip().lower()
    name = full_name or inv.get("full_name") or ""
    create_member(email, name, inv["role"], inv.get("features") or [],
                  password, invited_by=inv.get("invited_by", ""), must_reset=False)
    get_client().table("app_invites").update({
        "status": "accepted", "accepted_at": datetime.now(timezone.utc).isoformat()
    }).eq("token", token).execute()
    return {"email": email, "role": inv["role"], "features": inv.get("features") or [], "full_name": name}


def revoke_invite(token: str) -> None:
    get_client().table("app_invites").update({"status": "revoked"}).eq("token", token).execute()


def _send_invite_email(email: str, full_name: str, role: str, link: str) -> dict:
    from app.emailer import PURPLE, PURPLE_DARK, send_html
    greeting = f"Hi {full_name}," if full_name else "Hello,"
    html = f"""\
<!DOCTYPE html><html><body style="margin:0;background:#f0eff4;font-family:Inter,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="padding:32px 12px;"><tr><td align="center">
<table width="100%" style="max-width:560px;" cellpadding="0" cellspacing="0">
  <tr><td bgcolor="{PURPLE_DARK}" style="background-color:{PURPLE_DARK};background:linear-gradient(135deg,{PURPLE},{PURPLE_DARK});border-radius:16px 16px 0 0;padding:28px 32px;">
    <div style="font-size:.7rem;font-weight:700;letter-spacing:2px;color:#c4b5fd;">YQ BAHRAIN · MOBILE ACCESSORIES</div>
    <div style="font-size:1.3rem;font-weight:800;color:#fff;margin-top:6px;">You're invited to the AI Portal</div>
  </td></tr>
  <tr><td style="background:#fff;padding:28px 32px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 16px 16px;">
    <p style="font-size:.95rem;color:#111827;margin:0 0 8px;">{greeting}</p>
    <p style="font-size:.9rem;color:#374151;line-height:1.6;margin:0 0 20px;">
      You've been added to the YQ Bahrain AI Portal as a <strong>{role.title()}</strong>.
      Click below to set your password and activate your account.</p>
    <a href="{link}" style="display:inline-block;background:{PURPLE};color:#fff;text-decoration:none;
       font-weight:700;font-size:.9rem;padding:12px 28px;border-radius:10px;">Set my password →</a>
    <p style="font-size:.72rem;color:#9ca3af;margin-top:24px;">If the button doesn't work, copy this link:<br>{link}</p>
    <p style="font-size:.72rem;color:#9ca3af;margin-top:16px;padding-top:14px;border-top:1px solid #f1eefe;">
      This invite expires in 7 days · YQ Bahrain W.L.L</p>
  </td></tr>
</table></td></tr></table></body></html>"""
    return send_html(f"You're invited to the YQ Bahrain AI Portal", html, to=email)
