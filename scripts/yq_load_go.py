"""python -m scripts.yq_load_go : the ONE program of the ops skills that changes production.

    python -m scripts.yq_load_go --run <run> --token <t>     the go: what /yq-load runs under its table
    python -m scripts.yq_load_go --worker <run>              internal: the detached worker the go starts
    python -m scripts.yq_load_go --rehearse [--seconds N] [--no-box] [--dev]

It lives outside scripts/ops on purpose: that package is read-only by test, and no permission rule
written for `python -m scripts.ops ...` can match this name.

THE GO (the launcher: it returns in seconds)
  1. Refuses, in one sentence and with exit 2, when: this checkout is a linked worktree; the run or
     its plan is missing; a newer check of the same folder replaced this one (its plan carries
     superseded_by); the check said STOP or gave no token; the run already finished or FAILED
     (a failed staging is refused for good); a permissions.allow rule of Claude Code matches this
     program's own command line (it prints the entry to delete).
  2. Works the token out again (drop and staged files, HEAD, the live commit, the database
     fingerprint). Anything moved is "check again": the parts are printed, exit 3, nothing written.
  3. Shows a Windows Yes/No box: always on top, default No, closing itself as No after 540 s. The
     owner's sessions run in auto mode and show no permission prompt, so THIS is his confirmation.
     Under the file lines it shows what the check flagged, each on a line that starts ATTENTION
     (reports with different as-on dates and the like), and it names every option he gave the
     check that changes what a Yes does (--replace-ok and the like); a marker file the box would
     not show is refused. Anything but Yes: exit 4, nothing loaded.
  4. Takes business_data/ops/LOCK, starts the worker detached, hands the lock over, returns 0. The
     worker runs only as the child of the launcher that holds the lock, and only with that launcher's
     one-time word: the lock keeps the word's hash, never the word, so it cannot be read back.

THE WORKER (loadplan.STEPS; every step journalled; the lock's heartbeat every 10 s; the PC kept awake)
  token      the same check once more; data/clean emptied and confirmed empty
  backup     every table (scripts.db_backup) outside OneDrive, packed (AES-256 when the backup
             password is set), plain folder removed, the newest N pre-load archives kept.
             Failure: stop, nothing was written
  pre_gate   the before-snapshot (scripts.prod_gate). Failure: stop, nothing was written
  wait_tick  the token a third time, then a quiet moment between two production ticks. After a
             wait the token is checked again; the step ends only on a check that is followed at once
             by a quiet moment (a slow check that outlasts it waits for the next one)
  refresh    scripts.refresh.refresh(staging, send=False) in a CHILD process. It passes on: ok,
             verify passed, no loader notice, ingest_runs grew by exactly 2 in that attempt. A
             load-stage failure is run once more, identically; so is a load whose verification
             could not RUN (nothing was compared: that is no failed verify). Verify failed (it
             ran and the totals differ): the run is closed FAILED and this staging is refused
             from then on (the data IS loaded; the journal says so)
  reconcile  scripts.reconcile_check. Not passed: reported, the load goes on
  post_gate  the after-snapshot and its compare. Not passed: "gate needs a look", still published
  publish    staging -> business_data/Focus ERP <as-on date>/ (the marker of a partial export
             travels with the set, because verify reads it from the folder it checks; the
             one-time permission to replace does not), state.json (last_load, with
             profit_held_since while the profitability report is held back), journal finished, unlock

Every engine step is a child process (cwd = this checkout, PYTHONUTF8=1, no PYTHONIOENCODING, no
shell). Its output is read as bytes, decoded as UTF-8 with replacement and copied to the worker log;
it passes on its exit code first and on its ASCII marker second. Every child starts with the
environment this program was STARTED with (LAUNCH_ENV, copied in the first lines of this file).
This process itself imports scripts.ops.db for its own reads, and that module blanks the message
and model keys in os.environ; python-dotenv never fills a variable that is set, so a child given
os.environ would run with those keys blank instead of the way the engine runs when it is started by
hand. The refresh child still calls refresh(folder, send=False): the refresh sends no briefing
from this PC either way.

INTERRUPTED = a lock whose process is gone. The next go for the same run is a RESUME: files and
commits are checked (not the fingerprint, which the interrupted load has moved), the journalled
backup and before-snapshot are reused, the whole refresh runs again. While nothing was written, a
backup or snapshot whose file is gone is simply taken again. Once a refresh has BEGUN the database
may hold part of the load until a load finishes, and no sentence after that says otherwise: a
resume that cannot go on (its files or programs moved) is closed with those words, and a resume
whose worker never got going keeps its lock, so the load stays INTERRUPTED and can be resumed.
A run that is closed as failed after its refresh had begun carries step and written = true in its
closing row: scripts.ops.status.failed_load() reads them, and every skill then opens with LAST
LOAD FAILED until a load has finished.
A lock whose process is ALIVE but silent (no heartbeat for two minutes) also reads INTERRUPTED in
every skill; no second loader is started beside a live process, and the go says exactly that and
what he can do about a worker that has truly hung.

REHEARSAL: --rehearse runs this same launcher and worker with every engine call replaced by a stub
that sleeps, in a sandbox inside its own run folder (a run named 000000_<HHMM>). The lock, the
heartbeat and the journal are real, so "does the worker outlive the call that started it" can be
proven before any production go. A rehearsal cannot construct the real runner, opens no database
session and calls no web address. --dev lets a rehearsal (never a real go) run in a code worktree.
The dead lock a cut-off rehearsal leaves is no interrupted load: the next go or rehearsal clears it.

Exit codes: 0 started, 1 error, 2 refused, 3 check again, 4 no answer at the box.

The journal (business_data/ops/loads/<run>/journal.jsonl, read by `load status`): one row per
event: started, step_start, step_ok, step_failed (stops: true = the run ends there, false = it
needs a look and the load went on), note, and one closing row, finished (result ok | ok_needs_look)
or failed (result failed | check_again, written = may the database hold any of this load;
check_again: true = a new check is the way on. Result check_again reads "stopped before anything
was loaded", so a check-again that ends a load which had begun carries result failed). EVERY row
carries `text`, one plain sentence for the owner, and where it helps `why` and `next`: `load
status` prints exactly these three, so each must read well with nothing else beside it. Each is
at most 160 characters. step_ok of backup carries `archive` and of pre_gate `path`, which a resume
reads back.

This file never puts a backup back, never applies a migration, never deploys, never writes a
marker file into a folder that is loaded and never changes anything in the drop folder.
"""
from __future__ import annotations

import os

# The environment this program was started with, copied before anything else is imported (see
# child_env). scripts.ops.db, which this process imports later for its own reads, blanks the
# message and model keys in os.environ: no child of this program is ever started from os.environ.
LAUNCH_ENV = dict(os.environ)

import argparse  # noqa: E402
import ctypes  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import secrets  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from datetime import date, datetime, timedelta, timezone  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Callable  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops import config, gitinfo, loadplan, paths, status  # noqa: E402  (none opens a connection)

MODULE = loadplan.WRITER_MODULE
FOLLOW_LINE = "python -m scripts.ops load status --wait"
EXIT_OK, EXIT_ERROR, EXIT_REFUSED, EXIT_CHECK_AGAIN, EXIT_NO_ANSWER = 0, 1, 2, 3, 4

BOX_TIMEOUT_S = 540                 # the skill's tool call lasts 600 s at most
HEARTBEAT_S = 10                    # scripts.ops.status calls a lock stale after 120 s
START_WAIT_S = 10                   # the launcher waits this long for the worker's first journal line
RETRY_PAUSE_S = 30                  # before the one identical re-run of a failed load stage
NEW_RUNS_PER_LOAD = 2               # ingest_runs rows of one refresh: the loader's and its own
FIELD = 160                         # no sentence of the journal is longer (`load status` shows a field whole)
BACKUP_TIMEOUT_S, GATE_TIMEOUT_S, RECONCILE_TIMEOUT_S = 1800, 900, 1800      # read-only steps only
REHEARSAL_PREFIX = "000000_"        # not a date, so no drop folder can ever spell it
REHEARSAL_SECONDS = 180
NONCE_ENV = "YQ_LOAD_GO_NONCE"           # the one-time word the launcher hands its worker (never on disk)
BREAKAWAY_ENV = "YQ_LOAD_GO_BREAKAWAY"   # "1" when the worker was started outside the editor's job
for _own in (NONCE_ENV, BREAKAWAY_ENV):  # in the worker these two are in the copy made above: they are the
    LAUNCH_ENV.pop(_own, None)           # launcher's words to its worker, no part of anybody's environment
BAHRAIN = timezone(timedelta(hours=3))
PROFIT_KIND = "product_profitability"    # the profitability report, as the load check names it in plan.json

# Production's scheduler reads the ledger on every quarter-hour (UTC) and runs its daily agents at
# 08:00 Bahrain. A load starts 1 to 5 minutes after a tick, so the next one is at least 10 minutes
# away, and never inside the morning window.
TICK_S = 900
START_FROM_S, START_UNTIL_S = 60, 300
QUIET_FROM, QUIET_UNTIL = (7, 50), (8, 20)
TICK_TRIES = 4                      # token checks that may outlast the quiet moment before the load gives up

# Every load deletes and re-inserts the whole stock ledger. Once a refresh has begun, this is the
# sentence for the state of the database, whatever ended the run afterwards.
MAY_HOLD = "The database may hold part of the interrupted load until the next load."

# The options of the load check that change what a Yes does, as plan["options"] holds them:
# (key, the flag he typed, what it does). The box shows one line for each that is set.
OPTION_WORDS = (
    ("replace_ok", "--replace-ok", "invoices these files no longer hold are REMOVED from the database."),
    ("partial_ok", "--partial-ok", "a stock balance with fewer warehouses than the last one is accepted."),
    ("with_profitability", "--with-profitability", "the profitability report loads even if the check would hold "
                                                   "it back."),
    ("pricebook", "--pricebook load", "the price book loads, with the changes the table listed."),
)
# The loader's marker files stand for the first two options (loadplan.MARKERS, named after them).
# When the loaded set is published, the marker of a partial export travels with it: it is a
# property of the set, and the verify program reads it from the folder it checks. The marker of
# these options does NOT travel: it was a permission for this one load.
ONE_TIME_OPTIONS = ("replace_ok",)

# What the load check flagged (plan["flags"]) that he must have seen before he says Yes: one
# ATTENTION line of the box each. A flag may also come as a sentence of its own.
ATTENTION_WORDS = {
    "mixed_dates": "the reports of this folder carry different as-on dates: are they one export?",
    "older_than_loaded": "a report of this folder is older than what is already loaded: older figures would come back.",
    "last_load_failed": "the last load failed after it had begun to write: this load replaces it.",
}
# Flags that are no warning, or that the box shows on a line of their own already (the file's line,
# an ON YOUR WORD line), or that the table lists as items to check, or that stop the check before
# any box. A flag that is in neither list is shown under its own name: a new flag of the check is
# never swallowed in silence.
ROUTINE_FLAGS = frozenset((
    "hold_back", "needs_decision", "partial_drop", "price_scope", "catalog_switch", "invoices_changed",
    "vouchers_changed", "profit_above_ledger", "profit_held_twice", "replaced_earlier", "token", "dev", "stop",
    "moved_meanwhile", "marker_mismatch"))
ATTENTION_MAX = 8                   # lines of the box; the rest are counted

# One plain sentence for the line that opens each step in the journal (true for a resume as well).
STEP_OPENS = {
    "token": "Checking that nothing changed since the table was shown.",
    "backup": "Making sure there is a backup of every table from before the load.",
    "pre_gate": "Making sure there is a before-snapshot of the records a load must never change.",
    "wait_tick": "Checking once more that nothing changed, then waiting for a quiet moment between two production "
                 "ticks.",
    "refresh": "Loading the reports and verifying their totals: from this line on the database is being changed.",
    "reconcile": "Comparing the portal's numbers with the Focus reports.",
    "post_gate": "Taking the after-snapshot and comparing it with the before-snapshot.",
    "publish": "Copying the loaded set to business_data and recording the load.",
}
REHEARSAL_OPENS = {"refresh": "REHEARSAL: a stand-in for the load waits here; the database is not touched."}
# Whose words a line of a step's output is, when the journal quotes it for a step that needs a look.
LOOK_SAYS = {"reconcile": "The reconcile says", "post_gate": "The gate says"}

CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000
ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
MB_YESNO, MB_ICONQUESTION, MB_DEFBUTTON2, MB_SETFOREGROUND, MB_TOPMOST = 0x4, 0x20, 0x100, 0x10000, 0x40000
BOX_FLAGS = MB_YESNO | MB_ICONQUESTION | MB_DEFBUTTON2 | MB_SETFOREGROUND | MB_TOPMOST
_BOX_ANSWERS = {6: "yes", 7: "no", 32000: "timeout"}

VERDICT_WORDS = {                   # the same words as the load check's table
    "load": "LOAD", "trim": "LOAD (main-store block only)", "hold_back": "HOLD BACK", "leave_out": "LEAVE OUT",
    "needs_decision": "NEEDS YOUR DECISION", "not_loaded": "NOT LOADED", "stop": "STOP",
}

# The small program the refresh child runs. The worker never imports scripts.refresh itself: a
# crash, an encoding error or a hang of the engine must not take the lock and the journal with it.
# verify_error is set when the verification could not RUN (the refresh then keeps an 'error' in its
# verify result): nothing was compared, which is another thing than totals that differ.
REFRESH_PROGRAM = (
    "import json, sys\n"
    "from scripts.refresh import refresh\n"
    "res = refresh(sys.argv[1], send=False)\n"
    "verify = res.get('verify')\n"
    "changes = res.get('changes') or {}\n"
    "not_run = isinstance(verify, dict) and 'error' in verify\n"
    "out = {'ok': bool(res.get('ok')), 'data_as_of': res.get('data_as_of'), 'error': res.get('error'),\n"
    "       'loaded': res.get('loaded') or {},\n"
    "       'verify_ok': None if verify is None else bool(verify.get('ok')),\n"
    "       'verify_error': (str(verify.get('error') or '') or 'no detail given') if not_run else None,\n"
    "       'loader_notices': [str(t) for t in (changes.get('loader') or [])]}\n"
    "print('REFRESH_RESULT ' + json.dumps(out, default=str))\n"
    "sys.exit(0 if out['ok'] else 2)\n"
)
REFRESH_MARK = "REFRESH_RESULT "
_SNAPSHOT_LINE = re.compile(r"^snapshot (\S+\.json):", re.M)


