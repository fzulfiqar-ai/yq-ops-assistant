"""python -m scripts.yq_nightly : starts the daily encrypted backup of the live database (Task Scheduler runs it).

    python -m scripts.yq_nightly               run the backup now: what the scheduled task runs
    python -m scripts.yq_nightly --dry-run     the job's own dry run: where it would write, what it would prune
    python -m scripts.yq_nightly --print-task  print the command that creates the scheduled task (creates nothing)

The backup itself is scripts/nightly_backup.py (the job: a read-only snapshot of every table, packed into ONE
7-Zip AES-256 archive in %LOCALAPPDATA%\\yq-backups, the newest of each of the last 14 days and 8 weeks kept).
The job reads its password from its own environment only; this starter puts it there, from Windows Credential
Manager, so nothing has to be set with setx. One run, in order:

  1. Refuses with exit 3 when the folder it was started from, or this program's own folder, is a code worktree
     (.claude/worktrees), or when this Python is a virtual environment or the Microsoft Store one (WindowsApps):
     a scheduled task would lose either. The backup runs only from the project folder.
  2. Removes the plain work folders (work_<date>_<time>) that an earlier run of the job left in the backup
     folder when it did not finish; a folder written to in the last 15 minutes is left (a backup may still be
     running). Only folders named exactly like the job's own, never a link or a junction, never anything else.
  3. Reads the backup password where scripts/yq_secrets.py looks (Credential Manager first) and looks for 7-Zip.
     Without either the job is NOT started, so no backup is ever made unencrypted: it says so, adds one dated
     line to the log and ends with exit 2 (Task Scheduler shows a failed run).
  4. Starts `python -X utf8 -m scripts.nightly_backup` in the project folder. The password goes only to that
     child, in its environment (the job hands it on to 7-Zip): it is never printed or logged, never written to
     disk, never in this program's own environment. The job reads DATABASE_URL from the environment, else from
     the .env of the folder that holds its code (python-dotenv, which never overrides a variable already set).
     Its output is shown as it comes, with the password (and any password inside a database address) blanked
     should one ever appear. After 50 minutes the job is stopped, with everything it started (7-Zip), well
     before the task's own one-hour limit would stop this program too.
  5. Removes the plain work folder this run's job left, if it left one (a failure, a stop, Ctrl+C).
  6. Ends with the job's own exit code (0 done and encrypted, 1 failed, 2 done but not encrypted; 1 when it was
     stopped) and adds ONE plain line to nightly.log in the backup folder: start, end, exit code, the archive's
     name read from the job's own success line (and the name, never the value, of the place the password came
     from), or why nothing was made; and the names (only) of the plain folders removed or left.

A dry run passes the job's own --dry-run, removes nothing (it says what it would remove) and adds no line.

When Task Scheduler gives it a console window of its own, that window is minimised at once and the keyboard is
handed back to the window he was using, so a key pressed at that moment (Ctrl+C, say) does not land in it; the
window says to leave it open. A terminal that a shell shares is never touched.

--print-task prints ONE PowerShell command, Register-ScheduledTask, for the task "YQ Daily Backup": Sunday to
Thursday at 13:05; as the Windows user who runs it and only while that user is logged on (the password is in
that user's own Credential Manager); starting in this folder; running this Python with
-X utf8 -m scripts.yq_nightly; a run missed while the PC was off or asleep started as soon as it is back; a
run stopped after one hour. Not schtasks /Create: it can set neither the folder a task starts in (without it
`-m scripts.yq_nightly` starts in C:\\Windows\\System32 and is not found) nor the catch-up of a missed run.
The two schtasks lines printed with it (/Run, /Query) check the task once it is made.
"""
from __future__ import annotations

import argparse
import contextlib
import os
import re
import signal
import stat
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Callable, NamedTuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import nightly_backup  # noqa: E402  (the job: its folder rule, its 7-Zip lookup, its password name)
from scripts import yq_secrets  # noqa: E402

