"""Release 2b (R9) — the portal's Stock Moves pair the transfers by their contents with the module /yq-moves runs,
the per-store reconciliation from the stock ledger, and the Command Centre's team message (X1).

    python -m tests.test_r9_transfers

Same lightweight runner as the other suites (no pytest). Nothing here reaches a database or the network:
SUPABASE_URL / SUPABASE_KEY / DATABASE_URL are pinned to ci.invalid before any import, the read RPC is a stand-in
that answers the plan query, the pages of v_transfer_lines and v_salesman_stock_recon from synthetic rows, and the
service client is a stand-in query builder. Every name, number and amount is invented.

Covered (app/transfer_audit.py, GET /stock/flow, GET /stock/recon, GET /command/team-message):
  * pairs_from_rows is the pairing /yq-moves does on the same ledger (ledger._shape on stock_movements' columns with
    the loader's mask, then pairing.legs_from_rows and pair): the same legs and the same outcome for every voucher,
    with the portal's store rules or with config-style ones (aliases, stores that never count); the numbers as the
    read RPC sends them (floats, nulls) give the same legs as the database's Decimals; a narration's ID-shaped number
    is hashed in its masked form (unmasked, the pair is lost); a spelling that differs in case and spaces is one store;
  * GET /stock/flow: one row per issue line, newest first, with its status (received, late, elsewhere, qty_differs,
    in_grace, not_received, twins), the receipt it was paired with (both receipts of a transfer booked in under two,
    and what the two booked in together) and what the pairing rests on; the summary
    ({legs, qty, vouchers} per status, orphans and aside), the counts and the partners over EVERY issue of the
    window (the store that actually received); the 800 cap on the rows only; the window = `days` ending on the stock
    ledger's last date (any voucher type, not the last transfer's), both ends included; direction in; an empty
    ledger; a blank store; 503 when the ledger cannot be read; the feature gate;
  * GET /stock/recon: the view's rows, in its order, each with not_received_qty, qty_differs_vouchers and
    elsewhere_vouchers for its store (matched through the canon; 0 where the pairing found nothing; an issue sent to
    its own store that nothing booked back in is not received there past its deadline, as /yq-moves reads it); None,
    the rows still there, when the pairing cannot be worked out;
  * the read: pages of consecutive days of at most PAGE_LINES rows (a day never split, one larger day alone), each
    asked for by its date range only, every row read once; today's volume in five pages; the ledger's last date from
    the plan, or from its own query when no transfer is there; a page that holds other rows than the plan counted is
    answered once and never kept;
  * the cache: one read for many calls; invalidate() and app.datastamp.drop_process_caches() drop it; requests
    arriving together build once (the lock); a build that began before a drop is answered once and never kept; a
    failed build is told at once to the requests that waited for it and read again after the hold or a load; a
    request gives up after LOCK_WAIT_S; the look for a load comes first; importing the module (app/datastamp.py does)
    loads neither pandas nor the pairing;
  * GET /command/team-message: admin and management 200 with {run, data_date, text, created_at}, 204 with no body
    when none is stored, 403 for every other login, 401 without one, 503 without a word of the message or the error
    when the read fails; the read = the newest row of team_messages through the service client;
  * the schema text of v_salesman_stock_recon (app/ai.py) and the glossary line (scripts/seed_memory.py) say on hand
    is the stock ledger's balance and name the new columns.
"""
from __future__ import annotations

import ast
import io
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"

import warnings  # noqa: E402
warnings.filterwarnings("ignore")

from app import transfer_audit as ta  # noqa: E402

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


class _Patched:
    """Temporarily set module attributes; restores on exit (no pytest monkeypatch here)."""

    def __init__(self, *triples):
        self.triples = triples
        self.saved = []

    def __enter__(self):
        for mod, name, value in self.triples:
            self.saved.append((mod, name, getattr(mod, name)))
            setattr(mod, name, value)
        return self

    def __exit__(self, *exc):
        for mod, name, old in reversed(self.saved):
            setattr(mod, name, old)
        return False


def raises(kind, fn) -> Exception:
    try:
        fn()
    except kind as exc:
        return exc
    raise AssertionError(f"expected {kind.__name__}")


# ── the synthetic ledger ──────────────────────────────────────────────────────
# One week, Sunday 11 to Saturday 17 Oct 2026 (the stores work Saturday to Thursday; Friday is off). The last
# transfer is on the Thursday; the ledger runs to the Saturday (a sale), which is the date the pairing stands on.

MAIN, DAMAGE = ta.MAIN_STORE, ta.DAMAGE_STORE
VAN_A, VAN_B = "Van A - Acc WH", "Van B - Acc WH"
VAN_A_SPELT = "VAN A  -  ACC WH"           # the same van, as one row spells it
OUTLET = "Causeway"
CABLE, CHARGER, BANK, BUDS, CASE = ("ITEM-01 Cable One C 01", "ITEM-02 Charger Two CH2", "ITEM-03 Power Bank PB3",
                                    "ITEM-04 Ear Buds EB4", "ITEM-05 Case Five CS5")
ISSUE, RECEIVE, SALE = "Stock Issue Voucher", "Stock Receive Voucher", "Sales Invoice"
SUN, MON, TUE, WED, THU, FRI, SAT = (date(2026, 10, 11) + timedelta(days=n) for n in range(7))
LONG_AGO = SUN - timedelta(days=40)
EACH = Decimal("0.25")                       # the BHD every issued unit states
# Invented digit runs, put together here so this file holds none: two shaped like personal ID numbers that share
# their last three digits.
ID_A = "12345" + "6789"
ID_B = "98765" + "4789"


def sio(n: int, month: int = 10, series: str = "T") -> str:
    return f"SIO:{series}-26-{month:02d}-{n}"


def strv(n: int, month: int = 10, series: str = "T") -> str:
    return f"STRV:{series}-26-{month:02d}-{n}"


def issued(n, day, frm, to, lines, *, narration=None, month=10, series="T", blank=None) -> list[dict]:
    """The rows of one Stock Issue Voucher as stock_movements holds them (the receive side blank or 0)."""
    return [{"item_name": item, "move_date": day, "voucher": sio(n, month, series), "voucher_type": ISSUE,
             "warehouse_name": frm, "to_warehouse_name": to, "received_qty": blank, "issued_qty": Decimal(units),
             "received_value_bhd": blank, "issued_value_bhd": Decimal(units) * EACH, "narration": narration}
            for item, units in lines]


def booked(n, day, wh, lines, *, narration=None, month=10, series="T") -> list[dict]:
    """The rows of one Stock Receive Voucher (a receive leg states no value)."""
    return [{"item_name": item, "move_date": day, "voucher": strv(n, month, series), "voucher_type": RECEIVE,
             "warehouse_name": wh, "to_warehouse_name": None, "received_qty": Decimal(units), "issued_qty": Decimal(0),
             "received_value_bhd": Decimal(0), "issued_value_bhd": None, "narration": narration}
            for item, units in lines]


