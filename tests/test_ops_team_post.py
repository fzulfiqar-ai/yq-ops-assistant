"""The team message's writer (scripts/yq_team_post.py), on invented deliveries and a stand-in database connection.

    python -m tests.test_ops_team_post

Same lightweight runner as the other suites (no pytest). No database, no network: SUPABASE_URL, SUPABASE_KEY and
DATABASE_URL are pinned to ci.invalid before any import, and every connection is a stand-in that records what it
is sent. Every folder is a temporary one; every name, text and address is invented.

Covered:
  * stored: one short transaction (the statement timeout, the one upsert, the commit), the text exactly as the
    deliver command wrote it and its sha256 the manifest's; a second post of the same load sends the same upsert;
    the manifest is the one the pack row names, or the one in the delivery folder;
  * skipped (exit 0, nothing connects): no manifest (no delivery, a preview only), a short note, a manifest that
    cannot be read or is another load's; a checksum that is not the manifest's (the file changed, even only its
    line ends); the privacy filter not passed or not recorded; no text (no file named, an empty or unreadable
    file, a name outside the folder) or no data date; not a run name, a rehearsal, a code worktree;
  * failed (exit 1): no connection, a refusal of the upsert, a commit that does not go (rolled back and closed,
    the why naming only the kind of error, never the address or the login); DATABASE_URL not set, read from this
    checkout's own .env only;
  * main(): one line, the TEAM_POST line, and its exit code;
  * the file itself (AST): its SQL names only team_messages, it executes nothing else, it imports no app module, no
    HTTP client and no sender, and scripts/ops never imports it; the manifest it reads is the one
    scripts/ops/deliver.py writes.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import traceback
from contextlib import redirect_stdout
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

from scripts import yq_team_post as ytp  # noqa: E402
from scripts.ops import deliver, loadplan, paths  # noqa: E402

TESTS: list[tuple[str, object]] = []
RUN = "201026_0745"
DAY = "2026-10-20"
TEXT = ("*YQ Accessories - Tue 20 Oct*\nOctober so far: an invented line for the team.\n"
        "By 5 pm: reply here with your orders taken.\n")
DSN = "postgresql://invented:not-a-password@db.example.invalid:5432/invented"     # never reached: a stand-in connects
STORED = "TEAM_POST: stored"


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


NAMES_OF = {"html": "email.html", "text": "email.txt", "whatsapp": "whatsapp.txt", "page": "whatsapp.html"}


def delivery(run: str = RUN, *, kind: str = "full", text: str | None = TEXT, team_text=True, problems=None,
             data_date: str | None = DAY, preview: bool = False, pack: bool = True, fields: dict | None = None) -> Path:
    """What `python -m scripts.ops deliver` leaves for a load (its folder, files, manifest and pack row), written the
    way it writes them: paths.write_text, and deliver._sha of each text. `fields` replace fields of the manifest."""
    folder = paths.exports_dir(data_date or "2026-01-01") / "delivery" / (run + ("-preview" if preview else ""))
    files = {"html": "<p>invented brief</p>", "text": "invented brief\n"}
    if text is not None:
        files.update(whatsapp=text, page="<html><body>invented page</body></html>")
    for key, body in files.items():
        paths.write_text(folder / NAMES_OF[key], body)
    man = {"run": run, "kind": kind, "subject": "YQ invented subject", "made_on": data_date, "made_at": "07:58",
           "data_date": data_date, "preview": preview, "files": {k: NAMES_OF[k] for k in files},
           "sha256": {k: deliver._sha(v) for k, v in files.items()}, "team_text": team_text,
           "team_problems": problems}
    man.update(fields or {})
    mpath = paths.write_text(folder / "manifest.json", json.dumps(man, indent=1, ensure_ascii=False) + "\n")
    if pack and not preview:
        loadplan.delivery_append(run, "pack", kind=kind, manifest=str(mpath), subject=man["subject"],
                                 team_text=team_text, text=f"The {kind} delivery was prepared.")
    return folder


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.log.append(("execute", sql, params))
        if self.conn.fail_on and self.conn.fail_on in sql:
            raise self.conn.error


class FakeConn:
    """A psycopg connection's stand-in: it records each statement, the commit, the rollback and the close."""

    def __init__(self, fail_on: str | None = None, error: Exception | None = None, commit_error=None,
                 rollback_error=None, close_error=None):
        self.log: list[tuple] = []
        self.fail_on, self.error = fail_on, error
        self.commit_error, self.rollback_error, self.close_error = commit_error, rollback_error, close_error
        self.committed = self.rolled_back = 0
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.log.append(("commit",))
        if self.commit_error:
            raise self.commit_error
        self.committed += 1

    def rollback(self):
        self.log.append(("rollback",))
        self.rolled_back += 1
        if self.rollback_error:
            raise self.rollback_error

    def close(self):
        self.log.append(("close",))
        self.closed = True
        if self.close_error:
            raise self.close_error


