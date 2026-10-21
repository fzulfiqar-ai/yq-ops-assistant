"""python -m scripts.ops status : five lines on where things stand.

  1. the data dates and the last load        4. what is waiting (drop folders not loaded yet)
  2. this folder against the live site        5. open items on the action register
  3. the load lock (none / running / INTERRUPTED)

Also home of the small local state shared by the commands (business_data/ops/state.json and LOCK).
The lock belongs to scripts/yq_load_go.py; this module only reads it. A process is checked with
OpenProcess on Windows: os.kill(pid, 0) would TERMINATE it there, so it is never used.
"""
from __future__ import annotations

import ctypes
import json
import os
import platform
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from scripts.ops import config, fmt, gitinfo, paths

HEARTBEAT_STALE_S = 120
ANCHOR_SQL = (
    "SELECT (SELECT MAX(sale_date) FROM v_sales)::text AS focus_to, "
    "(SELECT MAX(as_of_date) FROM stock_balance)::text AS stock_as_of, "
    "(SELECT MAX(as_of_date) FROM v_receivables)::text AS ar_as_of"
)
BAHRAIN = timezone(timedelta(hours=3))


def today_bahrain() -> date:
    return datetime.now(BAHRAIN).date()


def read_state() -> dict:
    f = paths.state_file()
    if not f.is_file():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def write_state(patch: dict) -> dict:
    """Merge `patch` into state.json (a local, git-ignored file)."""
    state = read_state()
    state.update(patch)
    paths.write_text(paths.state_file(), json.dumps(state, indent=1, sort_keys=True, default=str) + "\n")
    return state


def pid_alive(pid: int) -> bool:
    """Is a process with this id running? Windows: OpenProcess + WaitForSingleObject(0)."""
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        synchronize, query_limited = 0x00100000, 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(synchronize | query_limited, False, int(pid))
        if not handle:
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == 0x102     # WAIT_TIMEOUT = still running
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    return Path(f"/proc/{int(pid)}").exists()


REHEARSAL_PREFIX = "000000_"       # a rehearsal's run name (scripts/yq_load_go.py --rehearse)


def this_host() -> str:
    """This PC's name, as the lock records it (upper case, one space between words)."""
    return " ".join(str(os.environ.get("COMPUTERNAME") or platform.node() or "").split()).upper()


def host_elsewhere(lock: dict | None) -> str | None:
    """The name of the PC that took this lock when it is not this one, else None. The project
    folder syncs through OneDrive, so a lock file can arrive from another PC: its process ids mean
    nothing here, and such a lock is never judged dead, resumed or cleared on this PC. A lock that
    names no PC (one written before the lock carried it) is read as this PC's."""
    host = " ".join(str((lock or {}).get("host") or "").split()).upper()
    here = this_host()
    return host if host and here and host != here else None


def read_lock() -> dict | None:
    """The lock file as a dict, None when there is none. The load's worker replaces the file every
    10 seconds (temp file + rename) and Windows refuses a read that meets that instant, so the read
    is tried again a few times before the error is passed on."""
    f = paths.lock_file()
    text = None
    for attempt in range(8):
        if not f.is_file():
            return None
        try:
            text = f.read_text(encoding="utf-8")
            break
        except FileNotFoundError:
            return None
        except OSError:
            if attempt == 7:
                raise
            time.sleep(0.05 * (attempt + 1))
    try:
        data = json.loads(text or "")
    except ValueError:
        return {"run": "?", "step": "?", "pid": 0}
    return data if isinstance(data, dict) else {"run": "?", "step": "?", "pid": 0}