def ledger_rows() -> list[dict]:
    rows: list[dict] = []
    rows += issued(1, SUN, MAIN, VAN_A, [(CABLE, 10), (CHARGER, 5)])
    rows += booked(1, MON, VAN_A, [(CABLE, 10), (CHARGER, 5)])                  # received by its deadline (Monday)
    rows += issued(2, SUN, MAIN, VAN_A, [(BANK, 2)], blank=Decimal(0))
    rows += booked(2, WED, VAN_A_SPELT, [(BANK, 2)])                            # late; the van spelt another way
    rows += issued(3, MON, MAIN, VAN_A, [(BUDS, 4)], narration="sent to " + ID_A)
    rows += booked(50, WED, VAN_B, [(BUDS, 4)], narration="Sent  TO " + ID_B)   # elsewhere: only the narration ties them
    rows += issued(4, TUE, MAIN, VAN_B, [(CABLE, 6), (CHARGER, 6)])
    rows += booked(4, TUE, VAN_B, [(CABLE, 6), (CHARGER, 5)])                   # other quantities
    rows += issued(5, SUN, MAIN, VAN_B, [(CASE, 3)])                            # not received (due Monday)
    rows += issued(6, THU, MAIN, VAN_A, [(CASE, 1)])                            # in grace (due Saturday)
    rows += issued(7, SUN, MAIN, VAN_B, [(BANK, 1)])
    rows += issued(8, SUN, MAIN, VAN_B, [(BANK, 1)])
    rows += booked(7, MON, VAN_B, [(BANK, 1)])                                  # twins: two identical issues, one receipt
    rows += issued(9, SUN, MAIN, MAIN, [(CABLE, 1)])                            # set aside: sent to its own store
    rows += issued(10, WED, VAN_A, MAIN, [(CHARGER, 2)])
    rows += booked(10, WED, MAIN, [(CHARGER, 2)])                               # into the main store
    rows += booked(60, TUE, MAIN, [(CABLE, 2)])                                 # a receipt no issue was found for
    rows += issued(11, SUN, MAIN, DAMAGE, [(CASE, 1)])
    rows += booked(11, THU, DAMAGE, [(CASE, 1)])                                # the damage store has a week
    rows += issued(12, LONG_AGO, MAIN, VAN_A, [(CASE, 2)], month=9)             # long ago, never received
    rows += issued(13, WED, MAIN, VAN_A, [(CHARGER, 1)])                        # due Thursday: not received by Saturday
    rows.append({"item_name": CABLE, "move_date": SAT, "voucher": "SI:SI-T-26-10-1", "voucher_type": SALE,
                 "warehouse_name": VAN_A, "to_warehouse_name": None, "received_qty": Decimal(0),
                 "issued_qty": Decimal(1), "received_value_bhd": Decimal(0), "issued_value_bhd": Decimal(1),
                 "narration": "cash sale " + ID_A})                              # the ledger's last date
    return rows


def _json_number(x):
    """A number as the read RPC sends it: json_agg writes numerics as JSON numbers, json.loads reads floats."""
    return None if x is None else json.loads(json.dumps(float(x)))


def view_rows(db_rows) -> list[dict]:
    """v_transfer_lines as the read RPC returns it: the two transfer types only, the date as text, numbers as JSON."""
    return [{"voucher": r["voucher"], "vtype": r["voucher_type"], "date": r["move_date"].isoformat(),
             "wh": r["warehouse_name"], "to_wh": r["to_warehouse_name"], "item": r["item_name"],
             "qty_in": _json_number(r["received_qty"]), "qty_out": _json_number(r["issued_qty"]),
             "value_in": _json_number(r["received_value_bhd"]), "value_out": _json_number(r["issued_value_bhd"]),
             "narration": r["narration"]}
            for r in db_rows if r["voucher_type"] in (ISSUE, RECEIVE)]


def moves_way(db_rows, stores):
    """The pairing as /yq-moves does it from the database: ledger.from_db's shaping of stock_movements (the loader's
    mask), then the pairing module on the ledger's own last date."""
    from scripts import ingest
    from scripts.ops import ledger, pairing
    rows = ledger._shape(db_rows, ingest.mask_personal_ids)
    legs = pairing.legs_from_rows(rows, stores=stores, scope=None)
    return pairing.pair(legs, data_date=ledger.last_date(rows))


RECON_COLUMNS = ("salesman", "warehouse_type", "transferred_in_qty", "transferred_in_value_bhd", "transferred_out_qty",
                 "sold_qty", "on_hand_qty", "shortage_qty", "shortage_value_bhd", "unexplained_qty", "is_van",
                 "warehouse_name", "store_kind", "received_qty", "customer_returns_qty", "excess_qty", "pending_qty",
                 "items_held", "first_move", "last_move")


def recon_row(store: str, kind: str, spelt: str | None = None) -> dict:
    """One row of v_salesman_stock_recon (r9): the old eleven columns, then the new nine; figures invented."""
    van = kind in ("van", "outlet", "other")
    values = (store, "hub" if kind == "main" else ("van" if van else kind), 10, 2.5, 1, 4, 5, 0, 0, 0, van,
              spelt or store, kind, 9, 1, 0, 1, 3, SUN.isoformat(), SAT.isoformat())
    return dict(zip(RECON_COLUMNS, values))


RECON_ROWS = [recon_row(VAN_B, "van"), recon_row(VAN_A, "van", spelt="van a - acc wh"), recon_row(OUTLET, "outlet"),
              recon_row(MAIN, "main"), recon_row(DAMAGE, "damage")]


class FakeRPC:
    """The read RPC (app.db_read.exec_sql / exec_sql_params) on synthetic rows. It answers exactly the SQL the
    module sends and records every call (the SQL, its parameters, the rows it gave)."""

    def __init__(self, rows, data_date, recon=RECON_ROWS, drop_one=False, fail=None):
        self.rows = list(rows)
        self.data_date = data_date
        self.recon = [dict(r) for r in recon]
        self.drop_one = drop_one          # a page holds one row fewer than the plan counted (a load moved under it)
        self.fail = fail
        self.calls: list[tuple] = []
        self.lock = threading.Lock()

    def q(self, sql: str) -> list[dict]:
        with self.lock:
            self.calls.append((sql, None, None))
        if self.fail is not None:
            raise self.fail
        if sql == ta.PLAN_SQL:
            per = Counter(r["date"] for r in self.rows)
            return [{"day": d, "lines": n, "data_date": self.data_date} for d, n in sorted(per.items())]
        if sql == ta.DATA_DATE_SQL:
            return [{"data_date": self.data_date}]
        if sql.startswith("SELECT * FROM v_salesman_stock_recon"):
            return [dict(r) for r in self.recon]
        raise AssertionError(f"unexpected SQL: {sql[:90]}")

    def qp(self, sql: str, params: list) -> list[dict]:
        assert sql == ta.TRANSFER_LINES_SQL, sql[:90]
        assert len(params) == 2 and all(isinstance(p, str) for p in params), params
        lo, hi = params
        got = [dict(r) for r in self.rows if lo <= r["date"] <= hi]
        if self.drop_one and got:
            got = got[1:]
        with self.lock:
            self.calls.append((sql, list(params), len(got)))
        return got

    def pages(self) -> list[tuple]:
        return [(c[1][0], c[1][1], c[2]) for c in self.calls if c[0] == ta.TRANSFER_LINES_SQL]

    def plans(self) -> int:
        return sum(1 for c in self.calls if c[0] == ta.PLAN_SQL)


