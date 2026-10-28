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
python scripts/qa/market_qa.py --base https://yqmarketplace.com --api https://api.yqmarketplace.com --out scratchpad/qa/prod

# R5: the same walk in Arabic, at any widths (here the brief's 390 phone and 1366 desktop)
python scripts/qa/market_qa.py --base http://localhost:5174 --api http://127.0.0.1:8002 --out scratchpad/qa/ar --lang ar --widths 390,1366
```

`--lang ar` stores «عربي» as the phone's choice before the first script (`localStorage['yq-lang']`,
which `public/market-lang.js` reads), uses an `ar-BH` browser locale, and checks the Arabic copy
(`STR_AR`, mirrored from `web/src/market/i18n/ar.ts`) instead of the English. It adds three hard
rules: `<html lang="ar" dir="rtl">`, the Arabic face (IBM Plex Sans Arabic) loaded, and
`BANNED_AR` (a literal "out of stock" in Arabic, the WEKOME tier and luxury words). Every English
run asserts the opposite: an English page never downloads the Arabic font. `--widths` takes any
list of widths: a design viewport of that width, else a touch phone under 768, a tablet under
1024, a desktop; the first phone and desktop carry the lead-only extras.

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

the six R4 design viewports — phones 360×780 and 430×932 (DPR 2) · tablet 768×1024 (touch) · a touch
laptop 1024×768 (the desktop shell under touch rules) · 1366×768 · 1920×1080. `--quick` keeps 430×932
and 1366×768, which are also the
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
| `tap-target` | on touch viewports every visible control is ≥36px in both dimensions (a Tailwind `after:-inset-*` hit expander or the market's `.hit` class counts; a checkbox is measured by its label) |
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
| `opening-plays` / `opening-once` / `opening-weekly` / `opening-reduced` | the opening plays on a fresh device, never on a reload, never in a new tab within 7 days (nor its night boot frame), static under reduced motion |

**Warnings (reported, never fatal)**

tap targets between 36 and 44px · `slider-autoplay` / `slider-hold` (timing can be flaky on a loaded
machine) · `gap-filler-mix` (every gap-filler row tagged "completes your order" — the basket should
mix lines, not push one SKU) · `paste-repeat` (home sends the merchant to `/quick` more than twice) · `copy-drift` (a literal this harness checks is no longer in
`web/src/market/i18n/en.ts`, or `ar.ts` under `--lang ar`) · a missing rep slug.

## How it measures

- **Sweep, not one screen.** Geometry and text checks run at the top of the page and then one screen
  at a time (up to 6), and the results are merged — otherwise only the first viewport's worth of
  controls would ever be checked, and `content-visibility: auto` sections would have no text.
- **Copy comes from the app.** Every literal is mirrored from `web/src/market/i18n/en.ts` (which
  `strings.ts` re-exports) in the `STR` table at the top of the script — and from `i18n/ar.ts` in
  `STR_AR` — and `verify_strings()` warns when one has drifted. Nothing
  was added to the app for testability: the harness uses roles, `aria-label`s and copy.
- **Product identity** on home comes from the thumbnail URL (`…/thumbs/<CODE>-product-320.webp`), so
  a product with no photo is invisible to the duplicate-rail check.
- **Tap targets** use the element box plus any absolutely positioned `::before`/`::after` with
  negative insets, which is how the app grows a 36px pill to 44px without changing its look.
- Findings are grouped per control shape and height, so a rail of twelve cards is one row with a
  count, not twelve rows.

## Before/after design reviews

A design review compares two builds screen by screen. Four scripts do it, and **nothing reaches
production except GET requests and the quote**:

| script | what it does |
|---|---|
| `readonly_api.py` | A local stand-in for the API on 127.0.0.1:8002. It records production GETs once and replays them, replays the quote POST per body, and answers every other write itself (logged to `writes.log`; `upstream.log` proves nothing else went out). `--upcoming <review folder>` serves pending "Coming soon" rows as preview cards. |
| `review_run.py` | Serves one built `dist-market` with `vite preview --strictPort` and takes lab Lighthouse (mobile and desktop, median of 3). It runs the harness on the 8 review screens with `--reduced-motion`, which gives stable frames, and with `--full-check` also runs every state with motion on. It also writes `meta.json` and `bundle.json`. It refuses a build that is not pointed at the stub. |
| `review_pack.py` | Makes before\|after composites per screen × viewport (side by side for portrait, stacked for landscape, in CSS pixels, ≤ 1560 px) and pairs long pages tile by tile, plus `findings.json` per screen. |
| `bundle_sizes.py` | The first-load script / stylesheet gzip and the market's fonts against `.github/lighthouserc.json`. A font counts only when a file of the build names it (CSS `@font-face`, the HTML, a script or the service worker); the portal's fonts, copied by the shared `public/`, are listed as not loaded. `--gate` exits 1 on a broken budget (CI's web job). The Arabic faces (R5) load on an Arabic page only, so they are held to their own budget (`FONT_ARABIC_BUDGET`, 90 KB) and the lighthouserc font budget stays the English page's. |
| `design_review.py` | Builds one self-contained `index.html` of the two runs, with WebP data URIs, the harness counts (BEFORE failures that come from rules new in R4 are labelled "expected"), Lighthouse and bundle budgets. |

```bash
python scripts/qa/readonly_api.py --cache <scratch>/stub --upcoming business_data/upcoming_review/2026-09-24 &
# build each side against the stub (web/ of that checkout):  VITE_API_URL=http://localhost:8002 npm run build:market
python scripts/qa/review_run.py --dist <before>/web/dist-market --out business_data/design_review/<date>/before --label before --commit <sha> --port 5173 --full-check
python scripts/qa/review_run.py --dist web/dist-market --out business_data/design_review/<date>/after --label after --commit <sha> --port 5174 --full-check
python scripts/qa/review_pack.py --before .../before --after .../after --out .../pack
python scripts/qa/design_review.py --before .../before --after .../after --out .../index.html [--notes notes.html]
```

Run the two sides one after the other, never in parallel. The `--reduced-motion` flag is also
available on `market_qa.py` directly.

## Related gates

`npx tsc -b && npx eslint . && npm run build:market && npm run build` in `web/`,
`python -m tests.test_shop`, and `python -m scripts.audit_grants` after any SQL.

# Portal harness

`portal_qa.py` does for the ops portal (the office AppShell and the salesman app) what
`market_qa.py` does for the marketplace. Every portal screen sits behind a sign-in, so it works
from a **recording**: the owner signs in once in a window the harness opens, the harness walks
every page and saves what each page received from the API, and from then on every run
**replays** that recording against any local build with a fake session. No login, no network, as
often as needed: before/after design reviews, failure injection, smoothness timings.

## The one sign-in (`--record`)

```bash
python scripts/qa/portal_qa.py --record --base https://ops.yqmarketplace.com \
    --api https://api.yqmarketplace.com --out business_data/design_review/<date>/portal [--rep "<Focus name>"]
