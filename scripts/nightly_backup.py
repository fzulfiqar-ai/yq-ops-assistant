"""Nightly encrypted backup of the live database — local only, never uploaded (release R7d, plan §25
P2, audit SEC-19: "backups are manual, local, unencrypted, and exclude auth and storage").

    python -m scripts.nightly_backup                 # what Task Scheduler runs every night
    python -m scripts.nightly_backup --dry-run       # where it would write and what it would prune
    python -m scripts.nightly_backup --keep-folder   # also keep the plain CSV folder (debugging only)

What one run does, in order:
  1. A consistent read-only snapshot of EVERY public table (scripts.db_backup --all: one REPEATABLE
     READ, READ ONLY transaction, CSV per table + manifest.json, verified against the manifest).
  2. Two extra read-only files in the same folder: auth_users.csv — the login list (id, email,
     created / last sign-in / confirmed times, the user_roles role and status) with NO password
     hash, token or phone — and storage_manifest.csv — every object in Supabase Storage (bucket,
     path, size, type, updated), the files themselves stay in Storage.
  3. One archive of that folder: 7-Zip, AES-256 with encrypted file names (-mhe=on), password from the
     YQ_BACKUP_PASSWORD environment variable. If 7-Zip or the password is missing the archive is a
     plain .zip and a LOUD warning is printed and written next to it — merchant phones and trade
     prices then sit unencrypted on this disk.
  4. The plain CSV folder is deleted (unless --keep-folder): only the archive stays.
  5. Retention: the newest archive of each of the last 14 days and of each of the last 8 ISO weeks
     are kept; every other yq-backup_* archive in the folder is deleted. Nothing else is touched.

Where: %LOCALAPPDATA%\\yq-backups (override with YQ_BACKUP_DIR). A folder inside OneDrive (or any
other synced folder you name in YQ_BACKUP_SYNCED_DIRS) is REFUSED: a backup there would be copied to
the cloud unencrypted-at-rest by the sync client, which is exactly what SEC-19 is about.

Nothing here writes to the database or sends anything over the network except the read-only
database session. Restore stays scripts.db_backup --restore (guarded, dry run by default), after
extracting the archive:  7z x yq-backup_<stamp>.7z -o<folder>

Windows Task Scheduler (the owner runs this once, in a Command Prompt, as himself):
  setx YQ_BACKUP_PASSWORD "<a long passphrase kept in the password manager>"
  schtasks /Create /TN "YQ nightly backup" /SC DAILY /ST 02:30 /RL LIMITED /F /TR "\"<repo>\\scripts\\nightly_backup.cmd\""
The password lives in the user environment, NOT in .env (which sits in the OneDrive folder).
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

KEEP_DAILY = 14
KEEP_WEEKLY = 8
PREFIX = "yq-backup_"
_NAME = re.compile(r"^yq-backup_(\d{4}-\d{2}-\d{2})_(\d{4})\.(7z|zip)$")
PASSWORD_ENV = "YQ_BACKUP_PASSWORD"
WARNING_FILE = "UNENCRYPTED-BACKUP-WARNING.txt"

AUTH_SQL = """
select u.id::text as id, lower(u.email) as email, u.created_at, u.last_sign_in_at, u.email_confirmed_at,
       r.role, r.status
from auth.users u
left join public.user_roles r on lower(r.email) = lower(u.email)
order by lower(u.email)
"""
STORAGE_SQL = """
select o.bucket_id, o.name, (o.metadata->>'size') as size_bytes, (o.metadata->>'mimetype') as mimetype,
       o.updated_at
