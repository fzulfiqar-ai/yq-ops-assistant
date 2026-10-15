"""python -m scripts.yq_nightly : starts the daily encrypted backup of the live database (Task Scheduler runs it).

    python -m scripts.yq_nightly               run the backup now: what the scheduled task runs
    python -m scripts.yq_nightly --dry-run     the job's own dry run: where it would write, what it would prune
    python -m scripts.yq_nightly --print-task  print the command that creates the scheduled task (creates nothing)

The backup itself is scripts/nightly_backup.py (the job: a read-only snapshot of every table, packed into ONE
7-Zip AES-256 archive in %LOCALAPPDATA%\\yq-backups, the newest of each of the last 14 days and 8 weeks kept).
The job reads its password from its own environment only; this starter puts it there, from Windows Credential
Manager, so nothing has to be set with setx. One run, in order:

  1. Refuses with exit 3 when the folder it was started from, or this program's own folder, is a code worktree
     (.claude/worktrees): the backup runs only from the project folder.
  2. Reads the backup password where scripts/yq_secrets.py looks (Credential Manager first) and looks for 7-Zip.
     Without either the job is NOT started, so no backup is ever made unencrypted: it says so, adds one dated
     line to the log and ends with exit 2 (Task Scheduler shows a failed run).
  3. Starts `python -X utf8 -m scripts.nightly_backup` in the project folder, with the password in that
     child's environment only: never on a command line, never printed or logged, never written to disk, never
     in this program's own environment. The job reads DATABASE_URL from the environment, else from the .env
     of the folder that holds its code (python-dotenv, which never overrides a variable already set). Its
     output is shown as it comes, with the password (and any password inside a database address) blanked
     should one ever appear.
  4. Ends with the job's own exit code (0 done and encrypted, 1 failed, 2 done but not encrypted) and adds ONE
     plain line to nightly.log in the backup folder: start, end, exit code, the archive's name, and the name
     (never the value) of the place the password came from; or why nothing was made.

A dry run passes the job's own --dry-run and adds no line to the log.

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
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

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
EXIT_FAILED = 1
EXIT_NOT_STARTED = 2        # the job's own 2 is "done, but not encrypted": Task Scheduler shows both as failed
EXIT_WORKTREE = 3
HIDDEN = "[hidden]"
SW_MINIMIZE, SW_SHOWMINNOACTIVE = 6, 7
_ARCHIVE = re.compile(r"yq-backup_\d{4}-\d{2}-\d{2}_\d{4}\.(?:7z|zip)")
_URL_PASSWORD = re.compile(r"(\b[a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s@]+@", re.IGNORECASE)


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


def outcome(code: int, lines: list[str], source: str | None) -> str:
    """What the log line says once the job has run: the archive it names, else why it failed."""
    text = "\n".join(lines)
    archives = _ARCHIVE.findall(text)
    if archives:
        sealed = "NOT ENCRYPTED" if "NOT ENCRYPTED" in text else "encrypted"
        return f"{archives[-1]}, {sealed} (password from {source})"
    if code == 0:
        return f"done, but the job named no archive (password from {source})"
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


# ── the run ───────────────────────────────────────────────────────────────────

def _say(text: str) -> None:
    out = sys.stdout
    if out is None:                     # pythonw: no console, nothing to show
        return
    try:
        print(text, file=out, flush=True)
    except (OSError, ValueError, UnicodeError):
        pass


def tuck_console_away() -> bool:
    """Minimises this program's console window and gives the keyboard back, but only a visible console of its
    own (the one Task Scheduler opens for it), never a terminal that a shell shares. When the window is the active
    one, SW_MINIMIZE hands activation to the window below it (the one he was using); otherwise SW_SHOWMINNOACTIVE
    leaves activation where it is. True when it did."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        kernel32, user32 = ctypes.WinDLL("kernel32"), ctypes.WinDLL("user32")
        kernel32.GetConsoleWindow.restype = ctypes.c_void_p
        kernel32.GetConsoleProcessList.argtypes = [ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32]
        kernel32.GetConsoleProcessList.restype = ctypes.c_uint32
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
        user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        ids = (ctypes.c_uint32 * 8)()
        attached = kernel32.GetConsoleProcessList(ids, 8)
        window = kernel32.GetConsoleWindow()
        if attached != 1 or not window or not user32.IsWindowVisible(window):
            return False
        active = user32.GetForegroundWindow() == window
        user32.ShowWindow(window, SW_MINIMIZE if active else SW_SHOWMINNOACTIVE)
        return True
    except (OSError, AttributeError, ValueError):
        return False


def run_child(argv: list[str], *, env: dict, cwd: Path, on_line: Callable[[str], None]) -> int:
    """Runs the job to its end and returns its exit code. Each line of its output (stdout and stderr, read as
    UTF-8 with replacement) goes to on_line as it comes. Nothing is read from the keyboard."""
    with subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT) as proc:
        for raw in proc.stdout or ():
            on_line(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
        return proc.wait()


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
    log: Path | None = None
    if not dry_run:
        tuck_console_away()
        try:
            log = backup_root() / LOG_NAME
        except SystemExit as exc:                 # the job's own rule: never a folder inside OneDrive
            say(f"{exc} Nothing was started.")
            return EXIT_FAILED

    def finish(code: int, what: str) -> int:
        if log is None:
            say(f"YQ daily backup, dry run: exit {code}. Nothing was written.")
        elif _write_log(log, started, code, what):
            say(f"YQ daily backup: exit {code}: {what}. One line was added to {log}.")
        else:
            say(f"YQ daily backup: exit {code}: {what}. The log {log} could not be written.")
        return code

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
    lines: list[str] = []

    def on_line(raw: str) -> None:
        clean = redact(raw, password)
        lines.append(clean)
        say(clean)

    try:
        code = run_child(job_argv(python, dry_run), env=job_env(password), cwd=root, on_line=on_line)
        what = outcome(code, lines, source)
    except KeyboardInterrupt:
        code, what = EXIT_FAILED, "stopped before the end (Ctrl+C)"
    except OSError as exc:
        code, what = EXIT_FAILED, f"NOT started: the job could not be started ({type(exc).__name__})"
    return finish(code, what)


def main(argv: list[str] | None = None, *, cwd: Path | None = None, root: Path | None = None,
         python: str | None = None, say: Callable[[str], None] | None = None) -> int:
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
    refusal = worktree_refusal(cwd if cwd is not None else Path.cwd(), root)
    if refusal:
        say(refusal)
        return EXIT_WORKTREE
    python = python or sys.executable
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
