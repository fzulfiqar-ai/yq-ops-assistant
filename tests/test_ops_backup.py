"""Backups: the encryption rule of the load's backup step, the read-back, the restore drill's guards, the
packing of old plain backups and the backup before a release (scripts/yq_load_go.py, scripts/yq_backup.py,
scripts/yq_secrets.py), and the snapshot itself (scripts/db_backup.py).

    python -m tests.test_ops_backup

Same lightweight runner as the other suites (no pytest). No database: SUPABASE_URL, SUPABASE_KEY and
DATABASE_URL are pinned to ci.invalid before any import; the restore drill itself (a throwaway PostgreSQL) is
run by hand, never here: its guards are tested instead, and the release's backup child is a stand-in that
writes invented files (scripts.db_backup --all never runs here). No real secret: YQ_SECRETS_OFF=1. The tests
that pack folders of invented files with 7-Zip and read them back print SKIP where 7-Zip is not installed.

Covered:
  * the secrets are read from nowhere while YQ_SECRETS_OFF=1 (tests can never read this PC's);
  * the rule: once a password exists 7-Zip must be there, and once a backup was encrypted a plain one is
    refused; the day encryption began is kept in business_data/ops/backup.json, not in state.json;
  * the box says when the backup will NOT be encrypted, and what goes out by e-mail after the load;
  * read-back: a whole zip passes, a damaged one does not; a 7z needs its password;
  * the drill: the database address must be on this PC (refused otherwise), the restore child gets that
    address under both names and never the password; the restore is applied (--yes) but never with
    --allow-newer; the newest pre-load archive is the one drilled;
  * old backups: without a password and 7-Zip nothing is changed; a dry run lists and changes nothing;
    with them, each folder is packed, read back with all its files, and only then deleted;
  * pack (plain backups already on this PC, a pre-load .zip or a folder with manifest.json): a dry run
    says what it would do and changes nothing; a folder and a zip each become a .7z next to them in the
    drill's layout (manifest.json at the top, never a zip inside), read back with the password and
    dated like the source (an old backup still sorts as old), and only then is the plain copy deleted;
    without the password or 7-Zip, with a .7z already there or for anything that is not a plain
    backup it refuses and touches nothing; an archive that does not read back is removed and the plain
    copy kept; a plain copy that cannot be deleted is reported (never claimed) and the exit code says so;
  * release (the backup before a release): a label that is not lower-case letters and digits with single
    hyphens between them, no backup password or no 7-Zip, a backup store (YQ_BACKUP_DIR) inside the project
    or the code folder or a relative one, or that release's folder or .7z already there is refused before
    anything (exit 2: nothing made, nothing touched, no backup started); the backup child is
    `scripts.db_backup --all --out <store>\\release\\<day>_pre-<label>` with no password in its environment
    (nor in the check's); the folder is checked, sealed while it is still there and only then deleted, and
    the archive's path is the last line; a backup that does not complete (it fails, is stopped, Ctrl+C) is
    removed, never left plain; one that does not match its manifest, lists no table, or cannot be sealed (7-Zip
    does not start, the archive does not read back) is kept and named, with no archive or .partial left; a
    plain folder that cannot be deleted is reported, never claimed; a stop (Ctrl+C, or an error nobody
    expected) in any stage is raised again after a last line that says what is left plain and what to do with
    it; one row in drills.jsonl each time, no secret in it;
  * unpack (a rollback's first step): only into a new or empty folder (by default a new one in the backup
    store, refused like release's store), never inside OneDrive or the project folder, and never for what is
    not a backup archive or for a .7z without the password and 7-Zip (exit 2, nothing written); the files are
    checked against their manifest and the folder holding manifest.json is the last line; a damaged archive,
    a wrong password, files that do not match, no manifest or a stop (Ctrl+C) leave nothing behind (exit 1, or
    the stop raised again), and what cannot be removed is named; with 7-Zip, release then unpack gives back
    every file byte for byte;
  * the snapshot (scripts/db_backup.py, on a stand-in connection over invented tables): the columns of every
    table come from one catalog query and every count and max from one statement, before one COPY per table,
    all in one read-only REPEATABLE READ transaction; the CSVs, manifest.json and the printed lines are exactly
    what the per-table queries wrote; a table name is quoted as an identifier in every statement.

The backup store is a temporary folder for the whole run (YQ_BACKUP_DIR, pinned below): no test reads or
writes this PC's real store.
"""
from __future__ import annotations

import contextlib
import csv
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import traceback
import zipfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"
os.environ.pop("YQ_MAIN_CHECKOUT", None)
os.environ["YQ_SECRETS_OFF"] = "1"
STORE = tempfile.mkdtemp(prefix="yq-test-backups-")
os.environ["YQ_BACKUP_DIR"] = STORE       # never this PC's %LOCALAPPDATA%\yq-backups

from scripts.ops import db  # noqa: E402,F401
from scripts.ops import config, paths, status  # noqa: E402
from scripts import yq_backup as yb  # noqa: E402
from scripts import yq_load_go as yq  # noqa: E402
from scripts import yq_secrets  # noqa: E402

import dotenv  # noqa: E402
_load_dotenv = dotenv.load_dotenv
dotenv.load_dotenv = lambda *a, **k: False      # scripts.db_backup reads the project's .env on import: never here
try:
    from scripts import db_backup  # noqa: E402
finally:
    dotenv.load_dotenv = _load_dotenv

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


class data_root:
    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        paths.set_data_root(self._tmp.name)
        return paths.data_root()

    def __exit__(self, *exc):
        paths.set_data_root(None)
        self._tmp.cleanup()
        return False


def backup_files(rows: int = 2) -> dict[str, str]:
    """The invented files of one plain backup: a table and the manifest that lists it."""
    return {"orders.csv": "id,total\n" + "".join(f"{i},1.500\n" for i in range(rows)),
            "manifest.json": json.dumps({"tables": {"orders": {"rows": rows}}})}


def plain_backup(folder: Path, rows: int = 2) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in backup_files(rows).items():
        (folder / name).write_text(text, encoding="utf-8")
    return folder


