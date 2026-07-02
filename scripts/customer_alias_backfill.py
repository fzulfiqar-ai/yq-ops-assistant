"""Phase C.1 — seed customer_aliases and resolve customer_id on orders + ar_ageing.

Every distinct name seen in orders.customer_name / ar_ageing.account is matched to a
customers row: exact-normalized first, then difflib fuzzy (>=0.92). Unresolved / ambiguous
names are written to a review CSV rather than force-matched. Idempotent (upserts aliases,
re-resolves FKs). Needs DATABASE_URL.

Usage:
    python -m scripts.customer_alias_backfill
"""
from __future__ import annotations

import csv
import difflib
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

FUZZY_THRESHOLD = 0.92
REVIEW_CSV = ROOT / "data" / "customer_alias_review.csv"

_SUFFIXES = r"\b(w l l|wll|s p c|spc|est|co|company|trading|trdg|group|bahrain|w\.l\.l|est\.)\b"


def normalize(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())
    s = re.sub(_SUFFIXES, " ", s)
    return re.sub(r"\s+", " ", s).strip()


def main() -> int:
    url = os.getenv("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL is not set.")
        return 1
    import psycopg  # type: ignore

    conn = psycopg.connect(url)
    cur = conn.cursor()

    cur.execute("SELECT id, name FROM customers")
    customers = cur.fetchall()
    by_norm: dict[str, int] = {}
    norm_names: list[str] = []
    id_by_norm_name: dict[str, int] = {}
    for cid, name in customers:
        n = normalize(name)
        by_norm.setdefault(n, cid)
        norm_names.append(n)
        id_by_norm_name[n] = cid

    # All names to resolve, from both spaces.
    cur.execute("SELECT DISTINCT customer_name FROM orders WHERE customer_name IS NOT NULL")
    names = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT DISTINCT account FROM ar_ageing WHERE account IS NOT NULL")
    names |= {r[0] for r in cur.fetchall()}

    resolved: dict[str, tuple[int, str, float]] = {}   # raw_name -> (customer_id, source, conf)
    review: list[tuple[str, str]] = []
    for raw in sorted(names):
        n = normalize(raw)
        if raw.lower().startswith("cash customer"):
            continue
        if n in by_norm:
            src = "identity" if raw in id_by_norm_name else "normalized"
            resolved[raw] = (by_norm[n], src, 1.0)
            continue
        match = difflib.get_close_matches(n, norm_names, n=1, cutoff=FUZZY_THRESHOLD)
        if match:
            resolved[raw] = (id_by_norm_name[match[0]], "fuzzy",
                             round(difflib.SequenceMatcher(None, n, match[0]).ratio(), 3))
        else:
            review.append((raw, n))

    # Upsert aliases: map BOTH the raw lowercased name and its normalized form to the customer.
    rows = 0
    for raw, (cid, src, conf) in resolved.items():
        for alias in {raw.lower(), normalize(raw)}:
            if not alias:
                continue
            cur.execute(
                "INSERT INTO customer_aliases(alias, customer_id, source, confidence) "
                "VALUES (%s,%s,%s,%s) ON CONFLICT (alias) DO UPDATE SET "
                "customer_id=excluded.customer_id, source=excluded.source, confidence=excluded.confidence",
                (alias, cid, src, conf))
            rows += 1

    # Resolve the FK columns from the aliases just written.
    cur.execute("""
        UPDATE orders o SET customer_id = ca.customer_id
        FROM customer_aliases ca WHERE ca.alias = lower(o.customer_name) AND o.customer_id IS NULL
    """)
    orders_linked = cur.rowcount
    cur.execute("""
        UPDATE ar_ageing a SET customer_id = ca.customer_id
        FROM customer_aliases ca WHERE ca.alias = lower(a.account) AND a.customer_id IS NULL
    """)
    ar_linked = cur.rowcount
    conn.commit()

    if review:
        REVIEW_CSV.parent.mkdir(parents=True, exist_ok=True)
        with open(REVIEW_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["raw_name", "normalized"])
            w.writerows(review)

    total = len([n for n in names if not n.lower().startswith("cash customer")])
    print(f"names seen: {total}")
    print(f"resolved:   {len(resolved)}  ({len(resolved) / total * 100:.0f}%)")
    print(f"aliases written: {rows}")
    print(f"FK linked — orders: {orders_linked} rows · ar_ageing: {ar_linked} rows")
    print(f"needs review: {len(review)}" + (f"  → {REVIEW_CSV}" if review else ""))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