TASK_NAME = "YQ Daily Backup"
TASK_DAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday")
TASK_AT = "13:05"
TASK_LIMIT_HOURS = 1
TASK_ARGS = "-X utf8 -m scripts.yq_nightly"
TASK_DESCRIPTION = "The daily encrypted backup of the live database (scripts/yq_nightly.py, docs/BACKUPS.md)."
CHECKS = (f'schtasks /Run /TN "{TASK_NAME}"', f'schtasks /Query /TN "{TASK_NAME}" /V /FO LIST')
JOB = ("-X", "utf8", "-m", "scripts.nightly_backup")
LOG_NAME = "nightly.log"
DEADLINE_S = 50 * 60        # this program stops the job itself, before the task's own limit stops this program
FRESH_S = 15 * 60           # a work folder written to this recently may belong to a backup that is still running
POLL_S = 1.0
READER_GRACE_S = 10.0
EXIT_FAILED = 1
EXIT_NOT_STARTED = 2        # the job's own 2 is "done, but not encrypted": Task Scheduler shows both as failed
EXIT_REFUSED = 3            # a code worktree, a virtual environment or the Microsoft Store Python
HIDDEN = "[hidden]"
SW_MINIMIZE, SW_SHOWMINNOACTIVE = 6, 7
WORK_DIR = re.compile(r"work_\d{4}-\d{2}-\d{2}_\d{4}")    # the job's own plain folder: dest / f"work_{stamp}"
_DONE = re.compile(r"Nightly backup: (?P<path>.+) \([\d,]+ bytes, (?P<sealed>AES-256 encrypted|NOT ENCRYPTED)\); "
                   r"pruned \d+ old archive\(s\)\.")
_URL_PASSWORD = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s@]+@", re.IGNORECASE)


class Ended(NamedTuple):
    code: int
    stopped: str | None = None      # None: the job ended by itself; "deadline" or "interrupted": this program stopped it


# ── where and what (pure) ─────────────────────────────────────────────────────

def backup_root() -> Path:
    """The job's own backup folder (YQ_BACKUP_DIR, else %LOCALAPPDATA%\\yq-backups). Raises SystemExit for a
    folder inside OneDrive, exactly as the job does."""
    return Path(nightly_backup.backup_root())


def find_7z() -> str | None:
    return nightly_backup.find_7z()


def log_path() -> Path | str:
    try:
        return backup_root() / LOG_NAME
    except SystemExit:
        return "%LOCALAPPDATA%\\yq-backups\\" + LOG_NAME


def worktree_refusal(*folders) -> str | None:
    """The refusal when any of these folders is a code worktree (.claude/worktrees/...), on any OS and case."""
    for folder in folders:
        text = str(folder).replace("\\", "/").rstrip("/").lower() + "/"
        if "/.claude/worktrees/" in text:
            return ("This folder is a code worktree (.claude/worktrees), not the project folder: the daily backup "
                    "and its scheduled task belong to the project folder only. Nothing was started.")
    return None


def interpreter_refusal(python: str, prefix: str, base_prefix: str) -> str | None:
    """The refusal for a Python that a scheduled task would lose: the Microsoft Store one (under WindowsApps, a
    folder that changes with each update) or a virtual environment (sys.prefix != sys.base_prefix)."""
    if any(part.lower() == "windowsapps" for part in re.split(r"[\\/]+", str(python))):
        return (f"This Python comes from the Microsoft Store ({python}): its folder changes with each update, so a "
                "scheduled task would lose it. Run this with a Python installed from python.org. Nothing was started.")
    if prefix != base_prefix:
        return (f"This Python is a virtual environment ({prefix}): it can be moved or rebuilt, so a scheduled task "
                f"would lose it. Run this with the installed Python ({base_prefix}). Nothing was started.")
    return None


def redact(text: str, password: str | None) -> str:
    """The text with the password, and the password part of any address like scheme://user:pass@host, blanked."""
    if password:
        text = text.replace(password, HIDDEN)
    return _URL_PASSWORD.sub(r"\1" + HIDDEN + "@", text)