class Connector:
    """Stands in for psycopg.connect: records the address it is handed; raises `error` or hands out `conn`."""

    def __init__(self, conn: FakeConn | None = None, error: Exception | None = None):
        self.conn, self.error, self.dsns = conn or FakeConn(), error, []

    def __call__(self, dsn):
        self.dsns.append(dsn)
        if self.error:
            raise self.error
        return self.conn


class OperationalError(Exception):
    """Stands in for psycopg.OperationalError (its message names the server and the login)."""


class UndefinedTable(Exception):
    """Stands in for psycopg.errors.UndefinedTable (the migration is not applied yet)."""


def post_of(run: str = RUN, connector: Connector | None = None, **kw) -> tuple[int, str]:
    kw.setdefault("env", {"DATABASE_URL": DSN})
    kw.setdefault("code_checkout", False)
    return ytp.post(run, connect=connector if connector is not None else Connector(), **kw)


def skipped(why_part: str, run: str = RUN, **kw) -> None:
    """The post is skipped with `why_part` in its why, exits 0 and never connects."""
    c = Connector()
    code, line = post_of(run, c, **kw)
    assert code == ytp.EXIT_OK and line.startswith("TEAM_POST: skipped (") and line.endswith(")"), line
    assert why_part in line, (why_part, line)
    assert c.dsns == [] and c.conn.log == [], "nothing connects for a skip"


# ── stored ───────────────────────────────────────────────────────────────────────────────────────

@test("stored: one short transaction - the statement timeout, the one upsert, the commit; the text is the file as "
      "the brief wrote it, its sha256 the manifest's")
def _():
    with data_root():
        folder = delivery()
        c = Connector()
        assert post_of(connector=c) == (ytp.EXIT_OK, STORED)
        assert c.dsns == [DSN]
        log = c.conn.log
        assert [e[0] for e in log] == ["execute", "execute", "commit", "close"], log
        assert log[0][1] == "set local statement_timeout = '15s'" and log[0][2] is None
        sql, params = log[1][1], log[1][2]
        assert sql == ytp.UPSERT_SQL and params == (RUN, date(2026, 10, 20), TEXT, sha(TEXT)), params
        assert (folder / "whatsapp.txt").read_bytes() == TEXT.encode("utf-8")
        man = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        assert params[3] == man["sha256"]["whatsapp"] == hashlib.sha256(params[2].encode("utf-8")).hexdigest()
        assert c.conn.committed == 1 and c.conn.rolled_back == 0 and c.conn.closed


@test("a second post of the same load sends the same upsert: one row per run, put in place of the old one")
def _():
    assert ("insert into team_messages (run, data_date, text, sha256) values (%s, %s, %s, %s) "
            "on conflict (run) do update set data_date = excluded.data_date, text = excluded.text, "
            "sha256 = excluded.sha256") == ytp.UPSERT_SQL
    with data_root():
        delivery()
        first, again = Connector(), Connector()
        assert post_of(connector=first) == (ytp.EXIT_OK, STORED) and post_of(connector=again) == (ytp.EXIT_OK, STORED)
        assert first.conn.log == again.conn.log and again.conn.committed == 1


@test("the manifest is the one the pack row names, else the one in the delivery folder; a row naming a file "
      "outside this data root's exports is passed over")
def _():
    with data_root() as root:
        folder = delivery(pack=False)                        # the pack row was never written: the folder is found
        assert ytp.find_manifest(RUN) == folder / "manifest.json"
        assert post_of()[1] == STORED
        elsewhere = root / "business_data" / "delivery" / RUN / "manifest.json"
        paths.write_text(elsewhere, "{}")                    # a stray copy, not under exports/ops: never read
        loadplan.delivery_append(RUN, "pack", kind="full", manifest=str(elsewhere), text="invented")
        assert ytp.find_manifest(RUN) == folder / "manifest.json"
        loadplan.delivery_append(RUN, "pack", kind="refused", why="invented", text="Not prepared.")   # no manifest
        assert ytp.find_manifest(RUN) == folder / "manifest.json" and post_of()[1] == STORED
    with data_root():
        folder = delivery()                                  # the pack row names it
        assert ytp.find_manifest(RUN) == Path(str(loadplan.delivery_read(RUN)[-1]["manifest"]))
        assert ytp.find_manifest("201026_0800") is None


