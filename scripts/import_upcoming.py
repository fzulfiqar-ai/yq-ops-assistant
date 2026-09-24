"""Build the WEKOME "Coming soon" cards from the shipment invoices — dry-run by default.

    python -m scripts.import_upcoming                      # dry run: review sheet + thumbs, no DB, no upload
    python -m scripts.import_upcoming --month 2026-10      # the arrival month the cards announce
    python -m scripts.import_upcoming --commit --yes       # upload photos + insert DRAFT rows (release step)

What it does (trust plan §6b, release R1b):
  1. reads shipment 1 (AS2026072701, the newest …1st(3).xlsx copy) and shipment 2 (…2nd(4).xlsx)
     through scripts/wekome_source.py and merges them by model code;
  2. asserts the no-duplicate rules: exactly 34 unique models, a product AND a box photo for
     every model, and 0 photo hashes shared between any two cards (WDC-C34a's box comes from the
     WK price list because the invoice cell points at the C34i box);
  3. builds the rows for shop_upcoming_items — brand, code, category, names and specs in EN/AR,
     variant chips, shipment ref, expected month — and refuses to build a row that carries any
     price, cost or quantity key (app.upcoming.assert_no_money);
  4. writes the OWNER REVIEW SHEET, a self-contained HTML page (cleaned photo, box photo, code,
     category, name EN/AR, spec, variant chips, expected label; every Arabic line flagged for
     native review), plus the cleaned photos and 160/320/512 WebP thumbs, under
     business_data/upcoming_review/<date>/ (gitignored: the folder is checked);
  5. with --commit --yes: uploads the photos to the PUBLIC catalog bucket under upcoming/ and
     inserts the rows as status 'draft' (existing codes keep their status, month and sort; copy
     and photos are refreshed). Object names carry a content hash ({code}-product-{sha1[:8]}.jpg),
     so a corrected photo is a NEW URL that reaches phones whose service worker cached the old one.
     Publishing is an admin action in the portal (Coming soon page), after the owner's review.

What is public when: the CARDS (names, specs, chips, month) reach the marketplace only once
published in the portal. The PHOTOS are public storage objects from --commit onwards — anyone
holding a storage URL can fetch one, though nothing links to them until the card is published.
No label text is written: the arrival label always derives from expected_month (app/upcoming.py).

Never reads Wekome_Shipments_Cost_Profit_Review_240926.xlsx; the invoice prices and quantities
the parser sees are dropped before anything is written. YQ_WEKOME_DIR points at the shipments
folder when the checkout has no Wekome/ of its own (a worktree).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

from app.catalog import THUMB_SIZES, make_thumb  # noqa: E402  — pure PIL, no DB
from app.upcoming import BRAND_DEFAULT, assert_no_money, expected_labels, parse_month  # noqa: E402
from scripts import wekome_source as wk  # noqa: E402

EXPECTED_MODELS = 34
PHOTO_PX = 1024                    # the cleaned master photo (the marketplace reads the WebP thumbs)
STORAGE_PREFIX = "upcoming"        # inside the public 'catalog' bucket
OUT_ROOT = ROOT / "business_data" / "upcoming_review"
DEFAULT_MONTH = "2026-10"          # "Arriving October" — the owner can change it per item in admin

# ── Arabic copy (translate generic words only; codes, wattage, Bluetooth versions, mAh, connector
#    names and sizes stay Latin). Every line is flagged for native review on the sheet. ──────────
CATEGORY_AR = {
    "Wireless Audio": "صوتيات لاسلكية",
    "Wired Earphones": "سماعات سلكية",
    "Speakers": "مكبرات صوت",
    "Data Cables": "كيابل شحن ونقل بيانات",
    "Wall Chargers": "شواحن حائط",
    "Car Chargers": "شواحن سيارة",
    "Screen Protectors": "واقيات شاشة",
}
COLOUR_AR = {"White": "أبيض", "Black": "أسود", "Blue": "أزرق", "Beige": "بيج", "Grey": "رمادي", "Gray": "رمادي",
             "Yellow": "أصفر", "Silver": "فضي", "Tarnish": "رمادي داكن"}
PLUG_AR = {"3.5 mm jack": "مقبس 3.5 mm", "USB-C plug": "قابس USB-C", "USB-C plug (digital)": "قابس USB-C (رقمي)",
           "UK plug": "قابس بريطاني"}
_CABLE_SPEC_AR = {
    "Braided + zinc alloy": "مضفّر + سبيكة زنك",
    "TPE": "TPE",
    "PVC": "PVC",
    "Nylon braided + aluminium alloy": "نايلون مضفّر + سبيكة ألمنيوم",
    "ABS + braided nylon": "ABS + نايلون مضفّر",
    "Braided + TPE + aluminium alloy": "مضفّر + TPE + سبيكة ألمنيوم",
}
# code → (name_ar, spec_ar); the EN side is WK_CATALOG (never invented).
WK_ARABIC: dict[str, tuple[str, str]] = {
    "WG-08":    ("سماعات لاسلكية برباط رقبة", "Bluetooth 6.0 · بطارية 200 mAh · تشغيل حتى 25 ساعة"),
    "WS-33":    ("سماعات أذن لاسلكية TWS", "Bluetooth 6.0 · مشغّل صوت 13 mm · نحو 4 ساعات لكل شحنة"),
    "WS-31":    ("سماعات أذن لاسلكية TWS (الجيل 3)", "Bluetooth 5.3 · مشغّل صوت 13 mm · 4–5 ساعات لكل شحنة"),
    "WS-55":    ("سماعات أذن لاسلكية TWS من WK Design (الجيل 5)", "Bluetooth 5.3 · علبة شحن 230 mAh · نحو 4 ساعات لكل شحنة"),
    "WS-56":    ("سماعات أذن لاسلكية TWS من WK Design، إصدار الشباب (الجيل 6)", "Bluetooth 5.3 · علبة شحن 250 mAh · نحو 4 ساعات لكل شحنة"),
    "WS-57":    ("سماعات أذن لاسلكية TWS من WK Design (الجيل 7)", "Bluetooth 5.3 · علبة شحن 250 mAh · نحو 4 ساعات لكل شحنة"),
    "WS-58":    ("سماعات أذن لاسلكية TWS من WK Design (الجيل 8)", "Bluetooth 5.3 · علبة شحن 240 mAh · نحو 4 ساعات لكل شحنة"),
    "YB13":     ("سماعات سلكية", "مشغّل صوت 10 mm · كيبل 1.2 m"),
    "YB03":     ("سماعات سلكية سلسلة Kingkong", "مشغّل صوت 14 mm · كيبل 1.2 m"),
    "YB05":     ("سماعات سلكية سلسلة Kingkong", "مشغّل صوت 14 mm · كيبل 1.2 m"),
    "WD-05":    ("مكبر صوت للنوم مع ضوضاء بيضاء مدمجة", "Bluetooth 5.3 · 5 W · بطارية 1200 mAh · تشغيل 6 ساعات فأكثر"),
    "WDC-C37":  ("كيبل شحن سريع مع حامل 240W (USB-C إلى USB-C)", _CABLE_SPEC_AR["Braided + zinc alloy"]),
    "WDC-C38":  ("كيبل شحن سريع مع حامل 27W (USB-C إلى Lightning)", _CABLE_SPEC_AR["Braided + zinc alloy"]),
    "WDC-C34a": ("كيبل شحن سريع ونقل بيانات 66W (USB-A إلى USB-C)", "TPE"),
    "WDC-C34i": ("كيبل شحن سريع ونقل بيانات 12W (USB-A إلى Lightning)", "TPE"),
    "WDC-C34m": ("كيبل شحن سريع ونقل بيانات 12W (USB-A إلى Micro-USB)", "TPE"),
    "WDC-C35":  ("كيبل شحن سريع ونقل بيانات PD 65W (USB-C إلى USB-C)", "TPE"),
    "WDC-C31":  ("كيبل شحن سريع ونقل بيانات 66W (USB-A إلى USB-C)", _CABLE_SPEC_AR["Nylon braided + aluminium alloy"]),
    "WDC-C32":  ("كيبل شحن سريع ونقل بيانات 65W (USB-C إلى USB-C)", _CABLE_SPEC_AR["Nylon braided + aluminium alloy"]),
    "WDC-C33":  ("كيبل شحن سريع ونقل بيانات 35W (USB-C إلى Lightning)", _CABLE_SPEC_AR["Nylon braided + aluminium alloy"]),
    "WDC-67":   ("كيبل بيانات Kingkong من PVC بقدرة 66W (USB-A إلى USB-C)", "PVC"),
    "WDC-68":   ("كيبل بيانات Kingkong من PVC بقدرة 65W (USB-C إلى USB-C)", "PVC"),
    "WDC-21":   ("كيبل شحن فائق السرعة ونقل بيانات", _CABLE_SPEC_AR["ABS + braided nylon"]),
    "WDC-97":   ("كيبل شحن سريع مضفّر Kingkong بتيار 6A (USB-A إلى USB-C)", _CABLE_SPEC_AR["Braided + TPE + aluminium alloy"]),
    "WDC-98":   ("كيبل شحن سريع مضفّر Kingkong بقدرة 65W (USB-C إلى USB-C)", _CABLE_SPEC_AR["Braided + TPE + aluminium alloy"]),
    "WDC-50":   ("كيبل شحن فائق السرعة مضفّر 4 في 1 بقدرة 240W", "USB-A / USB-C إلى USB-C / Lightning · سبيكة زنك"),
    "WDC-51":   ("كيبل شحن فائق السرعة سيليكون 4 في 1 بقدرة 240W", "USB-A / USB-C إلى USB-C / Lightning · سيليكون سائل"),
    "WP-U100":  ("شاحن سريع PD 20W (قابس بريطاني)", "منفذ USB-C واحد · حتى PD 20W"),
    "WP-U58":   ("شاحن سلسلة Flash Charge بقدرة 12W (قابس بريطاني)", "منفذا USB-A · حتى 12W"),
    "WP-C56":   ("شاحن سيارة شفاف 15W", "منفذا USB-A · سبيكة ألمنيوم + PC"),
    "WP-C53":   ("شاحن سيارة شفاف 48W", "USB-A 18W + USB-C PD 30W"),
    "WP-C52":   ("شاحن سيارة سريع مع مشغّل MP3 لاسلكي", "USB-A QC3.0 18W + USB-C 20W · مشغّل MP3"),
    "WTP-137":  ("واقي شاشة منحني 6D مضاد للغبار (HD)", "زجاج مقوّى 0.4 mm · 10 قطع في العلبة"),
    "WTP-138":  ("واقي شاشة منحني 6D مضاد للغبار (خصوصية)", "زجاج مقوّى 0.4 mm · 10 قطع في العلبة"),
}


def variant_label_ar(comps: dict[str, str], keys: list[str]) -> str:
    """The Arabic chip: colours and plug words translated, everything technical left Latin."""
    parts = []
    for k in keys:
        v = comps.get(k)
        if not v:
            continue
        if k == "colour":
            parts.append(COLOUR_AR.get(v, v))
        elif k == "plug":
            parts.append(PLUG_AR.get(v, v))
        elif k == "type" and v == "4-in-1":
            parts.append("4 في 1")
        elif k == "connector":
            parts.append(v.replace(" to ", " إلى "))
        else:
            parts.append(v)
    return " · ".join(parts)


# ── source ─────────────────────────────────────────────────────────────────────

def load_products() -> tuple[list[dict], list[dict]]:
    """(products, ships) — both shipments merged by model, the C34a box override applied."""
    for p in (wk.WK_SHIP1, wk.WK_SHIP2):
        if not p.exists():
            raise SystemExit(f"shipment workbook not found: {p}\n(set YQ_WEKOME_DIR to the 'Wekome Shipments' folder)")
    ships = [wk.read_invoice(wk.WK_SHIP1, 1), wk.read_invoice(wk.WK_SHIP2, 2)]
    products = wk.build_wekome_products(ships)
    for p in products:
        if p["code"] in wk.WK_BOX_OVERRIDE:
            p["box"] = wk.price_list_picture(*wk.WK_BOX_OVERRIDE[p["code"]]) or p["box"]
    return products, ships


def photo_hashes(products: list[dict]) -> dict[str, list[tuple[str, str]]]:
    """sha1 of every raw picture → the (code, kind) pairs that carry it."""
    seen: dict[str, list[tuple[str, str]]] = {}
    for p in products:
        for kind in ("pic", "box"):
            if p.get(kind):
                seen.setdefault(hashlib.sha1(p[kind]).hexdigest(), []).append((p["code"], "product" if kind == "pic" else "box"))
    return seen


def check_invariants(products: list[dict]) -> dict:
    """The no-duplicate rules from the plan; raises on the first breach."""
    codes = [p["code"] for p in products]
    if len(codes) != len(set(codes)):
        raise SystemExit(f"duplicate model codes: {sorted({c for c in codes if codes.count(c) > 1})}")
    if len(codes) != EXPECTED_MODELS:
        raise SystemExit(f"expected {EXPECTED_MODELS} unique models, found {len(codes)}: {codes}")
    missing = [p["code"] for p in products if not p.get("pic") or not p.get("box")]
    if missing:
        raise SystemExit(f"models without a product + box photo: {missing}")
    shared = {h: owners for h, owners in photo_hashes(products).items() if len(owners) > 1}
    if shared:
        raise SystemExit("photos shared between cards: " + "; ".join(", ".join(f"{c}/{k}" for c, k in o) for o in shared.values()))
    return {"models": len(codes), "photos": sum(1 for p in products for k in ("pic", "box") if p.get(k)), "shared": 0}


# ── rows ───────────────────────────────────────────────────────────────────────

def build_rows(products: list[dict], ships: list[dict], month: str | None = DEFAULT_MONTH) -> list[dict]:
    """shop_upcoming_items rows (no photos yet, no money, no quantities — asserted)."""
    invoices: dict[str, list[str]] = {}
    for s in ships:
        for ln in s["lines"]:
            invoices.setdefault(ln["code"], [])
            if s["invoice"] not in invoices[ln["code"]]:
                invoices[ln["code"]].append(s["invoice"])
    m = parse_month(month) if month else None
    ordered = sorted(products, key=lambda p: (wk.WK_CATEGORY_ORDER.index(p["category"]), p["code"]))
    rows = []
    for i, p in enumerate(ordered, 1):
        name_ar, spec_ar = WK_ARABIC[p["code"]]
        spec_en = p["spec"] + (f" · {p['common']}" if p.get("common") else "")
        if p.get("common"):
            spec_ar = f"{spec_ar} · {p['common']}"
        keys = [k for k in wk.VARIANT_ORDER if any(k in v["comps"] for v in p["variants"])]
        varying = [k for k in keys if len({v["comps"].get(k) for v in p["variants"]}) > 1]
        show = [k for k in keys if k in varying or (k == "colour" and p["category"] != "Screen Protectors")]
        variants = []
        for v in p["variants"]:
            if not v["label"]:
                continue
            variants.append({"label": v["label"], "label_ar": variant_label_ar(v["comps"], show), "comps": dict(v["comps"])})
        rows.append({
            "brand": BRAND_DEFAULT, "model_code": p["code"], "category": p["category"],
            "category_ar": CATEGORY_AR[p["category"]],
            "name_en": p["name"], "name_ar": name_ar, "spec_en": spec_en, "spec_ar": spec_ar,
            "variants": variants, "shipment_ref": " · ".join(invoices.get(p["code"], [])),
            "expected_month": m.isoformat() if m else None,
            # never a literal label: the marketplace derives "Arriving October" from the month, so a
            # later month change in the portal can never leave stale wording behind
            "expected_label_en": None, "expected_label_ar": None,
            "status": "draft", "sort_order": i,
        })
    assert_no_money(rows, "rows")
    return rows


def row_labels(row: dict) -> tuple[str, str]:
    """The (en, ar) label a row will show — for the review sheet and the console, the same rule
    the marketplace applies (app.upcoming.expected_labels)."""
    return expected_labels(row.get("expected_month"), row.get("expected_label_en"), row.get("expected_label_ar"))


def render_images(products: list[dict]) -> dict[str, dict]:
    """code → cleaned JPEG masters + the WebP size set app/catalog.py serves for catalog photos."""
    out = {}
    for p in products:
        pic = wk.clean_photo(p["pic"], PHOTO_PX, wk.WK_PHOTO_FRAME)
        box = wk.clean_photo(p["box"], PHOTO_PX, wk.WK_PHOTO_FRAME)
        out[p["code"]] = {
            "product": pic, "box": box,
            "thumbs": {s: make_thumb(pic, s, "WEBP") for s in THUMB_SIZES},
            "box_thumbs": {s: make_thumb(box, s, "WEBP") for s in THUMB_SIZES},
        }
    return out


# ── the owner review sheet ────────────────────────────────────────────────────

_CSS = """
:root{--plum:#6D4091;--soft:#F0EAF6;--ink:#191424;--muted:#6B6478;--rule:#E6E0EC;--warn:#904F0E;--warn-bg:#FBF0E1}
*{box-sizing:border-box}body{margin:0;padding:24px;font:14px/1.45 'Segoe UI',Arial,sans-serif;color:var(--ink);background:#faf8fc}
h1{font-size:24px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px;color:var(--plum)}.lede{color:var(--muted);margin:0 0 16px}
.kpis{display:flex;gap:18px;flex-wrap:wrap;margin:8px 0 12px}.kpi{background:#fff;border:1px solid var(--rule);border-radius:10px;padding:8px 14px}
.kpi b{display:block;font-size:20px}.kpi span{font-size:12px;color:var(--muted)}
.card{display:grid;grid-template-columns:200px 200px 1fr;gap:16px;background:#fff;border:1px solid var(--rule);border-radius:12px;padding:14px;margin:10px 0;break-inside:avoid}
.pic{width:200px;height:200px;border:1px solid var(--rule);border-radius:8px;background:#fff;display:flex;align-items:center;justify-content:center;overflow:hidden}
.pic img{max-width:100%;max-height:100%}.cap{font-size:11px;color:var(--muted);text-align:center;margin-top:4px;text-transform:uppercase;letter-spacing:.08em}
.code{font-weight:700;font-size:18px}.cat{font-size:12px;color:var(--muted)}.name{font-weight:600;margin-top:6px}.spec{color:#4a4457}
.ar{direction:rtl;text-align:right;font-family:'Segoe UI',Tahoma,Arial,sans-serif;background:var(--soft);padding:6px 10px;border-radius:8px;margin-top:6px}
.flag{display:inline-block;font-size:10.5px;font-weight:700;color:var(--warn);background:var(--warn-bg);padding:2px 7px;border-radius:9px;margin-left:8px;direction:ltr}
.chips{margin-top:8px}.chip{display:inline-block;font-size:12px;background:var(--soft);color:var(--plum);border:1px solid #d9cbe6;padding:2px 9px;border-radius:99px;margin:2px 4px 2px 0}
.chip.ar{display:inline-block;margin:2px 0 2px 4px;padding:2px 9px}.meta{font-size:12px;color:var(--muted);margin-top:8px}.meta b{color:var(--ink)}
.hint{font-size:12px;color:var(--muted);background:#fff;border:1px dashed var(--rule);border-radius:8px;padding:10px 12px;margin:0 0 12px}
@media print{body{padding:8mm}.card{page-break-inside:avoid}}
"""


def _data_uri(raw: bytes, mime: str = "image/jpeg") -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode()}"


def review_sheet(rows: list[dict], images: dict[str, dict], out_dir: Path, stats: dict) -> Path:
    e = html.escape
    by_cat: dict[str, list[dict]] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)
    flag = "<span class='flag'>Arabic · needs native review</span>"
    first_en, first_ar = row_labels(rows[0])
    parts = [
        "<h1>WEKOME · Coming soon · owner review</h1>",
        f"<p class='lede'>{stats['models']} models from invoice AS2026072701 (shipments 1 + 2), merged by model code. "
        f"Generated {date.today().isoformat()}. The cards on this sheet reach the marketplace only when published in the portal; "
        "the photos become public storage files at the --commit step (fetchable by anyone holding the storage URL, linked from nowhere until published).</p>",
        "<div class='kpis'>"
        f"<div class='kpi'><b>{stats['models']}</b><span>models</span></div>"
        f"<div class='kpi'><b>{stats['photos']}</b><span>photos (product + box)</span></div>"
        f"<div class='kpi'><b>{stats['shared']}</b><span>photos shared between cards</span></div>"
        f"<div class='kpi'><b>{e(first_en)}</b><span>{e(first_ar)}</span></div>"
        "</div>",
        "<p class='hint'>Check per card: the photo shows the right product and box, the name and spec match the WK price list, "
        "the variant chips are the colours/connectors/sizes you will stock, and the Arabic reads naturally. "
        "No prices or quantities appear here or anywhere on the marketplace: the card says “Price on arrival”.</p>",
    ]
    for cat in wk.WK_CATEGORY_ORDER:
        items = by_cat.get(cat) or []
        if not items:
            continue
        parts.append(f"<h2>{e(cat)} <span class='cat'>· {e(CATEGORY_AR[cat])}</span> · {len(items)}</h2>")
        for r in items:
            im = images[r["model_code"]]
            lab_en, lab_ar = row_labels(r)
            chips_en = "".join(f"<span class='chip'>{e(v['label'])}</span>" for v in r["variants"]) or "<span class='cat'>single variant</span>"
            chips_ar = "".join(f"<span class='chip ar'>{e(v['label_ar'])}</span>" for v in r["variants"] if v.get("label_ar"))
            parts.append(
                "<div class='card'>"
                f"<div><div class='pic'><img src='{_data_uri(im['thumbs'][320], 'image/webp')}' alt=''></div><div class='cap'>product</div></div>"
                f"<div><div class='pic'><img src='{_data_uri(im['box_thumbs'][320], 'image/webp')}' alt=''></div><div class='cap'>box</div></div>"
                "<div>"
                f"<div class='code'>{e(r['model_code'])}</div><div class='cat'>{e(r['category'])} · {e(r['category_ar'])}</div>"
                f"<div class='name'>{e(r['name_en'])}</div><div class='spec'>{e(r['spec_en'])}</div>"
                f"<div class='ar'><div><b>{e(r['name_ar'])}</b>{flag}</div><div>{e(r['spec_ar'])}</div></div>"
                f"<div class='chips'>{chips_en}</div>"
                + (f"<div class='chips ar'>{chips_ar}</div>" if chips_ar else "")
                + f"<div class='meta'>Expected: <b>{e(lab_en)}</b> · {e(lab_ar)} "
                  f"· shipment {e(r['shipment_ref'])} · status <b>{e(r['status'])}</b> · price label: <b>Price on arrival</b></div>"
                "</div></div>")
    doc = ("<!doctype html><html lang='en'><head><meta charset='utf-8'><title>WEKOME coming soon · owner review</title>"
           f"<style>{_CSS}</style></head><body>{''.join(parts)}</body></html>")
    out = out_dir / "wekome_coming_soon_review.html"
    out.write_text(doc, encoding="utf-8")
    return out


def write_assets(rows: list[dict], images: dict[str, dict], out_dir: Path) -> None:
    (out_dir / "photos").mkdir(parents=True, exist_ok=True)
    (out_dir / "thumbs").mkdir(parents=True, exist_ok=True)
    for code, im in images.items():
        (out_dir / "photos" / f"{code}-product.jpg").write_bytes(im["product"])
        (out_dir / "photos" / f"{code}-box.jpg").write_bytes(im["box"])
        for s, blob in im["thumbs"].items():
            if blob:
                (out_dir / "thumbs" / f"{code}-product-{s}.webp").write_bytes(blob)
        for s, blob in im["box_thumbs"].items():
            if blob:
                (out_dir / "thumbs" / f"{code}-box-{s}.webp").write_bytes(blob)
    (out_dir / "rows.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def _ignored_by_git(path: Path) -> bool | None:
    try:
        r = subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=ROOT, capture_output=True)
        return r.returncode == 0
    except OSError:
        return None


# ── commit (release step; never run from a dev session) ───────────────────────

def content_tag(raw: bytes) -> str:
    """The 8-hex content hash in every object name: a changed photo is a new object (and URL), so
    the one-year cache-control and the service worker's CacheFirst never pin an old picture."""
    return hashlib.sha1(raw).hexdigest()[:8]


def object_paths(code: str, im: dict) -> dict:
    """Where one model's files go inside the catalog bucket: masters and the WebP size sets, each
    name carrying its master's content hash."""
    tag_p, tag_b = content_tag(im["product"]), content_tag(im["box"])
    return {
        "product": f"{STORAGE_PREFIX}/{code}-product-{tag_p}.jpg",
        "box": f"{STORAGE_PREFIX}/{code}-box-{tag_b}.jpg",
        "thumbs": {s: f"{STORAGE_PREFIX}/thumbs/{code}-product-{tag_p}-{s}.webp" for s, blob in im["thumbs"].items() if blob},
        "box_thumbs": {s: f"{STORAGE_PREFIX}/thumbs/{code}-box-{tag_b}-{s}.webp" for s, blob in im["box_thumbs"].items() if blob},
    }


def commit(rows: list[dict], images: dict[str, dict]) -> None:
    from app.catalog import THUMB_CACHE, _BUCKET, ensure_bucket, public_url
    from app.database import get_client
    from app.upcoming import TABLE, invalidate
    ensure_bucket()
    bucket = get_client().storage.from_(_BUCKET)
    for r in rows:
        code = r["model_code"]
        im = images[code]
        paths = object_paths(code, im)
        for kind in ("product", "box"):
            bucket.upload(paths[kind], im[kind], {"content-type": "image/jpeg", "upsert": "true", "cache-control": THUMB_CACHE})
        thumbs, box_thumbs = {}, {}
        for s, path in paths["thumbs"].items():
            bucket.upload(path, im["thumbs"][s], {"content-type": "image/webp", "upsert": "true", "cache-control": THUMB_CACHE})
            thumbs[str(s)] = public_url(path)
        for s, path in paths["box_thumbs"].items():
            bucket.upload(path, im["box_thumbs"][s], {"content-type": "image/webp", "upsert": "true", "cache-control": THUMB_CACHE})
            box_thumbs[str(s)] = public_url(path)
        r["photo_url"] = public_url(paths["product"])
        r["box_url"] = public_url(paths["box"])
        r["photo_thumb_urls"] = thumbs
        r["box_thumb_urls"] = box_thumbs
        print(f"  uploaded {code}")
    client = get_client()
    existing = {(x["brand"], x["model_code"]): x for x in
                (client.table(TABLE).select("id,brand,model_code,status").execute().data or [])}
    inserted = updated = 0
    for r in rows:
        rec = {k: v for k, v in r.items() if k != "category_ar"}
        assert_no_money(rec, rec["model_code"])
        cur = existing.get((rec["brand"], rec["model_code"]))
        if cur:
            # keep the owner's status / month / sort; refresh copy, chips and photos
            for k in ("status", "expected_month", "expected_label_en", "expected_label_ar", "sort_order"):
                rec.pop(k, None)
            client.table(TABLE).update(rec).eq("id", cur["id"]).execute()
            updated += 1
        else:
            rec["created_by"] = "scripts.import_upcoming"
            client.table(TABLE).insert(rec).execute()
            inserted += 1
    invalidate()
    print(f"  rows: {inserted} inserted as draft, {updated} refreshed (status kept)")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--month", default=DEFAULT_MONTH, help="expected arrival month, YYYY-MM (default %(default)s)")
    ap.add_argument("--out", default=None, help="review folder (default business_data/upcoming_review/<today>)")
    ap.add_argument("--commit", action="store_true", help="upload photos and insert DRAFT rows (needs --yes)")
    ap.add_argument("--yes", action="store_true", help="confirm --commit against production")
    args = ap.parse_args(argv)

    print("WEKOME: reading shipment invoices …")
    products, ships = load_products()
    stats = check_invariants(products)
    rows = build_rows(products, ships, args.month)
    print(f"  {stats['models']} models · {stats['photos']} photos · 0 shared · rows carry no price/qty keys")

    print("WEKOME: cleaning photos + WebP thumbs …")
    images = render_images(products)

    out_dir = Path(args.out) if args.out else OUT_ROOT / date.today().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    ignored = _ignored_by_git(out_dir)
    if ignored is False:
        print(f"  ! {out_dir} is NOT gitignored — do not commit it (business_data/ should be ignored)")
    write_assets(rows, images, out_dir)
    sheet = review_sheet(rows, images, out_dir, stats)
    print(f"  wrote {sheet.relative_to(ROOT)}  ({sheet.stat().st_size / 1024:,.0f} KB) + photos/ thumbs/ rows.json")

    if not args.commit:
        print("  dry run — nothing uploaded, nothing written to the database. Re-run with --commit --yes after the owner's review.")
        return 0
    if not args.yes:
        print("  --commit needs --yes: this uploads the photos to the PUBLIC catalog bucket (fetchable by URL from then on)"
              " and inserts draft rows in production (the cards stay unpublished).")
        return 2
    print("WEKOME: committing (upload + draft rows) …")
    commit(rows, images)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