class Refusal(Exception):
    """The launcher will not start: one plain sentence, exit 2."""


class CheckAgain(Exception):
    """Something the token binds moved since the table was shown: this go writes nothing, exit 3.
    `written` = a refresh of this run had begun before (an interrupted load): the database may hold
    part of it, and the sentence for the owner must not say that nothing was loaded."""

    def __init__(self, moved: list[str], written: bool = False):
        super().__init__("; ".join(moved))
        self.moved, self.written = list(moved), bool(written)


class NoAnswer(Exception):
    """The box was not answered with Yes: exit 4."""


class Stop(Exception):
    """A step failed and the load stops at it."""

    def __init__(self, step: str, text: str, **fields):
        super().__init__(text)
        self.step, self.fields = step, fields


class LockTaken(Exception):
    """Another launcher holds, or just took, the lock."""


# ── Claude Code permission rules ──────────────────────────────────────────────────────────────

_RULE = re.compile(r"^\s*([A-Za-z]+)\s*(?:\((.*)\))?\s*$", re.S)
SHELL_TOOLS = ("bash", "powershell")


def _wild(pattern: str) -> str:
    return ".*".join(re.escape(part) for part in pattern.split("*"))


def rule_matches(entry: str, command: str) -> bool:
    """Would the Claude Code rule `entry` cover the shell command `command`? As Claude Code documents
    it: Tool(pattern) with Tool = Bash or PowerShell; `*` is any text; a trailing ' *' also matches
    the bare command; 'prefix:*' matches the prefix and the prefix + space + anything. This check
    exists to REFUSE, so it leans wide: a bare tool name covers everything and letter case is
    ignored for both tools (Windows runs `PYTHON` as well)."""
    m = _RULE.match(entry or "")
    if not m or m.group(1).lower() not in SHELL_TOOLS:
        return False
    pattern = m.group(2)
    if pattern is None:
        return True
    pattern = " ".join(pattern.split())
    command = " ".join(str(command).split())
    if pattern.endswith(":*") or pattern.endswith(" *"):
        regex = _wild(pattern[:-2]) + r"(?: .*)?"
    else:
        regex = _wild(pattern)
    return re.fullmatch(regex, command, re.I | re.S) is not None


def own_command_lines(run: str, token: str) -> list[str]:
    """The ways this program's go can be typed: what no allow rule may match."""
    tail = f"-m {MODULE} --run {run} --token {token}"
    here = sys.executable or "python"
    out: list[str] = []
    for exe in ("python", "python.exe", "py", "python3", here, here.replace("\\", "/"), f'"{here}"'):
        out.append(f"{exe} {tail}")
        out.append(f"{exe} -X utf8 {tail}")
    return out


def settings_files(root: Path) -> list[Path]:
    """The user, project and local settings of Claude Code."""
    return [Path.home() / ".claude" / "settings.json", Path(root) / ".claude" / "settings.json",
            Path(root) / ".claude" / "settings.local.json"]


def allow_entries_matching(commands: list[str], files: list[Path]) -> tuple[list[tuple[Path, str]], list[str]]:
    """(the allow entries that match one of `commands`, the files that could not be read)."""
    hits: list[tuple[Path, str]] = []
    unreadable: list[str] = []
    for f in files:
        f = Path(f)
        if not f.is_file():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            unreadable.append(f"{f} ({type(exc).__name__})")
            continue
        perms = data.get("permissions") if isinstance(data, dict) else None
        allow = perms.get("allow") if isinstance(perms, dict) else None
        for entry in allow if isinstance(allow, list) else []:
            if isinstance(entry, str) and any(rule_matches(entry, c) for c in commands):
                hits.append((f, entry))
    return hits, unreadable


# ── Windows: the box, wakefulness, liveness ───────────────────────────────────────────────────

def windows_box(title: str, text: str, timeout_s: float = BOX_TIMEOUT_S) -> str:
    """The owner's confirmation: 'yes', 'no', 'timeout', 'closed' or 'unavailable'. Only 'yes' loads."""
    if os.name != "nt":
        return "unavailable"
    try:
        show = ctypes.WinDLL("user32", use_last_error=True).MessageBoxTimeoutW
        show.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint,
                         ctypes.c_ushort, ctypes.c_uint]
        show.restype = ctypes.c_int
        answer = int(show(None, text, title, BOX_FLAGS, 0, int(timeout_s * 1000)))
    except Exception:  # noqa: BLE001
        return "unavailable"
    return _BOX_ANSWERS.get(answer, "closed" if answer else "unavailable")


def keep_awake(on: bool) -> bool:
    """Hold (or let go of) the 'system required' state for the calling thread, so the PC does not
    go to sleep in the middle of a load."""
    if os.name != "nt":
        return False
    try:
        fn = ctypes.WinDLL("kernel32", use_last_error=True).SetThreadExecutionState
        fn.argtypes = [ctypes.c_uint]
        fn.restype = ctypes.c_uint
        return bool(fn(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0)))
    except Exception:  # noqa: BLE001
        return False


def process_created(pid: int) -> int | None:
    """When the process with this id was created (an opaque number), None when it cannot be told.
    Windows gives process ids out again; the creation time tells a worker from a stranger."""
    try:
        if os.name != "nt":
            fields = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
            return int(fields[19])
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.GetProcessTimes.argtypes = [ctypes.c_void_p] * 5
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = k32.OpenProcess(0x1000, 0, int(pid))                # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            times = (ctypes.c_ulonglong * 4)()
            ok = k32.GetProcessTimes(handle, ctypes.byref(times, 0), ctypes.byref(times, 8),
                                     ctypes.byref(times, 16), ctypes.byref(times, 24))
            return int(times[0]) if ok else None
        finally:
            k32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return None


def worker_alive(lock: dict) -> bool:
    """Is the process that holds this lock still running? Read with OpenProcess (scripts.ops.status),
    never by signalling it: on Windows a signal ENDS the process."""
    try:
        pid = int(lock.get("pid") or 0)
    except (TypeError, ValueError):
        return False
    if not status.pid_alive(pid):
        return False
    then, now = lock.get("pid_created"), process_created(pid)
    if then is None or now is None:
        return True                          # cannot tell it from a stranger: never start a second load
    return int(then) == int(now)


# ── children ──────────────────────────────────────────────────────────────────────────────────

@dataclass
class Child:
    code: int
    text: str
    timed_out: bool = False


def child_env(base=None) -> dict:
    """What every child starts with: the environment this program was STARTED with (LAUNCH_ENV,
    never os.environ: by the time a child is started, scripts.ops.db has blanked the message and
    model keys of this process, and a key that is set, even to nothing, is never filled from the
    project's .env), UTF-8 forced, no PYTHONIOENCODING (the PowerShell tool sets one that the
    engine's own children would inherit) and neither of the launcher's two words to its worker.
    So an engine step runs here exactly as it runs when it is started by hand."""
    env = dict(LAUNCH_ENV if base is None else base)
    env["PYTHONUTF8"] = "1"
    for name in ("PYTHONIOENCODING", NONCE_ENV, BREAKAWAY_ENV):
        env.pop(name, None)
    return env


def run_child(argv: list, *, cwd: Path, env: dict, log: Callable[[str], None] | None = None,
              timeout: float | None = None) -> Child:
    """Run one child to its end. Output is read as BYTES and decoded here (UTF-8, replacement):
    an em dash or a stray cp1252 byte in a report line can never stop the worker."""
    proc = subprocess.Popen([str(a) for a in argv], cwd=str(cwd), env=env, shell=False,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            close_fds=True)
    expired = threading.Event()

    def _expire() -> None:
        expired.set()
        proc.kill()

    timer = threading.Timer(timeout, _expire) if timeout else None
    if timer is not None:
        timer.daemon = True
        timer.start()
    parts: list[str] = []
    try:
        for raw in proc.stdout:
            text = raw.decode("utf-8", errors="replace")
            parts.append(text)
            if log is not None:
                log("  | " + text.rstrip("\r\n"))
        code = proc.wait()
    finally:
        if timer is not None:
            timer.cancel()
        proc.stdout.close()
    return Child(int(code), "".join(parts), expired.is_set())


def worker_python() -> tuple[str, dict]:
    """(the interpreter the worker is started with, what its environment needs for that).
    Inside a virtual environment on Windows sys.executable is a small redirector that starts the
    real interpreter as a child of ITS own. The worker would then not be the launcher's own child,
    and that is the very thing it checks before it loads anything. So there the real interpreter is
    started directly and told which environment it belongs to, the way the standard library's
    multiprocessing starts its children."""
    base = getattr(sys, "_base_executable", None)
    here = sys.executable or "python"
    if os.name == "nt" and base and sys.prefix != getattr(sys, "base_prefix", sys.prefix) \
            and os.path.normcase(os.path.abspath(base)) != os.path.normcase(os.path.abspath(here)):
        return str(base), {"__PYVENV_LAUNCHER__": here}
    return here, {}


def spawn_worker(run: str, root: Path, nonce: str, log_path: Path):
    """Start the detached worker and return at once. No window, its own process group, outside the
    editor's job where Windows allows it (so closing Claude Code cannot take the load with it); its
    output goes to the run's worker log; nothing of the launcher's is inherited. The process it
    returns carries `breakaway`: False = Windows kept the worker inside the editor's job."""
    python, needs = worker_python()
    argv = [python, "-X", "utf8", "-m", MODULE, "--worker", run]
    env = child_env()
    env.update(needs)
    env[NONCE_ENV] = nonce
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as log:
        common = dict(cwd=str(root), stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                      close_fds=True, shell=False)
        if os.name != "nt":
            return subprocess.Popen(argv, start_new_session=True, env=env, **common)
        flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
        try:
            proc, outside = subprocess.Popen(argv, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB,
                                             env=dict(env, **{BREAKAWAY_ENV: "1"}), **common), True
        except OSError:                      # a job that forbids leaving it: the planned flags alone
            proc, outside = subprocess.Popen(argv, creationflags=flags, env=dict(env, **{BREAKAWAY_ENV: "0"}),
                                             **common), False
        proc.breakaway = outside             # the launcher tells the owner when it is False
        return proc


def _to_log(text: str) -> None:
    """The worker's stdout IS the run's worker log (the launcher points it there)."""
    try:
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
        sys.stdout.flush()
    except Exception:  # noqa: BLE001
        pass


# ── the lock, its heartbeat, the journal ──────────────────────────────────────────────────────

def word_hash(word: str) -> str:
    """What the lock and the journal keep of the launcher's one-time word: its sha256, written in
    letters only (as the token is). The word itself lives in the launcher's memory and in the
    worker's environment and is never written to a file: nobody can read it back from the lock and
    start a worker by hand."""
    return loadplan.letters_only(hashlib.sha256(str(word).encode("utf-8")).hexdigest())


