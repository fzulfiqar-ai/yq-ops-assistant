"""The load's writer (scripts/yq_load_go.py): the launcher, the worker and the rehearsal.

    python -m tests.test_ops_load_go

Same lightweight runner as the other suites (no pytest). The real launcher and worker code runs
here against stand-ins for everything outside the file: a fake step runner (no engine program is
ever started), a fake Yes/No box (no box is ever shown), a fake clock, a fake database fingerprint
(no psycopg, no network) and a temporary data root. SUPABASE_URL / SUPABASE_KEY / DATABASE_URL are
pinned to ci.invalid before any import. Every name and number is invented.

Covered:
  * the permission-rule matcher, and every refusal of the launcher (worktree, missing plan, a check
    that a newer one replaced, STOP, no token, finished run, failed run, a matching allow rule, the
    option misuse, a lock that cannot be read);
  * token moved -> exit 3 with the parts, no lock, runner never called; box No / no answer -> exit 4;
  * the worker: step order, backup first, nothing loaded when backup or the before-snapshot fails,
    the token re-checked right before the refresh, the tick rule, the one re-run of a failed load
    stage and the ingest_runs count, verify failed = FAILED for good, a verification that could not
    run = one identical re-run, a gate or reconcile that needs a look still publishes, publish with
    an older folder moved aside, state.json, unlock;
  * publish: the marker of a partial export travels with the set, the one-time permission to replace
    does not; state.json's profit_held_since (held back, not in the drop, loaded);
  * resume: backup and before-snapshot reused, no fingerprint compared, the whole refresh again;
    a backup or snapshot whose file is gone is retaken while nothing was written, never after;
  * a resume of a load that had BEGUN and ends early: in each of the four places it can end, nobody
    is told that nothing was written, and where the worker never got going the lock is kept;
  * a run closed as failed after its refresh had begun: step and written as
    scripts.ops.status.failed_load() reads them, and the LAST LOAD FAILED banner that follows;
  * the worker's word: its hash in the lock, never the word; a worker started from a lock read off
    the disk, beside a running load, or by another process than the launcher loads nothing; inside a
    Windows virtual environment the worker is still the launcher's own child;
  * the box: the check's ATTENTION lines under the file lines, the options that change what a Yes
    does; a marker file it would not show is refused; a line that sends him back to /yq-load names
    the folder (/yq-load <ddmmyy>), never "/yq-load again";
  * after the load: the retry of its e-mail is offered only where it would be accepted (a brief the
    deliver command refused, or a newer load on record, has none: that load has its own e-mail);
  * a worker kept inside the editor's job: the owner is told to keep the window open;
  * a live worker with a stale heartbeat: its own sentence with what he can do, no circle with
    `load status`; a rehearsal's lock: dead = cleared, alive = nothing starts beside it;
  * a token check that outlasts the quiet moment: the next one is waited for, the token checked again;
  * a failed verify: the totals that differ come first and each has its own journal line;
  * the journal: every row carries text, and the rows of a normal run, a failed verify, a check-again
    and an interrupted resume read well when only text, why and next are shown (the lines are here);
  * the lock: its shape as scripts.ops.status reads it, the heartbeat, a lock taken exclusively;
  * children: the environment this program was STARTED with (not the one scripts.ops.db has
    blanked), bytes decoded with replacement, the refresh child's own program;
  * the rehearsal: no real runner can be built, nothing is written outside its run folder, the
    database module is never even imported; on Windows one real detached rehearsal end to end;
  * the file itself (AST): no import of scripts.refresh, no os.kill, no forbidden option or module.
"""
from __future__ import annotations

import ast
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"
os.environ.pop("YQ_MAIN_CHECKOUT", None)

from scripts.ops import db  # noqa: E402,F401  (first, as every command does)
from scripts.ops import config, loadplan, paths, status  # noqa: E402
from scripts import yq_load_go as yq  # noqa: E402

TESTS: list[tuple[str, object]] = []
WRITER = ROOT / "scripts" / "yq_load_go.py"
RUN = "010126_0900"
GO = "python -m scripts.yq_load_go --run 010126_0900 --token x"
FOLLOW = "load started: follow it with python -m scripts.ops load status --wait"
NOON = datetime(2026, 1, 1, 9, 2, 0, tzinfo=timezone.utc)        # 2 minutes after a tick, midday in Bahrain
DEAD_PID = 2 ** 22 + 12345                                       # no such process
FIXTURE = {"api.health_url": "https://ci.invalid/health", "load.backup_keep": 2}


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


def raises(kind, fn) -> BaseException:
    try:
        fn()
    except kind as exc:
        return exc
    raise AssertionError(f"expected {kind.__name__}")


WORDS = ("text", "why", "next", "metrics")           # the fields of a journal row written for the owner
SAYS = ("text", "why", "next")                       # the three `load status` prints, and nothing else of a row


def whole(row: dict) -> str:
    """Everything a journal row says to the owner: what happened, why, what to do next."""
    return " | ".join(str(row[k]) for k in WORDS if row.get(k))


def shown(row: dict) -> str:
    """A journal row as the status screen shows it: its text, why and next, with nothing beside them."""
    return " ".join(str(row[k]) for k in SAYS if row.get(k))


class data_root:
    """A temporary data root for one test."""

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        paths.set_data_root(self._tmp.name)
        config.use(FIXTURE)
        return paths.data_root()

    def __exit__(self, *exc):
        paths.set_data_root(None)
        config.use(None)
        self._tmp.cleanup()
        return False


# ── the invented world ────────────────────────────────────────────────────────────────────────

class Clock:
    def __init__(self, start: datetime = NOON):
        self.t, self.sleeps, self.on_sleep = start, [], None

    def now(self) -> datetime:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += timedelta(seconds=seconds)
        if self.on_sleep:
            self.on_sleep()


class Box:
    def __init__(self, answer: str = "yes"):
        self.answer, self.shown, self.on_show = answer, [], None

    def __call__(self, title: str, text: str, timeout_s: float) -> str:
        self.shown.append((title, text, timeout_s))
        if self.on_show:
            self.on_show()
        return self.answer


class Proc:
    def __init__(self, pid: int, code=None, breakaway=None):
        self.pid, self._code = pid, code
        if breakaway is not None:
            self.breakaway = breakaway              # as spawn_worker marks the process it returns

    def poll(self):
        return self._code


class Spawn:
    """Starts no process. `inline` (run, nonce) -> exit code runs the worker right here, the way a
    worker that is already done by the time the launcher looks would appear. `breakaway` False =
    Windows kept the worker inside the editor's job."""

    def __init__(self, code=None, error: Exception | None = None, breakaway=None):
        self.calls, self._code, self._error, self.inline, self.breakaway = [], code, error, None, breakaway

    def __call__(self, run, root, nonce, log_path):
        self.calls.append({"run": run, "root": Path(root), "nonce": nonce, "log": Path(log_path)})
        if self._error:
            raise self._error
        if self.inline is not None:
            return Proc(os.getpid(), self.inline(run, nonce), self.breakaway)
        return Proc(os.getpid(), self._code, self.breakaway)

    @property
    def nonce(self) -> str:
        """The one-time word the launcher handed its worker (in memory and environment only)."""
        return self.calls[-1]["nonce"]

    @property
    def sha(self) -> str:
        """What the lock keeps of that word; its first eight letters tag the journal rows."""
        return yq.word_hash(self.nonce)


class World:
    """One test's outside world: a drop folder, a staged run, its plan, a database fingerprint."""

    def __init__(self, root: Path, run: str = RUN):
        self.root, self.run = root, run
        self.drop = root / "Uplaods" / run[:6]
        self.drop.mkdir(parents=True)
        (self.drop / "Sales_day_book1.xlsx").write_bytes(b"sales as exported")
        (self.drop / "Stock_balance1.xlsx").write_bytes(b"stock, every warehouse")
        self.staging = loadplan.staging_path(run)
        self.staging.mkdir(parents=True)
        (self.staging / "Sales_day_book1.xlsx").write_bytes(b"sales as exported")
        (self.staging / "Stock_balance1.xlsx").write_bytes(b"stock, main store block")
        self.gates = root / "business_data" / "gates"
        self.head, self.live = "a" * 40, "b" * 12
        self.fingerprint = {"orders": [10, 99], "order_lines": [50, 500], "ingest_runs": 7, "ingest_batches_open": []}
        self.newest_run = 7
        self.trace: list[str] = []          # everything that happened, in order
        self.log: list[str] = []
        parts = self.parts(True)
        self.plan = {                       # the shape scripts/ops/load_check.py writes
            "run": run, "drop": str(self.drop), "created_at": "2026-01-01T09:00:00+00:00",
            "as_on": {"order_lines": "2026-01-01", "stock_balance": "2026-01-01", "selling_prices:MA_base": "2026-01-09"},
            "as_on_all": {"order_lines": "2026-01-01", "stock_balance": "2026-01-01", "product_profitability": "2026-01-01",
                          "selling_prices:MA_base": "2026-01-09"},
            "data_date": "2026-01-01", "staging": str(self.staging), "stops": [], "flags": ["hold_back"],
            "not_loaded": ["photo1.jpg", "papers/"],
            "verdicts": [verdict("Sales_day_book1.xlsx", "order_lines", "Sales day book", "load", True),
                         verdict("Stock_balance1.xlsx", "stock_balance", "Stock balance", "trim", True),
                         verdict("Profit1.xlsx", "product_profitability", "Profitability report", "hold_back", False),
                         verdict("Price_book1.xlsx", "selling_prices:MA_base", "Price book", "needs_decision", False),
                         verdict(None, "receivables", "Receivables ageing", "not_loaded", False)],
            "expected": {"sales_to": "2025-12-31", "stock": {"items": 10},
                         "lines": ["Sales to Wed 31 Dec: 12 invoices in all.", "Stock (main store) as on Thu 01 Jan: 10 items."]},
            "options": {"dev": False, "pricebook": None},
            "token_parts": parts, "token": loadplan.token_of(parts)}
        loadplan.write_plan(self.plan)

    @property
    def token(self) -> str:
        return self.plan["token"]

    def parts(self, with_fingerprint: bool) -> dict:
        out = {"drop": loadplan.manifest(self.drop), "staging": loadplan.manifest(self.staging),
               "head": self.head, "live": self.live}
        if with_fingerprint:
            out["fingerprint"] = json.loads(json.dumps(self.fingerprint))
        return out

    def collect_parts(self, plan: dict, with_fingerprint: bool) -> dict:
        self.trace.append("token:" + ("with fingerprint" if with_fingerprint else "files and commits only"))
        return self.parts(with_fingerprint)

    def ingest_runs(self, after_id):
        self.trace.append("runs:" + str(after_id))
        if after_id is None:
            return self.newest_run, 0
        return self.newest_run, self.newest_run - after_id

    def archive(self, folder: Path, dest: Path, stamp: str):
        self.trace.append("archive")
        assert (Path(folder) / "manifest.json").is_file(), "packed before the backup was written"
        out = Path(dest) / f"yq-backup_{stamp}.7z"
        out.write_bytes(b"packed")
        return out, True

    def write_plan(self, **changes) -> None:
        self.plan.update(changes)
        loadplan.write_plan(self.plan)


def verdict(file, kind: str, label: str, code: str, staged: bool) -> dict:
    """One row of the load check's table, as it stores it in plan.json."""
    return {"file": file, "kind": kind, "label": label, "verdict": code, "decision": yq.VERDICT_WORDS[code],
            "reason": "the reason the check gave", "staged": file if staged else None}


def refresh_line(**changes) -> str:
    res = {"ok": True, "data_as_of": "2025-12-31", "error": None, "loaded": {"Sales_day_book": 40},
           "verify_ok": True, "verify_error": None, "loader_notices": []}
    res.update(changes)
    return "REFRESH_RESULT " + json.dumps(res)


class Runner:
    """The step runner of the tests: it starts nothing. Each step answers as a passing one would,
    unless a test put another answer in `script[label]` (a list, used in order)."""

    def __init__(self, world: World):
        self.w, self.calls, self.script, self.hooks, self._n = world, [], {}, {}, 0

    def labels(self) -> list[str]:
        return [c[0] for c in self.calls]

    def argv(self, label: str) -> list[str]:
        return [c[1] for c in self.calls if c[0] == label][-1]

    def __call__(self, label: str, argv: list, timeout=None) -> yq.Child:
        argv = [str(a) for a in argv]
        self.calls.append((label, argv, timeout))
        self.w.trace.append("run:" + label)
        if label in self.hooks:
            self.hooks[label]()
        queue = self.script.get(label) or []
        answer = queue.pop(0) if queue else getattr(self, "ok_" + label)
        return answer(argv) if callable(answer) else answer

    def ok_backup(self, argv) -> yq.Child:
        out = Path(argv[argv.index("--out") + 1])
        out.mkdir(parents=True, exist_ok=True)
        (out / "orders.csv").write_text("id\n1\n", encoding="utf-8")
        (out / "manifest.json").write_text("{}", encoding="utf-8")
        return yq.Child(0, f"  orders        1 rows -> orders.csv\n\nBackup complete: {out}  (1 tables, git abc1234, verify OK)\n")

    def _snapshot(self, argv) -> yq.Child:
        self._n += 1
        name = f"2026-01-01_09{self._n:02d}00_{argv[argv.index('--label') + 1]}.json"
        self.w.gates.mkdir(parents=True, exist_ok=True)
        (self.w.gates / name).write_text(json.dumps({"tables": {"orders": {"rows": 10}}}), encoding="utf-8")
        return yq.Child(0, f"snapshot {name}: 3 orders, 2 customers, FK orphans 0, digest abc\n")

    ok_pre_gate = _snapshot
    ok_post_gate = _snapshot

    def ok_refresh(self, argv) -> yq.Child:
        self.w.newest_run += 2
        return yq.Child(0, "\n=== Data refreshed ===\nData as of 2025-12-31.\nVerify: PASS\n" + refresh_line() + "\n")

    def ok_reconcile(self, argv) -> yq.Child:
        return yq.Child(0, "=" * 20 + "\nRECONCILED \u2014 platform numbers match the Focus reports.\n")

    def ok_compare(self, argv) -> yq.Child:
        return yq.Child(0, "compare a.json -> b.json\n  delta: orders: 10 -> 12 (+2)\n\nPASS: 0 failure(s), 1 explained delta(s)\n")


class Harness:
    """A world plus the stand-ins, and the two calls a load makes: the go and the worker."""

    def __init__(self, root: Path, *, answer: str = "yes", start: datetime = NOON, linked: bool = False,
                 settings: list | None = None, heartbeat_s: float = 3600, spawn: Spawn | None = None, run: str = RUN):
        self.w = World(root, run)
        self.runner, self.box, self.clock = Runner(self.w), Box(answer), Clock(start)
        self.spawn, self.out = spawn or Spawn(), []
        self.deps = yq.Deps(
            rehearsal=False, root=root, runner=self.runner, collect_parts=self.w.collect_parts,
            ingest_runs=self.w.ingest_runs, archive=self.w.archive, backup_root=lambda: root / "backups-out",
            backup_keep=lambda: int(config.get("load.backup_keep")), gates_dir=self.w.gates,
            staging=self.w.staging, clean_dir=root / "data" / "clean", published_dir=paths.published_dir,
            superseded_dir=paths.staging_dir() / "superseded", read_state=status.read_state,
            write_state=status.write_state, box=self.box,
            spawn=self.spawn, settings_files=settings or [], linked_worktree=lambda: linked, prepare=None,
            now=self.clock.now, sleep=self.clock.sleep, log=self.w.log.append, keep_awake=lambda on: True,
            wait_for_tick=True, start_wait_s=0, heartbeat_s=heartbeat_s)

    def go(self, token: str | None = None, **kw) -> int:
        return yq.launch(self.w.run, self.w.token if token is None else token, deps=self.deps, say=self.out.append, **kw)

    def work(self, **kw) -> int:
        """The worker, started by the launcher: in these tests the launcher is this very process."""
        kw.setdefault("parent_pid", os.getpid())
        kw.setdefault("nonce", self.spawn.nonce)
        kw.setdefault("deps", self.deps)
        return yq.worker(self.w.run, **kw)

    def inline(self) -> None:
        """From now on the spawn stand-in runs the worker to its end before the launcher looks."""
        self.spawn.inline = lambda run, nonce: yq.worker(run, deps=self.deps, nonce=nonce, parent_pid=os.getpid())

    def load(self) -> int:
        assert self.go() == 0, self.out
        return self.work()

    def launcher_gone(self) -> dict:
        """The launcher of the last go has ended, as it does a moment after it returns: the lock it
        left now names a process that is gone. -> the lock"""
        lock = dict(status.read_lock(), pid=DEAD_PID, pid_created=1)
        paths.write_text(paths.lock_file(), json.dumps(lock))
        return lock

    def rows(self, event: str | None = None, step: str | None = None) -> list[dict]:
        rows = loadplan.journal_read(self.w.run)
        for r in rows:                                  # no sentence of the journal is longer than a field
            for k in WORDS:
                assert len(str(r.get(k) or "")) <= yq.FIELD, (k, len(str(r[k])), r[k])
            assert None not in r.values(), r
            assert r.get("text"), f"a journal row with no sentence for the owner: {r}"
            for k in SAYS:                              # each of the three is read with nothing beside it
                words = str(r.get(k) or "")
                assert not words[:1].islower(), f"'{k}' does not start like a sentence: {words!r}"
        return [r for r in rows if (event is None or r.get("event") == event) and (step is None or r.get("step") == step)]

    def shown(self, first: int = 0) -> list[str]:
        """The journal as `load status` shows it, from row `first` on: text, why and next of each row."""
        return [shown(r) for r in self.rows()[first:]]

    def said(self) -> str:
        return "\n".join(self.out)

    def assert_nothing_started(self) -> None:
        assert self.runner.calls == [], self.runner.calls
        assert self.spawn.calls == [], "the worker was started"
        assert not paths.lock_file().exists(), "a lock was left"
        assert loadplan.run_result(self.w.run) in ("NOT_STARTED", "FINISHED", "FAILED")


def aged(f: Path) -> None:
    """Make a file look older than a heartbeat could leave it (a left-over, not a lock being written)."""
    old = time.time() - status.HEARTBEAT_STALE_S - 60
    os.utime(f, (old, old))


def interrupted(h: Harness, upto: str = "refresh", step: str | None = None) -> dict:
    """Leave the journal and the lock the way a worker that was cut off leaves them."""
    w = h.w
    archive = w.root / "backups-out" / "preload" / "yq-backup_2026-01-01_pre-data-010126_0900.7z"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(b"packed before the interruption")
    w.gates.mkdir(parents=True, exist_ok=True)
    pre = w.gates / "2026-01-01_085900_pre-data-010126.json"
    pre.write_text(json.dumps({"tables": {}}), encoding="utf-8")
    done = {"token": {}, "backup": {"archive": str(archive), "encrypted": True}, "pre_gate": {"path": str(pre)},
            "wait_tick": {"waited_seconds": 0}}
    yq.journal(w.run, "started", launch="oldlaunch", pid=DEAD_PID, resume=False, rehearsal=False, text="Load started.")
    for name in loadplan.STEPS:
        yq.journal(w.run, "step_start", step=name, text=yq.STEP_OPENS[name])
        if name == upto:
            break
        yq.journal(w.run, "step_ok", step=name, seconds=1.0, text="Done.", **done[name])
    lock = {"run": w.run, "pid": DEAD_PID, "pid_created": 1, "step": step or upto, "word_sha": "old-word",
            "started": "2026-01-01T09:00:00+00:00", "heartbeat": "2026-01-01T09:01:00+00:00"}
    paths.write_text(paths.lock_file(), json.dumps(lock))
    return {"archive": archive, "pre": pre, "lock": lock}


def cut_off(h: Harness, at: str = "refresh") -> None:
    """Start the load for real and cut its worker off inside the step `at`, the way a power cut does:
    the journal and the lock stay exactly as the worker had written them at that moment, and their
    process is gone. (interrupted() above invents that state; this one lets the real code make it.)"""
    seen: dict = {}

    def power_cut() -> None:
        seen["journal"] = loadplan.journal_file(h.w.run).read_bytes()
        seen["lock"] = dict(status.read_lock(), pid=DEAD_PID, pid_created=1)
        raise RuntimeError("cut off here")
    h.runner.hooks[at] = power_cut
    assert h.go() == 0 and h.work() == 1, h.out
    del h.runner.hooks[at]
    loadplan.journal_file(h.w.run).write_bytes(seen["journal"])       # nothing the worker wrote after that moment
    paths.write_text(paths.lock_file(), json.dumps(seen["lock"]))
    h.runner.calls.clear()
    h.w.trace.clear()
    h.box.shown.clear()
    h.out.clear()


# ── the permission-rule matcher ───────────────────────────────────────────────────────────────

@test("permission rules: the documented forms match the go; the read-only rule does not")
def _():
    blanket = ["Bash(python *)", "Bash(python:*)", "Bash(python -m scripts.yq_load_go *)",
               "Bash(python -m scripts.yq_load_go:*)", "Bash(python -m scripts.yq_load_go*)",
               "Bash(python -m scripts.*)", "Bash(*)", "Bash", "PowerShell(python *)", "PowerShell",
               "PowerShell(PYTHON -m scripts.yq_load_go *)", "Bash(python -m * --token *)"]
    for entry in blanket:
        assert yq.rule_matches(entry, GO), entry
    for entry in ["Bash(python -m scripts.ops *)", "PowerShell(python -m scripts.ops *)",
                  "Bash(python -m scripts.ops*)", "Bash(python -m scripts.ops:*)",
                  "Bash(python -m scripts.ops load *)", "Bash(python -m scripts.yq_load_go)",
                  "Bash(python -m scripts.yq_load_gone *)", "Read(python *)", "Bash(git push*)", "Bash()", "", "junk("]:
        assert not yq.rule_matches(entry, GO), entry
    assert yq.rule_matches("Bash(python -m scripts.yq_load_go *)", "python -m scripts.yq_load_go")   # the bare command
    assert yq.rule_matches("Bash(ls *)", "ls -la") and yq.rule_matches("Bash(ls *)", "ls")
    assert not yq.rule_matches("Bash(ls *)", "lsof") and yq.rule_matches("Bash(ls*)", "lsof")
    assert yq.rule_matches("Bash(npm run:*)", "npm run build") and not yq.rule_matches("Bash(npm run:*)", "npm runx")
    lines = yq.own_command_lines(RUN, "x")
    assert GO in lines and "python -X utf8 -m scripts.yq_load_go --run 010126_0900 --token x" in lines
    assert all(ln.endswith("--run 010126_0900 --token x") for ln in lines)
    assert loadplan.go_line(RUN, "x") == GO


@test("allow entries are read from the settings files; ask entries, other tools and broken files are told apart")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        user, project, local, broken = t / "u.json", t / "p.json", t / "l.json", t / "b.json"
        user.write_text(json.dumps({"permissions": {"ask": ["Bash(python -m scripts.yq_load_go*)"],
                                                    "allow": ["Bash(python -m scripts.ops *)", "Read(*)"]}}), encoding="utf-8")
        project.write_text("\ufeff" + json.dumps({"permissions": {"allow": ["Bash(git status)", "Bash(python *)"]}}),
                           encoding="utf-8")                                   # Notepad's byte-order mark
        local.write_text(json.dumps({"enabledMcpjsonServers": ["x"], "permissions": {"allow": "not a list"}}), encoding="utf-8")
        broken.write_text("{ not json", encoding="utf-8")
        hits, unreadable = yq.allow_entries_matching(yq.own_command_lines(RUN, "x"), [user, project, local, t / "absent.json"])
        assert hits == [(project, "Bash(python *)")] and unreadable == [], (hits, unreadable)
        hits, unreadable = yq.allow_entries_matching([GO], [broken])
        assert hits == [] and len(unreadable) == 1 and "b.json" in unreadable[0]
        names = [f.name for f in yq.settings_files(t)]
        assert names == ["settings.json", "settings.json", "settings.local.json"]
        assert yq.settings_files(t)[0].parent.name == ".claude" and yq.settings_files(t)[1] == t / ".claude" / "settings.json"


# ── the launcher's refusals ───────────────────────────────────────────────────────────────────

@test("refused in a linked worktree; --dev does not open a real go")
def _():
    with data_root() as root:
        h = Harness(root, linked=True)
        assert h.go() == 2 and "worktree" in h.said() and "Nothing was loaded" in h.said()
        assert h.box.shown == []
        h.assert_nothing_started()
        h.out.clear()
        assert h.go(dev=True) == 2 and "rehearsal only" in h.said()
        h.assert_nothing_started()
    with data_root() as root:                    # not linked: --no-box is refused for a real run all the same
        h2 = Harness(root)
        assert h2.go(no_box=True) == 2 and "rehearsal only" in h2.said() and h2.box.shown == []
        h2.assert_nothing_started()


@test("refused when the run or its plan is missing")
def _():
    with data_root() as root:
        h = Harness(root)
        assert yq.launch("020126_0900", "x", deps=h.deps, say=h.out.append) == 2
        assert "no load check" in h.said()
        for bad in ("", "010126", "..\\..\\x", "010126_09000"):
            assert yq.launch(bad, "x", deps=h.deps, say=h.out.append) == 2
        for broken in ("{ broken", "[1, 2]", "null"):          # no JSON, or JSON that is no plan
            h.out.clear()
            (loadplan.run_dir(RUN) / "plan.json").write_text(broken, encoding="utf-8")
            assert h.go() == 2 and "cannot be read" in h.said(), broken
            h.assert_nothing_started()
        assert h.box.shown == [] and h.w.trace == []


@test("a check that a newer one replaced is refused in one sentence; an interrupted one is closed, never left to loop")
def _():
    newer = "010126_0930"
    words = f"This check was replaced by a newer one (run {newer}): type /yq-load."
    with data_root() as root:                    # the load check retires the older checks of a folder: superseded_by
        h = Harness(root)
        h.w.write_plan(superseded_by=newer)
        assert h.go() == 2 and h.out == [words], h.out
        h.assert_nothing_started()
        assert h.box.shown == [] and h.w.trace == [] and loadplan.journal_read(RUN) == []
        assert h.go() == 2 and h.box.shown == []                # every time: a table that was replaced never loads
    with data_root() as root:                    # it is said before anything else that plan says: a stop, no token
        h = Harness(root)
        h.w.write_plan(superseded_by=newer, stops=["Stock ledger: a guard would refuse it"], token=None)
        assert h.go(token="x") == 2 and h.out == [words], h.out
    with data_root() as root:                    # a run that did load says that: nothing is to be done about it
        h = Harness(root)
        assert h.load() == 0
        h.w.write_plan(superseded_by=newer)
        h.out.clear()
        assert h.go() == 2 and "already loaded" in h.said() and "replaced" not in h.said()
    with data_root() as root:                    # replaced while the box stood open: asked once more after his Yes
        h = Harness(root)
        h.box.on_show = lambda: h.w.write_plan(superseded_by=newer)
        assert h.go() == 2
        assert h.out == [f"This check was replaced by a newer one (run {newer}) while the box was open. Nothing was "
                         "started: type /yq-load."], h.out
        assert len(h.box.shown) == 1 and h.spawn.calls == [] and not paths.lock_file().exists()
        assert loadplan.journal_read(RUN) == []
    with data_root() as root:                    # replaced after the worker began (a check that was already running)
        h = Harness(root)
        assert h.go() == 0
        h.runner.hooks["backup"] = lambda: h.w.write_plan(superseded_by=newer)
        assert h.work() == 3
        assert h.runner.labels() == ["backup", "pre_gate"] and "run:refresh" not in h.w.trace
        failed = h.rows("failed")[0]
        assert failed["result"] == "check_again" and failed["written"] is False and failed["step"] == "wait_tick"
        assert failed["why"] == ("This changed since the table was shown: the load check itself: a newer check replaced "
                                 f"it (run {newer}).")
        assert failed["text"] == "CHECK AGAIN at wait_tick. Nothing was written." and not paths.lock_file().exists()
    with data_root() as root:                    # an INTERRUPTED load whose check was replaced can never be resumed:
        h = Harness(root)                        # it is closed and its lock goes, or /yq-load would end here for ever
        interrupted(h, upto="refresh")
        h.w.write_plan(superseded_by=newer)
        assert h.go() == 2
        assert h.out == [f"The interrupted load is closed: it cannot be resumed. {MAY_HOLD}", words], h.out
        failed = h.rows("failed")[0]
        assert failed["result"] == "failed" and failed["check_again"] is True
        assert failed["written"] is True and failed["step"] == "refresh"
        assert failed["text"] == f"This interrupted load cannot be resumed: a newer check replaced its table. {MAY_HOLD}"
        assert failed["why"] == f"The check of run {newer} replaced this one." and failed["next"] == "Type /yq-load."
        assert not paths.lock_file().exists() and h.box.shown == [] and h.spawn.calls == [] and h.runner.calls == []
        assert status.failed_load() == {"run": RUN, "step": "refresh"}          # every skill now says LAST LOAD FAILED
        h.out.clear()
        assert h.go() == 2 and "refused for good" in h.said()
    with data_root() as root:                    # ... cut off before any refresh: nothing was written, and that is said
        h = Harness(root)
        interrupted(h, upto="pre_gate")
        h.w.write_plan(superseded_by=newer)
        assert h.go() == 2 and h.out == ["The interrupted load is closed: it cannot be resumed.", words], h.out
        failed = h.rows("failed")[0]
        assert failed["result"] == "check_again" and failed["written"] is False and "may hold" not in whole(failed)
        assert not paths.lock_file().exists() and status.failed_load() is None
    assert yq._replaced_by(RUN, {"superseded_by": ""}) is None and yq._replaced_by(RUN, {}) is None
    assert yq._replaced_by(RUN, {"superseded_by": newer}) == newer and yq._replaced_words(newer) == words
    assert yq._replaced_words(yq._replaced_by(RUN, {"superseded_by": True})) == \
        "This check was replaced by a newer one: type /yq-load."
    with data_root():                            # no plan file at all says nothing
        assert yq._replaced_by(RUN) is None


@test("refused when the check said STOP or gave no token")
def _():
    with data_root() as root:
        h = Harness(root)
        h.w.write_plan(verdicts=h.w.plan["verdicts"] + [{"file": "Stock_ledger1.xlsx", "verdict": "stop", "text": "guard"}])
        assert h.go() == 2 and "STOP" in h.said() and "Stock_ledger1.xlsx" in h.said()
        h.assert_nothing_started()
    for blank in (None, ""):
        with data_root() as root:
            h = Harness(root)
            token = h.w.token
            h.w.write_plan(token=blank)
            assert h.go(token=token) == 2 and "no token" in h.said()
            h.assert_nothing_started()
    with data_root() as root:                    # the check's own list of stops, with a token left in by mistake
        h = Harness(root)
        h.w.write_plan(stops=["Stock ledger: a guard would refuse it"])
        assert h.go() == 2 and "STOP" in h.said() and "a guard would refuse it" in h.said()
        h.assert_nothing_started()
    with data_root() as root:                    # ... written as one sentence instead of a list: a stop all the same
        h = Harness(root)
        h.w.write_plan(stops="the stock ledger guard would refuse it")
        assert h.go() == 2 and "STOP" in h.said() and "the stock ledger guard would refuse it" in h.said()
        h.assert_nothing_started()
    assert yq.plan_stops({"verdicts": [], "stops": "one sentence"}) == ["one sentence"]
    assert yq.plan_stops({"verdicts": [], "stops": ("a", "b")}) == ["a", "b"] and yq.plan_stops({"stops": ""}) == []
    assert yq.plan_stops({"verdicts": {"a.xlsx": "load", "b.xlsx": "STOP"}}) == ["b.xlsx"]
    assert yq.plan_stops({"verdicts": [{"report": "a", "decision": "Stop"}]}) == ["(no a)"]
    assert yq.plan_stops({"verdicts": [], "ok": False}) and yq.plan_stops({"verdict": "stop"})
    assert yq.plan_stops({"verdicts": [{"file": "a", "verdict": "needs_decision"}, {"file": "b", "verdict": "not_loaded"}]}) == []


@test("refused when the run already finished, or FAILED (a failed staging is refused for good)")
def _():
    with data_root() as root:
        h = Harness(root)
        yq.journal(RUN, "started", pid=1)
        yq.journal(RUN, "finished", result="ok")
        assert h.go() == 2 and "already loaded" in h.said()
        h.assert_nothing_started()
    with data_root() as root:
        h = Harness(root)
        yq.journal(RUN, "started", pid=1)
        yq.journal(RUN, "failed", result="failed")
        assert h.go() == 2 and "FAILED" in h.said() and "refused for good" in h.said()
        assert h.go() == 2
        h.assert_nothing_started()
        assert h.box.shown == []


@test("refused when a permissions.allow entry matches the go: the entry to delete is printed")
def _():
    forms = ["Bash(python *)", "Bash(python:*)", "PowerShell(python -m scripts.yq_load_go *)", "Bash(*)", "Bash",
             "Bash(py *)", f"PowerShell({sys.executable} *)", "Bash(" + sys.executable.replace("\\", "/") + ":*)"]
    for entry in forms:
        with data_root() as root:
            f = root / ".claude" / "settings.local.json"
            f.parent.mkdir(parents=True)
            f.write_text(json.dumps({"permissions": {"allow": ["Bash(python -m scripts.ops *)", entry]}}), encoding="utf-8")
            h = Harness(root, settings=[root / ".claude" / "settings.json", f])
            assert h.go() == 2, entry
            assert f'"{entry}"' in h.said() and str(f) in h.said() and "Nothing was loaded" in h.said()
            assert "scripts.ops *" not in h.said()
            h.assert_nothing_started()
            assert h.box.shown == []
    with data_root() as root:                    # the proper read-only rules and the ask rules: no refusal
        f = root / "settings.json"
        f.write_text(json.dumps({"permissions": {
            "allow": ["Bash(python -m scripts.ops *)", "PowerShell(python -m scripts.ops *)", "Bash(python -m scripts.ops*)"],
            "ask": ["Bash(python -m scripts.yq_load_go*)", "PowerShell(python -m scripts.yq_load_go*)"]}}), encoding="utf-8")
        h = Harness(root, settings=[f])
        assert h.go() == 0, h.out
    with data_root() as root:                    # a settings file that cannot be read: refused, not guessed
        f = root / "settings.json"
        f.write_text("{ not json", encoding="utf-8")
        h = Harness(root, settings=[f])
        assert h.go() == 2 and "cannot be read" in h.said()
        h.assert_nothing_started()


