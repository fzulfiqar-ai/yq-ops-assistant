"""Seed the Product Finds board from the historical WhatsApp dump.

    python -m scripts.import_unique_items ["Unique Items/Unique Items"] [--dry-run]

Walks a folder of images, uploads each to the PRIVATE 'finds' Supabase Storage bucket and
inserts a product_finds row (status 'new', source 'WhatsApp'). Idempotent: source_file has a
unique index, so a second run skips files already imported. Photos arrive un-annotated (the
WhatsApp captions/prices weren't attached to the files) — management fills name/price/note, or
sales annotates going forward. Videos (.mp4) are skipped for now (phase-1 board is images).
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from app.database import get_client  # noqa: E402
from app.product_finds import _BUCKET, ensure_bucket  # noqa: E402

_EXTS = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def run(folder: Path, dry: bool = False) -> int:
    client = get_client()
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in _EXTS)
    if not files:
        print(f"No images in {folder}")
        return 1

    # already-imported source files (dedupe against a re-run)
    done: set[str] = set()
    off = 0
    while True:
        b = (client.table("product_finds").select("source_file")
             .not_.is_("source_file", "null").range(off, off + 999).execute().data or [])
        done.update(r["source_file"] for r in b if r.get("source_file"))
        if len(b) < 1000:
            break
        off += 1000

    ensure_bucket()
    added = skipped = 0
    for p in files:
        if p.name in done:
            skipped += 1
            continue
        if dry:
            print(f"  would add {p.name}")
            added += 1
            continue
        ext = p.suffix.lower()
        path = f"finds/{uuid.uuid4().hex}{ext}"
        try:
            client.storage.from_(_BUCKET).upload(
                path, p.read_bytes(), {"content-type": _EXTS[ext], "upsert": "false"})
        except Exception as e:  # noqa: BLE001
            print(f"  ! upload failed {p.name}: {str(e)[:80]}")
            continue
        client.table("product_finds").insert({
            "image_path": path, "status": "new", "source": "WhatsApp",
            "posted_by": "sales-team", "source_file": p.name,
        }).execute()
        added += 1
        if added % 25 == 0:
            print(f"  … {added} uploaded")

    print(f"\n{'DRY RUN — ' if dry else ''}added: {added}, "
          f"skipped (already imported): {skipped}, total image files: {len(files)}")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry = "--dry-run" in sys.argv
    folder = Path(args[0]) if args else ROOT / "Unique Items" / "Unique Items"
    if not folder.is_absolute():
        folder = ROOT / folder
    if not folder.exists():
        print(f"ERROR: not found: {folder}")
        return 1
    return run(folder, dry=dry)


if __name__ == "__main__":
    raise SystemExit(main())
