"""Phase D — autonomous escalation. Pure-code rules over current agent state + memory
diffs. Fires once per fingerprint per 24h (deduped via audit_log) and emails the alert.
Also a deterministic daily intelligence brief (all agents, one email — no LLM needed).
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from app.agents import AGENTS, run_agent
from app.audit import log_event
from app.database import get_client

log = logging.getLogger(__name__)

OVERDUE_TOTAL_THRESHOLD = 10000.0
OVER90_THRESHOLD = 5000.0
OVERDUE_JUMP_THRESHOLD = 1000.0


def _f(d: dict, *path) -> float:
    cur = d
    for p in path:
        cur = (cur or {}).get(p) if isinstance(cur, dict) else None
    try:
        return float(cur or 0)
    except (TypeError, ValueError):
        return 0.0


def _fp(*parts) -> str:
    return hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _gather() -> dict:
    state: dict = {}
    for name in ("collections", "inventory", "risk_watch", "cashflow"):
        try:
            state[name] = run_agent(name, triggered_by="escalation")
        except Exception as e:  # noqa: BLE001
            log.warning("escalation gather %s failed: %s", name, e)
            state[name] = {}
    return state


# Each rule: state -> {key, severity, message, fingerprint} or None.
def _r_overdue_total(s):
    c = s.get("collections", {})
    t = _f(c, "total_overdue_bhd")
    if t > OVERDUE_TOTAL_THRESHOLD:
        return {"key": "overdue_total", "severity": "high",
                "message": f"Overdue receivables are BHD {t:,.0f} across {c.get('count', 0)} accounts.",
                "fingerprint": _fp("overdue_total")}
    return None


def _r_overdue_jump(s):
    ch = (s.get("collections", {}).get("changes") or {})
    if ch.get("first_run"):
        return None
    d = (ch.get("metric_deltas") or {}).get("total_overdue_bhd", 0)
    if d and d > OVERDUE_JUMP_THRESHOLD:
        return {"key": "overdue_jump", "severity": "medium",
                "message": f"Overdue receivables rose by BHD {d:,.0f} since the last run.",
                # Bucket by BHD-100 (was BHD-1000): a second, larger jump now fires instead of
                # being deduped under the same coarse fingerprint for 24h.
                "fingerprint": _fp("overdue_jump", round(d / 100))}
    return None


def _r_new_low_stock(s):
    ch = (s.get("inventory", {}).get("changes") or {})
    new = [] if ch.get("first_run") else (ch.get("new_items") or [])
    if new:
        return {"key": "new_low_stock", "severity": "high",
                "message": f"{len(new)} item(s) newly low/out of stock while still selling: {', '.join(new[:5])}.",
                "fingerprint": _fp("new_low_stock", *sorted(new))}
    return None


def _r_below_cost(s):
    below = s.get("risk_watch", {}).get("priced_below_cost") or []
    if below:
        items = [str(r.get("item_name")) for r in below[:5]]
        return {"key": "below_cost", "severity": "high",
                "message": f"{len(below)} product(s) selling below cost: {', '.join(items)}.",
                "fingerprint": _fp("below_cost", *sorted(items))}
    return None


def _r_over90(s):
    over90 = _f(s.get("cashflow", {}), "aging", "90_plus")
    if over90 > OVER90_THRESHOLD:
        return {"key": "over90_high", "severity": "medium",
                "message": f"BHD {over90:,.0f} of receivables is over 90 days overdue — collection risk.",
                "fingerprint": _fp("over90_high")}
    return None


RULES = [_r_overdue_total, _r_overdue_jump, _r_new_low_stock, _r_below_cost, _r_over90]


def evaluate() -> list[dict]:
    s = _gather()
    out = []
    for rule in RULES:
        try:
            t = rule(s)
            if t:
                out.append(t)
        except Exception as e:  # noqa: BLE001
            log.warning("escalation rule %s failed: %s", getattr(rule, "__name__", "?"), e)
    return out


def _recent_fingerprints() -> set[str]:
    try:
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        rows = (get_client().table("audit_log").select("detail")
                .eq("event", "escalation").gte("ts", since).execute().data or [])
        return {(r.get("detail") or {}).get("fingerprint") for r in rows if r.get("detail")}
    except Exception as e:  # noqa: BLE001
        log.warning("recent fingerprints lookup failed: %s", e)
        return set()


def check(send: bool = True) -> dict:
    """Evaluate rules, fire (email + log) only the not-recently-fired ones."""
    triggered = evaluate()
    fired_recently = _recent_fingerprints()
    fresh = [t for t in triggered if t["fingerprint"] not in fired_recently]
    if fresh and send:
        _send_alerts(fresh)
        for t in fresh:
            log_event("agent@yqbahrain.local", "escalation",
                      detail={"rule": t["key"], "severity": t["severity"], "fingerprint": t["fingerprint"]})
    return {
        "triggered": [t["key"] for t in triggered],
        "fired": [{"key": t["key"], "severity": t["severity"], "message": t["message"]} for t in fresh],
        "deduped": len(triggered) - len(fresh),
        "emailed": bool(fresh and send),
    }


def daily_brief(send: bool = True) -> dict:
    """Deterministic combined briefing — every agent's headline + a RANKED 'today's actions'
    list (priority_actions), with a stale-data banner if the last upload is old. One email."""
    from app.orchestrator import priority_actions, recommendations
    from app.reports import data_freshness

    # Run sequentially: the brief is a once-a-day job where reliability beats speed, and the shared
    # Supabase client isn't safe at high concurrency (socket errors). The brief uses NO LLM
    # (deterministic agents + scoring), so free-LLM caps are untouched; the LLM paths (/ask,
    # synthesis) already cache via the 7-day query_cache. → free-LLM capacity is a non-issue here.
    results = []
    for name, spec in AGENTS.items():
        if not getattr(spec, "in_brief", True):
            continue  # external/web agents (vendor_sourcing, research_scout) run on-demand, not here
        try:
            results.append(run_agent(name, triggered_by="schedule"))
        except Exception as e:  # noqa: BLE001
            log.warning("brief %s failed: %s", name, e)
    recs = recommendations(results)
    actions = priority_actions(results)
    fresh = data_freshness()
    emailed = False
    if send:
        res = _send_brief(results, actions, fresh)
        emailed = bool((res or {}).get("emailed"))
    return {"agents": len(results), "actions": actions, "recommendations": recs,
            "data": fresh, "emailed": emailed}


# ── email rendering ──────────────────────────────────────────────────────────

def _send_alerts(items: list[dict]) -> dict:
    from app.emailer import PURPLE, PURPLE_DARK, send_html
    colour = {"high": "#e11d48", "medium": "#d97706", "low": "#6b7280"}
    rows = "".join(
        f"<tr><td style='padding:10px 14px;border-bottom:1px solid #eee;'>"
        f"<span style='display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700;"
        f"color:#fff;background:{colour.get(t['severity'], '#6b7280')};'>{t['severity'].upper()}</span></td>"
        f"<td style='padding:10px 14px;border-bottom:1px solid #eee;font-size:14px;color:#1f2937;'>{t['message']}</td></tr>"
        for t in items)
    html = (f"<!DOCTYPE html><html><body style='margin:0;background:#f0eff4;font-family:Inter,Arial,sans-serif;'>"
            f"<table width='100%' cellpadding='0' cellspacing='0' style='padding:28px 12px;'><tr><td align='center'>"
            f"<table width='100%' style='max-width:600px;' cellpadding='0' cellspacing='0'>"
            f"<tr><td bgcolor='{PURPLE_DARK}' style='background-color:{PURPLE_DARK};background:linear-gradient(135deg,{PURPLE},{PURPLE_DARK});border-radius:16px 16px 0 0;padding:24px 28px;'>"
            f"<div style='font-size:.7rem;font-weight:700;letter-spacing:2px;color:#c4b5fd;'>YQ BAHRAIN · MOBILE ACCESSORIES</div>"
            f"<div style='font-size:1.25rem;font-weight:800;color:#fff;margin-top:6px;'>⚠ {len(items)} operational alert(s)</div></td></tr>"
            f"<tr><td style='background:#fff;padding:8px 14px 20px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 16px 16px;'>"
            f"<table style='width:100%;border-collapse:collapse;'>{rows}</table>"
            f"<p style='font-size:11px;color:#9ca3af;margin-top:14px;'>Autonomous escalation · YQ Bahrain W.L.L · verify before acting.</p>"
            f"</td></tr></table></td></tr></table></body></html>")
    try:  # also push a plain-text summary to Telegram if configured
        from app.notify import send_telegram
        lines = "\n".join(f"• [{t['severity'].upper()}] {t['message']}" for t in items)
        send_telegram(f"<b>⚠ {len(items)} YQ operational alert(s)</b>\n\n{lines}")
    except Exception:  # noqa: BLE001
        pass
    return send_html(f"YQ Bahrain · {len(items)} operational alert(s) — {datetime.now().strftime('%d %b %Y')}", html)


def _send_brief(results: list[dict], actions: list[dict], fresh: dict | None = None) -> dict:
    from app.emailer import PURPLE, PURPLE_DARK, send_html
    fresh = fresh or {}
    items = "".join(
        f"<li style='margin:6px 0;font-size:13.5px;color:#1f2937;'><b>{str(r.get('agent', '')).replace('_', ' ').title()}:</b> {r.get('summary', '')}</li>"
        for r in results)
    _uc = {3: "#e11d48", 2: "#d97706", 1: "#6b7280"}
    if actions:
        _rows = []
        for a in actions:
            dot = _uc.get(a.get("urgency", 1), "#6b7280")
            bhd_suffix = f" <b style='color:#4c1d95;'>(BHD {a['bhd_at_risk']:,.0f})</b>" if a.get("bhd_at_risk") else ""
            _rows.append(
                f"<li style='margin:7px 0;font-size:13.5px;color:#1f2937;'>"
                f"<span style='display:inline-block;width:8px;height:8px;border-radius:999px;"
                f"margin-right:8px;background:{dot};'></span>{a['action']}{bhd_suffix}</li>")
        act_html = "".join(_rows)
    else:
        act_html = "<li style='color:#6b7280;'>Nothing urgent today — you're on top of it.</li>"
    stale_html = ("" if not fresh.get("stale") else
                  f"<div style='background:#fffbeb;border:1px solid #fde68a;color:#92400e;border-radius:10px;"
                  f"padding:10px 14px;margin-bottom:14px;font-size:12.5px;'>⚠ Data is "
                  f"{fresh.get('days_behind', '?')} day(s) old (latest {fresh.get('data_until') or 'unknown'}). "
                  f"Upload the latest Focus exports for an accurate brief.</div>")
    html = (f"<!DOCTYPE html><html><body style='margin:0;background:#f0eff4;font-family:Inter,Arial,sans-serif;'>"
            f"<table width='100%' cellpadding='0' cellspacing='0' style='padding:28px 12px;'><tr><td align='center'>"
            f"<table width='100%' style='max-width:620px;' cellpadding='0' cellspacing='0'>"
            f"<tr><td bgcolor='{PURPLE_DARK}' style='background-color:{PURPLE_DARK};background:linear-gradient(135deg,{PURPLE},{PURPLE_DARK});border-radius:16px 16px 0 0;padding:24px 28px;'>"
            f"<div style='font-size:.7rem;font-weight:700;letter-spacing:2px;color:#c4b5fd;'>YQ BAHRAIN · MOBILE ACCESSORIES</div>"
            f"<div style='font-size:1.25rem;font-weight:800;color:#fff;margin-top:6px;'>Daily intelligence brief</div>"
            f"<div style='font-size:.8rem;color:#ddd6fe;margin-top:4px;'>{datetime.now().strftime('%A, %d %B %Y')}</div></td></tr>"
            f"<tr><td style='background:#fff;padding:18px 28px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 16px 16px;'>"
            f"{stale_html}"
            f"<div style='font-weight:700;color:#4c1d95;margin-bottom:6px;'>Today's priority actions</div><ul style='margin:0 0 16px;padding-left:4px;list-style:none;'>{act_html}</ul>"
            f"<div style='font-weight:700;color:#4c1d95;margin-bottom:6px;'>The team's findings</div><ul style='margin:0;padding-left:18px;'>{items}</ul>"
            f"<p style='font-size:11px;color:#9ca3af;margin-top:16px;'>AI-generated · verify figures before acting · YQ Bahrain W.L.L</p>"
            f"</td></tr></table></td></tr></table></body></html>")
    try:  # also push a compact summary to Telegram if configured
        from app.notify import send_telegram
        warn = ("⚠ Data {} day(s) old.\n\n".format(fresh.get("days_behind")) if fresh.get("stale") else "")
        act_txt = ("\n".join(f"• {a['action']}" + (f" (BHD {a['bhd_at_risk']:,.0f})" if a.get("bhd_at_risk") else "")
                             for a in actions)) if actions else "Nothing urgent today."
        send_telegram(f"<b>YQ daily brief — {datetime.now().strftime('%d %b %Y')}</b>\n\n{warn}"
                      f"<b>Today's actions:</b>\n{act_txt}")
    except Exception:  # noqa: BLE001
        pass
    return send_html(f"YQ Bahrain · Daily intelligence brief — {datetime.now().strftime('%d %b %Y')}", html)
