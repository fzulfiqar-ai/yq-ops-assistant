"""Copy the old Product Finds and Field Notes into Market Intel. COPY, never move.

    python -m scripts.market_intel_import                      # dry run (default): reads only, writes nothing
    python -m scripts.market_intel_import --limit 5            # dry run of the next 5 sightings only
    python -m scripts.market_intel_import --apply --limit 5    # a trial: the 5 oldest sightings not yet copied
    python -m scripts.market_intel_import --apply              # everything left; a re-run creates nothing new
    python -m scripts.market_intel_import --list               # print every sighting (default: the first 10)

Release R7b wrote this script (plan §18.4); Market Intel v2 (r8m, plan Part F, Phase 0) upgraded it: the
photos now go through the live capture's own processing and get a 320 px thumbnail, and the r8m photo
columns are filled when they exist. It runs ONCE, at release time, from the main project folder, after a
backup. It refuses --apply from a code worktree (.claude/worktrees/...): the .env found from there is
production's.

What it does:

  * every product_finds row becomes one market_observations row with source='import' (the 259 WhatsApp
    finds); rows the R7b API wrote before its migration existed (source_file 'mi:<uuid>:<n>', one row per
    photo) are grouped back into the ONE sighting they were, under that capture's client_uuid; every
    field_notes row becomes one sighting too (the 2 owner tests);
  * observed_at is when the find was made, never the import time. The seeded WhatsApp finds carry the
    seed day in posted_at (scripts/import_unique_items.py left it to its default), so their time comes
    from the WhatsApp file name kept in source_file ("WhatsApp Image 2026-07-05 at 4.11.20 PM.jpeg", read
    as Bahrain time, +03:00) when it is a real time no later than posted_at; otherwise from posted_at
    (field_notes: created_at). meta.time_from says which ("whatsapp_file_name", "posted_at",
    "created_at"); a row with no time at all is reported, not guessed. client_observed_at (r8m: when a
    phone took the photo) stays empty for every import;
  * no rep identity is invented and no shop is guessed: salesman_id, shop_customer_id and shop_name stay
    empty, created_by keeps the old row's poster as it was recorded (the seeded finds say 'sales-team'),
    and item_id stays empty: the imports are the office's "Finds library", nothing is clustered on fuzzy
    evidence;
  * text is cleaned the way the capture cleans it (control characters out, spaces collapsed) and stored
    as typed: the app masks phones and emails where it masks them (management, the AI pack, supplier
    files). What this script PRINTS is masked the same way (app.ai_insights.mask_text), the database's
    own error messages included (a failed CHECK quotes the row);
  * each photo is downloaded (read only) from where it is (bucket 'finds' or 'field-notes') and goes
    through app.market_intel.process_photo exactly as a live capture: magic bytes, the 8 MB upload cap,
    16 MP read from the header, EXIF (GPS) stripped, upright, at most 1600 px, WebP, a 64-bit dHash, the
    sha256 of the stored master and the capture's own 320 px WebP thumbnail (q70). The master is stored in
    the private 'finds' bucket at market/YYYY/MM/imp-<sighting>-<n>.webp (observed_at's month in UTC, as a
    capture's) and the thumbnail next to it as <master stem>-t320.webp (app.market_intel.derived_path).
    With the r8m columns, market_photos also gets thumb_path, sha256 and role 'product'. has_people_flag
    stays false: reviewers flag later;
  * a photo that cannot be imported (its object is not in the bucket, it is not a photo, it is too large,
    or anything fails while processing it) is reported and recorded in its sighting's
    meta.photos_not_imported, so no later run tries it again; the sighting comes in without it (and not
    at all when no photo, text or price is left). Any other storage failure (a missing bucket, an error
    page) stops the run: a blip never decides what is imported;
  * a photo within Hamming 8 of one already stored (or copied earlier in the same run) marks the sighting
    near_duplicate_of that one: a flag for the office, nothing is skipped or merged;
  * idempotent: client_uuid is uuid5 of 'import|product_finds|<id>' (or the capture's own uuid) and the
    insert is PostgREST's upsert with ignore-duplicates on that key, which the database runs as
    INSERT ... ON CONFLICT (client_uuid) DO NOTHING: a second run writes nothing. The sighting is written
    before its photos are uploaded, so nothing is uploaded for a row that turns out to exist already; a
    row under the same client_uuid that is not this import (a live capture, another legacy_ref) is never
    touched. A run that stops part-way is finished by the next one, which finds the sighting and adds
    what it lacks; object paths are fixed per sighting and photo, so a repeated upload replaces its own
    object and nothing is left orphaned once it has finished;
  * the schema is probed once: the r7b tables must exist (refused otherwise), and each r8m photo column is
    set only when it is there, so the script also works on the r7b schema alone.

The old tables are only read: no UPDATE, DELETE or TRUNCATE of product_finds / field_notes anywhere here.

Exit: 0 dry run or applied (rows that cannot be imported are listed, they are not errors); 1 an error (a
read or a write failed: the run stops there and a re-run picks up where it stopped); 2 refused (the R7b
tables are missing, Supabase is not configured, --apply from a worktree, or a bad option).
"""
from __future__ import annotations

import argparse
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from app import market_intel as mi  # noqa: E402
from app.ai_insights import mask_text  # noqa: E402
from app.market_intel import KIND_LABELS, UUID_NS, clean_text, money_out, parse_uuid  # noqa: E402

