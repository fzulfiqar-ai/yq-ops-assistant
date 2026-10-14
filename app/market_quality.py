"""Market Intel v2, Phase 1 (release R8m, plan Part F): quality capture and review.

A quality or packaging report from Spotted (an issue type of kind 'quality' / 'packaging', or the v1 kind
'complaint' with an issue type, or any safety report) is a sighting on a CASE: one real-world problem, a
product + an issue. Opportunity kinds (shop asks, competitor prices, new products) keep the R7b clusters
(market_items); one capture and one photo store feed both tracks. The tables arrive with
scripts/r8m_market_quality_migration.sql. Until then available() is False: the capture saves as v1 (the v2
fields are ignored, app.market_intel.capture) and every route of app/market_quality_api.py answers 503
with MISSING_HINT.

    lists      suppliers, brands (aliases, renamed_from, merged_into) and issue types (EN / AR labels, kind,
               safety default, the extra capture fields): admin-managed tables, no name in the code
    products   the product picker: the staff catalog with its 320 px thumbnails; at_shop = the shop
               ordered it on the marketplace in the last 180 days
    capture    plan_capture / find_open_case / link_case, called by app.market_intel.capture: the sighting
               joins an OPEN case (not closed / not substantiated / merged) of the same YQ SKU and issue
               type last seen within 60 days, else it opens one. A sighting with no SKU ("not sure / not
               ours") always opens its own case; the office links the product, merges or moves.
    workflow   submitted -> under_review <-> more_info_required -> verified | not_substantiated ->
               action_requested -> resolved -> closed (NEXT_STATUS; anything else 409). The evidence level
               is a separate number that only moves up: 1 reported, 2 evidence submitted, 3 inspected and
               verified by YQ (verifying sets at least 3), 4 supplier confirmed, 5 resolution verified.
    safety     a safety flag (or the issue type's safety default) puts the case on the Safety tab and
               e-mails ONE alert per case (app.emailer.send_html) after the answer has gone: a failed or
               slow e-mail never costs the capture. The claim is a 'safety_alert' event written first, the
               outcome a second one; a claim left without its outcome (the e-mail was lost with a failed
               request or a restart) is sent by the shop jobs' safety sweep.

Who: a rep captures and reads his own sightings with plain status words and the office's open question
(app.market_intel.mine), and answers that question; the office (any role but salesman) reads cases;
reviewers (REVIEW_ROLES) change them; management reads them with phones and e-mails masked and is
read-only centrally (app.auth); only an admin edits the lists or reopens a closed case — a closed case is
read-only until then. Every case change writes a market_case_events row (append-only); the routes write the
audit_log rows.
"""
from __future__ import annotations

import html as _html
import logging
import os
import re
import time
from datetime import date, datetime, timedelta, timezone

from app import market_intel as mi
from app.market_intel import REVIEW_ROLES, MarketIntelError, clean_text

log = logging.getLogger(__name__)

# ── vocabulary ────────────────────────────────────────────────────────────────

STATUSES = ("submitted", "under_review", "more_info_required", "verified", "not_substantiated",
            "action_requested", "resolved", "closed")
STATUS_LABELS = {"submitted": "Submitted", "under_review": "Under review",
                 "more_info_required": "More information required", "verified": "Verified",
                 "not_substantiated": "Not substantiated", "action_requested": "Action requested",
                 "resolved": "Resolved", "closed": "Closed"}
# what a rep reads about his own report ("My reports")
STATUS_WORDS = {"submitted": "Received", "under_review": "Being looked at",
                "more_info_required": "We need more from you", "verified": "Confirmed",
                "not_substantiated": "Not confirmed", "action_requested": "Sent to the supplier",
                "resolved": "Fixed", "closed": "Closed"}
# the only moves a case makes (anything else is 409); closed -> under_review is an admin's, with a reason
NEXT_STATUS: dict[str, tuple[str, ...]] = {
    "submitted": ("under_review", "more_info_required", "verified", "not_substantiated"),
    "under_review": ("more_info_required", "verified", "not_substantiated"),
    "more_info_required": ("under_review", "verified", "not_substantiated"),
    "verified": ("action_requested", "closed"),
    "not_substantiated": ("closed", "under_review"),
    "action_requested": ("resolved",),
    "resolved": ("closed",),
    "closed": ("under_review",),
}
REASON_REQUIRED = {("not_substantiated", "under_review"), ("closed", "under_review")}
CLOSED_STATUSES = ("closed", "not_substantiated")            # never takes a new sighting
OFF_SAFETY_TAB = ("resolved", "closed", "not_substantiated")
QUESTION_FROM = ("submitted", "under_review", "more_info_required")
EVIDENCE_LABELS = {1: "Reported", 2: "Evidence submitted", 3: "Inspected & verified by YQ",
                   4: "Supplier confirmed", 5: "Resolution verified"}
EVIDENCE_VERIFIED = 3
# What a level needs (evidence_cap): 3 only once a person verified the case (verification_method: the move to
# Verified sets it, with at least 3); 4 from Action requested on (the supplier was asked); 5 once Resolved.
EVIDENCE_NEEDS = {3: "Inspected & verified by YQ (level 3) comes with the move to Verified: verify the case first.",
                  4: "Supplier confirmed (level 4) is possible once the supplier was asked: from Action requested on.",
                  5: "Resolution verified (level 5) is possible once the case is Resolved."}
VERIFICATION_METHODS = ("sample_inspected", "photos_sufficient", "several_shops")
VERIFICATION_LABELS = {"sample_inspected": "Sample inspected", "photos_sufficient": "Photos judged sufficient",
                       "several_shops": "Reported by several shops"}
ISSUE_KINDS = ("quality", "packaging", "demand", "competitor", "other")
CASE_KINDS = ("quality", "packaging")
ISSUE_FIELDS = ("qty", "batch_ref", "packaging_version", "label_photo")
REPORT_BASES = ("customer_said", "rep_saw")
PHOTO_ROLES = ("product", "packaging", "label_batch", "receipt", "other")
ACTION_STATUSES = ("open", "responded", "resolved", "cancelled")
TABS = ("safety", "new", "need_info", "identify", "all")
LIST_TABLES = {"suppliers": "suppliers", "brands": "brands", "issue-types": "market_issue_types"}
CASE_WINDOW_DAYS = 60
AT_SHOP_DAYS = 180
AT_SHOP_TTL_S = 300.0
MISSING_HINT = mi.QUALITY_HINT
_BAHRAIN = timezone(timedelta(hours=3))
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,59}$")         # market_issue_types_code_slug, as the database checks it
RELINK_ACTOR = "system (brand re-linked)"
FINISH_ACTOR = "system (capture finished)"
SWEEP_ACTOR = "system (safety sweep)"
SWEEP_AFTER = timedelta(minutes=2)       # a safety sighting this old without its case / claim is finished
SWEEP_WITHIN = timedelta(days=7)
# A claim with no outcome ('sent' / 'not_sent') this long after it, or after the sweep's last try, lost its
# e-mail (the request failed after the claim — Starlette drops a failed request's background task — or the
# process restarted first): the sweep sends it. At most RESEND_LIMIT tries per claim; then it says so, once.
ORPHAN_AFTER = timedelta(minutes=10)
RESEND_LIMIT = 3
ANSWER_REASON = "the rep answered"
CLOSED_READ_ONLY = ("A closed case is read-only: an admin reopens it first (Closed → Under review, with a reason), "
                    "then it can be changed.")


# ── small helpers ─────────────────────────────────────────────────────────────

def _c():
    """The client app.market_intel uses (one place to stand a fake in for both modules)."""
    return mi.get_client()


def available() -> bool:
    return mi.available() and mi.quality_available()


def _need() -> None:
    if not available():
        raise MarketIntelError(MISSING_HINT, 503)


def _id(v, what: str) -> int | None:
    """A positive id from a form or JSON value; '' / None -> None; anything else -> 400."""
    if v in (None, ""):
        return None
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        raise MarketIntelError(f"Unknown {what}.") from None
    if n <= 0:
        raise MarketIntelError(f"Unknown {what}.")
    return n


_YES, _NO = {"1", "true", "yes", "on", "y"}, {"0", "false", "no", "off", "n", ""}


def _flag(v, what: str = "flag") -> bool:
    if isinstance(v, bool):
        return v
    s = str(v if v is not None else "").strip().lower()
    if s in _YES:
        return True
    if s in _NO:
        return False
    raise MarketIntelError(f"{what} is true or false.")


def _day(v, what: str) -> str | None:
    """'YYYY-MM-DD' (a due date, a filter bound); '' / None -> None; anything else -> 400."""
    if v in (None, ""):
        return None
    try:
        return date.fromisoformat(str(v).strip()[:10]).isoformat()
    except ValueError:
        raise MarketIntelError(f"The {what} must be a date like 2026-10-31.") from None


def _bahrain_day(v) -> str | None:
    t = mi._parse_ts(v)
    return t.astimezone(_BAHRAIN).date().isoformat() if t else None


def _who(email) -> str:
    """An actor as the timeline shows it: the login's local part (never the whole address)."""
    return str(email or "").split("@")[0] or "system"


def _chunks(ids: list, n: int = 100):
    for i in range(0, len(ids), n):
        yield ids[i:i + n]


def _m(v, mask: bool):
    return mi._m(v, mask)


# ── the admin lists ───────────────────────────────────────────────────────────

class Names:
    """One read of the three admin lists (small tables) for a request: rows by id, merged_into followed
    to the live row (a merged brand or supplier forwards; a renamed brand keeps its old name as an alias)."""

    def __init__(self, suppliers: list[dict], brands: list[dict], issues: list[dict]):
        self.suppliers = {int(r["id"]): r for r in suppliers if r.get("id") is not None}
        self.brands = {int(r["id"]): r for r in brands if r.get("id") is not None}
        self.issues = {int(r["id"]): r for r in issues if r.get("id") is not None}

    @classmethod
    def load(cls) -> "Names":
        c = _c()

        def rows(table: str) -> list[dict]:
            return mi._all(lambda: c.table(table).select("*").order("id"))
        return cls(rows("suppliers"), rows("brands"), rows("market_issue_types"))

    @staticmethod
    def _live(table: dict, id_) -> dict | None:
        try:
            row = table.get(int(id_))
        except (TypeError, ValueError):
            return None
        hops = 5
        while row and row.get("merged_into") and hops > 0:
            nxt = table.get(int(row["merged_into"]))
            if not nxt:
                break
            row, hops = nxt, hops - 1
        return row

    def brand(self, id_) -> dict | None:
        return self._live(self.brands, id_) if id_ not in (None, "") else None

    def supplier(self, id_) -> dict | None:
        return self._live(self.suppliers, id_) if id_ not in (None, "") else None

    def issue(self, id_) -> dict | None:
        try:
            return self.issues.get(int(id_)) if id_ not in (None, "") else None
        except (TypeError, ValueError):
            return None

    def brand_by_text(self, text) -> dict | None:
        """The brand a typed (or catalog) name means: its name or one of its aliases, any case."""
        t = mi._ci(text)
        if not t:
            return None
        for r in sorted(self.brands.values(), key=lambda r: (r.get("active") is False, int(r["id"]))):
            if any(mi._ci(n) == t for n in [r.get("name"), *(r.get("aliases") or [])]):
                return self.brand(r["id"])
        return None

    def safety_issue(self) -> dict | None:
        """The issue type a safety report without one is filed under: the first active safety-default
        type, else the first active quality type (by sort)."""
        act = [r for r in self.issues.values() if r.get("active") is not False]
        for want in (lambda r: bool(r.get("safety_default")), lambda r: r.get("kind") == "quality"):
            got = sorted((r for r in act if want(r)), key=lambda r: (int(r.get("sort") or 100), int(r["id"])))
            if got:
                return got[0]
        return None

    def supplier_of(self, case: dict) -> dict | None:
        """A case's supplier: its own, else its brand's."""
        s = self.supplier(case.get("supplier_id"))
        if not s and case.get("brand_id"):
            b = self.brand(case["brand_id"])
            s = self.supplier((b or {}).get("supplier_id"))
        return s


def _supplier_out(r: dict) -> dict:
    return {"id": r.get("id"), "name": r.get("name"), "code": r.get("code"), "active": r.get("active") is not False,
            "merged_into": r.get("merged_into")}


def _brand_out(r: dict) -> dict:
    return {"id": r.get("id"), "name": r.get("name"), "supplier_id": r.get("supplier_id"),
            "aliases": list(r.get("aliases") or []), "renamed_from": r.get("renamed_from"),
            "active": r.get("active") is not False, "merged_into": r.get("merged_into")}


def _issue_out(r: dict) -> dict:
    return {"id": r.get("id"), "kind": r.get("kind"), "code": r.get("code"), "label_en": r.get("label_en"),
            "label_ar": r.get("label_ar"), "safety_default": bool(r.get("safety_default")),
            "fields": list(r.get("fields") or []), "active": r.get("active") is not False,
            "sort": int(r.get("sort") if r.get("sort") is not None else 100)}


_OUT = {"suppliers": _supplier_out, "brands": _brand_out, "issue-types": _issue_out}


def categories() -> list[str]:
    """The catalog's categories (app.catalog.CATEGORY_ORDER, the shelf order the category master is
    keyed by), then any other the live catalog carries."""
    from app.catalog import CATEGORY_ORDER
    cats = list(CATEGORY_ORDER)
    items = (mi._catalog_ctx() or {}).get("items") or {}
    cats += sorted({str(it.get("category")).strip().upper() for it in items.values()
                    if str(it.get("category") or "").strip()} - set(cats))
    return cats


