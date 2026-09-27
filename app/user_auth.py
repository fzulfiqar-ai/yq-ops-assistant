"""Per-user authentication + team management, backed by Supabase Auth.

Replaces the old single shared dashboard password (`DASHBOARD_SECRET` / "yq2024").
Every user has a real Supabase Auth account — Supabase handles password hashing and
brute-force lockout — while their role + per-page feature access live in `user_roles`.

Two onboarding paths (admin chooses per invite):
  • temp password  — admin creates the account now with a generated password and a
    `must_reset` flag; the member is forced to set their own password on first login.
  • email invite   — a row in `app_invites` + a link the member opens to set their
    own password (works once a sending domain is verified in Resend).

`must_reset` is SERVER-OWNED (R1 security S6, 24-Sep-2026): the truth is user_roles.must_reset
(scripts/user_roles_must_reset_migration.sql), enforced by app.auth.get_current_user and
cleared only here after the API itself sets the new password (POST /auth/password). The copy in
the auth user_metadata is kept for the SPA banner but is user-writable, so nothing trusts it.
Until the migration runs the column is absent; every write below tolerates that.

Disabling a member also BANS the Supabase auth user (and re-activating unbans), so a disabled
login cannot mint fresh tokens while the user_roles row already refuses the old ones.

A FRESH client is used for sign-in so the cached service-role client
(`app.database.get_client`) is never re-authenticated as the signing-in user.

Team rules (release R7a, 27-Sep-2026) — invite_member / change_access / remove_member are what the
/team routes call, and accept_invite applies the same guards:
  • the role must be one of features.ROLES and every page one of features.FEATURES, inside the
    role's limit (features.ROLE_FEATURE_LIMITS) — 400 otherwise;
  • an owner (settings.owner_emails) keeps their role, pages and status and is never removed or
    re-invited — 409;
  • the last active admin is never demoted, disabled or removed — 409;
  • a role the database's user_roles_role_check does not accept yet (management before
    scripts/r7_rbac_migration.sql) is refused up front with a message naming the migration, before
    any auth user or password is touched — 400;
  • every change leaves a before/after row in shop_admin_audit (entity 'user'), or on audit_log when
    that table cannot take it.
"""
from __future__ import annotations

import logging
import os
import secrets
import string
import time
from datetime import datetime, timezone

from supabase import create_client

from app.config import settings
from app.database import (cached_user_row, get_client, invalidate_user_cache, missing_column_error,
                          must_reset_column_absent, note_must_reset_absent)

log = logging.getLogger(__name__)

# Single source of truth lives in app.features (backend + SPA via GET /auth/features).
from app.features import (FEATURES, ROLE_DEFAULT_FEATURES, ROLE_FEATURE_LIMITS, ROLE_LABELS,  # noqa: F401,E402
                          ROLES, may_hold)


# ── clients / helpers ────────────────────────────────────────────────────────

def _fresh_client():
    """Throwaway client — never the cached service client (sign-in mutates auth state)."""
    return create_client(settings.supabase_url, settings.supabase_key)


def _user_row(email: str) -> dict | None:
    """Cached in app.database so this and get_current_user's fetch_role share ONE query
    per request instead of two. Writes below flush it, so access changes apply at once."""
    return cached_user_row((email or "").strip().lower())


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


# ── team rules: validation, owner protection, the last admin (release R7a) ──────