def plain_zip(path: Path, rows: int = 2, inner: str = "") -> Path:
    """A plain pre-load zip as the load made them before encryption: the files at its top (or, with `inner`,
    one folder down)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in backup_files(rows).items():
            z.writestr(inner + name, text)
    return path


PW = "an-invented-test-password"     # the only password any test hands in (YQ_SECRETS_OFF=1: none is ever read)
OLD = time.time() - 30 * 86400       # the date of an old backup


class pack_world:
    """A temporary folder holding a data root of its own (`project`) and, beside it, a backup store (`store`): never
    inside the project folder, as %LOCALAPPDATA%\\yq-backups is not. The test password (or none), 7-Zip as found here
    (or a stand-in name, or none), and the waits of the delete set to nothing. Yields the temporary folder."""

    def __init__(self, password: str | None = PW, exe: str | None = "found"):
        self.password, self.exe = password, exe

    def __enter__(self) -> Path:
        self._root = data_root()
        root = self._root.__enter__()
        paths.set_data_root(root / "project")
        self._real = (yq_secrets.backup_password, yb.find_7z, yb.backup_root, yb.DELETE_WAIT_S)
        pw, exe = self.password, self.exe
        yq_secrets.backup_password = lambda env=None: (pw, "process_environment") if pw else (None, None)
        if exe != "found":
            yb.find_7z = lambda: exe
        yb.backup_root = lambda: root / "store"
        yb.DELETE_WAIT_S = 0
        return root

    def __exit__(self, *exc):
        yq_secrets.backup_password, yb.find_7z, yb.backup_root, yb.DELETE_WAIT_S = self._real
        return self._root.__exit__(*exc)


def snapshot(top: Path) -> dict:
    """Every folder and file under `top`, a file with its bytes and its date: what a refusal must leave alone."""
    return {str(p.relative_to(top)): (None if p.is_dir() else (p.read_bytes(), p.stat().st_mtime_ns))
            for p in sorted(top.rglob("*"))}


def rows_written() -> list[dict]:
    f = paths.ops_dir() / "drills.jsonl"
    return [json.loads(ln) for ln in f.read_text(encoding="utf-8").splitlines()] if f.is_file() else []


def unpacked(archive: Path, exe: str) -> dict[str, bytes]:
    """The files of a .7z as the drill unpacks it (yq_backup.unpack), by their path inside it."""
    with tempfile.TemporaryDirectory() as tmp:
        yb.unpack(archive, Path(tmp), PW, exe)
        return {p.relative_to(tmp).as_posix(): p.read_bytes() for p in Path(tmp).rglob("*") if p.is_file()}


def listing(archive: Path, exe: str, password: str | None) -> str:
    """What 7-Zip lists of an archive with this password, or with none: stdin is closed, so a prompt for one gets
    nothing (a 7-Zip that would wait for it anyway is stopped after 20 s, and has listed nothing)."""
    argv = [exe, "l", *([f"-p{password}"] if password is not None else []), str(archive)]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, timeout=20)
    except subprocess.TimeoutExpired:
        return ""
    return (r.stdout or "") + (r.stderr or "")


def names_hidden(archive: Path, exe: str) -> None:
    """The headers are encrypted too (-mhe=on): the right password lists the names, a wrong one or none lists none."""
    assert "manifest.json" in listing(archive, exe, PW), "the right password lists the names"
    for password in ("a wrong password", None):
        shown = listing(archive, exe, password)
        assert "manifest.json" not in shown and "orders.csv" not in shown, f"names listed with {password!r}"


@test("the secrets are read from nowhere while YQ_SECRETS_OFF=1")
def _():
    assert yq_secrets.backup_password({"YQ_BACKUP_PASSWORD": "from-env"}) == (None, None)
    assert yq_secrets.resend_key() is None and yq_secrets.read_credential("YQ_RESEND_API_KEY") is None
    os.environ["YQ_SECRETS_OFF"] = "0"
    try:
        found, source = yq_secrets.backup_password({"YQ_BACKUP_PASSWORD": "from-env"})
        # with the switch off, the environment answers last (Credential Manager or the registry may answer first)
        assert found and source in ("credential_manager", "user_environment", "process_environment")
    finally:
        os.environ["YQ_SECRETS_OFF"] = "1"


@test("the rule: a password needs 7-Zip; once encrypted, a plain backup is refused; the day is kept apart")
def _():
    assert yq.archive_refusal(None, None, None) is None, "no password yet: a plain backup with its warning"
    assert yq.archive_refusal("pw", "7z.exe", None) is None and yq.archive_refusal("pw", "7z.exe", "2026-10-15") is None
    assert "7-Zip is not installed" in yq.archive_refusal("pw", None, None)
    said = yq.archive_refusal(None, "7z.exe", "2026-10-15")
    assert said and "no longer found" in said and "2026-10-15" in said
    with data_root():
        assert yq.backup_marker() is None
        yq.mark_encrypted("2026-10-15")
        assert yq.backup_marker() == "2026-10-15"
        assert (paths.ops_dir() / "backup.json").is_file() and not paths.state_file().exists()


@test("the box says when the backup will NOT be encrypted, and what goes out by e-mail after the load")
def _():
    with data_root():
        notes = yq._real_box_notes()
        assert notes[0].startswith("ATTENTION: the backup before this load will NOT be encrypted")
        assert notes[-1] == "No e-mail after this load (delivery is off)."
        yq.mark_encrypted("2026-10-15")
        notes = yq._real_box_notes()
        assert notes[0].startswith("ATTENTION: the backup password is no longer found"), notes
        paths.write_text(paths.ops_dir() / "delivery.json", json.dumps({"mode": "on", "to": ["a@b.invalid", "c@d.invalid"]}))
        said = yq.delivery_words()
        assert said.startswith("Afterwards: the brief by e-mail to 2 address(es), with the team message"), said
        assert "a short note with no figure instead when a check needs a look" in said, "notes go out too: it says so"
        paths.write_text(paths.ops_dir() / "delivery.json", json.dumps({"mode": "test", "test_to": ["a@b.invalid"]}))
        assert "a TEST copy of the brief by e-mail to 1 address(es)" in yq.delivery_words()
    with data_root():
        title, text = yq.box_text("151026_0900", {"drop": "Uplaods/151026", "verdicts": []}, notes=["ATTENTION: x."])
        lines = text.splitlines()
        assert "ATTENTION: x." in lines and lines[-1] == "Yes = load now. No = nothing changes."


@test("the rule fails closed: a marker that cannot be read, or an encrypted archive in the store, means encryption began")
def _():
    with data_root() as root:
        store = root / "store" / "preload"
        store.mkdir(parents=True)
        assert yq.backup_marker(store) is None
        (store / "yq-backup_2026-10-15_pre-data-151026_0900.7z").write_bytes(b"7z")
        assert yq.backup_marker(store) == "2026-10-15", "an encrypted archive says so, whatever the marker"
        assert yq.archive_refusal(None, "7z.exe", yq.backup_marker(store)), "no password now: a plain one is refused"
        paths.write_text(paths.ops_dir() / "backup.json", "{ a broken copy")
        assert yq.backup_marker() == yq.MARKER_UNREADABLE and "cannot be read" in yq.MARKER_UNREADABLE
        assert "no longer found" in yq.archive_refusal(None, "7z.exe", yq.backup_marker())
        paths.write_text(paths.ops_dir() / "backup.json", json.dumps({"encrypted_since": ""}))
        assert yq.backup_marker() == yq.MARKER_UNREADABLE, "a marker without its day still counts"


@test("the worker is the one process handed the backup password; the engine programs it starts never are")
def _():
    real = dict(yq.LAUNCH_ENV)
    yq.LAUNCH_ENV[yq.BACKUP_PASSWORD_ENV] = "an-invented-password"
    seen = {}

    class Started:
        pid, breakaway = 4242, True

    real_popen = yq.subprocess.Popen

    def popen(argv, **kw):
        seen["env"] = kw.get("env")
        return Started()
    yq.subprocess.Popen = popen
    try:
        with data_root() as root:
            yq.spawn_worker("151026_0900", root, "a-word", root / "worker.log")
        assert seen["env"][yq.BACKUP_PASSWORD_ENV] == "an-invented-password", "the worker packs: it gets it"
        assert yq.BACKUP_PASSWORD_ENV not in yq.child_env(), "an engine child never does"
    finally:
        yq.subprocess.Popen = real_popen
        yq.LAUNCH_ENV.clear()
        yq.LAUNCH_ENV.update(real)
    assert yq.BACKUP_PASSWORD_ENV not in yq.child_env()


@test("read-back: a whole zip passes (.ZIP too), a damaged one does not, a 7z needs 7-Zip and its password")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = plain_backup(tmp / "b")
        good = tmp / "good.zip"
        with zipfile.ZipFile(good, "w", zipfile.ZIP_DEFLATED) as z:
            for f in src.iterdir():
                z.write(f, f.name)
        ok, said = yb.read_back(good, None, None)
        assert ok and "2 files" in said
        bad = tmp / "bad.zip"
        bad.write_bytes(good.read_bytes()[:40])
        assert yb.read_back(bad, None, None)[0] is False
        sz = tmp / "x.7z"
        sz.write_bytes(b"7z")
        assert yb.read_back(sz, None, None) == (False, "a 7z archive needs 7-Zip and the backup password to be read back")
        upper = plain_zip(tmp / "OLD-BACKUP.ZIP")                 # a .ZIP is a zip too, whatever the case
        assert yb.read_back(upper, None, None) == (True, "2 files, all whole")
        yb.unpack(upper, tmp / "out", None, None)
        assert sorted(p.name for p in (tmp / "out").iterdir()) == ["manifest.json", "orders.csv"]


@test("the drill: a database address off this PC is refused; the child gets the drill's address twice, no password")
def _():
    for dsn, ok in (("postgresql://postgres@127.0.0.1:5433/x", True), ("postgresql://postgres@localhost:5433/x", True),
                    ("postgresql://u:p@db.example.invalid:5432/postgres", False), ("not a dsn", False)):
        assert yb.is_loopback(dsn) is ok, dsn
    env = yb.drill_env({"DATABASE_URL": "postgresql://u:p@prod.invalid/db", "YQ_BACKUP_PASSWORD": "pw", "PATH": "x"},
                       "postgresql://postgres@127.0.0.1:5433/yq_restore_drill")
    assert env["DATABASE_URL"] == env["YQ_DRILL_PG"] == "postgresql://postgres@127.0.0.1:5433/yq_restore_drill"
    assert "YQ_BACKUP_PASSWORD" not in env and env["PATH"] == "x"
    try:
        yb.drill_env({}, "postgresql://u:p@prod.invalid:5432/db")
        raise AssertionError("an address off this PC was accepted")
    except yb.DrillError:
        pass
    argv = yb.restore_argv(Path("x"), ["a", "b"])
    assert "--yes" in argv and "--allow-newer" not in argv and argv[argv.index("--dsn-env") + 1] == "YQ_DRILL_PG"
    assert argv[argv.index("--tables") + 1] == "a,b" and "scripts.db_backup" in argv
    with tempfile.TemporaryDirectory() as tmp:
        pre = Path(tmp) / "preload"
        pre.mkdir()
        old, new = pre / "yq-backup_2026-10-11_a.zip", pre / "yq-backup_2026-10-14_b.zip"
        old.write_bytes(b"1")
        time.sleep(0.05)
        new.write_bytes(b"2")
        (pre / "notes.txt").write_text("x", encoding="utf-8")
        assert yb.newest_archive(Path(tmp)) == new


@test("old plain backups: nothing changes without a password and 7-Zip; a dry run only lists")
def _():
    with data_root() as root:
        folder = plain_backup(root / "business_data" / "backups" / "2026-09-24_test")
        said = []
        assert yb.legacy(True, say=said.append) == 2 and folder.is_dir() and "nothing was changed" in said[-1]
        real_pw, real_7z, real_root = yq_secrets.backup_password, yb.find_7z, yb.backup_root
        yq_secrets.backup_password = lambda env=None: ("pw", "process_environment")
        yb.find_7z = lambda: "7z.exe"
        yb.backup_root = lambda: root / "store"
        try:
            said = []
            assert yb.legacy(False, say=said.append) == 0 and folder.is_dir()
            assert any("would pack, test and then delete: 2026-09-24_test" in s for s in said)
        finally:
            yq_secrets.backup_password, yb.find_7z, yb.backup_root = real_pw, real_7z, real_root


@test("old plain backups with 7-Zip: packed, read back with every file, and only then deleted (SKIP without 7-Zip)")
def _():
    exe = yb.find_7z()
    if not exe:
        print("      SKIP: 7-Zip is not installed here")
        return
    with data_root() as root:
        folder = plain_backup(root / "business_data" / "backups" / "2026-09-24_test", rows=50)
        half = root / "store" / "legacy" / "2026-09-24_test.7z.partial"
        half.parent.mkdir(parents=True)
        half.write_bytes(b"what a stopped run left")
        real_pw, real_root = yq_secrets.backup_password, yb.backup_root
        yq_secrets.backup_password = lambda env=None: ("an-invented-test-password", "process_environment")
        yb.backup_root = lambda: root / "store"
        try:
            said = []
            assert yb.legacy(True, say=said.append) == 0, said
            archive = root / "store" / "legacy" / "2026-09-24_test.7z"
            assert archive.is_file() and not folder.exists() and not half.exists()
            assert any("half-made 2026-09-24_test.7z.partial" in s for s in said), "the stopped run's leftover is named"
            ok, info = yb.read_back(archive, "an-invented-test-password", exe)
            assert ok and "Files: 2" in info, info
            assert yb.read_back(archive, "a wrong password", exe)[0] is False
            names_hidden(archive, exe)
        finally:
            yq_secrets.backup_password, yb.backup_root = real_pw, real_root


@test("old plain backups: a folder whose .7z is already in the store, or whose files do not match its manifest, is "
      "refused and kept; an archive there is never replaced")
def _():
    with data_root() as root:
        base = root / "business_data" / "backups"
        left_over = plain_backup(base / "2026-09-24_left-over")      # what an earlier run could not fully delete
        (left_over / "orders.csv").unlink()
        broken = plain_backup(base / "2026-09-25_broken")
        (broken / "manifest.json").write_text(json.dumps({"tables": {"orders": {"rows": 9}}}), encoding="utf-8")
        store = root / "store"
        whole = store / "legacy" / "2026-09-24_left-over.7z"
        whole.parent.mkdir(parents=True)
        whole.write_bytes(b"the whole archive of an earlier run")
        real = (yq_secrets.backup_password, yb.find_7z, yb.backup_root)
        yq_secrets.backup_password = lambda env=None: (PW, "process_environment")
        yb.find_7z = lambda: "7z.exe"              # a stand-in: both are refused before 7-Zip would run
        yb.backup_root = lambda: store
        try:
            said: list[str] = []
            assert yb.legacy(False, say=said.append) == 0
            assert any("would refuse: 2026-09-24_left-over" in s for s in said), said
            before = snapshot(root / "business_data" / "backups"), snapshot(store)
            said = []
            assert yb.legacy(True, say=said.append) == 1, said
        finally:
            yq_secrets.backup_password, yb.find_7z, yb.backup_root = real
        assert (snapshot(root / "business_data" / "backups"), snapshot(store)) == before, "nothing replaced or deleted"
        text = "\n".join(said)
        assert "KEPT 2026-09-24_left-over: 2026-09-24_left-over.7z is already in" in text, text
        assert "KEPT 2026-09-25_broken: the files do not match their manifest" in text, text


@test("pack: a dry run says per path what it is (files, size) and where its .7z would go, and changes nothing")
def _():
    with pack_world(exe="7z.exe") as root:
        store = root / "store"
        z = plain_zip(store / "preload" / "yq-backup_2026-01-05_pre-data-050126_0900.zip")
        folder = plain_backup(store / "release" / "2026-01-06_pre-test", rows=3)
        other = store / "notes.txt"
        other.write_text("x", encoding="utf-8")
        before = snapshot(root)
        said: list[str] = []
        assert yb.pack([z, folder, other, store / "not-there.zip"], apply=False, say=said.append) == 0
        assert snapshot(root) == before, "a dry run changed something"
        assert not (paths.ops_dir() / "drills.jsonl").exists(), "a dry run records nothing"
        text = "\n".join(said)
        assert f"{z.name}: a plain zip, 1 table, 2 files" in text and str(z.with_suffix(".7z")) in text, text
        assert f"{folder.name}: a plain backup folder, 1 table, 2 files" in text, text
        assert str(folder.parent / f"{folder.name}.7z") in text, text
        assert "notes.txt: would refuse: it is not a plain backup" in text, text
        assert "not-there.zip: would refuse: it is not there" in text, text
        assert said[-1] == "Dry run: nothing was changed. Run with --apply to do it.", said
    with pack_world(password=None, exe=None) as root:
        folder = plain_backup(root / "store" / "release" / "2026-01-06_pre-test")
        said = []
        assert yb.pack([folder], apply=False, say=said.append) == 0 and folder.is_dir()
        text = "\n".join(said)
        assert "no backup password" in text and "7-Zip is not installed" in text and "--apply would refuse" in text
    with pack_world(exe="7z.exe") as root:                       # the command line, as he types it
        folder = plain_backup(root / "store" / "release" / "2026-01-06_pre-test")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert yb.main(["pack", str(folder)]) == 0
        assert "would be packed into" in out.getvalue() and folder.is_dir() and not rows_written()


@test("pack --apply without the backup password, or without 7-Zip: refused before anything, nothing touched")
def _():
    for password, exe, missing in ((None, "7z.exe", "backup password"), (PW, None, "7-Zip")):
        with pack_world(password=password, exe=exe) as root:
            store = root / "store"
            folder = plain_backup(store / "release" / "2026-01-06_pre-test")
            z = plain_zip(store / "preload" / "yq-backup_2026-01-05_pre-data-050126_0900.zip")
            before = snapshot(store)
            said: list[str] = []
            assert yb.pack([folder, z], apply=True, say=said.append) == 2, said
            assert snapshot(store) == before and not rows_written()
            assert missing in said[-1] and "nothing was changed" in said[-1], said


@test("pack --apply refuses what is not a plain backup, or whose .7z is already there, and touches nothing")
def _():
    with pack_world(exe="7z.exe") as root:            # a stand-in 7-Zip: a refusal never runs it
        store = root / "store"
        folder = plain_backup(store / "release" / "2026-01-06_pre-test")
        (store / "release" / "2026-01-06_pre-test.7z").write_bytes(b"an archive made before")
        z = plain_zip(store / "preload" / "yq-backup_2026-01-05_pre-data-050126_0900.zip")
        z.with_suffix(".7z").write_bytes(b"another one")
        loose = plain_backup(store / "release" / "no-manifest")
        (loose / "manifest.json").unlink()
        app = store / "release" / "web-app"            # a manifest.json that is not a backup's
        app.mkdir()
        (app / "manifest.json").write_text(json.dumps({"name": "x", "icons": []}), encoding="utf-8")
        sealed = store / "preload" / "yq-backup_2026-01-04_pre-data-040126_0900.7z"
        sealed.write_bytes(b"7z")
        before = snapshot(store)
        said: list[str] = []
        assert yb.pack([folder, z, loose, app, sealed], apply=True, say=said.append) == 1, said
        assert snapshot(store) == before, "a refusal touched something"
        rows = rows_written()
        assert [r["result"] for r in rows] == ["refused"] * 5, rows
        assert all(r["kind"] == "pack" and r["deleted"] is False for r in rows), rows
        assert "already there" in rows[0]["why"] and "already there" in rows[1]["why"], rows
        assert "not a plain backup" in rows[2]["why"] and "no backup manifest" in rows[3]["why"], rows
        assert "already encrypted" in rows[4]["why"], rows
        assert said[-1] == "0 of 5 plain backups are now only encrypted. Each of the others is named above.", said


@test("pack --apply on a plain backup folder: a .7z next to it in the drill's layout, read back, dated like the "
      "folder, and only then the folder is deleted (SKIP without 7-Zip)")
def _():
    exe = yb.find_7z()
    if not exe:
        print("      SKIP: 7-Zip is not installed here")
        return
    with pack_world() as root:
        folder = plain_backup(root / "store" / "release" / "2026-01-06_pre-test", rows=50)
        files = {p.name: p.read_bytes() for p in folder.iterdir()}
        os.utime(folder, (OLD, OLD))
        archive = folder.parent / "2026-01-06_pre-test.7z"
        real, seen = yb.read_back, []

        def watching(arch, password, exe_):            # what is read back, and whether the final name is there yet
            seen.append((arch.name, archive.exists()))
            return real(arch, password, exe_)
        yb.read_back = watching
        try:
            said: list[str] = []
            assert yb.pack([folder], apply=True, say=said.append) == 0, said
        finally:
            yb.read_back = real
        assert seen == [("2026-01-06_pre-test.7z.partial", False)], "made and read back as .partial, renamed after"
        assert archive.is_file() and not folder.exists(), said
        assert not (folder.parent / "2026-01-06_pre-test.7z.partial").exists()
        assert abs(archive.stat().st_mtime - OLD) < 2, "the archive keeps the backup's own date"
        ok, info = yb.read_back(archive, PW, exe)
        assert ok and "Files: 2" in info, info
        assert yb.read_back(archive, "a wrong password", exe)[0] is False
        names_hidden(archive, exe)
        assert unpacked(archive, exe) == files, "manifest.json and the tables at the top, as the drill reads them"
        [row] = rows_written()
        assert row["kind"] == "pack" and row["source"] == folder.name and row["archive"] == archive.name, row
        assert row["result"] == "packed" and row["deleted"] is True and row["files"] == 2, row
        assert isinstance(row["seconds"], (int, float)) and row["password_from"] == "process_environment", row
        assert PW not in (paths.ops_dir() / "drills.jsonl").read_text(encoding="utf-8"), "no secret in the row"
        text = "\n".join(said)
        assert "1. Every file matches its manifest" in text and "2. Packed 2026-01-06_pre-test.7z" in text, text
        assert "3. Deleted the plain folder" in text and "Done in" in text, text
        assert said[-1] == "1 of 1 plain backups are now only encrypted.", said


@test("pack --apply on a plain pre-load zip: unpacked in the backup store, checked, repacked as a .7z (never a zip "
      "inside one), read back, dated like the zip, then the zip is deleted (SKIP without 7-Zip)")
def _():
    exe = yb.find_7z()
    if not exe:
        print("      SKIP: 7-Zip is not installed here")
        return
    with pack_world() as root:
        store = root / "store"
        pre = store / "preload"
        z = plain_zip(pre / "yq-backup_2026-01-05_pre-data-050126_0900.zip", rows=40)
        nested = plain_zip(pre / "yq-backup_2026-01-06_pre-data-060126_0900.zip", rows=3,
                           inner="2026-01-06_pre-data-060126/")
        upper = plain_zip(pre / "yq-backup_2026-01-07_pre-data-070126_0900.ZIP", rows=4)
        for p in (z, nested, upper):
            os.utime(p, (OLD, OLD))
        newer = pre / "yq-backup_2026-01-09_pre-data-090126_0900.7z"
        newer.write_bytes(b"7z")                       # a later load's archive, written before the pack
        said: list[str] = []
        assert yb.pack([z, nested, upper], apply=True, say=said.append) == 0, said
        for src, rows in ((z, 40), (nested, 3), (upper, 4)):
            archive = src.with_suffix(".7z")
            assert archive.is_file() and not src.exists(), said
            assert abs(archive.stat().st_mtime - OLD) < 2, "the archive keeps the zip's own date"
            ok, info = yb.read_back(archive, PW, exe)
            assert ok and "Files: 2" in info, info
            want = {name: text.encode("utf-8") for name, text in backup_files(rows).items()}
            assert unpacked(archive, exe) == want, "the zip's files at the top of the .7z, and no zip inside"
        assert yb.newest_archive(store) == newer, "an old backup packed today still sorts as old (drill, pruning)"
        assert not [p.name for p in store.iterdir() if p.name.startswith("pack_")], "the unpacked copies are gone"
        rows = rows_written()
        assert [(r["result"], r["deleted"], r["source"]) for r in rows] == [
            ("packed", True, z.name), ("packed", True, nested.name), ("packed", True, upper.name)], rows
        text = "\n".join(said)
        assert "1. Read back the zip: 2 files, all whole" in text and "2. Unpacked 2 files into" in text, text
        assert "Removed the unpacked copy" in text and said[-1] == "3 of 3 plain backups are now only encrypted.", said


@test("pack --apply: an archive that does not read back whole, or cannot take its final name, leaves nothing behind "
      "and the plain copy is kept (SKIP without 7-Zip)")
def _():
    exe = yb.find_7z()
    if not exe:
        print("      SKIP: 7-Zip is not installed here")
        return
    with pack_world() as root:
        store = root / "store"
        folder = plain_backup(store / "release" / "2026-01-06_pre-test")
        z = plain_zip(store / "preload" / "yq-backup_2026-01-05_pre-data-050126_0900.zip")
        before = snapshot(store)
        real = yb.read_back
        answers = iter([(False, "7-Zip test failed (exit 2)"), (True, "Files: 1")])   # broken; one file short

        def read_back(archive, password, exe_):
            return real(archive, password, exe_) if archive.suffix == ".zip" else next(answers)
        yb.read_back = read_back
        try:
            said: list[str] = []
            assert yb.pack([folder, z], apply=True, say=said.append) == 1, said
        finally:
            yb.read_back = real
        assert snapshot(store) == before, "the plain copies are untouched; no archive and no unpacked copy is left"
        rows = rows_written()
        assert [(r["result"], r["deleted"]) for r in rows] == [("failed", False), ("failed", False)], rows
        assert "does not read back" in rows[0]["why"] and "all 2 files" in rows[1]["why"], rows
        assert sum("KEPT:" in s for s in said) == 2, said
        real_put = yb._put_in_place

        def refused(partial, archive):                 # the very last step, the rename: "Access is denied"
            raise PermissionError(13, "Access is denied")
        yb._put_in_place = refused
        try:
            said = []
            assert yb.pack([folder], apply=True, say=said.append) == 1, said
        finally:
            yb._put_in_place = real_put
        assert snapshot(store) == before, "no .partial and no archive under its final name; the folder is the copy"
        row = rows_written()[-1]
        assert row["result"] == "failed" and "could not be made (PermissionError: Access is denied)" in row["why"], row


@test("pack --apply: a backup whose files do not match its manifest is not packed, and nothing is deleted")
def _():
    with pack_world(exe="7z.exe") as root:            # a stand-in 7-Zip: the check stops both before it would run
        store = root / "store"
        folder = plain_backup(store / "release" / "2026-01-06_pre-test")
        (folder / "manifest.json").write_text(json.dumps({"tables": {"orders": {"rows": 5}}}), encoding="utf-8")
        z = store / "preload" / "yq-backup_2026-01-05_pre-data-050126_0900.zip"
        z.parent.mkdir(parents=True)
        with zipfile.ZipFile(z, "w") as zz:            # its manifest lists a table the zip does not hold
            zz.writestr("orders.csv", backup_files(2)["orders.csv"])
            zz.writestr("manifest.json", json.dumps({"tables": {"orders": {"rows": 2}, "customers": {"rows": 1}}}))
        before = snapshot(store)
        said: list[str] = []
        assert yb.pack([folder, z], apply=True, say=said.append) == 1, said
        assert snapshot(store) == before, "nothing packed, nothing deleted, no unpacked copy left"
        rows = rows_written()
        assert [(r["result"], r["why"], r["deleted"]) for r in rows] == [
            ("failed", "the files do not match their manifest", False)] * 2, rows
        assert not any("Every file matches its manifest" in s for s in said), said


@test("pack: a half-made .7z.partial that a stopped run left is named and made again; the drill and the load's "
      "pruning never take one for an archive (SKIP without 7-Zip for the second half)")
def _():
    with data_root() as root:
        pre = root / "store" / "preload"
        pre.mkdir(parents=True)
        old = pre / "yq-backup_2026-01-05_pre-data-050126_0900.7z"
        new = pre / "yq-backup_2026-01-06_pre-data-060126_0900.7z"
        half = pre / "yq-backup_2026-01-07_pre-data-070126_0900.7z.partial"
        for i, p in enumerate((old, new, half)):       # the half-made one is the newest of the three
            p.write_bytes(b"7z")
            os.utime(p, (OLD + 60 * i, OLD + 60 * i))
        assert yb.newest_archive(root / "store") == new, "the drill's pick"
        assert yq.prune_archives(pre, 2) == [] and old.exists() and half.exists(), "pruning counts finished ones only"
    exe = yb.find_7z()
    if not exe:
        print("      SKIP: 7-Zip is not installed here")
        return
    with pack_world() as root:
        folder = plain_backup(root / "store" / "release" / "2026-01-06_pre-test")
        half = folder.parent / "2026-01-06_pre-test.7z.partial"
        half.write_bytes(b"what a stopped run left")
        said: list[str] = []
        assert yb.pack([folder], apply=False, say=said.append) == 0
        assert any("half-made 2026-01-06_pre-test.7z.partial" in s for s in said), said
        said = []
        assert yb.pack([folder], apply=True, say=said.append) == 0, said
        archive = folder.parent / "2026-01-06_pre-test.7z"
        assert archive.is_file() and not half.exists() and not folder.exists(), said
        assert yb.read_back(archive, PW, exe) == (True, "Files: 2")
        assert any("2. A half-made 2026-01-06_pre-test.7z.partial from a run that was stopped" in s for s in said), said


@test("the delete tries again, clears a read-only flag, and reports what it could not delete instead of raising")
def _():
    with data_root() as root:
        real_wait, real_once = yb.DELETE_WAIT_S, yb._delete_once
        yb.DELETE_WAIT_S = 0
        try:
            folder = plain_backup(root / "a")
            os.chmod(folder / "orders.csv", stat.S_IREAD)  # Windows answers "Access is denied" to deleting it
            z = root / "b.zip"
            z.write_bytes(b"x")
            os.chmod(z, stat.S_IREAD)
            assert yb.remove_plain(folder) == [] and not folder.exists()
            assert yb.remove_plain(z) == [] and not z.exists()
            folder = plain_backup(root / "c")
            calls: list[Path] = []

            def denied(p):
                calls.append(p)
                raise PermissionError(13, "Access is denied")
            yb._delete_once = denied
            assert yb.remove_plain(folder) == ["manifest.json", "orders.csv"] and folder.is_dir()
            assert len(calls) == yb.DELETE_TRIES, "tried again before it gave up"

            def held_once(p):                          # OneDrive lets go after the first try
                if len(calls) == yb.DELETE_TRIES:
                    calls.append(p)
                    raise PermissionError(13, "Access is denied")
                real_once(p)
            yb._delete_once = held_once
            assert yb.remove_plain(folder) == [] and not folder.exists()
        finally:
            yb.DELETE_WAIT_S, yb._delete_once = real_wait, real_once


@test("pack --apply: a plain copy that cannot be deleted (Access is denied) is reported, never claimed; the archive "
      "stays and the exit code says so (SKIP without 7-Zip)")
def _():
    exe = yb.find_7z()
    if not exe:
        print("      SKIP: 7-Zip is not installed here")
        return
    with pack_world() as root:
        folder = plain_backup(root / "store" / "release" / "2026-01-06_pre-test")
        real_once = yb._delete_once

        def denied(p):
            raise PermissionError(13, "Access is denied")
        yb._delete_once = denied
        try:
            said: list[str] = []
            assert yb.pack([folder], apply=True, say=said.append) == 1, said
        finally:
            yb._delete_once = real_once
        archive = folder.parent / "2026-01-06_pre-test.7z"
        assert archive.is_file() and yb.read_back(archive, PW, exe)[0], "the encrypted copy stays, whole"
        assert sorted(p.name for p in folder.iterdir()) == ["manifest.json", "orders.csv"]
        text = "\n".join(said)
        assert "could NOT be deleted: 2 of its files are still there (manifest.json, orders.csv)" in text, text
        assert "NOT DONE" in text and "Done in" not in text, text
        [row] = rows_written()
        assert row["result"] == "packed" and row["deleted"] is False and row["left"] == 2, row
        assert said[-1] == "0 of 1 plain backups are now only encrypted. Each of the others is named above.", said


# ── the backup before a release (release) and its way back (unpack) ─────────────────────────────────────

DAY = "2026-01-06"                                           # the day a test's release backup is taken
MISSING_7Z = str(Path(STORE) / "no-7-zip-here" / "7z.exe")   # a 7-Zip that is "found" but can never start


def backup_done(out: Path, ok: bool = True) -> str:
    """The last line scripts.db_backup prints for a backup into `out`."""
    return f"\nBackup complete: {out}  (1 tables, git abc1234, verify {'OK' if ok else 'FAILED on 1 table(s)'})\n"


def whole_backup(rows: int = 2):
    """The stand-in child of a backup that completes: the invented files of one plain backup, and db_backup's lines."""
    def child(out: Path):
        plain_backup(out, rows)
        return 0, f"  orders {rows} rows -> orders.csv\n" + backup_done(out), ""
    return child


