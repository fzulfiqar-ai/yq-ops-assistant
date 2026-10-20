"""The Focus tables in ONE database transaction (Release 3 of the load speed-up, 20-Oct-2026).

scripts/load_supabase.py writes through this client when it is asked to: `--one-transaction`, or
YQ_LOAD_ONE_TRANSACTION=1 in its environment, which the load writer (scripts/yq_load_go.py) sets for
the refresh child it starts. TxnClient is the subset of the PostgREST builder that the loader calls
(table(..).select / eq / gte / lte / lt / gt / in_ / is_ / not_ / order / limit / range, .delete,
.insert, .update, .upsert, .execute) over ONE psycopg session on DATABASE_URL, so the loader's own
rules -- the span plans and their guards, upsert-then-prune, the ledger's range replace, the
snapshot replaces, the price-book void and revive, the audit rows and the ingest_runs row -- are the
very code that runs on either path. What changes:

  * Nothing is visible to any other session until commit(): production reads the old data or the
    new, never a half-replaced ledger. Any error before the commit rolls everything back.
  * An upsert is three statements per table (bulk_upsert), whatever its size: the rows are COPYed, as
    the JSON text a PostgREST request carries, into a temp table that drops at commit, and one
    server-side loop then applies them in the loader's own 500-row slices, in file order:
        INSERT INTO t (cols) SELECT r.cols FROM tmp s, json_populate_record(NULL::t, s.j) r
        WHERE s.ord in the slice ORDER BY s.ord ON CONFLICT (key) DO UPDATE SET col = EXCLUDED.col, ...
    json_populate_record is the conversion PostgREST applies to a request body itself: a price
    book's price_tiers text stays a jsonb string, '5.0' stays numeric 5.0, a float keeps every digit.
    The slices are the 500-row POSTs: a key repeated in a later slice wins as it did over REST, a key
    repeated inside one slice stops the load as it did over REST ("ON CONFLICT DO UPDATE command
    cannot affect row a second time"), and new rows take their ids in the same order.
  * The ledger's month windows are deleted in ONE statement (delete_windows), same rows.
  * A call the loader tolerates failing (load_supabase._guard) runs in a SAVEPOINT: a failure there
    undoes that call only, as a failed PostgREST request did, and the load goes on.
  * A read with no limit returns at most MAX_ROWS rows, as PostgREST's db-max-rows does (the loader
    pages by 1000 anyway).
  * The commit waits out the moments the writer names (YQ_LOAD_COMMIT_AVOID: production's
    quarter-hour jobs, the 8 o'clock agents), so no reader there sees the switch mid-run.

Known differences, both on failures only: a step that fails stops the whole load with nothing
written (REST kept the requests that had passed); and a statement may take STATEMENT_TIMEOUT_S where
a PostgREST request had the authenticator's 8 s, so a tolerated read that timed out over REST (the
load then went on as "upsert only") succeeds here. Every new row of the load, and every audit and
ingest_runs row, takes the transaction's start as its now() (REST: the time of its own request).

Every value reaches SQL as a parameter or through the catalog: names pass ident(), filter values are
cast to the column's own type read from pg_attribute, row values travel as JSON.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import time
import types
from datetime import datetime, timezone

ONE_TXN_ENV = "YQ_LOAD_ONE_TRANSACTION"      # "1": load_supabase writes through this client (no REST fallback)
AVOID_ENV = "YQ_LOAD_COMMIT_AVOID"           # "start-end,..." epoch seconds (UTC) in which the commit is not made
STATEMENT_TIMEOUT_S = 600                    # the ledger's COPY carries ~30 MB: room for a slow uplink
LOCK_TIMEOUT_S = 30                          # a lock held elsewhere fails the load fast: rolled back, run again
IDLE_IN_TXN_S = 300                          # a loader that stops talking: the server rolls its transaction back
HOLD_MAX_S = 1800                            # a commit is never held longer than this, whatever it is asked to avoid
WORK_MEM = "32MB"                            # the ledger's insert sorts ~12 MB by file order: in memory, not on disk
MAX_ROWS = 1000                              # PostgREST's db-max-rows on Supabase
APPLICATION_NAME = "yq-load-one-transaction"

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def ident(name: str) -> str:
    """A quoted identifier. Refuses anything that is not a plain lower-case table or column name."""
    if not isinstance(name, str) or not _IDENT.match(name):
        raise ValueError(f"not a plain identifier: {name!r}")
    return '"' + name + '"'


def body_json(value) -> str:
    """The text httpx sends as a PostgREST request body (json.dumps, compact, NaN refused)."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _filter_text(value) -> str:
    """A filter value as postgrest-py puts it in the URL (str(); booleans as true / false)."""
    if value is None:
        raise ValueError("a filter on null is written .is_(column, 'null')")
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def payload_columns(rows: list[dict]) -> list[str]:
    """The columns of a request body: every key of every row, first seen first (postgrest-py's
    `columns` parameter names the same set)."""
    cols: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                cols.append(k)
    return cols


