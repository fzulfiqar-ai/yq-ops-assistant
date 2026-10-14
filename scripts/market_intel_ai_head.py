"""The weekly AI Head's Market Intel step (plan §18.6 / §18.9 / §19 step 9; Market Intel v2 Part F1 / F8),
run on the owner's PC.

    python -m scripts.market_intel_ai_head pack                  # read only: exports/ai_head/<date>/market_intel.json
    python -m scripts.market_intel_ai_head pack --photos         # + short-lived signed photo URLs
    python -m scripts.market_intel_ai_head suggest FILE.json     # dry run: what would be written
    python -m scripts.market_intel_ai_head suggest FILE.json --apply --by owner@example.com

pack — reads v_market_signals_agent (no rep, shop, note, login or photo path in it) and, for the citations
and the photos, market_observations / market_photos, over DATABASE_URL (the owner login: the ai_head_ro
login cannot read base tables) in a read-only transaction. It ranks the open clusters and the
un-identified sightings by DISTINCT shops x recency (+ marketplace demand) and writes a dated JSON under
exports/ (gitignored), every text cell masked (app.ai_insights.mask_text: phones and emails) and no
competitor name. Every row names the sightings it rests on (`observation_ids`, newest first), which a
finding cites as [MI-<id>]. The photo-only sightings (no item, no case, no YQ product) that the ranking
leaves out follow in `to_identify`, newest first, so each one can get a reading too. A sighting on a
quality case (Market Intel v2: item_id NULL, case_id set) is neither: the office reviews it on the Cases
tab, so it is left out here. --photos adds, for every listed row, signed URLs (1 hour) to its photos,
each with its observation ID, never a people-flagged photo and never a receipt (it names a shop); that
needs the service key already in .env and never sends a photo anywhere — Claude Code reads them from
the owner's machine.

suggest — the AI Head's reading of those photos goes back as an UNVERIFIED suggestion, never as fact:
  an item      `ai_suggestion` + `ai_confidence` with verified = false, shown as "Unverified" on the item
               until a reviewer marks it verified; an item a person verified (or merged) is never
               written; a decision row records it (actor 'ai-head', approved_by = --by);
  a sighting   a photo-only sighting's meta.ai_suggestion (verified false, label "Unverified"), only while
               it is on no item and no case and its reading was not verified by a person; an audit_log
               row records it (approved_by = --by). The office identifies it.
Every suggestion cites the sightings it rests on — `observation_ids`, each one of its target's own
sightings (an item's, or the photo-only sighting itself) — and the stored reading carries them as
"sightings": ["MI-101", ...]. A reading with a phone number or an email in it is refused (a barcode goes
under "barcode": a GTIN-8 / 12 / 13 / 14 with a valid check digit). --apply needs --by, the owner's login,
like load_insights. Nothing else changes: no status, no identification, no merge (those are proposed in
the report and decided by a person on the board).
FILE is a JSON list:
  [{"item_id": 12, "observation_ids": [101, 105],
    "suggestion": {"brand": ..., "model": ..., "spec": ..., "visible_text": ...}, "confidence": 0.62},
   {"observation_id": 230, "observation_ids": [230], "suggestion": {...}, "confidence": 0.4}]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MAX_SUGGESTION_BYTES = 4000
MAX_CITED = 50                      # observation IDs one suggestion may cite
CITE_KEY = "sightings"              # the stored reading's citations: filled from observation_ids, never by the file
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
# plan Part F8: the four labels a Market Intel finding carries in any report
LABELS = {
    "Verified fact": "a person verified it on the board (an item's verified = true); the AI Head never sets it",
    "Unverified report": "what a rep saw or a shop said: a report until two shops or reps corroborate it "
                         "(plan §18.9) or a person verifies it",
    "AI suggestion": "the AI Head's reading of the photos, citing the sightings it rests on as [MI-<id>]; "
                     "stored Unverified by `suggest`",
    "Recommended action": "what the AI Head proposes; a person decides",
}
SUGGEST_FORMAT = [
    {"item_id": 12, "observation_ids": [101, 105],
     "suggestion": {"brand": "...", "model": "...", "spec": "...", "visible_text": "..."}, "confidence": 0.62},
    {"observation_id": 230, "observation_ids": [230], "suggestion": {"brand": "...", "model": "..."}, "confidence": 0.4},
]
UNSHOWN = ("competitor",)           # whose price it is may name a shop: never in the pack
# the open clusters and the un-identified sightings, without the sightings on a quality case (r8m's case_id,
# read through the whole row so the statement also runs before r8m and after its reverse)
SIGNALS_SQL = ("select s.* from v_market_signals_agent s "
               "where s.row_type <> 'unassigned' or not exists (select 1 from market_observations o "
               "where o.id = s.observation_id and to_jsonb(o) ->> 'case_id' is not null)")
_CITED = re.compile(r"(?:MI-)?(\d{1,12})", re.I)


def _ts(v) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def score(row: dict, now: datetime) -> float:
    """Distinct shops x recency, plus half a point per marketplace demand signal. A sighting seen
    today weighs 1, a week ago 0.5, a month ago ~0.2. Evidence is distinct shops, never raw counts."""
    seen = _ts(row.get("last_seen")) or now
    days = max(0.0, (now - seen).total_seconds() / 86400)
    recency = 1.0 / (1.0 + days / 7.0)
    shops = int(row.get("shops") or 0)
    return round((max(shops, 1) + 0.5 * int(row.get("system_signals") or 0)) * recency, 4)


def rank(rows: list[dict], now: datetime, top: int = 20) -> list[dict]:
    """Open clusters and un-identified sightings, best first (ties: more sightings, then newer)."""
    live = [r for r in rows if r.get("row_type") == "unassigned" or r.get("status") not in ("rejected", "merged", "approved")]
    out = [{**r, "score": score(r, now)} for r in live]
    out.sort(key=lambda r: (-r["score"], -int(r.get("observations") or 0), -((_ts(r.get("last_seen")) or now).timestamp())))
    return out[: max(1, top)]


def to_identify(rows: list[dict]) -> list[dict]:
    """Photo-only sightings — no item, no YQ product, at least one photo without people — newest first:
    the ones the AI Head is asked to name. The ranking alone can leave them out (one sighting, no shop)."""
    out = [r for r in rows if r.get("row_type") == "unassigned" and not r.get("yq_item_code")
           and int(r.get("usable_photos") or 0) > 0]
    out.sort(key=lambda r: (-(_ts(r.get("last_seen")) or EPOCH).timestamp(), -int(r.get("observation_id") or 0)))
    return out


def _key(r: dict) -> tuple[str, int]:
    """A pack row's identity: ('item', item_id) for a cluster, ('obs', observation_id) for a sighting."""
    if r.get("row_type") == "item" and r.get("item_id") is not None:
        return ("item", int(r["item_id"]))
    return ("obs", int(r.get("observation_id") or 0))


