"""Ops engine: the action list after a load (python -m scripts.ops watch, scripts/ops/checks.py) on a stand-in
movement audit.

    python -m tests.test_ops_watch

Same lightweight runner as the other suites (no pytest). No database, no network, no .env: SUPABASE_URL,
SUPABASE_KEY and DATABASE_URL are pinned to ci.invalid BEFORE any import and scripts.ops.db is imported
first. The movement audit (scripts.ops.moves, built beside this release) is a stand-in module put in
sys.modules: its audit() hands over an Audit in the contract's shape (d, source, result, costs, rows,
exceptions), so nothing here reads a ledger or reaches a database. Every folder is a temporary one; all
names, voucher numbers and amounts are invented and small.

Covered:
  * one recorder: record_moves is the only call of the register for the five movement rules, it records
    every rule on every run (an empty list clears), and no other module of the package records one;
  * the keys, levels and BHD of each kind of exception: ACT at the BHD threshold or past the working days,
    CHECK otherwise; titles with no count or age, stores in the detail only, who and due empty;
  * the damage store: one item per month of issue, its SIO numbers the evidence, the BHD summed;
  * the first screen shows the BASELINE and the THRESHOLDS once, and `replay` shows them again;
  * explain and done: the title said back, an explained item stays off unless it grows by more than a
    quarter, becomes more pressing or names a new voucher; a done item rests until its facts change;
  * the floor: folded per rule, counted under the floor, an item with no BHD and other rules never folded;
  * the replay: the smallest N with at most 13 receipts later, the 66th-largest BHD, the 90th percentile,
    the late transfers counted once, the same answer in any order, worked out once, config wins;
  * the load check's and the Command Centre's items are never recorded here and keep their ids and states;
  * the BHD of a changed voucher or invoice worked out at display time, 'gone' when it is gone, never stored;
  * the line on the two rules still to come;
  * ensure_recorded: once per ledger, the quick path that runs no audit, never raises, records nothing on
    a ledger that is not proven, nor when the audit is not built;
  * the entry point (text, --json, a word it does not know, an item it does not have) and the skill's form.
"""
from __future__ import annotations

import ast
import io
import json
import os
import random
import re
import sys
import tempfile
import traceback
import types
from contextlib import contextmanager, redirect_stdout
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"
os.environ.pop("YQ_MAIN_CHECKOUT", None)

from scripts.ops import db  # noqa: E402,F401  (first, as every command does)
from scripts.ops import __main__ as cli  # noqa: E402
from scripts.ops import alerts, checks, config, fmt, ledger, load_check, pairing, paths, status  # noqa: E402

TESTS: list[tuple[str, object]] = []
OPS = ROOT / "scripts" / "ops"
SKILL = ROOT / ".claude" / "skills" / "yq-watch" / "SKILL.md"

D = date(2026, 1, 20)                     # a Tuesday: the ledger's last day
MAIN, VAN_A, VAN_B, DAMAGE = "Main Store", "Van A - Acc WH", "Van B - Acc WH", "Damage Store"
ITEM_1, ITEM_2, ITEM_3 = "Cable One C1", "Charger Two C2", "Holder Three C3"      # the third has no cost
COSTS = {ITEM_1: SimpleNamespace(cost_bhd=Decimal("1.500")), ITEM_2: SimpleNamespace(cost_bhd=Decimal("4.000"))}
FIXTURE = {"warehouses.main": MAIN, "warehouses.aliases": {}, "warehouses.not_counted": [],
           "warehouses.damage": DAMAGE, "items.not_accessories": [], "items.bridge_exceptions": {}}
LIMITS = {"watch.floor_bhd": 0, "watch.act_bhd": 50, "watch.act_days": 2}
NOTE = "The stock ledger is read from the file Stock_ledger.xlsx: it agrees with what the portal holds."
_MISSING = object()


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


class data_root:
    """A temporary data root for one test, with the fixture's config (and `extra` on top)."""

    def __init__(self, extra: dict | None = None):
        self.extra = extra or {}

    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name).resolve()
        paths.set_data_root(self.root)
        config.use(dict(FIXTURE, **self.extra))
        return self.root

    def __exit__(self, *exc):
        paths.set_data_root(None)
        config.use(None)
        self._tmp.cleanup()
        return False


# ── the stand-in movement audit ───────────────────────────────────────────────

def sio(n) -> str:
    return f"SIO:X-26-01-{n}"


def strv(n) -> str:
    return f"STRV:X-26-01-{n}"


def exc(kind: str, *, issues=(), receipt=None, frm=MAIN, to=VAN_A, received_at=None, day=date(2026, 1, 12),
        deadline=date(2026, 1, 13), lag=None, lines=((ITEM_1, 2),), bhd="3", possible=(), open_=None) -> dict:
    out = {"kind": kind, "issues": list(issues), "receipt": receipt, "from": frm, "to": to,
           "received_at": received_at, "date": day, "deadline": deadline, "lag": lag, "lines": list(lines),
           "units": sum(Decimal(str(u)) for _i, u in lines), "bhd": Decimal(bhd) if bhd is not None else None,
           "basis": "landed cost", "possible": list(possible)}
    if open_ is not None:
        out["open"] = open_
    return out


def not_received(n, **kw) -> dict:
    return exc("not_received", issues=[sio(n)], **kw)


def make_audit(exceptions=(), *, d=D, rows=(), pairs=(), costs=None, ok=True, sha="sha256:" + "a" * 64,
               source="file") -> SimpleNamespace:
    src = {"source": source, "note": NOTE if ok else "The stock ledger is read from the portal's database.",
           "per_store_ok": ok, "why": None if ok else "the file differs from what the portal holds"}
    if sha:
        src["sha"] = sha
    return SimpleNamespace(d=d, source=src, result=SimpleNamespace(pairs=list(pairs)),
                           costs=COSTS if costs is None else costs, rows=list(rows), exceptions=list(exceptions))


class World:
    """What the stand-in scripts.ops.moves hands over, and how often it was asked."""

    def __init__(self, audit=None):
        self.audit = audit if audit is not None else make_audit()
        self.calls = 0
        self.error: Exception | None = None

    def module(self) -> types.ModuleType:
        mod = types.ModuleType("scripts.ops.moves")

        def audit(window=None, *, rep=None, prefer="auto"):
            self.calls += 1
            if self.error is not None:
                raise self.error
            return self.audit
        mod.audit = audit
        return mod


@contextmanager
def standing_in(world: World | None):
    """scripts.ops.moves is the world's stand-in (None: the module cannot be imported, as before it is built)."""
    old = sys.modules.get("scripts.ops.moves", _MISSING)
    sys.modules["scripts.ops.moves"] = world.module() if world is not None else None
    try:
        yield world
    finally:
        if old is _MISSING:
            sys.modules.pop("scripts.ops.moves", None)
        else:
            sys.modules["scripts.ops.moves"] = old


