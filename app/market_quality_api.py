"""Market Intel v2 routes (release R8m, plan Part F, Phase 1): quality cases, the admin lists and the
product picker. Registered from app/main.py right after app/market_intel_api.py:

    from app.market_quality_api import register; register(app, limiter)

Every route is behind the 'Market Intel' page (require_feature). Inside it (app/market_quality.py):

    GET   /market-intel/lists                          what the capture sheet and the Lists page read
                                                       (?all=1: an admin also gets inactive / merged rows)
    POST  /market-intel/lists/{kind}                   admin: a new supplier / brand / issue type
                                                       (kind = suppliers | brands | issue-types)
    PATCH /market-intel/lists/{kind}/{entry_id}        admin: edit, deactivate (active), merge (merged_into)
    GET   /market-intel/products                       the product picker (?q=&shop_id=&shop_phone=&limit=20)
    GET   /market-intel/cases                          office: one tab of cases + the tab counts
                                                       (?tab=&status=&supplier_id=&brand_id=&issue_type_id=
                                                        &from=&to=&offset=&limit=)
    GET   /market-intel/cases/{case_id}                office: one case (?raw=1: an admin sees the original of
                                                       a people-flagged photo; audited)
    PATCH /market-intel/cases/{case_id}                reviewers: fields, evidence (up only), one status move
    POST  /market-intel/cases/{case_id}/question       reviewers: ask the rep for more
    POST  /market-intel/observations/{obs_id}/answer   the rep who made the sighting: his answer (multipart:
                                                       text, optional photo, optional role)
    POST  /market-intel/cases/{case_id}/merge          reviewers: this case duplicates another (into_id)
    POST  /market-intel/observations/{obs_id}/move     reviewers: one sighting to another case
    POST  /market-intel/cases/{case_id}/action         reviewers: ask the supplier to act (a verified case)
    PATCH /market-intel/actions/{action_id}            reviewers: the supplier's response, YQ's check, status

Before scripts/r8m_market_quality_migration.sql every route but the picker answers 503 with the hint.
Management reads the cases with phones and e-mails masked (the storekeeper too) and its writes are refused
centrally (app/auth.py). A safety alert e-mail runs after the answer (BackgroundTasks). Every write leaves
an audit_log row (log_event) without the free text.

No `from __future__ import annotations`: the request models are defined inside register(), and FastAPI
must see real annotation objects to bind bodies.
"""
import logging

log = logging.getLogger(__name__)

FEATURE = "Market Intel"


