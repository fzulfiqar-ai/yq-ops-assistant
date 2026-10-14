"""python -m scripts.yq_backup : proves a backup can be restored, and puts old plain backups away encrypted.

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
        then is the plain folder deleted. Without --apply it only says what it would do. Afterwards,
        empty the OneDrive recycle bin: OneDrive keeps deleted files there for a while.

Never writes to production: the restore's database address is forced to a loopback address the drill
made, and any other address is refused before anything runs. The backup password comes from
scripts/yq_secrets.py and is never printed (7-Zip gets it on its command line, as scripts/nightly_backup.py
always has: a known limit on a PC that other people use).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
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
    the password (headers are encrypted: a wrong password cannot even list it)."""
    if archive.suffix == ".zip":
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
    if archive.suffix == ".zip":
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


# ── the old plain backups ────────────────────────────────────────────────────────────────────────

def legacy_folders() -> list[Path]:
    base = paths.data_root() / "business_data" / "backups"
    return sorted(p for p in base.iterdir() if p.is_dir() and (p / "manifest.json").is_file()) if base.is_dir() else []


def pack_legacy(folder: Path, dest_dir: Path, password: str, exe: str) -> Path:
    """One plain backup folder -> <dest>/<name>.7z, read back, its file count compared. Raises on any doubt."""
    from scripts import nightly_backup
    dest_dir.mkdir(parents=True, exist_ok=True)
    archive = dest_dir / f"{folder.name}.7z"
    if archive.exists():
        archive.unlink()
    r = subprocess.run(nightly_backup.seven_zip_cmd(exe, archive, folder, password), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0 or not archive.is_file():
        raise DrillError(f"7-Zip could not pack {folder.name}")
    ok, said = read_back(archive, password, exe)
    expected = sum(1 for p in folder.rglob("*") if p.is_file())
    if not ok or f"Files: {expected}" not in said:
        raise DrillError(f"{folder.name}: the archive does not read back with all {expected} files ({said})")
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
            say(f"  would pack, test and then delete: {f.name}")
        say("Dry run: nothing was changed. Run with --apply to do it.")
        return 0
    done = 0
    for f in folders:
        try:
            archive = pack_legacy(f, dest, password, exe)
        except DrillError as exc:
            say(f"  KEPT {f.name}: {exc}")
            continue
        shutil.rmtree(f, ignore_errors=True)
        gone = not f.exists()
        say(f"  {f.name}: packed and read back ({archive.name}); plain folder " + ("deleted" if gone else
                                                                                   "could NOT be deleted"))
        done += int(gone)
    record({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "legacy_packed": done,
            "folders": len(folders)})
    say(f"{done} of {len(folders)} folders are now only in the encrypted store. Empty the OneDrive recycle bin.")
    return 0 if done == len(folders) else 1


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
    args = ap.parse_args(argv)
    if args.cmd == "drill":
        return drill(Path(args.archive) if args.archive else None, args.keep)
    return legacy(args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