def watch(*words, json_out: bool = False) -> tuple[int, str]:
    argv = ["watch", *words, "--data-root", str(paths.data_root())] + (["--json"] if json_out else [])
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli.main(argv)
    return code, buf.getvalue()


def row(day, voucher, vtype, wh, item, *, to_wh=None, qty_in=0, qty_out=0) -> dict:
    """One ledger row in scripts.ops.ledger's shape."""
    return {"item": item, "date": day, "voucher": voucher, "vtype": vtype, "wh": wh, "to_wh": to_wh,
            "qty_in": Decimal(qty_in), "qty_out": Decimal(qty_out), "value_in": Decimal(0), "value_out": Decimal(0),
            "narr": None}


def keys_of(rule: str) -> dict[str, dict]:
    return {k: r for k, r in alerts.current().items() if r.get("rule") == rule}


def first_run() -> None:
    """Every movement rule has run once on nothing, so what the next run finds is listed, not a baseline."""
    checks.record_moves(make_audit(), D.isoformat())


# ── one recorder ──────────────────────────────────────────────────────────────

@test("one recorder: record_moves records every movement rule on every run; no other module records one")
def _():
    tree = ast.parse((OPS / "checks.py").read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and ast.unparse(n.func) == "alerts.record"]
    assert len(calls) == 1, "checks.py calls the register once, in record_moves"
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "record_moves")
    loops = [n for n in ast.walk(fn) if isinstance(n, ast.For) and ast.unparse(n.iter) == "MOVE_RULES"]
    assert loops and calls[0] in list(ast.walk(loops[0])), "the one call is in the loop over every rule"
    assert ast.unparse(calls[0].args[0]) == "rule" and ast.unparse(calls[0].args[1]) == "found[rule]"
    assert not calls[0].keywords, "the register's defaults: each rule's first run a baseline, the missing cleared"
    for path in sorted(OPS.rglob("*.py")):
        if path.name == "checks.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and ast.unparse(node.func).endswith("record") and node.args:
                first = node.args[0]
                assert not (isinstance(first, ast.Constant) and first.value in checks.MOVE_RULES), path.name
                assert "MOVE_RULES" not in ast.unparse(first), path.name
    assert checks.MOVE_RULES == ("xfer_open", "xfer_twins", "qty_diff", "xfer_elsewhere", "rcpt_orphan")
    assert set(checks.KIND_RULE.values()) == set(checks.MOVE_RULES)
    with data_root(LIMITS):
        out = checks.record_moves(make_audit([not_received(5)]), D.isoformat())
        assert set(out["counts"]) == set(checks.MOVE_RULES)
        assert all(alerts.has_run(rule) for rule in checks.MOVE_RULES), "an empty rule leaves its first-run marker"
        assert out["counts"]["xfer_open"]["baseline"] == 1 and keys_of("xfer_open")[f"xfer_open|{sio(5)}"]["baseline"]
        checks.record_moves(make_audit([not_received(5), exc("orphan", receipt=strv(8), received_at=VAN_B)]),
                            D.isoformat())
        assert [r["key"] for r in alerts.open_items()] == [f"rcpt_orphan|{strv(8)}"]
        c = checks.record_moves(make_audit([]), D.isoformat())          # nothing found: every item clears
        assert c["counts"]["xfer_open"]["cleared"] == 1 and c["counts"]["rcpt_orphan"]["cleared"] == 1
        assert not [r for r in alerts.current().values() if r.get("state") == "open"]


# ── keys, levels, BHD ─────────────────────────────────────────────────────────

@test("each kind: its key, its level (ACT at the BHD or past the working days), its BHD; words with no age")
def _():
    with data_root(LIMITS):
        first_run()
        found = [
            not_received(5, deadline=date(2026, 1, 14), bhd="3"),      # 5 working days past (Friday skipped): ACT
            not_received(6, deadline=date(2026, 1, 19), bhd="3"),      # 1 working day past: CHECK
            exc("qty_differs", issues=[sio(10)], receipt=strv(11), lag=2, lines=((ITEM_1, 2), (ITEM_2, -1)),
                bhd="50"),                                             # at the BHD threshold: ACT
            exc("elsewhere", issues=[sio(12)], receipt=strv(13), received_at=VAN_B, lag=1, bhd="12"),
            exc("orphan", receipt=strv(14), to=None, received_at=VAN_B, possible=[sio(15), sio(16)], bhd="60"),
            exc("twins", issues=[sio(9), sio(7), sio(8)], open_=1, deadline=date(2026, 1, 19), bhd="4.500"),
        ]
        checks.record_moves(make_audit(found), D.isoformat())
        cur = alerts.current()
        expect = {f"xfer_open|{sio(5)}": ("ACT", "3.000", [sio(5)]),
                  f"xfer_open|{sio(6)}": ("CHECK", "3.000", [sio(6)]),
                  f"qty_diff|{sio(10)}": ("ACT", "50.000", [sio(10), strv(11)]),
                  f"xfer_elsewhere|{sio(12)}": ("CHECK", "12.000", [sio(12), strv(13)]),
                  f"rcpt_orphan|{strv(14)}": ("ACT", "60.000", [strv(14)]),
                  f"xfer_twins|{sio(7)}": ("CHECK", "4.500", [sio(7), sio(8), sio(9)])}
        for key, (level, bhd, evidence) in expect.items():
            r = cur[key]
            assert (r["level"], r["bhd"], r["evidence"]) == (level, bhd, evidence), (key, r)
            assert r["who"] is None and r["due"] is None and r["state"] == "open" and not r["baseline"], r
            assert not re.search(r"\d", r["title"]), f"a title holds a count or an age: {r['title']}"
            assert fmt.day(D) not in r["detail"], "a detail holds no age (the ledger's day would make it one)"
            for store in (MAIN, VAN_A, VAN_B):
                assert store not in r["title"] and store not in key
            fmt.careful(r["title"])
            fmt.careful(r["detail"])
        assert "Van A - Acc WH" in cur[f"xfer_open|{sio(5)}"]["detail"], "the store is named in the detail"
        twins = cur[f"xfer_twins|{sio(7)}"]
        assert f"1 of 3 identical issues from {MAIN} to {VAN_A} have no receipt: {sio(7)}, {sio(8)}, {sio(9)}" in \
            twins["detail"] and twins["count"] == 1 and "cannot say which" in twins["detail"]
        orphan = cur[f"rcpt_orphan|{strv(14)}"]
        assert f"it may belong to {sio(15)}, {sio(16)}" in orphan["detail"] and sio(15) not in orphan["evidence"]
        diff = cur[f"qty_diff|{sio(10)}"]["detail"]
        assert f"received under {strv(11)} on Wed 14 Jan" in diff and f"{ITEM_2} (-1)" in diff, diff
        assert f"received at {VAN_B} under {strv(13)} on Tue 13 Jan" in cur[f"xfer_elsewhere|{sio(12)}"]["detail"]
        # the same audit again changes nothing; a twin without the count still says it, without naming one
        again = checks.record_moves(make_audit(found), D.isoformat())["counts"]
        assert sum(c["new"] for c in again.values()) == 0 and sum(c["same"] for c in again.values()) == 6
        items, _notes = checks.found_items([exc("twins", issues=[sio(9), sio(7)])], D, {}, None)
        assert items["xfer_twins"][0]["detail"].startswith("Not every one of 2 identical issues")
        # what the audit gives that names no voucher, or a kind not known, is counted, never recorded
        items, notes = checks.found_items([exc("not_received"), {"kind": "lost_in_space"}, not_received(5),
                                           not_received(5)], D, {}, None)
        assert len(items["xfer_open"]) == 1 and len(notes) == 3, notes
        # an issue still inside its deadline is not late: never listed, whatever the audit hands over
        waiting = [not_received(40, deadline=D), exc("twins", issues=[sio(41), sio(42)], deadline=D + timedelta(1)),
                   exc("damage_not_booked", issues=[sio(43)], to=DAMAGE, day=D, deadline=D + timedelta(7))]
        items, notes = checks.found_items(waiting + [not_received(44, deadline=D - timedelta(1))], D, {}, None)
        assert [it["key"] for rule in checks.MOVE_RULES for it in items[rule]] == [f"xfer_open|{sio(44)}"] and not notes
        # to the damage store the deadline is the first day out of grace (pairing.within_grace, /yq-stock damage)
        items, notes = checks.found_items([exc("damage_not_booked", issues=[sio(48)], to=DAMAGE, day=D - timedelta(7),
                                               deadline=D)], D, {}, None)
        assert [it["evidence"] for it in items["xfer_open"]] == [[sio(48)]] and not notes, (items["xfer_open"], notes)
        # ... and so is a set of identical issues the audit says went there (to_kind); with no to_kind, the old rule
        to_damage = exc("twins", issues=[sio(49), sio(50)], to=DAMAGE, day=D - timedelta(7), deadline=D, open_=1)
        for given, listed in ((dict(to_damage, to_kind="damage"), True), (to_damage, False),
                              (dict(to_damage, to=VAN_A, to_kind="van"), False),
                              (dict(not_received(51, deadline=D), to_kind="van"), False),
                              (dict(not_received(52, deadline=D), to_kind="damage"), True)):
            items, notes = checks.found_items([given], D, {}, None)
            assert bool(items["xfer_twins"] or items["xfer_open"]) is listed and not notes, given
        # the same exception as an object, one number alone where a list is expected, lines as mappings
        shaped = SimpleNamespace(**dict(not_received(45), issues=sio(45), lines=[{"item": ITEM_1, "units": 3}]))
        lone = dict(exc("orphan", receipt=strv(46), received_at=VAN_B), possible=sio(47))
        items, notes = checks.found_items([shaped, lone], D, {}, None)
        assert items["xfer_open"][0]["evidence"] == [sio(45)] and f"{ITEM_1} (3)" in items["xfer_open"][0]["detail"]
        assert f"it may belong to {sio(47)}" in items["rcpt_orphan"][0]["detail"] and not notes


