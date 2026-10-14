"""python -m scripts.ops deliver [--run <run>] [--preview]

After a load (the load's worker runs it once the load has closed: scripts/yq_load_go.py):
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
register, not the state, not the delivery journal. Nothing is ever sent from here: scripts/yq_send.py
sends, and only what this command wrote (it never works a figure out itself).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from scripts.ops import db  # noqa: F401  (first: it blanks the side channels before any app import)
from scripts.ops import actions as actions_mod
from scripts.ops import fmt, loadplan, paths, render, status

COMMAND = "deliver"
LOOK_STEPS = ("reconcile", "post_gate")      # a load whose check of these needs a look sends no figure


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

    files: dict[str, str] = {}
    if kind == "full":
        from scripts.ops import sales            # the whole brief engine: loaded only for a full delivery
        screen = sales.full_screen(record=not preview, enrich=True)
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
        out["warnings"] = list(screen["warnings"] or [])
    if not preview:
        loadplan.delivery_append(name, "pack", kind=kind, manifest=str(mpath), subject=subject,
                                 team_text=not problems, text=f"The {kind} delivery was prepared.")
    lines.append(f"Files: {folder}")
    return dict(out, text="\n".join(lines), flags={"kind": kind, "team_text": not problems})
