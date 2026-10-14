"""Copy the old Product Finds and Field Notes into Market Intel. COPY, never move.

    python -m scripts.market_intel_import                      # dry run (default): reads only, writes nothing
    python -m scripts.market_intel_import --limit 5            # dry run of the next 5 sightings only
    python -m scripts.market_intel_import --apply --limit 5    # a trial: the 5 oldest sightings not yet copied
    python -m scripts.market_intel_import --apply              # everything left; a re-run creates nothing new
    python -m scripts.market_intel_import --list               # print every sighting (default: the first 10)

Release R7b wrote this script (plan §18.4); Market Intel v2 (r8m, plan Part F, Phase 0) upgraded it: the
photos now go through the live capture's own processing and get a 320 px thumbnail, and the r8m columns
are filled when they exist. It runs ONCE, at release time, from the main project folder, after a backup.
It refuses --apply from a code worktree (.claude/worktrees/...): the .env found from there is production's.

What it does:

  * every product_finds row becomes one market_observations row with source='import' (the 259 WhatsApp
    finds); rows the R7b API wrote before its migration existed (source_file 'mi:<uuid>:<n>', one row per
    photo) are grouped back into the ONE sighting they were, under that capture's client_uuid; every
    field_notes row becomes one sighting too (the 2 owner tests);
  * observed_at is the old row's own time (posted_at / created_at), never the import time; so is
    client_observed_at when the r8m column exists. A row without a time is reported, not guessed;
  * no rep identity is invented and no shop is guessed: salesman_id, shop_customer_id and shop_name stay
    empty, created_by keeps the old row's poster as it was recorded (the seeded finds say 'sales-team'),
    and item_id stays empty: the imports are the office's "Finds library", nothing is clustered on fuzzy
    evidence;
  * text is cleaned the way the capture cleans it (control characters out, spaces collapsed) and stored
    as typed: the app masks phones and emails where it masks them (management, the AI pack, supplier
    files), and what this script PRINTS is masked the same way (app.ai_insights.mask_text);
  * each photo is downloaded (read only) from where it is (bucket 'finds' or 'field-notes') and goes
    through app.market_intel.process_photo exactly as a live capture: magic bytes, the 8 MB upload cap,
    16 MP read from the header, EXIF (GPS) stripped, upright, at most 1600 px, WebP, a 64-bit dHash. The
    master is stored in the private 'finds' bucket at market/YYYY/MM/imp-<sighting>-<n>.webp (the month it
    was seen) and a 320 px WebP thumbnail (q70) next to it as <master stem>-t320.webp. With the r8m columns,
    market_photos also gets thumb_path, sha256 (of the stored master) and role 'product'. has_people_flag
    stays false: reviewers flag later. A photo that cannot be imported is reported; its sighting comes in
    without it (and is not imported at all when no photo, text or price is left);
  * a photo within Hamming 8 of one already stored (or copied earlier in the same run) marks the sighting
    near_duplicate_of that one: a flag for the office, nothing is skipped or merged;
  * idempotent: client_uuid is uuid5 of 'import|product_finds|<id>' (or the capture's own uuid) and the
    insert is PostgREST's upsert with ignore-duplicates on that key, which the database runs as
    INSERT ... ON CONFLICT (client_uuid) DO NOTHING: a second run writes nothing. A sighting copied
    earlier whose photos did not all arrive (a stopped run) gets the missing ones, and a photo that
    could not be imported is noted in its meta so a re-run does not try it again; object paths are fixed
    per sighting and photo, so a resumed upload replaces its own object and leaves no orphan behind;
  * the schema is probed once: the r7b tables must exist (refused otherwise), and each r8m column is set
    only when it is there, so the script also works on the r7b schema alone.

The old tables are only read: no UPDATE, DELETE or TRUNCATE of product_finds / field_notes anywhere here.

Exit: 0 dry run or applied (rows that cannot be imported are listed, they are not errors); 1 an error (a
read or a write failed: the run stops there and a re-run picks up where it stopped); 2 refused (the R7b
tables are missing, Supabase is not configured, --apply from a worktree, or a bad option).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import re
import sys
import uuid
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
THUMB_EDGE = 320                # px, longest side of the thumbnail (contract §3)
THUMB_QUALITY = 70
SHOW = 10                       # sightings listed by default (--list: all of them)
ROLE = "product"                # market_photos.role of every imported photo (r8m)
# The r8m columns this script fills when the migration has added them; each is probed once.
R8M_COLUMNS = (("market_observations", "client_observed_at"), ("market_photos", "thumb_path"),
               ("market_photos", "role"), ("market_photos", "sha256"))
FIND_COLS = ("id,name,price_bhd,currency,note,category,source,image_path,status,promoted_item_code,"
             "source_file,posted_by,posted_at")
NOTE_COLS = "id,note,category,created_by,created_at,image_path"

_FALLBACK = re.compile(r"^mi:([0-9a-fA-F-]{36}):(\d+)$")
_LABEL = re.compile(r"^\[([^\]]+)\]")
_LABEL_TO_KIND = {v.lower(): k for k, v in KIND_LABELS.items()}
# field_notes.category -> market kind
_NOTE_KIND = {"competitor_price": "competitor_price", "stockout": "shop_asked", "demand": "shop_asked",
              "complaint": "complaint", "new_product": "new_product", "other": "other"}


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
    """A stored time as ISO 8601 with its offset (a naive one is UTC); None when there is none."""
    d = mi._parse_ts(v)
    return d.isoformat() if d else None


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
        photos = [{"bucket": "finds", "path": r["image_path"], "position": i}
                  for i, r in enumerate(rows) if r.get("image_path")]
        return {
            "client_uuid": client_uuid, "source": "import", "item_id": None,
            "kind": _kind_from_note(head.get("note")) or "new_product",
            "created_by": _who(head.get("posted_by")), "title": clean_text(head.get("name"), 160),
            "note": clean_text(head.get("note"), 2000), "category": category.upper() if category else None,
            "price_bhd": price, "observed_at": _iso(head.get("posted_at")),
            "legacy_ref": "product_finds:" + ",".join(str(r["id"]) for r in rows),
            "meta": {"legacy_status": head.get("status"), "legacy_source": head.get("source"),
                     "currency": head.get("currency"), "promoted_item_code": head.get("promoted_item_code"),
                     "source_file": None if src.startswith("mi:") else (src or None),
                     "photo_sources": [dict(p) for p in photos]},
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
        keep({
            "client_uuid": _stable(f"import|field_notes|{nid}"), "source": "import", "item_id": None,
            "kind": _NOTE_KIND.get(str(n.get("category") or "other"), "other"),
            "created_by": _who(n.get("created_by")), "title": None, "note": clean_text(n.get("note"), 2000),
            "category": None, "price_bhd": None, "observed_at": _iso(n.get("created_at")),
            "legacy_ref": f"field_notes:{nid}",
            "meta": {"legacy_category": n.get("category"), "photo_sources": [dict(p) for p in photos]},
            "photos": photos, "warnings": [],
        })
    obs.sort(key=lambda o: (o["observed_at"] or "", o["legacy_ref"]))
    return {"observations": obs, "skipped": skipped,
            "counts": {"finds": len(finds), "notes": len(notes), "observations": len(obs),
                       "photos": sum(len(o["photos"]) for o in obs)}}


def photo_paths(o: dict, position: int, ext: str, thumb_ext: str) -> tuple[str, str]:
    """(master, thumbnail) object paths, fixed per sighting and photo: a resumed run writes the same ones.
    The thumbnail is the master's stem + '-t320' (contract §3)."""
    seen = mi._parse_ts(o["observed_at"])
    stem = f"market/{seen:%Y/%m}/imp-{uuid.UUID(o['client_uuid']).hex}-{int(position)}"
    return stem + ext, stem + "-t320" + thumb_ext