```

A Chrome window opens on the portal's login page. Sign in as usual (the harness waits up to 10
minutes, `--wait-min`). The walk then starts by itself: every page in `--list` (42, a few seconds
each, about five minutes in all), each loaded, given time to finish its requests, and scrolled to
the bottom once so lazy sections load. Leave the window alone until it closes by itself. A detail page (a purchase order, a Market
Intel case, a merchant profile) is the first one linked from its list page. `--only` records some
pages again and merges them into an existing recording.

`--rep "<Focus name>"` (or a salesman id) also fetches the reads the API lets an admin take as a rep
(today `/shop/me/followups?rep=`, see `app/shop_api.py`) and files them under the plain key, so a
`--as salesman` replay shows that rep's Due list.

What is written, all under `<out>/rec/`:

- `index.json` — when and where it was recorded, the signed-in role, every page walked with the
  route it used and the requests it made, and the entries: `METHOD path?sorted-query` → body file,
  status, content type. The query is sorted, a signed URL's `token` and cache busters are left out.
- `api/<path>-<hash>.<ext>` — one file per distinct GET answer of the API; `supabase/…` for
  Supabase storage GETs (the product photos and Finds pictures the pages show, so a replay draws
  them) and REST GETs (the portal reads none today).

### Safety

- The browser context is **non-persistent** (a throw-away profile): no `storage_state`, no cookies,
  no tokens are ever read out of it, and it is deleted when the window closes. `--rep`'s extra reads
  are fetched inside the page, so the token never reaches Python.
- Only GET answers are saved, never a request header. A signed URL's `token=` and any JWT inside a
  recorded body are replaced with `qa-masked` before the file is written.
- Every request that is not a GET is answered **inside the browser** (`{"ok": true, "qa": "not
  sent"}`) — except Supabase's `POST /auth/v1/token`, the sign-in and the token refresh. Sign-out
  and password changes are never sent (a sign-out would end the owner's other sessions). The
  summary lists every write that was blocked.
- Nothing is recorded before the sign-in is confirmed (`/me` answered and the page left `/login`).
- `--out` must be inside a `business_data/` folder that Git ignores; the harness refuses anything
  else. A replay of a real recording holds real data too, so its `--out` follows the same rule (only
  the committed synthetic fixture may be replayed anywhere).

## Replay (`--replay`)

```bash
# web/: npm run build   (the portal build goes to web/dist; web/.env must be present)
python scripts/qa/portal_qa.py --replay --dist web/dist --rec business_data/design_review/<date>/portal \
    --out business_data/design_review/<date>/after                      # admin · 390,1024,1440 · light,dark

# the salesman app (SalesmanShell): the fake /me says salesman; a cart is seeded on phones
python scripts/qa/portal_qa.py --replay --dist web/dist --rec … --out …/salesman --as salesman

# failure injection: every API call of the page answers 500 (S10)
python scripts/qa/portal_qa.py --replay --dist web/dist --rec … --out …/fail-api --fail-api

# a few pages, one width, while iterating
python scripts/qa/portal_qa.py --replay --dist web/dist --rec … --out …/quick --only orders,receivables --widths 390 --themes light

# before/after: build each side, replay each against the SAME recording, one after the other
python scripts/qa/portal_qa.py --replay --dist <before>/web/dist --rec … --out …/before --port 5175
python scripts/qa/portal_qa.py --replay --dist web/dist          --rec … --out …/after  --port 5175
```

The build is served with `vite preview --strictPort` on `--port` (5175), started and stopped by
the harness (`taskkill /T /F` on Windows); a busy port, or a port serving another build, is refused.
Each context gets a fake Supabase session and a cached `/me` (the `perf_auth_check.py` approach);
`--as` decides the role. Then, per request:

| request | answer |
|---|---|
| the build's own files | served by `vite preview` |
| API GET in the recording | the recorded status and body |
| API GET not in it | the same path with the closest query (`near`, logged), else 404 JSON (`miss`, logged) |
| API write | `{"ok": true, "qa": "not sent"}`; the cart quote is priced from the recorded staff catalog |
| Supabase auth | a fake session / user, valid nowhere |
| Supabase storage miss | a grey placeholder (`asset`, logged) |
| anything else | never sent (`external`, logged) |

The page's clock (`Date` only) starts at the recording's instant and runs, so "3 days old" and
date-window queries read the same on every replay (`--clock real` to use today). Timers, animation
frames and `performance.now()` stay real.

Output in `--out`: `results.json`, `index.md` (hard failures, warnings, the S8 and S7 tables, the
scroller per page, the misses, every screenshot), `misses.json`,
`shots/<width>/<theme>/<page>.png` and `preview.log`. Exit 1 on any hard failure.

Widths: under 768 a touch phone (`390` → 390×844, DPR 2: tap targets and input sizes are checked);
1024 → 1024×768, 1440 → 1440×900, any other a desktop. The first phone and the first desktop are
the "lead" viewports for S1 and S4, which run in the first theme only, as does the S8 menu chain.
A page takes about 6 s per width and theme, so the default run (42 pages × 3 widths × 2 themes, plus
the chains) is about half an hour: narrow it with `--only`, `--widths` and `--themes` while iterating.

## What it checks

**Hard (exit 1)**

| check | rule |
|---|---|
| `horizontal-overflow` | the document's `scrollWidth ≤ innerWidth + 1`, the offenders named |
| `console-error` / `page-error` | none — an error the harness caused on purpose (a miss, an injected 500, a recorded error) is not counted |
| `request-failed` | no failed or ≥ 400 request other than those the harness answered on purpose |
| `tap-target` | on touch every visible control ≥ 36 px (market_qa's measure: the box plus a hit-area expander) |
| `input-font-size` | inputs ≥ 16 px on touch (iOS zooms below) |
| `fail-api-no-error` / `fail-api-false-empty` | **S10**, `--fail-api` only: the page shows an error (couldn't load / could not load / try again / retry / something went wrong / server error, or a Retry · Try again · Reload button) and never a false empty state ("No orders yet", "Everything ordered has been received", "No transfers in this window") |
| `error-boundary` | `--fail-api` only: the failure crashed the page into the portal's error screen (its "Something went wrong · Reload" would otherwise pass S10) |
| `page-crash` / `harness-error` | Playwright could not drive the page, or the replay could not answer a request |

**Warnings (reported, never fatal)**

| check | rule |
|---|---|
| `tap-target` | between 36 and 44 px on touch |
| `content-clipped` | content cut off at the right edge of a page-wide `overflow-x: hidden` frame (the office shell's `<main>` hides what a document would show as overflow) |
| `first-paint-white` | **S7**: a screencast from the navigation on; a near-white, near-uniform frame before the app paints is a flash when the app it turns into is dark (one line per width and theme, the pages and milliseconds in the S7 table) |
| `click-to-paint` | **S8**: in one tab, the menu from page to page (the office drawer is opened first on a phone); from the click to the new page's heading > 150 ms, or more than one loader (spinner, skeleton, `aria-busy`) on the way. API answers are instant here, so this is the app's own cost |
| `inner-scroller` | **S9**: on a touch phone the page scrolls inside an `overflow: auto` box instead of the document (no collapsing address bar, no tap-the-top, no browser Back restore); the scroller of every page is in the S9 table |
| `chrome-height` | **S12** (`--as salesman`, phones, a seeded cart): fixed and stuck-sticky chrome covers > 200 px after scrolling 400 px, overlaps counted once |
| `scroll-jank` | **S1** (lead phone): frame times of a scripted scroll at 4× CPU — `smooth.scroll_jank` when the document scrolls, the same measure inside the box otherwise; p95 over 20 ms or any frame over 50 ms |
| `back-scroll` | **S4** (lead viewports, the pages with a detail link): scroll, open the detail, Back — `smooth.back_scroll` for a document scroller, the box's `scrollTop` otherwise; more than 24 px off |
| `recorded-error` | the recording itself holds an error answer (production answered so) |
| `route-drift` | a route in `App.tsx` or a link in `lib/nav.ts` that the harness does not walk, or the reverse |

`smooth.py` is used when this checkout has it; without it the document-scroller halves of S1 and S4
are skipped with a note. `--no-smooth` and `--no-nav` skip S1/S4 and S8.

## Self-test

```bash
python scripts/qa/portal_qa.py --selftest     # needs web/dist (npm run build); builds nothing
```

First it proves the record mode's write guard on this machine with nothing real: a throw-away
listener on a free port plays the API, the recorder's own route sits in front of a headless
browser, and every way a page can write — fetch POST/PUT/PATCH/DELETE, a keepalive fetch,
`sendBeacon`, XHR, a form POST — is fired at it. The listener must receive GETs only, every write
must be answered in the browser, and the GET must be saved under its sorted key with its signed-URL
token and JWT masked and its `Authorization` header in no file.

Then it replays the committed **synthetic** fixture (`fixtures/portal_min/rec/`: invented names,
codes and round numbers only, `"synthetic": true`) against `web/dist` in three passes — admin at
1440 light and dark, `--fail-api` with two canary pages the harness serves itself (one must fail
S10, one must pass), and `--as salesman` on a 390 phone with a seeded cart — and checks the harness
end to end: screenshots made, the deliberate miss logged (and not double-counted as a console
error), S7, S8, S9, S4 and S12 measured, the failing canary caught and the honest one passed, the
cart priced locally. It prints PASS or FAIL per check and what the replay found on the real pages
(about three minutes). Output goes to a temp folder (or `--out`).

## Known limits

- The data is the admin's. A salesman replay shows the admin's answers for the caller-scoped reads
  (`/shop/me`, `/shop/me/today`, `/shop/me/book`, …); only `/shop/me/followups` takes `?rep=`.
- A recording is a snapshot: a newer build that asks for a new endpoint or a new query gets misses
  (listed in `misses.json`, near matches used where the path is known) until it is recorded again.
- Timings (S1, S7, S8) depend on the machine and on what else runs on it: compare before and after
  back to back on the same machine, never in parallel.
- The walk is recorded at 1440 px: a phone layout that asks for something the desktop did not gets
  a miss (listed), not an answer.
- Only page loads, the menu and Back are driven: dialogs, forms and sheets are not opened.
- iOS safe-area insets cannot be simulated in Chromium.
