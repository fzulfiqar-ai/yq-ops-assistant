# YQ Marketplace — the merchant front door (v2, 16-Sep-2026)

The marketplace is the existing `/c/{token}` shop, enhanced: same API (`app/shop*.py`), same
tables, same pricing engine. What is new is the front door (a root URL and `/{salesman}` storefronts
instead of a token link), a discovery-first home, a B2B cart and one-screen checkout, tracking with
five stages, merchant memory on the device — and, since v2, its own design system, shell and
performance path (this file). API contract: `docs/SHOP.md § Marketplace`. Design plan and decisions:
`~/.claude/plans/you-are-the-lead-wild-wilkinson.md`.

## One codebase, two builds

| Build | Entry | Stylesheet / Tailwind | Owns | Hosting |
|---|---|---|---|---|
| portal (default) | `web/src/main.tsx` → `PortalRoot` / `PublicApp` | `src/index.css` · `tailwind.config.js` | `yq-bahrain-ops.vercel.app`, `/c/{token}`, `/o/{token}` | Vercel `yq-bahrain-ops` |
| **market** (`VITE_APP=market`) | **`web/src/main.market.tsx`** → `MarketApp` | **`src/market/market.css` · `tailwind.market.config.js`** | `/`, `/{slug}`, `/p/{code}`, `/t/{category}`, `/shop`, `/search`, `/quick`, `/cart`, `/checkout`, `/orders`, `/me`, `/o/{token}`, `/c/{token}` (redirect) | Vercel `yq-marketplace` |

`web/vite.config.ts → marketHtml()` rewrites the shared `index.html` for the market build: the entry
(`main.market.tsx`, one network hop less), `<html lang dir data-app="market">`, `viewport-fit=cover`,
the plum theme colour, `color-scheme: light`, `noindex`, the manifest, **self-hosted font preloads
instead of the Google Fonts stylesheet**, a preconnect to the storage origin, and `data-app="market"`
on the prefetch script. PostCSS is configured inline so the market build compiles
`tailwind.market.config.js` (market glob only) while the portal keeps its config untouched. Market
chunks are named (`react`, `router`, `search`) for the budget check. Scripts: `npm run dev:market`,
`build:market`, `preview:market` (`web/.env.market` sets `VITE_APP=market`).

**Reserved first URL segments** (a salesman slug may never take them) live in four places and must
stay equal: `web/src/MarketApp.tsx` (`RESERVED`), `web/public/catalog-prefetch.js`, `app/shop.py`
(`_RESERVED_FALLBACK`) and the `shop_reserved_slugs` table (`scripts/marketplace_reserved_quick_migration.sql`
added `quick`, `fonts`).

## Design system ("The YQ Counter")

