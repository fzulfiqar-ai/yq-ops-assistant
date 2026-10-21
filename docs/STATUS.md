# Ops status: where the work stands (continuation record)

Last verified 21-Oct-2026. The master plan, with the backlog, the order of work and the acceptance criteria, is in the
owner's plans folder (its path is in Claude's project memory), not in this repository.

## Live

- Ops skills, phase 1 and its upgrade: `/yq-load` (check, then the Windows box, then the writer, then the e-mail) and
  `/yq-brief` (with the team message). The engine is `python -m scripts.ops`. The one program that writes to the database
  is `scripts/yq_load_go.py`; the one that sends the e-mail is `scripts/yq_send.py`. Tests: `tests/test_ops_*.py` (14
  suites, all run in CI). Reference: `docs/OPS_SKILLS.md`; backups and the restore drill: `docs/BACKUPS.md`.
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
  against the reference file (local only).
- `/yq-stock` with the Stock & Order Plan (21-Oct-2026): the screen and an editable workbook per data date under
  `exports/reports/stock/` (the order list per supplier, every item with its status, fast movers, the ageing review
  against the clear target with the price cuts' effect, the damage store, goods on the way, the settings and the
  season's lifts); the order rule per supplier (measured lead time, the monthly rhythm, the season, a longer
  history for items out most of the window, whole cartons); `ordered` / `cancel` log goods on order only after
  the owner's yes; `team` makes the copy without costs; `/yq-load` ends by showing it. Values at the landed cost
  from goods-received notes, the pricing-sheet cost where an item has none. Reference: `docs/OPS_SKILLS.md`.
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
- Real loads so far: the drops 111026, 141026, 151026, 181026 (without its Stock ledger) and 201026. Each load's journal is in `business_data/ops/loads/`
  (git-ignored).

## In progress

- Nothing is being built; the next step waits for the owner's go.
- The faster load's review follow-ups: the read-only test now refuses the loader (no deploy). The other four
  change `scripts/load_txn.py` or `scripts/refresh.py`, so they go with the next API deploy: each temporary
  table dropped once applied; the batch importer's lock inside the transaction; `refresh.py` passing the
  loader's own words up, so that a load that rolled back is journalled as writing nothing (today it reads
  LAST LOAD FAILED, the careful reading, and the next /yq-load replaces it as before); and the refresh time
  recorded without the commit's hold.

## Not built (the engine prints "not built yet")

`watch`, `moves`, `recon`, `kpi`, the weekly workbook (`report week`) and `check` (a drafted text against the computed
figures). The specification is the approved ops-skills plan of 8-Oct-2026 (the sections from /yq-watch to /yq-kpi, and
"Build order"). Phase 2 groundwork exists: `scripts/ops/ledger.py` (the stock ledger as whole vouchers, the narration
reduced to a hash) and `scripts/ops/items.py` (the item-name bridge), covered by `tests/test_ops_moves.py`; `/yq-stock`
leaves a hook for the van columns that `/yq-moves` will fill.

## Order of work (each step needs the owner's go)

`/yq-moves` and `/yq-watch`, the phase 2b release (Stock Moves views and engine speed), P3b
(`/yq-week`, the Stock and Movements sheets, the monthly comment lines), P4 (reconciliations and `/yq-kpi`), then Market
Intel phases 3 and 4.

## Open items

- The portal's Stock Moves page: its Pending and van figures are not reliable until the phase 2b release. It pairs issues
  and receipts by voucher number, and van stock comes from a snapshot of the main store only. Use `/yq-moves` once it is
  built.
- Profitability is held back at every load until Focus posts the cost of sales again.
- `/yq-stock`, answered by the owner on 20-Oct-2026: the measured lead time wins (per supplier once it can be
  measured); goods on order are logged when he says an order is placed; the order list counts the main store
  only until `/yq-moves`; items out of stock most of the window take a longer history; the sales team is the
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
  `scripts/ops` and `scripts/yq_*.py`, and the requirements files) makes the next load refuse until the API is deployed.
- `scripts/views.sql` is generated: edit the `*_migration.sql` file, and run `python -m scripts.audit_grants` after any
  migration.
- A load's e-mail goes once for each data set. `load status` keeps small offset marks in the run's folder, so follow a
  load from one session only; `python -m scripts.ops status` is read-only.
