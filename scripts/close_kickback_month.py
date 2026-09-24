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
    """The rows to freeze. Since R3a the maths lives in app.statements.build_rows (the same
    function the admin's "Create draft" button calls), so the CLI and the portal freeze
    identical figures. Amounts come back as 3-dp strings (Decimal, never float)."""
    from app.statements import build_rows
    return build_rows(period, basis)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--period", required=True, help="YYYY-MM")
    ap.add_argument("--basis", default="net_ex_vat", choices=["net_ex_vat", "vat_incl_display"])
    ap.add_argument("--status", default="draft", choices=["snapshot", "draft"])
    ap.add_argument("--note", default=None)
    ap.add_argument("--by", default="close_kickback_month")
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args(argv)

    from decimal import Decimal
    rows = build(a.period, a.basis)
    total = sum((Decimal(str(r["kickback_bhd"])) for r in rows), Decimal("0.000"))
    print(f"{a.period} on {a.basis} ({a.status}) -- {len(rows)} reps with a target, total kickback {total:.3f}")
    for r in rows:
        if Decimal(str(r["sales_bhd"])) or Decimal(str(r["kickback_bhd"])):
            print(f"  {r['salesman'][:24]:24s} {Decimal(str(r['sales_bhd'])):10.3f}  T{r['tier_reached']}  "
                  f"{Decimal(str(r['kickback_bhd'])):8.3f}  data {r['data_through']}")
    if not a.commit:
        print("\nDry run. Nothing written. Add --commit to freeze these rows.")
        return 0

    # A draft shares its data date with nothing else in the payable chain (draft / approved / paid:
    # the one_per_data_date index of r3_statements_migration.sql); a snapshot only with another
    # snapshot. Checked here so the refusal is a sentence, not a 23505 traceback.
    same_as = ["draft", "approved", "paid"] if a.status == "draft" else ["snapshot"]
    import psycopg
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=30) as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute("set local lock_timeout = '2s'")
        for r in rows:
            cur.execute("""select id, status from salesman_kickback_statements where salesman=%s and period=%s and basis=%s
                           and status = any(%s) and data_through is not distinct from %s order by id limit 1""",
                        (r["salesman"], r["period"], r["basis"], same_as, r["data_through"]))
            hit = cur.fetchone()
            if hit:
                raise SystemExit(f"REFUSED: statement #{hit[0]} ({hit[1]}) for {r['salesman']} {r['period']} "
                                 f"{r['basis']} data_through {r['data_through']} already exists")
            cur.execute("""insert into salesman_kickback_statements
                           (salesman, salesman_id, period, basis, status, data_through, sales_bhd, returns_bhd,
                            tier_reached, rate, kickback_bhd, target_snapshot, note, created_by)
                           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (r["salesman"], r["salesman_id"], r["period"], r["basis"], a.status, r["data_through"],
                         r["sales_bhd"], r["returns_bhd"], r["tier_reached"], r["rate"], r["kickback_bhd"],
                         json.dumps(r["target_snapshot"], default=str), a.note, a.by))
        # money in the audit row is a 3-dp string (a Decimal is not JSON; a float is not money)
        cur.execute("insert into audit_log (ts, user_email, event, detail) values (now(), %s, %s, %s)",
                    (a.by, "kickback.statement",
                     json.dumps({"period": a.period, "basis": a.basis, "status": a.status, "reps": len(rows),
                                 "total_kickback_bhd": f"{total:.3f}", "note": a.note})))
    print(f"\nFrozen {len(rows)} rows ({a.status}, {a.basis}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