class LockFile:
    """business_data/ops/LOCK: {run, pid, pid_created, step, started, heartbeat, word_sha, ...}.
    scripts.ops.status reads run, pid, step and heartbeat; every skill reads the lock through it.
    word_sha is word_hash() of the launcher's one-time word: it tells whose lock this is."""

    def __init__(self, run: str, word_sha: str, data: dict | None = None):
        self.path = paths.lock_file()
        self.data = dict(data or {})
        self.data.pop("nonce", None)         # a word in clear never goes back to disk
        self.data.update(run=run, word_sha=word_sha)
        self.released = False
        self._mutex = threading.Lock()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _text(self) -> str:
        self.data["heartbeat"] = self._now()
        return json.dumps(self.data, sort_keys=True, default=str) + "\n"

    def create(self, replacing: dict | None = None) -> None:
        """Take the lock. The file is created exclusively, so two launchers can never both hold it.
        `replacing` is the dead lock of an interrupted load that is being taken over."""
        with self._mutex:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if replacing is not None:
                self._set_aside(replacing)
            self.data.setdefault("started", self._now())
            self.data.setdefault("step", "start")
            self.data.update(pid=os.getpid(), pid_created=process_created(os.getpid()))
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                raise LockTaken() from None
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(self._text())

    def _set_aside(self, dead: dict) -> None:
        aside = self.path.with_name("LOCK." + str(self.data["word_sha"])[:8] + ".old")
        try:
            os.rename(self.path, aside)
        except FileNotFoundError:
            return                           # already gone: the exclusive create decides
        except OSError:
            raise LockTaken() from None
        try:                                 # a busy file is read again before it is taken for somebody's
            seen = _again(lambda: json.loads(aside.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            seen = {}
        if seen.get("word_sha") != dead.get("word_sha") or seen.get("pid") != dead.get("pid"):
            try:                             # it was somebody's live lock: give it back
                os.rename(aside, self.path)
            except OSError:
                self._drop(aside)
            raise LockTaken()
        self._drop(aside)

    @staticmethod
    def _drop(aside: Path) -> None:
        """Remove the dead lock that was set aside. A file a scanner holds at this moment is left
        where it is (nothing reads it): the take-over of an interrupted load never fails on it."""
        try:
            aside.unlink(missing_ok=True)
        except OSError:
            pass

    def update(self, **changes) -> bool:
        """Rewrite the lock (a fresh heartbeat every time). False when the file could not be written
        this once (a reader or the sync client holds it): the next beat writes it."""
        with self._mutex:
            self.data.update(changes)
            if self.released:
                return False
            try:
                paths.write_text(self.path, self._text())
                return True
            except OSError:
                return False

    def release(self) -> bool:
        """Remove the lock if it is still ours."""
        with self._mutex:
            self.released = True
            for _ in range(40):
                try:
                    held = json.loads(self.path.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    return True
                except (OSError, ValueError):
                    held = None
                if held is not None and held.get("word_sha") != self.data.get("word_sha"):
                    return False
                try:
                    self.path.unlink()
                    return True
                except FileNotFoundError:
                    return True
                except OSError:
                    time.sleep(0.25)
            return False


class Heartbeat(threading.Thread):
    """Refreshes the lock's heartbeat (and step) while the worker's main thread waits on a child."""

    def __init__(self, lock: LockFile, interval: float):
        super().__init__(daemon=True, name="yq-load-heartbeat")
        self._lockfile, self._interval, self._halt = lock, max(0.01, float(interval)), threading.Event()

    def run(self) -> None:
        while not self._halt.wait(self._interval):
            self._lockfile.update()

    def stop(self) -> None:
        self._halt.set()
        if self.is_alive():
            self.join(timeout=5)


def journal(run: str, event: str, **fields) -> bool:
    """One line in the run's journal; False when it could not be written. The folder sits in
    OneDrive: a reader or the sync client may hold the file for a moment, so a refused write is tried
    again (for about 20 s) instead of ending the load."""
    fields = {k: v for k, v in fields.items() if v is not None}
    for attempt in range(12):
        try:
            loadplan.journal_append(run, event, **fields)
            return True
        except OSError:
            time.sleep(0.25 * (attempt + 1))
    _to_log(f"(journal line not written: {event} {fields.get('step') or ''})")
    return False


def _resume_line(run: str) -> str:
    """What resumes an interrupted run: its own GO line once more (the launcher sees the dead lock)."""
    try:
        token = loadplan.read_plan(run).get("token")
    except (OSError, ValueError, AttributeError):
        token = None
    return loadplan.go_line(run, token) if token else f"(the plan of run {run} cannot be read: tell Claude)"


def _replaced_by(run: str, plan: dict | None = None) -> str | None:
    """The newer check that replaced this one (None = none did). The load check writes
    `superseded_by` into the plan of every earlier check of a folder when it retires them: a table
    he was shown and that a newer table has replaced is never loaded. With no plan given, the
    plan file is read as it is now (a file that cannot be read at this moment says nothing)."""
    if plan is None:
        try:
            plan = loadplan.read_plan(run)
        except (OSError, ValueError):
            return None
    newer = plan.get("superseded_by") if isinstance(plan, dict) else None
    if not newer:
        return None
    return _short(str(newer), 40) if isinstance(newer, str) else "a newer one"


def _replaced_words(newer: str, late: bool = False) -> str:
    """The refusal of a check that a newer one replaced (`late`: found out after the box)."""
    named = f" (run {newer})" if loadplan.is_run(newer) else ""
    if late:
        return (f"This check was replaced by a newer one{named} while the box was open. Nothing was started: "
                "type /yq-load.")
    return f"This check was replaced by a newer one{named}: type /yq-load."


def _clear_dead_lock(lock: dict) -> None:
    """Remove a lock whose process is gone, if the file still is the one that was read (a file
    that cannot be read at this moment is left alone: it may be somebody's new lock)."""
    f = paths.lock_file()
    try:
        raw = f.read_text(encoding="utf-8")
    except OSError:
        return
    try:
        held = json.loads(raw)
        same = held.get("word_sha") == lock.get("word_sha") and held.get("pid") == lock.get("pid")
    except (ValueError, AttributeError):
        same = lock.get("run") == "?"        # an unreadable lock, read as unreadable before
    if same:
        try:
            f.unlink()
        except OSError:
            pass


def _again(read):
    """Call `read()` (a reader of the lock file), once more after a pause when Windows refuses it.
    A running worker replaces the lock every 10 s, and a read that meets that very moment fails
    with a PermissionError: that is a busy file, never a reason to stop. (scripts.ops.status tries
    its own read again as well; this program is more patient still, because here the answer
    decides whether a load starts.)"""
    for attempt in range(8):
        try:
            return read()
        except OSError:
            time.sleep(0.05 * (attempt + 1))
    return read()


def _read_lock() -> dict | None:
    """The lock as scripts.ops.status reads it: None = no lock, run '?' = a file that is no lock.
    Raises OSError only when the file is there and cannot be read for several seconds."""
    return _again(status.read_lock)


def _lock_unreadable(exc: OSError, nothing: str) -> str:
    return (f"The load lock could not be read ({type(exc).__name__}): another program holds the file, and a load "
            f"may be running. {nothing}: try again in a minute.")


def _began(run: str, rows: list[dict] | None = None) -> bool:
    """Has a refresh of this run ever started? Every load deletes and re-inserts the whole stock
    ledger, so from that journal line on the database may hold part of the load until a load
    finishes: nothing said or journalled after it may claim that nothing was written."""
    rows = loadplan.journal_read(run) if rows is None else rows
    return any(r.get("event") == "step_start" and r.get("step") == "refresh" for r in rows)


def _stopped_step(run: str, lock: dict | None = None) -> str:
    """The step an interrupted load stands at: the one its lock names, unless that lock was left by a
    launcher whose worker never got going (it still says 'start'): then the journal's last step."""
    at = str((lock or {}).get("step") or "").replace(" (rehearsal)", "")
    if at in ("", "start", "?"):
        at = loadplan.last_step(run) or "start"
    return at


def _held_words(lock: dict, nothing: str) -> str:
    """Why nothing starts while a live process holds the lock. A worker that is alive but has not
    refreshed its heartbeat reads INTERRUPTED in every skill (scripts.ops.status goes by the
    heartbeat), and 'a load is running: follow it' would then send the owner in a circle; that case
    has a sentence of its own, which also says what he can do. No second loader is ever started
    beside a live process, a rehearsal's included."""
    held_by, at = str(lock.get("run") or "?"), str(lock.get("step") or "start")
    rehearsal = held_by.startswith(REHEARSAL_PREFIX)
    what = "rehearsal" if rehearsal else "load"
    try:
        seen = _again(status.lock_state)
    except OSError:
        seen = {}
    # by the heartbeat a silent worker reads 'interrupted'; a silent REHEARSAL reads 'none' with its
    # run under 'rehearsal_left' (a rehearsal is never an interrupted load). The process is there.
    silent = seen.get("rehearsal_left") == held_by or (seen.get("state") == "interrupted"
                                                       and seen.get("run") == held_by)
    if silent:
        minutes = max(1, status.HEARTBEAT_STALE_S // 60)
        pid = lock.get("pid")
        said = (f"The {what}'s worker is still on this PC but has written nothing for more than {minutes} minutes "
                f"(run {held_by}, at {at}). {nothing} and nothing is resumed while it is there: ask Claude to check it")
        try:                                 # is this process the worker for sure (born when the lock says)?
            sure = lock.get("pid_created") is not None and process_created(int(pid)) is not None
        except (TypeError, ValueError):
            sure = False
        if not sure:                         # it may be another program that was given the same number
            return said + f" (it could not be confirmed that process {pid} is that worker: do not end it yourself)."
        after = "; the next load or rehearsal clears its lock." if rehearsal else ", then type /yq-load to resume."
        return said + f"; if it has truly hung, end python.exe (pid {pid}) in Task Manager{after}"
    return f"A {what} is running (run {held_by}, at {at}). {nothing}: follow it with {FOLLOW_LINE}"


# ── reading the plan ──────────────────────────────────────────────────────────────────────────

def is_rehearsal(run: str, plan: dict) -> bool:
    """A rehearsal is a run named 000000_<HHMM> whose plan says so. Both or neither."""
    named, flagged = run.startswith(REHEARSAL_PREFIX), bool(plan.get("rehearsal"))
    if named != flagged:
        raise Refusal(f"The plan of run {run} does not say clearly whether it is a rehearsal. "
                      "Nothing was started: type /yq-load.")
    return named


def _first(d: dict, *keys):
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return None


def _code(x) -> str:
    """A verdict or a flag as its code word: 'HOLD BACK' and 'hold-back' are 'hold_back'."""
    return str(x or "").strip().lower().replace(" ", "_").replace("-", "_")


def verdict_rows(plan: dict) -> list[tuple[str, str, str]]:
    """(file, verdict, the decision in words) per line of the check's table. The load check writes a
    list of {file, label, verdict, decision, staged, ...}; a looser shape is still read."""
    raw = plan.get("verdicts")
    items = list(raw.items()) if isinstance(raw, dict) else [(None, v) for v in raw] if isinstance(raw, list) else []
    out: list[tuple[str, str, str]] = []
    for key, v in items:
        if isinstance(v, dict):
            label = _first(v, "label", "report", "kind")
            name = str(_first(v, "file", "name") or (f"(no {label})" if label else key or "?"))
            verdict = _code(_first(v, "verdict", "status", "decision"))
            words = str(v.get("decision") or VERDICT_WORDS.get(verdict, verdict.replace("_", " ")))
            if verdict == "needs_decision" and not v.get("staged"):
                words += " (left out of this load)"
        elif key is None:
            name, verdict, words = str(v), "", ""
        else:
            verdict = _code(v)
            name, words = str(key), VERDICT_WORDS.get(verdict, verdict.replace("_", " "))
        out.append((name, verdict, words))
    return out


def plan_stops(plan: dict) -> list[str]:
    """Why this plan may not be loaded: every line the check marked STOP, and its own list of stops."""
    out = [name for name, verdict, _words in verdict_rows(plan) if verdict == "stop"]
    stops = plan.get("stops")
    if isinstance(stops, (list, tuple)):
        out += [str(s) for s in stops]
    elif stops:
        out.append(str(stops))               # one sentence instead of a list is a stop all the same
    if str(plan.get("verdict") or "").strip().lower() == "stop" or plan.get("stop") or plan.get("ok") is False:
        out.append("the check as a whole")
    return out


def drop_path(plan: dict) -> Path:
    p = Path(str(plan.get("drop") or ""))
    return p if p.is_absolute() else paths.data_root() / p


def _iso_dates(values) -> list[date]:
    out = []
    for v in values:
        try:
            out.append(date.fromisoformat(str(v)[:10]))
        except ValueError:
            continue
    return out


def as_on_date(plan: dict, run: str) -> date | None:
    """The as-on date the loaded set is published under: the newest 'As on date' of the titled
    reports (a price book is dated by its export, so it does not count while a report does), else
    the date the run's name spells."""
    raw = plan.get("as_on")
    if isinstance(raw, dict):
        found = (_iso_dates(v for k, v in raw.items() if not str(k).startswith("selling_prices"))
                 or _iso_dates(raw.values()))
    else:
        found = _iso_dates(raw if isinstance(raw, list) else [raw])
    return max(found) if found else paths.drop_date(run[:6])


def _short(text: str, width: int = 96) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[:width - 3] + "..."


def _sentence(text: str, width: int = FIELD) -> str:
    """A reason as one sentence of the journal: a capital first letter, a full stop, at most
    `width` characters. The status screen prints a row's text, why and next and nothing else, so
    each of them must stand as a sentence on its own."""
    text = " ".join(str(text).split())
    if text[:1].islower():
        text = text[:1].upper() + text[1:]
    if text and text[-1] not in ".?!":
        text += "."
    return text if len(text) <= width else text[:width - 3] + "..."


def profit_held_since(plan: dict, run: str, previous: dict | None) -> str | None:
    """state.json's last_load.profit_held_since for this load: the report day since which the
    profitability report is held back (the brief prints 'held back since' from it), or None for
    no such key. `previous` is the last_load of the load before.
      this load held the report back      the day the load before wrote down, when it wrote one;
                                          else the as-on date of the report that is held now
      the report was not in this drop     what the load before wrote down: nothing changed
      the report loaded                   None: nothing is held back any more"""
    kept = _iso_dates([previous.get("profit_held_since")] if isinstance(previous, dict) else [])
    before = kept[0].isoformat() if kept else None
    rows = plan.get("verdicts") if isinstance(plan.get("verdicts"), list) else []
    verdicts = [_code(_first(v, "verdict", "status", "decision")) for v in rows
                if isinstance(v, dict) and str(v.get("kind") or "") == PROFIT_KIND]
    if "load" in verdicts:
        return None
    if "hold_back" not in verdicts:
        return before
    if before:
        return before
    for dates in (plan.get("as_on_all"), plan.get("as_on")):
        found = _iso_dates([dates.get(PROFIT_KIND)]) if isinstance(dates, dict) else []
        if found:
            return found[0].isoformat()
    day = (_iso_dates([plan.get("data_date")]) or [as_on_date(plan, run)])[0]
    return day.isoformat() if day else None


def expected_lines(plan: dict) -> list[str]:
    """The figures the check expects after the load, as the check worded them (they are shown here,
    never computed here)."""
    exp = plan.get("expected")
    if isinstance(exp, dict):
        exp = exp["lines"] if isinstance(exp.get("lines"), list) else [f"{k}: {v}" for k, v in exp.items()]
    if isinstance(exp, str):
        exp = exp.splitlines()
    return [str(ln) for ln in exp if str(ln).strip()] if isinstance(exp, list) else []


def option_flags(plan: dict) -> list[str]:
    """The options the owner gave the check that change what a Yes does, as he typed them."""
    opts = plan.get("options")
    if not isinstance(opts, dict):
        return []
    return [flag for key, flag, _words in OPTION_WORDS
            if (opts.get(key) == "load" if key == "pricebook" else bool(opts.get(key)))]


def option_lines(plan: dict) -> list[str]:
    """One line of the box per such option: a Yes with --replace-ok is not an ordinary Yes."""
    on = option_flags(plan)
    return [f"ON YOUR WORD ({flag}): {words}" for _key, flag, words in OPTION_WORDS if flag in on]


def option_mismatch(plan: dict, staging: Path) -> list[str]:
    """Where the plan's options and the loader's marker files in the staged folder disagree ([] =
    they agree). The check writes a marker only on the owner's option, and the box shows the
    option: a marker the box would not show, or an option the loader would not see, is never loaded."""
    opts = plan.get("options") if isinstance(plan.get("options"), dict) else {}
    try:
        names = [p.name for p in Path(staging).iterdir() if p.is_file()]
    except OSError:
        names = []
    out: list[str] = []
    for marker in loadplan.MARKERS:
        key = marker.lower()
        flag = "--" + key.replace("_", "-")
        there = any(n.split(".")[0].upper() == marker.upper() for n in names)     # the loader's own rule
        asked = bool(opts.get(key))
        if there and not asked:
            out.append(f"the staged folder holds the marker file {marker}, but the table did not show the "
                       f"option {flag}")
        elif asked and not there:
            out.append(f"the table showed the option {flag}, but its marker file {marker} is not in the "
                       "staged folder")
    return out


def one_time_marker(name: str) -> bool:
    """Is this staged file the marker of a one-time permission (ONE_TIME_OPTIONS)? Read by the
    loader's own rule for a marker: its name before the first dot, in any letter case."""
    stem = str(name).split(".")[0].lower()
    return any(marker.lower() == stem and marker.lower() in ONE_TIME_OPTIONS for marker in loadplan.MARKERS)


def attention_lines(plan: dict) -> list[str]:
    """What the check flagged that he must have seen before a Yes, one line of the box each, every
    one starting 'ATTENTION:'. The check keeps its flags in plan['flags'] as code words; a flag may
    also be a sentence, and plan['attention'] (sentences) is read the same way. A routine flag
    (ROUTINE_FLAGS) gives no line. A code word this file does not know still gives one, under its
    own name: nothing the check flags is ever dropped in silence."""
    raw: list = []
    for key in ("attention", "flags"):
        got = plan.get(key)
        raw += list(got) if isinstance(got, (list, tuple)) else [got] if isinstance(got, str) else []
    out: list[str] = []
    for item in raw:
        text = " ".join(str(item or "").split())
        word = _code(text)
        if not text or word in ROUTINE_FLAGS:
            continue
        if word in ATTENTION_WORDS:
            text = ATTENTION_WORDS[word]
        elif " " not in text:
            name = text.replace("_", " ")
            text = f"the check marked this load '{name}': read its line in the chat before you answer."
        if text.upper().startswith("ATTENTION"):
            text = text[len("ATTENTION"):].lstrip(" :")
        line = _short("ATTENTION: " + text, 150)
        if line not in out:
            out.append(line)
    return out


def box_text(run: str, plan: dict, *, resume_step: str | None = None, rehearsal: bool = False) -> tuple[str, str]:
    """(title, text) of the Yes/No box: the folder, one line per file with its decision, what the
    check flagged (ATTENTION), the options that change what a Yes does, the expected figures."""
    title = f"YQ load {run[:6]}" + (" (REHEARSAL)" if rehearsal else "")
    lines: list[str] = []
    if rehearsal:
        lines += ["REHEARSAL: nothing is loaded and the database is not touched.", ""]
    if resume_step:
        lines += [f"RESUME: the load of this folder was interrupted at step '{resume_step}'.",
                  "The whole load runs again from the same files.", ""]
    lines += [f"Folder: {drop_path(plan)}", ""]
    rows = verdict_rows(plan)
    for name, _verdict, words in rows[:16]:
        lines.append(_short(f"{name}: {words}" if words else name, 110))
    if len(rows) > 16:
        lines.append(f"(+{len(rows) - 16} more lines: see the table in the chat)")
    others = plan.get("not_loaded")
    if isinstance(others, list) and others:
        lines.append(f"(+{len(others)} other file(s) or folder(s): NOT LOADED by this command)")
    flagged = attention_lines(plan)
    if flagged:
        lines += [""] + flagged[:ATTENTION_MAX]
        if len(flagged) > ATTENTION_MAX:
            lines.append(f"ATTENTION: (+{len(flagged) - ATTENTION_MAX} more such lines: see the table in the chat)")
    said = option_lines(plan)
    if said:
        lines += [""] + said
    exp = expected_lines(plan)
    if exp:
        lines += ["", "After the load you should see:"] + ["- " + _short(ln, 150) for ln in exp[:10]]
        if len(exp) > 10:
            lines.append(f"(+{len(exp) - 10} more: see the table in the chat)")
    lines += ["", "Yes = load now. No = nothing changes."]
    return title, "\n".join(lines)


# ── the token ─────────────────────────────────────────────────────────────────────────────────

def _same_commit_form(old: str, new: str) -> str:
    """/health may report a short commit and the check may have stored a long one (or the reverse):
    one commit written two ways is not a change."""
    a, b = old.lower(), new.lower()
    if a and b and a != b and min(len(a), len(b)) >= 7 and (a.startswith(b) or b.startswith(a)):
        return old
    return new


def token_moved(plan: dict, deps: "Deps", with_fingerprint: bool) -> list[str]:
    """The parts of the token that differ from what the table showed, in plain words ([] = none).
    Without the fingerprint (a resume) only the files and the two commits are compared."""
    old = dict(plan.get("token_parts") or {})
    new = dict(deps.collect_parts(plan, with_fingerprint))
    new["live"] = _same_commit_form(str(old.get("live") or ""), str(new.get("live") or ""))
    if not with_fingerprint:
        return loadplan.moved_parts(dict(old, fingerprint={}), dict(new, fingerprint={}))
    if loadplan.token_of(new) == plan.get("token"):
        return []
    return loadplan.moved_parts(old, new) or ["the plan of this run (its token is not the one its own parts give)"]


def tick_wait_seconds(now: datetime) -> float:
    """0 when a load may start now, else the seconds until it may: 1 to 5 minutes after a
    quarter-hour (UTC), and never from 07:50 to 08:20 Bahrain."""
    start = now.astimezone(timezone.utc)
    t = start
    for _ in range(6):
        local = t.astimezone(BAHRAIN)
        quiet_from = local.replace(hour=QUIET_FROM[0], minute=QUIET_FROM[1], second=0, microsecond=0)
        quiet_until = local.replace(hour=QUIET_UNTIL[0], minute=QUIET_UNTIL[1], second=0, microsecond=0)
        if quiet_from <= local <= quiet_until:
            t = (quiet_until + timedelta(seconds=1)).astimezone(timezone.utc)
            continue
        into = (t.minute * 60 + t.second + t.microsecond / 1_000_000) % TICK_S
        if into < START_FROM_S:
            t = t + timedelta(seconds=START_FROM_S - into)
            continue
        if into > START_UNTIL_S:
            t = t + timedelta(seconds=TICK_S - into + START_FROM_S)
            continue
        break
    return max(0.0, (t - start).total_seconds())


# ── what the launcher and the worker are given ────────────────────────────────────────────────

@dataclass
class Deps:
    """Everything the launcher and the worker touch outside this file. real_deps() wires the real
    world, rehearsal_deps() a sandbox inside the run folder, tests their own stand-ins."""
    rehearsal: bool
    root: Path                                           # this checkout: cwd of the children
    runner: Callable[[str, list, float | None], Child]   # (label, argv, timeout) -> Child
    collect_parts: Callable[[dict, bool], dict]          # (plan, with_fingerprint) -> the token's parts now
    ingest_runs: Callable[[int | None], tuple[int, int]]   # (after id) -> (newest id, rows above it)
    archive: Callable[[Path, Path, str], tuple[Path, bool]]   # (folder, dest, stamp) -> (archive, encrypted)
    backup_root: Callable[[], Path]
    backup_keep: Callable[[], int]
    gates_dir: Path
    staging: Path
    clean_dir: Path
    published_dir: Callable[[date], Path]
    superseded_dir: Path
    read_state: Callable[[], dict]                       # state.json as it is (the load before this one)
    write_state: Callable[[dict], object]
    box: Callable[[str, str, float], str]
    spawn: Callable[[str, Path, str, Path], object]
    settings_files: list
    linked_worktree: Callable[[], bool]
    prepare: Callable[[], None] | None = None
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    sleep: Callable[[float], None] = time.sleep
    log: Callable[[str], None] = _to_log
    keep_awake: Callable[[bool], bool] = keep_awake
    wait_for_tick: bool = True
    start_wait_s: float = START_WAIT_S
    heartbeat_s: float = HEARTBEAT_S


def _real_prepare() -> None:
    """Before this process reads anything: the guard module (it blanks the message and model keys
    of THIS process; a child never gets them from here, see child_env), the project's .env, and
    the settings a load needs."""
    from scripts.ops import db  # noqa: F401
    import app.config  # noqa: F401
    config.get("api.health_url")
    config.get("load.backup_keep")


def _real_parts(plan: dict, with_fingerprint: bool, root: Path) -> dict:
    from scripts.ops import db
    live = str(db.health(config.get("api.health_url")).get("commit") or "")
    drop, run = drop_path(plan), str(plan["run"])
    if not with_fingerprint:
        return {"drop": loadplan.manifest(drop), "staging": loadplan.manifest(loadplan.staging_path(run)),
                "head": gitinfo.head(root) or "", "live": live}
    with db.session() as session:            # read-only: asserted by scripts.ops.db before it is handed out
        return loadplan.collect_parts(drop, run, root, live, session)


def _real_ingest_runs(after_id: int | None) -> tuple[int, int]:
    """(the newest ingest_runs id, the rows written above `after_id`), through the read-only session."""
    from scripts.ops import db
    with db.session() as session:
        if after_id is None:
            return int(session.q("select coalesce(max(id), 0) as max_id from ingest_runs")[0]["max_id"]), 0
        row = session.q("select count(*) as new_rows, coalesce(max(id), 0) as max_id from ingest_runs where id > %s",
                        (int(after_id),))[0]
        return max(int(after_id), int(row["max_id"])), int(row["new_rows"])


def _real_backup_root() -> Path:
    from scripts import nightly_backup
    return Path(nightly_backup.backup_root())


def _real_archive(folder: Path, dest: Path, stamp: str) -> tuple[Path, bool]:
    from scripts import nightly_backup as nb
    archive, encrypted = nb.make_archive(folder, dest, stamp, password=os.environ.get(nb.PASSWORD_ENV) or None,
                                         exe=nb.find_7z())
    if encrypted and (dest / nb.WARNING_FILE).exists():
        (dest / nb.WARNING_FILE).unlink()
    return Path(archive), bool(encrypted)


class RealRunner:
    """Starts one engine step as a child process: the ONLY thing in this file that does, and the
    only one that asks for the engine's environment (child_env: what this program was started
    with). A rehearsal can never hold one: the constructor refuses a rehearsal run."""

    def __init__(self, root: Path, run: str, log: Callable[[str], None] = _to_log):
        if run.startswith(REHEARSAL_PREFIX):
            raise RuntimeError("a rehearsal never gets the real runner")
        self.root, self.run, self.log = Path(root), run, log

    def __call__(self, label: str, argv: list, timeout: float | None = None) -> Child:
        return run_child(argv, cwd=self.root, env=child_env(), log=self.log, timeout=timeout)


def real_deps(run: str, plan: dict, root: Path = ROOT) -> Deps:
    if run.startswith(REHEARSAL_PREFIX) or plan.get("rehearsal"):
        raise RuntimeError("a rehearsal never gets the real world")
    root = Path(root)
    return Deps(
        rehearsal=False, root=root, runner=RealRunner(root, run),
        collect_parts=lambda p, with_fp: _real_parts(p, with_fp, root),
        ingest_runs=_real_ingest_runs, archive=_real_archive, backup_root=_real_backup_root,
        backup_keep=lambda: int(config.get("load.backup_keep")),
        gates_dir=root / "business_data" / "gates", staging=loadplan.staging_path(run),
        clean_dir=root / "data" / "clean", published_dir=paths.published_dir,
        superseded_dir=paths.staging_dir() / "superseded", read_state=status.read_state,
        write_state=status.write_state, box=windows_box, spawn=spawn_worker, settings_files=settings_files(root),
        linked_worktree=lambda: gitinfo.is_linked_worktree(root), prepare=_real_prepare)


# ── the rehearsal's world ─────────────────────────────────────────────────────────────────────

class StubRunner:
    """The only runner a rehearsal can have. It starts no process: it sleeps its share of the
    rehearsal's seconds and answers the way a step that passed would, writing its stand-in files
    inside the run folder and nowhere else."""

    SHARES = {"backup": 25, "pre_gate": 10, "refresh": 40, "reconcile": 10, "post_gate": 10, "compare": 5}

    def __init__(self, seconds: float, sandbox: Path, sleep: Callable[[float], None] = time.sleep):
        self.seconds, self.sandbox, self._sleep = max(0.0, float(seconds)), Path(sandbox).resolve(), sleep

    def _inside(self, path: Path) -> Path:
        p = Path(path).resolve()
        if p != self.sandbox and self.sandbox not in p.parents:
            raise RuntimeError(f"a rehearsal writes only inside its run folder, not {p}")
        return p

    def __call__(self, label: str, argv: list, timeout: float | None = None) -> Child:
        self._sleep(self.seconds * self.SHARES.get(label, 0) / 100.0)
        argv = [str(a) for a in argv]
        if label == "backup":
            out = self._inside(Path(argv[argv.index("--out") + 1]))
            paths.write_text(out / "manifest.json", "{}\n")
            text = f"Backup complete: {out}  (0 tables, git rehearsal, verify OK)"
        elif label in ("pre_gate", "post_gate"):
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
            name = stamp + "_" + argv[argv.index("--label") + 1] + ".json"
            paths.write_text(self._inside(self.sandbox / "gates" / name),
                             json.dumps({"rehearsal": True, "tables": {}}) + "\n")
            text = f"snapshot {name}: rehearsal"
        elif label == "refresh":
            text = REFRESH_MARK + json.dumps({"ok": True, "data_as_of": None, "error": None, "loaded": {},
                                              "verify_ok": True, "verify_error": None, "loader_notices": []})
        elif label == "reconcile":
            text = "RECONCILED (rehearsal: nothing was compared)"
        elif label == "compare":
            text = "PASS: 0 failure(s), 0 explained delta(s)"
        else:
            raise RuntimeError(f"a rehearsal has no stand-in for the step '{label}'")
        return Child(0, text + "\n", False)


class _RehearsalRuns:
    """ingest_runs for a rehearsal: an invented counter that grows by two per load. No database."""

    def __init__(self) -> None:
        self.newest = 0

    def __call__(self, after_id: int | None) -> tuple[int, int]:
        if after_id is None:
            return self.newest, 0
        self.newest = int(after_id) + NEW_RUNS_PER_LOAD
        return self.newest, NEW_RUNS_PER_LOAD


def rehearsal_parts(box: Path) -> dict:
    """The token's parts of a rehearsal: its own two folders and invented commits. No git, no web
    address, no database."""
    return {"drop": loadplan.manifest(box / "drop"), "staging": loadplan.manifest(box / "staging"),
            "head": "rehearsal", "live": "rehearsal", "fingerprint": {"rehearsal": [0, 0]}}


def _stub_archive(folder: Path, dest: Path, stamp: str) -> tuple[Path, bool]:
    Path(dest).mkdir(parents=True, exist_ok=True)
    return Path(shutil.make_archive(str(Path(dest) / ("yq-backup_" + stamp)), "zip", root_dir=str(folder))), False


def rehearsal_deps(run: str, plan: dict, root: Path = ROOT) -> Deps:
    if not run.startswith(REHEARSAL_PREFIX):
        raise RuntimeError("only a run named 000000_<HHMM> is a rehearsal")
    box = loadplan.run_dir(run)

    def read_state() -> dict:                # the sandbox's own state file, never the real one
        try:
            data = json.loads((box / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def write_state(patch: dict) -> None:
        paths.write_text(box / "state.json", json.dumps(patch, indent=1, sort_keys=True, default=str) + "\n")

    return Deps(
        rehearsal=True, root=Path(root), runner=StubRunner(float(plan.get("seconds") or 0), box),
        collect_parts=lambda _plan, _with_fp: rehearsal_parts(box),
        ingest_runs=_RehearsalRuns(), archive=_stub_archive, backup_root=lambda: box / "backups",
        backup_keep=lambda: 2, gates_dir=box / "gates", staging=box / "staging", clean_dir=box / "clean",
        published_dir=lambda as_on: box / "published" / f"Focus ERP {as_on.isoformat()}",
        superseded_dir=box / "superseded", read_state=read_state, write_state=write_state, box=windows_box,
        spawn=spawn_worker, settings_files=settings_files(root),
        linked_worktree=lambda: gitinfo.is_linked_worktree(root), prepare=None, wait_for_tick=False)


def deps_for(run: str, plan: dict, root: Path = ROOT) -> Deps:
    """The one place that decides which world a run gets. A rehearsal gets the sandbox, and the
    sandbox holds no way to the engine or the database."""
    if is_rehearsal(run, plan):
        return rehearsal_deps(run, plan, root)
    return real_deps(run, plan, root)


# ── small file helpers ────────────────────────────────────────────────────────────────────────

def _remove_tree(folder: Path) -> bool:
    """Remove a folder of our own making; True when it is gone."""
    folder = Path(folder)
    for _ in range(3):
        if not folder.exists():
            return True
        shutil.rmtree(folder, ignore_errors=True)
        if folder.exists():
            time.sleep(0.5)
    return not folder.exists()


def empty_folder(folder: Path, sleep: Callable[[float], None] = time.sleep) -> list[str]:
    """Remove `folder` with everything in it; the files that survived ([] = it is empty). The loader
    loads every CSV it finds in data/clean, so a left-over file would be loaded as if it were new."""
    folder = Path(folder)
    left: list[str] = []
    for attempt in range(5):
        shutil.rmtree(folder, ignore_errors=True)
        left = sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file()) if folder.exists() else []
        if not left:
            return []
        if attempt < 4:
            sleep(1)
    return left


def _rename(src: Path, dst: Path) -> None:
    last: OSError | None = None
    for _ in range(5):
        try:
            os.rename(src, dst)
            return
        except OSError as exc:               # a report of that folder is open somewhere: try again
            last = exc
            time.sleep(1)
    raise last if last is not None else OSError(f"could not move {src}")


def publish_folder(staging: Path, target: Path, superseded: Path, run: str, now: datetime) -> Path | None:
    """Copy the loaded set to `target` through a temporary folder renamed into place. A folder
    already there is moved to `superseded` with a time suffix; its new place is returned.
    The set is every staged file but the marker of a one-time permission (one_time_marker): a
    permission given for this load is no property of the files. The marker of a partial export IS
    one and travels with them. Each file is copied by scripts.ops.paths.copy_file, which refuses
    any place outside business_data/ and exports/ of the data root."""
    if not paths.allowed_output(target):
        raise PermissionError(f"the loaded set is published only under business_data/ and exports/, not in {target}")
    want = {name: meta for name, meta in loadplan.manifest(staging).items() if not one_time_marker(name)}
    if not want:
        raise OSError(f"no staged file in {staging}")
    tmp = target.with_name(".publishing_" + run)
    _remove_tree(tmp)
    tmp.mkdir(parents=True)
    try:
        for name in want:
            paths.copy_file(staging / name, tmp / name)
        if loadplan.manifest(tmp) != want:
            raise OSError("the copy differs from the staged files")
        aside = None
        if target.exists():
            superseded.mkdir(parents=True, exist_ok=True)
            aside = superseded / (target.name + "_" + now.astimezone(BAHRAIN).strftime("%Y%m%d-%H%M%S"))
            _rename(target, aside)
        try:
            _rename(tmp, target)
        except OSError:
            if aside is not None and not target.exists():
                _rename(aside, target)       # put the earlier folder back
            raise
        return aside
    finally:
        if tmp.exists():
            _remove_tree(tmp)


def prune_archives(folder: Path, keep: int) -> list[str]:
    """Keep the newest `keep` pre-load archives of ours in `folder`; the names removed. Nothing
    else in the folder is ever touched."""
    ours = sorted((p for p in Path(folder).glob("yq-backup_*_pre-data-*")
                   if p.is_file() and p.suffix in (".7z", ".zip")), key=lambda p: (p.stat().st_mtime, p.name))
    gone: list[str] = []
    for p in ours[:-keep] if keep > 0 else []:
        try:
            p.unlink()
            gone.append(p.name)
        except OSError:
            continue
    return gone


def parse_refresh(text: str) -> dict | None:
    """The one result line the refresh child prints, None when it never got that far."""
    for line in reversed(text.splitlines()):
        if line.startswith(REFRESH_MARK):
            try:
                res = json.loads(line[len(REFRESH_MARK):])
            except ValueError:
                return None
            return res if isinstance(res, dict) else None
    return None


def _why(child: Child) -> str:
    last = next((ln.strip() for ln in reversed(child.text.splitlines()) if ln.strip()), "")
    out = f" (exit code {child.code}"
    if child.timed_out:
        out += "; it ran out of time"
    if last:
        out += f"; its last line: {last[:160]}"
    return out + ")"


def _pick(lines: list[str], words: tuple[str, ...], limit: int = 12) -> list[str]:
    return [ln.strip()[:200] for ln in lines if any(w in ln for w in words)][:limit]


# ── the launcher ──────────────────────────────────────────────────────────────────────────────

def launch(run: str, token: str, *, dev: bool = False, no_box: bool = False, deps: Deps | None = None,
           say: Callable[[str], None] = print) -> int:
    """The go. Returns the exit code; every sentence for the owner goes through `say`."""
    try:
        return _launch(str(run or ""), str(token or ""), dev, no_box, deps, say)
    except Refusal as exc:
        say(str(exc))
        return EXIT_REFUSED
    except CheckAgain as exc:
        if exc.written:                      # an interrupted load that had begun: never "nothing was loaded"
            say(f"CHECK AGAIN. {MAY_HOLD} Since the table was shown, this changed:")
        else:
            say("CHECK AGAIN: nothing was loaded. Since the table was shown, this changed:")
        for part in exc.moved:
            say(f"  - {part}")
        say("Type /yq-load to check again.")
        return EXIT_CHECK_AGAIN
    except NoAnswer as exc:
        say(str(exc))
        return EXIT_NO_ANSWER


def _launch(run: str, token: str, dev: bool, no_box: bool, deps: Deps | None, say) -> int:
    if not loadplan.is_run(run):
        raise Refusal(f"'{run}' is not a run name (ddmmyy_HHMM). Nothing was started: type /yq-load.")
    linked = deps.linked_worktree() if deps is not None else gitinfo.is_linked_worktree(ROOT)
    if linked and not dev:
        raise Refusal("This folder is a code worktree (.claude/worktrees), not the project folder: a load is "
                      "never started from here. Nothing was loaded.")
    if not (loadplan.run_dir(run) / "plan.json").is_file():
        raise Refusal(f"Run {run} has no load check to go from. Nothing was loaded: type /yq-load.")
    try:
        plan = loadplan.read_plan(run)
        if not isinstance(plan, dict):
            raise ValueError("the plan is not an object")
    except (OSError, ValueError) as exc:
        raise Refusal(f"The plan of run {run} cannot be read ({type(exc).__name__}). Nothing was loaded: "
                      "type /yq-load.") from None
    rehearsal = is_rehearsal(run, plan)
    if (dev or no_box) and not rehearsal:
        raise Refusal("--dev and --no-box are for a rehearsal only. Nothing was loaded.")
    if deps is None:
        deps = deps_for(run, plan)
    if not rehearsal and paths.data_root().resolve() != Path(deps.root).resolve():
        raise Refusal("These programs are not in the project folder that holds the data. Nothing was loaded: "
                      "open Claude Code on the main project folder.")

    # a load that is running, was interrupted, or is over. The lock is read from its file, not through
    # status.lock_state(): that one reads the dead lock of a cut-off REHEARSAL as no lock at all (it
    # is no interrupted load), and here that left-over still has to be cleared before a load starts
    try:
        lock = _read_lock()
    except OSError as exc:
        raise Refusal(_lock_unreadable(exc, "Nothing was started")) from None
    result, dead, resume = loadplan.run_result(run), None, False
    if lock is not None:
        held_by, at = str(lock.get("run") or "?"), str(lock.get("step") or "start")
        if worker_alive(lock):               # a load's worker, or a rehearsal's: nothing starts beside it
            raise Refusal(_held_words(lock, "Nothing was started"))
        if held_by == "?":
            _clear_dead_lock(lock)           # a launcher died while writing it: no worker was ever started
            say("(an unreadable left-over lock is cleared: no load was running under it)")
        elif held_by != run:
            if not held_by.startswith(REHEARSAL_PREFIX):
                raise Refusal(f"LOAD INTERRUPTED at {at} (run {held_by}): that load is finished first. "
                              f"Nothing was started. Its resume: {_resume_line(held_by)}")
            _clear_dead_lock(lock)
            say(f"(the rehearsal {held_by} had stopped at {at}; its left-over lock is cleared)")
        elif result in ("FINISHED", "FAILED"):
            _clear_dead_lock(lock)           # the run is over; only its lock was left behind
        else:
            dead, resume = lock, True
    if result == "FINISHED":
        raise Refusal(f"Run {run} is already loaded. Nothing to do: for new reports type /yq-load.")
    if result == "FAILED":
        raise Refusal(f"Run {run} was closed as FAILED and is refused for good. Type /yq-load for a new check.")
    if result == "RUNNING":
        resume = True                        # the journal says it started and nothing holds a live lock

    # a check that a newer check of the same folder replaced is never loaded: he has seen a newer table
    newer = _replaced_by(run, plan)
    if newer:
        if resume:
            # The run was cut off AND its check was replaced: it can never be resumed. It is closed
            # here and its dead lock goes; left as it is, the check would keep sending him to this
            # very line, and this line would keep refusing.
            began = _began(run)
            journal(run, "failed", result="failed" if began else "check_again", check_again=True,
                    step=_stopped_step(run, dead), written=began,
                    text="This interrupted load cannot be resumed: a newer check replaced its table."
                         + (" " + MAY_HOLD if began else ""),
                    why=_sentence(f"the check of run {newer} replaced this one"), next="Type /yq-load.")
            if dead is not None:
                _clear_dead_lock(dead)
            say("The interrupted load is closed: it cannot be resumed." + (" " + MAY_HOLD if began else ""))
        raise Refusal(_replaced_words(newer))

    stops = plan_stops(plan)
    if stops:
        raise Refusal(f"The load check of run {run} said STOP ({_short('; '.join(stops), 120)}): nothing can be "
                      "loaded from it. Type /yq-load again once that is settled.")
    if not plan.get("token"):
        raise Refusal(f"The load check of run {run} gave no token, so there is nothing to go from. "
                      "Nothing was loaded: type /yq-load.")

    hits, unreadable = allow_entries_matching(own_command_lines(run, token), deps.settings_files)
    if hits:
        lines = [f'  delete the entry "{entry}" from {f}' for f, entry in hits]
        raise Refusal("Refused: a Claude Code permission rule would let a load start without asking you. "
                      "Nothing was loaded.\n" + "\n".join(lines) + "\nThen type /yq-load again.")
    if unreadable:
        raise Refusal("Refused: a Claude Code settings file cannot be read, so the load cannot be sure that no "
                      "rule pre-approves it: " + "; ".join(unreadable) + ". Nothing was loaded.")

    # the token: is everything still what the table showed?
    refresh_started = _began(run)            # an interrupted load whose refresh had begun: see _began
    stopped_at = _stopped_step(run, dead)
    if token != plan.get("token"):           # a stale or mistyped line: nothing is read, nothing is closed
        raise CheckAgain(["the GO line (its token is not the one of this run's table)"], written=refresh_started)
    try:
        if deps.prepare is not None:
            deps.prepare()
        moved = token_moved(plan, deps, with_fingerprint=not refresh_started)
    except (Exception, SystemExit) as exc:  # noqa: BLE001
        raise Refusal(f"The check could not be repeated ({type(exc).__name__}: {_short(str(exc), 200)}). "
                      "Nothing was started: try again in a minute.") from None
    if moved:
        if resume:
            # closed for good: these files can never be resumed. When the refresh had begun the row
            # says so and is no 'check_again' result (that one reads "stopped before anything was loaded")
            journal(run, "failed", result="failed" if refresh_started else "check_again", check_again=True,
                    step=stopped_at, written=refresh_started, moved=moved,
                    text="This interrupted load cannot be resumed: something changed since its table was shown."
                         + (" " + MAY_HOLD if refresh_started else ""),
                    why=_sentence("this changed: " + "; ".join(moved)), next="Type /yq-load for a new check.")
            if dead is not None:
                _clear_dead_lock(dead)
            say("The interrupted load is closed: it cannot be resumed.")
        raise CheckAgain(moved, written=refresh_started)

    # the options and the loader's marker files must tell one story: the box shows the options
    odd = option_mismatch(plan, Path(deps.staging))
    if odd:
        raise Refusal(f"Refused: the table of run {run} and its staged folder do not agree on what a Yes would do ("
                      + "; ".join(odd) + "). Nothing was started: type /yq-load.")

    # the owner's confirmation
    title, text = box_text(run, plan, resume_step=stopped_at if resume else None, rehearsal=rehearsal)
    answer = "yes" if no_box else str(deps.box(title, text, BOX_TIMEOUT_S))
    if answer != "yes":
        if answer == "no":
            raise NoAnswer("You answered No: nothing loaded.")
        if answer == "unavailable":
            raise NoAnswer("The Yes/No box could not be shown on this screen: no answer, nothing loaded.")
        raise NoAnswer("no answer, nothing loaded")

    # take the lock, start the worker, hand the lock over. The one-time word goes to the worker in
    # its environment; the lock and the journal keep its hash only. The lock of a resume whose
    # refresh had begun names that step from its first moment: if the worker never gets going, the
    # lock left behind still says where the load stands (every skill prints it)
    nonce = secrets.token_hex(16)
    word_sha = word_hash(nonce)
    first_step = stopped_at if (resume and refresh_started) else "start"
    lockfile = LockFile(run, word_sha, {"resume": resume, "rehearsal": rehearsal,
                                        "step": first_step + (" (rehearsal)" if rehearsal else "")})
    try:
        lockfile.create(replacing=dead)
    except LockTaken:
        raise Refusal("Another load took the lock while the box was open. Nothing was started here: follow "
                      f"that one with {FOLLOW_LINE}") from None
    log_path = loadplan.run_dir(run) / "worker.log"
    try:
        # the box may have stood open for minutes: what was true before it is asked once more
        if loadplan.run_result(run) in ("FINISHED", "FAILED"):
            raise Refusal(f"Run {run} was closed while the box was open. Nothing was started: type /yq-load.")
        newer = _replaced_by(run)
        if newer:
            raise Refusal(_replaced_words(newer, late=True))
        proc = deps.spawn(run, Path(deps.root), nonce, log_path)
    except Refusal:
        lockfile.release()
        raise
    except Exception as exc:  # noqa: BLE001
        lockfile.release()
        raise Refusal(f"The worker could not be started ({type(exc).__name__}: {_short(str(exc), 160)}). "
                      "Nothing was loaded.") from None
    try:
        return _confirm_start(run, proc, word_sha[:8], lockfile, deps, say, log_path)
    except Exception:  # noqa: BLE001  (the worker IS running: never say otherwise because a file could not be read)
        say(f"load started (its first lines could not be read here): follow it with {FOLLOW_LINE}")
        return EXIT_OK


def stay_open_words(rehearsal: bool) -> str:
    """Said when Windows kept the worker inside the editor's job: it ends with the editor's window."""
    if rehearsal:
        return "Keep the Claude Code window open until REHEARSAL FINISHED: this rehearsal stops if it is closed."
    return "Keep the Claude Code window open until LOAD FINISHED: this load stops if it is closed."


def _confirm_start(run: str, proc, tag: str, lockfile: LockFile, deps: Deps, say, log_path: Path) -> int:
    """The hand-over. The lock still carries this launcher's process; the worker writes its own into
    it as its first act and then journals 'started'. The launcher waits a few seconds to see that
    line, and writes the lock no more: a late write here could bring back a lock the worker has
    already released. A worker that dies as it starts is said now, not found later as an
    interrupted load. `tag` marks the journal rows of this launch (the first letters of the word's
    hash)."""
    deadline = time.monotonic() + float(deps.start_wait_s)
    while True:
        code = proc.poll()                   # before the journal is read: a worker seen gone has written all it will
        rows = loadplan.journal_read(run)
        mine = [r for r in rows if r.get("launch") == tag]
        ended = [r for r in mine if r.get("event") in ("finished", "failed")]
        if ended:
            last = ended[-1]
            say(" ".join(str(last[k]) for k in ("text", "why", "next") if last.get(k)) or str(last.get("event")))
            if last.get("event") == "finished":
                return EXIT_OK
            again = last.get("result") == "check_again" or last.get("check_again") is True
            return EXIT_CHECK_AGAIN if again else EXIT_ERROR
        if code is not None:
            began = _began(run, rows)
            if not mine and not began:
                lockfile.release()
                say(f"The worker stopped as it started (exit code {code}): nothing was loaded. Its log: {log_path}")
                return EXIT_ERROR
            # the load is INTERRUPTED and the lock stays, so every skill says so until it is resumed.
            # Once a refresh had begun, "nothing was loaded" is never said, whatever this worker did
            at = next((str(r["step"]) for r in reversed(rows) if r.get("step")), "start")
            if not mine:
                say(f"LOAD INTERRUPTED at {at}: the worker stopped as it started (exit code {code}). {MAY_HOLD} "
                    f"Its log: {log_path}")
            else:
                say(f"The worker stopped unexpectedly (exit code {code}): LOAD INTERRUPTED."
                    + (f" {MAY_HOLD}" if began else "") + f" Its log: {log_path}")
            say(f"Its resume: {_resume_line(run)}")
            return EXIT_ERROR
        if mine or time.monotonic() >= deadline:
            break
        time.sleep(0.2)
    inside_job = getattr(proc, "breakaway", None) is False or any(
        r.get("event") == "started" and r.get("breakaway") is False for r in mine)
    if inside_job:
        say(stay_open_words(bool(deps.rehearsal)))
    say(f"load started: follow it with {FOLLOW_LINE}")
    return EXIT_OK


# ── the worker ────────────────────────────────────────────────────────────────────────────────

def _last_ok(rows: list[dict], step: str) -> dict | None:
    for row in reversed(rows):
        if row.get("event") == "step_ok" and row.get("step") == step:
            return row
    return None


class Job:
    """One run of the worker: the steps of loadplan.STEPS, each journalled."""

    def __init__(self, run: str, plan: dict, deps: Deps, lock: LockFile, launch_tag: str,
                 breakaway: bool | None = None):
        self.run, self.plan, self.deps, self.lock, self.launch_tag = run, plan, deps, lock, launch_tag
        self.breakaway = breakaway
        self.ddmmyy = run[:6]
        rows = loadplan.journal_read(run)
        self.resume = any(r.get("event") == "started" for r in rows)
        # a refresh that started has moved the database fingerprint itself: from then on only the
        # files and the commits are compared, and neither the backup nor the before-snapshot is retaken
        self.resumed_after_write = _began(run, rows)
        self.reuse = {step: _last_ok(rows, step) for step in ("backup", "pre_gate")}
        self.stopped_at = loadplan.last_step(run)
        self.written = self.resumed_after_write          # may the database hold (part of) this load?
        self.verified = False                            # did this worker's refresh load AND verify?
        self.step = "start"
        self.t0 = deps.now()
        self.needs_look: list[str] = []
        self.pre_gate: Path | None = None
        self.result: dict = {}
        self.published: str | None = None

    # plumbing
    def j(self, event: str, **fields) -> bool:
        text = fields.get("text")
        if text and self.deps.rehearsal and "REHEARSAL" not in text:
            text = fields["text"] = "(rehearsal) " + text      # no line of a rehearsal reads like a real load
        written = journal(self.run, event, **fields)
        self.deps.log(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}Z] {event}"
                      + (f" {fields['step']}" if fields.get("step") else "") + (f": {text}" if text else ""))
        return written

    def seconds(self) -> float:
        return round(max(0.0, (self.deps.now() - self.t0).total_seconds()), 1)

    def _lock_step(self, step: str) -> str:
        return step + (" (rehearsal)" if self.deps.rehearsal else "")

    def look(self, step: str, text: str, lines: list[str] | None = None, **fields) -> None:
        """A step that did not pass but does not stop the load: journalled, carried to the end. Its
        lines (the gate's FAIL lines, the reconcile's mismatches) follow as one note each, each
        saying whose words it quotes: a note is read with nothing beside it."""
        self.needs_look.append(step)
        fields.setdefault("next", "The load goes on.")
        self.j("step_failed", step=step, seconds=self.seconds(), stops=False, text=_sentence(text),
               lines=len(lines) if lines else None, **fields)
        says = LOOK_SAYS.get(step, "It says")
        for line in (lines or [])[:12]:
            self.j("note", step=step, text=_short(f"{says}: {line}", FIELD))

    def _read(self, fn, what: str, tries: int = 3):
        last: BaseException | None = None
        for attempt in range(tries):
            try:
                return fn()
            except (Exception, SystemExit) as exc:  # noqa: BLE001
                last = exc
                if attempt + 1 < tries:
                    self.deps.sleep(10)
        raise Stop(self.step, f"{what} ({type(last).__name__}: {_short(str(last), 200)})")

    def _check_token(self) -> None:
        """Is everything still what the table showed? Asked at the first step and again right before
        the write. The plan file is read once more as well: a check that a newer check replaced
        meanwhile is not loaded (the lock keeps a new check from starting, not from finishing)."""
        newer = _replaced_by(self.run)
        if newer:
            named = f" (run {newer})" if loadplan.is_run(newer) else ""
            raise CheckAgain([f"the load check itself: a newer check replaced it{named}"])
        moved = self._read(lambda: token_moved(self.plan, self.deps, not self.resumed_after_write),
                           "the files, the live site or the database could not be read for the token check")
        if moved:
            raise CheckAgain(moved)

    def _snapshot(self, label: str, gate_label: str) -> tuple[Path | None, Child]:
        gates = Path(self.deps.gates_dir)
        before = set(gates.glob(f"*_{gate_label}.json")) if gates.is_dir() else set()
        child = self.deps.runner(label, [sys.executable, "-X", "utf8", "-m", "scripts.prod_gate", "snapshot",
                                         "--label", gate_label], GATE_TIMEOUT_S)
        named = _SNAPSHOT_LINE.search(child.text)
        path = gates / named.group(1) if named and Path(named.group(1)).name == named.group(1) else None
        if path is None or not path.is_file():
            fresh = sorted(set(gates.glob(f"*_{gate_label}.json")) - before) if gates.is_dir() else []
            path = fresh[-1] if fresh else None
        if path is not None:
            try:
                good = isinstance(json.loads(path.read_text(encoding="utf-8")).get("tables"), dict)
            except (OSError, ValueError, AttributeError):
                good = False
            if not good:
                path = None
        return path, child

    # the run
    def run_all(self) -> int:
        d = self.deps
        awake = d.keep_awake(True)
        beat = Heartbeat(self.lock, d.heartbeat_s)
        beat.start()
        try:
            opening = ("REHEARSAL started: nothing is loaded." if d.rehearsal else "Load started.")
            if self.resume:
                opening = (("REHEARSAL resumed" if d.rehearsal else "Load resumed")
                           + f": it had stopped at {self.stopped_at or 'the start'}; the whole load runs again.")
            # breakaway False = Windows kept this worker inside the editor's job: the owner is told, in
            # the row every status call shows first, that the window has to stay open
            self.j("started", launch=self.launch_tag, pid=os.getpid(), resume=self.resume or None,
                   rehearsal=d.rehearsal or None, awake=bool(awake), breakaway=self.breakaway,
                   options=option_flags(self.plan) or None, text=opening,
                   next=stay_open_words(d.rehearsal) if self.breakaway is False else None)
            if d.prepare is not None:
                d.prepare()
            for step in loadplan.STEPS:
                self.step, self.t0 = step, d.now()
                self.lock.update(step=self._lock_step(step))
                opens = (REHEARSAL_OPENS.get(step) if d.rehearsal else None) or STEP_OPENS[step]
                if not self.j("step_start", step=step, text=opens) and step == "refresh":
                    # a resume tells "the load had begun" from this very line: without it, no load
                    raise Stop("refresh", "the journal could not be written, so the load was not started")
                try:
                    out = getattr(self, "step_" + step)()
                except (CheckAgain, Stop):
                    raise
                except Exception as exc:  # noqa: BLE001
                    if not self.result:
                        raise
                    # the data is loaded and verified: what follows is reported, never turned into a failure
                    d.log(traceback.format_exc())
                    name = {"post_gate": "gate"}.get(step, step)
                    out = self.look(step, f"{name} needs a look: an unexpected error ({type(exc).__name__}: "
                                          f"{_short(str(exc), 100)})")
                if out is not None:
                    self.j("step_ok", step=step, seconds=self.seconds(), text=out[0], **out[1])
            return self._finish()
        except CheckAgain as exc:
            return self._close("check_again", self.step, "this changed since the table was shown: "
                               + "; ".join(exc.moved), {"moved": exc.moved})
        except Stop as exc:
            return self._close("failed", exc.step or self.step, str(exc), exc.fields)
        except BaseException as exc:  # noqa: BLE001  (the journal must say why, whatever it was)
            d.log(traceback.format_exc())
            return self._close("failed", self.step, f"an unexpected error at {self.step} ({type(exc).__name__}: "
                               f"{_short(str(exc), 100)})", {})
        finally:
            beat.stop()
            d.keep_awake(False)

    def _close(self, result: str, step: str, why: str, fields: dict) -> int:
        """The run ends here without finishing. The last line says what state the database is in
        (text), why, and what to do next: three sentences that are read with nothing beside them.
        fields['notes'] (the totals of a failed verify) follow the step's row as one note each.
        fields['state'] says what a refresh left when it loaded the data and did not pass: 'differs'
        (verified, and the totals differ) or 'unchecked' (the verification could not run).
        The row carries step and written: scripts.ops.status.failed_load() reads them, and while
        written is true every skill opens with LAST LOAD FAILED until a load has finished."""
        fields = dict(fields)
        notes = [str(n) for n in fields.pop("notes", None) or []]
        state = fields.pop("state", None)
        again = result == "check_again"
        why = _sentence(why)
        self.j("step_failed", step=step, seconds=self.seconds(), stops=True, text=why, lines=len(notes) or None,
               **fields)
        for line in notes[:12]:
            self.j("note", step=step, text=_short(line, FIELD))
        pre = "REHEARSAL: " if self.deps.rehearsal else ""
        nxt = "This run is closed: type /yq-load for a new check."
        if again and self.written:
            # a resume of a load that had begun: the database may hold part of it, so this is not the
            # "stopped before anything was loaded" ending that result check_again stands for
            text = f"{pre}CHECK AGAIN at {step}. {MAY_HOLD}"
            result, nxt = "failed", "Type /yq-load for a new check."
        elif again:
            text = f"{pre}CHECK AGAIN at {step}. Nothing was written."
            nxt = "Type /yq-load to check again."
        elif not self.written:
            text = f"{pre}LOAD STOPPED at {step}. Nothing was written to the database."
            nxt = "Type /yq-load to check again."
        elif self.verified:
            text = (f"{pre}LOAD FAILED at {step}. The data is in the database and its totals match the reports, but "
                    "the run is closed unpublished.")
        elif state == "differs":
            text = f"{pre}LOAD FAILED at {step}. The data is in the database, but its totals do not match the reports."
            nxt = "This run is closed. Ask Claude to look into these totals before anything is loaded again."
        elif state == "unchecked":
            text = (f"{pre}LOAD FAILED at {step}. The data is in the database, but its totals could not be checked "
                    "against the reports.")
        else:
            text = f"{pre}LOAD FAILED at {step}. The database holds this load, or part of it."
        self.j("failed", launch=self.launch_tag, result=result, check_again=again or None, step=step,
               written=bool(self.written), text=text, why=why, next=nxt, metrics=fields.get("metrics"),
               moved=fields.get("moved"))
        self.lock.release()
        return EXIT_CHECK_AGAIN if again else EXIT_ERROR

    def _finish(self) -> int:
        look, data_date, published = self.needs_look, self.result.get("data_as_of"), self.published
        nxt = None
        if self.deps.rehearsal:
            text, published = "REHEARSAL FINISHED: nothing was loaded; every step ran in its sandbox.", None
        else:
            text = "LOAD FINISHED." + (f" Sales data to {data_date}." if data_date else "")
            text += f" Published as '{published}'." if published else ""
        if look:
            text += " Needs a look: " + ", ".join(look) + "."
            nxt = ("Nothing was loaded" if self.deps.rehearsal else "The data is loaded and verified") \
                + ": ask Claude to look into the 'needs a look' lines above."
        self.j("finished", launch=self.launch_tag, result="ok" if not look else "ok_needs_look",
               needs_look=look or None, data_date=data_date, published=published, text=text, next=nxt)
        self.lock.release()
        return EXIT_OK

    # the steps
    def step_token(self):
        self._check_token()
        left = empty_folder(self.deps.clean_dir, self.deps.sleep)
        if left:
            raise Stop("token", f"{len(left)} file(s) could not be removed from data/clean (the first is "
                                f"{left[0]}): close the program that holds them")
        emptied = "The loader's work folder (data/clean) is empty."
        if self.resumed_after_write:
            return ("Files and programs are as the table showed (a resume does not compare the database). "
                    + emptied), {}
        return "Files, programs and database are as the table showed. " + emptied, {}

    def step_backup(self):
        """A resume reuses the journalled backup. Once a refresh had begun it is never retaken (it
        would hold a half-loaded state); before that, an archive that is gone is simply taken again."""
        d, prior = self.deps, self.reuse["backup"] or {}
        kept = str(prior.get("archive") or "")
        on_disk = bool(kept) and Path(kept).is_file()
        if kept and (on_disk or self.resumed_after_write):
            name = Path(kept).name
            if not on_disk:
                self.j("note", step="backup",
                       text=_short(f"The pre-load backup {name} is no longer where it was written.", FIELD))
            return (f"The backup taken before the interruption stays the baseline: {name}.",
                    {"reused": True, "archive": kept, "encrypted": prior.get("encrypted")})
        if self.resumed_after_write:
            return ("No backup is journalled for this run and none is taken now: the load had already started, "
                    "and a backup now would hold a half-loaded state."), {"reused": True, "archive": None}
        if kept:
            self.j("note", step="backup", text=_short(f"The backup {Path(kept).name} is gone from where it was "
                                                      "written: nothing was loaded yet, so a new one is taken.", FIELD))
        try:
            base = Path(d.backup_root()) / "preload"
            keep = max(1, int(d.backup_keep()))
        except (Exception, SystemExit) as exc:  # noqa: BLE001
            raise Stop("backup", "the backup folder or its setting could not be read ("
                                 f"{_short(str(exc), 200)})") from None
        day = d.now().astimezone(BAHRAIN).date().isoformat()
        folder = base / f"{day}_pre-data-{self.ddmmyy}"
        _remove_tree(folder)                 # the left-over of an attempt that never finished
        base.mkdir(parents=True, exist_ok=True)
        child = d.runner("backup", [sys.executable, "-X", "utf8", "-m", "scripts.db_backup", "--all", "--out",
                                    str(folder)], BACKUP_TIMEOUT_S)
        done = [ln for ln in child.text.splitlines() if ln.startswith("Backup complete:")]
        if child.code != 0 or not any("verify OK" in ln for ln in done):
            _remove_tree(folder)
            raise Stop("backup", "the backup did not complete" + _why(child))
        try:
            archive, encrypted = d.archive(folder, base, f"{day}_pre-data-{self.run}")
        except (Exception, SystemExit) as exc:  # noqa: BLE001
            _remove_tree(folder)
            raise Stop("backup", f"the backup could not be packed ({_short(str(exc), 200)})") from None
        plain_gone = _remove_tree(folder)
        pruned = prune_archives(base, keep)
        text = ("Backup of every table packed, "
                + ("encrypted" if encrypted else "UNENCRYPTED (no backup password set)")
                + f": {Path(archive).name}. Kept: the newest {keep}.")
        if not plain_gone:
            self.j("note", step="backup", text=_short(f"The plain backup folder could not be removed: {folder}", FIELD))
        return text, {"archive": str(archive), "encrypted": bool(encrypted), "kept": keep, "pruned": len(pruned)}

    def step_pre_gate(self):
        """The same rule as the backup: reused on a resume; retaken only while nothing was written."""
        prior = self.reuse["pre_gate"] or {}
        kept = str(prior.get("path") or "")
        on_disk = bool(kept) and Path(kept).is_file()
        if kept and (on_disk or self.resumed_after_write):
            self.pre_gate = Path(kept)
            if not on_disk:
                self.j("note", step="pre_gate", text=_short(
                    f"The before-snapshot {self.pre_gate.name} is no longer where it was written: the gate will have "
                    "nothing to compare with.", FIELD))
            return (f"The before-snapshot taken before the interruption stays the baseline: {self.pre_gate.name}.",
                    {"reused": True, "path": kept})
        if self.resumed_after_write:
            return ("No before-snapshot is journalled for this run and none is taken now: the load had already "
                    "started."), {"reused": True, "path": None}
        if kept:
            self.j("note", step="pre_gate", text=_short(f"The before-snapshot {Path(kept).name} is gone from where it "
                                                        "was written: nothing was loaded yet, so a new one is taken.",
                                                        FIELD))
        path, child = self._snapshot("pre_gate", f"pre-data-{self.ddmmyy}")
        if path is None:
            raise Stop("pre_gate", "the before-snapshot was not written" + _why(child))
        self.pre_gate = path
        text, fields = f"Before-snapshot written: {path.name}.", {"path": str(path)}
        if child.code != 0:
            text += f" Its program ended with code {child.code} (foreign-key orphans are reported that way): to check."
            fields["exit_code"] = child.code
        return text, fields

    def step_wait_tick(self):
        d = self.deps
        self._check_token()
        first = tick_wait_seconds(d.now())
        if not d.wait_for_tick:
            return (f"A real load would wait {round(first)} s here for a quiet moment between two production "
                    "ticks; a rehearsal does not."), {"waited_seconds": 0, "would_wait_seconds": round(first)}
        if first > 0:
            self.j("note", step="wait_tick", text=f"Waiting about {max(1, round(first / 60))} min: a load starts "
                   "1 to 5 minutes after production's quarter-hour tick and never from 07:50 to 08:20 Bahrain.")
        # The step ends only on a token check that is followed AT ONCE by a quiet moment. The check
        # itself can be slow (the live site or the database answering late): when it has eaten the
        # quiet moment, the next one is waited for and the token is checked again.
        waited, fresh, late = 0.0, True, 0   # fresh: the token was checked and no time has passed since
        while True:
            wait = tick_wait_seconds(d.now())
            if wait > 0:
                fresh = False
                nap = min(wait, 30.0)
                d.sleep(nap)
                waited += nap
                continue
            if fresh:
                break                        # the table holds and the next tick is at least 10 minutes away
            if late >= TICK_TRIES:
                raise Stop("wait_tick", f"the check before the load outlasted the quiet moment {late} times in a row "
                                        "(the live site or the database answers slowly): the load was not started")
            self._check_token()              # the table must still hold right before the write
            fresh, late = True, late + 1
        if waited:
            return (f"Waited {round(waited / 60, 1)} min for a quiet moment between two production ticks; the "
                    "table still holds."), {"waited_seconds": round(waited), "checks": late + 1}
        return ("No wait needed: production's next tick is at least 10 minutes away. The table still holds.",
                {"waited_seconds": 0, "checks": 1})

    def step_refresh(self):
        d = self.deps
        argv = [sys.executable, "-X", "utf8", "-c", REFRESH_PROGRAM, str(d.staging)]
        unchecked = 0                        # runs that loaded the data and could not compare it with the reports
        for attempt in (1, 2):
            before = self._read(lambda: d.ingest_runs(None), "the run records could not be read before the load")[0]
            if attempt > 1:
                self.j("step_start", step="refresh", attempt=attempt, text="The identical load runs once more.")
            self.written = True
            child = d.runner("refresh", argv, None)          # never cut short: a half load is the worst outcome
            res = parse_refresh(child.text)
            loaded = (res or {}).get("loaded") or {}
            not_run = str((res or {}).get("verify_error") or "")       # the verification could not RUN
            if res is not None and res.get("verify_ok") is False and not not_run:
                # The verification ran and the totals differ: the data IS loaded, and a second run
                # would not change that. The totals are named first (the line may be cut at 160
                # characters), and each of them once more as a note of its own
                said = str(res.get("error") or "The data loaded, but its totals do not match the reports.")
                names = [n.strip() for n in said.split("reports:", 1)[1].split(";")] if "reports:" in said else []
                names = [n for n in names if n]
                why = ("totals differ from the reports: " + "; ".join(names)) if names else (
                    "verification did not pass. " + said)
                notes = ["Differs from the reports: " + n for n in names[:11]]
                if len(names) > 11:
                    notes.append(f"(+{len(names) - 11} more totals differ: the worker log has them all)")
                raise Stop("refresh", why, attempt=attempt, loaded=loaded, data_as_of=res.get("data_as_of"),
                           metrics=_short("; ".join(names), FIELD) if names else None, notes=notes, state="differs")
            if res is None or not res.get("ok") or res.get("verify_ok") is not True:
                # The load stage failed, or the data loaded and nothing could be compared (the
                # verification could not run: a database that did not answer, say). Neither is a
                # failed verify, and either gets the one identical re-run
                if not_run:
                    unchecked += 1
                    why = f"the verification could not run ({not_run})"
                    first = "The data loaded, but its totals could not be checked: the identical load runs once more."
                else:
                    why = str((res or {}).get("error") or "") or ("the load program gave no result" + _why(child))
                    first = "The load stage failed: the identical load runs once more."
                if attempt == 1:
                    self.j("step_failed", step="refresh", attempt=1, stops=False, seconds=self.seconds(), loaded=loaded,
                           text=first, why=_sentence(why))
                    d.sleep(RETRY_PAUSE_S)
                    continue
                if not_run:
                    both = " in either run" if unchecked == 2 else ""
                    raise Stop("refresh", f"the data loaded, but its totals could not be checked{both}: {why}",
                               attempt=2, loaded=loaded, data_as_of=res.get("data_as_of"), state="unchecked")
                twice = "the load stage failed twice: " if unchecked == 0 else "the second run of the load failed: "
                raise Stop("refresh", twice + why, attempt=2, loaded=loaded)
            self.verified = True
            after, new_rows = self._read(lambda: d.ingest_runs(before),
                                         "the data is loaded and verified, but the run records could not be read")
            problems: list[str] = []
            if child.code != 0:
                problems.append(f"the load program ended with code {child.code}")
            if res.get("loader_notices"):
                problems.append("the loader reported: " + _short("; ".join(str(n) for n in res["loader_notices"]), 400))
            if new_rows > NEW_RUNS_PER_LOAD:
                problems.append(f"{new_rows} load records were written during it instead of {NEW_RUNS_PER_LOAD}: "
                                "another load may have run at the same time")
            elif new_rows < NEW_RUNS_PER_LOAD:
                problems.append(f"only {new_rows} of its {NEW_RUNS_PER_LOAD} load records were written")
            if problems:
                raise Stop("refresh", "the data is loaded and verified, but " + "; ".join(problems), attempt=attempt,
                           loaded=loaded, ingest_runs=[before, after], new_runs=new_rows,
                           data_as_of=res.get("data_as_of"))
            self.result = res
            text = "Loaded and verified." + (f" Sales data to {res['data_as_of']}." if res.get("data_as_of") else "")
            if attempt > 1:
                text += " (It passed on the second run.)"
            return text, {"attempt": attempt, "data_as_of": res.get("data_as_of"), "loaded": loaded,
                          "ingest_runs": [before, after], "new_runs": new_rows}
        raise Stop("refresh", "the load did not run")      # not reachable: the loop returns or raises

    def step_reconcile(self):
        child = self.deps.runner("reconcile", [sys.executable, "-X", "utf8", "-m", "scripts.reconcile_check",
                                               str(self.deps.staging)], RECONCILE_TIMEOUT_S)
        lines = child.text.splitlines()
        if child.code == 0 and any(ln.startswith("RECONCILED") for ln in lines):
            return "Reconciled: the portal's numbers match the Focus reports.", {}
        return self.look("reconcile", "reconcile needs a look" + _why(child),
                         _pick(lines, ("FAIL", "WHY:", "MISMATCH", "ERROR")))

    def step_post_gate(self):
        post, child = self._snapshot("post_gate", f"post-data-{self.ddmmyy}")
        if post is None:
            return self.look("post_gate", "gate needs a look: the after-snapshot was not written" + _why(child))
        if self.pre_gate is None or not self.pre_gate.is_file():
            return self.look("post_gate", "gate needs a look: there is no before-snapshot to compare with",
                             post=str(post))
        child = self.deps.runner("compare", [sys.executable, "-X", "utf8", "-m", "scripts.prod_gate", "compare",
                                             str(self.pre_gate), str(post)], GATE_TIMEOUT_S)
        lines = child.text.splitlines()
        if child.code == 0 and any(ln.startswith("PASS: 0 failure(s)") for ln in lines):
            deltas = sum(1 for ln in lines if ln.strip().startswith("delta:"))
            counted = "1 explained change" if deltas == 1 else f"{deltas} explained changes"
            return (f"Gate passed: no protected record changed ({counted}).",
                    {"pre": str(self.pre_gate), "post": str(post), "deltas": deltas})
        return self.look("post_gate", "gate needs a look" + ("" if child.code in (0, 1) else _why(child)),
                         [ln.strip() for ln in lines if ln.strip().startswith("FAIL")],
                         pre=str(self.pre_gate), post=str(post))

    def step_publish(self):
        d, out = self.deps, None
        as_on = as_on_date(self.plan, self.run)
        try:
            if as_on is None:
                raise OSError("the plan carries no as-on date")
            target = Path(d.published_dir(as_on))
            staged = Path(d.staging)
            aside = publish_folder(staged, target, Path(d.superseded_dir), self.run, d.now())
            self.published = target.name
            kept_back = sorted(p.name for p in staged.iterdir() if p.is_file() and one_time_marker(p.name))
            text = f"Published as '{target.name}'."
            if aside is not None:
                text += " The earlier folder of that date was moved to staging/superseded."
            if kept_back:                    # he gave it for this load: it is no property of the files
                text += " The one-time permission to replace is not kept with it."
            out = (_short(text, FIELD), {"published": target.name, "not_copied": kept_back or None,
                                         "superseded": str(aside) if aside is not None else None})
        except Exception as exc:  # noqa: BLE001  (the data is loaded: a folder that cannot be copied is reported)
            self.look("publish", "publish needs a look: the loaded set could not be copied to business_data.",
                      why=_sentence(f"{type(exc).__name__}: {exc}"), staged_in=str(d.staging),
                      next="The data is loaded and verified; only the copy of its files is missing.")
        look = self.needs_look
        last = {"run": self.run, "finished_at": d.now().astimezone(BAHRAIN).isoformat(timespec="seconds"),
                "data_date": self.result.get("data_as_of"), "published": self.published,
                "result": "ok" if not look else "ok, needs a look: " + ", ".join(look)}
        # since which report day the profitability report is held back (the brief says "held back
        # since" from it): only a load knows, so it is written down here and carried from load to load
        try:
            before = d.read_state().get("last_load")
        except (OSError, ValueError, AttributeError):
            before = None
        held = profit_held_since(self.plan, self.run, before if isinstance(before, dict) else None)
        if held:
            last["profit_held_since"] = held
        for attempt in range(8):
            try:
                d.write_state({"last_load": last})
                break
            except OSError:
                time.sleep(0.5)
        else:
            self.j("note", step="publish", text="state.json could not be written: the status line still shows the "
                                                "load before this one.")
        return out


def worker(run: str, *, deps: Deps | None = None, nonce: str | None = None, parent_pid: int | None = None) -> int:
    """The detached worker. It runs only when the launcher started it after a Yes. Three things must
    hold: the lock names this run; the lock carries the hash of the one-time word this process was
    handed in its environment (the word is in no file, so it cannot be read back from the lock); and
    the lock is still held by the process that started this worker, the launcher. A worker typed by
    hand, started from the lock of an interrupted load or beside a running one loads nothing.
    (nonce and parent_pid are given by tests only: the command line passes neither.)"""
    word = nonce if nonce is not None else os.environ.pop(NONCE_ENV, "")
    outside_job = {"1": True, "0": False}.get(os.environ.pop(BREAKAWAY_ENV, ""))
    parent = os.getppid() if parent_pid is None else int(parent_pid)
    try:
        lock = _read_lock() if loadplan.is_run(run) else None
    except OSError as exc:                   # a lock that cannot be read is nobody's proof of anything
        lock = None
        _to_log(f"(refused: the lock could not be read: {type(exc).__name__})")
    word_sha = word_hash(word) if word else ""
    ours = bool(lock and word_sha and lock.get("run") == run and lock.get("word_sha") == word_sha)
    if ours:
        try:
            holder = int(lock.get("pid") or 0)
        except (TypeError, ValueError):
            holder = 0
        if holder != parent:                 # until the hand-over below, the lock names the launcher
            ours = False
            _to_log(f"(refused: the lock is held by process {holder}; this worker was started by process {parent})")
    if not ours:
        print("This is the load's internal worker: it runs only when the launcher started it after your Yes. "
              "Nothing was loaded: type /yq-load.")
        return EXIT_REFUSED
    lockfile = LockFile(run, word_sha, lock)
    try:
        plan = loadplan.read_plan(run)
        if not isinstance(plan, dict):
            raise ValueError("the plan is not an object")
        if deps is None:
            deps = deps_for(run, plan)
    except (Refusal, OSError, ValueError, RuntimeError) as exc:
        why = _sentence(f"the worker could not start ({type(exc).__name__}: {_short(str(exc), 100)})")
        try:
            began = _began(run)
        except OSError:
            began = True                     # cannot tell: the careful answer keeps the lock
        if began:
            # The resume of a load that had begun could not even start. No closing row is written and
            # the lock stays (it still names the launcher, which ends in a moment): the load reads
            # INTERRUPTED in every skill and can be resumed. Nothing says that nothing was written.
            journal(run, "note", text="The resume could not start: the load stays INTERRUPTED. " + MAY_HOLD,
                    why=why, next="Type /yq-load to resume it.")
            _to_log(f"The resume of run {run} could not start ({why}). The lock is kept: LOAD INTERRUPTED.")
            return EXIT_ERROR
        journal(run, "failed", launch=word_sha[:8], result="failed", step="start", written=False,
                text="LOAD STOPPED before it began. Nothing was written to the database.",
                why=why, next="Type /yq-load to check again.")
        lockfile.release()
        return EXIT_ERROR
    lockfile.update(pid=os.getpid(), pid_created=process_created(os.getpid()))      # the hand-over
    return Job(run, plan, deps, lockfile, word_sha[:8], outside_job).run_all()


# ── the rehearsal ─────────────────────────────────────────────────────────────────────────────

def rehearse(seconds: int = REHEARSAL_SECONDS, *, no_box: bool = False, dev: bool = False,
             say: Callable[[str], None] = print, spawn=None, start_wait_s: float | None = None) -> int:
    """Build a sandbox run (000000_<HHMM>) and put it through the SAME launcher and worker."""
    if gitinfo.is_linked_worktree(ROOT) and not dev:         # said before anything is written (launch() checks again)
        say("This folder is a code worktree (.claude/worktrees), not the project folder: a rehearsal runs here "
            "only with --dev. Nothing was started.")
        return EXIT_REFUSED
    run = REHEARSAL_PREFIX + datetime.now(BAHRAIN).strftime("%H%M")
    box = loadplan.run_dir(run)
    try:
        lock = _read_lock()                  # from the file: see the launcher on why not status.lock_state()
    except OSError as exc:
        say(_lock_unreadable(exc, "No rehearsal was started"))
        return EXIT_REFUSED
    if lock is not None and not worker_alive(lock):
        held_by = str(lock.get("run") or "?")
        if held_by.startswith(REHEARSAL_PREFIX):
            _clear_dead_lock(lock)
            say(f"(the rehearsal {held_by} had stopped at {lock.get('step') or 'start'}: its worker did not "
                "survive. Its left-over lock is cleared.)")
            lock = None
        elif held_by == "?":                 # a launcher died while writing it: no worker was ever started
            _clear_dead_lock(lock)
            say("(an unreadable left-over lock is cleared: no load was running under it)")
            lock = None
    if lock is not None and worker_alive(lock):
        say(_held_words(lock, "No rehearsal was started"))
        return EXIT_REFUSED
    if lock is not None:                     # an interrupted REAL load: nothing of it is touched from here
        say(f"LOAD INTERRUPTED at {lock.get('step') or 'start'} (run {lock.get('run')}): that load is finished "
            "first. No rehearsal was started.")
        return EXIT_REFUSED
    older = sorted(paths.loads_dir().glob(REHEARSAL_PREFIX + "*")) if paths.loads_dir().is_dir() else []
    for old in older:                        # earlier rehearsals: sandboxes of ours, and nothing else
        if old.is_dir() and loadplan.is_run(old.name):
            _remove_tree(old)                # tried again: a scanner may hold a file of it for a moment
    paths.write_text(box / "drop" / "Rehearsal_report.txt", "a stand-in for a Focus report\n")
    paths.write_text(box / "staging" / "Rehearsal_report.txt", "a stand-in for a Focus report\n")
    paths.write_text(box / "clean" / "left_over.csv", "a left-over the token step must remove\n")
    parts = rehearsal_parts(box)
    plan = {"run": run, "rehearsal": True, "seconds": max(0, int(seconds)), "drop": str(box / "drop"),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "as_on": {"rehearsal": datetime.now(BAHRAIN).date().isoformat()},
            "verdicts": [{"file": "Rehearsal_report.txt", "kind": "rehearsal", "label": "Rehearsal report",
                          "verdict": "load", "decision": VERDICT_WORDS["load"], "reason": "a stand-in", "staged": "x"}],
            "expected": {"lines": ["Nothing: this is a rehearsal."]}, "options": {}, "stops": [],
            "token_parts": parts, "token": loadplan.token_of(parts)}
    loadplan.write_plan(plan)
    deps = rehearsal_deps(run, plan)
    if spawn is not None:
        deps.spawn = spawn
    if start_wait_s is not None:
        deps.start_wait_s = start_wait_s
    say(f"REHEARSAL {run}: about {plan['seconds']} s. The engine and the database are not touched; the lock, the "
        "heartbeat and the journal are real.")
    return launch(run, plan["token"], dev=dev, no_box=no_box, deps=deps, say=say)


# ── the command line ──────────────────────────────────────────────────────────────────────────

def _utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _utf8()
    ap = argparse.ArgumentParser(prog=f"python -m {MODULE}", allow_abbrev=False,
                                 description="The load's go. Started by /yq-load after its table; it asks with a "
                                             "Yes/No box before anything is loaded.")
    ap.add_argument("--run", help="the run the load check named (ddmmyy_HHMM)")
    ap.add_argument("--token", help="the token the load check printed")
    ap.add_argument("--worker", metavar="RUN", help="internal: the detached worker")
    ap.add_argument("--rehearse", action="store_true", help="a rehearsal: no engine, no database")
    ap.add_argument("--seconds", type=int, help="how long a rehearsal lasts")
    ap.add_argument("--no-box", action="store_true", help="rehearsal only: do not show the Yes/No box")
    ap.add_argument("--dev", action="store_true", help="rehearsal only: allow a code worktree")
    a = ap.parse_args(argv)
    chosen = [bool(a.worker), bool(a.rehearse), bool(a.run or a.token)]
    if sum(chosen) != 1:
        print("Give exactly one of: --run <run> --token <t>  |  --rehearse  |  (internal) --worker <run>. "
              "Nothing was started.")
        return EXIT_REFUSED
    if a.worker:
        if a.no_box or a.dev or a.seconds is not None:
            print("The worker takes no other option. Nothing was started.")
            return EXIT_REFUSED
        return worker(a.worker)              # every ending of a worker is journalled by the worker itself
    try:
        if a.rehearse:
            return rehearse(REHEARSAL_SECONDS if a.seconds is None else a.seconds, no_box=a.no_box, dev=a.dev)
        if not (a.run and a.token) or a.seconds is not None:
            print("The go needs --run <run> --token <t> and nothing else. Nothing was started.")
            return EXIT_REFUSED
        return launch(a.run, a.token, dev=a.dev, no_box=a.no_box)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {type(exc).__name__}: {str(exc)[:300]}. Nothing was started by this command.")
        traceback.print_exc(file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
