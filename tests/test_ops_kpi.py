"""Ops engine: the KPI scorecard (python -m scripts.ops kpi) and the KPI log (kpi log ...), on invented sources.

    python -m tests.test_ops_kpi

Same lightweight runner as the other suites (no pytest). No database, no network, no .env: SUPABASE_URL, SUPABASE_KEY
and DATABASE_URL are pinned to ci.invalid BEFORE any import, and the temporary data roots, the stock world and its
stand-ins are the ones of tests/test_ops_stock.py (it imports scripts.ops.db first and sets stdout to UTF-8). Every
log line, build record, screen and workbook is written under a temporary data root. All names and numbers are
invented.

Covered:
  * the log writer: the fields of each kind read one way, the line's keys (exactly kpi_log.KEYS), the refs and the
    purchase key; the preview, its token and the printed commit line (run through the real entry point); a commit
    with the token, the same line twice (nothing), a changed field (refused), --commit and --token alone; every
    refusal in one plain sentence (unknown kind, a field not taken or not given, a state, a check type, a date, a
    future date, a quantity, an amount, a phone or ID number, an e-mail address, a character the command line cannot
    carry, an accusing word, a note too long); `sent` refused without a build record, for a day that is no
    Saturday, before the report was built; logged with one;
  * the log shared with stock_orders: stock.read_promises still sees only the promise lines; the one lock
    (locked_append) refuses a write of either while the other holds it, a stale lock is taken over, the place and
    the texts are checked first;
  * every row of the scorecard, each status it can take, on invented sources (the stock world of the stock tests,
    with receipts, damage and a launch added where a row needs them);
  * the 90-day plan block; the header counts; the log marker; a MET with no evidence; a row that fails;
  * the command: its screen and facts under exports/ops/<D>/, the default month, the refusals, no long digit run;
  * the month workbook's KPI and KPI log sheets: the COUNTIF header equals the card's counts (from the card and from
    its JSON), typed values, no evidence line on the sheet, the totals;
  * the modules: reads only through scripts.ops.db, the log written only through stock_orders.locked_append, the
    screen only through sales.save_screen, every statement a read; the skill file's own rules.
"""
from __future__ import annotations

import ast
import io
import json
import os
import re
import shlex
import sys
import time
import traceback
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"

from tests.test_ops_stock import (  # noqa: E402  (first: it imports scripts.ops.db first, and sets stdout to UTF-8)
    CODES, D, World, data_root, L, model_of, raises, world)
from scripts.ops import __main__ as cli  # noqa: E402
from scripts.ops import config, db, kpi, kpi_log, ledger, paths, stock, stock_orders  # noqa: E402
from scripts.ops.workbook import formula as fx  # noqa: E402
from scripts.ops.workbook import sheets_kpi  # noqa: E402
from scripts.ops.workbook.book import Book as Workbook  # noqa: E402

TESTS: list[tuple[str, object]] = []

BH = timezone(timedelta(hours=3))
NOW = datetime(2026, 3, 16, 9, 30, 5, 250000, tzinfo=BH)        # every line in these tests is logged at this moment
TODAY = NOW.date()
FEB, MAR, JAN, DEC = "2026-02", "2026-03", "2026-01", "2025-12"
STEP = {"sales.kpi_steps": [{"from": "2026-01", "bhd": 100}]}
NINE = re.compile(r"(?<![0-9])[0-9]{9}(?![0-9])")
LONG = re.compile(r"(?<![0-9])[0-9]{15,}(?![0-9])")
Q_ITEM = "ITEM-Q Cable Quick QC7"                               # a launch: first received on 20 Jan 2026
kpi_log.CLOCK = lambda: NOW


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


def cfg(extra: dict | None = None) -> dict:
    return dict(STEP, **(extra or {}))


class Args:
    """The kpi command's options, as argparse hands them over."""

    def __init__(self, *spec, **fields):
        self.spec, self.ledger, self.json, self.data_root = list(spec), "auto", False, None
        for f in kpi_log.FIELDS:
            setattr(self, f, fields.get(f))
        self.commit, self.token = fields.get("commit", False), fields.get("token")


def log(*words, **fields) -> dict:
    """kpi log <words> --<field> <value> ... (a preview, or a commit with commit=True and token=...)."""
    return kpi.run(Args("log", *words, **fields))


def refused(*words, **fields) -> str:
    out = log(*words, **fields)
    assert out.get("exit") == 1 and not out["ok"], out
    text = out["text"]
    assert text and "\n" not in text and text.endswith("."), text
    return text


def run_printed(text: str) -> tuple[int, str]:
    """Run the commit line a preview printed, as printed, through the real entry point (the temporary data root)."""
    line = next(ln.strip() for ln in text.splitlines() if ln.strip().startswith("python -m scripts.ops kpi log"))
    argv = shlex.split(line)[3:] + ["--data-root", str(paths.data_root())]
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli.main(argv)
    return code, buf.getvalue()


def logged(*words, **fields) -> dict:
    """Preview, then log through the printed line; the line written."""
    prev = log(*words, **fields)
    assert prev.get("exit") is None, prev["text"]
    code, out = run_printed(prev["text"])
    assert code == 0 and "Logged: 1 line" in out, out
    return kpi_log.newest(kpi_log.read_lines(), kpi_log.KINDS[words[0]].name if words[0] != "sent" else "report")[
        prev["flags"]["ref"]]


def record(kind: str, key: str, built: str, folder: str = "2026-03-15", **extra) -> Path:
    """A build record of a workbook, as the builds write them (exports/ops/<D>/report-<kind>-<key>.json)."""
    rec = {"month" if kind == "month" else "week_ending": key, "data_date": folder, "built_at": built, **extra}
    return paths.write_text(paths.exports_dir(folder) / f"report-{kind}-{key}.json", json.dumps(rec))


def src_of(stock_parts=None, today: date = TODAY, **parts) -> kpi.Sources:
    """The scorecard's reads, given: the stock model (a (model, inp) pair, or an exception), and any other part."""
    given = {"anchors": {"focus_to": D.isoformat(), "focus_from": "2025-02-01", "uploaded_at": None}, "state": {},
             "market": [], "finds": [], "upcoming": [], "po": [], "months": {}, "skus": [],
             "shops": {"shops": 0, "regular": 0}, "rep_days": 0}
    if isinstance(stock_parts, BaseException):
        given["stock"] = stock_parts
    elif stock_parts is not None:
        given["stock"] = {"model": stock_parts[0], "inp": stock_parts[1]}
    else:
        given["stock"] = stock.StockInput("No stock ledger is loaded: there is no stock to plan.")
    given.update(parts)
    return kpi.Sources(given=given, today=today)


def ctx_of(month: str, *, today: date = TODAY, partial: bool | None = None, d: date = D) -> kpi.Ctx:
    first = date(int(month[:4]), int(month[5:]), 1)
    last = kpi.month_end(first)
    part = (last >= d) if partial is None else partial
    lines = kpi_log.read_lines()
    return kpi.Ctx(month=month, first=first, last=last, d=d, today=today, whole=not part, partial=part,
                   log_started=kpi_log.started(lines))


def stock_world(book=None):
    return model_of(World(book or world()))


def more_world():
    """The stock world with what the area 3 and 4 rows need in February: two receipts (one above BHD 50, one below),
    three units sent to damage, and a launch (ITEM-Q, first received on 20 Jan, one sold a day)."""
    b = world()
    b.receipt(L("C"), date(2026, 2, 12), 20, 60, "MRN:T-26-02-1")
    b.receipt(L("A"), date(2026, 2, 20), 10, 5, "MRN:T-26-02-2")
    b.to_damage(L("A"), date(2026, 2, 10), 3)
    b.receipt(Q_ITEM, date(2026, 1, 20), 50, 25, "MRN:T-26-01-9")
    d = date(2026, 1, 20)
    while d <= D:
        b.issue(Q_ITEM, d, 1).sale(Q_ITEM, d, 1)
        d += timedelta(days=1)
    return b


def every_met_named(card: dict) -> None:
    for r in card["rows"]:
        assert r["status"] in kpi.STATUSES, r
        if r["status"] == kpi.MET:
            assert r["evidence"] and all(isinstance(e, str) and e for e in r["evidence"]), r


# ── the log writer ────────────────────────────────────────────────────────────

