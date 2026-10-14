"""Ops engine: nothing in scripts/ops can write, send or shell out (the plan's first principle).

    python -m tests.test_ops_readonly

Same lightweight runner as the other suites (no pytest). No database, no network, no .env:
SUPABASE_URL, SUPABASE_KEY and DATABASE_URL are pinned to ci.invalid BEFORE any import (a worktree
under the main checkout would otherwise find the real .env through python-dotenv's walk-up), the
Supabase client talks to an in-memory transport and psycopg.connect is replaced by a stand-in.
All names and numbers are invented.

Covered:
  * the side channels (Telegram, e-mail, LLM keys) are blank after importing scripts.ops.db;
  * the guarded client: insert / update / delete / upsert, an RPC other than the two read ones and
    a storage signed link each raise, and none of them reaches the transport; reads pass;
  * a reset_client() cannot bring back an unguarded client; app.db_read reads through the guard;
  * the psycopg session is read-only before its first statement, asserted, closed when it is not;
  * check_sql refuses anything that is not one SELECT / WITH / SHOW;
  * the package itself (AST): who may import a client, a driver or subprocess; forbidden modules
    and calls; scripts/ops/gitinfo.py runs only `git -C <root> <allowed verb>` with no shell.
"""
from __future__ import annotations

import ast
import io
import os
import sys
import traceback
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"
os.environ["TELEGRAM_BOT_TOKEN"] = "would-send"          # must be blanked by scripts.ops.db
os.environ["RESEND_API_KEY"] = "would-send"

import httpx  # noqa: E402

from scripts.ops import db  # noqa: E402  (before any app import, as every command does)

OPS = ROOT / "scripts" / "ops"
TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


class Recorder:
    """An in-memory transport: records what reached it and answers like PostgREST."""

    def __init__(self, rows=None):
        self.seen: list[str] = []
        self.rows = rows if rows is not None else [{"x": 1}]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json=self.rows)

    @property
    def writes(self) -> list[str]:
        return [s for s in self.seen
                if not s.startswith(("GET ", "HEAD ")) and "run_readonly_query" not in s]


def fresh_client(rows=None):
    rec = Recorder(rows)
    db.uninstall()
    client = db.install(inner=httpx.MockTransport(rec), url="https://ci.invalid", key="ci-key")
    return client, rec


def refused(fn) -> bool:
    """True when the call raised (the SDK may wrap the violation: what matters is that it did not
    go through; the recorder is checked separately)."""
    try:
        fn()
    except Exception:  # noqa: BLE001  (db.ReadOnlyViolation, or the SDK's wrapper around it)
        return True
    return False


@test("side channels are blank after importing scripts.ops.db")
def _():
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "RESEND_API_KEY", "BREVO_API_KEY", "SMTP_PASS",
                "GROQ_API_KEY", "OPENROUTER_API_KEY", "AGENT_API_KEY", "RENDER_API_KEY"):
        assert os.environ.get(key) == "", key
    from app.config import settings
    assert settings.telegram_bot_token == "" and settings.groq_api_key == ""
    assert settings.supabase_url == "https://ci.invalid"          # the read path keeps its address


@test("table writes are refused and never reach the transport")
def _():
    client, rec = fresh_client()
    assert refused(lambda: client.table("t").insert({"a": 1}).execute())
    assert refused(lambda: client.table("t").update({"a": 2}).eq("a", 1).execute())
    assert refused(lambda: client.table("t").delete().eq("a", 1).execute())
    assert refused(lambda: client.table("t").upsert({"a": 1}).execute())
    assert rec.writes == [], rec.seen
    assert rec.seen == [], rec.seen


@test("an RPC other than the two read ones is refused; the read RPCs and SELECTs pass")
def _():
    client, rec = fresh_client()
    assert refused(lambda: client.rpc("ingest_commit", {"batch_id": 1}).execute())
    assert rec.seen == []
    client.rpc("run_readonly_query", {"sql_text": "select 1 as x"}).execute()
    client.rpc("run_readonly_query_params", {"sql_text": "select $1 as x", "params": ["1"]}).execute()
    client.table("t").select("a").limit(1).execute()
    assert len(rec.seen) == 3 and rec.writes == [], rec.seen