def obs_row(o: dict, cols: set[str], near_id: int | None = None, not_imported: list[dict] | None = None) -> dict:
    """The market_observations row for one planned sighting. The rep, the shop and the item stay empty
    (nothing invented or guessed); client_observed_at only when the r8m column exists."""
    meta = dict(o["meta"])
    if not_imported:
        meta["photos_not_imported"] = not_imported
    row = {"client_uuid": o["client_uuid"], "source": "import", "kind": o["kind"], "item_id": None,
           "salesman_id": None, "shop_customer_id": None, "shop_name": None, "created_by": o["created_by"],
           "title": o["title"], "note": o["note"], "category": o["category"], "price_bhd": o["price_bhd"],
           "observed_at": o["observed_at"], "legacy_ref": o["legacy_ref"], "meta": meta,
           "near_duplicate_of": near_id}
    if "market_observations.client_observed_at" in cols:
        row["client_observed_at"] = o["observed_at"]
    return row


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


def make_thumb(master: bytes) -> tuple[bytes, str, str]:
    """A 320 px thumbnail of the processed master: (bytes, extension, content type). WebP q70 like the
    capture's own thumbnails; JPEG only where Pillow has no WebP (the master is JPEG there too)."""
    from PIL import Image
    img = Image.open(io.BytesIO(master))
    img.load()
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((THUMB_EDGE, THUMB_EDGE), Image.Resampling.LANCZOS)
    img.info = {}
    out = io.BytesIO()
    if mi._webp_ok():
        img.save(out, format="WEBP", quality=THUMB_QUALITY, method=4, exif=b"")
        return out.getvalue(), ".webp", "image/webp"
    img.save(out, format="JPEG", quality=THUMB_QUALITY, optimize=True, exif=b"")
    return out.getvalue(), ".jpg", "image/jpeg"


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


