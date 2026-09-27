"""Backfill product categories from Focus's OWN item-group grouping (Multi_level_stock_movement).

Focus groups every item under an item-group (= category): 'Cable', 'Charger', 'Power Bank', 'Sim'…
That report is the authoritative category source — no guessing, and categories come from the data
(not hardcoded, per docs/CLAUDE.md). We:
  1. parse (item_name, category) from the multi-level report (level-0 = category, indented = item),
  2. seed the `categories` table (+ a coarse division: Accessories vs Telecom),
  3. backfill products.category_id by matching item_name -> product (via product_aliases).

Categories change rarely → run occasionally, or it runs automatically during a refresh whenever a
Multi_level_stock_movement file is present in the upload.

R7d (27-Sep-2026): after the Focus pass the CATEGORY MASTER applies (apply_category_master): every
product whose code is a catalog item takes the analytics category its marketplace category maps to
(category_master, scripts/r7d_category_master_migration.sql), because Focus files power banks under
Cable and cables under Car Charger. Each change is logged in category_master_log. Until that
migration runs the master is absent and the Focus groups stand, exactly as before.

  python -m scripts.category_backfill ["business_data/Focus ERP Updated Reports"]
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

DEFAULT_FOLDER = "business_data/Focus ERP Updated Reports"
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


def _all_rows(c, table: str, cols: str, page: int = 1000) -> list[dict]:
    out: list[dict] = []
    off = 0
    while True:
        b = c.table(table).select(cols).order("id").range(off, off + page - 1).execute().data or []
        out.extend(b)
        if len(b) < page:
            return out
        off += page


def master_targets(master: list[dict], categories: list[dict], catalog: list[dict],
                   products: list[dict]) -> dict[int, int]:
    """Pure (R7d): product id -> the analytics category the category master gives it -- the same rule
    as scripts/r7d_category_master_migration.sql. A product whose code is a catalog item takes the
    category its marketplace category maps to; only Accessories are governed (a product in a SIM /
    Devices / Giveaway category, a non-Accessories catalog row or a non-Accessories target is not)."""
    division = {r["id"]: (r.get("division") or "Accessories") for r in categories}
    target = {str(r["catalog_category"]): r["category_id"] for r in master}
    by_code: dict[str, dict] = {}
    for r in sorted(catalog, key=lambda x: x.get("id") or 0):
        by_code.setdefault(str(r.get("item_code") or "").upper(), r)
    out: dict[int, int] = {}
    for p in products:
        ci = by_code.get(str(p.get("sku_code") or "").upper())
        if not ci or (ci.get("division") or "Accessories") != "Accessories":
            continue
        new = target.get(str(ci.get("category") or ""))
        cur = p.get("category_id")
        if new is None or division.get(new) != "Accessories":
            continue
        if cur is not None and division.get(cur, "Accessories") != "Accessories":
            continue
        out[p["id"]] = new
    return out


def plan_category_master(master: list[dict], categories: list[dict], catalog: list[dict],
                         products: list[dict]) -> list[dict]:
    """Pure (R7d): the governed products whose category differs from the master's."""
    targets = master_targets(master, categories, catalog, products)
    return [{"product_id": p["id"], "sku_code": p.get("sku_code"), "old_category_id": p.get("category_id"),
             "new_category_id": targets[p["id"]]}
            for p in products if p["id"] in targets and targets[p["id"]] != p.get("category_id")]


def _load_master(c) -> dict | None:
    """The master and what it needs, or None before the migration (no category_master table)."""
    try:
        master = c.table("category_master").select("catalog_category,category_id").execute().data or []
    except Exception:  # noqa: BLE001 -- the migration has not run: Focus item-groups stand
        return None
    if not master:
        return None
    return {"master": master,
            "categories": c.table("categories").select("id,name,division").execute().data or [],
            "catalog": _all_rows(c, "catalog_items", "id,item_code,category,division"),
            "products": _all_rows(c, "products", "id,sku_code,category_id")}


def apply_category_master(c=None, source: str = "category_backfill") -> dict:
    """Catalog wins (R7d): re-point products.category_id to the category master, logging every
    change to category_master_log first. A no-op until scripts/r7d_category_master_migration.sql has
    created the master ({"ok": False})."""
    c = c or get_client()
    m = _load_master(c)
    if m is None:
        return {"ok": False, "reason": "no category_master (scripts/r7d_category_master_migration.sql not applied)"}
    plan = plan_category_master(m["master"], m["categories"], m["catalog"], m["products"])
    for i in range(0, len(plan), 200):
        chunk = plan[i:i + 200]
        c.table("category_master_log").insert([{**x, "source": source} for x in chunk]).execute()
    by_new: dict[int, list[int]] = defaultdict(list)
    for x in plan:
        by_new[x["new_category_id"]].append(x["product_id"])
    for cid, pids in by_new.items():
        for i in range(0, len(pids), 200):
            c.table("products").update({"category_id": cid}).in_("id", pids[i:i + 200]).execute()
    if plan:
        print(f"Category master: {len(plan)} product(s) re-pointed to the catalog's category")
    return {"ok": True, "changed": len(plan)}


def backfill(folder: str | None = None) -> dict:
    folder = folder or DEFAULT_FOLDER
    src_file, pairs = parse_categories(folder)
    if not pairs:
        print(f"No Multi_level_stock_movement report in '{folder}' — skipping category backfill.")
        # the catalog's categories still apply (new SKUs, or a master created since the last load)
        try:
            apply_category_master()
        except Exception as e:  # noqa: BLE001
            print(f"  category master not applied: {e}")
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

    # R7d: a product the category master governs keeps the catalog's category (no flip-flop through
    # the Focus group on every load); apply_category_master() below settles any difference
    m = _load_master(c)
    governed = set(master_targets(m["master"], m["categories"], m["catalog"], m["products"])) if m else set()
    by_cat: dict[int, list[int]] = defaultdict(list)
    matched, unmatched = 0, []
    for item, cat in pairs:
        pid = alias_map.get(item) or name_map.get(item)
        cid = cat_id.get(cat)
        if pid and cid:
            if pid not in governed:
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
    # R7d: the Focus item-groups file power banks under Cable; the catalog's category wins for every
    # catalog code once scripts/r7d_category_master_migration.sql has run (a no-op before it)
    try:
        cm = apply_category_master(c)
    except Exception as e:  # noqa: BLE001 -- never fail the refresh over the master
        cm = {"ok": False, "reason": str(e)[:120]}
    return {"ok": True, "categories": len(cats), "matched": matched, "unmatched": len(unmatched),
            "category_master": cm}


def main() -> int:
    folder = sys.argv[1] if len(sys.argv) > 1 else None
    return 0 if backfill(folder)["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
