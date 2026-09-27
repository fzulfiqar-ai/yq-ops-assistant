"""Market Intelligence routes (release R7b, plan §18). Registered from app/main.py:

    from app.market_intel_api import register; register(app, limiter)

Every route is behind the 'Market Intel' page (require_feature), which is decoupled from 'AI Assistant':
granting capture never opens /ask. Inside the page, who may do what (app/market_intel.py):

    POST  /market-intel/observations                 capture (multipart: 1-4 photos + fields) -> result card
    GET   /market-intel/mine                         the caller's own sightings ("My signals")
    GET   /market-intel/items                        the board (clusters + filters); a salesman gets the
                                                     aggregated items only, never another rep's name
    GET   /market-intel/items/{item_id}              one item (a salesman: his own sightings only)
    GET   /market-intel/review                       office: new items + un-identified sightings (?library=1:
                                                     the imported Finds baseline)
    PATCH /market-intel/items/{item_id}              reviewers: edit fields / move the status (audited)
    POST  /market-intel/items/{item_id}/approve      reviewers and management: Opportunity -> Approved + action
    POST  /market-intel/observations/{obs_id}/identify   reviewers: put a sighting on an item
    POST  /market-intel/photos/{photo_id}/people     reviewers: flag / unflag people in a photo

Management is read-only everywhere (app/auth.py refuses its writes centrally); the approve route is
the one write on its allowlist (auth.READ_ONLY_WRITE_ALLOWLIST), because approving an action is what
plan §7 gives management here. The capture reads the photos on the event loop (bounded by
read_capped) and does everything else — Pillow, storage, the database — in the threadpool.

No `from __future__ import annotations`: the request models are defined inside register(), and
FastAPI must see real annotation objects to bind bodies.
"""
import logging

log = logging.getLogger(__name__)

FEATURE = "Market Intel"


