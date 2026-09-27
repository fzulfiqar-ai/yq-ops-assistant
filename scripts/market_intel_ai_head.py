"""The weekly AI Head's Market Intel step (plan §18.6 / §18.9 / §19 step 9), run on the owner's PC.

    python -m scripts.market_intel_ai_head pack                  # read only: exports/ai_head/<date>/market_intel.json
    python -m scripts.market_intel_ai_head pack --photos         # + short-lived signed photo URLs for the top items
    python -m scripts.market_intel_ai_head suggest FILE.json     # dry run: what would be written
    python -m scripts.market_intel_ai_head suggest FILE.json --apply

pack — reads v_market_signals_agent (no rep, shop, note, login or photo path in it) over a read-only
connection, ranks the open clusters by DISTINCT shops x recency (+ marketplace demand), and writes a
dated JSON under exports/ (gitignored). --photos adds, for the top items only, signed URLs (1 hour)
to their photos, never a people-flagged one; that needs the service key already in .env and never
sends a photo anywhere — Claude Code reads them from the owner's machine.

suggest — the AI Head's reading of those photos goes back as `ai_suggestion` + `ai_confidence` with
verified = false: shown as "Unverified" on the item until a reviewer marks it verified. An item a
person already verified is never overwritten. Every write leaves a decision row (actor 'ai-head').
FILE is a JSON list: [{"item_id": 12, "suggestion": {"brand": ..., "model": ..., "spec": ...,
"visible_text": ...}, "confidence": 0.62}]. Nothing else about the item changes: no status, no merge
(merges are proposed in the report and approved by a person on the board).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MAX_SUGGESTION_BYTES = 4000


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


def validate_suggestions(raw) -> tuple[list[dict], list[str]]:
    """(clean rows, problems). A row needs an integer item_id, a non-empty JSON object under 4 KB and
    a confidence between 0 and 1."""
    ok: list[dict] = []
    bad: list[str] = []
    if not isinstance(raw, list):
        return [], ["the file must hold a JSON list"]
    for i, r in enumerate(raw):
        where = f"row {i + 1}"
        if not isinstance(r, dict):
            bad.append(f"{where}: not an object")
            continue
        try:
            item_id = int(r.get("item_id"))
        except (TypeError, ValueError):
            bad.append(f"{where}: item_id must be a number")
            continue
        s = r.get("suggestion")
        if not isinstance(s, dict) or not s:
            bad.append(f"{where}: suggestion must be a non-empty object")
            continue
        if len(json.dumps(s, ensure_ascii=False)) > MAX_SUGGESTION_BYTES:
            bad.append(f"{where}: suggestion is over {MAX_SUGGESTION_BYTES} bytes")
            continue
        try:
            conf = float(r.get("confidence"))
        except (TypeError, ValueError):
            bad.append(f"{where}: confidence must be a number between 0 and 1")
            continue
        if not 0 <= conf <= 1:
            bad.append(f"{where}: confidence must be between 0 and 1")
            continue
        ok.append({"item_id": item_id, "suggestion": s, "confidence": round(conf, 3)})
    return ok, bad


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
    return conn


def _rows(cur, sql: str, params=None) -> list[dict]:
    cur.execute(sql, params)
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def cmd_pack(args) -> int:
    now = datetime.now(timezone.utc)
    with _connect(read_only=True) as conn:
        cur = conn.cursor()
        rows = _rows(cur, "select * from v_market_signals_agent")
        top = rank(rows, now, args.top)
        photos: dict[int, list[str]] = {}
        if args.photos:
            ids = [int(r["item_id"]) for r in top if r.get("item_id")]
            paths = _rows(cur, "select o.item_id, p.bucket, p.path from market_photos p "
                               "join market_observations o on o.id = p.observation_id "
                               "where o.item_id = any(%s) and not p.has_people_flag order by p.id desc", (ids,)) if ids else []
            from app.market_intel import sign_many
            per_item: dict[int, list[tuple[str, str]]] = {}
            for p in paths:
                lst = per_item.setdefault(int(p["item_id"]), [])
                if len(lst) < args.per_item:
                    lst.append((p["bucket"], p["path"]))
            signed = sign_many([bp for lst in per_item.values() for bp in lst])
            photos = {i: [signed[bp] for bp in lst if bp in signed] for i, lst in per_item.items()}
        conn.rollback()
    out_dir = Path(args.out or ROOT / "exports" / "ai_head" / date.today().isoformat())
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at": now.isoformat(), "source": "v_market_signals_agent", "ranking": "distinct shops x recency",
               "photo_urls_expire_in": "1 hour" if args.photos else None,
               "items": [{**{k: (v.isoformat() if isinstance(v, datetime) else (str(v) if hasattr(v, "quantize") else v))
                             for k, v in r.items()}, "photo_urls": photos.get(int(r["item_id"]), []) if r.get("item_id") else []}
                         for r in top],
               "totals": {"clusters": sum(1 for r in rows if r.get("row_type") == "item"),
                          "unidentified": sum(1 for r in rows if r.get("row_type") == "unassigned")}}
    path = out_dir / "market_intel.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {path} ({len(top)} ranked of {len(rows)} rows{', with photo links' if args.photos else ''})")
    return 0


def cmd_suggest(args) -> int:
    raw = json.loads(Path(args.file).read_text(encoding="utf-8"))
    rows, problems = validate_suggestions(raw)
    for p in problems:
        print("  skipped:", p)
    if not rows:
        print("nothing to write")
        return 1 if problems else 0
    with _connect(read_only=not args.apply) as conn:
        cur = conn.cursor()
        items = {r["id"]: r for r in _rows(cur, "select id, title, verified, ai_suggestion, ai_confidence "
                                                "from market_items where id = any(%s)", ([r["item_id"] for r in rows],))}
        todo = []
        for r in rows:
            it = items.get(r["item_id"])
            if not it:
                print(f"  skipped: item {r['item_id']} does not exist")
            elif it["verified"]:
                print(f"  skipped: item {r['item_id']} ({it['title']}) was verified by a person; left as it is")
            elif it["ai_suggestion"] == r["suggestion"] and it["ai_confidence"] is not None \
                    and round(float(it["ai_confidence"]), 3) == r["confidence"]:
                print(f"  unchanged: item {r['item_id']} already carries this suggestion")
            else:
                todo.append(r)
        for r in todo:
            print(f"  item {r['item_id']}: confidence {r['confidence']:.2f} -> {json.dumps(r['suggestion'], ensure_ascii=False)[:120]}")
        if not args.apply:
            print(f"\nDry run: {len(todo)} suggestion(s) would be written as Unverified. Re-run with --apply.")
            conn.rollback()
            return 0
        for r in todo:
            cur.execute("update market_items set ai_suggestion = %s::jsonb, ai_confidence = %s, verified = false, "
                        "updated_at = now(), updated_by = 'ai-head' where id = %s and not verified",
                        (json.dumps(r["suggestion"], ensure_ascii=False), r["confidence"], r["item_id"]))
            if cur.rowcount:
                cur.execute("insert into market_item_decisions (item_id, event, actor, actor_role, detail) "
                            "values (%s, 'edit', 'ai-head', 'ai_head', %s::jsonb)",
                            (r["item_id"], json.dumps({"ai_suggestion": {"to": "Unverified", "confidence": r["confidence"]}})))
        conn.commit()
    print(f"\nwrote {len(todo)} suggestion(s), all Unverified until a reviewer marks them verified.")
    return 0


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Market Intel: the weekly AI Head's pack and suggestions.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack", help="read-only ranked export of the market signals")
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--photos", action="store_true", help="add 1-hour signed photo links for the top items")
    p.add_argument("--per-item", type=int, default=4)
    p.add_argument("--out", default=None)
    s = sub.add_parser("suggest", help="write the AI Head's readings as Unverified suggestions")
    s.add_argument("file")
    s.add_argument("--apply", action="store_true", help="write (default: dry run)")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    return cmd_pack(args) if args.cmd == "pack" else cmd_suggest(args)


if __name__ == "__main__":
    raise SystemExit(main())
