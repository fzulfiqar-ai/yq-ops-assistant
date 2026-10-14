"""R8m — Market Intel v2, Phase 0: importing the old Product Finds and Field Notes (plan Part F, F15).

    python -m tests.test_r8m_market_intel_import

Same lightweight runner as tests/test_r7b_market_intel.py, on its in-memory PostgREST + Storage stand-in
(imported from it and extended here: a column list per table, so a column that is not there answers the
way PostgREST does, paging that honours the offset, the upsert the importer uses, the (bucket, path) key
of market_photos, storage errors in the shape storage3 raises them, and switches that make a read or a
write fail). SUPABASE_URL, SUPABASE_KEY and DATABASE_URL are pinned to ci.invalid BEFORE any import: a
worktree under the main checkout would otherwise find the production .env through python-dotenv's
walk-up. On top of that, every run of the importer here happens with app.database.get_client (and every
app module's own reference to it) raising: the importer only ever gets the stand-in, and a helper of the
app that reached for a real client on its own (mi._upload, product_finds.ensure_bucket) fails the test.
Nothing reaches a database, a bucket or the network. Every name, email, price and photo is invented
("Shop Alpha", "Brand X", rep1@example.com, drawn rectangles).

Covered:
  * the dry run (the default) writes nothing (no insert, upsert, update or delete, no upload) yet reads
    and checks every photo, and says what --apply would create: per kind, photos and thumbnails, where
    the times come from, sightings already copied, rows that cannot be imported and why; its client
    cannot write at all; the app's own client cannot be reached;
  * --apply: one sighting per find / note (a fallback capture's rows stay one sighting, under its own
    uuid), source 'import', no rep, shop or item invented; every photo processed like a live capture (no
    EXIF / GPS, upright, <= 1600 px WebP, the same dHash) with the capture's own 320 px WebP thumbnail
    next to it as <stem>-t320.webp; thumb_path, sha256 and role set; the old tables and objects untouched;
  * time: observed_at from the WhatsApp file name (Bahrain time) when it is a real time no later than
    posted_at, else posted_at (field notes: created_at), meta.time_from saying which; the photo's month
    follows it (in UTC); client_observed_at is never written;
  * a second --apply creates nothing; a row hidden from the first read is never doubled (the upsert on
    client_uuid), this import's own row only gets what it lacks, and a row under the same uuid that is not
    this import (a live capture, another legacy_ref) is never touched, nothing uploaded for it;
  * the r7b schema alone: nothing r8m is sent (the stand-in refuses an unknown column), thumbnails are
    still stored; field_notes without its photo column; a missing old table;
  * rows that cannot be mapped are reported, never crashed on (a missing object, a corrupt or HEIC photo,
    any failure while processing a photo, no time, nothing at all, no id, a price the column cannot hold);
    a wrongly named photo is still read; a refused photo is recorded in meta, on a first run and when a
    stopped run is finished, and never tried again;
  * storage: only "Object not found" is a missing photo; a missing bucket, a KeyError or an error page
    stops the run;
  * --limit N: the N oldest, then the next N, then the rest;
  * refused (exit 2): the R7b tables missing, --apply from a code worktree, Supabase not configured, a bad
    --limit. Exit 1 when a read or write fails; the re-run resumes without a duplicate or an orphan;
  * near-duplicate photos are flagged, never skipped; what the script prints is masked, the database's
    own error messages and an unforeseen traceback included;
  * every read pages past PostgREST's 1,000-row cap; the test data is invented.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import random
import re
import sys
import traceback
import uuid
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Offline by construction, before anything loads app.config (whose load_dotenv never overrides these).
os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"

from tests import test_r7b_market_intel as r7b  # noqa: E402  (the stand-ins; it also sets stdout to UTF-8)
from storage3.exceptions import StorageApiError  # noqa: E402
from app import database, field_notes, market_intel as mi, product_finds  # noqa: E402,F401
from scripts import market_intel_import as imp  # noqa: E402

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


# ── the app's own client is out of reach ──────────────────────────────────────

class _RealClient(BaseException):
    """An app helper reached for the real Supabase client inside an import test. A BaseException, so no
    `except Exception` on the way (product_finds.ensure_bucket swallows those) can hide it."""


REACHED: list[str] = []


def _no_real_client(*_a, **_k):
    REACHED.append("".join(traceback.format_stack(limit=8)[:-1]))
    raise _RealClient("an app helper reached for the Supabase client during an import test")


def _client_guard() -> r7b._Patched:
    """app.database.get_client, and every loaded app module's own reference to it, raise."""
    triples = [(database, "get_client", _no_real_client)]
    for name, mod in sorted(sys.modules.items()):
        if name.startswith("app.") and mod is not database and callable(getattr(mod, "get_client", None)):
            triples.append((mod, "get_client", _no_real_client))
    return r7b._Patched(*triples)


# ── the stand-in, extended ────────────────────────────────────────────────────

_OBS_R7B = {"id", "item_id", "client_uuid", "source", "signal", "kind", "salesman_id", "created_by",
            "shop_customer_id", "shop_name", "area", "title", "brand", "competitor", "category", "note", "price_bhd",
            "demand_level", "demand_qty", "barcode_raw", "yq_item_code", "result", "near_duplicate_of", "legacy_ref",
            "meta", "observed_at", "created_at"}
_PHOTO_R7B = {"id", "observation_id", "bucket", "path", "position", "width", "height", "bytes", "phash",
              "has_people_flag", "created_at"}
_LEGACY = {"product_finds": {"id", "name", "price_bhd", "currency", "note", "category", "source", "image_path", "status",
                             "promoted_item_code", "source_file", "posted_by", "posted_at", "reviewed_by", "updated_at"},
           "field_notes": {"id", "note", "category", "created_by", "created_at", "image_path"}}
# the column lists of scripts/r7b_market_intel_migration.sql, and of the contract's r8m additions
R7B = {"market_observations": _OBS_R7B, "market_photos": _PHOTO_R7B, **_LEGACY}
R8M = {"market_observations": _OBS_R7B | {"case_id", "issue_type_id", "report_basis", "qty_affected", "batch_ref",
                                          "packaging_version", "safety_flag", "client_observed_at", "brand_id",
                                          "supplier_id", "brand_text", "supplier_text"},
       "market_photos": _PHOTO_R7B | {"role", "sha256", "thumb_path", "caption", "blurred_path"}, **_LEGACY}


def _pgrst(code: str, message: str) -> RuntimeError:
    return RuntimeError(json.dumps({"code": code, "message": message}))


