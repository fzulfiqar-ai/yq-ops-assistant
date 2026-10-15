"""Ops engine: the sales brief (python -m scripts.ops brief), on a synthetic world.

    python -m tests.test_ops_sales

Same lightweight runner as the other suites (no pytest). No database, no network, no .env:
SUPABASE_URL, SUPABASE_KEY and DATABASE_URL are pinned to ci.invalid BEFORE any import and
scripts.ops.db is imported first. db.rpc and db.rpc_params are replaced by a stand-in that answers
every statement the brief sends from an in-memory list of invented sale lines (two vans, a few
shops, round amounts), and the Command Centre call by a canned payload in its real shape. One test
runs the whole command through the guarded client against an in-memory transport instead, with the
real app.metrics.overview. Every folder is a temporary one.

The steady world: from Wed 1 Oct 2025, on every business day (Sunday to Thursday), Van A sells
20.000 to Shop 1 and Van B sells 10.000 to Shop 2. So a business day is 30.000 and a full week is
150.000, which makes every expected figure below a sum a reader can redo by hand.

Covered:
  * the window grammar (every form; top:N as a word; a bad one, a month in the year 0000 among
    them, refused in a plain sentence, before any read); words and options in any order;
  * the anchor is the last loaded sale day, never today; a weekend day is said so; a part day (the
    export day) is said so, the latest day is the loaded sale day before it and the 3-day, 7-day
    and full-week lines stop at the calendar day before it (a Sunday export shows the week that
    ended on the Saturday, a Saturday export the week before); a load on record that brought an
    older day makes no part day; the 30 days of the average always run to the data date;
  * complete days: the days before the export day are over whether or not they sold, so a
    Sunday-morning export with no sale since Thursday still shows the week that ended on the
    Saturday (by the stock date, or by the folder of the load when there is no stock date), and
    a week whose Saturday is the export day is not over; the screen says where the lines end;
  * latest day and last 3 days carry no percentage and sit beside the 30-day average business day
    (the Command Centre's daily tile when the payload has one, else the same rule); a bulk order is
    one at or above the configured value;
  * last 7 days and the last full week beside the average of the four weeks just before (not older
    ones, not the week itself; for one salesman too): inside, above and below the normal range;
    "not above" with its one line when the range starts at nought; a week holding one of the first
    or the last five business days of a month is a month-end week and is not judged;
  * month to date against the same business days last month; no percentage while the month has had
    no business day; SIM, Devices and giveaway lines never count; B2B and B2C; accounts invoiced
    without Cash Customer;
  * pace with a KPI step, with a portal target that differs, with the step reached, on the month's
    last business day, and "KPI step not set" with the four figures exactly once; a step of nought
    is no step (the step before it stays), a figure typed with its comma is read, an entry that
    cannot be read stops the command in the config reader's one plain sentence;
  * accounts that have not come back (never Cash Customer); the rep lines, each at its boundary,
    with the Command Centre's own rule for a silent rep (more than N business days), and a line
    to check if the two counts ever differ; rep:<name>;
  * DO TODAY: attention items on the register by tone, never a baseline, with their count and
    money, at most five listed, the older-items line; evidence is order numbers of waiting orders
    only, never a name, every one kept and two shown; the row that reads the profitability report
    says its basis, the report's date and since when it was held back; nothing recorded or
    cleared when a source of the list did not answer, and that brief does not count as read;
    rule 'attn' is recorded in one place only; an explained item stays off while it is what he
    explained and comes back when it is clearly larger (count or money) or names a new order; a
    done item rests until its facts change;
  * two lines when there is no new data; files only under exports/ and business_data/; the state;
    money to the fils; the wording guard; no long digit run; the statements themselves; the
    brief still prints under the banner of a load that failed;
  * no pointer to a command that is not built: what the brief, the register's lines, the e-mail, the
    notes and the skill tell a reader to type is a command or a skill that exists (the commands
    scripts/ops/__main__.py lists, the skills in .claude/skills), and what is left over when the
    register has more open items than a line holds is a count, never a pointer.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import traceback
from contextlib import redirect_stdout
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"
os.environ.pop("YQ_MAIN_CHECKOUT", None)

import httpx  # noqa: E402

from scripts.ops import db  # noqa: E402  (first, as every command does)
from scripts.ops import __main__ as cli  # noqa: E402
from scripts.ops import actions, alerts, config, fmt, paths, render, sales, status  # noqa: E402

from app import metrics, reports  # noqa: E402

TESTS: list[tuple[str, object]] = []

START = date(2025, 10, 1)            # a Wednesday
ANCHOR = date(2026, 1, 20)           # a Tuesday in the middle of a month
VAN_A, VAN_B = "Van A - Acc WH", "Van B - Acc WH"
FIXTURE = {
    "sales.kpi_steps": [],
    "brief.rep_share_min": 0.5, "brief.silent_business_days": 5, "brief.bulk_invoice_bhd": 100,
}


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


def raises(kind, fn) -> Exception:
    try:
        fn()
    except kind as exc:
        return exc
    raise AssertionError(f"expected {kind.__name__}")


def q3(x) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def business_day(d: date) -> bool:
    return d.weekday() not in (4, 5)                 # Friday and Saturday are the weekend


def sunday_of(d: date) -> date:
    return d - timedelta(days=(d.weekday() + 1) % 7)


# ── the synthetic world behind db.rpc / db.rpc_params ───────────────────────────────────────────

class World:
    """Invented sale lines and the SQL the brief sends, worked out in plain Python."""

    def __init__(self, to: date = ANCHOR, steady: bool = True, stock_as_of: date | None = None,
                 no_stock: bool = False):
        self.lines: list[dict] = []
        self.stock_as_of = stock_as_of                 # None: the snapshot is as on the day after the last sale
        self.no_stock = no_stock                       # True: no stock snapshot is loaded at all
        self.portal_target: str | None = "800.0"
        self.targets = [("Van A", "", 100, 200, 300), ("Van B", "", 50, 100, 150), ("Van B", "2026-01", 60, 120, 180)]
        self.calls: list[tuple[str, list | None]] = []
        self.fail: dict[str, Exception] = {}
        self.hide_in_dim: str | None = None            # a salesman the per-salesman read loses (a broken read)
        if steady:
            self.steady(START, to)

    def sale(self, day: date, rep: str, account: str, item: str, net, qty=1, channel: str = "B2B",
             division: str = "Accessories", giveaway: bool = False, invoice: str | None = None) -> "World":
        n = len(self.lines) + 1
        self.lines.append({"line_id": n, "day": day, "invoice": invoice or f"INV-{n:05d}", "rep": rep, "account": account,
                           "item": item, "net": Decimal(str(net)), "qty": Decimal(str(qty)), "channel": channel,
                           "division": division, "giveaway": giveaway,
                           "cash": account.lower().startswith("cash customer")})
        return self

    def steady(self, start: date, end: date) -> "World":
        d = start
        while d <= end:
            if business_day(d):
                self.sale(d, VAN_A, "Shop 1", "ITEM-01 Cable", 20, qty=2)
                self.sale(d, VAN_B, "Shop 2", "ITEM-02 Charger", 10, qty=1)
            d += timedelta(days=1)
        return self

    def drop(self, pred) -> "World":
        self.lines = [ln for ln in self.lines if not pred(ln)]
        return self

    # the basis and the salesman rule, as the statements spell them
    @staticmethod
    def _basis(ln: dict) -> bool:
        return ln["division"] not in ("SIM", "Devices") and not ln["giveaway"]

    @staticmethod
    def _rep(ln: dict, rep: str) -> bool:
        return rep == "" or ln["rep"] == rep or ln["rep"].startswith(rep + " - ")

    @staticmethod
    def _wins(text: str) -> list[tuple[str, date, date]]:
        return [(w["k"], date.fromisoformat(w["s"]), date.fromisoformat(w["e"])) for w in json.loads(text)]

    def _anchor(self) -> dict:
        last = max((ln["day"] for ln in self.lines), default=None)
        stock = None if self.no_stock else self.stock_as_of or (last + timedelta(days=1) if last else None)
        return {"focus_to": last.isoformat() if last else None, "stock_as_of": stock.isoformat() if stock else None,
                "ar_as_of": stock.isoformat() if stock else None}

    def _sales(self, params: list) -> list[dict]:
        acc: dict[tuple, dict] = {}
        for k, s, e in self._wins(params[0]):
            for ln in self.lines:
                if ln["giveaway"] or not s <= ln["day"] <= e:
                    continue
                g = acc.setdefault((k, ln["channel"], ln["division"]), {
                    "net": Decimal(0), "inv": set(), "cash_inv": set(), "cash": Decimal(0), "named": Decimal(0)})
                g["net"] += ln["net"]
                g["inv"].add(ln["invoice"])
                if ln["cash"]:
                    g["cash_inv"].add(ln["invoice"])
                    g["cash"] += ln["net"]
                elif ln["channel"] == "B2B":
                    g["named"] += ln["net"]
        return [{"k": k, "channel": ch, "division": dv, "net_bhd": q3(g["net"]), "invoices": len(g["inv"]),
                 "cash_invoices": len(g["cash_inv"]), "cash_net_bhd": q3(g["cash"]), "named_net_bhd": q3(g["named"])}
                for (k, ch, dv), g in acc.items()]

    def _dim(self, params: list) -> list[dict]:
        acc: dict[tuple, dict] = {}
        for k, s, e in self._wins(params[0]):
            for ln in self.lines:
                if not (self._basis(ln) and self._rep(ln, params[1]) and s <= ln["day"] <= e):
                    continue
                if ln["rep"] == self.hide_in_dim:
                    continue
                for dim, name in (("salesman", ln["rep"]), ("account", ln["account"]), ("sku", ln["item"])):
                    g = acc.setdefault((k, dim, name), {"net": Decimal(0), "units": Decimal(0), "inv": set(),
                                                         "channel": ln["channel"], "cash": False})
                    g["net"] += ln["net"]
                    g["units"] += ln["qty"]
                    g["inv"].add(ln["invoice"])
                    g["channel"] = min(g["channel"], ln["channel"])
                    g["cash"] = g["cash"] or ln["cash"]
        return [{"k": k, "dim": dim, "name": name, "net_bhd": q3(g["net"]), "units": float(g["units"]),
                 "invoices": len(g["inv"]), "channel": g["channel"], "is_cash": g["cash"], "code": name.split()[0]}
                for (k, dim, name), g in acc.items()]

    def _trend(self, params: list) -> list[dict]:
        anchor = date.fromisoformat(params[0])
        low = sunday_of(anchor) - timedelta(days=84)
        acc: dict[date, Decimal] = {}
        for ln in self.lines:
            if ln["division"] == "Accessories" and not ln["giveaway"] and low <= ln["day"] <= anchor:
                acc[sunday_of(ln["day"])] = acc.get(sunday_of(ln["day"]), Decimal(0)) + ln["net"]
        return [{"week_start": k.isoformat(), "net_bhd": q3(v)} for k, v in sorted(acc.items())]

    def _daily(self, params: list) -> list[dict]:
        """The portal's daily read (a later release): one row per day that sold, the 30 days to $1."""
        anchor = date.fromisoformat(params[0])
        acc: dict[date, dict] = {}
        for ln in self.lines:
            if ln["division"] == "Accessories" and not ln["giveaway"] and anchor - timedelta(days=30) < ln["day"] <= anchor:
                g = acc.setdefault(ln["day"], {"net": Decimal(0), "inv": set()})
                g["net"] += ln["net"]
                g["inv"].add(ln["invoice"])
        return [{"day": k.isoformat(), "net_bhd": q3(g["net"]), "invoices": len(g["inv"])} for k, g in sorted(acc.items())]

    def _top(self, params: list) -> list[dict]:
        out = []
        for k, s, e in self._wins(params[0]):
            inv: dict[str, dict] = {}
            for ln in self.lines:
                if self._basis(ln) and self._rep(ln, params[1]) and s <= ln["day"] <= e:
                    g = inv.setdefault(ln["invoice"], {"net": Decimal(0), "rep": ln["rep"], "account": ln["account"],
                                                       "day": ln["day"]})
                    g["net"] += ln["net"]
            if inv:
                no, g = sorted(inv.items(), key=lambda kv: (-kv[1]["net"], kv[0]))[0]
                out.append({"k": k, "invoice_no": no, "net_bhd": q3(g["net"]), "rep": g["rep"], "account": g["account"],
                            "sale_day": g["day"].isoformat()})
        return out

    def _lapsed(self, params: list) -> list[dict]:
        p0, p1, c0, c1 = (date.fromisoformat(x) for x in params[:4])
        acc: dict[str, dict] = {}
        for ln in self.lines:
            if self._basis(ln) and not ln["cash"] and p0 <= ln["day"] <= p1 and self._rep(ln, params[4]):
                g = acc.setdefault(ln["account"], {"net": Decimal(0), "last": None})
                g["net"] += ln["net"]
                if g["last"] is None or (ln["day"], ln["line_id"]) > (g["last"]["day"], g["last"]["line_id"]):
                    g["last"] = ln
        back = {ln["account"] for ln in self.lines if self._basis(ln) and c0 <= ln["day"] <= c1}
        return [{"account": a, "net_bhd": q3(g["net"]), "rep": g["last"]["rep"], "last_day": g["last"]["day"].isoformat()}
                for a, g in acc.items() if g["net"] > 0 and a not in back]

    def _targets(self, params: list) -> list[dict]:
        period, y0, m0 = params[0], date.fromisoformat(params[1]), date.fromisoformat(params[2])
        best: dict[str, tuple] = {}
        for row in self.targets:
            if row[1] in ("", period) and (row[0] not in best or row[1] > best[row[0]][1]):
                best[row[0]] = row
        months = {(ln["day"].year, ln["day"].month) for ln in self.lines if self._basis(ln) and y0 <= ln["day"] < m0}
        net = sum((ln["net"] for ln in self.lines if self._basis(ln) and y0 <= ln["day"] < m0), Decimal(0))
        return [{"portal_target": self.portal_target, "reps": len(best),
                 "tier1_bhd": float(sum(r[2] for r in best.values())), "tier2_bhd": float(sum(r[3] for r in best.values())),
                 "tier3_bhd": float(sum(r[4] for r in best.values())), "net_months": q3(net), "months": len(months)}]

    def answer(self, sql: str, params: list | None) -> list[dict] | None:
        """Rows for one of the brief's own statements; None when the statement is not one of them."""
        table = {
            status.ANCHOR_SQL: ("anchor", lambda: [self._anchor()]),
            sales.REP_NAMES_SQL: ("rep_names", lambda: [{"rep": n} for n in sorted({ln["rep"] for ln in self.lines})]),
            metrics.SALES_SQL: ("sales", lambda: self._sales(params)),
            sales.DIM_SQL: ("dim", lambda: self._dim(params)),
            metrics.TREND_SQL: ("trend", lambda: self._trend(params)),
            sales.TOP_INVOICE_SQL: ("top", lambda: self._top(params)),
            sales.LAPSED_SQL: ("lapsed", lambda: self._lapsed(params)),
            sales.TARGETS_SQL: ("targets", lambda: self._targets(params)),
            sales.PREV_DAY_SQL: ("prev_day", lambda: [{"prev_day": max(
                (ln["day"] for ln in self.lines if ln["day"] < date.fromisoformat(params[0])), default=None)}]),
        }
        if sql not in table:
            return None
        name, fn = table[sql]
        self.calls.append((name, list(params) if params is not None else None))
        if name in self.fail:
            raise self.fail[name]
        rows = fn()
        return json.loads(json.dumps(rows, default=str))        # what the RPC returns: plain JSON

    def rpc(self, sql: str) -> list[dict]:
        rows = self.answer(sql, None)
        assert rows is not None, f"unexpected statement: {sql[:90]}"
        return rows

    def rpc_params(self, sql: str, params: list) -> list[dict]:
        assert len(params) <= 8, "the read RPC binds $1..$8 only"
        assert all(isinstance(p, str) for p in params), "parameters travel as text"
        rows = self.answer(sql, list(params))
        assert rows is not None, f"unexpected statement: {sql[:90]}"
        return rows

    def names(self) -> list[str]:
        return [n for n, _p in self.calls]


# ── the Command Centre's payload, in its real shape ─────────────────────────────────────────────

def attn(key: str, tone: str, title: str, bhd=None, rows=None, detail: str = "", basis: str = "", count=None) -> dict:
    """One item of the Command Centre's attention list, as app.metrics._item builds it."""
    return {"key": key, "tone": tone, "title": title, "detail": detail, "basis": basis, "count": count, "bhd": bhd,
            "drill": None, "rows": rows or []}


def rep_row(name: str, last: str | None, days: int | None, no_target: bool = False) -> dict:
    return {"salesman": name, "name": name, "no_target": no_target, "net_bhd": 0.0, "last_invoice": last,
            "business_days_since_invoice": days}


def command_centre(attention=None, team=None, sold_out=None, portal_target=800.0, daily_avg=None,
                   unavailable=None, active=None, margin_report_date: str | None = "2026-01-21") -> dict:
    """What sales.read_overview returns. The profitability report is as on the day after ANCHOR
    unless a test says otherwise (the usual case: the report is exported with the other files)."""
    sales_tiles = [{"key": "sales.pace", "available": True, "target_bhd": portal_target}]
    if daily_avg is not None:
        sales_tiles.append({"key": "sales.daily", "available": True, "avg_business_day": daily_avg, "days": 30})
    items = list(sold_out or [])
    payload = {"modules": [
        {"key": "attention", "title": "Needs attention", "items": list(attention or []), "tiles": []},
        {"key": "sales", "tiles": sales_tiles},
        {"key": "team", "tiles": [{"key": "team.reps", "available": True, "rows": list(team or [])}]},
        {"key": "products", "tiles": [{"key": "products.sold_out", "available": True, "value": len(items),
                                       "bhd": sum(x["net_60_bhd"] for x in items), "items": items[:5]}]},
    ], "unavailable": list(unavailable or []), "notes": []}
    return {"payload": payload, "active": dict(active or {}), "margin_report_date": margin_report_date}


def loaded(run: str, data_date: date | None, **more) -> dict:
    """A last load on record: folder <ddmmyy>_<HHMM>, finished five minutes later, bringing `data_date`."""
    day = date(2000 + int(run[4:6]), int(run[2:4]), int(run[0:2]))
    out = {"run": run, "finished_at": f"{day.isoformat()}T{run[7:9]}:{int(run[9:11]) + 5:02d}:00+03:00", "result": "ok"}
    if data_date is not None:
        out["data_date"] = data_date.isoformat()
    return dict(out, **more)


# ── running the command ─────────────────────────────────────────────────────────────────────────

class data_root:
    """A temporary data root with the fixture config and a last load on record."""

    def __init__(self, cfg: dict | None = None, last_load: dict | None = None):
        self.cfg = dict(FIXTURE, **(cfg or {}))
        self.last_load = last_load

    def __enter__(self) -> Path:
        # Windows can still hold a just-written file for a moment (a virus scan): that is not a test failure
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name).resolve()          # as paths.set_data_root keeps it
        paths.set_data_root(self.root)
        config.use(self.cfg)
        status.write_state({"last_load": self.last_load or {
            "run": "210126_0910", "finished_at": "2026-01-21T09:10:00+03:00", "result": "ok", "data_date": "2026-01-20"}})
        return self.root

    def __exit__(self, *exc):
        paths.set_data_root(None)
        config.use(None)
        self._tmp.cleanup()
        return False


class Args:
    def __init__(self, spec=(), again: bool = False, top: int = 10):
        self.spec, self.again, self.top = list(spec), again, top


class standing_in:
    """db.rpc / db.rpc_params answered by the world, the Command Centre call by a canned payload."""

    def __init__(self, world: World, overview=None):
        self.world, self.overview, self.overview_calls = world, overview, 0

    def _overview(self) -> dict:
        self.overview_calls += 1
        if isinstance(self.overview, Exception):
            raise self.overview
        return self.overview if self.overview is not None else command_centre()

    def __enter__(self):
        self.saved = (db.rpc, db.rpc_params, sales.read_overview)
        db.rpc, db.rpc_params, sales.read_overview = self.world.rpc, self.world.rpc_params, self._overview
        return self

    def __exit__(self, *exc):
        db.rpc, db.rpc_params, sales.read_overview = self.saved
        return False


def brief(world: World, *spec: str, overview=None, again: bool = False, top: int = 10) -> dict:
    with standing_in(world, overview):
        return sales.run(Args(spec, again, top))


def line(text: str, start: str) -> str:
    hits = [ln for ln in text.splitlines() if ln.startswith(start)]
    assert hits, f"no line starts with {start!r} in:\n{text}"
    return hits[0]


def cells(text: str, start: str) -> list[str]:
    """A table row as its cells (columns are two or more spaces apart)."""
    return re.split(r"\s{2,}", line(text, start).strip())


# ── what was typed ──────────────────────────────────────────────────────────────────────────────