def lists(*, all_: bool = False) -> dict:
    """GET /market-intel/lists: what the capture sheet and the Lists page read. Active rows only, unless
    all_ (an admin's ?all=1): then inactive and merged rows too."""
    _need()
    try:
        names = Names.load()
    except Exception as e:  # noqa: BLE001
        if mi._missing(e):
            raise MarketIntelError(MISSING_HINT, 503) from None
        raise

    def keep(r: dict) -> bool:
        return all_ or (r.get("active") is not False and not r.get("merged_into"))
    issues = sorted((r for r in names.issues.values() if keep(r)),
                    key=lambda r: (ISSUE_KINDS.index(r["kind"]) if r.get("kind") in ISSUE_KINDS else 9,
                                   int(r.get("sort") if r.get("sort") is not None else 100),
                                   str(r.get("label_en") or "").lower()))
    return {"available": True,
            "suppliers": [_supplier_out(r) for r in sorted((r for r in names.suppliers.values() if keep(r)),
                                                            key=lambda r: str(r.get("name") or "").lower())],
            "brands": [_brand_out(r) for r in sorted((r for r in names.brands.values() if keep(r)),
                                                      key=lambda r: str(r.get("name") or "").lower())],
            "issue_types": [_issue_out(r) for r in issues],
            "categories": categories()}


def _aliases(v) -> list[str]:
    raw = v if isinstance(v, (list, tuple)) else str(v or "").split(",")
    out: list[str] = []
    for a in raw:
        a = clean_text(a, 80)
        if a and a.lower() not in [x.lower() for x in out]:
            out.append(a)
    if len(out) > 30:
        raise MarketIntelError("At most 30 aliases.")
    return out


def _entry_values(kind: str, body: dict, names: Names, current: dict | None) -> dict:
    """The validated columns of one list entry (create: current None; edit: only what the body has)."""
    row: dict = {}
    if "active" in body and body["active"] is not None:
        row["active"] = _flag(body["active"], "active")
    if kind in ("suppliers", "brands") and "name" in body:
        name = clean_text(body.get("name"), 120 if kind == "suppliers" else 80)
        if not name:
            raise MarketIntelError("A name is required.")
        row["name"] = name
    if kind == "suppliers" and "code" in body:
        row["code"] = clean_text(body.get("code"), 40)
    if kind == "brands":
        if "supplier_id" in body:
            sid = _id(body.get("supplier_id"), "supplier")
            if sid is not None and sid not in names.suppliers:
                raise MarketIntelError("Unknown supplier.")
            row["supplier_id"] = sid
        if "aliases" in body:
            row["aliases"] = _aliases(body.get("aliases"))
    if kind == "issue-types":
        if "kind" in body:
            k = str(body.get("kind") or "").strip().lower()
            if k not in ISSUE_KINDS:
                raise MarketIntelError("An issue type is quality, packaging, demand, competitor or other.")
            row["kind"] = k
        if "label_en" in body:
            lab = clean_text(body.get("label_en"), 80)
            if not lab:
                raise MarketIntelError("An English label is required.")
            row["label_en"] = lab
        if "label_ar" in body:
            row["label_ar"] = clean_text(body.get("label_ar"), 80)
        if "code" in body and body.get("code") not in (None, ""):
            code = str(body.get("code")).strip().lower()
            if not _SLUG.match(code):
                raise MarketIntelError("A code is up to 60 lower-case letters, digits, _ or - (like not_charging).")
            row["code"] = code
        if "safety_default" in body and body["safety_default"] is not None:
            row["safety_default"] = _flag(body["safety_default"], "safety_default")
        if "fields" in body:
            fields = [str(x).strip().lower() for x in (body.get("fields") or []) if str(x).strip()]
            bad = [x for x in fields if x not in ISSUE_FIELDS]
            if bad:
                raise MarketIntelError("Fields are any of qty, batch_ref, packaging_version, label_photo.")
            row["fields"] = list(dict.fromkeys(fields))
        if "sort" in body and body["sort"] is not None:
            try:
                s = int(body["sort"])
            except (TypeError, ValueError):
                raise MarketIntelError("The sort order is a whole number.") from None
            if not 0 <= s <= 10000:
                raise MarketIntelError("The sort order is between 0 and 10,000.")
            row["sort"] = s
    if current is None:                                           # a new entry: what it cannot be without
        if kind in ("suppliers", "brands") and not row.get("name"):
            raise MarketIntelError("A name is required.")
        if kind == "issue-types":
            if not row.get("kind") or not row.get("label_en"):
                raise MarketIntelError("An issue type needs a kind and an English label.")
            if not row.get("code"):
                slug = re.sub(r"[^a-z0-9]+", "_", row["label_en"].lower()).strip("_")[:60]
                if not _SLUG.match(slug or ""):
                    raise MarketIntelError("Give the issue type a code (like not_charging).")
                row["code"] = slug
    return row


def _clash(kind: str, row: dict, names: Names, self_id: int | None) -> str | None:
    """A name or code another live entry already uses, or None."""
    if kind == "issue-types" and row.get("code"):
        for r in names.issues.values():
            if r.get("code") == row["code"] and r.get("id") != self_id:
                return f"An issue type with the code {row['code']} already exists."
    if kind in ("suppliers", "brands") and row.get("name"):
        table = names.suppliers if kind == "suppliers" else names.brands
        want = row["name"].lower()
        for r in table.values():
            if r.get("id") == self_id or r.get("active") is False or r.get("merged_into"):
                continue
            taken = [r.get("name")] + (list(r.get("aliases") or []) if kind == "brands" else [])
            if any(str(n or "").lower() == want for n in taken):
                return f"A {kind[:-1]} called {row['name']} already exists."
    return None


def _merge_target(kind: str, names: Names, entry_id: int, target) -> int | None:
    if target in (None, ""):
        return None
    tid = _id(target, kind[:-1])
    table = names.suppliers if kind == "suppliers" else names.brands
    if tid == entry_id:
        raise MarketIntelError("An entry cannot be merged into itself.")
    if tid not in table:
        raise MarketIntelError(f"Unknown {kind[:-1]} to merge into.", 404)
    # Walk the target's own merge chain: it must never lead back here (a circle), and a chain that
    # already loops (made by hand) or runs on and on is refused instead of walked forever.
    seen: set[int] = set()
    cur = table.get(tid)
    while cur and cur.get("merged_into"):
        nxt = int(cur["merged_into"])
        if nxt == entry_id:
            raise MarketIntelError("That merge would go round in a circle.", 409)
        seen.add(int(cur["id"]))
        if nxt in seen or len(seen) > 50:
            raise MarketIntelError(f"The {kind[:-1]} to merge into is in a merge loop; untangle it first.", 409)
        cur = table.get(nxt)
    return tid


def _admin(role: str) -> None:
    if role != "admin":
        raise MarketIntelError("Only an admin can change the lists.", 403)


def create_entry(kind: str, body: dict, actor: str, role: str) -> dict:
    """POST /market-intel/lists/{suppliers|brands|issue-types} (admin)."""
    _admin(role)
    if kind not in LIST_TABLES:
        raise MarketIntelError("Unknown list.", 404)
    _need()
    names = Names.load()
    row = _entry_values(kind, body, names, None)
    clash = _clash(kind, row, names, None)
    if clash:
        raise MarketIntelError(clash, 409)
    row.update(created_by=actor or None, updated_by=actor or None)
    try:
        got = (_c().table(LIST_TABLES[kind]).insert(row).execute().data or [row])[0]
    except Exception as e:  # noqa: BLE001
        if mi._unique_violation(e):
            raise MarketIntelError("That name or code is already taken.", 409) from None
        raise
    return {"ok": True, "entry": _OUT[kind](got)}


def update_entry(kind: str, entry_id: int, body: dict, actor: str, role: str) -> dict:
    """PATCH /market-intel/lists/{kind}/{id} (admin): edit, deactivate (active false) or merge
    (merged_into: the entry it duplicates; the merged one turns inactive and forwards there). A brand
    renamed keeps its old name as an alias and in renamed_from, so old sightings still match it."""
    _admin(role)
    if kind not in LIST_TABLES:
        raise MarketIntelError("Unknown list.", 404)
    _need()
    names = Names.load()
    table = {"suppliers": names.suppliers, "brands": names.brands, "issue-types": names.issues}[kind]
    cur = table.get(int(entry_id))
    if not cur:
        raise MarketIntelError("That entry does not exist.", 404)
    row = _entry_values(kind, body, names, cur)
    if kind in ("suppliers", "brands") and "merged_into" in body:
        tid = _merge_target(kind, names, int(entry_id), body.get("merged_into"))
        row["merged_into"] = tid
        if tid is not None:
            row["active"] = False
    if kind == "brands" and row.get("name") and row["name"] != cur.get("name"):
        aliases = row.get("aliases", list(cur.get("aliases") or []))
        if cur.get("name") and cur["name"].lower() not in [a.lower() for a in aliases]:
            aliases = [*aliases, cur["name"]]
        row.update(aliases=aliases, renamed_from=cur.get("name"))
    diffs = {k: {"from": cur.get(k), "to": v} for k, v in row.items() if v != cur.get(k)}
    if not diffs:
        return {"ok": True, "changed": False, "entry": _OUT[kind](cur)}
    after = {**cur, **row}
    if kind == "issue-types" and "code" in diffs:
        clash = _clash(kind, {"code": after["code"]}, names, int(entry_id))
    elif kind != "issue-types" and after.get("active") is not False and not after.get("merged_into") \
            and ("name" in diffs or "active" in diffs or "merged_into" in diffs):
        clash = _clash(kind, {"name": after.get("name")}, names, int(entry_id))   # renamed or brought back
    else:
        clash = None
    if clash:
        raise MarketIntelError(clash, 409)
    upd = {k: row[k] for k in diffs}
    upd.update(updated_at=mi._iso(), updated_by=actor or None)
    try:
        got = _c().table(LIST_TABLES[kind]).update(upd).eq("id", int(entry_id)).execute().data
    except Exception as e:  # noqa: BLE001
        if mi._unique_violation(e):
            raise MarketIntelError("That name or code is already taken.", 409) from None
        raise
    out = {"ok": True, "changed": True, "entry": _OUT[kind]((got or [{**cur, **upd}])[0]), "fields": sorted(diffs)}
    if kind == "brands" and "supplier_id" in diffs:
        out["cases_moved"] = relink_brand_cases(int(entry_id), cur.get("supplier_id"), upd["supplier_id"])
    return out


def relink_brand_cases(brand_id: int, old_supplier, new_supplier) -> list[int]:
    """A brand moved from one supplier to another: its OPEN cases that still carry the old supplier (they
    took it from the brand) move to the new one, each with an 'edit' event by RELINK_ACTOR — otherwise
    new sightings of the brand would not join them (find_open_case keeps suppliers apart) and the old
    supplier's workbook would keep them. Closed, not substantiated and merged cases keep their history."""
    def rows():
        q = _c().table("market_cases").select("id,status,supplier_id,merged_into").eq("brand_id", int(brand_id))
        q = q.is_("supplier_id", "null") if old_supplier is None else q.eq("supplier_id", int(old_supplier))
        return q.order("id")
    moved = []
    for r in mi._all(rows):
        if r.get("merged_into") or r.get("status") in CLOSED_STATUSES:
            continue
        got = (_c().table("market_cases").update({"supplier_id": new_supplier, "updated_at": mi._iso(),
                                                  "updated_by": RELINK_ACTOR})
               .eq("id", int(r["id"])).eq("brand_id", int(brand_id)).execute().data)
        if got is not None and len(got) == 0:
            continue
        case_event(int(r["id"]), "edit", RELINK_ACTOR,
                   detail={"supplier_id": {"from": old_supplier, "to": new_supplier}, "brand_id": int(brand_id)})
        moved.append(int(r["id"]))
    return moved


# ── the product picker ────────────────────────────────────────────────────────

_at_shop: dict[int, tuple[float, frozenset]] = {}


def shop_codes(shop_id: int | None) -> frozenset:
    """The SKUs a shop ordered on the marketplace in the last AT_SHOP_DAYS (cancelled orders left out),
    upper-cased; cached AT_SHOP_TTL_S per shop. Empty when unknown or unreadable (at_shop is a hint)."""
    if not shop_id:
        return frozenset()
    hit = _at_shop.get(int(shop_id))
    now = time.monotonic()
    if hit and hit[0] > now:
        return hit[1]
    codes: set[str] = set()
    try:
        c = _c()
        since = (mi._now() - timedelta(days=AT_SHOP_DAYS)).isoformat()
        orders = mi._all(lambda: c.table("shop_orders").select("id,status").eq("customer_id", int(shop_id))
                         .gte("created_at", since).order("id"), 5)
        ids = [o["id"] for o in orders if o.get("status") != "cancelled"]
        for chunk in _chunks(ids):
            for ln in mi._all(lambda chunk=chunk: c.table("shop_order_lines").select("id,item_code")
                              .in_("order_id", chunk).order("id"), 20):
                if ln.get("item_code"):
                    codes.add(str(ln["item_code"]).upper())
    except Exception as e:  # noqa: BLE001 — a hint, never a failure
        log.info("market quality: a shop's ordered items not read: %s", e)
    out = frozenset(codes)
    _at_shop[int(shop_id)] = (now + AT_SHOP_TTL_S, out)
    return out


def rep_owns_shop(shop_id, salesman_id) -> bool:
    """Is this shop the salesman's own customer: assigned to him (shop_customers.salesman_id, or the
    first-touch sticky_salesman_id) or a shop he has a marketplace order for? Anything unreadable: no."""
    if not shop_id or not salesman_id:
        return False
    try:
        got = _c().table("shop_customers").select("*").eq("id", int(shop_id)).limit(1).execute().data or []
        if got and any(got[0].get(k) is not None and str(got[0][k]) == str(salesman_id)
                       for k in ("salesman_id", "sticky_salesman_id")):
            return True
        return bool(_c().table("shop_orders").select("id").eq("customer_id", int(shop_id))
                    .eq("salesman_id", int(salesman_id)).limit(1).execute().data)
    except Exception as e:  # noqa: BLE001 — a hint: when in doubt, nothing is said about the shop
        log.info("market quality: shop ownership not read: %s", e)
        return False


