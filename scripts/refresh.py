"""Verified refresh engine — turns a folder of fresh Focus exports into a verified, fresh
Supabase + a "what changed" briefing. The hands-off heart of data freshness (roadmap N0).

  python -m scripts.refresh ["business_data/Focus ERP Updated Reports"]

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

import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

DEFAULT_FOLDER = "business_data/Focus ERP Updated Reports"


def _run(mod: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", mod, *args], cwd=ROOT, capture_output=True, text=True)


# Table -> the Focus report name the team knows it by (for the "Loaded" chips on the Data page).
_TABLE_LABEL = {
    "order_lines": "Sales_day_book", "orders": "Summary_sales_register",
    "stock_balance": "Stock_balance_by_warehouse", "stock_movements": "Stock_ledger",
    "ar_ageing": "Customer_summary_ageing", "product_profitability": "Product_Profitability_Report",
    "selling_prices": "Price book", "ledger_entries": "Ledger",
    "products": "Item master", "customers": "Customer master",
    "ar_ageing_totals": "Receivables Focus total",
}


def _loaded_counts(stdout: str) -> dict:
    """Parse load_supabase's `upserted <table> <n>` lines into {report label: rows}."""
    out: dict = {}
    for m in re.finditer(r"upserted\s+(\w+)\s+(\d+)", stdout or ""):
        out[_TABLE_LABEL.get(m.group(1), m.group(1))] = int(m.group(2))
    return out


def _friendly(stage: str, tail: str) -> str:
    """Turn a subprocess failure into one sentence a non-engineer can act on. The raw tail is
    still returned separately as `detail`; it is no longer the headline the Data page shows."""
    t = tail or ""
    if "cannot affect row a second time" in t or "'21000'" in t:
        return ("One report lists the same product/price twice with an identical key, so the database "
                "refused the batch. The loader now folds such rows automatically -- upload again; if it "
                "repeats, send the file to the developer.")
    if stage == "ingest" and ("join" in t.lower() or "HARD FAIL" in t):
        return ("Sales_day_book and Summary_sales_register do not cover the same invoices (join below 80%). "
                "Export both for the same date range and upload them together.")
    if stage == "ingest":
        return "A report could not be parsed. Check it is an unmodified Focus export with the default file name."
    if "timed out" in t.lower() or "timeout" in t.lower():
        return "The database did not answer in time. Wait a minute and upload again."
    if "duplicate key" in t:
        return "A row already exists with a different key than expected. Send the file to the developer."
    return f"The {stage} step failed. See the technical detail below."


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
    if changes.get("aliases"):
        lines.append("Aliases: " + str(changes["aliases"]))
    if changes.get("error"):
        lines.append("(change-detect warn: " + str(changes["error"]) + ")")
    return head, "\n".join(lines)


def _finish(ok: bool, src_path: str, error: str | None, changes: dict,
            verify: dict | None, send: bool, detail: str | None = None,
            loaded: dict | None = None) -> dict:
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
            "errors": None if ok else ((error or "") + (f" | {detail}" if detail else ""))[:500],
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
    return {"ok": ok, "data_as_of": data_date, "error": error, "detail": detail,
            "loaded": loaded or {}, "changes": changes, "verify": verify, "notified": sent}


def refresh(folder: str | None = None, send: bool = True) -> dict:
    """Run the full verified refresh from `folder` (default: the Updated-Reports set)."""
    src = folder or DEFAULT_FOLDER
    src_path = src if Path(src).is_absolute() else str(ROOT / src)
    if not Path(src_path).exists():
        return _finish(False, src_path, f"source folder not found: {src_path}", {}, None, send)

    # 0 - a clean slate. ingest never deletes stale data/clean/*.csv, so a Sales-only upload used to
    #     re-load the last selling_prices.csv left on this host -- and, once Focus books became
    #     snapshots (R1), that leftover could have voided a newer book's rows. Clear it first.
    shutil.rmtree(ROOT / "data" / "clean", ignore_errors=True)

    # 1 - ingest (honours the >=80% join hard-gate: non-zero exit => abort, do NOT load)
    r1 = _run("scripts.ingest", src)
    if r1.returncode != 0:
        tail = (r1.stdout or r1.stderr or "")[-600:]
        return _finish(False, src_path, _friendly("ingest", tail), {}, None, send, detail=tail)

    # 2 - load. --staged tells the loader which files THIS run parsed: only a Focus price book in
    #     that folder may void the older rows of its book (scripts/load_supabase.py).
    r2 = _run("scripts.load_supabase", "--staged", src_path)
    loaded = _loaded_counts(r2.stdout)
    if r2.returncode != 0:
        tail = (r2.stderr or r2.stdout or "")[-600:]
        return _finish(False, src_path, _friendly("load", tail), {}, None, send,
                       detail=tail, loaded=loaded)

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

    # 3c - auto-grow the catalog from the freshly loaded price book (new SKUs appear in
    #      the Catalog automatically; the owner's only manual step is the photo)
    try:
        from app.catalog import sync_from_price_book
        sync_from_price_book()
    except Exception:  # noqa: BLE001
        pass

    # 3d - exact-name aliases for new Focus item strings (insert-only, confidence 1.0) and the
    #      unmapped-share warning event (> 2% of the last 30 days' accessory lines without a code)
    alias_res: dict = {}
    try:
        from scripts.alias_autofill import autofill, summary_line
        alias_res = autofill()
        alias_res["line"] = summary_line(alias_res)
    except Exception as e:  # noqa: BLE001
        alias_res = {"error": str(e)[:160], "line": f"alias autofill failed: {str(e)[:120]}"}

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
    changes["aliases"] = alias_res.get("line")
    changes["unmapped_share"] = (alias_res.get("share") or {}).get("share")

    ok = bool(verify and verify.get("ok"))

    # 6 - emit ingest.completed so the event backbone reacts (demand_forecast → reorder
    #     drafts on the next dispatch; failures alert immediately).
    try:
        from app import events
        events.emit(
            "ingest", "ingest.completed",
            severity="info" if ok else "critical",
            payload={
                "ok": ok,
                "cost_change_count": int(changes.get("cost_changes") or 0),
                "price_change_count": int(changes.get("price_changes") or 0),
                "new_skus": (changes.get("new_skus") or [])[:10],
                "summary": (changes.get("catalog") or "") if ok else "ingest verify FAILED",
            },
            dedupe=False,
        )
    except Exception:  # noqa: BLE001
        pass

    v_err = None
    if not ok:
        bad = [r["metric"] for r in (verify or {}).get("rows", []) if not r.get("passed")]
        v_err = ("The data loaded, but these totals do not match the reports: " + "; ".join(bad)
                 if bad else "Verification could not run: " + str((verify or {}).get("error") or "unknown"))
    return _finish(ok, src_path, v_err, changes, verify, send, loaded=loaded)


def main() -> int:
    folder = sys.argv[1] if len(sys.argv) > 1 else None
    return 0 if refresh(folder)["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
