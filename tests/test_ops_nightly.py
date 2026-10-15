"""The daily backup's starter (scripts/yq_nightly.py): the password reaches the backup job and nothing else; no
password or no 7-Zip means no job and exit 2; a code worktree, a virtual environment or the Microsoft Store Python
is refused with exit 3; the job's exit code is passed through with one plain line per run in nightly.log; the
job's plain work folders are never left behind; the job is stopped at the starter's own deadline; the printed task.

    python -m tests.test_ops_nightly

Same lightweight runner as the other suites (no pytest). The real backup job never runs here: every test hands
the starter a stand-in runner (two tests run the real runner on harmless children of their own, never the job).
No database (the addresses are pinned to ci.invalid before any import), no real secret (YQ_SECRETS_OFF=1; the
one password is invented), no window of this PC (the console tuck gets stand-in Win32 calls) and the backup store
is a temporary folder (YQ_BACKUP_DIR), never this PC's.

Covered:
  * the password is in the job's environment and nowhere else: not on its command line, not in what is printed
    or logged (even when the job's own output holds it, or a database address holds one), not in any file, not
    in this process's environment afterwards; the job runs in the project folder with UTF-8 forced;
  * no password, or no 7-Zip: the job is never started, exit 2, one dated line in the log per run;
  * a code worktree (the folder started from, or the code's own), a virtual environment or the Microsoft Store
    Python is refused with exit 3 before anything is read, started or written, --dry-run and --print-task too;
  * the job's exit code is passed through, and the line names the archive only from the job's own success line
    of a run that ended 0 or 2 (a failed run that names an old archive is a failure); a job that cannot be
    started, or a backup folder the job's rule refuses, ends with exit 1;
  * the job's plain work folders (work_<date>_<time>): one an earlier run left is removed at the start and one
    this run left after it ended (failed, stopped at the deadline, Ctrl+C), each named in the log (names only);
    a folder written to in the last minutes, a link or a junction, and anything named otherwise are left alone;
    one that cannot be removed is named, never claimed; a dry run only says what it would remove;
  * the deadline: the real runner stops a child that runs past it, together with what that child started, and
    says so; the starter's deadline comes before the task's own limit;
  * the console tuck, on stand-in Win32 calls: a console that a shell shares, a hidden one or none is never
    touched; its own visible console is minimised, handing the keyboard back when it was the active window;
  * --dry-run passes the job's own --dry-run and adds no line to the log;
  * --print-task prints ONE PowerShell line, read back here word by word: the task's name, Sunday to Thursday at
    13:05, the current user only while logged on, the project folder, this Python with -X utf8 -m
    scripts.yq_nightly, a missed run started later, a time limit, and nothing else (no user name, no password);
    PowerShell's own parser reads it without an error (SKIP where there is no PowerShell); it reads no secret,
    starts nothing and writes nothing;
  * the real runner: every output line as it comes (UTF-8, a bad byte replaced), the exit code back.
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
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
OLD_ARCHIVE = "yq-backup_2026-09-01_1305.7z"
DATED = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} start, \d{2}:\d{2}:\d{2} end, exit -?\d+: ")
STORE_PY = r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0\python.exe"


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


def done_lines(store: Path, archive: str = ARCHIVE, sealed: str = "AES-256 encrypted") -> list[str]:
    """What the job prints on a good run (its last line names the archive)."""
    return ["  orders                                  2 rows -> orders.csv",
            "extras: {'auth_users.csv': 2, 'storage_manifest.csv': 3}", "",
            f"Nightly backup: {store / archive} (1,024 bytes, {sealed}); pruned 0 old archive(s)."]


def work_folder(store: Path, name: str, age_s: float = 0.0) -> Path:
    """A plain work folder as the job makes one (a table in it), last written `age_s` seconds ago."""
    folder = store / name
    folder.mkdir(parents=True)
    (folder / "orders.csv").write_text("id,total\n1,1.500\n", encoding="utf-8")
    when = time.time() - age_s
    for p in (folder / "orders.csv", folder):
        os.utime(p, (when, when))
    return folder


def link_to(target: Path, link: Path) -> bool:
    """A link named `link` to the folder `target`: a junction on Windows (no rights needed), a symlink elsewhere.
    False where neither can be made here."""
    try:
        if sys.platform == "win32":
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
        else:
            os.symlink(target, link, target_is_directory=True)
    except (OSError, AttributeError, ImportError):
        return False
    return True


class FakeWin:
    """Stand-in Win32 calls for the console tuck: what Windows would answer, and every window it was asked to show."""

    def __init__(self, attached: int = 1, window: int | None = 11, visible: bool = True, foreground: int | None = 11,
                 fails: bool = False):
        self._attached, self._window, self._visible, self._fg, self._fails = attached, window, visible, foreground, fails
        self.shown: list[tuple[int, int]] = []

    def attached(self) -> int:
        if self._fails:
            raise OSError("GetConsoleProcessList failed")
        return self._attached

    def console_window(self):
        return self._window

    def visible(self, window) -> bool:
        return self._visible

    def foreground(self):
        return self._fg

    def show(self, window, how: int) -> None:
        self.shown.append((window, how))


class world:
    """A project folder and a backup store of their own, the invented password (or none), 7-Zip (or none), stand-in
    Win32 calls (a console of its own: the tuck shows on every real run) and a stand-in for the job that records
    what it was handed, makes the job's work folder `makes` when one is named, prints `lines` and ends with `code`
    (`stopped` when the runner had to stop it), or raises."""

    def __init__(self, password: str | None = PW, exe: str | None = "7z.exe", code: int = 0,
                 lines: list[str] | None = None, raises: BaseException | None = None, store_refused: bool = False,
                 makes: str | None = None, stopped: str | None = None):
        self.password, self.exe, self.code, self.raises = password, exe, code, raises
        self.lines, self.store_refused, self.makes, self.stopped = lines, store_refused, makes, stopped
        self.calls: list[dict] = []
        self.secret_reads = 0
        self.win = FakeWin()

    def __enter__(self) -> "world":
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.base = Path(self._tmp.name)
        self.root, self.store = self.base / "project", self.base / "store"
        self.root.mkdir()
        self._real = (yq_secrets.backup_password, yn.find_7z, yn.backup_root, yn.run_child, yn._win32)

        def backup_password(env=None):
            self.secret_reads += 1
            return (self.password, "credential_manager") if self.password else (None, None)

        def backup_root():
            if self.store_refused:
                raise SystemExit(f"REFUSING: {self.store} is inside a synced folder (OneDrive).")
            return self.store

        def runner(argv, *, env, cwd, on_line, deadline_s):
            self.calls.append({"argv": list(argv), "env": dict(env), "cwd": Path(cwd), "deadline_s": deadline_s})
            if self.makes:
                work_folder(self.store, self.makes)
            if self.raises is not None:
                raise self.raises
            for line in (done_lines(self.store) if self.lines is None else self.lines):
                on_line(line)
            return yn.Ended(self.code, self.stopped)

        yq_secrets.backup_password = backup_password
        yn.find_7z = lambda: self.exe
        yn.backup_root = backup_root
        yn.run_child = runner
        yn._win32 = lambda: self.win
        return self

    def __exit__(self, *exc):
        (yq_secrets.backup_password, yn.find_7z, yn.backup_root, yn.run_child, yn._win32) = self._real
        self._tmp.cleanup()
        return False

    @property
    def tucked(self) -> int:
        return len(self.win.shown)

    def main(self, *argv: str, cwd: Path | None = None, root: Path | None = None, python: str = "py.exe",
             prefixes: tuple[str, str] = ("C:/Python", "C:/Python")) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = yn.main(list(argv), cwd=cwd or self.root, root=root or self.root, python=python, prefixes=prefixes)
        return code, out.getvalue()

    def log(self) -> list[str]:
        f = self.store / yn.LOG_NAME
        return f.read_text(encoding="utf-8").splitlines() if f.is_file() else []

    def files_text(self) -> str:
        """Every file this world holds, read as text: what a secret must never be written into."""
        return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in self.base.rglob("*") if p.is_file())


# ── reading the printed PowerShell line back ──────────────────────────────────

def ps_words(text: str) -> list[tuple[str, str]]:
    """The words of one PowerShell command line, as the printed task uses them: ('q', text) for a '...' literal
    ('' inside it is one '), ('p', inner text) for a (...) group, ('w', word) for a bare word."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c == "'":
            j, buf = i + 1, []
            while True:
                if j >= n:
                    raise ValueError("a quote that is never closed")
                if text[j] == "'":
                    if j + 1 < n and text[j + 1] == "'":
                        buf.append("'")
                        j += 2
                        continue
                    break
                buf.append(text[j])
                j += 1
            out.append(("q", "".join(buf)))
            i = j + 1
        elif c == "(":
            depth, j, quoted = 0, i, False
            while j < n:
                ch = text[j]
                if ch == "'":
                    quoted = not quoted                   # '' flips twice: still right
                elif not quoted and ch == "(":
                    depth += 1
                elif not quoted and ch == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            else:
                raise ValueError("a ( that is never closed")
            out.append(("p", text[i + 1:j]))
            i = j + 1
        else:
            j = i
            while j < n and not text[j].isspace():
                j += 1
            out.append(("w", text[i:j]))
            i = j
    return out


def ps_call(text: str) -> tuple[str, dict]:
    """(command, {'-Name': value}): a value is the text of a literal or a bare word, a (command, params) pair for a
    group that starts with a command, the group's own text otherwise, or True for a switch."""
    words = ps_words(text.strip())
    kind, name = words[0]
    assert kind == "w" and re.fullmatch(r"[A-Za-z]+-[A-Za-z]+", name), f"not a command: {name!r}"
    params: dict = {}
    k = 1
    while k < len(words):
        kind, word = words[k]
        assert kind == "w" and word.startswith("-"), f"not a parameter: {word!r}"
        assert word not in params, f"{word} twice"
        nxt = words[k + 1] if k + 1 < len(words) else None
        if nxt is None or (nxt[0] == "w" and nxt[1].startswith("-")):
            params[word] = True
            k += 1
            continue
        value = nxt[1]
        if nxt[0] == "p" and re.match(r"[A-Za-z]+-[A-Za-z]+(\s|$)", value.strip()):
            params[word] = ps_call(value)
        else:
            params[word] = value
        k += 2
    return name, params


def powershell_parse_errors(line: str) -> int | None:
    """How many errors PowerShell's own parser finds in `line` (it only parses: nothing runs), or None where there
    is no PowerShell."""
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        return None
    script = ("$e = $null; $null = [System.Management.Automation.Language.Parser]::ParseInput("
              "$env:YQ_PS_LINE, [ref]$null, [ref]$e); $e.Count")
    r = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True,
                       env=dict(os.environ, YQ_PS_LINE=line), stdin=subprocess.DEVNULL, timeout=120)
    return int(r.stdout.strip().splitlines()[-1])