- **Tokens** — `web/src/market/market.css`, `--m-*` HSL triplets: plum `#6D4091` (sampled from the
  logo; the portal's `#6d28d9` is not used), deep ink, a warm off-white canvas, hairlines, `ok/warn/bad`
  tints, radii 6/10/14/18/24, ink-tinted shadows, `--m-gutter: clamp(12px, 3.2vw, 48px)`,
  `--m-content-max: 1600px`, `--m-safe-b` (every safe-area read goes through it), `--m-bottom-stack`
  (measured by the shell). Light only (`color-scheme: only light`).
- **Type** — Instrument Sans (UI, tabular figures) + Sora (display: titles, prices), variable woff2 in
  `web/public/fonts/` (OFL files alongside), preloaded, `font-display: swap` with metric-matched
  fallbacks (`size-adjust`/`ascent-override` computed with fontTools) so the swap causes no CLS.
- **Primitives** — `web/src/market/ui/`: `Button` (primary / secondary / ghost / danger / wa; 40–56px),
  `Chip`, `Stepper` (hold-to-repeat, tick animation, keypad on the number), `Sheet` (bottom sheet /
  dialog / side drawer, drag-to-dismiss, exit animation, focus trap), `ProductImage` (srcset from
  `thumb_urls`, reserved aspect ratio, fallback chain), `Field`/`Select`, `Skeleton`, `Toast` (with
  Undo action), `ErrorBoundary`. The market never imports portal UI — enforced by the
  `no-restricted-imports` block in `web/eslint.config.js`.
- **Motion** — CSS/WAAPI only (no animation library in the merchant bundle): nav pill slide, Add →
  "Added" → stepper morph, stepper tick, cart badge bump, total crossfade, sheet/drawer transitions,
  skeleton shimmer, order-placed check draw, hero pointer tilt (fine pointers only), View Transition
  on the product photo (phones, where supported). `prefers-reduced-motion` collapses everything.

## Shell (`web/src/market/shell/`)

`Shell` is the layout route. `useViewport()` picks `phone <768 · tablet 768–1023 · desktop 1024–1279 ·
wide ≥1280` synchronously from `matchMedia`.

- **Phone** — `PhoneHeader` (mark, wordmark, rep avatar → My YQ; back + title on sub-pages),
  the page, and the bottom stack: `[page bar] [CartDock] [FloatingNav]`. `FloatingNav` is a glass
  pill with five tabs — **Home · Shop · Search · Cart (live badge) · My YQ** — and a sliding active
  pill; the stack hides on scroll-down and returns on scroll-up (never while a page bar is showing).
  `CartDock` is the running order ("3 products · 42 pcs · BHD 39.600 → Review") on browsing pages.
- **Tablet / desktop** (`DesktopShell`, lazy — phones never download it) — `StickyHeader` (logo ·
  **Shop** mega-nav with category preview + facet chips + four best sellers with Add · large search
  that opens the ⌘K palette · New · Offers · Clearance · Quick order · Orders · My YQ · Cart with the
  running total), a category strip on Home/Shop, content in a fluid 1600px container, and from 1280px
  a persistent **MiniCart** aside (drawer below 1280). Grids run 4–6 columns.
- **Overlays** — `ShellContext.openProduct(code)` pushes history state so the `ProductPanel`
  (sheet / dialog / right drawer) opens over whichever page is showing and Back closes it;
  `/p/{code}` deep links still work. `SearchPalette` opens with ⌘K / Ctrl+K / `/`.
- **Page bars** — pages hand their sticky action (`Place order`, `Add all`) to the shell with
  `<PageBar>`; the shell stacks it above the nav and writes the measured height into
  `--m-bottom-stack`, so nothing is ever hidden under the nav.

## Code map (`web/src/market/`)

- `MarketContext.tsx` — `useMarket()` (catalog from the phone's saved copy then the live payload,
  attribution, lazy MiniSearch index, cart actions with quantity memory) and `useOrder()` (server
  quote, coupon, note, my orders). Cart lines live in `store/cart.ts` (external store, key
  `yq-shop-cart:market`) so an add re-renders one card, not the grid.
- `lib/format.ts` — money/dates/quantity rules, badge order, **`productName()` / `productDetail()`**
  (many `display_name`s are just the code; the real name is derived from `spec` and the "(VFAN)"
  suffix dropped), `priceAnchor()` (the struck-through price is only ever a REAL number: a recent
  trade-price cut, else the retail price). `lib/facets.ts` — sub-facets inferred from product text
  (connector, cable type, length, wattage, ports, capacity, TWS/neckband…), sort and quick filters.
  `lib/home.ts` — the home page's merchandising rules. `lib/quickParse.ts` — "24 x C18, 12 UK15,
  tws 6" → rows. `lib/searchGroups.ts` — grouped results (Codes · Products · Categories).
  `lib/device.ts` — what the phone remembers (reads cached in memory).
- `components/` — `MarketCard` (grid / compact / list; `Add · 12` → ✓ Added → stepper; Undo on
  remove; quick-view on hover), `Rail` (scroll-snap on touch, static row on desktop), `ProductPanel`,
  `QtySheet`, `SearchResults`, `SearchPalette`, `SearchField` (visible submit), `HomeBlocks`
  (mission strip, category tiles, offer strip, track card, product hero with "Also popular", Order
  again hero, unfinished-order card), `RepCard`, `ProgressBar`, `States`, `CheckMark`, `UpdateToast`.
- `pages/` — `Home` (two-phase render: top zone first, rails + grid on idle), `ShopPage` (tiles +
  New / Offers / Price drops / Clearance / Quick order entries; with `?f=` it is the "See all" shelf),
  `CategoryPage` → `GridPage` (facets, sort, filters, grid/list, all in the URL), `SearchPage`
  (instant grouped results with inline Add; Enter on an exact code adds it), `QuickOrderPage`
  (rows, paste a list, last order / saved lists, Add all), `CartPage` (two columns on desktop),
  `CheckoutPage` (area chips, Email under More, nav hidden), `TrackingPage` (confirmation +
  stages), `MyOrdersPage`, `MyYQPage` (rep, quick actions, orders, saved details, install, EN|AR).

## Home (block order)

Search (phone) → mission strip (Order again · Quick order · New · Offers · Price drops · Clearance)
→ unfinished-order card (once per session, when the cart has lines) → 8 category tiles → offer strip
(real `ends_at` only) → open-order card → **hero** (the top best seller; for a recognised merchant the
last order with "Add all") → rep card → rails: Your regular stock · Best sellers · Just arrived ·
Price drops · Clearance · On offer · Picked up again (each ≥3 items, capped 10, de-duplicated against
the grid, "See all" → the matching shelf) → All products (In stock / Offers, grid or list) → footer.

## Merchandising signals (backend, `app/shop.py`)

| Badge / field | Rule | Setting |
|---|---|---|
| `best_seller` | top N by 90-day units per category | `shop_best_seller_top_n` |
| `trending` | 30-day units ≥ min and growth vs previous 30 days | `shop_trending_*` |
| `new` | `catalog_items.created_at` within N days | `shop_new_days` |
| `selling_fast` | days of cover below N | `shop_low_stock_days_cover` |
| `on_offer` | an active item-scoped discount rule | — |
| **`price_drop` + `was_bhd`** | a REAL trade-price cut in `v_price_change` within N days, shown only while the live price still equals the cut price | `shop_price_drop_days` (30) |
| **`clearance`** | stock ≥ min units and days of cover ≥ N (never a best seller / trending / new), the worst M first | `shop_clearance_min_units` (12), `shop_clearance_days_cover` (365), `shop_clearance_max` (24) |
| retail anchor | the price book's B2C rate struck through above the trade price — everywhere when `shop_show_retail_compare` is on, on Clearance items also when it is off | `shop_clearance_show_retail` (1) |

No invented anchors, timers or scarcity: every struck-through price is a price that exists in the
price book, and "Only a few left" comes from stock.

## Images

`app/catalog.py` writes, per photo, the legacy 256px JPEG thumb plus **160/320/512 WebP**
(`thumbs/{code}-{kind}-{size}.webp`, one-year cache metadata); the payload carries `thumb_urls` and
`ProductImage` builds a `srcset` from it (older cached payloads fall back to `thumb_url`).
`public/catalog-prefetch.js` preloads two responsive images (the hero and the first rail card) as
soon as the catalog JSON lands. Backfill: `python -m scripts.make_market_thumbs [--only-missing]`
(360 files built on 16-Sep-2026; 320px averages 4.8 KB vs 6.8 KB for the 256 JPEG). Icons:
`python -m scripts.make_market_icons` (any + maskable + shortcuts) → `web/public/`.

## Deploying the marketplace (second Vercel project) — LIVE since 16-Sep-2026

- **Production:** `https://yq-marketplace.vercel.app` — Vercel project `yq-marketplace` (team
  `fzulfiqar-ai-s-projects`), environment `VITE_APP=market`,
  `VITE_API_URL=https://yq-ops-assistant.onrender.com`. The market build imports no Supabase code, so
  it needs no `VITE_SUPABASE_*` values.
- **Deploy from the developer machine:** `python -m scripts.deploy_web market` (or `portal`). Both
  projects are CLI-deployed from `web/`; the script swaps the CLI link, deploys, and restores it.
  Vercel builds with the project's env, so a local `.env` never leaks into the bundle.
- **Render:** `ALLOWED_ORIGINS` includes `https://yq-marketplace.vercel.app`. Add the custom domain
  there too when it exists, otherwise every API call is refused by CORS. Render's `autoDeploy` flag is
  on but pushes do not reach it (no GitHub-app webhook); deploys are triggered through the Render API.
- **Portal setting:** `shop_market_url = https://yq-marketplace.vercel.app` (Settings → Shop), so
  salesman links/QR and tracking URLs point at the marketplace.
- **Custom domain (later):** attach the root domain to `yq-marketplace` and `ops.` to
  `yq-bahrain-ops`; add both to `ALLOWED_ORIGINS`; update `shop_market_url`; print QR codes only then.
- **Verify after a deploy:** `/version.json` changes build id, `/sw.js` is `no-cache`, `/robots.txt`
  disallows all, `/` and `/furqan` render on a phone, `python -m scripts.audit_grants` exits 0.

Local: `cd web && npm run build:market && npm run preview:market` (port 5174) against a local
`uvicorn app.main:app --port 8001` (`web/.env` points `VITE_API_URL` there).

## Visual QA harness

`scratchpad/market_qa.py` (Python Playwright; Chromium is cached on the dev machine) boots against the
preview or `--base-url` production, walks 14 routes at 10 viewports (320 → 1920, an iPhone context
with safe-area insets injected, reduced motion) and asserts: no horizontal overflow, tap targets
(hard fail < 36px, warn < 44px), inputs ≥ 16px on touch, the last Add / page-bar button hit-testable
above the nav, zero console/page/request errors, market fonts loaded and no Google Fonts request, the
HTML transform applied, nav hide/show, no portal font leak. Screenshots land in `scratchpad/qa/shots`.
In Git Bash run it with `MSYS_NO_PATHCONV=1`. Static gates: `npx tsc -b && npx eslint . && npm run
build:market && npm run build` (the portal must keep building); `python -m tests.test_shop` and
`python -m scripts.audit_grants` after any backend/SQL touch.

## Performance

Same machine, Lighthouse mobile, simulated slow 4G, CPU 4× (this machine reads about 2× slower than
the CI runner, where the first deploy scored 64):

| | production (v1) | v2 |
|---|---|---|
| Performance / Accessibility / Best practices | 39 / — / — | 45 / 92 / 100 |
| LCP · FCP · TBT · Speed Index · CLS | 5.6 s · 4.3 s · 2.57 s · 5.0 s · 0.001 | 5.0 s · 4.4 s · 1.76 s · 3.9 s · 0 |
| Critical JS (gz) · CSS (gz) · fonts | 140 KB · 13.9 KB · Google Fonts (blocking) | ≈120 KB · 9.7 KB · 64 KB self-hosted, preloaded |

What moved it in v2: the static market entry (one round trip less), self-hosted preloaded fonts,
market-only CSS, MiniSearch off the critical path, memoised device reads, the two-phase Home render
(the first commit is the top zone only), `content-visibility` on cards below the fold, responsive
WebP thumbnails with long cache. LCP is still bound by "catalog JSON → first photo" on a cold visit;
the remaining levers are an image CDN in front of the bucket (Track B) and a warm API.
`.github/lighthouserc.json` budgets: perf ≥ 0.7, a11y ≥ 0.9, LCP ≤ 2.5 s, TBT ≤ 300 ms, CLS ≤ 0.05,
script ≤ 180 KB, stylesheet ≤ 35 KB, font ≤ 95 KB, TTI ≤ 3.5 s (warn-only, weekly workflow).

## Portal side (built 16-Sep-2026)

- **Shop Orders (desk)**: an *Unassigned orders* panel at the top (from `GET /shop/assignment-queue`,
  refreshed every minute) with a suggested rep and one-click Assign; `/shop-orders?queue=1` (the
  Telegram alert link) highlights it. The order drawer shows the salesman with attribution source and
  conflict flag, lets admins reassign, and offers **Confirm order (adjust quantities)** — the
  `ConfirmEditor` (per-line confirmed qty / remove, expected delivery, note) posts to
  `/shop/orders/{id}/confirm`. Statuses read Received / Confirmed / Preparing / On the way / Delivered.
- **Shop Orders (field, salesman phone)**: "Confirm order" opens the same editor inside the sheet;
  afterwards the forward button is *On the way* (Preparing is an optional side button), and every
  stage has the prefilled WhatsApp to the merchant on top.
- **Pick list** (`/picklist`, feature **Storekeeper**, role `storekeeper`): confirmed orders grouped by
  salesman with confirmed quantities, *Preparing (issued to rep)* and *On the way* buttons, and a
  total-to-pick panel. Storekeepers see only this page; admins can open it too.
- **Salesmen**: storefront card fields (title, photo URL, show card, show WhatsApp button).
- Shared pieces live in `web/src/pages/shop-ops/OrderActions.tsx`.
- **`shop_market_url`** (Settings → Shop): once set to the marketplace origin, `salesman_link` becomes
  `{market}/{slug}` (what the QR encodes) and every tracking URL in emails/WhatsApp points at the
  marketplace's `/o/{token}`. Empty = the legacy token link on the portal host.

## Service worker (market build only)

`vite.config.ts → marketPwa()` generates `sw.js` (Workbox): precached shell (incl. fonts, icons,
webp); the catalog (`/public/market`) NetworkFirst with a 4 s timeout and a 1-day cached fallback, so
a sleeping API still shows products; product photos CacheFirst (600 entries, 30 days); quote/order/
event and `/version.json` never cached. `market/lib/sw.ts` reads `/version.json` (`{build, kill}`,
emitted at build with the commit sha, served `no-store` per `vercel.json`) **before** registering: a
different `build` → worker update + the "New version ready · Refresh" toast; `kill: true` →
unregister everything, drop every cache, reload once, register nothing. To pull the plug on a bad
worker: redeploy with `VITE_SW_KILL=1`, wait for clients to reload, then redeploy without it.
Manifest: `web/public/market.webmanifest` (standalone, any + maskable icons, shortcuts Search / Cart
/ Orders, `start_url /?src=pwa`).

## Owner levers (the site can only sell with what the price book carries)

At least one quantity tier per top category (shows "24+ → 0.600" and the tier nudge) · one cart-value
rule with a real `ends_at` (offer strip + free-delivery progress) · `moq` / `pack_size` on SKUs that
ship in packs · photos for the last 7 SKUs · `public_whatsapp` + `photo_url` on salesman profiles · a
company WhatsApp for "Ask YQ" · a decision on a real clearance markdown.

## Not yet built (in order)

Verified identity via salesman-issued access links (`/join/{token}`, Phase 2) · regular-stock rail
from Focus history (needs `focus_customer_id` links) · web push · Arabic strings (the `locale` flag
and logical CSS are in place) · a real `shop_clearance_pct` markdown applied by the pricing engine.