@test("window grammar: every form is read, on the data date and never on today")
def _():
    day = date
    forms = {
        "day": (day(2026, 1, 20), day(2026, 1, 20)), "3d": (day(2026, 1, 18), day(2026, 1, 20)),
        "7d": (day(2026, 1, 14), day(2026, 1, 20)), "week": (day(2026, 1, 18), day(2026, 1, 20)),
        "last-week": (day(2026, 1, 11), day(2026, 1, 17)), "mtd": (day(2026, 1, 1), day(2026, 1, 20)),
        "last-month": (day(2025, 12, 1), day(2025, 12, 31)), "2025-11": (day(2025, 11, 1), day(2025, 11, 30)),
        "2026-01": (day(2026, 1, 1), day(2026, 1, 20)), "2026-1": (day(2026, 1, 1), day(2026, 1, 20)),
        "2025-12-30..2026-01-02": (day(2025, 12, 30), day(2026, 1, 2)),
        "2026-01-19..2026-02-05": (day(2026, 1, 19), day(2026, 1, 20)),
    }
    for word, (start, end) in forms.items():
        window, rep, again, top = sales.parse_spec([word])
        assert (window, rep, again, top) == (word, None, False, None), word
        win, title, notes = sales.resolve_window(window, ANCHOR, ANCHOR)
        assert (win.start, win.end) == (start, end), (word, win)
        assert title and isinstance(notes, list)
    assert sales.resolve_window("2026-01", ANCHOR, ANCHOR)[2] == ["January is not over: the data runs to Tue 20 Jan."]
    assert "stops there" in sales.resolve_window("2026-01-19..2026-02-05", ANCHOR, ANCHOR)[2][0]
    assert sales.resolve_window("last-month", ANCHOR, ANCHOR)[1] == "Last month (December 2025)"
    # a Saturday anchor: its own week is the last full week; the week so far is the whole week
    sat = day(2026, 1, 17)
    assert sales.resolve_window("last-week", sat, sat)[0].start == day(2026, 1, 11)
    assert sales.resolve_window("week", sat, sat)[0].start == day(2026, 1, 11)
    assert sales.resolve_window("last-week", day(2026, 1, 16), day(2026, 1, 16))[0].end == day(2026, 1, 10)
    # a part day (the latest full sale day is before the data date): the latest day is that sale day, the 3-day,
    # 7-day and full-week windows stop at the calendar day before the part day, the week so far and the month keep it
    sun, thu = day(2026, 1, 18), day(2026, 1, 15)
    on_part_day = {
        "day": (thu, thu), "3d": (thu, sat), "7d": (day(2026, 1, 11), sat), "last-week": (day(2026, 1, 11), sat),
        "week": (sun, sun), "mtd": (day(2026, 1, 1), sun)}
    for word, (start, end) in on_part_day.items():
        win = sales.resolve_window(word, sun, thu)[0]
        assert (win.start, win.end) == (start, end), (word, win)
    assert sales.resolve_window("last-week", sat, thu)[0].end == day(2026, 1, 10), "a Saturday part day: its week is open"
    assert sales.full_until(sun, sun) == sun and sales.full_until(sun, thu) == sat
    assert sales.full_week_end(sat) == sat and sales.full_week_end(day(2026, 1, 16)) == day(2026, 1, 10)
    # words in any order and case; a name of several words; one string with spaces
    assert sales.parse_spec(["MTD", "rep:Van", "A"]) == ("mtd", "Van A", False, None)
    assert sales.parse_spec(["rep:Van", "A", "Last_Week"]) == ("last-week", "Van A", False, None)
    assert sales.parse_spec(["7d rep:Van B again"]) == ("7d", "Van B", True, None)
    assert sales.parse_spec(["last", "week"]) == ("last-week", None, False, None), "two words, as it is said"
    assert sales.parse_spec(["rep:Van A last month"]) == ("last-month", "Van A", False, None)
    assert sales.parse_spec([]) == (None, None, False, None) and sales.parse_spec(["again"]) == (None, None, True, None)
    # top:N is a word too, anywhere among the others (the command line reads --top only after the last word)
    assert sales.parse_spec(["mtd", "top:3", "rep:Van", "B"]) == ("mtd", "Van B", False, 3)
    assert sales.parse_spec(["rep:Van", "B", "TOP:25", "mtd"]) == ("mtd", "Van B", False, 25)
    assert sales.parse_spec(["top:7"]) == (None, None, False, 7)


@test("a window that cannot be read is refused in one plain sentence, before any read")
def _():
    bad = {
        ("fortnight",): "I do not know the window 'fortnight'", ("2026-01-05",): "I do not know the window",
        ("last", "year"): "I do not know the window 'last'",
        ("mtd", "7d"): "Two windows were given", ("rep:",): "rep: needs a name",
        ("rep:Van A", "rep:Van B"): "one salesman only",
        ("mtd", "top:"): "top: needs a whole number above 0", ("top:many",): "top: needs a whole number above 0",
        ("top:0",): "top: needs a whole number above 0", ("top:-3", "mtd"): "top: needs a whole number above 0",
        ("top:2.5",): "top: needs a whole number above 0",
    }
    for spec, want in bad.items():
        exc = raises(sales.BriefInput, lambda s=spec: sales.parse_spec(list(s)))
        assert want in str(exc), (spec, str(exc))
    later = {
        "2026-13": "is not a month", "2026-02-30..2026-03-01": "is not a day on the calendar",
        "2026-00": "'2026-00' is not a month", "0000-01": "'0000-01' is not a month. Write a month as 2026-09.",
        "0000-01-01..0000-01-05": "is not a day on the calendar",
        "2026-01-10..2026-01-05": "The first day is after the last day", "2026-03": "No sales are loaded for March 2026",
        "2026-02-01..2026-02-05": "No sales are loaded after Tue 20 Jan 2026",
    }
    for word, want in later.items():
        exc = raises(sales.BriefInput, lambda w=word: sales.resolve_window(w, ANCHOR, ANCHOR))
        assert want in str(exc), (word, str(exc))
    with data_root():
        world = World()
        out = brief(world, "fortnight")
        assert out["ok"] is False and out["exit"] == 1 and out["files"] == []
        assert out["text"].startswith("I do not know the window 'fortnight'. Use one of: day, 3d, 7d, week, last-week,")
        assert "Traceback" not in out["text"] and "\n" not in out["text"] and world.calls == []
        out = brief(world, "2026-13")
        assert out["ok"] is False and out["text"] == "'2026-13' is not a month. Write a month as 2026-09."
        # a month in the year 0000 is no Python error either, and a window in a year long gone is just empty
        out = brief(world, "0000-01")
        assert out["ok"] is False and out["exit"] == 1
        assert out["text"] == "'0000-01' is not a month. Write a month as 2026-09."
        out = brief(world, "mtd", "top:x")
        assert out["ok"] is False and out["text"] == "top: needs a whole number above 0, for example top:20."
        assert not paths.exports_dir().exists(), "a refused window writes nothing"
        assert "No sales are loaded in this window." in brief(world, "0001-01")["text"]


# ── the full brief on the steady world ──────────────────────────────────────────────────────────

@test("the full brief: two head lines, then five sales rows beside what each is compared with")
def _():
    with data_root():
        world = World()
        out = brief(world)
        text = out["text"]
        head = text.splitlines()
        assert head[0] == "YQ ACCESSORIES BRIEF - data to Tue 20 Jan - loaded Wed 21 Jan 09:10", head[0]
        assert head[1] == "Accessories only, ex-VAT, BHD. Returns not deducted." and head[2] == ""
        assert out["ok"] is True and out["data_date"] == "2026-01-20" and out["command"] == "brief"
        assert cells(text, "SALES") == ["SALES", "Days", "BHD", "Invoices", "Beside"]
        assert cells(text, "Latest day") == ["Latest day", "Tue 20 Jan", "30.000", "2",
                                             "30-day average business day 30.000"]
        assert cells(text, "Last 3 days") == ["Last 3 days", "Sun 18 Jan - Tue 20 Jan", "90.000", "6",
                                              "30-day average business day 30.000"]
        assert cells(text, "Last 7 days") == ["Last 7 days", "Wed 14 Jan - Tue 20 Jan", "150.000", "10",
                                              "4-week average 150.000; inside the normal range"]
        assert cells(text, "Last full week") == ["Last full week", "Sun 11 Jan - Sat 17 Jan", "150.000", "10",
                                                 "4-week average 150.000; inside the normal range"]
        # 14 business days this month (1, 4-8, 11-15, 18-20 Jan) against the first 14 of December (1-18 Dec)
        assert cells(text, "Month to date") == ["Month to date", "Thu 01 Jan - Tue 20 Jan", "420.000", "28",
                                                "the same 14 business days last month 420.000: 0%"]
        assert line(text, "Largest invoice, latest day") == "Largest invoice, latest day: 20.000, Van A, Shop 1"
        assert line(text, "Largest invoice, last 3 days") == \
            "Largest invoice, last 3 days: 20.000, Van A, Shop 1, on Sun 18 Jan"
        assert line(text, "Month so far") == "Month so far: B2B 420.000, B2C 0.000; 28 invoices; 2 accounts invoiced"
        assert line(text, "ACCOUNTS") == "ACCOUNTS  every account that bought last month has bought again this month"
        assert "DO TODAY" in text and "Nothing open." in text
        assert len(text.splitlines()) <= 45, "one screen"
        assert max(len(ln) for ln in text.splitlines()) <= 120, max(text.splitlines(), key=len)


@test("one Command Centre call, one SALES_SQL with every window, one DIM_SQL, and the small reads")
def _():
    with data_root():
        world = World()
        with standing_in(world) as st:
            sales.run(Args())
            assert st.overview_calls == 1
        names = world.names()
        assert sorted(names) == ["anchor", "dim", "lapsed", "sales", "targets", "top", "trend"], sorted(names)
        params = dict(world.calls)
        keys = [w["k"] for w in json.loads(params["sales"][0])]
        assert keys == ["day", "d3", "d7", "lfw", "d30", "mtd", "cmp"], keys
        assert [w["k"] for w in json.loads(params["dim"][0])] == ["mtd", "cmp"] and params["dim"][1] == ""
        assert params["trend"] == ["2026-01-17"], "anchored on the Saturday that ends the last full week"
        assert params["lapsed"] == ["2025-12-01", "2025-12-31", "2026-01-01", "2026-01-20", ""]
        assert params["targets"] == ["2026-01", "2025-01-01", "2026-01-01"]
        d30 = next(w for w in json.loads(params["sales"][0]) if w["k"] == "d30")
        assert (d30["s"], d30["e"]) == ("2025-12-22", "2026-01-20"), "the 30 days to the data date"


@test("the basis: SIM, Devices and giveaway lines never count; B2C is the counters; Cash Customer is no account")
def _():
    with data_root():
        world = World()
        world.sale(date(2026, 1, 6), VAN_A, "Shop 1", "SIM-01 Starter", 500, division="SIM")
        world.sale(date(2026, 1, 6), VAN_A, "Shop 1", "PHONE-01 Handset", 300, division="Devices")
        world.sale(date(2026, 1, 6), VAN_A, "Shop 1", "ITEM-09 Gift", 0, qty=5, giveaway=True)
        world.sale(date(2026, 1, 6), "Counter One", "Cash Customer", "ITEM-01 Cable", 12, channel="B2C")
        text = brief(world)["text"]
        assert cells(text, "Month to date")[2:4] == ["432.000", "29"]
        assert line(text, "Month so far") == "Month so far: B2B 420.000, B2C 12.000; 29 invoices; 2 accounts invoiced"
        text = brief(world, "mtd")["text"]
        assert line(text, "TOTAL") == "TOTAL     432.000 in 29 invoices; 43 units; B2B 420.000, B2C 12.000"
        assert "2 accounts invoiced 420.000; cash customers 12.000 in 1 invoice" in text
        assert "Cash Customer" not in text.split("TOP ACCOUNTS")[1], "the walk-in is never in the account table"
        assert "SIM-01" not in text and "PHONE-01" not in text and "ITEM-09" not in text


@test("the anchor is the last loaded sale day; a weekend day is said so")
def _():
    with data_root(last_load={"run": "180126_0900", "finished_at": "2026-01-18T09:00:00+03:00", "data_date": "2026-01-17"}):
        world = World(to=date(2026, 1, 15))
        world.sale(date(2026, 1, 17), "Counter One", "Cash Customer", "ITEM-01 Cable", 7, channel="B2C")
        out = brief(world)
        assert out["data_date"] == "2026-01-17" and "data to Sat 17 Jan - loaded Sun 18 Jan 09:00" in out["text"]
        assert cells(out["text"], "Latest day")[:4] == ["Latest day", "Sat 17 Jan (weekend)", "7.000", "1"]
        assert cells(out["text"], "Last 3 days")[:4] == ["Last 3 days", "Thu 15 Jan - Sat 17 Jan", "37.000", "3"]
        assert cells(out["text"], "Last full week")[1] == "Sun 11 Jan - Sat 17 Jan", "a Saturday closes its own week"
        assert out["data_date"] != status.today_bahrain().isoformat(), "the calendar plays no part"


@test("a part day: the export day is said so and the latest day is the loaded sale day before it")
def _():
    with data_root():
        world = World(to=date(2026, 1, 19), stock_as_of=ANCHOR)       # the stock snapshot is as on the data date
        world.sale(ANCHOR, VAN_A, "Shop 1", "ITEM-01 Cable", 5)
        out = brief(world)
        text = out["text"]
        assert line(text, "Part day") == (
            "Part day: Tue 20 Jan is the day the reports were exported, so it may not be complete "
            "(5.000 in 1 invoice so far). The latest full day is Mon 19 Jan; the month to date includes the part day.")
        assert cells(text, "Latest day")[:4] == ["Latest day", "Mon 19 Jan", "30.000", "2"]
        assert cells(text, "Last 3 days")[:4] == ["Last 3 days", "Sat 17 Jan - Mon 19 Jan", "60.000", "4"]
        assert cells(text, "Last 7 days")[:4] == ["Last 7 days", "Tue 13 Jan - Mon 19 Jan", "150.000", "10"]
        assert cells(text, "Month to date")[:4] == ["Month to date", "Thu 01 Jan - Tue 20 Jan", "395.000", "27"]
        assert "prev_day" in world.names() and out["data_date"] == "2026-01-20"
        # the 30 days of the average run to the data date, part day and all: 22 Dec - 20 Jan is 21 full business
        # days of 30.000 and the 5.000 of the part day, over 22 (to the day before it would be 660 over 22)
        assert cells(text, "Latest day")[4] == "30-day average business day 28.864"
        d30 = next(w for w in json.loads(dict(world.calls)["sales"][0]) if w["k"] == "d30")
        assert (d30["s"], d30["e"]) == ("2025-12-22", "2026-01-20")
        # a window asked for by itself says which side of the part day it is on
        assert "Tue 20 Jan is a part day (the reports were exported that day): it is in this window." in \
            brief(world, "mtd")["text"]
        for word, days in (("day", "Mon 19 Jan"), ("3d", "Sat 17 Jan - Mon 19 Jan"), ("7d", "Tue 13 Jan - Mon 19 Jan"),
                           ("last-week", "Sun 11 Jan - Sat 17 Jan")):
            shown = brief(world, word)["text"]
            assert f": {days} - data to Tue 20 Jan" in shown.splitlines()[0], (word, shown.splitlines()[0])
            assert "Tue 20 Jan is a part day (the reports were exported that day): this window stops before it." in shown
        assert "part day" not in brief(world, "2025-12")["text"], "a month he named that ended long before says nothing"
    # the folder of the last load is named for the data date: the same rule without a stock snapshot
    with data_root(last_load={"run": "200126_1400", "finished_at": "2026-01-20T14:05:00+03:00"}):
        world = World(to=date(2026, 1, 19), stock_as_of=date(2026, 1, 10))
        world.sale(ANCHOR, VAN_A, "Shop 1", "ITEM-01 Cable", 5)
        assert line(brief(world)["text"], "Part day").startswith("Part day: Tue 20 Jan")
    with data_root(last_load=loaded("200126_1400", ANCHOR)):              # ... and when that load says it brought that day
        world = World(to=date(2026, 1, 19), stock_as_of=date(2026, 1, 10))
        world.sale(ANCHOR, VAN_A, "Shop 1", "ITEM-01 Cable", 5)
        assert line(brief(world)["text"], "Part day").startswith("Part day: Tue 20 Jan")
    # a full day (the snapshot is as on the day after): no part-day line and no extra read
    with data_root():
        world = World()
        assert "Part day" not in brief(world)["text"] and "prev_day" not in world.names()


@test("a load on record that brought an older day says nothing about the data date: no part day")
def _():
    # the last /yq-load was folder 210126 and brought sales to Tue 20 Jan; a later upload (the portal's Data
    # page, a load by hand) brought Wed 21 Jan, with the stock as on Thu 22 Jan: Wed 21 Jan is a full day
    with data_root():
        world = World(to=date(2026, 1, 21))
        out = brief(world)
        text = out["text"]
        assert out["data_date"] == "2026-01-21" and "Part day" not in text and "prev_day" not in world.names()
        assert text.splitlines()[0] == "YQ ACCESSORIES BRIEF - data to Wed 21 Jan - load time not recorded"
        assert cells(text, "Latest day")[:4] == ["Latest day", "Wed 21 Jan", "30.000", "2"]
        assert cells(text, "Last 3 days")[:4] == ["Last 3 days", "Mon 19 Jan - Wed 21 Jan", "90.000", "6"]
    # the rule itself: D is a part day when the export day (sales.export_day) is D
    state_only = {"last_load": loaded("210126_0910", ANCHOR)}
    wed, thu = date(2026, 1, 21), date(2026, 1, 22)
    assert sales.export_day(wed, {"stock_as_of": "2026-01-22"}, state_only) == thu, "the folder date is not its day"
    assert sales.export_day(wed, {"stock_as_of": "2026-01-21"}, state_only) == wed, "the stock snapshot still is"
    assert sales.export_day(wed, {"stock_as_of": "2026-01-22"}, {"last_load": loaded("210126_1500", wed)}) == wed
    assert sales.export_day(wed, {"stock_as_of": "2026-01-22"}, {"last_load": {"run": "210126_1500"}}) == wed
    assert sales.export_day(wed, {"stock_as_of": None}, {}) is None


@test("a part day beside a week end: the week that ended the day before is the last full week")
def _():
    sun, sat, thu = date(2026, 1, 18), date(2026, 1, 17), date(2026, 1, 15)
    # reports exported on Sunday 18 Jan: nothing sold on Friday and Saturday, one large order on Tuesday
    with data_root(last_load=loaded("180126_1300", sun)):
        world = World(to=thu, stock_as_of=sun)
        world.sale(sun, VAN_A, "Shop 1", "ITEM-01 Cable", 5)
        world.sale(date(2026, 1, 13), VAN_A, "Shop 9", "ITEM-01 Cable", 400, qty=40)
        out = brief(world)
        text = out["text"]
        assert line(text, "Part day") == (
            "Part day: Sun 18 Jan is the day the reports were exported, so it may not be complete "
            "(5.000 in 1 invoice so far). The latest day with sales before it is Thu 15 Jan; the 3-day, 7-day and "
            "full-week lines stop at Sat 17 Jan; the month to date includes the part day.")
        assert cells(text, "Latest day")[:4] == ["Latest day", "Thu 15 Jan", "30.000", "2"]
        assert cells(text, "Last 3 days")[:4] == ["Last 3 days", "Thu 15 Jan - Sat 17 Jan", "30.000", "2"]
        # the week 11 - 17 Jan is over and whole: five days of 30.000 and the order of 400.000
        assert cells(text, "Last 7 days") == ["Last 7 days", "Sun 11 Jan - Sat 17 Jan", "550.000", "11",
                                              "4-week average 150.000; above the normal range"]
        assert cells(text, "Last full week") == ["Last full week", "Sun 11 Jan - Sat 17 Jan", "550.000", "11",
                                                 "4-week average 150.000; above the normal range"]
        assert dict(world.calls)["trend"] == ["2026-01-17"], "the weekly totals end with that week"
        assert line(text, "Largest invoice, last 3 days") == \
            "Largest invoice, last 3 days: 20.000, Van A, Shop 1, on Thu 15 Jan"
        facts = json.loads((paths.exports_dir("2026-01-18") / "brief.json").read_text(encoding="utf-8"))
        assert facts["latest_day"] == "2026-01-15" and facts["full_to"] == "2026-01-17" and facts["part_day"] is True
        assert facts["sales"]["lfw"]["start"] == "2026-01-11" and facts["sales"]["four_week_average"]["bhd"] == "150.000"
        shown = brief(world, "last-week")["text"]
        assert shown.splitlines()[0].startswith("YQ ACCESSORIES BRIEF - Last full week: Sun 11 Jan - Sat 17 Jan - data to")
        assert line(shown, "TOTAL") == "TOTAL     550.000 in 11 invoices; 55 units; B2B 550.000, B2C 0.000"
        assert "Sun 18 Jan is a part day (the reports were exported that day): this window stops before it." in shown
        assert ": Thu 15 Jan - data to Sun 18 Jan" in brief(world, "day")["text"].splitlines()[0]
        assert "This week so far: Sun 18 Jan - data to" in brief(world, "week")["text"].splitlines()[0]
    # the same Sunday loaded the next morning is a full day: the same week, and the windows run to the Sunday
    with data_root(last_load=loaded("190126_0900", sun)):
        world = World(to=thu)
        world.sale(sun, VAN_A, "Shop 1", "ITEM-01 Cable", 5)
        text = brief(world)["text"]
        assert "Part day" not in text and cells(text, "Latest day")[:3] == ["Latest day", "Sun 18 Jan", "5.000"]
        assert cells(text, "Last 7 days")[1] == "Mon 12 Jan - Sun 18 Jan"
        assert cells(text, "Last full week")[1] == "Sun 11 Jan - Sat 17 Jan"
    # reports exported on Saturday 17 Jan: its own week is not over, so the week before is the last full one
    with data_root(last_load=loaded("170126_1500", sat)):
        world = World(to=thu, stock_as_of=sat)
        world.sale(sat, "Counter One", "Cash Customer", "ITEM-01 Cable", 7, channel="B2C")
        text = brief(world)["text"]
        assert line(text, "Part day").startswith("Part day: Sat 17 Jan is the day the reports were exported")
        assert "The latest day with sales before it is Thu 15 Jan; the 3-day, 7-day and full-week lines stop at Fri 16 Jan" \
            in line(text, "Part day")
        assert cells(text, "Latest day")[:4] == ["Latest day", "Thu 15 Jan", "30.000", "2"]
        assert cells(text, "Last 3 days")[:4] == ["Last 3 days", "Wed 14 Jan - Fri 16 Jan", "60.000", "4"]
        assert cells(text, "Last 7 days") == ["Last 7 days", "Sat 10 Jan - Fri 16 Jan", "150.000", "10",
                                              "4-week average 150.000; inside the normal range"]
        assert cells(text, "Last full week") == ["Last full week", "Sun 04 Jan - Sat 10 Jan", "150.000", "10",
                                                 "4-week average 150.000; month-end week: not judged"]
        assert dict(world.calls)["trend"] == ["2026-01-10"]
        assert cells(text, "Month to date")[:4] == ["Month to date", "Thu 01 Jan - Sat 17 Jan", "337.000", "23"]


