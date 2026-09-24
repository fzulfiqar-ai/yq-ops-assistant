"""Shop routes (see docs/SHOP.md). Registered onto the main app by app.main:

    from app.shop_api import register; register(app, limiter)

Kept in its own module (instead of inline in the 2,000-line main.py) so the ordering surface can
be read, tested and reviewed as one unit. Public routes are token-gated (the catalog share token,
constant-time compare) and rate-limited; portal routes use the same feature gates as every other
page ('Shop Orders' for salesmen/admins, 'Shop Admin' for rules/salesmen/settings).
"""
# NOTE: no `from __future__ import annotations` here — the routes are defined inside register(),
# and FastAPI must see real annotation objects (Request, the pydantic models) to bind bodies.
import logging
import secrets as _secrets

log = logging.getLogger(__name__)


def register(app, limiter) -> None:  # noqa: C901 — one registration function, many small routes
    from fastapi import BackgroundTasks, Depends, File, HTTPException, Request, Response, UploadFile
    from pydantic import BaseModel, Field

    from app import shop, shop_notify
    from app.audit import log_event
    from app.auth import CurrentUser, get_current_user, has_feature, require_admin, require_feature
    from app.catalog import share_token
    from app.config import settings as cfg
    from app.shop import ShopError

    def require_any_feature(*features: str):
        """Gate behind ANY of the given feature pages (the storekeeper has only 'Storekeeper')."""
        def _dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
            if any(has_feature(user, f) for f in features):
                return user
            raise HTTPException(status_code=403, detail=f"Requires access to one of {features}.")
        return _dep

    # Cacheable by any CDN in front of the API; stale-if-error keeps the last catalog on screen
    # while the free-tier container wakes up. Never indexed: trade prices are public, not promoted.
    MARKET_CACHE = "public, max-age=60, stale-while-revalidate=600, stale-if-error=86400"

    # ── models ────────────────────────────────────────────────────────────────
    class QuoteLine(BaseModel):
        item_code: str = Field(max_length=64)
        qty: int = Field(ge=1, le=shop.MAX_QTY)

    class QuoteRequest(BaseModel):
        lines: list[QuoteLine] = Field(max_length=shop.MAX_LINES)
        coupon_code: str | None = Field(default=None, max_length=40)
        referral_code: str | None = Field(default=None, max_length=32)

    class Customer(BaseModel):
        name: str = Field(max_length=120)
        phone: str = Field(max_length=32)
        shop: str | None = Field(default=None, max_length=120)
        area: str | None = Field(default=None, max_length=120)
        email: str | None = Field(default=None, max_length=160)

    class OrderRequest(QuoteRequest):
        salesman_id: int | None = None
        customer: Customer
        note: str | None = Field(default=None, max_length=1000)
        src: str | None = Field(default=None, max_length=80)
        session_id: str | None = Field(default=None, max_length=64)
        website: str | None = Field(default=None, max_length=200)   # honeypot — must stay empty

    class EventRequest(BaseModel):
        event: str = Field(max_length=16)
        item_code: str | None = Field(default=None, max_length=64)
        referral_code: str | None = Field(default=None, max_length=32)
        src: str | None = Field(default=None, max_length=80)
        session_id: str | None = Field(default=None, max_length=64)
        # marketplace (events v2): the device, the merchant when known, and a small PII-free meta bag
        device_id: str | None = Field(default=None, max_length=64)
        customer_id: int | None = None
        meta: dict | None = None

    class MarketOrderRequest(OrderRequest):
        device_id: str | None = Field(default=None, max_length=64)
        client_order_id: str | None = Field(default=None, max_length=64)   # idempotency key per device
        session_ref: str | None = Field(default=None, max_length=32)       # the /{slug} or ?ref this device remembers

    class CancelRequest(BaseModel):
        reason: str | None = Field(default=None, max_length=300)

    class MyOrdersRequest(BaseModel):
        tokens: list[str] = Field(max_length=20)

    class AssignRequest(BaseModel):
        salesman_id: int
        reason: str | None = Field(default=None, max_length=300)

    class ConfirmLine(BaseModel):
        line_id: int
        qty_confirmed: int | None = Field(default=None, ge=0, le=shop.MAX_QTY)
        line_status: str | None = Field(default=None, max_length=16)   # 'removed' drops the line
        note: str | None = Field(default=None, max_length=200)

    class ConfirmRequest(BaseModel):
        lines: list[ConfirmLine] = Field(default_factory=list, max_length=shop.MAX_LINES)
        expected_delivery: str | None = Field(default=None, max_length=120)
        note: str | None = Field(default=None, max_length=500)

    class StatusRequest(BaseModel):
        status: str = Field(max_length=16)
        note: str | None = Field(default=None, max_length=500)

    class SalesmanIn(BaseModel):
        name: str | None = Field(default=None, max_length=80)
        phone: str | None = Field(default=None, max_length=32)
        email: str | None = Field(default=None, max_length=160)
        whatsapp: str | None = Field(default=None, max_length=32)
        user_email: str | None = Field(default=None, max_length=160)
        focus_name: str | None = Field(default=None, max_length=120)
        referral_code: str | None = Field(default=None, max_length=32)
        is_active: bool | None = None
        notify_email: bool | None = None
        notify_whatsapp: bool | None = None
        sort_order: int | None = None

    class ShopSettingsIn(BaseModel):
        settings: dict[str, str]

    class RuleIn(BaseModel):
        name: str | None = Field(default=None, max_length=80)
        kind: str | None = Field(default=None, max_length=20)
        scope: dict | None = None
        min_qty: int | None = None
        min_value_bhd: float | None = None
        pct_off: float | None = None
        amount_off_bhd: float | None = None
        fixed_price_bhd: float | None = None
        coupon_code: str | None = Field(default=None, max_length=32)
        stackable: bool | None = None
        starts_at: str | None = Field(default=None, max_length=40)
        ends_at: str | None = Field(default=None, max_length=40)
        max_uses: int | None = None
        priority: int | None = None
        is_active: bool | None = None

    class CampaignIn(BaseModel):
        title: str | None = Field(default=None, max_length=80)
        title_ar: str | None = Field(default=None, max_length=160)
        line: str | None = Field(default=None, max_length=160)
        line_ar: str | None = Field(default=None, max_length=160)
        image_url: str | None = Field(default=None, max_length=400)
        # v3 creative — all optional; defaults (contain / lilac) are applied by shop.validate_campaign
        image_url_600: str | None = Field(default=None, max_length=400)
        image_fit: str | None = Field(default=None, max_length=12)
        product_codes: list[str] | None = Field(default=None, max_length=12)   # ≤3 enforced with a clear 400
        canvas: str | None = Field(default=None, max_length=12)
        cta_label: str | None = Field(default=None, max_length=40)
        cta_label_ar: str | None = Field(default=None, max_length=40)
        cta_to: str | None = Field(default=None, max_length=300)
        placement: list[str] | None = None
        category: str | None = Field(default=None, max_length=40)
        audience: str | None = Field(default=None, max_length=12)
        rule_id: int | None = None
        sponsored: bool | None = None
        sponsor_name: str | None = Field(default=None, max_length=60)
        starts_at: str | None = Field(default=None, max_length=40)
        ends_at: str | None = Field(default=None, max_length=40)
        is_active: bool | None = None
        sort_order: int | None = None

    class RestockRequest(BaseModel):
        item_code: str = Field(max_length=64)
        phone: str | None = Field(default=None, max_length=32)
        device_id: str | None = Field(default=None, max_length=64)
        referral_code: str | None = Field(default=None, max_length=32)

    class RecognizeRequest(BaseModel):
        phone: str = Field(max_length=32)
        device_id: str | None = Field(default=None, max_length=64)

    class RestockResolve(BaseModel):
        ids: list[int] = Field(max_length=200)

    # ── helpers ───────────────────────────────────────────────────────────────
    def _check_token(token: str) -> None:
        good = share_token(create=False)
        if not good or not _secrets.compare_digest(token, good):
            raise HTTPException(status_code=404, detail="Invalid catalog link.")

    def _ip(request: Request) -> str | None:
        # Proxy-aware (trusted hops from the right), shared with the rate limiter. The old
        # `split(",")[0]` took the client-controlled left end of X-Forwarded-For.
        from app.ratelimit import client_ip
        return client_ip(request)

    def _ua(request: Request) -> str:
        return (request.headers.get("user-agent") or "")[:200]

    def _scope(user: CurrentUser) -> tuple[int | None, bool]:
        """(salesman_id filter, is_admin). Non-admins only ever see their own orders."""
        if user.role == "admin":
            return None, True
        sm = shop.salesman_for_user(user.email)
        return (sm["id"] if sm else -1), False

    def _owner_contact() -> dict | None:
        from app.customer_contacts import wa_digits
        d = wa_digits(cfg.wa_human_number) if cfg.wa_human_number else None
        return {"name": "YQ Bahrain", "phone": d} if d else None

    # ── public ────────────────────────────────────────────────────────────────
    @app.post("/public/shop/{token}/quote")
    @limiter.limit("60/minute")
    def shop_quote(request: Request, token: str, body: QuoteRequest) -> dict:
        """Price a cart server-side (tiers, cart rules, coupon, margin floor, stock status)."""
        _check_token(token)
        try:
            q = shop.price_cart([ln.model_dump() for ln in body.lines], body.coupon_code, body.referral_code)
        except ShopError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {k: v for k, v in q.items() if not k.startswith("_")}

    @app.post("/public/shop/{token}/order")
    @limiter.limit("5/minute")
    def shop_order(request: Request, token: str, body: OrderRequest, background: BackgroundTasks) -> dict:
        """Submit an order: validate → re-price → persist → notify (background)."""
        _check_token(token)
        try:
            o = shop.create_order(body.model_dump(), ip=_ip(request), ua=_ua(request))
        except ShopError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        background.add_task(shop_notify.notify_new_order, o["id"])
        sm = o.get("salesman") or {}
        contact = ({"name": sm.get("name"), "phone": sm.get("whatsapp") or sm.get("phone")} if sm
                   else _owner_contact())
        return {
            "ok": True, "order_no": o["order_no"], "token": o["token"], "status_url": o["status_url"],
            "salesman": contact, "whatsapp_url": shop_notify.customer_to_salesman_wa_url(o),
            "email_url": shop_notify.customer_to_salesman_email_url(o),
            "totals": o["totals"], "has_backorder": bool(o.get("has_backorder")),
        }

    @app.get("/public/shop/order/{order_token}")
    @limiter.limit("30/minute")
    def shop_order_status(request: Request, order_token: str) -> dict:
        o = shop.get_order_by_token(order_token)
        if not o:
            raise HTTPException(status_code=404, detail="Order not found.")
        return shop.public_order_view(o)

    @app.post("/public/shop/{token}/event")
    @limiter.limit("120/minute")
    def shop_event(request: Request, token: str, body: EventRequest) -> dict:
        good = share_token(create=False)
        if not good or not _secrets.compare_digest(token, good):
            return {"ok": False}
        return {"ok": shop.record_event(body.model_dump(), ip=_ip(request), ua=_ua(request))}

    # ── public: the marketplace (token-less; the share token is resolved server-side) ──
    @app.get("/public/market")
    @limiter.limit("60/minute")
    def market_catalog(request: Request, ref: str | None = None):
        """The marketplace catalog for the root URL and every /{slug} storefront. Same payload as
        /public/catalog/{token} plus `rep` (the storefront card) — no token in the URL, so a
        rotation never breaks the storefront or the pre-JS prefetch."""
        data = shop.market_json(ref)
        if data is None:
            raise HTTPException(status_code=404, detail="The marketplace is not open.")
        return Response(content=data, media_type="application/json",
                        headers={"Cache-Control": MARKET_CACHE, "X-Robots-Tag": "noindex, nofollow"})

    @app.get("/public/rep/{slug}")
    @limiter.limit("60/minute")
    def market_rep(request: Request, slug: str) -> dict:
        """The salesman storefront card: name, title, photo, opt-in WhatsApp. Reserved words and
        unknown slugs are 404 so the front end falls back to the plain home page."""
        ctx = shop.context()
        if shop.is_reserved_slug(slug, ctx):
            raise HTTPException(status_code=404, detail="Not found.")
        card = shop.rep_card(ctx, slug)
        if not card:
            raise HTTPException(status_code=404, detail="Not found.")
        return card

    @app.post("/public/market/quote")
    @limiter.limit("60/minute")
    def market_quote(request: Request, body: QuoteRequest) -> dict:
        if not shop.market_enabled():
            raise HTTPException(status_code=404, detail="The marketplace is not open.")
        try:
            q = shop.price_cart([ln.model_dump() for ln in body.lines], body.coupon_code, body.referral_code)
        except ShopError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {k: v for k, v in q.items() if not k.startswith("_")}

    @app.post("/public/market/order")
    @limiter.limit("10/minute")
    def market_order(request: Request, body: MarketOrderRequest, background: BackgroundTasks) -> dict:
        """Place a marketplace order: attribution via resolve_salesman, idempotent per
        (device_id, client_order_id), per-phone/per-device daily caps, merchant record upserted."""
        if not shop.market_enabled():
            raise HTTPException(status_code=404, detail="The marketplace is not open.")
        try:
            o = shop.create_order(body.model_dump(), ip=_ip(request), ua=_ua(request), market=True)
        except ShopError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        if not o.get("duplicate"):
            background.add_task(shop_notify.notify_new_order, o["id"])
        sm = o.get("salesman") or {}
        name = str(sm.get("name") or "")
        return {
            "ok": True, "duplicate": bool(o.get("duplicate")),
            "order_no": o["order_no"], "token": o["token"], "status_url": o["status_url"],
            "status": o.get("status", "new"), "status_label": shop.STATUS_LABELS.get(o.get("status", "new"), "Received"),
            "assigned": bool(sm), "attribution": o.get("attribution_source"),
            "salesman": ({"name": name, "first_name": name.split(" ")[0] if name else ""} if sm else None),
            "whatsapp_url": shop_notify.customer_to_salesman_wa_url(o),
            "email_url": shop_notify.customer_to_salesman_email_url(o),
            "totals": o["totals"], "has_backorder": bool(o.get("has_backorder")),
            # 'small' = sent under the wholesale minimum as a request the rep confirms case by case
            "order_kind": o.get("order_kind") or "standard",
        }

    @app.post("/public/market/event")
    @limiter.limit("240/minute")
    def market_event(request: Request, body: EventRequest) -> dict:
        if not shop.market_enabled():
            return {"ok": False}
        return {"ok": shop.record_event(body.model_dump(), ip=_ip(request), ua=_ua(request))}

    @app.post("/public/shop/order/{order_token}/cancel")
    @limiter.limit("3/hour")
    def shop_order_cancel(request: Request, order_token: str, background: BackgroundTasks,
                          body: CancelRequest | None = None) -> dict:
        """The merchant cancels while the order is still Received (token-gated, like the status page)."""
        try:
            o = shop.cancel_by_customer(order_token, body.reason if body else None)
        except ShopError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        background.add_task(shop_notify.notify_customer_cancel, o["id"])
        shop.record_event({"event": "cancel", "salesman_id": o.get("salesman_id"), "device_id": o.get("device_id"),
                           "customer_id": o.get("customer_id"), "meta": {"reason": (body.reason if body else None) or ""}},
                          ip=_ip(request), ua=_ua(request))
        return {"ok": True, "status": o["status"], "order": shop.public_order_view(o)}

    @app.post("/public/shop/my-orders")
    @limiter.limit("30/minute")
    def shop_my_orders(request: Request, body: MyOrdersRequest) -> dict:
        """Summaries for the order tokens this device holds (its own orders only, by construction)."""
        return {"orders": shop.orders_by_tokens(body.tokens)}

    @app.get("/public/share/{token}/{item_code}")
    @limiter.limit("60/minute")
    def shop_share(request: Request, token: str, item_code: str, ref: str | None = None):
        """Rich link preview (Open Graph + JSON-LD Product) that forwards browsers to the shop."""
        import html as _html
        import json as _json
        _check_token(token)
        it = shop.share_item(item_code)
        if not it:
            raise HTTPException(status_code=404, detail="Item not found.")
        base = shop._base_url()
        target = f"{base}/c/{token}?item={_html.escape(it['item_code'])}"
        if ref:
            target += f"&ref={_html.escape(ref[:32])}"
        price = f"BHD {it['price_bhd']:.3f}" if it.get("price_bhd") is not None else "Ask for price"
        labels = {"in_stock": "In stock", "low_stock": "Only a few left", "out_of_stock": "Sold out"}
        schema_avail = {"in_stock": "https://schema.org/InStock", "low_stock": "https://schema.org/LimitedAvailability",
                        "out_of_stock": "https://schema.org/OutOfStock"}
        title = f"{it['item_code']} · {price} · {labels[it['stock_status']]} — YQ Bahrain trade catalog"
        desc = (it.get("spec") or "")[:180]
        ld = {"@context": "https://schema.org", "@type": "Product", "name": it["item_code"], "sku": it["item_code"],
              "description": desc, "brand": {"@type": "Brand", "name": "VFAN"},
              "image": [it["image"]] if it.get("image") else [],
              "offers": {"@type": "Offer", "priceCurrency": "BHD", "price": it.get("price_bhd"),
                         "availability": schema_avail[it["stock_status"]], "url": target,
                         "seller": {"@type": "Organization", "name": "YQ Bahrain"}}}
        e = _html.escape
        og_image = f'<meta property="og:image" content="{e(it["image"])}">' if it.get("image") else ""
        card = "summary_large_image" if it.get("image") else "summary"
        page = (
            "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<title>{e(title)}</title>"
            f"<meta name=\"description\" content=\"{e(desc)}\">"
            "<meta property=\"og:type\" content=\"product\"><meta property=\"og:site_name\" content=\"YQ Bahrain\">"
            f"<meta property=\"og:title\" content=\"{e(title)}\"><meta property=\"og:description\" content=\"{e(desc)}\">"
            f"{og_image}<meta property=\"og:url\" content=\"{e(target)}\">"
            f"<meta name=\"twitter:card\" content=\"{card}\">"
            f"<meta http-equiv=\"refresh\" content=\"0;url={e(target)}\">"
            f"<script type=\"application/ld+json\">{_json.dumps(ld)}</script>"
            f"<script>location.replace({_json.dumps(target)});</script>"
            "</head><body style=\"font-family:system-ui;padding:24px;color:#1a1430\">"
            f"<p>Opening <a href=\"{e(target)}\">{e(it['item_code'])} in the YQ Bahrain catalog</a>…</p></body></html>"
        )
        return Response(content=page, media_type="text/html", headers={"Cache-Control": "public, max-age=300"})

    # ── public: a returning merchant, recognised by phone ───────────────────
    @app.post("/public/market/recognize")
    @limiter.limit("10/minute")
    def market_recognize(request: Request, body: RecognizeRequest) -> dict:
        known = shop.recognize_phone(body.phone, body.device_id)
        return {"known": known}

    # ── public: "tell me when back" ─────────────────────────────────────────
    @app.post("/public/market/restock")
    @limiter.limit("20/minute")
    def market_restock(request: Request, body: RestockRequest) -> dict:
        ok = shop.add_restock(body.item_code, body.phone, body.device_id, body.referral_code)
        return {"ok": ok}

    # ── portal: campaigns (Shop Admin) + restock list (Shop Orders) ──────────
    @app.get("/shop/campaigns")
    def shop_campaigns_list(_user: CurrentUser = Depends(require_feature("Shop Admin"))) -> dict:
        return {"campaigns": shop.list_campaigns()}

    @app.post("/shop/campaigns")
    def shop_campaigns_create(body: CampaignIn, user: CurrentUser = Depends(require_feature("Shop Admin"))) -> dict:
        try:
            row = shop.upsert_campaign(body.model_dump(exclude_unset=True), by=user.email)
        except ShopError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        log_event(user.email, "shop.campaign_create", detail={"id": row.get("id")})
        return row

    @app.patch("/shop/campaigns/{campaign_id}")
    def shop_campaigns_update(campaign_id: int, body: CampaignIn, user: CurrentUser = Depends(require_feature("Shop Admin"))) -> dict:
        try:
            row = shop.upsert_campaign(body.model_dump(exclude_unset=True), by=user.email, campaign_id=campaign_id)
        except ShopError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        log_event(user.email, "shop.campaign_update", detail={"id": campaign_id})
        return row

    @app.delete("/shop/campaigns/{campaign_id}")
    def shop_campaigns_delete(campaign_id: int, user: CurrentUser = Depends(require_feature("Shop Admin"))) -> dict:
        shop.delete_campaign(campaign_id)
        log_event(user.email, "shop.campaign_delete", detail={"id": campaign_id})
        return {"ok": True}

    @app.post("/shop/campaigns/image")
    async def shop_campaigns_image(file: UploadFile = File(...), user: CurrentUser = Depends(require_feature("Shop Admin"))) -> dict:
        from app.uploads import MAX_PHOTO_BYTES, UploadTooLarge, content_matches, photo_ext, read_capped
        ext = photo_ext(file.filename or "")
        if not ext:
            raise HTTPException(status_code=400, detail="Please upload a JPG, PNG or WEBP image.")
        try:
            data = await read_capped(file, MAX_PHOTO_BYTES)
        except UploadTooLarge as e:
            raise HTTPException(status_code=400, detail=f"Image too large ({e}).") from e
        if not content_matches(file.filename or "", data):
            raise HTTPException(status_code=400, detail="That file is not a valid image.")
        from app.catalog import upload_campaign_image
        urls = upload_campaign_image(data)
        if not urls:
            raise HTTPException(status_code=500, detail="Could not store the image.")
        log_event(user.email, "shop.campaign_image", detail={"bytes": len(data)})
        return urls

    @app.get("/shop/restock")
    def shop_restock_list(user: CurrentUser = Depends(require_feature("Shop Orders"))) -> dict:
        sid, admin = _scope(user)
        ref = None
        if not admin:
            sm = shop.salesman_for_user(user.email)
            ref = (sm or {}).get("referral_code") or "-"
        return {"requests": shop.list_restock(ref)}

    @app.post("/shop/restock/resolve")
    def shop_restock_resolve(body: RestockResolve, user: CurrentUser = Depends(require_feature("Shop Orders"))) -> dict:
        n = shop.resolve_restock(body.ids)
        log_event(user.email, "shop.restock_resolve", detail={"n": n})
        return {"ok": True, "n": n}

    # ── portal: rules (Shop Admin) ────────────────────────────────────────────
    @app.get("/shop/rules")
    def shop_rules_list(_user: CurrentUser = Depends(require_feature("Shop Admin"))) -> dict:
        return {"rules": shop.list_rules()}

    @app.post("/shop/rules")
    def shop_rules_create(body: RuleIn, user: CurrentUser = Depends(require_feature("Shop Admin"))) -> dict:
        try:
            row = shop.upsert_rule(body.model_dump(exclude_unset=True), by=user.email)
        except ShopError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        log_event(user.email, "shop.rule_create", detail={"id": row.get("id"), "kind": row.get("kind")})
        return row

    @app.patch("/shop/rules/{rule_id}")
    def shop_rules_update(rule_id: int, body: RuleIn, user: CurrentUser = Depends(require_feature("Shop Admin"))) -> dict:
        try:
            row = shop.upsert_rule(body.model_dump(exclude_unset=True), by=user.email, rule_id=rule_id)
        except ShopError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        log_event(user.email, "shop.rule_update", detail={"id": rule_id})
        return row

    @app.delete("/shop/rules/{rule_id}")
    def shop_rules_delete(rule_id: int, user: CurrentUser = Depends(require_feature("Shop Admin"))) -> dict:
        shop.delete_rule(rule_id)
        log_event(user.email, "shop.rule_delete", detail={"id": rule_id})
        return {"ok": True}

    @app.post("/shop/rules/preview")
    def shop_rules_preview(body: RuleIn, _user: CurrentUser = Depends(require_feature("Shop Admin"))) -> dict:
        """Dry-run a rule: items touched + margin-floor breaches, without saving."""
        try:
            row = shop.validate_rule(body.model_dump(exclude_unset=True))
        except ShopError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {"rule": row, "summary": shop.rule_summary(row), "impact": shop.rule_impact(row)}

    # ── portal: salesman mode — the logged-in catalog + ordering for a shop (feature Catalog) ──
    class StaffQuoteRequest(BaseModel):
        lines: list[QuoteLine] = Field(max_length=shop.MAX_LINES)
        coupon_code: str | None = Field(default=None, max_length=40)

    class StaffOrderRequest(StaffQuoteRequest):
        salesman_id: int | None = None          # admins without a linked salesman row choose one
        customer: Customer
        note: str | None = Field(default=None, max_length=1000)

    @app.get("/shop/catalog")
    def shop_staff_catalog(user: CurrentUser = Depends(require_feature("Catalog"))) -> dict:
        """Same payload as the public catalog + exact stock units + who I am (never public)."""
        r = shop.catalog_payload(None, staff_email=user.email)
        r["me"]["is_admin"] = user.role == "admin"
        return r

    @app.post("/shop/quote")
    def shop_staff_quote(body: StaffQuoteRequest, user: CurrentUser = Depends(require_feature("Catalog"))) -> dict:
        sm = shop.salesman_for_user(user.email)
        try:
            q = shop.price_cart([ln.model_dump() for ln in body.lines], body.coupon_code, (sm or {}).get("referral_code"))
        except ShopError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return {k: v for k, v in q.items() if not k.startswith("_")}

    @app.post("/shop/order")
    def shop_staff_order(request: Request, body: StaffOrderRequest, background: BackgroundTasks,
                         user: CurrentUser = Depends(require_feature("Catalog"))) -> dict:
        """A salesman (or admin) places an order FOR a shop — source 'salesman', placed_by = login."""
        try:
            o = shop.create_order(body.model_dump(), ip=_ip(request), ua=_ua(request), staff_email=user.email)
        except ShopError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        background.add_task(shop_notify.notify_new_order, o["id"])
        log_event(user.email, "shop.order_placed", detail={"order_id": o["id"], "order_no": o["order_no"]})
        sm = o.get("salesman") or {}
        return {
            "ok": True, "order_id": o["id"], "order_no": o["order_no"], "token": o["token"],
            "status_url": o["status_url"],
            "salesman": ({"name": sm.get("name"), "phone": sm.get("whatsapp") or sm.get("phone")} if sm else None),
            "whatsapp_url": shop_notify.salesman_to_customer_wa_url(o, "new"),   # the salesman's tap TO the shop
            "email_url": None,
            "totals": o["totals"], "has_backorder": bool(o.get("has_backorder")), "source": "salesman",
        }

    @app.get("/shop/customers")
    def shop_staff_customers(user: CurrentUser = Depends(require_feature("Shop Orders"))) -> dict:
        """Recent shops for the checkout quick-pick (a salesman sees his own; admins see all)."""
        sid, _admin = _scope(user)
        if sid == -1:
            return {"customers": []}
        return {"customers": shop.recent_customers(sid)}

    # ── portal: orders ────────────────────────────────────────────────────────
    @app.get("/shop/orders")
    def shop_orders_list(status: str | None = None, q: str | None = None, limit: int = 50, offset: int = 0,
                         user: CurrentUser = Depends(require_feature("Shop Orders"))) -> dict:
        sid, _admin = _scope(user)
        if sid == -1:
            return {"orders": [], "count": 0,
                    "hint": "Your login is not linked to a salesman yet — an admin can link it on the Salesmen page."}
        return shop.list_orders(status, q, limit, offset, salesman_id=sid)

    def _visible(user: CurrentUser, o: dict | None) -> bool:
        """May this staff user see/act on this order? Admins and the storekeeper: every order;
        a salesman: his own."""
        if not o:
            return False
        if user.role in ("admin", "storekeeper"):
            return True
        sid, _admin = _scope(user)
        return o.get("salesman_id") == sid

    @app.get("/shop/orders/{order_id}")
    def shop_order_detail(order_id: int,
                          user: CurrentUser = Depends(require_any_feature("Shop Orders", "Storekeeper"))) -> dict:
        o = shop.get_order(order_id)
        if not _visible(user, o):
            raise HTTPException(status_code=404, detail="Order not found.")
        o["whatsapp_url"] = shop_notify.salesman_to_customer_wa_url(o)
        base = shop.market_base()
        o["status_url"] = f"{base}/o/{o.get('token')}" if base else f"/o/{o.get('token')}"
        allowed = None if user.role == "admin" else shop.ROLE_STATUSES.get(user.role)
        nxt = list(shop.NEXT_STATUS.get(o["status"], ()))
        o["next_statuses"] = [s for s in nxt if allowed is None or s in allowed]
        o["steps"] = shop.order_steps(o)
        o["status_label"] = shop.STATUS_LABELS.get(o["status"], o["status"])
        o.pop("ip_hash", None)
        return o

    @app.post("/shop/orders/{order_id}/status")
    def shop_order_set_status(order_id: int, body: StatusRequest, background: BackgroundTasks,
                              user: CurrentUser = Depends(require_any_feature("Shop Orders", "Storekeeper"))) -> dict:
        cur = shop.get_order(order_id)
        if not _visible(user, cur):
            raise HTTPException(status_code=404, detail="Order not found.")
        allowed = None if user.role == "admin" else shop.ROLE_STATUSES.get(user.role)
        try:
            o = shop.set_status(order_id, body.status, body.note, actor=user.email, allowed=allowed)
        except ShopError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        background.add_task(shop_notify.notify_status, order_id, body.status, body.note)
        log_event(user.email, "shop.order_status", detail={"order_id": order_id, "status": body.status})
        o["whatsapp_url"] = shop_notify.salesman_to_customer_wa_url(o, body.status)
        nxt = list(shop.NEXT_STATUS.get(o["status"], ()))
        o["next_statuses"] = [s for s in nxt if allowed is None or s in allowed]
        o["steps"] = shop.order_steps(o)
        o.pop("ip_hash", None)
        return {"ok": True, "order": o}

    @app.post("/shop/orders/{order_id}/confirm")
    def shop_order_confirm(order_id: int, body: ConfirmRequest, background: BackgroundTasks,
                           user: CurrentUser = Depends(require_feature("Shop Orders"))) -> dict:
        """Confirm with changes: confirmed quantities / removed lines, expected delivery, re-price."""
        cur = shop.get_order(order_id)
        if not _visible(user, cur):
            raise HTTPException(status_code=404, detail="Order not found.")
        try:
            o = shop.confirm_order(order_id, [ln.model_dump() for ln in body.lines], body.expected_delivery,
                                   body.note, actor=user.email)
        except ShopError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        background.add_task(shop_notify.notify_status, order_id, "confirmed", body.note)
        log_event(user.email, "shop.order_confirm",
                  detail={"order_id": order_id, "changed": o.get("changed"), "removed": o.get("removed")})
        totals = o.pop("totals", None)
        o["whatsapp_url"] = shop_notify.salesman_to_customer_wa_url(o, "confirmed")
        o["next_statuses"] = list(shop.NEXT_STATUS.get(o["status"], ()))
        o["steps"] = shop.order_steps(o)
        o.pop("ip_hash", None)
        return {"ok": True, "order": o, "totals": totals, "changed": o.get("changed"), "removed": o.get("removed"),
                "whatsapp_url": o["whatsapp_url"], "next_statuses": o["next_statuses"]}

    @app.post("/shop/orders/{order_id}/assign")
    def shop_order_assign(order_id: int, body: AssignRequest, background: BackgroundTasks,
                          user: CurrentUser = Depends(require_feature("Shop Orders"))) -> dict:
        """Admins assign or reassign; a salesman may only take an unassigned order for himself."""
        sid, is_admin = _scope(user)
        try:
            o = shop.assign_order(order_id, body.salesman_id, actor=user.email, reason=body.reason,
                                  is_admin=is_admin, actor_salesman_id=(sid if sid and sid > 0 else None))
        except ShopError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        background.add_task(shop_notify.notify_assigned, order_id)
        log_event(user.email, "shop.order_assign",
                  detail={"order_id": order_id, "salesman_id": body.salesman_id, "reason": body.reason})
        o["whatsapp_url"] = shop_notify.salesman_to_customer_wa_url(o, o.get("status"))
        o["next_statuses"] = list(shop.NEXT_STATUS.get(o["status"], ()))
        o.pop("ip_hash", None)
        return {"ok": True, "order": o}

    @app.get("/shop/assignment-queue")
    def shop_assignment_queue(_user: CurrentUser = Depends(require_feature("Shop Orders"))) -> dict:
        """Unassigned open orders with a suggested rep (history, not AI)."""
        return shop.assignment_queue()

    @app.get("/shop/picklist")
    def shop_picklist(salesman_id: int | None = None,
                      user: CurrentUser = Depends(require_any_feature("Storekeeper", "Shop Orders"))) -> dict:
        """The storekeeper's pick list: confirmed / preparing orders grouped by salesman + totals per item.
        A salesman only ever sees his own."""
        if user.role == "salesman":
            salesman_id = _scope(user)[0]
            if salesman_id == -1:
                return {"groups": [], "totals_by_item": [], "count": 0}
        return shop.picklist(salesman_id)

    @app.get("/shop/analytics")
    def shop_analytics(days: int = 30, user: CurrentUser = Depends(require_feature("Shop Orders"))) -> dict:
        """Funnel, AOV, top products, salesman leaderboard, attribution. Salesmen see their own slice."""
        if user.role == "admin":
            return shop.analytics(days)
        sm = shop.salesman_for_user(user.email)
        if not sm:
            return {"days": days, "orders": 0, "hint": "Your login is not linked to a salesman yet."}
        return shop.analytics(days, salesman=sm)

    @app.get("/shop/me")
    def shop_me(user: CurrentUser = Depends(require_feature("Shop Orders"))) -> dict:
        return shop.me_payload(user.email)

    # ── portal: salesmen (Shop Admin) ─────────────────────────────────────────
    @app.get("/shop/salesmen")
    def shop_salesmen_list(_user: CurrentUser = Depends(require_feature("Shop Admin"))) -> dict:
        return {"salesmen": shop.list_salesmen()}

    @app.post("/shop/salesmen")
    def shop_salesmen_create(body: SalesmanIn, user: CurrentUser = Depends(require_feature("Shop Admin"))) -> dict:
        try:
            row = shop.upsert_salesman(body.model_dump(exclude_none=True), by=user.email)
        except ShopError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        log_event(user.email, "shop.salesman_create", detail={"id": row.get("id"), "name": row.get("name")})
        return row

    @app.patch("/shop/salesmen/{salesman_id}")
    def shop_salesmen_update(salesman_id: int, body: SalesmanIn,
                             user: CurrentUser = Depends(require_feature("Shop Admin"))) -> dict:
        try:
            row = shop.upsert_salesman(body.model_dump(exclude_none=True), by=user.email, salesman_id=salesman_id)
        except ShopError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except IndexError as e:
            raise HTTPException(status_code=404, detail="Salesman not found.") from e
        log_event(user.email, "shop.salesman_update", detail={"id": salesman_id})
        return row

    @app.delete("/shop/salesmen/{salesman_id}")
    def shop_salesmen_delete(salesman_id: int, user: CurrentUser = Depends(require_feature("Shop Admin"))) -> dict:
        shop.delete_salesman(salesman_id)
        log_event(user.email, "shop.salesman_delete", detail={"id": salesman_id})
        return {"ok": True}

    @app.get("/shop/salesmen/{salesman_id}/qr.png")
    def shop_salesman_qr(salesman_id: int, user: CurrentUser = Depends(require_feature("Shop Orders"))):
        sm = shop._salesman_by_id(salesman_id)
        if not sm:
            raise HTTPException(status_code=404, detail="Salesman not found.")
        if user.role != "admin" and (sm.get("user_email") or "").lower() != user.email.lower():
            raise HTTPException(status_code=403, detail="Not your link.")
        return Response(content=shop.qr_png(shop.salesman_link(sm)), media_type="image/png",
                        headers={"Cache-Control": "private, max-age=3600"})

    # ── portal: settings + data hygiene ───────────────────────────────────────
    @app.get("/settings/shop")
    def shop_settings_get(_admin: CurrentUser = Depends(require_admin)) -> dict:
        return {"settings": shop.shop_settings(force=True)}

    @app.put("/settings/shop")
    def shop_settings_put(body: ShopSettingsIn, admin: CurrentUser = Depends(require_admin)) -> dict:
        try:
            vals = shop.update_shop_settings(body.settings, by=admin.email)
        except ShopError as e:      # e.g. shop_market_promises is not a valid promise list — nothing was saved
            raise HTTPException(status_code=400, detail=str(e)) from e
        log_event(admin.email, "settings.shop", detail={"keys": sorted(body.settings.keys())})
        ignored = sorted(k for k in body.settings if k not in shop.SETTING_DEFAULTS)
        return {"ok": True, "settings": vals, "ignored": ignored}

    @app.get("/shop/margins")
    def shop_margins(_user: CurrentUser = Depends(require_feature("Shop Admin"))) -> dict:
        """Per-SKU landed cost vs trade price (live version of the owner's landed-cost workbook)."""
        return shop.margin_health()

    @app.get("/shop/unpriced-stock")
    def shop_unpriced_stock(_user: CurrentUser = Depends(require_feature("Shop Admin"))) -> dict:
        return {"rows": shop.unpriced_stock()}

    # ── "Coming soon" (app/upcoming.py; trust plan §6b, release R1b) ─────────────
    # The announced WEKOME range before it lands: a separate, cached, whitelisted payload (never
    # part of the catalog payload, so the home page's LCP does not wait for it), a "notify me"
    # that reuses the restock table and its limits, an admin list/patch, and the rep's interest
    # list scoped to his referral code. No price, cost or quantity ever leaves through here.
    from app import upcoming

    class UpcomingInterestRequest(BaseModel):
        upcoming_id: int
        phone: str | None = Field(default=None, max_length=32)
        qty_interest: int | None = Field(default=None, ge=1, le=shop.MAX_QTY)   # optional, "no commitment"
        device_id: str | None = Field(default=None, max_length=64)
        ref: str | None = Field(default=None, max_length=32)

    class UpcomingPatch(BaseModel):
        status: str | None = Field(default=None, max_length=16)
        expected_month: str | None = Field(default=None, max_length=10)      # YYYY-MM
        expected_label_en: str | None = Field(default=None, max_length=80)
        expected_label_ar: str | None = Field(default=None, max_length=80)
        name_en: str | None = Field(default=None, max_length=160)
        name_ar: str | None = Field(default=None, max_length=160)
        spec_en: str | None = Field(default=None, max_length=300)
        spec_ar: str | None = Field(default=None, max_length=300)
        category: str | None = Field(default=None, max_length=60)
        catalog_item_code: str | None = Field(default=None, max_length=64)   # links the card to the live item (auto-retire)
        sort_order: int | None = None

    @app.get("/public/market/upcoming")
    @limiter.limit("60/minute")
    def market_upcoming(request: Request):
        """Published, not-yet-retired upcoming cards (whitelisted fields only), cached 60 s."""
        if not shop.market_enabled():
            raise HTTPException(status_code=404, detail="The marketplace is not open.")
        return Response(content=upcoming.public_json(), media_type="application/json",
                        headers={"Cache-Control": MARKET_CACHE, "X-Robots-Tag": "noindex, nofollow"})

    @app.post("/public/market/upcoming/interest")
    @limiter.limit("20/minute")      # the restock endpoint's limit: it is the same request, for a card that has no stock yet
    def market_upcoming_interest(request: Request, body: UpcomingInterestRequest) -> dict:
        if not shop.market_enabled():
            raise HTTPException(status_code=404, detail="The marketplace is not open.")
        ok = upcoming.add_interest(body.upcoming_id, body.phone, body.device_id, body.ref, body.qty_interest)
        return {"ok": ok}

    @app.get("/shop/upcoming")
    def shop_upcoming_list(user: CurrentUser = Depends(require_feature("Catalog"))) -> dict:
        """Admins / Shop Admin: every card with its interest count. A rep: the published ones only
        (for the share cards on Today)."""
        can_edit = user.role == "admin" or has_feature(user, "Shop Admin")
        return {"items": upcoming.list_admin(all_statuses=can_edit), "can_edit": can_edit,
                "settings": upcoming.settings()}

    @app.patch("/shop/upcoming/{item_id}")
    def shop_upcoming_update(item_id: int, body: UpcomingPatch,
                             user: CurrentUser = Depends(require_feature("Shop Admin"))) -> dict:
        """Publish / withdraw / mark arrived, set the expected month or labels, edit copy, link the
        catalog code. Publishing is an office action, so the gate is Shop Admin (admins pass)."""
        changes = body.model_dump(exclude_unset=True)
        try:
            row = upcoming.update_item(item_id, changes, by=user.email)
        except upcoming.UpcomingError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        log_event(user.email, "shop.upcoming_update", detail={"id": item_id, "keys": sorted(changes), "status": row.get("status")})
        return row

    @app.get("/shop/upcoming/interest")
    def shop_upcoming_interest(user: CurrentUser = Depends(require_feature("Shop Orders"))) -> dict:
        """"Shops interested from your link" — a rep sees interest that came through his referral
        code; admins see all of it."""
        _sid, admin = _scope(user)
        ref = None
        if not admin:
            sm = shop.salesman_for_user(user.email)
            ref = (sm or {}).get("referral_code") or "-"
        return {"interest": upcoming.list_interest(ref)}
