# Marketplace QA harness

`market_qa.py` drives the YQ Marketplace in Chromium (Python Playwright) across every viewport we
design for and every state that matters, and turns what it measures into a pass/fail report plus a
screenshot set you can flip through. It is the harness `docs/MARKETPLACE.md` § *Visual QA harness*
describes, and the P5 gate of the v3 plan.

## Run it

```bash
# the market build, served by the preview (web/: npm run build:market && npm run preview:market)
python scripts/qa/market_qa.py --base http://localhost:5174 --out scratchpad/qa/shots

# two viewports only (390x844 + 1440x900) while iterating
python scripts/qa/market_qa.py --base http://localhost:5174 --out scratchpad/qa/shots --quick

# one state (keys: python scripts/qa/market_qa.py --list --out .)
python scripts/qa/market_qa.py --base http://localhost:5174 --out scratchpad/qa/shots --only cart_under,checkout_small

# production
python scripts/qa/market_qa.py --base https://yq-marketplace.vercel.app --api https://yq-ops-assistant.onrender.com --out scratchpad/qa/prod
```

In Git Bash prefix with `MSYS_NO_PATHCONV=1` so the URLs survive. The exit code is **1** when a hard
check fails, 0 otherwise (warnings never fail the run). `--api` is only used to find a real rep slug
for the storefront route; `--base` is the site under test.

Output in `--out`:

- `results.json` — every run, finding (level, check, route, viewport, measured values) and screenshot
- `index.md` — the hard failures, the warnings, and a table of every screenshot with route, viewport and state
- `shots/<viewport>/<state>[__<variant>].png`

## Nothing is ordered

`POST /public/market/order` is intercepted and answered with a mocked receipt
(`QA-0001` / `qa-token`), as are the order read behind it (`/public/shop/order/…`) and the phone
lookup (`/public/market/recognize`). Everything else — catalog, quote, gap fillers — is the live API,
so the numbers in the report are real numbers. Service workers are blocked, so a cached shell never
masks a broken build.

## Viewports

phones 320×640, 360×780, 390×844 (DPR 2), 430×932 · tablet 768×1024 (touch) · laptop/desktop
1024×768, 1280×800, 1440×900, 1920×1080. `--quick` keeps 390×844 and 1440×900, which are also the
"lead" viewports that carry the extras (full-page shots, element crops, slider timing, the campaign
injection, reduced motion, the opening). iOS safe-area insets cannot be simulated in Chromium, so
they are not covered here — check a real phone for the notch.

## States

`home`, `storefront` (a real `/{slug}`), `shop`, `shop_deals`, `shop_clearance`, `shop_best`,
`shop_moving`, `category`, `search`, `search_q`, `quick`, `cart_empty`, `cart_under`, `cart_met`,
`checkout_small`, `checkout_met`, `orders`, `me`, `about`, `about_trade`, `product`, `campaign`,
`reduced_home`, `reduced_cart`, `opening`.

The cart states seed `localStorage['yq-shop-cart:market']` before the first script runs
(`UK04-C ×5` = BHD 5 under the BHD 20 minimum; `UK15 ×14` = BHD 21 over it). Every state gets a fresh
browser context, so nothing leaks between them; the opening is the only state that does not
pre-stamp `sessionStorage['yq-splash-session']`.

## What it asserts

**Hard (exit 1)**

| check | rule |
|---|---|
| `horizontal-overflow` | `scrollWidth ≤ innerWidth + 1`; the offending elements are named (elements clipped by a scroller are not counted) |
| `console-error` / `page-error` | none |
| `request-failed` | no failed or ≥400 request except the event ping and the intercepted order calls |
| `google-fonts` | no request to fonts.googleapis/gstatic — the market self-hosts |
| `fonts` | `document.fonts.check` for Sora and Instrument Sans |
| `tap-target` | on touch viewports every visible control is ≥36px in both dimensions (a Tailwind `after:-inset-*` hit expander counts; a checkbox is measured by its label) |
| `input-font-size` | inputs ≥16px on touch (iOS zoom) |
| `object-fit-cover` | no cropped `<img>` outside circular avatars; a campaign creative that asked for `image_fit: cover` is a warning, not a failure |
| `banned-copy` | no `slow mover`, `no minimum`, `Save N%` |
| `missing-copy` | the state's required wording (e.g. "away from your wholesale order", "Wholesale order ready") |
| `home-duplicate-product` | a product code appears in at most one home section (the all-products grid excluded) |
| `slider-dots` | dot count == slide count |
| `bottom-nav` | exactly Home · Browse · Restock · Orders · My YQ |
| `pinned-search` | after scrolling 800px the phone search band is still within the top 140px |
| `search-band-clip` / `search-hint-ghost` | watched frame by frame for two rotations: the rotating hint never paints outside the pill unclipped, and the finished crossfade leaves one line, not a ghost |
| `aside-under-header` | the desktop aside starts below the sticky header, not behind it |
| `campaign-once` / `campaign-aside` | an injected campaign renders exactly once in the main column and never again in the aside |
| `reveal-stuck` | after scrolling to the bottom, every `.reveal` has opacity 1 |
| `reduced-motion-reveal` / `reduced-motion-opening` | under `prefers-reduced-motion` nothing is hidden and the opening is the static frame |
| `restock-actions`, `small-order-cta`, `small-order-sheet` | "Keep restocking" + "Request a small order", and the sheet opens on "Small order request" |
| `checkout-submit`, `placed-screen` | the mocked order lands on "Small order request received" / "Wholesale order received" |
| `opening-plays` / `opening-once` / `opening-reduced` | the opening plays on a fresh session, never on a reload, static under reduced motion |

**Warnings (reported, never fatal)**

tap targets between 36 and 44px · `slider-autoplay` / `slider-hold` (timing can be flaky on a loaded
machine) · `gap-filler-mix` (every gap-filler row tagged "completes your order" — the basket should
mix lines, not push one SKU) · `paste-repeat` (home sends the merchant to `/quick` more than twice) · `copy-drift` (a literal this harness checks is no longer in
`web/src/market/strings.ts`) · a missing rep slug.

## How it measures

- **Sweep, not one screen.** Geometry and text checks run at the top of the page and then one screen
  at a time (up to 6), and the results are merged — otherwise only the first viewport's worth of
  controls would ever be checked, and `content-visibility: auto` sections would have no text.
- **Copy comes from the app.** Every literal is mirrored from `web/src/market/strings.ts` in the
  `STR` table at the top of the script, and `verify_strings()` warns when one has drifted. Nothing
  was added to the app for testability: the harness uses roles, `aria-label`s and copy.
- **Product identity** on home comes from the thumbnail URL (`…/thumbs/<CODE>-product-320.webp`), so
  a product with no photo is invisible to the duplicate-rail check.
- **Tap targets** use the element box plus any absolutely positioned `::before`/`::after` with
  negative insets, which is how the app grows a 36px pill to 44px without changing its look.
- Findings are grouped per control shape and height, so a rail of twelve cards is one row with a
  count, not twelve rows.

## Related gates

`npx tsc -b && npx eslint . && npm run build:market && npm run build` in `web/`,
`python -m tests.test_shop`, and `python -m scripts.audit_grants` after any SQL.
