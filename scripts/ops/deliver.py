"""python -m scripts.ops deliver [--run <run>] [--preview]

After a load (the load's worker runs it once the load has closed: scripts/yq_load_go.py):
  * first, the MOVEMENT STEP (moves_step), in a process of its own with its own time budget
    (MOVES_TIMEOUT_S, inside the worker's DELIVER_TIMEOUT_S for the whole delivery): the five move rules of
    /yq-watch recorded for this ledger (scripts.ops.checks.ensure_recorded, once per ledger; never by a
    preview), so the brief and the e-mail list the transfers the new ledger shows to check, and the facts of
    the brief's MOVEMENTS line (scripts.ops.moves.brief_facts). Both read the stock ledger and the costs; on
    a slow database they are the delivery's longest wait, so they get their own clock. A step that fails, or
    takes longer and is stopped, is one line of this command's screen and never stops the delivery: the
    brief, which takes the step's answer over and redoes none of it, then says the MOVEMENTS were not read
    and lists the transfer items as they stood. The step's process is this interpreter running this
    module's own _moves_child (multiprocessing, spawn): never a shell or another program (scripts/ops
    imports no subprocess, tests/test_ops_readonly.py);
  * the full brief, worked out ONCE exactly as /yq-brief works it out (sales.full_screen), saved as
    exports/ops/<D>/brief.txt and .json, the action register recorded and the brief stamped as read,
    just as a /yq-brief would; the first plain /yq-brief after it shows this very brief (state
    brief_unshown), so the chat and the e-mail carry the same figures and nothing is worked out twice;
  * in exports/ops/<D>/delivery/<run>/:
      email.html, email.txt   the management e-mail (private: accounts, shops, the rep table)
      whatsapp.txt            the team message, made only when render.privacy_problems finds nothing
      whatsapp.html           that message in an editable box, with Copy and Open in WhatsApp
      manifest.json           the kind, the subject, each file and its sha256
  * one 'pack' row in the run's delivery journal (loadplan.delivery_append).

What is made depends on how the load closed (its journal's closing row):
  full    it finished and its checks passed (a publish that needs a look does not count against it)
  look    it finished, but its reconcile or its gate needs a look: a short note with no figure
  failed  it was closed as failed after it had begun to write: a short note with no figure
  none    nothing (it stopped before it wrote anything, or it is not closed)

--preview writes the files into delivery/<run>-preview/ and records nothing at all: not the action
register (the move rules neither), not the state, not the delivery journal. Nothing is ever sent from
here: scripts/yq_send.py sends, and only what this command wrote (it never works a figure out itself).
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime

from scripts.ops import db  # noqa: F401  (first: it blanks the side channels before any app import)
from scripts.ops import actions as actions_mod
from scripts.ops import fmt, loadplan, paths, render, status

COMMAND = "deliver"
LOOK_STEPS = ("reconcile", "post_gate")      # a load whose check of these needs a look sends no figure
# the movement step's own budget, in seconds: a cold ledger read and the database's parity and cost reads take
# about 20 s; it leaves the brief and the e-mail most of the worker's 300 s (yq_load_go.DELIVER_TIMEOUT_S)
MOVES_TIMEOUT_S = 90
PREVIEW_RECORDS_NOTHING = "a preview records nothing"


def add_arguments(parser) -> None:
    parser.add_argument("--run", help="the load (ddmmyy_HHMM); default: the last load on record")
    parser.add_argument("--preview", action="store_true",
                        help="write the files into delivery/<run>-preview/ and record nothing")


def closing_row(run: str) -> dict | None:
    rows = [r for r in loadplan.journal_read(run) if r.get("event") in ("finished", "failed")]
    return rows[-1] if rows else None


def kind_of(closing: dict | None) -> tuple[str, list[str]]:
    """(full | look | failed | none, the steps that need a look)."""
    if not closing:
        return "none", []
    if closing.get("event") == "finished":
        look = [s for s in (closing.get("needs_look") or []) if s in LOOK_STEPS]
        return ("look" if look else "full"), look
    if closing.get("written"):
        return "failed", [str(closing.get("step") or "")]
    return "none", []


def load_info(run: str, closing: dict | None) -> dict:
    """What the e-mail says about the load itself: its checks, its dates, its time, the profitability
    evidence the load check kept (load_check: plan['profitability'])."""
    try:
        plan = loadplan.read_plan(run)
    except (OSError, ValueError):
        plan = {}
    rows = loadplan.journal_read(run)
    info = {"run": run, "result": (closing or {}).get("result"), "timings": (closing or {}).get("timings"),
            "total_seconds": (closing or {}).get("total_seconds"), "as_on": (plan or {}).get("as_on") or {},
            "profitability": (plan or {}).get("profitability")}
    for r in rows:
        step, event = r.get("step"), r.get("event")
        if step == "refresh" and event == "step_ok":
            info["verify"] = "passed" + (f" (sales to {fmt.day(r.get('data_as_of'))})" if r.get("data_as_of") else "")
        elif step == "reconcile":
            info["reconcile"] = "matched" if event == "step_ok" else "needs a look"
        elif step == "post_gate" and event in ("step_ok", "step_failed"):
            info["gate"] = (f"passed ({r.get('deltas')} explained changes)" if event == "step_ok" else "needs a look")
    return info


def delivery_settings() -> dict:
    """business_data/ops/delivery.json (git-ignored), or {} when there is none. Only one setting is read
    here: customer_names (default on), whether the e-mail names accounts and shops."""
    f = paths.ops_dir() / "delivery.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    except (OSError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def own_last_ingest_run(run: str) -> int | None:
    """The newest ingest_runs id this load's own refresh left (its journalled refresh row), if known."""
    for r in reversed(loadplan.journal_read(run)):
        pair = r.get("ingest_runs") if r.get("step") == "refresh" else None
        if isinstance(pair, list) and len(pair) == 2:
            try:
                return int(pair[1])
            except (TypeError, ValueError):
                return None
    return None


