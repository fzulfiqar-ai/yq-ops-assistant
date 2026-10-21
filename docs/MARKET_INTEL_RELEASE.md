# Market Intel v2: the release, step by step

*For the owner. Releases R8m (the quality track) and R8n (the weekly AI review reads Market Intel). The script is
`scripts\mi_v2_release.ps1`; this page says what each part does, how long it takes, what can go wrong and what to do.
Claude prepared it; you run it. The office guide is `docs/MARKET_INTEL.md`, the reps' one-page guide
`docs/MARKET_INTEL_HOWTO.md`, the general release rules `docs/RELEASE.md`.*

## What goes live

- **Spotted v2**, for reps and office staff: six tiles (Quality problem, Packaging, Customer asked for, Competitor,
  New product/trend, Other), the product picker, issue chips, a quantity, "Customer said / I saw it", a label photo,
  a safety chip; English and Arabic.
- **Cases** for quality and packaging reports (timeline, verification, supplier actions), **Lists** (suppliers, brands,
  issue types; admin only), **Supplier reports** (the monthly workbook: draft, approve, download) and **My reports**
  for each rep.
- The old **Product Finds and Field Notes** copied into Market Intel, each photo with a small thumbnail. A copy: the
  old boards are not changed.
- The **weekly AI review** reads the Market Intel sightings (until now it read the old boards).

In the database: new tables and columns (r8m) and the AI review's market view rebuilt (r8n). Both only add; the code
running today ignores them. Every step that writes asks you first.

## Before you start

1. **Claude merges the release into `main`, pushes it and tells you.** GitHub then runs **ci** (5-10 minutes). When ci
   is green, **cf-deploy** puts the new portal and marketplace live by itself a few minutes later. From then until
   `-Part api` has finished, the new Market Intel tabs say "Not available yet" and a Spotted report is saved the old
   way (without its issue type or quantity). So the push and the parts belong at a quiet hour (an evening, never during
   a Focus load), one part straight after the other. The marketplace code does not change in this release; it is only
   rebuilt, so merchants may see "New version ready".
2. The PC stays on, awake and online for about an hour. Python, git and the `.env` are already on it.
3. Open PowerShell in the project folder and bring it up to date:

   ```
   cd "<the project folder>"
   git pull --ff-only
   ```

   If git refuses, stop and tell Claude.

## The order

One part at a time, in this order. Each part stops at the first problem and says what failed; any part can be run
again.

```
powershell -ExecutionPolicy Bypass -File scripts\mi_v2_release.ps1 -Part check
powershell -ExecutionPolicy Bypass -File scripts\mi_v2_release.ps1 -Part backup
powershell -ExecutionPolicy Bypass -File scripts\mi_v2_release.ps1 -Part migrate
powershell -ExecutionPolicy Bypass -File scripts\mi_v2_release.ps1 -Part api
powershell -ExecutionPolicy Bypass -File scripts\mi_v2_release.ps1 -Part web
powershell -ExecutionPolicy Bypass -File scripts\mi_v2_release.ps1 -Part import
powershell -ExecutionPolicy Bypass -File scripts\mi_v2_release.ps1 -Part verify
```

| Part | What it does | Time | Writes to production | Your YES |
|---|---|---|---|---|
| check | folder, branch, GitHub ci, keys, load lock | 1 min | no | none |
| backup | copies every table, takes the before-snapshot | about 5 min | no (reads) | 1 |
| migrate | r8m, then r8n: rehearse, apply, grant audit | about 3 min | yes | 4 |
| api | deploys the new API on Render, waits for /health | 5-10 min | yes | 1 |
| web | proves the new portal and marketplace are live | 1-15 min | no (GitHub did it) | none |
| import | copies the old finds, then the thumbnails | 15-25 min | yes | up to 4 |
| verify | after-snapshot and compare, API and web, your 5 checks | 2 min + 10 min | no | none |

Before every step that writes to production (or runs on it) the script shows `PRODUCTION STEP: ...` and asks
**Type YES to continue**. Anything else stops the part before that step: nothing of it runs. Exit codes: 0 done,
1 failed (stop and tell Claude), 2 stopped (no YES, or something to do first; the message says what).

Everything on the screen also goes to `business_data\releases\<date>_mi_v2.log`, with passwords, keys and tokens
masked: no secret is ever printed. `business_data\releases\mi_v2_state.json` keeps what the later parts need: the
backup folder, the before-snapshot and the API deploy to roll back to.

## Part by part

### check (1 minute, reads only)

