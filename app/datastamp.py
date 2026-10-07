"""The newest data load this process has seen (release R8).

The portal and the marketplace answer from in-process caches (app/reports.py, app/metrics.py,
app/shop.py). An upload through the Data page drops them itself (app.ai.flush_cache). The same
refresh engine run anywhere else (a developer's PC, a cron) used to go unnoticed here: on
7-Oct-2026 the Dashboard showed September for half an hour after October was loaded.

Every load ends by writing an ingest_runs row: scripts/load_supabase.py once the tables are in,
scripts/refresh.py at the very end, the batch importer on commit. So the highest ingest_runs.id
is the stamp of the newest load. sync() reads it at most once every CHECK_EVERY_S and drops this
process's caches when it has moved. The cached readers call sync() on entry, so any traffic
notices the load: a portal page, the header chip's GET /freshness, a marketplace visit.

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
LOOK_WAIT_S = 1.0                # what a request gives a look that began beside it, at most (0 = never wait)
_UNSEEN = object()               # no stamp noted yet (None is a real answer: an empty table)
_seen: dict = {"read": None, "stamp": _UNSEEN, "at": float("-inf"), "failing": False}
_lock = threading.Lock()


def newest_load() -> int | None:
    """The highest ingest_runs.id, None for an empty table. One small GET with the service client
    (yq_readonly is not granted ingest_runs, so the read-only RPC cannot answer it). The SDK's own
    1 + 2 + 4 s retry on a 503 is off: the next look is CHECK_EVERY_S away anyway."""
    rows = (get_client().table("ingest_runs").select("id").order("id", desc=True)
            .limit(1).retry(False).execute().data or [])
    return int(rows[0]["id"]) if rows else None


def watch(read: Callable[[], int | None] | None = None) -> None:
    """Start noticing loads in this process, from a clean slate: the next look only notes the
    stamp. The API calls it once at boot; a test passes its own reader."""
    _seen.update(read=read or newest_load, stamp=_UNSEEN, at=float("-inf"), failing=False)


def sync() -> bool:
    """Drop this process's caches if a load landed since the last look; True when it did.

    Built to sit at the entry of every cached reader: between looks it costs a comparison. When a
    look is due, ONE caller makes the small read; nobody queues to make another.

    A request that arrives while that look is under way gives it a moment (until LOOK_WAIT_S after
    the look began, at most) instead of answering from a payload that is about to be dropped. A
    page sends its requests together: without this, whichever arrived second (the Dashboard, or
    the header chip beside it) still showed the old numbers. So a look that hangs costs the
    requests that arrived beside it LOOK_WAIT_S, once, and costs every later request nothing.

    The first look after boot only notes the stamp. It never raises: a read that fails keeps the
    stamp it had, and the next look tries again."""
    read = _seen["read"]
    if read is None:
        return False
    since = time.monotonic() - _seen["at"]
    if since >= CHECK_EVERY_S and _lock.acquire(blocking=False):
        try:
            return _look(read)
        finally:
            _lock.release()
    if since < LOOK_WAIT_S and _lock.acquire(timeout=LOOK_WAIT_S - since):
        _lock.release()                              # the look beside us is over (or there was none)
    return False


def _look(read: Callable[[], int | None]) -> bool:
    """One look at the newest load, with the lock held."""
    if time.monotonic() - _seen["at"] < CHECK_EVERY_S:
        return False                                 # another request looked while this one got here
    _seen["at"] = time.monotonic()
    try:
        stamp = read()
        _seen["failing"] = False
        was, _seen["stamp"] = _seen["stamp"], stamp
        if was is _UNSEEN or stamp == was:
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