def parse_avoid(text: str | None) -> tuple[list[tuple[int, int]], str | None]:
    """Pure: ('start-end,...' epoch seconds) -> ([(start, end)], None), or ([], why) when the text is
    not that. An empty text avoids nothing."""
    out: list[tuple[int, int]] = []
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d{9,11})-(\d{9,11})", part)
        if not m or int(m.group(1)) >= int(m.group(2)):
            return [], f"not a list of start-end seconds: {part[:40]!r}"
        out.append((int(m.group(1)), int(m.group(2))))
    return sorted(out), None


def hold_seconds(now_s: float, intervals) -> float:
    """Pure: how long a commit at now_s must wait to land outside every [start, end) interval.
    Back-to-back or overlapping intervals are waited out together."""
    t = float(now_s)
    moved = True
    while moved:
        moved = False
        for a, b in intervals:
            if a <= t < b:
                t, moved = float(b), True
    return t - float(now_s)


class _Query:
    """One PostgREST-style request against the open transaction (built, then execute())."""

    def __init__(self, client: "TxnClient", table: str):
        self.c, self.t = client, table
        self.op, self.cols, self.count = "select", "*", None
        self.where: list[str] = []
        self.params: list = []
        self.order_by: list[str] = []
        self.lim: int | None = None
        self.off: int | None = None
        self.neg = False
        self.payload = None
        self.on_conflict, self.ignore_duplicates = "", False

    # reads
    def select(self, *columns: str, count=None, head=None):
        self.op, self.count = "select", count
        self.cols = ",".join(columns) if columns else "*"
        return self

    def _filter(self, col: str, op: str, value):
        if self.neg:
            raise ValueError("not_ is written here only before is_()")
        self.where.append(f"_t.{ident(col)} {op} %s::{self.c.col_type(self.t, col)}")
        self.params.append(_filter_text(value))
        return self

    def eq(self, col, value):
        return self._filter(col, "=", value)

    def neq(self, col, value):
        return self._filter(col, "<>", value)

    def gt(self, col, value):
        return self._filter(col, ">", value)

    def gte(self, col, value):
        return self._filter(col, ">=", value)

    def lt(self, col, value):
        return self._filter(col, "<", value)

    def lte(self, col, value):
        return self._filter(col, "<=", value)

    def in_(self, col, values):
        if self.neg:
            raise ValueError("not_ is written here only before is_()")
        self.where.append(f"_t.{ident(col)} = any(%s::{self.c.col_type(self.t, col)}[])")
        self.params.append([_filter_text(v) for v in values])
        return self

    def is_(self, col, value):
        neg, self.neg = self.neg, False
        if str(value).lower() != "null":
            raise ValueError("is_ is written here only with 'null'")
        self.c.col_type(self.t, col)                       # the column must exist, as PostgREST checks
        self.where.append(f"_t.{ident(col)} is {'not ' if neg else ''}null")
        return self

    @property
    def not_(self):
        self.neg = True
        return self

    def order(self, column, *, desc=False, nullsfirst=None, foreign_table=None):
        if foreign_table:
            raise ValueError("no embedded resources here")
        nulls = "" if nullsfirst is None else (" nulls first" if nullsfirst else " nulls last")
        self.order_by.append(f"_t.{ident(column)} {'desc' if desc else 'asc'}{nulls}")
        return self

    def limit(self, size, *, foreign_table=None):
        self.lim = int(size)
        return self

    def range(self, start, end, foreign_table=None):
        self.off, self.lim = int(start), int(end) - int(start) + 1
        return self

    # writes
    def delete(self, *, count=None, returning=None):
        self.op, self.count = "delete", count
        return self

    def insert(self, json, *, count=None, returning=None, upsert=False, default_to_null=True):
        self.op, self.payload = "insert", json
        return self

    def update(self, json, *, count=None, returning=None):
        self.op, self.payload = "update", json
        return self

    def upsert(self, json, *, count=None, returning=None, ignore_duplicates=False, on_conflict="",
               default_to_null=True):
        self.op, self.payload = "upsert", json
        self.on_conflict, self.ignore_duplicates = on_conflict, ignore_duplicates
        return self

    def retry(self, enabled: bool):
        return self

    def _where(self) -> str:
        return (" where " + " and ".join(self.where)) if self.where else ""

    def execute(self):
        c, t = self.c, self.t
        if self.op == "select":
            cols = ("_t.*" if self.cols.strip() in ("", "*")
                    else ", ".join(f"_t.{ident(x.strip())}" for x in self.cols.split(",")))
            sql = f"select {cols} from public.{ident(t)} _t{self._where()}"
            if self.order_by:
                sql += " order by " + ", ".join(self.order_by)
            sql += f" limit {min(self.lim, MAX_ROWS) if self.lim is not None else MAX_ROWS}"
            if self.off:
                sql += f" offset {self.off}"
            text = c.fetch_one(f"select coalesce(json_agg(_q), '[]'::json)::text from ({sql}) _q", self.params)
            n = None
            if self.count:
                n = int(c.fetch_one(f"select count(*) from public.{ident(t)} _t{self._where()}", self.params))
            return types.SimpleNamespace(data=json.loads(text), count=n)
        if self.op == "delete":
            if not self.where:
                raise ValueError("a DELETE without a filter is refused (PostgREST's safeupdate)")
            n = c.run(f"delete from public.{ident(t)} _t{self._where()}", self.params)
            return types.SimpleNamespace(data=[], count=n)
        if self.op == "update":
            if not self.where:
                raise ValueError("an UPDATE without a filter is refused (PostgREST's safeupdate)")
            if not isinstance(self.payload, dict) or not self.payload:
                raise ValueError("an update takes one object")
            sets = ", ".join(f"{ident(k)} = _b.{ident(k)}" for k in self.payload)
            n = c.run(f"update public.{ident(t)} _t set {sets} from json_populate_record(null::public.{ident(t)}, "
                      f"%s::json) _b{self._where()}", [body_json(self.payload)] + self.params)
            return types.SimpleNamespace(data=[], count=n)
        rows = self.payload if isinstance(self.payload, list) else [self.payload]
        if self.op == "insert":
            return types.SimpleNamespace(data=[], count=c.insert_rows(t, rows))
        if self.op == "upsert":
            return types.SimpleNamespace(data=[], count=c.bulk_upsert(t, rows, self.on_conflict,
                                                                      self.ignore_duplicates))
        raise ValueError(f"unknown request: {self.op}")


