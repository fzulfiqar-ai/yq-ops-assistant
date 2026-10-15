"""Backups: the encryption rule of the load's backup step, the read-back, the restore drill's guards and the
packing of old plain backups (scripts/yq_load_go.py, scripts/yq_backup.py, scripts/yq_secrets.py).

    python -m tests.test_ops_backup

Same lightweight runner as the other suites (no pytest). No database: SUPABASE_URL, SUPABASE_KEY and
DATABASE_URL are pinned to ci.invalid before any import; the restore drill itself (a throwaway PostgreSQL) is
run by hand, never here: its guards are tested instead. No real secret: YQ_SECRETS_OFF=1. One test packs a
folder of invented files with 7-Zip and reads it back; it prints SKIP where 7-Zip is not installed.

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
    copy kept; a plain copy that cannot be deleted is reported (never claimed) and the exit code says so.

The backup store is a temporary folder for the whole run (YQ_BACKUP_DIR, pinned below): no test reads or
writes this PC's real store.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
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
    """A data root and a backup store of their own, the test password (or none), 7-Zip as found here (or a
    stand-in name, or none), and the waits of the delete set to nothing."""

    def __init__(self, password: str | None = PW, exe: str | None = "found"):
        self.password, self.exe = password, exe

    def __enter__(self) -> Path:
        self._root = data_root()
        root = self._root.__enter__()
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
