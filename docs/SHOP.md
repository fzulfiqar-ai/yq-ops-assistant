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
- **The sold-out rule (release R1, 24-Sep-2026).** A sold-out line stays on the shelf — same URL, same
  record, labelled **"Sold out"** (never "Out of stock"; Arabic «نفدت الكمية») — and sits after every
  available line on every listing (`app/shop.py _load_items` server-side, `web/src/market/lib/facets.ts`
  client-side, one comparator). Two switches decide whether it can still be ordered as a **backorder**:
  `shop_allow_backorder` for **merchants (marketplace + share link)** and `shop_allow_backorder_staff`
  for the salesman app (`/shop/*`, default `1`). With the merchant switch off, Add is replaced by
  **"Tell me when back"** (a restock request the rep sees; it is never sent without a phone number),
  nothing sold out reaches the cart from any path (cards, panel, palette ⇧Enter, Order again, Quick
  order — Enter never substitutes another SKU for a typed sold-out code), and a sold-out cart line is
  blocked at the quote with `Sold out — can't be ordered right now. Remove it to send your order.`
  A rep's confirmation re-price always keeps its backorder lines whatever either switch says.
  The shop never mutates stock; Focus stays the system of record and the salesman confirms every order.
- **"Sold out" is a verified zero.** The Focus *Stock balance by warehouse* report omits zero-balance items
  (checked read-only 24-Sep-2026: 0 rows with `net_qty <= 0` across the 13 `stock_balance` snapshots since
  June), so a catalog SKU absent from the latest snapshot has none. That reading is only as good as the
  snapshot is recent: older than `shop_stock_fresh_days` (default 3) the status still shows, with the
  snapshot date beside it ("Sold out · stock as of 21 Sep", the blocked reason "Sold out as of 21 Sep — …",
  `stock_fresh: false` in the payload). There is no "unknown" state.
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
Errors: `400 {"detail": "..."}` for a malformed or empty cart only. A line that cannot be ordered (unknown item,
qty below MOQ, out of stock with backorder off, no price) comes back in `lines[]` with `unavailable: true` and a
`blocked_reason`, priced at zero and left out of the totals — see "Catalog hygiene and dead cart lines" below.

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
  "email_url": "mailto:rep@pie-int.com?subject=…&body=…",  // same, by email; null when the salesman has no email on file
  "totals": { …quote shape… }, "has_backorder": false }
```
Rate limit 5/min per IP. Server re-prices and re-checks stock; the client's totals are never used.
Salesman routing: `?ref=` link → dropdown `salesman_id` → `shop_default_salesman` setting → owner.

### `GET /public/shop/order/{order_token}` — customer status page
```jsonc
{ "order_no": "YQ-2609-0001", "status": "new|confirmed|packed|delivered|cancelled",
  "created_at": "…", "updated_at": "…",
  "salesman": { "name": "Furqan Ahmed", "whatsapp_url": "https://wa.me/…?text=…",
                "email_url": "mailto:…?subject=…&body=…" | null } | null,
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
  "kpis": { "orders_7d", "orders_30d", "value_30d_bhd", "customers_30d" }, "focus": { "revenue_90d_bhd" } | null }`  (target_bhd retired 21-Sep-2026 -- targets return via `scripts/import_targets.py` into `salesman_targets(salesman, period)`)
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
  attribution: { by_referral[], by_src[], by_coupon[] }, daily[],
  search: { searches, zero_results, zero_rate_pct, terms[{term, searches, zero}], zero_terms[] },
  rails[{rail, clicks, sessions}], engagement: { search, share, install, reorder, cancel, checkout_start, devices },
  ops: { by_attribution[], unassigned_now, conflicts, sla_min, sla_breaches, median_time_to_confirm_min,
  cancelled_by_customer, cancelled_by_staff }, identity: { customers, repeat_customers, repeat_rate_pct,
  market_orders, staff_orders, legacy_orders }, vitals: { samples, lcp_ms_p75, inp_ms_p75, cls_p75 } }`
  (the marketplace learning loop, 16-Sep-2026; the market build posts one `vitals` event per visit).