def lock_state(now: datetime | None = None) -> dict:
    """{'state': 'none' | 'running' | 'interrupted', 'run', 'step'}: interrupted = the lock's
    process is gone, or its heartbeat is older than HEARTBEAT_STALE_S. The dead lock of a REHEARSAL
    is not an interrupted load (a rehearsal never reaches the database): it reads 'none' with
    'rehearsal_left' naming it, and the next real load clears it. A lock taken on another PC
    (host_elsewhere) carries 'elsewhere' with that PC's name: its process cannot be looked at from
    here, so its heartbeat alone says running or interrupted, and nothing on this PC settles it."""
    lock = read_lock()
    if lock is None:
        return {"state": "none"}
    now = now or datetime.now(timezone.utc)
    beat = lock.get("heartbeat")
    age = None
    if beat:
        try:
            age = (now - datetime.fromisoformat(str(beat))).total_seconds()
        except (TypeError, ValueError):
            age = None
    away = host_elsewhere(lock)
    alive = True if away else pid_alive(int(lock.get("pid") or 0))
    stale = age is None or age > HEARTBEAT_STALE_S
    state = "running" if (alive and not stale) else "interrupted"
    run, step = lock.get("run"), lock.get("step") or "start"
    if state == "interrupted" and str(run or "").startswith(REHEARSAL_PREFIX) and not away:
        return {"state": "none", "rehearsal_left": run, "step": step}
    out = {"state": state, "run": run, "step": step}
    if away:
        out["elsewhere"] = away
    return out


def when(text) -> datetime | None:
    """A time as the journal and state.json write it (ISO, with its offset); None when it is none."""
    try:
        t = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo is not None else t.replace(tzinfo=timezone.utc)


def failed_load(unreadable: list | None = None) -> dict | None:
    """The newest load that was CLOSED as failed after its refresh had begun (the database may hold
    part of it), while no load has finished since. None otherwise. A load whose one transaction
    rolled back is closed with written false (scripts/yq_load_go.py): it wrote nothing, so it is none.

    Every real run's journal is read, not the newest folder's alone: a later check that never
    loaded (he answered No, or it stopped), a rehearsal, or a run that was closed before it wrote
    anything changes nothing in the database, so none of them may take the banner away. Only a
    load that FINISHED after the failure does.

    `unreadable`, when given, collects the runs whose journal could not be read (held by another
    program at this moment, or not text): None then means "none failed" only when it stays empty.
    The load check needs that difference (its "nothing new" must never miss a failed load); for any
    other caller such a journal says nothing, as before."""
    from scripts.ops import loadplan
    last = read_state().get("last_load")
    good = when(last.get("finished_at")) if isinstance(last, dict) else None
    bad: tuple[datetime, str, dict] | None = None
    base = paths.loads_dir()
    for folder in (base.iterdir() if base.is_dir() else []):
        run = folder.name
        if not loadplan.is_run(run) or run.startswith(REHEARSAL_PREFIX) or not folder.is_dir():
            continue
        try:
            rows = loadplan.journal_read(run)
        except OSError:
            if unreadable is not None:
                unreadable.append(run)
            continue                          # a journal held by another program this moment says nothing
        except ValueError:
            if unreadable is None:
                raise                         # as it always did, for a caller that does not ask
            unreadable.append(run)
            continue
        for row in rows:
            at = when(row.get("at"))
            if at is None:
                continue
            if row.get("event") == "finished":
                good = at if good is None or at > good else good
            elif row.get("event") == "failed" and row.get("written") and (bad is None or at > bad[0]):
                bad = (at, run, row)
    if bad is None or (good is not None and good >= bad[0]):
        return None
    return {"run": bad[1], "step": bad[2].get("step") or "?"}


def interrupted_banner() -> str | None:
    """The line every command opens with while a load is not whole: an interrupted one, or one
    closed as failed after it had begun to write."""
    st = lock_state()
    if st["state"] == "interrupted":
        return f"LOAD INTERRUPTED at {st['step']}: run /yq-load"
    if st["state"] == "none":
        failed = failed_load()
        if failed:
            return (f"LAST LOAD FAILED at {failed['step']} (run {failed['run']}): the database may hold part "
                    "of it: run /yq-load")
    return None


def anchors() -> dict:
    """The data dates, read like the Command Centre reads them (latest loaded day, never today)."""
    from scripts.ops import db
    rows = db.rpc(ANCHOR_SQL)
    return rows[0] if rows else {}


def site_state(root: Path | None = None, live: str | None = None) -> dict:
    """This folder against the live site, on the engine files only (scripts/ops/gitinfo.py).
    state: same | behind | release_not_live | changed_here | unknown."""
    root = root or paths.CODE_ROOT
    out: dict = {"state": "unknown", "head": (gitinfo.head(root) or "")[:8]}
    try:
        if live is None:
            from scripts.ops import db
            live = str(db.health(config.get("api.health_url")).get("commit") or "")
        out["live"] = live[:8]
        out["live_full"] = live               # exactly as /health reports it (the token's 'live' part)
        full = gitinfo.resolve(root, live) if live else None
        if not full or not gitinfo.is_ancestor(root, full, "HEAD"):
            out["state"] = "behind"
            return out
        local = gitinfo.engine_local_changes(root)
        if local:
            out.update(state="changed_here", paths=local)
            return out
        ahead = gitinfo.engine_committed_diff(root, full, "HEAD")
        if ahead:
            out.update(state="release_not_live", paths=ahead)
            return out
        out["state"] = "same"
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:160]
    return out


