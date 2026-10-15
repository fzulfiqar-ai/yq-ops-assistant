"""Ops engine foundation: paths, config, text rendering, git questions, status and the entry point.

    python -m tests.test_ops_engine

Same lightweight runner as the other suites (no pytest). No database, no network, no .env
(SUPABASE_URL / SUPABASE_KEY / DATABASE_URL are pinned to ci.invalid before any import). Every
folder is a temporary one; all names and numbers are invented.

Covered:
  * paths: the data root (flag, YQ_MAIN_CHECKOUT, a worktree's main checkout), drop folders ordered
    by the date their name spells, outputs refused outside business_data/ and exports/;
  * config: a missing file or key stops with the key's name, types are checked, the monthly step in
    force, no number in the committed key list;
  * fmt: BHD to the fils half-up, shares and changes, the fixed-width table, the wording guard;
  * gitinfo: verbs outside the list are refused; the questions answer on this repository;
  * status: the lock (none / running / interrupted), drop folders waiting, the five lines offline;
  * the entry point: unknown and not-built commands, the interrupted banner, --json;
  * selftest: the name and amount scan behind --repo-clean.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import traceback
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

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
from scripts.ops import config, fmt, gitinfo, paths, selftest, status  # noqa: E402

TESTS: list[tuple[str, object]] = []

FIXTURE = {
    "api.health_url": "https://ci.invalid/health",
    "sales.kpi_steps": [{"from": "2026-01", "bhd": 100}, {"from": "2026-03", "bhd": 200}],
    "load.profit_gap_max": 0.5, "load.blank_cost_share_max": 0.5, "load.backup_keep": 3,
    "brief.rep_share_min": 0.5, "brief.silent_business_days": 5, "brief.bulk_invoice_bhd": 100,
    "stock.lead_time_days": 10, "stock.display_items": [], "items.bridge_exceptions": {},
    "items.not_accessories": [], "warehouses.main": "Main Store", "warehouses.aliases": {},
    "warehouses.not_counted": [], "report.category_names": {}, "report.prepared_by": "",
    "stock.fast_movers": 3, "stock.cover_kpi_days": [10, 20],
}


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


class data_root:
    """A temporary data root for one test (business_data/, exports/ and drops are made on demand)."""

    def __enter__(self) -> Path:
        # Windows: a scanner can hold a just-written file for a moment (WinError 145 on cleanup)
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self._tmp.name)
        paths.set_data_root(self.root)
        config.use(None)
        return self.root

    def __exit__(self, *exc):
        paths.set_data_root(None)
        config.use(None)
        self._tmp.cleanup()
        return False


def raises(kind, fn) -> Exception:
    try:
        fn()
    except kind as exc:
        return exc
    raise AssertionError(f"expected {kind.__name__}")


# ── paths ─────────────────────────────────────────────────────────────────────

@test("the data root: flag, then YQ_MAIN_CHECKOUT, then the main checkout of a worktree")
def _():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["YQ_MAIN_CHECKOUT"] = tmp
        try:
            assert paths.data_root() == Path(tmp)
            paths.set_data_root(ROOT)
            assert paths.data_root() == ROOT.resolve()
        finally:
            paths.set_data_root(None)
            os.environ.pop("YQ_MAIN_CHECKOUT", None)
    expected = ROOT.parents[2] if paths.is_worktree() else ROOT
    assert paths.data_root() == expected


@test("drop folders are ordered by the date their name spells, not by name")
def _():
    with data_root() as root:
        for name in ("280926", "071026", "210926", "notes", "9999999"):
            (root / "Uplaods" / name).mkdir(parents=True)
        (root / "Uploads" / "081026").mkdir(parents=True)
        assert paths.drop_date("071026") == date(2026, 10, 7)
        assert paths.drop_date("311326") is None and paths.drop_date("abc") is None
        names = [p.name for _d, p in paths.drop_folders()]
        assert names == ["210926", "280926", "071026", "081026"], names
        assert paths.find_drop(None).name == "081026"
        assert paths.find_drop("280926").name == "280926"
        assert paths.find_drop("010101") is None


@test("outputs are refused outside business_data/ and exports/")
def _():
    with data_root() as root:
        ok = paths.write_text(paths.exports_dir("2026-01-31") / "brief.txt", "x\n")
        assert ok.read_text(encoding="utf-8") == "x\n"
        paths.write_text(paths.ops_dir() / "state.json", "{}\n")
        raises(PermissionError, lambda: paths.write_text(root / "scripts" / "leak.txt", "x"))
        raises(PermissionError, lambda: paths.write_text(root / "CLAUDE.md", "x"))
        assert not (root / "scripts").exists()
        assert paths.published_dir(date(2026, 1, 31)).name == "Focus ERP 2026-01-31"
        raises(ValueError, lambda: paths.reports_dir("daily"))


# ── config ────────────────────────────────────────────────────────────────────

@test("a missing config file or key stops with the name of what is missing")
def _():
    with data_root():
        exc = raises(config.ConfigMissing, lambda: config.get("stock.lead_time_days"))
        assert "config.json" in str(exc)
        paths.write_text(paths.config_file(), json.dumps({"stock.lead_time_days": 10}))
        config.use(None)
        assert config.get("stock.lead_time_days") == 10
        exc = raises(config.ConfigMissing, lambda: config.get("load.backup_keep"))
        assert "load.backup_keep" in str(exc)
        raises(KeyError, lambda: config.get("not.a.key"))
        assert "load.backup_keep" in config.missing_keys() and "stock.lead_time_days" not in config.missing_keys()


@test("config types are checked and the monthly step in force is the newest not after the month")
def _():
    config.use(dict(FIXTURE, **{"load.backup_keep": "ten"}))
    try:
        raises(config.ConfigMissing, lambda: config.get("load.backup_keep"))
        config.use(FIXTURE)
        assert config.kpi_step("2025-12") is None
        assert config.kpi_step("2026-02") == Decimal("100")
        assert config.kpi_step("2026-03") == Decimal("200") and config.kpi_step("2027-01") == Decimal("200")
        config.use(dict(FIXTURE, **{"sales.kpi_steps": []}))
        assert config.kpi_step("2026-03") is None
        assert config.number("load.profit_gap_max") == Decimal("0.5")
    finally:
        config.use(None)


@test("the committed key list holds names and types only: no number, no threshold")
def _():
    src = (ROOT / "scripts" / "ops" / "config.py").read_text(encoding="utf-8")
    body = src.split("KEYS:", 1)[1].split("_TYPES", 1)[0]
    assert not any(ch.isdigit() for ch in body.replace("$1", "")), "a digit inside KEYS"
    assert set(FIXTURE) == set(config.KEYS), sorted(set(FIXTURE) ^ set(config.KEYS))


# ── fmt ───────────────────────────────────────────────────────────────────────

@test("money is BHD to the fils, half-up; shares and changes read plainly")
def _():
    grouped = "1,234" + ".500"          # built here: a literal of that shape is what --repo-clean refuses
    assert fmt.bhd("1234.5") == grouped and fmt.bhd(Decimal("0.0005")) == "0.001"
    assert fmt.bhd("1234.5", 0) == "1,235" and fmt.bhd(None) == "0.000"
    assert fmt.q3("2.0005") == Decimal("2.001")
    assert fmt.pct(1, 3) == "33.3%" and fmt.pct(1, 0) == "n/a"
    assert fmt.change(150, 100) == "+50%" and fmt.change(50, 100) == "-50%" and fmt.change(5, 0) == "n/a"
    assert fmt.units(Decimal("1200")) == "1,200"
    assert fmt.day("2026-01-31") == "Sat 31 Jan" and fmt.day(None) == "n/a"


@test("the table is fixed-width; the audit wording guard refuses accusation words")
def _():
    out = fmt.grid(["Van", "Units", "BHD"], [["Van A", "10", "1.500"], ["Van Bee", "2", "10.000"]])
    lines = out.splitlines()
    assert lines[0].startswith("Van") and lines[1].endswith(" 1.500") and lines[2].endswith("10.000")
    assert len({len(ln) for ln in lines}) == 1
    assert fmt.careful("Transfer not received yet: to check") == "Transfer not received yet: to check"
    for bad in ("possible fraud", "stolen units", "stock leakage", "Theft"):
        raises(ValueError, lambda b=bad: fmt.careful(b))


# ── gitinfo ───────────────────────────────────────────────────────────────────

@test("git questions answer on this repository; an unlisted verb is refused")
def _():
    head = gitinfo.head(ROOT)
    assert head and len(head) == 40
    assert gitinfo.resolve(ROOT, head[:12]) == head and gitinfo.resolve(ROOT, "0" * 12) is None
    assert gitinfo.is_ancestor(ROOT, head, "HEAD") is True
    assert isinstance(gitinfo.is_linked_worktree(ROOT), bool)
    assert gitinfo.engine_committed_diff(ROOT, head, "HEAD") == []
    assert isinstance(gitinfo.engine_local_changes(ROOT), list)
    raises(ValueError, lambda: gitinfo._git(ROOT, "commit", "-m", "x"))
    assert ":(exclude)scripts/ops" in gitinfo.ENGINE_PATHS and "requirements.lock" in gitinfo.ENGINE_PATHS
    assert not any(p.endswith(".sql") or "docs" in p for p in gitinfo.ENGINE_PATHS)
    # the owner-side programs (the writer, the sender, the secrets and backup tools) never ship with the API:
    # a commit of them must not make the load check ask for a deploy
    engine = set(gitinfo._lines(gitinfo._git(ROOT, "ls-files", "--", *gitinfo.ENGINE_PATHS)))
    assert "scripts/refresh.py" in engine and "app/main.py" in engine
    assert not any(p.startswith(("scripts/yq_", "scripts/ops/")) for p in engine), sorted(engine)


# ── status ────────────────────────────────────────────────────────────────────

def write_lock(pid: int, beat: datetime, step: str = "refresh") -> None:
    paths.write_text(paths.lock_file(), json.dumps(
        {"run": "010126_0900", "pid": pid, "step": step, "started": beat.isoformat(), "heartbeat": beat.isoformat()}))


@test("the load lock reads none, running or interrupted (dead process, or a stale heartbeat)")
def _():
    now = datetime.now(timezone.utc)
    with data_root():
        assert status.lock_state()["state"] == "none" and status.interrupted_banner() is None
        assert status.pid_alive(os.getpid()) is True and status.pid_alive(0) is False
        write_lock(os.getpid(), now)
        st = status.lock_state(now)
        assert st["state"] == "running" and st["step"] == "refresh" and status.interrupted_banner() is None
        write_lock(os.getpid(), now - timedelta(seconds=status.HEARTBEAT_STALE_S + 30))
        assert status.lock_state(now)["state"] == "interrupted"
        write_lock(2 ** 22 + 12345, now)              # no such process
        assert status.lock_state(now)["state"] == "interrupted"
        assert status.interrupted_banner() == "LOAD INTERRUPTED at refresh: run /yq-load"


@test("status offline prints five lines: data, folder, lock, waiting, action list")
def _():
    class Args:
        offline = True

    with data_root() as root:
        for name in ("010126", "080126"):
            (root / "Uplaods" / name).mkdir(parents=True)
        status.write_state({"last_load": {"run": "010126_0900", "finished_at": "2026-01-01T09:10:00+03:00",
                                          "result": "ok"}})
        paths.write_text(paths.ops_dir() / "alerts.jsonl", "\n".join(json.dumps(r) for r in [
            {"key": "a|1", "level": "ACT", "state": "open"}, {"key": "a|2", "level": "CHECK", "state": "open"},
            {"key": "a|2", "level": "CHECK", "state": "explained"}, {"key": "a|3", "level": "CHECK", "state": "open"}]))
        out = status.run(Args())
        lines = out["text"].splitlines()
        assert len(lines) == 5, lines
        assert "010126_0900" in lines[0] and lines[2] == "Load lock: none."
        assert "080126" in lines[3] and "010126" not in lines[3]
        assert lines[4] == "Action list: 1 ACT, 1 CHECK."
        assert status.unloaded_drops(None) == ["010126", "080126"]


@test("the site state is told on the engine files only")
def _():
    head = gitinfo.head(ROOT)
    st = status.site_state(ROOT, live=head)
    assert st["state"] in ("same", "changed_here"), st     # this checkout against itself
    assert status.site_state(ROOT, live="0" * 12)["state"] == "behind"
    assert set(status.SITE_TEXT) == {"same", "behind", "release_not_live", "changed_here", "unknown"}


# ── the entry point ───────────────────────────────────────────────────────────

def run_cli(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli.main(argv)
    return code, buf.getvalue()


@test("the entry point: unknown command, a command not built yet, the banner, --json")
def _():
    code, out = run_cli(["nonsense"])
    assert code == 1 and "unknown command" in out
    code, out = run_cli([])
    assert code == 0 and "status" in out and "selftest" in out
    assert set(cli.COMMANDS) >= {"status", "selftest", "load", "brief", "watch", "moves", "stock", "report", "kpi"}
    with data_root() as root:
        code, out = run_cli(["status", "--offline", "--data-root", str(root)])
        assert code == 0 and len(out.strip().splitlines()) == 5, out
        write_lock(2 ** 22 + 12345, datetime.now(timezone.utc), step="backup")
        code, out = run_cli(["status", "--offline", "--data-root", str(root)])
        assert out.splitlines()[0] == "LOAD INTERRUPTED at backup: run /yq-load", out
        code, out = run_cli(["status", "--offline", "--json", "--data-root", str(root)])
        payload = json.loads(out)
        assert payload["command"] == "status" and payload["banner"].startswith("LOAD INTERRUPTED")
        assert out.isascii()


# ── selftest ──────────────────────────────────────────────────────────────────

@test("the repo-clean scan finds a live name (whole words) and a three-decimal amount of 1,000 or more")
def _():
    names = ["Zed Trading", "Orla"]
    assert selftest.scan_text("stock for ZED TRADING today", names)
    assert selftest.scan_text("the Orla van", names)
    assert not selftest.scan_text("floral pattern, zedtrading", names)
    assert selftest.scan_text("total " + "7,654" + ".321 ex-VAT", [])       # invented, built at run time
    assert selftest.scan_text("value " + "76543" + ".210", [])
    for fine in ("999.500", "1,234.50", "version 3.1.5", "0.001", "row 1234 of 5678", "12,345 lines"):
        assert not selftest.scan_text(fine, []), fine
    assert all(p.endswith(("/", ".py", ".md", ".yml", ".txt", ".cmd", "-", "_", ".gitignore", ".env.example"))
               for p in selftest.ALLOWED_PATHS)
    assert not any(p.startswith(("web", "app/")) for p in selftest.ALLOWED_PATHS)


@test("the write guard self-check passes in-process")
def _():
    ok, lines = selftest.guard_check()
    assert ok, lines
    assert not db.installed()


# ── the load contract (scripts/ops/loadplan.py) ───────────────────────────────

@test("the token binds files, commits and the database fingerprint, and is letters only")
def _():
    from scripts.ops import loadplan
    with data_root() as root:
        drop = root / "Uplaods" / "010126"
        drop.mkdir(parents=True)
        (drop / "Sales_day_book1.xlsx").write_bytes(b"one")
        (drop / "notes").mkdir()
        m = loadplan.manifest(drop)
        assert list(m) == ["Sales_day_book1.xlsx"] and m["Sales_day_book1.xlsx"]["size"] == 3
        assert m["Sales_day_book1.xlsx"]["sha256"].startswith("sha256:")
        parts = {"drop": m, "staging": m, "head": "a" * 40, "live": "b" * 12,
                 "fingerprint": {"orders": [10, 99], "ingest_runs": 5, "ingest_batches_open": []}}
        token = loadplan.token_of(parts)
        assert len(token) == 32 and token.isalpha() and token == loadplan.token_of(dict(parts))
        (drop / "Sales_day_book1.xlsx").write_bytes(b"two")
        moved = dict(parts, drop=loadplan.manifest(drop), fingerprint={"orders": [11, 100], "ingest_runs": 5,
                                                                       "ingest_batches_open": []})
        assert loadplan.token_of(moved) != token
        why = loadplan.moved_parts(parts, moved)
        assert why == ["the drop folder: Sales_day_book1.xlsx", "the database table orders"], why
        raises(ValueError, lambda: loadplan.token_of({"drop": m}))
        assert loadplan.go_line("010126_0900", token) == f"python -m scripts.yq_load_go --run 010126_0900 --token {token}"
        assert not loadplan.go_line("010126_0900", token).startswith("python -m scripts.ops")


@test("a run has a plan, a go.cmd and a journal; its result is read from the journal")
def _():
    from scripts.ops import loadplan
    with data_root() as root:
        run = "010126_0900"
        assert loadplan.is_run(run) and not loadplan.is_run("010126") and not loadplan.is_run("../x")
        raises(ValueError, lambda: loadplan.run_dir("..\\..\\x"))
        loadplan.write_plan({"run": run, "drop": "Uplaods/010126", "token": "abc", "verdicts": []})
        assert loadplan.read_plan(run)["token"] == "abc"
        cmd = loadplan.write_go_cmd(run, "abc", root).read_text(encoding="utf-8")
        assert "python -m scripts.yq_load_go --run 010126_0900 --token abc" in cmd and "pause" in cmd
        assert loadplan.run_result(run) == "NOT_STARTED" and loadplan.last_step(run) is None
        loadplan.journal_append(run, "started", pid=1)
        loadplan.journal_append(run, "step_start", step="backup")
        assert loadplan.run_result(run) == "RUNNING" and loadplan.last_step(run) == "backup"
        loadplan.journal_append(run, "finished")
        assert loadplan.run_result(run) == "FINISHED" and loadplan.latest_run() == run
        assert loadplan.staging_path(run) == root / "business_data" / "staging" / run
        assert set(loadplan.STEPS) >= {"backup", "pre_gate", "refresh", "reconcile", "post_gate", "publish"}


# ── the action register (scripts/ops/alerts.py) ───────────────────────────────

def item(key, level="CHECK", bhd="1.500", title="Quantity differs: to check"):
    return {"key": key, "level": level, "title": title, "detail": "issued 5, received 4", "who": "Storekeeper",
            "due": "2026-01-05", "bhd": bhd, "evidence": ["SIO:X-26-01-1", "STRV:X-26-01-2"]}


@test("the register: first run is the baseline, later finds are new, vanished ones are cleared")
def _():
    from scripts.ops import alerts
    with data_root():
        c = alerts.record("qty_diff", [item("qty_diff|SIO:X-26-01-1|A1"), item("qty_diff|SIO:X-26-01-2|A1")], "2026-01-02")
        assert c == {"new": 0, "same": 0, "cleared": 0, "baseline": 2, "explained": 0}, c
        assert alerts.open_items() == [] and len(alerts.open_items(include_baseline=True)) == 2
        assert alerts.baseline_summary()[0]["count"] == 2 and alerts.baseline_summary()[0]["bhd"] == Decimal("3.000")
        c = alerts.record("qty_diff", [item("qty_diff|SIO:X-26-01-2|A1"), item("qty_diff|SIO:X-26-01-9|A1", "ACT", "40")],
                          "2026-01-03")
        assert (c["new"], c["same"], c["cleared"]) == (1, 1, 1), c
        new = alerts.open_items()
        assert [r["key"] for r in new] == ["qty_diff|SIO:X-26-01-9|A1"] and new[0]["id"] == "A3"
        assert alerts.current()["qty_diff|SIO:X-26-01-1|A1"]["state"] == "cleared"
        assert alerts.record("qty_diff", [item("qty_diff|SIO:X-26-01-2|A1"),
                                          item("qty_diff|SIO:X-26-01-9|A1", "ACT", "40")], "2026-01-04")["same"] == 2


@test("an explained item stays off for good; ids are stable; keys never hold a name")
def _():
    from scripts.ops import alerts
    with data_root():
        alerts.record("xfer_open", [], "2026-01-01")                       # the rule has run: no baseline later
        alerts.record("seed", [item("seed|SIO:X-26-01-0")], "2026-01-01")
        alerts.record("xfer_open", [item("xfer_open|SIO:X-26-01-5", "ACT", "12")], "2026-01-02")
        row = alerts.open_items()[0]
        assert row["id"] == "A2" and alerts.find("a2")["key"] == "xfer_open|SIO:X-26-01-5"
        alerts.set_state("A2", "explained", "written off with the supplier")
        assert alerts.open_items() == [] and "xfer_open|SIO:X-26-01-5" in alerts.explained()
        c = alerts.record("xfer_open", [item("xfer_open|SIO:X-26-01-5", "ACT", "12")], "2026-01-03")
        assert c["explained"] == 1 and alerts.open_items() == []
        raises(KeyError, lambda: alerts.set_state("A99", "done"))
        raises(ValueError, lambda: alerts.set_state("A2", "snoozed"))
        raises(ValueError, lambda: alerts.record("xfer_open", [item("other|SIO:1")], "2026-01-03"))
        raises(ValueError, lambda: alerts.record("xfer_open", [item("xfer_open|SIO:2", title="possible theft")], "2026-01-03"))
        h = alerts.ref_hash("Van  A")
        assert h == alerts.ref_hash("van a") and h.isalpha() and "van" not in h
        assert alerts.make_key("van_gap", h, "2026-01") == f"van_gap|{h}|2026-01"
        raises(ValueError, lambda: alerts.make_key("r", "a|b"))


@test("open items are ordered ACT first, then by BHD; the block caps what it lists")
def _():
    from scripts.ops import alerts
    with data_root():
        alerts.record("r", [], "2026-01-01")
        alerts.record("seed", [item("seed|x")], "2026-01-01")
        alerts.record("r", [item("r|1", "CHECK", "90"), item("r|2", "ACT", "5"), item("r|3", "ACT", "50"),
                            item("r|4", "CHECK", "1")], "2026-01-02")
        order = [r["key"] for r in alerts.open_items()]
        assert order == ["r|3", "r|2", "r|1", "r|4"], order
        text = alerts.render(alerts.open_items(), limit=2)
        assert text.splitlines()[0].startswith("ID") and "(+2 more open)" in text
        assert "50.000" in text and alerts.render([]) == "Nothing open."
        assert status.register_counts() == {"ACT": 2, "CHECK": 3}


@test("event rules: no first-run baseline, nothing cleared by itself; has_run tells a rule has run")
def _():
    from scripts.ops import alerts
    with data_root():
        assert alerts.has_run("inv_changed") is False
        c = alerts.record("inv_changed", [item("inv_changed|INV-1")], "2026-01-02", baseline=False, clear_missing=False)
        assert (c["new"], c["baseline"]) == (1, 0) and alerts.has_run("inv_changed") is True
        assert [r["key"] for r in alerts.open_items()] == ["inv_changed|INV-1"]
        c = alerts.record("inv_changed", [item("inv_changed|INV-2")], "2026-01-03", baseline=False, clear_missing=False)
        assert c["cleared"] == 0 and len(alerts.open_items()) == 2          # the first stays open until he says so
        alerts.set_state("inv_changed|INV-1", "done", "checked with accounts")
        assert [r["key"] for r in alerts.open_items()] == ["inv_changed|INV-2"]


@test("an explained item comes back when it is more pressing, a quarter larger, or names a new voucher")
def _():
    from scripts.ops import alerts
    with data_root():
        alerts.record("attn", [dict(item("attn|sold_out", "CHECK", "100"), count=4)], "2026-01-02", baseline=False)
        alerts.set_state("attn|sold_out", "explained", "known: waiting for the shipment")
        same = dict(item("attn|sold_out", "CHECK", "110"), count=5)             # a little larger: still explained
        assert alerts.record("attn", [same], "2026-01-03", baseline=False)["explained"] == 1
        assert alerts.open_items() == []
        bigger = dict(item("attn|sold_out", "CHECK", "130"), count=4)            # BHD more than a quarter up
        c = alerts.record("attn", [bigger], "2026-01-04", baseline=False)
        assert c["new"] == 1 and "attn|sold_out" not in alerts.explained()
        row = alerts.open_items()[0]
        assert row["id"] == "A1" and row.get("reopened") is True
        alerts.set_state("A1", "explained", "still known")
        worse = dict(item("attn|sold_out", "ACT", "130"), count=4)               # now more pressing
        assert alerts.record("attn", [worse], "2026-01-05", baseline=False)["new"] == 1
        alerts.set_state("A1", "explained", "known")
        named = dict(item("attn|sold_out", "ACT", "130"), count=4, evidence=["SIO:X-26-01-1", "STRV:X-26-01-2", "SIO:X-26-01-7"])
        assert alerts.record("attn", [named], "2026-01-06", baseline=False)["new"] == 1


@test("an explanation ends with its condition: gone, then back, is news again (not for event rules)")
def _():
    from scripts.ops import alerts
    with data_root():
        alerts.record("attn", [dict(item("attn|stale", "CHECK", None), count=None)], "2026-01-02", baseline=False)
        alerts.set_state("attn|stale", "explained", "known")
        assert alerts.record("attn", [dict(item("attn|stale", "CHECK", None))], "2026-01-03", baseline=False)["explained"] == 1
        alerts.record("attn", [], "2026-01-04", baseline=False)                  # the condition is gone
        assert "attn|stale" not in alerts.explained()
        c = alerts.record("attn", [dict(item("attn|stale", "CHECK", None))], "2026-01-09", baseline=False)
        assert c["new"] == 1 and [r["key"] for r in alerts.open_items()] == ["attn|stale"]
        alerts.record("inv_changed", [item("inv_changed|INV-1")], "2026-01-02", baseline=False, clear_missing=False)
        alerts.set_state("inv_changed|INV-1", "explained", "accounts corrected it")
        alerts.record("inv_changed", [], "2026-01-03", baseline=False, clear_missing=False)
        assert "inv_changed|INV-1" in alerts.explained()                         # an event stays explained
        config.use(dict(FIXTURE, **{"sales.kpi_steps": [{"from": "2026-01", "bhd": "NaN"}]}))
        try:
            raises(config.ConfigMissing, lambda: config.kpi_step("2026-03"))
        finally:
            config.use(None)


@test("a long title cannot widen the action list")
def _():
    from scripts.ops import alerts
    with data_root():
        alerts.record("r", [item("r|1", title="A report was held back twice in a row because its cost of sales "
                                              "is short of the ledger and nobody has posted it yet")],
                      "2026-01-02", baseline=False)
        lines = alerts.render(alerts.open_items()).splitlines()
        what = lines[1].split("  ")
        assert max(len(ln) for ln in lines) < 160 and any(cell.strip().endswith("...") for cell in what), lines


@test("files are copied and removed only under business_data/ and exports/")
def _():
    with data_root() as root:
        src = root / "Uplaods" / "010126" / "a.xlsx"
        src.parent.mkdir(parents=True)
        src.write_bytes(b"abc")
        dst = paths.copy_file(src, paths.staging_dir() / "010126_0900" / "a.xlsx")
        assert dst.read_bytes() == b"abc" and src.read_bytes() == b"abc"
        assert not list(dst.parent.glob("*.copying"))
        assert paths.allowed_output(dst) and not paths.allowed_output(src)
        raises(PermissionError, lambda: paths.copy_file(src, root / "Uplaods" / "010126" / "b.xlsx"))
        raises(PermissionError, lambda: paths.remove_file(src))
        assert paths.remove_file(dst) is True and paths.remove_file(dst) is False
        assert src.exists()


@test("a cut-off rehearsal is not an interrupted load; a load closed after it began to write is named")
def _():
    from scripts.ops import loadplan
    now = datetime.now(timezone.utc)
    with data_root():
        paths.write_text(paths.lock_file(), json.dumps(
            {"run": "000000_0900", "pid": 2 ** 22 + 12345, "step": "refresh (rehearsal)",
             "started": now.isoformat(), "heartbeat": now.isoformat()}))
        st = status.lock_state(now)
        assert st["state"] == "none" and st["rehearsal_left"] == "000000_0900"
        assert status.interrupted_banner() is None
        paths.lock_file().unlink()
        run = "010126_0900"
        loadplan.journal_append(run, "started")
        loadplan.journal_append(run, "step_start", step="refresh")
        assert status.failed_load() is None
        loadplan.journal_append(run, "failed", step="refresh", written=True)
        assert status.failed_load() == {"run": run, "step": "refresh"}
        assert status.interrupted_banner().startswith("LAST LOAD FAILED at refresh (run 010126_0900)")
        status.write_state({"last_load": {"run": "020126_0900",
                                          "finished_at": (now + timedelta(minutes=5)).isoformat()}})
        assert status.failed_load() is None and status.interrupted_banner() is None
        paths.write_text(paths.state_file(), "[]")
        assert status.read_state() == {}
        paths.write_text(paths.lock_file(), "[1, 2]")
        assert status.read_lock() == {"run": "?", "step": "?", "pid": 0}


@test("site_state keeps the live commit exactly as it was reported; a bad sales step names the key")
def _():
    head = gitinfo.head(ROOT)
    assert status.site_state(ROOT, live=head[:12])["live_full"] == head[:12]
    config.use(dict(FIXTURE, **{"sales.kpi_steps": [{"from": "2026-01", "bhd": "3,000"}, {"from": "2026-02", "bhd": 0}]}))
    try:
        assert config.kpi_step("2026-03") == Decimal("3000")          # a typed comma is read; a step of 0 is none
        config.use(dict(FIXTURE, **{"sales.kpi_steps": ["3000"]}))
        exc = raises(config.ConfigMissing, lambda: config.kpi_step("2026-03"))
        assert "sales.kpi_steps" in str(exc)
    finally:
        config.use(None)


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