class TxnClient:
    """The loader's client for one load: one psycopg session on DATABASE_URL, one transaction,
    one commit. open() it, hand it to load_supabase's table functions, then commit() or rollback(),
    and close() it."""

    def __init__(self, conn, started_at=None):
        self.conn, self.started_at = conn, started_at
        self._types: dict[str, dict[str, str]] = {}
        self._n = 0
        self.statements = 0
        self.commit_sent = False                             # set the moment the one COMMIT goes out

    @classmethod
    def open(cls, dsn: str | None = None, *, statement_timeout_s: int = STATEMENT_TIMEOUT_S,
             lock_timeout_s: int = LOCK_TIMEOUT_S, idle_s: int = IDLE_IN_TXN_S) -> "TxnClient":
        """Connect and open THE transaction with its timeouts (a savepoint needs an open transaction: on
        an idle connection psycopg's transaction() would BEGIN and COMMIT on its own). The timeouts are
        SET LOCAL: they hold for this transaction whichever pooler mode DATABASE_URL names, and never
        outlive it on a pooled backend."""
        import psycopg
        dsn = dsn or os.environ.get("DATABASE_URL")
        if not dsn:
            raise RuntimeError("DATABASE_URL is not set: the one-transaction load needs its own database session")
        # TCP keepalives as scripts/ops/db.py has them: a connection the pooler dropped is noticed in about a
        # minute; no prepared statements (a transaction pooler may hand a session's statements to another backend)
        conn = psycopg.connect(dsn, autocommit=False, connect_timeout=30, application_name=APPLICATION_NAME,
                               keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
                               prepare_threshold=None)
        try:
            with conn.cursor() as cur:
                cur.execute("select now()")                 # opens the transaction; every new row's imported_at
                started = cur.fetchone()[0]
                cur.execute(f"set local statement_timeout = '{int(statement_timeout_s)}s'")
                cur.execute(f"set local lock_timeout = '{int(lock_timeout_s)}s'")
                cur.execute(f"set local idle_in_transaction_session_timeout = '{int(idle_s)}s'")
                cur.execute(f"set local work_mem = '{WORK_MEM}'")
        except BaseException:
            conn.close()
            raise
        return cls(conn, started)

    # plumbing
    def run(self, sql: str, params=None) -> int:
        self.statements += 1
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return int(cur.rowcount if cur.rowcount is not None else 0)

    def fetch_one(self, sql: str, params=None):
        self.statements += 1
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return row[0] if row else None

    def columns(self, table: str) -> dict[str, str]:
        """{column: its base type as format_type spells it} of public.<table>, read once. No typmod: a
        filter value is compared as PostgREST's untyped text is, never rounded to numeric(12,3) or cut
        to varchar(n) first."""
        if table not in self._types:
            self.statements += 1
            with self.conn.cursor() as cur:
                cur.execute("select a.attname, format_type(a.atttypid, null) from pg_attribute a "
                            "where a.attrelid = to_regclass(%s) and a.attnum > 0 and not a.attisdropped "
                            "order by a.attnum", (f"public.{ident(table)}",))
                found = {str(n): str(typ) for n, typ in cur.fetchall()}
            if not found:
                raise LookupError(f'relation "public.{table}" does not exist')
            self._types[table] = found
        return self._types[table]

    def col_type(self, table: str, col: str) -> str:
        types_ = self.columns(table)
        if col not in types_:
            raise LookupError(f'column {table}.{col} does not exist')
        return types_[col]

    # the builder entry point, and the guarded call
    def table(self, name: str) -> _Query:
        return _Query(self, name)

    @contextlib.contextmanager
    def savepoint(self):
        """A guarded call of the loader (load_supabase._guard): psycopg's nested transaction is a
        SAVEPOINT inside the open one. An error in it rolls back to the savepoint and is raised for
        the loader's own except, which carries on as it did after a failed PostgREST request."""
        self.statements += 2                                 # SAVEPOINT, then RELEASE or ROLLBACK TO
        with self.conn.transaction():
            yield

    # the set-based writes
    def insert_rows(self, table: str, rows: list[dict]) -> int:
        """INSERT a request body of rows (audit_log, ingest_runs) the way PostgREST does."""
        rows = [r for r in rows if r is not None]
        if not rows:
            return 0
        cols = payload_columns(rows)
        t = ident(table)
        return self.run(f"insert into public.{t} ({', '.join(ident(c) for c in cols)}) "
                        f"select {', '.join('_b.' + ident(c) for c in cols)} "
                        f"from json_populate_recordset(null::public.{t}, %s::json) _b", [body_json(rows)])

    def bulk_upsert(self, table: str, rows: list[dict], on_conflict: str, ignore_duplicates: bool = False,
                    chunk: int = 500) -> int:
        """Every row of one table in three statements: COPY the rows (JSON text, in file order) into a temp
        table that drops at commit, then one server-side loop of INSERT .. ON CONFLICT .. DO UPDATE of
        every column of the body (PostgREST's merge-duplicates), one `chunk` of rows at a time, as the
        REST path's POSTs of that size. Returns the rows sent."""
        rows = list(rows or [])
        if not rows:
            return 0
        keys = [k.strip() for k in str(on_conflict or "").split(",") if k.strip()]
        if not keys:
            raise ValueError(f"an upsert of {table} names no conflict key (on_conflict)")
        size = int(chunk)
        if size < 1:
            raise ValueError(f"not a slice size: {chunk!r}")
        cols = payload_columns(rows)
        self._n += 1
        tmp = f"_load_rows_{self._n}"
        t = ident(table)
        insert = (f"insert into public.{t} ({', '.join(ident(c) for c in cols)}) "
                  f"select {', '.join('_r.' + ident(c) for c in cols)} "
                  f"from {tmp} _s, json_populate_record(null::public.{t}, _s.j) _r "
                  f"where _s.ord >= _k and _s.ord < _k + {size} order by _s.ord "
                  f"on conflict ({', '.join(ident(k) for k in keys)}) do ")
        insert += "nothing" if ignore_duplicates else (
            "update set " + ", ".join(f"{ident(c)} = excluded.{ident(c)}" for c in cols))
        with self.conn.cursor() as cur:
            cur.execute(f"create temp table {tmp} (ord bigint primary key, j json not null) on commit drop")
            with cur.copy(f"copy {tmp} (ord, j) from stdin") as cp:
                for i, r in enumerate(rows):
                    cp.write_row((i, body_json(r)))
            # one round trip: the slices run one after the other on the server, each seeing the ones before
            cur.execute(f"do $load$ begin for _k in 0..{len(rows) - 1} by {size} loop {insert}; end loop; end $load$")
        self.statements += 3
        return len(rows)

    def delete_windows(self, table: str, col: str, windows: list[tuple[str, str]]) -> int:
        """The rows with col inside any [lo, hi] window, in ONE statement (the REST path deletes the
        same windows one call each)."""
        if not windows:
            return 0
        typ = self.col_type(table, col)
        cond = " or ".join(f"(_t.{ident(col)} >= %s::{typ} and _t.{ident(col)} <= %s::{typ})" for _ in windows)
        return self.run(f"delete from public.{ident(table)} _t where {cond}",
                        [str(v) for lo, hi in windows for v in (lo, hi)])

    # the end
    def commit(self, avoid=None, *, now=time.time, sleep=time.sleep, say=print) -> float:
        """THE commit. When it would land inside one of the `avoid` intervals (epoch seconds), it waits
        until the last of them ends first (HOLD_MAX_S at most); the transaction stays open meanwhile and
        production keeps reading the data as it was. A transaction that an error has aborted is never
        committed (Postgres would answer a COMMIT there with a silent rollback): it raises. Sets
        commit_sent the moment the COMMIT goes out. Returns the seconds it waited."""
        self._healthy("commit")
        wait = min(hold_seconds(now(), avoid or []), float(HOLD_MAX_S))
        if wait > 0:
            until = datetime.fromtimestamp(now() + wait, timezone.utc)
            say(f"  (one transaction: the commit waits {wait:.0f} s, until {until:%H:%M:%S} UTC: production's "
                f"scheduled jobs read these tables until then)")
            with self.conn.cursor() as cur:
                cur.execute(f"set local idle_in_transaction_session_timeout = '{int(wait) + 120}s'")
            sleep(wait)
            self._healthy("commit")
        self.commit_sent = True
        self.conn.commit()
        return wait

    def _healthy(self, what: str) -> None:
        """The transaction is open and no error has aborted it, or this raises."""
        state = getattr(getattr(getattr(self.conn, "info", None), "transaction_status", None), "name", "INTRANS")
        if state != "INTRANS":
            raise RuntimeError(f"no {what}: the transaction is {state} (an error aborted it, or it is closed); "
                               f"nothing of this load was written")

    def rollback(self) -> None:
        try:
            self.conn.rollback()
        except Exception:  # noqa: BLE001  (a connection that is gone has rolled back on the server)
            pass

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass
