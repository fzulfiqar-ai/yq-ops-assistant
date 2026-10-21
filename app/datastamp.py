"""The newest data load this process has seen (release R8).

The portal and the marketplace answer from in-process caches (app/reports.py, app/metrics.py,
app/shop.py, app/transfer_audit.py). An upload through the Data page drops them itself
(app.ai.flush_cache). The same refresh engine run anywhere else (a developer's PC, a cron) used
to go unnoticed here: on 7-Oct-2026 the Dashboard showed September for half an hour after October
was loaded.

Every load ends by writing an ingest_runs row: scripts/load_supabase.py once the tables are in,
scripts/refresh.py at the very end, the batch importer on commit. So the highest ingest_runs.id
is the stamp of the newest load. sync() has it read at most once every CHECK_EVERY_S and this
process's caches are dropped when it has moved. The cached readers call sync() on entry, so any
traffic notices the load: a portal page, the header chip's GET /freshness, a marketplace visit.

The read runs on a thread of its own, never on a request's: a database that takes the request and
does not answer costs a page a moment (LOOK_WAIT_S), not the 60 s the client gives a read.

Off until watch() is called: the API does that at boot (app/main.py). Scripts, crons and the test
suites never read the stamp unless they ask to.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Callable

from app.database import get_client

log = logging.getLogger(__name__)

CHECK_EVERY_S = 30.0             # one small read per process in this long, whatever the traffic
LOOK_WAIT_S = 1.0                # what a request gives a look, at most: the one it began or one under way (0 = never wait)
_UNSEEN = object()               # no stamp noted yet (None is a real answer: an empty table)
_seen: dict = {"read": None, "stamp": _UNSEEN, "at": float("-inf"), "failing": False}
_lock = threading.Lock()         # held from the moment a look begins until its read is back


def newest_load() -> int | None:
    """The highest ingest_runs.id, None for an empty table. One small GET with the service client
    (yq_readonly is not granted ingest_runs, so the read-only RPC cannot answer it). The SDK's own
    1 + 2 + 4 s retry on a 503 is off: the next look is CHECK_EVERY_S away anyway."""
    rows = (get_client().table("ingest_runs").select("id").order("id", desc=True)
            .limit(1).retry(False).execute().data or [])
    return int(rows[0]["id"]) if rows else None


def watch(read: Callable[[], int | None] | None = None) -> None:
    """Start noticing loads in this process, from a clean slate: the first stamp read is the one
    later looks are set against. The API calls it once at boot; a test passes its own reader."""
    _seen.update(read=read or newest_load, stamp=_UNSEEN, at=float("-inf"), failing=False)


def sync() -> bool:
    """Drop this process's caches if a load landed since the last look; True when the look this
    call began dropped them.

    Built to sit at the entry of every cached reader: between looks it costs a comparison. When a
    look is due, ONE caller begins it; nobody queues to begin another. The small read runs on a
    thread of its own (_look), so the database never holds a request: read on the request's own
    thread, a Supabase that accepted the request and did not answer held a warm page for the
    client's 60 s, and the request after it began the next 60 s.

    The request that began the look, and any that arrive while it is under way, give it a moment
    (until LOOK_WAIT_S after it began, at most) instead of answering from a payload that is about
    to be dropped. A page sends its requests together: without this, whichever arrived second (the
    Dashboard, or the header chip beside it) still showed the old numbers. So a look that hangs
    costs the requests that came with it LOOK_WAIT_S, once, and costs every later request nothing.

    It never raises: a read that fails keeps the stamp it had, and the next look tries again."""
    read = _seen["read"]
    if read is None:
        return False
    since = time.monotonic() - _seen["at"]
    found: list[bool] = []                           # what the look this call began found, once it is over
    if since >= CHECK_EVERY_S and _lock.acquire(blocking=False):
        if not _begin():
            _lock.release()
            return False
        try:                                         # the look releases the lock when its read is back
            threading.Thread(target=_look, args=(read, found), name="datastamp-look", daemon=True).start()
        except Exception as e:  # noqa: BLE001 -- no thread to be had: no look this time, and no request fails for it
            _lock.release()
            log.warning("newest data load not looked for (cached numbers stay until their own expiry): %s", e)
            return False
        since = 0.0
    if since < LOOK_WAIT_S and _lock.acquire(timeout=LOOK_WAIT_S - since):
        _lock.release()                              # the look is over (or there was none)
    return bool(found and found[0])


def _begin() -> bool:
    """With the lock just taken: does a look begin now? Not when another request looked while this
    one got here. Notes when it began."""
    now = time.monotonic()
    if now - _seen["at"] < CHECK_EVERY_S:
        return False
    _seen["at"] = now
    return True


def _look(read: Callable[[], int | None], found: list[bool]) -> None:
    """One look at the newest load, on its own thread. sync() took the lock for it; it is released
    here, whatever the read did."""
    try:
        found.append(_note(read))
    finally:
        _lock.release()


def _note(read: Callable[[], int | None]) -> bool:
    """Read the stamp and set it against the one noted before; True when the caches were dropped."""
    began = _seen["at"]
    try:
        stamp = read()
        after_failure, _seen["failing"] = _seen["failing"], False
        was, _seen["stamp"] = _seen["stamp"], stamp
        if was is _UNSEEN:
            # The first stamp this process learns is only a note while nothing can have been built
            # without it: every reader looks before it builds and gives the look LOOK_WAIT_S. After a
            # look that failed, or one slower than that moment, pages were built with no stamp to set
            # a later load against: one that landed meanwhile would never be noticed. Drop them once.
            if not after_failure and time.monotonic() - began <= LOOK_WAIT_S:
                return False
            drop_process_caches()
            log.info("newest data load %s first read after pages may have been built: cached numbers dropped once", stamp)
            return True
        if stamp == was:
            return False
        drop_process_caches()
        log.info("data load %s noticed (was %s): cached numbers dropped", stamp, was)
        return True
    except Exception as e:  # noqa: BLE001 -- a request never fails because of this
        # the first failure of a run is a warning; the repeats every CHECK_EVERY_S are not
        log.log(logging.DEBUG if _seen["failing"] else logging.WARNING,
                "newest data load not read (cached numbers stay until their own expiry): %s", e)
        _seen["failing"] = True
        return False


def drop_process_caches() -> None:
    """The in-process half of app.ai.flush_cache(): every payload this process built from loaded
    data. Each part is dropped on its own, so one that fails never keeps the others."""
    try:  # the report + dashboard payloads, and with them the reps' sales block and the Command Centre
        from app.reports import invalidate_dashboard_cache
        invalidate_dashboard_cache()
    except Exception as e:  # noqa: BLE001
        log.warning("report caches not dropped: %s", e)
    # the assistant's "data as of" label, only where the assistant is loaded: importing app.ai here
    # would pull the LLM client into a request that never needed it (getattr: the module may be
    # half-way through its own import on another thread, and then it has no label yet)
    label = getattr(sys.modules.get("app.ai"), "_data_date_cache", None)
    if isinstance(label, dict):
        label.update(d="", at=0.0)
    try:  # the shop payload derives from the same data
        from app.shop import invalidate
        invalidate()
    except Exception as e:  # noqa: BLE001
        log.warning("shop context not dropped: %s", e)
    try:  # the Stock Moves pairing (GET /stock/flow, /stock/recon), worked out once per load from the stock ledger
        from app.transfer_audit import invalidate as invalidate_transfers
        invalidate_transfers()
    except Exception as e:  # noqa: BLE001
        log.warning("stock moves pairing not dropped: %s", e)