def _cited_ids(v) -> list[int] | None:
    """[101, 105] or ["MI-101", "MI-105"] -> [101, 105] (ascending, once each); None when malformed."""
    if not isinstance(v, list) or not 1 <= len(v) <= MAX_CITED:
        return None
    out: set[int] = set()
    for x in v:
        if isinstance(x, bool):
            return None
        if isinstance(x, int):
            n = x
        elif isinstance(x, str) and _CITED.fullmatch(x.strip()):
            n = int(_CITED.fullmatch(x.strip()).group(1))
        else:
            return None
        if n <= 0:
            return None
        out.add(n)
    return sorted(out)


def is_gtin(code: str) -> bool:
    """A GS1 barcode: 8, 12, 13 or 14 digits whose last one is the check digit (weights 3 and 1 from the
    right). An 8-digit phone number passes only when its last digit happens to fit (1 in 10)."""
    if not re.fullmatch(r"\d{8}|\d{12,14}", code or ""):
        return False
    body, check = [int(c) for c in code[:-1]], int(code[-1])
    return (10 - sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body))) % 10) % 10 == check


def _carries_contact(v, key: str | None = None) -> bool:
    """A phone-like run of digits or an email anywhere in a reading (app.ai_insights.has_contact, the
    rule the views and the insights loader share). A barcode is a product code, not a phone: a GTIN with
    a valid check digit and nothing else, under the key "barcode"."""
    from app.ai_insights import has_contact
    if isinstance(v, dict):
        return any(has_contact(str(k)) or _carries_contact(x, str(k)) for k, x in v.items())
    if isinstance(v, list):
        return any(_carries_contact(x, key) for x in v)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        v = str(v)
    if isinstance(v, str):
        return not (key == "barcode" and is_gtin(v.strip())) and has_contact(v)
    return False


