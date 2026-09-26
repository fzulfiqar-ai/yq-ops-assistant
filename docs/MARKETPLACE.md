# YQ Marketplace — the merchant front door (v3, 20-Sep-2026)

The marketplace is the existing `/c/{token}` shop, enhanced: same API (`app/shop*.py`), same
tables, same pricing engine. What is new is the front door (a root URL and `/{salesman}` storefronts
instead of a token link), a discovery-first home, a B2B cart and one-screen checkout, tracking with
five stages, merchant memory on the device — and, since v2, its own design system, shell and
performance path (this file). **v3 (branch `market-v3`, three rounds: 17-Sep foundations,
20-Sep redesign + design polish) turned that shop into a wholesale counter:** the basket-value
minimum is the mechanism the whole page leans on, clearance became an opportunity instead of an
apology, the phone got a pinned plum search band and a promo slider, and the app opens the way the
ops portal signs in. API contract: `docs/SHOP.md § Marketplace`. v2 design plan:
`~/.claude/plans/you-are-the-lead-wild-wilkinson.md`; v3 plan and decisions D1–D11:
`~/.claude/plans/we-need-to-rework-hashed-hejlsberg.md`.

## Positioning and copy

**YQ — Where Bahrain restocks. Restock faster. Sell more.** `S.kicker` is the first line (phone band,
desktop header, the opening); `S.tagline` is the second (splash, PWA manifest, order received).
Every word lives in `web/src/market/strings.ts` — nothing is written in a component.

