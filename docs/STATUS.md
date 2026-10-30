# Ops status: where the work stands (continuation record)

Last verified 21-Oct-2026. The master plan, with the backlog, the order of work and the acceptance criteria, is in the
owner's plans folder (its path is in Claude's project memory), not in this repository.

## Live

- Polish, ship 2 (released 30-Oct-2026 overnight on the owner's go, `4f65e0f`, web and API; release log in
  `docs/MARKETPLACE.md`): the ops portal and the salesman app on the marketplace's standard. Office: the plum brand
  (Instrument Sans + Sora, the night sidebar, one KPI tile, one card, tokens instead of hex; it came in as one merge,
  `433ca4b`, so it can be reverted on his word), a first frame that is never white, "Connecting" only after 4 s, a
  weekly intro that can be skipped, page changes that keep the old page while the next one's code loads (fetched
  ahead on hover and when idle), an error boundary per page, the window scrolls, the seven hand-made pop-ups are
  real sheets (Esc, focus, an exit), toasts above sheets, no blur. Salesman app: no blur on the tab bar or header,
  each tab keeps its place, 16 px inputs and 44 px targets, the shop pick survives a WhatsApp detour (12 h), and
  sign-out clears a shared phone. Proof: the browser checks `web/scripts/*_check.py` (admin, sales, shell, layers,
  boot) and their node rules in CI. An independent re-rate scored live 7.0/10, new 7.5/10 (about 8.2 with its first
  item fixed, which it is: coloured KPI figures keep their size). Open: the office's sheets stay light in dark mode
  (his call); the shop pick is not tied to the signed-in rep (a phone the first rep did not sign out of keeps the
  shop up to 12 h); the Undo card can sit over the checkout while a sheet is open; Spotted covers a row's Qty and +
  at rest on a phone; Back with a product sheet open leaves the catalog; delivery shows a zero amount, not "Free";
  the Data upload page and the rep's desktop catalog shift as they load; KPI labels wrap at some widths; the chart
  pages paint 260-420 ms after a click headless (target 150 ms).
- The Browse menu fix (29-Oct-2026, `dbe8899`): the menu sits above the category strip, and a click keeps it open.
- Live (30-Oct-2026, `c559ac1`, web and API; `docs/MARKETPLACE.md` § Motion and the release log): the
  marketplace feel pass, the design review's fixes, the smooth opening and living slides; then round 2 for the
  owner's laptop (desktop only, the phone unchanged): a product opens with the page still, solid surfaces instead of
  frosted glass, the opening cheap on integrated graphics, the banner reel with the count and the light, the typed
  search, and "calm but alive" for computers that ask for reduced motion (his Windows had animations off, so the
  site stood still; switched on for him). The Lenis glide stays (measured against native scrolling on his laptop).
  Released on the owner's go after an independent re-rate (live 5/10, new 7/10) and a code review, whose findings
  were fixed first. Open: a panel's mount blocks ~120 ms headless (measure on his GPU), the calm panel photo pops
  late, the opening's hold (his call), reel photos that look alike, no pause control for the search hint.
- YQ Assist, the marketplace chat (release assist1, 29-Oct-2026; `docs/MARKETPLACE.md` § YQ Assist): the free-AI
  repair, the chat with voice, photos and lists, and the portal page. The chat is shown only once the owner switches
  it on in Settings (on since 29-Oct-2026). The fix for voice uploads (`5e0279c`) went live with assist2.
- YQ Assist v2 (release assist2, live 29-Oct-2026 at `627d1f4`; `docs/MARKETPLACE.md` § YQ Assist v2): selling in
  stock only, problem reports with tickets, escalation, the new look and the portal's YQ Assist tabs. Tickets are
  still off and no policy answer is written yet, so every policy question goes to the rep.
- YQ Assist 2.1, the salesman voice (release assist2.1, live 29-Oct-2026 ~20:35 at `3de9168`;
  `docs/MARKETPLACE.md` § YQ Assist 2.1): it asks before guessing, one next step with quick replies, what shops buy
  with an item, it speaks first, the bubble beside the button.
- The marketplace polish, ship 1 (released 28-Oct-2026 on the owner's go; `docs/MARKETPLACE.md`, release log):
  smoother photos, pages, sheets and Back, honest prices when the price check is slow or down, and the QA
  harness's smoothness guards (`scripts/qa/smooth.py`). Open: the owner's call on the opening's hold time (1.9 s,
  2.8 s on a first visit; the plan said 1.6 s); 11 minor findings of the final review, deferred.