class _Fresh:
    """transfer_audit's cache as it was, afterwards; empty for the length of the block."""

    def __enter__(self):
        self.saved = dict(ta._state)
        ta._state.update(snap=None, failed=None)
        return self

    def __exit__(self, *exc):
        ta._state.clear()
        ta._state.update(self.saved)
        return False


def _rpc(rpc: FakeRPC):
    """The read RPC replaced by the stand-in (the module and the endpoints import it at call time)."""
    from app import db_read
    return _Patched((db_read, "exec_sql", rpc.q), (db_read, "exec_sql_params", rpc.qp))


def world_snapshot() -> ta.Snapshot:
    return ta.snapshot(view_rows(ledger_rows()), SAT.isoformat())


def _outcomes(result) -> dict:
    """{voucher: (what became of it, the other leg's number or None)} for every issue and every receipt."""
    out = {}
    for no, got in list(result.by_issue.items()) + list(result.by_receipt.items()):
        other = getattr(got, "receipt", None) if no.startswith("SIO") else getattr(got, "issue", None)
        out[no] = (getattr(got, "outcome", type(got).__name__), other.no if other is not None else None)
    return out


# ── the pairing is /yq-moves' own ─────────────────────────────────────────────

@test("pairs_from_rows = the pairing /yq-moves does on the same ledger: the same legs, pairs, opens, orphans and outcomes")
def _():
    db = ledger_rows()
    mine = ta.pairs_from_rows(view_rows(db), SAT.isoformat())
    theirs = moves_way(db, ta.PORTAL_STORES)
    for part in ("issues", "receipts", "aside", "out_of_scope", "first"):
        assert getattr(mine.legs, part) == getattr(theirs.legs, part), part
    assert mine.data_date == theirs.data_date == SAT, "the ledger's last date (a sale), not the last transfer's"
    assert (mine.pairs, mine.open, mine.orphans, mine.aside) == (theirs.pairs, theirs.open, theirs.orphans, theirs.aside)
    assert mine.by_issue == theirs.by_issue and mine.by_receipt == theirs.by_receipt
    assert mine.counts() == theirs.counts()
    assert mine.legs.last == THU and theirs.legs.last == SAT, "the view holds transfers only; the ledger the sale too"
    assert len(mine.legs.issues) == 12 and len(mine.legs.receipts) == 8 and len(mine.aside) == 1


@test("pairs_from_rows: config-style store rules (aliases, stores that never count) pair exactly as the portal's own")
def _():
    from scripts.ops import pairing
    db = ledger_rows()
    config_like = pairing.StoreRules(main=MAIN, damage=DAMAGE, not_counted=[DAMAGE, "Samples Warehouse"],
                                     aliases={"Accessories Warehouse 01-1": MAIN, "Van A - Acc WH 10-1": VAN_A})
    assert _outcomes(ta.pairs_from_rows(view_rows(db), SAT)) == _outcomes(moves_way(db, config_like))
    portal = ta.PORTAL_STORES
    assert (portal.main, portal.damage, portal.not_counted, dict(portal.aliases)) == (MAIN, DAMAGE, (), {})
    for spelt, name in (("Accessories Warehouse 01-1", MAIN), ("van a - acc wh 10-1", VAN_A), (VAN_A_SPELT, VAN_A)):
        assert portal.canon(spelt) == portal.canon(name), spelt          # the generic canon covers the spellings
    assert portal.kind(MAIN) == "main" and portal.kind(DAMAGE) == "damage" and portal.kind(OUTLET) == "outlet"


@test("pairs_from_rows: the RPC's floats and nulls give the database's Decimals; each issue's outcome as designed")
def _():
    from scripts.ops import pairing
    r = ta.pairs_from_rows(view_rows(ledger_rows()), SAT)
    want = {sio(1): (pairing.RECEIVED, strv(1)), sio(2): (pairing.LATE, strv(2)), sio(3): (pairing.ELSEWHERE, strv(50)),
            sio(4): (pairing.QTY_DIFFERS, strv(4)), sio(5): (pairing.NOT_RECEIVED, None),
            sio(6): (pairing.IN_GRACE, None), sio(7): (pairing.RECEIVED, strv(7)), sio(8): (pairing.TWINS, None),
            sio(10): (pairing.RECEIVED, strv(10)), sio(11): (pairing.RECEIVED, strv(11)),
            sio(12, month=9): (pairing.NOT_RECEIVED, None), sio(13): (pairing.NOT_RECEIVED, None)}
    got = _outcomes(r)
    assert {k: got[k] for k in want} == want, {k: got[k] for k in want}
    assert got[strv(60)] == ("Orphan", None)
    assert [a.voucher for a in r.aside] == [sio(9)] and r.aside[0].reason == pairing.OWN_STORE
    p4 = r.by_issue[sio(4)]
    assert p4.diffs == ((CABLE, Decimal(6), Decimal(6)), (CHARGER, Decimal(6), Decimal(5)))
    assert r.by_issue[sio(2)].receipt.wh == "van a - acc wh", "a spelling in other case and spaces is one store"
    assert r.by_issue[sio(7)].ambiguous and r.by_issue[sio(8)].twins == (sio(7), sio(8))
    assert r.by_issue[sio(1)].issue.value == Decimal("3.75") and r.by_issue[sio(1)].issue.unvalued == 0


@test("pairs_from_rows: the narration is masked before it is hashed (the two ID numbers tie the legs); unmasked, the pair is lost")
def _():
    from scripts.ops import pairing
    rows = view_rows(ledger_rows())
    masked = ta.pairs_from_rows(rows, SAT)
    p3 = masked.by_issue[sio(3)]
    assert p3.outcome == pairing.ELSEWHERE and p3.basis == ("narration",) and p3.same_narr
    raw = ta.pairs_from_rows(rows, SAT, mask=lambda text: text)
    assert raw.by_issue[sio(3)].outcome == pairing.NOT_RECEIVED and raw.by_receipt[strv(50)].__class__.__name__ == "Orphan"
    shaped = ta.shape(rows)
    assert all(r["narr"] is None or (len(r["narr"]) == pairing.NARR_LEN and r["narr"].isalpha()) for r in shaped)
    assert not any(ID_A in str(v) or ID_B in str(v) for r in shaped for v in r.values()), "no narration is kept"
    snap = ta.snapshot(rows, SAT)
    assert ID_A not in repr(snap.__dict__) and ID_B not in repr(snap.__dict__)