def validate_suggestions(raw) -> tuple[list[dict], list[str]]:
    """(clean rows, problems). A row names ONE target — item_id (a cluster) or observation_id (a
    photo-only sighting) — and carries a non-empty JSON object under 4 KB, with no phone number or
    email in it, and a confidence between 0 and 1. observation_ids, the sightings the reading rests on
    (101 or "MI-101", at most 50), is checked for shape here and against the database by cite_problem()
    before anything is written: a suggestion that cites nothing, or cites another target's sightings,
    is never stored."""
    ok: list[dict] = []
    bad: list[str] = []
    if not isinstance(raw, list):
        return [], ["the file must hold a JSON list"]
    for i, r in enumerate(raw):
        where = f"row {i + 1}"
        if not isinstance(r, dict):
            bad.append(f"{where}: not an object")
            continue
        has_item, has_obs = r.get("item_id") is not None, r.get("observation_id") is not None
        if has_item == has_obs:
            bad.append(f"{where}: name one target, item_id (a cluster) or observation_id (a photo-only sighting)")
            continue
        key = "item_id" if has_item else "observation_id"
        try:
            target = int(r.get(key))
        except (TypeError, ValueError):
            bad.append(f"{where}: {key} must be a number")
            continue
        s = r.get("suggestion")
        if not isinstance(s, dict) or not s:
            bad.append(f"{where}: suggestion must be a non-empty object")
            continue
        if CITE_KEY in s:
            bad.append(f"{where}: '{CITE_KEY}' is filled from observation_ids; leave it out of the suggestion")
            continue
        if len(json.dumps(s, ensure_ascii=False)) > MAX_SUGGESTION_BYTES:
            bad.append(f"{where}: suggestion is over {MAX_SUGGESTION_BYTES} bytes")
            continue
        if _carries_contact(s):
            bad.append(f"{where}: the suggestion carries a phone number or an email address (a barcode goes "
                       f"under \"barcode\": a GTIN with a valid check digit)")
            continue
        try:
            conf = float(r.get("confidence"))
        except (TypeError, ValueError):
            bad.append(f"{where}: confidence must be a number between 0 and 1")
            continue
        if not 0 <= conf <= 1:
            bad.append(f"{where}: confidence must be between 0 and 1")
            continue
        row = {key: target, "suggestion": s, "confidence": round(conf, 3)}
        if r.get("observation_ids") is not None:
            ids = _cited_ids(r.get("observation_ids"))
            if ids is None:
                bad.append(f"{where}: observation_ids must list 1 to {MAX_CITED} observation IDs (101 or \"MI-101\")")
                continue
            row["observation_ids"] = ids
        ok.append(row)
    return ok, bad


def cite_problem(row: dict, own: set[int], where: str) -> str | None:
    """Why a suggestion may not be stored for want of evidence, or None. It cites at least one
    sighting, and only its own target's: an item's sightings, or the photo-only sighting itself (a
    reading rests on the photos the pack showed)."""
    ids = row.get("observation_ids") or []
    if not ids:
        return f"{where}: cites no sighting (list the observation IDs whose photos you read in observation_ids)"
    foreign = [i for i in ids if i not in own]
    if foreign:
        return f"{where}: cites " + ", ".join(f"MI-{i}" for i in foreign[:5]) + ", not one of its own sightings"
    return None


def stored_suggestion(row: dict) -> dict:
    """The reading as stored: the AI Head's object plus the sightings it cites ("MI-<id>"), so the item
    card shows the evidence beside the reading (Part F8: an AI suggestion carries its observation IDs)."""
    return {**row["suggestion"], CITE_KEY: [f"MI-{i}" for i in row.get("observation_ids") or []]}


def sighting_reading(row: dict, at: str) -> dict:
    """What a photo-only sighting stores under meta.ai_suggestion: the shape the item page serves
    (suggestion, confidence, verified, label), plus who and when."""
    return {"suggestion": stored_suggestion(row), "confidence": row["confidence"], "verified": False,
            "label": "Unverified", "by": "ai-head", "at": at}


def _connect(read_only: bool):
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    url = os.getenv("DATABASE_URL")
    if not url:
        sys.exit("ERROR: DATABASE_URL is not set (.env: Supabase -> Settings -> Database -> Session pooler URI).")
    import psycopg
    conn = psycopg.connect(url)
    if read_only:
        conn.read_only = True
        conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ     # one snapshot for every read
    return conn


def _rows(cur, sql: str, params=None) -> list[dict]:
    cur.execute(sql, params)
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _sign(pairs: list[tuple[str, str]]) -> dict[tuple[str, str], str]:
    """1-hour signed URLs from the private bucket (app.market_intel.sign_many, with the service key)."""
    from app.market_intel import sign_many
    return sign_many(pairs)