class release_world(pack_world):
    """pack_world, with the release's backup child (scripts.db_backup --all --out <folder>, started through yb.run)
    answered by a stand-in: `child(out)` writes into the folder and returns (exit code, stdout, stderr), or raises.
    Every other command (the --verify check) runs for real. What each command was started with is kept in `calls`;
    the 7-Zip found is one that can never start, unless a test asks for the real one ("found")."""

    def __init__(self, password: str | None = PW, exe: str | None = MISSING_7Z, child=None):
        super().__init__(password, exe)
        self.child = child or whole_backup()
        self.calls: list[tuple[list[str], dict | None]] = []

    def __enter__(self) -> Path:
        root = super().__enter__()
        self._run = real = yb.run

        def run(cmd, env=None, timeout=1800):
            argv = [str(c) for c in cmd]
            self.calls.append((argv, env))
            if "--all" not in argv:
                return real(cmd, env, timeout)
            code, out, err = self.child(Path(argv[argv.index("--out") + 1]))
            return subprocess.CompletedProcess(argv, code, out, err)
        yb.run = run
        return root

    def __exit__(self, *exc):
        yb.run = self._run
        return super().__exit__(*exc)

    def backups(self) -> list[list[str]]:
        return [argv for argv, _env in self.calls if "--all" in argv]


