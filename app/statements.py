"""Kickback statements (release R3a, 24-Sep-2026): a rep's month frozen on a stated basis.

Rows live in salesman_kickback_statements (scripts/kickback_statements_migration.sql, M8). A
statement is written ONCE and then only moves forward through its statuses:

    draft ──► approved ──► paid
      │           │
      └───────────┴──► superseded        (a corrected month is a NEW draft; the old row is marked)
    snapshot                             (a documented moment, never paid, never moved)

Amounts are never edited: approve / paid / supersede touch only the status and the who/when
columns, every move is a compare-and-swap on the status the caller saw (two admins clicking at
once cannot both "approve"), and every move writes an audit_log row. The figures themselves come
from build_rows(), which is exactly what the rep's Today card computes (app.shop.rep_month_sales +
tier_progress on the owner's ex-VAT basis), so a statement can never disagree with the screen.

One month is paid once (re-review fixes, 24-Sep-2026):
  * a rep, month and basis can hold ONE approved-or-paid row. transition() refuses to approve a
    draft while one exists and names it, so the admin supersedes it deliberately first; the
    partial unique index in scripts/r3_statements_migration.sql backs that up under a race.
  * a month is approved only once it has ended in Bahrain (the running month can be drafted as a
    documented moment, never approved) and only months with loaded sales can be drafted at all.
  * create_draft() compares FIGURES (sales, tier, kickback), not data dates: a rep whose open
    draft / approved / paid row already carries the current figures is skipped and stale open
    drafts are retired, so a later data day never spawns a second copy of the same month.
  * data_through is capped at the last day of the period, so an August statement made in
    September says "data to 31 Aug", not the September load date.

Owner decisions in force: basis = ex-VAT (net_bhd), net of Focus Sales Returns only once a Sales
Return register is loaded (returns_bhd stays NULL until then — never estimated), the whole month at
the reached tier. Money is Decimal, 3 dp, ROUND_HALF_UP; JSON carries 3-dp strings.

Every read tolerates the table not existing yet (the API may deploy before M8 on a fresh
database): it logs and answers "not available" instead of a 500. The optional columns added by
scripts/r3_statements_migration.sql (paid_by, superseded_*) are written when present and dropped
from the payload when PostgREST says they are not there yet.
"""
from __future__ import annotations

import calendar
import json
import logging
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from app.database import get_client

log = logging.getLogger(__name__)

TABLE = "salesman_kickback_statements"
BASES = ("net_ex_vat", "vat_incl_display")
STATUSES = ("snapshot", "draft", "approved", "paid", "superseded")
CHAIN = ("draft", "approved", "paid")          # the payable chain; snapshots sit outside it
CLOSED = ("approved", "paid")                  # money that is final
# Forward-only. A key that is missing or maps to () is terminal.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "draft": ("approved", "superseded"),
    "approved": ("paid", "superseded"),
    "paid": (),
    "snapshot": (),
    "superseded": (),
}
CONFLICT_MSG = "This statement changed a moment ago — refresh and try again"
# Columns that exist only after scripts/r3_statements_migration.sql. Writes naming them are
# retried without them when PostgREST answers "column not found" (PGRST204 / 42703).
OPTIONAL_COLS = ("paid_by", "superseded_at", "superseded_by", "superseded_reason", "superseded_by_id")
_MISSING_COLUMN_CODES = ("42703", "PGRST204")

_Q3 = Decimal("0.001")


class StatementError(ValueError):
    """An admin-facing problem (HTTP 400 at the edge)."""


class StatementConflict(StatementError):
    """A refusal caused by another row's state — a lost compare-and-swap, an approved or paid
    statement already in place, a duplicate written concurrently. HTTP 409 at the edge: the
    admin refreshes and decides, nothing is retried blindly."""


# ── money ─────────────────────────────────────────────────────────────────────

def d3(x) -> Decimal:
    """Decimal, 3 dp, ROUND_HALF_UP. None / '' / junk → 0.000."""
    if x is None or x == "":
        return Decimal("0.000")
    try:
        return Decimal(str(x)).quantize(_Q3, rounding=ROUND_HALF_UP)
    except Exception:  # noqa: BLE001
        return Decimal("0.000")