- Ops skills, phase 1 and its upgrade: `/yq-load` (check, then the Windows box, then the writer, then the e-mail) and
  `/yq-brief` (with the team message). The engine is `python -m scripts.ops`. The one program that writes to the database
  is `scripts/yq_load_go.py`; the one that sends the e-mail is `scripts/yq_send.py`. Tests: `tests/test_ops_*.py` (every
  suite runs in CI). Reference: `docs/OPS_SKILLS.md`; backups and the restore drill: `docs/BACKUPS.md`.
- Done (15-Oct-2026): H, the phase 1 hardening pack: one full e-mail per data set, a check that finds nothing new ends
  without a GO line, the brief points only at built commands, the check shows how long each report takes, `pack` for
  plain backups, and the documents brought current.
- Done (18-Oct-2026), the owner's decision of that day:
  - A Stock ledger that lacks only SIM now loads. SIM never counts, so those movements go with the
    load and come back with the next export that has them. The box shows an ATTENTION line, and SIM
    vouchers are never listed to check.
  - Anything else missing still stops.
  - The box says when a load that must wait will write (a WAIT line), and the wait's journal line
    gives the time.
  - The cause, confirmed by the owner: the SIM items became Service items in Focus (the Sim group,
    under Non Cogs Groups). Focus keeps Service items out of its stock reports, so both stock reports
    lost them, history included, while the Sales day book kept them. Drop 181026 was loaded without
    its ledger.
  - SIM is told by the item's own name in whole words. The substring rule of v_sales also takes
    salesmen's names, and the warehouses are named after salesmen.
- `/yq-month`: the monthly sales workbook in the layout of the September report (`python -m scripts.ops report month
  [YYYY-MM]`, whole months only, written as a DRAFT under `exports/reports/monthly/`). Its golden test rebuilds September
  against the reference file (local only), the reference's sheets in their order.