@test("pairs_from_rows: the data date decides grace: on the ledger's Saturday an issue due Thursday is not received; on the last transfer's day it is in grace")
def _():
    from scripts.ops import pairing
    rows = view_rows(ledger_rows())
    assert ta.pairs_from_rows(rows, SAT).by_issue[sio(13)].outcome == pairing.NOT_RECEIVED
    assert ta.pairs_from_rows(rows, THU).by_issue[sio(13)].outcome == pairing.IN_GRACE
    assert ta.pairs_from_rows(rows, None).data_date == THU, "no data date: the rows' own last"


# ── GET /stock/flow ───────────────────────────────────────────────────────────

ROW_KEYS = ["issued_on", "voucher_no", "item_name", "from_warehouse", "to_warehouse", "issued_qty", "issued_value_bhd",
            "received_qty", "received_by", "received_on", "receipt_no", "status", "basis"]
PAYLOAD_KEYS = {"warehouse", "direction", "days", "rows", "count", "summary", "partners", "as_of", "basis", "shown",
                "counts"}


@test("flow out: one row per issue line, newest first, each status, its receipt and basis; the store shown as the ledger writes it most")
def _():
    f = ta.flow(world_snapshot(), days=7)
    assert set(f) == PAYLOAD_KEYS
    assert (f["warehouse"], f["direction"], f["days"], f["as_of"], f["basis"]) == (MAIN, "out", 7, "2026-10-17", "contents")
    assert f["count"] == f["shown"] == len(f["rows"]) == 12
    assert all(list(r) == ROW_KEYS for r in f["rows"])
    order = [(r["voucher_no"], r["item_name"]) for r in f["rows"]]
    assert order == [(sio(6), CASE), (sio(13), CHARGER), (sio(4), CABLE), (sio(4), CHARGER), (sio(3), BUDS),
                     (sio(11), CASE), (sio(8), BANK), (sio(7), BANK), (sio(5), CASE), (sio(2), BANK),
                     (sio(1), CABLE), (sio(1), CHARGER)], order
    by = {(r["voucher_no"], r["item_name"]): r for r in f["rows"]}
    assert by[(sio(1), CABLE)] == {
        "issued_on": "2026-10-11", "voucher_no": sio(1), "item_name": CABLE, "from_warehouse": MAIN,
        "to_warehouse": VAN_A, "issued_qty": 10, "issued_value_bhd": 2.5, "received_qty": 10, "received_by": VAN_A,
        "received_on": "2026-10-12", "receipt_no": strv(1), "status": "received", "basis": "contents+number+date"}
    assert by[(sio(2), BANK)]["status"] == "late" and by[(sio(2), BANK)]["received_by"] == VAN_A, "the spelling shown most"
    assert by[(sio(2), BANK)]["basis"] == "contents+number"
    e = by[(sio(3), BUDS)]
    assert (e["status"], e["to_warehouse"], e["received_by"], e["receipt_no"], e["basis"]) == \
        ("elsewhere", VAN_A, VAN_B, strv(50), "contents+narration")
    q = [by[(sio(4), CABLE)], by[(sio(4), CHARGER)]]
    assert [(r["status"], r["issued_qty"], r["received_qty"], r["basis"]) for r in q] == \
        [("qty_differs", 6, 6, "overlap+number+date"), ("qty_differs", 6, 5, "overlap+number+date")]
    assert (by[(sio(7), BANK)]["status"], by[(sio(7), BANK)]["basis"]) == ("received", "contents+number+date+twins")
    t = by[(sio(8), BANK)]
    assert (t["status"], t["basis"], t["received_qty"], t["received_by"], t["received_on"], t["receipt_no"]) == \
        ("twins", "twins", 0, None, None, None)
    for key, status in (((sio(5), CASE), "not_received"), ((sio(13), CHARGER), "not_received"),
                        ((sio(6), CASE), "in_grace")):
        r = by[key]
        assert (r["status"], r["basis"], r["received_qty"], r["receipt_no"]) == (status, None, 0, None), key
    d = by[(sio(11), CASE)]
    assert (d["status"], d["to_warehouse"], d["received_on"]) == ("received", DAMAGE, "2026-10-15"), "the damage store's week"
    assert {r["status"] for r in f["rows"]} <= set(ta.STATUSES)


@test("flow out: the summary, the counts and the partners cover every issue of the window (the store that actually received)")
def _():
    f = ta.flow(world_snapshot(), days=7)
    s = f["summary"]
    assert list(s) == list(ta.SUMMARY_KEYS)
    assert s == {"received": {"legs": 4, "qty": 17, "vouchers": 3}, "late": {"legs": 1, "qty": 2, "vouchers": 1},
                 "elsewhere": {"legs": 1, "qty": 4, "vouchers": 1}, "qty_differs": {"legs": 2, "qty": 12, "vouchers": 1},
                 "in_grace": {"legs": 1, "qty": 1, "vouchers": 1}, "not_received": {"legs": 2, "qty": 4, "vouchers": 2},
                 "twins": {"legs": 1, "qty": 1, "vouchers": 1}, "orphans": {"legs": 0, "qty": 0, "vouchers": 0},
                 "aside": {"legs": 1, "qty": 1, "vouchers": 1}}, s
    per_status = Counter(r["status"] for r in f["rows"])
    assert all(per_status[k] == s[k]["legs"] for k in ta.STATUSES), per_status
    assert f["counts"] == {"received": 3, "late": 1, "elsewhere": 1, "qty_differs": 1, "in_grace": 1, "not_received": 2,
                           "twin_items": 1, "twin_slots": 1, "orphans": 0, "aside": 1, "issued_vouchers": 10,
                           "issued_units": 41}, f["counts"]
    assert sum(v["vouchers"] for k, v in s.items() if k in ta.STATUSES) == f["counts"]["issued_vouchers"]
    assert sum(v["legs"] for k, v in s.items() if k in ta.STATUSES) == f["count"]
    assert f["partners"] == [{"name": VAN_B, "qty": 21.0}, {"name": VAN_A, "qty": 19.0}, {"name": DAMAGE, "qty": 1.0}]
    # the same summary as pairing.summarise over the window, which /yq-moves prints
    from scripts.ops import pairing
    snap = world_snapshot()
    told = pairing.summarise(snap.result, first=SUN, last=SAT, store=MAIN, direction="out")
    assert {k: v for k, v in f["counts"].items() if k in pairing.COUNT_KEYS} == told["counts"]


@test("flow in: the issues sent to the store, partners by the sender; a receipt with no issue and a voucher set aside count here")
def _():
    f = ta.flow(world_snapshot(), days=7, direction="in")
    assert (f["direction"], f["count"], f["shown"]) == ("in", 1, 1)
    (row,) = f["rows"]
    assert (row["voucher_no"], row["from_warehouse"], row["to_warehouse"], row["received_by"], row["status"]) == \
        (sio(10), VAN_A, MAIN, MAIN, "received")
    assert f["summary"]["received"] == {"legs": 1, "qty": 2, "vouchers": 1}
    assert f["summary"]["orphans"] == {"legs": 1, "qty": 2, "vouchers": 1}
    assert f["summary"]["aside"] == {"legs": 1, "qty": 1, "vouchers": 1}
    assert (f["counts"]["orphans"], f["counts"]["aside"], f["counts"]["issued_vouchers"]) == (1, 1, 1)
    assert f["partners"] == [{"name": VAN_A, "qty": 2.0}]
    assert ta.flow(world_snapshot(), days=7, direction="sideways")["direction"] == "out", "anything else is out, as before"


