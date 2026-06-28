"""Verified refresh engine — turns a folder of fresh Focus exports into a verified, fresh
Supabase + a "what changed" briefing. The hands-off heart of data freshness (roadmap N0).

  python -m scripts.refresh ["Focus ERP Updated Reports"]

Pipeline (abort early on a bad gate — never load drifted data silently):
  1. ingest(folder)      -> parse reports to data/clean CSVs. Honours ingest's >=80% voucher<->
                            invoice join HARD-GATE (non-zero exit => abort, do NOT load).
  2. load_supabase       -> snapshot-safe upserts (stock_balance/ar_ageing replace per as_of_date).
  3. flush_cache         -> drop the stale text-to-SQL answer cache.
  4. verify_numbers      -> post-load validation of DB totals vs the source reports (PASS/FAIL).
  5. catalog_watch+anomaly -> what changed / data-integrity scan.
  6. notify              -> "Data refreshed as of X - N changes - verify PASS/FAIL" (Telegram+email).
  7. ingest_runs         -> record the run (status + data_as_of) for the freshness banner.

Text is kept ASCII so it prints safely on the Windows console.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

DEFAULT_FOLDER = "Focus ERP Updated Reports"


def _run(mod: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", mod, *args], cwd=ROOT, capture_output=True, text=True)


def _briefing(ok: bool, data_date, changes: dict, verify: dict | None, error: str | None) -> tuple[str, str]:
    head = "Data refreshed" if ok else "Data refresh - needs attention"
    lines = [f"Data as of {data_date or 'unknown'}."]
    if error:
        lines.append(f"PROBLEM: {error}")
    if verify:
        vr = "PASS" if verify["ok"] else "FAIL"
        detail = "; ".join(f"{r['metric']} {r['diff_pct']:.2f}%" for r in verify.get("rows", []))
        lines.append(f"Verify: {vr} - {detail}")
    if changes.get("catalog"):
        lines.append("Changes: " + str(changes["catalog"]))
    if changes.get("new_skus"):
        lines.append("New SKUs: " + ", ".join(map(str, changes["new_skus"][:8])))
    if changes.get("anomaly"):
        lines.append("Integrity: " + str(changes["anomaly"]))
    if changes.get("error"):
        lines.append("(change-detect warn: " + str(changes["error"]) + ")")
    return head, "\n".join(lines)


def _finish(ok: bool, src_path: str, error: str | None, changes: dict,
            verify: dict | None, send: bool) -> dict:
    data_date = None
    try:
        from app.reports import data_as_of
        data_date = data_as_of()
    except Exception:  # noqa: BLE001
        pass
    head, body = _briefing(ok, data_date, changes or {}, verify, error)
    # record the run for the "Data as of" freshness banner (best-effort)
    try:
        from app.database import get_client
        get_client().table("ingest_runs").insert({
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "status": "ok" if ok else "error",
            "file": f"refresh {Path(src_path).name} (data as of {data_date})",
            "errors": None if ok else (error or "")[:500],
        }).execute()
    except Exception:  # noqa: BLE001
        pass
    sent: dict = {}
    if send:
        try:
            from app.notify import notify
            sent = notify(f"YQ - {head}", body)
        except Exception as e:  # noqa: BLE001
            sent = {"error": str(e)[:120]}
    out = f"\n=== {head} ===\n{body}\n(notify: {sent})"
    try:
        print(out)
    except UnicodeEncodeError:  # Windows cp1252 console can't render some report chars
        print(out.encode("ascii", "replace").decode())
    return {"ok": ok, "data_as_of": data_date, "error": error,
            "changes": changes, "verify": verify, "notified": sent}


def refresh(folder: str | None = None, send: bool = True) -> dict:
    """Run the full verified refresh from `folder` (default: the Updated-Reports set)."""
    src = folder or DEFAULT_FOLDER
    src_path = src if Path(src).is_absolute() else str(ROOT / src)
    if not Path(src_path).exists():
        return _finish(False, src_path, f"source folder not found: {src_path}", {}, None, send)

    # 1 - ingest (honours the >=80% join hard-gate: non-zero exit => abort, do NOT load)
    r1 = _run("scripts.ingest", src)
    if r1.returncode != 0:
        tail = (r1.stdout or r1.stderr or "")[-300:]
        return _finish(False, src_path,
                       f"ingest gate failed (join < 80% or parse error). {tail}", {}, None, send)

    # 2 - load
    r2 = _run("scripts.load_supabase")
    if r2.returncode != 0:
        tail = (r2.stderr or r2.stdout or "")[-300:]
        return _finish(False, src_path, f"load_supabase failed. {tail}", {}, None, send)

    # 3 - flush stale answer cache
    try:
        from app.ai import flush_cache
        flush_cache()
    except Exception:  # noqa: BLE001
        pass

    # 3b - refresh product categories if a Multi_level (item-group) report is in the folder
    #      (no-op when it isn't — categories change rarely, so this is an occasional upload)
    try:
        from scripts.category_backfill import backfill as _cat_backfill
        _cat_backfill(src_path)
    except Exception:  # noqa: BLE001
        pass

    # 4 - verify (post-load validation vs the source reports)
    verify: dict | None = None
    try:
        from scripts.verify_numbers import run_checks
        v_ok, v_rows = run_checks(Path(src_path))
        verify = {"ok": v_ok, "rows": v_rows}
    except Exception as e:  # noqa: BLE001
        verify = {"ok": False, "rows": [], "error": str(e)[:160]}

    # 5 - what changed + integrity
    changes: dict = {}
    try:
        from app.agents import run_agent
        cw = run_agent("catalog_watch", triggered_by="schedule")
        an = run_agent("anomaly", triggered_by="schedule")
        changes = {
            "catalog": cw.get("summary"),
            "new_skus": (cw.get("changes") or {}).get("new_items", []),
            "price_changes": cw.get("price_change_count", 0),
            "cost_changes": cw.get("cost_change_count", 0),
            "anomaly": an.get("summary"),
        }
    except Exception as e:  # noqa: BLE001
        changes = {"error": str(e)[:160]}

    ok = bool(verify and verify.get("ok"))
    return _finish(ok, src_path, None if ok else "verify FAILED - DB totals drifted from the reports",
                   changes, verify, send)


def main() -> int:
    folder = sys.argv[1] if len(sys.argv) > 1 else None
    return 0 if refresh(folder)["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
