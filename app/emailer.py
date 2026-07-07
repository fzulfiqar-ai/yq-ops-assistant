"""Backend SMTP sender for agent briefings.

The backend emails the briefing itself so schedulers (n8n / GitHub Actions) only
need to make ONE authenticated HTTP call — no SMTP credentials configured in n8n.

Reads the same env vars as scripts/send_digest.py:
  SMTP_HOST (default smtp.gmail.com) · SMTP_PORT (587) · SMTP_USER · SMTP_PASS ·
  ALERT_EMAIL_TO (comma-separated recipients)

`send_agent(result)` renders a clean light/purple HTML email from any agent result
dict (it auto-discovers the agent's list fields) and returns a status dict:
  {"emailed": True, "to": "..."}                      — sent
  {"emailed": False, "reason": "smtp_not_configured"} — env vars missing
  {"emailed": False, "reason": "smtp_error: ..."}     — SMTP raised
"""
from __future__ import annotations

import os
import smtplib
import socket
from contextlib import contextmanager
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

PURPLE = "#6d28d9"
PURPLE_DARK = "#4c1d95"

# result keys that are metadata, not displayable data tables
_META_KEYS = {"agent", "description", "generated_at", "summary", "email", "count"}

# Professional, business-report headings per agent — used for BOTH the email subject
# and the in-email H1, so every briefing reads like a report, not a feature blurb.
# (The verbose AgentSpec.description stays as the explanatory text on the Agents page.)
AGENT_EMAIL_TITLES: dict[str, str] = {
    "collections": "Overdue Receivables & Collection Actions",
    "inventory": "Reorder Priorities — Low Stock",
    "margin": "Margin Alert — Products Below or Near Cost",
    "sales_insights": "Sales Performance Review",
    "sales_push": "Targeted Sell-Through Opportunities",
    "sales_outreach": "Customer Reorder Outreach",
    "growth_plan": "Weekly Growth Plan",
    "customer_health": "Customer Retention Watch",
    "cashflow": "Cashflow & Receivables Forecast",
    "risk_watch": "Risk & Integrity Review",
    "inventory_aging": "Ageing Inventory Report",
    "salesman_performance": "Salesman Performance Report",
    "purchase_insights": "Purchasing & Cost Review",
    "salesman_stock_recon": "Van Stock Reconciliation",
    "trend": "Product Momentum Report",
    "marketing": "Marketing & Promotion Plan",
    "catalog_watch": "Catalog & Price Changes",
    "vendor_sourcing": "New Supplier Scouting",
    "demand_forecast": "Demand Forecast & Stock-Out Risk",
    "abc_xyz": "Inventory ABC / XYZ Classification",
    "deadstock_liquidation": "Dead-Stock Clearance Plan",
    "winback": "Lapsed-Customer Win-Back List",
    "credit_exposure": "Credit Exposure Review",
    "working_capital": "Working-Capital Release Opportunities",
    "pricing_optimization": "Pricing Optimization Review",
    "reorder_proposal": "Draft Purchase Order for Review",
    "procurement_status": "Procurement Pipeline Status",
    "cross_sell": "Cross-Sell & Bundle Opportunities",
    "vendor_scorecard": "Vendor Performance Scorecard",
    "trend_radar": "Trend Radar — Restock Rising Items",
    "lead_gen": "New Retailer Lead List",
    "research_scout": "Market Research Briefing",
    "price_drift": "Margin Erosion Alert",
    "returns_investigator": "Product Returns Investigation",
    "ops_sentinel": "Platform Health Report",
    "outreach_builder": "Outreach Queue — Ready to Send",
    "contact_enrich": "Customer Contact Enrichment",
    "outreach_digest": "Outreach Digest — Awaiting Approval",
    "growth_scorecard": "Weekly Growth Scorecard",
    "content_engine": "Marketing Content Drafts",
    "content_poll": "Video Render Status",
}


def email_title(result: dict) -> str:
    """A professional heading for this agent's email (curated map → agent name → 'Agent')."""
    name = str(result.get("agent", "") or "")
    return AGENT_EMAIL_TITLES.get(name) or name.replace("_", " ").title() or "Operations Briefing"


def smtp_configured() -> bool:
    return bool(os.getenv("SMTP_USER") and os.getenv("SMTP_PASS") and os.getenv("ALERT_EMAIL_TO"))