@test("flow: an issue booked in under two receipts (pairing step S) is received on each of its lines, both receipt numbers named, what the two booked in together")
def _():
    from scripts.ops import pairing
    # Van B books SIO 20 in under two receipts on the Monday, numbered next to each other, the second holding exactly
    # what the first lacks (items no other open issue to Van B holds, so no other step can take either receipt)
    split = (issued(20, MON, MAIN, VAN_B, [(CABLE, 3), (BUDS, 2)])
             + booked(70, MON, VAN_B, [(CABLE, 3)]) + booked(71, MON, VAN_B, [(BUDS, 2)]))
    snap = ta.snapshot(view_rows(ledger_rows() + split), SAT.isoformat())
    got = snap.result.by_issue[sio(20)]
    assert isinstance(got, pairing.Pair) and got.step == "S" and got.outcome == "received", got
    assert {got.receipt.no, *(x.no for x in got.also)} == {strv(70), strv(71)} and len(got.also) == 1
    both = " + ".join([got.receipt.no] + [x.no for x in got.also])
    f = ta.flow(snap, days=7)
    rows = [(r["item_name"], r["issued_qty"], r["received_qty"], r["received_by"], r["received_on"], r["receipt_no"],
             r["status"]) for r in f["rows"] if r["voucher_no"] == sio(20)]
    assert rows == [(CABLE, 3, 3, VAN_B, "2026-10-12", both, "received"),
                    (BUDS, 2, 2, VAN_B, "2026-10-12", both, "received")], rows
    assert {r["basis"] for r in f["rows"] if r["voucher_no"] == sio(20)} == {ta._basis(got, pairing)}
    # one issue, counted once, as received; neither receipt counts as a receipt with no issue
    plain = ta.flow(world_snapshot(), days=7)
    assert f["counts"]["received"] == plain["counts"]["received"] + 1 and f["counts"]["orphans"] == 0
    assert f["counts"]["issued_vouchers"] == plain["counts"]["issued_vouchers"] + 1
    assert f["summary"]["received"] == {"legs": plain["summary"]["received"]["legs"] + 2,
                                        "qty": plain["summary"]["received"]["qty"] + 5,
                                        "vouchers": plain["summary"]["received"]["vouchers"] + 1}
    # the summary is pairing.summarise's, unchanged; the store's figures hold nothing to check for it
    told = pairing.summarise(snap.result, first=SUN, last=SAT, store=MAIN, direction="out")
    assert {k: v for k, v in f["counts"].items() if k in pairing.COUNT_KEYS} == told["counts"]
    assert ta.store_figures(snap) == ta.store_figures(world_snapshot())
    sent_in = ta.flow(snap, days=7, direction="in", warehouse=VAN_B)
    assert {r["receipt_no"] for r in sent_in["rows"] if r["voucher_no"] == sio(20)} == {both}
    assert sent_in["counts"]["orphans"] == 0


@test("flow: the window is `days` ending on the ledger's last date, both ends included; clamped to 1..365")
def _():
    snap = world_snapshot()
    one = ta.flow(snap, days=1)
    assert one["count"] == 0 and one["days"] == 1, "nothing was issued on the Saturday"
    three = ta.flow(snap, days=3)                       # Thursday to Saturday
    assert [r["voucher_no"] for r in three["rows"]] == [sio(6)]
    seven = ta.flow(snap, days=7)                       # Sunday to Saturday: the Sunday is in
    assert sio(1) in {r["voucher_no"] for r in seven["rows"]}
    six = ta.flow(snap, days=6)                         # Monday to Saturday: the Sunday is out
    assert sio(1) not in {r["voucher_no"] for r in six["rows"]} and sio(3) in {r["voucher_no"] for r in six["rows"]}
    ninety = ta.flow(snap, days=90)
    assert sio(12, month=9) in {r["voucher_no"] for r in ninety["rows"]}
    assert ninety["summary"]["not_received"]["vouchers"] == 3
    assert ta.flow(snap, days=0)["days"] == 1 and ta.flow(snap, days=4000)["days"] == ta.MAX_DAYS
    blank = ta.flow(snap, days=7, warehouse="  ")
    assert blank["warehouse"] == MAIN and blank["count"] == 12, "a blank store is the main store"
    spelt = ta.flow(snap, days=7, warehouse="accessories  WAREHOUSE")
    assert spelt["count"] == 12, "the store is compared through the canon"
    van = ta.flow(snap, days=7, warehouse=VAN_A)
    assert [r["voucher_no"] for r in van["rows"]] == [sio(10)]


@test("flow: the 800 cap is on the rows only; count, summary, counts and partners are over every issue line")
def _():
    rows: list[dict] = []
    for k in range(300):                                 # 300 issues of three lines each, all received the same day
        day = SAT - timedelta(days=k % 7)
        lines = [(CABLE, k + 1), (CHARGER, 1), (BANK, 2)]
        rows += issued(k, day, MAIN, VAN_A, lines, series="C")
        rows += booked(k, day, VAN_A, lines, series="C")
    snap = ta.snapshot(view_rows(rows), SAT)
    f = ta.flow(snap, days=7)
    assert (f["count"], f["shown"], len(f["rows"])) == (900, 800, 800)
    assert f["summary"]["received"] == {"legs": 900, "qty": sum(k + 4 for k in range(300)), "vouchers": 300}
    assert f["counts"]["issued_vouchers"] == 300 and f["counts"]["received"] == 300
    assert f["partners"] == [{"name": VAN_A, "qty": float(sum(k + 4 for k in range(300)))}]
    assert f["rows"][0]["issued_on"] == SAT.isoformat() and f["rows"][-1]["issued_on"] >= (SAT - timedelta(days=6)).isoformat()
    days_seen = [r["issued_on"] for r in f["rows"]]
    assert days_seen == sorted(days_seen, reverse=True), "newest first"
    small = ta.flow(snap, days=7, cap=5)
    assert (small["count"], small["shown"], len(small["rows"])) == (900, 5, 5) and small["rows"] == f["rows"][:5]


@test("flow: an empty ledger is an empty payload of the same shape")
def _():
    empty = ta.flow(ta.snapshot([], None), days=30)
    assert set(empty) == PAYLOAD_KEYS and empty["rows"] == [] and empty["count"] == empty["shown"] == 0
    assert empty["as_of"] is None and empty["partners"] == [] and set(empty["counts"]) == set(ta.COUNT_KEYS)
    assert all(v == {"legs": 0, "qty": 0, "vouchers": 0} for v in empty["summary"].values())
    loaded = ta.flow(ta.snapshot([], SAT.isoformat()), days=30)
    assert loaded["as_of"] == "2026-10-17" and loaded["count"] == 0, "a ledger with no transfer yet"