class _Q(r7b._Query):
    """r7b's query stand-in (which caps a read at 1,000 rows and honours range()'s offset, as PostgREST
    does) plus: the table's columns are checked (42703 on a read, PGRST204 on a write, as PostgREST
    answers), every range() is recorded, upsert(on_conflict, ignore_duplicates) answers [] for a row that
    is there (the base merges), and the (bucket, path) key of market_photos."""

    def __init__(self, db, table):
        super().__init__(db, table)
        self._cols, self._conflict, self._ignore = "*", "", False

    def select(self, cols="*", count=None, head=False):
        self._cols = cols
        return super().select(cols, count=count, head=head)

    def range(self, a, b):
        self.db.ranges.append((self.table, a, b))
        return super().range(a, b)

    def upsert(self, payload, *, on_conflict="", ignore_duplicates=False, **_kw):
        self.op, self.payload, self._conflict, self._ignore = "upsert", payload, on_conflict, ignore_duplicates
        return self

    def _items(self) -> list[dict]:
        return self.payload if isinstance(self.payload, list) else [self.payload]

    def _check(self) -> None:
        cols = self.db.schema.get(self.table)
        if cols is None or self.table in self.db.missing:
            return
        if self.op == "select" and self._cols != "*":
            for c in str(self._cols).split(","):
                if c.strip() and c.strip() not in cols:
                    raise _pgrst("42703", f"column {self.table}.{c.strip()} does not exist")
        if self.op in ("insert", "upsert", "update"):
            for p in self._items():
                for k in p:
                    if k not in cols:
                        raise _pgrst("PGRST204", f"Could not find the '{k}' column of '{self.table}' in the schema cache")
        if self.op == "insert" and self.table == "market_photos":
            rows = self.db.tables.get("market_photos", [])
            for p in self._items():
                if any(r.get("bucket") == p.get("bucket") and r.get("path") == p.get("path") for r in rows):
                    raise _pgrst("23505", "duplicate key value violates unique constraint market_photos_path_key")

    def execute(self):
        self._check()
        if self.op == "upsert":
            return self._upsert()
        return super().execute()

    def _upsert(self):
        db = self.db
        db.calls.append(("upsert", self.table, self.payload, list(self.filters)))
        fail = db.fail.pop(("upsert", self.table), None)
        if fail is not None:
            raise fail
        if self.table in db.missing:
            raise _pgrst("42P01", f'relation "public.{self.table}" does not exist')
        rows = db.tables.setdefault(self.table, [])
        keys = [k.strip() for k in self._conflict.split(",") if k.strip()]
        out = []
        for p in self._items():
            hit = next((r for r in rows if keys and all(r7b._same(r.get(k), p.get(k)) for k in keys)), None)
            if hit is not None:
                if not self._ignore:
                    hit.update(p)
                    out.append(dict(hit))
                continue
            r = dict(p)
            r.setdefault("id", db.next_id(self.table))
            r.setdefault("created_at", db.now_iso())
            rows.append(r)
            out.append(dict(r))
        return SimpleNamespace(data=out, count=None)


class _Bucket(r7b._Bucket):
    """Downloads answer like storage3: StorageApiError(message, error, statusCode)."""

    def upload(self, path, data, opts=None):
        st = self.st
        if st.fail_after is not None and st.uploads >= st.fail_after:
            raise RuntimeError("storage down")
        st.uploads += 1
        return super().upload(path, data, opts)

    def download(self, path):
        st = self.st
        st.calls.append(("download", self.name, path))
        if st.fail_download:
            raise RuntimeError("503 Service Unavailable")
        if st.fail_keyerror:
            raise KeyError("statusCode")
        if self.name not in st.buckets:
            raise StorageApiError("Bucket not found", "not_found", 404)
        if (self.name, path) not in st.objects:
            raise StorageApiError("Object not found", "not_found", 404)
        return st.objects[(self.name, path)][0]


class _Storage(r7b._Storage):
    def __init__(self):
        super().__init__()
        self.buckets = {"finds", "field-notes"}
        self.fail_after: int | None = None      # uploads allowed before the next one fails
        self.fail_download = False
        self.fail_keyerror = False
        self.uploads = 0

    def from_(self, name):
        return _Bucket(self, name)


class _DB(r7b._FakeDB):
    def __init__(self, tables=None, missing=None, schema=None):
        super().__init__(tables, missing=missing)
        self.schema = R8M if schema is None else schema
        self.storage = _Storage()
        self.ranges: list[tuple] = []

    def table(self, name):
        return _Q(self, name)

    def wrote(self, table: str | None = None) -> list[tuple]:
        return [c for c in self.calls if c[0] in ("insert", "update", "delete", "upsert") and (table is None or c[1] == table)]

    def uploads(self) -> list[tuple]:
        return [c for c in self.storage.calls if c[0] in ("upload", "remove")]

    def downloads(self) -> list[tuple]:
        return [c for c in self.storage.calls if c[0] == "download"]


# ── invented fixtures ─────────────────────────────────────────────────────────

_PICS: dict = {}


