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
from datetime import date
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