- `GET /shop/margins` (Shop Admin) → `{ "rows": [{ item_code, spec, category, price_incl_vat_bhd, price_ex_vat_bhd,
  landed_cost_bhd, profit_bhd, margin_pct, markup_pct, floor_bhd, stock_status, sold_90d,
  status: ok|below_floor|no_cost|no_price }], "summary": { items, with_cost, below_floor, vat_rate, min_margin_pct } }`
  — the live replacement for the hand-built landed-cost workbook (sorted worst margin first).
- `GET /shop/unpriced-stock` (Shop Admin) → `{ "rows": [{ item_name, warehouse_name, stock_qty, value_bhd, matched_code,
  match_source, as_of_date }] }` — stock with no active catalog code (not in the current price book) = cannot be sold online.
- `GET /settings/shop` → `{ "settings": { shop_min_margin_pct, shop_vat_rate, shop_low_stock_units, shop_low_stock_days_cover,
  shop_allow_backorder, shop_allow_backorder_staff, shop_stock_fresh_days, shop_min_order_bhd, shop_free_delivery_threshold_bhd, shop_delivery_fee_bhd, shop_default_salesman,
  shop_order_prefix, shop_social_proof_min_customers, shop_show_retail_compare, shop_best_seller_top_n,
  shop_trending_growth_pct, shop_trending_min_units, shop_new_days } }` (all strings; booleans are "1"/"0");
  `PUT /settings/shop` body `{ "settings": {…partial} }` (admin).
- `GET /catalog` items now also carry `stock_status`, `moq`, `pack_size`; `POST /catalog/item` accepts `moq`, `pack_size`.

## Salesman mode — the second link (15-Sep-2026)
Two links, one app. The **catalog link** (`/c/{token}`) is for merchants and anyone else. The **portal login**
(`ops.yqmarketplace.com`, email + password) shows a `salesman`-role user exactly two tabs, phone-first:
**Catalog** (`/shop`) and **Orders** (`/shop-orders`). Admins keep the full sidebar and can also open `/shop`.

The salesman catalog is the merchant shop screen in *salesman mode*: same grid/cart, plus exact stock units,
the salesman pre-attached, and a checkout that places an order **for a shop** (customer details, quick-pick of
recent customers). Salesman-placed orders have `source = "salesman"` and `placed_by = <login email>`; the salesman
is not alerted about his own order (owner copy + customer confirmation still go out), and the success screen gives
him a one-tap WhatsApp confirmation to the shop instead.

Endpoints (bearer token, feature-gated):
- `GET /shop/catalog` (Catalog) → the public payload shape + per item `stock_qty` (exact units) + `mode: "salesman"`,
  `me: {salesman_id, salesman_name, is_admin}`, `ref` = the caller's own salesman row.
- `POST /shop/quote` (Catalog) body `{lines, coupon_code?}` → Quote.
- `POST /shop/order` (Catalog) body `{lines, coupon_code?, salesman_id? (admin only), customer:{name, phone, shop?, area?, email?}, note?}`
  → `{ok, order_id, order_no, token, status_url, salesman, whatsapp_url (TO the customer), email_url: null, totals, has_backorder, source: "salesman"}`.
- `GET /shop/customers` (Shop Orders) → `{customers: [{name, phone, shop, area, email, orders, last_order_at}]}`.
- `GET /shop/orders?status=confirmed,packed` — `status` accepts a comma list; the response now also carries
  `counts: {new, confirmed, packed, delivered, cancelled}` (scoped) and rows carry `placed_by`.
Buckets in the salesman UI: New = `new`; In progress = `confirmed,packed`; Done = `delivered,cancelled`.
Salesman default features are `Catalog` + `Shop Orders`; logins are created from the Team page (or in bulk by the owner).

## How a salesman hears about an order (three independent paths)
1. **The Shop Orders page** — the order is written to the database the moment it is submitted, so it is
   visible in the portal even if every message below fails. Nothing depends on a notification.
2. **The customer's own tap** — the success screen and the status page offer a WhatsApp link and a
   `mailto:` link, both pre-filled with the salesman's address/number and the whole order. These need no
   keys and work today; the send comes from the customer's own device, so the salesman can reply directly.
