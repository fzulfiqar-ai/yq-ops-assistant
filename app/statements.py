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

Owner decisions in force: basis = ex-VAT (net_bhd), net of Focus Sales Returns only once a Sales
Return register is loaded (returns_bhd stays NULL until then — never estimated), the whole month at
the reached tier. Money is Decimal, 3 dp, ROUND_HALF_UP; JSON carries 3-dp strings.

Every read tolerates the table not existing yet (the API may deploy before M8 on a fresh
database): it logs and answers "not available" instead of a 500. The optional columns added by
scripts/r3_statements_migration.sql (paid_by, superseded_*) are written when present and dropped
from the payload when PostgREST says they are not there yet.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from app.database import get_client

log = logging.getLogger(__name__)

TABLE = "salesman_kickback_statements"
BASES = ("net_ex_vat", "vat_incl_display")
STATUSES = ("snapshot", "draft", "approved", "paid", "superseded")
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
    """An admin-facing problem (HTTP 400 at the edge; CONFLICT_MSG is a 409)."""


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
    card at the moment of freezing. Reps whose target row yields no tiers are skipped."""
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
        tgt = shop.rep_target(name, period)
        tp = shop.tier_progress(tgt, amt, data_date, basis=basis)
        if not tp:
            continue
        rows.append({
            "salesman": name, "salesman_id": sid.get(name), "period": period, "basis": basis,
            "data_through": (str(data_date or "")[:10] or None), "sales_bhd": s3(tp["mtd_bhd"]),
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


def rep_summary(sm: dict | None) -> dict:
    """For /shop/me: `last_closed` = the rep's most recent approved or paid statement (the money
    that is final), `draft` = the newest open draft (the month being closed). Keyed by the Focus
    name, like the targets. None / None for an unlinked login or before the table exists."""
    out: dict = {"last_closed": None, "draft": None}
    name = str((sm or {}).get("focus_name") or "").strip()
    if not name:
        return out
    try:
        closed = (_select().eq("salesman", name).in_("status", ["approved", "paid"])
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
    return out


# ── writes (insert once; forward-only status moves) ──────────────────────────

def _audit(by: str, event: str, detail: dict) -> None:
    from app.audit import log_event
    log_event(by, event, detail=detail)


def create_draft(period: str, by: str, note: str | None = None, basis: str | None = None) -> dict:
    """Freeze the month as draft rows, one per rep with a target. A rep whose identical draft
    already exists (same period, basis, status and data_through) is skipped, never rewritten; an
    OLDER open draft for the same rep/period/basis (an earlier data date) is moved to superseded
    — forward only, audited — so one month has one open draft per rep. Approved and paid rows are
    never touched by this. Returns what was created, skipped and superseded."""
    from app import shop
    basis = basis or shop.KICKBACK_BASIS
    period = valid_period(period)
    rows = build_rows(period, basis)
    if not rows:
        raise StatementError("No rep has a target row for this month — import targets first.")
    client = get_client()
    try:
        existing = (client.table(TABLE).select("id,salesman,status,data_through")
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
        mine = by_rep.get(r["salesman"], [])
        new_dt = str(r["data_through"] or "")
        same = [x for x in mine if x.get("status") in ("draft", "approved", "paid")
                and str(x.get("data_through") or "") == new_dt]
        if same:
            skipped.append({"salesman": r["salesman"], "id": same[0]["id"],
                            "reason": f"a {same[0].get('status')} statement with the same data date exists"})
            continue
        closed = [x for x in mine if x.get("status") in ("approved", "paid")]
        payload = {**r, "status": "draft", "note": note, "created_by": by,
                   "target_snapshot": r["target_snapshot"] if r["target_snapshot"] is not None else {}}
        ins = client.table(TABLE).insert(payload).execute().data or []
        new_id = (ins[0] or {}).get("id") if ins else None
        created.append({"salesman": r["salesman"], "id": new_id, "sales_bhd": r["sales_bhd"],
                        "tier_reached": r["tier_reached"], "kickback_bhd": r["kickback_bhd"],
                        "data_through": r["data_through"],
                        # a correction after approval: the admin must supersede the closed row by hand
                        "note": (f"{len(closed)} approved/paid statement(s) exist for this rep — supersede "
                                 f"the old one deliberately if this draft replaces it") if closed else None})
        for old in mine:
            # only an OLDER open draft gives way (an earlier data date); a draft carrying newer data
            # than this run (a data rollback) stays, and approved/paid rows are never touched here
            if (old.get("status") == "draft" and old["id"] != new_id
                    and str(old.get("data_through") or "") < new_dt):
                moved = _move(old["id"], "draft", "superseded", by, when,
                              reason=f"replaced by draft {new_id} (data to {r['data_through']})",
                              superseded_by_id=new_id)
                if moved:
                    superseded.append({"salesman": r["salesman"], "id": old["id"], "by": new_id})
    total = total_kickback([c for c in created] or [])
    _audit(by, "kickback.statement", {"period": period, "basis": basis, "status": "draft",
                                      "reps": len(created), "skipped": len(skipped), "superseded": len(superseded),
                                      "total_kickback_bhd": total, "note": note})
    return {"period": period, "basis": basis, "created": created, "skipped": skipped, "superseded": superseded,
            "total_kickback_bhd": total}


def _move(statement_id: int, from_status: str, to_status: str, by: str, when: str, *,
          reason: str | None = None, superseded_by_id: int | None = None) -> dict | None:
    """The compare-and-swap: UPDATE … WHERE id = $1 AND status = $from. Only status and the
    who/when columns are named — no amount can travel through here. Returns the row moved, or
    None when the status had already changed underneath (the caller decides how to answer)."""
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
        rows = _run({**fields, **{k: v for k, v in extra.items() if v is not None}})
    except Exception as e:  # noqa: BLE001 — the R3 columns are optional until their migration runs
        if extra and _missing_column(e):
            log.info("%s: optional columns not there yet, moving without them (%s)", TABLE, e)
            rows = _run(fields)
        else:
            raise
    return rows[0] if rows else None


def transition(statement_id: int, to_status: str, by: str, reason: str | None = None) -> dict:
    """Move one statement forward (approve / paid / supersede). Refuses anything that is not a
    forward step, and answers CONFLICT_MSG when the row moved under the caller's feet. Audited."""
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
    moved = _move(int(statement_id), cur, to_status, by, _now_iso(), reason=(reason or "").strip() or None)
    if not moved:
        raise StatementError(CONFLICT_MSG)
    _audit(by, f"kickback.statement_{to_status}",
           {"id": int(statement_id), "salesman": row.get("salesman"), "period": row.get("period"),
            "basis": row.get("basis"), "from": cur, "to": to_status, "kickback_bhd": s3(row.get("kickback_bhd")),
            "reason": (reason or "").strip() or None})
    return public_row(moved)