@test("the fields are read one way; a line holds exactly KEYS; each kind's ref; the purchase key drops the voucher prefix")
def _():
    for given, key in (("MRN:T-26-01-1", "T-26-01-1"), ("t 26 01 1", "T-26-01-1"), (" mrn : T-26-01-1", "T-26-01-1"),
                       ("Eid order", "EID-ORDER"), ("Fábrica Uno", "FABRICA-UNO"), ("", "")):
        assert kpi_log.purchase_key(given) == key, (given, kpi_log.purchase_key(given))
    with data_root():
        f = kpi_log.read_fields("candidate", {"ref": "  TWS   x1 ", "check_types": "Demand, price,price",
                                              "qty": "1,000"}, TODAY)
        assert f == {"ref": "TWS x1", "check_types": ["price", "demand"], "qty": "1000", "event_date": "2026-03-16",
                     "state": "open", "source": "typed"}, f
        line = kpi_log.make_line("candidate", f, NOW)
        assert tuple(sorted(line)) == tuple(sorted(kpi_log.KEYS)) and line["ref"] == "cand:TWS-X1"
        assert line["logged_at"] == "2026-03-16T09:30:05+03:00" and len(line["id"]) == 12 and line["id"].isalpha()
        assert line["supplier"] is None and line["amount_bhd"] is None and line["check_types"] == ["price", "demand"]
        assert all(isinstance(v, str) or v is None for k, v in line.items() if k != "check_types")
        refs = {
            "candidate": ({"item_code": "cb9"}, "cand:CB9"),
            "quote": ({"ref": "MRN:T-26-02-1", "supplier": "VFAN Co.", "amount_bhd": "12.50"}, "quote:T-26-02-1:vfan-co"),
            "terms": ({"supplier": "VFAN", "note": "thirty days credit"}, "terms:GENERAL:vfan"),
            "receipt_check": ({"ref": "T-26-02-1", "qty": "20", "check_types": "quantity"}, "check:T-26-02-1"),
            "claim": ({"ref": "T-26-02-1 cables", "supplier": "VFAN"}, "claim:vfan:T-26-02-1-CABLES"),
        }
        for kind, (given, ref) in refs.items():
            fields = kpi_log.read_fields(kind, given, TODAY)
            assert kpi_log.make_line(kind, fields, NOW)["ref"] == ref, kind
        assert kpi_log.read_fields("quote", {"ref": "x", "supplier": "V", "amount_bhd": "12.50"}, TODAY)["amount_bhd"] \
            == "12.5"
        closed = kpi_log.read_fields("claim", {"ref": "x", "supplier": "V", "state": "Closed"}, TODAY)
        assert closed["state"] == "closed" and closed["closed_date"] == TODAY.isoformat()   # closed on its event day
        assert kpi_log.token(line) == kpi_log.token(dict(line, logged_at="2026-03-17T10:00:00+03:00", id="x"))
        assert kpi_log.token(line) != kpi_log.token(dict(line, qty="999"))
        assert re.fullmatch(r"[a-p]{12}", kpi_log.token(line))


@test("the preview prints the line and the commit line; the printed line logs it; the same line again writes nothing")
def _():
    with data_root():
        prev = log("candidate", ref="TWS x1", check_types="price,demand", supplier="VFAN", note="two shops compared")
        assert prev["ok"] and prev.get("exit") is None, prev
        text = prev["text"]
        assert "KPI LOG  a product assessed before it is ordered: cand:TWS-X1" in text, text
        assert "Nothing is logged yet. Once he says yes, this logs exactly this line:" in text
        assert "still to log: quality, competitor" in text and not kpi_log.log_file().exists()
        tok = prev["flags"]["token"]
        assert f"--commit --token {tok}" in text and "--event-date 2026-03-16" in text      # the day is written out
        code, out = run_printed(text)
        assert code == 0 and "Logged: 1 line in the KPI log." in out, out
        rows = kpi_log.read_lines()
        assert len(rows) == 1 and rows[0]["ref"] == "cand:TWS-X1" and rows[0]["note"] == "two shops compared"
        raw = kpi_log.log_file().read_text(encoding="utf-8")
        assert raw == json.dumps(rows[0], ensure_ascii=False, sort_keys=True) + "\n"
        again = log("candidate", ref="TWS x1", check_types="demand,price", supplier="VFAN", note="two shops compared")
        assert "Nothing to log: the KPI log already holds this line as it is." in again["text"]
        assert "--commit" not in again["text"]
        code, out = run_printed(text)                       # the old commit line, once more: nothing written
        assert code == 0 and "Nothing to log" in out and len(kpi_log.read_lines()) == 1
        more = log("candidate", ref="TWS x1", check_types="quality", source="said")
        assert "On file for cand:TWS-X1 with this line: price, quality, demand (still to log: competitor)." \
            in more["text"], more["text"]
        assert '--source said' in more["text"]
        run_printed(more["text"])
        assert [r["check_types"] for r in kpi_log.of_ref(kpi_log.read_lines(), "candidate")["cand:TWS-X1"]] == \
            [["price", "demand"], ["quality"]]                # a later line of the ref is appended, never written over


@test("a commit is refused when a field changed since the preview, without its token, or a token without --commit")
def _():
    with data_root():
        prev = log("quote", ref="T-26-02-1", supplier="VFAN", amount_bhd="40")
        tok = prev["flags"]["token"]
        text = refused("quote", ref="T-26-02-1", supplier="VFAN", amount_bhd="41", event_date="2026-03-16",
                       state="received", commit=True, token=tok)
        assert text == "The line changed since the preview: nothing was logged. Run the preview again and log what it shows."
        assert "run the preview first" in refused("quote", ref="T-26-02-1", supplier="VFAN", commit=True)
        assert "--token goes with --commit" in refused("quote", ref="T-26-02-1", supplier="VFAN", token=tok)
        assert not kpi_log.log_file().exists()
        ok = log("quote", ref="T-26-02-1", supplier="VFAN", amount_bhd="40", event_date="2026-03-16", state="received",
                 commit=True, token=tok)
        assert ok["ok"] and ok["flags"]["logged"] == 1 and len(kpi_log.read_lines()) == 1
        assert "lists the kinds" in refused(ref="x")       # fields with no kind
        listing = log()["text"]
        for kind in ("candidate", "quote", "terms", "receipt_check", "claim", "sent weekly"):
            assert kind in listing, kind


@test("every refusal is one plain sentence, and nothing is written")
def _():
    with data_root():
        cases = (
            (("frobnicate",), {}, "is not a kind of the KPI log"),
            (("report",), {}, "is not a kind of the KPI log"),
            (("quote",), {"ref": "x", "supplier": "V", "check_types": "price"}, "A quote takes no check types"),
            (("quote",), {"ref": "x"}, "A quote needs its supplier: give --supplier."),
            (("candidate",), {"note": "x"}, "is named by --ref (a short name) or --item-code"),
            (("candidate",), {"ref": "x", "state": "maybe"}, "has no state 'maybe'"),
            (("candidate",), {"ref": "x", "check_types": "price,colour"}, "has no check type 'colour'"),
            (("candidate",), {"ref": "x", "event_date": "16/03/2026"}, "is written YYYY-MM-DD"),
            (("candidate",), {"ref": "x", "event_date": "2026-02-30"}, "is written YYYY-MM-DD"),
            (("candidate",), {"ref": "x", "event_date": "2026-03-17"}, "is after today (2026-03-16)"),
            (("claim",), {"ref": "x", "supplier": "V", "state": "closed", "closed_date": "2026-03-20"}, "is after today"),
            (("claim",), {"ref": "x", "supplier": "V", "closed_date": "2026-03-10"}, "A claim still raised has no closed date"),
            (("candidate",), {"ref": "x", "qty": "2.5"}, "is not a whole number of units"),
            (("quote",), {"ref": "x", "supplier": "V", "amount_bhd": "1.2345"}, "at most three decimals"),
            (("quote",), {"ref": "x", "supplier": "V", "amount_bhd": "-4"}, "is not a number of BHD"),
            (("candidate",), {"ref": "-x"}, "cannot start with '-'"),
            (("candidate",), {"ref": "x", "note": "called " + "3912" + " 3456"}, "a run of eight or more digits"),
            (("candidate",), {"ref": "x", "note": "ID " + "8" * 9}, "a run of eight or more digits"),
            (("candidate",), {"ref": "x", "note": "mail " + "a" + "@" + "b.example"}, "an e-mail address"),
            (("candidate",), {"ref": "x", "note": 'say "yes"'}, "cannot carry safely"),
            (("candidate",), {"ref": "x", "note": "cost $5"}, "cannot carry safely"),
            (("candidate",), {"ref": "stolen goods"}, "a word the log never uses"),
            (("candidate",), {"ref": "x", "note": "y" * (kpi_log.NOTE_MAX + 1)}, "is longer than"),
            (("candidate",), {"ref": "x", "note": "  "}, "was given empty"),
            (("candidate",), {"ref": "/+/"}, "has no letter or digit"),
            (("receipt_check",), {"ref": "T-1", "qty": "4", "check_types": " "}, "Check types were given empty"),
            (("candidate", "extra"), {"ref": "x"}, "One kind at a time"),
        )
        for words, fields, needle in cases:
            text = refused(*words, **fields)
            assert needle in text, (words, fields, text)
        assert not kpi_log.log_file().exists()
        assert log("receipt_check", ref="T-1", qty="0", check_types="quantity")["ok"]      # nought units is a count


