# Ops status: where the work stands (continuation record)

Last verified 18-Oct-2026. The master plan, with the backlog, the order of work and the acceptance criteria, is in the
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
  - The cause that morning: both stock reports of Focus came without the SIM starter packs, which
    the Sales day book still had. That points to an export filter, or to a change to the items or to
    the user's rights in Focus. Drop 181026 was loaded without its ledger.
- `/yq-month`: the monthly sales workbook in the layout of the September report (`python -m scripts.ops report month
  [YYYY-MM]`, whole months only, written as a DRAFT under `exports/reports/monthly/`). Its golden test rebuilds September
  against the reference file (local only).
- `/yq-stock`: stock headline, order list, fast-mover availability and ageing for the main store
  (`python -m scripts.ops stock [reorder|ageing|<code>]`); values at the landed cost from goods-received notes, the
  pricing-sheet cost where an item has none.
- The photo archive: `python -m scripts.yq_photos archive [--apply]` and `verify <archive>` (see `docs/BACKUPS.md`).
- The daily database backup: `python -m scripts.yq_nightly`, started by the Task Scheduler task `YQ Daily Backup`
  (Sunday to Thursday, while the owner is logged on; see `docs/BACKUPS.md`).
- Market Intel v2 (migrations r8m and r8n). Reference: `docs/MARKET_INTEL.md` and the pages beside it
  (`docs/MARKET_INTEL_HOWTO.md`, `docs/MARKET_INTEL_RELEASE.md`).
- Real loads so far: the drops 111026, 141026, 151026 and 181026 (without its Stock ledger). Each load's journal is in `business_data/ops/loads/`
  (git-ignored).

## In progress

- Nothing; the next step waits for the owner's go.

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
- `/yq-stock` questions for the owner: which lead time wins (measured or the configured default), what counts as goods on
  order, which items are display stands, and the floor on the daily rate for items out of stock most of the period.

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
