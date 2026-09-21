"""Fill catalog photos from a folder of supplier photo folders (21-Sep-2026).

    python -m scripts.import_photo_folders "210926/图片" [--dry-run]

The supplier ships photos as one folder per model code:

    图片/P01/白底图.jpg          white-background product shot (preferred)
    图片/P01/1.jpg, 2.jpg …      lifestyle shots
    图片/T10/主图/1主图-黑色.jpg  "main image" sub-folder, colour variants
    图片/X10/X10.png

Like scripts/import_vfan_photos.py this NEVER creates or edits items and only fills
product_image_url where it is NULL (owner-uploaded photos always win), so a second run is
a no-op. A model folder maps to every catalog variant whose item_code starts with the
folder's tokens (P01 -> P01-1Mtr, P01-2Mtr; X10 -> X10 LT, X10 MK), via the same
match_codes() rule the workbook importer uses. Nothing is read from the folder except
image files; nothing else is uploaded.

Picking the product photo, in order: a file whose name contains 白底 (white background),
then the first image inside 主图 (main images), then the numerically-first image in the
folder root. Package photos are not derived from folders (the supplier does not separate them).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

import warnings  # noqa: E402
warnings.filterwarnings("ignore")

from scripts.import_vfan_photos import MODEL_RE, match_codes  # noqa: E402

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
MAX_BYTES = 8 * 1024 * 1024   # matches app.uploads.MAX_PHOTO_BYTES


def _num_key(p: Path):
    m = re.match(r"(\d+)", p.stem)
    return (0, int(m.group(1))) if m else (1, p.name.lower())


def pick_product_photo(folder: Path) -> Path | None:
    imgs = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMG_EXT]
    white = [p for p in imgs if "白底" in p.name]
    if white:
        return sorted(white, key=_num_key)[0]
    main = folder / "主图"
    if main.is_dir():
        m = [p for p in main.iterdir() if p.is_file() and p.suffix.lower() in IMG_EXT]
        if m:
            return sorted(m, key=_num_key)[0]
    if imgs:
        return sorted(imgs, key=_num_key)[0]
    return None


def run(src: Path, dry: bool) -> int:
    from app.catalog import _BUCKET, _safe_code, ensure_bucket, public_url, upload_thumb
    from app.database import get_client

    folders = sorted(p for p in src.iterdir() if p.is_dir() and MODEL_RE.match(p.name.upper()))
    print(f"model folders under {src.name}: {', '.join(p.name for p in folders) or '(none)'}")
    client = get_client()
    items = (client.table("catalog_items")
             .select("item_code,product_image_url").execute().data or [])
    if not dry:
        ensure_bucket()
    filled, skipped, unmatched = 0, 0, []
    for folder in folders:
        photo = pick_product_photo(folder)
        if photo is None:
            print(f"  {folder.name}: no image file found")
            continue
        if photo.stat().st_size > MAX_BYTES:
            print(f"  {folder.name}: {photo.name} is {photo.stat().st_size // 1024} KB (> 8 MB) -- skipped")
            continue
        hits = match_codes(folder.name, items)
        if not hits:
            unmatched.append(folder.name)
            continue
        data = photo.read_bytes()
        for code in hits:
            it = next(i for i in items if i["item_code"] == code)
            if it.get("product_image_url"):
                skipped += 1
                print(f"  keep   {code:16} already has a photo")
                continue
            if dry:
                filled += 1
                print(f"  would fill {code:16} from {folder.name}/{photo.relative_to(folder)} ({len(data) // 1024} KB)")
                continue
            ext = ".png" if data[:8].startswith(b"\x89PNG") else ".jpg"
            path = f"items/{_safe_code(code)}-product-supplier{ext}"
            try:
                client.storage.from_(_BUCKET).upload(
                    path, data,
                    {"content-type": "image/png" if ext == ".png" else "image/jpeg", "upsert": "true"})
            except Exception as e:  # noqa: BLE001
                print(f"  ! upload failed {path}: {str(e)[:80]}")
                continue
            upload_thumb(code, "product", data)
            (client.table("catalog_items")
             .update({"product_image_url": public_url(path), "updated_by": "supplier_photo_import"})
             .eq("item_code", code).is_("product_image_url", "null").execute())
            it["product_image_url"] = public_url(path)
            filled += 1
            print(f"  filled {code:16} from {folder.name}/{photo.relative_to(folder)} ({len(data) // 1024} KB)")
    print(f"\n{'DRY RUN -- ' if dry else ''}filled={filled} kept={skipped}"
          + (f" unmatched folders: {', '.join(unmatched)}" if unmatched else ""))
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    if not args:
        print(__doc__)
        return 1
    src = Path(args[0])
    if not src.is_absolute():
        src = ROOT / src
    if not src.is_dir():
        print(f"ERROR: not a folder: {src}")
        return 1
    return run(src, dry="--dry-run" in sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