@test("the damage store: one item per month of issue, its SIO numbers the evidence, the BHD summed")
def _():
    with data_root(LIMITS):
        first_run()
        dmg = [exc("damage_not_booked", issues=[sio(31)], to=DAMAGE, day=date(2026, 1, 8), deadline=date(2026, 1, 15),
                   bhd="2"),
               exc("damage_not_booked", issues=[sio(30)], to=DAMAGE, frm=VAN_A, day=date(2026, 1, 2),
                   deadline=date(2026, 1, 9), bhd="5.250"),
               exc("damage_not_booked", issues=[sio(29)], to=DAMAGE, day=date(2025, 12, 20),
                   deadline=date(2025, 12, 27), bhd=None)]
        checks.record_moves(make_audit(dmg), D.isoformat())
        cur = alerts.current()
        jan, dec = cur["xfer_open|damage|2026-01"], cur["xfer_open|damage|2025-12"]
        assert jan["evidence"] == [sio(30), sio(31)] and jan["bhd"] == "7.250", jan
        assert dec["evidence"] == [sio(29)] and dec["bhd"] is None
        assert jan["level"] == "ACT", "the older issue is more than the working days past its deadline"
        assert "Issued to Damage Store in January 2026" in jan["detail"] and f"from {VAN_A}" in jan["detail"]
        assert jan["title"] == checks.TITLES["damage"] and len(keys_of("xfer_open")) == 2


def damage(n: int, day: date, bhd: str = "2") -> dict:
    return exc("damage_not_booked", issues=[sio(n)], to=DAMAGE, day=day, deadline=day + timedelta(7), bhd=bhd)


@test("an older item that names a new voucher is listed like a new one: the damage store's month, a set of twins")
def _():
    month = "xfer_open|damage|2026-01"
    twins = "xfer_twins|" + sio(7)
    with data_root(LIMITS):
        found = [damage(30, date(2026, 1, 2)), damage(31, date(2026, 1, 5)),
                 exc("twins", issues=[sio(7), sio(8), sio(9)], open_=1, deadline=date(2026, 1, 13), bhd="4.500")]
        first = checks.record_moves(make_audit(found), D.isoformat())["counts"]      # the rules' first run
        assert (first["xfer_open"]["baseline"], first["xfer_twins"]["baseline"]) == (1, 1), first
        assert alerts.open_items() == [] and [b["count"] for b in alerts.baseline_summary()] == [1, 1]
        ids = {month: alerts.find(month)["id"], twins: alerts.find(twins)["id"]}
        # one of the month's issues is booked in and the set is valued anew: nothing named that was not, still older
        changed = [damage(31, date(2026, 1, 5)),
                   exc("twins", issues=[sio(7), sio(8), sio(9)], open_=1, deadline=date(2026, 1, 13), bhd="5")]
        c = checks.record_moves(make_audit(changed), D.isoformat())["counts"]
        assert (c["xfer_open"]["same"], c["xfer_open"]["new"], c["xfer_twins"]["same"], c["xfer_twins"]["new"]) == \
            (1, 0, 1, 0), c
        assert alerts.find(month)["baseline"] and alerts.find(month)["evidence"] == [sio(31)]
        assert alerts.open_items() == [], "still counted once, not listed"
        # a later issue of the same month is not booked in, and a fourth identical issue joins the set: listed now
        grown = [damage(31, date(2026, 1, 5)), damage(32, date(2026, 1, 8)),
                 exc("twins", issues=[sio(7), sio(8), sio(9), sio(10)], open_=2, deadline=date(2026, 1, 13), bhd="9")]
        c = checks.record_moves(make_audit(grown), D.isoformat())["counts"]
        assert (c["xfer_open"]["new"], c["xfer_twins"]["new"]) == (1, 1), c
        for key, evidence in ((month, [sio(31), sio(32)]), (twins, [sio(7), sio(8), sio(9), sio(10)])):
            r = alerts.find(key)
            assert (r["baseline"], r["state"], r["id"], r["evidence"]) == (False, "open", ids[key], evidence), r
        assert [r["key"] for r in alerts.open_items()] == [twins, month], "DO TODAY and the e-mail read this list"
        assert alerts.baseline_summary() == []
        assert "2 of 4 identical issues" in alerts.find(twins)["detail"]
        # and it stays listed: the next run that finds it as it is counts it as before
        c = checks.record_moves(make_audit(grown), D.isoformat())["counts"]
        assert (c["xfer_open"]["same"], c["xfer_twins"]["same"]) == (1, 1) and len(alerts.open_items()) == 2
    # the register's rule itself, for any rule: an older item that changes without a new number stays older
    with data_root():
        def item(evidence, bhd):
            return {"key": "r|1", "level": "CHECK", "title": "Something to check", "detail": "d", "bhd": bhd,
                    "evidence": evidence}
        assert alerts.record("r", [item(["V1", "V2"], "4")], D.isoformat())["baseline"] == 1
        assert alerts.record("r", [item(["V2"], "9")], D.isoformat())["same"] == 1 and alerts.open_items() == []
        assert alerts.record("r", [item(["V2", "V3"], "9")], D.isoformat())["new"] == 1
        assert [r["key"] for r in alerts.open_items()] == ["r|1"]
        alerts.record("r", [], D.isoformat())
        assert alerts.record("r", [item(["V2"], "9")], D.isoformat())["new"] == 1, "back after it cleared: news"
        assert alerts.find("r|1")["baseline"] is False