def products(q: str | None = None, *, shop_id: int | None = None, shop_phone: str | None = None,
             limit: int = 20, salesman_id: int | None = None, own_shops_only: bool = False) -> list[dict]:
    """GET /market-intel/products: catalog items for the capture sheet's "Which product?" — with words, the
    best matches (the code exactly, a code that starts with them, then names / brands / categories holding
    every word); without, the shop's own items first. Thumbnails are the catalog's public 320 px WebPs.
    own_shops_only (a salesman): the shop counts only when it is HIS customer (rep_owns_shop); any other
    shop — another rep's, or a phone that is no customer at all — gives exactly the answer of no shop, so
    the picker reveals neither another shop's purchases nor whether a phone is a customer."""
    from app.catalog import public_url, thumb_path
    ctx = mi._catalog_ctx() or {}
    items = ctx.get("items") or {}
    order = [c for c in (ctx.get("order") or list(items)) if c in items]
    sid = shop_id or (mi._shop_customer_id(shop_phone) if shop_phone else None)
    if own_shops_only and not rep_owns_shop(sid, salesman_id):
        sid = None
    mine = shop_codes(sid)
    lim = max(1, min(int(limit or 20), 50))
    words = [w for w in re.split(r"\s+", mi._ci(q)) if w]
    qc = mi._compact(q)
    scored: list[tuple] = []
    for pos, code in enumerate(order):
        it = items[code]
        at = str(code).upper() in mine
        if words:
            cc = mi._compact(code)
            hay = " ".join(mi._ci(it.get(k)) for k in ("display_name", "brand", "category", "spec")) + " " + mi._ci(code)
            if qc and cc == qc:
                rank = 0
            elif qc and cc.startswith(qc):
                rank = 1
            elif all(w in hay for w in words):
                rank = 2
            else:
                continue
        else:
            rank = 0 if at else 1
        scored.append((rank, not at, pos, code))
    scored.sort()
    out = []
    for _r, _n, _p, code in scored[:lim]:
        it = items[code]
        out.append({"code": str(code), "name": it.get("display_name") or str(code), "brand": it.get("brand"),
                    "category": it.get("category"),
                    "thumb_url": public_url(thumb_path(str(code), "product", 320)) if it.get("product_image_url") else None,
                    "package_thumb_url": (public_url(thumb_path(str(code), "package", 320))
                                          if it.get("package_image_url") else None),
                    "at_shop": str(code).upper() in mine})
    return out


# ── capture: which track, which case ──────────────────────────────────────────

def clean_v2(raw: dict, n_photos: int = 0) -> dict:
    """The capture's v2 fields (Spotted v2), validated without a database read. Absent -> None / False;
    photo_roles comes back aligned with the photos ('product' for a photo without one)."""
    basis = str(raw.get("report_basis") or "").strip().lower() or None
    if basis is not None and basis not in REPORT_BASES:
        raise MarketIntelError("The report basis is customer_said or rep_saw.")
    qty = None
    if raw.get("qty_affected") not in (None, ""):
        try:
            qty = int(str(raw["qty_affected"]).strip())
        except ValueError:
            raise MarketIntelError("The quantity affected must be a whole number.") from None
        if not 0 < qty <= 100000:
            raise MarketIntelError("The quantity affected must be between 1 and 100,000.")
    roles: list[str] = []
    if str(raw.get("photo_roles") or "").strip():
        roles = [r.strip().lower() or "product" for r in str(raw["photo_roles"]).split(",")]
        if any(r not in PHOTO_ROLES for r in roles):
            raise MarketIntelError("A photo role is product, packaging, label_batch, receipt or other.")
    return {"issue_type_id": _id(raw.get("issue_type_id"), "issue type"), "report_basis": basis,
            "qty_affected": qty, "batch_ref": clean_text(raw.get("batch_ref"), 60),
            "packaging_version": clean_text(raw.get("packaging_version"), 60),
            "safety_flag": _flag(raw.get("safety_flag"), "safety_flag"),
            "brand_id": _id(raw.get("brand_id"), "brand"), "supplier_id": _id(raw.get("supplier_id"), "supplier"),
            "photo_roles": (roles + ["product"] * n_photos)[:n_photos]}


def plan_capture(v2: dict, f: dict, yq_item: dict | None) -> dict:
    """What the v2 fields point at, and the track. A CASE when there is an issue type of kind quality or
    packaging, or the kind is 'complaint' with an issue type, or the report is a safety report (a safety
    report without an issue type is filed under Names.safety_issue). Unknown ids are refused (400).
    Returns {"issue", "case", "safety", "brand", "supplier", "obs_cols"} — obs_cols are the sighting's
    v2 columns, with the brand / supplier snapshots as typed or as linked."""
    names = Names.load()
    issue = names.issue(v2.get("issue_type_id")) if v2.get("issue_type_id") else None
    if v2.get("issue_type_id") and not issue:
        raise MarketIntelError("Unknown issue type.")
    safety = bool(v2.get("safety_flag") or (issue or {}).get("safety_default"))
    if safety and not issue:
        issue = names.safety_issue()
    brand = names.brand(v2.get("brand_id")) if v2.get("brand_id") else None
    if v2.get("brand_id") and not brand:
        raise MarketIntelError("Unknown brand.")
    brand = brand or names.brand_by_text(f.get("brand")) or names.brand_by_text((yq_item or {}).get("brand"))
    supplier = names.supplier(v2.get("supplier_id")) if v2.get("supplier_id") else None
    if v2.get("supplier_id") and not supplier:
        raise MarketIntelError("Unknown supplier.")
    supplier = supplier or names.supplier((brand or {}).get("supplier_id"))
    case = bool(issue) and (issue.get("kind") in CASE_KINDS or f.get("kind") == "complaint" or safety)
    obs_cols = {"issue_type_id": (issue or {}).get("id"), "report_basis": v2.get("report_basis"),
                "qty_affected": v2.get("qty_affected"), "batch_ref": v2.get("batch_ref"),
                "packaging_version": v2.get("packaging_version"), "safety_flag": safety,
                "brand_id": (brand or {}).get("id"), "supplier_id": (supplier or {}).get("id"),
                "brand_text": f.get("brand") or (brand or {}).get("name") or (yq_item or {}).get("brand"),
                "supplier_text": (supplier or {}).get("name")}
    return {"issue": issue, "case": case, "safety": safety, "brand": brand, "supplier": supplier,
            "obs_cols": obs_cols}


def _open_cases(yq_code: str | None, issue_type_id, now: datetime, supplier_id=None) -> list[dict]:
    """The OPEN cases a quality sighting may join, oldest first: the same YQ SKU and issue type, not closed /
    not substantiated / merged, last seen within CASE_WINDOW_DAYS, and never another supplier's (a case with
    a supplier takes a sighting of that supplier, or of none known). Suppliers compare as they live in the
    list: a case still naming a supplier that was merged into another is that other supplier's. No SKU
    ("not sure / not ours") -> none: unknown products never pool on an issue alone."""
    if not yq_code or not issue_type_id:
        return []
    since = now - timedelta(days=CASE_WINDOW_DAYS)
    rows = (_c().table("market_cases").select("*").eq("yq_item_code", yq_code).eq("issue_type_id", int(issue_type_id))
            .is_("merged_into", "null").order("id").limit(50).execute().data or [])
    out = []
    names: Names | None = None
    for r in rows:
        if r.get("status") in CLOSED_STATUSES:
            continue
        if supplier_id and r.get("supplier_id") and str(r["supplier_id"]) != str(supplier_id):
            names = names or Names.load()           # read only when two ids differ: a merge may make them one
            theirs, ours = names.supplier(r["supplier_id"]), names.supplier(supplier_id)
            if not theirs or not ours or str(theirs["id"]) != str(ours["id"]):
                continue
        seen = mi._parse_ts(r.get("last_seen")) or mi._parse_ts(r.get("first_seen")) or mi._parse_ts(r.get("created_at"))
        if seen and seen < since:
            continue
        out.append(r)
    return out


def find_open_case(yq_code: str | None, issue_type_id, now: datetime, supplier_id=None) -> dict | None:
    """The open case a quality sighting joins (_open_cases): the OLDEST, so two cases that were opened for
    one problem at the same moment converge on one."""
    got = _open_cases(yq_code, issue_type_id, now, supplier_id)
    return got[0] if got else None


def case_title(yq_item: dict | None, yq_code: str | None, f: dict, issue: dict | None) -> str:
    """'<product name or the typed words> · <issue label>'."""
    what = ((yq_item or {}).get("display_name") or yq_code or f.get("title") or clean_text(f.get("note"), 80)
            or "Product not identified")
    return clean_text(f"{what} · {(issue or {}).get('label_en') or 'Issue'}", 160) or "Quality report"


def case_event(case_id: int, event: str, actor: str, *, from_status: str | None = None,
               to_status: str | None = None, detail: dict | None = None) -> dict:
    """One row of a case's history (market_case_events, append-only in the database); returns it."""
    row = {"case_id": int(case_id), "event": event, "actor": actor or "system", "at": mi._iso(),
           "from_status": from_status, "to_status": to_status, "detail": detail or {}}
    return (_c().table("market_case_events").insert(row).execute().data or [row])[0]


def _case(case_id) -> dict | None:
    try:
        cid = int(case_id)
    except (TypeError, ValueError):
        return None
    got = _c().table("market_cases").select("*").eq("id", cid).limit(1).execute().data or []
    return got[0] if got else None


def _live(case: dict | None, hops: int = 5) -> dict | None:
    """Follow merged_into to the case a sighting now belongs to."""
    while case and case.get("merged_into") and hops > 0:
        case, hops = _case(case["merged_into"]), hops - 1
    return case


def refresh_case(case_id: int) -> dict:
    """Recount a case from its sightings (distinct shops, first / last seen) and cache it on the row."""
    obs = mi._all(lambda: _c().table("market_observations").select("id,observed_at,shop_customer_id,shop_name")
                  .eq("case_id", int(case_id)).order("id"))
    shops = {f"c{o['shop_customer_id']}" if o.get("shop_customer_id") else f"n{mi._ci(o.get('shop_name'))}"
             for o in obs if o.get("shop_customer_id") or mi._ci(o.get("shop_name"))}
    seen = sorted(t for t in (mi._parse_ts(o.get("observed_at")) for o in obs) if t)
    stats: dict = {"obs_count": len(obs), "shop_count": len(shops)}
    if seen:
        stats.update(first_seen=mi._iso(seen[0]), last_seen=mi._iso(seen[-1]))
    _c().table("market_cases").update({**stats, "updated_at": mi._iso()}).eq("id", int(case_id)).execute()
    return stats


def _has_photo(obs_id: int) -> bool:
    return bool(_c().table("market_photos").select("id").eq("observation_id", int(obs_id)).limit(1).execute().data)


def _reached(case_id: int) -> set[str]:
    """The statuses a case has been in: every timeline line that moved it (created, status, merge) names its
    new status."""
    rows = mi._all(lambda: _c().table("market_case_events").select("id,to_status").eq("case_id", int(case_id))
                   .not_.is_("to_status", "null").order("id"))
    return {str(r["to_status"]) for r in rows if r.get("to_status")}


def evidence_cap(case: dict, reached: set[str] | None = None) -> int:
    """The highest evidence level a case's HISTORY allows (EVIDENCE_NEEDS), whatever its status now: 2 until a
    person verified it (no verification_method), 3 once verified, 4 once it went to Action requested (the
    supplier was asked), 5 once it went to Resolved. A case closed straight from Verified stays at 3: it was
    never asked of a supplier, never resolved. `reached`: the statuses it has been in (_reached; read here
    when not given); its status now always counts."""
    if not case.get("verification_method"):
        return 2
    if reached is None:
        reached = _reached(int(case["id"])) if case.get("id") is not None else set()
    been = set(reached) | {str(case.get("status") or "")}
    if "resolved" in been:
        return 5
    return 4 if "action_requested" in been else 3


def _raise_evidence(case: dict, level: int, actor: str, why: str, **detail) -> tuple[dict, bool]:
    """Evidence only moves up — in the database too: the write carries `evidence_level < level`, so a stale
    read (a reviewer verified meanwhile) can never lower it. The 'evidence' event is written only when a row
    really changed. Returns (the case as now known, changed)."""
    cur = int(case.get("evidence_level") or 1)
    if level <= cur:
        return case, False
    got = (_c().table("market_cases").update({"evidence_level": level, "updated_at": mi._iso(),
                                              "updated_by": actor or "system"})
           .eq("id", int(case["id"])).lt("evidence_level", level).execute().data)
    if got is not None and len(got) == 0:
        return case, False                    # already at this level or above
    case_event(case["id"], "evidence", actor, detail={"from": cur, "to": level, "why": why, **detail})
    return {**case, "evidence_level": level}, True


def _set_safety(case: dict, actor: str, **detail) -> dict:
    if case.get("safety"):
        return case
    got = (_c().table("market_cases").update({"safety": True, "updated_at": mi._iso(), "updated_by": actor or "system"})
           .eq("id", int(case["id"])).eq("safety", False).execute().data)
    if got is not None and len(got) == 0:
        return {**case, "safety": True}       # someone made it a safety case meanwhile
    case_event(case["id"], "edit", actor, detail={"safety": {"from": False, "to": True}, **detail})
    return {**case, "safety": True}


def _card_part(case: dict, issue: dict | None, *, joined: bool, title: str) -> dict:
    """The case on the rep's result card. `title` is built from HIS sighting and the catalog (case_title),
    never the case's own title, which the office may have edited or another rep's words may have made."""
    return {"id": case.get("id"), "title": title, "status": case.get("status"),
            "status_words": STATUS_WORDS.get(case.get("status"), ""), "issue": (issue or {}).get("label_en"),
            "joined": joined, "sightings": int(case.get("obs_count") or 0), "shops": int(case.get("shop_count") or 0),
            "safety": bool(case.get("safety"))}