class TeamChangeRefused(Exception):
    """A team change the rules refuse; `status` is what the route answers: 400 for bad input
    (unknown role or page, a role the database does not accept yet), 404 for nobody by that
    email, 409 for a protected account (an owner, the last active admin)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


STATUSES = ("active", "disabled")
OWNER_LOCKED_MSG = ("This is the owner's account. Its role, pages and status cannot be changed, "
                    "and it cannot be removed or re-invited.")
LAST_ADMIN_MSG = "At least one active admin must remain. Make someone else an admin first."
ROLE_NOT_ENABLED_MSG = ("The {label} role is not switched on in the database yet. "
                        "Apply scripts/r7_rbac_migration.sql, then try again.")

# The roles user_roles_role_check accepted before scripts/r7_rbac_migration.sql (production,
# 27-Sep-2026) — these never need the probe below.
_LEGACY_DB_ROLES = frozenset({"admin", "member", "manager", "viewer", "salesman", "storekeeper"})
_ROLE_CHECK_SQL = ("select pg_get_constraintdef(oid) as def from pg_constraint "
                   "where conrelid = 'public.user_roles'::regclass and conname = 'user_roles_role_check'")
_ROLE_CHECK_TTL_S = 300.0
_role_check: dict = {"at": float("-inf"), "definition": None}


def _norm(email: str | None) -> str:
    return (email or "").strip().lower()


def owner_emails() -> frozenset[str]:
    return frozenset(_norm(e) for e in settings.owner_emails if _norm(e))


def is_owner(email: str | None) -> bool:
    return bool(_norm(email)) and _norm(email) in owner_emails()


def validate_role(role: str | None) -> None:
    if role is not None and role not in ROLES:
        raise TeamChangeRefused(f"Unknown role '{role}'. Choose one of: {', '.join(ROLES)}.")


def validate_features(role: str | None, features) -> list[str]:
    """The page list as stored: every entry one of FEATURES and inside the role's limit."""
    if not isinstance(features, list) or not all(isinstance(f, str) for f in features):
        raise TeamChangeRefused("Pages must be a list of page names.")
    unknown = sorted({f for f in features if f not in FEATURES})
    if unknown:
        raise TeamChangeRefused(f"Unknown page(s): {', '.join(unknown)}.")
    outside = sorted({f for f in features if not may_hold(role, f)})
    if outside:
        allowed = ", ".join(f for f in FEATURES if may_hold(role, f))
        raise TeamChangeRefused(f"{ROLE_LABELS.get(role or '', role)} cannot be given: {', '.join(outside)}. "
                                f"Allowed: {allowed}.")
    return list(dict.fromkeys(features))


def role_enabled_in_db(role: str) -> bool | None:
    """Does user_roles_role_check accept `role`? Read from the live constraint through the
    read-only RPC (yq_readonly may read pg_constraint) and cached for five minutes; None when it
    cannot be read — the write then goes ahead and a CHECK violation is translated instead."""
    if role in _LEGACY_DB_ROLES:
        return True
    now = time.monotonic()
    definition = _role_check["definition"]
    if definition is None or now - _role_check["at"] >= _ROLE_CHECK_TTL_S:
        try:
            from app.db_read import exec_sql
            rows = exec_sql(_ROLE_CHECK_SQL)
        except Exception as e:  # noqa: BLE001 — the RPC is down or not deployed: let the write decide
            log.info("role check probe failed: %s", e)
            return None
        definition = str((rows[0] or {}).get("def") or "") if rows else ""
        _role_check.update(at=now, definition=definition)
    return not definition or f"'{role}'" in definition     # no CHECK at all accepts every role


def ensure_role_enabled(role: str) -> None:
    if role_enabled_in_db(role) is False:
        raise TeamChangeRefused(ROLE_NOT_ENABLED_MSG.format(label=ROLE_LABELS.get(role, role)))


def _role_check_violation(exc: Exception) -> bool:
    text = f"{getattr(exc, 'code', '')} {getattr(exc, 'message', '')} {exc}"
    return "user_roles_role_check" in text


def _not_enabled(role: str | None) -> TeamChangeRefused:
    _role_check.update(at=float("-inf"), definition=None)      # re-read the constraint next time
    return TeamChangeRefused(ROLE_NOT_ENABLED_MSG.format(label=ROLE_LABELS.get(role or "", role)))


def _fresh_row(email: str) -> dict | None:
    """The user_roles row as it is now (the 60 s cache dropped first)."""
    invalidate_user_cache(email)
    return _user_row(email)


def _active_admins() -> set[str]:
    rows = (get_client().table("user_roles").select("email").eq("role", "admin").eq("status", "active")
            .execute().data or [])
    return {_norm(r.get("email")) for r in rows}


def _is_active_admin(row: dict | None) -> bool:
    return bool(row) and row.get("role") == "admin" and (row.get("status") or "active") == "active"