@test("the stores as the audit read them (store_rules), and unit costs held to six decimals as /yq-moves holds them")
def _():
    with data_root(LIMITS):
        own = pairing.StoreRules(main="Other Main", damage="Other Damage")
        audit = make_audit()
        audit.store_rules = own
        audit.stores = [SimpleNamespace(store=VAN_A, kind="van")]          # the audit's table of vans and outlets
        assert checks._stores(audit) is own
        assert checks._stores(make_audit()).kind(DAMAGE) == "damage", "no store_rules: config.json's stores"
        # 3 units at 0.333333 is 0.999999: one rounding, of the total, gives the fils /yq-moves shows
        costs = {ITEM_1: Decimal("0.333333"), ITEM_2: SimpleNamespace(cost=Decimal("1.2345678"))}
        assert checks._cost(costs, ITEM_1) == Decimal("0.333333") and checks._cost(costs, ITEM_2) == Decimal("1.234568")
        assert checks._value([(ITEM_1, 3)], costs) == Decimal("1.000")
        index = checks.LedgerIndex([row(D, sio(3), ledger.ISSUE, MAIN, ITEM_1, to_wh=VAN_A, qty_out=3),
                                    row(D, "SI:9", ledger.SALE, VAN_A, ITEM_1, qty_out=6)], costs)
        assert index.shown({"rule": "voucher_changed", "key": f"voucher_changed|{sio(3)}|{ITEM_1}"})[:2] == \
            ("1.000", Decimal("1.000"))
        assert index.shown({"rule": "inv_changed", "key": "inv_changed|SI : 9"})[:2] == ("2.000", Decimal("2.000"))


# ── the first screen ──────────────────────────────────────────────────────────

@test("the first screen shows the BASELINE and the THRESHOLDS once; replay shows the thresholds again")
def _():
    world = World(make_audit([not_received(5, bhd="3"), not_received(6, bhd="2"),
                              exc("orphan", receipt=strv(14), received_at=VAN_B, bhd="7")]))
    with data_root(LIMITS), standing_in(world):
        code, text = watch()
        assert code == 0, text
        assert "MOVEMENTS  recorded for the stock ledger to Tue 20 Jan: 0 new" in text and "3 counted once" in text
        assert "MOVEMENTS TO CHECK" in text and "Nothing open." in text, "the baseline is counted, not listed"
        assert "BASELINE (shown once" in text and "THRESHOLDS (shown once" in text, text
        base = text.split("BASELINE (shown once")[1]
        assert re.search(r"not received after the deadline\s+2\s+5\.000", base), base
        assert re.search(r"receipts with no issue found\s+1\s+7\.000", base), base
        assert "set in config.json" in text and "BHD 50.000" in text
        assert status.read_state().get(checks.STATE_SHOWN), "the screen that showed them says so"
        code, text = watch()
        assert code == 0 and "BASELINE" not in text and "THRESHOLDS" not in text, text
        assert "MOVEMENTS  already recorded for the stock ledger to Tue 20 Jan." in text
        code, text = watch("replay")
        assert code == 0 and "THRESHOLDS (in force" in text and "BASELINE" not in text, text
        code, text = watch("all")
        assert "(older) Transfer not received after its deadline" in text and sio(5) in text, text


# ── explain and done ──────────────────────────────────────────────────────────

@test("explain and done: the title said back; explained stays off unless larger, more pressing or a new voucher")
def _():
    with data_root(LIMITS):
        first_run()
        nr = not_received(5, bhd="10", deadline=date(2026, 1, 19))           # CHECK: one working day past
        dmg = exc("damage_not_booked", issues=[sio(30)], to=DAMAGE, day=date(2026, 1, 5), deadline=date(2026, 1, 12),
                  bhd="2")
        world = World(make_audit([nr, dmg]))
        with standing_in(world):
            checks.record_moves(world.audit, D.isoformat())
            ids = {r["key"]: r["id"] for r in alerts.open_items()}
            a, b = ids[f"xfer_open|{sio(5)}"], ids["xfer_open|damage|2026-01"]
            code, text = watch("explain", a, "booked", "under", "another", "number")
            assert code == 0 and text.splitlines()[0] == f"{a} explained: {checks.TITLES['not_received']}", text
            assert "His words: booked under another number" in text and "more than a quarter" in text
            assert f"xfer_open|{sio(5)}" in alerts.explained()
            assert [r["key"] for r in alerts.open_items()] == ["xfer_open|damage|2026-01"]

            def again(*found, d=D) -> dict:
                return checks.record_moves(make_audit(list(found), d=d), d.isoformat())["counts"]["xfer_open"]

            assert again(not_received(5, bhd="12.500", deadline=date(2026, 1, 19)), dmg)["explained"] == 1, \
                "a quarter larger exactly: still what he explained"
            assert again(not_received(5, bhd="12.510", deadline=date(2026, 1, 19)), dmg)["new"] == 1
            assert alerts.find(a)["state"] == "open" and alerts.find(a).get("reopened") and alerts.find(a)["id"] == a
            watch("explain", a, "known")
            assert again(nr, dmg)["explained"] == 1
            assert again(nr, dmg, d=date(2026, 1, 25))["new"] == 1, "more than the working days past: more pressing"
            assert alerts.find(a)["level"] == "ACT"
            # done: rests while its facts stay; a new voucher in the month brings it back
            code, text = watch("done", b)
            assert code == 0 and text.splitlines()[0] == f"{b} marked done: {checks.TITLES['damage']}", text
            assert alerts.find(b)["state"] == "done" and "rests until its facts change" in text
            assert again(nr, dmg)["same"] >= 1 and alerts.find(b)["state"] == "done"
            more = exc("damage_not_booked", issues=[sio(33)], to=DAMAGE, day=date(2026, 1, 6),
                       deadline=date(2026, 1, 13), bhd="1")
            again(nr, dmg, more)
            assert alerts.find(b)["state"] == "open" and alerts.find(b)["evidence"] == [sio(30), sio(33)]
            watch("explain", b, "known damage")
            again(nr, dmg, more)
            assert alerts.find(b)["state"] == "explained"
            third = exc("damage_not_booked", issues=[sio(34)], to=DAMAGE, day=date(2026, 1, 7),
                        deadline=date(2026, 1, 14), bhd="0.100")
            assert again(nr, dmg, more, third)["new"] == 1, "a voucher that was not there brings it back"
            # the words a command needs
            code, text = watch("explain", a)
            assert code == 1 and "Say why in your own words" in text
            code, text = watch("done", "A999")
            assert code == 1 and "No item A999 on the action list" in text
            code, text = watch("A999")
            assert code == 1 and "No item A999" in text


