"""Freeze reps' kickback figures for a month into salesman_kickback_statements (24-Sep-2026).

    python -m scripts.close_kickback_month --period 2026-09                          # dry run (prints only)
    python -m scripts.close_kickback_month --period 2026-09 --basis vat_incl_display \\
        --status snapshot --note "what reps saw until the ex-VAT switch" --commit
    python -m scripts.close_kickback_month --period 2026-09 --status draft --commit   # the month's statement

Uses exactly the same functions the rep's Today card uses (app.shop.rep_month_sales, rep_target,
tier_progress), so a statement can never disagree with the screen it froze. One transaction per run,
plus an audit_log row. Never updates or deletes an existing statement: a re-run with the same key is
refused, and a later statement for the same month is simply a new row (older ones can be marked
superseded by an admin).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")


def build(period: str, basis: str) -> list[dict]:
    from app import shop
    from app.database import get_client
    targets = get_client().table("salesman_targets").select("*").execute().data or []
    salesmen = get_client().table("salesmen").select("id,focus_name").execute().data or []
    sid = {(s.get("focus_name") or "").strip(): s["id"] for s in salesmen if s.get("focus_name")}
    rows = []
    for name in sorted({t["salesman"] for t in targets}):
        amt, data_date = shop.rep_month_sales(name, basis=basis, period=period)
        tgt = shop.rep_target(name, period)
        tp = shop.tier_progress(tgt, amt, data_date, basis=basis)
        if not tp:
            continue
        rows.append({
            "salesman": name, "salesman_id": sid.get(name), "period": period, "basis": basis,
            "data_through": (data_date or "")[:10] or None, "sales_bhd": tp["mtd_bhd"], "returns_bhd": None,
            "tier_reached": tp["tier_reached"], "rate": tp["kickback_pct"], "kickback_bhd": tp["kickback_bhd"],
            "target_snapshot": tgt,
        })
    return rows


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--period", required=True, help="YYYY-MM")
    ap.add_argument("--basis", default="net_ex_vat", choices=["net_ex_vat", "vat_incl_display"])
    ap.add_argument("--status", default="draft", choices=["snapshot", "draft"])
    ap.add_argument("--note", default=None)
    ap.add_argument("--by", default="close_kickback_month")
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args(argv)

    rows = build(a.period, a.basis)
    total = round(sum(r["kickback_bhd"] for r in rows), 3)
    print(f"{a.period} on {a.basis} ({a.status}) -- {len(rows)} reps with a target, total kickback {total:.3f}")
    for r in rows:
        if r["sales_bhd"] or r["kickback_bhd"]:
            print(f"  {r['salesman'][:24]:24s} {r['sales_bhd']:10.3f}  T{r['tier_reached']}  {r['kickback_bhd']:8.3f}  data {r['data_through']}")
    if not a.commit:
        print("\nDry run. Nothing written. Add --commit to freeze these rows.")
        return 0

    import psycopg
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=30) as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute("set local lock_timeout = '2s'")
        for r in rows:
            cur.execute("""select 1 from salesman_kickback_statements where salesman=%s and period=%s and basis=%s
                           and status=%s and data_through is not distinct from %s""",
                        (r["salesman"], r["period"], r["basis"], a.status, r["data_through"]))
            if cur.fetchone():
                raise SystemExit(f"REFUSED: a {a.status} statement for {r['salesman']} {r['period']} "
                                 f"{r['basis']} data_through {r['data_through']} already exists")
            cur.execute("""insert into salesman_kickback_statements
                           (salesman, salesman_id, period, basis, status, data_through, sales_bhd, returns_bhd,
                            tier_reached, rate, kickback_bhd, target_snapshot, note, created_by)
                           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (r["salesman"], r["salesman_id"], r["period"], r["basis"], a.status, r["data_through"],
                         r["sales_bhd"], r["returns_bhd"], r["tier_reached"], r["rate"], r["kickback_bhd"],
                         json.dumps(r["target_snapshot"], default=str), a.note, a.by))
        cur.execute("insert into audit_log (ts, user_email, event, detail) values (now(), %s, %s, %s)",
                    (a.by, "kickback.statement",
                     json.dumps({"period": a.period, "basis": a.basis, "status": a.status, "reps": len(rows),
                                 "total_kickback_bhd": total, "note": a.note})))
    print(f"\nFrozen {len(rows)} rows ({a.status}, {a.basis}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
