# YQ Shop — shareable ordering catalog

The public catalog link (`/c/{token}`) is now an ordering system: customers browse live prices and
stock status, fill a cart, choose (or arrive with) a salesman, and submit. Orders are routed to the
salesman by email + WhatsApp and appear in the portal under **Shop Orders**.

Everything is derived from the same Focus uploads that already drive the portal: prices from the
price book (`v_catalog` → `v_price_list_by_book`), stock from the latest `stock_balance` snapshot
(`v_catalog_stock`), landed cost from MRN uploads (`v_catalog_cost` / `mrn_landed_costs`), velocity and
co-purchases from `v_sales` (`v_catalog_velocity`, `v_catalog_pairs`). No manual sync step exists.
Quantities and costs never leave the server.

Code: `app/shop.py` (payload, pricing engine, orders, salesmen, rules, margins), `app/shop_notify.py`
(email / Telegram / WhatsApp), `app/shop_api.py` (routes, registered from `app/main.py`),
`scripts/shop_migration.sql`, `tests/test_shop.py`, `web/src/pages/shop/*` (public UI),
`web/src/pages/{ShopOrders,Salesmen,ShopRules,OrderStatus}.tsx`.

## Rules baked in
- Stock is shown as a status only: `in_stock` / `low_stock` ("Only a few left", units ≤ `shop_low_stock_units`)
  / `out_of_stock`. Never a number. A `selling_fast` badge appears when days of cover (90-day velocity)
  drop below `shop_low_stock_days_cover`.
- Out-of-stock items can still be ordered as **backorder** when `shop_allow_backorder=1`. The shop never
  mutates stock; Focus stays the system of record and the salesman confirms every order.
- All prices/discounts are computed server-side (`app/shop.py::price_cart`); client totals are ignored.
- **Margin floor:** no rule/coupon/tier may price a unit below
  `landed_cost × (1 + shop_min_margin_pct) × (1 + shop_vat_rate)` (price-book rates are VAT-inclusive).
  The discount is clamped and the breach is logged; `POST /shop/rules/preview` shows breaches before saving.
- Discount precedence: item-level rules first (quantity tiers, salesman offers, bundle prices — best
  non-stackable, then stackable ones), then ONE cart-level discount: the better of the automatic
  cart-value rule and the coupon, unless a rule is marked stackable.
- Ethical urgency only: badges and countdowns come from real data (velocity, real `ends_at`, real stock).

## Public API (no login; token-gated + rate-limited)

### `GET /public/catalog/{token}?ref={referral_code}`
Backward compatible with the old payload (all old keys kept). New keys:

```jsonc
{
  "company": "YQ Bahrain", "brand": "VFAN",
  "prices_updated": "2026-09-06", "stock_as_of": "2026-09-14",
  "whatsapp": "97337497693",                       // owner number — fallback CTA
  "categories": ["CABLE", "CHARGER", ...],
  "items": [{
    "item_code": "T02", "display_name": "T02", "spec": "T02 Aipord ...", "category": "BLUETOOTH HEADSET", "brand": "VFAN",
    "price_bhd": 2.95,                             // trade (B2B) price — what the customer pays
    "b2c_bhd": null,                               // retail price — null unless shop_show_retail_compare=1 (owner: B2B only)
    "compare_at_bhd": 6.0, "save_pct": 51,         // retail compare-at when > trade and setting on, else null
    "product_image_url": "...", "package_image_url": "...", "thumb_url": "...|null",
    "stock_status": "in_stock" | "low_stock" | "out_of_stock",
    "moq": 1, "pack_size": null,
    "tiers": [{ "min_qty": 12, "unit_price_bhd": 2.7, "label": "12+ → BHD 2.700", "rule_id": 5 }],
    "badges": ["best_seller" | "trending" | "new" | "on_offer" | "selling_fast"],
    "social_proof": "Ordered by 12 shops this month" | null
  }],
  "salesmen": [{ "id": 1, "name": "Furqan Ahmed", "referral_code": "furqan" }],
  "offers": [{ "id": 3, "name": "Weekend cable deal", "kind": "cart_value", "summary": "5% off on orders over BHD 100.000",
               "ends_at": "2026-09-21T20:59:00+00:00" | null, "min_value_bhd": 100, "min_qty": null, "pct_off": 5,
               "amount_off_bhd": null, "coupon_code": null, "scope_codes": [], "scope_categories": [] }],
  "pairs": [{ "item_code": "T02", "with": ["X24 CC", "UK04"] }],     // frequently bought together (top 3)
  "settings": { "currency": "BHD", "min_order_bhd": 0, "free_delivery_threshold_bhd": 0, "delivery_fee_bhd": 0,
                "allow_backorder": true, "show_retail_compare": true },
  "ref": { "referral_code": "furqan", "salesman_id": 1, "salesman_name": "Furqan Ahmed" } | null
}
```

### `POST /public/shop/{token}/quote`
Body: `{ "lines": [{ "item_code": "T02", "qty": 12 }], "coupon_code": "", "referral_code": "" }`
Returns the priced cart (the same shape is embedded in the order response as `totals`):

