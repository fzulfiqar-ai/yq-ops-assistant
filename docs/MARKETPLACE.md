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
`/public/market[?ref=]` before the JavaScript downloads and preloads the first product photos. The
portal build keeps its self-destroying service worker; the market build ships the Workbox worker
with the version kill-switch described below.

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

## Deploying the marketplace (second Vercel project) — LIVE since 16-Sep-2026

- **Production:** `https://yq-marketplace.vercel.app` — Vercel project `yq-marketplace` (team
  `fzulfiqar-ai-s-projects`), environment `VITE_APP=market`,
  `VITE_API_URL=https://yq-ops-assistant.onrender.com`. The market build imports no Supabase code, so
  it needs no `VITE_SUPABASE_*` values.
- **Deploy from the developer machine:** `python -m scripts.deploy_web market` (or `portal`). Both
  projects are CLI-deployed from `web/`; the script swaps the CLI link, deploys, and restores it.
  Vercel builds with the project's env, so a local `.env` never leaks into the bundle.
- **Render:** `ALLOWED_ORIGINS` includes `https://yq-marketplace.vercel.app` (16-Sep-2026). Add the
  custom domain there too when it exists, otherwise every API call is refused by CORS. Note: Render's
  `autoDeploy` flag is on but pushes do not reach it (no GitHub-app webhook); deploys are triggered
  through the Render API.
- **Portal setting:** `shop_market_url = https://yq-marketplace.vercel.app` (Settings → Shop), so
  salesman links/QR and tracking URLs point at the marketplace.
- **Git integration (owner click, optional):** Vercel → project → *Connect Git Repository* → root
  directory **`web`**. After that, CLI deploys must run from the repository root, and every push to
  `main` deploys automatically with a preview URL per branch.
- **Custom domain (later):** attach the root domain to `yq-marketplace` and `ops.` to
  `yq-bahrain-ops`; add both to `ALLOWED_ORIGINS`; update `shop_market_url`; print QR codes only then.
- **Verify after a deploy:** `/version.json` changes build id, `/sw.js` is `no-cache`, `/robots.txt`
  disallows all, `/` and `/furqan` render on a phone, `python -m scripts.audit_grants` exits 0.

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

## Service worker (market build only)

`vite.config.ts → marketPwa()` generates `sw.js` (Workbox): precached shell; the catalog
(`/public/market`) NetworkFirst with a 4 s timeout and a 1-day cached fallback, so a sleeping API
still shows products; product photos CacheFirst (300 entries, 30 days); fonts stale-while-revalidate;
quote/order/event and `/version.json` never cached. `market/lib/sw.ts` reads `/version.json`
(`{build, kill}`, emitted at build with the commit sha, served `no-store` per `vercel.json`) **before**
registering: a different `build` → worker update + the "New version ready · Refresh" toast;
`kill: true` → unregister everything, drop every cache, reload once, register nothing. To pull the
plug on a bad worker: redeploy with `VITE_SW_KILL=1`, wait for clients to reload, then redeploy
without it. Verified headless: offline renders the cached catalog; kill leaves 0 registrations and
0 caches with no reload loop (`scratchpad/sw_smoke.py` on 16-Sep-2026).

## Performance baseline (Lighthouse mobile, simulated slow 4G, 16-Sep-2026)

| | first deploy | after the speed pass |
|---|---|---|
| Performance / Accessibility / Best practices | 39 / 96 / 96 | 64 / 100 / 100 |
| LCP · FCP · TBT · Speed Index | 4.5 s · 3.3 s · 2.7 s · 14.1 s | 4.2 s · 2.5 s · 0.5 s · 6.5 s |
| Legible text (≥ 12px) | 51% | 95% |

What moved it: the prefetch script preloads the first product photos (`fetchpriority=high`), the
search index and the rest of the first page render only when the main thread is idle, and the
service worker registers after `load`. LCP is still above the 2.5 s budget under simulated
throttling because the largest photo can only be requested after the catalog JSON arrives and it is
served from Supabase storage; the next lever is smaller first-screen thumbnails (≈160 px WebP) or an
image CDN in front of the bucket (Cloudflare, Track B). `.github/workflows/lighthouse.yml` re-measures
weekly and on demand; field values (p75) appear in Shop Analytics once merchants visit.

## Not yet built (in order)

Verified identity via salesman-issued access links (`/join/{token}`, Phase 2) · regular-stock rail
from Focus history (needs `focus_customer_id` links) · web push · Quick Order mode · desktop
persistent mini-cart · Arabic.