# ── skipped ──────────────────────────────────────────────────────────────────────────────────────

@test("skipped, and nothing connects: no delivery, a preview only, a short note, a manifest that cannot be read or "
      "is another load's")
def _():
    with data_root():
        skipped("no delivery manifest for this load")
        delivery(preview=True)                               # a preview's folder is <run>-preview: never read
        skipped("no delivery manifest for this load")
    for kind in ("look", "failed"):
        with data_root():
            delivery(kind=kind, text=None, team_text=False)   # a note has no team message
            skipped(f"no full brief (kind {kind})")
    with data_root():
        folder = delivery()
        (folder / "manifest.json").write_text("{not json", encoding="utf-8")
        skipped("the delivery manifest cannot be read (JSONDecodeError)")
    with data_root():
        delivery(fields={"run": "201026_0800"})              # the folder of this run holds another load's manifest
        skipped("is not this load's own")
    with data_root():
        delivery(preview=True, pack=False)
        folder = paths.exports_dir(DAY) / "delivery" / RUN
        preview = paths.exports_dir(DAY) / "delivery" / (RUN + "-preview")
        paths.write_text(folder / "manifest.json", (preview / "manifest.json").read_text(encoding="utf-8"))
        skipped("is not this load's own")                    # a preview's manifest copied into the load's folder


@test("skipped: the team message file is not the one the brief wrote (changed, its line ends changed, gone)")
def _():
    for change in (lambda f: f.write_bytes(TEXT.encode("utf-8") + b"and one more line\n"),
                   lambda f: f.write_bytes(TEXT.replace("\n", "\r\n").encode("utf-8")),
                   lambda f: f.write_bytes(b"")):
        with data_root():
            change(delivery() / "whatsapp.txt")
            skipped("its checksum is not the manifest's")
    with data_root():
        (delivery() / "whatsapp.txt").unlink()
        skipped("no text: the team message file cannot be read (FileNotFoundError)")
    with data_root():
        delivery(fields={"sha256": {"html": "x", "text": "y"}})   # the manifest keeps no checksum of the message
        skipped("its checksum is not the manifest's")


@test("skipped: the privacy filter did not pass, or the manifest does not record it")
def _():
    with data_root():                                        # the deliver command's own way: no file, the problems
        delivery(text=None, team_text=False, problems=["a customer or shop is named (Ab...)"])
        skipped("the privacy filter did not pass: no team message was made")
    with data_root():                                        # a file and problems both: the problems win
        delivery(problems=["the word 'overdue' is not for the group"])
        skipped("the privacy filter did not pass")
    for value in (False, None, "yes", 1):
        with data_root():
            delivery(team_text=value)
            skipped("the privacy filter did not pass: no team message was made")
    with data_root():
        folder = delivery()
        man = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        del man["team_text"]
        paths.write_text(folder / "manifest.json", json.dumps(man))
        skipped("the privacy filter did not pass: the manifest does not record it")


@test("skipped: no text (no file named, an empty or non-UTF-8 file, a name outside the folder) or no data date")
def _():
    with data_root():
        delivery(text=None)                                  # privacy passed, and yet no file is named
        skipped("no text: the delivery names no team message file")
    with data_root():
        delivery(text=" \n\n")
        skipped("no text: the team message is empty")
    with data_root():
        folder = delivery()
        raw = b"invented \xe9 text\n"                      # Latin-1, not UTF-8, with a checksum that matches it
        (folder / "whatsapp.txt").write_bytes(raw)
        man = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        man["sha256"]["whatsapp"] = hashlib.sha256(raw).hexdigest()
        paths.write_text(folder / "manifest.json", json.dumps(man))
        skipped("no text: the team message file is not UTF-8 text")
    for name in ("../whatsapp.txt", "sub/whatsapp.txt", "sub\\whatsapp.txt", "..", "."):
        with data_root():
            delivery(fields={"files": {"html": "email.html", "text": "email.txt", "whatsapp": name}})
            skipped("outside its folder")
    for when in (None, "", "20-10-2026", "2026-02-30"):
        with data_root():
            delivery(data_date=when)
            skipped("the delivery manifest names no data date")


