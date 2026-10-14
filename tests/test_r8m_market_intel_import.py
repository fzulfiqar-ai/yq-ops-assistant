"""R8m — Market Intel v2, Phase 0: importing the old Product Finds and Field Notes (plan Part F, F15).

    python -m tests.test_r8m_market_intel_import

Same lightweight runner as tests/test_r7b_market_intel.py, on its in-memory PostgREST + Storage stand-in
(imported from it and extended here: a column list per table, so a column that is not there answers the
way PostgREST does, paging that honours the offset, the upsert the importer uses, the (bucket, path) key
of market_photos, and storage reads that can be told to fail). SUPABASE_URL, SUPABASE_KEY and
DATABASE_URL are pinned to ci.invalid BEFORE any import: a worktree under the main checkout would
otherwise find the production .env through python-dotenv's walk-up. scripts/market_intel_import.py runs
here only against the stand-in; nothing reaches a database, a bucket or the network. Every name, email,
price and photo is invented ("Shop Alpha", "Brand X", rep1@example.com, drawn rectangles).

Covered:
  * the dry run (the default) writes nothing (no insert, upsert, update or delete, no upload) yet reads
    and checks every photo, and says what --apply would create: per kind, photos and thumbnails,
    sightings already copied, rows that cannot be imported and why; its client cannot write at all;
  * --apply: one sighting per find / note (a fallback capture's rows stay one sighting, under its own
    uuid), source 'import', no rep, shop or item invented; the original time kept as observed_at and
    client_observed_at; every photo processed like a live capture (no EXIF / GPS, upright, <= 1600 px
    WebP, the same dHash) with a 320 px WebP thumbnail next to it as <stem>-t320.webp; thumb_path,
    sha256 and role set; the old tables and objects untouched;
  * a second --apply creates nothing; a capture that reached Market Intel live is left alone;
  * the r7b schema alone: nothing r8m is sent (the stand-in refuses an unknown column), thumbnails are
    still stored; field_notes without its photo column; a missing old table;
  * rows that cannot be mapped are reported, never crashed on (a missing object, a corrupt or HEIC photo,
    no time, nothing at all, no id, a price the column cannot hold); a wrongly named photo is still read;
  * --limit N: the N oldest, then the next N, then the rest;
  * refused (exit 2): the R7b tables missing, --apply from a code worktree, Supabase not configured, a bad
    --limit. Exit 1 when a read or write fails; the re-run resumes without a duplicate or an orphan;
  * near-duplicate photos are flagged, never skipped; what the script prints is masked;
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
from app import market_intel as mi  # noqa: E402
from scripts import market_intel_import as imp  # noqa: E402

TESTS: list[tuple[str, object]] = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


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
    """r7b's query stand-in plus: the table's columns are checked (42703 on a read, PGRST204 on a write,
    as PostgREST answers), range() honours its offset, upsert(on_conflict, ignore_duplicates), and the
    (bucket, path) key of market_photos."""

    def __init__(self, db, table):
        super().__init__(db, table)
        self._cols, self._offset, self._conflict, self._ignore = "*", None, "", False

    def select(self, cols="*", count=None, head=False):
        self._cols = cols
        return super().select(cols, count=count, head=head)

    def range(self, a, b):
        self._offset = a
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
        if self.op == "select" and self._offset is not None:
            lim, self.lim = self.lim, None
            res = super().execute()
            res.data = res.data[self._offset: self._offset + lim]
            return res
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
    def upload(self, path, data, opts=None):
        st = self.st
        if st.fail_after is not None and st.uploads >= st.fail_after:
            raise RuntimeError("storage down")
        st.uploads += 1
        return super().upload(path, data, opts)

    def download(self, path):
        self.st.calls.append(("download", self.name, path))
        if self.st.fail_download:
            raise RuntimeError("503 Service Unavailable")
        return super().download(path)


class _Storage(r7b._Storage):
    def __init__(self):
        super().__init__()
        self.fail_after: int | None = None      # uploads allowed before the next one fails
        self.fail_download = False
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


def _at(minutes: int) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat()


def _find(i: int, path: str | None, **kw) -> dict:
    row = {"id": i, "name": None, "price_bhd": None, "currency": "BHD", "note": None, "category": None,
           "source": "WhatsApp", "image_path": path, "status": "new", "promoted_item_code": None,
           "source_file": f"WhatsApp Image 2026-01-01 at 9.00.0{i % 10} AM.jpeg", "posted_by": "sales-team",
           "posted_at": _at(i)}
    row.update(kw)
    return row


def FINDS() -> list[dict]:
    return [
        _find(1, "finds/a1.jpeg"),
        _find(2, "finds/a2.jpeg"),
        _find(3, "finds/a3.png"),
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
    lines: list[str] = []
    with redirect_stderr(io.StringIO()):
        code = imp.main(list(argv), client=fake, out=lines.append)
    return code, "\n".join(str(x) for x in lines)


def _obs(fake) -> dict[str, dict]:
    return {o["legacy_ref"]: o for o in fake.rows("market_observations")}


def _photos_of(fake, obs_id) -> list[dict]:
    return sorted((p for p in fake.rows("market_photos") if p["observation_id"] == obs_id), key=lambda p: p["position"])


ALL_REFS = {"product_finds:1", "product_finds:2", "product_finds:3", "product_finds:4", "product_finds:5,6",
            "field_notes:1", "field_notes:2"}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. the dry run
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
    downloads = sorted(c[2] for c in fake.storage.calls if c[0] == "download")
    assert downloads == sorted(p for (_b, p) in OBJECTS()), downloads
    assert "DRY RUN" in text and "read: 6 product_finds, 2 field_notes -> 7 sightings with 7 photos" in text, text
    assert "to create: 7 sightings (new_product 4, competitor_price 2, shop_asked 1)" in text, text
    assert "photos: 7, each with a 320 px thumbnail" in text
    assert "already in Market Intel (skipped): 0 sightings" in text
    assert "near-duplicate photo: 0 sightings" in text and "cannot be imported" not in text
    assert "every r8m column" in text and "Dry run: nothing written." in text
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
    assert f1["meta"]["legacy_status"] == "new" and f1["meta"]["source_file"].startswith("WhatsApp Image")
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


@test("--apply: the original time is kept (observed_at and client_observed_at), never the import time")
def _():
    fake = _seed()
    fake.clock = datetime(2026, 10, 14, 7, 0, tzinfo=timezone.utc)
    assert _run(fake, "--apply")[0] == 0
    obs = _obs(fake)
    assert obs["product_finds:1"]["observed_at"] == "2026-07-12T13:43:11+00:00"
    assert obs["product_finds:4"]["observed_at"] == _at(4)
    assert obs["product_finds:5,6"]["observed_at"] == T1.isoformat()
    assert obs["field_notes:1"]["observed_at"] == "2026-06-30T05:59:00+00:00"
    for o in obs.values():
        assert o["client_observed_at"] == o["observed_at"], o
        assert o["created_at"] == fake.clock.isoformat() != o["observed_at"]
    # the photo lives under the month it was seen, not the month it was imported
    assert _photos_of(fake, obs["field_notes:2"]["id"])[0]["path"].startswith("market/2026/07/")
    assert _photos_of(fake, obs["product_finds:5,6"]["id"])[0]["path"].startswith("market/2026/09/")


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
        assert t.format == "WEBP" and max(t.size) == imp.THUMB_EDGE and topts["content-type"] == "image/webp"
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


@test("idempotent: a capture that reached Market Intel live under the same uuid is left as it is")
def _():
    live = {"id": 50, "client_uuid": CU, "source": "rep", "kind": "competitor_price", "item_id": None,
            "observed_at": T1.isoformat(), "legacy_ref": None, "meta": None}
    fake = _seed(extra={"market_observations": [live]})
    code, text = _run(fake, "--apply")
    assert code == 0, text
    assert [o for o in fake.rows("market_observations") if o["client_uuid"] == CU] == [live]
    assert _photos_of(fake, 50) == [] and len(fake.rows("market_observations")) == 7
    assert "1 more arrived as live captures" in text, text


# ═══════════════════════════════════════════════════════════════════════════════
# 3. the r7b schema alone, missing tables and columns
# ═══════════════════════════════════════════════════════════════════════════════

@test("r7b schema only: nothing r8m is sent (the stand-in refuses an unknown column), thumbnails are still stored")
def _():
    fake = _seed(schema=R7B)
    code, text = _run(fake, "--apply")
    assert code == 0, text
    assert "not set: market_observations.client_observed_at, market_photos.thumb_path, market_photos.role, " \
           "market_photos.sha256" in text, text
    assert len(fake.rows("market_observations")) == 7 and len(fake.rows("market_photos")) == 7
    for o in fake.rows("market_observations"):
        assert "client_observed_at" not in o
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
        assert fake.wrote() == [] and fake.uploads() == []
        assert not [c for c in fake.storage.calls if c[0] == "download"]


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
# 4. rows that cannot be mapped
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
        {**_find(28, "finds/ok26.jpeg"), "id": "x"},                                   # no id
    ]
    objects = {("finds", "finds/broken.jpg"): b"\xff\xd8\xff\xe0" + b"\x00" * 64,
               ("finds", "finds/x.heic"): b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64,
               ("finds", "finds/t24.jpeg"): _pic(24),
               ("finds", "finds/ok26.jpeg"): _pic(26),
               ("finds", "finds/png-as.jpeg"): _pic(27, fmt="PNG")}
    return _seed(finds=finds, notes=[], objects=objects)


@test("unmappable rows are reported with the reason, never crashed on; what can come in, comes in")
def _():
    fake = _awkward()
    code, text = _run(fake)
    assert code == 0, text
    assert fake.wrote() == []
    code, text = _run(fake, "--apply")
    assert code == 0, text
    obs = _obs(fake)
    assert set(obs) == {"product_finds:22", "product_finds:23", "product_finds:26", "product_finds:27"}, set(obs)
    assert "cannot be imported: 4" in text, text
    for ref, why in (("product_finds:21", "finds:finds/gone.jpeg: not found in storage"),
                     ("product_finds:24", "no original time"),
                     ("product_finds:25", "nothing to import: no photo, no text, no price"),
                     ("product_finds:?", "the row has no id")):
        assert re.search(re.escape(ref) + r"\s+.*" + re.escape(why), text), (ref, why, text)
    assert "photos left out (their sighting comes in without them): 2" in text
    assert "finds:finds/broken.jpg: could not be decoded (truncated or corrupt)" in text
    assert "finds:finds/x.heic: not a JPG, PNG or WEBP photo" in text
    assert _photos_of(fake, obs["product_finds:22"]["id"]) == [] and _photos_of(fake, obs["product_finds:23"]["id"]) == []
    assert obs["product_finds:22"]["meta"]["photos_not_imported"] == [
        {"position": 0, "reason": "could not be decoded (truncated or corrupt)"}]
    assert obs["product_finds:26"]["price_bhd"] is None and "price left out: not between 0 and 100,000 BHD" in text
    assert len(_photos_of(fake, obs["product_finds:26"]["id"])) == 1
    assert len(_photos_of(fake, obs["product_finds:27"]["id"])) == 1, "a PNG under a .jpeg name is read by its bytes"
    # a second run creates nothing and does not try the refused photos again
    n_writes, n_down = len(fake.wrote()), len([c for c in fake.storage.calls if c[0] == "download"])
    code, text = _run(fake, "--apply")
    assert code == 0 and len(fake.wrote()) == n_writes, text
    assert len([c for c in fake.storage.calls if c[0] == "download"]) == n_down + 1, "only the missing object, again"
    assert "already in Market Intel (skipped): 4 sightings" in text and "photos left out" not in text


@test("plan (pure): no id, no time and nothing at all are skipped with the reason; a v1 plan keeps its shape")
def _():
    plan = imp.build_plan([_find(1, "finds/a1.jpeg"), {"id": None}, _find(3, "", posted_at=None, name="x"),
                           _find(4, None)], [{"id": 9, "note": " ", "category": "other", "created_by": None,
                                              "created_at": "2026-07-01T00:00:00+00:00", "image_path": None}])
    assert plan["counts"] == {"finds": 4, "notes": 1, "observations": 1, "photos": 1}
    assert sorted((s["ref"], s["reason"]) for s in plan["skipped"]) == [
        ("field_notes:9", "nothing to import: no photo, no text, no price"),
        ("product_finds:3", "no original time (the old row has none)"),
        ("product_finds:4", "nothing to import: no photo, no text, no price"),
        ("product_finds:?", "the row has no id")]
    o = plan["observations"][0]
    assert o["photos"] == [{"bucket": "finds", "path": "finds/a1.jpeg", "position": 0}] and o["warnings"] == []
    assert imp.build_plan([_find(1, "finds/a1.jpeg")], [])["observations"][0]["client_uuid"] == o["client_uuid"]


# ═══════════════════════════════════════════════════════════════════════════════
# 5. --limit, refusals, failures and the resume
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
    from app import database

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
    assert set(_obs(fake)) == {"field_notes:1", "field_notes:2"}, "nothing after the failure was written"
    fake.storage.fail_after = None
    code, text = _run(fake, "--apply")
    assert code == 0, text
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


# ═══════════════════════════════════════════════════════════════════════════════
# 6. near-duplicates, masking, paging, invented data
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
        except Exception:  # noqa: BLE001
            print(f"FAIL  {name}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