@test("a storage signed link (POST) and an upload are refused")
def _():
    client, rec = fresh_client()
    assert refused(lambda: client.storage.from_("orders").create_signed_url("a/b.pdf", 3600))
    assert refused(lambda: client.storage.from_("orders").upload("a/b.txt", b"x"))
    assert rec.writes == [], rec.seen


@test("a reset cannot bring back an unguarded client; app.database hands out the guarded one")
def _():
    client, rec = fresh_client()
    from app import database as appdb
    assert appdb.get_client() is client
    appdb.reset_client()
    again = appdb.get_client()
    assert again is client
    assert refused(lambda: again.table("t").insert({"a": 1}).execute())
    assert rec.writes == []


@test("db.rpc and db.rpc_params read through the guard and log the statement")
def _():
    _, rec = fresh_client(rows=[{"focus_to": "2026-01-31"}])
    db.SQL_LOG.clear()
    assert db.rpc("SELECT 1 AS focus_to") == [{"focus_to": "2026-01-31"}]
    assert db.rpc_params("SELECT $1::date AS focus_to", ["2026-01-31"]) == [{"focus_to": "2026-01-31"}]
    assert [s for s in rec.seen if "run_readonly_query" in s] == rec.seen and len(rec.seen) == 2
    assert [c for c, _t in db.SQL_LOG].count("rpc") == 2


@test("check_sql refuses anything that is not one read statement")
def _():
    for good in ("select 1", "  WITH a AS (select 1) select * from a", "show transaction_read_only",
                 "select updated_at, created_by from t"):
        db.check_sql(good)
    for bad in ("delete from t", "select 1; drop table t", "update t set a = 1", "insert into t values (1)",
                "with d as (delete from t returning 1) select * from d", "create temp table x (a int)",
                "select 1 -- then\n; truncate t", "copy t to stdout", "set role postgres"):
        try:
            db.check_sql(bad)
        except db.ReadOnlyViolation:
            continue
        raise AssertionError(f"accepted: {bad}")


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._last = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.log.append(("execute", " ".join(str(sql).split()), self.conn.read_only))
        self._last = str(sql)

    def fetchone(self):
        return (self.conn.read_only_reported,)

    def fetchall(self):
        return [{"n": 1}]


class FakeConn:
    def __init__(self, reported="on"):
        self.log: list = []
        self._ro = None
        self.read_only_reported = reported
        self.closed = False
        self.rolled_back = 0

    @property
    def read_only(self):
        return self._ro

    @read_only.setter
    def read_only(self, value):
        assert not [e for e in self.log if e[0] == "execute"], "read_only set after a statement"
        self._ro = value
        self.log.append(("read_only", value))

    def cursor(self, row_factory=None):
        return FakeCursor(self)

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed = True


@test("the session is read-only before its first statement, and asserted")
def _():
    import psycopg
    real = psycopg.connect
    conn = FakeConn("on")
    seen_kwargs: dict = {}

    def fake_connect(dsn, **kw):
        seen_kwargs.update(kw)
        return conn
    psycopg.connect = fake_connect
    try:
        s = db.session(timeout_s=30)
        assert seen_kwargs.get("autocommit") is False
        assert conn.log[0] == ("read_only", True)
        assert all(ro is True for kind, _sql, ro in [e for e in conn.log if e[0] == "execute"])
        assert any("show transaction_read_only" in e[1] for e in conn.log if e[0] == "execute")
        assert conn.rolled_back == 0, "no rollback before the session is handed out"
        try:
            s.q("delete from t")
        except db.ReadOnlyViolation:
            pass
        else:
            raise AssertionError("a write statement reached the session")
        assert not any("delete" in e[1] for e in conn.log if e[0] == "execute")
        assert s.q("select 1 as n") == [{"n": 1}]
        s.close()
        assert conn.closed and conn.rolled_back == 1
    finally:
        psycopg.connect = real