def register(app, limiter) -> None:  # noqa: C901 — one registration function, many small routes
    from fastapi import Depends, File, Form, HTTPException, Request, UploadFile
    from pydantic import BaseModel, Field
    from starlette.concurrency import run_in_threadpool

    from app import market_intel as mi
    from app.audit import log_event
    from app.auth import CurrentUser, require_feature
    from app.features import is_read_only, masks_contacts
    from app.uploads import MAX_PHOTO_BYTES, UploadTooLarge, read_capped

    gate = require_feature(FEATURE)

    def _office(user: CurrentUser) -> bool:
        return user.role != "salesman"

    def _me(user: CurrentUser) -> str:
        """The login as a sighting stores it (created_by) and "my own" compares it: lower case."""
        return (user.email or "").strip().lower()

    def _caps(user: CurrentUser) -> dict:
        return {"office": _office(user), "can_review": user.role in mi.REVIEW_ROLES,
                "can_approve": user.role in mi.APPROVE_ROLES, "can_capture": not is_read_only(user.role)}

    def _fail(e: "mi.MarketIntelError"):
        raise HTTPException(status_code=e.status, detail=str(e))

    # ── models ────────────────────────────────────────────────────────────────
    class ItemUpdate(BaseModel):
        kind: str | None = Field(default=None, max_length=40)
        title: str | None = Field(default=None, max_length=200)
        brand: str | None = Field(default=None, max_length=80)
        competitor: str | None = Field(default=None, max_length=100)
        category: str | None = Field(default=None, max_length=80)
        barcode: str | None = Field(default=None, max_length=40)
        yq_item_code: str | None = Field(default=None, max_length=64)
        status: str | None = Field(default=None, max_length=20)
        action: str | None = Field(default=None, max_length=20)
        reason: str | None = Field(default=None, max_length=500)
        merged_into: int | None = None
        verified: bool | None = None      # a reviewer confirms the AI Head's reading

    class ApproveRequest(BaseModel):
        action: str = Field(max_length=20)
        reason: str | None = Field(default=None, max_length=500)

    class IdentifyRequest(BaseModel):
        item_id: int | None = None
        title: str | None = Field(default=None, max_length=200)
        kind: str | None = Field(default=None, max_length=40)
        brand: str | None = Field(default=None, max_length=80)
        competitor: str | None = Field(default=None, max_length=100)
        category: str | None = Field(default=None, max_length=80)
        barcode: str | None = Field(default=None, max_length=40)
        yq_item_code: str | None = Field(default=None, max_length=64)

    class PeopleFlag(BaseModel):
        flag: bool = True

    # ── capture ───────────────────────────────────────────────────────────────
    @app.post("/market-intel/observations")
    @limiter.limit("30/minute")
    async def market_intel_capture(
        request: Request,
        photos: list[UploadFile] = File(default=[]),
        client_uuid: str | None = Form(default=None, max_length=64),
        kind: str | None = Form(default=None, max_length=40),
        note: str | None = Form(default=None, max_length=2000),
        title: str | None = Form(default=None, max_length=200),
        brand: str | None = Form(default=None, max_length=80),
        competitor: str | None = Form(default=None, max_length=100),
        category: str | None = Form(default=None, max_length=80),
        price_bhd: str | None = Form(default=None, max_length=20),
        demand_level: str | None = Form(default=None, max_length=10),
        demand_qty: str | None = Form(default=None, max_length=10),
        shop_name: str | None = Form(default=None, max_length=160),
        shop_phone: str | None = Form(default=None, max_length=40),
        area: str | None = Form(default=None, max_length=80),
        barcode: str | None = Form(default=None, max_length=40),
        user: CurrentUser = Depends(gate),
    ) -> dict:
        """One sighting from the Spotted sheet -> the instant result card. Photos arrive already
        resized on the phone; the server re-checks, strips and hashes them. A retry with the same
        client_uuid returns the first answer and writes nothing."""
        files = [p for p in (photos or []) if p is not None and (p.filename or "")]
        if len(files) > mi.MAX_PHOTOS:
            raise HTTPException(status_code=400, detail=f"At most {mi.MAX_PHOTOS} photos per sighting.")
        blobs: list[tuple[str, bytes]] = []
        for f in files:
            try:
                blobs.append((f.filename or "photo.jpg", await read_capped(f, MAX_PHOTO_BYTES)))
            except UploadTooLarge as e:
                raise HTTPException(status_code=413, detail=f"Photo too large ({e}).") from None
        raw = {"client_uuid": client_uuid, "kind": kind, "note": note, "title": title, "brand": brand,
               "competitor": competitor, "category": category, "price_bhd": price_bhd,
               "demand_level": demand_level, "demand_qty": demand_qty, "shop_name": shop_name,
               "shop_phone": shop_phone, "area": area, "barcode": barcode}
        try:
            card = await run_in_threadpool(mi.capture, _me(user), raw, blobs)
        except mi.MarketIntelError as e:
            _fail(e)
        log_event(user.email, "market_intel.capture",
                  detail={"result": card.get("result"), "photos": card.get("photos"), "kind": kind or "other",
                          "observation_id": card.get("observation_id"), "replayed": card.get("replayed"),
                          "fallback": card.get("fallback")})
        return card

    @app.get("/market-intel/mine")
    def market_intel_mine(limit: int = 50, user: CurrentUser = Depends(gate)) -> dict:
        return {**mi.mine(_me(user), limit=limit), "capabilities": _caps(user)}

    # ── the board ─────────────────────────────────────────────────────────────
    @app.get("/market-intel/items")
    def market_intel_items(kind: str | None = None, status: str | None = None, brand: str | None = None,
                           category: str | None = None, rep: str | None = None, area: str | None = None,
                           since: str | None = None, q: str | None = None, mine: bool = False,
                           limit: int = 300, user: CurrentUser = Depends(gate)) -> dict:
        office = _office(user)
        filters = {"kind": kind, "status": status, "brand": brand, "category": category, "since": since,
                   "q": (q or "")[:80], "mine": mine}
        if office:                     # a rep never filters by another rep (or learns who saw what)
            filters.update(rep=rep, area=area)
        try:
            out = mi.board(office=office, actor=_me(user), filters=filters, limit=limit,
                           mask=masks_contacts(user.role))
        except mi.MarketIntelError as e:
            _fail(e)
        return {**out, "capabilities": _caps(user)}

    @app.get("/market-intel/items/{item_id}")
    def market_intel_item(item_id: int, user: CurrentUser = Depends(gate)) -> dict:
        try:
            out = mi.item_detail(item_id, office=_office(user), actor=_me(user), mask=masks_contacts(user.role))
        except mi.MarketIntelError as e:
            _fail(e)
        return {**out, "capabilities": _caps(user)}

    @app.get("/market-intel/review")
    def market_intel_review(library: bool = False, limit: int = 60, offset: int = 0,
                            user: CurrentUser = Depends(gate)) -> dict:
        if not _office(user):
            raise HTTPException(status_code=403, detail="The review queue is for the office.")
        return {**mi.review_queue(library=library, limit=limit, offset=offset, mask=masks_contacts(user.role)),
                "capabilities": _caps(user)}

    # ── deciding ──────────────────────────────────────────────────────────────
    @app.patch("/market-intel/items/{item_id}")
    def market_intel_update(item_id: int, body: ItemUpdate, user: CurrentUser = Depends(gate)) -> dict:
        changes = body.model_dump(exclude_unset=True)
        try:
            out = mi.update_item(item_id, changes, user.email, user.role)
        except mi.MarketIntelError as e:
            _fail(e)
        log_event(user.email, "market_intel.update",
                  detail={"item_id": item_id, "fields": sorted(k for k in changes if k != "reason"),
                          "status": out.get("status"), "changed": out.get("changed")})
        return out

    @app.post("/market-intel/items/{item_id}/approve")
    def market_intel_approve(item_id: int, body: ApproveRequest, user: CurrentUser = Depends(gate)) -> dict:
        try:
            out = mi.approve_item(item_id, body.action, body.reason, user.email, user.role)
        except mi.MarketIntelError as e:
            _fail(e)
        log_event(user.email, "market_intel.approve", detail={"item_id": item_id, "action": body.action})
        return out

    @app.post("/market-intel/observations/{obs_id}/identify")
    def market_intel_identify(obs_id: int, body: IdentifyRequest, user: CurrentUser = Depends(gate)) -> dict:
        try:
            out = mi.identify_observation(obs_id, body.model_dump(exclude_unset=True), user.email, user.role)
        except mi.MarketIntelError as e:
            _fail(e)
        log_event(user.email, "market_intel.identify", detail=out)
        return out

    @app.post("/market-intel/photos/{photo_id}/people")
    def market_intel_people(photo_id: int, body: PeopleFlag, user: CurrentUser = Depends(gate)) -> dict:
        try:
            out = mi.flag_photo(photo_id, body.flag, user.email, user.role)
        except mi.MarketIntelError as e:
            _fail(e)
        log_event(user.email, "market_intel.people_flag", detail=out)
        return out
