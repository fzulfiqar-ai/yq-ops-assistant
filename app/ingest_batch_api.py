"""Routes for the transactional batch importer (release R2a). Registered from app/main.py next to
the shop routes; the old POST /ingest path is untouched and stays the default until the replay in
tests/test_r2_importer.py has proven the batch path identical.

    POST /ingest/preview                 admin  upload Focus exports -> stage -> preview (no live write)
    GET  /ingest/batches                 admin  batch history
    GET  /ingest/batches/{id}            admin  one batch (summary, exceptions, status)
    POST /ingest/batches/{id}/commit     admin  {acknowledged: [codes]} -> ingest_commit (one transaction)
    POST /ingest/batches/{id}/undo       admin  ingest_undo
    POST /ingest/batches/{id}/reject     admin  a previewed batch the admin does not want (status only)

Every database call here is a blocking psycopg call on the API's OWN session (DATABASE_URL, see
app/ingest_batch.connect_direct) -- never PostgREST, whose RPCs are capped at 8 s. They all run
off the event loop (run_in_threadpool), the audit/cache/catalog housekeeping included: the
one-worker container must keep answering /health or Render kills it mid-upload (the 14-Sep-2026
lesson). Without DATABASE_URL the routes say so and the default Upload & refresh is unaffected.

`backend_factory` is the seam the tests use to point these routes at a fake or a local Postgres.

No `from __future__ import annotations` here: the request models are defined inside register(),
and FastAPI cannot resolve a postponed (string) annotation to a local class -- it silently turned
`body: AckRequest` into a query parameter (422 on every commit).
"""
import logging
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app import audit

log = logging.getLogger("yq.ingest_batch")
ROOT = Path(__file__).resolve().parents[1]
BATCH_STAGING = ROOT / "data" / "_batch"


def backend_factory():
    """A fresh DirectBackend (the caller closes it). Raises DirectDbUnavailable without DATABASE_URL."""
    from app.ingest_batch import DirectBackend
    return DirectBackend.open()


def _with_backend(fn):
    """Open a backend, run fn(backend), close it whatever happens."""
    backend = backend_factory()
    try:
        return fn(backend)
    finally:
        backend.close()


def _flush_answer_cache(notes: dict) -> None:
    try:
        from app.ai import flush_cache
        flush_cache()
        notes["cache"] = "flushed"
    except Exception as e:  # noqa: BLE001
        notes["cache"] = str(e)[:80]


def _sync_catalog(notes: dict) -> None:
    """The marketplace catalog mirrors the current price book: re-sync after a commit AND after
    an undo that touched selling_prices, or activation would keep following the undone book."""
    try:
        from app.catalog import sync_from_price_book
        notes["catalog_synced"] = sync_from_price_book()
    except Exception as e:  # noqa: BLE001
        notes["catalog_synced"] = str(e)[:80]