def prepare_photo(data: bytes, path: str) -> dict:
    """One old photo, ready to store: {"ok": True, master fields (process_photo), "thumb", "thumb_ext",
    "thumb_type", "sha256"} or {"ok": False, "reason"}. Nothing is written."""
    from app.uploads import MAX_PHOTO_BYTES
    if not data:
        return {"ok": False, "reason": "the stored file is empty"}
    if len(data) > MAX_PHOTO_BYTES:
        return {"ok": False, "reason": f"larger than {MAX_PHOTO_BYTES // (1024 * 1024)} MB"}
    try:
        p = mi.process_photo(data, "photo" + _sniff_ext(data, path))
    except mi.MarketIntelError as e:
        return {"ok": False, "reason": _refusal(e)}
    thumb, thumb_ext, thumb_type = make_thumb(p["data"])
    return {"ok": True, "data": p["data"], "ext": p["ext"], "content_type": p["content_type"],
            "width": p["width"], "height": p["height"], "bytes": p["bytes"], "phash": p["phash"],
            "thumb": thumb, "thumb_ext": thumb_ext, "thumb_type": thumb_type,
            "sha256": hashlib.sha256(p["data"]).hexdigest()}


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
    return f"{type(e).__name__}: {str(e)[:200]}"


def _missing_column(e: Exception) -> bool:
    """PostgREST's answers for a column that is not there: 42703 on a read, PGRST204 on a write. (A
    missing TABLE is 42P01 / PGRST205: app.market_intel.is_missing_table.)"""
    msg = f"{getattr(e, 'code', '')} {e}"
    return "42703" in msg or "PGRST204" in msg


