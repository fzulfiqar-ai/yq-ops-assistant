"""Production preservation gate: read-only before/after snapshot of every business record.

    python -m scripts.prod_gate snapshot --label before-r0      # writes business_data/gates/<ts>_<label>.json
    python -m scripts.prod_gate compare A.json B.json           # exit 1 on any unexplained change

The marketplace is live. Every release takes a snapshot before and after and compares them.
The rules (24-Sep-2026 plan, section 3):

  * No pre-existing order, order line, order event, merchant, salesman, target or audit row may
    disappear. Protected tables may only grow.
  * A pre-existing order keeps its order_no, created_at, token, customer, total_bhd, lines
    (item, qty, unit and line price) and source. Its salesman may change only if a new
    'assigned' event explains it; its status may only move forward (or to cancelled) with a new
    event; confirmation may fill qty_confirmed / line_status / total_confirmed_bhd.
  * New orders, events, shop_events and audit rows are expected business deltas and are listed.
  * FK orphans must stay at zero.

Every session is READ ONLY inside one REPEATABLE READ transaction (the Supabase pooler ignores
the startup read-only option, so it is set on the transaction itself). Output files carry no
phone numbers, emails or names -- only ids, codes and money -- and live under business_data/
(gitignored).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

GATES = ROOT / "business_data" / "gates"
PROTECTED = ["shop_orders", "shop_order_lines", "shop_order_events", "shop_customers", "salesmen",
             "salesman_targets", "audit_log", "user_roles", "shop_restock_requests"]
STATUS_RANK = {"new": 0, "confirmed": 1, "packed": 2, "out_for_delivery": 3, "delivered": 4}
ORDER_FIXED = ["order_no", "token_hash", "created_at", "customer_id", "total_bhd", "subtotal_bhd",
               "discount_bhd", "delivery_bhd", "source", "order_kind", "referral_code"]


def _dec(x):
    return None if x is None else str(Decimal(x).quantize(Decimal("0.001")))


def _conn():
    import psycopg
    c = psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=30)
    c.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    c.read_only = True
    return c


def snapshot() -> dict:
    snap: dict = {"taken_at": datetime.now(timezone.utc).isoformat(), "tables": {}, "orders": {},
                  "customers": {}, "salesmen": {}, "targets": {}, "fk_orphans": {}, "money": {}}
    with _conn() as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute("show transaction_read_only")
        assert cur.fetchone()[0] == "on", "gate session is not read-only"
        cur.execute("select table_name from information_schema.tables "
                    "where table_schema='public' and table_type='BASE TABLE' order by 1")
        for (t,) in cur.fetchall():
            cur.execute(f'select count(*) from public."{t}"')
            n = cur.fetchone()[0]
            cur.execute("select 1 from information_schema.columns where table_schema='public' "
                        "and table_name=%s and column_name='id' and data_type in ('bigint','integer','smallint')", (t,))
            mx = None
            if cur.fetchone():
                cur.execute(f'select max(id)::text from public."{t}"')
                mx = cur.fetchone()[0]
            snap["tables"][t] = {"rows": n, "max_id": mx}

        cur.execute("""select id, order_no, md5(coalesce(token,'')), created_at::text, status, customer_id,
                              salesman_id, issued_to_salesman_id, total_bhd, subtotal_bhd, discount_bhd,
                              delivery_bhd, total_confirmed_bhd, source, order_kind, referral_code,
                              payment_status, focus_invoice_no
                       from shop_orders order by id""")
        for r in cur.fetchall():
            snap["orders"][str(r[0])] = {
                "order_no": r[1], "token_hash": r[2], "created_at": r[3], "status": r[4],
                "customer_id": r[5], "salesman_id": r[6], "issued_to_salesman_id": r[7],
                "total_bhd": _dec(r[8]), "subtotal_bhd": _dec(r[9]), "discount_bhd": _dec(r[10]),
                "delivery_bhd": _dec(r[11]), "total_confirmed_bhd": _dec(r[12]), "source": r[13],
                "order_kind": r[14], "referral_code": r[15], "payment_status": r[16],
                "focus_invoice_no": r[17], "lines": {}, "events": [],
            }
        cur.execute("""select id, order_id, item_code, qty, unit_price_bhd, line_total_bhd, qty_confirmed, line_status
                       from shop_order_lines order by id""")
        for lid, oid, code, qty, up, lt, qc, ls in cur.fetchall():
            o = snap["orders"].get(str(oid))
            target = o["lines"] if o else snap.setdefault("orphan_lines", {})
            target[str(lid)] = {"item_code": code, "qty": qty, "unit_price_bhd": _dec(up),
                                "line_total_bhd": _dec(lt), "qty_confirmed": qc, "line_status": ls}
        cur.execute("select id, order_id, event, ts::text from shop_order_events order by id")
        for eid, oid, ev, ts in cur.fetchall():
            o = snap["orders"].get(str(oid))
            (o["events"] if o else snap.setdefault("orphan_events", [])).append([eid, ev, ts])

        cur.execute("""select id, md5(coalesce(phone,'')), salesman_id, sticky_salesman_id, orders_count,
                              total_bhd, created_at::text from shop_customers order by id""")
        for cid, ph, sid, sticky, oc, tot, ca in cur.fetchall():
            snap["customers"][str(cid)] = {"phone_hash": ph, "salesman_id": sid, "sticky_salesman_id": sticky,
                                           "orders_count": oc, "total_bhd": _dec(tot), "created_at": ca}
        cur.execute("select id, referral_code, focus_name, is_active, md5(coalesce(user_email,'')) from salesmen order by id")
        for sid, ref, fn, act, ue in cur.fetchall():
            snap["salesmen"][str(sid)] = {"referral_code": ref, "focus_name": fn, "is_active": act, "login_hash": ue}
        cur.execute("""select salesman, period, team, target_bhd, tier2_bhd, tier3_bhd, kickback_t1, kickback_t2,
                              kickback_t3 from salesman_targets order by salesman, period""")
        for s, p, tm, t1, t2, t3, k1, k2, k3 in cur.fetchall():
            snap["targets"][f"{s}|{p}"] = {"team": tm, "tiers": [_dec(t1), _dec(t2), _dec(t3)],
                                            "rates": [str(k1), str(k2), str(k3)]}

        # generic FK orphan check (single-column FKs)
        cur.execute("""select c.conname, cl.relname, a.attname, pl.relname, pa.attname
                       from pg_constraint c
                       join pg_class cl on cl.oid=c.conrelid join pg_class pl on pl.oid=c.confrelid
                       join pg_namespace n on n.oid=cl.relnamespace
                       join pg_attribute a on a.attrelid=c.conrelid and a.attnum=c.conkey[1]
                       join pg_attribute pa on pa.attrelid=c.confrelid and pa.attnum=c.confkey[1]
                       where c.contype='f' and n.nspname='public' and array_length(c.conkey,1)=1""")
        for name, child, col, parent, pcol in cur.fetchall():
            cur.execute(f'select count(*) from public."{child}" c where c."{col}" is not null and not exists '
                        f'(select 1 from public."{parent}" p where p."{pcol}" = c."{col}")')
            snap["fk_orphans"][name] = cur.fetchone()[0]

        # Focus-side money the reports show (to explain deltas after a data load)
        cur.execute("select count(*), coalesce(sum(gross_bhd),0), coalesce(sum(taxable_bhd),0), max(line_date)::text from order_lines")
        n, g, tx, mx = cur.fetchone()
        snap["money"]["order_lines"] = {"rows": n, "gross": _dec(g), "taxable": _dec(tx), "max_date": mx}
        cur.execute("select count(*), coalesce(sum(gross_bhd),0), max(order_date)::text from orders")
        n, g, mx = cur.fetchone()
        snap["money"]["orders"] = {"rows": n, "gross": _dec(g), "max_date": mx}
        cur.execute("""select as_of_date::text, count(*), coalesce(sum(net_qty),0), coalesce(sum(total_value_bhd),0)
                       from stock_balance where as_of_date = (select max(as_of_date) from stock_balance) group by 1""")
        r = cur.fetchone()
        snap["money"]["stock_balance_latest"] = r and {"as_of": r[0], "rows": r[1], "qty": str(r[2]), "value": _dec(r[3])}
        cur.execute("""select as_of_date::text, count(*), coalesce(sum(balance_bhd),0) from ar_ageing
                       where as_of_date = (select max(as_of_date) from ar_ageing) group by 1""")
        r = cur.fetchone()
        snap["money"]["ar_latest"] = r and {"as_of": r[0], "rows": r[1], "total": _dec(r[2])}
        cur.execute("select price_book, count(*) from selling_prices group by 1 order by 1")
        snap["money"]["selling_prices"] = {pb: n for pb, n in cur.fetchall()}
        cur.execute("select status, count(*), coalesce(sum(total_bhd),0) from shop_orders group by 1 order by 1")
        snap["money"]["shop_orders_by_status"] = {s: [n, _dec(v)] for s, n, v in cur.fetchall()}
    snap["digest"] = hashlib.sha256(json.dumps(snap["orders"], sort_keys=True).encode()).hexdigest()[:16]
    return snap


def compare(a: dict, b: dict) -> tuple[list[str], list[str]]:
    fails, notes = [], []
    for t in PROTECTED:
        ra, rb = a["tables"].get(t, {}).get("rows"), b["tables"].get(t, {}).get("rows")
        if ra is not None and (rb is None or rb < ra):
            fails.append(f"{t}: rows went {ra} -> {rb} (protected tables may only grow)")
    for t, meta in sorted(b["tables"].items()):
        ra = a["tables"].get(t, {}).get("rows")
        if ra is None:
            notes.append(f"new table {t}: {meta['rows']} rows")
        elif ra != meta["rows"]:
            notes.append(f"{t}: {ra} -> {meta['rows']} ({meta['rows'] - ra:+d})")
    for oid, oa in a["orders"].items():
        ob = b["orders"].get(oid)
        if ob is None:
            fails.append(f"order {oid} ({oa['order_no']}) DISAPPEARED")
            continue
        for k in ORDER_FIXED:
            if oa.get(k) != ob.get(k):
                fails.append(f"order {oa['order_no']}: {k} changed {oa.get(k)!r} -> {ob.get(k)!r}")
        new_events = [e for e in ob["events"] if e[0] not in {x[0] for x in oa["events"]}]
        lost = [e for e in oa["events"] if e[0] not in {x[0] for x in ob["events"]}]
        if lost:
            fails.append(f"order {oa['order_no']}: events disappeared {lost}")
        if oa["salesman_id"] != ob["salesman_id"] and not any(e[1] == "assigned" for e in new_events):
            fails.append(f"order {oa['order_no']}: salesman {oa['salesman_id']} -> {ob['salesman_id']} with no new 'assigned' event")
        if oa["status"] != ob["status"]:
            ok_move = ob["status"] == "cancelled" or STATUS_RANK.get(ob["status"], -1) > STATUS_RANK.get(oa["status"], 99)
            if not ok_move or not new_events:
                fails.append(f"order {oa['order_no']}: status {oa['status']} -> {ob['status']} "
                             f"({'backwards' if not ok_move else 'no event'})")
            else:
                notes.append(f"order {oa['order_no']}: status {oa['status']} -> {ob['status']} (with event)")
        for lid, la in oa["lines"].items():
            lb = ob["lines"].get(lid)
            if lb is None:
                fails.append(f"order {oa['order_no']}: line {lid} DISAPPEARED")
                continue
            for k in ("item_code", "qty", "unit_price_bhd", "line_total_bhd"):
                if la[k] != lb[k]:
                    fails.append(f"order {oa['order_no']} line {lid}: {k} {la[k]!r} -> {lb[k]!r}")
        extra = set(ob["lines"]) - set(oa["lines"])
        if extra:
            fails.append(f"order {oa['order_no']}: lines added to an existing order {sorted(extra)}")
    for oid in sorted(set(b["orders"]) - set(a["orders"]), key=int):
        o = b["orders"][oid]
        notes.append(f"new order {o['order_no']} ({o['status']}, BHD {o['total_bhd']})")
    for cid, ca in a["customers"].items():
        cb = b["customers"].get(cid)
        if cb is None:
            fails.append(f"customer {cid} DISAPPEARED")
        elif ca["phone_hash"] != cb["phone_hash"]:
            fails.append(f"customer {cid}: phone changed")
    for sid, sa in a["salesmen"].items():
        sb = b["salesmen"].get(sid)
        if sb is None:
            fails.append(f"salesman {sid} ({sa['referral_code']}) DISAPPEARED")
        elif sa["referral_code"] != sb["referral_code"]:
            fails.append(f"salesman {sid}: referral_code {sa['referral_code']} -> {sb['referral_code']} (links would break)")
    for k, ta in a["targets"].items():
        tb = b["targets"].get(k)
        if tb is None:
            fails.append(f"target {k} DISAPPEARED")
        elif ta != tb:
            fails.append(f"target {k} changed {ta} -> {tb}")
    for name, n in b["fk_orphans"].items():
        if n and n > a["fk_orphans"].get(name, 0):
            fails.append(f"FK {name}: orphans {a['fk_orphans'].get(name, 0)} -> {n}")
    for k in ("order_lines", "orders", "stock_balance_latest", "ar_latest", "selling_prices", "shop_orders_by_status"):
        if a["money"].get(k) != b["money"].get(k):
            notes.append(f"money {k}: {a['money'].get(k)} -> {b['money'].get(k)}")
    return fails, notes


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snapshot")
    s.add_argument("--label", default="gate")
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    a = ap.parse_args(argv)
    if a.cmd == "snapshot":
        snap = snapshot()
        GATES.mkdir(parents=True, exist_ok=True)
        path = GATES / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M%S')}_{a.label}.json"
        path.write_text(json.dumps(snap, indent=1, sort_keys=True), encoding="utf-8")
        orphans = sum(v for v in snap["fk_orphans"].values() if v)
        print(f"snapshot {path.name}: {len(snap['orders'])} orders "
              f"{snap['money']['shop_orders_by_status']}, {len(snap['customers'])} customers, "
              f"{len(snap['targets'])} targets, FK orphans {orphans}, digest {snap['digest']}")
        return 0 if orphans == 0 else 1
    A = json.loads(Path(a.a).read_text(encoding="utf-8"))
    B = json.loads(Path(a.b).read_text(encoding="utf-8"))
    fails, notes = compare(A, B)
    print(f"compare {Path(a.a).name} -> {Path(a.b).name}")
    for n in notes:
        print(f"  delta: {n}")
    for f in fails:
        print(f"  FAIL:  {f}")
    print(f"\n{'PASS' if not fails else 'FAIL'}: {len(fails)} failure(s), {len(notes)} explained delta(s)")
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