3. **Automatic server email/Telegram** (`app/shop_notify.py`) — fires by itself, with no customer action,
   as soon as an email provider (`RESEND_API_KEY` or SMTP) or `TELEGRAM_BOT_TOKEN` is set on the host. This
   is the only path that still reaches the salesman when the customer closes the tab without tapping anything.

## Setup
1. Apply `scripts/shop_migration.sql` (`python -m scripts.apply_sql scripts/shop_migration.sql`, needs `DATABASE_URL`
   in `.env` — Supabase → Settings → Database → Session pooler URI — or paste the file into the SQL editor).
2. Add salesmen on **Salesmen** (Shop Admin) — name, phone, email; copy their link / QR. Contact details live only in the DB.
3. Settings → Shop: minimum order, free-delivery threshold, low-stock thresholds, backorder on/off, margin floor, VAT rate.
4. Render env: an email provider (`RESEND_API_KEY` or SMTP), `ALERT_EMAIL_TO` (owner copy), optional Telegram, `APP_BASE_URL`.
5. Grant salesmen the **Shop Orders** feature (Team page) and link their login on the Salesmen page (`user_email`).
6. `python -m tests.test_shop` — pricing engine + live checks.

## Catalog hygiene and dead cart lines (15-Sep-2026)

- **Hidden SKUs.** `catalog_items.hidden = true` keeps a SKU in the item master and the price-book mirror but out of
  both catalogs (public link and salesman app). Used for the three display stands. Do not use `is_active` for this:
  the auto-sync mirrors `is_active` from the price book and would switch it back on at the next upload.
- **Categories.** One vocabulary, in shelf order: `app.catalog.CATEGORY_ORDER` (CABLE, CHARGER, CAR CHARGER,
  POWER BANK, EARPHONE, BLUETOOTH HEADSET, BLUETOOTH SPEAKER, CAR ACCESSORIES). New SKUs from the price book are
  filed by `app.catalog.classify_category(code, name, spec)`; the book's own category column is only the fallback.
  The shop payload lists items in this order.
- **No company phone number** in either catalog payload (the `whatsapp` key is gone). The merchant reaches his
  own salesman from the order confirmation.
- **Item codes are case-insensitive on input.** The catalog stores the book's spelling (`X05 UL-1Mtr`); quote,
  order and share lookups go through `shop.resolve_code()`. Before this, 23 mixed-case SKUs could not be ordered.
- **A line that cannot be ordered no longer fails the whole quote.** `POST …/quote` returns it in `lines[]` with
  `unavailable: true`, `blocked_reason` (`No longer in the catalog.`, `Price on request — ask your salesman.`,
  `Sold out — can't be ordered right now. Remove it to send your order.` when backorders are off (dated
  `Sold out as of 21 Sep — …` once the stock snapshot is stale), `Minimum order is N.`) and zero prices. Totals, `items` and `units`
  cover the orderable lines only. `can_submit` is false and `block_reason` says what to do next, in this order:
  remove the dead line(s), then reach the minimum order. `POST …/order` refuses with that same `block_reason`.
  An empty cart is still a 400.

## Speed (15-Sep-2026)

Measured on production before the change (4G phone profile): first product at ~6.5 s, the catalog request
starting only at 3.35 s, and the API answering in ~1.4 s even when awake. What changed:

- **API.** `shop.context()` is stale-while-revalidate: past its 60 s TTL the current copy is served at once and
  one background thread rebuilds it; only a cold process or `invalidate()` builds synchronously, so an owner's
  upload is still visible on the next request (a generation counter stops an in-flight refresh from overwriting a
  newer invalidation). The share token and the price date live in the context (they were two database calls on
  every request). `shop.public_catalog_json()` serializes the public payload once per refresh per salesman link,
  and `GET /public/catalog/{token}` returns those bytes with `Cache-Control: public, max-age=60,
  stale-while-revalidate=600`. Warm request locally: ~400 ms → ~15 ms. `rotate_share_token()` invalidates the
  shop context so a revoked link dies at once.
- **First request.** `web/public/catalog-prefetch.js` (loaded from `web/index.html` with the API base in `data-api`)
  starts the catalog fetch for `/c/{token}` before the app has downloaded; `getCatalog()` picks up that promise
  (`window.__yqCatalog`). It must stay a same-origin file: the CSP in `web/vercel.json` is `script-src 'self'`, which
  blocks inline scripts and inline event handlers. The first attempt used both (an inline script and a fonts
  `onload` swap), and in production that silently disabled the prefetch and stopped the brand fonts from loading.