def _fold_into(dup: dict, keep: dict, obs: dict, actor: str) -> dict:
    """Two captures of one product and issue opened a case each at the same moment: the newer one (`dup`)
    folds into the older (`keep`) — its sighting moves, it closes as merged — evidence and safety carried,
    both recounted, both timelines say so. Returns `keep` as now known."""
    _c().table("market_observations").update({"case_id": int(keep["id"]), "result": "seen_before"}) \
        .eq("case_id", int(dup["id"])).execute()
    obs.update(case_id=int(keep["id"]), result="seen_before")
    _c().table("market_cases").update({"merged_into": int(keep["id"]), "status": "closed",
                                       "status_reason": f"merged into #{keep['id']}", "changed_at": mi._iso(),
                                       "changed_by": actor, "updated_at": mi._iso(), "updated_by": actor}) \
        .eq("id", int(dup["id"])).is_("merged_into", "null").execute()
    why = "opened at the same moment as an older case of the same product and issue"
    case_event(dup["id"], "merge", actor, from_status=dup.get("status"), to_status="closed",
               detail={"into": int(keep["id"]), "moved_observations": [int(obs["id"])], "why": why})
    case_event(keep["id"], "merge", actor, detail={"from": int(dup["id"]), "moved_observations": [int(obs["id"])],
                                                   "why": why})
    keep, _ = _raise_evidence(keep, min(int(dup.get("evidence_level") or 1), evidence_cap(keep)), actor, "merge",
                              from_case=int(dup["id"]))
    if dup.get("safety"):
        keep = _set_safety(keep, actor, from_case=int(dup["id"]))
    refresh_case(int(dup["id"]))
    return keep


def link_case(obs: dict, plan: dict, *, found: dict | None, yq_item: dict | None, f: dict, actor: str,
              has_photo: bool, defer=None, now: datetime | None = None) -> tuple[dict, bool]:
    """Put a sighting on its case: `found` (already its case_id) or a new case opened for it — and when an
    older open case of the same product, issue and supplier appeared meanwhile (two captures at once), the
    new case folds into it. The evidence rises to 2 with a photo, a safety sighting makes the case a safety
    case, the counts are recounted, every change is an event, and a safety report claims the case's one
    alert. Returns (the card's case part, safety_alerted: the case holds its alert claim)."""
    now = now or mi._now()
    issue = plan["issue"]
    supplier_id = (plan.get("supplier") or {}).get("id")
    joined = bool(found)
    if found:
        case = found
        if has_photo:
            case, _ = _raise_evidence(case, 2, actor, "photo", observation_id=obs["id"])
        if plan["safety"]:
            case = _set_safety(case, actor, observation_id=obs["id"])
        case_event(case["id"], "link", actor, detail={"observation_id": obs["id"]})
    else:
        row = {"yq_item_code": obs.get("yq_item_code"), "issue_type_id": issue["id"],
               "brand_id": (plan.get("brand") or {}).get("id"), "supplier_id": supplier_id,
               "batch_ref": obs.get("batch_ref"), "packaging_version": obs.get("packaging_version"),
               "status": "submitted", "evidence_level": 2 if has_photo else 1, "safety": bool(plan["safety"]),
               "title": case_title(yq_item, obs.get("yq_item_code"), f, issue),
               "first_seen": obs.get("observed_at"), "last_seen": obs.get("observed_at"), "obs_count": 1,
               "shop_count": 1 if (obs.get("shop_customer_id") or obs.get("shop_name")) else 0,
               "created_by": actor or "system", "updated_by": actor or "system"}
        case = (_c().table("market_cases").insert(row).execute().data or [row])[0]
        _c().table("market_observations").update({"case_id": case["id"]}).eq("id", obs["id"]).execute()
        obs["case_id"] = case["id"]
        case_event(case["id"], "created", actor, to_status="submitted",
                   detail={"observation_id": obs["id"], "issue_type_id": issue["id"],
                           "yq_item_code": obs.get("yq_item_code"), "evidence_level": row["evidence_level"]})
        older = [r for r in _open_cases(obs.get("yq_item_code"), issue["id"], now, supplier_id)
                 if int(r["id"]) < int(case["id"])]
        if older:
            case = _fold_into(case, older[0], obs, actor)
            joined = True
    case = {**case, **refresh_case(int(case["id"]))}
    alerted = _claim_and_send(int(case["id"]), actor, int(obs["id"]), defer) if plan["safety"] else False
    return _card_part(case, issue, joined=joined,
                      title=case_title(yq_item, obs.get("yq_item_code"), f, issue)), alerted


def card_case(obs: dict) -> tuple[dict | None, bool]:
    """The case part of a replayed card (a retried capture): the case it is on now; safety_alerted only when
    that case holds its alert claim."""
    case = _live(_case(obs.get("case_id")))
    if not case:
        return None, False
    issue = Names.load().issue(obs.get("issue_type_id") or case.get("issue_type_id"))
    code = obs.get("yq_item_code")
    item = ((mi._catalog_ctx() or {}).get("items") or {}).get(code or "")
    return (_card_part(case, issue, joined=obs.get("result") == "seen_before",
                       title=case_title(item, code, {"title": obs.get("title"), "note": obs.get("note")}, issue)),
            bool(obs.get("safety_flag")) and alert_claimed(int(case["id"])))


# ── a capture that stopped half-way: finish it (the phone's retry, the safety sweep) ──

def _needs_case(obs: dict, names: Names) -> dict | None:
    """The issue a stored sighting's case is filed under when its capture would have put it on a case —
    plan_capture's rule read back from the row (issue_type_id = the issue as filed, safety_flag = the
    safety as decided) — else None."""
    issue = names.issue(obs.get("issue_type_id"))
    if issue and (issue.get("kind") in CASE_KINDS or obs.get("kind") == "complaint" or obs.get("safety_flag")):
        return issue
    return None


def _mentions(e: dict, obs_id: int) -> bool:
    d = e.get("detail") or {}
    return d.get("observation_id") == obs_id or obs_id in (d.get("moved_observations") or [])


def _case_events(case_id: int) -> list[dict]:
    return mi._all(lambda: _c().table("market_case_events").select("id,case_id,event,detail")
                   .eq("case_id", int(case_id)).order("id"))


def capture_finished(obs: dict) -> bool:
    """Did this sighting's capture get to its end? A quality sighting needs its case, a line on that case's
    timeline that names it, and — for a safety report — the case's alert claim. Every other sighting is
    whole once inserted."""
    if not obs.get("issue_type_id"):
        return True
    if not _needs_case(obs, Names.load()):
        return True
    case = _live(_case(obs.get("case_id"))) if obs.get("case_id") else None
    if not case:
        return False
    events = _case_events(int(case["id"]))
    if not any(_mentions(e, int(obs["id"])) for e in events):
        return False
    return not obs.get("safety_flag") or any(e.get("event") == "safety_alert"
                                             and (e.get("detail") or {}).get("email") == "queued" for e in events)


def finish_capture(obs: dict, *, now: datetime, defer=None, actor: str = FINISH_ACTOR) -> tuple[dict | None, bool]:
    """Finish a capture that stopped after its sighting was saved (an error between the insert and the
    alert claim), as the capture would have, and idempotently: a sighting with no case joins the open case
    or opens one (link_case); one already on its case gets the missing timeline line, evidence, safety flag,
    recount and alert claim. A second run changes nothing. Returns (the card's case part, safety_alerted)."""
    names = Names.load()
    issue = _needs_case(obs, names)
    if not issue:
        return None, False
    code = obs.get("yq_item_code")
    item = ((mi._catalog_ctx() or {}).get("items") or {}).get(code or "")
    f = {"title": obs.get("title"), "note": obs.get("note")}
    safety = bool(obs.get("safety_flag"))
    plan = {"issue": issue, "case": True, "safety": safety, "brand": names.brand(obs.get("brand_id")),
            "supplier": names.supplier(obs.get("supplier_id"))}
    has_photo = _has_photo(int(obs["id"]))
    case = _live(_case(obs.get("case_id"))) if obs.get("case_id") else None
    if case is None:
        found = find_open_case(code, issue["id"], now, obs.get("supplier_id"))
        if found:
            _c().table("market_observations").update({"case_id": int(found["id"])}).eq("id", int(obs["id"])).execute()
            obs["case_id"] = int(found["id"])
        return link_case(obs, plan, found=found, yq_item=item, f=f, actor=actor, has_photo=has_photo, defer=defer,
                         now=now)
    if not any(_mentions(e, int(obs["id"])) for e in _case_events(int(case["id"]))):
        case_event(case["id"], "link", actor, detail={"observation_id": int(obs["id"]), "finished": True})
    if has_photo:
        case, _ = _raise_evidence(case, 2, actor, "photo", observation_id=int(obs["id"]))
    if safety:
        case = _set_safety(case, actor, observation_id=int(obs["id"]))
    case = {**case, **refresh_case(int(case["id"]))}
    alerted = _claim_and_send(int(case["id"]), actor, int(obs["id"]), defer) if safety else False
    return _card_part(case, issue, joined=obs.get("result") == "seen_before", title=case_title(item, code, f, issue)), \
        alerted


def sweep_safety(now: datetime) -> dict:
    """The shop jobs' safety sweep (every tick), for the alerts nobody else will finish:
      (1) safety sightings of the last SWEEP_WITHIN with an issue type but no case, older than SWEEP_AFTER (a
          capture may still be running), whose phone may never retry (the app was closed): finished
          (finish_capture: their case and their claim, and the e-mail);
      (2) open safety cases with no alert claim (a capture or a reviewer's flag stopped between the two),
          unchanged for SWEEP_AFTER: claimed and e-mailed;
      (3) open safety cases whose claim never got its e-mail (_resend): sent now, one sender per case.
    The database's one-claim-per-case index makes a race with a phone's retry harmless."""
    if not available():
        return {"skipped": MISSING_HINT}
    c = _c()
    lo, hi = mi._iso(now - SWEEP_WITHIN), mi._iso(now - SWEEP_AFTER)
    out: dict = {"finished": [], "alerted": [], "resent": [], "failed": []}
    loose = mi._all(lambda: c.table("market_observations").select("*").eq("safety_flag", True).is_("case_id", "null")
                    .gte("created_at", lo).lte("created_at", hi).order("id"), 5)
    for o in loose:
        if not o.get("issue_type_id"):
            continue                          # no list to file it under: alerted at capture (alert_without_case)
        try:
            part, _ = finish_capture(o, now=now)
            if part:
                out["finished"].append(int(o["id"]))
        except Exception as e:  # noqa: BLE001 — one stuck sighting never stops the others
            out["failed"].append({"observation_id": o.get("id"), "error": f"{type(e).__name__}: {e}"[:160]})
    open_ = [r for r in mi._all(lambda: c.table("market_cases").select("id,status,safety,merged_into,updated_at")
                                .eq("safety", True).is_("merged_into", "null").order("id"))
             if r.get("status") not in OFF_SAFETY_TAB]
    alerts = _alert_events([int(r["id"]) for r in open_])
    for r in open_:
        evs = alerts.get(int(r["id"]), [])
        if any((e.get("detail") or {}).get("email") == "queued" for e in evs):
            continue                          # claimed: (3) below sees to its e-mail
        changed = mi._parse_ts(r.get("updated_at"))
        if changed and now - changed < SWEEP_AFTER:
            continue
        try:
            if claim_alert(int(r["id"]), SWEEP_ACTOR, None):
                send_safety_alert(int(r["id"]), None)
                out["alerted"].append(int(r["id"]))
        except Exception as e:  # noqa: BLE001
            out["failed"].append({"case_id": r.get("id"), "error": f"{type(e).__name__}: {e}"[:160]})
    _resend(open_, alerts, now, out)
    if out["failed"]:
        out["error"] = f"{len(out['failed'])} safety report(s) could not be finished"
    return out


def _alert_events(case_ids: list[int]) -> dict[int, list[dict]]:
    """The 'safety_alert' lines of these cases (claim, tries, outcomes), oldest first, by case."""
    out: dict[int, list[dict]] = {}
    for chunk in _chunks(sorted(set(case_ids))):
        for e in mi._all(lambda chunk=chunk: _c().table("market_case_events").select("id,case_id,at,detail")
                         .in_("case_id", chunk).eq("event", "safety_alert").order("id")):
            out.setdefault(int(e["case_id"]), []).append(e)
    return out


def _email(e: dict) -> str | None:
    return (e.get("detail") or {}).get("email")


def _resend(open_: list[dict], alerts: dict[int, list[dict]], now: datetime, out: dict) -> None:
    """(3) of the sweep: a claim with no outcome ('sent' / 'not_sent') after it, ORPHAN_AFTER after the claim
    or the last try, never got its e-mail — the request failed after the claim (Starlette drops a failed
    request's background task) or the process restarted before the task ran. It is sent now, with a 'resend'
    line first (the try). A claim a merge or a move carried (carried_from: the case whose claim sends) waits
    for that case: still open, it is that case's own line; merged away or closed, its outcome is read, and
    without one the carried case sends. One sender per case even when two sweeps run at once: each try is
    taken with a conditional write on the case row as this sweep read it (_take_send). After RESEND_LIMIT
    tries without an outcome the sweep stops, says so once on the case ('gave_up') and in its error."""
    roots: dict[int, int] = {}
    for cid, evs in alerts.items():
        claim = next((e for e in evs if _email(e) == "queued"), None)
        root = ((claim or {}).get("detail") or {}).get("carried_from")
        if root and int(root) not in alerts:
            roots[cid] = int(root)
    sent_at_root = {cid for cid, evs in _alert_events(sorted(set(roots.values()))).items()
                    if any(_email(e) in ("sent", "not_sent") for e in evs)} if roots else set()
    for r in open_:
        cid = int(r["id"])
        evs = alerts.get(cid, [])
        claim = next((e for e in evs if _email(e) == "queued"), None)
        if claim is None:
            continue
        carried = (claim.get("detail") or {}).get("carried_from")
        if carried and cid not in roots:
            continue                          # its first case is open: the e-mail is that case's own
        if carried and roots[cid] in sent_at_root:
            continue
        after = [e for e in evs if int(e["id"]) > int(claim["id"])]
        if any(_email(e) in ("sent", "not_sent", "gave_up") for e in after):
            continue
        tries = [e for e in after if _email(e) == "resend"]
        last = mi._parse_ts((tries[-1] if tries else claim).get("at"))
        if last and now - last < ORPHAN_AFTER:
            continue                          # the e-mail may still be on its way
        try:
            if not _take_send(r):
                continue                      # another sweep took this one
            if len(tries) >= RESEND_LIMIT:
                case_event(cid, "safety_alert", SWEEP_ACTOR, detail={"email": "gave_up", "tries": len(tries)})
                out["failed"].append({"case_id": cid, "error": f"the safety e-mail was tried {len(tries) + 1} "
                                                               "times and never confirmed"})
                continue
            case_event(cid, "safety_alert", SWEEP_ACTOR,
                       detail={"email": "resend", "claim_id": int(claim["id"]), "try": len(tries) + 1})
            send_safety_alert(cid, (claim.get("detail") or {}).get("observation_id"))
            out["resent"].append(cid)
        except Exception as e:  # noqa: BLE001 — one stuck alert never stops the others
            out["failed"].append({"case_id": cid, "error": f"{type(e).__name__}: {e}"[:160]})


