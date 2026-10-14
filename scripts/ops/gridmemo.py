"""One parse of each report file during a load check (scripts/ops/load_check.py).

The check reads some workbooks more than once: the stock ledger for its own comparison and again in
the loader's read-only preview (app.ingest_batch.parse_folder), the stock balance for its trim, its
proof and the preview. Parsing the stock ledger alone takes about a quarter of a minute. While a
check runs, scripts.ingest.read_grid is wrapped (GridMemo.active):

- every read is counted and timed per file, so the check's plan.json says what parsing cost;
- with memo on, a second read of the SAME CONTENT gets a copy of the first grid. The key is the
  file's sha256, so a staged copy and its original share one parse, and a file that changed
  between two reads is parsed again. Each caller gets its own copy: no caller sees another's edits.

The original function is put back when the check ends, whatever happened. Nothing here writes."""
from __future__ import annotations

import contextlib
import hashlib
import time
from pathlib import Path


class GridMemo:
    def __init__(self, memo: bool = True):
        self.memo = bool(memo)
        self.reads: dict[str, dict] = {}         # file name -> {"parsed": n, "served": n, "seconds": s}
        self._grids: dict[str, object] = {}      # sha256 -> the first grid parsed from that content

    def _note(self, name: str, parsed: bool, seconds: float) -> None:
        row = self.reads.setdefault(name, {"parsed": 0, "served": 0, "seconds": 0.0})
        row["parsed" if parsed else "served"] += 1
        row["seconds"] = round(row["seconds"] + seconds, 2)

    def parse_seconds(self) -> float:
        return round(sum(r["seconds"] for r in self.reads.values()), 2)

    def summary(self) -> dict:
        return {"memo": self.memo, "seconds": self.parse_seconds(), "files": dict(sorted(self.reads.items()))}

    @contextlib.contextmanager
    def active(self):
        from scripts import ingest as ing
        original = ing.read_grid

        def read_grid(path):
            p = Path(path)
            t = time.perf_counter()
            key = None
            if self.memo:
                try:
                    key = hashlib.sha256(p.read_bytes()).hexdigest()
                except OSError:
                    key = None                    # unreadable here: the parser says why
                if key is not None and key in self._grids:
                    grid = self._grids[key].copy()
                    self._note(p.name, False, time.perf_counter() - t)
                    return grid
            grid = original(p)
            if key is not None:
                self._grids[key] = grid.copy()
            self._note(p.name, True, time.perf_counter() - t)
            return grid

        ing.read_grid = read_grid
        try:
            yield self
        finally:
            ing.read_grid = original
            self._grids.clear()
