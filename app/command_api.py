"""Management Command Centre routes (release R7b, plan §8). Registered onto the main app by app.main:

    from app.command_api import register; register(app)

  GET /management/overview?period=today|7d|mtd|last_month|quarter  (admin + management)
      freshness {focus_to, marketplace_live, match_rate} + the modules in the plan's order
  GET /management/attention                                         (admin + management)
      the needs-attention exceptions alone
  GET /freshness                                                    (any portal login)
      the header chip: Focus data date and whether the marketplace answered; the invoice links
      (match_rate: delivered orders whose Focus invoice link a person ACCEPTED, "confirmed x of y" —
      a suggestion awaiting acceptance does not count) only for admin and management

Every figure comes from app/metrics.py (the metric dictionary): marketplace money ex-VAT, units the
shop asked for, the company target read as ex-VAT Accessories on business days. The routes only
gate and pick the period. They are GETs, so the read-only management login passes the central gate
in app.auth; the role check sits in the route (like GET /report/{key}) so the route table in
tests/test_r1_security.py reads it as a plain "user" dependency.
"""
# NOTE: no `from __future__ import annotations` — FastAPI must see real annotation objects.
import logging

log = logging.getLogger(__name__)

# ── least privilege on a shared payload ────────────────────────────────────────
# metrics.overview()/attention() are built once per period and shared by every caller (cached), so
# what a login may not read is taken out per request, on copies — never in the cached dicts.
# Cost, margin, gross profit, the below-cost list and stock AT COST: admins and logins holding
# 'Margins'. The receivables book (balances by account): admins and logins holding 'Receivables'.
MODULE_FEATURE: dict[str, str] = {"profitability": "Margins", "receivables": "Receivables"}
TILE_FEATURE: dict[str, str] = {"products.stock_shape": "Margins"}          # stock valued at cost
ATTENTION_FEATURE: dict[str, str] = {"below_cost": "Margins", "unowned_ar": "Receivables"}
GUARDED_FEATURES = frozenset(MODULE_FEATURE.values()) | frozenset(TILE_FEATURE.values()) |     frozenset(ATTENTION_FEATURE.values())


def granted(user) -> frozenset[str] | None:
    """The guarded features this login holds; None = everything (admin)."""
    from app.auth import has_feature
    if getattr(user, "role", "") == "admin":
        return None
    return frozenset(f for f in GUARDED_FEATURES if has_feature(user, f))


def _restricted_tile(t: dict, feature: str) -> dict:
    """The tile's place, with no figure: only its name and why it is empty."""
    return {"key": t.get("key"), "label": t.get("label"), "unit": t.get("unit"), "drill": None,
            "available": False, "value": None, "restricted": feature,
            "basis": f"Shown to logins with the {feature} page.",
            "note": f"Needs the {feature} page — an admin can grant it on the Team page."}


def scope_items(items: list[dict] | None, allowed: frozenset[str] | None) -> list[dict]:
    if allowed is None:
        return list(items or [])
    return [i for i in items or [] if ATTENTION_FEATURE.get(i.get("key"), "") in allowed | {""}]


def scope_overview(payload: dict, allowed: frozenset[str] | None) -> dict:
    """The overview a login may read: modules, tiles and attention items it lacks the page for are
    left out (a stock-at-cost tile keeps its place, marked restricted). A new dict; the input (the
    shared cached payload) is never changed."""
    if allowed is None or not isinstance(payload, dict) or "modules" not in payload:
        return payload
    mods = []
    for m in payload.get("modules") or []:
        need = MODULE_FEATURE.get(m.get("key"))
        if need and need not in allowed:
            continue
        m = dict(m)
        if m.get("key") == "attention":
            m["items"] = scope_items(m.get("items"), allowed)
            m["all_clear"] = not m["items"]
        m["tiles"] = [(_restricted_tile(t, TILE_FEATURE[t.get("key")])
                       if TILE_FEATURE.get(t.get("key")) and TILE_FEATURE[t.get("key")] not in allowed else t)
                      for t in m.get("tiles") or []]
        mods.append(m)
    return {**payload, "modules": mods}


def scope_attention(payload: dict, allowed: frozenset[str] | None) -> dict:
    if allowed is None or not isinstance(payload, dict) or "items" not in payload:
        return payload
    return {**payload, "items": scope_items(payload.get("items"), allowed)}


def register(app) -> None:
    from fastapi import Depends, HTTPException

    from app import metrics
    from app.auth import CurrentUser, get_current_user

    def _require_command(user: CurrentUser) -> None:
        if user.role not in metrics.COMMAND_ROLES:
            raise HTTPException(status_code=403, detail="The Command Centre is for admin and management logins.")

    @app.get("/management/overview")
    def management_overview(period: str = metrics.DEFAULT_PERIOD,
                            user: CurrentUser = Depends(get_current_user)) -> dict:
        """The Command Centre: every module for one period, each tile labelled with its basis.
        Focus figures are cached until the next data upload; marketplace figures for a minute."""
        _require_command(user)
        if period not in metrics.PERIODS:
            raise HTTPException(status_code=400,
                                detail=f"Unknown period '{period}'. Use one of: {', '.join(metrics.PERIODS)}.")
        return scope_overview(metrics.overview(period), granted(user))

    @app.get("/management/attention")
    def management_attention(user: CurrentUser = Depends(get_current_user)) -> dict:
        """The needs-attention exceptions (current state, not tied to a period)."""
        _require_command(user)
        return scope_attention(metrics.attention(), granted(user))

    @app.get("/freshness")
    def data_freshness_chip(user: CurrentUser = Depends(get_current_user)) -> dict:
        """The portal header's data-freshness chip ("Focus data to 24 Sep · Marketplace live")."""
        return metrics.freshness(show_match=user.role in metrics.COMMAND_ROLES)