def s3(x) -> str:
    """The JSON form of a BHD amount: a 3-dp string, never a float."""
    return format(d3(x), "f")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _missing_column(e: Exception) -> bool:
    code = str(getattr(e, "code", "") or "")
    if code in _MISSING_COLUMN_CODES:
        return True
    msg = str(e)
    return any(c in msg for c in _MISSING_COLUMN_CODES) or ("column" in msg and "does not exist" in msg)


def _missing_table(e: Exception) -> bool:
    code = str(getattr(e, "code", "") or "")
    msg = str(e)
    return code in ("42P01", "PGRST205") or "42P01" in msg or "PGRST205" in msg or \
        ("relation" in msg and "does not exist" in msg) or "Could not find the table" in msg


def _unique_violation(e: Exception) -> bool:
    """Postgres 23505 through PostgREST: the partial unique indexes of r3_statements_migration.sql
    (one approved/paid row per rep-month-basis; one chain row per data date) refused a write."""
    code = str(getattr(e, "code", "") or "")
    msg = str(e)
    return code == "23505" or "23505" in msg or "duplicate key value" in msg


# ── pure rules ────────────────────────────────────────────────────────────────

def can_transition(current: str | None, target: str | None) -> bool:
    """Forward only: draft → approved → paid, draft/approved → superseded; nothing leaves paid,
    snapshot or superseded. Unknown statuses never move."""
    return target in TRANSITIONS.get(str(current or ""), ())


def valid_period(period) -> str:
    """'YYYY-MM' or a StatementError."""
    p = str(period or "").strip()
    if len(p) != 7 or p[4] != "-" or not (p[:4].isdigit() and p[5:].isdigit()) or not 1 <= int(p[5:]) <= 12:
        raise StatementError("Period must be YYYY-MM.")
    return p


def period_end(period: str) -> date:
    """The last calendar day of 'YYYY-MM'."""
    p = valid_period(period)
    y, m = int(p[:4]), int(p[5:7])
    return date(y, m, calendar.monthrange(y, m)[1])


def month_label(period: str) -> str:
    """'2026-09' → 'September 2026' (for refusals an admin reads)."""
    p = valid_period(period)
    return f"{calendar.month_name[int(p[5:7])]} {p[:4]}"


def cap_data_through(data_date, period: str) -> str | None:
    """The data date a statement carries: the latest loaded sale, but never past the period's own
    last day — an August statement frozen from a September load says 'data to 31 Aug'."""
    d = str(data_date or "")[:10]
    if not d:
        return None
    end = period_end(period).isoformat()
    return d if d <= end else end


def month_has_ended(period: str, today: date | None = None) -> bool:
    """True once the whole of `period` is in the past in Bahrain (UTC+3)."""
    if today is None:
        from app import shop
        today = shop.bahrain_today()
    return valid_period(period) < today.strftime("%Y-%m")


def figures(r: dict) -> tuple[Decimal, int, Decimal]:
    """What makes two statements 'the same month': the frozen sales, the tier and the kickback.
    The data date is deliberately NOT part of it — a later load that changes nothing must not
    spawn a second copy of the month."""
    try:
        tier = int(r.get("tier_reached") or 0)
    except (TypeError, ValueError):
        tier = 0
    return (d3(r.get("sales_bhd")), tier, d3(r.get("kickback_bhd")))


def public_row(r: dict) -> dict:
    """A statement as the portal receives it: amounts as 3-dp strings, the target snapshot parsed,
    nothing else changed. (Rows carry rep names — staff, not merchants — and no phone or email.)"""
    out = dict(r)
    for k in ("sales_bhd", "returns_bhd", "kickback_bhd"):
        if out.get(k) is not None:
            out[k] = s3(out[k])
    if out.get("rate") is not None:
        try:
            out["rate"] = float(out["rate"])
        except (TypeError, ValueError):
            pass
    snap = out.get("target_snapshot")
    if isinstance(snap, str):
        try:
            out["target_snapshot"] = json.loads(snap)
        except ValueError:
            pass
    out["next_statuses"] = list(TRANSITIONS.get(str(out.get("status") or ""), ()))
    return out