def _moves(before: dict, role=None, features=None, status=None) -> bool:
    if role is not None and role != before.get("role"):
        return True
    if features is not None and sorted(set(features)) != sorted(set(before.get("features") or [])):
        return True
    return status is not None and status != (before.get("status") or "active")


def check_team_change(email: str, before: dict | None, *, role: str | None = None,
                      features: list[str] | None = None, status: str | None = None,
                      remove: bool = False) -> None:
    """Refuse (409) any change to an owner's access, removing an owner, and anything that would
    leave no active admin. `before` is the member's user_roles row as it is now (None = no row)."""
    email = _norm(email)
    if is_owner(email) and (remove or (before is not None and _moves(before, role, features, status))):
        raise TeamChangeRefused(OWNER_LOCKED_MSG, 409)
    if not _is_active_admin(before):
        return
    stays = (not remove and (role if role is not None else before.get("role")) == "admin"
             and (status if status is not None else before.get("status") or "active") == "active")
    if not stays and not (_active_admins() - {email}):
        raise TeamChangeRefused(LAST_ADMIN_MSG, 409)


_AUDIT_KEYS = ("email", "role", "features", "status", "full_name", "must_reset")


def _audit_view(row: dict | None) -> dict | None:
    return {k: row.get(k) for k in _AUDIT_KEYS if k in row} if row else None


def audit_team(actor: str, email: str, action: str, before: dict | None, after: dict | None) -> None:
    """One before/after row per team change in shop_admin_audit (entity 'user'); when that table
    cannot take it (not migrated, down) the same facts go to audit_log. Never raises. A password,
    a temporary password or an invite token is never part of either side."""
    b, a = _audit_view(before), _audit_view(after)
    if action == "update" and b == a:
        return
    from app import shop_audit
    if shop_audit.record(actor, "user", _norm(email), action, b, a):
        return
    from app.audit import log_event
    log_event(actor, "team.audit", detail={"email": _norm(email), "action": action, "before": b, "after": a})


def invite_member(actor: str, email: str, full_name: str, role: str, features: list[str],
                  method: str = "temp") -> dict:
    """POST /team/invite. Admins implicitly get every page; everyone else exactly what was picked,
    inside their role's limit. Guards run before any auth user, password or invite is touched."""
    email = _norm(email)
    if "@" not in email:
        raise TeamChangeRefused("Enter a valid email.")
    if method not in ("temp", "email"):
        raise TeamChangeRefused("Choose a temporary password or an email invite.")
    validate_role(role)
    grant = list(FEATURES) if role == "admin" else validate_features(role, features)
    before = _fresh_row(email)
    if before is not None and is_owner(email):
        raise TeamChangeRefused(OWNER_LOCKED_MSG, 409)        # an invite resets the password
    check_team_change(email, before, role=role, features=grant, status="active")
    ensure_role_enabled(role)
    action = "update" if before else "create"
    if method == "email":
        res = create_email_invite(email, full_name, role, grant, invited_by=actor)
        audit_team(actor, email, action, before, {"email": email, "role": role, "features": grant,
                                                  "status": "invited", "full_name": full_name})
        return {"mode": "email", **res}
    tmp = generate_temp_password()
    create_member(email, full_name, role, grant, tmp, invited_by=actor, must_reset=True)
    audit_team(actor, email, action, before, _fresh_row(email))
    return {"mode": "temp", "email": email, "temp_password": tmp}


def change_access(actor: str, email: str, role: str | None = None, features: list[str] | None = None,
                  status: str | None = None) -> dict | None:
    """PATCH /team/{email}. A new role without a page list keeps the pages it may hold (the
    role's defaults when none are left). Returns the row after the change."""
    email = _norm(email)
    validate_role(role)
    if status is not None and status not in STATUSES:
        raise TeamChangeRefused(f"Unknown status '{status}'. Choose active or disabled.")
    before = _fresh_row(email)
    if before is None:
        raise TeamChangeRefused("No team member with that email.", 404)
    new_role = role if role is not None else before.get("role")
    if features is not None:
        features = validate_features(new_role, features)
    elif role is not None and role != before.get("role") and role in ROLE_FEATURE_LIMITS:
        kept = [f for f in (before.get("features") or []) if may_hold(role, f)]
        features = kept or list(ROLE_DEFAULT_FEATURES.get(role, []))
    check_team_change(email, before, role=role, features=features, status=status)
    if role is not None and role != before.get("role"):
        ensure_role_enabled(role)
    update_access(email, role=role, features=features, status=status)
    after = _fresh_row(email)
    audit_team(actor, email, "update", before, after)
    return after


def remove_member(actor: str, email: str) -> None:
    """DELETE /team/{email}: never yourself, never an owner, never the last active admin."""
    email = _norm(email)
    if email == _norm(actor):
        raise TeamChangeRefused("You cannot remove your own account.")
    before = _fresh_row(email)
    check_team_change(email, before, remove=True)
    remove_user(email)
    audit_team(actor, email, "delete", before, None)


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
    # the server-owned flag when the column exists, else the metadata hint (display only)
    must_reset = row["must_reset"] if row.get("must_reset") is not None else meta.get("must_reset")
    return {
        "email": email,
        "role": row.get("role", "member"),
        "features": row.get("features") or [],
        "full_name": row.get("full_name") or meta.get("full_name") or "",
        "must_reset": bool(must_reset),
    }


# ── user provisioning ────────────────────────────────────────────────────────

def _missing_column(exc: Exception, column: str) -> bool:
    """A SELECT says 42703; an INSERT/UPDATE payload naming an unknown column gets PostgREST's
    PGRST204 'Could not find the ... column ... in the schema cache'. Both mean 'not migrated
    yet' (app.database.missing_column_error)."""
    return missing_column_error(exc, column)


def _write_role_row(row: dict, *, insert: bool) -> None:
    """Insert or update a user_roles row; when the DB predates the must_reset column, leave it
    out (known absent) or retry without it on PGRST204 / 42703 — the API deploys before
    user_roles_must_reset_migration.sql, and /team/invite + /team/accept must keep working."""
    client = get_client()

    def _go(payload: dict) -> None:
        if insert:
            client.table("user_roles").insert(payload).execute()
        else:
            client.table("user_roles").update(payload).eq("email", payload["email"]).execute()

    if "must_reset" in row and must_reset_column_absent():
        _go({k: v for k, v in row.items() if k != "must_reset"})
        return
    try:
        _go(row)
    except Exception as exc:  # noqa: BLE001
        if "must_reset" not in row or not _missing_column(exc, "must_reset"):
            raise
        note_must_reset_absent()
        _go({k: v for k, v in row.items() if k != "must_reset"})


def _upsert_role(email: str, role: str, features: list[str],
                 full_name: str = "", invited_by: str = "", status: str = "active",
                 must_reset: bool | None = None) -> None:
    email = email.strip().lower()
    row: dict = {"email": email, "role": role, "features": features, "status": status}
    if full_name:
        row["full_name"] = full_name
    if invited_by:
        row["invited_by"] = invited_by
    if must_reset is not None:
        row["must_reset"] = bool(must_reset)
    try:
        _write_role_row(row, insert=not _user_row(email))
    except Exception as exc:  # noqa: BLE001
        if _role_check_violation(exc):
            raise _not_enabled(role) from exc
        raise
    invalidate_user_cache(email)


def _clear_must_reset(email: str) -> None:
    """user_roles.must_reset = false (no-op before the migration adds the column)."""
    email = email.strip().lower()
    if must_reset_column_absent():
        invalidate_user_cache(email)
        return
    try:
        get_client().table("user_roles").update({"must_reset": False}).eq("email", email).execute()
    except Exception as exc:  # noqa: BLE001
        if _missing_column(exc, "must_reset"):
            note_must_reset_absent()
        else:
            log.warning("clear must_reset failed for %s: %s", email, exc)
    invalidate_user_cache(email)


def check_password(email: str, password: str) -> bool:
    """True when `password` signs `email` in (a fresh client, never the cached service client).
    POST /auth/password uses it so a stolen access token alone cannot change the password."""
    email = (email or "").strip().lower()
    if not email or not password:
        return False
    try:
        sess = _fresh_client().auth.sign_in_with_password({"email": email, "password": password})
    except Exception:  # noqa: BLE001 — wrong password, banned, network
        return False
    return bool(sess and getattr(sess, "session", None) and sess.session.access_token)


