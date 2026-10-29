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

**Smoothness and motion (`smooth.py`, 28-Oct-2026)**

These run in English with motion on, on the lead viewports only (430×932 and 1366×768), each on a
fresh page of the state's context, so nothing they scroll or open reaches the other checks. A result
within its target is kept as a run note. Not run under `--reduced-motion` or `--lang ar`.

| check | state | rule |
|---|---|---|
| `scroll-jank` (S1) | home, phone | frame times of a scripted 900 px/s scroll over the first 6000 px at 4× CPU: p95, frames over 50 ms and the worst frame. A warning above p95 20 ms or any frame over 50 ms, **never fatal** (frame times depend on the machine); compare builds on one machine, interleaved |
| `photo-blank` (S2) | home | 150 ms after landing on a screen, every photo tile is loaded or drawing its placeholder (tint, shimmer or a running fade), never an empty white box |
| `panel-scroll` (S3) | home | closing a product panel leaves the page where it was (± 2 px) |
| `back-scroll` (S4) | home | Back from a page lands with the same card at the same place on screen (± 24 px) |
| `lazy-blank` (S5) | shop | opened while its code is still downloading (CDP holds every chunk the HTML does not name), the page shows the shell and a skeleton |
| `sheet-drag` / `sheet-overshoot` / `sheet-dim` (S6) | home, phone | a product sheet follows a touch drag, never rises above its resting place on the way in, and the dim behind it lifts with the drag |
| `photo-fade` (M1) | home | every product photo on screen transitions its own opacity (a card's `transition-transform` utility once replaced the fade) |
| `photo-flight` (M4) | home | tapping a card flies its photo into the sheet; the clone is removed and the sheet's photo is visible |
| `escape-stack` | home | Escape in the photo viewer over a product panel closes the viewer only |
| `page-wait` (M3) | home, phone | after the shell sat idle, the Browse tab opens the shop with no page skeleton (warm pages never flash one) |
| `palette-layers` | home, desktop | a product opened from a Ctrl+K result sits on top: the focus is in it, and Escape closes it first and leaves the palette |
| `reveal-flick` / `reveal-stuck` (M7) | home | a 4000 px/s flick shows the rows at once (`is-instant`), and nothing stays hidden |
| `lite-mode` (M16) | home | with `navigator.deviceMemory = 1` the page runs `html[data-lite]` and hides nothing |
| `reveal-phone` (M6) | home, phone | a phone has sections that rise in below the fold |
| `palette-focus` | home, desktop | Ctrl+K opens the search palette with the typing focus in its field |
| `back-entry` | shop, phone | arriving from another site on `/shop?f=clearance` and tapping a filter chip (which replaces the history entry), the header's Back goes to Home, never out of the shop |

A Playwright error inside one of them is the hard failure `smooth-error`. A check that finds nothing
to measure (no quick-view card, no zoom button) is a warning, never a pass.

**The Browse menu (`browse-menu`, 29-Oct-2026)** runs on every desktop width (1024×768 touch, 1366,
1920, or the `--widths` given), in English and Arabic, on Home, on a fresh page. With a mouse:
hovering Browse opens the menu with its first row on top (the category strip once painted over the
menu's first 45 px, so a click there opened the strip's chip), a click on Browse while hovering keeps
it open (it used to toggle shut what hover had opened), and a click on the first row opens that row's
page. On the touch row: a tap opens the menu and a second tap closes it.

**The feel pass (29-Oct-2026)** adds these, each written to fail on the build before it. The menu
checks run with the Browse menu (every desktop width, both languages); the rest are on the lead
viewports in English with the S-checks, except where the table says otherwise.

| Check | Where | What passes |
|---|---|---|
| `menu-intent` / `menu-aim` | home, desktop | Browse opens after a short rest, so a passing mouse never flashes it; a diagonal move from the first category into its panel keeps that panel, and resting on a row switches to it |
| `glide-scroll` | home | with a mouse, one wheel notch glides (it is still moving 250 ms in, rests within 1.5 s; its worst frame is a measured number, since headless Chrome paints on the CPU); a touch screen keeps native scrolling (no `html.lenis`) |
| `glide-layers` | home, desktop | with the search palette open the wheel moves nothing behind it, and the page never shifts sideways |
| `card-hover` / `press-spring` | home, desktop | a card under the mouse lifts ~4 px above its neighbours and its photo zooms to ~1.06; a pressed button gives and springs back past its size before it settles |
| `first-screen` | home, desktop | a fresh visit: the category discs come in one after another and with their photos (held 500 ms, none is an empty circle), the hero photo is whole from the first frame, and the entrance lets go within 3 s; with the catalogue held 2.5 s it waits for the discs |
| `calm` | home, desktop | under reduced motion: no glide, no entrance, no drift or title rise, every reveal shown, and (round 2) no running animation that moves anything: fades may run |
| `opening-logo` | home, desktop | while the opening's code is held 1.2 s, the first frame's logo tile stays on screen |
| `opening-motion` | home, desktop + phone | with the opening's code held 0.9 s: the served first frame already moves (the dawn rises from the first paint), the opening carries that dawn on instead of restarting it, nothing large is drawn through a live `filter`, the exit is booked when the opening mounts (a timer started it, so a busy page delayed it or cut it), and something keeps moving through the hold. Its frame rate is measured in real Chrome, not here |
| `reveal-wave` | home, desktop | a grid row's cards rise in reading order (right to left in Arabic) from ~24 px, never blurred, and a section title below the fold rises with the scroll |
| `hero-drift` / `header-shadow` | home, desktop | leaving the top slider it scrolls slower and dims; the header gains a soft shadow once the page moves (both where the browser has scroll timelines) |
| `hover-pill` | home, desktop | one pill element slides between the header strip's chips under the mouse |
| `add-flight` | home, desktop + phone | Add sends one copy of the photo to the visible cart (the mini-cart column, the Restock button, or the phone's Restock tab, whose bar comes back for it); it is gone within a second, the card moves ≤ 8 px, the count rolls and the stepper comes in. From the keypad it flies only once the keypad has gone; from the palette it does not fly at all (the cart is under its dim) |
| `page-glide` | home, desktop | an in-app page change rises 16 px over ~320 ms |

**The design review's polish (29-Oct-2026)** runs on Home and Cable, on the lead viewports, in both
languages, each check on a fresh page:

| Check | Where | What passes |
|---|---|---|
| `caps-labels` | home, category | no tracked ALL-CAPS label, light or night, the whole page scrolled through (the opening's Skip is not on these pages) |
| `bidi-chips` | category + the Browse menu, Arabic | each chip's count sits after its label, and each label reads right to left (a `dir="auto"` island turned "USB إلى C" round) |
| `kbd-badge` | home, Arabic, ≥ 1280 px | the search box's shortcut badge reads ⌘K left to right |
| `slides-alive` | home (English, desktop + phone), category (both languages) | a product slide is never a still picture: through its dwell the discs keep floating and the art breathes in (≥ 0.5 px each in 0.9 s, once the spring into the fan has settled) |

The empty Orders page must show its "How an order moves" steps (a needle of the `orders` state).

**Round 2 (29-Oct-2026, evening: smooth on the owner's laptop, motion he can see)** adds these on the
desktop lead viewport in English, each written to fail on the build before it:

| Check | Where | What passes |
|---|---|---|
| `panel-still` | home, desktop | opening a product (a real click) moves nothing on the page: the header, a neighbouring card and every fixed element hold still (≤ 1 px), the drawer never steps sideways, no layout shift, the dim is not blurred, the photo lands on the panel's photo (± 4 px), and once open nothing behind keeps animating. Run it headed too: a headless browser has no scrollbar to step by |
| `no-blur` | home, desktop | nothing is drawn through a `backdrop-filter` at rest, under a product panel or under the palette (the chat panel aside), and no lasting `will-change` or `clip-path` animation |
| `opening-motion` | home, desktop + phone | as above, and once risen the night frame stands still: nothing larger than half the screen is scaled or looped through the hold |
| `banner-reel` | home + category, desktop | the banner's centre disc turns through ≥ 2 products within 5.6 s of the page settling, and no disc is ever left white (the old photo gone before the new one arrived); on Home, the slideshow's pause button holds the reel and the light |
| `calm-alive` | home, desktop | under reduced motion the page is calm but alive: within 6.5 s the banner changes (a slide or a reel photo) and the search hint changes, a product panel fades in (≥ 120 ms from first sight to whole), and nothing moves (no transform animation runs, the panel never slides) |
| `search-typing` | home, desktop, both languages | the header's search hint is typed: an example appears a character at a time (≥ 3 characters within 1.2 s, in steps of one or two), is erased and the next one typed; with the trigger focused it stands on a whole example |

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
| `design_review.py` | Builds one self-contained `index.html` of the two runs, with WebP data URIs, the harness counts (BEFORE failures of the checks named in `--new-checks` are labelled "expected"; `--r4` is the R4 review's preset), Lighthouse and bundle budgets. `--title`, `--after-name` and `--data-note` say what the page compares. |

```bash
python scripts/qa/readonly_api.py --cache <scratch>/stub --upcoming business_data/upcoming_review/2026-09-24 &
# build each side against the stub (web/ of that checkout):  VITE_API_URL=http://localhost:8002 npm run build:market
python scripts/qa/review_run.py --dist <before>/web/dist-market --out business_data/design_review/<date>/before --label before --commit <sha> --port 5173 --full-check
python scripts/qa/review_run.py --dist web/dist-market --out business_data/design_review/<date>/after --label after --commit <sha> --port 5174 --full-check
python scripts/qa/review_pack.py --before .../before --after .../after --out .../pack
python scripts/qa/design_review.py --before .../before --after .../after --out .../index.html [--notes notes.html] [--new-checks photo-blank,lazy-blank]
```

Run the two sides one after the other, never in parallel. The `--reduced-motion` flag is also
available on `market_qa.py` directly.

## Related gates

`npx tsc -b && npx eslint . && npm run build:market && npm run build` in `web/`,
`python -m tests.test_shop`, and `python -m scripts.audit_grants` after any SQL.