@test("a session that does not report read-only is closed and refused")
def _():
    import psycopg
    real = psycopg.connect
    conn = FakeConn("off")
    psycopg.connect = lambda dsn, **kw: conn
    try:
        try:
            db.session()
        except db.ReadOnlyViolation:
            pass
        else:
            raise AssertionError("a read-write session was handed out")
        assert conn.closed
    finally:
        psycopg.connect = real


@test("with_backend builds the preview backend first (it sets read-only), then asserts")
def _():
    import psycopg
    real = psycopg.connect
    conn = FakeConn("on")
    psycopg.connect = lambda dsn, **kw: conn
    try:
        s = db.session(with_backend=True)
        assert s.backend is not None and s.backend.read_only is True
        kinds = [e[0] for e in conn.log]
        assert kinds[0] == "read_only"
        assert conn.rolled_back == 0
        s.close()
    finally:
        psycopg.connect = real


# ── the package itself ────────────────────────────────────────────────────────

MAY_IMPORT = {                      # module (prefix) -> files of scripts/ops allowed to import it
    "subprocess": {"gitinfo.py"},
    "psycopg": {"db.py"},
    "supabase": {"db.py"},
    "httpx": {"db.py", "selftest.py"},
    "urllib": {"db.py"},
    "app.database": {"db.py"},
    "app.db_read": {"db.py"},
}
FORBIDDEN_IMPORTS = (
    "app.agents", "app.ai", "app.events", "app.notify", "app.emailer", "app.escalation", "app.whatsapp",
    "app.outreach", "app.social_publish", "app.llm_router", "app.shop_notify", "scripts.refresh",
    "scripts.apply_sql", "scripts.apply_migration", "scripts.render_deploy", "scripts.deploy_render",
    "scripts.deploy_cf", "scripts.deploy_web", "scripts.send_digest", "scripts.yq_load_go",
    "scripts.yq_send", "scripts.yq_secrets", "scripts.yq_backup",
    "requests", "smtplib", "socket", "ftplib", "telnetlib",
)
# scripts.load_supabase holds the loader AND its pure guards: only these names may be imported.
LOAD_SUPABASE_PURE = {"marker_present", "date_span", "warehouse_set_guard", "span_guard", "salesman_guard",
                      "stale_pricebook_rows", "snapshot_guard", "pricebook_key", "focus_book_kind",
                      "PARTIAL_OK_MARKER", "REPLACE_OK_MARKER"}
FORBIDDEN_CALLS = {("os", "system"), ("os", "popen"), ("os", "kill"), ("os", "startfile"),
                   ("os", "execv"), ("os", "spawnv")}
CLIENT_CALLS = {"table", "rpc", "storage", "upsert"}     # PostgREST / storage builders
CLIENT_CALL_FILES = {"db.py", "selftest.py"}            # selftest uses them to TEST the guard


def package_files() -> list[Path]:
    return sorted(p for p in OPS.rglob("*.py"))


def imports_of(tree: ast.AST) -> list[tuple[str, list[str]]]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(a.name, []) for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append((node.module, [a.name for a in node.names]))
    return out


@test("only db.py may import a client or a driver; only gitinfo.py may import subprocess")
def _():
    for f in package_files():
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        for module, names in imports_of(tree):
            for prefix, allowed in MAY_IMPORT.items():
                hit = module == prefix or module.startswith(prefix + ".") or (module == "app" and
                                                                               prefix.startswith("app.") and prefix[4:] in names)
                assert not hit or f.name in allowed, f"{f.name} imports {module} ({names})"


