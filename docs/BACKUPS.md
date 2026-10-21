# Backups: the daily backup, the backup before every load and every release, the restore drill, the photo archive

A backup here is a copy of every table of the live database (one CSV file per table and a `manifest.json`), kept on the
office PC in `%LOCALAPPDATA%\yq-backups`. That folder is never inside OneDrive (the daily backup refuses a OneDrive folder;
`YQ_BACKUP_DIR` can point it at another local folder) and nothing in it is ever uploaded.

| Where in `%LOCALAPPDATA%\yq-backups` | What is there | Made by |
|---|---|---|
| the folder itself | `yq-backup_<date>_<time>.7z`, one archive each working day, and `nightly.log`, one line per run | `scripts/nightly_backup.py`, started by the scheduled task `YQ Daily Backup` through `scripts/yq_nightly.py` (below) |
| `preload\` | `yq-backup_<date>_pre-data-<run>.7z`, taken before each load; the newest few are kept (`load.backup_keep` in `business_data/ops/config.json`) | `/yq-load` (`scripts/yq_load_go.py`) |
| `legacy\` | `<name>.7z`, the older backups that sat in `business_data/backups`, packed one at a time | `python -m scripts.yq_backup legacy --apply` |
| `release\` | `<date>_pre-<label>.7z`, the backup taken before a release, such as `2026-10-21_pre-r9.7z`; an older release's backup may still be a plain folder (a CSV per table and `manifest.json`), such as `2026-10-14_pre-mi-v2`, which `pack` encrypts | `python -m scripts.yq_backup release <label>`, a release script's first part (below); the older plain folders were moved here after their release |
| `photos\` | `<date>_<HHMM>_photos.7z` (a run never replaces an archive), an encrypted copy of the Market Intel photos with a manifest of every file (below) | `python -m scripts.yq_photos archive --apply` |

## The backup password

Archives are encrypted with 7-Zip (AES-256, file names hidden too) and the backup password. The load, the restore drill,
the packing commands (`legacy` and `pack`) and the release backup (`release` and `unpack`), all below, look for the
password in this order, and say only where they found it, never the password itself (`scripts/yq_secrets.py`):

1. Windows Credential Manager, the generic credential `YQ_BACKUP_PASSWORD`;
2. the Windows user environment (`HKCU\Environment`), read afresh each time, so a value set later with `setx` works at once;
3. the environment of the running program.

The project's `.env` is never read for it, because it sits in OneDrive. To store the password once, in Credential Manager,
run `python %LOCALAPPDATA%\yq-tools\set_secrets.py password` (an owner-side helper kept outside the repository): it makes a
long password, stores it, shows it once in a Windows box for you to write down, and refuses to run when a password is
already stored. Or type `cmdkey /generic:YQ_BACKUP_PASSWORD /user:yq /pass` in a Command Prompt and enter your own (it shows
nothing as you type). Keep a copy of the password away from this PC: without it no archive can be opened on another PC.

The daily backup (next section) is started by `scripts/yq_nightly.py`, which looks for the password in the same places.
The password goes only to the backup job, in that job's own environment, and from there to 7-Zip; it never goes to a log,
a file or the screen. Nothing needs to be set with `setx`.

## The daily backup (release R7d, audit SEC-19)

`scripts/nightly_backup.py` takes a read-only snapshot of every table, the login list (no password hashes) and a list of
the files in Supabase Storage, packs it into ONE archive encrypted with 7-Zip AES-256 (file names hidden too), deletes the
plain copy, and keeps the newest archive of each of the last 14 days and of each of the last 8 weeks. It writes to
`%LOCALAPPDATA%\yq-backups` — never OneDrive (a OneDrive folder is refused) — and never uploads anything.

It is started by the starter, `python -m scripts.yq_nightly`, run from the project folder:

1. It refuses, with exit code 3, when it is started from a code worktree (`.claude/worktrees`) or with a Python that a
   scheduled task would lose: a virtual environment, or the Microsoft Store Python (its folder changes with each update).
2. It removes the plain work folders (`work_<date>_<time>`) that an earlier run of the backup left in the backup folder
   when it did not finish (a failure, the PC going to sleep, a closed window). A folder written to in the last 15 minutes
   is left in place; nothing named otherwise, and no link, is ever touched.
3. It reads the backup password from Credential Manager (stored once, above) and looks for 7-Zip. Without either it does
   not start the backup, so no unencrypted copy is ever made: it adds a line to the log and ends with exit code 2, which
   Task Scheduler shows as a failed run.
4. It starts `python -X utf8 -m scripts.nightly_backup` in the project folder, with the password in that program's own
   environment only, shows its output as it comes and ends with its exit code. A backup still running after 50 minutes
   is stopped, with the 7-Zip it started (the task's own limit is one hour).
5. It removes the plain work folder of its own run, if the run did not finish.
6. It adds one line to `nightly.log` (below).

`python -m scripts.yq_nightly --dry-run` runs the backup's own dry run instead: where the archive would go, whether it
would be encrypted and what would be pruned, and which leftover work folders would be removed. It writes nothing and logs
no line. (Started on its own without the password or 7-Zip, `scripts/nightly_backup.py` writes a `.zip` with a warning
file, `UNENCRYPTED-BACKUP-WARNING.txt`, next to it and ends with exit code 2; the starter never starts it that way.)

### The scheduled task `YQ Daily Backup`

Sunday to Thursday at 13:05, as the owner and only while he is logged on (Credential Manager belongs to his Windows user;
a locked screen counts as logged on), starting in the project folder. A run missed while the PC was off or asleep starts
as soon as it is back; a run still going after an hour is stopped. While it runs, its window is minimised to the taskbar;
it closes by itself when the backup is done.

To set it up once, in a PowerShell window in the project folder, with 7-Zip installed and the password stored (above):

1. `python -m scripts.yq_nightly --dry-run`: the backup's own dry run; it says `(encrypted: True)`.
2. `python -m scripts.yq_nightly --print-task`: prints ONE command, filled in with this PC's Python and the project
   folder; it creates nothing itself. Paste that line into the same window: it creates the task, or replaces one of the
   same name. It is a `Register-ScheduledTask` command because `schtasks /Create` can set neither the folder a task
   starts in nor the catch-up of a missed run.
3. `schtasks /Run /TN "YQ Daily Backup"`, then, once the run is done, `schtasks /Query /TN "YQ Daily Backup" /V /FO LIST`:
   Last Result 0, Logon Mode `Interactive only`, Start In the project folder.

### The last runs

- `nightly.log` in `%LOCALAPPDATA%\yq-backups` holds one line per run: when it started and ended, the exit code, and the
  archive's name with the place the password came from (the place, never the password), or why nothing was made; and the
  names (only) of any work folders it removed or left in place. The newest lines:
  `Get-Content $env:LOCALAPPDATA\yq-backups\nightly.log -Tail 5` in PowerShell.
- `schtasks /Query /TN "YQ Daily Backup" /V /FO LIST` shows the last run time, its result and the next run time.
- Task Scheduler (`taskschd.msc`), Task Scheduler Library, `YQ Daily Backup`: the History tab lists the runs once
  "Enable All Tasks History" is on (in the Actions pane on the right).

Exit codes: 0 done and encrypted; 1 failed, or stopped after 50 minutes (the log line says why); 2 no encrypted backup was
made (the password or 7-Zip is missing; the log line says which); 3 refused: started from a code worktree, or with a
virtual environment or the Microsoft Store Python.

## The backup before every load

`/yq-load` backs up every table before it loads anything (the `backup` step of the load; `docs/OPS_SKILLS.md`, section 2):

- The archive is `preload\yq-backup_<date>_pre-data-<run>.7z`, encrypted with the backup password when 7-Zip is installed
  and the password is stored.
- Each archive is read back whole (a `.7z` with 7-Zip's own test and the password) before the folder of tables it was made
  from is deleted. An archive that does not read back stops the load at its backup step, before anything is written.
- Once one backup has been encrypted, `business_data/ops/backup.json` records the day (`encrypted_since`). From then on a
  load without the password, or without 7-Zip, stops at its backup step before anything is written and says what to set:
  store the password (above) or install 7-Zip, then type `/yq-load` again. The Windows box shows a line that starts
  `ATTENTION` when the password or 7-Zip is missing.
- A load that is resumed after an interruption keeps the backup taken before it; once a load has begun to write, no new
  backup is taken for it.

## The restore drill

`python -m scripts.yq_backup drill [--archive <file>] [--keep]` proves that a backup can be restored. It works on this PC
only. It:

1. reads back the newest archive in `preload\` (or the one named with `--archive`);
2. unpacks it outside OneDrive and checks every file against its `manifest.json`;
3. starts a throwaway PostgreSQL of its own (the portable one in `%LOCALAPPDATA%\yq-tools\pgsql`) and gives it production's
   schema; production is only read, for its schema;
4. restores every table into it with the guarded restore of `scripts.db_backup`;
5. compares every table's row count with the manifest, twice (the restore does it, and the drill again).

Then it deletes the throwaway database and the unpacked files, because they hold the real data (`--keep` leaves them). The
database address the restore uses must be on this PC; any other is refused before anything runs. A table the throwaway
database cannot hold is listed as not drilled. The result is a row of `business_data/ops/drills.jsonl`: `passed` or `failed`
with the reason, the archive's name, whether it was encrypted, and the name (never the value) of the place the password
came from. Exit code: 0 passed, 1 failed, 2 no archive to drill.

## Packing older backups: `legacy` and `pack`

- `python -m scripts.yq_backup legacy [--apply]` takes the older backup folders under `business_data/backups/` (each
  holds a `manifest.json`). With `--apply` it packs each one into `legacy\<folder name>.7z` with the backup
  password, tests the archive with 7-Zip, compares the file count, and only then deletes the folder; a folder that does not
  pass is kept and named. Without `--apply` it only says what it would do. Afterwards empty the OneDrive recycle bin, which
  keeps deleted files for a while. The run leaves a row in `business_data/ops/drills.jsonl` too.
- `python -m scripts.yq_backup pack <path> [<path> ...] [--apply]` encrypts a pre-load `.zip` archive or a backup folder
  (`manifest.json` and one CSV per table, for example an older release backup under `release\`) into a `.7z` beside it
  with the backup password, reads the archive back, and only then deletes the original. Without `--apply` it only says
  what it would do. A packed archive can be checked with `python -m scripts.yq_backup drill --archive <file>`.

## The backup before a release: `release`, and `unpack` for a rollback

`python -m scripts.yq_backup release <label>` is the backup a release script takes as its first part; the label names
the release in lower-case letters and digits with single hyphens between them (`r9`, `mi-v2`). In one run it:

1. backs up every table of the live database, only read, with `python -m scripts.db_backup --all` into
   `release\<date>_pre-<label>` in the backup store: never under `business_data`, so the daily OneDrive copy of the
   project folder never picks a plain copy up (the backup password is not in that program's environment);
2. checks every file against its `manifest.json` (`python -m scripts.db_backup --verify`);
3. packs the folder into `release\<date>_pre-<label>.7z` with the backup password (7-Zip AES-256, names hidden), as
   `pack` does: made as `.7z.partial`, read back with the password and every file counted, and only then renamed;
4. deletes the plain folder, and prints the archive's path as its last line.

It refuses, with nothing made, without the backup password or 7-Zip (a release backup is never plain), and when that
folder or its `.7z` is already there: a second backup of a release on the same day takes another label, such as `r9-2`.
A backup that does not complete (a lost connection, a stop) is removed, since it is not a backup. One whose files do not
match their manifest is kept for a look; one that could not be packed is kept too, a whole backup, which
`pack <folder> --apply` encrypts later. A row with `"kind": "release"` goes to `business_data/ops/drills.jsonl`. Exit
code: 0 only the encrypted copy is left; 1 it did not finish (its last line says what is left plain); 2 refused,
nothing was made.

A release script runs it once, after its own YES, stops unless the exit code is 0, and keeps the last line (the archive)
for its rollback part. The archive can be checked at any time with `python -m scripts.yq_backup drill --archive <file>`.

**A rollback** unpacks that archive first, then restores from the folder `unpack` prints:

```
python -m scripts.yq_backup unpack <archive> [--to <folder>]
python -m scripts.db_backup --restore <folder> --tables <t1,t2,…>          # a dry run: the plan
python -m scripts.db_backup --restore <folder> --tables <t1,t2,…> --yes    # one transaction
```

`unpack` reads the archive back with the password, unpacks it into a new, empty folder (by default a new
`unpack_<date>_<time>_1` folder in the backup store; a folder named with `--to` must be new or empty, and outside
OneDrive and the project folder), checks every file against its manifest and prints the folder that holds
`manifest.json` as its last line. A copy that does not unpack whole, or does not match its manifest, is removed again.
A plain `.zip` backup is unpacked the same way, without the password. The folder holds the backup unencrypted: delete
it once the restore is done. A row with `"kind": "unpack"` goes to `drills.jsonl`. Exit code: 0 unpacked and checked;
1 it did not unpack whole (its last line says what is left); 2 refused, nothing was written.

## The photo archive: `scripts/yq_photos.py`

The photos of Market Intel, and those of the older Product Finds and Field Notes boards, are kept in two private storage
buckets of the live project. `scripts/yq_photos.py` keeps an encrypted copy of them on this PC. It only reads production:
the rows and the list of objects through the read-only database session of `scripts/ops/db.py`, the photos through the
same read-only client.

- `python -m scripts.yq_photos archive` only looks: for each bucket, the objects and their size, the database rows that
  point at a photo, the rows whose photo storage does not hold and the objects no row points at. Nothing is downloaded,
  written or changed.
- `python -m scripts.yq_photos archive --apply [--out <folder>]` downloads every object into a work folder under
  `photos\`, takes the checksum of each one and compares it with the checksum the database recorded when the photo was
  taken (where it recorded one), and lists every object in `manifest.json`. The folder is packed into
  `photos\<date>_<HHMM>_photos.7z` with the backup password and the settings of the nightly job, the archive is read back with
  7-Zip's own test and its file count compared, and only then is the work folder deleted. It is refused without the
  backup password or 7-Zip, in a folder OneDrive syncs, inside the code folder, and on a drive without room for the
  copy. Exit code: 0 all clean; 1 the archive was made, but a photo is missing from storage, differs from its database
  checksum or could not be downloaded (each one is listed); 2 nothing was archived.
- `python -m scripts.yq_photos verify <archive> [--sample <n>]` is its restore drill: it reads the archive back,
  unpacks it outside OneDrive, checks every file against the manifest (checksum and size), opens a few photos at random
  and deletes the unpacked copy. Exit code: 0 the copy is whole, 1 it is not, 2 not run.

An `archive --apply` run that starts the download leaves a row in `business_data/ops/drills.jsonl` with
`"kind": "photos"`: the time, the archive's name, `passed`, `problems` or `failed` (with the reason), the name (never the
value) of the place the password came from, and counts only.

## Restoring

Unpack the archive with `python -m scripts.yq_backup unpack <archive>` (above: it checks the files and prints the
folder), or extract it by hand (`7z x yq-backup_<date>_<time>.7z -o<folder>`, it asks for the password), then use the
guarded restore, which is a dry run unless `--yes`: `python -m scripts.db_backup --restore <folder> --tables <list>`
(see its docstring and docs/MIGRATIONS.md, "Backup, restore and the preservation gate"). An older plain folder under
`release\` is already a backup folder: check it with `python -m scripts.db_backup --verify <folder>` and restore from it
directly. The restore refuses to overwrite rows that are newer than the backup, and it is rolled back as a whole on any
mismatch. Delete an unpacked folder once the restore is done: it holds the backup unencrypted.

Use these programs on the office PC only, not on a shared machine.