def _citations(cur, rows: list[dict], cap: int) -> dict[tuple[str, int], list[int]]:
    """The sightings each listed row rests on, newest first: an item's own (at most `cap`), or the
    photo-only sighting itself."""
    out = {_key(r): [int(r["observation_id"])] for r in rows
           if r.get("row_type") == "unassigned" and r.get("observation_id") is not None}
    items = sorted({k[1] for k in map(_key, rows) if k[0] == "item"})
    if items:
        for o in _rows(cur, "select id, item_id from market_observations where item_id = any(%s) "
                            "order by observed_at desc, id desc", (items,)):
            lst = out.setdefault(("item", int(o["item_id"])), [])
            if len(lst) < max(1, cap):
                lst.append(int(o["id"]))
    return out


def _photo_links(cur, rows: list[dict], per_row: int) -> dict[tuple[str, int], list[dict]]:
    """1-hour signed links to the listed rows' photos — items and photo-only sightings alike — newest
    sighting first, at most `per_row` each, never a photo flagged with people and never a receipt (r8m's
    role, read through the whole row: 'product' before r8m): {row: [{"observation_id": 101, "url": ...}]}."""
    keys = [_key(r) for r in rows]
    items = sorted({k[1] for k in keys if k[0] == "item"})
    sightings = sorted({k[1] for k in keys if k[0] == "obs"})
    if not items and not sightings:
        return {}
    found = _rows(cur, "select o.id as observation_id, o.item_id, p.bucket, p.path from market_photos p "
                       "join market_observations o on o.id = p.observation_id "
                       "where (o.item_id = any(%s) or o.id = any(%s)) and not p.has_people_flag "
                       "and coalesce(to_jsonb(p) ->> 'role', 'product') <> 'receipt' "
                       "order by o.observed_at desc, o.id desc, p.position, p.id", (items, sightings))
    item_set, obs_set = set(items), set(sightings)
    per: dict[tuple[str, int], list[tuple[int, str, str]]] = {}
    for p in found:
        oid, iid = int(p["observation_id"]), p.get("item_id")
        if iid is not None and int(iid) in item_set:
            k = ("item", int(iid))
        elif oid in obs_set:
            k = ("obs", oid)
        else:
            continue
        lst = per.setdefault(k, [])
        if len(lst) < max(1, per_row):
            lst.append((oid, p["bucket"], p["path"]))
    signed = _sign([(b, path) for lst in per.values() for _, b, path in lst]) if per else {}
    return {k: [{"observation_id": oid, "url": signed[(b, path)]} for oid, b, path in lst if (b, path) in signed]
            for k, lst in per.items()}


def _plain(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    return v


def _entry(r: dict, cites: dict, photos: dict, with_photos: bool) -> dict:
    """One pack row: the view's columns with every text cell masked (phones, emails) and no competitor
    name, then the sightings to cite, the label and (with --photos) the photo links, which are not masked:
    a signed link is not text a person typed."""
    from app.ai_insights import mask_text
    k = _key(r)
    e = {key: mask_text(_plain(v)) for key, v in r.items() if key not in UNSHOWN}
    e["observation_ids"] = cites.get(k, [])
    e["label"] = "Unverified report"
    e["corroborated"] = int(r.get("shops") or 0) >= 2 or int(r.get("reps") or 0) >= 2
    e["needs_identification"] = r.get("row_type") == "unassigned" and not r.get("yq_item_code")
    if with_photos:
        e["photos"] = photos.get(k, [])
    return e


def cmd_pack(args) -> int:
    now = datetime.now(timezone.utc)
    with _connect(read_only=True) as conn:
        cur = conn.cursor()
        rows = _rows(cur, SIGNALS_SQL)
        top = rank(rows, now, args.top)
        listed = {_key(r) for r in top}
        identify = [r for r in to_identify(rows) if _key(r) not in listed][: max(0, args.identify)]
        cites = _citations(cur, top + identify, args.cite)
        photos = _photo_links(cur, top + identify, args.per_item) if args.photos else {}
        conn.rollback()
    out_dir = Path(args.out or ROOT / "exports" / "ai_head" / date.today().isoformat())
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at": now.isoformat(),
               "source": "v_market_signals_agent without the sightings on a quality case (the office reviews those); "
                         "market_observations / market_photos for the citations and photos; DATABASE_URL, read-only",
               "ranking": "distinct shops x recency",
               "photo_urls_expire_in": "1 hour" if args.photos else None,
               "masked": "phones and emails in every text cell (app.ai_insights.mask_text); no competitor name",
               "labels": LABELS,
               "cite": "every finding cites the sightings it rests on as [MI-<id>], from the row's observation_ids",
               "suggest": "write readings as a JSON list like suggest_format (observation_ids = the sightings whose "
                          "photos you read; never a phone, an email or a person's name); "
                          "`python -m scripts.market_intel_ai_head suggest FILE` is a dry run, and --apply --by "
                          "<the owner's login> stores them Unverified, only after the owner's yes",
               "suggest_format": SUGGEST_FORMAT,
               "items": [_entry(r, cites, photos, args.photos) for r in top],
               "to_identify": [_entry(r, cites, photos, args.photos) for r in identify],
               "totals": {"clusters": sum(1 for r in rows if r.get("row_type") == "item"),
                          "unidentified": sum(1 for r in rows if r.get("row_type") == "unassigned"),
                          "photo_only": len(to_identify(rows))}}
    path = out_dir / "market_intel.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {path} ({len(top)} ranked + {len(identify)} to identify, of {len(rows)} rows"
          f"{', with photo links' if args.photos else ''})")
    return 0