- The month-end pack (21-Oct-2026): `/yq-month` adds Stock (as on the month's last day), Movements, Reconciliation,
  KPI and KPI log between Daily and Data, totals only. The DRAFT writes the month's facts and, once, the
  commentary template; Claude drafts the ten comment lines and three notes with placeholders only,
  `python -m scripts.ops check` refuses any typed figure and prints the FINAL line, and only the owner's
  "approved" runs it (refused when a load moved the month's sales figures; the DRAFT stays); then
  `/monthly-review` for the same month. `recon <YYYY-MM>`: the four reconciliations (main store and vans from the
  counts, sales and receivables with the month's Ledger export, purchases' papers). `/yq-kpi`: the KPI scorecard
  with the 90-day plan's rows, and the KPI log written only after his yes (`scripts/ops/kpi.py`, `kpi_log.py`,
  `recon_blocks.py`, `ledger_export.py`, `textcheck.py`, `workbook/facts.py`).
- `/yq-week` (21-Oct-2026): the weekly sales workbook for a whole Sunday-to-Saturday week (`python -m scripts.ops
  report week [YYYY-MM-DD]`, the last full week by default, under `exports/reports/weekly/`): eleven sheets, every
  tile in four windows (the week, the four weeks before, the same days last month, the month to date against the
  KPI step), three comment lines as formulas, the Actions sheet (counts only) and the Stock and Movements sheets it
  shares with the month-end pack (totals only); a build record with the due day for KPI 7. The skill then follows
  `/weekly-review` for the same Saturday; its Top 5 is written to the portal only after the owner's answers.
- The count sheets (21-Oct-2026): `/yq-month prep` (`python -m scripts.ops recon prep [full]`) makes one workbook of
  blind sheets under `exports/reports/counts/` (each van and outlet, the main store's cycle list: the fast movers,
  the twenty highest-value items and the month's movement exceptions; `full` every item), and `/yq-month count
  <file>` reads a counted copy back against the stock ledger at the end of the day before the count, in units
  and BHD at landed cost; results kept in `business_data/ops/counts/` (`scripts/ops/recon.py`, `counts.py`).
- `/yq-stock` with the Stock & Order Plan (21-Oct-2026): the screen and an editable workbook per data date under
  `exports/reports/stock/` (the order list per supplier, every item with its status, fast movers, the ageing review
  against the clear target with the price cuts' effect, the damage store, goods on the way, the settings and the
  season's lifts); the order rule per supplier (measured lead time, the monthly rhythm, the season, a longer
  history for items out most of the window, whole cartons); `ordered` / `cancel` log goods on order only after
  the owner's yes; `team` makes the copy without costs; `/yq-load` ends by showing it. Values at the landed cost
  from goods-received notes, the pricing-sheet cost where an item has none. Reference: `docs/OPS_SKILLS.md`.
- `/yq-moves` (21-Oct-2026): every transfer between the main store and the vans and outlets paired by what its
  two vouchers hold (`scripts/ops/pairing.py`, the one module the portal shares in the phase 2b release), each
  van's and outlet's stock from opening to closing with its check, and what is to check in BHD at landed cost;
  accessories only (`scripts/ops/moves.py`).
- The order list counts the vans, the outlets and stock moving between them beside the main store
  (`scripts/ops/position.py`, `stock.position_stores`); the Stock & Order Plan file is layout v3 ("Vans and
  outlets"), and v1 and v2 files still read for `ordered` and `team`.
- `/yq-watch` (21-Oct-2026): the action list after each load (`scripts/ops/checks.py`): the transfer items, ACT
  or CHECK with their BHD, recorded once per ledger before the brief; the load check's and the Command Centre's
  items read-only; explain and done on the owner's words. The brief gains a MOVEMENTS line and the e-mail a
  Transfers paragraph.
- The phase 2b release (22-Oct-2026, live on `main`, the API and the portal; every part of
  `scripts\r9_release.ps1` passed, `moves portal` PASS for the last 7 days and the whole ledger, the gate compare
  clean):
  - the portal's Stock Movement page pairs the transfers by their contents with `scripts/ops/pairing.py`, the
    code of `/yq-moves`, over the view `v_transfer_lines` (`app/transfer_audit.py`; a transfer booked in under
    two receipts names both); each van card takes its stock from the ledger (`v_salesman_stock_recon`); the W1
    notice is gone and `v_transfer_flow` is dropped;
  - the ID numbers in the stored stock narrations are masked (`row_hash` unchanged);
  - the four load follow-ups: each temporary table dropped once applied; the batch importer's lock taken as the
    loader opens; a load that rolled back closes "stopped and rolled back"; the refresh time recorded without
    the commit's hold;
  - X1: `scripts/yq_team_post.py` stores each load's team message in `team_messages` after the delivery step,
    and the Command Centre shows it with a Copy button (`GET /command/team-message`, admin and management).
- Release r10, the long-number mask (22-Oct-2026, live on `main`, the API and the database): every run of 10 or
  more digits in a narration is masked, its last 3 digits kept, by the parser (`scripts/text_mask.py`), in `v_sales`
  and in the stored narrations (`scripts/r10_long_number_mask_migration.sql`, which on the owner's yes also masked
  the 9-digit runs the optional R7b data step had left); `row_hash` unchanged. Read-only after the release: no run
  of 9 digits or more left in clear in `order_lines` or `stock_movements`, `moves portal` PASS.
- Release backups: `python -m scripts.yq_backup release <label>` seals an encrypted backup outside OneDrive, and
  `unpack` opens one for a rollback (R1, `docs/BACKUPS.md`).
- The keep-alive Worker reads Supabase every ten minutes and alerts by Telegram once it is deployed (the code
  part of making the repository private; the owner's steps are kept outside the repository).
- The photo archive: `python -m scripts.yq_photos archive [--apply]` and `verify <archive>` (see `docs/BACKUPS.md`).
- The daily database backup: `python -m scripts.yq_nightly`, started by the Task Scheduler task `YQ Daily Backup`
  (Sunday to Thursday, while the owner is logged on; see `docs/BACKUPS.md`).
- Market Intel v2 (migrations r8m and r8n). Reference: `docs/MARKET_INTEL.md` and the pages beside it
  (`docs/MARKET_INTEL_HOWTO.md`, `docs/MARKET_INTEL_RELEASE.md`).
- The faster load (20-Oct-2026, live on `main` and the API): the check compares the Stock ledger voucher by
  voucher instead of in the engine preview and shows a short block (the whole check is in `check.txt`); the
  gate snapshot, the backup, the reconcile and the verify batch their queries; and the load writes every Focus
  table in one transaction (`scripts/load_txn.py`), so it waits only around production's 8 o'clock agents.
  `ONE_TRANSACTION = False` in `scripts/yq_load_go.py` brings back the older loader, with no deploy.
- Real loads so far: the drops 111026, 141026, 151026, 181026 (without its Stock ledger), 201026 and 221026 (the
  first one-transaction load: 3.8 minutes). Each load's journal is in `business_data/ops/loads/` (git-ignored).

## In progress

- The month-end pack's first real run is on the first November load (with the October Ledger export in the drop).
- YQ Assist 2.2 (release assist2.2), built 29-Oct-2026 on branch `worktree-assist-sales`, not released: the YQ mark
  in the orb, a bubble that names something real and changes each visit, product names with the codes, one
  conversation with a human voice (never the same reply twice, an order for "what should I order", help for "how can
  you help me sell", an apology for a complaint), problems talked through before the rep, Arabic in the office's own
  lines, "Tell me when it's back" in the chat (`docs/MARKETPLACE.md` § YQ Assist 2.2).
  - Waiting for: the owner's review of the transcripts and the screenshots, then his go.
  - Release steps: `docs/RELEASE.md` § Release assist2.2 (no migration).
  - Scorecard: `python -m scripts.eval_assist`, with the new gate `fresh`.

## Not built (the engine prints "not built yet")

Every command the engine lists is built. The specification was the approved ops-skills plan of 8-Oct-2026 (the
sections from /yq-week to /yq-kpi, and "Build order").

## Order of work (each step needs the owner's go)

Market Intel phases 3 and 4 once the rep reports are enough (judged from about 11 to 25 November).

## Open items

- The AI weekly and monthly reviews have not run on this PC yet: from a code worktree their pack finds no database
  login (it reads only its own checkout's `.env`), so the first `/yq-week` in the project folder is their first run.
- KPI 1a and 1b are worked out for the month of the stock ledger's last day and the month before it only (an older
  month reads NOT EVIDENCED); `stock.build(as_of=)` could serve any month. KPI 5a counts sales with returns not
  deducted; the month's Ledger export (`ledger_export`) could net them. Both change the KPI rows and their tests.
- `/yq-watch`'s "Not checked yet" line still says both checks come with the month-end release: the returns against
  last month's kickback tier are now in the sales reconciliation (`recon`), and sold below cost is not built.

- The phase 2b release's last proof: the next load's team message in the Command Centre, with its Copy button
  (X1; the release's own checks cannot see it before a load). Not in 2b (the owner's decision): X3, the two staff addresses still in tracked files, and D11, the
  Market Intel grant. The portal's Data page upload still uses the older loader.
- Not masked yet (the r10 review, for the owner's decision): `selling_prices.narration` (the read-only role reads
  it) and `ledger_entries.narration` (service role only); AI replies from before r10 kept up to 7 days in
  `query_cache`; a local 8-digit phone number typed without its country code (an 8-digit run, like the order
  numbers).
- Profitability is held back at every load until Focus posts the cost of sales again.
- `/yq-stock`, answered by the owner on 20-Oct-2026: the measured lead time wins (per supplier once it can be
  measured); goods on order are logged when he says an order is placed; the order list counts the vans and
  the outlets beside the main store (his choice of 21-Oct-2026: all sellable stock, transfers inside their
  deadline included); items out of stock most of the window take a longer history; the sales team is the
  only lever on old stock. Still open: which items are display stands (the catalog's hidden items are the
  candidates).

## Owner decisions (15-Oct-2026)

- The KPI step stays as configured. The order after H was the monthly report, then stock, then moves.
- SIM transfers are left out of the storekeeper check (`/yq-moves` is accessories only).
- The WEKOME codes take the approved Categories rows (set in the git-ignored config); a new Screen Protectors row.
- `/yq-stock` values stock at the landed cost from goods-received notes, the pricing-sheet cost only where none exists.

## Rules that bite

- The project folder stays on `main`; code changes are made in worktrees; never run a load, `scripts.refresh`,
  `scripts.apply_sql` or a deploy script from a worktree.
- Run `python -m scripts.ops selftest --repo-clean` before every commit, and commit by path: no names, numbers, prices or
  totals in anything committed.
- A change to an engine file on `main` (`gitinfo.ENGINE_PATHS`: the `app` and `scripts` Python files outside
  `scripts/ops` and `scripts/yq_*.py`, and the requirements files; and `scripts/ops/pairing.py`, the one file of
  `scripts/ops` the API runs, `gitinfo.ENGINE_FILES_IN_OPS`) makes the next load refuse until the API is deployed.
- `scripts/views.sql` is generated: edit the `*_migration.sql` file, and run `python -m scripts.audit_grants` after any
  migration.
- A load's e-mail goes once for each data set. `load status` keeps small offset marks in the run's folder, so follow a
  load from one session only; `python -m scripts.ops status` is read-only.