SITE_TEXT = {
    "same": "same programs as the live site",
    "behind": "this folder is behind the live site: pull it",
    "release_not_live": "a portal release is merged but not live: deploy it, then load",
    "changed_here": "a program file was changed on this PC: tell Claude",
    "unknown": "could not compare with the live site",
}


def unloaded_drops(last_run: str | None) -> list[str]:
    """Drop folders dated after the last loaded one (names as he typed them)."""
    last = paths.drop_date((last_run or "")[:6]) if last_run else None
    return [p.name for d, p in paths.drop_folders() if last is None or d > last]


def register_counts() -> dict:
    f = paths.ops_dir() / "alerts.jsonl"
    counts: dict[str, int] = {}
    if not f.is_file():
        return counts
    latest: dict[str, dict] = {}
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("key"):
            latest[row["key"]] = row
    for row in latest.values():
        if row.get("state", "open") == "open":
            counts[row.get("level", "CHECK")] = counts.get(row.get("level", "CHECK"), 0) + 1
    return counts


def add_arguments(parser) -> None:
    parser.add_argument("--offline", action="store_true", help="local files only: no database, no live-site call")


def run(args) -> dict:
    state = read_state()
    last = state.get("last_load") or {}
    lines: list[str] = []
    data_date = None
    warnings: list[str] = []

    if getattr(args, "offline", False):
        lines.append("Data: not read (offline). Last load: " + _last_load_text(last))
    else:
        try:
            a = anchors()
            data_date = fmt.as_date(a.get("focus_to"))
            age = (today_bahrain() - data_date).days if data_date else None
            lines.append(
                f"Data: sales to {fmt.day(data_date)}" + (f" ({age} days old)" if age is not None else "")
                + f"; stock as on {fmt.day(a.get('stock_as_of'))}; receivables as on {fmt.day(a.get('ar_as_of'))}."
                + " Last load: " + _last_load_text(last))
        except Exception as exc:  # noqa: BLE001
            warnings.append(str(exc)[:200])
            lines.append("Data: could not be read (" + str(exc)[:80] + "). Last load: " + _last_load_text(last))

    if getattr(args, "offline", False):
        lines.append("This folder: not compared (offline).")
    else:
        site = site_state()
        extra = f" (folder {site.get('head', '?')}, live {site.get('live', '?')})"
        lines.append("This folder: " + SITE_TEXT[site["state"]] + extra + ".")

    lk = lock_state()
    lock_text = {"none": "none", "running": f"running at {lk.get('step')}",
                 "interrupted": f"INTERRUPTED at {lk.get('step')}: run /yq-load"}[lk["state"]]
    if lk.get("rehearsal_left"):
        lock_text = "none (a cut-off rehearsal left its lock; the next load clears it)"
    failed = failed_load() if lk["state"] == "none" else None
    if failed:
        lock_text += f"; LAST LOAD FAILED at {failed['step']} (run {failed['run']}): run /yq-load"
    lines.append("Load lock: " + lock_text + ".")

    waiting = unloaded_drops(last.get("run"))
    lines.append("Waiting: " + (("new reports in " + ", ".join(waiting[-3:]) + ": run /yq-load") if waiting
                                else "no drop folder newer than the last load") + ".")

    counts = register_counts()
    lines.append("Action list: " + (", ".join(f"{n} {lvl}" for lvl, n in sorted(counts.items())) if counts
                                    else "nothing open") + ".")
    return {"ok": True, "command": "status", "data_date": data_date.isoformat() if data_date else None,
            "text": "\n".join(lines), "warnings": warnings}


def _last_load_text(last: dict) -> str:
    if not last:
        return "none recorded"
    return f"{last.get('run', '?')} at {str(last.get('finished_at', '?'))[:16]} ({last.get('result', 'ok')})"