# ── the floor ─────────────────────────────────────────────────────────────────

@test("the floor: movement items below it are folded per rule; no BHD and other rules are never folded")
def _():
    with data_root(dict(LIMITS, **{"watch.floor_bhd": 5})):
        first_run()
        alerts.record("attn", [{"key": "attn|sold_out", "level": "CHECK", "title": "Sold out with demand",
                                "detail": "basis", "bhd": "1", "evidence": []}], D.isoformat(), baseline=False)
        found = [not_received(5, bhd="3"), not_received(6, bhd="4.999"), not_received(7, bhd=None),
                 not_received(8, bhd="5"), exc("orphan", receipt=strv(14), received_at=VAN_B, bhd="1")]
        world = World(make_audit(found))
        with standing_in(world):
            checks.record_moves(world.audit, D.isoformat())
            floor = Decimal("5")
            listed = [r["key"] for r in alerts.open_items(floor=floor, floor_rules=checks.MOVE_RULES)]
            # ACT (past the working days) first, the larger BHD first; the Command Centre's small item is no movement
            assert listed == [f"xfer_open|{sio(8)}", f"xfer_open|{sio(7)}", "attn|sold_out"], listed
            assert alerts.under_floor(floor, checks.MOVE_RULES) == {"rcpt_orphan": 1, "xfer_open": 2}
            assert len(alerts.open_items()) == 6, "without a floor nothing is folded"
            assert alerts.under_floor(None, checks.MOVE_RULES) == {}
            assert [r["key"] for r in checks.movement_items()] == [f"xfer_open|{sio(8)}", f"xfer_open|{sio(7)}"]
            s = checks.summary()
            assert (s["open"], s["floor_bhd"], s["under_floor"]) == (2, "5.000", {"rcpt_orphan": 1, "xfer_open": 2})
            code, text = watch()
            assert "UNDER THE FLOOR (below BHD 5.000 each" in text, text
            assert "not received after the deadline: 2 (BHD 7.999)" in text
            assert "receipts with no issue found: 1 (BHD 1.000)" in text
            assert sio(5) not in text.split("UNDER THE FLOOR")[0]
            code, text = watch("all")
            assert sio(5) in text and sio(6) in text and "UNDER THE FLOOR" not in text
    # a baseline item is counted as the baseline, never under the floor
    with data_root(dict(LIMITS, **{"watch.floor_bhd": 5})):
        checks.record_moves(make_audit([not_received(5, bhd="1")]), D.isoformat())       # the first run: baseline
        assert alerts.under_floor(Decimal(5), checks.MOVE_RULES) == {}


# ── the replay ────────────────────────────────────────────────────────────────

ISSUE_DAY = date(2026, 1, 4)              # a Sunday: the receipt is due on Monday 5 January
LATENESS = [0] * 6 + [1] * 4 + [2] * 3 + [3] * 3 + [4] * 2 + [5] * 2     # working days late, 20 receipts
RECEIVED = {0: date(2026, 1, 5), 1: date(2026, 1, 6), 2: date(2026, 1, 7), 3: date(2026, 1, 8),
            4: date(2026, 1, 10), 5: date(2026, 1, 11)}                  # the Friday is no working day


def replay_world(item: str = ITEM_3) -> tuple[list[dict], list]:
    """Twenty transfers received with LATENESS, an old one far outside the 90 days, all of `item`."""
    rows, pairs = [], []
    for n, late in enumerate(LATENESS, start=100):
        rows.append(row(ISSUE_DAY, sio(n), ledger.ISSUE, MAIN, item, to_wh=VAN_A, qty_out=1))
        rows.append(row(RECEIVED[late], strv(n), ledger.RECEIVE, VAN_A, item, qty_in=1))
        pairs.append(SimpleNamespace(issue=SimpleNamespace(number=sio(n)), receipt=SimpleNamespace(number=strv(n))))
    old = date(2025, 9, 1)
    rows.append(row(old, sio(99), ledger.ISSUE, MAIN, item, to_wh=VAN_A, qty_out=1))
    rows.append(row(old + timedelta(days=60), strv(99), ledger.RECEIVE, VAN_A, item, qty_in=1))
    pairs.append((sio(99), strv(99)))
    return rows, pairs