# ── the tests ─────────────────────────────────────────────────────────────────

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
        assert call["deadline_s"] == yn.DEADLINE_S, "the runner is given the starter's deadline"
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


@test("a virtual environment or the Microsoft Store Python is refused with exit 3 before anything; no task is "
      "printed for one")
def _():
    for py in (STORE_PY, r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\python.exe",
               r"C:\Program Files\WindowsApps\PythonSoftwareFoundation.Python.3.12_3.12.1008.0_x64__q\python3.12.exe"):
        said = yn.interpreter_refusal(py, r"C:\Python", r"C:\Python")
        assert said and "Microsoft Store" in said and "Nothing was started" in said, py
    said = yn.interpreter_refusal(r"C:\proj\.venv\Scripts\python.exe", r"C:\proj\.venv", r"C:\Python314")
    assert said and "virtual environment" in said and r"C:\Python314" in said and "Nothing was started" in said, said
    for fine in (r"C:\Python314\python.exe", "/usr/bin/python3", r"C:\Tools\MyWindowsAppsCopy\python.exe"):
        assert yn.interpreter_refusal(fine, "/p", "/p") is None, fine
    with world() as w:
        for argv in ((), ("--dry-run",), ("--print-task",)):
            for python, prefixes in ((STORE_PY, ("C:/Python", "C:/Python")),
                                     (r"C:\proj\.venv\Scripts\python.exe", (r"C:\proj\.venv", r"C:\Python314"))):
                code, out = w.main(*argv, python=python, prefixes=prefixes)
                assert code == 3 and "Nothing was started" in out, (argv, out)
                assert "Register-ScheduledTask" not in out, out
        assert w.calls == [] and w.secret_reads == 0 and w.tucked == 0 and w.log() == [] and not w.store.exists()