def plan_writes(cur, rows: list[dict], at: str) -> list[dict]:
    """The validated rows that may be stored, each with what it stores (`store`); prints why every other
    row is left alone. Reads only."""
    item_ids = sorted({r["item_id"] for r in rows if "item_id" in r})
    obs_ids = sorted({r["observation_id"] for r in rows if "observation_id" in r})
    items = {int(x["id"]): x for x in _rows(cur, "select id, title, status, verified, ai_suggestion, ai_confidence "
                                                 "from market_items where id = any(%s)", (item_ids,))} if item_ids else {}
    own: dict[int, set[int]] = {}
    if item_ids:
        for o in _rows(cur, "select id, item_id from market_observations where item_id = any(%s)", (item_ids,)):
            own.setdefault(int(o["item_id"]), set()).add(int(o["id"]))
    seen = {int(x["id"]): x for x in _rows(cur, "select o.id, o.item_id, o.meta, to_jsonb(o) ->> 'case_id' as case_id "
                                                "from market_observations o where o.id = any(%s)",
                                           (obs_ids,))} if obs_ids else {}
    todo: list[dict] = []
    for r in rows:
        if "item_id" in r:
            it, where = items.get(r["item_id"]), f"item {r['item_id']}"
            if not it:
                print(f"  skipped: {where} does not exist")
                continue
            if it["status"] == "merged":
                print(f"  skipped: {where} was merged into another item; read that one instead")
                continue
            if it["verified"]:
                print(f"  skipped: {where} ({it['title']}) was verified by a person; left as it is")
                continue
            why = cite_problem(r, own.get(r["item_id"], set()), where)
            if why:
                print("  skipped:", why)
                continue
            store = stored_suggestion(r)
            if it["ai_suggestion"] == store and it["ai_confidence"] is not None \
                    and round(float(it["ai_confidence"]), 3) == r["confidence"]:
                print(f"  unchanged: {where} already carries this suggestion")
                continue
        else:
            ob, where = seen.get(r["observation_id"]), f"sighting MI-{r['observation_id']}"
            if not ob:
                print(f"  skipped: {where} does not exist")
                continue
            if ob["item_id"] is not None:
                print(f"  skipped: {where} was identified meanwhile (item {ob['item_id']}); read the item instead")
                continue
            if ob.get("case_id"):
                print(f"  skipped: {where} is a quality report on case {ob['case_id']}; the office reviews it there")
                continue
            why = cite_problem(r, {r["observation_id"]}, where)
            if why:
                print("  skipped:", why)
                continue
            store = sighting_reading(r, at)
            meta = ob.get("meta")
            stored = meta.get("ai_suggestion") if isinstance(meta, dict) else None
            if isinstance(stored, dict) and str(stored.get("verified")).lower() == "true":
                print(f"  skipped: {where}: a person verified its reading; left as it is")
                continue
            if isinstance(stored, dict) and stored.get("suggestion") == store["suggestion"] \
                    and stored.get("confidence") == r["confidence"]:
                print(f"  unchanged: {where} already carries this suggestion")
                continue
        todo.append({**r, "store": store})
    return todo


