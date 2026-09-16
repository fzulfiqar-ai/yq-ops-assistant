# YQ Marketplace — the merchant front door (16-Sep-2026)

The marketplace is the existing `/c/{token}` shop, enhanced: same API (`app/shop*.py`), same
tables, same pricing engine, same product sheet, stepper and cart hook. What is new is the front
door (a root URL and `/{salesman}` storefronts instead of a token link), a discovery-first home,
a B2B cart and one-screen checkout, tracking with five stages, and merchant memory on the device.
Plan and decisions: `~/.claude/plans` (A–Z) — the API contract is in `docs/SHOP.md § Marketplace`.

## One codebase, two builds

| Build | Entry | Owns | Hosting |
|---|---|---|---|
| portal (default) | `web/src/main.tsx` → `PortalRoot` / `PublicApp` | `yq-bahrain-ops.vercel.app`, `/c/{token}`, `/o/{token}` | existing Vercel project |
| **market** (`VITE_APP=market`) | `web/src/main.tsx` → `web/src/MarketApp.tsx` | `/`, `/{slug}`, `/p/{code}`, `/t/{category}`, `/search`, `/cart`, `/checkout`, `/orders`, `/o/{token}`, `/c/{token}` (redirect) | a **second Vercel project** on its own hostname (later the custom domain) |

`web/vite.config.ts` reads `VITE_APP`: the market build gets its own title/description/Open Graph,
`<meta name="robots" content="noindex">`, `<link rel="manifest" href="/market.webmanifest">`,
and `data-app="market"` on the prefetch script so `public/catalog-prefetch.js` fetches
`/public/market[?ref=]` before the JavaScript downloads. The portal build keeps its self-destroying
service worker; the market build has no service worker yet (the Workbox worker with a version
kill-switch is the PWA step).

## Code map (`web/src/market/`)

- `MarketContext.tsx` — catalog (painted from the phone's saved copy, replaced by the live payload),
  attribution (`ref`), device cart (`useCart('market')`) with per-SKU quantity memory, live quote
  (`useQuote` → `POST /public/market/quote`), my orders (tokens → summaries), MiniSearch index.
- `lib/device.ts` — what the phone remembers: device id, remembered rep (90 days), order tokens,
  last quantity per SKU, checkout details (opt-in), recent searches, idempotency key.
- `lib/marketApi.ts` — token-less endpoints; `lib/events.ts` — funnel pings (events v2);
  `lib/search.ts` — MiniSearch + synonym map (type c → USB-C, tws → earbuds, 20w → fast charge…);
  `lib/install.ts` — add-to-home-screen prompt; `strings.ts` — every word on screen.
- `components/Chrome.tsx` — TopBar, BottomNav (Home · Search · Cart · Orders), CartBar, Page;
  `components/MarketCard.tsx` — the product card (Add → stepper preset to the remembered quantity,
  tap the number for the keypad, "Tell Ahmed" on sold-out); `components/Bits.tsx` — search box,
  chips, rail, offer hero, rep banner, progress bar, keypad sheet, states.
- `pages/` — Home (anonymous vs recognized, rails capped 3/4 and de-duplicated against the grid),
  SearchPage, CartPage, CheckoutPage, TrackingPage (also the confirmation), MyOrdersPage, LegacyRedirect.

Attribution on the device: `/{slug}` is remembered ONLY after the server confirms the slug resolves
(`payload.ref`); an unknown slug bounces to `/` and leaves the previously remembered rep intact.
Checkout sends `session_ref`, `device_id` and a per-attempt `client_order_id` (a retry after a
timeout returns the same order).

## Deploying the marketplace (second Vercel project)

1. Vercel → Add New Project → import the same GitHub repo → **Root Directory `web`**, framework Vite,
   build `npm run build`, output `dist`. Enable Git integration (previews per branch).
2. Environment variables: `VITE_APP=market`, `VITE_API_URL=https://yq-ops-assistant.onrender.com`
   (and the two `VITE_SUPABASE_*` values the portal uses — unused by the market build but harmless).
3. On Render add the new hostname (and later the custom domain) to `ALLOWED_ORIGINS`, otherwise every
   API call is refused by CORS. `web/vercel.json` already allows the API host in `connect-src`.
4. Point the domain (when bought) at the market project; print QR codes only after that.
5. Verify: open `/` and `/furqan` on a phone, add a product, place an order, open the tracking link;
   `python -m scripts.audit_grants` still exits 0; Lighthouse on the preview.

Local: `cd web && VITE_API_URL=http://127.0.0.1:8000 VITE_APP=market npx vite build --outDir dist-market`
then `npx vite preview --outDir dist-market --port 5174` against a local `uvicorn app.main:app`.
The headless smoke used on 16-Sep-2026 (Playwright, Chromium) walked home → storefront → unknown slug
→ search → add → product → cart → checkout → placed/tracking → my orders → recognized home → desktop,
with zero console errors; first load ≈ 127 KB gzipped (budget 180).

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

## Not yet built (in order)

Service worker + version kill-switch for the market build · verified identity via salesman-issued
access links (`/join/{token}`, Phase 2) · regular-stock rail from Focus history (needs
`focus_customer_id` links) · web push · Quick Order mode · Arabic.
