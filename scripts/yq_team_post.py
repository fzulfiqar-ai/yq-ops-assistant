"""python -m scripts.yq_team_post --run <run> : stores one load's team message for the Command Centre's Copy button.

The load's worker runs it after the delivery step (scripts/yq_load_go.py deliver_after); it can be run again by
hand for a load whose delivery was made. What it stores is exactly the team message `python -m scripts.ops deliver`
wrote for that load, exports/ops/<date>/delivery/<run>/whatsapp.txt, and only when the manifest beside it says so:
  * manifest.json is there, names this load, is no preview and is the delivery of a full brief;
  * the privacy filter passed, as the manifest records it (team_text true, no team_problems: the deliver command
    ran scripts/ops/render.py privacy_problems on the finished text before it wrote the file);
  * the file's sha256 is the one the manifest keeps (it is the file the brief wrote, unchanged since);
  * it holds some text, and the manifest names the data date.
It never makes, changes or checks a word of the message itself, and it stores nothing from a code worktree or for a
rehearsal.

The store: one row of team_messages (run, data_date, text, sha256), inserted, or put in place of the row of the same
run, in one short transaction on DATABASE_URL (statement timeout 15 s). DATABASE_URL comes from the environment, else
from this checkout's own .env, never from a folder above it. No other table is named. The text is the file as it
is; its sha256 is the manifest's, so the row can be checked against the file.

Its last line is always one of
    TEAM_POST: stored
    TEAM_POST: skipped (<why>)     nothing to store for this load (exit 0)
    TEAM_POST: failed (<why>)      it should be stored and is not: the database refused it or did not answer (exit 1)
and the why never carries the database's address, a password or a word of the message.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ops import gitinfo, loadplan, paths, status  # noqa: E402  (none of them opens a connection)

MODULE = "scripts.yq_team_post"
MARK = "TEAM_POST:"
EXIT_OK, EXIT_FAILED = 0, 1
STATEMENT_TIMEOUT_S = 15
CONNECT_TIMEOUT_S = 15
APPLICATION_NAME = "yq-team-post"
# the only two statements this program sends: they name the one table and nothing else
TIMEOUT_SQL = f"set local statement_timeout = '{STATEMENT_TIMEOUT_S}s'"
UPSERT_SQL = ("insert into team_messages (run, data_date, text, sha256) values (%s, %s, %s, %s) "
              "on conflict (run) do update set data_date = excluded.data_date, text = excluded.text, "
              "sha256 = excluded.sha256")


class Skipped(Exception):
    """Nothing to store for this load (exit 0)."""


class Failed(Exception):
    """It should be stored and is not (exit 1)."""


# ── the message to store ─────────────────────────────────────────────────────────────────────────

def _under(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
    except (OSError, ValueError):
        return False
    return True


def find_manifest(run: str) -> Path | None:
    """The manifest the deliver command wrote for this load: the one its newest 'pack' row names, when that file
    is in this data root's exports/ops/<date>/delivery/<run>/; else the one found there. Never a preview's: its
    folder is <run>-preview and no row names it."""
    base = paths.exports_dir()
    try:
        rows = loadplan.delivery_read(run)
    except (OSError, ValueError):
        rows = []
    for row in reversed(rows):
        named = row.get("manifest") if row.get("event") == "pack" else None
        if named:
            p = Path(str(named))
            if p.name == "manifest.json" and p.parent.name == run and _under(p, base) and p.is_file():
                return p
    hits = sorted(base.glob(f"*/delivery/{run}/manifest.json")) if base.is_dir() else []
    return hits[-1] if hits else None


def _part(man: dict, key: str, name: str) -> str:
    """man[key][name] as text, '' when the manifest does not have it."""
    block = man.get(key)
    return str(block.get(name) or "") if isinstance(block, dict) else ""


def team_message(run: str) -> dict:
    """{run, data_date, text, sha256}: this load's team message, checked against its manifest. Raises Skipped."""
    manifest = find_manifest(run)
    if manifest is None:
        raise Skipped("no delivery manifest for this load")
    try:
        man = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Skipped(f"the delivery manifest cannot be read ({type(exc).__name__})") from None
    if not isinstance(man, dict) or str(man.get("run") or "") != run or man.get("preview"):
        raise Skipped("the delivery manifest is not this load's own")
    kind = str(man.get("kind") or "")
    if kind != "full":
        raise Skipped(f"this load's delivery is no full brief (kind {kind or 'none'}), so it has no team message")
    if "team_text" not in man:
        raise Skipped("the privacy filter did not pass: the manifest does not record it")
    if man.get("team_text") is not True or man.get("team_problems"):
        raise Skipped("the privacy filter did not pass: no team message was made")
    name = _part(man, "files", "whatsapp")
    if not name:
        raise Skipped("no text: the delivery names no team message file")
    if any(ch in name for ch in "/\\:") or name in (".", ".."):       # a plain file name, on every system
        raise Skipped("the manifest names a team message file outside its folder")
    try:
        body = (manifest.parent / name).read_bytes()
    except OSError as exc:
        raise Skipped(f"no text: the team message file cannot be read ({type(exc).__name__})") from None
    sha = hashlib.sha256(body).hexdigest()
    if sha != _part(man, "sha256", "whatsapp"):
        raise Skipped("the team message file is not the one the brief wrote (its checksum is not the manifest's)")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        raise Skipped("no text: the team message file is not UTF-8 text") from None
    if not text.strip():
        raise Skipped("no text: the team message is empty")
    try:
        data_date = date.fromisoformat(str(man.get("data_date") or ""))
    except ValueError:
        raise Skipped("the delivery manifest names no data date") from None
    return {"run": run, "data_date": data_date, "text": text, "sha256": sha}


