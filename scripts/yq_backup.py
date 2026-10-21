"""python -m scripts.yq_backup : proves a backup can be restored, and puts plain backups away encrypted.

    python -m scripts.yq_backup drill [--archive <file>] [--keep]
        The restore drill, on THIS PC only: the newest pre-load archive (or the one named) is read back,
        unpacked outside OneDrive, checked against its manifest, and restored, every table, into a
        throwaway PostgreSQL of its own (the portable one in %LOCALAPPDATA%\\yq-tools). Production is only
        read, for its schema (scripts.local_replay_db --schema-only). Every table's row count is compared
        with the manifest twice: by the restore itself, and again here. The result is a row of
        business_data/ops/drills.jsonl; the cluster and the unpacked files are deleted afterwards (they hold
        unmasked data) unless --keep.

    python -m scripts.yq_backup legacy [--apply]
        The plain backup folders under business_data/backups/ (inside OneDrive, so synced to the cloud):
        each is packed into %LOCALAPPDATA%\\yq-backups\\legacy\\<name>.7z with the backup password (7-Zip
        AES-256, names hidden), read back with 7-Zip's own test and its file count compared, and only
        then is the plain folder deleted. A folder whose .7z is already there, or whose files do not
        match its manifest, is refused and kept. Without --apply it only says what it would do. Afterwards,
        empty the OneDrive recycle bin: OneDrive keeps deleted files there for a while.

    python -m scripts.yq_backup pack <path> [<path> ...] [--apply]
        Plain backups that are already on this PC: a pre-load .zip (preload\\yq-backup_..._pre-data-....zip)
        or a backup folder holding manifest.json (release\\<name>). Each becomes <name>.7z next to it, with
        the backup password (7-Zip AES-256, names hidden) and laid out like the load's own pre-load archives
        (manifest.json and the tables at the top), so `drill --archive <it>` restores it. The source is
        checked first: a folder against its manifest (scripts.db_backup --verify); a zip is tested,
        unpacked in the backup store (outside OneDrive) and its files checked the same way, so no zip ever
        sits inside the .7z. The .7z is made as <name>.7z.partial, read back with the password, every file
        counted, given the source's date (the load's pruning and the drill go by date: an old backup keeps
        sorting as old), and only then renamed; a .partial that a stopped run left is named and made again.
        Only then is the plain copy deleted, tried again a few times, and anything that could not be
        deleted is named, never passed over. A path that is not a plain backup, or whose .7z is already
        there, is refused. Without --apply it only says what it would do. One row per path in
        business_data/ops/drills.jsonl. Exit 0: every plain copy is gone; 1: one is left (refused, failed
        or not deleted); 2: refused, the backup password or 7-Zip is missing.

    python -m scripts.yq_backup release <label>
        The backup before a release (a release script's first part; the label is lower-case letters and
        digits with single hyphens between them, such as r9 or mi-v2). Every table of the live database,
        only read, by `python -m scripts.db_backup --all --out <the backup store>\\release\\<date>_pre-<label>`
        as a child (never under business_data, so never in the daily OneDrive copy of the project folder;
        the backup password is never in its environment), then `scripts.db_backup --verify` on that folder,
        then sealed as pack seals a folder: <date>_pre-<label>.7z made as .partial, read back with the
        password and every file counted, renamed, and only then the plain folder deleted. The archive's path
        is the last line printed. Refused before anything is made without the backup password or 7-Zip (a
        release backup is only ever encrypted), for a backup store that is or leads inside OneDrive, the code
        folder or the project folder (YQ_BACKUP_DIR; a relative one is read from the folder this runs in),
        and when that folder or its .7z is already there. A backup that did not complete is removed (it is
        not a backup); one that completed but does not match its manifest, or could not be sealed, is kept
        and named. Every way out once the backup has begun, a stop with Ctrl+C included, ends with a line
        that says what is left plain and what to do with it, and one row in business_data/ops/drills.jsonl
        (a row that cannot be written is said and changes nothing else; a stop is then raised again). Exit
        0: only the encrypted copy is left; 1: it did not finish (the last line says what is left plain);
        2: refused, nothing was made.

    python -m scripts.yq_backup unpack <archive> [--to <folder>]
        A rollback's first step: a backup archive (a .7z with the password, or a plain .zip) read back,
        unpacked into a new, empty folder (default: a new unpack_<date>_<time>_<n> folder in the backup
        store, refused like release's store; a folder named with --to must be new or empty, and outside
        OneDrive and the project folder), and its files checked against its manifest (scripts.db_backup
        --verify). The folder for `python -m scripts.db_backup --restore <folder> --tables ...` is the last
        line printed; it holds the backup unencrypted, so it is deleted once the restore is done. A copy that
        does not unpack whole, does not match its manifest, or whose unpack is stopped (Ctrl+C) is removed
        again, and the last line says so. One row in drills.jsonl. Exit 0: unpacked and checked; 1: it did
        not unpack whole (the last line says what is left); 2: refused, nothing was written.

Never writes to production: the restore's database address is forced to a loopback address the drill
made, and any other address is refused before anything runs; a release backup only reads it. The backup
password comes from scripts/yq_secrets.py and is never printed (7-Zip gets it on its command line, as
scripts/nightly_backup.py always has: a known limit on a PC that other people use); the scripts.db_backup
children of legacy, pack, release and unpack never have it in their environment.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import yq_secrets  # noqa: E402
from scripts.ops import paths  # noqa: E402

DRILL_DB = "yq_restore_drill"
PG_BIN = Path(os.environ.get("LOCALAPPDATA", "")) / "yq-tools" / "pgsql" / "bin"
LOOPBACK = {"localhost", "127.0.0.1", "::1"}


class DrillError(Exception):
    pass


def backup_root() -> Path:
    from scripts import nightly_backup
    return Path(nightly_backup.backup_root())


def find_7z() -> str | None:
    from scripts import nightly_backup
    return nightly_backup.find_7z()


def newest_archive(root: Path) -> Path | None:
    found = sorted((p for p in (root / "preload").glob("yq-backup_*") if p.suffix in (".zip", ".7z")),
                   key=lambda p: (p.stat().st_mtime, p.name))
    return found[-1] if found else None


def is_loopback(dsn: str) -> bool:
    try:
        host = urlparse(dsn).hostname or ""
    except ValueError:
        return False
    return host in LOOPBACK


def read_back(archive: Path, password: str | None, exe: str | None) -> tuple[bool, str]:
    """(it reads back whole, what was found). A zip: zipfile's own CRC test; a 7z: 7-Zip's test with
    the password (headers are encrypted: a wrong password cannot even list it). A .ZIP is a zip too."""
    if archive.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(archive) as z:
                bad = z.testzip()
                n = len(z.namelist())
        except (OSError, zipfile.BadZipFile) as exc:
            return False, f"the zip cannot be read ({type(exc).__name__})"
        return (bad is None and n > 0), (f"{n} files, all whole" if bad is None else f"{bad} is damaged")
    if not exe or not password:
        return False, "a 7z archive needs 7-Zip and the backup password to be read back"
    r = subprocess.run([exe, "t", f"-p{password}", "-y", str(archive)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    ok = r.returncode == 0 and "Everything is Ok" in (r.stdout or "")
    files = next((ln for ln in (r.stdout or "").splitlines() if ln.strip().startswith("Files:")), "").strip()
    return ok, (files or ("Everything is Ok" if ok else f"7-Zip test failed (exit {r.returncode})"))


def unpack(archive: Path, dest: Path, password: str | None, exe: str | None) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
        return
    r = subprocess.run([exe, "x", f"-p{password}", "-y", f"-o{dest}", str(archive)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise DrillError(f"7-Zip could not unpack it (exit {r.returncode})")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def run(cmd: list, env: dict | None = None, timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run([str(c) for c in cmd], cwd=str(ROOT), env=env, shell=False, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def start_cluster(pg_ctl: Path, pg_dir: Path, port: int, log: Path) -> int:
    """pg_ctl start with nothing captured: the server it starts would hold a captured pipe open, and the
    call would never return. Its own output goes to `log`."""
    r = subprocess.run([str(pg_ctl), "-D", str(pg_dir), "-o", f"-p {port} -c listen_addresses=127.0.0.1",
                        "-l", str(log), "-w", "start"], cwd=str(ROOT), shell=False, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    return r.returncode


def restore_argv(folder: Path, tables: list[str]) -> list[str]:
    """The restore the drill runs: every table of the backup, applied (--yes), never --allow-newer."""
    return [sys.executable, "-X", "utf8", "-m", "scripts.db_backup", "--restore", str(folder), "--tables",
            ",".join(tables), "--yes", "--dsn-env", "YQ_DRILL_PG"]


def drill_env(base: dict, dsn: str) -> dict:
    """The restore child's environment: the drill's address under YQ_DRILL_PG AND under DATABASE_URL, so
    not even the default address can lead to production. Refused when the address is not loopback."""
    if not is_loopback(dsn):
        raise DrillError("the drill's database address is not on this PC: refused")
    env = dict(base)
    env["YQ_DRILL_PG"] = dsn
    env["DATABASE_URL"] = dsn
    env.pop(yq_secrets.BACKUP_PASSWORD, None)
    return env


def record(row: dict) -> None:
    f = paths.ops_dir() / "drills.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "a", encoding="utf-8", newline="\n") as out:
        out.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def drill(archive: Path | None, keep: bool, say=print) -> int:
    t0 = time.monotonic()
    root = backup_root()
    archive = archive or newest_archive(root)
    if archive is None or not archive.is_file():
        say("No pre-load archive to drill.")
        return 2
    password, source = yq_secrets.backup_password()
    exe = find_7z()
    row = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "archive": archive.name,
           "encrypted": archive.suffix == ".7z", "password_from": source}
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    work = root / f"drill_{stamp}"
    pg_dir = work / "pg"
    port = free_port()
    pg_ctl = PG_BIN / "pg_ctl.exe"
    started = False
    try:
        ok, said = read_back(archive, password, exe)
        say(f"1. Read back {archive.name}: {said}")
        if not ok:
            raise DrillError("the archive does not read back whole")
        folder = work / "files"
        unpack(archive, folder, password, exe)
        if not (folder / "manifest.json").is_file():
            inner = [p for p in folder.iterdir() if p.is_dir() and (p / "manifest.json").is_file()]
            folder = inner[0] if inner else folder
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        tables = sorted(manifest["tables"])
        say(f"2. Unpacked {len(tables)} tables into {work}")
        v = run([sys.executable, "-X", "utf8", "-m", "scripts.db_backup", "--verify", str(folder)])
        if v.returncode != 0 or "Verify OK" not in v.stdout:
            raise DrillError("the unpacked files do not match their manifest")
        say("3. Every file matches its manifest (rows and columns)")
        for tool in ("initdb.exe", "pg_ctl.exe", "createdb.exe"):
            if not (PG_BIN / tool).is_file():
                raise DrillError(f"the portable PostgreSQL is missing {tool} in {PG_BIN}")
        r = run([PG_BIN / "initdb.exe", "-D", pg_dir, "-U", "postgres", "-A", "trust", "-E", "UTF8"])
        if r.returncode != 0:
            raise DrillError("initdb failed: " + (r.stderr or r.stdout)[-200:])
        if start_cluster(pg_ctl, pg_dir, port, work / "pg.log") != 0:
            raise DrillError("the throwaway PostgreSQL did not start")
        started = True
        dsn = f"postgresql://postgres@127.0.0.1:{port}/{DRILL_DB}"
        env = dict(os.environ, YQ_LOCAL_PG_PORT=str(port))
        dotenv = paths.data_root() / ".env"          # production's address, read only for its schema
        if dotenv.is_file():
            env["YQ_ENV_FILE"] = str(dotenv)
        r = run([sys.executable, "-X", "utf8", "-m", "scripts.local_replay_db", "--db", DRILL_DB, "--schema-only"], env)
        if r.returncode != 0:
            raise DrillError("the schema could not be copied: " + (r.stdout + r.stderr)[-300:])
        say(f"4. A throwaway PostgreSQL on port {port} holds production's schema (read only from production)")
        here = set(existing_tables(dsn))
        skipped = [t for t in tables if t not in here]       # a table the local copy cannot hold (pgvector)
        tables = [t for t in tables if t in here]
        if skipped:
            say(f"   Not drilled ({len(skipped)}): {', '.join(skipped)}: the local copy of the schema leaves them out")
            row["not_drilled"] = skipped
        r = run(restore_argv(folder, tables), drill_env(os.environ, dsn))
        if r.returncode != 0 or "Restore complete." not in r.stdout:
            raise DrillError("the restore did not complete: " + (r.stdout + r.stderr)[-500:])
        counts = recount(dsn, tables)
        # the restore records itself: one audit_log row more than the backup holds (db_backup.restore)
        expected = {t: int(manifest["tables"][t]["rows"]) + (1 if t == "audit_log" else 0) for t in tables}
        wrong = {t: (counts.get(t), expected[t]) for t in tables if counts.get(t) != expected[t]}
        rows = sum(int(manifest["tables"][t]["rows"]) for t in tables)
        if wrong:
            raise DrillError(f"{len(wrong)} tables hold another count than the manifest: {sorted(wrong)[:5]}")
        say(f"5. Restored every table: {len(tables)} tables, {rows:,} rows, each count equal to the manifest")
        row.update(result="passed", tables=len(tables), rows=rows, seconds=round(time.monotonic() - t0, 1))
        record(row)
        say(f"RESTORE DRILL PASSED in {row['seconds']} s.")
        return 0
    except (DrillError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        row.update(result="failed", why=str(exc)[:300], seconds=round(time.monotonic() - t0, 1))
        record(row)
        say(f"RESTORE DRILL FAILED: {exc}")
        return 1
    finally:
        if started:
            run([pg_ctl, "-D", pg_dir, "-m", "fast", "-w", "stop"])
        if not keep:
            shutil.rmtree(work, ignore_errors=True)


def existing_tables(dsn: str) -> list[str]:
    """The public tables of the drill's database (loopback only)."""
    if not is_loopback(dsn):
        raise DrillError("refused: not a loopback address")
    import psycopg
    with psycopg.connect(dsn, connect_timeout=15) as conn, conn.cursor() as cur:
        cur.execute("select table_name from information_schema.tables where table_schema = 'public' "
                    "and table_type = 'BASE TABLE'")
        return sorted(str(r[0]) for r in cur.fetchall())