@test("`sent` is refused without a build record, for a day that is no Saturday, or before the report was built")
def _():
    with data_root():
        text = refused("sent", "weekly", "2026-03-14")
        assert text.startswith("No build record shows that the weekly report for the week ending 14 March 2026 was "
                               "built"), text
        assert "/yq-month builds it" in refused("sent", "monthly", FEB)
        assert "is not one" in refused("sent", "weekly", "2026-03-13")          # a Friday
        assert "is not one" in refused("sent", "monthly", "2026-13")
        assert "Say which report was sent" in refused("sent", "daily", FEB)
        record("week", "2026-03-14", "2026-03-15T08:00:00+03:00")
        assert "A report takes no ref" in refused("sent", "weekly", "2026-03-14", ref="x")
        record("month", FEB, "2026-03-04T10:00:00+03:00", draft=True)
        assert "cannot have been sent on 2026-03-14, before that" in refused("sent", "weekly", "2026-03-14",
                                                                              event_date="2026-03-14")
        line = logged("sent", "weekly", "2026-03-14")
        assert line["ref"] == "report:weekly:2026-03-14" and line["state"] == "sent" and line["kind"] == "report"
        assert logged("sent", "monthly", FEB, note="to management")["ref"] == "report:monthly:2026-02"
        # a record of another period beside it is never taken for this one
        paths.write_text(paths.exports_dir("2026-03-15") / "report-month-2026-1.json", json.dumps({"month": "2026-01"}))
        assert [r["month"] for r in kpi_log.records("month", FEB)] == [FEB]
        assert kpi_log.records("month", "2026-10") == []


@test("the log is shared with the orders: read_promises sees only promise lines; one lock for both writers")
def _():
    with data_root():
        promise = stock_orders.promise_lines("VFAN", date(2026, 3, 1), [("CB2", 10)], "typed", NOW.replace(tzinfo=None))
        assert stock_orders.append(stock.kpi_log_file(), promise) == 1
        logged("candidate", item_code="CB2", check_types="price")
        logged("quote", ref="T-26-02-1", supplier="VFAN")
        assert kpi_log.log_file() == stock.kpi_log_file()
        assert stock.read_promises() == promise, stock.read_promises()
        assert [r["kind"] for r in kpi_log.read_lines()] == ["promise", "candidate", "quote"]
        fifo = stock_orders.fifo([], stock.read_promises(), D, lambda s: 40)
        assert fifo["logged"] == 1                          # the evidence lines are no goods on order
        lock = stock_orders.lock_of(kpi_log.log_file())
        paths.write_text(lock, "")                          # the order command is writing
        prev = log("terms", supplier="VFAN", note="thirty days credit")
        out = log("terms", supplier="VFAN", note="thirty days credit", event_date="2026-03-16", state="agreed",
                  commit=True, token=prev["flags"]["token"])
        assert out.get("exit") == 1 and out["text"] == ("Nothing was logged: the KPI log is being written by another "
                                                        "command: try again in a minute."), out
        old = time.time() - 120                             # left by a command that stopped: taken over
        os.utime(lock, (old, old))
        assert log("terms", supplier="VFAN", note="thirty days credit", event_date="2026-03-16", state="agreed",
                   commit=True, token=prev["flags"]["token"])["flags"]["logged"] == 1
        assert not lock.exists()
        kpi_lock = kpi_log.log_file().with_name("kpi_log.jsonl.lock")
        paths.write_text(kpi_lock, "")                      # ... and the evidence command's lock stops an order
        exc = raises(RuntimeError, lambda: stock_orders.append(stock.kpi_log_file(), promise))
        assert str(exc) == stock_orders.LOCK_BUSY
        paths.remove_file(kpi_lock)
        assert stock_orders.locked_append(kpi_log.log_file(), []) == 0 and not lock.exists()
        raises(ValueError, lambda: stock_orders.locked_append(kpi_log.log_file(), ["one\ntwo"]))
        raises(ValueError, lambda: stock_orders.locked_append(kpi_log.log_file(), [{"kind": "x"}]))
        raises(PermissionError, lambda: stock_orders.locked_append(paths.data_root() / "kpi_log.jsonl", ["{}"]))
        assert not lock.exists() and len(kpi_log.read_lines()) == 4


# ── the scorecard, row by row ─────────────────────────────────────────────────

@test("1a stock cover: MET inside the band, NOT MET outside it, NOT EVIDENCED for an older month or with no ledger")
def _():
    with data_root(cfg()):
        parts = stock_world()
        row = kpi.row_cover(ctx_of(FEB), src_of(parts))
        assert row["status"] == kpi.NOT_MET and row["target"] == "30 to 45 days", row
        assert row["actual"].startswith(f"{int(row['value'])} days at the end of February 2026; "), row
        assert "at today's costs" in row["evidence"][0] and "the last supplier receipt (MRN:T-26-03-1" in row["evidence"][1]
        now = kpi.row_cover(ctx_of(MAR), src_of(parts))
        assert now["actual"].startswith(f"{int(now['value'])} days on Sun 15 Mar 2026") and "the month before)" in now["actual"]
        days = int(row["value"])
        assert kpi.row_cover(ctx_of(JAN), src_of(parts))["status"] == kpi.NOT_EVIDENCED
        assert "is not one of them" in kpi.row_cover(ctx_of(JAN), src_of(parts))["evidence"][0]
        nothing = kpi.row_cover(ctx_of(FEB), src_of())
        assert nothing["status"] == kpi.NOT_EVIDENCED and "No stock ledger is loaded" in nothing["evidence"][0]
    with data_root(cfg({"stock.cover_kpi_days": [days - 1, days + 1]})):
        assert kpi.row_cover(ctx_of(FEB), src_of(stock_world()))["status"] == kpi.MET


@test("1b fast-mover availability: NOT MET under the plan's share, MET at the configured one, NOT EVIDENCED unread")
def _():
    with data_root(cfg()):
        ctx = ctx_of(FEB)
        row = kpi.row_availability(ctx, src_of(stock_world()))
        assert row["status"] == kpi.NOT_MET and row["value"] == Decimal("0.5"), row
        assert row["target"] == "95% of the fast movers' days in stock" and "kpi.fast_mover_availability_min" in ctx.used
        assert "out on every one of those days: FO5" in row["evidence"], row["evidence"]
        assert kpi.row_availability(ctx_of(FEB), src_of())["status"] == kpi.NOT_EVIDENCED
    with data_root(cfg({"kpi.fast_mover_availability_min": 0.5})):
        ctx = ctx_of(FEB)
        assert kpi.row_availability(ctx, src_of(stock_world()))["status"] == kpi.MET and not ctx.used
    with data_root(cfg({"kpi.fast_mover_availability_min": 2})):            # a wrong value stops the row, in words
        row = kpi._safe(kpi.row_availability, "1b", ctx_of(FEB), src_of(stock_world()))
        assert row["status"] == kpi.NOT_EVIDENCED and "must be a share from nought to one" in row["evidence"][0], row


@test("1c ageing: MET when the month's build record names its ageing part; NOT EVIDENCED without; NOTHING DUE mid-month")
def _():
    with data_root(cfg()):
        row = kpi.row_ageing(ctx_of(FEB), src_of())
        assert row["status"] == kpi.NOT_EVIDENCED and "no workbook of February 2026 is on record" in row["evidence"][0]
        record("month", FEB, "2026-03-03T09:00:00+03:00", draft=True)
        row = kpi.row_ageing(ctx_of(FEB), src_of())
        assert row["status"] == kpi.NOT_EVIDENCED and "has no ageing part" in row["evidence"][0], row
        record("month", FEB, "2026-03-04T09:00:00+03:00", folder="2026-03-16", draft=False,
               parts={"ageing": {"items_with_action": 3}})
        row = kpi.row_ageing(ctx_of(FEB), src_of())
        assert row["status"] == kpi.MET and "the final workbook of February 2026 built on Wed 04 Mar 2026 holds the " \
            "ageing part" in row["evidence"][0] and "3 items with an action" in row["evidence"][0], row
        assert kpi.row_ageing(ctx_of(MAR), src_of())["status"] == kpi.NOTHING_DUE
        assert kpi._names_ageing({"parts": ["stock", "Ageing"]}) and not kpi._names_ageing({"parts": {"ageing": None}})