@test("the job's exit code is passed through; the archive is named only from the job's own success line of a run "
      "that ended 0 or 2, and a failed run that names an old archive is logged as a failure")
def _():
    cases = (
        (0, None, "exit 0: " + ARCHIVE + ", encrypted"),
        (2, ["WARNING: THIS BACKUP IS NOT ENCRYPTED (7-Zip was not found).",
             "Nightly backup: C:\\s\\yq-backup_2026-10-15_1305.zip (9 bytes, NOT ENCRYPTED); pruned 0 old archive(s)."],
         "exit 2: yq-backup_2026-10-15_1305.zip, NOT ENCRYPTED"),
        (1, ["Traceback (most recent call last):", '  File "x.py", line 1, in <module>', "",
             "SystemExit: 7-Zip failed (exit 2): disk full"],
         "exit 1: failed: SystemExit: 7-Zip failed (exit 2): disk full"),
        (1, ["  orders   2 rows -> orders.csv", "Traceback (most recent call last):",
             f"PermissionError: [WinError 32] The file is in use: 'C:\\s\\{OLD_ARCHIVE}'"],
         f"exit 1: failed: PermissionError: [WinError 32] The file is in use: 'C:\\s\\{OLD_ARCHIVE}'"),
        (1, done_lines(Path("C:/s"))[-1:] + ["Traceback (most recent call last):", "OSError: [Errno 22] Invalid argument"],
         "exit 1: failed: OSError: [Errno 22] Invalid argument"),
        (7, [], "exit 7: failed, and the job printed nothing"),
        (0, [f"pruned {OLD_ARCHIVE}", "no success line at all"], "exit 0: done, but the job printed no success line"),
    )
    for code, lines, want in cases:
        with world(code=code, lines=lines) as w:
            got, out = w.main()
            assert got == code, (code, got, out)
            [line] = w.log()
            assert DATED.match(line) and want in line, (want, line)
            if code not in (0, 2) or "no success line" in want:
                assert "encrypted" not in line.lower() and "password from" not in line, line
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


