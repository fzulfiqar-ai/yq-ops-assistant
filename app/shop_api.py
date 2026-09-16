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
    from fastapi import BackgroundTasks, Depends, HTTPException, Request, Response
    from pydantic import BaseModel, Field

    from app import shop, shop_notify
    from app.audit import log_event
    from app.auth import CurrentUser, require_admin, require_feature
    from app.catalog import share_token
    from app.config import settings as cfg
    from app.shop import ShopError

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

    @app.get("/shop/orders/{order_id}")
    def shop_order_detail(order_id: int, user: CurrentUser = Depends(require_feature("Shop Orders"))) -> dict:
        sid, is_admin = _scope(user)
        o = shop.get_order(order_id)
        if not o or (not is_admin and o.get("salesman_id") != sid):
            raise HTTPException(status_code=404, detail="Order not found.")
        o["whatsapp_url"] = shop_notify.salesman_to_customer_wa_url(o)
        o["status_url"] = f"{shop._base_url()}/o/{o.get('token')}" if shop._base_url() else f"/o/{o.get('token')}"
        o["next_statuses"] = list(shop.NEXT_STATUS.get(o["status"], ()))
        o.pop("ip_hash", None)
        return o

    @app.post("/shop/orders/{order_id}/status")
    def shop_order_set_status(order_id: int, body: StatusRequest, background: BackgroundTasks,
                              user: CurrentUser = Depends(require_feature("Shop Orders"))) -> dict:
        sid, is_admin = _scope(user)
        cur = shop.get_order(order_id)
        if not cur or (not is_admin and cur.get("salesman_id") != sid):
            raise HTTPException(status_code=404, detail="Order not found.")
        try:
            o = shop.set_status(order_id, body.status, body.note, actor=user.email)
        except ShopError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        background.add_task(shop_notify.notify_status, order_id, body.status, body.note)
        log_event(user.email, "shop.order_status", detail={"order_id": order_id, "status": body.status})
        o["whatsapp_url"] = shop_notify.salesman_to_customer_wa_url(o, body.status)
        o["next_statuses"] = list(shop.NEXT_STATUS.get(o["status"], ()))
        o.pop("ip_hash", None)
        return {"ok": True, "order": o}

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
        vals = shop.update_shop_settings(body.settings, by=admin.email)
        log_event(admin.email, "settings.shop", detail={"keys": sorted(body.settings.keys())})
        return {"ok": True, "settings": vals}

    @app.get("/shop/margins")
    def shop_margins(_user: CurrentUser = Depends(require_feature("Shop Admin"))) -> dict:
        """Per-SKU landed cost vs trade price (live version of the owner's landed-cost workbook)."""
        return shop.margin_health()

    @app.get("/shop/unpriced-stock")
    def shop_unpriced_stock(_user: CurrentUser = Depends(require_feature("Shop Admin"))) -> dict:
        return {"rows": shop.unpriced_stock()}