def summarize(rows: list[dict]) -> dict:
    """Totals per status for a listing (Decimal sums, 3-dp strings out)."""
    tot: dict[str, Decimal] = {}
    n: dict[str, int] = {}
    for r in rows:
        st = str(r.get("status") or "")
        tot[st] = tot.get(st, Decimal("0.000")) + d3(r.get("kickback_bhd"))
        n[st] = n.get(st, 0) + 1
    return {st: {"count": n[st], "kickback_bhd": s3(tot[st])} for st in sorted(n)}


# ── building a month (the same maths as the rep's card) ──────────────────────

def build_rows(period: str, basis: str | None = None) -> list[dict]:
    """One row per rep with a target: the month's Accessories sales on `basis` (giveaways out, SIM
    never), the tier reached and the kickback at that tier's rate on the whole month. Exactly
    app.shop's rep_month_sales / rep_target / tier_progress, so the frozen figure equals the Today
    card at the moment of freezing. Reps whose target row yields no tiers are skipped.

    Refuses a period after the month of the latest loaded sale (there is nothing to freeze), and
    caps data_through at the period's last day."""
    from app import shop
    basis = basis or shop.KICKBACK_BASIS
    if basis not in BASES:
        raise StatementError(f"basis must be one of {BASES}.")
    period = valid_period(period)
    client = get_client()
    targets = client.table("salesman_targets").select("salesman,period").execute().data or []
    salesmen = client.table("salesmen").select("id,focus_name").execute().data or []
    sid = {(s.get("focus_name") or "").strip(): s["id"] for s in salesmen if s.get("focus_name")}
    rows: list[dict] = []
    for name in sorted({str(t["salesman"]) for t in targets if t.get("salesman")}):
        amt, data_date = shop.rep_month_sales(name, basis=basis, period=period)
        latest = str(data_date or "")[:10] or None
        if latest and period > latest[:7]:
            raise StatementError(f"No sales are loaded for {month_label(period)} yet — the latest loaded sale is {latest}.")
        data_through = cap_data_through(latest, period)
        tgt = shop.rep_target(name, period)
        tp = shop.tier_progress(tgt, amt, data_through, basis=basis)
        if not tp:
            continue
        rows.append({
            "salesman": name, "salesman_id": sid.get(name), "period": period, "basis": basis,
            "data_through": data_through, "sales_bhd": s3(tp["mtd_bhd"]),
            "returns_bhd": None,                       # valued only from a Sales Return register
            "tier_reached": int(tp["tier_reached"]), "rate": float(tp["kickback_pct"]),
            "kickback_bhd": s3(tp["kickback_bhd"]), "target_snapshot": tgt,
        })
    return rows


def total_kickback(rows: list[dict]) -> str:
    return s3(sum((d3(r.get("kickback_bhd")) for r in rows), Decimal("0.000")))


# ── reads ─────────────────────────────────────────────────────────────────────

def _select():
    return get_client().table(TABLE).select("*")


def list_statements(period: str | None = None, salesman: str | None = None, limit: int = 300) -> dict:
    """Every statement (newest period first), optionally one period and/or one rep. `available`
    is False when the table is not there yet, so the page can say so instead of erroring."""
    try:
        q = _select()
        if period:
            q = q.eq("period", valid_period(period))
        if salesman:
            q = q.eq("salesman", str(salesman).strip())
        rows = (q.order("period", desc=True).order("salesman").order("id", desc=True)
                .limit(max(1, min(int(limit), 1000))).execute().data or [])
    except StatementError:
        raise
    except Exception as e:  # noqa: BLE001
        if _missing_table(e):
            log.warning("%s missing (apply kickback_statements_migration.sql): %s", TABLE, e)
            return {"statements": [], "available": False, "summary": {}}
        raise
    out = [public_row(r) for r in rows]
    return {"statements": out, "available": True, "summary": summarize(rows)}


def get_statement(statement_id: int) -> dict | None:
    rows = _select().eq("id", int(statement_id)).limit(1).execute().data or []
    return rows[0] if rows else None


def closed_rows(salesman: str, period: str, basis: str, *, exclude_id: int | None = None) -> list[dict]:
    """The approved / paid statements of one rep-month-basis (at most one once the R3 index is in
    place; the code refuses a second one before the database has to)."""
    rows = (_select().eq("salesman", salesman).eq("period", period).eq("basis", basis)
            .in_("status", list(CLOSED)).order("id", desc=True).execute().data or [])
    return [r for r in rows if exclude_id is None or int(r.get("id") or 0) != int(exclude_id)]