@test("2a new items: four sources, one code counted once; MET in the band, NOT MET outside it, NOT EVIDENCED unread")
def _():
    market = [{"item_id": 1, "first_move": "2026-02-03", "code": "CB9"}, {"item_id": 2, "first_move": "2026-01-30"},
              {"item_id": 3, "first_move": "2026-02-27", "code": None}]
    finds = [{"id": 7, "status": "promoted", "code": "cb9", "moved_on": "2026-02-10"},
             {"id": 8, "status": "archived", "moved_on": "2026-02-10"}]
    upcoming = [{"model_code": "WK-1", "code": None, "status": "draft", "created_on": "2026-02-05"},
                {"model_code": "WK-2", "code": None, "status": "withdrawn", "created_on": "2026-02-05"}]
    with data_root(cfg({"kpi.new_skus_band": [3, 4]})):
        logged("candidate", ref="TWS x1", event_date="2026-02-20")
        logged("candidate", item_code="WK-1", event_date="2026-02-21")       # the Upcoming card's model, again
        src = src_of(market=market, finds=finds, upcoming=upcoming)
        row = kpi.row_new_items(ctx_of(FEB), src)
        assert row["value"] == 4 and row["status"] == kpi.MET, row      # CB9, item 3, WK-1, TWS-X1
        assert "2 items named by two sources, counted once" in row["evidence"][0], row["evidence"]
        assert row["actual"] == "4 new items: 2 from Market Intel, 1 finds, 1 Upcoming cards, 2 candidates logged"
        unread = src_of(market=RuntimeError("down"), finds=finds, upcoming=upcoming)
        row = kpi.row_new_items(ctx_of(FEB), unread)
        assert row["status"] == kpi.NOT_EVIDENCED and "Market Intel (the portal) could not be read (RuntimeError)" in \
            row["evidence"], row
    with data_root(cfg({"kpi.new_skus_band": [5, 10]})):
        assert kpi.row_new_items(ctx_of(FEB), src_of(market=market))["status"] == kpi.NOT_MET     # 2: below
    with data_root(cfg({"kpi.new_skus_band": [0, 1]})):
        row = kpi.row_new_items(ctx_of(FEB), src_of(market=market, finds=RuntimeError("down")))
        assert row["status"] == kpi.NOT_MET, row                        # above the band: more cannot bring it back


@test("2b checked before ordering: judged on the day each check was LOGGED; a new code with no candidate; nothing ordered")
def _():
    with data_root(cfg()):
        parts = stock_world()
        assert kpi.row_checked(ctx_of(MAR), src_of(parts))["status"] == kpi.NOTHING_DUE
        log_at = lambda when: setattr(kpi_log, "CLOCK", lambda: when)   # noqa: E731
        try:
            log_at(datetime(2026, 3, 2, 9, 0, tzinfo=BH))
            logged("candidate", item_code="NW1", check_types="price,quality,demand,competitor", event_date="2026-03-02")
            log_at(datetime(2026, 3, 3, 9, 0, tzinfo=BH))
            logged("candidate", item_code="NW1", state="ordered", event_date="2026-03-03")
            log_at(datetime(2026, 3, 4, 9, 0, tzinfo=BH))
            logged("candidate", ref="Lamp", item_code="NW2", check_types="price,demand", event_date="2026-03-04")
            log_at(datetime(2026, 3, 9, 9, 0, tzinfo=BH))
            logged("candidate", ref="Lamp", check_types="quality,competitor", event_date="2026-03-04")   # too late
        finally:
            kpi_log.CLOCK = lambda: NOW
        promises = stock_orders.promise_lines("VFAN", date(2026, 3, 5), [("NW2", 10), ("CA1", 50), ("ZZ9", 5)],
                                              "typed", datetime(2026, 3, 5, 10, 0))
        stock_orders.append(stock.kpi_log_file(), promises)
        row = kpi.row_checked(ctx_of(MAR), src_of(parts))
        ev = "\n".join(row["evidence"])
        assert row["status"] == kpi.NOT_MET, row
        assert "cand:NW1: ordered on Tue 03 Mar 2026, all four checks logged by then" in ev
        assert "cand:LAMP: ordered on Thu 05 Mar 2026; not logged by then: quality, competitor" in ev
        assert "ZZ9: a new item ordered on Thu 05 Mar 2026 with no candidate line" in ev
        assert "CA1" not in ev                              # a repeat order of an item received before: no new item
        assert row["actual"] == "1 of 3 new items ordered had all four checks logged by the order day"
        untold = kpi.row_checked(ctx_of(MAR), src_of())
        assert untold["status"] == kpi.NOT_MET and "cannot be told new or repeat" in "\n".join(untold["evidence"])
    with data_root(cfg()):
        logged("candidate", item_code="NW1", check_types="price,quality,demand,competitor", event_date="2026-03-02")
        stock_orders.append(stock.kpi_log_file(), stock_orders.promise_lines(
            "VFAN", date(2026, 3, 5), [("NW1", 10)], "typed", datetime(2026, 3, 5, 10, 0)))
        row = kpi.row_checked(ctx_of(MAR), src_of(stock_world()))
        # the checks were logged on 16 Mar (NOW), after the order of 5 Mar: not before the order
        assert row["status"] == kpi.NOT_MET and "not logged by then: price, quality, demand, competitor" in \
            row["evidence"][0], row
    with data_root(cfg()):
        stock_orders.append(stock.kpi_log_file(), stock_orders.promise_lines(
            "VFAN", date(2026, 3, 5), [("ZZ9", 5)], "typed", datetime(2026, 3, 5, 10, 0)))
        row = kpi.row_checked(ctx_of(MAR), src_of(stock_world()))
        assert row["status"] == kpi.NOT_EVIDENCED and "with no candidate line" in row["evidence"][0], row
    with data_root(cfg()):
        kpi_log.CLOCK = lambda: datetime(2026, 3, 2, 9, 0, tzinfo=BH)
        try:
            logged("candidate", item_code="NW1", check_types="price,quality,demand,competitor", event_date="2026-03-02")
            logged("candidate", item_code=CODES["A"], check_types="price", event_date="2026-03-02")
        finally:
            kpi_log.CLOCK = lambda: NOW
        stock_orders.append(stock.kpi_log_file(), stock_orders.promise_lines(
            "VFAN", date(2026, 3, 5), [("NW1", 10), (CODES["A"], 40)], "typed", datetime(2026, 3, 5, 10, 0)))
        row = kpi.row_checked(ctx_of(MAR), src_of(stock_world()))
        # CA1 was received before: its candidate's order is a repeat, no new item; NW1 had its four checks first
        assert row["status"] == kpi.MET and row["evidence"] == [
            "cand:NW1: ordered on Thu 05 Mar 2026, all four checks logged by then"], row
        assert row["actual"] == "1 of 1 new item ordered had all four checks logged by the order day"
    with data_root(cfg()):
        # the log's first line is the one logged now (16 Mar); an order of 1 Mar could not have had its checks logged
        logged("candidate", item_code="QQ1", state="ordered", event_date="2026-03-01")
        row = kpi.row_checked(ctx_of(MAR), src_of(stock_world()))
        assert row["status"] == kpi.NOT_EVIDENCED and row["evidence"] == [
            "cand:QQ1: ordered on Sun 01 Mar 2026, before the KPI log started"], row


@test("2c launches: the items first received in the 90 days, their first 30 and 60 days: counts under NOTHING DUE")
def _():
    with data_root(cfg()):
        parts = stock_world(more_world())
        row = kpi.row_launches(ctx_of(FEB), src_of(parts))
        assert row["status"] == kpi.NOTHING_DUE and row["value"] == 1, row
        assert row["actual"] == ("1 item first received in the 90 days to Sat 28 Feb 2026: 50 units received; sold in "
                                 "their first 30 days 30 units (1 with that many days behind them), in their first 60 "
                                 "days 0 (0)"), row["actual"]
        assert "50 received Tue 20 Jan 2026, 30 sold in 30 days" in row["evidence"][0], row["evidence"]
        assert kpi.row_launches(ctx_of(FEB), src_of())["status"] == kpi.NOT_EVIDENCED