@test("skipped, and nothing is read: not a run name, a rehearsal, a code worktree")
def _():
    with data_root():
        delivery()
        for bad in ("../x", "201026", "201026_0745\n", "", "2O1026_0745"):
            skipped("not a run name (ddmmyy_HHMM)", run=bad)
        delivery(run="000000_0900")
        skipped("a rehearsal: nothing is stored", run="000000_0900")
        skipped("this is a code worktree: the team message is stored from the project folder only", code_checkout=True)
        real = ytp.in_code_checkout
        try:
            ytp.in_code_checkout = lambda: True              # asked when the caller does not say
            skipped("this is a code worktree", code_checkout=None)
            ytp.in_code_checkout = lambda: False
            assert post_of(code_checkout=None)[1] == STORED
        finally:
            ytp.in_code_checkout = real
        assert isinstance(ytp.in_code_checkout(), bool)


# ── failed ───────────────────────────────────────────────────────────────────────────────────────

@test("failed: no connection, the upsert refused, a commit that does not go - exit 1, rolled back and closed, the "
      "why naming only the kind of error")
def _():
    secret = OperationalError('connection to server at "db.example.invalid" failed: password authentication failed '
                              'for user "invented" (not-a-password)')
    with data_root():
        delivery()
        c = Connector(error=secret)
        code, line = post_of(connector=c)
        assert (code, line) == (ytp.EXIT_FAILED, "TEAM_POST: failed (no connection to the database (OperationalError))")
        assert c.dsns == [DSN] and "invented" not in line and "not-a-password" not in line and "example" not in line
        conn = FakeConn(fail_on="insert into team_messages", error=UndefinedTable('relation "team_messages" does not '
                                                                                  'exist'))
        code, line = post_of(connector=Connector(conn))
        assert (code, line) == (ytp.EXIT_FAILED, "TEAM_POST: failed (the database did not store it (UndefinedTable))")
        assert [e[0] for e in conn.log] == ["execute", "execute", "rollback", "close"] and conn.committed == 0
        conn = FakeConn(commit_error=OperationalError("server closed the connection unexpectedly"),
                        rollback_error=OperationalError("the connection is closed"))
        code, line = post_of(connector=Connector(conn))
        assert (code, line) == (ytp.EXIT_FAILED, "TEAM_POST: failed (the database did not store it (OperationalError))")
        assert [e[0] for e in conn.log] == ["execute", "execute", "commit", "rollback", "close"] and conn.closed
        conn = FakeConn(fail_on="statement_timeout", error=UndefinedTable("invented"))
        assert post_of(connector=Connector(conn))[0] == ytp.EXIT_FAILED and len(conn.log) == 3   # no upsert after it
        conn = FakeConn(close_error=OperationalError("invented"))   # a close that fails after the commit: it is stored
        assert post_of(connector=Connector(conn)) == (ytp.EXIT_OK, STORED) and conn.committed == 1


@test("failed: DATABASE_URL is not set; it is read from the environment, else from this checkout's own .env only")
def _():
    with data_root() as root, tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        delivery()
        missing = Path(tmp) / "missing.env"
        c = Connector()
        assert post_of(connector=c, env={}, env_file=missing) == (
            ytp.EXIT_FAILED, "TEAM_POST: failed (DATABASE_URL is not set: the project folder's .env names it)")
        assert c.dsns == []
        env_file = Path(tmp) / ".env"
        env_file.write_text("DATABASE_URL=postgresql://from-file@file.example.invalid/x\n", encoding="utf-8")
        c = Connector()
        assert post_of(connector=c, env={"DATABASE_URL": "  "}, env_file=env_file)[1] == STORED
        assert c.dsns == ["postgresql://from-file@file.example.invalid/x"]
        c = Connector()
        assert post_of(connector=c, env={"DATABASE_URL": DSN}, env_file=env_file)[1] == STORED and c.dsns == [DSN]
        assert ytp.database_url({}, missing) == "" and root.is_dir()
    src = (ROOT / "scripts" / "yq_team_post.py").read_text(encoding="utf-8")
    assert "load_dotenv" not in src and "find_dotenv" not in src, "python-dotenv's walk up the folders is never used"


