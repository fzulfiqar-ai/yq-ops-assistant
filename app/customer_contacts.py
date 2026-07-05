"""Customer contact enrichment — find business phones/emails so outreach drafts
become one-tap wa.me sends. Public business listings only (Tavily), fills blanks,
never overwrites manual entries. Same PDPL posture as lead enrichment."""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone

from app.database import get_client

log = logging.getLogger(__name__)

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Bahrain numbers: mobile 3xxx xxxx, landline 17xx/77xx xxxx, with optional +973
_PHONE = re.compile(r"(?:\+?973[\s-]?)?(?:3\d{3}|17\d{2}|77\d{2})[\s-]?\d{4}")


def wa_digits(phone: str | None) -> str | None:
    """Normalize to wa.me digits (adds the 973 country code when missing)."""
    if not phone:
        return None
    d = re.sub(r"\D", "", phone)
    if len(d) == 8:
        d = "973" + d
    return d if 10 <= len(d) <= 15 else None


def get_contacts(names: list[str]) -> dict[str, dict]:
    """{customer_name: {phone, email, website}} for the given names (service client)."""
    if not names:
        return {}
    try:
        rows = get_client().table("customer_contacts").select(
            "customer_name,phone,email,website").in_("customer_name", names).execute().data or []
        return {r["customer_name"]: r for r in rows}
    except Exception as e:  # noqa: BLE001
        log.warning("get_contacts failed: %s", e)
        return {}


def enrich_missing(names: list[str], limit: int = 8) -> int:
    """Best-effort Tavily lookup for customers with NO contact row yet. Capped per
    run (the agent runs weekly, so coverage builds up without hammering the API)."""
    key = os.getenv("TAVILY_API_KEY", "")
    if not key or not names:
        return 0
    import requests
    have = set(get_contacts(names))
    todo = [n for n in names if n and n not in have][:limit]
    found = 0
    for name in todo:
        try:
            r = requests.post("https://api.tavily.com/search",
                              json={"api_key": key, "query": f"{name} Bahrain shop contact phone",
                                    "max_results": 4, "include_answer": False}, timeout=15)
            r.raise_for_status()
            results = r.json().get("results") or []
        except Exception as e:  # noqa: BLE001
            log.warning("contact enrich failed for %s: %s", name, str(e)[:80])
            continue
        blob = " ".join(f"{x.get('title', '')} {x.get('content', '')} {x.get('url', '')}"
                        for x in results)
        phone_m = _PHONE.search(blob)
        phone = phone_m.group(0).strip() if phone_m else None
        email_m = _EMAIL.search(blob)
        website = next((x.get("url", "").split("?")[0] for x in results
                        if x.get("url") and not any(s in x["url"] for s in
                                                    ("facebook.", "instagram.", "tavily."))), None)
        row = {"customer_name": name, "source": "tavily",
               "enriched_at": datetime.now(timezone.utc).isoformat()}
        if phone:
            row["phone"] = phone
        if email_m:
            row["email"] = email_m.group(0)
        if website:
            row["website"] = website
        # store even empty results (marks "tried") so we don't re-search every week
        try:
            get_client().table("customer_contacts").upsert(row, on_conflict="customer_name").execute()
            if phone or email_m:
                found += 1
        except Exception as e:  # noqa: BLE001
            log.warning("contact upsert failed for %s: %s", name, e)
    return found
