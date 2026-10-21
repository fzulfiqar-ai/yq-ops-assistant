"""Where the ops engine finds things.

The CODE root is this checkout. The DATA root is the main project folder: the only place that holds
the Focus drops (Uplaods/<ddmmyy>/), business_data/ and exports/. They are the same folder once the
project folder is on main; in a code worktree (.claude/worktrees/<name>) the data root is the main
checkout above it, exactly as tests/test_r2_importer.py resolves it (YQ_MAIN_CHECKOUT wins).

Outputs go only under business_data/ and exports/ of the data root (both git-ignored): write_text()
refuses any other place, so a report can never land in a tracked folder.
"""
from __future__ import annotations

import os
import re
import tempfile
from datetime import date
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[2]
DROP_FOLDERS = ("Uplaods", "Uploads")          # the owner's folder is spelt 'Uplaods'; both are read
_DDMMYY = re.compile(r"^(\d{2})(\d{2})(\d{2})$")

_override: Path | None = None


def set_data_root(path) -> None:
    """--data-root on the command line (tests use it too)."""
    global _override
    _override = Path(path).resolve() if path else None


def root_is_set() -> bool:
    """True when the data root was named (--data-root, or a test's set_data_root), not found by itself."""
    return _override is not None


def is_worktree() -> bool:
    """True when this checkout is a linked worktree under <main>/.claude/worktrees/."""
    return CODE_ROOT.parent.name == "worktrees" and CODE_ROOT.parent.parent.name == ".claude"


def data_root() -> Path:
    if _override is not None:
        return _override
    env = os.environ.get("YQ_MAIN_CHECKOUT")
    if env:
        return Path(env)
    return CODE_ROOT.parents[2] if is_worktree() else CODE_ROOT


def ops_dir() -> Path:
    return data_root() / "business_data" / "ops"


def config_file() -> Path:
    return ops_dir() / "config.json"


def state_file() -> Path:
    return ops_dir() / "state.json"


def lock_file() -> Path:
    return ops_dir() / "LOCK"


def loads_dir() -> Path:
    return ops_dir() / "loads"


def golden_dir() -> Path:
    return ops_dir() / "golden"


def staging_dir() -> Path:
    return data_root() / "business_data" / "staging"


def published_dir(as_on: date) -> Path:
    """The set that is loaded: scripts/verify_numbers.py reads the newest 'Focus ERP 20*' folder."""
    return data_root() / "business_data" / f"Focus ERP {as_on.isoformat()}"


def exports_dir(data_date: date | str | None = None) -> Path:
    base = data_root() / "exports" / "ops"
    if data_date is None:
        return base
    return base / (data_date.isoformat() if isinstance(data_date, date) else str(data_date))


def reports_dir(kind: str) -> Path:
    """exports/reports/weekly, monthly or stock (the workbooks he forwards), or counts (the count sheets
    he prints and types the counts into)."""
    if kind not in ("weekly", "monthly", "stock", "counts"):
        raise ValueError("reports_dir: kind is 'weekly', 'monthly', 'stock' or 'counts'")
    return data_root() / "exports" / "reports" / kind


def drop_date(name: str) -> date | None:
    """'071026' -> 2026-10-07 (the folder name spells the date as ddmmyy); anything else -> None."""
    m = _DDMMYY.match(name.strip())
    if not m:
        return None
    d, mth, y = (int(g) for g in m.groups())
    try:
        return date(2000 + y, mth, d)
    except ValueError:
        return None


def drop_folders() -> list[tuple[date, Path]]:
    """Every dated drop folder, oldest first. Sorted by the date the name spells, never by name:
    '280926' sorts after '071026' as text but is nine days older."""
    out: list[tuple[date, Path]] = []
    for top in DROP_FOLDERS:
        base = data_root() / top
        if not base.is_dir():
            continue
        for p in base.iterdir():
            d = drop_date(p.name) if p.is_dir() else None
            if d is not None:
                out.append((d, p))
    return sorted(out, key=lambda t: (t[0], str(t[1])))


def find_drop(arg: str | None) -> Path | None:
    """The drop folder for 'ddmmyy' (or a path); with no argument the latest one."""
    if arg:
        p = Path(arg)
        if p.is_dir():
            return p.resolve()
        for top in DROP_FOLDERS:
            cand = data_root() / top / arg
            if cand.is_dir():
                return cand
        return None
    drops = drop_folders()
    return drops[-1][1] if drops else None


def _allowed_output(path: Path) -> bool:
    root = data_root().resolve()
    p = path.resolve()
    for sub in ("business_data", "exports"):
        base = root / sub
        if p == base or base in p.parents:
            return True
    return False


def allowed_output(path) -> bool:
    """True when `path` is under business_data/ or exports/ of the data root: the only places
    scripts/ops may write, copy to or delete from."""
    return _allowed_output(Path(path))


def copy_file(src, dst) -> Path:
    """Copy one file (bytes and times) to a place under business_data/ or exports/ of the data
    root: to a temp name first, then moved into place. The source is never changed."""
    import shutil
    src, dst = Path(src), Path(dst)
    if not _allowed_output(dst):
        raise PermissionError(f"scripts/ops writes only under business_data/ and exports/: {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".copying")
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    finally:
        if tmp.exists():
            tmp.unlink()
    return dst


def remove_file(path) -> bool:
    """Delete one file under business_data/ or exports/ of the data root (the guard of write_text).
    A missing file is not an error. True when a file was removed."""
    path = Path(path)
    if not _allowed_output(path):
        raise PermissionError(f"scripts/ops removes only under business_data/ and exports/: {path}")
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def write_text(path: Path, text: str) -> Path:
    """Write a UTF-8 text file under business_data/ or exports/ of the data root: to a temp file
    first, then moved into place (a half-written file is never left behind)."""
    path = Path(path)
    if not _allowed_output(path):
        raise PermissionError(f"scripts/ops writes only under business_data/ and exports/: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return path


def append_lines(path: Path, lines: list[str]) -> Path:
    """Append lines (UTF-8, "\\n" line ends) to a file under business_data/ or exports/ of the data root,
    the folder and the file made when they are not there. The guard is write_text's. The lines are
    flushed and synced to the disk before the file is closed. No locking: one writer at a time.
    A line holding a line break is refused (it would be two lines in a log read line by line)."""
    path = Path(path)
    if not _allowed_output(path):
        raise PermissionError(f"scripts/ops writes only under business_data/ and exports/: {path}")
    if any("\n" in s or "\r" in s for s in lines):
        raise ValueError("append_lines: a line holds a line break")
    if not lines:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write("".join(s + "\n" for s in lines))
        f.flush()
        os.fsync(f.fileno())
    return path