It refuses to run from a code worktree (`.claude\worktrees\...`), which would reach production through the project
folder's `.env`. Then: the folder is on `main` and level with GitHub; no tracked file is changed (your own untracked
files are fine); the release files are there; `.env` holds the keys the tools need (it shows the names, never the
values); no Focus load is running (`business_data\ops\LOCK` is absent); Python works; GitHub's **ci** is green for
this commit. Last, what is live now: the Render deploys, the API's commit, the marketplace build.

What can go wrong:
- "not the main project folder": you are in a worktree or a copy. Go to the project folder.
- "not at origin/main": run `git pull --ff-only`, then check again.
- "ci is still running": wait five minutes, run check again. "ci FAILED": do not release; tell Claude.
- "LOCK exists": a Focus load is running or was cut off. In Claude Code type `/yq-load` to settle it first. Never
  delete the lock by hand.
- A key missing from `.env`: tell Claude its name (never the value).

### backup (about 5 minutes, reads production, 1 YES)

Runs `python -m scripts.yq_backup release mi-v2`: every table is copied, checked against its manifest and sealed
encrypted into `%LOCALAPPDATA%\yq-backups\release\<date>_pre-mi-v2.7z` (never under `business_data`, never in OneDrive;
a second run is named `mi-v2-2`, and the first archive stays the rollback point). The script keeps the archive's name as
`backup_archive` and takes the gate snapshot `pre-mi-v2`: every order, line, customer, salesman and target, fingerprinted
for the compare at the end.

What can go wrong: no internet, or the Supabase project paused (its dashboard says so; Resume it, the data is safe);
run backup again. "FK orphans": tell Claude.

### migrate (about 3 minutes, 4 YES)

