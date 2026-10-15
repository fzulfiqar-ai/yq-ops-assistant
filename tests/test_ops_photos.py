"""The photo archive: scripts/yq_photos.py (an encrypted copy of the Market Intelligence photos, on this PC).

    python -m tests.test_ops_photos

Same lightweight runner as the other suites (no pytest). No database, no network, no real secret:
SUPABASE_URL, SUPABASE_KEY and DATABASE_URL are pinned to ci.invalid before any import (a worktree under the
main checkout would otherwise find the real .env), YQ_SECRETS_OFF=1 and the test password is invented. Storage
and the database are a fake source / a fake session; the one test of the download path uses an in-memory HTTP
transport behind the real read-only client. Every name, path and number below is invented. Tests that need
7-Zip print SKIP where it is not installed; the rest stub the packing and the unpacking.

Covered:
  * without --apply: counts and sizes per bucket, the rows that point at a photo, rows without an object and
    objects without a row; nothing downloaded, written or recorded, and the backup password is never read;
  * --apply is refused without the password, without 7-Zip, in a synced folder and on a full drive, before
    anything is downloaded or created;
  * the archive: every object downloaded and hashed, the sha256 of the database compared where there is one
    (match true / false / none), manifest.json, the encrypted pack, the work folder deleted, one row of
    drills.jsonl with counts only; the password and the key never in any output, row or manifest;
  * a missing object, a row without an object, a sha256 that differs, a download that fails for good and a
    size that differs from the listing are REPORTED (exit 1, the list) and the rest is still archived; a
    transient failure is retried; a failed pack leaves no work folder and spares an earlier archive;
  * names inside the archive: a key that is not a plain name (.., drive letters, reserved names, case clashes)
    gets a hashed name and can never be written outside the work folder;
  * verify: files checked against the manifest (sha256 and size), random photos opened with Pillow, a changed,
    missing, extra, unsafe or corrupt file found, the unpacked copy always deleted;
  * the real source: the SQL is only SELECT (the read-only session's own gate), the rows are read before the
    object list, absent tables and columns are notes, folder placeholders are left out, a download is a plain GET
    through the read-only client and a missing object is told apart from a failure;
  * the module can neither write to storage nor to the database (AST), and CI runs this suite.
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import json
import os
import random
import re
import shutil
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ["SUPABASE_URL"] = "https://ci.invalid"
os.environ["SUPABASE_KEY"] = "ci-key"
os.environ["DATABASE_URL"] = "postgresql://ci:ci@ci.invalid:5432/ci"
os.environ.pop("YQ_MAIN_CHECKOUT", None)
os.environ["YQ_SECRETS_OFF"] = "1"

import httpx  # noqa: E402

from scripts.ops import db  # noqa: E402  (before any app import, as every command does)
from scripts.ops import config, paths  # noqa: E402
from scripts import yq_backup as yb  # noqa: E402
from scripts import yq_photos as yp  # noqa: E402
from scripts import yq_secrets  # noqa: E402

TESTS: list[tuple[str, object]] = []
PASSWORD = "an-invented-test-password"
NOW = datetime(2026, 10, 15, 9, 30, 0)
LATER = datetime(2026, 10, 15, 9, 45, 0)


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


class data_root:
    def __enter__(self) -> Path:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        paths.set_data_root(self._tmp.name)
        return paths.data_root()

    def __exit__(self, *exc):
        paths.set_data_root(None)
        self._tmp.cleanup()
        return False


class patched:
    """Replace attributes for the length of a block: patched((module, "name", value), ...)."""

    def __init__(self, *triples):
        self.triples = triples

    def __enter__(self):
        self.old = [(obj, attr, getattr(obj, attr)) for obj, attr, _v in self.triples]
        for obj, attr, value in self.triples:
            setattr(obj, attr, value)
        return self

    def __exit__(self, *exc):
        for obj, attr, value in reversed(self.old):
            setattr(obj, attr, value)
        return False


def nosleep(_seconds):
    return None


def with_secrets(root: Path, password=PASSWORD, exe="7z.exe"):
    """The backup password, 7-Zip and the backup folder as the archive sees them."""
    return patched((yq_secrets, "backup_password", lambda env=None: (password, "process_environment") if password else (None, None)),
                   (yb, "find_7z", lambda: exe), (yb, "backup_root", lambda: root / "store"))


# ── invented photos and a fake storage ───────────────────────────────────────

def png(color, size=(6, 6)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def jpeg(color, size=(8, 8)) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


MASTER = ("finds", "market/2026/10/aa11.png")
THUMB = ("finds", "market/2026/10/aa11-t320.png")
LEGACY_FIND = ("finds", "finds/bb22.png")
NOTE = ("field-notes", "notes/cc33.jpg")
ORPHAN = ("finds", "market/2026/10/dd44.png")


def store_objects() -> dict:
    return {MASTER: png((200, 20, 20)), THUMB: png((200, 20, 20), (3, 3)), LEGACY_FIND: png((20, 200, 20)),
            NOTE: jpeg((20, 20, 200)), ORPHAN: png((9, 9, 9))}


def ref(key, source, row_id, sha256=None) -> dict:
    return {"bucket": key[0], "path": key[1], "source": source, "id": row_id, "sha256": sha256}


def store_refs(objects: dict) -> list[dict]:
    return [ref(MASTER, "market_photos.path", 1, sha(objects[MASTER])), ref(THUMB, "market_photos.thumb_path", 1),
            ref(LEGACY_FIND, "product_finds.image_path", 7), ref(NOTE, "field_notes.image_path", 3)]


def tables_of(refs: list[dict]) -> dict:
    out: dict[str, dict] = {}
    for r in refs:
        t = out.setdefault(r["source"].split(".")[0], {"rows": 0, "refs": 0, "checksums": 0})
        t["refs"] += 1
        t["checksums"] += 1 if r["sha256"] else 0
    for t in out.values():
        t["rows"] = t["refs"]
    return out


class FakeSource:
    """What SupabaseSource gives: read() and download(). `objects` is {(bucket, path): bytes}; a key in `missing`
    is listed but storage answers "not found"; `failing` is {key: failures before an answer} (99 = for good);
    `listed` overrides the size the listing states."""

    def __init__(self, objects=None, refs=None, missing=(), failing=None, listed=None, notes=()):
        self.objects = dict(store_objects() if objects is None else objects)
        self.refs = [dict(r) for r in (store_refs(self.objects) if refs is None else refs)]
        self.missing, self.failing, self.listed = set(missing), dict(failing or {}), dict(listed or {})
        self.notes = list(notes)
        self.downloads: list[tuple] = []
        self.closed = False

    def read(self) -> dict:
        objs = [{"bucket": b, "path": p, "size": self.listed.get((b, p), len(d))} for (b, p), d in sorted(self.objects.items())]
        return {"objects": objs, "refs": self.refs, "tables": tables_of(self.refs), "notes": self.notes}

    def download(self, bucket, path):
        key = (bucket, path)
        self.downloads.append(key)
        if key in self.missing:
            raise yp.ObjectMissing("object not found")
        if self.failing.get(key, 0) > 0:
            self.failing[key] -= 1
            raise yp.DownloadFailed("storage said 500: boom")
        return self.objects[key]

    def close(self):
        self.closed = True


class FakePack:
    """yq_photos.pack without 7-Zip: keeps a copy of the folder it was asked to pack, writes a stand-in archive."""

    def __init__(self, keep: Path, fail: str | None = None):
        self.keep, self.fail, self.calls = keep, fail, []

    def __call__(self, folder, archive, password, exe):
        self.calls.append((Path(folder), Path(archive), password, exe))
        if self.fail:
            raise yp.PhotoError(self.fail)
        if self.keep.exists():
            shutil.rmtree(self.keep)
        shutil.copytree(folder, self.keep)
        Path(archive).write_bytes(b"stand-in archive")
        return f"Files: {sum(1 for p in Path(folder).rglob('*') if p.is_file())}"


def run_archive(root: Path, source=None, apply=True, out=None, pack=None, now=NOW, password=PASSWORD, exe="7z.exe"):
    """archive() on a fake source with the packing stubbed: (exit code, output lines, the FakePack, the source)."""
    source = source if source is not None else FakeSource()
    pack = pack or FakePack(root / "kept")
    said: list[str] = []
    with with_secrets(root, password, exe), patched((yp, "pack", pack)):
        code = yp.archive(apply, out, source, said.append, now=now, sleep=nosleep)
    return code, said, pack, source


def drill_rows(root: Path) -> list[dict]:
    f = root / "business_data" / "ops" / "drills.jsonl"
    return [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines()] if f.is_file() else []


def photos_folder(root: Path) -> Path:
    return root / "store" / "photos"


# ── names inside the archive ─────────────────────────────────────────────────

@test("a plain object key keeps its path; any other key gets a hashed name that cannot leave the folder")
def _():
    used: set[str] = set()
    assert yp.local_name("finds", "market/2026/10/ab12-t320.webp", used) == "files/finds/market/2026/10/ab12-t320.webp"
    strange = ["../x.png", "a/../b.png", "a\\b.png", "C:/x.png", "con.png", "nul", "x.", "a b.png", "\u00fcn\u00ef.png",
               "a//b.png", "", ".hidden", "-lead.png", "x" * 300, "/abs.png", "a/./b.png", "lpt1.txt"]
    seen = set()
    for key in strange:
        name = yp.local_name("finds", key, set())
        assert name.startswith("files/_odd/") and name.count("/") == 2, (key, name)
        assert ".." not in name and ":" not in name and "\\" not in name and not name.startswith("/"), name
        assert name == yp.local_name("finds", key, set()), "the same key always gets the same name"
        seen.add(name)
    assert len(seen) == len(strange), "different keys, different names"
    assert yp.local_name("finds", "pic.WEBP", set()) == "files/finds/pic.WEBP"
    assert yp.local_name("finds", "a b.WEBP", set()).endswith(".webp")
    clash: set[str] = set()
    first = yp.local_name("finds", "Same.png", clash)
    second = yp.local_name("finds", "same.png", clash)
    assert first == "files/finds/Same.png" and second.startswith("files/_odd/"), "one file on Windows: the second is renamed"
    assert yp.local_name("odd bucket", "a.png", set()).startswith("files/_odd/")


@test("a key that is the folder of another key (market/x, market/x/y.png) gets a hashed name; no order or case can break a write")
def _():
    for first, second in (("market/x", "market/x/y.png"), ("market/x/y.png", "market/x"), ("Market/X/y.png", "market/x"),
                          ("a/b/c/d.png", "a/b"), ("a", "a/b/c/d.png"), ("a/b", "A")):
        used: set[str] = set()
        one = yp.local_name("finds", first, used)
        two = yp.local_name("finds", second, used)
        assert one == "files/finds/" + first, (first, one)
        assert two.startswith("files/_odd/"), (first, second, two)
    # whatever the keys, every name can be written on a real disk and no two are the same file on Windows
    rng = random.Random(7)
    parts = ["a", "A", "b", "x.png", "X.PNG"]
    for _trial in range(200):
        keys = sorted({"/".join(rng.choice(parts) for _ in range(rng.randint(1, 4))) for _ in range(rng.randint(2, 8))})
        used = set()
        names = [yp.local_name("finds", key, used) for key in keys]
        assert len({n.lower() for n in names}) == len(names), (keys, names)
        with tempfile.TemporaryDirectory() as tmp:
            for name in names:
                target = Path(tmp) / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"x")                                  # FileExistsError before the fix


# ── without --apply ──────────────────────────────────────────────────────────

@test("without --apply: counts per bucket, rows, rows without an object, objects without a row; nothing else happens")
def _():
    objects = store_objects()
    del objects[THUMB]                                  # a row whose object is gone
    refs = store_refs(store_objects())
    src = FakeSource(objects, refs, notes=["a note from the reader"])

    def boom(env=None):
        raise AssertionError("a dry run must not look for the backup password")
    with data_root() as root, patched((yq_secrets, "backup_password", boom), (yb, "backup_root", lambda: root / "store")):
        said: list[str] = []
        assert yp.archive(False, None, src, said.append, now=NOW) == 0
        text = "\n".join(said)
        assert "bucket finds" in text and "bucket field-notes" in text
        assert re.search(r"bucket finds\s+3 objects", text) and re.search(r"bucket field-notes\s+1 objects", text), text
        assert re.search(r"all buckets\s+4 objects", text), text
        assert re.search(r"market_photos\s+2 rows, 2 object reference\(s\), 1 with a recorded sha256", text), text
        assert re.search(r"product_finds\s+1 rows", text) and re.search(r"field_notes\s+1 rows", text), text
        assert "Rows whose object is not in storage: 1" in text and "market_photos.thumb_path: row id 1" in text, text
        assert "Objects without a database row: 1 (archived all the same)" in text, text
        assert "note: a note from the reader" in text
        assert "Dry run: nothing was downloaded or changed" in text
        assert src.downloads == [] and src.closed is False, "a source handed in is the caller's to close"
        assert not (root / "store").exists() and not (root / "business_data").exists(), "a dry run writes nothing"
        for key in (MASTER, ORPHAN, THUMB):
            assert key[1] not in text, "a dry run prints counts and row ids, never an object path"


@test("a source the dry run opened itself is closed; a source that cannot be read stops the run with one sentence")
def _():
    class Broken:
        closed = False

        def read(self):
            raise RuntimeError("could not connect to postgresql://user:secret@db.invalid:5432/x")

        def close(self):
            self.closed = True
    opened = Broken()
    said: list[str] = []
    with data_root() as root, patched((yp.SupabaseSource, "open", classmethod(lambda cls: opened)),
                                      (yb, "backup_root", lambda: root / "store")):
        assert yp.archive(False, None, None, said.append) == 2
    text = "\n".join(said)
    assert "Not run: the photo list could not be read" in text and "Nothing was changed" in text
    assert "secret" not in text and "db.invalid" not in text and "<address>" in text, "no address, no password"
    assert opened.closed is True


@test("a work folder or an unpacked copy left by an interrupted run is reported (unencrypted photos) and left alone")
def _():
    with data_root() as root, with_secrets(root):
        left = [photos_folder(root) / "work_20261014_010101", photos_folder(root) / "verify_20261014_020202"]
        for folder in left:
            folder.mkdir(parents=True)
            (folder / "x.bin").write_bytes(b"1")
        (photos_folder(root) / "work_20261014_010101.7z").write_bytes(b"a staging file is not a folder")
        said: list[str] = []
        assert yp.archive(False, None, FakeSource(), said.append, now=NOW) == 0
        warning = next(s for s in said if "UNENCRYPTED" in s)
        assert "2 folder(s)" in warning and "work_20261014_010101" in warning and "verify_20261014_020202" in warning, warning
        assert "work_20261014_010101.7z" not in warning, "a staging file is not a folder of photos"
        assert all(folder.is_dir() for folder in left), "never deleted by a dry run"


# ── --apply: refusals ────────────────────────────────────────────────────────

@test("--apply without the password, or without 7-Zip, changes nothing and downloads nothing")
def _():
    with data_root() as root:
        for password, exe, words in ((None, "7z.exe", ["no backup password"]), (PASSWORD, None, ["7-Zip is not installed"]),
                                     (None, None, ["no backup password", "7-Zip is not installed"])):
            src = FakeSource()
            code, said, pack, _src = run_archive(root, src, password=password, exe=exe)
            text = "\n".join(said)
            assert code == 2 and "Nothing was changed" in text, text
            assert all(w in text for w in words), text
            assert src.downloads == [] and pack.calls == []
            assert not (root / "store").exists() and drill_rows(root) == []


@test("--apply is refused in a folder OneDrive syncs (--out, or the default folder) and on a full drive")
def _():
    with data_root() as root:
        src = FakeSource()
        code, said, pack, _s = run_archive(root, src, out=root / "OneDrive - Test Co" / "photos")
        assert code == 2 and "synced folder" in "\n".join(said) and src.downloads == [], said
        assert not (root / "OneDrive - Test Co").exists()

        def refuse():
            raise SystemExit("REFUSING: the backup folder is inside a synced folder (OneDrive).")
        said = []
        with patched((yq_secrets, "backup_password", lambda env=None: (PASSWORD, "process_environment")),
                     (yb, "find_7z", lambda: "7z.exe"), (yb, "backup_root", refuse)):
            assert yp.archive(True, None, src, said.append, now=NOW) == 2
        assert "REFUSING" in "\n".join(said) and src.downloads == []

        from scripts import nightly_backup
        inside = ROOT / "photo-copies-that-must-not-exist"                    # the code folder is a public repository
        with patched((nightly_backup, "is_synced", lambda path, env=None: False)):     # (here the checkout is in OneDrive too)
            code, said, pack, _s = run_archive(root, src, out=inside)
        assert code == 2 and "public repository" in "\n".join(said) and src.downloads == [] and not inside.exists(), said

        said = []
        tiny = lambda path: type("U", (), {"total": 10, "used": 9, "free": 1})()      # noqa: E731
        with with_secrets(root), patched((yp.shutil, "disk_usage", tiny), (yp, "pack", FakePack(root / "kept"))):
            assert yp.archive(True, None, src, said.append, now=NOW, sleep=nosleep) == 2
        assert "not enough free space" in "\n".join(said) and src.downloads == []
        assert not (root / "store").exists() and drill_rows(root) == []


# ── --apply: the archive ─────────────────────────────────────────────────────

@test("the archive: every object downloaded, hashed and compared; manifest; packed; work folder gone; counts in drills.jsonl")
def _():
    with data_root() as root:
        objects = store_objects()
        code, said, pack, src = run_archive(root, FakeSource(objects))
        text = "\n".join(said)
        assert code == 0, said
        assert "PHOTO ARCHIVE PASSED" in text and "1 match the sha256 in the database, 4 have none to compare, 0 differ" in text, text
        final = photos_folder(root) / "2026-10-15_0930_photos.7z"
        assert final.is_file() and final.read_bytes() == b"stand-in archive"
        assert sorted(p.name for p in photos_folder(root).iterdir()) == [final.name], "no work folder, no staging file left"
        folder, staging, password, exe = pack.calls[0]
        assert folder == photos_folder(root) / "work_20261015_093000" and staging.parent == folder.parent
        assert staging.name == "work_20261015_093000.7z" and password == PASSWORD and exe == "7z.exe"
        assert not folder.exists(), "the work folder is deleted"
        assert sorted(src.downloads) == sorted(objects), "every object, once"
        kept = pack.keep
        manifest = json.loads((kept / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["kind"] == "yq-photos" and manifest["version"] == 1 and manifest["problems"] == []
        assert manifest["counts"]["objects"] == 5 and manifest["counts"]["checked"] == 1
        assert manifest["counts"]["no_checksum"] == 4 and manifest["counts"]["orphans"] == 1
        by_key = {(e["bucket"], e["path"]): e for e in manifest["files"]}
        assert set(by_key) == set(objects)
        for key, data in objects.items():
            e = by_key[key]
            assert set(e) == {"bucket", "path", "file", "size", "sha256", "db_sha256", "match", "refs"}, e
            assert e["sha256"] == sha(data) and e["size"] == len(data)
            assert (kept / e["file"]).read_bytes() == data, "the file in the archive is the object, byte for byte"
        assert by_key[MASTER]["match"] is True and by_key[MASTER]["db_sha256"] == sha(objects[MASTER])
        assert by_key[MASTER]["refs"] == ["market_photos.path#1"]
        assert by_key[THUMB]["match"] is None and by_key[THUMB]["db_sha256"] is None
        assert by_key[ORPHAN]["match"] is None and by_key[ORPHAN]["refs"] == []
        rows = drill_rows(root)
        assert len(rows) == 1
        row = rows[0]
        assert row["kind"] == "photos" and row["result"] == "passed" and row["archive"] == "2026-10-15_0930_photos.7z"
        assert (row["objects"], row["checked"], row["no_checksum"], row["mismatches"], row["missing"], row["errors"],
                row["orphans"]) == (5, 1, 4, 0, 0, 0, 1), row
        assert isinstance(row["seconds"], float) and row["password_from"] == "process_environment"
        blob = text + json.dumps(row) + json.dumps(manifest)
        assert PASSWORD not in blob and "ci-key" not in blob and "ci.invalid" not in blob, "no secret anywhere"
        for key in objects:
            assert key[1] not in json.dumps(row), "the log row holds counts, never a path"


@test("--out puts the archive and the work folder in that folder; every run makes its own archive and replaces none")
def _():
    with data_root() as root:
        out = root / "elsewhere" / "photo-copies"
        code, said, pack, _src = run_archive(root, out=out)
        assert code == 0 and (out / "2026-10-15_0930_photos.7z").is_file() and pack.calls[0][0].parent == out.resolve()
        assert not (root / "store").exists()
        (out / "2026-10-15_0930_photos.7z").write_bytes(b"first")
        code, said, pack, _src = run_archive(root, out=out, now=LATER)               # a quarter of an hour later
        assert code == 0 and (out / "2026-10-15_0945_photos.7z").read_bytes() == b"stand-in archive"
        for number in (2, 3):                                                          # the very same minute, twice more
            code, said, pack, _src = run_archive(root, out=out, now=NOW)
            assert code == 0 and (out / f"2026-10-15_0930_photos_{number}.7z").read_bytes() == b"stand-in archive", said
        assert (out / "2026-10-15_0930_photos.7z").read_bytes() == b"first", "an earlier archive is never replaced"
        assert sorted(p.name for p in out.iterdir()) == ["2026-10-15_0930_photos.7z", "2026-10-15_0930_photos_2.7z",
                                                          "2026-10-15_0930_photos_3.7z", "2026-10-15_0945_photos.7z"]


@test("a missing object, a row without an object, a sha256 that differs, a download that fails and a short download are reported")
def _():
    with data_root() as root:
        objects = store_objects()
        refs = store_refs(objects)
        refs[0]["sha256"] = "0" * 64                                         # the database says another hash
        refs.append(ref(("finds", "market/2026/10/ee55.png"), "market_photos.path", 9, "a" * 64))     # no such object
        gone = ("finds", "market/2026/10/ff66.png")
        objects[gone] = b"listed but gone"
        refs.append(ref(gone, "market_photos.path", 10))
        src = FakeSource(objects, refs, missing={gone}, failing={LEGACY_FIND: 99}, listed={NOTE: 10_000})
        code, said, pack, _s = run_archive(root, src)
        text = "\n".join(said)
        assert code == 1 and "PHOTO ARCHIVE PASSED" not in text, text
        assert "PROBLEMS (5)" in text and "2 missing, 1 differ, 2 not downloaded" in text, text
        assert re.search(r"mismatch\s+finds:market/2026/10/aa11\.png \(market_photos\.path row 1\)", text), text
        assert re.search(r"missing\s+finds:market/2026/10/ee55\.png \(market_photos\.path row 9\)", text), text
        assert re.search(r"missing\s+finds:market/2026/10/ff66\.png.*cannot serve", text), text
        assert re.search(r"error\s+finds:finds/bb22\.png.*could not be downloaded \(storage said 500: boom\)", text), text
        assert re.search(r"error\s+field-notes:notes/cc33\.jpg.*storage lists 10000", text), text
        assert src.downloads.count(LEGACY_FIND) == yp.RETRIES, "retried, then given up"
        assert src.downloads.count(gone) == 1, "a missing object is not retried"
        final = photos_folder(root) / "2026-10-15_0930_photos.7z"
        assert final.is_file(), "the archive is made all the same: everything that could be read is in it"
        manifest = json.loads((pack.keep / "manifest.json").read_text(encoding="utf-8"))
        kinds = sorted(p["type"] for p in manifest["problems"])
        assert kinds == ["error", "error", "mismatch", "missing", "missing"], kinds
        files = {(e["bucket"], e["path"]): e for e in manifest["files"]}
        assert files[MASTER]["match"] is False and MASTER in files, "a photo that differs is still kept, as found"
        assert LEGACY_FIND not in files and gone not in files
        row = drill_rows(root)[0]
        assert row["result"] == "problems" and (row["mismatches"], row["missing"], row["errors"]) == (1, 2, 2), row
        assert row["objects"] == len(files) == 4, "six objects were listed; two could not be read"


@test("a download that fails twice and then works is retried and not reported")
def _():
    with data_root() as root:
        src = FakeSource(failing={MASTER: 2, NOTE: 1})
        code, said, pack, _s = run_archive(root, src)
        assert code == 0, said
        assert src.downloads.count(MASTER) == 3 and src.downloads.count(NOTE) == 2
        assert json.loads((pack.keep / "manifest.json").read_text(encoding="utf-8"))["problems"] == []


@test("a pack that fails leaves no work folder or staging file, spares an earlier archive, and is recorded as failed")
def _():
    with data_root() as root:
        old = photos_folder(root) / "2026-10-15_0930_photos.7z"                       # made in this very minute
        old.parent.mkdir(parents=True)
        old.write_bytes(b"yesterday's good archive")
        quoting = f"7-Zip could not pack the photos (exit 2) while running 7z a -p{PASSWORD}"      # a tool that quotes its command line
        code, said, pack, _s = run_archive(root, pack=FakePack(root / "kept", fail=quoting))
        text = "\n".join(said)
        assert code == 2 and "PHOTO ARCHIVE FAILED" in text and "No archive was made" in text, text
        assert old.read_bytes() == b"yesterday's good archive"
        assert [p.name for p in photos_folder(root).iterdir()] == [old.name]
        row = drill_rows(root)[0]
        assert row["result"] == "failed" and "could not pack" in row["why"] and row["archive"] is None
        assert PASSWORD not in json.dumps(row) and PASSWORD not in text, "the password is masked even in an error text"


def many(n: int) -> dict:
    """n invented photos (all the same kind of key)."""
    return {("finds", f"p/{i:02d}.png"): png((i, i, i)) for i in range(n)}


@test("every download fails (key rotated, network down): the run stops after a few objects, exit 2, nothing packed or replaced")
def _():
    with data_root() as root:
        objects = many(9)
        store = photos_folder(root)
        store.mkdir(parents=True)
        this_minute, yesterday = store / "2026-10-15_0930_photos.7z", store / "2026-10-14_0900_photos.7z"
        this_minute.write_bytes(b"a good archive made in this very minute")
        yesterday.write_bytes(b"yesterday's")
        src = FakeSource(objects, refs=[], failing={k: 99 for k in objects})
        code, said, pack, _s = run_archive(root, src)
        text = "\n".join(said)
        assert code == 2, text
        assert "PHOTO ARCHIVE FAILED" in text and f"{yp.ABORT_AFTER} downloads in a row failed" in text, text
        assert "No archive was made" in text and "the archive was made" not in text and "PROBLEMS" not in text, text
        assert len(src.downloads) == yp.ABORT_AFTER * yp.RETRIES, "five objects, each tried three times, then it stops"
        assert pack.calls == [], "nothing was packed"
        assert this_minute.read_bytes() == b"a good archive made in this very minute" and yesterday.read_bytes() == b"yesterday's"
        assert sorted(p.name for p in store.iterdir()) == [yesterday.name, this_minute.name], "no work folder, no new archive"
        row = drill_rows(root)[0]
        assert row["result"] == "failed" and row["archive"] is None and row["objects"] == 0, row


@test("storage refuses the key (401 / 403): the run stops at the first object, without retries, exit 2")
def _():
    class Refusing(FakeSource):
        def download(self, bucket, path):
            self.downloads.append((bucket, path))
            raise yp.SourceRefused("storage refused the key (HTTP 401)")
    with data_root() as root:
        src = Refusing()
        code, said, pack, _s = run_archive(root, src)
        text = "\n".join(said)
        assert code == 2 and "PHOTO ARCHIVE FAILED: storage refused the key (HTTP 401)" in text, text
        assert "No archive was made" in text and "the archive was made" not in text, text
        assert len(src.downloads) == 1 and pack.calls == [] and not list(photos_folder(root).glob("*.7z"))
        assert drill_rows(root)[0]["result"] == "failed"


@test("a run with download errors never replaces an archive: it gets a name of its own")
def _():
    with data_root() as root:
        store = photos_folder(root)
        store.mkdir(parents=True)
        first = store / "2026-10-15_0930_photos.7z"
        first.write_bytes(b"a good archive made in this very minute")
        src = FakeSource(failing={MASTER: 99, NOTE: 99})                         # two of the five cannot be read
        code, said, pack, _s = run_archive(root, src)
        text = "\n".join(said)
        assert code == 1 and "PROBLEMS (2)" in text, text
        assert first.read_bytes() == b"a good archive made in this very minute", "the earlier archive is untouched"
        assert sorted(p.name for p in store.iterdir()) == [first.name, "2026-10-15_0930_photos_2.7z"]
        assert "2026-10-15_0930_photos_2.7z" in text and drill_rows(root)[0]["archive"] == "2026-10-15_0930_photos_2.7z"


@test("objects are listed but not one can be downloaded: nothing is archived (exit 2), even below the stop limit")
def _():
    with data_root() as root:
        objects = many(3)
        assert len(objects) < yp.ABORT_AFTER
        for src in (FakeSource(objects, refs=[], failing={k: 99 for k in objects}),      # every download fails
                    FakeSource(objects, refs=[], missing=set(objects))):                  # listed, but all gone
            code, said, pack, _s = run_archive(root, src)
            assert code == 2 and "none of the 3 listed object(s) could be downloaded" in "\n".join(said), said
            assert "No archive was made" in "\n".join(said) and pack.calls == []
        assert not list(photos_folder(root).glob("*.7z"))
        assert [r["result"] for r in drill_rows(root)] == ["failed", "failed"]


@test("failures that are not in a row do not stop the run: a photo that downloads, or a missing object, resets the count")
def _():
    n = yp.ABORT_AFTER - 1                                                         # one less than the stop limit
    objects = many(2 * n + 2)
    keys = sorted(objects)
    with data_root() as root:
        failing = {k: 99 for k in keys[:n] + keys[n + 1:2 * n + 1]}                # n failures, a good photo, n failures, a good photo
        code, said, pack, src = run_archive(root, FakeSource(objects, refs=[], failing=failing))
        assert code == 1 and f"PROBLEMS ({2 * n})" in "\n".join(said), said
        assert len(src.downloads) == 2 * n * yp.RETRIES + 2 and pack.calls, "every object was tried"
        failing = {k: 99 for k in keys[:n] + keys[n + 1:2 * n + 1]}                # the middle one is listed but gone instead
        code, said, pack, src = run_archive(root, FakeSource(objects, refs=[], failing=failing, missing={keys[n]}), now=LATER)
        assert code == 1 and f"PROBLEMS ({2 * n + 1})" in "\n".join(said), said
        assert "downloads in a row" not in "\n".join(said), "2n failures with a missing object between are not a run of them"


@test("keys market/x and market/x/y.png together no longer crash the run: both are archived, the clashing one under a hashed name")
def _():
    with data_root() as root:
        objects = {("finds", "market/x"): png((1, 1, 1)), ("finds", "market/x/y.png"): png((2, 2, 2)),
                   ("finds", "Market/X/z.png"): png((3, 3, 3))}
        code, said, pack, _s = run_archive(root, FakeSource(objects, refs=[]))
        assert code == 0, said
        manifest = json.loads((pack.keep / "manifest.json").read_text(encoding="utf-8"))
        names = {e["path"]: e["file"] for e in manifest["files"]}
        assert set(names) == {key[1] for key in objects}, "the original keys stay in the manifest"
        assert len({n.lower() for n in names.values()}) == 3 and sum(n.startswith("files/_odd/") for n in names.values()) >= 1
        for (bucket, path), data in objects.items():
            assert (pack.keep / names[path]).read_bytes() == data


@test("a listed object with an unsafe name is an error (exit 1): never requested, and not counted as a failed download")
def _():
    class Picky(FakeSource):
        def download(self, bucket, path):
            if path == LEGACY_FIND[1]:
                self.downloads.append((bucket, path))
                raise yp.UnsafeKey("has a surprise")                          # the source's own check says no
            return super().download(bucket, path)
    unsafe = ["../../functions/v1/x", "a.png?x", "a/../b.png", "x%2Fy.png", "a\\b.png", "/abs.png"]
    assert len(unsafe) > yp.ABORT_AFTER, "more of them than the stop limit: they are no failed downloads"
    with data_root() as root:
        objects = store_objects()
        for key in unsafe:
            objects[("finds", key)] = b"must never be fetched"
        src = Picky(objects, refs=[])
        code, said, pack, _s = run_archive(root, src)
        text = "\n".join(said)
        assert code == 1, text
        assert not [d for d in src.downloads if d[1] in unsafe], "not one request for them"
        assert src.downloads.count(LEGACY_FIND) == 1, "an unsafe name is not retried"
        assert text.count("object name refused") == len(unsafe) + 1 and f"PROBLEMS ({len(unsafe) + 1})" in text, text
        assert "refused: has a surprise" in text and "starts with '/'" in text and "'..'" in text, text
        manifest = json.loads((pack.keep / "manifest.json").read_text(encoding="utf-8"))
        assert len(manifest["files"]) == 4 and sorted({p["type"] for p in manifest["problems"]}) == ["error"]
        assert drill_rows(root)[0]["errors"] == len(unsafe) + 1


@test("the password is masked before an error text is squeezed and cut, so no piece of it survives (archive, verify, _clean)")
def _():
    secret = "zq7  two spaces\nand-k9x\\tail"                      # a run of spaces, a newline and a backslash in it
    pieces = ("zq7", "two spaces", "and-k9x", "k9x", "tail", "-pzq7")
    filler = "x" * 130
    texts = [f"7-Zip failed: {filler} -p{secret} and then some more words",               # the 160-character cut falls inside it
             f"{filler[:60]} Command {['7z', 'a', '-p' + secret]!r} failed {filler}",       # as a list shows it: backslash doubled
             f"-p{secret}"]
    squeezed_then_cut = " ".join(texts[0].split())[:160]
    assert "-pzq7" in squeezed_then_cut, "the first text really is one the old order of work (squeeze, cut, then mask) leaked from"
    for text in texts:
        out = yp._clean(yp.PhotoError(text), secret)
        assert not any(p in out for p in pieces), out
        assert len(out) <= 160, out
    assert all("<password>" in yp._clean(yp.PhotoError(t), secret) for t in texts[1:]), "masked, not just cut away"
    assert yp._clean(ValueError("plain failure"), None) == "ValueError: plain failure"
    with data_root() as root:                                                            # the whole path of a failed pack
        code, said, pack, _s = run_archive(root, password=secret, pack=FakePack(root / "kept", fail=texts[0]))
        blob = "\n".join(said) + json.dumps(drill_rows(root))
        assert code == 2 and "PHOTO ARCHIVE FAILED" in blob and not any(p in blob for p in pieces), blob
        archive = root / "x_photos.7z"                                                   # and verify's failure messages
        archive.write_bytes(b"x")

        def unpack_fails(a, dest, pw, exe):
            raise OSError(texts[0])
        said = []
        with with_secrets(root, secret), patched((yb, "read_back", lambda a, pw, exe: (True, "Files: 1")), (yb, "unpack", unpack_fails)):
            assert yp.verify(archive, 5, said.append, now=LATER) == 1
        assert "VERIFY FAILED" in "\n".join(said) and not any(p in "\n".join(said) for p in pieces), said


@test("a name the file system cannot hold goes under a hashed name inside the work folder; a name that climbs out is refused")
def _():
    with data_root() as root:
        evil = {("finds", "../../escape.png"): png((1, 2, 3)), ("finds", "ok/fine.png"): png((4, 5, 6)),
                ("finds", "Case.png"): png((7, 8, 9)), ("finds", "case.png"): png((10, 11, 12)),
                ("finds", "con.png"): png((13, 14, 15)), ("finds", "a b.png"): png((16, 17, 18))}
        src = FakeSource(evil, refs=[])
        code, said, pack, _s = run_archive(root, src)
        text = "\n".join(said)
        assert code == 1 and "object name refused" in text and "../../escape.png" in text, text         # listed as an error
        assert ("finds", "../../escape.png") not in src.downloads, "never requested"
        manifest = json.loads((pack.keep / "manifest.json").read_text(encoding="utf-8"))
        names = {e["path"]: e["file"] for e in manifest["files"]}
        assert "../../escape.png" not in names and names["ok/fine.png"] == "files/finds/ok/fine.png"
        assert names["con.png"].startswith("files/_odd/") and names["a b.png"].startswith("files/_odd/")
        assert len(set(n.lower() for n in names.values())) == 5, "no two files with the same name on Windows"
        written = {p.relative_to(pack.keep).as_posix() for p in pack.keep.rglob("*") if p.is_file()}
        assert written == set(names.values()) | {"manifest.json"}
        outside = [p for p in (root / "store").rglob("*") if p.is_file()]
        assert [p.name for p in outside] == ["2026-10-15_0930_photos.7z"], "nothing written outside the work folder"
        assert not (root / "store" / "escape.png").exists() and not (root / "escape.png").exists()


@test("an empty store still makes a valid archive with a manifest, and rows with no object are all reported")
def _():
    with data_root() as root:
        refs = [ref(MASTER, "market_photos.path", 1, "b" * 64)]
        code, said, pack, _s = run_archive(root, FakeSource({}, refs))
        assert code == 1 and "missing" in "\n".join(said)
        manifest = json.loads((pack.keep / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["files"] == [] and manifest["counts"]["objects"] == 0 and len(manifest["problems"]) == 1


@test("more problems than the screen shows: the rest is said to be in the manifest")
def _():
    with data_root() as root:
        objects = {("finds", f"p/{i}.png"): png((i, i, i)) for i in range(yp.SHOWN + 5)}
        refs = [ref(k, "market_photos.path", n, "c" * 64) for n, k in enumerate(objects)]     # every hash differs
        code, said, pack, _s = run_archive(root, FakeSource(objects, refs))
        text = "\n".join(said)
        assert code == 1 and f"PROBLEMS ({yp.SHOWN + 5})" in text and "and 5 more" in text and "manifest.json" in text
        assert len(json.loads((pack.keep / "manifest.json").read_text(encoding="utf-8"))["problems"]) == yp.SHOWN + 5


# ── the packing ──────────────────────────────────────────────────────────────

@test("pack uses the shared 7-Zip settings (AES-256, names hidden), reads the archive back and counts its files")
def _():
    from scripts import nightly_backup
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        folder = tmp / "payload"
        (folder / "files").mkdir(parents=True)
        (folder / "files" / "a.png").write_bytes(b"1")
        (folder / "manifest.json").write_text("{}", encoding="utf-8")
        archive = tmp / "x.7z"
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            archive.write_bytes(b"7z")
            return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        with patched((yp.subprocess, "run", run), (yb, "read_back", lambda a, pw, exe: (True, "Files: 2"))):
            assert yp.pack(folder, archive, PASSWORD, "7z.exe") == "Files: 2"
        assert calls == [nightly_backup.seven_zip_cmd("7z.exe", archive, folder, PASSWORD)]
        assert "-mhe=on" in calls[0] and "-t7z" in calls[0] and f"-p{PASSWORD}" in calls[0]
        for bad_read, why in (((True, "Files: 3"), "all 2 files"), ((False, "Files: 2"), "all 2 files"), ((True, ""), "all 2 files")):
            with patched((yp.subprocess, "run", run), (yb, "read_back", lambda a, pw, exe, r=bad_read: r)):
                try:
                    yp.pack(folder, archive, PASSWORD, "7z.exe")
                except yp.PhotoError as exc:
                    assert why in str(exc) and PASSWORD not in str(exc)
                else:
                    raise AssertionError("a pack that does not read back with its files was accepted")

        def fail(argv, **kw):
            return type("R", (), {"returncode": 2, "stdout": "", "stderr": f"error with {argv[0]}"})()
        archive.unlink()
        with patched((yp.subprocess, "run", fail)):
            try:
                yp.pack(folder, archive, PASSWORD, "7z.exe")
            except yp.PhotoError as exc:
                assert "exit 2" in str(exc) and PASSWORD not in str(exc)
            else:
                raise AssertionError("a failed 7-Zip was accepted")


@test("with 7-Zip: the archive is encrypted, reads back whole, verifies, and a wrong password cannot open it (SKIP without 7-Zip)")
def _():
    exe = yb.find_7z()
    if not exe:
        print("      SKIP: 7-Zip is not installed here")
        return
    with data_root() as root:
        objects = store_objects()
        said: list[str] = []
        with with_secrets(root, exe=exe):
            assert yp.archive(True, None, FakeSource(objects), said.append, now=NOW, sleep=nosleep) == 0, said
            final = photos_folder(root) / "2026-10-15_0930_photos.7z"
            assert final.is_file() and sorted(p.name for p in photos_folder(root).iterdir()) == [final.name]
            ok, info = yb.read_back(final, PASSWORD, exe)
            assert ok and "Files: 6" in info, info                         # five photos and manifest.json
            assert yb.read_back(final, "a wrong password", exe)[0] is False, "the names are hidden too"
            said = []
            assert yp.verify(final, 5, said.append, rng=random.Random(1), now=LATER) == 0, said
            text = "\n".join(said)
            assert "Checked 5 file(s) against the manifest: 5 match" in text and "Opened 5 of 5 sampled" in text, text
            assert "VERIFY PASSED" in text and PASSWORD not in text
            assert not list(photos_folder(root).glob("verify_*")), "the unpacked copy is deleted"
            said = []
            with patched((yq_secrets, "backup_password", lambda env=None: ("a wrong password", "x"))):
                assert yp.verify(final, 5, said.append, now=LATER) == 1
            assert "does not read back whole" in "\n".join(said) and not list(photos_folder(root).glob("verify_*"))


# ── verify ───────────────────────────────────────────────────────────────────

def payload(root: Path) -> Path:
    """A real payload folder (files/ and manifest.json) made by archive() on the invented store."""
    code, _said, pack, _src = run_archive(root)
    assert code == 0
    folder = root / "payload"
    shutil.copytree(pack.keep, folder)
    return folder


def run_verify(root: Path, folder: Path, sample=5, password=PASSWORD, stuck=False):
    """verify() on a prepared folder: 7-Zip's read-back and unpacking are stand-ins that hand the folder over.
    With stuck=True the unpacked copy cannot be deleted (rmtree does nothing) and is left for the test to look at."""
    archive = root / "some_photos.7z"
    archive.write_bytes(b"stand-in")
    said: list[str] = []
    n = sum(1 for p in folder.rglob("*") if p.is_file())
    stubs = [(yb, "read_back", lambda a, pw, exe: (True, f"Files: {n}")),
             (yb, "unpack", lambda a, dest, pw, exe: shutil.copytree(folder, dest, dirs_exist_ok=True))]
    if stuck:
        stubs.append((yp.shutil, "rmtree", lambda *args, **kwargs: None))
    with with_secrets(root, password), patched(*stubs):
        code = yp.verify(archive, sample, said.append, rng=random.Random(3), now=LATER)
    if not stuck:
        assert not list(photos_folder(root).glob("verify_*")), "the unpacked copy is always deleted"
    return code, "\n".join(said)


@test("verify: a whole archive passes; every file is checked and N photos are opened")
def _():
    with data_root() as root:
        folder = payload(root)
        code, text = run_verify(root, folder, sample=3)
        assert code == 0, text
        assert "Checked 5 file(s) against the manifest: 5 match (sha256 and size), 0 differ, 0 missing, 0 not in the manifest" in text
        assert "Opened 3 of 3 sampled photo(s) with Pillow (5 photos in the archive)" in text and "VERIFY PASSED" in text
        code, text = run_verify(root, folder, sample=0)
        assert code == 0 and "Opened 0 of 0 sampled" in text
        code, text = run_verify(root, folder, sample=99)
        assert code == 0 and "Opened 5 of 5 sampled" in text


@test("verify finds a changed, a missing, an extra, an unsafe and a corrupt file, and a manifest that is not there")
def _():
    with data_root() as root:
        folder = payload(root)
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        first = folder / manifest["files"][0]["file"]
        original = first.read_bytes()

        first.write_bytes(original + b"x")                                  # changed: longer
        code, text = run_verify(root, folder)
        assert code == 1 and "1 differ" in text and "VERIFY FAILED" in text, text
        flipped = bytearray(original)                                        # changed: one byte, same length
        flipped[-5] ^= 0x01
        first.write_bytes(bytes(flipped))
        code, text = run_verify(root, folder, sample=0)                      # no photo opened: only the hash can tell
        assert code == 1 and "1 differ" in text, text
        first.write_bytes(original)

        first.unlink()                                                       # missing
        code, text = run_verify(root, folder)
        assert code == 1 and "1 missing" in text, text
        first.write_bytes(original)

        (folder / "files" / "stray.png").write_bytes(b"x")                  # extra
        code, text = run_verify(root, folder)
        assert code == 1 and "1 not in the manifest" in text, text
        (folder / "files" / "stray.png").unlink()

        bad = json.loads(json.dumps(manifest))                              # a name that climbs out
        bad["files"][0]["file"] = "../outside.png"
        (folder / "manifest.json").write_text(json.dumps(bad), encoding="utf-8")
        code, text = run_verify(root, folder)
        assert code == 1 and "1 with an unsafe name" in text, text

        junk = b"this is not a picture"                                      # sha256 and size right, Pillow cannot open it
        broken = json.loads(json.dumps(manifest))
        broken["files"][0].update(sha256=sha(junk), size=len(junk))
        first.write_bytes(junk)
        (folder / "manifest.json").write_text(json.dumps(broken), encoding="utf-8")
        code, text = run_verify(root, folder, sample=99)
        assert code == 1 and "1 cannot be opened" in text and "0 differ" in text, text
        code, text = run_verify(root, folder, sample=0)
        assert code == 0, "with no photo opened the bytes are all right"

        (folder / "manifest.json").unlink()
        code, text = run_verify(root, folder)
        assert code == 1 and "no manifest.json" in text, text


@test("verify tells the problems the archive was made with, and finds the manifest one folder down")
def _():
    with data_root() as root:
        objects = store_objects()
        refs = store_refs(objects)
        refs[0]["sha256"] = "0" * 64
        code, _said, pack, _s = run_archive(root, FakeSource(objects, refs))
        assert code == 1
        wrapped = root / "wrapped"
        shutil.copytree(pack.keep, wrapped / "inner")
        code, text = run_verify(root, wrapped)
        assert code == 0 and "records 1 problem(s)" in text, text


@test("verify fails an archive whose manifest records download errors (photos missing from it), and only notes the other problems")
def _():
    with data_root() as root:
        code, _said, pack, _s = run_archive(root, FakeSource(failing={LEGACY_FIND: 99}))          # one photo could not be read
        assert code == 1
        folder = root / "with-errors"
        shutil.copytree(pack.keep, folder)
        code, text = run_verify(root, folder)
        assert code == 1 and "records 1 download error(s)" in text and "VERIFY FAILED" in text and "VERIFY PASSED" not in text, text
        assert "Checked 4 file(s) against the manifest: 4 match" in text, "what is in the archive is whole all the same"
        # the same archive with its error problem taken out of the manifest passes: only the errors decide
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        manifest["problems"] = [dict(p, type="missing") for p in manifest["problems"]]
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        code, text = run_verify(root, folder)
        assert code == 0 and "records 1 problem(s)" in text and "download error" not in text, text


@test("verify says so when the unpacked copy could not be deleted, also when it stops early")
def _():
    with data_root() as root:
        folder = payload(root)

        def clear():
            for left in photos_folder(root).glob("verify_*"):
                shutil.rmtree(left)
        code, text = run_verify(root, folder, stuck=True)                         # all is well, but the copy sticks
        assert code == 0 and "could not be deleted" in text and "UNENCRYPTED" in text, text
        assert len(list(photos_folder(root).glob("verify_*"))) == 1
        clear()
        (folder / "manifest.json").unlink()                                        # an early stop: there is no manifest
        code, text = run_verify(root, folder, stuck=True)
        assert code == 1 and "no manifest.json" in text and "could not be deleted" in text and "UNENCRYPTED" in text, text
        clear()
        (folder / "manifest.json").write_text("{ this is not json", encoding="utf-8")        # another early stop
        code, text = run_verify(root, folder, stuck=True)
        assert code == 1 and "VERIFY FAILED" in text and "could not be deleted" in text, text
        assert text.count("could not be deleted") == 1, "said once"
        clear()
        code, text = run_verify(root, folder)                                     # and when it can be deleted, nothing is said
        assert "could not be deleted" not in text


@test("verify needs the password and 7-Zip, an archive that exists, and an unpacking folder that is not synced")
def _():
    with data_root() as root:
        said: list[str] = []
        with with_secrets(root):
            assert yp.verify(root / "nothing.7z", 5, said.append) == 2 and "there is no archive" in said[-1]
        archive = root / "a.7z"
        archive.write_bytes(b"x")
        for password, exe, words in ((None, "7z.exe", "no backup password"), (PASSWORD, None, "7-Zip is not installed")):
            said = []
            with with_secrets(root, password, exe):
                assert yp.verify(archive, 5, said.append) == 2 and words in said[-1], said

        def synced():
            raise SystemExit("REFUSING: the backup folder is inside a synced folder (OneDrive).")
        said = []
        with patched((yq_secrets, "backup_password", lambda env=None: (PASSWORD, "process_environment")),
                     (yb, "find_7z", lambda: "7z.exe"), (yb, "backup_root", synced)):
            assert yp.verify(archive, 5, said.append) == 2
        assert "REFUSING" in said[-1] and "Nothing was changed" in said[-1]


@test("verify of an archive that does not read back stops there")
def _():
    with data_root() as root:
        archive = root / "a.7z"
        archive.write_bytes(b"x")
        said: list[str] = []
        with with_secrets(root), patched((yb, "read_back", lambda a, pw, exe: (False, "7-Zip test failed (exit 2)"))):
            assert yp.verify(archive, 5, said.append, now=LATER) == 1
        assert "does not read back whole" in "\n".join(said) and not photos_folder(root).exists()


# ── the real source ──────────────────────────────────────────────────────────

FULL_SCHEMA = {
    "market_photos": {"id", "observation_id", "bucket", "path", "position", "thumb_path", "blurred_path", "sha256", "role"},
    "product_finds": {"id", "name", "image_path", "status"},
    "field_notes": {"id", "note", "image_path"},
}


class FakeSession:
    """The read-only database session: the real gate (db.check_sql) on every statement, and a schema that
    refuses a column it does not have, as Postgres would."""

    def __init__(self, schema=None, rows=None, objects=None):
        self.schema = {t: set(c) for t, c in (FULL_SCHEMA if schema is None else schema).items()}
        self.rows = rows or {}
        self.objects = objects or []
        self.log: list[str] = []
        self.closed = False

    def q(self, sql, params=None):
        db.check_sql(sql)
        self.log.append(sql)
        if "information_schema.columns" in sql:
            return [{"column_name": c} for c in sorted(self.schema.get(params[0], ()))]
        if "from storage.objects" in sql:
            return [dict(o) for o in self.objects if o["bucket_id"] in params[0]]
        m = re.match(r"select (.+?) from (\w+)", sql)
        cols, table = [c.strip() for c in m.group(1).split(",")], m.group(2)
        for c in cols:
            assert c in self.schema[table], f"column {c} does not exist in {table}"
        data = self.rows.get(table, [])
        if "image_path is not null" in sql:
            data = [r for r in data if r.get("image_path") is not None]
        return [{c: r.get(c) for c in cols} for r in data]

    def close(self):
        self.closed = True


SESSION_ROWS = {
    "market_photos": [
        {"id": 1, "bucket": "finds", "path": "market/2026/10/aa11.png", "thumb_path": "market/2026/10/aa11-t320.png",
         "blurred_path": None, "sha256": "ABC123 "},
        {"id": 2, "bucket": "finds", "path": "market/2026/10/bb22.png", "thumb_path": None, "blurred_path": "market/2026/10/bb22-blur.png",
         "sha256": None},
    ],
    "product_finds": [{"id": 7, "image_path": "finds/old1.png"}, {"id": 8, "image_path": None}, {"id": 9, "image_path": "  "}],
    "field_notes": [{"id": 3, "image_path": "notes/old2.jpg"}],
}
SESSION_OBJECTS = [
    {"bucket_id": "finds", "name": "market/2026/10/aa11.png", "size_bytes": "120"},
    {"bucket_id": "finds", "name": "market/.emptyFolderPlaceholder", "size_bytes": "0"},
    {"bucket_id": "finds", "name": "market/2026/", "size_bytes": None},
    {"bucket_id": "field-notes", "name": "notes/old2.jpg", "size_bytes": "oops"},
    {"bucket_id": "orders", "name": "doc.pdf", "size_bytes": "5"},
]


@test("the real source reads the rows and then the object list, with SELECTs only")
def _():
    sess = FakeSession(rows=SESSION_ROWS, objects=SESSION_OBJECTS)
    built = []
    src = yp.SupabaseSource(sess, lambda: built.append(1) or object())
    snap = src.read()
    assert built == [], "reading never builds the storage client"
    assert sess.closed is True, "the session is released as soon as it has read: the downloads take minutes"
    src.close()
    src.close()                                              # twice is safe
    assert sess.log[-1].startswith("select bucket_id, name") and "from storage.objects" in sess.log[-1]
    assert not any("storage.objects" in s for s in sess.log[:-1]), "the rows come first, then the object list"
    assert all(re.match(r"\s*select\b", s) for s in sess.log)
    refs = {(r["source"], r["id"]): r for r in snap["refs"]}
    assert set(refs) == {("market_photos.path", 1), ("market_photos.thumb_path", 1), ("market_photos.path", 2),
                         ("market_photos.blurred_path", 2), ("product_finds.image_path", 7), ("field_notes.image_path", 3)}
    assert refs[("market_photos.path", 1)]["sha256"] == "abc123", "lower-cased and trimmed"
    assert refs[("market_photos.thumb_path", 1)]["sha256"] is None, "only a master has a recorded hash"
    assert refs[("product_finds.image_path", 7)]["bucket"] == "finds" and refs[("field_notes.image_path", 3)]["bucket"] == "field-notes"
    assert snap["tables"] == {"market_photos": {"rows": 2, "refs": 4, "checksums": 1},
                              "product_finds": {"rows": 1, "refs": 1, "checksums": 0},
                              "field_notes": {"rows": 1, "refs": 1, "checksums": 0}}
    objs = {(o["bucket"], o["path"]): o["size"] for o in snap["objects"]}
    assert objs == {("finds", "market/2026/10/aa11.png"): 120, ("field-notes", "notes/old2.jpg"): None}, objs
    assert snap["notes"] == ["2 folder placeholder(s) in storage ignored (they are not photos)"]
    inv = yp.analyse(snap)
    assert inv["total_objects"] == 2 and inv["total_bytes"] == 120
    assert len(inv["missing_refs"]) == 4 and len(inv["orphans"]) == 0


@test("tables and columns that are not there yet are notes, not failures")
def _():
    older = {"market_photos": {"id", "bucket", "path"}, "product_finds": {"id", "image_path"}}      # before r8m; no field_notes
    sess = FakeSession(schema=older, rows=SESSION_ROWS, objects=[])
    snap = yp.SupabaseSource(sess, lambda: None).read()
    assert {r["source"] for r in snap["refs"]} == {"market_photos.path", "product_finds.image_path"}
    assert all(r["sha256"] is None for r in snap["refs"])
    text = " | ".join(snap["notes"])
    assert "no sha256 column yet" in text and "field_notes does not exist" in text, text
    none = FakeSession(schema={"unrelated": {"id"}}, objects=[])
    snap = yp.SupabaseSource(none, lambda: None).read()
    assert snap["refs"] == [] and snap["tables"] == {} and len([n for n in snap["notes"] if "does not exist" in n]) == 3
    broken = FakeSession(schema={"market_photos": {"name"}, "product_finds": {"id"}, "field_notes": {"id"}}, objects=[])
    notes = yp.SupabaseSource(broken, lambda: None).read()["notes"]
    assert any("no id / path column" in n for n in notes) and sum("no image_path column" in n for n in notes) == 2


@test("a download builds the client once, returns the bytes, and tells a missing object from a failure")
def _():
    class StorageApiError(Exception):                      # the SDK's error has these three attributes
        def __init__(self, message, code, status):
            super().__init__(f"{{'statusCode': {status}, 'error': {code}, 'message': {message}}}")
            self.message, self.code, self.status = message, code, status

    answers = {"a.png": b"bytes", "gone.png": StorageApiError("Object not found", "not_found", "404"),
               "nobucket.png": StorageApiError("Bucket not found", "not_found", "404"),
               "boom.png": StorageApiError("Internal error at https://x.invalid/secret-path", "internal", "500"),
               "net.png": ConnectionError("https://x.invalid/secret-path unreachable")}

    class Bucket:
        def download(self, path):
            a = answers[path]
            if isinstance(a, Exception):
                raise a
            return a

    class Client:
        class storage:
            @staticmethod
            def from_(bucket):
                return Bucket()
    made = []
    src = yp.SupabaseSource(FakeSession(), lambda: made.append(1) or Client())
    assert src.download("finds", "a.png") == b"bytes" and src.download("finds", "a.png") == b"bytes" and made == [1]
    for path, exc, words in (("gone.png", yp.ObjectMissing, "object not found"), ("nobucket.png", yp.DownloadFailed, "Bucket not found"),
                             ("boom.png", yp.DownloadFailed, "storage said 500"), ("net.png", yp.DownloadFailed, "ConnectionError")):
        try:
            src.download("finds", path)
        except exc as e:
            assert words in str(e), (path, str(e))
            assert "secret-path" not in str(e), "no address in a message"
        else:
            raise AssertionError(f"{path}: no error")
    src.close()
    assert src.session.closed is True


@test("through the real read-only client a download is a plain GET; 404 and 500 are told apart; nothing else is sent")
def _():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/gone.webp"):
            return httpx.Response(404, json={"statusCode": "404", "error": "not_found", "message": "Object not found"})
        if request.url.path.endswith("/boom.webp"):
            return httpx.Response(500, json={"statusCode": "500", "error": "internal", "message": "Internal error"})
        return httpx.Response(200, content=b"photo-bytes")
    db.uninstall()
    client = db.install(inner=httpx.MockTransport(handler), url="https://ci.invalid", key="ci-key")
    try:
        src = yp.SupabaseSource(FakeSession(), lambda: client)
        assert src.download("finds", "market/2026/10/ok.webp") == b"photo-bytes"
        try:
            src.download("finds", "market/2026/10/gone.webp")
        except yp.ObjectMissing:
            pass
        else:
            raise AssertionError("a 404 is a missing object")
        try:
            src.download("finds", "market/2026/10/boom.webp")
        except yp.DownloadFailed as exc:
            assert str(exc) == "storage said 500: Internal error", str(exc)
        else:
            raise AssertionError("a 500 is a failure")
        assert seen and all(s.startswith("GET /storage/v1/object/") for s in seen), seen
        assert "GET /storage/v1/object/finds/market/2026/10/ok.webp" in seen
        try:
            client.storage.from_("finds").list("market")
        except Exception:                                                    # noqa: BLE001  (the guard refuses a POST)
            pass
        else:
            raise AssertionError("the read-only client let a POST through")
        assert not [s for s in seen if not s.startswith("GET ")], seen
    finally:
        db.uninstall()


@test("the real source: 401 / 403 in any body, and a client that cannot be built, are refusals (never retried); 500 is a failure")
def _():
    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        if name == "gateway401.png":                  # the API gateway's body has no 'error' or 'statusCode': the SDK trips on it
            return httpx.Response(401, json={"message": "Invalid API key"})
        if name == "json403.png":
            return httpx.Response(403, json={"statusCode": "403", "error": "Unauthorized", "message": "bad key"})
        if name == "text401.png":
            return httpx.Response(401, text="unauthorized", headers={"content-type": "text/plain"})
        if name == "boom.png":
            return httpx.Response(500, json={"statusCode": "500", "error": "internal", "message": "Internal error"})
        return httpx.Response(200, content=b"ok")
    db.uninstall()
    client = db.install(inner=httpx.MockTransport(handler), url="https://ci.invalid", key="ci-key")
    try:
        src = yp.SupabaseSource(FakeSession(), lambda: client)
        for name, status in (("gateway401.png", "401"), ("json403.png", "403"), ("text401.png", "401")):
            try:
                yp.fetch(src, "finds", f"d/{name}", sleep=nosleep)
            except yp.SourceRefused as exc:
                assert f"HTTP {status}" in str(exc) and "ci-key" not in str(exc) and "ci.invalid" not in str(exc), str(exc)
            else:
                raise AssertionError(f"{name}: not a refusal")
        try:
            src.download("finds", "d/boom.png")
        except yp.DownloadFailed as exc:
            assert "storage said 500" in str(exc)
        else:
            raise AssertionError("a 500 is a failure, not a refusal")
        assert src.download("finds", "d/ok.png") == b"ok"
    finally:
        db.uninstall()
    built: list[int] = []

    def no_client():
        built.append(1)
        raise RuntimeError("SUPABASE_URL / SUPABASE_KEY are not set (postgresql://u:secret@db.invalid:5432/x)")
    src = yp.SupabaseSource(FakeSession(), no_client)
    try:
        yp.fetch(src, "finds", "d/ok.png", sleep=nosleep)
    except yp.SourceRefused as exc:
        assert "could not be built" in str(exc) and "secret" not in str(exc) and "db.invalid" not in str(exc), str(exc)
    else:
        raise AssertionError("a client that cannot be built is a refusal")
    assert built == [1], "asked once: not retried"


REFUSED_KEYS = [
    "", "/abs.png", "//host/x.png", "../../functions/v1/x", "a/../b.png", "a/./b.png", "./a.png", "..", ".", "a//b.png",
    "a/b/", "a.png?x", "a.png#frag", "a\\b.png", "a\tb.png", "a\nb.png", "a\x00b.png", "a\x7fb.png", "\x85x.png",
    "a%20b.png", "a%2Fb.png", "%2e%2e/x", "a/%2e%2e/b", "a%e2%82%ac.png", " lead.png", "a:b.png", "1a:b.png", "C:/x.png",
    "http://evil.example/x.png",
]
ORDINARY_KEYS = [
    "market/2026/10/0123456789abcdef0123456789abcdef.webp", "market/2026/10/0123456789abcdef-t320.webp",
    "market/2026/10/0123456789abcdef-blur.webp", "market/2026/10/imp-12-1.webp", "finds/0123456789abcdef.jpg", "notes/ab.jpeg",
    "a b.png", "caf\u00e9.png", "\u0627\u0644.png", "100%.png", "a%.png", "%zz.png", "a%2g", "a;b.png", "a+b.png", "a&b=c.png",
    "a@b.png", "a[1].png", "a{1}.png", "a|b.png", "a^b.png", "a`b.png", "a'b.png", 'a"b.png', "a<b>.png", "a*b.png",
    "a,b.png", "a!b.png", "a$b.png", "a(b).png", "a~b.png", "a=b.png", "-lead.png", ".hidden", "a..b.png", "..a",
    "a/..b/c.png", "a/b..", "a/...", "trail.png ", "x/a:b.png", "a b/c d.png", "A.PNG", "\u2028x.png", "\ufeffx.png",
]


@test("unsafe_key: a name the SDK would turn into another address is refused, with a reason; ordinary names and buckets are not")
def _():
    for key in REFUSED_KEYS:
        why = yp.unsafe_key("finds", key)
        assert why and isinstance(why, str), f"{key!r} was not refused"
    for key in ORDINARY_KEYS:
        assert yp.unsafe_key("finds", key) is None, f"{key!r} was refused: {yp.unsafe_key('finds', key)}"
    assert yp.unsafe_key("field-notes", "notes/ab.jpeg") is None
    for bucket in ("orders", "catalog", "finds/..", "", "FINDS"):
        assert yp.unsafe_key(bucket, "a.png"), f"bucket {bucket!r} was accepted"
    reasons = {key: yp.unsafe_key("finds", key) for key in ("/abs.png", "../../functions/v1/x", "a.png?x", "a.png#f", "a\\b.png",
                                                              "a\x00b.png", "a%2Fb.png")}
    assert "starts with '/'" in reasons["/abs.png"] and "'..'" in reasons["../../functions/v1/x"], reasons
    assert "'?'" in reasons["a.png?x"] and "'#'" in reasons["a.png#f"] and "backslash" in reasons["a\\b.png"], reasons
    assert "control character" in reasons["a\x00b.png"] and "escape" in reasons["a%2Fb.png"], reasons


@test("the rule matches the SDK: an allowed name is requested at exactly its own address, a refused name is never requested")
def _():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, content=b"x")
    extra = ["%", "%4", "%%", "a%2", "x_y:z.png", "a b:c.png", "\u00fc:b.png", "\U0001F600.png", "a\u0301.png", "dir/", "a..",
             "a/b:c.png", "tab\tx.png", "nl\nx.png", "del\x7fx.png", "nul\x00x.png", "%2e%2e", "a/%2E%2E/b", "a b/../c.png"]
    db.uninstall()
    client = db.install(inner=httpx.MockTransport(handler), url="https://ci.invalid", key="ci-key")
    try:
        src = yp.SupabaseSource(FakeSession(), lambda: client)
        for key in REFUSED_KEYS + ORDINARY_KEYS + extra:
            seen.clear()
            if yp.unsafe_key("finds", key):
                try:
                    src.download("finds", key)
                except yp.UnsafeKey:
                    pass
                else:
                    raise AssertionError(f"{key!r} was fetched")
                assert seen == [], (key, seen)
            else:
                assert src.download("finds", key) == b"x"
                assert seen == ["/storage/v1/object/finds/" + key], f"{key!r} went to {seen}"
        seen.clear()
        try:
            src.download("orders", "a.png")
        except yp.UnsafeKey:
            pass
        assert seen == [], "a bucket outside the two photo buckets is never asked"
    finally:
        db.uninstall()


@test("the whole production wiring: fake database rows, the real read-only client over an in-memory storage, the archive, verify")
def _():
    objects = store_objects()
    served = {f"/storage/v1/object/{b}/{p}": data for (b, p), data in objects.items()}
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        data = served.get(request.url.path)
        if data is None:
            return httpx.Response(404, json={"statusCode": "404", "error": "not_found", "message": "Object not found"})
        return httpx.Response(200, content=data)
    rows = {
        "market_photos": [{"id": 1, "bucket": MASTER[0], "path": MASTER[1], "thumb_path": THUMB[1], "blurred_path": None,
                           "sha256": sha(objects[MASTER]).upper()}],
        "product_finds": [{"id": 7, "image_path": LEGACY_FIND[1]}],
        "field_notes": [{"id": 3, "image_path": NOTE[1]}],
    }
    listing = [{"bucket_id": b, "name": p, "size_bytes": str(len(d))} for (b, p), d in sorted(objects.items())]
    exe = yb.find_7z()
    db.uninstall()
    client = db.install(inner=httpx.MockTransport(handler), url="https://ci.invalid", key="ci-key")
    try:
        with data_root() as root:
            session = FakeSession(rows=rows, objects=listing)
            said: list[str] = []
            pack = patched() if exe else patched((yp, "pack", FakePack(root / "kept")))
            with with_secrets(root, exe=exe or "7z.exe"), pack:
                code = yp.archive(True, None, yp.SupabaseSource(session, lambda: client), said.append, now=NOW, sleep=nosleep)
                text = "\n".join(said)
                assert code == 0, text
                assert session.closed is True and len(session.log) == 7, session.log      # 3 schema probes, 3 tables, the list
                assert sorted(seen) == sorted(f"GET {p}" for p in served), "one plain GET per object, nothing else"
                assert "1 match the sha256 in the database, 4 have none to compare, 0 differ" in text, text
                assert "ci-key" not in text and PASSWORD not in text and "ci.invalid" not in text
                assert drill_rows(root)[0]["objects"] == 5
                final = photos_folder(root) / "2026-10-15_0930_photos.7z"
                assert final.is_file() and sorted(p.name for p in photos_folder(root).iterdir()) == [final.name]
                if exe:
                    said = []
                    assert yp.verify(final, 5, said.append, rng=random.Random(2), now=LATER) == 0, said
                    assert "Checked 5 file(s) against the manifest: 5 match" in "\n".join(said)
                else:
                    print("      note: 7-Zip is not installed here; the packing was a stand-in and verify was not run")
    finally:
        db.uninstall()


@test("fetch retries a failure, not a missing object; the source of a dry run is never asked to download")
def _():
    src = FakeSource(failing={MASTER: 1})
    assert yp.fetch(src, *MASTER, sleep=nosleep) == src.objects[MASTER] and src.downloads == [MASTER, MASTER]
    src = FakeSource(missing={MASTER})
    try:
        yp.fetch(src, *MASTER, sleep=nosleep)
    except yp.ObjectMissing:
        assert src.downloads == [MASTER]
    else:
        raise AssertionError("a missing object was found")

    class Odd:
        calls = 0

        def download(self, bucket, path):
            Odd.calls += 1
            raise ValueError("not classified by the source")
    try:
        yp.fetch(Odd(), "finds", "x", tries=2, sleep=nosleep)
    except yp.DownloadFailed as exc:
        assert str(exc) == "ValueError" and Odd.calls == 2
    else:
        raise AssertionError("an unclassified error was swallowed")


# ── the module itself ────────────────────────────────────────────────────────

# every verb with which Storage (upload, update, remove, move, copy, signed links, buckets), PostgREST (insert, update,
# upsert, delete, rpc, table) or a database cursor can be written to, or a message sent
WRITE_VERBS = {"upload", "update", "remove", "move", "copy", "create_signed_url", "create_signed_urls", "create_signed_upload_url",
               "upload_to_signed_url", "create_bucket", "update_bucket", "empty_bucket", "delete_bucket", "insert", "upsert",
               "delete", "rpc", "table", "execute", "executemany", "commit", "list", "list_v2", "send", "sendmail", "post", "put",
               "patch"}
HARMLESS = {("row", "update"), ("h", "update"), ("sys.path", "insert")}      # a dict of counts, a running hash, the import path


def write_calls(source: str) -> list[str]:
    """Every call of one of those verbs in the source, except the three harmless ones above."""
    return sorted(ast.unparse(n.func) for n in ast.walk(ast.parse(source))
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in WRITE_VERBS
                  and (ast.unparse(n.func.value), n.func.attr) not in HARMLESS)


@test("the module can neither write to storage nor to the database, and sends nothing (update and every write verb are forbidden)")
def _():
    for snippet in ('client.storage.from_("b").upload("p", b"x")', 'client.storage.from_("b").update("p", b"x")',
                    'client.storage.from_("b").remove(["p"])', 'client.storage.from_("b").move("a", "b")',
                    'client.storage.from_("b").copy("a", "b")', 'client.storage.from_("b").create_signed_url("p", 5)',
                    'client.storage.from_("b").upload_to_signed_url("p", "t", b"x")', 'c.table("t").insert({}).execute()',
                    'c.table("t").update({}).eq("a", 1)', 'c.table("t").delete()', 'cur.execute("select 1")',
                    "something.update(x)", "other.insert(0, x)"):
        assert write_calls(snippet), f"the check does not see: {snippet}"
    for fine in ("row.update(a=1)", "h.update(block)", 'sys.path.insert(0, "x")', "used.add(1)", "ids.setdefault(a, [])",
                 'client.storage.from_("b").download("p")'):
        assert not write_calls(fine), f"the check refuses: {fine}"
    source = (ROOT / "scripts" / "yq_photos.py").read_text(encoding="utf-8")
    assert write_calls(source) == [], f"scripts/yq_photos.py calls {write_calls(source)}"
    tree = ast.parse(source)
    storage = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "storage"]
    assert len(storage) == 1, "storage is touched in one place only: SupabaseSource.download"
    imported = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imported |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            imported.add(n.module)
    assert not imported & {"requests", "smtplib", "socket", "urllib.request", "psycopg", "supabase", "httpx", "app.database"}, imported
    assert not any(m.startswith("app.") or m == "app" for m in imported), "the app's own modules (and their writers) are not used"
    text = (ROOT / "scripts" / "yq_photos.py").read_text(encoding="utf-8")
    for word in ("SUPABASE_KEY", "supabase_key", "service_role", "YQ_BACKUP_PASSWORD"):
        assert word not in text.replace("cmdkey /generic:YQ_BACKUP_PASSWORD", ""), f"the module names {word}"


@test("main(): the commands and their options reach the right function")
def _():
    seen = {}
    with patched((yp, "archive", lambda apply, out: seen.update(archive=(apply, out)) or 0),
                 (yp, "verify", lambda path, sample: seen.update(verify=(path, sample)) or 0)):
        assert yp.main(["archive"]) == 0 and seen["archive"] == (False, None)
        assert yp.main(["archive", "--apply", "--out", "somewhere"]) == 0 and seen["archive"] == (True, "somewhere")
        assert yp.main(["verify", "x_photos.7z"]) == 0 and seen["verify"] == (Path("x_photos.7z"), 5)
        assert yp.main(["verify", "x_photos.7z", "--sample", "2"]) == 0 and seen["verify"] == (Path("x_photos.7z"), 2)
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            yp.main([])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("a command is required")


@test("CI runs this suite and compiles the module")
def _():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    loop = next(line for line in ci.splitlines() if line.strip().startswith("for suite in"))
    assert re.search(r"\bphotos\b", loop), loop
    compile_line = next(line for line in ci.splitlines() if "compileall" in line and "scripts/ops" in line)
    assert "scripts/yq_photos.py" in compile_line, compile_line


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
        finally:
            paths.set_data_root(None)
            config.use(None)
    db.uninstall()
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