def newest_ingest_run() -> int | None:
    """The newest ingest_runs id in the database now (read-only), or None when it cannot be read."""
    try:
        from scripts.ops import db
        with db.session() as session:
            return int(session.q("select coalesce(max(id), 0) as max_id from ingest_runs")[0]["max_id"])
    except Exception:  # noqa: BLE001  (the brief itself says so if the database does not answer)
        return None


def stale_reason(run: str, state: dict) -> str | None:
    """Why the database no longer holds what this load left, so that its full brief cannot be made now
    (it would show another load's figures under this load's name), or None. Right after the load, as the
    worker runs this, none holds; a retry hours later may meet any of them."""
    last = str((state.get("last_load") or {}).get("run") or "")
    if last != run:
        return f"the last load on record is run {last or '(none)'}, not this one"
    failed = status.failed_load()
    if failed and failed.get("run") != run:
        return f"a later load (run {failed.get('run')}) failed after it began to write"
    own = own_last_ingest_run(run)
    newest = newest_ingest_run() if own is not None else None
    if own is not None and newest is not None and newest > own:
        return "the database was written after this load (a Data-page upload or another load)"
    return None


def _moves_child(conn, root: str, record: bool, days: int) -> None:
    """The movement step itself, run in its own process by moves_step: the move rules recorded for this ledger
    (`record`; sales.record_moves, which asks checks.ensure_recorded) and the MOVEMENTS facts (sales.read_moves,
    moves.brief_facts), sent back on `conn` as one answer {facts, facts_why, recorded, recorded_why}. A part that
    fails is its reason in the answer; nothing here raises, and the answer is sent once."""
    out = {"facts": None, "facts_why": None, "recorded": None,
           "recorded_why": None if record else PREVIEW_RECORDS_NOTHING}
    try:
        try:
            paths.set_data_root(root)           # the parent's data root: a process of its own does not inherit it
            from scripts.ops import sales
        except Exception as exc:  # noqa: BLE001  (a reason in the answer, never a stop)
            out["facts_why"] = f"{type(exc).__name__}: {str(exc)[:120]}"
            if record:
                out["recorded_why"] = out["facts_why"]
            return
        if record:
            out["recorded"], out["recorded_why"] = sales.record_moves(True)
        try:
            out["facts"] = sales.read_moves(days)
        except Exception as exc:  # noqa: BLE001  (a reason in the answer, never a stop)
            out["facts_why"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    finally:
        try:
            conn.send(out)
        finally:
            conn.close()


def _failed_step(why: str, record: bool) -> dict:
    return {"facts": None, "facts_why": why, "recorded": None,
            "recorded_why": why if record else PREVIEW_RECORDS_NOTHING}


def moves_step(record: bool, days: int = 7, *, timeout: float | None = None, target=None) -> dict:
    """The movement step (the module text) in a process of its own: this interpreter, spawned, running `target`
    (_moves_child) with the same environment, the same data root and the same folder, given `timeout` seconds
    (MOVES_TIMEOUT_S) to answer. Returns {facts, facts_why, recorded, recorded_why, seconds}: the child's answer,
    or, when it did not start, gave no answer or ran out of time (it is then stopped), the reason in its place.
    It never raises: the brief and the e-mail are made whatever it answers."""
    import multiprocessing
    budget = MOVES_TIMEOUT_S if timeout is None else timeout
    began = time.monotonic()
    answer: dict | None = None
    why = None
    child = None
    try:
        ctx = multiprocessing.get_context("spawn")
        receive, send = ctx.Pipe(duplex=False)
        child = ctx.Process(target=target or _moves_child, name="yq-delivery-movement-step", daemon=True,
                            args=(send, str(paths.data_root()), bool(record), int(days)))
        child.start()
        send.close()                            # the child holds its own end: its end of it is seen when it stops
        try:
            if receive.poll(budget):
                got = receive.recv()
                answer = got if isinstance(got, dict) else None
                why = None if answer is not None else "the movement step gave no answer"
            else:
                why = f"the movement step took longer than {budget:g} s and was stopped"
        except (EOFError, OSError):
            why = "the movement step ended without an answer"
        finally:
            receive.close()
    except Exception as exc:  # noqa: BLE001  (the delivery goes on)
        why = f"the movement step could not run ({type(exc).__name__})"
    finally:
        if child is not None and child.pid is not None:
            child.join(5 if answer is not None else 0)
            if child.is_alive():
                child.terminate()
                child.join(5)
    out = dict(answer) if answer is not None else _failed_step(why or "the movement step gave no answer", record)
    out["seconds"] = round(time.monotonic() - began, 1)
    return out


def moves_note(step: dict, preview: bool) -> str | None:
    """The one line the delivery adds when the movement step did not record the move rules (never for a
    preview, which records nothing); None when it did."""
    if preview:
        return None
    answer = step.get("recorded")
    if isinstance(answer, dict) and answer.get("ok"):
        return None
    why = " ".join(str(step.get("recorded_why") or (answer or {}).get("why") or "no reason given").split())
    said = f"Transfer checks: not recorded this time ({why}); the brief lists the transfer items as they stood."
    try:
        return fmt.careful(said)
    except ValueError:              # a word the brief never uses, in the reason: the line without it
        return "Transfer checks: not recorded this time; the brief lists the transfer items as they stood."


def run(args) -> dict:
    out = {"ok": True, "command": COMMAND, "data_date": None, "text": "", "flags": {}, "files": [], "warnings": []}
    state = status.read_state()
    name = getattr(args, "run", None) or str((state.get("last_load") or {}).get("run") or "")
    if not loadplan.is_run(name):
        return dict(out, ok=False, exit=2, text="No load to deliver: name a run (ddmmyy_HHMM).")
    preview = bool(getattr(args, "preview", False))
    closing = closing_row(name)
    kind, steps = kind_of(closing)
    made_on = status.today_bahrain()
    made_at = datetime.now(status.BAHRAIN).strftime("%H:%M")
    if kind == "none":
        if not preview:
            loadplan.delivery_append(name, "pack", kind="none", text="Nothing to deliver: the load did not write.")
        return dict(out, text=f"Delivery of run {name}: nothing to deliver (the load did not write anything).",
                    flags={"kind": "none"})

    if kind == "full" and not preview:
        why = stale_reason(name, state)
        if why:
            loadplan.delivery_append(name, "pack", kind="refused", why=why, text=f"Not prepared: {why}.")
            return dict(out, ok=False, exit=2, flags={"kind": "refused"},
                        text=f"Delivery of run {name}: NOT prepared: {why}. Its brief would show other figures than "
                             "this load's, so nothing is sent for it.")

    files: dict[str, str] = {}
    notes: list[str] = []
    if kind == "full":
        from scripts.ops import sales            # the whole brief engine: loaded only for a full delivery
        # the movement step first, on its own clock: the brief then lists what this ledger shows and redoes none of it
        step = moves_step(not preview, sales.MOVES_DAYS)
        said = moves_note(step, preview)
        if said:
            notes.append(said)
        screen = sales.full_screen(record=not preview, enrich=True, moves=step)
        d, facts = screen["d"], screen["facts"]
        top, every = actions_mod.ranked(facts, d)
        team = render.team_text(facts, made_on)
        problems = render.privacy_problems(team, facts)
        names = delivery_settings().get("customer_names", True) is not False
        info = load_info(name, closing)
        files["html"] = render.email_html(facts, top, every, info, None if problems else team, made_on, names=names)
        files["text"] = render.email_text(facts, top, every, info, None if problems else team, made_on, names=names)
        if not problems:
            files["whatsapp"] = team + "\n"
            files["page"] = render.whatsapp_page(team, made_on)
        subject = render.email_subject(facts, made_on)
        data_date = d
    else:
        data_date = fmt.as_date((closing or {}).get("data_date")) or made_on
        subject = render.note_subject(kind, name)
        files["html"] = render.note_html(kind, name, steps)
        files["text"] = render.note_text(kind, name, steps)
        problems, screen, facts = [], None, None

    folder = paths.exports_dir(data_date) / "delivery" / (name + ("-preview" if preview else ""))
    names_of = {"html": "email.html", "text": "email.txt", "whatsapp": "whatsapp.txt", "page": "whatsapp.html"}
    written = {}
    for key, text in files.items():
        written[key] = str(paths.write_text(folder / names_of[key], text))
    manifest = {"run": name, "kind": kind, "subject": subject, "made_on": made_on.isoformat(), "made_at": made_at,
                "data_date": data_date.isoformat(), "preview": preview, "files": {k: names_of[k] for k in files},
                "sha256": {k: _sha(v) for k, v in files.items()}, "team_text": "whatsapp" in files,
                "team_problems": problems or None}
    mpath = paths.write_text(folder / "manifest.json", json.dumps(manifest, indent=1, ensure_ascii=False) + "\n")
    out["files"] = list(written.values()) + [str(mpath)]
    out["data_date"] = data_date.isoformat()

    lines = [f"Delivery of run {name} ({'preview: nothing recorded' if preview else kind}): {subject}"]
    if kind == "full":
        if not preview:
            from scripts.ops import sales
            out["files"] += [str(f) for f in sales.save_screen(screen["d"], "brief", screen["text"], facts)]
            patch = dict(screen["patch"] or {})
            if patch.get("last_brief_data_date"):          # the brief was whole: the chat shows it once
                patch["brief_unshown"] = {"run": name, "data_date": screen["d"].isoformat(), "at": made_at}
            status.write_state(patch)
        lines.append("Team message: " + ("ready (whatsapp.txt, whatsapp.html)" if not problems
                                         else "NOT made: " + "; ".join(problems)))
        lines += notes
        out["warnings"] = list(screen["warnings"] or [])
    if not preview:
        loadplan.delivery_append(name, "pack", kind=kind, manifest=str(mpath), subject=subject,
                                 team_text=not problems, text=f"The {kind} delivery was prepared.")
    lines.append(f"Files: {folder}")
    return dict(out, text="\n".join(lines), flags={"kind": kind, "team_text": not problems})