@test("the replay: the smallest N with at most 13 receipts later, the 66th-largest, the 90th percentile")
def _():
    with data_root():
        rows, pairs = replay_world()
        found = [not_received(200 + i, day=date(2026, 1, 10), deadline=date(2026, 1, 11), bhd=str(i))
                 for i in range(1, 71)]
        found.append(not_received(300, day=date(2025, 10, 1), bhd="900"))     # an issue older than the 90 days
        stores = checks._stores(None)
        res = checks.replay_defaults(SimpleNamespace(pairs=pairs), COSTS, D, rows=rows, exceptions=found,
                                     stores=stores)
        assert res["act_days"] == 1, res        # 10 receipts came more than 1 working day late, 14 more than 0
        assert res["floor_bhd"] == Decimal("5.000"), res  # 70 items valued 1..70: the 66th-largest
        assert res["act_bhd"] == Decimal("63.000"), res   # nearest rank: ceil(0.9 x 70) = 63
        basis = res["basis"]
        assert (basis["receipts"], basis["later"], basis["items"], basis["unvalued"]) == (20, 10, 70, 14), basis
        assert (basis["first"], basis["last"]) == ("2025-10-23", "2026-01-20")
        # the same answer from the same facts in another order, and from pairs given as tuples or mappings
        rng = random.Random(7)
        for _ in range(3):
            shuffled_rows, shuffled_found = rows[:], found[:]
            rng.shuffle(shuffled_rows)
            rng.shuffle(shuffled_found)
            other = [p if isinstance(p, tuple) else {"issue": p.issue.number, "receipt": p.receipt.number}
                     for p in reversed(pairs)]
            assert checks.replay_defaults(SimpleNamespace(pairs=other), COSTS, D, rows=shuffled_rows,
                                          exceptions=shuffled_found, stores=stores) == res
        # fewer than 66 items: no floor; nothing valued and no receipt: nothing to rest on
        few = checks.replay_defaults(SimpleNamespace(pairs=[]), COSTS, D, rows=[], exceptions=found[:3], stores=stores)
        assert few["floor_bhd"] == Decimal(0) and few["act_bhd"] == Decimal("3.000") and few["act_days"] is None
        none = checks.replay_defaults(None, {}, D)
        assert none["act_bhd"] is None and none["act_days"] is None and none["floor_bhd"] == Decimal(0)
        assert checks._act_days([9] * 13) == 0 and checks._act_days([9] * 14) == 9


@test("the replay reads the pairing's own legs (`no`), and an issue booked in under two receipts by the later one")
def _():
    with data_root():
        rows, pairs = replay_world()
        stores = checks._stores(None)
        res = checks.replay_defaults(SimpleNamespace(pairs=pairs), COSTS, D, rows=rows, exceptions=[], stores=stores)
        legs = [SimpleNamespace(issue=SimpleNamespace(no=sio(n)), receipt=SimpleNamespace(no=strv(n)), also=())
                for n in range(100, 100 + len(LATENESS))]
        got = checks.replay_defaults(SimpleNamespace(pairs=legs), COSTS, D, rows=rows, exceptions=[], stores=stores)
        assert got["basis"]["pairs_unread"] == 0 and got["basis"]["receipts"] == res["basis"]["receipts"] == 20, got
        assert got["act_days"] == res["act_days"] == 1
        # SIO 100 was booked in on time and again five working days late (step S): it counts as five days late
        late_rows = rows + [row(RECEIVED[5], strv(150), ledger.RECEIVE, VAN_A, ITEM_3, qty_in=1)]
        legs[0] = SimpleNamespace(issue=SimpleNamespace(no=sio(100)), receipt=SimpleNamespace(no=strv(100)),
                                  also=(SimpleNamespace(no=strv(150)),))
        split = checks.replay_defaults(SimpleNamespace(pairs=legs), COSTS, D, rows=late_rows, exceptions=[],
                                       stores=stores)
        assert split["basis"]["receipts"] == 20 and split["basis"]["later"] == res["basis"]["later"] + 1, split


@test("the replay counts a late transfer once: valued at cost, unless an exception already stands for it")
def _():
    with data_root():
        rows, pairs = replay_world(ITEM_1)                    # 14 late of 20, each 1 unit at 1.500
        diff = exc("qty_differs", issues=[sio(118)], receipt=strv(118), day=ISSUE_DAY, bhd="7")
        res = checks.replay_defaults(SimpleNamespace(pairs=pairs), COSTS, D, rows=rows, exceptions=[diff],
                                     stores=checks._stores(None))
        assert res["basis"]["items"] == 14 and res["basis"]["unvalued"] == 0, res      # 13 late + the exception
        assert res["floor_bhd"] == Decimal(0)
        assert res["act_bhd"] == Decimal("1.500"), res      # nearest rank 13 of 14: the 13th smallest is a late one
        alone = checks.replay_defaults(SimpleNamespace(pairs=pairs), COSTS, D, rows=rows, exceptions=[diff],
                                       stores=checks._stores(None))
        assert alone == res, "the same arguments, the same answer"
        without = checks.replay_defaults(SimpleNamespace(pairs=pairs), COSTS, D, rows=rows, exceptions=[],
                                         stores=checks._stores(None))
        assert without["basis"]["items"] == 14, "the late transfer stands for itself when no exception does"


@test("the replay is worked out once, kept with its basis, printed once; a value in config.json wins")
def _():
    rows, pairs = replay_world()
    # due on the day before the ledger's last day: one working day past it, so only the BHD can make one ACT
    found = [not_received(200 + i, day=date(2026, 1, 15), deadline=date(2026, 1, 19), bhd=str(i)) for i in range(1, 71)]
    world = World(make_audit(found, rows=rows, pairs=pairs))
    with data_root(), standing_in(world):
        code, text = watch()
        assert code == 0, text
        kept = status.read_state()[checks.STATE_DEFAULTS]
        assert (kept["act_days"], kept["floor_bhd"], kept["act_bhd"]) == (1, "5.000", "63.000"), kept
        assert kept["basis"]["receipts"] == 20 and kept["at"]
        assert "replay: the 66th-largest of 70 items" in text and "replay: the 90th percentile" in text
        assert "1 working day past the deadline (replay: 10 of 20 receipts came later than that)" in text, text
        lim = checks.thresholds()
        assert lim["from"] == {"floor_bhd": "replay", "act_bhd": "replay", "act_days": "replay"}
        assert (lim["floor_bhd"], lim["act_bhd"], lim["act_days"]) == (Decimal("5.000"), Decimal("63.000"), 1)
        # a new ledger with other figures: the replay is not worked out again
        world.audit = make_audit(found[:2], rows=rows, pairs=pairs, sha="sha256:" + "b" * 64)
        code, text = watch()
        assert "THRESHOLDS" not in text and status.read_state()[checks.STATE_DEFAULTS] == kept
        code, text = watch("replay")
        assert "BHD 5.000" in text and "BHD 63.000" in text
        assert "The replay: the issues of Thu 23 Oct to Tue 20 Jan" in text
        # the items were levelled with it: ACT at 63 and above; one working day past the deadline is not more than one
        cur = alerts.current()
        assert cur[f"xfer_open|{sio(270)}"]["level"] == "ACT" and cur[f"xfer_open|{sio(263)}"]["level"] == "ACT"
        assert cur[f"xfer_open|{sio(262)}"]["level"] == "CHECK"
        config.use(dict(FIXTURE, **{"watch.floor_bhd": 9}))
        lim = checks.thresholds()
        assert lim["floor_bhd"] == Decimal("9") and lim["from"]["floor_bhd"] == "config"
        assert lim["act_bhd"] == Decimal("63.000") and lim["from"]["act_bhd"] == "replay"
        config.use(dict(FIXTURE, **{"watch.act_days": "two"}))
        raises(config.ConfigMissing, checks.thresholds)          # a value of the wrong kind stops in one sentence
        code, text = watch()
        assert code == 1 and "watch.act_days" in text
    with data_root(LIMITS), standing_in(World(make_audit(found, rows=rows, pairs=pairs))):
        watch()
        assert checks.STATE_DEFAULTS not in status.read_state(), "all three set in config.json: no replay is kept"