@test("complete days: a Sunday-morning export with no sale since Thursday still shows the week that ended on the Saturday")
def _():
    sun, sat, fri, thu = date(2026, 1, 18), date(2026, 1, 17), date(2026, 1, 16), date(2026, 1, 15)
    note = ("The reports were exported on Sun 18 Jan and hold no sale after Thu 15 Jan: the 3-day, 7-day and "
            "full-week lines run to Sat 17 Jan.")
    # exported on Sunday morning: nothing sold on Friday or Saturday, and nothing yet on the Sunday. The data date
    # is the Thursday; Friday and Saturday are over all the same, so the week 11 - 17 Jan is complete
    with data_root(last_load=loaded("180126_0800", thu)):
        world = World(to=thu, stock_as_of=sun)
        world.sale(date(2026, 1, 13), VAN_A, "Shop 9", "ITEM-01 Cable", 400, qty=40)
        out = brief(world)
        text = out["text"]
        head = text.splitlines()
        assert out["data_date"] == "2026-01-15" and "Part day" not in text and "prev_day" not in world.names()
        assert head[0] == "YQ ACCESSORIES BRIEF - data to Thu 15 Jan - loaded Sun 18 Jan 08:05"
        assert head[3] == note and head[4] == "" and head[5].startswith("SALES"), head[3:6]
        assert cells(text, "Latest day")[:4] == ["Latest day", "Thu 15 Jan", "30.000", "2"]
        assert cells(text, "Last 3 days")[:4] == ["Last 3 days", "Thu 15 Jan - Sat 17 Jan", "30.000", "2"]
        # the week: five days of 30.000 and the order of 400.000, read against the weeks before it
        assert cells(text, "Last 7 days") == ["Last 7 days", "Sun 11 Jan - Sat 17 Jan", "550.000", "11",
                                              "4-week average 150.000; above the normal range"]
        assert cells(text, "Last full week") == ["Last full week", "Sun 11 Jan - Sat 17 Jan", "550.000", "11",
                                                 "4-week average 150.000; above the normal range"]
        assert dict(world.calls)["trend"] == ["2026-01-17"], "the weekly totals end with that week"
        assert cells(text, "Month to date")[:4] == ["Month to date", "Thu 01 Jan - Thu 15 Jan", "730.000", "23"]
        assert line(text, "Largest invoice, last 3 days") == \
            "Largest invoice, last 3 days: 20.000, Van A, Shop 1, on Thu 15 Jan"
        facts = json.loads(Path(out["files"][1]).read_text(encoding="utf-8"))
        assert (facts["latest_day"], facts["full_to"], facts["exported"], facts["part_day"]) == \
            ("2026-01-15", "2026-01-17", "2026-01-18", False)
        assert facts["sales"]["no_sale_after"] == {"day": "2026-01-15", "exported": "2026-01-18", "full_to": "2026-01-17"}
        # the windows asked for by themselves end on the same day and say why
        for word, days in (("3d", "Thu 15 Jan - Sat 17 Jan"), ("7d", "Sun 11 Jan - Sat 17 Jan"),
                           ("last-week", "Sun 11 Jan - Sat 17 Jan")):
            shown = brief(world, word)["text"].splitlines()
            assert f": {days} - data to Thu 15 Jan" in shown[0], (word, shown[0])
            assert shown[3] == ("The reports were exported on Sun 18 Jan and hold no sale after Thu 15 Jan: "
                                "this window runs to Sat 17 Jan."), (word, shown[3])
        assert line(brief(world, "last-week")["text"], "TOTAL") == \
            "TOTAL     550.000 in 11 invoices; 55 units; B2B 550.000, B2C 0.000"
        # ... and the windows that end on the data date itself say nothing of it
        for word, days in (("day", "Latest day: Thu 15 Jan"), ("week", "This week so far: Sun 11 Jan - Thu 15 Jan"),
                           ("mtd", "Month to date: Thu 01 Jan - Thu 15 Jan")):
            shown = brief(world, word)["text"]
            assert f"- {days} - data to Thu 15 Jan" in shown.splitlines()[0], (word, shown.splitlines()[0])
            assert "exported on" not in shown, word
    # the same morning with no stock snapshot loaded: the folder of the load that brought the Thursday names the day
    with data_root(last_load=loaded("180126_0800", thu)):
        text = brief(World(to=thu, no_stock=True))["text"]
        assert note in text and cells(text, "Last full week")[1] == "Sun 11 Jan - Sat 17 Jan"
    # ... but a load on record that brought another day says nothing of this data: the lines stop at the data date
    with data_root(last_load=loaded("180126_0800", date(2026, 1, 14))):
        world = World(to=thu, no_stock=True)
        text = brief(world)["text"]
        assert "exported on" not in text and "Part day" not in text
        assert cells(text, "Last 3 days")[1] == "Tue 13 Jan - Thu 15 Jan"
        assert cells(text, "Last 7 days")[1] == "Fri 09 Jan - Thu 15 Jan"
        assert cells(text, "Last full week")[1] == "Sun 04 Jan - Sat 10 Jan"
        assert dict(world.calls)["trend"] == ["2026-01-10"]
    # exported the morning after the data date, as on any working day: nothing changes and nothing is said
    with data_root(last_load=loaded("160126_0900", thu)):
        text = brief(World(to=thu))["text"]
        assert "exported on" not in text and cells(text, "Last 3 days")[1] == "Tue 13 Jan - Thu 15 Jan"
        assert cells(text, "Last full week")[1] == "Sun 04 Jan - Sat 10 Jan"
    # the rules themselves
    assert sales.full_until(thu, thu, sun) == sat, "the days before the export are over"
    assert sales.full_until(thu, thu, sat) == fri, "the export day itself is not"
    assert sales.full_until(thu, thu, fri) == thu and sales.full_until(thu, thu) == thu
    assert sales.full_until(sun, thu, sun) == sat, "a part day: the day before it"
    assert sales.full_until(sun, sun, sun) == sun, "a part day that is the only day there is"
    assert sales.full_week_end(sales.full_until(thu, thu, sun)) == sat
    assert sales.full_week_end(sales.full_until(thu, thu, sat)) == date(2026, 1, 10)
    assert sales.resolve_window("last-week", thu, thu, sat)[0] == sales.Win("win", date(2026, 1, 11), sat)
    assert sales.resolve_window("3d", thu, thu, sat)[0] == sales.Win("win", thu, sat)
    assert sales.resolve_window("day", thu, thu, sat)[0] == sales.Win("win", thu, thu), "the latest day is a sale day"
    # the export day: the stock date; with none, the folder of the load that brought the data date; of two days the
    # earlier; never a day before the data date
    by_load = {"last_load": loaded("180126_0800", thu)}
    assert sales.export_day(thu, {"stock_as_of": "2026-01-18"}, {}) == sun
    assert sales.export_day(thu, {"stock_as_of": None}, by_load) == sun
    assert sales.export_day(thu, {}, {"last_load": {"run": "180126"}}) == sun, "a load made by hand names its day too"
    assert sales.export_day(thu, {"stock_as_of": None}, {"last_load": loaded("180126_0800", date(2026, 1, 14))}) is None
    assert sales.export_day(thu, {"stock_as_of": "2026-01-19"}, by_load) == sun, "of two, the earlier"
    assert sales.export_day(thu, {"stock_as_of": "2026-01-17"}, by_load) == sat
    assert sales.export_day(thu, {"stock_as_of": "2026-01-10"}, {}) is None, "a snapshot older than the sales says nothing"
    assert sales.export_day(thu, {"stock_as_of": "2026-01-10"}, by_load) == sun
    assert sales.export_day(thu, {}, {}) is None


@test("a week whose Saturday is the export day itself is not complete: the week before stays the last full week")
def _():
    sat, thu = date(2026, 1, 17), date(2026, 1, 15)
    # exported on Saturday 17 Jan before anything sold that day: the Friday is over, the Saturday is not
    with data_root(last_load=loaded("170126_0900", thu)):
        world = World(to=thu, stock_as_of=sat)
        text = brief(world)["text"]
        assert "Part day" not in text and "prev_day" not in world.names()
        assert text.splitlines()[3] == ("The reports were exported on Sat 17 Jan and hold no sale after Thu 15 Jan: "
                                        "the 3-day, 7-day and full-week lines run to Fri 16 Jan.")
        assert cells(text, "Latest day")[:4] == ["Latest day", "Thu 15 Jan", "30.000", "2"]
        assert cells(text, "Last 3 days")[:4] == ["Last 3 days", "Wed 14 Jan - Fri 16 Jan", "60.000", "4"]
        assert cells(text, "Last 7 days")[:4] == ["Last 7 days", "Sat 10 Jan - Fri 16 Jan", "150.000", "10"]
        assert cells(text, "Last full week") == ["Last full week", "Sun 04 Jan - Sat 10 Jan", "150.000", "10",
                                                 "4-week average 150.000; month-end week: not judged"]
        assert dict(world.calls)["trend"] == ["2026-01-10"]
        shown = brief(world, "last-week")["text"]
        assert "Last full week: Sun 04 Jan - Sat 10 Jan - data to Thu 15 Jan" in shown.splitlines()[0]
        assert "exported on" not in shown, "that week ended before the data date: nothing to explain"
        assert "this window runs to Fri 16 Jan." in brief(world, "7d")["text"].splitlines()[3]
    # the same Saturday with a counter sale on it is a part day: the same week (the test before this one), and
    # a Saturday that was exported the next morning closes its own week
    with data_root(last_load=loaded("180126_0900", sat)):
        world = World(to=thu)
        world.sale(sat, "Counter One", "Cash Customer", "ITEM-01 Cable", 7, channel="B2C")
        text = brief(world)["text"]
        assert "exported on" not in text and "Part day" not in text
        assert cells(text, "Last full week")[1] == "Sun 11 Jan - Sat 17 Jan"


@test("a part day with no loaded sale day before it is the only day there is: it is shown as the latest day")
def _():
    with data_root(last_load=loaded("200126_1400", ANCHOR)):
        world = World(steady=False, stock_as_of=ANCHOR).sale(ANCHOR, VAN_A, "Shop 1", "ITEM-01 Cable", 5)
        out = brief(world)
        assert "prev_day" in world.names(), "the day before was looked for"
        assert "Part day" not in out["text"]
        assert cells(out["text"], "Latest day")[:4] == ["Latest day", "Tue 20 Jan", "5.000", "1"]
        assert json.loads(Path(out["files"][1]).read_text(encoding="utf-8"))["part_day"] is False


@test("latest day and last 3 days carry no percentage, beside the 30-day average business day")
def _():
    with data_root():
        world = World()
        world.sale(date(2026, 1, 19), VAN_B, "Shop 2", "ITEM-02 Charger", 150, qty=15)     # a large order
        text = brief(world)["text"]
        for start in ("Latest day", "Last 3 days", "Largest invoice"):
            for ln in [x for x in text.splitlines() if x.startswith(start)]:
                assert "%" not in ln, ln
        # 22 business days in the 30 days to the data date (22 Dec - 20 Jan): 660 + 150 over 22
        assert cells(text, "Last 3 days")[2:] == ["240.000", "7", "30-day average business day 36.818"]
        assert line(text, "Largest invoice, last 3 days") == \
            "Largest invoice, last 3 days: 150.000, Van B, Shop 2, on Mon 19 Jan (a bulk order)"
        assert "bulk" not in line(text, "Largest invoice, latest day")
        # the portal's own tile wins when the payload has it
        text = brief(world, overview=command_centre(daily_avg=33.5), again=True)["text"]
        assert cells(text, "Latest day")[4] == "30-day average business day 33.500"
        facts = json.loads((paths.exports_dir("2026-01-20") / "brief.json").read_text(encoding="utf-8"))
        assert facts["sales"]["average_business_day"]["note"] == "the Command Centre's daily tile"
    # a bulk order is an invoice AT or above the configured value (100.000 in the fixture)
    with data_root():
        at = World().sale(ANCHOR, VAN_B, "Shop 2", "ITEM-02 Charger", 100, qty=10)
        assert line(brief(at)["text"], "Largest invoice, latest day") == \
            "Largest invoice, latest day: 100.000, Van B, Shop 2 (a bulk order)"
    with data_root():
        under = World().sale(ANCHOR, VAN_B, "Shop 2", "ITEM-02 Charger", "99.999", qty=10)
        assert line(brief(under)["text"], "Largest invoice, latest day") == \
            "Largest invoice, latest day: 99.999, Van B, Shop 2"


@test("last 7 days and the last full week: inside, above and below the normal range")
def _():
    with data_root():
        above = World().sale(date(2026, 1, 14), VAN_A, "Shop 1", "ITEM-01 Cable", 90, qty=9)
        text = brief(above)["text"]
        assert cells(text, "Last 7 days")[2:] == ["240.000", "11", "4-week average 150.000; above the normal range"]
        assert cells(text, "Last full week")[2:] == ["240.000", "11", "4-week average 150.000; above the normal range"]
    with data_root():
        below = World().drop(lambda ln: ln["day"] in (date(2026, 1, 14), date(2026, 1, 15)))
        text = brief(below)["text"]
        assert cells(text, "Last 7 days")[2:] == ["90.000", "6", "4-week average 150.000; below the normal range"]
        assert cells(text, "Last full week")[2:] == ["90.000", "6", "4-week average 150.000; below the normal range"]
    with data_root():
        # one heavy week in the history widens the range: 240 in the last 7 days is inside it
        mixed = World().sale(date(2025, 12, 9), VAN_A, "Shop 1", "ITEM-01 Cable", 300, qty=30)
        mixed.sale(date(2026, 1, 19), VAN_A, "Shop 1", "ITEM-01 Cable", 90, qty=9)
        text = brief(mixed)["text"]
        assert cells(text, "Last 7 days")[2:] == ["240.000", "11", "4-week average 150.000; inside the normal range"]
        assert cells(text, "Last full week")[4] == "4-week average 150.000; inside the normal range"
        facts = json.loads((paths.exports_dir("2026-01-20") / "brief.json").read_text(encoding="utf-8"))
        assert len(facts["sales"]["weeks_before"]) == 12 and facts["sales"]["d7"]["xmr"]["points"] == 13
        assert facts["sales"]["weeks_before"].count("150.000") == 11 and "450.000" in facts["sales"]["weeks_before"]


@test("the four-week average is the four full weeks just before the last full week: not older ones, not the week itself")
def _():
    def heavy_week(day: date) -> World:
        return World().sale(day, VAN_A, "Shop 1", "ITEM-01 Cable", 300, qty=30)
    with data_root():
        # a heavy week five weeks back (7 - 13 Dec) is no part of the four
        text = brief(heavy_week(date(2025, 12, 9)))["text"]
        assert cells(text, "Last 7 days")[4].startswith("4-week average 150.000;")
        assert cells(text, "Last full week")[4].startswith("4-week average 150.000;")
    with data_root():
        # one three weeks back (21 - 27 Dec) is: 150 + 450 + 150 + 150 over 4, on both week lines
        text = brief(heavy_week(date(2025, 12, 23)))["text"]
        assert cells(text, "Last 7 days")[4] == "4-week average 225.000; inside the normal range"
        assert cells(text, "Last full week")[4] == "4-week average 225.000; inside the normal range"
        facts = json.loads((paths.exports_dir("2026-01-20") / "brief.json").read_text(encoding="utf-8"))
        assert facts["sales"]["four_week_average"] == {"bhd": "225.000",
                                                       "weeks": ["150.000", "450.000", "150.000", "150.000"]}
    with data_root():
        # the oldest of the twelve (19 - 25 Oct) is none of the four either
        text = brief(heavy_week(date(2025, 10, 21)))["text"]
        assert cells(text, "Last full week")[4].startswith("4-week average 150.000;")
    with data_root():
        # and a heavy last full week (11 - 17 Jan) leaves the average it is set beside alone
        text = brief(heavy_week(date(2026, 1, 13)))["text"]
        assert cells(text, "Last full week")[2:] == ["450.000", "11", "4-week average 150.000; above the normal range"]


@test("a range that starts at nought cannot judge a low week: the line says 'not above', never 'inside'")
def _():
    def uneven() -> World:
        """Every other week from 19 Oct to 28 Dec carries one order of 120.000: weeks of 270 and 150 in turn.
        Their range by the method: centre 210, a usual move of 120 between weeks, so 210 - 2.66 x 120 (under nought,
        so 0) up to 210 + 2.66 x 120 = 529.2."""
        world, week = World(), date(2025, 10, 19)
        while week < date(2026, 1, 4):
            world.sale(week + timedelta(days=1), VAN_A, "Shop 1", "ITEM-01 Cable", 120, qty=12)
            week += timedelta(days=14)
        return world
    with data_root():
        world = uneven().drop(lambda ln: date(2026, 1, 11) <= ln["day"] <= date(2026, 1, 17))   # a week with no sale
        text = brief(world)["text"]
        assert cells(text, "Last 7 days")[2:] == ["90.000", "6", "4-week average 210.000; not above the normal range"]
        assert cells(text, "Last full week") == ["Last full week", "Sun 11 Jan - Sat 17 Jan", "0.000", "0",
                                                 "4-week average 210.000; not above the normal range"]
        lines = text.splitlines()
        note = "The normal range starts at 0.000 here, so a low week cannot be judged: compare it with the 4-week average."
        assert lines[lines.index(line(text, "Month to date")) + 1] == note == sales.NOT_ABOVE_NOTE
        assert text.count(note) == 1 and "inside the normal range" not in text and "below the normal range" not in text
        facts = json.loads((paths.exports_dir("2026-01-20") / "brief.json").read_text(encoding="utf-8"))
        assert facts["sales"]["lfw"]["xmr"]["lower"] == "0.000" and facts["sales"]["lfw"]["xmr"]["upper"] == "529.200"
        assert facts["sales"]["lfw"]["reading"] == "not above the normal range"
    with data_root():
        # a week over the top of that range still reads above; the other line keeps its "not above" and the one note
        world = uneven().sale(date(2026, 1, 19), VAN_A, "Shop 1", "ITEM-01 Cable", 400, qty=40)
        text = brief(world)["text"]
        assert cells(text, "Last 7 days")[2:] == ["550.000", "11", "4-week average 210.000; above the normal range"]
        assert cells(text, "Last full week")[4] == "4-week average 210.000; not above the normal range"
        assert text.count(sales.NOT_ABOVE_NOTE) == 1
    with data_root():
        # the steady world has a range that does not reach nought: no such line there
        assert sales.NOT_ABOVE_NOTE not in brief(World())["text"]
    # the rule itself, on a week in the middle of a month: the range of weeks of 270 and 150 in turn is 0 to 529.2
    win = sales.Win("lfw", date(2026, 1, 11), date(2026, 1, 17))
    turn = [Decimal(270), Decimal(150)] * 6
    assert [sales.reading(win, turn, Decimal(v))[0] for v in ("0", "150", "529.2", "529.201")] == [
        "not above the normal range", "not above the normal range", "not above the normal range", "above the normal range"]
    near = [Decimal(150)] * 11 + [Decimal(170)]                    # a narrow range, well above nought
    assert [sales.reading(win, near, Decimal(v))[0] for v in ("100", "150", "200")] == [
        "below the normal range", "inside the normal range", "above the normal range"]
    assert sales.reading(win, [Decimal(0)] * 12, Decimal(0))[0] == "not above the normal range"
    assert sales.reading(win, [Decimal(0)] * 12, Decimal(1))[0] == "above the normal range"