@contextmanager
def _force_ipv4():
    """Force IPv4 DNS resolution for the duration of an SMTP send.

    Railway (and many container hosts) have no IPv6 route, so Python resolving
    smtp.gmail.com to an AAAA record fails with 'Errno 101 Network is unreachable'.
    Filtering getaddrinfo to AF_INET keeps the real hostname (so TLS cert
    verification still works) while connecting over IPv4. The endpoint send is
    blocking on a single-threaded worker, so this temporary patch is request-local.
    """
    _orig = socket.getaddrinfo

    def _ipv4_only(host, port, family=0, *args, **kwargs):
        return _orig(host, port, socket.AF_INET, *args, **kwargs)

    socket.getaddrinfo = _ipv4_only
    try:
        yield
    finally:
        socket.getaddrinfo = _orig


def _send_via_http(subject: str, html: str, recipients: list[str]) -> dict | None:
    """Send over an HTTPS email API (port 443) — works on Railway where SMTP is blocked.

    Picks the provider whose API key is set (Resend, then Brevo). Returns a status
    dict, or None if no HTTP provider is configured (caller falls back to SMTP)."""
    import requests

    resend_key = os.getenv("RESEND_API_KEY", "")
    brevo_key = os.getenv("BREVO_API_KEY", "")
    sender = os.getenv("EMAIL_FROM", "YQ Bahrain <onboarding@resend.dev>")

    try:
        if resend_key:
            r = requests.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {resend_key}", "Content-Type": "application/json"},
                json={"from": sender, "to": recipients, "subject": subject, "html": html},
                timeout=20,
            )
            if r.status_code in (200, 201):
                return {"emailed": True, "to": ", ".join(recipients), "via": "resend"}
            return {"emailed": False, "reason": f"resend_error {r.status_code}: {r.text[:200]}"}

        if brevo_key:
            # parse "Name <email>" -> {name, email}
            email_only = sender.split("<")[-1].rstrip(">").strip()
            name = sender.split("<")[0].strip() or "YQ Bahrain"
            r = requests.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": brevo_key, "Content-Type": "application/json"},
                json={
                    "sender": {"name": name, "email": email_only},
                    "to": [{"email": a} for a in recipients],
                    "subject": subject,
                    "htmlContent": html,
                },
                timeout=20,
            )
            if r.status_code in (200, 201):
                return {"emailed": True, "to": ", ".join(recipients), "via": "brevo"}
            return {"emailed": False, "reason": f"brevo_error {r.status_code}: {r.text[:200]}"}
    except Exception as e:
        return {"emailed": False, "reason": f"http_email_error: {type(e).__name__}: {e}"}

    return None  # no HTTP provider configured → caller tries SMTP


def send_html(subject: str, html: str, to: str | None = None) -> dict:
    """Send an HTML email. `to` overrides ALERT_EMAIL_TO (used for invites)."""
    to = to or os.getenv("ALERT_EMAIL_TO", "")
    if not to:
        return {"emailed": False, "reason": "no_recipient (set ALERT_EMAIL_TO)"}
    recipients = [a.strip() for a in to.split(",") if a.strip()]

    # Prefer an HTTPS email API — SMTP ports are blocked on Railway.
    http_result = _send_via_http(subject, html, recipients)
    if http_result is not None:
        return http_result

    # Fallback: direct SMTP (works locally; blocked on Railway's network).
    user = os.getenv("SMTP_USER", "")
    pw = os.getenv("SMTP_PASS", "")
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "587"))
    if not (user and pw):
        return {"emailed": False, "reason": "no_email_provider (set RESEND_API_KEY or SMTP_USER/SMTP_PASS)"}
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html, "html"))
    try:
        with _force_ipv4(), smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls()
            s.login(user, pw)
            s.sendmail(user, recipients, msg.as_string())
        return {"emailed": True, "to": to}
    except Exception as e:
        return {"emailed": False, "reason": f"smtp_error: {type(e).__name__}: {e}"}


def _fmt_cell(key: str, val) -> str:
    if val is None:
        return "—"
    if isinstance(val, bool):
        return "Yes" if val else "No"
    if isinstance(val, (int, float)) and "bhd" in key.lower():
        return f"BHD {float(val):,.2f}"
    if isinstance(val, float):
        return f"{val:,.2f}"
    return str(val)