- **Two front doors.** `web/src/main.tsx` sends `/c/` and `/o/` to `PublicApp` (shop + order status only: no
  supabase session client, no intro animation, no query cache) and everything else to `PortalRoot`. Entry bundle
  567 KB → 194 KB. `lib/shopApi.ts` loads `lib/api` lazily, only for the salesman calls.
- **Page.** A returning merchant's first paint is the copy saved on the phone (`yq-shop-catalog:{token}:{ref}`;
  the public payload has no quantities or costs), replaced by the live one when it lands; a 404 clears it. The
  visit/view pings fire after the catalog answers. The first commit draws 12 cards, the rest a frame later. Fonts
  load without blocking paint. Logo 82 KB PNG → 4 KB WebP.
- **Sold out last.** `_load_items` sorts sold-out SKUs after every in-stock one; the page keeps sold out last under
  every sort. The Sold out pill is red.
- **Still true:** Render free sleeps after 15 minutes idle (~50 s wake). `.github/workflows/keepalive.yml` pings
  every 10 minutes, but GitHub's scheduler can drift; only a paid instance removes cold starts entirely.

## Marketplace (16-Sep-2026) — the token-less front door, attribution, lifecycle

The catalog IS the marketplace: same payload, same pricing engine, same tables. What changed
(`scripts/marketplace_migration.sql`, applied 16-Sep-2026; plan in `~/.claude/plans`):

**Statuses** (DB value → what the merchant reads): `new` Received → `confirmed` Confirmed → `packed` Preparing
(the storekeeper issued the goods to the salesman; `issued_to_salesman_id`/`issued_at` are stamped) →
`out_for_delivery` On the way → `delivered` Delivered; `cancelled`. `packed` is optional (confirmed →
out_for_delivery is allowed). Each stage has its own timestamp column. `shop.NEXT_STATUS` is the transition
table; `shop.ROLE_STATUSES` limits the `storekeeper` role (new role + feature **Storekeeper**) to
`packed` / `out_for_delivery`; salesmen act on their own orders, admins on all.

**Who owns an order** (`shop.resolve_salesman`, stored in `shop_orders.attribution_source`): the merchant's admin
assignment (`shop_customers.salesman_id`) → a Focus mapping placeholder (`focus_salesman_name`) → the sticky
first-touch rep (`sticky_salesman_id`) while the last order is within `shop_sticky_days` (setting, 90) → the
`/{slug}` or `?ref=` this device arrived with (`session_ref`) → an explicit pick at checkout → the
`shop_default_salesman` setting → **unassigned** (queue). When the session's rep differs from the recorded
one the order is flagged `attribution_conflict` instead of switching silently; past the sticky window the live
link wins, still flagged. Every merchant-placed order upserts `shop_customers` by normalised phone
(write-if-blank profile, sticky rep written once, `device_ids`, counts); `shop_customer_phones` holds extra
numbers for one shop.

**Idempotency + abuse caps**: `POST /public/market/order` with `device_id` + `client_order_id` returns the same
order (`duplicate: true`) on a retry; `shop_phone_daily_cap` (10) and `shop_device_daily_cap` (20) refuse more
orders per 24 h; the per-IP limit is 10/minute on a proxy-aware key (`app/ratelimit.py`).

### Public marketplace API (no token in the URL; the share token is resolved server-side)
- `GET /public/market?ref={slug}` → the catalog payload (+ `rep` storefront card when the slug resolves, `has_tiers`
  per item, `settings.public_tiers`, `settings.areas`). `Cache-Control: public, max-age=60,
  stale-while-revalidate=600, stale-if-error=86400`; every `/public/*` answer carries `X-Robots-Tag: noindex`.
  404 when `shop_market_enabled` is 0. 60/min.
- `GET /public/rep/{slug}` → `{slug, salesman_id, name, first_name, title, photo_url, whatsapp_url|null}`;
  reserved words (`shop_reserved_slugs`) and unknown slugs are 404. WhatsApp only when the rep set `public_whatsapp`.