class stand_in_seal:
    """yb.seal answered by a stand-in that writes the archive (a few bytes) and says what 7-Zip's test says; what it
    was handed (the plain folder's files at that moment, the password, the archive) is kept in `seen`."""

    def __enter__(self):
        self.seen: list[dict] = []
        self._real = yb.seal

        def seal(folder, archive, password, exe, date=None, note=None):
            self.seen.append({"files": sorted(p.name for p in folder.iterdir()), "password": password,
                              "archive": archive})
            archive.write_bytes(b"7z stand-in")
            return "Files: 2"
        yb.seal = seal
        return self

    def __exit__(self, *exc):
        yb.seal = self._real
        return False


def release_paths(root: Path, label: str = "r9", day: str = DAY) -> tuple[Path, Path]:
    """(the plain folder, the archive) of a release backup in a test's backup store."""
    folder = root / "store" / "release" / f"{day}_pre-{label}"
    return folder, folder.with_name(folder.name + ".7z")


@test("release: the label is lower-case letters and digits with single hyphens between them, at most 40 characters; "
      "any other is refused before anything (exit 2), on the command line too")
def _():
    for good in ("r9", "mi-v2", "r8m", "2b", "r9-2", "a" * 40):
        assert yb.label_refusal(good) is None, good
    for bad in ("", "R9", "r9_x", "r9 ", "-r9", "r9-", "r9--2", "mi v2", "r9/x", "..", "r9.1", "é9", "a" * 41, "r9\n"):
        assert yb.label_refusal(bad), bad
    w = release_world()
    with w as root:
        before = snapshot(root)
        said: list[str] = []
        assert yb.release("R9", say=said.append, today=DAY) == 2, said
        assert said == ["Refused, nothing was made: the label 'R9' is not lower-case letters and digits with single "
                        "hyphens between them (such as r9 or mi-v2), at most 40 characters."], said
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert yb.main(["release", "pre r9"]) == 2
        assert "Refused, nothing was made: the label 'pre r9'" in out.getvalue(), out.getvalue()
        assert snapshot(root) == before and not w.calls and not rows_written(), "nothing started, made or written"


