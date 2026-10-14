"""Thumbnails and blurred copies for Market Intel photos stored before release R8m (plan Part F, Phase 0).

    python -m scripts.market_intel_thumbs                  # dry run (default): counts only, writes nothing
    python -m scripts.market_intel_thumbs --apply          # make up to 200 thumbnails (+ blurred copies)
    python -m scripts.market_intel_thumbs --apply --limit 500

Needs scripts/r8m_market_quality_migration.sql applied first (the thumb_path / blurred_path columns) and the
Supabase service key in .env (it reads and writes through the API's own client, app.database). What it does
(app.market_intel.backfill_thumbnails):

  * every market_photos row without a thumbnail — captured before R8m, imported from the old Finds / Field
    Notes boards, or whose thumbnail upload failed — gets its 320 px WebP (q70) written NEXT TO the master
    as <stem>-t320.webp in the same private bucket, and thumb_path set; until then the lists show the master;
  * every photo flagged with people that has no blurred copy gets one (<stem>-blur.webp, blurred_path):
    until it has one, such a photo is shown to nobody but an admin who asks for the original;
  * the masters are only read: no object is moved, overwritten or deleted. A photo that cannot be read is
    listed and skipped; a re-run picks up whatever is still missing.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Market Intel photo thumbnails and blurred copies (dry run by default).")
    ap.add_argument("--apply", action="store_true", help="write the thumbnails and copies (default: count only)")
    ap.add_argument("--limit", type=int, default=200, help="at most this many of each per run (default 200)")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    from app import market_intel as mi
    try:
        out = mi.backfill_thumbnails(limit=max(1, args.limit), dry_run=not args.apply)
    except mi.MarketIntelError as e:              # the database did not answer (a blip): nothing was written
        print(f"Not run: {e}")
        return 2
    if not out.get("ok"):
        print(f"Not run: {out.get('reason')}")
        return 2
    print(f"Photos without a thumbnail: {out['missing_thumbs']}; flagged photos without a blurred copy: "
          f"{out['missing_blurs']}.")
    if out["dry_run"]:
        print("Dry run: nothing written. Run again with --apply to make them.")
        return 0
    print(f"Made {out['thumbs']} thumbnail(s) and {out['blurs']} blurred cop(y/ies).")
    if out["failed"]:
        print(f"{len(out['failed'])} could not be made:")
        print(json.dumps(out["failed"][:20], indent=1))
    return 1 if out["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