def _table(title: str, rows: list[dict]) -> str:
    if not rows:
        return ""
    # column order from the first row; keep it compact (max 5 columns)
    cols = list(rows[0].keys())[:5]
    th = "".join(
        f'<th style="text-align:left;padding:8px 12px;font-size:.7rem;font-weight:700;'
        f'text-transform:uppercase;letter-spacing:.4px;color:#6d28d9;'
        f'border-bottom:2px solid #ede9fe;">{c.replace("_"," ").title()}</th>'
        for c in cols
    )
    body = ""
    for i, r in enumerate(rows[:15]):
        bg = "#ffffff" if i % 2 == 0 else "#faf9ff"
        tds = "".join(
            f'<td style="padding:8px 12px;font-size:.82rem;color:#1f2937;'
            f'border-bottom:1px solid #f1eefe;">{_fmt_cell(c, r.get(c))}</td>'
            for c in cols
        )
        body += f'<tr style="background:{bg};">{tds}</tr>'
    extra = (
        f'<div style="font-size:.72rem;color:#9ca3af;margin:6px 2px 0;">'
        f'… and {len(rows) - 15} more</div>'
        if len(rows) > 15 else ""
    )
    return (
        f'<div style="font-size:.9rem;font-weight:700;color:#4c1d95;margin:22px 0 8px;">{title}</div>'
        f'<table style="width:100%;border-collapse:collapse;background:#fff;border:1px solid #ede9fe;'
        f'border-radius:12px;overflow:hidden;">{th and f"<thead><tr>{th}</tr></thead>"}'
        f'<tbody>{body}</tbody></table>{extra}'
    )


def _agent_html(result: dict) -> str:
    title = email_title(result)
    summary = result.get("summary", "")
    generated_raw = result.get("generated_at", "")
    try:
        from datetime import timezone
        dt = datetime.fromisoformat(generated_raw.replace("Z", "+00:00"))
        generated = dt.strftime("%d %b %Y, %I:%M %p UTC")
    except Exception:
        generated = generated_raw

    date_str = datetime.now().strftime("%A, %d %B %Y")

    tables = ""
    for key, val in result.items():
        if key in _META_KEYS:
            continue
        if isinstance(val, list) and val and isinstance(val[0], dict):
            tables += _table(key.replace("_", " ").title(), val)

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f0eff4;font-family:Inter,Helvetica,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0eff4;padding:32px 12px;">
  <tr><td align="center">
    <table width="100%" style="max-width:640px;" cellpadding="0" cellspacing="0">

      <!-- Header -->
      <tr><td bgcolor="{PURPLE_DARK}" style="background-color:{PURPLE_DARK};background:linear-gradient(135deg,{PURPLE} 0%,{PURPLE_DARK} 100%);border-radius:16px 16px 0 0;padding:28px 32px;">
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td>
              <div style="font-size:.7rem;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#c4b5fd;margin-bottom:6px;">YQ BAHRAIN · MOBILE ACCESSORIES</div>
              <div style="font-size:1.3rem;font-weight:800;color:#ffffff;line-height:1.3;">{title}</div>
              <div style="font-size:.8rem;color:#ddd6fe;margin-top:4px;">Autonomous AI Agent Briefing &nbsp;·&nbsp; {date_str}</div>
            </td>
            <td align="right" style="vertical-align:top;">
              <div style="background:rgba(255,255,255,.15);border-radius:8px;padding:6px 12px;font-size:.7rem;font-weight:700;color:#ede9fe;white-space:nowrap;">AI AGENT</div>
            </td>
          </tr>
        </table>
      </td></tr>

      <!-- Summary banner -->
      <tr><td style="background:#1e1b4b;padding:16px 32px;">
        <p style="margin:0;font-size:.95rem;line-height:1.6;color:#e0e7ff;font-weight:500;">{summary}</p>
      </td></tr>

      <!-- Body -->
      <tr><td style="background:#ffffff;padding:28px 32px;border-left:1px solid #e5e7eb;border-right:1px solid #e5e7eb;">
        {tables if tables else '<p style="color:#6b7280;font-size:.9rem;margin:0;">No detailed records to display for this briefing.</p>'}
      </td></tr>

      <!-- Footer -->
      <tr><td style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:0 0 16px 16px;padding:18px 32px;">
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td style="font-size:.7rem;color:#9ca3af;line-height:1.6;">
              Generated {generated}<br>
              <strong style="color:#6b7280;">YQ Bahrain W.L.L · Mobile Accessories Distribution · Bahrain</strong><br>
              AI-generated briefing — verify all figures before taking action. For internal use only.
            </td>
            <td align="right" style="vertical-align:middle;">
              <div style="width:36px;height:36px;background:linear-gradient(135deg,{PURPLE},{PURPLE_DARK});border-radius:8px;display:inline-block;"></div>
            </td>
          </tr>
        </table>
      </td></tr>

    </table>
  </td></tr>
</table>
</body></html>"""


def send_agent(result: dict) -> dict:
    """Render + email an agent result. Returns a status dict (never raises)."""
    subject = f"YQ Bahrain · {email_title(result)} — {datetime.now().strftime('%d %b %Y')}"
    try:
        return send_html(subject, _agent_html(result))
    except Exception as e:  # defensive: emailing must never break the agent run
        return {"emailed": False, "reason": f"render_error: {type(e).__name__}: {e}"}