def rep_summary(sm: dict | None) -> dict:
    """For /shop/me: `last_closed` = the rep's most recent approved or paid statement (the money
    that is final), `draft` = the newest open draft (the month being closed; `in_progress` when
    that month has not ended yet, so the card says 'so far', not 'awaiting approval'). Keyed by
    the Focus name, like the targets. None / None for an unlinked login or before the table
    exists."""
    out: dict = {"last_closed": None, "draft": None}
    name = str((sm or {}).get("focus_name") or "").strip()
    if not name:
        return out
    try:
        closed = (_select().eq("salesman", name).in_("status", list(CLOSED))
                  .order("period", desc=True).order("id", desc=True).limit(1).execute().data or [])
        draft = (_select().eq("salesman", name).eq("status", "draft")
                 .order("period", desc=True).order("id", desc=True).limit(1).execute().data or [])
    except Exception as e:  # noqa: BLE001 — the card must never break the Today screen
        log.debug("statement summary unavailable for %s: %s", name, e)
        return out
    keep = ("id", "period", "status", "basis", "data_through", "sales_bhd", "returns_bhd", "tier_reached",
            "rate", "kickback_bhd", "approved_at", "paid_at", "created_at")
    for key, rows in (("last_closed", closed), ("draft", draft)):
        if rows:
            r = public_row(rows[0])
            out[key] = {k: r.get(k) for k in keep}
    if out["draft"]:
        try:
            out["draft"]["in_progress"] = not month_has_ended(str(out["draft"].get("period") or ""))
        except StatementError:
            out["draft"]["in_progress"] = False
    return out


# ── writes (insert once; forward-only status moves) ──────────────────────────

def _audit(by: str, event: str, detail: dict) -> None:
    from app.audit import log_event
    log_event(by, event, detail=detail)