def _take_send(row: dict) -> bool:
    """One sender per case: a conditional write on the case row exactly as this sweep read it (its
    updated_at). A second sweep holding the same read meets a changed row and leaves the e-mail to the
    first; the value written is always later than the one read, so the write itself changes the row."""
    was = mi._parse_ts(row.get("updated_at"))
    at = mi._now()
    if was and at <= was:
        at = was + timedelta(microseconds=1)
    q = _c().table("market_cases").update({"updated_at": mi._iso(at), "updated_by": SWEEP_ACTOR}).eq("id", int(row["id"]))
    q = q.eq("updated_at", row["updated_at"]) if row.get("updated_at") else q.is_("updated_at", "null")
    got = q.execute().data
    return not (got is not None and len(got) == 0)


# ── the safety alert ──────────────────────────────────────────────────────────

def _later(defer, fn, *args) -> None:
    """Run fn after the answer (defer = BackgroundTasks.add_task) or now; never raises."""
    try:
        if defer is not None:
            defer(fn, *args)
        else:
            fn(*args)
    except Exception as e:  # noqa: BLE001 — an alert must never cost a capture
        log.warning("market quality: safety alert not scheduled: %s", e)


def _claim_and_send(case_id: int, actor: str, observation_id, defer) -> bool:
    """Claim the case's one alert; the caller that wins sends it (after the answer when `defer`). Returns True:
    once claim_alert has returned, the case holds its claim — this caller's or the one that beat it — which is
    what safety_alerted reports. A claim that could not be written at all raises (the phone retries). Callers
    claim as their LAST fallible step; should the e-mail still never go (the request failed afterwards and
    its background task was dropped, or the process restarted), the safety sweep sends it (_resend)."""
    if claim_alert(case_id, actor, observation_id):
        _later(defer, send_safety_alert, case_id, observation_id)
    return True


def _claim_event(case_id: int) -> dict | None:
    """The case's safety-alert claim (its one 'queued' safety_alert event), or None."""
    rows = (_c().table("market_case_events").select("id,detail").eq("case_id", int(case_id)).eq("event", "safety_alert")
            .order("id").limit(50).execute().data or [])
    return next((r for r in rows if (r.get("detail") or {}).get("email") == "queued"), None)


def alert_claimed(case_id: int) -> bool:
    """Does this case hold its safety-alert claim (the one 'queued' safety_alert event)?"""
    return _claim_event(case_id) is not None


def _carry_claim(src_id: int, dst_id: int, actor: str, observation_id) -> None:
    """A merge or a move took an alerted problem to another case: the claim goes along, with no e-mail (the
    problem was alerted under its first case). carried_from names the case whose own claim sends — the first
    of a chain of carries — which is the outcome the safety sweep looks for."""
    claim = _claim_event(int(src_id))
    if claim is None:
        return
    root = (claim.get("detail") or {}).get("carried_from") or int(src_id)
    claim_alert(int(dst_id), actor, observation_id, carried_from=int(root))


def claim_alert(case_id: int, actor: str, observation_id, **extra) -> bool:
    """Claim the case's ONE safety alert by inserting the claim (a 'safety_alert' event, email 'queued');
    True = this caller won and sends the e-mail. The database allows one claim per case
    (market_case_events_one_alert_claim_idx): a claim that loses a race to another phone, a reviewer or the
    sweep is a unique violation, read here as "already claimed" (never a failed capture) — and should that
    index ever be missing, the older of two claims still wins."""
    try:
        mine = case_event(case_id, "safety_alert", actor,
                          detail={"email": "queued", "observation_id": observation_id, **extra})
    except Exception as e:  # noqa: BLE001
        if mi._unique_violation(e):
            return False
        raise
    rows = (_c().table("market_case_events").select("id,detail").eq("case_id", int(case_id)).eq("event", "safety_alert")
            .order("id").limit(50).execute().data or [])
    first = next((r for r in rows if (r.get("detail") or {}).get("email") == "queued"), None)
    return first is None or mine.get("id") is None or str(first.get("id")) == str(mine.get("id"))


def alert_recipients() -> str:
    """ALERT_EMAIL_TO, else the owner logins (settings.owner_emails): the safety alert's recipients."""
    to = (os.getenv("ALERT_EMAIL_TO") or "").strip()
    if to:
        return to
    try:
        from app.config import get_settings
        return ",".join(get_settings().owner_emails)
    except Exception:  # noqa: BLE001
        return ""


def _alert_html(case: dict | None, obs: dict, issue: dict | None) -> tuple[str, str]:
    """The safety alert e-mail. It carries NO free text a person typed — no note, no typed product name, no
    batch or packaging text, no case title (it may hold typed words), no shop, no rep: only the case number,
    the product's code and name from the catalog, the issue's label from the admin list, the time of the
    report (Bahrain) and the link to the case page, where the office reads the rest. A code the catalog does
    not hold now (kept while the catalog could not be read) is not printed: it is 'not identified' here."""
    from app.ai_insights import mask_text
    from app.shop import resolve_code
    raw = (case or {}).get("yq_item_code") or obs.get("yq_item_code")
    ctx = mi._catalog_ctx() or {}
    code = resolve_code(ctx, raw) if raw and ctx.get("items") else None
    item = (ctx.get("items") or {}).get(code) if code else None
    name = (item or {}).get("display_name") if item else None
    product = (f"{code} · {name}" if name else code) if code else ""
    name = name or code or "Product not identified"
    label = (issue or {}).get("label_en") or "Issue not given"
    when = mi._parse_ts(obs.get("observed_at")) or mi._parse_ts((case or {}).get("last_seen")) or mi._now()
    on_case = bool(case and case.get("id"))
    rows = [("Case", f"#{case['id']}" if on_case else
             (f"not on a case yet (report #{obs['id']})" if obs.get("id") else "not on a case yet")),
            ("Product", mask_text(product) or "Not identified (see the case)"),
            ("Issue", mask_text(label)),
            ("Reported", f"{when.astimezone(_BAHRAIN):%d %b %Y, %H:%M} (Bahrain)")]
    base = (os.getenv("APP_BASE_URL") or "").rstrip("/")
    link = (f"{base}/market-intel/cases/{case['id']}" if on_case else f"{base}/market-intel") if base else ""
    body = "".join(f"<tr><td style=\"padding:4px 12px 4px 0;color:#6b7280\">{_html.escape(str(k))}</td>"
                   f"<td style=\"padding:4px 0\">{_html.escape(str(v))}</td></tr>" for k, v in rows)
    html = ("<div style=\"font-family:Arial,sans-serif;font-size:14px;color:#111827\">"
            "<h2 style=\"color:#b91c1c;margin:0 0 8px\">Safety report</h2>"
            f"<table style=\"border-collapse:collapse\">{body}</table>"
            "<p style=\"margin:12px 0 0\">Please review it within one business day."
            + (f" <a href=\"{_html.escape(link)}\">Open the case</a>." if link else "")
            + "</p><p style=\"color:#6b7280;font-size:12px\">YQ Market Intel. What was reported, the shop and the "
            "rep are on the case page, never in this e-mail.</p></div>")
    return f"YQ Market Intel - Safety report: {mask_text(name)} · {mask_text(label)}"[:200], html


def send_safety_alert(case_id: int | None, observation_id: int | None = None) -> dict:
    """E-mail one safety alert (app.emailer.send_html to alert_recipients()). Runs after the answer and
    never raises: the outcome is a second 'safety_alert' event on the case and an audit_log row. The e-mail
    holds no free text at all (_alert_html): the case number, the catalog product, the issue label, the time
    and the link — what was reported, the shop and the rep stay on the case page."""
    out: dict = {"case_id": case_id, "observation_id": observation_id, "emailed": False}
    try:
        case = _case(case_id) if case_id else None
        obs = {}
        if observation_id:
            got = _c().table("market_observations").select("*").eq("id", int(observation_id)).limit(1).execute().data
            obs = (got or [{}])[0]
        issue = None
        try:
            issue = Names.load().issue((case or {}).get("issue_type_id") or obs.get("issue_type_id"))
        except Exception:  # noqa: BLE001 — the alert goes out without the label
            pass
        subject, html = _alert_html(case, obs, issue)
        from app import emailer
        res = emailer.send_html(subject, html, to=alert_recipients() or None) or {}
        out["emailed"] = bool(res.get("emailed"))
        if not out["emailed"]:
            from app.ai_insights import mask_text
            out["reason"] = mask_text(str(res.get("reason") or "not delivered"))[:200]
    except Exception as e:  # noqa: BLE001
        log.warning("market quality: safety alert e-mail failed: %s", e)
        out["reason"] = f"{type(e).__name__}"[:200]
    if case_id:
        try:
            case_event(int(case_id), "safety_alert", "system",
                       detail={"email": "sent" if out["emailed"] else "not_sent",
                               **({"reason": out["reason"]} if out.get("reason") else {})})
        except Exception as e:  # noqa: BLE001
            log.info("market quality: safety alert outcome not recorded: %s", e)
    try:
        from app.audit import log_event
        log_event("system", "market_intel.safety_alert", detail=out)
    except Exception:  # noqa: BLE001
        pass
    return out


def alert_without_case(obs: dict, *, defer=None) -> bool:
    """A safety report with no issue type to file a case under (the lists are empty): alert anyway, once
    for this sighting — better twice than never."""
    _later(defer, send_safety_alert, None, int(obs["id"]))
    return True


# ── reading: the cases list, one case, a rep's view ───────────────────────────

def _in_tab(r: dict, tab: str) -> bool:
    st = r.get("status")
    if tab == "safety":
        return bool(r.get("safety")) and st not in OFF_SAFETY_TAB
    if tab == "new":
        return st == "submitted"
    if tab == "need_info":
        return st == "more_info_required"
    if tab == "identify":
        return not r.get("yq_item_code") and st not in CLOSED_STATUSES
    return True


def _obs_of(case_ids: list[int], cols: str = "*") -> list[dict]:
    out: list[dict] = []
    for chunk in _chunks(sorted(set(case_ids))):
        out += mi._all(lambda chunk=chunk: _c().table("market_observations").select(cols)
                       .in_("case_id", chunk).order("id"))
    return out


def _cover(obs: list[dict], photos: dict[int, list[dict]]) -> dict | None:
    """A case's list picture: the newest sighting's first photo without people, else a flagged one that
    has its blurred copy."""
    flagged = None
    for o in sorted(obs, key=lambda o: (str(o.get("observed_at") or ""), o.get("id") or 0), reverse=True):
        for p in photos.get(int(o["id"]), []):
            if not p.get("has_people_flag"):
                return p
            if flagged is None and p.get("blurred_path"):
                flagged = p
    return flagged


def _case_row(r: dict, names: Names, signed: dict, cover: dict | None, *, mask: bool) -> dict:
    issue = names.issue(r.get("issue_type_id"))
    brand = names.brand(r.get("brand_id"))
    supplier = names.supplier_of(r)
    thumb = mi.photo_urls(cover, signed, master=False)[1] if cover else None
    level = int(r.get("evidence_level") or 1)
    return {"id": r.get("id"), "title": _m(r.get("title"), mask), "status": r.get("status"),
            "status_label": STATUS_LABELS.get(r.get("status"), ""), "evidence_level": level,
            "evidence_label": EVIDENCE_LABELS.get(level, ""), "safety": bool(r.get("safety")),
            "severity": r.get("severity"),
            "issue_type": ({"id": issue.get("id"), "code": issue.get("code"), "label_en": issue.get("label_en"),
                            "label_ar": issue.get("label_ar"), "kind": issue.get("kind")} if issue else None),
            "yq_item_code": r.get("yq_item_code"), "brand": (brand or {}).get("name"), "brand_id": (brand or {}).get("id"),
            "supplier": (supplier or {}).get("name"), "supplier_id": (supplier or {}).get("id"),
            "obs_count": int(r.get("obs_count") or 0), "shop_count": int(r.get("shop_count") or 0),
            "first_seen": r.get("first_seen"), "last_seen": r.get("last_seen"), "thumb_url": thumb,
            "due_date": r.get("due_date"), "assignee": _m(r.get("assignee"), mask), "merged_into": r.get("merged_into")}


