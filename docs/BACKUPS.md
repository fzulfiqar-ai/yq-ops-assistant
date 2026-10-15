# Backups: the nightly job, the backup before every load, the restore drill, the photo archive

A backup here is a copy of every table of the live database (one CSV file per table and a `manifest.json`), kept on the
office PC in `%LOCALAPPDATA%\yq-backups`. That folder is never inside OneDrive (the nightly job refuses a OneDrive folder;
`YQ_BACKUP_DIR` can point it at another local folder) and nothing in it is ever uploaded.

| Where in `%LOCALAPPDATA%\yq-backups` | What is there | Made by |
|---|---|---|
| the folder itself | `yq-backup_<date>_<time>.7z`, one archive each night, and `nightly.log` | `scripts/nightly_backup.py`, started by Task Scheduler once the one-time setup below is done |
| `preload\` | `yq-backup_<date>_pre-data-<run>.7z`, taken before each load; the newest few are kept (`load.backup_keep` in `business_data/ops/config.json`) | `/yq-load` (`scripts/yq_load_go.py`) |
| `legacy\` | `<name>.7z`, the older backups that sat in `business_data/backups`, packed one at a time | `python -m scripts.yq_backup legacy --apply` |
| `release\` | one backup for each release, such as `2026-10-14_pre-mi-v2` (a CSV per table and `manifest.json`, packed with `pack`) | moved here after a release (a release script writes its backup under `business_data/backups/` first), or made here with `python -m scripts.db_backup --all --out <a folder here>` |
| `photos\` | `<date>_photos.7z`, an encrypted copy of the Market Intel photos with a manifest of every file (below) | `python -m scripts.yq_photos archive --apply` |

## The backup password

Archives are encrypted with 7-Zip (AES-256, file names hidden too) and the backup password. The load, the restore drill
and the packing commands (`legacy` and `pack`, below) look for the password in this order, and say only where they found
it, never the password itself (`scripts/yq_secrets.py`):

1. Windows Credential Manager, the generic credential `YQ_BACKUP_PASSWORD`;
2. the Windows user environment (`HKCU\Environment`), read afresh each time, so a value set later with `setx` works at once;
3. the environment of the running program.

The project's `.env` is never read for it, because it sits in OneDrive. To store the password once, in Credential Manager,
run `python %LOCALAPPDATA%\yq-tools\set_secrets.py password` (an owner-side helper kept outside the repository): it makes a
long password, stores it, shows it once in a Windows box for you to write down, and refuses to run when a password is
already stored. Or type `cmdkey /generic:YQ_BACKUP_PASSWORD /user:yq /pass` in a Command Prompt and enter your own (it shows
nothing as you type). Keep a copy of the password away from this PC: without it no archive can be opened on another PC.

The nightly job (next section) is a separate program that reads the password from its own environment only, so a scheduled
nightly job needs the password set for the Windows user as well (`setx`, in its setup). Use the same password for both.

## The nightly backup (release R7d, audit SEC-19)

`scripts/nightly_backup.py` takes a read-only snapshot of every table, the login list (no password hashes) and a list of
the files in Supabase Storage, packs it into ONE archive encrypted with 7-Zip AES-256 (file names hidden too), deletes the
plain copy, and keeps the newest archive of each of the last 14 days and of each of the last 8 weeks. It writes to
`%LOCALAPPDATA%\yq-backups` — never OneDrive (a OneDrive folder is refused) — and never uploads anything.

Without 7-Zip or without the password the job cannot make the `.7z`: it writes a `.zip` instead, leaves a warning file
(`UNENCRYPTED-BACKUP-WARNING.txt`) next to it and ends with exit code 2, which Task Scheduler shows as a failed run.

### One-time setup (the owner, in a Command Prompt on the office PC)

1. Install 7-Zip (https://www.7-zip.org).
2. Set the password for your Windows user, so that the scheduled task can read it. Keep it in the password manager and not
   in `.env`, which sits in OneDrive:

       setx YQ_BACKUP_PASSWORD "a long passphrase"

3. Schedule it every night at 02:30 (Bahrain), as yourself:

       schtasks /Create /TN "YQ nightly backup" /SC DAILY /ST 02:30 /RL LIMITED /F /TR "\"C:\Users\<you>\OneDrive - YqBahrain\Desktop\YQ Bahrain Mobile Accessories\scripts\nightly_backup.cmd\""

   Check it once by hand: `python -m scripts.nightly_backup --dry-run`, then `schtasks /Run /TN "YQ nightly backup"`
   and look at `%LOCALAPPDATA%\yq-backups\nightly.log`.

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
  (`manifest.json` and one CSV per table, for example a release backup under `release\`) into a `.7z` beside it with the
  backup password, reads the archive back, and only then deletes the original. Without `--apply` it only says what it
  would do. A packed archive can be checked with `python -m scripts.yq_backup drill --archive <file>`.

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
  `photos\<date>_photos.7z` with the backup password and the settings of the nightly job, the archive is read back with
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

Extract the archive (`7z x yq-backup_<date>_<time>.7z -o<folder>`, it asks for the password), then use the guarded
restore, which is a dry run unless `--yes`: `python -m scripts.db_backup --restore <folder> --tables <list>` (see its
docstring and docs/MIGRATIONS.md, "Backup, restore and the preservation gate"). A folder under `release\` is already a
backup folder: check it with `python -m scripts.db_backup --verify <folder>` and restore from it directly. The restore
refuses to overwrite rows that are newer than the backup, and it is rolled back as a whole on any mismatch.

Use these programs on the office PC only, not on a shared machine.