```jsonc
{
  "ok": true,
  "lines": [{ "item_code": "T02", "display_name": "T02", "spec": "…", "image_url": "…", "qty": 12, "moq": 1,
              "list_price_bhd": 2.95, "unit_price_bhd": 2.7, "discount_bhd": 3.0, "line_total_bhd": 32.4,
              "stock_status": "in_stock", "backorder": false,
              "applied": [{ "rule_id": 5, "name": "Buy 12+", "kind": "qty_tier" }], "warning": null }],
  "subtotal_bhd": 35.4, "discount_bhd": 3.0, "delivery_bhd": 0, "total_bhd": 32.4, "units": 12, "items": 1,
  "discounts": [{ "rule_id": 5, "name": "Buy 12+", "kind": "qty_tier", "amount_bhd": 3.0 }],
  "coupon": { "code": "WELCOME5", "valid": true, "message": "5% off applied" } | null,
  "progress": { "kind": "free_delivery" | "cart_value", "threshold_bhd": 100, "remaining_bhd": 67.6,
                "unlocked": false, "label": "Add BHD 67.600 more for free delivery" } | null,
  "warnings": ["X24 CC is out of stock — it will be backordered and confirmed by your salesman."],
  "can_submit": true, "min_order_bhd": 0, "has_backorder": false,
  "block_reason": null                                  // human-readable reason when can_submit is false
}
```
Errors: `400 {"detail": "..."}` (unknown item, qty below MOQ, out of stock when backorder is off, …).

### `POST /public/shop/{token}/order`
Body:
```jsonc
{ "lines": [{ "item_code": "T02", "qty": 12 }], "coupon_code": "", "referral_code": "furqan",
  "salesman_id": 1,                                 // from the dropdown (optional when referral_code given)
  "customer": { "name": "Ali", "phone": "33001122", "shop": "Ali Mobiles", "area": "Manama", "email": "" },
  "note": "Deliver Tuesday", "src": "outreach-12", "session_id": "…", "website": "" }   // website = honeypot, must be empty
```
Response:
```jsonc
{ "ok": true, "order_no": "YQ-2609-0001", "token": "…", "status_url": "https://…/o/{token}",
  "salesman": { "name": "Furqan Ahmed", "phone": "97337158552" },   // owner contact when no salesman resolved
  "whatsapp_url": "https://wa.me/97337158552?text=…",   // customer taps to send the order summary to the salesman
  "totals": { …quote shape… }, "has_backorder": false }
```
Rate limit 5/min per IP. Server re-prices and re-checks stock; the client's totals are never used.
Salesman routing: `?ref=` link → dropdown `salesman_id` → `shop_default_salesman` setting → owner.

### `GET /public/shop/order/{order_token}` — customer status page
```jsonc
{ "order_no": "YQ-2609-0001", "status": "new|confirmed|packed|delivered|cancelled",
  "created_at": "…", "updated_at": "…",
  "salesman": { "name": "Furqan Ahmed", "whatsapp_url": "https://wa.me/…?text=…" } | null,
  "customer": { "name": "Ali", "shop": "Ali Mobiles", "area": "Manama" },
  "lines": [{ "item_code": "T02", "display_name": "T02", "qty": 12, "unit_price_bhd": 2.7, "line_total_bhd": 32.4,
              "stock_status": "in_stock", "backorder": false, "image_url": "…" }],
  "subtotal_bhd": 35.4, "discount_bhd": 3.0, "delivery_bhd": 0, "total_bhd": 32.4, "has_backorder": false, "note": "…",
  "timeline": [{ "ts": "…", "event": "created" | "status:confirmed" | …, "note": null }] }
```

### `POST /public/shop/{token}/event` — funnel ping (fire-and-forget)
Body `{ "event": "view|item|add|checkout", "item_code": "", "referral_code": "", "src": "", "session_id": "" }` → `{ "ok": true }`.

### `GET /public/share/{token}/{item_code}?ref={code}`
HTML page with Open Graph + JSON-LD `Product` (title = code · price · availability), then redirects to
`/c/{token}?item={code}&ref={code}` — share THIS URL on WhatsApp for a rich preview card.

## Portal API (login required)
- `GET /shop/orders?status=&q=&limit=50&offset=0` (feature **Shop Orders**; salesman role sees only orders whose
  salesman is linked to their login via `salesmen.user_email`) → `{ "orders": [{ id, order_no, status, customer_name,
  customer_phone, customer_shop, customer_area, salesman_id, salesman_name, total_bhd, items_count, units_count,
  has_backorder, created_at, updated_at, source, referral_code, coupon_code }], "count": n, "hint"?: "…" }`
- `GET /shop/orders/{id}` → the full order row (`subtotal_bhd`, `discount_bhd`, `delivery_bhd`, `total_bhd`, `coupon_code`,
  `customer_email`, `note`, `notify_result`, …) + `lines[]` + `events[]` + `salesman` + `whatsapp_url` (prefilled message to
  the customer) + `status_url` + `next_statuses[]`
