"""The daily backup's starter (scripts/yq_nightly.py): the password reaches the backup job and nothing else; no
password or no 7-Zip means no job and exit 2; a code worktree is refused with exit 3; the job's exit code is
passed through with one plain line per run in nightly.log; the printed scheduled task.

    python -m tests.test_ops_nightly

Same lightweight runner as the other suites (no pytest). The real backup job never runs here: every test hands
the starter a stand-in runner (one test runs the real runner on a harmless child of its own, never the job). No
database (the addresses are pinned to ci.invalid before any import), no real secret (YQ_SECRETS_OFF=1; the one
password is invented) and the backup store is a temporary folder (YQ_BACKUP_DIR), never this PC's.

Covered:
  * the password is in the job's environment and nowhere else: not on its command line, not in what is printed
    or logged (even when the job's own output holds it, or a database address holds one), not in any file, not
    in this process's environment afterwards; the job runs in the project folder with UTF-8 forced;
  * no password, or no 7-Zip: the job is never started, exit 2, one dated line in the log per run;
  * a code worktree (the folder started from, or the code's own) is refused with exit 3 before anything is
    read, started or written, --dry-run and --print-task included;
  * the job's exit code is passed through, and the line names the archive or the failure; a job that cannot
    be started, or a backup folder the job's rule refuses, ends with exit 1;
  * --dry-run passes the job's own --dry-run and adds no line to the log;
  * --print-task prints the task (its name, Sunday to Thursday at 13:05, the current user only while logged on,
    the project folder, this Python with -X utf8 -m scripts.yq_nightly, a missed run started later, the two
    schtasks checks) and never a password; it reads no secret, starts nothing and writes nothing;
  * the real runner: every output line as it comes (UTF-8, a bad byte replaced), the exit code back.
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"
os.environ.pop("YQ_MAIN_CHECKOUT", None)
os.environ.pop("YQ_BACKUP_PASSWORD", None)
os.environ["YQ_SECRETS_OFF"] = "1"
STORE = tempfile.mkdtemp(prefix="yq-test-nightly-")
os.environ["YQ_BACKUP_DIR"] = STORE       # never this PC's %LOCALAPPDATA%\yq-backups

from scripts import yq_nightly as yn  # noqa: E402
from scripts import yq_secrets  # noqa: E402

TESTS: list[tuple[str, object]] = []

PW = "an-invented-nightly-password"       # the only password any test hands in (YQ_SECRETS_OFF=1: none is read)
DB_PW = "an-invented-db-password"
ARCHIVE = "yq-backup_2026-10-15_1305.7z"
DATED = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} start, \d{2}:\d{2}:\d{2} end, exit -?\d+: ")


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


def done_lines(store: Path, archive: str = ARCHIVE, sealed: str = "AES-256 encrypted") -> list[str]:
    """What the job prints on a good run (its last line names the archive)."""
    return ["  orders                                  2 rows -> orders.csv",
            "extras: {'auth_users.csv': 2, 'storage_manifest.csv': 3}",
            f"Nightly backup: {store / archive} (1,024 bytes, {sealed}); pruned 0 old archive(s)."]


class world:
    """A project folder and a backup store of their own, the invented password (or none), 7-Zip (or none), and a
    stand-in for the job that records what it was handed, prints `lines` and ends with `code` (or raises)."""

    def __init__(self, password: str | None = PW, exe: str | None = "7z.exe", code: int = 0,
                 lines: list[str] | None = None, raises: BaseException | None = None, store_refused: bool = False):
        self.password, self.exe, self.code, self.raises = password, exe, code, raises
        self.lines, self.store_refused = lines, store_refused
        self.calls: list[dict] = []
        self.secret_reads = 0
        self.tucked = 0

    def __enter__(self) -> "world":
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(self._tmp.name)
        self.root, self.store = base / "project", base / "store"
        self.root.mkdir()
        self._real = (yq_secrets.backup_password, yn.find_7z, yn.backup_root, yn.run_child, yn.tuck_console_away)

        def backup_password(env=None):
            self.secret_reads += 1
            return (self.password, "credential_manager") if self.password else (None, None)

        def backup_root():
            if self.store_refused:
                raise SystemExit(f"REFUSING: {self.store} is inside a synced folder (OneDrive).")
            return self.store

        def runner(argv, *, env, cwd, on_line):
            self.calls.append({"argv": list(argv), "env": dict(env), "cwd": Path(cwd)})
            if self.raises is not None:
                raise self.raises
            for line in (done_lines(self.store) if self.lines is None else self.lines):
                on_line(line)
            return self.code

        def tuck():
            self.tucked += 1
            return False
        yq_secrets.backup_password = backup_password
        yn.find_7z = lambda: self.exe
        yn.backup_root = backup_root
        yn.run_child = runner
        yn.tuck_console_away = tuck
        return self

    def __exit__(self, *exc):
        (yq_secrets.backup_password, yn.find_7z, yn.backup_root, yn.run_child, yn.tuck_console_away) = self._real
        self._tmp.cleanup()
        return False

    def main(self, *argv: str, cwd: Path | None = None, root: Path | None = None) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = yn.main(list(argv), cwd=cwd or self.root, root=root or self.root, python="py.exe")
        return code, out.getvalue()

    def log(self) -> list[str]:
        f = self.store / yn.LOG_NAME
        return f.read_text(encoding="utf-8").splitlines() if f.is_file() else []

    def files_text(self) -> str:
        """Every file this world holds, read as text: what a secret must never be written into."""
        base = Path(self._tmp.name)
        return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in base.rglob("*") if p.is_file())


@test("the password reaches the job's environment only: never its command line, the output, the log, a file or "
      "this process's environment; the job runs in the project folder with UTF-8 forced")
def _():
    before = dict(os.environ)
    echoed = done_lines(Path("C:/store"))[:2] + [
        f"a careless line that holds the password: -p{PW}",
        f"connection to postgresql://postgres.x:{DB_PW}@db.ci.invalid:5432/postgres refused",
        done_lines(Path("C:/store"))[-1]]
    with world(lines=echoed) as w:
        code, out = w.main()
        assert code == 0, out
        [call] = w.calls
        assert call["argv"] == ["py.exe", "-X", "utf8", "-m", "scripts.nightly_backup"], call["argv"]
        assert not any(PW in a for a in call["argv"]), "the password on a command line"
        assert call["env"]["YQ_BACKUP_PASSWORD"] == PW, "the job reads it from its environment"
        assert call["env"]["PYTHONUTF8"] == "1" and "PYTHONIOENCODING" not in call["env"]
        assert call["cwd"] == w.root, "the job starts in the project folder"
        assert PW not in out and DB_PW not in out and out.count(yn.HIDDEN) == 2, out
        assert "please leave it open" in out and w.tucked == 1, out
        [line] = w.log()
        assert DATED.match(line), line
        assert "exit 0: " + ARCHIVE + ", encrypted (password from credential_manager)" in line, line
        assert PW not in w.files_text() and DB_PW not in w.files_text(), "a secret was written to a file"
    assert dict(os.environ) == before, "this process's environment changed"
    assert not any(PW in v for v in os.environ.values())


@test("no backup password: the job is never started, exit 2, one dated line in the log for each run")
def _():
    with world(password=None) as w:
        code, out = w.main()
        assert code == 2 and w.calls == [], (code, w.calls)
        assert "NOT started: no backup password was found" in out and "none was made" in out, out
        code, _out = w.main()
        lines = w.log()
        assert code == 2 and len(lines) == 2, lines
        assert all(DATED.match(ln) and "exit 2: NOT started: no backup password was found" in ln for ln in lines), lines
        assert all("nothing was backed up" in ln for ln in lines), lines


@test("no 7-Zip (with or without the password): the job is never started, exit 2, the line says what is missing")
def _():
    with world(exe=None) as w:
        code, out = w.main()
        assert code == 2 and w.calls == [] and "7-Zip is not installed" in out, out
        [line] = w.log()
        assert "exit 2: NOT started: 7-Zip is not installed" in line, line
    with world(password=None, exe=None) as w:
        code, out = w.main()
        assert code == 2 and w.calls == [], out
        [line] = w.log()
        assert "no backup password was found" in line and "and 7-Zip is not installed" in line, line


@test("a code worktree (the folder started from, or the code's own) is refused with exit 3 before anything is read, "
      "started or written; --dry-run and --print-task too")
def _():
    for bad in (r"C:\Users\x\Proj\.claude\worktrees\nb1-nightly", "C:/Users/x/Proj/.CLAUDE/Worktrees/nb1/scripts",
                "/home/x/proj/.claude/worktrees/a/"):
        said = yn.worktree_refusal(bad)
        assert said and "code worktree" in said and "Nothing was started" in said, bad
    for fine in (r"C:\Users\x\YQ Bahrain Mobile Accessories", "/home/runner/work/repo/repo",
                 r"C:\x\.claude\worktreesX\a", r"C:\x\claude\worktrees\a"):
        assert yn.worktree_refusal(fine) is None, fine
    with world() as w:
        tree = w.root / ".claude" / "worktrees" / "nb1-nightly"
        for argv in ((), ("--dry-run",), ("--print-task",)):
            for cwd, root in ((tree, w.root), (w.root, tree)):
                code, out = w.main(*argv, cwd=cwd, root=root)
                assert code == 3 and "code worktree" in out, (argv, out)
                assert "Register-ScheduledTask" not in out, out
        assert w.calls == [] and w.secret_reads == 0 and w.tucked == 0 and w.log() == []
        assert not w.store.exists(), "nothing written"


@test("the job's exit code is passed through, and its line names the archive or why it failed")
def _():
    cases = (
        (0, None, "exit 0: " + ARCHIVE + ", encrypted"),
        (2, ["WARNING: THIS BACKUP IS NOT ENCRYPTED (7-Zip was not found).",
             "Nightly backup: C:\\s\\yq-backup_2026-10-15_1305.zip (9 bytes, NOT ENCRYPTED); pruned 0 old archive(s)."],
         "exit 2: yq-backup_2026-10-15_1305.zip, NOT ENCRYPTED"),
        (1, ["Traceback (most recent call last):", '  File "x.py", line 1, in <module>', "",
             "SystemExit: 7-Zip failed (exit 2): disk full"],
         "exit 1: failed: SystemExit: 7-Zip failed (exit 2): disk full"),
        (7, [], "exit 7: failed, and the job printed nothing"),
        (0, ["no archive line at all"], "exit 0: done, but the job named no archive"),
    )
    for code, lines, want in cases:
        with world(code=code, lines=lines) as w:
            got, out = w.main()
            assert got == code, (code, got, out)
            [line] = w.log()
            assert DATED.match(line) and want in line, (want, line)
            assert f"YQ daily backup: exit {code}: " in out, out
    with world(raises=PermissionError(13, "Access is denied")) as w:
        got, out = w.main()
        [line] = w.log()
        assert got == 1 and "exit 1: NOT started: the job could not be started (PermissionError)" in line, line
    with world(raises=KeyboardInterrupt()) as w:
        got, _out = w.main()
        [line] = w.log()
        assert got == 1 and "exit 1: stopped before the end (Ctrl+C)" in line, line
    with world(store_refused=True) as w:
        got, out = w.main()
        assert got == 1 and w.calls == [] and w.secret_reads == 0, out
        assert "REFUSING" in out and "Nothing was started." in out and not w.store.exists(), out


@test("--dry-run passes the job's own --dry-run (password in its environment only) and adds no line to the log")
def _():
    with world(lines=["would write C:\\s\\yq-backup_2026-10-15_1305.7z (encrypted: True)",
                      "would delete 0 old archive(s): -"]) as w:
        code, out = w.main("--dry-run")
        [call] = w.calls
        assert code == 0 and call["argv"] == ["py.exe", "-X", "utf8", "-m", "scripts.nightly_backup", "--dry-run"]
        assert call["env"]["YQ_BACKUP_PASSWORD"] == PW and PW not in out
        assert "would write" in out and "Nothing was written." in out, out
        assert w.log() == [] and not w.store.exists() and w.tucked == 0, "a dry run wrote something"
    with world(password=None) as w:
        code, out = w.main("--dry-run")
        assert code == 2 and w.calls == [] and w.log() == [] and "NOT started" in out, out


@test("--print-task prints the task (name, Sunday to Thursday at 13:05, the current user only while logged on, the "
      "folder, this Python, a missed run started later, the checks) and never a password; it reads, starts and "
      "writes nothing")
def _():
    with world() as w:
        code, out = w.main("--print-task")
        assert code == 0 and w.calls == [] and w.secret_reads == 0 and w.log() == [] and not w.store.exists(), out
        assert PW not in out and "YQ_BACKUP_PASSWORD" not in out, out
        [cmd] = [ln for ln in out.splitlines() if ln.startswith("Register-ScheduledTask ")]
        assert cmd == yn.task_command("py.exe", w.root), "the line printed is the command, whole"
    folder = r"C:\Users\x\YQ Bahrain Mobile Accessories"
    cmd = yn.task_command(r"C:\Python314\python.exe", folder)
    assert "\n" not in cmd and cmd.startswith("Register-ScheduledTask -TaskName 'YQ Daily Backup' ")
    assert cmd.endswith(" -Force"), "it replaces a task of the same name"
    assert "-Weekly -WeeksInterval 1 -DaysOfWeek Sunday,Monday,Tuesday,Wednesday,Thursday -At '13:05'" in cmd, cmd
    assert "Friday" not in cmd and "Saturday" not in cmd, cmd
    assert ("-UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive "
            "-RunLevel Limited") in cmd, "the current user, only while logged on, not elevated"
    for never in ("-Password", "-User ", "S4U", "ServiceAccount", "Highest", "SYSTEM"):
        assert never not in cmd, never
    assert (r"-Execute 'C:\Python314\python.exe' -Argument '-X utf8 -m scripts.yq_nightly' "
            f"-WorkingDirectory '{folder}'") in cmd, cmd
    assert "-StartWhenAvailable" in cmd and "-ExecutionTimeLimit (New-TimeSpan -Hours 1)" in cmd, cmd
    assert "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries" in cmd, cmd
    assert yn.ps_quote("C:\\it's here") == "'C:\\it''s here'" and yn.ps_quote("a\u2019b") == "'a\u2019\u2019b'"
    text = yn.task_text(r"C:\Python314\python.exe", folder, r"C:\s\nightly.log")
    assert 'schtasks /Run /TN "YQ Daily Backup"' in text, text
    assert 'schtasks /Query /TN "YQ Daily Backup" /V /FO LIST' in text, text
    assert "Nothing has been created" in text and "PowerShell" in text and r"C:\s\nightly.log" in text, text
    assert "Sunday to Thursday at 13:05" in text and "only while you are logged on" in text, text


@test("the real runner (on a harmless child of its own, never the job): each line as it comes, a bad byte "
      "replaced, the child's environment and folder, the exit code back")
def _():
    code = ("import os, sys; print('one', flush=True); print(os.environ['YQ_T'], flush=True); "
            "print(os.path.basename(os.getcwd()), flush=True); "
            "sys.stdout.buffer.write(b'caf\\xe9\\n'); sys.stdout.flush(); sys.stderr.write('two\\n'); sys.exit(5)")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        here = Path(tmp) / "child-folder"
        here.mkdir()
        lines: list[str] = []
        got = yn.run_child([sys.executable, "-c", code], env=dict(os.environ, YQ_T="from-its-env"), cwd=here,
                           on_line=lines.append)
    assert got == 5, (got, lines)
    assert lines[:3] == ["one", "from-its-env", "child-folder"], lines
    assert "caf\ufffd" in lines and "two" in lines and len(lines) == 5, lines
    assert "YQ_T" not in os.environ, "the child's environment is its own"


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
    shutil.rmtree(STORE, ignore_errors=True)
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