# ── GET /stock/recon ──────────────────────────────────────────────────────────

@test("recon: the view's rows in its order, each with what the pairing found for its store (through the canon), 0 where nothing")
def _():
    out = ta.recon([dict(r) for r in RECON_ROWS], world_snapshot())
    assert set(out) == {"vans", "count", "as_of"} and out["count"] == len(RECON_ROWS) and out["as_of"] == "2026-10-17"
    assert [r["salesman"] for r in out["vans"]] == [r["salesman"] for r in RECON_ROWS]
    for got, was in zip(out["vans"], RECON_ROWS):
        assert {k: v for k, v in got.items() if k not in ta.RECON_KEYS} == was, "the view's columns are untouched"
        assert list(got)[:len(RECON_COLUMNS)] == list(RECON_COLUMNS)
    figures = {r["salesman"]: tuple(r[k] for k in ta.RECON_KEYS) for r in out["vans"]}
    # Van A: the issue due Thursday (1) and the old one (2); its elsewhere voucher. Van B: the issue due Monday (3)
    # and the missing twin (1); its voucher of other quantities. The main store: its issue to itself that nothing
    # booked back in, past its deadline (1), as /yq-moves reads it. The issue in grace counts nowhere.
    assert figures == {VAN_A: (3, 0, 1), VAN_B: (4, 1, 0), OUTLET: (0, 0, 0), MAIN: (1, 0, 0), DAMAGE: (0, 0, 0)}, figures
    by_store = ta.store_figures(world_snapshot())
    assert by_store["van a - acc wh"] == {"not_received_qty": Decimal(3), "qty_differs_vouchers": 0, "elsewhere_vouchers": 1}


@test("store figures: an issue sent to its own store and booked back in by nothing is not received there past its deadline; its mirror, or grace, counts nothing")
def _():
    own = (issued(30, MON, VAN_A, VAN_A, [(BANK, 1)]) + booked(80, MON, VAN_A, [(BANK, 1)])     # booked back in there
           + issued(31, THU, VAN_B, VAN_B, [(BUDS, 1)]))                                        # due Saturday: in grace
    snap = ta.snapshot(view_rows(ledger_rows() + own), SAT.isoformat())
    aside = {a.voucher: a for a in snap.result.aside}
    assert (aside[sio(30)].partner, aside[strv(80)].partner, aside[sio(31)].partner) == (strv(80), sio(30), None)
    assert ta.store_figures(snap) == ta.store_figures(world_snapshot()), "the mirror pair and the issue in grace count nothing"
    assert ta.store_figures(world_snapshot())["accessories warehouse"] == {
        "not_received_qty": Decimal(1), "qty_differs_vouchers": 0, "elsewhere_vouchers": 0}, "SIO 9, due on the Monday"
    # on the Saturday after, past its deadline, the issue in grace is not received too
    later = ta.snapshot(view_rows(ledger_rows() + own), (SAT + timedelta(days=7)).isoformat())
    assert ta.store_figures(later)["van b - acc wh"]["not_received_qty"] == \
        ta.store_figures(ta.snapshot(view_rows(ledger_rows()), (SAT + timedelta(days=7)).isoformat()))[
            "van b - acc wh"]["not_received_qty"] + 1


@test("recon: without the pairing the rows still come, the three figures None")
def _():
    out = ta.recon([dict(r) for r in RECON_ROWS], None)
    assert out["count"] == len(RECON_ROWS) and out["as_of"] is None
    assert all(r[k] is None for r in out["vans"] for k in ta.RECON_KEYS)
    assert ta.recon([], world_snapshot()) == {"vans": [], "count": 0, "as_of": "2026-10-17"}


# ── the read ──────────────────────────────────────────────────────────────────

@test("plan_pages: consecutive days while a page holds at most the cap; a day is never split, a larger day is a page alone")
def _():
    d = [SUN + timedelta(days=n) for n in range(6)]
    assert ta.plan_pages([]) == []
    assert ta.plan_pages([(d[0], 3), (d[1], 4), (d[2], 3), (d[3], 9), (d[4], 2), (d[5], 1)], cap=10) == \
        [(d[0], d[2], 10), (d[3], d[3], 9), (d[4], d[5], 3)]
    assert ta.plan_pages([(d[0], 25), (d[1], 1)], cap=10) == [(d[0], d[0], 25), (d[1], d[1], 1)]
    # today's volume (about 25,600 lines over thirteen months) in pages well inside the 8-second limit
    year = [(SUN - timedelta(days=n), 64) for n in range(400, 0, -1)]
    pages = ta.plan_pages(year)
    assert len(pages) == 5 and all(n <= ta.PAGE_LINES for _a, _b, n in pages) and sum(n for *_x, n in pages) == 25600
    assert ta.PAGE_LINES <= 6000 and ta.PAGE_WORKERS <= 4


@test("read_lines: every page asks for its date range only, holds at most PAGE_LINES rows (a day never split), and every row is read once")
def _():
    rows: list[dict] = []
    for n in range(60):
        day = SUN - timedelta(days=59 - n)
        many = 80 if n == 30 else 7 + n % 13             # one day holds more than a page
        for k in range(many):
            rows += issued(n * 100 + k, day, MAIN, VAN_A, [(CABLE, k + 1)], month=day.month, series="P")
    view = view_rows(rows)
    rpc = FakeRPC(view, SAT.isoformat())
    with _Patched((ta, "PAGE_LINES", 50)):
        got, data_date, pages, settled = ta.read_lines(rpc.q, rpc.qp)
        planned = ta.plan_pages(sorted((date.fromisoformat(d), n) for d, n in Counter(r["date"] for r in view).items()), 50)
    assert data_date == SAT and settled and pages == len(planned) == len(rpc.pages())
    asked = sorted(rpc.pages())
    assert [(a, b) for a, b, _n in asked] == [(a.isoformat(), b.isoformat()) for a, b, _n in planned]
    for (lo, hi, n), (plo, phi, pn) in zip(asked, planned):
        assert n == pn and (n <= 50 or lo == hi), (lo, hi, n)
    for (_a, hi, _n), (lo, _b, _m) in zip(asked, asked[1:]):
        assert hi < lo, "the pages never overlap"
    assert sorted(json.dumps(r, sort_keys=True) for r in got) == sorted(json.dumps(r, sort_keys=True) for r in view)
    assert rpc.plans() == 1 and len(rpc.calls) == 1 + pages
    assert any(lo == hi and n == 80 for lo, hi, n in asked), "the larger day is a page of its own"