@test("a plain work folder an earlier run left is removed at the start and named in the log (names only); one "
      "written to in the last minutes, a link or a junction, and anything named otherwise are left alone")
def _():
    with world() as w:
        old = work_folder(w.store, "work_2026-10-14_1305", age_s=3 * 3600)
        older = work_folder(w.store, "work_2026-10-13_1305", age_s=27 * 3600)
        fresh = work_folder(w.store, "work_2026-10-15_1300", age_s=60)
        others = [w.store / n for n in ("work_notes", "work_2026-10-15_1305x", "release", "preload", "Work_2026-10-11_1305")]
        for d in others:
            d.mkdir()
            os.utime(d, (time.time() - 3 * 3600,) * 2)
        named_file = w.store / "work_2026-10-12_1305.txt"
        named_file.write_text("x", encoding="utf-8")
        outside = work_folder(w.base, "outside", age_s=3 * 3600)
        link = w.store / "work_2026-10-10_1305"
        linked = link_to(outside, link)
        code, out = w.main()
        assert code == 0, out
        assert not old.exists() and not older.exists(), "the leftovers are gone"
        assert fresh.exists() and (fresh / "orders.csv").exists(), "a folder written to minutes ago is left"
        assert all(d.exists() for d in others) and named_file.exists(), "nothing named otherwise is touched"
        if linked:
            assert (outside / "orders.csv").exists() and os.path.lexists(link), "a link and what it points at stay"
        else:
            print("      SKIP: no link or junction can be made here")
        [line] = w.log()
        assert "removed leftover plain folder(s): work_2026-10-13_1305, work_2026-10-14_1305" in line, line
        assert "left in place, written to in the last 15 minutes: work_2026-10-15_1300" in line, line
        assert "orders.csv" not in line and "work_2026-10-10_1305" not in line and "work_notes" not in line, line
        assert "removed leftover plain folder(s)" in out, out


@test("after a run that did not finish (failed, stopped at the deadline, Ctrl+C) its own plain work folder is "
      "removed and named; a folder that was there before the job started is not")