# ── what this command never records ───────────────────────────────────────────

def seed_others() -> dict[str, str]:
    """A load check's and a Command Centre's items on the register, as their own recorders put them."""
    alerts.record("inv_changed", [{"key": "inv_changed|SI : 77", "level": "CHECK", "title": "Invoice changed in "
                                   "Focus after it was loaded: to check", "detail": "SI : 77: changed",
                                   "bhd": None, "evidence": ["SI : 77"]},
                                  {"key": "inv_changed|SI : 78", "level": "CHECK", "title": "Invoice changed in "
                                   "Focus after it was loaded: to check", "detail": "SI : 78: changed",
                                   "bhd": None, "evidence": ["SI : 78"]},
                                  {"key": "inv_changed|SI : 79", "level": "CHECK", "title": "Invoice no longer in "
                                   "Focus's export: to check", "detail": "SI : 79: removed",
                                   "bhd": None, "evidence": ["SI : 79"]}],
                  D.isoformat(), baseline=False, clear_missing=False)
    alerts.record("voucher_changed", [
        {"key": f"voucher_changed|{sio(3)}|{ITEM_1}", "level": "CHECK", "title": "Stock voucher changed in Focus "
         "after it was loaded: to check", "detail": "differs", "bhd": None, "evidence": [sio(3)]},
        {"key": f"voucher_changed|{sio(4)}|{ITEM_2}", "level": "CHECK", "title": "Stock voucher no longer in "
         "Focus's ledger: to check", "detail": "differs", "bhd": None, "evidence": [sio(4)]}],
        D.isoformat(), baseline=False, clear_missing=False)
    alerts.record("attn", [{"key": "attn|sold_out", "level": "ACT", "title": "2 items sold out with demand",
                            "detail": "basis", "bhd": "20", "count": 2, "evidence": []}],
                  D.isoformat(), baseline=False)
    return {k: r["id"] for k, r in alerts.current().items()}


def ledger_rows() -> list[dict]:
    return [row(date(2026, 1, 9), sio(3), ledger.ISSUE, MAIN, ITEM_1, to_wh=VAN_A, qty_out=4),
            row(date(2026, 1, 9), sio(3), ledger.ISSUE, MAIN, ITEM_2, to_wh=VAN_A, qty_out=1),
            row(date(2026, 1, 11), "SI:77", ledger.SALE, VAN_A, ITEM_1, qty_out=2),
            row(date(2026, 1, 11), "SI:77", ledger.SALE, VAN_A, ITEM_2, qty_out=1),
            row(date(2026, 1, 11), "SI:78", ledger.SALE, VAN_A, ITEM_1, qty_out=2),
            row(date(2026, 1, 11), "SI:78", ledger.SALE, VAN_A, ITEM_3, qty_out=5)]


@test("the load check's and the Command Centre's items are never recorded here: same ids, states and lines")
def _():
    assert checks.LOAD_CHECK_RULES == load_check.EVENT_RULES + load_check.STATE_RULES
    from scripts.ops import sales
    assert checks.COMMAND_CENTRE_RULES == (sales.ATTN_RULE,)
    src = (OPS / "checks.py").read_text(encoding="utf-8")
    for rule in checks.LOAD_CHECK_RULES + checks.COMMAND_CENTRE_RULES + ("ATTN_RULE",):
        assert not re.search(r"record\(\s*[\"']?" + re.escape(rule), src), rule
    with data_root(LIMITS):
        ids = seed_others()
        lines_before = alerts.alerts_file().read_text(encoding="utf-8").splitlines()
        world = World(make_audit([not_received(5)], rows=ledger_rows()))
        with standing_in(world):
            checks.ensure_recorded()
            watch()
            watch("all")
            checks.record_moves(make_audit([]), D.isoformat())
        after = alerts.current()
        for key, ident in ids.items():
            assert after[key]["id"] == ident and after[key]["state"] == "open", key
            assert after[key]["bhd"] == ("20.000" if key == "attn|sold_out" else None), "a BHD shown is never stored"
        lines_after = alerts.alerts_file().read_text(encoding="utf-8").splitlines()
        assert lines_after[:len(lines_before)] == lines_before, "the register is only ever appended to"
        added = [json.loads(ln) for ln in lines_after[len(lines_before):]]
        assert added and all(r.get("rule") in checks.MOVE_RULES for r in added), added


@test("BHD at display time: a changed voucher's units and an invoice's lines at cost, 'gone' when gone")
def _():
    with data_root(LIMITS):
        ids = seed_others()
        world = World(make_audit([], rows=ledger_rows()))
        with standing_in(world):
            code, text = watch()
            assert code == 0, text
            block = text.split("FROM THE LOAD CHECK")[1].split("FROM THE COMMAND CENTRE")[0]
            shown = {ln.split()[0]: ln.split()[-1] for ln in block.splitlines() if ln.startswith("A")}
            assert shown[ids[f"voucher_changed|{sio(3)}|{ITEM_1}"]] == "6.000", block      # 4 units at 1.500
            assert shown[ids[f"voucher_changed|{sio(4)}|{ITEM_2}"]] == "gone", block
            assert shown[ids["inv_changed|SI : 77"]] == "7.000", block                   # 2 x 1.500 + 1 x 4.000
            assert shown[ids["inv_changed|SI : 78"]] == "3.000*", block                  # one line has no cost
            assert shown[ids["inv_changed|SI : 79"]] == "gone", block
            assert "* some lines have no cost on file" in block
            assert "20.000" in text.split("FROM THE COMMAND CENTRE")[1], "the Command Centre's own figure, as kept"
            code, one = watch(ids["inv_changed|SI : 77"])
            assert code == 0 and "BHD:       7.000 (worked out now, at cost)" in one, one
            saved = json.loads((paths.exports_dir(D) / "watch.json").read_text(encoding="utf-8"))
            assert {f["bhd_text"] for f in saved["load_check"]} == {"6.000", "gone", "7.000", "3.000*"}
            for key in ids:
                assert alerts.current()[key]["bhd"] in (None, "20.000"), "never stored"
            world.audit = make_audit([], rows=ledger_rows(), ok=False)        # an unproven ledger: not worked out
            code, text = watch()
            assert "the BHD of a changed invoice or voucher is not worked out this time" in text, text
            assert "the file differs from what the portal holds" in text