@test("a week that holds one of the first or the last five business days of a month is not judged")
def _():
    jan = date(2026, 1, 1)
    assert sorted(sales.month_edge_days(jan)) == [date(2026, 1, d) for d in (1, 4, 5, 6, 7, 25, 26, 27, 28, 29)]
    assert sales.touches_month_edge(date(2026, 1, 4), date(2026, 1, 10)) is True        # 4-7 Jan: first five
    assert sales.touches_month_edge(date(2026, 1, 8), date(2026, 1, 14)) is False       # 8 Jan is the sixth
    assert sales.touches_month_edge(date(2026, 1, 11), date(2026, 1, 17)) is False
    assert sales.touches_month_edge(date(2026, 1, 18), date(2026, 1, 24)) is False
    assert sales.touches_month_edge(date(2026, 1, 19), date(2026, 1, 25)) is True       # 25 Jan: last five
    assert sales.touches_month_edge(date(2026, 1, 30), date(2026, 1, 31)) is False      # a weekend, no business day
    assert sales.touches_month_edge(date(2025, 12, 28), date(2026, 1, 3)) is True       # the turn of the month
    with data_root():
        # data to Wed 14 Jan: the last 7 days (8-14 Jan) are judged, the last full week (4-10 Jan) is not
        text = brief(World(to=date(2026, 1, 14)))["text"]
        assert cells(text, "Last 7 days")[4] == "4-week average 150.000; inside the normal range"
        assert cells(text, "Last full week")[4] == "4-week average 150.000; month-end week: not judged"
    with data_root():
        # data to Tue 3 Feb: both windows cross the month end, however large the week
        world = World(to=date(2026, 2, 3)).sale(date(2026, 1, 29), VAN_A, "Shop 1", "ITEM-01 Cable", 500, qty=50)
        text = brief(world)["text"]
        assert cells(text, "Last 7 days")[2:] == ["650.000", "11", "4-week average 150.000; month-end week: not judged"]
        assert cells(text, "Last full week")[2:] == ["650.000", "11", "4-week average 150.000; month-end week: not judged"]
        assert "normal range" not in text


@test("month to date against the same business days last month, in percent")
def _():
    with data_root():
        world = World().sale(date(2026, 1, 5), VAN_A, "Shop 3", "ITEM-03 Earphones", 210, qty=3)
        text = brief(world)["text"]
        assert cells(text, "Month to date") == ["Month to date", "Thu 01 Jan - Tue 20 Jan", "630.000", "29",
                                                "the same 14 business days last month 420.000: +50%"]
        assert line(text, "Month so far") == "Month so far: B2B 630.000, B2C 0.000; 29 invoices; 3 accounts invoiced"
        text = brief(world, "mtd")["text"]
        assert "against the same 14 business days last month (Mon 01 Dec 2025 - Thu 18 Dec 2025) 420.000: +50%" in text
    with data_root():
        # nothing sold in those days last month: no percentage is invented
        world = World().drop(lambda ln: date(2025, 12, 1) <= ln["day"] <= date(2025, 12, 18))
        assert cells(brief(world)["text"], "Month to date")[4] == "the same 14 business days last month 0.000: n/a"


@test("a month that has had no business day yet carries no percentage and calls no rep below")
def _():
    fri = date(2026, 5, 1)                               # the 1st of May 2026 is a Friday
    with data_root(last_load=loaded("020526_0900", fri)):
        world = World(to=date(2026, 4, 30))
        world.sale(fri, "Counter One", "Cash Customer", "ITEM-01 Cable", 7, channel="B2C")
        team = [rep_row("Van A", "2026-04-30", 0), rep_row("Van B", "2026-04-30", 0)]
        out = brief(world, overview=command_centre(team=team))
        text = out["text"]
        # a weekend's counter sale beside the first working day of April (30.000) would read -77%
        assert cells(text, "Month to date") == ["Month to date", "Fri 01 May", "7.000", "1",
                                                "no business day yet this month: not compared with last month"]
        assert "%" not in line(text, "Month to date")
        assert line(text, "REPS") == ("REPS      month to date not compared (no business day yet this month); "
                                      "0 with no invoice for over 5 business days")
        block = text.split("REPS")[1].split("STOCK")[0]
        assert "Van A" not in block and "Van B" not in block, "nobody is below a working day on a weekend's sales"
        facts = json.loads(Path(out["files"][1]).read_text(encoding="utf-8"))
        assert facts["sales"]["mtd"]["compare"]["compared"] is False and facts["sales"]["mtd"]["change"] is None
        assert facts["reps"]["below"] is None and facts["reps"]["salesmen"] == []
        shown = brief(world, "mtd")["text"].splitlines()
        assert "          no business day yet this month: not compared with last month" in shown
        assert not any("against" in ln or "%" in ln for ln in shown[:8])
    sun = date(2026, 2, 1)                               # the 1st of February 2026 is a Sunday: one business day
    with data_root(last_load=loaded("020226_0900", sun)):
        world = World(to=sun)
        text = brief(world, overview=command_centre(team=[rep_row("Van B", "2026-02-01", 0)]))["text"]
        assert cells(text, "Month to date")[4] == "the same 1 business day last month 30.000: 0%"
        assert line(text, "REPS").startswith("REPS      0 below 50% of the same 1 business day last month;")
        assert "against the same 1 business day last month (Thu 01 Jan) 30.000: 0%" in brief(world, "mtd")["text"]
        assert "1 business days" not in text


# ── pace and the target figures ─────────────────────────────────────────────────────────────────

def pace_world() -> World:
    return World().sale(date(2026, 1, 5), VAN_A, "Shop 3", "ITEM-03 Earphones", 210, qty=3)     # month to date 630


@test("pace: the KPI step in force, the portal target on the same line when it differs")
def _():
    step = {"sales.kpi_steps": [{"from": "2025-06", "bhd": 500}, {"from": "2026-01", "bhd": 900}]}
    with data_root(step):
        # 14 of January's 21 business days are done: (900 - 630) / 7 and (800 - 630) / 7 a day
        text = brief(pace_world())["text"]
        assert line(text, "Pace") == ("Pace: KPI step 900.000: 70.0% done, 38.571 a day still needed, 7 business days left; "
                                      "portal target 800.000: 24.286 a day still needed")
        assert "Four monthly figures" not in text
    with data_root(step):
        world = pace_world()
        world.portal_target = "900.0"                                  # the same figure: said once
        assert line(brief(world)["text"], "Pace") == \
            "Pace: KPI step 900.000: 70.0% done, 38.571 a day still needed, 7 business days left"
    with data_root(step):
        world = pace_world()
        world.portal_target = None                                     # no portal target at all
        assert "portal" not in line(brief(world)["text"], "Pace")
    with data_root({"sales.kpi_steps": [{"from": "2026-01", "bhd": 600}]}):
        assert line(brief(pace_world())["text"], "Pace") == (
            "Pace: KPI step 600.000: 105.0% done, reached, 7 business days left; "
            "portal target 800.000: 24.286 a day still needed")
    # the month's last business day (Thu 29 Jan, 21 days of 30.000): what is short is said once, and no
    # "0 business days left" after "no business day left"
    last_day = date(2026, 1, 29)
    with data_root(step, last_load=loaded("300126_0900", last_day)):
        assert line(brief(World(to=last_day))["text"], "Pace") == (
            "Pace: KPI step 900.000: 70.0% done, 270.000 short, no business day left; "
            "portal target 800.000: 170.000 short, no business day left")
    with data_root(last_load=loaded("300126_0900", last_day)):
        assert line(brief(World(to=last_day))["text"], "Pace") == \
            "Pace: KPI step not set; portal target 800.000: 170.000 short, no business day left"
    with data_root({"sales.kpi_steps": [{"from": "2026-01", "bhd": 600}]}, last_load=loaded("300126_0900", last_day)):
        assert line(brief(World(to=last_day))["text"], "Pace") == (
            "Pace: KPI step 600.000: 105.0% done, reached; portal target 800.000: 170.000 short, no business day left")


@test("a KPI step of nought is no step; an entry that cannot be read stops the command in one plain sentence")
def _():
    with data_root({"sales.kpi_steps": [{"from": "2026-01", "bhd": 0}]}):
        text = brief(pace_world())["text"]
        assert line(text, "Pace").startswith("Pace: KPI step not set; portal target 800.000:")
        assert "Four monthly figures exist." in text, "with no step in force he is still asked to choose"
    with data_root({"sales.kpi_steps": [{"from": "2026-01", "bhd": "900"}]}):          # a figure kept as text still counts
        assert line(brief(pace_world())["text"], "Pace").startswith("Pace: KPI step 900.000: 70.0% done")
    with data_root({"sales.kpi_steps": [{"from": "2026-02", "bhd": 900}]}):            # a step that starts next month
        assert line(brief(pace_world())["text"], "Pace").startswith("Pace: KPI step not set")
    with data_root({"sales.kpi_steps": [{"from": "2026-01", "bhd": "1,200"}]}):        # typed with its comma
        assert sales.kpi_step_for("2026-01") == Decimal(1200)
        assert line(brief(pace_world())["text"], "Pace").startswith(f"Pace: KPI step {fmt.bhd(1200)}: 52.5% done, ")
    # an entry that cannot be read: the config reader's own sentence, which names the key and the file
    for bad in ([{"from": "2026-01"}], [{"from": "2026-01", "bhd": "a lot"}], ["2026-01"], [None]):
        with data_root({"sales.kpi_steps": bad}) as root:
            world = World()
            said = str(raises(config.ConfigMissing, lambda w=world: brief(w)))
            assert said.startswith("config: every entry of 'sales.kpi_steps' is "), said
            assert said.endswith(str(root / "business_data" / "ops" / "config.json")) and "\n" not in said, said
            assert "sales" not in world.names() and not paths.exports_dir().exists(), "stopped before any sales read"
    # a figure that is no number at all is refused by the same reader, in a sentence of the same kind
    for junk in ("NaN", float("nan"), "Infinity", float("inf")):
        with data_root({"sales.kpi_steps": [{"from": "2026-01", "bhd": junk}]}) as root:
            world = World()
            said = str(raises(config.ConfigMissing, lambda w=world: brief(w)))
            assert said == ("config: 'sales.kpi_steps' holds a figure that is not a number in "
                            + str(root / "business_data" / "ops" / "config.json")), said
            assert "sales" not in world.names() and not paths.exports_dir().exists()
    with data_root({"sales.kpi_steps": [{"from": "2026-01"}]}) as root:
        with standing_in(World()):
            code, out = run_cli(["brief", "--data-root", str(root)])
            assert code == 1 and out.strip().startswith("config: every entry of 'sales.kpi_steps' is ")
            assert "\n" not in out.strip() and "Traceback" not in out, "one sentence, no traceback"
            code, out = run_cli(["brief", "7d", "--data-root", str(root)])
            assert code == 0, "a window has no pace line and does not read the steps"
    with data_root({"sales.kpi_steps": [{"from": "2026-01", "bhd": "NaN"}]}) as root:
        with standing_in(World()):
            code, out = run_cli(["brief", "--data-root", str(root)])
            assert code == 1 and out.strip().startswith("config: 'sales.kpi_steps' holds a figure that is not a number")
            assert "\n" not in out.strip() and "Traceback" not in out and "ERROR" not in out
    with data_root({"sales.kpi_steps": [{"from": "2025-06", "bhd": 500}, {"from": "2026-01", "bhd": 0}]}):
        # the step in force for a month is the newest one that has started. An entry of nought is no entry (the
        # config reader's rule), so it does not end the step before it: 500.000 stays in force
        assert sales.kpi_step_for("2025-05") is None and sales.kpi_step_for("2025-12") == Decimal(500)
        assert sales.kpi_step_for("2026-01") == Decimal(500) and sales.kpi_step_for("2026-03") == Decimal(500)
        assert line(brief(pace_world())["text"], "Pace").startswith("Pace: KPI step 500.000: 126.0% done, reached")
    with data_root({"sales.kpi_steps": [{"from": "2026-01", "bhd": -5}, {"from": "2026-01", "bhd": "-0.000"}]}):
        assert sales.kpi_step_for("2026-01") is None, "a figure under nought is no step either"
    with data_root({"sales.kpi_steps": [{"from": "2026-01", "bhd": -5}, {"from": "2026-01", "bhd": "-Infinity"}]}):
        said = str(raises(config.ConfigMissing, lambda: sales.kpi_step_for("2026-01")))
        assert said.startswith("config: 'sales.kpi_steps' holds a figure that is not a number in "), said


@test("KPI step not set: the four figures to choose from are printed exactly once")
def _():
    with data_root():
        world = pace_world()
        text = brief(world)["text"]
        assert line(text, "Pace") == \
            "Pace: KPI step not set; portal target 800.000: 24.286 a day still needed, 7 business days left"
        lines = text.splitlines()
        at = lines.index("  Four monthly figures exist. Say which one management measures you on (shown once):")
        rows = [re.split(r"\s{2,}", ln.strip()) for ln in lines[at + 1:at + 5]]
        # Oct, Nov and Dec 2025 hold 22, 21 and 23 business days of 30.000: 1,980 over the 3 months loaded
        assert rows == [["your KPI sheet's step", "(yours to say)"],
                        ["the portal's monthly target", "800.000"],
                        ["the tiers of 2 salesmen added up", "160.000 / 320.000 / 480.000"],
                        ["the average of the last 3 months", "660.000"]], rows
        assert status.read_state()["brief_targets_shown"] == "2026-01-20"
        again = brief(world, again=True)["text"]
        assert line(again, "Pace").startswith("Pace: KPI step not set") and "Four monthly figures" not in again
    with data_root():
        # the figures could not be read: nothing is marked as shown, the pace falls back on the payload
        world = pace_world()
        world.fail["targets"] = RuntimeError("canceling statement due to statement timeout")
        out = brief(world)
        assert line(out["text"], "Pace") == \
            "Pace: KPI step not set; portal target 800.000: 24.286 a day still needed, 7 business days left"
        assert "Four monthly figures" not in out["text"] and "brief_targets_shown" not in status.read_state()
        assert any("the target figures could not be read" in w for w in out["warnings"])


# ── accounts, reps, stock ───────────────────────────────────────────────────────────────────────

@test("accounts that bought last month and not this month: counted, valued, top three with their salesman")
def _():
    with data_root():
        world = World()
        world.sale(date(2025, 12, 10), VAN_A, "Shop 3", "ITEM-01 Cable", 70)
        world.sale(date(2025, 12, 11), VAN_B, "Shop 4", "ITEM-01 Cable", 40)
        world.sale(date(2026, 1, 6), VAN_B, "Shop 4", "ITEM-01 Cable", 40)                  # came back
        world.sale(date(2025, 12, 12), "Counter One", "Cash Customer", "ITEM-01 Cable", 99, channel="B2C")
        world.sale(date(2025, 12, 14), VAN_B, "Shop 5", "ITEM-01 Cable", 15)
        world.sale(date(2026, 1, 7), VAN_B, "Shop 5", "SIM-01 Starter", 60, division="SIM")  # a SIM is no accessory sale
        world.sale(date(2025, 12, 15), VAN_A, "Shop 6", "ITEM-09 Gift", 0, giveaway=True)     # a giveaway is no purchase
        world.sale(date(2025, 12, 16), VAN_A, "Shop 7", "ITEM-01 Cable", 30)
        world.sale(date(2025, 12, 17), VAN_B, "Shop 7", "ITEM-01 Cable", 5)                   # the latest line names the rep
        world.sale(date(2025, 12, 21), VAN_A, "Shop 8", "ITEM-01 Cable", 4)
        text = brief(world)["text"]
        assert line(text, "ACCOUNTS") == \
            "ACCOUNTS  4 accounts that bought last month have not bought this month (BHD 124.000 last month)"
        at = text.splitlines().index(line(text, "ACCOUNTS"))
        table = [re.split(r"\s{2,}", ln.strip()) for ln in text.splitlines()[at + 1:at + 5]]
        assert table == [["Largest of them", "Last month", "Salesman"], ["Shop 3", "70.000", "Van A"],
                         ["Shop 7", "35.000", "Van B"], ["Shop 5", "15.000", "Van B"]], table
        assert "Shop 8" not in text.split("ACCOUNTS")[1].split("REPS")[0], "three names, the rest is in the count"
        assert "Cash Customer" not in text.split("ACCOUNTS")[1].split("REPS")[0]
        facts = json.loads((paths.exports_dir("2026-01-20") / "brief.json").read_text(encoding="utf-8"))
        assert [a["account"] for a in facts["accounts_not_back"]["accounts"]] == ["Shop 3", "Shop 7", "Shop 5", "Shop 8"]
    with data_root():
        one = World().sale(date(2025, 12, 10), VAN_A, "Shop 3", "ITEM-01 Cable", 70)
        assert line(brief(one)["text"], "ACCOUNTS") == \
            "ACCOUNTS  1 account that bought last month has not bought this month (BHD 70.000 last month)"


@test("reps: below the configured share of the same business days last month; no invoice for N business days")
def _():
    with data_root():
        # Van B stops after Thu 8 Jan: 6 business days x 10 = 60 against 14 x 10 = 140, and 8 business days silent
        world = World().drop(lambda ln: ln["rep"] == VAN_B and ln["day"] > date(2026, 1, 8))
        world.sale(date(2026, 1, 6), "Counter One", "Cash Customer", "ITEM-01 Cable", 1, channel="B2C")
        team = [rep_row("Van A", "2026-01-20", 0), rep_row("Van B", "2026-01-08", 8), rep_row("Van C", None, None),
                rep_row("Counter One", "2025-11-02", 55, no_target=True),      # a counter: no target row
                rep_row("Van D", "2025-11-02", 55)]                             # switched off in the portal
        text = brief(world, overview=command_centre(team=team, active={"Van D": False, "Van A": True}))["text"]
        assert line(text, "REPS") == ("REPS      1 below 50% of the same 14 business days last month; "
                                      "2 with no invoice for over 5 business days")
        at = text.splitlines().index(line(text, "REPS"))
        table = [re.split(r"\s{2,}", ln.strip()) for ln in text.splitlines()[at + 1:at + 4]]
        assert table[0] == ["Salesman", "Month to date", "Same days last month", "Last invoice", "Business days since",
                            "To check"]
        assert table[1] == ["Van B", "60.000", "140.000", "Thu 08 Jan", "8", "below, no invoice"], table[1]
        assert table[2] == ["Van C", "0.000", "0.000", "none on record", "no invoice"], table[2]
        block = text.split("REPS")[1].split("STOCK")[0]
        assert "Van A" not in block and "Counter One" not in block and "Van D" not in block
    with data_root({"brief.rep_share_min": 0.4, "brief.silent_business_days": 9}):
        # the same month under other settings: 60 is not below 40% of 140, and 8 days is not over 9
        world = World().drop(lambda ln: ln["rep"] == VAN_B and ln["day"] > date(2026, 1, 8))
        text = brief(world, overview=command_centre(team=[rep_row("Van B", "2026-01-08", 8)]))["text"]
        assert line(text, "REPS") == ("REPS      0 below 40% of the same 14 business days last month; "
                                      "0 with no invoice for over 9 business days")
    with data_root({"brief.silent_business_days": 1}):
        assert line(brief(World(), overview=command_centre(team=[rep_row("Van B", "2026-01-18", 2)]))["text"], "REPS") \
            .endswith("1 with no invoice for over 1 business day")