@test("no module of the package imports a writer, a sender or a deploy tool")
def _():
    for f in package_files():
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        for module, names in imports_of(tree):
            for bad in FORBIDDEN_IMPORTS:
                assert not (module == bad or module.startswith(bad + ".")), f"{f.name} imports {module}"
                if "." in bad:
                    parent, leaf = bad.rsplit(".", 1)
                    assert not (module == parent and leaf in names), f"{f.name} imports {bad}"
            if module == "scripts.load_supabase":
                extra = set(names) - LOAD_SUPABASE_PURE
                assert not extra, f"{f.name} imports non-pure names from scripts.load_supabase: {sorted(extra)}"
            assert not (module == "scripts" and "load_supabase" in names), f"{f.name} imports the whole loader"


@test("no os.system / os.popen / os.kill, no eval or exec, no client builders outside db.py")
def _():
    for f in package_files():
        tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if isinstance(fn, ast.Name):
                assert fn.id not in ("eval", "exec", "__import__"), f"{f.name} calls {fn.id}()"
                assert fn.id != "get_client", f"{f.name} calls get_client()"
            if isinstance(fn, ast.Attribute):
                if isinstance(fn.value, ast.Name):
                    assert (fn.value.id, fn.attr) not in FORBIDDEN_CALLS, f"{f.name} calls {fn.value.id}.{fn.attr}()"
                own_read = fn.attr == "rpc" and isinstance(fn.value, ast.Name) and fn.value.id == "db"
                if fn.attr in CLIENT_CALLS and f.name not in CLIENT_CALL_FILES and not own_read:
                    raise AssertionError(f"{f.name} calls .{fn.attr}(): reads go through scripts.ops.db")
                assert fn.attr != "get_client", f"{f.name} calls get_client()"


@test("gitinfo.py runs only `git -C <root> <allowed verb>` with an argv list and no shell")
def _():
    from scripts.ops import gitinfo
    tree = ast.parse((OPS / "gitinfo.py").read_text(encoding="utf-8"))
    runs = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Name) and n.func.value.id == "subprocess"]
    assert len(runs) == 1 and runs[0].func.attr == "run", "exactly one subprocess.run, in _git"
    call = runs[0]
    argv = call.args[0]
    assert isinstance(argv, ast.List) and [getattr(e, "value", None) for e in argv.elts[:2]] == ["git", "-C"]
    shell = [k for k in call.keywords if k.arg == "shell"]
    assert shell and isinstance(shell[0].value, ast.Constant) and shell[0].value.value is False
    verbs = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_git":
            verb = node.args[1]
            assert isinstance(verb, ast.Constant) and isinstance(verb.value, str), "a computed git verb"
            verbs.append(verb.value)
    assert verbs and set(verbs) <= set(gitinfo.VERBS), verbs
    assert set(gitinfo.VERBS) <= {"rev-parse", "merge-base", "diff", "status", "cat-file", "ls-files"}
    try:
        gitinfo._git(ROOT, "push")
    except ValueError:
        pass
    else:
        raise AssertionError("an unlisted verb was run")


@test("the writer lives outside the package and the package never names its module in an import")
def _():
    assert not (OPS / "yq_load_go.py").exists()
    for f in package_files():
        for module, names in imports_of(ast.parse(f.read_text(encoding="utf-8"))):
            assert "yq_load_go" not in module and "yq_load_go" not in names, f.name


@test("the sender and the secret stores stay outside the package: no module of it reads a secret or sends")
def _():
    for name in ("yq_send.py", "yq_secrets.py", "yq_backup.py"):
        assert not (OPS / name).exists(), name
    for f in package_files():
        text = f.read_text(encoding="utf-8")
        for word in ("CredRead", "winreg", "api.resend.com", "YQ_RESEND_API_KEY", "YQ_BACKUP_PASSWORD"):
            assert word not in text, f"{f.name} names {word}: secrets and sending live in scripts/yq_*.py"
        for module, names in imports_of(ast.parse(text)):
            assert not any(m in module or m in names for m in ("yq_send", "yq_secrets", "yq_backup")), f.name


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
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
