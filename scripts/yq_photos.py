"""python -m scripts.yq_photos : an encrypted copy of the Market Intelligence photos, kept on this PC only.

    python -m scripts.yq_photos archive [--apply] [--out <dir>]
        The photos live in two PRIVATE Supabase Storage buckets ('finds': Market Intel masters, thumbnails,
        blurred copies and the old Product Finds board; 'field-notes': the old Field Notes board). Nothing
        else keeps a copy of them. Without --apply this only LOOKS (read only) and prints counts: the objects
        and their size in each bucket, the database rows that point at a photo, the rows whose object is gone
        and the objects no row points at. Nothing is downloaded, written or changed.
        With --apply: every object is downloaded, one at a time, into a work folder under the backup folder
        (%LOCALAPPDATA%\\yq-backups\\photos\\work_<stamp>), hashed (sha256) and compared with the sha256 the
        database recorded at capture, where it recorded one. manifest.json lists every object (bucket, path,
        size, sha256, db_sha256, match). The folder is packed with the backup password (7-Zip AES-256, file
        names hidden: the settings of scripts/nightly_backup.py) into <date>_<HHMM>_photos.7z (a run never
        replaces an earlier archive: the same minute again becomes _2, _3), the archive is read back (7-Zip's
        own test, file count compared) and the work folder is deleted. A row of business_data/ops/drills.jsonl
        records the run (counts only). Refused without the backup password or 7-Zip, in a folder OneDrive
        syncs, and when the drive is too full.
        Exit 0: all clean. 1: the archive was made but a photo is missing in storage, differs from the sha256
        in its database row or could not be downloaded, or its name was one the Storage client would turn into
        another address (never requested); they are listed. 2: nothing was archived, also when storage refuses
        the key or keeps failing (ABORT_AFTER downloads in a row), or when no photo at all could be downloaded.

    python -m scripts.yq_photos verify <archive> [--sample 5]
        The restore drill for an archive: it is unpacked into a work folder, every file is checked against its
        manifest sha256 and size, N random photos are opened with Pillow, and the work folder is deleted.

Only reads production: the rows and the object list come from a read-only database session
(scripts/ops/db.py), the photos from Storage through the same read-only client (GET only: the guard refuses
everything else). The Supabase key is never printed, logged or stored here. The backup password comes from
scripts/yq_secrets.py and is never printed (7-Zip gets it on its command line, as for every backup here).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import yq_backup as yb  # noqa: E402
from scripts import yq_secrets  # noqa: E402

KIND = "photos"
BUCKETS = ("finds", "field-notes")           # the two private photo buckets (app/market_intel.BUCKETS)
MANIFEST = "manifest.json"
FILES_DIR = "files"
PLACEHOLDER = ".emptyFolderPlaceholder"      # what Supabase leaves in an "empty folder": not a photo
RETRIES = 3                                  # tries of one object (a failure is retried, a missing object is not)
ABORT_AFTER = 5                              # objects in a row that fail all their tries: storage or the network is down
MARGIN = 64 * 1024 * 1024                    # free space wanted on top of two copies of the photos
IMAGE_EXTS = (".webp", ".jpg", ".jpeg", ".png", ".gif")
SHOWN = 50                                   # problems printed; all of them are in the manifest
MARKET = "market_photos"                     # master (path), thumbnail and blurred copy, in the bucket of the row
LEGACY = (("product_finds", "finds"), ("field_notes", "field-notes"))     # the old boards: image_path, one bucket each


class PhotoError(Exception):
    """The run cannot go on. The sentence is for the owner: no address, key or name in it."""


class ObjectMissing(Exception):
    """Storage has no such object."""


class DownloadFailed(Exception):
    """An object could not be read, even after the retries."""


class SourceRefused(PhotoError):
    """Storage will not serve this program at all (the key is refused with 401 / 403, or the client could not be
    built): trying the next object cannot help, the run stops."""


class UnsafeKey(Exception):
    """An object name the Storage client would turn into another address (or object) than its own: never requested."""


# ── small helpers (pure) ──────────────────────────────────────────────────────

def fmt_bytes(n) -> str:
    n = float(n or 0)
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{int(n)} bytes" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _norm_sha(v) -> str | None:
    s = str(v or "").strip().lower()
    return s or None


def _mask(text) -> str:
    """One line, any web or database address replaced: an error text never carries one to the screen or a log."""
    return re.sub(r"(?i)\b(?:postgres(?:ql)?|https?)://\S+", "<address>", " ".join(str(text).split()))


def _secret_forms(secret) -> list[str]:
    """The ways a secret can stand in an error text: as it is, as a list or a string shows it (backslashes doubled,
    a newline written \\n) and as JSON shows it. Longest first."""
    if not secret:
        return []
    return sorted({f for f in (secret, repr(secret)[1:-1], json.dumps(secret)[1:-1]) if f}, key=len, reverse=True)


def _clean(exc: BaseException, secret=None) -> str:
    """An error for the owner's screen. The secret is masked FIRST, on the raw text: squeezing the spaces or cutting
    at 160 characters before it could leave a piece of it (or a spelling that no longer matches). Then any address is
    masked, the text made one line and cut. A PhotoError is already a sentence; any other error gets its class name."""
    text = str(exc)
    for form in _secret_forms(secret):
        text = text.replace(form, "<password>")
    prefix = "" if isinstance(exc, PhotoError) else f"{type(exc).__name__}: "
    return (prefix + _mask(text))[:160]


_PLAIN_SEGMENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*$")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
MAX_LOCAL = 150                              # characters of a name inside the archive (Windows paths are short)


def _plain(segment: str) -> bool:
    return (bool(_PLAIN_SEGMENT.match(segment)) and not segment.endswith(".") and len(segment) <= 100
            and segment.split(".")[0].lower() not in _RESERVED)


def _clashes(rel: str, used: set[str]) -> bool:
    """True when `rel` cannot be a file beside the names already given out: it is one of them (letter case aside), it
    is the folder of one (a name cannot be a file and a folder, as with the keys 'x' and 'x/y.png'), or it lies in a
    folder that is already a file. `used` holds files as 'a/b' and folders as 'a/' (lower-cased)."""
    low = rel.lower()
    if low in used or low + "/" in used:
        return True
    pieces = low.split("/")
    return any("/".join(pieces[:i]) in used for i in range(1, len(pieces)))


def _claim(rel: str, used: set[str]) -> None:
    low = rel.lower()
    used.add(low)
    pieces = low.split("/")
    for i in range(1, len(pieces)):
        used.add("/".join(pieces[:i]) + "/")


def local_name(bucket: str, path: str, used: set[str]) -> str:
    """Where an object goes inside the archive: files/<bucket>/<path> when every part of that is a plain
    name any system accepts (letters, digits, '_', '-', '.'), else files/_odd/<hash of the key><extension>.
    A key that would clash with another (the same file in another letter case, or a file that is also the folder of
    another key: 'market/x' and 'market/x/y.png') gets the hashed name too, so no order of keys can fail a write.
    `used` (lower-cased names and folders so far) is updated. The real key is always in the manifest."""
    parts = [bucket, *str(path).split("/")]
    rel = "/".join([FILES_DIR, *parts])
    if not all(_plain(p) for p in parts) or len(rel) > MAX_LOCAL or _clashes(rel, used):
        ext = Path(str(path)).suffix
        ext = ext.lower() if re.fullmatch(r"\.[A-Za-z0-9]{1,8}", ext or "") else ""
        digest = hashlib.sha1(f"{bucket}/{path}".encode("utf-8", "surrogatepass")).hexdigest()[:16]
        rel, n = f"{FILES_DIR}/_odd/{digest}{ext}", 2
        while _clashes(rel, used):                                    # the same key twice: never overwrite a file
            rel, n = f"{FILES_DIR}/_odd/{digest}-{n}{ext}", n + 1
    _claim(rel, used)
    return rel


_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")


def unsafe_key(bucket: str, path: str) -> str | None:
    """Why an object must not be requested, or None. The Storage client writes the name into the address as if it were
    part of a URL: it drops a '?' or '#' tail, decodes %XX, resolves '.', '..' and '//', reads a leading 'scheme:',
    strips a leading space and the tab and new line, so such a name asks for ANOTHER object, or sends the service key to
    another path of the same host ('../../functions/v1/x'). Measured against the pinned client (the test walks a long
    list of names through it); everything else (spaces, accents, a lone '%', 'a..b') arrives exactly as written."""
    if bucket not in BUCKETS:
        return "not one of the photo buckets"
    key = str(path)
    if not key:
        return "empty name"
    if key.startswith("/"):
        return "starts with '/'"
    if key.startswith(" "):
        return "starts with a space"
    if any(unicodedata.category(c) == "Cc" for c in key):
        return "has a control character"
    for char, words in (("?", "has a '?'"), ("#", "has a '#'"), ("\\", "has a backslash")):
        if char in key:
            return words
    if _ESCAPE.search(key):
        return "has a '%XX' escape (the client would decode it)"
    parts = key.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return "has an empty, '.' or '..' part"
    if ":" in parts[0]:
        return "has ':' in its first part (the client would read a scheme)"
    return None


def _not_found(exc: BaseException) -> bool:
    """Storage's answer for an OBJECT that is not there, and nothing else ("Object not found", 404). A
    missing bucket, an edge page or a network error is a failure, not a missing photo."""
    message = str(getattr(exc, "message", "") or "").strip().lower()
    status = str(getattr(exc, "status", "") or "").strip()
    code = str(getattr(exc, "code", "") or "").strip().lower()
    return (message == "object not found" and status in ("404", "400")) or (code == "not_found" and status == "404"
                                                                             and "bucket" not in message)


def _reason(exc: BaseException) -> str:
    """Why a download failed, for the list: the storage answer's words, or the error's class. No address."""
    message = getattr(exc, "message", None)
    if message:
        return f"storage said {getattr(exc, 'status', '?')}: {_mask(message)[:60]}"
    return type(exc).__name__


def _http_status(exc: BaseException) -> int | None:
    """The HTTP status behind a failed Storage call. The SDK raises StorageApiError (with .status) when the answer's body
    has the fields it expects; for any other body (the API gateway's, a plain page) it fails with another error from
    inside its own handler, so the chain of causes is walked for the error that carries the response."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        for value in (getattr(exc, "status", None), getattr(getattr(exc, "response", None), "status_code", None)):
            code = _int(value)
            if code:
                return code
        exc = exc.__cause__ or exc.__context__
    return None


# ── what the database and storage hold ────────────────────────────────────────

def columns_of(sess, table: str) -> set[str]:
    rows = sess.q("select column_name from information_schema.columns "
                  "where table_schema = 'public' and table_name = %s", [table])
    return {str(r["column_name"]) for r in rows}


def read_references(sess) -> tuple[list[dict], dict, list[str]]:
    """Every database reference to a photo object: (refs, per-table counts, notes). A ref is {bucket, path,
    source ('market_photos.thumb_path'), id, sha256 or None}. market_photos holds the master (with the
    sha256 recorded at capture, once the R8m columns exist), its thumbnail and its blurred copy;
    product_finds and field_notes hold the one photo of the old boards. A table or column that is not
    there is a note, not a failure: the migrations may not all have been applied."""
    refs: list[dict] = []
    tables: dict[str, dict] = {}
    notes: list[str] = []
    have = columns_of(sess, MARKET)
    want = [c for c in ("id", "bucket", "path", "thumb_path", "blurred_path", "sha256") if c in have]
    if not have:
        notes.append(f"{MARKET} does not exist: no Market Intel photo rows")
    elif "id" not in want or "path" not in want:
        notes.append(f"{MARKET} has no id / path column: skipped")
    else:
        data = sess.q(f"select {', '.join(want)} from {MARKET} order by id")
        before = len(refs)
        for r in data:
            bucket = str(r.get("bucket") or "finds")
            for col in ("path", "thumb_path", "blurred_path"):
                if r.get(col):
                    refs.append({"bucket": bucket, "path": str(r[col]), "source": f"{MARKET}.{col}", "id": r["id"],
                                 "sha256": _norm_sha(r.get("sha256")) if col == "path" else None})
        tables[MARKET] = {"rows": len(data), "refs": len(refs) - before,
                          "checksums": sum(1 for r in data if _norm_sha(r.get("sha256")))}
        if "sha256" not in want:
            notes.append(f"{MARKET} has no sha256 column yet: nothing to compare the photos with")
    for table, bucket in LEGACY:
        have = columns_of(sess, table)
        if not have:
            notes.append(f"{table} does not exist: no rows of the old board")
        elif "id" not in have or "image_path" not in have:
            notes.append(f"{table} has no image_path column: skipped")
        else:
            data = sess.q(f"select id, image_path from {table} where image_path is not null order by id")
            data = [r for r in data if str(r.get("image_path") or "").strip()]
            for r in data:
                refs.append({"bucket": bucket, "path": str(r["image_path"]), "source": f"{table}.image_path",
                             "id": r["id"], "sha256": None})
            tables[table] = {"rows": len(data), "refs": len(data), "checksums": 0}
    return refs, tables, notes


def read_objects(sess, buckets=BUCKETS) -> tuple[list[dict], list[str]]:
    """Every object of the photo buckets: (objects as {bucket, path, size}, notes). Read from storage.objects,
    the table Storage keeps its own list in (one SELECT; the Storage API's list is a POST the read-only
    client refuses). A folder placeholder is no photo and is left out, said in the notes."""
    data = sess.q("select bucket_id, name, (metadata->>'size') as size_bytes from storage.objects "
                  "where bucket_id = any(%s) order by bucket_id, name", [list(buckets)])
    objects, ignored = [], 0
    for r in data:
        name = str(r.get("name") or "")
        if not name or name.endswith("/") or name.rsplit("/", 1)[-1] == PLACEHOLDER:
            ignored += 1
            continue
        objects.append({"bucket": str(r["bucket_id"]), "path": name, "size": _int(r.get("size_bytes"))})
    return objects, ([f"{ignored} folder placeholder(s) in storage ignored (they are not photos)"] if ignored else [])


class SupabaseSource:
    """The real source: a read-only database session for the rows and the object list, and the read-only
    Supabase client (scripts/ops/db.py) for the downloads. `client_factory` builds the client on the first
    download, so a dry run never creates one."""

    def __init__(self, session, client_factory):
        self.session = session
        self._factory = client_factory
        self._client = None
        self._closed = False

    @classmethod
    def open(cls) -> "SupabaseSource":
        from scripts.ops import db
        return cls(db.session(), db.client)

    def close(self) -> None:
        """Release the database session (safe to call twice)."""
        if self._closed:
            return
        self._closed = True
        try:
            self.session.close()
        except Exception:  # noqa: BLE001
            pass

    def read(self) -> dict:
        """{objects, refs, tables, notes}. The rows are read BEFORE the object list: a photo is stored before
        its row is written, so a row read first always finds its object in the list read second. The session
        is released as soon as both are read: the downloads that follow can take minutes and need no
        database."""
        try:
            refs, tables, notes = read_references(self.session)
            objects, more = read_objects(self.session)
        finally:
            self.close()
        return {"objects": objects, "refs": refs, "tables": tables, "notes": notes + more}

    def download(self, bucket: str, path: str) -> bytes:
        """One object's bytes. Raises UnsafeKey (a name the client would send elsewhere: nothing is requested),
        SourceRefused (the client cannot be built, or storage answers 401 / 403: the run stops), ObjectMissing
        (storage says there is no such object) or DownloadFailed (anything else)."""
        why = unsafe_key(bucket, path)
        if why:
            raise UnsafeKey(why)
        try:
            if self._client is None:
                self._client = self._factory()
        except Exception as exc:  # noqa: BLE001  (a missing key or address, a library that will not start)
            raise SourceRefused(f"the storage client could not be built ({_clean(exc)})") from None
        try:
            return bytes(self._client.storage.from_(bucket).download(path))
        except Exception as exc:  # noqa: BLE001
            if _not_found(exc):
                raise ObjectMissing("object not found") from None
            status = _http_status(exc)
            if status in (401, 403):
                raise SourceRefused(f"storage refused the key (HTTP {status})") from None
            raise DownloadFailed(_reason(exc)) from None


def fetch(source, bucket: str, path: str, tries: int = RETRIES, sleep=time.sleep) -> bytes:
    """One object, retried on a failure. A missing object, an unsafe name and a refusal are not retried: trying again
    cannot change the answer."""
    last = "download failed"
    for attempt in range(1, tries + 1):
        try:
            return source.download(bucket, path)
        except (ObjectMissing, UnsafeKey, PhotoError):
            raise
        except DownloadFailed as exc:
            last = str(exc)
        except Exception as exc:  # noqa: BLE001  (a source that does not classify its own errors)
            last = type(exc).__name__
        if attempt < tries:
            sleep(0.4 * attempt)
    raise DownloadFailed(last)


def analyse(snap: dict) -> dict:
    """The comparison of the rows with the objects (pure)."""
    objects = snap["objects"]
    have = {(o["bucket"], o["path"]) for o in objects}
    by_key: dict[tuple, list[dict]] = {}
    for r in snap["refs"]:
        by_key.setdefault((r["bucket"], r["path"]), []).append(r)
    buckets = {b: {"objects": 0, "bytes": 0} for b in BUCKETS}
    for o in objects:
        s = buckets.setdefault(o["bucket"], {"objects": 0, "bytes": 0})
        s["objects"] += 1
        s["bytes"] += o["size"] or 0
    return {"buckets": buckets, "objects": objects, "refs": snap["refs"], "by_key": by_key,
            "tables": snap["tables"], "notes": list(snap["notes"]),
            "missing_refs": [r for r in snap["refs"] if (r["bucket"], r["path"]) not in have],
            "orphans": [o for o in objects if (o["bucket"], o["path"]) not in by_key],
            "total_objects": len(objects), "total_bytes": sum(s["bytes"] for s in buckets.values())}


def describe(inv: dict, say) -> None:
    say("Photo storage (read only):")
    for name in sorted(inv["buckets"]):
        s = inv["buckets"][name]
        say(f"  bucket {name:<12} {s['objects']:>7,} objects  {fmt_bytes(s['bytes'])}")
    say(f"  {'all buckets':<19} {inv['total_objects']:>7,} objects  {fmt_bytes(inv['total_bytes'])}")
    say("Database rows that point at a photo:")
    if not inv["tables"]:
        say("  none found")
    for table, t in sorted(inv["tables"].items()):
        extra = f", {t['checksums']:,} with a recorded sha256" if table == MARKET else ""
        say(f"  {table:<14} {t['rows']:>7,} rows, {t['refs']:,} object reference(s){extra}")
    miss = inv["missing_refs"]
    say(f"Rows whose object is not in storage: {len(miss):,}")
    if miss:
        ids: dict[str, list] = {}
        for r in miss:
            ids.setdefault(r["source"], []).append(r["id"])
        for source, found in sorted(ids.items()):
            shown = ", ".join(str(i) for i in found[:10]) + (", ..." if len(found) > 10 else "")
            say(f"  {source}: row id {shown}")
    say(f"Objects without a database row: {len(inv['orphans']):,}"
        + (" (archived all the same)" if inv["orphans"] else ""))
    for note in inv["notes"]:
        say(f"  note: {note}")


# ── where it goes ─────────────────────────────────────────────────────────────

def photos_dir(out=None) -> Path:
    """The folder of the archives and the work folders: --out, else <backup folder>/photos. A folder a
    sync client uploads (OneDrive) is refused: the photos would be copied to the cloud unencrypted."""
    from scripts import nightly_backup
    if out:
        dest = Path(out).expanduser().resolve()
        if nightly_backup.is_synced(dest):
            raise PhotoError(f"{dest} is inside a synced folder (OneDrive): keep the photo archive on this disk "
                             "only; name a local folder with --out")
        if dest == ROOT or ROOT in dest.parents:
            raise PhotoError(f"{dest} is inside the code folder, a public repository: name a folder outside it")
        return dest
    try:
        return Path(yb.backup_root()) / "photos"
    except SystemExit as exc:                        # nightly_backup.backup_root refuses a synced folder
        raise PhotoError(str(exc)) from None


def leftovers(dest: Path | None) -> list[str]:
    """Work folders (archive) and unpacked copies (verify) of an earlier run that did not finish: they hold
    UNENCRYPTED photos."""
    if dest is None or not dest.is_dir():
        return []
    return sorted(p.name for pattern in ("work_*", "verify_*") for p in dest.glob(pattern) if p.is_dir())


def space_refusal(dest: Path, total_bytes: int) -> str | None:
    """The work folder plus the archive hold the photos twice: refuse before downloading when the drive
    cannot take that."""
    probe = dest
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    need = 2 * int(total_bytes) + MARGIN
    free = shutil.disk_usage(probe).free
    if free < need:
        return (f"not enough free space on the drive of {dest}: about {fmt_bytes(need)} are needed, "
                f"{fmt_bytes(free)} are free")
    return None


def free_name(dest: Path, stem: str) -> Path:
    """<dest>/<stem>.7z, or <stem>_2.7z, <stem>_3.7z ... when that exists: a run never replaces an earlier archive,
    whatever it holds (a run that could read nothing must not overwrite a good one made a minute before)."""
    path, n = dest / f"{stem}.7z", 2
    while path.exists():
        path, n = dest / f"{stem}_{n}.7z", n + 1
    return path


def pack(folder: Path, archive: Path, password: str, exe: str) -> str:
    """The folder as an encrypted 7z (nightly_backup.seven_zip_cmd: AES-256, file names hidden), read back
    with 7-Zip's own test, its file count compared with the folder's. Returns 7-Zip's 'Files' line. Raises
    PhotoError on any doubt."""
    from scripts import nightly_backup
    if archive.exists():
        archive.unlink()
    r = subprocess.run(nightly_backup.seven_zip_cmd(exe, archive, folder, password), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0 or not archive.is_file():
        raise PhotoError(f"7-Zip could not pack the photos (exit {r.returncode})")
    ok, said = yb.read_back(archive, password, exe)
    expected = sum(1 for p in folder.rglob("*") if p.is_file())
    found = re.search(r"Files:\s*(\d+)", said or "")
    if not ok or not found or int(found.group(1)) != expected:
        raise PhotoError(f"the archive does not read back with all {expected} files ({said})")
    return said


def encryption_refusal(password: str | None, exe: str | None) -> str | None:
    """Why nothing may be packed, or None: the photos are archived encrypted or not at all."""
    missing = []
    if not password:
        missing.append("no backup password is set on this PC (cmdkey /generic:YQ_BACKUP_PASSWORD /user:yq /pass)")
    if not exe:
        missing.append("7-Zip is not installed")
    return " and ".join(missing) or None


def _record(row: dict, say) -> None:
    try:
        yb.record(row)
    except OSError as exc:
        say(f"WARNING: business_data/ops/drills.jsonl could not be written ({type(exc).__name__}).")


def _problem_line(p: dict) -> str:
    where = f"{p['bucket']}:{p['path']}" if p.get("path") else p.get("source", "?")
    row = f" ({p['source']} row {p['id']})" if p.get("source") and p.get("id") is not None else ""
    return f"  {p['type']:<8} {where}{row}: {p['detail']}"


# ── archive ───────────────────────────────────────────────────────────────────

def archive(apply: bool = False, out=None, source=None, say=print, now: datetime | None = None,
            sleep=time.sleep) -> int:
    t0 = time.monotonic()
    now = now or datetime.now()
    password = pw_from = exe = None
    if apply:
        password, pw_from = yq_secrets.backup_password()
        exe = yb.find_7z()
        refused = encryption_refusal(password, exe)
        if refused:
            say(f"Not run: the photos are archived encrypted or not at all, and {refused}. Nothing was changed.")
            return 2
    try:
        dest = photos_dir(out)
    except PhotoError as exc:
        if apply:
            say(f"Not run: {exc}. Nothing was changed.")
            return 2
        dest = None
        say(f"Note: {exc}")
    owns = source is None
    try:
        if owns:
            source = SupabaseSource.open()
        inv = analyse(source.read())
    except Exception as exc:  # noqa: BLE001  (the database, the network, a missing setting)
        say(f"Not run: the photo list could not be read ({_clean(exc)}). Nothing was changed.")
        if owns and source is not None:
            source.close()
        return 2
    try:
        describe(inv, say)
        old = leftovers(dest)
        if old:
            say(f"WARNING: {len(old)} folder(s) of an interrupted run are still in {dest}: they hold "
                "UNENCRYPTED photos. Delete them: " + ", ".join(old))
        if not apply:
            say(f"Dry run: nothing was downloaded or changed. --apply archives {inv['total_objects']:,} object(s), "
                f"about {fmt_bytes(inv['total_bytes'])}" + (f", into {dest}." if dest else "."))
            return 0
        return _apply(source, inv, dest, password, pw_from, exe, now, t0, say, sleep)
    finally:
        if owns:
            source.close()


def _apply(source, inv: dict, dest: Path, password: str, pw_from: str, exe: str, now: datetime, t0: float,
           say, sleep) -> int:
    full = space_refusal(dest, inv["total_bytes"])
    if full:
        say(f"Not run: {full}. Nothing was changed.")
        return 2
    stamp = now.strftime("%Y%m%d_%H%M%S")
    work, staging = dest / f"work_{stamp}", dest / f"work_{stamp}.7z"
    row = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": KIND, "archive": None,
           "password_from": pw_from, "objects": 0, "bytes": 0, "rows": sum(t["rows"] for t in inv["tables"].values()),
           "checked": 0, "no_checksum": 0, "mismatches": 0, "missing": 0, "errors": 0, "orphans": len(inv["orphans"])}
    entries: list[dict] = []
    problems: list[dict] = [{"type": "missing", "bucket": r["bucket"], "path": r["path"], "source": r["source"],
                             "id": r["id"], "detail": "the database row points at an object storage does not have"}
                            for r in inv["missing_refs"]]
    why = None
    try:
        (work / FILES_DIR).mkdir(parents=True, exist_ok=True)
        used: set[str] = set()
        total = inv["total_objects"]
        say(f"Downloading {total:,} object(s) into {work} ...")
        failed_in_a_row = 0
        for n, obj in enumerate(inv["objects"], 1):
            bucket, path = obj["bucket"], obj["path"]
            refs = inv["by_key"].get((bucket, path), [])
            tags = [f"{r['source']}#{r['id']}" for r in refs]
            where = {"bucket": bucket, "path": path, "source": refs[0]["source"] if refs else None,
                     "id": refs[0]["id"] if refs else None}
            refused = unsafe_key(bucket, path)             # checked here too: no source may be trusted with a name like that
            if refused:
                problems.append({"type": "error", **where, "detail": f"object name refused: {refused}"})
                continue
            try:
                data = fetch(source, bucket, path, sleep=sleep)
            except ObjectMissing:
                failed_in_a_row = 0                         # storage answered: the connection and the key are fine
                problems.append({"type": "missing", **where, "detail": "storage lists the object but cannot serve it"})
                continue
            except UnsafeKey as exc:
                problems.append({"type": "error", **where, "detail": f"object name refused: {exc}"})
                continue
            except DownloadFailed as exc:
                problems.append({"type": "error", **where, "detail": f"could not be downloaded ({exc})"})
                failed_in_a_row += 1
                if failed_in_a_row >= ABORT_AFTER:
                    raise PhotoError(f"{failed_in_a_row} downloads in a row failed ({exc}): storage or the network is "
                                     "not answering") from None
                continue
            failed_in_a_row = 0
            sha = hashlib.sha256(data).hexdigest()
            dbs = sorted({d for d in (_norm_sha(r.get("sha256")) for r in refs) if d})
            match = (all(d == sha for d in dbs)) if dbs else None
            rel = local_name(bucket, path, used)
            target = (work / rel).resolve()
            if work.resolve() not in target.parents:
                raise PhotoError("an object name would be written outside the work folder: stopped")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            entries.append({"bucket": bucket, "path": path, "file": rel, "size": len(data), "sha256": sha,
                            "db_sha256": dbs[0] if dbs else None, "match": match, "refs": tags})
            row["bytes"] += len(data)
            if match is False:
                problems.append({"type": "mismatch", **where, "detail": "sha256 differs from the one in the database"})
            if obj.get("size") is not None and obj["size"] != len(data):
                problems.append({"type": "error", **where,
                                 "detail": f"{len(data)} bytes downloaded, storage lists {obj['size']}"})
            if n % 250 == 0:
                say(f"  {n:,} of {total:,}")
        if total and not entries:                           # a manifest and no photo is no archive of these photos
            by_type = {kind: sum(1 for p in problems if p["type"] == kind) for kind in ("missing", "error")}
            raise PhotoError(f"none of the {total} listed object(s) could be downloaded ({by_type['missing']} not in "
                             f"storage, {by_type['error']} failed or refused)")
        row.update(objects=len(entries), checked=sum(1 for e in entries if e["match"] is True),
                   no_checksum=sum(1 for e in entries if e["match"] is None),
                   mismatches=sum(1 for p in problems if p["type"] == "mismatch"),
                   missing=sum(1 for p in problems if p["type"] == "missing"),
                   errors=sum(1 for p in problems if p["type"] == "error"))
        manifest = {"kind": "yq-photos", "version": 1, "created_at": row["at"], "buckets": list(BUCKETS),
                    "counts": {k: row[k] for k in ("objects", "bytes", "rows", "checked", "no_checksum", "mismatches",
                                                   "missing", "errors", "orphans")},
                    "notes": inv["notes"], "files": entries, "problems": problems}
        (work / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        say(f"Packing {len(entries):,} photo(s) and the manifest, encrypted ...")
        said = pack(work, staging, password, exe)
        final = free_name(dest, f"{now:%Y-%m-%d_%H%M}_{KIND}")      # never an earlier archive's name
        os.replace(staging, final)
        row["archive"] = final.name
        say(f"Archive: {final} ({fmt_bytes(final.stat().st_size)}, AES-256, file names hidden); read back whole ({said}).")
    except (PhotoError, OSError, subprocess.SubprocessError) as exc:
        why = _clean(exc, password)                                 # 7-Zip's own errors can quote its command line
    finally:
        shutil.rmtree(work, ignore_errors=True)
        try:
            staging.unlink()
        except OSError:
            pass
    if work.exists():
        say(f"WARNING: the work folder {work} could not be deleted. It holds UNENCRYPTED photos: delete it.")
    row["seconds"] = round(time.monotonic() - t0, 1)
    if why:
        row.update(result="failed", why=why[:200])
        _record(row, say)
        say(f"PHOTO ARCHIVE FAILED: {why}. No archive was made.")
        return 2
    row["result"] = "problems" if problems else "passed"
    _record(row, say)
    say(f"{row['objects']:,} photo(s), {fmt_bytes(row['bytes'])}: {row['checked']:,} match the sha256 in the database, "
        f"{row['no_checksum']:,} have none to compare, {row['mismatches']:,} differ.")
    if problems:
        say(f"PROBLEMS ({len(problems)}): the archive was made, but these need a look "
            f"({row['missing']} missing, {row['mismatches']} differ, {row['errors']} not downloaded):")
        for p in problems[:SHOWN]:
            say(_problem_line(p))
        if len(problems) > SHOWN:
            say(f"  ... and {len(problems) - SHOWN} more: all are in {MANIFEST} inside the archive.")
        return 1
    say(f"PHOTO ARCHIVE PASSED in {row['seconds']} s.")
    return 0


# ── verify ────────────────────────────────────────────────────────────────────

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def open_photo(path: Path) -> str | None:
    """None when Pillow can decode the whole photo, else why not (the error's class)."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.load()
            if im.size[0] < 1 or im.size[1] < 1:
                return "empty image"
    except Exception as exc:  # noqa: BLE001  (truncated, corrupt, not a picture, too large)
        return type(exc).__name__
    return None


def _manifest_root(folder: Path) -> Path | None:
    if (folder / MANIFEST).is_file():
        return folder
    inner = [p for p in folder.iterdir() if p.is_dir() and (p / MANIFEST).is_file()] if folder.is_dir() else []
    return inner[0] if len(inner) == 1 else None


def check_folder(root: Path, manifest: dict, sample: int, rng, say) -> int:
    """Every file of the manifest against the unpacked folder, and N random photos opened. Returns the
    number of faults found (0 = the copy is whole)."""
    entries = manifest.get("files")
    if not isinstance(entries, list):
        say("   the manifest has no file list")
        return 1
    base = root.resolve()
    faults = {"differ": 0, "missing": 0, "unsafe": 0, "extra": 0}
    listed: set[Path] = set()
    for e in entries:
        rel = str(e.get("file") or "")
        target = (root / rel).resolve()
        if not rel or base not in target.parents:
            faults["unsafe"] += 1
            continue
        listed.add(target)
        if not target.is_file():
            faults["missing"] += 1
        elif sha256_file(target) != _norm_sha(e.get("sha256")) or target.stat().st_size != e.get("size"):
            faults["differ"] += 1
    for p in base.rglob("*"):
        if p.is_file() and p not in listed and p != base / MANIFEST:
            faults["extra"] += 1
    ok = len(entries) - faults["differ"] - faults["missing"] - faults["unsafe"]
    say(f"3. Checked {len(entries):,} file(s) against the manifest: {ok:,} match (sha256 and size), "
        f"{faults['differ']} differ, {faults['missing']} missing, {faults['extra']} not in the manifest"
        + (f", {faults['unsafe']} with an unsafe name" if faults["unsafe"] else ""))
    pool = [e for e in entries if str(e.get("file") or "").lower().endswith(IMAGE_EXTS)]
    picked = rng.sample(pool, min(sample, len(pool))) if sample > 0 else []
    bad = 0
    for e in picked:
        target = (root / str(e["file"])).resolve()
        if base in target.parents and target.is_file() and open_photo(target) is None:
            continue
        bad += 1
    say(f"4. Opened {len(picked) - bad} of {len(picked)} sampled photo(s) with Pillow"
        + (f": {bad} cannot be opened" if bad else (f" ({len(pool):,} photos in the archive)" if picked else "")))
    return sum(faults.values()) + bad


def verify(path: Path, sample: int = 5, say=print, rng=None, now: datetime | None = None) -> int:
    t0 = time.monotonic()
    path = Path(path)
    if not path.is_file():
        say(f"Not run: there is no archive {path}.")
        return 2
    password, _source = yq_secrets.backup_password()
    exe = yb.find_7z()
    refused = encryption_refusal(password, exe)
    if refused:
        say(f"Not run: opening the archive needs the backup password and 7-Zip, and {refused}. Nothing was changed.")
        return 2
    try:
        work = photos_dir() / f"verify_{(now or datetime.now()):%Y%m%d_%H%M%S}"
    except PhotoError as exc:
        say(f"Not run: {exc}. Nothing was changed.")
        return 2
    faults = 1
    try:
        ok, said = yb.read_back(path, password, exe)
        say(f"1. Read back {path.name}: {said}")
        if not ok:
            say("VERIFY FAILED: the archive does not read back whole (a wrong password reads the same way).")
            return 1
        yb.unpack(path, work, password, exe)
        root = _manifest_root(work)
        if root is None:
            say("VERIFY FAILED: the archive holds no manifest.json.")
            return 1
        manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
        say(f"2. Unpacked into {work}")
        faults = check_folder(root, manifest, sample, rng or random.Random(), say)
        known = [p for p in (manifest.get("problems") or []) if isinstance(p, dict)]
        errors = [p for p in known if p.get("type") == "error"]
        if errors:                                          # photos the archive was made without: it is not a whole copy
            say(f"5. The manifest records {len(errors)} download error(s) made when the archive was built: those photos "
                "are not in it (or not whole). Make a new archive once storage answers.")
            faults += len(errors)
        if len(known) > len(errors):
            say(f"5. Note: the manifest records {len(known) - len(errors)} problem(s) found when the archive was made "
                "(a photo missing in storage, or differing from its database sha256).")
    except (yb.DrillError, OSError, ValueError, KeyError, AttributeError) as exc:
        say(f"VERIFY FAILED: {_clean(exc, password)}")
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if work.exists():                                   # said on every way out, an early stop included
            say(f"WARNING: the unpacked copy {work} could not be deleted. It holds UNENCRYPTED photos: delete it.")
    gone = " The unpacked copy has been deleted." if not work.exists() else ""
    if faults:
        say(f"VERIFY FAILED: {faults} fault(s).{gone}")
        return 1
    say(f"VERIFY PASSED in {round(time.monotonic() - t0, 1)} s.{gone}")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="python -m scripts.yq_photos", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("archive", help="list the photos (read only) or, with --apply, archive them encrypted")
    a.add_argument("--apply", action="store_true", help="download, hash, pack and read back (without it: counts only)")
    a.add_argument("--out", help="the folder for the archive and the work folder "
                                 "(default: %%LOCALAPPDATA%%\\yq-backups\\photos)")
    v = sub.add_parser("verify", help="unpack an archive, check every file against its manifest, open some photos")
    v.add_argument("archive", help="the <date>_<HHMM>_photos.7z to check")
    v.add_argument("--sample", type=int, default=5, help="photos to open with Pillow (default 5)")
    args = ap.parse_args(argv)
    if args.cmd == "archive":
        return archive(args.apply, args.out)
    return verify(Path(args.archive), max(0, args.sample))


if __name__ == "__main__":
    raise SystemExit(main())