@test("release without the backup password, or without 7-Zip: refused before the backup (exit 2); nothing is made, "
      "so nothing is left plain, and it says so")
def _():
    for password, exe, missing in ((None, MISSING_7Z, "no backup password"), (PW, None, "7-Zip is not installed")):
        w = release_world(password=password, exe=exe)
        with w as root:
            before = snapshot(root)
            said: list[str] = []
            assert yb.release("r9", say=said.append, today=DAY) == 2, said
            assert snapshot(root) == before and not w.calls and not rows_written(), "the backup never started"
            assert said[-1].startswith("Refused, nothing was made: ") and missing in said[-1], said
            assert said[-1].endswith("A release backup is only ever made encrypted: none was taken, so nothing is left "
                                     "plain."), said


@test("release: a backup of that release already there (its folder, its .7z, or both) is refused before anything "
      "(exit 2), each with what to do; nothing is touched and the backup never starts")
def _():
    w = release_world()
    with w as root:
        rel = root / "store" / "release"
        plain_backup(rel / f"{DAY}_pre-r9")                          # a whole backup made by hand
        (rel / f"{DAY}_pre-mi-v2.7z").write_bytes(b"an archive made before")
        (rel / f"{DAY}_pre-r8m.7z").write_bytes(b"an archive whose plain folder that run could not delete")
        plain_backup(rel / f"{DAY}_pre-r8m")
        (rel / f"{DAY}_pre-x1").write_bytes(b"a file of that name")
        before = snapshot(root)
        for label, words in (("r9", "If it is a whole backup (python -m scripts.db_backup --verify <it>), python -m "
                                    "scripts.yq_backup pack <it> --apply encrypts it"),
                             ("mi-v2", "this release was backed up today. Check that archive with python -m "
                                       "scripts.yq_backup drill --archive <it>"),
                             ("r8m", f"Its plain folder {DAY}_pre-r8m is next to it"),
                             ("x1", "is already there")):
            said: list[str] = []
            assert yb.release(label, say=said.append, today=DAY) == 2, said
            assert said[-1].startswith("Refused, nothing was made: ") and words in said[-1], (label, said)
            assert f"another label (such as {label}-2)" in said[-1], said
        assert snapshot(root) == before and not w.calls and not rows_written()


@test("release: the backup child is scripts.db_backup --all --out <store>\\release\\<day>_pre-<label> without the "
      "password in its environment; the folder is checked, sealed while it is still there, and only then deleted; "
      "the archive's path is the last line; one row, no secret in it")
def _():
    w = release_world()
    os.environ[yq_secrets.BACKUP_PASSWORD] = PW               # in this process's environment: no child may get it
    try:
        with w as root, stand_in_seal() as sealed:
            folder, archive = release_paths(root)
            said: list[str] = []
            assert yb.release("r9", say=said.append, today=DAY) == 0, said
            [argv] = w.backups()
            assert argv == [sys.executable, "-X", "utf8", "-m", "scripts.db_backup", "--all", "--out",
                            str(folder)], argv
            assert "business_data" not in folder.relative_to(root).parts, "never where the OneDrive copy looks"
            assert [a[a.index("-m") + 2] for a, _env in w.calls] == ["--all", "--verify"], w.calls
            for _argv, env in w.calls:
                assert env is not None and yq_secrets.BACKUP_PASSWORD not in env, "a child got the password"
                assert env["DATABASE_URL"] == os.environ["DATABASE_URL"]
            assert sealed.seen == [{"files": ["manifest.json", "orders.csv"], "password": PW, "archive": archive}]
            assert archive.is_file() and not folder.exists(), said
            text = "\n".join(said)
            assert said[0].startswith(f"Release backup {DAY}_pre-r9: every table of the live database (only read) "
                                      f"into {folder.parent} (outside OneDrive)") and "process_environment" in said[0]
            assert "  1. Backed up every table (scripts.db_backup --all, read only): 1 table, 2 files, " in text, text
            assert "  2. Every file matches its manifest (rows and columns)" in text, text
            assert f"  3. Packed {archive.name} (" in text and "read it back with the password (Files: 2)" in text, text
            assert "  4. Deleted the plain folder" in text, text
            assert said[-2].endswith("only the encrypted copy is left."), said
            assert said[-1] == str(archive), "the archive's path is the last line"
            [row] = rows_written()
            assert {k: row[k] for k in ("kind", "label", "source", "archive", "result", "deleted", "tables", "files",
                                        "password_from")} == {
                "kind": "release", "label": "r9", "source": folder.name, "archive": archive.name, "result": "packed",
                "deleted": True, "tables": 1, "files": 2, "password_from": "process_environment"}, row
            assert isinstance(row["seconds"], (int, float)) and PW not in (paths.ops_dir() / "drills.jsonl").read_text(
                encoding="utf-8"), "no secret in the row"
            out = io.StringIO()                                # the command line, as a release script runs it
            with contextlib.redirect_stdout(out):
                assert yb.main(["release", "r10"]) == 0
            last = out.getvalue().splitlines()[-1]
            assert last.endswith("_pre-r10.7z") and Path(last).is_file() and Path(last).parent == folder.parent, last
            assert not Path(last[:-len(".7z")]).exists()
    finally:
        os.environ.pop(yq_secrets.BACKUP_PASSWORD, None)


@test("release: a backup that does not complete (it fails, it is stopped, Ctrl+C) is removed, never left plain: "
      "exit 1 (Ctrl+C: raised again), the last line says so with the reason (no address in it) and names what could "
      "not be deleted, one row; the label can run again")
def _():
    def failing(out: Path):
        out.mkdir(parents=True, exist_ok=True)
        (out / "orders.csv").write_text("id,total\n0,1.500\n", encoding="utf-8")       # what it wrote before it failed
        return 1, "  orders 1 rows -> orders.csv\n", ("Traceback (most recent call last):\npsycopg.OperationalError: "
                                                      "connection to postgresql://u:an-invented-secret@db.invalid:5432 "
                                                      "failed\n")

    def stopped(out: Path):
        plain_backup(out)
        raise subprocess.TimeoutExpired(["db_backup"], yb.RELEASE_TIMEOUT_S)

    for child, words in ((failing, "exit 1: psycopg.OperationalError: connection to <address> failed"),
                         (stopped, "it was stopped after 30 minutes")):
        w = release_world(child=child)
        with w as root, stand_in_seal() as sealed:
            folder, archive = release_paths(root)
            said: list[str] = []
            assert yb.release("r9", say=said.append, today=DAY) == 1, said
            assert not folder.exists() and not sealed.seen and not list(folder.parent.glob("*.7z*")), said
            assert said[-1] == (f"RELEASE BACKUP NOT DONE: the backup did not complete ({words}). What it wrote was "
                                "removed: nothing is left plain. Run this again once the cause is put right."), said
            assert "an-invented-secret" not in "\n".join(said) + json.dumps(rows_written())
            [row] = rows_written()
            assert row["result"] == "failed" and row["deleted"] is False and words in row["why"], row
            w.child = whole_backup()
            said = []
            assert yb.release("r9", say=said.append, today=DAY) == 0 and archive.is_file(), said

    def interrupted(out: Path):
        plain_backup(out)
        raise KeyboardInterrupt

    def denied(p):
        raise PermissionError(13, "Access is denied")
    for held in (False, True):                                  # the second time, what it wrote cannot be deleted
        with release_world(child=interrupted) as root:
            folder = release_paths(root)[0]
            real_once = yb._delete_once
            if held:
                yb._delete_once = denied
            said = []
            try:
                yb.release("r9", say=said.append, today=DAY)
            except KeyboardInterrupt:
                pass
            else:
                raise AssertionError("Ctrl+C was swallowed")
            finally:
                yb._delete_once = real_once
            if held:
                assert sorted(p.name for p in folder.iterdir()) == ["manifest.json", "orders.csv"]
                assert said[-1] == ("RELEASE BACKUP NOT DONE: it was stopped (Ctrl+C). What it wrote could NOT all be "
                                    "removed (2 of its files are still there (manifest.json, orders.csv)): delete "
                                    f"{folder} by hand, it holds part of the backup unencrypted."), said
            else:
                assert not folder.exists(), "a stopped backup is never left plain"
                assert said[-1] == ("RELEASE BACKUP NOT DONE: it was stopped (Ctrl+C). What it wrote was removed: "
                                    "nothing is left plain."), said
            [row] = rows_written()
            assert (row["result"], row["stage"], row["why"], row.get("left")) == (
                "stopped", "backup", "it was stopped (Ctrl+C)", 2 if held else None), row


@test("release: a backup that completed but does not match its manifest (by the check after it, or by its own), or "
      "lists no table, is kept as it is and named, never sealed: exit 1, one row")
def _():
    def mismatch(out: Path, own_check: bool):
        plain_backup(out)
        (out / "manifest.json").write_text(json.dumps({"tables": {"orders": {"rows": 9}}}), encoding="utf-8")
        return (1, backup_done(out, ok=False), "") if own_check else (0, backup_done(out), "")

    def no_table(out: Path):
        out.mkdir(parents=True, exist_ok=True)
        (out / "manifest.json").write_text(json.dumps({"tables": {}}), encoding="utf-8")
        return 0, backup_done(out), ""

    both = ["manifest.json", "orders.csv"]
    own = "the backup's own check found files that do not match its manifest"
    for child, why, files in ((lambda out: mismatch(out, False), "the files do not match their manifest", both),
                              (lambda out: mismatch(out, True), own, both),
                              (no_table, "the backup lists no table", ["manifest.json"])):
        w = release_world(child=child)
        with w as root, stand_in_seal() as sealed:
            folder, archive = release_paths(root)
            said: list[str] = []
            assert yb.release("r9", say=said.append, today=DAY) == 1, said
            assert sorted(p.name for p in folder.iterdir()) == files, "kept as it was"
            assert not sealed.seen and not archive.exists() and not any("Every file matches" in s for s in said), said
            assert said[-1] == (f"RELEASE BACKUP NOT DONE: {why}. The plain folder {folder} is kept (unencrypted, "
                                f"outside OneDrive) for a look (python -m scripts.db_backup --verify {folder}); delete "
                                "it before running this again."), said
            [row] = rows_written()
            assert (row["result"], row["why"], row["deleted"]) == ("failed", why, False), row