@test("refused while a load is running, and when another run was interrupted")
def _():
    with data_root() as root:
        h = Harness(root)
        fresh = datetime.now(timezone.utc).isoformat(timespec="seconds")
        mine = {"run": "311225_0900", "pid": os.getpid(), "pid_created": yq.process_created(os.getpid()),
                "step": "refresh", "word_sha": "n", "heartbeat": fresh}
        paths.write_text(paths.lock_file(), json.dumps(mine))
        assert status.lock_state()["state"] == "running"
        assert h.go() == 2 and "A load is running (run 311225_0900, at refresh)" in h.said()
        assert "Nothing was started: follow it with python -m scripts.ops load status --wait" in h.said()
        assert h.spawn.calls == [] and h.box.shown == []
        h.out.clear()
        paths.write_text(paths.lock_file(), json.dumps(dict(mine, pid=DEAD_PID)))
        assert h.go() == 2 and "LOAD INTERRUPTED at refresh (run 311225_0900)" in h.said()
        assert h.spawn.calls == [] and paths.lock_file().exists() and "cannot be read: tell Claude" in h.said()
        h.out.clear()                                             # with its plan on file, the refusal gives its resume line
        loadplan.write_plan({"run": "311225_0900", "token": "thetokenofthatrun", "verdicts": []})
        assert h.go() == 2
        assert "Its resume: python -m scripts.yq_load_go --run 311225_0900 --token thetokenofthatrun" in h.said()
        h.out.clear()                                             # this process id, but another process's birth
        paths.write_text(paths.lock_file(), json.dumps(dict(mine, pid_created=1)))
        assert yq.worker_alive(dict(mine, pid_created=1)) is False and yq.worker_alive(mine) is True
        assert h.go() == 2 and "LOAD INTERRUPTED" in h.said()


@test("a worker that is alive but silent for two minutes: its own sentence, no circle with `load status`, no second loader")
def _():
    stale = "2026-01-01T00:00:00+00:00"
    me = os.getpid()
    for run in ("311225_0900", RUN):                 # another run's worker, and this very run's
        with data_root() as root:
            h = Harness(root)
            if run == RUN:
                interrupted(h, upto="refresh")       # the journal says this run's refresh had begun
            lock = {"run": run, "pid": me, "pid_created": yq.process_created(me),
                    "step": "refresh", "word_sha": "n", "heartbeat": stale}
            paths.write_text(paths.lock_file(), json.dumps(lock))
            # every skill reads this lock as INTERRUPTED and sends him to /yq-load ...
            assert status.lock_state()["state"] == "interrupted" and yq.worker_alive(lock) is True
            assert status.interrupted_banner() == "LOAD INTERRUPTED at refresh: run /yq-load"
            assert h.go() == 2
            said = h.said()
            # ... so the go must not answer "a load is running: follow it" (that status says INTERRUPTED again)
            assert "A load is running" not in said and "follow it with" not in said, said
            # the whole sentence: what is the matter, that nothing starts, and what he can do about it
            assert said == (
                f"The load's worker is still on this PC but has written nothing for more than 2 minutes (run {run}, at "
                "refresh). Nothing was started and nothing is resumed while it is there: ask Claude to check it; if it "
                f"has truly hung, end python.exe (pid {me}) in Task Manager, then type /yq-load to resume."), said
            assert h.spawn.calls == [] and h.box.shown == [] and h.runner.calls == []      # no second loader, no resume
            assert status.read_lock() == lock, "the lock of a live process was touched"
            assert h.rows("failed") == []
    with data_root() as root:                        # a REHEARSAL's worker that has gone silent: still a live process
        h = Harness(root)
        lock = {"run": "000000_0859", "pid": me, "pid_created": yq.process_created(me), "rehearsal": True,
                "step": "refresh (rehearsal)", "word_sha": "r", "heartbeat": stale}
        paths.write_text(paths.lock_file(), json.dumps(lock))
        # by the heartbeat it is no interrupted LOAD (no banner anywhere) ...
        assert status.lock_state() == {"state": "none", "rehearsal_left": "000000_0859", "step": "refresh (rehearsal)"}
        assert status.interrupted_banner() is None and yq.worker_alive(lock) is True
        assert h.go() == 2                           # ... but its process is there: nothing starts beside it
        assert h.said() == (
            "The rehearsal's worker is still on this PC but has written nothing for more than 2 minutes (run "
            "000000_0859, at refresh (rehearsal)). Nothing was started and nothing is resumed while it is there: ask "
            f"Claude to check it; if it has truly hung, end python.exe (pid {me}) in Task Manager; the next load or "
            "rehearsal clears its lock."), h.said()
        assert h.spawn.calls == [] and h.box.shown == [] and status.read_lock() == lock
    with data_root() as root:                        # a lock that did not note its process's birth: the number alone may
        h = Harness(root)                            # be another program's by now, so nobody is told to end it
        lock = {"run": "311225_0900", "pid": me, "step": "refresh", "word_sha": "n", "heartbeat": stale}
        paths.write_text(paths.lock_file(), json.dumps(lock))
        assert yq.worker_alive(lock) is True, "it cannot be told from a stranger: nothing starts beside it"
        assert h.go() == 2
        assert h.said().endswith("nothing is resumed while it is there: ask Claude to check it (it could not be confirmed "
                                 f"that process {me} is that worker: do not end it yourself)."), h.said()
        assert "Task Manager" not in h.said() and h.spawn.calls == [] and status.read_lock() == lock
    linked, real_settings = yq.gitinfo.is_linked_worktree, yq.settings_files
    yq.gitinfo.is_linked_worktree, yq.settings_files = (lambda r: False), (lambda r: [])
    try:
        with data_root():                            # a rehearsal asked for at that moment gets the same sentence
            lock = {"run": "311225_0900", "pid": os.getpid(), "pid_created": yq.process_created(os.getpid()),
                    "step": "refresh", "word_sha": "n", "heartbeat": stale}
            paths.write_text(paths.lock_file(), json.dumps(lock))
            spawn, out = Spawn(), []
            assert yq.rehearse(0, no_box=True, say=out.append, spawn=spawn, start_wait_s=0) == 2
            assert "has written nothing for more than 2 minutes" in out[0] and "No rehearsal was started" in out[0]
            assert spawn.calls == [] and status.read_lock() == lock
    finally:
        yq.gitinfo.is_linked_worktree, yq.settings_files = linked, real_settings
    assert status.HEARTBEAT_STALE_S == 120, "the sentence says two minutes"


