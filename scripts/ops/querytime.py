"""Seconds per report of the load check's preview (scripts/ops/load_check.py, step 7).

The preview (app.ingest_batch.run_preview, read-only) asks the database through ONE backend object:
for each report a column lookup and then one diff (the report's parsed rows travel as one jsonb
parameter, and the report's target table is the diff's second parameter), then a few helpers
(sales by day and by salesman, the stock snapshot, prices, item aliases, MRNs). Nobody could tell
which report cost what: the step took about 200 of a check's 230 seconds.

TimedBackend stands in for that object while the preview runs. Every attribute and every method is
the backend's own, handed over unchanged (an attribute set on it is set on the backend); query() is
the backend's query, called with the same arguments, its answer or its error passed on as it came.
Around each call it reads a clock and puts the seconds down to
  - a report: the target the call names (the diff's second parameter, or the one parameter of the
    report's column lookup), when that name is one of `targets`;
  - else a helper: the name of the function of the preview that asked (its leading _ dropped).
When a report's diff has answered, on_report(target, seconds, ok) is told, once per diff: the check
prints one short progress line from it. Nothing here reads, writes or sends anything itself."""
from __future__ import annotations

import sys
import time


class TimedBackend:
    """The preview's backend, with its query() timed (see the module's text)."""

    def __init__(self, inner, targets=(), on_report=None, clock=time.perf_counter):
        state = {"inner": inner, "targets": frozenset(str(t) for t in targets), "on_report": on_report,
                 "clock": clock, "reports": {}, "helpers": {}, "queries": 0, "seconds": 0.0}
        object.__setattr__(self, "_timed_state", state)

    # ── the backend, unchanged ────────────────────────────────────────────────
    def __getattr__(self, name):
        # asked only for what this object does not have itself: everything but query() and the
        # bookkeeping below is the backend's own attribute
        return getattr(object.__getattribute__(self, "_timed_state")["inner"], name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_timed_state")["inner"], name, value)

    def __delattr__(self, name):
        delattr(object.__getattribute__(self, "_timed_state")["inner"], name)

    def query(self, sql, params=None):
        st = object.__getattribute__(self, "_timed_state")
        try:
            asked_by = sys._getframe(1).f_code.co_name      # noqa: SLF001  (who asked: a label, nothing more)
        except (AttributeError, ValueError):
            asked_by = ""
        clock = st["clock"]
        began = clock()
        ok = False
        try:
            answer = st["inner"].query(sql, params)
            ok = True
            return answer
        finally:
            _timed_note(st, asked_by, params, clock() - began, ok)

    def timing_summary(self) -> dict:
        """{reports: {target: s}, helpers: {name: s}, queries: n, seconds: s} for plan.json."""
        st = object.__getattribute__(self, "_timed_state")
        return {"reports": {k: round(v, 2) for k, v in st["reports"].items()},
                "helpers": {k: round(v, 2) for k, v in sorted(st["helpers"].items())},
                "queries": int(st["queries"]), "seconds": round(st["seconds"], 2)}


def _timed_report(st: dict, params) -> tuple[str | None, bool]:
    """(the report a call is for, is it that report's diff). The diff is called with
    [rows, target, ...]; the report's column lookup with [target]."""
    if not isinstance(params, (list, tuple)):
        return None, False
    if len(params) >= 2 and isinstance(params[1], str) and params[1] in st["targets"]:
        return params[1], True
    if len(params) == 1 and isinstance(params[0], str) and params[0] in st["targets"]:
        return params[0], False
    return None, False


def _timed_note(st: dict, asked_by: str, params, seconds: float, ok: bool) -> None:
    try:
        st["queries"] += 1
        st["seconds"] += seconds
        report, is_diff = _timed_report(st, params)
        if report is not None:
            st["reports"][report] = st["reports"].get(report, 0.0) + seconds
        else:
            helper = (asked_by or "").lstrip("_") or "other"
            st["helpers"][helper] = st["helpers"].get(helper, 0.0) + seconds
        if report is not None and is_diff and st["on_report"] is not None:
            st["on_report"](report, round(st["reports"][report], 1), ok)
    except Exception:  # noqa: BLE001  (a progress line must never change what the preview gets)
        pass


def unwrap(backend):
    """The backend itself, whether or not it is wrapped in a TimedBackend."""
    if isinstance(backend, TimedBackend):
        return object.__getattribute__(backend, "_timed_state")["inner"]
    return backend
