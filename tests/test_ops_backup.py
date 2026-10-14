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
    with them, each folder is packed, read back with all its files, and only then deleted.
"""
from __future__ import annotations

import io
import json
import os
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


def plain_backup(folder: Path, rows: int = 2) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "orders.csv").write_text("id,total\n" + "".join(f"{i},1.500\n" for i in range(rows)), encoding="utf-8")
    (folder / "manifest.json").write_text(json.dumps({"tables": {"orders": {"rows": rows}}}), encoding="utf-8")
    return folder


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
        assert yq.delivery_words().startswith("Afterwards, if every check passes: the brief by e-mail to 2 address(es)")
        paths.write_text(paths.ops_dir() / "delivery.json", json.dumps({"mode": "test", "test_to": ["a@b.invalid"]}))
        assert "a TEST copy of the brief by e-mail to 1 address(es)" in yq.delivery_words()
    with data_root():
        title, text = yq.box_text("151026_0900", {"drop": "Uplaods/151026", "verdicts": []}, notes=["ATTENTION: x."])
        lines = text.splitlines()
        assert "ATTENTION: x." in lines and lines[-1] == "Yes = load now. No = nothing changes."


@test("read-back: a whole zip passes, a damaged one does not, a 7z needs 7-Zip and its password")
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
        real_pw, real_root = yq_secrets.backup_password, yb.backup_root
        yq_secrets.backup_password = lambda env=None: ("an-invented-test-password", "process_environment")
        yb.backup_root = lambda: root / "store"
        try:
            said = []
            assert yb.legacy(True, say=said.append) == 0, said
            archive = root / "store" / "legacy" / "2026-09-24_test.7z"
            assert archive.is_file() and not folder.exists()
            ok, info = yb.read_back(archive, "an-invented-test-password", exe)
            assert ok and "Files: 2" in info, info
            assert yb.read_back(archive, "a wrong password", exe)[0] is False, "the names are hidden too"
        finally:
            yq_secrets.backup_password, yb.backup_root = real_pw, real_root


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
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