@test("reps at the boundary: silent is over N business days, as the Command Centre counts it; below is under the share")
def _():
    with data_root():
        # exactly N business days without an invoice is not silent yet; N + 1 is (app.metrics.silent_reps: days > N;
        # the last test of this file runs the real Command Centre rule beside this one, at the boundary)
        team = [rep_row("Van A", "2026-01-13", 5), rep_row("Van B", "2026-01-12", 6)]
        text = brief(World(), overview=command_centre(team=team))["text"]
        assert line(text, "REPS").endswith("; 1 with no invoice for over 5 business days")
        block = text.split("REPS")[1].split("STOCK")[0]
        assert "Van B" in block and "Van A" not in block
    with data_root():
        # Van B invoices on the first 7 of the 14 business days: 70.000 is half of 140.000, and half is not below half
        world = World().drop(lambda ln: ln["rep"] == VAN_B and ln["day"] > date(2026, 1, 11))
        text = brief(world, overview=command_centre(team=[rep_row("Van B", "2026-01-11", 2)]))["text"]
        assert line(text, "REPS") == ("REPS      0 below 50% of the same 14 business days last month; "
                                      "0 with no invoice for over 5 business days")
        facts = json.loads((paths.exports_dir("2026-01-20") / "brief.json").read_text(encoding="utf-8"))
        assert facts["reps"]["salesmen"] == [] and facts["reps"]["below"] == 0
    with data_root():
        # one fils less and he is below
        world = World().drop(lambda ln: ln["rep"] == VAN_B and ln["day"] > date(2026, 1, 11))
        world.sale(date(2026, 1, 11), VAN_B, "Shop 2", "ITEM-02 Charger", "-0.001")
        text = brief(world, overview=command_centre(team=[rep_row("Van B", "2026-01-11", 2)]))["text"]
        assert line(text, "REPS").startswith("REPS      1 below 50% of the same 14 business days last month; 0 with")
        assert cells(text, "  Van B") == ["Van B", "69.999", "140.000", "Sun 11 Jan", "2", "below"]
    with data_root():
        # more reps than the table shows: five are named and the rest are counted
        team = [rep_row(f"Van {c}", None, None) for c in "CDEFGHIJ"]
        text = brief(World(), overview=command_centre(team=team))["text"]
        assert line(text, "REPS").endswith("; 8 with no invoice for over 5 business days")
        block = text.split("REPS")[1].split("STOCK")[0].splitlines()
        assert [ln.split()[1] for ln in block[2:7]] == ["C", "D", "E", "F", "G"] and block[7] == "  (+3 more)", block


@test("with the portal's own figure as the setting, a REPS count that differs from the Command Centre's item is said so")
def _():
    n = metrics.SILENT_AFTER_BUSINESS_DAYS
    team = [rep_row("Van A", "2026-01-20", 0), rep_row("Van B", "2026-01-08", n + 1), rep_row("Van C", None, None)]

    def item(count: int) -> dict:
        return attn("rep_silence", "warn", f"{count} reps with no Accessories invoice for over {n} business days",
                    count=count, basis="Active reps with a target")
    with data_root({"brief.silent_business_days": n}):
        # one rule on both sides, and both count two: nothing is said
        out = brief(World(), overview=command_centre(team=team, attention=[item(2)]))
        assert line(out["text"], "REPS").endswith(f"; 2 with no invoice for over {n} business days")
        assert out["warnings"] == []
        # the Command Centre counts one (a salesman with a target and no active record in the portal, say): a line
        # to check, never a silent disagreement between two lines of one screen
        out = brief(World(), overview=command_centre(team=team, attention=[item(1)]), again=True)
        assert out["warnings"] == [f"REPS counts 2 salesmen with no invoice for over {n} business days and the "
                                   "Command Centre's list counts 1: to check"]
        fmt.careful(out["warnings"][0])
        out = brief(World(), overview=command_centre(team=team, attention=[]), again=True)       # ... or none at all
        assert out["warnings"] == [f"REPS counts 2 salesmen with no invoice for over {n} business days and the "
                                   "Command Centre's list counts 0: to check"]
        # the attention list was not whole: there is nothing to compare, and only that is said
        out = brief(World(), overview=command_centre(team=team, attention=[item(1)], unavailable=["reps"]), again=True)
        assert out["warnings"] == ["the Command Centre could not read: reps; the action list was left as it stood"]
    with data_root({"brief.silent_business_days": n + 2}):
        # another setting: each line names its own figure and the two are not compared
        out = brief(World(), overview=command_centre(team=team, attention=[item(2)]))
        assert line(out["text"], "REPS").endswith(f"; 1 with no invoice for over {n + 2} business days")
        assert out["warnings"] == []
    one = {"silent": 1}
    assert sales.silent_count_differs(one, [item(1)], n) is None
    assert sales.silent_count_differs({"silent": 0}, [], n) is None, "none on either side"
    assert "counts 1 salesman with" in sales.silent_count_differs(one, [item(3)], n)
    assert sales.silent_count_differs({}, [item(3)], n) is None, "the team table was not read"
    assert sales.silent_count_differs(one, None, n) is None, "the attention list was not read"
    assert sales.silent_count_differs(one, [attn("rep_silence", "warn", "x")], n) is None, "an item with no count"
    assert sales.silent_count_differs(one, [item(3)], n + 1) is None, "another setting"


@test("stock, until the stock skill: the Command Centre's sold-out-with-demand list, top three")
def _():
    sold = [{"item": f"ITEM-{n:02d} Thing", "qty_60": 10 * n, "net_60_bhd": 100.0 - n} for n in range(1, 6)]
    with data_root():
        text = brief(World(), overview=command_centre(sold_out=sold))["text"]
        assert line(text, "STOCK") == "STOCK     5 items sold out, still asked for (BHD 485.000 sold in the last 60 days)"
        at = text.splitlines().index(line(text, "STOCK"))
        table = [re.split(r"\s{2,}", ln.strip()) for ln in text.splitlines()[at + 1:at + 5]]
        assert table == [["Largest of them", "Sold in 60 days", "BHD"], ["ITEM-01 Thing", "10", "99.000"],
                         ["ITEM-02 Thing", "20", "98.000"], ["ITEM-03 Thing", "30", "97.000"]], table
        assert "ITEM-04" not in text
    with data_root():
        assert line(brief(World())["text"], "STOCK") == \
            "STOCK     Nothing is sold out that was still asked for in the last 60 days"


# ── one salesman ────────────────────────────────────────────────────────────────────────────────

def rep_world() -> World:
    world = World()
    world.sale(date(2026, 1, 6), "Van A - SIM WH", "Shop 3", "ITEM-03 Earphones", 40, qty=2)   # his second warehouse
    world.sale(date(2026, 1, 6), "Van AB - Acc WH", "Shop 4", "ITEM-01 Cable", 77)               # another man
    return world


@test("rep:<name> narrows every figure to one salesman, his Focus names read as rep_matches reads them")
def _():
    with data_root():
        world = rep_world()
        out = brief(world, "mtd", "rep:van", "a")                          # case is forgiven
        text = out["text"]
        assert text.splitlines()[0].endswith("- salesman: Van A"), text.splitlines()[0]
        # 14 business days x 20 for the van, plus 40 from his second warehouse; never the 77 of Van AB
        assert line(text, "TOTAL") == "TOTAL     320.000 in 15 invoices; 30 units; B2B 320.000, B2C 0.000"
        assert cells(text, "Van A") == ["Van A", "320.000", "100.0%", "15", "30"]
        assert "Van AB" not in text and "Van B" not in text and "Shop 2" not in text and "Shop 4" not in text
        assert "against the same 14 business days last month (Mon 01 Dec 2025 - Thu 18 Dec 2025) 280.000: +14%" in text
        params = dict(world.calls)
        assert params["dim"][1] == "Van A" and "sales" not in params, "one salesman is read through DIM_SQL alone"
        assert Path(out["files"][0]).name == "brief-mtd-rep-van-a.txt"
        for typed in ("Van A", "VAN A - ACC WH", "van a - sim wh"):
            assert sales.resolve_rep(typed, [VAN_A, "Van A - SIM WH", "Van AB - Acc WH", VAN_B]).lower() == typed.lower()
        names = [VAN_A, "Van AB - Acc WH", VAN_B, "Counter One"]
        assert sales.resolve_rep("count", names) == "Counter One" and sales.resolve_rep("van b", names) == "Van B"
        assert "More than one salesman fits 'Van': Van A, Van AB, Van B" in str(
            raises(sales.BriefInput, lambda: sales.resolve_rep("Van", names)))
        out = brief(world, "mtd", "rep:Zed")
        assert out["ok"] is False and out["exit"] == 1
        assert out["text"] == "No salesman fits 'Zed'. Names in the sales data: Van A, Van AB, Van B."
        for focus, name, want in ((VAN_A, "Van A", True), ("Van AB - Acc WH", "Van A", False), ("Van A", "Van A", True)):
            assert metrics.rep_matches(focus, name) is want and World._rep({"rep": focus}, name) is want


@test("the full brief for one salesman: his sales lines and accounts, and none of the company lines")
def _():
    with data_root():
        world = rep_world().sale(date(2025, 12, 10), VAN_A, "Shop 5", "ITEM-01 Cable", 25)
        world.sale(date(2025, 12, 10), VAN_B, "Shop 6", "ITEM-01 Cable", 60)
        with standing_in(world) as st:
            out = sales.run(Args(["rep:Van A"]))
            assert st.overview_calls == 0
        text = out["text"]
        # his 30 days: 22 business days x 20, plus the 40 of 6 Jan, over 22
        assert cells(text, "Latest day") == ["Latest day", "Tue 20 Jan", "20.000", "1", "30-day average business day 21.818"]
        assert cells(text, "Last 7 days") == ["Last 7 days", "Wed 14 Jan - Tue 20 Jan", "100.000", "5",
                                              "4-week average 110.000; not judged for one salesman"]
        assert cells(text, "Month to date")[2:] == ["320.000", "15", "the same 14 business days last month 305.000: +5%"]
        assert line(text, "ACCOUNTS") == \
            "ACCOUNTS  1 of his accounts that bought last month has not bought this month (BHD 25.000 last month)"
        assert "Shop 6" not in text and "Pace:" not in text and "DO TODAY" not in text.replace("and DO TODAY are", "")
        assert text.splitlines()[-1] == \
            "Pace, reps, stock and DO TODAY are company lines: /yq-brief without rep: shows them."
        assert "last_brief_data_date" not in status.read_state() and not alerts.alerts_file().exists()
        assert Path(out["files"][0]).name == "brief-rep-van-a.txt"
        # a name with no Latin letter still gets a plain file name (its hash, as a register key would)
        other = sales.file_stem("2026-01", "\u0639\u0644\u064a")
        assert other.startswith("brief-2026-01-rep-h") and other.isascii() and other.replace("-", "").isalnum()
        assert sales.file_stem(None, None) == "brief" and sales.file_stem("2025-12-01..2025-12-07", None) == \
            "brief-2025-12-01-to-2025-12-07"
        assert sorted(world.names()) == ["anchor", "dim", "lapsed", "rep_names", "top"]


@test("one salesman: his four-week average is his own four full weeks before the last full week, not that week")
def _():
    with data_root():
        world = rep_world().sale(date(2026, 1, 13), VAN_A, "Shop 1", "ITEM-01 Cable", 80, qty=8)   # in the last full week
        text = brief(world, "rep:Van A")["text"]
        # his weeks before: 100, 100, 100 and 140 (the 40 of 6 Jan); the week itself, 180, is no part of its own average
        assert cells(text, "Last full week") == ["Last full week", "Sun 11 Jan - Sat 17 Jan", "180.000", "6",
                                                 "4-week average 110.000; not judged for one salesman"]
        asked = {w["k"]: (w["s"], w["e"]) for w in json.loads(dict(world.calls)["dim"][0])}
        assert [asked[k] for k in ("w4", "w3", "w2", "w1")] == [
            ("2025-12-14", "2025-12-20"), ("2025-12-21", "2025-12-27"), ("2025-12-28", "2026-01-03"),
            ("2026-01-04", "2026-01-10")]
        assert asked["lfw"] == ("2026-01-11", "2026-01-17")


# ── DO TODAY ────────────────────────────────────────────────────────────────────────────────────

def seven_items() -> list[dict]:
    """Seven attention items as the Command Centre gives them: each with its count (how many orders, accounts,
    items or reps it stands for) except the one that is a plain statement."""
    return [
        attn("orders_waiting", "alert", "3 orders waiting to be confirmed", 45.5, count=3,
             rows=[{"label": "YQ-2601-0003"}, {"label": "YQ-2601-0004"}, {"label": "YQ-2601-0005"}]),
        attn("unowned_ar", "alert", "BHD 300.000 over 90 days sits with no active rep", 300.0, count=4,
             rows=[{"label": "Van Gone"}]),
        attn("below_cost", "alert", "1 item selling below cost", None, count=1, rows=[{"label": "ITEM-07 Thing"}]),
        attn("sold_out", "warn", "2 items sold out with demand", 60.0, count=2),
        attn("rep_silence", "warn", "1 rep with no Accessories invoice for over 7 business days", None, count=1,
             rows=[{"label": "Van B"}], detail="Van B (since 8 Jan)", basis="Active reps with a target"),
        attn("delivered_no_invoice", "warn", "2 delivered orders with no Focus invoice found", 20.0, count=2),
        attn("stale_data", "info", "Focus data is out of date", None),
    ]


@test("DO TODAY: attention items go on the register by tone, never as a baseline, and five are listed")
def _():
    with data_root():
        out = brief(World(), overview=command_centre(attention=seven_items()))
        text = out["text"]
        state = alerts.current()
        assert sorted(state) == ["attn|below_cost", "attn|delivered_no_invoice", "attn|orders_waiting", "attn|rep_silence",
                                 "attn|sold_out", "attn|stale_data", "attn|unowned_ar"]
        assert {k: r["level"] for k, r in state.items()} == {
            "attn|orders_waiting": "ACT", "attn|unowned_ar": "ACT", "attn|below_cost": "ACT", "attn|sold_out": "CHECK",
            "attn|rep_silence": "CHECK", "attn|delivered_no_invoice": "CHECK", "attn|stale_data": "CHECK"}
        assert all(r["baseline"] is False and r["state"] == "open" for r in state.values())
        assert state["attn|unowned_ar"]["bhd"] == "300.000" and state["attn|below_cost"]["bhd"] is None
        # the count the Command Centre gave goes with each item: what an explained item is measured against
        assert {k.split("|")[1]: r["count"] for k, r in state.items()} == {
            "orders_waiting": 3, "unowned_ar": 4, "below_cost": 1, "sold_out": 2, "rep_silence": 1,
            "delivered_no_invoice": 2, "stale_data": None}
        # every order number is kept (the register compares them one by one); the table has room for two
        assert state["attn|orders_waiting"]["evidence"] == ["YQ-2601-0003", "YQ-2601-0004", "YQ-2601-0005"]
        assert state["attn|unowned_ar"]["evidence"] == [] and state["attn|rep_silence"]["evidence"] == []
        assert all("Van" not in k and "|" not in k.split("|", 1)[1] for k in state), "a key never holds a name"
        # nor does any other field: the Command Centre's detail and rows name reps, the register keeps the definition
        assert state["attn|rep_silence"]["detail"] == "Active reps with a target"
        assert "Van" not in alerts.alerts_file().read_text(encoding="utf-8")
        block = text.split("DO TODAY\n")[1].splitlines()
        assert block[0].split()[:3] == ["ID", "Level", "Who"]
        listed = [ln.split()[0:2] for ln in block[1:6]]
        assert [lv for _id, lv in listed] == ["ACT", "ACT", "ACT", "CHECK", "CHECK"], listed
        assert "BHD 300.000 over 90 days sits with no active rep" in block[1], "ACT first, the larger money first"
        assert re.split(r"\s{2,}", block[2]) == ["A1", "ACT", "3 orders waiting to be confirmed",
                                                 "YQ-2601-0003, YQ-2601-0004", "45.500"], block[2]
        assert "YQ-2601-0005" not in text, "two order numbers on the row, the third stays in the register"
        # the row that reads the profitability report carries a star; its basis is the line under the table
        assert block[3].split(None, 2) == ["A3", "ACT", "1 item selling below cost *"], block[3]
        assert block[6] == "* A3: Focus cost of sales, profitability report of Wed 21 Jan"
        assert block[7] == "(+2 more open)" and len(block) == 8
        assert max(len(ln) for ln in block) <= 120, "the table stays inside the window"
        assert out["flags"] == {"open": {"ACT": 3, "CHECK": 4},
                                "recorded": {"new": 7, "same": 0, "cleared": 0, "baseline": 0, "explained": 0}}
        # the rule ran once, and that first run listed what it found: no empty run was put before it
        saved = [json.loads(ln) for ln in alerts.alerts_file().read_text(encoding="utf-8").splitlines()]
        assert [(r["rule"], r["found"]) for r in saved if r.get("event") == "first_run"] == [("attn", 7)]
        assert len(saved) == 8 and alerts.baseline_summary() == [] and not alerts.explained_file().exists()
        facts = json.loads(Path(out["files"][1]).read_text(encoding="utf-8"))
        assert [(r["id"], r["count"]) for r in facts["do_today"]["open"][:3]] == [("A2", 4), ("A1", 3), ("A3", 1)]
        # the next brief: one item has gone, one is new, the rest stand as they were
        later = [i for i in seven_items() if i["key"] != "sold_out"]
        later.append(attn("invoiced_open", "warn", "1 invoiced order still open"))
        out = brief(World(), overview=command_centre(attention=later), again=True)
        assert out["flags"]["recorded"] == {"new": 1, "same": 6, "cleared": 1, "baseline": 0, "explained": 0}
        assert alerts.current()["attn|sold_out"]["state"] == "cleared"
        assert alerts.current()["attn|orders_waiting"]["id"] == state["attn|orders_waiting"]["id"]


@test("DO TODAY: the older-items line when another rule has a baseline; nothing is cleared on a failed read")
def _():
    old = [{"key": f"xfer_open|SIO:X-26-01-{n}", "level": "CHECK", "title": "Transfer not received yet: to check",
            "detail": "", "bhd": "5", "evidence": [f"SIO:X-26-01-{n}"]} for n in (1, 2)]
    with data_root():
        alerts.record("xfer_open", old, "2026-01-19")                  # that rule's first run: its baseline
        items = seven_items()[:2]
        text = brief(World(), overview=command_centre(attention=items))["text"]
        assert text.splitlines()[-1] == "2 older items counted once, not listed"
        assert "SIO:X-26-01-1" not in text, "a baseline item is counted, not listed"
        assert len(alerts.open_items()) == 2
        # the Command Centre answered in part: the list is left as it stood and the screen says so
        out = brief(World(), overview=command_centre(attention=[], unavailable=["orders"]), again=True)
        assert len(alerts.open_items()) == 2 and "part of the Command Centre did not answer" in out["text"]
        assert any("could not read: orders" in w for w in out["warnings"])
        # it did not answer at all: the sales lines still print, the register is untouched
        out = brief(World(), overview=RuntimeError("the read timed out"), again=True)
        assert out["ok"] is True and cells(out["text"], "Latest day")[2] == "30.000"
        assert len(alerts.open_items()) == 2 and "was not read this time" in out["text"]
        assert line(out["text"], "REPS").startswith("REPS      not read this time")
        assert line(out["text"], "STOCK") == "STOCK     not read this time"
        assert any("the Command Centre could not be read" in w for w in out["warnings"])
        # it answers again with nothing to do: now the two items are cleared
        brief(World(), overview=command_centre(attention=[]), again=True)
        assert alerts.open_items() == []


@test("a brief the Command Centre did not answer does not count as read: the next one prints the whole screen")
def _():
    with data_root():
        world = World()
        out = brief(world, overview=RuntimeError("canceling statement due to statement timeout"))
        assert "was not read this time" in out["text"] and line(out["text"], "STOCK") == "STOCK     not read this time"
        state = status.read_state()
        assert "last_brief_data_date" not in state and "last_brief_load_run" not in state and "recorded" not in out["flags"]
        assert state["brief_targets_shown"] == "2026-01-20", "the four figures were on that screen: shown once is shown"
        # the next plain /yq-brief: the whole screen, with the reps, the stock and the attention list this time
        out = brief(world, overview=command_centre(attention=seven_items()))
        assert out["text"].startswith("YQ ACCESSORIES BRIEF - data to Tue 20 Jan") and "(+2 more open)" in out["text"]
        assert out["flags"]["recorded"]["new"] == 7 and status.read_state()["last_brief_data_date"] == "2026-01-20"
        assert len(brief(world, overview=command_centre(attention=seven_items()))["text"].splitlines()) == 2
    with data_root():
        # a source the attention list is worked out from did not answer: nothing is recorded (an item missing from
        # the list may only be an item that could not be worked out), and the brief is not counted as read
        world = World()
        out = brief(world, overview=command_centre(attention=seven_items()[:1], unavailable=["below_cost", "customers"]))
        assert "(part of the Command Centre did not answer: the list below is as it stood)" in out["text"]
        assert "recorded" not in out["flags"] and not alerts.alerts_file().exists()
        assert "last_brief_data_date" not in status.read_state()
        assert out["warnings"] == ["the Command Centre could not read: below_cost; the action list was left as it stood",
                                   "the Command Centre could not read: customers (the action list does not use it)"]
        assert len(brief(world, overview=command_centre(attention=seven_items()))["text"].splitlines()) > 20
    with data_root():
        # a source the list never reads (the customer table, the landed margin) costs nothing here
        world = World()
        out = brief(world, overview=command_centre(attention=seven_items(), unavailable=["customers", "landed"]))
        assert "did not answer" not in out["text"] and out["flags"]["recorded"]["new"] == 7
        assert status.read_state()["last_brief_data_date"] == "2026-01-20"
        assert out["warnings"] == ["the Command Centre could not read: customers, landed (the action list does not use it)"]
    # every source the Command Centre's attention list is worked out from, by the reader's own names
    src = (ROOT / "app" / "metrics.py").read_text(encoding="utf-8")
    needed = ("anchor", "orders", "match", "salesmen", "reps", "attainment", "receivables", "ar_owner", "stock", "movers",
              "margin_totals", "below_cost")
    assert sorted(sales.ATTENTION_SOURCES) == sorted(needed)
    for name in needed:
        assert f'"{name}"' in src, f"app.metrics no longer reads a source called {name}"


