"""One-off: build 256px export thumbnails for every existing catalog photo.

    python -m scripts.catalog_thumbs_backfill

New/replaced photos get their thumb automatically (app.catalog.upload_image);
this covers the 157 photos imported before thumbnails existed.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

import warnings  # noqa: E402
warnings.filterwarnings("ignore")


def main() -> int:
    import requests

    from app.catalog import upload_thumb
    from app.db_read import exec_sql

    rows = exec_sql(
        "SELECT item_code, product_image_url, package_image_url FROM v_catalog "
        "WHERE product_image_url IS NOT NULL OR package_image_url IS NOT NULL"
    ) or []
    sess = requests.Session()
    done = failed = 0
    for r in rows:
        for kind in ("product", "package"):
            url = r.get(f"{kind}_image_url")
            if not url:
                continue
            try:
                resp = sess.get(url, timeout=15)
                resp.raise_for_status()
                upload_thumb(r["item_code"], kind, resp.content)
                done += 1
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"  ! {r['item_code']}-{kind}: {str(e)[:70]}")
    print(f"thumbnails: {done} built, {failed} failed")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