def _():
    for kind, setup, want in (
            ("failed", dict(code=1, lines=["SystemExit: 7-Zip failed (exit 2): disk full"]), "exit 1: failed: "),
            ("deadline", dict(code=1, lines=["  orders   2 rows -> orders.csv"], stopped="deadline"),
             f"exit 1: stopped after {yn.DEADLINE_S // 60} minutes, the backup did not finish"),
            ("ctrl+c", dict(code=1, lines=[], stopped="interrupted"), "exit 1: stopped before the end (Ctrl+C)")):
        with world(makes="work_2026-10-15_1305", **setup) as w:
            fresh = work_folder(w.store, "work_2026-10-15_1301", age_s=30)    # another backup's, still being written
            code, out = w.main()
            assert code == 1, (kind, out)
            assert not (w.store / "work_2026-10-15_1305").exists(), f"{kind}: this run's plain folder is left"
            assert fresh.exists(), f"{kind}: a folder from before the job started was removed"
            [line] = w.log()
            assert want in line and "removed this run's plain folder(s): work_2026-10-15_1305" in line, (kind, line)
    with world(makes="work_2026-10-15_1305") as w:                            # a good run leaves none behind
        code, _out = w.main()
        [line] = w.log()
        assert code == 0 and not (w.store / "work_2026-10-15_1305").exists() and "removed this run's" in line, line


@test("a plain work folder that cannot be removed is named, never claimed; a dry run only says what it would remove")
def _():
    real = yn._remove
    yn._remove = lambda folder: ["orders.csv"]          # "Access is denied", every try
    try:
        with world() as w:
            stuck = work_folder(w.store, "work_2026-10-14_1305", age_s=3 * 3600)
            code, out = w.main()
            [line] = w.log()
            assert code == 0 and stuck.exists(), out
            assert "could NOT remove plain folder(s): work_2026-10-14_1305" in line, line
            assert "removed leftover" not in line, line
    finally:
        yn._remove = real
    with world(lines=["would write C:\\s\\yq-backup_2026-10-15_1305.7z (encrypted: True)"]) as w:
        old = work_folder(w.store, "work_2026-10-14_1305", age_s=3 * 3600)
        code, out = w.main("--dry-run")
        assert code == 0 and old.exists() and w.log() == [], out
        assert "would remove leftover plain folder(s): work_2026-10-14_1305" in out, out


@test("the deadline: the real runner stops a child that runs past it together with what that child started, and "
      "says so; the starter's deadline comes before the task's own limit")
def _():
    assert 0 < yn.DEADLINE_S <= yn.TASK_LIMIT_HOURS * 3600 - 5 * 60, "time to clean and log before the task's limit"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        beat = Path(tmp) / "heartbeat.txt"
        grandchild = ("import sys, time, pathlib; p = pathlib.Path(sys.argv[1]); t = time.time()\n"
                      "while time.time() - t < 60:\n    p.write_text(str(time.time())); time.sleep(0.1)")
        code = ("import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]]); "
                "print('started', flush=True); time.sleep(60)")
        lines: list[str] = []
        t0 = time.monotonic()
        ended = yn.run_child([sys.executable, "-c", code, grandchild, str(beat)], env=dict(os.environ),
                             cwd=Path(tmp), on_line=lines.append, deadline_s=3)
        took = time.monotonic() - t0
        assert ended.stopped == "deadline" and took < 30, (ended, took)
        assert "started" in lines, lines
        for _wait in range(50):                          # the grandchild has begun its beat
            if beat.exists():
                break
            time.sleep(0.1)
        time.sleep(0.5)
        first = beat.read_text(encoding="utf-8") if beat.exists() else ""
        time.sleep(1.0)
        later = beat.read_text(encoding="utf-8") if beat.exists() else ""
        assert first == later, "what the child started (7-Zip, say) is still running after the deadline"


@test("the console tuck, on stand-in Win32 calls: a console a shell shares, a hidden one or none is never touched; "
      "its own visible console is minimised, the keyboard handed back when it was the active window")
def _():
    for fake in (FakeWin(attached=2), FakeWin(attached=3, foreground=99), FakeWin(window=None), FakeWin(window=0),
                 FakeWin(visible=False), FakeWin(fails=True)):
        assert yn.tuck_console_away(win=fake) is False and fake.shown == [], vars(fake)
    active = FakeWin(foreground=11)
    assert yn.tuck_console_away(win=active) is True and active.shown == [(11, yn.SW_MINIMIZE)], active.shown
    behind = FakeWin(foreground=99)
    assert yn.tuck_console_away(win=behind) is True and behind.shown == [(11, yn.SW_SHOWMINNOACTIVE)], behind.shown
    assert (yn.SW_MINIMIZE, yn.SW_SHOWMINNOACTIVE) == (6, 7), "the Win32 values"


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


