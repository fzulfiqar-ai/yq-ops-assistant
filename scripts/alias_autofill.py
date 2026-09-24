"""Exact-name alias autofill after every load + the unmapped-share warning (R2, 24-Sep-2026).

    python -m scripts.alias_autofill              # insert exact-name aliases, emit the warning if due
    python -m scripts.alias_autofill --dry-run    # print what would be inserted, write nothing

Why: v_sales.sku_code comes from product_aliases (alias_text = the exact Focus item string). A new
Focus item string (a SKU added to the price book, a renamed line) sells from day one but counts as
nothing until someone runs the reconcile, so its catalog row looks like slow stock and the rep's
attributed numbers are short. scripts/refresh.py calls autofill() after each load:

  * only proposals the matcher grades 1.0 (the Focus string IS a product's exact item_name) are
    written, insert-only (an alias that exists is never re-pointed -- a curated alias always wins).
    Everything below 1.0 stays for a human (scripts/alias_backfill_preview.py). Codes the owner
    holds back (the near-duplicate SKUs still to be settled: 'X24 CC 1Mtr' / 'X24 CL 1Mtr' on
    24-Sep-2026, held back from alias_backfill_preview --commit with --exclude) are never written
    either: the list lives in app_settings 'alias_autofill_exclude' (comma-separated codes, seeded
    by economics_v2_migration.sql) with DEFAULT_HOLD_BACK as the fallback before the setting exists.
    No settings endpoint edits this key (app/settings.py takes the numeric costing keys only,
    app/shop_api.py the shop_* keys): the developer changes it with one statement,
      update app_settings set value = 'A,B' where key = 'alias_autofill_exclude';
  * then the unmapped share of the last 30 days of ACCESSORY lines (SIM never counts toward
    targets) is measured on v_sales, anchored to the data's own last sale date, and when it is
    above UNMAPPED_SHARE_MAX an agent event 'data.unmapped_share' (severity warn) is emitted through
    app/events.py, so the feed and the Telegram reaction carry it. Deduped for 24 h per data date
    and share, so a re-run does not spam.
Nothing here deletes or rewrites a row.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

UNMAPPED_SHARE_MAX = 0.02
WINDOW_DAYS = 30
EVENT_TYPE = "data.unmapped_share"
HOLD_BACK_SETTING = "alias_autofill_exclude"
# the owner's 24-Sep-2026 hold-back (X24 CC vs X24 CC 1Mtr, X24 CL vs X24 CL 1Mtr: near-duplicate
# SKUs in the price book, not merged) -- used until app_settings carries the list
DEFAULT_HOLD_BACK = ("X24 CC 1Mtr", "X24 CL 1Mtr")

SHARE_SQL = f"""
WITH mx AS (SELECT MAX(sale_date) AS d FROM v_sales)
SELECT COUNT(*)                                                       AS lines,
       COUNT(*) FILTER (WHERE sku_code IS NULL)                       AS unmapped,
       COALESCE(SUM(revenue_bhd), 0)                                  AS rev,
       COALESCE(SUM(revenue_bhd) FILTER (WHERE sku_code IS NULL), 0)  AS rev_unmapped,
       (SELECT d FROM mx)::text                                       AS data_to