def job_argv(python: str, dry_run: bool) -> list[str]:
    return [python, *JOB, *(["--dry-run"] if dry_run else [])]


def job_env(password: str, base: dict | None = None) -> dict:
    """The job's environment: a copy of this program's, with the password added to the copy only, UTF-8 forced
    and the output unbuffered (shown as it comes)."""
    env = dict(os.environ if base is None else base)
    env[nightly_backup.PASSWORD_ENV] = password
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("PYTHONIOENCODING", None)
    return env


def success_line(lines: list[str]) -> tuple[str, bool] | None:
    """(the archive's name, encrypted) from the job's own last line, 'Nightly backup: <archive> (<n> bytes,
    AES-256 encrypted|NOT ENCRYPTED); pruned <n> old archive(s).', or None when it printed none."""
    for line in reversed(lines):
        m = _DONE.fullmatch(line.strip())
        if m:
            return PureWindowsPath(m.group("path")).name, m.group("sealed") == "AES-256 encrypted"
    return None


def outcome(code: int, lines: list[str], source: str | None) -> str:
    """What the log line says once the job has ended by itself: the archive named by its success line (read only
    for exit 0 or 2, the job's two endings with an archive), else why it failed."""
    done = success_line(lines) if code in (0, 2) else None
    if done is not None:
        name, encrypted = done
        return f"{name}, {'encrypted' if encrypted else 'NOT ENCRYPTED'} (password from {source})"
    if code == 0:
        return "done, but the job printed no success line"
    last = next((ln.strip() for ln in reversed(lines) if ln.strip()), "")
    return f"failed: {last[:200]}" if last else "failed, and the job printed nothing"


def log_line(started: datetime, ended: datetime, code: int, what: str) -> str:
    return f"{started:%Y-%m-%d %H:%M:%S} start, {ended:%H:%M:%S} end, exit {code}: {' '.join(what.split())}"


def ps_quote(text) -> str:
    """A PowerShell single-quoted literal: each single-quote character doubled (the curly ones too)."""
    s = str(text)
    for q in ("'", "\u2018", "\u2019", "\u201a", "\u201b"):
        s = s.replace(q, q + q)
    return "'" + s + "'"


def task_command(python: str, folder) -> str:
    """The ONE PowerShell line that creates the task, or replaces one of the same name. It holds no secret and
    names no user: the user is whoever runs it, read from Windows at that moment."""
    action = (f"New-ScheduledTaskAction -Execute {ps_quote(python)} -Argument {ps_quote(TASK_ARGS)} "
              f"-WorkingDirectory {ps_quote(folder)}")
    trigger = (f"New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek {','.join(TASK_DAYS)} "
               f"-At {ps_quote(TASK_AT)}")
    principal = ("New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) "
                 "-LogonType Interactive -RunLevel Limited")
    settings = ("New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
                f"-ExecutionTimeLimit (New-TimeSpan -Hours {TASK_LIMIT_HOURS})")
    return (f"Register-ScheduledTask -TaskName {ps_quote(TASK_NAME)} -Description {ps_quote(TASK_DESCRIPTION)} "
            f"-Action ({action}) -Trigger ({trigger}) -Principal ({principal}) -Settings ({settings}) -Force")


def task_text(python: str, folder, log) -> str:
    days = f"{TASK_DAYS[0]} to {TASK_DAYS[-1]}"
    return "\n".join([
        f'The scheduled task "{TASK_NAME}". Nothing has been created: this only prints.',
        "Paste this ONE line into a PowerShell window on this PC (not a Command Prompt), as yourself. It creates",
        "the task, or replaces one of the same name:",
        "",
        task_command(python, folder),
        "",
        f"It runs:   {python} {TASK_ARGS}",
        f"in:        {folder}",
        f"when:      {days} at {TASK_AT}, as you, only while you are logged on (the backup password is read",
        "           from your own Credential Manager). A run missed while the PC was off or asleep starts as soon",
        f"           as it is back; a run still going after {TASK_LIMIT_HOURS} hour is stopped.",
        "",
        "Then check it (in PowerShell or a Command Prompt):",
        f"  {CHECKS[0]}",
        f"  {CHECKS[1]}",
        "The second shows Last Run Time and Last Result (0 once the run is done), Logon Mode: Interactive only,",
        f"Start In and the days. The newest line of {log} says what the run made.",
    ])