@test("DO TODAY keeps the order numbers of waiting orders as evidence, and nothing that could be a name")
def _():
    items = [
        attn("orders_waiting", "alert", "4 orders waiting to be confirmed", 45.5,
             rows=[{"label": "Shop24"}, {"label": "YQ-2601-0003"}, {"label": "Van 7"}, {"label": "yqb-2601-00012"},
                   {"label": "YQ-2601-0005"}]),
        attn("rep_silence", "warn", "3 reps with no Accessories invoice for over 7 business days", None,
             rows=[{"label": "Zed"}, {"label": "Van7"}, {"label": "Rep-2"}, {"label": "YQ-2601-0009"}],
             basis="Active reps with a target"),
        attn("unowned_ar", "alert", "BHD 300.000 over 90 days sits with no active rep", 300.0,
             rows=[{"label": "Shop24"}, {"label": "No invoice on record"}]),
        attn("sold_out", "warn", "1 item sold out with demand", 16.0, rows=[{"label": "ITEM-07"}]),
    ]
    with data_root():
        out = brief(World(), overview=command_centre(attention=items))
        state = alerts.current()
        # order numbers by their shape (<prefix>-<yymm>-<counter>), every one of them, and only on the waiting orders
        assert state["attn|orders_waiting"]["evidence"] == ["YQ-2601-0003", "yqb-2601-00012", "YQ-2601-0005"]
        assert all(row["evidence"] == [] for key, row in state.items() if key != "attn|orders_waiting")
        saved = alerts.alerts_file().read_text(encoding="utf-8")
        block = out["text"].split("DO TODAY\n")[1]
        for name in ("Shop24", "Van7", "Van 7", "Rep-2", "Zed", "ITEM-07", "YQ-2601-0009", "No invoice on record"):
            assert name not in saved and name not in block, name
        # the screen prints the first two; the third is in the register and in the saved facts, not on the row
        assert "YQ-2601-0003, yqb-2601-00012" in block and "YQ-2601-0005" not in out["text"]
        assert sales.EVIDENCE_SHOWN == 2
        assert state["attn|orders_waiting"]["bhd"] == "45.500" and state["attn|unowned_ar"]["bhd"] == "300.000"
    for label, ok in (("YQ-2601-0003", True), ("AB-2612-12345", True), ("YQ-2601-003", False), ("YQ 2601 0003", False),
                      ("Van7", False), ("Shop-24", False), ("2601-0003", False), ("YQ-2601-0003 Shop 1", False),
                      ("TOOLONGX-2601-0003", False)):
        got = sales.attention_found([attn("orders_waiting", "alert", "x", rows=[{"label": label}])])[0]["evidence"]
        assert got == ([label] if ok else []), (label, got)
    assert sales.EVIDENCE_KEYS == ("orders_waiting",)
    # six orders listed, one of them twice: six numbers kept, each once, in the Command Centre's order
    six = [{"label": f"YQ-2601-{n:04d}"} for n in (1, 2, 3, 2, 4, 5, 6)]
    kept = sales.attention_found([attn("orders_waiting", "alert", "6 orders waiting to be confirmed", rows=six)])[0]
    assert kept["evidence"] == [f"YQ-2601-{n:04d}" for n in range(1, 7)]
    # the money and the count are what the Command Centre gave, or nothing: never a guess, never a nought for a blank
    for given, bhd in ((45.5, Decimal("45.500")), ("300", Decimal("300.000")), (0, Decimal("0.000")), (None, None),
                       ("n/a", None), (float("nan"), None), (True, None)):
        row = sales.attention_found([attn("sold_out", "warn", "x", bhd=given)])[0]
        assert (row["bhd"], row["count"]) == (bhd, None), (given, row["bhd"], row["count"])
        assert row["bhd"] is None or str(row["bhd"]) == f"{bhd:.3f}", "to the fils, as the register writes it"
    for given, count in ((3, 3), ("17", 17), (2.0, 2), (0, 0), (None, None), ("many", None), (2.5, None), (-1, None),
                         (True, None), (float("inf"), None)):
        assert sales.attention_found([attn("sold_out", "warn", "x", count=given)])[0]["count"] == count, given


@test("the row that reads the profitability report says its basis, the report's date and since when it was held back")
def _():
    items = [attn("below_cost", "alert", "1 item selling below cost", None, rows=[{"label": "ITEM-07 Thing"}],
                  basis="Ex-VAT sales under Focus COGS on the latest profitability report"),
             attn("sold_out", "warn", "2 items sold out with demand", 60.0)]

    def today(text: str) -> list[str]:
        """The DO TODAY block: its lines after the table's heading."""
        return text.split("DO TODAY\n")[1].splitlines()[1:]
    with data_root():
        # the report on file is as on Mon 12 Jan, the sales run to Tue 20 Jan, and no load says it held a report back
        out = brief(World(), overview=command_centre(attention=items, margin_report_date="2026-01-12"))
        basis = "Focus cost of sales, profitability report of Mon 12 Jan, older than the data to Tue 20 Jan"
        # on the screen: a star on the row, and the row's basis and dates on the line under the table, by its id
        rows = today(out["text"])
        assert [re.split(r"\s{2,}", ln) for ln in rows[:2]] == [["A1", "ACT", "1 item selling below cost *"],
                                                                 ["A2", "CHECK", "2 items sold out with demand", "60.000"]]
        assert rows[2:] == [f"* A1: {basis}"], rows
        # in the register: the whole sentence in the title, so every screen that lists the item prints it
        assert alerts.current()["attn|below_cost"]["title"] == f"1 item selling below cost ({basis})"
        assert alerts.current()["attn|below_cost"]["detail"] == \
            "Ex-VAT sales under Focus COGS on the latest profitability report"
        assert alerts.current()["attn|sold_out"]["title"] == "2 items sold out with demand", "no other row is touched"
        facts = json.loads((paths.exports_dir("2026-01-20") / "brief.json").read_text(encoding="utf-8"))
        assert facts["do_today"]["profit_basis"] == basis
        assert facts["do_today"]["open"][0]["title"] == f"1 item selling below cost ({basis})"
        # the same report the next morning: the same words, so the item stands as it was
        again = brief(World(), overview=command_centre(attention=items, margin_report_date="2026-01-12"), again=True)
        assert again["flags"]["recorded"] == {"new": 0, "same": 2, "cleared": 0, "baseline": 0, "explained": 0}
    held = loaded("210126_0910", ANCHOR, profit_held_since="2026-01-21")
    with data_root(last_load=held):
        # the load left the report of Wed 21 Jan out and wrote the day down: the row says since when
        text = brief(World(), overview=command_centre(attention=items, margin_report_date="2026-01-12"))["text"]
        assert today(text)[-1] == ("* A1: Focus cost of sales, profitability report of Mon 12 Jan, "
                                   "held back since Wed 21 Jan")
        assert alerts.current()["attn|below_cost"]["title"].endswith("of Mon 12 Jan, held back since Wed 21 Jan)")
    with data_root(last_load=held):
        # a report as new as that day is on file after all: nothing is held back any more
        text = brief(World(), overview=command_centre(attention=items, margin_report_date="2026-01-21"))["text"]
        assert today(text)[-1] == "* A1: Focus cost of sales, profitability report of Wed 21 Jan"
        assert "held back" not in text and "older than" not in text
    with data_root():
        text = brief(World(), overview=command_centre(attention=items, margin_report_date=None))["text"]
        assert today(text)[-1] == "* A1: Focus cost of sales, profitability report date not read"
    with data_root():
        # the row is not among the five listed: no line under the table for a row that is not on it
        many = [attn(f"k{n}", "alert", f"item {n}", 100.0 - n) for n in range(5)] + items[:1]
        text = brief(World(), overview=command_centre(attention=many, margin_report_date="2026-01-12"))["text"]
        assert today(text)[-1] == "(+1 more open)" and "*" not in "".join(today(text))
        assert alerts.current()["attn|below_cost"]["title"].endswith("older than the data to Tue 20 Jan)")
    # only a title that ends with the words this module adds is taken apart
    whole = "1 item selling below cost (Focus cost of sales, profitability report of Mon 12 Jan)"
    assert sales.basis_apart(whole) == ("1 item selling below cost",
                                        "Focus cost of sales, profitability report of Mon 12 Jan")
    for other in ("2 items sold out with demand", "Transfer not received yet: to check", "",
                  "(Focus cost of sales, profitability report of Mon 12 Jan)", whole + " and more"):
        assert sales.basis_apart(other) == (other, None), other
    d = ANCHOR
    assert sales.profit_basis(d, d, {}) == "Focus cost of sales, profitability report of Tue 20 Jan"
    assert sales.profit_basis(date(2025, 12, 30), d, {}) == \
        "Focus cost of sales, profitability report of Tue 30 Dec 2025, older than the data to Tue 20 Jan"
    for junk in ("not a day", "", None, "2026-01-12", "2026-01-05"):       # no day, or no later than the report on file
        state = {"last_load": {"profit_held_since": junk}}
        said = sales.profit_basis(date(2026, 1, 12), d, state)
        assert said.endswith("of Mon 12 Jan, older than the data to Tue 20 Jan"), junk
    assert sales.profit_basis(date(2026, 1, 12), d, {"last_load": {"profit_held_since": "2026-01-13"}}).endswith(
        "of Mon 12 Jan, held back since Tue 13 Jan")
    for words in (sales.profit_basis(d, d, {}), sales.profit_basis(None, d, {})):
        fmt.careful(words)


@test("rule 'attn' is recorded in one place only, and never as a baseline")
def _():
    # No other module records rule 'attn' itself. The register marks a rule's items cleared when a run no longer
    # finds them, so a second recorder with a list of its own would clear these on every run; and one that left
    # the baseline on (the register's default for a rule's first run) would hide the first finds for good.
    records_attn = re.compile(r"\brecord\(\s*(?:[\"']attn[\"']|(?:sales\.)?ATTN_RULE\b)")
    for path in sorted((ROOT / "scripts" / "ops").rglob("*.py")) + [ROOT / "scripts" / "yq_load_go.py"]:
        src = path.read_text(encoding="utf-8")
        hits = records_attn.findall(src)
        if path.name == "sales.py" and path.parent.name == "ops":
            assert len(hits) == 1, "sales.record_attention records the rule, in one call"
            assert re.search(r"alerts\.record\(ATTN_RULE,[^\n]*\bbaseline=False\)", src), "never a baseline"
            assert "_rule_has_run" not in src, "no empty first run is put before it any more"
        else:
            assert not hits, f"{path.name} records rule 'attn' itself: it must call sales.record_attention"
    assert sales.ATTN_RULE == "attn"
    with data_root():
        # the register is new and this is the rule's first run: what it finds is listed, not counted once and hidden
        counts = sales.record_attention([attn("sold_out", "warn", "2 items sold out with demand", 20.0, count=2)],
                                        "2026-01-20")
        assert counts == {"new": 1, "same": 0, "cleared": 0, "baseline": 0, "explained": 0}
        assert [r["key"] for r in alerts.open_items()] == ["attn|sold_out"] and alerts.baseline_summary() == []
        # the second run finds the same thing: it stands; the third finds nothing: it is cleared
        assert sales.record_attention([attn("sold_out", "warn", "2 items sold out with demand", 20.0, count=2)],
                                      "2026-01-21")["same"] == 1
        assert sales.record_attention([], "2026-01-22")["cleared"] == 1 and alerts.open_items() == []


@test("an explained item stays off while it is what he explained, and comes back when it is clearly larger")
def _():
    def sold(n: int, bhd: float | None = None) -> dict:
        return attn("sold_out", "warn", f"{n} items sold out with demand", 10.0 * n if bhd is None else bhd, count=n)

    def cost(n: int) -> dict:
        return attn("below_cost", "alert", f"{n} items selling below cost", count=n)

    def today(out: dict) -> str:
        return out["text"].split("DO TODAY\n")[1]
    with data_root():
        out = brief(World(), overview=command_centre(attention=[sold(4), cost(2)]))
        assert out["flags"]["recorded"] == {"new": 2, "same": 0, "cleared": 0, "baseline": 0, "explained": 0}
        item = alerts.find("attn|sold_out")
        assert (item["count"], item["bhd"]) == (4, "40.000")
        # he explains the sold-out item as it stands: 4 items, 40.000. The register keeps what he explained
        alerts.set_state(item["id"], "explained", "known: waiting for the shipment")
        assert alerts.explained()["attn|sold_out"]["facts"] == {"level": "CHECK", "bhd": "40.000", "count": 4,
                                                                "evidence": []}
        # the same condition the next mornings: as it was, smaller, and a quarter larger (5 items, 50.000), which
        # is not MORE than a quarter. It stays off the list each time, and the flags count it
        for n in (4, 3, 5):
            out = brief(World(), overview=command_centre(attention=[sold(n), cost(2)]), again=True)
            assert out["flags"]["recorded"] == {"new": 0, "same": 1, "cleared": 0, "baseline": 0, "explained": 1}, n
            assert "sold out with demand" not in today(out) and alerts.find("attn|sold_out")["state"] == "explained"
        assert [r["key"] for r in alerts.open_items()] == ["attn|below_cost"]
        # 6 items are more than a quarter over the 4 he explained (the money, 50.000, is not): it is back, as news
        out = brief(World(), overview=command_centre(attention=[sold(6, bhd=50.0), cost(2)]), again=True)
        assert out["flags"]["recorded"] == {"new": 1, "same": 1, "cleared": 0, "baseline": 0, "explained": 0}
        back = alerts.find("attn|sold_out")
        assert (back["state"], back["count"], back["id"], back.get("reopened"), back["baseline"]) == \
            ("open", 6, item["id"], True, False)
        assert "6 items sold out with demand" in today(out) and "attn|sold_out" not in alerts.explained()
        # the money alone brings it back as well. He explains it again, at 6 items and 50.000: at 62.500 (a quarter
        # more) it stays off, one fils over that it is back
        alerts.set_state(back["id"], "explained", "the shipment is on its way")
        out = brief(World(), overview=command_centre(attention=[sold(6, bhd=62.5), cost(2)]), again=True)
        assert out["flags"]["recorded"]["explained"] == 1 and "sold out with demand" not in today(out)
        out = brief(World(), overview=command_centre(attention=[sold(6, bhd=62.501), cost(2)]), again=True)
        assert out["flags"]["recorded"]["new"] == 1 and "6 items sold out with demand" in today(out)
        assert alerts.find("attn|sold_out")["bhd"] == "62.501" and alerts.explained() == {}


@test("explained waiting orders come back when an order waits that was not there, not when an old one is confirmed")
def _():
    def orders(*numbers: str, bhd: float = 30.0) -> dict:
        return attn("orders_waiting", "alert", f"{len(numbers)} orders waiting to be confirmed", bhd, count=len(numbers),
                    rows=[{"label": no} for no in numbers])
    first, second, third, fourth = (f"YQ-2601-000{n}" for n in (1, 2, 3, 4))
    with data_root():
        brief(World(), overview=command_centre(attention=[orders(first, second, third)]))
        alerts.set_state("attn|orders_waiting", "explained", "three test orders")
        assert alerts.explained()["attn|orders_waiting"]["facts"]["evidence"] == [first, second, third]
        # the oldest of the three is confirmed. Nothing new waits, so it stays off: the third order was there when
        # he explained it (a list cut to the two numbers the screen shows would have called it new)
        out = brief(World(), overview=command_centre(attention=[orders(second, third, bhd=20.0)]), again=True)
        assert out["flags"]["recorded"]["explained"] == 1 and "Nothing open." in out["text"]
        assert alerts.find("attn|orders_waiting")["state"] == "explained"
        # another order waits now. Three again and the same money, but one of them was not there: it is back
        out = brief(World(), overview=command_centre(attention=[orders(second, third, fourth)]), again=True)
        assert out["flags"]["recorded"] == {"new": 1, "same": 0, "cleared": 0, "baseline": 0, "explained": 0}
        row = alerts.find("attn|orders_waiting")
        assert (row["state"], row["evidence"], row.get("reopened")) == ("open", [second, third, fourth], True)
        block = out["text"].split("DO TODAY\n")[1]
        assert f"{second}, {third}" in block and fourth not in out["text"], "two numbers on the row, as ever"


@test("an item he marks done rests until its facts change")
def _():
    def cost(n: int) -> dict:
        return attn("below_cost", "alert", f"{n} items selling below cost", count=n)
    with data_root():
        brief(World(), overview=command_centre(attention=[cost(2)]))
        alerts.set_state(alerts.find("attn|below_cost")["id"], "done")
        out = brief(World(), overview=command_centre(attention=[cost(2)]), again=True)
        assert alerts.open_items() == [] and "Nothing open." in out["text"]
        assert out["flags"]["recorded"] == {"new": 0, "same": 1, "cleared": 0, "baseline": 0, "explained": 0}
        # a third item sells below cost: the facts changed, so it is open again
        out = brief(World(), overview=command_centre(attention=[cost(3)]), again=True)
        assert [r["key"] for r in alerts.open_items()] == ["attn|below_cost"]
        assert "3 items selling below cost *" in out["text"] and out["flags"]["recorded"]["new"] == 1
        # ... and so does another profitability report under the same count: its date is part of what the row says
        alerts.set_state("attn|below_cost", "done")
        assert alerts.open_items() == []
        out = brief(World(), overview=command_centre(attention=[cost(3)], margin_report_date="2026-01-12"), again=True)
        assert out["flags"]["recorded"]["new"] == 1 and len(alerts.open_items()) == 1
        assert "* A1: Focus cost of sales, profitability report of Mon 12 Jan, older than the data to Tue 20 Jan" \
            in out["text"]


@test("a read that fails costs its own line only; without the sales read there is no brief")
def _():
    with data_root():
        world = World()
        for name in ("dim", "trend", "top", "lapsed"):
            world.fail[name] = RuntimeError("canceling statement due to statement timeout")
        team = [rep_row("Van B", "2026-01-08", 8)]
        out = brief(world, overview=command_centre(team=team))
        text = out["text"]
        assert out["ok"] is True and cells(text, "Month to date")[2:4] == ["420.000", "28"]
        assert cells(text, "Last 7 days")[2:] == ["150.000", "10", "weekly totals not read"]
        assert line(text, "Largest invoice, latest day") == "Largest invoice, latest day: not read this time"
        assert line(text, "Month so far").endswith("28 invoices; accounts invoiced: not read")
        assert line(text, "ACCOUNTS") == "ACCOUNTS  not read this time"
        assert line(text, "REPS") == ("REPS      month to date by salesman not read this time; "
                                      "1 with no invoice for over 5 business days")
        assert len(out["warnings"]) == 4 and all("could not be read" in w for w in out["warnings"])
        assert "normal range" not in text and "4-week average" not in text
    with data_root():
        world = World()
        world.fail["sales"] = RuntimeError("canceling statement due to statement timeout")
        exc = raises(RuntimeError, lambda: brief(world))
        assert "the sales figures could not be read" in str(exc)
        assert "last_brief_data_date" not in status.read_state() and not paths.exports_dir().exists()


# ── no new data, files, state ───────────────────────────────────────────────────────────────────

