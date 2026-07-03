"""Catalog / Item Master — the platform version of the salesman catalog Excel.

Items live in catalog_items (photos in the PUBLIC 'catalog' storage bucket, price tiers
per the owner's sheet: Dealer / Causeway&RoadShow / RRP). The standard selling rate is
read live from the price book via v_catalog, so price-book uploads update the catalog
without any sync job. Salesmen get the Catalog feature only; customers get a tokenized
public link that exposes ONLY item + photo + RRP (never dealer pricing).
"""
from __future__ import annotations

import io
import logging
import re
import secrets
import time
from datetime import datetime, timezone

from app.config import settings
from app.database import get_client
from app.db_read import exec_sql, exec_sql_params

log = logging.getLogger(__name__)

_BUCKET = "catalog"
CATEGORY_ORDER = ["CABLE", "CHARGER", "EARPHONE", "BLUETOOTH HEADSET", "FOR CAR",
                  "POWER BANK", "BLUETOOTH SPEAKER"]

PUBLIC_FIELDS = ("item_code", "display_name", "spec", "category", "brand", "rrp",
                 "product_image_url", "package_image_url")


def ensure_bucket() -> None:
    """Create the public photo bucket on first use (idempotent, best-effort)."""
    try:
        get_client().storage.create_bucket(_BUCKET, options={"public": True})
    except Exception:  # noqa: BLE001 — already exists / race → fine
        pass


def public_url(path: str) -> str:
    return f"{settings.supabase_url.rstrip('/')}/storage/v1/object/public/{_BUCKET}/{path}"