FROM v_sales, mx
WHERE division = 'Accessories' AND sale_date > mx.d - {WINDOW_DAYS}
"""

TOP_UNMAPPED_SQL = f"""
WITH mx AS (SELECT MAX(sale_date) AS d FROM v_sales)
SELECT item_name, COUNT(*) AS lines, COALESCE(SUM(revenue_bhd), 0) AS rev
FROM v_sales, mx
WHERE division = 'Accessories' AND sale_date > mx.d - {WINDOW_DAYS} AND sku_code IS NULL
GROUP BY item_name ORDER BY lines DESC, rev DESC LIMIT 10
"""


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def pick_exact(proposals: list[dict], hold_back=()) -> list[dict]:
    """Pure: the proposals safe to write without a human -- confidence 1.0 (exact product name),
    a real products row to point at, a non-empty Focus string, and a code the owner has not held
    back (`hold_back`: codes, case-insensitive)."""
    held = {str(c).strip().upper() for c in hold_back if str(c).strip()}
    return [p for p in proposals
            if _f(p.get("confidence")) >= 1.0 and p.get("product_id") and p.get("proposed_code")
            and str(p.get("item_name") or "").strip() and str(p["proposed_code"]).strip().upper() not in held]


def parse_hold_back(value) -> set[str]:
    """Pure: 'X24 CC 1Mtr, X24 CL 1Mtr' -> {'X24 CC 1MTR', 'X24 CL 1MTR'}."""
    return {c.strip().upper() for c in str(value or "").split(",") if c.strip()}


def hold_back_codes(client) -> set[str]:
    """The owner's hold-back list from app_settings, DEFAULT_HOLD_BACK when the setting is not there."""
    try:
        rows = client.table("app_settings").select("value").eq("key", HOLD_BACK_SETTING).limit(1).execute().data or []
        if rows:
            return parse_hold_back(rows[0].get("value"))
    except Exception:  # noqa: BLE001
        pass
    return parse_hold_back(",".join(DEFAULT_HOLD_BACK))


def unmapped_share(row: dict | None) -> dict:
    """Pure: {lines, unmapped, share, rev, rev_unmapped, rev_share, data_to, over} from SHARE_SQL's row."""
    r = row or {}
    lines = int(_f(r.get("lines")))
    unmapped = int(_f(r.get("unmapped")))
    rev, rev_un = _f(r.get("rev")), _f(r.get("rev_unmapped"))
    share = (unmapped / lines) if lines else 0.0
    return {"lines": lines, "unmapped": unmapped, "share": round(share, 4),
            "rev": round(rev, 3), "rev_unmapped": round(rev_un, 3),
            "rev_share": round((rev_un / rev) if rev else 0.0, 4),
            "data_to": (str(r.get("data_to") or "")[:10] or None),
            "over": share > UNMAPPED_SHARE_MAX}