@test("no new data since the last brief: two lines only, unless 'again'")
def _():
    with data_root() as root:
        (root / "Uplaods" / "210126").mkdir(parents=True)
        world = World()
        first = brief(world)
        assert len(first["text"].splitlines()) > 20 and status.read_state()["last_brief_data_date"] == "2026-01-20"
        world.calls.clear()
        with standing_in(world) as st:
            out = sales.run(Args())
            assert st.overview_calls == 0 and world.names() == ["anchor"], "one small read, nothing else"
        assert out["text"].splitlines() == [
            "Data to Tue 20 Jan (loaded Wed 21 Jan 09:10): nothing new since the last brief. "
            "For the same brief again: /yq-brief again",
            "Waiting: no drop folder newer than the last load."]
        assert out["ok"] is True and out["files"] == [] and out["data_date"] == "2026-01-20"
        (root / "Uplaods" / "220126").mkdir()
        assert brief(world)["text"].splitlines()[1] == "Waiting: new reports in 220126: run /yq-load."
        for full in (brief(world, again=True), brief(world, "again")):
            assert full["text"].startswith("YQ ACCESSORIES BRIEF - data to Tue 20 Jan")
        assert brief(world, "mtd")["text"].startswith("YQ ACCESSORIES BRIEF - Month to date:"), "a window always answers"
        # a second load of the same day (the part day completed) is new data on the same date
        status.write_state({"last_load": {"run": "210126_1700", "finished_at": "2026-01-21T17:05:00+03:00",
                                          "result": "ok", "data_date": "2026-01-20"}})
        out = brief(world)
        assert out["text"].startswith("YQ ACCESSORIES BRIEF - data to Tue 20 Jan - loaded Wed 21 Jan 17:05")
        assert len(brief(world)["text"].splitlines()) == 2 and status.read_state()["last_brief_load_run"] == "210126_1700"
        world.sale(date(2026, 1, 21), VAN_A, "Shop 1", "ITEM-01 Cable", 20)        # a new day is loaded
        out = brief(world)
        assert out["text"].startswith("YQ ACCESSORIES BRIEF - data to Wed 21 Jan - load time not recorded")
        assert status.read_state()["last_brief_data_date"] == "2026-01-21"
    with data_root():
        # a window or one salesman never counts as "the brief was read"
        world = World()
        brief(world, "7d")
        brief(world, "rep:Van A")
        assert "last_brief_data_date" not in status.read_state()
        assert brief(world)["text"].startswith("YQ ACCESSORIES BRIEF - data to")


@test("a last load on record that is not an object costs the load time on the head line, never the brief")
def _():
    with data_root():
        for junk in ("210126_0910", ["210126_0910"], 7):
            status.write_state({"last_load": junk})
            world = World()
            out = brief(world, again=True)
            text = out["text"]
            assert out["ok"] is True, junk
            assert text.splitlines()[0] == "YQ ACCESSORIES BRIEF - data to Tue 20 Jan - load time not recorded", junk
            assert cells(text, "Latest day")[:4] == ["Latest day", "Tue 20 Jan", "30.000", "2"] and "Part day" not in text
            assert status.read_state()["last_brief_load_run"] == "", junk
            two = brief(world)["text"].splitlines()
            assert len(two) == 2 and two[0].startswith("Data to Tue 20 Jan: nothing new since the last brief."), two
    junk_state = {"last_load": "210126_0910"}
    assert sales.export_day(ANCHOR, {"stock_as_of": None}, junk_state) is None
    assert sales.loaded_text(junk_state, ANCHOR) is None
    assert sales.profit_basis(date(2026, 1, 12), ANCHOR, junk_state).endswith("older than the data to Tue 20 Jan")


@test("the screen and the facts are saved under exports/ only; the state remembers the data date")
def _():
    with data_root() as root:
        before = {p for p in root.rglob("*") if p.is_file()}
        code_before = {p.name for p in (ROOT / "scripts" / "ops").iterdir()}
        out = brief(World(), overview=command_centre(attention=seven_items()))
        folder = root / "exports" / "ops" / "2026-01-20"
        assert [Path(f) for f in out["files"]] == [folder / "brief.txt", folder / "brief.json"]
        assert (folder / "brief.txt").read_text(encoding="utf-8") == out["text"] + "\n"
        facts = json.loads((folder / "brief.json").read_text(encoding="utf-8"))
        assert facts["data_date"] == "2026-01-20" and facts["kind"] == "full" and facts["part_day"] is False
        assert facts["sales"]["mtd"]["bhd"] == "420.000" and facts["sales"]["mtd"]["compare"]["bhd"] == "420.000"
        assert facts["sales"]["d7"]["reading"] == "inside the normal range"
        assert facts["sales"]["lfw"]["month_end_week"] is False
        assert facts["do_today"]["recorded"]["new"] == 7 and len(facts["do_today"]["open"]) == 7
        brief(World(), "2025-12")
        brief(World(), "2025-12-01..2025-12-07", "rep:Van B")
        new = sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file() and p not in before)
        assert new == ["business_data/ops/alerts.jsonl",
                       "exports/ops/2026-01-20/brief-2025-12-01-to-2025-12-07-rep-van-b.json",
                       "exports/ops/2026-01-20/brief-2025-12-01-to-2025-12-07-rep-van-b.txt",
                       "exports/ops/2026-01-20/brief-2025-12.json", "exports/ops/2026-01-20/brief-2025-12.txt",
                       "exports/ops/2026-01-20/brief.json", "exports/ops/2026-01-20/brief.txt"], new
        state = json.loads((root / "business_data" / "ops" / "state.json").read_text(encoding="utf-8"))
        assert state["last_brief_data_date"] == "2026-01-20" and state["last_load"]["run"] == "210126_0910"
        assert {p.name for p in (ROOT / "scripts" / "ops").iterdir()} == code_before, "nothing lands beside the code"
        assert json.dumps(out), "the result is plain JSON for --json"


@test("a window in detail: totals, by salesman, top accounts, top products")
def _():
    with data_root():
        world = World()
        for n in range(3, 16):                                         # thirteen more shops and items in December
            world.sale(date(2025, 12, 9), VAN_B, f"Shop {n}", f"ITEM-{n:02d} Thing", n, qty=n)
        out = brief(world, "last-month", top=3)
        text = out["text"]
        assert text.splitlines()[0] == (
            "YQ ACCESSORIES BRIEF - Last month (December 2025): Mon 01 Dec 2025 - Wed 31 Dec 2025 "
            "- data to Tue 20 Jan - loaded Wed 21 Jan 09:10")
        # 23 business days x 30 = 690, plus 3 + 4 + ... + 15 = 117
        assert line(text, "TOTAL") == "TOTAL     807.000 in 59 invoices; 186 units; B2B 807.000, B2C 0.000"
        assert "15 accounts invoiced 807.000; cash customers 0.000 in 0 invoices" in text
        assert cells(text, "BY SALESMAN") == ["BY SALESMAN", "BHD", "Share", "Invoices", "Units"]
        assert cells(text, "Van A") == ["Van A", "460.000", "57.0%", "23", "46"]
        assert cells(text, "Van B") == ["Van B", "347.000", "43.0%", "36", "140"]
        assert cells(text, "TOP ACCOUNTS") == ["TOP ACCOUNTS (3 of 15)", "BHD", "Share", "Invoices"]
        assert cells(text, "Shop 1 ") == ["Shop 1", "460.000", "57.0%", "23"]
        assert cells(text, "TOP PRODUCTS") == ["TOP PRODUCTS (3 of 15)", "BHD", "Share", "Units"]
        assert cells(text, "ITEM-15") == ["ITEM-15 Thing", "15.000", "1.9%", "15"]
        assert "Shop 14" not in text and "against" not in text, "no comparison is invented for a whole month"
        assert out["warnings"] == [] and "DO TODAY" not in text
        facts = json.loads(Path(out["files"][1]).read_text(encoding="utf-8"))
        assert len(facts["accounts"]) == 15 and len(facts["products"]) == 15 and facts["total"]["bhd"] == "807.000"
        empty = brief(world, "2025-10-03..2025-10-04")                 # a Friday and a Saturday
        assert "No sales are loaded in this window." in empty["text"] and empty["ok"] is True


@test("a salesman table that does not add up to the window's total is said so, as something to check")
def _():
    with data_root():
        world = World()
        assert brief(world, "2025-12")["warnings"] == []
        world.hide_in_dim = VAN_B                                      # the per-salesman read loses one man's lines
        out = brief(world, "2025-12")
        # December: 23 business days of 20.000 for Van A; the total still holds Van B's 230.000
        assert out["ok"] is True and line(out["text"], "TOTAL").startswith("TOTAL     690.000 in 46 invoices")
        assert out["warnings"] == ["the salesman table adds up to 460.000 and the total is 690.000: to check"]
        fmt.careful(out["warnings"][0])


# ── format, wording, privacy ────────────────────────────────────────────────────────────────────

@test("money is to the fils everywhere: three decimals, never two, never a float's tail")
def _():
    with data_root({"sales.kpi_steps": [{"from": "2026-01", "bhd": 900}]}):
        world = World().sale(ANCHOR, VAN_A, "Shop 3", "ITEM-03 Earphones", "12.3456", qty=3)
        world.sale(date(2025, 12, 10), VAN_A, "Shop 9", "ITEM-01 Cable", "0.1")
        sold = [{"item": "ITEM-08 Thing", "qty_60": 4, "net_60_bhd": 16.5}]
        team = [rep_row("Van B", "2026-01-08", 8)]
        texts = [brief(world, overview=command_centre(attention=seven_items(), sold_out=sold, team=team))["text"],
                 brief(world, "mtd")["text"], brief(world, "7d", "rep:Van A")["text"]]
        for text in texts:
            for ln in text.splitlines():
                for m in re.finditer(r"\d[\d,]*\.\d+", ln):
                    after = ln[m.end():m.end() + 1]
                    decimals = len(m.group(0).split(".")[1])
                    assert decimals == (1 if after == "%" else 3), (m.group(0), ln)
        assert cells(texts[0], "Latest day")[2] == "42.346" and "12.3456" not in texts[0]


@test("no line can hold a word the wording guard refuses, whatever a name in the data says")
def _():
    with data_root():
        world = World()
        world.sale(ANCHOR, VAN_A, "Leak Proof Phones", "ITEM-05 Anti-Theft Lock", 400, qty=2)
        world.sale(date(2025, 12, 10), VAN_A, "Stolen Moments Cafe", "ITEM-01 Cable", 55)
        items = [attn("below_cost", "alert", "1 item selling below cost", None, basis="Sales under cost: no leakage meant"),
                 attn("sold_out", "warn", "1 item sold out with demand: Fraud Guard", 9.0)]
        sold = [{"item": "ITEM-06 Leak Detector", "qty_60": 3, "net_60_bhd": 9.0}]
        outs = [brief(world, overview=command_centre(attention=items, sold_out=sold)), brief(world, "day"),
                brief(world, "day", "rep:Van A")]
        for out in outs:
            assert out["ok"] is True
            for ln in out["text"].splitlines():
                fmt.careful(ln)                                         # raises on a refused word
        assert "Largest invoice, latest day: 400.000, Van A, ---- Proof Phones (a bulk order)" in outs[0]["text"]
        assert "ITEM-05 ---------- Lock" in outs[1]["text"] and "------ Moments Cafe" in outs[0]["text"]
        assert alerts.current()["attn|below_cost"]["detail"] == "Sales under cost: no ------- meant"
        assert alerts.current()["attn|sold_out"]["title"] == "1 item sold out with demand: ----- Guard"
        for row in alerts.current().values():
            fmt.careful(row["title"])
            fmt.careful(row["detail"] or "")
        assert sales._guarded("Transfer not received yet: to check") == "Transfer not received yet: to check"
        # the two-line answer goes through the same guard, whatever its lines are built from
        kept = sales.nothing_new
        sales.nothing_new = lambda d, state: ["Data to Tue 20 Jan: nothing new. Fraud Guard", "Waiting: Stolen Moments."]
        try:
            two = brief(world)
        finally:
            sales.nothing_new = kept
        assert two["text"].splitlines() == ["Data to Tue 20 Jan: nothing new. ----- Guard", "Waiting: ------ Moments."]


@test("no long digit run reaches the screen or the saved files: a number typed into a name is masked")
def _():
    with data_root() as root:
        world = World()
        nine, sixteen = "98765" + "4321", "4111" + "1" * 12                # invented; built here, never spelt out
        world.sale(ANCHOR, VAN_A, f"Shop 77 call {nine}", "ITEM-01 Cable", 60)
        world.sale(date(2025, 12, 10), VAN_A, f"Shop 78 card {sixteen}", "ITEM-01 Cable", 55)
        outs = [brief(world), brief(world, "day"), brief(world, "last-month", "rep:Van A")]
        saved = "".join(p.read_text(encoding="utf-8") for p in (root / "exports").rglob("*") if p.is_file())
        for text in [o["text"] for o in outs] + [saved]:
            assert not re.search(r"(?<!\d)\d{9}(?!\d)", text) and not re.search(r"\d{15,}", text)
        assert "Shop 77 call [number]" in outs[0]["text"] and "Shop 78 card [number]" in outs[0]["text"]


@test("the statements: reads only, one statement each, the one basis, no column named t, at most 8 parameters")
def _():
    own = {"DIM_SQL": sales.DIM_SQL, "TOP_INVOICE_SQL": sales.TOP_INVOICE_SQL, "LAPSED_SQL": sales.LAPSED_SQL,
           "TARGETS_SQL": sales.TARGETS_SQL, "PREV_DAY_SQL": sales.PREV_DAY_SQL, "REP_NAMES_SQL": sales.REP_NAMES_SQL}
    for name, sql in own.items():
        db.check_sql(sql)
        assert not sql.rstrip().endswith(";") and ";" not in sql, name
        assert not re.search(r"\bAS\s+t\b", sql, re.I) and not re.search(r"[\s,]t\.", sql), name
        used = {int(n) for n in re.findall(r"\$(\d+)", sql)}
        assert used <= set(range(1, 9)) and used == set(range(1, len(used) + 1)), (name, used)
        assert "v_sales" in sql
    for name in ("DIM_SQL", "TOP_INVOICE_SQL", "LAPSED_SQL", "TARGETS_SQL"):
        assert "v.division NOT IN ('SIM', 'Devices') AND NOT v.is_giveaway" in own[name], name
        assert "net_bhd" in own[name]
    assert "GROUPING SETS ((x.rep), (x.account), (x.item))" in sales.DIM_SQL
    assert "jsonb_to_recordset($1::jsonb)" in sales.DIM_SQL and "jsonb_to_recordset($1::jsonb)" in sales.TOP_INVOICE_SQL
    assert "NOT v.is_cash_customer" in sales.LAPSED_SQL
    for sql in (sales.DIM_SQL, sales.TOP_INVOICE_SQL, sales.LAPSED_SQL):
        assert "starts_with(v.salesman_resolved," in sql and " LIKE " not in sql, "a typed name is never a pattern"
    for table in ("customer_contacts", "leads", "salesmen", "orders ", "order_lines"):
        assert all(table not in sql for sql in own.values()), table
    src = (ROOT / "scripts" / "ops" / "sales.py").read_text(encoding="utf-8")
    assert "config.number(\"brief.rep_share_min\")" in src and "config.get(\"brief.silent_business_days\")" in src
    assert "config.kpi_step(" in src and "use_cache=False" in src and "today_bahrain" not in src


# ── the command line, and the whole command through the guard ───────────────────────────────────