@test("3a quotations: NOT EVIDENCED until the floor is set; per major receipt two suppliers MET, one NOT MET, none ...")
def _():
    with data_root(cfg()):
        row = kpi.row_quotes(ctx_of(FEB), src_of(stock_world(more_world())))
        assert row["status"] == kpi.NOT_EVIDENCED and "'kpi.major_purchase_bhd' is not set" in row["evidence"][0]
        assert row["source"].endswith("register not found")
    with data_root(cfg({"kpi.major_purchase_bhd": 50, "kpi.supplier_register": "Z:/nowhere/register.xlsx"})):
        parts = stock_world(more_world())
        row = kpi.row_quotes(ctx_of(FEB), src_of(parts))
        assert row["status"] == kpi.NOT_EVIDENCED and row["source"].endswith("register not found"), row
        assert row["evidence"] == ["MRN:T-26-02-1 (Thu 12 Feb 2026, BHD 60.000): no quotation logged for it"], row
        logged("quote", ref="MRN:T-26-02-1", supplier="VFAN", event_date="2026-02-01")
        assert kpi.row_quotes(ctx_of(FEB), src_of(parts))["status"] == kpi.NOT_MET
        logged("quote", ref="T-26-02-1", supplier="Other Co", event_date="2026-02-13")       # after the receipt
        assert kpi.row_quotes(ctx_of(FEB), src_of(parts))["status"] == kpi.NOT_MET
        logged("quote", ref="t 26 02 1", supplier="Second Co", event_date="2026-02-02")
        row = kpi.row_quotes(ctx_of(FEB), src_of(parts))
        assert row["status"] == kpi.MET and "quotations from 2 suppliers (second-co, vfan)" in row["evidence"][0], row
        assert row["actual"] == "1 of 1 major purchase with two or more quotations"
        assert kpi.row_quotes(ctx_of(DEC), src_of(parts))["status"] == kpi.NOTHING_DUE      # no receipt at all
        unvalued = more_world().receipt(L("B"), date(2026, 2, 25), 5, 0, "MRN:T-26-02-3")
        row = kpi.row_quotes(ctx_of(FEB), src_of(stock_world(unvalued)))
        assert row["status"] == kpi.NOT_EVIDENCED and row["evidence"][0] == (
            "MRN:T-26-02-3 (Wed 25 Feb 2026): 5 units on lines that state no value: it cannot be told whether it is a "
            "major purchase"), row
    with data_root(cfg({"kpi.major_purchase_bhd": 50})):
        register = paths.write_text(paths.ops_dir() / "supplier register.xlsx", "x")
        config.use(dict(json.loads(paths.config_file().read_text(encoding="utf-8")),
                        **{"kpi.supplier_register": str(register)}))
        assert kpi.row_quotes(ctx_of(FEB), src_of(stock_world(more_world())))["source"].endswith(
            "the supplier register supplier register.xlsx")


@test("3b terms: every supplier ordered from with a terms line; NOT EVIDENCED without one; price against the last order")
def _():
    po = [{"item_code": "CA1", "vendor": "VFAN", "last_ordered": "2026-02-10", "rate_now": "0.5", "rate_prev": "0.4"},
          {"item_code": "CB2", "vendor": "vfan", "last_ordered": "2026-02-10", "rate_now": "2", "rate_prev": "2"}]
    with data_root(cfg({"stock.supplier_aliases": {"VFAN Trading": "VFAN"}})):
        assert kpi.row_terms(ctx_of(FEB), src_of())["status"] == kpi.NOTHING_DUE
        assert kpi.row_terms(ctx_of(FEB), src_of(po=RuntimeError("x")))["status"] == kpi.NOT_EVIDENCED
        row = kpi.row_terms(ctx_of(FEB), src_of(po=po))
        assert row["status"] == kpi.NOT_EVIDENCED and "VFAN: no terms line on file" in row["evidence"], row
        assert "price against the last order: 1 dearer, 0 cheaper, 1 the same (2 codes ordered in February 2026)" in \
            row["evidence"], row["evidence"]
        logged("terms", supplier="VFAN Trading", note="thirty days credit", event_date="2026-02-05")
        row = kpi.row_terms(ctx_of(FEB), src_of(po=po))
        assert row["status"] == kpi.MET and row["actual"].startswith("terms on file for 1 of 1 supplier"), row
        stock_orders.append(stock.kpi_log_file(), stock_orders.promise_lines(
            "Second Co", date(2026, 2, 8), [("CA1", 10)], "typed", datetime(2026, 2, 8, 10, 0)))
        assert kpi.row_terms(ctx_of(FEB), src_of(po=po))["status"] == kpi.NOT_EVIDENCED


@test("3c delivery: each receipt by the day promised (a quote or terms line with a due date), its lead time beside it")
def _():
    with data_root(cfg()):
        parts = stock_world(more_world())
        assert kpi.row_delivery(ctx_of(DEC), src_of(stock_world()))["status"] == kpi.NOTHING_DUE
        row = kpi.row_delivery(ctx_of(FEB), src_of(parts))
        assert row["status"] == kpi.NOT_EVIDENCED and len(row["evidence"]) == 2, row
        logged("quote", ref="T-26-02-1", supplier="VFAN", due_date="2026-02-15", event_date="2026-01-10")
        logged("terms", ref="T-26-02-2", supplier="VFAN", note="delivery promised", due_date="2026-02-18",
               event_date="2026-01-10")
        row = kpi.row_delivery(ctx_of(FEB), src_of(parts))
        assert row["status"] == kpi.NOT_MET, row
        assert "MRN:T-26-02-1 received Thu 12 Feb 2026, by the day promised (Sun 15 Feb 2026)" in row["evidence"][0]
        assert "MRN:T-26-02-2 received Fri 20 Feb 2026, 2 days after the day promised" in row["evidence"][1]
        logged("terms", ref="T-26-02-2", supplier="VFAN", note="delivery promised", due_date="2026-02-25",
               event_date="2026-01-12")
        assert kpi.row_delivery(ctx_of(FEB), src_of(parts))["status"] == kpi.MET
        lead = kpi.row_delivery(ctx_of(MAR), src_of(parts))
        assert "days from supplier invoice INV-5 (VFAN)" in lead["evidence"][0], lead["evidence"]


@test("4a incoming check: counted in full with quantity, quality and packaging MET; short or missing NOT MET; none ...")
def _():
    with data_root(cfg()):
        parts = stock_world(more_world())
        assert kpi.row_incoming(ctx_of(DEC), src_of(stock_world()))["status"] == kpi.NOTHING_DUE
        row = kpi.row_incoming(ctx_of(FEB), src_of(parts))
        assert row["status"] == kpi.NOT_EVIDENCED, row
        logged("receipt_check", ref="MRN:T-26-02-1", qty="20", check_types="quantity,quality,packaging",
               event_date="2026-02-12")
        logged("receipt_check", ref="T-26-02-2", qty="8", check_types="quantity", event_date="2026-02-20")
        row = kpi.row_incoming(ctx_of(FEB), src_of(parts))
        assert row["status"] == kpi.NOT_MET, row
        assert row["evidence"][1] == ("MRN:T-26-02-2 (Fri 20 Feb 2026): 8 of 10 units counted; not recorded: quality, "
                                      "packaging"), row["evidence"]
        assert row["actual"] == "28 of 30 units counted (93.3%); 1 of 2 receipts checked in full"
        logged("receipt_check", ref="T-26-02-2", qty="10", check_types="quality,packaging", event_date="2026-02-21")
        row = kpi.row_incoming(ctx_of(FEB), src_of(parts))
        assert row["status"] == kpi.MET and row["value"] == Decimal("1.000"), row


@test("4b damage and claims: settled by the due day MET, late or overdue NOT MET, damage with no claim NOT EVIDENCED")
def _():
    with data_root(cfg()):
        assert kpi.row_damage(ctx_of(FEB), src_of(stock_world()))["status"] == kpi.NOTHING_DUE      # nothing went
        parts = stock_world(more_world())
        row = kpi.row_damage(ctx_of(FEB), src_of(parts))
        assert row["status"] == kpi.NOT_EVIDENCED and row["value"] == 3, row
        assert "3 units went to the damage store in February 2026 and no claim is logged" in row["evidence"]
        logged("claim", ref="T-26-02-1", supplier="VFAN", due_date="2026-02-20", event_date="2026-02-11")
        row = kpi.row_damage(ctx_of(FEB), src_of(parts))
        assert row["status"] == kpi.NOT_MET and "still open, due Fri 20 Feb 2026" in row["evidence"][0], row
        logged("claim", ref="T-26-02-1", supplier="VFAN", state="closed", event_date="2026-02-19")
        row = kpi.row_damage(ctx_of(FEB), src_of(parts))
        assert row["status"] == kpi.MET and row["evidence"][0].startswith("claim:vfan:T-26-02-1: closed on Thu 19 Feb"), row
        logged("claim", ref="cables", supplier="VFAN", due_date="2026-03-30", event_date="2026-03-02")
        mar = kpi.row_damage(ctx_of(MAR), src_of(parts))
        assert mar["status"] == kpi.NOTHING_DUE and "not due yet" in mar["actual"], mar
        logged("claim", ref="late", supplier="VFAN", due_date="2026-02-25", event_date="2026-02-02")
        logged("claim", ref="late", supplier="VFAN", state="closed", closed_date="2026-03-01", event_date="2026-03-01")
        late = kpi.row_damage(ctx_of(FEB), src_of(parts))
        assert late["status"] == kpi.NOT_MET and "(4 days late)" in "\n".join(late["evidence"]), late
        logged("claim", ref="no due", supplier="VFAN", event_date="2026-01-12")
        assert kpi.row_damage(ctx_of(JAN), src_of(parts))["status"] == kpi.NOT_EVIDENCED
        assert kpi.row_damage(ctx_of(JAN), src_of())["status"] == kpi.NOT_EVIDENCED


