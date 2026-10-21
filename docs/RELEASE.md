# RELEASE.md — the release train and the rollback runbook

The marketplace is live and takes real orders. Every release, whatever its size, runs this protocol
(plan of 24-Sep-2026, §9 "Release protocol"). The tools it names are all in `scripts/`; none of them
touches a Render environment variable, and none of them writes to production outside the step that
says so.

Three deployables, three tools:

| Piece | Where it runs | Deploy | Roll back |
|---|---|---|---|
| API (`app/`) | Render, service `yq-ops-assistant` (Docker, free plan). **Render does not deploy on push** — every deploy is an API call. | `python -m scripts.render_deploy deploy --commit <sha> --wait` | `python -m scripts.render_deploy rollback --deploy <dep-id> --wait` (or the dashboard's Rollback) |
| Marketplace (`web/dist-market`) | Cloudflare Worker `yq-marketplace` (static assets + `workers/market.js`) at yqmarketplace.com | `wrangler versions upload` → `wrangler versions deploy <id>@100%` | `wrangler versions deploy <previous>@100%` |
| Portal (`web/dist`) | Cloudflare Worker `yq-bahrain-ops` at ops.yqmarketplace.com | same | same |
| Database | Supabase Postgres | `scripts/apply_sql <file> --rehearse`, then apply | `<file>_reverse.sql`, or a guarded `db_backup --restore` |

**CI, and a private repository (C1).** Render deploys only through `render_deploy` (its Auto-Deploy is No: a push never
deploys the API). The release scripts read CI through the GitHub API: without a key while the repository is public;
while it is private, with `GH_READ_TOKEN` (the main folder's `.env`, or the PowerShell window's environment), and
without that token they ask you to confirm CI by hand on github.com.

**A push to `main` that touches `web/**` deploys the web at push time.** `.github/workflows/cf-deploy.yml`
builds both bundles and runs `wrangler deploy` for both Workers (`workers/market.js` included) the
moment the merge lands — before any step below, and before the API. Three ways to keep the train in order:

- deploy the API first, from the release branch: `render_deploy deploy --commit <sha>` takes any
  commit, so steps 1–3 can run before the merge, and the merge then deploys the web onto the new API; or
- push the release in two commits: the release commit without any `web/**` change first (the API is
  deployed from it), then the web commit once the API is live. Release 2b does it this way (below), so
  its web goes live after the API; or
- merge, and accept the window (minutes) in which the new web talks to the old API. R6 is tolerant of
  it: the Worker computes the same `W/"sha1[:24]"` ETag against an API without one, and a missing 1024
  rendition falls back to 512.

Whichever way, the version bookkeeping in step 4 starts AFTER the push: `versions list` shows the cf-deploy
upload as the newest version, and "the version live before this train" is the one before it. The
versioned path below is still what a release train uses for the switch itself, so it is explicit and
reversible; `wrangler deploy` leaves no version to roll back to but the previous upload (see "Rollback").

---

## Release protocol (every train)

Work from the repo root with the release commit checked out on `main`. `.env` holds `DATABASE_URL`
(Supabase session pooler), `RENDER_API_KEY`, `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN_WRITE`.
Nothing below prints a secret.

### 1. Backup and gate

```
python -m scripts.yq_backup release <rN>                  # every public table: verified, encrypted, read back
python -m scripts.prod_gate snapshot --label before-<rN>   # business_data/gates/<ts>_before-<rN>.json
```

Release backups are encrypted by themselves (R1, 21-Oct-2026). `release` backs up every table (the same
`db_backup --all` REPEATABLE READ snapshot) into `%LOCALAPPDATA%\yq-backups\release\<date>_pre-<rN>` (never under
`business_data/`, never in OneDrive), checks it, encrypts it into `<date>_pre-<rN>.7z` (the backup password, 7-Zip
AES-256), reads it back, deletes the plain folder and prints the archive's path as its last line (exit 0 only then).
Keep that path: the rollback needs it. Exit 1: it did not finish, and its last line says what plain copy is left.
Exit 2: refused, nothing made (no backup password or 7-Zip, a bad label, or that day's backup of the label already
there: run it again with another label, such as `<rN>-2`). A plain `db_backup --all` folder under `business_data/backups/`
would sit in the daily OneDrive copy with merchant phones and trade prices: not for releases any more. `prod_gate`
fingerprints every pre-existing order, line, event, merchant, salesman and target; the compare in step 5 is against
this file.

### 2. Migrations (only when the release has any)

For each `scripts/*_migration.sql` in the release, in the order the release notes list them:

```
python -m scripts.apply_sql scripts/<name>_migration.sql --rehearse   # runs on prod, ALWAYS rolls back
python -m scripts.apply_sql scripts/<name>_migration.sql              # apply (lock_timeout 2 s, statement_timeout 60 s)
python -m scripts.audit_grants                                        # must exit 0: nothing exposed to anon/authenticated
```

The rehearsal proves the file against the real schema and the real data (a CHECK that an existing
row would violate fails HERE, not half-way through the apply). A file with its own `COMMIT` is refused
by `--rehearse`; do not add one. Every migration has a `<name>_reverse.sql` beside it.

R6 ships one: `scripts/shop_events_error_migration.sql` (the shop_events CHECK gains `'error'`). It
must be applied **before** the web deploy — until it is, the new ErrorBoundaries' reports fail quietly
exactly as they did before, so the order is a matter of not losing the first day's errors, not of
safety.

### 3. API

R6 image step first, **before** the deploy that carries `THUMB_SIZES = (160, 320, 512, 1024)`: the
payload then lists a 1024 rendition for every product photo, so build them first (idempotent, storage
only, additive):

```
python -m scripts.make_market_thumbs --only-missing
```

(The web falls back to the 512 rendition and then the original if a 1024 file is missing, so a
forgotten backfill costs one 404 per zoom, not a broken photo. Package renditions already exist.)

Then the deploy:

```
python -m scripts.render_deploy status                       # the last 5 deploys: id, status, commit — note the live one (for the rollback)
python -m scripts.render_deploy deploy --commit <sha> --wait # exactly this commit; waits for "live"
```

Then the GET smoke, in this order (a cold container takes ~50 s on the first call; the keep-warm
Worker normally has it awake). A GET with a header dump, never `curl -I`: FastAPI does not add HEAD to
a GET route, so `-I` is a 405 from the API (the Worker on yqmarketplace.com does answer HEAD), and only
a request that sends `Accept-Encoding` sees `Content-Encoding: gzip`:

```
curl -s https://api.yqmarketplace.com/health
    → {"status":"ok", ..., "commit":"<sha12>"}   the SHA must be the one just deployed (R6: /health carries RENDER_GIT_COMMIT)
curl -s -o /dev/null -D - -H 'Accept-Encoding: gzip' https://api.yqmarketplace.com/public/market
    → 200, ETag: W/"…", Cache-Control: public, max-age=60, stale-while-revalidate=600, stale-if-error=86400, Content-Encoding: gzip, Vary: Accept-Encoding
curl -s -o /dev/null -D - -H 'Accept-Encoding: gzip' -H 'If-None-Match: <that etag>' https://api.yqmarketplace.com/public/market
    → 304, no body
curl -s 'https://api.yqmarketplace.com/public/market?ref=<slug1>' | head -c 300     two real rep slugs: "ref" and "rep" filled
curl -s 'https://api.yqmarketplace.com/public/market?ref=<slug2>' | head -c 300
curl -s https://api.yqmarketplace.com/public/rep/<slug1>
curl -s https://api.yqmarketplace.com/public/shop/order/<a known tracking token>
```

plus one quote POST, which prices a cart and writes nothing:

```
curl -s -X POST https://api.yqmarketplace.com/public/market/quote -H 'content-type: application/json' \
     -d '{"lines":[{"item_code":"X01","qty":2}]}'
```

### 4. Web

Build with the release commit baked in (the same env `scripts/deploy_cf.py` uses), then upload a
version and switch traffic to it — market first, then portal:

```
cd web
BUILD_ID=<sha> VITE_APP=market VITE_API_URL=https://api.yqmarketplace.com npm run build:market
npx --yes wrangler@4 versions upload -c wrangler.market.jsonc          # prints the new version id
npx --yes wrangler@4 versions deploy <new-version-id>@100% -c wrangler.market.jsonc -y

BUILD_ID=<sha> VITE_API_URL=https://api.yqmarketplace.com VITE_SUPABASE_URL=… VITE_SUPABASE_ANON_KEY=… npm run build
npx --yes wrangler@4 versions upload -c wrangler.portal.jsonc
npx --yes wrangler@4 versions deploy <new-version-id>@100% -c wrangler.portal.jsonc -y
```

`npx wrangler@4 versions list -c wrangler.market.jsonc` shows the ids; write down the id that was live
before the switch. (`python -m scripts.deploy_cf market|portal` is the one-shot equivalent of build +
`wrangler deploy`; it is fine for a hotfix, but leaves the version bookkeeping to `versions list`.)

Smoke on the live hostname. The split between the asset layer and the Worker (no `run_worker_first`:
a file is the asset layer's, a navigation to an unknown path is the asset layer's index.html, and any
other miss — the `/api/market` fetch, a script/font fetch of a missing file — is the Worker's) is
Cloudflare's shared router; it was read in the source and verified in `wrangler dev` on 24-Sep-2026,
and these lines on the live hostname are the proof on the real edge (a request without
`Sec-Fetch-Mode`, such as curl's, counts as a non-navigation):

```
curl -s https://yqmarketplace.com/version.json                                        → the build id you just deployed
curl -s -o /dev/null -D - https://yqmarketplace.com/api/market                        → 200 application/json, X-Yq-Cache: MISS|HIT|STALE, ETag   (R6 edge catalog)
curl -s -o /dev/null -D - -H 'Sec-Fetch-Mode: cors' https://yqmarketplace.com/assets/does-not-exist.js
                                                                                      → 404, Cache-Control: no-store        (a chunk fetch: the Worker, never index.html)
curl -s -o /dev/null -D - -H 'Sec-Fetch-Mode: navigate' https://yqmarketplace.com/some-deep-route
                                                                                      → 200 text/html, Cache-Control: public, max-age=0, must-revalidate   (a navigation: the asset layer's index.html, never immutable)
curl -s -o /dev/null -D - https://yqmarketplace.com/assets/<a chunk named in index.html>
                                                                                      → 200, Cache-Control: … immutable    (a file: the asset layer)
```

Then, in the Cloudflare dashboard, Workers & Pages → yq-marketplace → Metrics: the request count must
move by about one per page load, not ~15 — the files and the navigations are not invocations (§7).

Then the QA harness (`scripts/qa/market_qa.py`) against production — it reads the live catalog, mocks
the order POST, the receipt, the phone lookup and the event ping, and never places an order:

```
python scripts/qa/market_qa.py --base https://yqmarketplace.com --api https://api.yqmarketplace.com --out business_data/qa/<rN>
```

### 5. Verify

```
python -m scripts.prod_gate snapshot --label after-<rN>
python -m scripts.prod_gate compare business_data/gates/<ts>_before-<rN>.json business_data/gates/<ts>_after-<rN>.json
```

The compare exits non-zero on any change to a pre-existing order, line, attribution or total that no
audited event explains. Every delta it lists must be explained: new real orders and events, a status
moving forward through real business, the columns the migration added. Nothing else.

### 6. Stop rule and the watch

**Any unexplained change to a pre-existing order, line, attribution or total means stop and roll
back** (below), then investigate on the backup, never on prod.

For 24 h after the train, watch:

- notify results (Shop Orders: the "not notified" badge; `shop_jobs` output in the keep-warm logs);
- RUM: the Shop Analytics card shows LCP / INP / CLS p75 and the visit count; since R6 the same
  `/shop/analytics` JSON (`vitals`) also carries `visits` (every beacon, the empty pagehide ones
  included), the `catalog_src` breakdown and the LCP phases (`lcp_ttfb_ms_p75` high = the origin was
  cold, `lcp_load_ms_p75` high = photos, `lcp_render_ms_p75` high = the app), and the same breakdown
  reads directly in SQL:

  ```sql
  select meta->>'catalog_src' as src, count(*) as visits,
         percentile_cont(0.75) within group (order by (meta->>'lcp')::float)        as lcp_p75,
         percentile_cont(0.75) within group (order by (meta->>'lcp_ttfb')::float)   as ttfb_p75,
         percentile_cont(0.75) within group (order by (meta->>'lcp_load')::float)   as load_p75,
         percentile_cont(0.75) within group (order by (meta->>'lcp_render')::float) as render_p75
  from shop_events where event = 'vitals' and ts > now() - interval '1 day'
  group by 1 order by 2 desc;
  ```

  `edge-hit` / `edge-stale` / `pre-edge-*` should dominate; a run of `api` / `pre-api` means the
  Worker is not answering; `none` is a visit that ended before a catalog arrived;
- errors: `select ts, meta->>'where', meta->>'build', meta->>'code', meta->>'reason', meta->>'route'
  from shop_events where event = 'error' order by ts desc limit 50` — a class of error that starts
  with the new `build` and did not exist before it is a rollback signal;
- the Worker: `npx wrangler@4 tail -c wrangler.market.jsonc` shows one event per invocation
  (`/api/market`, the odd missing chunk) and the Worker's own `origin timeout|error after N ms` line
  when the origin fails; a stream of those lines means the origin is down and merchants are on the
  last good copy. A run of 404s on `/api/market` with the store open means the API's kill switch is
  off by mistake — the edge forwards a 404 as the origin's answer and drops its copy (§7).

### 7. The Workers Free budget, and what a 404 means at the edge

Workers Free is 100,000 invocations a day, and only requests that reach `workers/market.js` count:
`/api/market` (one per page load), a missing chunk or font after a deploy, a non-navigation fetch of
an unknown path. Files and navigations are the asset layer's — free, unlimited, never an invocation
(there is no `run_worker_first`; the wrangler config and the header of `workers/market.js` have the
routing and why any `run_worker_first` array would defeat the missing-chunk 404). At ~150 visits/day
the Worker uses under 1 % of the budget.

The cliff, written down: past the cap Cloudflare answers every invocation with a 429 until the daily
reset (midnight UTC). `/api/market` then 429s and the app falls back to the API's `/public/market` by
itself (`marketApi.ts`, `catalog-prefetch.js`); a missing chunk 429s instead of 404ing and
`vite:preloadError` still reloads once; the files, the fonts and every navigation stay up. (The first
R6 cut had `/api/*`, `/assets/*` and `/fonts/*` in `run_worker_first` — every chunk an invocation, so
~5k first visits, or one loop over `/assets/x.js`, would have turned every chunk into a 429 until
midnight.)

One-time, in the Cloudflare dashboard (nothing in the repo can set these): Notifications → add the
Workers usage alert for the account ("Usage approaching limit" / Free plan), so a broadcast day is seen
coming; and, optional (one rule is free), Security → WAF → Rate limiting rules:
`yqmarketplace.com/api/market`, 60 requests per minute per IP — the API's own limit, applied before an
invocation is spent. Workers Paid (USD 5/month, 10 M requests) removes the cliff altogether; that is a
decision for the owner, not a step in this protocol.

The kill switch through the edge: `shop_market_enabled=0` makes the API answer 404, and the Worker
treats a 404/410 as the origin's answer, not as an outage — the cached copy is deleted (in the
background refresh too) and the 404 forwarded, so the store closes at the edge within `max-age` (60 s)
of the switch. The last good copy covers a 5xx, a 429 and a timeout only.

---

## Rollback

Each piece rolls back on its own; the order is the reverse of the deploy only when a change spans
pieces (a web change that needs a new API field: roll the web back first).

### Web (market or portal)

```
cd web
npx --yes wrangler@4 versions list -c wrangler.market.jsonc              # find the previous version id
npx --yes wrangler@4 versions deploy <previous-version-id>@100% -c wrangler.market.jsonc -y
```

or, for the last deploy only, `npx --yes wrangler@4 rollback -c wrangler.market.jsonc`. Same with
`wrangler.portal.jsonc`. Since R6 a tab that still holds the newer index.html gets a real 404 for a
chunk that no longer exists and reloads itself once (`vite:preloadError`), so the switch cannot strand
a merchant on a broken page; the service worker sees the older `build` in `/version.json` and offers
"New version ready" as usual.

### API

```
python -m scripts.render_deploy status                            # the deploy ids; the previous "live" one is the target
python -m scripts.render_deploy rollback --deploy <dep-id> --wait
```

or Render dashboard → the service → Deploys → Rollback on the previous live deploy. Verify with
`/health` (`commit` = the SHA you rolled back to). `render_deploy.py` never reads or writes the env
set; if a release also needed a new env var, remove it in the dashboard, one key at a time.

### Database

1. **Migrations:** run the release's `scripts/<name>_reverse.sql` files in reverse order, through
   `apply_sql` (rehearse first). Data corrections in this codebase are flags (`is_test`, `voided`,
   `status`), so they reverse by UPDATE, never by deleting rows.
2. **A table restore** is the last resort and goes only through the guarded restore. A release backup is an
   encrypted archive (step 1). Unpack first: `python -m scripts.yq_backup unpack <archive>` (or `--to <folder>`, a
   new or empty folder outside OneDrive and the project folder) prints the checked folder as its last line; run
   `--verify` and `--restore` on that folder, then delete it (it holds the backup unencrypted):

   ```
   python -m scripts.yq_backup unpack <archive> [--to <folder>]             # last line: the checked folder
   python -m scripts.db_backup --verify <folder>                            # re-parse the CSVs against the manifest
   python -m scripts.db_backup --restore <folder> --tables <t1,t2,…>        # DRY RUN: prints the plan
   python -m scripts.db_backup --restore <folder> --tables <t1,t2,…> --yes  # one transaction
   ```

   The guards refuse a restore that would cascade into a table outside `--tables`, that would
   overwrite live rows newer than the backup (`--allow-newer` is a deliberate, written-down decision,
   never a default), or whose CSV header no longer matches the table. **Never restore over newer live
   orders.** Restoring `shop_orders` means restoring its lines and events with it (the FK-closure guard
   says so).
3. `prod_gate compare` again after any database rollback; the "after" must equal the "before" for every
   pre-existing record.

### What never changes in a rollback

- Checkout and the `/:slug` rep links keep their URLs; `/c/:token` stays a redirect to the marketplace.
- No env var is edited by a script. No table is dropped.
- A rollback is itself a train: it gets a gate snapshot and a compare.

---

## Release 2b (r9): the release script, part by part

*For the owner. What goes live: the portal's Stock Movement page pairs the transfers by their contents (the same code
as `/yq-moves`) and takes each van's stock from the ledger; ID numbers in the stored stock narrations are masked; the
four load fixes; the team message with its Copy button in the Command Centre. The script is `scripts\r9_release.ps1`.
Claude prepared it; you run it, one part at a time, in PowerShell in the project folder, on `main`, after
`git pull --ff-only`. It refuses to run from a code worktree (`.claude\worktrees\...`), which would reach production
through the project folder's `.env`.*

**When.** After a morning load, at a quiet hour, never during a Focus load. The release changes engine files, so from
the push until `-Part api` has finished `/yq-load` refuses: go from `-Part backup` to `-Part api` in one sitting.

**The web goes live after the API: two pushes.** Claude pushes the release commit (the database files, the API, the
engine; no `web/**` change) and tells you; you run check, backup, migrate and api. Then Claude pushes the web commit;
you run `git pull --ff-only`, then web, verify and migrate2. From the web part on, the script accepts that later commit
as long as nothing the API is built from (`app\`, the code under `scripts\`, the requirements, the `Dockerfile`)
changed since the release commit; otherwise it stops and says so.

```
powershell -ExecutionPolicy Bypass -File scripts\r9_release.ps1 -Part check
powershell -ExecutionPolicy Bypass -File scripts\r9_release.ps1 -Part backup
powershell -ExecutionPolicy Bypass -File scripts\r9_release.ps1 -Part migrate
powershell -ExecutionPolicy Bypass -File scripts\r9_release.ps1 -Part api
powershell -ExecutionPolicy Bypass -File scripts\r9_release.ps1 -Part web
powershell -ExecutionPolicy Bypass -File scripts\r9_release.ps1 -Part verify
powershell -ExecutionPolicy Bypass -File scripts\r9_release.ps1 -Part migrate2
```

| Part | What it does | Time | Writes to production | Your YES |
|---|---|---|---|---|
| check | folder, branch, the release files, the key names, GitHub ci, what is live | 1 min | no | none |
| backup | `python -m scripts.yq_backup release r9`, then the before-snapshot (gate) | 5-10 min | no (reads) | 1 |
| migrate | `r9_stock_moves`, then `r9_team_messages`: rehearse, apply, grant audit | about 3 min | yes | 4 |
| api | the Render deploy of the release commit; `/health` must show it | 5-10 min | yes | 1 |
| web | the cf-deploy run of the web commit is green and the live builds carry it | 1-15 min | no (GitHub did it) | none |
| verify | `moves portal` must print PASS; the after-snapshot compared with the backup's | about 3 min | no | none |
| migrate2 | `r9_transfer_flow_drop`: rehearse, apply, grant audit | about 2 min | yes | 2 |

Before every step that writes to production (or runs on it) the script shows `PRODUCTION STEP: ...` and asks
**Type YES to continue**; anything else stops the part before that step. Exit codes: 0 done, 1 failed (stop and tell
Claude), 2 stopped (no YES, or something to do first; the message says what). The screen also goes to
`business_data\releases\<date>_r9.log`, secrets masked; `business_data\releases\r9_state.json` keeps the backup
archive, the before-snapshot, the API deploy to roll back to and how far the train got.

- **check** — branch `main`, level with GitHub, no changed tracked file, no load lock, `.env` (the key names, never the
  values; `GH_READ_TOKEN` once the repository is private), the release files, `ci` green for this commit, what is live.
- **backup** — release backups are now encrypted by `yq_backup release` (step 1 above): every table goes to
  `%LOCALAPPDATA%\yq-backups\release\<date>_pre-r9`, is checked against its manifest, encrypted with the backup
  password and read back, and the plain copy is deleted: never under `business_data`, never in OneDrive. Its last line,
  the archive, goes into the state. The first attempt is labelled `r9`, a later one `r9-2`, `r9-3` ... (yq_backup
  refuses a second backup of one label on one day). Exit 1: it did not finish, its last line says what plain copy is
  left: tell Claude. Exit 2: refused, nothing made: if it says the backup is already there, run the part again,
  otherwise tell Claude; nothing of the release starts without the backup. When the part runs again, the first backup
  of the release stays the rollback point.
- **migrate** — `r9_stock_moves`: the view `v_transfer_lines`, `v_salesman_stock_recon` rebuilt from the ledger (its
  old columns kept) and the ID numbers in the stored stock narrations masked. **The narration mask cannot be undone**:
  the encrypted backup keeps the old text. The mask covers the text only: `row_hash` beside it is a hash of the raw
  row, the unmasked narration included, from which a masked number can be worked back by whoever reads those rows (the
  service role, a `db_backup` copy). No view shows `row_hash` (the AI and the portal never see it): treat it as
  sensitive; the real fix (a rehash and a reload) is a later release. Then `r9_team_messages`: the table behind the Copy button (no login reads it
  directly; the API shows it to the admin and management logins). Each file is rehearsed (it runs and is rolled back),
  applied after a second YES, then `audit_grants` must exit 0.
- **api** — writes down the deploy that is live (the rollback target), deploys the release commit to Render and waits
  until `/health` reports it. Then tell Claude: Claude pushes the web commit.
- **web** — reads only. The cf-deploy run of the web commit (or the last one, when nothing under `web/` changed since)
  must be green with both deploy steps run, its build must be of the release commit or a later one, and the live
  marketplace and portal must carry that build id. When the portal's build id cannot be found, it asks you to look at
  ops.yqmarketplace.com/stock: the amber "Being corrected" notice must be gone.
- **verify** — reads only. `/health` on the release; `python -m scripts.ops moves portal` must print PASS: it reads
  the view `v_transfer_lines` (read-only), pairs it with the API's own code and compares every voucher (its outcome,
  its receipt or receipts) and every count with `/yq-moves`' own pairing, for the last 7 days and the whole ledger. A
  FAIL names the first twenty vouchers that differ: do not run migrate2, tell Claude. Then the gate snapshot after the
  release, compared with the one taken at the backup (an unexplained change to an order, line or total is the stop
  rule, step 6). Then three checks of your own in the portal, listed on the screen.
- **migrate2** — only once verify passed for the API that is live, and only while `/health` still shows the release
  (an older API reads `v_transfer_flow`): drops the old view.

### If something is wrong: the 2b rollback

```
powershell -ExecutionPolicy Bypass -File scripts\r9_release.ps1 -Part rollback
powershell -ExecutionPolicy Bypass -File scripts\r9_release.ps1 -Part rollback -RestoreTables <t1,t2>
```

Each piece asks YES; anything else skips that piece. The order follows who reads what:

1. **R1** `r9_transfer_flow_drop_reverse` puts `v_transfer_flow` back, before the API goes back (an older API reads it).
2. **R2** the API back to the deploy `-Part api` wrote down (`render_deploy rollback --deploy <id> --wait`); `/health`
   must then show the earlier commit.
3. **R3** `r9_team_messages_reverse`, then `r9_stock_moves_reverse` (the old `v_salesman_stock_recon` back,
   `v_transfer_lines` dropped), only when Claude says so and only once the API is back: while `/health` still shows the
   release, or gives no answer at all, the script holds them (run the part again once `/health` answers). The
   narration mask cannot be undone: the stored narrations stay masked.
4. **R4** a table restore, the last resort, only with `-RestoreTables` exactly as Claude gives the list:
   `yq_backup unpack <archive> --to <scratch>` (a new folder next to the archive, outside OneDrive; its last line is
   the checked folder), then on that folder `db_backup --verify` and the restore's dry run, then YES and `--yes`. The
   guarded restore refuses over live rows newer than the backup. The backup predates the mask: restoring
   `stock_movements` would bring the unmasked narrations back. Claude deletes the unpacked folder afterwards (it holds
   the backup unencrypted).
5. **R5** a gate snapshot after the rollback, compared with the one taken at the backup.

The web stays: the new Stock Movement page and the Command Centre work with an older API too. Should the new portal
itself misbehave, switch it back as "Rollback → Web" above says.

After an API rollback `/yq-load` refuses ("a portal release is merged but not live"): `main` still carries the 2b
engine files while the API is the older one, so loads wait until Claude reverts `main` or the API is deployed again.

### After the release

- `/yq-load` as usual; after it the Command Centre shows that load's team message, with its Copy button.
- `/yq-moves` and the portal's Stock Movement page show the same pairs for the same window.
- Tell Claude the release is done: `docs/STATUS.md` and the notes are updated, and the unpacked folder of a restore, if
  one was made, is deleted.

---

## Reference: what R6 added to the checks

- `/health` → `commit` (RENDER_GIT_COMMIT): the smoke can prove which build answers.
- `/public/market` → weak `ETag`, `If-None-Match` → 304, pre-gzipped body; `Vary: Accept-Encoding`.
- `https://yqmarketplace.com/api/market` → the edge copy (`X-Yq-Cache`, `Age`); `catalog-prefetch.js`
  and `marketApi.ts` read it first and fall back to the API by themselves — nothing to configure. An
  unknown `?ref=` slug is folded onto the no-ref copy at the edge (no key, no origin call); a 404 from
  the API is authoritative (copy dropped, 404 forwarded).
- Missing `/assets/*` and `/fonts/*` → 404 `no-store` from the Worker, which sees only non-navigation
  misses; there is no `run_worker_first` (§7).
- `shop_events.event = 'error'`: client error telemetry from both apps, with `build`.
- `THUMB_SIZES` gained 1024 → `make_market_thumbs --only-missing` before the API deploy.
- `docs/DEPLOY_RENDER.md` describes the one-time Render setup; this file is the recurring protocol.