@test("release: an archive that cannot be made (7-Zip does not start), or does not read back whole, leaves no archive "
      "and no .partial, and keeps the plain folder, a whole backup, with the way to seal it later: exit 1 (SKIP "
      "without 7-Zip for the read-back)")
def _():
    with release_world() as root:                              # a 7-Zip that cannot start
        folder, archive = release_paths(root)
        said: list[str] = []
        assert yb.release("r9", say=said.append, today=DAY) == 1, said
        assert sorted(p.name for p in folder.iterdir()) == ["manifest.json", "orders.csv"]
        assert not list(folder.parent.glob("*.7z*")), "no archive, no .partial"
        assert said[-1].startswith(f"RELEASE BACKUP NOT DONE: {archive.name} could not be made "
                                   "(FileNotFoundError"), said
        assert said[-1].endswith(f"The plain folder {folder} is kept (unencrypted, outside OneDrive), a whole backup: "
                                 f"once the cause is put right, python -m scripts.yq_backup pack {folder} --apply "
                                 "encrypts it and then deletes it."), said
        assert rows_written()[-1]["result"] == "failed"
    exe = yb.find_7z()
    if not exe:
        print("      SKIP: 7-Zip is not installed here")
        return
    with release_world(exe="found") as root:
        folder, archive = release_paths(root)
        real = yb.read_back
        yb.read_back = lambda arch, password, exe_: (False, "7-Zip test failed (exit 2)")
        try:
            said = []
            assert yb.release("r9", say=said.append, today=DAY) == 1, said
        finally:
            yb.read_back = real
        assert sorted(p.name for p in folder.iterdir()) == ["manifest.json", "orders.csv"]
        assert not list(folder.parent.glob("*.7z*")), "the archive that did not read back is gone, .partial too"
        assert said[-1].startswith(f"RELEASE BACKUP NOT DONE: {archive.name} does not read back with all 2 files "
                                   "(7-Zip test failed (exit 2)). The plain folder"), said


@test("release: stopped (Ctrl+C, or an error nobody expected) while it checks, seals or deletes: raised again, after a "
      "last line that says what is left plain and what to do with it, and a row with the stage and the reason")
def _():
    def stop(*_a, **_k):
        raise KeyboardInterrupt

    def bug(*_a, **_k):
        raise TypeError("an invented bug")
    kept = ("The plain folder {folder} is kept (unencrypted, outside OneDrive): python -m scripts.yq_backup pack "
            "{folder} --apply checks it against its manifest, encrypts it and then deletes it.")
    for stage, name, fake, exc, why in (("check", "verify_folder", stop, KeyboardInterrupt, "it was stopped (Ctrl+C)"),
                                        ("seal", "seal", stop, KeyboardInterrupt, "it was stopped (Ctrl+C)"),
                                        ("seal", "seal", bug, TypeError, "it stopped on an unexpected TypeError"),
                                        ("delete", "_delete_once", stop, KeyboardInterrupt, "it was stopped (Ctrl+C)")):
        with release_world() as root, stand_in_seal():
            folder, archive = release_paths(root)
            real = getattr(yb, name)
            setattr(yb, name, fake)
            said: list[str] = []
            try:
                yb.release("r9", say=said.append, today=DAY)
            except exc:
                pass
            else:
                raise AssertionError(f"{stage}: the stop was swallowed")
            finally:
                setattr(yb, name, real)
            assert sorted(p.name for p in folder.iterdir()) == ["manifest.json", "orders.csv"], "the plain folder stays"
            if stage == "delete":
                end = (f"The encrypted copy {archive} is made and read back, but the plain folder could NOT all be "
                       "deleted (2 of its files are still there (manifest.json, orders.csv)): delete "
                       f"{folder} by hand.")
                assert archive.is_file(), said
            else:
                end = kept.format(folder=folder)
                assert not list(folder.parent.glob("*.7z*")), "no archive and no .partial"
            assert said[-1] == f"RELEASE BACKUP NOT DONE: {why}. {end}", said
            [row] = rows_written()
            assert (row["result"], row["stage"], row["why"], row["deleted"]) == ("stopped", stage, why, False), row


@test("release: a backup store (YQ_BACKUP_DIR) inside the project folder or the code folder, or a relative one read "
      "from the code folder, is refused before anything (exit 2): no plain copy where OneDrive or git sees it")
def _():
    w = release_world()
    with w as root:
        patched, real_root = yb.backup_root, w._real[2]       # the real rule (scripts.nightly_backup: YQ_BACKUP_DIR)
        project, code_store = paths.data_root(), ROOT / "yq-backups-never-made"
        project.mkdir(parents=True, exist_ok=True)
        before, cwd = snapshot(root), os.getcwd()
        yb.backup_root = real_root
        in_code = "is inside the code folder, a public repository"
        try:
            for store, where in ((project / "yq-backups", "is inside the project folder, which the daily OneDrive"),
                                 (code_store, in_code), (Path("relative-store"), in_code)):
                os.environ["YQ_BACKUP_DIR"] = str(store)
                os.chdir(ROOT)                                # a relative store is read from the folder this runs in
                said: list[str] = []
                assert yb.release("r9", say=said.append, today=DAY) == 2, said
                assert len(said) == 1 and said[0].startswith("Refused, nothing was made: ") and where in said[0], said
                assert said[0].endswith(f"{yb.STORE_FIX}."), said
        finally:
            os.environ["YQ_BACKUP_DIR"] = STORE
            os.chdir(cwd)
            yb.backup_root = patched
        assert snapshot(root) == before and not w.calls and not rows_written(), "nothing started, made or written"
        assert not code_store.exists() and not (ROOT / "relative-store").exists()


@test("release: a plain folder that cannot be deleted after the seal (Access is denied) is reported, never claimed: "
      "exit 1, the archive stays, the row says so")
def _():
    with release_world() as root, stand_in_seal():
        folder, archive = release_paths(root)
        real_once = yb._delete_once

        def denied(p):
            raise PermissionError(13, "Access is denied")
        yb._delete_once = denied
        try:
            said: list[str] = []
            assert yb.release("r9", say=said.append, today=DAY) == 1, said
        finally:
            yb._delete_once = real_once
        assert archive.is_file() and sorted(p.name for p in folder.iterdir()) == ["manifest.json", "orders.csv"]
        assert "could NOT be deleted: 2 of its files are still there (manifest.json, orders.csv)" in "\n".join(said)
        assert said[-1] == (f"RELEASE BACKUP NOT DONE: the encrypted copy {archive} is made and read back, but the "
                            "plain folder is still there (see above)."), said
        [row] = rows_written()
        assert (row["result"], row["deleted"], row["left"]) == ("packed", False, 2), row


@test("unpack: refused before anything (exit 2, nothing written) into a folder that is not empty, a file, a folder "
      "inside OneDrive or the project folder; for what is not a backup archive; for a .7z without the password or "
      "7-Zip")
def _():
    with pack_world(exe=MISSING_7Z) as root, tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        z = plain_zip(tmp / "in" / f"{DAY}_pre-r9.zip")
        full = tmp / "full"
        full.mkdir()
        (full / "x.txt").write_text("x", encoding="utf-8")
        a_file = tmp / "a-file.txt"
        a_file.write_text("x", encoding="utf-8")
        code = ROOT / "restore-here-never-made"                    # the code folder: a public repository
        before = snapshot(root), snapshot(tmp)
        for archive, to, words in ((z, full, "is not empty: name a new folder, or an empty one"),
                                   (z, a_file, "is a file, not a folder"),
                                   (z, tmp / "OneDrive - YQ" / "restore", "is inside a synced folder (OneDrive)"),
                                   (z, paths.data_root() / "business_data" / "restore", "is inside the project folder"),
                                   (z, code, "is inside the code folder, a public repository"),
                                   (tmp / "in" / "missing.7z", None, "is not a backup archive on this PC"),
                                   (a_file, None, "is not a backup archive on this PC"),
                                   (tmp / "in", None, "is not a backup archive on this PC")):
            said: list[str] = []
            assert yb.unpack_backup(archive, to, say=said.append) == 2, (archive, to, said)
            assert len(said) == 1 and said[0].startswith("Refused, nothing was written: ") and words in said[0], said
        patched = yb.backup_root
        yb.backup_root = lambda: paths.data_root() / "yq-backups"      # its own folder: a store inside the project
        try:
            said = []
            assert yb.unpack_backup(z, None, say=said.append) == 2, said
        finally:
            yb.backup_root = patched
        assert len(said) == 1 and "is inside the project folder" in said[0], said
        assert said[0].endswith(f"{yb.STORE_FIX}."), said
        assert (snapshot(root), snapshot(tmp)) == before and not code.exists() and not rows_written()
    for password, exe, missing in ((None, MISSING_7Z, "no backup password"), (PW, None, "7-Zip is not installed")):
        with pack_world(password=password, exe=exe) as root:
            sz = root / "store" / "release" / f"{DAY}_pre-r9.7z"
            sz.parent.mkdir(parents=True)
            sz.write_bytes(b"7z")
            before = snapshot(root)
            said = []
            assert yb.unpack_backup(sz, None, say=said.append) == 2 and missing in said[-1], said
            assert snapshot(root) == before and not rows_written()


@test("unpack: a plain zip into a named empty folder, or a new folder of the backup store, checked against its "
      "manifest, the folder holding manifest.json the last line; a damaged archive, files that do not match their "
      "manifest or no manifest leave nothing behind (exit 1)")
