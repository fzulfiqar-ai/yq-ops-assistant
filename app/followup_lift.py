"""Does the follow-up list change what shops buy? The hold-out readout (release R7d, 27-Sep-2026;
plan §23 item 4, audit OFF-16).

app/followups.py already holds back 1 shop in 5 per rep (sha1(rep|shop) % 5 == 0, never stored):
the rep never sees those shops as due. Until now nothing recorded WHICH shops were served or held
on a given day, so there was nothing to compare. This module records it and reads the answer:

  * followup_exposures — written ONCE per rep per Bahrain day, on the rep's first read of his Due
    list (GET /shop/me/followups or the Today card, GET /shop/me/today): every shop on his due and
    lapsed lists (holdout = false) and every shop the hold-out kept off them (holdout = true), with
    the list (kind) and the rank in the combined ordering, so both arms are ranked alike. The write
    runs after the answer is sent (a background task) and never slows or fails the read; the
    unique key (served_on, salesman_id, shop_key, kind) makes a second copy impossible even across
    processes, and a per-process memo skips the work after the first read of the day.
  * v_followup_lift (scripts/r7d_offer_ledger_migration.sql) — per week and rep, served vs holdout:
    shops, how many bought within 14 days of the first exposure that week (a Focus Accessories
    invoice, cash and giveaways excluded), the BHD, the rate, and how many served shops the rep
    tapped (audit_log 'followup.tap'). `complete` is false while the 14-day window is still open.
  * a small read-only card on the Salesmen page (GET /shop/followups/lift).

With 5 reps and a few dozen due shops each, a week is noise: the card shows the running total and
says plainly that only a large difference means anything. Admin views of the list (?rep=, the
company book) are never exposures — only the rep's own read counts.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

_BAHRAIN = timezone(timedelta(hours=3))
_done: dict[tuple[str, str], bool] = {}       # (salesman id, Bahrain day) → written this process
MEMO_MAX = 2000


def bahrain_day(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(_BAHRAIN).date().isoformat()


def forget() -> None:
    _done.clear()


def exposure_rows(sm: dict, listing: dict, day: str) -> list[dict]:
    """Pure: the rows for one rep's day from followups.followups(focus, include_holdout=True) —
    due + lapsed with the holdout flag, ranked together (combined ranking = the rank the shop
    would hold if nobody were held back)."""
    from app.followups import shop_key
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for kind in ("due", "lapsed"):
        for r in listing.get(kind) or []:
            shop = str(r.get("shop") or "").strip()
            if not shop:
                continue
            key = shop_key(shop)
            if (key, kind) in seen:
                continue
            seen.add((key, kind))
            rank = r.get("rank")
            rows.append({"served_on": day, "salesman_id": int(sm["id"]), "shop_key": key[:200], "kind": kind,
                         "rank": int(rank) if isinstance(rank, int) else None, "holdout": bool(r.get("holdout"))})
    return rows


def log_exposures(sm: dict | None, now: datetime | None = None) -> dict:
    """Record today's served and held shops for this rep, once. Never raises."""
    if not sm or not sm.get("id"):
        return {"skipped": "no rep"}
    focus = str(sm.get("focus_name") or "").strip()
    if not focus:
        return {"skipped": "no Focus name"}
    day = bahrain_day(now)
    memo = (str(sm["id"]), day)
    if _done.get(memo):
        return {"skipped": "already logged today"}
    from app import followups, shop
    try:
        if not shop.has_column("followup_exposures", "served_on"):
            return {"skipped": "followup_exposures not installed"}
        client = shop.get_client()
        have = (client.table("followup_exposures").select("id").eq("served_on", day)
                .eq("salesman_id", int(sm["id"])).limit(1).execute().data)
        if have:
            _remember(memo)
            return {"skipped": "already logged today"}
        listing = followups.followups(focus, include_holdout=True)
        rows = exposure_rows(sm, listing, day)
        if rows:
            client.table("followup_exposures").upsert(
                rows, on_conflict="served_on,salesman_id,shop_key,kind", ignore_duplicates=True).execute()
        _remember(memo)
        return {"served_on": day, "rows": len(rows), "holdout": sum(1 for r in rows if r["holdout"])}
    except Exception as e:  # noqa: BLE001 — measurement never costs the rep his list
        log.warning("follow-up exposures for rep %s not recorded: %s", sm.get("id"), e)
        return {"error": f"{type(e).__name__}: {e}"[:160]}


def _remember(memo: tuple[str, str]) -> None:
    if len(_done) >= MEMO_MAX:
        _done.clear()
    _done[memo] = True


LIFT_SQL = ("SELECT week_start::text AS week_start, salesman_id, salesman_name, arm, shops, bought_14d, buy_rate_pct, "
            "net_bhd_14d, net_bhd_per_shop, tapped, complete, data_through::text AS data_through "
            "FROM v_followup_lift ORDER BY week_start DESC, salesman_name, arm")


def lift() -> dict:
    """GET /shop/followups/lift: the weekly rows plus a running total per arm (complete windows
    only — an open window would flatter neither side fairly). available False before the migration."""
    from app.db_read import exec_sql
    try:
        rows = exec_sql(LIFT_SQL) or []
    except Exception as e:  # noqa: BLE001 — the view arrives with scripts/r7d_offer_ledger_migration.sql
        log.info("v_followup_lift unavailable: %s", e)
        return {"available": False, "rows": [], "totals": {}, "note": "Exposure logging is not switched on yet."}
    totals: dict[str, dict] = {}
    for r in rows:
        if not r.get("complete"):
            continue
        t = totals.setdefault(r["arm"], {"shops": 0, "bought_14d": 0, "net_bhd_14d": 0.0})
        t["shops"] += int(r.get("shops") or 0)
        t["bought_14d"] += int(r.get("bought_14d") or 0)
        t["net_bhd_14d"] = round(t["net_bhd_14d"] + float(r.get("net_bhd_14d") or 0), 3)
    for t in totals.values():
        t["buy_rate_pct"] = round(100.0 * t["bought_14d"] / t["shops"], 1) if t["shops"] else None
        t["net_bhd_per_shop"] = round(t["net_bhd_14d"] / t["shops"], 3) if t["shops"] else None
    return {"available": True, "rows": rows, "totals": totals,
            "note": ("1 shop in 5 is held back from each rep's list. A few dozen shops a week is a small "
                     "sample: only a large, lasting gap between the two arms means the list works.")}