def autofill(exec_sql=None, client=None, emit=None, dry_run: bool = False) -> dict:
    """Run the exact-name autofill and the share check. All three collaborators default to the
    app's own (exec_sql = yq_readonly RPC, client = service role, emit = app.events.emit) so tests
    can pass fakes. Returns a summary dict; never raises past a failed read (the load must finish)."""
    from scripts.alias_backfill_preview import UNALIASED_SQL, propose

    if exec_sql is None:
        from app.db_read import exec_sql as _x
        exec_sql = _x
    out: dict = {"proposed": 0, "picked": 0, "inserted": 0, "codes": [], "held_back": [], "share": None,
                 "warned": False, "dry_run": dry_run}
    try:
        unaliased = exec_sql(UNALIASED_SQL) or []
        products = exec_sql("SELECT id, sku_code, item_name FROM products WHERE sku_code IS NOT NULL") or []
    except Exception as e:  # noqa: BLE001
        out["error"] = f"read failed: {str(e)[:140]}"
        return out
    proposals = propose(unaliased, products)
    if client is None:
        try:
            from app.database import get_client
            client = get_client()
        except Exception:  # noqa: BLE001
            client = None
    held = hold_back_codes(client) if client is not None else parse_hold_back(",".join(DEFAULT_HOLD_BACK))
    exact = pick_exact(proposals)
    picked = pick_exact(proposals, held)
    out["proposed"] = len(proposals)
    out["picked"] = len(picked)
    out["codes"] = sorted({str(p["proposed_code"]) for p in picked})
    out["held_back"] = sorted({str(p["proposed_code"]) for p in exact if str(p["proposed_code"]).upper() in held})
    if picked and not dry_run and client is not None:
        recs = [{"alias_text": p["item_name"], "product_id": p["product_id"]} for p in picked]
        try:
            before = _alias_count(client)
            for i in range(0, len(recs), 500):
                client.table("product_aliases").upsert(recs[i:i + 500], on_conflict="alias_text",
                                                       ignore_duplicates=True).execute()
            after = _alias_count(client)
            out["inserted"] = (after - before) if (before is not None and after is not None) else len(recs)
            try:
                client.table("audit_log").insert({
                    "user_email": "alias_autofill", "event": "product_aliases.autofill",
                    "question": "exact-name aliases after load",
                    "detail": {"proposed": len(recs), "inserted": out["inserted"], "codes": out["codes"],
                               "rev_90_bhd": round(sum(_f(p.get("rev_90")) for p in picked), 3)},
                }).execute()
            except Exception as e:  # noqa: BLE001
                print(f"  (audit_log not written: {str(e)[:100]})")
        except Exception as e:  # noqa: BLE001
            out["error"] = f"insert failed: {str(e)[:140]}"
    # the share is measured AFTER the insert, so what was just aliased no longer counts as unmapped
    try:
        share = unmapped_share((exec_sql(SHARE_SQL) or [{}])[0])
        out["share"] = share
        if share["over"]:
            top = exec_sql(TOP_UNMAPPED_SQL) or []
            share["top"] = [{"item_name": str(t.get("item_name")), "lines": int(_f(t.get("lines"))),
                             "rev": round(_f(t.get("rev")), 3)} for t in top]
            if not dry_run:
                if emit is None:
                    from app.events import emit as _emit
                    emit = _emit
                pct = round(share["share"] * 100, 1)
                out["warned"] = bool(emit(
                    "ingest", EVENT_TYPE, entity_type="v_sales", entity_key=share["data_to"], severity="warn",
                    payload={"summary": (f"{pct}% of the last {WINDOW_DAYS} days' accessory lines "
                                         f"({share['unmapped']} of {share['lines']}, BHD {share['rev_unmapped']:,.3f}) "
                                         f"carry no catalog code (limit {UNMAPPED_SHARE_MAX:.0%}). "
                                         f"Run scripts/alias_backfill_preview.py."),
                             "share": share["share"], "lines": share["lines"], "unmapped": share["unmapped"],
                             "rev_unmapped_bhd": share["rev_unmapped"], "data_to": share["data_to"],
                             "top": share["top"]},
                    fingerprint=f"{EVENT_TYPE}:{share['data_to']}:{pct}"))
    except Exception as e:  # noqa: BLE001
        out["share_error"] = str(e)[:140]
    return out


def _alias_count(client) -> int | None:
    try:
        r = client.table("product_aliases").select("id", count="exact").limit(1).execute()
        return int(r.count) if r.count is not None else None
    except Exception:  # noqa: BLE001
        return None


def summary_line(res: dict) -> str:
    """One line for the refresh briefing."""
    if res.get("error"):
        return f"alias autofill: {res['error']}"
    s = res.get("share") or {}
    line = f"aliases: {res.get('inserted', 0)} exact-name inserted"
    if res.get("codes"):
        line += " (" + ", ".join(res["codes"][:8]) + (" ..." if len(res["codes"]) > 8 else "") + ")"
    if res.get("held_back"):
        line += "; held back (owner decision): " + ", ".join(res["held_back"][:6])
    if s:
        line += (f"; unmapped accessory lines last {WINDOW_DAYS}d: {s['unmapped']}/{s['lines']} = "
                 f"{s['share'] * 100:.1f}%" + (" -- OVER 2%, warning event emitted" if res.get("warned")
                                               else (" -- over 2%" if s.get("over") else "")))
    return line


def main(argv: list[str]) -> int:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    dry = "--dry-run" in argv
    res = autofill(dry_run=dry)
    print(summary_line(res))
    if (res.get("share") or {}).get("top"):          # share is None when the read failed
        for t in res["share"]["top"]:
            print(f"   {t['lines']:4} lines  BHD {t['rev']:>9,.3f}  {t['item_name'][:70]}")
    if dry:
        print(f"(dry run: {res['picked']} exact-name aliases would be inserted; nothing written)")
    return 0 if not res.get("error") else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