First **r8m** (the quality tables and columns), then **r8n** (the AI review's market view). For each: a rehearsal on
production that is always rolled back, then the real apply, then the grant audit (nothing may be open to the public
key). Each apply is one transaction: all of it lands, or none of it. r8m goes first as the migration notes list it; r8n
does not need r8m, so this order is safe either way.

What can go wrong:
- A rehearsal fails: nothing changed. Send Claude the log.
- An apply fails on a lock (it waits at most 2 seconds for a busy table): nothing of that file was applied. Wait a few
  minutes and run migrate again. A file already applied is applied again harmlessly (it asks YES again).
- audit_grants fails: something is open to the public key. Stop and tell Claude before going on.

### api (5-10 minutes, 1 YES)

Writes down the Render deploy that is live now (the rollback target), deploys the release commit (Render does not
deploy on a push), then waits until `https://api.yqmarketplace.com/health` reports the new commit. The old API keeps
answering until the new one is live.

What can go wrong: the Render build fails: the old API is still live and works; tell Claude. /health still shows the
old commit after 8 minutes: look at the Render dashboard, then tell Claude.

### web (1-15 minutes, reads only)

The new portal and marketplace went live with the push (cf-deploy, after ci). This part proves it: the cf-deploy run of
this commit is green and it really deployed both sites (a run without its Cloudflare keys ends green having deployed
nothing), `yqmarketplace.com/version.json` names this build, and the portal's files carry it. If GitHub cannot be
read, it asks you to look and type YES.

What can go wrong: cf-deploy red, or not run: the previous web stays live and works with the new API. On GitHub:
Actions, cf-deploy, Run workflow (branch main); when it is green, run web again. The portal build is not found: open
`ops.yqmarketplace.com/market-intel` in a private window; if the Cases and Supplier reports tabs are there, type YES.

### import (15-25 minutes, up to 4 YES)

Copies the old Product Finds and Field Notes into Market Intel:

1. A dry run: it reads everything (it opens every photo, so this takes a few minutes) and says how many sightings it
   would create. It stops if r8m is not applied yet.
2. YES: a trial with the 5 oldest.
3. YES: everything left, each photo processed like a live capture, with its thumbnail.
4. YES: the same import once more. It must create 0: that proves a re-run is harmless.
5. The thumbnails for photos taken before this release: a dry run, then YES to make the missing thumbnails (and the
   blurred copies of photos flagged with people).

A copy: `product_finds` and `field_notes` are never changed. Run a second time, the part finds nothing left to import
and goes straight to the thumbnails.

What can go wrong: it stops part way: run import again; it resumes where it stopped and never makes a duplicate.
Photos that cannot be imported are listed and their sighting comes in without them: not an error. The second run
creates something: stop and tell Claude. Some thumbnails cannot be made: they are listed, and the lists show the full
photo instead; tell Claude.

### verify (2 minutes, then your 10 minutes)

The gate snapshot `post-mi-v2`, compared with `pre-mi-v2`: it must say PASS (no pre-existing order, line or total
changed without an explanation). Then /health must show the release commit. Then your five checks in the portal:

1. **Lists** (Market Intel, Lists, as admin): add your first real supplier, one of its brands and at least one quality
   issue type (English and Arabic label; safety on for heat, smoke, swelling or a shock).
2. **Spotted with a photo**: the camera button on the Market Intel page, or Spotted in the rep app. Take a photo,
   choose Quality problem, the product and the issue, Save. The card says it was saved.
3. **A case**: Market Intel, Cases, New shows that report as a case, with its thumbnail and timeline. A case can never
   be deleted (its timeline is permanent): if it was only a test, set it to Not confirmed with the reason
   "release test", then Closed.
4. **A draft supplier report**: build one for that supplier and this month. It opens as a Draft; leave it so.
5. **Library**: the old finds are there, with small thumbnails.

What can go wrong: the compare says FAIL. That is the stop rule (`docs/RELEASE.md`): tell Claude at once; the rollback
is below.

## If something is wrong: the rollback

```
powershell -ExecutionPolicy Bypass -File scripts\mi_v2_release.ps1 -Part rollback
```

Most problems need only the web and the API rolled back: the database changes only add, and the code from before this
release ignores them. Each piece asks YES; anything else skips that piece and goes on to the next. In this order:

1. **Web** (Cloudflare, a minute): the script lists the portal's versions; type the id of the newest one created
   before the release, then YES. Then the same for the marketplace (it did not change in this release: leave it
   unless it misbehaves). It needs `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN_WRITE` in `.env`, loaded for
   that window only and never shown; without them use the Cloudflare dashboard (the Worker, Deployments).
2. **API** (Render, about 5 minutes): back to the deploy `-Part api` wrote down (Enter accepts it), YES, then /health
   must show the earlier commit.
3. **Database**, only when Claude says so: the reverses in the right order, **r8n first, then r8m**, each rehearsed
   before it is applied, each followed by the grant audit.
   - The r8n reverse puts the AI review's view back as it was. It touches no data. While r8n is in place, re-running
     `r7b_ai_head_migration.sql` fails: reverse r8n first, run R7b AI Head, then apply r8n again.
   - The r8m reverse REFUSES while r8m holds any data: lists, cases, reports and, once the import has run, the capture
     times of the imported finds and the photo thumbnails. That refusal is its protection, and nothing is changed.
     Leave the database as it is (the earlier code ignores it); dropping that data anyway is a decision for Claude
     (`docs/MIGRATIONS.md`, Release R8m).
4. **A restore from the backup**: the last resort, only with Claude, who gives you the table list. The script checks
   the backup, shows the restore plan (a dry run), then asks YES. The restore refuses by itself to overwrite rows newer
   than the backup (orders or sightings since the release).
5. A gate snapshot after the rollback, compared with the one before the release.

Then tell Claude what happened.

## After the release

1. **Give reviewers the page.** Team & access: give the Market Intel page to each office person who reviews reports.
   The role decides the rest: admin, member, operations and sales manager review cases and build supplier reports
   (only admin edits the Lists); management reads, approves and downloads supplier reports; reps capture and see My
   reports. The reps already hold the page, and new invites get it.
2. **Fill the Lists** (Market Intel, Lists): the suppliers, their brands and the issue types reps will tap (kind
   quality or packaging, English and Arabic labels, safety on for heat, smoke, swelling or a shock, and the extra
   fields each needs: quantity, batch, packaging version, label photo). Nothing is filled in for you (the repository
   is public), and until an issue type exists a quality report has no issue chips.
3. **Tell the reps.** Send the one-page guide `docs\MARKET_INTEL_HOWTO.md` (English and Arabic) to the salesmen's
   group: Spotted has new tiles and a safety chip, and My reports shows what happened to each report.
4. **Safety alerts.** A safety report e-mails `ALERT_EMAIL_TO` (set on Render), or the owner logins when it is not
   set. Look at the Safety tab within one business day of any safety report.
5. **The weekly AI review.** This week, in Claude Code in the project folder, run `/weekly-review`: the Market Intel
   sightings reach it now, cited as `[MI-<id>]`.
6. **The first supplier report**, at the start of next month: build the draft, approve it, download it and send it by
   hand (it is never e-mailed automatically).
7. Keep `business_data\releases\<date>_mi_v2.log` and the archive the script names as `backup_archive`
   (`%LOCALAPPDATA%\yq-backups\release\<date>_pre-mi-v2.7z`, see `docs/BACKUPS.md`); there is nothing to move. The
   script's rollback part unpacks that archive beside itself, then verifies and restores from the folder it prints. The
   14-Oct-2026 release was backed up the old way, as a plain folder: a restore from that one is done with Claude
   (`python -m scripts.db_backup --verify <folder>`, then `--restore`).