# ── the store ────────────────────────────────────────────────────────────────────────────────────

def database_url(env=None, env_file: Path | None = None) -> str:
    """DATABASE_URL from the environment, else from this checkout's own .env (python-dotenv's walk up the folders
    is never used: from a code worktree it would find the project folder's .env, that is production)."""
    env = os.environ if env is None else env
    dsn = str(env.get("DATABASE_URL") or "").strip()
    if dsn:
        return dsn
    try:
        from dotenv import dotenv_values
    except ImportError:
        return ""
    return str(dotenv_values(env_file or ROOT / ".env").get("DATABASE_URL") or "").strip()


def connect_db(dsn: str):
    """One psycopg session: autocommit off, a short connect timeout, no prepared statements (a transaction pooler
    may hand a session's statements to another backend), its name in pg_stat_activity."""
    import psycopg
    return psycopg.connect(dsn, autocommit=False, connect_timeout=CONNECT_TIMEOUT_S,
                           application_name=APPLICATION_NAME, prepare_threshold=None)


def store(msg: dict, dsn: str, connect=None) -> None:
    """The one row, in one short transaction: the statement timeout (SET LOCAL: it ends with the transaction,
    whatever pooler DATABASE_URL names), the upsert, the commit. Raises Failed, naming only the kind of error."""
    try:
        conn = (connect or connect_db)(dsn)
    except Exception as exc:  # noqa: BLE001  (its message may name the server or the login: only its kind is told)
        raise Failed(f"no connection to the database ({type(exc).__name__})") from None
    try:
        with conn.cursor() as cur:
            cur.execute(TIMEOUT_SQL)
            cur.execute(UPSERT_SQL, (msg["run"], msg["data_date"], msg["text"], msg["sha256"]))
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001  (the connection may be gone already: nothing was committed)
            pass
        raise Failed(f"the database did not store it ({type(exc).__name__})") from None
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


# ── the command ──────────────────────────────────────────────────────────────────────────────────

def in_code_checkout() -> bool:
    """True in a code worktree (.claude/worktrees/<name>, or any other linked worktree): it reads the project
    folder's files, but nothing it runs may write the project's database."""
    if paths.is_worktree():
        return True
    try:
        return gitinfo.is_linked_worktree(ROOT)
    except Exception:  # noqa: BLE001  (no git on this PC: the folder test above is the answer)
        return False


def post(run: str, *, connect=None, env=None, env_file: Path | None = None,
         code_checkout: bool | None = None) -> tuple[int, str]:
    """(exit code, the last line) for one load."""
    try:
        if not loadplan.is_run(run):
            raise Skipped("not a run name (ddmmyy_HHMM)")
        if run.startswith(status.REHEARSAL_PREFIX):
            raise Skipped("a rehearsal: nothing is stored")
        if in_code_checkout() if code_checkout is None else code_checkout:
            raise Skipped("this is a code worktree: the team message is stored from the project folder only")
        msg = team_message(run)
        dsn = database_url(env, env_file)
        if not dsn:
            raise Failed("DATABASE_URL is not set: the project folder's .env names it")
        store(msg, dsn, connect)
    except Skipped as exc:
        return EXIT_OK, f"{MARK} skipped ({exc})"
    except Failed as exc:
        return EXIT_FAILED, f"{MARK} failed ({exc})"
    except Exception as exc:  # noqa: BLE001  (whatever it was, the last line says so)
        return EXIT_FAILED, f"{MARK} failed (an unexpected error: {type(exc).__name__})"
    return EXIT_OK, f"{MARK} stored"


def main(argv: list[str] | None = None, **deps) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog=f"python -m {MODULE}", description=__doc__.split("\n\n")[0])
    ap.add_argument("--run", required=True, help="the load (ddmmyy_HHMM)")
    args = ap.parse_args(argv)
    code, line = post(args.run, **deps)
    print(line, flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