def create_member(email: str, full_name: str, role: str, features: list[str],
                  password: str, invited_by: str = "", must_reset: bool = True) -> dict:
    """Create (or update) a Supabase Auth user + their user_roles row. A re-invited member whose
    auth user was BANNED when they were disabled (update_access) is unbanned in the same call,
    else the row says active while Supabase still refuses the sign-in."""
    email = email.strip().lower()
    if role not in ROLES:
        role = "member"
    meta = {"must_reset": must_reset, "full_name": full_name}
    client = get_client()
    existing = _find_auth_user(email)
    if existing:
        client.auth.admin.update_user_by_id(
            existing.id, {"password": password, "user_metadata": meta, "ban_duration": "none"})
    else:
        client.auth.admin.create_user(
            {"email": email, "password": password, "email_confirm": True, "user_metadata": meta}
        )
    _upsert_role(email, role, features, full_name, invited_by, status="active", must_reset=must_reset)
    return {"email": email, "role": role, "features": features, "full_name": full_name}


def set_password(email: str, password: str) -> bool:
    """Set a user's password and clear must_reset — the only path that clears the server-owned
    flag (POST /auth/password for the member themself; an admin force-reset)."""
    u = _find_auth_user(email)
    if not u:
        return False
    meta = dict(getattr(u, "user_metadata", None) or {})
    meta["must_reset"] = False
    get_client().auth.admin.update_user_by_id(u.id, {"password": password, "user_metadata": meta})
    _clear_must_reset(email)
    return True


# Supabase has no "banned forever"; a century is the documented way to spell it.
_BAN_FOREVER = "876000h"


def set_auth_ban(email: str, banned: bool) -> bool:
    """Ban (or unban) the Supabase auth user so a disabled member cannot sign in again or refresh
    a session. Returns False when the auth user is missing or the call fails — the user_roles
    status is the gate the API enforces either way."""
    u = _find_auth_user(email)
    if not u:
        return False
    try:
        get_client().auth.admin.update_user_by_id(
            u.id, {"ban_duration": _BAN_FOREVER if banned else "none"})
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("auth %s failed for %s: %s", "ban" if banned else "unban", email, e)
        return False


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
        try:
            get_client().table("user_roles").update(upd).eq("email", email.strip().lower()).execute()
        except Exception as exc:  # noqa: BLE001
            if _role_check_violation(exc):
                raise _not_enabled(role) from exc
            raise
        invalidate_user_cache(email)
    if status is not None:
        set_auth_ban(email, banned=(status != "active"))


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
    invalidate_user_cache(email)


def list_members() -> dict:
    """Return {'users': [...active accounts...], 'invites': [...pending invites...]}."""
    client = get_client()
    users = client.table("user_roles").select(
        "email,role,features,status,full_name"
    ).order("role").execute().data or []
    for u in users:
        u["is_owner"] = is_owner(u.get("email"))     # the Team page shows "Owner" and no Edit
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
    """Member sets their password → create the account + mark invite accepted. The team rules
    apply here too (an owner's row, the last active admin, a role the database does not accept
    yet): TeamChangeRefused, and the invite stays pending."""
    inv = get_invite(token)
    if not inv:
        return None
    email = inv["email"].strip().lower()
    name = full_name or inv.get("full_name") or ""
    role = inv["role"] if inv.get("role") in ROLES else "member"      # create_member's own fallback
    features = inv.get("features") or []
    before = _fresh_row(email)
    if before is not None and is_owner(email):
        raise TeamChangeRefused(OWNER_LOCKED_MSG, 409)
    check_team_change(email, before, role=role, features=features, status="active")
    ensure_role_enabled(role)
    create_member(email, name, role, features, password, invited_by=inv.get("invited_by", ""), must_reset=False)
    get_client().table("app_invites").update({
        "status": "accepted", "accepted_at": datetime.now(timezone.utc).isoformat()
    }).eq("token", token).execute()
    audit_team(email, email, "update" if before else "create", before, _fresh_row(email))
    return {"email": email, "role": role, "features": features, "full_name": name}


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