def recount(dsn: str, tables: list[str]) -> dict:
    """The rows of each table in the drill's database (psycopg on the loopback address only)."""
    if not is_loopback(dsn):
        raise DrillError("refused: not a loopback address")
    import psycopg
    out = {}
    with psycopg.connect(dsn, connect_timeout=15) as conn, conn.cursor() as cur:
        for t in tables:
            cur.execute(f'select count(*) from public."{t}"')
            out[t] = int(cur.fetchone()[0])
    return out


# ── sealing a plain backup, and deleting the plain copy ─────────────────────────────────────────────

DELETE_TRIES = 5          # OneDrive, a virus scanner or an Explorer window often lets go of a file a moment later
DELETE_WAIT_S = 1.0


def files_of(folder: Path) -> int:
    return sum(1 for p in folder.rglob("*") if p.is_file())


def counted(said: str, expected: int) -> bool:
    """Does 7-Zip's test (read_back) count `expected` files? It prints no count for an archive of one file."""
    m = re.search(r"Files:\s*(\d+)", said)
    return (int(m.group(1)) if m else 1) == expected


def drop(path: Path) -> bool:
    """Remove a file this program made (an archive that did not read back); True when it is gone."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        return False
    return True


PARTIAL = ".partial"


def partial_of(archive: Path) -> Path:
    """Where an archive is made until it reads back whole: <name>.7z.partial. Nothing that reads the backup store
    takes one for an archive (the drill and the load's pruning go by .7z / .zip, the nightly retention by name)."""
    return archive.with_name(archive.name + PARTIAL)


def plain_env(base: dict | None = None) -> dict:
    """A scripts.db_backup child's environment: this one's without the backup password, which only 7-Zip is given (on
    its command line)."""
    env = dict(os.environ if base is None else base)
    env.pop(yq_secrets.BACKUP_PASSWORD, None)
    return env


def verify_folder(folder: Path) -> None:
    """Every file of a plain backup against its manifest (scripts.db_backup --verify, as the drill runs it).
    Raises DrillError when they differ, or when the check cannot run."""
    try:
        v = run([sys.executable, "-X", "utf8", "-m", "scripts.db_backup", "--verify", str(folder)], env=plain_env())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DrillError(f"the files could not be checked against their manifest ({type(exc).__name__})") from None
    if v.returncode != 0 or "Verify OK" not in v.stdout:
        raise DrillError("the files do not match their manifest")


def _put_in_place(partial: Path, archive: Path) -> None:
    """seal's last step: the whole, dated .partial takes its final name (a test stands in for it)."""
    os.replace(partial, archive)


def seal(folder: Path, archive: Path, password: str, exe: str, date: tuple[float, float] | None = None,
         note=None) -> str:
    """The files of `folder` into `archive` with scripts.nightly_backup's own 7-Zip command (AES-256, names
    hidden, the files at the top as in the load's pre-load archives). It is made as <archive>.partial, read back
    there with the password (7-Zip's own test, every file of the folder counted), given `date` (atime, mtime)
    when one is set, and only then renamed to `archive`: a run that is stopped leaves a .partial, never a
    half-made archive under the final name. A .partial such a run left is removed first, and `note` is told.
    What the read-back said. Raises DrillError on any doubt, its .partial removed; an `archive` already there
    is refused, never replaced; `folder` is never changed."""
    from scripts import nightly_backup
    if os.path.lexists(archive):
        raise DrillError(f"{archive.name} is already there")
    partial = partial_of(archive)
    if os.path.lexists(partial):
        if not drop(partial):
            raise DrillError(f"a half-made {partial.name} from a run that was stopped could not be removed")
        if note is not None:
            note(f"A half-made {partial.name} from a run that was stopped was removed; it is made again")
    expected = files_of(folder)
    try:
        r = subprocess.run(nightly_backup.seven_zip_cmd(exe, partial, folder, password), capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0 or not partial.is_file():
            raise DrillError(f"7-Zip could not pack {archive.name} (exit {r.returncode})")
        ok, said = read_back(partial, password, exe)
        if not ok or not counted(said, expected):
            raise DrillError(f"{archive.name} does not read back with all {expected} files ({said})")
        if date is not None:
            os.utime(partial, date)
        if os.path.lexists(archive):
            raise DrillError(f"{archive.name} appeared while it was being made: not replaced")
        _put_in_place(partial, archive)
    except OSError as exc:              # 7-Zip not startable, "Access is denied" on the date or the rename
        drop(partial)
        raise DrillError(f"{archive.name} could not be made ({type(exc).__name__}: {exc.strerror or 'no detail'})") \
            from None
    except BaseException:
        drop(partial)
        raise
    return said


def _delete_once(path: Path) -> None:
    """One try at deleting a plain copy: a folder with all it holds (as much as can go), or a file. May raise."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)       # what it could not delete is counted afterwards
    elif os.path.lexists(path):
        path.unlink()


def _make_writable(path: Path) -> None:
    """Clear the read-only flag of what is left: Windows answers "Access is denied" to deleting a read-only file."""
    try:
        found = [path, *path.rglob("*")] if path.is_dir() and not path.is_symlink() else [path]
    except OSError:
        found = [path]
    for p in found:
        try:
            os.chmod(p, stat.S_IMODE(os.lstat(p).st_mode) | stat.S_IWRITE)
        except (OSError, NotImplementedError):
            pass


def _still_there(path: Path) -> list[str]:
    """What is left of `path`: [] when it is gone; else the files left in it (by their path inside it), or its own
    name for a file, or for a folder that holds nothing but empty folders."""
    try:
        if not os.path.lexists(path):
            return []
        if path.is_dir() and not path.is_symlink():
            return sorted(p.relative_to(path).as_posix() for p in path.rglob("*") if p.is_file()) or [path.name]
        return [path.name]
    except OSError:
        return [path.name]


def remove_plain(path: Path) -> list[str]:
    """Delete a plain copy of a backup (a zip, a folder, or a copy this program unpacked) and say what is left: []
    when all of it is gone. Tried DELETE_TRIES times, DELETE_WAIT_S apart, a read-only flag cleared before each
    new try. It never raises, "Access is denied" included: what it could not delete is the answer."""
    path = Path(path)
    left: list[str] = []
    for attempt in range(max(1, DELETE_TRIES)):
        if attempt:
            time.sleep(DELETE_WAIT_S)
            _make_writable(path)
        try:
            _delete_once(path)
        except OSError:
            pass
        left = _still_there(path)
        if not left:
            return []
    return left


def left_words(src: Path, left: list[str]) -> str:
    """remove_plain's answer in words: what of `src` is still there."""
    if left == [src.name]:
        return "its empty folder is still there" if src.is_dir() else "it is still there"
    shown = ", ".join(left[:5]) + (f" and {len(left) - 5} more" if len(left) > 5 else "")
    return f"{len(left)} of its files {'is' if len(left) == 1 else 'are'} still there ({shown})"


# ── the old plain backups ────────────────────────────────────────────────────────────────────────

def legacy_folders() -> list[Path]:
    base = paths.data_root() / "business_data" / "backups"
    return sorted(p for p in base.iterdir() if p.is_dir() and (p / "manifest.json").is_file()) if base.is_dir() else []


def pack_legacy(folder: Path, dest_dir: Path, password: str, exe: str, note=None) -> Path:
    """One plain backup folder -> <dest>/<name>.7z. Refused when that archive is already there: a folder that an
    earlier run could not delete in full is never packed over its whole archive. The folder is checked against
    its manifest first (verify_folder), then sealed (seal: made as .partial, read back, every file counted).
    Raises DrillError on any doubt."""
    archive = dest_dir / f"{folder.name}.7z"
    if os.path.lexists(archive):
        raise DrillError(f"{archive.name} is already in {dest_dir} and is never replaced (if this folder is what an "
                         "earlier run could not delete, delete it by hand)")
    verify_folder(folder)
    dest_dir.mkdir(parents=True, exist_ok=True)
    seal(folder, archive, password, exe, note=note)
    return archive


def legacy(apply: bool, say=print) -> int:
    folders = legacy_folders()
    if not folders:
        say("No plain backup folder under business_data/backups/.")
        return 0
    password, source = yq_secrets.backup_password()
    exe = find_7z()
    if not password or not exe:
        say("Set the backup password first (cmdkey /generic:YQ_BACKUP_PASSWORD /user:yq /pass) and install 7-Zip: "
            "nothing was changed.")
        return 2
    dest = backup_root() / "legacy"
    say(f"{len(folders)} plain backup folders; encrypted copies go to {dest} (password from {source}).")
    if not apply:
        for f in folders:
            there = dest / f"{f.name}.7z"
            say(f"  would refuse: {f.name} ({there.name} is already in {dest})" if os.path.lexists(there)
                else f"  would pack, test and then delete: {f.name}")
        say("Dry run: nothing was changed. Run with --apply to do it.")
        return 0
    done = 0
    for f in folders:
        try:
            archive = pack_legacy(f, dest, password, exe, note=lambda text, f=f: say(f"  {f.name}: {text}"))
        except DrillError as exc:
            say(f"  KEPT {f.name}: {exc}")
            continue
        left = remove_plain(f)
        gone = not left
        say(f"  {f.name}: packed and read back ({archive.name}); plain folder " + (
            "deleted" if gone else f"could NOT be deleted: {left_words(f, left)}"))
        done += int(gone)
    record({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "legacy_packed": done,
            "folders": len(folders)})
    say(f"{done} of {len(folders)} folders are now only in the encrypted store. Empty the OneDrive recycle bin.")
    return 0 if done == len(folders) else 1


# ── plain backups already on this PC: a pre-load zip, a backup folder ───────────────────────────────

KIND_WORDS = {"zip": "a plain zip", "folder": "a plain backup folder"}


def _n(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def size_words(n: int) -> str:
    for unit, step in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= step:
            return f"{n / step:.1f} {unit}"
    return _n(n, "byte")


def manifest_tables(text: str) -> int:
    """How many tables a backup manifest lists; 0 when the text is not one (a web app's manifest.json, say)."""
    try:
        data = json.loads(text)
    except ValueError:
        return 0
    tables = data.get("tables") if isinstance(data, dict) else None
    return len(tables) if isinstance(tables, dict) else 0


def _is_junction(p: Path) -> bool:
    check = getattr(os.path, "isjunction", None)          # Python 3.12+: a Windows link to a folder
    return bool(check and check(p))


def survey(src: Path) -> dict:
    """What `src` is, before anything is done: {kind, target, tables, files, bytes}, or {refused: why}."""
    try:
        if os.path.islink(src) or _is_junction(src):
            return {"refused": "it is a link to another place: name that place itself"}
        if src.is_file() and src.suffix.lower() == ".zip":
            kind, target = "zip", src.with_suffix(".7z")
        elif src.is_dir() and (src / "manifest.json").is_file():
            kind, target = "folder", src.with_name(src.name + ".7z")
        elif not src.exists():
            return {"refused": "it is not there"}
        elif src.suffix.lower() == ".7z":
            return {"refused": "it is already encrypted (a .7z)"}
        else:
            return {"refused": "it is not a plain backup (a .zip, or a folder holding manifest.json)"}
        if os.path.lexists(target):
            return {"refused": f"{target.name} is already there next to it"}
        if kind == "zip":
            with zipfile.ZipFile(src) as z:
                names = [i.filename for i in z.infolist() if not i.is_dir()]
                inner = [n for n in names if n.count("/") == 1 and n.endswith("/manifest.json")]
                where = "manifest.json" if "manifest.json" in names else (inner[0] if len(inner) == 1 else None)
                text = z.read(where).decode("utf-8") if where else ""
            files, size = len(names), src.stat().st_size
        else:
            found = [p for p in src.rglob("*") if p.is_file()]
            files, size = len(found), sum(p.stat().st_size for p in found)
            text = (src / "manifest.json").read_text(encoding="utf-8")
        tables = manifest_tables(text)
        if not tables:
            return {"refused": "it holds no backup manifest (a manifest.json that lists its tables)"}
        return {"kind": kind, "target": target, "tables": tables, "files": files, "bytes": size,
                "partial": os.path.lexists(partial_of(target))}
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        return {"refused": f"it cannot be read ({type(exc).__name__})"}


def scratch_dir(stamp: str, n: int, kind: str = "pack") -> Path:
    """A new, empty folder in the backup store (outside OneDrive), <kind>_<stamp>_<n>: pack unpacks a zip into one,
    unpack a backup for a restore. Never one already there (FileExistsError), since it is deleted afterwards."""
    try:
        root = backup_root()
    except SystemExit as exc:                        # scripts.nightly_backup refuses a store inside OneDrive
        raise DrillError(str(exc)) from None
    tmp = root / f"{kind}_{stamp}_{n}"
    tmp.mkdir(parents=True, exist_ok=False)
    return tmp


def backup_folder(top: Path) -> Path:
    """The folder of an unpacked zip that holds manifest.json: its top, or the one folder below it that does."""
    if (top / "manifest.json").is_file():
        return top
    inner = [p for p in top.iterdir() if p.is_dir() and (p / "manifest.json").is_file()]
    if len(inner) != 1:
        raise DrillError("the zip holds no manifest.json at its top or one folder down")
    return inner[0]


def check_and_seal(src: Path, info: dict, password: str, exe: str, scratch, line, unpacked: list) -> str:
    """pack_one's steps up to a .7z read back whole, dated and under its final name; raises on any doubt, and
    then no archive of this run is left (seal names it only once it is whole). A zip is unpacked into scratch()
    (added to `unpacked`, which the caller deletes)."""
    kind, target = info["kind"], info["target"]
    dated = src.stat()
    folder = src
    if kind == "zip":
        ok, said = read_back(src, None, None)
        line(f"Read back the zip: {said}")
        if not ok:
            raise DrillError("the zip does not read back whole")
        tmp = scratch()
        unpacked.append(tmp)
        unpack(src, tmp, None, None)
        folder = backup_folder(tmp)
        found = files_of(folder)
        if found != info["files"]:
            raise DrillError(f"only {found} of the zip's {info['files']} files are in its backup folder")
        line(f"Unpacked {_n(found, 'file')} into {tmp} (outside OneDrive)")
    verify_folder(folder)
    line("Every file matches its manifest (rows and columns)")
    said = seal(folder, target, password, exe, date=(dated.st_atime, dated.st_mtime), note=line)  # pruning goes by date
    try:
        size = size_words(target.stat().st_size)
    except OSError:                                  # only for the line below: never a reason to undo the pack
        size = "its size unknown"
    line(f"Packed {target.name} ({size}) and read it back with the password ({said}); it keeps the {kind}'s date")
    return said


def pack_one(src: Path, scratch, password: str, source: str | None, exe: str, say=print) -> tuple[dict, bool]:
    """One plain backup -> <its name>.7z next to it (see pack): (its row, True when only the encrypted copy is
    left). The plain copy is deleted only after the .7z read back whole; once that delete began, the .7z stays."""
    t0 = time.monotonic()
    row: dict = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": "pack", "source": src.name,
                 "deleted": False}
    info = survey(src)
    if "refused" in info:
        say(f"{src.name}: REFUSED: {info['refused']}. Nothing was changed.")
        row.update(result="refused", why=info["refused"], seconds=round(time.monotonic() - t0, 1))
        return row, False
    kind, target = info["kind"], info["target"]
    row.update(source_type=kind, archive=target.name, files=info["files"], password_from=source)
    say(f"{src.name}: {KIND_WORDS[kind]}, {_n(info['tables'], 'table')}, {_n(info['files'], 'file')}, "
        f"{size_words(info['bytes'])}")
    steps = [0]

    def line(text: str) -> None:
        steps[0] += 1
        say(f"  {steps[0]}. {text}")

    unpacked: list[Path] = []
    try:
        try:
            check_and_seal(src, info, password, exe, scratch, line, unpacked)
        except (DrillError, OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile,
                subprocess.TimeoutExpired) as exc:
            why = str(exc) or type(exc).__name__
            say(f"  KEPT: {why}. The plain {kind} is untouched.")
            row.update(result="failed", why=why[:300])
        else:
            left = remove_plain(src)
            row.update(result="packed", deleted=not left)
            if left:
                row["left"] = len(left)
                line(f"The plain {kind} could NOT be deleted: {left_words(src, left)}. Close what holds it (Excel, an "
                     "Explorer window, OneDrive), then delete it by hand: the encrypted copy is whole.")
            else:
                line(f"Deleted the plain {kind}")
    finally:
        for tmp in unpacked:
            stuck = remove_plain(tmp)
            if stuck:
                row["unpacked_left"] = len(stuck)
                say(f"  WARNING: the unpacked copy in {tmp} could not be removed ({len(stuck)} still there): delete "
                    "that folder by hand, it holds the backup unencrypted.")
            else:
                line("Removed the unpacked copy")
    row["seconds"] = round(time.monotonic() - t0, 1)
    done = bool(row["deleted"]) and "unpacked_left" not in row
    if done:
        say(f"  Done in {row['seconds']} s: only the encrypted copy is left.")
    elif row["result"] == "packed":
        say("  NOT DONE: the encrypted copy is made and read back, but a plain copy is still there (see above).")
    return row, done


def _missing(password: str | None, exe: str | None) -> str:
    out = []
    if not password:
        out.append("no backup password is found on this PC (set it once: cmdkey /generic:YQ_BACKUP_PASSWORD /user:yq "
                   "/pass)")
    if not exe:
        out.append("7-Zip is not installed (https://www.7-zip.org)")
    return "; ".join(out)


def pack(sources: list[Path], apply: bool, say=print) -> int:
    """Plain backups already on this PC -> encrypted .7z archives next to them (the module's docstring says how).
    Exit 0: every plain copy is gone (a dry run: 0 as well); 1: one is left; 2: no password or no 7-Zip."""
    todo: list[Path] = []
    for s in sources:
        p = Path(os.path.abspath(s))
        if all(os.path.normcase(str(p)) != os.path.normcase(str(q)) for q in todo):
            todo.append(p)
    password, source = yq_secrets.backup_password()
    exe = find_7z()
    missing = _missing(password, exe)
    if not apply:
        for src in todo:
            info = survey(src)
            if "refused" in info:
                say(f"{src.name}: would refuse: {info['refused']}.")
                continue
            say(f"{src.name}: {KIND_WORDS[info['kind']]}, {_n(info['tables'], 'table')}, {_n(info['files'], 'file')}, "
                f"{size_words(info['bytes'])}: would be packed into {info['target']}, read back with the password, "
                "and only then deleted."
                + (f" The half-made {partial_of(info['target']).name} a stopped run left there would be made again."
                   if info["partial"] else ""))
        if missing:
            say(f"Note: {missing}: --apply would refuse.")
        say("Dry run: nothing was changed. Run with --apply to do it.")
        return 0
    if missing:
        say(f"Refused, nothing was changed: {missing}.")
        return 2
    from scripts import nightly_backup
    say(f"{_n(len(todo), 'plain backup')}: each is checked against its manifest, packed with 7-Zip (AES-256, names "
        f"hidden; the password from {source}), read back with the password, and only then deleted.")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    done, unrecorded, synced = 0, False, False
    for n, src in enumerate(todo, 1):
        row, ok = pack_one(src, lambda n=n: scratch_dir(stamp, n), password, source, exe, say)
        try:
            record(row)
        except OSError as exc:
            unrecorded = True
            say(f"  The row of {src.name} could not be written to drills.jsonl ({type(exc).__name__}).")
        done += int(ok)
        synced = synced or bool(row["deleted"] and nightly_backup.is_synced(src))
    if synced:
        say("Empty the OneDrive recycle bin: OneDrive keeps deleted files there for a while.")
    say(f"{done} of {len(todo)} plain backups are now only encrypted."
        + ("" if done == len(todo) else " Each of the others is named above."))
    return 0 if done == len(todo) and not unrecorded else 1


# ── the backup before a release, sealed in the same run; unpacked again for a rollback ──────────────────

LABEL = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
LABEL_MAX = 40
RELEASE_TIMEOUT_S = 1800                 # the backup of every table takes about 5 minutes
_ADDRESS = re.compile(r"(?i)\b(?:postgres(?:ql)?|https?)://\S+")


class Unfinished(DrillError):
    """The backup did not complete: what it wrote is not a backup."""


def label_refusal(label: str) -> str | None:
    """Why `label` cannot name a release backup, or None. It becomes part of a folder name: lower-case letters and
    digits with single hyphens between them, at most LABEL_MAX characters."""
    if len(label) <= LABEL_MAX and LABEL.fullmatch(label):
        return None
    return (f"the label {label[:60]!r} is not lower-case letters and digits with single hyphens between them (such as "
            f"r9 or mi-v2), at most {LABEL_MAX} characters")


STORE_FIX = r"point YQ_BACKUP_DIR at a local folder outside it, or unset it (the default is %LOCALAPPDATA%\yq-backups)"


def inside_project(path: Path) -> str | None:
    """'<path> is inside <which>' when `path` (resolved: a link is followed) is the code folder (a public repository)
    or the project folder (the daily OneDrive copy picks it up), or lies inside one of them; None when it does not."""
    real = Path(path).resolve()
    for top, what in ((ROOT, "the code folder, a public repository"),
                      (paths.data_root(), "the project folder, which the daily OneDrive copy picks up")):
        base = Path(top).resolve()
        if real == base or base in real.parents:
            return f"{real} is inside {what}"
    return None


def store_refusal(folder: Path) -> str | None:
    """Why a plain backup may not be made in `folder` of the backup store, or None. backup_root() refuses a store that
    names OneDrive; this also catches one that only leads there (a link), and one inside the code or project folder
    (YQ_BACKUP_DIR pointed there, or a relative one, read from the folder this runs in)."""
    from scripts import nightly_backup
    real = Path(folder).resolve()
    if nightly_backup.is_synced(real):
        return f"{real} is inside a synced folder (OneDrive): {STORE_FIX}"
    inside = inside_project(real)
    return f"{inside}, where a plain backup is never made: {STORE_FIX}" if inside else None


def release_folder(label: str, day: str) -> Path:
    """Where a release's backup is made: <the backup store>\\release\\<day>_pre-<label>, as an absolute path (the backup
    child runs in the code folder: a relative store would be another place to it), outside OneDrive and outside the
    project folder, so the daily OneDrive copy never sees the plain copy. Raises SystemExit for a store that names
    OneDrive (scripts.nightly_backup's rule) and DrillError for one that store_refusal refuses."""
    folder = Path(os.path.abspath(backup_root() / "release" / f"{day}_pre-{label}"))
    refused = store_refusal(folder)
    if refused:
        raise DrillError(refused)
    return folder


def backup_argv(folder: Path) -> list[str]:
    """The release's backup: every public table, in one read-only REPEATABLE READ snapshot, into `folder`."""
    return [sys.executable, "-X", "utf8", "-m", "scripts.db_backup", "--all", "--out", str(folder)]


def child_words(text: str) -> str:
    """The last line a child printed, for the screen and drills.jsonl: one line, any web or database address
    replaced, at most 200 characters."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return _ADDRESS.sub("<address>", lines[-1])[:200] if lines else "it printed nothing"


def take_backup(folder: Path) -> None:
    """scripts.db_backup --all into `folder`, as a child in the code folder (it reads the database address from its
    environment, else from that folder's .env; the backup password is not in its environment). Raises Unfinished when
    the backup did not complete (it failed, was stopped or did not start: what it wrote is not a backup), and
    DrillError when it completed but its own check of the files against the manifest failed."""
    try:
        r = run(backup_argv(folder), env=plain_env(), timeout=RELEASE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise Unfinished(f"it was stopped after {RELEASE_TIMEOUT_S // 60} minutes") from None
    except OSError as exc:
        raise Unfinished(f"it could not be started ({type(exc).__name__})") from None
    out, err = r.stdout or "", r.stderr or ""
    done = [ln for ln in out.splitlines() if ln.startswith("Backup complete:")]
    if not done:
        raise Unfinished(f"exit {r.returncode}: {child_words(err if err.strip() else out)}")
    if r.returncode != 0 or "verify OK" not in done[-1]:
        raise DrillError("the backup's own check found files that do not match its manifest")


def backup_size(folder: Path) -> tuple[int, int, int]:
    """(the tables its manifest lists, its files, their bytes) of a plain backup folder."""
    tables = manifest_tables((folder / "manifest.json").read_text(encoding="utf-8"))
    found = [p for p in folder.rglob("*") if p.is_file()]
    return tables, len(found), sum(p.stat().st_size for p in found)


def _release_there(folder: Path, archive: Path, label: str) -> str | None:
    """Why a release backup is refused because one of that name is already there, with what to do; or None."""
    if os.path.lexists(archive):
        also = (f" Its plain folder {folder.name} is next to it, which that run could not delete: delete it by hand."
                if os.path.lexists(folder) else "")
        return (f"{archive} is already there: this release was backed up today. Check that archive with python -m "
                f"scripts.yq_backup drill --archive <it>, or give this backup another label (such as {label}-2).{also}")
    if os.path.lexists(folder):
        return (f"{folder} is already there. If it is a whole backup (python -m scripts.db_backup --verify <it>), "
                "python -m scripts.yq_backup pack <it> --apply encrypts it; if a stopped run left it, delete it; or "
                f"give this backup another label (such as {label}-2).")
    return None


def _close(row: dict, t0: float, last: str, say) -> None:
    """The end of a release or an unpack: its row in drills.jsonl (one that cannot be written is said and changes
    nothing else: the archive or the folder is what the run is for), then its last line."""
    row.setdefault("seconds", round(time.monotonic() - t0, 1))
    try:
        record(row)
    except OSError as exc:
        say(f"  WARNING: the row of this run could not be written to business_data/ops/drills.jsonl "
            f"({type(exc).__name__}).")
    say(last)


def _stopped(exc: BaseException) -> str:
    """Why a run ended on what nothing catches on purpose: Ctrl+C, or an unexpected error by its class name."""
    if isinstance(exc, KeyboardInterrupt):
        return "it was stopped (Ctrl+C)"
    return f"it stopped on an unexpected {type(exc).__name__}"


def _remove_unfinished(folder: Path, row: dict) -> str:
    """Remove what a backup that did not complete wrote (it is not a backup): the words for what is left."""
    left = remove_plain(folder)
    if left:
        row["left"] = len(left)
        return (f"What it wrote could NOT all be removed ({left_words(folder, left)}): delete {folder} by hand, it "
                "holds part of the backup unencrypted.")
    return "What it wrote was removed: nothing is left plain."


def _release_left(folder: Path, archive: Path, stage: str, stopped: bool = False) -> str:
    """What a release that ended in `stage` (check, seal or delete) leaves, and what to do with it: the words after its
    reason. A whole backup that a stop left is for `pack <it> --apply` (which checks it first); one whose files did not
    match their manifest is kept for a look."""
    left = _still_there(folder)
    if stage == "delete":
        if not left:
            return f"The encrypted copy {archive} is made and read back, and the plain folder is deleted."
        return (f"The encrypted copy {archive} is made and read back, but the plain folder could NOT all be deleted "
                f"({left_words(folder, left)}): delete {folder} by hand.")
    if not left:
        return "Nothing is left plain."
    if stopped:
        return (f"The plain folder {folder} is kept (unencrypted, outside OneDrive): python -m scripts.yq_backup pack "
                f"{folder} --apply checks it against its manifest, encrypts it and then deletes it.")
    if stage == "seal":
        return (f"The plain folder {folder} is kept (unencrypted, outside OneDrive), a whole backup: once the cause "
                f"is put right, python -m scripts.yq_backup pack {folder} --apply encrypts it and then deletes it.")
    return (f"The plain folder {folder} is kept (unencrypted, outside OneDrive) for a look (python -m "
            f"scripts.db_backup --verify {folder}); delete it before running this again.")


def _unpacked_gone(made: Path | None, existed: bool, row: dict) -> str:
    """Remove the copy that an unpack which failed or was stopped made (an empty folder named with --to is left as it
    was): the words for what is left."""
    if made is None:
        return "Nothing was written."
    row["folder"] = made.name
    left = remove_plain(made)
    if left:
        row["left"] = len(left)
        return (f"The unpacked copy could NOT all be removed ({left_words(made, left)}): delete {made} by hand, it "
                "holds the backup unencrypted.")
    if existed:
        with contextlib.suppress(OSError):
            made.mkdir()                             # the empty folder that was named is left as it was
    return "The unpacked copy was removed."


def release(label: str, say=print, today: str | None = None) -> int:
    """The backup before a release (the module's docstring says how). Exit 0: only the encrypted copy is left and the
    last line is its path; 1: it did not finish (the last line says what is left plain); 2: refused, nothing made."""
    t0 = time.monotonic()
    refused = label_refusal(label)
    if refused:
        say(f"Refused, nothing was made: {refused}.")
        return 2
    password, source = yq_secrets.backup_password()
    exe = find_7z()
    missing = _missing(password, exe)
    if missing:
        say(f"Refused, nothing was made: {missing}. A release backup is only ever made encrypted: none was taken, so "
            "nothing is left plain.")
        return 2
    try:
        folder = release_folder(label, today or datetime.now().strftime("%Y-%m-%d"))
    except SystemExit as exc:                        # scripts.nightly_backup refuses a store inside OneDrive
        say(f"Refused, nothing was made: {exc}")
        return 2
    except (DrillError, OSError) as exc:             # a store inside the project folder, or one that cannot be read
        say(f"Refused, nothing was made: {exc}.")
        return 2
    archive = folder.with_name(folder.name + ".7z")
    there = _release_there(folder, archive, label)
    if not there:
        try:
            folder.parent.mkdir(parents=True, exist_ok=True)
            folder.mkdir()                           # this run's own: another run started with the label is refused
        except OSError as exc:
            there = _release_there(folder, archive, label) or f"{folder} could not be made ({type(exc).__name__})."
    if there:
        say(f"Refused, nothing was made: {there}")
        return 2
    row: dict = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": "release", "label": label,
                 "source": folder.name, "archive": archive.name, "deleted": False, "password_from": source}
    say(f"Release backup {folder.name}: every table of the live database (only read) into {folder.parent} (outside "
        f"OneDrive), checked against its manifest, packed with 7-Zip (AES-256, names hidden; the password from "
        f"{source}), read back with the password, and only then the plain folder deleted.")
    steps = [0]

    def line(text: str) -> None:
        steps[0] += 1
        say(f"  {steps[0]}. {text}")

    stage = "backup"                                 # then check, seal and delete: what is left depends on it
    try:
        take_backup(folder)
        stage = "check"
        tables, files, size = backup_size(folder)
        row.update(tables=tables, files=files)
        line(f"Backed up every table (scripts.db_backup --all, read only): {_n(tables, 'table')}, {_n(files, 'file')}, "
             f"{size_words(size)}")
        if not tables:
            raise DrillError("the backup lists no table")
        verify_folder(folder)
        line("Every file matches its manifest (rows and columns)")
        stage = "seal"
        said = seal(folder, archive, password, exe, note=line)
        stage = "delete"
        try:
            packed = size_words(archive.stat().st_size)
        except OSError:                              # only for the line below: never a reason to undo the seal
            packed = "its size unknown"
        line(f"Packed {archive.name} ({packed}) and read it back with the password ({said})")
        left = remove_plain(folder)
    except Unfinished as exc:
        end = _remove_unfinished(folder, row)
        if "left" not in row:
            end += " Run this again once the cause is put right."
        row.update(result="failed", why=f"the backup did not complete ({exc})"[:300])
        _close(row, t0, f"RELEASE BACKUP NOT DONE: the backup did not complete ({exc}). {end}", say)
        return 1
    except (DrillError, OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as exc:
        why = (str(exc) or type(exc).__name__).rstrip(".")
        row.update(result="failed", why=why[:300])
        _close(row, t0, f"RELEASE BACKUP NOT DONE: {why}. {_release_left(folder, archive, stage)}", say)
        return 1
    except BaseException as exc:                     # Ctrl+C, or an error nobody expected: said, recorded, raised again
        with contextlib.suppress(Exception):         # best effort: nothing here may stand in for the stop itself
            why = _stopped(exc)
            end = (_remove_unfinished(folder, row) if stage == "backup"      # a stopped backup is not a backup
                   else _release_left(folder, archive, stage, stopped=True))
            row.update(result="stopped", stage=stage, why=why, deleted=stage == "delete" and not _still_there(folder))
            _close(row, t0, f"RELEASE BACKUP NOT DONE: {why}. {end}", say)
        raise
    row.update(result="packed", deleted=not left)
    if left:
        row["left"] = len(left)
        line(f"The plain folder could NOT be deleted: {left_words(folder, left)}. Close what holds it (an Explorer "
             "window, say), then delete it by hand: the encrypted copy is whole.")
        _close(row, t0, f"RELEASE BACKUP NOT DONE: the encrypted copy {archive} is made and read back, but the plain "
                        "folder is still there (see above).", say)
        return 1
    line("Deleted the plain folder")
    row["seconds"] = round(time.monotonic() - t0, 1)
    say(f"  Done in {row['seconds']} s: only the encrypted copy is left.")
    _close(row, t0, str(archive), say)
    return 0


def unpack_target(to) -> Path:
    """The folder named with --to, to unpack a backup into: new or empty, outside OneDrive and outside the project
    folder (the code folder is a public repository, and the daily OneDrive copy picks the project folder up). Raises
    DrillError with the reason it is refused."""
    from scripts import nightly_backup
    dest = Path(to).expanduser().resolve()
    if nightly_backup.is_synced(dest):
        raise DrillError(f"{dest} is inside a synced folder (OneDrive), and the unpacked backup is unencrypted: name a "
                         "folder on this disk only")
    inside = inside_project(dest)
    if inside:
        raise DrillError(f"{inside}: name a folder outside it")
    if dest.exists() and not dest.is_dir():
        raise DrillError(f"{dest} is a file, not a folder")
    if dest.is_dir() and any(dest.iterdir()):
        raise DrillError(f"{dest} is not empty: name a new folder, or an empty one")
    return dest


def new_unpack_folder(stamp: str) -> Path:
    """unpack's own place when no --to is named: a new folder in the backup store, unpack_<stamp>_<n>, as an absolute
    path (the --verify check runs in the code folder: a relative store would be another place to it)."""
    for n in range(1, 100):
        try:
            return Path(os.path.abspath(scratch_dir(stamp, n, kind="unpack")))
        except FileExistsError:
            continue
    raise DrillError("no new folder could be made in the backup store")


def unpacked_backup(top: Path) -> Path:
    """The folder of an unpacked archive that holds its manifest.json: its top, or the one folder below it that does."""
    try:
        return backup_folder(top)
    except DrillError:
        raise DrillError("the archive holds no manifest.json at its top or one folder down: it is not a backup") \
            from None


def unpack_backup(archive: Path, to: Path | None = None, say=print) -> int:
    """A backup archive -> a new, empty folder, checked against its manifest, for scripts.db_backup --restore (the
    module's docstring says how). Exit 0: the last line is the folder; 1: it did not unpack whole (the last line says
    what is left); 2: refused, nothing was written."""
    t0 = time.monotonic()
    archive = Path(os.path.abspath(archive))
    encrypted = archive.suffix.lower() == ".7z"
    if not (encrypted or archive.suffix.lower() == ".zip") or not archive.is_file():
        say(f"Refused, nothing was written: {archive} is not a backup archive on this PC (a .7z, or a plain .zip).")
        return 2
    password, source = yq_secrets.backup_password() if encrypted else (None, None)
    exe = find_7z() if encrypted else None
    missing = _missing(password, exe) if encrypted else ""
    if missing:
        say(f"Refused, nothing was written: {missing}.")
        return 2
    try:
        dest = unpack_target(to) if to is not None else None
        if dest is None:                             # the store, refused here before anything: inside OneDrive (by
            refused = store_refusal(Path(os.path.abspath(backup_root())))      # name or by a link) or the project
            if refused:
                raise DrillError(refused)
    except SystemExit as exc:
        say(f"Refused, nothing was written: {exc}")
        return 2
    except (DrillError, OSError) as exc:
        say(f"Refused, nothing was written: {exc}.")
        return 2
    row: dict = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": "unpack",
                 "archive": archive.name, "encrypted": encrypted, "password_from": source}
    where = str(dest) if dest is not None else "a new folder in the backup store (outside OneDrive)"
    say(f"Unpack {archive.name} into {where}, checked against its manifest, for a restore.")
    steps = [0]

    def line(text: str) -> None:
        steps[0] += 1
        say(f"  {steps[0]}. {text}")

    made: Path | None = None
    existed = False
    try:
        ok, said = read_back(archive, password, exe)
        line(f"Read back {archive.name}{' with the password' if encrypted else ''}: {said}")
        if not ok:
            raise DrillError("the archive does not read back whole"
                             + (" (a wrong password reads the same way)" if encrypted else ""))
        if dest is None:
            made = new_unpack_folder(datetime.now().strftime("%Y%m%d_%H%M%S"))
        else:
            existed = dest.is_dir()
            dest.mkdir(parents=True, exist_ok=True)
            made = dest
        unpack(archive, made, password, exe)
        folder = unpacked_backup(made)
        tables, files, _size = backup_size(folder)
        line(f"Unpacked {_n(files, 'file')} ({_n(tables, 'table')}) into {made}")
        if not tables:
            raise DrillError("its manifest.json lists no table: it is not a backup")
        verify_folder(folder)
        line("Every file matches its manifest (rows and columns)")
    except (DrillError, OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile,
            subprocess.TimeoutExpired) as exc:
        why = (str(exc) or type(exc).__name__).rstrip(".")
        row.update(result="failed", why=why[:300])
        _close(row, t0, f"UNPACK FAILED: {why}. {_unpacked_gone(made, existed, row)}", say)
        return 1
    except BaseException as exc:                     # Ctrl+C, or an error nobody expected: said, recorded, raised again
        with contextlib.suppress(Exception):         # best effort: nothing here may stand in for the stop itself
            why = _stopped(exc)
            row.update(result="stopped", why=why)
            end = _unpacked_gone(made, existed, row)                 # never left unencrypted by a stop
            _close(row, t0, f"UNPACK FAILED: {why}. {end}", say)
        raise
    row.update(result="unpacked", folder=made.name, tables=tables, files=files)
    say(f"  The restore: python -m scripts.db_backup --restore {folder} --tables <t1,t2,...> (a dry run; --yes applies "
        f"it). Delete {made} once it is done: it holds the backup unencrypted.")
    _close(row, t0, str(folder), say)
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="python -m scripts.yq_backup", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("drill", help="restore a pre-load archive into a throwaway local PostgreSQL")
    d.add_argument("--archive", help="the archive to drill (default: the newest pre-load archive)")
    d.add_argument("--keep", action="store_true", help="keep the unpacked files and the cluster")
    lg = sub.add_parser("legacy", help="encrypt the plain backups under business_data/backups")
    lg.add_argument("--apply", action="store_true", help="do it (without it: say what would be done)")
    pk = sub.add_parser("pack", help="encrypt plain backups already on this PC (a pre-load .zip, or a folder holding "
                                     "manifest.json): each becomes <name>.7z next to it")
    pk.add_argument("paths", nargs="+", metavar="path", help="a plain pre-load .zip or a backup folder")
    pk.add_argument("--apply", action="store_true", help="do it (without it: say what would be done)")
    rl = sub.add_parser("release", help="back up every table before a release, sealed encrypted in the backup store's "
                                        "release folder; the archive's path is the last line")
    rl.add_argument("label", help="the release, in lower-case letters and digits with single hyphens between (r9, "
                                  "mi-v2)")
    up = sub.add_parser("unpack", help="decrypt a backup archive into a new, empty folder and check it, for "
                                       "scripts.db_backup --restore; the folder is the last line")
    up.add_argument("archive", help="the .7z (or a plain .zip) to unpack")
    up.add_argument("--to", help="a new or empty folder, outside OneDrive and the project folder (default: a new "
                                 "folder in the backup store)")
    args = ap.parse_args(argv)
    if args.cmd == "drill":
        return drill(Path(args.archive) if args.archive else None, args.keep)
    if args.cmd == "pack":
        return pack([Path(p) for p in args.paths], args.apply)
    if args.cmd == "release":
        return release(args.label)
    if args.cmd == "unpack":
        return unpack_backup(Path(args.archive), Path(args.to) if args.to else None)
    return legacy(args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