def create_draft(period: str, by: str, note: str | None = None, basis: str | None = None) -> dict:
    """Freeze the month as draft rows, one per rep with a target, and return what was created,
    skipped and superseded. Per rep:

      * an open draft / approved / paid row that already carries the current FIGURES (sales,
        tier, kickback — whatever its data date) means nothing has changed: the rep is skipped
        and any other open draft is retired (superseded by that row), so a month never gains a
        second copy just because another day of data was loaded;
      * otherwise the current open drafts are stale and move to superseded (forward only,
        audited), the new draft is inserted, and each retired row is linked to it;
      * an approved / paid row is never touched. When the new figures differ from it the draft is
        still written, with a note: approval of the new draft is refused until the old row is
        superseded by hand. When that row sits on the SAME data date the rep is skipped instead
        (the database allows one chain row per data date), with the same instruction.

    A concurrent duplicate (the partial unique index) is a StatementConflict (409), never a 500."""
    from app import shop
    basis = basis or shop.KICKBACK_BASIS
    period = valid_period(period)
    rows = build_rows(period, basis)
    if not rows:
        raise StatementError("No rep has a target row for this month — import targets first.")
    client = get_client()
    try:
        existing = (client.table(TABLE).select("id,salesman,status,data_through,sales_bhd,tier_reached,kickback_bhd")
                    .eq("period", period).eq("basis", basis).execute().data or [])
    except Exception as e:  # noqa: BLE001
        if _missing_table(e):
            raise StatementError("The statements table is not there yet — apply kickback_statements_migration.sql.") from e
        raise
    by_rep: dict[str, list[dict]] = {}
    for r in existing:
        by_rep.setdefault(str(r.get("salesman")), []).append(r)
    created, skipped, superseded = [], [], []
    when = _now_iso()
    for r in rows:
        name = r["salesman"]
        mine = sorted(by_rep.get(name, []), key=lambda x: int(x.get("id") or 0))
        chain = [x for x in mine if x.get("status") in CHAIN]
        closed = [x for x in chain if x.get("status") in CLOSED]
        open_drafts = [x for x in chain if x.get("status") == "draft"]
        new_dt = str(r["data_through"] or "")
        same = [x for x in chain if figures(x) == figures(r)]
        if same:
            keep = next((x for x in same if x.get("status") in CLOSED), None) or same[-1]
            skipped.append({"salesman": name, "id": keep["id"],
                            "reason": (f"#{keep['id']} ({keep.get('status')}, data to {keep.get('data_through') or '?'}) "
                                       f"already carries these figures")})
            for old in open_drafts:                      # every other open draft is stale now
                if old["id"] != keep["id"]:
                    moved = _move(old["id"], "draft", "superseded", by, when,
                                  reason=f"figures unchanged — #{keep['id']} already carries them",
                                  superseded_by_id=keep["id"])
                    if moved:
                        superseded.append({"salesman": name, "id": old["id"], "by": keep["id"]})
            continue
        blocker = [x for x in closed if str(x.get("data_through") or "") == new_dt]
        if blocker:
            b = blocker[0]
            skipped.append({"salesman": name, "id": b["id"],
                            "reason": (f"#{b['id']} is {b.get('status')} on the same data date with different figures — "
                                       f"supersede #{b['id']} first, then create the draft again")})
            continue
        # the open drafts are stale: retire them BEFORE the insert (one chain row per data date),
        # then link each to the new draft
        retired: list[int] = []
        for old in open_drafts:
            moved = _move(old["id"], "draft", "superseded", by, when,
                          reason=f"replaced by a newer draft (data to {r['data_through']})")
            if moved:
                retired.append(old["id"])
        payload = {**r, "status": "draft", "note": note, "created_by": by,
                   "target_snapshot": r["target_snapshot"] if r["target_snapshot"] is not None else {}}
        try:
            ins = client.table(TABLE).insert(payload).execute().data or []
        except Exception as e:  # noqa: BLE001
            if _unique_violation(e):
                raise StatementConflict(f"{name} · {month_label(period)}: a statement on the same data date was written "
                                        f"a moment ago — refresh and try again") from e
            raise
        new_id = (ins[0] or {}).get("id") if ins else None
        for old_id in retired:
            _link_superseded(old_id, new_id)
            superseded.append({"salesman": name, "id": old_id, "by": new_id})
        created.append({"salesman": name, "id": new_id, "sales_bhd": r["sales_bhd"],
                        "tier_reached": r["tier_reached"], "kickback_bhd": r["kickback_bhd"],
                        "data_through": r["data_through"],
                        # a correction after approval: the admin must supersede the closed row by hand
                        "note": (f"#{closed[-1]['id']} is {closed[-1].get('status')} with different figures — "
                                 f"approval of this draft is refused until #{closed[-1]['id']} is superseded") if closed else None})
    total = total_kickback(created)
    _audit(by, "kickback.statement", {"period": period, "basis": basis, "status": "draft",
                                      "reps": len(created), "skipped": len(skipped), "superseded": len(superseded),
                                      "total_kickback_bhd": total, "note": note})
    return {"period": period, "basis": basis, "created": created, "skipped": skipped, "superseded": superseded,
            "total_kickback_bhd": total}


def _move(statement_id: int, from_status: str, to_status: str, by: str, when: str, *,
          reason: str | None = None, superseded_by_id: int | None = None) -> dict | None:
    """The compare-and-swap: UPDATE … WHERE id = $1 AND status = $from. Only status and the
    who/when columns are named — no amount can travel through here. Returns the row moved, or
    None when the status had already changed underneath (the caller decides how to answer). A
    unique-index refusal (a second approved/paid row written concurrently) is a StatementConflict."""
    fields: dict = {"status": to_status}
    extra: dict = {}
    if to_status == "approved":
        fields.update(approved_by=by, approved_at=when)
    elif to_status == "paid":
        fields["paid_at"] = when
        extra["paid_by"] = by
    elif to_status == "superseded":
        extra.update(superseded_at=when, superseded_by=by, superseded_reason=(reason or None),
                     superseded_by_id=superseded_by_id)
    for k in list(fields):
        assert k not in ("sales_bhd", "returns_bhd", "kickback_bhd", "rate", "tier_reached", "target_snapshot")
    client = get_client()

    def _run(payload: dict):
        return (client.table(TABLE).update(payload).eq("id", int(statement_id)).eq("status", from_status)
                .execute().data or [])
    try:
        try:
            rows = _run({**fields, **{k: v for k, v in extra.items() if v is not None}})
        except Exception as e:  # noqa: BLE001 — the R3 columns are optional until their migration runs
            if extra and _missing_column(e):
                log.info("%s: optional columns not there yet, moving without them (%s)", TABLE, e)
                rows = _run(fields)
            else:
                raise
    except Exception as e:  # noqa: BLE001
        if _unique_violation(e):
            raise StatementConflict("Another approved or paid statement for this rep and month was written a moment "
                                    "ago — refresh and supersede one of them deliberately") from e
        raise
    return rows[0] if rows else None