@test("main(): it prints one line, the TEAM_POST line, and returns its exit code")
def _():
    def run_main(*argv, **deps) -> tuple[int, str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = ytp.main(list(argv), **deps)
        return code, buf.getvalue()
    with data_root():
        delivery()
        assert run_main("--run", RUN, connect=Connector(), env={"DATABASE_URL": DSN}, code_checkout=False) == (
            ytp.EXIT_OK, STORED + "\n")
        code, out = run_main("--run", RUN, connect=Connector(error=OperationalError("x")), env={"DATABASE_URL": DSN},
                             code_checkout=False)
        assert code == ytp.EXIT_FAILED and out.splitlines() == ["TEAM_POST: failed (no connection to the database "
                                                                "(OperationalError))"], out
        assert run_main("--run", "201026_0800", connect=Connector(), code_checkout=False) == (
            ytp.EXIT_OK, "TEAM_POST: skipped (no delivery manifest for this load)\n")
        def broken(run):
            raise RuntimeError("invented")
        real = ytp.team_message
        try:
            ytp.team_message = broken
            assert run_main("--run", RUN, connect=Connector(), code_checkout=False) == (
                ytp.EXIT_FAILED, "TEAM_POST: failed (an unexpected error: RuntimeError)\n")
        finally:
            ytp.team_message = real


# ── the file itself ──────────────────────────────────────────────────────────────────────────────

@test("its SQL names only team_messages and it executes nothing else; no app module, no HTTP client, no sender; "
      "scripts/ops never imports it; ASCII strings")
def _():
    sql = " ".join((ytp.TIMEOUT_SQL, ytp.UPSERT_SQL)).lower()
    scan = sql.replace("do update set", "do-update-set")    # the conflict clause names no table
    tables = set(re.findall(r"\b(?:into|update|from|join|table|using)\s+([a-z_][a-z0-9_.]*)", scan))
    assert tables == {"team_messages"}, tables
    assert not re.search(r"\b(delete|drop|truncate|alter|grant|revoke|create|copy|select|call|do\s+nothing)\b",
                         sql.replace("do update", "")), sql
    assert ";" not in sql and sql.count("insert into") == 1
    assert ytp.STATEMENT_TIMEOUT_S == 15 and ytp.TIMEOUT_SQL == "set local statement_timeout = '15s'"
    src = (ROOT / "scripts" / "yq_team_post.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    executed = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in ("execute", "executemany", "copy")]
    assert executed and all(isinstance(n.args[0], ast.Name) and n.args[0].id in ("TIMEOUT_SQL", "UPSERT_SQL")
                            for n in executed), "only the two statements are ever sent"
    assert [n.func.attr for n in executed] == ["execute", "execute"]
    names, top = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names |= {f"{node.module}.{a.name}" for a in node.names}
    for node in tree.body:
        if isinstance(node, ast.Import):
            top |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            top.add(node.module)
    for bad in ("app", "httpx", "requests", "supabase", "smtplib", "urllib", "socket", "subprocess",
                "scripts.ops.db", "scripts.ops.sales", "scripts.ops.render", "scripts.refresh", "scripts.load_supabase",
                "scripts.load_txn", "scripts.yq_send", "scripts.yq_secrets", "scripts.yq_load_go", "scripts.yq_backup"):
        assert not any(n == bad or n.startswith(bad + ".") for n in names), bad
    assert "psycopg" in names and "psycopg" not in top, "the driver is imported only where it connects"
    for s in (n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)):
        assert s.isascii(), s[:40]
    for f in sorted((ROOT / "scripts" / "ops").rglob("*.py")):          # the read-only package never imports it
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                said = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
                assert not any("yq_team_post" in n for n in said), f.name


@test("the manifest it reads is the one scripts/ops/deliver.py writes: its fields, the file's name, the folder")
def _():
    tree = ast.parse((ROOT / "scripts" / "ops" / "deliver.py").read_text(encoding="utf-8"))
    assigned = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned[target.id] = {k.value: v for k, v in zip(node.value.keys, node.value.values)
                                           if isinstance(k, ast.Constant)}
    fields = set(assigned["manifest"])
    assert {"run", "kind", "data_date", "preview", "files", "sha256", "team_text", "team_problems"} <= fields, fields
    names_of = assigned["names_of"]
    assert isinstance(names_of["whatsapp"], ast.Constant) and names_of["whatsapp"].value == "whatsapp.txt"
    folders = [n for n in ast.walk(tree) if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div)
               and isinstance(n.right, ast.Constant) and n.right.value == "delivery"
               and isinstance(n.left, ast.Call) and getattr(n.left.func, "attr", "") == "exports_dir"]
    assert folders, "exports/ops/<date>/delivery/<run>/"
    with data_root():                                        # the checksum the manifest keeps is of the bytes on disk
        f = paths.write_text(paths.exports_dir(DAY) / "x.txt", TEXT)
        assert hashlib.sha256(f.read_bytes()).hexdigest() == deliver._sha(TEXT)


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
