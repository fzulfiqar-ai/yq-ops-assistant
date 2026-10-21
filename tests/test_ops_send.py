"""The sender of a load's e-mail (scripts/yq_send.py), on invented deliveries and a stand-in mail service.

    python -m tests.test_ops_send

Same lightweight runner as the other suites (no pytest). No network: the HTTP opener is a stand-in that
records each request and answers as Resend does. No real secret: YQ_SECRETS_OFF=1 is set before any import,
so scripts/yq_secrets.py refuses Credential Manager and the registry, and every test hands its own invented
key in. The Windows box is a stand-in. Every folder is a temporary one; every address is invented.

Covered:
  * off: nothing is sent and that is said; test: only the test address, [TEST] in the subject, never counted;
  * a send: the request (from, to, subject, html, text, reply_to), the key in the Authorization header only,
    an Idempotency-Key; recorded in the delivery journal and state.json without the key anywhere;
  * no second copy: the same kind to the same recipients is refused; --resend asks at the box (No refuses,
    Yes sends with a new Idempotency-Key); a test copy never blocks the real one;
  * failures: a refused request (4xx) is recorded with the service's message; a server error or no answer is
    retried once with the same Idempotency-Key; no key on the PC says how to set it; a file that changed since
    the brief wrote it is refused; a newer load on record refuses an older load's brief;
  * a load that wrote nothing has nothing to send; the settings are checked (mode, addresses);
  * one full e-mail per data set: a load of the very Focus files whose full e-mail went already sends nothing
    and its record reads "skipped: same data as run <run>" (journal and state.json); changed data, another data
    date go; a staged copy the check made itself or a report the load did not take changes nothing; a test copy
    never counts and is never held back; a "needs a look" note on that data is held back too, a failed load's
    note always goes; --resend asks at the box, naming the load whose e-mail carried the data;
  * the retry after a failure is printed only where it would be accepted: a newer load has its own e-mail;
  * the file itself (AST): it imports no database driver, no app module and no other way to send.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import traceback
import urllib.error
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"
os.environ.pop("YQ_MAIN_CHECKOUT", None)
os.environ["YQ_SECRETS_OFF"] = "1"

from scripts import yq_secrets  # noqa: E402
from scripts import yq_send as ys  # noqa: E402
from scripts.ops import loadplan, paths, status  # noqa: E402

TESTS: list[tuple[str, object]] = []
RUN = "151026_0900"
KEY = "re_" + "k" * 20                    # an invented key: it must appear in no file


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


class data_root:
    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        paths.set_data_root(self._tmp.name)
        return paths.data_root()

    def __exit__(self, *exc):
        paths.set_data_root(None)
        self._tmp.cleanup()
        return False


def settings(mode="on", **over):
    conf = {"mode": mode, "from": "YQ Bahrain <reports@mail.example.invalid>", "to": ["owner@example.invalid"],
            "test_to": ["tester@example.invalid"], "reply_to": "owner@example.invalid"}
    conf.update(over)
    paths.write_text(paths.ops_dir() / "delivery.json", json.dumps(conf))


def delivery(kind="full", run=RUN, last_run=RUN) -> Path:
    """What `python -m scripts.ops deliver` writes for a load, and the pack row that names it."""
    folder = paths.exports_dir(date(2026, 10, 14)) / "delivery" / run
    files = {"html": "<p>invented brief</p>", "text": "invented brief\n"}
    for key, body in files.items():
        paths.write_text(folder / ("email.html" if key == "html" else "email.txt"), body)
    man = {"run": run, "kind": kind, "subject": "YQ Daily Sales & Operations Brief - Thu 15 Oct 2026",
           "files": {"html": "email.html", "text": "email.txt"},
           "sha256": {k: hashlib.sha256(v.encode("utf-8")).hexdigest() for k, v in files.items()}}
    mpath = paths.write_text(folder / "manifest.json", json.dumps(man))
    loadplan.delivery_append(run, "pack", kind=kind, manifest=str(mpath), text="prepared")
    status.write_state({"last_load": {"run": last_run}})
    return folder


class Response:
    def __init__(self, body: dict):
        self.body = json.dumps(body).encode("utf-8")

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Opener:
    """Records every request; answers from `answers` in order (a dict = 200 with that body, an int = that HTTP
    status, an exception = raised)."""

    def __init__(self, *answers):
        self.answers, self.requests = list(answers) or [{"id": "em_1"}], []

    def __call__(self, req, timeout=None):
        self.requests.append({"url": req.full_url, "headers": dict(req.header_items()),
                              "body": json.loads(req.data.decode("utf-8")), "timeout": timeout})
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, int):
            raise urllib.error.HTTPError(req.full_url, answer, "error", {},
                                         io.BytesIO(json.dumps({"message": f"invented error {answer}"}).encode()))
        return Response(answer)


def send(**kw):
    kw.setdefault("key_reader", lambda: KEY)
    kw.setdefault("say", lambda s: None)
    return ys.send(RUN, **kw)


def rows(event=None):
    return [r for r in loadplan.delivery_read(RUN) if event is None or r.get("event") == event]


def no_key_anywhere(root: Path) -> None:
    for f in root.rglob("*"):
        if f.is_file():
            assert KEY not in f.read_text(encoding="utf-8", errors="replace"), f"the key is in {f}"


# ── loads with their data (one full e-mail per data set) ─────────────────────────────────────────

OTHER, LATER = "151026_0920", "151026_0940"          # the same folder checked and loaded again, minutes later
SALES, STOCK, PROFIT = "Sales_day_book1.xlsx", "Stock_balance1.xlsx", "Profit1.xlsx"
REPORTS = {SALES: b"sales as exported", STOCK: b"stock, every warehouse", PROFIT: b"profitability as exported"}
EXPECTED = {"sales_to": "2026-10-15", "invoices": 7, "stock": {"items": 3}, "lines": ["Sales to Thu 15 Oct: 7 invoices."]}
OPTIONS = {"dev": False, "pricebook": None, "with_profitability": False, "replace_ok": False, "partial_ok": False}


def fingerprint(body: bytes) -> dict:
    return {"size": len(body), "sha256": "sha256:" + hashlib.sha256(body).hexdigest()}


def planned(run: str, reports: dict | None = None, data_date: str = "2026-10-15",
            trimmed: bytes = b"stock, main store block", expected: dict | None = None, options: dict | None = None,
            head: str = "a" * 40) -> None:
    """The check's record of a load (plan.json, as scripts/ops/load_check.py writes it): each report of the drop
    folder with its size and sha256 (token_parts.drop); the copies it staged (token_parts.staging), where the stock
    balance trimmed to the main store is the check's own copy, made anew by every check; the table, in which the
    report held back has no staged copy; the figures it expects, its options and the programs (token_parts.head)."""
    reports = dict(REPORTS if reports is None else reports)
    verdicts = [{"file": SALES, "kind": "order_lines", "verdict": "load", "staged": SALES},
                {"file": STOCK, "kind": "stock_balance", "verdict": "trim", "staged": "Main_store_" + STOCK},
                {"file": PROFIT, "kind": "product_profitability", "verdict": "hold_back", "staged": None}]
    staging = {SALES: fingerprint(reports[SALES]), "Main_store_" + STOCK: fingerprint(trimmed)}
    loadplan.write_plan({"run": run, "data_date": data_date, "token": "invented", "verdicts": verdicts,
                         "expected": dict(EXPECTED if expected is None else expected),
                         "options": dict(OPTIONS if options is None else options),
                         "token_parts": {"drop": {n: fingerprint(b) for n, b in reports.items()}, "staging": staging,
                                         "head": head, "live": "b" * 12,
                                         "fingerprint": {"orders": [1, int(run[-4:])]}}})   # each check sees its own


def loaded(run: str, kind: str = "full", **plan) -> None:
    """A load that closed and whose delivery was prepared: its plan, its closing row, its manifest. A load that
    finished is the last load on record; one that failed after it began to write never becomes it."""
    planned(run, **plan)
    if kind == "failed":
        loadplan.journal_append(run, "failed", result="failed", written=True, step="refresh", text="LOAD FAILED.")
        last = str((status.read_state().get("last_load") or {}).get("run") or "")
    else:
        loadplan.journal_append(run, "finished", result="ok", text="LOAD FINISHED.")
        last = run
    delivery(kind=kind, run=run, last_run=last)


def send_of(run: str, **kw) -> int:
    kw.setdefault("key_reader", lambda: KEY)
    kw.setdefault("say", lambda s: None)
    return ys.send(run, **kw)


def rows_of(run: str, event=None):
    return [r for r in loadplan.delivery_read(run) if event is None or r.get("event") == event]


def main_says(*argv) -> tuple[int, str]:
    out = io.StringIO()
    try:
        stdout, sys.stdout = sys.stdout, out
        code = ys.main(list(argv))
    finally:
        sys.stdout = stdout
    return code, out.getvalue()


@test("off: nothing is sent and that is said; no settings file at all is off too")
def _():
    with data_root():
        delivery()
        said = []
        assert send(opener=Opener(), say=said.append) == ys.EXIT_OK and "off" in said[0]
        assert rows("not_sent")[0]["reason"] == "off"
        settings("off")
        opener = Opener()
        assert send(opener=opener) == ys.EXIT_OK and opener.requests == []


@test("a send: the request as the service wants it, the key only in its header, an idempotency key; recorded")
def _():
    with data_root() as root:
        settings()
        delivery()
        opener = Opener({"id": "em_42"})
        assert send(opener=opener, auto=True) == ys.EXIT_OK
        req = opener.requests[0]
        assert req["url"] == ys.API and req["timeout"] == ys.TIMEOUT_S
        assert req["body"]["to"] == ["owner@example.invalid"] and req["body"]["from"].startswith("YQ Bahrain <")
        assert req["body"]["subject"].startswith("YQ Daily Sales") and req["body"]["html"] == "<p>invented brief</p>"
        assert req["body"]["text"] == "invented brief\n" and req["body"]["reply_to"] == "owner@example.invalid"
        assert req["headers"]["Authorization"] == "Bearer " + KEY and len(req["headers"]["Idempotency-key"]) == 64
        sent = rows("sent")[0]
        assert sent["id"] == "em_42" and sent["to_count"] == 1 and sent["how"] == "auto" and sent["kind"] == "full"
        assert status.read_state()["last_delivery"]["event"] == "sent"
        no_key_anywhere(root)


@test("no second copy: the same e-mail is refused; --resend asks at the box, No refuses, Yes sends anew")
def _():
    with data_root():
        settings()
        delivery()
        first = Opener()
        assert send(opener=first) == ys.EXIT_OK
        try:
            send(opener=Opener())
            raise AssertionError("a second copy went")
        except ys.Refused as exc:
            assert "went already" in str(exc) and "--resend" in str(exc)
        shown = []

        def box(title, text, timeout):
            shown.append(text)
            return answer[0]
        answer = ["no"]
        try:
            send(opener=Opener(), resend=True, box=box)
            raise AssertionError("No at the box sent it")
        except ys.Refused:
            pass
        answer[0] = "yes"
        again = Opener()
        assert send(opener=again, resend=True, box=box) == ys.EXIT_OK and len(shown) == 2
        assert again.requests[0]["headers"]["Idempotency-key"] != first.requests[0]["headers"]["Idempotency-key"]
        assert [r["how"] for r in rows("sent")] == ["by hand", "resend"]


@test("test mode: only the test address, [TEST] in the subject, never counted against the real e-mail")
def _():
    with data_root():
        settings()
        delivery()
        t = Opener()
        assert send(opener=t, test=True) == ys.EXIT_OK
        assert t.requests[0]["body"]["to"] == ["tester@example.invalid"]
        assert t.requests[0]["body"]["subject"].startswith("[TEST] ") and t.requests[0]["body"]["text"].startswith("TEST:")
        assert rows("test_sent") and not rows("sent")
        real = Opener()
        assert send(opener=real) == ys.EXIT_OK and real.requests[0]["body"]["to"] == ["owner@example.invalid"]
    with data_root():
        settings("test")
        delivery()
        t = Opener()
        assert send(opener=t) == ys.EXIT_OK and t.requests[0]["body"]["to"] == ["tester@example.invalid"]


@test("failures: a refused request is recorded; a server error is tried once more with the same key; no key")
def _():
    with data_root() as root:
        settings()
        delivery()
        try:
            send(opener=Opener(422))
            raise AssertionError("it went")
        except ys.Failed as exc:
            assert "HTTP 422" in str(exc) and "invented error 422" in str(exc)
        flaky = Opener(503, {"id": "em_2"})
        assert send(opener=flaky) == ys.EXIT_OK and len(flaky.requests) == 2
        keys = {r["headers"]["Idempotency-key"] for r in flaky.requests}
        assert len(keys) == 1, "a retry of one attempt carries the same idempotency key"
        no_key_anywhere(root)
    with data_root():
        settings()
        delivery()
        try:
            send(opener=Opener(), key_reader=lambda: None)
            raise AssertionError("it went without a key")
        except ys.Failed as exc:
            assert "cmdkey /generic:YQ_RESEND_API_KEY" in str(exc)
        assert yq_secrets.resend_key() is None and yq_secrets.backup_password() == (None, None), "secrets are off in tests"


@test("main(): a failure is recorded with the line that retries it; nothing is reloaded")
def _():
    with data_root():
        settings()
        delivery()
        real = ys.yq_secrets.resend_key
        ys.yq_secrets.resend_key = lambda: None
        out = io.StringIO()
        try:
            stdout, sys.stdout = sys.stdout, out
            code = ys.main(["--run", RUN])
        finally:
            sys.stdout = stdout
            ys.yq_secrets.resend_key = real
        assert code == ys.EXIT_ERROR and f"Its retry: python -m scripts.yq_send --run {RUN}" in out.getvalue()
        failed = rows("failed")[-1]
        assert failed["next"] == f"Its retry: python -m scripts.yq_send --run {RUN}"
    assert ys.main(["--run", "not-a-run"]) == ys.EXIT_REFUSED


@test("refused: a file that changed since the brief wrote it; an older load's brief once a newer load is on record")
def _():
    with data_root():
        settings()
        folder = delivery()
        (folder / "email.html").write_text("<p>changed by hand</p>", encoding="utf-8")
        try:
            send(opener=Opener())
            raise AssertionError("a changed file went")
        except ys.Failed as exc:
            assert "changed since" in str(exc)
    for last in ("161026_0900", ""):                  # a newer load on record, or none at all (state.json unreadable)
        with data_root():
            settings()
            delivery(last_run=last)
            try:
                send(opener=Opener())
                raise AssertionError("an older brief went")
            except ys.Refused as exc:
                assert f"the last load on record is run {last or '(none)'}, not this one" in str(exc), str(exc)


@test("no second copy to anybody: one more address is refused without the box; a test copy never moves the real key")
def _():
    with data_root():
        settings()
        delivery()
        assert send(opener=Opener()) == ys.EXIT_OK
        settings(to=["owner@example.invalid", "second@example.invalid"])
        try:
            send(opener=Opener())
            raise AssertionError("a second address sent it to everybody again")
        except ys.Refused as exc:
            assert "went already" in str(exc)
    with data_root():
        settings()
        delivery()
        lost = Opener(422)
        try:
            send(opener=lost)                       # it did not go (or went and the answer was lost)
        except ys.Failed:
            pass
        assert send(opener=Opener(), test=True) == ys.EXIT_OK
        again = Opener()
        assert send(opener=again) == ys.EXIT_OK
        assert again.requests[0]["headers"]["Idempotency-key"] == lost.requests[0]["headers"]["Idempotency-key"]


@test("a sent e-mail whose record cannot be written says so; a brief the deliver command refuses is a refusal")
def _():
    with data_root():
        settings()
        delivery()
        real_append, real_sleep = ys.loadplan.delivery_append, ys.time.sleep

        def append(run, event, **fields):
            if event == "sent":
                raise OSError("held by another program")
            return real_append(run, event, **fields)
        ys.loadplan.delivery_append, ys.time.sleep = append, (lambda s: None)
        said = []
        try:
            assert send(opener=Opener(), say=said.append) == ys.EXIT_OK
        finally:
            ys.loadplan.delivery_append, ys.time.sleep = real_append, real_sleep
        assert said[-1].startswith("Delivery: e-mail sent") and "do not send it again by hand" in said[-1], said
    with data_root():
        settings()
        status.write_state({"last_load": {"run": RUN}})             # no manifest: the sender asks for the brief
        real_run = ys.subprocess.run
        ys.subprocess.run = lambda *a, **k: subprocess.CompletedProcess(a, 2, stdout="Delivery of run x: NOT prepared: "
                                                                                    "the database was written after this "
                                                                                    "load.", stderr="")
        try:
            send(opener=Opener())
            raise AssertionError("a brief of other figures went")
        except ys.Refused as exc:
            assert "the database was written after this load" in str(exc)
        finally:
            ys.subprocess.run = real_run


@test("a load that wrote nothing has nothing to send; the settings are checked")
def _():
    with data_root():
        settings()
        delivery(kind="none")
        opener = Opener()
        assert send(opener=opener) == ys.EXIT_OK and opener.requests == []
    for bad in ({"mode": "maybe"}, {"to": ["not an address"]}, {"from": "nobody"}):
        with data_root():
            settings(**bad)
            delivery()
            try:
                send(opener=Opener())
                raise AssertionError(f"accepted {bad}")
            except ys.Failed:
                pass


@test("one full e-mail per data set: the same Focus files loaded again send nothing, and the record says whose it was")
def _():
    with data_root():
        settings()
        loaded(RUN)
        assert send_of(RUN, opener=Opener()) == ys.EXIT_OK and rows_of(RUN, "sent")
        loaded(OTHER, trimmed=b"stock, main store block, trimmed anew")      # the same files; the check's own copy is new
        opener, said = Opener(), []
        assert send_of(OTHER, opener=opener, say=said.append, auto=True) == ys.EXIT_OK
        assert opener.requests == [], "the same data went out twice"
        assert len(said) == 1 and said[0].startswith(f"Delivery: no e-mail was sent: the data matches run {RUN} (its "
                                                     "e-mail went on "), said
        row = rows_of(OTHER)[-1]
        assert row["event"] == "not_sent" and row["reason"] == f"skipped: same data as run {RUN}", row
        assert row["same_as"] == RUN and row["kind"] == "full" and row["text"].startswith("Skipped: same data as run ")
        assert not rows_of(OTHER, "sent")
        last = status.read_state()["last_delivery"]
        assert (last["run"], last["event"], last["same_as"]) == (OTHER, "not_sent", RUN), last
        assert last["reason"] == f"skipped: same data as run {RUN}"
        # the data set: the data date and each report the load took, as it lay in the drop folder
        mine = ys.data_set(OTHER)
        assert mine == ys.data_set(RUN) and mine["data_date"] == "2026-10-15" and len(mine["reports"]) == 2, mine
        assert not any(fingerprint(b"stock, main store block, trimmed anew")["sha256"] in r for r in mine["reports"])
        assert not any(fingerprint(REPORTS[PROFIT])["sha256"] in r for r in mine["reports"]), "a report held back counted"
        assert any(fingerprint(REPORTS[STOCK])["sha256"] in r for r in mine["reports"]), "the trimmed report's own file"


@test("other data is sent: a changed report, another data date; a report the load did not take changes nothing")
def _():
    for change, goes in (({"reports": dict(REPORTS, **{SALES: b"sales with one more invoice"})}, True),
                         ({"data_date": "2026-10-16"}, True),
                         ({"reports": dict(REPORTS, **{PROFIT: b"profitability exported again"})}, False)):
        with data_root():
            settings()
            loaded(RUN)
            assert send_of(RUN, opener=Opener()) == ys.EXIT_OK
            loaded(OTHER, **change)
            opener = Opener()
            assert send_of(OTHER, opener=opener) == ys.EXIT_OK
            assert bool(opener.requests) is goes and bool(rows_of(OTHER, "sent")) is goes, change


@test("the same files loaded again after a fix or with other options are other data: other figures, options, programs")
def _():
    for change in ({"expected": dict(EXPECTED, stock={"items": 4})},          # the stock trimmed another way now
                   {"options": dict(OPTIONS, with_profitability=True)},        # the profitability report loaded anyway
                   {"options": dict(OPTIONS, pricebook="load")},               # the price book loaded this time
                   {"options": dict(OPTIONS, partial_ok=True)},
                   {"head": "c" * 40}):                                        # other programs (a parser fix)
        with data_root():
            settings()
            loaded(RUN)
            assert send_of(RUN, opener=Opener()) == ys.EXIT_OK
            loaded(OTHER, **change)
            opener = Opener()
            assert send_of(OTHER, opener=opener) == ys.EXIT_OK and len(opener.requests) == 1, change
    with data_root():                                   # the check's lines of words are no part of it
        settings()
        loaded(RUN)
        assert send_of(RUN, opener=Opener()) == ys.EXIT_OK
        loaded(OTHER, expected=dict(EXPECTED, lines=["Sales to Thu 15 Oct: the same, worded another way."]))
        opener = Opener()
        assert send_of(OTHER, opener=opener) == ys.EXIT_OK and opener.requests == []


@test("only the newest e-mail that went counts: after a failed load's note the same data goes again; so after other data")
def _():
    with data_root():                                   # A full -> C fails after it began to write, its note goes -> B
        settings()
        loaded(RUN)
        assert send_of(RUN, opener=Opener()) == ys.EXIT_OK
        loaded(OTHER, kind="failed")
        assert send_of(OTHER, opener=Opener()) == ys.EXIT_OK and rows_of(OTHER, "sent")[0]["kind"] == "failed"
        loaded(LATER)                                   # the repair: the same files, loaded whole
        opener = Opener()
        assert send_of(LATER, opener=opener) == ys.EXIT_OK and len(opener.requests) == 1, "the last e-mail said FAILED"
    with data_root():                                   # A (data D) at 09:00, then C (other data) at 09:20, then B (D)
        settings()
        a, c = "151026_0950", "151026_0910"             # C is the newer e-mail, though its name sorts first
        loaded(a)
        loaded(c, reports=dict(REPORTS, **{SALES: b"sales with one more invoice"}))
        loaded(LATER)
        loadplan.delivery_append(a, "sent", kind="full", at="2026-10-15T06:00:00+00:00")
        loadplan.delivery_append(c, "sent", kind="full", at="2026-10-15T06:20:00+00:00")
        opener = Opener()
        assert send_of(LATER, opener=opener) == ys.EXIT_OK and len(opener.requests) == 1, "an older e-mail decided it"
    with data_root():                                   # A (D), C (other data), B (D) inside one second
        settings()
        loaded(RUN)
        loaded(OTHER, reports=dict(REPORTS, **{SALES: b"sales with one more invoice"}))
        loaded(LATER)
        for run in (RUN, OTHER):
            loadplan.delivery_append(run, "sent", kind="full", at="2026-10-15T06:00:00+00:00")
        assert ys.last_sent(LATER)["run"] == OTHER, "a tie goes to the later run"
        opener = Opener()
        assert send_of(LATER, opener=opener) == ys.EXIT_OK and len(opener.requests) == 1


@test("a delivery journal that cannot be read (not UTF-8, broken lines) is passed over: it never stops a send")
def _():
    with data_root():
        settings()
        old = "011026_0900"
        loadplan.run_dir(old).mkdir(parents=True)
        (loadplan.run_dir(old) / "delivery.jsonl").write_bytes(b'\xff\xfe not text\n{"event": "sent", broken\n')
        loaded(RUN)
        assert send_of(RUN, opener=Opener()) == ys.EXIT_OK and rows_of(RUN, "sent")
        assert ys.last_sent(OTHER)["run"] == RUN
        loaded(OTHER)
        opener = Opener()
        assert send_of(OTHER, opener=opener) == ys.EXIT_OK and opener.requests == [], "the same data went twice"


@test("no retry is offered exactly where the retry would be refused as an older load's brief")
def _():
    newer = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat(timespec="seconds")
    no_retry = f"No retry is needed: a newer load (run {OTHER}) has its own e-mail."
    nobody = "No retry: no load is on record as the last load, so this load's brief cannot be sent."
    for kind, look, made, mode, last, said in (
            ("full", None, True, "on", OTHER, no_retry),
            ("look", None, True, "on", OTHER, None),             # a note: never refused as an older load's
            ("full", ["post_gate"], False, "on", OTHER, None),   # no brief made yet: a check needing a look makes a note
            ("full", ["publish"], False, "on", OTHER, no_retry),  # ... a publish that needs a look does not
            ("full", None, True, "test", OTHER, None),           # a test copy of a brief that is made is never refused
            ("full", None, False, "test", OTHER, no_retry),      # ... the deliver command refuses to make it
            ("full", None, True, "off", OTHER, None),            # off: nothing is refused, nothing is sent
            ("full", None, True, "on", RUN, None),               # the last load on record: its retry goes
            ("full", None, True, "on", "", nobody)):
        with data_root():
            settings(mode)
            planned(RUN)
            loadplan.journal_append(RUN, "finished", result="ok", needs_look=look, text="LOAD FINISHED.")
            if made:
                delivery(kind=kind, run=RUN, last_run=RUN)
            status.write_state({"last_load": {"run": last, "finished_at": newer} if last else {}})
            got = ys.no_retry_needed(RUN)
            assert got == said, (kind, look, made, mode, last, got)


def engine_trees(folder: Path) -> tuple[str, str, str, str]:
    """Four git trees in a throwaway repository (plumbing only: no commit, no hook, no signature): a start, the
    start with a doc, a skills module and the sender changed (no engine file: scripts/ops/gitinfo.py ENGINE_PATHS),
    the start with the loader changed, and the start with the pairing module changed (the one file of scripts/ops
    the API runs: gitinfo.ENGINE_FILES_IN_OPS)."""
    def git(*args, data=None):
        return subprocess.run(["git", "-C", str(folder), *args], input=data, capture_output=True, text=True,
                              check=True).stdout.strip()
    git("init", "-q")

    def tree(files: dict) -> str:
        git("read-tree", "--empty")
        for path, body in files.items():
            git("update-index", "--add", "--cacheinfo", f"100644,{git('hash-object', '-w', '--stdin', data=body)},{path}")
        return git("write-tree")
    start = {"scripts/loader.py": "x = 1\n", "docs/a.md": "one\n", "scripts/ops/brief.py": "y = 1\n",
             "scripts/ops/pairing.py": "p = 1\n"}
    return (tree(start),
            tree(dict(start, **{"docs/a.md": "two\n", "scripts/ops/brief.py": "y = 2\n", "scripts/yq_send.py": "z\n"})),
            tree(dict(start, **{"scripts/loader.py": "x = 2\n"})),
            tree(dict(start, **{"scripts/ops/pairing.py": "p = 2\n"})))


@test("the programs are the engine files (the pairing module among them) and the options as the box reads them: "
      "a docs commit or 'skip' is the same")
def _():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        start, docs, loader, pairing = engine_trees(Path(tmp))
        assert ys.same_programs(start, docs, Path(tmp)) and not ys.same_programs(start, loader, Path(tmp))
        assert not ys.same_programs(start, pairing, Path(tmp)), "the pairing module ships with the API"
        assert not ys.same_programs(start, "--output=x", Path(tmp)), "only a commit id is handed to git"
        assert not ys.same_programs("a" * 40, "c" * 40), "commits git does not know are other programs"
        real = ys.GIT_ROOT
        ys.GIT_ROOT = Path(tmp)
        try:
            for head, goes in ((docs, False), (loader, True), (pairing, True)):
                with data_root():
                    settings()
                    loaded(RUN, head=start)
                    assert send_of(RUN, opener=Opener()) == ys.EXIT_OK
                    loaded(OTHER, head=head)
                    opener = Opener()
                    assert send_of(OTHER, opener=opener) == ys.EXIT_OK and bool(opener.requests) is goes, head
        finally:
            ys.GIT_ROOT = real
    with data_root():                                   # the price book left out by default or on his word: the same
        settings()
        loaded(RUN, options=dict(OPTIONS, pricebook=None))
        assert send_of(RUN, opener=Opener()) == ys.EXIT_OK
        loaded(OTHER, options=dict(OPTIONS, pricebook="skip", dev=None))
        opener = Opener()
        assert send_of(OTHER, opener=opener) == ys.EXIT_OK and opener.requests == []


@test("a delivery.json or a journal row of the wrong shape never keeps a failure from being recorded")
def _():
    with data_root():
        settings(to=5)                                   # not a list of addresses
        loaded(RUN)
        code, said = main_says("--run", RUN)
        retry = f"Its retry: python -m scripts.yq_send --run {RUN}"
        assert code == ys.EXIT_ERROR and "TypeError" in said and said.rstrip().endswith(retry), said
        assert rows_of(RUN, "failed")[-1]["next"] == retry
    with data_root():
        settings()
        planned(RUN)
        loadplan.journal_append(RUN, "finished", result="ok", needs_look=5, text="LOAD FINISHED.")
        status.write_state({"last_load": {"run": OTHER}})
        assert ys.no_retry_needed(RUN) is None


@test("no brief made yet and the deliver command would refuse it for another reason: no retry, and why")
def _():
    later_failed = ("No retry: a later load (run 151026_0920) failed after it began to write, so this load's brief "
                    "cannot be made now.")
    written = ("No retry: the database was written after this load (a Data-page upload or another load), so this "
               "load's brief cannot be made now.")
    with data_root():                                   # a later load failed after it began to write
        settings(mode="maybe")
        planned(RUN)
        loadplan.journal_append(RUN, "finished", result="ok", text="LOAD FINISHED.")
        status.write_state({"last_load": {"run": RUN}})
        loadplan.journal_append(OTHER, "failed", result="failed", written=True, step="refresh", text="LOAD FAILED.",
                                at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(timespec="seconds"))
        assert ys.no_retry_needed(RUN) == later_failed
        code, said = main_says("--run", RUN)
        assert code == ys.EXIT_ERROR and said.rstrip().endswith(later_failed) and "Its retry" not in said, said
    with data_root():                                   # the deliver command refused it already: its row says why
        settings()
        planned(RUN)
        loadplan.journal_append(RUN, "finished", result="ok", text="LOAD FINISHED.")
        status.write_state({"last_load": {"run": RUN}})
        loadplan.delivery_append(RUN, "pack", kind="refused", why="the database was written after this load (a "
                                 "Data-page upload or another load)", text="Not prepared: the database was written.")
        assert ys.no_retry_needed(RUN) == written
    with data_root():                                   # the brief was made: the retry sends it, whatever came later
        settings()
        loaded(RUN)
        loadplan.journal_append(OTHER, "failed", result="failed", written=True, step="refresh", text="LOAD FAILED.",
                                at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(timespec="seconds"))
        assert ys.no_retry_needed(RUN) is None


@test("a test copy never counts as the data set's e-mail and is never held back; a data set that cannot be told goes")
def _():
    with data_root():
        settings("test")
        loaded(RUN)
        assert send_of(RUN, opener=Opener()) == ys.EXIT_OK and rows_of(RUN, "test_sent") and not rows_of(RUN, "sent")
        settings()
        loaded(OTHER)
        real = Opener()
        assert send_of(OTHER, opener=real) == ys.EXIT_OK and real.requests[0]["body"]["to"] == ["owner@example.invalid"]
        loaded(LATER)
        test_copy = Opener()
        assert send_of(LATER, opener=test_copy, test=True) == ys.EXIT_OK and len(test_copy.requests) == 1
        assert rows_of(LATER, "test_sent") and not rows_of(LATER, "not_sent")
    with data_root():
        settings()
        loaded(RUN)
        assert send_of(RUN, opener=Opener()) == ys.EXIT_OK
        loaded(OTHER)
        (loadplan.run_dir(OTHER) / "plan.json").unlink()            # nothing says what this load took: it goes
        opener = Opener()
        assert ys.data_set(OTHER) is None and send_of(OTHER, opener=opener) == ys.EXIT_OK and opener.requests


@test("--resend stays the deliberate way: the box names the load whose e-mail carried the data; No sends nothing")
def _():
    with data_root():
        settings()
        loaded(RUN)
        assert send_of(RUN, opener=Opener()) == ys.EXIT_OK
        loaded(OTHER)
        assert send_of(OTHER, opener=Opener()) == ys.EXIT_OK and rows_of(OTHER, "not_sent")
        shown, answer = [], ["no"]

        def box(title, text, timeout):
            shown.append(text)
            return answer[0]
        try:
            send_of(OTHER, opener=Opener(), resend=True, box=box)
            raise AssertionError("No at the box sent it")
        except ys.Refused:
            pass
        assert len(shown) == 1 and f"the same data as load {RUN}" in shown[0] and not rows_of(OTHER, "sent"), shown
        answer[0] = "yes"
        again = Opener()
        assert send_of(OTHER, opener=again, resend=True, box=box) == ys.EXIT_OK and len(again.requests) == 1
        assert [r["how"] for r in rows_of(OTHER, "sent")] == ["resend"] and len(shown) == 2
        try:
            send_of(OTHER, opener=Opener())                 # once it went, the run's own rule holds as before
            raise AssertionError("a second copy went")
        except ys.Refused as exc:
            assert "went already" in str(exc)


@test("a short note: 'needs a look' on the very data whose full e-mail went is held back; a failed load's goes")
def _():
    with data_root():
        settings()
        loaded(RUN)
        assert send_of(RUN, opener=Opener()) == ys.EXIT_OK
        loaded(OTHER, kind="look")                           # the same files again, and this time a check needs a look
        opener = Opener()
        assert send_of(OTHER, opener=opener) == ys.EXIT_OK and opener.requests == []
        assert rows_of(OTHER)[-1]["reason"] == f"skipped: same data as run {RUN}"
        loaded(LATER, kind="failed")                         # the same files, and the load failed after it began to write
        note = Opener()
        assert send_of(LATER, opener=note) == ys.EXIT_OK and len(note.requests) == 1, "the database may hold part of it"
        assert rows_of(LATER, "sent")[0]["kind"] == "failed" and not rows_of(LATER, "not_sent")
    with data_root():                                        # other data: a note goes as it always did
        settings()
        loaded(RUN)
        assert send_of(RUN, opener=Opener()) == ys.EXIT_OK
        loaded(OTHER, kind="look", reports=dict(REPORTS, **{SALES: b"sales, exported later"}))
        note = Opener()
        assert send_of(OTHER, opener=note) == ys.EXIT_OK and len(note.requests) == 1
        assert rows_of(OTHER, "sent")[0]["kind"] == "look"


@test("main(): after a failure the retry is printed only where it would be accepted; a newer load has its own e-mail")
def _():
    retry = f"Its retry: python -m scripts.yq_send --run {RUN}"
    with data_root():
        settings(mode="maybe")                               # delivery.json is wrong: nothing goes until it is put right
        loaded(RUN)
        code, said = main_says("--run", RUN)
        assert code == ys.EXIT_ERROR and said.rstrip().endswith(retry) and ys.no_retry_needed(RUN) is None, said
        since = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat(timespec="seconds")
        status.write_state({"last_load": {"run": OTHER, "finished_at": since}})       # another load finished since
        code, said = main_says("--run", RUN)
        no_retry = f"No retry is needed: a newer load (run {OTHER}) has its own e-mail."
        assert code == ys.EXIT_ERROR and said.rstrip().endswith(no_retry) and "Its retry" not in said, said
        assert rows_of(RUN, "failed")[-1]["next"] == no_retry and ys.no_retry_needed(RUN) == no_retry
        before = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
        status.write_state({"last_load": {"run": "141026_0900", "finished_at": before}})   # this load's own record was lost
        said = ys.no_retry_needed(RUN)
        assert said.startswith("No retry: the last load on record is run 141026_0900, not this one") and "newer" not in said
    with data_root():                                        # a load that failed never becomes the last load on record
        settings(mode="maybe")
        status.write_state({"last_load": {"run": "141026_0900", "finished_at": "2026-10-14T10:20:00+03:00"}})
        loaded(RUN, kind="failed")
        code, said = main_says("--run", RUN)
        assert code == ys.EXIT_ERROR and said.rstrip().endswith(retry) and ys.no_retry_needed(RUN) is None, said


@test("the file itself: no database driver, no app module, no other way to send; ASCII strings; secrets by name only")
def _():
    src = (ROOT / "scripts" / "yq_send.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    for bad in ("psycopg", "supabase", "httpx", "requests", "smtplib", "app", "scripts.ops.db", "scripts.ops.sales",
                "scripts.refresh", "scripts.load_supabase"):
        assert not any(n == bad or n.startswith(bad + ".") for n in names), bad
    assert "scripts.yq_secrets" in names or "scripts" in names
    # the key is never handed to anything that prints or writes: no print, say, record or write call names it
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if fn in ("print", "say", "record", "delivery_append", "write_text", "write_state"):
                used = {n.id for a in list(node.args) + [k.value for k in node.keywords] for n in ast.walk(a)
                        if isinstance(n, ast.Name)}
                assert "key" not in used, f"{fn}() is handed the key"
    for s in (n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)):
        assert s.isascii(), s[:40]


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
            paths.set_data_root(None)
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