def _link_superseded(old_id: int, new_id: int | None) -> None:
    """After the replacement draft exists: record which row replaced a retired draft. Only
    superseded_by_id is named, only on a row that is already superseded and unlinked; silently
    skipped until the R3 columns exist."""
    if not new_id:
        return
    try:
        (get_client().table(TABLE).update({"superseded_by_id": int(new_id)}).eq("id", int(old_id))
         .eq("status", "superseded").is_("superseded_by_id", "null").execute())
    except Exception as e:  # noqa: BLE001
        if not _missing_column(e):
            log.warning("could not link superseded statement %s to %s: %s", old_id, new_id, e)


def transition(statement_id: int, to_status: str, by: str, reason: str | None = None) -> dict:
    """Move one statement forward (approve / paid / supersede). Refuses anything that is not a
    forward step, answers CONFLICT_MSG when the row moved under the caller's feet, and refuses to
    approve (a) a month that has not ended in Bahrain and (b) a month that already has an
    approved or paid statement for that rep and basis — the refusal names that row so the admin
    supersedes it deliberately. Audited."""
    if to_status not in STATUSES or to_status in ("draft", "snapshot"):
        raise StatementError(f"Cannot move a statement to '{to_status}'.")
    try:
        row = get_statement(statement_id)
    except Exception as e:  # noqa: BLE001
        if _missing_table(e):
            raise StatementError("The statements table is not there yet — apply kickback_statements_migration.sql.") from e
        raise
    if not row:
        raise StatementError("Statement not found.")
    cur = str(row.get("status") or "")
    if not can_transition(cur, to_status):
        raise StatementError(f"A {cur} statement cannot become {to_status}.")
    if to_status == "superseded" and not (reason or "").strip():
        raise StatementError("A reason is required to supersede a statement.")
    if to_status == "approved":
        period = str(row.get("period") or "")
        if not month_has_ended(period):
            raise StatementError(f"{month_label(period)} has not ended yet — approve once the month closes and the "
                                 f"final Focus load is in.")
        # The figures must cover the WHOLE month: a draft frozen mid-month (data to the 21st, say)
        # stays a draft after the month ends — create a fresh draft once a Focus load reaches the
        # last day, and approve that one (money is only ever approved on complete data).
        end = period_end(period).isoformat()
        dt = str(row.get("data_through") or "")[:10]
        if not dt or dt < end:
            raise StatementError(f"This draft only has sales data to {dt or 'an unknown date'}, not the whole of "
                                 f"{month_label(period)} (to {end}). Load the month-end Focus reports, create a new "
                                 f"draft and approve that one.")
        others = closed_rows(str(row.get("salesman") or ""), period, str(row.get("basis") or ""),
                             exclude_id=int(statement_id))
        if others:
            o = others[0]
            if str(o.get("status")) == "paid":
                raise StatementConflict(f"{row.get('salesman')} · {month_label(period)} is already PAID (#{o.get('id')}, "
                                        f"BHD {s3(o.get('kickback_bhd'))}). A paid statement is final; this draft cannot "
                                        f"be approved — settle any difference as a separate adjustment with the office.")
            raise StatementConflict(f"{row.get('salesman')} · {month_label(period)} already has a {o.get('status')} "
                                    f"statement (#{o.get('id')}, BHD {s3(o.get('kickback_bhd'))}). Supersede #{o.get('id')} "
                                    f"first if this draft replaces it.")
    moved = _move(int(statement_id), cur, to_status, by, _now_iso(), reason=(reason or "").strip() or None)
    if not moved:
        raise StatementConflict(CONFLICT_MSG)
    _audit(by, f"kickback.statement_{to_status}",
           {"id": int(statement_id), "salesman": row.get("salesman"), "period": row.get("period"),
            "basis": row.get("basis"), "from": cur, "to": to_status, "kickback_bhd": s3(row.get("kickback_bhd")),
            "reason": (reason or "").strip() or None})
    return public_row(moved)