# ── the job's plain work folders ──────────────────────────────────────────────

def _own_folder(path: Path) -> bool:
    """A folder of its own: not a link, not a junction or any other reparse point, so removing it never reaches
    anything outside it."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISDIR(st.st_mode) and not (getattr(st, "st_file_attributes", 0) & reparse)


def leftovers(store: Path) -> list[Path]:
    """The job's plain work folders directly in the backup folder: named exactly work_<date>_<time>, real folders
    only (never a link or a junction), never anything else; by name."""
    try:
        found = sorted(store.iterdir(), key=lambda p: p.name)
    except OSError:
        return []
    return [p for p in found if WORK_DIR.fullmatch(p.name) and _own_folder(p)]


def last_written(folder: Path) -> float:
    """The newest modification time of the folder and of what is directly in it."""
    newest = 0.0
    with contextlib.suppress(OSError):
        newest = os.lstat(folder).st_mtime
        with os.scandir(folder) as found:
            for entry in found:
                with contextlib.suppress(OSError):
                    newest = max(newest, entry.stat(follow_symlinks=False).st_mtime)
    return newest


def fresh(folder: Path) -> bool:
    return time.time() - last_written(folder) < FRESH_S


def _remove(folder: Path) -> list[str]:
    """Deletes one plain work folder with all it holds (tried again, a read-only flag cleared, as the backup tools
    delete a plain copy); what could not be deleted, [] when it is gone. Never raises."""
    from scripts import yq_backup
    return yq_backup.remove_plain(folder)


def sweep(store: Path, spare: Callable[[Path], bool]) -> tuple[list[str], list[str], list[str]]:
    """Removes the job's plain work folders that `spare` does not keep: (removed, could not be removed, spared),
    by name only; what was in them is never listed."""
    removed: list[str] = []
    stuck: list[str] = []
    spared: list[str] = []
    for folder in leftovers(store):
        if spare(folder):
            spared.append(folder.name)
        elif _remove(folder):
            stuck.append(folder.name)
        else:
            removed.append(folder.name)
    return removed, stuck, spared


# ── the console window ────────────────────────────────────────────────────────

class _Win32:
    """The Win32 calls the console tuck makes, and nothing else (a seam: the tests hand in stand-ins)."""

    def __init__(self) -> None:
        import ctypes
        self._kernel32, self._user32 = ctypes.WinDLL("kernel32"), ctypes.WinDLL("user32")
        self._kernel32.GetConsoleWindow.restype = ctypes.c_void_p
        self._kernel32.GetConsoleProcessList.argtypes = [ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32]
        self._kernel32.GetConsoleProcessList.restype = ctypes.c_uint32
        self._user32.GetForegroundWindow.restype = ctypes.c_void_p
        self._user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
        self._user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._ids = (ctypes.c_uint32 * 8)()

    def attached(self) -> int:
        return int(self._kernel32.GetConsoleProcessList(self._ids, 8))

    def console_window(self):
        return self._kernel32.GetConsoleWindow()

    def visible(self, window) -> bool:
        return bool(self._user32.IsWindowVisible(window))

    def foreground(self):
        return self._user32.GetForegroundWindow()

    def show(self, window, how: int) -> None:
        self._user32.ShowWindow(window, how)


def _win32():
    return _Win32() if sys.platform == "win32" else None


def tuck_console_away(win=None) -> bool:
    """Minimises this program's console window and gives the keyboard back, but only a visible console of its
    own (the one Task Scheduler opens for it), never a terminal that a shell shares. When the window is the active
    one, SW_MINIMIZE hands activation to the window below it (the one he was using); otherwise SW_SHOWMINNOACTIVE
    leaves activation where it is. True when it did."""
    try:
        win = _win32() if win is None else win
        if win is None:
            return False
        window = win.console_window()
        if win.attached() != 1 or not window or not win.visible(window):
            return False
        win.show(window, SW_MINIMIZE if win.foreground() == window else SW_SHOWMINNOACTIVE)
        return True
    except (OSError, AttributeError, ValueError):
        return False


# ── the run ───────────────────────────────────────────────────────────────────

def _say(text: str) -> None:
    out = sys.stdout
    if out is None:                     # pythonw: no console, nothing to show
        return
    try:
        print(text, file=out, flush=True)
    except (OSError, ValueError, UnicodeError):
        pass


def _stop_tree(proc: subprocess.Popen) -> None:
    """Ends the job and everything it started (7-Zip): taskkill /T on Windows, its process group elsewhere, and the
    job itself in any case."""
    if sys.platform == "win32":
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=60)
    else:
        with contextlib.suppress(OSError, AttributeError):
            os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(OSError):
        proc.kill()


def _reap(proc: subprocess.Popen) -> int:
    try:
        return proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        return EXIT_FAILED


def run_child(argv: list[str], *, env: dict, cwd: Path, on_line: Callable[[str], None], deadline_s: float) -> Ended:
    """Runs the job to its end and says how it ended. Each line of its output (stdout and stderr, read as UTF-8
    with replacement) goes to on_line as it comes; nothing is read from the keyboard. A job still running after
    `deadline_s` (wall clock or monotonic, whichever says more: a PC that slept counts), or Ctrl+C, stops it with
    everything it started."""
    proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, start_new_session=sys.platform != "win32")

    def pump() -> None:
        for raw in proc.stdout or ():
            on_line(raw.decode("utf-8", errors="replace").rstrip("\r\n"))

    reader = threading.Thread(target=pump, name="yq-nightly-output", daemon=True)
    reader.start()
    began_wall, began_mono = time.time(), time.monotonic()
    stopped: str | None = None
    try:
        while True:
            try:
                code = proc.wait(timeout=POLL_S)
                break
            except subprocess.TimeoutExpired:
                if max(time.time() - began_wall, time.monotonic() - began_mono) >= deadline_s:
                    stopped = "deadline"
                    _stop_tree(proc)
                    code = _reap(proc)
                    break
    except KeyboardInterrupt:
        stopped = "interrupted"
        _stop_tree(proc)
        code = _reap(proc)
    reader.join(timeout=READER_GRACE_S)
    if not reader.is_alive() and proc.stdout is not None:
        with contextlib.suppress(OSError):
            proc.stdout.close()
    return Ended(code, stopped)


def _write_log(log: Path, started: datetime, code: int, what: str) -> str | None:
    """Appends one line to the log; returns it, or None when the log cannot be written (never fatal)."""
    line = log_line(started, datetime.now().astimezone(), code, what)
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        return None
    return line


def run(*, dry_run: bool, root: Path, python: str, say: Callable[[str], None]) -> int:
    started = datetime.now().astimezone()
    notes: list[str] = []

    def note(text: str) -> None:
        notes.append(text)
        say(f"YQ daily backup: {text}.")

    if not dry_run:
        tuck_console_away()
    try:
        store: Path | None = backup_root()
    except SystemExit as exc:                     # the job's own rule: never a folder inside OneDrive
        if not dry_run:
            say(f"{exc} Nothing was started.")
            return EXIT_FAILED
        store = None                              # the job's own dry run says so
    log = None if dry_run or store is None else store / LOG_NAME

    def finish(code: int, what: str) -> int:
        what = "; ".join([what, *notes])
        if log is None:
            say(f"YQ daily backup, dry run: exit {code}. Nothing was written.")
        elif _write_log(log, started, code, what):
            say(f"YQ daily backup: exit {code}: {what}. One line was added to {log}.")
        else:
            say(f"YQ daily backup: exit {code}: {what}. The log {log} could not be written.")
        return code

    if store is not None and dry_run:
        stale = [f.name for f in leftovers(store) if not fresh(f)]
        if stale:
            say(f"Dry run: would remove leftover plain folder(s): {', '.join(stale)}")
    elif store is not None:
        removed, stuck, spared = sweep(store, fresh)
        if removed:
            note(f"removed leftover plain folder(s): {', '.join(removed)}")
        if stuck:
            note(f"could NOT remove plain folder(s): {', '.join(stuck)}")
        if spared:
            note(f"left in place, written to in the last {FRESH_S // 60} minutes: {', '.join(spared)}")

    password, source = yq_secrets.backup_password()
    exe = find_7z()
    needs = (("no backup password was found (Windows Credential Manager first)", password),
             ("7-Zip is not installed", exe))
    missing = [why for why, found in needs if not found]
    if missing:
        why = " and ".join(missing)
        say(f"NOT started: {why}. The backup is only ever made encrypted, so none was made: docs/BACKUPS.md "
            "says how to set this up.")
        return finish(EXIT_NOT_STARTED, f"NOT started: {why}; nothing was backed up")

    if dry_run:
        say("YQ daily backup, dry run: the job's own dry run follows; it writes nothing.")
    else:
        say(f"YQ daily backup started {started:%Y-%m-%d %H:%M}. This window closes by itself when the backup is "
            "done: please leave it open.")
    before = {f.name for f in leftovers(store)} if store is not None and not dry_run else set()
    lines: list[str] = []

    def on_line(raw: str) -> None:
        clean = redact(raw, password)
        lines.append(clean)
        say(clean)

    try:
        ended = run_child(job_argv(python, dry_run), env=job_env(password), cwd=root, on_line=on_line,
                          deadline_s=DEADLINE_S)
        if ended.stopped == "deadline":
            code, what = EXIT_FAILED, f"stopped after {DEADLINE_S // 60} minutes, the backup did not finish"
        elif ended.stopped:
            code, what = EXIT_FAILED, "stopped before the end (Ctrl+C)"
        else:
            code, what = ended.code, outcome(ended.code, lines, source)
    except KeyboardInterrupt:
        code, what = EXIT_FAILED, "stopped before the end (Ctrl+C)"
    except OSError as exc:
        code, what = EXIT_FAILED, f"NOT started: the job could not be started ({type(exc).__name__})"
    if store is not None and not dry_run:
        removed, stuck, _spared = sweep(store, lambda f: f.name in before)
        if removed:
            note(f"removed this run's plain folder(s): {', '.join(removed)}")
        if stuck:
            note(f"could NOT remove plain folder(s): {', '.join(stuck)}")
    return finish(code, what)


def main(argv: list[str] | None = None, *, cwd: Path | None = None, root: Path | None = None,
         python: str | None = None, prefixes: tuple[str, str] | None = None,
         say: Callable[[str], None] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.yq_nightly", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    what = ap.add_mutually_exclusive_group()
    what.add_argument("--dry-run", action="store_true",
                      help="the job's own dry run: where it would write and what it would prune; no log line")
    what.add_argument("--print-task", action="store_true",
                      help="print the command that creates the scheduled task; it creates nothing")
    a = ap.parse_args(argv)
    say = say or _say
    root = Path(root) if root is not None else ROOT
    python = python or sys.executable
    prefix, base_prefix = prefixes if prefixes is not None else (sys.prefix, sys.base_prefix)
    refusal = (worktree_refusal(cwd if cwd is not None else Path.cwd(), root)
               or interpreter_refusal(python, prefix, base_prefix))
    if refusal:
        say(refusal)
        return EXIT_REFUSED
    if a.print_task:
        say(task_text(python, root, log_path()))
        return 0
    return run(dry_run=a.dry_run, root=root, python=python, say=say)


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
