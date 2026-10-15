"""python -m scripts.yq_send : sends the e-mail of one load's delivery. The ONLY program that sends anything.

    python -m scripts.yq_send --run <run> [--auto]    send it (the load's worker runs this right after a load)
    python -m scripts.yq_send --run <run> --test      to the test address only, marked [TEST], never counted
    python -m scripts.yq_send --run <run> --resend    send it once more: a Yes in the Windows box comes first

What it sends is exactly what `python -m scripts.ops deliver` wrote for that load, in
exports/ops/<date>/delivery/<run>/: the manifest names the subject and the files, and each file's sha256 is
checked against it before anything goes. It never works a figure out itself. When the delivery of the load
was not prepared (the PC slept right after the load, say), it runs that command first.

Settings, business_data/ops/delivery.json (git-ignored; no file means off):
    {"mode": "on" | "test" | "off", "from": "YQ Bahrain <reports@...>", "to": ["..."], "test_to": ["..."],
     "reply_to": "...", "customer_names": true}
mode test turns every send into a test send (the first days); off sends nothing and says so.

The key: Windows Credential Manager, the generic credential YQ_RESEND_API_KEY (scripts/yq_secrets.py). It is
never printed and never written anywhere; only the request's Authorization header carries it.

No second copy: a 'sent' row in the run's delivery journal (business_data/ops/loads/<run>/delivery.jsonl) for
the same kind and the same recipients refuses a second send; --resend asks at the box first. Each request
also carries an Idempotency-Key, so a retry of one attempt can never deliver two copies.

One full e-mail per data set: when the newest e-mail that went for real (a 'sent' row in any load's delivery
journal; a test copy is 'test_sent' and never counts) is the full brief of the very data of this load
(data_set: the data date, the sha256 of each Focus report the load took, and the figures, options and
programs of its check, as plan.json recorded them), nothing is sent and the row reads "skipped: same data as
run <run>". The note that such a load needs a look would only repeat it, and is held back the same way. The
note of a load that failed after it began to write always goes (the database may hold part of it), and the
brief after such a note goes too, whatever its data. --resend still sends, after a Yes at the box.

Every outcome is a row of that journal and state.json's last_delivery. Exit codes: 0 sent, or nothing to do
(off, nothing to deliver, the same data went already); 1 error (recorded; nothing is reloaded, and the line
that retries it is printed, or why none is needed); 2 refused (a copy went already, a newer load exists, No
at the box).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import yq_secrets  # noqa: E402
from scripts.ops import gitinfo, loadplan, paths, status  # noqa: E402  (none of them opens a connection)

API = "https://api.resend.com/emails"
TIMEOUT_S = 30
DELIVER_TIMEOUT_S = 300
MODULE = "scripts.yq_send"
EXIT_OK, EXIT_ERROR, EXIT_REFUSED = 0, 1, 2
LOOK_STEPS = ("reconcile", "post_gate")      # scripts/ops/deliver.py LOOK_STEPS: these needing a look make a note
GIT_ROOT = ROOT                              # the checkout whose history holds the commits the checks record
_COMMIT = re.compile(r"[0-9a-f]{7,40}")
_ADDRESS = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[A-Za-z]{2,}$")
_SENDER = re.compile(r"^(?:[^<>@]*<)?([^@\s<>,;]+@[^@\s<>,;]+\.[A-Za-z]{2,})>?$")


class Refused(Exception):
    """Nothing is sent, on purpose (exit 2)."""


class Failed(Exception):
    """Nothing could be sent (exit 1)."""


def retry_line(run: str) -> str:
    return f"python -m {MODULE} --run {run}"


def delivery_kind(run: str) -> tuple[str | None, bool]:
    """(the kind of this load's e-mail, whether its brief is made): the kind its manifest names, else what the
    deliver command will make of the load's closing row (scripts/ops/deliver.py kind_of: full, look, failed or
    none). The kind is None when the manifest cannot be read."""
    manifest = find_manifest(run)
    if manifest is not None:
        try:
            return str(json.loads(manifest.read_text(encoding="utf-8")).get("kind") or ""), True
        except (OSError, ValueError, AttributeError):
            return None, True
    closing = [r for r in loadplan.journal_read(run) if r.get("event") in ("finished", "failed")]
    if not closing:
        return "none", False
    if closing[-1].get("event") == "finished":
        look = [s for s in (closing[-1].get("needs_look") or []) if s in LOOK_STEPS]
        return ("look" if look else "full"), False
    return ("failed" if closing[-1].get("written") else "none"), False


def brief_refused(run: str) -> str | None:
    """Why the deliver command would refuse to make this load's brief now, for the last load on record, where this
    program can tell (scripts/ops/deliver.py stale_reason, which also asks the database): a later load failed after
    it began to write, or the deliver command refused it already and its 'pack' row says why (the database was
    written after this load, say: that never comes undone). None otherwise."""
    failed = status.failed_load()
    if failed and failed.get("run") != run:
        return (f"No retry: a later load (run {failed.get('run')}) failed after it began to write, so this load's "
                "brief cannot be made now.")
    pack = next((r for r in reversed(loadplan.delivery_read(run)) if r.get("event") == "pack"), None)
    if pack and pack.get("kind") == "refused":
        why = str(pack.get("why") or "").strip().rstrip(".")
        return f"No retry: {why or 'the deliver command refused it'}, so this load's brief cannot be made now."
    return None


def no_retry_needed(run: str) -> str | None:
    """Why no retry of this load's e-mail is offered: where the retry (retry_line) would be refused. Its e-mail is a
    full brief (delivery_kind) and delivery.json is not off (a file the sender refuses counts as on), and either
    state.json's last_load names another run or none, unless only test copies go of a brief already made (a test
    copy is never refused as an older load's; a brief not made yet the deliver command refuses all the same), or
    this is the last load but its brief is not made yet and the deliver command would refuse it (brief_refused).
    None everywhere else, and when a file cannot be read or has the wrong shape: the retry is then offered, as it
    always was. (A load closed as failed never becomes the last load on record, and its note is never refused.)"""
    try:
        last = status.read_state().get("last_load")
        last = last if isinstance(last, dict) else {}
        other = str(last.get("run") or "")
        kind, made = delivery_kind(run)
        if kind != "full":
            return None
        try:
            mode = settings()["mode"]
        except (Failed, TypeError, AttributeError):
            mode = "on"                      # a file it refuses: once it is put right, it sends for real
        if mode == "off":
            return None
        if not made and other == run:        # the retry asks the deliver command for the brief first
            refused = brief_refused(run)
            if refused:
                return refused
        if other == run or (mode == "test" and made):
            return None
        closing = [r for r in loadplan.journal_read(run) if r.get("event") in ("finished", "failed")]
    except (OSError, ValueError, AttributeError, TypeError):
        return None
    if not other:
        return "No retry: no load is on record as the last load, so this load's brief cannot be sent."
    theirs = status.when(last.get("finished_at"))
    mine = status.when(closing[-1].get("at")) if closing else None
    if theirs is not None and mine is not None and theirs < mine:        # this load's own record of itself was lost
        return f"No retry: the last load on record is run {other}, not this one, so this load's brief cannot be sent."
    return f"No retry is needed: a newer load (run {other}) has its own e-mail."


# ── what is set up ───────────────────────────────────────────────────────────────────────────────

def settings() -> dict:
    """delivery.json, checked. No file = mode off."""
    f = paths.ops_dir() / "delivery.json"
    if not f.is_file():
        return {"mode": "off", "why": "business_data/ops/delivery.json does not exist"}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Failed(f"delivery.json cannot be read ({type(exc).__name__})") from None
    if not isinstance(data, dict):
        raise Failed("delivery.json is not an object")
    mode = str(data.get("mode") or "off").strip().lower()
    if mode not in ("on", "test", "off"):
        raise Failed(f"delivery.json: mode must be on, test or off, not {mode!r}")
    out = {"mode": mode, "from": str(data.get("from") or "").strip(), "reply_to": str(data.get("reply_to") or "").strip()}
    for key in ("to", "test_to"):
        raw = data.get(key) or []
        raw = [raw] if isinstance(raw, str) else raw
        out[key] = [str(a).strip() for a in raw if str(a).strip()]
        bad = [a for a in out[key] if not _ADDRESS.match(a)]
        if bad:
            raise Failed(f"delivery.json: '{key}' holds something that is not an e-mail address")
    if mode != "off":
        if not _SENDER.match(out["from"]):
            raise Failed("delivery.json: 'from' must be an address on the sending domain")
        if not out["to"] and mode == "on":
            raise Failed("delivery.json: 'to' names nobody")
    return out


def digest(addresses: list[str]) -> str:
    return hashlib.sha256(",".join(sorted(a.lower() for a in addresses)).encode("utf-8")).hexdigest()[:16]


# ── the delivery to send ─────────────────────────────────────────────────────────────────────────

def find_manifest(run: str) -> Path | None:
    """The manifest the deliver command wrote for this run (never a preview's)."""
    for row in reversed(loadplan.delivery_read(run)):
        if row.get("event") == "pack" and row.get("manifest"):
            p = Path(str(row["manifest"]))
            if p.is_file():
                return p
    base = paths.exports_dir()
    hits = sorted(base.glob(f"*/delivery/{run}/manifest.json")) if base.is_dir() else []
    return hits[-1] if hits else None


def prepare(run: str, env: dict | None = None) -> None:
    """The deliver command for this run, when it did not run after the load. It records like a brief."""
    cmd = [sys.executable, "-X", "utf8", "-m", "scripts.ops", "deliver", "--run", run]
    child = subprocess.run(cmd, cwd=str(ROOT), env=dict(env if env is not None else os.environ), shell=False,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=DELIVER_TIMEOUT_S)
    if child.returncode != 0:
        last = (child.stdout or child.stderr or "").strip().splitlines()[-1:] or ["no output"]
        if child.returncode == 2:            # the deliver command refused: the database no longer holds this load
            raise Refused(f"the brief for this load is not made now ({last[0][:200]})")
        raise Failed(f"the brief for this load could not be prepared ({last[0][:120]})")


def read_delivery(manifest: Path) -> tuple[dict, dict]:
    """(the manifest, {html, text}) with each file checked against the sha256 the manifest keeps."""
    try:
        man = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Failed(f"the delivery's manifest cannot be read ({type(exc).__name__})") from None
    files = {}
    for key in ("html", "text"):
        name = (man.get("files") or {}).get(key)
        if not name:
            raise Failed(f"the delivery has no {key} part")
        try:
            body = (manifest.parent / name).read_text(encoding="utf-8")
        except OSError as exc:
            raise Failed(f"the delivery's {name} cannot be read ({type(exc).__name__})") from None
        if hashlib.sha256(body.encode("utf-8")).hexdigest() != (man.get("sha256") or {}).get(key):
            raise Failed(f"the delivery's {name} is not the file the brief wrote (it changed since)")
        files[key] = body
    return man, files


# ── the one request ──────────────────────────────────────────────────────────────────────────────

def post(key: str, body: dict, idem: str, opener=None) -> dict:
    """POST to Resend. Returns its answer ({id}); raises Failed with the status and its message (never
    the key). One retry on a server error or a timeout, with the same Idempotency-Key."""
    data = json.dumps(body).encode("utf-8")
    opener = opener or urllib.request.urlopen
    last = "no answer"
    for attempt in (1, 2):
        req = urllib.request.Request(API, data=data, method="POST", headers={
            "Authorization": "Bearer " + key, "Content-Type": "application/json",
            "Idempotency-Key": idem, "User-Agent": "yq-send/1"})
        try:
            with opener(req, timeout=TIMEOUT_S) as resp:
                answer = json.loads(resp.read().decode("utf-8") or "{}")
                return answer if isinstance(answer, dict) else {}
        except urllib.error.HTTPError as exc:
            try:
                said = json.loads(exc.read().decode("utf-8") or "{}").get("message") or ""
            except (ValueError, OSError, AttributeError):
                said = ""
            last = f"HTTP {exc.code}" + (f": {str(said)[:160]}" if said else "")
            if exc.code < 500:
                break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = f"no connection ({type(exc).__name__})"
        if attempt == 1:
            time.sleep(3)
    raise Failed(f"the mail service did not take it: {last}")


# ── the command ──────────────────────────────────────────────────────────────────────────────────

def record(run: str, event: str, **fields) -> bool:
    """One row of the run's delivery journal, tried again for about 20 s (the folder syncs through
    OneDrive), and state.json's last_delivery. -> whether the row was written: a 'sent' row that is lost
    would let a later send go out twice."""
    written = False
    for attempt in range(12):
        try:
            loadplan.delivery_append(run, event, **fields)
            written = True
            break
        except OSError:
            time.sleep(0.25 * (attempt + 1))
    last = {"run": run, "event": event, "at": datetime.now(status.BAHRAIN).isoformat(timespec="seconds"),
            "kind": fields.get("kind")}
    if fields.get("same_as"):                # skipped: the data set's e-mail went with that load
        last.update(same_as=fields["same_as"], reason=fields.get("reason"))
    try:
        status.write_state({"last_delivery": last})
    except OSError:
        pass
    return written


def already_sent(run: str, kind: str) -> dict | None:
    """The last real send of this load's e-mail of this kind, to ANY list of addresses: one more address in
    delivery.json must not send it to everybody again without the box."""
    for row in reversed(loadplan.delivery_read(run)):
        if row.get("event") == "sent" and row.get("kind") == kind:
            return row
    return None


# ── one full e-mail per data set ─────────────────────────────────────────────────────────────────

def data_set(run: str) -> dict | None:
    """What a load put in the database, as its check recorded it in plan.json: the data date; the size and sha256
    of each Focus report it took (a line of the table with a staged copy) as the report lay in the drop folder
    (token_parts.drop: never a staged copy, which the check makes anew for some reports, such as the stock balance
    trimmed to the main store); the figures the check expected (expected, less its lines of words); the check's
    options (a price book loaded or left out, the profitability report loaded anyway, a replace or a partial on
    purpose); and the programs that loaded it (token_parts.head). The same files loaded by other programs or with
    other options write other figures, so they are other data. A report the load did not take is no part of it.
    None when the plan does not say (no plan, a check with no token, nothing taken)."""
    try:
        plan = loadplan.read_plan(run)
    except (OSError, ValueError):
        return None
    parts = plan.get("token_parts") if isinstance(plan, dict) else None
    drop = parts.get("drop") if isinstance(parts, dict) else None
    if not isinstance(drop, dict):
        return None
    taken = []
    for line in plan.get("verdicts") or []:
        meta = drop.get(str(line.get("file") or "")) if isinstance(line, dict) and line.get("staged") else None
        if isinstance(meta, dict) and meta.get("sha256"):
            taken.append(f"{meta['sha256']} {meta.get('size')}")
    if not taken:
        return None
    expected = plan.get("expected")
    if isinstance(expected, dict):
        expected = {k: v for k, v in expected.items() if k != "lines"}
    from scripts.yq_load_go import option_flags      # the options as the box reads them ('skip' is no option)
    return {"data_date": str(plan.get("data_date") or ""), "reports": sorted(taken), "expected": expected,
            "options": sorted(option_flags(plan)), "programs": str(parts.get("head") or "")}


def same_programs(a: str, b: str, root: Path | None = None) -> bool:
    """Did two checks load with the same engine: the same commit, or two commits whose engine files do not differ
    (scripts/ops/gitinfo.py ENGINE_PATHS: the loader, the app, the requirements; a commit of docs, of the skills'
    modules, of the web pages or of the tests changes nothing that is loaded)? Where git cannot tell (a commit it
    does not know, a head with changes not committed) they count as different: the e-mail then goes."""
    if a == b:
        return True
    if not (_COMMIT.fullmatch(a or "") and _COMMIT.fullmatch(b or "")):
        return False
    try:
        cp = subprocess.run(["git", "-C", str(root or GIT_ROOT), "diff", "--quiet", a, b, "--", *gitinfo.ENGINE_PATHS],
                            shell=False, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return cp.returncode == 0


def _in_time_order(run: str) -> str:
    """ddmmyy_HHMM as yymmddHHMM: run names in the order of their days and minutes."""
    return run[4:6] + run[2:4] + run[:2] + run[7:]


def last_sent(but: str) -> dict | None:
    """The newest e-mail that went for real, of any load but `but`: a 'sent' row of any kind (the full brief or a
    note; a test copy is 'test_sent' and never counts) in the loads' own delivery journals, the newest by its
    time, then by its run's day and minute, then by its place in the journal (two sends in one second): its row,
    with its run in it. A journal that cannot be read (held by another program, not UTF-8) is passed over: an old
    broken file never stops a send."""
    base = paths.loads_dir()
    try:
        folders = list(base.iterdir()) if base.is_dir() else []
    except OSError:
        folders = []
    best, best_key = None, None
    for folder in folders:
        run = folder.name
        if run == but or not loadplan.is_run(run) or run.startswith(status.REHEARSAL_PREFIX):
            continue
        try:
            rows = loadplan.delivery_read(run)
        except (OSError, ValueError):
            continue
        for i, row in enumerate(rows):
            at = status.when(row.get("at")) if row.get("event") == "sent" else None
            if at is not None and (best_key is None or (at, _in_time_order(run), i) > best_key):
                best, best_key = dict(row, run=run), (at, _in_time_order(run), i)
    return best


def same_data_sent(run: str) -> dict | None:
    """The newest e-mail that went for real (last_sent), when it is a full brief of the very data of this load:
    this load's e-mail would only repeat it. None when the newest is a note (a load that failed after it began
    to write, say: the e-mail after it must say what the database holds now), when it carried other data, when
    none went, or when either data set cannot be told (the e-mail then goes, as it always did)."""
    last = last_sent(run)
    if last is None or last.get("kind") != "full":
        return None
    mine, theirs = data_set(run), data_set(str(last["run"]))
    if mine is None or theirs is None:
        return None
    programs = same_programs(mine.pop("programs"), theirs.pop("programs"))
    return last if mine == theirs and programs else None


def went_at(row: dict) -> str:
    """When the e-mail of a row went, on the Bahrain clock ('15 Oct at 09:10')."""
    t = status.when(row.get("at"))
    return t.astimezone(status.BAHRAIN).strftime("%d %b at %H:%M") if t else str(row.get("at") or "")[:16]


def confirm_resend(run: str, row: dict, box=None) -> bool:
    """The same Windows box as the load (its rule: a Yes in the first seconds does not count). `row` is the
    'sent' row of this load's own e-mail, or that of the load whose full e-mail carried the same data (its
    run is in the row: same_data_sent)."""
    if box is None:
        from scripts.yq_load_go import windows_box as box
    at = str(row.get("at") or "")[:16].replace("T", " ")
    other = str(row.get("run") or run)
    if other != run:
        text = (f"Load {run} holds the same data as load {other}, whose brief was e-mailed already ({at} UTC).\n\n"
                f"Send the brief of load {run} all the same?\n\nYes = send it. No = nothing is sent.")
    else:
        text = (f"The brief of load {run} was e-mailed already ({at} UTC).\n\nSend it once more, to the same "
                "address(es)?\n\nYes = send again. No = nothing is sent.")
    return box(f"YQ e-mail {run[:6]}", text, 120) == "yes"


def send(run: str, *, test: bool = False, resend: bool = False, auto: bool = False, key_reader=None,
         opener=None, box=None, env: dict | None = None, say=print) -> int:
    conf = settings()
    mode = conf["mode"]
    if mode == "off":
        record(run, "not_sent", reason="off", text="Delivery is off: nothing was sent.")
        say(f"Delivery: off ({conf.get('why') or 'mode off in delivery.json'}): nothing was sent.")
        return EXIT_OK
    test = test or mode == "test"
    manifest = find_manifest(run)
    if manifest is None:
        prepare(run, env)
        manifest = find_manifest(run)
        if manifest is None:
            raise Failed("the brief for this load was not prepared")
    man, files = read_delivery(manifest)
    kind = str(man.get("kind") or "")
    if kind not in ("full", "look", "failed"):
        record(run, "not_sent", kind=kind or None, text="Nothing to send for this load.")
        say(f"Delivery: nothing to send for run {run} (the load wrote nothing).")
        return EXIT_OK
    last_run = str((status.read_state().get("last_load") or {}).get("run") or "")
    if kind == "full" and last_run != run and not (test or resend):
        raise Refused(f"the last load on record is run {last_run or '(none)'}, not this one: the brief to send is "
                      "that load's own")
    to = conf["test_to"] if test else conf["to"]
    if not to:
        raise Failed("delivery.json names no " + ("test address" if test else "recipient"))
    to_digest = digest(to)
    sent_before = None if test else already_sent(run, kind)
    if sent_before and not resend:
        raise Refused(f"the e-mail of run {run} went already ({str(sent_before.get('at'))[:16]} UTC); to send it once "
                      f"more: {retry_line(run)} --resend")
    # one full e-mail per data set: a load of the very data whose full e-mail went already sends nothing, nor its
    # note that a check needs a look. A failed load's note always goes: the database may hold part of that load.
    # (A test copy never counts, and is never held back.)
    same = None if (test or sent_before or kind == "failed") else same_data_sent(run)
    if same and not resend:
        other = str(same["run"])
        record(run, "not_sent", kind=kind, reason=f"skipped: same data as run {other}", same_as=other,
               text=f"Skipped: same data as run {other}, whose e-mail went on {went_at(same)}: no e-mail was sent.")
        say(f"Delivery: no e-mail was sent: the data matches run {other} (its e-mail went on {went_at(same)}).")
        return EXIT_OK
    if resend and (sent_before or same) and not confirm_resend(run, sent_before or same, box):
        raise Refused("No at the box: nothing was sent")
    key = (key_reader or yq_secrets.resend_key)()
    if not key:
        raise Failed("no sending key on this PC: set it once with  cmdkey /generic:YQ_RESEND_API_KEY /user:resend "
                     "/pass  (it asks for the key)")
    subject, html, text = str(man.get("subject") or "YQ brief"), files["html"], files["text"]
    if test:
        subject = "[TEST] " + subject
        html = ('<p style="font-family:Arial,sans-serif;font-size:12px;color:#904F0E;">TEST: this copy went to the '
                "test address only.</p>" + html)
        text = "TEST: this copy went to the test address only.\n\n" + text
    event = "test_sent" if test else "sent"
    # the same attempt keeps its key until it is recorded as sent (a retry after a timeout that did deliver
    # is then dropped by the mail service); a test copy in between never changes the key of the real one
    n = sum(1 for r in loadplan.delivery_read(run)
            if r.get("event") == event and r.get("kind") == kind and r.get("to_digest") == to_digest)
    idem = hashlib.sha256(f"{run}|{kind}|{to_digest}|{event}|{n}".encode("utf-8")).hexdigest()
    body = {"from": conf["from"], "to": to, "subject": subject, "html": html, "text": text}
    if conf.get("reply_to"):
        body["reply_to"] = conf["reply_to"]
    answer = post(key, body, idem, opener)
    how = "resend" if resend else "auto" if auto else "by hand"
    kept = record(run, event, kind=kind, to_count=len(to), to_digest=to_digest, id=str(answer.get("id") or "") or None,
                  how=how, subject=subject[:160], text=f"The {kind} e-mail went to {len(to)} address(es).")
    warn = "" if kept else " (its record could not be written: do not send it again by hand)"
    say(f"Delivery: {'TEST ' if test else ''}e-mail sent to {len(to)} address(es) ({kind}, {how}): {subject}{warn}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog=f"python -m {MODULE}", description=__doc__.split("\n\n")[0])
    ap.add_argument("--run", required=True, help="the load (ddmmyy_HHMM)")
    ap.add_argument("--auto", action="store_true", help="the load's worker sends it (recorded as such)")
    ap.add_argument("--test", action="store_true", help="to the test address only, marked [TEST]")
    ap.add_argument("--resend", action="store_true", help="once more, after a Yes at the Windows box")
    args = ap.parse_args(argv)
    run = args.run
    if not loadplan.is_run(run):
        print(f"'{run}' is not a run name (ddmmyy_HHMM). Nothing was sent.")
        return EXIT_REFUSED
    try:
        return send(run, test=args.test, resend=args.resend, auto=args.auto)
    except Refused as exc:
        record(run, "not_sent", reason=str(exc)[:160], text="Not sent: " + str(exc)[:150])
        print(f"Delivery: NOT sent: {exc}.")
        return EXIT_REFUSED
    except Failed as exc:
        no_retry = no_retry_needed(run)      # a retry that would itself be refused is never offered
        record(run, "failed", why=str(exc)[:200], text="The e-mail could not be sent.",
               next=no_retry or f"Its retry: {retry_line(run)}")
        print(f"Delivery: NOT sent: {exc}. Nothing was reloaded. " + (no_retry or f"Its retry: {retry_line(run)}"))
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001  (recorded, whatever it was; the key is in no message)
        no_retry = no_retry_needed(run)
        record(run, "failed", why=f"{type(exc).__name__}", text="The e-mail could not be sent.",
               next=no_retry or f"Its retry: {retry_line(run)}")
        print(f"Delivery: NOT sent: an unexpected error ({type(exc).__name__}). Nothing was reloaded. "
              + (no_retry or f"Its retry: {retry_line(run)}"))
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