def _after_commit(batch_id: int, result: dict, actor: str) -> dict:
    """Post-commit housekeeping (all best-effort, never fails the commit): answer cache, the
    catalog mirror of the price book, the freshness record, a notification and the event backbone.
    Honest about what the batch path does NOT run: scripts/refresh.py's verify_numbers (the commit
    asserted every total inside the transaction instead) and category_backfill (a categories
    export is not a batch target; it is listed under `ignored` in the preview)."""
    notes: dict = {"not_run": ["verify_numbers", "category_backfill"]}
    targets = list((result or {}).get("targets") or {})
    _flush_answer_cache(notes)
    if "selling_prices" in targets:
        _sync_catalog(notes)
    try:
        from app.database import get_client
        from app.reports import data_as_of
        d = data_as_of()
        # 'batch N ...' is the shape ingest_undo excludes when it looks for refreshes OUTSIDE the batch path
        get_client().table("ingest_runs").insert({
            "finished_at": datetime.now(timezone.utc).isoformat(), "status": "ok",
            "file": f"batch {batch_id} committed by {actor} (data as of {d})",
            "rows_loaded": sum(int((t.get("rows") or 0)) for t in (result.get("targets") or {}).values()),
        }).execute()
        notes["data_as_of"] = d
    except Exception as e:  # noqa: BLE001
        notes["ingest_runs"] = str(e)[:80]
    lines = [f"{t}: +{a.get('inserted', 0)} / ~{a.get('updated', 0)} / -{a.get('deleted', a.get('voided', 0))}"
             for t, v in (result.get("targets") or {}).items() for a in [v.get("actions") or {}]]
    try:
        from app.notify import notify
        notes["notified"] = notify(f"YQ - batch {batch_id} committed",
                                   f"Batch {batch_id} committed by {actor} in one transaction (every total re-asserted).\n"
                                   + "\n".join(lines) + "\nverify_numbers / category_backfill were not run on this path.")
    except Exception as e:  # noqa: BLE001
        notes["notified"] = str(e)[:80]
    try:
        from app import events
        events.emit("ingest", "ingest.completed", severity="info",
                    payload={"ok": True, "path": "batch", "batch_id": batch_id, "targets": targets,
                             "not_run": notes["not_run"],
                             "summary": f"batch {batch_id} committed (totals asserted in-transaction; verify_numbers/category_backfill not run)"},
                    dedupe=False)
    except Exception:  # noqa: BLE001
        pass
    return notes


def _after_undo(batch_id: int, result: dict, actor: str) -> dict:
    notes: dict = {}
    targets = list((result or {}).get("targets") or {})
    _flush_answer_cache(notes)
    if "selling_prices" in targets:
        _sync_catalog(notes)
    try:
        from app import events
        events.emit("ingest", "ingest.batch_undone", severity="warn",
                    payload={"batch_id": batch_id, "targets": targets, "by": actor,
                             "summary": f"batch {batch_id} undone: its rows are gone and the rows it replaced are back"},
                    dedupe=False)
    except Exception:  # noqa: BLE001
        pass
    return notes