The language is wholesale, not retail: sections and CTAs speak *restock · wholesale order · your
shop · stock · reorder* ("Your restock", "Place wholesale order", "Restock essentials", "Keep
restocking"); cards keep the short "Add". The slogans "wholesale only", "retail" and "consumer" are
never used, and neither are the words slow / dead / old / non-moving — in code, seeds or admin
defaults.

**The four promises** (`shop_market_promises`, JSON, admin-editable, each row `{key, en, ar, icon,
to}`) are the wholesale answer to "free shipping", and only claim what the data backs:

| key | English | Arabic | links to |
|---|---|---|---|
| `delivery` | Free delivery across Bahrain | توصيل مجاني في كل البحرين | `/about#delivery` |
| `trade` | Trade prices for shops | أسعار الجملة للمحلات | `/about#trade` |
| `stock` | Real warehouse stock | مخزون حقيقي من المستودع | `/shop?f=instock` |
| `rep` | Every order confirmed by your rep | كل طلب يؤكده مندوبك | `/about#how` |

`stock` says **real**, never *live* or *real-time*: the catalog is a dated snapshot and the footer
prints its date (`stock_as_of`), so a freshness claim would contradict the same page. A cadence
claim ("updated daily") is not the fix either — only promise it once the upload is provably daily.
Three mirrors must stay equal: `app/shop.py SETTING_DEFAULTS`, `DEFAULT_PROMISES` in
`web/src/pages/Settings.tsx`, `S.about.intro` in `strings.ts`; `tests/test_shop.py` guards the words
in both languages. `shop.validate_promises()` now checks the value **on save** (a JSON list of ≤ 6
objects, unique non-empty `key`, non-empty `en`, optional text `ar`/`icon`/`to`, a link that is a
marketplace path or https) and `PUT /settings/shop` answers 400 with a message the admin can act on
— the bar used to blank itself silently on a bad value. The bar renders the first 4.

**Honesty rules (standing).** The seller price book is the only source of a price — the site never
marks anything down. No invented anchors, timers or scarcity. A struck-through price is *only* a
real previous trade price after a cut in the price book (`was_bhd`, rendered as the "Was … ↓x%"
pill); **retail is never struck through** — it is shown as the merchant's own maths instead
(`marginOf()`: `Retail BHD 1.500 · Your margin BHD 1.100/pc · 73%`). "Only a few left" comes from
stock. "Deal" is reserved for a real drop, offer or bundle; a clearing line reads "Last chance".

## One codebase, two builds

| Build | Entry | Stylesheet / Tailwind | Owns | Hosting |
|---|---|---|---|---|
| portal (default) | `web/src/main.tsx` → `PortalRoot` / `PublicApp` | `src/index.css` · `tailwind.config.js` | `ops.yqmarketplace.com`, `/c/{token}`, `/o/{token}` | Cloudflare Worker `yq-bahrain-ops` (Vercel redirects) |
| **market** (`VITE_APP=market`) | **`web/src/main.market.tsx`** → `MarketApp` | **`src/market/market.css` · `tailwind.market.config.js`** | `/`, `/{slug}`, `/p/{code}`, `/t/{category}`, `/shop`, `/search`, `/quick`, `/cart`, `/checkout`, `/orders`, `/me`, `/about`, `/o/{token}`, `/c/{token}` (redirect) | Cloudflare Worker `yq-marketplace` (Vercel redirects) |

`web/vite.config.ts → marketHtml()` rewrites the shared `index.html` for the market build: the entry
(`main.market.tsx`, one network hop less), `<html lang dir data-app="market">`, `viewport-fit=cover`,
the plum theme colour, `color-scheme: light`, `noindex`, the manifest, **self-hosted font preloads
instead of the Google Fonts stylesheet**, a preconnect to the storage origin, `data-app="market"`
on the prefetch script — and, since v3, the **boot frame**: a small inline `#yq-boot` node (night
gradient + the logo tile as a base64 `data:` URI, ~5 KB) so the first painted pixel is the brand's
night field rather than the cream shell, with the opening mounting on top of an identical picture.
PostCSS is configured inline so the market build compiles `tailwind.market.config.js` (market glob
only) while the portal keeps its config untouched. Market chunks are named (`react`, `router`,
`search`) for the budget check. Scripts: `npm run dev:market`, `build:market`, `preview:market`
(`web/.env.market` sets `VITE_APP=market`).

**Reserved first URL segments** (a salesman slug may never take them) live in four places and must
stay equal: `web/src/MarketApp.tsx` (`RESERVED`), `web/public/catalog-prefetch.js`, `app/shop.py`
(`_RESERVED_FALLBACK`) and the `shop_reserved_slugs` table. v3 added **no** new top-level route.

## Design system ("The YQ Counter")

- **Tokens** — `web/src/market/market.css`, `--m-*` HSL triplets: plum `#6D4091` (sampled from the
  logo; the portal's `#6d28d9` is not used), deep ink, a warm off-white canvas, hairlines, `ok/warn/bad`
  tints, radii 6/10/14/18/24, ink-tinted shadows, `--m-gutter: clamp(12px, 3.2vw, 48px)`,
  `--m-content-max: 1600px`, `--m-safe-b` (every safe-area read goes through it), `--m-bottom-stack`
  (measured by the shell). Light only (`color-scheme: only light`).
  **v3 additions** (mirrored into `tailwind.market.config.js` as `deal`, `fresh`, `sash`, `tile.*`,
  `night`, `arc.*`): `--m-deal` amber `#F2B33D` + `-soft`/`-ink` (Last chance / Deal), `--m-fresh`
  mint `#2E9E6B` + `-soft`/`-ink` (New), `--m-sash` `#824FAB` (the header diagonal), the pastel promo
  canvases `--m-tile-lilac / -apricot / -mint`, and the portal's night set `--m-night` `#140F24`,
  `--m-night-2` `#2A1259`, arcs `--m-arc-1/2/3` (`#1C0B3F`, `#4922E5`, `#A558FB`) with
  `--m-grad-night`. Every pairing carries its measured contrast ratio in the comment beside it.
  Also written by the shell at runtime: `--m-brand-h` / `--m-search-h` (phone band),
  `--m-sticky-h` (desktop header + category strip).
- **Type** — Instrument Sans (UI, tabular figures) + Sora (display: titles, prices), variable woff2 in
  `web/public/fonts/` (OFL files alongside), preloaded, `font-display: swap` with metric-matched
  fallbacks (`size-adjust`/`ascent-override` computed with fontTools) so the swap causes no CLS.
- **Primitives** — `web/src/market/ui/`: `Button` (primary / secondary / ghost / danger / wa; 40–56px),
  `Chip` (v3 tones `deal` / `fresh`), `SectionHeader` (extracted from `Rail`), `Stepper`
  (hold-to-repeat, tick animation, keypad on the number), `Sheet` (bottom sheet / dialog / side
  drawer, drag-to-dismiss, exit animation, focus trap), `ProductImage` (srcset from `thumb_urls`,
  reserved aspect ratio, fallback chain), `Field`/`Select`, `Skeleton`, `Toast` (with Undo action),
  `ErrorBoundary`. The market never imports portal UI — enforced by the `no-restricted-imports`
  block in `web/eslint.config.js`, which also bans `motion` and `recharts` inside `src/market/**`.
- **v3 CSS blocks** — everything added in v3 lives in `market.css` between paired
  `/* v3: name */ … /* end v3: name */` comments, so each feature owns its rules: **reveal**
  (scroll-in), **horizon** (the arc field), **sash** (the header diagonal + `scroll-padding-top`
  for the pinned band), **slider** (`.slider-track` / `.slider-cell`, `SlideCard`, the
  `.canvas-lilac|apricot|mint|plum|night` surfaces, `.creative` for composed art, dot dwell-fill,
  slide states), **hints** (the rotating search example), **sweep** (the one diagonal light pass on
  the splash logo). Classes consumers build dynamically (`canvas-${slide.canvas}`) are safelisted in
  `tailwind.market.config.js`; the classes live in `@layer components` so a Tailwind utility on the
  same element still wins.
- **Motion** — CSS/WAAPI only, no animation library in the merchant bundle: nav pill slide, Add →
  "Added" → stepper morph, stepper tick, cart badge bump, total crossfade, sheet/drawer transitions,
  skeleton shimmer, order-placed check draw, View Transition on the product photo, the promo slider
  (scroll-snap + `scrollTo`), the arc rise, the reveal stagger. Everything that moves is
  gated on `prefers-reduced-motion: no-preference` or dies in the kill switch — and **nothing under
  reduced motion ever hides content**: the reveal double-gates (`html[data-reveal]` *and* the media
  query), the opening becomes a static frame, the slider never auto-advances.
- **`useReveal`** (`hooks/useReveal.ts`) — one module-level `IntersectionObserver` for all `.reveal`
  nodes inside a ref. In a *layout* effect it marks nodes already in (or above) the viewport `.is-in`
  before the browser paints, so nothing fades in on first paint and the LCP element is never delayed;
  only the rest are observed. Rules for consumers: every `.reveal` must sit inside an element passed
  to the hook; put it on a section wrapper or a desktop grid card — never on compact rail cards
  (horizontally off-screen), never inside a `.cv-*` (`content-visibility`) element, never on an
  element with its own transition; keep the className stable; pass `deps` that change when new
  `.reveal` nodes render. Stagger with `style={{ ['--i']: index }}` (capped at 6 × 45 ms).

## Shell (`web/src/market/shell/`)

`Shell` is the layout route. `useViewport()` picks `phone <768 · tablet 768–1023 · desktop 1024–1279 ·
wide ≥1280` synchronously from `matchMedia`.

- **Phone** — `PhoneHeader`, the page, and the bottom stack: `[page bar] [CartDock] [FloatingNav]`.
  `FloatingNav` is a glass pill with five tabs — **Home · Browse · Restock (live line badge) · Orders
  · My YQ** — and a sliding active pill; `activeIndex()` maps by the *first* path segment only, so a
  storefront slug that merely starts like a route is a storefront (`/search` → Browse; `/cart`,
  `/checkout`, `/quick` → Restock; `/orders`, `/o/…` → Orders; `/me`, `/about` → My YQ). Search is no
  longer a tab — it lives in the header band. `CartDock` is the running restock ("3 products · 42 pcs
  · BHD 39.600 → Review restock", plus the wholesale progress) on browsing pages; the stack hides on
  scroll-down and returns on scroll-up.
- **The pinned search band** — a page asks for it with `useSearchBand()`: **Home** (`/`, `/{slug}`,
  `/p/{code}`), **ShopPage** (`/shop`) and **CategoryPage** (`/t/{category}`). `PhoneHeader` then
  renders ONE sticky plum `<header>` whose `top` is `calc(-1 * var(--m-brand-h))` with
  `padding-top: var(--m-safe-t)`: row 1 (logo + kicker, or "Welcome back, {shop}", and the rep
  avatar — or back + title on a shelf) rides away with the page, while row 2, the white search pill,
  stays pinned. **No scroll handler.** A `useLayoutEffect` measures the two rows into `--m-brand-h`
  and `--m-search-h` (the pinned part, safe area included) on `<html>`, so a page can pin its own row
  under the band: `GridPage` pins the facet chips at `top: var(--m-search-h)` with a 1px sentinel
  that gives them a hairline once stuck, and `market.css` sets `scroll-padding-top` from the same
  variable so focus and `#anchors` never land under the band. The pill is a single link to `/search`
  (one tab stop) with a **rotating hint** inside (`SearchHints`, `S.search.hints`: Type-C cable · 20W
  charger · TWS earbuds · iPhone cable · Car charger · Power bank) — crossfaded, `aria-hidden`,
  paused while a panel or the palette is open, off under reduced motion. Other pages keep the light
  header (back arrow + title).
- **Tablet / desktop** (`DesktopShell`, lazy — phones never download it) — the dark plum **utility
  promise bar** (`PromiseBar`, with "Add YQ app" at its end), then a sticky 72px glass
  **`StickyHeader`**: logo + "Where Bahrain restocks." · **Browse** mega-nav (category preview, facet
  chips, four best sellers with Add) · a large search trigger carrying the same rotating hint that
  opens the ⌘K palette · **Deals** (only when real deals or last-chance lines are in stock; it
  replaced the old New / Offers / Clearance links) · Quick order · Orders · My YQ · **Restock** (line
  count and a thin wholesale-progress line). A category strip rides under it on Home and Browse,
  which makes the sticky block taller than the header row — everything that must sit below it pins to
  `var(--m-sticky-h, var(--m-header-h))`, never to a hard-coded 72px. Content sits in a fluid 1600px
  container; from 1280px a persistent aside carries **`MiniCart`** (wholesale state + `PopularRows`
  when empty) and **`Spotlight`** (the same slide model as the hero, minus whatever the page already
  showed). Below 1280 the header's Restock opens the mini-cart as a side drawer. The aside's run-out
  margin is measured (`main − aside`) so it never gets sliced by the header or leave dead cream above
  the footer, and the page's closing tail (`<PageTail>`: the CTA band and the footer) renders at full
  container width below the column row. Grids run 4–5 columns (a fifth only from `3xl`, because past
  four a wholesale name clips).
- **Overlays** — `ShellContext.openProduct(code)` pushes history state so the `ProductPanel`
  (sheet / dialog / right drawer) opens over whichever page is showing and Back closes it;
  `/p/{code}` deep links still work. `SearchPalette` opens with ⌘K / Ctrl+K / `/`.
- **Page bars** — pages hand their sticky action (`Place wholesale order`, `Keep restocking`,
  `Add all`) to the shell with `<PageBar>`; the shell stacks it above the nav and writes the measured
  height into `--m-bottom-stack`, so nothing is ever hidden under the nav.

## Code map (`web/src/market/`)

- `MarketContext.tsx` — `useMarket()` (catalog from the phone's saved copy then the live payload,
  attribution, lazy MiniSearch index, cart actions with quantity memory) and `useOrder()` (server
  quote, coupon, note, my orders). Cart lines live in `store/cart.ts` (external store, key
  `yq-shop-cart:market`) so an add re-renders one card, not the grid.
- `lib/format.ts` — money/dates/quantity rules, badge order and `BADGE_META` (`clearance` → "Last
  chance", `on_offer` → "Deal"), **`productName()` / `productDetail()`** (many `display_name`s are
  just the code; the real name is derived from `spec` and the "(VFAN)" suffix dropped),
  **`variantOf()`** (≤3 short chips — connector, wattage, length, ports, capacity — that separate two
  look-alike SKUs), **`marginOf()`** (trade vs `compare_at_bhd ?? b2c_bhd`, the merchant's margin and
  %), `priceAnchor()` (now `was_bhd` only — retail is never struck).
  `lib/facets.ts` — sub-facets inferred from product text, sort, and the quick filters including
  **`deals`**. `lib/home.ts` — the merchandising rules: `lastChance()`, `dealSets()`, `dealsLine()`,
  **`homeRails()`**, `brandTiles()`, `heroSplit()`, `spreadFamilies()` (two variants of one model
  never land side by side), `regularStock()`, `pickedUpAgain()`, `categoryTiles()`.
  **`lib/slides.ts`** — the one slide model (below). `lib/smallOrder.ts` — the session-scoped
  small-order acknowledgement. `lib/quickParse.ts` — "24 x C18, 12 UK15, tws 6" → rows.
  `lib/searchGroups.ts` — grouped results. `lib/device.ts` — what the phone remembers.
- `components/` — `MarketCard` (grid / compact / list; image → `brand · code` kicker → family name →
  variant chips → price per piece → the "Was … ↓x%" pill in its own slot → `MarginStrip` → stock word
  → Add · 12 → ✓ Added → stepper, heart, Undo on remove), `Rail`, `SectionHeader`, **`PromoSlider`**,
  **`SlideCard`**, **`ComposedCreative`**, **`DealsSection`**, **`BrandTiles`**,
  **`ContinueRestock`**, **`CtaBand`**, **`WholesaleState`** (+ `WholesaleFillers`,
  `SmallRequestButton`), **`SmallOrderSheet`**, `ProductPanel`, `QtySheet`, `SearchResults`,
  `SearchPalette`, `SearchField` (+ `SearchHints`), `HomeBlocks` (category tiles, mission strip,
  offer strip, track card), `PromiseBar`/`PromiseStrip`, `RepCard`, `ProgressBar`, `Spotlight`,
  `CampaignStrip` (now only the category banner), `States` (incl. the extended `Footer`), `Splash`,
  `Lightbox`, `CheckMark`, `UpdateToast`. Gone in v3: `MinimumBar`, the campaign strip + hero pair
  (one slider now), the separate product hero and its `useTilt` hook.
- `pages/` — `Home` (two-phase render; the below-the-fold half is its own chunk, **`HomeBelow`**),
  `ShopPage` (tiles + entries; with `?f=` it is the "See all" shelf), `CategoryPage` → `GridPage`
  (facets, sort, sticky chips **In stock · Deals · Price drops · Last chance · Saved**, grid/list,
  all in the URL), `SearchPage` (instant grouped results; empty state = Recent → Popular restocks →
  categories), `QuickOrderPage`, `CartPage` ("Your restock"), `CheckoutPage`, `TrackingPage`,
  `MyOrdersPage`, `MyYQPage`, `AboutPage` (`#how`, `#trade`, `#delivery`, `#privacy`).

## Home (block order)

Every product appears in **at most one** section (`homeRails()` reserves every deal code for Deals,
then Restock essentials → New arrivals → Moving fast; "Picked up again" skips anything already shown;
rail products sink to the end of the grid), and every campaign appears **once per page**
(`lib/slides.ts` builds the list once, `useClaimSlides()` tells the aside Spotlight what the main
column already spent). The paste-a-list offer is made exactly twice on each layout.

**Phone** — plum band + pinned search → promise strip (the four promises, wrapped over two rows) →
**`PromoSlider`** (`heroDeck()`: slide 1 as built — `catalog-prefetch.js` preloads its image — then
only slides the page does not repeat 400px lower, at most one deals card, capped at 3; 2:1
`SlideCard`s on a scroll-snap track that bleeds to the screen edges, dots + a 44px pause/play
control, 5.5s auto-advance that pauses on touch/scroll/focus/hidden tab and for 8s after) → round
**category tiles** (`HomeBlocks.CategoryTiles`) → what to do next (**`ContinueRestock`** — the
wholesale gap with one-tap fillers, or Order again, or the paste card — the track card, the live
offer strip, the three wholesale actions, the rep card) → *[phase 2, `HomeBelow`]* Your regular stock
(recognised) → **Restock essentials** → **Stock-Up Deals / Last-Chance Stock** (`DealsSection`) →
**New arrivals** → **`BrandTiles`** → **Moving fast in Bahrain** (the one tinted block in the middle
of the page) → Picked up again → the paste card (first visits) → **All products** (In stock / Deals
chips, grid or list, 12 cards then a page on idle) → **`CtaBand`** "Ready to restock?" on the night
palette → **`Footer`** (in `components/States.tsx`: rep, delivery rule, About/How/Trade/Delivery/
Privacy, the price and stock dates).

**Desktop** — the same list on a wider rhythm, with two differences: the top is a **hero
composition** (`heroSplit()`) — a 21:9 `PromoSlider` stage that dissolves between slides, beside up
to two stacked pastel `SlideCard` tiles taken from the same deck, never a tile that clones a heading
further down — and sections rise in as they scroll (`.reveal`, desktop grid cards only). The CTA band
and footer are hosted by the shell below the aside column (`PageTail`).

## The wholesale order engine (what the merchant sees)

The minimum is a **basket value**, not a per-SKU MOQ: `shop_min_order_bhd` (Settings → Shop; the
owner's value is BHD 20), `shop_small_order_mode` (`request` | `allow` | `block`),
`shop_small_order_fee_bhd`, `shop_gap_suggestions` (6). The quote (`price_cart()`) returns
`minimum {value_bhd, remaining_bhd, met, mode, kind, fee_bhd}` and `gap_suggestions[]`; the catalog
payload carries `settings.min_order_bhd` and **`settings.small_order_mode`**, so Home can say "BHD 7
away" before a quote exists.

**`components/WholesaleState.tsx`** renders it in three variants — `page` (Restock), `compact`
(mini-cart), `home` (Continue your restock) — and every number in it is the server's:

- **no minimum configured** (`minimum == null`): the card does not render at all.
- **under**: kicker "Wholesale order" · **"BHD 7.000 away from your wholesale order"** ("Almost
  there — BHD 2.000 to go" in the last fifth) · a progress rail (`role=progressbar`, width
  transition) labelled *in your restock* / *wholesale minimum* · **"Complete your restock with
  these"** — the gap fillers · the actions.
- **met**: **"Wholesale order ready"** with the tick drawing, primary "Continue to checkout".

**Gap fillers are a mixed basket, never one SKU.** The server ranks candidates (cart pairs → best
sellers of the same categories → anything in stock) by how close one default quantity lands on the
gap, and **on an exact tie a clearing line wins** — the minimum doubles as the clearance engine, at
the price-book price. The client then offers each row at about a third of what is left (never below
the line's own pack), so the merchant fills the gap with three things they can sell, not 25 of one
cable; only a row that really does close the gap is tagged "completes your order". On the Restock
page the list is handed to a `<WholesaleFillers>` block *below* the merchant's own lines (on a phone
it pushed their restock off the first screen; in the desktop sticky column it pushed the CTA out of
a box the page cannot scroll).

**Under the minimum the primary is always "Keep restocking"** — in `block` mode too, where a disabled
"Place wholesale order" used to leave the page with no action at all. The secondary follows the mode:

| mode | secondary | checkout | placed / my orders |
|---|---|---|---|
| `request` (default) | **"Request a small order"** → `SmallOrderSheet`: *"Orders under BHD 20.000 are reviewed by your YQ representative and confirmed on WhatsApp, case by case."* (+ the fee line only when `fee_bhd > 0`) → "Continue to request" (`sessionStorage yq-small-ack`) | title "Small order request", CTA "Send request", the reassurance line says no payment now | "Small order request received"; `order_kind = 'small'`, badged in My orders / My YQ, tracking says the rep confirms case by case |
| `allow` | none — the order goes through like any other, so the CTA is plain checkout | standard | standard |
| `block` | none, only the reason | — | — |

`order_kind` flows end to end: the row value → `market_order()`'s response (`app/shop_api.py`) →
`MarketOrderResponse` → the placed screen; and `orders_by_tokens()` selects it → `MyOrderSummary` →
the badge on `/orders`, `/me` and the tracking page.

## Stock-Up Deals / Last-Chance Stock

Owner decision (17-Sep-2026): **no markdown of any kind** — every price shown is the price book's.
The section sells *availability + margin*. `dealSets()` (`lib/home.ts`) builds four real sets, all in
stock, de-duplicated against each other and de-clustered so two variants of one model never sit side
by side:

| set | rule |
|---|---|
| `drops` | a real trade-price cut that still carries the old price (`was_bhd`) |
| `offers` | a live item-scoped discount rule |
| `bundles` | a live bundle rule |
| `lastChance` | the `clearance` badge, best real retail margin first, no cap |

`DealsSection` is titled **Stock-Up Deals** only when at least one *real* deal exists
(`hasRealDeals`); otherwise it is simply **Last-Chance Stock**, kicker *"Lines we're clearing · trade
price · while stock lasts"*. Chips (All · Price drops · Offers · Bundles · Last chance) appear only
for sets that have lines, and only when there is more than one kind. Phone: a swipe rail with a
See-all tile. Desktop: two full rows of grid cards that rise in.

**The merchant's maths is on the card**, not a discount: `MarginStrip` prints `Retail BHD 1.500` over
`Your margin BHD 1.100/pc · 73%` (one line on narrow cards), from `marginOf()` — the payload's own
`compare_at_bhd`/`b2c_bhd` against `price_bhd`. A solid amber **"High margin"** tag appears only at
≥ 50 % of retail. The badge on a clearing line is **"Last chance"** (amber); "Deal" is reserved for a
real offer or drop; a genuine cut also gets the **"Was 2.000 · ↓5%"** pill in its own slot. Elsewhere
the same lines are surfaced as a data slide, the desktop Spotlight, the Browse `Deals` chip, the
category-page composed banner, and the gap-filler tie-break above.

## Campaign creative (`shop_campaigns`)

Root cause fixed in v3: a 1200-wide banner was being rendered at 220×148 and squashed into a 64px
square with `object-cover`. `scripts/marketplace_campaign_creative_migration.sql` (additive,
idempotent) adds four columns:

| column | meaning |
|---|---|
| `image_url_600` | the 600w rendition the upload route already returns (srcset 600/1200) |
| `image_fit` | `contain` (default, never cropped) \| `cover` |
| `product_codes` | up to 3 catalog codes for a **composed** creative (`CAMPAIGN_MAX_PRODUCTS`) |
| `canvas` | `lilac` (default) \| `apricot` \| `mint` \| `plum` \| `night` |

Backend: `CampaignIn` (all four optional), `validate_campaign()` (enum checks, ≤3 codes, codes kept
in the admin's spelling), `_CAMPAIGN_NULLABLE`, and `campaigns_payload()` — which resolves
`product_codes` against the live catalog (`_campaign_live_codes`), so a hidden or renamed SKU simply
drops out. Front end: `ComposedCreative` lays up to three product photos on white discs fanned
−8°/0/+8° over the canvas tint, each `object-contain` inside its circle — no upload, no crop, always
on-brand; an uploaded photo gets `srcset` 600/1200 and `object-contain` unless the office chose
`cover`. Every frame has a fixed aspect and `width`/`height`, so no campaign costs CLS.

Admin: **Offers & Rules → Campaigns** (`web/src/pages/shop-ops/CampaignsSection.tsx`, route
`/shop-rules`, feature *Shop Admin*). The Creative control is **Upload photo | Compose from
products** (code picker with search), plus the fit toggle and the canvas picker, with a live preview
that renders exactly what the market renders, and help text explaining that a hero campaign is kept
out of the strip automatically.

## The opening (`components/Splash.tsx`)

The marketplace opens the way the ops portal signs in: deep night, the glow horizon rising from
below, and YQ said once. The portal's `glow-horizon.tsx` is ported to CSS (the "v3: horizon" block —
four absolutely positioned, blurred ellipses; `motion` is banned in this bundle). Sequence: arcs rise
(0–900 ms) → the logo tile takes one light sweep (560 ms) → "Where Bahrain restocks." (420 ms) →
"Restock faster. Sell more." (640 ms) → under the horizon, the first three promises or "Welcome back,
{shop}" with the "Refill my shelf" shortcut (820 ms) → the composition **rests** ~400 ms and leaves
at 1.9 s (2.8 s with the shortcut) in a 320 ms fade with a 2 % scale.

- **At most once per device every 7 days, and never twice in a tab** (`lib/splashGate.ts`:
  `localStorage yq-splash-at` + `sessionStorage yq-splash-session`, in try/catch — blocked storage
  means no opening), **standalone PWA included**. Every rep or WhatsApp link opens a new tab, so a
  session gate alone covered a ready catalog again each time. `public/catalog-prefetch.js` mirrors
  the rule and switches the served night frame off (`html[data-boot=off]`) on every other load. The
  30-day `yq-splash-seen` stamp only decides when a returning merchant gets the longer welcome with
  the shortcut.
- **Instant first frame:** the served HTML carries a static `#yq-boot` node with the same night field
  and the same logo tile in the same place (`marketHtml()`), `Shell.tsx` paints the same night as the
  chunk's Suspense fallback, and `Splash` removes the static node as it mounts (and then does not
  replay the tile's spring). An 8s CSS safety net hides the boot node if the chunk never arrives.
- Tap, click, wheel or Esc skips; the skip control takes focus and focus returns afterwards.
- **Reduced motion:** the same finished frame, static, 600 ms, then a plain fade.
- It is an overlay only — the catalog loads and the page renders underneath the whole time.

The night palette is reused deliberately and sparingly: the closing **CTA band** (which mounts its
arcs the first time it scrolls into view) and the `night` campaign canvas. The header stays flat plum.

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
| retail (`compare_at_bhd`) | the price book's B2C rate — sent everywhere when `shop_show_retail_compare` is on, on clearing lines also when it is off. **v3: the market no longer strikes it through**; it renders the margin instead | `shop_clearance_show_retail` (1) |

No invented anchors, timers or scarcity: the only struck price is a real previous trade price, and
"Only a few left" comes from stock.

## Images

`app/catalog.py` writes, per photo, the legacy 256px JPEG thumb plus **160/320/512 WebP**
(`thumbs/{code}-{kind}-{size}.webp`, one-year cache metadata); the payload carries `thumb_urls` and
`ProductImage` builds a `srcset` from it (older cached payloads fall back to `thumb_url`).
`public/catalog-prefetch.js` preloads the first slide's image as soon as the catalog JSON lands — it
mirrors the slide-1 picker exported from `lib/slides.ts` (including the recognised-merchant rule) and
shares the `sizes` string with the slider, so the preload and the render agree. Backfill:
`python -m scripts.make_market_thumbs [--only-missing]` (360 files built on 16-Sep-2026; 320px
averages 4.8 KB vs 6.8 KB for the 256 JPEG). Icons: `python -m scripts.make_market_icons` (any +
maskable + shortcuts) → `web/public/`.

## Deploying the marketplace (second Vercel project) — LIVE since 16-Sep-2026

**Order for a release like v3:**

1. **SQL first** — `python -m scripts.apply_sql scripts/marketplace_campaign_creative_migration.sql`,
   then `python -m scripts.audit_grants` (exit 0).
2. **Render** — `python -m scripts.render_deploy deploy --commit <sha> --wait` (deploy-only; never
   `scripts/deploy_render.py`, which would overwrite the live env set); pushes do not reach it.
3. **Superseded 20-Sep-2026:** web now ships to Cloudflare (`cf-deploy.yml` on push, or
   `python -m scripts.deploy_cf market|portal`). Historical: `scripts.deploy_web market|portal` — both
   projects are CLI-deployed from `web/`; the script swaps the CLI link, deploys and restores it.
   Vercel builds with the project's env, so a local `.env` never leaks into the bundle.
4. **Portal settings** — the production **wholesale minimum is set in Settings → Shop**
   (`shop_min_order_bhd`; the owner's value is **BHD 20**, and Settings marks that as the recommended
   number). It is never hardcoded. Check `shop_small_order_mode` (`request`) at the same time, and
   **flush the catalog cache** so the new settings reach the payload.
   ⚠️ At the time of writing production still had `shop_min_order_bhd = 0` — with no minimum the whole
   wholesale state card is hidden by design, so the engine looks missing until this is set.
5. **Verify** — `/version.json` changes build id, `/sw.js` is `no-cache`, `/robots.txt` disallows all,
   `/` and `/{slug}` render on a phone, then rerun the QA harness against production.

- **Production:** `https://yqmarketplace.com` — Cloudflare Worker `yq-marketplace` (static assets; "Pages" is now this product; default URL `yq-marketplace.yqbahrain.workers.dev` until the domain is attached)
  (account id + Write token in the root `.env`), built locally with `VITE_APP=market`,
  `VITE_API_URL=https://yq-ops-assistant.onrender.com` by `python -m scripts.deploy_cf market`,
  and on every push to `main` by `.github/workflows/cf-deploy.yml` once the `CLOUDFLARE_*`
  repository secrets exist. The market build imports no Supabase code, so it needs no
  `VITE_SUPABASE_*` values. Cloudflare hosts both apps since 20-Sep-2026: Vercel Hobby capped transfer at
  100 GB/month (hit) and forbids commercial use.
- **Vercel:** `yq-marketplace.vercel.app` (project `yq-marketplace`, team `fzulfiqar-ai-s-projects`)
  now only 308-redirects to Pages — `web/vercel.json` `redirects`, deployed with
  `python -m scripts.deploy_web market`. Old WhatsApp links and QR codes keep working through it.
- **Headers:** `web/public/_headers` (Cloudflare) mirrors `web/vercel.json` `headers` (Vercel): CSP,
  `noindex`, immutable `/assets/*`, `no-cache` `sw.js`, `no-store` `version.json`. Change both together.
- **Render:** `ALLOWED_ORIGINS` includes `https://yqmarketplace.com`. Add the custom domain
  there too when it exists, otherwise every API call is refused by CORS.
- **Portal setting:** `shop_market_url = https://yqmarketplace.com` (Settings → Shop), so
  salesman links/QR and tracking URLs point at the marketplace.
- **Custom domain (later):** attach the root domain to the `yq-marketplace` Worker and `ops.`
  to `yq-bahrain-ops` (`routes` with `custom_domain: true` in the wrangler configs, free); add both to `ALLOWED_ORIGINS`; update
  `shop_market_url`; print QR codes only then.

Local: `cd web && npm run build:market && npm run preview:market` (port 5174) against a local
`uvicorn app.main:app --port 8001` (`web/.env` points `VITE_API_URL` there).

## Visual QA harness — `scripts/qa/market_qa.py` (committed in v3)

Python Playwright (Chromium is cached on the dev machine). Full notes: `scripts/qa/README.md`.

```bash
python scripts/qa/market_qa.py --base http://localhost:5174 --out scratchpad/qa/shots
python scripts/qa/market_qa.py --base http://localhost:5174 --out scratchpad/qa/shots --quick
python scripts/qa/market_qa.py --base http://localhost:5174 --out scratchpad/qa/shots --only cart_under,checkout_small
python scripts/qa/market_qa.py --base https://yqmarketplace.com --api https://yq-ops-assistant.onrender.com --out scratchpad/qa/prod
```

`--quick` keeps 390×844 and 1440×900 (also the "lead" viewports that carry the extras: full-page
shots, element crops, slider timing, the campaign injection, reduced motion, the opening);
`--only` takes state keys (`--list` prints them); `--list` exits after printing. In Git Bash prefix
with `MSYS_NO_PATHCONV=1`. Exit code 1 on a hard failure, 0 otherwise — warnings never fail a run.
Output: `results.json`, `index.md` and `shots/<viewport>/<state>.png`.

**9 viewports** (320×640 → 1920×1080, incl. a touch tablet) × **25 states** (`--list` prints them:
`home`, `storefront`,
`shop`, `shop_deals`, `shop_clearance`, `shop_best`, `shop_moving`, `category`, `search`, `search_q`,
`quick`, `cart_empty`, `cart_under`, `cart_met`, `checkout_small`, `checkout_met`, `orders`, `me`,
`about`, `about_trade`, `product`, `campaign`, `reduced_home`, `reduced_cart`, `opening`). Each state
gets a fresh context; the cart states seed `localStorage['yq-shop-cart:market']` (UK04-C ×5 = BHD 5
under the minimum; UK15 ×14 = BHD 21 over it). **Nothing is ordered:** the order POST, the order read
and the phone lookup are intercepted and mocked; everything else — catalog, quote, gap fillers — is
the live API, so the report's numbers are real. Service workers are blocked.

**Hard checks:** horizontal overflow · console/page errors · failed or ≥400 requests · no Google
Fonts request and both market fonts loaded · tap targets ≥36px and inputs ≥16px on touch (a small
control reaches 44px with the invisible `.hit` area in `market.css`, never by being redrawn) · no
`object-fit: cover` outside circular avatars · banned copy (`slow mover`, `no minimum`, `Save N%`) ·
each state's required wording ("away from your wholesale order", "Wholesale order ready") · a product
code in at most one home section · slider dot count == slide count · the bottom nav is exactly Home ·
Browse · Restock · Orders · My YQ · the phone search band still pinned after scrolling 800px · the
rotating hint never paints outside the pill and leaves no ghost · the desktop aside starts below the
sticky header · an injected campaign renders exactly once and never again in the aside · every
`.reveal` ends at opacity 1 · under reduced motion nothing is hidden and the opening is the static
frame · the restock actions, small-order sheet, checkout submit and placed screen · the opening plays
on a fresh device, never on a reload and never in a new tab within 7 days. **Warnings** (never
fatal): tap targets between 36 and 44px, slider autoplay/hold timing, gap-filler mix, a repeated paste offer, `copy-drift` (a literal the
harness checks is no longer in `strings.ts`), a missing rep slug.

**Last full run against the local preview (20-Sep-2026): 200 pages, 264 screenshots, 0 hard
failures, 266 advisory tap-target warnings** (controls between 36 and 44px — the standing backlog,
not a regression).

Measurement notes worth keeping: the harness sweeps a screen at a time (up to 6) and merges results,
otherwise only the first screenful would be checked and `content-visibility` sections would have no
text; every literal is mirrored from `strings.ts`; product identity on Home comes from the thumbnail
URL, so a photo-less product is invisible to the duplicate-rail check. iOS safe-area insets cannot be
simulated in Chromium — check a real phone for the notch.

Static gates: `npx tsc -b && npx eslint . && npm run build:market && npm run build` in `web/` (the
portal must keep building); `python -m tests.test_shop` (51 cases) and `python -m scripts.audit_grants`
after any backend/SQL touch.

## Performance

Same machine, Lighthouse, simulated slow 4G, CPU 4× against a local API (this machine reads about
2× slower than the CI runner, where the first deploy scored 64):

| mobile Home | production (v1) | v2 | **v3** |
|---|---|---|---|
| Performance / Accessibility / Best practices | 39 / — / — | 45–46 / 92–96 / 100 | **70 / 100 / 100** |
| FCP · LCP · TBT · Speed Index · CLS | 4.3 s · 5.6 s · 2.57 s · 5.0 s · 0.001 | 4.4 s · ~5.0 s · ~1.8 s · 3.9 s · 0 | **2.18 s · 3.41 s · 790 ms · 3.05 s · 0.023** |

Desktop Home: **92 / 100 / 100**, LCP **1.17 s**, CLS **0**. Mobile `/cart`: **69 / 100 / 100**,
LCP **4.44 s**. The LCP element on mobile Home is the **slide-1 headline text** — as intended: the
slide's copy paints before its art, and `useReveal` never hides it.

Bundles: initial JS **133.9 KB gz** (+5.0 KB over pre-v3) and CSS **17.4 KB gz** (+7.0 KB) — both
inside the Lighthouse budgets (`.github/lighthouserc.json`: perf ≥ 0.7, a11y ≥ 0.9, LCP ≤ 2.5 s,
TBT ≤ 300 ms, CLS ≤ 0.05, script ≤ 180 KB, stylesheet ≤ 35 KB, font ≤ 95 KB, TTI ≤ 3.5 s; warn-only,
weekly workflow, so a release runs Lighthouse locally).

What moved it: the static market entry, self-hosted preloaded fonts, market-only CSS, MiniSearch off
the critical path, memoised device reads, the two-phase Home render (v3 moved the whole
below-the-fold half into its own `HomeBelow` chunk), `content-visibility` on cards below the fold,
responsive WebP thumbnails with long cache, the slide-1 preload that mirrors the renderer, and a
reveal that marks already-visible nodes before paint instead of fading them in. LCP is still bound by
"catalog JSON → first paint" on a cold visit; the remaining levers are an image CDN in front of the
bucket (Track B) and a warm API.

## Portal side

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
  total-to-pick panel.
- **Settings → Shop** (v3): the wholesale minimum with its recommended value, *Under the minimum* as a
  proper select (request / allow / block), and a **Promises editor** — four rows of key · English ·
  Arabic · icon · link, with "Restore defaults", round-tripped through `JSON.parse` before saving and
  rejected by the API with a readable message if it is not a valid promise list.
- **Offers & Rules → Campaigns** (v3): the creative picker described above.
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
Manifest: `web/public/market.webmanifest` (standalone, any + maskable icons, shortcuts Search /
**Restock** / Orders, `start_url /?src=pwa`).

## Owner levers (the site can only sell with what the price book carries)

**Set `shop_min_order_bhd` to 20 in production** — until it is, the wholesale engine has nothing to
show · at least one quantity tier per top category (shows "24+ → 0.600" and the tier nudge) · one
cart-value rule with a real `ends_at` (offer strip + free-delivery progress) · a real item-scoped
discount or bundle rule, which is what turns the section title into "Stock-Up Deals" and earns a
truthful "Deal" badge · `moq` / `pack_size` on SKUs that ship in packs · photos for the last 7 SKUs
(a photo-less product cannot appear in a slider, a brand tile or a composed creative) ·
`public_whatsapp` + `photo_url` on salesman profiles · a company WhatsApp for "Ask YQ".

## Not done yet (in order)

- **Arabic / RTL is prepared but not switched on.** Every string is centralised in `strings.ts`, the
  CSS uses logical properties only (`ps-`/`pe-`/`start`/`end`), `locale` flips `lang`/`dir` on
  `<html>`, campaigns carry `title_ar` / `line_ar` / `cta_label_ar` and every promise has `ar`. What
  is missing is the translated string file and a language switch that persists.
- **The AI concierge** (`shop_market_ai_enabled`, phase C) is not built.
- **7 SKUs still have no photo.**
- Verified identity via salesman-issued access links (`/join/{token}`, Phase 2) · regular-stock rail
  from Focus history (needs `focus_customer_id` links) · web push · a real `shop_clearance_pct`
  markdown applied by the pricing engine (the owner has declined one so far — D3 is the answer
  without it).

## Release log

- **v3 (17 → 20-Sep-2026, branch `market-v3`, commits `0834a7e` → `99fe741` → `5759d53`)** —
  wholesale-first. Round 1: the four promises with Arabic + validation, `small_order_mode` in the
  catalog settings, `order_kind` end to end, the clearance tie-break in gap fillers, the campaign
  creative columns + migration (tests 42 → 51), the copy audit, the v3 tokens and CSS blocks,
  `SectionHeader`, `useReveal`, `variantOf`/`marginOf`, `dealSets`/`homeRails`/`brandTiles`, the
  `deals` quick filter and the slide model. Round 2: the plum band with pinned search, the new bottom
  nav, the promo slider with composed art, round category tiles, Stock-Up Deals with chips, the new
  rails, Continue your restock, the night CTA band, the fuller footer, card v3, the wholesale state
  card and small-order flow, the opening, and the two portal editors. Round 3: the committed QA
  harness and two passes of screenshot-driven design review (restock above the fold, the "Was" pill
  in its own slot, mixed-basket fillers, opaque slide peek, wrapping promise strip, labelled desktop
  header actions, category peek on Browse, the instant night first frame, and "Live warehouse stock"
  → "Real warehouse stock" in all three mirrors).
- **v2.1 (16-Sep-2026)** — desktop aside (`MiniCart` + `Spotlight`), the campaigns layer (table
  `shop_campaigns`, admin tab, `GET/POST/PATCH/DELETE /shop/campaigns`, image upload), the promise
  bar, the wholesale order engine (`shop_min_order_bhd`, `shop_small_order_mode`,
  `shop_small_order_fee_bhd`, `shop_gap_suggestions`, `gap_fillers()`, `order_kind` +
  `minimum_gap_bhd` on orders — `scripts/marketplace_wholesale_migration.sql`), returning-merchant
  recognition (`POST /public/market/recognize`), restock requests, ready-order links
  (`/{slug}?order=CODE:QTY`), saved items, the lightbox, `/about`, `lib/errors.ts` and the first
  splash. All of it is still here; v3 replaced its renderings — `MinimumBar` became `WholesaleState`,
  the campaign strip + hero became one `PromoSlider`, and "No minimum order" became the trade-price
  promise.
- **v2 (16-Sep-2026)** — the market build, the design system, the shell, self-hosted fonts, the
  two-phase Home, responsive WebP thumbs and the service worker.
