# Ops status: where the work stands (continuation record)

Last verified 15-Oct-2026 by a read-only audit. The master plan, with the backlog, the order of work and the acceptance
criteria, is in the owner's plans folder (its path is in Claude's project memory), not in this repository.

## Live

- Ops skills, phase 1 and its upgrade: `/yq-load` (check, then the Windows box, then the writer, then the e-mail) and
  `/yq-brief` (with the team message). The engine is `python -m scripts.ops`. The one program that writes to the database
  is `scripts/yq_load_go.py`; the one that sends the e-mail is `scripts/yq_send.py`. Tests: `tests/test_ops_*.py` (10
  suites, all run in CI). Reference: `docs/OPS_SKILLS.md`; backups and the restore drill: `docs/BACKUPS.md`.
- Market Intel v2 (migrations r8m and r8n). Reference: `docs/MARKET_INTEL.md` and the pages beside it
  (`docs/MARKET_INTEL_HOWTO.md`, `docs/MARKET_INTEL_RELEASE.md`).
- Real loads so far: the drops 111026, 141026 and 151026. Each load's journal is in `business_data/ops/loads/`
  (git-ignored).

## In progress

- H, the phase 1 hardening pack (15-Oct-2026): no second full e-mail for the same data; "nothing new" ends the check
  without a GO line; the brief's pointers name only commands that are built; counter and wording fixes; the seconds each
  report takes, shown in the check; `pack` for plain backups; the documents.

## Not built (the engine prints "not built yet")

`watch`, `moves`, `stock`, `report` (the week and month workbooks), `recon` and `kpi`; `check` (a drafted text against the
computed figures) is registered and not built either. The specification is the approved ops-skills plan of 8-Oct-2026
(the sections from /yq-watch to /yq-kpi, and "Build order"). Phase 2 groundwork exists: `scripts/ops/ledger.py` (the stock
ledger as whole vouchers, the narration reduced to a hash) and `scripts/ops/items.py` (the item-name bridge), covered by
`tests/test_ops_moves.py`.

## Order of work (each step needs the owner's go)

H hardening, then MI1 photo archive, P3a `/yq-month` and `/yq-week`, P2 `/yq-stock`, `/yq-moves`, `/yq-watch`, the
phase 2b release (Stock Moves views and engine speed), P3b, P4 reconciliations and `/yq-kpi`, MI3, MI4.

## Open items

- The portal's Stock Moves page: its Pending and van figures are not reliable until the phase 2b release. It pairs issues and receipts by
  voucher number, and van stock comes from a snapshot of the main store only. Use `/yq-moves` once it is built.
- Profitability is held back at every load until Focus posts the cost of sales again.
- Category rows for the WEKOME codes: an owner decision at the start of P3a.

## Owner decisions (15-Oct-2026)

- The KPI step stays as configured. The order after H is the monthly report, then stock, then moves.
- SIM transfers are left out of the storekeeper check (`/yq-moves` is accessories only).

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
