"""The ONE module in scripts/ops that opens a connection. Import it before any app.* module.

scripts/ops reads production and must never change it. The credentials a local process holds can
write (the read RPC is callable only with the service-role key; DATABASE_URL is the owner login),
so "read-only" cannot rest on the credentials. It rests on this module:

  1. Side channels are blanked at import: the Telegram, e-mail and LLM keys are set to '' in
     os.environ (python-dotenv never overrides a variable that is already set), so nothing a reused
     app function calls can send a message or reach a model.
  2. The Supabase client is built HERE, with a transport that raises ReadOnlyViolation for every
     request app.database._is_read() does not accept (anything but GET / HEAD and the two SELECT-only
     RPCs), and it is installed as the client app.database hands to every reused function. A later
     reset cannot bring back an unguarded client: create_client in that module is replaced too.
  3. The psycopg session (needed for run_preview and for tables the RPC role cannot read) is opened
     with autocommit off and read_only on, and `show transaction_read_only` is asserted before it
     is handed out. Statements other than SELECT / WITH / SHOW are refused before they are sent.

tests/test_ops_readonly.py proves each point without a network, and walks the package so no other
module can import a client, a writer or subprocess.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[2]
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

# Everything a reused app function could use to SEND something or to call a model. Blanked, never read.
SIDE_CHANNEL_KEYS = (
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "RESEND_API_KEY", "BREVO_API_KEY", "SMTP_USER", "SMTP_PASS",
    "ALERT_EMAIL_TO", "EMAIL_FROM", "OPENROUTER_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY",
    "SCALEWAY_API_KEY", "TOGETHER_API_KEY", "GEMINI_API_KEY", "MISTRAL_API_KEY", "COHERE_API_KEY",
    "GITHUB_MODELS_TOKEN", "CLOUDFLARE_API_TOKEN", "SAMBANOVA_API_KEY", "NVIDIA_API_KEY",
    "MOONSHOT_API_KEY", "TAVILY_API_KEY", "YOUTUBE_API_KEY", "AGNES_API_KEY", "WA_ACCESS_TOKEN",
    "WA_PHONE_NUMBER_ID", "FB_PAGE_TOKEN", "AGENT_API_KEY", "RENDER_API_KEY",
)
_SETTINGS_ATTRS = (
    "telegram_bot_token", "telegram_chat_id", "openrouter_api_key", "groq_api_key", "cerebras_api_key",
    "scaleway_api_key", "together_api_key", "gemini_api_key", "mistral_api_key", "cohere_api_key",
    "github_models_token", "cloudflare_api_token", "sambanova_api_key", "nvidia_api_key",
    "moonshot_api_key", "tavily_api_key", "youtube_api_key", "agnes_api_key", "wa_access_token",
    "wa_phone_number_id", "fb_page_token", "agent_api_key",
)

READ_ONLY_SQL = re.compile(r"^\s*(select|with|show)\b", re.I)        # as scripts/ai_head/pack.py
WRITE_WORDS = re.compile(
    r"\b(insert|update|delete|merge|alter|drop|create|grant|revoke|truncate|copy|call|vacuum|lock)\b", re.I)

SQL_LOG: list[tuple[str, str]] = []          # (channel, first 200 characters): the read-only proof


class ReadOnlyViolation(RuntimeError):
    """Something tried to write, send or sign through scripts/ops."""


def blank_side_channels() -> None:
    for k in SIDE_CHANNEL_KEYS:
        os.environ[k] = ""
    cfg = sys.modules.get("app.config")
    if cfg is not None:                      # app.config was imported first: blank what it already read
        for attr in _SETTINGS_ATTRS:
            if hasattr(cfg.settings, attr):
                setattr(cfg.settings, attr, "")


blank_side_channels()

_install_lock = threading.Lock()
_client = None


def _log(channel: str, text: str) -> None:
    SQL_LOG.append((channel, " ".join(str(text).split())[:200]))


def _guarded_http_client(inner=None):
    import httpx
    from app import database as appdb

    class ReadOnlyTransport(httpx.BaseTransport):
        def __init__(self, nxt):
            self._next = nxt

        def handle_request(self, request):
            if not appdb._is_read(request):
                raise ReadOnlyViolation(
                    f"scripts/ops is read-only: refused {request.method} {request.url.path}")
            _log("http", f"{request.method} {request.url.path}")
            return self._next.handle_request(request)

        def close(self):
            self._next.close()

    base = inner if inner is not None else httpx.HTTPTransport(http2=False, limits=appdb._POOL_LIMITS, retries=1)
    return httpx.Client(transport=ReadOnlyTransport(appdb.RetryReadsTransport(base)),
                        timeout=appdb._TIMEOUT, follow_redirects=True)


def install(inner=None, url: str | None = None, key: str | None = None):
    """Build the guarded Supabase client and make it the client of app.database. `inner` is a
    stand-in transport for tests; url / key default to the environment."""
    global _client
    with _install_lock:
        blank_side_channels()
        from app import database as appdb
        from app.config import settings
        from supabase import create_client
        from supabase.lib.client_options import SyncClientOptions

        u = url or settings.supabase_url
        k = key or settings.supabase_key
        if not u or not k:
            raise RuntimeError("SUPABASE_URL / SUPABASE_KEY are not set: scripts/ops reads them from the "
                               "project folder's .env")
        client = create_client(u, k, options=SyncClientOptions(httpx_client=_guarded_http_client(inner)))
        appdb._client = client
        # app.database.get_client() rebuilds through this name after a reset_client(): keep the guard.
        appdb.create_client = lambda *a, **kw: client
        _client = client
        return client


def client():
    """The guarded client (installed on first use)."""
    return _client if _client is not None else install()


def installed() -> bool:
    return _client is not None


def uninstall() -> None:
    """Tests only: forget the guarded client."""
    global _client
    _client = None
    appdb = sys.modules.get("app.database")
    if appdb is not None:
        appdb._client = None


def rpc(sql: str) -> list[dict]:
    """A parameter-free SELECT through the read-only RPC (executes as the SELECT-only role, 8 s).
    The SQL is wrapped as a subquery server-side: no trailing semicolon, never alias a column `t`."""
    client()
    from app.db_read import exec_sql
    _log("rpc", sql)
    return exec_sql(sql)


def rpc_params(sql: str, params: list) -> list[dict]:
    """A SELECT with $1..$8 text parameters through the read-only RPC (cast in SQL; a list goes as
    one JSON parameter)."""
    client()
    from app.db_read import exec_sql_params
    _log("rpc", sql)
    return exec_sql_params(sql, params)


def check_sql(sql: str) -> None:
    """Refuse anything that is not one SELECT / WITH / SHOW statement."""
    if not READ_ONLY_SQL.match(sql) or ";" in sql.strip().rstrip(";") or WRITE_WORDS.search(sql):
        raise ReadOnlyViolation("scripts/ops: refusing a statement that is not a read: " + sql.strip()[:60])


class Session:
    """A read-only psycopg session on DATABASE_URL (the tables the RPC role cannot read, and the
    load preview). q() takes psycopg %s placeholders. Close it when done; nothing is ever kept."""

    def __init__(self, conn, backend=None):
        self.conn, self.backend = conn, backend

    def q(self, sql: str, params=None) -> list[dict]:
        from psycopg.rows import dict_row
        check_sql(sql)
        _log("pg", sql)
        with self.conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def close(self) -> None:
        try:
            self.conn.rollback()
        finally:
            self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def session(with_backend: bool = False, timeout_s: int = 120) -> Session:
    """Open the read-only session. with_backend=True also builds app.ingest_batch.PgBackend on it
    (for run_preview): the backend is built FIRST because its constructor sets the session
    read-only, and psycopg refuses to change that once a statement has opened the transaction.
    No rollback happens before the session is handed out (it would undo the backend's settings)."""
    import app.config  # noqa: F401  (loads the project .env; side channels stay blank)
    import psycopg

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set: scripts/ops reads it from the project folder's .env")
    # TCP keepalives: a connection the pooler has dropped is noticed within about a minute instead
    # of hanging the command (seen once: a read-only probe sat for over ten minutes on a dead socket)
    conn = psycopg.connect(dsn, autocommit=False, connect_timeout=30, keepalives=1, keepalives_idle=30,
                           keepalives_interval=10, keepalives_count=3)
    try:
        backend = None
        if with_backend:
            from app.ingest_batch import PgBackend
            backend = PgBackend(conn, read_only=True)
        else:
            conn.read_only = True
        with conn.cursor() as cur:
            cur.execute("set statement_timeout = '%ds'" % int(timeout_s))
            cur.execute("show transaction_read_only")
            state = cur.fetchone()[0]
        if str(state).lower() != "on":
            raise ReadOnlyViolation("the database session is not read-only (transaction_read_only = %s)" % state)
    except BaseException:
        conn.close()
        raise
    return Session(conn, backend)


def health(url: str, timeout_s: int = 60, retries: int = 1) -> dict:
    """GET the API's /health (the live commit). A plain read; no credential is sent."""
    last: Exception | None = None
    for _ in range(retries + 1):
        try:
            req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:   # noqa: S310 (fixed https address from config)
                return json.loads(resp.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            last = exc
    raise RuntimeError(f"the live site did not answer at {url}: {last}")
