"""Single source of truth for grantable feature pages and roles.

Consumed by: user_auth (validation), auth.require_feature (API gates), GET /auth/features
(the Team page builds its chips from this — never re-hardcode the list in the SPA), and
the nav/Gate strings in web/src must match these names exactly.

Adding a page = append here + gate the route in the SPA. "Team"/"Data" are admin-only
surfaces and intentionally not grantable features.
"""
from __future__ import annotations

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
]

ROLES: list[str] = ["admin", "member", "salesman"]

# Default grants offered at invite time (admin implicitly has everything).
ROLE_DEFAULT_FEATURES: dict[str, list[str]] = {
    "member": ["Dashboard", "Sales", "Inventory", "Receivables"],
    "salesman": ["Catalog", "Product Finds"],
}