- `POST /public/market/quote` — same body/answer as the token quote.
- `POST /public/market/order` — `OrderRequest` + `device_id`, `client_order_id`, `session_ref` →
  `{ok, duplicate, order_no, token, status_url, status, status_label, assigned, attribution, salesman{name,first_name}|null,
  whatsapp_url, email_url, totals, has_backorder}`. 10/min per IP.
- `POST /public/market/event` — events v2: `event` in view|item|add|checkout|order|search|search_zero|remove|qty|cart|
  checkout_start|rail_click|reco_click|share|install|reorder|cancel|vitals|push_subscribe, plus `device_id`,
  `customer_id`, `meta` (kept keys: q, rail, pos, results, count, value, lcp, inp, cls, reason, from, to, code). 240/min.
- `GET /public/shop/order/{token}` now also returns `status_label`, `steps[]` (five stages with done/current/at),
  `can_cancel`, `expected_delivery`, `total_confirmed_bhd`, `has_changes`, per line `qty_confirmed` + `line_status`.
- `POST /public/shop/order/{token}/cancel` `{reason?}` — while `new` only; 3/hour.
- `POST /public/shop/my-orders` `{tokens: [≤20]}` → summaries for the tokens a device holds ("My orders" without an account).

### Portal additions (bearer + feature)
- `POST /shop/orders/{id}/confirm` `{lines:[{line_id, qty_confirmed?, line_status?: 'removed', note?}], expected_delivery?, note?}`
  → confirms with changes, re-prices at the confirmed quantities (`subtotal/total_confirmed_bhd`), returns
  `changed[]`, `removed[]`, `totals`, a prefilled WhatsApp to the merchant, `next_statuses`.
- `POST /shop/orders/{id}/assign` `{salesman_id, reason?}` — admins reassign any open order; a salesman may only take an
  unassigned one for himself. First assignment sets the merchant's sticky rep when none is recorded. Notifies the rep.
- `GET /shop/assignment-queue` → unassigned open orders with `age_min` and a suggestion (rep who served the phone
  before, else most orders in the area in 90 days).
- `GET /shop/picklist?salesman_id=` (Storekeeper or Shop Orders) → confirmed/preparing orders grouped by salesman with
  CONFIRMED quantities + `totals_by_item`.
- `GET/POST /shop/orders/{id}[/status]` accept the storekeeper (all orders, limited statuses) and return `steps`,
  `status_label`, role-filtered `next_statuses`.
- `salesmen` gains `title`, `photo_url`, `public_profile`, `public_whatsapp`; a `referral_code` that is a reserved
  marketplace address is refused.
- Setting `shop_market_url` (the marketplace origin): when set, `shop.salesman_link()` returns `{market}/{slug}`
  (the QR encodes it) and `shop.market_base()` drives every merchant-facing URL (`status_url`, tracking links in
  emails and WhatsApp). Empty = legacy `/c/{token}?ref=` on `APP_BASE_URL`. Portal screens: `docs/MARKETPLACE.md`.
- `GET /scheduler/shop-jobs` (X-Agent-Key) — unassigned reminders after `shop_assign_sla_min` (re-alert every 2 h)
  + session cleanup; called every 15 min by `.github/workflows/shop-cron.yml`.

### Views for the learning loop
`v_customer_regulars` (Focus cadence per merchant × SKU: times bought, median qty, cadence days, due flag — service
role only, carries customer names), `v_shop_assignment_queue`, `v_shop_search_terms`, `v_shop_rail_perf`
(PII-free, granted to `yq_readonly`). `v_shop_orders_agent` gained appended columns.

### Verify
`python -m tests.test_shop` (attribution matrix, lifecycle, storefront card, tiers switch, limiter) and
`python -m scripts.audit_grants` (exit 0). The in-process smoke on 16-Sep-2026 walked: market → rep card →
idempotent order → my-orders → confirm 2→1 (1.500→0.750) → storekeeper refused `delivered`, allowed `packed`
(goods issued to the rep) → on the way → delivered → customer cancel refused; second order cancelled by the
customer; third order unassigned → queue → admin assign → merchant's sticky rep set.