@test("a rehearsal's lock: a live rehearsal blocks a real go with a plain sentence; a lock it cannot read stops the go")
def _():
    me = os.getpid()
    with data_root() as root:
        h = Harness(root)
        lock = {"run": "000000_0859", "pid": me, "pid_created": yq.process_created(me), "rehearsal": True,
                "step": "backup (rehearsal)", "word_sha": "r",
                "heartbeat": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        paths.write_text(paths.lock_file(), json.dumps(lock))
        assert status.lock_state() == {"state": "running", "run": "000000_0859", "step": "backup (rehearsal)"}
        assert h.go() == 2
        assert h.said() == ("A rehearsal is running (run 000000_0859, at backup (rehearsal)). Nothing was started: follow "
                            "it with python -m scripts.ops load status --wait"), h.said()
        assert h.spawn.calls == [] and h.box.shown == [] and h.runner.calls == [] and status.read_lock() == lock
        assert loadplan.journal_read(RUN) == []
    with data_root() as root:                    # the lock file is there and no read of it succeeds: never a guess
        h = Harness(root)
        paths.write_text(paths.lock_file(), json.dumps({"run": "311225_0900", "pid": me, "step": "refresh"}))
        real_read, real_sleep = status.read_lock, yq.time.sleep

        def held():
            raise PermissionError(13, "another program holds the lock file")
        status.read_lock, yq.time.sleep = held, (lambda s: None)
        try:
            assert h.go() == 2
            out: list[str] = []
            linked, real_settings = yq.gitinfo.is_linked_worktree, yq.settings_files
            yq.gitinfo.is_linked_worktree, yq.settings_files = (lambda r: False), (lambda r: [])
            try:
                assert yq.rehearse(0, no_box=True, say=out.append, spawn=Spawn(), start_wait_s=0) == 2
            finally:
                yq.gitinfo.is_linked_worktree, yq.settings_files = linked, real_settings
            buf = io.StringIO()
            real_out, sys.stdout = sys.stdout, buf
            try:                                 # a worker that cannot read the lock has no proof of anything
                assert yq.worker(RUN, deps=h.deps, nonce="any word", parent_pid=me) == 2
            finally:
                sys.stdout = real_out
        finally:
            status.read_lock, yq.time.sleep = real_read, real_sleep
        assert h.said() == ("The load lock could not be read (PermissionError): another program holds the file, and a "
                            "load may be running. Nothing was started: try again in a minute."), h.said()
        assert out == ["The load lock could not be read (PermissionError): another program holds the file, and a load "
                       "may be running. No rehearsal was started: try again in a minute."], out
        assert "(refused: the lock could not be read: PermissionError)" in buf.getvalue()
        assert h.spawn.calls == [] and h.box.shown == [] and h.runner.calls == []
        assert [p.name for p in paths.loads_dir().iterdir()] == [RUN], "a rehearsal's folder was made"


@test("left-over locks that block nothing: a dead rehearsal's, an unreadable one, a finished run's")
def _():
    with data_root() as root:
        h = Harness(root)
        paths.write_text(paths.lock_file(), json.dumps({"run": "000000_0859", "pid": DEAD_PID, "step": "refresh (rehearsal)",
                                                        "word_sha": "r"}))
        # scripts.ops.status reads a cut-off rehearsal's lock as no lock at all (it is no interrupted load) ...
        assert status.lock_state() == {"state": "none", "rehearsal_left": "000000_0859", "step": "refresh (rehearsal)"}
        assert status.interrupted_banner() is None
        # ... so the go reads the file itself: the left-over is cleared and said before the load takes the lock
        assert h.go() == 0 and h.out[0] == ("(the rehearsal 000000_0859 had stopped at refresh (rehearsal); its "
                                            "left-over lock is cleared)"), h.out
        assert status.read_lock()["run"] == RUN and status.lock_state()["state"] == "running"
    with data_root() as root:
        h = Harness(root)
        paths.write_text(paths.lock_file(), "")
        # written just now (here, or synced from another PC while its launcher writes it): never cleared
        assert h.go() == 2 and "being written at this moment" in h.said() and paths.lock_file().read_text() == ""
        assert h.spawn.calls == []
        h.out.clear()
        aged(paths.lock_file())                  # a launcher's left-over: no load ever ran under it
        assert h.go() == 0 and "unreadable" in h.said() and status.read_lock()["run"] == RUN
    with data_root() as root:
        h = Harness(root)
        yq.journal(RUN, "started", pid=1)
        yq.journal(RUN, "finished", result="ok")
        paths.write_text(paths.lock_file(), json.dumps({"run": RUN, "pid": DEAD_PID, "step": "publish", "word_sha": "x"}))
        assert h.go() == 2 and "already loaded" in h.said()
        assert not paths.lock_file().exists(), "the lock of a finished run was left behind"


# ── the token and the box ─────────────────────────────────────────────────────────────────────

@test("token moved: exit 3, the moved parts are printed, no lock, the runner is never called")
def _():
    with data_root() as root:
        h = Harness(root)
        (h.w.drop / "Sales_day_book1.xlsx").write_bytes(b"exported again")
        h.w.fingerprint["orders"] = [11, 100]
        assert h.go() == 3
        said = h.said()
        assert "CHECK AGAIN" in said and "the drop folder: Sales_day_book1.xlsx" in said
        assert "the database table orders" in said and "/yq-load" in said
        h.assert_nothing_started()
        assert h.box.shown == [] and loadplan.journal_read(RUN) == []
    with data_root() as root:                    # each part on its own
        for change, words in ((lambda w: setattr(w, "head", "c" * 40), "this folder's programs (HEAD)"),
                              (lambda w: setattr(w, "live", "d" * 12), "the live site's commit"),
                              (lambda w: (w.staging / "Stock_balance1.xlsx").write_bytes(b"x"), "the staged copies: Stock_balance1.xlsx"),
                              (lambda w: w.fingerprint.update(ingest_runs=9), "the database table ingest_runs")):
            for p in (root / "Uplaods", root / "business_data"):
                yq._remove_tree(p)
            h = Harness(root)
            change(h.w)
            assert h.go() == 3 and words in h.said(), (words, h.out)
            h.assert_nothing_started()


@test("a token that is not this run's is 'check again' before anything is read; one commit written two ways is no change")
def _():
    with data_root() as root:
        h = Harness(root)
        assert h.go(token="notthetoken") == 3 and "GO line" in h.said()
        assert h.w.trace == [] and h.box.shown == []
        h.assert_nothing_started()
        h.w.live = "b" * 40                       # /health reports the long form of the same commit
        assert h.go() == 0, h.out
    assert yq._same_commit_form("b" * 12, "b" * 40) == "b" * 12 and yq._same_commit_form("b" * 12, "c" * 40) == "c" * 40
    assert yq._same_commit_form("", "b" * 40) == "b" * 40 and yq._same_commit_form("bbb", "bbbb") == "bbbb"


@test("the box: No or no answer is exit 4, nothing loaded, and the same go may be asked again")
def _():
    for answer, words in (("no", "You answered No: nothing loaded."), ("timeout", "no answer, nothing loaded"),
                          ("closed", "no answer, nothing loaded"), ("unavailable", "could not be shown")):
        with data_root() as root:
            h = Harness(root, answer=answer)
            assert h.go() == 4, answer
            assert words in h.said() and "nothing loaded" in h.said()
            h.assert_nothing_started()
            assert len(h.box.shown) == 1 and loadplan.journal_read(RUN) == []
            h.box.answer = "yes"                  # he says go again: the box is shown again and Yes loads
            assert h.go() == 0 and len(h.box.shown) == 2 and len(h.spawn.calls) == 1


@test("the box says what he approves: folder, one line per file, expected figures; default No, on top, 540 s")
def _():
    with data_root() as root:
        h = Harness(root)
        assert h.go() == 0
        title, text, timeout_s = h.box.shown[0]
        assert title == "YQ load 010126" and timeout_s == 540 == yq.BOX_TIMEOUT_S
        lines = text.splitlines()
        assert lines[0] == f"Folder: {h.w.drop}"
        assert "Sales_day_book1.xlsx: LOAD" in lines and "Stock_balance1.xlsx: LOAD (main-store block only)" in lines
        assert "Profit1.xlsx: HOLD BACK" in lines and "(no Receivables ageing): NOT LOADED" in lines
        assert "Price_book1.xlsx: NEEDS YOUR DECISION (left out of this load)" in lines
        assert "(+2 other file(s) or folder(s): NOT LOADED by this command)" in lines
        assert "After the load you should see:" in lines and "- Sales to Wed 31 Dec: 12 invoices in all." in lines
        assert "- Stock (main store) as on Thu 01 Jan: 10 items." in lines and not any("sales_to" in ln for ln in lines)
        assert "the reason the check gave" not in text and max(len(ln) for ln in lines) <= 160
        assert lines[-1] == "Yes = load now. No = nothing changes."
        assert "RESUME" not in text and "REHEARSAL" not in text
        assert h.w.plan["flags"] == ["hold_back"] and "ATTENTION" not in text      # its own file line says it already
    assert yq.BOX_FLAGS & 0x4 and yq.BOX_FLAGS & 0x100 and yq.BOX_FLAGS & 0x40000      # Yes/No, default No, topmost
    title, text = yq.box_text("000000_0900", {"drop": "x", "verdicts": [f"file {i}" for i in range(20)],
                                              "expected": "one\ntwo"}, resume_step="refresh", rehearsal=True)
    assert title == "YQ load 000000 (REHEARSAL)" and "interrupted at step 'refresh'" in text
    assert "(+4 more lines" in text and "- one" in text.splitlines() and text.startswith("REHEARSAL")
    loose = yq.box_text(RUN, {"drop": "x", "verdicts": {"a.xlsx": "load", "b.xlsx": "hold back"}, "expected": {"Stock items": 10}})[1]
    assert "a.xlsx: LOAD" in loose.splitlines() and "b.xlsx: HOLD BACK" in loose.splitlines() and "- Stock items: 10" in loose
    said_load = dict(verdict("Price_book1.xlsx", "selling_prices:MA_base", "Price book", "needs_decision", True))
    assert "left out" not in yq.box_text(RUN, {"drop": "x", "verdicts": [said_load]})[1]
    assert set(yq.VERDICT_WORDS) == set(loadplan.VERDICTS)
    if os.name != "nt":
        assert yq.windows_box("t", "x", 0.01) == "unavailable"


MIXED = "ATTENTION: the reports of this folder carry different as-on dates: are they one export?"
OLDER = "ATTENTION: a report of this folder is older than what is already loaded: older figures would come back."
SIM_OUT = "ATTENTION: the Stock ledger has no SIM movements: the loaded ones leave with this load (SIM never counts)."


@test("the box shows what the check flagged, each line starting ATTENTION, under the file lines; a plan with no flag shows none")
def _():
    with data_root() as root:                    # two flags: he cannot say Yes without having seen them
        h = Harness(root)
        h.w.write_plan(flags=["mixed_dates", "older_than_loaded"])
        assert h.go() == 0, h.out
        lines = h.box.shown[0][1].splitlines()
        marked = [i for i, ln in enumerate(lines) if ln.startswith("ATTENTION:")]
        assert [lines[i] for i in marked] == [MIXED, OLDER], lines
        files = lines.index("(+2 other file(s) or folder(s): NOT LOADED by this command)")        # the last file line
        assert lines[files + 1] == "" and marked == [files + 2, files + 3], "not straight under the file lines"
        assert lines[marked[-1] + 1] == "" and lines[marked[-1] + 2] == "After the load you should see:"
        assert lines[-1] == "Yes = load now. No = nothing changes." and max(len(ln) for ln in lines) <= 160
    with data_root() as root:                    # with an option as well: files, then ATTENTION, then ON YOUR WORD
        h = Harness(root)
        with_options(h, (loadplan.MARKERS[1],), partial_ok=True)
        h.w.write_plan(flags=["hold_back", "mixed_dates"])
        assert h.go() == 0, h.out
        lines = h.box.shown[0][1].splitlines()
        order = [ln.split(":")[0].split(" (")[0] for ln in lines if ln.startswith(("ATTENTION", "ON YOUR WORD", "After"))]
        assert order == ["ATTENTION", "ON YOUR WORD", "After the load you should see"], order
    for none in ([], None, ["hold_back", "needs_decision", "partial_drop", "price_scope", "replaced_earlier", "token"]):
        with data_root() as root:                # no flag, or only routine ones: not one ATTENTION line
            h = Harness(root)
            h.w.write_plan(flags=none)
            assert yq.attention_lines(h.w.plan) == [] and h.go() == 0
            assert "ATTENTION" not in h.box.shown[0][1], none
    # every flag scripts/ops/load_check.py raises today is either a line of the box or known to be routine: one that
    # is neither (a flag added there later) is shown under its own name, so this list is where it is decided
    todays = ("mixed_dates", "replaced_earlier", "older_than_loaded", "hold_back", "profit_above_ledger",
              "profit_held_twice", "needs_decision", "price_scope", "invoices_changed", "vouchers_changed",
              "catalog_switch", "partial_drop", "moved_meanwhile", "marker_mismatch", "last_load_failed", "stop", "dev",
              "token", "sim_ledger_out")
    assert set(todays) <= set(yq.ATTENTION_WORDS) | yq.ROUTINE_FLAGS, set(todays) - set(yq.ATTENTION_WORDS) - yq.ROUTINE_FLAGS
    assert yq.attention_lines({"flags": list(todays)}) == [
        MIXED, OLDER, "ATTENTION: the last load failed after it had begun to write: this load replaces it.", SIM_OUT]
    # the shapes a flag may come in: its code word, a sentence, a sentence that already says ATTENTION
    assert yq.attention_lines({"flags": ["mixed_dates"]}) == [MIXED] == yq.attention_lines({"flags": ["Mixed-Dates"]})
    assert yq.attention_lines({"flags": ["the stock file is two days older than the sales file"]}) == [
        "ATTENTION: the stock file is two days older than the sales file"]
    assert yq.attention_lines({"flags": ["ATTENTION: the reports carry different as-on dates (Mon 05 Jan, Wed 07 Jan)"],
                               "attention": ["mixed_dates", "mixed_dates"]}) == [
        MIXED, "ATTENTION: the reports carry different as-on dates (Mon 05 Jan, Wed 07 Jan)"]
    # a flag this file does not know is shown under its own name: nothing the check flags is dropped in silence
    assert yq.attention_lines({"flags": ["same_day_reload"]}) == [
        "ATTENTION: the check marked this load 'same day reload': read its line in the chat before you answer."]
    assert yq.attention_lines({"flags": "mixed_dates"}) == [MIXED] and yq.attention_lines({"flags": {"a": 1}}) == []
    assert yq.attention_lines({}) == [] and yq.attention_lines({"flags": ["", None, "  "]}) == []
    assert not set(yq.ATTENTION_WORDS) & yq.ROUTINE_FLAGS
    many = yq.box_text(RUN, {"drop": "x", "verdicts": [], "flags": [f"flag_{i}" for i in range(11)]})[1].splitlines()
    assert len([ln for ln in many if ln.startswith("ATTENTION:")]) == yq.ATTENTION_MAX + 1
    assert f"ATTENTION: (+{11 - yq.ATTENTION_MAX} more such lines: see the table in the chat)" in many
    long = yq.attention_lines({"flags": ["word " * 60]})
    assert len(long) == 1 and len(long[0]) <= 150 and long[0].endswith("...")
    box = yq.box_text("000000_0900", {"drop": "x", "verdicts": [], "flags": []}, rehearsal=True)[1]
    assert "ATTENTION" not in box


def with_options(h: Harness, markers: tuple = (), **options) -> None:
    """What `load check` leaves when he gave it options: the options in the plan, the loader's marker
    files in staging (written before the token is worked out, so the token covers them)."""
    for name in markers:
        (h.w.staging / name).write_text(f"written by load check {RUN} on the owner's option\n", encoding="utf-8")
    parts = h.w.parts(True)
    h.w.write_plan(options=dict({"dev": False, "pricebook": None, "replace_ok": False, "partial_ok": False,
                                 "with_profitability": False}, **options),
                   token_parts=parts, token=loadplan.token_of(parts))


@test("the box names every option that changes what a Yes does; a plain load shows none")
def _():
    replace_ok, partial_ok = loadplan.MARKERS
    with data_root() as root:
        h = Harness(root)
        plain = yq.box_text(RUN, h.w.plan)[1]
        assert "ON YOUR WORD" not in plain and yq.option_flags(h.w.plan) == [] and yq.option_lines(h.w.plan) == []
        with_options(h, (replace_ok, partial_ok), replace_ok=True, partial_ok=True, with_profitability=True,
                     pricebook="load")
        assert h.go() == 0, h.out
        text = h.box.shown[0][1]
        lines = text.splitlines()
        assert text != plain, "a Yes with --replace-ok looks like an ordinary Yes"
        assert "ON YOUR WORD (--replace-ok): invoices these files no longer hold are REMOVED from the database." in lines
        assert "ON YOUR WORD (--partial-ok): a stock balance with fewer warehouses than the last one is accepted." in lines
        assert any(ln.startswith("ON YOUR WORD (--with-profitability): ") for ln in lines)
        assert any(ln.startswith("ON YOUR WORD (--pricebook load): ") for ln in lines)
        said = [i for i, ln in enumerate(lines) if ln.startswith("ON YOUR WORD")]
        assert len(said) == 4 and lines.index("After the load you should see:") > said[-1] > lines.index(f"Folder: {h.w.drop}")
        assert lines[-1] == "Yes = load now. No = nothing changes." and max(len(ln) for ln in lines) <= 160
        assert h.work() == 0                         # the journal keeps his words with the start of the load
        assert h.rows("started")[0]["options"] == ["--replace-ok", "--partial-ok", "--with-profitability", "--pricebook load"]
    with data_root() as root:                        # one option only; 'skip' and False are not options to show
        h = Harness(root)
        with_options(h, (partial_ok,), partial_ok=True, pricebook="skip")
        assert yq.option_flags(h.w.plan) == ["--partial-ok"] and len(yq.option_lines(h.w.plan)) == 1
        assert h.load() == 0 and h.rows("started")[0]["options"] == ["--partial-ok"]
    with data_root() as root:
        h = Harness(root)
        assert h.load() == 0 and "options" not in h.rows("started")[0]
    assert yq.option_flags({"options": "junk"}) == [] and yq.option_flags({}) == []
    assert {key for key, _flag, _words in yq.OPTION_WORDS} >= {m.lower() for m in loadplan.MARKERS}


@test("a marker file the box would not show, or an option without its marker, is refused: nothing starts")
def _():
    replace_ok, partial_ok = loadplan.MARKERS
    for marker, flag in ((replace_ok, "--replace-ok"), (partial_ok, "--partial-ok")):
        key = marker.lower()
        with data_root() as root:                    # the marker sits in staging, the plan does not carry the option
            h = Harness(root)
            with_options(h, (marker,))
            assert yq.option_mismatch(h.w.plan, h.w.staging), marker
            assert h.go() == 2, h.out
            assert f"marker file {marker}" in h.said() and f"did not show the option {flag}" in h.said()
            assert "Nothing was started" in h.said() and h.box.shown == []
            h.assert_nothing_started()
        with data_root() as root:                    # the same marker under another spelling: the loader's own rule
            h = Harness(root)
            with_options(h, (marker.lower() + ".txt",))
            assert h.go() == 2 and h.box.shown == [], h.out
            h.assert_nothing_started()
        with data_root() as root:                    # the option is in the plan, its marker is not in staging
            h = Harness(root)
            with_options(h, (), **{key: True})
            assert h.go() == 2, h.out
            assert f"showed the option {flag}" in h.said() and "is not in the staged folder" in h.said()
            assert h.box.shown == []
            h.assert_nothing_started()
        with data_root() as root:                    # both there: no refusal
            h = Harness(root)
            with_options(h, (marker,), **{key: True})
            assert yq.option_mismatch(h.w.plan, h.w.staging) == [] and h.go() == 0, h.out
    with data_root() as root:                        # a marker added after the check is 'check again' first: the token moved
        h = Harness(root)
        (h.w.staging / replace_ok).write_text("added by hand\n", encoding="utf-8")
        assert h.go() == 3 and f"the staged copies: {replace_ok}" in h.said() and h.box.shown == []
        h.assert_nothing_started()
    assert yq.option_mismatch({"options": None}, Path("no such folder")) == []


# ── the launcher starts the worker ────────────────────────────────────────────────────────────

@test("Yes: the lock is taken, the worker is started once, the launcher returns 0 with the follow line")
def _():
    with data_root() as root:
        h = Harness(root)
        assert h.go() == 0
        assert h.out[-1] == FOLLOW
        call = h.spawn.calls[0]
        assert len(h.spawn.calls) == 1 and call["run"] == RUN and call["root"] == root
        assert call["log"] == loadplan.run_dir(RUN) / "worker.log" and len(call["nonce"]) == 32
        lock = status.read_lock()
        assert {"run", "pid", "step", "started", "heartbeat"} <= set(lock), lock
        assert lock["run"] == RUN and lock["step"] == "start" and lock["word_sha"] == h.spawn.sha
        assert lock["pid"] == os.getpid(), "until the hand-over the lock names the launcher"
        assert status.lock_state() == {"state": "running", "run": RUN, "step": "start"}
        assert h.runner.calls == [] and loadplan.journal_read(RUN) == []       # the launcher runs no step
        assert h.w.trace == ["token:with fingerprint"]
        h.out.clear()
        assert h.go() == 2 and "A load is running" in h.said() and len(h.spawn.calls) == 1


@test("a lock is taken exclusively: a go that lost the race starts nothing; a worker that cannot start leaves no lock")
def _():
    with data_root() as root:
        h = Harness(root)

        def somebody_else_starts() -> None:
            paths.write_text(paths.lock_file(), json.dumps({"run": RUN, "pid": os.getpid(), "step": "start", "word_sha": "theirs",
                                                            "pid_created": yq.process_created(os.getpid())}))
        h.box.on_show = somebody_else_starts
        assert h.go() == 2 and "Another load took the lock" in h.said()
        assert h.spawn.calls == [] and status.read_lock()["word_sha"] == "theirs"
    with data_root() as root:
        h = Harness(root, spawn=Spawn(error=OSError("no such program")))
        assert h.go() == 2 and "could not be started" in h.said() and not paths.lock_file().exists()
    with data_root() as root:                    # the worker died as it started: said now, lock released
        h = Harness(root, spawn=Spawn(code=1))
        assert h.go() == 1 and "stopped as it started" in h.said() and not paths.lock_file().exists()
    with data_root() as root:                    # the worker runs but its journal cannot be read here: still "started"
        h = Harness(root)
        real_read = loadplan.journal_read

        def unreadable(run):
            if h.spawn.calls:                    # from the moment the worker is started
                raise PermissionError("held by the sync client")
            return real_read(run)
        loadplan.journal_read = unreadable
        try:
            assert h.go() == 0, h.out
        finally:
            loadplan.journal_read = real_read
        assert "load started" in h.out[-1] and "Nothing was started" not in h.said()
        assert len(h.spawn.calls) == 1 and status.read_lock()["word_sha"] == h.spawn.sha
    with data_root() as root:                    # ... or after its first lines: interrupted, with the line that resumes it
        h = Harness(root)

        def dies_midway(run, nonce):
            yq.journal(run, "started", launch=yq.word_hash(nonce)[:8], pid=DEAD_PID)
            yq.journal(run, "step_start", step="token")
            return 9
        h.spawn.inline = dies_midway
        assert h.go() == 1 and "LOAD INTERRUPTED" in h.said() and paths.lock_file().exists()
        assert f"Its resume: python -m scripts.yq_load_go --run {RUN} --token {h.w.token}" in h.said()
        assert "may hold" not in h.said(), "nothing of this load had been written"
    with data_root():
        a, b = yq.LockFile(RUN, "aaaa"), yq.LockFile(RUN, "bbbb")
        a.create()
        raises(yq.LockTaken, b.create)
        assert status.read_lock()["word_sha"] == "aaaa"
        assert b.release() is False and paths.lock_file().exists()           # not b's to remove
        assert a.release() is True and not paths.lock_file().exists()
        assert a.update(step="x") is False and not paths.lock_file().exists()   # a released lock is never written back


@test("a worker that is already done when the launcher looks: its last line is said, its exit code passed on")
def _():
    with data_root() as root:
        h = Harness(root)
        h.inline()
        assert h.go() == 0, h.out
        assert h.out[-1].startswith("LOAD FINISHED.") and FOLLOW not in h.out
        assert loadplan.run_result(RUN) == "FINISHED" and not paths.lock_file().exists()
        time.sleep(0.05)
        assert not paths.lock_file().exists(), "the launcher wrote the lock back after the worker released it"
    with data_root() as root:
        h = Harness(root)
        h.inline()
        h.runner.script["backup"] = [yq.Child(1, "no database\n")]
        assert h.go() == 1 and "LOAD STOPPED at backup" in h.out[-1] and not paths.lock_file().exists()
    with data_root() as root:
        h = Harness(root)
        h.inline()
        h.runner.hooks["backup"] = lambda: h.w.fingerprint.update(orders=[11, 100])
        assert h.go() == 3 and "CHECK AGAIN at wait_tick" in h.out[-1] and not paths.lock_file().exists()
        assert "Nothing was written." in h.out[-1] and h.rows("failed")[0]["result"] == "check_again"


@test("a worker Windows kept inside the editor's job: he is told to keep the window open, by the launcher and in the journal")
def _():
    stay = yq.stay_open_words(False)
    assert stay == "Keep the Claude Code window open until LOAD FINISHED: this load stops if it is closed."
    assert "REHEARSAL FINISHED" in yq.stay_open_words(True) and len(yq.stay_open_words(True)) <= yq.FIELD
    with data_root() as root:                    # the launcher learns it from the process it started ...
        h = Harness(root, spawn=Spawn(breakaway=False))
        assert h.go() == 0 and h.out[-2:] == [stay, FOLLOW], h.out
    with data_root() as root:                    # ... or from the worker's first journal line
        h = Harness(root)

        def started_inside(run, nonce):
            yq.journal(run, "started", launch=yq.word_hash(nonce)[:8], pid=os.getpid(), breakaway=False, text="Load started.")
            return None
        h.spawn.inline = started_inside
        assert h.go() == 0 and h.out[-2:] == [stay, FOLLOW], h.out
    for breakaway in (True, None):               # outside the job, or not known: nothing more is said
        with data_root() as root:
            h = Harness(root, spawn=Spawn(breakaway=breakaway))
            assert h.go() == 0 and h.out == [FOLLOW], h.out
    # the worker's side: the launcher tells it through the environment how it was started
    for value, want in (("0", False), ("1", True), (None, None)):
        with data_root() as root:
            h = Harness(root)
            assert h.go() == 0
            os.environ.pop(yq.BREAKAWAY_ENV, None)
            if value is not None:
                os.environ[yq.BREAKAWAY_ENV] = value
            try:
                assert h.work() == 0
                assert yq.BREAKAWAY_ENV not in os.environ
            finally:
                os.environ.pop(yq.BREAKAWAY_ENV, None)
            row = h.rows("started")[0]
            assert row.get("breakaway") is want, row
            if want is False:                    # `load status` prints text, why and next of every row
                assert row["text"] == "Load started." and row["next"] == stay
            else:
                assert "next" not in row
    with data_root() as root:                    # a resume inside the job: both sentences, each within the status line
        h = Harness(root)
        interrupted(h, upto="wait_tick")
        assert h.go() == 0
        os.environ[yq.BREAKAWAY_ENV] = "0"
        try:
            assert h.work() == 0
        finally:
            os.environ.pop(yq.BREAKAWAY_ENV, None)
        row = h.rows("started")[-1]
        assert "it had stopped at wait_tick" in row["text"] and row["next"] == stay and row["breakaway"] is False


def files_text(root: Path) -> dict:
    """Every file under the data root, as text (what anybody could read off the disk)."""
    return {str(p.relative_to(root)): p.read_text(encoding="utf-8", errors="replace") for p in root.rglob("*") if p.is_file()}


@test("the worker runs only when the launcher started it: no word, a wrong one, or another parent loads nothing")
def _():
    me = os.getpid()
    with data_root() as root:
        h = Harness(root)
        buf = io.StringIO()
        real, sys.stdout = sys.stdout, buf
        try:
            assert yq.worker(RUN, deps=h.deps, nonce="", parent_pid=me) == 2      # typed by hand, no lock at all
            assert h.go() == 0
            lock_text = paths.lock_file().read_text(encoding="utf-8")
            assert yq.worker(RUN, deps=h.deps, nonce="", parent_pid=me) == 2
            assert yq.worker(RUN, deps=h.deps, nonce="guess", parent_pid=me) == 2
            assert yq.worker("020126_0900", deps=h.deps, nonce=h.spawn.nonce, parent_pid=me) == 2
            # the right word, but not started by the process that holds the lock (the launcher)
            assert yq.worker(RUN, deps=h.deps, nonce=h.spawn.nonce, parent_pid=DEAD_PID) == 2
            assert os.getppid() != me and yq.worker(RUN, deps=h.deps, nonce=h.spawn.nonce) == 2    # the real os.getppid()
            for value in status.read_lock().values():                             # anything the lock holds, as the word
                assert yq.worker(RUN, deps=h.deps, nonce=str(value), parent_pid=me) == 2, value
            os.environ.pop(yq.NONCE_ENV, None)
            assert yq.main(["--worker", RUN]) == 2
            os.environ[yq.NONCE_ENV] = h.spawn.sha                                # what a reader of the lock could set
            try:
                assert yq.main(["--worker", RUN]) == 2
                assert yq.NONCE_ENV not in os.environ, "the worker left the word in its environment"
            finally:
                os.environ.pop(yq.NONCE_ENV, None)
        finally:
            sys.stdout = real
        said = buf.getvalue()
        assert "internal worker" in said and h.runner.calls == [] and loadplan.journal_read(RUN) == []
        assert "(refused: the lock is held by process" in said                    # the worker log says which rule refused
        assert paths.lock_file().read_text(encoding="utf-8") == lock_text         # the launcher's lock is untouched
        assert h.work() == 0                                                      # ... and its own worker still runs


@test("the launcher's word is in no file: the lock and the journal keep its hash, in letters only")
def _():
    with data_root() as root:
        h = Harness(root)
        assert h.go() == 0
        word, sha = h.spawn.nonce, h.spawn.sha
        lock = status.read_lock()
        assert "nonce" not in lock and lock["word_sha"] == sha == yq.word_hash(word) and sha != word
        assert len(sha) == 64 and sha.isalpha() and sha.islower(), "a hash with digits could look like an ID number"
        assert yq.word_hash(word) != yq.word_hash(word + "x") and yq.word_hash("a") == yq.word_hash("a")
        assert all(word not in text for text in files_text(root).values()), "the word is on the disk before the worker starts"
        seen = {}
        h.runner.hooks["refresh"] = lambda: seen.update(lock=paths.lock_file().read_text(encoding="utf-8"))
        assert h.work() == 0
        assert word not in seen["lock"] and sha in seen["lock"]                   # the worker's lock, after the hand-over
        on_disk = files_text(root)
        assert all(word not in text for text in on_disk.values()), [n for n, t in on_disk.items() if word in t]
        assert all(word not in line for line in h.w.log), "the word is in the worker log"
        assert {r["launch"] for r in h.rows() if "launch" in r} == {sha[:8]}
    with data_root():                            # a lock read back from an older file never carries a word on
        lk = yq.LockFile(RUN, "sha", {"nonce": "a word in clear", "step": "x"})
        lk.create()
        assert "nonce" not in status.read_lock() and status.read_lock()["word_sha"] == "sha"
        assert lk.release() is True


@test("no worker from a lock read off the disk: not from an interrupted load, not beside a running one")
def _():
    me = os.getpid()
    with data_root() as root:                    # an interrupted load: its dead lock starts nothing, with or without a box
        h = Harness(root)
        interrupted(h, upto="refresh")
        held = status.read_lock()
        buf = io.StringIO()
        real, sys.stdout = sys.stdout, buf
        try:
            for value in list(held.values()) + ["old-word", ""]:
                for parent in (me, DEAD_PID):
                    assert yq.worker(RUN, deps=h.deps, nonce=str(value), parent_pid=parent) == 2, (value, parent)
            # even somebody who knew the word of that launch: the launcher that held the lock is gone
            paths.write_text(paths.lock_file(), json.dumps(dict(held, word_sha=yq.word_hash("the word of that launch"))))
            assert yq.worker(RUN, deps=h.deps, nonce="the word of that launch", parent_pid=me) == 2
        finally:
            sys.stdout = real
        assert f"(refused: the lock is held by process {DEAD_PID}; this worker was started by process {me})" in buf.getvalue()
        assert h.box.shown == [] and h.runner.calls == [] and loadplan.run_result(RUN) == "RUNNING"
        assert status.read_lock()["pid"] == DEAD_PID and h.rows("failed") == []
    with data_root() as root:                    # a running load: a second worker beside the first
        h = Harness(root)
        assert h.go() == 0
        seen = {}

        def second_worker() -> None:             # while the first worker is inside its refresh
            if seen:
                return
            seen["codes"] = []
            runner2 = Runner(h.w)
            deps2 = yq.Deps(**{**h.deps.__dict__, "runner": runner2})
            lock = status.read_lock()            # after the hand-over: it names the first worker
            for value in lock.values():
                seen["codes"].append(yq.worker(RUN, deps=deps2, nonce=str(value), parent_pid=me))
            # the launcher's own word, from a process the first worker did not start
            seen["codes"].append(yq.worker(RUN, deps=deps2, nonce=h.spawn.nonce, parent_pid=DEAD_PID))
            seen["codes"].append(yq.worker(RUN, deps=deps2, nonce=h.spawn.nonce))
            seen["steps"] = runner2.labels()
        h.runner.hooks["refresh"] = second_worker
        buf = io.StringIO()
        real, sys.stdout = sys.stdout, buf
        try:
            assert h.work() == 0
        finally:
            sys.stdout = real
        assert seen["codes"] and set(seen["codes"]) == {2} and seen["steps"] == []
        assert h.runner.labels().count("refresh") == 1 and len(h.rows("started")) == 1
        assert loadplan.run_result(RUN) == "FINISHED"


# ── the worker ────────────────────────────────────────────────────────────────────────────────

@test("the steps run in loadplan.STEPS order, each journalled start then ok with its seconds")
def _():
    with data_root() as root:
        h = Harness(root)
        assert h.load() == 0
        assert [r["step"] for r in h.rows("step_start")] == list(loadplan.STEPS)
        assert [r["step"] for r in h.rows("step_ok")] == list(loadplan.STEPS)
        assert all(isinstance(r["seconds"], (int, float)) and r.get("text") for r in h.rows("step_ok"))
        assert h.runner.labels() == ["backup", "pre_gate", "refresh", "reconcile", "post_gate", "compare"]
        events = [r["event"] for r in h.rows()]
        assert events[0] == "started" and events[-1] == "finished" and "failed" not in events
        assert h.rows("started")[0]["launch"] == h.spawn.sha[:8] == h.rows("finished")[0]["launch"]
        assert "next" not in h.rows("started")[0] and "check_again" not in h.rows("finished")[0]
        assert loadplan.run_result(RUN) == "FINISHED" and h.rows("finished")[0]["result"] == "ok"
        assert not paths.lock_file().exists()
        # what a resume reads back from the journal
        assert Path(h.rows("step_ok", "backup")[0]["archive"]).is_file() and h.rows("step_ok", "backup")[0]["encrypted"] is True
        assert Path(h.rows("step_ok", "pre_gate")[0]["path"]).is_file()
        # every child is python -X utf8 with a fixed program; only the refresh takes no time limit
        for label, argv, timeout in h.runner.calls:
            assert argv[:3] == [sys.executable, "-X", "utf8"], argv
            assert (timeout is None) == (label == "refresh"), (label, timeout)
        assert h.runner.argv("backup")[3:7] == ["-m", "scripts.db_backup", "--all", "--out"]
        assert h.runner.argv("pre_gate")[3:] == ["-m", "scripts.prod_gate", "snapshot", "--label", "pre-data-010126"]
        assert h.runner.argv("post_gate")[3:] == ["-m", "scripts.prod_gate", "snapshot", "--label", "post-data-010126"]
        assert h.runner.argv("refresh")[3:] == ["-c", yq.REFRESH_PROGRAM, str(h.w.staging)]
        assert h.runner.argv("reconcile")[3:] == ["-m", "scripts.reconcile_check", str(h.w.staging)]
        cmp_argv = h.runner.argv("compare")
        assert cmp_argv[3:6] == ["-m", "scripts.prod_gate", "compare"]
        assert cmp_argv[6].endswith("_pre-data-010126.json") and cmp_argv[7].endswith("_post-data-010126.json")


@test("the backup is the first thing run, packed outside the data root, plain folder removed, newest N kept")
def _():
    with data_root() as root:
        h = Harness(root)
        base = root / "backups-out" / "preload"
        base.mkdir(parents=True)
        for i, name in enumerate(["yq-backup_2025-12-01_pre-data-011225_0900.7z", "yq-backup_2025-12-02_pre-data-021225_0900.zip",
                                  "yq-backup_2025-12-03_pre-data-031225_0900.7z"]):
            (base / name).write_bytes(b"old")
            os.utime(base / name, (1_700_000_000 + i, 1_700_000_000 + i))
        (base / "yq-backup_2025-12-03_0230.7z").write_bytes(b"a nightly archive: not ours to prune")
        (base / "notes.txt").write_text("keep", encoding="utf-8")
        assert h.load() == 0
        runs = [t for t in h.w.trace if t.startswith("run:")]
        assert runs[0] == "run:backup"
        assert h.w.trace.index("run:backup") < h.w.trace.index("archive") < h.w.trace.index("run:pre_gate") < h.w.trace.index("run:refresh")
        out = Path(h.runner.argv("backup")[-1])
        assert out == base / "2026-01-01_pre-data-010126" and not out.exists(), "the plain folder is still there"
        mine = base / "yq-backup_2026-01-01_pre-data-010126_0900.7z"
        assert mine.is_file()
        left = sorted(p.name for p in base.iterdir())
        assert left == ["notes.txt", "yq-backup_2025-12-03_0230.7z", "yq-backup_2025-12-03_pre-data-031225_0900.7z", mine.name], left
        row = h.rows("step_ok", "backup")[0]
        assert row["kept"] == 2 and row["pruned"] == 2 and "packed, encrypted and read back: yq-backup_2026-01-01" in row["text"]
    with data_root() as root:                    # no password: the journal says UNENCRYPTED
        h = Harness(root)
        h.deps.archive = lambda folder, dest, stamp: (h.w.archive(folder, dest, stamp)[0], False)
        assert h.load() == 0 and "UNENCRYPTED" in h.rows("step_ok", "backup")[0]["text"]
        assert h.rows("step_ok", "backup")[0]["encrypted"] is False


@test("the refresh never runs when the backup fails: stop, nothing written, the run is closed")
def _():
    bad = [yq.Child(1, "Traceback ...\nSystemExit: DATABASE_URL missing\n"),
           yq.Child(0, "  orders 1 rows\n"),                                             # exit 0 but no marker
           yq.Child(0, "Backup complete: x  (1 tables, git abc, verify FAILED on 1 table(s))\n"),
           yq.Child(1, "Backup complete: x  (1 tables, git abc, verify OK)\n")]          # the marker but exit 1
    for child in bad:
        with data_root() as root:
            h = Harness(root)

            def half_written(argv, child=child):
                out = Path(argv[argv.index("--out") + 1])
                out.mkdir(parents=True)
                (out / "customers.csv").write_text("phone\n", encoding="utf-8")
                return child
            h.runner.script["backup"] = [half_written]
            assert h.go() == 0 and h.work() == 1
            assert h.runner.labels() == ["backup"], h.runner.labels()
            assert "archive" not in h.w.trace
            failed = h.rows("failed")[0]
            assert failed["written"] is False and failed["step"] == "backup" and failed["result"] == "failed"
            assert failed["text"] == "LOAD STOPPED at backup. Nothing was written to the database."
            assert "The backup did not complete (exit code" in failed["why"] and failed["next"] == "Type /yq-load to check again."
            assert h.rows("step_failed", "backup")[0]["text"] == failed["why"]      # the step's own row says the same
            assert not (root / "backups-out" / "preload" / "2026-01-01_pre-data-010126").exists(), "plain CSVs left behind"
            assert not paths.lock_file().exists() and loadplan.run_result(RUN) == "FAILED"
            assert not paths.state_file().exists()
            h.out.clear()
            assert h.go() == 2 and "refused for good" in h.said()
    with data_root() as root:                    # the archive step fails: the plain folder goes, nothing loads
        h = Harness(root)

        def no_pack(folder, dest, stamp):
            raise SystemExit("7-Zip failed (exit 2)")
        h.deps.archive = no_pack
        assert h.go() == 0 and h.work() == 1 and h.runner.labels() == ["backup"]
        assert "could not be packed" in whole(h.rows("failed")[0]) and "7-Zip failed" in h.rows("failed")[0]["why"]
        assert not (root / "backups-out" / "preload" / "2026-01-01_pre-data-010126").exists()
    with data_root() as root:                    # a backup folder inside a synced place is refused by its own guard
        h = Harness(root)

        def synced():
            raise SystemExit("REFUSING: the folder is inside a synced folder")
        h.deps.backup_root = synced
        assert h.go() == 0 and h.work() == 1 and h.runner.calls == []
        assert h.rows("failed")[0]["written"] is False


@test("the refresh never runs when the before-snapshot is not written")
def _():
    for child in (yq.Child(1, "Traceback ...\nKeyError: 'DATABASE_URL'\n"), yq.Child(0, "snapshot nothing_pre-data-010126.json: x\n")):
        with data_root() as root:
            h = Harness(root)
            h.runner.script["pre_gate"] = [child]
            assert h.go() == 0 and h.work() == 1
            assert h.runner.labels() == ["backup", "pre_gate"]
            failed = h.rows("failed")[0]
            assert failed["written"] is False and "LOAD STOPPED at pre_gate" in failed["text"]
            assert not paths.lock_file().exists() and loadplan.run_result(RUN) == "FAILED"
            assert h.rows("step_ok", "backup"), "the backup that was taken stays journalled"
    with data_root() as root:                    # written, but its program reports FK orphans with exit 1: a note
        h = Harness(root)
        h.runner.script["pre_gate"] = [lambda argv: yq.Child(1, h.runner.ok_pre_gate(argv).text)]
        assert h.load() == 0
        assert "to check" in h.rows("step_ok", "pre_gate")[0]["text"] and h.rows("step_ok", "pre_gate")[0]["exit_code"] == 1
    with data_root() as root:                    # a file name with a path in it is never followed
        h = Harness(root)
        h.runner.script["pre_gate"] = [yq.Child(0, "snapshot ..\\..\\elsewhere.json: x\n")]
        assert h.go() == 0 and h.work() == 1 and h.runner.labels() == ["backup", "pre_gate"]
    with data_root() as root:                    # ... not even to a file that is there and reads like a snapshot
        h = Harness(root)                        # (forward slashes: a path on every system)
        (root / "business_data" / "gates").mkdir(parents=True, exist_ok=True)
        (root / "elsewhere.json").write_text(json.dumps({"tables": {}}), encoding="utf-8")
        h.runner.script["pre_gate"] = [yq.Child(0, "snapshot ../../elsewhere.json: x\n")]
        assert h.go() == 0 and h.work() == 1 and h.runner.labels() == ["backup", "pre_gate"]
        assert h.rows("failed")[0]["why"].startswith("The before-snapshot was not written"), h.rows("failed")[0]


@test("the token is checked a second time just before the refresh; a change there is 'check again', nothing written")
def _():
    with data_root() as root:
        h = Harness(root)
        assert h.load() == 0
        t = h.w.trace
        checks = [i for i, x in enumerate(t) if x.startswith("token:")]
        assert len(checks) == 3 and t[checks[0]] == "token:with fingerprint"          # launcher, token step, before the write
        assert checks[1] < t.index("run:backup") and t.index("run:pre_gate") < checks[2] < t.index("run:refresh")
        assert t[checks[2] + 1] == "runs:None" and t[checks[2] + 2] == "run:refresh", t[checks[2]:checks[2] + 4]
    with data_root() as root:
        h = Harness(root)
        h.runner.hooks["pre_gate"] = lambda: h.w.fingerprint.update(orders=[11, 100])   # a portal upload meanwhile
        assert h.go() == 0 and h.work() == 3
        assert h.runner.labels() == ["backup", "pre_gate"] and "run:refresh" not in h.w.trace
        failed = h.rows("failed")[0]
        assert failed["result"] == "check_again" and failed["written"] is False and failed["step"] == "wait_tick"
        assert "CHECK AGAIN at wait_tick" in failed["text"] and "the database table orders" in failed["why"]
        assert "Nothing was written" in failed["text"] and failed["moved"] == ["the database table orders"]
        assert failed["next"] == "Type /yq-load to check again." and failed["check_again"] is True
        assert not paths.lock_file().exists()
        assert Path(h.rows("step_ok", "backup")[0]["archive"]).is_file(), "a backup already taken is kept"
        assert not paths.state_file().exists() and not paths.published_dir(datetime(2026, 1, 1).date()).exists()
    with data_root() as root:                    # the same at the worker's first step (the box sat open for minutes)
        h = Harness(root)
        assert h.go() == 0
        (h.w.staging / "Sales_day_book1.xlsx").write_bytes(b"swapped")
        assert h.work() == 3 and h.runner.calls == []
        assert h.rows("failed")[0]["step"] == "token" and "the staged copies: Sales_day_book1.xlsx" in h.rows("failed")[0]["why"]


@test("data/clean is emptied before anything runs; a file that survives stops the load")
def _():
    with data_root() as root:
        h = Harness(root)
        clean = root / "data" / "clean"
        (clean / "sub").mkdir(parents=True)
        (clean / "selling_prices.csv").write_text("an old price book\n", encoding="utf-8")
        (clean / "sub" / "x.csv").write_text("x\n", encoding="utf-8")
        seen = {}
        h.runner.hooks["backup"] = lambda: seen.update(left=list(clean.rglob("*")) if clean.exists() else [])
        assert h.load() == 0 and seen["left"] == []
        assert "The loader's work folder (data/clean) is empty." in h.rows("step_ok", "token")[0]["text"]
    with data_root() as root:
        h = Harness(root)
        clean = root / "data" / "clean"
        clean.mkdir(parents=True)
        (clean / "selling_prices.csv").write_text("held open by another program\n", encoding="utf-8")
        real = yq.shutil.rmtree
        yq.shutil.rmtree = lambda p, **kw: None if Path(p) == clean else real(p, **kw)
        try:
            assert h.go() == 0 and h.work() == 1
        finally:
            yq.shutil.rmtree = real
        assert h.runner.calls == [] and "selling_prices.csv" in h.rows("failed")[0]["why"]
        assert h.rows("failed")[0]["written"] is False and h.clock.sleeps == [1, 1, 1, 1]


@test("the tick rule: a start 1 to 9 minutes after a quarter-hour, a wait otherwise, never 07:50 to 08:20 Bahrain")
def _():
    def at(h, m, s=0):
        return datetime(2026, 1, 1, h, m, s, tzinfo=timezone.utc)

    for h_, m, s, want in ((9, 1, 0, 0), (9, 3, 0, 0), (9, 5, 0, 0), (9, 16, 0, 0), (9, 50, 0, 0), (9, 0, 0, 60),
                           (9, 0, 30, 30), (9, 5, 1, 0), (9, 7, 0, 0), (9, 9, 0, 0), (9, 9, 1, 419), (9, 10, 0, 360),
                           (9, 14, 59, 61), (9, 59, 59, 61), (4, 49, 0, 0), (4, 46, 0, 0), (4, 50, 0, 1801),
                           (5, 0, 0, 1201), (5, 20, 0, 1), (5, 20, 1, 0), (5, 31, 0, 0), (4, 36, 0, 0)):
        assert yq.tick_wait_seconds(at(h_, m, s)) == want, (h_, m, s, yq.tick_wait_seconds(at(h_, m, s)), want)
    # after a slow refresh the window closes earlier: at 300 s it is the rule of the first loads
    for h_, m, s, want in ((9, 5, 0, 0), (9, 5, 1, 659), (9, 7, 0, 540), (4, 50, 0, 2460), (5, 20, 1, 659)):
        assert yq.tick_wait_seconds(at(h_, m, s), 300) == want, (h_, m, s, want)

    def allowed(t) -> bool:
        local = t.astimezone(yq.BAHRAIN)
        quiet = (7, 50, 0) <= (local.hour, local.minute, local.second) <= (8, 20, 0)
        into = (t.minute % 15) * 60 + t.second
        return 60 <= into <= 540 and not quiet

    t = at(0, 0)
    for _ in range(24 * 60 * 2):                 # every half minute of a day
        wait = yq.tick_wait_seconds(t)
        assert (wait == 0) == allowed(t), t
        assert allowed(t + timedelta(seconds=wait)), (t, wait)
        assert wait <= 31 * 60, (t, wait)
        t += timedelta(seconds=30)
    assert yq.tick_wait_seconds(datetime(2026, 1, 1, 12, 3, tzinfo=yq.BAHRAIN)) == 0      # any time zone is read right


@test("the box says when a load that must wait will write, for a quick or a slow backup: at 07:46 and 07:55 Bahrain about 08:20 (the morning window), at 07:31 nothing")
def _():
    def at(h, m, s=0):
        return datetime(2026, 1, 1, h, m, s, tzinfo=timezone.utc)

    assert yq.BEFORE_WRITE_S == (120, 360) and yq.BEFORE_WRITE_STEP_S == 30 and yq.WAIT_NOTE_S == 300
    assert yq.wait_note(at(4, 55)) == ("WAIT: a Yes now may not write until about 08:20 Bahrain (up to 25 min from now): "
                                       "no load writes from 07:50 to 08:20 Bahrain (production's 8 o'clock run).")
    # the review's case: after a quick backup a Yes at 07:46 would not wait, after a slow one it waits out the window
    assert yq.wait_note(at(4, 46)) == ("WAIT: a Yes now may not write until about 08:20 Bahrain (up to 34 min from now): "
                                       "no load writes from 07:50 to 08:20 Bahrain (production's 8 o'clock run).")
    assert yq.wait_note(at(4, 31)) is None and yq.wait_note(NOON) is None      # every end of the backup is in a start window
    # outside the morning window the tick rule alone can wait long enough to be told, with its own reason
    assert yq.wait_note(at(7, 8)) == ("WAIT: a Yes now may not write until about 10:16 Bahrain (up to 8 min from now): "
                                      "a load starts 1 to 9 minutes after production's quarter-hour tick.")
    assert yq.wait_note(at(7, 12)) is None                                     # 2 min at most: not worth a line
    assert "1 to 5 minutes" in yq.wait_note(at(7, 4), 300)                     # after a slow refresh the window is shorter
    with data_root() as root:                    # the box itself: the line sits with the other notes, before the end
        h = Harness(root, start=at(4, 55))
        assert h.go() == 0, h.out
        lines = h.box.shown[0][1].splitlines()
        assert yq.wait_note(at(4, 55)) in lines and lines[-1] == "Yes = load now. No = nothing changes.", lines
    with data_root() as root:
        h = Harness(root, start=at(4, 31))
        assert h.go() == 0, h.out
        assert "WAIT:" not in h.box.shown[0][1]


@test("the start window closes earlier after a slow refresh, never later than 9 minutes nor earlier than 5")
def _():
    assert yq.start_until_s(None) == 540 == yq.START_UNTIL_S                 # nothing on record: 240 s assumed
    assert yq.start_until_s(167) == 540                                      # the refreshes of the first loads
    assert yq.start_until_s(300) == 405                                      # 900 - 120 - 1.25 x 300
    assert yq.start_until_s(500) == 300 == yq.START_UNTIL_MIN_S              # very slow: the first rule, never less
    assert yq.start_until_s("junk") == 540 and yq.start_until_s(-5) == 540
    # a refresh that starts at the last allowed second ends TICK_MARGIN_S before the next tick at least
    for took in (60, 167, 240, 300, 400):
        until = yq.start_until_s(took)
        assert until + took + yq.TICK_MARGIN_S <= yq.TICK_S or until == yq.START_UNTIL_MIN_S, (took, until)
    assert yq.last_refresh_seconds(lambda: {"last_load": {"refresh_seconds": 155.1}}) == 155.1
    assert yq.last_refresh_seconds(lambda: {"last_load": {"refresh_seconds": True}}) is None
    assert yq.last_refresh_seconds(lambda: {}) is None

    def broken():
        raise OSError("held")
    assert yq.last_refresh_seconds(broken) is None
    with data_root() as root:                    # the load reads the window from the last load's refresh time
        h = Harness(root, start=datetime(2026, 1, 1, 9, 6, 0, tzinfo=timezone.utc))
        h.deps.read_state = lambda: {"last_load": {"refresh_seconds": 500}}
        assert h.load() == 0
        row = h.rows("step_ok", "wait_tick")[0]
        assert row["until_s"] == 300 and row["waited_seconds"] == 600, row          # 09:06 is past 5 minutes: 09:16
    # the slowest of the last refreshes counts, not the last one alone: one quick day never widens the window
    recent = {"last_load": {"refresh_seconds": 150, "refresh_seconds_recent": [160, 420, 150]}}
    assert yq.last_refresh_seconds(lambda: recent) == 420 and yq.start_until_s(420) == yq.START_UNTIL_MIN_S
    assert yq.last_refresh_seconds(lambda: {"last_load": {"refresh_seconds_recent": "junk", "refresh_seconds": 9}}) == 9
    with data_root() as root:                    # publish keeps the last REFRESH_RECENT refresh times
        h = Harness(root)
        before = {"last_load": {"run": "301225_0900", "refresh_seconds_recent": [100, 110, 120, 130, 140]}}
        status.write_state(before)
        h.deps.read_state = status.read_state
        assert h.load() == 0
        kept = status.read_state()["last_load"]["refresh_seconds_recent"]
        assert len(kept) == yq.REFRESH_RECENT == 5 and kept[:4] == [110, 120, 130, 140], kept


@test("the worker waits for the quiet moment and checks the token again after the wait")
def _():
    with data_root() as root:
        h = Harness(root, start=datetime(2026, 1, 1, 9, 10, 0, tzinfo=timezone.utc))
        assert h.load() == 0
        assert sum(h.clock.sleeps) == 360 and max(h.clock.sleeps) <= 30
        assert h.clock.now() == datetime(2026, 1, 1, 9, 16, 0, tzinfo=timezone.utc)
        row = h.rows("step_ok", "wait_tick")[0]
        assert row["waited_seconds"] == 360 and row["seconds"] == 360.0 and "the table still holds" in row["text"]
        assert row["checks"] == 2 and row["until_s"] == 540
        note = h.rows("note", "wait_tick")
        assert note and "07:50 to 08:20 Bahrain" in note[0]["text"] and "1 to 9 minutes" in note[0]["text"]
        assert note[0]["text"].startswith("Waiting about 6 min, to write at about 12:16: "), note[0]["text"]
        t = h.w.trace
        checks = [i for i, x in enumerate(t) if x.startswith("token:")]
        assert len(checks) == 4 and checks[3] == t.index("run:refresh") - 2          # ... token, runs:None, run:refresh
    with data_root() as root:                    # no wait needed: one check, and the write starts in the quiet moment
        h = Harness(root)
        assert h.load() == 0 and h.clock.sleeps == []
        assert h.rows("step_ok", "wait_tick")[0]["checks"] == 1 and h.rows("step_ok", "wait_tick")[0]["waited_seconds"] == 0
    with data_root() as root:                    # something changes during the wait: nothing is loaded
        h = Harness(root, start=datetime(2026, 1, 1, 9, 10, 0, tzinfo=timezone.utc))
        h.clock.on_sleep = lambda: h.w.fingerprint.update(stock_balance=[5, 5])
        assert h.go() == 0 and h.work() == 3
        assert "run:refresh" not in h.w.trace and h.rows("failed")[0]["result"] == "check_again"
    with data_root() as root:                    # the morning window: the first second after 08:20 Bahrain
        h = Harness(root, start=datetime(2026, 1, 1, 4, 55, 0, tzinfo=timezone.utc))
        assert h.load() == 0 and h.clock.now() == datetime(2026, 1, 1, 5, 20, 1, tzinfo=timezone.utc)
        assert "Waiting about 25 min, to write at about 08:20: " in h.rows("note", "wait_tick")[0]["text"]


def slow_checks(h: Harness, slow: dict) -> None:
    """Make the token check number n of this run (the launcher's is 1) take slow[n] seconds: the live
    site or the database answering late."""
    real, seen = h.w.collect_parts, {"n": 0}

    def collect(plan, with_fingerprint):
        seen["n"] += 1
        h.clock.t += timedelta(seconds=slow.get(seen["n"], 0))      # the time passes inside the check, not in a nap
        return real(plan, with_fingerprint)
    h.deps.collect_parts = collect


@test("a token check that outlasts the quiet moment: the next one is waited for and the token is checked again")
def _():
    def started_at(h: Harness) -> dict:
        seen = {}
        h.runner.hooks["refresh"] = lambda: seen.update(at=h.clock.now())
        return seen

    with data_root() as root:                    # the check after the wait takes ten minutes
        h = Harness(root, start=datetime(2026, 1, 1, 9, 10, 0, tzinfo=timezone.utc))
        slow_checks(h, {4: 600})                 # 1 launcher, 2 token step, 3 before the wait, 4 after it
        seen = started_at(h)
        assert h.load() == 0
        at = seen["at"]
        assert yq.tick_wait_seconds(at) == 0, f"the write began at {at}: {900 - (at.minute * 60 + at.second) % 900} s before a tick"
        assert at == datetime(2026, 1, 1, 9, 31, 0, tzinfo=timezone.utc)       # 09:16 + the slow check, then the next moment
        t = h.w.trace
        checks = [i for i, x in enumerate(t) if x.startswith("token:")]
        assert len(checks) == 5 and checks[4] == t.index("run:refresh") - 2, t
        row = h.rows("step_ok", "wait_tick")[0]
        assert row["checks"] == 3 and row["waited_seconds"] == 360 + 300 and "the table still holds" in row["text"]
    with data_root() as root:                    # the FIRST check of the step is the slow one: it ends in a quiet moment or waits
        h = Harness(root, start=datetime(2026, 1, 1, 9, 1, 0, tzinfo=timezone.utc))
        slow_checks(h, {3: 200})                 # 09:01:00 -> 09:04:20, still inside the quiet moment
        seen = started_at(h)
        assert h.load() == 0 and seen["at"] == datetime(2026, 1, 1, 9, 4, 20, tzinfo=timezone.utc)
        assert h.rows("step_ok", "wait_tick")[0]["checks"] == 1
    with data_root() as root:
        h = Harness(root, start=datetime(2026, 1, 1, 9, 8, 0, tzinfo=timezone.utc))
        slow_checks(h, {3: 200})                 # 09:08:00 -> 09:11:20: the moment is gone, the next is waited for
        seen = started_at(h)
        assert h.load() == 0 and seen["at"] == datetime(2026, 1, 1, 9, 16, 0, tzinfo=timezone.utc)
        assert yq.tick_wait_seconds(seen["at"]) == 0 and h.rows("step_ok", "wait_tick")[0]["checks"] == 2
    with data_root() as root:                    # something moved while a slow check ate the moment: found by the next check
        h = Harness(root, start=datetime(2026, 1, 1, 9, 10, 0, tzinfo=timezone.utc))
        slow_checks(h, {4: 600})
        h.clock.on_sleep = lambda: h.w.fingerprint.update(orders=[11, 100]) if h.clock.now().minute >= 26 else None
        assert h.go() == 0 and h.work() == 3 and "run:refresh" not in h.w.trace
    with data_root() as root:                    # every check too slow: the load gives up, nothing written
        h = Harness(root, start=datetime(2026, 1, 1, 9, 10, 0, tzinfo=timezone.utc))
        slow_checks(h, {n: 600 for n in range(4, 40)})
        assert h.go() == 0 and h.work() == 1
        assert "run:refresh" not in h.w.trace and h.runner.labels() == ["backup", "pre_gate"]
        failed = h.rows("failed")[0]
        assert failed["step"] == "wait_tick" and failed["written"] is False and failed["result"] == "failed"
        assert f"outlasted the quiet moment {yq.TICK_TRIES} times in a row" in failed["why"]
        assert failed["text"] == "LOAD STOPPED at wait_tick. Nothing was written to the database."
        assert len([x for x in h.w.trace if x.startswith("token:")]) == 3 + yq.TICK_TRIES
        assert not paths.lock_file().exists()


def utc(h, m, s=0) -> datetime:
    return datetime(2026, 1, 1, h, m, s, tzinfo=timezone.utc)


@test("the one-transaction rule: a refresh starts at any moment but one that could still run when the 8 o'clock agents begin, until they are done")
def _():
    need = yq.REFRESH_ASSUMED_S                  # nothing on record: a refresh is taken to last 4 minutes
    assert yq.refresh_need_s(None) == need and yq.refresh_need_s(300) == 375 and yq.refresh_need_s("junk") == need
    # the agents' window is 07:59:45 (the clocks' margin) to 08:20:00 Bahrain = 04:59:45 to 05:20:00 UTC
    for (h_, m, s), want in (((9, 0, 0), 0), ((9, 0, 30), 0), ((9, 10, 0), 0), ((9, 14, 59), 0), ((4, 49, 0), 0),
                             ((4, 55, 44), 0), ((4, 55, 45), 1456), ((4, 56, 0), 1441), ((5, 0, 0), 1201),
                             ((5, 19, 59), 2), ((5, 20, 0), 1), ((5, 20, 1), 0), ((5, 31, 0), 0), ((20, 59, 0), 0)):
        got = yq.agents_wait_seconds(utc(h_, m, s), need)
        assert got == want, (h_, m, s, got, want)
    assert yq.agents_wait_seconds(utc(4, 51, 25), 500) == 1716 and yq.agents_wait_seconds(utc(4, 51, 24), 500) == 0
    # the REST loader's rule is untouched, and the loader in use picks the rule
    assert yq.start_wait_seconds(utc(9, 10), 540) == yq.tick_wait_seconds(utc(9, 10), 540) == 360
    assert yq.start_wait_seconds(utc(9, 10), 540, one_transaction=True) == 0

    a, b = utc(4, 59, 45), utc(5, 20)

    def allowed(t) -> bool:
        return not (t <= b and t + timedelta(seconds=need) >= a)

    t = utc(0, 0)
    for _ in range(24 * 60 * 2):                 # every half minute of a day
        wait = yq.agents_wait_seconds(t, need)
        assert (wait == 0) == allowed(t), t
        assert allowed(t + timedelta(seconds=wait)), (t, wait)
        assert wait <= 25 * 60, (t, wait)
        t += timedelta(seconds=30)


@test("the moments the one commit keeps out of: each quarter-hour tick from 15 s before to 90 s after, and the 8 o'clock agents")
def _():
    from scripts.load_txn import hold_seconds, parse_avoid     # the loader's own reading of the list
    now = utc(9, 2)
    av = yq.commit_avoid(now)
    t0 = int(now.timestamp())
    assert av == sorted(av) and all(b - a == yq.COMMIT_CLEAR_BEFORE_S + yq.COMMIT_CLEAR_AFTER_S for a, b in av)
    ticks = [a + yq.COMMIT_CLEAR_BEFORE_S for a, _b in av]
    assert ticks[0] == t0 - 120 and all(x % yq.TICK_S == 0 for x in ticks) and ticks[-1] >= t0 + yq.COMMIT_AVOID_S - 900
    morning = yq.commit_avoid(utc(3, 30))       # 06:30 Bahrain: the agents' window lies inside the span
    agents = (int(utc(4, 59, 45).timestamp()), int(utc(5, 20).timestamp()))
    assert agents in morning and agents not in av
    spec = yq.avoid_spec(av)
    assert parse_avoid(spec) == (av, None)
    # what the loader then does: a commit at 09:00:30 waits to 09:01:30, one at 08:59:50 too, one at 09:02 at once
    assert hold_seconds(utc(9, 0, 30).timestamp(), av) == 60 and hold_seconds(utc(8, 59, 50).timestamp(), av) == 100
    assert hold_seconds(utc(9, 2).timestamp(), av) == 0
    # inside the agents' window it waits to its end (the start rule keeps a refresh from getting there)
    assert hold_seconds(utc(5, 0, 30).timestamp(), morning) == 19 * 60 + 30


@test("the worker with the one-transaction loader: no tick wait, the refresh child told so with the moments to keep out of, the agents waited out")
def _():
    with data_root() as root:                    # 09:10: the REST loader would wait six minutes; this one starts at once
        h = Harness(root, start=utc(9, 10))
        h.deps.one_transaction = True
        assert h.load() == 0 and h.clock.sleeps == []
        row = h.rows("step_ok", "wait_tick")[0]
        assert row["text"] == ("No wait needed: the load writes in one transaction, at a moment clear of production's "
                               "quarter-hour jobs. The table still holds."), row["text"]
        assert row["one_transaction"] is True and row["waited_seconds"] == 0 and row["checks"] == 1 and "until_s" not in row
        argv = h.runner.argv("refresh")
        assert argv[3:6] == ["-c", yq.REFRESH_PROGRAM, str(h.w.staging)] and argv[6] == yq.ONE_TXN_ARG
        assert argv[7] == yq.avoid_spec(yq.commit_avoid(utc(9, 10))), "the list is made when the child starts"
        assert h.rows("finished")[0]["result"] == "ok"
    with data_root() as root:                    # 07:56 Bahrain: a refresh now could straddle the agents: after 08:20
        h = Harness(root, start=utc(4, 56))
        h.deps.one_transaction = True
        assert h.load() == 0 and h.clock.now() == utc(5, 20, 1)
        assert h.rows("note", "wait_tick")[0]["text"] == (
            "Waiting about 24 min, to write at about 08:20: production's 8 o'clock agents run from 08:00 to 08:20 Bahrain "
            "and no load writes meanwhile.")
        row = h.rows("step_ok", "wait_tick")[0]
        assert row["text"] == "Waited 24.0 min for production's 8 o'clock agents; the table still holds." and row["checks"] == 2
        assert row["waited_seconds"] == 1441 and yq.agents_wait_seconds(utc(5, 20, 1)) == 0
    with data_root() as root:                    # 07:50: a refresh started now is over before the agents begin
        h = Harness(root, start=utc(4, 50))
        h.deps.one_transaction = True
        assert h.load() == 0 and h.clock.sleeps == []
    with data_root() as root:                    # a slow refresh on record starts the margin before 8 earlier
        h = Harness(root, start=utc(4, 52))
        h.deps.one_transaction = True
        h.deps.read_state = lambda: {"last_load": {"refresh_seconds_recent": [90, 400]}}       # 1.25 x 400 = 500 s
        assert h.load() == 0 and h.clock.now() == utc(5, 20, 1)
        assert h.rows("step_ok", "wait_tick")[0]["need_s"] == 500
    with data_root() as root:                    # a rehearsal says what a real one-transaction load would wait for
        h = Harness(root, start=utc(4, 56))
        h.deps.one_transaction, h.deps.wait_for_tick = True, False
        assert h.load() == 0
        assert h.rows("step_ok", "wait_tick")[0]["text"] == ("A real load would wait 1441 s here for production's "
                                                             "8 o'clock agents; a rehearsal does not.")


@test("the one-transaction re-run: a failed load stage left nothing, so the second run waits by the same rule; the box's WAIT line names the agents")
def _():
    def stage_fails(h, minutes):
        def answer(argv):
            h.clock.t += timedelta(minutes=minutes)
            h.w.newest_run += 1                  # refresh's own error row
            return yq.Child(2, refresh_line(ok=False, verify_ok=None, error="The load step failed.") + "\n")
        return answer

    with data_root() as root:                    # the first run fails at 07:56: the second waits for the agents
        h = Harness(root, start=utc(4, 50))
        h.deps.one_transaction = True
        starts = []
        h.runner.hooks["refresh"] = lambda: starts.append(h.clock.now())
        h.runner.script["refresh"] = [stage_fails(h, 6)]
        assert h.load() == 0, h.rows()[-1]
        assert starts == [utc(4, 50), utc(5, 20, 1)], starts
        assert [n["text"] for n in h.rows("note", "refresh")] == [
            "Waited 23.5 min for production's 8 o'clock agents before the second run."]
        first, second = [c[1] for c in h.runner.calls if c[0] == "refresh"]
        assert first[:7] == second[:7] and first[7] != second[7], "the same load; the list moves with the clock"
    with data_root() as root:                    # at noon there is nothing to wait for
        h = Harness(root)
        h.deps.one_transaction = True
        h.runner.script["refresh"] = [stage_fails(h, 1)]
        assert h.load() == 0 and h.rows("note", "refresh") == []
    # the box: before 8 o'clock only, and the agents named
    line = yq.wait_note(utc(4, 55), one_transaction=True)
    assert line == ("WAIT: a Yes now may not write until about 08:20 Bahrain (up to 25 min from now): no load writes "
                    "while production's 8 o'clock agents run (08:00 to 08:20)."), line
    assert len(line) <= 160
    assert yq.wait_note(utc(4, 46), one_transaction=True) is None       # every end of the backup still writes before 8
    assert yq.wait_note(utc(4, 50), one_transaction=True) is not None   # a slow backup would end too close to 8
    assert yq.wait_note(utc(7, 8), one_transaction=True) is None        # a quarter-hour tick is no wait for this loader
    assert yq.wait_note(utc(7, 8)) is not None                          # ... for the REST loader it still is
    with data_root() as root:                    # the box itself takes the rule of the loader in use
        h = Harness(root, start=utc(4, 55))
        h.deps.one_transaction = True
        assert h.go() == 0, h.out
        assert line in h.box.shown[0][1].splitlines()
    with data_root() as root:
        h = Harness(root, start=utc(7, 8))
        h.deps.one_transaction = True
        assert h.go() == 0 and "WAIT:" not in h.box.shown[0][1]


@test("a load-stage failure is run once more, identically; ingest_runs is counted for the attempt that passed")
def _():
    def stage_fails(h):
        def answer(argv):
            h.w.newest_run += 1                                   # refresh's own error row
            return yq.Child(2, "=== Data refresh - needs attention ===\n"
                            + refresh_line(ok=False, verify_ok=None, error="The load step failed.", loaded={"Sales_day_book": 12}) + "\n")
        return answer

    with data_root() as root:
        h = Harness(root)
        h.runner.script["refresh"] = [stage_fails(h)]
        assert h.load() == 0
        calls = [c for c in h.runner.calls if c[0] == "refresh"]
        assert len(calls) == 2 and calls[0][1] == calls[1][1], "the re-run is not identical"
        assert yq.RETRY_PAUSE_S in h.clock.sleeps
        first = h.rows("step_failed", "refresh")[0]
        assert first["attempt"] == 1 and first["stops"] is False and "runs once more" in first["text"]
        assert first["why"] == "The load step failed."
        ok = h.rows("step_ok", "refresh")[0]
        assert ok["attempt"] == 2 and ok["ingest_runs"] == [8, 10] and ok["new_runs"] == 2 and ok["data_as_of"] == "2025-12-31"
        assert "second run" in ok["text"] and loadplan.run_result(RUN) == "FINISHED"
        assert h.w.trace.count("runs:None") == 2 and "runs:8" in h.w.trace            # read before EACH attempt
    with data_root() as root:                    # twice: closed, and the journal says the database may hold part of it
        h = Harness(root)
        h.runner.script["refresh"] = [stage_fails(h), yq.Child(1, "Traceback (most recent call last):\nMemoryError\n")]
        assert h.go() == 0 and h.work() == 1
        assert h.runner.labels() == ["backup", "pre_gate", "refresh", "refresh"]
        failed = h.rows("failed")[0]
        assert failed["written"] is True and "The load stage failed twice" in failed["why"] and "MemoryError" in failed["why"]
        assert failed["text"] == "LOAD FAILED at refresh. The database holds this load, or part of it."
        assert failed["next"] == "This run is closed: type /yq-load for a new check."
        assert h.rows("step_start", "refresh")[1]["text"] == "The identical load runs once more."
        assert not paths.lock_file().exists() and not paths.state_file().exists()
        assert not paths.published_dir(datetime(2026, 1, 1).date()).exists()
        h.out.clear()
        assert h.go() == 2 and "refused for good" in h.said()


@test("a refresh that verified but is not clean is closed FAILED: a loader notice, or ingest_runs not grown by exactly 2")
def _():
    def grew(h, n, **changes):
        def answer(argv):
            h.w.newest_run += n
            return yq.Child(0, refresh_line(**changes) + "\n")
        return answer

    for n, changes, words in ((3, {}, "another load may have run at the same time"), (1, {}, "only 1 of its 2"),
                              (0, {}, "only 0 of its 2"),
                              (2, {"loader_notices": ["span replace skipped: 4 invoices would vanish"]}, "the loader reported: span replace skipped")):
        with data_root() as root:
            h = Harness(root)
            h.runner.script["refresh"] = [grew(h, n, **changes)]
            assert h.go() == 0 and h.work() == 1, words
            assert h.runner.labels() == ["backup", "pre_gate", "refresh"], "it was run again, or went on"
            failed = h.rows("failed")[0]
            assert words in failed["why"] and failed["written"] is True and "loaded and verified, but" in failed["why"]
            assert "its totals match the reports, but the run is closed unpublished" in failed["text"]
            assert loadplan.run_result(RUN) == "FAILED" and not paths.lock_file().exists()
            assert not paths.published_dir(datetime(2026, 1, 1).date()).exists()
    with data_root() as root:                    # the marker without the exit code is not a pass either
        h = Harness(root)
        h.runner.script["refresh"] = [lambda argv: (h.w.__setattr__("newest_run", h.w.newest_run + 2), yq.Child(5, refresh_line() + "\n"))[1]]
        assert h.go() == 0 and h.work() == 1 and "ended with code 5" in h.rows("failed")[0]["why"]


@test("verify failed: FAILED for good, no second run, the data is said to be loaded, a second go is refused")
def _():
    with data_root() as root:
        h = Harness(root)
        status.write_state({"last_load": {"run": "311225_0900", "result": "ok"}})

        def verify_fails(argv):
            h.w.newest_run += 2
            return yq.Child(2, refresh_line(ok=False, verify_ok=False,
                                            error="The data loaded, but these totals do not match the reports: Sales; Stock value") + "\n")
        h.runner.script["refresh"] = [verify_fails]
        assert h.go() == 0 and h.work() == 1
        assert h.runner.labels() == ["backup", "pre_gate", "refresh"], "verify failed is never run again"
        failed = h.rows("failed")[0]
        assert failed["written"] is True and failed["step"] == "refresh" and failed["result"] == "failed"
        assert failed["why"] == "Totals differ from the reports: Sales; Stock value." and failed["metrics"] == "Sales; Stock value"
        # the data IS loaded, and that is what the line says; and nobody is sent to load it again
        assert failed["text"] == "LOAD FAILED at refresh. The data is in the database, but its totals do not match the reports."
        assert failed["next"] == "This run is closed. Ask Claude to look into these totals before anything is loaded again."
        assert "check_again" not in failed and "state" not in failed
        stopped = h.rows("step_failed", "refresh")[0]
        assert stopped["stops"] is True and stopped["lines"] == 2 and stopped["text"] == failed["why"]
        assert "state" not in stopped and "notes" not in stopped
        assert [r["text"] for r in h.rows("note", "refresh")] == ["Differs from the reports: Sales",
                                                                 "Differs from the reports: Stock value"]
        events = [(r["event"], r.get("step")) for r in h.rows()]            # the step's row, its notes, the closing row
        assert events[-4:] == [("step_failed", "refresh"), ("note", "refresh"), ("note", "refresh"), ("failed", "refresh")]
        assert not paths.lock_file().exists() and loadplan.run_result(RUN) == "FAILED"
        assert status.read_state()["last_load"]["run"] == "311225_0900", "a failed load was recorded as the last load"
        assert not paths.published_dir(datetime(2026, 1, 1).date()).exists()
        shown = len(h.box.shown)
        h.out.clear()
        assert h.go() == 2 and "FAILED" in h.said() and "refused for good" in h.said()
        assert len(h.box.shown) == shown and len(h.spawn.calls) == 1


def verify_fails_with(h: Harness, error: str):
    def answer(argv):
        h.w.newest_run += 2
        return yq.Child(2, refresh_line(ok=False, verify_ok=False, error=error) + "\n")
    return answer


@test("verify failed: the totals that differ come first, and every one of them is in the journal even when the line is cut")
def _():
    names = ["Sales gross (file span)", "Sales taxable (file span)", "Receivables total", "Stock snapshot units",
             "Stock snapshot value", "Channel split B2C", "Channel split B2B"]
    told = "The data loaded, but these totals do not match the reports: "
    with data_root() as root:
        h = Harness(root)
        h.runner.script["refresh"] = [verify_fails_with(h, told + "; ".join(names))]
        assert h.go() == 0 and h.work() == 1
        failed = h.rows("failed")[0]
        assert len("totals differ from the reports: " + "; ".join(names)) > yq.FIELD      # the case the status line cuts
        assert failed["why"].startswith("Totals differ from the reports: Sales gross (file span); Sales taxable (file span); ")
        assert failed["why"].endswith("...") and len(failed["why"]) == yq.FIELD
        shown = [n for n in names if n in failed["why"]]
        assert len(shown) >= 5, f"only {len(shown)} of the names fit in the line the owner reads"
        assert [r["text"] for r in h.rows("note", "refresh")] == ["Differs from the reports: " + n for n in names]
        assert h.rows("step_failed", "refresh")[0]["lines"] == len(names)
        assert "The data is in the database, but its totals do not match the reports." in failed["text"]
        assert failed["written"] is True
    with data_root() as root:                    # more than a screenful: the first eleven and how many more
        h = Harness(root)
        many = [f"Total {i:02d}" for i in range(1, 15)]
        h.runner.script["refresh"] = [verify_fails_with(h, told + "; ".join(many))]
        assert h.go() == 0 and h.work() == 1
        notes = [r["text"] for r in h.rows("note", "refresh")]
        assert len(notes) == 12 and notes[10] == "Differs from the reports: Total 11"
        assert notes[11] == "(+3 more totals differ: the worker log has them all)"
    with data_root() as root:                    # it ran and did not pass, and the refresh named no total: no invented names
        h = Harness(root)
        h.runner.script["refresh"] = [verify_fails_with(h, "Verification could not run: unknown")]
        assert h.go() == 0 and h.work() == 1
        assert h.runner.labels().count("refresh") == 1, "a verification that RAN and did not pass is never run again"
        failed = h.rows("failed")[0]
        assert failed["why"] == "Verification did not pass. Verification could not run: unknown."
        assert "metrics" not in failed and h.rows("note", "refresh") == [] and "lines" not in h.rows("step_failed", "refresh")[0]
        assert failed["written"] is True and loadplan.run_result(RUN) == "FAILED"


def cannot_verify(h: Harness, detail: str = "the database did not answer in time"):
    """The refresh loaded the data and its verification could not RUN (scripts/refresh.py keeps the
    error in its verify result; the child's program passes it on as verify_error)."""
    def answer(argv):
        h.w.newest_run += 2                      # the loader's record and the refresh's own, as in any load
        return yq.Child(2, refresh_line(ok=False, verify_ok=False, verify_error=detail,
                                        error="Verification could not run: " + detail) + "\n")
    return answer


@test("a verification that could not RUN is no failed verify: one identical re-run; twice, and the run is closed as unchecked")
def _():
    with data_root() as root:                    # a passing glitch: the identical load once more, and it verifies
        h = Harness(root)
        h.runner.script["refresh"] = [cannot_verify(h)]
        assert h.load() == 0, h.rows()[-1]
        calls = [c for c in h.runner.calls if c[0] == "refresh"]
        assert len(calls) == 2 and calls[0][1] == calls[1][1], "the re-run is not identical"
        assert yq.RETRY_PAUSE_S in h.clock.sleeps
        first = h.rows("step_failed", "refresh")[0]
        assert first["attempt"] == 1 and first["stops"] is False
        assert first["text"] == "The data loaded, but its totals could not be checked: the identical load runs once more."
        assert first["why"] == "The verification could not run (the database did not answer in time)."
        assert h.rows("step_start", "refresh")[1]["text"] == "The identical load runs once more."
        ok = h.rows("step_ok", "refresh")[0]     # the records of the run that passed are the ones counted
        assert ok["attempt"] == 2 and ok["ingest_runs"] == [9, 11] and ok["new_runs"] == 2 and "second run" in ok["text"]
        assert loadplan.run_result(RUN) == "FINISHED" and h.rows("finished")[0]["result"] == "ok"
        assert h.rows("failed") == [] and h.rows("note", "refresh") == []
        assert status.read_state()["last_load"]["run"] == RUN
    with data_root() as root:                    # twice: closed, and the line says what is so: loaded, not checked
        h = Harness(root)
        h.runner.script["refresh"] = [cannot_verify(h), cannot_verify(h, "the report folder could not be read")]
        assert h.go() == 0 and h.work() == 1
        assert h.runner.labels() == ["backup", "pre_gate", "refresh", "refresh"], h.runner.labels()
        failed = h.rows("failed")[0]
        assert failed["result"] == "failed" and failed["written"] is True and failed["step"] == "refresh"
        assert failed["text"] == ("LOAD FAILED at refresh. The data is in the database, but its totals could not be "
                                  "checked against the reports.")
        assert failed["why"] == ("The data loaded, but its totals could not be checked in either run: the verification "
                                 "could not run (the report folder could not be read).")
        assert failed["next"] == "This run is closed: type /yq-load for a new check."
        assert "metrics" not in failed and h.rows("note", "refresh") == [], "no total was compared: none is named"
        assert "do not match" not in whole(failed) and "differ" not in whole(failed)
        assert not paths.lock_file().exists() and not paths.state_file().exists()
        h.out.clear()
        assert h.go() == 2 and "refused for good" in h.said()
    with data_root() as root:                    # could not verify, then the load stage itself fails: closed, each said as it was
        h = Harness(root)
        h.runner.script["refresh"] = [cannot_verify(h), yq.Child(1, "Traceback (most recent call last):\nMemoryError\n")]
        assert h.go() == 0 and h.work() == 1
        failed = h.rows("failed")[0]
        assert failed["why"].startswith("The second run of the load failed: the load program gave no result (exit code 1")
        assert failed["text"] == "LOAD FAILED at refresh. The database holds this load, or part of it."
    with data_root() as root:                    # the load stage fails, then the data loads and cannot be verified
        h = Harness(root)
        h.runner.script["refresh"] = [yq.Child(2, refresh_line(ok=False, verify_ok=None, error="The load step failed.") + "\n"),
                                      cannot_verify(h)]
        assert h.go() == 0 and h.work() == 1
        failed = h.rows("failed")[0]
        assert failed["why"].startswith("The data loaded, but its totals could not be checked: the verification could not run")
        assert "could not be checked against the reports" in failed["text"]
    # a verification that ran and differs stays what it was: FAILED at once, whatever else the result carries
    with data_root() as root:
        h = Harness(root)
        h.runner.script["refresh"] = [verify_fails_with(h, "The data loaded, but these totals do not match the reports: Sales")]
        assert h.go() == 0 and h.work() == 1 and h.runner.labels().count("refresh") == 1


@test("a gate that does not pass still publishes and is journalled 'gate needs a look' with its FAIL lines")
def _():
    with data_root() as root:
        h = Harness(root)
        h.runner.script["compare"] = [yq.Child(1, "compare a -> b\n  delta: orders: 10 -> 12 (+2)\n"
                                                  "  FAIL:  order 12 (ORD-12) DISAPPEARED\n\nFAIL: 1 failure(s), 1 explained delta(s)\n")]
        assert h.load() == 0
        look = h.rows("step_failed", "post_gate")[0]
        assert look["text"] == "Gate needs a look." and look["stops"] is False and look["lines"] == 2
        assert look["next"] == "The load goes on."
        # each line of the gate is a note that says whose words it is: a note is read with nothing beside it
        assert [r["text"] for r in h.rows("note", "post_gate")] == ["The gate says: FAIL: order 12 (ORD-12) DISAPPEARED",
                                                                    "The gate says: FAIL: 1 failure(s), 1 explained delta(s)"]
        assert look["pre"].endswith("_pre-data-010126.json") and look["post"].endswith("_post-data-010126.json")
        assert h.rows("step_ok", "publish") and paths.published_dir(datetime(2026, 1, 1).date()).is_dir()
        done = h.rows("finished")[0]
        assert done["result"] == "ok_needs_look" and done["needs_look"] == ["post_gate"] and "Needs a look: post_gate" in done["text"]
        assert done["next"] == "The data is loaded and verified: ask Claude to look into the 'needs a look' lines above."
        assert status.read_state()["last_load"]["result"] == "ok, needs a look: post_gate"
        assert loadplan.run_result(RUN) == "FINISHED" and not paths.lock_file().exists()
    with data_root() as root:                    # PASS on the screen but exit 1: the exit code comes first
        h = Harness(root)
        h.runner.script["compare"] = [yq.Child(1, "PASS: 0 failure(s), 0 explained delta(s)\n")]
        assert h.load() == 0 and h.rows("finished")[0]["needs_look"] == ["post_gate"]
    with data_root() as root:                    # no after-snapshot, and a reconcile that differs: both reported, still published
        h = Harness(root)
        h.runner.script["post_gate"] = [yq.Child(1, "Traceback\nOSError: timed out\n")]
        h.runner.script["reconcile"] = [yq.Child(2, "Dashboard receivables   10.00   12.00  FAIL (20.00%)\n   WHY: tile drifted\n"
                                                    "MISMATCHES FOUND \u2014 see WHY hints above.\n")]
        assert h.load() == 0
        assert h.runner.labels() == ["backup", "pre_gate", "refresh", "reconcile", "post_gate"]      # nothing to compare
        assert h.rows("finished")[0]["needs_look"] == ["reconcile", "post_gate"]
        rec = h.rows("step_failed", "reconcile")[0]
        assert rec["stops"] is False and rec["lines"] == 3 and "Reconcile needs a look (exit code 2" in rec["text"]
        said = [r["text"] for r in h.rows("note", "reconcile")]
        assert len(said) == 3 and "The reconcile says: WHY: tile drifted" in said
        assert all(line.startswith("The reconcile says: ") for line in said)
        assert h.rows("step_failed", "post_gate")[0]["text"].startswith("Gate needs a look: the after-snapshot was not written")
        assert paths.published_dir(datetime(2026, 1, 1).date()).is_dir()


@test("an unexpected error: before the load it stops with nothing written; after a verified load it is only 'needs a look'")
def _():
    def boom(argv):
        raise ZeroDivisionError("a bug in a step")

    with data_root() as root:
        h = Harness(root)
        h.runner.script["pre_gate"] = [boom]
        assert h.go() == 0 and h.work() == 1
        failed = h.rows("failed")[0]
        assert failed["written"] is False and "LOAD STOPPED at pre_gate" in failed["text"]
        assert failed["why"] == "An unexpected error at pre_gate (ZeroDivisionError: a bug in a step)."
        assert "run:refresh" not in h.w.trace and not paths.lock_file().exists()
        assert any("Traceback" in ln for ln in h.w.log), "the worker log holds no traceback"
    with data_root() as root:
        h = Harness(root)
        h.runner.script["refresh"] = [boom]          # the engine's child could not even be started
        assert h.go() == 0 and h.work() == 1
        assert h.rows("failed")[0]["written"] is True and "The database holds this load, or part of it" in h.rows("failed")[0]["text"]
    with data_root() as root:
        h = Harness(root)
        h.runner.script["reconcile"] = [boom]
        h.runner.script["post_gate"] = [boom]
        assert h.load() == 0
        done = h.rows("finished")[0]
        assert done["needs_look"] == ["reconcile", "post_gate"] and done["result"] == "ok_needs_look"
        assert h.rows("step_failed", "reconcile")[0]["text"] == \
            "Reconcile needs a look: an unexpected error (ZeroDivisionError: a bug in a step)."
        assert h.rows("step_failed", "post_gate")[0]["text"] == \
            "Gate needs a look: an unexpected error (ZeroDivisionError: a bug in a step)."
        assert paths.published_dir(datetime(2026, 1, 1).date()).is_dir() and status.read_state()["last_load"]["run"] == RUN
        assert not paths.lock_file().exists() and loadplan.run_result(RUN) == "FINISHED"


def brief_naps():
    """A stand-in for time.sleep in a test that would otherwise wait out the writer's retry pauses
    (twenty seconds of them for one journal line). It still sleeps, a hundredth of a second at most:
    a file that Windows holds for a moment (a scanner, another test run on a busy PC) is ridden out
    as in real life, where a stand-in that does not sleep at all would let every retry fail at once."""
    real = time.sleep
    return lambda seconds: real(min(max(float(seconds), 0.0), 0.01))


@test("a journal that cannot be written: the load does not begin (a resume tells 'it had begun' from that very line)")
def _():
    with data_root() as root:
        h = Harness(root)
        real_append, real_sleep = loadplan.journal_append, yq.time.sleep

        def append(run, event, **fields):
            if event == "step_start" and fields.get("step") == "refresh":
                raise PermissionError("the sync client holds the file")
            return real_append(run, event, **fields)
        loadplan.journal_append, yq.time.sleep = append, brief_naps()
        try:
            assert h.go() == 0 and h.work() == 1
        finally:
            loadplan.journal_append, yq.time.sleep = real_append, real_sleep
        assert "run:refresh" not in h.w.trace and h.runner.labels() == ["backup", "pre_gate"]
        failed = h.rows("failed")[0]
        assert failed["written"] is False and "journal could not be written" in failed["why"]
        assert failed["text"] == "LOAD STOPPED at refresh. Nothing was written to the database."
        assert not paths.lock_file().exists()
    with data_root():
        calls = []
        real_append, real_sleep = loadplan.journal_append, yq.time.sleep

        def flaky(run, event, **fields):
            calls.append(event)
            if len(calls) < 3:
                raise PermissionError("busy")
            return real_append(run, event, **fields)
        loadplan.journal_append, yq.time.sleep = flaky, brief_naps()
        try:
            assert yq.journal(RUN, "note", text="third time lucky", nothing=None) is True
        finally:
            loadplan.journal_append, yq.time.sleep = real_append, real_sleep
        assert len(calls) >= 3 and calls[:3] == ["note"] * 3, calls          # (more only when the file itself was busy)
        rows = loadplan.journal_read(RUN)
        assert len(rows) == 1 and rows[0]["text"] == "third time lucky" and "nothing" not in rows[0]


@test("after the load has closed: the brief, then the e-mail, then one Delivery line; nothing of it changes the load")
def _():
    said = "Delivery: e-mail sent to 1 address(es) (full, auto): YQ brief"
    retry = "Its retry: python -m scripts.yq_send --run " + RUN
    with data_root() as root:
        h = Harness(root)
        h.deps.deliver = True
        seen = {}

        def at_deliver():
            seen["lock"], seen["last"] = paths.lock_file().exists(), loadplan.journal_read(RUN)[-1]["event"]
        h.runner.hooks["deliver"] = at_deliver
        h.runner.script["deliver"] = [yq.Child(0, "written: exports/ops/2026-01-01/delivery\n")]
        h.runner.script["send"] = [yq.Child(0, "a line before\n" + said + "\n")]
        assert h.load() == 0
        assert seen == {"lock": False, "last": "finished"}, seen          # the load had closed and unlocked first
        assert h.runner.labels()[-2:] == ["deliver", "send"], h.runner.labels()
        assert h.runner.argv("deliver")[-5:] == ["-m", "scripts.ops", "deliver", "--run", RUN]
        assert h.runner.argv("send")[-5:] == ["-m", "scripts.yq_send", "--run", RUN, "--auto"]
        timeouts = {c[0]: c[2] for c in h.runner.calls}
        assert timeouts["deliver"] == yq.DELIVER_TIMEOUT_S and timeouts["send"] == yq.SEND_TIMEOUT_S
        rows = loadplan.delivery_read(RUN)
        assert [r["event"] for r in rows] == ["queued", "ended"] and rows[-1]["text"] == said, rows
        assert loadplan.journal_read(RUN)[-1]["event"] == "finished"      # the load's journal ends on its closing row
    for answer, starts in (([yq.Child(1, "Traceback\nKeyError: x\n")], "Delivery: the brief could not be prepared "
                                                                         "(exit code 1; its last line: KeyError: x)"),
                           (None, "Delivery: the e-mail program gave no answer (exit code 1; it ran out of time)"),
                           ("raise", "Delivery: an unexpected error (OSError)")):
        with data_root() as root:
            h = Harness(root)
            h.deps.deliver = True
            if answer == "raise":
                h.runner.script["deliver"] = [yq.Child(0, "")]

                def boom():
                    raise OSError("the disk")
                h.runner.hooks["send"] = boom
            elif answer:
                h.runner.script["deliver"] = answer
            else:
                h.runner.script["deliver"] = [yq.Child(0, "")]
                h.runner.script["send"] = [yq.Child(1, "", timed_out=True)]
            assert h.load() == 0, starts                                  # the load's own result, whatever the e-mail did
            last = loadplan.delivery_read(RUN)[-1]
            assert last["event"] == "ended" and last["text"].startswith(starts), last["text"]
            assert last["text"].endswith(". " + retry), last["text"]
            assert loadplan.run_result(RUN) == "FINISHED"
    with data_root() as root:                                             # nothing written: no brief, no e-mail
        h = Harness(root)
        h.deps.deliver = True
        h.runner.script["backup"] = [yq.Child(1, "No space left on the disk.\n")]
        assert h.load() != 0
        assert "deliver" not in h.runner.labels() and loadplan.delivery_read(RUN) == []
    with data_root() as root:                                             # tests and harnesses deliver nothing
        h = Harness(root)
        assert h.deps.deliver is False and h.load() == 0
        assert "deliver" not in h.runner.labels() and loadplan.delivery_read(RUN) == []
    with data_root():                                                     # a rehearsal only says what would happen
        loadplan.journal_append(RUN, "finished", result="ok")
        calls = []
        deps = SimpleNamespace(rehearsal=True, deliver=True, runner=lambda *a: calls.append(a), keep_awake=lambda on: True)
        yq.Job.deliver_after(SimpleNamespace(run=RUN, deps=deps))
        rows = loadplan.delivery_read(RUN)
        assert calls == [] and [r["event"] for r in rows] == ["ended"] and "(rehearsal)" in rows[0]["text"], rows
    # the line `load status` and the sender print to retry it is the writer's own
    from scripts.ops import load_check
    from scripts import yq_send
    assert yq.SEND_LINE == load_check.SEND_RETRY and yq.SEND_LINE.format(run=RUN) == yq_send.retry_line(RUN)


@test("after the load: a retry is offered only where it would be accepted; a newer load has its own e-mail")
def _():
    newer = "020126_0900"

    def finished_since():                       # another load finished while this one's brief was being made
        at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(timespec="seconds")
        status.write_state({"last_load": {"run": newer, "finished_at": at}})
    refused = yq.Child(2, "Delivery of run 010126_0900: NOT prepared: the database was written after this load (a "
                          "Data-page upload or another load). Its brief would show other figures than this load's, so "
                          "nothing is sent for it.\n")
    no_retry = f"No retry is needed: a newer load (run {newer}) has its own e-mail."
    for answer, hook, starts, ends in (
            (refused, None, "Delivery: no brief was made for this load (exit code 2; its last line: Delivery of run ",
             yq.NO_BRIEF_RETRY),
            (refused, finished_since, "Delivery: no brief was made for this load (exit code 2", no_retry),
            (yq.Child(1, "Traceback\nKeyError: x\n"), finished_since, "Delivery: the brief could not be prepared (exit "
                                                                      "code 1; its last line: KeyError: x)", no_retry)):
        with data_root() as root:
            h = Harness(root)
            h.deps.deliver = True
            paths.write_text(paths.ops_dir() / "delivery.json", json.dumps(        # delivery on, as on the PC
                {"mode": "on", "from": "YQ <reports@mail.example.invalid>", "to": ["owner@example.invalid"]}))
            h.runner.script["deliver"] = [answer]
            if hook:
                h.runner.hooks["deliver"] = hook
            assert h.load() == 0 and loadplan.run_result(RUN) == "FINISHED"      # the load's own result, whatever
            last = loadplan.delivery_read(RUN)[-1]
            assert last["event"] == "ended" and last["text"].startswith(starts), last["text"]
            assert last["text"].endswith(" " + ends) and "Its retry" not in last["text"], last["text"]
            assert "send" not in h.runner.labels(), "the e-mail was tried without its brief"
    assert yq.NO_BRIEF_RETRY.startswith("No retry: ") and "next load sends its own e-mail" in yq.NO_BRIEF_RETRY
    with data_root() as root:                    # exit code 2 without the deliver command's refusal (a usage error): a failure
        h = Harness(root)
        h.deps.deliver = True
        h.runner.script["deliver"] = [yq.Child(2, "usage: python -m scripts.ops deliver\nerror: unrecognized arguments\n")]
        assert h.load() == 0
        last = loadplan.delivery_read(RUN)[-1]["text"]
        assert last.startswith("Delivery: the brief could not be prepared (exit code 2; its last line: error: "), last
        assert last.endswith(f". Its retry: python -m scripts.yq_send --run {RUN}"), last
    assert yq.DELIVER_REFUSED == "NOT prepared:"     # the deliver command's own word (tests/test_ops_deliver.py)


@test("a line that sends him back to /yq-load names the folder: he once typed '/yq-load again' as a line said it")
def _():
    assert yq.folder_word({"drop": "C:/x/Uplaods/151026"}) == "151026"
    for drop in ("C:/x/Uplaods/151026 (2)", "C:/x/Uplaods/151026/second set", "D:/elsewhere/reports", "", None):
        assert yq.folder_word({"drop": drop}) == "<ddmmyy>", drop      # not what /yq-load <ddmmyy> would find
    assert yq.folder_word(None) == "<ddmmyy>"
    assert yq.again_line({"drop": "C:/x/Uplaods/151026"}, "151026_0900") == "Type /yq-load 151026 again."
    assert yq.again_line({"drop": "C:/x/Uplaods/151026"}, "151026_0900", "Then") == "Then type /yq-load 151026 again."
    rehearsal = {"rehearsal": True, "drop": "C:/x/business_data/ops/loads/000000_1027/drop"}
    assert yq.again_line(rehearsal, "000000_1027") == "Ask for the rehearsal again."   # he asks for one in words
    assert yq.again_line(rehearsal, "000000_1027", "Then") == "Then ask for the rehearsal again."
    with data_root() as root:                    # the check said STOP
        h = Harness(root)
        h.w.write_plan(stops=["Stock ledger: a guard would refuse it"])
        assert h.go() == 2 and h.said().endswith("Once that is settled, type /yq-load 010126 again."), h.said()
    with data_root() as root:                    # a permission rule would start a load without asking him
        f = root / ".claude" / "settings.local.json"
        f.parent.mkdir(parents=True)
        f.write_text(json.dumps({"permissions": {"allow": ["Bash(python *)"]}}), encoding="utf-8")
        h = Harness(root, settings=[f])
        assert h.go() == 2 and h.said().endswith("\nThen type /yq-load 010126 again."), h.said()
    for refused in (yq.archive_refusal("pw", None, None), yq.archive_refusal(None, "7z.exe", "2026-10-15")):
        assert refused.endswith(", then type /yq-load"), refused      # (the box and the backup step): nothing after it
    said = [s for s in code_strings(writer_tree()) if "/yq-load again" in s]     # what the writer says, not its notes
    assert said == [], f"a line names no folder before 'again': {said}"


@test("the real providers: only reads that scripts.ops.db accepts, each session closed, the live site asked once")
def _():
    with data_root() as root:
        w = World(root)
        seen = {"sql": [], "open": 0, "closed": 0, "health": []}

        class FakeSession:
            def q(self, sql, params=None):
                db.check_sql(sql)                                  # what the real session does before it sends
                seen["sql"].append((" ".join(sql.split()), params))
                if "to_regclass" in sql:
                    return [{"ok": True}]
                if "ingest_batches" in sql:
                    return [{"id": 4}]
                if "from audit_log" in sql:
                    return [{"event": "ingest", "at": "2026-01-01 09:20:00+00"}]
                if "coalesce(file" in sql:
                    return [{"id": 8, "file": "refresh 010126_0900 (data as of 2025-12-31)"},
                            {"id": 9, "file": "Focus refresh (data as of 2025-12-31)"},
                            {"id": 10, "file": "refresh _upload (data as of 2026-01-01)"}]
                if "new_rows" in sql:
                    return [{"new_rows": 2, "max_id": 9}]
                if "ingest_runs" in sql:
                    return [{"max_id": 7}]
                return [{"n": 3, "max_id": 30}]

            def __enter__(self):
                seen["open"] += 1
                return self

            def __exit__(self, *exc):
                seen["closed"] += 1
                return False

        def health(url, **kw):
            seen["health"].append(url)
            return {"status": "ok", "commit": "c" * 12}
        real_session, real_health = db.session, db.health
        db.session, db.health = (lambda **kw: FakeSession()), health
        try:
            parts = yq._real_parts(w.plan, True, yq.ROOT)
            assert set(parts) == {"drop", "staging", "head", "live", "fingerprint"} and len(loadplan.token_of(parts)) == 32
            assert parts["live"] == "c" * 12 and len(parts["head"]) == 40
            assert parts["drop"] == loadplan.manifest(w.drop) and parts["staging"] == loadplan.manifest(w.staging)
            assert set(parts["fingerprint"]) == set(loadplan.FINGERPRINT_TABLES) | {"ingest_runs", "ingest_batches_open"}
            assert parts["fingerprint"]["ingest_batches_open"] == [4] and parts["fingerprint"]["orders"] == [3, 30]
            assert seen["health"] == ["https://ci.invalid/health"] and seen["open"] == seen["closed"] == 1
            light = yq._real_parts(w.plan, False, yq.ROOT)          # a resume: no session is even opened
            assert set(light) == {"drop", "staging", "head", "live"} and seen["open"] == 1
            assert yq._real_ingest_runs(None) == (7, 0)
            assert yq._real_ingest_runs(7) == (9, 2)
            assert seen["sql"][-1] == ("select count(*) as new_rows, coalesce(max(id), 0) as max_id from ingest_runs where id > %s", (7,))
            assert seen["open"] == seen["closed"] == 3
            # a resume reads what another load wrote meanwhile: one session, SELECTs the guard accepts
            yq.journal(RUN, "step_start", step="refresh")       # this run's own refresh left the loader's row
            foreign = yq._real_foreign_writes(dict(w.plan, token_parts={"fingerprint": {"ingest_runs": 7}}))
            assert seen["open"] == seen["closed"] == 4 and len(foreign) == 2, foreign
            assert "refresh _upload" in foreign[0] and "Data page" in foreign[1]
        finally:
            db.session, db.health = real_session, real_health
        relative = dict(w.plan, drop="Uplaods/010126")              # a plan may name the drop folder either way
        assert yq.drop_path(relative) == w.drop and yq.drop_path(w.plan) == w.drop


@test("publish: a new folder through a temporary one, an older folder of that date moved aside, state.json, unlock")
def _():
    with data_root() as root:
        h = Harness(root)
        target = paths.published_dir(datetime(2026, 1, 1).date())
        target.mkdir(parents=True)
        (target / "Sales_day_book_old.xlsx").write_bytes(b"what was loaded before")
        status.write_state({"last_brief_data_date": "2025-12-30"})
        seen = {}
        h.runner.hooks["compare"] = lambda: seen.update(before=sorted(p.name for p in target.iterdir()))
        assert h.load() == 0
        assert seen["before"] == ["Sales_day_book_old.xlsx"], "published before the gate"
        assert target.name == "Focus ERP 2026-01-01"
        assert loadplan.manifest(target) == loadplan.manifest(h.w.staging)
        assert loadplan.manifest(h.w.staging), "staging is copied, not moved"
        aside = list((paths.staging_dir() / "superseded").iterdir())
        assert len(aside) == 1 and aside[0].name.startswith("Focus ERP 2026-01-01_2026")
        assert [p.name for p in aside[0].iterdir()] == ["Sales_day_book_old.xlsx"]
        assert not [p for p in (root / "business_data").iterdir() if p.name.startswith(".publishing")]
        state = status.read_state()
        last = state["last_load"]
        # the fixture's plan holds its profitability report back: the load writes the day down (see the test below)
        assert set(last) == {"run", "finished_at", "data_date", "result", "published", "profit_held_since",
                             "refresh_seconds", "refresh_seconds_recent"}, last       # the next load's start window
        assert last["run"] == RUN and last["data_date"] == "2025-12-31" and last["result"] == "ok"
        assert last["published"] == "Focus ERP 2026-01-01" and last["finished_at"].endswith("+03:00")
        assert state["last_brief_data_date"] == "2025-12-30", "the rest of state.json was lost"
        assert "010126_0900" in status._last_load_text(last) and status.unloaded_drops(last["run"]) == []
        row = h.rows("step_ok", "publish")[0]
        assert row["published"] == "Focus ERP 2026-01-01" and row["superseded"] == str(aside[0])
        assert row["text"] == ("Published as 'Focus ERP 2026-01-01'. The earlier folder of that date was moved to "
                               "staging/superseded.") and "not_copied" not in row
        assert h.rows()[-1]["event"] == "finished" and h.rows()[-2]["step"] == "publish"
        assert "Published as 'Focus ERP 2026-01-01'" in h.rows("finished")[0]["text"] and not paths.lock_file().exists()
        assert sorted(p.name for p in h.w.drop.iterdir()) == ["Sales_day_book1.xlsx", "Stock_balance1.xlsx"]     # drop untouched
    with data_root() as root:                    # nothing to publish under: the load stays loaded, publish needs a look
        h = Harness(root)
        h.w.write_plan(as_on=None)
        h.w.run = RUN
        yq._remove_tree(h.w.staging / "nothing")
        h.deps.published_dir = lambda as_on: (_ for _ in ()).throw(OSError("the disk is full"))
        assert h.load() == 0
        assert h.rows("finished")[0]["needs_look"] == ["publish"] and status.read_state()["last_load"]["published"] is None
        look = h.rows("step_failed", "publish")[0]
        assert look["text"] == "Publish needs a look: the loaded set could not be copied to business_data."
        assert look["why"] == "OSError: the disk is full."
        assert look["next"] == "The data is loaded and verified; only the copy of its files is missing."
    with data_root() as root:                    # the copy may land nowhere but under business_data/ and exports/
        h = Harness(root)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as elsewhere:
            h.deps.published_dir = lambda as_on: Path(elsewhere) / f"Focus ERP {as_on.isoformat()}"
            assert h.load() == 0
            assert h.rows("finished")[0]["needs_look"] == ["publish"]
            assert h.rows("step_failed", "publish")[0]["why"].startswith(
                "PermissionError: the loaded set is published only under business_data/ and exports/, not in ")
            assert list(Path(elsewhere).iterdir()) == [], "something was written outside the data root"
            assert status.read_state()["last_load"]["published"] is None
    assert yq.as_on_date({"as_on": "2026-01-05"}, RUN).isoformat() == "2026-01-05"
    assert yq.as_on_date({"as_on": {"stock": "2026-01-05", "ar": "2026-01-04", "x": None}}, RUN).isoformat() == "2026-01-05"
    assert yq.as_on_date({}, RUN).isoformat() == "2026-01-01" and yq.as_on_date({}, "000000_0900") is None


@test("publish: the marker of a partial export travels with the set; the one-time permission to replace does not")
def _():
    replace_ok, partial_ok = loadplan.MARKERS
    reports = ["Sales_day_book1.xlsx", "Stock_balance1.xlsx"]

    def published_set(root: Path) -> Path:      # under THIS test's data root (the path is worked out inside it)
        folder = paths.published_dir(datetime(2026, 1, 1).date())
        assert root in folder.parents, folder
        return folder
    with data_root() as root:                    # both options given: the loader sees both markers in staging ...
        h = Harness(root)
        target = published_set(root)
        with_options(h, (replace_ok, partial_ok), replace_ok=True, partial_ok=True)
        seen = {}
        h.runner.hooks["refresh"] = lambda: seen.update(staged=sorted(p.name for p in h.w.staging.iterdir()))
        assert h.load() == 0, h.rows()[-1]
        assert seen["staged"] == sorted([partial_ok, replace_ok] + reports)
        # ... and the set that is kept says "partial on purpose" (verify reads it from the folder it checks), no more
        published = sorted(p.name for p in target.iterdir())
        assert published == sorted([partial_ok] + reports), published
        assert (target / partial_ok).read_bytes() == (h.w.staging / partial_ok).read_bytes()
        want = {name: meta for name, meta in loadplan.manifest(h.w.staging).items() if name != replace_ok}
        assert loadplan.manifest(target) == want
        assert sorted(p.name for p in h.w.staging.iterdir()) == sorted([partial_ok, replace_ok] + reports), "staging changed"
        row = h.rows("step_ok", "publish")[0]
        assert row["not_copied"] == [replace_ok] and row["published"] == "Focus ERP 2026-01-01"
        assert row["text"] == "Published as 'Focus ERP 2026-01-01'. The one-time permission to replace is not kept with it."
        assert h.rows("finished")[0]["result"] == "ok"
    with data_root() as root:                    # spelt the way the loader also reads them: any letter case, any ending
        h = Harness(root)
        with_options(h, (replace_ok.lower() + ".txt", partial_ok.title() + ".flag"), replace_ok=True, partial_ok=True)
        assert h.load() == 0
        assert sorted(p.name for p in published_set(root).iterdir()) == sorted([partial_ok.title() + ".flag"] + reports)
        assert h.rows("step_ok", "publish")[0]["not_copied"] == [replace_ok.lower() + ".txt"]
    with data_root() as root:                    # the partial marker alone travels; nothing is said about a permission
        h = Harness(root)
        with_options(h, (partial_ok,), partial_ok=True)
        assert h.load() == 0 and (published_set(root) / partial_ok).is_file()
        assert h.rows("step_ok", "publish")[0]["text"] == "Published as 'Focus ERP 2026-01-01'."
    with data_root() as root:                    # no marker in staging: none appears in the set
        h = Harness(root)
        assert h.load() == 0 and sorted(p.name for p in published_set(root).iterdir()) == reports
    assert yq.one_time_marker(replace_ok) and yq.one_time_marker(replace_ok.lower() + ".txt")
    assert not yq.one_time_marker(partial_ok) and not yq.one_time_marker("Sales_day_book1.xlsx")
    assert not yq.one_time_marker(replace_ok + "_report.xlsx"), "only the marker itself: its name before the first dot"
    assert {m.lower() for m in loadplan.MARKERS if yq.one_time_marker(m)} == set(yq.ONE_TIME_OPTIONS)
    with data_root() as root:                    # a staging folder that holds nothing but the permission: nothing to publish
        only = root / "business_data" / "staging" / "only"
        only.mkdir(parents=True)
        (only / replace_ok).write_text("x", encoding="utf-8")
        target = published_set(root)
        exc = raises(OSError, lambda: yq.publish_folder(only, target, root / "business_data" / "aside", RUN, NOON))
        assert "no staged file" in str(exc) and not target.exists()
        # and a place outside the data root is refused before anything is made there
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as elsewhere:
            (only / "Sales_day_book1.xlsx").write_bytes(b"x")
            raises(PermissionError, lambda: yq.publish_folder(only, Path(elsewhere) / "Focus ERP 2026-01-01",
                                                              root / "business_data" / "aside", RUN, NOON))
            assert list(Path(elsewhere).iterdir()) == []


def load_with_profit(root: Path, row: dict | None, previous: dict | None, **plan) -> dict:
    """One whole load whose plan carries this line for the profitability report (None = the drop
    brought none, and the check wrote no line), after a load that left `previous` as last_load.
    -> the last_load this load wrote"""
    h = Harness(root)
    if previous is not None:
        status.write_state({"last_load": previous})
    rows = [v for v in h.w.plan["verdicts"] if v["kind"] != yq.PROFIT_KIND] + ([row] if row else [])
    h.w.write_plan(verdicts=rows, **plan)
    assert h.load() == 0, h.rows()[-1]
    return status.read_state()["last_load"]


@test("state.json: profit_held_since is written while the profitability report is held back, carried, and dropped once it loads")
def _():
    held = verdict("Profit1.xlsx", yq.PROFIT_KIND, "Profitability report", "hold_back", False)
    loaded = verdict("Profit1.xlsx", yq.PROFIT_KIND, "Profitability report", "load", True)
    absent = verdict(None, yq.PROFIT_KIND, "Profitability report", "not_loaded", False)
    before = {"run": "311225_0900", "result": "ok", "profit_held_since": "2025-12-28"}
    # 1. this load held the report back
    with data_root() as root:                    # ... for the first time: the as-on date of the report that is held
        last = load_with_profit(root, held, {"run": "311225_0900", "result": "ok"},
                                as_on_all={"order_lines": "2026-01-01", yq.PROFIT_KIND: "2025-12-31"})
        assert last["profit_held_since"] == "2025-12-31" and last["run"] == RUN
    with data_root() as root:                    # ... and the load before had held it too: its day stands
        last = load_with_profit(root, held, before, as_on_all={yq.PROFIT_KIND: "2025-12-31"})
        assert last["profit_held_since"] == "2025-12-28"
    with data_root() as root:                    # ... with no state file at all (the first load through this program)
        assert load_with_profit(root, held, None)["profit_held_since"] == "2026-01-01"        # the fixture's as_on_all
    # 2. the report was not in the drop: what the load before wrote down is carried over, and nothing is invented
    for row in (absent, None):
        with data_root() as root:
            assert load_with_profit(root, row, before)["profit_held_since"] == "2025-12-28"
        with data_root() as root:
            assert "profit_held_since" not in load_with_profit(root, row, {"run": "311225_0900", "result": "ok"})
    # 3. the report loaded: the key is absent, whatever the load before had written
    with data_root() as root:
        last = load_with_profit(root, loaded, before)
        assert "profit_held_since" not in last and last["run"] == RUN
    # the day comes from the plan: every report's date first, then the loaded ones, then the day of the reports
    p = {"verdicts": [held]}
    assert yq.profit_held_since(dict(p, as_on_all={yq.PROFIT_KIND: "2026-01-03"}, as_on={yq.PROFIT_KIND: "2026-01-02"}), RUN, {}) == "2026-01-03"
    assert yq.profit_held_since(dict(p, as_on={yq.PROFIT_KIND: "2026-01-02"}, data_date="2026-01-05"), RUN, None) == "2026-01-02"
    assert yq.profit_held_since(dict(p, as_on={"order_lines": "2026-01-02"}, data_date="2026-01-05"), RUN, None) == "2026-01-05"
    assert yq.profit_held_since(dict(p, as_on={"order_lines": "2026-01-02"}), RUN, None) == "2026-01-02"      # the set's own day
    assert yq.profit_held_since(p, RUN, None) == "2026-01-01"                                                  # the run's name
    # the verdict as the check words it, and a day on file that is no day
    assert yq.profit_held_since({"verdicts": [dict(held, verdict=None, decision="HOLD BACK")]}, RUN, None) == "2026-01-01"
    assert yq.profit_held_since({"verdicts": [dict(held, verdict="HOLD BACK")]}, RUN, {"profit_held_since": "not a day"}) == "2026-01-01"
    assert yq.profit_held_since({"verdicts": [absent]}, RUN, {"profit_held_since": "not a day"}) is None
    assert yq.profit_held_since({"verdicts": [loaded]}, RUN, before) is None
    assert yq.profit_held_since({}, RUN, before) == "2025-12-28" and yq.profit_held_since({"verdicts": "junk"}, RUN, None) is None
    # another report held back is not this one
    other = verdict("Stock_balance1.xlsx", "stock_balance", "Stock balance", "hold_back", False)
    assert yq.profit_held_since({"verdicts": [other]}, RUN, None) is None
    with data_root() as root:                    # a state file that cannot be read costs the day, never the load
        h = Harness(root)

        def unreadable():
            raise PermissionError(13, "the sync client holds state.json")
        h.deps.read_state = unreadable
        assert h.load() == 0 and status.read_state()["last_load"]["profit_held_since"] == "2026-01-01"


# ── resume ────────────────────────────────────────────────────────────────────────────────────

@test("resume: backup and before-snapshot are reused, no fingerprint is compared, the whole refresh runs again")
def _():
    with data_root() as root:
        h = Harness(root)
        left = interrupted(h, upto="refresh")
        h.w.fingerprint = {"orders": [0, 0], "stock_movements": [0, 0], "ingest_runs": 8, "ingest_batches_open": []}   # half loaded
        h.w.newest_run = 8
        assert status.interrupted_banner() == "LOAD INTERRUPTED at refresh: run /yq-load"
        assert h.go() == 0, h.out
        title, text, _ = h.box.shown[0]
        assert title == "YQ load 010126" and "RESUME" in text and "interrupted at step 'refresh'" in text
        lock = status.read_lock()
        assert lock["word_sha"] == h.spawn.sha != "old-word" and lock["resume"] is True and lock["pid"] == os.getpid()
        # the refresh had begun: the new lock says so from its first moment, whatever becomes of its worker
        assert lock["step"] == "refresh" and status.lock_state()["step"] == "refresh"
        # where an interrupted load stands: its lock's step, or the journal's last when a launcher's lock was left
        assert yq._stopped_step(RUN, {"step": "backup"}) == "backup" and yq._stopped_step(RUN, None) == "refresh"
        assert yq._stopped_step(RUN, {"step": "start"}) == "refresh" == yq._stopped_step(RUN, {"step": "start (rehearsal)"})
        assert yq._stopped_step(RUN, {"step": "token (rehearsal)"}) == "token" and yq._stopped_step("020126_0900", {}) == "start"
        # Windows lists a deleted file until its last reader lets go, and a scanner on a busy PC can
        # hold one for seconds: wait for the listing (the loop ends the moment it is clear)
        deadline = time.monotonic() + 30
        while list(paths.ops_dir().glob("LOCK.*")) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not list(paths.ops_dir().glob("LOCK.*")), "the dead lock was left beside the new one"
        assert h.work() == 0
        assert all(t == "token:files and commits only" for t in h.w.trace if t.startswith("token:")), h.w.trace
        assert len([t for t in h.w.trace if t.startswith("token:")]) == 3
        assert h.runner.labels() == ["refresh", "reconcile", "post_gate", "compare"], h.runner.labels()
        assert "archive" not in h.w.trace
        assert h.runner.argv("compare")[6] == str(left["pre"]), "the compare did not use the first before-snapshot"
        reused = [r for r in h.rows("step_ok") if r.get("reused")]
        assert [r["step"] for r in reused] == ["backup", "pre_gate"]
        assert reused[0]["archive"] == str(left["archive"]) and reused[1]["path"] == str(left["pre"])
        started = h.rows("started")
        assert len(started) == 2 and started[1]["resume"] is True and "stopped at refresh" in started[1]["text"]
        assert "a resume does not compare the database" in h.rows("step_ok", "token")[-1]["text"]
        assert loadplan.run_result(RUN) == "FINISHED" and not paths.lock_file().exists()
        assert status.read_state()["last_load"]["run"] == RUN
    with data_root() as root:                    # the dead lock, once set aside, is busy: read refused once, delete refused
        h = Harness(root)
        interrupted(h, upto="refresh")
        real_unlink, real_read, real_sleep, refused = Path.unlink, Path.read_text, yq.time.sleep, []

        def held(self, missing_ok=False):
            if self.name.startswith("LOCK.") and self.name.endswith(".old"):
                raise PermissionError(13, "a scanner holds the file")
            return real_unlink(self, missing_ok=missing_ok)

        def busy_once(self, *a, **kw):
            if self.name.startswith("LOCK.") and self.name.endswith(".old") and not refused:
                refused.append(self.name)
                raise PermissionError(13, "a scanner holds the file")
            return real_read(self, *a, **kw)
        Path.unlink, Path.read_text, yq.time.sleep = held, busy_once, brief_naps()
        try:
            assert h.go() == 0, h.out            # the take-over of an interrupted load does not fail on either
        finally:
            Path.unlink, Path.read_text, yq.time.sleep = real_unlink, real_read, real_sleep
        assert len(refused) == 1 and "Another load took the lock" not in h.said()
        assert status.read_lock()["word_sha"] == h.spawn.sha and status.read_lock()["resume"] is True
        assert len(list(paths.ops_dir().glob("LOCK.*.old"))) == 1 and h.work() == 0


MAY_HOLD = "The database may hold part of the interrupted load until the next load."
NOTHING = ("nothing was loaded", "Nothing was written", "Nothing was loaded", "before anything was loaded",
           "before it began")                    # what nobody may be told once a refresh had begun


def says_nothing_was_written(text: str) -> list[str]:
    return [words for words in NOTHING if words in text]


def later() -> datetime:
    """A moment at which a lock nobody refreshes any more has gone stale."""
    return datetime.now(timezone.utc) + timedelta(seconds=status.HEARTBEAT_STALE_S + 5)


@test("a resume whose files or commits moved is closed: 'check again', the lock is cleared, the run is refused")
def _():
    with data_root() as root:                    # a mistyped line closes nothing: the interrupted load can still be resumed
        h = Harness(root)
        interrupted(h, upto="refresh")
        assert h.go(token="mistyped") == 3 and "GO line" in h.said()
        assert h.rows("failed") == [] and status.read_lock()["word_sha"] == "old-word" and h.w.trace == []
        assert loadplan.run_result(RUN) == "RUNNING"
        # the refresh of this load had begun: even this refusal does not say that nothing was loaded
        assert f"CHECK AGAIN. {MAY_HOLD}" in h.said() and not says_nothing_was_written(h.said()), h.out
        h.out.clear()
        assert h.go() == 0 and "RESUME" in h.box.shown[0][1]
    with data_root() as root:                    # (a) the launcher closes it: its files moved
        h = Harness(root)
        interrupted(h, upto="refresh")
        (h.w.staging / "Sales_day_book1.xlsx").write_bytes(b"replaced since")
        assert h.go() == 3
        said = h.said()
        assert "the staged copies: Sales_day_book1.xlsx" in said and "cannot be resumed" in said
        assert f"CHECK AGAIN. {MAY_HOLD} Since the table was shown, this changed:" in h.out
        assert not says_nothing_was_written(said), says_nothing_was_written(said)
        assert h.out[-1] == "Type /yq-load to check again."
        failed = h.rows("failed")[0]
        # result 'check_again' is what `load status` reads as "stopped before anything was loaded"
        assert failed["result"] == "failed" and failed["check_again"] is True
        assert failed["written"] is True and failed["step"] == "refresh"
        assert failed["text"].endswith(MAY_HOLD) and not says_nothing_was_written(whole(failed))
        assert failed["next"] == "Type /yq-load for a new check."
        assert not paths.lock_file().exists() and h.box.shown == [] and h.spawn.calls == [] and h.runner.calls == []
        h.out.clear()
        assert h.go() == 2 and "refused for good" in h.said()
    with data_root() as root:                    # the same ending before any refresh: there "nothing was loaded" is true
        h = Harness(root)
        interrupted(h, upto="pre_gate")
        (h.w.staging / "Sales_day_book1.xlsx").write_bytes(b"replaced since")
        assert h.go() == 3 and "CHECK AGAIN: nothing was loaded." in h.said() and "may hold" not in h.said()
        failed = h.rows("failed")[0]
        assert failed["result"] == "check_again" and failed["written"] is False and "may hold" not in whole(failed)


@test("a resume of a load that had begun, ended by the worker's own token check: closed, and never 'nothing was written'")
def _():
    with data_root() as root:                    # (b) he opens a report in Excel during the tick wait
        h = Harness(root, start=datetime(2026, 1, 1, 9, 10, 0, tzinfo=timezone.utc))
        interrupted(h, upto="refresh")
        h.w.newest_run = 8
        assert h.go() == 0 and "RESUME" in h.box.shown[0][1]
        h.clock.on_sleep = lambda: (h.w.drop / "~$Stock_balance1.xlsx").write_bytes(b"open in Excel")
        assert h.work() == 3, "a check-again keeps its exit code"
        assert h.runner.calls == [], "the refresh ran again although the files had moved"
        failed = h.rows("failed")[-1]
        assert failed["result"] == "failed" and failed["check_again"] is True and failed["written"] is True
        assert failed["text"] == f"CHECK AGAIN at wait_tick. {MAY_HOLD}"
        assert not says_nothing_was_written(whole(failed)), whole(failed)
        assert "the drop folder: ~$Stock_balance1.xlsx" in failed["why"] and failed["next"] == "Type /yq-load for a new check."
        assert failed["moved"] == ["the drop folder: ~$Stock_balance1.xlsx"]
        assert h.rows("step_failed", "wait_tick")[-1]["stops"] is True
        assert not paths.lock_file().exists() and loadplan.run_result(RUN) == "FAILED"      # closed: a new check is the way on
        h.out.clear()
        assert h.go() == 2 and "refused for good" in h.said()
    with data_root() as root:                    # ... at the worker's first step, seen by the launcher that started it
        h = Harness(root)
        interrupted(h, upto="refresh")
        h.inline()
        h.box.on_show = lambda: (h.w.staging / "Sales_day_book1.xlsx").write_bytes(b"swapped while the box was open")
        assert h.go() == 3, h.out
        assert "CHECK AGAIN at token" in h.out[-1] and MAY_HOLD in h.out[-1] and not says_nothing_was_written(h.said())
        assert h.rows("failed")[-1]["result"] == "failed" and h.rows("failed")[-1]["written"] is True
    with data_root() as root:                    # a FIRST go ended the same way: nothing was written, and that is said
        h = Harness(root, start=datetime(2026, 1, 1, 9, 10, 0, tzinfo=timezone.utc))
        assert h.go() == 0
        h.clock.on_sleep = lambda: (h.w.drop / "~$Stock_balance1.xlsx").write_bytes(b"open in Excel")
        assert h.work() == 3
        failed = h.rows("failed")[0]
        assert failed["result"] == "check_again" and failed["written"] is False and failed["check_again"] is True
        assert failed["text"].endswith("Nothing was written.") and "may hold" not in whole(failed)


@test("a resume of a load that had begun whose worker cannot start: no closing row, the lock is kept, it stays INTERRUPTED")
def _():
    real_read = loadplan.read_plan

    def held(run):
        raise PermissionError("the sync client holds plan.json")

    with data_root() as root:                    # (c) the worker cannot read the plan as it starts
        h = Harness(root)
        interrupted(h, upto="refresh")
        h.w.newest_run = 8
        assert h.go() == 0
        before = status.read_lock()
        buf = io.StringIO()
        real, sys.stdout = sys.stdout, buf
        loadplan.read_plan = held
        try:
            assert yq.worker(RUN, deps=None, nonce=h.spawn.nonce, parent_pid=os.getpid()) == 1
        finally:
            loadplan.read_plan, sys.stdout = real_read, real
        assert h.rows("failed") == [] and loadplan.run_result(RUN) == "RUNNING", "the interrupted load was closed"
        assert status.read_lock() == before, "the lock was released or rewritten"
        note = h.rows("note")[-1]
        assert note["text"] == f"The resume could not start: the load stays INTERRUPTED. {MAY_HOLD}"
        assert "the sync client holds plan.json" in note["why"] and note["next"] == "Type /yq-load to resume it."
        assert "launch" not in note and "step" not in note and not says_nothing_was_written(whole(note))
        assert "LOAD INTERRUPTED" in buf.getvalue() and not says_nothing_was_written(buf.getvalue())
        assert status.lock_state(later()) == {"state": "interrupted", "run": RUN, "step": "refresh"}
        h.launcher_gone()                        # the launcher has ended: every skill now opens with the banner,
        assert status.interrupted_banner() == "LOAD INTERRUPTED at refresh: run /yq-load"      # naming where the load stands
        h.out.clear()                            # ... and /yq-load resumes it
        assert h.go() == 0, h.out
        assert "interrupted at step 'refresh'" in h.box.shown[-1][1]
        assert h.work() == 0 and h.runner.labels() == ["refresh", "reconcile", "post_gate", "compare"]
        assert loadplan.run_result(RUN) == "FINISHED" and not paths.lock_file().exists()
    with data_root() as root:                    # a FIRST go whose worker cannot start: nothing was written, closed, unlocked
        h = Harness(root)
        assert h.go() == 0
        loadplan.read_plan = held
        try:
            assert h.work(deps=None) == 1
        finally:
            loadplan.read_plan = real_read
        failed = h.rows("failed")[0]
        assert failed["written"] is False and failed["text"] == "LOAD STOPPED before it began. Nothing was written to the database."
        assert failed["launch"] == h.spawn.sha[:8] and not paths.lock_file().exists()
    with data_root() as root:                    # interrupted BEFORE any refresh, worker cannot start: as a first go
        h = Harness(root)
        interrupted(h, upto="pre_gate")
        assert h.go() == 0
        loadplan.read_plan = held
        try:
            assert h.work(deps=None) == 1
        finally:
            loadplan.read_plan = real_read
        assert h.rows("failed")[0]["written"] is False and not paths.lock_file().exists()


@test("a resume of a load that had begun whose worker dies as it starts: LOAD INTERRUPTED is said and the lock stays")
def _():
    with data_root() as root:                    # (d) the launcher sees the worker gone before its first journal line
        h = Harness(root, spawn=Spawn(code=1))
        interrupted(h, upto="refresh")
        assert status.interrupted_banner() == "LOAD INTERRUPTED at refresh: run /yq-load"
        assert h.go() == 1
        said = h.said()
        assert "LOAD INTERRUPTED at refresh: the worker stopped as it started (exit code 1)." in said and MAY_HOLD in said
        assert not says_nothing_was_written(said), says_nothing_was_written(said)
        assert h.out[-1] == f"Its resume: python -m scripts.yq_load_go --run {RUN} --token {h.w.token}"
        assert h.rows("failed") == [] and loadplan.run_result(RUN) == "RUNNING"
        lock = status.read_lock()
        assert lock is not None and lock["run"] == RUN and lock["word_sha"] == h.spawn.sha, "the lock was removed"
        assert status.lock_state(later()) == {"state": "interrupted", "run": RUN, "step": "refresh"}
        h.launcher_gone()
        assert status.interrupted_banner() == "LOAD INTERRUPTED at refresh: run /yq-load"
        h.spawn._code, h.box.shown[:] = None, []     # the next go resumes it
        h.w.newest_run = 8
        assert h.go() == 0 and "interrupted at step 'refresh'" in h.box.shown[0][1]
        assert h.work() == 0 and loadplan.run_result(RUN) == "FINISHED"
    with data_root() as root:                    # ... or after its first lines: the same words about the database
        h = Harness(root)
        interrupted(h, upto="refresh")

        def dies_midway(run, nonce):
            yq.journal(run, "started", launch=yq.word_hash(nonce)[:8], pid=DEAD_PID, resume=True)
            yq.journal(run, "step_start", step="token")
            return 9
        h.spawn.inline = dies_midway
        assert h.go() == 1 and "stopped unexpectedly (exit code 9): LOAD INTERRUPTED." in h.said()
        assert MAY_HOLD in h.said() and not says_nothing_was_written(h.said()) and paths.lock_file().exists()
    with data_root() as root:                    # interrupted BEFORE any refresh: nothing was loaded, and the lock goes
        h = Harness(root, spawn=Spawn(code=1))
        interrupted(h, upto="pre_gate")
        assert h.go() == 1 and "stopped as it started (exit code 1): nothing was loaded" in h.said()
        assert "may hold" not in h.said() and not paths.lock_file().exists()


@test("interrupted before the refresh began: the database IS compared, the backup is reused, the snapshot is taken now")
def _():
    with data_root() as root:
        h = Harness(root)
        interrupted(h, upto="pre_gate")
        assert h.go() == 0 and "interrupted at step 'pre_gate'" in h.box.shown[0][1]
        assert h.w.trace == ["token:with fingerprint"] and status.read_lock()["step"] == "start"
        assert h.work() == 0
        assert h.runner.labels() == ["pre_gate", "refresh", "reconcile", "post_gate", "compare"]
        assert all(t == "token:with fingerprint" for t in h.w.trace if t.startswith("token:"))
        assert h.rows("step_ok", "backup")[-1]["reused"] is True and not h.rows("step_ok", "pre_gate")[-1].get("reused")
    with data_root() as root:                    # ... and a database that moved meanwhile is 'check again' as on a first go
        h = Harness(root)
        interrupted(h, upto="backup")
        h.w.fingerprint["orders"] = [11, 100]
        assert h.go() == 3 and "the database table orders" in h.said()
        assert h.rows("failed")[0]["written"] is False and not paths.lock_file().exists()
    with data_root() as root:                    # a journal that says it started, with no lock at all: still a resume
        h = Harness(root)
        interrupted(h, upto="refresh")
        paths.lock_file().unlink()
        assert h.go() == 0 and "RESUME" in h.box.shown[0][1]
        assert h.work() == 0 and h.runner.labels()[0] == "refresh"


@test("a resume before anything was written takes the backup or the snapshot again when its file is gone; after, never")
def _():
    with data_root() as root:                    # interrupted at the before-snapshot; the journalled archive is gone
        h = Harness(root)
        left = interrupted(h, upto="pre_gate")
        left["archive"].unlink()
        assert h.go() == 0 and h.work() == 0
        assert h.runner.labels() == ["backup", "pre_gate", "refresh", "reconcile", "post_gate", "compare"], h.runner.labels()
        assert h.w.trace.index("run:backup") < h.w.trace.index("archive") < h.w.trace.index("run:refresh")
        row = h.rows("step_ok", "backup")[-1]
        assert not row.get("reused") and Path(row["archive"]).is_file(), "the load ran with no backup on disk"
        assert Path(row["archive"]).read_bytes() == b"packed", "not a backup taken by this resume"
        note = [r["text"] for r in h.rows("note", "backup")]
        assert note == [f"The backup {left['archive'].name} is gone from where it was written: nothing was loaded yet, "
                        "so a new one is taken."]
        assert loadplan.run_result(RUN) == "FINISHED"
    with data_root() as root:                    # interrupted at the tick wait; the journalled before-snapshot is gone
        h = Harness(root)
        left = interrupted(h, upto="wait_tick")
        left["pre"].unlink()
        assert h.go() == 0 and h.work() == 0
        assert h.runner.labels() == ["pre_gate", "refresh", "reconcile", "post_gate", "compare"], h.runner.labels()
        assert h.rows("step_ok", "backup")[-1]["reused"] is True         # its archive is still there: reused
        row = h.rows("step_ok", "pre_gate")[-1]
        assert not row.get("reused") and Path(row["path"]).is_file() and Path(row["path"]) != left["pre"]
        assert h.runner.argv("compare")[6] == row["path"], "the gate compared with a snapshot that is gone"
        assert h.rows("finished")[0]["result"] == "ok" and "needs_look" not in h.rows("finished")[0]
        assert [r["text"] for r in h.rows("note", "pre_gate")] == [
            f"The before-snapshot {left['pre'].name} is gone from where it was written: nothing was loaded yet, so a new "
            "one is taken."]
    with data_root() as root:                    # both files there: both reused, nothing is taken twice
        h = Harness(root)
        interrupted(h, upto="wait_tick")
        assert h.go() == 0 and h.work() == 0
        assert h.runner.labels() == ["refresh", "reconcile", "post_gate", "compare"]
        assert h.rows("note", "backup") == [] and h.rows("note", "pre_gate") == []
    with data_root() as root:                    # the refresh HAD begun: a backup now would hold a half-loaded state
        h = Harness(root)
        left = interrupted(h, upto="refresh")
        left["archive"].unlink()
        left["pre"].unlink()
        h.w.newest_run = 8
        assert h.go() == 0 and h.work() == 0
        assert h.runner.labels() == ["refresh", "reconcile", "post_gate"], h.runner.labels()      # nothing retaken, nothing to compare
        assert "archive" not in h.w.trace
        assert h.rows("step_ok", "backup")[-1]["reused"] is True and h.rows("step_ok", "pre_gate")[-1]["reused"] is True
        assert [r["text"] for r in h.rows("note", "backup")] == [
            f"The pre-load backup {left['archive'].name} is no longer where it was written."]
        assert "the gate will have nothing to compare with" in h.rows("note", "pre_gate")[0]["text"]
        assert h.rows("finished")[0]["needs_look"] == ["post_gate"]
        assert "no before-snapshot to compare with" in h.rows("step_failed", "post_gate")[0]["text"]
    with data_root() as root:                    # ... and a second resume after that one says the same, not "?"
        h = Harness(root)
        interrupted(h, upto="refresh")
        yq.journal(RUN, "step_ok", step="backup", seconds=0.0, text="None taken.", reused=True)     # as a resume with no backup wrote it
        yq.journal(RUN, "step_ok", step="pre_gate", seconds=0.0, text="None taken.", reused=True)
        h.w.newest_run = 8
        assert h.go() == 0 and h.work() == 0
        assert "No backup is journalled for this run" in h.rows("step_ok", "backup")[-1]["text"]
        assert "No before-snapshot is journalled for this run" in h.rows("step_ok", "pre_gate")[-1]["text"]
        assert h.runner.labels() == ["refresh", "reconcile", "post_gate"]


# ── what the other commands read of a load that did not finish ────────────────────────────────

def last_failed(step: str, run: str = RUN) -> str:
    """The line every command opens with (scripts.ops.status.interrupted_banner) after such a run."""
    return f"LAST LOAD FAILED at {step} (run {run}): the database may hold part of it: run /yq-load"


@test("a run closed as failed after its refresh had begun: step and written as scripts.ops.status.failed_load() reads them")
def _():
    told = "The data loaded, but these totals do not match the reports: Sales"
    with data_root() as root:                    # closed by the worker: the verify failed
        h = Harness(root)
        assert status.failed_load() is None and status.interrupted_banner() is None
        h.runner.script["refresh"] = [verify_fails_with(h, told)]
        assert h.go() == 0 and h.work() == 1
        closing = h.rows()[-1]
        assert closing["event"] == "failed" and closing["step"] == "refresh" and closing["written"] is True
        assert status.lock_state() == {"state": "none"}, "the lock of a closed run was left"
        assert status.failed_load() == {"run": RUN, "step": "refresh"}
        assert status.interrupted_banner() == last_failed("refresh")
        # /yq-load is the way on: this run is refused for good, a new check is a new run, and once THAT one has
        # loaded, nothing says FAILED any more
        h.out.clear()
        assert h.go() == 2 and "refused for good" in h.said()
        after = Harness(root, run="020126_0900")
        assert after.load() == 0, after.rows()[-1]
        assert status.failed_load() is None and status.interrupted_banner() is None
    with data_root() as root:                    # the load stage failed twice: the database may hold part of the load
        h = Harness(root)
        crash = yq.Child(1, "Traceback (most recent call last):\nMemoryError\n")
        h.runner.script["refresh"] = [crash, crash]
        assert h.go() == 0 and h.work() == 1
        assert h.rows()[-1]["written"] is True and status.failed_load() == {"run": RUN, "step": "refresh"}
    with data_root() as root:                    # loaded and verified, then closed: its records were not as they must be
        h = Harness(root)
        h.runner.script["refresh"] = [lambda argv: yq.Child(0, refresh_line() + "\n")]       # no load record was written
        assert h.go() == 0 and h.work() == 1
        assert h.rows()[-1]["written"] is True and status.interrupted_banner() == last_failed("refresh")
    for label in ("backup", "pre_gate"):         # closed BEFORE anything was written: there is nothing to make whole
        with data_root() as root:
            h = Harness(root)
            h.runner.script[label] = [yq.Child(1, "no database\n")]
            assert h.go() == 0 and h.work() == 1
            closing = h.rows()[-1]
            assert closing["event"] == "failed" and closing["written"] is False and closing["step"] == label
            assert status.failed_load() is None and status.interrupted_banner() is None
    with data_root() as root:                    # a check-again on a first go: nothing was written either
        h = Harness(root)
        h.runner.hooks["pre_gate"] = lambda: h.w.fingerprint.update(orders=[11, 100])
        assert h.go() == 0 and h.work() == 3
        assert h.rows()[-1]["result"] == "check_again" and status.failed_load() is None
        assert status.interrupted_banner() is None
    with data_root() as root:                    # an interrupted load that had begun, closed by the launcher: its files moved
        h = Harness(root)
        interrupted(h, upto="refresh")
        assert status.interrupted_banner() == "LOAD INTERRUPTED at refresh: run /yq-load" and status.failed_load() is None
        (h.w.staging / "Sales_day_book1.xlsx").write_bytes(b"replaced since")
        assert h.go() == 3
        closing = h.rows()[-1]
        assert closing["event"] == "failed" and closing["step"] == "refresh" and closing["written"] is True
        assert status.failed_load() == {"run": RUN, "step": "refresh"}
        assert status.interrupted_banner() == last_failed("refresh")          # INTERRUPTED became FAILED: never silence
    with data_root() as root:                    # ... or closed by its resumed worker's own check, at the step where it stood
        h = Harness(root, start=datetime(2026, 1, 1, 9, 10, 0, tzinfo=timezone.utc))
        interrupted(h, upto="refresh")
        assert h.go() == 0
        h.clock.on_sleep = lambda: (h.w.drop / "~$Stock_balance1.xlsx").write_bytes(b"open in Excel")
        assert h.work() == 3
        assert status.failed_load() == {"run": RUN, "step": "wait_tick"}
        assert status.interrupted_banner() == last_failed("wait_tick")
    with data_root() as root:                    # interrupted BEFORE any refresh, then closed: nothing was written
        h = Harness(root)
        interrupted(h, upto="pre_gate")
        (h.w.staging / "Sales_day_book1.xlsx").write_bytes(b"replaced since")
        assert h.go() == 3 and h.rows()[-1]["written"] is False
        assert status.failed_load() is None and status.interrupted_banner() is None
    with data_root() as root:                    # a resume whose worker never got going is NOT closed: it stays INTERRUPTED
        h = Harness(root, spawn=Spawn(code=1))
        interrupted(h, upto="refresh")
        assert h.go() == 1
        h.launcher_gone()
        assert status.failed_load() is None and h.rows("failed") == []
        assert status.interrupted_banner() == "LOAD INTERRUPTED at refresh: run /yq-load"


@test("the journal as `load status` shows it (text, why and next, nothing else): a normal run, a failed verify, "
      "a check-again, an interrupted resume")
def _():
    assert set(yq.STEP_OPENS) == set(loadplan.STEPS) and set(yq.REHEARSAL_OPENS) <= set(loadplan.STEPS)
    assert all(len(s) <= yq.FIELD and s.endswith(".") for s in list(yq.STEP_OPENS.values()) + list(yq.REHEARSAL_OPENS.values()))
    backup = "yq-backup_2026-01-01_pre-data-010126_0900.7z"
    before = "2026-01-01_090100_pre-data-010126.json"
    normal = [
        "Load started.",
        "Checking that nothing changed since the table was shown.",
        "Files, programs and database are as the table showed. The loader's work folder (data/clean) is empty.",
        "Making sure there is a backup of every table from before the load.",
        f"Backup of every table packed, encrypted and read back: {backup}. Kept: the newest 2.",
        "Making sure there is a before-snapshot of the records a load must never change.",
        f"Before-snapshot written: {before}.",
        "Checking once more that nothing changed, then waiting for a quiet moment between two production ticks.",
        "No wait needed: production's next tick is at least 6 minutes away. The table still holds.",
        "Loading the reports and verifying their totals: from this line on the database is being changed.",
        "Loaded and verified. Sales data to 2025-12-31.",
        "Comparing the portal's numbers with the Focus reports.",
        "Reconciled: the portal's numbers match the Focus reports.",
        "Taking the after-snapshot and comparing it with the before-snapshot.",
        "Gate passed: no protected record changed (1 explained change).",
        "Copying the loaded set to business_data and recording the load.",
        "Published as 'Focus ERP 2026-01-01'.",
        "LOAD FINISHED. Sales data to 2025-12-31. Published as 'Focus ERP 2026-01-01'.",
    ]
    with data_root() as root:                    # 1. a normal run: two lines a step, what it is about and how it ended
        h = Harness(root)
        assert h.load() == 0
        assert h.shown() == normal, "\n".join(h.shown())
        assert [r["event"] for r in h.rows()] == ["started"] + ["step_start", "step_ok"] * len(loadplan.STEPS) + ["finished"]
    with data_root() as root:                    # 2. a failed verify: what differs, each total, and what is so now
        h = Harness(root)
        h.runner.script["refresh"] = [verify_fails_with(
            h, "The data loaded, but these totals do not match the reports: Sales; Stock value")]
        assert h.go() == 0 and h.work() == 1
        said = h.shown()
        assert said[:10] == normal[:10] and said[10:] == [
            "Totals differ from the reports: Sales; Stock value.",
            "Differs from the reports: Sales",
            "Differs from the reports: Stock value",
            "LOAD FAILED at refresh. The data is in the database, but its totals do not match the reports. Totals differ "
            "from the reports: Sales; Stock value. This run is closed. Ask Claude to look into these totals before "
            "anything is loaded again.",
        ], "\n".join(said[10:])
    with data_root() as root:                    # 3. a check-again: what changed, that nothing was written, what to type
        h = Harness(root)
        h.runner.hooks["pre_gate"] = lambda: h.w.fingerprint.update(orders=[11, 100])       # a portal upload meanwhile
        assert h.go() == 0 and h.work() == 3
        said = h.shown()
        assert said[:8] == normal[:8] and said[8:] == [
            "This changed since the table was shown: the database table orders.",
            "CHECK AGAIN at wait_tick. Nothing was written. This changed since the table was shown: the database table "
            "orders. Type /yq-load to check again.",
        ], "\n".join(said[8:])
    with data_root() as root:                    # 4. an interrupted load and its resume
        h = Harness(root)
        cut_off(h, "refresh")                    # the real worker, cut off inside the load itself
        assert h.shown() == normal[:10], "\n".join(h.shown())           # the last line he reads: the database is being changed
        assert status.interrupted_banner() == "LOAD INTERRUPTED at refresh: run /yq-load"
        assert h.go() == 0, h.out
        assert "RESUME: the load of this folder was interrupted at step 'refresh'." in h.box.shown[0][1].splitlines()
        assert h.work() == 0
        resumed = [
            "Load resumed: it had stopped at refresh; the whole load runs again.",
            normal[1],
            "Files and programs are as the table showed (a resume does not compare the database). The loader's work "
            "folder (data/clean) is empty.",
            normal[3],
            f"The backup taken before the interruption stays the baseline: {backup}.",
            normal[5],
            f"The before-snapshot taken before the interruption stays the baseline: {before}.",
        ] + normal[7:]
        assert h.shown(10) == resumed, "\n".join(h.shown(10))
        assert h.runner.labels() == ["refresh", "reconcile", "post_gate", "compare"] and status.interrupted_banner() is None
    with data_root() as root:                    # ... interrupted and resumed where the resume cannot go on: closed in three lines
        h = Harness(root)
        cut_off(h, "refresh")
        (h.w.staging / "Sales_day_book1.xlsx").write_bytes(b"replaced since")
        assert h.go() == 3
        assert h.shown(10) == [
            "This interrupted load cannot be resumed: something changed since its table was shown. The database may hold "
            "part of the interrupted load until the next load. This changed: the staged copies: Sales_day_book1.xlsx. Type "
            "/yq-load for a new check."], "\n".join(h.shown(10))
    with data_root() as root:                    # a step that needs a look, and the load that goes on to its end
        h = Harness(root)
        h.runner.script["compare"] = [yq.Child(1, "  FAIL:  order 12 (ORD-12) DISAPPEARED\n\nFAIL: 1 failure(s)\n")]
        assert h.load() == 0
        assert h.shown()[13:] == [
            normal[13],
            "Gate needs a look. The load goes on.",
            "The gate says: FAIL: order 12 (ORD-12) DISAPPEARED",
            "The gate says: FAIL: 1 failure(s)",
            normal[15],
            normal[16],
            "LOAD FINISHED. Sales data to 2025-12-31. Published as 'Focus ERP 2026-01-01'. Needs a look: post_gate. The "
            "data is loaded and verified: ask Claude to look into the 'needs a look' lines above.",
        ], "\n".join(h.shown()[13:])
    with data_root() as root:                    # a load stage that failed once: said, run again, and passed
        h = Harness(root)
        h.runner.script["refresh"] = [yq.Child(2, refresh_line(ok=False, verify_ok=None, error="The load step failed.") + "\n")]
        assert h.load() == 0
        assert h.shown()[9:12] == [
            normal[9],
            "The load stage failed: the identical load runs once more. The load step failed.",
            "The identical load runs once more.",
        ] and h.shown()[12] == "Loaded and verified. Sales data to 2025-12-31. (It passed on the second run.)"


# ── the lock and its heartbeat ────────────────────────────────────────────────────────────────

@test("the lock has the shape scripts.ops.status reads, names the step, and its heartbeat moves while a step runs")
def _():
    with data_root() as root:
        h = Harness(root, heartbeat_s=0.02)
        seen = {}
        real_now = yq.LockFile.__dict__["_now"]
        yq.LockFile._now = staticmethod(lambda: datetime.now(timezone.utc).isoformat())       # finer than a second

        def during_refresh() -> None:
            # read the way the writer itself reads a lock that a heartbeat is rewriting (here every
            # 20 ms): on Windows a read that meets the very moment of the replace is refused once
            first = yq._again(status.read_lock)
            seen["state"] = yq._again(status.lock_state)
            seen["keys"] = set(first)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                now = yq._again(status.read_lock)
                if now and now["heartbeat"] != first["heartbeat"]:
                    seen["beat"] = (first["heartbeat"], now["heartbeat"], now["step"], now["pid"])
                    break
                time.sleep(0.01)
        h.runner.hooks["refresh"] = during_refresh
        h.runner.hooks["backup"] = lambda: seen.update(backup=yq._again(status.lock_state))
        try:
            assert h.load() == 0
        finally:
            yq.LockFile._now = real_now
        assert seen["state"] == {"state": "running", "run": RUN, "step": "refresh"}, seen["state"]
        assert seen["backup"] == {"state": "running", "run": RUN, "step": "backup"}
        assert {"run", "pid", "step", "started", "heartbeat", "pid_created", "word_sha"} <= seen["keys"]
        assert "nonce" not in seen["keys"]
        assert "beat" in seen, "the heartbeat did not move while the step ran"
        a, b, step, pid = seen["beat"]
        assert datetime.fromisoformat(b) > datetime.fromisoformat(a) and step == "refresh" and pid == os.getpid()
        assert not paths.lock_file().exists()
        time.sleep(0.1)
        assert not paths.lock_file().exists(), "a late heartbeat brought the lock back"
    with data_root():                            # the reader's own rules, on a lock written by the writer
        lk = yq.LockFile(RUN, "n1", {"step": "backup"})
        lk.create()
        now = datetime.now(timezone.utc)
        assert status.lock_state(now)["state"] == "running"
        assert status.lock_state(now + timedelta(seconds=status.HEARTBEAT_STALE_S + 5))["state"] == "interrupted"
        assert yq.HEARTBEAT_S * 6 <= status.HEARTBEAT_STALE_S
        assert yq.worker_alive(status.read_lock()) is True
        assert lk.update(step="refresh") is True and status.read_lock()["step"] == "refresh"
        assert lk.release() is True


@test("a lock read that meets the heartbeat's replace is tried again: a busy file is never a reason to stop")
def _():
    real_read, real_sleep = status.read_lock, yq.time.sleep
    naps: list[float] = []

    def busy_then(answer, times: int):
        left = {"n": times}

        def read():
            if left["n"] > 0:
                left["n"] -= 1
                raise PermissionError(13, "the worker is replacing the lock at this very moment")
            return answer
        return read

    yq.time.sleep = naps.append
    try:
        assert yq._again(busy_then({"run": RUN}, 3)) == {"run": RUN} and len(naps) == 3 and max(naps) < 1
        assert yq._again(busy_then(None, 0)) is None
        raises(PermissionError, lambda: yq._again(busy_then({}, 99)))          # truly unreadable: said, not hidden
        with data_root() as root:                # the go, while a running load's lock is busy for a moment
            h = Harness(root)
            fresh = datetime.now(timezone.utc).isoformat(timespec="seconds")
            lock = {"run": "311225_0900", "pid": os.getpid(), "pid_created": yq.process_created(os.getpid()),
                    "step": "refresh", "word_sha": "n", "heartbeat": fresh}
            paths.write_text(paths.lock_file(), json.dumps(lock))
            status.read_lock = busy_then(lock, 2)
            assert h.go() == 2 and "A load is running (run 311225_0900, at refresh)" in h.said(), h.out
    finally:
        status.read_lock, yq.time.sleep = real_read, real_sleep


# ── children ──────────────────────────────────────────────────────────────────────────────────

KEY = "TELEGRAM_BOT_TOKEN"                           # one of the keys scripts.ops.db blanks in the process that imports it


class launched_with:
    """For one test: the environment this program was 'started with' (yq.LAUNCH_ENV), with changes."""

    def __init__(self, **changes):
        self.changes = changes

    def __enter__(self) -> dict:
        self.real = dict(yq.LAUNCH_ENV)
        yq.LAUNCH_ENV.update(self.changes)
        return yq.LAUNCH_ENV

    def __exit__(self, *exc):
        yq.LAUNCH_ENV.clear()
        yq.LAUNCH_ENV.update(self.real)
        return False


@test("the child environment: what this program was STARTED with, PYTHONUTF8=1, no PYTHONIOENCODING, none of the launcher's words")
def _():
    env = yq.child_env({"PATH": "x", "PYTHONIOENCODING": "utf-8:surrogateescape", "PYTHONUTF8": "0",
                        yq.NONCE_ENV: "secret", yq.BREAKAWAY_ENV: "1", "DATABASE_URL": "postgresql://ci.invalid/x"})
    assert env == {"PATH": "x", "PYTHONUTF8": "1", "DATABASE_URL": "postgresql://ci.invalid/x"}, env
    assert KEY in db.SIDE_CHANNEL_KEYS and os.environ[KEY] == "", "scripts.ops.db blanks this key in this process"
    assert yq.NONCE_ENV not in yq.LAUNCH_ENV and yq.BREAKAWAY_ENV not in yq.LAUNCH_ENV
    os.environ["SET_AFTER_THE_START"] = "x"
    try:
        with launched_with(**{KEY: "a key of the launching shell", "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}):
            # with no base it is the copy made when the program started, never os.environ as it is by now
            live = yq.child_env()
            assert live[KEY] == "a key of the launching shell" and os.environ[KEY] == ""
            assert live["PYTHONUTF8"] == "1" and "PYTHONIOENCODING" not in live and "SET_AFTER_THE_START" not in live
            assert live["SUPABASE_URL"] == "https://ci.invalid"
            assert yq.LAUNCH_ENV["PYTHONIOENCODING"] == "cp1252" and yq.LAUNCH_ENV["PYTHONUTF8"] == "0", "the copy itself changed"
            # every engine step is started with exactly that
            seen: dict = {}
            real_run = yq.run_child
            yq.run_child = lambda argv, **kw: seen.update(kw, argv=argv) or yq.Child(0, "", False)
            try:
                assert yq.RealRunner(ROOT, RUN)("backup", ["x", "y"], 5).code == 0
            finally:
                yq.run_child = real_run
            assert seen["env"] == live and seen["cwd"] == ROOT and seen["timeout"] == 5 and seen["argv"] == ["x", "y"]
            # and so is the worker (it is the one that starts the engine steps), with the launcher's two words on top
            started: list = []

            class Popen:
                def __init__(self, argv, **kw):
                    started.append((list(argv), kw))
                    self.pid = 1
            real_popen = yq.subprocess.Popen
            yq.subprocess.Popen = Popen
            try:
                with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                    yq.spawn_worker(RUN, ROOT, "the one-time word", Path(tmp) / "worker.log")
            finally:
                yq.subprocess.Popen = real_popen
            argv, kw = started[0]
            python, needs = yq.worker_python()
            assert argv == [python, "-X", "utf8", "-m", "scripts.yq_load_go", "--worker", RUN] and len(started) == 1
            handed = kw["env"]
            assert handed[KEY] == "a key of the launching shell" and handed[yq.NONCE_ENV] == "the one-time word"
            assert handed["PYTHONUTF8"] == "1" and "PYTHONIOENCODING" not in handed and "SET_AFTER_THE_START" not in handed
            assert all(handed[k] == v for k, v in needs.items())
            own = {yq.NONCE_ENV, yq.BREAKAWAY_ENV} | set(needs)
            assert {k: v for k, v in handed.items() if k not in own} == live
            assert kw["cwd"] == str(ROOT) and kw.get("shell") is False and kw["stdin"] == subprocess.DEVNULL
            assert yq.NONCE_ENV not in yq.LAUNCH_ENV, "the word was left in the copy"
    finally:
        os.environ.pop("SET_AFTER_THE_START", None)
    assert KEY not in yq.LAUNCH_ENV or yq.LAUNCH_ENV[KEY] != "a key of the launching shell"


@test("the engine's environment is the one the program was started with, though scripts.ops.db has blanked os.environ "
      "(a fresh interpreter)")
def _():
    # In a new process, exactly as the worker does it: this program is imported (it copies the environment in its
    # first lines), THEN the guard module blanks the keys of the process, and then an engine step is started.
    program = (
        "import json, os, sys\n"
        "from scripts import yq_load_go as yq\n"
        "from scripts.ops import db\n"
        "handed = {}\n"
        "real = yq.run_child\n"
        "def run_child(argv, **kw):\n"
        "    handed.update(kw['env'])\n"
        "    return real(argv, **kw)\n"
        "yq.run_child = run_child\n"
        "probe = \"import os; e = os.environ.get; print('CHILD', e('TELEGRAM_BOT_TOKEN'), e('GROQ_API_KEY'), "
        "e('PYTHONUTF8'), e('PYTHONIOENCODING'), e('YQ_LOAD_GO_NONCE'))\"\n"
        "child = yq.RealRunner(yq.ROOT, '010126_0900', lambda line: None)('probe', [sys.executable, '-c', probe], 60)\n"
        "print('RESULT ' + json.dumps({\n"
        "    'process': [os.environ.get('TELEGRAM_BOT_TOKEN'), os.environ.get('GROQ_API_KEY')],\n"
        "    'handed': [handed.get('TELEGRAM_BOT_TOKEN'), handed.get('GROQ_API_KEY', 'ABSENT'), handed.get('PYTHONUTF8'),\n"
        "               'PYTHONIOENCODING' in handed, 'YQ_LOAD_GO_NONCE' in handed],\n"
        "    'child': child.text.strip(), 'code': child.code,\n"
        "    'copy_is_first': 'YQ_LOAD_GO_NONCE' not in yq.LAUNCH_ENV and yq.LAUNCH_ENV.get('TELEGRAM_BOT_TOKEN')}))\n")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        env = dict(os.environ, YQ_MAIN_CHECKOUT=tmp, USERPROFILE=tmp, HOME=tmp, PYTHONIOENCODING="cp1252")
        env["TELEGRAM_BOT_TOKEN"] = "a key of the launching shell"          # set where the program is started
        env.pop("GROQ_API_KEY", None)                                        # not set there: the engine reads its .env
        env[yq.NONCE_ENV] = "the word a launcher handed its worker"          # as in a worker's own environment
        cp = subprocess.run([sys.executable, "-X", "utf8", "-c", program], cwd=str(ROOT), env=env, capture_output=True,
                            timeout=180)
        text = cp.stdout.decode("utf-8", "replace") + cp.stderr.decode("utf-8", "replace")
        assert cp.returncode == 0, text
        result = json.loads([ln for ln in text.splitlines() if ln.startswith("RESULT ")][-1][len("RESULT "):])
    assert result["process"] == ["", ""], "scripts.ops.db did not blank the keys of the process itself"
    # the step got the key as it was set, did NOT get a blank for the key that was not set, and none of the launcher's words
    assert result["handed"] == ["a key of the launching shell", "ABSENT", "1", False, False], result
    assert result["child"] == "CHILD a key of the launching shell None 1 None None" and result["code"] == 0, result
    assert result["copy_is_first"] == "a key of the launching shell", result


@test("inside a Windows virtual environment the worker is started with the real interpreter, as the launcher's own child")
def _():
    names = ("executable", "_base_executable", "prefix", "base_prefix")
    missing = object()
    real = {n: getattr(sys, n, missing) for n in names}
    real_os = yq.os.name

    def start(os_name: str, **values) -> tuple[str, dict]:
        try:
            yq.os.name = os_name
            for n, v in values.items():
                if v is missing:
                    if hasattr(sys, n):
                        delattr(sys, n)
                else:
                    setattr(sys, n, v)
            return yq.worker_python()
        finally:
            yq.os.name = real_os
            for n, v in real.items():
                if v is missing:
                    if hasattr(sys, n):
                        delattr(sys, n)
                else:
                    setattr(sys, n, v)

    venv = dict(executable=r"C:\work\venv\Scripts\python.exe", _base_executable=r"C:\Python\python.exe",
                prefix=r"C:\work\venv", base_prefix=r"C:\Python")
    # there sys.executable is a redirector that starts the real interpreter as ITS child: the worker would not be
    # the launcher's own child (which it checks before it loads), so the real one is started and told where it belongs
    assert start("nt", **venv) == (r"C:\Python\python.exe", {"__PYVENV_LAUNCHER__": r"C:\work\venv\Scripts\python.exe"})
    plain = dict(executable=r"C:\Python\python.exe", _base_executable=r"C:\Python\python.exe", prefix=r"C:\Python",
                 base_prefix=r"C:\Python")
    assert start("nt", **plain) == (r"C:\Python\python.exe", {})                       # no environment: as it is
    assert start("posix", **venv) == (r"C:\work\venv\Scripts\python.exe", {})          # elsewhere it is no redirector
    assert start("nt", **dict(venv, _base_executable=missing)) == (r"C:\work\venv\Scripts\python.exe", {})
    assert start("nt", **dict(venv, _base_executable=None)) == (r"C:\work\venv\Scripts\python.exe", {})
    assert start("nt", **dict(venv, _base_executable=r"c:\WORK\venv\Scripts\PYTHON.exe")) == \
        (r"C:\work\venv\Scripts\python.exe", {}) or os.name != "nt"                    # the same file, written another way
    assert sys.executable == real["executable"] and yq.os.name == real_os
    python, needs = yq.worker_python()                                                 # as this very test run is started
    if os.name == "nt" and sys.prefix != sys.base_prefix:
        # this run IS inside such an environment: the real interpreter, told where it belongs, is our own child
        # (no process in between) and is that same environment
        assert python != sys.executable and needs == {"__PYVENV_LAUNCHER__": sys.executable}, (python, needs)
        probe = "import os, sys; print(os.getppid(), sys.prefix == sys.argv[1], sys.executable == sys.argv[2])"
        cp = subprocess.run([python, "-c", probe, sys.prefix, sys.executable], env=dict(yq.child_env(), **needs),
                            capture_output=True, timeout=60)
        assert cp.stdout.decode("utf-8", "replace").split() == [str(os.getpid()), "True", "True"], cp.stdout + cp.stderr
    else:
        assert (python, needs) == (sys.executable, {})


@test("child output is read as bytes: a cp1252 em dash, UTF-8 and the exit code all come through; a time limit ends it")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        program = ("import os, sys\n"
                   "sys.stdout.buffer.write(b'RECONCILED \\x97 platform numbers match\\n')\n"
                   "sys.stdout.buffer.write('utf8 \\u2014 dash\\n'.encode('utf-8'))\n"
                   "sys.stdout.buffer.flush()\n"
                   "print(os.environ.get('PYTHONUTF8'), os.environ.get('PYTHONIOENCODING'), sys.flags.utf8_mode)\n"
                   "print(os.path.realpath(os.getcwd()), flush=True)\n"
                   "sys.stderr.write('to stderr\\n')\n"
                   "sys.exit(3)\n")
        log: list[str] = []
        runner = yq.RealRunner(Path(tmp), RUN, log.append)
        with launched_with(PYTHONIOENCODING="cp1252"):               # as when the PowerShell tool starts the go
            child = runner("probe", [sys.executable, "-c", program])
        lines = child.text.splitlines()
        assert child.code == 3 and child.timed_out is False
        assert lines[0] == "RECONCILED \ufffd platform numbers match", lines[0]
        assert lines[1] == "utf8 \u2014 dash" and lines[2] == "1 None 1"
        assert lines[3] == os.path.realpath(tmp) and "to stderr" in child.text
        assert any(ln.startswith("RECONCILED") for ln in lines)
        assert log and all(ln.startswith("  | ") for ln in log)
        t0 = time.monotonic()
        slow = yq.run_child([sys.executable, "-c", "import time; print('going', flush=True); time.sleep(60)"],
                            cwd=Path(tmp), env=yq.child_env(), timeout=1.0)
        assert slow.timed_out is True and "going" in slow.text and time.monotonic() - t0 < 30


@test("an em dash, a traceback or odd bytes in a step's output never stop the worker")
def _():
    with data_root() as root:
        h = Harness(root)
        odd = b"caf\xe9 \x97 \xff".decode("utf-8", errors="replace")
        h.runner.script["reconcile"] = [yq.Child(0, f"{odd}\nRECONCILED \u2014 platform numbers match the Focus reports.\n")]
        h.runner.script["backup"] = [lambda argv: yq.Child(0, "\u2265 80% join \u2014 fine\n" + h.runner.ok_backup(argv).text)]
        assert h.load() == 0 and h.rows("finished")[0]["result"] == "ok"
        assert json.loads(loadplan.journal_file(RUN).read_text(encoding="utf-8").splitlines()[0])["event"] == "started"


@test("the refresh child's own program: one REFRESH_RESULT line from refresh(folder, send=False)")
def _():
    compile(yq.REFRESH_PROGRAM, "<refresh child>", "exec")
    assert "refresh(sys.argv[1], send=False)" in yq.REFRESH_PROGRAM
    fake = ("import json\n"
            "def refresh(folder=None, send=True):\n"
            "    print('=== Data refreshed \\u2014 ' + str(folder) + ' ===')\n"
            "    mode = open('mode.txt', encoding='utf-8').read().strip()\n"
            "    open('seen.json', 'w', encoding='utf-8').write(json.dumps({'folder': folder, 'send': send}))\n"
            "    if mode == 'ok':\n"
            "        return {'ok': True, 'data_as_of': '2025-12-31', 'error': None, 'detail': None, 'loaded': {'Sales_day_book': 40},\n"
            "                'changes': {'loader': [], 'catalog': 'x'}, 'verify': {'ok': True, 'rows': []}, 'notified': {}}\n"
            "    if mode == 'verify':\n"
            "        return {'ok': False, 'data_as_of': '2025-12-31', 'error': 'totals do not match: Sales', 'loaded': {'a': 1},\n"
            "                'changes': {'loader': ['span replace skipped']}, 'verify': {'ok': False, 'rows': []}}\n"
            "    if mode in ('not_run', 'not_run_no_words'):\n"                # as scripts/refresh.py leaves it when run_checks raised
            "        why = 'the database did not answer' if mode == 'not_run' else ''\n"
            "        return {'ok': False, 'data_as_of': '2025-12-31', 'error': 'Verification could not run: ' + (why or 'unknown'),\n"
            "                'loaded': {'a': 1}, 'changes': {}, 'verify': {'ok': False, 'rows': [], 'error': why}}\n"
            "    return {'ok': False, 'data_as_of': None, 'error': 'The load step failed.', 'loaded': {}, 'changes': {}, 'verify': None}\n")
    want = {"ok": {"ok": True, "data_as_of": "2025-12-31", "error": None, "loaded": {"Sales_day_book": 40},
                   "verify_ok": True, "verify_error": None, "loader_notices": []},
            "verify": {"ok": False, "data_as_of": "2025-12-31", "error": "totals do not match: Sales", "loaded": {"a": 1},
                       "verify_ok": False, "verify_error": None, "loader_notices": ["span replace skipped"]},
            # the verification could not RUN: told apart from totals that differ, even when the error has no words
            "not_run": {"ok": False, "data_as_of": "2025-12-31", "error": "Verification could not run: the database did not answer",
                        "loaded": {"a": 1}, "verify_ok": False, "verify_error": "the database did not answer",
                        "loader_notices": []},
            "not_run_no_words": {"ok": False, "data_as_of": "2025-12-31", "error": "Verification could not run: unknown",
                                 "loaded": {"a": 1}, "verify_ok": False, "verify_error": "no detail given",
                                 "loader_notices": []},
            "load": {"ok": False, "data_as_of": None, "error": "The load step failed.", "loaded": {}, "verify_ok": None,
                     "verify_error": None, "loader_notices": []}}
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "scripts").mkdir()
        (t / "scripts" / "refresh.py").write_text(fake, encoding="utf-8")       # a stand-in engine, in a stand-in checkout
        for mode, expected in want.items():
            (t / "mode.txt").write_text(mode, encoding="utf-8")
            child = yq.run_child([sys.executable, "-X", "utf8", "-c", yq.REFRESH_PROGRAM, "staging folder"],
                                 cwd=t, env=yq.child_env())
            assert yq.parse_refresh(child.text) == expected, (mode, child.text)
            assert child.code == (0 if mode == "ok" else 2)
            assert json.loads((t / "seen.json").read_text(encoding="utf-8")) == {"folder": "staging folder", "send": False}
            assert [ln for ln in child.text.splitlines() if ln.startswith("REFRESH_RESULT ")][-1].isascii()
        # the one transaction: the two words reach the loader through the refresh's environment, and only then
        (t / "scripts" / "refresh.py").write_text(
            "import json, os\n"
            "def refresh(folder=None, send=True):\n"
            f"    open('env.json', 'w').write(json.dumps([os.environ.get({yq.ONE_TXN_ENV!r}), os.environ.get({yq.AVOID_ENV!r})]))\n"
            "    return {'ok': True, 'data_as_of': None, 'error': None, 'loaded': {}, 'changes': {}, 'verify': {'ok': True}}\n",
            encoding="utf-8")
        env = {k: v for k, v in yq.child_env().items() if k not in (yq.ONE_TXN_ENV, yq.AVOID_ENV)}
        for extra, want in ((["--one-transaction", "1760941890-1760941995"], ["1", "1760941890-1760941995"]),
                            (["--one-transaction"], ["1", ""]), ([], [None, None])):
            child = yq.run_child([sys.executable, "-X", "utf8", "-c", yq.REFRESH_PROGRAM, "staging folder"] + extra,
                                 cwd=t, env=env)
            assert child.code == 0 and json.loads((t / "env.json").read_text()) == want, (extra, child.text)
    assert yq.parse_refresh("noise\nREFRESH_RESULT not json\n") is None and yq.parse_refresh("no line") is None
    assert yq.parse_refresh("REFRESH_RESULT [1]\n") is None


# ── the rehearsal ─────────────────────────────────────────────────────────────────────────────

@test("a rehearsal can construct no real runner, and a real run can construct no rehearsal")
def _():
    with data_root() as root:
        plan = {"rehearsal": True, "seconds": 0}
        deps = yq.deps_for("000000_0900", plan)
        assert deps.rehearsal is True and type(deps.runner) is yq.StubRunner and deps.wait_for_tick is False
        assert deps.prepare is None
        box = loadplan.run_dir("000000_0900")
        for p in (deps.gates_dir, deps.staging, deps.clean_dir, deps.superseded_dir, deps.backup_root(),
                  deps.published_dir(datetime(2026, 1, 1).date())):
            assert box in Path(p).parents, p
        raises(RuntimeError, lambda: yq.real_deps("000000_0900", plan))
        raises(RuntimeError, lambda: yq.real_deps("000000_0900", {}))
        raises(RuntimeError, lambda: yq.real_deps(RUN, {"rehearsal": True}))
        raises(RuntimeError, lambda: yq.RealRunner(root, "000000_0900"))
        raises(RuntimeError, lambda: yq.rehearsal_deps(RUN, plan))
        raises(yq.Refusal, lambda: yq.is_rehearsal("000000_0900", {}))             # named like one, not marked
        raises(yq.Refusal, lambda: yq.is_rehearsal(RUN, {"rehearsal": True}))       # marked, not named like one
        assert paths.drop_date("000000") is None, "a drop folder could spell the rehearsal's name"
        real = yq.deps_for(RUN, {})                                                 # built, never called: no connection
        assert type(real.runner) is yq.RealRunner and real.rehearsal is False and real.wait_for_tick is True
        # a real load writes in one transaction (and takes its window); a rehearsal says what such a load would wait
        assert yq.ONE_TRANSACTION is True and real.one_transaction is True and deps.one_transaction is True
        assert real.gates_dir == yq.ROOT / "business_data" / "gates" and real.clean_dir == yq.ROOT / "data" / "clean"
        assert real.staging == loadplan.staging_path(RUN) and real.backup_keep() == 2
        assert real.published_dir(datetime(2026, 1, 1).date()) == paths.published_dir(datetime(2026, 1, 1).date())
        # the stub answers like a passing step, starts nothing, and writes only inside its run folder
        stub = yq.StubRunner(0, box)
        raises(RuntimeError, lambda: stub("backup", ["x", "--out", str(root / "elsewhere")]))
        raises(RuntimeError, lambda: stub("deploy", []))
        assert not (root / "elsewhere").exists()
        assert yq.parse_refresh(stub("refresh", []).text)["ok"] is True
        assert sum(yq.StubRunner.SHARES.values()) == 100
        naps: list[float] = []
        yq.StubRunner(40, box, naps.append)("refresh", [])
        assert naps == [16.0]


def tree(root: Path, skip: Path) -> dict:
    return {str(p.relative_to(root)): p.stat().st_size for p in root.rglob("*") if p.is_file() and skip not in p.parents}


@test("a whole rehearsal: the same launcher and worker, no process started, nothing outside its run folder")
def _():
    with data_root() as root:
        status.write_state({"last_load": {"run": "311225_0900", "result": "ok"}})
        (root / "business_data" / "Focus ERP 2025-12-31").mkdir(parents=True)
        (root / "business_data" / "Focus ERP 2025-12-31" / "a.xlsx").write_bytes(b"x")
        (root / "data" / "clean").mkdir(parents=True)
        (root / "data" / "clean" / "orders.csv").write_text("the real left-overs are not a rehearsal's to remove\n", encoding="utf-8")
        before = tree(root, root / "none")
        spawn, out = Spawn(), []

        class Never:
            def __getattr__(self, name):
                raise AssertionError(f"a rehearsal reached for subprocess.{name}")

        def never(*a, **kw):
            raise AssertionError("a rehearsal reached for the database or the live site")

        def no_env(*a, **kw):                    # the stub runner starts nothing: it is handed no environment at all
            raise AssertionError("a rehearsal asked for the engine's environment")
        real_sub, real_session, real_health, real_rpc = yq.subprocess, db.session, db.health, db.rpc
        yq.subprocess, db.session, db.health, db.rpc = Never(), never, never, never
        real_env, yq.child_env = yq.child_env, no_env
        linked = yq.gitinfo.is_linked_worktree
        yq.gitinfo.is_linked_worktree = lambda r: True               # as in the worktree this was built in
        real_settings, yq.settings_files = yq.settings_files, (lambda r: [])   # never this machine's own Claude Code rules
        real_stdout, sys.stdout = sys.stdout, io.StringIO()          # the worker's log goes to its stdout
        try:
            assert yq.rehearse(0, no_box=True, dev=False, say=out.append, spawn=spawn, start_wait_s=0) == 2
            assert "worktree" in "\n".join(out) and spawn.calls == []      # no --dev: refused like a real go
            out.clear()
            assert yq.rehearse(0, no_box=True, dev=True, say=out.append, spawn=spawn, start_wait_s=0) == 0, out
            run = spawn.calls[0]["run"]
            assert run.startswith("000000_") and out[0].startswith(f"REHEARSAL {run}") and out[-1] == FOLLOW
            assert status.lock_state()["step"] == "start (rehearsal)" and status.lock_state()["state"] == "running"
            assert yq.worker(run, nonce=spawn.nonce) == 2                  # the same rule as a real load: its launcher's child only
            assert yq.worker(run, nonce=spawn.nonce, parent_pid=os.getpid()) == 0      # deps chosen by the plan: the sandbox
            worker_log = sys.stdout.getvalue()
        finally:
            sys.stdout = real_stdout
            yq.subprocess, db.session, db.health, db.rpc = real_sub, real_session, real_health, real_rpc
            yq.child_env = real_env
            yq.gitinfo.is_linked_worktree, yq.settings_files = linked, real_settings
        box = loadplan.run_dir(run)
        rows = loadplan.journal_read(run)
        assert [r["step"] for r in rows if r["event"] == "step_ok"] == list(loadplan.STEPS)
        assert rows[0]["rehearsal"] is True and rows[-1]["event"] == "finished" and "REHEARSAL FINISHED" in rows[-1]["text"]
        assert "published" not in rows[-1] and "Published" not in rows[-1]["text"], "a rehearsal says it published something"
        assert all(r.get("text") for r in rows), "a row with no sentence for the owner"
        assert all("rehearsal" in r["text"].lower() for r in rows), "a line reads like a real load"
        # as `load status` shows it: the same steps as a load, every line marked, and none that claims a change
        said = [shown(r) for r in rows]
        opens = {r["step"]: r["text"] for r in rows if r["event"] == "step_start"}
        assert said[0] == "REHEARSAL started: nothing is loaded."
        assert said[-1] == "REHEARSAL FINISHED: nothing was loaded; every step ran in its sandbox."
        assert opens["refresh"] == "REHEARSAL: a stand-in for the load waits here; the database is not touched."
        assert opens["backup"] == "(rehearsal) " + yq.STEP_OPENS["backup"] and len(opens) == len(loadplan.STEPS)
        assert not any("database is being changed" in line for line in said)
        assert "profit_held_since" not in json.loads((box / "state.json").read_text(encoding="utf-8"))["last_load"]
        assert "REHEARSAL FINISHED" in worker_log and "step_ok refresh: (rehearsal) Loaded and verified." in worker_log
        assert "a rehearsal does not" in [r for r in rows if r.get("step") == "wait_tick" and r["event"] == "step_ok"][0]["text"]
        assert not paths.lock_file().exists()
        assert tree(root, box) == before, "the rehearsal changed something outside its run folder"
        assert status.read_state()["last_load"]["run"] == "311225_0900"
        inside = {str(p.relative_to(box)).replace("\\", "/") for p in box.rglob("*") if p.is_file()}
        today = datetime.now(yq.BAHRAIN).date().isoformat()
        assert f"published/Focus ERP {today}/Rehearsal_report.txt" in inside and "state.json" in inside
        assert any(n.startswith("gates/") and n.endswith("_pre-data-000000.json") for n in inside)
        assert any(n.startswith("backups/preload/yq-backup_") and n.endswith(".zip") for n in inside)
        assert not any(n.startswith("clean/") for n in inside), "the sandbox's own data/clean was not emptied"
        assert json.loads((box / "state.json").read_text(encoding="utf-8"))["last_load"]["run"] == run
        # a second go on the finished rehearsal is refused like any finished run
        assert yq.launch(run, loadplan.read_plan(run)["token"], dev=True, no_box=True, say=out.append) == 2


@test("starting a rehearsal: a dead rehearsal's lock is cleared and said, older sandboxes go, a real load is never touched")
def _():
    linked, real_settings = yq.gitinfo.is_linked_worktree, yq.settings_files
    yq.gitinfo.is_linked_worktree, yq.settings_files = (lambda r: False), (lambda r: [])
    try:
        with data_root() as root:
            for name in ("000000_0101", "000000_0102"):          # earlier rehearsals
                paths.write_text(loadplan.run_dir(name) / "plan.json", "{}\n")
            real_run = World(root)                               # a real, checked run: not a rehearsal's to remove
            paths.write_text(paths.lock_file(), json.dumps({"run": "000000_0102", "pid": DEAD_PID, "word_sha": "r",
                                                            "step": "refresh (rehearsal)"}))
            spawn, out = Spawn(), []
            assert yq.rehearse(0, no_box=True, say=out.append, spawn=spawn, start_wait_s=0) == 0, out
            assert "000000_0102 had stopped at refresh (rehearsal): its worker did not survive" in out[0]
            run = spawn.calls[0]["run"]
            left = sorted(p.name for p in paths.loads_dir().iterdir())
            assert left == sorted([run, RUN]), left
            assert (loadplan.run_dir(RUN) / "plan.json").is_file() and real_run.staging.is_dir()
            out.clear()                                          # ... and while that rehearsal holds the lock: no second one
            assert yq.rehearse(0, no_box=True, say=out.append, spawn=spawn, start_wait_s=0) == 2
            assert out == [f"A rehearsal is running (run {run}, at start (rehearsal)). No rehearsal was started: follow it "
                           "with python -m scripts.ops load status --wait"], out
            assert len(spawn.calls) == 1 and loadplan.run_dir(run).is_dir()
        with data_root() as root:                                # a file that is no lock (a launcher died writing it):
            paths.write_text(paths.lock_file(), "not a lock")    # cleared and said, exactly as a go does it
            spawn, out = Spawn(), []
            assert yq.rehearse(0, no_box=True, say=out.append, spawn=spawn, start_wait_s=0) == 2, out   # once it is old
            assert "being written at this moment" in out[0] and spawn.calls == []
            aged(paths.lock_file())
            out.clear()
            assert yq.rehearse(0, no_box=True, say=out.append, spawn=spawn, start_wait_s=0) == 0, out
            assert out[0] == "(an unreadable left-over lock is cleared: no load was running under it)"
            assert status.read_lock()["run"] == spawn.calls[0]["run"]
        with data_root() as root:                                # an interrupted REAL load: the rehearsal keeps away
            h = Harness(root)
            interrupted(h, upto="refresh")
            spawn, out = Spawn(), []
            assert yq.rehearse(0, no_box=True, say=out.append, spawn=spawn, start_wait_s=0) == 2
            assert "LOAD INTERRUPTED at refresh (run 010126_0900)" in out[0] and spawn.calls == []
            assert status.read_lock()["word_sha"] == "old-word" and loadplan.run_result(RUN) == "RUNNING"
            assert [p.name for p in paths.loads_dir().iterdir()] == [RUN]
    finally:
        yq.gitinfo.is_linked_worktree, yq.settings_files = linked, real_settings


@test("a rehearsal never even imports the database module, the engine or a driver (a fresh interpreter)")
def _():
    program = (
        "import os, sys\n"
        "from scripts import yq_load_go as yq\n"
        "from scripts.ops import loadplan, paths, status\n"
        "class P:\n"
        "    pid = os.getpid()\n"
        "    def poll(self): return None\n"
        "seen = {}\n"
        "def spawn(run, root, nonce, log): seen.update(run=run, nonce=nonce); return P()\n"
        "yq.gitinfo.is_linked_worktree = lambda r: False\n"
        "yq.settings_files = lambda r: []\n"
        "assert yq.rehearse(0, no_box=True, say=lambda s: None, spawn=spawn, start_wait_s=0) == 0\n"
        "assert yq.worker(seen['run'], nonce=seen['nonce'], parent_pid=os.getpid()) == 0\n"
        "assert loadplan.run_result(seen['run']) == 'FINISHED' and not paths.lock_file().exists()\n"
        "bad = [m for m in ('scripts.ops.db', 'scripts.refresh', 'scripts.load_supabase', 'scripts.ingest', 'scripts.db_backup',\n"
        "                   'scripts.prod_gate', 'scripts.nightly_backup', 'psycopg', 'supabase', 'httpx', 'app.config',\n"
        "                   'app.database', 'dotenv') if m in sys.modules]\n"
        "print('LOADED', bad)\n")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        env = dict(os.environ, YQ_MAIN_CHECKOUT=tmp, USERPROFILE=tmp, HOME=tmp)
        cp = subprocess.run([sys.executable, "-X", "utf8", "-c", program], cwd=str(ROOT), env=env, capture_output=True, timeout=120)
        text = cp.stdout.decode("utf-8", "replace") + cp.stderr.decode("utf-8", "replace")
        assert cp.returncode == 0, text
        assert "LOADED []" in text, text


@test("END TO END on Windows: a real detached rehearsal outlives its launcher, journals every step and unlocks")
def _():
    if os.name != "nt":
        print("SKIP  (the detached start is Windows-only)")
        return
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        env = dict(os.environ, YQ_MAIN_CHECKOUT=tmp, USERPROFILE=tmp)        # a temp data root; no real settings file
        env.pop("PYTHONIOENCODING", None)
        cp = subprocess.run([sys.executable, "-m", "scripts.yq_load_go", "--rehearse", "--seconds", "5", "--no-box", "--dev"],
                            cwd=str(ROOT), env=env, capture_output=True, timeout=120)
        out = cp.stdout.decode("utf-8", "replace")
        if cp.returncode == 2 and "permission rule" in out:
            print("SKIP  (this checkout's own Claude Code settings pre-approve the go: the rehearsal is refused, as it must be)")
            return
        assert cp.returncode == 0, out + cp.stderr.decode("utf-8", "replace")
        assert FOLLOW in out and "REHEARSAL 000000_" in out
        ops = Path(tmp) / "business_data" / "ops"
        run_dir = [p for p in (ops / "loads").iterdir() if p.name.startswith("000000_")][0]
        journal_file = run_dir / "journal.jsonl"

        def rows() -> list[dict]:
            return [json.loads(ln) for ln in journal_file.read_text(encoding="utf-8").splitlines() if ln.strip()]

        first = rows()                           # the launcher is gone; the worker is not done
        assert first and first[0]["event"] == "started" and first[-1]["event"] != "finished", first
        lock = json.loads((ops / "LOCK").read_text(encoding="utf-8"))
        assert lock["run"] == run_dir.name and status.pid_alive(int(lock["pid"])), "the worker is not alive"
        assert int(lock["pid"]) == first[0]["pid"] and "(rehearsal)" in lock["step"]
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and rows()[-1]["event"] not in ("finished", "failed"):
            time.sleep(0.5)
        done = rows()
        assert done[-1]["event"] == "finished", done[-1]
        assert [r["step"] for r in done if r["event"] == "step_ok"] == list(loadplan.STEPS)
        assert first[0]["awake"] is True and first[0]["breakaway"] in (True, False)
        stay = yq.stay_open_words(True)
        if first[0]["breakaway"] is False:       # Windows kept the worker inside this window's job: he is told, twice
            assert first[0]["next"] == stay and stay in out, (first[0], out)
        else:
            assert "next" not in first[0] and stay not in out, (first[0], out)
        # the real hand-over: the worker was the launcher's own child and held its word; the files hold the hash only
        assert len(lock["word_sha"]) == 64 and lock["word_sha"].isalpha() and "nonce" not in lock
        assert first[0]["launch"] == lock["word_sha"][:8] == done[-1]["launch"]
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and ((ops / "LOCK").exists() or status.pid_alive(int(lock["pid"]))):
            time.sleep(0.2)
        assert not (ops / "LOCK").exists() and not status.pid_alive(int(lock["pid"]))
        log = (run_dir / "worker.log").read_text(encoding="utf-8")
        assert "REHEARSAL FINISHED" in log and "Traceback" not in log and "refused" not in log, log[-600:]
        assert not (ops / "state.json").exists() and not (Path(tmp) / "business_data" / "staging").exists()
        # a worker typed by hand afterwards, with what the files hold as its word: refused, nothing runs
        again = subprocess.run([sys.executable, "-m", "scripts.yq_load_go", "--worker", run_dir.name], cwd=str(ROOT),
                               env=dict(env, **{yq.NONCE_ENV: lock["word_sha"]}), capture_output=True, timeout=120)
        assert again.returncode == 2 and b"internal worker" in again.stdout, again.stdout
        assert rows() == done and not (ops / "LOCK").exists()


# ── the command line ──────────────────────────────────────────────────────────────────────────

@test("the command line takes exactly one of the go, the rehearsal and the worker, and no shortcut option")
def _():
    buf = io.StringIO()
    real, sys.stdout = sys.stdout, buf
    real_err, sys.stderr = sys.stderr, io.StringIO()
    try:
        assert yq.main([]) == 2
        assert yq.main(["--run", RUN]) == 2 and yq.main(["--token", "x"]) == 2
        assert yq.main(["--rehearse", "--run", RUN, "--token", "x"]) == 2
        assert yq.main(["--worker", RUN, "--rehearse"]) == 2 and yq.main(["--worker", RUN, "--dev"]) == 2
        assert yq.main(["--run", RUN, "--token", "x", "--seconds", "1"]) == 2
        for extra in (["--yes"], ["--force"], ["--allow-newer"], ["--w", RUN], ["--reh"], ["--send"]):
            exc = raises(SystemExit, lambda extra=extra: yq.main(["--run", RUN, "--token", "x"] + extra))
            assert exc.code == 2, extra
    finally:
        sys.stdout, sys.stderr = real, real_err
    assert "Nothing was started" in buf.getvalue()
    assert (yq.EXIT_OK, yq.EXIT_ERROR, yq.EXIT_REFUSED, yq.EXIT_CHECK_AGAIN, yq.EXIT_NO_ANSWER) == (0, 1, 2, 3, 4)


# ── the file itself ───────────────────────────────────────────────────────────────────────────

def writer_tree() -> ast.Module:
    return ast.parse(WRITER.read_text(encoding="utf-8"), filename=str(WRITER))


def code_strings(tree: ast.Module) -> list[str]:
    """Every string constant of the file that is not a docstring."""
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                docs.add(id(first.value))
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]


@test("the writer imports nothing from scripts.refresh, the loader or the app, and uses no os.kill (AST)")
def _():
    tree = writer_tree()
    imported: list[tuple[str, list[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [(a.name, []) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append((node.module, [a.name for a in node.names]))
    never = ("scripts.refresh", "scripts.load_supabase", "scripts.ingest", "scripts.apply_sql", "scripts.apply_migration",
             "scripts.db_backup", "scripts.prod_gate", "scripts.reconcile_check", "scripts.verify_numbers",
             "app.agents", "app.events", "app.notify", "app.ai", "app.database", "psycopg", "supabase", "httpx", "signal")
    for module, names in imported:
        for bad in never:
            assert module != bad and not module.startswith(bad + "."), f"imports {module}"
            parent, _, leaf = bad.rpartition(".")
            assert not (parent and module == parent and leaf in names), f"imports {bad}"
    assert {m for m, _n in imported if m.startswith("app")} == {"app.config"}       # the project's .env, nothing else
    assert ("scripts", ["nightly_backup"]) in imported                                # backup_root and make_archive
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os":
            assert node.attr not in ("kill", "killpg", "system", "popen", "startfile", "execv", "spawnv", "_exit"), node.attr
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in ("eval", "exec", "__import__"), node.func.id
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr != "import_module", "a computed import"
    assert "scripts.refresh" not in sys.modules, "the engine was imported into this process"


@test("the writer copies the environment before it imports anything else, and starts no child from os.environ (AST)")
def _():
    tree = writer_tree()
    body = [n for n in tree.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]      # past the docstring
    assert isinstance(body[0], ast.ImportFrom) and body[0].module == "__future__"
    assert isinstance(body[1], ast.Import) and [a.name for a in body[1].names] == ["os"], "something is imported before the copy"
    copy = body[2]
    assert isinstance(copy, ast.Assign) and [ast.unparse(t) for t in copy.targets] == ["LAUNCH_ENV"]
    assert ast.unparse(copy.value) == "dict(os.environ)"
    first_ops = min(i for i, n in enumerate(body) if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("scripts"))
    assert first_ops > 2, "scripts.ops is imported before the environment is copied"
    # os.environ is read in four places: the copy, the worker's two words (taken out as they are read), and the
    # backup password (its own process's, after the project's .env was loaded). No child is given os.environ
    parent = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    uses = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "environ" and isinstance(node.value, ast.Name) and node.value.id == "os":
            up = node
            while not isinstance(up, ast.Call):
                up = parent[up]
            uses.append(ast.unparse(up))
    # (the backup password is read by scripts/yq_secrets.py from LAUNCH_ENV, Credential Manager or the registry)
    assert sorted(uses) == sorted(["dict(os.environ)", "os.environ.pop(NONCE_ENV, '')",
                                   "os.environ.pop(BREAKAWAY_ENV, '')"]), uses
    for node in ast.walk(tree):                  # every Popen of the file names its environment, and none is os.environ
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "Popen":
            env = [k.value for k in node.keywords if k.arg == "env"]
            assert len(env) == 1 and "os.environ" not in ast.unparse(env[0]), ast.unparse(node)[:80]
    assert yq.NONCE_ENV not in yq.LAUNCH_ENV and yq.BREAKAWAY_ENV not in yq.LAUNCH_ENV


@test("the writer starts processes in two places only, never through a shell; the engine programs, then the delivery")
def _():
    tree = writer_tree()
    owners: dict[str, int] = {}
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) \
                    and node.func.value.id == "subprocess":
                assert node.func.attr == "Popen", f"subprocess.{node.func.attr} in {fn.name}"
                owners[fn.name] = owners.get(fn.name, 0) + 1
                for k in node.keywords:
                    assert not (k.arg == "shell" and getattr(k.value, "value", None) is not False), "a shell"
    assert set(owners) == {"run_child", "spawn_worker"}, owners
    strings = code_strings(tree)
    modules = sorted({s for s in strings if s.startswith("scripts.") and " " not in s})
    # the three engine programs of the load, then (after it has closed) the brief and the e-mail
    assert modules == ["scripts.db_backup", "scripts.ops", "scripts.prod_gate", "scripts.reconcile_check",
                       "scripts.yq_send"], modules
    assert sum("from scripts.refresh import refresh" in s for s in strings) == 1          # inside the child's program
    for s in strings:
        for bad in ("--restore", "--yes", "--allow-newer", "--force", "apply_sql", "apply_migration", "load_supabase",
                    "scripts.ingest", "deploy", "REPLACE_OK", "PARTIAL_OK", "git push", "DROP ", "TRUNCATE"):
            assert bad not in s, f"'{bad}' in the string {s[:60]!r}"
    src = WRITER.read_text(encoding="utf-8")

    def callers(name: str, attr_of: str | None = None) -> list[str]:
        """The functions of the file that call `name` (or `attr_of.name`)."""
        out = []
        for fn in [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))]:
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                f = node.func
                if attr_of is None and isinstance(f, ast.Name) and f.id == name:
                    out.append(fn.name)
                if attr_of and isinstance(f, ast.Attribute) and f.attr == name and isinstance(f.value, ast.Name) \
                        and f.value.id == attr_of:
                    out.append(fn.name)
        return out
    assert callers("RealRunner") == ["real_deps"], "the real runner is built in one place"
    assert callers("real_deps") == ["deps_for"], "the real world is chosen in one place"
    assert callers("run_child") == ["RealRunner"], "only the real runner starts an engine step"
    assert callers("_real_parts") == ["real_deps"] and callers("spawn_worker") == []      # handed over as deps.spawn
    # the environment of a child is asked for where a child is started, and nowhere else (never by the stub runner)
    assert sorted(callers("child_env")) == ["RealRunner", "spawn_worker"] and callers("worker_python") == ["spawn_worker"]
    assert callers("copy_file", "paths") == ["publish_folder"], "the loaded set is copied by the guarded copy only"
    assert callers("copy2", "shutil") == [] and callers("copy", "shutil") == [] and callers("copytree", "shutil") == []
    assert sorted(callers("session", "db")) == ["_real_foreign_writes", "_real_ingest_runs", "_real_parts"]
    assert callers("health", "db") == ["_real_parts"]
    assert callers("rpc", "db") == [] and callers("rpc_params", "db") == [] and callers("install", "db") == []
    spawn = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "spawn_worker"][0]
    text = ast.get_source_segment(src, spawn)
    assert "python, needs = worker_python()" in text
    assert '[python, "-X", "utf8", "-m", MODULE, "--worker", run]' in text
    assert "CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP" in text and "stdin=subprocess.DEVNULL" in text
    assert "close_fds=True" in text and "stderr=subprocess.STDOUT" in text and "cwd=str(root)" in text
    assert (yq.CREATE_NO_WINDOW, yq.CREATE_NEW_PROCESS_GROUP, yq.CREATE_BREAKAWAY_FROM_JOB) == (0x08000000, 0x200, 0x01000000)
    assert (yq.ES_CONTINUOUS | yq.ES_SYSTEM_REQUIRED) == 0x80000001
    assert yq.MODULE == "scripts.yq_load_go" and not (ROOT / "scripts" / "ops" / "yq_load_go.py").exists()


# ── the failure paths (independent review, before the first production go) ────────────────────

@test("the box: a Yes that comes at once does not count (a key typed for another window); three such answers load nothing")
def _():
    def boxed(answers, timeout_s=540):           # [(the button's number, the seconds the box stood open)]
        t, shows, queue = [100.0], [], list(answers)

        def show(title, text, timeout_ms):
            button, took = queue.pop(0)
            shows.append((text, timeout_ms))
            t[0] += took
            return button
        return yq.windows_box("YQ load 010126", "the table", timeout_s, show=show, clock=lambda: t[0]), shows

    said, shows = boxed([(6, 10.0)])
    assert said == "yes" and shows == [("the table", 540000)]
    said, shows = boxed([(6, 0.5), (6, 12.0)])   # a stray Y as the box took the keyboard, then an answer he gave
    assert said == "yes" and len(shows) == 2
    assert shows[1][0].startswith("A Yes came within 3 seconds") and shows[1][0].endswith("\n\nthe table")
    assert shows[1][1] == 539500, "the time is one budget, not one per showing"
    assert boxed([(6, 0.5), (7, 5.0)])[0] == "no"
    said, shows = boxed([(6, 0.25), (6, 0.25), (6, 0.5)])
    assert said == "too_fast" and len(shows) == 3 == yq.BOX_SHOWS
    assert boxed([(7, 0.25)])[0] == "no", "a quick No is a No: only a Yes has to be a read one"
    assert boxed([(6, 3.0)])[0] == "yes" and yq.BOX_MIN_READ_S == 3
    assert boxed([(32000, 540.0)])[0] == "timeout" and boxed([(2, 1.0)])[0] == "closed" and boxed([(0, 0.0)])[0] == "unavailable"
    said, shows = boxed([(6, 0.5), (6, 0.5)], timeout_s=1.0)
    assert said == "timeout" and len(shows) == 2 and shows[1][1] == 500      # nothing is left for a third showing

    def broken(title, text, timeout_ms):
        raise OSError("no desktop")
    assert yq.windows_box("t", "x", 5, show=broken, clock=lambda: 0.0) == "unavailable"
    with data_root() as root:                    # the launcher: that is no answer, nothing loads, and it says why
        h = Harness(root, answer="too_fast")
        assert h.go() == 4
        assert h.out == ["Yes came at once every time the box opened, before it could be read. That is no answer: "
                         "nothing loaded. Type /yq-load 010126 again."], h.out         # the folder, named
        h.assert_nothing_started()
    with data_root() as root:                    # No at the RESUME box of a load that had begun: the database is not as it was
        h = Harness(root, answer="no")
        interrupted(h, upto="refresh")
        assert h.go() == 4
        assert h.out == [f"You answered No: nothing more was loaded and the load stays INTERRUPTED. {MAY_HOLD}"], h.out
        assert not says_nothing_was_written(h.said()) and status.read_lock()["word_sha"] == "old-word"
        h.box.answer = "timeout"
        h.out.clear()
        assert h.go() == 4 and MAY_HOLD in h.said() and not says_nothing_was_written(h.said())
    with data_root() as root:                    # ... interrupted before any refresh: "nothing loaded" is true, and said
        h = Harness(root, answer="no")
        interrupted(h, upto="pre_gate")
        assert h.go() == 4 and h.out == ["You answered No: nothing loaded."], h.out


@test("LAST LOAD FAILED stays until a load FINISHES: a later check that never loaded, a rehearsal or a run closed "
      "before it wrote takes nothing away")
def _():
    told = "The data loaded, but these totals do not match the reports: Sales"
    with data_root() as root:
        h = Harness(root)
        h.runner.script["refresh"] = [verify_fails_with(h, told)]
        assert h.go() == 0 and h.work() == 1
        assert status.failed_load() == {"run": RUN, "step": "refresh"}
        # he types /yq-load: the new check makes a newer run folder, and he answers No at its box
        newer = Harness(root, run="020126_0900", answer="no")
        ahead = time.time() + 600
        os.utime(loadplan.run_dir("020126_0900"), (ahead, ahead))
        assert loadplan.latest_run() == "020126_0900" and newer.go() == 4
        assert status.failed_load() == {"run": RUN, "step": "refresh"}
        assert status.interrupted_banner() == last_failed("refresh")
        # a rehearsal that finished afterwards is no load
        yq.journal("000000_0905", "finished", result="ok", text="REHEARSAL FINISHED: nothing was loaded.")
        assert status.failed_load() == {"run": RUN, "step": "refresh"}
        # a newer run that was closed BEFORE it wrote anything changed nothing in the database either
        third = Harness(root, run="030126_0900")
        third.runner.script["backup"] = [yq.Child(1, "no database\n")]
        assert third.go() == 0 and third.work() == 1 and third.rows()[-1]["written"] is False
        assert status.failed_load() == {"run": RUN, "step": "refresh"}
        assert status.interrupted_banner() == last_failed("refresh")
        # only a load that finishes makes the database whole again
        fourth = Harness(root, run="040126_0900")
        assert fourth.load() == 0, fourth.rows()[-1]
        assert status.failed_load() is None and status.interrupted_banner() is None
    with data_root() as root:                    # two failures: the banner names the later one
        h = Harness(root)
        crash = yq.Child(1, "Traceback (most recent call last):\nMemoryError\n")
        h.runner.script["refresh"] = [crash, crash]
        assert h.go() == 0 and h.work() == 1
        rows = loadplan.journal_read(RUN)        # ... which was closed a day before the next one
        day = timedelta(days=1)
        text = "\n".join(json.dumps(dict(r, at=(datetime.fromisoformat(r["at"]) - day).isoformat(timespec="seconds")),
                                    sort_keys=True) for r in rows) + "\n"
        loadplan.journal_file(RUN).write_text(text, encoding="utf-8")
        second = Harness(root, run="020126_0900")
        second.runner.script["refresh"] = [verify_fails_with(second, told)]
        assert second.go() == 0 and second.work() == 1
        assert status.failed_load() == {"run": "020126_0900", "step": "refresh"}
    with data_root():                            # a state file whose last load is no object, a journal row with no time
        paths.write_text(paths.state_file(), json.dumps({"last_load": "yesterday"}))
        paths.write_text(loadplan.journal_file(RUN), json.dumps({"event": "failed", "written": True, "step": "refresh"}) + "\n")
        assert status.failed_load() is None
        assert status.when("2026-01-01T09:00:00") == datetime(2026, 1, 1, 9, tzinfo=timezone.utc) and status.when(None) is None
    for name in ("010126_0900\n", "٠١٠١٢٦_٠٩٠٠", "010126_0900 ", "010126-0900", None, 7):
        assert not loadplan.is_run(name), repr(name)         # a line break or another script's digits is no run name
    assert loadplan.is_run(RUN)


@test("the one identical re-run starts only on what the table showed: files and commits are compared again, never the database")
def _():
    def stage_fails(h, then=None):
        def answer(argv):
            h.w.newest_run += 1                  # refresh's own error row
            if then is not None:
                then()
            return yq.Child(2, refresh_line(ok=False, verify_ok=None, error="The load step failed.") + "\n")
        return answer

    with data_root() as root:                    # nothing moved: the re-run goes ahead, after one more comparison
        h = Harness(root)
        h.runner.script["refresh"] = [stage_fails(h)]
        assert h.load() == 0
        at = [n for n, t in enumerate(h.w.trace) if t == "run:refresh"]
        between = h.w.trace[at[0] + 1:at[1]]
        assert "token:files and commits only" in between and "token:with fingerprint" not in between, between
    with data_root() as root:                    # the database has moved, of course: the first run moved it
        h = Harness(root)
        h.runner.script["refresh"] = [stage_fails(h, then=lambda: h.w.fingerprint.update(orders=[11, 100]))]
        assert h.load() == 0 and h.runner.labels().count("refresh") == 2
    with data_root() as root:                    # the programs changed while the first run was going: no second run
        h = Harness(root)
        h.runner.script["refresh"] = [stage_fails(h, then=lambda: setattr(h.w, "head", "c" * 40))]
        assert h.go() == 0 and h.work() == 3
        assert h.runner.labels().count("refresh") == 1, "the re-run started on programs the table never showed"
        failed = h.rows("failed")[0]
        assert failed["result"] == "failed" and failed["written"] is True and failed["check_again"] is True
        assert failed["text"] == f"CHECK AGAIN at refresh. {MAY_HOLD}" and failed["step"] == "refresh"
        assert "this folder's programs (HEAD)" in failed["why"] and not says_nothing_was_written(whole(failed))
        assert status.interrupted_banner() == last_failed("refresh") and not paths.lock_file().exists()
    with data_root() as root:                    # ... or a staged file was replaced meanwhile
        h = Harness(root)
        swap = lambda: (h.w.staging / "Sales_day_book1.xlsx").write_bytes(b"replaced meanwhile")     # noqa: E731
        h.runner.script["refresh"] = [stage_fails(h, then=swap)]
        assert h.go() == 0 and h.work() == 3 and h.runner.labels().count("refresh") == 1
        assert "the staged copies: Sales_day_book1.xlsx" in h.rows("failed")[0]["why"]
    with data_root() as root:                    # ... or a newer check replaced this one
        h = Harness(root)
        h.runner.script["refresh"] = [stage_fails(h, then=lambda: h.w.write_plan(superseded_by="010126_0930"))]
        assert h.go() == 0 and h.work() == 3 and h.runner.labels().count("refresh") == 1
        assert "a newer check replaced it (run 010126_0930)" in h.rows("failed")[0]["why"]

    # the clock. A run that left the database WHOLE (loaded, its verification could not run) waits for a quiet
    # moment between two production ticks before the second run, exactly as the first run did ...
    with data_root() as root:
        h = Harness(root)                        # 09:02:00, two minutes after a tick
        starts: list[datetime] = []
        h.runner.hooks["refresh"] = lambda: starts.append(h.clock.now())

        def slow_and_unverified(argv):
            h.clock.t += timedelta(minutes=8)    # the first run took eight minutes: the next tick is 5 minutes away
            return cannot_verify(h)(argv)
        h.runner.script["refresh"] = [slow_and_unverified]
        assert h.load() == 0, h.rows()[-1]
        assert starts[0] == NOON and starts[1] == datetime(2026, 1, 1, 9, 16, 0, tzinfo=timezone.utc), starts
        assert yq.tick_wait_seconds(starts[1]) == 0
        note = h.rows("note", "refresh")
        assert [n["text"] for n in note] == ["Waited 5.5 min for a quiet moment between two production ticks before "
                                             "the second run."], note
        at = [n for n, t in enumerate(h.w.trace) if t == "run:refresh"]
        assert h.w.trace[at[0] + 1:at[1]] == ["token:files and commits only", "runs:None"], \
            "the table is compared after the wait, right before the second run"
        assert sum(h.clock.sleeps) == yq.RETRY_PAUSE_S + 330        # the pause, then 5.5 minutes to the quiet moment
    with data_root() as root:                    # ... and never inside the morning window
        h = Harness(root, start=datetime(2026, 1, 1, 4, 46, 0, tzinfo=timezone.utc))      # 07:46 in Bahrain
        starts = []
        h.runner.hooks["refresh"] = lambda: starts.append(h.clock.now())

        def five_minutes_unverified(argv):
            h.clock.t += timedelta(minutes=5)
            return cannot_verify(h)(argv)
        h.runner.script["refresh"] = [five_minutes_unverified]
        assert h.load() == 0, h.rows()[-1]
        local = starts[1].astimezone(yq.BAHRAIN)
        assert (local.hour, local.minute, local.second) > (8, 20, 0) and yq.tick_wait_seconds(starts[1]) == 0, starts
    # ... while a load stage that failed part-way is run again at once: half a ledger is the worse thing to leave
    with data_root() as root:
        h = Harness(root)
        starts = []
        h.runner.hooks["refresh"] = lambda: starts.append(h.clock.now())

        def slow_failure(argv):
            h.clock.t += timedelta(minutes=8)
            return stage_fails(h)(argv)
        h.runner.script["refresh"] = [slow_failure]
        assert h.load() == 0
        assert (starts[1] - starts[0]).total_seconds() == 8 * 60 + yq.RETRY_PAUSE_S and h.rows("note", "refresh") == []


@test("a program file changed in the folder after the check is a change of the programs, though HEAD stands still")
def _():
    from scripts.ops import gitinfo
    real_head, real_local = gitinfo.head, gitinfo.engine_local_changes
    gitinfo.head = lambda root: "a" * 40
    try:
        gitinfo.engine_local_changes = lambda root: []
        clean = loadplan.head_part(ROOT)
        gitinfo.engine_local_changes = lambda root: ["scripts/load_supabase.py", "app/ingest_batch.py"]
        dirty = loadplan.head_part(ROOT)
    finally:
        gitinfo.head, gitinfo.engine_local_changes = real_head, real_local
    assert clean == "a" * 40 and dirty == "a" * 40 + " + changed here: app/ingest_batch.py, scripts/load_supabase.py"
    named = "this folder's programs (changed on this PC and not committed: app/ingest_batch.py, scripts/load_supabase.py)"
    assert loadplan.moved_parts({"head": clean}, {"head": dirty}) == [named]
    assert loadplan.moved_parts({"head": clean}, {"head": "b" * 40}) == ["this folder's programs (HEAD)"]
    with data_root() as root:                    # edited while the box stood open: the worker's first check stops it
        h = Harness(root)
        h.box.on_show = lambda: setattr(h.w, "head", dirty)
        assert h.go() == 0 and h.work() == 3
        failed = h.rows("failed")[0]
        assert failed["result"] == "check_again" and failed["written"] is False and failed["step"] == "token"
        assert "changed on this PC and not committed: app/ingest_batch.py" in failed["why"]
        assert h.runner.calls == [], "a program of the engine was started"
    with data_root() as root:                    # even a table whose own token was made over such a file never loads
        h = Harness(root)
        h.w.head = dirty
        parts = h.w.parts(True)
        h.w.write_plan(token_parts=parts, token=loadplan.token_of(parts))
        assert h.go() == 3 and named in h.said(), h.out
        h.assert_nothing_started()
        assert h.box.shown == []


@test("the lock names the engine program the worker waits on; while one of a worker that is gone still runs, nothing "
      "starts beside it")
def _():
    with data_root() as root:                    # a runner that starts real children tells the worker, which writes the lock
        h = Harness(root)
        during, after = {}, {}

        class Telling(Runner):
            on_child = None                      # as yq.RealRunner: the worker sets it

            def __call__(self, label, argv, timeout=None):
                self.on_child(label, os.getpid())
                during[label] = dict(status.read_lock())
                out = super().__call__(label, argv, timeout)
                self.on_child(label, None)
                after[label] = dict(status.read_lock())
                return out
        h.runner = h.deps.runner = Telling(h.w)
        assert h.load() == 0
        seen = during["refresh"]
        assert seen["child_pid"] == os.getpid() and seen["child_step"] == "refresh"
        assert seen["child_created"] == yq.process_created(os.getpid())
        assert yq.loader_alive(seen) == os.getpid()
        gone = after["refresh"]
        assert gone["child_pid"] is None and gone["child_step"] is None and yq.loader_alive(gone) is None
        assert set(during) == {"backup", "pre_gate", "refresh", "reconcile", "post_gate", "compare"}
    assert yq.RealRunner(ROOT, RUN).on_child is None and not hasattr(Runner(None), "on_child")
    got: list = []                               # run_child itself: the id as the child starts, None once it has ended
    child = yq.run_child([sys.executable, "-c", "print('x')"], cwd=ROOT, env=yq.child_env(), seen=got.append)
    assert child.code == 0 and len(got) == 2 and isinstance(got[0], int) and got[0] > 0 and got[1] is None, got

    linked, real_settings = yq.gitinfo.is_linked_worktree, yq.settings_files
    yq.gitinfo.is_linked_worktree, yq.settings_files = (lambda r: False), (lambda r: [])
    try:
        with data_root() as root:                # the worker was ended by hand; the loader it had started was not
            h = Harness(root)
            lock = dict(interrupted(h, upto="refresh")["lock"], child_pid=os.getpid(),
                        child_created=yq.process_created(os.getpid()), child_step="refresh")
            paths.write_text(paths.lock_file(), json.dumps(lock))
            assert not yq.worker_alive(lock) and yq.loader_alive(lock) == os.getpid()
            assert h.go() == 2
            assert h.out == [f"The worker of run {RUN} is gone, but the program it had started at step refresh is still "
                             f"running on this PC (python.exe, pid {os.getpid()}) and may still be writing to the "
                             "database. Nothing was started and nothing is resumed beside it: wait until it has ended, "
                             "then type /yq-load. If it has truly hung, end that process in Task Manager first."], h.out
            assert h.box.shown == [] and h.spawn.calls == [] and status.read_lock() == lock, "the lock was touched"
            assert loadplan.run_result(RUN) == "RUNNING" and h.rows("failed") == []
            out: list[str] = []                  # a rehearsal keeps away as well
            assert yq.rehearse(0, no_box=True, say=out.append, spawn=Spawn(), start_wait_s=0) == 2
            assert "is still running on this PC" in out[0] and "No rehearsal was started" in out[0], out
            assert status.read_lock() == lock and [p.name for p in paths.loads_dir().iterdir()] == [RUN]
            # the same number given to another program later is nobody's loader: the load can be resumed
            other = dict(lock, child_created=1)
            paths.write_text(paths.lock_file(), json.dumps(other))
            assert yq.loader_alive(other) is None and yq.loader_alive(dict(lock, child_pid=DEAD_PID)) is None
            assert yq.loader_alive(dict(lock, child_pid=None)) is None and yq.loader_alive({"child_pid": "x"}) is None
            h.out.clear()
            assert h.go() == 0 and "interrupted at step 'refresh'" in h.box.shown[0][1]
    finally:
        yq.gitinfo.is_linked_worktree, yq.settings_files = linked, real_settings


@test("on Windows the worker's own job ends what it started: a worker that is ended takes its loader with it")
def _():
    if os.name != "nt":
        assert yq.own_job() is False
        print("SKIP  (a Windows job object)")
        return
    program = (
        "import subprocess, sys, time\n"
        "from scripts import yq_load_go as yq\n"
        "print('JOB', yq.own_job(), flush=True)\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "print('CHILD', child.pid, flush=True)\n"
        "time.sleep(120)\n")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        env = dict(os.environ, YQ_MAIN_CHECKOUT=tmp, USERPROFILE=tmp, PYTHONUTF8="1")
        parent = subprocess.Popen([sys.executable, "-X", "utf8", "-c", program], cwd=str(ROOT), env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        child_pid = None
        try:
            made = parent.stdout.readline().decode("utf-8", "replace").split()
            child_pid = int(parent.stdout.readline().decode("utf-8", "replace").split()[1])
            assert made[:1] == ["JOB"] and status.pid_alive(child_pid), (made, child_pid)
            if made[1] != "True":                # Windows refused the job here: the lock's child record is the guard
                print("SKIP  (Windows did not allow a job of its own in this session)")
                return
            parent.kill()                        # as Task Manager's End task does: no clean-up of its own runs
            parent.wait(timeout=30)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and status.pid_alive(child_pid):
                time.sleep(0.1)
            assert not status.pid_alive(child_pid), "the child outlived the process that started it"
        finally:
            if parent.poll() is None:
                parent.kill()
            if child_pid and status.pid_alive(child_pid):
                subprocess.run(["taskkill", "/PID", str(child_pid), "/F"], capture_output=True, timeout=30)
            parent.stdout.close()


@test("a load that had begun never loses its lock while its closing line is not on disk")
def _():
    real_journal = yq.journal

    def no_closing_row(run, event, **fields):    # the journal file is held: only the closing row cannot be written
        if event in ("failed", "finished"):
            return False
        return real_journal(run, event, **fields)

    told = "The data loaded, but these totals do not match the reports: Sales"
    with data_root() as root:                    # the worker: a failed verify whose closing row cannot be written
        h = Harness(root)
        h.runner.script["refresh"] = [verify_fails_with(h, told)]
        assert h.go() == 0
        yq.journal = no_closing_row
        try:
            assert h.work() == 1
        finally:
            yq.journal = real_journal
        assert h.rows("failed") == [] and loadplan.run_result(RUN) == "RUNNING"
        lock = status.read_lock()
        assert lock is not None and lock["run"] == RUN and lock["step"] == "refresh", "the lock went with no closing row"
        assert any("the lock is kept" in line for line in h.w.log)
        h.launcher_gone()                        # the worker has ended: every skill says so until /yq-load settles it
        assert status.interrupted_banner() == "LOAD INTERRUPTED at refresh: run /yq-load"
    with data_root() as root:                    # ... while a run that wrote nothing is simply unlocked, row or no row
        h = Harness(root)
        h.runner.script["backup"] = [yq.Child(1, "no database\n")]
        assert h.go() == 0
        yq.journal = no_closing_row
        try:
            assert h.work() == 1
        finally:
            yq.journal = real_journal
        assert not paths.lock_file().exists() and status.interrupted_banner() is None
    with data_root() as root:                    # a load that FINISHED is unlocked even so: it is whole, and state.json says so
        h = Harness(root)
        assert h.go() == 0
        yq.journal = no_closing_row
        try:
            assert h.work() == 0
        finally:
            yq.journal = real_journal
        assert not paths.lock_file().exists() and status.read_state()["last_load"]["run"] == RUN
        assert any("FINISHED could not be written" in line for line in h.w.log)

    # the launcher. An interrupted load that can never be resumed is closed by a row; without the row its dead lock stays
    with data_root() as root:
        h = Harness(root)
        interrupted(h, upto="refresh")
        (h.w.staging / "Sales_day_book1.xlsx").write_bytes(b"replaced since")
        yq.journal = no_closing_row
        try:
            assert h.go() == 3
        finally:
            yq.journal = real_journal
        assert status.read_lock()["word_sha"] == "old-word" and h.rows("failed") == []
        assert "It stays INTERRUPTED" in h.said() and MAY_HOLD in h.said() and not says_nothing_was_written(h.said())
        assert "Try /yq-load 010126 again in a minute." in h.said(), h.said()          # the folder, named
        h.out.clear()                            # the next /yq-load can write the row: closed, unlocked, LAST LOAD FAILED
        assert h.go() == 3 and "The interrupted load is closed: it cannot be resumed." in h.said()
        assert not paths.lock_file().exists() and status.interrupted_banner() == last_failed("refresh")
    with data_root() as root:                    # the same for a check that a newer one replaced
        h = Harness(root)
        interrupted(h, upto="refresh")
        h.w.write_plan(superseded_by="010126_0930")
        yq.journal = no_closing_row
        try:
            assert h.go() == 2
        finally:
            yq.journal = real_journal
        assert status.read_lock()["word_sha"] == "old-word" and "It stays INTERRUPTED" in h.said()
        h.out.clear()
        assert h.go() == 2 and not paths.lock_file().exists() and status.interrupted_banner() == last_failed("refresh")
    with data_root() as root:                    # interrupted BEFORE any refresh: there is nothing to keep a lock for
        h = Harness(root)
        interrupted(h, upto="pre_gate")
        (h.w.staging / "Sales_day_book1.xlsx").write_bytes(b"replaced since")
        yq.journal = no_closing_row
        try:
            assert h.go() == 3
        finally:
            yq.journal = real_journal
        assert not paths.lock_file().exists() and "It stays INTERRUPTED" not in h.said()

    # after his Yes at the RESUME box of a load that had begun: whatever keeps the worker from starting, the lock stays
    with data_root() as root:
        h = Harness(root, spawn=Spawn(error=OSError("worker.log is held")))
        interrupted(h, upto="refresh")
        assert h.go() == 2
        assert h.out == ["The worker could not be started (OSError: worker.log is held). LOAD INTERRUPTED at refresh: the "
                         f"load stays as it was. {MAY_HOLD} Type /yq-load to resume."], h.out
        lock = status.read_lock()
        assert lock is not None and lock["run"] == RUN and lock["step"] == "refresh" and lock["word_sha"] == h.spawn.sha
        assert h.rows("failed") == [] and loadplan.run_result(RUN) == "RUNNING"
        h.launcher_gone()
        assert status.interrupted_banner() == "LOAD INTERRUPTED at refresh: run /yq-load"
        h.spawn._error, h.w.newest_run = None, 8  # and the next go resumes it
        h.out.clear()
        assert h.go() == 0 and h.work() == 0 and loadplan.run_result(RUN) == "FINISHED"
    with data_root() as root:                    # ... replaced by a newer check while that box stood open: closed, by a row
        h = Harness(root)
        interrupted(h, upto="refresh")
        h.box.on_show = lambda: h.w.write_plan(superseded_by="010126_0930")
        assert h.go() == 2
        assert h.out[0] == f"The interrupted load is closed: it cannot be resumed. {MAY_HOLD}" and "replaced" in h.out[1]
        closing = h.rows("failed")[0]
        assert closing["written"] is True and closing["step"] == "refresh" and closing["result"] == "failed"
        assert not paths.lock_file().exists() and status.interrupted_banner() == last_failed("refresh")
        assert h.spawn.calls == []
    with data_root() as root:                    # a first go whose worker cannot start: nothing loaded, no lock (as before)
        h = Harness(root, spawn=Spawn(error=OSError("no such program")))
        assert h.go() == 2 and h.out[-1].endswith("Nothing was loaded.") and not paths.lock_file().exists()


@test("a resume before anything was written takes a backup and a before-snapshot again when the old ones are hours old")
def _():
    with data_root() as root:                    # interrupted before the refresh, resumed a day later
        h = Harness(root, start=datetime.now(timezone.utc) + timedelta(days=1, minutes=2))
        h.clock.t = h.clock.t.replace(minute=2, second=0, microsecond=0)     # two minutes after a tick
        kept = interrupted(h, upto="wait_tick")
        assert h.go() == 0 and h.work() == 0, h.rows()[-1]
        assert h.runner.labels() == ["backup", "pre_gate", "refresh", "reconcile", "post_gate", "compare"]
        notes = [n["text"] for n in h.rows("note")]
        assert any("no longer recent" in n and "a new one is taken" in n for n in notes if "backup" in n.lower()), notes
        assert any("before-snapshot" in n and "no longer recent" in n for n in notes), notes
        backup = h.rows("step_ok", "backup")[-1]
        assert "reused" not in backup and backup["archive"] != str(kept["archive"])
    with data_root() as root:                    # resumed within the hour: the journalled ones stand (as before)
        h = Harness(root, start=datetime.now(timezone.utc) + timedelta(minutes=20))
        h.clock.t = h.clock.t.replace(second=0, microsecond=0)
        interrupted(h, upto="wait_tick")
        assert h.go() == 0
        h.deps.wait_for_tick = False
        assert h.work() == 0, h.rows()[-1]
        assert h.runner.labels() == ["refresh", "reconcile", "post_gate", "compare"]
    with data_root() as root:                    # once the refresh had begun the old baseline is all there is, whatever its age
        h = Harness(root, start=datetime.now(timezone.utc) + timedelta(days=3))
        h.clock.t = h.clock.t.replace(minute=2, second=0, microsecond=0)
        interrupted(h, upto="refresh")
        h.w.newest_run = 8
        assert h.go() == 0 and h.work() == 0, h.rows()[-1]
        assert h.runner.labels() == ["refresh", "reconcile", "post_gate", "compare"]
        assert h.rows("step_ok", "backup")[-1]["reused"] is True


@test("the writer runs on Python 3.11: grammar, an encoding on every text open, no real name or amount")
def _():
    src = WRITER.read_text(encoding="utf-8")
    tree = ast.parse(src, feature_version=(3, 11))
    assert src.startswith('"""') and "from __future__ import annotations" in src
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
            mode = node.args[1].value if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) else "r"
            assert "b" in mode or any(k.arg == "encoding" for k in node.keywords), f"open() without encoding, line {node.lineno}"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("read_text", "write_text"):
            ours = isinstance(node.func.value, ast.Name) and node.func.value.id == "paths"     # paths.write_text is UTF-8
            assert ours or any(k.arg == "encoding" for k in node.keywords), \
                f"{node.func.attr} without encoding, line {node.lineno}"
        if isinstance(node, ast.JoinedStr):                       # no f-string nested in an f-string (3.12 only when same-quoted)
            assert not any(isinstance(v, ast.FormattedValue) and any(isinstance(x, ast.JoinedStr) for x in ast.walk(v.value))
                           for v in node.values), f"a nested f-string, line {node.lineno}"
    from scripts.ops import selftest
    assert not selftest.scan_text(src, []), selftest.scan_text(src, [])
    assert not selftest.scan_text(Path(__file__).read_text(encoding="utf-8"), [])
    # what the owner reads (the box, the launcher's sentences, the journal) prints in any console
    for s in code_strings(tree):
        assert s.isascii(), f"a non-ASCII character in the string {s[:50]!r}"
    assert max(len(ln) for ln in src.splitlines()) <= 120


# ── measuring a load, the box after a Yes too soon, the lock's PC (14-Oct-2026) ─────────────────

@test("every line a child prints reaches the worker log with its time and the seconds since the child started")
def _():
    import re
    with tempfile.TemporaryDirectory() as tmp:
        log: list[str] = []
        child = yq.run_child([sys.executable, "-c", "print('one', flush=True); print('two', flush=True)"],
                             cwd=Path(tmp), env=yq.child_env(), log=log.append)
        assert child.code == 0 and child.text.splitlines() == ["one", "two"], "the text the steps read is unchanged"
        stamp = re.compile(r"^  \| \d\d:\d\d:\d\dZ \+ *\d+s (one|two)$")
        assert len(log) == 2 and all(stamp.match(ln) for ln in log), log


@test("the closing row carries the run's profile, seconds per step and in all; the refresh time is kept for the next load")
def _():
    with data_root() as root:
        h = Harness(root)
        h.runner.hooks["backup"] = lambda: setattr(h.clock, "t", h.clock.t + timedelta(seconds=60))
        h.runner.hooks["refresh"] = lambda: setattr(h.clock, "t", h.clock.t + timedelta(seconds=155))
        assert h.load() == 0
        fin = h.rows("finished")[0]
        assert fin["timings"]["backup"] == 60.0 and fin["timings"]["refresh"] == 155.0, fin["timings"]
        assert set(fin["timings"]) == set(loadplan.STEPS) and fin["total_seconds"] == 215.0, fin
        assert status.read_state()["last_load"]["refresh_seconds"] == 155.0
    with data_root() as root:                    # a run closed as failed carries its profile too
        h = Harness(root)
        h.runner.script["backup"] = [yq.Child(1, "boom\n")]
        assert h.go() == 0 and h.work() == 1
        failed = h.rows("failed")[0]
        assert "backup" in failed["timings"] and "total_seconds" in failed
        assert "refresh_seconds" not in (status.read_state().get("last_load") or {})


@test("a Yes too soon: the box comes back after a short pause, says why and asks for one click; the box text says so first")
def _():
    t, shows, pauses = [100.0], [], []
    answers = [(6, 0.5), (6, 9.0)]

    def show(title, text, timeout_ms):
        button, took = answers.pop(0)
        shows.append((text, timeout_ms))
        t[0] += took
        return button

    def pause(seconds):
        pauses.append(seconds)
        t[0] += seconds
    said = yq.windows_box("YQ load 010126", "the table", 540, show=show, clock=lambda: t[0], pause=pause)
    assert said == "yes" and pauses == [yq.BOX_REOPEN_PAUSE_S] == [2]
    assert shows[1][0].startswith("A Yes came within 3 seconds") and "click your answer once" in shows[1][0]
    assert shows[1][1] == 540000 - 500 - 2000, "the pause comes out of the one time budget"
    with data_root() as root:
        w = World(root)
        lines = yq.box_text(RUN, w.plan)[1].splitlines()
        assert lines[-2] == "Read first: a Yes in the first 3 seconds is not counted. Then click once."
        assert lines[-1] == "Yes = load now. No = nothing changes."


@test("the lock names its PC; a lock taken on another PC is never judged, resumed or cleared here")
def _():
    with data_root() as root:
        h = Harness(root)
        assert h.go() == 0
        assert status.read_lock()["host"] == status.this_host() != ""
    with data_root() as root:                    # another PC's lock: its process number means nothing here
        h = Harness(root)
        lock = interrupted(h, upto="refresh")["lock"]
        paths.write_text(paths.lock_file(), json.dumps(dict(lock, host="OTHER-PC")))
        assert h.go() == 2
        assert "another PC (OTHER-PC)" in h.said() and "Nothing was started" in h.said(), h.said()
        assert paths.lock_file().exists() and h.spawn.calls == [] and h.runner.calls == [] and h.box.shown == []
        st = status.lock_state()
        assert st["elsewhere"] == "OTHER-PC" and st["state"] == "interrupted"     # its heartbeat is old
        fresh = datetime.now(timezone.utc).isoformat(timespec="seconds")
        paths.write_text(paths.lock_file(), json.dumps(dict(lock, host="OTHER-PC", heartbeat=fresh)))
        assert status.lock_state()["state"] == "running", "a live heartbeat from that PC reads as running"
    with data_root() as root:                    # a lock that names no PC (written before this change) is this PC's
        h = Harness(root)
        interrupted(h, upto="pre_gate")
        assert status.host_elsewhere(status.read_lock()) is None
        assert h.go() == 0 and "RESUME" in h.box.shown[0][1]
    assert status.host_elsewhere({"host": status.this_host().lower()}) is None, "the same PC in another case"
    assert status.host_elsewhere({"host": "  "}) is None and status.host_elsewhere(None) is None


@test("a resume reads what another load wrote meanwhile: a Data-page upload or a batch import closes it, its own rows do not")
def _():
    upload = ["another load wrote to the database after this check (refresh _upload (data as of 2026-01-01))"]
    with data_root() as root:                    # the launcher's own check of the resume
        h = Harness(root)
        interrupted(h, upto="refresh")
        h.deps.foreign_writes = lambda plan: list(upload)
        assert h.go() == 3, h.out
        closing = h.rows("failed")[-1]
        assert closing["written"] is True and closing["check_again"] is True and closing["moved"] == upload
        assert status.failed_load() == {"run": RUN, "step": "refresh"} and not paths.lock_file().exists()
        assert h.spawn.calls == [] and h.runner.calls == []
    with data_root() as root:                    # ... or the resumed worker's own check, before the refresh runs again
        h = Harness(root)
        interrupted(h, upto="refresh")
        later: list = []
        h.deps.foreign_writes = lambda plan: list(later)
        assert h.go() == 0
        later += upload
        assert h.work() == 3 and "run:refresh" not in h.w.trace
        assert h.rows("failed")[-1]["written"] is True and h.rows("failed")[-1]["moved"] == upload
    with data_root() as root:                    # nothing foreign: the resume goes on as before
        h = Harness(root)
        interrupted(h, upto="refresh")
        asked: list = []
        h.deps.foreign_writes = lambda plan: asked.append(plan["run"]) or []
        assert h.go() == 0 and h.work() == 0 and asked and set(asked) == {RUN}
    with data_root() as root:                    # a first go compares the whole fingerprint: the records are not asked
        h = Harness(root)
        asked = []
        h.deps.foreign_writes = lambda plan: asked.append(1) or []
        assert h.load() == 0 and asked == []


@test("foreign writes: this run's own rows (one loader row per start of its refresh) pass; another folder, a batch, "
      "a loader row too many, an upload since the go do not")
def _():
    class Rows:
        def __init__(self, runs, events):
            self.runs, self.events, self.sql = runs, events, []

        def q(self, sql, params=None):
            db.check_sql(sql)
            self.sql.append((sql, params))
            return self.events if "audit_log" in sql else self.runs
    plan = {"run": RUN, "created_at": "2026-01-01T09:00:00+00:00", "token_parts": {"fingerprint": {"ingest_runs": 7}}}
    own = [{"id": 8, "file": f"refresh {RUN} (data as of 2025-12-31)"}, {"id": 9, "file": "Focus refresh (data as of x)"}]
    with data_root():
        loadplan.journal_append(RUN, "started", at="2026-01-01T09:05:00+00:00")
        loadplan.journal_append(RUN, "step_start", step="refresh")
        assert loadplan.own_attempts(loadplan.journal_read(RUN)) == 1
        assert loadplan.foreign_writes(Rows(own, []), RUN, plan) == []
        rows = Rows(own, [])
        loadplan.foreign_writes(rows, RUN, plan)
        # the upload log is read from the go's first start, not from the check: an upload in between was seen
        # by the go's own comparison of the whole fingerprint
        assert rows.sql[0][1] == (7,) and rows.sql[1][1] == ("2026-01-01T09:05:00+00:00",)
        other = own + [{"id": 10, "file": "refresh _upload (data as of 2026-01-01)"},
                       {"id": 11, "file": "batch 4 committed by owner (data as of 2026-01-01)"},
                       {"id": 12, "file": "refresh 020126_0900 (data as of 2026-01-01)"}]
        said = loadplan.foreign_writes(Rows(other, [{"event": "ingest", "at": "2026-01-01 09:30", "recognised": "3"}]),
                                       RUN, plan)
        assert len(said) == 4 and "refresh _upload" in said[0] and "batch 4" in said[1] and "020126_0900" in said[2]
        assert "ingest" in said[3]
        # an upload that died after its loader and before its own row: one loader row more than this run's attempts
        died = own + [{"id": 10, "file": "Focus refresh (data as of 2026-01-01)"}]
        said = loadplan.foreign_writes(Rows(died, []), RUN, plan)
        assert said == ["1 more loader record(s) than this run's own refreshes since the check: another load ran the "
                        "loader"], said
        loadplan.journal_append(RUN, "step_start", step="refresh", attempt=2)          # its own second run: two rows
        assert loadplan.foreign_writes(Rows(died, []), RUN, plan) == []
        # an upload that recognised no report wrote nothing; the other upload events count
        quiet = [{"event": "ingest", "at": "2026-01-01 09:30", "recognised": "0"}]
        assert loadplan.foreign_writes(Rows(own, quiet), RUN, plan) == []
        marked = [{"event": "ingest_markers", "at": "2026-01-01 09:31", "recognised": ""}]
        assert "ingest_markers" in loadplan.foreign_writes(Rows(own, marked), RUN, plan)[0]
    with data_root():                            # no journal yet: the check's own time is where the log is read from
        rows = Rows(own, [])
        loadplan.foreign_writes(rows, RUN, plan)
        assert rows.sql[1][1] == ("2026-01-01T09:00:00+00:00",)
        assert "1 more loader record" in loadplan.foreign_writes(Rows(own, []), RUN, plan)[0]
        assert loadplan.foreign_writes(Rows(own, []), RUN, {"run": RUN}) == [], "no fingerprint, no time: nothing"
    # the texts it reads are the ones the programs write: a change there must fail here first
    refresh = (ROOT / "scripts" / "refresh.py").read_text(encoding="utf-8")
    loader = (ROOT / "scripts" / "load_supabase.py").read_text(encoding="utf-8")
    batch = (ROOT / "app" / "ingest_batch_api.py").read_text(encoding="utf-8")
    assert 'f"refresh {Path(src_path).name} (data as of {data_date})"' in refresh
    assert '"file": f"Focus refresh (data as of {data_date})"' in loader and loadplan.LOADER_RUN_PREFIX == "Focus refresh ("
    assert 'f"batch {batch_id} committed by {actor} (data as of {d})"' in batch
    main_py = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    for event in ('"ingest"', '"ingest_markers"', '"ingest_purge"'):
        assert f"log_event(user.email, {event}" in main_py or f"log_event(admin.email, {event}" in main_py, event


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
    paths.set_data_root(None)
    config.use(None)
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
