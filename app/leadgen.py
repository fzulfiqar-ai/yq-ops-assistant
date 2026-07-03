"""LATER — free-first B2B lead generation (Three-Tiered Brain · Tier 2). Advise-only.

Discovers candidate retailers/shops in Bahrain from OpenStreetMap (Overpass — free, no key),
dedupes against our existing customers, scores fit against our product focus, and feeds a
human-actioned pipeline. The team calls/visits; the agent only drafts the list.

COMPLIANCE: Overpass is ODbL (attribute OSM where leads are shown). Optional Google Places
enrichment restricts caching → store only minimal business fields. Bahrain PDPL: B2B business
contact info only, no personal data.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

from app.database import get_client

log = logging.getLogger(__name__)

# Overpass mirrors (tried in order). OSM policy requires a descriptive User-Agent.
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
_HEADERS = {"User-Agent": "YQ-Bahrain-Ops/1.0 (B2B lead discovery; contact furqanahmed223@gmail.com)"}
# Shop types that buy/resell mobile accessories — direct resellers, wholesalers, AND modern trade.
SHOP_TYPES = (
    "mobile_phone", "electronics", "computer", "telecommunication",        # direct accessory resellers
    "wholesale", "trade",                                                  # B2B resellers / cash & carry
    "department_store", "supermarket", "mall", "general", "variety_store",  # modern trade
    "convenience", "kiosk",                                                # small B2C resellers
)
# Known modern-trade / retail chains in Bahrain & the GCC (case-insensitive name match → brand).
MT_CHAINS = {
    "lulu": "Lulu Hypermarket", "ansar": "Ansar Gallery", "sharaf": "Sharaf DG",
    "carrefour": "Carrefour", "mega mart": "Mega Mart", "megamart": "Mega Mart",
    "geant": "Geant", "ramez": "Ramez", "nesto": "Nesto", "lals": "Lals",
    "home centre": "Home Centre", "emax": "Emax", "jarir": "Jarir",
    "x-cite": "X-cite", "xcite": "X-cite", "al jazira": "Al Jazira Supermarket",
    "midway": "Midway Supermarket", "city centre": "City Centre", "the avenues": "The Avenues",
    "dragon": "Dragon City", "seef": "Seef Mall",
}
# Segment → base fit. Wholesalers & mobile/electronics resellers are the fastest B2B wins; modern
# trade is high-value but a longer, strategic sale; general retail is smaller B2C reselling.
_SEG_BASE = {"wholesale": 62, "mobile": 60, "modern_trade": 58, "electronics": 55, "general": 38}
ATTRIBUTION = "Lead data © OpenStreetMap contributors (ODbL)."


def classify(tags: dict, name: str) -> tuple[str, str | None]:
    """Return (segment, brand). segment ∈ modern_trade|wholesale|mobile|electronics|general."""
    low = (name or "").lower()
    brand = next((b for kw, b in MT_CHAINS.items() if kw in low), None)
    shop = (tags.get("shop") or "").lower()
    amenity = (tags.get("amenity") or "").lower()
    if shop in ("supermarket", "department_store", "mall", "general") or amenity == "marketplace" or brand:
        return "modern_trade", brand
    if shop in ("wholesale", "trade") or any(w in low for w in
                                             ("wholesale", "cash & carry", "trading", "distribution", "import")):
        return "wholesale", brand
    if shop in ("mobile_phone", "telecommunication"):
        return "mobile", brand
    if shop in ("electronics", "computer"):
        return "electronics", brand
    return "general", brand


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize(s: object) -> str:
    return "".join(ch for ch in str(s or "").lower() if ch.isalnum())


def _overpass_query(shop_types: tuple[str, ...]) -> str:
    pat = "|".join(shop_types)
    chain = "|".join(MT_CHAINS.keys())
    # Shop-type matches + named modern-trade chains (any shop) + marketplaces/souqs.
    return (
        "[out:json][timeout:40];"
        'area["ISO3166-1"="BH"][admin_level=2]->.bh;'
        "("
        f'node["shop"~"^({pat})$"](area.bh);'
        f'way["shop"~"^({pat})$"](area.bh);'
        f'node["shop"]["name"~"{chain}",i](area.bh);'
        f'way["shop"]["name"~"{chain}",i](area.bh);'
        'node["amenity"="marketplace"](area.bh);'
        'way["amenity"="marketplace"](area.bh);'
        ");"
        "out center tags 400;"
    )


def discover_overpass(shop_types: tuple[str, ...] = SHOP_TYPES) -> list[dict]:
    """Query Overpass for candidate shops in Bahrain. Tries mirrors in order; [] on total failure."""
    query = _overpass_query(shop_types)
    elements: list[dict] = []
    for url in OVERPASS_URLS:
        try:
            r = requests.post(url, data={"data": query}, headers=_HEADERS, timeout=45)
            if r.ok:
                elements = r.json().get("elements", [])
                break
            log.warning("overpass %s %s: %s", url, r.status_code, r.text[:120])
        except Exception as e:  # noqa: BLE001
            log.warning("overpass %s failed: %s", url, e)
    if not elements:
        return []
    out: list[dict] = []
    for el in elements:
        t = el.get("tags", {})
        name = (t.get("name") or t.get("name:en") or "").strip()
        if not name:
            continue
        seg, brand = classify(t, name)
        out.append({
            "name": name,
            "category": t.get("shop") or t.get("amenity"),
            "segment": seg,
            "brand": brand,
            "area": t.get("addr:city") or t.get("addr:suburb") or t.get("addr:district") or t.get("addr:place"),
            "phone": t.get("phone") or t.get("contact:phone"),
            "website": t.get("website") or t.get("contact:website"),
            "lat": el.get("lat") or (el.get("center") or {}).get("lat"),
            "lon": el.get("lon") or (el.get("center") or {}).get("lon"),
            "source": "overpass",
            "source_ref": f"{el.get('type')}/{el.get('id')}",
        })
    return out


def discover_apify(search_terms: tuple[str, ...] = ("mobile accessories shop", "mobile phone shop"),
                   max_per_search: int = 40) -> list[dict]:
    """OPTIONAL Google-Maps discovery via Apify (compass/crawler-google-places actor) —
    unlike OSM it returns phone/website/ADDRESS for most places. Needs APIFY_TOKEN;
    [] when unset or on failure. Synchronous actor run (keep max_per_search modest)."""
    import os
    token = os.getenv("APIFY_TOKEN") or os.getenv("APIFY_API_TOKEN") or ""
    if not token:
        return []
    url = ("https://api.apify.com/v2/acts/compass~crawler-google-places/"
           f"run-sync-get-dataset-items?token={token}")
    payload = {
        "searchStringsArray": list(search_terms),
        "locationQuery": "Bahrain",
        "maxCrawledPlacesPerSearch": max(1, min(max_per_search, 60)),
        "language": "en",
        "skipClosedPlaces": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=290)
        r.raise_for_status()
        places = r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("apify discovery failed: %s", str(e)[:160])
        return []
    out: list[dict] = []
    for p in places if isinstance(places, list) else []:
        name = (p.get("title") or "").strip()
        if not name:
            continue
        seg, brand = classify({}, name)
        out.append({
            "name": name,
            "category": (p.get("categoryName") or "shop").lower(),
            "segment": seg,
            "brand": brand,
            "area": p.get("neighborhood") or p.get("city"),
            "phone": p.get("phone") or p.get("phoneUnformatted"),
            "website": p.get("website"),
            "address": p.get("address"),
            "lat": (p.get("location") or {}).get("lat"),
            "lon": (p.get("location") or {}).get("lng"),
            "source": "apify_gmaps",
            "source_ref": p.get("placeId") or name,
        })
    return out


def _existing_customer_names() -> set[str]:
    try:
        rows = get_client().table("v_top_customers").select("customer_name").limit(3000).execute().data or []
        return {_normalize(r.get("customer_name")) for r in rows if r.get("customer_name")}
    except Exception:  # noqa: BLE001
        return set()


def score_fit(lead: dict) -> int:
    """0-100 fit: segment value + recognised-chain boost + contactability + locatability."""
    s = _SEG_BASE.get(lead.get("segment", "general"), 38)
    if lead.get("brand"):
        s += 10
    if lead.get("phone"):
        s += 12
    if lead.get("website"):
        s += 8
    if lead.get("area"):
        s += 5
    return max(0, min(100, s))


def _log_event(lead_id: int, event: str, detail: dict | None = None, by: str = "system") -> None:
    try:
        get_client().table("lead_events").insert(
            {"lead_id": lead_id, "event": event, "detail": detail or {}, "by": by}).execute()
    except Exception as e:  # noqa: BLE001
        log.warning("lead_event failed: %s", e)


def discover_and_import(shop_types: tuple[str, ...] = SHOP_TYPES) -> dict:
    """Discover → dedupe vs existing customers → segment + score → bulk upsert (merge).

    Merge-upsert refreshes segment/brand/fit on re-runs WITHOUT disturbing status/notes (those
    columns aren't sent), so the pipeline survives a re-discovery. Returns new vs refreshed counts.
    """
    found = discover_overpass(shop_types)
    # Apify Google-Maps source (optional, needs APIFY_TOKEN) — richer contact fields.
    # Overpass rows win on ref collisions; both go through the same customer dedupe.
    found += discover_apify()
    customers = _existing_customer_names()
    c = get_client()
    try:
        existing = {r["source_ref"] for r in
                    (c.table("leads").select("source_ref").limit(5000).execute().data or [])}
    except Exception:  # noqa: BLE001
        existing = set()
    rows, new, refreshed, skipped_existing = [], 0, 0, 0
    for lead in found:
        if _normalize(lead["name"]) in customers:
            skipped_existing += 1
            continue
        lead["fit_score"] = score_fit(lead)
        refreshed += 1 if lead["source_ref"] in existing else 0
        new += 0 if lead["source_ref"] in existing else 1
        rows.append(lead)
    if rows:
        try:
            c.table("leads").upsert(rows, on_conflict="source,source_ref").execute()
        except Exception as e:  # noqa: BLE001
            log.warning("bulk lead upsert failed: %s", e)
    return {"found": len(found), "new": new, "refreshed": refreshed,
            "skipped_existing": skipped_existing, "attribution": ATTRIBUTION}


def list_leads(status: str | None = None, limit: int = 100) -> list[dict]:
    try:
        q = get_client().table("leads").select("*").order("fit_score", desc=True).limit(limit)
        if status:
            q = q.eq("status", status)
        return q.execute().data or []
    except Exception:  # noqa: BLE001
        return []


def pipeline() -> dict:
    try:
        rows = get_client().table("v_lead_pipeline").select("*").execute().data or []
    except Exception:  # noqa: BLE001
        rows = []
    by = {r["status"]: r for r in rows}
    return {"by_status": by, "total": sum(r.get("leads", 0) for r in rows),
            "stages": ["new", "contacted", "visited", "quoted", "ordered", "rejected"]}


def set_status(lead_id: int, status: str, by: str = "user") -> bool:
    valid = {"new", "contacted", "visited", "quoted", "ordered", "rejected"}
    if status not in valid:
        return False
    try:
        get_client().table("leads").update({"status": status, "updated_at": _now()}).eq("id", lead_id).execute()
        _log_event(lead_id, "status_change", {"to": status}, by)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("set_status failed: %s", e)
        return False


# Fields the portal may edit inline (leads v2 — contactability + follow-up).
EDITABLE = ("phone", "website", "email", "address", "contact_name",
            "last_contacted", "next_action", "notes", "assigned_to")


def update_lead(lead_id: int, changes: dict, by: str = "user") -> bool:
    payload = {k: (v or None) for k, v in changes.items() if k in EDITABLE}
    if not payload:
        return False
    payload["updated_at"] = _now()
    try:
        get_client().table("leads").update(payload).eq("id", lead_id).execute()
        _log_event(lead_id, "edited", {"fields": sorted(payload)}, by)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("update_lead failed: %s", e)
        return False


def enrich_lead(lead_id: int, by: str = "user") -> dict:
    """OPTIONAL Tavily enrichment: search the business's public web presence for a
    website/email/address. Only fills blanks — never overwrites hand-entered data.
    PDPL-conscious: public business listings only."""
    import os
    key = os.getenv("TAVILY_API_KEY", "")
    if not key:
        return {"ok": False, "reason": "TAVILY_API_KEY not configured"}
    row = (get_client().table("leads").select("*").eq("id", lead_id).limit(1).execute().data or [None])[0]
    if not row:
        return {"ok": False, "reason": "lead not found"}
    import re

    import requests
    q = f"{row.get('name')} {row.get('area') or ''} Bahrain contact"
    try:
        r = requests.post("https://api.tavily.com/search",
                          json={"api_key": key, "query": q, "max_results": 5,
                                "include_answer": False}, timeout=20)
        r.raise_for_status()
        results = r.json().get("results") or []
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"search failed: {str(e)[:80]}"}
    blob = " ".join(f"{x.get('title', '')} {x.get('content', '')} {x.get('url', '')}" for x in results)
    found: dict = {}
    if not row.get("email"):
        m = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", blob)
        if m:
            found["email"] = m.group(0)
    if not row.get("website"):
        for x in results:
            url = x.get("url") or ""
            if url and not any(s in url for s in ("facebook.", "instagram.", "tavily.")):
                found["website"] = url.split("?")[0]
                break
    if not row.get("phone"):
        m = re.search(r"(?:\+?973[\s-]?)?(?:3\d{3}|17\d{2}|77\d{2})[\s-]?\d{4}", blob)
        if m:
            found["phone"] = m.group(0).strip()
    if found:
        found["enriched_at"] = _now()
        get_client().table("leads").update(found).eq("id", lead_id).execute()
        _log_event(lead_id, "enriched", {"fields": sorted(k for k in found if k != "enriched_at")}, by)
    return {"ok": True, "found": {k: v for k, v in found.items() if k != "enriched_at"},
            "checked": len(results)}