def register(app, limiter) -> None:  # noqa: C901 — one registration function, many small routes
    from fastapi import BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile
    from pydantic import BaseModel, Field
    from starlette.concurrency import run_in_threadpool

    from app import market_intel as mi
    from app import market_quality as mq
    from app.audit import log_event
    from app.auth import CurrentUser, require_feature
    from app.features import is_read_only, masks_contacts, strips_contacts
    from app.uploads import MAX_PHOTO_BYTES, UploadTooLarge, read_capped

    gate = require_feature(FEATURE)

    def _office(user: CurrentUser) -> bool:
        return user.role != "salesman"

    def _me(user: CurrentUser) -> str:
        return (user.email or "").strip().lower()

    def _mask(user: CurrentUser) -> bool:
        """Management reads free text masked; the storekeeper never receives a phone or an e-mail either."""
        return masks_contacts(user.role) or strips_contacts(user.role)

    def _caps(user: CurrentUser) -> dict:
        return {"office": _office(user), "can_review": user.role in mi.REVIEW_ROLES,
                "can_approve": user.role in mi.APPROVE_ROLES, "can_capture": not is_read_only(user.role),
                "can_admin": user.role == "admin"}

    def _fail(e: "mi.MarketIntelError"):
        raise HTTPException(status_code=e.status, detail=str(e))

    def _office_only(user: CurrentUser) -> None:
        if not _office(user):
            raise HTTPException(status_code=403, detail="Quality cases are for the office.")

    # ── models ────────────────────────────────────────────────────────────────
    class ListEntry(BaseModel):
        name: str | None = Field(default=None, max_length=200)
        code: str | None = Field(default=None, max_length=60)
        active: bool | None = None
        supplier_id: int | None = None                 # brands
        aliases: list[str] | None = Field(default=None, max_length=40)
        merged_into: int | None = None                 # suppliers, brands (null = unmerge)
        kind: str | None = Field(default=None, max_length=20)          # issue types
        label_en: str | None = Field(default=None, max_length=120)
        label_ar: str | None = Field(default=None, max_length=120)
        safety_default: bool | None = None
        fields: list[str] | None = Field(default=None, max_length=10)
        sort: int | None = None

    class CaseUpdate(BaseModel):
        status: str | None = Field(default=None, max_length=30)
        reason: str | None = Field(default=None, max_length=600)
        verification_method: str | None = Field(default=None, max_length=30)
        verification_reason: str | None = Field(default=None, max_length=600)
        evidence_level: int | None = None
        severity: int | None = None
        assignee: str | None = Field(default=None, max_length=160)
        due_date: str | None = Field(default=None, max_length=20)
        safety: bool | None = None
        title: str | None = Field(default=None, max_length=200)
        yq_item_code: str | None = Field(default=None, max_length=64)
        brand_id: int | None = None
        supplier_id: int | None = None

    class Question(BaseModel):
        text: str = Field(max_length=600)
        observation_id: int | None = None              # only that sighting's rep is asked (else every rep on it)

    class MergeRequest(BaseModel):
        into_id: int

    class MoveRequest(BaseModel):
        case_id: int

    class ActionRequest(BaseModel):
        request: str = Field(max_length=1200)
        owner: str | None = Field(default=None, max_length=160)
        due_date: str | None = Field(default=None, max_length=20)
        supplier_id: int | None = None

    class ActionUpdate(BaseModel):
        supplier_response: str | None = Field(default=None, max_length=2400)
        resolution_check: str | None = Field(default=None, max_length=1200)
        status: str | None = Field(default=None, max_length=20)

    # ── the lists ─────────────────────────────────────────────────────────────
    @app.get("/market-intel/lists")
    def market_intel_lists(all_: bool = Query(default=False, alias="all"), user: CurrentUser = Depends(gate)) -> dict:
        try:
            out = mq.lists(all_=bool(all_ and user.role == "admin"))
        except mi.MarketIntelError as e:
            _fail(e)
        return {**out, "capabilities": _caps(user)}

    @app.post("/market-intel/lists/{kind}")
    def market_intel_list_create(kind: str, body: ListEntry, user: CurrentUser = Depends(gate)) -> dict:
        try:
            out = mq.create_entry(kind, body.model_dump(exclude_unset=True), user.email, user.role)
        except mi.MarketIntelError as e:
            _fail(e)
        log_event(user.email, "market_intel.list_create", detail={"list": kind, "id": out["entry"].get("id")})
        return out

    @app.patch("/market-intel/lists/{kind}/{entry_id}")
    def market_intel_list_update(kind: str, entry_id: int, body: ListEntry, user: CurrentUser = Depends(gate)) -> dict:
        try:
            out = mq.update_entry(kind, entry_id, body.model_dump(exclude_unset=True), user.email, user.role)
        except mi.MarketIntelError as e:
            _fail(e)
        log_event(user.email, "market_intel.list_update",
                  detail={"list": kind, "id": entry_id, "fields": out.get("fields", []), "changed": out.get("changed")})
        return out

    # ── the product picker ────────────────────────────────────────────────────
    @app.get("/market-intel/products")
    def market_intel_products(q: str | None = None, shop_id: int | None = None, shop_phone: str | None = None,
                              limit: int = 20, user: CurrentUser = Depends(gate)) -> list:
        return mq.products((q or "")[:80], shop_id=shop_id, shop_phone=(shop_phone or "")[:40] or None, limit=limit)

    # ── cases ─────────────────────────────────────────────────────────────────
    @app.get("/market-intel/cases")
    def market_intel_cases(tab: str = "all", status: str | None = None, supplier_id: int | None = None,
                           brand_id: int | None = None, issue_type_id: int | None = None,
                           date_from: str | None = Query(default=None, alias="from", max_length=20),
                           date_to: str | None = Query(default=None, alias="to", max_length=20),
                           offset: int = 0, limit: int = 50, user: CurrentUser = Depends(gate)) -> dict:
        _office_only(user)
        try:
            out = mq.cases(tab=tab, status=status, supplier_id=supplier_id, brand_id=brand_id,
                           issue_type_id=issue_type_id, date_from=date_from, date_to=date_to, offset=offset,
                           limit=limit, mask=_mask(user))
        except mi.MarketIntelError as e:
            _fail(e)
        return {**out, "capabilities": _caps(user)}

    @app.get("/market-intel/cases/{case_id}")
    def market_intel_case(case_id: int, raw: bool = False, user: CurrentUser = Depends(gate)) -> dict:
        _office_only(user)
        raw = bool(raw and user.role == "admin")
        try:
            out = mq.case_detail(case_id, role=user.role, mask=_mask(user), raw=raw)
        except mi.MarketIntelError as e:
            _fail(e)
        if raw:                                      # an original of a people photo was handed out
            log_event(user.email, "market_intel.raw_photos", detail={"case_id": case_id})
        return {**out, "capabilities": _caps(user)}

    @app.patch("/market-intel/cases/{case_id}")
    def market_intel_case_update(case_id: int, body: CaseUpdate, background: BackgroundTasks,
                                 user: CurrentUser = Depends(gate)) -> dict:
        changes = body.model_dump(exclude_unset=True)
        try:
            out = mq.update_case(case_id, changes, user.email, user.role, defer=background.add_task)
        except mi.MarketIntelError as e:
            _fail(e)
        log_event(user.email, "market_intel.case_update",
                  detail={"case_id": case_id, "fields": out.get("fields", []), "status": out.get("status"),
                          "changed": out.get("changed"), "safety_alerted": out.get("safety_alerted")})
        return out

    @app.post("/market-intel/cases/{case_id}/question")
    def market_intel_case_question(case_id: int, body: Question, user: CurrentUser = Depends(gate)) -> dict:
        try:
            out = mq.ask_question(case_id, body.text, user.email, user.role, observation_id=body.observation_id)
        except mi.MarketIntelError as e:
            _fail(e)
        log_event(user.email, "market_intel.case_question",
                  detail={"case_id": case_id, "observation_id": body.observation_id})
        return out

    @app.post("/market-intel/observations/{obs_id}/answer")
    @limiter.limit("30/minute")
    async def market_intel_answer(
        request: Request,
        obs_id: int,
        text: str | None = Form(default=None, max_length=2000),
        role: str | None = Form(default=None, max_length=20),
        photo: UploadFile | None = File(default=None),
        user: CurrentUser = Depends(gate),
    ) -> dict:
        """The rep's answer to the office's question on his sighting: text and / or one photo (read on the
        event loop, bounded; processed and stored in the threadpool)."""
        blob = None
        if photo is not None and (photo.filename or ""):
            try:
                blob = (photo.filename or "photo.jpg", await read_capped(photo, MAX_PHOTO_BYTES))
            except UploadTooLarge as e:
                raise HTTPException(status_code=413, detail=f"Photo too large ({e}).") from None
        try:
            out = await run_in_threadpool(mq.answer, obs_id, text, blob, _me(user), role_name=role)
        except mi.MarketIntelError as e:
            _fail(e)
        log_event(user.email, "market_intel.answer", detail={"observation_id": obs_id, "photo": out.get("photo")})
        return out

    @app.post("/market-intel/cases/{case_id}/merge")
    def market_intel_case_merge(case_id: int, body: MergeRequest, user: CurrentUser = Depends(gate)) -> dict:
        try:
            out = mq.merge_case(case_id, body.into_id, user.email, user.role)
        except mi.MarketIntelError as e:
            _fail(e)
        log_event(user.email, "market_intel.case_merge", detail=out)
        return out

    @app.post("/market-intel/observations/{obs_id}/move")
    def market_intel_observation_move(obs_id: int, body: MoveRequest, user: CurrentUser = Depends(gate)) -> dict:
        try:
            out = mq.move_observation(obs_id, body.case_id, user.email, user.role)
        except mi.MarketIntelError as e:
            _fail(e)
        log_event(user.email, "market_intel.observation_move", detail=out)
        return out

    @app.post("/market-intel/cases/{case_id}/action")
    def market_intel_case_action(case_id: int, body: ActionRequest, user: CurrentUser = Depends(gate)) -> dict:
        try:
            out = mq.create_action(case_id, body.model_dump(), user.email, user.role)
        except mi.MarketIntelError as e:
            _fail(e)
        log_event(user.email, "market_intel.action_create",
                  detail={"case_id": case_id, "action_id": out["action"].get("id"),
                          "supplier_id": out["action"].get("supplier_id")})
        return out

    @app.patch("/market-intel/actions/{action_id}")
    def market_intel_action_update(action_id: int, body: ActionUpdate, user: CurrentUser = Depends(gate)) -> dict:
        try:
            out = mq.update_action(action_id, body.model_dump(exclude_unset=True), user.email, user.role)
        except mi.MarketIntelError as e:
            _fail(e)
        log_event(user.email, "market_intel.action_update",
                  detail={"action_id": action_id, "changed": out.get("changed"), "status": out["action"].get("status"),
                          "cases": out.get("cases", [])})
        return out