@test("5a sales: against the KPI step; NOTHING DUE below it while the month is not over; growth and last year beside")
def _():
    months = {"2025-02": {"net": Decimal("80"), "invoices": 4}, "2026-02": {"net": Decimal("120"), "invoices": 7},
              "2026-01": {"net": Decimal("90"), "invoices": 5}, "2025-12": {"net": Decimal("60"), "invoices": 3},
              "2025-11": {"net": Decimal("50"), "invoices": 3}, "2025-10": {"net": Decimal("50"), "invoices": 2},
              "2025-09": {"net": Decimal("40"), "invoices": 2}, "2026-03": {"net": Decimal("70"), "invoices": 4}}
    with data_root(cfg()):
        row = kpi.row_sales(ctx_of(FEB), src_of(months=months))
        assert row["status"] == kpi.MET and row["actual"] == ("BHD 120.000 against the step of BHD 100.000 "
                                                              "(120.0%)"), row
        assert row["evidence"][1] == ("the 3 months to February 2026: BHD 270.000, +93% on the 3 before (BHD "
                                      "140.000)"), row["evidence"]
        assert row["evidence"][2] == "February 2025: BHD 80.000 (+50% a year on)"
        assert kpi.row_sales(ctx_of(MAR), src_of(months=months))["status"] == kpi.NOTHING_DUE        # not over
        whole = kpi.row_sales(ctx_of(MAR, partial=False), src_of(months=months))
        assert whole["status"] == kpi.NOT_MET
        assert kpi.row_sales(ctx_of(FEB), src_of(months=RuntimeError("x")))["status"] == kpi.NOT_EVIDENCED
        young = src_of(months=months, anchors={"focus_to": D.isoformat(), "focus_from": "2025-06-01"})
        assert "the sales data does not reach it (it starts on Sun 01 Jun 2025)" in \
            kpi.row_sales(ctx_of(FEB), young)["evidence"][2]
    with data_root({"sales.kpi_steps": []}):
        row = kpi.row_sales(ctx_of(FEB), src_of(months=months))
        assert row["status"] == kpi.NOT_EVIDENCED and "the KPI step is not set" in row["actual"]
    with data_root({"sales.kpi_steps": [{"from": "2026-01", "bhd": "NaN"}]}):
        row = kpi.row_sales(ctx_of(FEB), src_of(months=months))
        assert row["status"] == kpi.NOT_EVIDENCED and "sales.kpi_steps" in row["evidence"][0], row


@test("5b SKU productivity: the best sellers against the rest, margins at landed cost; no target: NOTHING DUE")
def _():
    skus = [{"code": CODES["A"], "net_bhd": "40", "units": "40"}, {"code": CODES["B"], "net_bhd": "30", "units": "10"}]
    skus += [{"code": f"X{n}", "net_bhd": "1", "units": "1"} for n in range(25)]
    with data_root(cfg()):
        row = kpi.row_productivity(ctx_of(FEB), src_of(stock_world(), skus=skus))
        assert row["status"] == kpi.NOTHING_DUE and row["actual"].startswith("the 20 best sellers: "), row
        assert "the other 7: margin n/a" in row["actual"] and "25 items without a landed cost" in row["evidence"][1]
        assert kpi.row_productivity(ctx_of(FEB), src_of(skus=RuntimeError("x")))["status"] == kpi.NOT_EVIDENCED
        bare = kpi.row_productivity(ctx_of(FEB), src_of(skus=skus))
        assert bare["status"] == kpi.NOTHING_DUE and "no margin is worked out" in bare["evidence"][1]


@test("7 reports on time: due in the month; built by the due day MET, late or missing NOT MET, before the first ...")
def _():
    with data_root(cfg()):
        row = kpi.row_reports(ctx_of(FEB), src_of())
        assert row["status"] == kpi.NOT_EVIDENCED and len(row["evidence"]) == 5, row     # 4 weeks and January's report
        assert row["target"] == "weekly by the Sunday after its week; monthly (final) by working day 5"
        assert row["evidence"][0] == ("the week ending Sat 31 Jan 2026: due Sun 01 Feb 2026, no build record; due before "
                                      "the first weekly workbook was built")
        assert row["evidence"][4].startswith("the January 2026 report: due Thu 05 Feb 2026, no final file")
        for sat, built in (("2026-01-31", "2026-02-01T09:00:00+03:00"), ("2026-02-07", "2026-02-08T09:00:00+03:00"),
                           ("2026-02-14", "2026-02-16T09:00:00+03:00")):
            record("week", sat, built, folder=sat)
        record("month", JAN, "2026-02-04T09:00:00+03:00", folder="2026-02-01", draft=False)
        row = kpi.row_reports(ctx_of(FEB), src_of())
        ev = "\n".join(row["evidence"])
        assert row["status"] == kpi.NOT_MET, row
        assert "the week ending Sat 14 Feb 2026: due Sun 15 Feb 2026, built Mon 16 Feb 2026 (1 day late)" in ev
        assert "the week ending Sat 21 Feb 2026: due Sun 22 Feb 2026, no build record by then" in ev
        assert "the January 2026 report: due Thu 05 Feb 2026, built Wed 04 Feb 2026" in ev
        assert row["actual"] == "3 of 5 reports due were prepared on time"
        logged("sent", "weekly", "2026-01-31")
        assert "; sent (logged)" in kpi.row_reports(ctx_of(FEB), src_of())["evidence"][0]
        early = kpi.row_reports(ctx_of(MAR, partial=True, today=date(2026, 2, 28)), src_of())
        assert early["status"] == kpi.NOTHING_DUE and "due later than today" in early["evidence"][0], early
    with data_root(cfg()):
        for sat, built in (("2026-01-31", "2026-02-01T09:00"), ("2026-02-07", "2026-02-07T18:00"),
                           ("2026-02-14", "2026-02-15T08:00"), ("2026-02-21", "2026-02-22T07:00")):
            record("week", sat, built + ":00+03:00", folder=sat)
        record("month", JAN, "2026-02-05T16:00:00+03:00", folder="2026-02-01", draft=False)
        row = kpi.row_reports(ctx_of(FEB), src_of())
        assert row["status"] == kpi.MET and row["actual"] == "5 of 5 reports due were prepared on time", row
    with data_root(cfg({"kpi.weekly_due_weekday": "Monday", "kpi.monthly_due_working_day": 2,
                        "kpi.working_days": ["Sunday"]})):
        record("month", FEB, "2026-03-02T09:00:00+03:00", folder="2026-03-01", draft=True)     # a DRAFT only
        record("month", JAN, "2026-02-03T09:00:00+03:00", folder="2026-02-01", draft=False)
        ctx = ctx_of(MAR, partial=True, today=date(2026, 3, 16))
        row = kpi.row_reports(ctx, src_of())
        assert not ctx.used and row["target"] == "weekly by the Monday after its week; monthly (final) by working day 2"
        assert "the February 2026 report: due Sun 08 Mar 2026, no final file (a DRAFT only) by then" in \
            "\n".join(row["evidence"]), row["evidence"]
        assert kpi._working_day(date(2026, 3, 1), 2, {6}) == date(2026, 3, 8)


