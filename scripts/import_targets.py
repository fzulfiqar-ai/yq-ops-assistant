"""Load the owner's salesman target file into salesman_targets (21-Sep-2026).

    python -m scripts.import_targets "business_data/targets/Targets Oct 2026.xlsx"            # dry run
    python -m scripts.import_targets "business_data/targets/Targets Oct 2026.xlsx" --commit
    python -m scripts.import_targets targets.csv --period 2026-10 --commit   # file has no Month column

Expected columns (header names are matched loosely, any order, extra columns ignored):
    Salesman        the Focus salesman name exactly as it appears in Sales reports
                    (or the portal name from the salesmen roster -- both are accepted)
    Target (BHD)    monthly target, numeric
    Month           optional, YYYY-MM or a date; missing -> --period, or '' = standing target

Rules: dry run by default and prints every row with its resolution; refuses to write if any
salesman name is unknown (no silent typos); upsert on (salesman, period) so re-running the same
file is a no-op; never touches other periods. Requires scripts/targets_v2_migration.sql.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

import pandas as pd  # noqa: E402

NAME_HINTS = ("salesman", "sales man", "rep", "name")
TARGET_HINTS = ("target", "tier 1", "tier1", "bhd", "amount")
MONTH_HINTS = ("month", "period", "date")
# Optional tier columns (21-Sep-2026 scheme): thresholds for tiers 2/3, kickback % per tier, team.
OPTIONAL = {
    "tier2_bhd": ("tier 2", "tier2"), "tier3_bhd": ("tier 3", "tier3"),
    "kickback_t1": ("kickback 1", "kickback_t1", "kb1"), "kickback_t2": ("kickback 2", "kickback_t2", "kb2"),
    "kickback_t3": ("kickback 3", "kickback_t3", "kb3"), "team": ("team",),
}


def _pct(v):
    """'5%' / 5 / 0.05 -> 0.05"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip().rstrip("%")
    f = float(s)
    return round(f / 100, 4) if f > 1 else f


def _col(cols: list[str], hints: tuple[str, ...]) -> str | None:
    low = {c: c.lower() for c in cols}
    for h in hints:
        for c, cl in low.items():
            if h in cl:
                return c
    return None


def _period(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, (datetime, pd.Timestamp)):
        return v.strftime("%Y-%m")
    s = str(v).strip()
    m = re.match(r"^(\d{4})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    try:
        return pd.to_datetime(s).strftime("%Y-%m")
    except Exception:  # noqa: BLE001
        return s


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--period", default=None, help="YYYY-MM to use when the file has no Month column")
    ap.add_argument("--commit", action="store_true")
    a = ap.parse_args(argv)

    path = Path(a.file)
    if not path.is_absolute():
        path = ROOT / path
    df = pd.read_csv(path) if path.suffix.lower() == ".csv" else pd.read_excel(path)
    df.columns = [str(c).strip() for c in df.columns]
    ncol, tcol, mcol = _col(list(df.columns), NAME_HINTS), _col(list(df.columns), TARGET_HINTS), _col(list(df.columns), MONTH_HINTS)
    opt = {k: _col(list(df.columns), hints) for k, hints in OPTIONAL.items()}
    opt = {k: c for k, c in opt.items() if c and c not in (ncol, tcol, mcol)}
    print(f"file: {path.name}  columns: {list(df.columns)}\n  salesman={ncol!r} target={tcol!r} month={mcol!r} extra={opt}")
    if not ncol or not tcol:
        print("ERROR: could not find the Salesman and Target columns.")
        return 1

    from app.database import get_client
    c = get_client()
    roster = c.table("salesmen").select("name,focus_name,is_active").execute().data or []
    known = {}
    for r in roster:
        for k in (r.get("focus_name"), r.get("name")):
            if k:
                known[k.strip().lower()] = r.get("focus_name") or r.get("name")

    rows, bad = [], []
    for _, r in df.iterrows():
        name = str(r[ncol]).strip() if not pd.isna(r[ncol]) else ""
        if not name or name.lower().startswith("total"):
            continue
        try:
            target = float(r[tcol])
        except Exception:  # noqa: BLE001
            bad.append(f"{name}: target {r[tcol]!r} is not a number")
            continue
        resolved = known.get(name.lower())
        if not resolved:
            bad.append(f"{name}: not in the salesmen roster (add them in Salesmen first)")
            continue
        period = _period(r[mcol]) if mcol else (a.period or "")
        row = {"salesman": resolved, "period": period, "target_bhd": round(target, 3),
               "updated_by": f"import {path.name}", "updated_at": datetime.now(timezone.utc).isoformat()}
        for k, col in opt.items():
            v = r[col]
            if v is None or (isinstance(v, float) and pd.isna(v)):
                continue
            row[k] = str(v).strip().lower() if k == "team" else (_pct(v) if k.startswith("kickback") else float(v))
        rows.append(row)
        tiers = f"  tiers {row.get('tier2_bhd', '-')}/{row.get('tier3_bhd', '-')}" if opt else ""
        print(f"  {resolved:24} {period or '(standing)':10} BHD {target:,.0f}{tiers}  {row.get('team', '')}")

    if bad:
        print("\nREFUSED -- fix these rows first:")
        for b in bad:
            print("  -", b)
        return 2
    if not rows:
        print("nothing to load")
        return 1
    if not a.commit:
        print(f"\nDry run: {len(rows)} rows would be upserted on (salesman, period). Add --commit to write.")
        return 0
    c.table("salesman_targets").upsert(rows, on_conflict="salesman,period").execute()
    print(f"\nLoaded {len(rows)} targets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