def _pic(k: int, size=(480, 360), fmt="JPEG", gps=False, orientation=1, quality=90) -> bytes:
    """A synthetic shelf photo: rectangles placed by `k` (another k, another picture; the same k at another
    size or quality, the same picture to the dHash). JPEG carries an EXIF block like a phone's."""
    key = (k, size, fmt, gps, orientation, quality)
    if key in _PICS:
        return _PICS[key]
    from PIL import Image, ImageDraw
    w, h = size
    img = Image.new("RGB", size, (236, 236, 230))
    d = ImageDraw.Draw(img)
    rnd = random.Random(k)
    for _ in range(7):                                  # fractions of the frame: the layout ignores the size
        fx, fy = rnd.random() * 0.75, rnd.random() * 0.75
        fw, fh = 0.1 + rnd.random() * 0.4, 0.1 + rnd.random() * 0.4
        shade = rnd.randrange(0, 150)
        d.rectangle([fx * w, fy * h, (fx + fw) * w, (fy + fh) * h], fill=(shade, shade // 2, 255 - shade))
    buf = io.BytesIO()
    if fmt == "JPEG":
        exif = Image.Exif()
        exif[0x0112] = orientation                      # Orientation
        exif[0x010F] = "SyntheticCam"                   # Make
        if gps:
            exif[0x8825] = {1: "N", 2: (26.0, 13.0, 0.0), 3: "E", 4: (50.0, 35.0, 0.0)}
        img.save(buf, format="JPEG", quality=quality, exif=exif)
    else:
        img.save(buf, format=fmt, **({"quality": quality} if fmt == "WEBP" else {}))
    _PICS[key] = buf.getvalue()
    return _PICS[key]


T0 = datetime(2026, 7, 12, 13, 42, 11, tzinfo=timezone.utc)      # the day the WhatsApp dump was seeded
T1 = datetime(2026, 9, 27, 8, 0, tzinfo=timezone.utc)            # a capture saved before the R7b migration
CU = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"                     # that capture's own uuid
WA = "WhatsApp Image 2026-07-05 at 4.1{}.20 PM{}.jpeg"           # sent in WhatsApp a week before the seed


def _at(minutes: int) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat()


def _find(i: int, path: str | None, **kw) -> dict:
    row = {"id": i, "name": None, "price_bhd": None, "currency": "BHD", "note": None, "category": None,
           "source": "WhatsApp", "image_path": path, "status": "new", "promoted_item_code": None,
           "source_file": None, "posted_by": "sales-team", "posted_at": _at(i)}
    row.update(kw)
    return row


def FINDS() -> list[dict]:
    return [
        _find(1, "finds/a1.jpeg", source_file=WA.format(1, "")),
        _find(2, "finds/a2.jpeg", source_file=WA.format(2, "")),
        _find(3, "finds/a3.png", source_file=WA.format(3, " (1)")),
        _find(4, "finds/a4.webp", name="Brand X 20W charger", price_bhd=2.4, category="charger",
              note="Seen at   Shop Alpha\x07", status="reviewing", posted_by="Boss@Example.com"),
        _find(5, "finds/f5.webp", name="Brand Y cable", price_bhd=1.5, note="[Competitor price] · cheaper · shop: Shop Alpha",
              source="Market Intel", source_file=f"mi:{CU}:0", posted_by="rep1@example.com", posted_at=T1.isoformat()),
        _find(6, "finds/f6.jpeg", name="Brand Y cable", price_bhd=1.5, note="[Competitor price] · cheaper · shop: Shop Alpha",
              source="Market Intel", source_file=f"mi:{CU}:1", posted_by="rep1@example.com", posted_at=T1.isoformat()),
    ]


def NOTES() -> list[dict]:
    return [{"id": 1, "note": "Shop Beta asks for 20 more power banks", "category": "demand",
             "created_by": "boss@example.com", "created_at": "2026-06-30T05:59:00+00:00", "image_path": None},
            {"id": 2, "note": "", "category": "competitor_price", "created_by": "boss@example.com",
             "created_at": "2026-07-03T19:34:00+00:00", "image_path": "notes/n2.jpg"}]


def OBJECTS() -> dict:
    return {("finds", "finds/a1.jpeg"): _pic(1, gps=True, orientation=6),
            ("finds", "finds/a2.jpeg"): _pic(2),
            ("finds", "finds/a3.png"): _pic(3, fmt="PNG"),
            ("finds", "finds/a4.webp"): _pic(4, fmt="WEBP"),
            ("finds", "finds/f5.webp"): _pic(5, fmt="WEBP"),
            ("finds", "finds/f6.jpeg"): _pic(6),
            ("field-notes", "notes/n2.jpg"): _pic(7)}


def _seed(*, schema=None, finds=None, notes=None, objects=None, missing=None, extra=None) -> _DB:
    tables = {"product_finds": FINDS() if finds is None else finds, "field_notes": NOTES() if notes is None else notes,
              "market_observations": [], "market_photos": [], "salesmen": r7b.SALESMEN,
              "shop_customers": r7b.SHOP_CUSTOMERS}
    tables.update(extra or {})
    fake = _DB(tables, missing=missing, schema=schema)
    for (bucket, path), data in (OBJECTS() if objects is None else objects).items():
        fake.storage.objects[(bucket, path)] = (data, {})
    return fake


def _run(fake, *argv) -> tuple[int, str]:
    """The importer's command line on the stand-in, with the app's own client raising throughout."""
    lines: list[str] = []
    REACHED.clear()
    with _client_guard(), redirect_stderr(io.StringIO()):
        code = imp.main(list(argv), client=fake, out=lines.append)
    assert not REACHED, REACHED
    return code, "\n".join(str(x) for x in lines)


def _obs(fake) -> dict[str, dict]:
    return {o["legacy_ref"]: o for o in fake.rows("market_observations")}


def _photos_of(fake, obs_id) -> list[dict]:
    return sorted((p for p in fake.rows("market_photos") if p["observation_id"] == obs_id), key=lambda p: p["position"])


def _hide_first_read():
    """The first read of market_observations sees nothing: every sighting goes the 'create' way."""
    return r7b._Patched((imp.Importer, "_existing", lambda self, uuids: {}))


ALL_REFS = {"product_finds:1", "product_finds:2", "product_finds:3", "product_finds:4", "product_finds:5,6",
            "field_notes:1", "field_notes:2"}
CORRUPT = b"\xff\xd8\xff\xe0" + b"\x00" * 64


# ═══════════════════════════════════════════════════════════════════════════════
# 1. the dry run, and what may never be reached
# ═══════════════════════════════════════════════════════════════════════════════

@test("fixtures: the invented photos are far apart to the dHash (no accidental near-duplicate)")
def _():
    hashes = [mi.process_photo(data, "x" + ("." + path.rsplit(".", 1)[-1]))["phash"]
              for (_b, path), data in OBJECTS().items()]
    for i in range(len(hashes)):
        for j in range(i + 1, len(hashes)):
            assert mi.hamming(hashes[i], hashes[j]) > mi.PHASH_NEAR, (i, j, mi.hamming(hashes[i], hashes[j]))


@test("dry run (the default): nothing written, every photo read and checked, the report says what --apply would create")
def _():
    fake = _seed()
    before = {k: v[0] for k, v in fake.storage.objects.items()}
    code, text = _run(fake)
    assert code == 0, text
    assert fake.wrote() == [] and fake.uploads() == [], (fake.wrote(), fake.uploads())
    assert fake.rows("market_observations") == [] and fake.rows("market_photos") == []
    assert {k: v[0] for k, v in fake.storage.objects.items()} == before
    assert sorted(c[2] for c in fake.downloads()) == sorted(p for (_b, p) in OBJECTS())
    assert "DRY RUN" in text and "read: 6 product_finds, 2 field_notes -> 7 sightings with 7 photos" in text, text
    assert "time seen from: created_at 2, posted_at 2, whatsapp_file_name 3" in text, text
    assert "to create: 7 sightings (new_product 4, competitor_price 2, shop_asked 1)" in text, text
    assert "photos: 7, each with a 320 px thumbnail" in text
    assert "already in Market Intel (skipped): 0 sightings" in text
    assert "near-duplicate photo: 0 sightings" in text and "cannot be imported" not in text
    assert "with the r8m photo columns (thumb_path, role, sha256)" in text and "Dry run: nothing written." in text
    assert imp.parse_args([]).apply is False


@test("dry run: its client cannot write at all (insert / upsert / update / delete / upload / remove raise before anything is sent)")
def _():
    fake = _seed()
    guard = imp.NoWrites(fake)
    for op in ("insert", "upsert", "update", "delete"):
        try:
            getattr(guard.table("market_observations"), op)({"x": 1})
            raise AssertionError(op)
        except imp.DryRunWrite:
            pass
    for op in ("upload", "update", "remove", "move"):
        try:
            getattr(guard.storage.from_("finds"), op)("market/x.webp", b"x")
            raise AssertionError(op)
        except imp.DryRunWrite:
            pass
    try:
        guard.storage.create_bucket("finds")
        raise AssertionError("create_bucket")
    except imp.DryRunWrite:
        pass
    assert guard.table("product_finds").select("id").limit(1).execute().data
    assert guard.storage.from_("finds").download("finds/a2.jpeg") == OBJECTS()[("finds", "finds/a2.jpeg")]
    assert fake.wrote() == [] and fake.uploads() == []


@test("guard: during every run here the app's own client raises; a run that reached ensure_bucket or mi._upload fails the test")
def _():
    fake = _seed()
    real_create = imp.Importer._create

    def careless(self, o):              # a future regression: an app helper that writes by itself
        product_finds.ensure_bucket()   # swallows every Exception, yet not this one
        return real_create(self, o)
    try:
        with r7b._Patched((imp.Importer, "_create", careless)):
            _run(fake)
        raise AssertionError("the guard did not fire")
    except _RealClient:
        pass
    assert REACHED and "ensure_bucket" in REACHED[0], REACHED
    REACHED.clear()
    try:
        with _client_guard():
            mi._upload({"data": b"x", "ext": ".webp", "content_type": "image/webp"}, datetime.now(timezone.utc))
        raise AssertionError("mi._upload reached no client")
    except _RealClient:
        pass
    assert REACHED and database.get_client is not _no_real_client, "restored after the run"
    REACHED.clear()
    assert fake.wrote() == [] and fake.uploads() == []


# ═══════════════════════════════════════════════════════════════════════════════
# 2. --apply
# ═══════════════════════════════════════════════════════════════════════════════

@test("--apply: one sighting per find / note, source import, stable ids, no rep / shop / item invented, text cleaned not masked")
def _():
    fake = _seed()
    code, text = _run(fake, "--apply")
    assert code == 0, text
    obs = _obs(fake)
    assert set(obs) == ALL_REFS, set(obs)
    for o in obs.values():
        assert o["source"] == "import" and o["item_id"] is None and o["salesman_id"] is None, o
        assert o["shop_customer_id"] is None and o["shop_name"] is None and o["near_duplicate_of"] is None, o
    f1 = obs["product_finds:1"]
    assert f1["client_uuid"] == str(uuid.uuid5(mi.UUID_NS, "import|product_finds|1"))
    assert f1["created_by"] == "sales-team" and f1["kind"] == "new_product" and f1["title"] is None and f1["note"] is None
    assert f1["meta"]["legacy_status"] == "new" and f1["meta"]["source_file"] == WA.format(1, "")
    assert f1["meta"]["photo_sources"] == [{"bucket": "finds", "path": "finds/a1.jpeg", "position": 0}]
    f4 = obs["product_finds:4"]
    assert (f4["title"], f4["price_bhd"], f4["category"]) == ("Brand X 20W charger", "2.400", "CHARGER")
    assert f4["note"] == "Seen at Shop Alpha" and f4["created_by"] == "boss@example.com", f4
    g = obs["product_finds:5,6"]
    assert g["client_uuid"] == CU and g["kind"] == "competitor_price" and g["price_bhd"] == "1.500"
    assert "Shop Alpha" in g["note"] and g["shop_name"] is None, "the note is kept as typed; no shop is guessed from it"
    assert g["meta"]["source_file"] is None and g["created_by"] == "rep1@example.com"
    assert [p["position"] for p in _photos_of(fake, g["id"])] == [0, 1]
    n1, n2 = obs["field_notes:1"], obs["field_notes:2"]
    assert n1["kind"] == "shop_asked" and n1["note"] == "Shop Beta asks for 20 more power banks"
    assert n2["kind"] == "competitor_price" and n2["note"] is None and len(_photos_of(fake, n2["id"])) == 1
    assert n1["client_uuid"] == str(uuid.uuid5(mi.UUID_NS, "import|field_notes|1"))
    assert "created: 7 sightings (new_product 4, competitor_price 2, shop_asked 1)" in text, text
    assert "product_finds and field_notes are unchanged" in text


@test("time: a WhatsApp file name is read as Bahrain time (12-hour, 24-hour, (n) and _hash endings); nothing else is a time")
def _():
    bh = imp.BAHRAIN
    at = datetime(2026, 7, 5, 16, 11, 20, tzinfo=bh)
    for name in ("WhatsApp Image 2026-07-05 at 4.11.20 PM.jpeg", "WhatsApp Image 2026-07-05 at 4.11.20 PM (3).jpeg",
                 "WhatsApp Image 2026-07-05 at 4.11.20\u202fPM.jpeg", "WhatsApp Image 2026-07-05 at 4.11.20 p.m..jpeg",
                 "WhatsApp Video 2026-07-05 at 16.11.20.mp4", "WhatsApp Image 2026-07-05 at 16.11.20_a1b2c3.jpg"):
        assert imp.whatsapp_time(name) == at, name
    assert imp.whatsapp_time("WhatsApp Image 2026-07-05 at 12.30.00 AM.jpeg") == datetime(2026, 7, 5, 0, 30, tzinfo=bh)
    assert imp.whatsapp_time("WhatsApp Image 2026-07-05 at 12.30.00 PM.jpeg") == datetime(2026, 7, 5, 12, 30, tzinfo=bh)
    for bad in ("WhatsApp Image 2026-02-30 at 9.00.00 AM.jpeg", "WhatsApp Image 2026-07-05 at 13.00.00 PM.jpeg",
                "WhatsApp Image 2026-07-05 at 9.61.00 AM.jpeg", "WhatsApp Image 2026-07-05 at 25.00.00.jpeg",
                "IMG_0001.jpg", "photo.jpg", "", None, f"mi:{CU}:0"):
        assert imp.whatsapp_time(bad) is None, bad


@test("time: observed_at is the WhatsApp time when it is no later than posted_at, else posted_at; meta.time_from says which")
def _():
    posted = "2026-07-12T13:43:11+00:00"
    plan = imp.build_plan([
        _find(1, "x1.jpeg", source_file=WA.format(1, ""), posted_at=posted),                       # sent, then seeded
        _find(2, "x2.jpeg", source_file="WhatsApp Image 2026-07-20 at 4.11.20 PM.jpeg", posted_at=posted),  # after posting
        _find(3, "x3.jpeg", source_file="WhatsApp Image 2026-02-30 at 9.00.00 AM.jpeg", posted_at=posted),  # no such day
        _find(4, "x4.jpeg", posted_at=posted),                                                     # a portal upload
        _find(5, "x5.jpeg", source_file="WhatsApp Image 2026-07-01 at 1.30.00 AM.jpeg", posted_at=None),    # no posted_at
        _find(6, "x6.jpeg", posted_at="2026-07-12T16:43:11+03:00"),                                # an offset: kept in UTC
    ], [{"id": 1, "note": "n", "category": "demand", "created_by": None, "created_at": "2026-06-30T05:59:00+00:00",
         "image_path": None}])
    by = {o["legacy_ref"]: o for o in plan["observations"]}
    assert (by["product_finds:1"]["observed_at"], by["product_finds:1"]["meta"]["time_from"]) == \
        ("2026-07-05T13:11:20+00:00", "whatsapp_file_name")
    for ref in ("product_finds:2", "product_finds:3", "product_finds:4"):
        assert (by[ref]["observed_at"], by[ref]["meta"]["time_from"]) == (posted, "posted_at"), by[ref]
    assert (by["product_finds:5"]["observed_at"], by["product_finds:5"]["meta"]["time_from"]) == \
        ("2026-06-30T22:30:00+00:00", "whatsapp_file_name"), "a find without posted_at keeps its WhatsApp time"
    assert by["product_finds:6"]["observed_at"] == posted
    assert (by["field_notes:1"]["observed_at"], by["field_notes:1"]["meta"]["time_from"]) == \
        ("2026-06-30T05:59:00+00:00", "created_at")
    # the photo's month is observed_at's in UTC: 01:30 on 1 July in Bahrain is still June
    master, thumb = imp.photo_paths(by["product_finds:5"], 0, ".webp", ".webp")
    assert master.startswith("market/2026/06/imp-") and thumb == master[: -len(".webp")] + "-t320.webp"


@test("--apply: observed_at is when the find was made, never the import time; the photo's month follows it; client_observed_at is never written")
def _():
    fake = _seed()
    fake.clock = datetime(2026, 10, 14, 7, 0, tzinfo=timezone.utc)
    code, text = _run(fake, "--apply")
    assert code == 0, text
    obs = _obs(fake)
    expect = {"product_finds:1": ("2026-07-05T13:11:20+00:00", "whatsapp_file_name"),
              "product_finds:3": ("2026-07-05T13:13:20+00:00", "whatsapp_file_name"),
              "product_finds:4": (_at(4), "posted_at"),
              "product_finds:5,6": (T1.isoformat(), "posted_at"),
              "field_notes:1": ("2026-06-30T05:59:00+00:00", "created_at")}
    for ref, (when, source) in expect.items():
        assert (obs[ref]["observed_at"], obs[ref]["meta"]["time_from"]) == (when, source), (ref, obs[ref])
    for o in obs.values():
        assert "client_observed_at" not in o, o
        assert o["created_at"] == fake.clock.isoformat() != o["observed_at"]
    assert not [c for c in fake.wrote() if "client_observed_at" in json.dumps(c[2], default=str)]
    assert _photos_of(fake, obs["product_finds:1"]["id"])[0]["path"].startswith("market/2026/07/")
    assert _photos_of(fake, obs["field_notes:2"]["id"])[0]["path"].startswith("market/2026/07/")
    assert _photos_of(fake, obs["product_finds:5,6"]["id"])[0]["path"].startswith("market/2026/09/")
    # one find sent at 01:30 on 1 July (Bahrain): its photo is filed under June, the UTC month
    fake = _seed(finds=[_find(9, "finds/a2.jpeg", source_file="WhatsApp Image 2026-07-01 at 1.30.00 AM.jpeg")], notes=[])
    assert _run(fake, "--apply")[0] == 0
    o9 = _obs(fake)["product_finds:9"]
    assert o9["observed_at"] == "2026-06-30T22:30:00+00:00"
    assert _photos_of(fake, o9["id"])[0]["path"].startswith("market/2026/06/")


@test("--apply: each photo is processed like a live capture (no EXIF / GPS, upright, <= 1600 px WebP, same dHash) + a 320 px thumbnail")
def _():
    from PIL import Image
    big = _pic(8, (2000, 1500), gps=True, orientation=6)        # a full-size phone photo, turned on its side
    fake = _seed(finds=FINDS() + [_find(8, "finds/big.jpeg")],
                 objects={**OBJECTS(), ("finds", "finds/big.jpeg"): big})
    assert _run(fake, "--apply")[0] == 0
    obs = _obs(fake)
    rows = fake.rows("market_photos")
    assert len(rows) == 8
    for p in rows:
        assert p["bucket"] == "finds" and re.fullmatch(r"market/2026/\d\d/imp-[0-9a-f]{32}-\d\.webp", p["path"]), p["path"]
        assert p["thumb_path"] == p["path"][: -len(".webp")] + "-t320.webp"
        assert p["role"] == "product" and p["has_people_flag"] is False
        master, opts = fake.storage.objects[("finds", p["path"])]
        assert opts == {"content-type": "image/webp", "upsert": "true"}, opts
        assert p["sha256"] == hashlib.sha256(master).hexdigest() and p["bytes"] == len(master)
        m = Image.open(io.BytesIO(master))
        assert m.format == "WEBP" and len(m.getexif()) == 0 and max(m.size) <= mi.MAX_EDGE
        assert m.size == (p["width"], p["height"]) and b"SyntheticCam" not in master
        thumb, topts = fake.storage.objects[("finds", p["thumb_path"])]
        t = Image.open(io.BytesIO(thumb))
        assert t.format == "WEBP" and max(t.size) == mi.THUMB_EDGE and topts["content-type"] == "image/webp"
        assert len(thumb) < len(master)
    b8 = _photos_of(fake, obs["product_finds:8"]["id"])[0]
    assert (b8["width"], b8["height"]) == (1200, 1600), "orientation 6 turned upright, 2000 px cut to 1600"
    assert b8["phash"] == mi.process_photo(big, "big.jpg")["phash"], "the same dHash as a live capture"
    t8 = Image.open(io.BytesIO(fake.storage.objects[("finds", b8["thumb_path"])][0]))
    assert t8.size == (240, 320)
    f1 = _photos_of(fake, obs["product_finds:1"]["id"])[0]
    assert (f1["width"], f1["height"]) == (360, 480), "upright: the GPS-tagged photo was taken on its side"
    # 8 old objects + 8 masters + 8 thumbnails, and nothing else
    assert len(fake.storage.objects) == 24


@test("--apply: the old tables and the old photos are never changed (copy, never move)")
def _():
    fake = _seed()
    finds0, notes0 = [dict(r) for r in fake.rows("product_finds")], [dict(r) for r in fake.rows("field_notes")]
    assert _run(fake, "--apply")[0] == 0
    assert fake.wrote("product_finds") == [] and fake.wrote("field_notes") == []
    assert fake.rows("product_finds") == finds0 and fake.rows("field_notes") == notes0
    for (bucket, path), data in OBJECTS().items():
        assert fake.storage.objects[(bucket, path)][0] == data, path
    from PIL import Image
    assert Image.open(io.BytesIO(fake.storage.objects[("finds", "finds/a1.jpeg")][0])).getexif().get_ifd(0x8825)
    assert not [c for c in fake.storage.calls if c[0] == "remove"]
    src = (ROOT / "scripts" / "market_intel_import.py").read_text(encoding="utf-8").lower()
    assert "delete from" not in src and "update product_finds" not in src and "update field_notes" not in src
    assert ".remove(" not in src and ".move(" not in src


# ═══════════════════════════════════════════════════════════════════════════════
# 3. idempotency and the duplicate key
# ═══════════════════════════════════════════════════════════════════════════════

@test("idempotent: a second --apply creates nothing (no row, no upload) and its dry run says everything is in")
def _():
    fake = _seed()
    assert _run(fake, "--apply")[0] == 0
    n_writes, n_up, objects = len(fake.wrote()), len(fake.uploads()), dict(fake.storage.objects)
    code, text = _run(fake, "--apply")
    assert code == 0, text
    assert len(fake.wrote()) == n_writes and len(fake.uploads()) == n_up, "nothing new"
    assert fake.storage.objects == objects
    assert len(fake.rows("market_observations")) == 7 and len(fake.rows("market_photos")) == 7
    assert "already in Market Intel (skipped): 7 sightings" in text and "created: 0 sightings" in text, text
    code, text = _run(fake)
    assert code == 0 and "already in Market Intel (skipped): 7 sightings" in text and "to create: 0 sightings" in text


@test("duplicate key: rows hidden from the first read are never doubled (upsert on client_uuid); this import's own row gets only what it lacks")
def _():
    fake = _seed()
    assert _run(fake, "--apply")[0] == 0
    n_up = len(fake.uploads())
    with _hide_first_read():
        code, text = _run(fake, "--apply")
    assert code == 0, text
    assert len(fake.rows("market_observations")) == 7 and len(fake.rows("market_photos")) == 7
    assert len(fake.uploads()) == n_up, "the sighting is written first and was found there: nothing uploaded"
    assert len([c for c in fake.calls if c[0] == "upsert"]) == 14, "each sighting tried once more and ignored"
    assert "already in Market Intel (skipped): 7 sightings" in text and "created: 0 sightings" in text, text
    # this import's own row lost a photo row: hidden from the first read, it gets that photo back, once
    n2 = _obs(fake)["field_notes:2"]
    fake.tables["market_photos"] = [p for p in fake.rows("market_photos") if p["observation_id"] != n2["id"]]
    with _hide_first_read():
        code, text = _run(fake, "--apply")
    assert code == 0 and "completed (copied before, photos missing): 1 sighting, 1 photo" in text, text
    assert len(_photos_of(fake, n2["id"])) == 1 and len(fake.rows("market_observations")) == 7
    with _hide_first_read():
        assert _run(fake, "--apply")[0] == 0
    assert len(_photos_of(fake, n2["id"])) == 1 and len(fake.rows("market_photos")) == 7


@test("duplicate key: a live capture or another import holding the uuid is never touched and nothing is uploaded for it (seen first or not)")
def _():
    live = {"id": 50, "client_uuid": CU, "source": "rep", "kind": "competitor_price", "item_id": None,
            "observed_at": T1.isoformat(), "legacy_ref": None, "meta": None}
    other = {"id": 51, "client_uuid": str(uuid.uuid5(mi.UUID_NS, "import|product_finds|1")), "source": "import",
             "kind": "new_product", "item_id": None, "observed_at": T1.isoformat(), "legacy_ref": "product_finds:999",
             "meta": {}}
    hexes = (uuid.UUID(CU).hex, uuid.UUID(other["client_uuid"]).hex)
    for hidden in (False, True):
        fake = _seed(extra={"market_observations": [dict(live), dict(other)]})
        if hidden:
            with _hide_first_read():
                code, text = _run(fake, "--apply")
        else:
            code, text = _run(fake, "--apply")
        assert code == 0, (hidden, text)
        rows = fake.rows("market_observations")
        assert [o for o in rows if o["id"] == 50] == [live] and [o for o in rows if o["id"] == 51] == [other], hidden
        assert _photos_of(fake, 50) == [] and _photos_of(fake, 51) == []
        assert not [c for c in fake.storage.calls if c[0] == "upload" and any(h in c[2] for h in hexes)], hidden
        assert len(rows) == 7 and "2 more uuids are held by rows that are not this import" in text, (hidden, text)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. the r7b schema alone, missing tables and columns
# ═══════════════════════════════════════════════════════════════════════════════

@test("r7b schema only: nothing r8m is sent (the stand-in refuses an unknown column), thumbnails are still stored")
def _():
    fake = _seed(schema=R7B)
    code, text = _run(fake, "--apply")
    assert code == 0, text
    assert "not set: market_photos.thumb_path, market_photos.role, market_photos.sha256" in text, text
    assert len(fake.rows("market_observations")) == 7 and len(fake.rows("market_photos")) == 7
    for p in fake.rows("market_photos"):
        assert not {"thumb_path", "role", "sha256"} & set(p), p
        stem = p["path"][: -len(".webp")]
        assert ("finds", stem + "-t320.webp") in fake.storage.objects
    # and the stand-in really does refuse an r8m column on this schema, as PostgREST would
    try:
        fake.table("market_photos").insert({"observation_id": 1, "bucket": "finds", "path": "x", "thumb_path": "y"}).execute()
        raise AssertionError("the stand-in accepted a column the r7b schema does not have")
    except RuntimeError as e:
        assert "PGRST204" in str(e)


@test("refused (exit 2) when the R7b tables are missing: nothing read further, nothing written")
def _():
    for table in ("market_observations", "market_photos"):
        fake = _seed(missing={table})
        for argv in ((), ("--apply",)):
            code, text = _run(fake, *argv)
            assert code == 2 and "REFUSED" in text and "r7b_market_intel_migration.sql" in text, (table, argv, text)
        assert fake.wrote() == [] and fake.uploads() == [] and fake.downloads() == []


@test("a missing old table is read as empty; field_notes without its photo column is read without it")
def _():
    fake = _seed(missing={"product_finds"})
    code, text = _run(fake, "--apply")
    assert code == 0 and "product_finds: not there" in text, text
    assert set(_obs(fake)) == {"field_notes:1", "field_notes:2"}
    schema = dict(R8M, field_notes=R8M["field_notes"] - {"image_path"})
    notes = [{k: v for k, v in n.items() if k != "image_path"} for n in NOTES()]
    fake = _seed(schema=schema, notes=notes)
    code, text = _run(fake, "--apply")
    assert code == 0, text
    assert "field_notes:1" in _obs(fake) and "field_notes:2" not in _obs(fake)
    assert "field_notes:2" in text and "nothing to import: no photo, no text, no price" in text


# ═══════════════════════════════════════════════════════════════════════════════
# 5. rows and photos that cannot be imported, storage errors
# ═══════════════════════════════════════════════════════════════════════════════

def _awkward() -> _DB:
    finds = [
        _find(21, "finds/gone.jpeg"),                                                  # object missing, no text
        _find(22, "finds/broken.jpg", name="Brand Z earbuds"),                         # corrupt JPEG, has a name
        _find(23, "finds/x.heic", note="from an iPhone"),                              # HEIC
        _find(24, "finds/t24.jpeg", name="No time", posted_at=None),                   # no original time
        _find(25, "", name=None),                                                      # nothing at all
        _find(26, "finds/ok26.jpeg", name="Big number", price_bhd=250000),             # a price the column cannot hold
        _find(27, "finds/png-as.jpeg", name="Wrongly named"),                          # PNG bytes under .jpeg
        _find(28, "finds/gone2.jpeg", name="Brand Q lamp"),                            # object missing, has a name
        {**_find(29, "finds/ok26.jpeg"), "id": "x"},                                   # no id
    ]
    objects = {("finds", "finds/broken.jpg"): CORRUPT,
               ("finds", "finds/x.heic"): b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64,
               ("finds", "finds/t24.jpeg"): _pic(24),
               ("finds", "finds/ok26.jpeg"): _pic(26),
               ("finds", "finds/png-as.jpeg"): _pic(27, fmt="PNG")}
    return _seed(finds=finds, notes=[], objects=objects)


@test("unmappable rows are reported with the reason, never crashed on; what can come in, comes in; refusals are recorded once")
def _():
    fake = _awkward()
    code, text = _run(fake)
    assert code == 0 and fake.wrote() == [], text
    code, text = _run(fake, "--apply")
    assert code == 0, text
    obs = _obs(fake)
    assert set(obs) == {"product_finds:22", "product_finds:23", "product_finds:26", "product_finds:27",
                        "product_finds:28"}, set(obs)
    assert "cannot be imported: 4" in text, text
    for ref, why in (("product_finds:21", "finds:finds/gone.jpeg: not found in storage"),
                     ("product_finds:24", "no original time"),
                     ("product_finds:25", "nothing to import: no photo, no text, no price"),
                     ("product_finds:?", "the row has no id")):
        assert re.search(re.escape(ref) + r"\s+.*" + re.escape(why), text), (ref, why, text)
    assert "photos left out (their sighting comes in without them): 3" in text
    assert "finds:finds/broken.jpg: could not be decoded (truncated or corrupt)" in text
    assert "finds:finds/x.heic: not a JPG, PNG or WEBP photo" in text
    assert "finds:finds/gone2.jpeg: not found in storage" in text
    for ref, reason in (("product_finds:22", "could not be decoded (truncated or corrupt)"),
                        ("product_finds:23", "not a JPG, PNG or WEBP photo"),
                        ("product_finds:28", "not found in storage")):
        assert _photos_of(fake, obs[ref]["id"]) == []
        assert obs[ref]["meta"]["photos_not_imported"] == [{"position": 0, "reason": reason}], obs[ref]["meta"]
    assert obs["product_finds:26"]["price_bhd"] is None and "price left out: not between 0 and 100,000 BHD" in text
    assert len(_photos_of(fake, obs["product_finds:26"]["id"])) == 1
    assert len(_photos_of(fake, obs["product_finds:27"]["id"])) == 1, "a PNG under a .jpeg name is read by its bytes"
    # a second run creates nothing and tries none of the recorded photos again
    n_writes, n_down = len(fake.wrote()), len(fake.downloads())
    code, text = _run(fake, "--apply")
    assert code == 0 and len(fake.wrote()) == n_writes, text
    assert [c[2] for c in fake.downloads()[n_down:]] == ["finds/gone.jpeg"], "only the one with nothing else to show"
    assert "already in Market Intel (skipped): 5 sightings" in text and "photos left out" not in text


@test("storage: only 'Object not found' is a missing photo; a missing bucket, a KeyError or an error page stops the run (exit 1), nothing recorded")
def _():
    assert imp._not_found(StorageApiError("Object not found", "not_found", 404))
    assert imp._not_found(StorageApiError("Object not found", "not_found", "400"))
    for other in (StorageApiError("Bucket not found", "not_found", 404), StorageApiError("Bucket not found", "Bucket not found", "404"),
                  StorageApiError("Object not found", "not_found", 500), StorageApiError("Internal Server Error", "internal", 500),
                  KeyError("finds/x.jpeg"), FileNotFoundError("x"), RuntimeError("404 Not Found"),
                  RuntimeError("Object not found")):
        assert not imp._not_found(other), repr(other)
    fake = _seed()
    fake.storage.buckets.discard("field-notes")
    for argv in ((), ("--apply",)):
        code, text = _run(fake, *argv)
        assert code == 1 and "could not read field-notes:notes/n2.jpg from storage: StorageApiError" in text, text
    assert set(_obs(fake)) == {"field_notes:1"}, "nothing at or after the failed read"
    fake = _seed()
    fake.storage.fail_keyerror = True
    code, text = _run(fake, "--apply")
    assert code == 1 and "KeyError" in text and set(_obs(fake)) == {"field_notes:1"}, text
    for o in fake.rows("market_observations"):
        assert not (o.get("meta") or {}).get("photos_not_imported"), o


@test("a photo that fails anywhere while being processed (a codec, Pillow, the thumbnail) is that photo's reason; the run goes on")
def _():
    real = mi.process_photo
    bad = OBJECTS()[("finds", "finds/a4.webp")]

    def flaky(data, name):
        if data == bad:
            raise OSError("broken data stream when reading image file")
        return real(data, name)
    fake = _seed()
    with r7b._Patched((mi, "process_photo", flaky)):
        code, text = _run(fake)
        assert code == 0 and "finds:finds/a4.webp: could not be processed (OSError)" in text, text
        assert "to create: 7 sightings" in text
        code, text = _run(fake, "--apply")
    assert code == 0, text
    f4 = _obs(fake)["product_finds:4"]
    assert _photos_of(fake, f4["id"]) == []
    assert f4["meta"]["photos_not_imported"] == [{"position": 0, "reason": "could not be processed (OSError)"}]
    assert len(fake.rows("market_photos")) == 6
    assert "WARNING" not in text

    def no_thumb(*_a, **_k):            # the capture's own thumbnail step (app.market_intel._small) fails
        raise ValueError("thumbnail failed")
    fake = _seed()
    with r7b._Patched((mi, "_small", no_thumb)):
        code, text = _run(fake)
    assert code == 0 and text.count("could not be processed (ValueError)") == 7, text
    assert "to create: 3 sightings" in text and "cannot be imported: 4" in text, "photo-only finds wait; the rest come in"
    # a refusal is recorded and never tried again: a fault that refuses every photo is shouted, not listed quietly
    assert "WARNING: none of the 7 photos read could be imported" in text, text


# ═══════════════════════════════════════════════════════════════════════════════
# 6. --limit, refusals, failures and the resume
# ═══════════════════════════════════════════════════════════════════════════════

@test("--limit N: a trial of the N oldest, then the next N, then the rest; its dry run shows only N")
def _():
    fake = _seed()
    code, text = _run(fake, "--limit", "1")
    assert code == 0 and "to create: 1 sighting" in text and "--limit 1: 6 more sightings left for a later run" in text, text
    assert fake.wrote() == []
    code, text = _run(fake, "--apply", "--limit", "2")
    assert code == 0, text
    assert set(_obs(fake)) == {"field_notes:1", "field_notes:2"}, "oldest first"
    assert "--limit 2: 5 more sightings left for a later run" in text
    assert _run(fake, "--apply", "--limit", "2")[0] == 0
    assert set(_obs(fake)) == {"field_notes:1", "field_notes:2", "product_finds:1", "product_finds:2"}
    code, text = _run(fake, "--apply")
    assert code == 0 and set(_obs(fake)) == ALL_REFS and "--limit" not in text
    assert len(fake.rows("market_photos")) == 7


@test("refused (exit 2): a bad --limit, --apply from a code worktree, Supabase not configured; --with-phash still parses")
def _():
    fake = _seed()
    for bad in (("--limit", "0"), ("--limit", "x"), ("--bogus",)):
        assert _run(fake, *bad)[0] == 2, bad
    assert fake.wrote() == []
    assert imp.parse_args(["--with-phash"]).with_phash is True and imp.parse_args([]).with_phash is False
    assert imp.in_worktree(Path("C:/w/YQ/.claude/worktrees/agent-x")) is True
    assert imp.in_worktree(Path("C:/w/YQ")) is False and imp.in_worktree(Path("C:/w/worktrees/x")) is False

    def no_client():
        raise RuntimeError("Missing required Supabase env vars: SUPABASE_URL")
    lines: list[str] = []
    with r7b._Patched((imp, "ROOT", Path("C:/w/YQ/.claude/worktrees/agent-x")), (database, "get_client", no_client)):
        assert imp.main(["--apply"], out=lines.append) == 2
        assert "REFUSED: --apply from a code worktree" in lines[-1], lines
        assert imp.main([], out=lines.append) == 2
        assert "REFUSED: Supabase is not configured" in lines[-1], lines


@test("exit 1 when an upload fails: the run stops there; the re-run finishes with no duplicate and no orphan object")
def _():
    fake = _seed()
    fake.storage.fail_after = 3          # note 2's master + thumbnail, find 1's master; its thumbnail fails
    code, text = _run(fake, "--apply")
    assert code == 1 and "ERROR at product_finds:1" in text and "Re-run with --apply" in text, text
    assert set(_obs(fake)) == {"field_notes:1", "field_notes:2", "product_finds:1"}, "nothing after the failure"
    assert _photos_of(fake, _obs(fake)["product_finds:1"]["id"]) == [], "its sighting is there, its photo not yet"
    fake.storage.fail_after = None
    code, text = _run(fake, "--apply")
    assert code == 0 and "completed (copied before, photos missing): 1 sighting, 1 photo" in text, text
    assert set(_obs(fake)) == ALL_REFS and len(fake.rows("market_photos")) == 7
    referenced = {p["path"] for p in fake.rows("market_photos")} | {p["thumb_path"] for p in fake.rows("market_photos")}
    stored = {path for (b, path) in fake.storage.objects if path.startswith("market/")}
    assert stored == referenced, stored ^ referenced


@test("exit 1 when a photo row fails after its sighting was written: the re-run adds the missing photo (completed)")
def _():
    fake = _seed()
    fake.fail[("insert", "market_photos")] = RuntimeError("boom")
    code, text = _run(fake, "--apply")
    assert code == 1 and "market_photos insert failed for field_notes:2" in text, text
    n2 = _obs(fake)["field_notes:2"]
    assert _photos_of(fake, n2["id"]) == []
    code, text = _run(fake)                                   # the dry run sees the gap
    assert code == 0 and "to complete (copied before, photos missing): 1 sighting, 1 photo" in text, text
    code, text = _run(fake, "--apply")
    assert code == 0 and "completed (copied before, photos missing): 1 sighting, 1 photo" in text, text
    assert len(_photos_of(fake, n2["id"])) == 1 and set(_obs(fake)) == ALL_REFS
    assert len(fake.rows("market_photos")) == 7 and len(fake.rows("market_observations")) == 7


@test("resume: a photo refused while a stopped run is finished is recorded in meta like on a first run, and never tried again")
def _():
    fake = _seed()
    fake.fail[("insert", "market_photos")] = RuntimeError("boom")
    assert _run(fake, "--apply")[0] == 1
    fake.storage.objects[("field-notes", "notes/n2.jpg")] = (CORRUPT, {})         # the object went bad meanwhile
    code, text = _run(fake, "--apply")
    assert code == 0, text
    n2 = _obs(fake)["field_notes:2"]
    assert n2["meta"]["photos_not_imported"] == [{"position": 0, "reason": "could not be decoded (truncated or corrupt)"}]
    assert _photos_of(fake, n2["id"]) == [] and "field-notes:notes/n2.jpg: could not be decoded" in text
    meta_writes = [c for c in fake.wrote("market_observations") if c[0] == "update"]
    assert len(meta_writes) == 1 and set(meta_writes[0][2]) == {"meta"}, meta_writes
    n_down = len(fake.downloads())
    code, text = _run(fake, "--apply")
    assert code == 0 and len(fake.downloads()) == n_down, "not tried again"
    assert "already in Market Intel (skipped): 7 sightings" in text and "photos left out" not in text


@test("exit 1 when storage cannot be read (not a missing object): a blip never decides what is imported")
def _():
    fake = _seed()
    fake.storage.fail_download = True
    for argv in ((), ("--apply",)):
        code, text = _run(fake, *argv)
        assert code == 1 and "could not read field-notes:notes/n2.jpg from storage" in text, text
    assert set(_obs(fake)) == {"field_notes:1"}, "only what came before the failed read"


@test("exit 1 on a failed probe read (an error, not a refusal) and on anything unforeseen, with its traceback")
def _():
    fake = _seed()
    fake.fail[("select", "market_observations")] = RuntimeError("connection reset")
    code, text = _run(fake)
    assert code == 1 and "ERROR: could not read market_observations: RuntimeError: connection reset" in text, text

    def boom(*_a, **_k):
        raise ValueError("a bug")
    fake = _seed()
    with r7b._Patched((imp, "build_plan", boom)):
        code, text = _run(fake, "--apply")
    assert code == 1 and "ERROR: unexpected failure" in text and "ValueError: a bug" in text, text
    assert fake.wrote() == [] and fake.uploads() == []


@test("errors are printed masked: the database quotes the failing row (a phone, an email), the report does not")
def _():
    fake = _seed()
    fake.fail[("upsert", "market_observations")] = RuntimeError(json.dumps({
        "code": "23514", "message": "violates check constraint",
        "details": "Failing row contains (9, call 3000 0001, beta@example.com)."}))
    code, text = _run(fake, "--apply")
    assert code == 1 and "Failing row contains" in text, text
    assert "3000 0001" not in text and "beta@example.com" not in text and "[number]" in text and "[email]" in text

    def boom(*_a, **_k):
        raise ValueError("bad row: call 3000 0001 or beta@example.com")
    with r7b._Patched((imp, "build_plan", boom)):
        code, text = _run(_seed(), "--apply")
    assert code == 1 and "ValueError: bad row: call [number] or [email]" in text, text
    assert "3000 0001" not in text and "beta@example.com" not in text


# ═══════════════════════════════════════════════════════════════════════════════
# 7. near-duplicates, masking, paging, invented data
# ═══════════════════════════════════════════════════════════════════════════════

@test("near-duplicate photos are flagged near_duplicate_of the earlier sighting, never skipped")
def _():
    finds = [_find(31, "finds/d31.jpeg"), _find(32, "finds/d32.jpeg"), _find(33, "finds/d33.jpeg")]
    objects = {("finds", "finds/d31.jpeg"): _pic(31, (640, 480), quality=92),
               ("finds", "finds/d32.jpeg"): _pic(31, (400, 300), quality=55),      # the same picture, sent again
               ("finds", "finds/d33.jpeg"): _pic(33)}
    fake = _seed(finds=finds, notes=[], objects=objects)
    code, text = _run(fake)
    assert code == 0 and "near-duplicate photo: 1 sighting" in text, text
    code, text = _run(fake, "--apply")
    assert code == 0, text
    obs = _obs(fake)
    assert len(obs) == 3
    assert obs["product_finds:32"]["near_duplicate_of"] == obs["product_finds:31"]["id"]
    assert obs["product_finds:31"]["near_duplicate_of"] is None and obs["product_finds:33"]["near_duplicate_of"] is None


@test("text: stored as typed (cleaned like a capture); what the script prints is masked")
def _():
    note = "Ask Shop Beta on 3000 0001 or beta@example.com"
    fake = _seed(finds=[_find(41, None, name="Brand X\tpower  bank", note=note)], notes=[], objects={})
    code, text = _run(fake, "--apply", "--list")
    assert code == 0, text
    o = _obs(fake)["product_finds:41"]
    assert o["note"] == note and o["title"] == "Brand X power bank"
    assert "3000 0001" not in text and "beta@example.com" not in text, text
    assert "Brand X power bank" in text
    code, text = _run(_seed(finds=[_find(42, None, note=note)], notes=[], objects={}), "--list")
    assert "Ask Shop Beta on [number] or [email]" in text, text


@test("reads page past PostgREST's row cap: every old row is read, in order, with the offset")
def _():
    finds = [_find(i, None, name=f"Brand X item {i}") for i in range(101, 106)]
    notes = [{"id": i, "note": f"note {i}", "category": "other", "created_by": "boss@example.com",
              "created_at": "2026-07-01T00:00:00+00:00", "image_path": None} for i in range(1, 4)]
    fake = _seed(finds=finds, notes=notes, objects={})
    with r7b._Patched((imp, "PAGE", 2)):
        code, text = _run(fake, "--apply")
    assert code == 0, text
    assert len(fake.rows("market_observations")) == 8
    assert [(a, b) for t, a, b in fake.ranges if t == "product_finds"] == [(0, 1), (2, 3), (4, 5)]
    assert [(a, b) for t, a, b in fake.ranges if t == "field_notes"] == [(0, 1), (2, 3)]
    assert len(fake.ranges) > 5, "market_photos is paged too"
    # and at the real cap: the shared stand-in answers at most r7b.MAX_ROWS rows per read, as PostgREST does
    n = r7b.MAX_ROWS + 5
    fake = _seed(finds=[_find(i, None, name=f"Brand X item {i}") for i in range(1, n + 1)], notes=[], objects={})
    code, text = _run(fake)
    assert code == 0 and f"read: {n} product_finds" in text and f"to create: {n} sightings" in text, text
    assert [(a, b) for t, a, b in fake.ranges if t == "product_finds"] == [(0, 999), (1000, 1999)]


@test("the test data is invented: reserved domains only (example.com, .invalid), one deliberate fake phone, no real-looking amount")
def _():
    src = Path(__file__).read_text(encoding="utf-8")
    emails = set(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", src))
    assert emails and all(e.lower().endswith(("@example.com", ".invalid")) for e in emails), emails
    from app.ai_insights import PHONE_RE
    assert {m.group(0) for m in PHONE_RE.finditer(src)} <= {"3000 0001"}, set(PHONE_RE.findall(src))
    assert not re.search(r"(?<![\w.,])(?:\d{1,3}(?:,\d{3})+|\d{4,})\.\d{3}(?!\d)", src)


def main() -> int:
    passed = failed = 0
    for name, fn in TESTS:
        try:
            fn()
            print(f"PASS  {name}")
            passed += 1
        except (Exception, _RealClient):  # noqa: BLE001 — a _RealClient escaping a test fails that test only
            print(f"FAIL  {name}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