def cases(*, tab: str = "all", status: str | None = None, supplier_id=None, brand_id=None, issue_type_id=None,
          date_from=None, date_to=None, offset: int = 0, limit: int = 50, mask: bool = False) -> dict:
    """GET /market-intel/cases (office): the cases of a tab — safety (open safety cases), new (submitted),
    need_info (waiting for the rep), identify (open cases with no product), all — newest sighting first,
    with filters. Every case is read (paged), so `count` is the true total; `tabs` counts each tab over
    every case that is not merged, whatever the filters."""
    _need()
    tab = (tab or "all").strip().lower()
    if tab not in TABS:
        raise MarketIntelError("Unknown tab.")
    if status and status not in STATUSES:
        raise MarketIntelError("Unknown status.")
    sid, bid, iid = _id(supplier_id, "supplier"), _id(brand_id, "brand"), _id(issue_type_id, "issue type")
    d_from, d_to = _day(date_from, "from date"), _day(date_to, "to date")
    try:
        rows = mi._all(lambda: _c().table("market_cases").select("*").order("last_seen", desc=True)
                       .order("id", desc=True))
    except Exception as e:  # noqa: BLE001
        if mi._missing(e):
            raise MarketIntelError(MISSING_HINT, 503) from None
        raise
    rows = [r for r in rows if not r.get("merged_into")]
    tabs = {t: sum(1 for r in rows if _in_tab(r, t)) for t in TABS if t != "all"}
    names = Names.load()
    sel = [r for r in rows if _in_tab(r, tab)]
    if status:
        sel = [r for r in sel if r.get("status") == status]
    if sid:
        want = (names.supplier(sid) or {}).get("id", sid)
        sel = [r for r in sel if (names.supplier_of(r) or {}).get("id") == want]
    if bid:
        want = (names.brand(bid) or {}).get("id", bid)
        sel = [r for r in sel if (names.brand(r.get("brand_id")) or {}).get("id") == want]
    if iid:
        sel = [r for r in sel if r.get("issue_type_id") is not None and int(r["issue_type_id"]) == iid]
    if d_from:
        sel = [r for r in sel if (_bahrain_day(r.get("last_seen")) or "") >= d_from]
    if d_to:
        sel = [r for r in sel if (_bahrain_day(r.get("first_seen")) or "9999") <= d_to]
    total = len(sel)
    off, lim = max(0, int(offset or 0)), max(1, min(int(limit or 50), 200))
    page = sel[off:off + lim]
    obs = _obs_of([int(r["id"]) for r in page], "id,case_id,observed_at")
    photos = mi._photos_for([int(o["id"]) for o in obs])
    by_case: dict[int, list[dict]] = {}
    for o in obs:
        by_case.setdefault(int(o["case_id"]), []).append(o)
    covers = {int(r["id"]): _cover(by_case.get(int(r["id"]), []), photos) for r in page}
    signed = mi.sign_many([k for p in covers.values() if p for k in mi.photo_keys(p, master=False)])
    return {"available": True, "rows": [_case_row(r, names, signed, covers.get(int(r["id"])), mask=mask) for r in page],
            "count": total, "offset": off, "limit": lim, "tabs": tabs}


def next_statuses(case: dict, role: str) -> list[str]:
    """The moves this login may make on the case now (empty for anyone but a reviewer, or a merged case)."""
    if role not in REVIEW_ROLES or case.get("merged_into"):
        return []
    if case.get("status") == "closed" and role != "admin":
        return []
    return list(NEXT_STATUS.get(case.get("status"), ()))


def _actions_for(case_ids: list[int]) -> dict[int, list[dict]]:
    links: list[dict] = []
    for chunk in _chunks(sorted(set(case_ids))):
        links += mi._all(lambda chunk=chunk: _c().table("supplier_action_cases").select("action_id,case_id")
                         .in_("case_id", chunk).order("action_id"))
    ids = sorted({int(x["action_id"]) for x in links})
    acts: dict[int, dict] = {}
    for chunk in _chunks(ids):
        for a in mi._all(lambda chunk=chunk: _c().table("supplier_actions").select("*").in_("id", chunk).order("id")):
            acts[int(a["id"])] = a
    out: dict[int, list[dict]] = {}
    for x in links:
        a = acts.get(int(x["action_id"]))
        if a:
            out.setdefault(int(x["case_id"]), []).append(a)
    return out


def _action_out(a: dict, names: Names, *, mask: bool) -> dict:
    s = names.supplier(a.get("supplier_id"))
    return {"id": a.get("id"), "supplier_id": a.get("supplier_id"), "supplier": (s or {}).get("name"),
            "request": _m(a.get("request"), mask), "owner": _m(a.get("owner"), mask), "due_date": a.get("due_date"),
            "status": a.get("status") or "open", "supplier_response": _m(a.get("supplier_response"), mask),
            "resolution_check": _m(a.get("resolution_check"), mask), "created_at": a.get("created_at"),
            "updated_at": a.get("updated_at"), "created_by": _who(a.get("created_by"))}


def _event_out(e: dict, *, mask: bool) -> dict:
    return {"id": e.get("id"), "at": e.get("at") or e.get("created_at"), "event": e.get("event"),
            "actor": _who(e.get("actor")), "from_status": e.get("from_status"), "to_status": e.get("to_status"),
            "detail": _m(e.get("detail") or {}, mask)}


def _suggestions(case: dict, names: Names, *, mask: bool) -> list[dict]:
    """Other open cases of the same product or the same issue: what this one may be merged with."""
    c, seen, out = _c(), {int(case["id"])}, []
    for col, val in (("yq_item_code", case.get("yq_item_code")), ("issue_type_id", case.get("issue_type_id"))):
        if not val:
            continue
        for r in (c.table("market_cases").select("*").eq(col, val).is_("merged_into", "null")
                  .order("last_seen", desc=True).order("id", desc=True).limit(20).execute().data or []):
            if int(r["id"]) in seen or r.get("status") in CLOSED_STATUSES:
                continue
            seen.add(int(r["id"]))
            same_product = bool(case.get("yq_item_code")) and r.get("yq_item_code") == case.get("yq_item_code")
            same_issue = r.get("issue_type_id") == case.get("issue_type_id")
            row = _case_row(r, names, {}, None, mask=mask)
            row.update(same_product=same_product, same_issue=same_issue)
            out.append(row)
    out.sort(key=lambda x: (not (x["same_product"] and x["same_issue"]), not x["same_product"]))
    return out[:10]


def case_detail(case_id: int, *, role: str, mask: bool = False, raw: bool = False) -> dict:
    """GET /market-intel/cases/{id} (office): the case, its timeline (events), its sightings with photos
    (masters + thumbnails; a people-flagged photo only blurred unless `raw`, an admin's ?raw=1), its
    supplier actions and the open cases it may duplicate (suggestions)."""
    _need()
    case = _case(case_id)
    if not case:
        raise MarketIntelError("That case does not exist.", 404)
    names = Names.load()
    obs = mi._all(lambda: _c().table("market_observations").select("*").eq("case_id", int(case_id))
                  .order("observed_at", desc=True).order("id", desc=True))
    photos = mi._photos_for([int(o["id"]) for o in obs])
    signed = mi.sign_many([k for ps in photos.values() for p in ps for k in mi.photo_keys(p, master=True, raw=raw)])
    reps = mi._salesmen_names()
    events = mi._all(lambda: _c().table("market_case_events").select("*").eq("case_id", int(case_id)).order("id"))
    actions = _actions_for([int(case_id)]).get(int(case_id), [])
    items = (mi._catalog_ctx() or {}).get("items") or {}
    row = _case_row(case, names, {}, None, mask=mask)
    row.update(batch_ref=_m(case.get("batch_ref"), mask), packaging_version=_m(case.get("packaging_version"), mask),
               verification_method=case.get("verification_method"),
               verification_label=VERIFICATION_LABELS.get(case.get("verification_method") or ""),
               verification_reason=_m(case.get("verification_reason"), mask),
               status_reason=_m(case.get("status_reason"), mask), changed_at=case.get("changed_at"),
               changed_by=_who(case.get("changed_by")) if case.get("changed_by") else None,
               created_at=case.get("created_at"), next_statuses=next_statuses(case, role),
               # the levels this login may raise the evidence to now (only up, only what the case's history
               # supports; nothing on a closed case, which is read-only until an admin reopens it)
               evidence_next=(list(range(int(case.get("evidence_level") or 1) + 1,
                                         evidence_cap(case, {str(e["to_status"]) for e in events
                                                             if e.get("to_status")}) + 1))
                              if role in REVIEW_ROLES and not case.get("merged_into")
                              and case.get("status") != "closed" else []),
               yq_item=mi.yq_card(items.get(case.get("yq_item_code") or "")) if case.get("yq_item_code") else None,
               thumb_url=None)
    out_obs = []
    for o in obs:
        x = mi._obs_out(o, photos.get(int(o["id"]), []), signed, reps, office=True, actor="", mask=mask,
                        master=True, raw=raw)
        issue = names.issue(o.get("issue_type_id"))
        x["issue_label"] = (issue or {}).get("label_en")
        out_obs.append(x)
    first = next((p for o in out_obs for p in o["photos"] if p.get("thumb_url")), None)
    row["thumb_url"] = first["thumb_url"] if first else None
    return {"available": True, "case": row, "events": [_event_out(e, mask=mask) for e in events],
            "observations": out_obs, "actions": [_action_out(a, names, mask=mask) for a in actions],
            "suggestions": _suggestions(case, names, mask=mask)}


def _open_question(case: dict, obs_id: int, events: list[dict]) -> str | None:
    """The office's question this sighting's rep still has to answer: the case waits for more information,
    the latest question meant for everyone on the case or for this sighting, and no answer from this
    sighting after it."""
    if case.get("status") != "more_info_required":
        return None
    q = a = None
    for e in sorted(events, key=lambda e: int(e.get("id") or 0)):
        d = e.get("detail") or {}
        if e.get("event") == "question" and d.get("observation_id") in (None, obs_id):
            q = e
        elif e.get("event") == "answer" and d.get("observation_id") == obs_id:
            a = e
    if not q or (a and int(a.get("id") or 0) > int(q.get("id") or 0)):
        return None
    return (q.get("detail") or {}).get("text") or None


def rep_case_info(obs_rows: list[dict]) -> dict[int, dict]:
    """{observation id: {"case_status_words", "open_question"}} for a rep's own sightings on cases: the
    status in plain words (of the case it is on now, merges followed) and the office's open question to
    him. Nothing else about the case — its title, other sightings, shops or reps — reaches a rep."""
    if not available():
        return {}
    ids = sorted({int(o["case_id"]) for o in obs_rows if o.get("case_id")})
    if not ids:
        return {}
    rows: dict[int, dict] = {}
    for chunk in _chunks(ids):
        for r in mi._all(lambda chunk=chunk: _c().table("market_cases").select("id,status,merged_into")
                         .in_("id", chunk).order("id")):
            rows[int(r["id"])] = r
    live: dict[int, dict] = {}
    for cid, r in list(rows.items()):          # following a merge may add the target to `rows`
        cur, hops = r, 5
        while cur and cur.get("merged_into") and hops > 0:
            nxt = rows.get(int(cur["merged_into"])) or _case(cur["merged_into"])
            if nxt:
                rows[int(nxt["id"])] = nxt
            cur, hops = nxt, hops - 1
        if cur:
            live[cid] = cur
    events: dict[int, list[dict]] = {}
    for chunk in _chunks(sorted({int(c["id"]) for c in live.values()})):
        for e in mi._all(lambda chunk=chunk: _c().table("market_case_events").select("id,case_id,event,detail")
                         .in_("case_id", chunk).in_("event", ["question", "answer"]).order("id")):
            events.setdefault(int(e["case_id"]), []).append(e)
    out: dict[int, dict] = {}
    for o in obs_rows:
        case = live.get(int(o["case_id"])) if o.get("case_id") else None
        if case:
            out[int(o["id"])] = {"case_status_words": STATUS_WORDS.get(case.get("status"), ""),
                                 "open_question": _open_question(case, int(o["id"]), events.get(int(case["id"]), []))}
    return out


# ── deciding: the workflow, questions, merges, supplier actions ───────────────

_CASE_EDITABLE = ("title", "severity", "assignee", "due_date", "safety", "yq_item_code", "brand_id", "supplier_id")
_VERIFIED_OR_LATER = ("verified", "action_requested", "resolved", "closed")


def _reviewer(role: str) -> None:
    if role not in REVIEW_ROLES:
        raise MarketIntelError("Only the office can change quality cases.", 403)


def _open_case_for_change(case_id: int) -> dict:
    _need()
    case = _case(case_id)
    if not case:
        raise MarketIntelError("That case does not exist.", 404)
    if case.get("merged_into"):
        raise MarketIntelError(f"This case was merged into #{case['merged_into']}; change that one.", 409)
    return case


def _clean_case_field(k: str, v, names: Names):
    if k == "title":
        t = clean_text(v, 160)
        if not t:
            raise MarketIntelError("A case needs a title.")
        return t
    if k == "severity":
        if v in (None, ""):
            return None
        try:
            s = int(v)
        except (TypeError, ValueError):
            raise MarketIntelError("Severity is 1, 2 or 3.") from None
        if s not in (1, 2, 3):
            raise MarketIntelError("Severity is 1, 2 or 3.")
        return s
    if k == "assignee":
        return clean_text(v, 120)
    if k == "due_date":
        return _day(v, "due date")
    if k == "safety":
        return _flag(v, "safety")
    if k == "yq_item_code":
        return mi._clean_edit("yq_item_code", v) if v not in (None, "") else None
    if k == "brand_id":
        if v in (None, ""):
            return None
        b = names.brand(_id(v, "brand"))
        if not b:
            raise MarketIntelError("Unknown brand.")
        return int(b["id"])
    if k == "supplier_id":
        if v in (None, ""):
            return None
        s = names.supplier(_id(v, "supplier"))
        if not s:
            raise MarketIntelError("Unknown supplier.")
        return int(s["id"])
    return v


def _cas(case_id: int, status: str, upd: dict) -> None:
    """Write `upd` only if the case is still in `status` (two reviewers at once: the second gets 409)."""
    got = _c().table("market_cases").update(upd).eq("id", int(case_id)).eq("status", status).execute().data
    if got is not None and len(got) == 0:
        raise MarketIntelError("Someone changed this case meanwhile. Reload and try again.", 409)


def _linked_actions(case_id: int) -> list[dict]:
    return _actions_for([int(case_id)]).get(int(case_id), [])