@test("read_lines: the ledger's last date from the plan, or from its own query when the ledger holds no transfer")
def _():
    rpc = FakeRPC(view_rows(ledger_rows()), SAT.isoformat())
    got, data_date, pages, settled = ta.read_lines(rpc.q, rpc.qp)
    assert data_date == SAT and pages == 1 and settled and len(got) == len(view_rows(ledger_rows()))
    empty = FakeRPC([], SAT.isoformat())
    assert ta.read_lines(empty.q, empty.qp) == ([], SAT, 0, True)
    assert [c[0] for c in empty.calls] == [ta.PLAN_SQL, ta.DATA_DATE_SQL]
    nothing = FakeRPC([], None)
    assert ta.read_lines(nothing.q, nothing.qp) == ([], None, 0, True)
    for sql in (ta.PLAN_SQL, ta.DATA_DATE_SQL, ta.TRANSFER_LINES_SQL):
        assert " AS t," not in sql and " AS t " not in sql, "no column is aliased t (the RPC wraps the query as t)"
    assert "v_stock_daily_movement" in ta.PLAN_SQL and "$1::date AND $2::date" in ta.TRANSFER_LINES_SQL


# ── the cache ─────────────────────────────────────────────────────────────────

@test("current(): one read for many calls; invalidate() and datastamp.drop_process_caches() drop it; the look for a load comes first")
def _():
    from app import datastamp
    rpc = FakeRPC(view_rows(ledger_rows()), SAT.isoformat())
    looks: list[int] = []
    with _Fresh(), _rpc(rpc), _Patched((datastamp, "sync", lambda: looks.append(1) or False)):
        first = ta.current()
        assert ta.current() is first and ta.current() is first and rpc.plans() == 1
        assert len(looks) == 3, "every call looks for a load before it answers from the cache"
        assert first.settled and first.lines == len(rpc.rows) and first.pages == 1 and first.data_date == SAT
        gen = ta._state["gen"]
        ta.invalidate()
        assert ta._state["snap"] is None and ta._state["gen"] == gen + 1
        second = ta.current()
        assert second is not first and rpc.plans() == 2
        datastamp.drop_process_caches()
        assert ta._state["snap"] is None and ta._state["gen"] == gen + 2, "a data load drops the pairing"
        assert ta.current() is not second and rpc.plans() == 3


@test("current(): requests arriving together build once (the lock); the second takes the first one's snapshot")
def _():
    entered, release = threading.Event(), threading.Event()
    reads: list[int] = []

    def slow_read():
        reads.append(1)
        entered.set()
        assert release.wait(20), "the test never released the read"
        return view_rows(ledger_rows()), SAT, 1, True
    got: list = []
    with _Fresh():
        a = threading.Thread(target=lambda: got.append(ta.current(read=slow_read)))
        a.start()
        assert entered.wait(20)
        b = threading.Thread(target=lambda: got.append(ta.current(read=slow_read)))
        b.start()
        time.sleep(0.2)
        assert len(reads) == 1 and b.is_alive(), "the second request waits for the build under way"
        release.set()
        a.join(20)
        b.join(20)
        assert len(got) == 2 and got[0] is got[1] and len(reads) == 1


@test("current(): a build that began before a drop, or whose pages held other rows than planned, is answered once and never kept")
def _():
    def read_while_loading():
        ta.invalidate()                                  # a load is noticed while the ledger is being read
        return view_rows(ledger_rows()), SAT, 1, True
    with _Fresh():
        snap = ta.current(read=read_while_loading)
        assert snap.result is not None and ta._state["snap"] is None
    rpc = FakeRPC(view_rows(ledger_rows()), SAT.isoformat(), drop_one=True)
    with _Fresh(), _rpc(rpc):
        snap = ta.current()
        assert snap.settled is False and ta._state["snap"] is None
        ta.current()
        assert rpc.plans() == 2, "the next request reads again"


@test("current(): a failed build is Unavailable, told at once to the requests that waited; read again after the hold or a load; a request gives up after LOCK_WAIT_S")
def _():
    calls: list[int] = []

    def broken():
        calls.append(1)
        raise RuntimeError("canceling statement due to statement timeout")
    with _Fresh():
        e = raises(ta.Unavailable, lambda: ta.current(read=broken))
        assert str(e) == ta.UNAVAILABLE and isinstance(e.__cause__, RuntimeError)
        raises(ta.Unavailable, lambda: ta.current(read=broken))
        assert len(calls) == 1, "within the hold nobody reads again"
        with _Patched((ta, "FAIL_HOLD_S", 0.0)):
            raises(ta.Unavailable, lambda: ta.current(read=broken))
        assert len(calls) == 2, "after the hold the next request reads again"
        ta.invalidate()
        assert ta.current(read=lambda: (view_rows(ledger_rows()), SAT, 1, True)).result is not None, "a load clears the hold"
    entered, release = threading.Event(), threading.Event()

    def parked():
        entered.set()
        release.wait(20)
        return [], SAT, 0, True
    with _Fresh(), _Patched((ta, "LOCK_WAIT_S", 0.1)):
        t = threading.Thread(target=lambda: ta.current(read=parked))
        t.start()
        assert entered.wait(20)
        raises(ta.Unavailable, lambda: ta.current(read=parked))
        release.set()
        t.join(20)


@test("importing the module loads neither pandas nor the pairing; PORTAL_STORES is built on first use (subprocess, no network)")
def _():
    code = ("import sys; import app.transfer_audit as t; t.invalidate(); "
            "assert 'PORTAL_STORES' not in vars(t); "
            "print(sorted(m for m in ('pandas', 'scripts.ops.pairing', 'scripts.ingest', 'app.ai', 'openai') if m in sys.modules)); "
            "s = t.PORTAL_STORES; assert s is t.PORTAL_STORES is t.portal_stores(); print('pairing' if 'scripts.ops.pairing' in sys.modules else '-')")
    env = dict(os.environ, SUPABASE_URL="https://ci.invalid", SUPABASE_KEY="ci-key",
               DATABASE_URL="postgresql://ci:ci@ci.invalid:5432/ci", PYTHONUTF8="1")
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env, capture_output=True, text=True,
                       encoding="utf-8", timeout=180)
    assert r.returncode == 0, (r.stdout + r.stderr)[-1500:]
    assert r.stdout.split("\n")[:2] == ["[]", "pairing"], r.stdout[-300:]


# ── the endpoints ─────────────────────────────────────────────────────────────

def _client_as(rows: dict):
    """TestClient whose bearer token text is the user's local part; user_roles rows from `rows`."""
    from fastapi.testclient import TestClient
    import app.main as m
    from app import auth, database
    database.invalidate_user_cache()
    p = _Patched((auth, "_decode_token", lambda tok: {"sub": "uid-" + tok, "email": tok + "@example.com"}),
                 (database, "_select_user_row", lambda email: rows.get(email)),
                 (m.limiter, "enabled", False))
    return TestClient(m.app), p


def _user(email, role, features=()):
    return {"email": email, "role": role, "features": list(features), "status": "active", "full_name": email.split("@")[0]}


ROLES = ("admin", "management", "member", "salesman", "storekeeper")
USERS = {f"{r}@example.com": _user(f"{r}@example.com", r) for r in ROLES}
USERS["mover@example.com"] = _user("mover@example.com", "member", ["Stock Movement"])


def _as(who: str) -> dict:
    return {"Authorization": f"Bearer {who}"}