@test("the plan block: each target beside its actual; unknown and unreadable targets NOT EVIDENCED; none set")
def _():
    months = {FEB: {"net": Decimal("120"), "invoices": 7}}
    plan = {FEB: {"Sales": 100, "invoices": 9, "shops buying": 3, "regular_shops": 2, "rep_days": 5,
                  "availability": 95, "cover_days": [80, 100], "new_items": 1, "stockists": 1, "sales_bhd": "lots"}}
    with data_root(cfg({"kpi.plan_targets": plan, "kpi.shop_min_bhd": 10})):
        src = src_of(stock_world(), months=months, shops={"shops": 4, "regular": 1}, rep_days=6)
        card = kpi.scorecard(FEB, sources=src)
        got = {(p["name"], p["target"]): (p["actual"], p["status"]) for p in card["plan"]["rows"]}
        assert got[("Sales", "BHD 100.000")] == ("BHD 120.000", kpi.MET), got
        assert got[("Invoices", "9")] == ("7", kpi.NOT_MET)
        assert got[("Shops buying", "3")] == ("4", kpi.MET)
        assert got[("Regular shops (each of the three months)", "2")] == ("1", kpi.NOT_MET)
        assert got[("Selling rep-days", "5")] == ("6", kpi.MET)
        assert got[("Fast-mover availability", "95.0%")] == ("50.0%", kpi.NOT_MET)
        cover = next(p for p in card["plan"]["rows"] if p["name"] == "Stock cover (days)")
        assert cover["target"] == "80 to 100" and cover["status"] == kpi.MET, cover
        assert got[("New items", "1")][1] == kpi.NOT_MET                 # none: the portal and the log name nothing
        assert got[("stockists", "1")] == ("n/a", kpi.NOT_EVIDENCED)
        assert got[("Sales", "lots")] == ("n/a", kpi.NOT_EVIDENCED)
        assert card["plan"]["counts"] == {kpi.MET: 4, kpi.NOT_MET: 4, kpi.NOT_EVIDENCED: 2, kpi.NOTHING_DUE: 0}
        assert src.shop_min == Decimal("10") and "kpi.shop_min_bhd" not in card["defaults_used"]
        text = "\n".join(kpi.render(card))
        assert "90-DAY PLAN  February 2026: 4 MET, 4 NOT MET, 2 NOT EVIDENCED, 0 NOTHING DUE" in text
        assert "- Shops buying: v_sales: named shops buying BHD 10.000 or more in a month" in text
    with data_root(cfg({"kpi.plan_targets": {MAR: {"sales": 100, "cover": 50}}})):
        card = kpi.scorecard(MAR, sources=src_of(stock_world(), months={MAR: {"net": Decimal("70"), "invoices": 3}}))
        rows = {p["name"]: p for p in card["plan"]["rows"]}
        assert rows["Sales"]["status"] == kpi.NOTHING_DUE                # not over, below the floor
        assert rows["Stock cover (days)"]["target"] == "at most 50"
    with data_root(cfg()):
        card = kpi.scorecard(FEB, sources=src_of(months=months))
        assert not card["plan"]["rows"] and "no 90-day plan targets are set for February 2026" in card["plan"]["note"]
        assert "  no 90-day plan targets are set for February 2026 (config 'kpi.plan_targets')." in kpi.render(card)


@test("the header counts each status; the log marker; a MET with nothing named is not a MET; a failing row stands aside")
def _():
    with data_root(cfg()):
        card = kpi.scorecard(FEB, sources=src_of(stock_world(more_world())))
        assert sum(card["counts"].values()) == len(card["rows"]) == len(kpi.ROWS) == 14
        assert [r["key"] for r in card["rows"]] == list(kpi.ROW_NAMES)
        for s in kpi.STATUSES:
            assert card["counts"][s] == sum(1 for r in card["rows"] if r["status"] == s)
        every_met_named(card)
        text = kpi.render(card)
        assert text[0] == "KPI SCORECARD  February 2026 (data to 15 March 2026)"
        assert text[1] == "  " + ", ".join(f"{card['counts'][s]} {s}" for s in kpi.STATUSES)
        assert text[2].startswith("  The KPI log has no line yet")
        logged("candidate", ref="x", event_date="2026-02-03")
        card = kpi.scorecard(MAR, sources=src_of(stock_world()))
        assert card["log"]["marker"] == "log started Mon 16 Mar 2026" and card["partial"]
        assert kpi.render(card)[0] == "KPI SCORECARD  March 2026 (month to date, data to 15 March 2026)"
        assert kpi.render(card)[2] == "  The KPI log: log started Mon 16 Mar 2026."
        card = kpi.scorecard(FEB, sources=src_of(stock_world()))
        assert card["log"]["marker"].startswith("The KPI log started on Mon 16 Mar 2026, after February 2026")
        bare = kpi._row("9z", "x", "y", kpi.MET, "z", [], "s")
        assert bare["status"] == kpi.NOT_EVIDENCED and bare["evidence"] == ["no evidence could be named for it"]

        def broken(ctx, src):
            raise ZeroDivisionError("boom")
        row = kpi._safe(broken, "5b", ctx_of(FEB), src_of())
        assert row["status"] == kpi.NOT_EVIDENCED and row["kpi"] == "SKU productivity"
        assert row["evidence"] == ["it could not be worked out this time (ZeroDivisionError)"]


# ── the command ───────────────────────────────────────────────────────────────

class KpiWorld(World):
    """The stock world, and the scorecard's own statements answered from invented figures."""

    def __init__(self, book=None, anchors=None, months=None, **kw):
        super().__init__(book, **kw)
        self.anchors = anchors or {"focus_to": D.isoformat(), "focus_from": "2025-02-01", "uploaded_at": None}
        self.months = months or {FEB: ("120", 7)}

    def rpc(self, sql):
        if sql == kpi.ANCHOR_SQL:
            db.check_sql(sql)
            self.sql.append(sql)
            return [dict(self.anchors)]
        return super().rpc(sql)

    def rpc_params(self, sql, params):
        db.check_sql(sql)
        self.sql.append(sql)
        assert all(isinstance(p, str) for p in params), params
        if sql == kpi.MONTHS_SQL:
            return [{"m": m, "net_bhd": v, "invoices": n} for m, (v, n) in self.months.items()]
        if sql == kpi.SKU_SQL:
            return [{"code": CODES["A"], "item": "ITEM-A", "net_bhd": "40", "units": "40"}]
        if sql == kpi.PO_SQL:
            return []
        if sql == kpi.SHOPS_SQL:
            return [{"shops": 3, "regular": 1}]
        if sql == kpi.REP_DAYS_SQL:
            return [{"rep_days": 6}]
        raise AssertionError(f"a statement the stand-in does not know: {sql[:70]}")

    def session(self, **kw):
        world_ = self
        inner = super().session(**kw)

        class Session:
            def q(self, sql, params=None):
                if sql in (kpi.MARKET_SQL, kpi.FINDS_SQL, kpi.UPCOMING_SQL):
                    db.check_sql(sql)
                    world_.sql.append(sql)
                    return []
                return inner.q(sql, params)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return Session()


class standing:
    def __init__(self, w: KpiWorld):
        self.w = w

    def __enter__(self):
        self.saved = (db.rpc, db.rpc_params, db.session, ledger.for_audit)
        db.rpc, db.rpc_params, db.session, ledger.for_audit = self.w.rpc, self.w.rpc_params, self.w.session, \
            self.w.for_audit
        return self.w

    def __exit__(self, *exc):
        db.rpc, db.rpc_params, db.session, ledger.for_audit = self.saved
        return False


@test("the command: one screen, its facts under exports/ops/<D>/, every statement a read, no long digit run")
def _():
    with data_root(cfg()) as root:
        w = KpiWorld()
        with standing(w):
            out = kpi.run(Args(FEB))
        assert out["ok"] and out["data_date"] == D.isoformat(), out
        assert out["flags"]["month"] == FEB and sum(out["flags"]["counts"].values()) == 14
        files = [Path(f) for f in out["files"]]
        assert files == [root / "exports" / "ops" / D.isoformat() / f"kpi-{FEB}.txt",
                         root / "exports" / "ops" / D.isoformat() / f"kpi-{FEB}.json"], files
        assert files[0].read_text(encoding="utf-8") == out["text"] + "\n"
        facts = json.loads(files[1].read_text(encoding="utf-8"))
        assert facts["month"] == FEB and facts["counts"] == out["flags"]["counts"] and len(facts["rows"]) == 14
        assert not NINE.search(out["text"]) and not LONG.search(out["text"])
        assert set(w.sql) >= {kpi.ANCHOR_SQL, kpi.MONTHS_SQL, kpi.SKU_SQL, kpi.PO_SQL, kpi.MARKET_SQL, kpi.FINDS_SQL,
                              kpi.UPCOMING_SQL}, sorted(s[:30] for s in w.sql)
        for sql in w.sql:
            db.check_sql(sql)
        assert "5a Monthly sales: MET" in out["text"] and "Not read this time" not in out["text"], out["text"]
        for bad, needle in ((Args("2026-13"), "is not one"), (Args("2026-04"), "No data is loaded for 2026-04 yet"),
                            (Args(FEB, MAR), "Say one month"), (Args(FEB, note="x"), "belong to `kpi log`")):
            with standing(KpiWorld()):
                got = kpi.run(bad)
            assert got.get("exit") == 1 and needle in got["text"], (needle, got["text"])


