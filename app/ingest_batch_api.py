"""Routes for the transactional batch importer (release R2a). Registered from app/main.py next to
the shop routes; the old POST /ingest path is untouched and stays the default until the replay in
tests/test_r2_importer.py has proven the batch path identical.

    POST /ingest/preview                 admin  upload Focus exports -> stage -> preview (no live write)
    GET  /ingest/batches                 admin  batch history
    GET  /ingest/batches/{id}            admin  one batch (summary, exceptions, status)
    POST /ingest/batches/{id}/commit     admin  {acknowledged: [codes]} -> ingest_commit RPC (one transaction)
    POST /ingest/batches/{id}/undo       admin  ingest_undo RPC
    POST /ingest/batches/{id}/reject     admin  a previewed batch the admin does not want (status only)

Parsing and staging run off the event loop (run_in_threadpool): the one-worker container must
keep answering /health or Render kills it mid-upload (the 14-Sep-2026 lesson).

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

log = logging.getLogger("yq.ingest_batch")
ROOT = Path(__file__).resolve().parents[1]
BATCH_STAGING = ROOT / "data" / "_batch"


def backend_factory():
    from app.ingest_batch import RestBackend
    return RestBackend()


def _after_commit(batch_id: int, result: dict, actor: str) -> dict:
    """Post-commit housekeeping (all best-effort, never fails the commit): answer cache, the
    catalog mirror of the price book, the freshness record and the event backbone."""
    notes: dict = {}
    targets = list((result or {}).get("targets") or {})
    try:
        from app.ai import flush_cache
        flush_cache()
        notes["cache"] = "flushed"
    except Exception as e:  # noqa: BLE001
        notes["cache"] = str(e)[:80]
    if "selling_prices" in targets:
        try:
            from app.catalog import sync_from_price_book
            notes["catalog_synced"] = sync_from_price_book()
        except Exception as e:  # noqa: BLE001
            notes["catalog_synced"] = str(e)[:80]
    try:
        from app.database import get_client
        from app.reports import data_as_of
        d = data_as_of()
        get_client().table("ingest_runs").insert({
            "finished_at": datetime.now(timezone.utc).isoformat(), "status": "ok",
            "file": f"batch {batch_id} committed by {actor} (data as of {d})",
            "rows_loaded": sum(int((t.get("rows") or 0)) for t in (result.get("targets") or {}).values()),
        }).execute()
        notes["data_as_of"] = d
    except Exception as e:  # noqa: BLE001
        notes["ingest_runs"] = str(e)[:80]
    try:
        from app import events
        events.emit("ingest", "ingest.completed", severity="info",
                    payload={"ok": True, "batch_id": batch_id, "targets": targets, "summary": f"batch {batch_id} committed"},
                    dedupe=False)
    except Exception:  # noqa: BLE001
        pass
    return notes


def register(app, limiter) -> None:  # noqa: C901 - one registration function, a few small routes
    from fastapi import Depends, File, HTTPException, UploadFile
    from pydantic import BaseModel
    from starlette.concurrency import run_in_threadpool

    from app.audit import log_event
    from app.auth import CurrentUser, require_admin
    from app.ingest_batch import CommitRefused, commit_batch, rpc_error_message, run_preview, undo_batch

    class AckRequest(BaseModel):
        acknowledged: list[str] = []

    class NoteRequest(BaseModel):
        reason: str | None = None

    def _staging_dir() -> Path:
        d = BATCH_STAGING / datetime.now().strftime("%Y%m%d-%H%M%S-") / uuid.uuid4().hex[:8]
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _preview_folder(folder: Path, actor: str) -> dict:
        backend = backend_factory()
        res = run_preview(folder, backend, actor=actor, persist=True)
        return res

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
                return {"ok": False, "error": f"{f.filename}: unsupported type '{suffix}'. Use .xlsx, .xls or .csv."}
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
        except Exception as e:  # noqa: BLE001
            log.exception("ingest preview failed")
            log_event(admin.email, "ingest.preview_failed", detail={"files": saved, "error": str(e)[:300]})
            return {"ok": False, "files": saved, "error": f"Preview failed: {str(e)[:200]}"}
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        s = res["summary"]
        log_event(admin.email, "ingest.preview", detail={
            "batch_id": res.get("batch_id"), "files": saved, "targets": {t: v.get("actions") for t, v in s.get("targets", {}).items()},
            "blocking": s.get("blocking_codes"), "commit_available": s.get("commit_available")})
        return {"ok": True, "files": saved, **res}

    @app.get("/ingest/batches")
    def ingest_batches(limit: int = 20, _admin: CurrentUser = Depends(require_admin)) -> dict:
        backend = backend_factory()
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
                           "commit": {t: v.get("actions") for t, v in (s.get("commit") or {}).items()}})
        return {"batches": slim, "migration_applied": True}

    @app.get("/ingest/batches/{batch_id}")
    def ingest_batch_get(batch_id: int, _admin: CurrentUser = Depends(require_admin)) -> dict:
        backend = backend_factory()
        b = backend.get_batch(batch_id) if backend.tables_ready() else None
        if not b:
            raise HTTPException(status_code=404, detail="Batch not found.")
        return b

    @app.post("/ingest/batches/{batch_id}/commit")
    async def ingest_batch_commit(batch_id: int, body: AckRequest, admin: CurrentUser = Depends(require_admin)) -> dict:
        backend = backend_factory()

        def _do():
            return commit_batch(batch_id, backend, admin.email, acknowledged=body.acknowledged)
        try:
            result = await run_in_threadpool(_do)
        except CommitRefused as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:  # noqa: BLE001
            msg = rpc_error_message(e)
            log_event(admin.email, "ingest.commit_failed", detail={"batch_id": batch_id, "error": msg[:400]})
            try:
                backend.update_batch(batch_id, summary=(backend.get_batch(batch_id) or {}).get("summary", {})
                                     | {"last_commit_error": msg[:400]})
            except Exception:  # noqa: BLE001
                pass
            return {"ok": False, "error": f"Commit rolled back: {msg[:300]}"}
        notes = await run_in_threadpool(_after_commit, batch_id, result, admin.email)
        log_event(admin.email, "ingest.commit", detail={"batch_id": batch_id, "acknowledged": body.acknowledged,
                                                        "result": {t: v.get("actions") for t, v in (result.get("targets") or {}).items()}})
        return {"ok": True, "result": result, "after": notes}

    @app.post("/ingest/batches/{batch_id}/undo")
    async def ingest_batch_undo(batch_id: int, body: NoteRequest | None = None,
                                admin: CurrentUser = Depends(require_admin)) -> dict:
        backend = backend_factory()
        try:
            result = await run_in_threadpool(undo_batch, batch_id, backend, admin.email)
        except Exception as e:  # noqa: BLE001
            msg = rpc_error_message(e)
            log_event(admin.email, "ingest.undo_failed", detail={"batch_id": batch_id, "error": msg[:400]})
            return {"ok": False, "error": f"Undo refused: {msg[:300]}"}
        try:
            from app.ai import flush_cache
            flush_cache()
        except Exception:  # noqa: BLE001
            pass
        log_event(admin.email, "ingest.undo", detail={"batch_id": batch_id, "reason": (body.reason if body else None),
                                                      "result": result.get("targets")})
        return {"ok": True, "result": result}

    @app.post("/ingest/batches/{batch_id}/reject")
    def ingest_batch_reject(batch_id: int, body: NoteRequest | None = None,
                            admin: CurrentUser = Depends(require_admin)) -> dict:
        backend = backend_factory()
        b = backend.get_batch(batch_id) if backend.tables_ready() else None
        if not b:
            raise HTTPException(status_code=404, detail="Batch not found.")
        if b["status"] != "previewed":
            return {"ok": False, "error": f"Batch is {b['status']}; only a previewed batch can be rejected."}
        backend.update_batch(batch_id, status="rejected",
                             summary=(b.get("summary") or {}) | {"rejected_by": admin.email, "reject_reason": (body.reason if body else None)})
        log_event(admin.email, "ingest.reject", detail={"batch_id": batch_id, "reason": (body.reason if body else None)})
        return {"ok": True}
