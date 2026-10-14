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

Every outcome is a row of that journal and state.json's last_delivery. Exit codes: 0 sent, or nothing to do
(off, nothing to deliver); 1 error (recorded; nothing is reloaded, and the line that retries it is printed);
2 refused (a copy went already, a newer load exists, No at the box).
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
from scripts.ops import loadplan, paths, status  # noqa: E402  (none of them opens a connection)

API = "https://api.resend.com/emails"
TIMEOUT_S = 30
DELIVER_TIMEOUT_S = 300
MODULE = "scripts.yq_send"
EXIT_OK, EXIT_ERROR, EXIT_REFUSED = 0, 1, 2
_ADDRESS = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[A-Za-z]{2,}$")
_SENDER = re.compile(r"^(?:[^<>@]*<)?([^@\s<>,;]+@[^@\s<>,;]+\.[A-Za-z]{2,})>?$")


class Refused(Exception):
    """Nothing is sent, on purpose (exit 2)."""


class Failed(Exception):
    """Nothing could be sent (exit 1)."""


def retry_line(run: str) -> str:
    return f"python -m {MODULE} --run {run}"


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

def record(run: str, event: str, **fields) -> None:
    try:
        loadplan.delivery_append(run, event, **fields)
    except OSError:
        pass
    try:
        status.write_state({"last_delivery": {"run": run, "event": event,
                                              "at": datetime.now(status.BAHRAIN).isoformat(timespec="seconds"),
                                              "kind": fields.get("kind")}})
    except OSError:
        pass


def already_sent(run: str, kind: str, to_digest: str) -> dict | None:
    for row in reversed(loadplan.delivery_read(run)):
        if row.get("event") == "sent" and row.get("kind") == kind and row.get("to_digest") == to_digest:
            return row
    return None


def confirm_resend(run: str, row: dict, box=None) -> bool:
    """The same Windows box as the load (its rule: a Yes in the first seconds does not count)."""
    if box is None:
        from scripts.yq_load_go import windows_box as box
    at = str(row.get("at") or "")[:16].replace("T", " ")
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
    if kind == "full" and last_run and last_run != run and not (test or resend):
        raise Refused(f"a newer load (run {last_run}) is on record: its own brief is the one to send")
    to = conf["test_to"] if test else conf["to"]
    if not to:
        raise Failed("delivery.json names no " + ("test address" if test else "recipient"))
    to_digest = digest(to)
    sent_before = None if test else already_sent(run, kind, to_digest)
    if sent_before and not resend:
        raise Refused(f"the e-mail of run {run} went already ({str(sent_before.get('at'))[:16]} UTC); to send it once "
                      f"more: {retry_line(run)} --resend")
    if resend and sent_before and not confirm_resend(run, sent_before, box):
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
    n = sum(1 for r in loadplan.delivery_read(run) if r.get("event") in ("sent", "test_sent"))
    idem = hashlib.sha256(f"{run}|{kind}|{to_digest}|{'test' if test else 'send'}|{n}".encode("utf-8")).hexdigest()
    body = {"from": conf["from"], "to": to, "subject": subject, "html": html, "text": text}
    if conf.get("reply_to"):
        body["reply_to"] = conf["reply_to"]
    answer = post(key, body, idem, opener)
    event = "test_sent" if test else "sent"
    how = "resend" if resend else "auto" if auto else "by hand"
    record(run, event, kind=kind, to_count=len(to), to_digest=to_digest, id=str(answer.get("id") or "") or None,
           how=how, subject=subject[:160], text=f"The {kind} e-mail went to {len(to)} address(es).")
    say(f"Delivery: {'TEST ' if test else ''}e-mail sent to {len(to)} address(es) ({kind}, {how}): {subject}")
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
        record(run, "failed", why=str(exc)[:200], text="The e-mail could not be sent.",
               next=f"Its retry: {retry_line(run)}")
        print(f"Delivery: NOT sent: {exc}. Nothing was reloaded. Its retry: {retry_line(run)}")
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001  (recorded, whatever it was; the key is in no message)
        record(run, "failed", why=f"{type(exc).__name__}", text="The e-mail could not be sent.",
               next=f"Its retry: {retry_line(run)}")
        print(f"Delivery: NOT sent: an unexpected error ({type(exc).__name__}). Nothing was reloaded. "
              f"Its retry: {retry_line(run)}")
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