@test("the default month: the data date's month once it is whole (a load in the month after), else the month before")
def _():
    with data_root(cfg()):
        with standing(KpiWorld()):
            assert kpi.run(Args())["flags"]["month"] == FEB         # mid-March: February is the last whole month
        anchors = {"focus_to": "2026-03-31", "focus_from": "2025-02-01", "uploaded_at": "2026-04-01T08:00:00+03:00"}
        src = src_of(stock_world(), anchors=anchors, today=date(2026, 4, 1))
        assert kpi.scorecard(sources=src)["month"] == MAR
        src = src_of(stock_world(), anchors=dict(anchors, uploaded_at="2026-03-31T20:00:00+03:00"), today=date(2026, 4, 1))
        assert kpi.scorecard(sources=src)["month"] == FEB            # no load since the month ended: not whole yet
        src = src_of(stock_world(), anchors=RuntimeError("down"))
        card = kpi.scorecard(FEB, sources=src)
        assert card["data_date"] == D and "the stock ledger's last day (15 March 2026) stands in" in card["notes"][0]
        assert "the data dates (v_sales) (RuntimeError)" in card["unread"]


# ── the workbook sheets ───────────────────────────────────────────────────────

def _value(ws, ref: str):
    r, c = fx.parse_cell(ref)
    return ws.cells[(r, c)].value


@test("the KPI sheet: the COUNTIF header equals the card's counts, from the card and from its JSON; typed; no evidence")
def _():
    plan = {FEB: {"sales": 100, "invoices": 9}}
    with data_root(cfg({"kpi.plan_targets": plan})) as root:
        card = kpi.scorecard(FEB, sources=src_of(stock_world(more_world()),
                                                 months={FEB: {"net": Decimal("120"), "invoices": 7}}))
        for given in (card, json.loads(json.dumps(kpi.jsonable(card)))):
            book = Workbook()
            ws = book.sheet("KPI")
            where = sheets_kpi.kpi_sheet(ws, given)
            assert book.evaluate() == []
            for s, ref in where["counts"].items():
                assert _value(ws, ref) == card["counts"][s], (s, ref)
                r, c = fx.parse_cell(ref)
                assert ws.cells[(r, c)].formula == f'COUNTIF($F${where["first_row"] + 1}:$F${where["last_row"] + 1},"{s}")'
            for s, ref in where["plan_counts"].items():
                assert _value(ws, ref) == card["plan"]["counts"][s]
            texts = [cell.value for cell in ws.cells.values() if cell.kind == "s"]
            for r in card["rows"]:
                for e in r["evidence"]:
                    assert e not in texts, e                 # the evidence stays on the screen
            first = where["first_row"]
            assert ws.cells[(first, 3)].kind == "n" and ws.cells[(first, 3)].value == float(card["rows"][0]["value"])
            assert ws.cells[(first, 5)].value == card["rows"][0]["status"]
            book.save(root / "exports" / f"kpi-sheet-{len(texts)}-{id(given)}.xlsx")
        assert sheets_kpi.STATUSES == kpi.STATUSES


@test("the KPI log sheet: per kind its lines, refs and states, the totals a SUM; no ref, note or name on it")
def _():
    with data_root(cfg()) as root:
        logged("candidate", ref="TWS x1", check_types="price", note="private words", event_date="2026-02-03")
        logged("candidate", ref="TWS x1", check_types="demand", event_date="2026-02-04")
        logged("quote", ref="T-26-02-1", supplier="VFAN", event_date="2026-03-02")
        stock_orders.append(stock.kpi_log_file(), stock_orders.promise_lines(
            "VFAN", date(2026, 2, 8), [("CA1", 10)], "typed", datetime(2026, 2, 8, 10, 0)))
        lines = kpi_log.month_lines(FEB)
        assert [r["kind"] for r in lines] == ["candidate", "candidate", "promise"]
        book = Workbook()
        ws = book.sheet("KPI log")
        where = sheets_kpi.kpi_log_sheet(ws, lines, title="KPI log - February 2026")
        assert book.evaluate() == []
        total = where["total_row"]
        assert ws.cells[(total, 1)].value == 3 and ws.cells[(total, 2)].value == 2
        row = {ws.cells[(r, 0)].value: r for r in range(where["first_row"], where["last_row"] + 1)}
        cand = row["Products assessed (candidates)"]
        assert ws.cells[(cand, 1)].value == 2 and ws.cells[(cand, 2)].value == 1 and ws.cells[(cand, 3)].value == "open 1"
        assert ws.cells[(row["Quotations"], 1)].value == 0
        texts = " ".join(str(c.value) for c in ws.cells.values() if c.kind == "s")
        for private in ("private words", "TWS", "VFAN", "cand:", "CA1"):
            assert private not in texts, private
        book.save(root / "exports" / "kpi-log-sheet.xlsx")
        raises(ValueError, lambda: kpi_log.month_lines("Feb"))


# ── the modules and the skill ─────────────────────────────────────────────────

@test("the modules: reads only through scripts.ops.db, the log only through locked_append, the screen through save_screen")
def _():
    ops = ROOT / "scripts" / "ops"
    for name in ("kpi.py", "kpi_log.py", "workbook/sheets_kpi.py"):
        src = (ops / name).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for word in ("import psycopg", "import httpx", "supabase", "subprocess", "yq_load_go", ".table(", "upsert",
                     "write_text", "append_lines", "open("):
            assert word not in src, (name, word)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        for c in calls:
            if c.func.attr in ("rpc", "rpc_params", "session"):
                assert isinstance(c.func.value, ast.Name) and c.func.value.id == "db", (name, c.func.attr)
    log_src = (ops / "kpi_log.py").read_text(encoding="utf-8")
    assert log_src.count("stock_orders.locked_append(") == 1 and "db." not in log_src
    kpi_src = (ops / "kpi.py").read_text(encoding="utf-8")
    assert "sales.save_screen(" in kpi_src and kpi_src.index("from scripts.ops import db") < kpi_src.index(
        "from scripts.ops import config")
    from scripts.ops import sales
    assert kpi.BASIS_SQL == sales.BASIS_SQL
    for sql in (kpi.ANCHOR_SQL, kpi.MONTHS_SQL, kpi.SKU_SQL, kpi.SHOPS_SQL, kpi.REP_DAYS_SQL, kpi.PO_SQL,
                kpi.MARKET_SQL, kpi.FINDS_SQL, kpi.UPCOMING_SQL):
        db.check_sql(sql)
        assert ";" not in sql and " AS t " not in sql and " as t " not in sql
    assert cli.COMMANDS["kpi"][0] == "scripts.ops.kpi"
    assert kpi_log.KINDS["candidate"].checks == ("price", "quality", "demand", "competitor")
    assert kpi_log.KINDS["receipt_check"].checks == ("quantity", "quality", "packaging")
    assert kpi_log.KEYS == ("kind", "ref", "id", "logged_at", "event_date", "state", "source", "note", "check_types",
                            "supplier", "item_code", "qty", "amount_bhd", "due_date", "closed_date")


DESIGN = {"12", "30", "60", "70", "600000", "90", "180", "120"}      # tests/test_ops_skills.py DESIGN_NUMBERS


@test("the skill: frontmatter, the kpi and status commands in both shells, $ARGUMENTS once, no token typed, no figure")
def _():
    import yaml
    text = (ROOT / ".claude" / "skills" / "yq-kpi" / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n") and len(text.splitlines()) < 70
    head, body = text[4:].split("\n---\n", 1)
    fm = yaml.safe_load(head)
    assert set(fm) == {"name", "description", "argument-hint", "allowed-tools"} and fm["name"] == "yq-kpi"
    assert fm["allowed-tools"] == ["Bash(python -m scripts.ops kpi *)", "PowerShell(python -m scripts.ops kpi *)",
                                   "Bash(python -m scripts.ops status *)", "PowerShell(python -m scripts.ops status *)"]
    assert text.count("$ARGUMENTS") == 1 and "python -m scripts.ops kpi $ARGUMENTS" in body
    assert "--token" not in text and "never type or change its token yourself" in body
    assert "only after he says yes" in body and "Never accuse anyone" in body and "type no number yourself" in body
    for command in re.findall(r"`(python [^`]*)`", body):
        assert command.startswith(("python -m scripts.ops kpi", "python -m scripts.ops status")), command
    for token in set(re.findall(r"\d[\d,.]*\d|\d", text)):
        assert len(token) == 1 or token in DESIGN, token
    for kind in ("candidate", "quote", "terms", "receipt_check", "claim"):
        assert f"`{kind}`" in body and kind in kpi_log.KINDS


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
        finally:
            kpi_log.CLOCK = lambda: NOW
    paths.set_data_root(None)
    config.use(None)
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