@test("--print-task prints ONE PowerShell line, read back word by word: name, Sunday to Thursday at 13:05, the current "
      "user only while logged on, the folder, this Python, a missed run started later, a limit; never a password; "
      "PowerShell's own parser reads it (SKIP without PowerShell); it reads, starts and writes nothing")
def _():
    with world() as w:
        code, out = w.main("--print-task")
        assert code == 0 and w.calls == [] and w.secret_reads == 0 and w.log() == [] and not w.store.exists(), out
        assert PW not in out and "YQ_BACKUP_PASSWORD" not in out, out
        [printed] = [ln for ln in out.splitlines() if ln.startswith("Register-ScheduledTask ")]
        folder = str(w.root)
    for line, python, where in ((printed, "py.exe", folder),
                                (yn.task_command(r"C:\Python314\python.exe", r"C:\Users\x\it's here"),
                                 r"C:\Python314\python.exe", r"C:\Users\x\it's here")):
        name, p = ps_call(line)
        assert name == "Register-ScheduledTask", name
        assert set(p) == {"-TaskName", "-Description", "-Action", "-Trigger", "-Principal", "-Settings", "-Force"}, p
        assert p["-TaskName"] == "YQ Daily Backup" and p["-Force"] is True and isinstance(p["-Description"], str)
        assert p["-Action"] == ("New-ScheduledTaskAction", {"-Execute": python, "-Argument": "-X utf8 -m scripts.yq_nightly",
                                                            "-WorkingDirectory": where}), p["-Action"]
        assert p["-Trigger"] == ("New-ScheduledTaskTrigger", {
            "-Weekly": True, "-WeeksInterval": "1", "-DaysOfWeek": "Sunday,Monday,Tuesday,Wednesday,Thursday",
            "-At": "13:05"}), p["-Trigger"]
        assert p["-Principal"] == ("New-ScheduledTaskPrincipal", {
            "-UserId": "[Security.Principal.WindowsIdentity]::GetCurrent().Name", "-LogonType": "Interactive",
            "-RunLevel": "Limited"}), "the current user, only while logged on, not elevated: " + repr(p["-Principal"])
        assert p["-Settings"] == ("New-ScheduledTaskSettingsSet", {
            "-StartWhenAvailable": True, "-AllowStartIfOnBatteries": True, "-DontStopIfGoingOnBatteries": True,
            "-ExecutionTimeLimit": ("New-TimeSpan", {"-Hours": str(yn.TASK_LIMIT_HOURS)})}), p["-Settings"]
        assert PW not in line and "YQ_BACKUP_PASSWORD" not in line
    errors = powershell_parse_errors(printed)
    if errors is None:
        print("      SKIP: no PowerShell here to parse the line")
    else:
        assert errors == 0, f"PowerShell finds {errors} error(s) in the printed line"
    text = yn.task_text(r"C:\Python314\python.exe", r"C:\Users\x\YQ Bahrain Mobile Accessories", r"C:\s\nightly.log")
    assert 'schtasks /Run /TN "YQ Daily Backup"' in text, text
    assert 'schtasks /Query /TN "YQ Daily Backup" /V /FO LIST' in text, text
    assert "Nothing has been created" in text and "PowerShell" in text and r"C:\s\nightly.log" in text, text
    assert "Sunday to Thursday at 13:05" in text and "only while you are logged on" in text, text


@test("scripts/nightly_backup.cmd (read as text, never run) points at the starter's task, not the old setx and 02:30 "
      "setup, and still runs the job by hand as before")
def _():
    text = (ROOT / "scripts" / "nightly_backup.cmd").read_text(encoding="utf-8")
    assert "python -m scripts.yq_nightly --print-task" in text and "YQ Daily Backup" in text, text
    for old in ("setx", "02:30", "YQ nightly backup", "Task Scheduler runs this file"):
        assert old not in text, f"the .cmd still teaches {old!r}"
    assert "python -m scripts.nightly_backup" in text and "yq-backups" in text, "what tests/test_r7d_ops.py reads"


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
        ended = yn.run_child([sys.executable, "-c", code], env=dict(os.environ, YQ_T="from-its-env"), cwd=here,
                             on_line=lines.append, deadline_s=120)
    assert ended == yn.Ended(5, None), (ended, lines)
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