def run_cli(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli.main(argv)
    return code, buf.getvalue()


@test("the entry point: python -m scripts.ops brief [window] [rep:<name>] [--again] [--top N] [--json]")
def _():
    with data_root() as root:
        world = World()
        with standing_in(world):
            code, out = run_cli(["brief", "--data-root", str(root)])
            assert code == 0 and out.splitlines()[0].startswith("YQ ACCESSORIES BRIEF - data to Tue 20 Jan")
            code, out = run_cli(["brief", "--data-root", str(root)])
            assert code == 0 and len(out.strip().splitlines()) == 2 and "nothing new since the last brief" in out
            code, out = run_cli(["brief", "--again", "--data-root", str(root)])
            assert code == 0 and "DO TODAY" in out
            code, out = run_cli(["brief", "mtd", "rep:Van", "B", "--top", "1", "--data-root", str(root)])
            assert code == 0 and "salesman: Van B" in out and "TOP ACCOUNTS (1 of 1)" in out
            # the rows as a word, in the middle of the others
            code, out = run_cli(["brief", "last-month", "top:1", "rep:Van", "A", "--data-root", str(root)])
            assert code == 0 and "salesman: Van A" in out and "TOP PRODUCTS (1 of 1)" in out, out
            code, out = run_cli(["brief", "last-month", "top:1", "--top", "5", "--data-root", str(root)])
            assert code == 0 and "TOP ACCOUNTS (1 of 2)" in out, "the word wins over the option"
            code, out = run_cli(["brief", "0000-01", "--data-root", str(root)])
            assert code == 1 and out.strip() == "'0000-01' is not a month. Write a month as 2026-09."
            code, out = run_cli(["brief", "fortnight", "--data-root", str(root)])
            assert code == 1 and out.strip().startswith("I do not know the window 'fortnight'.")
            code, out = run_cli(["brief", "7d", "--json", "--data-root", str(root)])
            payload = json.loads(out)
            assert code == 0 and payload["command"] == "brief" and payload["data_date"] == "2026-01-20"
            assert payload["text"].startswith("YQ ACCESSORIES BRIEF - Last 7 days:") and out.isascii()


@test("a window, a salesman and the options may be typed in any order: one request, one screen")
def _():
    with data_root() as root:
        where = ["--data-root", str(root)]
        with standing_in(World()):
            # `brief mtd rep:<name>` and `brief rep:<name> mtd`
            base = run_cli(["brief", "mtd", "rep:Van", "B"] + where)
            assert base[0] == 0 and base[1].splitlines()[0].startswith("YQ ACCESSORIES BRIEF - Month to date: ")
            assert base[1].splitlines()[0].endswith(" - salesman: Van B")
            for argv in (["brief", "rep:Van", "B", "mtd"], ["brief", "rep:van b", "MTD"], ["brief", "mtd rep:Van B"]):
                assert run_cli(argv + where) == base, argv
            # an option before the words, between them, and in the middle of a name of two words
            for argv in (["brief"] + where + ["mtd", "rep:Van", "B"], ["brief", "mtd"] + where + ["rep:Van", "B"],
                         ["brief", "rep:Van"] + where + ["B", "mtd"]):
                assert run_cli(argv) == base, argv
            # the case the command line used to refuse: an option, then one more word
            code, out = run_cli(["brief", "mtd", "--top", "3", "rep:Van", "B"] + where)
            assert code == 0 and out == base[1], "one account and one product either way: the same screen"
            # --top is read wherever it stands (December has two accounts and two products)
            one_row = run_cli(["brief", "last-month", "--top", "1"] + where)
            assert one_row[0] == 0 and "TOP ACCOUNTS (1 of 2)" in one_row[1] and "TOP PRODUCTS (1 of 2)" in one_row[1]
            for argv in (["brief", "--top", "1", "last-month"] + where, ["brief"] + where + ["last", "--top", "1", "month"],
                         ["brief", "last-month"] + where + ["top:1"], ["brief", "top:1", "last", "month"] + where):
                assert run_cli(argv) == one_row, argv
            assert "TOP ACCOUNTS (2 of 2)" in run_cli(["brief", "last-month"] + where)[1]
            # --json and --again as well
            code, out = run_cli(["brief", "7d", "--json", "rep:Van", "A"] + where)
            payload = json.loads(out)
            assert code == 0 and payload["text"].splitlines()[0].startswith("YQ ACCESSORIES BRIEF - Last 7 days: ")
            assert payload["text"].splitlines()[0].endswith(" - salesman: Van A")
            first = run_cli(["brief"] + where)[1]
            assert first.startswith("YQ ACCESSORIES BRIEF - data to Tue 20 Jan") and "DO TODAY" in first
            assert len(run_cli(["brief"] + where)[1].strip().splitlines()) == 2
            again = run_cli(["brief", "--again"] + where)[1]
            assert again.startswith("YQ ACCESSORIES BRIEF - data to Tue 20 Jan") and "DO TODAY" in again
            for argv in (["brief"] + where + ["--again"], ["brief"] + where + ["again"], ["brief", "again"] + where):
                assert run_cli(argv)[1] == again, argv
            # what cannot be read is still refused in a plain sentence, wherever the option stands
            code, out = run_cli(["brief", "mtd", "--top", "3", "fortnight"] + where)
            assert code == 1 and out.strip().startswith("I do not know the window 'fortnight'.")


@test("a load that failed part-way puts its banner above the brief, and the brief still prints")
def _():
    said = "LAST LOAD FAILED at refresh (run 210126_0910): the database may hold part of it: run /yq-load"
    with data_root() as root:
        kept = status.interrupted_banner
        status.interrupted_banner = lambda: said                 # what scripts.ops.status answers after such a load
        try:
            with standing_in(World()):
                code, out = run_cli(["brief", "--data-root", str(root)])
                shown = out.splitlines()
                assert code == 0 and shown[0] == said and shown[1] == "", shown[:2]
                assert shown[2] == "YQ ACCESSORIES BRIEF - data to Tue 20 Jan - loaded Wed 21 Jan 09:10"
                assert "DO TODAY" in out and cells(out, "Month to date")[2] == "420.000"
                code, out = run_cli(["brief", "mtd", "--json", "--data-root", str(root)])
                payload = json.loads(out)
                assert code == 0 and payload["banner"] == said and payload["text"].startswith("YQ ACCESSORIES BRIEF - ")
                # the two-line answer carries it too
                code, out = run_cli(["brief", "--data-root", str(root)])
                assert code == 0 and out.splitlines()[0] == said and "nothing new since the last brief" in out
        finally:
            status.interrupted_banner = kept


# ── no pointer to a command that is not built ───────────────────────────────────────────────────

ENRICHED = {                     # what scripts/ops/enrich.py adds for the e-mail, in its shape (invented rows)
    "team": [{"salesman": VAN_A, "name": "Van A", "mtd_bhd": "280.000", "change_pct": 5.0, "invoices": 14, "shops": 1,
              "last_invoice": "2026-01-20", "business_days_since_invoice": 0, "target_bhd": "100.000",
              "achieved_pct": 280.0, "needed_per_business_day_bhd": "0.000", "shops_due": 0},
             {"salesman": VAN_B, "name": "Van B", "mtd_bhd": "60.000", "change_pct": None, "invoices": 6, "shops": 1,
              "last_invoice": "2026-01-08", "business_days_since_invoice": 8, "target_bhd": "100.000",
              "achieved_pct": 60.0, "needed_per_business_day_bhd": "5.714", "shops_due": 1}],
    "push": [{"item": "ITEM-01 Cable", "units_30_days": 40, "units_30_before": 10, "bhd_30_days": "200.000",
              "rise_bhd": "150.000", "in_stock": 300, "days_cover": 120}],
    "movers": {"rising": [], "falling": []},
    "receivables": {"total_bhd": "500.000", "accounts": 4, "over_90_pct": 12.5, "over_90_bhd": "62.500",
                    "top_overdue": [{"account": "Shop 1", "overdue_bhd": "40.000", "over_90_bhd": "20.000",
                                     "last_receipt": "2026-01-01"}]},
    "orders": {"orders": 2, "bhd": "40.000", "oldest_hours": 30},
    "shops_due": {"due": 1, "lapsed": 0, "monthly_value_bhd": "60.000", "by_rep": {VAN_B: 1},
                  "shops": [{"shop": "Shop 2", "rep": VAN_B, "days_since": 40, "usual_gap_days": 14.0,
                             "monthly_value_bhd": "60.000"}]},
    "discounts": {"evidenced": False, "why": "the Focus sales export carries no discount values"},
    "stock_shape": None, "not_read": [],
}
LOAD_ROW = {"run": "210126_0910", "result": "ok", "as_on": {"stock_balance": "2026-01-21", "receivables": "2026-01-21"},
            "verify": "passed", "reconcile": "matched", "gate": "passed", "timings": {}, "total_seconds": 300.0}
MADE_ON = date(2026, 1, 21)


def older_items(n: int) -> list[dict]:
    """Items of another rule, found on that rule's first run: the register counts them once, it does not list them."""
    return [{"key": f"xfer_open|SIO:X-26-01-{k}", "level": "CHECK", "title": "Transfer not received yet: to check",
             "detail": "", "bhd": "5", "evidence": [f"SIO:X-26-01-{k}"]} for k in range(1, n + 1)]


def the_email(world: World, overview: dict) -> dict:
    """The management e-mail of the invented world, as the delivery makes it: the brief's own facts (a preview,
    so the register stays as it is), the ranked actions, the team text; both parts of it, and what went in."""
    with standing_in(world, overview):
        shown = sales.full_screen(record=False)
    facts = dict(shown["facts"], enriched=json.loads(json.dumps(ENRICHED)))
    top, every = actions.ranked(facts, shown["d"])
    team = render.team_text(facts, MADE_ON)
    return {"facts": facts, "top": top, "every": every, "team": team,
            "html": render.email_html(facts, top, every, LOAD_ROW, team, MADE_ON),
            "text": render.email_text(facts, top, every, LOAD_ROW, team, MADE_ON)}


def commands_by_state() -> tuple[list[str], list[str]]:
    """(built, not built) among the commands scripts/ops/__main__.py lists. A command that is not built is the one
    __main__ answers with "'x' is not built yet": the module it names is not there."""
    built, gone = [], []
    for name, (module, _about) in cli.COMMANDS.items():
        try:
            there = importlib.util.find_spec(module) is not None
        except ModuleNotFoundError:                     # a package above the module is not there either
            there = False
        (built if there else gone).append(name)
    return built, gone


def skills_on_disk() -> set[str]:
    return {p.parent.name for p in (ROOT / ".claude" / "skills").glob("yq-*/SKILL.md")}


def dead_pointers(text: str, built: list[str], gone: list[str], skills: set[str]) -> list[str]:
    """Where `text` sends a reader to something that is not there: a /yq-<word> that is no skill, a
    `scripts.ops <word>` line for a command that is not built, or "the <command> command" for one that is not."""
    found = [f"/{s}" for s in re.findall(r"/(yq-[a-z]+)", text) if s not in skills]
    found += [f"scripts.ops {w}" for w in re.findall(r"scripts\.ops\s+([a-z]+)", text) if w not in built]
    for name in gone:
        found += re.findall(rf"\b{name}\s+command\b(?!\s+centre)", text, re.I)
    return found


@test("the full list is not built: the register and the e-mail say how many more there are and point nowhere")
def _():
    with data_root():
        brief(World(), overview=command_centre(attention=seven_items()))
        items = alerts.open_items()
        assert len(items) == 7
        cut = alerts.render(items, limit=3).splitlines()
        assert cut[-1] == "(+4 more open)" and sum(1 for ln in cut if ln.startswith("A")) == 3, cut
        for whole in (alerts.render(items).splitlines(), alerts.render(items, limit=7).splitlines()):
            assert not whole[-1].startswith("(+"), "a list that fits says nothing of the rest"
        assert alerts.render(items, limit=6).splitlines()[-1] == "(+1 more open)"
        assert alerts.render([]) == "Nothing open."
        # the e-mail: the five that rank first, and a count of the others
        mail = the_email(World(), command_centre(attention=seven_items()))
        assert len(mail["top"]) == actions.TOP and mail["every"] - len(mail["top"]) >= 2
        n = mail["every"] - len(mail["top"])
        assert f"({n} more on the action list)" in mail["text"].splitlines(), mail["text"]
        assert f">{n} more on the action list.</p>" in mail["html"]
        for body in (mail["text"], mail["html"]):
            assert "Claude Code)" not in body and "action list:" not in body, "the count points nowhere"
        shown = render.email_text(mail["facts"], mail["top"], len(mail["top"]), LOAD_ROW, None, MADE_ON)
        assert "more on the action list" not in shown, "nothing is left over when every action is on the e-mail"
        assert "Nothing is open on the action list." in render.email_text(mail["facts"], [], 0, LOAD_ROW, None, MADE_ON)
    with data_root():
        # the older items (another rule's first finds) are a count too: one is "item", not "items"
        alerts.record("xfer_open", older_items(1), "2026-01-19")
        text = brief(World(), overview=command_centre(attention=seven_items()[:2]))["text"]
        assert text.splitlines()[-1] == "1 older item counted once, not listed" and "more open" not in text
        assert "SIO:X-26-01-1" not in text, "a baseline item is counted, not listed"
    # the skill no longer waits for a screen to name a command that is not built, and says what to tell him instead
    skill = (ROOT / ".claude" / "skills" / "yq-brief" / "SKILL.md").read_text(encoding="utf-8")
    assert "If the screen names a command that is not built yet" not in skill
    assert "say that it arrives in a later phase" in skill and "/yq-watch" not in skill


@test("nothing a person reads points at a command that is not built: brief, register, e-mail, notes, skill")
def _():
    built, gone = commands_by_state()
    skills = skills_on_disk()
    assert {"status", "brief", "load", "deliver"} <= set(built) and not set(built) & set(gone), (built, gone)
    assert {"yq-brief", "yq-load"} <= skills, skills
    for name in gone:                                   # __main__ itself reports it, and runs nothing
        code, said = run_cli([name])
        assert code == 1 and said.strip() == f"'{name}' is not built yet.", (name, code, said)
    # the finder bites (with the lists given, skills too, so these do not move when a command or a skill is built):
    # the old lines are caught; the commands and skills that exist, and the Command Centre, are not
    here, away, these = ["brief", "status", "load"], ["watch", "stock", "moves"], {"yq-brief", "yq-load"}
    assert dead_pointers("(+2 more: /yq-watch)", here, away, these) == ["/yq-watch"]
    assert dead_pointers("2 older items counted once: /yq-watch", here, away, these) == ["/yq-watch"]
    assert dead_pointers("see /yq-week and /yq-month", here, [], these) == ["/yq-week", "/yq-month"]
    assert dead_pointers("run python -m scripts.ops stock", here, away, these) == ["scripts.ops stock"]
    assert dead_pointers("run python -m scripts.ops frobnicate", here, [], these) == ["scripts.ops frobnicate"]
    assert dead_pointers("the Moves command lists them", here, away, these) == ["Moves command"]
    assert dead_pointers("type /yq-brief again, or /yq-load; python -m scripts.ops status; the stock Command Centre",
                         here, away, these) == []
    texts: dict[str, str] = {}
    with data_root() as root:
        alerts.record("xfer_open", older_items(2), "2026-01-19")           # another rule's first run: older items
        (root / "Uplaods" / "220126").mkdir(parents=True)                  # a drop folder waits to be loaded
        world = World()
        centre = command_centre(attention=seven_items(), team=[rep_row("Van B", "2026-01-08", 8)],
                                sold_out=[{"item": "ITEM-08 Thing", "qty_60": 4, "net_60_bhd": 16.5}])
        texts["the full brief"] = brief(world, overview=centre)["text"]
        assert "(+2 more" in texts["the full brief"] and "older items counted once" in texts["the full brief"]
        texts["the brief, nothing new"] = brief(world)["text"]             # two lines: the data date, what is waiting
        assert len(texts["the brief, nothing new"].splitlines()) == 2
        for spec in (("mtd",), ("7d", "rep:Van A"), ("rep:Van A",), ("fortnight",), ("2026-13",), ("again",)):
            texts["brief " + " ".join(spec)] = brief(world, *spec, overview=centre)["text"]
        texts["no team message yet"] = sales.team_message()
        items = alerts.open_items()
        texts["the register, cut"] = alerts.render(items, limit=2)
        texts["the register, whole"] = alerts.render(items)
        mail = the_email(world, centre)
        texts["the e-mail"], texts["the e-mail, plain text"] = mail["html"], mail["text"]
        assert mail["every"] > len(mail["top"]), "the line that says how many more is among them"
        texts["the e-mail, nothing open"] = (render.email_html(mail["facts"], [], 0, LOAD_ROW, None, MADE_ON)
                                             + render.email_text(mail["facts"], [], 0, LOAD_ROW, None, MADE_ON))
        texts["the e-mail, no names"] = render.email_html(mail["facts"], mail["top"], mail["every"], LOAD_ROW,
                                                          mail["team"], MADE_ON, test=True, names=False)
        texts["the team message"] = mail["team"]
        texts["the team message page"] = render.whatsapp_page(mail["team"], MADE_ON)
        for kind in ("look", "failed"):
            texts[f"the note, {kind}"] = (render.note_text(kind, "210126_0910", ["reconcile"])
                                          + render.note_html(kind, "210126_0910", ["reconcile"]))
    texts["the skill"] = (ROOT / ".claude" / "skills" / "yq-brief" / "SKILL.md").read_text(encoding="utf-8")
    assert all(text.strip() for text in texts.values()), [label for label, text in texts.items() if not text.strip()]
    caught = {label: dead_pointers(text, built, gone, skills) for label, text in texts.items()}
    assert not any(caught.values()), {label: found for label, found in caught.items() if found}


SALESMEN = [{"id": 1, "name": "Van A", "focus_name": "Van A", "is_active": True, "referral_code": None},
            {"id": 2, "name": "Van B", "focus_name": "Van B", "is_active": True, "referral_code": None}]


def _target_row(name: str, net: float, last: str) -> dict:
    return {"salesman": name, "target_period": "", "team": "normal", "target_bhd": 100, "tier2_bhd": 200, "tier3_bhd": 300,
            "kickback_t1": 0.01, "kickback_t2": 0.02, "kickback_t3": 0.03, "net_bhd": net, "gross_bhd": net * 1.1,
            "invoices": 5, "shops": 1, "last_sale": last, "no_target": False, "period": "2026-01",
            "data_through": "2026-01-20"}


def portal_answer(world: World, sql: str, params: list | None, van_b_last: str = "2026-01-08",
                  report_date: str = "2026-01-21", below_cost: int = 0) -> list[dict]:
    """What the rest of the Command Centre reads, in a few rows: Van B's last invoice (8 Jan unless
    said), one item sold out that still sold, the profitability report's date and how many of its
    items sell below cost. Any statement not named here gets no rows."""
    s = sql.strip()
    if s == metrics.ANCHOR_SQL:
        return [{"focus_to": "2026-01-20", "focus_from": START.isoformat(), "stock_as_of": "2026-01-21",
                 "ar_as_of": "2026-01-21", "margin_report_date": report_date, "target_bhd": "800.0",
                 "shop_vat_rate": "0.10", "price_drop_days": None}]
    if s == reports.ATTAINMENT_SQL.strip():
        return [_target_row("Van A", 280.0, "2026-01-20"), _target_row("Van B", 60.0, van_b_last)]
    if "::text AS day" in s:                              # the daily tile's read (a later portal release)
        return json.loads(json.dumps(world._daily(params)))
    if "AS month_named_bhd" in s:
        return [{"rep": VAN_A, "last_invoice": "2026-01-20", "month_net_bhd": 280, "month_named_bhd": 280},
                {"rep": VAN_B, "last_invoice": van_b_last, "month_net_bhd": 60, "month_named_bhd": 60}]
    if "AS qty_30" in s:
        return [{"item_name": "ITEM-09 Thing", "qty_30": 0, "net_30": 0, "qty_prev": 4, "net_prev": 16, "qty_60": 4,
                 "net_60": 16}]
    if "FROM v_stock_health h" in s:
        return [{"item_name": "ITEM-09 Thing", "division": "Accessories", "status": "out", "current_stock": 0,
                 "sell_value_bhd": 0, "sold_90d": 4, "days_cover": 0, "cost_bhd": 1.0, "cost_source": "mrn"}]
    if "AS invoiced_open" in s:
        return [{"eligible": 0, "matched": 0, "unmatched_bhd": 0, "awaiting": 0, "awaiting_bhd": 0, "invoiced_open": 0,
                 "last_order_at": None}]
    if "FROM v_product_margin" in s and "COUNT(*) AS n" in s:
        return [{"n": 3 if below_cost else 0, "below": below_cost, "net": 0, "net_ex": 0, "gp_ex": 0, "gp_rep": 0}]
    if "FROM v_product_margin" in s and "is_below_cost" in s and "ORDER BY gp_ex_vat_bhd" in s and below_cost:
        return [{"item_name": "ITEM-07 Thing", "net_amount_bhd": 11.0, "cogs_bhd": 12.0, "gp_ex_vat_bhd": -2.0,
                 "margin_ex_vat_pct": -20.0, "is_below_cost": True}]
    return []


def through_the_guard(world: World, **portal) -> tuple[dict, list[str], list[tuple[str, list | None]]]:
    """sales.run on the guarded client over an in-memory transport, with the real app.metrics.overview:
    the world answers the brief's own statements, portal_answer the rest of the Command Centre's.
    Returns (the result, the requests seen, the statements sent)."""
    seen: list[str] = []
    statements: list[tuple[str, list | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        path = request.url.path
        if request.method == "GET" and path.endswith("/salesmen"):
            return httpx.Response(200, json=SALESMEN)
        if request.method == "POST" and path.endswith(("/rpc/run_readonly_query", "/rpc/run_readonly_query_params")):
            body = json.loads(request.content)
            sql, params = body["sql_text"], body.get("params")
            statements.append((sql, params))
            rows = world.answer(sql, params)
            return httpx.Response(200, json=rows if rows is not None else portal_answer(world, sql, params, **portal))
        return httpx.Response(404, json={"message": "not a read this test expects"})

    assert not db.installed(), "an earlier test built a client: every test above must stay on its stand-ins"
    metrics.invalidate()
    db.install(inner=httpx.MockTransport(handler), url="https://ci.invalid", key="ci-key")
    try:
        out = sales.run(Args())
    finally:
        db.uninstall()
    return out, seen, statements


@test("the whole brief through the guarded client: the real Command Centre build, reads only, nothing written")
def _():
    # the setting is the portal's own figure for a silent rep, so the REPS line and the attention item must agree
    portal_n = metrics.SILENT_AFTER_BUSINESS_DAYS
    over, at = date(2026, 1, 8), date(2026, 1, 11)            # Van B's last invoice: one day over the figure, and at it
    assert metrics.business_days(over + timedelta(days=1), ANCHOR) == portal_n + 1, "restate the two days for the new figure"
    assert metrics.business_days(at + timedelta(days=1), ANCHOR) == portal_n
    cfg = {"brief.silent_business_days": portal_n}
    world = World().drop(lambda ln: ln["rep"] == VAN_B and ln["day"] > over)
    with data_root(cfg):
        out, seen, statements = through_the_guard(world, report_date="2026-01-12", below_cost=1)
        text = out["text"]
        assert out["ok"] is True and out["warnings"] == [], out["warnings"]
        assert cells(text, "Latest day")[:4] == ["Latest day", "Tue 20 Jan", "20.000", "1"]
        assert cells(text, "Month to date")[2] == "340.000"            # 14 x 20 for Van A, 6 x 10 for Van B
        assert line(text, "Pace") == \
            "Pace: KPI step not set; portal target 800.000: 65.714 a day still needed, 7 business days left"
        assert line(text, "REPS") == ("REPS      1 below 50% of the same 14 business days last month; "
                                      f"1 with no invoice for over {portal_n} business days")
        assert cells(text, "  Van B") == ["Van B", "60.000", "140.000", "Thu 08 Jan", str(portal_n + 1), "below, no invoice"]
        assert line(text, "STOCK") == "STOCK     1 item sold out, still asked for (BHD 16.000 sold in the last 60 days)"
        # what the Command Centre itself flags on this world goes on the register: one to act on, the rest to check
        state = alerts.current()
        assert {k: r["level"] for k, r in state.items()} == {
            "attn|below_cost": "ACT", "attn|rep_silence": "CHECK", "attn|sold_out": "CHECK", "attn|stale_data": "CHECK"}
        assert all(r["state"] == "open" and r["evidence"] == [] for r in state.values())
        assert "1 item sold out with demand" in text
        assert f"1 rep with no Accessories invoice for over {portal_n} business days" in text, "the same rule, the same man"
        # the profitability report's date is the one the Command Centre's own anchor read gave
        assert state["attn|below_cost"]["title"] == ("1 item selling below cost (Focus cost of sales, profitability report "
                                                     "of Mon 12 Jan, older than the data to Tue 20 Jan)")
        star = state["attn|below_cost"]["id"]
        assert (f"* {star}: Focus cost of sales, profitability report of Mon 12 Jan, older than the data to Tue 20 Jan"
                in text.splitlines())
        assert [ln for ln in text.splitlines() if ln.startswith(star + " ")][0].endswith("1 item selling below cost *")
        saved = alerts.alerts_file().read_text(encoding="utf-8")
        assert "Van" not in saved and "ITEM-07" not in saved and "ITEM-09" not in saved, "no rep and no item by name"
        # the read-only proof: GET and the two read RPCs only, every statement one SELECT with at most 8 parameters
        assert seen and all(s.startswith("GET ") or "/rpc/run_readonly_query" in s for s in seen), sorted(set(seen))
        assert sum(1 for s in seen if s.startswith("GET ")) == 1, "the rep names: one GET through the same guard"
        for sql, params in statements:
            assert re.match(r"\s*(select|with)\b", sql, re.I) and ";" not in sql, sql[:60]
            assert params is None or len(params) <= 8
        sent = [sql for sql, _p in statements]
        assert sent.count(metrics.SALES_SQL) == 2, "the Command Centre's own read and the brief's one read"
        assert sent.count(sales.DIM_SQL) == 1 and sent.count(sales.TARGETS_SQL) == 1 and sent.count(sales.LAPSED_SQL) == 1
        assert sent.count(metrics.ANCHOR_SQL) == 1, "one Command Centre build"
        assert not db.installed()
    with data_root(cfg):
        # at the figure itself neither calls him silent: the REPS line and the Command Centre's item count alike
        out, _seen, _sent = through_the_guard(world, van_b_last=at.isoformat())
        assert out["ok"] is True and out["warnings"] == [], out["warnings"]
        assert line(out["text"], "REPS") == ("REPS      1 below 50% of the same 14 business days last month; "
                                             f"0 with no invoice for over {portal_n} business days")
        assert cells(out["text"], "  Van B") == ["Van B", "60.000", "140.000", "Sun 11 Jan", str(portal_n), "below"]
        assert sorted(alerts.current()) == ["attn|sold_out", "attn|stale_data"], sorted(alerts.current())
        assert "with no Accessories invoice" not in out["text"]


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
    db.uninstall()
    paths.set_data_root(None)
    config.use(None)
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
