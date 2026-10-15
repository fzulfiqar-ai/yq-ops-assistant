"""Whether `load status` offers the retry of a load's cut-off e-mail (scripts/ops/load_check.py, run_status).

The sender (scripts/yq_send.py, another program: no module of scripts/ops imports it) refuses to send an
older load's full brief. Offering its retry line there would only lead to that refusal, so the status says
why there is none, in the sender's own words. This is the sender's no_retry_needed, read from the same files.
No retry is offered for run R when its e-mail is a full brief (the kind its manifest names, or, with no
manifest yet, what the deliver command makes of R's closing row: finished, and neither reconcile nor
post_gate needs a look) and delivery.json sends (its mode is not off; a file the sender refuses counts as
on: once it is put right, it sends for real), and either
  1. state.json's last_load names another run than R, or none, unless only test copies go of a brief
     already made (a test copy is never refused as an older load's); or
  2. R is the last load on record, its brief is not made yet, and the deliver command would refuse to
     make it now (brief_refused): a later load failed after it began to write, or R's last 'pack' row
     says the deliver command refused it already, and why.
Where a file cannot be read, the retry is offered, as it always was (a journal that is not text makes the
failed load "could not tell", and that offers the retry too). tests/test_ops_load.py runs this rule and the
sender's side by side, so the two cannot drift apart unseen."""
from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.ops import loadplan, paths, status

LOOK_STEPS = ("reconcile", "post_gate")      # scripts/ops/deliver.py LOOK_STEPS: these needing a look make a note
# the sender's own checks of delivery.json (its settings): a file they refuse counts as on
_ADDRESS = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[A-Za-z]{2,}$")
_SENDER = re.compile(r"^(?:[^<>@]*<)?([^@\s<>,;]+@[^@\s<>,;]+\.[A-Za-z]{2,})>?$")


def delivery_mode() -> str:
    """off | test | on, as the sender reads business_data/ops/delivery.json: no file is off; a file the sender
    would refuse (unreadable, not an object, a mode it does not know, something that is no e-mail address,
    no sender address, nobody to send to) counts as on."""
    f = paths.ops_dir() / "delivery.json"
    if not f.is_file():
        return "off"
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return "on"
        mode = str(data.get("mode") or "off").strip().lower()
        if mode not in ("on", "test", "off"):
            return "on"
        found: dict[str, list[str]] = {}
        for key in ("to", "test_to"):
            raw = data.get(key) or []
            raw = [raw] if isinstance(raw, str) else raw
            found[key] = [str(a).strip() for a in raw if str(a).strip()]
            if any(not _ADDRESS.match(a) for a in found[key]):
                return "on"
        if mode != "off":
            if not _SENDER.match(str(data.get("from") or "").strip()):
                return "on"
            if not found["to"] and mode == "on":
                return "on"
    except (OSError, ValueError, TypeError):
        return "on"
    return mode


def manifest_of(run: str) -> Path | None:
    """The manifest the deliver command wrote for this run (never a preview's), found as the sender finds it."""
    for row in reversed(loadplan.delivery_read(run)):
        if row.get("event") == "pack" and row.get("manifest"):
            p = Path(str(row["manifest"]))
            if p.is_file():
                return p
    base = paths.exports_dir()
    hits = sorted(base.glob(f"*/delivery/{run}/manifest.json")) if base.is_dir() else []
    return hits[-1] if hits else None


def email_kind(run: str) -> tuple[str | None, bool]:
    """(the kind of this load's e-mail, whether its brief is made): the kind its manifest names, else what the
    deliver command makes of the load's closing row (full, look, failed or none). None when the manifest cannot
    be read."""
    manifest = manifest_of(run)
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
    """Why the deliver command would refuse to make this load's brief now, for the last load on record, where it
    can be told from the files: a later load failed after it began to write (status.failed_load, read as the
    sender reads it), or the deliver command refused it already and its last 'pack' row says why. None otherwise."""
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
    """Why no retry of this load's e-mail is offered, in the sender's words; None where the retry is the line to
    offer, and whenever a file cannot be read."""
    try:
        last = status.read_state().get("last_load")
        last = last if isinstance(last, dict) else {}
        other = str(last.get("run") or "")
        kind, made = email_kind(run)
        if kind != "full":
            return None
        mode = delivery_mode()
        if mode == "off":
            return None
        if not made and other == run:        # the retry would ask the deliver command for the brief first
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