BUCKET = "finds"                # where every processed master and thumbnail goes (PRIVATE)
PAGE = 1000                     # PostgREST's row cap: every table read is an ordered loop of pages
MAX_PAGES = 200                 # a safety stop for that loop
IN_CHUNK = 100                  # uuids / ids per in.(...) filter: keeps the URL short
SHOW = 10                       # sightings listed by default (--list: all of them)
ROLE = "product"                # market_photos.role of every imported photo (r8m)
# The r8m columns this script fills when the migration has added them; each is probed once. An import
# never writes client_observed_at: it means "when the phone took the photo".
R8M_COLUMNS = (("market_photos", "thumb_path"), ("market_photos", "role"), ("market_photos", "sha256"))
BAHRAIN = timezone(timedelta(hours=3))      # WhatsApp names its files in the PC's clock: Bahrain, no DST
FIND_COLS = ("id,name,price_bhd,currency,note,category,source,image_path,status,promoted_item_code,"
             "source_file,posted_by,posted_at")
NOTE_COLS = "id,note,category,created_by,created_at,image_path"

_FALLBACK = re.compile(r"^mi:([0-9a-fA-F-]{36}):(\d+)$")
_LABEL = re.compile(r"^\[([^\]]+)\]")
_LABEL_TO_KIND = {v.lower(): k for k, v in KIND_LABELS.items()}
# field_notes.category -> market kind
_NOTE_KIND = {"competitor_price": "competitor_price", "stockout": "shop_asked", "demand": "shop_asked",
              "complaint": "complaint", "new_product": "new_product", "other": "other"}
# A WhatsApp export's file name: "WhatsApp Image 2026-07-05 at 4.11.20 PM (1).jpeg" (12-hour, as the seeded
# folder has them; any space before AM / PM) or the 24-hour "... at 16.11.20.jpeg" / "..._a1b2c3.jpg".
_WA_NAME = re.compile(r"^WhatsApp (?:Image|Video) (\d{4})-(\d{2})-(\d{2}) at (\d{1,2})\.(\d{2})\.(\d{2})"
                      r"(?:\s*([AaPp])\.?[Mm]\.?)?(?:\s*\(\d+\)|_[0-9A-Za-z]+)?\.[0-9A-Za-z]+$")


class Refused(RuntimeError):
    """The run cannot start (exit 2)."""


class Stop(RuntimeError):
    """A read or a write failed: the run stops here (exit 1); a re-run resumes."""


class DryRunWrite(RuntimeError):
    """A write reached the dry run's client: a bug, and nothing was sent."""


class _NoColumn(RuntimeError):
    """An old table lacks a column this script reads."""


# ── the plan (pure: no database, no storage) ──────────────────────────────────

def _stable(key: str) -> str:
    return str(uuid.uuid5(UUID_NS, key))


def _iso(v) -> str | None:
    """A stored time as ISO 8601 in UTC (a naive one is taken as UTC); None when there is none."""
    d = mi._parse_ts(v)
    return d.astimezone(timezone.utc).isoformat() if d else None


def whatsapp_time(name) -> datetime | None:
    """The time in a WhatsApp export's file name, as Bahrain time; None for any other name or for a time
    that cannot be (13 PM, 30 February ...)."""
    m = _WA_NAME.match(str(name or "").strip())
    if not m:
        return None
    year, month, day, hour, minute, second = (int(x) for x in m.groups()[:6])
    half = (m.group(7) or "").lower()
    if half:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if half == "p" else 0)
    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=BAHRAIN)
    except ValueError:
        return None


def _find_time(source_file, posted_at) -> tuple[str | None, str | None]:
    """(observed_at in UTC, where it came from) for one find: the WhatsApp file name's time when it is a
    real time no later than posted_at (a find cannot be sent after it was posted), else posted_at."""
    posted = mi._parse_ts(posted_at)
    sent = whatsapp_time(source_file)
    if sent is not None and (posted is None or sent <= posted):
        return sent.astimezone(timezone.utc).isoformat(), "whatsapp_file_name"
    if posted is not None:
        return posted.astimezone(timezone.utc).isoformat(), "posted_at"
    return None, None


