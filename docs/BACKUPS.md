# Nightly backups (release R7d, audit SEC-19)

`scripts/nightly_backup.py` takes a read-only snapshot of every table, the login list (no password hashes) and a
list of the files in Supabase Storage, packs it into ONE archive encrypted with 7-Zip AES-256 (file names hidden
too), deletes the plain copy, and keeps the newest archive of each of the last 14 days and of each of the last 8
weeks. It writes to `%LOCALAPPDATA%\yq-backups` — never OneDrive (a OneDrive folder is refused) — and never uploads
anything.

## One-time setup (the owner, in a Command Prompt on the office PC)

1. Install 7-Zip (https://www.7-zip.org). Without it the archive is a plain `.zip` and a loud
   `UNENCRYPTED-BACKUP-WARNING.txt` appears next to it (and the task reports a failure).
2. Keep the password in the password manager and set it for your Windows user — **not** in `.env`, which sits in
   OneDrive:

       setx YQ_BACKUP_PASSWORD "a long passphrase"

3. Schedule it every night at 02:30 (Bahrain), as yourself:

       schtasks /Create /TN "YQ nightly backup" /SC DAILY /ST 02:30 /RL LIMITED /F /TR "\"C:\Users\<you>\OneDrive - YqBahrain\Desktop\YQ Bahrain Mobile Accessories\scripts\nightly_backup.cmd\""

   Check it once by hand: `python -m scripts.nightly_backup --dry-run`, then `schtasks /Run /TN "YQ nightly backup"`
   and look at `%LOCALAPPDATA%\yq-backups\nightly.log`.

## Restoring

Extract the archive (`7z x yq-backup_<date>_<time>.7z -o<folder>`, it asks for the password), then use the guarded
restore, which is a dry run unless `--yes`: `python -m scripts.db_backup --restore <folder> --tables <list>`
(see its docstring and docs/MIGRATIONS.md, "Backup, restore and the preservation gate").

Note: 7-Zip receives the password on its command line, so another program running as the same Windows user at that
moment could read it. On the office PC that is acceptable; do not run the task on a shared machine.
