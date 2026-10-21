"""Ops engine: goods on order, the "order placed" log and its first-in-first-out clearing (scripts.ops.stock_orders).

    python -m tests.test_ops_stock_orders

Same lightweight runner as the other suites (no pytest). No database, no network, no .env: SUPABASE_URL,
SUPABASE_KEY and DATABASE_URL are pinned to ci.invalid BEFORE any import, and the temporary data roots (and the
world of the last fifo test) are the ones of tests/test_ops_stock.py (it also sets stdout and stderr to UTF-8 when
it is imported). Every log is written under a temporary data root. All names and numbers are invented.

Covered:
  * slug and ref_of; a quantity as a whole number of units;
  * promise_lines: the keys and values of a line (no cost, no price), and each refusal in one plain sentence;
  * token: the same order gives the same token in any line order; a changed file or line gives another;
  * plan_write: a new order, the same order again (nothing), a corrected quantity (one line), a code taken off
    the order (a cancel line), other orders untouched, a cancelled ref given again; cancel_lines;
  * append: JSON lines that stock.read_promises reads back (the newest per ref); a place outside the data folders,
    and a line that is not a promise line, refused before anything is made; a fresh lock refuses, a stale one is
    taken over once, the lock is held while writing and gone afterwards, after a failed write too;
  * fifo: one order received, a part arrival, two overlapping orders (oldest first), a receipt on the order's own
    day, units beyond the orders ignored, closed lines passed over, late and future, unmatched and unreadable
    lines, the model code, and the world of tests/test_ops_stock.py;
  * the module: no top-level import of stock, no client, no file opened but the log (through paths) and its lock.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import sys
import time
import traceback
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"

from tests.test_ops_stock import (  # noqa: E402  (first: it imports scripts.ops.db first, and sets stdout to UTF-8)
    D, L, World, data_root, model_of, raises)
from scripts.ops import config, paths, stock  # noqa: E402
from scripts.ops import stock_orders as so  # noqa: E402

TESTS: list[tuple[str, object]] = []

NOW = datetime(2026, 10, 20, 9, 30, 15, 250000)      # when the lines are logged (kept to the second)
STAMP = "2026-10-20T09:30:15"
LATER = datetime(2026, 10, 20, 11, 0, 0)
B = "ITEM-B Charger Beta"                            # an item: its stock name, and its code CB2
B_LEDGER = f"{B} CB2"                                # ... and its ledger name


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


def day(n: int, month: int = 10) -> date:
    return date(2026, month, n)


def refused(fn) -> str:
    """The refusal of fn: a StockInput (a ValueError the command prints as it is), one plain sentence."""
    exc = raises(ValueError, fn)
    assert isinstance(exc, stock.StockInput), type(exc)
    text = str(exc)
    assert text and "\n" not in text and text.endswith("."), text
    return text


def lines_of(supplier, on, pairs, source="typed", at=NOW, **kw) -> list[dict]:
    return so.promise_lines(supplier, on, pairs, source, at, **kw)


def item(name: str, code: str, *receipts) -> stock.Item:
    """A stock Item with its main-store supplier receipts [(day, units)], oldest first."""
    return stock.Item(ledger_name=f"{name} {code}", name=name, code=code,
                      receipts=[(on, f"MRN:T-{n}", Decimal(units), Decimal(0))
                                for n, (on, units) in enumerate(receipts, 1)])


def lead(supplier: str) -> int:
    """The lead time of a supplier, in days: VFAN 40, any other 50."""
    return {"VFAN": 40}.get(supplier, 50)


def line_of(result: dict, ref: str) -> dict:
    return next(x for x in result["lines"] if x["ref"] == ref)


# ── the lines ─────────────────────────────────────────────────────────────────

@test("slug and ref_of: letters and digits in lower case joined by '-'; a ref is supplier:date:CODE; whole units")
def _():
    assert so.slug("VFAN") == "vfan" and so.slug("  VFAN Co., Ltd. ") == "vfan-co-ltd"
    assert so.slug("Fábrica Uno") == "fabrica-uno"                     # an accent is dropped, never a word split
    assert so.slug("No.2 / Test-Supplier") == "no-2-test-supplier"
    assert so.slug("") == so.slug(None) == so.slug(" -/- ") == ""
    assert so.ref_of("VFAN", day(20), " cb2 ") == "vfan:2026-10-20:CB2"
    assert so.ref_of("VFAN Co.", "2026-10-01", "x05  uc") == "vfan-co:2026-10-01:X05 UC"     # inner spaces collapsed
    for given, n in ((30, 30), ("30", 30), (" 30 ", 30), ("30.0", 30), (30.0, 30), (Decimal("3E+1"), 30),
                     ("1,000", 1000), ("+7", 7)):
        assert so.whole(given) == Decimal(n) and str(so.whole(given)) == str(n), given
    for bad in (0, -3, 2.5, "2.5", "1,5", "1,00", "ten", "", None, True, False, float("nan"), float("inf"),
                Decimal("NaN"), "30 pcs", [30]):
        assert so.whole(bad) is None, bad


@test("promise_lines: one line per item, these keys and no other (no cost, no price), every value text")
def _():
    out = lines_of("  VFAN  ", day(1), [(" cb2 ", "100"), ("ca1", 30.0), ("X05  uc", Decimal("12"))],
                   source="Stock plan 2026-10-20.xlsx")

    def line(code, qty):
        return {"kind": "promise", "ref": f"vfan:2026-10-01:{code}", "supplier": "VFAN", "item_code": code,
                "qty": qty, "event_date": "2026-10-01", "logged_at": STAMP, "state": "open",
                "source": "Stock plan 2026-10-20.xlsx"}

    assert out == [line("CB2", "100"), line("CA1", "30"), line("X05 UC", "12")], out
    for x in out:
        assert sorted(x) == sorted(so.LINE_KEYS) and all(isinstance(v, str) for v in x.values()), x
        assert not any(re.search(r"cost|price|value|bhd|amount|rate", k) for k in x), x
    assert lines_of("VFAN", day(20), [("CB2", 1)])[0]["event_date"] == "2026-10-20"       # today is not the future
    assert lines_of("VFAN", day(21), [("CB2", 1)], today=day(21))[0]["event_date"] == "2026-10-21"   # today passed in
    assert lines_of("VFAN", "2026-10-02", [("CB2", 1)], at="2026-10-20T09:30:15.999")[0]["logged_at"] == STAMP


@test("promise_lines refuses in one plain sentence: no line, a blank or doubled code, a bad quantity, a future date")
def _():
    good = [("CB2", 10)]
    assert "no line to log" in refused(lambda: lines_of("VFAN", day(1), []))
    assert "no line to log" in refused(lambda: lines_of("VFAN", day(1), None))
    for blank in ("", "   ", None):
        assert "no item code" in refused(lambda b=blank: lines_of("VFAN", day(1), [("CA1", 1), (b, 5)]))
    for bad in (0, -5, 2.5, "2.5", "ten", "", None, True):
        text = refused(lambda q=bad: lines_of("VFAN", day(1), [("CB2", q)]))
        assert text.startswith("The quantity of CB2 (") and "not a whole number of units above nought" in text, text
    assert refused(lambda: lines_of("VFAN", day(1), [("CB2", 1), (" cb2 ", 2)])) == \
        "CB2 is on two lines: give it once, with its whole quantity."
    assert refused(lambda: lines_of("VFAN", day(21), good)) == \
        "The order date 2026-10-21 is after today (2026-10-20): an order is logged once it is placed."
    for odd in ([("CB2",)], [("CB2", 10, "0.5")], ["CB2 10"], [{"code": "CB2", "qty": 10}]):
        assert "two values" in refused(lambda o=odd: lines_of("VFAN", day(1), o))   # a third value (a price) is refused
    for nobody in ("", "  ", None):
        assert "names no supplier" in refused(lambda s=nobody: lines_of(s, day(1), good))
    assert "no letter or digit" in refused(lambda: lines_of("-/-", day(1), good))
    for blank in ("", None):
        assert "has no date" in refused(lambda x=blank: lines_of("VFAN", x, good))
    for not_a_day in ("next week", "2026-13-01", "01/10/2026"):
        assert "is not a date" in refused(lambda x=not_a_day: lines_of("VFAN", x, good))
    assert "names its source" in refused(lambda: lines_of("VFAN", day(1), good, source="  "))
    assert "not a date and time" in refused(lambda: lines_of("VFAN", day(1), good, at="tomorrow"))


@test("token: 12 letters a-p from the file and the lines; the same order in any line order is the same token")
def _():
    sha = "ab" * 32
    base = so.token(sha, "VFAN", day(1), [("CB2", 100), ("CA1", 30)])
    assert re.fullmatch(r"[a-p]{12}", base), base
    # the documented canonical text: JSON of the file, the supplier, the date and the lines [CODE, units] sorted
    canon = json.dumps({"date": "2026-10-01", "file": sha, "lines": [["CA1", "30"], ["CB2", "100"]],
                        "supplier": "VFAN"}, separators=(",", ":"))
    assert base == "".join("abcdefghijklmnop"[int(c, 16)] for c in hashlib.sha256(canon.encode()).hexdigest()[:12])
    assert so.token(sha, "VFAN", day(1), [("CB2", 100), ("CA1", 30)]) == base                  # stable
    assert so.token(sha, " VFAN ", "2026-10-01", [["CA1", "30"], (" cb2", 100.0)]) == base     # one canonical text
    others = {
        so.token("cd" * 32, "VFAN", day(1), [("CB2", 100), ("CA1", 30)]),         # another file
        so.token(None, "VFAN", day(1), [("CB2", 100), ("CA1", 30)]),              # typed: no file
        so.token(sha, "WEKOME", day(1), [("CB2", 100), ("CA1", 30)]),             # another supplier
        so.token(sha, "VFAN", day(2), [("CB2", 100), ("CA1", 30)]),               # another order date
        so.token(sha, "VFAN", day(1), [("CB2", 101), ("CA1", 30)]),               # a changed quantity
        so.token(sha, "VFAN", day(1), [("CB2", 100)]),                            # a line dropped
        so.token(sha, "VFAN", day(1), [("CB2", 100), ("CA1", 30), ("HG3", 5)]),   # a line added
        so.token(sha, "VFAN", day(1), [("CB2", 30), ("CA1", 100)]),               # the quantities swapped
    }
    assert base not in others and len(others) == 8, others


@test("plan_write: a new order is written; the same order again writes nothing; a corrected quantity is one line")
def _():
    first = lines_of("VFAN", day(1), [("CB2", 100), ("CA1", 30)], source="plan-a.xlsx")
    assert so.plan_write(None, first) == {"write": first, "unchanged": [], "cancel": []}     # no log yet
    assert so.plan_write([], first)["write"] == first
    existing = list(first)                       # as stock.read_promises gives them: the newest line of each ref
    again = so.promise_lines("VFAN", day(1), [("CA1", "30"), ("cb2", 100)], "plan-a.xlsx", LATER)
    assert so.plan_write(existing, again) == {"write": [], "unchanged": ["vfan:2026-10-01:CA1", "vfan:2026-10-01:CB2"],
                                              "cancel": []}
    fixed = so.promise_lines("VFAN", day(1), [("CB2", 80), ("CA1", 30)], "plan-b.xlsx", LATER)
    p = so.plan_write(existing, fixed)
    assert [x["ref"] for x in p["write"]] == ["vfan:2026-10-01:CB2"] and p["write"][0]["qty"] == "80"
    assert p["unchanged"] == ["vfan:2026-10-01:CA1"] and p["cancel"] == []
    # a line written by hand: a number for its quantity and no state; it is open, and the same quantity
    hand = {k: v for k, v in dict(first[0], qty=100).items() if k != "state"}
    assert so.plan_write([hand], first[:1])["unchanged"] == ["vfan:2026-10-01:CB2"]
    raises(ValueError, lambda: so.plan_write([], first + first))                 # a ref given twice


@test("plan_write: a code taken off the order is a cancel line; other orders untouched; a cancelled ref can return")
def _():
    vfan = lines_of("VFAN", day(1), [("CB2", 100), ("CA1", 30)], source="plan-a.xlsx")
    other_day = lines_of("VFAN", day(5), [("CA1", 7)])
    other_supplier = lines_of("WEKOME", day(1), [("HG3", 12)])
    closed = dict(lines_of("VFAN", day(1), [("SN4", 9)])[0], state="cancelled")
    existing = vfan + other_day + other_supplier + [closed]
    dropped = so.promise_lines("VFAN", day(1), [("CB2", 100)], "plan-b.xlsx", LATER)
    p = so.plan_write(existing, dropped)
    assert p["write"] == [] and p["unchanged"] == ["vfan:2026-10-01:CB2"]
    # CA1 is off the 1 Oct order of VFAN: cancelled, with its 30 units, at the new lines' time and source; SN4 is
    # closed already, and VFAN's 5 Oct order and WEKOME's 1 Oct order are other orders: none of them is touched
    assert p["cancel"] == [{"kind": "promise", "ref": "vfan:2026-10-01:CA1", "supplier": "VFAN", "item_code": "CA1",
                            "qty": "30", "event_date": "2026-10-01", "logged_at": "2026-10-20T11:00:00",
                            "state": "cancelled", "source": "plan-b.xlsx"}], p["cancel"]
    back = so.promise_lines("VFAN", day(1), [("CB2", 100), ("SN4", 9)], "plan-c.xlsx", LATER)
    p = so.plan_write(existing, back)
    assert [(x["ref"], x["state"]) for x in p["write"]] == [("vfan:2026-10-01:SN4", "open")]     # given again: open
    assert [x["ref"] for x in p["cancel"]] == ["vfan:2026-10-01:CA1"]


@test("cancel_lines: a cancelled line for every open ref of that supplier and order date, and nothing else")
def _():
    a = lines_of("VFAN", day(1), [("CB2", 100), ("CA1", 30)])
    b = lines_of("VFAN", day(5), [("CA1", 7)])
    c = lines_of("WEKOME", day(1), [("HG3", 12)])
    done = dict(lines_of("VFAN", day(1), [("SN4", 9)])[0], state="Received")
    existing = a + b + c + [done]
    at = datetime(2026, 10, 21, 8, 0, 0)
    out = so.cancel_lines(existing, " vfan ", "2026-10-01", at)
    assert [(x["ref"], x["qty"]) for x in out] == [("vfan:2026-10-01:CA1", "30"), ("vfan:2026-10-01:CB2", "100")]
    assert all(x["state"] == "cancelled" and x["logged_at"] == "2026-10-21T08:00:00" and x["source"] == "typed"
               and sorted(x) == sorted(so.LINE_KEYS) for x in out), out
    assert so.cancel_lines(existing + out, "VFAN", day(1), at) == []              # cancelled already: nothing open
    assert so.cancel_lines(existing, "VFAN", day(2), at) == [] and so.cancel_lines(None, "VFAN", day(1), at) == []
    assert so.cancel_lines(existing, "WEKOME", day(1), at, source="plan-a.xlsx")[0]["source"] == "plan-a.xlsx"
    assert "names no supplier" in refused(lambda: so.cancel_lines(existing, "", day(1), at))
    assert "is not a date" in refused(lambda: so.cancel_lines(existing, "VFAN", "1 Oct", at))


# ── the write ─────────────────────────────────────────────────────────────────

@test("append: JSON lines, one per line, that stock.read_promises reads back (the newest line of each ref)")
def _():
    with data_root() as root:
        log = stock.kpi_log_file()
        assert log == root / "business_data" / "ops" / "kpi_log.jsonl" and stock.read_promises() is None
        assert so.append(log, []) == 0 and so.append(log, None) == 0
        assert not log.exists() and not so.lock_of(log).exists()                 # no line: nothing made
        first = lines_of("Fábrica Uno", day(1), [("CB2", 100), ("CA1", 30)], source="plan-a.xlsx")
        assert so.append(log, first) == 2
        raw = log.read_text(encoding="utf-8")
        assert raw == "".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in first)
        assert "Fábrica Uno" in raw and "\\u" not in raw                        # written as it is (ensure_ascii off)
        assert [json.loads(t) for t in raw.splitlines()] == first
        assert sorted(stock.read_promises(), key=lambda r: r["ref"]) == sorted(first, key=lambda r: r["ref"])
        fixed = so.promise_lines("Fábrica Uno", day(1), [("CB2", 80)], "plan-b.xlsx", LATER)
        p = so.plan_write(stock.read_promises(), fixed)
        assert so.append(log, p["write"] + p["cancel"]) == 2                     # CB2 to 80, CA1 taken off
        back = {r["ref"]: r for r in stock.read_promises()}
        assert set(back) == {"fabrica-uno:2026-10-01:CB2", "fabrica-uno:2026-10-01:CA1"}
        assert back["fabrica-uno:2026-10-01:CB2"]["qty"] == "80"
        assert back["fabrica-uno:2026-10-01:CA1"]["state"] == "cancelled"
        assert len(log.read_text(encoding="utf-8").splitlines()) == 4            # appended, never rewritten
        assert log.read_text(encoding="utf-8").startswith(raw)
        again = so.plan_write(stock.read_promises(), fixed)
        assert again["write"] == [] and again["cancel"] == [] and so.append(log, again["write"]) == 0
        assert not so.lock_of(log).exists()


@test("append: a place outside business_data/ and exports/, or a line that is not a promise line, is refused first")
def _():
    with data_root() as root:
        line = lines_of("VFAN", day(1), [("CB2", 1)])
        for outside in (root / "scripts" / "kpi_log.jsonl", root / "Uplaods" / "201026" / "kpi_log.jsonl",
                        root / "kpi_log.jsonl", root / "business_data" / ".." / "kpi_log.jsonl"):
            raises(PermissionError, lambda p=outside: so.append(p, line))
            assert not outside.exists() and not so.lock_of(outside).exists()
        assert not (root / "scripts").exists() and not (root / "Uplaods").exists()      # not even the folder
        assert so.append(paths.exports_dir() / "orders" / "kpi_log.jsonl", line) == 1    # exports/ is allowed too
        log = stock.kpi_log_file()
        good = line[0]
        for bad in (dict(good, cost="1.5"),                        # a cost: never
                    dict(good, price_bhd="2"),                     # a price: never
                    {k: v for k, v in good.items() if k != "source"},
                    dict(good, qty=5),                             # a number, not text
                    dict(good, kind="quote"), dict(good, ref=""), dict(good, state="pending"),
                    dict(good, qty="2.5"),                         # an open line's quantity is a whole number
                    "not a line"):
            raises(ValueError, lambda b=bad: so.append(log, [good, b]))
            assert not log.exists() and not so.lock_of(log).exists(), bad     # nothing written, no lock left
        assert so.append(log, [dict(good, state="cancelled", qty="")]) == 1    # a cancel line keeps an unread quantity


@test("append: from a code worktree with no data root named, the project folder's KPI log is never written")
def _():
    with data_root():
        line = lines_of("VFAN", day(1), [("CB2", 1)])
        log = stock.kpi_log_file()
        saved_worktree, saved_root = paths.is_worktree, paths._override
        paths.is_worktree, paths._override = (lambda: True), None
        try:
            for write in (lambda: so.append(log, line), lambda: so.locked_append(log, ['{"kind": "report"}'])):
                assert "code worktree" in str(raises(PermissionError, write))
        finally:
            paths.is_worktree, paths._override = saved_worktree, saved_root
        assert not log.exists() and not so.lock_of(log).exists()
        assert so.append(log, line) == 1                     # with the data root named, the same write goes through


@test("append: a fresh lock refuses in one sentence, a stale one is taken over once, the lock is always let go")
def _():
    with data_root():
        log = stock.kpi_log_file()
        lock = so.lock_of(log)
        assert lock == log.with_name("kpi_log.jsonl.lock")
        line = lines_of("VFAN", day(1), [("CB2", 1)])
        paths.write_text(lock, "")                                     # another command is writing
        exc = raises(RuntimeError, lambda: so.append(log, line))
        assert str(exc) == "the KPI log is being written by another command: try again in a minute"
        assert lock.exists() and not log.exists()                      # its lock is not this command's to take away
        assert so.append(log, []) == 0                                 # no line: no lock wanted, no refusal
        young = time.time() - (so.LOCK_STALE_SECONDS - 10)              # 50 seconds old: still writing
        os.utime(lock, (young, young))
        raises(RuntimeError, lambda: so.append(log, line))
        old = time.time() - 120                                        # two minutes old: a command that stopped
        os.utime(lock, (old, old))
        assert so.append(log, line) == 1 and not lock.exists()
        assert len(log.read_text(encoding="utf-8").splitlines()) == 1
        # a stale lock taken by another command while this one removed it: tried once more, then refused; and
        # only one lock is ever taken over, even when the one found next is stale too
        real_remove = paths.remove_file
        for next_age in (0, 120):
            removed = []

            def racing(p, next_age=next_age, removed=removed):
                gone = real_remove(p)
                removed.append(Path(p))
                if Path(p) == lock:
                    paths.write_text(lock, "")
                    then = time.time() - next_age
                    os.utime(lock, (then, then))
                return gone

            paths.write_text(lock, "")
            os.utime(lock, (old, old))
            paths.remove_file = racing
            try:
                raises(RuntimeError, lambda: so.append(log, line))
            finally:
                paths.remove_file = real_remove
            assert removed == [lock] and lock.exists(), (next_age, removed)
            assert len(log.read_text(encoding="utf-8").splitlines()) == 1
            paths.remove_file(lock)
        # the lock is held while the lines are written, and let go after the write, failed or not
        real_append = paths.append_lines
        held = []

        def watching(path, texts):
            held.append(lock.exists())
            return real_append(path, texts)

        def failing(path, texts):
            raise OSError("the disk is full")

        try:
            paths.append_lines = watching
            assert so.append(log, line) == 1
            paths.append_lines = failing
            raises(OSError, lambda: so.append(log, line))
        finally:
            paths.append_lines = real_append
        assert held == [True] and not lock.exists()
        assert len(log.read_text(encoding="utf-8").splitlines()) == 2


# ── the clearing ──────────────────────────────────────────────────────────────

@test("fifo: one order received in full; then a part arrival that leaves the rest due")
def _():
    order = lines_of("VFAN", day(1), [("CB2", 100)])
    r = so.fifo([item(B, "CB2", (day(15), 100))], order, day(18), lead)
    x = r["lines"][0]
    # 100 ordered on 1 Oct, 100 received on 15 Oct: 100 - 100 = 0 due, nothing on order
    assert (x["qty"], x["received"], x["due"]) == (100, 100, 0) and r["on_order"] == {}
    assert (r["logged"], r["counted"], r["unmatched"], r["log"]) == (1, 0, 0, True)
    assert (x["ref"], x["supplier"], x["code"], x["item_name"], x["ledger_name"], x["date"]) == \
        ("vfan:2026-10-01:CB2", "VFAN", "CB2", B, B_LEDGER, day(1))
    assert x["expected_by"] == date(2026, 11, 10) and not x["late"] and not x["future"]     # 1 Oct + 40 days
    r = so.fifo([item(B, "CB2", (day(15), 60))], order, day(18), lead)
    x = r["lines"][0]
    # only 60 of the 100 arrived on 15 Oct: 100 - 60 = 40 still due, deducted from the order list
    assert (x["received"], x["due"]) == (60, 40) and r["on_order"] == {B_LEDGER: Decimal(40)} and r["counted"] == 1
    assert all(isinstance(v, Decimal) for v in (x["qty"], x["received"], x["due"], r["on_order"][B_LEDGER]))
    # the other 40 on 17 Oct: 60 + 40 = 100 received, nothing due
    assert so.fifo([item(B, "CB2", (day(15), 60), (day(17), 40))], order, day(18), lead)["on_order"] == {}


@test("fifo: two overlapping orders are paid oldest first: the older order's receipt never clears the newer one")
def _():
    at = datetime(2026, 10, 25, 9, 0)
    orders = lines_of("VFAN", day(1), [("CB2", 100)], at=at) + lines_of("VFAN", day(20), [("CB2", 50)], at=at)
    ra, rb = "vfan:2026-10-01:CB2", "vfan:2026-10-20:CB2"

    def run(*receipts):
        return so.fifo([item(B, "CB2", *receipts)], orders, day(25), lead)

    # A 100 on 1 Oct, B 50 on 20 Oct, 100 received on 25 Oct (after both): oldest first, A takes the 100
    # (100 - 100 = 0) and nothing is left for B: 50 stay due
    r = run((day(25), 100))
    assert (line_of(r, ra)["due"], line_of(r, rb)["received"], line_of(r, rb)["due"]) == (0, 0, 50)
    assert r["on_order"] == {B_LEDGER: Decimal(50)} and r["counted"] == 1
    assert [x["ref"] for x in r["lines"]] == [ra, rb]                                    # oldest first
    # the same 100 on 15 Oct, between the two: it pays A, and B (dated after it) cannot be paid by it
    r = run((day(15), 100))
    assert (line_of(r, ra)["due"], line_of(r, rb)["due"]) == (0, 50)
    # 120 on 25 Oct: A takes 100, B the other 20: 50 - 20 = 30 due
    r = run((day(25), 120))
    assert (line_of(r, ra)["received"], line_of(r, rb)["received"], line_of(r, rb)["due"]) == (100, 20, 30)
    # 60 on 10 Oct pays A only (60, 40 left); 70 on 22 Oct pays A's 40 and B 30: 50 - 30 = 20 due
    r = run((day(10), 60), (day(22), 70))
    assert (line_of(r, ra)["due"], line_of(r, rb)["received"], line_of(r, rb)["due"]) == (0, 30, 20)
    assert r["on_order"] == {B_LEDGER: Decimal(20)}


@test("fifo: a receipt on the order's own day pays nothing of it (strictly after); units beyond the orders are ignored")
def _():
    order = lines_of("VFAN", day(10), [("CB2", 40)])
    r = so.fifo([item(B, "CB2", (day(10), 40))], order, day(18), lead)
    assert r["lines"][0]["due"] == 40 and r["on_order"] == {B_LEDGER: Decimal(40)}    # 40 - 0: same day, not after
    r = so.fifo([item(B, "CB2", (day(10), 40), (day(11), 40))], order, day(18), lead)
    assert r["lines"][0]["due"] == 0                                                   # the 11 Oct receipt: 40 - 40
    # 20 ordered on 1 Oct, 50 received on 5 Oct: 20 pay the order and the other 30 are ignored, even by an order
    # placed after that receipt (10 on 6 Oct stay 10 due)
    first = lines_of("VFAN", day(1), [("CB2", 20)])
    after = lines_of("VFAN", day(6), [("CB2", 10)])
    r = so.fifo([item(B, "CB2", (day(5), 50))], first + after, day(18), lead)
    x = line_of(r, "vfan:2026-10-01:CB2")
    assert (x["received"], x["due"]) == (20, 0) and line_of(r, "vfan:2026-10-06:CB2")["due"] == 10
    assert r["on_order"] == {B_LEDGER: Decimal(10)}
    # old stock: a receipt dated before every order pays none of them (20 stay due)
    r = so.fifo([item(B, "CB2", (day(1, 9), 500))], first, day(18), lead)
    assert r["on_order"] == {B_LEDGER: Decimal(20)}


@test("fifo: a cancelled or otherwise closed line is passed over; the newest line of a ref is the one that counts")
def _():
    a = "ITEM-A Cable Alpha"
    both = lines_of("VFAN", day(1), [("CB2", 100), ("CA1", 30)])
    cancel = so.cancel_lines(both, "VFAN", day(1), LATER)          # CA1 first, then CB2 (by ref)
    its = [item(B, "CB2"), item(a, "CA1")]
    r = so.fifo(its, both + cancel[:1], day(18), lead)              # the raw log: CA1's newest line is its cancel
    assert [x["ref"] for x in r["lines"]] == ["vfan:2026-10-01:CB2"] and r["on_order"] == {B_LEDGER: Decimal(100)}
    assert (r["logged"], r["counted"], r["unmatched"]) == (1, 1, 0)
    r = so.fifo(its, both + cancel, day(18), lead)                  # the whole order cancelled
    assert (r["lines"], r["on_order"], r["logged"], r["counted"]) == ([], {}, 0, 0)
    for state in ("Cancelled", " CLOSED ", "done", "received", "canceled"):
        r = so.fifo(its, [dict(both[0], state=state)], day(18), lead)
        assert (r["lines"], r["logged"]) == ([], 0), state
    for state in ("open", "", None, "partial"):                    # anything not closed counts
        assert so.fifo(its, [dict(both[0], state=state)], day(18), lead)["counted"] == 1, state
    assert so.fifo(its, None, day(18), lead) == {"lines": [], "on_order": {}, "logged": 0, "counted": 0,
                                                  "unmatched": 0, "log": False}         # no log at all
    assert so.fifo(its, [], day(18), lead)["log"] is True


@test("fifo: expected by = order date + the supplier's lead time; late 30 days after that; future after the ledger")
def _():
    d = day(20)
    asked = []

    def lead_seen(supplier):
        asked.append(supplier)
        return lead(supplier)

    old = lines_of("VFAN", day(1, 8), [("CB2", 10), ("CA1", 10)])
    edge = lines_of("VFAN", day(11, 8), [("SN4", 10)])
    wek = lines_of("WEKOME", day(1, 9), [("HG3", 10)])
    soon = lines_of("VFAN", day(21), [("FO5", 10)], at=datetime(2026, 10, 21, 8, 0))
    its = [item(B, "CB2"), item("ITEM-A Cable Alpha", "CA1", (day(15, 9), 10)), item("ITEM-N Speaker New", "SN4"),
           item("ITEM-C Holder Gamma", "HG3"), item("ITEM-F Fan Out", "FO5")]
    r = so.fifo(its, old + edge + wek + soon, d, lead_seen)
    x = {line["code"]: line for line in r["lines"]}
    # CB2: 1 Aug + 40 = 10 Sep expected; 10 Sep + 30 = 10 Oct, and on 20 Oct its 10 are still due: late
    assert x["CB2"]["expected_by"] == day(10, 9) and x["CB2"]["late"]
    # CA1: the same order, but its 10 came on 15 Sep: nothing due, never late
    assert x["CA1"]["due"] == 0 and not x["CA1"]["late"]
    # SN4: 11 Aug + 40 = 20 Sep; + 30 = 20 Oct, which is D itself: not after it, so not late yet ...
    assert x["SN4"]["expected_by"] == day(20, 9) and not x["SN4"]["late"]
    assert so.fifo(its, edge, day(21), lead)["lines"][0]["late"]                     # ... one day on, it is
    # HG3: WEKOME's lead time is 50 days here: 1 Sep + 50 = 21 Oct, not late
    assert x["HG3"]["expected_by"] == day(21) and not x["HG3"]["late"]
    # FO5: ordered on 21 Oct, after the ledger's last day: future, due in full, not late
    assert x["FO5"]["future"] and x["FO5"]["due"] == 10 and not x["FO5"]["late"]
    assert [c for c, line in x.items() if line["future"]] == ["FO5"]
    assert set(asked) == {"VFAN", "WEKOME"}                                          # the line's own supplier
    # late_days is the caller's: with 0, SN4 is late on D already (20 Oct is after 20 Sep)
    assert {line["code"] for line in so.fifo(its, old + edge, d, lead, late_days=0)["lines"] if line["late"]} == \
        {"CB2", "SN4"}
    r = so.fifo(its, old, d, lambda supplier: None)                                  # no lead time: no expected day
    assert all(line["expected_by"] is None and not line["late"] for line in r["lines"])


@test("fifo: a code no item has, a code or model code two items share, an unreadable quantity or date: unmatched")
def _():
    its = [item(B, "CB2", (day(15), 5)), item("ITEM-X Case Red", "XR1"), item("ITEM-X Case Blue", "XB1"),
           item("X05 Holder Uno", "X05 UC"), item("ITEM-T Twin One", "TW1"), item("ITEM-T Twin Two", "TW1")]
    logged = lines_of("VFAN", day(1), [("NOPE-9", 5), ("ITEM-B", 7), ("ITEM-X", 3), ("x05  uc", 4), ("TW1", 2)])
    by_hand = [  # lines written into the log by hand: a quantity or a date that cannot be read, then a good one
        {"kind": "promise", "ref": "h1", "item_code": "CB2", "qty": "lots", "event_date": "2026-10-01"},
        {"kind": "promise", "ref": "h2", "item_code": "CB2", "qty": "2.5", "event_date": "2026-10-01"},
        {"kind": "promise", "ref": "h3", "item_code": "CB2", "qty": "0", "event_date": "2026-10-01"},
        {"kind": "promise", "ref": "h4", "item_code": "CB2", "qty": 6, "event_date": "next week"},
        {"kind": "promise", "ref": "h5", "item_code": "cb2", "qty": 3, "event_date": "2026-10-02"},   # no supplier
        {"kind": "quote", "ref": "q1", "item_code": "CB2", "qty": 500, "event_date": "2026-10-01"},   # no promise
    ]
    r = so.fifo(its, logged + by_hand, day(18), lead)
    by = {line["ref"]: line for line in r["lines"]}
    assert set(by) == {"vfan:2026-10-01:NOPE-9", "vfan:2026-10-01:ITEM-B", "vfan:2026-10-01:ITEM-X",
                       "vfan:2026-10-01:X05 UC", "vfan:2026-10-01:TW1", "h5"}           # h1-h4 cannot be read
    nope = by["vfan:2026-10-01:NOPE-9"]
    assert nope["item_name"] is None and nope["ledger_name"] is None and (nope["received"], nope["due"]) == (0, 5)
    assert by["vfan:2026-10-01:ITEM-X"]["ledger_name"] is None                  # ITEM-X starts two items' names
    assert by["vfan:2026-10-01:TW1"]["ledger_name"] is None                     # TW1 is the code of two items
    assert by["vfan:2026-10-01:ITEM-B"]["ledger_name"] == B_LEDGER              # its model code: one item
    assert by["vfan:2026-10-01:X05 UC"]["ledger_name"] == "X05 Holder Uno X05 UC"   # case and inner spaces
    h5 = by["h5"]
    assert h5["ledger_name"] == B_LEDGER and h5["supplier"] == "" and h5["expected_by"] == day(2) + timedelta(days=50)
    # CB2: ITEM-B 7 (1 Oct) and h5 3 (2 Oct); the 5 received on 15 Oct pay the older first: 7 - 5 = 2, h5 3 left
    assert (by["vfan:2026-10-01:ITEM-B"]["due"], h5["due"]) == (2, 3)
    assert r["on_order"] == {B_LEDGER: Decimal(5), "X05 Holder Uno X05 UC": Decimal(4)}   # 2 + 3, and 4
    # open: 5 logged + h1-h5 = 10; unmatched: NOPE-9, ITEM-X, TW1 and h1-h4 = 7; counted: ITEM-B, X05 UC, h5 = 3
    assert (r["logged"], r["unmatched"], r["counted"]) == (10, 7, 3), r


@test("fifo on the world of tests/test_ops_stock.py: the 30 Jan receipt pays the January order, not the March one")
def _():
    with data_root():
        m, _inp = model_of(World())
    plannable = [it for it in m.items if not it.display]
    at = datetime(2026, 3, 15, 9, 0)
    jan = so.promise_lines("VFAN", "2026-01-01", [("cb2", 50)], "typed", at)
    mar = so.promise_lines("VFAN", "2026-03-01", [("CB2", 30), ("NOPE-9", 5)], "typed", at)
    gone = so.promise_lines("VFAN", "2026-03-02", [("CB2", 99)], "typed", at)
    gone += so.cancel_lines(gone, "VFAN", "2026-03-02", at)
    r = so.fifo(plannable, jan + mar + gone, D, lambda supplier: 40)
    # ITEM-B's receipts: 30 on 1 Jun 2025 (before both orders: it pays neither) and 100 on 30 Jan 2026, which pays
    # the January order's 50 (50 - 50 = 0); the March order is dated after it, so its 30 stay due; 50 are ignored
    j, mc = line_of(r, "vfan:2026-01-01:CB2"), line_of(r, "vfan:2026-03-01:CB2")
    assert (j["received"], j["due"], mc["received"], mc["due"]) == (50, 0, 0, 30)
    assert r["on_order"] == {L("B"): Decimal(30)}
    assert (r["logged"], r["counted"], r["unmatched"]) == (3, 1, 1), r                 # the 2 Mar order: cancelled
    assert mc["expected_by"] == date(2026, 4, 10) and not mc["late"] and not mc["future"]   # 1 Mar + 40 days


# ── the module ────────────────────────────────────────────────────────────────

@test("the module: no top-level import of stock (it would be circular), no client, no file opened but its own two")
def _():
    src = (ROOT / "scripts" / "ops" / "stock_orders.py").read_text(encoding="utf-8")
    for word in ("import psycopg", "import httpx", "supabase", "subprocess", "yq_load_go", ".table(", "upsert",
                 "write_text", "db.rpc", "db.session"):
        assert word not in src, word
    top_level = r"^(from scripts\.ops import .*\bstock\b|import scripts\.ops\.stock\b|from scripts\.ops\.stock\b)"
    assert not re.search(top_level, src, re.M), "a top-level import of stock would be circular"
    assert "from scripts.ops import stock" in src                              # ... it is done inside a function
    calls = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)]
    assert not any(isinstance(c.func, ast.Name) and c.func.id == "open" for c in calls), "a file opened directly"
    assert "paths.append_lines(" in src and "os.O_EXCL" in src                 # the log through paths; the lock
    assert so.LINE_KEYS == ("kind", "ref", "supplier", "item_code", "qty", "event_date", "logged_at", "state", "source")


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
    paths.set_data_root(None)
    config.use(None)
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
