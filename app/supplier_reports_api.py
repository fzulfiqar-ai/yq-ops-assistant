"""Monthly supplier workbook routes (Market Intel v2, Phase 2). Registered from app/main.py:

    from app.supplier_reports_api import register; register(app, limiter)

Every route is behind the 'Market Intel' page (require_feature) and, inside it, open to the office
and management only (app/supplier_reports.py holds the rules):

    POST  /market-intel/supplier-reports                       reviewers: build the month's draft
                                                               {supplier_id, month: "YYYY-MM", edition}
                                                               (an existing draft is rebuilt in place)
    GET   /market-intel/supplier-reports                       reviewers + management: the list
                                                               ?supplier_id=&month=&edition=&status=&offset=&limit=
    GET   /market-intel/supplier-reports/{report_id}           reviewers + management: the preview
                                                               (counts, narrative, every candidate row)
    PATCH /market-intel/supplier-reports/{report_id}           reviewers, a draft only:
                                                               {narrative?, included_observation_ids?}
    POST  /market-intel/supplier-reports/{report_id}/approve   approvers: draft -> approved; the earlier
                                                               approved report of the same supplier,
                                                               month and edition -> superseded
    GET   /market-intel/supplier-reports/{report_id}/download  reviewers + management, approved only:
                                                               the .xlsx; {at, by, sha256} is logged

A salesman (or a storekeeper holding the page) gets 403 on every one. Management is read-only
centrally (app/auth.py): the GETs work; the approve route works once its template
(supplier_reports.APPROVE_ROUTE) is on auth.READ_ONLY_WRITE_ALLOWLIST. Before
scripts/r8m_market_quality_migration.sql every route answers 503 with the hint. Nothing is e-mailed.

No `from __future__ import annotations`: the request models are defined inside register(), and
FastAPI must see real annotation objects to bind bodies.
"""
import logging

log = logging.getLogger(__name__)

FEATURE = "Market Intel"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def register(app, limiter) -> None:
    from fastapi import Depends, HTTPException, Request
    from fastapi.responses import Response
    from pydantic import BaseModel, Field

    from app import supplier_reports as sr
    from app.audit import log_event
    from app.auth import CurrentUser, read_only_refuses, require_feature
    from app.features import is_read_only

    gate = require_feature(FEATURE)

    def _fail(e: "sr.SupplierReportError"):
        raise HTTPException(status_code=e.status, detail=str(e))

    def _reader(user: CurrentUser) -> None:
        """Reviewers and management; a rep never reaches a supplier report."""
        if user.role not in sr.APPROVE_ROLES:
            raise HTTPException(status_code=403, detail="Supplier reports are for the office and management.")

    def _reviewer(user: CurrentUser) -> None:
        if user.role not in sr.REVIEW_ROLES:
            raise HTTPException(status_code=403, detail="Only the office can build or edit supplier reports.")

    def _caps(user: CurrentUser) -> dict:
        approve_path = sr.APPROVE_ROUTE.replace("{report_id}", "1")
        can_approve = user.role in sr.APPROVE_ROLES and not (
            is_read_only(user.role) and read_only_refuses("POST", approve_path))
        return {"can_build": user.role in sr.REVIEW_ROLES, "can_edit": user.role in sr.REVIEW_ROLES,
                "can_approve": can_approve, "can_download": user.role in sr.APPROVE_ROLES}

    # ── models ────────────────────────────────────────────────────────────────
    class BuildRequest(BaseModel):
        supplier_id: int
        month: str = Field(max_length=10)
        edition: str = Field(default="supplier", max_length=10)

    class ReportPatch(BaseModel):
        narrative: str | None = Field(default=None, max_length=sr.MAX_NARRATIVE)
        included_observation_ids: list[int] | None = Field(default=None, max_length=5000)

    # ── build / list / preview ────────────────────────────────────────────────
    @app.post("/market-intel/supplier-reports")
    @limiter.limit("10/minute")
    def supplier_report_build(request: Request, body: BuildRequest, user: CurrentUser = Depends(gate)) -> dict:
        _reviewer(user)
        try:
            out = sr.build_draft(body.supplier_id, body.month, body.edition, user.email, user.role)
        except sr.SupplierReportError as e:
            _fail(e)
        rep = out["report"]
        log_event(user.email, "market_intel.supplier_report.build",
                  detail={"report_id": rep.get("id"), "supplier_id": body.supplier_id, "month": rep.get("month"),
                          "edition": rep.get("edition"), "rebuilt": out.get("rebuilt"),
                          "reports": (rep.get("counts") or {}).get("reports")})
        return {**out, "capabilities": _caps(user)}

    @app.get("/market-intel/supplier-reports")
    def supplier_report_list(supplier_id: int | None = None, month: str | None = None, edition: str | None = None,
                             status: str | None = None, offset: int = 0, limit: int = 50,
                             user: CurrentUser = Depends(gate)) -> dict:
        _reader(user)
        try:
            out = sr.list_reports(user.role, supplier_id=supplier_id, month=month, edition=edition, status=status,
                                  offset=offset, limit=limit)
        except sr.SupplierReportError as e:
            _fail(e)
        return {**out, "capabilities": _caps(user)}

    @app.get("/market-intel/supplier-reports/{report_id}")
    def supplier_report_get(report_id: int, user: CurrentUser = Depends(gate)) -> dict:
        _reader(user)
        try:
            out = sr.get_report(report_id, user.role)
        except sr.SupplierReportError as e:
            _fail(e)
        return {**out, "capabilities": _caps(user)}

    # ── edit / approve / download ─────────────────────────────────────────────
    @app.patch("/market-intel/supplier-reports/{report_id}")
    def supplier_report_edit(report_id: int, body: ReportPatch, user: CurrentUser = Depends(gate)) -> dict:
        _reviewer(user)
        changes = body.model_dump(exclude_unset=True)
        try:
            out = sr.update_draft(report_id, changes, user.email, user.role)
        except sr.SupplierReportError as e:
            _fail(e)
        log_event(user.email, "market_intel.supplier_report.edit",
                  detail={"report_id": report_id, "fields": sorted(changes),
                          "included": len(out["report"].get("included_observation_ids") or [])})
        return {**out, "capabilities": _caps(user)}

    @app.post(sr.APPROVE_ROUTE)
    def supplier_report_approve(report_id: int, user: CurrentUser = Depends(gate)) -> dict:
        _reader(user)
        try:
            out = sr.approve(report_id, user.email, user.role)
        except sr.SupplierReportError as e:
            _fail(e)
        log_event(user.email, "market_intel.supplier_report.approve",
                  detail={"report_id": report_id, "superseded": out.get("superseded")})
        return out

    @app.get("/market-intel/supplier-reports/{report_id}/download")
    @limiter.limit("10/minute")
    def supplier_report_download(request: Request, report_id: int, user: CurrentUser = Depends(gate)) -> Response:
        _reader(user)
        try:
            got = sr.download(report_id, user.email, user.role)
        except sr.SupplierReportError as e:
            _fail(e)
        log_event(user.email, "market_intel.supplier_report.download",
                  detail={"report_id": report_id, "sha256": got["sha256"], "bytes": got["bytes"],
                          "edition": got["edition"], "month": got["month"]})
        return Response(content=got["data"], media_type=XLSX,
                        headers={"Content-Disposition": f'attachment; filename="{got["filename"]}"',
                                 "Cache-Control": "no-store"})