def _safe_code(code: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", (code or "").strip()) or "item"


def upload_image(code: str, kind: str, data: bytes, content_type: str, by: str = "") -> str:
    """Store a product/package photo and point the item at it. Returns the public URL."""
    ensure_bucket()
    kind = "package" if kind == "package" else "product"
    ext = {"image/png": ".png", "image/webp": ".webp"}.get(content_type, ".jpg")
    path = f"items/{_safe_code(code)}-{kind}-{int(time.time())}{ext}"
    get_client().storage.from_(_BUCKET).upload(
        path, data, {"content-type": content_type, "upsert": "false"})
    url = public_url(path)
    col = f"{kind}_image_url"
    get_client().table("catalog_items").update(
        {col: url, "updated_by": by, "updated_at": datetime.now(timezone.utc).isoformat()}
    ).eq("item_code", code).execute()
    return url


def list_catalog(include_inactive: bool = False) -> dict:
    """Full catalog grouped for the portal page (all price tiers — internal users)."""
    where = "" if include_inactive else "WHERE is_active"
    rows = exec_sql(
        "SELECT item_code, display_name, spec, category, brand, division, dealer_price, "
        "roadshow_price, rrp, standard_rate, product_image_url, package_image_url, "
        f"sort_order, is_active, updated_at FROM v_catalog {where} "
        "ORDER BY category, sort_order NULLS LAST, item_code"
    ) or []
    cats = sorted({r.get("category") or "OTHER" for r in rows},
                  key=lambda c: (CATEGORY_ORDER.index(c) if c in CATEGORY_ORDER else 99, c))
    return {"items": rows, "categories": cats, "count": len(rows)}


def upsert_item(payload: dict, by: str = "") -> dict:
    code = (payload.get("item_code") or "").strip().upper()
    if not code:
        raise ValueError("item_code is required")
    fields = {k: payload.get(k) for k in (
        "display_name", "spec", "category", "brand", "division", "dealer_price",
        "roadshow_price", "rrp", "sort_order", "is_active") if k in payload}
    fields.update(item_code=code, updated_by=by,
                  updated_at=datetime.now(timezone.utc).isoformat())
    get_client().table("catalog_items").upsert(fields, on_conflict="item_code").execute()
    return {"ok": True, "item_code": code}


def delete_item(code: str) -> dict:
    get_client().table("catalog_items").delete().eq("item_code", code).execute()
    return {"ok": True}


# ── public share link (customers see item + photo + RRP only) ─────────────────

def share_token(create: bool = True) -> str | None:
    """Stable random token in app_settings; the public catalog URL embeds it."""
    try:
        r = get_client().table("app_settings").select("value").eq(
            "key", "catalog_share_token").limit(1).execute().data
        if r:
            return r[0]["value"]
        if not create:
            return None
        tok = secrets.token_urlsafe(18)
        get_client().table("app_settings").upsert(
            {"key": "catalog_share_token", "value": tok,
             "description": "Public catalog share-link token (rotate to revoke old links)"},
            on_conflict="key").execute()
        return tok
    except Exception as e:  # noqa: BLE001
        log.warning("share_token failed: %s", e)
        return None


def rotate_share_token() -> str:
    tok = secrets.token_urlsafe(18)
    get_client().table("app_settings").upsert(
        {"key": "catalog_share_token", "value": tok,
         "description": "Public catalog share-link token (rotate to revoke old links)"},
        on_conflict="key").execute()
    return tok


def public_catalog(token: str) -> dict | None:
    """RRP-only view for customers. None = bad token."""
    good = share_token(create=False)
    if not good or not secrets.compare_digest(token, good):
        return None
    rows = exec_sql(
        "SELECT item_code, display_name, spec, category, brand, rrp, "
        "product_image_url, package_image_url FROM v_catalog "
        "WHERE is_active AND rrp IS NOT NULL "
        "ORDER BY category, sort_order NULLS LAST, item_code"
    ) or []
    cats = sorted({r.get("category") or "OTHER" for r in rows},
                  key=lambda c: (CATEGORY_ORDER.index(c) if c in CATEGORY_ORDER else 99, c))
    return {"items": rows, "categories": cats, "brand": "VFAN", "company": "YQ Bahrain"}


# ── branded .xlsx export (same shape as the sheet the owner shares today) ─────

_xlsx_cache: dict = {"sig": None, "bytes": None}


def export_xlsx() -> bytes:
    """Workbook with one sheet per category, embedded photos, all price tiers.
    Cached until any catalog row changes (images are re-downloaded on rebuild only)."""
    sig_row = (exec_sql("SELECT COUNT(*) AS n, MAX(updated_at) AS u FROM catalog_items") or [{}])[0]
    sig = f"{sig_row.get('n')}|{sig_row.get('u')}"
    if _xlsx_cache["sig"] == sig and _xlsx_cache["bytes"]:
        return _xlsx_cache["bytes"]

    import requests
    from openpyxl import Workbook
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    data = list_catalog()
    wb = Workbook()
    wb.remove(wb.active)
    head_fill = PatternFill("solid", fgColor="6D28D9")
    head_font = Font(color="FFFFFF", bold=True)
    headers = ["NO.", "PRODUCT PICTURE", "PACKAGE PICTURE", "CODE", "SPEC",
               "Dealer Cost", "CauseWay & Road Show", "RRP", "Standard Rate (BHD)"]
    widths = [5, 18, 18, 12, 46, 12, 18, 10, 16]
    sess = requests.Session()

    def _img(url: str | None) -> XLImage | None:
        if not url:
            return None
        try:
            r = sess.get(url, timeout=10)
            r.raise_for_status()
            im = XLImage(io.BytesIO(r.content))
            scale = 100 / max(im.height, 1)
            im.height, im.width = int(im.height * scale), int(im.width * scale)
            return im
        except Exception:  # noqa: BLE001
            return None

    for cat in data["categories"]:
        ws = wb.create_sheet(title=(cat or "OTHER")[:31])
        for j, (h, w) in enumerate(zip(headers, widths), start=1):
            c = ws.cell(row=1, column=j, value=h)
            c.fill, c.font = head_fill, head_font
            c.alignment = Alignment(horizontal="center", vertical="center")
            ws.column_dimensions[get_column_letter(j)].width = w
        ws.freeze_panes = "A2"
        r = 2
        for i, it in enumerate([x for x in data["items"] if (x.get("category") or "OTHER") == cat], 1):
            ws.row_dimensions[r].height = 80
            vals = [i, None, None, it.get("item_code"), it.get("spec") or it.get("display_name"),
                    it.get("dealer_price"), it.get("roadshow_price"), it.get("rrp"),
                    it.get("standard_rate")]
            for j, v in enumerate(vals, start=1):
                cell = ws.cell(row=r, column=j, value=v)
                cell.alignment = Alignment(vertical="center", wrap_text=(j == 5),
                                           horizontal="center" if j != 5 else "left")
            for col, key in ((2, "product_image_url"), (3, "package_image_url")):
                im = _img(it.get(key))
                if im:
                    ws.add_image(im, f"{get_column_letter(col)}{r}")
            r += 1

    buf = io.BytesIO()
    wb.save(buf)
    out = buf.getvalue()
    _xlsx_cache.update(sig=sig, bytes=out)
    return out


def sync_from_price_book() -> int:
    """Auto-grow the catalog after every ingest: any active MA_base price-book SKU not
    yet in catalog_items is added with its name/category — so a new item in the price
    list appears in the catalog automatically and the owner's ONLY manual step is the
    photo. Never touches existing rows (owner edits are preserved)."""
    candidates = exec_sql(
        "SELECT DISTINCT ON (sp.sku_code) sp.sku_code AS code, "
        "COALESCE(NULLIF(TRIM(sp.item_name), ''), sp.sku_code) AS name, "
        "COALESCE(p.item_name, sp.item_name) AS spec, "
        "UPPER(COALESCE(c.name, 'OTHER')) AS category "
        "FROM selling_prices sp "
        "LEFT JOIN products p ON p.sku_code = sp.sku_code "
        "LEFT JOIN categories c ON c.id = p.category_id "
        "WHERE sp.price_book = 'MA_base' AND COALESCE(sp.rate_bhd, 0) > 0 "
        "AND sp.sku_code IS NOT NULL AND TRIM(sp.sku_code) <> '' "
        "ORDER BY sp.sku_code, sp.imported_at DESC"
    ) or []
    # existing codes via the service client (RLS-proof) — only INSERT true newcomers
    existing: set[str] = set()
    off = 0
    while True:
        b = (get_client().table("catalog_items").select("item_code")
             .range(off, off + 999).order("item_code").execute().data or [])
        existing |= {r["item_code"] for r in b}
        if len(b) < 1000:
            break
        off += 1000
    items = []
    for r in candidates:
        code = str(r.get("code") or "").strip()
        if not code or code in existing:
            continue
        blob = f"{r.get('spec') or ''} {r.get('name') or ''}".lower()
        items.append({
            "item_code": code,
            "display_name": str(r.get("name") or code)[:120],
            "spec": r.get("spec"),
            "category": r.get("category"),
            "brand": "VFAN" if "vfan" in blob else None,
            "is_active": True,
            "updated_by": "price-book auto-sync",
        })
    n = bulk_upsert(items)
    if n:
        log.info("catalog auto-sync: %d new item(s) from the price book", n)
    return n


# Referenced by scripts/catalog_import.py so parsing lives in one place at import time too.
def bulk_upsert(items: list[dict]) -> int:
    if not items:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    for it in items:
        it.setdefault("updated_at", now)
    get_client().table("catalog_items").upsert(items, on_conflict="item_code").execute()
    return len(items)


def item_exists(code: str) -> bool:
    r = exec_sql_params("SELECT 1 AS x FROM catalog_items WHERE item_code = $1 LIMIT 1", [code])
    return bool(r)
