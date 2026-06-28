"""Upload guards for the two file-upload endpoints (PO PDF, Focus Excel/CSV).

Keeps untrusted input bounded BEFORE it reaches a parser: a hard size cap that doesn't buffer
the whole oversized payload first, a per-request file-count cap, and a lightweight magic-byte
sniff so a renamed file can't be fed to the wrong parser. (xlsx is a zip; a size cap is the
practical defence against decompression bombs at this scale.)
"""
from __future__ import annotations

from fastapi import UploadFile

MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # 15 MB per file
MAX_INGEST_FILES = 30               # one daily Focus export set is small

# Magic-byte signatures by extension. Text formats (.csv) have no signature → extension-only.
_MAGIC: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF",),
    ".xlsx": (b"PK\x03\x04",),                            # OOXML = zip container
    ".xls": (b"\xD0\xCF\x11\xE0\xA1\xB1\x1a\xe1",),       # legacy OLE2 compound file
    # Field-note photos (reps snap competitor tags / shelves on a phone)
    ".jpg": (b"\xFF\xD8\xFF",),
    ".jpeg": (b"\xFF\xD8\xFF",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".webp": (b"RIFF",),                                  # RIFF....WEBP
    ".heic": (b"\x00\x00\x00\x18ftyp", b"\x00\x00\x00\x1cftyp"),  # iPhone HEIC
}

MAX_PHOTO_BYTES = 8 * 1024 * 1024  # 8 MB — a phone photo, not a scan dump
# (extension -> content-type) for the image formats we accept on field notes
PHOTO_TYPES: dict[str, str] = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".heic": "image/heic",
}


def photo_ext(filename: str) -> str:
    """Lower-cased extension if it's an accepted image type, else '' (reject)."""
    name = (filename or "").lower()
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    return ext if ext in PHOTO_TYPES else ""


class UploadTooLarge(ValueError):
    """Raised by read_capped when an upload exceeds the size cap."""


async def read_capped(file: UploadFile, max_bytes: int = MAX_UPLOAD_BYTES) -> bytes:
    """Read an UploadFile, refusing anything over max_bytes WITHOUT buffering the whole
    oversized payload. Uses Starlette's .size when present; otherwise reads max_bytes+1 and
    rejects if the cap is exceeded."""
    size = getattr(file, "size", None)
    if isinstance(size, int) and size > max_bytes:
        raise UploadTooLarge(f"max {max_bytes // (1024 * 1024)} MB")
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise UploadTooLarge(f"max {max_bytes // (1024 * 1024)} MB")
    return data


def content_matches(filename: str, data: bytes) -> bool:
    """True if the bytes look like the declared extension (magic-byte check). Unknown/text
    extensions (e.g. .csv) pass. Defends against a renamed file handed to a binary parser."""
    name = (filename or "").lower()
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    sigs = _MAGIC.get(ext)
    if not sigs:
        return True
    head = data[:16]
    return any(head.startswith(s) for s in sigs)