from storage.objects o
order by o.bucket_id, o.name
"""


# ── where (pure) ──────────────────────────────────────────────────────────────

def synced_roots(env: dict | None = None) -> list[Path]:
    """Folders a sync client uploads: OneDrive (personal + business) and any extra ones named in
    YQ_BACKUP_SYNCED_DIRS (';'-separated)."""
    env = dict(os.environ if env is None else env)
    out = []
    for k in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
        if env.get(k):
            out.append(Path(env[k]))
    for part in str(env.get("YQ_BACKUP_SYNCED_DIRS") or "").split(";"):
        if part.strip():
            out.append(Path(part.strip()))
    return out


def _norm(p: Path) -> str:
    return os.path.normcase(os.path.abspath(str(p))).rstrip("\\/")


def is_synced(path: Path, env: dict | None = None) -> bool:
    """True when `path` is inside a synced folder or has a 'OneDrive' folder anywhere in it."""
    target = _norm(path)
    for root in synced_roots(env):
        r = _norm(root)
        if target == r or target.startswith(r + os.sep):
            return True
    return any(part.lower().startswith("onedrive") for part in Path(os.path.abspath(str(path))).parts)


def backup_root(env: dict | None = None) -> Path:
    """YQ_BACKUP_DIR, else %LOCALAPPDATA%\\yq-backups (else ~/.yq-backups off Windows). Raises
    SystemExit for a synced folder."""
    env = dict(os.environ if env is None else env)
    if env.get("YQ_BACKUP_DIR"):
        root = Path(env["YQ_BACKUP_DIR"])
    elif env.get("LOCALAPPDATA"):
        root = Path(env["LOCALAPPDATA"]) / "yq-backups"
    else:
        root = Path.home() / ".yq-backups"
    if is_synced(root, env):
        raise SystemExit(f"REFUSING: {root} is inside a synced folder (OneDrive). Backups hold merchant phones and "
                         f"trade prices; keep them on this disk only. Set YQ_BACKUP_DIR to a local folder.")
    return root


# ── retention (pure) ──────────────────────────────────────────────────────────

def parse_name(name: str) -> tuple[date, str] | None:
    m = _NAME.match(name)
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1)), m.group(2)
    except ValueError:
        return None


def plan_retention(names: list[str], today: date, keep_daily: int = KEEP_DAILY,
                   keep_weekly: int = KEEP_WEEKLY) -> tuple[list[str], list[str]]:
    """(keep, delete) among our archive names. Kept: the newest archive of each of the last
    `keep_daily` days (today included) and the newest of each of the last `keep_weekly` ISO weeks
    (this week included). Anything not named like an archive of ours is in neither list."""
    ours = [(n, parse_name(n)) for n in names]
    ours = [(n, p) for n, p in ours if p]
    newest_by_day: dict[date, str] = {}
    for n, (d, hhmm) in sorted(ours, key=lambda x: (x[1][0], x[1][1], x[0])):
        newest_by_day[d] = n                       # later time of day wins
    keep: set[str] = set()
    day_floor = today - timedelta(days=keep_daily - 1)
    for d, n in newest_by_day.items():
        if day_floor <= d <= today:
            keep.add(n)
    this_monday = today - timedelta(days=today.weekday())
    week_floor = this_monday - timedelta(weeks=keep_weekly - 1)
    newest_by_week: dict[date, tuple[date, str]] = {}
    for d, n in newest_by_day.items():
        monday = d - timedelta(days=d.weekday())
        if week_floor <= monday <= this_monday and d <= today:
            cur = newest_by_week.get(monday)
            if cur is None or d > cur[0]:
                newest_by_week[monday] = (d, n)
    keep.update(n for _d, n in newest_by_week.values())
    delete = sorted(n for n, _p in ours if n not in keep)
    return sorted(keep), delete


# ── the archive ───────────────────────────────────────────────────────────────

def find_7z() -> str | None:
    for cand in (shutil.which("7z"), shutil.which("7za"), r"C:\Program Files\7-Zip\7z.exe",
                 r"C:\Program Files (x86)\7-Zip\7z.exe"):
        if cand and Path(cand).exists():
            return cand
    return None


def seven_zip_cmd(exe: str, archive: Path, folder: Path, password: str) -> list[str]:
    """7-Zip, AES-256, encrypted headers (file names hidden too), maximum compression."""
    return [exe, "a", "-t7z", "-mx=7", "-mhe=on", f"-p{password}", "-y", str(archive), str(folder / "*")]


def make_archive(folder: Path, dest: Path, stamp: str, *, password: str | None, exe: str | None) -> tuple[Path, bool]:
    """(archive path, encrypted). Plain zip + a warning file when it cannot encrypt."""
    if exe and password:
        archive = dest / f"{PREFIX}{stamp}.7z"
        r = subprocess.run(seven_zip_cmd(exe, archive, folder, password), capture_output=True, text=True)
        if r.returncode != 0 or not archive.exists():
            raise SystemExit(f"7-Zip failed (exit {r.returncode}): {(r.stderr or r.stdout)[-400:]}")
        return archive, True
    why = "7-Zip was not found" if not exe else f"{PASSWORD_ENV} is not set"
    banner = ("!" * 78 + "\n"
              f"WARNING: THIS BACKUP IS NOT ENCRYPTED ({why}).\n"
              "It holds merchant phones, trade prices and landed costs in plain CSV inside a zip.\n"
              "Install 7-Zip (https://www.7-zip.org) and set the password:  setx " + PASSWORD_ENV + " \"...\"\n"
              + "!" * 78)
    print(banner, file=sys.stderr)
    (dest / WARNING_FILE).write_text(banner + f"\nlast unencrypted archive: {PREFIX}{stamp}.zip\n", encoding="utf-8")
    base = dest / f"{PREFIX}{stamp}"
    shutil.make_archive(str(base), "zip", root_dir=str(folder))
    return Path(str(base) + ".zip"), False


# ── the extra read-only files ─────────────────────────────────────────────────

def _write_rows(path: Path, cur, sql: str) -> int:
    cur.execute(sql)
    cols = [d.name for d in cur.description]
    n = 0
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for row in cur.fetchall():
            w.writerow(["" if v is None else v for v in row])
            n += 1
    return n


def extras(folder: Path) -> dict:
    """auth_users.csv (no hashes) and storage_manifest.csv, read-only. A part that cannot be read
    is reported, never fatal: the table backup is the part that matters."""
    import psycopg
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    out: dict = {}
    url = os.environ.get("DATABASE_URL")
    if not url:
        return {"error": "DATABASE_URL missing"}
    with psycopg.connect(url, connect_timeout=30) as conn:
        conn.read_only = True
        for name, sql in (("auth_users.csv", AUTH_SQL), ("storage_manifest.csv", STORAGE_SQL)):
            try:
                with conn.transaction(), conn.cursor() as cur:
                    out[name] = _write_rows(folder / name, cur, sql)
            except Exception as e:  # noqa: BLE001
                out[name] = f"skipped: {type(e).__name__}: {str(e)[:160]}"
    return out


# ── the run ───────────────────────────────────────────────────────────────────

def run(*, dry_run: bool = False, keep_folder: bool = False, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    dest = backup_root()
    stamp = now.astimezone().strftime("%Y-%m-%d_%H%M")
    existing = sorted(p.name for p in dest.glob(f"{PREFIX}*")) if dest.exists() else []
    if dry_run:
        _keep, delete = plan_retention(existing + [f"{PREFIX}{stamp}.7z"], now.astimezone().date())
        print(f"would write {dest / (PREFIX + stamp)}.7z (encrypted: {bool(find_7z() and os.environ.get(PASSWORD_ENV))})")
        print(f"would delete {len(delete)} old archive(s): {', '.join(delete) or '-'}")
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    folder = dest / f"work_{stamp}"
    from scripts import db_backup
    db_backup.backup(None, folder, all_tables=True)            # read-only, verified, raises on a mismatch
    print("extras:", extras(folder))
    archive, encrypted = make_archive(folder, dest, stamp, password=os.environ.get(PASSWORD_ENV) or None,
                                      exe=find_7z())
    if not keep_folder:
        shutil.rmtree(folder, ignore_errors=True)
    if encrypted and (dest / WARNING_FILE).exists():
        (dest / WARNING_FILE).unlink()
    names = sorted(p.name for p in dest.glob(f"{PREFIX}*"))
    _keep, delete = plan_retention(names, now.astimezone().date())
    for n in delete:
        (dest / n).unlink(missing_ok=True)
    print(f"\nNightly backup: {archive} ({archive.stat().st_size:,} bytes, "
          f"{'AES-256 encrypted' if encrypted else 'NOT ENCRYPTED'}); pruned {len(delete)} old archive(s).")
    return 0 if encrypted else 2      # 2 = done, but unencrypted: Task Scheduler shows it as a failure


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print the destination and the pruning plan only")
    ap.add_argument("--keep-folder", action="store_true", help="keep the plain CSV folder next to the archive")
    a = ap.parse_args(argv)
    return run(dry_run=a.dry_run, keep_folder=a.keep_folder)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