def _():
    with pack_world(exe=None) as root, tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        z = plain_zip(tmp / "in" / "yq-backup_2026-01-05_pre-data-050126_0900.zip", rows=3,
                      inner="2026-01-05_pre-data/")
        to = tmp / "restore"
        to.mkdir()                                              # an empty folder, named with --to
        said: list[str] = []
        assert yb.unpack_backup(z, to, say=said.append) == 0, said
        inner = to / "2026-01-05_pre-data"
        assert os.path.samefile(said[-1], inner), said
        assert {p.name: p.read_text(encoding="utf-8") for p in inner.iterdir()} == backup_files(3)
        text = "\n".join(said)
        assert f"  1. Read back {z.name}: 2 files, all whole" in text and "  2. Unpacked 2 files (1 table) into" in text
        assert "  3. Every file matches its manifest (rows and columns)" in text, text
        assert said[-2].startswith("  The restore: python -m scripts.db_backup --restore "), said
        assert "it holds the backup unencrypted" in said[-2], said
        [row] = rows_written()
        assert {k: row[k] for k in ("kind", "archive", "folder", "result", "encrypted", "tables", "files")} == {
            "kind": "unpack", "archive": z.name, "folder": to.name, "result": "unpacked", "encrypted": False,
            "tables": 1, "files": 2}, row
        out = io.StringIO()                                     # the command line, with no --to
        with contextlib.redirect_stdout(out):
            assert yb.main(["unpack", str(z)]) == 0
        made = Path(out.getvalue().splitlines()[-1])
        assert made.name == "2026-01-05_pre-data" and made.parent.name.startswith("unpack_"), made
        assert os.path.samefile(made.parent.parent, root / "store"), "a new folder of the backup store"
        bad = tmp / "in" / "damaged.zip"
        bad.write_bytes(z.read_bytes()[:40])
        wrong = tmp / "in" / "wrong.zip"
        with zipfile.ZipFile(wrong, "w") as zz:                 # its manifest says 7 rows; the table holds 2
            zz.writestr("orders.csv", backup_files(2)["orders.csv"])
            zz.writestr("manifest.json", json.dumps({"tables": {"orders": {"rows": 7}}}))
        loose = tmp / "in" / "loose.zip"
        with zipfile.ZipFile(loose, "w") as zz:
            zz.writestr("orders.csv", backup_files(2)["orders.csv"])
        for archive, why, end in (
                (bad, "the archive does not read back whole", "Nothing was written."),
                (wrong, "the files do not match their manifest", "The unpacked copy was removed."),
                (loose, "the archive holds no manifest.json at its top or one folder down: it is not a backup",
                 "The unpacked copy was removed.")):
            target = tmp / f"restore-{archive.stem}"
            target.mkdir()
            said = []
            assert yb.unpack_backup(archive, target, say=said.append) == 1, said
            assert said[-1] == f"UNPACK FAILED: {why}. {end}", said
            assert target.is_dir() and not any(target.iterdir()), "the named empty folder is left as it was"
            assert rows_written()[-1]["result"] == "failed" and rows_written()[-1]["why"] == why
        said = []
        assert yb.unpack_backup(wrong, None, say=said.append) == 1, said
        assert [p.name for p in (root / "store").iterdir() if p.name.startswith("unpack_")] == [made.parent.name], \
            "the new folder of the failed unpack is removed again"


@test("unpack: stopped (Ctrl+C) while it checks the copy: raised again, after the copy is removed (a named empty "
      "folder is left as it was) or named when it cannot be, in a last line and a row")
def _():
    def stop(*_a, **_k):
        raise KeyboardInterrupt

    def denied(p):
        raise PermissionError(13, "Access is denied")
    with pack_world(exe=None) as root, tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        z = plain_zip(tmp / "in" / f"{DAY}_pre-r9.zip")
        for to, held in ((tmp / "restore", False), (None, False), (tmp / "restore-held", True)):
            if to is not None:
                to.mkdir()                                     # an empty folder, named with --to
            real_verify, real_once = yb.verify_folder, yb._delete_once
            yb.verify_folder = stop
            if held:
                yb._delete_once = denied
            said: list[str] = []
            try:
                yb.unpack_backup(z, to, say=said.append)
            except KeyboardInterrupt:
                pass
            else:
                raise AssertionError("Ctrl+C was swallowed")
            finally:
                yb.verify_folder, yb._delete_once = real_verify, real_once
            row = rows_written()[-1]
            assert (row["kind"], row["result"], row["why"]) == ("unpack", "stopped", "it was stopped (Ctrl+C)"), row
            if held:
                assert said[-1].startswith("UNPACK FAILED: it was stopped (Ctrl+C). The unpacked copy could NOT all be "
                                           "removed (2 of its files are still there (manifest.json, orders.csv)): "
                                           "delete "), said
                assert said[-1].endswith(" by hand, it holds the backup unencrypted.") and row["left"] == 2, said
            else:
                assert said[-1] == "UNPACK FAILED: it was stopped (Ctrl+C). The unpacked copy was removed.", said
                if to is not None:
                    assert to.is_dir() and not any(to.iterdir()), "the named empty folder is left as it was"
                else:
                    assert not [p for p in (root / "store").iterdir() if p.name.startswith("unpack_")], "its folder too"


@test("release then unpack, with 7-Zip (SKIP without): the archive reads back only with the password and hides the "
      "names; unpack gives every file back byte for byte in a new folder of the backup store; a wrong password "
      "unpacks nothing")
def _():
    exe = yb.find_7z()
    if not exe:
        print("      SKIP: 7-Zip is not installed here")
        return
    written: dict[str, bytes] = {}

    def child(out: Path):                                     # a whole backup, and the bytes it put on disk
        answer = whole_backup(rows=50)(out)
        written.update({p.name: p.read_bytes() for p in out.iterdir()})
        return answer
    with release_world(exe="found", child=child) as root:
        folder, archive = release_paths(root, "mi-v2")
        said: list[str] = []
        assert yb.release("mi-v2", say=said.append, today=DAY) == 0, said
        assert said[-1] == str(archive) and archive.is_file() and not folder.exists(), said
        assert yb.read_back(archive, PW, exe) == (True, "Files: 2")
        assert yb.read_back(archive, "a wrong password", exe)[0] is False
        names_hidden(archive, exe)
        said = []
        assert yb.unpack_backup(archive, None, say=said.append) == 0, said
        back = Path(said[-1])
        assert back.name.startswith("unpack_") and os.path.samefile(back.parent, root / "store"), back
        assert sorted(written) == ["manifest.json", "orders.csv"], written
        assert {p.name: p.read_bytes() for p in back.iterdir()} == written, "every file back, byte for byte"
        assert f"  1. Read back {archive.name} with the password: Files: 2" in said, said
        real_pw = yq_secrets.backup_password
        yq_secrets.backup_password = lambda env=None: ("a wrong password", "process_environment")
        try:
            said = []
            assert yb.unpack_backup(archive, None, say=said.append) == 1, said
        finally:
            yq_secrets.backup_password = real_pw
        assert said[-1] == ("UNPACK FAILED: the archive does not read back whole (a wrong password reads the same "
                            "way). Nothing was written."), said
        assert [p.name for p in (root / "store").iterdir() if p.name.startswith("unpack_")] == [back.name]
        rows = rows_written()
        assert [(r["kind"], r["result"]) for r in rows] == [("release", "packed"), ("unpack", "unpacked"),
                                                            ("unpack", "failed")], rows
        assert PW not in (paths.ops_dir() / "drills.jsonl").read_text(encoding="utf-8")


# ── the snapshot itself: scripts/db_backup.py on a stand-in database ─────────────────────────────────

IDENT = r'"((?:[^"]|"")*)"'          # a quoted SQL identifier (an embedded double quote doubled)
BRANCH = re.compile(r"select (\d+), count\(\*\), (max\(id\)::text|null::text), (max\(created_at\)::text|null::text) "
                    r"from public\." + IDENT)


def unquote(text: str) -> str:
    return text.replace('""', '"')


def stand_in_tables() -> dict:
    """Invented tables of every shape the manifest records: an id that is bigint, integer, smallint, uuid or
    absent; created_at of three types, all NULL or absent; an empty table; a name that needs quoting; values with
    a comma, quotes, a line break and a NULL. {name: {"columns": [(name, type, nullable, has a default)],
    "rows": [{column: value}]}}; every value is made here, at run time."""
    def at(day: int, hour: int) -> str:                       # a timestamptz as the UTC session prints it
        return f"2026-01-{day:02d} {hour:02d}:30:00+00"

    notes = {1: "plain", 2: 'a comma, and "quotes"', 3: None, 10: "two\nlines"}
    return {
        "orders": {"columns": [("id", "bigint", False, True), ("created_at", "timestamp with time zone", False, True),
                               ("note", "text", True, False), ("qty", "integer", False, False)],
                   "rows": [{"id": i, "created_at": at(i, 9), "note": n, "qty": 2 * i} for i, n in notes.items()]},
        "order_lines": {"columns": [("id", "integer", False, True), ("order_id", "bigint", False, False),
                                    ("created_at", "timestamp without time zone", True, False)],
                        "rows": [{"id": i, "order_id": 1 + i % 2, "created_at": f"2026-01-0{i} 10:00:00"}
                                 for i in (1, 2, 3)]},
        "app_settings": {"columns": [("key", "text", False, False), ("value", "jsonb", True, False),
                                     ("updated_at", "timestamp with time zone", True, True)],
                         "rows": [{"key": f"setting_{i}", "value": json.dumps({"n": i}), "updated_at": at(5, i)}
                                  for i in range(3)]},
        "uuid_items": {"columns": [("id", "uuid", False, True), ("created_at", "timestamp with time zone", True, False),
                                   ("label", "text", True, False)],
                       "rows": [{"id": f"00000000-0000-4000-8000-00000000000{i}", "created_at": None,
                                 "label": "café"} for i in range(2)]},
        "empty_log": {"columns": [("id", "smallint", False, True), ("created_at", "date", True, False)], "rows": []},
        "audit_log": {"columns": [("id", "bigint", False, True), ("ts", "timestamp with time zone", False, True),
                                  ("event", "text", True, False)],
                      "rows": [{"id": 7 + i, "ts": at(6, i), "event": f"event {i}"} for i in range(4)]},
        "Mixed Case": {"columns": [("id", "bigint", False, True), ("v", "numeric", True, False)],
                       "rows": [{"id": i, "v": f"{i}.5"} for i in (4, 9)]},
    }