def _int_id(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _kind_from_note(note) -> str | None:
    m = _LABEL.match(str(note or "").strip())
    return _LABEL_TO_KIND.get(m.group(1).strip().lower()) if m else None


def _price(v) -> tuple[str | None, str | None]:
    """(the amount as a 3-dp string, a warning): one the column cannot hold is left out and reported."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None, None
    p = money_out(v)
    if p is None:
        return None, "price left out: not a number"
    if not Decimal("0") <= Decimal(p) < Decimal("100000"):
        return None, "price left out: not between 0 and 100,000 BHD"
    return p, None


def _who(v) -> str | None:
    """The old row's poster as it was recorded (an email, or 'sales-team' for the seeded finds)."""
    s = clean_text(v, 254)
    return s.lower() if s else None


def _has_content(o: dict) -> bool:
    return bool(o.get("title") or o.get("note") or o.get("price_bhd") is not None)


def build_plan(finds: list[dict], notes: list[dict]) -> dict:
    """The sightings to write, pure: see the module doc. Rows that cannot be mapped are listed under
    "skipped" with the reason and are never written."""
    obs: list[dict] = []
    skipped: list[dict] = []
    groups: dict[str, list[dict]] = {}
    singles: list[dict] = []
    for f in finds:
        if _int_id(f.get("id")) is None:
            skipped.append({"ref": "product_finds:?", "reason": "the row has no id"})
            continue
        m = _FALLBACK.match(str(f.get("source_file") or ""))
        cu = parse_uuid(m.group(1)) if m else None
        if cu:
            groups.setdefault(cu, []).append({**f, "_pos": int(m.group(2))})
        else:
            singles.append(f)

    def keep(o: dict) -> None:
        if not o["observed_at"]:
            skipped.append({"ref": o["legacy_ref"], "reason": "no original time (the old row has none)"})
        elif not o["photos"] and not _has_content(o):
            skipped.append({"ref": o["legacy_ref"], "reason": "nothing to import: no photo, no text, no price"})
        else:
            obs.append(o)

    def one(rows: list[dict], client_uuid: str) -> dict:
        rows = sorted(rows, key=lambda r: (r.get("_pos", 0), int(r["id"])))
        head = rows[0]
        price, warn = _price(head.get("price_bhd"))
        category = clean_text(head.get("category"), 60)
        src = str(head.get("source_file") or "")
        observed_at, time_from = _find_time(src, head.get("posted_at"))
        photos = [{"bucket": "finds", "path": r["image_path"], "position": i}
                  for i, r in enumerate(rows) if r.get("image_path")]
        return {
            "client_uuid": client_uuid, "source": "import", "item_id": None,
            "kind": _kind_from_note(head.get("note")) or "new_product",
            "created_by": _who(head.get("posted_by")), "title": clean_text(head.get("name"), 160),
            "note": clean_text(head.get("note"), 2000), "category": category.upper() if category else None,
            "price_bhd": price, "observed_at": observed_at,
            "legacy_ref": "product_finds:" + ",".join(str(r["id"]) for r in rows),
            "meta": {"legacy_status": head.get("status"), "legacy_source": head.get("source"),
                     "currency": head.get("currency"), "promoted_item_code": head.get("promoted_item_code"),
                     "source_file": None if src.startswith("mi:") else (src or None),
                     "time_from": time_from, "photo_sources": [dict(p) for p in photos]},
            "photos": photos, "warnings": [warn] if warn else [],
        }

    for f in sorted(singles, key=lambda r: int(r["id"])):
        keep(one([f], _stable(f"import|product_finds|{int(f['id'])}")))
    for cu, rows in groups.items():
        keep(one(rows, cu))
    for n in notes:
        nid = _int_id(n.get("id"))
        if nid is None:
            skipped.append({"ref": "field_notes:?", "reason": "the row has no id"})
            continue
        photos = [{"bucket": "field-notes", "path": n["image_path"], "position": 0}] if n.get("image_path") else []
        observed_at = _iso(n.get("created_at"))
        keep({
            "client_uuid": _stable(f"import|field_notes|{nid}"), "source": "import", "item_id": None,
            "kind": _NOTE_KIND.get(str(n.get("category") or "other"), "other"),
            "created_by": _who(n.get("created_by")), "title": None, "note": clean_text(n.get("note"), 2000),
            "category": None, "price_bhd": None, "observed_at": observed_at,
            "legacy_ref": f"field_notes:{nid}",
            "meta": {"legacy_category": n.get("category"), "time_from": "created_at" if observed_at else None,
                     "photo_sources": [dict(p) for p in photos]},
            "photos": photos, "warnings": [],
        })
    obs.sort(key=lambda o: (o["observed_at"] or "", o["legacy_ref"]))
    return {"observations": obs, "skipped": skipped,
            "counts": {"finds": len(finds), "notes": len(notes), "observations": len(obs),
                       "photos": sum(len(o["photos"]) for o in obs)}}


def photo_paths(o: dict, position: int, ext: str, thumb_ext: str) -> tuple[str, str]:
    """(master, thumbnail) object paths, fixed per sighting and photo: a resumed run writes the same ones.
    The month is observed_at's, in UTC; the thumbnail is the master's stem + '-t320', named as the app
    names every derivative (contract §3)."""
    seen = mi._parse_ts(o["observed_at"]).astimezone(timezone.utc)
    path = f"market/{seen:%Y/%m}/imp-{uuid.UUID(o['client_uuid']).hex}-{int(position)}{ext}"
    return path, mi.derived_path(path, mi.THUMB_SUFFIX, thumb_ext)


def obs_row(o: dict, near_id: int | None = None, not_imported: list[dict] | None = None) -> dict:
    """The market_observations row for one planned sighting. The rep, the shop and the item stay empty
    (nothing invented or guessed), and so does client_observed_at: no phone took these photos just now."""
    meta = dict(o["meta"])
    if not_imported:
        meta["photos_not_imported"] = not_imported
    return {"client_uuid": o["client_uuid"], "source": "import", "kind": o["kind"], "item_id": None,
            "salesman_id": None, "shop_customer_id": None, "shop_name": None, "created_by": o["created_by"],
            "title": o["title"], "note": o["note"], "category": o["category"], "price_bhd": o["price_bhd"],
            "observed_at": o["observed_at"], "legacy_ref": o["legacy_ref"], "meta": meta,
            "near_duplicate_of": near_id}


def photo_row(obs_id: int, up: dict, cols: set[str]) -> dict:
    """The market_photos row for one stored master; thumb_path, role and sha256 only with the r8m columns."""
    row = {"observation_id": obs_id, "bucket": BUCKET, "path": up["path"], "position": up["position"],
           "width": up["width"], "height": up["height"], "bytes": up["bytes"], "phash": up["phash"],
           "has_people_flag": False}
    if "market_photos.thumb_path" in cols:
        row["thumb_path"] = up["thumb_path"]
    if "market_photos.role" in cols:
        row["role"] = ROLE
    if "market_photos.sha256" in cols:
        row["sha256"] = up["sha256"]
    return row


# ── photos: the live capture's processing + a thumbnail ───────────────────────

def _sniff_ext(data: bytes, path: str) -> str:
    """The file's real type from its first bytes (an old upload may carry the wrong extension). Anything
    else keeps the path's own extension, so process_photo refuses it with its usual words."""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    name = path.rsplit("/", 1)[-1].lower()
    return "." + name.rsplit(".", 1)[-1] if "." in name else ""


def _refusal(e: Exception) -> str:
    """process_photo's refusal (written for a rep with a camera) in words for this report."""
    msg = str(e)
    for key, words in (("JPG, PNG or WEBP", "not a JPG, PNG or WEBP photo"),
                       ("too large", "over 16 MP"),
                       ("valid photo", "not a valid photo (its bytes do not match its type)"),
                       ("could not be read", "could not be decoded (truncated or corrupt)")):
        if key in msg:
            return words
    return msg


_PHOTO_FIELDS = ("data", "ext", "content_type", "width", "height", "bytes", "phash", "sha256",
                 "thumb", "thumb_ext", "thumb_type")


def prepare_photo(data: bytes, path: str) -> dict:
    """One old photo, ready to store: {"ok": True, and process_photo's master, dHash, sha256 and
    thumbnail} or {"ok": False, "reason"}. Nothing is written, and nothing about one photo (Pillow, a
    codec, exif_transpose, the thumbnail) can stop the run: whatever fails is that photo's reason."""
    from app.uploads import MAX_PHOTO_BYTES
    if not data:
        return {"ok": False, "reason": "the stored file is empty"}
    if len(data) > MAX_PHOTO_BYTES:
        return {"ok": False, "reason": f"larger than {MAX_PHOTO_BYTES // (1024 * 1024)} MB"}
    try:
        p = mi.process_photo(data, "photo" + _sniff_ext(data, path))
        return {"ok": True, **{k: p[k] for k in _PHOTO_FIELDS}}
    except mi.MarketIntelError as e:
        return {"ok": False, "reason": _refusal(e)}
    except Exception as e:  # noqa: BLE001 — exif_transpose, a codec, the thumbnail: this photo only
        return {"ok": False, "reason": f"could not be processed ({type(e).__name__})"}


# ── the client: reads, probes, and a dry run that cannot write ────────────────

class _Guard:
    """Passes every attribute through except the named writes, which raise before anything is sent."""

    def __init__(self, inner, refuse: tuple[str, ...], what: str):
        self._inner, self._refuse, self._what = inner, refuse, what

    def __getattr__(self, attr):
        if attr in self._refuse:
            raise DryRunWrite(f"dry run: {attr} on {self._what} refused")
        return getattr(self._inner, attr)


class _StorageGuard(_Guard):
    def from_(self, bucket):
        return _Guard(self._inner.from_(bucket), ("upload", "update", "remove", "move", "copy"), f"bucket {bucket}")


class NoWrites:
    """The dry run's client: reads pass through; an insert, update, upsert, delete, upload, move or remove
    raises DryRunWrite before it reaches the network."""

    def __init__(self, inner):
        self._inner = inner

    def table(self, name):
        return _Guard(self._inner.table(name), ("insert", "update", "upsert", "delete"), f"table {name}")

    @property
    def storage(self):
        return _StorageGuard(self._inner.storage, ("create_bucket", "update_bucket", "empty_bucket",
                                                   "delete_bucket"), "storage")


def _n(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _short(e: Exception) -> str:
    """An error for the report, masked: the database quotes the failing row (notes, phones) in some."""
    return mask_text(f"{type(e).__name__}: {str(e)[:200]}")


def _missing_column(e: Exception) -> bool:
    """PostgREST's answers for a column that is not there: 42703 on a read, PGRST204 on a write. (A
    missing TABLE is 42P01 / PGRST205: app.market_intel.is_missing_table.)"""
    msg = f"{getattr(e, 'code', '')} {e}"
    return "42703" in msg or "PGRST204" in msg


def _not_found(e: Exception) -> bool:
    """Storage's answer for an OBJECT that is not there, and nothing else: StorageApiError with the message
    "Object not found" (error not_found, status 404). A missing bucket ("Bucket not found"), an edge page,
    a KeyError or any other failure is not a reason to give a photo up: it stops the run."""
    message = str(getattr(e, "message", "") or "").strip().lower()
    status = str(getattr(e, "status", "") or "404").strip()
    return message == "object not found" and status in ("404", "400")


def _paged(fetch) -> list[dict]:
    """fetch(first, last) -> one page; every page until a short one."""
    out: list[dict] = []
    for i in range(MAX_PAGES):
        rows = fetch(i * PAGE, (i + 1) * PAGE - 1) or []
        out += rows
        if len(rows) < PAGE:
            return out
    raise Stop(f"more than {MAX_PAGES * PAGE} rows in one table: stopped")


def probe(c) -> set[str]:
    """The R7b tables must be there (else Refused); returns the r8m columns that are, as "table.column"."""
    for t in ("market_observations", "market_photos"):
        try:
            c.table(t).select("id").limit(1).execute()
        except Exception as e:  # noqa: BLE001
            if mi.is_missing_table(e):
                raise Refused(f"{t} does not exist: apply scripts/r7b_market_intel_migration.sql first.") from None
            raise Stop(f"could not read {t}: {_short(e)}") from None
    have: set[str] = set()
    for t, col in R8M_COLUMNS:
        try:
            c.table(t).select(col).limit(1).execute()
            have.add(f"{t}.{col}")
        except Exception as e:  # noqa: BLE001
            if not _missing_column(e):
                raise Stop(f"could not read {t}.{col}: {_short(e)}") from None
    return have


def _read_table(c, table: str, cols: str) -> list[dict] | None:
    """Every row of an old table, ordered by id; None when the table is not there."""
    try:
        return _paged(lambda a, b: c.table(table).select(cols).order("id").range(a, b).execute().data)
    except Stop:
        raise
    except Exception as e:  # noqa: BLE001
        if _missing_column(e):
            raise _NoColumn(f"{table}: {_short(e)}") from None
        if mi.is_missing_table(e):
            return None
        raise Stop(f"could not read {table}: {_short(e)}") from None


def read_legacy(c) -> tuple[list[dict] | None, list[dict] | None]:
    """(product_finds rows, field_notes rows); None for a table that is not there. field_notes without
    its photo column (field_notes_photo_migration.sql never applied) is read without it."""
    try:
        finds = _read_table(c, "product_finds", FIND_COLS)
    except _NoColumn as e:
        raise Stop(f"a column this script reads is missing: {e}") from None
    try:
        notes = _read_table(c, "field_notes", NOTE_COLS)
    except _NoColumn:
        try:
            notes = _read_table(c, "field_notes", NOTE_COLS.replace(",image_path", ""))
        except _NoColumn as e:
            raise Stop(f"a column this script reads is missing: {e}") from None
    return finds, notes


def _ours(row: dict, o: dict) -> bool:
    """A market_observations row under this sighting's client_uuid is this import's own: anything else (a
    live capture that reached Market Intel itself, another legacy_ref) is never touched."""
    return row.get("source") == "import" and str(row.get("legacy_ref") or "") == o["legacy_ref"]


def _refused_positions(row: dict) -> set[int]:
    return {int(x.get("position") or 0) for x in ((row.get("meta") or {}).get("photos_not_imported") or [])}


# ── the run ───────────────────────────────────────────────────────────────────

class Importer:
    """One run: probe, read the old tables, plan, then create (or, in a dry run, count) each sighting in
    order, oldest first. Everything it would write goes through `self.c`, which in a dry run is NoWrites."""

    def __init__(self, client, *, apply: bool = False, limit: int | None = None, show_all: bool = False,
                 out=print):
        self.apply = bool(apply)
        self.c = client if self.apply else NoWrites(client)
        self.limit = limit
        self.show_all = show_all
        self.say = out
        self.cols: set[str] = set()                     # the r8m columns that exist
        self.known: list[tuple[object, int]] = []       # (observation id, or a legacy ref in a dry run; dHash)
        self.created: list[dict] = []                   # sightings created (a dry run: to create)
        self.completed: list[dict] = []                 # sightings copied before that got their missing photos
        self.by_kind: dict[str, int] = {}
        self.photos = 0                                 # photos (each with a thumbnail) of created sightings
        self.photos_added = 0                           # photos (each with a thumbnail) of completed ones
        self.near = 0
        self.already = 0                                # copied before, nothing missing
        self.foreign = 0                                # the uuid is held by a row that is not this import
        self.left = 0                                   # held back by --limit
        self.unmapped: list[tuple[str, str]] = []       # (ref, why): not imported at all
        self.photo_problems: list[tuple[str, str, str]] = []   # (ref, photo, why): its sighting comes without it
        self.warnings: list[tuple[str, str]] = []
        self.tried = self.refused = 0                   # photos read this run, and of them the ones refused

    # -- reads ------------------------------------------------------------------
    def _existing(self, uuids: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for i in range(0, len(uuids), IN_CHUNK):
            try:
                rows = (self.c.table("market_observations").select("id,client_uuid,source,legacy_ref,meta")
                        .in_("client_uuid", uuids[i:i + IN_CHUNK]).execute().data or [])
            except Exception as e:  # noqa: BLE001
                raise Stop(f"could not read market_observations: {_short(e)}") from None
            for r in rows:
                out[str(r["client_uuid"]).lower()] = r
        return out

    def _row(self, client_uuid: str) -> dict | None:
        try:
            got = (self.c.table("market_observations").select("id,client_uuid,source,legacy_ref,meta")
                   .eq("client_uuid", client_uuid).limit(1).execute().data or [])
        except Exception as e:  # noqa: BLE001
            raise Stop(f"could not read market_observations: {_short(e)}") from None
        return got[0] if got else None

    def _stored_photos(self) -> list[dict]:
        try:
            return _paged(lambda a, b: self.c.table("market_photos").select("id,observation_id,position,phash")
                          .order("id").range(a, b).execute().data)
        except Stop:
            raise
        except Exception as e:  # noqa: BLE001
            raise Stop(f"could not read market_photos: {_short(e)}") from None

    def _positions(self, obs_id: int) -> set[int]:
        try:
            rows = (self.c.table("market_photos").select("position").eq("observation_id", obs_id)
                    .execute().data or [])
        except Exception as e:  # noqa: BLE001
            raise Stop(f"could not read market_photos: {_short(e)}") from None
        return {int(r.get("position") or 0) for r in rows}

    def _prepare(self, p: dict) -> dict:
        """Download one old photo (read only) and process it. An object that is not in its bucket is a
        reason; any other storage failure stops the run: a blip must not decide what is imported."""
        try:
            data = self.c.storage.from_(p["bucket"]).download(p["path"])
        except Exception as e:  # noqa: BLE001
            if not _not_found(e):
                raise Stop(f"could not read {p['bucket']}:{p['path']} from storage: {_short(e)}") from None
            return {"ok": False, "reason": "not found in storage"}
        return prepare_photo(data, p["path"])

    def _prepare_all(self, photos: list[dict]) -> tuple[list[tuple[dict, dict]], list[dict]]:
        """(the photos ready to store, with their plan entry; the ones that cannot be, with the reason)."""
        good, bad = [], []
        for p in photos:
            r = self._prepare(p)
            self.tried += 1
            if r["ok"]:
                good.append((p, r))
            else:
                self.refused += 1
                bad.append({"position": p["position"], "photo": f"{p['bucket']}:{p['path']}", "reason": r["reason"]})
        return good, bad

    # -- writes (apply only) ----------------------------------------------------
    def _upload(self, o: dict, p: dict, r: dict) -> dict:
        path, thumb_path = photo_paths(o, p["position"], r["ext"], r["thumb_ext"])
        store = self.c.storage.from_(BUCKET)
        try:
            store.upload(path, r["data"], {"content-type": r["content_type"], "upsert": "true"})
            store.upload(thumb_path, r["thumb"], {"content-type": r["thumb_type"], "upsert": "true"})
        except Exception as e:  # noqa: BLE001
            raise Stop(f"storage upload failed for {o['legacy_ref']} photo {p['position']}: {_short(e)}") from None
        return {"position": p["position"], "path": path, "thumb_path": thumb_path, "width": r["width"],
                "height": r["height"], "bytes": r["bytes"], "phash": r["phash"], "sha256": r["sha256"]}

    def _store_photos(self, o: dict, obs_id: int, good: list[tuple[dict, dict]]) -> None:
        """Every master and its thumbnail, then their market_photos rows (one insert)."""
        if not good:
            return
        ups = [self._upload(o, p, r) for p, r in good]
        try:
            self.c.table("market_photos").insert([photo_row(obs_id, u, self.cols) for u in ups]).execute()
        except Exception as e:  # noqa: BLE001
            raise Stop(f"market_photos insert failed for {o['legacy_ref']}: {_short(e)}") from None

    def _insert_obs(self, o: dict, near_id, not_imported: list[dict]) -> tuple[int | None, dict | None]:
        """(the new row's id, None), or (None, the row that already holds this client_uuid). PostgREST's
        upsert ignoring duplicates on client_uuid: an existing row is never changed by it."""
        row = obs_row(o, near_id if isinstance(near_id, int) else None, not_imported)
        try:
            got = (self.c.table("market_observations")
                   .upsert(row, on_conflict="client_uuid", ignore_duplicates=True).execute().data or [])
        except Exception as e:  # noqa: BLE001
            raise Stop(f"market_observations insert failed for {o['legacy_ref']}: {_short(e)}") from None
        if got:
            return int(got[0]["id"]), None
        there = self._row(o["client_uuid"])
        if not there:
            raise Stop(f"market_observations: {o['legacy_ref']} was neither written nor found")
        return None, there

    def _note_not_imported(self, o: dict, row: dict, bad: list[dict]) -> None:
        """Record in this import's own row the photos a later run must not try again (as a first run does
        when it writes the row)."""
        meta = dict(row.get("meta") or {})
        listed = list(meta.get("photos_not_imported") or [])
        seen = {int(x.get("position") or 0) for x in listed}
        add = [{"position": b["position"], "reason": b["reason"]} for b in bad if b["position"] not in seen]
        if not add:
            return
        meta["photos_not_imported"] = listed + add
        try:
            (self.c.table("market_observations").update({"meta": meta}).eq("id", int(row["id"]))
             .eq("source", "import").execute())
        except Exception as e:  # noqa: BLE001
            raise Stop(f"market_observations meta update failed for {o['legacy_ref']}: {_short(e)}") from None
        row["meta"] = meta

    # -- one sighting -----------------------------------------------------------
    def _nearest(self, hashes: list[int]):
        """The sighting of the closest photo seen so far within Hamming 8 of any of these (first wins a tie)."""
        best = None
        for ref, h in self.known:
            d = min(mi.hamming(x, h) for x in hashes)
            if d <= mi.PHASH_NEAR and (best is None or d < best[1]):
                best = (ref, d)
        return best[0] if best else None

    def _create(self, o: dict) -> None:
        good, bad = self._prepare_all(o["photos"])
        if not good and not _has_content(o):
            why = "; ".join(f"{b['photo']}: {b['reason']}" for b in bad)
            self.unmapped.append((o["legacy_ref"], f"no photo could be imported ({why}) and there is no text or price"))
            return
        near = self._nearest([r["phash"] for _, r in good]) if good else None
        obs_id: object = o["legacy_ref"]
        if self.apply:
            # the sighting first, then its photos: nothing is uploaded for a row that turns out to exist
            new_id, there = self._insert_obs(o, near, [{"position": b["position"], "reason": b["reason"]} for b in bad])
            if there is not None:
                self._finish_existing(o, there, good, bad)
                return
            obs_id = new_id
            self.photo_problems += [(o["legacy_ref"], b["photo"], b["reason"]) for b in bad]
            self._store_photos(o, new_id, good)
        else:
            self.photo_problems += [(o["legacy_ref"], b["photo"], b["reason"]) for b in bad]
        self.known += [(obs_id, r["phash"]) for _, r in good]
        self.created.append({**o, "_photos": len(good), "_near": near is not None})
        self.by_kind[o["kind"]] = self.by_kind.get(o["kind"], 0) + 1
        self.photos += len(good)
        self.near += near is not None
        self.warnings += [(o["legacy_ref"], w) for w in o.get("warnings") or []]

    def _finish_existing(self, o: dict, row: dict, good: list, bad: list[dict]) -> None:
        """A row already held this client_uuid (it was not there at the first read): this import's own row
        gets only the photos it lacks; any other row is left exactly as it is, and nothing is uploaded."""
        if not _ours(row, o):
            self.foreign += 1
            return
        done = self._positions(int(row["id"])) | _refused_positions(row)
        good = [(p, r) for p, r in good if p["position"] not in done]
        bad = [b for b in bad if b["position"] not in done]
        if not good and not bad:
            self.already += 1
            return
        self._complete_with(o, row, good, bad)

    def _complete(self, o: dict, row: dict, missing: list[dict]) -> None:
        good, bad = self._prepare_all(missing)
        self._complete_with(o, row, good, bad)

    def _complete_with(self, o: dict, row: dict, good: list, bad: list[dict]) -> None:
        self.photo_problems += [(o["legacy_ref"], b["photo"], b["reason"]) for b in bad]
        if self.apply:
            self._note_not_imported(o, row, bad)
            self._store_photos(o, int(row["id"]), good)
        if not good:
            return
        self.known += [(int(row["id"]), r["phash"]) for _, r in good]
        self.completed.append({**o, "_photos": len(good), "_near": False})
        self.photos_added += len(good)

    # -- the whole run ----------------------------------------------------------
    def run(self) -> int:
        say = self.say
        say("Market Intel import: " + ("APPLY (writes)" if self.apply else "DRY RUN (reads only, nothing is written)"))
        try:
            self.cols = probe(self.c)
            finds, notes = read_legacy(self.c)
        except Refused as e:
            say(f"REFUSED: {e}")
            return 2
        except Stop as e:
            say(f"ERROR: {e}")
            return 1
        missing = [f"{t}.{c}" for t, c in R8M_COLUMNS if f"{t}.{c}" not in self.cols]
        say("schema: the R7b tables with the r8m photo columns (thumb_path, role, sha256)" if not missing
            else "schema: the R7b tables; not there, so not set: " + ", ".join(missing)
            + " (thumbnails are still stored next to each master)")
        for table, rows in (("product_finds", finds), ("field_notes", notes)):
            if rows is None:
                say(f"{table}: not there, nothing to copy from it")
        plan = build_plan(finds or [], notes or [])
        say(f"read: {len(finds or [])} product_finds, {len(notes or [])} field_notes -> "
            f"{_n(plan['counts']['observations'], 'sighting')} with {_n(plan['counts']['photos'], 'photo')}")
        times: dict[str, int] = {}
        for o in plan["observations"]:
            times[o["meta"].get("time_from") or "?"] = times.get(o["meta"].get("time_from") or "?", 0) + 1
        say("time seen from: " + ", ".join(f"{k} {v}" for k, v in sorted(times.items())) if times else "time seen from: -")
        self.unmapped += [(s["ref"], s["reason"]) for s in plan["skipped"]]
        try:
            code = self._work(plan)
        except DryRunWrite as e:          # a bug: the guard stopped it before anything was sent
            say(f"ERROR: {e}")
            code = 1
        self._summary(code)
        return code

    def _work(self, plan: dict) -> int:
        try:
            existing = self._existing([o["client_uuid"] for o in plan["observations"]])
            stored = self._stored_photos()
        except Stop as e:
            self.say(f"ERROR: {e}")
            return 1
        have: dict[int, set[int]] = {}
        for p in stored:
            have.setdefault(int(p["observation_id"]), set()).add(int(p.get("position") or 0))
            if p.get("phash") is not None:
                self.known.append((int(p["observation_id"]), int(p["phash"])))
        work: list[tuple[dict, dict | None, list[dict]]] = []
        for o in plan["observations"]:
            row = existing.get(o["client_uuid"])
            if row is None:
                work.append((o, None, o["photos"]))
                continue
            if not _ours(row, o):
                self.foreign += 1           # a live capture under the same uuid, or another import: untouched
                continue
            done = have.get(int(row["id"]), set()) | _refused_positions(row)
            todo = [p for p in o["photos"] if p["position"] not in done]
            if todo:
                work.append((o, row, todo))
            else:
                self.already += 1
        if self.limit is not None and len(work) > self.limit:
            self.left = len(work) - self.limit
            work = work[: self.limit]
        for i, (o, row, photos) in enumerate(work, 1):
            try:
                if row is None:
                    self._create(o)
                else:
                    self._complete(o, row, photos)
            except Stop as e:
                self.say(f"ERROR at {o['legacy_ref']}: {e}")
                self.say("Stopped there: nothing after it was attempted. Re-run with --apply to finish; it skips "
                         "what is already in." if self.apply else "Stopped there.")
                return 1
            if i % 25 == 0 and i < len(work):
                self.say(f"  ... {i} of {len(work)}")
        return 0

    def _line(self, o: dict) -> str:
        """One sighting for the report: ref, day seen, kind, photos, price, and the text MASKED."""
        bits = [f"{o['_photos']} photo(s)"]
        if o.get("_near"):
            bits.append("near-duplicate photo")
        if o.get("price_bhd") is not None:
            bits.append(f"BHD {o['price_bhd']}")
        text = mask_text(o.get("title") or o.get("note") or "") or ""
        if text:
            bits.append(text[:60])
        return f"  {o['legacy_ref']:<26} {str(o['observed_at'])[:10]}  {o['kind']:<16} " + "  ".join(bits)

    def _summary(self, code: int) -> None:
        say = self.say
        verb = "created" if self.apply else "to create"
        kinds = ", ".join(f"{k} {n}" for k, n in sorted(self.by_kind.items(), key=lambda kv: (-kv[1], kv[0])))
        say(f"already in Market Intel (skipped): {_n(self.already, 'sighting')}"
            + (f"; {self.foreign} more uuids are held by rows that are not this import (left as they are)"
               if self.foreign else ""))
        say(f"{verb}: {_n(len(self.created), 'sighting')}" + (f" ({kinds})" if kinds else ""))
        say(f"  photos: {self.photos}, each with a {mi.THUMB_EDGE} px thumbnail, in bucket {BUCKET} under market/")
        say(f"  near-duplicate photo: {_n(self.near, 'sighting')} (flagged near_duplicate_of; nothing skipped or merged)")
        if self.completed:
            say(f"{'completed' if self.apply else 'to complete'} (copied before, photos missing): "
                f"{_n(len(self.completed), 'sighting')}, {_n(self.photos_added, 'photo')}, each with a thumbnail")
        if self.left:
            say(f"--limit {self.limit}: {_n(self.left, 'more sighting')} left for a later run")
        if self.tried >= 3 and self.refused == self.tried:
            # a refused photo is recorded and never tried again: a fault that refuses all of them must be seen
            say(f"WARNING: none of the {self.tried} photos read could be imported. Check the reasons below before "
                "--apply: a refused photo is recorded in its sighting and not tried again.")
        if self.unmapped:
            say(f"cannot be imported: {len(self.unmapped)}")
            for ref, why in self.unmapped[:50]:
                say(f"  {ref:<26} {why}")
            if len(self.unmapped) > 50:
                say(f"  ... and {len(self.unmapped) - 50} more")
        if self.photo_problems:
            say(f"photos left out (their sighting comes in without them): {len(self.photo_problems)}")
            for ref, photo, why in self.photo_problems[:50]:
                say(f"  {ref:<26} {photo}: {why}")
            if len(self.photo_problems) > 50:
                say(f"  ... and {len(self.photo_problems) - 50} more")
        if self.warnings:
            say(f"notes: {len(self.warnings)}")
            for ref, why in self.warnings[:50]:
                say(f"  {ref:<26} {why}")
        rows = self.created + self.completed
        if rows:
            listed = rows if self.show_all else rows[:SHOW]
            head = "sightings" if len(listed) == len(rows) else f"first {SHOW} sightings"
            what = verb if not self.completed else ("created or completed" if self.apply else "to create or complete")
            say(f"{head} {what} (text masked):")
            for o in listed:
                say(self._line(o))
            if len(listed) < len(rows):
                say(f"  ... and {len(rows) - len(listed)} more (--list prints them all)")
        if code:
            return
        if self.apply:
            say(f"\nDone: {_n(len(self.created), 'sighting')} created, {len(self.completed)} completed, "
                f"{self.photos + self.photos_added} photos and as many thumbnails stored. "
                "product_finds and field_notes are unchanged.")
        else:
            say("\nDry run: nothing written. Re-run with --apply to copy (a trial first: --apply --limit 5).")


# ── command line ──────────────────────────────────────────────────────────────

def _positive(v: str) -> int:
    try:
        n = int(v)
    except ValueError:
        n = 0
    if n < 1:
        raise argparse.ArgumentTypeError("--limit takes a whole number of 1 or more")
    return n


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Copy Product Finds + Field Notes into Market Intel (dry run by default).")
    ap.add_argument("--apply", action="store_true", help="write the copies (default: dry run, read only)")
    ap.add_argument("--limit", type=_positive, default=None, metavar="N",
                    help="at most N sightings this run, oldest first (a trial import)")
    ap.add_argument("--list", action="store_true", help="print every sighting, not only the first 10")
    ap.add_argument("--with-phash", action="store_true",
                    help="kept for the old command line: every photo is now processed and hashed anyway")
    return ap.parse_args(argv)


def in_worktree(root: Path) -> bool:
    """A code checkout under <main>/.claude/worktrees/: its .env walk-up reaches production."""
    return root.parent.name == "worktrees" and root.parent.parent.name == ".claude"


def main(argv=None, *, client=None, out=print) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as e:             # --help, or a bad option (argparse printed why)
        return int(e.code or 0)
    if client is None:
        if args.apply and in_worktree(ROOT):
            out("REFUSED: --apply from a code worktree: the .env it finds is production's. "
                "Run it from the main project folder, after a backup.")
            return 2
        try:
            from app.database import get_client
            client = get_client()
        except Exception as e:  # noqa: BLE001 — no SUPABASE_URL / SUPABASE_KEY, or a malformed one
            out(f"REFUSED: Supabase is not configured: {mask_text(str(e))}")
            return 2
    try:
        return Importer(client, apply=args.apply, limit=args.limit, show_all=args.list, out=out).run()
    except Exception:  # noqa: BLE001 — anything unforeseen is an error (exit 1), with its traceback, masked
        import traceback
        out("ERROR: unexpected failure; a re-run resumes where this one stopped.\n"
            + mask_text(traceback.format_exc()))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
