"""Copy the old Product Finds and Field Notes into Market Intel (release R7b, plan §18.4). COPY, never move.

    python -m scripts.market_intel_import                 # dry run (default): read-only, prints the plan
    python -m scripts.market_intel_import --apply         # write the copies (one transaction)
    python -m scripts.market_intel_import --apply --with-phash   # + read each photo to store its dHash / size

Needs scripts/r7b_market_intel_migration.sql applied first, and DATABASE_URL in .env (the session-pooler
URI). What it does:

  * every product_finds row becomes one market_observations row with source='import' (the 259 WhatsApp
    finds of July); rows the R7b API wrote before the migration existed (source_file 'mi:<uuid>:<n>',
    one row per photo) are grouped back into the ONE sighting they were, under that client_uuid;
  * every field_notes row becomes one sighting too (the 2 owner tests);
  * each photo is referenced where it already is (bucket 'finds' / 'field-notes', same object path):
    no object is copied, moved or deleted; --with-phash downloads each one (read only) to fill the
    64-bit dHash, width, height and size, so a rep re-sending an old WhatsApp photo is caught;
  * item_id stays NULL: the imports are the baseline library the review queue shows on its own page;
    nothing is auto-clustered on fuzzy evidence;
  * client_uuid is uuid5 of 'import|product_finds|<id>' (or the capture's own uuid) with ON CONFLICT
    (client_uuid) DO NOTHING, so a second run writes nothing and a re-run after new fallback captures
    copies only those.

The old tables are only read: no UPDATE, DELETE or TRUNCATE of product_finds / field_notes anywhere here.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.market_intel import KIND_LABELS, UUID_NS, money_out, parse_uuid  # noqa: E402

_FALLBACK = re.compile(r"^mi:([0-9a-fA-F-]{36}):(\d+)$")
_LABEL = re.compile(r"^\[([^\]]+)\]")
_LABEL_TO_KIND = {v.lower(): k for k, v in KIND_LABELS.items()}
# field_notes.category -> market kind
_NOTE_KIND = {"competitor_price": "competitor_price", "stockout": "shop_asked", "demand": "shop_asked",
              "complaint": "complaint", "new_product": "new_product", "other": "other"}


def _stable(key: str) -> str:
    import uuid
    return str(uuid.uuid5(UUID_NS, key))


def _iso(v) -> str | None:
    if v is None:
        return None
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def _kind_from_note(note) -> str | None:
    m = _LABEL.match(str(note or "").strip())
    return _LABEL_TO_KIND.get(m.group(1).strip().lower()) if m else None


def build_plan(finds: list[dict], notes: list[dict]) -> dict:
    """The sightings to write, pure (no database): see the module doc."""
    obs: list[dict] = []
    groups: dict[str, list[dict]] = {}
    singles: list[dict] = []
    for f in sorted(finds, key=lambda r: int(r["id"])):
        m = _FALLBACK.match(str(f.get("source_file") or ""))
        cu = parse_uuid(m.group(1)) if m else None
        if cu:
            groups.setdefault(cu, []).append({**f, "_pos": int(m.group(2))})
        else:
            singles.append(f)

    def one(rows: list[dict], client_uuid: str) -> dict:
        rows = sorted(rows, key=lambda r: (r.get("_pos", 0), int(r["id"])))
        head = rows[0]
        return {
            "client_uuid": client_uuid, "source": "import", "item_id": None,
            "kind": _kind_from_note(head.get("note")) or "new_product",
            "created_by": head.get("posted_by"), "title": (head.get("name") or None),
            "note": head.get("note") or None,
            "category": (str(head.get("category")).strip().upper() or None) if head.get("category") else None,
            "price_bhd": money_out(head.get("price_bhd")),
            "observed_at": _iso(head.get("posted_at")),
            "legacy_ref": "product_finds:" + ",".join(str(r["id"]) for r in rows),
            "meta": {"legacy_status": head.get("status"), "legacy_source": head.get("source"),
                     "currency": head.get("currency"), "promoted_item_code": head.get("promoted_item_code"),
                     "source_file": head.get("source_file") if not str(head.get("source_file") or "").startswith("mi:") else None},
            "photos": [{"bucket": "finds", "path": r["image_path"], "position": i}
                       for i, r in enumerate(rows) if r.get("image_path")],
        }

    for f in singles:
        obs.append(one([f], _stable(f"import|product_finds|{f['id']}")))
    for cu, rows in groups.items():
        obs.append(one(rows, cu))
    for n in sorted(notes, key=lambda r: int(r["id"])):
        obs.append({
            "client_uuid": _stable(f"import|field_notes|{n['id']}"), "source": "import", "item_id": None,
            "kind": _NOTE_KIND.get(str(n.get("category") or "other"), "other"),
            "created_by": n.get("created_by"), "title": None, "note": (n.get("note") or None),
            "category": None, "price_bhd": None, "observed_at": _iso(n.get("created_at")),
            "legacy_ref": f"field_notes:{n['id']}", "meta": {"legacy_category": n.get("category")},
            "photos": ([{"bucket": "field-notes", "path": n["image_path"], "position": 0}] if n.get("image_path") else []),
        })
    obs.sort(key=lambda o: (o["observed_at"] or "", o["legacy_ref"]))
    return {"observations": obs,
            "counts": {"finds": len(finds), "notes": len(notes), "observations": len(obs),
                       "photos": sum(len(o["photos"]) for o in obs)}}


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Copy Product Finds + Field Notes into Market Intel (dry run by default).")
    ap.add_argument("--apply", action="store_true", help="write the copies (default: dry run, read only)")
    ap.add_argument("--with-phash", action="store_true",
                    help="download each photo (read only) to store its dHash, width, height and size")
    return ap.parse_args(argv)


def _read(cur, sql: str) -> list[dict]:
    cur.execute(sql)
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _photo_facts(bucket: str, path: str) -> dict:
    """dHash / size of a stored photo, read through the service client. Never writes anything."""
    import io
    from PIL import Image, ImageOps
    from app.database import get_client
    from app.market_intel import dhash64, to_signed64
    data = get_client().storage.from_(bucket).download(path)
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    return {"phash": to_signed64(dhash64(img)), "width": img.size[0], "height": img.size[1], "bytes": len(data)}


def main(argv=None) -> int:
    args = parse_args(argv)
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    url = os.getenv("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL is not set (.env: Supabase -> Settings -> Database -> Session pooler URI).")
        return 2
    import psycopg
    with psycopg.connect(url) as conn:
        if not args.apply:
            conn.read_only = True
        cur = conn.cursor()
        cur.execute("select to_regclass('public.market_observations') is not null")
        ready = cur.fetchone()[0]
        finds = _read(cur, "select id, name, price_bhd, currency, note, category, source, image_path, status, "
                           "promoted_item_code, source_file, posted_by, posted_at from product_finds order by id")
        notes = _read(cur, "select id, note, category, created_by, created_at, image_path from field_notes order by id")
        plan = build_plan(finds, notes)
        done: set[str] = set()
        if ready:
            cur.execute("select client_uuid::text from market_observations where client_uuid = any(%s::uuid[])",
                        ([o["client_uuid"] for o in plan["observations"]],))
            done = {r[0] for r in cur.fetchall()}
        todo = [o for o in plan["observations"] if o["client_uuid"] not in done]
        print(f"product_finds: {plan['counts']['finds']}  field_notes: {plan['counts']['notes']}  ->  "
              f"{plan['counts']['observations']} sightings, {plan['counts']['photos']} photos")
        print(f"already copied: {len(done)}  to copy: {len(todo)}")
        for o in todo[:5]:
            print(f"  {o['legacy_ref']:<24} {o['kind']:<17} {o['price_bhd'] or '':>8}  {len(o['photos'])} photo(s)  "
                  f"{(o['title'] or o['note'] or '')[:50]}")
        if not args.apply:
            print("\nDry run: nothing written. Re-run with --apply to copy." +
                  ("" if ready else "\nApply scripts/r7b_market_intel_migration.sql first: market_observations is missing."))
            conn.rollback()
            return 0
        if not ready:
            print("ERROR: apply scripts/r7b_market_intel_migration.sql first (market_observations is missing).")
            return 2
        wrote = photos = hashed = 0
        for o in todo:
            cur.execute(
                "insert into market_observations (client_uuid, source, kind, salesman_id, created_by, title, note, "
                "category, price_bhd, observed_at, legacy_ref, meta, item_id) "
                "values (%s, 'import', %s, (select id from salesmen where lower(user_email) = lower(%s) limit 1), "
                "%s, %s, %s, %s, %s::numeric, coalesce(%s::timestamptz, now()), %s, %s::jsonb, null) "
                "on conflict (client_uuid) do nothing returning id",
                (o["client_uuid"], o["kind"], o["created_by"] or "", o["created_by"], o["title"], o["note"],
                 o["category"], o["price_bhd"], o["observed_at"], o["legacy_ref"], json.dumps(o["meta"], default=str)))
            got = cur.fetchone()
            if not got:
                continue
            wrote += 1
            for p in o["photos"]:
                facts: dict = {}
                if args.with_phash:
                    try:
                        facts = _photo_facts(p["bucket"], p["path"])
                        hashed += 1
                    except Exception as e:  # noqa: BLE001 — a missing object keeps its row, without a hash
                        print(f"  photo not read ({p['bucket']}/{p['path']}): {type(e).__name__}")
                cur.execute("insert into market_photos (observation_id, bucket, path, position, width, height, bytes, phash) "
                            "values (%s, %s, %s, %s, %s, %s, %s, %s) on conflict (bucket, path) do nothing",
                            (got[0], p["bucket"], p["path"], p["position"], facts.get("width"), facts.get("height"),
                             facts.get("bytes"), facts.get("phash")))
                photos += cur.rowcount
        conn.commit()
        print(f"\ncopied {wrote} sightings and {photos} photo references ({hashed} hashed). "
              "product_finds and field_notes are unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