@test("GET /stock/flow and /stock/recon end to end on the stand-in RPC: one read and one pairing serve both")
def _():
    rpc = FakeRPC(view_rows(ledger_rows()), SAT.isoformat())
    client, p = _client_as(USERS)
    with p, _Fresh(), _rpc(rpc):
        r = client.get("/stock/flow?days=7", headers=_as("admin"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body == json.loads(json.dumps(ta.flow(world_snapshot(), days=7)))
        assert body["summary"]["twins"] == {"legs": 1, "qty": 1, "vouchers": 1} and body["shown"] == 12
        r = client.get("/stock/flow?direction=in&days=7", headers=_as("mover"))
        assert r.status_code == 200 and r.json()["rows"][0]["voucher_no"] == sio(10), r.text
        r = client.get("/stock/recon", headers=_as("admin"))
        assert r.status_code == 200, r.text
        vans = {v["salesman"]: v for v in r.json()["vans"]}
        assert (vans[VAN_B]["not_received_qty"], vans[VAN_B]["qty_differs_vouchers"]) == (4, 1)
        assert r.json()["as_of"] == "2026-10-17"
        assert rpc.plans() == 1, "the pairing is worked out once for every request until the next load"
        for who in ("member", "salesman", "storekeeper"):
            assert client.get("/stock/flow", headers=_as(who)).status_code == 403, who
            assert client.get("/stock/recon", headers=_as(who)).status_code == 403, who
        assert client.get("/stock/flow").status_code == 401


@test("GET /stock/flow is a 503 with a plain sentence when the ledger cannot be read; /stock/recon still answers its rows")
def _():
    rpc = FakeRPC([], None, fail=RuntimeError("relation v_transfer_lines does not exist"))
    client, p = _client_as(USERS)
    recon_only = FakeRPC([], None)

    def q(sql):
        return recon_only.q(sql) if sql.startswith("SELECT * FROM v_salesman_stock_recon") else rpc.q(sql)
    from app import db_read
    with p, _Fresh(), _Patched((db_read, "exec_sql", q), (db_read, "exec_sql_params", rpc.qp)):
        r = client.get("/stock/flow?days=30", headers=_as("admin"))
        assert r.status_code == 503 and r.json()["detail"] == ta.UNAVAILABLE, r.text
        assert "v_transfer_lines" not in r.text
        r = client.get("/stock/recon", headers=_as("admin"))
        assert r.status_code == 200 and r.json()["count"] == len(RECON_ROWS) and r.json()["as_of"] is None
        assert all(v["not_received_qty"] is None for v in r.json()["vans"])


class _Table:
    """The service client's query builder for one table: every method chains and is recorded; execute() answers."""

    def __init__(self, data=None, error=None):
        self.data = data if data is not None else []
        self.error = error
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        def _m(*a, **k):
            self.calls.append((name, a, k))
            return self
        return _m

    def execute(self):
        if self.error is not None:
            raise self.error
        return self


class _Client:
    def __init__(self, table: _Table):
        self.team = table
        self.names: list[str] = []

    def table(self, name):
        self.names.append(name)
        return self.team if name == "team_messages" else _Table()


MESSAGE = {"run": "run-201026-1", "data_date": "2026-10-20", "text": "Team, yesterday: Van A 80% of target\n",
           "created_at": "2026-10-21T04:55:12.345678+00:00"}


@test("GET /command/team-message: admin and management get the newest message; 204 when none; every other login 403; 401 without one")
def _():
    from app import database
    client, p = _client_as(USERS)
    table = _Table([dict(MESSAGE, sha256="ab" * 32)])
    fake = _Client(table)
    with p, _Patched((database, "get_client", lambda: fake)):
        for who in ("admin", "management"):
            r = client.get("/command/team-message", headers=_as(who))
            assert r.status_code == 200 and r.json() == MESSAGE, (who, r.status_code, r.text)
        assert "team_messages" in fake.names
        assert ("select", ("run,data_date,text,created_at",), {}) in table.calls
        assert ("order", ("created_at",), {"desc": True}) in table.calls and ("limit", (1,), {}) in table.calls
        for who in ("member", "salesman", "storekeeper", "mover"):
            assert client.get("/command/team-message", headers=_as(who)).status_code == 403, who
        assert client.get("/command/team-message").status_code == 401
        table.data = []
        r = client.get("/command/team-message", headers=_as("admin"))
        assert r.status_code == 204 and r.content == b"", (r.status_code, r.content)
        # management is read-only: nothing but the read is open to it here
        assert client.post("/command/team-message", headers=_as("management")).status_code in (403, 405)


@test("GET /command/team-message: a failed read is a 503 that carries no word of the message or of the error")
def _():
    from app import database
    client, p = _client_as(USERS)
    fake = _Client(_Table(error=RuntimeError('{"message": "permission denied for table team_messages", "hint": "Van A 80%"}')))
    with p, _Patched((database, "get_client", lambda: fake)):
        r = client.get("/command/team-message", headers=_as("admin"))
        assert r.status_code == 503, r.text
        assert "permission" not in r.text and "Van A" not in r.text and "team_messages" not in r.text


@test("GET /command/team-message is a plain user dependency with the role check in the route, like the Command Centre's own")
def _():
    from fastapi.routing import APIRoute
    import app.main as m
    from app import auth
    routes = [r for r in m.app.routes if isinstance(r, APIRoute) and r.path == "/command/team-message"]
    assert len(routes) == 1 and routes[0].methods == {"GET"}
    assert [d.call for d in routes[0].dependant.dependencies] == [auth.get_current_user]
    assert not auth.read_only_refuses("GET", "/command/team-message"), "management may read it"


# ── the words ─────────────────────────────────────────────────────────────────

NEW_COLUMNS = ("warehouse_name", "store_kind", "received_qty", "customer_returns_qty", "excess_qty", "pending_qty",
               "items_held", "first_move", "last_move")


def _constant(path: Path, name: str):
    """A module-level constant read from the file's syntax tree (nothing is imported)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        targets = [node.target] if isinstance(node, ast.AnnAssign) else getattr(node, "targets", [])
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path.name}")


@test("the AI's schema text and the glossary: on hand is the stock ledger's balance, and the new columns are named")
def _():
    schema = _constant(ROOT / "app" / "ai.py", "_VIEW_SCHEMA")
    block = schema.split("v_salesman_stock_recon", 1)[1].split("\n\n", 1)[0]
    assert "STOCK LEDGER" in block and "on_hand_qty = the stock ledger's balance" in block
    for col in NEW_COLUMNS + ("salesman", "transferred_in_qty", "on_hand_qty", "shortage_value_bhd", "unexplained_qty"):
        assert col in block, col
    glossary = _constant(ROOT / "scripts" / "seed_memory.py", "GLOSSARY")
    (line,) = [g for g in glossary if "v_salesman_stock_recon" in g]
    assert "stock ledger's balance" in line and "not the stock snapshot" in line
    for col in NEW_COLUMNS:
        assert col in line, col


def main() -> int:
    passed = failed = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"PASS  {name}")
            passed += 1
        except Exception:  # noqa: BLE001
            print(f"FAIL  {name}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
