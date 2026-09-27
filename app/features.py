"""Single source of truth for grantable feature pages and roles.

Consumed by: user_auth (validation), auth.require_feature (API gates), GET /auth/features
(the Team page builds its chips from this — never re-hardcode the list in the SPA), and
the nav/Gate strings in web/src must match these names exactly.

Adding a page = append here + gate the route in the SPA. "Team"/"Data" are admin-only
surfaces and intentionally not grantable features.
"""
from __future__ import annotations

import re

FEATURES: list[str] = [
    "Dashboard",
    "Live Feed",
    "AI Agents",
    "AI Assistant",
    "Inventory",
    "Orders",
    "Stock Movement",
    "Sales",
    "Leads",
    "Margins",
    "Receivables",
    "Catalog",
    "Product Finds",
    "Marketing",
    "Shop Orders",
    "Shop Admin",
    "Storekeeper",     # the warehouse pick list: confirmed marketplace orders grouped by salesman
]

ROLES: list[str] = ["admin", "member", "salesman", "storekeeper", "management"]

# What the Team page shows for each role.
ROLE_LABELS: dict[str, str] = {
    "admin": "Admin", "member": "Member", "salesman": "Salesman", "storekeeper": "Storekeeper",
    "management": "Management",
}

# Allowed by user_roles_role_check once scripts/r7_rbac_migration.sql has run, but not offered
# yet: the capability layer (plan §7) switches them on when someone holds one. Until then the
# team API refuses them like any unknown role.
RESERVED_ROLES: tuple[str, ...] = ("operations", "sales_manager", "finance")

# Default grants offered at invite time (admin implicitly has everything).
ROLE_DEFAULT_FEATURES: dict[str, list[str]] = {
    "member": ["Dashboard", "Sales", "Inventory", "Receivables"],
    "salesman": ["Catalog", "Shop Orders"],   # the two-tab salesman app; Product Finds stays grantable
    "storekeeper": ["Storekeeper"],           # marks orders Preparing / On the way, nothing else
    # company-wide reports and every marketplace order, read only (release R7a, stream B5)
    "management": ["Dashboard", "Sales", "Margins", "Receivables", "Inventory", "Shop Orders"],
}

# ── Management (release R7a): company-wide READ, no writes anywhere ──────────────
# app.auth.get_current_user refuses every non-GET request from a read-only role except
# auth.READ_ONLY_WRITE_ALLOWLIST (the login's own password).
READ_ONLY_ROLES: frozenset[str] = frozenset({"management"})
# The ONLY pages a role may hold, whatever its user_roles row lists (a role not named here has
# no limit). Management reads the company's reports and marketplace orders: never the AI tools
# (agents, free-text SQL), Shop Admin (offers, reps, settings), Marketing or Leads (unmasked
# customer phones), and Team / Data / Settings / statements are admin-only already.
ROLE_FEATURE_LIMITS: dict[str, frozenset[str]] = {
    "management": frozenset(ROLE_DEFAULT_FEATURES["management"]),
}
# Roles that see a merchant's phone and email masked in order payloads (plan §7 phones.full).
CONTACT_MASKED_ROLES: frozenset[str] = frozenset({"management"})
# Roles that never receive a merchant's phone or email at all, not even masked (release R7b): the
# storekeeper packs and hands over goods, so the order's lines and shop name are all it needs.
CONTACT_STRIPPED_ROLES: frozenset[str] = frozenset({"storekeeper"})


def is_read_only(role: str | None) -> bool:
    return (role or "") in READ_ONLY_ROLES


def may_hold(role: str | None, feature: str) -> bool:
    """False when `feature` is outside the role's limit (ROLE_FEATURE_LIMITS)."""
    limit = ROLE_FEATURE_LIMITS.get(role or "")
    return limit is None or feature in limit


def masks_contacts(role: str | None) -> bool:
    return (role or "") in CONTACT_MASKED_ROLES


def strips_contacts(role: str | None) -> bool:
    return (role or "") in CONTACT_STRIPPED_ROLES


MASK = "•••••"


def mask_phone(phone: str | None) -> str | None:
    """'+973 3312 3456' → '+973 ••••• 456'; a number without the country code keeps only its
    last three digits ('••••• 456'). Too short to keep anything → '•••••'."""
    if not phone:
        return phone
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) < 6:
        return MASK
    prefix = "+973 " if digits.startswith("973") and len(digits) > 8 else ""
    return f"{prefix}{MASK} {digits[-3:]}"


def mask_email(email: str | None) -> str | None:
    """'orders@example.com' → 'o•••••@example.com' (the domain stays: it says which company)."""
    if not email:
        return email
    local, at, domain = str(email).partition("@")
    if not at:
        return MASK
    return f"{local[:1]}{MASK}@{domain}"