def register(app, limiter) -> None:  # noqa: C901 - one registration function, a few small routes
    from fastapi import Depends, File, HTTPException, UploadFile
    from pydantic import BaseModel
    from starlette.concurrency import run_in_threadpool

    from app.auth import CurrentUser, require_admin
    from app.ingest_batch import CommitFailed, CommitRefused, DirectDbUnavailable, commit_batch, rpc_error_message, run_preview, undo_batch

    class AckRequest(BaseModel):
        acknowledged: list[str] = []

    class NoteRequest(BaseModel):
        reason: str | None = None

    def _staging_dir() -> Path:
        d = BATCH_STAGING / datetime.now().strftime("%Y%m%d-%H%M%S-") / uuid.uuid4().hex[:8]
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _preview_folder(folder: Path, actor: str) -> dict:
        return _with_backend(lambda be: run_preview(folder, be, actor=actor, persist=True))

    def _unavailable(e: Exception) -> dict:
        return {"ok": False, "error": f"Batch importer unavailable: {str(e)[:240]}"}

    @app.post("/ingest/preview")
    async def ingest_preview(files: list[UploadFile] = File(...), admin: CurrentUser = Depends(require_admin)) -> dict:
        """Upload Focus exports -> parsed, staged in ingest_stage, previewed. Nothing live changes
        until the batch is committed."""
        from app.uploads import MAX_INGEST_FILES, UploadTooLarge, content_matches, read_capped
        if len(files) > MAX_INGEST_FILES:
            return {"ok": False, "error": f"Too many files (max {MAX_INGEST_FILES}). Upload the daily Focus set."}
        staging = _staging_dir()
        saved: list[str] = []
        for f in files:
            suffix = (Path(f.filename or "upload.xlsx").suffix or ".xlsx").lower()
            if suffix not in (".xlsx", ".xls", ".csv", ".xml"):
                shutil.rmtree(staging, ignore_errors=True)
                return {"ok": False, "error": f"{f.filename}: unsupported type '{suffix}'. Use .xlsx or .xls."}
            try:
                contents = await read_capped(f)
            except UploadTooLarge as e:
                shutil.rmtree(staging, ignore_errors=True)
                return {"ok": False, "error": f"{f.filename}: too large ({e})."}
            if suffix != ".xml" and not content_matches(f.filename or "", contents):
                shutil.rmtree(staging, ignore_errors=True)
                return {"ok": False, "error": f"{f.filename}: content doesn't match a {suffix} file."}
            safe_name = re.sub(r"[^A-Za-z0-9._ ()-]", "_", Path(f.filename or f"upload{suffix}").name)
            (staging / safe_name).write_bytes(contents)
            saved.append(safe_name)
        try:
            res = await run_in_threadpool(_preview_folder, staging, admin.email)
        except DirectDbUnavailable as e:
            return _unavailable(e) | {"files": saved}
        except Exception as e:  # noqa: BLE001
            log.exception("ingest preview failed")
            await run_in_threadpool(audit.log_event, admin.email, "ingest.preview_failed", detail={"files": saved, "error": str(e)[:300]})
            return {"ok": False, "files": saved, "error": f"Preview failed: {str(e)[:200]}"}
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        s = res["summary"]
        await run_in_threadpool(audit.log_event, admin.email, "ingest.preview", detail={
            "batch_id": res.get("batch_id"), "files": saved, "targets": {t: v.get("actions") for t, v in s.get("targets", {}).items()},
            "blocking": s.get("blocking_codes"), "hard_blocking": s.get("hard_blocking_codes"),
            "commit_available": s.get("commit_available")})
        return {"ok": True, "files": saved, **res}

    @app.get("/ingest/batches")
    async def ingest_batches(limit: int = 20, _admin: CurrentUser = Depends(require_admin)) -> dict:
        def _do(backend):
            if not backend.tables_ready():
                return {"batches": [], "migration_applied": False}
            rows = backend.list_batches(max(1, min(limit, 100)))
            slim = []
            for b in rows:
                s = b.get("summary") or {}
                slim.append({k: b.get(k) for k in ("id", "status", "files", "created_by", "created_at", "committed_by",
                                                    "committed_at", "undone_by", "undone_at")}
                            | {"targets": {t: {"file_rows": v.get("file_rows"), "actions": v.get("actions")}
                                           for t, v in (s.get("targets") or {}).items()},
                               "blocking_codes": s.get("blocking_codes") or [],
                               "hard_blocking_codes": s.get("hard_blocking_codes") or [],
                               "last_commit_error": s.get("last_commit_error"),
                               "commit": {t: v.get("actions") for t, v in (s.get("commit") or {}).items()}})
            return {"batches": slim, "migration_applied": True, "storage": backend.storage()}
        try:
            return await run_in_threadpool(_with_backend, _do)
        except DirectDbUnavailable as e:
            return {"batches": [], "migration_applied": False, "error": str(e)[:240]}

    @app.get("/ingest/batches/{batch_id}")
    async def ingest_batch_get(batch_id: int, _admin: CurrentUser = Depends(require_admin)) -> dict:
        try:
            b = await run_in_threadpool(_with_backend, lambda be: be.get_batch(batch_id) if be.tables_ready() else None)
        except DirectDbUnavailable as e:
            raise HTTPException(status_code=503, detail=str(e)[:240]) from e
        if not b:
            raise HTTPException(status_code=404, detail="Batch not found.")
        return b

    @app.post("/ingest/batches/{batch_id}/commit")
    async def ingest_batch_commit(batch_id: int, body: AckRequest, admin: CurrentUser = Depends(require_admin)) -> dict:
        def _do(backend):
            try:
                return commit_batch(batch_id, backend, admin.email, acknowledged=body.acknowledged)
            except CommitRefused:
                raise
            except Exception as e:  # noqa: BLE001
                msg = rpc_error_message(e)
                # Did the transaction commit before the failure reached us (a dropped connection
                # after COMMIT)? Ask on a FRESH session: the one that raised may be unusable.
                try:
                    b = _with_backend(lambda be: be.get_batch(batch_id))
                except Exception:  # noqa: BLE001
                    b = None
                if b and b.get("status") == "committed" and (b.get("summary") or {}).get("commit"):
                    return {"batch_id": batch_id, "status": "committed", "targets": b["summary"]["commit"],
                            "note": f"the commit completed but its response was lost ({msg[:120]})"}
                # record the error server-side, only while the batch is still 'previewed' (a merge,
                # never a read-modify-write that could erase a commit record written meanwhile)
                try:
                    _with_backend(lambda be: be.merge_summary(
                        batch_id, {"last_commit_error": msg[:400], "last_commit_error_at": datetime.now(timezone.utc).isoformat()},
                        only_status="previewed"))
                except Exception:  # noqa: BLE001
                    pass
                raise CommitFailed(msg) from e
        try:
            result = await run_in_threadpool(_with_backend, _do)
        except CommitRefused as e:
            return {"ok": False, "error": str(e)}
        except DirectDbUnavailable as e:
            return _unavailable(e)
        except CommitFailed as e:
            msg = str(e)
            await run_in_threadpool(audit.log_event, admin.email, "ingest.commit_failed", detail={"batch_id": batch_id, "error": msg[:400]})
            return {"ok": False, "error": f"Commit did not complete: {msg[:300]}. The batch history shows whether it went through; "
                                          "a failed commit rolls back completely."}
        notes = await run_in_threadpool(_after_commit, batch_id, result, admin.email)
        await run_in_threadpool(audit.log_event, admin.email, "ingest.commit", detail={
            "batch_id": batch_id, "acknowledged": body.acknowledged,
            "result": {t: v.get("actions") for t, v in (result.get("targets") or {}).items()}, "note": result.get("note")})
        return {"ok": True, "result": result, "after": notes}

    @app.post("/ingest/batches/{batch_id}/undo")
    async def ingest_batch_undo(batch_id: int, body: NoteRequest | None = None,
                                admin: CurrentUser = Depends(require_admin)) -> dict:
        try:
            result = await run_in_threadpool(_with_backend, lambda be: undo_batch(batch_id, be, admin.email))
        except DirectDbUnavailable as e:
            return _unavailable(e)
        except Exception as e:  # noqa: BLE001
            msg = rpc_error_message(e)
            await run_in_threadpool(audit.log_event, admin.email, "ingest.undo_failed", detail={"batch_id": batch_id, "error": msg[:400]})
            return {"ok": False, "error": f"Undo refused: {msg[:300]}"}
        notes = await run_in_threadpool(_after_undo, batch_id, result, admin.email)
        await run_in_threadpool(audit.log_event, admin.email, "ingest.undo", detail={
            "batch_id": batch_id, "reason": (body.reason if body else None), "result": result.get("targets")})
        return {"ok": True, "result": result, "after": notes}

    @app.post("/ingest/batches/{batch_id}/reject")
    async def ingest_batch_reject(batch_id: int, body: NoteRequest | None = None,
                                  admin: CurrentUser = Depends(require_admin)) -> dict:
        def _do(backend):
            b = backend.get_batch(batch_id) if backend.tables_ready() else None
            if not b:
                return None
            if b["status"] != "previewed":
                return {"ok": False, "error": f"Batch is {b['status']}; only a previewed batch can be rejected."}
            # one conditional statement: if a commit landed in between, nothing is overwritten
            if not backend.reject(batch_id, {"rejected_by": admin.email, "reject_reason": (body.reason if body else None)}):
                return {"ok": False, "conflict": True,
                        "error": "This batch changed a moment ago (it may have been committed). Refresh the list."}
            try:
                backend.prune()          # its staged rows have no further use
            except Exception:  # noqa: BLE001
                pass
            return {"ok": True}
        try:
            out = await run_in_threadpool(_with_backend, _do)
        except DirectDbUnavailable as e:
            return _unavailable(e)
        if out is None:
            raise HTTPException(status_code=404, detail="Batch not found.")
        if out.get("conflict"):
            raise HTTPException(status_code=409, detail=out["error"])
        if out.get("ok"):
            await run_in_threadpool(audit.log_event, admin.email, "ingest.reject", detail={"batch_id": batch_id, "reason": (body.reason if body else None)})
        return out