def write_suggestions(cur, todo: list[dict], by: str) -> int:
    """Store each reading Unverified, recording who approved storing it (`by`, the owner's login). Only
    ai_suggestion / ai_confidence / verified = false (an item) or meta.ai_suggestion (a photo-only
    sighting) change: never a status, an item_id, a case or a merge. Each write repeats its guards in the
    WHERE, so an item a person verified or merged meanwhile, or a sighting the office identified or put on
    a case meanwhile, or whose reading a person verified, is left alone. Returns how many were stored."""
    n = 0
    for r in todo:
        cites = r.get("observation_ids") or []
        if "item_id" in r:
            cur.execute("update market_items set ai_suggestion = %s::jsonb, ai_confidence = %s, verified = false, "
                        "updated_at = now(), updated_by = 'ai-head' where id = %s and not verified and status <> 'merged'",
                        (json.dumps(r["store"], ensure_ascii=False), r["confidence"], r["item_id"]))
            if cur.rowcount:
                cur.execute("insert into market_item_decisions (item_id, event, actor, actor_role, detail) "
                            "values (%s, 'edit', 'ai-head', 'ai_head', %s::jsonb)",
                            (r["item_id"], json.dumps({"ai_suggestion": {"to": "Unverified", "confidence": r["confidence"],
                                                                          "sightings": cites, "approved_by": by}})))
                n += 1
        else:
            cur.execute("update market_observations o set meta = coalesce(o.meta, '{}'::jsonb) "
                        "|| jsonb_build_object('ai_suggestion', %s::jsonb) "
                        "where o.id = %s and o.item_id is null and to_jsonb(o) ->> 'case_id' is null "
                        "and (o.meta is null or jsonb_typeof(o.meta) = 'object') "
                        "and coalesce(o.meta -> 'ai_suggestion' ->> 'verified', '') <> 'true'",
                        (json.dumps(r["store"], ensure_ascii=False), r["observation_id"]))
            if cur.rowcount:
                cur.execute("insert into audit_log (user_email, event, detail) "
                            "values ('ai-head', 'market_intel.ai_suggestion', %s::jsonb)",
                            (json.dumps({"observation_id": r["observation_id"], "to": "Unverified",
                                         "confidence": r["confidence"], "sightings": cites, "approved_by": by}),))
                n += 1
    return n


def cmd_suggest(args) -> int:
    if args.apply and not (args.by and "@" in args.by):
        print("--apply needs --by <the owner's login email>: who approved storing these readings (as load_insights)")
        return 2
    raw = json.loads(Path(args.file).read_text(encoding="utf-8"))
    rows, problems = validate_suggestions(raw)
    for p in problems:
        print("  skipped:", p)
    if not rows:
        print("nothing to write")
        return 1 if problems else 0
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _connect(read_only=not args.apply) as conn:
        cur = conn.cursor()
        todo = plan_writes(cur, rows, at)
        for r in todo:
            what = f"item {r['item_id']}" if "item_id" in r else f"sighting MI-{r['observation_id']}"
            print(f"  {what}: confidence {r['confidence']:.2f}, citing "
                  + ", ".join(f"MI-{i}" for i in r["observation_ids"])
                  + " -> " + json.dumps(r["suggestion"], ensure_ascii=False)[:120])
        if not args.apply:
            print(f"\nDry run: {len(todo)} suggestion(s) would be written as Unverified. Re-run with --apply.")
            conn.rollback()
            return 0
        written = write_suggestions(cur, todo, args.by)
        conn.commit()
    print(f"\nwrote {written} suggestion(s), all Unverified until a reviewer marks them verified (approved by {args.by}).")
    return 0


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Market Intel: the weekly AI Head's pack and suggestions.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack", help="read-only ranked export of the market signals")
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--identify", type=int, default=20, help="photo-only sightings listed beyond the ranking")
    p.add_argument("--cite", type=int, default=25, help="observation IDs listed per item (newest first)")
    p.add_argument("--photos", action="store_true", help="add 1-hour signed photo links for every listed row")
    p.add_argument("--per-item", type=int, default=4)
    p.add_argument("--out", default=None)
    s = sub.add_parser("suggest", help="write the AI Head's readings as Unverified suggestions")
    s.add_argument("file")
    s.add_argument("--apply", action="store_true", help="write (default: dry run)")
    s.add_argument("--by", default=None, help="the owner's login email: who approved storing them (required with --apply)")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    return cmd_pack(args) if args.cmd == "pack" else cmd_suggest(args)


if __name__ == "__main__":
    raise SystemExit(main())