@test("the line on the two rules still to come names the profitability report's state")
def _():
    with data_root(LIMITS), standing_in(World()):
        code, text = watch()
        assert checks.hooks_line() in text.splitlines()
        assert ("Not checked yet: sold below cost (it needs the profitability report) and sales returns against "
                "last month's kickback tier (with the month-end Ledger export); both come with the month-end "
                "release.") in text
        status.write_state({"last_load": {"run": "190126_0800", "profit_held_since": "2026-01-13"}})
        assert checks.hooks_line().startswith("Not checked yet: sold below cost (the profitability report is held "
                                              "back since Tue 13 Jan) and sales returns")


# ── once per ledger ───────────────────────────────────────────────────────────

@test("ensure_recorded: once per ledger, the quick path runs no audit, never raises, nothing on an unproven ledger")
def _():
    with data_root(LIMITS) as root:
        world = World(make_audit([not_received(5)]))
        with standing_in(world):
            first = checks.ensure_recorded()
            assert first["ok"] and first["recorded"] and first["key"] == "sha256:" + "a" * 64, first
            assert status.read_state()[checks.STATE_RECORDED]["data_date"] == D.isoformat()
            lines = alerts.alerts_file().read_text(encoding="utf-8")
            second = checks.ensure_recorded()
            assert second["ok"] and second["already"] and not second["recorded"], second
            assert alerts.alerts_file().read_text(encoding="utf-8") == lines, "the second call writes nothing"
            assert checks.ensure_recorded(record=False) == {"ok": True, "recorded": False,
                                                            "why": "a preview records nothing"}
            # a new ledger is recorded again
            world.audit = make_audit([not_received(6)], sha="sha256:" + "c" * 64)
            assert checks.ensure_recorded()["recorded"]
            assert alerts.current()[f"xfer_open|{sio(5)}"]["state"] == "cleared"
        # the loaded folder's file is the key: once recorded, the next call does not even run the audit
        folder = root / "business_data" / "Focus ERP 2026-01-20"
        folder.mkdir(parents=True)
        (folder / "Stock_ledger_200126.xlsx").write_bytes(b"an invented workbook")
        world = World(make_audit([not_received(7)], sha=None))
        with standing_in(world):
            done = checks.ensure_recorded()
            assert done["recorded"] and done["key"] == checks._file_key() and done["key"].startswith("sha256:")
            calls = world.calls
            assert checks.ensure_recorded()["already"] and world.calls == calls, "no audit on the quick path"
        # the database: keyed on its last day
        world = World(make_audit([], sha=None, source="database"))
        with standing_in(world):
            assert checks.ledger_key(world.audit) == "db:2026-01-20"
    with data_root(LIMITS):
        world = World(make_audit([not_received(5)], ok=False))
        with standing_in(world):
            said = checks.ensure_recorded()
            assert said["ok"] is False and said["why"] == "the file differs from what the portal holds", said
            assert not alerts.alerts_file().exists() and checks.STATE_RECORDED not in status.read_state()
            raises(checks.NotProven, lambda: checks.record_moves(world.audit, D.isoformat()))
            assert not alerts.alerts_file().exists()
            code, text = watch()
            assert code == 0
            assert "MOVEMENTS  not recorded this time: the file differs from what the portal holds" in text
            world.error = RuntimeError("boom")
            said = checks.ensure_recorded()
            assert said == {"ok": False, "recorded": False, "why": "the movement audit could not run (RuntimeError)"}
            world.error = ledger.NoLedgerFile("no Stock_ledger file in Focus ERP 2026-01-20")
            assert checks.ensure_recorded()["why"] == "no Stock_ledger file in Focus ERP 2026-01-20"
        with standing_in(None):                          # the movement audit is not there at all
            said = checks.ensure_recorded()
            assert said == {"ok": False, "recorded": False, "why": "the movement audit is not built yet"}, said
            code, text = watch()
            assert code == 0 and "the movement audit is not built yet" in text and "FROM THE LOAD CHECK" in text


# ── the entry point and the skill ─────────────────────────────────────────────

@test("the entry point: the screen, --json, a word it does not know; the screen is saved; the words are careful")
def _():
    odd = "Leak Corner - Acc WH"                         # a store whose name holds a word the guard refuses
    world = World(make_audit([not_received(5, to=odd, bhd="60")]))
    with data_root(dict(LIMITS, **{"watch.floor_bhd": 0})), standing_in(world):
        first_run()
        code, out = watch(json_out=True)
        payload = json.loads(out)
        assert code == 0 and payload["command"] == "watch" and payload["data_date"] == D.isoformat(), payload
        assert payload["flags"]["recorded"] is True and payload["flags"]["open"] == {"ACT": 1}
        assert any(f.endswith("watch.txt") for f in payload["files"]) and out.isascii()
        detail = alerts.current()[f"xfer_open|{sio(5)}"]["detail"]
        assert "Corner" not in detail and detail.startswith(f"{sio(5)}: issued"), "the plain fallback stands in"
        code, text = watch("frobnicate")
        assert code == 1 and "is not a word of this command" in text
        code, text = watch("explain")
        assert code == 1 and "Name the item" in text
        code, text = watch()
        for ln in text.splitlines():
            fmt.careful(ln)
        assert "Not checked yet" in text and "To explain an item: /yq-watch explain" in text
    assert checks.parse_spec(["explain", "a7", "his", "words"]) == ("explain", "a7", "his words")
    assert checks.parse_spec(["done a7"]) == ("done", "a7", None)
    assert checks.parse_spec([]) == ("open", None, None) and checks.parse_spec(["ALL"]) == ("all", None, None)
    assert checks.working_days_after(date(2026, 1, 14), D) == 5 and checks.working_days_after(D, D) == 0
    assert checks.working_days_after(date(2026, 1, 15), date(2026, 1, 16)) == 0, "a Friday is no working day"
    holiday = pairing.Rules(holidays=frozenset({date(2026, 1, 18)}))
    assert checks.working_days_after(date(2026, 1, 14), D, holiday) == 4


@test("the skill: the form of the other yq skills (frontmatter, Bash and PowerShell twins, under 70 lines)")
def _():
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\nname: \"yq-watch\"\n"), text[:40]
    assert len(text.splitlines()) < 70 and text.count("$ARGUMENTS") == 1
    assert '  - "Bash(python -m scripts.ops watch *)"\n  - "PowerShell(python -m scripts.ops watch *)"\n' in text
    assert "Run exactly `python -m scripts.ops watch $ARGUMENTS`" in text and "600000" in text
    for said in ("Show the screen unchanged, in a code block", "type no number yourself",
                 "run them only on his own words", "Never explain or close an item he did not name",
                 "grows by more than a quarter, becomes more pressing, or names a voucher that was not there",
                 "Never accuse anyone", "to check", "Run nothing else that loads, writes or deploys"):
        assert said in text, said
    assert "watch" in cli.COMMANDS and cli.COMMANDS["watch"][0] == "scripts.ops.checks"


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