def _resolve_actions(case_id: int, actor: str) -> list[int]:
    """A case moved to Resolved: each of its supplier actions that holds the supplier's response AND YQ's
    resolution check is resolved with it (otherwise the supplier workbook lists it as still open, awaiting
    YQ's check). An incomplete or cancelled action keeps its status. Every case the action covers gets an
    'action' event. Returns the actions resolved."""
    done: list[int] = []
    for a in _linked_actions(case_id):
        if not (a.get("supplier_response") and a.get("resolution_check")) or a.get("status") in ("resolved", "cancelled"):
            continue
        got = (_c().table("supplier_actions").update({"status": "resolved", "updated_at": mi._iso(), "updated_by": actor})
               .eq("id", int(a["id"])).eq("status", a.get("status") or "open").execute().data)
        if got is not None and len(got) == 0:
            continue                                  # changed meanwhile: left as it is now
        links = mi._all(lambda aid=int(a["id"]): _c().table("supplier_action_cases").select("action_id,case_id")
                        .eq("action_id", aid).order("case_id"))
        for x in links:
            case_event(int(x["case_id"]), "action", actor,
                       detail={"action_id": int(a["id"]), "why": "the case was resolved",
                               "changes": {"status": {"from": a.get("status") or "open", "to": "resolved"}}})
        done.append(int(a["id"]))
    return done


def update_case(case_id: int, changes: dict, actor: str, role: str, *, defer=None) -> dict:
    """PATCH /market-intel/cases/{id} (reviewers): fields, the evidence level (up only, and only as far as the
    case's history supports: evidence_cap — 3 comes with the move to Verified, 4 once it went to Action
    requested, 5 once it went to Resolved; 409 otherwise) and/or one status move along NEXT_STATUS with what
    the move needs — verified: a method and what it found (evidence at least 3); not substantiated: a reason;
    back to under review from not substantiated or closed: a reason (closed: an admin only); action requested:
    a linked supplier action; resolved: a supplier action with the supplier's response and YQ's check. A
    CLOSED case takes nothing but the admin's reopen (409 CLOSED_READ_ONLY). Every change is an event."""
    _reviewer(role)
    case = _open_case_for_change(case_id)
    names = Names.load()
    reason = clean_text(changes.get("reason"), 500)
    upd: dict = {}
    diffs: dict = {}
    for k in _CASE_EDITABLE:
        if k in changes and not (k in ("title", "safety") and changes[k] is None):   # null clears the others
            v = _clean_case_field(k, changes[k], names)
            if v != case.get(k):
                upd[k] = v
                diffs[k] = {"from": case.get(k), "to": v}
    cur = case.get("status")
    new = changes.get("status")
    level = int(case.get("evidence_level") or 1)
    want_level = level
    if changes.get("evidence_level") not in (None, ""):
        try:
            want_level = int(changes["evidence_level"])
        except (TypeError, ValueError):
            raise MarketIntelError("The evidence level is 1 to 5.") from None
        if want_level not in EVIDENCE_LABELS:
            raise MarketIntelError("The evidence level is 1 to 5.")
        if want_level < level:
            raise MarketIntelError(f"The evidence level only moves up (it is {EVIDENCE_LABELS[level]}).", 409)
    method = str(changes.get("verification_method") or "").strip().lower() or None
    why = clean_text(changes.get("verification_reason"), 500)
    new = new or None
    moved = new is not None and new != cur
    if moved:
        if new not in STATUSES:
            raise MarketIntelError("Unknown status.")
        if new not in NEXT_STATUS.get(cur, ()):
            raise MarketIntelError(f"A case that is {STATUS_LABELS.get(cur, cur)} cannot move to "
                                   f"{STATUS_LABELS[new]}.", 409)
        if cur == "closed" and role != "admin":
            raise MarketIntelError("Only an admin can reopen a closed case.", 403)
        if (cur, new) in REASON_REQUIRED and not reason:
            raise MarketIntelError("Say why the case is looked at again (a reason is required).")
        if new == "not_substantiated" and not reason:
            raise MarketIntelError("Say why it is not substantiated (a reason is required).")
        if new == "verified":
            if method not in VERIFICATION_METHODS:
                raise MarketIntelError("Choose how it was verified: sample inspected, photos sufficient or "
                                       "several shops.")
            if not why:
                raise MarketIntelError("Say what the verification found.")
            want_level = max(want_level, EVIDENCE_VERIFIED)
        if new == "action_requested" and not _linked_actions(int(case_id)):
            raise MarketIntelError("Request the supplier action first (it moves the case on its own).", 409)
        if new == "resolved" and not any(a.get("supplier_response") and a.get("resolution_check")
                                         and a.get("status") != "cancelled" for a in _linked_actions(int(case_id))):
            raise MarketIntelError("A case is resolved only when a supplier action has the supplier's response "
                                   "and YQ's check.", 409)
        upd.update(status=new, status_reason=reason, changed_at=mi._iso(), changed_by=actor)
    if (method is not None or why is not None) and not (moved and new == "verified"):
        # a verification is recorded with the move to Verified; afterwards it can be corrected
        if cur not in _VERIFIED_OR_LATER:
            raise MarketIntelError("The verification is recorded with the move to Verified.")
        if method is not None and method not in VERIFICATION_METHODS:
            raise MarketIntelError("Choose how it was verified: sample inspected, photos sufficient or several shops.")
    if method is not None or why is not None:
        for k, v in (("verification_method", method), ("verification_reason", why)):
            if v is not None and v != case.get(k):
                upd[k] = v
                if not (moved and new == "verified"):
                    diffs[k] = {"from": case.get(k), "to": v}
    if cur == "closed" and (diffs or want_level > level):
        # a closed case takes one change: an admin's reopen (above), on its own; nothing else — no field,
        # no supplier, no safety flag (a fresh e-mail), no verification, no evidence
        raise MarketIntelError(CLOSED_READ_ONLY, 409)
    if want_level > level:
        # a level the case's history (with this move) does not support is refused: 3 needs the move to
        # Verified, 4 the supplier asked (it went to Action requested), 5 the case Resolved
        cap = evidence_cap({**case, **{k: upd[k] for k in ("status", "verification_method") if k in upd}},
                           _reached(int(case_id)) | {str(cur)})
        if want_level > cap:
            raise MarketIntelError(EVIDENCE_NEEDS[cap + 1], 409)
    if not upd and want_level <= level:
        return {"ok": True, "changed": False, "case_id": int(case_id), "status": cur, "evidence_level": level,
                "safety_alerted": False}
    fields = sorted(upd)
    if upd:
        upd.update(updated_at=mi._iso(), updated_by=actor)
        _cas(int(case_id), cur, upd)
        if diffs:
            case_event(int(case_id), "edit", actor,
                       detail={**diffs, **({"reason": reason} if reason and not moved else {})})
        if moved:
            detail = {"reason": reason, "verification_method": method if new == "verified" else None,
                      "verification_reason": why if new == "verified" else None}
            case_event(int(case_id), "status", actor, from_status=cur, to_status=new,
                       detail={k: v for k, v in detail.items() if v})
            if new == "resolved":
                fields += [f"action:{a}" for a in _resolve_actions(int(case_id), actor)]
    raised = False
    if want_level > level:
        # its own guarded write (evidence_level < the new level): a level someone raised meanwhile stays
        _, raised = _raise_evidence(case, want_level, actor, "verified" if moved and new == "verified" else "reviewer")
        if raised:
            fields.append("evidence_level")
    # the case is read BEFORE the claim: the claim is this request's last fallible step, so its e-mail (a
    # background task, dropped if the request then failed) is not lost to a later error
    now_row = _case(case_id) or {}
    alerted = _claim_and_send(int(case_id), actor, None, defer) if upd.get("safety") is True else False
    return {"ok": True, "changed": bool(upd) or raised, "case_id": int(case_id),
            "status": now_row.get("status", upd.get("status", cur)),
            "evidence_level": int(now_row.get("evidence_level") or want_level), "safety_alerted": alerted,
            "fields": sorted(fields)}


def ask_question(case_id: int, text, actor: str, role: str, *, observation_id=None) -> dict:
    """POST /market-intel/cases/{id}/question (reviewers): ask the rep(s) for more — the case moves to
    More information required; observation_id narrows the question to that sighting's rep."""
    _reviewer(role)
    case = _open_case_for_change(case_id)
    q = clean_text(text, 500)
    if not q:
        raise MarketIntelError("Write the question.")
    oid = _id(observation_id, "sighting")
    if oid is not None:
        got = _c().table("market_observations").select("id,case_id").eq("id", oid).limit(1).execute().data or []
        if not got or str(got[0].get("case_id")) != str(case_id):
            raise MarketIntelError("That sighting is not on this case.")
    cur = case.get("status")
    if cur not in QUESTION_FROM:
        raise MarketIntelError(f"A case that is {STATUS_LABELS.get(cur, cur)} cannot ask for more information.", 409)
    moved = cur != "more_info_required"
    if moved:
        _cas(int(case_id), cur, {"status": "more_info_required", "status_reason": "question to the rep",
                                 "changed_at": mi._iso(), "changed_by": actor, "updated_at": mi._iso(),
                                 "updated_by": actor})
    case_event(int(case_id), "question", actor, detail={"text": q, "observation_id": oid})
    if moved:
        case_event(int(case_id), "status", actor, from_status=cur, to_status="more_info_required")
    return {"ok": True, "case_id": int(case_id), "status": "more_info_required"}


def _latest_question(events: list[dict], obs_id: int) -> dict | None:
    """The office's latest question to this sighting's rep (to it, or to everyone on the case)."""
    return next((e for e in sorted(events, key=lambda e: int(e.get("id") or 0), reverse=True)
                 if e.get("event") == "question" and (e.get("detail") or {}).get("observation_id") in (None, obs_id)),
                None)


def _answer_after_question(events: list[dict], obs_id: int) -> dict | None:
    """This sighting's answer to that latest question, or None."""
    q = _latest_question(events, obs_id)
    if q is None:
        return None
    return next((e for e in sorted(events, key=lambda e: int(e.get("id") or 0), reverse=True)
                 if e.get("event") == "answer" and (e.get("detail") or {}).get("observation_id") == obs_id
                 and int(e.get("id") or 0) > int(q.get("id") or 0)), None)


def _answer_reason(obs_id: int) -> str:
    return f"{ANSWER_REASON} (MI-{int(obs_id)})"


def _answer_move(actor: str, obs_id: int) -> dict:
    at = mi._iso()
    return {"status": "under_review", "status_reason": _answer_reason(obs_id), "changed_at": at, "changed_by": actor,
            "updated_at": at, "updated_by": actor}


def _moved_by_answer(case: dict, obs_id: int, actor: str) -> bool:
    """Is the case Under review because THIS sighting's answer moved it (its rep, its reason)?"""
    return (case.get("status") == "under_review" and case.get("status_reason") == _answer_reason(obs_id)
            and mi._ci(case.get("changed_by")) == actor)


def _undo_answer(case: dict, actor: str, before: dict, photo_id, stored: dict | None) -> None:
    """An answer that could not be written after its move: the case goes back to waiting for it (only while
    this rep's move is still what it holds), the photo row and the uploaded files go — so the rep's retry finds
    the question open. Best effort; should the case stay moved, the retry resumes it (answer)."""
    try:
        _c().table("market_cases").update({"status": "more_info_required", **before, "updated_at": mi._iso(),
                                           "updated_by": actor}) \
            .eq("id", int(case["id"])).eq("status", "under_review").eq("changed_by", actor).execute()
    except Exception as e:  # noqa: BLE001
        log.warning("market quality: an answer's move was not put back: %s", e)
    if photo_id:
        try:
            _c().table("market_photos").delete().eq("id", int(photo_id)).execute()
        except Exception as e:  # noqa: BLE001
            log.warning("market quality: an answer's photo row was not removed: %s", e)
    if stored:
        _discard(stored)


def _finish_answer(case: dict, obs_id: int, actor: str, ans: dict, events: list[dict]) -> str:
    """A recorded answer sent again (the phone resent it): what a stopped attempt left out is written now,
    each part only when missing — the move to Under review with its timeline line, the photo's evidence.
    Returns the case's status."""
    status = case.get("status")
    line = any(e.get("event") == "status" and e.get("to_status") == "under_review"
               and int(e.get("id") or 0) > int(ans.get("id") or 0) for e in events)
    write_line = False
    if status == "more_info_required":
        try:
            _cas(int(case["id"]), "more_info_required", _answer_move(actor, obs_id))
            status, write_line = "under_review", True
        except MarketIntelError:
            status = (_case(case["id"]) or {}).get("status") or status        # the office moved it meanwhile
    elif not line and _moved_by_answer(case, obs_id, actor):
        write_line = True
    if write_line:
        case_event(int(case["id"]), "status", actor, from_status="more_info_required", to_status="under_review")
    photo_id = (ans.get("detail") or {}).get("photo_id")
    if photo_id:
        _raise_evidence(case, 2, actor, "photo", observation_id=int(obs_id), photo_id=photo_id)
    return status