def _not_found(e: Exception) -> bool:
    """Storage's answer for an object that is not there (any other failure stops the run)."""
    if isinstance(e, (KeyError, FileNotFoundError)):
        return True
    msg = f"{getattr(e, 'status', '')} {getattr(e, 'code', '')} {e}".lower()
    return "not found" in msg or "not_found" in msg or "404" in msg


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
        self.live = 0                                   # the capture reached Market Intel itself
        self.left = 0                                   # held back by --limit
        self.unmapped: list[tuple[str, str]] = []       # (ref, why): not imported at all
        self.photo_problems: list[tuple[str, str, str]] = []   # (ref, photo, why): its sighting comes without it
        self.warnings: list[tuple[str, str]] = []

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

    def _stored_photos(self) -> list[dict]:
        try:
            return _paged(lambda a, b: self.c.table("market_photos").select("id,observation_id,position,phash")
                          .order("id").range(a, b).execute().data)
        except Stop:
            raise
        except Exception as e:  # noqa: BLE001
            raise Stop(f"could not read market_photos: {_short(e)}") from None

    def _prepare(self, p: dict) -> dict:
        """Download one old photo (read only) and process it; a missing object is a reason, not an error.
        Any other storage failure stops the run: a blip must not decide what is imported."""
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
            if r["ok"]:
                good.append((p, r))
            else:
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

    def _insert_photos(self, o: dict, obs_id: int, ups: list[dict]) -> None:
        if not ups:
            return
        try:
            self.c.table("market_photos").insert([photo_row(obs_id, u, self.cols) for u in ups]).execute()
        except Exception as e:  # noqa: BLE001
            raise Stop(f"market_photos insert failed for {o['legacy_ref']}: {_short(e)}") from None

    def _insert_obs(self, o: dict, near_id, not_imported: list[dict]) -> tuple[int, bool]:
        """(observation id, created?). An upsert that ignores duplicates on client_uuid: a sighting that is
        already there is never touched; its id is read back instead."""
        row = obs_row(o, self.cols, near_id if isinstance(near_id, int) else None, not_imported)
        try:
            got = (self.c.table("market_observations")
                   .upsert(row, on_conflict="client_uuid", ignore_duplicates=True).execute().data or [])
            if got:
                return int(got[0]["id"]), True
            again = (self.c.table("market_observations").select("id").eq("client_uuid", o["client_uuid"])
                     .limit(1).execute().data or [])
        except Exception as e:  # noqa: BLE001
            raise Stop(f"market_observations insert failed for {o['legacy_ref']}: {_short(e)}") from None
        if not again:
            raise Stop(f"market_observations: {o['legacy_ref']} was neither written nor found")
        return int(again[0]["id"]), False

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
        self.photo_problems += [(o["legacy_ref"], b["photo"], b["reason"]) for b in bad]
        near = self._nearest([r["phash"] for _, r in good]) if good else None
        obs_id: object = o["legacy_ref"]
        made = True
        if self.apply:
            ups = [self._upload(o, p, r) for p, r in good]
            obs_id, made = self._insert_obs(o, near, [{"position": b["position"], "reason": b["reason"]} for b in bad])
            if not made:                # written meanwhile by another run: add only the photos it lacks
                have = {int(x.get("position") or 0) for x in self._stored_photos()
                        if int(x.get("observation_id") or 0) == obs_id}
                ups = [u for u in ups if u["position"] not in have]
            self._insert_photos(o, obs_id, ups)
        self.known += [(obs_id, r["phash"]) for _, r in good]
        if not made:
            self.completed.append({**o, "_photos": len(good), "_near": False})
            self.photos_added += len(good)
            return
        self.created.append({**o, "_photos": len(good), "_near": near is not None})
        self.by_kind[o["kind"]] = self.by_kind.get(o["kind"], 0) + 1
        self.photos += len(good)
        self.near += near is not None
        self.warnings += [(o["legacy_ref"], w) for w in o.get("warnings") or []]

    def _complete(self, o: dict, obs_id: int, missing: list[dict]) -> None:
        good, bad = self._prepare_all(missing)
        self.photo_problems += [(o["legacy_ref"], b["photo"], b["reason"]) for b in bad]
        if not good:
            return
        if self.apply:
            self._insert_photos(o, obs_id, [self._upload(o, p, r) for p, r in good])
        self.known += [(obs_id, r["phash"]) for _, r in good]
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
        say("schema: the R7b tables with every r8m column (client_observed_at, thumb_path, role, sha256)" if not missing
            else "schema: the R7b tables; not there, so not set: " + ", ".join(missing)
            + " (thumbnails are still stored next to each master)")
        for table, rows in (("product_finds", finds), ("field_notes", notes)):
            if rows is None:
                say(f"{table}: not there, nothing to copy from it")
        plan = build_plan(finds or [], notes or [])
        say(f"read: {len(finds or [])} product_finds, {len(notes or [])} field_notes -> "
            f"{_n(plan['counts']['observations'], 'sighting')} with {_n(plan['counts']['photos'], 'photo')}")
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
        work: list[tuple[dict, int | None, list[dict]]] = []
        for o in plan["observations"]:
            ex = existing.get(o["client_uuid"])
            if ex is None:
                work.append((o, None, o["photos"]))
                continue
            if ex.get("source") != "import":
                self.live += 1          # the capture itself reached Market Intel: nothing to add to it
                continue
            done = set(have.get(int(ex["id"]), set()))
            done |= {int(x.get("position") or 0) for x in ((ex.get("meta") or {}).get("photos_not_imported") or [])}
            todo = [p for p in o["photos"] if p["position"] not in done]
            if todo:
                work.append((o, int(ex["id"]), todo))
            else:
                self.already += 1
        if self.limit is not None and len(work) > self.limit:
            self.left = len(work) - self.limit
            work = work[: self.limit]
        for i, (o, obs_id, photos) in enumerate(work, 1):
            try:
                if obs_id is None:
                    self._create(o)
                else:
                    self._complete(o, obs_id, photos)
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
            + (f"; {self.live} more arrived as live captures (left as they are)" if self.live else ""))
        say(f"{verb}: {_n(len(self.created), 'sighting')}" + (f" ({kinds})" if kinds else ""))
        say(f"  photos: {self.photos}, each with a {THUMB_EDGE} px thumbnail, in bucket {BUCKET} under market/")
        say(f"  near-duplicate photo: {_n(self.near, 'sighting')} (flagged near_duplicate_of; nothing skipped or merged)")
        if self.completed:
            say(f"{'completed' if self.apply else 'to complete'} (copied before, photos missing): "
                f"{_n(len(self.completed), 'sighting')}, {_n(self.photos_added, 'photo')}, each with a thumbnail")
        if self.left:
            say(f"--limit {self.limit}: {_n(self.left, 'more sighting')} left for a later run")
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
            out(f"REFUSED: Supabase is not configured: {e}")
            return 2
    try:
        return Importer(client, apply=args.apply, limit=args.limit, show_all=args.list, out=out).run()
    except Exception:  # noqa: BLE001 — anything unforeseen is an error (exit 1), with its traceback for the record
        import traceback
        out("ERROR: unexpected failure; a re-run resumes where this one stopped.\n" + traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
