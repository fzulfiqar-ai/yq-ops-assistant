"""Business settings (app_settings table) — the costing chain + sales targets.

Single source for numbers that used to be hardcoded: FX rates, VFAN dealer discount,
landing+VAT uplift, target markup. The owner's method (VFAN New Order Pricing sheet):

    RMB list ÷ (1 + dealer_discount)  → net RMB
    net RMB ÷ fx_rmb_usd × fx_usd_bhd → base BHD  (what the Focus PO books)
    base × (1 + landing_vat_pct)      → landed cost
    landed × (1 + target_markup)      → suggested sell (then rounded by hand)

Reads are cached 60 s; admin edits go through /settings/costing which busts the cache.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from app.database import get_client

log = logging.getLogger(__name__)

DEFAULTS: dict[str, float] = {
    "fx_rmb_usd": 6.8,
    "fx_usd_bhd": 0.37744,
    "dealer_discount": 0.18,
    "landing_vat_pct": 0.30,
    "target_markup": 0.70,
    "monthly_sales_target_bhd": 0.0,
}

_cache: dict = {"at": 0.0, "vals": None}


def all_settings(force: bool = False) -> dict[str, float]:
    now = time.time()
    if not force and _cache["vals"] is not None and now - _cache["at"] < 60:
        return _cache["vals"]
    vals = dict(DEFAULTS)
    try:
        rows = get_client().table("app_settings").select("key,value").execute().data or []
        for r in rows:
            k = r.get("key")
            if k in DEFAULTS:
                try:
                    vals[k] = float(r.get("value"))
                except (TypeError, ValueError):
                    pass
    except Exception as e:  # noqa: BLE001 — table may not exist yet; defaults keep us correct
        log.warning("app_settings read failed (using defaults): %s", e)
    _cache.update(at=now, vals=vals)
    return vals


def setting(key: str) -> float:
    return all_settings().get(key, DEFAULTS.get(key, 0.0))


def rmb_to_bhd() -> float:
    """BHD per net-RMB via the USD leg (0.37744 / 6.8 ≈ 0.0555)."""
    s = all_settings()
    return s["fx_usd_bhd"] / s["fx_rmb_usd"]


def update_settings(changes: dict[str, float], by: str = "") -> dict[str, float]:
    client = get_client()
    for k, v in changes.items():
        if k not in DEFAULTS:
            continue
        client.table("app_settings").upsert(
            {"key": k, "value": str(float(v)), "updated_by": by,
             "updated_at": datetime.now(timezone.utc).isoformat()},
            on_conflict="key").execute()
    return all_settings(force=True)