def answer(obs_id: int, text, photo: tuple[str, bytes] | None, actor: str, *, role_name: str | None = None,
           now: datetime | None = None) -> dict:
    """POST /market-intel/observations/{id}/answer (the rep who made the sighting): his reply to the office's
    open question, with an optional photo appended to the sighting. The case goes back to Under review.
    Safe to retry, whatever stopped an attempt:
      * the move comes first — an answer that lost a race (the office moved the case meanwhile) leaves no
        photo row, no event and no file behind (409);
      * a photo row or answer that then fails puts the move back and removes the upload (_undo_answer), so
        the retry finds the question open; a case left moved anyway (the undo failed too, or the process
        stopped) is resumed by the retry: the move is this sighting's (_moved_by_answer), the answer is not
        written yet — it is written now;
      * the same answer again once it was written (the phone resent it) is answered as saved, finishing what
        the attempt left out (_finish_answer) — never a 409."""
    _need()
    now = now or mi._now()
    actor = (actor or "").strip().lower()
    oid = int(obs_id)
    got = _c().table("market_observations").select("*").eq("id", oid).limit(1).execute().data or []
    if not got:
        raise MarketIntelError("That report does not exist.", 404)
    obs = got[0]
    if mi._ci(obs.get("created_by")) != actor or not actor:
        raise MarketIntelError("Only the rep who sent this report can answer.", 403)
    case = _live(_case(obs.get("case_id")))
    events = mi._all(lambda: _c().table("market_case_events").select("id,case_id,event,detail,to_status")
                     .eq("case_id", int(case["id"])).in_("event", ["question", "answer", "status"])
                     .order("id")) if case else []
    reply = clean_text(text, 1000)
    question = _open_question(case, oid, events) if case else None
    resume = False
    if not question and case:
        done = _answer_after_question(events, oid)
        if done is not None and ((done.get("detail") or {}).get("text") or None) == (reply or None):
            status = _finish_answer(case, oid, actor, done, events)
            return {"ok": True, "observation_id": oid, "case_status_words": STATUS_WORDS.get(status, ""),
                    "photo": bool((done.get("detail") or {}).get("photo_id")), "replayed": True}
        resume = done is None and _latest_question(events, oid) is not None and _moved_by_answer(case, oid, actor)
    if not question and not resume:
        raise MarketIntelError("There is no open question on this report.", 409)
    if not reply and not photo:
        raise MarketIntelError("Write an answer or add a photo.")
    role = str(role_name or "product").strip().lower() or "product"
    if role not in PHOTO_ROLES:
        raise MarketIntelError("A photo role is product, packaging, label_batch, receipt or other.")
    processed = mi.process_photo(photo[1], photo[0]) if photo else None     # validated before any write
    stored = None
    if processed:
        try:
            stored = mi._upload(processed, now, thumb=True)   # the files first: no database row yet
        except Exception as e:  # noqa: BLE001
            log.warning("market quality: answer photo upload failed: %s", e)
            raise MarketIntelError("Could not save the photo. Please try again.", 503) from None
    if resume:
        before = {"status_reason": "question to the rep"}
    else:
        before = {k: case.get(k) for k in ("status_reason", "changed_at", "changed_by")}
        try:
            # the move first: an answer that lost a race leaves no photo row and no event behind — only the
            # uploaded files, which are removed again
            _cas(int(case["id"]), "more_info_required", _answer_move(actor, oid))
        except MarketIntelError:
            if stored:
                _discard(stored)
            raise
    photo_id = None
    try:
        if stored:
            have = _c().table("market_photos").select("id,position").eq("observation_id", oid).execute().data or []
            pos = max([int(p.get("position") or 0) for p in have] + [-1]) + 1
            row = {"observation_id": oid, "bucket": mi.BUCKET, "path": stored["path"], "position": pos,
                   "width": processed["width"], "height": processed["height"], "bytes": processed["bytes"],
                   "phash": processed["phash"], "role": role, "sha256": processed["sha256"],
                   "thumb_path": stored["thumb_path"], "caption": "answer to the office's question"}
            photo_id = ((_c().table("market_photos").insert(row).execute().data or [row])[0]).get("id")
        case_event(int(case["id"]), "answer", actor, detail={"observation_id": oid, "text": reply, "photo_id": photo_id})
    except Exception as e:  # noqa: BLE001 — nothing of the answer is written: undo the move, the retry starts over
        _undo_answer(case, actor, before, photo_id, stored)
        log.warning("market quality: an answer was not saved: %s", e)
        raise MarketIntelError("Could not save your answer. Please try again.", 503) from None
    case_event(int(case["id"]), "status", actor, from_status="more_info_required", to_status="under_review")
    if photo_id:
        _raise_evidence(case, 2, actor, "photo", observation_id=oid, photo_id=photo_id)
    return {"ok": True, "observation_id": oid, "case_status_words": STATUS_WORDS["under_review"],
            "photo": bool(photo_id)}


def _discard(stored: dict) -> None:
    """Best effort: remove an upload no row will ever point at."""
    try:
        _c().storage.from_(mi.BUCKET).remove([p for p in (stored.get("path"), stored.get("thumb_path")) if p])
    except Exception as e:  # noqa: BLE001
        log.info("market quality: an unused upload was not removed: %s", e)


def _takes_sightings(dst: dict) -> None:
    """A merge or a move lands on an OPEN case: a closed or not-substantiated one takes no sightings (a safety
    case merged into one would drop off the Safety tab and out of every report)."""
    if dst.get("status") in CLOSED_STATUSES:
        raise MarketIntelError(f"Case #{dst['id']} is {STATUS_LABELS.get(dst.get('status'), dst.get('status'))}: it "
                               "takes no sightings. Pick an open case (or reopen that one first).", 409)


def _keeps_sightings(src: dict) -> None:
    """...and a closed or not-substantiated case keeps its sightings: it is not merged away and none is moved
    out until it is reopened — a closed one by an admin (CLOSED_READ_ONLY), a not-substantiated one by a
    reviewer, with a reason. (The case page offers no merge or move on such a case.)"""
    if src.get("status") == "closed":
        raise MarketIntelError(CLOSED_READ_ONLY, 409)
    if src.get("status") == "not_substantiated":
        raise MarketIntelError("A not-substantiated case keeps its sightings: reopen it first (Not substantiated → "
                               "Under review, with a reason).", 409)


def merge_case(case_id: int, into_id, actor: str, role: str) -> dict:
    """POST /market-intel/cases/{id}/merge (reviewers): this case duplicates `into_id`, an OPEN case. Its
    sightings and supplier actions move there, it closes as merged (merged_into), the target keeps the higher
    evidence level and the safety flag — and the alert claim: a problem already alerted is not e-mailed again,
    by a later sighting or the safety sweep. Both are recounted and both histories say so. A closed or
    not-substantiated case is not merged away (_keeps_sightings)."""
    _reviewer(role)
    src = _open_case_for_change(case_id)
    _keeps_sightings(src)
    dst = _live(_case(_id(into_id, "case")))
    if not dst:
        raise MarketIntelError("The case to merge into does not exist.", 404)
    if int(dst["id"]) == int(src["id"]):
        raise MarketIntelError("Pick another case to merge into.")
    _takes_sightings(dst)
    moved = [int(o["id"]) for o in mi._all(lambda: _c().table("market_observations").select("id")
                                           .eq("case_id", int(src["id"])).order("id"))]
    _cas(int(src["id"]), src.get("status"), {"merged_into": int(dst["id"]), "status": "closed",
                                              "status_reason": f"merged into #{dst['id']}", "changed_at": mi._iso(),
                                              "changed_by": actor, "updated_at": mi._iso(), "updated_by": actor})
    if moved:
        _c().table("market_observations").update({"case_id": int(dst["id"])}).eq("case_id", int(src["id"])).execute()
    have = {int(a["id"]) for a in _linked_actions(int(dst["id"]))}
    for a in _linked_actions(int(src["id"])):
        if int(a["id"]) not in have:
            _c().table("supplier_action_cases").insert({"action_id": int(a["id"]), "case_id": int(dst["id"])}).execute()
    case_event(int(src["id"]), "merge", actor, from_status=src.get("status"), to_status="closed",
               detail={"into": int(dst["id"]), "moved_observations": moved})
    case_event(int(dst["id"]), "merge", actor, detail={"from": int(src["id"]), "moved_observations": moved})
    # the target takes the higher evidence only as far as its own state allows (evidence_cap): a verified
    # duplicate's level 3 does not make an unverified target "inspected and verified"
    dst, _ = _raise_evidence(dst, min(int(src.get("evidence_level") or 1), evidence_cap(dst)), actor, "merge",
                             from_case=int(src["id"]))
    if src.get("safety") and not dst.get("safety"):
        dst = _set_safety(dst, actor, from_case=int(src["id"]))
    _carry_claim(int(src["id"]), int(dst["id"]), actor, None)      # no e-mail: carried, not new
    refresh_case(int(dst["id"]))
    refresh_case(int(src["id"]))
    return {"ok": True, "case_id": int(src["id"]), "merged_into": int(dst["id"]), "moved_observations": len(moved)}


def move_observation(obs_id: int, case_id, actor: str, role: str) -> dict:
    """POST /market-intel/observations/{id}/move (reviewers): put one sighting on another OPEN case (or, for a
    sighting on no case yet — an old complaint — on a case). Both cases are recounted, both histories say so.
    Like a merge, the sighting carries its photo's evidence and its safety flag (the case it joins goes on the
    Safety tab; no second e-mail: the problem was alerted already). A closed or not-substantiated case keeps
    its sightings (_keeps_sightings)."""
    _reviewer(role)
    _need()
    got = _c().table("market_observations").select("*").eq("id", int(obs_id)).limit(1).execute().data or []
    if not got:
        raise MarketIntelError("That sighting does not exist.", 404)
    obs = got[0]
    dst = _live(_case(_id(case_id, "case")))
    if not dst:
        raise MarketIntelError("That case does not exist.", 404)
    src_id = int(obs["case_id"]) if obs.get("case_id") else None
    if src_id == int(dst["id"]):
        return {"ok": True, "changed": False, "observation_id": int(obs_id), "case_id": int(dst["id"])}
    _takes_sightings(dst)
    src = _case(src_id) if src_id else None
    if src and not src.get("merged_into"):
        _keeps_sightings(src)                                  # (a merged-away case's sighting may still move)
    _c().table("market_observations").update({"case_id": int(dst["id"])}).eq("id", int(obs_id)).execute()
    case_event(int(dst["id"]), "move", actor, detail={"observation_id": int(obs_id), "from_case": src_id})
    if src_id:
        case_event(src_id, "move", actor, detail={"observation_id": int(obs_id), "to_case": int(dst["id"])})
    if _has_photo(int(obs_id)):
        dst, _ = _raise_evidence(dst, 2, actor, "photo", observation_id=int(obs_id))
    if obs.get("safety_flag"):
        dst = _set_safety(dst, actor, observation_id=int(obs_id))
        if src_id:                                    # alerted already: the claim moves along, no second e-mail
            _carry_claim(src_id, int(dst["id"]), actor, int(obs_id))
    refresh_case(int(dst["id"]))
    if src_id:
        refresh_case(src_id)
    return {"ok": True, "changed": True, "observation_id": int(obs_id), "case_id": int(dst["id"]), "from_case": src_id}


def create_action(case_id: int, body: dict, actor: str, role: str) -> dict:
    """POST /market-intel/cases/{id}/action (reviewers): ask the supplier to act on a VERIFIED case (no
    complaint is a proven defect until a person verified it). The case moves to Action requested FIRST (a
    reviewer who lost a race gets 409 and nothing is created), then the supplier action is created and
    linked (a second action on such a case only links)."""
    _reviewer(role)
    case = _open_case_for_change(case_id)
    if case.get("status") not in ("verified", "action_requested"):
        raise MarketIntelError("Verify the case before asking the supplier for action.", 409)
    request = clean_text(body.get("request"), 1000)
    if not request:
        raise MarketIntelError("Write what the supplier is asked to do.")
    names = Names.load()
    sid = _id(body.get("supplier_id"), "supplier")
    supplier = names.supplier(sid) if sid else names.supplier_of(case)
    if sid and not supplier:
        raise MarketIntelError("Unknown supplier.")
    if not supplier:
        raise MarketIntelError("Choose the supplier (the case has none yet).")
    row = {"supplier_id": int(supplier["id"]), "request": request, "owner": clean_text(body.get("owner"), 120),
           "due_date": _day(body.get("due_date"), "due date"), "status": "open",
           "created_by": actor or None, "updated_by": actor or None}
    moved = case.get("status") == "verified"
    if moved:
        _cas(int(case_id), "verified", {"status": "action_requested", "status_reason": "supplier action requested",
                                         "changed_at": mi._iso(), "changed_by": actor, "updated_at": mi._iso(),
                                         "updated_by": actor})
    action = (_c().table("supplier_actions").insert(row).execute().data or [row])[0]
    _c().table("supplier_action_cases").insert({"action_id": int(action["id"]), "case_id": int(case_id)}).execute()
    case_event(int(case_id), "action", actor, detail={"action_id": int(action["id"]), "supplier_id": int(supplier["id"]),
                                                      "request": request, "due_date": row["due_date"]})
    if moved:
        case_event(int(case_id), "status", actor, from_status="verified", to_status="action_requested",
                   detail={"action_id": int(action["id"])})
    return {"ok": True, "case_id": int(case_id), "status": "action_requested",
            "action": _action_out(action, names, mask=False)}


def update_action(action_id: int, body: dict, actor: str, role: str) -> dict:
    """PATCH /market-intel/actions/{id} (reviewers): the supplier's response, YQ's resolution check and the
    action's status (open / responded / resolved / cancelled). Recording a response on an open action
    marks it responded unless a status is given. Every linked case's history says so."""
    _reviewer(role)
    _need()
    got = _c().table("supplier_actions").select("*").eq("id", int(action_id)).limit(1).execute().data or []
    if not got:
        raise MarketIntelError("That supplier action does not exist.", 404)
    cur = got[0]
    upd: dict = {}
    if "supplier_response" in body:
        upd["supplier_response"] = clean_text(body.get("supplier_response"), 2000)
    if "resolution_check" in body:
        upd["resolution_check"] = clean_text(body.get("resolution_check"), 1000)
    if body.get("status") not in (None, ""):
        st = str(body["status"]).strip().lower()
        if st not in ACTION_STATUSES:
            raise MarketIntelError("An action is open, responded, resolved or cancelled.")
        upd["status"] = st
    elif upd.get("supplier_response") and (cur.get("status") or "open") == "open":
        upd["status"] = "responded"
    diffs = {k: {"from": cur.get(k), "to": v} for k, v in upd.items() if v != cur.get(k)}
    names = Names.load()
    if not diffs:
        return {"ok": True, "changed": False, "action": _action_out(cur, names, mask=False)}
    upd = {k: upd[k] for k in diffs}
    upd.update(updated_at=mi._iso(), updated_by=actor or None)
    new = (_c().table("supplier_actions").update(upd).eq("id", int(action_id)).execute().data or [{**cur, **upd}])[0]
    links = mi._all(lambda: _c().table("supplier_action_cases").select("action_id,case_id")
                    .eq("action_id", int(action_id)).order("case_id"))
    for x in links:
        case_event(int(x["case_id"]), "action", actor, detail={"action_id": int(action_id), "changes": diffs})
    return {"ok": True, "changed": True, "action": _action_out(new, names, mask=False),
            "cases": [int(x["case_id"]) for x in links]}
