"""Build the marketplace's responsive WebP thumbnails (160/320/512) for every catalog photo.

    python -m scripts.make_market_thumbs [--only-missing] [--codes X01,UK15] [--kinds product]
                                         [--dry-run] [--workers 6]

New/replaced photos get every size automatically (app.catalog.upload_thumb); this covers the
photos imported before the size set existed. Files land next to the legacy 256px JPEG thumbs
in the public `catalog` bucket (additive — nothing is deleted) with a one-year cache header.
Prints per-size byte totals so the egress saving is visible.
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

import warnings  # noqa: E402
warnings.filterwarnings("ignore")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only-missing", action="store_true", help="skip codes that already have every size")
    ap.add_argument("--codes", default="", help="comma-separated item codes (default: all with a photo)")
    ap.add_argument("--kinds", default="product,package")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    import requests

    from app.catalog import THUMB_SIZES, get_client, make_thumb, public_url, thumb_path, upload_thumb
    from app.db_read import exec_sql

    rows = exec_sql(
        "SELECT item_code, product_image_url, package_image_url FROM v_catalog "
        "WHERE product_image_url IS NOT NULL OR package_image_url IS NOT NULL ORDER BY item_code"
    ) or []
    want = {c.strip().upper() for c in args.codes.split(",") if c.strip()}
    kinds = [k.strip() for k in args.kinds.split(",") if k.strip() in ("product", "package")]
    jobs: list[tuple[str, str, str]] = []
    for r in rows:
        if want and str(r["item_code"]).upper() not in want:
            continue
        for kind in kinds:
            url = r.get(f"{kind}_image_url")
            if url:
                jobs.append((str(r["item_code"]), kind, url))

    existing: set[str] = set()
    if args.only_missing:
        try:
            listing = get_client().storage.from_("catalog").list("thumbs", {"limit": 5000})
            existing = {f"thumbs/{f['name']}" for f in listing if f.get("name")}
        except Exception as e:  # noqa: BLE001
            print(f"  ! could not list bucket ({str(e)[:60]}); building everything")
        jobs = [j for j in jobs if not all(thumb_path(j[0], j[1], s) in existing for s in THUMB_SIZES)]

    print(f"{len(jobs)} photo(s) -> {len(THUMB_SIZES)} WebP sizes each{' (dry run)' if args.dry_run else ''}")
    if not jobs:
        return 0

    sess = requests.Session()
    totals = {s: 0 for s in THUMB_SIZES}
    totals_legacy = 0

    def work(code: str, kind: str, url: str) -> tuple[str, str, dict[int, int], int, str | None]:
        try:
            resp = sess.get(url, timeout=20)
            resp.raise_for_status()
            data = resp.content
            sizes = {s: len(make_thumb(data, s, "WEBP") or b"") for s in THUMB_SIZES}
            legacy = len(make_thumb(data) or b"")
            if not args.dry_run:
                upload_thumb(code, kind, data, legacy=False)
            return code, kind, sizes, legacy, None
        except Exception as e:  # noqa: BLE001
            return code, kind, {}, 0, str(e)[:80]

    done = failed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(work, *j) for j in jobs]
        for f in as_completed(futs):
            code, kind, sizes, legacy, err = f.result()
            if err:
                failed += 1
                print(f"  ! {code}-{kind}: {err}")
                continue
            done += 1
            for s, n in sizes.items():
                totals[s] += n
            totals_legacy += legacy
            if done % 25 == 0:
                print(f"  ... {done}/{len(jobs)}")

    print(f"\n{done} built, {failed} failed")
    for s in THUMB_SIZES:
        print(f"  {s:>3}px WebP  total {totals[s] / 1024:8.1f} KB   avg {totals[s] / max(done, 1) / 1024:5.1f} KB")
    print(f"  256px JPEG  total {totals_legacy / 1024:8.1f} KB   avg {totals_legacy / max(done, 1) / 1024:5.1f} KB  (legacy, for comparison)")
    if jobs and not args.dry_run:
        c, k, _ = jobs[0]
        print(f"\nexample: {public_url(thumb_path(c, k, 320))}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