class StandInDb:
    """A stand-in psycopg connection over invented tables. It answers every statement scripts/db_backup.py sends
    (the catalog, the per-table counts and max values, the batched ones, COPY) from the same rows, as PostgreSQL
    would, and records each one: one round trip each. Batched rows come back in reverse order (a parallel plan
    promises none)."""

    def __init__(self, tables: dict, views: dict | None = None, fks: list | None = None):
        self.tables, self.views, self.fks = tables, views or {}, fks or []
        self.sent: list[tuple[str, str]] = []           # (execute | copy, the statement on one line)
        self.isolation_level = self.read_only = None
        self.transactions, self.in_tx, self.outside_tx = 0, False, 0
        self.tx_settings = None                         # (isolation level, read only) as the transaction began

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @contextlib.contextmanager
    def transaction(self):
        self.transactions += 1
        self.tx_settings = (self.isolation_level, self.read_only)
        self.in_tx = True
        try:
            yield
        finally:
            self.in_tx = False

    def cursor(self):
        return StandInCursor(self)

    def record(self, kind: str, sql) -> str:
        text = " ".join(str(sql).split())
        self.sent.append((kind, text))
        self.outside_tx += not self.in_tx
        return text

    def max_text(self, t: str, col: str) -> str | None:
        """max(col)::text: NULLs left out, None when there is no value."""
        vals = [r[col] for r in self.tables[t]["rows"] if r[col] is not None]
        return str(max(vals)) if vals else None

    def csv_bytes(self, t: str) -> bytes:
        """What COPY ... TO STDOUT (FORMAT csv, HEADER true) sends: the header, then one record per row."""
        names = [c[0] for c in self.tables[t]["columns"]]
        buf = io.StringIO()
        out = csv.writer(buf, lineterminator="\n")
        out.writerow(names)
        out.writerows([[r[n] for n in names] for r in self.tables[t]["rows"]])
        return buf.getvalue().encode("utf-8")

    def answer(self, text: str, params) -> list[tuple]:
        if text == "show transaction_read_only":
            return [("on",)]
        if text == "set local time zone 'UTC'":
            return []
        if "from information_schema.tables" in text:
            return [(t,) for t in sorted(self.tables)]
        if "from pg_constraint" in text:
            return list(self.fks)
        if "from information_schema.columns" in text:
            rels = {**self.views, **self.tables}
            if params:                                  # the per-table query: one table's columns
                return [(n, ty, "YES" if nu else "NO", d) for n, ty, nu, d in rels.get(params[0], {}).get("columns", [])]
            return [(t, n, ty, "YES" if nu else "NO", d) for t in sorted(rels) for n, ty, nu, d in rels[t]["columns"]]
        m = re.fullmatch(r"select count\(\*\) from public\." + IDENT, text)
        if m:
            return [(len(self.tables[unquote(m.group(1))]["rows"]),)]
        m = re.fullmatch(r"select max\((id|created_at)\)::text from public\." + IDENT, text)
        if m:
            return [(self.max_text(unquote(m.group(2)), m.group(1)),)]
        branches = [BRANCH.fullmatch(part) for part in text.split(" union all ")]
        if all(branches):
            rows = []
            for b in branches:
                t = unquote(b.group(4))
                rows.append((int(b.group(1)), len(self.tables[t]["rows"]),
                             None if b.group(2) == "null::text" else self.max_text(t, "id"),
                             None if b.group(3) == "null::text" else self.max_text(t, "created_at")))
            return rows[::-1]
        raise AssertionError(f"the stand-in does not know this statement: {text[:200]}")


class StandInCursor:
    def __init__(self, db: StandInDb):
        self.db, self.rows = db, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.rows = self.db.answer(self.db.record("execute", sql), params)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def copy(self, sql):
        text = self.db.record("copy", sql)
        m = re.fullmatch(r"COPY public\." + IDENT + r" TO STDOUT WITH \(FORMAT csv, HEADER true\)", text)
        assert m, f"the stand-in does not know this COPY: {text}"
        data = self.db.csv_bytes(unquote(m.group(1)))
        return contextlib.nullcontext(data[i:i + 5] for i in range(0, len(data), 5))     # in small chunks


def stand_in() -> StandInDb:
    return StandInDb(stand_in_tables(), views={"v_orders": {"columns": [("id", "bigint", True, False)]}},
                     fks=[("order_lines_order_id_fkey", "order_lines", "orders", "c")])


def run_backup(db: StandInDb, out: Path, tables: list[str] | None = None, all_tables: bool = True) -> tuple[dict, str]:
    """scripts.db_backup.backup on the stand-in: (the manifest it wrote, what it printed)."""
    real = (db_backup._conn, db_backup._git_sha)
    db_backup._conn = lambda dsn_env="DATABASE_URL": db
    db_backup._git_sha = lambda: "abc1234"
    printed = io.StringIO()
    try:
        with contextlib.redirect_stdout(printed):
            db_backup.backup(tables, out, all_tables=all_tables)
    finally:
        db_backup._conn, db_backup._git_sha = real
    return json.loads((out / "manifest.json").read_text(encoding="utf-8")), printed.getvalue()


def expected_manifest(db: StandInDb, names: list[str], taken_at: str) -> str:
    """manifest.json as the per-table queries wrote it: per table rows, bytes, columns, then max_id for an integer
    id and max_created_at when the table has a created_at column (null when there is no value)."""
    tables = {}
    for t in names:
        cols = db.tables[t]["columns"]
        entry = {"rows": len(db.tables[t]["rows"]), "bytes": len(db.csv_bytes(t)),
                 "columns": [{"name": n, "type": ty, "nullable": nu, "has_default": d} for n, ty, nu, d in cols]}
        types = {n: ty for n, ty, _, _ in cols}
        if types.get("id") in ("bigint", "integer", "smallint"):
            entry["max_id"] = db.max_text(t, "id")
        if "created_at" in types:
            entry["max_created_at"] = db.max_text(t, "created_at")
        tables[t] = entry
    return json.dumps({"taken_at": taken_at, "git": "abc1234", "snapshot": "repeatable read, read only, one transaction",
                       "tables": tables,
                       "fks": [{"name": a, "child": b, "parent": p, "on_delete": d} for a, b, p, d in db.fks]}, indent=2)


@test("db_backup: the CSVs, manifest.json and the printed lines are exactly what the per-table queries wrote")
def _():
    db = stand_in()
    names = sorted(db.tables)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "b"
        manifest, printed = run_backup(db, out)
        assert sorted(p.name for p in out.iterdir()) == sorted([f"{t}.csv" for t in names] + ["manifest.json"])
        for t in names:
            assert (out / f"{t}.csv").read_bytes() == db.csv_bytes(t), t
        text = (out / "manifest.json").read_text(encoding="utf-8")
        assert text == expected_manifest(db, names, manifest["taken_at"]), text
        assert printed == ("".join(f"  {t:32s} {len(db.tables[t]['rows']):8d} rows -> {t}.csv\n" for t in names)
                           + f"\nBackup complete: {out}  ({len(names)} tables, git abc1234, verify OK)\n"), printed
        # the per-table queries (what --restore still sends) read the same values from the same stand-in
        with db.cursor() as cur:
            for t in names:
                cols = db_backup._columns(cur, t)
                cur.execute(f'select count(*) from public."{t}"')
                assert manifest["tables"][t] == {"rows": cur.fetchone()[0], "bytes": (out / f"{t}.csv").stat().st_size,
                                                 "columns": cols, **db_backup._maxes(cur, t, cols)}, t


@test("db_backup: the columns of every table in one catalog query, every count and max in one statement, both "
      "before one COPY per table, all in one read-only REPEATABLE READ transaction")
def _():
    import psycopg
    db = stand_in()
    with tempfile.TemporaryDirectory() as tmp:
        run_backup(db, Path(tmp) / "b")
    n = len(db.tables)
    texts = [s for _, s in db.sent]
    copies = [i for i, (kind, _) in enumerate(db.sent) if kind == "copy"]
    catalog = [i for i, s in enumerate(texts) if "information_schema.columns" in s]
    counted = [i for i, s in enumerate(texts) if "count(*)" in s]
    assert len(copies) == n and len(catalog) == 1 and len(counted) == 1, texts
    assert max(catalog + counted) < min(copies), "the columns, counts and max values come before the first COPY"
    assert len(db.sent) == n + 6, f"{len(db.sent)} round trips for {n} tables: one COPY each and six more"
    assert texts[:2] == ["show transaction_read_only", "set local time zone 'UTC'"], texts[:2]
    assert db.transactions == 1 and db.outside_tx == 0, "every statement inside the one transaction"
    assert db.tx_settings == (psycopg.IsolationLevel.REPEATABLE_READ, True), db.tx_settings


@test("db_backup: a table name is quoted as an identifier in every statement (an embedded double quote doubled); "
      "the batch answers by position, whatever order its rows come back in")
def _():
    assert db_backup._qi('Odd "Name"') == '"Odd ""Name"""' and db_backup._qi("orders") == '"orders"'
    tables = stand_in_tables()
    tables['Odd "Name"'] = tables.pop("orders")
    db = StandInDb(tables)
    names = ['Odd "Name"', "Mixed Case", "app_settings"]
    with db.cursor() as cur:
        got = db_backup._counts(cur, names, db_backup._all_columns(cur))
    assert got == {'Odd "Name"': (4, {"max_id": "10", "max_created_at": db.max_text('Odd "Name"', "created_at")}),
                   "Mixed Case": (2, {"max_id": "9"}), "app_settings": (3, {})}, got
    [batch] = [s for _, s in db.sent if "count(*)" in s]
    assert 'from public."Odd ""Name"""' in batch and 'from public."Mixed Case"' in batch, batch
    assert db_backup._counts(db.cursor(), [], {}) == {} and len(db.sent) == 2, "no table: no statement"


@test("db_backup: a table list keeps its order, names a table that is not there, backs a repeated one up once and "
      "batches only the tables it backs up; with none left, no count statement is sent")
def _():
    db = stand_in()
    with tempfile.TemporaryDirectory() as tmp:
        manifest, printed = run_backup(db, Path(tmp) / "b", ["orders", "not_a_table", "Mixed Case", "orders"], False)
        assert list(manifest["tables"]) == ["orders", "Mixed Case"], list(manifest["tables"])
        assert "  skip not_a_table: not a public table\n" in printed and "(2 tables, git abc1234, verify OK)" in printed
        assert [s for k, s in db.sent if k == "copy"] == ['COPY public."orders" TO STDOUT WITH (FORMAT csv, HEADER true)',
                                                         'COPY public."Mixed Case" TO STDOUT WITH (FORMAT csv, HEADER true)']
        [batch] = [s for _, s in db.sent if "count(*)" in s]
        assert batch.count(" union all ") == 1 and "app_settings" not in batch, batch
        empty = stand_in()
        manifest, printed = run_backup(empty, Path(tmp) / "c", ["not_a_table"], False)
        assert manifest["tables"] == {} and "(0 tables, git abc1234, verify OK)" in printed, printed
        assert not [s for _, s in empty.sent if "count(*)" in s], "an empty UNION ALL is never sent"


def main() -> int:
    passed = failed = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"PASS  {name}")
            passed += 1
        except Exception:  # noqa: BLE001
            print(f"FAIL  {name}")
            traceback.print_exc()
            failed += 1
        finally:
            paths.set_data_root(None)
            config.use(None)
    shutil.rmtree(STORE, ignore_errors=True)
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
