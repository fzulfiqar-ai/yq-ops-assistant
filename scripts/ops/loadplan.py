"""The contract between `load check` (read-only, this package) and the writer (scripts/yq_load_go.py).

A RUN is one check of one drop folder: its name is the staging folder name, <ddmmyy>_<HHMM>.
  business_data/staging/<run>/            the copies that will be loaded (and nothing else)
  business_data/ops/loads/<run>/plan.json what the owner was shown: verdicts, expected figures,
                                          the parts of the token, the options he asked for
  business_data/ops/loads/<run>/go.cmd    the same GO line, for a double-click
  business_data/ops/loads/<run>/journal.jsonl   one line per step the writer started / finished

The TOKEN binds the go to what was shown. It is the sha256 of: every file of the drop folder and of
staging (name, size, sha256), HEAD, the live commit, and a fingerprint of the Focus tables. If any
part moved between the check and the go, the writer answers "check again" and writes nothing. It is
printed in letters only (0-9 written as g-p), so it can never look like an ID number.

plan.json, as the writer reads it: run, drop (the folder), created_at, token (None for a dev or a
stopped check), token_parts, options, stops, flags, not_loaded, expected {lines ...},
verdicts [{file, label, verdict, decision, staged, reason}], as_on (the as-on date per report kind
that LOADS: the published folder is named after the newest of them, price books aside) and
as_on_all (the same for every report of the folder).

Everything here reads files or runs SELECTs; the lock and the journal are local files.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from scripts.ops import paths

RUN_RE = re.compile(r"^\d{6}_\d{4}$")
WRITER_MODULE = "scripts.yq_load_go"
STEPS = ("token", "backup", "pre_gate", "wait_tick", "refresh", "reconcile", "post_gate", "publish")
VERDICTS = ("load", "trim", "hold_back", "leave_out", "needs_decision", "not_loaded", "stop")
# Focus tables the load changes: count and max id of each go into the fingerprint.
FINGERPRINT_TABLES = ("orders", "order_lines", "stock_movements", "stock_balance", "ar_ageing",
                      "selling_prices", "product_profitability")
MARKERS = ("REPLACE_OK", "PARTIAL_OK")       # the loader's marker files (scripts/load_supabase.py)
_DIGIT_TO_LETTER = str.maketrans("0123456789", "ghijklmnop")


def is_run(name: str) -> bool:
    return bool(RUN_RE.match(name or ""))


def run_dir(run: str) -> Path:
    if not is_run(run):
        raise ValueError(f"not a run name (ddmmyy_HHMM): {run!r}")
    return paths.loads_dir() / run


def staging_path(run: str) -> Path:
    if not is_run(run):
        raise ValueError(f"not a run name (ddmmyy_HHMM): {run!r}")
    return paths.staging_dir() / run


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def manifest(folder: Path) -> dict:
    """{file name: {size, sha256}} for every file directly in `folder` (sub-folders are not part
    of a load). Names are sorted, so the same folder always gives the same manifest."""
    out: dict = {}
    folder = Path(folder)
    if not folder.is_dir():
        return out
    for p in sorted(folder.iterdir(), key=lambda q: q.name.lower()):
        if p.is_file():
            out[p.name] = {"size": p.stat().st_size, "sha256": sha256_file(p)}
    return out


def fingerprint(session) -> dict:
    """Count and max id of the Focus tables, the newest ingest_runs id and the open portal batches:
    what a load (this one or somebody else's) would move. `session` is scripts.ops.db.Session."""
    out: dict = {}
    for table in FINGERPRINT_TABLES:
        row = session.q(f"select count(*) as n, coalesce(max(id), 0) as max_id from {table}")[0]
        out[table] = [int(row["n"]), int(row["max_id"])]
    out["ingest_runs"] = int(session.q("select coalesce(max(id), 0) as max_id from ingest_runs")[0]["max_id"])
    # the table arrives with the batch importer; it is looked up first so a missing table never
    # raises inside the session (a rollback there would undo the preview backend's settings)
    has = session.q("select to_regclass('public.ingest_batches') is not null as ok")[0]["ok"]
    rows = session.q("select id from ingest_batches where status in ('parsing', 'previewed') order by id") if has else []
    out["ingest_batches_open"] = [int(r["id"]) for r in rows]
    return out


def collect_parts(drop: Path, run: str, root: Path, live: str, session) -> dict:
    """The token's parts as they are NOW: the check computes them once, the writer computes them
    again (twice) and compares. `live` is the commit /health reports; `session` a read-only
    scripts.ops.db.Session."""
    from scripts.ops import gitinfo
    return {
        "drop": manifest(Path(drop)),
        "staging": manifest(staging_path(run)),
        "head": gitinfo.head(root) or "",
        "live": str(live or ""),
        "fingerprint": fingerprint(session),
    }


def letters_only(hex_text: str) -> str:
    return hex_text.translate(_DIGIT_TO_LETTER)


def token_of(parts: dict) -> str:
    """parts = {drop: manifest, staging: manifest, head: str, live: str, fingerprint: dict}."""
    need = {"drop", "staging", "head", "live", "fingerprint"}
    if set(parts) != need:
        raise ValueError(f"token parts must be exactly {sorted(need)}")
    canon = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return letters_only(hashlib.sha256(canon.encode("utf-8")).hexdigest())[:32]


def moved_parts(old: dict, new: dict) -> list[str]:
    """Which parts of the token changed, in plain words (for the 'check again' message)."""
    out: list[str] = []
    for side, label in (("drop", "the drop folder"), ("staging", "the staged copies")):
        a, b = old.get(side) or {}, new.get(side) or {}
        for name in sorted(set(a) | set(b)):
            if a.get(name) != b.get(name):
                out.append(f"{label}: {name}")
    if old.get("head") != new.get("head"):
        out.append("this folder's programs (HEAD)")
    if old.get("live") != new.get("live"):
        out.append("the live site's commit")
    fa, fb = old.get("fingerprint") or {}, new.get("fingerprint") or {}
    for name in sorted(set(fa) | set(fb)):
        if fa.get(name) != fb.get(name):
            out.append(f"the database table {name}")
    return out


def go_line(run: str, token: str) -> str:
    return f"python -m {WRITER_MODULE} --run {run} --token {token}"


def write_plan(plan: dict) -> Path:
    """plan needs at least: run, drop (path), created_at, verdicts, token, token_parts."""
    run = plan["run"]
    path = run_dir(run) / "plan.json"
    paths.write_text(path, json.dumps(plan, indent=1, sort_keys=True, default=str) + "\n")
    return path


def read_plan(run: str) -> dict:
    return json.loads((run_dir(run) / "plan.json").read_text(encoding="utf-8"))


def write_go_cmd(run: str, token: str, root: Path) -> Path:
    """A double-click fallback: the same command, in the project folder, waiting for a key."""
    text = ("@echo off\r\n"
            f"cd /d \"{root}\"\r\n"
            f"{go_line(run, token)}\r\n"
            "echo.\r\n"
            "pause\r\n")
    return paths.write_text(run_dir(run) / "go.cmd", text)


def journal_file(run: str) -> Path:
    return run_dir(run) / "journal.jsonl"


def journal_append(run: str, event: str, **fields) -> dict:
    """Append one line to the run's journal (event: started, step_start, step_ok, step_failed,
    note, finished, failed). Opened in append mode so a crash never loses earlier lines."""
    row = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event}
    row.update(fields)
    f = journal_file(run)
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "a", encoding="utf-8", newline="\n") as out:
        out.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    return row


def journal_read(run: str) -> list[dict]:
    f = journal_file(run)
    if not f.is_file():
        return []
    rows = []
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def run_result(run: str) -> str:
    """NOT_STARTED | RUNNING | FINISHED | FAILED from the journal alone (the lock tells
    INTERRUPTED: see scripts.ops.status.lock_state)."""
    rows = journal_read(run)
    if not rows:
        return "NOT_STARTED"
    last = [r for r in rows if r.get("event") in ("finished", "failed")]
    if last:
        return "FINISHED" if last[-1]["event"] == "finished" else "FAILED"
    return "RUNNING"


def last_step(run: str) -> str | None:
    for row in reversed(journal_read(run)):
        if row.get("step"):
            return row["step"]
    return None


def latest_run() -> str | None:
    base = paths.loads_dir()
    if not base.is_dir():
        return None
    runs = sorted((p for p in base.iterdir() if p.is_dir() and is_run(p.name)),
                  key=lambda p: p.stat().st_mtime)
    return runs[-1].name if runs else None
