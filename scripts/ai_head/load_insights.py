"""Load an AI Head review's insights.json into ai_insights -- only after the owner decided (plan §19).

    python -m scripts.ai_head.load_insights exports/ai_head/2026-09-26/insights.json
        dry run (the default): validate the file and show what WOULD load. Writes nothing, connects to nothing.

    python -m scripts.ai_head.load_insights <file> --approve A1,A3 --reject A2,A4,A5 --approve-report \
        --by owner@example.com
        still a dry run: the decisions are applied in memory and shown.

    ... --commit
        writes, in ONE transaction on DATABASE_URL (the owner's session; the ai_head_ro login is read-only
        and cannot). Refuses while any Top-5 action is undecided.

    python -m scripts.ai_head.load_insights <file> --done A1 --by owner@example.com --commit
        later: an approved action was carried out.

Decisions:
  --approve / --reject IDS   the items' "id" values from insights.json (comma-separated)
  --approve-report           every statement that is not a recommendation (facts, analysis, hypotheses)
                             is approved: the owner has read the report and lets management see it
  --done IDS                 approved -> done
  --by EMAIL                 who decided (required with any decision; recorded as decided_by)

Rules (the loader refuses the whole file on any of them):
  * kind weekly | monthly; week_ending a Saturday (weekly) or the month's last day (monthly);
  * tag fact | analysis | recommendation | hypothesis (FACT, LOW-CONFIDENCE HYPOTHESIS ... accepted);
    a hypothesis is always low confidence; confidence high | medium | low;
  * rank 1-5 only on recommendations, each rank once, at most five;
  * section one of the §19 sections (app/ai_insights.SECTIONS); text 1-2000 characters;
  * no phone number or email address anywhere in text, action or evidence.
A statement already in the table (same kind, week, section, tag and text: insight_key) is never
rewritten; only its status can move (proposed -> approved | rejected, approved -> done), which the
table's trigger enforces as well.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from app.ai_insights import SECTIONS, STATUSES, has_contact  # noqa: E402

TAG_ALIASES = {
    "fact": "fact", "analysis": "analysis", "recommendation": "recommendation", "hypothesis": "hypothesis",
    "low-confidence hypothesis": "hypothesis", "low confidence hypothesis": "hypothesis",
}
CONFIDENCES = ("high", "medium", "low")
TRANSITIONS = {("proposed", "approved"), ("proposed", "rejected"), ("approved", "done")}


class InsightError(ValueError):
    pass


def insight_key(kind: str, week_ending: str, section: str, tag: str, text: str) -> str:
    return hashlib.sha256(f"{kind}|{week_ending}|{section}|{tag}|{text.strip()}".encode("utf-8")).hexdigest()


def _ids(s: str | None) -> set[str]:
    return {x.strip() for x in (s or "").split(",") if x.strip()}


def validate(doc: dict) -> dict:
    """The file, normalised (tags lower-case, keys computed) or InsightError naming every problem."""
    errs: list[str] = []
    kind = str(doc.get("kind") or "weekly").strip().lower()
    if kind not in ("weekly", "monthly"):
        errs.append(f"kind must be weekly or monthly, not {kind!r}")
    try:
        we = date.fromisoformat(str(doc.get("week_ending") or "")[:10])
    except ValueError:
        raise InsightError("week_ending must be a date (YYYY-MM-DD)") from None
    if kind == "weekly" and we.weekday() != 5:
        errs.append(f"week_ending {we} is not a Saturday (weeks run Sunday-Saturday)")
    if kind == "monthly" and (we + timedelta(days=1)).day != 1:
        errs.append(f"week_ending {we} is not the last day of a month")
    data_as_of = doc.get("data_as_of")
    if data_as_of:
        try:
            data_as_of = date.fromisoformat(str(data_as_of)[:10]).isoformat()
        except ValueError:
            errs.append("data_as_of must be a date")
    items = doc.get("items")
    if not isinstance(items, list) or not items:
        raise InsightError("insights.json has no items")
    seen_ids, seen_ranks, seen_keys, out = set(), set(), set(), []
    for n, it in enumerate(items, 1):
        where = f"item {n} ({it.get('id') or 'no id'})"
        iid = str(it.get("id") or "").strip()
        if not iid:
            errs.append(f"{where}: needs an id")
        elif iid in seen_ids:
            errs.append(f"{where}: duplicate id")
        seen_ids.add(iid)
        tag = TAG_ALIASES.get(str(it.get("tag") or "").strip().lower())
        if not tag:
            errs.append(f"{where}: tag must be FACT, ANALYSIS, RECOMMENDATION or LOW-CONFIDENCE HYPOTHESIS")
        conf = str(it.get("confidence") or "").strip().lower() or None
        if conf not in CONFIDENCES:
            errs.append(f"{where}: confidence must be high, medium or low")
        if tag == "hypothesis" and conf != "low":
            errs.append(f"{where}: a hypothesis is low confidence by definition")
        section = str(it.get("section") or "").strip()
        if section not in SECTIONS:
            errs.append(f"{where}: section must be one of {', '.join(SECTIONS)}")
        text = str(it.get("text") or "").strip()
        if not 1 <= len(text) <= 2000:
            errs.append(f"{where}: text must be 1-2000 characters")
        action = (str(it.get("action")).strip() or None) if it.get("action") is not None else None
        rank = it.get("rank")
        if rank is not None:
            if tag != "recommendation":
                errs.append(f"{where}: only a recommendation takes a rank")
            elif not isinstance(rank, int) or not 1 <= rank <= 5:
                errs.append(f"{where}: rank must be 1-5")
            elif rank in seen_ranks:
                errs.append(f"{where}: rank {rank} used twice")
            seen_ranks.add(rank)
        status = str(it.get("status") or "proposed").strip().lower()
        if status not in STATUSES:
            errs.append(f"{where}: status must be one of {', '.join(STATUSES)}")
        evidence = it.get("evidence")
        for label, val in (("text", text), ("action", action), ("evidence", json.dumps(evidence, ensure_ascii=False))):
            if has_contact(val):
                errs.append(f"{where}: {label} carries a phone number or email address -- remove it")
        key = insight_key(kind, we.isoformat(), section, tag or "", text)
        if key in seen_keys:
            errs.append(f"{where}: the same statement appears twice")
        seen_keys.add(key)
        out.append({"id": iid, "section": section, "tag": tag, "text": text, "evidence": evidence,
                    "confidence": conf, "action": action, "rank": rank, "status": status,
                    "decided_by": it.get("decided_by"), "insight_key": key})
    if errs:
        raise InsightError("insights.json refused:\n  - " + "\n  - ".join(errs))
    return {"kind": kind, "week_ending": we.isoformat(), "data_as_of": data_as_of, "items": out}


def decide(doc: dict, *, approve: set[str] = frozenset(), reject: set[str] = frozenset(),
           done: set[str] = frozenset(), approve_report: bool = False, by: str | None = None) -> dict:
    """Apply the owner's decisions in memory. Unknown ids, a double decision and a decision without
    --by are refused."""
    ids = {i["id"] for i in doc["items"]}
    unknown = (set(approve) | set(reject) | set(done)) - ids
    if unknown:
        raise InsightError("unknown id(s): " + ", ".join(sorted(unknown)))
    both = set(approve) & set(reject)
    if both:
        raise InsightError("approved and rejected at once: " + ", ".join(sorted(both)))
    if (approve or reject or done or approve_report) and not (by and "@" in by):
        raise InsightError("--by <email> is required with a decision")
    for it in doc["items"]:
        if it["id"] in approve:
            it["status"] = "approved"
        elif it["id"] in reject:
            it["status"] = "rejected"
        elif approve_report and it["tag"] != "recommendation" and it["status"] == "proposed":
            it["status"] = "approved"
        if it["id"] in done:
            if it["status"] not in ("approved", "done"):
                raise InsightError(f"{it['id']}: only an approved action can be done")
            it["status"] = "done"
        if it["status"] != "proposed":
            it["decided_by"] = it.get("decided_by") or by
    nameless = [i["id"] for i in doc["items"] if i["status"] != "proposed" and not i.get("decided_by")]
    if nameless:
        raise InsightError("--by <email> is required: these carry a decision but no decider: " + ", ".join(nameless))
    return doc


def undecided_top(doc: dict) -> list[str]:
    return [i["id"] for i in doc["items"] if i["tag"] == "recommendation" and i["rank"] and i["status"] == "proposed"]


def commit(conn, doc: dict, source_file: str, actor: str | None) -> dict:
    """One transaction: insert new statements, move the status of known ones along TRANSITIONS.
    Returns the counts. The caller commits."""
    cur = conn.cursor()
    cur.execute("select to_regclass('public.ai_insights') is not null")
    if not cur.fetchone()[0]:
        raise InsightError("ai_insights does not exist yet: apply scripts/r7b_ai_head_migration.sql first")
    inserted = moved = kept = 0
    refused: list[str] = []
    for it in doc["items"]:
        cur.execute("select status from ai_insights where insight_key = %s", (it["insight_key"],))
        row = cur.fetchone()
        if row is None:
            decided = it["status"] != "proposed"
            cur.execute("""
                insert into ai_insights (kind, week_ending, section, tag, text, evidence, confidence, action, rank,
                                         data_as_of, status, decided_by, decided_at, insight_key, source_file, created_by)
                values (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, case when %s then now() end, %s, %s, %s)
                on conflict (insight_key) do nothing""",
                        (doc["kind"], doc["week_ending"], it["section"], it["tag"], it["text"],
                         json.dumps(it["evidence"], ensure_ascii=False) if it["evidence"] is not None else None,
                         it["confidence"], it["action"], it["rank"], doc["data_as_of"], it["status"],
                         it["decided_by"] if decided else None, decided, it["insight_key"], source_file, actor))
            inserted += cur.rowcount
            continue
        have = row[0]
        if have == it["status"]:
            kept += 1
        elif (have, it["status"]) in TRANSITIONS:
            cur.execute("""update ai_insights set status = %s, decided_by = %s, decided_at = now()
                            where insight_key = %s and status = %s""",
                        (it["status"], it["decided_by"], it["insight_key"], have))
            moved += cur.rowcount
        else:
            refused.append(f"{it['id']}: {have} -> {it['status']}")
    if refused:
        raise InsightError("status moves the table does not allow (nothing was written): " + "; ".join(refused))
    return {"inserted": inserted, "status_moved": moved, "unchanged": kept}


def show(doc: dict) -> str:
    lines = [f"{doc['kind']} review, week ending {doc['week_ending']} (Focus data to {doc['data_as_of'] or '?'}):"]
    for it in doc["items"]:
        r = f"#{it['rank']} " if it["rank"] else ""
        lines.append(f"  {it['id']:>5}  {it['status']:9} {it['tag']:14} {it['confidence'] or '':6} "
                     f"{r}{it['section']}: {it['text'][:90]}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", help="exports/ai_head/<date>/insights.json")
    ap.add_argument("--approve", default="")
    ap.add_argument("--reject", default="")
    ap.add_argument("--done", default="")
    ap.add_argument("--approve-report", action="store_true")
    ap.add_argument("--by", default=None, help="the owner's login email (decided_by)")
    ap.add_argument("--commit", action="store_true", help="write to ai_insights (default: dry run)")
    a = ap.parse_args(argv)

    path = Path(a.file)
    try:
        doc = validate(json.loads(path.read_text(encoding="utf-8")))
        doc = decide(doc, approve=_ids(a.approve), reject=_ids(a.reject), done=_ids(a.done),
                     approve_report=a.approve_report, by=a.by)
    except (InsightError, json.JSONDecodeError, OSError) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    print(show(doc))
    open_top = undecided_top(doc)
    if not a.commit:
        print("\nDRY RUN: nothing written." + (f" Still undecided in the Top 5: {', '.join(open_top)}." if open_top else ""))
        return 0
    if open_top:
        print(f"REFUSED: decide every Top-5 action first (--approve / --reject): {', '.join(open_top)}", file=sys.stderr)
        return 2
    url = os.getenv("DATABASE_URL")
    if not url:
        print("REFUSED: DATABASE_URL is not set (.env) -- the owner's session writes ai_insights.", file=sys.stderr)
        return 2
    import psycopg

    try:
        with psycopg.connect(url) as conn:
            conn.execute("set lock_timeout = '2s'")
            conn.execute("set statement_timeout = '30s'")
            res = commit(conn, doc, str(path), a.by)
            conn.commit()
    except InsightError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    print(f"\nCOMMITTED: {res['inserted']} new, {res['status_moved']} status change(s), {res['unchanged']} unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