- `POST /shop/orders/{id}/status` body `{ "status": "confirmed", "note": "" }` → `{ "ok": true, "order": {…} }`
  (allowed: new→confirmed|cancelled, confirmed→packed|delivered|cancelled, packed→delivered|cancelled)
- `GET /shop/me` → `{ "salesman": {…}|null, "link": "https://…/c/{token}?ref=furqan", "qr_url": "/shop/salesmen/1/qr.png",
  "kpis": { "orders_7d", "orders_30d", "value_30d_bhd", "customers_30d" }, "focus": { "revenue_90d_bhd", "target_bhd" } | null }`
- `GET /shop/salesmen` (feature **Shop Admin**) → `{ "salesmen": [{ id, name, phone, email, whatsapp, user_email, focus_name,
  referral_code, is_active, sort_order, notify_email, notify_whatsapp, link, orders_30d }] }`;
  `POST /shop/salesmen` (same fields, `name` required; phone/whatsapp normalised to wa digits), `PATCH /shop/salesmen/{id}`,
  `DELETE /shop/salesmen/{id}`, `GET /shop/salesmen/{id}/qr.png` (PNG; salesmen may fetch their own).
- `GET /shop/rules` (Shop Admin) → `{ "rules": [{ id, name, kind, scope: {item_codes[], categories[], referral_codes[]},
  min_qty, min_value_bhd, pct_off, amount_off_bhd, fixed_price_bhd, coupon_code, stackable, starts_at, ends_at, max_uses,
  uses, priority, is_active, summary, status: live|scheduled|expired|exhausted|inactive }] }`;
  `POST /shop/rules`, `PATCH /shop/rules/{id}` (any subset of those fields; exactly one of pct_off / amount_off_bhd /
  fixed_price_bhd; `qty_tier` needs `min_qty ≥ 2`; `cart_value` needs `min_value_bhd`; `coupon` needs `coupon_code`;
  `salesman_offer` needs `scope.referral_codes`; `bundle_price` needs `scope.item_codes`) → the saved rule +
  `impact: { items, breach_count, breaches: [{ item_code, unit_bhd, floor_bhd }] }`; `DELETE /shop/rules/{id}`;
  `POST /shop/rules/preview` (same body, nothing saved) → `{ rule, summary, impact }`.
- `GET /shop/analytics?days=30` (feature Shop Orders; salesmen get their own slice) → `{ days, since, funnel: { sessions,
  item_views, adds, checkouts, orders, conversion_pct }, orders, cancelled, value_bhd, aov_bhd, units, customers,
  backorder_rate_pct, top_products[], leaderboard[] (per salesman: orders, value_bhd, customers, aov_bhd),
  attribution: { by_referral[], by_src[], by_coupon[] }, daily[] }`.
- `GET /shop/margins` (Shop Admin) → `{ "rows": [{ item_code, spec, category, price_incl_vat_bhd, price_ex_vat_bhd,
  landed_cost_bhd, profit_bhd, margin_pct, markup_pct, floor_bhd, stock_status, sold_90d,
  status: ok|below_floor|no_cost|no_price }], "summary": { items, with_cost, below_floor, vat_rate, min_margin_pct } }`
  — the live replacement for the hand-built landed-cost workbook (sorted worst margin first).
- `GET /shop/unpriced-stock` (Shop Admin) → `{ "rows": [{ item_name, warehouse_name, stock_qty, value_bhd, matched_code,
  match_source, as_of_date }] }` — stock with no active catalog code (not in the current price book) = cannot be sold online.
- `GET /settings/shop` → `{ "settings": { shop_min_margin_pct, shop_vat_rate, shop_low_stock_units, shop_low_stock_days_cover,
  shop_allow_backorder, shop_min_order_bhd, shop_free_delivery_threshold_bhd, shop_delivery_fee_bhd, shop_default_salesman,
  shop_order_prefix, shop_social_proof_min_customers, shop_show_retail_compare, shop_best_seller_top_n,
  shop_trending_growth_pct, shop_trending_min_units, shop_new_days } }` (all strings; booleans are "1"/"0");
  `PUT /settings/shop` body `{ "settings": {…partial} }` (admin).
- `GET /catalog` items now also carry `stock_status`, `moq`, `pack_size`; `POST /catalog/item` accepts `moq`, `pack_size`.

## Setup
1. Apply `scripts/shop_migration.sql` (`python -m scripts.apply_sql scripts/shop_migration.sql`, needs `DATABASE_URL`
   in `.env` — Supabase → Settings → Database → Session pooler URI — or paste the file into the SQL editor).
2. Add salesmen on **Salesmen** (Shop Admin) — name, phone, email; copy their link / QR. Contact details live only in the DB.
3. Settings → Shop: minimum order, free-delivery threshold, low-stock thresholds, backorder on/off, margin floor, VAT rate.
4. Render env: an email provider (`RESEND_API_KEY` or SMTP), `ALERT_EMAIL_TO` (owner copy), optional Telegram, `APP_BASE_URL`.
5. Grant salesmen the **Shop Orders** feature (Team page) and link their login on the Salesmen page (`user_email`).
6. `python -m tests.test_shop` — pricing engine + live checks.
