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
        return metrics.overview(period)

    @app.get("/management/attention")
    def management_attention(user: CurrentUser = Depends(get_current_user)) -> dict:
        """The needs-attention exceptions (current state, not tied to a period)."""
        _require_command(user)
        return metrics.attention()

    @app.get("/freshness")
    def data_freshness_chip(user: CurrentUser = Depends(get_current_user)) -> dict:
        """The portal header's data-freshness chip ("Focus data to 24 Sep · Marketplace live")."""
        return metrics.freshness(show_match=user.role in metrics.COMMAND_ROLES)
