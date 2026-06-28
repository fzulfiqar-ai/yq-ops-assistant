"""Backfill product categories from Focus's OWN item-group grouping (Multi_level_stock_movement).

Focus groups every item under an item-group (= category): 'Cable', 'Charger', 'Power Bank', 'Sim'…
That report is the authoritative category source — no guessing, and categories come from the data
(not hardcoded, per docs/CLAUDE.md). We:
  1. parse (item_name, category) from the multi-level report (level-0 = category, indented = item),
  2. seed the `categories` table (+ a coarse division: Accessories vs Telecom),
  3. backfill products.category_id by matching item_name -> product (via product_aliases).

Categories change rarely → run occasionally, or it runs automatically during a refresh whenever a
Multi_level_stock_movement file is present in the upload.

  python -m scripts.category_backfill ["Focus ERP Updated Reports"]
"""
from __future__ import annotations

import glob
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

from app.database import get_client  # noqa: E402
from scripts.ingest import read_grid  # noqa: E402

DEFAULT_FOLDER = "Focus ERP Updated Reports"
# Coarse division — reviewable seed (the granular categories are Focus's own item-groups).
TELECOM = {"Sim", "Postpaid Giveaway", "Batelco TRA Devices"}


def _clean(s: object) -> str:
    return " ".join(str(s).split())  # collapse newlines / repeated spaces


def parse_categories(folder: str) -> tuple[str | None, list[tuple[str, str]]]:
    src = folder if Path(folder).is_absolute() else str(ROOT / folder)
    files = glob.glob(str(Path(src) / "*ulti_level_stock_movement*.xls*"))
    if not files:
        return None, []
    g = read_grid(Path(files[0]))
    pairs: list[tuple[str, str]] = []  # (item_name, category)
    cat: str | None = None
    for i in range(6, len(g)):
        c0 = g.iat[i, 0]
        if c0 is None:
            continue
        raw = str(c0)
        low = raw.strip().lower()
        if low in ("", "nan") or "total" in low:
            continue
        lead = len(raw) - len(raw.lstrip())
        if lead == 0:                  # level-0 row = item-group / category
            cat = _clean(raw)
        elif cat:                      # indented = an item under the current category
            pairs.append((raw.strip(), cat))
    return files[0], pairs


def backfill(folder: str | None = None) -> dict:
    folder = folder or DEFAULT_FOLDER
    src_file, pairs = parse_categories(folder)
    if not pairs:
        print(f"No Multi_level_stock_movement report in '{folder}' — skipping category backfill.")
        return {"ok": False, "reason": "no multi-level report"}
    c = get_client()

    # 1 — seed categories (name + coarse division)
    cats = sorted({cat for _, cat in pairs})
    for name in cats:
        c.table("categories").upsert(
            {"name": name, "division": "Telecom" if name in TELECOM else "Accessories"},
            on_conflict="name").execute()
    cat_id = {r["name"]: r["id"] for r in (c.table("categories").select("id,name").execute().data or [])}

    # 2 — map each report item -> a product (via aliases first, then the product name)
    alias_map = {a["alias_text"]: a["product_id"]
                 for a in (c.table("product_aliases").select("alias_text,product_id").execute().data or [])
                 if a.get("alias_text")}
    name_map = {p["item_name"]: p["id"]
                for p in (c.table("products").select("id,item_name").execute().data or [])
                if p.get("item_name")}

    by_cat: dict[int, list[int]] = defaultdict(list)
    matched, unmatched = 0, []
    for item, cat in pairs:
        pid = alias_map.get(item) or name_map.get(item)
        cid = cat_id.get(cat)
        if pid and cid:
            by_cat[cid].append(pid)
            matched += 1
        else:
            unmatched.append(item)

    # 3 — backfill products.category_id (batched per category)
    for cid, pids in by_cat.items():
        uniq = list(dict.fromkeys(pids))
        for i in range(0, len(uniq), 200):
            c.table("products").update({"category_id": cid}).in_("id", uniq[i:i + 200]).execute()

    print(f"Categories seeded: {len(cats)} | report items: {len(pairs)} | "
          f"matched to a product: {matched} | unmatched: {len(unmatched)}")
    if unmatched:
        print("  e.g. unmatched:", unmatched[:8])
    return {"ok": True, "categories": len(cats), "matched": matched, "unmatched": len(unmatched)}


def main() -> int:
    folder = sys.argv[1] if len(sys.argv) > 1 else None
    return 0 if backfill(folder)["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
